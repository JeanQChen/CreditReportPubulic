"""M930-3 真实写作主链验收（**正式**、create-only、版本化）。

用法：
    python -m evaluation.run_m930_3_acceptance --subject <company_id> \
        --subject-name <公司名称>                                               # 离线确定性模式
    python -m evaluation.run_m930_3_acceptance --subject <company_id> \
        --subject-name <公司名称> --mode real                                   # 真实 LLM
    python -m evaluation.run_m930_3_acceptance --validate-only <run目录>

`--subject` 与 `--subject-name` **均必填**（`subj-2`）：报告写给谁、以及读者面怎么称呼这个主体，
都由本次报告输入声明；财务库只被用来**核对**该主体的名称、快照与 current Evidence Set，不再由
库里排序后第一条快照决定，也不再替声明挑一个名字。改前只有 `--subject`，于是章节与正文里凡是
引用公司名的地方都印出证券代码——那是把标识当名字。

本 runner 的位置（§五）：

  * 它是 M930-3 的**正式**验收入口。临时探针（`_m930_3_*probe.py`）**不得**再承担这个角色，
    也不得复用或覆盖任何历史 `narr-3` / `_m930_3_preview_*` 结果目录；
  * 结果一律写进 `evaluation/results/m930_3_acceptance_<UTC>/`，**create-only**：目录已存在即
    拒绝，绝不覆盖任何历史结果（与 `run_tree_table_acceptance.py` 同一约定）；
  * 它**只读**真实库（`data/evidence.db`、`data/financial_v2.db` 只开只读 URI），**不写**任何
    真实库、不重建索引；落库只发生在本次 run 目录内的 `section_chain_v2.db`；
  * 六个验收项是**机器判据**，不是人读文字。任何一项不达标即 `fail`，退出码非 0，且报告里
    逐项写明实测值与判据——**不得**为了让结果好看去改信任根或期望值（那是 `A6` 的判据本身）。

`A6` 的做法：在跑任何东西**之前**对全部信任根（真实库、四类冻结资产、两份 prompt 资产、以及
DemoScope profile 本身）逐个求 sha256，跑完之后**再求一次**，逐字节比较。任何一个字节变了，
A6 就 fail。这既证明本 runner 没有改写信任根，也证明它没有「按实得结果回写期望值」。

诚实边界（必须与结论一起读）：
  * 离线模式的生成器是**确定性替身**（`OfflineNarrationClient`）。它的文本**逐字取自真实材料
    正文/权威事实文本**，不新增任何字面成分——但它终究是替身。因此离线模式证明的是「主链在
    真实输入+真实正文+真实库上能贯通」，不是「真实模型的产出质量」；
  * 「人读内容通过」是**另一类**结论，本 runner 只在 `A1` 里给出可机读的最小证据（是否存在
    由多条已接受 Claim 组织成的一句自然句），其余人读判断由人工复核模板承担。
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

#: 本 runner 的版本。判定集或产物形状变化时必须升版（否则同一 runner 版本对应两份判据）。
#: acc-2（M930-3.2）：A1 拆出两个分别可见的子门并拒 raw join；A2 由「非空」改为段落/表格结构门；
#: A5 的零裸事实改为逐句（含 composed）独立重算；门结论新增 `sub_gates`。历史 run 保持只读。
#: acc-3（M930-3.3）：A4 的整本读回重组由「非 fail 即过」改为**必须 pass**（`skipped` 不再
#: 冒充通过）；A5 新增 §四 1 身份分层审计（补件运行不得改写冻结策略依赖、后继 Pack 身份必须
#: 在内容/版本身份里）与版本身份审计（`report_version.topic_pack_ids` 必须等于本轮实际使用的
#: current Pack 集）；`follow_up_dependency` 观察项改名为 `follow_up_identity` 并逐次执行摊开。
#: acc-4（M930-3.3）：判据**不变**，new 的是产物形状——新增 A1 源头对账（只读诊断：
#: `observations.company_material_source_reconciliation`，七层：Contract 需求 → 标题树召回 →
#: 实际 span/表 → 读取轨迹 → ResearchMaterial → Pack manifest → Writer 精确材料清单）与报告顶部
#: 的对应诚实行。升版是为了让这份产物与 acc-3 的产物在读者侧可区分，不是判据放宽。
#: acc-5（M930-3 任务二）：A5 版本身份审计新增 §4.6 的两项绑定核对 —— 身份声明的
#: `section_draft_ids` 必须等于本轮各节 Draft 身份（排序去重），且身份声明的
#: `assembled_payload_fingerprint` 必须等于按规范载荷**独立重算**的值。同时任务一的导航
#: 规则升到 `anp-2`：A1 源头对账新增 `tree_navigation` / `nav_read_scope` 两层——从本节
#: 各 topic 真实发过的 `TREE_NAVIGATION` 事件逐条回读候选依据与舍弃原因、读根 / 读集 /
#: 未读范围（层 2b，紧邻导航召回层）。
#: 判据方向是收紧（新增 fail-closed 核对），不是放宽。
#: 同轮补强：Draft 身份的**预期值**改取自本轮各节定稿现场（`run.sections[*].draft.draft_id`），
#: 不再取自报告自己携带的那份——报告自报值只能用来互核，用来自证就会让「换了 Draft 又同步
#: 重建身份」的自洽报告静默过关。报告携带值与定稿现场不一致、或定稿现场不可得，都记问题。
#: acc-6（M930-3 任务二）：**调用预算门**。预算从「一个数字、事后检查」改为「按类别分列、
#: 在共享 LLM 客户端**发请求之前**判定」（`llm.budget`），并新增 A7 调用账本门：账本（每次
#: 尝试的类别/节/状态）、客户端侧记录、各节计量三者必须逐项守恒，且真实模式下逐条与
#: `logs/llm` 的 call_id 对账。产物新增 `llm_call_ledger.json`，故报告 schema 升版。
#: 判据方向是收紧（新增守恒与事前上限），不是放宽；历史 run 保持只读。
#: acc-7（M930-3 一次性真实预览授权，2026-09-23）：**已批准的调用预算改了数字**（叙述类 4/节、
#: 蕴含 40/节、可选评估以「已批准的上限 0」表示本轮不发、整轮 112），报告里**没有**费用型
#: input/output token 限额，改为逐阶段记录**输出容量**（`llm_calls.output_capacity`）；传输层新增
#: 「截断即本次调用失败并停止」（`reject_truncated`），因此 A7 证据里多一条截断判据。
#: 判据方向仍是收紧（新增事前上限与截断即停），不是放宽；历史 run 保持只读。
#: acc-8（批次 A/B 补充裁决，2026-09-23）：三个新身份被拆开，报告 schema 随之升版——
#:   * 报告结论日与财务期末**彻底分离**：`report_as_of` 由本轮时钟显式声明，
#:     `FinancialSnapshot.as_of_date` 只作财务期末（不再被用来推导报告日）；
#:   * 来源清单 `source_manifest.json`：全部已登记材料 + 披露日期状态 + 类型判断 + 单一选中主件
#:     与 typed 未选原因（登记 ≠ 同权用于当前事实）；
#:   * 被拒候选提案集与被拒时提出的补件诉求各自成产物：`proposal_set_rejections.json`、
#:     `follow_up_needs.json`（**待裁决、未执行**，不是 gap，也不自动执行）。
#: 判据方向仍是收紧（新增身份分离与留存要求），不是放宽；历史 run 保持只读。
#: acc-9（M930-3 批次 C / 3.3，2026-09-23）：**已批准的叙述类调用上限再次重估**，把预算从
#: 「谁批的」推进到「按代码常量推得出、并在发请求之前比对」：`narration` 由 6/节 18 总抬到
#: **8/节 24 总**（新增栏目定向补足那一次），整轮 118 → **124**；新增 `_narration_structural_bound()`
#: 与 `_call_budget` 里的**事前**比对（上限低于结构上界即整轮拒绝、一个请求都不发），并新增
#: typed 拒绝原因 `subsection_uncovered`（它会出现在 `proposal_set_rejections.json` 里，因此
#: 报告的判读窗口也变了）。判据方向是收紧（新增「上限必须盖得住结构上界」这条预检），不是放宽；
#: 历史 run 保持只读。
#: acc-10（M930-3 批次 C / 3.4，2026-09-23）：`proposal_set_rejections.json` 载荷升到
#: **/2**（每条记录新增逐候选 `candidate_audit`、结构性 `named_subsections` 与
#: `whole_set_rejected`，顶层新增逐候选原因词表与结构化 kind 表），`acceptance_report.json`
#: 的 `proposal_set_rejections` 块相应新增 `candidate_reason_vocabulary` 与
#: `candidate_audit_total`——**报告的判读窗口变了**，因此报告 schema 一并前进。判据方向是收紧
#: （新增「逐候选原因必须完整且与整束身份逐一对应」这条不变式），不是放宽；历史 run 保持只读。
#: acc-11（M930-3 批次 C / 3.5，2026-09-24）：A7 的**失败节调用对账**。旧口径里「本节抛错」
#: 会在逐节循环里被 `continue` 过去，于是那一节的账本尝试**既不计数也不对账**——而报告顶部那句
#: 「账本 / 客户端记录 / 各节计量三者在 A7 里逐项相等」在失败节上因此是**假的**，且没有任何数字
#: 能让人看出来。本版把第三个视图的适用范围写死成「有产出的节」，并给失败节显式桶：
#: `reconciliation.meter_absent_sections`（逐节 已发 / 成功返回 / 未结算 / 归属类别 / 错误）+
#: 守恒式 `ledger_total == meter_sum + attempts_unmetered + attempts_unattributed`（残差必须为 0）；
#: 另加两条判据：账本状态词表封闭（未登记状态不得被归并进「已发」或「成功返回」），以及单向的
#: 「客户端说成功 ⇒ 账本必须有一次成功」（离线替身不报 status ⇒ 记 `applicable=false`，不适用
#: 不等于通过）。判据方向是收紧（把原先无归属的账面数字收进显式桶并断言），不是放宽；
#: 报告顶部与 `llm_calls.note` 的措辞随口径改动，因此报告 schema 一并前进。历史 run 保持只读。
#: acc-12（M930-3 批次 D，2026-09-24）：**真实模式的两处恒真前提**。第一次真实模式启动即崩在
#: `NameError: name 'RT' is not defined`（`RealEnvironment._build` 研究侧注入 `RT.RealResearchLLM()`
#: 但该别名从未导入）——MOCK eval 覆盖不到这条只在 `--mode real` 上走的分支，于是「真实模式可
#: 启动」这件事当时**没有任何断言或证据**。本版修好该导入，并把这一类缺陷变成可被断言拦住的
#: 形状：①`_writer_policy` 在真实模式下对齐「报告声称的已批准模型」与「实际要用的
#: `config.LLM_MODEL`」，不一致即整轮拒绝（否则报告会**声称**用了已批准模型而实际用了别的，
#: 从产物里看不出来）；②`evals/test_m930_3_acceptance_gates.py` 新增 AST 检查：runner 里任何
#: 以属性访问出现的裸模块别名都必须在文件内被绑定过（`RT` 这类只在真实分支上会触发的 NameError
#: 由此在离线可测）。判据方向是收紧，不是放宽；**报告载荷形状未变**，故
#: `ACCEPTANCE_REPORT_SCHEMA_VERSION` 仍为 `m930-3-acc-report-11`。历史 run 保持只读。
#: acc-13（M930-3 返修 P4，2026-09-24）：**主体与报告日**。
#:   * 主体改由本次**报告输入声明**（`--subject`，必填）：改前主体取自对 `current_snapshot`
#:     的「按 company_id/as_of_date/scope 排序后取第一条」——库里排序第一条快照属于谁，报告就
#:     写给谁。那是把存量库的排序当成用户输入（那条查询的字面形态**已从本文件删除**，
#:     `evals/test_m930_3_subject_and_report_date.py` 有静态守卫盯着它不再回来）。本版按声明
#:     主体核对快照与 current Evidence Set；声明与库不一致、或同一主体有多条 current 快照而
#:     声明没有收窄到唯一一条，都拒绝（任选一条就是「排序第一」换个地方再犯）。报告新增
#:     `subject_declaration` 块——**载荷形状变了**，故报告 schema 一并前进；
#:   * 报告参考日与快照选择日**版本化分开**（`rc-rd-1`）：`RouteContext` 新增
#:     `snapshot_as_of_date`（选快照 / 本地材料截止）与 `report_as_of_source`（声明还是回落），
#:     `report_as_of` 不再由 `FinancialSnapshot.as_of_date` 冒充。
#: 判据方向是收紧，不是放宽；历史 run 保持只读。
#: acc-14（M930-3 返修 P5，2026-09-24）：**失败也可审计**。新增只读诊断产物
#: `failure_diagnostics.json`（`failure-diagnostics/1`）：逐文档选用/未选用（登记 ≠ 选用）、
#: 逐 aspect 的原始材料 → Pack 材料 → 可写事实与**期间/归属**两类别拒绝（同一份 typed 记录，
#: 不合并成一句）、交给 Writer 的精确清单（逐成员字数/切句/可用原子/逐条排除原因）、被拒提案与
#: 待裁决补件（失败节内联，因为那些行只挂在异常对象上）。报告与拒绝报告都新增
#: `failure_diagnostics` 块——**载荷形状变了**，故报告 schema 一并前进。
#: 语义要点：没有 `SectionDraft` 的节，逐 aspect 状态写「本 run 未能读回验收」（唯一措辞常量
#: `NOT_READ_BACK_THIS_RUN`），**不得**写成「已证明本节没有材料」；此时仍给出**输入侧**权威面
#: 并明确标为输入面。判据方向是收紧（新增一份必须存在且必须诚实的诊断产物），不是放宽；
#: 历史 run 保持只读。诊断**不参与任何判据**，也不改任何门的期望值。
#: acc-15（M930-3 返修，2026-09-24）：**拒绝报告也要说清「这一轮本来是为谁跑的」**。
#: 这一条是 acc-14 的真实 run 暴露的：`m930_3_acceptance_20260924T005116Z` 在第一节第一次写作
#: 调用上被 provider 截断而整轮拒绝，产物齐全（含 `failure_diagnostics.json`），但
#: `acceptance_report.json` 里**根本没有** `subject_declaration` 块——装配已经完成、主体与两个
#: 日期都已核对出来，拒绝报告却把它们丢了。读者看不出这份缺口属于哪家公司，也看不出用的是
#: 哪一期快照：这正是 `subj-1`（主体由声明决定）与「失败也可审计」同时要求的东西。
#: 现在正常路径与拒绝路径**共用同一份** `subject_declaration` 构造：
#:   * 装配已完成（`state` 非空）⇒ 给**已核对**的读数（快照 id / 快照选择日 / Evidence Set /
#:     来源清单主体）；
#:   * 装配尚未发生 ⇒ 只有声明本身（主体、声明人、声明版本、报告参考日、收窄条件），
#:     `verified_against` 写 `None` 而不是 `{}`——空字典会被读成「核对过、结果为空」。
#: 拒绝报告**载荷形状变了**，故报告 schema 一并前进；判据方向是收紧（拒绝报告不得比正常报告
#: 少说主体），不是放宽。历史 run 保持只读。
#:
#: **证据边界（不得含混）**：`m930_3_acceptance_20260924T005116Z` 是 **acc-14** 的实测，它证明的是
#: **缺陷存在**；它不是 acc-15/acc-16 的实测通过，也不得被写成 acc-15 的真实 run。
#: acc-15 起的形状由聚焦 eval（`evals/test_m930_3_subject_and_report_date.py` 第 D 组）覆盖，
#: 真实 run 的复验要等下一次获批的 create-only 预览。
#: acc-16（M930-3 返修，2026-09-24）：**拒绝报告里的「主体 + 两个日期」必须各自直接可读**。
#: acc-15 已把 `subject_declaration` 补进拒绝报告，但装配完成的现场里**报告截止日只存在于
#: 声明块的嵌套里、快照日只存在于 `verified_against` 里**——读者仍要跳两层才拼得出「这份缺口
#: 属于哪家公司、截止到什么时候、用的是哪一期财务」。本版把三者平列在块顶层：
#: `subject_id` / `report_as_of`（时钟派生的报告截止日）/ `snapshot_as_of_date`（财务快照日）；
#: `verified_against` 只留确认过的其余读数（快照 id / Evidence Set / 清单主体）。
#: `report_as_of` 只取 `inputs.report_as_of`，**不取** `dims["as_of_date"]`——两条日期轴不得互相
#: 顶替。装配**之前**的拒绝仍只呈现已声明未核对的信息，且**不放**读数位（缺字段 ≠ 读数为空）。
#: 拒绝报告**载荷形状又变了**，故报告 schema 一并前进；判据方向仍是收紧，历史 run 保持只读。
#: acc-17（M930-3 返修，2026-09-24）：**Writer 首次调用被 provider 截断的结构性修法 + 预算重估**。
#: 这一条针对 acc-14/acc-15 那次真实 run 暴露的失败：第一节第一次写作调用被截断，整轮拒绝。
#:   * Writer 链改为**按确定性 Contract aspect 范围分批请求**（`wbatch-1`，策略 `pw-10`，
#:     提示词 `pack_section_writer_proposals_v6@proposals-8`）：每批仍可访问完整、精确的 Writer
#:     材料集合（`authority_facts`/`materials`/`must_use_facts`/`gaps`/`requestable_aspects`/
#:     `requirement_ids` 逐字不变），只限定本批要回答的 aspect 范围；一批被截断即**拒绝该批结果**
#:     （半截 JSON 永不解析、永不采纳），并按对半确定性缩小重问，每轮至多 `MAX_SWEEP_SHRINK_STEPS`
#:     次；缩到单 aspect 仍失败即 typed fail-closed。全部批次成功后合并成**一份**完整有序提案集，
#:     门只消费这个完整集合；失败不留下部分 Draft/Claim/Pack 写入。
#:   * `proposal_set_rejections.json` 升到 **/3**：每条被拒记录新增 `batches`（本轮逐批调用读数），
#:     因为「一次尝试」不再等于「一次调用」。拒绝报告与 `acceptance_report.json` 的判读窗口
#:     随之改变（报告侧新增 `batches` 的读法说明），故报告 schema 一并前进。
#:   * **叙述类调用上限按分批重估为每节 ≤62 / 三节共 ≤144，并显式标为尚未批准**
#:     （`APPROVED_BUDGET_POLICY_VERSION` → `m930-3-budget-4`，`total_max_attempts` 124 → 244）。
#:     旧口径 8/24 是按「一轮写作 = 一次请求」推的，那个结构依据已被代码改动取消；若不重估，
#:     预检会通过、真实请求会发出去、然后中途撞上限整轮失败。**在用户批准这组数字之前，真实模式
#:     在预检处整轮拒绝**（`approved=False`），一个请求都不发——不得把未获批的数字写成已批准。
#: 判据方向是收紧（新增「截断即拒该批」「上限必须盖得住分批后的结构上界」两条），不是放宽；
#: 历史 run 与冻结资产保持只读。
#: acc-18（M930-3 业务纵链补充门 / 第 1 条，2026-09-24）：**导航键层级进产物**。导航规则升到
#: `anp-3`，Profile schema 升到 `anps-3`（条目新增 `parent_keys`），新增两条规则 id：
#: `navsegment-complete-label-segment`（标题的某段必须与被查键**逐字符相同**才算完整标签段命中）
#: 与 `navparent-contract-ancestor-label`（本层无完整标签段命中时，沿真实标题树回退到由冻结
#: Contract 主题/问题标题派生的祖先声明键，读其**有界**正文）。A1 层 2b 的 `TREE_NAVIGATION`
#: 回读因此平列四个新字段：`nav_keys` / `parent_keys` / `key_tier`（`declared` 或 `ancestor`）/
#: `rule_version`，`nav_read_scope` 相应新增 `declared_tier_aspects` / `ancestor_tier_aspects`。
#: 这么改是为了让「某个子项读到的到底是自己的正文，还是父节点正文」以及**靠哪一层的键**读到的
#: 在产物里自证：只看读根节点号，读者区分不出本层精确命中与祖先层回退命中。载荷形状变了，
#: 故报告 schema 一并前进。**这四个字段是只读回读**，不参与任何判据，也不构成 coverage 判定：
#: 读到父节点正文只证明**材料到达**，采购/生产/销售各项是否 covered 仍由正文、引用与 Contract
#: 规则各自判定。判据方向不是放宽（新规则是收紧的整段判据；祖先层是**回退**且不改变任何
#: 既有阈值），历史 run 与冻结资产保持只读。
#: 同批另一处**实修**（同属 acc-18，未另起版本号）：源头对账汇总处原先直接写
#: `row["evidence_ids"]`，而重切读数只有 `requested_evidence_ids`——**只要本轮真的发生过一次
#: 有界重切，整个 A1 源头对账就抛 `KeyError` 退化成 `unavailable`**，报告顶部却只字不提
#: （`_a1_source_reconciliation` 会把异常吞成 `unavailable`，而 `honesty` 只在
#: `read_only_diagnostic` 时才写那一段）。两种字段名是事实，故按 `_recall_evidence_ids` 显式
#: 兼容、缺字段如实返回空；并给 `unavailable` 补一条**顶部诚实行**：缺读回面是「这一环未执行」，
#: **不等于**「材料到达为零」。这是修一个真实丢掉的读回面，不是放宽任何判据。
#: acc-19（M930-3 业务纵链补充门 / 跨文档方案 §0.3.1「调用归属与事前准入」分步实施第一步，
#: 2026-09-24）：**研究侧调用进共享门**。改动三处，都是「让已经声称的事成立」：
#:   * **门必须在研究之前安装**。旧序是 `with RealEnvironment(...)` → `env.inputs = _build()`
#:     （研究就在这里发生）→ 才 `LB.install(budget_gate)`，于是研究阶段 `reserve_attempt` 看到
#:     `_INSTALLED is None` **既不识别、也不计数、也不受任何上限**。现在先建政策、先比结构上界、
#:     先 `install`，再进环境；`finally` 里卸载并把已发生的尝试账本封存进产物。
#:   * **研究侧三个 prompt 登记为独立轴**（`AXIS_RESEARCH`）。旧登记表恰 4 键，都是写作侧；
#:     一旦门覆盖研究，未登记的 `research_action_v1`/`research_answer_v1`/
#:     `research_entailment_v1` 会在**首次研究调用**处因「归属不清」被拒（与 `enforce` 无关）。
#:     登记的是**归属**，不是数值：写作轴每一条数字与 `approved` 标志**一字未动**
#:     （62/144、40/100、0/0、`total_max_attempts=244`、`total_approved=False`），研究侧
#:     `AXIS_RESEARCH.approved=False` ⇒ **真实模式下预检即整轮拒绝、零 provider 请求**。
#:     `m930-3-budget-4` → **`m930-3-budget-5`**（登记表内容变了就必须升版）。
#:   * 研究在 **topic 作用域**内计量（`LB.research_scope(topic_id)`），写作仍按节计量；两条轴
#:     的账互不消耗，**244 仍是写作轴上限**，研究侧上限（每 topic 36、整轮 216）**单独成行**，
#:     不得把 244 说成全部 provider 调用的总上限。
#: 判据方向不是放宽：研究侧上限**数值一个都没改**（`max_llm_calls_per_topic` 仍镜像既有 36），
#: 本次只是让它进入同一个事前门；历史 run 与冻结资产保持只读。
#: acc-20（跨文档方案 §8.11「逐 topic 自派生」，**由用户于 2026-09-24 明确裁决改走本项**）：
#: 研究侧上限从「**一个共享值** = 3 × 首个 topic 的 aspect 数 = 36，六个 topic 共用」
#: 改成「**逐 topic 自派生** = 每 aspect 上界 × 该 topic 自己的 aspect 数」。原因不是想多要额度，
#: 而是共享值**在方向上就错了**：它给 4 个 aspect 的 `industry_scale_cycle` 9 次/aspect，
#: 却给 18 个 aspect 的 `company_business` 2 次/aspect——那不是上限，是按 topic 大小分配的配额。
#: 实测后果已经出现过：a7 里 `company_legal_risks` 用了 37、`industry_scale_cycle` 用了 40，
#: 双双越过那个共享的 36，而两者都**没有失败尝试**（即都是合法工作被上限卡住，不是重试烧掉的）。
#:   * 每 aspect 的上界**不是新定的数字**，而是从既有代码常量推出：
#:     `max_need_rounds_per_aspect=2`（内层循环最多 2 轮，`force_converge` 再给 1 轮 = 3）
#:     ⇒ 每个 need ≤ 3 次 action + 1 次 answer + 1 次 entailment = 5 次；
#:     needs 数 ≤ 1 + `max_need_rounds_per_aspect` = 3 ⇒ **每个 aspect ≤ 15 次**。
#:     实测最坏读数是 10 次/aspect（a7 industry：4 aspect 用了 40 = 10/aspect），低于 15，
#:     因此 15 是盖得住的上界而不是估计值。方案 §8.11 原文写的 `3 ×` 已明显低于实测（industry
#:     用了 8 次/aspect），照抄会把它从 36 **降到 12**——把一个已经合法跑出 32 次的 topic 当场
#:     卡死，即本文件反复写明的那条「上限低于结构上界不是更严格，而是把一次注定失败的运行
#:     伪装成可运行」。故本版按**代码常量推出的结构上界**取 `15 × aspects(该 topic)`。
#:   * `max_tool_calls_per_topic` 同样改为逐 topic（同一表达式、同一个输入，不能只改一半）。
#:   * 研究轴 `AxisCap` 因此带 `per_scope_max_attempts`（逐 topic 各一个上限），整轮上限 = 各
#:     topic 上限之**和**（不是「单值 × topic 数」）。镜像复核也从「逐值相等」改成**逐 topic 逐值**。
#:   * 预检新增**研究轴的覆盖检查**（此前只管 narration）：研究轴上限若低于结构上界即整轮拒绝、
#:     零 provider 请求——与写作轴同一条理由、同一处落点。
#: `m930-3-budget-5` → **`m930-3-budget-6`**。写作轴 62/144、40/100、0/0、244 **一字未动**。
#: acc-22（M930-3 **定点返修 C 批**，2026-09-25）：本批不加功能，只修五处**已定位**的缺陷——
#: 取料质量、逐来源检索责任、公司/行业写作的整束连坐、财务提示词与确定性校验门的冲突、
#: 以及截断与报告一致性收口。按 C1→C5 逐项实施，每项只跑受影响的聚焦回归；代码冻结后再跑
#: 一次 MOCK 与一次**新建**的真实运行。**本批升版一次**，后续各项不再另起版本号（沿用 acc-18
#: 「同批另一处实修」的处理方式）。
#:   * **C1（导航规则 `anp-3` → `anp-4`：topic 标题段按 question 归属）**。旧规则把冻结
#:     Contract 的 topic 标题**整段**交给该 topic 的**每个** aspect 当祖先键，于是真实 Contract
#:     的主题标题「主营业务、经营模式、产业链、收入成本毛利构成、客户与供应商集中度」被拆开后，
#:     `经营模式` 一段落进了**该 topic 全部 aspect** 的祖先键——`收入构成` / `毛利率` /
#:     `产品与方案` / `应用场景` / `产业链位置` 因此凭共享父节点读到与它们无关的
#:     「采购/生产/销售/研发」正文（三份真实文档实测都是同一个 `3经营模式` 节点）。
#:     新规则 `navtopic-segment-question-ownership`：topic 标题的每个完整标签段**先归属到恰好
#:     一个 question**（分层多信号纯字符串判据 `exact_label_segment` → `label_containment` →
#:     `shared_substring` → `aspect_field_substring`，取最强层，同层多问争用即丢），再随该
#:     question 进入祖先层。争用或全无信号时**不强选**：该段丢弃并留下可审计原因
#:     （`topic-segment-contended` / `topic-segment-no-signal`），既不猜也不悄悄换一个答案。
#:     判据输入只有冻结 Contract 的 topic 标题 / question 文本 / aspect 声明字段——**公司名、
#:     页码、答案词、文档标题都不参与**。`PROFILE_SCHEMA_VERSION` 仍是 `anps-3`（`parent_keys`
#:     字段本来就有，变的是它的**取值**），`anp-1`/`anp-2`/`anp-3` 进历史登记表。
#:     A1 层 2b 的四个回读字段形状未变，但**判读窗口变了**（同一字段名的取值语义换了，
#:     `rule_version` 读出 `anp-4`），读产物的人据此判断「这条材料是按哪一层的键读来的」⇒
#:     报告 schema 一并前进。这是「先修取料质量」的第一条：**读到祖先正文只证明材料到达，
#:     不构成 coverage**；六个 `company_business_model.*` 子项仍按各自事实/引用判 covered。
#:     判据方向是收紧（新规则只在**更强**的判据下才给键，且争用即丢），历史 run 与冻结资产只读。
#:   * **C3（`proposal-set-rejections/3` → `/4`：定向重提案的跨修订去向）**。真实 run 暴露的
#:     整束连坐是：路径 B 的一张材料里出现高风险硬事实面，整束候选被拒，而**与它无关的候选**
#:     也一起归零。用户裁决**否定**了「从原束里删掉被牵连的几条、拿剩下的充数」（那会把
#:     「完整有序提案集」这条冻结契约悄悄改成「挑剩下的」）；本项改为**有界、定向的重提案**：
#:     把「按同一份参考基线重新提出完整提案集」的权利定向交回给**已有的那一轮**写作重试。
#:     * 被拒记录新增两个字段：`answered_by_attempt`（排定由第几次生成回答；未排定为 `None`）
#:       与 `reproposal`（`RejectionReproposalTrace`：`from_attempt` / `to_attempt` /
#:       `note_version` / `original_candidate_ids` / `next_candidate_ids` / `destinations`）。
#:       两者都是**加字段**，没有删除也没有改语义 ⇒ 载荷形状变了，故 **/3 → /4**。
#:     * 去向词表**封闭四码**：`carried_verbatim` / `carried_rebound` / `carried_ambiguous` /
#:       `not_reexpressed`。跨修订的候选身份无法用 `candidate_id` 连（它含 `draft_revision`，
#:       逐次生成必变），唯一确定性可比的是**逐字节相同的 `claim_text`**——这也正是合并阶段
#:       已有的去重键。**不设「改写」码**：改写与否在机械判据下不可判定，设了就是编造归因。
#:     * `destinations` 的键集**恒等于**该束 `candidate_ids`（完整、有序、不裁剪）；原束的
#:       `ProposalSetRejectionRecord` 身份与逐条原因**一字不动**，只多两个字段。
#:     * **不新增调用额度**：定向轮用的就是本策略已有的那一轮重试（`max_llm_retries`），
#:       因此结构上界仍是「`max_llm_retries + 1` 轮 + 1 轮栏目定向补足」。写作轴 62/144 / 244
#:       **一字未动**，`APPROVED_BUDGET_POLICY_VERSION` 仍是 `m930-3-budget-6`。
#:     * 产物的**判读窗口变了**：`acceptance_report.json` 的 `proposal_set_rejections` 块新增
#:       `directed_reproposal`（排定数 / 真的产出下一修订数 / 词表 / 上限 / 触发 kind），
#:       `proposal_set_rejections.json` 顶层新增四个同类键；诚实性说明新增一段「`reproposal=null`
#:       读作没有可写的跨修订事实，**不得**读作都还在」。故报告 schema 一并前进。
#:       （acc-24 的 ④ 把这一句**再改细**：`null` 只说明「本轮没有形成可核验的重提案去向」；
#:       「没排定」与「排定而未成形」由 `answered_by_attempt` 分辨——见该批的逐项记录。）
#:     判据方向是收紧（新增「去向键集必须完整对应原束」「不得借定向轮删掉无关候选」两条），
#:     不是放宽。**未实施**「从原束删除被牵连候选」这条被否决的替代方案。历史 run 只读。
#:   * **C5（截断条款的文字与实际控制流对齐；研究轴进可见面）**。旧文字说「首次截断即整轮
#:     停止」，而代码里早已存在**有界**的确定性缩批重试（`MAX_SWEEP_SHRINK_STEPS`=2，对半拆分
#:     后重问），于是 A7 文字、账本 note、实际控制流三者互不一致；更糟的是 **A7 判的是「有
#:     没有出现过截断」**：一次被缩批救回的截断（该节最终照常产出 `SectionDraft`）也会被
#:     记红，而真正该红的「截断进入了结局」反倒混在里面说不清。
#:     * 处置口径改为逐字一致：截断内容**一律不解析、不采用**；**唯一合法恢复路径**是有界的
#:       确定性缩批重问；**缩至单个 aspect 仍被截断、或缩批额度用尽即 fail-closed**（该节
#:       typed `batch_truncated`，该节没有产出）；不自动重跑、不换模型、不换 prompt。
#:     * **A7 改判最终结局**（`truncation_policy.recovered` / `.unrecovered` /
#:       `.research_unrecovered`）：救回的截断逐条列出、**不为红**；为红的是未被恢复的写作轴
#:       截断（对应节没有产出）与研究轴截断。
#:     * **研究轴进入可见面**：`RT.RealResearchLLM` 此前**不记任何调用**，研究轴的截断对 A7
#:       完全不可见。现在它逐次记 `calls`（与 `LlmNarrationClient` 同形状），A7 的截断判据
#:       因此覆盖两条轴并逐条标明 `axis`。**只加可见面，不改研究链行为**：`reject_truncated`
#:       仍缺省 `False`，研究响应照旧被返回——正因为如此，研究轴的截断**没有**合法恢复路径，
#:       它的残缺正文已被返回并继续被解析，本项据此如实计入 `research_unrecovered` 并判红；
#:       要不要让研究轴也 fail-closed 是**内容语义**变更，不在本次授权内。
#:     * 产物**判读窗口变了**：`truncation_policy` 新增 `recovered` / `unrecovered` /
#:       `research_unrecovered` / `research_fail_closed` / `research_client_available` /
#:       `truncated_attempts_research_axis` 六个键，`truncated_calls` 每条新增 `axis` /
#:       `reject_truncated` / `call_kind`；A7 的红/绿判据换成「结局里有截断吗」。同批不另起
#:       版本号（仍是 `acc-22` / `report-21`）。
#:     判据方向是收紧（判据从「出现过截断」变成「截断进入结局」，且多覆盖一条轴），不是放宽。
#: acc-23（M930-3 **前置修复 T 批**，2026-09-25，用户就本次真实 r4 单独授权时点名的三项窄修）：
#: 只修三处，不开放其他扩权。**本批升版一次**（`acc-22` → `acc-23`、`report-21` → `report-22`）。
#:   * **T1（权威 Pack 读回的材料包人读/机器读文件）**。`cross_document_evidence.md` 只是按来源
#:     分组的证据读回，写作侧只给 `writer_material_manifest` 的逐条 `chars`/`sentences` 计数——
#:     没有一个产物能从**候选材料**这一层回答「这一次到底取到了哪些原文、它们各自被保留还是
#:     被拒绝、理由是什么」。新增 `material_pack.md`（人读）与 `material_pack.json`（机器读），
#:     逐条给完整原文 + 文档四轴身份 + 页/span locator + topic/aspect 对应 + 处置去向与理由。
#:     **读自 `state.inputs.pack_sets` 的权威 Pack**（`ResearchMaterial` / `AspectResearchResult`
#:     / `ResearchMaterialDisposition` / `TopicResearchPack.source_set`），**不是**从 Writer
#:     prompt 倒抄，也不止给 ID 与字数。两个文件都进 `ARTIFACTS`（因此进 `artifact_index.json`
#:     的逐文件 sha256 账）与 `manifest.json` 的 `material_pack` 指针。历史 run 只读。
#:   * **T2（来源台账成员一律按四轴身份核对）**。`DocumentSourceSet.role_of` 此前**只比
#:     `document_id`**：同 id、错 `document_version` / `evidence_set_version` 的键会被**当成
#:     成员命中**并返回一个角色——内容寻址身份下这是两份不同的文档，就近匹配会把「声明了 B、
#:     拿到了 A」变成静默错配。新增 `SM.source_key_axes` 作为全链唯一的键比较单位，
#:     `role_of` 走四轴并有 `member_key` / `has_member` 两个具名入口；逐来源台账的三处关联
#:     （`member_source_classes` / `material_ids_by_aspect_source` / `dispatched_without_call`）
#:     与调用痕迹匹配全部改为四轴，`member_source_classes` 不再接受**裸 `document_id`** 作键；
#:     `aspect_source_responsibility_fingerprint` 的规范排序键也改四轴（只按 id 排序时，
#:     同 id 不同版本两行的先后取决于输入顺序，同一台账会有两个指纹）。`SourceManifest.role_of` /
#:     `by_document_key` 增加可选的 `evidence_set_version` 强制核对；真实 runner 的
#:     `member_lives` 与会话查找不再按 `document_id` 找当前锚。**同 id 错身份一律 fail-closed**，
#:     且错误信息分开报「同 id 错身份」与「根本不在集合内」（前者是身份写错，后者是漏登记）。
#:     判据方向是收紧，历史 Pack 一字未改。
#:   * **T3（研究轴拒收截断）**。C5 只做到了「可见」：`RealResearchLLM` 记 `calls` 但
#:     `reject_truncated` 仍是缺省 `False`，于是 provider 的残缺正文照旧被返回，而研究轴三个
#:     解析入口（动作 / 答案 / 蕴含）都只拿 `resp.text`、都不查 `finish_reason`——**任何一处**
#:     漏查，半截 JSON 就会被解析成合法结构并进入后续材料。现在门设在**调用返回之前**
#:     （`RealResearchLLM._call` 逐字传 `reject_truncated=True`，并新增同名类属性供 A7 读取），
#:     截断因此是一次**失败的调用**：`self.calls` 仍逐条记下 `call_id`/`finish_reason`/usage，
#:     但半截正文到不了任何解析入口。研究轴**没有**缩批这一层（写作轴的有界缩批口径一字未动），
#:     故被截断的研究调用即该次调用失败、该问如实停在既有 typed 终态 `MODEL_OUTPUT_INVALID`，
#:     不在 stop_reason 里同「模型给了非法 JSON」混成一句。`truncation_policy` 的 `policy`
#:     文本与判据窗口据此同步（`research_unrecovered` 改述为「该句柄自报不拒收」这一档，
#:     生产适配器落在 `research_fail_closed`，**不为红**——它没有进入任何内容）。
#:     判据方向是收紧（从「可见」到「不采用」），不是放宽。
#: acc-24（M930-3 r4 后的**业务内容定点返修**，2026-09-25）：先修失败路径与逐栏目取料，
#: 不靠统一放大预算/top-k 换「完整」。**本批升版一次**（`acc-23` → `acc-24`、
#: `report-22` → `report-23`；`proposal_set_rejections.json` 升到 `/5`）。逐项：
#:   * **① 失败路径 `len(None)`**。真实 run r4 的三节全部没有 `SectionDraft`，A1 源头对账据此
#:     把 Writer 侧五项如实记成 `None`（不是 `[]`/`0`），而诚实性说明直接对它取 `len()` ⇒
#:     `TypeError` ⇒ **整份 `acceptance_report.json` 与 `artifact_index.json` 都没写出来**。
#:     「这一层没读回」与「这一层确实一份都没有」是两条不同的读数，因此新增
#:     `_count_clause` 把 `None` 如实印成「不可读回（不是 0）」，并把同一处第二段（
#:     `company_section.draft.claim_candidates`，`draft` 为 `None` 时同样是崩溃点）按有无
#:     `SectionDraft` 分流：有 Draft 才说「材料全部可读、无一被丢」，没有 Draft 只说前六层。
#:     另加 `_write_run_assembly_fallback`：装配阶段无论为何抛错，都先写出
#:     `status="assembly_error"` 的**降级报告** + `artifact_index.json`，再重抛原异常——
#:     现场一定要留下，代码缺陷不得被咽掉。
#:   * **② A7 改判「截断调用 → 缩批调用」的批次谱系**。旧判据是「该节最终有没有
#:     `SectionDraft`」：r4 的两批截断（`80e7bf29…` / `225db388…`）各被两个缩批子请求答回、
#:     该节此后因**别的**拒绝类型失败，于是两次**已经恢复**的截断被记成结局缺口——读回的人会
#:     去补一个不存在的缺口。新判据走身份：账本里被截断那次调用的 `call_id` → 拒绝审计里的
#:     那条批记录 → 沿 `parent_batch_id` 收它的缩批子树 → 子树里**有无无子孙的截断叶**。
#:     为此 `BatchCallRecord` 新增 `parent_batch_id`（`WriterPlanBatch` 早已有它，只是没带上
#:     产物），`proposal_set_rejections.json` 因此升到 `/5`。四桶：`recovered` /
#:     `recovered_downstream_failed`（救回但该节此后因其它原因失败）/ `recovered_unverified`
#:     （谱系不可核验，只以该节产出为证）都不为红，`unrecovered` 为红。
#:     **旧载荷仍要能读**：r4 自己的 `proposal_set_rejections.json` 就是 `/4`（没有那一列），若
#:     一律判「不可核验」，r4 那两次已经答回的截断照样会被记成结局缺口——本批要修的错原样还在。
#:     因此对旧载荷按**全或无**的规则从 `label` 家族、`shrink_depth` 层级与 `aspect_ids` 的
#:     保序划分重建父子关系（`_reconstruct_batch_children`，版本
#:     `BATCH_LINEAGE_RECONSTRUCTION_VERSION`）：重建得出来就判「已恢复」并把依据记成
#:     `reconstructed_by_label`，套不上就退回「不可核验」，**不猜**。
#:   * **③ 研究轴说明按实际句柄状态生成**。旧 A7 红字在写作轴单独判红时也会照印「该句柄自报
#:     `reject_truncated=False` ⇒ 残缺正文已被返回并继续解析」——r4 现场写作轴 2 次未恢复、
#:     研究轴 0 次，这句话与研究侧事实相反。现在两条轴各自按实际读数成形，研究轴为 0 时明确
#:     写「**没有**未恢复的截断」并给出 `research_fail_closed` 的实得次数。
#:   * **④ `reproposal=null` 的读法改回它实际能支住的那一句**。旧措辞把它写成「一律读作**没有
#:     可写的跨修订事实**」——那把「**没排定**」与「**排定了但那一轮未成形**」压成一句话，
#:     并且给了一个产物支不住的结论：`null` 只说明「本轮没有形成可核验的重提案去向」。要分辨
#:     是哪一种，看 `answered_by_attempt`。
#:     以上四条都**没有**放宽任何 fail-closed：判据方向是「从错误的判据换成正确的判据」。
#: acc-25（同上批定点返修，继续；**第二次升版**：`acc-24` → `acc-25`、`report-23` →
#: `report-24`）。逐项：
#:   * **⑤ 内容形态分流（用户 ③）**。材料构建期早就算出 §二 2.3 的内容形态（`selection_form`
#:     的所问事项 / 选项 / 选中状态），但 `sections/material_context._decode_envelope` 把它
#:     **丢在解析层**：Writer 与所有门都看不见「这一份是勾选表单行」与「这一份是叙述材料」的
#:     分界（全仓此前只有写入处与词表定义处两个出现点）。现在它随正文一起走到读的人手上——
#:     `ResolvedWriterMaterial` 新增派生字段 `content_qualification`（**不改** `reading_view`：
#:     原文仍是抽取式、一个字符不改；也不新增 `material_type`，形态是**读法**不是新身份），
#:     写手请求面（`pw-11`）的每行 materials 因此带着它，读回产物（`material-pack/3`）逐条摊开
#:     五列 + **允许用途**（`asked_item_applicability_only`）+ **排除项**（不得推否定性结论 /
#:     不得替宽栏目作证 / 不得当叙述正文）。表单行读不定（`resolved=false`）时**不产出**结构化
#:     事实，也不因此新增缺口：只有 Contract 必需信息确实未满足才另记缺口。**不做**「一律删除
#:     勾选符号」的粗清洗：原文与定位逐字保留，形态只是**另开一路**。
#:   * 判据方向是**加一路分流**并收紧表单行的用途，不是放宽任何门。
#: acc-26（同上批定点返修，继续；**第三次升版**：`acc-25` → `acc-26`、`report-24` →
#: `report-25`）。逐项：
#:   * **② 逐栏目取料：读根资格（`anp-4` → `anp-5`，条目 wire `anps-3` → `anps-4`）**。
#:     本批不动任何预算 / top-k / 阈值，改的是"哪几个候选**有资格当读根**"。r4 现场的三类
#:     逐栏目误召（产品与方案读 `十一、衍生产品情况`；期间口径读 `报告期内的内部控制制度
#:     建设及实施情况` 等 13 份无关章节；客户集中度读 `十三、关联方及关联交易`）成因同一个：
#:     **声明键的片段命中了无关标题**，而完整标签段判据此前只施加在祖先层。现在读根要过
#:     两道纯字符串判据——(a) `navroot-label-anchored`：本层键的**匹配形态**须落在标题的某个
#:     完整标签段上（整段相等不限长度；前 / 后缀与整键夹带的放宽**只对短标题**生效，长句
#:     披露标题里切出的短片段不算）；(b) `navroot-subject-adjacency`：读根标题 / 路径须与该
#:     aspect 的**主体标签**（`requirement_text` 括号前的头部，条目新增字段 `subject_head`）
#:     共享 ≥2 字最长公共子串。报告的**判读窗口因此变了**四件事：`rule_version` 自 `anp-5`
#:     起写 `anp-5`；`fallback_reason` 新增一档 **`no_anchored_read_root`**（有候选、也够分，
#:     但上提后没有任何一支读根定位得到这一栏 ⇒ 读集为空，**不退回**读那些不合格的根）；
#:     `candidates[].discard_reason` 新增两档 `not_label_anchored` / `not_subject_adjacent`
#:     （被刷掉的根仍逐条留在候选审计里，不静默消失）；`subject_head` 为空时**不加**第二道
#:     判据（推导不出主体标签的 aspect 不得凭空收紧自己）。判据方向是收紧：**不**改打分、
#:     **不**改近分带宽度、**不**放宽任何 fail-closed；"够分但定位不到"从"读了无关章节"
#:     改成"如实留 typed 缺口"。同批不新增功能、不改任何提示词。
#: acc-27（同上批定点返修，继续；**第四次升版**：`acc-26` → `acc-27`、`report-25` →
#: `report-26`）。逐项：
#:   * **⑥ 行业节的「仅用标点拼接 Claim」改判为「拒 + 有界定向重组织」**。判据一字未改
#:     （第 8 条四条机械判据仍是同一实现、同一条错误文本；`nrules-9` / `ng-9` 与 `nrules-8` /
#:     `ng-8` 在「什么样的 composed 句通过」上完全一致）。改的是**被拒之后**：组织器在
#:     `parse_organizer_plan` 之后、建段落之前做一次**确定性**重组织（模块版本
#:     `NARRATIVE_ORGANIZER_REORGANIZATION_VERSION = "norg-reorg-1"`，随 trace 落 `reorganization_*`）：
#:     第一级按模型**自己的**句中句末标点切段并逐段重绑 Claim（文本逐字不变，`"".join(parts)`
#:     必须等于原文，模型写的衔接语留在相邻那段）；切不出全合法的段时落第二级——每条 Claim
#:     各自成一句**逐字事实句**（`NS.render_factual_text` 的唯一口径），且第二级**只对
#:     「Claim 文本 + 纯标点接缝」的句子生效**（判据唯一实现在 `NS.is_punctuation_only_join`）：
#:     接缝上有模型自写组织语或自造表面时禁止，否则「模型自造了一个数字」会被改写成一份不含该
#:     数字的合法正文（那是把拒掉的版本**洗掉**，不是修好）。`claims_not_present_in_order` 与
#:     高风险表面、含糊期间、越界条数一律**不补救**，照旧 fail-closed。
#:   * **⑥-拒绝的替代方案（需要裁决，本批没有实现）**：「有界定向重组织」曾考虑**再发一次**
#:     定向组织 LLM 调用（让模型自己把它写的那一句拆开）。不行，且不是口味问题：本 runner 的
#:     叙述上限是**结构性推导**的——`_assert_budget_covers_structural_bound` 按
#:     「每节 (aspect 批数 + 最大缩批步数) × 3 × 2 + 2」算出公司 62 / 财务 44 / 行业 38（合计
#:     144），那次推导里"每节写作另有门后自然组织 1 次"已经用掉了那一格；再加一次每节 1 次
#:     就变成 63 / 45 / 39（合计 147）> 62 / 44 / 38，该断言直接失败。要放行就必须抬批准过的
#:     预算常数——那是另一件事，须单独授权，本批**不做**。
#:   * **⑥-新判据：正文不得替系统自报检索 / 核验**（`SYSTEM_PROVENANCE_PHRASES`，第 9 条）。
#:     年报转述的行业数据不得被写成「系统已独立联网核验」；材料原文写着的这些字照转不误
#:     （短语必须能在**本句声明的 Claim 文本**里逐字找到）。它**不进** `HIGH_RISK_SURFACE_MARKERS`：
#:     写入侧的候选授权面与 `claim_binding_gate` 一字未动，不因为一个文风判据丢内容。
#:     `narr-6` 不动（wire 未变），只有判定集前进：`nrules-8` → `nrules-9`、`ng-8` → `ng-9`。
#:     勾选表单行是**读法**不是禁词：正文里逐字转录的勾选原文必须照旧放行（已加正例），
#:     「不做一律删除勾选符号的粗清洗」这条纪律因此有回归钉住。
#:   * A3 新增一条 runner 侧**独立复算**：行业节正文不得自称系统的检索 / 核验（本文件自带一份
#:     短语元组与 `_has_cjk` 同级的独立实现，不回放写入侧结论）。
#: acc-28（同上批定点返修，继续；**第五次升版**：`acc-27` → `acc-28`、`report-26` →
#: `report-27`）。逐项：
#:   * **一条填错的补件申请不再杀死整节**（§三/1 的前半）。真实 run r5 的 financial 死在这里：
#:     候选与草稿单元都已成形、确定性硬门也已通过，门后构造补件时**一条**申请的四个 id 跨行
#:     拼了（`topic_id` 取自主题 `fin_source_scope` 那一行、`aspect_id` 取自
#:     `fin_statements_availability` 那一行）。旧实现整节抛出 ⇒ **已经把内容写出来的那一节
#:     连同已过硬门的 Draft 一起消失**，而产物里连「模型提过一条填错的申请」都读不到——它
#:     长得像「本节本来就写不出内容」。两件事被压成一件：**本节的内容状态**与**某一条申请
#:     自己不成立**。校验一字未减（七条判据与 fail-closed 全部原样），改的是**不成立的范围**：
#:     新增 `_follow_up_needs_partition` 逐条隔离，不成立的照原样留档成 typed 记录
#:     （封闭原因码 `FOLLOW_UP_REJECTION_CODES`、可读原因、它自己那份响应里的序号
#:     `spec_index`、它填的四个 id），成立的照常成为 `FollowUpNeed`；`_follow_up_needs` 的
#:     「全有或全无」语义**原样保留**给需要它的调用方（这不是把门放宽，是新增一条被判定的路）。
#:     同一条隔离也用在**被拒各轮**（`_pending_follow_up_needs`）：那边过去已经在隔离，现在与
#:     成功节共用**同一份**实现与**同一种**记录长相，不再是两套。
#:   * **产物侧新增这一半**：`PackWriteOutcome.follow_up_rejections`（缺省空元组）→ sidecar →
#:     `failure_diagnostics.json` 逐节 `rejected_follow_up_applications`（**无论本节有没有
#:     Draft 都内联**：这正是「没有 Draft」与「有一条填错的申请」要能分开读的那些节）→
#:     `follow_up_needs.json` 顶层 `follow_up_rejected_applications` /
#:     `total_rejected_applications` → 报告的诚实性说明。两处产物因此升号：
#:     `follow-up-needs/1` → `/2`、`failure-diagnostics/1` → `/2`。升号的理由是**读法**而不是
#:     「多了字段」：/1 的读者会把「本节一条待裁决诉求都没有」读成「模型没有提过任何补件」，
#:     把上面那个消失事件读成「本节写不出内容」。它**不是** gap、不进 Draft 身份，也不是候选
#:     层面的拒绝（那三种身份各自另有字段，不得互相填充）。
#:   * **行业节的「外部来源」要求本轮没接上，而且要说清是「哪一跳没接」**（§三/3 的后半，
#:     即「找出正式 `InformationNeed` 为什么从未把 `external` 要求交给 Router/ToolRegistry」）。
#:     两个原因**各自独立、分开写**：
#:       - (a) 本轮 `RouteContext.external_research_enabled=False`（与本次授权范围一致：更窄，
#:         不是放宽）。真实外部路径另有已报告的阻断（缺 `event_date`、`dateLastCrawled` 冒充
#:         `published_at`），不在此处绕过。
#:       - (b) 本轮注入的 `information_need_builder` 是纯结构件
#:         `evals.test_demo_topic_runtime._NeedBuilder`：它**不**把冻结
#:         `EvidenceRequirementRef.source_classes` 投成 `InformationNeed.required_source_types`，
#:         也不投 `required_evidence_types`，`time_scope=None`。于是
#:         `routing.router.requires_external_source()`（外部路由的**唯一**判据）在任何 aspect 上
#:         都不可能为真——**即使开关打开**，正式的 `InformationNeed` 也带不出 `external` 要求。
#:         这一条不是「本轮没有可检索的外部来源」，是**链路里那一跳没接**。
#:     于是「冻结 Contract 声明某 aspect 只能取外部来源」与「链路本轮根本没发过外部请求」
#:     是两条**各自**可读的事实，不得合并成「行业没有可写内容」。诚实记录落成
#:     `external_retrieval` 块（`external-retrieval/1`，`_external_retrieval_honesty`）+ 报告顶部
#:     一条诚实性说明：写的是「**本轮未检索**」，**不是**「已证明没有外部来源」；发行人对第三方
#:     数字的转述（年报正文里的 SNE / IEA / SMM 等）也不得因此被读成**已验证**的外部事实。
#:     本轮**不连接**外部检索，也**不替换**那个结构件：替换会改变正式链路的 need 内容与路由
#:     结果（那是另一件事，须单独授权）。
#: acc-29（同上批定点返修第二段；**第六次升版**：`acc-28` → `acc-29`、`report-27` →
#: `report-28`）。这一段的判据与产物都在**材料侧**，逐项：
#:   * **勾选表单行的形状读法修正**（§二 2.3）。r6 里挂在 `founded_date` 下的四份股东/实控人
#:     勾选材料被读成**普通正文**（`text`）——真实勾选行的形状是
#:     ``⟨选项串⟩ ⟨这一行管着的那句话⟩``，旧读法把选项串之后那句当成最后一个选项的标签，
#:     长度一超 `SELECTION_OPTION_MAX_CHARS` 就整行退化成正文，于是那条边**看起来**能支撑
#:     成立日期或业务构成，实则不能。修正后读成 `selection_form`：选项串之后那句逐字记进
#:     新的第六列 `trailing_content`（随行留档，`TREE_MATERIAL_SELECTION_EXCLUSIONS` 明文
#:     禁止用它作支撑）。反向错误同样挡：勾选行后面接着**一整段披露正文**的混合片段必须留在
#:     `text`——判据是「只承载一句话」（句末标点恰好一个且在末尾 + 句读总数不超过
#:     `SELECTION_TRAILING_CLAUSE_MAX`；实测真实披露段落是"一个句号 + 四个逗号"的长句，
#:     只数句末标点拦不住）。三处升号，理由都是**读法/形状变了**：读法 `tmr-1` → `tmr-2`、
#:     资格列五列 → 六列（`material-pack/2` → `/3`）、写作策略 `pw-12` → `pw-13`。
#:     **材料一份都没删**：被纠正的是栏目归属与「这条边最多能证明什么」。
#:   * **栏目归属作为一条可读读数落进产物**（§二 2.3 的前半，acc-29 内实现）。r6 里挂在
#:     `company_identity_basic.founded_date` 下的四份材料**证明不了成立日期**，却以「这一 aspect
#:     读到的材料」的身份出现在 `aspect_results[].material_ids` 里。根因不是采信写错，是**归属
#:     机制**没写出来：该 aspect 的导航 `status=fallback`、零候选、零读节点 ⇒ `aspect_materials`
#:     为空 ⇒ 有界 Evidence 重切补位，重切产物被记成该 aspect 的材料。新增只读派生层
#:     `material_attribution`（`material-attribution/1`，规则 `mar-1`，**只读诊断，不参与判据**）：
#:     逐条从 `TREE_TOOL_RESULT` / `EVIDENCE_FALLBACK_RECUT` 两条真事件派生归属依据
#:     （`aspect_tree_read_set` / `evidence_fallback_recut` / `unattributed`＝如实登记未知），
#:     并给出 `aspects_only_on_fallback_recut`（栏目覆盖**仍未达成**）。材料不重命名、不删除，
#:     `material_ids` 一个字都不改——纠正的是**这条归属最多说明什么**。本批**不**另起升号：
#:     acc-29/report-28 已覆盖这一段（一段一批号，同一批里报告形状不升两次）。
#:   * **支撑资格成为一条独立的候选级判据**（§二 2.3 的后半）。新增整束拒绝 kind 与逐候选
#:     原因 `path_b_ineligible_material_scope`（第四张词表同步：结构化 kind 表；触发类恰好
#:     两种）。它只作用于**路径 B factual 边**：候选文本（去空白）不是该表单行 `asked_item`
#:     的子串、或含有该行的 `trailing_content` ⇒ 这条边不能承重。这一条**不是**高风险面：
#:     材料留着、身份不动。因此它与高风险面**共用**同一份额度
#:     （`MAX_DIRECTED_REPROPOSAL_PASSES`，值仍为 1，预算中性）与同一次排定，整束明细合成
#:     **一句**（分两条记会变成"同一束被拒两次"的假账），说明文本升到 `hrrp-2`，第四条出路是
#:     「把叙述逐字收进该行所问事项之内，或改绑权威事实，两者都做不到就撤下并如实发补件」。
#:     数字/否定/勾选状态/主体身份仍由**未改动的**高风险面独立挡住（纵深防御，任一条不通过都
#:     不得入正文）。
#:   * 行业节的**外部来源那一跳**在本批（acc-30，定点返修步骤 ③）**接上了**：注入的
#:     `information_need_builder` 换成生产实现 `harness.aspect_need_builder
#:     .FrozenAspectInformationNeedBuilder`（`anb-1`），它用权威派生器
#:     `TS.derive_support_eligibility(...).required_source_classes` 把冻结来源类搬进正式
#:     `InformationNeed`，于是 `routing.router.requires_external_source()` 在**声明了
#:     `external` 的 aspect 上真的为真**。两条事实仍然**分开写**、各自可读：
#:       - (a) 本批仍 `EXTERNAL_RESEARCH_ENABLED=False`（无联网授权）。且开关现在**是门**：
#:         `harness.policies.external_research_unauthorized` 在**执行前**拒掉
#:         `SEARCH_EXTERNAL` / `FETCH_EXTERNAL` / `SNAPSHOT_EXTERNAL` 三个动作并留痕，
#:         因此"need 带出要求"不会静默变成"真的联网"。
#:       - (b) `acc-29` 及之前那条"注入的是纯结构件、丢弃 `source_classes`"的原因**已消除**，
#:         但它的分类码作为**历史值**保留在封闭集里：只有当注入的构造器**又**变回那个已知
#:         结构替身时才会被产出（否则历史读法会被无痕改写）。
#:     终态因此是一个**封闭枚举**：`not_retrieved`（本轮没有任何外部执行痕迹）与
#:     `retrieved_none_adoptable`（**执行过**外部检索、但没有可采用的外部事实）互斥，后者
#:     必须有正面证据；`not_retrieved` 不得与任何"已检索"表述同时出现。
#: acc-31（M930-3 r7b **后**定点返修第一段；**第七次升版**：`acc-30` → `acc-31`、
#: `report-29` → `report-30`）。理由只有一个，且是**跨层判据漂移**，不是"多了字段"：
#:   * **A2 的单元格判据与已裁决的分量规则对齐（C4 落在验收侧）。** wire 门（`narrative_schema.
#:     financial_cell_components`）在 C4 已改成按**分量**核对 Claim 文本（数值 / 期间表达 /
#:     完整代理口径限定语各自逐字出现），但本 runner 的 A2 仍旧要求「**整格文本**是配对 Claim
#:     的连续子串」。两者在 r7b 的真实数据上**必然打架**：`financial_table_cell` 用权威自己的
#:     标点把数值与限定语拼成 `-9.94。代理口径（PROXY_FINANCE_EXPENSES）`，而写作提示词要求
#:     `claim_text` **中间不得有句末标点**，于是 Claim 只能写成
#:     `2023年度的利息保障倍数为-9.94，代理口径（PROXY_FINANCE_EXPENSES）`——三个分量逐字一致，
#:     只差 `，` 与 `。` 一个标点。**标点从而成了第四条隐含要求**，与写作侧要求数学上互斥，
#:     r7b 的 4 个代理单元格（`-9.94` / `-14.29` / `-10.28` / `429.48`）因此全被作废，
#:     A2 判红。本段把验收侧判据换成与 wire 门同一口径的**分量**判据。
#:     **一条判据都没有放松**，逐条保留并仍由本门独立重算：
#:       - 单元格必须**精确等于**该权威事实应有的可见文本（输出侧；裸值表格照旧判红）；
#:       - `financial_pack` 事实坐标必须落在本 authority artifact 内且恰一条；
#:       - 期间口径（封闭取值）、与公式 `period_requirement` 的复算一致、列名 == 独立重算的
#:         期间表达（时点写作期间量的误读照旧判红）；
#:       - Claim 必须有可派生的 citation ID；
#:       - 呈现位置唯一（正文句与表格不得同时出现同一条 Claim）。
#:     新增的只是把「整格连续子串」换成「三个分量各自逐字出现」——错误数值、错误期间、漏代理
#:     限定语、错配 Claim 仍然各自判红（分量判据**更严**：旧判据下"单元格是 Claim 子串"只要
#:     出现一次即可，分量判据要求每一段都在）。负值公式**不动**：`(a)` 裁决（保留计算所得
#:     负值并显示 `PROXY_FINANCE_EXPENSES` 及其代理口径限制）继续有效，本段不碰任何计算。
#: 历史产物**不改写**：r7/r7b 的目录、`acceptance_report.json`（`report-29` 及其 A2 结论）
#: 原样留在盘上，新判据只影响本版及以后的 run。
#: acc-32（M930-3「先证明能成稿」定点批 §一.2）：把**逐候选裁出**这条出口带上产物与报告。
#: 新增的是「一束被拒**不一定**整束作废」的可读性：束里只有部分候选踩线时，被点名的逐条拒掉、
#: 其余以新修订继续走链，该记录多出一个 `carve_out` 逐条对账；`proposal_set_rejections.json`
#: 的顶层多出裁出规则版本、原因词表、`model_calls_added: 0` 与「与 `reproposal` 互斥」
#: （`proposal-set-rejections/7` → `/8`）。**判据一字未减**：裁出这条出口只在**非**裁出轮上
#: 排定、一条都没幸存即整束拒绝、原束的完整有序身份与逐候选审计一字不改。
#: 历史产物**不改写**：r7/r7b 的目录与 `acceptance_report.json`（`report-29`/`report-30`）原样
#: 留在盘上，新读法只影响本版及以后的 run。
#: acc-33（M930-3「先证明能成稿」定点批 §一.2·读者面，2026-09-26）：**主体名称可核实**。
#:   * 报告输入声明的字段变了（新增主体名称），故 `SUBJECT_DECLARATION_VERSION` `subj-1`→`subj-2`：
#:     旧读者不得把带名称的新声明当 `subj-1` 读，也不得把缺名称的旧声明当新声明读；
#:   * 名称只由**声明**给出（`--subject-name`，必填），权威库只被用来**核对**——从
#:     `financial_source_document`（`subject_match_status='matched'` 且
#:     `declared_company_name` 非空）读出该主体**唯一**一个权威自己声明过的名称，与本次声明
#:     逐字比对。库里有 0 个名字、有多个不同名字、或与声明不一致，都拒绝。改前
#:     `ReportJobInput.company_name` 直接填成 `company`（证券代码），于是章节与正文里凡是引用
#:     公司名的地方都印出代码——那是把标识当名字。**不得**改成「从库里挑一个名字」：那等于让
#:     存量库决定「谁被写」，与 `--subject` 必填所拦住的正是同一类错误；
#:   * 报告载荷形状随之变（`subject_declaration` 新增 `subject_name` 读数位、未核对路径新增
#:     `subject_name_declared`），故报告 schema `report-31` → `report-32`。判据方向是收紧，
#:     不是放宽；历史 run 保持只读。
#: acc-33 → acc-34（指令 D §二（b），2026-09-27）：新增 `source_attribution.json` /
#: `source_attribution.md` 两个读者面产物（来源归属轴 `srattr-1`：材料名 + 登记身份@版本 +
#: 精确页码 + 可核实披露日，披露日不可核实就写「披露日未知」），报告侧新增
#: `source_attribution` 指针块。归属语由**系统**从已登记身份确定性渲染，写者一个字也不能写；
#: 它**不改正文、不改正文指纹**（正文仍由唯一渲染器产出并逐字节重算比对），只作逐句对账。
#: 因此报告载荷形状变（新增块）而判据方向不变：`report-32` → `report-33`。历史 run 保持只读。
#: acc-34 → acc-35（指令 D §三，2026-09-27）：新增 `pre_gate_draft.json` / `pre_gate_draft.md`
#: 两个**失败侧**的只读产物（门前留存口径 `pgr-1`），报告侧新增 `pre_gate_draft` 指针块；
#: `proposal_set_rejections.json` 的每条拒绝记录新增 `retained_pre_gate`（逐批门前草稿正文 +
#: 逐单元出处轴 + 该批自己的标签 + 三档来源），因此该产物 `proposal-set-rejections/8` → `/9`。
#: 判据**一字未减**：它不参与任何门的判定、不进 `SectionResult`、不进正文与预览、不放宽任何门；
#: 改的是**失败侧的可读面**——「这一节到底写出过什么」在 r8 的产物里读不出来（LLM 账本只有
#: 元数据，事后不可恢复），现在逐批留档。因此报告载荷形状变（新增块）而判据方向不变：
#: `report-33` → `report-34`。历史 run 保持只读。
#: acc-35 → acc-36（M930-3 业务取材纵链修复 §一，2026-09-28）：把 runtime 的**逐条 typed
#: 栏目未达原因**接进只读诊断与人读页。改前 `TopicRuntimeResult.gaps` 只在**运行现场**，
#: 而 `failure_diagnostics.json` 逐 aspect 只报覆盖门/资格门，读者看到的是「这一栏没过」；
#: 于是「本轮预算没让这次检索发生」「派发了、导航给不出候选」「材料拿到了、话对不上本栏目」
#: 「表格槽位没有合格表材料」「查过但合格未命中条件未成立」这五件事在产物里被压成同一件，
#: 最容易被写成「语料里没有」。这一版：
#:   * `RealInputs` 新增 `topic_results`（逐 section 的 `TopicRuntimeResult`，**只读诊断**用，
#:     不参与任何判据）——typed 原因只在它身上，Pack 本体不带；
#:   * `_failure_diagnostics_body` 逐 aspect 新增 `column_unmet`（逐条原因 + 派发去路计数 +
#:     材料/事实/表对象计数），逐节新增 `column_unmet_summary`（按闭集逐条的栏目计数，
#:     计数为 0 的那一条也列出）；闭集从 `harness.topic_runtime.UNMET_COLUMN_REASONS` 取，
#:     **不**在这里抄一份字面量；
#:   * 本节取不到运行结果（财务节走 `FinancialFactPack`，不走 topic 研究相位）时为
#:     `available=false` 的**不可判定**条目，**不是**空 `entries`（空会被读成「本栏没问题」）。
#: 因此诊断产物升号 `failure-diagnostics/2` → `/3`；判据**一字未减**，报告载荷形状未变，
#: 故 `ACCEPTANCE_REPORT_SCHEMA_VERSION` 停在 `report-34`。历史 run 保持只读。
#:
#: `acc-37`（M930-3 第二批 · 表对象读法）：
#:   * 材料包读回里**表对象信道**的材料不再印成「内容形态：读不出」——它们的形态
#:     （`table_object`）、**允许用途**（`navigable_reading_material_only`）与**排除项**
#:     （`no_numeric_authority` / `no_support_from_cell_values` / `no_computation_by_llm` / …）
#:     从信封自己的 `reading_policy` 原样带出，与 §二 2.3 的 span 形态词表**分开成档**；
#:   * 节级直方图因此多一族 `table_object`，`unavailable` **只**表示「信封里既没有可读形态、
#:     也不是表对象信封」；节级另加 `table_object_material_ids`；
#:   * 形状变了 ⇒ `MATERIAL_PACK_SCHEMA_VERSION` 升到 `material-pack/4`。
#:     判据**一字未减**：这只是读回侧的读数，一处也不改运行时的准入/保留/资格判定。
#: `acc-38`（M930-3 业务取材纵链修复 §三 · 替身选材与组织）：
#:   * 过长却有业务价值的原句不再整条落选：`_slice_atoms` 把它按**小句边界**切成有界原子
#:     （每个原子都是原句的连续子串，上限仍是 `_SLICE_MAX`，不把整段当一条 Claim、不补写
#:     主语），逐原子各自过同一套过滤器——因此「同一句里有一条高危」不再是「这一句全不可用」；
#:   * 候选原子必须**自带陈述对象**：接上一小句往下说的残片（「并通过长期协议…」「能满足快充…」
#:     「形成全面、先进的产品矩阵」）不再作为事实候选进入正文。它们逐字、可回查、非高风险，
#:     写入侧看不出来，但读者从正文中段读起不知道说的是谁；逐条记 typed 未采用原因，不补主语；
#:   * 材料按**来源角色**稳定重排（`current_state_source` 先、同类较旧者后）后提案：同一段
#:     原文同时落在较新与较旧两份同类材料里时，不再由较旧的那一份先占住这句话；
#:   * 组织侧每个接缝各取一个中性并列连接语（不再整句共用一个），且句首事实不得是承接残片。
#:   判据**一字未减**：这四处都只改**替身提案/组织的选材与行文**，Pack、Writer manifest、
#:   资格门、绑定门、蕴含门、引用门与只读 UI 一律不动；候选被拒仍即整节 fail-closed。
#:   报告载荷形状未变，故 `ACCEPTANCE_REPORT_SCHEMA_VERSION` 停在 `report-34`。历史 run 保持只读。
#:   * **能说什么、不能说什么（按实测口径，不按意图）**：同版本两次独立离线重放（`dual_offline_r12`
#:     与 `_r13`）的 company 材料集逐份逐字节相同、研究侧十项读数逐项相同 ⇒ **同版本内确定性成立**，
#:     故 `dual_offline_r11`→`r12` 的 78→80 不是运行间抖动。但本批**不宣称**「Pack 读数不变」：
#:     跨版本还能读到 identity 88→83、legal 102→96、写入侧 company 29→41、recut 9→12 等变化。
#:     其中一部分与「写入侧补件回灌研究」相容（两次运行的台账都含 writer 之后的再次研究块段），
#:     但**首个 writer 调用之前**的 identity 段本身即 80→75，这一处**未被归因**，如实登记为残留——
#:     既不写成「与本批无关」（本批未证明），也不写成「已查明」（本批未查明）。本批能证的是：
#:     上面四处改动的**代码路径**只在替身提案/组织；Pack 侧的读数差异本批没有归因，留待裁决。
#:   * 另登记一条**未修**的选材瓶颈（本批只量不修）：`max_path_b_per_material=2` 是**每份材料**
#:     至多 2 条原子。逐材料复算（limit=2 vs 不限）显示 7 份材料共 **15 条**「原句里已可安全切出、
#:     逐字可回查、自带陈述对象」的原子从未进入候选（其中 6 条来自可表达当前状态的 2025 年材料）。
#:     它与本条第一处（8–80 窗口 + 小句切分）是**两个独立成因**：前者决定「一句能不能被切开」，
#:     后者决定「切开后这一份材料里能有几条被提案」。本批不动这个上限。
#:
#: **`acc-39`（§0.18 W8 单通道的读取面）**：`tlp-1` / `gto-3` / `gtm-1` 落地后，目标表的正式
#: 材料改由图侧单通道产出，信封种类由 `tom-1`（`table-object-material-v1`）换成 `gtm-1`
#: （`graph-table-material-v1`）。读取面因此改两处，**判据一字未减**：
#:   * `_table_object_qualification` **同时**认两条信封种类（`gtm-1` 当前单通道 ＋ `tom-1`
#:     历史 run）。只认旧那一种，新信道的表材料会落回 `kind=unavailable`，于是**已放行、
#:     已进 Pack、已进 Writer 清单**的表在人读页印成「内容形态：**读不出**」——与 acc-37
#:     修掉的是同一个错，只是换了一条信道重演；
#:   * 读数带出**实际观察到的** `envelope_kind`，两条信道的读数不得被读成同一批对象的读数
#:     （形状变了 ⇒ `MATERIAL_PACK_SCHEMA_VERSION` `/4` → `/5`）。
#:   报告载荷形状未变，故 `ACCEPTANCE_REPORT_SCHEMA_VERSION` 停在 `report-34`。历史 run 保持只读。
#:
#: **`acc-40`（M930-3 定点业务纠正① · 勾选行的**栏目归属**）**：读法 `tmr-2` → `tmr-3`——
#: 勾选表单行的「所问事项」**只**取行内前缀，行内没写主语时这一列**留空**，
#: **不再**回指所在节点标题。改的是**读法**（同一份原文在 `tmr-2` 与 `tmr-3` 下产出不同的
#: `asked_item`／不同的 payload 字节／不同的 content 指纹），因此必须升号。真实损害已在
#: r 系列 run 里点名：NDSD_2025 第 28 页那行 `□适用 √不适用` 所在节点是
#: `（8） 主要销售客户和主要供应商情况`——那是**栏目**，原件的勾选框属于其下
#: 「主要客户其他情况说明」「主要供应商其他情况说明」两个**子项**；回指把「集中度这一栏
#: 不适用」写成可由表单行证明的结论，而同一页紧跟着的集中度表（前五名合计销售额/占比）
#: 在原件里真实存在、与那行勾选框无关，且当前**未取得数字资格**（记为系统读取/交付缺口，
#: 不是「来源称不适用」，也不是「用户未提供」）。空 `asked_item` 是 fail-closed
#: （`pack_writer._scope_confined` 对空所问事项一律返回 `False`）。**材料一份都没删**：
#: 原文、locator、指纹、选项与选中状态全部留档，丢的只是那条凭空合成的归属。
#: 读取面同步一处：所问事项为空时印成人话（`行内没写主语`），并把「所在节点」标成**仅导航坐标**，
#: 不让读者把它当主语补回来。报告载荷形状未变，故 `ACCEPTANCE_REPORT_SCHEMA_VERSION`
#: 停在 `report-34`。历史 run 保持只读。
RUNNER_VERSION = "m930-3-acc-40"
#: 验收报告自身的 schema 版本（`acceptance_report.json` 的读者据此判读）。
ACCEPTANCE_REPORT_SCHEMA_VERSION = "m930-3-acc-report-34"
#: §三 3（acc-28）：**本轮是否启用外部检索漏斗**。这里定义一次、声明一次、报告读它——
#: 不在第二处再抄一个字面量（抄一遍就会在下次改判时变成第二个真值）。acc-30 起它是**真门**：
#: `harness.policies.external_research_unauthorized` 按它决定外部动作执不执行。
EXTERNAL_RESEARCH_ENABLED = False
#: §三 3：`external_retrieval.not_connected_reason_codes` 的**封闭取值集**。
#: `need_builder_drops_source_classes` = 注入的 need 构造器是已知的结构替身（历史原因，
#: `acc-29` 的现场；接线生产实现后不再产出，但保留在集里以免历史读法被无痕改写）；
#: `external_research_not_authorized_this_run` = 本轮开关为关，外部动作**执行前被拒**。
EXTERNAL_RETRIEVAL_NEED_BUILDER_DROPS_SOURCE_CLASSES = \
    "information_need_builder_stub_drops_source_classes"
EXTERNAL_RETRIEVAL_NOT_AUTHORIZED = "external_research_not_authorized_this_run"
EXTERNAL_RETRIEVAL_REASON_CODES = (
    EXTERNAL_RETRIEVAL_NEED_BUILDER_DROPS_SOURCE_CLASSES,
    EXTERNAL_RETRIEVAL_NOT_AUTHORIZED,
)
#: acc-29 的旧常量名（值不变）：只在"注入的构造器是已知结构替身"时产出。保留旧名是为了让
#: 既有读者的回查路径不断——它现在只是上面那条封闭集里的一个成员。
EXTERNAL_RETRIEVAL_NOT_CONNECTED = EXTERNAL_RETRIEVAL_NEED_BUILDER_DROPS_SOURCE_CLASSES
#: `external_retrieval` 块的 schema 版本（读者据此判读「本轮未检索」这句话的边界）。
#: `acc-30` 升 `/2` 的理由是**读法**而不是"多了字段"：`/1` 的读者会把 `not_connected_reason_code`
#: 当成唯一原因码（字符串），而现场现在有**两条各自独立的原因**、且多了一个**终态**枚举——
#: `/1` 的读者会把"没接上"读成唯一结论，读不出"要求已到达链路、是本轮未授权"。
EXTERNAL_RETRIEVAL_SCHEMA_VERSION = "external-retrieval/2"
#: 外部检索**终态**（封闭枚举，互斥）。`not_retrieved` = 本轮没有任何外部执行痕迹；
#: `retrieved_none_adoptable` = **执行过**外部检索但没有可采用的外部事实（须有正面证据）。
EXTERNAL_TERMINAL_NOT_RETRIEVED = "not_retrieved"
EXTERNAL_TERMINAL_RETRIEVED_NONE_ADOPTABLE = "retrieved_none_adoptable"
#: §二 2.3（acc-29）：材料**归属依据**读数面的 schema 与规则版本。规则版本只标**派生规则**的
#: 那一套（哪条真事件的哪个字段推出哪种依据），与材料本身的身份、与任何资格判据都无关：
#: 这一层是只读诊断，不参与判据，也不改任何持久化对象。
MATERIAL_ATTRIBUTION_SCHEMA_VERSION = "material-attribution/1"
MATERIAL_ATTRIBUTION_RULE_VERSION = "mar-1"
#: 归属依据的三种取值（封闭集合）。`aspect_tree_read_set` = 这一 aspect 的标题树有界读集交出来的；
#: `evidence_fallback_recut` = 该 aspect 的读集**什么都没交出来**之后，有界 Evidence 重切补位的；
#: `unattributed` = 两条真事件都解释不了这次归属——**如实登记未知**，不是「干净」。
ATTRIBUTION_TREE_READ_SET = "aspect_tree_read_set"
ATTRIBUTION_EVIDENCE_FALLBACK = "evidence_fallback_recut"
ATTRIBUTION_UNATTRIBUTED = "unattributed"
DEFAULT_RESULTS_ROOT = "evaluation/results"
RUN_ID_PREFIX = "m930_3_acceptance_"

MODE_OFFLINE = "offline"
MODE_REAL = "real"

#: 调用预算（M930-3 任务二：调用预算门窄范围修复）。三条口径纪律：
#:
#:   1. **批准的量与计量的量必须是同一个量**。旧口径把「每节 ≤2 次（门前提案 1 + 门后组织 1）」
#:      当成全链预算，而代码实际计量的是「写作 + 逐条蕴含 + 门后组织 + 可选章级评估」——
#:      两者不是同一个量，且检查发生在**三节全跑完之后**。
#:   2. **分项列示**：写作/组织（`narration`）与逐候选蕴含（`claim_entailment`）各自有上限，
#:      不再用一个数字冒充全链预算。可选章级评估（`section_llm_evaluator`）同理。
#:   3. **事前拦截**：三个层级（本类 / 本节 / 整轮）都在 `llm.budget` 的共享入口**发请求之前**
#:      判定；第 N+1 次请求不会到达 provider。门在**进入真实环境之前**安装（研究发生在
#:      `RealEnvironment._build` 内，晚装一步研究就不受门约束）。
#:   4. **两条轴，不合并**：写作轴（`AXIS_WRITING`：各类 `CategoryCap` + `total_max_attempts`）
#:      与研究轴（`AXIS_RESEARCH`：三类研究 prompt 共用一个 `AxisCap`，**逐 topic 各一个上限**，
#:      整轮 = 各 topic 上限之和）。两条轴的计量互不消耗；研究调用**不得**吃写作额度，
#:      写作调用也**不得**吃研究额度。报告里研究侧上限**单独成行**——写作轴的整轮上限
#:      **不是**全部 provider 调用的总上限。
#:
#: `approved=False` 的类别 ⇒ 真实模式下**一个请求都不发**（在预检处整轮拒绝，明确列出还缺
#: 哪些批准）。「本类本轮不许发」不用「不批准」表达，而用 `approved=True` + 上限 0：那是一个
#: **已批准的**决定（有出处、可复核），不会让整轮无法以强制模式运行。
#:
#: 预算政策里**没有**费用型 input/output token 限额（`max_output_tokens=None`）：本次授权的
#: 目标是「材料完整、事实正确、正文自然」，token 不是本次的约束轴。每次请求实际发出去的
#: `max_tokens` 是**技术参数**，按阶段给足（见 `OUTPUT_CAPACITY_*` 与
#: 报告里的 `llm_calls.output_capacity`），并逐次记进账本。
APPROVED_BUDGET_POLICY_VERSION = "m930-3-budget-7"
#: 已批准模型：真实模式只允许这一个（换模型 = 换口径，不属于已批准范围）。
APPROVED_BUDGET_MODEL = "deepseek-v4-pro"

#: **本次运行的批准出处**（逐字引用，不是转述）。批准是**用户的**决定，不是 runner 自批：
#: 2026-09-26 正式提示词明文授权「一次全新、create-only 的 `r6` 真实运行，不覆盖 `r1`–`r5`，
#: **不自动重试或连续发起 `r7`**」，并写明本次只用于验证公司与财务是否形成正式、带引用、
#: 可供人阅读的内容，另如实记录行业外部来源尚未检索的缺口。因此本版把写作轴与研究轴的
#: `approved` 置为 True——**批准的是这一次运行，且数字一个都没改**（写作轴仍是 acc-18 重估后的
#: 62/144、40/100、0/0、244；研究轴是**acc-20 逐 topic 自派生** = 15 × 该 topic 的 aspect 数，
#: 整轮上限 = 各 topic 之和 990，见下面的 acc-20 段）。这句话写进每一类的 `basis`，读产物的人
#: 不必去翻对话记录才知道「谁批的」。
#:
#: **本常量只换出处、不换数字**：上一版引的是 2026-09-24 那次授权（`r5` 的批准依据）。`r6` 是
#: 另一次授权，且带自己的条件（单次、不自动重试、财务负值取 (a)），因此这里逐字换成今天的
#: 那一句；上限、类别、`approved` 取值与所有门一字未动。
#:
#: 与跨文档方案 §0.3.1(c)「本轮不自行批准真实运行」的关系：那一句约束的是**方案作者自行
#: 批准**（方案未被用户与 Codex 验收之前不得自批）。现在用户的正式提示词已经给出这一次运行
#: 的批准，方案要的是「批准须来自用户」，而不是「永远不许跑」。两条不冲突。
#: **仍然不许的**：为跑通而抬高任何上限、改小结构上界断言、换模型、跳过模型身份/结构上界/
#: 归属登记三项预检——那不是「获批」，那是把未获批的运行伪装成可运行。
APPROVAL_PROVENANCE = (
    "2026-09-26 用户正式提示词：「授权执行一次全新、create-only 的 r6 真实运行，不覆盖 r1–r5，"
    "不自动重试或连续发起 r7」（本次只用于验证公司与财务是否形成正式、带引用、可供人阅读的"
    "内容，并记录行业外部来源尚未检索的真实缺口；不得据此宣布三节通过或 M930-3 关闭。"
    "财务负值呈现按用户裁决取 (a)：保留计算所得负值并显示 `PROXY_FINANCE_EXPENSES` 及其代理"
    "口径限制，不改「不适用」、不改冻结公式政策）。")

#: **最终句语义门 B 的批准出处**（与 `APPROVAL_PROVENANCE` 并列，**不覆盖**它）。
#:
#: 为什么要有第二份出处：B 是本批（2026-09-27）新增的**真实调用点**，新的调用点必须显式登记
#: 归属与上限，而它的批准依据不是 2026-09-26 那次 r6 授权（那次授权里根本没有这个调用点）。
#: 逐字引的是用户 2026-09-27 的正式提示词（指令 D 第四项）。同一句提示词也明文写明本批
#: **不自行发起新的真实 LLM run**——「上限已声明」与「这一次已经获批发出真实请求」是两件事：
#: 真实运行仍须用户单独授权（见 `_call_budget(mode=REAL)` 的预检与报告里的四栏记账）。
FINAL_SENTENCE_APPROVAL_PROVENANCE = (
    "2026-09-27 用户正式提示词（指令 D 第四项）：「实施用户已裁决的最终句语义审核 B……真实模型"
    "调用走共享客户端并留账；调用上限只用于防循环和截断恢复，不以费用为由削减材料或内容质量。」"
    "（同一提示词写明本批「**不自行发起新的真实 LLM run 或外部检索**」：本项只声明上限与归属，"
    "真实运行须单独授权。）")

#: 已批准的门前写作重试额度（`WriterPolicy.max_llm_retries` 取值域 {0,1} 的最大值）。
#: **单独成一个常量**，是为了让下面 `_narration_structural_bound()` 从**同一处**取值推导上界：
#: 若两处各写一个 1，改了其中一处而另一处没改，预算上界就会悄悄与真实链路脱钩。
APPROVED_WRITER_MAX_LLM_RETRIES = 1

#: 逐阶段的**输出容量**（发往 provider 的 `max_tokens`），单位是 token。
#:
#: 这不是「费用上限」，而是「让该阶段的输出结构能完整出来」的容量选择；数字全部落在**本 provider
#: 上已经成功返回过**的量级内（`logs/llm` 5435 条历史真实调用：`pack_section_writer_v1` 实测
#: 输出 ≤2261、`section_financial_v3` ≤3354、结构化蕴含 `research_entailment_v1` ≤1714；同一
#: 端点成功返回过的最大输出是 8102，即请求 8192 时未被拒）。选更小的值会把「容量不够」变成
#: 「截断即失败」，选超出端点能力的大值会让第一次请求直接报错——两种都会让这次唯一授权的真实
#: 预览以失败收场，因此这里取有实测支撑的档位，而不是「越大越好」。
OUTPUT_CAPACITY_NARRATION = 8192      # 门前提案（含草稿单元）+ 门后自然组织：要出完整正文
OUTPUT_CAPACITY_CLAIM_ENTAILMENT = 4096   # 逐候选蕴含：一条结构化决定（裁决 + 简短理由）
#: 最终句语义门 B：**一个 draft revision 的全部承载事实最终句**逐原子的结构化决定（`nsfid-1`）。
#: 它的输出量随句子数与原子数增长（一节的最终句可以有几十句、逐句若干原子），因此**与蕴含同一
#: 档 4096**，而不是更小：这一门的失败形状恰好是「输出被截断 ⇒ 未能形成决定 ⇒ 整节阻断」，
#: 容量不足会被读成「语义核验没通过」。逐次实际值仍记在账本与客户端记录里。
OUTPUT_CAPACITY_FINAL_SENTENCE_FIDELITY = 4096
OUTPUT_CAPACITY_BASIS = (
    "本 provider 实测：历史真实调用里同族 prompt 的输出上限为 section_financial_v3 ≤3354、"
    "pack_section_writer_v1 ≤2261、结构化蕴含 research_entailment_v1 ≤1714 token；本端点"
    "成功返回过的最大输出为 8102 token（请求 8192）。叙述类取 8192（≥ 实测最大值 2.4 倍，"
    "且仍在本端点已验证的档位内），蕴含取 4096（≥ 实测最大值 2.4 倍）。逐次实际值记在账本"
    "的 `attempts[].max_tokens` 与客户端记录的 `calls[].max_tokens` 里。"
    "acc-17 追加：叙述类改为**按 aspect 范围分批**之后，一次请求要输出的**更少**（本批而非整节），"
    "因此 8192 这个容量对「一次请求」只多不少；相对的，调用**次数**上去了——次数由预算门按类别"
    "计数（见 `_narration_structural_bound()`），不靠压缩容量来省。"
    "容量与次数是两条轴，不得互相顶替：调小容量换「省」会把「容量不够」变成「截断即失败」。")

#: 非 section 的归属：半事务反例探针**不**在预算门内（它的替身不发真实请求，且若让它占账，
#: 它会按真实模式的整轮上限把一次真实运行掐掉）。这条在 A7 里逐字说明，不是默认放行。
ROLLBACK_PROBE_SCOPE = "<rollback_probe:ungated>"

#: **缺省**节序 = v1 profile（三节）。本文件里约 50 处读它；驱动端读到 profile 后由
#: `bind_section_order()` 就在地重绑，使那些读点整体跟随所选 profile（双节演示版 → 两节）。
#: 为什么不在 50 处逐点改成「profile 参数」：那既会漏改一处（漏掉的那处会让「三节」在双节
#: run 里复活：报告里凭空多出一节全空的会计行），又会把节序塞进本来只做会计/报告的函数签名。
SECTION_ORDER = ("company", "financial", "industry")

#: 财务节的**唯一**字面量真值。财务不是主题研究节：它有自己的相位（`FinancialFactPack`）与
#: 自己的权威载荷（`FinancialAuthorityInput`），因此凡按「本节有没有跨文档主题研究」分流的
#: 地方都按这个身份判，而不是按所处位置猜。
FINANCIAL_SECTION_ID = "financial"

#: 本次进程实际使用的 demo scope profile 路径。`None` ⇒ 用
#: `planning.demo_scope.DEFAULT_PROFILE_PATH`（旧三节，**行为一字不变**）。
#: `--demo-scope-profile` 只改这一个进程内的取值：不写任何状态文件、不改冻结资产。
_DEMO_SCOPE_PROFILE_PATH: str | None = None


def active_profile_path() -> str:
    """本次进程实际使用的 profile 路径（缺省即 `DEFAULT_PROFILE_PATH`，不复制其字面量）。"""
    from planning import demo_scope as SC
    return _DEMO_SCOPE_PROFILE_PATH or SC.DEFAULT_PROFILE_PATH


def topic_section_ids(order: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """本轮的**跨文档主题研究节**：节序里除财务以外的那些（保持节序）。

    主题研究节要跑「来源 → 材料 → Pack」那条链（`company` / `industry` 这类），财务不跑；
    这条分流的唯一依据是节身份，不是「它在节序里排第几」，因此换 profile（双节演示版）时
    结果自动跟着走，不写死三节。
    """
    return tuple(s for s in (SECTION_ORDER if order is None else order)
                 if s != FINANCIAL_SECTION_ID)


def topic_section_workers(order: tuple[str, ...] | None = None
                          ) -> tuple[tuple[str, object], ...]:
    """本轮主题研究节的 `(节, worker 模块)` **有序对**（次序即节序）。

    分派只按**节身份**查注册表，不按位置、不按节数。没注册的节**整轮拒绝**（fail-closed）：
    节序是 profile 说的、报告要照它记一节，而研究相位没有对应 worker 时，静默跳过会让报告
    少一节却看不出来，拿别的 worker 顶替则是把另一节的材料记到这一节头上。
    """
    from sections import company_worker as CW
    from sections import industry_worker as IW

    registry = {"company": CW, "industry": IW}
    pairs: list[tuple[str, object]] = []
    for section_id in topic_section_ids(order):
        worker = registry.get(section_id)
        if worker is None:
            raise AcceptanceRefusal(
                f"demo scope profile 声明了节 {section_id!r}，但研究相位没有注册它的 worker"
                f"（已注册：{sorted(registry)}）：节集只由 profile 派生，不得静默跳过或顶替"
                f"（fail-closed）")
        pairs.append((section_id, worker))
    return tuple(pairs)


def section_order_for(profile) -> tuple[str, ...]:
    """节序**只由所选 profile** 派生（`DemoScopeProfile.selected_section_ids`）。

    空集与重复一律拒绝：节序无从派生时整轮就该停，而不是退回缺省（那会把「profile 说两节、
    代码按三节跑」变成一次静默的错配）。
    """
    ids = tuple(str(s) for s in (getattr(profile, "selected_section_ids", ()) or ()))
    if not ids:
        raise AcceptanceRefusal(
            "demo scope profile 未声明 selected_section_ids：节序无从派生（fail-closed）")
    if len(set(ids)) != len(ids):
        raise AcceptanceRefusal(f"demo scope profile selected_section_ids 有重复：{ids}")
    return ids


#: 已绑定 profile 的 `profile_id`（**只是报告里的观察项**，不参与任何判据）。
_ACTIVE_PROFILE_ID = ""


def bind_section_order(profile) -> tuple[str, ...]:
    """把模块级节序重绑到 profile 派生的节序，并返回它（在报告里留一笔）。"""
    global SECTION_ORDER, _ACTIVE_PROFILE_ID
    SECTION_ORDER = section_order_for(profile)
    _ACTIVE_PROFILE_ID = str(getattr(profile, "profile_id", "") or "")
    return SECTION_ORDER


def _active_profile_id() -> str:
    """报告里记的 profile 身份；未绑定时如实写空串，不替它猜一个。"""
    return _ACTIVE_PROFILE_ID


def _section_aspect_counts() -> dict:
    """各节**投影**里的 Contract aspect 数——分批策略的输入面上界，只由冻结资产决定。

    **为什么取投影全集、而不是本次选中的那个子集**：`plan_aspect_batches` 的输入是
    `selected_aspects = {a for a in scan.aspect_status if projection.aspect(a) is not None}`，
    即 `selected ⊆ projection.aspects`（`_selected_aspects` 处逐字如此）。选中的是**本次数据**
    决定的子集，而预算是**发请求之前**就要定的上界，必须是不依赖本次数据的那种数：
    `aspect_batch_count` 对 aspect 数单调不减，故「按全集推」≥「按任何子集推」。反过来按子集
    推，就会在数据比上次多的时候恰好不够——那正是预算门最不该有的方向。

    读不出来即整轮拒绝：推不出上界就没有「上限够不够」可谈，此时**一个请求都不发**。
    """
    from planning import demo_scope as SC
    from sections import pack_writer as PW
    from sections import writing_spec as WS

    try:
        profile = SC.load_demo_scope_profile(active_profile_path())
        spec = WS.load_writing_spec(str(REPO / profile.writing_spec_asset))
    except Exception as exc:  # noqa: BLE001 —— 读不出来就是推不出上界，必须 fail-closed
        raise AcceptanceRefusal(
            "冻结 WritingSpec 读不出来，无法推出叙述类的结构性上界，本次一个请求都不发："
            f"{type(exc).__name__}: {exc}") from exc
    counts: dict[str, int] = {}
    for section_id in SECTION_ORDER:
        try:
            projection = PW.ContractProjection.create(
                spec, section_id=section_id,
                contract_version=profile.contract_version,
                contract_fingerprint=profile.contract_fingerprint)
        except Exception as exc:  # noqa: BLE001
            raise AcceptanceRefusal(
                f"冻结 WritingSpec 里 section={section_id!r} 的 Contract 投影建不出来，"
                f"无法推出结构性上界，本次一个请求都不发：{type(exc).__name__}: {exc}") from exc
        counts[section_id] = len(tuple(projection.aspects))
    return counts


def _narration_structural_bound() -> dict:
    """按**代码常量 + 冻结 Contract** 推出的 `narration` 每节 / 整轮结构性上界——不写死数字。

    为什么要有这个函数：`_call_budget_policy()` 里的上限是「谁批的」，而这个函数回答「按当前
    代码，一节最坏能发几次」。两者是**两件事**：上限低于结构上界时，预检会通过、真实调用已经
    发出去，然后在中途撞上限整轮失败（旧口径 4/12 配 `max_llm_retries=1` 就是这个形状）。
    因此 `_call_budget` 在**任何调用之前**用本函数比对上限，不足即整轮拒绝。

    逐项来源（全部是**可读的代码常量**，改了它们这里自动跟着变）：

      * 每节写作轮数 = 第 0 轮 + 有界重写轮数：
        `sections.company_worker.MAX_FOLLOW_UP_ROUNDS + 1`；
      * 每次写作的**轮数** = 完整提案集至多 `APPROVED_WRITER_MAX_LLM_RETRIES + 1` 轮
        （第 1 轮整束被拒 → 模型重新输出**完整** JSON）+ 栏目定向 `MAX_SUBSECTION_FOCUS_PASSES`
        轮（`PackWriterPolicy` 之外的独立额度，且**恰好一次**：失败即整节 fail-closed）。
        **C3 的定向重提案**（`MAX_DIRECTED_REPROPOSAL_PASSES`）**不加轮**：它并不新开一轮，
        而是把「重新输出完整 JSON」这件事**定向**到上面那 `APPROVED_WRITER_MAX_LLM_RETRIES`
        轮重试里已存在的那一轮（`answered_by_attempt == attempt + 1`），且仅在
        `attempt <= max_llm_retries` 时才排定。因此 `rounds_per_pass` 恒为
        `1 + 1 + 1 = 3`，与 C3 之前逐值相同——**这不是估计，是「定向轮 ⊆ 重试轮」这条
        排定条件在本式上的直接结果**；额度耗尽时它退化为「照常拒绝并整节 fail-closed」，
        不可能多发一次调用；
      * 每**轮**的调用数 = `aspect_batch_count(本节 aspect 数) + MAX_SWEEP_SHRINK_STEPS`：
        一轮 = 一次**分批扫描**（逐批请求，逐批解析，全部成功才合并成一份完整提案集），批数由
        `plan_aspect_batches` 唯一决定；每一批被 provider 截断时该批被拒绝并按对半确定性缩小重问，
        净增 1 次调用，而每轮这样的缩小至多 `MAX_SWEEP_SHRINK_STEPS` 次。**零 aspect 也必须算
        1 次**（`aspect_batch_count` 对 ≤0 返回 1，与 `plan_aspect_batches` 的零 aspect 分支
        逐值一致）：财务/附注权威的 `scan.aspect_status` 为空，但那一节照样每轮发一次请求。
      * 每轮门后自然组织 ≤1 次（`_finalize_section` 每轮调用一次；退化路径不发请求，故是上界）。
      * 每轮**最终句语义门 B** ≤1 次（`_finalize_section` 每轮调用一次；没有承载事实的最终句时
        `FSF.drive_final_sentence_gate` **直接返回且不发任何请求**，故逐轮 1 次仍是上界）。
        B 与组织是**两个不同的调用点**（`nsfid-1` / `nsfr-1`），各成一个类别上限：合记成一类
        会让「核了多少句」在账本里读不出来。

    三个数（叙述 / 逐候选蕴含 / B）都是**上界**，不是预期值：模型第一次就覆盖全部栏目、每批都
    不被截断时，每节只有「写作轮数 × 批数 + 组织」那么多次。上界按**投影全集**（见
    `_section_aspect_counts`）算，因此它对本次实际选中的 aspect 子集只多不少。
    """
    from sections import company_worker as CW
    from sections import pack_writer as PW

    writer_passes = int(CW.MAX_FOLLOW_UP_ROUNDS) + 1
    rounds_per_pass = (APPROVED_WRITER_MAX_LLM_RETRIES + 1
                       + int(PW.MAX_SUBSECTION_FOCUS_PASSES))
    organizer_calls_per_pass = 1
    final_sentence_calls_per_pass = 1
    max_shrink_steps = int(PW.MAX_SWEEP_SHRINK_STEPS)
    aspects_by_section = _section_aspect_counts()
    calls_per_round_by_section = {
        sid: PW.aspect_batch_count(n) + max_shrink_steps
        for sid, n in aspects_by_section.items()}
    per_section_by_section = {
        sid: writer_passes * (rounds_per_pass * calls + organizer_calls_per_pass)
        for sid, calls in calls_per_round_by_section.items()}
    return {
        "writer_passes_per_section": writer_passes,
        "rounds_per_pass": rounds_per_pass,
        "organizer_calls_per_pass": organizer_calls_per_pass,
        "final_sentence_calls_per_pass": final_sentence_calls_per_pass,
        "max_shrink_steps": max_shrink_steps,
        "aspects_by_section": dict(aspects_by_section),
        "batches_by_section": {sid: PW.aspect_batch_count(n)
                               for sid, n in aspects_by_section.items()},
        "calls_per_round_by_section": dict(calls_per_round_by_section),
        "per_section_by_section": dict(per_section_by_section),
        # `proposal_calls_per_pass` 是**最坏那一节**的每轮写作调用数：单节上限对所有节是同一个
        # 数字，所以「盖得住」必须按最坏一节判，不能按平均值。
        "proposal_calls_per_pass":
            rounds_per_pass * max(calls_per_round_by_section.values()),
        # B 的每一节都相同（每节写作轮数相同、每次定稿至多一次），因此它没有「最坏一节」问题。
        "final_sentence_per_section": writer_passes * final_sentence_calls_per_pass,
        "final_sentence_total":
            writer_passes * final_sentence_calls_per_pass * len(SECTION_ORDER),
        "per_section": max(per_section_by_section.values()),
        "total": sum(per_section_by_section.values()),
        "sections": len(SECTION_ORDER),
    }


def _utc_now() -> str:
    """运行期**计时**用的即时时钟（预算的 elapsed 计时、事件时间戳）。

    注意：它**不是**运行身份时间的来源——`generated_at` / `report_as_of` /
    目录时间戳一律由 `execute_run` 里那**一次** `ReportClockInstant` 派生（O-11）。
    本函数只用于「现在几点」的持续计时，冻结它会让 elapsed 预算失效。
    """
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _j(value):
    """dataclass / tuple / Path → 可 JSON 序列化结构（只做形状转换，不改写任何取值）。"""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _j(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _j(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_j(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _all_payload_resolvers(resolvers: tuple):
    """把逐份来源的 payload resolver 组合成**一个**只读解析入口。

    单份时**原样返回**（不包一层）：单文档退化的读回面必须与改前逐字相同。多份时用
    `TopicRuntimeDependencies` 同一个组合器（0 命中 = dangling、≥2 命中 = 歧义，都
    fail-closed），不另造第二个 Store，也不落盘。
    """
    from harness import topic_runtime as TR
    if len(resolvers) == 1:
        return resolvers[0]
    return TR.CombinedPayloadResolver(tuple(resolvers))


# ---------------------------------------------------------------------------
# 离线确定性生成器（替身）：文本逐字取自**真实正文/权威事实文本**
# ---------------------------------------------------------------------------

#: 描述性原子的**候选选取**：只取以句读收尾的完整小句（换行截断的残片不是句子，表格单元格、
#: 标题残段因此自然落选）。这是替身的选材纪律，不是任何门的一部分。
_SLICE_SENTENCE = re.compile(r"[^。！？；\n]+[。！？；]")
_SLICE_TRIM = "　 ,，、：:；;（）()[]【】\"' “”‘’"
def _has_selection_marker(text: str) -> bool:
    """文本里有没有勾选/符号字形（片段是**勾选框行**，不是可读描述）。

    判据取自材料侧**同一个**符号字形集合（`TM.is_selection_marker`，含 Unicode 私用区）：
    替身若自带一份手写字形表，两处词表会各自漂移——某个字形在材料侧算勾选记号、在替身侧不算，
    替身就会把勾选框行当成「自然句」的示范。这是共用同一份判据，不是放宽任何门。
    """
    from harness import tree_materials as TM
    return any(TM.is_selection_marker(ch) for ch in str(text or ""))
#: 描述性原子的长度窗口：太短不构成可读断言，太长就不是「原子」。
_SLICE_MIN, _SLICE_MAX = 8, 80

#: 原子的**不可再分单位**：小句边界。切分只在逗号 / 分号后落刀——句读已由 `_SLICE_SENTENCE`
#: 先切开，因此这里切出的每一段都仍是**原句的连续子串**（不新增、不改写、不换序、不补主语）。
_SLICE_CLAUSE_SEP = "，,；;"
#: 「承接引导词」封闭集（`sclead-1`）：小句以它开头，说明它是**接着上一小句说**下去的。这样的
#: 分句单独拎出来读不成一句完整的话（「并通过…」「能满足…」一类）。组织侧据此不把它排在
#: 句首／段首（见 `_organizer_sentence`）；选材侧另有更严的一条（必须自带陈述对象），见下。
#:
#: 这是**替身的选材纪律**，不是任何门的一部分——门只看被选中原子的授权。
_SLICE_CONTINUATION_LEADS = (
    "并", "并且", "且", "以及", "同时", "此外", "进而", "从而", "因此", "所以",
    "以", "能", "可", "将", "为", "在", "对", "与", "包括", "其中", "通过",
)
#: 陈述对象标记（封闭、通用）：一段字面里出现任一个，即认为它自带了陈述对象。**不含**任何公司名、
#: 证券代码或用例关键词——它认的是「材料自己写没写主语」，不是「写的是哪一家」。
_SLICE_SUBJECT_MARKERS = ("公司", "本公司", "本集团", "集团", "发行人", "企业")

#: 逐条未采用原因的**唯一**字面量（选材账与人读页共用一份，避免同一件事两个说法）。
_SLICE_SHORT_REASON = "长度窗口外（替身选材）"
_SLICE_UNSPLITTABLE_REASON = "过长且无安全切分点（替身选材）"
_SLICE_RESIDUAL_REASON = "承接残片：无独立主语（替身选材）"
_SLICE_NO_SUBJECT_REASON = "无陈述对象：读不出是谁的事（替身选材）"


def _slice_has_subject(text: str) -> bool:
    """这段文本里**逐字**有没有陈述对象标记（谁）。只做字面命中，不改语序、不做句法分析。"""
    return any(marker in str(text or "") for marker in _SLICE_SUBJECT_MARKERS)


def _slice_starts_with_lead(text: str) -> bool:
    """这段文本是不是以承接引导词开头（接着上一小句往下说）。"""
    body = str(text or "")
    return any(body.startswith(lead) for lead in _SLICE_CONTINUATION_LEADS)


def _residual_fragment(text: str) -> bool:
    """这段文本是不是**承接残片**：以承接引导词开头，且自己不带任何陈述对象。

    组织侧读这一条：据此不把它排在句首／段首（选材侧另有更严的一条，见 `_slice_atoms`）。
    """
    return _slice_starts_with_lead(text) and not _slice_has_subject(text)


def _slice_clause_spans(text: str) -> list[tuple[int, int]]:
    """一句原文 → 各小句的**字符区间**（区间不含分隔符本身；空小句不产出）。"""
    spans: list[tuple[int, int]] = []
    start = 0
    for index, ch in enumerate(text):
        if ch in _SLICE_CLAUSE_SEP:
            if index > start:
                spans.append((start, index))
            start = index + 1
    if len(text) > start:
        spans.append((start, len(text)))
    return spans


def _slice_atoms(text: str) -> tuple[list[str], list[tuple[str, str]]]:
    """一句原文 → `(可用原子, [(未采用片段, typed 原因)])`。**每个原子都是 `text` 的连续子串。**

    落刀只有两处，都可逐条复算：

      * `_SLICE_MAX` 上界——从句首起**尽量装满**（贪心），于是切出的原子数最少、每段最长；
      * `_SLICE_MIN` 下界与**陈述对象**——一段要独立成一条事实，必须自己写出**是谁**的事。

    第二条是关键。只按长度切，会把「并通过…」「能满足…」「形成全面、先进的产品矩阵」这类接着
    上一小句往下说的残片当成事实；读者从正文中段读起就不知道说的是谁，而写入侧对此**看不出来**
    （它是一条逐字、可回查、非高风险的原子）。判据因此不是「以引导词开头就拒」——「在电池材料
    领域，公司拥有…」以「在」开头却是完整一句——而是**这一段的字面里有没有陈述对象**：认的是
    材料自己写没写主语，不是词表命中了几个。

    这是「有界、确定性的抽取式原子切分」：上限原样停在 `_SLICE_MAX`（不因为某段原文长就把窗口
    抬到它那么长），不把整段当一条 Claim，不补写原文没有的主语，不改语序。切不动的内容
    （单个小句自己就超过上界）或切出来仍不带主体的内容如实留下原因——**不强凑正文**。
    """
    body = str(text or "")
    if len(body) < _SLICE_MIN:
        return [], [(body, _SLICE_SHORT_REASON)]
    if len(body) <= _SLICE_MAX:
        if _slice_has_subject(body):
            return [body], []
        return [], [(body, _SLICE_RESIDUAL_REASON if _slice_starts_with_lead(body)
                     else _SLICE_NO_SUBJECT_REASON)]
    spans = _slice_clause_spans(body)
    if len(spans) <= 1:
        # 单个小句自己就超过上界：句读之间没有更细的刀口，切不动。
        return [], [(body, _SLICE_UNSPLITTABLE_REASON)]

    atoms: list[str] = []
    rejected: list[tuple[str, str]] = []
    cursor = 0
    while cursor < len(spans):
        start = spans[cursor][0]
        end = cursor
        while end + 1 < len(spans) and spans[end + 1][1] - start <= _SLICE_MAX:
            end += 1
        run = body[start:spans[end][1]]
        if len(run) > _SLICE_MAX:
            # 贪心第一段就超上界 ⇒ 首个小句自己就超过上界（句读之间没有更细的刀口）。
            rejected.append((run, _SLICE_UNSPLITTABLE_REASON))
            cursor = end + 1
            continue
        if len(run) < _SLICE_MIN:
            rejected.append((run, _SLICE_SHORT_REASON))
            cursor = end + 1
            continue
        if not _slice_has_subject(run):
            # 这一段里没有任何陈述对象。它前面的小句同样没有（贪心已尽量装满），因此**从这段里
            # 再往后切也切不出主体**——整段不采用，不补主语、不另起半句。
            rejected.append((run, _SLICE_RESIDUAL_REASON if _slice_starts_with_lead(run)
                             else _SLICE_NO_SUBJECT_REASON))
            cursor = end + 1
            continue
        atoms.append(run)
        cursor = end + 1
    return atoms, rejected


def _descriptive_slices(reading: str, limit: int) -> list[str]:
    """从**真实材料正文**里挑出非高风险描述性原子（逐字、连续、不新增任何字面成分）。

    过滤条件就是路径 B 授权的是**非高风险**原子这一条：切出的文本里不得出现数字（只能走路径 A
    预验证）、未绑定期间的含糊措辞、以及封闭高风险标记（显式否定 / 勾选与适用状态 / 表格行列与
    合计占比 / 因果与结论连接 / 趋势结论）与法人主体身份；另加三条**选材**条件——只取完整小句
    （以 `。！？；` 收尾）、不含勾选框字形、且**自带陈述对象**（见 `_slice_atoms`）。选材条件只决定
    「替身挑哪几段真实文本当提案」，不放宽任何判据：被选中的文本仍是逐字、连续、可复核的真实正文。

    过长却有业务价值的原句不再整条落选：`_slice_atoms` 把它按小句切成有界原子，每个原子各自过
    下面这套过滤器（一条原子踩了数字/高风险，不再牵连同句的其它原子）。**过滤是逐原子判的**，
    因此「同一句里有一条高危」不再是「这一句全不可用」。

    §二：替身**必须**在这里就把高风险表面挡掉，而不是提上去让写入侧拒——写入侧拒的是**候选**，
    而候选被拒即整节 fail-closed。一份材料里唯一那条描述性句子恰好带「未发生」时，正确的行为是
    「本节没有可用的路径 B 原子」（如实留下 A1 缺口，并发出有界补件申请），不是把整节写崩。
    """
    from sections import narrative_schema as NS

    out: list[str] = []
    for raw in _SLICE_SENTENCE.findall(str(reading or "")):
        # 收尾句读本身不是断言内容，去掉后仍是**连续的**逐字子串（不新增任何字面成分）。
        # 去掉的是**整个**句末标点集（`NS.SENTENCE_TERMINATORS`，含 `；`）：候选是一句里的
        # **原子子句**，句末标点由组织器在组织时给，不由候选自带——自带句末标点的候选串起来
        # 就是 `A。同时B。` 那种双句容器（冻结判据 ng-8 已拒）。
        sentence = raw.strip().strip(_SLICE_TRIM).strip().rstrip(NS.SENTENCE_TERMINATORS)
        atoms, _rejected = _slice_atoms(sentence)
        for text in atoms:
            if not (_SLICE_MIN <= len(text) <= _SLICE_MAX):
                continue
            if _has_selection_marker(text):
                continue
            if NS.high_risk_surface_tokens(text):
                continue
            if not any("一" <= ch <= "鿿" for ch in text):
                continue
            if text in out:
                continue
            out.append(text)
            if len(out) >= limit:
                return out
    return out


#: 材料在**来源角色**上的优先序（数值越小越先提案）：`current_state_source` 优先，
#: 「同类较旧／期间不可核实」的成员排最后，其余（其他系列的同主题成员、不在系列轴上的成员）
#: 居中。取值**不在这里另写一份字面量**，而是从 `source_role_scope` 的两组冻结角色读出来——
#: 抄一份就会在下次改判时变成第二个真值。
def _material_role_rank(role) -> int:
    from sections import source_role_scope as SRS
    if role in tuple(SRS.CURRENT_STATE_ANCHOR_ROLES):
        return 0
    if role in tuple(SRS.CANNOT_ESTABLISH_CURRENT_STATE_ROLES):
        return 2
    return 1


def _materials_in_role_order(materials: list) -> list:
    """把请求面给的材料行按 `_material_role_rank` **稳定**重排（同档内保持请求给的顺序）。

    为什么需要它：同一段原文常常同时出现在**同类较新的那一份**与**同类较旧的那一份**里
    （年报「主要业务」逐节照抄是常态）。替身的跨材料同文去重按**先见先得**，于是较旧的那份
    先占住这句话；若它的候选随后被「只由同类较旧材料支撑」这条门裁掉，较新的那份**再没有机会
    重新提它**——内容就整条缺席。按角色重排把顺序这件事交回给**材料自己的角色**，让较新且可核实
    的那一份先说话（较旧的那份的**独有**原文照旧提案，由同一条门如实裁决）。
    """
    return sorted(materials, key=lambda m: _material_role_rank(m.get("source_role")))



#: 替身的分段纪律：同一主题超过这么多句就**连续**分下一段（prompt 明确允许「条数多的主题可以
#: 连续分成几段」）。真实组织器自己决定切分，这里只是一个确定性的替身纪律，**不是**门的判据。
_ORGANIZER_MAX_SENTENCES_PER_PARAGRAPH = 4
#: 替身唯一有资格下的「这两条是同一件事」结论所对应的**封闭**去向码（`NS.CLAIM_OMISSION_REASONS`
#: 的成员之一，不自己发明措辞）。
_ORGANIZER_REDUNDANT_REASON = "redundant_with_selected_claim"


def _organizer_theme_groups(payload: dict) -> list[list[dict]]:
    """按输入给的 `theme_key` 归段，段序按 `theme_order`。

    未出现在 `theme_order` 里的键按其**首次出现顺序**排在后面（输入面自己不完整时按输入顺序
    兜底，不丢 Claim、也不替它编一个主题）。缺 `theme_key` 时退回 `topic_id`——即旧行为。
    """
    by_theme: dict[str, list[dict]] = {}
    for claim in payload["claims"]:
        key = str(claim.get("theme_key") or claim.get("topic_id") or "")
        by_theme.setdefault(key, []).append(claim)
    ordered = [str(t) for t in (payload.get("theme_order") or []) if str(t) in by_theme]
    ordered += [key for key in by_theme if key not in ordered]
    return [by_theme[key] for key in ordered]


def _organizer_text_key(text) -> str:
    """逐字包含判据用的字符序列：只剥标点与空白，不剥任何实词、不换字。"""
    from sections import narrative_schema as NS

    return "".join(ch for ch in str(text or "")
                   if ch not in NS.JOIN_RESIDUE_PUNCTUATION and not ch.isspace())


def _organizer_drop_contained(group: list[dict]) -> tuple[list[dict], list[dict]]:
    """把**逐字包含**在另一条里的 Claim 判为重复，保留信息更全的那条（替身唯一能下的去重结论）。

    判据只有一条、且**可复算**：两条 Claim 文本各自剥掉标点与空白之后，短的那串是长的那串的
    真子串（等长时保留**先出现**的那条）。这时短的那条**每一个字**都已在长的那条里原样出现，
    删掉短的不丢任何字面——它是「同一件事写了两遍」，不是「两件事」。

    措辞不同（多一个「或」「的」、或换了词序）的近义重复**不删**：判定「说的是不是同一件事」
    是组织器（模型）的职责（`norg-4` 起输入面就把这条写成了它的职责，`norg-5` 又补上
    `redundancy_candidates` 这份**线索**），替身代它下这个结论就
    等于替它写正文。这类疑似重复在离线重放里如实列出，留给模型裁决。
    """
    from sections import narrative_schema as NS

    if _ORGANIZER_REDUNDANT_REASON not in NS.CLAIM_OMISSION_REASONS:
        raise AssertionError(
            f"去向码 {_ORGANIZER_REDUNDANT_REASON!r} 不在冻结的封闭词表 "
            f"{tuple(NS.CLAIM_OMISSION_REASONS)} 里（替身不得自创去向措辞）")
    texts = [_organizer_text_key(c["text"]) for c in group]
    kept: list[dict] = []
    dropped: list[dict] = []
    for index, claim in enumerate(group):
        body = texts[index]
        redundant = False
        for other_index, other in enumerate(texts):
            if other_index == index or not body or body not in other:
                continue
            if len(other) > len(body) or other_index < index:
                redundant = True
                break
        (dropped if redundant else kept).append(claim)
    return kept, dropped


#: 替身的归属语候选**序**（顺序即优先序）。取值只能来自冻结的 `NS.ATTRIBUTION_MARKERS`，
#: 替身不自创措辞；先试「长而具体」的那几个，是为了让「这句是发行人自己说的」一眼可读。
_ORGANIZER_ATTRIBUTION_PREFERENCE = (
    "据公司自身表述", "据公司披露", "公司自述", "公司披露", "公司称",
    "年度报告披露", "年报披露")


def _organizer_attributions(NS) -> tuple[str, ...]:
    """归属语候选序，按冻结标记表核对后返回（表外词一律丢弃，替身不得自创归属措辞）。"""
    table = tuple(NS.ATTRIBUTION_MARKERS)
    ordered = [p for p in _ORGANIZER_ATTRIBUTION_PREFERENCE if p in table]
    ordered += [p for p in table if p not in ordered]
    if not ordered:
        raise AssertionError("冻结的归属标记表为空：替身不得为此自创归属措辞")
    return tuple(ordered)


def _connector_would_repeat(connector: str, texts: Sequence[str]) -> bool:
    """被接的那几句话里，有没有**自己就以这个连接语开头**的。

    接缝只发生在第 2 条起（`texts[1:]`）：第 1 条在句首、前面没有接缝，不存在重复。
    判据是「逐字以它开头」，不是「含有它」——中间出现的同一个词是材料自己的行文，
    与接缝无关；只有开头那一个会与接缝里的连接语连成「此外，此外，」。
    """
    return any(text.startswith(connector) for text in texts[1:])


def _organizer_sentence(NS, chunk: list[dict], index: int) -> dict:
    """一个 chunk → 一句。

    单条自成句（自带句末标点的原样保留；不带标点的由组织器补一个句末 `。`——句末标点本就是
    组织器的职责，见 prompt「句末标点是你分句的地方」）。多条用**带前置分隔标点**的衔接语
    串成一句：第一字符必须是 `，`，连接语只能落在它后面（`nrules-12` 判据 e）。

    衔接语只从**中性并列**词表 `NS.NEUTRAL_CONNECTORS`（`此外` / `同时`）里取，**每个接缝
    各取一个**并按（chunk 序号 + 接缝序号）轮转——整句共用一个连接语会让读者读到「…，此外，
    …，此外，…」那种机械串接。**不再**轮转整张 `NS.CONNECTORS`：
    那张表里有 `另一方面` / `在此基础上` / `其中` / `综上`，它们断言两条断言之间存在对照 /
    递进 / 从属 / 归纳关系——那是材料的判断，不是组织语可以替它下的结论（`nrules-13` 判据 10）。
    替身本身**没有**「材料有没有说过这个关系」的信息（它只看得到已定稿的 Claim 文本），所以它
    唯一站得住的写法就是中性并列。

    **两处改动的批次分开记**（否则「同一句话为什么变了」会被归错原因）：收**词表**
    （`NS.CONNECTORS` → `NS.NEUTRAL_CONNECTORS`，`norg-5`）与收**粒度**（整句一个 → 逐接缝
    各一个，`acc-38`）是两次独立改动；`acc-38` 的探针只打回粒度那一处就足以让公司
    `SectionResult` 逐字回到旧读数（见 `evals/test_m930_3_prewrite_offline_replay.py` 的
    `acc-38` 归因注记），因此这两维各有独立归因，不互相借力。

    发行人自述（`NS.issuer_self_description_hits`）必须带归属语（`nrules-13` 判据 11）：归属语
    落在**该条 Claim 紧前的那个组织段**——声明序第 0 条落在句首段（`attr + "，"`），第 i 条
    落在第 i 个接缝里（`"，" + connector + attr + "，"`）。归属语本身不引入数字、期间、主体或
    结论，与 `此外` / `同时` 同权，因此替身可以自行写出。
    """
    texts = [str(c["text"]) for c in chunk]
    needs_attribution = [bool(NS.issuer_self_description_hits(t)) for t in texts]
    if len(chunk) == 1:
        head_choices = _organizer_attributions(NS) if needs_attribution[0] else ("",)
        for attr in head_choices:
            body = (attr + "，" if attr else "") + (
                texts[0] if _claim_is_self_contained(texts[0]) else texts[0] + "。")
            if not NS.unauthorized_surfaces_within_claims(body, texts):
                return {"text": body, "claim_ids": [str(chunk[0]["claim_id"])]}
        raise AssertionError(
            f"发行人自述句找不到能通过冻结判据的归属语：候选 "
            f"{list(_organizer_attributions(NS))}（替身不得为此自创归属措辞）")
    connectors = tuple(NS.NEUTRAL_CONNECTORS)
    if not connectors:
        raise AssertionError("中性并列连接语词表为空：替身不得为此自创连接语")
    # 句首那条事实不得是承接残片（「并通过…」「能满足…」一类）：把第一个**自带陈述对象**的
    # Claim 稳定地提到最前，其余保持原有相对顺序。选材侧（`_slice_atoms`）已经把承接残片挡在
    # 候选之外，这里是同一份纪律在组织侧的兜底——读者读到的第一句必须是站得住的一句。
    if _residual_fragment(texts[0]):
        head_pos = next((i for i, t in enumerate(texts) if not _residual_fragment(t)), 0)
        if head_pos:
            order = [head_pos] + [i for i in range(len(texts)) if i != head_pos]
            chunk = [chunk[i] for i in order]
            texts = [texts[i] for i in order]
            needs_attribution = [needs_attribution[i] for i in order]
    # 两个优先档，顺序即优先序：先试「不与被接那句话自己的开头重复」的连接语，再退回原样轮转。
    # 材料正文常自己就以 `此外，` / `同时，` 开头，此时接缝里再补一个同一个词，读者看到的就是
    # 「此外，此外，」——中性并列语只负责把两条已定稿的断言接起来，重复不是它该付出的代价。
    # 第二档保留，是因为「全部候选都重复」时**必须仍然写得出正文**：那是材料自己的写法，
    # 不是替身可以据此少写一句话的理由（少写就是把内容丢掉）。两档都不放宽任何判据。
    preferred = [c for c in connectors if not _connector_would_repeat(c, texts)]
    pools = [preferred] if len(preferred) == len(connectors) else [preferred, list(connectors)]
    for pool in pools:
        if not pool:
            continue
        for offset in range(len(pool)):
            attr_choices = _organizer_attributions(NS) if any(needs_attribution) else ("",)
            for attr in attr_choices:
                head = attr + "，" if (attr and needs_attribution[0]) else ""
                # **每个接缝各选一个**连接语，按接缝序号在可选池里轮转。整句只用一个连接语
                # 会在读者面前变成「…，此外，…，此外，…，此外，…」——中性并列语本来只负责把
                # 两条已定稿的断言接起来，一句话里重复三遍就成了机械串接。每个接缝仍各自排除
                # 「与被接那句自己的开头重复」的连接语（材料正文自己以 `此外，` 开头时）。
                #
                # 接缝里先落分隔标点，再落连接语，最后才落归属语：`…，此外，据公司自身表述，<Claim>`
                # 读起来是「另有一条，且这条出自发行人自述」；把归属语插在连接语**前面**会读成
                # 「…，据公司自身表述此外，<Claim>」，归属语与连接语黏在一起，读者断不开。
                seams: list[str] = []
                for i in range(1, len(texts)):
                    allowed = [c for c in pool if not texts[i].startswith(c)] or list(pool)
                    seams.append(
                        "，" + allowed[(index + offset + i) % len(allowed)]
                        + (attr + "，" if needs_attribution[i] else "") + texts[i])
                body = head + texts[0] + "".join(seams) + "。"
                if not NS.unauthorized_surfaces_within_claims(body, texts):
                    return {"text": body, "claim_ids": [str(c["claim_id"]) for c in chunk]}
    raise AssertionError(
        f"中性并列连接语词表 {list(connectors)} 里没有一个能通过冻结判据："
        "替身不得为此自创连接语（词表与判据本身不一致就是要报的缺陷）")


def _compose_prose_text(atom_texts: Sequence[str]) -> str:
    """一段草稿里的原子 → **一段自然散文**（逐字、连续、不新增任何字面成分）。

    单元文本就是「读者将要读到的那段话本身」（请求 rules 原话），因此它必须**逐字包含**它声明的
    每一条原子——候选是从这段话里长出来的账，不是另写一段。替身的写法因此是：按原子在材料正文里
    的原始顺序逐个落一句，句末补一个句末标点。

    句末标点由组织层给、不由原子自带（候选文本按 `proposals-7` 已去掉末尾句读），因此这里补
    `。` 与「补回原文句读」是同一件事，不新增任何断言成分。
    """
    return "".join(f"{text}。" for text in atom_texts)


def _prose_specs(atoms_by_source: Mapping[tuple[str, str], Sequence[tuple[str, str]]]
                 ) -> list[dict]:
    """逐候选的出处 → `natural_prose_draft` 规格（当前线 `proposals-12` 的第一个顶层键）。

    **哪条轴可用不是替身选的**：`pprov-1` 规定两条出处轴互斥，且本节有材料行时只准材料轴
    （`_parse_one_prose_unit` 对「有材料行却写事实轴」是整批拒绝）。轴因此不在这一层选——
    本函数拿到的键 `("fact"|"material", ref)` **已经是**调用侧按请求**自己的**输入面判定的结果
    （与 `natural_prose_example`：请求里那份按输入面分档的示例，同一条判据）：

      * 出处落在材料行 ⇒ `source_member_refs` 只放那一行；
      * 出处落在权威事实行（财务节的常态）⇒ `source_fact_refs` 只放那一行。

    逐个候选的出处是**候选自己声明的那一行**（路径 A 绑事实行 / 它自己引用所指的材料，路径 B 绑
    材料行），不按文本相似度猜：草稿与候选的对应因此是同一个构造的两个投影，而不是模型事后自报。
    段落顺序 = 出处首次出现的顺序（`dict` 保序），原子顺序 = 候选在该出处里的生成顺序。

    `atom_candidate_keys` 覆盖本段全部原子、并集恰好等于候选集：多一条是伪造映射，少一条会被
    闭合核对（`NS.validate_natural_prose_mapping`）判为「候选旁路塞入」。

    两张表都空 ⇒ 空数组（rules：这一档草稿与候选都留空，把所需材料写进 `follow_up_needs`）。
    """
    specs: list[dict] = []
    for position, ((axis, ref), items) in enumerate(atoms_by_source.items()):
        # 轴名由锚自己说出来（`material` ⇒ 材料行、`fact` ⇒ 事实行），不由这一层二次推断：
        # 哪条轴合法是调用侧按请求输入面判的，这里再判一次就是第二份口径。
        axis_key = "source_member_refs" if axis == "material" else "source_fact_refs"
        other = ("source_fact_refs" if axis_key == "source_member_refs"
                 else "source_member_refs")
        specs.append({
            "prose_key": f"p{position + 1}",
            "text": _compose_prose_text([text for _key, text in items]),
            axis_key: [ref], other: [],
            "atom_candidate_keys": [key for key, _text in items]})
    return specs


def read_request_payload(content: str, *, what: str) -> Any:
    """请求正文 → **领头的那个完整 JSON 值**（写入侧的请求纪律允许其后逐字追加说明）。

    写入侧把「返修说明 / 栏目定向说明 / 批内形状纠正说明」逐字追加在**完整原请求之后**
    （`PW._append_note`，唯一追加点 `PW._messages_for`），因此真实模型看到的是
    「一个完整 JSON 值 + 一个换行 + 一段中文说明」——不是「整段都是 JSON」。替身模拟的是
    **看到这份请求的模型**，它的读法必须与那一份请求的实际形状一致：读领头的完整值，后面的
    说明照读但不解析。冻结的留存重放夹具早就是这么读的（`_payload_of`：「末尾可能带批注，
    故用 `raw_decode`」）。

    容忍**只有**这一种，且两条同时成立才容忍：
      * 领头的那个完整值能**严格**解析出来（不是 `strict=False`、不是截断拼接）；
      * 它后面**确实还有**内容，且那内容以换行开头——这正是 `PW._append_note` 的产出形状：
        追加前保证原请求以换行结尾，说明本身就是紧随其后的那段文本。

    第二条不是修辞：`{"a":1}{"b":2}`（两个 JSON 值首尾相接）同样会让 `json.loads` 抛
    `Extra data`，而它是**损坏的请求**、不是「请求 + 说明」。要求分隔符必须是换行，就把
    「说明」与「第二个 JSON 值」分成两件事，前者才被读成说明。

    领头段本身不合法时抛回**原始**错误，不猜、不修补、不放宽：写入侧对**模型返回**的容错口径
    （`PW.load_json_with_proven_leniency`：只容忍字符串内裸换行/回车/制表符）与这里的**请求面**
    是两件事，不得互相借道——领头的值是本进程自己 `json.dumps` 出来的，任何容错都不该被用到。
    """
    text = str(content)
    try:
        return json.loads(text)
    except ValueError as strict_exc:
        stripped = text.lstrip()
        try:
            payload, end = json.JSONDecoder().raw_decode(stripped)
        except ValueError:
            raise strict_exc
        if stripped[end:end + 1] not in ("\n", "\r"):
            raise strict_exc
        return payload


class OfflineNarrationClient:
    """离线确定性生成器：门前提案束 + 门后自然组织计划。

    单一注入面、单一结果结构（`PW.NarrationResult`）——与真实 client 走**同一**接口，否则
    「测试里过的路径」与「真实跑的路径」就不是同一条。

    门前提案束的构造规则（三节**完全相同**，没有按 section 分支的专用规则）：
      * 路径 A：逐字选择 `authority_facts` 里的事实，候选文本**逐字等于**该事实文本
        （数字因此天然由它自己的权威事实授权）；
      * 路径 B：从 `materials`（本次写作的**精确**材料清单，行里带真实正文读视图）里逐字取
        非高风险描述性切片，支撑边只绑 material 身份、不带任何事实身份；
      * context：每份材料一个草稿单元，上下文边只绑 material，不授权事实；
      * `follow_up_needs`：**数据驱动**的有界补件申请（§六）——见 `_follow_up_plan`。空数组是
        **结论**（本节的精确材料清单已经够用，或同一个诉求已经在本相位的上一轮发出过），不是
        「模型忘了提」。

    `NarrationResult.model` 一律取 `resolve_model_policy(model_policy)`（与既有 stub 的同一约定）：
    门的判据是「声明策略 → 模型一致」，替身没有自己的模型名，报一个别的名字只会让门误判。
    `call_id` 带 `offline-` 前缀，替身身份在产物里始终可辨认。
    """

    def __init__(self, *, max_path_a: int = 3, max_path_b_per_material: int = 2) -> None:
        self.max_path_a = max_path_a
        #: 路径 B 的预算**按材料**给，不是全局给：读一份材料时提出**数个**描述性原子是正常的
        #: 写作行为，而全局上限会把「同一主题的多条原子」直接掐掉，于是组织器永远只有单 Claim
        #: 的组——那不是主链的能力上限，是替身自己造出来的假象（A1 要证的正是多 Claim 自然句）。
        #: 放大的是替身的**提案数**，不是任何门：每条候选仍要逐条过期间/高风险表面/聚合门/
        #: 蕴含门，验收侧不对任何判据放宽。
        self.max_path_b_per_material = max_path_b_per_material
        self.calls: list[dict] = []
        #: 已经发出的补件诉求（`(section_id, topic_id, aspect_id)` → statement）。有界重写只有
        #: 一轮：同一个诉求再发一次就是「同一件事问第二遍」，而相位对第二轮仍发诉求是 typed
        #: 终止的。这里记下来，是为了让「不再问」成为**结论**而不是「忘了问」。
        self._needs_sent: dict[tuple[str, str, str], str] = {}

    # -- 门前：候选 + 草稿单元 ------------------------------------------------
    def _proposals(self, messages, prompt_version, model_policy,
                   call_id: str) -> "PW.NarrationResult":
        from sections import pack_writer as PW
        from sections import narrative_schema as NS

        # 本批请求 = 完整原请求 + 逐字追加的说明（整轮说明 → 本批批内纠正说明）。整轮说明/批内
        # 纠正都是**真实存在**的请求面，因此替身按 `read_request_payload` 读领头的完整值——
        # 用 `json.loads` 读整段会在「这一批被形状纠正后重问」的那一次当场抛 `Extra data`，
        # 而那正是最需要替身如实作答的一次（它纠正的就是本批自己的返回）。
        payload = read_request_payload(messages[0]["content"], what="门前提案请求")
        candidates: list[dict] = []
        units: list[dict] = []
        #: 逐候选记下**它自己长在哪一行上**：`("fact"|"material", ref) → [(候选键, 该候选的逐字文本)]`。
        #: 当前线（`proposals-12`）要求「先写草稿、再为草稿里每个事实原子提交候选」，而草稿单元的
        #: 出处是**两条互斥的轴**（`pprov-1`），且哪条轴可用**由本节的材料面决定、不由模型挑**
        #: （`_parse_one_prose_unit`：本节有材料行时只准材料轴）。替身因此不能事后按文本猜出处，
        #: 而是就地记下每条候选的出处行，草稿按这张表分组——这正是「草稿是候选的来源」那句话的
        #: 确定性版本。`dict` 保序（Python 3.7+），因此草稿段序 = 候选首次出现的出处序。
        atoms_by_source: dict[tuple[str, str], list[tuple[str, str]]] = {}
        # 本节的材料面（请求**自己的**输入面）：有材料行 ⇒ 草稿出处只准材料轴；一张材料行都没有
        # （财务节：`FinancialFactPack` 的事实不经过 Pack 材料）⇒ 只准事实轴。替身按这张面判轴，
        # 不按 section 名分支、也不事后按文本相似度猜。
        materials = list(payload.get("materials") or [])
        material_ref_by_id = {str(m.get("material_id") or ""): str(m.get("ref") or "")
                              for m in materials}
        # `must_use_facts` 是权威侧声明的「本节必须被 claim 或如实留下缺口」的事实集：
        # 上限（`max_path_a`）只约束**额外的**候选，不约束这些事实——否则替身会以自己的
        # 预算裁掉权威的 required 面，那既不是真实模型的行为，也会把「替身偷懒」误判成
        # 「主链有缺陷」。替身过滤它们的判据仍与其它候选完全相同（空文本/含糊期间即不提案）。
        # 必须事实与可选事实在 `pw-12` 之后都按**短别名** `ref` 指认（`support_refs` 声明了
        # 这次请求给了哪些行）。替身因此也按 `ref` 读请求——它模拟的是**看到这份请求**的模型，
        # 而不是「知道内部身份的那一层」；身份展开由写入侧做，替身不掺和。
        must_use = {str(f.get("ref") or "") for f in (payload.get("must_use_facts") or [])}

        for index, fact in enumerate(payload.get("authority_facts") or []):
            if str(fact.get("ref") or "") not in must_use and len(candidates) >= self.max_path_a:
                continue
            text = str(fact.get("text") or "").strip()
            if not text or NS.vague_period_hits(text):
                continue
            # 候选是**原子子句**：权威表面自带的**末尾**句末标点是句读、不是断言内容，替身按
            # `proposals-7` 的「候选文本的写法」把它去掉（照抄内容，不照抄句读）。只去末尾那一个
            # ——口径限定语自成一句时中间那个「。」**不是**句读，它随限定语一起留在候选里
            # （去掉它等于把「代理口径」降级成修饰语，`pw-8` 的硬门会打回）。
            claim_text = text.rstrip(NS.SENTENCE_TERMINATORS).strip() or text
            if not claim_text:
                continue
            candidates.append({
                "candidate_key": f"a{index + 1}", "claim_text": claim_text,
                # 支撑边按 `pw-12` 的请求纪律只写 `ref`（+ 事实性边的 support_role）：
                # authority_kind / container_id / fact_id / material_id / support_semantics /
                # authorization_path 由写入侧从被引用的那一行展开。替身照请求的纪律写。
                "support": [{"ref": str(fact["ref"]), "support_role": "primary"}]})
            # 草稿的出处：这一段从**这一条权威事实行**长出来。草稿文本取该事实的**逐字**原句
            # （候选文本是它去掉末尾句读的那一段，因此逐字包含成立）。
            #
            # 落在哪条轴上由**本节的材料面**决定，不由替身挑：本节有材料行时只准材料轴，而路径 A
            # 的 material 锚点由**该事实自己的引用**派生（`_factual_proposal`：`material_id` 取自
            # 权威条目，不由模型选）——因此这里落在请求行自己带的那份材料上。一条事实读不出材料
            # 锚点、而本节又声明了材料行时，草稿无法把它声明在任何一条轴上（有材料行时事实轴整批
            # 被拒）：这时**照常提候选、不悄悄丢掉**，让下游闭合核对如实报出「这条候选没有被草稿
            # 覆盖」——替身不替链决定一条声明不出来的候选该不该存在。
            if materials:
                anchor = material_ref_by_id.get(str(fact.get("material_id") or ""))
                if anchor:
                    atoms_by_source.setdefault(
                        ("material", anchor), []).append((f"a{index + 1}", text))
                else:
                    atoms_by_source.setdefault(
                        ("fact", str(fact["ref"])), []).append((f"a{index + 1}", text))
            else:
                atoms_by_source.setdefault(
                    ("fact", str(fact["ref"])), []).append((f"a{index + 1}", text))

        b_index = 0
        # 跨材料去重：同一句正文可能同时出现在两份材料里（同一页被两个 Pack/两个 span 覆盖，
        # 或年报逐节照抄上一年的同类章节）。「同一句原子提两次」不是第二条断言，替身自己也
        # 必须只提一次——这是替身的提案纪律，不是对门的放宽。
        #
        # **先见先得**在这里是有方向的：按 `_materials_in_role_order` 遍历，「较新且可核实」
        # 的那一份先占住这句话。否则较旧的那份先提、随后被「同类较旧材料不得写成当前状态」
        # 这条门裁掉时，较新的那一份已经因为「同文已提过」而没有机会——那是内容的整条缺席，
        # 不是门的裁决。
        proposed_texts: set[str] = set()
        slices_by_topic: dict[str, int] = {}
        for member in _materials_in_role_order(materials):
            reading = str(member.get("text") or "")
            topic_id = str(member.get("topic_id") or "")
            for slice_text in _descriptive_slices(reading, self.max_path_b_per_material):
                if slice_text in proposed_texts:
                    continue
                proposed_texts.add(slice_text)
                slices_by_topic[topic_id] = slices_by_topic.get(topic_id, 0) + 1
                b_index += 1
                candidates.append({
                    "candidate_key": f"b{b_index}", "claim_text": slice_text,
                    "support": [{"ref": str(member["ref"]), "support_role": "primary"}]})
                atoms_by_source.setdefault(
                    ("material", str(member["ref"])), []).append(
                        (f"b{b_index}", slice_text))
            head = _descriptive_slices(reading, 1)
            if head:
                units.append({
                    "unit_key": f"u-{member['material_id']}", "unit_kind": "paragraph",
                    "text": head[0],
                    # context 边在 `pw-12` 之后一个身份字段都不写：只要 `ref`。
                    "context_support": [{"ref": str(member["ref"])}]})

        plan = {"natural_prose_draft": _prose_specs(atoms_by_source),
                "claim_candidates": candidates, "narrative_draft_units": units,
                "follow_up_needs": self._follow_up_plan(payload, slices_by_topic)}
        return PW.NarrationResult(
            text=json.dumps(plan, ensure_ascii=False), call_id=call_id,
            model=self._reported_model(model_policy), prompt_version=prompt_version,
            status="ok", input_tokens=0, output_tokens=0, latency_ms=0, finish_reason="stop")

    #: 一个主题至少要有这么多条合法描述性原子，才够**自然组织**成一句（§六/A1.2 的下界）。
    MIN_DESCRIPTIVE_ATOMS_PER_TOPIC = 2

    def _follow_up_plan(self, payload: dict, slices_by_topic: dict) -> list[dict]:
        """有界补件申请（§六）：只在**本节的精确材料清单撑不起同一主题的多条描述性原子**时提。

        规则数据驱动、不按 section 分支：

          * 逐 topic 数「本次能从真实材料里授权的描述性原子」（与路径 B 候选同一个选材函数）；
          * 全都在下界以上 ⇒ 空数组。空数组是**结论**（本节权威输入已经够用）；
          * 否则取最弱的那些 topic —— 排序键 `(可用原子数, -材料份数, topic id)`：先看「写得出
            几条」，材料数相同才看别处。**优先在有材料的主题里补**：一份材料都没有的主题，缺的
            不是「代表性材料」而是「材料本身」，对它提「补充材料」在检索侧多半是空转（§六 的
            路线 1/2 说的正是「用已存在于 OutlineSpan 的客户材料补进正文」）；这样的主题只有在
            **没有**任何可读主题时才作为兜底被选中，那同样是如实表达诉求，只是它多半补不回来。
            同一档内用 topic id 保证确定性（不固定取第一个 topic）；
          * **一轮里为每个可读弱主题各提一条**，不再只挑一个：正式相位本来就把需求按 target
            requirement 分组、逐条独立裁决（`_follow_up_authority`），只问一个 topic 就下「语料里
            没有别的可用内容」的判断，是对**诉求范围**下结论而不是对语料下结论。条数等于本节
            真正弱掉的可读主题数，不放大任何单条预算（每条仍是同样的 `tree_inspect:1`）；
          * 同一个 `(section, topic, aspect)` 已经在上一轮申请过就不再申请——有界重写只有一轮，
            同一件事问第二遍会被相位判成无界返修（typed 终止）。

        替身**只表达诉求**：材料从哪来、来什么、够不够，全部由正式 Harness 的 FollowUp 执行
        与新的 Pack successor 决定。这里不预选材料、不写字符串进 Pack。
        """
        from sections import narrative_schema as NS

        section_id = str((payload.get("task") or {}).get("section_id") or "")
        rows = [r for r in (payload.get("requestable_aspects") or []) if r]
        if not rows:
            return []
        materials_by_topic: dict[str, int] = {}
        for member in payload.get("materials") or []:
            topic_id = str(member.get("topic_id") or "")
            if topic_id:
                materials_by_topic[topic_id] = materials_by_topic.get(topic_id, 0) + 1
        per_topic: dict[str, int] = {}
        for row in rows:
            topic_id = str(row.get("topic_id") or "")
            if topic_id:
                per_topic.setdefault(topic_id, slices_by_topic.get(topic_id, 0))
        weak = [(count, materials_by_topic.get(topic_id, 0), topic_id)
                for topic_id, count in per_topic.items()
                if count < self.MIN_DESCRIPTIVE_ATOMS_PER_TOPIC]
        if not weak:
            return []
        # 有材料可读的弱主题优先（见 docstring：没有材料的主题补不出「代表性材料」）；
        # 兜底是「一条可读主题都没有」，此时仍如实提出诉求。排序键里材料数取**降序**：
        # 材料越多的弱主题，越可能是本节真正要写的那一块，越值得一次有界的补取。
        readable = [row for row in weak if row[1] > 0]
        ordered = sorted(readable or weak, key=lambda row: (row[0], -row[1], row[2]))
        if not readable:
            # 兜底只提一条：没有材料的主题之间没有「谁更值得补」的差别，提多条只是把空转放大。
            ordered = ordered[:1]
        needs: list[dict] = []
        for _, _, topic_id in ordered:
            in_topic = sorted((r for r in rows if str(r.get("topic_id") or "") == topic_id),
                              key=lambda r: str(r.get("aspect_id") or ""))
            degraded = [r for r in in_topic
                        if str(r.get("status") or "") in NS.NON_COVERED_ASPECT_STATUSES]
            row = (degraded or in_topic)[0]
            aspect_id = str(row.get("aspect_id") or "")
            key = (section_id, topic_id, aspect_id)
            if key in self._needs_sent:
                continue
            statement = (f"需要补充公司主题 {topic_id} 下「{aspect_id}」的原始材料："
                         f"本节的精确材料清单在该主题下不足以给出多条非高风险描述性断言。")
            self._needs_sent[key] = statement
            needs.append({"statement": statement,
                          "target_requirement_id": str(row.get("target_requirement_id") or ""),
                          "topic_id": topic_id, "question_id": str(row.get("question_id") or ""),
                          "aspect_id": aspect_id, "requiredness": "required",
                          "expected_source_class": "company_industry",
                          # 非空是**执行前提**：检索侧对空字段诉求 fail-closed。提示名沿用树检视能力。
                          "budget_hint": "tree_inspect:1"})
        return needs

    def _reported_model(self, model_policy: str) -> str:
        """替身报出的模型名 = **调用它的那一次的策略**解析出的模型名（不自己发明一个）。"""
        from sections import pack_writer as PW

        return PW.resolve_model_policy(model_policy)

    # -- 门后：自然组织 ------------------------------------------------------
    def _organizer(self, messages, prompt_version, call_id: str) -> "PW.NarrationResult":
        """按输入 Claim **逐字**组织：先按 `theme_key` 归段，段内用**冻结词表**里的衔接语串句。

        七条都由冻结判据或输入面本身决定，不是替身自创的文风：

        1. 每条 Claim 文本**原样、完整、按声明顺序**出现在它声明的句子里（§七 1）；
        2. Claim 文本**之间**插入的衔接语只取自 `NS.NEUTRAL_CONNECTORS`（`此外` / `同时`，
           冻结的**封闭**中性并列词表），且只从其中选那个**串起来之后**仍不被冻结判据判为
           高风险表面的连接语——替身没有立场自己发明连接语，也不得把 `；` 当成「组织」（那
           正是被拒的机械拼接）。判据用的是组合句那一套
           （`NS.unauthorized_surfaces_within_claims`，边界感知）：只测连接语本身不够，
           真正要过门的是「Claim + 衔接语」拼出来的整句。选择时先避开「被接的那句话自己就以
           同一个词开头」的那些（`_connector_would_repeat`）：材料正文常自己写着 `此外，`，
           再接一个就读成 `此外，此外，`。避不开时**照常写出正文**（退回原样轮转），因为
           那是材料自己的写法，不是替身可以据此少写一句的理由；
        3. **接缝必须先落分隔标点**（`nrules-12` 判据 e）：衔接语写成 `"，" + connector`。
           `NS.NEUTRAL_CONNECTORS` 的每一条都是**句首**连接语（自带尾部逗号），直接 `join`
           出来就是「…研发、生产、销售此外，公司…」这种病句——它既不是机械拼接也不是双句
           相接，a/b/c 三条判据都拦不住，只有 e 条按**接缝的第一个字符**逐条检查。替身不得
           为此自己写组织语：`"，"` 是标点，不是事实面，也不改变任何断言的强度；
        4. 一句最多 `NS.MAX_COMPOSED_CLAIMS_PER_SENTENCE` 条 Claim，超过就**多写一句**；
        5. **一句就是一个句子**：句末标点至多出现在末尾。**自带句末标点**的 Claim 本身就是一句，
           只能**单独成句**——因此同段的 Claim 先按「是否自带句末标点」切段：不带标点的
           连续 Claim 用衔接语串成一句（句末补一个 `。`），带标点的那条自己占一句。替身不得把
           中间那个句末标点吞掉（那会让 Claim 文本不再逐字在场），也不得把两条完整句子首尾
           相接（那就是被拒的双句容器）；
        5b. **自带句首连接语的 Claim 只起头、不被接**（`_claim_opens_with_neutral_connector`）：
           Claim 文本自己以 `此外，` / `同时，` 开头时，它已经带了一句的句首组织成分；再接在
           别的 Claim 后面，接缝里就会连着出现两个连接语（「…，同时，此外，公司…」）。两个
           连接语各自合规，连在一起却是机械重复。因此这样的 Claim 一律另起一句——文本一个字
           不改（`norg-5` 的「只加组织、不改断言」照旧），改的只是分句；
        6. **按业务主题归段**：分段键取输入给的 `theme_key`（系统从材料标题路径确定性派生），
           段序按 `theme_order`，段内按输入顺序。同一主题超过
           `_ORGANIZER_MAX_SENTENCES_PER_PARAGRAPH` 句就**连续**分下一段（prompt 允许「条数多时
           可以连续分成几段」）。这条纪律针对的正是「讲着产品突然跳到会计政策」的段落：会计
           政策与身份事实有自己的 `theme_key`，不会落进经营模式段；
        7. **同段内逐字包含的另一条 Claim 记 `omitted / redundant_with_selected_claim`**
           （判据见 `_organizer_drop_contained`）。这是替身唯一有资格下的「这两条是同一件事」
           结论：更短那条的每一个字都已在更长那条里原样出现，删掉不丢任何字面。措辞不同
           （差一个「或」「的」）的近义重复**不删**——判定「说的是不是同一件事」是组织器的
           职责，替身代它下这个结论就等于替它写正文；这类疑似重复如实列出留给模型裁决。
           输入面的 `redundancy_candidates`（`norg-5`）替身**照收不判**：它只把线索原样呈现给
           真正的组织器，自己不对清单里任何一对下结论（与 `norg-4` 起对近义对的处理同一立场）；
        8. **关系性连接语一律不用**（`nrules-13` 判据 10）：替身只看得到已定稿的 Claim 文本，
           没有「材料有没有说过这个对照 / 递进 / 从属 / 归纳关系」的信息，因此它唯一站得住的
           写法就是中性并列——这条纪律落在词表上（第 2 条），判据本身由门执行；
        9. **发行人自述必须带归属语**（`nrules-13` 判据 11）：Claim 文本含
           `NS.issuer_self_description_hits` 命中词时，替身在该条 Claim **紧前的组织段**写一个
           `NS.ATTRIBUTION_MARKERS` 里的归属语（句首段或接缝里，见 `_organizer_sentence`）。
           归属语不引入数字、期间、主体或结论，因此不触犯「不得新增事实表面」。
           接缝里的次序是「分隔标点 → 连接语 → 归属语」，读成 `…，此外，据公司自身表述，<Claim>`
           （归属语与连接语黏在一起读者断不开，因此归属语**不**放在连接语前面）。
        10. **组织语不改断言的时点**（`nrules-14` 判据 12）：替身写出的组织成分只有中性并列
            连接语与归属语两类（见上两条），两者都不含 `NS.CURRENT_STATE_FRAMING_MARKERS` 里的
            当前式措辞，因此本条对替身是**结构性成立**的，不需要额外分支。这不是「替身豁免」：
            判据仍由门逐句执行，替身只是恰好不可能违反它——把它写在这里，是为了让后来者改
            连接语/归属语词表时知道：一旦引入 `目前` / `仍` / `持续` 这类词，这条纪律立刻失效。
        """
        from sections import narrative_schema as NS
        from sections import pack_writer as PW

        payload = json.loads(messages[-1]["content"])
        limit = NS.MAX_COMPOSED_CLAIMS_PER_SENTENCE
        kept_groups: list[list[dict]] = []
        redundant_ids: set[str] = set()
        for group in _organizer_theme_groups(payload):
            kept, redundant = _organizer_drop_contained(group)
            kept_groups.append(kept)
            redundant_ids.update(str(c["claim_id"]) for c in redundant)
        paragraphs: list[dict] = []
        for kept in kept_groups:
            sentences = [_organizer_sentence(NS, chunk, index)
                         for index, chunk in enumerate(_organizer_chunks(kept, limit))]
            for start in range(0, len(sentences), _ORGANIZER_MAX_SENTENCES_PER_PARAGRAPH):
                paragraphs.append({"sentences":
                                   sentences[start:start + _ORGANIZER_MAX_SENTENCES_PER_PARAGRAPH]})
        plan = {
            "paragraphs": paragraphs,
            "claim_dispositions": [
                {"claim_id": str(c["claim_id"]),
                 "disposition": "omitted" if str(c["claim_id"]) in redundant_ids else "selected",
                 "reason_code": (_ORGANIZER_REDUNDANT_REASON
                                 if str(c["claim_id"]) in redundant_ids else None)}
                for c in payload["claims"]],
        }
        return PW.NarrationResult(
            text=json.dumps(plan, ensure_ascii=False), call_id=call_id,
            # 组织器槽位（`NO.NARRATIVE_ORGANIZER_MODEL_POLICY = "narrator"`）是**槽位名**而不是
            # 可解析的 policy（`resolve_model_policy` 明确拒绝它），因此这里报 stub 记号，
            # 与既有组织器 stub 同一约定。
            model=PW.MODEL_POLICY_STUB, prompt_version=prompt_version,
            status="ok", input_tokens=0, output_tokens=0, latency_ms=0, finish_reason="stop")

    def narrate(self, *, messages, system, prompt_version, model_policy):
        from llm import budget as LB
        from sections import narrative_organizer as NO
        from sections import pack_writer as PW

        self.calls.append({"prompt_version": prompt_version, "model_policy": model_policy})
        call_id = f"offline-{len(self.calls)}"
        # 替身的每一次「请求」也走**同一套**事前记账入口（`llm.budget`）：
        #   * 离线不耗费真实调用，因此门的安装形式是 `enforce=False`（只记账、不拦截）；
        #   * 但**归属**与**逐次落账**与真实链完全一致——否则「离线完整链的调用记录可逐项守恒」
        #     就只是替身自己的一本私账，无法用来解释真实链的计数；
        #   * 未安装账本时 `reserve_attempt` 返回 None，既有行为不变。
        ticket = LB.reserve_attempt(call_id=call_id, prompt_version=prompt_version,
                                    model=None, max_tokens=None)
        try:
            if prompt_version == NO.NARRATIVE_ORGANIZER_PROMPT_VERSION:
                result = self._organizer(messages, prompt_version, call_id)
            elif prompt_version != PW.NARRATION_PROMPT_VERSION:
                raise AssertionError(
                    f"离线生成器不认识 prompt 版本 {prompt_version!r}"
                    "（不得对未知输入面静默编一份输出）")
            else:
                result = self._proposals(messages, prompt_version, model_policy, call_id)
        except BaseException as exc:  # noqa: BLE001 — 失败的尝试照样占一次
            LB.settle_attempt(ticket, status=LB.STATUS_ERROR,
                              error=f"{type(exc).__name__}: {exc}")
            raise
        LB.settle_attempt(ticket, status=LB.STATUS_OK)
        return result


class OfflineEntailmentClient:
    """语义门（P9）的确定性替身：只回「已蕴含」，不联网、不真实调用。

    离线模式下它证明的是「这条链在语义门在场时可贯通」；**它不构成任何语义判断**。
    与 `OfflineNarrationClient` 同一约定：报出的模型名取调用它的策略解析值。
    """

    ENTAILED = '{"verdict": "entailed", "reason_code": null, "rationale": "权威/材料逐字给出"}'

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def evaluate(self, *, messages, system, prompt_version, model_policy, **extra):
        from llm import budget as LB
        from sections import pack_writer as PW

        self.calls.append({"prompt_version": prompt_version, "model_policy": model_policy})
        call_id = f"offline-ent-{len(self.calls)}"
        # 与 `OfflineNarrationClient.narrate` 同一约定：事前记账、失败也占一次。
        ticket = LB.reserve_attempt(call_id=call_id, prompt_version=prompt_version,
                                    model=None, max_tokens=None)
        LB.settle_attempt(ticket, status=LB.STATUS_OK)
        return PW.NarrationResult(
            text=self.ENTAILED, call_id=call_id,
            model=PW.resolve_model_policy(model_policy), prompt_version=prompt_version,
            status="ok", input_tokens=0, output_tokens=0, latency_ms=0, finish_reason="stop")


class OfflineFinalSentenceClient:
    """最终句语义门 B（`nsfid-1`）的确定性替身。

    **它证明什么、不证明什么（与 `OfflineEntailmentClient` 同一条纪律）**：它证明「这条链在 B
    门**在场**时可贯通」——B 门会把请求面里机械定位出的**每一条**原子逐条认领，聚合结论由逐原子
    结果推出，因此「漏审句子 / 漏认领原子 / 自相矛盾的聚合」这几类结构性失败在离线重放里**照样
    会红**。它**不**构成任何语义判断：它不看正文，也不会发现连接语新增了因果、期间被换或代理口径
    被丢——那些只有真实判定面才答得出来。因此离线重放全绿**不等于**「语义门已覆盖」；真实 run
    的授权文本必须逐字写明这一条。

    它**读请求**（与真实门发的是同一份 `build_sentence_fidelity_messages` 输出），因此它的认领面
    不可能比真实门看到的宽：`nsfr-2` 起 `located_atoms` 是**逐句的预填槽位表**（键 = 每一句的
    `sentence_id`，判定字段一律留空），替身按这张表**逐行**认领——它认领的是这一句自己的槽位，
    不跨句借表面，也不自行发明原子、Claim 或支撑边。归属规则是确定性的，且**只**允许落在该句
    自己声明过、且有 factual 支撑边的 Claim 上；找不到这样一条 Claim 时它**如实**给出
    `not_entailed` + `support_binding_missing`（而不是把一条无支撑的断言写成「已蕴含」）。
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    # -- 内部 --------------------------------------------------------------

    @staticmethod
    def _request(messages) -> dict:
        """取本门唯一输入面的 JSON 体（不是这份形状就当场抛，不猜）。"""
        if not messages:
            raise AssertionError("最终句核验请求为空：替身不得对空输入编一份输出")
        body = messages[-1].get("content") if isinstance(messages[-1], dict) else None
        try:
            payload = json.loads(str(body))
        except (TypeError, ValueError) as exc:
            raise AssertionError(f"最终句核验请求不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise AssertionError("最终句核验请求必须是 JSON 对象")
        for key in ("sentences", "claims", "accepted_bindings", "located_atoms"):
            if key not in payload:
                raise AssertionError(f"最终句核验请求缺字段 {key!r}（不得对未知输入面静默作答）")
        return payload

    @classmethod
    def _payload(cls, messages) -> str:
        from sections import narrative_schema as NS

        request = cls._request(messages)
        factual = {str(row.get("accepted_support_binding_id"))
                   for row in request["accepted_bindings"]
                   if str(row.get("semantics") or "") == "factual"}
        declared = {str(row.get("claim_id")): [str(b) for b in (row.get("accepted_binding_ids")
                                                               or ())]
                    for row in request["claims"]}
        # `nsfr-2`：定位读数按**句**分组进输入面（`sentence_id -> [待答槽位]`）。替身按这张
        # 逐句槽位表逐行认领——它认领的是**这一句**自己的槽位，不跨句借表面。
        located = request["located_atoms"]
        if not isinstance(located, dict) or set(located) != {
                str(s.get("sentence_id")) for s in request["sentences"]}:
            raise AssertionError(
                "最终句核验请求的 `located_atoms` 必须是逐句槽位表（键 = 每一句的 sentence_id）")
        by_sentence: dict[str, list[dict]] = {
            str(sid): list(rows or ()) for sid, rows in located.items()}
        per_sentence: list[dict] = []
        rejected = False
        for sentence in request["sentences"]:
            sid = str(sentence.get("sentence_id"))
            own = [str(c) for c in (sentence.get("claim_ids") or ())]
            rows: list[dict] = []
            for atom in by_sentence.get(sid, ()):
                kind = str(atom.get("atom_kind"))
                surface = str(atom.get("atom_surface"))
                if kind not in NS.FINAL_SENTENCE_ATOM_KINDS:
                    raise AssertionError(f"定位读数给出未登记的原子类别 {kind!r}")
                carriers = [cid for cid in own
                            if [b for b in declared.get(cid, ()) if b in factual]]
                chosen = sorted(carriers)[0] if carriers else None
                if chosen is None:
                    # 句子里确实有这条表面，但**没有任何**该句声明过、且有 factual 支撑边的 Claim
                    # 承载它：如实记成未通过，而不是替它编一条授权。原子行声明的 Claim 只能落在
                    # **该句自己声明过**的 Claim 上（`nsfid-1` 的逐原子引用面判据），因此取该句
                    # 最小 id（`nsfr-2` 起槽位面不再给任何 Claim 提示：提示按**全束** Claim 算，
                    # 可能属于另一句，照抄它会让「未通过」被读成「过期」）。
                    if not own:
                        raise AssertionError(
                            f"承载事实的最终句 {sid!r} 没有声明任何 Claim：请求面自相矛盾")
                    rejected = True
                    rows.append({
                        "atom_kind": kind, "atom_surface": surface,
                        "claim_id": sorted(own)[0],
                        "accepted_binding_ids": [],
                        "verdict": "not_entailed",
                        "reason_code": "support_binding_missing",
                        "rationale": "离线替身：该句没有一条带 factual 支撑边的已接受 Claim 承载此原子",
                    })
                    continue
                rows.append({
                    "atom_kind": kind, "atom_surface": surface, "claim_id": chosen,
                    "accepted_binding_ids": [b for b in declared[chosen] if b in factual],
                    "verdict": "entailed", "reason_code": None,
                    "rationale": "离线替身：逐字认领请求面里已定位的原子（不构成语义判断）",
                })
            per_sentence.append({"sentence_id": sid, "atoms": rows})
        return json.dumps({
            "per_sentence": per_sentence,
            "verdict": ("rejected" if rejected else "entailed"),
            "reason_code": ("atom_not_entailed" if rejected else None),
        }, ensure_ascii=False)

    # -- 门面 --------------------------------------------------------------

    def evaluate(self, *, messages, system, prompt_version, model_policy, **extra):
        from llm import budget as LB
        from sections import pack_writer as PW

        self.calls.append({"prompt_version": prompt_version, "model_policy": model_policy})
        call_id = f"offline-fsf-{len(self.calls)}"
        # 与 `OfflineEntailmentClient` 同一约定：事前记账、失败也占一次。
        ticket = LB.reserve_attempt(call_id=call_id, prompt_version=prompt_version,
                                    model=None, max_tokens=None)
        try:
            text = self._payload(messages)
        except BaseException as exc:  # noqa: BLE001 — 失败的尝试照样占一次
            LB.settle_attempt(ticket, status=LB.STATUS_ERROR,
                              error=f"{type(exc).__name__}: {exc}")
            raise
        LB.settle_attempt(ticket, status=LB.STATUS_OK)
        return PW.NarrationResult(
            text=text, call_id=call_id,
            model=PW.resolve_model_policy(model_policy), prompt_version=prompt_version,
            status="ok", input_tokens=0, output_tokens=0, latency_ms=0, finish_reason="stop")


# ---------------------------------------------------------------------------
# 真实输入（只读）
# ---------------------------------------------------------------------------

def _information_need_builder() -> object:
    """§三 3（acc-30）：本轮注入的 `information_need_builder`（**单一来源**，注入点与报告读同一个）。

    **生产实现** `harness.aspect_need_builder.FrozenAspectInformationNeedBuilder`（`anb-1`）。
    `acc-29` 及之前注入的是纯结构件 `evals.test_demo_topic_runtime._NeedBuilder`：它把
    `required_evidence_types` / `required_source_types` 都留空，于是
    `routing.router.requires_external_source()`（外部路由的**唯一**判据）在任何 aspect 上都为假，
    冻结 Contract 的 `external` 来源类**从未离开过 runner**。生产实现改用权威派生器
    `TS.derive_support_eligibility(...).required_source_classes`（与
    `external_retrieval.requirement_side` 的读法同一个函数）把那条声明搬进正式 need。

    这**不是**放开外部检索：本批仍 `EXTERNAL_RESEARCH_ENABLED=False`（无联网授权），且
    `harness.policies.external_research_unauthorized` 会在执行前拒掉三个外部动作——need 带出
    要求与"本轮真的发了外部请求"是两条各自可读的事实。诚实记录要能**指名**构造器，因此不
    在这里另抄一个类名字面量。
    """
    from harness.aspect_need_builder import FrozenAspectInformationNeedBuilder
    return FrozenAspectInformationNeedBuilder()


@dataclasses.dataclass(frozen=True)
class RealInputs:
    """一次验收所需**全部**真实输入（全部只读；不含任何样例专用分支）。"""

    company_id: str
    document_id: str
    document_version: str
    raw_pdf_sha256: str
    evidence_set_version: str
    #: 报告生成日：由本轮**唯一时钟瞬间**按显式配置时区换算（O-11），不再取自财务快照期末。
    report_as_of: str
    #: 同一瞬间的 UTC 时间戳（轨迹/运行身份时间，不进入内容身份）。
    generated_at: str
    report_timezone: str
    #: 本轮全部当前、政策允许的上传材料的来源清单（登记 ≠ 选用，两者都在里面）。
    source_manifest: object
    dims: dict
    contract: object
    projection: object
    scope_fingerprint: str
    tasks: dict
    requirements: dict
    writing_spec: object
    presentation_profile: object
    resolver: object
    pack_store: object
    authorities: dict
    financial_phase: object
    pack_sets: dict
    #: 逐 section 的 `TR.TopicRuntimeResult`（按 `task.topic_ids` 顺序），**只读诊断**用。
    #:
    #: 为什么必须单独留一份：runtime 的**逐条 typed 栏目未达原因**（`UNMET_COLUMN_REASONS`：
    #: 预算阻止派发 / 已派发但未命中 / 命中但不支持栏目 / 表未获资格 / 审计不完整）只挂在
    #: `TopicRuntimeResult.gaps` 上，**不进 Pack 本体**（Pack 只落 `ResearchGap` 与
    #: `ContractGap` 那几类 typed 对象）。只留 `pack_set` 的话，人读页就只能从 Pack 反推，
    #: 而反推不出「这一栏本轮根本没查」与「查了没命中」的区别——那正是本批要修的读法。
    #: 因此这里按**运行现场**留一份，读回时逐 aspect 取用；它不参与任何判据。
    topic_results: dict = dataclasses.field(default_factory=dict)
    #: §九：写作相位的 `dependencies_of` —— 每条 `FollowUpNeed` 按**它自己的 target topic** 找到
    #: 对应 requirement 的运行时依赖。它必须是真实组合根（`subject`/`relation` 切面相等），否则
    #: 有界重写要么跑不动，要么在用首 topic 的依赖冒充第二个 topic 的。
    dependencies_of: object = None
    #: §九：每个 section **自己的** `TopicRunContext`（运行身份逐节独立：同一次运行里三节不得
    #: 共用一条 run_id，否则第二条 run 会把第一节的裁决记录当成同一次）。
    run_contexts: dict = dataclasses.field(default_factory=dict)
    #: A1 源头对账（**只读诊断**）：本次运行研究侧注入的 LLM。真实模式是 `RT.RealResearchLLM`，
    #: 离线模式是 `OfflineResearchLLM`。材料召回/保留/事实资格的对账**不再**读它——那三层现在
    #: 从真实轨迹事件（`TREE_NAVIGATION` / `TREE_TOOL_RESULT` / `EVIDENCE_FALLBACK_RECUT`）与
    #: Pack 自己的 gap 记录里回读；留着对象只为让报告能如实写明本次研究侧用的是哪一种 LLM。
    research_llm: object = None
    #: A1 源头对账（**只读诊断**）：本次运行的 trace 事件容器。对账只按**类型**计数（读取轨迹
    #: 那一层），不把 payload 抄进报告。
    trace_sink: object = None
    #: §三 3（acc-28）：本轮是否启用外部检索漏斗。这是**现场值**——`RouteContext` 与
    #: `external_retrieval` 块读同一个来源（`EXTERNAL_RESEARCH_ENABLED`），报告不另抄一个字面量。
    external_research_enabled: bool = False
    #: §三 3（acc-28）：本轮注入的 `information_need_builder` **实例**。报告按它的实际类型
    #: 指名（`_information_need_builder` 是唯一注入点），因此「need 侧带不出 `external`」这句话
    #: 是对**现场这个对象**说的，不是对一段描述说的。
    information_need_builder: object = None


# ---------------------------------------------------------------------------
# 离线模式的**研究侧** LLM 替身：只替换 LLM 这一层，其余全是正式链路
# ---------------------------------------------------------------------------

#: 离线替身给每条已检视证据至多提一条 claim（确定性上界，不是"提到为止"）。
_OFFLINE_RESEARCH_MAX_CLAIMS = 6
#: 一句可引用的正文至少要有这么多字符：更短的一行多是表头/页码，不构成可引用描述。
_OFFLINE_RESEARCH_MIN_EXCERPT_CHARS = 24
#: 离线替身报出的模型名（与叙述替身同一约定：替身报 stub 记号，不冒充真实模型）。
_OFFLINE_RESEARCH_MODEL = "offline-research-stub"
#: 固定 usage：替身不打真实请求，用量必须**确定**（否则 Pack 内容身份会随环境漂移）。
_OFFLINE_RESEARCH_INPUT_TOKENS = 120
_OFFLINE_RESEARCH_OUTPUT_TOKENS = 40

_EVIDENCE_ID_LIST_RE = re.compile(r"本地证据 evidence_id[^:：]*[:：]\s*(.+)")
_EVIDENCE_BLOCK_RE = re.compile(r"^### evidence_id=(\S+)", re.M)
#: 引用块由 `harness/entailment.py::entailment_prompt_vars` 渲染成**项目符号行**
#: `- [i] evidence_id=…`（与 claims 段的 `- c1 …` 同一形状，见 `harness/entailment.py:973`
#: 与替身自身的 `_claim_lines`）。这里的 `^-\s*` 必须与那个渲染逐字对齐：早先写作 `^\[…\]`
#: 时**恒不匹配**（行首是 `- `），`cited_id` 恒为空 dict ⇒ `body_by_id.get("")` 恒为 `""`
#: ⇒ 计数包含恒为假 ⇒ **每条 claim 都被判 `UNSUPPORTED`** ⇒ `_adopt_facts` 在蕴含层
#: （`harness/topic_runtime.py:2259`）拒掉全部提案 ⇒ 离线纵链**一条候选都造不出来**
#: （Pack 事实恒 0、Writer `authority_facts` 恒 0）。读数见
#: `_m930_3_probe/M930_3_QREWORK_PART1_NUMERIC_CHANNEL_PLAN.md` §1.7–§1.8；
#: 该耦合由 `evals/test_m930_3_numeric_channel.py` §1b 用**真渲染器**钉住。
_CITATION_LINE_RE = re.compile(r"^-\s*\[(\d+)\]\s*evidence_id=(\S+)", re.M)


class OfflineResearchLLM:
    """离线模式的研究 LLM 替身。**只有 LLM 这一层是替身**，链路本身是正式的。

    它存在的理由是「离线跑一轮完整链做回归」，不是「让验收更容易过」。因此：

      * 它接进的是**正式** `harness.runtime.run_question` 循环：真 Router（`routing.router.route`）、
        真 `ToolRegistry`、真 `_adopt_facts` 预验证事实资格门。它**不**把整段原文复制成
        "已验证事实"——那条旁路（旧 `_SpanRecallResearch`）已被删除；
      * 它**抽取式**作答：每条 claim 的文本都是**当次 prompt 里真实出现过的证据正文的逐字子串**，
        不写固定答案、不概括、不做换算；
      * 它的蕴含判定是**逐字包含检查**而不是自报 `SUPPORTED`：claim 文本必须逐字出现在它自己
        声明的引用所指向的那段证据里，否则判 `UNSUPPORTED`。这比「按构造为真」严格，但它仍然
        只是离线替身——真实的事实资格只能由真实模式的 `RT.RealResearchLLM` 走出；
      * 它是**确定性**的：同样的材料给出同样的输出与同样的 usage。
      * 动作选择读 `allowed_actions`（由真实路由派生），不自己发明当前路由不许可的动作。

    **它不参与任何内容验收判定**：`MODE_OFFLINE` 的产物只在离线回归里有意义。
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    # -- 内部 --------------------------------------------------------------
    def _respond(self, text: str, *, kind: str, prompt_version: str, **extra) -> "object":
        from llm import budget as LB
        from llm import client as LLC

        index = len(self.calls) + 1
        self.calls.append({"kind": kind, "index": index,
                           "prompt_version": prompt_version, **extra})
        # **与离线写作替身同一约定**：替身的每次「请求」也走同一套事前记账入口
        # （`llm.budget.reserve_attempt`）。这不是形式主义——研究侧调用此后受**独立轴**
        # （`AXIS_RESEARCH`，按 topic 计量）约束，如果替身自己记一本私账，离线就跑不出
        # 「两条轴各自的账可逐项守恒」，也无法在离线阶段暴露「研究调用落在错误作用域」：
        # 归属与作用域判定发生在 `enforce` **之前**，因此离线同样会拒。
        # 未安装账本时 `reserve_attempt` 返回 None，既有行为不变。
        ticket = LB.reserve_attempt(call_id=f"offline-research-{index}",
                                    prompt_version=prompt_version,
                                    model=None, max_tokens=None)
        LB.settle_attempt(ticket, status=LB.STATUS_OK)
        return LLC.LLMResponse(
            text=text, input_tokens=_OFFLINE_RESEARCH_INPUT_TOKENS,
            output_tokens=_OFFLINE_RESEARCH_OUTPUT_TOKENS, latency_ms=1,
            model=_OFFLINE_RESEARCH_MODEL, call_id=f"offline-research-{index}",
            finish_reason="stop")

    # -- LLM 注入协议 -------------------------------------------------------
    def select_action(self, prompt_vars: dict) -> "object":
        from harness import runtime as RT

        allowed = {line[2:].strip() for line in
                   str(prompt_vars.get("allowed_actions") or "").splitlines()
                   if line.startswith("- ")}
        summary = str(prompt_vars.get("evidence_summary") or "")
        inspected = str(prompt_vars.get("already_inspected") or "").strip()
        converging = str(prompt_vars.get("must_converge") or "").startswith("是")
        evidence_ids = _evidence_ids_in(summary)
        if converging or (inspected and inspected != "（无）"):
            action, arguments = "ANSWER", {}
        elif evidence_ids and "INSPECT_EVIDENCE" in allowed:
            #: **单数**：`INSPECT_EVIDENCE` 的动作层与工具契约层已统一到 `{"evidence_id": …}`
            #: （工具层本就只读这一个键、`max_results=1`）。一次动作 = 一次 tool call，由既有
            #: harness 环按 `can_afford_tool_call` 记账——**不**把一次复数偷偷展开成多次未记账请求，
            #: 也**不**靠放大 `max_need_rounds_per_aspect` / top-k 造候选。
            action, arguments = ("INSPECT_EVIDENCE",
                                 {"evidence_id": evidence_ids[0]})
        elif "SEARCH_LOCAL" in allowed:
            action, arguments = ("SEARCH_LOCAL", {
                "query": str(prompt_vars.get("question") or "")})
        else:
            # 当前路由没有任何可用检索动作 ⇒ 如实带缺口停下，不自造动作。
            action, arguments = "STOP_WITH_GAP", {
                "reason": "离线替身：当前路由未开放任何本地检索动作"}
        self.calls.append({"kind": "action", "action": action})
        return self._respond(json.dumps({"action": action, "arguments": arguments},
                                        ensure_ascii=False), kind="action-done",
                             prompt_version=RT.RESEARCH_ACTION_PROMPT_VERSION)

    def generate_answer(self, prompt_vars: dict) -> "object":
        from harness import runtime as RT

        available = str(prompt_vars.get("available_material") or "")
        aspect_ids = _aspect_ids_in(str(prompt_vars.get("required_aspects") or ""))
        claims: list[dict] = []
        citations: list[dict] = []
        for evidence_id, body in _evidence_blocks(available)[:_OFFLINE_RESEARCH_MAX_CLAIMS]:
            excerpt = _citable_excerpt(body)
            if excerpt is None:
                continue
            claims.append({"claim_id": f"c{len(claims) + 1}", "kind": "fact",
                           "text": excerpt, "citation_refs": [len(citations)]})
            citations.append({"ref_type": "evidence", "evidence_id": evidence_id})
        # 本 need 是**按 aspect 构造**的（`InformationNeedBuilder.build(aspect, ...)`），
        # 因此它的 claim 确实属于该 aspect；这里如实回填覆盖，不额外声明没读到的东西。
        claim_ids = [c["claim_id"] for c in claims]
        answer = {
            "answer_text": "".join(c["text"] for c in claims),
            "claims": claims, "citations": citations,
            "aspects": [{"aspect_id": a, "text": "", "claim_ids": claim_ids}
                        for a in aspect_ids],
            "confidence": "low",
        }
        self.calls.append({"kind": "answer", "claims": len(claims)})
        return self._respond(json.dumps(answer, ensure_ascii=False), kind="answer-done",
                             prompt_version=RT.RESEARCH_ANSWER_PROMPT_VERSION)

    def evaluate_entailment_batch(self, prompt_vars: dict) -> "object":
        """逐字包含检查：claim 文本必须逐字出现在它自己声明的引用所指的证据里。

        判定只用到两样东西：claim 自己声明的**引用序号**（`cites=[…]`），以及引用序号到
        `evidence_id` 的映射（引用块）。两处都由**正式渲染器** `harness/entailment.py::
        entailment_prompt_vars` 生成，本函数只负责解析——因此引用块的行形状（项目符号
        `- [i] …`）是与那个渲染器的**耦合**，改动其一必须同改另一，见 `_CITATION_LINE_RE`。
        解析不出 `evidence_id` 时判 `UNSUPPORTED`（fail-closed），**不**退化成「按构造为真」。
        """
        from harness import runtime as RT

        evidence = _evidence_blocks(str(prompt_vars.get("evidence") or ""))
        body_by_id = {eid: _normalize(text) for eid, text in evidence}
        cited_id = {int(i): eid for i, eid in _CITATION_LINE_RE.findall(
            str(prompt_vars.get("citations") or ""))}
        verdicts: list[dict] = []
        for claim_id, cited, text in _claim_lines(str(prompt_vars.get("claims") or "")):
            needle = _normalize(text)
            supported = bool(cited) and bool(needle) and all(
                needle in body_by_id.get(cited_id.get(i, ""), "")
                for i in cited)
            verdicts.append({
                "claim_id": claim_id, "citation_ids": [str(i) for i in cited],
                "verdict": "SUPPORTED" if supported else "UNSUPPORTED",
                "reason": ("claim 文本逐字出现在其声明的证据正文里（离线替身的包含检查）"
                           if supported else
                           "claim 文本未逐字出现在其声明的证据正文里（离线替身的包含检查）")})
        self.calls.append({"kind": "entailment", "verdicts": len(verdicts)})
        return self._respond(json.dumps({"verdicts": verdicts}, ensure_ascii=False),
                             kind="entailment-done",
                             prompt_version=RT.RESEARCH_ENTAILMENT_PROMPT_VERSION)


def _normalize(text: str) -> str:
    """比较用归一：只去空白，不改字符——包含检查必须逐字。"""
    return re.sub(r"\s+", "", text or "")


def _evidence_ids_in(summary: str) -> list[str]:
    match = _EVIDENCE_ID_LIST_RE.search(summary or "")
    if not match:
        return []
    return [token.strip() for token in match.group(1).split(",") if token.strip()]


def _evidence_blocks(text: str) -> list[tuple[str, str]]:
    """按 `### evidence_id=…` 头切出 (evidence_id, 正文) 列表（顺序保持）。"""
    matches = list(_EVIDENCE_BLOCK_RE.finditer(text or ""))
    blocks: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        head_end = text.find("\n", match.end())
        body = text[head_end + 1:end] if head_end != -1 else ""
        blocks.append((match.group(1), body))
    return blocks


def _citable_excerpt(body: str) -> str | None:
    """从证据正文里取一句**逐字**可引用的描述；取不到就返回 None（不提这条）。"""
    for line in (body or "").splitlines():
        stripped = line.strip()
        if len(stripped) >= _OFFLINE_RESEARCH_MIN_EXCERPT_CHARS:
            return stripped
    return None


def _aspect_ids_in(required_aspects: str) -> list[str]:
    return [line[2:].split(":", 1)[0].strip()
            for line in (required_aspects or "").splitlines()
            if line.startswith("- ") and ":" in line]


def _claim_lines(text: str) -> list[tuple[str, list[int], str]]:
    """解析 entailment prompt 里的 claims 段：`- c1 [fact] cites=[0]: <可含换行的正文>`。"""
    rows: list[tuple[str, list[int], str]] = []
    current: tuple[str, list[int], list[str]] | None = None
    for line in (text or "").splitlines():
        if line.startswith("- "):
            if current is not None:
                rows.append((current[0], current[1], "\n".join(current[2])))
            head, _, tail = line[2:].partition(": ")
            claim_id = head.split(" ", 1)[0]
            cites = re.search(r"cites=\[([^\]]*)\]", head)
            refs = [int(x) for x in (cites.group(1).split(",") if cites else [])
                    if x.strip().isdigit()]
            current = (claim_id, refs, [tail])
        elif current is not None and line.startswith("  "):
            # 第二行是「确定性预检」标记，不是 claim 正文——但只在它确实以该标记开头时排除。
            if not line.strip().startswith("确定性预检"):
                current[2].append(line)
        elif current is not None:
            current[2].append(line)
    if current is not None:
        rows.append((current[0], current[1], "\n".join(current[2])))
    return rows


class RealEnvironment:
    """真实输入的装配与生命周期（临时库/审计目录只存在于本次运行内）。"""

    def __init__(self, *, clock, mode: str = MODE_OFFLINE,
                 declaration: DeclaredReportInput, source_path_resolver=None) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="m930_3_acc_")
        self.root = Path(self._tmp.name)
        self.inputs: RealInputs | None = None
        #: 本轮唯一时钟瞬间（`ReportClockInstant`）：`report_as_of` / `generated_at` 都取自它。
        self._clock = clock
        #: 本次报告输入声明（P4）：主体由声明决定——库只能被用来**核对**它。
        self._declaration = declaration
        #: 运行模式决定**研究侧** LLM 注入的是真实 provider 还是确定性替身；两者共用同一条正式
        #: 研究链（Router / ToolRegistry / 预验证资格门），差别只在 LLM 这一层。
        self._mode = mode
        #: **本 run 的上传输入解析器**（`sections.cited_run_input.RunInputResolver`）。给出时，
        #: 逐份成员「去哪读原始 PDF」由它按 `(document_id, file_sha256)` 回答，**永不**回落到
        #: `member.source_path`。为 `None` 时用登记路径（历史兼容运行，含全部既有离线重放）。
        #: 它是**读哪一份文件**的入口，不是「允许读得更少」的开关：下面逐份的 sha256 相等判定
        #: 一字不改，解析出来的路径还必须落在解析器自己的根目录内。
        self._source_path_resolver = source_path_resolver
        #: 研究侧预算轴与现场研究预算的**镜像复核对账**（`_build` 里填）。默认「未核对」——
        #: 读它的人从中看得到「本研究入口是否真的受过事前门约束」，而不是默认已通过。
        self.research_axis_evidence: dict = {
            "checked": False, "reason": "尚未进入研究构建（未核对）"}

    def __enter__(self) -> "RealEnvironment":
        # Evidence 读面：`evidence.store._db_path` 是模块全局，且 `evidence_gateway` 要求在
        # 任何 span 快照之前就已绑定（要的是「当前 Evidence 权威」而不是「本次运行的临时库」）。
        # 绑定的是**真实只读库路径**，绑定动作本身不写库；退出时还原，避免把全局留给同进程的
        # 后续工作（eval 里可以长期绑定，验收 runner 不该这样做）。
        from evidence import store as estore

        self._saved_evidence_db = estore._db_path
        estore._db_path = (REPO / "data/evidence.db").resolve()
        # 财务读面同理：正式 Router 的 `RouteContext` 要从**真实只读财务库**取当前快照的可用
        # 字段/指标/期间（`routing.context` 走 `financial_v2.store` 的模块级路径）。这里**只绑
        # 路径**，不调用 `init_db`——`init_db` 会跑迁移、写库，那是修改历史数据库，本 runner
        # 明确不做。绑定后只读使用；退出时还原。
        from financial_v2 import store as fstore

        self._saved_financial_db = fstore._db_path
        fstore._db_path = (REPO / "data/financial_v2.db").resolve()
        try:
            self.inputs = self._build(self.root)
        except BaseException:
            estore._db_path = self._saved_evidence_db
            fstore._db_path = self._saved_financial_db
            self._tmp.cleanup()
            raise
        return self

    def __exit__(self, *exc) -> None:
        from evidence import store as estore
        from financial_v2 import store as fstore

        estore._db_path = getattr(self, "_saved_evidence_db", None)
        fstore._db_path = getattr(self, "_saved_financial_db", None)
        self._tmp.cleanup()

    # -- 真实输入装配 --------------------------------------------------------
    def _build(self, workdir: Path) -> RealInputs:
        # §二 1：研究侧**不再**注入 `evals.test_demo_topic_runtime._SpanRecallResearch`
        # ——那个替身绕过 Router 与预验证资格门，把整段原文复制成"已验证事实"并自报
        # SUPPORTED。「跑得通」与「内容可信」是两件事，验收链只能取后者。现在只留下
        # `_NeedBuilder` / `_Sink` 两个纯结构件（前者按冻结 aspect 构造 need，后者收集轨迹），
        # 研究本身走正式 `harness.runtime.run_question`（真 Router + 真 ToolRegistry +
        # 真 `_adopt_facts`）；离线模式也只替换 LLM 这一层（`OfflineResearchLLM`）。
        # §三 3（acc-28）：need 构造器改由模块级工厂 `_information_need_builder()` 产出——
        # 注入点与 `external_retrieval` 块读**同一个**实例，报告因此能按实际类型指名它。
        from evals.test_demo_topic_runtime import _Sink
        from contracts.loader_v2 import load_contract_v2
        from document_structure import live_span_source as LSS
        from document_structure import navigation as NAV
        from document_structure import synopsis as SY
        from harness import r2_dependencies as R2
        from harness import runtime as RT
        from harness import source_policy_resolver as SPR
        from harness import topic_runtime as TR
        from harness import tree_tools as TT
        from planning import demo_scope as SC
        from planning import schema as PS
        from routing import context as RContext
        from routing import router as RR
        from sections import company_worker as CW
        from sections import financial_worker as FW
        from sections import industry_worker as IW
        from sections import pack_set as PSet
        from sections import pack_writer as PW
        from sections import presentation_profile as PP
        from sections import writing_spec as WS
        from tools import adapters as A

        from evidence import store as estore
        from harness import source_manifest as SM

        evidence_db = (REPO / "data/evidence.db").resolve()
        # `_db_path` 由 `__enter__` 绑定（并保证退出还原）；这里只读它，不重新绑定。
        _require_evidence_bound(estore, evidence_db)

        # 主体身份取自**本次报告输入声明**（P4）：既不再取自 PDF 所在目录名，也不取自
        # 「财务库里排序后第一条快照属于谁」。库的作用是把声明**核对成事实**，不是决定它。
        dims = _financial_dims(REPO / "data/financial_v2.db", self._declaration)
        if not dims:
            raise AcceptanceRefusal(
                f"真实输入不足（financial_snapshot={dims!r}）："
                "本 runner 不得用合成输入冒充真实输入")
        company = str(self._declaration.subject_id)
        if str(dims["company_id"]) != company:  # 不变量：核对结果不得与声明的主体不同
            raise AcceptanceRefusal(
                f"财务快照核对结果 {dims['company_id']!r} 与声明主体 {company!r} 不一致")

        # 一.1：为**全部**当前、政策允许的上传材料建立来源清单，再由清单**选中**本次绑定文档。
        # 改前这里是 `sorted(glob(...))[0]`——按 PDF 路径字典序取第一份，与业务无关、与
        # 主题无关、与披露日期无关，且被丢弃的另外两份没有任何记账。现在登记与选用分开：
        # 每份材料都在清单里，未选中的也带 reason_code + 理由。
        manifest = SM.build_source_manifest(
            db_path=evidence_db, company_id=company,
            generated_at=self._clock.generated_at,
            report_as_of=self._clock.report_as_of,
            report_timezone=self._clock.report_timezone,
            financial_data_cutoff=str(dims["as_of_date"]))
        # 「同主体」的第二个核对面：来源清单必须就是**声明主体**的清单。清单是拿
        # `company_id=company` 建的，这里把结果再对一次——防止将来某次重构把某个维度参数
        # 悄悄换成另一个值，让「声明的公司」与「登记的材料」不再指向同一个主体。
        if str(manifest.company_id) != company:
            raise AcceptanceRefusal(
                f"来源清单的主体 {manifest.company_id!r} 与声明主体 {company!r} 不一致："
                "声明主体与 current Evidence Set 必须指向同一个公司")
        entry = manifest.by_document_id(manifest.primary_document_id) \
            if manifest.primary_document_id else None
        if entry is None:
            raise AcceptanceRefusal(
                "来源清单里没有任何可用的当前材料，本 run 无法构成真实验收："
                + "；".join(f"{s.document_id}[{s.reason_code}]" for s in manifest.selection))

        # 一.1b（跨文档 §L0.3）：**清单层准入闸**。先于任何非空会话/上下文构造判定；未准入
        # 即拒绝开跑（零 tree 调用、零 Pack 提交），绝不「暗中挑一份充当当前来源」，也不以
        # 源集首项兜底。
        blocked = SM._admit_source_set(manifest)
        if blocked is not None:
            raise AcceptanceRefusal(
                f"来源清单未通过源集准入：{blocked.reason_code} {blocked.reason}")
        member_entries = manifest.source_set_entries()
        if not member_entries:
            raise AcceptanceRefusal(
                "来源清单的有序源集为空，本 run 无法构成真实验收："
                + "；".join(f"{s.document_id}[{s.reason_code}]" for s in manifest.selection))

        # 一.1c：**逐份**成员的读取准备。每份各做一次「注册身份 ↔ 磁盘实读」完整性核对
        # （清单里的 sha256 是登记值，必须与磁盘实读一致，否则「按清单选的文档」和「真正被
        # 解析的 PDF」就不是同一份东西），通过后才各起一个有身份的 live 快照与树会话。
        # 「登记了三份 ≠ 读了三份」在这里落地：任何一份核不过就整轮拒绝开跑，而不是把没核过的
        # 成员留在源集里冒充「已纳入」。
        member_lives: dict = {}
        member_sessions: dict = {}
        anchor_sha: str | None = None
        # T4：当前锚的四轴。锚的**实物**（sha / 会话 / 导航索引）与逐份成员一样按四轴取回，
        # 不按 `document_id`：同 id 的另一版本正是源集里合法共存的另一份文档。
        anchor_entry_key = manifest.key_of(entry)
        if anchor_entry_key is None:
            raise AcceptanceRefusal(
                f"清单选中的当前锚 {entry.document_id} 无法构成文档键（缺证据集版本）："
                "锚身份不完整时不得按 document_id 就近取一份成员顶替（fail-closed）")
        anchor_axes = SM.source_key_axes(anchor_entry_key)
        for member in member_entries:
            member_key = manifest.key_of(member)
            if member_key is None:
                raise AcceptanceRefusal(
                    f"源集成员 {member.document_id} 无法构成文档键（缺证据集版本）")
            member_axes = SM.source_key_axes(member_key)
            if self._source_path_resolver is not None:
                #: 本 run 的上传输入：**按 `(document_id, sha256)` 定位上传对象**，没有
                #: 「登记路径」这条回落。解析器自己会在命中 0 份 / ≥2 份 / 对象丢失 / 字节
                #: 变化时抛错；这里再断言一次它答的路径确实落在它自己的根目录内——解析器
                #: 返回一个别处的路径（哪怕内容对）就不是「读了本次上传的那一份」。
                pdf = self._source_path_resolver.resolve(
                    document_id=str(member.document_id),
                    file_sha256=str(member.file_sha256))
                if not self._source_path_resolver.owns(pdf):
                    raise AcceptanceRefusal(
                        f"源集成员 {member.document_id} 解析出的路径不在本 run 的上传目录内："
                        f"{pdf}（fail-closed）")
            else:
                pdf = Path(member.source_path) if member.source_path else None
                if pdf is None or not pdf.exists():
                    raise AcceptanceRefusal(
                        f"源集成员 {member.document_id} 的 source_path 不可达："
                        f"{member.source_path!r}")
            member_sha = _sha256_file(pdf)
            if member_sha != member.file_sha256:
                raise AcceptanceRefusal(
                    f"源集成员 {member.document_id} 的磁盘内容与登记 sha256 不一致："
                    f"登记 {member.file_sha256} / 实读 {member_sha}（fail-closed）")
            if not member.evidence_set_version:
                raise AcceptanceRefusal(
                    f"源集成员 {member.document_id} 没有 current evidence set："
                    f"{member.eligibility_reason}")
            member_live = LSS.build_live_verified_span_snapshot(LSS.LiveSpanBuildRequest(
                company_id=company, document_id=member.document_id,
                document_version=member.document_version,
                raw_pdf_path=str(pdf), raw_pdf_sha256=member_sha,
                expected_current_evidence_set_version=member.evidence_set_version))
            # T4：逐份成员的读回实物按**四轴**记账。按 `document_id` 记账时，同 id 不同
            # 版本的成员会互相覆盖（源集里恰好允许同 id 多版本共存），后读的那份会顶掉前一份，
            # 「三份源集」在导航层就悄悄少了一份。
            member_lives[SM.source_key_axes(member_key)] = member_live
            member_sessions[member_key] = TT.TreeInspectionSession(member_live)
            if member_axes == anchor_axes:
                anchor_sha = member_sha
        if anchor_sha is None:
            raise AcceptanceRefusal(
                f"清单选中的当前锚 {entry.document_id} 不在有序源集内："
                "fail-closed（不以别的成员顶替锚）")
        sha = anchor_sha
        doc_version = entry.document_version
        set_version = entry.evidence_set_version
        # T4：当前锚按**四轴**在源集会话里取回；按 `document_id` 取会命中同 id 的另一版本，
        # 于是锚的导航索引、`anchor_key` 与随后的事实归属都落在另一份文档上。
        anchor_matches = [k for k in member_sessions
                          if SM.source_key_axes(k) == anchor_axes]
        if len(anchor_matches) != 1:
            raise AcceptanceRefusal(
                f"当前锚在源集会话里不是恰好一份（命中 {len(anchor_matches)} 份）："
                f"anchor={anchor_axes}；同 id 不同版本的成员不得互相顶替（fail-closed）")
        anchor_key = anchor_matches[0]
        members: list = []
        for k in member_sessions:
            # T4：角色按**四轴**回查台账。只给 (id, version) 时，同 id 同版本、不同证据集的
            # 两行会互相命中对方的角色——源集成员拿到的会是**另一份文档**的 source_role。
            role = manifest.role_of(k.document_id, k.document_version,
                                    k.evidence_set_version)
            if role is None:
                raise AcceptanceRefusal(
                    f"源集成员 {k.document_id} 在清单里没有 source_role："
                    "不得默认它参与检索（fail-closed）")
            members.append((k, role))
        sources = TR.DocumentSourceSet(members=tuple(members))

        profile = SC.load_demo_scope_profile(active_profile_path())
        policy_resolver = SPR.FrozenSourcePolicyResolver.from_asset(
            REPO / profile.source_policy_asset)
        contract = load_contract_v2(str(REPO / profile.contract_asset))
        # 主体名称取自**声明 + 权威核对**（`subj-2`），不再退化成证券代码：改前这里把
        # `company_name` 直接填成 `company`，于是章节/正文里凡是引用公司名的地方都印出代码。
        business = PS.ReportJobInput(
            job_id="job_m930_3_acceptance_0001", company_id=company,
            company_name=str(dims["company_name"]),
            credit_type="general", report_as_of=self._clock.report_as_of,
            contract_version="v2")
        source_inputs = {
            "case_input_id": "case_m930_3_acceptance_0001",
            "document_id": entry.document_id,
            "document_version": doc_version, "raw_pdf_sha256": sha,
            "current_evidence_set_version": set_version,
            "substrate_dependency_versions": {
                k: f"{k}-v1" for k in SC.SUBSTRATE_DEPENDENCY_KEYS},
            "external_policy_snapshot_id": None,
            "budget_policy_id": profile.budget_policy_id,
            "budget_policy_version": profile.budget_policy_version,
            "model_policy_id": "demo_model_policy_v1", "code_fingerprint": "1" * 64,
        }
        manifest_scope = SC.build_scope_input_manifest(profile, business, source_inputs)
        projection = SC.project_contract_v2_scope(contract, profile, manifest_scope)
        SC.verify_demo_projection(projection, contract)
        requirements = {r.topic_id: r for r in projection.requirements}
        tasks = {t.section_id: t for t in projection.report_plan.section_tasks}
        #: 祖先层回退（M930-3 业务纵链补充门；`anp-4` 定点返修 C1 收窄键的来源）：当某个 aspect
        #: 在自己的层级里**没有任何完整标签段命中**时，沿真实标题树回退到由冻结 Contract 派生的
        #: 祖先声明键。`anp-4` 起，祖先键 = **该 aspect 所属 question 的文本** + **按 question
        #: 归属规则判给该 question 的 topic 标题段**；`anp-3` 用的是整段 topic 标题，那会让同一
        #: topic 下的每个 aspect 都读到别的 question 的正文（真实 Contract 上 13 个 aspect 齐读
        #: `3经营模式`）。这些键只来自冻结 Contract —— 它不是本 runner 的输入、更不是公司规则，
        #: 因此在这里一次性从 Contract 派生并复用。派生为空即 fail-closed 拒绝开跑：
        #: 空标签会让祖先层回退静默关闭，把「读不到祖先正文」误报成「该 aspect 没有祖先」。
        ancestor_labels, sibling_keys = NAV.contract_ancestor_inputs(contract)
        if not ancestor_labels:
            raise AcceptanceRefusal(
                "Contract 未派生出任何主题/问题祖先标题，父节点回退将静默关闭；"
                "拒绝以空祖先声明继续。")
        if not sibling_keys:
            raise AcceptanceRefusal(
                "Contract 未派生出任何兄弟项排除集，兄弟项排除将静默关闭"
                "（祖先层可能仍含兄弟栏目的字段名）；拒绝以空排除集继续。")
        spec = WS.load_writing_spec(str(REPO / profile.writing_spec_asset))
        pprofile = PP.load_presentation_profile(
            str(REPO / profile.presentation_profile_asset))

        # 逐 topic 的 aspect 数（**真实投影**，本 runner 的唯一真值来源）。
        aspects_by_topic = {tid: len(r.aspects) for tid, r in sorted(requirements.items())}
        # 研究预算仍是**一份共享**的 `ResearchBudgetPolicy`，取各 topic 结构上界的**最大值**。
        # 为什么不是「逐 topic 各一份」：这份政策会通过 `TopicRunContext.budget_policy` 进入
        # 运行身份，而 `harness/topic_runtime.py::run_topic_requirement` 对此有 fail-closed
        # 断言（`budget_state.policy == run_context.budget_policy`）；一个 section 只有**一个**
        # run_context，要逐 topic 各持一份就必须改 `company_worker.run_backbone_topic_phase`
        # 与 `BackboneWriterSectionInput` 的公共入口签名——那是跨文档方案**未覆盖**的公共
        # wire 变更，本轮不做。
        # 逐 topic 的**事前硬上限**因此放在真正审计的那道门上：`AxisCap.per_scope_max_attempts`
        # （见 `_research_axis_cap`）。它对除最大 topic 外的每个 topic 都**更紧**，所以「本 topic
        # 最多发几次」的实际约束就是逐 topic 的那个值；这份共享值只是它不得低于的结构上界。
        # 旧口径的毛病同时消失：不再是「把首个 topic 的 aspect 数沿用于全部 topic」。
        max_aspects = max(aspects_by_topic.values())
        # ── 工具轴的**下沿**：本 topic 的初次必读结构上界（责任矩阵现场派生）─────────
        #
        # 与 `harness/topic_runtime.py` 用**同一个** `TR.mandatory_dispatch_bound()` 复算，输入
        # 是本轮**真实**的源集、导航能力与冻结投影出的 aspect 集（三份来源全部
        # `retrieval_required` 时 = 逐 aspect 3 次，单文档退化时 = 逐 aspect 1 次）。因此
        # 「上限盖不盖得住必读」不再靠人估：runtime 侧还有一次同式的 fail-closed 断言。
        #
        # 工具轴上限 = 初次必读结构上界 + 可选调用结构上界 × 该 topic 的 aspect 数，取各 topic
        # 的最大值（共享政策的原因见下）。它**不是**把旧值统一放大：旧值 `3 × aspects` 在任何
        # 跨源 topic 上都**低于**必读下沿，本就无法执行；这里的两项各自有结构来由。
        _tool_mandatory_by_topic = {}
        for _tid, _req in sorted(requirements.items()):
            # 责任台账按 §L1.5 在**检索前**逐 aspect 派生（与 runtime 里那一次输入轴完全相同：
            # 同一个 `derive_aspect_source_responsibility`、同一份真实源集、同样的 resolved）。
            _responsibility = tuple(
                row for _aspect in _req.aspects
                for row in TR.derive_aspect_source_responsibility(
                    aspect=_aspect, sources=sources,
                    current_state=SM.CURRENT_STATE_RESOLVED))
            _tool_mandatory_by_topic[_tid] = TR.mandatory_dispatch_bound(
                _req.aspects, _responsibility,
                anchor=sources.current_state_key(), multi_source_navigation=True)
        tool_mandatory_bound = max(_tool_mandatory_by_topic.values())
        tool_optional_allowance = RESEARCH_OPTIONAL_TOOL_CALLS_PER_ASPECT * max_aspects
        budget = TR.ResearchBudgetPolicy(
            policy_id=profile.budget_policy_id, version=profile.budget_policy_version,
            tier="demo_backbone",
            max_need_rounds_per_aspect=RESEARCH_MAX_NEED_ROUNDS_PER_ASPECT,
            max_need_rounds_per_topic=2,
            max_tool_calls_per_topic=tool_mandatory_bound + tool_optional_allowance,
            max_tokens_per_topic=200000,
            max_llm_calls_per_topic=RESEARCH_LLM_CALLS_PER_ASPECT * max_aspects,
            max_elapsed_ms_per_topic=900000, tree_max_spans_per_aspect=6,
            tree_max_chars_per_span=4000)
        self.research_tool_budget_evidence = {
            "rule_version": TR.MANDATORY_FIRST_READ_RULE_VERSION,
            "source": "本 topic 的责任矩阵现场派生 + 两类调用的结构上界",
            "mandatory_tool_calls_by_topic": _tool_mandatory_by_topic,
            "mandatory_tool_calls_bound": tool_mandatory_bound,
            "optional_tool_calls_per_aspect": RESEARCH_OPTIONAL_TOOL_CALLS_PER_ASPECT,
            "optional_tool_calls_allowance": tool_optional_allowance,
            "max_tool_calls_per_topic": budget.max_tool_calls_per_topic,
            "derivation": {
                "optional_per_aspect": "1（兜底重切，每栏至多一次）"
                                       f" + {RESEARCH_MAX_NEED_ROUNDS_PER_ASPECT}（follow-up 轮数）"
                                       f" × {RESEARCH_TOOL_CALLS_PER_NEED}（每 need 内层工具上界）"
                                       f" = {RESEARCH_OPTIONAL_TOOL_CALLS_PER_ASPECT}",
                "mandatory": "Σ_aspect |{来源 : 该来源是当前锚 或 责任判定必须检索}|"
                             "（`harness/topic_runtime.py::mandatory_dispatch_bound` 同式复算）",
                "note": "旧口径 `3 × aspects` 在任何跨源 topic 上低于必读下沿，已由本推导替代；"
                        "这不是「统一放大 top-k」，而是把工具轴补上与 LLM 轴同等的结构推导",
            },
        }

        from llm import budget as LB

        # **研究侧事前门的镜像复核**（放在研究真正开始之前）。门的政策在进入本环境之前就已
        # 建好（研究侧上限只能来自那一步），而现场这份 `max_llm_calls_per_topic` 到这里才由
        # Contract 投影算出来——两处一旦漂移，研究阶段受的就是**另一组**上限。逐 topic 与整轮
        # 两个数必须与镜像逐值相等，否则整轮拒绝、一个研究请求都不发。
        research_topic_ids = tuple(sorted(requirements))
        gate = LB.installed()
        if gate is None:
            if self._mode == MODE_REAL:
                raise AcceptanceRefusal(
                    "真实模式下尚未安装调用预算门就进入研究构建：研究请求会不受任何事前门"
                    "约束（fail-closed；门必须在 `with RealEnvironment(...)` **之前**安装）")
            # 离线替身不发真实请求，这里如实记录「本研究入口不受门约束」，不假装已核对。
            self.research_axis_evidence = {
                "checked": False,
                "reason": "未安装预算门：本入口的研究请求不经过事前门（离线替身不发真实请求）",
            }
        else:
            self.research_axis_evidence = _assert_research_axis_mirrors_research_policy(
                gate.policy, aspects_by_topic=aspects_by_topic, runtime_budget=budget,
                topic_ids=research_topic_ids)

        # §L3.3/§L3.7：**一个** registry、**一个**工具名、**一个** run_id 下的源集会话。
        # 工具按调用参数里的四轴精确分派到对应那份会话；取不到即 typed `SOURCE_NOT_BOUND`，
        # 绝不回退到源集里的任何一份。
        span_sessions = TT.SourceSetSpanSessions(member_sessions)
        reg, span_sessions = A.build_source_set_registry(
            sessions=span_sessions, audit_dir=workdir / "audit")
        anchor_session = span_sessions.session_for(anchor_key)
        if anchor_session is None:
            raise AcceptanceRefusal(
                f"源集会话里找不到当前锚 {anchor_key.document_id}（fail-closed）")
        # 逐份成员各建一棵只读导航索引（§L2.3）：跨源只是「有几个索引」变了，导航规则一个
        # 字节都没变（`PROFILE_RULE_VERSION` 不因跨源升版）。某一份索引用不了即整轮拒绝——
        # 静默跳过那一份会让「3 份源集」在导航层悄悄变成 2 份。
        nav_indexes: list = []
        for member_key in span_sessions.keys():
            # T4：逐份成员的读回实物同样按四轴取回（与上面写入时同一个键）。
            member_axes = SM.source_key_axes(member_key)
            if member_axes not in member_lives:
                raise AcceptanceRefusal(
                    f"源集成员 {member_key.to_dict()} 没有对应的读回实物："
                    "逐份成员与逐份实物必须四轴一一对应（fail-closed）")
            member_live = member_lives[member_axes]
            snapshot, outline = member_live.snapshot, member_live.document_outline
            synopses = SY.build_navigation_synopses(
                node_ids=sorted({n.node_id for n in outline.nodes}), spans=snapshot.spans,
                coverages=snapshot.coverages, dispositions=snapshot.dispositions,
                policy=member_live.qualification_policy)
            member_index = NAV.NavigationIndex(
                outline, synopses, span_node_ids={s.node_id for s in snapshot.spans},
                # `anp-7` 主体补读要用的是**逐节点自有正文字符数**（与简介同源、
                # 同样只读 `navigation_admissible` 的 span）。不给这个量，补读规则会
                # 判不了"这一节有没有实质正文"并**不补读**——即整条纵向链退回 `anp-6`
                # 的读集。这里显式给，且只给计数、不给正文，索引仍然拿不到证据内容。
                body_char_counts=SY.build_navigation_body_chars(snapshot.spans))
            if member_index.unavailable_reason is not None:
                raise AcceptanceRefusal(
                    f"来源 {member_key.document_id} 的导航索引不可用："
                    f"{member_index.unavailable_reason}")
            # 键是**文档键 dict**（不可哈希），因此按**有序对**登记：序位是语义，逐份台账与
            # `navigate_all` 都按它读，不能靠 dict 的偶然迭代序。
            nav_indexes.append((member_key.to_dict(), member_index))
        indexes = NAV.SourceSetNavigationIndex(nav_indexes)
        # 每份会话各自签发重切 payload 的唯一入口：写作侧读材料时三份的 payload 都要能解析，
        # 因此按顺序组合（`CombinedPayloadResolver`：0 命中 = dangling、≥2 命中 = 歧义，都
        # fail-closed），而不是只认锚那一份。
        span_resolvers = tuple(s.resolver for s in span_sessions.sessions())
        # `v6` 表对象通道：每个会话另有**独立重切**的表材料解析器（`tom-1`）。它与 span
        # 解析器是两种解析语义（`tmr-2` vs `tom-1`），互不代答：任一 ref 只有本语义的那一个
        # 会命中，因此组合器不会出现"同一 payload 被两套来源同时认领"。两份**一起**给出去，
        # 否则 Pack 里的表材料在写作侧读回时会整批 dangling。
        table_resolvers = tuple(s.table_resolver for s in span_sessions.sessions())
        payload_resolvers = span_resolvers + table_resolvers

        document = TR.DocumentIdentity(**anchor_session.document_identity())
        db = workdir / "research.db"
        store = TR.SqliteTopicStoreAdapter(db)
        store.init()
        _dep, _sp, scv, sev = R2.build_r2_material_dependencies(db)
        reader = PSet.SqlitePackSetStore(
            path=db, resolver=_all_payload_resolvers(payload_resolvers))
        sink = _Sink()
        #: 研究侧 LLM：真实模式是**真实 provider**（`RT.RealResearchLLM`，`harness/runtime.py`
        #: 的生产实现），离线模式是确定性替身。两者注入的是同一条正式研究链，差别只在 LLM 这一
        #: 层（`TR.TopicRuntimeDependencies.llm`）。
        research_llm = (RT.RealResearchLLM() if self._mode == MODE_REAL
                        else OfflineResearchLLM())
        #: §二 1：focused follow-up 的 `RouteContext` 是**真实的**（真快照 / 真可用文档 / 真
        #: 可用字段），且本次运行只构造一次并复用同一个对象——`route_fn` 与 `research_question`
        #: 必须看到同一个上下文，否则「路由看到的」与「研究跑起来的」可能静默错配。
        #:
        #: `rc-rd-1`（P4）：两个日期在这里**各就各位**——`as_of_date` 只做快照选择，
        #: `report_as_of` 由本轮时钟声明（报告参考日），不再由财务期末冒充。改前本 runner 只能
        #: 「只把 dims["as_of_date"] 当快照选择器、不覆写 report_as_of」，于是路由看到的报告日
        #: 仍然是财务期末——一个被登记却没有被修掉的冲突。现在接口分开了，冲突不再存在。
        route_context = RContext.build_route_context(
            company, scope=str(dims["scope"]), currency=str(dims["currency"]),
            as_of_date=str(dims["as_of_date"]), report_as_of=self._clock.report_as_of,
            purpose=str(dims["purpose"]),
            # 外部漏斗本轮不启用（与本次授权范围一致：更窄，不是放宽）。真实外部路径另有已报告
            # 的阻断（缺 `event_date`、`dateLastCrawled` 冒充 `published_at`），不在此处绕过。
            # 值取自模块常量：报告侧读同一个常量（`external_retrieval` 块），不出现第二个真值。
            external_research_enabled=EXTERNAL_RESEARCH_ENABLED)

        #: §三 3：本轮**唯一**的 need 构造器实例。同一次运行里所有 topic/section 共用它，
        #: 因此报告侧按实际类型指名的那一句覆盖全部 aspect（不是一个 aspect 一个替身）。
        need_builder = _information_need_builder()

        def _deps(requirement, run_context):
            nav_profile = NAV.build_navigation_profile(
                requirement.aspects, contract_version=requirement.contract_version,
                contract_fingerprint=requirement.contract_fingerprint,
                ancestor_labels=ancestor_labels, sibling_keys=sibling_keys)
            # 跨源导航（§L2.3）：同一套规则、同一个 profile，逐份来源各跑一次。当前锚只作
            # 单文档读视图的落点，**不**决定别的成员是否参与检索。
            navigation = TR.SourceSetTreeNavigation(
                indexes=indexes, profile=nav_profile, anchor_key=anchor_key.to_dict())
            return TR.TopicRuntimeDependencies(
                registry=reg, llm=research_llm, navigation=navigation,
                information_need_builder=need_builder,
                route_context_builder=lambda: route_context,
                # 真 Router（规则优先；冲突且未注入 fallback ⇒ FALLBACK_UNAVAILABLE，fail-closed）。
                route_fn=RR.route, budget_state=TR.TopicBudgetState(policy=budget),
                trace_sink=sink, store=store, payload_resolvers=payload_resolvers,
                source_policy_resolver=policy_resolver,
                set_completeness_verifier=scv, set_enumeration_verifier=sev,
                clock=_utc_now)
            # 注意：**不**注入 `research_question` —— 缺省即 `harness.runtime.run_question`
            # 这条正式入口；注入任何东西都是一次新的替换。

        def _context(requirement, run_id):
            return TR.TopicRunContext(
                run_id=run_id, case_id=run_id + "-case", company_id=requirement.company_id,
                section_id=requirement.section_id, task_id=requirement.task_id,
                report_as_of=requirement.report_as_of,
                demo_scope_fingerprint=manifest_scope.scope_input_fingerprint,
                projection_version=projection.projection_id, document=document,
                # 有序源集整体进运行身份（`content_identity`）：成员、序位、角色任一变化都
                # 必须换运行身份，否则「同一身份、两套来源集」会让逐来源台账与产物对不上。
                sources=sources,
                # `started_at` 是**运行身份时间**（轨迹用），取自本轮唯一时钟瞬间，
                # 而不是再取一次 now——两处各取一次就是 O-11 禁止的跨午夜错位。
                budget_policy=budget, started_at=self._clock.generated_at)

        pack_sets: dict[str, object] = {}
        topic_results: dict[str, object] = {}
        run_contexts: dict[str, object] = {}
        #: 主题研究节的分派**只由本次 profile 派生的节序**决定（`topic_section_workers()`）：
        #: 不写死「公司 + 行业」这一对——双节演示版（公司 + 财务）里没有 industry，写死就会
        #: 在 `tasks['industry']` 上 KeyError（`acc-36` 实测）；未注册的节一律拒绝而不是跳过。
        #: 财务不在这里跑——它有自己的相位（下方 `run_backbone_financial_phase`）。
        for section_id, worker in topic_section_workers():
            task = tasks[section_id]
            section_reqs = tuple(requirements[t] for t in task.topic_ids)
            extra = {} if worker is IW else {"section_id": section_id}
            # §九：本节的研究相位与写作相位**共用**同一个 `TopicRunContext`（同一节就是同一次
            # 运行）；写作侧的有界重写按**每个 target topic** 的 requirement 解析依赖，因此同一个
            # run_context 必须能被多条 requirement 复用，而不是「一节一条、第二条另起一次运行」。
            context = _context(section_reqs[0], f"m930-3-acceptance-{section_id}")
            run_contexts[section_id] = context
            phase = worker.run_backbone_topic_phase(
                task, **extra, requirements=section_reqs,
                run_context=context, dependencies_of=_deps, store=reader)
            pack_sets[section_id] = phase.pack_set
            # 运行现场留一份逐 topic 结果（见 `RealInputs.topic_results`）：typed 栏目未达原因
            # 只在它身上，Pack 本体不带。这里**不**按 `phase.results` 的实际形态做任何筛选——
            # 读回侧自己按 aspect 取 `reason == "column_unmet"` 的那些条目。
            topic_results[section_id] = tuple(phase.results)

        fin_phase = FW.run_backbone_financial_phase(
            tasks["financial"], company_id=dims["company_id"],
            projection_id=projection.projection_id,
            contract_version=contract.contract_version,
            contract_fingerprint=projection.contract_fingerprint,
            company_name=str(dims["company_name"]),
            fin_db=str(REPO / "data/financial_v2.db"),
            scope=dims["scope"], currency=dims["currency"], purpose=dims["purpose"],
            as_of_date=dims["as_of_date"], snapshot_id=dims["snapshot_id"])

        authorities: dict[str, object] = {}
        for section_id in SECTION_ORDER:
            task = tasks[section_id]
            requirement = requirements[task.topic_ids[0]]
            common = {"company_id": company, "report_as_of": self._clock.report_as_of,
                      "contract_version": requirement.contract_version,
                      "contract_fingerprint": requirement.contract_fingerprint}
            if section_id == FINANCIAL_SECTION_ID:
                # `company_name` 只进财务权威身份（`pwr-4`）：它是读者面「主体」一栏的可核实名称，
                # 不是材料、不是事实，也不进 TopicPack 权威——主题权威没有、也不该有这个字段。
                authorities[section_id] = PW.FinancialAuthorityInput.create(
                    task, fin_phase.artifact, note_gap=fin_phase.evidence_note_gap,
                    fact_topic_map=_financial_fact_topic_map(fin_phase.artifact, task),
                    company_name=str(dims["company_name"]),
                    **common)
            else:
                authorities[section_id] = PW.TopicPackAuthorityInput.create(
                    task, pack_sets[section_id], **common)

        return RealInputs(
            company_id=company, document_id=entry.document_id,
            document_version=doc_version,
            raw_pdf_sha256=sha, evidence_set_version=set_version,
            report_as_of=self._clock.report_as_of,
            generated_at=self._clock.generated_at,
            report_timezone=self._clock.report_timezone,
            source_manifest=manifest,
            dims=dims, contract=contract,
            projection=projection,
            scope_fingerprint=manifest_scope.scope_input_fingerprint,
            tasks=tasks, requirements=requirements, writing_spec=spec,
            presentation_profile=pprofile,
            resolver=_all_payload_resolvers(payload_resolvers),
            pack_store=reader, authorities=authorities, financial_phase=fin_phase,
            pack_sets=pack_sets, topic_results=topic_results,
            dependencies_of=_deps, run_contexts=run_contexts,
            # 同一个研究侧 LLM 实例贯穿研究轮与写作轮（写作轮的有界重写会再调 `_deps`，
            # 走的是同一个 `_OfflineResearchLLM`），因此它的调用记录不会被截断成快照。
            research_llm=research_llm, trace_sink=sink,
            external_research_enabled=EXTERNAL_RESEARCH_ENABLED,
            information_need_builder=need_builder)


class AcceptanceRefusal(RuntimeError):
    """无法构成一次真实验收（输入不足 / 授权不足）。**不是**「验收失败」，如实上报即可。"""


def _require_evidence_bound(estore, expected: Path) -> None:
    """确认 Evidence 读面绑在**真实**库上：绑错路径就等于拿另一个库当权威（fail-closed）。"""
    bound = getattr(estore, "_db_path", None)
    if bound is None or Path(bound).resolve() != expected:
        raise AcceptanceRefusal(
            f"Evidence 读面未绑定到真实库（当前 {bound!r}，期望 {str(expected)!r}）")


#: 报告输入声明的版本（P4）。
#: `subj-2`（M930-3「先证明能成稿」定点批 §二·读者面）：声明多了一项**主体名称**。改前读者面
#: 只有一个证券代码（`主体：300750`），而报告里所有「主体」字样都要读者认得出来是谁；名称由
#: 声明带入、并与权威自己的来源文档登记（`financial_source_document.declared_company_name`）
#: 核对 —— 仍然是「声明 + 核对」，不是「从库里挑一个」。字段集变了，旧读者不得把它读成
#: `subj-1`，故版本必须前进。
SUBJECT_DECLARATION_VERSION = "subj-2"


@dataclasses.dataclass(frozen=True)
class DeclaredReportInput:
    """本次报告的**输入声明**：主体由调用方声明，不是从库里推出来的。

    `subj-1`（M930-3 返修 P4）：改前主体取自 `current_snapshot ORDER BY company_id,
    as_of_date, scope LIMIT 1` —— 即「财务库里排序后第一条快照属于谁，这份报告就写给谁」。
    那是把**存量库的排序**当成了用户输入。现在主体来自声明（CLI 参数 / 调用方），库只被用来
    **核对该主体**的财务快照与 current Evidence Set。

    `subj-2`：声明另带**主体名称**（`subject_name`）。名称与主体标识是两件事：标识用来核对
    财务快照与材料清单，名称是读者面的写法。名称同样只能来自声明，且必须与**权威自己的来源
    文档登记的名称**逐字一致（见 `_financial_dims`）；给不出来就拒绝，而不是把证券代码当名称
    印给读者，也不是从库里挑一个名字补上。

    `scope`/`currency`/`purpose`/`snapshot_as_of` 是可选的**收窄条件**：只有当同一主体存在
    多个 current 快照时，声明才需要把它们一起说清楚；说不清楚就拒绝，而不是任选一条。
    """

    subject_id: str
    report_as_of: str
    #: 主体名称（读者面写法）。带默认值**只为**不影响既有位置参数；构造期一律拒绝空值。
    subject_name: str = ""
    scope: str | None = None
    currency: str | None = None
    purpose: str | None = None
    #: 快照选择日的收窄条件（选哪一期财务数据）。**不是**报告参考日：报告参考日在上面的
    #: `report_as_of`，两者可以不同，而且不同才是常态。
    snapshot_as_of: str | None = None
    #: 声明来源（可审计；本批只有 CLI 一个来源，但不把它写成隐含前提）。
    declared_by: str = "cli"
    declaration_version: str = SUBJECT_DECLARATION_VERSION

    def __post_init__(self) -> None:
        if not str(self.subject_id or "").strip():
            raise AcceptanceRefusal("报告输入声明缺少主体：不得由库决定这份报告写给谁")
        if not str(self.report_as_of or "").strip():
            raise AcceptanceRefusal("报告输入声明缺少报告参考日")
        if not str(self.subject_name or "").strip():
            raise AcceptanceRefusal(
                "报告输入声明缺少主体名称：读者面不得只印证券代码，也不得由库替它补一个名字")


def _financial_dims(db: Path, declaration: DeclaredReportInput) -> dict | None:
    """只读打开真实财务库，取**声明主体**的 current 快照口径（不写、不建索引）。

    返回 `None` 只有一种含义：库不存在或读不了（调用方按「真实输入不足」处理）。声明的主体
    在库里**没有** current 快照是另一回事——那是「声明与库不一致」，必须拒绝且不能回落，
    因此这里抛 `AcceptanceRefusal`，不返回空。

    `subj-2`：这里同时核对声明的**主体名称**（`_declared_company_name`）。快照核对的是「这份
    财务数据是谁的」，名称核对的是「读者面该写谁」；两者同源（同一个 `company_id`），但各自
    逐字核对，任一不一致都拒绝。

    同一主体在同一 `as_of_date` 上有多条 current 快照、而声明没有把 `scope`/`currency`/
    `purpose` 收窄到唯一一条时同样拒绝：任选一条就是把「排序第一」换个地方再犯一次。
    """
    if not db.exists():
        return None
    conn = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT company_id, scope, currency, as_of_date, purpose, snapshot_id "
            "FROM current_snapshot WHERE company_id = ? ORDER BY as_of_date, scope, currency",
            (declaration.subject_id,)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not rows:
        raise AcceptanceRefusal(
            f"本次报告输入声明的主体 {declaration.subject_id!r} 在财务库里没有 current 快照："
            "声明与库不一致时不得改口去写库里第一条快照所属的公司（subject 由输入决定，"
            "不由库决定）")
    pinned = [r for r in rows
              if (declaration.scope is None or r[1] == declaration.scope)
              and (declaration.currency is None or r[2] == declaration.currency)
              and (declaration.purpose is None or r[4] == declaration.purpose)
              and (declaration.snapshot_as_of is None
                   or r[3] == declaration.snapshot_as_of)]
    if not pinned:
        raise AcceptanceRefusal(
            f"主体 {declaration.subject_id!r} 的 current 快照都不满足声明的收窄条件"
            f"（scope={declaration.scope!r} currency={declaration.currency!r} "
            f"purpose={declaration.purpose!r} as_of_date={declaration.snapshot_as_of!r}）："
            "候选 " + "；".join(f"{r[1]}/{r[2]}/{r[3]}/{r[4]}" for r in rows))
    if len(pinned) > 1:
        raise AcceptanceRefusal(
            f"主体 {declaration.subject_id!r} 有 {len(pinned)} 条 current 快照同时满足声明的收窄"
            "条件，无法确定本次报告用哪一条：任选一条就是「排序第一」换个地方再犯一次，"
            "必须由声明把 scope/currency/purpose/快照选择日 说清楚。候选 "
            + "；".join(f"{r[5]}({r[1]}/{r[2]}/{r[3]}/{r[4]})" for r in pinned))
    company_name = _declared_company_name(db, declaration)
    if company_name is None:
        # 与快照查询同一口径：读不了就是「读不了」，由调用方按真实输入不足处理；
        # 「登记里没有这个名字」是另一回事，上面已按拒绝处理。
        return None
    row = pinned[0]
    return {
        "company_id": row[0], "scope": row[1], "currency": row[2], "as_of_date": row[3],
        "purpose": row[4], "snapshot_id": row[5],
        # 读者面的公司名称（`subj-2`）：与权威自己的来源文档登记逐字一致（见上）。
        "company_name": company_name,
        # 声明本身也随 dims 带出去：报告里要能读到「主体是谁声明的、报告参考日是哪一个」。
        "subject_id_declared": declaration.subject_id,
        "subject_name_declared": declaration.subject_name,
        "subject_declaration_version": declaration.declaration_version,
        "subject_declared_by": declaration.declared_by,
        "report_as_of_declared": declaration.report_as_of,
    }


def _declared_company_name(db: Path, declaration: "DeclaredReportInput") -> str:
    """把声明的**主体名称**核对成事实（`subj-2`）：只读财务库的来源文档登记。

    判据只有一条口径：该主体在库里**登记过**的名称必须存在，且与声明**逐字相等**。
    一个主体登记了多个互相冲突的名称同样拒绝 —— 那是来源侧自身的矛盾，不能由读者面任选一个。
    登记缺失（没有来源文档行、名称为空、`subject_match_status` 不是 `matched`）同样拒绝：
    「声明了一个库里没有任何权威记录支持的名称」与「声明与库不一致」是同一类缺陷。

    库不存在/读不了返回 `None` 由调用方按「真实输入不足」处理（与快照查询同一口径）。
    """
    conn = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT declared_company_name FROM financial_source_document "
            "WHERE company_id = ? AND subject_match_status = 'matched' "
            "AND declared_company_name IS NOT NULL AND TRIM(declared_company_name) <> ''",
            (declaration.subject_id,)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    names = [str(r[0]).strip() for r in rows]
    if not names:
        raise AcceptanceRefusal(
            f"主体 {declaration.subject_id!r} 在财务库里没有任何来源文档登记过主体名称："
            "读者面要写的公司名称必须由权威自己的登记核对，不得由本 runner 编造一个")
    if len(set(names)) > 1:
        raise AcceptanceRefusal(
            f"主体 {declaration.subject_id!r} 的来源文档登记了不止一个主体名称 {sorted(set(names))}："
            "来源侧自身不一致，读者面不得任选一个")
    actual = names[0]
    declared = str(declaration.subject_name).strip()
    if actual != declared:
        raise AcceptanceRefusal(
            f"声明的主体名称 {declared!r} 与财务库来源文档登记的 {actual!r} 不一致："
            "主体名称同样是「声明 + 核对」，不一致时不得改口去写库里的名字")
    return actual


def _financial_fact_topic_map(artifact, task) -> tuple[tuple[str, str], ...]:
    """artifact fact → topic 的**组合根声明**（M930-5 才有正式组合根；本批显式声明）。

    **两条并列的归属轴**，各自读事实的**不同字段**（这是本函数唯一容易写错的地方）：

    1. 指标事实：`fact.code` 是 formula_id ⇒ 查 `financial_worker._FORMULA_TO_TOPIC`；
    2. 科目金额事实：`fact.code` 是 `standard_item_code` ⇒ 查
       `financial_worker.structure_item_topic`（资产负债表科目 → `fin_balance_structure`）。

    在 `M930-3 阶段 A v2` 之前只有轴 1，于是**每一条** `item_*` 事实都落 `None` ⇒
    `OUTSIDE_SECTION_TOPIC` ⇒ 被 `pack_writer.scan_financial` 静默丢弃：权威里有总资产、
    总负债、流动/非流动项目，读者面上一格都没有。轴 2 就是把这个缺口接上。

    两张表的**完整性**在这里当场核（fail-closed，而不是等某条事实悄悄落空）：资产负债表子集
    必须是 `_STRUCTURE_ITEMS` 的**真子集**，且两张表**不得**对同一个 code 给出两个归属——
    那才是真正的歧义，出了就停，不发明归属。
    """
    from sections import financial_worker as FW
    from sections import pack_writer as PW

    if not FW._BALANCE_SHEET_ITEMS <= FW._STRUCTURE_ITEMS:
        raise AcceptanceRefusal(
            f"financial_worker._BALANCE_SHEET_ITEMS 里有不属于 _STRUCTURE_ITEMS 的科目 "
            f"{sorted(FW._BALANCE_SHEET_ITEMS - FW._STRUCTURE_ITEMS)}："
            "本表只解释「已纳入的科目里哪些属于资产负债表」，不新增科目")
    double = sorted(set(FW._FORMULA_TO_TOPIC) & set(FW._STRUCTURE_ITEM_TO_TOPIC))
    if double:
        raise AcceptanceRefusal(
            f"科目/公式 code {double} 同时出现在 _FORMULA_TO_TOPIC 与 _STRUCTURE_ITEM_TO_TOPIC："
            "同一个 code 有两个归属就是歧义，出了就停，不发明归属")

    topics = set(task.topic_ids)

    def _route(code: str) -> str | None:
        """两条轴依次读；两条都不认 ⇒ `None`（由调用方落 `OUTSIDE_SECTION_TOPIC`）。"""
        metric_topic = FW._FORMULA_TO_TOPIC.get(code)
        if metric_topic is not None:
            return metric_topic
        return FW.structure_item_topic(code)

    out: list[tuple[str, str]] = []
    for fact in artifact.facts:
        topic = _route(str(getattr(fact, "code", "") or ""))
        out.append((str(fact.fact_id), topic if topic in topics
                    else PW.OUTSIDE_SECTION_TOPIC))
    for gap in getattr(artifact, "gaps", ()) or ():
        topic = _route(str(gap.get("formula_id") or ""))
        out.append((str(gap.get("fact_id") or ""),
                    topic if topic in topics else PW.OUTSIDE_SECTION_TOPIC))
    # 规范形：去重 + 升序。映射本身是**声明**，不是排序偏好，所以归一化不改变任何归属；
    # 但同一 fact 出现在多处时不得给出两个归属（那才是真正的歧义），因此先判冲突再去重。
    by_fact: dict[str, str] = {}
    for fact_id, topic_id in out:
        seen = by_fact.setdefault(fact_id, topic_id)
        if seen != topic_id:
            raise AcceptanceRefusal(
                f"财务事实 {fact_id!r} 被同时归到 {seen!r} 与 {topic_id!r}："
                "归属有歧义时不得写作（不发明归属）")
    return tuple(sorted(by_fact.items()))


# ---------------------------------------------------------------------------
# 信任根：跑前 / 跑后逐个字节比较（A6 的证据就是这两份快照）
# ---------------------------------------------------------------------------

def _trust_roots(profile) -> list[tuple[str, Path]]:
    """本次验收**只读消费**的全部信任根（真实库 / 冻结资产 / prompt 资产 / profile 本身）。"""
    from planning import demo_scope as SC
    from sections import claim_entailment_evaluator as CEE
    from sections import narrative_organizer as NO
    from sections import pack_writer as PW

    return [
        ("evidence_db", REPO / "data/evidence.db"),
        ("financial_db", REPO / "data/financial_v2.db"),
        # 真实 section store 也一并纳入：本次运行只写 run 目录内的库，这条哈希就是「没有写
        # 真实 store」的字节级证据。
        ("sections_db", REPO / "data/sections.db"),
        ("demo_scope_profile", REPO / active_profile_path()),
        ("contract_asset", REPO / profile.contract_asset),
        ("source_policy_asset", REPO / profile.source_policy_asset),
        ("writing_spec_asset", REPO / profile.writing_spec_asset),
        ("presentation_profile_asset", REPO / profile.presentation_profile_asset),
        ("writer_prompt_asset", REPO / "llm/prompts" / f"{PW.NARRATION_PROMPT_ASSET}.txt"),
        ("organizer_prompt_asset",
         REPO / "llm/prompts" / f"{NO.NARRATIVE_ORGANIZER_PROMPT_ASSET}.txt"),
        ("entailment_prompt_asset",
         REPO / "llm/prompts" / f"{CEE.CLAIM_ENTAILMENT_PROMPT_ASSET}.txt"),
    ]


def _trust_root_hashes(profile) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, path in _trust_roots(profile):
        out[name] = _sha256_file(path) if path.exists() else "<MISSING>"
    return out


# ---------------------------------------------------------------------------
# 判据（六个验收项）
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class GateOutcome:
    """一个验收项的机器结论。`status` 只有 pass / fail / refused 三个取值。

    `sub_gates`：一个门里的**多条各自可见**的判据（§八：A1 的「≥1 条合法路径 B Claim」与
    「≥1 个多 Claim 自然段落」必须分别可见，不得混成一句 detail）。子门只做**展开**：门自己的
    `status` 仍由全部子门 + 门级问题的并集决定，子门不得单独把门改判为通过。
    """

    gate_id: str
    title: str
    status: str
    detail: str
    evidence: dict
    sub_gates: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        return {"gate_id": self.gate_id, "title": self.title, "status": self.status,
                "detail": self.detail, "evidence": self.evidence,
                "sub_gates": [dict(row) for row in self.sub_gates]}


def _sub_gate(sub_gate_id: str, title: str, problems: list[str], evidence: dict) -> dict:
    """一个子门的机器结论（`status` 只有 pass / fail；判据与证据口径与门级一致）。"""
    return {"sub_gate_id": sub_gate_id, "title": title,
            "status": "fail" if problems else "pass",
            "detail": "；".join(problems) if problems else "判据成立",
            "evidence": evidence}


def _gate(gate_id: str, title: str, problems: list[str], evidence: dict,
          sub_gates: "tuple[dict, ...] | list[dict]" = ()) -> GateOutcome:
    subs = tuple(sub_gates or ())
    # 子门是门的一部分：任一子门红 ⇒ 门红。这里把子门结论并入门级 `detail`，使「绿门里藏着一个
    # 红子门」在报告顶部也能看见——门与子门不得各说各话。
    failed = [f"{s['sub_gate_id']} {s['title']}：{s['detail']}" for s in subs
              if s.get("status") != "pass"]
    all_problems = list(problems) + failed
    return GateOutcome(gate_id=gate_id, title=title,
                       status="fail" if all_problems else "pass",
                       detail="；".join(all_problems) if all_problems else "全部判据成立",
                       evidence=evidence, sub_gates=subs)


def _binding_fact_ids(section) -> dict[str, list[tuple[str, str, str]]]:
    """已定稿 Claim → 它的 factual accepted binding 的权威坐标 `(kind, container, fact_id)`。

    坐标口径只有 `NS.binding_fact_key` 一份实现——本 runner 不另立第二套。
    """
    from sections import narrative_schema as NS

    by_id = {b.accepted_support_binding_id: b for b in section.acceptance.accepted_bindings}
    out: dict[str, list[tuple[str, str, str]]] = {}
    for claim in section.claims:
        keys = []
        for binding_id in claim.accepted_binding_ids:
            binding = by_id.get(binding_id)
            key = None if binding is None else NS.binding_fact_key(binding)
            if key is not None:
                keys.append(key)
        out[claim.claim_id] = keys
    return out


def _numbers_recomputed_from_authority(authority, task, section) -> dict:
    """**独立复算**数字权威（不读门自己的结论）：Claim 里的每个数字必须逐字来自其路径 A 事实。

    授权根是权威事实**自己的文本**（`PW.scan_authority` 的读视图），不是候选文本自身——否则
    任何数字都会自证合法。这条复算是「LLM 不得计算」的可机读证据：财务正文里的每个数字都必须
    在权威事实里原样存在。
    """
    from sections import narrative_schema as NS
    from sections import pack_writer as PW

    scan = PW.scan_authority(authority, task)
    texts = {str(entry.fact_id): str(entry.text) for entry in scan.facts}
    keyed = _binding_fact_ids(section)
    checked: list[str] = []
    violations: list[str] = []
    for claim in section.claims:
        authorized: set[str] = set()
        for _kind, _container, fact_id in keyed.get(claim.claim_id, ()):
            if str(fact_id) in texts:
                authorized |= set(NS.authorized_numeric_tokens([texts[str(fact_id)]]))
        for token in NS.scan_numeric_tokens(claim.text):
            checked.append(token)
            if not NS.numeric_token_authorized(token, authorized):
                violations.append(f"{claim.claim_id}:{token}")
    return {"numbers_checked": len(checked), "unauthorized": violations[:12],
            "unauthorized_count": len(violations)}


def _path_b_requery(section, resolver, pack_set) -> dict:
    """**独立复算**路径 B：每条路径 B 的 factual accepted binding 的真实载体必须可回查。

    复算四件事，全部不依赖写入侧的自证——**验收侧自己**从 PackSet + resolver 重建材料上下文：

      1. manifest 成员必须在场，且 `payload_ref` 能被 `PayloadResolver` 解析（dangling 即失败）；
      2. 解析出的字节重算 sha256 == 成员声明的 `payload_hash`；
      3. 该成员在**验收侧重建**的正文上下文里可交叉复检（`reading_for_manifest_member`
         逐项比对 payload/locator/哈希/读视图指纹），取回**同一份真实正文**；
      4. 候选文本里的每个高风险表面 token 必须**逐字出现在那份正文里**
         （把写入侧的预验证在验收侧**再算一遍**，而不是相信它的结论）。
    """
    from sections import material_context as MC
    from sections import narrative_schema as NS
    from harness import topic_schema as TS

    manifest = section.draft.material_manifest
    problems: list[str] = []
    try:
        context = MC.resolve_writer_material_context(pack_set=pack_set, resolver=resolver,
                                                     section_id=section.section_id)
    except Exception as exc:  # noqa: BLE001 — 验收侧重建上下文失败即回查不成立
        return {"path_b_bindings_resolved": 0, "member_refs": [],
                "problems": [f"验收侧材料上下文重建失败：{type(exc).__name__}: {str(exc)[:200]}"],
                "problem_count": 1, "context_rebuilt": False}

    text_by_claim = {c.claim_id: str(c.text) for c in section.claims}
    candidate_text = {c.candidate_id: str(c.claim_text)
                      for c in section.draft.claim_candidates}

    resolved_ids: list[str] = []
    for binding in section.acceptance.accepted_bindings:
        if binding.support_semantics != "factual" \
                or binding.authorization_path != "path_b_material_derived":
            continue
        member_ref = NS.manifest_member_ref(str(binding.authority_container_id),
                                           str(binding.material_id))
        entry = manifest.entry_for(member_ref)
        if entry is None:
            problems.append(f"{binding.accepted_support_binding_id}: 不在精确材料清单内")
            continue
        resolved = TS.verify_material_payload_ref(
            TS.MaterialPayloadRef.from_dict(dict(entry.payload_ref)), resolver)
        body = resolved.payload_bytes
        if body is None or _sha256_bytes(body) != str(entry.payload_hash):
            problems.append(f"{member_ref}: 解析出的字节哈希 != 成员 payload_hash")
            continue
        resolved_ids.append(member_ref)
        # 读视图取自**验收侧**重建的上下文（经共享的唯一交叉复检实现），不是写入侧自报字段。
        reading = MC.reading_for_manifest_member(
            material_context=context, member=entry).reading_view
        # 逐条已定稿 Claim（经 candidate 回指）复算高风险表面逐字在场。
        for claim in section.claims:
            if binding.accepted_support_binding_id not in claim.accepted_binding_ids:
                continue
            text = text_by_claim.get(claim.claim_id) or candidate_text.get(
                claim.claim_candidate_id, "")
            missing = [t for t in NS.high_risk_surface_tokens(text) if t not in reading]
            if missing:
                problems.append(f"{claim.claim_id}: 高风险表面 {missing[:6]} 不在绑定材料正文里")
    return {"path_b_bindings_resolved": len(resolved_ids),
            "member_refs": sorted(set(resolved_ids))[:12],
            "problems": problems[:12], "problem_count": len(problems),
            "context_rebuilt": True}


def _multi_claim_sentences(section) -> list[dict]:
    """final Narrative 里由**多条**已接受 Claim 组织成的一句（多 Claim 自然句的最小证据）。"""
    out: list[dict] = []
    for paragraph in section.narrative.paragraphs:
        for sentence in paragraph.sentences:
            if len(sentence.claim_ids) < 2:
                continue
            out.append({"paragraph_id": paragraph.paragraph_id,
                        "sentence_id": sentence.sentence_id,
                        "sentence_kind": sentence.sentence_kind,
                        "claim_ids": list(sentence.claim_ids),
                        "text": sentence.text})
    return out


def _organization_residue(text: str, claim_texts) -> str | None:
    """runner 侧**独立**复算（§八 A1）：按声明顺序逐条剔除句子声明的 Claim 文本后的组织成分。

    * 返回 `None`：某条声明的 Claim 文本**没有**按序出现在句子里 —— 句子声称的支撑与它实际
      呈现的内容不一致（绑定与正文不一致），fail-closed；
    * 返回空串/纯标点：该句是这些 Claim 文本的**机械拼接**（`A；B。`），组织器一个字都没有
      贡献 —— 这正是 §八 A1 要拒的 raw join。

    实现与 `sections.narrative_schema` 的同口径判据**分开重写**：验收侧不得只回放写入侧自己
    的结论，否则「组织器没组织」这句话就成了写入侧自证。
    """
    body = str(text or "")
    cursor = 0
    parts: list[str] = []
    for raw in claim_texts:
        needle = str(raw or "")
        if not needle:
            return None
        found = body.find(needle, cursor)
        if found < 0:
            return None
        parts.append(body[cursor:found])
        cursor = found + len(needle)
    parts.append(body[cursor:])
    return "".join(parts)


def _natural_organization_audit(section) -> dict:
    """A1 子门 2 的**独立复算**：公司节的多 Claim 句子是不是「自然组织」。

    逐句复核（全部由产物侧重算，不读写入侧任何结论）：

      1. 句子里声明的每个 Claim 必须在本节**current** Claim 集合内（未知/陈旧 Claim 即失败）；
      2. 每条声明的 Claim 文本必须**原样、完整、按声明顺序**出现在句子里；
      3. 剔除 Claim 文本后剩下的组织成分必须含**汉字** —— 空串或纯标点就是机械拼接（raw join）；
      4. 句子不得含**句中**句末标点：`A。同时B。` 是两句首尾相接，不是一条多 Claim 自然句；
      5. 一条句子承载的 Claim 数不得超过 `NS.MAX_COMPOSED_CLAIMS_PER_SENTENCE`；
      6. 相邻 Claim 之间的接缝必须**以分隔标点起头**：`A此外，B` 是病句（判据 e，`nrules-12`），
         `A，此外，B` 才是组织。

    判据 4 与冻结判据 `ng-8` 同口径、**各自实现**（这里重写，不回放写入侧结论）：它的存在是为了
    让「多 Claim 自然句有几个」这个计数本身不被双句容器灌水。判据 6 同理对 `ng-11` 的判据 e
    ——计数不得被「黏连接缝」的句子灌水（那种句子过不了冻结门，因此也不该在这里被数成自然句）。
    """
    from sections import narrative_schema as NS

    by_id = {str(c.claim_id): str(c.text) for c in section.claims}
    rows: list[dict] = []
    for paragraph in section.narrative.paragraphs:
        for sentence in paragraph.sentences:
            ids = tuple(str(i) for i in sentence.claim_ids)
            if len(ids) < 2:
                continue
            unknown = [i for i in ids if i not in by_id]
            texts = [by_id[i] for i in ids if i in by_id]
            residue = (None if unknown
                       else _organization_residue(sentence.text, texts))
            raw_join = residue is not None and not _has_cjk(residue)
            over_bound = len(ids) > NS.MAX_COMPOSED_CLAIMS_PER_SENTENCE
            internal_terminator = _has_internal_terminator(sentence.text)
            seam_unseparated = (0 if unknown
                               else _unseparated_seam_count(sentence.text, texts))
            rows.append({
                "paragraph_id": paragraph.paragraph_id,
                "sentence_id": sentence.sentence_id,
                "sentence_kind": sentence.sentence_kind,
                "claim_ids": list(ids),
                "unknown_claim_ids": unknown,
                "claims_verbatim_in_order": residue is not None,
                "organizing_residue": residue,
                "raw_join": bool(raw_join),
                "internal_terminator": bool(internal_terminator),
                "unseparated_seams": int(seam_unseparated),
                "over_claim_bound": bool(over_bound),
                "natural": bool(residue is not None and not raw_join
                                and not internal_terminator and not over_bound
                                and not seam_unseparated),
                "text": sentence.text,
            })
    return {"sentences": rows, "multi_claim_count": len(rows),
            "natural_count": sum(1 for r in rows if r["natural"])}


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in str(text or ""))


#: 句末标点（**独立**于 `sections.narrative_schema` 的同一集合，验收侧不 import 它的常量）。
_TERMINATORS = "。；;！？!?"

#: ⑥「正文不得替系统自报检索 / 核验」的短语集（**独立**于 `sections.narrative_schema` 的
#: `SYSTEM_PROVENANCE_PHRASES`，理由同 `_TERMINATORS`：验收侧的复算不得回放写入侧的结论，
#: 否则「两边都同意」只是同一份实现的回声）。两边都取同一组字面是**判据本身**要求的，
#: 不是复制粘贴的巧合。
_SYSTEM_PROVENANCE_PHRASES = ("互联网", "联网", "检索", "抓取", "爬取", "核验", "本系统")


def _self_reported_provenance(narrative, claims) -> list[dict]:
    """正文里**替系统自报**检索 / 核验、而这些字面不在该句声明的 Claim 文本里的句子（⑥）。

    与写入侧同形：短语必须**逐字**出现在它自己声明的 Claim 文本里。年报转述的行业数据
    （材料原文写着「核验」照转不误）不是系统独立联网核验的结果，因此不得被写成那样。
    """
    texts = {str(getattr(c, "claim_id", "") or ""): str(getattr(c, "text", "") or "")
             for c in (claims or ())}
    hits: list[dict] = []
    for paragraph in getattr(narrative, "paragraphs", ()) or ():
        for sentence in getattr(paragraph, "sentences", ()) or ():
            authorized = [texts.get(str(cid), "") for cid in sentence.claim_ids]
            matched = [p for p in _SYSTEM_PROVENANCE_PHRASES
                       if p in str(sentence.text or "")
                       and not any(p in text for text in authorized)]
            if matched:
                hits.append({"sentence_id": sentence.sentence_id, "phrases": matched})
    return hits


def _has_internal_terminator(text: str) -> bool:
    """句末标点是否出现在**句中**（末尾的一串只是句读，不算）。"""
    body = str(text or "").strip()
    while body and body[-1] in _TERMINATORS:
        body = body[:-1].rstrip()
    return any(ch in _TERMINATORS for ch in body)


#: 接缝的分隔标点（**独立**于 `sections.narrative_schema.SEAM_LEAD_PUNCTUATION`，理由同
#: `_TERMINATORS`：验收侧的复算不得回放写入侧的结论）。
_SEAM_LEAD_PUNCTUATION = "，,、：:"


def _unseparated_seam_count(text: str, claim_texts) -> int:
    """runner 侧**独立**复算：相邻两条 Claim 文本之间的接缝有几个**不以分隔标点起头**。

    判据同 `nrules-12` 第 8 条判据 e：接缝是相邻两条 Claim 文本之间那段组织成分，它的第一个
    字符必须是 `_SEAM_LEAD_PUNCTUATION` 里的标点。连接语（`此外，`/`同时，`）自带一个**尾部**
    逗号，直接写在接缝开头就会读成「上一条断言 + 连接语」黏连（`…销售此外，公司…`）——它既不
    是机械拼接（组织成分里有汉字），也不是双句相接（句中没有句末标点），前几条判据都拦不住。

    定位用与 `_organization_residue` 同口径的 `find` 顺序匹配；跨度取不到时返回 `0`——那种
    句子已经在「声明文本是否按序在场」上被记为不自然，本判据不必替它下第二个结论（同一份文本
    只报**第一个**不合法之处，与写入侧的顺序一致）。
    """
    body = str(text or "")
    texts = [str(t or "") for t in (claim_texts or ())]
    if len(texts) < 2:
        return 0
    spans: list[tuple[int, int]] = []
    cursor = 0
    for needle in texts:
        if not needle:
            return 0
        found = body.find(needle, cursor)
        if found < 0:
            return 0
        spans.append((found, found + len(needle)))
        cursor = found + len(needle)
    return sum(1 for pos in range(len(spans) - 1)
               if spans[pos][1] >= spans[pos + 1][0]
               or body[spans[pos][1]] not in _SEAM_LEAD_PUNCTUATION)


def _claim_is_self_contained(text: str) -> bool:
    """Claim 文本**自带尾部句末标点**（即它本身已经是一句完整的话）。

    这样的 Claim **只能单独成句**：它的文本必须逐字在场，把它排在别的 Claim 前面就会在句子
    中间留下一个句末标点（`ng-8` 判据 d 拒），而把那个标点吞掉又会让文本不再逐字在场。
    因此替身对它的唯一合法动作就是让它自己占一句。
    """
    body = str(text or "").strip()
    return bool(body) and body[-1] in _TERMINATORS


def _claim_opens_with_neutral_connector(text: str) -> bool:
    """Claim 文本**自己就以一个中性并列连接语开头**（`此外，` / `同时，`）。

    这种 Claim 自带一句的**句首组织成分**。把它接在别的 Claim 后面、接缝里再补一个连接语，
    读者看到的就是「…，同时，此外，公司将电动化…」这种**连着两个连接语**的接缝——两个连接语
    各自都合规（都取自冻结的中性并列词表），连在一起却是机械重复，正是「不机械串接」要挡的
    形态。替身对它的合法动作只有一个：让它**自己起头**（接缝因此不落在它前面）。文本仍然逐字
    在场，一个字不改、一个判据不放宽，改的只是分句。
    """
    from sections import narrative_schema as NS

    body = str(text or "").lstrip()
    return any(body.startswith(connector) for connector in NS.NEUTRAL_CONNECTORS)


def _organizer_chunks(group: list[dict], limit: int) -> list[list[dict]]:
    """把同 topic 的 Claim 切成待组织的句子（替身的分句纪律，见 `_organizer` docstring）。

    不变量：每个 chunk 要么是**恰好一条**自带句末标点的 Claim（自己成句），要么是一段**连续
    的不带尾部标点**的 Claim，长度不超过 `limit`；**并**且，除 chunk 的首条以外，每条 Claim
    的文本都**不以**中性并列连接语开头（`_claim_opens_with_neutral_connector`）——自带句首
    连接语的 Claim 由它自己起头，接缝里因此不会出现两个连着的连接语。
    """
    chunks: list[list[dict]] = []
    run: list[dict] = []
    for claim in group:
        text = str(claim["text"])
        if _claim_is_self_contained(text):
            if run:
                chunks.append(run)
                run = []
            chunks.append([claim])
            continue
        # 自带句首连接语的 Claim 只**起头**、不被接：先收束当前 run，再让它当新 run 的第一条。
        # run 为空时无需收束（它本来就在句首，接缝不在它前面）。
        if run and _claim_opens_with_neutral_connector(text):
            chunks.append(run)
            run = []
        run.append(claim)
        if len(run) >= limit:
            chunks.append(run)
            run = []
    if run:
        chunks.append(run)
    return chunks


def _slice_audit(reading: str) -> dict:
    """逐条给出替身的**选材**结论与排除原因（诊断用，不参与任何判据）。

    A1 若因「多 Claim 自然句不可达」为红，必须能回答「语料里到底有多少条可用原子、其余为什么
    不可用」。这里把同一份选材实现（`_descriptive_slices` 用的**同一个** `_slice_atoms` 切分与
    同一套排除顺序）摊开成逐条记录：记录的是**替身的选材理由**，不是门的判据——门仍然只看被
    选中的那些原子的授权。

    逐条记录的是**原子**而不是原句：一条过长原句被 `_slice_atoms` 切开后，每个原子各自一行
    （各自被什么排除）；切不动的那一段（承接残片 / 无安全切分点）也各占一行，原因写在
    `excluded_by` 的第一条。这样「这一句 441 字为什么一条也没进来」与「它其实进来了三条、
    第四条被上限挤掉」在产物里是**两件可分辨的事**。
    """
    from sections import narrative_schema as NS

    rows: list[dict] = []
    for raw in _SLICE_SENTENCE.findall(str(reading or "")):
        sentence = (raw.strip().strip(_SLICE_TRIM).strip()
                    .rstrip(NS.SENTENCE_TERMINATORS))
        atoms, rejected = _slice_atoms(sentence)
        for text, pre_reason in [(a, "") for a in atoms] + list(rejected):
            reasons: list[str] = [pre_reason] if pre_reason else []
            if _has_selection_marker(text):
                reasons.append("勾选框/符号字形（替身选材）")
            if NS.scan_numeric_tokens(text):
                reasons.append("含数字（只能走路径 A）")
            if NS.vague_period_hits(text):
                reasons.append("未绑定期间措辞（期间门）")
            # §二：其余高风险表面按**同一份封闭集合**逐条记录（显式否定 / 勾选与适用状态 / 表格
            # 行列与合计占比 / 因果与结论连接 / 趋势结论 / 法人主体身份）。逐条列出命中成分，
            # 而不是笼统写「高风险」——A1 为红时，读者要能看到「是哪几个字挡住了它」。
            risky = tuple(t for t in NS.high_risk_surface_tokens(text)
                          if t not in tuple(NS.scan_numeric_tokens(text))
                          and t not in tuple(NS.vague_period_hits(text)))
            if risky:
                reasons.append(f"高风险表面（路径 B 不授权）{list(risky)}")
            if not any("一" <= ch <= "鿿" for ch in text):
                reasons.append("无汉字")
            rows.append({"text": text[:_SLICE_MAX], "excluded_by": reasons})
    return {"sentences": rows,
            "eligible": [r["text"] for r in rows if not r["excluded_by"]]}


def _descriptive_atom_audit(output, resolver, pack_set) -> dict:
    """精确材料清单里**可授权描述性原子**的清点（诊断用）。

    逐成员给出「该成员的正文里有哪些句、各自被什么排除、剩下几条可用」；`eligible_total`
    就是本节能提出的路径 B 原子上限。数值只描述语料，不构成判据。
    """
    from sections import material_context as MC

    try:
        context = MC.resolve_writer_material_context(
            pack_set=pack_set, resolver=resolver, section_id=output.section_id)
    except Exception as exc:  # noqa: BLE001 —— 清点失败不影响判据，如实记录
        return {"members": [], "eligible_total": 0,
                "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    by_reason: dict[str, int] = {}
    members: list[dict] = []
    eligible_total = 0
    for entry in output.draft.material_manifest.entries:
        try:
            reading = MC.reading_for_manifest_member(
                material_context=context, member=entry).reading_view
        except Exception as exc:  # noqa: BLE001
            members.append({"member_ref": entry.member_ref,
                            "error": f"{type(exc).__name__}: {str(exc)[:120]}"})
            continue
        audit = _slice_audit(reading)
        eligible_total += len(audit["eligible"])
        for row in audit["sentences"]:
            # 一条句子可能同时踩多条（勾选框前缀与「报告期」常常同句）：**逐条计数、不互斥**，
            # 这样才能看出「去掉勾选框也仍然过不了期间门」这种事。
            for reason in row["excluded_by"]:
                by_reason[reason] = by_reason.get(reason, 0) + 1
        members.append({"member_ref": entry.member_ref,
                        "chars": len(str(reading or "")),
                        "sentences": audit["sentences"][:4],
                        "eligible_count": len(audit["eligible"])})
    return {"members": members, "eligible_total": eligible_total,
            "excluded_by_reason": dict(sorted(by_reason.items()))}


#: 源头对账里逐条预览的截断长度。**只截断报告里的预览**：判读用的是内存里的全文。
_RECON_PREVIEW_CHARS = 72


def _recall_evidence_ids(row: dict) -> tuple[str, ...]:
    """一条召回读数里**父 Evidence 标识**（两种读数形状的显式兼容）。

    树检视与有界重切报的字段名不同，这是**事实**而不是笔误：树检视报「被考察的父
    Evidence」，重切报「它请求重切的 Evidence」。原先汇总处直接写 `row["evidence_ids"]`，
    于是**只要本轮真的发生过一次重切，整个 A1 源头对账就抛 `KeyError` 退化成
    `unavailable`**（读回面整层消失，报告顶部却只字不提）。这里显式取两种形状之一，
    缺字段如实返回空——兼容的是字段名，不伪造任何一条标识。
    """
    for key in ("evidence_ids", "requested_evidence_ids"):
        value = row.get(key)
        if value:
            return tuple(str(x) for x in value)
    return ()


def _a1_source_reconciliation(state: "RunState", section_id: str = "company") -> dict:
    """A1 的源头对账：Contract 需求 → 标题树召回 → 实际 span/表 → 读取轨迹 → ResearchMaterial
    → Pack manifest → Writer exact manifest（**只读诊断，不参与任何判据**）。

    为什么必须有这一层：`eligible_total == 0` 只是**替身选材策略**对当前若干份成员的诊断结果，
    它回答不了「材料到底在哪一层掉的」，更不能被读成「原始语料没有可写事实」。这条对账把七层
    摊成一份可复核的清单，逐层回答：

    1. **需求层**：本节各 topic 的冻结 Contract 投影里有哪些 aspect、各自要求什么；
    2. **导航层**：每个 aspect 由标题树导航给出了几个 node（`node_ids` 计数）；
    3. **召回层**：那一次**有界**（同一 `max_spans`/`max_chars_per_span`，未放大）树调用召回了
       几条候选、各自多少字、采信了哪一条、其余为什么没进 Pack；
    4. **读取轨迹**：本次运行留下的 trace 事件按类型计数（执行过什么、各几次）；
    5. **材料层**：Pack 里每份材料的类型 / locator / 正文字数 / 被哪些 aspect 引用；
    6. **Pack manifest**：每个 topic 的 pack 身份、材料数、各 aspect 的产出与缺口、Pack 内
       是否出现同一份正文被两条 aspect 各召回一次（重复召回）；
    7. **Writer 精确材料清单**：每个清单成员回指到哪个 `(pack_id, material_id)`、正文多少字、
       切出几句、可用原子几条、逐条排除原因。

    末尾的 `lost_at` 把「掉的层」滚成几个计数：**召回了却未被采信**（含被丢候选里已经存在合法
    描述性原子的条数）、**被采信却不在 Writer 清单里**、**在清单里却切不出句子 / 切出 0 条可用
    原子**、**整个 aspect 一份材料都没有**（未读范围）。
    """
    from sections import material_context as MC

    task = state.inputs.tasks.get(section_id)
    section = state.sections.get(section_id)
    # 只有**连 task 都没有**（没跑到这一节）才整份不可读：那时需求面与 Pack 面都无从读起。
    # 写作没成功（`section is None`）**不是**整份不可读的理由——七层里只有第七层
    # （Writer 精确材料清单）读的是写作产物，前六层是研究相位的事实，必须照常交出。
    # 真实 run r3 实证：三节全部写作失败，于是这句 early return 把「研究真的读过哪些 span、
    # 导航真的开了哪些读集、Pack 真的收了哪些材料」一并吞成一句「源头对账无从做起」，
    # 读回的人无从区分「研究没读到」与「写作没产出」。
    if task is None:
        return {"status": "unavailable", "section_id": section_id,
                "detail": "本节没有 task（本 run 未跑到这一节），源头对账无从做起："
                          "需求层与 Pack 层都没有对象"}
    try:
        return _reconcile_company_sources(state, section_id, task, section, MC)
    except Exception as exc:  # noqa: BLE001 —— 诊断失败不影响判据，如实记录
        return {"status": "unavailable", "section_id": section_id,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def _material_attribution(*, recall: list[dict], tree_navigation: list[dict],
                          material_aspects: dict[str, list[str]],
                          aspect_topic: dict[str, str]) -> dict:
    """材料的**归属依据**（只读派生；不改身份、不删成员、不进任何判据）。

    为什么必须有这一层：r6 里挂在 `company_identity_basic.founded_date` 下的四份股东/实控人勾选
    材料**证明不了成立日期**，但它们确实以「这一 aspect 读到的材料」的身份出现在
    `aspect_results[].material_ids` 里。这不是采信写错了，是**归属机制**没被写出来：那条 aspect
    的标题树导航三条事件全是 `status=fallback`、零候选、零读节点、连一次树调用都没发过
    ⇒ `aspect_materials` 为空 ⇒ **有界 Evidence 重切**（`_bounded_evidence_recut`）补位，把重切
    出来的材料记成该 aspect 的材料。只看 `material_ids` 会把「有界 fallback 兜底读到的」读成
    「这一栏目按标题树真的读到了」——两者不是一件事，而这条差别在过去**在产物里读不出来**。

    这一层只做一件事：把每条归属的依据从**已经发过的真事件**里派生出来，逐条留档。依据的来源
    恰好两个（调用方已解析的层 2/3 读数）：`TREE_TOOL_RESULT`（该 aspect 的树调用）与
    `EVIDENCE_FALLBACK_RECUT`（该 aspect 的兜底重切，载荷里有 `recut_material_ids`）。不新增
    持久化边界、不重算研究相位、不改任何 `ResearchMaterial` 字段（它没有、也不该有「读法」
    字段）。派生规则名逐行留档（`rules`），读者可以不信结论、只信事件。

    参数只吃**已经解析过的读数**（不重新遍历 trace、不碰 Pack），因此它可以在夹具上直接驱动：
    正反例不必造一个真 RunState。
    """
    recut_by_aspect: dict[str, dict[str, dict]] = {}
    inspected_aspects: set[str] = set()
    for row in recall:
        aspect_id = str(row.get("aspect_id") or "")
        if row.get("layer") == "evidence_recut":
            bucket = recut_by_aspect.setdefault(aspect_id, {})
            for material_id in tuple(row.get("recut_material_ids") or ()):
                bucket[str(material_id)] = row
        elif row.get("layer") == "tree_inspect":
            inspected_aspects.add(aspect_id)

    attribution_by_material: dict[str, dict] = {}
    attribution_by_aspect: dict[str, dict] = {}
    unattributed_refs: list[str] = []
    for material_id in sorted(material_aspects):
        for aspect_id in sorted(set(material_aspects[material_id])):
            recut_row = recut_by_aspect.get(aspect_id, {}).get(material_id)
            if recut_row is not None:
                # 该材料正是这次兜底重切交出来的：依据取自载荷里的 id 成员关系。
                basis = ATTRIBUTION_EVIDENCE_FALLBACK
                rule = "fallback_recut_material_id_membership"
                trigger = recut_row.get("trigger")
            elif aspect_id in inspected_aspects:
                # 该 aspect 发过树调用、且**没有**兜底重切事件（兜底只在读集为空时才发）⇒
                # 材料来自读集。这是从两条事件的联合形状推出的，规则名照写出来。
                basis = ATTRIBUTION_TREE_READ_SET
                rule = "tree_inspect_without_fallback_recut"
                trigger = None
            else:
                basis = ATTRIBUTION_UNATTRIBUTED
                rule = "no_event_accounts_for_this_attribution"
                trigger = None
            material_row = attribution_by_material.setdefault(
                material_id, {"basis": None, "rules": [], "trigger": None, "aspect_bases": {}})
            material_row["aspect_bases"][aspect_id] = basis
            material_row["rules"].append(rule)
            if material_row["basis"] is None:
                material_row["basis"] = basis
            elif material_row["basis"] != basis:
                # 同一份材料在两条 aspect 上的依据不同：报 `mixed`，不挑一个好看的。
                material_row["basis"] = "mixed"
            if trigger:
                material_row["trigger"] = trigger
            aspect_row = attribution_by_aspect.setdefault(aspect_id, {"by_basis": {}})
            aspect_row["by_basis"].setdefault(basis, []).append(material_id)
            if basis == ATTRIBUTION_UNATTRIBUTED:
                unattributed_refs.append(f"{aspect_id}/{material_id}")
    # 反向事实：兜底重切**交出来但没有被归属到任何 aspect** 的材料 id。它是「重切成功、归属
    # 没落上」的现场，与「重切失败」不是一件事，因此单列。
    recut_not_attributed = sorted(
        f"{aspect_id}/{material_id}"
        for aspect_id, bucket in recut_by_aspect.items()
        for material_id in bucket
        if aspect_id not in set(material_aspects.get(material_id, ())))
    # 导航侧逐 aspect **聚合全部** `TREE_NAVIGATION` 事件，不取最后一条：同一 aspect 一轮里会发
    # 多条（r6 实测最多 3 条：先按有效键命中若干次，最后常常还有一条 `status=fallback` 的空读）。
    # 只取最后一条会把它记成「零读节点」——那正是把「导航真的开过读集」误报成「导航什么都没读」
    # 的反向错误，与这一层要纠正的误读同源。
    nav_rows_by_aspect: dict[str, list[dict]] = {}
    for row in tree_navigation:
        nav_rows_by_aspect.setdefault(str(row.get("aspect_id") or ""), []).append(row)
    aspect_attribution: list[dict] = []
    for aspect_id in sorted(attribution_by_aspect):
        by_basis = attribution_by_aspect[aspect_id]["by_basis"]
        counts = {basis: len(ids) for basis, ids in sorted(by_basis.items())}
        total = sum(counts.values())
        fallback_only = (counts.get(ATTRIBUTION_EVIDENCE_FALLBACK, 0) == total
                         and total > 0 and ATTRIBUTION_TREE_READ_SET not in counts)
        nav_rows = nav_rows_by_aspect.get(aspect_id, [])
        aspect_attribution.append({
            "aspect_id": aspect_id,
            "topic_id": aspect_topic.get(aspect_id),
            "material_count": total,
            "by_basis": counts,
            # 导航侧逐项与归属依据**并列**，读者可以自己对账「读集为空 + 材料全来自兜底」。
            "nav_event_count": len(nav_rows),
            "nav_read_node_total": sum(int(r.get("read_node_count") or 0) for r in nav_rows),
            "nav_selected_events": sum(1 for r in nav_rows if r.get("selected_node_id")),
            "nav_fallback_events": sum(1 for r in nav_rows
                                       if str(r.get("status")) == "fallback"),
            "only_fallback_recut": fallback_only,
            "material_ids": [m for m in sorted(material_aspects)
                             if aspect_id in set(material_aspects[m])],
        })
    return {
        "schema_version": MATERIAL_ATTRIBUTION_SCHEMA_VERSION,
        "rule_version": MATERIAL_ATTRIBUTION_RULE_VERSION,
        # 依据的**全部**来源：两条真事件。写出来读者才能自己回到轨迹里核。
        "source_events": ["TREE_TOOL_RESULT", "EVIDENCE_FALLBACK_RECUT"],
        "by_material_id": {k: {"basis": v["basis"], "trigger": v["trigger"],
                               "rules": sorted(set(v["rules"])),
                               "aspect_bases": dict(sorted(v["aspect_bases"].items()))}
                           for k, v in sorted(attribution_by_material.items())},
        "by_aspect": aspect_attribution,
        # 这两项是**本节最该被一眼看到**的两个结论面：哪些 aspect 的材料**全部**来自兜底重切
        # （⇒ 该栏目的标题树导航没有交出材料），以及哪些归属两条事件都解释不了。
        "aspects_only_on_fallback_recut": [row["aspect_id"] for row in aspect_attribution
                                           if row["only_fallback_recut"]],
        "unattributed_refs": sorted(unattributed_refs),
        "recut_materials_not_attributed": recut_not_attributed,
        "note": ("本层是**只读派生**：依据逐条取自 `TREE_TOOL_RESULT` / `EVIDENCE_FALLBACK_RECUT` "
                 "两条真事件（层 2/3 已解析），材料本身既不重命名也不删除、`material_ids` 一个字"
                 "都不改。它纠正的是**栏目归属**的读法：依据为 "
                 f"`{ATTRIBUTION_EVIDENCE_FALLBACK}` 的材料之所以被记成「这一 aspect 读到的材料」，"
                 "是因为该 aspect 自己的读集**什么都没交出来**、兜底重切才补位；这不等于该栏目"
                 "按标题树真的读到了，也不把该材料升格成该栏目的支撑。`"
                 f"{ATTRIBUTION_UNATTRIBUTED}` 是**如实登记未知**（两条事件都解释不了这次归属），"
                 "不是「干净」；`aspects_only_on_fallback_recut` 里的 aspect，其栏目覆盖仍然"
                 "**未达成**，按既有 completion rules 该发 gap 的发 gap。"),
    }


def _reconcile_company_sources(state: "RunState", section_id: str, task, section, MC) -> dict:
    """`_a1_source_reconciliation` 的实现体（把导入面留在上一层，便于逐层排错）。"""
    from harness import topic_schema as TS

    authority = state.authority_of(section_id)
    pack_set = getattr(authority, "pack_set", None)
    context = None
    context_error = ""
    try:
        context = MC.resolve_writer_material_context(
            pack_set=pack_set, resolver=state.inputs.resolver, section_id=section_id)
    except Exception as exc:  # noqa: BLE001
        context_error = f"{type(exc).__name__}: {str(exc)[:160]}"

    # ---- 层 1：冻结 Contract 投影（需求面）----------------------------------
    topic_ids = tuple(str(t) for t in (task.topic_ids or ()))
    requirements: list[dict] = []
    for topic_id in topic_ids:
        requirement = state.inputs.requirements.get(topic_id)
        if requirement is None:
            requirements.append({"topic_id": topic_id, "missing_requirement": True})
            continue
        aspects = tuple(getattr(requirement, "aspects", ()) or ())
        requirements.append({
            "topic_id": topic_id,
            "question_ids": [str(q) for q in tuple(requirement.question_ids or ())],
            "aspect_count": len(aspects),
            "aspects": [{"aspect_id": str(a.aspect_id), "question_id": str(a.question_id),
                         "kind": str(a.kind), "content_role": str(a.content_role),
                         "output_destination": str(a.output_destination),
                         "missing_policy": str(a.missing_policy),
                         "time_scope": str(a.time_scope),
                         "requirement_text": str(a.requirement_text)[:_RECON_PREVIEW_CHARS]}
                        for a in aspects],
        })

    # ---- 层 2/3：导航 node 与**有界**树调用的召回/采信 ----------------------
    # §二 1：这一层现在从**真实轨迹事件**与 **Pack 自己的 gap 记录**回读，不再读任何研究替身
    # 的私有记录。理由是可核性：替身的 `calls` 只证明「替身被调用过」，轨迹事件证明的是
    # 「正式运行时真的向真 Registry 发过哪一次调用、拿到什么」。因此这里只有三个来源：
    #
    #   * `TREE_TOOL_RESULT`      —— 树导航→有界候选 node 的那次真工具调用；
    #   * `EVIDENCE_FALLBACK_RECUT` —— 有界 Evidence 重切（§七 fallback）那次真工具调用；
    #   * `FOLLOW_UP_OUTCOME` / `FOLLOW_UP_NO_NEW_EVIDENCE` —— focused follow-up 的真实收束。
    #
    # 「材料在哪一层掉的」还多一个**权威**来源：Pack 自己的 `unresolved`（`tree_material_gaps`
    # / `tree_material_bounded_out`），它逐 span 记了原因（`unassigned_or_fallback_span` /
    # `evidence_has_no_qualified_span` / `over_max_spans` / `over_max_chars_per_span` …）。
    # 这两边合起来才回答得了「是语料没有，还是上界/资格把它挡下了」。
    events = tuple(getattr(getattr(state.inputs, "trace_sink", None), "events", ()) or ())
    aspect_topic = {str(a["aspect_id"]): str(row["topic_id"])
                    for row in requirements for a in row.get("aspects", ())}
    recall: list[dict] = []
    follow_up: list[dict] = []
    #: §二 2.3：**内容处置**逐条留痕（原文摘录 + span/evidence 级 locator + typed 原因）。
    #: 它们既不是材料也不是 gap——`not_used` 不是 gap，把它们并进 gap 会让读回的人以为重切失败。
    #: 因此单列一表，逐条可查「哪个候选、什么形态、为什么不作材料、原文/定位在哪」。
    content_dispositions: list[dict] = []
    for event in events:
        event_type = str(event.get("type") or "")
        payload = dict(event.get("payload") or {})
        aspect_id = str(payload.get("aspect_id") or "")
        topic_id = aspect_topic.get(aspect_id)
        if topic_id is None:
            continue
        if event_type == "TREE_TOOL_RESULT":
            recall.append({
                "topic_id": topic_id, "aspect_id": aspect_id, "layer": "tree_inspect",
                "status": str(payload.get("status") or ""),
                "error_code": payload.get("error_code"),
                # 检索轨迹只记 id（不含文本）：可核的是「触达过哪些父 Evidence」。
                "evidence_ids": [str(x) for x in (payload.get("evidence_ids") or ())],
                "candidate_count": int(payload.get("candidate_count") or 0),
                # §二 2.3：候选的三条**互斥**去路（材料 / 结构 gap / 内容处置）各自的数量必须
                # 一起回读。只报 `candidate_count` 会把「结构上切不出来」与「切出来了但内容
                # 不合格」混成一句"没进材料"，读回的人分不清该修上界还是该修内容资格。
                "gap_count": int(payload.get("gap_count") or 0),
                "content_disposition_count": int(
                    payload.get("content_disposition_count") or 0),
                "tool_calls": int(payload.get("tool_calls") or 0)})
        elif event_type == "EVIDENCE_FALLBACK_RECUT":
            recall.append({
                "topic_id": topic_id, "aspect_id": aspect_id, "layer": "evidence_recut",
                "trigger": payload.get("trigger"),
                "upstream_need_id": payload.get("upstream_need_id"),
                "status": str(payload.get("status") or ""),
                "error_code": payload.get("error_code"),
                "requested_evidence_ids": [str(x) for x in
                                           (payload.get("requested_evidence_ids") or ())],
                "recut_material_ids": [str(x) for x in
                                       (payload.get("recut_material_ids") or ())],
                "recut_material_count": int(payload.get("recut_material_count") or 0),
                "gap_count": int(payload.get("gap_count") or 0),
                "skipped_count": int(payload.get("skipped_count") or 0),
                "content_disposition_count": int(
                    payload.get("content_disposition_count") or 0),
                "tool_calls": int(payload.get("tool_calls") or 0)})
        elif event_type == "FOLLOW_UP_OUTCOME":
            follow_up.append({
                "topic_id": topic_id, "aspect_id": aspect_id,
                "need_id": str(payload.get("need_id") or ""),
                "completion_status": payload.get("completion_status"),
                "stop_reason": payload.get("stop_reason")})
        elif event_type == "FOLLOW_UP_NO_NEW_EVIDENCE":
            follow_up.append({
                "topic_id": topic_id, "aspect_id": aspect_id,
                "need_id": str(payload.get("need_id") or ""), "no_new_evidence": True,
                "cited_evidence": [str(x) for x in (payload.get("cited_evidence") or ())]})
        elif event_type == "TREE_CONTENT_DISPOSITION":
            # 与候选**人口**判定无关：这些候选结构上切得出来，只是内容形态不合格。
            # 逐条原样回读（含 typed 原因与 locator），不重新解释、不并入 gap。
            content_dispositions.append({
                "topic_id": topic_id, "aspect_id": aspect_id,
                "via": payload.get("via"),
                **{k: v for k, v in payload.items()
                   if k not in ("aspect_id", "via")}})

    # ---- 层 2b：标题树导航的**有界读集**（键层级 / 候选依据 / 舍弃原因 / 读根 / 读集 / 未读范围）
    # 逐条只读回读本节各 topic 的研究相位真实发过的 `TREE_NAVIGATION`：这条 aspect 用的是**哪两层
    # 声明键**（本层 Contract 声明键 / 祖先层 Contract 祖先派生的键）、实际生效的是哪一层、哪条候选
    # 命中、凭分数与命中面被选中、其余为什么被舍弃、读根上提到哪个祖先、读集有多大、被 node 预算
    # 截掉多少。把键层级一并平列出来，是因为「读到了父节点正文」这句话在报告里必须能自证是
    # **按哪一层的键**定位的：只看读根节点号，读者无法区分「本层精确命中」与「祖先层回退命中」。
    # 「导航有没有真的把正文交出来」只有在**产物里**逐层对得上号才算数，所以这里写的是回读，
    # 不是叙述；它不参与任何判据（`key_tier` 取自事件载荷，不在这里重算）。
    aspect_topic = {str(a["aspect_id"]): str(row["topic_id"])
                    for row in requirements for a in row.get("aspects", ())}
    tree_navigation: list[dict] = []
    for event in events:
        if str(event.get("type")) != "TREE_NAVIGATION":
            continue
        payload = dict(event.get("payload") or {})
        aspect_id = str(payload.get("aspect_id") or "")
        if aspect_id not in aspect_topic:
            continue
        band = [str(x) for x in (payload.get("band_node_ids") or ())]
        read_roots = [str(x) for x in (payload.get("read_root_node_ids") or ())]
        tree_navigation.append({
            "topic_id": aspect_topic[aspect_id], "aspect_id": aspect_id,
            # 键层级（`anp-4`）：本层声明键 / 祖先声明键 / 实际生效的那一层 / 规则版本。四者都取自
            # 事件载荷原样回读；缺字段即空，不在报告侧补算、不据此判通过。
            "nav_keys": [str(x) for x in (payload.get("nav_keys") or ())],
            "parent_keys": [str(x) for x in (payload.get("parent_keys") or ())],
            "key_tier": payload.get("key_tier"),
            "rule_version": payload.get("rule_version"),
            "status": str(payload.get("status") or ""),
            "fallback_reason": payload.get("fallback_reason"),
            "selected_node_id": payload.get("selected_node_id"),
            "band_node_ids": band,
            "read_root_node_ids": read_roots,
            # 读根本身不在近分带里 ⇒ 上提到祖先后才开读的（「挂在父章节上的正文」正是这样回来的）。
            "climbed": bool([n for n in read_roots if n not in band]),
            "read_node_count": len(tuple(payload.get("read_node_ids") or ())),
            "unread_node_ids": [str(x) for x in (payload.get("unread_node_ids") or ())],
            "unread_total": int(payload.get("unread_total") or 0),
            "candidates": [{"node_id": str(c.get("node_id") or ""),
                            "title": str(c.get("title") or "")[:_RECON_PREVIEW_CHARS],
                            "score": c.get("score"),
                            "in_read_set": c.get("in_read_set"),
                            "discard_reason": c.get("discard_reason")}
                           for c in tuple(payload.get("candidates") or ())[:5]],
        })
    nav_read_scope = {
        "event_count": len(tree_navigation),
        "event_aspects": sorted({row["aspect_id"] for row in tree_navigation}),
        # 按**生效键层级**分组：祖先层生效的 aspect 就是「本层无完整标签段命中、沿真实标题树
        # 回退到 Contract 派生的祖先声明键」的那批。这是回退是否真的被用上的唯一读数面。
        "declared_tier_aspects": sorted({row["aspect_id"] for row in tree_navigation
                                         if row["key_tier"] == "declared"}),
        "ancestor_tier_aspects": sorted({row["aspect_id"] for row in tree_navigation
                                         if row["key_tier"] == "ancestor"}),
        "selected_aspects": sorted({row["aspect_id"] for row in tree_navigation
                                    if row["selected_node_id"]}),
        "unselected_aspects": sorted({row["aspect_id"] for row in tree_navigation
                                      if not row["selected_node_id"]}),
        "climbed_aspects": sorted({row["aspect_id"] for row in tree_navigation
                                   if row["climbed"]}),
        "unread_aspects": sorted({row["aspect_id"] for row in tree_navigation
                                  if row["unread_total"]}),
        "unread_total": sum(row["unread_total"] for row in tree_navigation),
        "read_node_total": sum(row["read_node_count"] for row in tree_navigation),
    }

    # ---- 层 4：读取轨迹（按类型计数，不抄 payload）--------------------------
    by_type: dict[str, int] = {}
    for event in events:
        key = str(event.get("type") or "?")
        by_type[key] = by_type.get(key, 0) + 1
    trace = {"event_count": len(events), "by_type": dict(sorted(by_type.items()))}

    # ---- 层 5/6：Pack 材料与 Pack manifest ---------------------------------
    material_chars: dict[str, int] = {}
    material_aspects: dict[str, list[str]] = {}
    packs: list[dict] = []
    for topic_id in topic_ids:
        try:
            pack = pack_set.pack_for(topic_id)
        except Exception as exc:  # noqa: BLE001
            packs.append({"topic_id": topic_id, "missing_pack": f"{type(exc).__name__}"})
            continue
        for material in tuple(pack.materials or ()):
            material_id = str(material.material_id)
            material_chars[material_id] = _material_text_chars(material, state.inputs.resolver)
        for result in tuple(pack.aspect_results or ()):
            for material_id in tuple(result.material_ids or ()):
                material_aspects.setdefault(str(material_id), []).append(str(result.aspect_id))
        seen_hashes: dict[str, list[str]] = {}
        for material in tuple(pack.materials or ()):
            seen_hashes.setdefault(str(material.content_hash), []).append(
                str(material.material_id))
        # 逐条回读 Pack 的 `unresolved`：#「材料在哪一层掉的」最有权威的一份记录。`detail`
        # 里逐 span 写了**该层自己的**原因（`unassigned_or_fallback_span` /
        # `evidence_has_no_qualified_span` / `over_max_spans` / `over_max_chars_per_span` …），
        # 顶层 `reason_code` 只说明是哪一类 gap。两者都记下来，不合并成一句。
        pack_gaps: list[dict] = []
        for gap in tuple(pack.unresolved or ()):
            detail = str(getattr(gap, "detail", "") or "")
            pack_gaps.append({
                "aspect_ids": [str(x) for x in tuple(getattr(gap, "aspect_ids", ()) or ())],
                "reason_code": str(getattr(gap, "reason_code", "") or ""),
                "layer_reason": detail.rsplit(": ", 1)[-1] if ": " in detail else "",
                "detail": detail[:_RECON_PREVIEW_CHARS],
                "blocking": bool(getattr(gap, "blocking", False))})
        packs.append({
            "topic_id": topic_id, "pack_id": str(pack.pack_id),
            "materials": len(tuple(pack.materials or ())),
            "facts": len(tuple(pack.facts or ())),
            "gaps": len(tuple(pack.unresolved or ())),
            "conflicts": len(tuple(pack.conflicts or ())),
            "not_found_audits": len(tuple(pack.not_found_audits or ())),
            "duplicate_content_hashes": {h: ids for h, ids in sorted(seen_hashes.items())
                                         if len(ids) > 1},
            "gap_rows": pack_gaps,
            "gap_reason_histogram": _count_by(pack_gaps, "reason_code"),
            "gap_layer_reason_histogram": _count_by(
                [row for row in pack_gaps if row["layer_reason"]], "layer_reason"),
            "aspects": [{"aspect_id": str(r.aspect_id), "status": str(r.status),
                         "materials": len(tuple(r.material_ids or ())),
                         "facts": len(tuple(r.supported_fact_ids or ())),
                         "unresolved": len(tuple(r.unresolved_ids or ())),
                         "not_found_audit": r.not_found_audit_id}
                        for r in tuple(pack.aspect_results or ())],
        })

    # ---- 层 5b：材料的**归属依据**（只读派生；不改身份、不删成员、不进任何判据）------
    material_attribution = _material_attribution(
        recall=recall, tree_navigation=tree_navigation,
        material_aspects=material_aspects, aspect_topic=aspect_topic)

    # ---- 层 7：Writer 精确材料清单 -----------------------------------------

    # ---- 层 7：Writer 精确材料清单 -----------------------------------------
    # 这是七层里**唯一**读写作产物的一层：本节没产出时它读不回，如实标不可判定；
    # 前六层不因此受影响（它们在研究相位就已经定下来）。
    writer_entries_status = "read_back_ok"
    writer_entries_error = ""
    entries: tuple = ()
    # 三种读不回的现场必须**分开说**，因为它们是三件事：
    #   * 产物里没有这一节的记录（`state.sections` 里没有它）；
    #   * 有节记录但没有 `SectionDraft`；
    #   * 有 `SectionDraft` 但清单本身取不到。
    # 三者的共同点是「本节没有产出」，但原因不同。旧文案在第二种现场写「本节**有**
    # `SectionDraft`」——它读的是 `section is not None`，而 `draft` 是 `None`，于是把
    # 「没有产出」说成了「有产出但清单丢了」，原因被吞掉。
    # 注意**不**由「`sections` 里有没有这条记录」去推断「这一节有没有跑过」：正式 runner 里
    # 写作失败的节恰恰**不写**节记录（`_run_writer_sections` 在异常分支 `continue`），所以
    # 「没有记录」既可能是没跑到，也可能是跑失败了。这里只陈述**产物里看得见**的事实，
    # 并把本节错误原样附上，让读回的人自己分辨。
    writer_draft = getattr(section, "draft", None) if section is not None else None
    if writer_draft is None:
        writer_entries_status = "not_available"
        section_error_text = str(
            (getattr(state, "section_errors", None) or {}).get(section_id, "") or "")
        writer_entries_error = (
            ("本节没有产出：产物里没有这一节的节记录，无 SectionDraft"
             if section is None else
             "本节没有产出：有节记录但无 SectionDraft（写作失败）")
            + "，Writer 精确材料清单不可读回"
            + (f"（本节错误：{section_error_text[:120]}）" if section_error_text else ""))
    else:
        manifest = getattr(writer_draft, "material_manifest", None)
        if manifest is None:
            writer_entries_status = "not_available"
            writer_entries_error = "本节有 SectionDraft 但材料清单不可得"
        else:
            entries = tuple(manifest.entries)
    writer_side_read = writer_entries_status == "read_back_ok"

    def _writer_side(value):
        """Writer 侧读不回时记 `None`（**不是**空容器：`[]` 会被读成「确实一份都没有」）。"""
        return value if writer_side_read else None

    writer_manifest: list[dict] = []
    for entry in entries:
        reading = ""
        if context is not None:
            try:
                reading = str(MC.reading_for_manifest_member(
                    material_context=context, member=entry).reading_view or "")
            except Exception:  # noqa: BLE001
                reading = ""
        audit = _slice_audit(reading)
        writer_manifest.append({
            "member_ref": str(entry.member_ref), "pack_id": str(entry.pack_id),
            "material_id": str(entry.material_id),
            "chars": len(reading), "sentences": len(audit["sentences"]),
            "eligible": len(audit["eligible"]),
            "excluded_by": sorted({r for row in audit["sentences"]
                                   for r in row["excluded_by"]})})

    # ---- 汇总：材料在哪一层掉的 --------------------------------------------
    # Pack 侧取重放出来的**身份键** `(topic_id, material_id)`（`material_id` 全局唯一，
    # 但键里带 topic 才能表达「哪一节/哪个主题的 Pack 里有它」）；Writer 清单侧只有容器
    # 身份 `pack_id`，所以用 Pack 反查 topic，再逐条核对「清单成员真的在这个 Pack 里」。
    pack_id_topic = {str(pack["pack_id"]): str(pack["topic_id"])
                     for pack in packs if "missing_pack" not in pack}
    material_keys_in_packs: set[tuple[str, str]] = set()
    for topic_id in topic_ids:
        try:
            real = pack_set.pack_for(topic_id)
        except Exception:  # noqa: BLE001
            continue
        for material in tuple(real.materials or ()):
            material_keys_in_packs.add((topic_id, str(material.material_id)))

    manifest_in_pack = [
        {"member_ref": row["member_ref"], "pack_id": row["pack_id"],
         "topic_id": pack_id_topic.get(row["pack_id"], ""),
         "material_id": row["material_id"],
         "in_pack": (pack_id_topic.get(row["pack_id"], ""), row["material_id"])
         in material_keys_in_packs}
        for row in writer_manifest]
    manifest_keys = [(row["topic_id"], row["material_id"]) for row in manifest_in_pack]

    aspects_with_material = {(str(pack["topic_id"]), str(row["aspect_id"]))
                             for pack in packs for row in pack.get("aspects", ())
                             if row["materials"]}
    aspects_without_material = [
        {"topic_id": row["topic_id"], "aspect_id": aspect["aspect_id"]}
        for row in requirements for aspect in row.get("aspects", ())
        if (row["topic_id"], aspect["aspect_id"]) not in aspects_with_material]

    # 逐 topic 的**原始计数**（真工具调用 / 真召回到的候选父 Evidence / Pack 里的材料与缺口）。
    # 这里只并列计数，不断言「一条候选必然派生一条材料」——那是研究运行时自己的规则，对账不替
    # 它下结论。
    per_topic: list[dict] = []
    for topic_id in topic_ids:
        rows = [row for row in recall if row["topic_id"] == topic_id]
        inspect_rows = [row for row in rows if row["layer"] == "tree_inspect"]
        recut_rows = [row for row in rows if row["layer"] == "evidence_recut"]
        pack_row = next((p for p in packs if str(p.get("topic_id")) == topic_id), {})
        per_topic.append({
            "topic_id": topic_id,
            "tree_inspect_calls": len(inspect_rows),
            "evidence_recut_calls": len(recut_rows),
            "recall_calls": len(rows),
            "candidate_evidence_ids": len({e for row in inspect_rows
                                           for e in row["evidence_ids"]}),
            "recalled_candidates": sum(row["candidate_count"] for row in inspect_rows),
            "recut_evidence_ids": len({e for row in recut_rows
                                       for e in row["requested_evidence_ids"]}),
            "recut_materials": sum(row["recut_material_count"] for row in recut_rows),
            "tool_gaps": sum(row["gap_count"] for row in recut_rows),
            "tool_skipped": sum(row["skipped_count"] for row in recut_rows),
            "follow_up_attempts": sum(1 for row in follow_up
                                      if row["topic_id"] == topic_id),
            "pack_materials": int(pack_row.get("materials") or 0),
            "pack_facts": int(pack_row.get("facts") or 0),
            "pack_gaps": int(pack_row.get("gaps") or 0),
        })

    # 本节**上界**（原样读取，证明「没有靠放大上界换材料」）：取自本节的真实研究预算政策。
    section_context = state.inputs.run_contexts.get(section_id)
    section_budget = getattr(section_context, "budget_policy", None)
    bounds_used = {
        "source": "run_context.budget_policy（本节正式研究预算政策）",
        "max_spans_per_aspect": getattr(section_budget, "tree_max_spans_per_aspect", None),
        "max_chars_per_span": getattr(section_budget, "tree_max_chars_per_span", None),
        "max_need_rounds_per_aspect": getattr(section_budget, "max_need_rounds_per_aspect", None),
        "max_llm_calls_per_topic": getattr(section_budget, "max_llm_calls_per_topic", None),
        "max_tool_calls_per_topic": getattr(section_budget, "max_tool_calls_per_topic", None),
        # 工具轴的**逐项推导**（`m930-3-tool-axis-1`）：旧口径 `3 × aspects` 被本项替代，
        # 原因是它在任何跨源 topic 上都低于「初次必读」的结构下沿（详见常量处与
        # `_research_derivation`）。这里原样登记本轮的推导读数，供读者核「是否靠放大上界换材料」。
        "tool_axis": dict(getattr(state, "research_tool_budget_evidence", None) or {
            "status": "unavailable",
            "detail": "本轮没有登记工具轴推导读数（不得读成「未放大」或「已放大」）",
        }),
        # **如实登记一条未生效的配置**（不是"已生效但没用满"）：`max_need_rounds_per_topic`
        # 只在 `ResearchBudgetPolicy` 里声明与校验，全仓**没有任何地方读取它**。把它读成
        # 「每个 topic 最多两轮 need」是错的——Contract 一个 topic 有多个 aspect，首轮广覆盖
        # 就可能超过两次。本 topic 真正生效的有限 follow-up 门是：每 aspect 轮次
        # （`max_need_rounds_per_aspect`，经真实 `MaxRounds` 生效）+ topic 级预算
        # （`max_llm_calls_per_topic` / `max_tool_calls_per_topic` / token / elapsed，由
        # `can_afford` 事前预留 + 事后 `charge_need` 复核）。它**不**构成本批的裁决项，
        # 也不得被当作「已生效的波次上界」引用。
        "max_need_rounds_per_topic": {
            "value": getattr(section_budget, "max_need_rounds_per_topic", None),
            "in_effect": False,
            "note": ("声明并校验，但全仓无读取点（死配置）：**不得**读成「每个 topic 最多两轮 "
                     "need」。实际生效的有限门是每 aspect 轮次 + topic 级预算（见本块其他键）"),
        },
    }

    pack_gap_rows = [row for pack in packs for row in pack.get("gap_rows", ())]
    lost_at = {
        "tree_inspect_calls": sum(1 for row in recall if row["layer"] == "tree_inspect"),
        "evidence_recut_calls": sum(1 for row in recall if row["layer"] == "evidence_recut"),
        "recall_calls": len(recall),
        "recalled_candidates": sum(row["candidate_count"] for row in recall
                                   if row["layer"] == "tree_inspect"),
        "candidate_evidence_total": len({e for row in recall
                                         for e in _recall_evidence_ids(row)}),
        "recut_material_total": sum(row["recut_material_count"] for row in recall
                                    if row["layer"] == "evidence_recut"),
        "tool_gaps_total": sum(row["gap_count"] for row in recall
                               if row["layer"] == "evidence_recut"),
        "tool_skipped_total": sum(row["skipped_count"] for row in recall
                                  if row["layer"] == "evidence_recut"),
        "tool_skipped_reasons": _count_by(
            [row for row in recall if row["layer"] == "tree_inspect"],
            "status"),
        "follow_up_attempts": len(follow_up),
        "follow_up_no_new_evidence": sum(1 for row in follow_up
                                         if row.get("no_new_evidence")),
        "follow_up_cited_evidence_total": len({e for row in follow_up
                                               for e in row.get("cited_evidence", ())}),
        "bounds_used": bounds_used,
        # 「材料是在哪一层掉的」的**权威**答案：Pack 自己的逐条 gap。两个直方图缺一不可——
        # 顶层 `reason_code` 只说类别，`layer_reason` 才是「上界截断 / 资格不通过」的现场。
        "pack_gap_reason_histogram": _count_by(pack_gap_rows, "reason_code"),
        "pack_gap_layer_reason_histogram": _count_by(
            [row for row in pack_gap_rows if row["layer_reason"]], "layer_reason"),
        # 这一项**不再可算**，如实登记而不是悄悄删掉：旧值来自研究替身把丢弃 span 的原文抄进
        # 内存（`research_recall[...].discarded[*].eligible_atoms`）。那条旁路已按 §二 1 删除，
        # 正式轨迹与 Pack 里都**不含**被丢弃 span 的正文，因此「被丢的 span 里有没有合法描述性
        # 原子」在现行产物里无从核验。要恢复它必须新增一份「被丢 span 的正文」审计面——那是
        # 新的持久化边界，不在本次授权内，故留空并说明。
        "discarded_with_eligible_atoms": [],
        # Pack → Writer 的**精确集合差**：两边各自多出来的那些（不解释成因，只列事实）。
        # 以下五项**全部**读 Writer 清单：本节写作没成功时它们是 `None` + 一条 typed 原因，
        # 不是空列表——空列表在这里会被读成「一份都没掉」，而真实情况是「这一层没读回」。
        "writer_side_status": writer_entries_status,
        "writer_side_error": writer_entries_error,
        "pack_materials_missing_from_writer_manifest": _writer_side([
            f"{topic_id}/{material_id}"
            for topic_id, material_id in sorted(material_keys_in_packs)
            if (topic_id, material_id) not in manifest_keys]),
        "writer_manifest_members_not_in_pack": _writer_side([
            row for row in manifest_in_pack if not row["in_pack"]]),
        "writer_members_without_sentence": _writer_side([
            row["member_ref"] for row in writer_manifest if row["sentences"] == 0]),
        "writer_members_zero_eligible": _writer_side([
            row["member_ref"] for row in writer_manifest if row["eligible"] == 0]),
        "writer_member_excluded_by_reason": _writer_side(
            _reason_histogram(writer_manifest)),
        "aspects_without_any_material": aspects_without_material,
        "aspects_without_any_material_count": len(aspects_without_material),
        "per_topic": per_topic,
    }
    # 研究侧用的是真实 provider 还是确定性替身：这不是细节，读者必须一眼看到——离线 run 的
    # 「事实」是替身走正式链得到的，不能当成内容验收证据。
    research_llm = state.inputs.research_llm
    research = {
        "kind": ("real_provider" if state.mode == MODE_REAL else "offline_stub"),
        "class": type(research_llm).__name__,
        "model": getattr(research_llm, "model", None),
        "calls_recorded": len(getattr(research_llm, "calls", ()) or ()),
        "same_chain_as_real_mode": True,
        "note": ("真实模式注入 `RT.RealResearchLLM`（真实 provider）；离线模式注入本 runner 的"
                 "确定性替身。两者注入的是**同一条正式研究链**（真 Router / 真 ToolRegistry / "
                 "真预验证资格门），只有 LLM 这一层不同；离线产物不构成内容验收证据。"),
    }
    return {
        "status": "read_only_diagnostic",
        "section_id": section_id,
        "writer_material_context_error": context_error,
        "requirements": requirements,
        "recall": recall,
        "content_dispositions": content_dispositions,
        "follow_up": follow_up,
        "research_llm": research,
        "tree_navigation": tree_navigation,
        "nav_read_scope": nav_read_scope,
        "trace": trace,
        "pack_materials": {
            "chars_by_material_id": dict(sorted(material_chars.items())),
            "aspects_by_material_id": {k: sorted(v) for k, v in sorted(material_aspects.items())},
            # §二 2.3：`aspects_by_material_id` 只说「记在哪条 aspect 名下」，不说**凭什么**记在
            # 那里。归属依据与它并列交出，读回的人不必回到轨迹里才能分辨「读集交出来的」与
            # 「兜底重切补位的」。
            "attribution_by_material_id": {
                k: v["basis"] for k, v in material_attribution["by_material_id"].items()},
        },
        "material_attribution": material_attribution,
        "packs": packs,
        "writer_manifest_status": writer_entries_status,
        "writer_manifest_error": writer_entries_error,
        "writer_manifest": writer_manifest,
        "writer_manifest_members_in_pack": manifest_in_pack,
        "lost_at": lost_at,
        "note": ("本清单是**只读诊断**，不参与任何判据，也不改变任何门的期望值。它只回答"
                 "「材料从 Contract 需求（含标题树导航的有界读集 / 近分带 / 未读范围）到 "
                 "Writer 精确材料清单的逐层里，在哪一层没有被采用」，"
                 "不声明语料里有没有可写事实。召回/保留两层的读数是**真轨迹事件**"
                 "（`TREE_TOOL_RESULT` / `EVIDENCE_FALLBACK_RECUT` / `FOLLOW_UP_*`）与 Pack 自己的 "
                 "`unresolved` 记录，不读任何研究替身的私有记录。"
                 "§二 2.3 起，候选的三条**互斥**去路分开报：`recall[]` 的 `gap_count`（结构上"
                 "切不出来）与 `content_disposition_count`（切出来了但内容形态不合格），"
                 "外加逐条的 `content_dispositions[]`（typed 原因 + 原文摘录 + locator）。"
                 "内容处置**不是** gap——`not_used` 不是 gap。"
                 "**材料层现在给出归属依据**（`material_attribution`，与 "
                 "`pack_materials.attribution_by_material_id` 同源）：`material_ids` 只说材料"
                 "记在哪条 aspect 名下，不说凭什么记在那里。依据逐条从同两条真事件派生"
                 "（树调用 / 兜底重切），三种取值是 `aspect_tree_read_set` / "
                 "`evidence_fallback_recut` / `unattributed`（未知如实登记）。"
                 "`aspects_only_on_fallback_recut` 里的 aspect，其栏目覆盖仍然**未达成**——"
                 "兜底重切补位不等于该栏目被标题树读到。材料一份都没删、`material_ids` 一个字"
                 "都没改。"
                 "**七层里只有第七层读写作产物**：`writer_manifest_status=not_available` 时"
                 "只有 `writer_manifest*` 与 `lost_at` 的 Writer 侧五项不可判定（记 `None` + "
                 "`writer_side_error`，**不是**空列表），前六层（需求 / 导航 / 召回 / 轨迹 / "
                 "材料 / Pack manifest）照常交出——研究相位真的读过什么，不因写作没成功而"
                 "变成「没读过」。"),
    }


def _reason_histogram(writer_manifest: list[dict]) -> dict:
    """逐成员排除原因汇总：一条句子可能同时踩多条，**逐条计数、不互斥**（与选材实现同一口径）。"""
    out: dict[str, int] = {}
    for row in writer_manifest:
        for reason in row["excluded_by"]:
            out[reason] = out.get(reason, 0) + 1
    return dict(sorted(out.items()))


def _count_by(rows: list[dict], key: str) -> dict:
    """逐行按某个字段计数（空值归 `?`）：只计数、不互斥、不解释成因。"""
    out: dict[str, int] = {}
    for row in rows:
        token = str(row.get(key) or "?")
        out[token] = out.get(token, 0) + 1
    return dict(sorted(out.items()))


def _count_clause(label: str, value) -> str:
    """`标签 N 个` / `标签 **不可读回**（不是 0）`——Writer 侧计数的**人读渲染**。

    `_a1_source_reconciliation` 在本节没有产出时把这些计数记成 `None`——**故意**不是 `[]`/`0`：
    「这一层没读回」与「这一层确实一份都没有」是两条不同的读数。旧文案直接对它取 `len()`，
    于是失败路径（三节都没有 `SectionDraft`）在报告正文装配处抛 `TypeError`，**整份
    `acceptance_report.json` 与 `artifact_index.json` 都没写出来**——最需要报告的现场反而没有报告。
    本函数把 `None` 如实印成「不可读回（不是 0）」，绝不印成一个会被读成「确认为零」的数字。
    """
    if value is None:
        return f"{label} **不可读回**（本节没有产出：不是 0，而是没有读数）"
    return f"{label} {len(value)} 个"


def _material_text_chars(material, resolver) -> int:
    """材料 payload 里的真实正文字数（回查不到就记 -1：诊断计数不得编造）。"""
    from harness import topic_schema as TS

    try:
        resolved = TS.verify_material_payload_ref(material.payload_ref, resolver)
    except Exception:  # noqa: BLE001
        return -1
    body = resolved.payload_bytes
    if body is None:
        return -1
    try:
        return len(str(json.loads(body.decode("utf-8"))["content"]["text"] or ""))
    except Exception:  # noqa: BLE001
        return -1


def _cross_document_evidence(state: "RunState", section_id: str) -> dict:
    """跨文档四块**只读**证据（§L2/§L4；不参与任何判据）。

    四个问题在这里逐份、逐 `(aspect, 来源)` 摊开回答——它们是「注册了三份 ≠ 读了三份」
    这句话在产物里的落点：

    1. `source_set`：本轮研究**实际消费**的有序源集（成员、角色、序位、内容指纹）；
    2. `aspect_source_responsibility`：检索**前**逐对派生的责任（该份必须查 / 不必查及依据）；
    3. `per_source_aspect_outcome`：逐对四臂结果（查到了 / 查了没命中 / 要求查但没跑成 /
       不要求查），臂 C2 必须带真实未完成原因，臂 C1 必须带 typed 依据；
    4. `material_destination_by_document`：**材料去哪了**——逐份来源下，每个 `(aspect, 来源)`
       产出的材料 id 是否真的在 Pack 里、payload 能不能解析、正文多少字、是否进了 Writer
       精确材料清单。这一层回答「哪一份的材料在第几步掉的」。

    全部读自 **Pack 本体**（`store.load_current` 的 typed 回读）与本轮现场，不读任何自报字段；
    Pack 缺失或读取失败如实记 `unavailable`，**不**记成「零命中」（那是两件事）。
    """
    from harness import topic_schema as TS

    task = state.inputs.tasks.get(section_id)
    section = state.sections.get(section_id)
    if task is None:
        return {"status": "unavailable", "section_id": section_id,
                "detail": "本节没有 task，跨文档证据无从读起"}
    authority = state.authority_of(section_id)
    pack_set = getattr(authority, "pack_set", None)
    topic_ids = tuple(str(t) for t in (task.topic_ids or ()))
    if pack_set is None or not topic_ids:
        return {"status": "unavailable", "section_id": section_id,
                "detail": f"本节没有可用 Pack 集（pack_set={'有' if pack_set else '无'}，"
                          f"topic 数 {len(topic_ids)}）"}

    # Writer 精确清单侧：**只有真读过 draft 的材料清单才算查过**。本节没产出（无 draft）或
    # draft 上没有清单时，这一列是**不可判定**，不是「false」——把「没查到」记成 false 就是
    # 把未执行记成零命中。故此处只有 `writer_checked=True` 时才给布尔值。
    writer_keys: set[tuple[str, str]] = set()
    writer_checked = False
    writer_error = ""
    if section is None:
        writer_error = "本节未产出（无 draft），Writer 清单侧不可得"
    else:
        manifest = getattr(getattr(section, "draft", None), "material_manifest", None)
        if manifest is None:
            writer_error = "本节有 draft 但材料清单不可得，Writer 清单侧不可判定"
        else:
            writer_checked = True
            for row in tuple(getattr(manifest, "entries", ()) or ()):
                writer_keys.add((str(getattr(row, "pack_id", "")),
                                 str(getattr(row, "material_id", ""))))

    packs: list[dict] = []
    responsibility: list[dict] = []
    outcomes: list[dict] = []
    destinations: list[dict] = []
    for topic_id in topic_ids:
        try:
            pack = pack_set.pack_for(topic_id)
        except Exception as exc:  # noqa: BLE001
            packs.append({"topic_id": topic_id, "missing_pack": True,
                          "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
            continue
        source_set = getattr(pack, "source_set", None)
        packs.append({
            "topic_id": topic_id, "pack_id": str(pack.pack_id),
            "source_set": source_set.to_dict() if source_set is not None else None,
            "source_set_fingerprint": (source_set.fingerprint()
                                      if source_set is not None else None),
            "material_count": len(tuple(pack.materials or ())),
            "aspect_count": len(tuple(pack.aspect_results or ())),
        })
        material_by_id = {str(m.material_id): m for m in tuple(pack.materials or ())}
        for row in tuple(getattr(pack, "aspect_source_responsibility", ()) or ()):
            responsibility.append({"topic_id": topic_id, **row.to_dict()})
        for outcome in tuple(getattr(pack, "source_aspect_outcomes", ()) or ()):
            outcomes.append({"topic_id": topic_id, **outcome.to_dict()})
        # 「材料去哪了」：逐 `(aspect, 来源)` 取 Pack 里**记下来的**材料 id，逐个回查 Pack 与
        # Writer 清单。臂 A 的材料 id 是 Pack 自己写的，这里只做**去向核对**，不重新归因。
        for outcome in tuple(getattr(pack, "source_aspect_outcomes", ()) or ()):
            document_id = str(outcome.source_document_key.document_id)
            for material_id in tuple(getattr(outcome, "material_ids", ()) or ()):
                material = material_by_id.get(str(material_id))
                row = {
                    "topic_id": topic_id, "aspect_id": str(outcome.aspect_id),
                    "document_id": document_id, "arm": str(outcome.arm),
                    "material_id": str(material_id),
                    "in_pack": material is not None,
                    # 查过才给布尔值；没查过是 `None` + `writer_manifest_query` 说明为什么。
                    "in_writer_manifest": (
                        (str(pack.pack_id), str(material_id)) in writer_keys
                        if writer_checked else None),
                    "writer_manifest_query": ("checked" if writer_checked
                                              else "not_available"),
                }
                if material is not None:
                    row["material_type"] = str(material.material_type)
                    row["locator"] = material.locator.to_dict()
                    row["payload_chars"] = _material_text_chars(
                        material, state.inputs.resolver)
                destinations.append(row)

    return {
        "status": "readback",
        "section_id": section_id,
        "arm_vocabulary": list(TS.SOURCE_ASPECT_OUTCOME_ARMS),
        "projected_terminals": list(TS.PROJECTED_TERMINALS),
        "unfulfilled_reasons": list(TS.UNFULFILLED_REASONS),
        "writer_manifest_status": ("checked" if writer_checked else "not_available"),
        "writer_manifest_error": writer_error,
        "source_set": packs,
        "aspect_source_responsibility": responsibility,
        "per_source_aspect_outcome": outcomes,
        "material_destination_by_document": destinations,
        "note": ("这四块是**只读证据**，不参与任何判据。`material_destination_by_document` 逐条"
                 "回答「这份材料在第几步掉的」：`in_pack=false` 是 Pack 自己记了 id 却查不到"
                 "（身份不一致，属缺陷）；`payload_chars=-1` 是 payload 回查不到；"
                 "`in_writer_manifest=false` 是到了 Writer 精确清单这一层之前掉的。"
                 "**查过才有布尔值**：本节未产出或清单不可得时该列为 `null`、"
                 "`writer_manifest_query=not_available`、块级 `writer_manifest_status=not_available`"
                 "并给出 typed 原因——**不可判定不是 false**，不得记成「材料没到 Writer」。"
                 "缺 Pack / 读不出时记 `unavailable` 或 `missing_pack`，**不得**记成零命中。"
                 "**两本账不得混读**：`aspect_source_responsibility` 与 "
                 "`per_source_aspect_outcome` 是**同一本**逐 `(aspect, 来源)` 的账（行数必然"
                 "相等），`material_destination_by_document` 是**另一本**逐 "
                 "`(aspect, 来源, 材料)` 的账（只含臂 A 记下的材料），行数不同不是矛盾，"
                 "更不是「差掉的那些被跳过」。**「材料进过 Writer 请求面」不等于「`SectionDraft` "
                 "落地」**：`in_writer_manifest=true` 只说明这条材料在本节的 Writer 精确材料清单"
                 "里；本节有没有产出看 `failure_diagnostics.json` 的 "
                 "`sections[*].has_section_draft` / `read_back_status`。"),
    }


#: T1 材料包读回产物的载荷形状版本。字段增删必须改这个号：读法变了（例如
#: 「原文读不出」与「原文是空串」、「身份第四轴取不到」与「第四轴是空」是两组不同结论），
#: 旧读者必须看得出来。
#: `/3`（M930-3 定点返修 ②）：`selection_form` 的读法多一列 `trailing_content`（这一行
#: **管着**的那句所述内容），且**判定它是不是表单行**的判据变了（尾随内容不再被当成"最后一个
#: 选项标签"）。旧读者按"整段文本不含句读才是表单行"去读，会把 `/3` 里成片的表单行读成
#: 普通正文——那是两组不同的结论，因此号必须前进。
#: `/4`（M930-3 第二批）：**表对象信道**的材料不再落进 `content_qualification = None`。
#: 它们现在带 `kind="table_object"` 与 `reading_policy`（阅读材料 / 数字权威 / 允许用途 /
#: 排除项 / 表题 / 表体行数），节级还多 `table_object_material_ids`；span 侧同位置新增
#: `reading_policy=None` 以保持形状一致。旧读者会把表对象读成 `unavailable`
#: ——那是「读失败」，与「这条信道不归 span 分类器管」是两组不同的结论。
#: `/5`（本批）：`reading_policy` 多一个键 `envelope_kind`——读回侧现在**同时**认两条表对象
#: 信道（`tom-1` 历史 run 与 §0.18 W8 之后的 `gtm-1` 单通道），故读数必须带出**实际观察到
#: 的是哪一条**，否则两条信道的表材料在产物里长得一模一样，谁是谁无从查起。`/4` 的读者
#: 不知道有这个键，会把它读成「表对象只有一条信道」——形状变了，号必须前进。
MATERIAL_PACK_SCHEMA_VERSION = "material-pack/5"


def _material_pack_readback_all(state: "RunState") -> dict:
    """材料包读回的**入口**：它自己不抛（与 `_failure_diagnostics_payload` 同一条纪律）。

    读回写手的 bug 不能把整轮产物一起带走——那正是「失败不可审计」的最糟形态。构建失败时
    返回一份**明说「这份读回本身没读成」**的载荷（`status="build_failed"` + 逐节 `unavailable`
    的 typed 原因），而不是留一个看起来正常的空 `entries`——空表会被读成「这一次一份原文都没有」。
    """
    payload = {
        "schema_version": MATERIAL_PACK_SCHEMA_VERSION,
        "status": "readback",
        "raw_text_field": "text",
        "sections": {},
        "entry_count": 0,
        "sections_readback": [],
        "sections_unavailable": [],
        "note": ("逐条读自**权威 Pack**（`TopicResearchPack.materials` 与逐材料恰一条的 "
                 "`ResearchMaterialDisposition`），不是 Writer prompt 的倒抄，也不止 ID 与字数："
                 "`text` 是完整原文，`document_identity` 是四轴，`locator` 是原件，"
                 "`disposition` 是保留/拒绝与 typed 理由。`text_status=unavailable` 表示 payload "
                 "回查不到（`text_chars=-1`），**不是**「原文为空」；"
                 "`document_identity_basis != source_set_member_same_id_and_version` 表示第四轴"
                 "取不到（对不上或对上多份），此时 `evidence_set_version` 是 `None` 而**不是**"
                 "猜测值。**「取到原文」不等于「进了正文」**：去向列说的是 Pack 侧的研究处置。"),
    }
    failed: list[dict] = []
    for section_id in SECTION_ORDER:
        # **逐节**兜底：某一节读不回来不得让其余节跟着消失，更不得把整份产物带走。
        try:
            block = _material_pack_readback(state, section_id)
        except Exception as exc:  # noqa: BLE001 —— 读回写手的 bug 不得升级成 run 失败
            block = {"status": "build_failed", "section_id": section_id,
                     "detail": f"本节读回构建期异常：{type(exc).__name__}: {str(exc)[:200]}",
                     "entry_count": 0, "entries": []}
            failed.append({"section_id": section_id,
                           "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
        payload["sections"][section_id] = block
        payload["entry_count"] += int(block.get("entry_count") or 0)
        (payload["sections_readback"] if block.get("status") == "readback"
         else payload["sections_unavailable"]).append(section_id)
    if failed:
        payload["status"] = "partial"
        payload["build_failed_sections"] = failed
        payload["note"] += (" **本次读回不完整**：逐节构建期异常见 `build_failed_sections`，"
                            "这些节的 `entries` 缺失——那是**读不回来**，不是「没有材料」。")
    return payload


def _material_pack_readback(state: "RunState", section_id: str) -> dict:
    """**从权威 Pack 读回**的材料包（T1；人读/机器读同一份读数的两个渲染）。

    它回答的是「这一次真实运行到底取到了哪些原文」，逐条给出四件事：

      1. **完整原文**（不是字数、不是摘要）：经 `verify_material_payload_ref` 解出 payload
         后取 `content.text` 全文；解不出就记 `unavailable` + typed 原因，**不**记空串——
         「读不出」与「本来就没有」是两件事；
      2. **文档及版本身份**：四轴（`company_id` / `document_id` / `document_version` /
         `evidence_set_version`）。前三轴在 `material.locator` 上；`evidence_set_version`
         只在**源集成员**上，因此按 `(document_id, document_version)` 与 `pack.source_set`
         的成员对**恰好一份**——对不上或对上多份时留 `None` 并写明 `identity_basis`，
         **不猜**（与全链的四轴纪律同一条）；
      3. **页/span locator**：`material.locator` 原件（`page` / `block_range` /
         `section_path` / `table_title` / `offset`），不重写、不折算；
      4. **topic/aspect 对应**与**保留或拒绝去向及理由**：`ResearchMaterialDisposition`
         的 `admission_state`（`admitted`/`rejected`）、`retention_state`
         （`retained`/`dropped`）、`source_validation`、`reason_code`、`reason_proof`。
         另附「有没有进 Writer 精确材料清单」（查过才给布尔值，未产出时是 `null`）。

    读的是 `state.inputs.pack_sets` / `authority.pack_set` 的 **Pack 本体**（`TopicResearchPack`
    逐字段），**不是** Writer prompt 的倒抄，也不是 `writer_material_manifest` 的 `chars` 计数。
    Pack 缺失或读取失败如实记 `unavailable`/`missing_pack`，不记成零命中。
    """
    from harness import topic_schema as TS

    # 现场可能根本没有 `tasks`（整轮在建现场之前就被拒）——那是「读不回来」，不是异常，
    # 更不是「没有材料」：这里如实记原因，而不是让 AttributeError 升级成 run 失败。
    tasks = getattr(getattr(state, "inputs", None), "tasks", None)
    task = None if tasks is None else tasks.get(section_id)
    if task is None:
        return {"status": "unavailable", "section_id": section_id,
                "detail": ("本节的现场没有 task，材料包无从读起"
                           if tasks is not None else
                           "本次运行的现场没有 tasks（在建现场之前就结束了）")}
    try:
        authority = state.authority_of(section_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "section_id": section_id,
                "detail": f"权威取不到：{type(exc).__name__}: {str(exc)[:160]}"}
    pack_set = getattr(authority, "pack_set", None)
    topic_ids = tuple(str(t) for t in (task.topic_ids or ()))
    if pack_set is None or not topic_ids:
        return {"status": "unavailable", "section_id": section_id,
                "detail": f"本节没有可用 Pack 集（pack_set={'有' if pack_set else '无'}，"
                          f"topic 数 {len(topic_ids)}）"}

    # Writer 侧去向只作**附加**列：查过才给布尔值，没查过是 `null` + typed 原因。
    section = state.sections.get(section_id)
    writer_keys: set[tuple[str, str]] = set()
    writer_checked = False
    writer_error = ""
    if section is None:
        writer_error = "本节未产出（无 draft），Writer 清单侧不可得"
    else:
        manifest = getattr(getattr(section, "draft", None), "material_manifest", None)
        if manifest is None:
            writer_error = "本节有 draft 但材料清单不可得，Writer 清单侧不可判定"
        else:
            writer_checked = True
            for row in tuple(getattr(manifest, "entries", ()) or ()):
                writer_keys.add((str(getattr(row, "pack_id", "")),
                                 str(getattr(row, "material_id", ""))))

    packs: list[dict] = []
    entries: list[dict] = []
    for topic_id in topic_ids:
        try:
            pack = pack_set.pack_for(topic_id)
        except Exception as exc:  # noqa: BLE001
            packs.append({"topic_id": topic_id, "missing_pack": True,
                          "error": f"{type(exc).__name__}: {str(exc)[:160]}"})
            continue
        materials = tuple(pack.materials or ())
        dispositions = {str(d.material_id): d
                        for d in tuple(getattr(pack, "material_dispositions", ()) or ())}
        aspects_by_material: dict[str, list[str]] = {}
        for result in tuple(pack.aspect_results or ()):
            for material_id in tuple(result.material_ids or ()):
                aspects_by_material.setdefault(str(material_id), []).append(
                    str(result.aspect_id))
        # 源集成员：`evidence_set_version` 的唯一去处。按 (id, version) 建索引，命中多份即
        # 不可判定（**不取第一份**——那正是四轴就近匹配的错法）。
        members_by_pair: dict[tuple[str, str], list] = {}
        for member in tuple(getattr(pack.source_set, "members", ()) or ()):
            key = member[0]
            members_by_pair.setdefault(
                (str(key.document_id), str(key.document_version)), []).append(key)

        packs.append({
            "topic_id": topic_id, "pack_id": str(pack.pack_id),
            "source_set_fingerprint": (pack.source_set.fingerprint()
                                       if getattr(pack, "source_set", None) is not None
                                       else None),
            "material_count": len(materials),
            "disposition_count": len(dispositions),
        })

        for material in materials:
            material_id = str(material.material_id)
            disposition = dispositions.get(material_id)
            locator = material.locator
            document_id = str(getattr(locator, "document_id", "") or "")
            document_version = str(getattr(locator, "document_version", "") or "")
            candidates = members_by_pair.get((document_id, document_version), [])
            if len(candidates) == 1:
                key = candidates[0]
                identity = {
                    "company_id": str(key.company_id), "document_id": str(key.document_id),
                    "document_version": str(key.document_version),
                    "evidence_set_version": str(key.evidence_set_version)}
                identity_basis = "source_set_member_same_id_and_version"
            else:
                identity = {"company_id": "", "document_id": document_id,
                            "document_version": document_version,
                            "evidence_set_version": None}
                identity_basis = ("no_source_set_member" if not candidates
                                  else f"ambiguous_{len(candidates)}_source_set_members")

            text, text_status, qualification = _material_text_and_qualification(
                material, state.inputs.resolver)
            disposition_block = (
                None if disposition is None else
                {"disposition_id": str(disposition.disposition_id),
                 "admission_state": str(disposition.admission_state),
                 "retention_state": str(disposition.retention_state),
                 "source_validation": str(disposition.source_validation),
                 "reason_code": str(disposition.reason_code),
                 "reason_proof": str(disposition.reason_proof),
                 "policy_version": str(disposition.policy_version),
                 # 处置自己记的 aspect 绑定；与 `aspect_results[].material_ids` 是两条来源，
                 # 两条都写出来，不一致时读的人看得见（不在这里替任何一条说话）。
                 "aspect_ids": [str(x) for x in tuple(disposition.aspect_ids or ())]})
            entries.append({
                "section_id": section_id, "topic_id": topic_id,
                "pack_id": str(pack.pack_id),
                "material_id": material_id,
                "material_type": str(material.material_type),
                "content_hash": str(material.content_hash),
                "document_identity": identity,
                "document_identity_basis": identity_basis,
                "locator": locator.to_dict() if hasattr(locator, "to_dict") else {},
                "aspect_ids_from_aspect_results": aspects_by_material.get(material_id, []),
                "disposition": disposition_block,
                "retention_destination": _retention_destination(disposition),
                "in_writer_manifest": (
                    (str(pack.pack_id), material_id) in writer_keys
                    if writer_checked else None),
                "writer_manifest_query": "checked" if writer_checked else "not_available",
                # §二 2.3：这一份**是什么形态**。勾选表单行在这里摊成六列，并附允许用途与
                # 排除项；`text` 仍是完整原文（符号字形原样保留），形态读法**不改写原文**。
                "content_qualification": qualification,
                "text": text,
                "text_chars": (len(text) if text_status == "resolved" else -1),
                "text_status": text_status,
            })

    kinds: dict[str, int] = {}
    for entry in entries:
        qualification = entry.get("content_qualification")
        kind = (str(qualification.get("kind", "") or "")
                if isinstance(qualification, Mapping) else "unavailable")
        kinds[kind] = kinds.get(kind, 0) + 1
    #: 直方图的键分三族，**不得**互相替代：§二 2.3 的 span 词表成员（`text` /
    #: `selection_form` / …）、表对象信道的 `table_object`、以及 `unavailable`
    #: （信封里既没有可读形态、也不是表对象信封）。加了 `table_object` 之后
    #: `unavailable` 才真的只表示「读不出形态」；从前它**同时**吞掉了表对象，
    #: 于是 11 份已准入、原文 143 字的表材料在节级读起来像 11 次读失败。
    table_object_ids = [entry["material_id"] for entry in entries
                        if isinstance(entry.get("content_qualification"), Mapping)
                        and entry["content_qualification"].get("kind") == "table_object"]
    selection_forms = [entry["material_id"] for entry in entries
                       if isinstance(entry.get("content_qualification"), Mapping)
                       and entry["content_qualification"].get("kind") == "selection_form"]
    return {
        "status": "readback",
        "section_id": section_id,
        "admission_states": list(TS.MATERIAL_ADMISSION_STATES),
        "retention_states": list(TS.MATERIAL_RETENTION_STATES),
        "source_validations": list(TS.MATERIAL_SOURCE_VALIDATIONS),
        "reason_codes": list(TS.MATERIAL_DISPOSITION_REASON_CODES),
        "writer_manifest_status": "checked" if writer_checked else "not_available",
        "writer_manifest_error": writer_error,
        "source_set": packs,
        "entry_count": len(entries),
        "content_kind_counts": dict(sorted(kinds.items())),
        # 勾选表单行的 material_id 一览：读的人不必去正文里认字形才知道有哪些表单行。
        "selection_form_material_ids": selection_forms,
        # 表对象信道材料的 material_id 一览：读的人不必逐条翻 `content_qualification`
        # 才知道这一节里哪些是表对象。
        "table_object_material_ids": table_object_ids,
        "entries": entries,
        "note": ("逐条读自**权威 Pack**（`TopicResearchPack.materials` 与逐材料恰一条的 "
                 "`ResearchMaterialDisposition`），不是 Writer prompt 的倒抄，也不止 ID 与字数："
                 "`text` 是完整原文，`document_identity` 是四轴，`locator` 是原件，"
                 "`disposition` 是保留/拒绝与 typed 理由。`text_status=unavailable` 表示 payload "
                 "回查不到（`text_chars=-1`），**不是**「原文为空」；"
                 "`document_identity_basis != source_set_member_same_id_and_version` 表示第四轴"
                 "取不到（对不上或对上多份），此时 `evidence_set_version` 是 `None` 而**不是**猜测值。"
                 "`content_qualification` 是 §二 2.3 的**内容形态**读法（材料构建期写入、已进 "
                 "payload 哈希）：`kind=selection_form` 的条目另带**六列**（所问事项 / 选项 / "
                 "选中状态 / 选项串之后的内容（`trailing_content`）/ 所在节点 / 来源）与**允许用途**、"
                 "**排除项**。形态读法**不改写原文**："
                 "`text` 仍是逐字原文，符号字形原样在内。形态名读不懂时 `kind_basis` 记 "
                 "`unavailable: kind 不在封闭词表内`，**不**降级成 `text`（那等于替它声称"
                 "「这是叙述材料」）；`selection_basis` 区分「本来就不是表单行」与"
                 "「是表单行但状态未判定」两种情形，不合并成一个 null。"
                 "**表对象信道**的材料形态为 `table_object`：它的允许用途与排除项不在 §二 2.3 "
                 "那条词表里，而在同信封的 `reading_policy` 下原样带出"
                 "（`reading_material` 与 `numeric_authority` 是**两条正交声明**，不合并）；"
                 "`kind_in_vocabulary=False` 对它是「不归那条词表管」，**不是**「读不懂」——"
                 "从前这两种情形在节级直方图里都落 `unavailable`，于是已准入、已保留、原文可读、"
                 "已进 Writer 清单的表材料在节级读起来像读失败。"
                 "**形态与去向是两条轴**：这一列说的是「它是什么」，`disposition`/`retention` "
                 "说的是「Pack 侧怎么处置它」。"),
    }


def _retention_destination(disposition) -> str:
    """材料的**去向下场**一句话（只由 `ResearchMaterialDisposition` 的两轴决定）。

    `admission_state` × `retention_state` 是两条独立的轴，四种组合各有含义，不得压成一个
    「保留/拒绝」布尔：被拒但留在 Pack 里（`rejected` + `retained`）与被拒且已丢弃
    （`rejected` + `dropped`）在读回时是两件事。
    """
    if disposition is None:
        return "no_disposition_recorded"
    admitted = str(disposition.admission_state) == "admitted"
    retained = str(disposition.retention_state) == "retained"
    if admitted and retained:
        return "retained_for_writing"
    if admitted and not retained:
        return "dropped_after_admission"
    if not admitted and retained:
        return "rejected_but_kept_in_pack"
    return "rejected_and_dropped"


def _material_full_text(material, resolver) -> tuple[str, str]:
    """材料的**完整原文**与读出状态（读不出就是 `unavailable`，不返回空串冒充「没有内容」）。

    与 `_material_text_chars` 同一条 payload 回查路径，区别只在它返回正文本身而不是长度：
    字数够不够读，与原文是什么，是两个问题，产物里两样都要有。
    """
    text, status, _ = _material_text_and_qualification(material, resolver)
    return text, status


def _table_object_qualification(envelope: Mapping) -> dict | None:
    """**表对象**信封的形态读法（与 §二 2.3 的 span 内容形态**不是**同一条信道）。

    表对象信封按设计**不带** `content_qualification`：那一列是给 span 候选的形态分类
    （`text` / `selection_form` / …），表对象不由那条分类器说话。它自己的形态、**允许用途**与
    **排除项**写在信封的 `reading_policy` 里（逐字由表对象信道构建期写入、已进 payload 哈希），
    表题与表体行数在 `content.structured_payload` 里。

    返回 `None` 表示「这不是表对象信封」——此时调用方沿用原来的读不出口径，**不**在这里替
    任何别的材料类型编一个形态。

    两条**正交**声明在这里原样搬运、不合并：`reading_material`（它是不是阅读材料）与
    `numeric_authority`（它有没有数字权威）。表对象的设计是前者为真、后者为假，
    「这张表能读」与「表里的数字能以它为准」因此不会被读成一件事。

    信封种类是**两种**，不是一种：`tom-1`（`table-object-material-v1`，历史 run 的信道）与
    `gtm-1`（`graph-table-material-v1`，§0.18 W8 单通道之后的当前信道）。两者都按同一套
    `reading_policy` + `content.structured_payload` 说话，差别只在表题键名（`table_title` /
    `title_text`）。**只认其中一种**会把另一种信道的表材料读成「不是表对象信封」→ 落回
    `kind=unavailable`，于是**已放行、已进 Pack、已进 Writer 清单**的表在人读页上印成
    「读不出形态」——这正是本函数当初要修掉的那个错，只是换了一条信道重演。
    """
    from harness import graph_table_materials as GTM
    from harness import table_object_materials as TOM

    kind = str(envelope.get("envelope_kind", "") or "")
    accepted = (TOM.TABLE_OBJECT_MATERIAL_ENVELOPE_KIND,   # tom-1：历史 run
                GTM.GRAPH_TABLE_MATERIAL_ENVELOPE_KIND)    # gtm-1：当前单通道
    if kind not in accepted:
        return None
    policy = envelope.get("reading_policy")
    policy = dict(policy) if isinstance(policy, Mapping) else {}
    content = envelope.get("content")
    structured = content.get("structured_payload") if isinstance(content, Mapping) else None
    structured = dict(structured) if isinstance(structured, Mapping) else {}
    rows = structured.get("body_row_count")
    # 表题键名逐信道不同（`gtm-1` 是 `title_text`）——这里两条都试，不替哪一条写死默认值。
    title = ""
    for key in ("table_title", "title_text"):
        title = str(structured.get(key, "") or "")
        if title:
            break
    return {
        #: 形态名是**读回侧**的档位名，不进 `TM.TREE_MATERIAL_CONTENT_KINDS`（那是 span 候选的
        #: 分类词表；把它塞进去等于声称表对象由一个不管它的分类器判过）。
        "kind": "table_object",
        "kind_in_vocabulary": False,
        "kind_basis": ("envelope: " + kind
                       + "（表对象信道；形态与允许用途写在同信封的 `reading_policy` 里，"
                         "不走 span 内容形态分类器）"),
        "selection": None,
        "selection_basis": None,
        "reading_policy": {
            #: 实际观察到的是哪一条信道。两条信道的读数**不得**被读成同一批对象的读数。
            "envelope_kind": kind,
            "reading_material": policy.get("reading_material"),
            "numeric_authority": policy.get("numeric_authority"),
            "permitted_use": str(policy.get("permitted_use", "") or ""),
            "exclusions": [str(x) for x in (policy.get("exclusions") or ())],
            "table_title": title,
            "body_row_count": (int(rows) if isinstance(rows, int) else None),
        },
    }


def _material_text_and_qualification(material, resolver) -> tuple[str, str, dict | None]:
    """材料的完整原文 + 读出状态 + **内容形态读法**（三者同一趟 payload 回查，不读两遍）。

    第三项逐字取自信封的 `content_qualification`（§二 2.3，材料构建期写入、已进 payload
    哈希）。它是**派生**读法：原文仍在第一项里，一个字符不改。勾选表单行在这里摊成六列
    （所问事项 / 选项 / 选中状态 / 所在节点 / 来源 / 尾随内容 `trailing_content`，`tmr-2` 起）
    并附**允许用途与排除项**——因此读回产物里
    「这一份是表单行」与「这一份是叙述材料」是分得开的，不必靠人去正文里认字形。
    第一列（所问事项）自 `tmr-3` 起**允许为空**：行内没写主语时印成「行内没写主语」，
    「所在节点」同时标成**仅导航坐标**——这两句必须印出来，否则读者会拿节点标题把主语补回来
    （那正是 `tmr-2` 的错，也是本批要纠正的那条归属）。
    读不出形态时记 `kind=unavailable` + typed 原因，**不**记成 `text`（那是「按叙述材料读」）。

    信封里**没有** `content_qualification` 时有两条不同的结论，因此走两条不同的返回：
    **表对象信封**（`tom-1` 历史信道与 `gtm-1` 当前单通道，两条都认）→
    `_table_object_qualification`（形态、允许用途、排除项在 `reading_policy` 里，读得出）；
    **别的材料** → `None`（这才是真正的「读不出形态」）。把前者也记成 `None`
    会让「这条信道不归 span 分类器管」与「读失败」在产物里长得一模一样。
    """
    from harness import topic_schema as TS
    from harness import tree_materials as TM

    try:
        resolved = TS.verify_material_payload_ref(material.payload_ref, resolver)
    except Exception as exc:  # noqa: BLE001
        return "", f"unavailable: payload 回查失败 {type(exc).__name__}", None
    body = resolved.payload_bytes
    if body is None:
        return "", "unavailable: payload 无字节", None
    try:
        envelope = json.loads(body.decode("utf-8"))
        text = envelope["content"]["text"] or ""
    except Exception as exc:  # noqa: BLE001
        return "", f"unavailable: payload 解析失败 {type(exc).__name__}", None
    raw = envelope.get("content_qualification")
    qualification: dict | None = None
    if not isinstance(raw, Mapping):
        # §二 2.3 的 `content_qualification` 只覆盖 **span** 候选的形态分类。表对象信道按设计
        # 不带这一列——它的形态、允许用途与排除项写在信封自己的 `reading_policy` 里。过去这里
        # 一律返回 `None`，于是**已准入、已保留、原文 143 字、已进 Writer 清单**的表对象在人读
        # 页上印成「内容形态：**读不出**」，在节级直方图里落到 `unavailable`——把「这条信道不归
        # 那个分类器管」写成了「读失败」。两者是不同的结论，因此各自成档。
        qualification = _table_object_qualification(envelope)
    else:
        kind = str(raw.get("kind", "") or "")
        selection = raw.get("selection")
        # 原件 locator：`to_dict()` 给页/块/偏移/章节路径，`document_id`/`document_version`
        # 是 locator 对象上的字段（与 `_material_pack_readback` 的取法同一条，不另立第二套）。
        material_locator = getattr(material, "locator", None)
        locator = dict(material_locator.to_dict()) if hasattr(material_locator, "to_dict") else {}
        for axis in ("document_id", "document_version", "section_path", "page"):
            if not locator.get(axis):
                locator[axis] = getattr(material_locator, axis, "") or locator.get(axis)
        columns = None
        if isinstance(selection, Mapping):
            columns = TM.selection_form_columns(
                selection=selection, locator=locator,
                source_identity=str(getattr(material, "source_identity", "") or ""))
        qualification = {
            "kind": kind,
            # 形态名读不懂时**不降级成 text**：那等于替它声称「这是叙述材料」。
            "kind_in_vocabulary": kind in TM.TREE_MATERIAL_CONTENT_KINDS,
            "kind_basis": ("envelope" if kind in TM.TREE_MATERIAL_CONTENT_KINDS
                           else "unavailable: kind 不在封闭词表内"),
            "selection": columns,
            # 摊不出这六列时要分清两种情形，不合并成一个 null。
            "selection_basis": (
                None if columns is not None else
                ("not_a_selection_form" if kind != "selection_form" else
                 "selection_form_unresolved: "
                 + str(selection.get("unresolved_reason", "")
                       if isinstance(selection, Mapping) else "missing"))),
            #: 表对象信道的两条声明。span 候选没有这一段（它们的范围判据写在表单行的六列里），
            #: 但键**保留**——形状逐条一致，读者才不用猜「缺这一键」是什么意思。
            "reading_policy": None,
        }
    return str(text), "resolved", qualification


def _material_pack_md(state: "RunState") -> str:
    """材料包的**人读**版（T1）：逐条摊开原文与去向，供人直接验收取料质量。

    与 `cross_document_evidence.md` 的分工：那份按**来源**回答「哪一份查了没有」，
    本份按**材料**回答「取到的原文是什么、它被保留还是拒绝、为什么」。两份都只读同一批
    Pack 读数，都没有自己的判据。

    它**自己不算**：正文与 `material_pack.json` 是**同一次读回**（`_material_pack_readback_all`）
    的两个渲染。两份各算一次的话，同一轮的两个文件迟早会对不上，而「人读的那份」往往是最后
    被相信的那份。
    """
    return _material_pack_md_from(_material_pack_readback_all(state))


def _qualification_md_line(qualification: Any) -> str:
    """内容形态读法的人读一行（勾选表单行把**六列**摊在原文之前，便于人核）。"""
    if not isinstance(qualification, Mapping):
        return ("- 内容形态：**读不出**（信封里既没有 `content_qualification`，"
                "也不是表对象信封）——")
    kind = str(qualification.get("kind", "") or "")
    if kind == "table_object":
        policy = qualification.get("reading_policy")
        policy = dict(policy) if isinstance(policy, Mapping) else {}
        rows = policy.get("body_row_count")
        return ("- 内容形态：`table_object`（**表对象信道**：形态、允许用途与排除项写在同信封的 "
                "`reading_policy` 里，**不走** span 内容形态分类器——`kind_in_vocabulary` 为假"
                "说的是「不归那条词表管」，**不是**「读不懂」）\n"
                f"  - 表题：{('`' + policy['table_title'] + '`') if policy.get('table_title') else '（对象未给可核表题）'}"
                f"｜表体行：{rows if isinstance(rows, int) else '（对象未给行数）'}\n"
                f"  - 阅读材料：`{policy.get('reading_material')}`"
                f"｜数字权威：`{policy.get('numeric_authority')}`"
                "（两条**正交**：这张表能读，不等于表里的数字能以它为准）\n"
                f"  - 允许用途：`{policy.get('permitted_use')}`；排除项："
                + "、".join(f"`{x}`" for x in policy.get("exclusions") or ()))
    if not qualification.get("kind_in_vocabulary"):
        return (f"- 内容形态：**读不懂**（`{kind}`，`{qualification.get('kind_basis')}`）——"
                "不按叙述材料读，也不当成 `text`。")
    if kind != "selection_form":
        return f"- 内容形态：`{kind}`（叙述材料，按原文读）"
    columns = qualification.get("selection")
    if not isinstance(columns, Mapping):
        return (f"- 内容形态：`selection_form`，但**摊不出六列**"
                f"（`{qualification.get('selection_basis')}`）——这一份不得当业务正文。")
    options = "；".join(
        f"{o['marker']}{o['label']}（{'已选' if o['selected'] else '未选'}）"
        for o in columns.get("options") or ())
    source = columns.get("source") or {}
    trailing = str(columns.get("trailing_content", "") or "")
    # 所问事项**允许为空**（`tmr-3`）：行内没写主语时读法不再回指所在节点标题——节点可能是
    # 这一行的**栏目**，回指会把子项状态锚到整个栏目上。空值必须印成人话，不能印成一对空反引号
    # （读者会读成"渲染漏了"，或者更糟：拿下面的「所在节点」把主语补回来）。
    asked_item = str(columns.get("asked_item", "") or "")
    if asked_item:
        asked_line = (f"  - 所问事项：`{asked_item}`"
                      f"（取自 `{columns.get('asked_item_scope')}`）\n")
        node_label = "所在节点"
    else:
        asked_line = ("  - 所问事项：**行内没写主语**（本行读法不回指所在节点标题）——"
                      "这一行读出的是「某一**行内未具名**事项的适用性」，**不指向任何栏目**；"
                      "原文与来源照旧留档，缺的只是那条归属\n")
        node_label = "所在节点（**仅导航坐标**：本行没写主语，不得据它补主语）"
    return (f"- 内容形态：`selection_form`（**分流**：只在所问事项上说话，不是叙述正文）\n"
            + asked_line
            + f"  - 选项：{options}\n"
            + "  - 选中状态："
            + ("、".join(f"`{x}`" for x in columns.get("selected_labels") or ()) or "（无）")
            + f"\n  - 选项串之后的内容："
            + (f"`{trailing}`（**不是**给正文用的原文，" if trailing
               else "（这一行没有尾随内容）")
            + ("按 `no_support_from_trailing_content` 排除）" if trailing else "")
            + f"\n  - {node_label}：`{(columns.get('node') or {}).get('section_path')}`\n"
            f"  - 来源：`{source.get('source_identity')}` / doc `{source.get('document_id')}` / "
            f"页 `{source.get('page')}` / 块 `{source.get('block_range')}` / 偏移 `{source.get('offset')}`\n"
            f"  - 允许用途：`{columns.get('permitted_use')}`；排除项："
            + "、".join(f"`{x}`" for x in columns.get("exclusions") or ()))


def _material_pack_md_from(payload: dict) -> str:
    """把材料包读回的载荷渲染成人读版（**不重新读 Pack**，只换渲染）。"""
    lines = ["# M930-3 材料包 · 逐条原文与去向（人工阅读用）", "",
             "机器可读版在 `material_pack.json`；两者的读数出自同一次读回。",
             "本文件的每一条都读自 **Pack 本体**（`TopicResearchPack.materials` + 逐材料恰一条的 "
             "`ResearchMaterialDisposition`），不是 Writer prompt 的倒抄。", "",
             "**「取到了原文」不等于「进了正文」**：`去向` 列说的是 Pack 侧的研究处置"
             "（保留 / 拒绝 / 丢弃），`进 Writer 清单` 列只是附加读数——本节未产出时那一列是"
             "`null`（不可判定），不是 `false`。", ""]
    if payload.get("build_failed_sections"):
        lines += ["**本次读回不完整**：以下节在读回时构建期异常，其明细缺失——那是**读不回来**，"
                  "不是「没有材料」：", ""]
        lines += [f"- `{row['section_id']}`：{row['error']}"
                  for row in payload["build_failed_sections"]]
        lines.append("")
    for section_id in SECTION_ORDER:
        block = dict(payload.get("sections", {}).get(section_id) or
                     {"status": "unavailable", "detail": "本节的读回没有落进载荷"})
        lines += [f"## 节 `{section_id}`", ""]
        if block.get("status") != "readback":
            lines += [f"- 不可读：`{block.get('status')}` —— {block.get('detail', '')}",
                      "- **这不等于「零命中」**：读不出与查不到是两件事。", ""]
            continue
        for pack in block["source_set"]:
            if pack.get("missing_pack"):
                lines += [f"- topic `{pack['topic_id']}`：**缺 Pack**（{pack.get('error')}）", ""]
                continue
            lines += [f"- topic `{pack['topic_id']}`（Pack `{pack['pack_id']}`，"
                      f"材料 {pack['material_count']}，处置记录 {pack['disposition_count']}）", ""]
        entries = block["entries"]
        lines += [f"### 材料逐条：{len(entries)} 条", ""]
        _tob = block.get("table_object_material_ids") or []
        if _tob:
            lines += [f"- 其中 **表对象信道** {len(_tob)} 份（`kind=table_object`）：这些不是 §二 2.3 "
                      "span 形态词表的成员，形态与允许用途写在各自信封的 `reading_policy` 里。"
                      "它们**能读**、也**进了 Writer 清单**，但「能读」与「表里的数字能以它为准」"
                      "是两条正交声明——逐条见下。", ""]
        for row in entries:
            identity = row["document_identity"]
            set_version = identity.get("evidence_set_version")
            lines += [
                f"#### `{row['material_id']}`（{row['material_type']}）",
                "",
                f"- topic / aspect：`{row['topic_id']}` / "
                + ("、".join(f"`{a}`" for a in row["aspect_ids_from_aspect_results"]) or "（无）")
                + f"（材料对应的 aspect 另有处置侧绑定："
                + ("、".join(f"`{a}`" for a in (row["disposition"] or {}).get("aspect_ids", ()))
                   or "（无）") + "）",
                f"- 文档：公司 `{identity.get('company_id')}` / "
                f"doc `{identity.get('document_id')}` / 版本 `{identity.get('document_version')}` / "
                f"证据集 `{set_version if set_version is not None else '**取不到（不是猜的值）**'}`"
                f"（身份依据 `{row['document_identity_basis']}`）",
                f"- 定位：`{json.dumps(row['locator'], ensure_ascii=False)}`",
            ]
            disposition = row["disposition"]
            if disposition is None:
                lines.append("- 处置：**没有处置记录**（材料侧义务是每份恰一条，"
                             "缺记录本身就是要修的事实）")
            else:
                lines.append(
                    f"- 处置：admission=`{disposition['admission_state']}` / "
                    f"retention=`{disposition['retention_state']}` / "
                    f"source_validation=`{disposition['source_validation']}`；"
                    f"理由码 `{disposition['reason_code']}`；理由 `{disposition['reason_proof']}`")
            lines.append(f"- 去向：`{row['retention_destination']}`；"
                         f"进 Writer 清单：`{row['in_writer_manifest']}`"
                         f"（`{row['writer_manifest_query']}`）")
            qualification = row.get("content_qualification")
            lines.append(_qualification_md_line(qualification))
            if row["text_status"] == "resolved":
                lines += ["", f"- 原文（{row['text_chars']} 字）：", "",
                          "```text", row["text"], "```", ""]
            else:
                lines += ["", f"- 原文：**读不出**（`{row['text_status']}`）——"
                              "这不等于「原文为空」。", ""]
    return "\n".join(lines) + "\n"


def _cross_document_md(state: "RunState") -> str:
    """跨文档四块证据的**人读**版（逐节逐份并排，供人核「三份材料各是什么下场」）。

    它不重复 `acceptance_report.json` 的机器判据，只把同一批读数摊成可读的表：源集成员与角色、
    逐 `(aspect, 来源)` 的责任与四臂结果、材料的去向。`material_destination_by_document`
    只列**臂 A 记下来的**材料（臂 B/C2/C1 本来就没有材料），因此「这里没有某份的行」不等于
    「那份被跳过了」——那份的下场在四臂表里。
    """
    lines = ["# M930-3 跨文档联合检索 · 逐份证据（人工阅读用）", "",
             "机器可读版在 `acceptance_report.json` 的 `cross_document_evidence` 块。",
             "本文件里的每一行都读自 **Pack 本体**（typed 回读）与本节定稿现场，不是自报字段。",
             "",
             "**本文件里有两本不同的账，行数不同不是矛盾**：",
             "",
             "- **责任账 = 四臂账**：逐 `(aspect, 来源)` 一行，两表行数**必然相等**"
             "（同一对 `(aspect, 来源)` 各一行）。",
             "- **材料去向账**：逐 `(aspect, 来源, 材料)` 一行，只含**臂 A 记下来的材料**，"
             "因此与上面那本账行数不同——少了不代表那份被跳过，也不代表没查。",
             "",
             "**「材料进过 Writer 请求面」不等于「`SectionDraft` 落地」**："
             "`在 Writer 清单` 这一列只回答「该材料在不在本节的 Writer 精确材料清单里」；"
             "本节到底有没有产出，看 `failure_diagnostics.json` 的 "
             "`sections[*].has_section_draft` 与 `read_back_status`——写作失败时那一列是"
             "「不可判定」（`null`），不是 `false`。", ""]
    for section_id in SECTION_ORDER:
        block = _cross_document_evidence(state, section_id)
        lines += [f"## 节 `{section_id}`", ""]
        if block.get("status") != "readback":
            lines += [f"- 不可读：`{block.get('status')}` —— {block.get('detail', '')}",
                      "- **这不等于「零命中」**：读不出与查不到是两件事。", ""]
            continue
        lines += ["### 实际消费的源集", ""]
        for pack in block["source_set"]:
            if pack.get("missing_pack"):
                lines += [f"- topic `{pack['topic_id']}`：**缺 Pack**（{pack.get('error')}）", ""]
                continue
            lines += [f"- topic `{pack['topic_id']}`（Pack `{pack['pack_id']}`，"
                      f"aspect {pack['aspect_count']}，材料 {pack['material_count']}）", ""]
            source_set = pack.get("source_set") or {}
            lines += ["  | 序位 | 文档 | 版本 | 来源角色 |", "  | --- | --- | --- | --- |"]
            for order, member in enumerate(source_set.get("members", ())):
                # 键名取自 `DocumentSourceSet.to_dict()`（`source_document_key`）；写错键名会让
                # 整列**渲染成空**却不报错——那正是「注册了三份」看起来像「读了三份」的假象。
                key = (member.get("source_document_key", {})
                       if isinstance(member, dict) else {})
                if not key:
                    lines.append(
                        f"  | {order} | **读不出文档身份** | - | "
                        f"`{member.get('source_role', '') if isinstance(member, dict) else ''}` |")
                    continue
                lines.append(
                    f"  | {order} | `{key.get('document_id', '')}` | "
                    f"`{key.get('document_version', '')}` | `{member.get('source_role', '')}` |")
            lines += ["", f"  内容指纹：`{pack.get('source_set_fingerprint')}`", ""]
        responsibility_rows = block["aspect_source_responsibility"]
        four_arm_rows = block["per_source_aspect_outcome"]
        destination_rows = block["material_destination_by_document"]
        lines += [f"### 检索前责任（逐 aspect × 逐来源）：{len(responsibility_rows)} 行", "",
                  "（这本账与下面的「逐来源四臂结果」是**同一本账**：都是逐 "
                  "`(aspect, 来源)` 一行，行数必然相等。）", "",
                  "| topic | aspect | 来源 | 必须检索 | 依据 |", "| --- | --- | --- | --- | --- |"]
        for row in block["aspect_source_responsibility"]:
            basis = "；".join(":".join(str(p) for p in b) for b in row.get("basis", ()) or ())
            lines.append(
                f"| `{row['topic_id']}` | `{row['aspect_id']}` | "
                f"`{row['source_document_key']['document_id']}` | "
                f"{row.get('retrieval_required')} | {basis[:160]} |")
        lines += ["", f"### 逐来源四臂结果：{len(four_arm_rows)} 行"
                      f"（与上面责任账同为逐 `(aspect, 来源)`，行数 {len(responsibility_rows)} 相同）",
                  "",
                  "| topic | aspect | 来源 | 臂 | 材料数 | 未完成原因 / 终态 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for row in four_arm_rows:
            record = row.get("search_record") or {}
            detail = (record.get("unfulfilled_reason")
                      or record.get("projected_terminal")
                      or record.get("synthesized_stop_reason")
                      or ("未要求检索" if row["arm"] == "C1" else ""))
            lines.append(
                f"| `{row['topic_id']}` | `{row['aspect_id']}` | "
                f"`{row['source_document_key']['document_id']}` | `{row['arm']}` | "
                f"{len(row.get('material_ids') or ())} | {detail} |")
        lines += ["", f"### 材料去向（只列臂 A 记下的材料）：{len(destination_rows)} 行", "",
                  "（这本账是逐 `(aspect, 来源, 材料)` 一行，与上面那本逐 `(aspect, 来源)` 的账"
                  "**行数不同是正常的**——它只含臂 A 记下的材料。）", "",
                  "`在 Writer 清单` 只回答「该材料在不在本节的 Writer 精确材料清单里」，"
                  "**不回答这一节有没有产出**：写作失败时它是「不可判定」。", "",
                  "| topic | aspect | 来源 | 材料 | 类型 | 在 Pack | payload 字数 | 在 Writer 清单 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- |"]
        for row in destination_rows:
            writer_cell = ("不可判定" if row.get("writer_manifest_query") != "checked"
                           else str(row["in_writer_manifest"]))
            lines.append(
                f"| `{row['topic_id']}` | `{row['aspect_id']}` | `{row['document_id']}` | "
                f"`{row['material_id']}` | `{row.get('material_type', '')}` | "
                f"{row['in_pack']} | {row.get('payload_chars', '')} | "
                f"{writer_cell} |")
        if block.get("writer_manifest_status") == "not_available":
            lines += ["", f"- Writer 清单侧：**不可判定**（{block.get('writer_manifest_error', '')}）"
                          "——「不可判定」不是 false，不能被读成「材料没到 Writer」。",
                          "- 本节**没有产出** `SectionDraft`：上面那些 `(aspect, 来源)` 行只说明"
                          "**检索面发生过什么**，不说明写作链落过什么。材料进过请求面 ≠ Draft 落地。"]
        lines.append("")
    return "\n".join(lines)


def _composed_sentence_observation(state: "RunState") -> dict:
    """run 级**观察**（明确不是判据）：把「由多条已接受 Claim 组成的一句终稿句子」按节列出。

    多 Claim 自然句的机器判据在 A1，且限定在**公司节**。某一节的语料可能只支持单条原子，
    本项目把同一性质在其它节上的实得证据摊开，供人工复核区分「机制不具备该能力」与「那一节
    的真实语料只够一条」。**观察不得用于把任何门改判为通过**：门的判据、期望值、权威都不变。
    """
    by_section: dict[str, dict] = {}
    for section_id in SECTION_ORDER:
        section = state.sections.get(section_id)
        if section is None:
            by_section[section_id] = {"sections_present": False}
            continue
        rows = _multi_claim_sentences(section)
        path_a = sum(1 for b in section.acceptance.accepted_bindings
                     if str(b.authorization_path) == "path_a_prevalidated")
        path_b = sum(1 for b in section.acceptance.accepted_bindings
                     if str(b.authorization_path) == "path_b_material_derived")
        by_section[section_id] = {
            "sections_present": True,
            "claims": len(section.claims),
            "accepted_bindings_path_a": path_a,
            "accepted_bindings_path_b": path_b,
            "multi_claim_sentence_count": len(rows),
            "multi_claim_sentences": rows[:2],
        }
    return by_section


def _path_b_claims(section) -> dict[str, str]:
    """本节**已定稿 Claim** 里由路径 B factual 边支撑的那些：`claim_id → claim_text`。

    只认「factual 语义 + 路径 B 授权」的边：context 边（不授权事实）与路径 A 边都不在其中 ——
    这正是 §三 A/B 的路径区分在验收侧的复算。
    """
    by_id = {str(b.accepted_support_binding_id): b for b in section.acceptance.accepted_bindings}
    out: dict[str, str] = {}
    for claim in section.claims:
        for binding_id in claim.accepted_binding_ids:
            binding = by_id.get(str(binding_id))
            if binding is None:
                continue
            if str(binding.support_semantics) == "factual" \
                    and str(binding.authorization_path) == "path_b_material_derived":
                out[str(claim.claim_id)] = str(claim.text)
    return out


def _gate_a1_company(output, authority, task, resolver) -> GateOutcome:
    """A1：公司节必须从**真实 Pack 材料载荷**里写出合法路径 B 描述性 Claim，并且至少有一句把
    同一主题下的 ≥2 条这样的原子**自然组织**起来的句子。

    两个子门分别可见（§八 A1）：`A1.1` 授权面（真实载荷 + 可独立回查 + 非高风险），`A1.2`
    组织面（多 Claim 自然句，拒 raw join）。子门红 ⇒ 门红，不混成一句 detail。
    """
    from sections import narrative_schema as NS

    problems: list[str] = []
    counts = {
        "draft": 1 if output.draft is not None else 0,
        "proposals": len(output.draft.proposed_support_refs),
        "claim_candidates": len(output.draft.claim_candidates),
        "aggregate_decisions": len(output.aggregate_decisions),
        "entailment_decisions": len(output.entailment_decisions),
        "accepted_bindings": len(output.acceptance.accepted_bindings),
        "claims": len(output.claims),
        "narrative_paragraphs": len(output.narrative.paragraphs),
        "result": 1 if output.result is not None else 0,
        "material_manifest_members": len(output.draft.material_manifest.entries),
    }
    for name, value in sorted(counts.items()):
        if value <= 0:
            problems.append(f"链上 {name} 必须 > 0（实际 {value}）")
    # ---- 子门 A1.1：≥1 条合法路径 B 描述性 Claim（真实载荷 + 可回查 + 非高风险）----
    requery = _path_b_requery(output, resolver, authority.pack_set)
    path_b = _path_b_claims(output)
    sub1_problems: list[str] = []
    if not path_b:
        sub1_problems.append("没有任何已定稿 Claim 由路径 B factual 边支撑"
                             "（context 边不授权事实，不得顶替）")
    if requery["path_b_bindings_resolved"] <= 0:
        sub1_problems.append("没有任何**可独立回查**的路径 B factual accepted binding"
                             f"（问题 {requery['problem_count']} 条）")
    if requery["problem_count"]:
        sub1_problems.append(f"路径 B 载荷/locator 回查失败：{requery['problems'][:3]}")
    # §二 / §三：路径 B 只授权**非高风险**描述性原子。写入侧与绑定门已按边拒绝，验收侧**再算
    # 一遍**：一条已成立的路径 B Claim 若自身携带高风险表面，说明两层里有一层漏了。
    high_risk_claims = {cid: list(NS.high_risk_surface_tokens(text))
                        for cid, text in sorted(path_b.items())
                        if NS.high_risk_surface_tokens(text)}
    if high_risk_claims:
        sub1_problems.append("路径 B Claim 自身携带高风险表面（路径 B 只授权非高风险描述性原子）："
                             f"{high_risk_claims}")
    sub1 = _sub_gate(
        "A1.1", "至少一条合法路径 B 描述性 Claim（真实材料载荷 + exact locator 可独立回查 + 非高风险）",
        sub1_problems,
        {"path_b_claims": sorted(path_b), "path_b_claim_count": len(path_b),
         "path_b_claim_texts": [path_b[c][:80] for c in sorted(path_b)][:4],
         "high_risk_path_b_claims": high_risk_claims,
         "path_b_requery": requery})

    # ---- 子门 A1.2：≥1 个**多 Claim 自然段落**（同一主题下 ≥2 条合法非高风险描述性原子）----
    organization = _natural_organization_audit(output)
    atom_audit = _descriptive_atom_audit(output, resolver, authority.pack_set)
    sub2_problems: list[str] = []
    natural_rows = [row for row in organization["sentences"] if row["natural"]]
    # 一个「自然组织」的句子还必须真的把**同一主题下 ≥2 条路径 B 描述性原子**组织起来：否则
    # 「组织能力」可以由两条路径 A 事实的并列来伪造，而 §六 要的是公司节的描述性内容。
    usable: list[dict] = []
    for row in natural_rows:
        path_b_ids = [cid for cid in row["claim_ids"] if cid in path_b]
        topics: dict[str, list[str]] = {}
        for claim in output.claims:
            if str(claim.claim_id) in path_b_ids:
                topics.setdefault(str(claim.topic_id), []).append(str(claim.claim_id))
        same_topic = [ids for ids in topics.values() if len(ids) >= 2]
        if len(path_b_ids) >= 2 and same_topic:
            usable.append({**row, "path_b_claim_ids": path_b_ids,
                           "same_topic_path_b_ids": sorted(same_topic[0])})
    if not usable:
        reasons = []
        for row in organization["sentences"]:
            why = []
            if row["unknown_claim_ids"]:
                why.append(f"声明了本节不存在的 Claim {row['unknown_claim_ids']}")
            if not row["claims_verbatim_in_order"]:
                why.append("有条声明的 Claim 文本没有按序原样出现在句子里")
            if row["raw_join"]:
                why.append("剔除 Claim 文本后只剩标点：机械拼接（raw join）")
            if row["over_claim_bound"]:
                why.append(f"一句承载 {len(row['claim_ids'])} 条 Claim，超过"
                           f" {NS.MAX_COMPOSED_CLAIMS_PER_SENTENCE} 条上界")
            if not why:
                why.append("句子里不足 2 条路径 B 描述性原子（或它们不在同一主题下）")
            reasons.append({"sentence_id": row["sentence_id"], "excluded_by": why})
        sub2_problems.append(
            "final Narrative 里没有「同一主题下 ≥2 条合法描述性原子自然组织成一句」的句子"
            f"（多 Claim 句 {organization['multi_claim_count']} 个，可用的 0 个；"
            f"本节精确材料清单可授权的描述性句子共 {atom_audit['eligible_total']} 条，"
            "逐条排除原因见 descriptive_atom_audit）")
        sub2_rows = reasons
    else:
        sub2_rows = [{"sentence_id": r["sentence_id"], "text": r["text"],
                      "path_b_claim_ids": r["path_b_claim_ids"],
                      "same_topic_path_b_ids": r["same_topic_path_b_ids"]}
                     for r in usable[:3]]
    sub2 = _sub_gate(
        "A1.2", "至少一个多 Claim 自然段落（同一主题 ≥2 条合法非高风险描述性原子，非机械拼接）",
        sub2_problems,
        {"natural_multi_claim_sentences": sub2_rows,
         "natural_sentence_count": len(usable),
         "path_b_claim_topic_coverage": sorted(
             {str(c.topic_id) for c in output.claims
              if str(c.claim_id) in path_b}),
         "organization_audit": organization,
         "descriptive_atom_audit_eligible_total": atom_audit["eligible_total"]})

    multi = _multi_claim_sentences(output)
    numbers = _numbers_recomputed_from_authority(authority, task, output)
    evidence = {"chain_counts": counts, "path_b_requery": requery,
                "multi_claim_sentences": multi[:3],
                "multi_claim_sentence_count": len(multi),
                "descriptive_atom_audit": atom_audit,
                "numeric_authority_recompute": numbers,
                "material_reading_source": (
                    "验收侧用**同一条**权威输入（PackSet）+ 注入的 PayloadResolver 自己重建的"
                    "材料正文上下文（wmctx-1），再经共享的 `reading_for_manifest_member` 取回"
                    "逐字比对的真实正文；A1 的路径 B 回查不读写入侧的任何自报字段"),
                "gates": {"narrative_gate_version": output.gate_result.gate_version,
                          # 规则/渲染器版本挂在**门前 Draft** 上（`SectionDraft` 的两个写入侧
                          # 输入）：门结果只记录门自己的版本，不含写作规则版本。
                          "narrative_rules_version": output.draft.writer_rules_version,
                          "writer_renderer_version": output.draft.writer_renderer_version,
                          "blocking": bool(output.gate_result.blocking)},
                "persistence": dict(output.persistence)}
    return _gate("A1", "公司节：真实材料正文 → 合法路径 B Claim + 多 Claim 自然句",
                 problems, evidence, sub_gates=(sub1, sub2))


# ---------------------------------------------------------------------------
# 财务读者可见面：期间口径 / 期间表达 / 单元格披露（**独立重算**）
# ---------------------------------------------------------------------------
#
# 生产侧用 `financial_v2.period_basis` 派生「口径 → 期间表达」、用
# `sections.narrative_schema.financial_table_cell` 拼表格单元格。验收侧**不 import 那两个
# 常数或函数**，而是按同一份规则各写一遍（与 `_TERMINATORS` 同样的刻意重复）：两侧漂移时本门
# 失败，而不是被生产侧自己的实现"自证"通过。要防的缺陷正是这两类 —— 把流量指标挂在裸期间末日
# 的列名下（读者把期间量读成时点量），把代理口径的数值光秃秃地放进表格（读者把代理值读成受审的
# 精确值）。C4 之后 wire 门按**分量**核 Claim 文本（数值 / 期间表达 / 完整限定语各自逐字出现），
# 因此它挡得住「输入侧就没写出来」；但它**不读表格对象**，所以「Claim 文本写了、而渲染进表体的
# 那一格却是裸值」这种过界情形它看不到 —— 本门按**输出侧**各写一遍判据，两者缺一不可。

#: 期间口径的封闭取值（时点 / 期间）；独立重写，不 import `period_basis.PERIOD_BASES`。
_FIN_BASES = ("end", "flow")
#: 代理口径的权威状态码与显式标记词；独立重写，不 import `narrative_schema` 的常量。
_FIN_PROXY_STATUS = "CALCULATED_PROXY"
_FIN_PROXY_MARKER = "代理口径"
#: 3 月/9 月期末的季度汉字（与生产侧同一规则表，各自实现）。
_FIN_QUARTER_CN = {3: "一", 9: "三"}


def _fin_authority_date(period: str) -> tuple[int, int] | None:
    """`YYYY-MM-DD` → (年, 月)；不是这个形状返回 `None`。

    与 `financial_v2.period_basis._parse_year_month` **同一判据、各自实现**：本门不 import 它，
    两侧漂移时由本门失败，而不是被生产侧自己的实现"自证"通过。
    """
    parts = str(period or "").strip().split("-")
    if len(parts) != 3 or [len(p) for p in parts] != [4, 2, 2]:
        return None
    if not all(p.isdigit() for p in parts):
        return None
    month = int(parts[1])
    return (int(parts[0]), month) if 1 <= month <= 12 else None


def _fin_period_expression(period: str, basis: str) -> str:
    """(权威期间记号, 口径) → 读者可读的**期间表达**（独立重算，规则表与生产侧相同）。

    规则只由期间末日所在的年/月与口径决定，因此不存在按公司/年份/页码特判的余地。记号不是
    `YYYY-MM-DD` 形状时**逐字返回原记号**（不替权威发明期间措辞）。口径非法时返回空串，调用方
    据此判红。
    """
    if basis not in _FIN_BASES:
        return ""
    ymd = _fin_authority_date(period)
    if ymd is None:
        return str(period or "")
    year, month = ymd
    if basis == "end":
        if month == 12:
            return f"{year}年末"
        if month == 6:
            return f"{year}年半年末"
        if month in (3, 9):
            return f"{year}年{_FIN_QUARTER_CN[month]}季度末"
        return f"{year}年{month}月末"
    if month == 12:
        return f"{year}年度"
    if month == 9:
        return f"{year}年前三季度"
    if month == 6:
        return f"{year}年半年度"
    if month == 3:
        return f"{year}年一季度"
    return f"{year}年1-{month}月"


def _fin_basis_from_formula(code: str) -> str:
    """指标事实的期间口径**独立重算**：读公式定义自己的 `period_requirement`。

    只有 `end` 是时点量；`flow` / `flow/end` / `flow/avg` / `yoy_*` 的呈现期间一律是「期间」。
    取不到公式定义时返回空串（本门据此只核「声明口径 + 表达」，不替它默认一个方向）。
    """
    try:
        from financial_v2 import formulas as _FF
        definition = _FF.get_formula(str(code))
    except Exception:
        return ""
    requirement = str(getattr(definition, "period_requirement", "") or "").strip()
    if not requirement:
        return ""
    return "end" if requirement == "end" else "flow"


def _fin_period_text(fact) -> str:
    """该事实在**给读者看的文本**里的期间说法（独立重写，规则与生产侧相同）。

    优先权威自己声明的 `period_label`（由 `financial_v2.period_basis` 从期间记号与口径
    确定性派生），取不到时**逐字退回**它的期间记号 `period`——**不替权威发明措辞**。
    不能一律用 `period`：流量指标的 `period` 是期间末日（`2025-12-31`），把期间量写成
    「2025-12-31的…」会让读者把期间量读成时点量。
    """
    label = str(getattr(fact, "period_label", "") or "").strip()
    if label:
        return label
    return str(getattr(fact, "period", "") or "").strip()


def _fin_cell_components(fact) -> tuple[tuple[str, str], ...]:
    """一格财务单元格的**分量**：(判据名, 必须逐字出现在配对 Claim 文本里的文本)。

    **独立重算**，不 import `sections.narrative_schema.financial_cell_components`：本门是
    被审产物的**外部**读者，两侧漂移时必须由本门失败，而不是被生产侧自己的实现"自证"通过
    （`_fin_period_expression` / `_fin_cell_text` 同理）。

    C4（本批定点返修）把判据从「整格文本是 Claim 的连续子串」改成这里的**分量**，理由是
    旧判据让**标点**承担了判据：`narrative_schema.financial_table_cell` 用权威自己的标点把
    数值与限定语拼起来（`3.42。代理口径（…）`），而写作提示词要求 `claim_text` 中间不得有
    句末标点，两条要求**互斥**，于是 24 条本来正确的代理事实被一个标点差异整体作废。
    拆开之后每条要求只针对一个语义分量，标点不再承担任何判据。

    三个分量缺一不可：少了数值 ⇒ 这一格无值可陈；少了期间表达 ⇒ 读者不知道这是哪一期的数
    （同一指标跨期成行，期间是**行身份**的一部分）；少了限定语 ⇒ 代理口径被读成受审的精确值。
    限定语**不因「写在 citation 链里」而免除**：引用链不进表格正文，读者看不到。
    """
    value = (str(getattr(fact, "display", "") or "").strip()
             or str(getattr(fact, "value_text", "") or "").strip())
    parts = [("数值", value), ("期间表达", _fin_period_text(fact))]
    if str(getattr(fact, "status", "") or "").strip() == _FIN_PROXY_STATUS:
        parts.append(("代理口径的限定语",
                      str(getattr(fact, "note", "") or "").strip() or _FIN_PROXY_MARKER))
    return tuple(parts)


def _fin_cell_text(fact) -> str:
    """表格单元格应有的可见文本（数值渲染 + 代理时的口径限定语），**独立重算**。

    `FORMULA_REVIEW` §0 要求代理口径在结果里显式披露。本函数问的是一个**渲染**问题：读者在
    那一格里**看到的**是不是权威自己的可见文本（数值 + 代理时的完整限定语）。它与 wire 门侧的
    判据是**两件事**，不要互相顶替：

      * wire 门（C4 之后）按 `financial_cell_components` 的**分量**核对 Claim 文本是否承担了
        数值 / 期间表达 / 完整限定语——它管的是**输入侧**（写不出来就不许进表）；
      * 本函数管的是**输出侧**：表格对象里那一格的实际文本必须逐字等于权威自己的可见文本
        （`3.42。代理口径（…）`）。因此「单元格被换成裸值、而限定语只留在 Claim 文本里」这种
        过界情形由本函数点出，wire 门看不到（它不读表格对象）。
    """
    value = (str(getattr(fact, "display", "") or "").strip()
             or str(getattr(fact, "value_text", "") or "").strip())
    if str(getattr(fact, "status", "") or "").strip() != _FIN_PROXY_STATUS:
        return value
    qualifier = str(getattr(fact, "note", "") or "").strip() or _FIN_PROXY_MARKER
    return f"{value}。{qualifier}"


def _financial_readability(output, authority) -> dict:
    """§七 2 / §八 A2：财务节「可读」的机器判据 —— 段落与表格的**结构**，不是「非空」。

    逐项由产物侧重算：

      1. 可读结构必须存在：至少一个段落，或至少一张表（两者皆无 ⇒ 不成立）；
      2. 正文句：声明的 Claim 必须在本节 current 集合内；多 Claim 句必须逐字、按序包含每条声明
         的 Claim 文本，且剔除后剩下的组织成分必须含汉字（`A；B。` 式机械拼接即失败）；一句不得
         超过 `NS.MAX_COMPOSED_CLAIMS_PER_SENTENCE` 条；句子里不得出现未由所声明 Claim 逐字
         授权的表面（数字 / 否定 / 状态 / 表格关系 / 因果 / **趋势结论**）；
      3. 表格：表头 =「指标 + ≥2 期间」且列名必须能由权威 artifact **自己声明的期间**派生；每个
         数据行的单元格数与期间列数一致，**非空**单元格与 Claim 按非空顺序逐位对应；该 Claim
         必须恰有一条 `financial_pack` factual 事实身份，且该事实的 container 就是本 artifact、
         `fact_id` 在场；表/行的 `unit` 与表的 `entity_scope` 不得为空；
         单元格与配对 Claim 文本的关系按**分量**核（C4，见第 5 条之前的说明）：该权威事实的
         **数值 / 期间表达 / 完整代理口径限定语**必须各自逐字出现在配对 Claim 的文本里，
         **不**要求「整格文本是 Claim 的连续子串」——那会把值与限定语之间的那个标点变成
         第四条隐含要求，而写作侧被明令禁止在 `claim_text` 中间写句末标点；
      4. **期间口径与期间表达**（M930-3 返修 P3）：每条进入表格的权威事实必须**自己声明**期间口径
         （`end` 时点 / `flow` 期间，封闭取值）；列名必须**等于**由该事实自己的期间记号与口径
         **独立重算**出来的期间表达 —— 于是 `2025-12-31` 这种裸期间末日不可能通过，流量指标也
         不可能被挂在时点列名下；指标事实的口径还要与公式定义自己的 `period_requirement` 复算一致；
      5. **读者可见的口径披露**（同上）：单元格必须**等于**该权威事实应有的可见文本（数值渲染 +
         代理时的口径限定语）。只查「单元格是 Claim 的逐字子串」会放过光秃秃的代理值 —— 披露
         必须出现在读者读数字的那一格，而不是只藏在 Claim 文本或 `claim_id` 链里；
      6. 呈现位置：本节每条 Claim 必须**恰好**一个呈现位置（正文句声明 或 表格单元格）。重复、
         遗漏、引用了不存在的 Claim 都是失败 —— 这正是「不覆盖」与「重复计数」的分界；
      7. 表级文字（caption / 表头标签）里的表面必须能由**权威事实文本**逐字授权（表由权威事实
         确定性构造，不得由 LLM 写文案）。
    """
    from sections import narrative_schema as NS

    problems: list[str] = []
    claim_text = {str(c.claim_id): str(c.text) for c in output.claims}
    claim_by_id = {str(c.claim_id): c for c in output.claims}
    facts = tuple(getattr(authority.artifact, "facts", ()) or ())
    fact_by_id = {str(getattr(f, "fact_id", "")): f for f in facts}
    artifact_id = str(getattr(authority.artifact, "artifact_id", ""))
    declared_periods = [str(p) for p in (getattr(authority.artifact, "periods", ()) or ())]
    # `period_label` 也是权威事实自己的字段（`ffpa-2` 起的 `period_label` / 期间表达）：列名写
    # 期间表达时，它必须能由**该事实自己的权威字段**逐字授权。它不替代下面的独立重算 ——
    # 「列名等于由 (period, basis) 重算出来的表达」是另一条判据，两条都要过。
    authority_texts = [str(getattr(f, key, "") or "")
                       for f in facts
                       for key in ("text", "display", "value_text", "label", "period",
                                   "period_label")]
    coordinates = _binding_fact_ids(output)

    if not output.narrative.paragraphs and not output.narrative.tables:
        problems.append("财务节既没有段落也没有表格：可读内容结构不成立（`body_chars > 0` 不是可读性）")

    prose_ids: list[str] = []
    prose_rows: list[dict] = []
    for paragraph in output.narrative.paragraphs:
        for sentence in paragraph.sentences:
            ids = [str(i) for i in sentence.claim_ids]
            prose_ids += ids
            row = {"sentence_id": sentence.sentence_id, "kind": sentence.sentence_kind,
                   "claim_ids": ids, "text": sentence.text}
            unknown = [i for i in ids if i not in claim_text]
            if unknown:
                problems.append(f"{sentence.sentence_id}: 声明了本节不存在的 Claim {unknown}")
                prose_rows.append({**row, "excluded_by": ["未知 Claim"]})
                continue
            texts = [claim_text[i] for i in ids]
            # 组合句走边界感知核验（与冻结门 `ng-8` 同口径、各自实现）：整句一次扫描会在
            # 「Claim 段 | 组织段」的接缝上合成出声明 Claim 里并不存在的表面。
            if len(ids) > 1:
                surfaces = NS.unauthorized_surfaces_within_claims(sentence.text, texts)
            else:
                surfaces = NS.unauthorized_surfaces(sentence.text, texts)
            if surfaces:
                problems.append(f"{sentence.sentence_id}: 正文出现未由所声明 Claim 逐字授权的表面"
                                f" {list(surfaces)[:6]}（含趋势/比较结论）")
            residue = None
            if len(ids) > 1:
                residue = _organization_residue(sentence.text, texts)
                if residue is None:
                    problems.append(f"{sentence.sentence_id}: 有条声明的 Claim 文本没有按序原样出现"
                                    "在句子里（绑定与正文不一致）")
                elif not _has_cjk(residue):
                    problems.append(f"{sentence.sentence_id}: 是 Claim 文本的机械拼接（raw join："
                                    "剔除 Claim 文本后只剩标点）")
                if _has_internal_terminator(sentence.text):
                    problems.append(f"{sentence.sentence_id}: 含句中句末标点，实际是两个句子首尾"
                                    "相接（`A。同时B。` 不是一条多 Claim 自然句）")
            if len(ids) > NS.MAX_COMPOSED_CLAIMS_PER_SENTENCE:
                problems.append(f"{sentence.sentence_id}: 一句承载 {len(ids)} 条 Claim，超过"
                                f" {NS.MAX_COMPOSED_CLAIMS_PER_SENTENCE} 条上界")
            prose_rows.append({**row, "organizing_residue": residue,
                               "unauthorized_surfaces": list(surfaces)})

    tabled_ids = [str(i) for i in NS.tabled_claim_ids(output.narrative.tables)]
    table_rows: list[dict] = []
    for table in output.narrative.tables:
        header = [str(h) for h in table.header]
        periods_in_header = header[1:]
        if len(header) < 3 or not periods_in_header:
            problems.append(f"表 {table.table_id}: 表头 {header} 不是「指标 + ≥2 期间」"
                            f"（实际 {len(header)} 列）")
        if header and header[0] != NS.FINANCIAL_TABLE_LABEL_HEADER:
            problems.append(f"表 {table.table_id}: 首列表头必须是"
                            f" {NS.FINANCIAL_TABLE_LABEL_HEADER!r}（实际 {header[0]!r}）")
        if declared_periods:
            # 列名是**期间表达**（`2025年度` / `2025年末`）而不是裸期间记号，因此这里不复算"列名
            # ∈ 声明期间"，而是复算「列名可由某个权威声明的期间派生」——逐格不等式的**精确**配对
            # 由下面按事实自己的期间做（列名与事实期间必须一一对上）。
            derivable = {_fin_period_expression(p, basis)
                         for p in declared_periods for basis in _FIN_BASES}
            derivable |= {str(p) for p in declared_periods}
            undeclared = [t for t in periods_in_header if t not in derivable]
            if undeclared:
                problems.append(f"表 {table.table_id}: 列名 {undeclared} 不是权威 artifact"
                                f" 声明期间 {declared_periods[:6]} 的任何期间表达")
        for name, value in (("unit", table.unit), ("entity_scope", table.entity_scope)):
            if not str(value or "").strip():
                problems.append(f"表 {table.table_id}: {name} 不得为空（表级身份必须可追溯）")
        for surface in NS.unauthorized_surfaces(
                f"{table.caption}\n" + "\n".join([*header, *(
                    str(row.label) for row in table.rows)]), authority_texts):
            problems.append(f"表 {table.table_id}: 表级文字出现权威事实未授权的表面 {surface!r}")
        for row in table.rows:
            cells = [str(c) for c in row.cells]
            ids = [str(i) for i in row.claim_ids]
            if len(cells) != len(periods_in_header):
                problems.append(f"表 {table.table_id} 行 {row.label!r}: 单元格数 {len(cells)}"
                                f" != 期间列数 {len(periods_in_header)}")
            filled = [(pos, cell) for pos, cell in enumerate(cells) if cell.strip()]
            if len(filled) != len(ids):
                problems.append(f"表 {table.table_id} 行 {row.label!r}: 非空单元格数 {len(filled)}"
                                f" 与 Claim 数 {len(ids)} 不一致（按非空顺序逐位对应）")
            for (pos, cell), cid in zip(filled, ids):
                if cid not in claim_text:
                    problems.append(f"表 {table.table_id} 行 {row.label!r}: 单元格引用了本节不存在的"
                                    f" Claim {cid}")
                    continue
                if not NS.claim_citation_ids(claim_by_id[cid]):
                    problems.append(f"表 {table.table_id}: Claim {cid} 没有可派生的 citation ID")
                financial = [k for k in coordinates.get(cid, ()) if k[0] == "financial_pack"]
                if len(financial) != 1:
                    problems.append(f"表 {table.table_id} 行 {row.label!r}: Claim {cid} 必须恰有"
                                    f"一条 financial_pack factual 事实身份（实际 {len(financial)}）")
                    continue
                _kind, container, fact_id = financial[0]
                fact = fact_by_id.get(str(fact_id))
                if str(container) != artifact_id or fact is None:
                    problems.append(f"表 {table.table_id}: Claim {cid} 的事实坐标"
                                    f" ({container}, {fact_id}) 不在本 authority artifact"
                                    f" {artifact_id} 内")
                    continue
                # C4：单元格与配对 Claim 文本的关系按**分量**核（数值 / 期间表达 / 完整限定语
                # 各自逐字出现），**不**要求「整格文本是 Claim 的连续子串」。旧判据让标点承担了
                # 判据：权威自己的 `financial_table_cell` 用「。」把数值与限定语拼起来，而写作侧
                # 被明令禁止在 `claim_text` 中间写句末标点——两条要求互斥。分量由本门按权威事实
                # 自己的字段**独立重算**（`_fin_cell_components`，不 import 生产侧实现）。
                for label, needle in _fin_cell_components(fact):
                    if needle and needle not in claim_text[cid]:
                        problems.append(
                            f"表 {table.table_id} 行 {row.label!r}: 配对 Claim {cid} 的文本没有逐字"
                            f"承担该权威事实的{label} {needle!r}（判据是分量，不由标点承担）")
                # P3：期间口径 / 期间表达 / 读者可见的口径披露，全部按**权威事实自己的字段**
                # 独立重算。这三条各自都能被「单元格是权威可见文本」放过（那一条只核输出侧），
                # 因此必须单列。
                fact_period = str(getattr(fact, "period", "") or "")
                declared_basis = str(getattr(fact, "period_basis", "") or "").strip()
                column_text = periods_in_header[pos] if pos < len(periods_in_header) else ""
                if declared_basis not in _FIN_BASES:
                    problems.append(
                        f"表 {table.table_id} 行 {row.label!r}: 事实 {fact_id} 没有声明期间口径"
                        f"（period_basis={declared_basis!r}，封闭取值 {list(_FIN_BASES)}）："
                        "读者无法判断这个数值说的是时点还是期间")
                else:
                    if str(getattr(fact, "kind", "") or "") == "calculation":
                        recomputed_basis = _fin_basis_from_formula(
                            str(getattr(fact, "code", "") or ""))
                        if recomputed_basis and recomputed_basis != declared_basis:
                            problems.append(
                                f"表 {table.table_id} 行 {row.label!r}: 事实 {fact_id} 声明的口径"
                                f" {declared_basis!r} 与公式定义 `period_requirement` 独立复算的"
                                f"口径 {recomputed_basis!r} 不一致")
                    expected_text = _fin_period_expression(fact_period, declared_basis)
                    if column_text != expected_text:
                        problems.append(
                            f"表 {table.table_id} 行 {row.label!r}: 单元格所属列 {column_text!r}"
                            f" 与该事实期间 {fact_period!r} 不一致（口径 {declared_basis!r} 下的"
                            f"期间表达应为 {expected_text!r}）：列名写期间末日时，读者会把期间量"
                            "误读成时点量")
                expected_cell = _fin_cell_text(fact)
                if cell != expected_cell:
                    problems.append(
                        f"表 {table.table_id} 行 {row.label!r}: 单元格 {cell!r} 不等于该权威事实应有"
                        f"的可见文本 {expected_cell!r}"
                        + ("（代理口径的限定语必须出现在读者读数字的那一格，"
                           "只藏在 Claim 文本或 claim_id 链里不算披露）"
                           if str(getattr(fact, "status", "") or "") == _FIN_PROXY_STATUS else ""))
            table_rows.append({"table_id": table.table_id, "label": str(row.label),
                               "periods": periods_in_header, "claim_ids": ids,
                               "filled_cells": [cell for _pos, cell in filled],
                               "units": str(row.unit)})

    duplicates = sorted(set(prose_ids) & set(tabled_ids))
    if duplicates:
        problems.append(f"这些 Claim 同时出现在正文句与表格里（呈现位置必须恰好一个）：{duplicates}")
    unknown_positions = sorted((set(prose_ids) | set(tabled_ids)) - set(claim_text))
    if unknown_positions:
        problems.append(f"正文/表格引用了本节不存在的 Claim：{unknown_positions}")
    unplaced = sorted(set(claim_text) - set(prose_ids) - set(tabled_ids))
    excuses = _unpresented_claims(output, unplaced)
    if excuses["unexcused"]:
        problems.append(f"这些 Claim 既不在正文也不在表格里，也没有「本节不呈现」的封闭理由："
                        f"{excuses['unexcused'][:6]}")
    if excuses["table_reason_but_no_table"]:
        problems.append("这些 Claim 的 omitted 理由是 presented_as_table_row，但表格里没有它们："
                        f"{excuses['table_reason_but_no_table'][:6]}")
    # 反向：表格承载的 Claim，其去向必须是 omitted/presented_as_table_row（不另行陈述事实）。
    for cid in sorted(set(tabled_ids)):
        disp = next((d for d in (getattr(output, "claim_narrative_dispositions", ()) or ())
                     if str(getattr(d, "claim_id", "")) == cid), None)
        if disp is None:
            problems.append(f"表格承载的 Claim {cid} 没有 Claim 去向记录")
        elif str(getattr(disp, "disposition", "")) != "omitted" \
                or str(getattr(disp, "reason_code", "")) != "presented_as_table_row":
            problems.append(f"表格承载的 Claim {cid} 的去向不是 omitted/presented_as_table_row"
                            f"（实际 {getattr(disp, 'disposition', None)!r}/"
                            f"{getattr(disp, 'reason_code', None)!r}）")

    return {"problems": problems[:12], "problem_count": len(problems),
            "unpresented": {"excused": excuses["excused"],
                            "disposition_count": excuses["disposition_count"]},
            "paragraphs": len(output.narrative.paragraphs),
            "tables": len(output.narrative.tables),
            "tabled_claim_count": len(tabled_ids), "prose_claim_count": len(set(prose_ids)),
            "claim_count": len(claim_text),
            "prose_sentences": prose_rows[:6], "table_rows": table_rows[:8],
            "fin_table_min_periods": NS.FINANCIAL_TABLE_MIN_PERIODS,
            "declared_periods": declared_periods[:12]}


def _gate_a2_financial(output, authority, task) -> GateOutcome:
    """A2：财务节走**同一条**主链的路径 A（预验证事实），正文可读且数字全部来自权威。"""
    problems: list[str] = []
    counts = {
        "draft": 1 if output.draft is not None else 0,
        "claim_candidates": len(output.draft.claim_candidates),
        "aggregate_decisions": len(output.aggregate_decisions),
        "entailment_decisions": len(output.entailment_decisions),
        "accepted_bindings": len(output.acceptance.accepted_bindings),
        "claims": len(output.claims),
        "result": 1 if output.result is not None else 0,
    }
    for name, value in sorted(counts.items()):
        if value <= 0:
            problems.append(f"链上 {name} 必须 > 0（实际 {value}）")
    path_a = [b for b in output.acceptance.accepted_bindings
              if b.support_semantics == "factual" and b.authority_kind == "financial_pack"]
    if not path_a:
        problems.append("没有任何 financial_pack 的 factual accepted binding"
                        "（财务事实只能走路径 A 预验证）")
    if len(output.draft.material_manifest.entries) != 0:
        problems.append("财务分支没有 exact ResearchMaterial 边界，材料清单必须为空")
    numbers = _numbers_recomputed_from_authority(authority, task, output)
    if numbers["unauthorized_count"]:
        problems.append(f"正文数字未经权威事实逐字授权 {numbers['unauthorized'][:6]}"
                        "（LLM 不得计算或改写数值）")
    body_chars = len(output.result.markdown if output.result is not None else "")
    if body_chars <= 0:
        problems.append("财务节没有正文（人读内容门不成立）")
    # §七 2 / §八 A2：「可读」必须由**结构**证明（段落或表格，且单元格/句子逐位可核），
    # 不能再用 `body_chars > 0` 代替 —— 24 条 Claim 拼成一句 574 字长句同样满足「非空」。
    readability = _financial_readability(output, authority)
    problems += readability["problems"]
    evidence = {"chain_counts": counts,
                "financial_path_a_bindings": len(path_a),
                "material_manifest_members": len(output.draft.material_manifest.entries),
                "numeric_authority_recompute": numbers,
                "readability": readability,
                "paragraphs": len(output.narrative.paragraphs),
                "tables": len(output.narrative.tables),
                "markdown_chars": body_chars,
                "artifact_id": str(getattr(authority.artifact, "artifact_id", "")),
                "fact_count": len(getattr(authority.artifact, "facts", ()) or ()),
                "note_gap": bool(getattr(authority, "note_gap", None)),
                "gates": {"narrative_gate_version": output.gate_result.gate_version,
                          "narrative_rules_version": output.draft.writer_rules_version,
                          "writer_renderer_version": output.draft.writer_renderer_version}}
    return _gate("A2", "财务节：同一条主链上的路径 A 事实 + 可读正文 + 数字零计算",
                 problems, evidence)


def _gate_a3_industry(output, task) -> GateOutcome:
    """A3：行业节有合法材料就写，否则必须留下 typed 缺口——**不得**伪造覆盖。"""
    problems: list[str] = []
    claims = len(output.claims)
    gaps = len(output.result.unresolved)
    covered_topics = {c.topic_id for c in output.claims}
    gap_topics = {u.topic_id for u in output.result.unresolved}
    if claims > 0:
        unsupported = [c.claim_id for c in output.claims if not c.accepted_binding_ids]
        if unsupported:
            problems.append(f"有 Claim 没有任何 accepted binding {unsupported[:6]}（伪造覆盖）")
    elif gaps <= 0:
        problems.append("本节既没有 Claim 也没有 typed 缺口：这不是「少写」，而是没有如实交代")
    missing = sorted(set(task.topic_ids) - covered_topics - gap_topics)
    if missing:
        problems.append(f"本节 topic 既未覆盖也未登记缺口：{missing}")
    # ⑥：年报**转述**的行业数据不得被写成「系统已独立联网核验」的结果。本判据在验收侧**独立**
    # 复算（`_SYSTEM_PROVENANCE_PHRASES` 自带一份元组），不回放写入侧结论。
    self_reported = _self_reported_provenance(output.narrative, output.claims)
    if self_reported:
        problems.append(
            "本节正文替系统自报检索 / 核验（这些字面不在该句声明的 Claim 文本里）："
            f"{self_reported[:3]}（年报转述的行业数据不得标作系统已独立联网核验）")
    evidence = {"claims": claims, "unresolved": gaps,
                "covered_topics": sorted(covered_topics), "gap_topics": sorted(gap_topics),
                "status": str(output.result.status),
                "claim_types": sorted({str(c.claim_type) for c in output.claims}),
                "self_reported_provenance": len(self_reported),
                "path_b_claims": sum(
                    1 for b in output.acceptance.accepted_bindings
                    if b.authorization_path == "path_b_material_derived"),
                "path_a_claims": sum(
                    1 for b in output.acceptance.accepted_bindings
                    if b.authorization_path == "path_a_prevalidated")}
    return _gate("A3", "行业节：有合法材料就写，否则 typed 缺口（不伪造覆盖）",
                 problems, evidence)


def _reassemble_from_readback(run: "RunState") -> dict:
    """用**读回**的门后对象重建每一节，再组装一次报告，与实得报告比较。

    诚实边界：`gate_result` / `evaluation` / `binding` **不在**当前链的持久化面里（它们是门前
    门与章级评估的产物，链容器设计上不装它们）。因此本复算把它们沿用实得值，而 Draft / 决定 /
    accepted binding / 定稿 Claim / final Narrative / Result / FND 全部取自读回——组装器自己在
    `(draft, authority)` 上重算决定链并与读回值逐项比对，所以「读回值被改坏」会在这里被拒。
    """
    from sections import report_assembler as RA

    if run.report is None:
        return {"status": "skipped",
                "detail": "本次没有实得报告（组装未成功），读回重组无从比较"}
    if set(run.readback) != set(run.sections) or not run.sections:
        return {"status": "fail",
                "detail": (f"读回集合 {sorted(run.readback)} 与产出集合 "
                           f"{sorted(run.sections)} 不一致")}
    for section_id, section in run.sections.items():
        loaded = run.readback[section_id]
        live = section
        checks = {
            "draft_identity_body": loaded.draft.identity_body() == live.draft.identity_body(),
            "claim_ids": (tuple(c.claim_id for c in loaded.claims)
                          == tuple(c.claim_id for c in live.claims)),
            "claim_texts": (tuple(c.text for c in loaded.claims)
                            == tuple(c.text for c in live.claims)),
            "narrative_identity_body": (loaded.narrative.identity_body()
                                        == live.narrative.identity_body()),
            "result_markdown": loaded.result.markdown == live.result.markdown,
            "result_markdown_fingerprint": (loaded.result.markdown_fingerprint
                                            == live.result.markdown_fingerprint),
            "accepted_bindings": (len(loaded.accepted_bindings)
                                  == len(live.acceptance.accepted_bindings)),
            # 读回的 accepted 集合必须**恰好等于**用读回的 (Draft, 决定束) 重算出来的那一束：
            # 库里落下的边与库里落下的决定必须自洽（不是「两边都少了几条就都算对」）。
            # 比**集合**不比顺序：读回侧的行序由存储决定，而重算侧的顺序是 `DraftAcceptance`
            # 自己的规范序 —— 顺序差异不是链的差异，「少/多了一条边」才是。
            "accepted_bindings_match_recomputed": (
                sorted(b.accepted_support_binding_id for b in loaded.accepted_bindings)
                == sorted(b.accepted_support_binding_id
                          for b in _acceptance_from_readback(loaded).accepted_bindings)),
            "fact_narrative_dispositions": (len(loaded.fact_narrative_dispositions)
                                            == len(live.dispositions)),
            "claim_narrative_dispositions": (len(loaded.claim_narrative_dispositions)
                                             == len(live.claim_narrative_dispositions)),
        }
        if not all(checks.values()):
            return {"status": "fail",
                    "detail": f"section={section_id!r} 读回对象与写入侧不逐项相等："
                              f"{[k for k, v in checks.items() if not v]}",
                    "checks": checks}
    inputs = tuple(RA.SectionAssemblyInput(
        task=run.inputs.tasks[s], authority=run.authority_of(s),
        draft=run.readback[s].draft, gate_result=run.sections[s].gate_result,
        aggregate_decisions=tuple(run.readback[s].aggregate_decisions),
        entailment_decisions=tuple(run.readback[s].entailment_decisions),
        acceptance=_acceptance_from_readback(run.readback[s]),
        claims=tuple(run.readback[s].claims), narrative=run.readback[s].narrative,
        claim_narrative_dispositions=tuple(run.readback[s].claim_narrative_dispositions),
        dispositions=tuple(run.readback[s].fact_narrative_dispositions),
        result=run.readback[s].result, evaluation=run.sections[s].evaluation,
        binding=run.sections[s].binding,
        # §12.4.4 第 4 步：读回链上用的是**从库里读回来的**那一份决定（不是内存里的）。
        # 组装器自己重算状态，因此「库里少了一条 / 读回来的锚点是旧的」都会在这里当场显形。
        final_sentence_decisions=tuple(run.readback[s].final_sentence_decisions),
    ) for s in SECTION_ORDER)
    report = RA.assemble_report(
        projection=run.inputs.projection, scope=run.scope, section_inputs=inputs,
        version_inputs=run.version_inputs, generated_at=run.generated_at)
    same = (report.report_id == run.report.report_id
            and report.markdown == run.report.markdown
            and report.report_version == run.report.report_version)
    evidence = {"readback_report_id": report.report_id,
                "live_report_id": run.report.report_id,
                "markdown_equal": report.markdown == run.report.markdown}
    if not same:
        # 不一致时必须能指名道姓：逐字段列出规范载荷的差异（证据，不改变判定口径）。
        evidence["payload_diff"] = _payload_field_diff(run.report, report)
    return {"status": "pass" if same else "fail",
            "detail": ("读回重组得到同一份报告" if same
                       else "读回重组出的报告与实得报告不同"),
            **evidence}


def _payload_field_diff(live, readback) -> dict:
    """规范载荷的逐字段差异（只读证据）：字段名 → 差异摘要。"""
    from sections import narrative_schema as NS

    a = NS.assembled_payload_body(**NS.report_payload_kwargs(live))
    b = NS.assembled_payload_body(**NS.report_payload_kwargs(readback))
    out: dict = {}
    for key in a:
        if a[key] == b[key]:
            continue
        if key != "sections":
            out[key] = {"live": a[key], "readback": b[key]}
            continue
        per: dict = {}
        if len(a[key]) != len(b[key]):
            per["__length__"] = [len(a[key]), len(b[key])]
        for sa, sb in zip(a[key], b[key]):
            bad = [k for k in sa if sa[k] != sb[k]]
            if bad:
                per[sa["section_id"]] = bad
        out[key] = per or "集合相同但顺序不同"
    return out


def _acceptance_from_readback(loaded):
    """读回的 `(Draft, 决定束)` → `AB.DraftAcceptance`：**重算**，不重装、不自报。

    v2 链刻意不落一张「拒绝表」：拒绝留痕的 source of truth 是**决定自身**（失败的 aggregate
    决定 / `verdict='rejected'` 的 entailment 决定），store 侧据此只建两个只读投影视图。因此
    读回侧也不得拿一个「只装 accepted、拒绝集留空」的容器顶上去——`DraftAcceptance` 的
    `subject_keys` 必须完备（每个 subject revision 恰一个结论），accepted 集**与**拒绝集必须
    同时成立。这里调用与写入侧、组装器同一实现 `AB.accept_draft_bindings`，输入是**读回的**
    Draft 与**读回的**决定束；组装器随后会在同一 `(draft, authority)` 上再重算一次并逐项比对，
    所以「读回值被改坏」仍然会被拒。
    """
    from sections import accepted_binding as AB

    return AB.accept_draft_bindings(
        loaded.draft, tuple(loaded.aggregate_decisions),
        entailment_decisions=tuple(loaded.entailment_decisions))


def _gate_a4_persistence(db: Path, run: "RunState") -> GateOutcome:
    """A4：完整 v2 链落进正式 store；独立读回重组同一 Section/Report；失败不留半事务。"""
    from sections import store as ST

    problems: list[str] = []
    counted: dict[str, int] = {}
    row_counts: dict[str, dict[str, int]] = {}
    missed = [s for s in SECTION_ORDER if s not in run.sections]
    if missed:
        problems.append(f"有章节未产出，其链未落库（不把「没产出」算成「链完整」）：{missed}")
    for section_id in sorted(run.sections):
        section = run.sections[section_id]
        draft_id = section.draft.draft_id
        counted[section_id] = run.commit_counts.get(section_id, -1)
        if counted[section_id] != 1:
            problems.append(f"section={section_id!r} 的 current 链提交次数必须恰为 1"
                            f"（实际 {counted[section_id]}）")
        row_counts[section_id] = _family_counts(db, draft_id)
        expected = {
            "current_section_material_manifest_v2": 1,
            "current_section_wmpd_v2": len(section.draft.material_disposition_ids),
            "current_section_subject_v2": (len(section.draft.claim_candidate_ids)
                                           + len(section.draft.narrative_draft_unit_ids)),
            "current_section_proposal_v2": len(section.draft.proposed_support_ids),
            "current_section_binding_decision_v2": len(section.aggregate_decisions),
            "current_section_entailment_decision_v2": len(section.entailment_decisions),
            "current_section_accepted_binding_v2": len(section.acceptance.accepted_bindings),
            "current_section_fnd_v2": len(section.dispositions),
            "current_section_claim_narrative_disposition_v2": len(
                section.claim_narrative_dispositions),
            "current_section_claim_v2": len(section.claims),
            "current_section_narrative_v2": 1,
            "current_section_unresolved_v2": len(section.draft.unresolved_ids),
            "current_section_result_v2": 1,
        }
        for family, want in sorted(expected.items()):
            got = row_counts[section_id].get(family)
            if got != want:
                problems.append(f"{section_id}/{family} 行数 {got} != 产出对象计数 {want}")
        if run.readback.get(section_id) is None:
            problems.append(f"section={section_id!r} 读回为空")

    reassembly = _reassemble_from_readback(run) if not problems else {
        "status": "skipped", "detail": "本节已有其它 A4 问题，读回重组只在无问题时执行"}
    # 「章节读回通过」不得冒充「整本通过」：整本读回重组必须**真的跑过并通过**。`skipped`
    # （例如没有实得报告可比较）不是 pass，必须显式记成问题——否则「报告从未组装过」的
    # 一次运行会拿到一个 A4 绿门，而它其实一次整本读回重算都没做过。
    if reassembly["status"] != "pass":
        problems.append(f"整本读回重组未通过（status={reassembly['status']}）："
                        f"{reassembly.get('detail')}")

    rollback = _rollback_probe(run) if run.rollback is not None else {"status": "skipped"}
    if rollback["status"] != "pass":
        problems.append(f"半事务反例未成立：{rollback.get('detail')}")

    evidence = {"commit_counts": counted, "row_counts": row_counts,
                "readback_reassembly": reassembly, "rollback_probe": rollback,
                "db_relpath": str(db.name)}
    return _gate("A4", "持久化：完整 v2 链 + 独立读回重组 + 失败不留半事务",
                 problems, evidence)


def _family_counts(db: Path, draft_id: str) -> dict[str, int]:
    """**独立**探针：直连 sqlite 数行数（不经 store 的读入口，因此与「读回」是两条证据）。"""
    from sections import store as ST

    families = (*ST.V2_WRITER_SIDE_FAMILIES, *ST.V2_DECISION_FAMILIES,
                *ST.V2_POST_GATE_FAMILIES)
    conn = sqlite3.connect(str(db))
    try:
        out: dict[str, int] = {}
        for family in families:
            out[family] = int(conn.execute(
                f"SELECT COUNT(*) FROM {family} WHERE draft_id = ?", (draft_id,)).fetchone()[0])
        return out
    finally:
        conn.close()


def _rollback_probe(run: "RunState") -> dict:
    """在**另一个** run-local 空库上注入第 4 行插入失败：各 family 必须 0 行。"""
    from sections import store as ST

    if run.rollback is None:
        return {"status": "skipped"}
    db = run.rollback["db"]
    counts = _all_family_counts(db)
    if any(counts.values()):
        return {"status": "fail", "detail": f"失败后库里仍有行：{counts}"}
    if not run.rollback.get("raised"):
        return {"status": "fail", "detail": "注入的失败没有抛出（提交被吞掉了）"}
    return {"status": "pass", "detail": "注入失败后各 family 0 行（整事务回滚）",
            "family_counts": counts, "insert_attempts": run.rollback.get("insert_attempts")}


def _all_family_counts(db: Path) -> dict[str, int]:
    from sections import store as ST

    families = (*ST.V2_WRITER_SIDE_FAMILIES, *ST.V2_DECISION_FAMILIES,
                *ST.V2_POST_GATE_FAMILIES)
    conn = sqlite3.connect(str(db))
    try:
        return {family: int(conn.execute(f"SELECT COUNT(*) FROM {family}").fetchone()[0])
                for family in families}
    finally:
        conn.close()


def _citation_requery(run: "RunState") -> dict:
    """引用/定位子**可回查**：逐条支撑边的来源按**它自己那条路径**重新解析。

    两条 factual 路径的可回查物**不同源**，因此本函数只按路径各自的口径取：
      * 路径 B：该边自己的 manifest 成员的 payload 定位子必须能被 resolver 重新解析
        （dangling / 哈希 / locator / 版本任一不符即失败），成员与边在 `material_id` /
        `source_identity` 上逐字一致，成员的 `locator_ref` 形状合法，且引用锚点必须能由
        **材料自己的** authority assessment 派生（`NS.material_citation_ref`，不读边自报的引用）；
      * 路径 A：该边指向的权威事实必须能在**本节 authority 输入**里按 (container, fact id)
        找到（「事实不存在」不得被当成「没有东西要查」）。
    另加一条与路径无关的产物侧判据：每条已定稿 Claim 必须能派生出**非空且互不相同**的
    citation ID。

    「citation 与它的支撑边逐条一致」不在本函数里重复实现——那是组装器
    （`report_assembler._expected_claim_citations`）的**唯一**复算，组装通过即已成立。

    诚实边界：路径 A 的引用锚点来自**权威事实自己的** `citation_refs`（`financial_fact_id`
    这类事实身份本身就是它的定位子，没有单独的 payload 定位子），因此这里对路径 A 只核
    「事实在自己那条权威里存在且可派生引用」，不假装它也有一个 payload locator 可解析。
    """
    from harness import topic_schema as TS
    from sections import narrative_schema as NS

    resolved_payloads = 0
    authority_resolved = 0
    citation_ids: set[str] = set()
    problems: list[str] = []
    for section_id in SECTION_ORDER:
        section = run.sections.get(section_id)
        if section is None:
            continue
        authority = run.authority_of(section_id)
        manifest = section.draft.material_manifest
        entries = NS.authority_fact_entries(authority)
        for binding in section.acceptance.accepted_bindings:
            if str(binding.support_semantics) != "factual":
                continue
            bid = str(binding.accepted_support_binding_id)
            path = str(binding.authorization_path)
            if path == "path_b_material_derived":
                member_ref = NS.manifest_member_ref(str(binding.authority_container_id),
                                                    str(binding.material_id))
                member = manifest.entry_for(member_ref)
                if member is None:
                    problems.append(f"{bid}: 路径 B 边引用的 material 不在本节 exact manifest 里")
                    continue
                if str(binding.source_identity) != str(member.source_identity):
                    problems.append(
                        f"{bid}: source_identity 与 manifest 成员不符（{binding.source_identity!r} ≠ "
                        f"{member.source_identity!r}）")
                try:
                    TS.verify_material_payload_ref(
                        TS.MaterialPayloadRef.from_dict(dict(member.payload_ref or {})),
                        run.inputs.resolver)
                    resolved_payloads += 1
                except Exception as exc:  # noqa: BLE001 — 定位子不可回查即失败
                    problems.append(f"{bid}: {type(exc).__name__}: {str(exc)[:120]}")
                # §四.1：验收必须核**边自己**的字段，不得只回查 manifest 后声称边合格。
                if binding.payload_ref is None or \
                        dict(binding.payload_ref) != dict(member.payload_ref):
                    problems.append(
                        f"{bid}: 路径 B 边**自己**的 payload_ref 与 manifest 成员不符或缺席"
                        "（边必须自闭合，不得由验收从 manifest 重新拼接）")
                for what, value in (("路径 B 边", binding.locator_ref),
                                    ("manifest 成员", member.locator_ref)):
                    try:
                        if NS.validate_locator(value, what) is None:
                            problems.append(f"{bid}: {what} 的 locator_ref 为空（loc-1 必须带定位）")
                    except NS.NarrativeSchemaError as exc:
                        problems.append(f"{bid}: {what} 的 locator_ref 形状非法：{exc}")
                if NS.locator_sort_key(binding.locator_ref) \
                        != NS.locator_sort_key(member.locator_ref):
                    problems.append(f"{bid}: 路径 B 边的 locator_ref 与 manifest 成员不符")
                try:
                    NS.material_citation_ref(authority, str(binding.authority_container_id),
                                             str(binding.material_id))
                except Exception as exc:  # noqa: BLE001 — 引用锚点不可派生即失败
                    problems.append(f"{bid}: 引用锚点不可从材料派生："
                                    f"{type(exc).__name__}: {str(exc)[:120]}")
            elif path == "path_a_prevalidated":
                coord = NS.binding_fact_key(binding)
                if coord is None or coord not in entries:
                    problems.append(
                        f"{bid}: 路径 A 边指向的权威事实 {coord!r} 不在本节 authority 输入内"
                        "（引用不得指向别的章节或不存在的权威）")
                else:
                    authority_resolved += 1
            else:
                problems.append(f"{bid}: 授权路径既不是路径 A 也不是路径 B（{path!r}）")
        for claim in section.claims:
            ids = NS.claim_citation_ids(claim)
            if not ids:
                problems.append(f"{claim.claim_id}: 没有可派生的 citation ID")
            if len(set(ids)) != len(ids):
                problems.append(f"{claim.claim_id}: citation ID 重复")
            if len(ids) != len(claim.citation_refs):
                problems.append(f"{claim.claim_id}: citation ID 与 citation_refs 条数不等")
            citation_ids.update(ids)
    return {"payload_locators_resolved": resolved_payloads,
            "authority_facts_re_resolved": authority_resolved,
            "distinct_citation_ids": len(citation_ids),
            "problems": problems[:12], "problem_count": len(problems)}


def _factual_atom_coverage(run: "RunState") -> dict:
    """**不支持的事实 = 0**：每条 factual 原子都必须由合格 Claim 及其 factual support edge 蕴含。

    §八 A5：判据覆盖 `sentence_kind="factual"` **与** `sentence_kind="composed"` 里每一个
    claim-bound factual 原子 —— composed 句承载的事实与 factual 句同级，不能因为句类不同就免检
    （真实样本里正文全是 composed，只数 factual 句会让这一门空通过）。逐句**独立重算**：

      * 声明的 Claim 必须存在且 current；句内重复、跨句重复声明都算不支持；
      * 每条 Claim 必须有 ≥1 条 factual accepted binding（context 边不授权事实，顶不上）；
      * 每条 factual accepted binding 必须同时携带 aggregate 决定与蕴含决定，且两条决定都在
        本节的**决定集**内（类型层已强制非空，这里从产物侧再点一遍，使结论不依赖构造期）；
      * 句子的 citation 必须由它声明的 Claim **确定性派生**：逐字等于这些 Claim 的
        `NS.claim_citation_ids` 并集，且无重复、无多寡；
      * composed 句不得新增未授权高风险表面（数字 / 否定 / 状态 / 表格关系 / 因果 / **趋势**）；
      * 已定稿 Claim 必须都落在正文句或表格里（无遗漏）。
    """
    from sections import narrative_schema as NS

    unsupported: list[str] = []
    factual_sentences = 0
    composed_sentences = 0
    covered_claims: dict[str, set[str]] = {}
    for section_id in SECTION_ORDER:
        section = run.sections.get(section_id)
        if section is None:
            continue
        claim_by_id = {str(c.claim_id): c for c in section.claims}
        binding_by_id = {str(b.accepted_support_binding_id): b
                         for b in section.acceptance.accepted_bindings}
        decision_ids = {str(getattr(d, "binding_decision_id", "") or "")
                        for d in getattr(section, "aggregate_decisions", ()) or ()}
        entailment_ids = {str(getattr(d, "entailment_decision_id", "") or "")
                          for d in getattr(section, "entailment_decisions", ()) or ()}
        coverage = covered_claims.setdefault(section_id, set())

        for paragraph in section.narrative.paragraphs:
            for sentence in paragraph.sentences:
                if sentence.sentence_kind not in ("factual", "composed"):
                    continue
                composed_sentences += int(sentence.sentence_kind == "composed")
                factual_sentences += int(sentence.sentence_kind == "factual")
                ids = [str(i) for i in sentence.claim_ids]
                if not ids:
                    # factual / composed 句在类型层就要求 ≥1 条 claim_id；走到这里说明产物被改坏。
                    unsupported.append(
                        f"{sentence.sentence_id}: {sentence.sentence_kind} 句不声明任何 Claim")
                    continue
                duplicated = sorted({i for i in ids if ids.count(i) > 1})
                if duplicated:
                    unsupported.append(f"{sentence.sentence_id}: 句内重复声明 Claim {duplicated}")
                unknown = [i for i in ids if i not in claim_by_id]
                if unknown:
                    unsupported.append(f"{sentence.sentence_id}: 声明了本节不存在的 Claim {unknown}")
                    continue
                for cid in dict.fromkeys(ids):
                    coverage.add(cid)
                # citation 必须由这些 Claim 确定性派生（集合逐字相等、无重复、无多寡）。
                derived: list[str] = []
                for cid in ids:
                    for citation in NS.claim_citation_ids(claim_by_id[cid]):
                        if citation not in derived:
                            derived.append(citation)
                declared = [str(c) for c in sentence.citation_ids]
                if len(set(declared)) != len(declared):
                    unsupported.append(f"{sentence.sentence_id}: citation ID 重复声明")
                if set(declared) != set(derived):
                    unsupported.append(
                        f"{sentence.sentence_id}: citation 不是由所声明 Claim 确定性派生的"
                        f"（声明 {sorted(set(declared) - set(derived))[:4]} 多出、"
                        f"{sorted(set(derived) - set(declared))[:4]} 缺失）")
                # composed 句：自由组织不得新增未授权高风险表面（边界感知：表面只在单个组织段
                # 内合成，不跨「Claim 段 | 组织段」的接缝凭空造出 token）。
                if sentence.sentence_kind == "composed":
                    texts = [str(claim_by_id[i].text) for i in ids]
                    surfaces = NS.unauthorized_surfaces_within_claims(sentence.text, texts)
                    if surfaces:
                        unsupported.append(
                            f"{sentence.sentence_id}: composed 文本新增未授权高风险表面"
                            f" {list(surfaces)[:6]}")

        tabled = {str(i) for i in NS.tabled_claim_ids(section.narrative.tables)}
        absent_from_tables = sorted(tabled - set(claim_by_id))
        if absent_from_tables:
            unsupported.append(f"表格引用了本节不存在的 Claim {absent_from_tables}")

        # 逐条已定稿 Claim（不区分它由正文句还是表格承载）：支撑边与呈现位置都从产物侧重算。
        unplaced_seen: list[str] = []
        for claim in section.claims:
            cid = str(claim.claim_id)
            in_prose = cid in coverage
            in_table = cid in tabled
            if in_prose and in_table:
                unsupported.append(f"{cid}: 同时出现在正文句与表格里（呈现位置必须恰好一个）")
            if not in_prose and not in_table:
                unplaced_seen.append(cid)
            binding_ids = [str(b) for b in claim.accepted_binding_ids]
            if not binding_ids:
                unsupported.append(f"{cid}: 没有任何 accepted binding（无据陈述）")
                continue
            factual = [binding_by_id[b] for b in binding_ids
                       if b in binding_by_id
                       and str(binding_by_id[b].support_semantics) == "factual"]
            if not factual:
                unsupported.append(f"{cid}: 只有 context 边、没有 factual accepted binding"
                                   "（context 不授权事实）")
                continue
            for binding in factual:
                missing = []
                if not binding.binding_decision_id:
                    missing.append("aggregate 决定")
                elif decision_ids and str(binding.binding_decision_id) not in decision_ids:
                    missing.append(f"aggregate 决定 {binding.binding_decision_id} 不在本节决定集内")
                if not binding.entailment_decision_id:
                    missing.append("蕴含决定")
                elif entailment_ids and str(binding.entailment_decision_id) not in entailment_ids:
                    missing.append(f"蕴含决定 {binding.entailment_decision_id} 不在本节决定集内")
                if missing:
                    unsupported.append(
                        f"{binding.accepted_support_binding_id}: 缺 {('、'.join(missing))}")
        if unplaced_seen:
            # 「没写进正文」必须有封闭理由：否则就是静默遗漏（§三 C 第 5 条）。
            excuses = _unpresented_claims(section, unplaced_seen)
            unsupported += [f"未呈现的 Claim {row}" for row in excuses["unexcused"]]
            unsupported += [f"{cid}: omitted 理由是 presented_as_table_row，但表格里没有它"
                            for cid in excuses["table_reason_but_no_table"]]
    return {"factual_sentences": factual_sentences,
            "composed_sentences": composed_sentences,
            "claims_covered": {s: len(v) for s, v in sorted(covered_claims.items())},
            "unsupported": unsupported[:12], "unsupported_count": len(unsupported)}


def _unpresented_claims(section, unplaced_ids) -> dict:
    """未出现在正文/表格里的 Claim，必须各自有一条**带封闭理由**的 omitted 去向。

    `presented_as_table_row` 不在此列：那个理由码的含义恰恰是**已呈现**（由表格行承载），因此
    它必须同时出现在表格里 —— 反之（有理由、表格里却没有）本身就是不一致。返回
    `{"excused": {...}, "unexcused": [...], "table_reason_but_no_table": [...]}`。
    """
    by_claim = {}
    for disp in getattr(section, "claim_narrative_dispositions", ()) or ():
        by_claim[str(getattr(disp, "claim_id", "") or "")] = disp
    excused: dict[str, str] = {}
    unexcused: list[str] = []
    table_reason: list[str] = []
    for cid in unplaced_ids:
        disp = by_claim.get(cid)
        if disp is None:
            unexcused.append(f"{cid}: 没有 Claim 去向记录")
            continue
        disposition = str(getattr(disp, "disposition", ""))
        reason = getattr(disp, "reason_code", None)
        if disposition == "omitted" and reason in ("outside_section_topic",
                                                   "redundant_with_selected_claim"):
            excused[cid] = str(reason)
        elif disposition == "omitted" and reason == "presented_as_table_row":
            table_reason.append(cid)
        else:
            unexcused.append(f"{cid}: 去向 {disposition!r}/{reason!r} 不是「本节不呈现」")
    return {"excused": excused, "unexcused": unexcused,
            "table_reason_but_no_table": table_reason,
            "disposition_count": len(by_claim)}


def _post_gate_adjudication_audit(run: "RunState") -> dict:
    """复核「门后被延后裁定的规则」在本 run 上确实被**门后对象**满足了。

    §0.13 / §三 A：`required_fact_not_proposed` 只说明「门在候选束上看不到门后对象」，裁定者
    从门换成组装器。组装器已逐条复核；验收侧**自己再算一遍**同一命题（只看产物，不读组装器的
    结论）：对每条被延后的 rework issue，按 `container:fact` 找到 FND，要求它 `claimed`，
    或（Contract 必需且已留下显式 unresolved/block）。任一条不成立即 A5 失败——延后不是免检。
    """
    from sections import narrative_schema as NS

    deferred_rules = set(NS.POST_GATE_ADJUDICATED_GATE_RULES)
    rows: list[dict] = []
    problems: list[str] = []
    for section_id in SECTION_ORDER:
        section = run.sections.get(section_id)
        if section is None:
            continue
        gaps = {str(u.unresolved_id) for u in section.result.unresolved}
        by_fact: dict[tuple[str, str], object] = {}
        for disp in section.dispositions:
            by_fact[(str(disp.container_identity),
                     str(disp.authority_specific_fact_id))] = disp
        for issue in section.gate_result.issues_of("rework"):
            rule_id = str(issue.rule_id)
            if rule_id not in deferred_rules:
                continue
            container, _, fact_id = str(issue.location).rpartition(":")
            disp = by_fact.get((container, fact_id))
            claimed = disp is not None and str(getattr(disp, "disposition", "")) == "claimed"
            required_gap = (disp is not None and bool(getattr(disp, "required", False))
                            and str(getattr(disp, "unresolved_id", "") or "") in gaps)
            row = {"section_id": section_id, "rule_id": rule_id,
                   "location": str(issue.location), "fnd_found": disp is not None,
                   "claimed": bool(claimed), "required_with_explicit_gap": bool(required_gap),
                   "adjudicated": bool(claimed or required_gap)}
            rows.append(row)
            if not row["adjudicated"]:
                problems.append(
                    f"{section_id}/{rule_id}@{issue.location}: 门后未成立"
                    f"（FND 在场={row['fnd_found']}，claimed={row['claimed']}，"
                    f"必需且留缺口={row['required_with_explicit_gap']}）")
    return {"deferred_rule_ids": sorted(deferred_rules), "issues": rows[:12],
            "issue_count": len(rows), "problems": problems[:6],
            "problem_count": len(problems)}


def _authority_containers(authority: object) -> dict[str, str]:
    """一个权威输入声明的容器：`{topic_id: pack_id}`。财务权威没有 Pack 集，如实返回空表。

    按 **topic** 配对（不是拼成一个扁平集合）有实质理由：successor 的语义是「被申请的那个
    topic 现在解析到新的 Pack」，**其余 topic 的容器必须原样保留**。扁平集合会把
    「该 topic 换了容器」（正常）与「别的 topic 被换掉」（异常）压成同一件事。
    """
    pack_set = getattr(authority, "pack_set", None)
    out: dict[str, str] = {}
    for pack in tuple(getattr(pack_set, "packs", ()) or ()):
        out[str(getattr(pack, "topic_id", "") or "")] = str(getattr(pack, "pack_id", "") or "")
    return out


def _external_retrieval_honesty(state: "RunState") -> dict:
    """§三 3（acc-30）：**本轮对外部来源要求到底做了什么**——只读现场，不判通过、不判「已证明没有」。

    要回答的是三个**分开**的问题，产物里也分开列：

    * **声明侧**：哪些 aspect 的冻结 `EvidenceRequirementRef` 声明了「只能取外部来源」。
      逐 aspect 由权威函数 `TS.derive_support_eligibility` 从冻结 ref **重算**
      （`required_source_classes`），不硬编码任何 topic / aspect / 行业名。
    * **需要侧**（acc-30 新增，只读回读）：那些声明**有没有真的到达链路**。逐 aspect 用
      **同一个**注入的构造器重建一次 need，读它的 `required_source_types` 是否含 `external`、
      以及真实 `routing.router.requires_external_source(need)` 是否为真。重建的 `need_id` 是
      **诊断用**的（带 `::need-readback` 后缀），不是本轮真实 need id，也不参与任何判据。
      只有注入的构造器**自称是生产实现**（带 `version` 且模块为 `harness.aspect_need_builder`）
      时才回读；否则如实记 `available = false`，**不**对任意替身二次调用。
    * **尝试侧**：本轮 Pack 里有没有任何**外部漏斗**痕迹（`external_funnel` /
      `external_facts`）。没有 = 本轮**没有执行过**外部检索，**不是**「已证明不存在外部来源」。

    **终态是封闭枚举、二者互斥**：尝试侧有正面证据 ⇒ `retrieved_none_adoptable`
    （**执行过**、但没有可采用的外部事实）；否则 ⇒ `not_retrieved`。`not_retrieved` 不得与
    任何"已检索"表述同时出现——没有请求被发出，就没有可复核的失败面。

    **原因码是一条封闭集**（acc-30 起是 list，不再压成单个字符串），两条原因各自独立：
    (a) 本轮 `external_research_enabled` 为假 ⇒ 外部动作**执行前被拒**
    （`harness.policies.external_research_unauthorized`，在 `harness/runtime.py` 的工具执行
    路径上留痕）；(b) 注入的 need 构造器又是那个已知的结构替身 ⇒ 冻结 `source_classes`
    仍到不了 need（**历史原因**，接线生产实现后不再产出）。两者不得合并成一条结论。

    最后一条读法钉在这里：发行人**转述**的第三方数字（年报正文里的 SNE / IEA / SMM 等）
    是**发行人转录**，不因本节而升格为**已验证的外部事实**；`ExternalFact` 的资格、快照/正文
    哈希、SourcePolicy、日期与 locator 是另一套绑定，本轮一条都没有产出。
    """
    from harness import topic_schema as TS
    from routing import router as RR

    builder = getattr(state.inputs, "information_need_builder", None)
    builder_name = ("%s.%s" % (type(builder).__module__, type(builder).__qualname__)
                    if builder is not None else None)
    enabled = bool(getattr(state.inputs, "external_research_enabled", False))
    # 生产实现的判据：模块名 + 自称版本。替身没有 `version`，因此不会被当生产实现回读。
    is_production_builder = bool(
        builder is not None
        and type(builder).__module__ == "harness.aspect_need_builder"
        and getattr(builder, "version", None))
    is_known_structural_stub = bool(
        builder_name and builder_name.endswith("test_demo_topic_runtime._NeedBuilder"))

    requirement_side: dict[str, dict] = {}
    need_side_sections: dict[str, dict] = {}
    external_aspects: dict[str, list[str]] = {}
    external_carried: dict[str, list[str]] = {}
    for section_id in SECTION_ORDER:
        task = (state.inputs.tasks or {}).get(section_id)
        if task is None:
            continue
        rows: list[dict] = []
        carried_rows: list[dict] = []
        for topic_id in tuple(getattr(task, "topic_ids", ()) or ()):
            requirement = (state.inputs.requirements or {}).get(str(topic_id))
            if requirement is None:
                continue
            for snap in tuple(getattr(requirement, "aspects", ()) or ()):
                classes = tuple(TS.derive_support_eligibility(snap).required_source_classes)
                row = {
                    "topic_id": str(topic_id),
                    "aspect_id": str(getattr(snap, "aspect_id", "")),
                    "required_source_classes": list(classes),
                    "source": "frozen_evidence_requirement_ref",
                }
                rows.append(row)
                if "external" in classes:
                    external_aspects.setdefault(section_id, []).append(row["aspect_id"])
                if not is_production_builder:
                    continue
                # 只读回读：同一个构造器、同一份冻结快照，重建一次诊断用 need。
                probe = builder.build(
                    snap, need_id=str(row["aspect_id"]) + "::need-readback",
                    company_id=str(getattr(state.inputs, "company_id", "") or ""),
                    section_id=section_id,
                    report_as_of=getattr(state.inputs, "report_as_of", None))
                sources = list(getattr(probe, "required_source_types", ()) or ())
                carried = "external" in sources
                carried_rows.append({
                    "topic_id": row["topic_id"], "aspect_id": row["aspect_id"],
                    "required_source_types": sources,
                    "requires_external_source": bool(RR.requires_external_source(probe)),
                    "declared_external": row["aspect_id"] in external_aspects.get(
                        section_id, []),
                })
                if carried:
                    external_carried.setdefault(section_id, []).append(row["aspect_id"])
        requirement_side[section_id] = {
            "aspect_count": len(rows),
            # 只内联**声明了外部来源**的那些行：其余 60 余行的四轴身份在
            # `failure_diagnostics.json` / `material_pack.json` 里已逐条摊开，这里再抄一遍
            # 会把「本轮外部实况」这一件事淹掉。
            "aspects_declaring_external": external_aspects.get(section_id, []),
            "rows_declaring_external": [r for r in rows
                                        if "external" in r["required_source_classes"]],
        }
        need_side_sections[section_id] = {
            "available": is_production_builder,
            "reason": None if is_production_builder
                      else "builder_is_not_the_production_implementation",
            "aspect_count_read_back": len(carried_rows),
            "aspects_carrying_external": external_carried.get(section_id, []),
            "rows": carried_rows,
        }

    # 声明侧与需要侧必须**同源**：两处都由 `TS.derive_support_eligibility` 派生，因此对每个
    # aspect 都应有「声明了 external ⟺ need 带出 external」。不一致就是接线断了，如实记出来。
    mismatches: list[dict] = []
    for section_id, rows in need_side_sections.items():
        declaring = set(requirement_side[section_id]["aspects_declaring_external"])
        for row in rows["rows"]:
            if row["declared_external"] != row["requires_external_source"]:
                mismatches.append({"section_id": section_id, **row})

    attempt_side: dict[str, dict] = {}
    for section_id, pack_set in dict(state.inputs.pack_sets or {}).items():
        packs = tuple(getattr(pack_set, "packs", ()) or ())
        attempt_side[str(section_id)] = {
            "topic_count": len(packs),
            "packs_with_external_funnel": sum(
                1 for p in packs if getattr(p, "external_funnel", None) is not None),
            "external_fact_count": sum(
                len(tuple(getattr(p, "external_facts", ()) or ())) for p in packs),
        }
    attempted = any(v["packs_with_external_funnel"] or v["external_fact_count"]
                    for v in attempt_side.values())
    declared = sorted({a for ids in external_aspects.values() for a in ids})
    carried = sorted({a for ids in external_carried.values() for a in ids})

    hit_reasons: set[str] = set()
    if not enabled:
        hit_reasons.add(EXTERNAL_RETRIEVAL_NOT_AUTHORIZED)
    if is_known_structural_stub:
        hit_reasons.add(EXTERNAL_RETRIEVAL_NEED_BUILDER_DROPS_SOURCE_CLASSES)
    # 按**封闭集的定义顺序**输出，而不是命中顺序：产物里两条原因的先后不随现场抖动
    # （读者可以拿现场值直接与 `not_connected_reason_code_set` 对齐）。
    reason_codes: list[str] = [c for c in EXTERNAL_RETRIEVAL_REASON_CODES if c in hit_reasons]
    terminal = (EXTERNAL_TERMINAL_RETRIEVED_NONE_ADOPTABLE if attempted
                else EXTERNAL_TERMINAL_NOT_RETRIEVED)

    if attempted:
        statement = (
            f"本轮**执行过**外部检索（Pack 里有外部漏斗痕迹），但没有可采用的外部事实："
            f"冻结 Contract 声明了 {len(declared)} 个 aspect 的来源类为 `external`，"
            "而 `external_facts` 为 0。这是「已检索未取得」，"
            "**不是**「已证明不存在可用的外部来源」，也不是「行业没有可写内容」。")
    else:
        statement = (
            "本轮**未检索**外部来源。冻结 Contract 声明了 "
            f"{len(declared)} 个 aspect 的来源类为 `external`，其中 {len(carried)} 个的要求"
            "**已经到达正式 `InformationNeed`**（需要侧逐条回读），"
            "而本轮 Pack 里没有任何外部漏斗痕迹（无 `external_funnel`、无 `external_facts`）——"
            "这是**链路事实**：要求到了、但本轮的授权门在执行前拒掉了外部动作。"
            "**不是**「已证明不存在可用的外部来源」，也不是「行业没有可写内容」。")

    return {
        "schema_version": EXTERNAL_RETRIEVAL_SCHEMA_VERSION,
        "external_research_enabled": enabled,
        "information_need_builder": builder_name,
        # acc-30 起是**列表**：两条原因各自独立，可以同时成立，不得压成一个字符串。
        "not_connected_reason_codes": reason_codes,
        "not_connected_reason_code_set": list(EXTERNAL_RETRIEVAL_REASON_CODES),
        # 终态（封闭枚举，互斥）：`not_retrieved` / `retrieved_none_adoptable`。
        "terminal_state": terminal,
        "authorization": {
            "external_research_enabled": enabled,
            "external_actions_unauthorized": not enabled,
            "enforced_by": "harness.policies.external_research_unauthorized",
            "enforced_at": "harness.runtime（工具执行前；SEARCH_EXTERNAL / FETCH_EXTERNAL / "
                           "SNAPSHOT_EXTERNAL 三个动作一律执行前拒绝并留痕）",
            "reason": (None if enabled else
                       "本轮未启用外部检索漏斗：需要侧可以带出 `external` 要求，"
                       "但没有任何外部动作会被执行。"),
        },
        "aspects_declaring_external": declared,
        "aspects_carrying_external_in_need": carried,
        "requirement_side": requirement_side,
        "need_side": {
            "builder_is_production_implementation": is_production_builder,
            "declared_and_need_sides_agree": not mismatches,
            "mismatches": mismatches,
            "sections": need_side_sections,
        },
        "attempt_side": attempt_side,
        "external_retrieval_attempted": attempted,
        "statement": statement,
        "readings": [
            "「声明了必须外部来源」「要求真的到达了 need」「本轮发过外部请求」是**三条**各自"
            "可读的事实：第一条读冻结 `EvidenceRequirementRef`，第二条读注入构造器重建出的 "
            "`InformationNeed.required_source_types`，第三条读 Pack 的外部漏斗痕迹。"
            "三者不得合并成一条结论。",
            "「本轮未检索」**不等于**「已检索而没找到」：终态是互斥的封闭枚举，"
            "`not_retrieved` 时**没有**任何请求被发出，因此没有可复核的失败面；"
            "`retrieved_none_adoptable` 必须有正面证据（外部漏斗痕迹）才允许出现。",
            "发行人对第三方数字的**转述**（年报正文里的 SNE / IEA / SMM 等）是发行人转录，"
            "**不得**被读成已验证的外部事实；`ExternalFact` 另有资格决定、快照/正文哈希、"
            "SourcePolicy、日期与 locator 的绑定，本轮一条都没有产出。",
            "原因码是**封闭集**：" + "、".join(
                "`%s`" % c for c in EXTERNAL_RETRIEVAL_REASON_CODES) + "。"
            "`" + EXTERNAL_RETRIEVAL_NOT_AUTHORIZED + "` 指本轮开关为关、外部动作执行前被拒；"
            "`" + EXTERNAL_RETRIEVAL_NEED_BUILDER_DROPS_SOURCE_CLASSES + "` 指注入的 need "
            "构造器仍是已知结构替身、冻结 `source_classes` 到不了 need（接线生产实现后不再产出）。"
            "两条都不是「没有外部来源」的判定。",
        ],
    }


def _follow_up_identity_audit(run: "RunState") -> dict:
    """§四 1 / §4.6 身份分层：补件运行是**操作事件**，不得改写策略依赖，也不得丢掉后继身份。

    三套身份各归各位，这里逐条摊开并对账：

    * **策略依赖**：`dependency_fingerprint` 是 Contract / SourcePolicy / WritingSpec /
      PresentationProfile 四类资产的冻结指纹，同一报告各节逐字节相等。`follow_up_run_id` 与
      trace refs 属 operational run identity（`AGENTS.md` §3 把它放在三套身份集**之外**），
      一旦它改写了指纹，这里当场变红——**不是**「待裁决的冲突」，是身份分层被破坏。
    * **内容身份**：后继 Pack 必须进 Draft 的 `authority_container_ids` 与 exact manifest
      （`pack_writer._authority_container_ids`），否则「换了后继 Pack」在内容身份上不可见。
    * **版本身份**：Pack 集进 `ReportVersionInputs.topic_pack_ids`（§四 1）——见
      `_report_version_identity_audit`。
    * **操作身份**：每次有界执行各有一个 `follow_up_run_id`，其 trace ref 必须指向**它自己**，
      两次执行不得折叠成同一串 ref（旧缺陷：同一 task 上两次执行都是 `…:FOLLOW_UP_EXECUTED:0`，
      产物里分不出「1 次」还是「2 次」）。

    `superseded_topics` 是「被申请的 topic 换到了新 Pack」，`retained_topics` 是其余 topic
    的容器逐字未变；旧 Pack **行本身**的不可变性由 topic store 的不可变性承担，不在这里重证。
    """
    projection_fp = str(getattr(run.inputs.projection, "dependency_fingerprint", "") or "")
    run_rows: list[dict] = []
    section_rows: list[dict] = []
    problems: list[str] = []
    for section_id in SECTION_ORDER:
        section = run.sections.get(section_id)
        if section is None:
            continue
        draft = section.draft
        draft_fp = str(getattr(draft, "dependency_fingerprint", "") or "")
        draft_containers = {str(c) for c in
                            tuple(getattr(draft, "authority_container_ids", ()) or ())}
        refs = [str(r) for r in tuple(getattr(section, "follow_up_run_refs", ()) or ())]
        input_containers = _authority_containers(run.inputs.authorities[section_id])
        used_containers = _authority_containers(run.authority_of(section_id))
        superseded = sorted(t for t, pid in input_containers.items()
                            if used_containers.get(t, "") != pid)
        retained = sorted(t for t, pid in input_containers.items()
                          if used_containers.get(t, "") == pid)
        added = sorted(t for t in used_containers if t not in input_containers)
        successor_pack_ids = sorted({used_containers[t] for t in superseded
                                     if used_containers.get(t)})
        runs = tuple(run.follow_up_runs.get(section_id) or ())
        section_run_ids: list[str] = []
        section_run_refs: list[str] = []
        for item in runs:
            needs = tuple(getattr(item, "needs", ()) or ())
            decisions = tuple(getattr(item, "decisions", ()) or ())
            own_id = str(getattr(item, "follow_up_run_id", "") or "")
            item_refs = [str(x) for x in tuple(getattr(item, "trace_refs", ()) or ())]
            executed = [d for d in decisions if bool(getattr(d, "executed", False))]
            new_packs = [str(x) for x in tuple(getattr(item, "new_pack_ids", ()) or ())]
            section_run_ids.append(own_id)
            section_run_refs.extend(item_refs)
            run_rows.append({
                "section_id": section_id,
                "follow_up_run_id": own_id,
                "need_ids": [str(n.need_id) for n in needs],
                "target_requirement_ids": sorted({str(n.target_requirement_id) for n in needs}),
                "topic_ids": sorted({str(n.topic_id) for n in needs}),
                "aspect_ids": sorted({str(n.aspect_id) for n in needs}),
                "section_draft_revisions": sorted(
                    {str(n.section_draft_revision) for n in needs}),
                "rules_version": str(getattr(item, "rules_version", "") or ""),
                "verdicts": [str(getattr(d, "verdict", "")) for d in decisions],
                "executed_count": len(executed),
                "rejected_count": len(decisions) - len(executed),
                "new_pack_ids": new_packs,
                "new_pack_topics": sorted(t for t, pid in used_containers.items()
                                          if pid in set(new_packs)),
                "trace_refs": item_refs,
                "refs_point_at_own_run": bool(item_refs)
                and all(own_id and own_id in ref for ref in item_refs),
            })
            if not own_id:
                problems.append(f"section={section_id!r} 的补件运行没有 follow_up_run_id"
                                "（操作身份不得为空）")
            if not item_refs:
                problems.append(f"section={section_id!r} 的补件运行 {own_id!r} 没有留下任何 "
                                "trace ref：执行过却没有可回查的痕迹")
            elif not all(own_id in ref for ref in item_refs):
                problems.append(
                    f"section={section_id!r} 的补件运行 {own_id!r} 的 trace ref 没有指向它自己："
                    f"{item_refs}（两次有界执行会折叠成同一串）")
            if len(new_packs) != len(executed):
                problems.append(
                    f"section={section_id!r} 的补件运行 {own_id!r} 的新 Pack 数 {len(new_packs)} "
                    f"与执行数 {len(executed)} 不符（被拒绝的诉求不得产出 Pack）")
        body_text = json.dumps(draft.identity_body(), ensure_ascii=False, sort_keys=True,
                               default=str)
        leaked = [rid for rid in section_run_ids if rid and rid in body_text]
        if leaked:
            problems.append(f"section={section_id!r} 的 Draft 内容身份里出现了补件运行 ID "
                            f"{leaked}：操作身份不得进入内容身份（§4.6）")
        if set(section_run_refs) != set(refs):
            problems.append(
                f"section={section_id!r} 的 follow_up_run_refs 与保留下来的补件运行不自洽："
                f"refs={refs} vs runs={section_run_refs}")
        if draft_fp != projection_fp:
            problems.append(
                f"section={section_id!r} 的 Draft 依赖指纹被改写：{draft_fp!r} != 冻结投影 "
                f"{projection_fp!r}（补件是操作事件，不是策略资产变更；同族必须逐字节一致）")
        if successor_pack_ids and not set(successor_pack_ids) <= draft_containers:
            problems.append(
                f"section={section_id!r} 的后继 Pack {successor_pack_ids} 不在 Draft 的 "
                f"authority_container_ids（内容身份）里：{sorted(draft_containers)}"
                "——「换了后继 Pack」必须在内容身份上可见")
        if added:
            problems.append(f"section={section_id!r} 的权威里出现了原本不属于本节 topic 的容器："
                            f"{added}（补件不得把别的 topic 塞进本节权威）")
        section_rows.append({
            "section_id": section_id,
            "follow_up_executed": bool(refs) or bool(runs)
            or run.authority_of(section_id) is not run.inputs.authorities[section_id],
            "follow_up_run_count": len(runs),
            "follow_up_run_ids": section_run_ids,
            "follow_up_run_refs": refs,
            "authority_object_is_successor": (
                run.authority_of(section_id) is not run.inputs.authorities[section_id]),
            "input_containers_by_topic": input_containers,
            "used_containers_by_topic": used_containers,
            "superseded_topics": superseded,
            "retained_topics": retained,
            "new_topics": added,
            "successor_pack_ids": successor_pack_ids,
            "successor_pack_ids_in_draft_containers": sorted(
                set(successor_pack_ids) & draft_containers),
            "draft_dependency_fingerprint": draft_fp,
            "projection_dependency_fingerprint": projection_fp,
            "dependency_fingerprint_frozen": draft_fp == projection_fp,
        })
    # 操作身份必须两两可区分（同一次运行里两次有界执行不得共用身份，也不得共用 ref）
    by_id: dict[str, list[str]] = {}
    for row in run_rows:
        by_id.setdefault(row["follow_up_run_id"], []).append(
            f"{row['section_id']}:{','.join(row['need_ids'])}")
    shared_ids = {k: v for k, v in by_id.items() if len(v) > 1}
    if shared_ids:
        problems.append(f"同一个 follow_up_run_id 被多次执行共用：{shared_ids}")
    for i, first in enumerate(run_rows):
        for later in run_rows[i + 1:]:
            shared = sorted(set(first["trace_refs"]) & set(later["trace_refs"]))
            if shared:
                problems.append(
                    f"两次补件执行的 trace ref 折叠成同一串（"
                    f"{first['section_id']}/{first['follow_up_run_id']} 与 "
                    f"{later['section_id']}/{later['follow_up_run_id']}）：{shared}")
    note = ("每次有界执行的 ref 都指向它自己的 follow_up_run_id；Draft 的依赖指纹保持冻结；"
            "后继 Pack 的身份落在 authority_container_ids（内容身份）而不是依赖指纹里。")
    if not run_rows:
        note = ("本轮没有任何有界补件执行（没有需要补件的可读主题）：身份分层无对象可核，"
                "本审计如实记为 0 次执行，不据此声明任何通过结论。")
    return {"runs": run_rows, "sections": section_rows,
            "run_count": len(run_rows), "problems": problems,
            "problem_count": len(problems),
            "sections_with_rewritten_fingerprint": [
                row["section_id"] for row in section_rows
                if not row["dependency_fingerprint_frozen"]],
            "note": note}


def _report_version_identity_audit(run: "RunState") -> dict:
    """§四 1 版本身份：`report_version` 的 Pack 集必须**恰好**是本轮实际使用的 current Pack 集。

    「实际使用」= 各节**定稿所用**权威（有界重写后是 successor 集）里的 Pack 集，由本 runner
    自己读出后**声明**给组装器；组装器再与它自己从权威取回的集合逐字段对账。这里额外核两件事：
    组装出的 `report_version.topic_pack_ids` 与本次声明**同集合**（多一个 ⇒ 混进旧 Pack/错 Pack；
    少一个 ⇒ 有节的 Pack 身份丢失），且报告级权威容器清单**包含**本轮实际使用的每个 Pack。
    """
    expected: set[str] = set()
    for section_id in SECTION_ORDER:
        pack_set = getattr(run.authority_of(section_id), "pack_set", None)
        expected.update(str(p.pack_id) for p in tuple(getattr(pack_set, "packs", ()) or ()))
    declared = {str(x) for x in tuple(
        getattr(run.version_inputs, "topic_pack_ids", ()) or ())}
    problems: list[str] = []
    evidence: dict = {"expected_pack_ids": sorted(expected),
                      "declared_pack_ids": sorted(declared)}
    if run.report is None:
        evidence["status"] = "unavailable"
        evidence["detail"] = "本轮没有实得报告：版本身份无从复核（不算通过）"
        return {"problems": [evidence["detail"]], **evidence}
    version_identity = getattr(run.report, "version_identity", None)
    report_packs = {str(x) for x in tuple(
        getattr(version_identity, "topic_pack_ids", ()) or ())}
    containers = {str(x) for x in tuple(getattr(run.report, "authority_container_ids", ()) or ())}
    evidence.update({"report_version_pack_ids": sorted(report_packs),
                     "report_authority_container_ids": sorted(containers),
                     "report_dependency_fingerprint": str(
                         getattr(run.report, "dependency_fingerprint", "") or ""),
                     "status": "checked"})
    if declared != expected:
        problems.append(f"声明的 Pack 集 {sorted(declared)} 与各节定稿权威取回的 Pack 集 "
                        f"{sorted(expected)} 不同集合")
    if report_packs != expected:
        problems.append(f"report_version 绑定的 Pack 集 {sorted(report_packs)} 与本轮实际使用的 "
                        f"current Pack 集 {sorted(expected)} 不同集合（旧 Pack / 错 Pack 不得进版本）")
    missing = sorted(expected - containers)
    if missing:
        problems.append(f"报告级权威容器清单缺少本轮实际使用的 Pack：{missing}")
    frozen = str(getattr(run.inputs.projection, "dependency_fingerprint", "") or "")
    if str(getattr(run.report, "dependency_fingerprint", "") or "") != frozen:
        problems.append("报告级依赖指纹不等于冻结投影的指纹（报告版本根被改写）")
    # §4.6（acc-5 新增）：身份必须绑定各节 SectionDraft 身份与「排除自身版本字段后的
    # assembled canonical payload」指纹。这里**独立重算**，不复用写入侧任何缓存。
    from sections import narrative_schema as NS
    # 预期值取**本轮各节定稿现场**（`run.sections` 里的 Draft 身份），不取报告自报的那份：
    # 拿报告自报值去核报告身份是自证循环——「换过 Draft 的报告」只要顺手重建身份就能静默过关。
    live_sections = getattr(run, "sections", None)
    draft_expected = tuple(sorted(
        str(s.draft.draft_id) for s in tuple((live_sections or {}).values())))
    draft_reported = tuple(sorted(
        str(s.section_draft_id)
        for s in tuple(getattr(run.report, "sections", None) or ())))
    draft_declared = tuple(str(x) for x in tuple(
        getattr(version_identity, "section_draft_ids", ()) or ()))
    payload_declared = str(
        getattr(version_identity, "assembled_payload_fingerprint", "") or "")
    try:
        payload_actual = NS.assembled_payload_fingerprint_of(run.report)
    except Exception as exc:  # noqa: BLE001 — 重算失败如实记为问题，不当作通过
        payload_actual = ""
        problems.append(f"规范载荷指纹无法独立重算：{exc!r}")
    evidence.update({"section_draft_ids_expected": list(draft_expected),
                     "section_draft_ids_reported": list(draft_reported),
                     "section_draft_ids_declared": list(draft_declared),
                     "assembled_payload_fingerprint_declared": payload_declared,
                     "assembled_payload_fingerprint_recomputed": payload_actual,
                     "assembled_payload_included_fields": list(
                         NS.ASSEMBLED_PAYLOAD_INCLUDED_FIELDS),
                     "assembled_payload_excluded_fields": list(
                         NS.ASSEMBLED_PAYLOAD_EXCLUDED_FIELDS)})
    if live_sections is None:
        problems.append(
            "本轮没有各节定稿现场（`run.sections`）：各节 SectionDraft 身份无从独立核对")
    elif draft_reported != draft_expected:
        problems.append(
            f"报告各节携带的各节 SectionDraft 身份 {list(draft_reported)} 与本轮定稿现场 "
            f"{list(draft_expected)} 不一致（报告不得自称另一个 Draft）")
    if draft_declared != draft_expected:
        problems.append(
            f"report_version 绑定的 SectionDraft 身份 {list(draft_declared)} 与本轮各节实际 "
            f"Draft 身份 {list(draft_expected)} 不一致（改 Draft ID 必须换版本）")
    if payload_actual and payload_declared != payload_actual:
        problems.append(
            "report_version 绑定的规范载荷指纹与独立重算值不一致（载荷变了、版本没变）")
    return {"problems": problems, **evidence}


def _gate_a5_assembly(run: "RunState") -> GateOutcome:
    """A5：至少公司+财务可组装；缺口状态可显示；不支持的事实为 0；引用/定位子可回查。

    外加 §四 1 身份分层：补件运行不得改写冻结策略依赖，后继 Pack 身份不得丢，版本身份必须
    等于本轮实际使用的 current Pack 集（`_follow_up_identity_audit` /
    `_report_version_identity_audit`）。这两项是**如实记的问题**，不是「待裁决的接口冲突」：
    身份分层一旦被破坏，报告版本的来源就不再可信。
    """
    problems: list[str] = []
    follow_up_identity = _follow_up_identity_audit(run)
    version_identity = _report_version_identity_audit(run)
    identity_evidence = {"follow_up_identity": follow_up_identity,
                         "report_version_identity": version_identity}
    problems.extend(f"身份分层：{p}" for p in follow_up_identity["problems"])
    problems.extend(f"版本身份：{p}" for p in version_identity["problems"])
    if run.report is None:
        return _gate("A5", "组装：可组装 + 缺口可显示 + 零裸事实 + 引用可回查",
                     [f"报告未能组装：{run.assembly_error}"] + problems,
                     {"assembly_error": run.assembly_error, **identity_evidence})
    section_ids = [s.section_id for s in run.report.sections]
    for required in ("company", "financial"):
        if required not in section_ids:
            problems.append(f"{required} 节不在组装出的报告里")
    coverage = run.report.scope_coverage
    if not coverage:
        problems.append("报告没有 scope_coverage（覆盖/缺口无从显示）")
    coverage_without_anything = [c for c in coverage
                                 if not c.get("has_body") and not c.get("has_explicit_gap")]
    if coverage_without_anything:
        problems.append(f"有内容单位既无正文也无显式缺口：{coverage_without_anything[:4]}")
    unsupported = _factual_atom_coverage(run)
    if unsupported["unsupported_count"]:
        problems.append(f"存在不支持的事实原子 {unsupported['unsupported'][:4]}")
    citations = _citation_requery(run)
    if citations["problem_count"]:
        problems.append(f"引用/定位子回查失败：{citations['problems'][:3]}")
    adjudication = _post_gate_adjudication_audit(run)
    if adjudication["problem_count"]:
        problems.append(f"门后延后裁定未成立：{adjudication['problems'][:3]}")
    evidence = {"post_gate_adjudication": adjudication,
                **identity_evidence,
                "report_id": run.report.report_id,
                "report_version": run.report.report_version,
                "sections": section_ids,
                "scope_coverage": [{"section_id": c.get("section_id"),
                                    "has_body": c.get("has_body"),
                                    "has_explicit_gap": c.get("has_explicit_gap")}
                                   for c in coverage],
                "gap_index_size": len(run.report.gap_index),
                "conflict_index_size": len(run.report.conflict_index),
                "markdown_chars": len(run.report.markdown),
                "unsupported_facts": unsupported,
                "citation_requery": citations}
    return _gate("A5", "组装：可组装 + 缺口可显示 + 零裸事实 + 引用可回查", problems, evidence)


def _gate_a6_trust_roots(before: dict, after: dict) -> GateOutcome:
    """A6：信任根与期望值一个字节都没变（跑前快照 vs 跑后快照）。"""
    problems: list[str] = []
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            problems.append(f"信任根 {name} 在本次运行中被改动："
                            f"{str(before.get(name))[:12]} → {str(after.get(name))[:12]}")
    evidence = {"trust_roots": {k: {"before": before.get(k), "after": after.get(k),
                                    "unchanged": before.get(k) == after.get(k)}
                                for k in sorted(before)},
                "note": ("本 runner 不写任何真实库、不改任何冻结资产或 prompt 资产，"
                         "也不按实得结果回写期望值；上面每个 key 的 before/after 必须逐字节相等")}
    return _gate("A6", "信任根与期望值零改动（跑前/跑后逐字节比较）", problems, evidence)


# ---------------------------------------------------------------------------
# 驱动：三节各自成链，各跑一次唯一写作主链
# ---------------------------------------------------------------------------

class RunState:
    """一次运行的现场（含全部中间身份，供判据与产物使用）。"""

    def __init__(self, *, inputs: RealInputs, run_dir: Path, mode: str,
                 generated_at: str, policy) -> None:
        self.inputs = inputs
        self.run_dir = run_dir
        self.mode = mode
        self.generated_at = generated_at
        self.policy = policy
        self.sections: dict[str, object] = {}
        #: §六：每节**定稿所用**的权威（有界重写发生后是 successor）。判据与组装都必须按它核对：
        #: 拿输入权威去核一份基于 successor 写出来的 draft，会得到「容器不在权威集合内」的假失败。
        self.authorities: dict[str, object] = {}
        #: §三 F：本节相位里真的发生过的有界补件运行（`FollowUpExecutionResult`，按节保存）。
        #: 相位返回值里带着它，路线侧若只留 `sections[0]` 就会把它丢掉——那样「跑了几次补件、
        #: 各自指向哪个 run / need / 后继 Pack」在产物里就无从复核（这正是 trace 折叠缺陷能藏身
        #: 的原因）。这里逐节留下，供 A5 的身份分层审计使用。
        self.follow_up_runs: dict[str, tuple] = {}
        #: §二 3：每节里**被整束拒绝**的候选提案集的 typed 审计（`ProposalSetRejectionRecord`
        #: 的 `to_dict()`），按节保存。被拒的束**不因被拒而消失**：它的完整有序身份与 typed
        #: 原因必须能在产物里逐条回查，且**不得**被裁剪成「剩下的那些就是原来的提案集」。
        #: 失败节也要有——本节整体失败时，这份审计正是「为什么没有内容」的唯一结构化证据。
        self.proposal_set_rejections: dict[str, list[dict]] = {}
        #: §二 2.6：相位**无法裁决**而被终止时随异常带出的 `FollowUpNeed`（按节）。
        #: 它们是**待裁决提议**：不因候选被拒或相位终止而丢失，也**不**由本组合根执行。
        #: 键为 section_id，值为 {"follow_up_needs": [...], "follow_up_untypeable": [...],
        #: "phase_error": str}。
        self.pending_follow_up_needs: dict[str, dict] = {}
        #: §六：**被采信**的那一轮里，自己不成立的补件申请（按节）。每条含它在自己那一份响应
        #: 里的序号、封闭原因码与可读原因。它与 `pending_follow_up_needs` 是两件事：那些是
        #: 「相位终止、诉求等待裁决」，这些是「本节已经写出内容，但某些申请自己不成立」。
        #: 二者都不阻断本节，也都不许丢——「诉求不成立」≠「没有诉求」。
        self.follow_up_rejections: dict[str, list[dict]] = {}
        self.section_errors: dict[str, str] = {}
        self.commit_counts: dict[str, int] = {}
        self.readback: dict[str, object] = {}
        self.scope: tuple = ()
        self.version_inputs: object = None
        self.report: object = None
        self.assembly_error: str = ""
        self.rollback: dict | None = None
        #: 本轮唯一的调用账本（`llm.budget.LLMCallBudget`）。判据、报告与对账都读它，
        #: 而不是各处自己数：计数只有一个来源，才谈得上「逐项守恒」。
        self.budget: object = None

    @property
    def drive_order(self) -> tuple[str, ...]:
        return tuple(s for s in SECTION_ORDER)

    def authority_of(self, section_id: str):
        """本节**定稿所用**的权威：有 successor 时是 successor，否则是注入的输入权威。"""
        return self.authorities.get(section_id) or self.inputs.authorities[section_id]


def _section_input(inputs: RealInputs, section_id: str):
    """一个 section 进入写作相位的一切（全部由本组合根注入，不在相位里猜）。"""
    from sections import company_worker as CW
    from sections import pack_writer as PW

    task = inputs.tasks[section_id]
    requirement = inputs.requirements[task.topic_ids[0]]
    projection = PW.ContractProjection.create(
        inputs.writing_spec, section_id=section_id,
        contract_version=requirement.contract_version,
        contract_fingerprint=requirement.contract_fingerprint)
    return CW.BackboneWriterSectionInput(
        task=task, authority=inputs.authorities[section_id], projection=projection,
        writing_spec=inputs.writing_spec, presentation_profile=inputs.presentation_profile,
        # 依赖指纹**逐字节**取自冻结投影：三节必须同族，组装器会逐项核对。
        dependency_fingerprint=str(inputs.projection.dependency_fingerprint),
        requirements=tuple(inputs.requirements[t] for t in task.topic_ids),
        # §九：本节**自己的**运行上下文（研究相位用的就是它）。有界重写里的每条 FollowUpNeed
        # 按自己的 target topic 的 requirement 解析依赖，但它们共享本节这一次运行的上下文。
        run_context=inputs.run_contexts.get(section_id))


def _drive_sections(state: RunState, *, llm_client, entailment_llm_client,
                    final_sentence_llm_client, section_store) -> None:
    """逐节驱动**唯一**写作主链；每节的失败如实记在该节名下，不牵连别的节。

    一节一个调用（各自成链、各自恰一次提交）——这是组合根的选择：本节链的原子单位是「一条
    section 链」，把三节塞进一次调用只会让「哪一节失败」在产物里无从分辨。

    走的是 **formal** 入口（§九）：验收 runner 不接受 `not_injected`，因此无 store 时不是
    「少落一次库」，而是本节 typed 失败。

    `final_sentence_llm_client`：最终句语义门 B 的客户端。**它不是可选项**——写作相位的定稿路径
    在缺注入时当场抛出（`_finalize_section` 里的 fail-closed 前置），因为「没有决定」不得被读成
    「已核验」。组合根在这一层把同一个客户端交给三节，而不是让相位自己去造一个。
    """
    _drive_into(state.inputs, sections=state.sections, errors=state.section_errors,
                commits=state.commit_counts, policy=state.policy,
                generated_at=state.generated_at, llm_client=llm_client,
                entailment_llm_client=entailment_llm_client,
                final_sentence_llm_client=final_sentence_llm_client,
                section_store=section_store,
                follow_up_runs=state.follow_up_runs,
                rejections=state.proposal_set_rejections,
                pending_follow_up=state.pending_follow_up_needs,
                follow_up_rejections=state.follow_up_rejections)
    # §六：把每节定稿所用的权威（可能是 FollowUp 产生的 successor）带进现场，供组装与判据使用。
    for section_id, section in state.sections.items():
        authority = getattr(section, "authority", None)
        if authority is not None:
            state.authorities[section_id] = authority


def _drive_into(inputs: RealInputs, *, sections: dict, errors: dict, commits: dict,
                policy, generated_at: str, llm_client, entailment_llm_client,
                final_sentence_llm_client, section_store,
                follow_up_runs: dict | None = None,
                rejections: dict | None = None,
                pending_follow_up: dict | None = None,
                follow_up_rejections: dict | None = None) -> None:
    """把三节写进调用方给的容器里（现场重建用它，因此不持有任何全局状态）。

    `follow_up_runs` 非空时逐节留下相位里真的发生过的补件运行（§三 F）；只留 `sections[0]`
    会把「跑了几次补件」丢掉，产物里就只剩一条组装错误字符串可看。

    `rejections` 非空时逐节留下**被整束拒绝的候选提案集**的 typed 审计（§二 3）。失败节同样
    要有：整节失败时，这份审计是「为什么这一节没有内容」的唯一结构化证据，而 `errors[section_id]`
    只有一条截断到 900 字符的字符串——「原始完整有序提案集 + typed 拒绝原因」都在异常对象里，
    不在这里取出来就会随异常一起消失。

    `pending_follow_up` 非空时逐节留下**相位无法裁决**的 `FollowUpNeed`（§二 2.6）：它们同样
    只挂在异常对象上。**留存 ≠ 执行**：本组合根只把它们落成待裁决提议，补件的裁决权在 Harness
    （Contract / 预算 / SourcePolicy），越权自动执行等于绕开那条门。

    `follow_up_rejections` 非空时逐节留下**被采信那一轮里自己不成立**的补件申请（§六）。它们
    与 `pending_follow_up` 是**不同**的两件事：那些是「相位终止、诉求等待裁决」，这些是「本节
    已经写出内容，但模型提的某几条申请自己不成立」。二者都不阻断本节，也都不得被丢掉——
    否则「模型提过一条填错的申请」会与「模型没有提过申请」在产物里变成同一件事。
    """
    from llm import budget as LB
    from llm import client as LLC
    from sections import company_worker as CW
    from sections import pack_writer as PW
    from sections import store as ST

    real_commit = ST.commit_section_chain_v2

    def _counting_commit(chain):
        commits[str(chain.draft.section_id)] = commits.get(
            str(chain.draft.section_id), 0) + 1
        return real_commit(chain)

    if section_store is not None:
        ST.commit_section_chain_v2 = _counting_commit
    try:
        for section_id in SECTION_ORDER:
            section_input = _section_input(inputs, section_id)
            try:
                # 归属作用域：本节发出的每一次请求都记到本节名下（含门前提案、有界重写、
                # 逐候选蕴含、门后组织、可选章级评估）。离开作用域即还原，不污染相邻的节。
                with LB.section_scope(section_id):
                    # §九：正式入口强制注入 Section Store，并要求每条链「恰一次提交 + 独立读回」。
                    phase = CW.run_formal_m930_writer_phase(
                        (section_input,), section_store=section_store, llm_client=llm_client,
                        entailment_llm_client=entailment_llm_client,
                        final_sentence_llm_client=final_sentence_llm_client, policy=policy,
                        store=inputs.pack_store, material_resolver=inputs.resolver,
                        # §九：真实组合根的 `dependencies_of`（按每条诉求自己的 target topic 解析）。
                        dependencies_of=inputs.dependencies_of,
                        created_at=generated_at, evaluated_at=generated_at)
            except LB.LLMCallBudgetError:
                # 预算门在**发出请求之前**拒绝：这不是「本节失败、别的节继续」——继续只会一路
                # 撞同一个上限、并在真实模式下继续烧已批准的额度。整轮停止，由 `execute_run`
                # 落一份带账本的 typed 拒绝报告。
                raise
            except LLC.LLMTruncatedResponse:
                # provider 截断了本次输出：同样不是「本节失败、别的节继续」。残缺的提案/段落/
                # 决定不得当成合格内容，也不得「重跑一次试试」；整轮停止，如实落拒绝产物。
                raise
            except Exception as exc:  # noqa: BLE001 — 失败如实记录，不伪装成「本节无内容」
                errors[section_id] = f"{type(exc).__name__}: {str(exc)[:900]}"
                # §二 3：整节失败时，被拒束的完整有序身份与 typed 原因只挂在异常对象上。这里
                # 不取出来，产物里就只剩上面那行截断字符串——「20 个候选被拒」与「20 个候选
                # 里有 3 个被拒」在 900 字符里分辨不出来。
                if rejections is not None:
                    rejections[section_id] = [
                        r.to_dict() for r in tuple(getattr(exc, "rejections", ()) or ())]
                # §二 2.6：被拒各轮 / 无法裁决的补件诉求随异常带出，落成**待裁决提议**。
                # 没有诉求就不写这一节（不发明空条目）——但「有诉求却查不到」从此不可能。
                if pending_follow_up is not None:
                    needs = [PW._jsonable(n) for n in
                             tuple(getattr(exc, "follow_up_needs", ()) or ())]
                    untypeable = [dict(u) for u in
                                  tuple(getattr(exc, "follow_up_untypeable", ()) or ())]
                    if needs or untypeable:
                        pending_follow_up[section_id] = {
                            "follow_up_needs": needs,
                            "follow_up_untypeable": untypeable,
                            "phase_error": f"{type(exc).__name__}: {str(exc)[:900]}"}
                continue
            sections[section_id] = phase.sections[0]
            if follow_up_runs is not None:
                follow_up_runs[section_id] = tuple(phase.follow_up_runs)
            if rejections is not None:
                # 成功节也可能有被拒束（重试过、最终那一束被采信）：**采信不追溯赦免**，
                # 被拒的仍然逐条留着。
                rejections[section_id] = [
                    r.to_dict() for r in tuple(
                        getattr(phase.sections[0], "proposal_set_rejections", ()) or ())]
            if follow_up_rejections is not None:
                # §六：被采信那一轮里**自己不成立**的补件申请。它们不阻断本节（Draft 已经过门），
                # 但「模型提过一条填错的申请」必须与「模型没有提过申请」在产物里分开。
                follow_up_rejections[section_id] = [
                    dict(r) for r in tuple(
                        getattr(phase.sections[0], "follow_up_rejections", ()) or ())]
    finally:
        if section_store is not None:
            ST.commit_section_chain_v2 = real_commit


def _readback_sections(state: RunState, section_store) -> None:
    from sections import store as ST

    for section_id, section in state.sections.items():
        loaded = ST.load_current_section_chain_v2(section.draft.draft_id)
        if loaded is not None:
            state.readback[section_id] = loaded


def _assemble(state: RunState) -> None:
    """组装整本报告（三节都必须在场；缺一节即如实记失败，不拼一份「部分可用」的报告）。"""
    from sections import report_assembler as RA

    inputs = state.inputs
    missing = [s for s in SECTION_ORDER if s not in state.sections]
    if missing:
        state.assembly_error = f"有章节未产出，不得组装整本报告：{missing}"
        return
    if len(state.readback) != len(state.sections):
        state.assembly_error = "有章节的 current 链没有读回，不得组装"
        return
    scope = tuple(RA.ScopeRequirement(section_id=s, title=inputs.tasks[s].title,
                                      topic_ids=tuple(inputs.tasks[s].topic_ids))
                  for s in SECTION_ORDER)
    # Topic Pack 集必须声明**完整** Pack 集，而不是「有 proposal 引用到的那些容器」：版本身份
    # 绑定的是本次真正提交的 Pack 集合（§四 1：完整、来自冻结身份根）。一节在权威侧被给了 Pack、
    # 却整节落下缺口（一条 Claim 都没写）时，它的 Pack 仍必须进版本 —— 否则「Pack 集不同、正文
    # 相同」的两次运行会撞上同一个 report_version。声明取自本 runner **自己跑出来的** Pack 集
    # （不是组装器内部怎么取权威容器），因此这条断言仍然是在核「组装器收到的权威」与「本轮实际
    # 提交的 Pack 集」是否同一集合。
    # 声明取自各节**定稿所用**的权威（§六：有界重写后是 successor 集）——不是本轮开跑前的基集：
    # 否则「收了一次补件材料」的这次运行会把版本绑在一个本节从未据以写作的 Pack 集上。
    topic_pack_ids: set[str] = set()
    for section_id in SECTION_ORDER:
        pack_set = getattr(state.authority_of(section_id), "pack_set", None)
        topic_pack_ids.update(str(p.pack_id) for p in tuple(getattr(pack_set, "packs", ()) or ()))
    financial_id = str(getattr(state.authority_of("financial").artifact, "artifact_id", ""))
    state.scope = scope
    state.version_inputs = RA.ReportVersionInputs(
        scope_input_fingerprint=inputs.scope_fingerprint,
        plan_id=inputs.projection.plan_id,
        selected_task_ids=tuple(sorted(inputs.tasks[s].task_id for s in SECTION_ORDER)),
        topic_pack_ids=tuple(sorted(topic_pack_ids)),
        financial_fact_pack_artifact_id=financial_id or None,
        model_policy_id=state.sections["company"].draft.model_policy,
        prompt_version=state.sections["company"].draft.prompt_version)
    section_inputs = tuple(RA.SectionAssemblyInput(
        task=inputs.tasks[s], authority=state.authority_of(s),
        draft=state.sections[s].draft, gate_result=state.sections[s].gate_result,
        aggregate_decisions=tuple(state.sections[s].aggregate_decisions),
        entailment_decisions=tuple(state.sections[s].entailment_decisions),
        acceptance=state.sections[s].acceptance, claims=tuple(state.sections[s].claims),
        narrative=state.sections[s].narrative,
        claim_narrative_dispositions=tuple(state.sections[s].claim_narrative_dispositions),
        dispositions=tuple(state.sections[s].dispositions),
        result=state.sections[s].result, evaluation=state.sections[s].evaluation,
        binding=state.sections[s].binding,
        # §12.4.4 第 4 步：把本节**定稿时形成的那一条**最终句决定交给组装器。组装器不轻信它
        # ——它按四样可重算输入自己重算 `NS.final_sentence_gate_state`，因此这里给错（或漏给）
        # 不会变成「通过」，而是当场落成 block / 守恒判据为红。
        final_sentence_decisions=tuple(state.sections[s].final_sentence_decisions),
    ) for s in SECTION_ORDER)
    try:
        state.report = RA.assemble_report(
            projection=inputs.projection, scope=scope, section_inputs=section_inputs,
            version_inputs=state.version_inputs, generated_at=state.generated_at)
    except Exception as exc:  # noqa: BLE001 — 组装被拒是**结论**，不是崩溃
        state.assembly_error = f"{type(exc).__name__}: {str(exc)[:900]}"


def _rollback_drive(state: RunState, *, llm_client, entailment_llm_client,
                    final_sentence_llm_client) -> dict | None:
    """半事务反例：在 run-local 空库上注入第 4 行插入失败，断言各 family 0 行。

    现场是**独立的**容器：本节真实产出的对象一张都不进这里，因此「各 family 0 行」不会被
    别处的写入污染。
    """
    from sections import store as ST

    db = state.run_dir / "rollback_probe.db"
    ST.init_db(db)
    probe: dict = {"db": db, "raised": False, "insert_attempts": 0}
    real_insert = ST._v2_insert
    attempts: list[str] = []

    def _explode(conn, family, **kwargs):
        attempts.append(family)
        # 第 4 行成员插入**起**一律失败：不只第一次抛出就停，否则后续节会继续插自己的行，
        # 「各 family 0 行」就会被后一节的行污染，反例本身失效。
        if len(attempts) >= 4:
            raise RuntimeError("injected: 从第 4 行成员插入起一律失败")
        return real_insert(conn, family, **kwargs)

    errors: dict[str, str] = {}
    ST._v2_insert = _explode
    try:
        # 注射点在事务内。`_drive_into` 按节吞异常并记账，所以「是否抛出」要看**它记下的账**，
        # 不能只看本函数有没有收到异常——否则注入成功反而被判成「没抛」。
        _drive_into(state.inputs, sections={}, errors=errors, commits={},
                    policy=state.policy, generated_at=state.generated_at,
                    llm_client=llm_client, entailment_llm_client=entailment_llm_client,
                    final_sentence_llm_client=final_sentence_llm_client,
                    section_store=ST)
    except Exception as exc:  # noqa: BLE001 — 逃到这一层也要如实记录
        errors["<escaped>"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    finally:
        ST._v2_insert = real_insert
    probe["raised"] = bool(errors)
    probe["error"] = next(iter(errors.values()), "")
    probe["insert_attempts"] = len(attempts)
    return probe


# ---------------------------------------------------------------------------
# 产物
# ---------------------------------------------------------------------------

#: 本次运行写入 run 目录的全部机器产物（`artifact_index.json` 逐个求哈希）。
ARTIFACTS = (
    "acceptance_report.json", "llm_call_ledger.json", "section_chain_v2.db",
    "section_drafts.json",
    "section_claims.json", "section_narratives.json", "section_results.json",
    "section_evaluations.json", "section_unresolved.json",
    "proposal_set_rejections.json", "follow_up_needs.json", "assembled_report.json",
    "report_preview.md", "before_after.md", "rollback_probe.db", "manifest.json",
    "manual_review.md", "source_manifest.json", "artifact_index.json",
    # §L2/§L4：跨文档逐份证据的人读版（「登记了三份 ≠ 读了三份」的落点）。
    "cross_document_evidence.md",
    # T1：**从权威 Pack 读回**的材料包（人读 + 机器读两个渲染，同一份读数）。
    # 它回答的是 `cross_document_evidence.md` 回答不了的那一问：这一次到底取到了**哪些原文**，
    # 每条原文的文档四轴身份、页/span 定位、topic/aspect 归属，以及它在 Pack 侧被保留还是拒绝、
    # 理由是什么。两个文件都进这里，因此都进 `artifact_index.json` 的逐文件 sha256 账。
    "material_pack.md", "material_pack.json",
    # §二（b）来源归属轴的读者面（`srattr-1`）。两个渲染出自同一次读数。
    "source_attribution.md", "source_attribution.json",
    # 指令 D §三：**失败侧**的只读门前诊断（逐字标注「未核验、不可发布」）。两个渲染出自同一次
    # 读数。它**不是** `SectionResult`、不进正文、没有任何门读它。
    "pre_gate_draft.md", "pre_gate_draft.json",
    # 本批新增：**受阻章节的不可发布预览**（`blocked-section-preview/2`）。它与上一条是两件事：
    # `pre_gate_draft` 只覆盖「本节根本没成形」，本件覆盖「本节**已经**成形、但整本报告因**别节**
    # 被拒」。两个渲染出自同一次读回，逐字标注「未核验、不可发布」，三栏分开摆。
    "blocked_section_preview.md", "blocked_section_preview.json",
    # P5：失败也可审计的**只读诊断**（逐文档选用/未选用、逐 aspect 的链与三类拒绝、
    # Writer 精确清单、被拒提案与待裁决补件）。它与报告分开：报告是判据的现场，这份是人读的
    # 诊断，不冒充任何 Pack/SectionDraft/SectionResult。
    "failure_diagnostics.json",
)


def _artifacts_payload(state: RunState) -> dict:
    """逐节产物（只做形状转换，不改写任何取值；被拒的节如实记在 `errors` 里）。"""
    drafts: dict = {}
    claims: list = []
    narratives: dict = {}
    results: dict = {}
    evaluations: dict = {}
    unresolved: list = []
    for section_id in SECTION_ORDER:
        section = state.sections.get(section_id)
        if section is None:
            continue
        drafts[section_id] = _j(section.draft)
        claims.extend(_j(c) for c in sorted(section.claims, key=lambda c: c.claim_id))
        narratives[section_id] = _j(section.narrative)
        results[section_id] = _j(section.result)
        evaluations[section_id] = {
            "evaluation": _j(section.evaluation), "binding": _j(section.binding),
            "summary": _j(getattr(section, "disposition_trace", {}))}
        unresolved.extend(_j(u) for u in section.result.unresolved)
    return {"drafts": drafts, "claims": claims, "narratives": narratives,
            "results": results, "evaluations": evaluations, "unresolved": unresolved}


def _blocked_section_preview_payload(state: RunState) -> dict:
    """受阻章节的**不可发布预览**载荷（本批新增的**第三个**出口；只读，不改任何门）。

    为什么需要第三个出口：正式出口只有两条——整本报告组装成功 ⇒ `report_preview.md`；
    本节根本没成形 ⇒ 失败侧门前诊断 `pre_gate_draft.*`（`pgr-1`）。真实 run r9 落在**第三条**
    现场：财务节**已经**有 `SectionDraft`/门侧决定/`SectionResult`/逐条引用（门前留存那条出口
    因此明确不覆盖它），而整本报告因为**公司节没产出**被拒 ⇒ `report_preview.md` **一个字都没写**。
    结果是「别节失败、**本节内容也丢了**」。本件把库里确实存在的东西读出来，逐字标注
    **不可发布**，并在同一份产物里分三栏摆开「已过门内容 / 被拒的门前草稿 / 替身组织器产出」。

    三条纪律落在这一处：

      * **它读 `acceptance_report.json`**（`assembly_error` / `report_version` / 节标题），因此
        必须在那一份写盘**之后**调用。它判读的正是「整本报告为什么没成」——而那个问题只在
        `report_preview.md` 写不出来时才需要回答。
      * **它不放宽任何门**：正式组装器对 `REWORK/BLOCKED/FAILED` 的拒绝判据（
        `sections/report_assembler.py`）与 runner 的「有章节未产出不得组装」一字未动。
      * **正文来源只回声声明，不推断**：真实档声明为 `model`、离线档声明为 `stub_organizer`，
        而且**只声明真的走到了草稿的节**。没有产出的节声明它「正文来自某处」是空话——
        `prose_origin` 的空档（`not_applicable`）本来就该显式留在产物上。
      * 本件自己失败**不得**弄坏整轮：`build_failed` 载荷自带「这是读不回来，不是没有内容」。
    """
    from evaluation import blocked_section_preview as BSP

    driven = [s for s in SECTION_ORDER if state.sections.get(s) is not None]
    stub, model = (driven, []) if state.mode == MODE_OFFLINE else ([], driven)
    try:
        return BSP.build_preview(state.run_dir, sections=SECTION_ORDER, source_kind="real_run",
                                 stub_sections=stub, model_sections=model)
    except Exception as exc:  # noqa: BLE001 —— 本件自己失败也要留下可读的一句
        return BSP.failed_payload(f"{type(exc).__name__}: {str(exc)[:400]}")


def _write_blocked_section_preview(state: RunState) -> dict:
    """写两个渲染（机器读 + 人读）。**必须无条件调用**：它存在的理由正是「报告被拒」那一刻。"""
    from evaluation import blocked_section_preview as BSP

    payload = _blocked_section_preview_payload(state)
    _write_json(state.run_dir / "blocked_section_preview.json", payload)
    _write_text(state.run_dir / "blocked_section_preview.md", BSP.render_md(payload))
    return payload


#: `proposal_set_rejections.json` 的载荷形状版本。字段增删必须改这个号——复核者的读法依赖它。
#: /1 → /2（3.4，2026-09-23）：每条记录新增 `candidate_audit`（逐候选 typed 原因 + 点名规则 +
#: 逐字高风险表面）、`named_subsections`（结构性给出被点名的栏目）与 `whole_set_rejected`，
#: 顶层新增逐候选原因词表。**读法变了**：旧读者会把「只有整束 kind」当成审计的全部。
#: /2 → /3（§二 确定性分批，2026-09-24）：每条记录新增 `batches` = **本轮逐批**的调用读数
#: （有序：`batch_id` / `label`（如 `2/4`）/ `aspect_ids` / `status`（`ok` | `truncated`）/
#: `call_id` / `output_tokens` / `finish_reason` / `shrink_depth` / `focus`）。
#: **读法又变了**：一次「尝试」现在**不是一个请求**——一轮写作 = 一次分批扫描，因此
#: `narration_call_id` 只是代表调用，而「这一轮到底问了什么、哪一批被截断、缩小了几层」
#: 必须从 `batches` 读。旧读者会把整束被拒读成「一次调用返回了一束坏 JSON」。
#: /3 → /4（§二 3 / C3 定向重提案，2026-09-25）：每条记录新增 `answered_by_attempt`
#: （这一次拒绝是否**已排定**由第几次生成定向回答，`null` = 没排定）与 `reproposal`
#: （被排定的那一轮真的产出结构化提案集时的**跨修订逐候选去向**：原束每条候选在下一修订里
#: 是 `carried_verbatim` / `carried_rebound` / `carried_ambiguous` 还是 `not_reexpressed`），
#: 顶层新增去向词表。**读法再变一次**：被拒的那一束不再只有「为什么被拒」——它现在带着
#: 「下一条路走得怎么样」，因此「模型有没有把无关候选悄悄删掉换取通过」**可以被读出来**；
#: 旧读者会把 `null` 读成「没有这次记录」，也会把「定向了但那一轮没成形」与「定向后原样
#: 重出」混成一件事。`destinations` 的键集恒等于该束 `candidate_ids`（完整、有序、不裁剪）。
#: /4 → /5（acc-24 / ① — §二 A7 批次谱系，2026-09-25）：每条 `batches[]` 新增
#: `parent_batch_id`（这一批是哪一批对半缩小的结果，`null` = 本轮的原始批）。**读法变了**：
#: 「这次截断后来被缩批答回来了没有」此后是可判的——从被截断那次调用的 `call_id` 定位到那条
#: 批记录，沿 `parent_batch_id` 收它的子孙，子树里有没有**无子孙的截断叶**就是答案。旧读者
#: 只能靠 `label` 的字符串前缀（`2/4` 与 `2/4·缩小1`）猜父子关系，并因此只能退回到「该节
#: 最终有没有 `SectionDraft`」这个**错的**判据上（真实 run r4 实证：两次已经恢复的截断被
#: 记成结局缺口）。
#: /7 → /8（M930-3「先证明能成稿」定点批 §一.2 **逐候选裁出**，2026-09-26）：每条记录新增
#: `carve_out`（`CandidateCarveOutTrace`）：这一束**没有被整束作废**，而是把不合格的那几条
#: 逐条拒掉、其余候选以**新修订**继续走链时的逐候选对账——原束每条候选要么带着**排除原因**
#: （`path_b_high_risk_surface` / `path_b_ineligible_material_scope` /
#: `candidate_structure_invalid`）与逐字表面，要么带着它在**新修订**里的 `next_candidate_id`；
#: 顶层新增裁出原因词表与 `carve_out_version`。**读法又变一次**：`carve_out=null` 此后是
#: **有歧义的**，它同时覆盖三种现场——（i）这一束根本没被裁出（走了定向重提案或整束
#: fail-closed），（ii）排定了裁出而那一轮没能产出新修订，（iii）裁出这一路不适用（一条都没
#: 幸存）。要分辨靠三样：`answered_by_attempt`（非 `null` ⇒ 走的是定向重提案）、
#: `reproposal`（非 `null` ⇒ 同前）、以及**写作侧** `writer_batch_audit` 里那一轮的
#: `carve_out: true` 与 `calls`。旧读者会把「被逐候选裁出」读成「整束被拒」——那正是本批
#: 要修掉的过度拒绝，两种结局在旧口径下完全同形。
#: 另有一条**不变式**（本版起由 `ProposalSetRejectionRecord` 强制）：`carve_out` 与
#: `reproposal` **互斥**；`carve_out.model_calls_added` 恒为 `0`（裁出不新增任何模型调用，
#: 被裁出者的原文仍在那条 `narration_call_id` 指向的 `logs/llm/*.jsonl` 里）。
#: 指令 D §三 → `proposal-set-rejections/9`：每条被拒记录新增 `retained_pre_gate`（门前留存：
#: 三档来源 + 逐批草稿正文 + 逐单元出处轴 + 该批自己的标签）。同样**只加字段**，但读法变了：
#: /8 里「一束被拒」在产物上只有**拒绝原因**可读，「这一轮到底写出过什么」读不出来——真实 run r8
#: 的 company 节因此只留下失败那一批的 `draft_unit_ids`，前三批已经写出的正文随异常消失（不可
#: 恢复：LLM 账本只有元数据）。旧读者会把「这一节门前什么都没有」当成事实，而真相可能是
#: 「写出来了、但整束被拒」。新字段说的正是后者，且**不改变这次拒绝**：它没有通过侧，任何门都不读它。
PROPOSAL_SET_REJECTIONS_SCHEMA_VERSION = "proposal-set-rejections/9"


def _psr_total(state: RunState) -> int:
    """被拒束总数（`manifest.json` 与诚实性说明共用的同一个计数）。"""
    return sum(len(v) for v in state.proposal_set_rejections.values())


def _candidate_audit_total(state: RunState) -> int:
    """被拒束里**逐候选**审计条目总数（3.4）。

    与 `_psr_total` 同一纪律：报告块与诚实性说明共用的**同一个**计数——两处各写一份
    `sum(...)` 必然漂移，而「本次给了多少条逐候选原因」正是读者用来判断审计是否完整的数。
    """
    return sum(len(item.get("candidate_audit", ()) or ())
               for rows in state.proposal_set_rejections.values() for item in rows)


def _directed_reproposal_total(state: RunState) -> dict:
    """C3 定向重提案的两个计数（`scheduled` 排定 / `traced` 真的产出了下一修订）。

    两个数**必须分开报**，因为它们是两件事：

      * `scheduled`（`answered_by_attempt is not None`）说的是「这一束在**排定**上有没有拿到那条
        定向路」——判据在拒绝发生的那一刻就已确定，与后续是否成功无关。
      * `traced`（`reproposal is not None`）说的是「排定的那一轮**真的**组出了结构化提案集」，
        因此才有跨修订逐候选去向可写。若那一轮以 `PackWriterError` / 预算耗尽收场，trace 恒为
        `None`——这是**诚实的 None**，不是「没定向」。

    只报其一都会误导：只报 `scheduled` 会把「定向了但没产出可比对象」读成「查过了、都还在」；
    只报 `traced` 会把「根本没排定」与「排定了但没成形」混成一件事。

    不变量：`traced <= scheduled`（有 trace 蕴含被排定）。它是 `ProposalSetRejectionRecord`
    的单向蕴含在产物侧的复核——违反即为产物自相矛盾，不能用「下游再解释」糊过去。
    """
    scheduled = traced = 0
    for rows in state.proposal_set_rejections.values():
        for item in rows:
            if item.get("answered_by_attempt") is not None:
                scheduled += 1
                if item.get("reproposal") is not None:
                    traced += 1
            elif item.get("reproposal") is not None:
                raise AssertionError(
                    "被拒记录带着跨修订去向却没有排定轮次——产物自相矛盾（traced 必须蕴含 scheduled）")
    return {"scheduled": scheduled, "traced": traced}


def _proposal_set_rejections_payload(state: RunState) -> dict:
    """§二 3：被整束拒绝的候选提案集的 typed 审计（逐节）。

    这份产物存在的唯一理由是让「原地删掉几个候选、再把剩下的冒充原提案集」**结构上可被发现**：

      * 一条记录对应**一整束**被拒的提案集（`ProposalSetRejectionRecord`），不是「其中几个候选」。
        被采信的那一束之外，每一次尝试都必须有记录；`sections[].llm_calls` 与
        「被拒束数 + 1」在 `pack_writer` 内已守恒核对（不守恒即 fail-closed）。
      * 已解析的束用内容寻址 id 标身份，未解析的束（`schema_invalid`）只能给模型自报标签——
        此时**不得**伪造派生 id。
      * 原文不在这里重复：`narration_call_id` 指向 `logs/llm/<ts>__<call_id>.jsonl`，那份记录里
        有完整 prompt 与 completion。本产物只留身份 + typed 原因 + 指针。
      * **拒绝不是缺口**：被拒束本身既不是 gap 也不是 block，不得转写成「材料缺失」；反过来，
        Contract 必需内容在相应范围检索后仍未满足时，仍必须另外生成有 Contract 依据的 gap。
      * 3.4：每条记录另带 `candidate_audit`（**逐候选** typed 原因）。它是**整束被拒的解释**，
        **不是**一张幸存者名单——`whole_set_rejected` 恒为 `true`，且逐候选键集恒等于该束的
        完整有序候选身份，因此「从这一束里挑几条留下」在产物里没有可表达的形态。
    """
    from sections import pack_writer as PW

    by_section = {s: list(state.proposal_set_rejections.get(s, ())) for s in SECTION_ORDER}
    return {
        "schema_version": PROPOSAL_SET_REJECTIONS_SCHEMA_VERSION,
        "rejection_kinds": list(PW.PROPOSAL_SET_REJECTION_KINDS),
        # 3.4：逐候选原因词表一并落盘。读者不必回头读 `pack_writer.py` 才能判读
        # `candidate_audit[].reasons`——判读窗口里的每个码都是一个可复核的判据。
        "candidate_reason_vocabulary": list(PW.PROPOSAL_SET_REJECTION_CANDIDATE_REASONS),
        "structured_kinds": list(PW.STRUCTURED_PROPOSAL_SET_REJECTION_KINDS),
        # C3：跨修订去向词表一并落盘（读者不必回头读 `pack_writer.py` 才能判读
        # `reproposal.destinations[].destination`）。它只到「还在不在、类型还一样不一样」为止。
        "candidate_reproposal_destinations": list(PW.CANDIDATE_REPROPOSAL_DESTINATIONS),
        "reproposal_note_version": PW.HIGH_RISK_REPROPOSAL_NOTE_VERSION,
        "reproposal_max_passes": PW.MAX_DIRECTED_REPROPOSAL_PASSES,
        "reproposal_trigger_kinds": list(PW.REPROPOSAL_TRIGGER_KINDS),
        # C4：批次形状纠正（`bsc-1`）。读者不必回头读 `pack_writer.py` 才能判读某批
        # 「为什么被重问了一次」「这一次重问算不算预算」。三件事一并落盘：
        #   * 附注版本与被纠正批次的触发码（两者相等由测试逐字核对）；
        #   * **同一批只纠正一次**的上限（再次失败即停，不是无限重问）；
        #   * 它与**截断缩小共用**同一份额外调用额度，`max_steps_per_round` 就是
        #     `_narration_structural_bound()` 读的那个数——纠正**不**抬高任何预算上限。
        "batch_shape_correction_note_version": PW.BATCH_SHAPE_CORRECTION_NOTE_VERSION,
        "batch_shape_correction_max_per_batch": PW.MAX_BATCH_SHAPE_CORRECTIONS_PER_BATCH,
        "batch_shape_correction_trigger_kind": PW.BATCH_SHAPE_CORRECTION_TRIGGER_KIND,
        "batch_shape_correction_slack_pool": {
            "max_steps_per_round": int(PW.MAX_SWEEP_SHRINK_STEPS),
            "shared_with": "batch_truncated_shrink",
            "new_budget": False,
        },
        # §一.2：**逐候选裁出**（`cco-1`）。读者不必回头读 `pack_writer.py` 才能判读
        # `carve_out` 这条记录。四件事一并落盘：
        #   * 规则版本与逐候选排除原因词表（封闭）；
        #   * 它与 `reproposal` 互斥（两种出口各自有独立的下一轮，同时排定就是凭空多出一轮）；
        #   * 它**不新增任何模型调用**（`model_calls_added` 恒为 `0`，逐条记录里可核）；
        #   * 它的**有界性**：裁出只在**非**裁出轮上排定（裁出轮自己不得再裁），因此「再裁一刀」
        #     不构成循环——上界由模型轮次的上界给出，不另设数字额度。
        "candidate_carve_out_version": PW.CANDIDATE_CARVE_OUT_VERSION,
        "candidate_carve_out_reasons": list(PW.CANDIDATE_CARVE_OUT_REASONS),
        "candidate_carve_out_model_calls_added": 0,
        "candidate_carve_out_exclusive_with": "reproposal",
        "candidate_carve_out_repeat_policy": {
            "on_carve_out_round": False,
            "numeric_quota": None,
            "reason": "裁出不允许在裁出轮上再裁；重复出现只可能来自新的模型轮，"
                      "而上界由模型轮次的上界给出",
        },
        "sections": by_section,
        "total_rejected_bundles": sum(len(v) for v in by_section.values()),
        "sections_completed": sorted(state.sections),
        "sections_failed": {s: e for s, e in state.section_errors.items()},
        "note": ("逐条记录**整束**被拒的候选提案集（完整有序身份 + typed 原因 + 逐候选原因 + "
                 "指向 logs/llm 的 narration_call_id）；被拒束不得被裁剪后冒充原集合，也不得被"
                 "当成 gap。`candidate_audit` 说明的是**整束为什么被拒**（逐条候选被点了什么名，"
                 "未被单独点名的记 `not_individually_implicated`），它没有通过侧，"
                 "**不得**被读成「哪几条可以留用」。C3：`path_b_high_risk_surface` 这一类被拒时"
                 "还会排定一次**定向重提案**（用的是已有那一轮重试，不新增调用额度），"
                 "`answered_by_attempt` 记「已排定由第几次生成回答」，`reproposal` 记"
                 "「被排定的那一轮里每条原候选的去向」。`reproposal=null` **只**说明"
                 "「本轮没有形成可核验的重提案去向」，它把两种现场合并在一起——**没排定**，或"
                 "**排定了而那一轮没能产出结构化提案集**——因此**不得**被读成「没有可写的跨修订"
                 "事实」，更不得读成「追过了、都还在」。要分辨是哪一种，看 `answered_by_attempt`："
                 "它为 `null` 才是「没排定」，非 `null` 而 `reproposal` 仍为 `null` 则是「排定了"
                 "但那一轮未成形」。本文件不复制提示词或模型原文，只留指针。"
                 "**C4（批次形状纠正）**：某一批的返回**形状不合法**（解析器在那一批上失败）时，"
                 "先按 `batch_shape_correction_note_version` 给**那一批**附上准确的错误与合法修法"
                 "重问**一次**，其余批次的请求与已拿到的返回一个字都不改；**同一批不再纠正第二次**，"
                 "再次失败即在那一轮落成 typed 拒绝。这一份额外调用与**截断缩小共用**同一份额度"
                 "（`batch_shape_correction_slack_pool`，不新增预算）。被丢弃的那一次返回里提过的"
                 "诉求仍逐条取入 `follow_up_needs.json`（「被丢弃」不等于「没提过」）；"
                 "它的逐批读数（含 `shape_corrections`、`slack_steps_used`）在写作侧产物"
                 "`writer_batch_audit` 里。**纠正不是重跑整轮**：整轮重跑会把与这次失败无关的"
                 "批次已经拿到的合法返回全部丢掉，本产物因此能分辨「哪一批被重问过」与"
                 "「哪一束被拒了」。"
                 "**§一.2（逐候选裁出，`candidate_carve_out_version`）**：一条记录被拒**不一定**"
                 "意味着它整束作废。束里只有**部分**候选踩线（携带高风险表面 / 支撑边越权）时，"
                 "把被点名的那几条**逐条**拒掉、其余候选以**新修订**继续走链，是本版起的一条出口："
                 "此时该记录多出一个 `carve_out`，逐条写明每条原候选的去向（带排除原因与逐字表面，"
                 "或带它在**新修订**里的 `next_candidate_id`），而**原束的留档一字不改**"
                 "（`whole_set_rejected` 仍为 `true`，`candidate_ids` 仍是那束的完整有序身份）。"
                 "裁出**不新增任何模型调用**（`model_calls_added` 恒为 `0`；该轮在写作侧产物里"
                 "读作 `carve_out: true` 且 `calls: 0`）。**读法**：`carve_out=null` 是**有歧义**的"
                 "——它同时覆盖「没走这条出口」「排定了而那一轮没能产出新修订」「一条都没幸存」"
                 "三种现场，要分辨得看 `answered_by_attempt` / `reproposal` 与写作侧那一轮的读数；"
                 "**不得**把 `carve_out=null` 读成「整束被拒」，也不得把它读成「裁过了」。"),
    }


def _psr_honesty(state: RunState) -> str:
    """§二 3：把「本节被拒过几束」与「本节最终有没有内容」分开说清楚。

    这两件事在本批之前是**同一个字符串**：`max_llm_retries=0` 时「一束被拒」与「本节失败」
    必然同时发生，产物里只有一条 `section_errors` 截断字符串。`max_llm_retries=1` 之后它们
    解耦了，因此必须分别陈述，且**拒绝不得被当成缺口**。
    """
    total = _psr_total(state)
    per_section = {s: len(state.proposal_set_rejections.get(s, ()))
                   for s in SECTION_ORDER if state.proposal_set_rejections.get(s)}
    if not total:
        return ("本次运行**没有**任何候选提案集被整束拒绝（`proposal_set_rejections.json` 为空）；"
                "这不等于「内容通过」——内容是否达标由 A1 与人工复核判定。")
    detail = "；".join(f"{s} 被拒 {n} 束" for s, n in sorted(per_section.items()))
    failed = sorted(s for s in per_section if s in state.section_errors)
    # 3.4：逐候选审计的**读法**必须在读者第一眼看到的地方讲清，而不是只藏在产物里。
    audited = _candidate_audit_total(state)
    # C3：定向重提案的读数同样要在第一眼可见——「排定了几次」「其中几次真的产出了下一修订」
    # 是两个不同的数，只报其一就会让「定向了但没有下一修订可比」看起来像「没定向」。
    directed = _directed_reproposal_total(state)
    return (f"本次运行有 **{total} 束**候选提案集被整束拒绝，逐条完整有序身份与 typed 原因见 "
            f"`proposal_set_rejections.json`（{detail}）。被拒的是**整束**——不存在「从 20 个候选里"
            "删掉 3 个、把剩下 17 个当成原提案集」这种做法：被拒的束从不进入聚合绑定门，"
            "重新写作时模型必须重新给出**完整** JSON，digest 重算。"
            + (f"同一份产物另给出**逐候选**原因（本次共 {audited} 条）：它解释整束为什么被拒，"
               "没被单独点名的那几条记作未被单独牵连——**不得**被读成「哪几条可以留用」，"
               "该产物里没有通过侧。" if audited else "")
            + (f"其中 {directed['scheduled']} 束被排定了一次**定向重提案**"
               f"（路径 B 高风险面；用的是已有那一轮重试，不新增调用额度），"
               f"其中 {directed['traced']} 束真的产出了下一修订、因此带出逐候选跨修订去向"
               "（保留 / 未再表达 / 同文歧义）：它回答的是「模型有没有为了通过而把无关候选"
               "悄悄删掉」——`reproposal=null` 只说明「本轮没有形成可核验的重提案去向」"
               "（**没排定**，或**排定了而那一轮未成形**，看 `answered_by_attempt` 分辨），"
               "既不是「没有可写的事实」，也不是「都还在」。"
               if directed["scheduled"] else "")
            + (f"其中 {failed} 整节最终无产出：本节的缺口**不是**因为没有被拒的那几个候选，"
               "而是所有尝试都未通过门。" if failed else "")
            + "最后：**被拒不是 gap**——缺口另有 Contract 依据与已检索范围，`not_used` 也不是 gap。")


#: `follow_up_needs.json` 的载荷形状版本。字段增删必须改这个号——复核者的读法依赖它。
#: `follow-up-needs/2`：新增 `follow_up_rejected_applications`（**被采信节**里自己不成立的申请）。
#: 它与 `sections[*].follow_up_untypeable`（相位终止节里不成立的申请）是**同一类记录、不同场景**：
#: 前者节有产出、后者节无产出。把两者混成一个键会让「本节写出来了，只是某条申请填错了」与
#: 「本节整个没写出来」在产物里变成同一件事。
FOLLOW_UP_NEEDS_SCHEMA_VERSION = "follow-up-needs/2"


def _pending_needs_total(state: RunState) -> int:
    """待裁决诉求总数（`manifest.json` 与诚实性说明共用的同一个计数）。"""
    return sum(len(v.get("follow_up_needs", ()))
               for v in state.pending_follow_up_needs.values())


def _follow_up_needs_payload(state: RunState) -> dict:
    """§二 2.6：相位**无法裁决**的 `FollowUpNeed`（逐节，待裁决提议）。

    这份产物存在的唯一理由是让「模型提出过什么补件诉求」在相位终止后仍然可查：

      * `status="pending_adjudication"`：**未裁决、未执行**。补件是否执行由 Harness 按 Contract、
        预算与 SourcePolicy 决定；本组合根只负责留存，**不得**把它读成「已经补过件」。
      * 它**不是** gap：缺口要有 Contract 依据与相应范围的检索记录；这里只有一条诉求。
      * `follow_up_untypeable` 是连 Contract 校验都没过的原始诉求（含不成立原因）——同样不丢：
        「诉求不成立」与「没有诉求」是两件事。
      * 已由 Harness 裁决并执行过的补件运行**不在这里**：它们是 `FollowUpExecutionResult`，
        逐节记在 `section_drafts.json` / phase 的 follow-up runs 里。两套身份不得混用。

    `follow_up_rejected_applications` 是同一件事在**另一侧**的读法：本节**写出来了**（Draft 过了
    确定性硬门），但被采信那一轮里模型提的某几条申请自己不成立（四个 id 没有逐字取自
    `requestable_aspects` 的同一行、或需求 id 对不上）。它们不阻断本节、也不是 gap，但绝不
    悄悄丢掉：丢掉的后果正是「诉求不成立」被读成「没有诉求」。逐条给出序号与封闭原因码。
    """
    by_section = {s: dict(state.pending_follow_up_needs[s]) for s in SECTION_ORDER
                  if state.pending_follow_up_needs.get(s)}
    rejected_applications = {s: [dict(r) for r in rows] for s, rows in
                             state.follow_up_rejections.items() if rows}
    return {
        "schema_version": FOLLOW_UP_NEEDS_SCHEMA_VERSION,
        "status": "pending_adjudication",
        "sections": by_section,
        "total_pending_needs": sum(len(v.get("follow_up_needs", ())) for v in by_section.values()),
        "total_untypeable": sum(len(v.get("follow_up_untypeable", ())) for v in by_section.values()),
        "follow_up_rejected_applications": rejected_applications,
        "total_rejected_applications": sum(len(v) for v in rejected_applications.values()),
        "adjudicated_runs": {s: len(tuple(runs))
                             for s, runs in state.follow_up_runs.items() if runs},
        "sections_completed": sorted(state.sections),
        "note": ("这些诉求**尚未被 Harness 裁决，也未被执行**：它们不因候选被拒或相位终止而丢失，"
                 "也不由本组合根自动执行。它们不是 gap，素材本身仍在权威/审计链里。"
                 "已执行的补件运行是另一套身份（FollowUpExecutionResult），不在本文件。"
                 "`follow_up_rejected_applications` 是**已写出内容**的节里自己不成立的申请"
                 "（逐条带原因码）：它记的是「模型提了、但这条不成立」，不是「本节缺了它」，"
                 "也不是缺口。"),
    }


def _follow_up_needs_honesty(state: RunState) -> str:
    """§二 2.6：把「提出过诉求」与「诉求已裁决执行」分开说清楚。"""
    total = _pending_needs_total(state)
    untypeable = sum(len(v.get("follow_up_untypeable", ()))
                     for v in state.pending_follow_up_needs.values())
    rejected = {s: len(rows) for s, rows in state.follow_up_rejections.items() if rows}
    rejected_total = sum(rejected.values())
    executed = {s: len(tuple(r)) for s, r in state.follow_up_runs.items() if r}
    # §六：**已写出内容**的节里那些自己不成立的申请也必须在诚实性说明里出现。只报待裁决那一侧，
    # 会让「模型提了一条填错的申请、本节照常写出来了」在报告里读成「本运行没有任何申请问题」。
    rejected_note = (
        f"另有 **{rejected_total} 条**申请自己不成立（"
        + "；".join(f"{s} {n} 条" for s, n in sorted(rejected.items()))
        + "）：这些节**已经写出内容**（Draft 过了确定性硬门），只是模型提的这几条申请没有"
          "逐字取自 `requestable_aspects` 的同一行（见 `follow_up_needs.json` 的"
          "`follow_up_rejected_applications`，逐条带原因码）。它们不是缺口，也不阻断本节。"
        if rejected_total else "")
    if not total and not untypeable and not rejected_total:
        return ("本次运行没有**待裁决**的 FollowUpNeed（`follow_up_needs.json` 为空）；"
                + (f"已由 Harness 裁决并执行的补件运行见 phase 的 follow-up runs（{executed}）。"
                   if executed else "本运行也没有发生任何补件运行。"))
    if not total and not untypeable:
        return ("本次运行没有**待裁决**的 FollowUpNeed（没有相位因此终止）；" + rejected_note)
    detail = "；".join(f"{s} 待裁决 {len(v.get('follow_up_needs', ()))} 条"
                      for s, v in sorted(state.pending_follow_up_needs.items()))
    return (f"本次运行有 **{total} 条** FollowUpNeed **待裁决、未执行**（{detail}"
            + (f"；另有 {untypeable} 条连 Contract 校验都未通过的原始诉求一并留存" if untypeable
               else "")
            + "）。它们随相位终止一起被留存，**不是** gap，也**没有**被自动执行——"
              "是否补件由 Harness 按 Contract、预算与 SourcePolicy 决定。"
            + (f"本运行另有已执行的补件运行 {executed}。" if executed else "")
            + ("　" + rejected_note if rejected_note else ""))


def _final_sentence_decision_lines(section, section_id: str) -> list[str]:
    """**只读**展示本节最终句语义决定（`nsfid-1`）：逐句一句话，说清「核没核、核成什么样」。

    §12.4.4 第 6 步（面试版只读展示）。三条纪律写在这里，因为它决定了这一块**能**说什么：

      * **只显示**：本函数不改正文、不重算任何门的期望值、不发任何请求、不写任何库。它读的是
        已经定稿的那一份正文与那一份决定——门在前一步就跑完了，显示不能反过来改门。
      * **「没有决定」不是「通过」**：本节有承载事实的最终句却没有（有效的）决定时，这里如实
        落成 `final_sentence_decision_missing` / `..._stale` / `..._duplicate`，而不是留白、
        也不写成「未核」。状态一律由 `NS.final_sentence_gate_state` 复算——与定稿协调器、
        组装器、Store 读回**同一份实现**，因此这里显示的档位与产物里那个 block 逐字一致。
      * **「没有对象可核」也不是「通过」**：本节**没有**承载事实的最终句时，`gate_state` 的
        结论是「不阻断」，但这里的措辞**不**写「有且仅有一条有效决定」（那种场合一条决定都
        没有），而是明说「没有事实原子可核」。这一档**不是**缺口，也**不是**已核验。
      * **覆盖面到哪儿为止**：只覆盖**段落里的最终句**。表格行不带句子身份（`NarrativeTableRow`
        的身份是行内容寻址的 `row_id`），单元格里的数字**不**经本门逐原子核验；它们仍受
        `natfid-1` 的表面比对与 Claim 级蕴含门保护。这条边界是**已知缺口**，如实写在下面，
        不得读成已覆盖。
    """
    from sections import narrative_schema as NS

    lines = [f"### 最终句语义决定（`{NS.FINAL_SENTENCE_DECISION_SCHEMA_VERSION}`，逐句只读回查）", ""]
    narrative = getattr(section, "narrative", None)
    if narrative is None:  # 真出现时如实说「读不出来」，不静默跳过
        return lines + ["本节正文对象读不出来：本块无从判读（**不是**「本节没有事实句」）", ""]
    decisions = tuple(getattr(section, "final_sentence_decisions", ()) or ())
    try:
        reason, detail = NS.final_sentence_gate_state(
            narrative=narrative, decisions=decisions,
            claims=tuple(getattr(section, "claims", ()) or ()),
            accepted_bindings=tuple(
                getattr(getattr(section, "acceptance", None), "accepted_bindings", ()) or ()))
        expected = NS.fact_bearing_sentence_ids(narrative)
    except Exception as exc:  # noqa: BLE001 —— 显示块不得把整份产物一起带走
        return lines + [f"**本节决定状态读不出来**（{type(exc).__name__}）："
                        "这既不是「通过」也不是「没有事实句」", ""]
    decision = decisions[0] if len(decisions) == 1 else None
    # 第三档必须与另外两档分开写：`reason is None` 有**两种**成因——「有一条有效决定」与
    # 「本节根本没有承载事实的最终句，本门无对象可核」。后者**没有**决定（`decisions` 为空），
    # 若也写成「有且仅有一条有效决定」，就是把「没有对象可核」冒充成「已核验通过」——
    # 恰恰是 §12.4.4 第 6 步点名禁止的那种读法。所以这里按 `expected` 再分一次岔。
    if reason is None:
        state = ("状态 `entailed`（有且仅有一条**有效**决定）" if expected
                 else "本节没有事实原子可核（**不是**「已核验通过」，也**不是**缺口："
                      "本门对空覆盖面不产出决定）")
    else:
        state = f"**未通过/未形成**：`{reason}`"
    lines.append(f"- 承载事实的最终句 {len(expected)} 句；决定 {len(decisions)} 条；{state}")
    if reason is not None:
        lines.append(f"  - {detail}")
    by_sentence: dict[str, list] = {}
    for row in decisions:
        for atom in row.atoms:
            by_sentence.setdefault(str(atom.sentence_id), []).append(atom)
    claim_text = {str(c.claim_id): str(c.text) for c in (getattr(section, "claims", ()) or ())}
    for paragraph in narrative.paragraphs:
        for sentence in paragraph.sentences:
            if sentence.sentence_id not in expected:
                continue  # `transition` 句不承载事实，本门无对象可核（见 `fact_bearing_sentence_ids`）
            atoms = by_sentence.get(str(sentence.sentence_id), [])
            rejected = [a for a in atoms if a.verdict != "entailed"]
            verdict = ("（本节没有唯一有效决定）" if decision is None
                       else "`rejected`" if rejected else f"`{decision.verdict}`")
            lines.append(f"  - `{sentence.sentence_id}`（{sentence.sentence_kind}）{verdict}；"
                         f"逐原子 {len(atoms)} 条；声明 Claim "
                         + ("、".join(f"`{cid}`" for cid in sentence.claim_ids) or "—"))
            lines.append(f"    - 句子：{sentence.text[:200]}")
            for atom in atoms:
                who = f"`{atom.claim_id}`" if atom.claim_id else "（没有一条已接受 Claim 声明它）"
                why = f"；原因码 `{atom.reason_code}`" if atom.reason_code else ""
                lines.append(f"    - 原子 `{atom.atom_kind}`：{atom.atom_surface} → "
                             f"`{atom.verdict}`{why}；由 {who} 声明；"
                             f"factual 支撑边 "
                             + ("、".join(f"`{b}`" for b in atom.accepted_binding_ids) or "—"))
            if not atoms:
                lines.append("    - 本句**没有逐原子读数**（决定缺失/过期，或本句没有可定位的"
                             "事实原子）：不得读成「已逐原子核验通过」")
    lines += ["", "**覆盖面边界（如实写）**：本块只覆盖**段落里的最终句**；表格行不带句子身份，"
              "单元格里的数字不经本门逐原子核验（仍受 `natfid-1` 表面比对与 Claim 级蕴含门"
              "保护）。这是**已知缺口**，不得读成已覆盖。",
              "本块只读：不参与任何判据、不改正文、不触发任何调用。"
              f"（声明 Claim 原文见 `claims` 侧：{len(claim_text)} 条）", ""]
    return lines


def _before_after_md(state: RunState) -> str:
    """人读对照：权威原文（before）→ 正文句子与其 Claim/binding 锚点（after）。"""
    lines = ["# Before / After（M930-3 验收）", "",
             "Before = 上游权威输入里的材料/事实原文与权威状态（本轮不改写）；",
             "After = 门后定稿的 final Narrative 句子（每句回指 Claim，Claim 再回指支撑边）。",
             "每节的「最终句语义决定」块给出**逐句**的核验状态（只读，不参与判据）。", ""]
    for section_id in SECTION_ORDER:
        section = state.sections.get(section_id)
        task = state.inputs.tasks[section_id]
        lines += [f"## {task.title}（section_id={section_id}）", ""]
        if section is None:
            lines += ["本节未产出：", "",
                      f"- 失败原因：`{state.section_errors.get(section_id, '')}`", ""]
            continue
        claim_text = {c.claim_id: c.text for c in section.claims}
        anchor: dict[str, str] = {}
        for binding in section.acceptance.accepted_bindings:
            fact = (binding.fact_id or binding.financial_fact_id or binding.note_fact_id
                    or binding.external_fact_id or binding.material_id or "")
            for claim in section.claims:
                if binding.accepted_support_binding_id in claim.accepted_binding_ids:
                    anchor.setdefault(claim.claim_id,
                                      f"{binding.authority_kind}:{binding.authority_container_id}:{fact}")
        lines += [f"- 链上计数：候选 {len(section.draft.claim_candidates)} / "
                  f"proposal {len(section.draft.proposed_support_refs)} / "
                  f"aggregate {len(section.aggregate_decisions)} / "
                  f"entailment {len(section.entailment_decisions)} / "
                  f"accepted binding {len(section.acceptance.accepted_bindings)} / "
                  f"Claim {len(section.claims)} / 段落 {len(section.narrative.paragraphs)}",
                  f"- section_status：`{section.result.status}`；"
                  f"评估结论：`{section.evaluation_decision}`；调用次数：{section.llm_calls}", "",
                  "| 正文句子（after） | 引用 Claim | 权威锚点 |", "|---|---|---|"]
        for paragraph in section.narrative.paragraphs:
            for sentence in paragraph.sentences:
                anchor_text = "、".join(
                    f"`{cid}`/`{anchor.get(cid, '?')}`" for cid in sentence.claim_ids) or "—"
                lines.append(f"| {sentence.text} | {sentence.sentence_kind}："
                             f"{', '.join(sentence.claim_ids) or '—'} | {anchor_text} |")
        lines += ["", "### 权威事实（before）", "",
                  "| 权威事实文本 | Claim |", "|---|---|"]
        for claim in sorted(section.claims, key=lambda c: c.claim_id):
            lines.append(f"| {claim_text.get(claim.claim_id, '')} | `{claim.claim_id}` |")
        lines += ["", "### 缺口与状态", ""]
        if section.result.unresolved:
            lines += ["| 缺口主题 | 权威状态 | 表达 |", "|---|---|---|"]
            for gap in section.result.unresolved:
                lines.append(f"| {gap.topic_id} | `{gap.state}` / `{gap.reason_code}` | "
                             f"{str(gap.detail).replace('|', '/')} |")
        else:
            lines.append("（本节没有缺口）")
        lines += [""] + _final_sentence_decision_lines(section, section_id) + [""]
    return "\n".join(lines)


def _source_manifest_md(state: RunState) -> str:
    """来源清单的人读视图：登记了什么、依据是什么、选了什么、没选的理由是什么。"""
    m = state.inputs.source_manifest
    lines = ["# M930-3 来源清单（人工阅读用；逐字段机器可读版见 `source_manifest.json`）", "",
             "本清单覆盖**全部**已登记的上传材料。**登记 ≠ 同权用于当前事实**："
             "每一份都在这里，能不能用于本轮事实另由「资格」与「选用」两列表达。", "",
             f"- 清单规则版本：`{m.policy_version}`",
             f"- 报告生成日（`report_as_of`）：`{m.report_as_of}`"
             f"（时区 `{m.report_timezone}`）",
             f"- 同一瞬间 UTC（`generated_at`）：`{m.generated_at}`",
             f"- 财务数据期末（`FinancialSnapshot.as_of_date`，**不是**报告生成日）："
             f"`{m.financial_data_cutoff}`",
             "", "## 已登记材料", "",
             "| 文档 | 内容版本 | 注册类型 | 有证据支持的类型判断 | 披露/发行日期 | "
             "内容报告期间 | 入库时间 | 资格 |",
             "|---|---|---|---|---|---|---|---|"]
    for e in m.entries:
        d = e.disclosure
        disc = d.date if d.date else (
            f"unknown（封面月粒度线索 {d.period_hint}，不升格、不回填）"
            if d.period_hint else "unknown（无可核实依据）")
        lines.append(
            f"| `{e.document_id}` | `{e.document_version}` | "
            f"`{e.registered_source_type}` | "
            f"{e.type_judgment.document_class or '未判定'}"
            f"（依据 {e.type_judgment.basis.locator if e.type_judgment.basis else '—'}） | "
            f"{disc} | {e.content_report_period.period or 'unknown'} | "
            f"{d.ingestion_time} | `{e.eligibility}` |")
    lines += ["", "### 逐份依据与来源政策影响", ""]
    for e in m.entries:
        lines += [f"**`{e.document_id}`**", "",
                  f"- 类型判断依据：{e.type_judgment.basis.text.strip()[:200] if e.type_judgment.basis else '未在封面找到可核实类型标记'}",
                  f"- 披露日期依据：{e.disclosure.basis}",
                  f"- 内容报告期间依据：{e.content_report_period.basis}",
                  f"- 资格依据：{e.eligibility_reason}",
                  f"- SourcePolicy 影响：{e.policy_effect.note}", ""]
    lines += [f"## 源集与选用记账（当前锚状态：`{m.current_state}`；`not_used` 的也在这里）", ""]
    for s in m.selection:
        lines += [f"- `{s.document_id}@{s.document_version}` → `{s.selection_state}`"
                  f" / 角色 `{getattr(s, 'source_role', 'not_used')}`"
                  f" / 序位 {getattr(s, 'retrieval_order', -1)}"
                  f" / `{s.reason_code}`：{s.reason}"]
    if getattr(m, "current_state_reason", None):
        lines += [f"- 当前锚无法解析的理由：{m.current_state_reason}"]
    if getattr(m, "registration_class_mismatches", ()):
        lines += ["", "## 注册类型与内容识别不一致（typed 审计，不是 gap）", ""]
        for mm in m.registration_class_mismatches:
            lines.append(
                f"- `{mm.document_key.document_id}@{mm.document_key.document_version}`："
                f"注册 `{mm.registered_class}` / 识别 `{mm.content_identified_class}`"
                f"（{mm.identification_basis}）")
    lines += ["", "## 清单级发现", ""]
    for f in m.provenance_findings:
        lines.append(f"- {f}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# §二（b）来源归属轴的**读者面**（`srattr-1`）
# ---------------------------------------------------------------------------
#
# 三条日期轴里，(a) **事实适用期** 与 (b) **来源归属** 与 (c) **`report_as_of`** 互不顶替。
# 本块落地的是 (b)：读者要能看出「这句话说的是**哪一份材料、哪个版本、哪一页**，那份材料的
# **披露日**是哪天（或不可核实）」。
#
# **为什么是独立产物而不是塞进正文**：正式章节正文由唯一渲染器从 final Narrative 产出，且
# 组装器对正文做**逐字节**重算比对（`report_assembler`），`markdown_fingerprint` 与
# `section_version` 都挂在它上面。把归属语插进正文句子里等于改动那条 wire 的身份派生，
# 超出本批授权面。归属语因此渲染成**读者面的对账产物**：与正文同一次定稿读数、逐句可回查，
# 但不冒充正文、不改正文指纹。它**不是**判据，也不放宽任何门。
#
# 归属语只由 `sections/source_attribution.py` 从**已登记身份**确定性渲染：写者一个字也不能写
# （路径 B 的候选里连期间都不许有，更不许有日期），因此不存在「模型自己说这是哪份材料」。

#: `source_attribution.json` 的载荷形状版本。字段增删必须改这个号。
SOURCE_ATTRIBUTION_ARTIFACT_SCHEMA_VERSION = "m930-3-source-attribution-1"

#: 逐条「**这一份材料**没能渲染出归属语」的 **typed** 原因码。逐条点名，不用一句泛化理由——
#: 三种来由意思完全不同：登记表里没有这一份 / 登记数据自身不合规 / 材料 locator 没有精确页码。
SOURCE_ATTRIBUTION_SKIP_REASONS = (
    "no_registered_entry",     # (document_id, document_version) 不在本轮来源清单里
    "entry_not_conforming",    # 登记条目的披露状态自身不合规（**不**洗成 unknown）
    "page_not_precise",        # 材料 locator 没有精确页码（不写「约第几页」）
)

#: 「**这一节**的定稿读数根本读不回来」的原因码。它与上面那三个**不是**一回事：上面说的是
#: 某一份材料渲染不出归属语，这里说的是这一节的 Claim／采信边／Narrative 有一块取不到，
#: 于是这一节**一句都读不出来**。两者必须分得开，否则「读不回来」会被读成「这一节没有带材料
#: 支撑边的句子」——那正是`_material_pack_readback_all` 那条纪律要防的静默错。
SOURCE_ATTRIBUTION_SECTION_UNAVAILABLE_REASONS = (
    "section_not_readable",    # 节的 claims / acceptance.accepted_bindings / narrative.paragraphs 取不到
)


def _source_attribution_rows(state: RunState, material_pack: dict) -> tuple[list, list]:
    """从**权威 Pack 读回**的逐材料 locator 取 `(material_id, doc, ver, name, page)` 行。

    行源是 `material_pack` 的同一次读数（不重新读 Pack，也不从 Writer prompt 倒抄）：
    归属语的「哪一页」必须与 `material_pack.md` 里那一条材料的 locator **逐字同源**，
    两处各读一次就迟早会对不上。
    """
    rows: list[tuple] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    for section_id in SECTION_ORDER:
        block = (material_pack.get("sections") or {}).get(section_id) or {}
        for entry in block.get("entries") or ():
            material_id = str(entry.get("material_id") or "")
            if not material_id or material_id in seen:
                continue
            seen.add(material_id)
            identity = entry.get("document_identity") or {}
            locator = entry.get("locator") or {}
            document_id = str(identity.get("document_id")
                              or locator.get("document_id") or "")
            document_version = str(identity.get("document_version")
                                   or locator.get("document_version") or "")
            page = locator.get("page")
            name = str(locator.get("source_name") or "")
            if not isinstance(page, int) or isinstance(page, bool) or page < 1:
                skipped.append({"material_id": material_id, "section_id": section_id,
                                "document_id": document_id,
                                "document_version": document_version,
                                "page": page, "reason_code": "page_not_precise"})
                continue
            rows.append((material_id, section_id, document_id, document_version, name, page))
    return rows, skipped


def _source_attribution_payload(state: RunState, material_pack: dict) -> dict:
    """来源归属的**入口**：它自己不抛（与 `_material_pack_readback_all` 同一条纪律）。

    读回写手的 bug 不能把整轮产物一起带走——那正是「失败不可审计」的最糟形态。构建失败时
    返回一份**明说「这份归属账本身没读成」**的载荷（`status="build_failed"`），而不是留一份
    看起来正常的空表——空表会被读成「本轮没有材料、也没有带支撑边的正文句」。
    """
    from sections import source_attribution as SRA

    try:
        return _source_attribution_readback(state, material_pack, SRA)
    except Exception as exc:  # noqa: BLE001 —— 读回写手的 bug 不得升级成 run 失败
        return {
            "schema_version": SOURCE_ATTRIBUTION_ARTIFACT_SCHEMA_VERSION,
            "status": "build_failed",
            "attribution_version": SRA.SOURCE_ATTRIBUTION_VERSION,
            "detail": f"来源归属读回构建期异常：{type(exc).__name__}: {str(exc)[:200]}",
            "disclosure_states": list(SRA.ATTRIBUTION_DISCLOSURE_STATES),
            "disclosure_unknown_label": SRA.DISCLOSURE_UNKNOWN_LABEL,
            "forbidden_disclosure_substitutes": list(SRA.FORBIDDEN_DISCLOSURE_SUBSTITUTES),
            "registered_documents": [], "materials": [], "sentences": [],
            "rendered_attributions": [], "skipped_materials": [],
            "sections_readback": [], "sections_unavailable": [],
            "skip_reason_codes": list(SOURCE_ATTRIBUTION_SKIP_REASONS),
            "section_unavailable_reason_codes":
                list(SOURCE_ATTRIBUTION_SECTION_UNAVAILABLE_REASONS),
            "note": ("**本次来源归属读回本身没读成**（`status=build_failed`）：上面的空表是"
                     "「读不回来」，**不是**「本轮没有材料」。逐节原因见 `detail`。它不影响"
                     "正文，也不改任何门——但这份账这次不能用。"),
        }


def _source_attribution_readback(state: RunState, material_pack: dict, SRA) -> dict:
    """来源归属的机器读版本：逐登记材料 + 逐 Pack 材料 + **逐句**的归属语。

    唯一真值来源是 `state.inputs.source_manifest.entries`（活对象，不是从 JSON 重读）：
    披露日可不可核实由 `harness/source_manifest.py` 判过一次，这里**只消费**那个判断结果。
    """
    registry = {(str(e.document_id), str(e.document_version)): e
                for e in state.inputs.source_manifest.entries}
    rows, skipped = _source_attribution_rows(state, material_pack)
    # 登记表里没有的那几份：**逐条**记 `no_registered_entry`，不进归属语表。
    resolvable: list[tuple] = []
    for row in rows:
        if (row[2], row[3]) not in registry:
            skipped.append({"material_id": row[0], "section_id": row[1],
                            "document_id": row[2], "document_version": row[3],
                            "page": row[5], "reason_code": "no_registered_entry"})
            continue
        resolvable.append(row)
    attributions = SRA.attributions_by_key(
        [(r[0], r[2], r[3], r[4], r[5]) for r in resolvable],
        entries_by_document=registry)
    # 行进了表、归属语仍没渲染出来 ⇒ 登记条目的披露状态自身不合规。**逐条**记，且**不**把它
    # 洗成 `unknown`——那是把「登记数据有问题」伪装成「披露日确实不可核实」。
    for row in resolvable:
        if row[0] not in attributions:
            skipped.append({"material_id": row[0], "section_id": row[1],
                            "document_id": row[2], "document_version": row[3],
                            "page": row[5], "reason_code": "entry_not_conforming"})

    sentences, readback, unavailable = _source_attribution_sentences(state, attributions)
    words = sorted({t for s in sentences for t in s["attribution_texts"]})
    return {
        "schema_version": SOURCE_ATTRIBUTION_ARTIFACT_SCHEMA_VERSION,
        "status": "partial" if unavailable else "readback",
        "attribution_version": SRA.SOURCE_ATTRIBUTION_VERSION,
        "disclosure_states": list(SRA.ATTRIBUTION_DISCLOSURE_STATES),
        "disclosure_unknown_label": SRA.DISCLOSURE_UNKNOWN_LABEL,
        "forbidden_disclosure_substitutes": list(SRA.FORBIDDEN_DISCLOSURE_SUBSTITUTES),
        "registered_documents": [
            {"document_id": str(e.document_id), "document_version": str(e.document_version),
             "source_name": str(e.source_name),
             "disclosure_state": str(e.disclosure.state),
             "disclosure_date": e.disclosure.date or "",
             "disclosure_basis": str(e.disclosure.basis or ""),
             "eligibility": str(e.eligibility)}
            for e in state.inputs.source_manifest.entries],
        "materials": [
            {"material_id": mid,
             "document_id": attributions[mid].document_id,
             "document_version": attributions[mid].document_version,
             "page_number": attributions[mid].page_number,
             "disclosure_state": attributions[mid].disclosure_state,
             "disclosure_date": attributions[mid].disclosure_date,
             "attribution_id": attributions[mid].attribution_id,
             "rendered": SRA.render_source_attribution(attributions[mid])}
            for mid in sorted(attributions)],
        "sentences": sentences,
        "rendered_attributions": words,
        "skipped_materials": sorted(skipped, key=lambda s: (s["reason_code"], s["material_id"])),
        "skip_reason_codes": list(SOURCE_ATTRIBUTION_SKIP_REASONS),
        "sections_readback": readback,
        "sections_unavailable": unavailable,
        "section_unavailable_reason_codes":
            list(SOURCE_ATTRIBUTION_SECTION_UNAVAILABLE_REASONS),
        "note": (
            "来源归属轴（`srattr-1`）：它回答的是「这句话说的是**哪一份材料、哪个版本、哪一页**，"
            "那份材料的**披露日**是哪天」，**不是**「截至报告生成日仍然如此」。披露日只在登记"
            "判断为 `verified` 时才有值；不可核实一律写「"
            f"{SRA.DISCLOSURE_UNKNOWN_LABEL}」，**不得**用入库时间／PDF 元数据／上传时间／财务"
            "期末顶替。归属语由**系统**从已登记身份确定性渲染，写者一个字也不能写；它对正文"
            "只作对账，**不改正文、不改正文指纹**，也不是判据。"
            + (" **本次逐句归属不完整**：`sections_unavailable` 里的节定稿读数取不到，"
               "它们**不是**「没有带材料支撑边的句子」，而是**没读回来**。"
               if unavailable else "")),
    }


def _source_attribution_sentences(state: RunState, attributions: dict) -> tuple[list, list, list]:
    """**逐句**归属语：每句 → 所声明的 Claim → 采信边 → 材料 → 系统渲染的归属语。

    同时把「一句话由两个不同**文档版本**共同支撑，而 Claim 与其引用**都不带期间**」显式标出：
    这正是 `§二 2` 要防的读法——**不能用相似文本抹平新旧材料的实质差异**，也不能让读者把它
    读成「一直如此」。只标出，不改写任何正文。

    **逐节兜底**（与 `_material_pack_readback_all` 同一条纪律）：某一节的定稿读数取不到时，
    记一条 `section_not_readable` + typed 原因并跳过**这一节**，不让整份产物跟着消失，也
    **不**把「读不回来」静默写成「这一节没有带材料支撑边的句子」——后一种写法会让读者以为
    该节真的没有事实句。返回 `(逐句表, 读回的节, 读不回来的节)`。
    """
    from sections import source_attribution as SRA

    out: list[dict] = []
    readback: list[str] = []
    unavailable: list[dict] = []
    for section_id in SECTION_ORDER:
        section = state.sections.get(section_id)
        if section is None:
            # 本轮根本没走到这一节（失败身份由 `acceptance_report.json` 的逐节错误另记）。
            continue
        missing = [name for name in ("claims", "acceptance", "narrative")
                   if getattr(section, name, None) is None]
        acceptance = getattr(section, "acceptance", None)
        narrative = getattr(section, "narrative", None)
        if acceptance is not None and getattr(acceptance, "accepted_bindings", None) is None:
            missing.append("acceptance.accepted_bindings")
        if narrative is not None and getattr(narrative, "paragraphs", None) is None:
            missing.append("narrative.paragraphs")
        if missing:
            unavailable.append({
                "section_id": section_id, "reason_code": "section_not_readable",
                "missing": missing,
                "detail": "本节定稿读数取不到：" + "、".join(missing)
                          + "。**不是**「本节没有带材料支撑边的句子」。"})
            continue
        readback.append(section_id)
        claims = {str(c.claim_id): c for c in section.claims}
        by_binding = {str(b.accepted_support_binding_id): b
                      for b in acceptance.accepted_bindings}
        for paragraph in narrative.paragraphs:
            for sentence in getattr(paragraph, "sentences", ()) or ():
                material_ids: list[str] = []
                versions: set[str] = set()
                periods: set[str] = set()
                for claim_id in getattr(sentence, "claim_ids", ()) or ():
                    claim = claims.get(str(claim_id))
                    if claim is None:
                        continue
                    for bid in getattr(claim, "accepted_binding_ids", ()) or ():
                        binding = by_binding.get(str(bid))
                        if binding is None:
                            continue
                        material_id = str(getattr(binding, "material_id", "") or "")
                        if not material_id:
                            continue
                        material_ids.append(material_id)
                        attr = attributions.get(material_id)
                        if attr is not None:
                            versions.add(f"{attr.document_id}@{attr.document_version}")
                    for ref in getattr(claim, "citation_refs", ()) or ():
                        if getattr(ref, "period", None):
                            periods.add(str(ref.period))
                material_ids = list(dict.fromkeys(material_ids))
                if not material_ids:
                    continue
                out.append({
                    "section_id": section_id,
                    "paragraph_id": getattr(paragraph, "paragraph_id", ""),
                    "sentence_id": getattr(sentence, "sentence_id", ""),
                    "index": getattr(sentence, "index", None),
                    "sentence_kind": getattr(sentence, "sentence_kind", ""),
                    "text": getattr(sentence, "text", ""),
                    "claim_ids": [str(c) for c in (getattr(sentence, "claim_ids", ()) or ())],
                    "material_ids": material_ids,
                    "attribution_ids": [attributions[m].attribution_id
                                        for m in material_ids if m in attributions],
                    "attribution_texts": [SRA.render_source_attribution(attributions[m])
                                          for m in material_ids if m in attributions],
                    "materials_without_attribution": [m for m in material_ids
                                                      if m not in attributions],
                    "document_versions": sorted(versions),
                    "declared_periods": sorted(periods),
                    # `§二 2` 的反面判据（只标注，不改正文）。
                    "multi_version_without_period": bool(len(versions) > 1 and not periods),
                })
    return out, readback, unavailable


def _source_attribution_md(payload: dict) -> str:
    """来源归属的人读版（与 `source_attribution.json` 同一次读数的两个渲染）。"""
    lines = ["# M930-3 来源归属（`srattr-1`）· 人读版", "",
             "机器可读版在 `source_attribution.json`；两者出自同一次读数。", "",
             "本件回答的是「读者凭什么知道这句话说的是**哪一份材料、哪个版本、哪一页**、"
             "那份材料的**披露日**是哪天」。它不是判据、不改正文、不改正文指纹，"
             "也**不**把归属语读成「截至报告生成日仍然如此」。", "",
             f"- 口径版本：`{payload['attribution_version']}`",
             f"- 披露日不可核实时的固定表述：`{payload['disclosure_unknown_label']}`",
             "- 被禁的披露日替代物（逐个点名，不得混进日期位）："
             + "、".join(f"`{x}`" for x in payload["forbidden_disclosure_substitutes"]),
             "", "## 已登记材料（披露日的唯一真值来源）", "",
             "| 文档 | 内容版本 | 材料名 | 披露/发行日期 | 资格 |", "|---|---|---|---|---|"]
    for e in payload["registered_documents"]:
        when = (e["disclosure_date"] if e["disclosure_state"] == "verified"
                else f"不可核实（渲染为「{payload['disclosure_unknown_label']}」）")
        lines.append(f"| `{e['document_id']}` | `{e['document_version']}` | "
                     f"{e['source_name']} | {when} | `{e['eligibility']}` |")
    lines += ["", "## 本轮 Pack 材料的归属语（逐条）", ""]
    if payload["materials"]:
        lines += ["| material_id | 文档 | 版本 | 页 | 归属语（系统渲染） |", "|---|---|---|---|---|"]
        for m in payload["materials"]:
            lines.append(f"| `{m['material_id']}` | `{m['document_id']}` | "
                         f"`{m['document_version']}` | {m['page_number']} | {m['rendered']} |")
    else:
        lines.append("（本轮没有任何材料渲染出归属语——**不是**「本轮没有材料」："
                     "逐条原因见下表。）")
    lines += ["", "### 没能渲染出归属语的材料（逐条 typed 原因）", ""]
    if payload["skipped_materials"]:
        lines += ["| material_id | 节 | 文档 | 版本 | 页 | 原因码 |", "|---|---|---|---|---|---|"]
        for s in payload["skipped_materials"]:
            lines.append(f"| `{s['material_id']}` | `{s['section_id']}` | `{s['document_id']}` | "
                         f"`{s['document_version']}` | {s['page']} | `{s['reason_code']}` |")
    else:
        lines.append("（本次没有任何材料被跳过。）")
    lines += ["", "## 正文逐句归属（读者面的逐句回查）", "",
              "「正文句 → Claim → 采信边 → 材料 → 系统渲染的归属语」。"
              "带 ⚠ 的句子由**两个不同文档版本**共同支撑，而 Claim 与其引用都不带期间——"
              "人读时不能把它读成「一直如此」，也不能用相似文本抹平新旧材料的实质差异。", ""]
    if payload.get("sections_unavailable"):
        lines += ["### 逐句归属**读不回来**的节（不是「本节没有事实句」）", "",
                  "| 节 | 原因码 | 取不到的读数 |", "|---|---|---|"]
        for u in payload["sections_unavailable"]:
            lines.append(f"| `{u['section_id']}` | `{u['reason_code']}` | "
                         f"{'、'.join(f'`{m}`' for m in u.get('missing') or [])} |")
        lines.append("")
    if payload["sentences"]:
        lines += ["| 节 | 句 | 类型 | 归属语 |", "|---|---|---|---|"]
        for s in payload["sentences"]:
            texts = "；".join(s["attribution_texts"]) or "（无可渲染归属语）"
            if s["materials_without_attribution"]:
                texts += ("；**未渲染**：" +
                          "、".join(f"`{m}`" for m in s["materials_without_attribution"]))
            flag = " ⚠ 多版本无期间" if s["multi_version_without_period"] else ""
            lines.append(f"| `{s['section_id']}` | `{s['paragraph_id']}` 第 {s['index']} 句"
                         f"（`{s['sentence_kind']}`） | `{s['sentence_id']}` | {texts}{flag} |")
    else:
        lines.append("（本次没有任何正文句带材料支撑边。）")
    flagged = [s for s in payload["sentences"] if s["multi_version_without_period"]]
    lines += ["", f"- 正文句总数（带材料支撑边的）：**{len(payload['sentences'])}**；"
                  f"其中「多版本无期间」标注：**{len(flagged)}** 句。",
              f"- 不同归属语条数：**{len(payload['rendered_attributions'])}**。",
              f"- 读回完整的节：**{len(payload.get('sections_readback') or [])}**；"
              f"读不回来的节：**{len(payload.get('sections_unavailable') or [])}**"
              "（后者**不是**「该节没有事实句」）。",
              "- 这一段只作**标注**：正文与正文指纹一个字都没改；`multi_version_without_period` "
              "不是判据结论，也不是「已抹平／未抹平」的判定。", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 指令 D §三：失败侧的**只读门前诊断视图**（`未核验、不可发布`）
# ---------------------------------------------------------------------------
#
# **它解决的是哪一件事**：真实 run r8 的 company 节里，一整轮 4 批里有 3 批 `status=ok`，第四批
# 的草稿单元没声明出处轴 ⇒ 整轮以 `schema_invalid` 终止。终止本身是对的（缺一批 ⇒ 提案集不完整
# ⇒ aggregate 必须 fail），但当时的产物把**前三批已经写出来的草稿正文**一起带走了：读者能看到的
# 只有一条属于**失败那一批**的 `draft_unit_ids`。于是「这一节到底有没有内容」这个问题在失败侧
# 变成了不可回答——那正是「失败不可审计」。
#
# **它是什么、不是什么**：它是 `ProposalSetRejectionRecord.retained_pre_gate` 的人读/机器读两个
# 渲染，逐字标注 `未核验、不可发布`。它**不是** `SectionResult`、**不是**正式预览、**不是**任何
# 判定对象的替代，不进正文、不进 `report_preview.md`，也不被任何门读取。它**不放宽任何门**：
# 被拒的那一束仍然是被拒的，本节仍然没有正式章。
#
# **它自己不抛**（与 `_material_pack_readback_all` / `_failure_diagnostics_payload` 同一条纪律）：
# 读回写手的 bug 不能把整轮产物一起带走。

#: `pre_gate_draft.json` 的载荷形状版本。字段增删必须改这个号。
PRE_GATE_DRAFT_ARTIFACT_SCHEMA_VERSION = "m930-3-pre-gate-draft-1"

#: 本节在**失败侧**的门口状态（封闭取值）。四者意思不同，不得合并：
#:   * `readback`：本节有正式 `SectionDraft` ⇒ 门前留存**不是**这一节的出口（它被采信了）。
#:   * `retained_unvetted`：本节未成形，但被拒的某一轮留下了门前已写出来的内容（逐字未核验）。
#:   * `nothing_retained`：本节未成形，且那些轮**没有**任何一批交出合法返回（`basis="none"`）。
#:     「无可留存」是**事实**，不是「我们没去查」。
#:   * `unavailable`：本节的门前留存**读不回来**（拒绝记录缺字段/形状不对）。它**不是**
#:     `nothing_retained`——把读回失败写成「没有留存」正是这条纪律要防的静默错。
PRE_GATE_DRAFT_SECTION_STATUSES = (
    "readback", "retained_unvetted", "nothing_retained", "unavailable")

#: 门口诊断上**逐字**印出的不可发布标注。**只有一处定义**在 `pack_writer`（留存对象自己带），
#: 这里只是把同一个串取来用——产物侧不得另写一句措辞，否则两处措辞会漂移。
_PRE_GATE_LABEL = "未核验、不可发布"

#: 写手侧「本轮没有任何一批交出合法返回」那一档的来源码（与
#: `pack_writer.PRE_GATE_RETENTION_EMPTY_BASIS` 同一个串，由 eval 逐字钉住相等）。
#: **它必须单独判，不能只判真假**：它是非空字符串，只判真值就会把「门前没有可留的东西」
#: 读成 `retained_unvetted`——那是把「无可留存」印成「留了一段未核验的内容」。
PRE_GATE_EMPTY_BASIS = "none"


def _pre_gate_section_title(state: RunState, section_id: str) -> str:
    """本节标题；现场没有 tasks 时留空串，**不**编一个标题（与 `_failure_diagnostics` 同口径）。"""
    tasks = getattr(getattr(state, "inputs", None), "tasks", None)
    task = None if tasks is None else tasks.get(section_id)
    return "" if task is None else str(getattr(task, "title", "") or "")


def _pre_gate_draft_section(state: RunState, section_id: str) -> dict:
    """一节的门口诊断：**逐轮**的拒绝原因 + **逐轮**留存下来的门前内容。"""
    title = _pre_gate_section_title(state, section_id)
    rows = list(state.proposal_set_rejections.get(section_id, ()) or ())
    section = state.sections.get(section_id)
    rounds: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            return {"section_id": section_id, "title": title, "status": "unavailable",
                    "section_error": "", "retained_rounds": [], "rejections": [],
                    "detail": (f"拒绝记录不是对象（得到 {type(row).__name__}）："
                               "本节的门前留存**读不回来**，不得读成「没有留存」")}
        kept = row.get("retained_pre_gate")
        rounds.append({
            "attempt": row.get("attempt"),
            "rejection_kind": row.get("rejection_kind"),
            "rejection_detail": row.get("rejection_detail"),
            # **逐批**的调用读数：哪一批 ok、哪一批被截断/未通过。失败那一批的原因在
            # `rejection_detail` 里逐字给出，这里给出它在整轮里的坐标。
            "batch_calls": [
                {"batch_id": b.get("batch_id"), "label": b.get("label"),
                 "status": b.get("status"), "aspect_ids": list(b.get("aspect_ids") or [])}
                for b in (row.get("batches") or ()) if isinstance(b, dict)],
            "retained_basis": (kept or {}).get("basis") if isinstance(kept, dict) else None,
            "retained_prose_unit_total": ((kept or {}).get("prose_unit_total")
                                          if isinstance(kept, dict) else None),
            "retained_batches": ([dict(b) for b in (kept.get("batches") or ())]
                                 if isinstance(kept, dict) else []),
        })
    if section is not None:
        status = "readback"
        detail = ("本节有正式 `SectionDraft`（被采信了）：门前留存**不是**这一节的出口。"
                  + ("本节另有被拒的轮次，逐轮原因见下。" if rounds else ""))
    elif not rows:
        status = "nothing_retained"
        detail = ("本节未成形，且**没有任何被拒的轮次**可查——因此也没有门前留存。"
                  "这一条说的是「没有可留的东西」，不是「留了但是空的」。")
    elif any(r["retained_basis"] and r["retained_basis"] != PRE_GATE_EMPTY_BASIS
             for r in rounds):
        status = "retained_unvetted"
        detail = (f"本节未成形；被拒的轮次里留有门前已写出来的草稿正文。**逐字未核验、"
                  f"不可发布**（{_PRE_GATE_LABEL}）：它没有通过任何门，也不得被引用进任何报告。")
    else:
        status = "nothing_retained"
        detail = ("本节未成形；被拒的每一轮都**没有**任何一批交出合法返回（`basis` 全为 "
                  "`none`）⇒ 门前没有可留存的内容。这是「无可留存」，不是「读不回来」。")
    return {"section_id": section_id, "title": title, "status": status,
            "section_error": str((state.section_errors or {}).get(section_id) or ""),
            "retained_rounds": rounds,
            "rejections": [{"attempt": r["attempt"], "rejection_kind": r["rejection_kind"],
                            "rejection_detail": r["rejection_detail"]} for r in rounds],
            "detail": detail}


def _pre_gate_draft_payload(state: RunState) -> dict:
    """门口诊断的**入口**：它自己不抛（与 `_material_pack_readback_all` 同一条纪律）。

    构建失败时返回一份**明说「这份诊断本身没读成」**的载荷（`status="build_failed"`），而不是
    留一份看起来正常的空表——空表会被读成「本节门前什么都没有」。
    """
    from sections import pack_writer as PW

    try:
        body = _pre_gate_draft_body(state, PW)
    except Exception as exc:  # noqa: BLE001 —— 读回写手的 bug 不得升级成 run 失败
        return {
            "schema_version": PRE_GATE_DRAFT_ARTIFACT_SCHEMA_VERSION,
            "status": "build_failed",
            "label": PW.PRE_GATE_RETENTION_LABEL,
            "retention_version": PW.PRE_GATE_RETENTION_VERSION,
            "section_statuses": list(PRE_GATE_DRAFT_SECTION_STATUSES),
            "detail": f"门口诊断构建期异常：{type(exc).__name__}: {str(exc)[:200]}",
            "sections": [], "prose_unit_total": 0,
            "note": (f"**本次门口诊断本身没读成**（`status=build_failed`）：下面的空表是"
                     f"「读不回来」，**不是**「本轮门前什么都没有」。它不影响正文，也不改任何门"
                     f"——但这份账这次不能用。"),
        }
    return body


def _pre_gate_draft_body(state: RunState, PW) -> dict:
    sections = [_pre_gate_draft_section(state, section_id)
                for section_id in SECTION_ORDER
                if section_id in state.sections or state.proposal_set_rejections.get(section_id)]
    return {
        "schema_version": PRE_GATE_DRAFT_ARTIFACT_SCHEMA_VERSION,
        "status": "readback",
        "label": PW.PRE_GATE_RETENTION_LABEL,
        "retention_version": PW.PRE_GATE_RETENTION_VERSION,
        "retention_bases": list(PW.PRE_GATE_RETENTION_BASES),
        "section_statuses": list(PRE_GATE_DRAFT_SECTION_STATUSES),
        "sections": sections,
        "prose_unit_total": sum(int(r.get("retained_prose_unit_total") or 0)
                                for s in sections for r in s["retained_rounds"]),
        "note": (
            f"**{PW.PRE_GATE_RETENTION_LABEL}**。本件是**失败侧**的只读诊断：本节没有成形时，"
            "把被拒那些轮里**已经写出来**的门前草稿正文、逐单元出处轴与该轮**逐批**的拒绝原因"
            "原样列出，供人判断「这一节到底有没有内容」以及「是哪一批、因为什么没通过」。"
            "它**不是** `SectionResult`、**不是**正式预览、**不是**任何判定对象的替代：不进正文、"
            "不进 `report_preview.md`，没有任何门读它，它也**不放宽**任何门——被拒的那一束仍然是"
            "被拒的，本节仍然没有正式章。门前草稿正文**逐字未核验**，不得引用进任何报告，也不得"
            "据此改判任何门。"),
    }


def _pre_gate_draft_md(payload: dict) -> str:
    """门口诊断的人读版（与 `pre_gate_draft.json` 同一次读数的两个渲染）。"""
    label = payload["label"]
    # 诚实性说明**只有一处定义**（载荷的 `note`）：人读版把它逐字印出来，而不是另写一句
    # 措辞。另写一句会让「哪些话是本件的定性」在同一份产物的两个渲染里各说各话——而这份
    # 定性恰恰是「不得把它读成正文」这条纪律的落点。
    lines = [f"# M930-3 门前草稿（失败侧只读诊断）· **{label}**", "",
             f"> {payload['note']}", "",
             f"机器可读版在 `pre_gate_draft.json`；两者出自同一次读数。留存口径 "
             f"`{payload['retention_version']}`。", ""]
    if payload.get("detail"):
        lines += [f"- 本次诊断读回异常：{payload['detail']}", ""]
    if not payload["sections"]:
        lines += ["（本次没有任何节点的门前留存可读。）", ""]
        return "\n".join(lines)
    for s in payload["sections"]:
        lines += [f"## {s['section_id']} · {s['title']}", "",
                  f"- 门口状态：`{s['status']}`", f"- 判读：{s['detail']}"]
        if s.get("section_error"):
            lines.append(f"- 本节错误：`{s['section_error']}`")
        lines.append("")
        if not s["retained_rounds"]:
            lines.append("（本节没有可读的被拒轮次。）")
            lines.append("")
            continue
        for r in s["retained_rounds"]:
            lines += [f"### 第 {r['attempt']} 轮 · 拒绝原因 `{r['rejection_kind']}`", "",
                      f"{r['rejection_detail']}", ""]
            if r["batch_calls"]:
                lines += ["| 批次 | 状态 | aspects |", "|---|---|---|"]
                for b in r["batch_calls"]:
                    lines.append(f"| `{b['label']}` | `{b['status']}` | "
                                 f"{len(b['aspect_ids'])} 项 |")
                lines.append("")
            if r["retained_basis"]:
                lines += [f"- 门前留存来源：`{r['retained_basis']}`；"
                          f"草稿单元合计 **{r['retained_prose_unit_total']}** 段。", ""]
                for b in r["retained_batches"]:
                    lines.append(f"#### 留存批次 {b['label']}"
                                 f"（aspects {len(b['aspect_ids'])} 项）")
                    lines.append("")
                    if not b["prose_units"]:
                        lines.append("（这一批没有写出门前草稿单元。）")
                        lines.append("")
                        continue
                    for u in b["prose_units"]:
                        origin = ("材料行 " + "、".join(f"`{m}`" for m in u["source_member_refs"])
                                  if u["source_member_refs"] else
                                  "权威事实行 " + "、".join(f"`{f}`"
                                                            for f in u["source_fact_refs"]))
                        lines += [f"- `{u['prose_unit_id']}`（第 {u['index']} 段；出处：{origin}；"
                                  f"原子候选 {len(u['atom_candidate_ids'])} 条）", "",
                                  f"  > {u['text']}", ""]
    lines += ["", f"- 门前留存草稿单元合计：**{payload['prose_unit_total']}** 段"
                  f"（**全部 {label}**）。", ""]
    return "\n".join(lines)


def _manual_review_md(state: RunState, gates: list[GateOutcome]) -> str:
    '''人工复核模板：机器门**不**代表「人读内容通过」。'''
    lines = ["# M930-3 验收 · 人工复核模板", "",
             "机器门（`acceptance_report.json`）证明的是**链路贯通与守恒**，不是内容质量。",
             "以下逐项请人工填写 `yes` / `no` 与理由；未填写前，本 run 不得当作「人读内容通过」。", "",
             "**整本报告被拒时，本节内容不一定也是空的**：拒绝的判据是「有章节未产出，不得组装"
             "整本报告」，因此**别节**失败会让已经过门的本节正文一个字都写不进 `report_preview.md`。"
             "要看「这一轮到底有没有内容可读」，读 `blocked_section_preview.md`——它逐节分三栏"
             "列出已过门内容 / 被拒的门前草稿 / 替身组织器产出，逐字标 **未核验、不可发布**。"
             "它**不是** `SectionResult`、**不是**正式预览、**不**放宽任何门。", ""]
    for gate in gates:
        lines += [f"## {gate.gate_id} {gate.title}", "",
                  f"- 机器结论：`{gate.status}`", f"- 判据说明：{gate.detail}", ""]
        for sub in gate.sub_gates:
            # 子门**分别可见**（§八 A1）：一个门里的多条判据不得被合并成一句 detail。
            lines += [f"### {sub['sub_gate_id']} {sub['title']}", "",
                      f"- 子门结论：`{sub['status']}`", f"- 子门判据：{sub['detail']}", ""]
        lines += ["- 人工复核：`<待填写>`", "- 理由：`<待填写>`", ""]
    obs = _composed_sentence_observation(state)
    lines += ["## 观察：多 Claim 自然句（**不是判据**，不得据此改判任何门）", "",
              "机器判据只有 A1（限定公司节）。下面按节列出实得情况，用于区分「机制不具备该能力」"
              "与「该节的真实语料只够一条原子」：", ""]
    for section_id in SECTION_ORDER:
        row = obs[section_id]
        if not row.get("sections_present"):
            lines.append(f"- `{section_id}`：本节未产出")
            continue
        lines.append(f"- `{section_id}`：claims={row['claims']}，"
                     f"路径 A 绑定={row['accepted_bindings_path_a']}，"
                     f"路径 B 绑定={row['accepted_bindings_path_b']}，"
                     f"多 Claim 句={row['multi_claim_sentence_count']}")
        for sentence in row["multi_claim_sentences"]:
            lines.append(f"  - `{sentence['sentence_id']}`（{len(sentence['claim_ids'])} 条 Claim）："
                         f"{sentence['text'][:200]}")
    lines += ["", "## 材料包（这一次到底取到了哪些原文）", "",
              "逐条原文、文档四轴身份、页/span locator、topic/aspect 归属与 Pack 侧处置去向，"
              "在 `material_pack.md`（人读）/ `material_pack.json`（机器读）里。"
              "这里只给计数与几个必须由人回答的问题：", ""]
    _mp_sections = _material_pack_readback_all(state).get("sections", {})
    for section_id in SECTION_ORDER:
        block = dict(_mp_sections.get(section_id) or {"status": "unavailable", "detail": "未读回"})
        if block.get("status") != "readback":
            lines.append(f"- `{section_id}`：**读不回来**（`{block.get('status')}`："
                         f"{block.get('detail', '')}）——注意「读不回来」不是「没有材料」。")
        else:
            lines.append(f"- `{section_id}`：材料 {block['entry_count']} 条"
                         f"（缺处置记录 "
                         f"{sum(1 for r in block['entries'] if r['disposition'] is None)} 条、"
                         f"原文读不出 "
                         f"{sum(1 for r in block['entries'] if r['text_status'] != 'resolved')} 条、"
                         f"第四轴取不到 "
                         f"{sum(1 for r in block['entries'] if r['document_identity_basis'] != 'source_set_member_same_id_and_version')} 条）")
    lines += ["",
              "- 材料原文是否确实是本主题该用的材料（不是别的主题/别的期间）：`<待填写>`",
              "- 理由：`<待填写>`",
              "- 被拒绝或被丢弃的材料，理由是否说得通：`<待填写>`",
              "- 理由：`<待填写>`", ""]
    lines += ["", "## 正文抽样（人工判断「像不像人读的内容」）", ""]
    for section_id in SECTION_ORDER:
        section = state.sections.get(section_id)
        lines += [f"### {section_id}", ""]
        lines.append("（本节未产出）" if section is None else section.result.markdown[:4000])
        lines.append("")
    return "\n".join(lines)


#: P5 只读诊断产物的载荷形状版本。字段增删必须改这个号：读法变了（例如「本节没有材料」与
#: 「本 run 未能读回验收」是两个不同结论），旧读者必须看得出来。
#: `failure-diagnostics/2`：逐节新增 `rejected_follow_up_applications`（**被采信**那一轮里
#: 自己不成立的补件申请）。老的读者会把「有产出但某条申请不成立」读成「本节一切正常」。
#: `/3`（M930-3 定点返修 ①）：逐 aspect 新增 `column_unmet`（本栏目未达 covered 的**逐条**
#: typed 原因 + 派发去路计数）。旧读者看不到它，只能从 Pack 反推「覆盖门没过」，于是
#: 「本轮预算没让检索发生」与「查了没命中」与「材料到了但话对不上栏目」被读成同一件事——
#: 那正是这一批要修掉的读法，故号必须前进。
FAILURE_DIAGNOSTICS_SCHEMA_VERSION = "failure-diagnostics/3"


def _unmet_column_reasons_closed_set() -> tuple[str, ...]:
    """闭集 `UNMET_COLUMN_REASONS` 的**唯一**取法：从 runtime 取，不在这里抄一份字面量。

    抄一份的代价是两份清单会漂：runtime 加了第六类，诊断侧的「逐条原因计数」里它会**默默
    缺席**，而缺席与「本次没命中」在产物上长得一样。
    """
    from harness import topic_runtime as TR
    return tuple(TR.UNMET_COLUMN_REASONS)


def _column_unmet_by_aspect(topic_results: object) -> dict[str, dict]:
    """从**运行现场**的逐 topic 结果里取「本栏目未达 covered 的逐条 typed 原因」。

    数据源是 `TopicRuntimeResult.gaps`（`harness/topic_runtime.py` 的 `declared_gaps`）。
    Pack 本体不带这一层，所以读数只能来自运行现场（见 `RealInputs.topic_results`）。

    三条纪律：

    1. **逐条独立**：`unmet_column_reasons` 是闭集 `UNMET_COLUMN_REASONS` 的子集，读回时
       逐条陈列，**不**塌成 `coverage_gate_not_met`（那是粗粒度的门名，不是原因）；
    2. **不可判定不是空**：本节没有运行结果（例如财务节不走 topic 研究相位）时返回的条目
       带 `available=False` 与 typed 说明，**不**返回空 `entries` 让读者读成「这一栏没问题」；
    3. **不编造**：这里只搬运 runtime 已经写下的字段，不重算、不推断、不按 aspect 反查
       Pack 补一个「像原因的原因」；
    4. **只收栏目级裁决条目**：`declared_gaps` 里还有若干**别的身份**的 gap
       （`aspect_terminal_gap` / `research_contract_gap` / `tree_material_gaps` /
       `tree_structure_unavailable` 等），它们回答的是另一类问题，且各自已有读回通道
       （Pack 的 `unresolved` / `contract_gaps`）。把它们也塞进这个键会让字段名与内容不符，
       也会把 9 条同名材料 gap 淹掉唯一那一条栏目级结论。判据是「带非空
       `unmet_column_reasons`」——那正是栏目级裁决的标记。
    """
    out: dict[str, dict] = {}
    results = tuple(topic_results or ())
    if not results:
        return out
    for result in results:
        for gap in tuple(getattr(result, "gaps", ()) or ()):
            if not isinstance(gap, dict):
                continue
            aspect_id = str(gap.get("aspect_id") or "")
            if not aspect_id:
                continue
            typed = tuple(str(r) for r in (gap.get("unmet_column_reasons") or ()))
            if not typed and str(gap.get("reason") or "") != "column_unmet":
                continue
            # 同一 aspect 可能同时有 `column_unmet`（未达 covered）与门名条目（覆盖门未过）：
            # 两者是**同一次判定的两个粒度**，读回时都要在，故按 `reason` 逐条收。
            entry = out.setdefault(aspect_id, {
                "available": True,
                "entries": [],
                "typed_reasons": [],
                # 闭集以 runtime 写下的为准；runtime 那一版没带 `closed_set` 时退回本仓唯一
                # 取法（`_unmet_column_reasons_closed_set`）——两条路都指向同一份 runtime 常量。
                "closed_set": list(gap.get("closed_set")
                                   or _unmet_column_reasons_closed_set()),
            })
            entry["entries"].append({
                "reason": str(gap.get("reason") or ""),
                "column_status": gap.get("column_status"),
                "unmet_column_reasons": list(typed),
                "dispatch_counts": dict(gap.get("dispatch_counts") or {}),
                "dispatched_without_call": dict(gap.get("dispatched_without_call") or {}),
                "material_count": gap.get("material_count"),
                "table_material_count": gap.get("table_material_count"),
                "table_object_material_count": gap.get("table_object_material_count"),
                "fact_count": gap.get("fact_count"),
                "rule_version": gap.get("rule_version"),
                "detail": str(gap.get("detail") or ""),
            })
            for reason in typed:
                if reason not in entry["typed_reasons"]:
                    entry["typed_reasons"].append(reason)
    return out


def _column_unmet_unavailable(detail: str) -> dict:
    """本节取不到 typed 栏目未达原因时的条目：**不可判定**，不是「没有问题」。"""
    return {"available": False, "entries": [], "typed_reasons": [],
            "closed_set": [], "detail": detail}

#: 无 `SectionDraft` 时逐 aspect 的**唯一**状态措辞（P5 §5 第 14 行）。
#: 它**不**等于「已证明本节没有材料」：本节链根本没跑完，无从读回。
NOT_READ_BACK_THIS_RUN = "本 run 未能读回验收"


def _failure_diagnostics_payload(state: "RunState") -> dict:
    """P5 诊断产物的**入口**：它自己不抛。

    诊断写手的 bug 不能把「整轮被拒」的产物一起带走——那恰好是「失败不可审计」的最糟形态：
    本轮已经失败了，写产物时再崩一次，连现场都没了。构建失败时返回一份**明说「诊断本身没读回」**
    的载荷（`_diagnostics_build_failure`）：`documents` / `sections` 一律 `None`，并带上异常名与
    消息，而不是留一份看起来正常的空壳（空壳会被读成「确实没有材料」）。
    """
    try:
        return _failure_diagnostics_body(state)
    except Exception as exc:  # noqa: BLE001 —— 诊断失败不得升级成 run 失败
        return _diagnostics_build_failure(state, exc)


def _diagnostics_build_failure(state: "RunState", exc: BaseException) -> dict:
    """诊断**自身**构建失败时的载荷：明说诊断没读回，不给任何可被当成结论的读数。"""
    run_dir = getattr(state, "run_dir", None)
    return {
        "schema_version": FAILURE_DIAGNOSTICS_SCHEMA_VERSION,
        "milestone": "M930-3",
        "runner_version": RUNNER_VERSION,
        "mode": str(getattr(state, "mode", "")),
        "generated_at": str(getattr(state, "generated_at", "")),
        "run_dir": ("" if run_dir is None else Path(run_dir).name),
        "not_read_back_label": NOT_READ_BACK_THIS_RUN,
        # None（不是 `{}` / `[]` / 0）：读不回来就是读不回来，空容器会被读成「确实是空的」。
        "documents": None,
        "sections": None,
        "diagnostics_error": f"{type(exc).__name__}: {str(exc)[:300]}",
        "note": ("**诊断产物本身没能读回**（原因见 `diagnostics_error`）：本轮逐文档选用/未选用、"
                 "逐 aspect 的链、Writer 精确清单与被拒提案都**无从判读**。此时"
                 f"`documents` / `sections` 是 `None` 而不是空容器——**不得**把它读成"
                 "「本次没有材料」。整轮的拒绝原因仍以 `acceptance_report.json` 为准。"),
    }


def _failure_diagnostics_body(state: "RunState") -> dict:
    """P5：**失败也可审计**的只读诊断产物（落本次 create-only 目录）。

    它回答四个问题，逐节、逐 aspect 地摊开，且**只读**——不参与任何判据、不改任何门的期望值、
    不写任何库、不冒充 `TopicPack` / `SectionDraft` / `SectionResult`：

      1. **逐文档选用/未选用**：本轮登记了哪些当前材料，哪一份被选中，其余为什么没被选中；
      2. **逐 aspect 的链**：原始材料（有界导航 + 真工具调用读回的候选）→ Pack 材料 →
         可写事实，以及**期间/归属/引用**三类拒绝各自的数量与 typed 原因；
      3. **交给 Writer 的精确清单**：`SectionDraft.material_manifest` 逐成员的字数 / 切句 /
         可用原子 / 逐条排除原因；
      4. **被拒提案与待裁决补件**：逐节计数 + 规范产物指针；本节**没有 `SectionDraft`** 时
         额外把这些行**内联**进来——整节失败时，那些行是本节的唯一结构化证据，只留指针等于
         让读者去另一个文件里猜哪几行属于这一节。

    没有 `SectionDraft` 的节：逐 aspect 的 `read_back_status` 写 `NOT_READ_BACK_THIS_RUN`
    （「本 run 未能读回验收」），并且**不**写出任何能被读成「已证明没有材料」的状态。此时仍然
    给出**输入侧**的权威面（Writer *本来会被给到*什么），但明确标为输入面而不是读回结果——
    「没有产出」与「没有材料」是两件事，本产物不允许把它们混成一句。
    """
    from sections import pack_writer as PW

    manifest = state.inputs.source_manifest
    documents = {
        "policy_version": str(getattr(manifest, "policy_version", "")),
        "company_id": str(getattr(manifest, "company_id", "")),
        "selected_document_id": getattr(manifest, "primary_document_id", None),
        "registered_total": len(tuple(getattr(manifest, "entries", ()) or ())),
        "entries": [{
            "document_id": str(entry.document_id),
            "document_version": str(entry.document_version),
            "source_name": str(entry.source_name),
            "registry_status": str(entry.registry_status),
            "registered_source_type": str(entry.registered_source_type),
            # 有证据支持的类型判断与注册值**分列**：注册值原样报告，不暗改历史 DB。
            "judged_document_class": entry.type_judgment.document_class,
            "type_basis": (None if entry.type_judgment.basis is None else
                           {"text": str(entry.type_judgment.basis.text)[:200],
                            "locator": str(entry.type_judgment.basis.locator)}),
            "eligibility": str(entry.eligibility),
            "eligibility_reason": str(entry.eligibility_reason),
            "disclosure_date": entry.disclosure.date,
            "disclosure_state": str(entry.disclosure.state),
            "content_report_period": entry.content_report_period.period,
            "evidence_set_version": entry.evidence_set_version,
        } for entry in tuple(getattr(manifest, "entries", ()) or ())],
        "selection": [{
            "document_id": str(row.document_id),
            "document_version": str(row.document_version),
            "selection_state": str(row.selection_state),
            "reason_code": str(row.reason_code),
            "reason": str(row.reason)[:400],
            "source_role": str(getattr(row, "source_role", "not_used")),
            "retrieval_order": int(getattr(row, "retrieval_order", -1)),
        } for row in tuple(getattr(manifest, "selection", ()) or ())],
        # `§L0.6`/`§L0.8`：源集成员的内容寻址身份（四轴），逐份可复算、不含路径与时间。
        "source_set": [k.to_dict()
                       for k in tuple(getattr(manifest, "document_keys", ()) or ())],
        "current_state": str(getattr(manifest, "current_state", "resolved")),
        "current_state_reason": getattr(manifest, "current_state_reason", None),
        # `§L0.7`：注册类型与内容识别不一致 → typed 审计，**不是** gap。
        "registration_class_mismatches": [
            m.to_dict() for m in tuple(
                getattr(manifest, "registration_class_mismatches", ()) or ())],
        "note": ("登记 ≠ 选用：全部已登记材料都在 `entries` 里，选用决定在 `selection` 里逐份带 "
                 "reason_code。**进入源集的材料不是「未被选中」**——同系列的旧期间成员是 "
                 "`history_and_conflict_source`、其他系列成员是 `topic_participating_source`，"
                 "两者都同等资格按主题检索；只有 `not_used` 才不作为本 run 的事实来源。"
                 "当前锚（`primary_document_id`）只是旧单文档接口的兼容读视图。"),
    }

    sections: dict[str, dict] = {}
    for section_id in SECTION_ORDER:
        task = state.inputs.tasks.get(section_id)
        section = state.sections.get(section_id)
        draft = getattr(section, "draft", None) if section is not None else None
        has_draft = draft is not None
        read_back_status = ("read_back_ok" if has_draft else NOT_READ_BACK_THIS_RUN)

        # 权威输入（**输入侧**）：与本节是否产出过 SectionDraft 无关。取不到就如实记原因，
        # 不让整份诊断因为一节取不到而失败。
        scan = None
        authority_error = ""
        try:
            authority = state.authority_of(section_id)
            if task is not None:
                scan = PW.scan_authority(authority, task)
        except Exception as exc:  # noqa: BLE001 —— 诊断失败不影响判据，如实记录
            authority_error = f"{type(exc).__name__}: {str(exc)[:200]}"

        # 冻结 Contract 面（aspect 的权威清单：这一节到底被要求写哪些 aspect）。
        requirement_face: dict[str, dict] = {}
        for topic_id in tuple(getattr(task, "topic_ids", ()) or ()):
            requirement = state.inputs.requirements.get(str(topic_id))
            for aspect in tuple(getattr(requirement, "aspects", ()) or ()):
                requirement_face[str(aspect.aspect_id)] = {
                    "topic_id": str(topic_id),
                    "question_id": str(getattr(aspect, "question_id", "") or ""),
                    "kind": str(getattr(aspect, "kind", "") or ""),
                    "time_scope": str(getattr(aspect, "time_scope", "") or ""),
                    "missing_policy": str(getattr(aspect, "missing_policy", "") or ""),
                    "requirement_text": str(getattr(aspect, "requirement_text", ""))[:400],
                }

        # 逐 aspect 的召回 / Pack / 可写事实三层。有产出时从**同一份**源头对账里取（不另写一套
        # 扫描实现）；无产出时如实说明这一层读不到。
        recon = _a1_source_reconciliation(state, section_id)
        recon_ok = recon.get("status") == "read_only_diagnostic"
        # 第七层（Writer 精确材料清单）有**自己**的状态，不等于整份对账的状态：前六层是研究相位的
        # 事实，写作失败也照样交得出来，所以整份对账可以「成功」而第七层读不回。整份对账整个
        # 取不到时（连 task 都没有），第七层当然也读不回——两种情形都落到同一个 `False`。
        manifest_read = recon.get("writer_manifest_status") == "read_back_ok"
        # 逐 aspect 的**验收读回**三层（`raw` / `pack` / `content_dispositions`）要两个条件同时成立
        # 才报数：节有 `SectionDraft`（`read_back_status == read_back_ok`）且对账本身跑得通。
        # 只要缺一个，这三层就必须是 `None`：本节没有 `SectionDraft` 时，逐 aspect 状态写的是
        # 「本 run 未能读回验收」，此时再报一串 0 会被读成「这几层确实是空的」——正是本函数
        # 文档里禁止的那种「把一个『没读回』说成事实断言」。输入侧的读数（Contract 要求、
        # 权威覆盖、两类拒绝、可写事实）不受此限，它们不依赖本节有没有产出。
        aspect_readback_ok = recon_ok and has_draft
        recall_rows = list(recon.get("recall", ())) if recon_ok else []
        disposition_rows = list(recon.get("content_dispositions", ())) if recon_ok else []
        pack_rows = list(recon.get("packs", ())) if recon_ok else []
        nav_rows = list(recon.get("tree_navigation", ())) if recon_ok else []

        # 本栏目未达 covered 的**逐条** typed 原因：只从**运行现场**取（`topic_results`），
        # 因为它不在 Pack 本体上。**与 `recon_ok` 无关**：研究相位的结果在写作相位失败时
        # 照样成立，用 `recon_ok` 去拦会把「这一栏本轮为什么没拿到材料」一起藏掉——那正是
        # 本批要修的那种「读不到就什么都不说」。取不到时给**不可判定**条目，不给空 entries。
        column_unmet = _column_unmet_by_aspect(
            (getattr(state.inputs, "topic_results", None) or {}).get(section_id))
        column_unmet_unavailable: dict = (
            {} if column_unmet else
            _column_unmet_unavailable(
                "本节的 topic 研究运行结果不在本次验收输入里"
                "（财务节走 `FinancialFactPack`，不走 topic 研究相位），"
                "因此本栏目没有 typed 栏目未达原因可读——"
                "**不可判定**，不等于「本栏目没有问题」"))

        facts_by_aspect: dict[str, list[dict]] = {}
        if scan is not None:
            for entry in tuple(scan.facts or ()):
                row = {
                    "authority_kind": str(entry.authority_kind),
                    "container_identity": str(entry.container_identity),
                    "fact_id": str(entry.fact_id),
                    "topic_id": str(entry.topic_id),
                    "period": str(entry.period),
                    "scope": str(entry.scope),
                    "fact_type": str(entry.fact_type),
                    "required": bool(entry.required),
                    "text": str(entry.text)[:300],
                    # 引用侧：这条可写事实的引用是否绑到了 material / payload / locator。
                    # 「能写」必须包含「引得到」，因此三个都报。
                    "citation_binding": {
                        "material_id": entry.material_id,
                        "has_payload_ref": entry.payload_ref is not None,
                        "has_locator_ref": entry.locator_ref is not None,
                        "source_identity": str(entry.source_identity),
                        "content_fingerprint": str(entry.content_fingerprint),
                    },
                }
                for aspect_id in tuple(entry.aspect_ids or ()):
                    facts_by_aspect.setdefault(str(aspect_id), []).append(row)

        period_exclusions = [dict(row) for row in
                             (tuple(scan.period_exclusions) if scan is not None else ())]
        excluded_facts = [{"container_id": c, "fact_id": f, "topic_id": t}
                          for c, f, t in (tuple(scan.excluded_facts) if scan is not None else ())]

        # 这一份就是第七层的读数，因此按第七层自己的可读性取（读不回时**不**退化成空列表，
        # 空列表在这里会被读成「Writer 一份都没拿到」）。
        writer_face = recon.get("writer_manifest", ()) if manifest_read else ()
        writer_members_by_pack: dict[str, list[dict]] = {}
        for row in writer_face:
            writer_members_by_pack.setdefault(str(row.get("pack_id", "")), []).append(dict(row))

        aspect_ids = sorted(set(requirement_face) | set(facts_by_aspect)
                            | ({k for k in (scan.aspect_status if scan is not None else {})}))
        aspects: list[dict] = []
        for aspect_id in aspect_ids:
            face = requirement_face.get(aspect_id, {})
            raw_rows = [r for r in recall_rows if str(r.get("aspect_id")) == aspect_id]
            nav = [r for r in nav_rows if str(r.get("aspect_id")) == aspect_id]
            aspect_pack = [
                {"topic_id": str(pack.get("topic_id")),
                 "pack_id": str(pack.get("pack_id")),
                 "missing_pack": pack.get("missing_pack"),
                 "materials": next((int(a.get("materials") or 0)
                                    for a in pack.get("aspects", ())
                                    if str(a.get("aspect_id")) == aspect_id), None),
                 "facts": next((int(a.get("facts") or 0)
                                for a in pack.get("aspects", ())
                                if str(a.get("aspect_id")) == aspect_id), None),
                 "unresolved": next((int(a.get("unresolved") or 0)
                                     for a in pack.get("aspects", ())
                                     if str(a.get("aspect_id")) == aspect_id), None),
                 "gap_rows": [row for row in pack.get("gap_rows", ())
                              if aspect_id in tuple(row.get("aspect_ids", ()) or ())]}
                for pack in pack_rows]
            aspects.append({
                "aspect_id": aspect_id,
                "topic_id": face.get("topic_id", ""),
                "requirement_text": face.get("requirement_text", ""),
                "kind": face.get("kind", ""), "time_scope": face.get("time_scope", ""),
                "missing_policy": face.get("missing_policy", ""),
                # 本节这一 aspect 的**读回状态**：没有 SectionDraft 时这里是
                # 「本 run 未能读回验收」，不是「没有材料」。
                "read_back_status": read_back_status,
                "authority_status": (None if scan is None
                                     else scan.aspect_status.get(aspect_id)),
                # 读不到就是 `None`，**不是 0**：0 会被读成「这一层确实是空的」，而真相是
                # 「这次没读回来」。两者在产物里必须长得不一样。
                "raw": (None if not aspect_readback_ok else {
                    "tree_inspect_calls": sum(int(r.get("tool_calls") or 0)
                                              for r in raw_rows),
                    "candidate_count": sum(int(r.get("candidate_count") or 0)
                                           for r in raw_rows),
                    "recut_calls": sum(1 for r in raw_rows
                                       if r.get("layer") == "evidence_recut"),
                    "recut_materials": sum(int(r.get("recut_material_count") or 0)
                                           for r in raw_rows),
                    "structural_gaps": sum(int(r.get("gap_count") or 0) for r in raw_rows),
                    "tool_skipped": sum(int(r.get("skipped_count") or 0) for r in raw_rows),
                    "content_dispositions": sum(
                        int(r.get("content_disposition_count") or 0) for r in raw_rows),
                    "bounded_read_nodes": sum(int(r.get("read_node_count") or 0) for r in nav),
                    "unread_nodes": sum(int(r.get("unread_total") or 0) for r in nav),
                }),
                "pack": (None if not aspect_readback_ok else aspect_pack),
                "writable_facts": facts_by_aspect.get(aspect_id, []),
                # 期间 / 归属两类拒绝逐条 typed（不合并成一句）。**归属粒度是 topic**：权威自己的
                # 排除记录带的是 `(container, fact_id, topic_id)`，没有 aspect 字段——因此这里按
                # topic 归到该 topic 的各 aspect 下，**不**编造 aspect 级的归属。同一 topic 下多个
                # aspect 会看到同几行，这是读视图的重复，不是「同一条被拒了多次」。
                "rejections": {
                    "period": [row for row in period_exclusions
                               if str(row.get("topic_id")) == face.get("topic_id")],
                    "not_in_this_section_aspect_scope": [
                        row for row in excluded_facts
                        if str(row["topic_id"]) == face.get("topic_id")],
                },
                "content_dispositions": (None if not aspect_readback_ok else [
                    {k: v for k, v in row.items() if k != "aspect_id"}
                    for row in disposition_rows if str(row.get("aspect_id")) == aspect_id]),
                # 本栏目未达 covered 的**逐条** typed 原因（闭集 `UNMET_COLUMN_REASONS` 的子集）
                # 与本次派发的真实去路计数。四类彼此不可互推：`budget_blocked_dispatch` 是
                # 「本轮没查」，`dispatched_no_candidate` 是「派发了、导航给不出候选」，
                # `material_found_but_unsupported` 是「材料到了、话对不上栏目」，
                # `table_material_unqualified` 是「表未获资格」，`search_audit_incomplete` 是
                # 「查过但未命中条件不成立」。**不得**合并成笼统的 `coverage_gate_not_met`，
                # 更不得写成「语料里没有」。本节没有运行结果时为不可判定条目（不是空 entries）。
                "column_unmet": (column_unmet.get(aspect_id)
                                 or column_unmet_unavailable),
            })

        sections[section_id] = {
            "section_id": section_id,
            "title": "" if task is None else str(getattr(task, "title", "")),
            "topic_ids": [] if task is None else [str(t) for t in tuple(task.topic_ids or ())],
            "has_section_draft": has_draft,
            "read_back_status": read_back_status,
            "section_error": str(state.section_errors.get(section_id, "")),
            "draft_revision": (None if draft is None
                               else str(getattr(draft, "draft_revision", "") or "")),
            # **这一块的可读性只看第七层自己的状态**（`writer_manifest_status`），不看整份对账
            # 的 `status`。之前这里用整份对账的 `status`：真实的失败节里前六层照样可读，于是整份
            # 对账是 `read_only_diagnostic`，可第七层明明读不回，却被报成「清单读回了，而且成员是
            # 空列表」——把「没读回」写成了「确实一份都没有」。原因也随之被吞掉（旧代码去取
            # `recon["detail"]/["error"]`，而对账成功时这两个键都不存在，于是原因恒为空串）。
            "writer_material_manifest": {
                "members": ([dict(row) for row in writer_face] if manifest_read else None),
                # 读不回时 `member_count` 是 `None` 而不是 0：0 是「清单确实是空的」，这里说的是
                # 「这次没读回来」。
                "member_count": (len(writer_face) if manifest_read else None),
                "read_back_status": (read_back_status if manifest_read
                                     else NOT_READ_BACK_THIS_RUN),
                # 原因取第七层自己的 typed 原因。整份对账都取不到时（`unavailable`）第七层没有
                # 单独的原因，就沿用那份对账的 `detail`/`error`——仍然是一条如实的原因，不会
                # 变成空串。
                "read_back_note": ("" if manifest_read else str(
                    recon.get("writer_manifest_error")
                    or recon.get("detail") or recon.get("error") or "")[:200]),
            },
            "authority_face_note": (
                "本块是**输入侧**（这一节的权威本来会给 Writer 什么），不是读回结果。"
                + ("" if has_draft else
                   f"本节没有 `SectionDraft`：逐 aspect 状态为「{NOT_READ_BACK_THIS_RUN}」，"
                   "**不得**读成「已证明本节没有材料」。")),
            "authority_error": authority_error,
            "authority_coverage": (None if scan is None else dict(scan.coverage_counts)),
            # 本节 typed 栏目未达原因的**去路计数合并**：逐条原因各有多少个栏目落到它上面。
            # 这是**索引**，不是结论——每个栏目的逐条原因在 `aspects[*].column_unmet` 里。
            # 计数为 0 的那一条**仍然列出**（`closed_set` 全量在场）：缺席的原因与「这一栏
            # 没有命中任何原因」是两件事，读回时不能靠「列表里没有」去分辨。
            "column_unmet_summary": {
                "available": bool(column_unmet),
                "closed_set": sorted({r for row in column_unmet.values()
                                      for r in row["typed_reasons"]}),
                "reason_counts": {code: sum(1 for row in column_unmet.values()
                                            if code in row["typed_reasons"])
                                  for code in _unmet_column_reasons_closed_set()},
                "aspects_with_typed_reasons": sorted(column_unmet),
                "detail": ("" if column_unmet else
                           "本节没有 typed 栏目未达原因可读（财务节走 `FinancialFactPack`，"
                           "不走 topic 研究相位）——**不可判定**，不是「本节没有缺口」"),
            },
            "aspects": aspects,
            "rejected_proposal_bundles": {
                "count": len(state.proposal_set_rejections.get(section_id, ()) or ()),
                "artifact": "proposal_set_rejections.json",
                # 整节失败时内联：那些行只挂在异常对象上，报告里的 900 字符串分辨不出
                # 「20 个候选被拒」与「其中 3 个被拒」。
                "rows": ([dict(r) for r in state.proposal_set_rejections.get(section_id, ()) or ()]
                         if not has_draft else []),
            },
            "pending_follow_up_needs": {
                "count": len(state.pending_follow_up_needs.get(section_id, {})
                             .get("follow_up_needs", ()) or ()),
                "untypeable_count": len(state.pending_follow_up_needs.get(section_id, {})
                                        .get("follow_up_untypeable", ()) or ()),
                "artifact": "follow_up_needs.json",
                "rows": (dict(state.pending_follow_up_needs.get(section_id, {}))
                         if not has_draft else {}),
            },
            # §六：**被采信**那一轮里自己不成立的补件申请（本节有产出）。它与上面那一块是
            # 两种场景，逐条都以 `code` 给出封闭原因、`spec_index` 给出它在那份响应里的序号。
            # 内联**总是**给出（不按 `has_draft` 分流）：这一块的存在意义正是「节写出来了、
            # 但某条申请不成立」，按 `has_draft` 分流会把唯一该看的场景藏起来。
            "rejected_follow_up_applications": {
                "count": len(state.follow_up_rejections.get(section_id, ()) or ()),
                "artifact": "follow_up_needs.json",
                "rows": [dict(r) for r in state.follow_up_rejections.get(section_id, ()) or ()],
            },
        }

    return {
        "schema_version": FAILURE_DIAGNOSTICS_SCHEMA_VERSION,
        "milestone": "M930-3",
        "runner_version": RUNNER_VERSION,
        "mode": state.mode,
        "generated_at": state.generated_at,
        "run_dir": state.run_dir.name,
        "not_read_back_label": NOT_READ_BACK_THIS_RUN,
        "documents": documents,
        "sections": sections,
        "note": ("本产物是**只读诊断**：不参与任何判据、不改任何门的期望值、不写任何库、不冒充 "
                 "`TopicPack` / `SectionDraft` / `SectionResult`。`not_used` 不是 gap；"
                 f"没有 `SectionDraft` 的节逐 aspect 写「{NOT_READ_BACK_THIS_RUN}」，"
                 "**不得**读成「已证明没有材料」，也不得据此宣布任何门通过。"
                 "逐 aspect 的 `column_unmet` 是本栏目未达 covered 的**逐条** typed 原因"
                 "（闭集取自 `harness.topic_runtime.UNMET_COLUMN_REASONS`，"
                 "数据来自运行现场 `TopicRuntimeResult.gaps`）：五条**彼此不可互推**，"
                 "最常见的一条说的是**本轮根本没查**（预算没让这次检索发生），"
                 "它和「查了没命中」是两件事，因此逐条陈列、**不得**合并成笼统的"
                 " `coverage_gate_not_met`，更不得写成「语料里没有」；"
                 "本节取不到运行结果时该键是 `available=false` 的**不可判定**条目，"
                 "不是空的 `entries`。原因码逐条的完整清单只在 runtime 那一处，"
                 "读者按 `closed_set` 判读（本产物不复制这份清单）。"),
    }


def _write_run(state: RunState, *, before: dict, after: dict, gates: list[GateOutcome],
               llm_calls: dict, status: str) -> dict:
    from sections import pack_writer as PW

    payload = _artifacts_payload(state)
    _write_json(state.run_dir / "section_drafts.json", payload["drafts"])
    _write_json(state.run_dir / "section_claims.json", payload["claims"])
    _write_json(state.run_dir / "section_narratives.json", payload["narratives"])
    _write_json(state.run_dir / "section_results.json", payload["results"])
    _write_json(state.run_dir / "section_evaluations.json", payload["evaluations"])
    _write_json(state.run_dir / "section_unresolved.json", payload["unresolved"])
    # §二 3：被整束拒绝的候选提案集的 typed 审计。**先于** `acceptance_report.json` 写盘，
    # 因为报告里的诚实性说明要引用它的计数。
    _write_json(state.run_dir / "proposal_set_rejections.json",
                _proposal_set_rejections_payload(state))
    # §二 2.6：**待裁决**的 FollowUpNeed（相位终止时随异常带出）。与上面同一理由先写盘：
    # 诚实性说明要引用它的计数。
    _write_json(state.run_dir / "follow_up_needs.json", _follow_up_needs_payload(state))
    if state.report is not None:
        _write_json(state.run_dir / "assembled_report.json", {
            "report": _j(state.report),
            "scope_coverage": _j(state.report.scope_coverage),
            "gap_index": _j(state.report.gap_index),
            "conflict_index": _j(state.report.conflict_index),
            "retention": _j(state.report.retention)})
        _write_text(state.run_dir / "report_preview.md", state.report.markdown)
    _write_text(state.run_dir / "before_after.md", _before_after_md(state))
    _write_text(state.run_dir / "manual_review.md", _manual_review_md(state, gates))
    _write_json(state.run_dir / "llm_call_ledger.json",
                _ledger_payload(state, mode=state.mode, generated_at=state.generated_at,
                                run_dir=state.run_dir, status=status,
                                accounting=llm_calls))
    # 一.1–一.5：来源清单落成产物。人工复核要能回答「本轮一共登记了几份材料、每份的
    # 披露日期依据是什么、哪一份被选中、其余为什么没被选中」——只把结论写进 markdown 不够，
    # 逐字段的清单必须可回查。
    _write_json(state.run_dir / "source_manifest.json",
                _j(state.inputs.source_manifest))
    _write_text(state.run_dir / "source_manifest.md", _source_manifest_md(state))
    # §L2/§L4：跨文档**逐份**证据的人读版。它与 `source_manifest.md` 是两件事：清单说的是
    # 「库里登记了哪些材料、选了哪些」，这里说的是「本轮研究实际消费了哪些、逐份查了什么、
    # 材料去了哪」。「登记了三份」在清单里成立，「读了三份」只能在这里自证。
    _write_text(state.run_dir / "cross_document_evidence.md",
                _cross_document_md(state))
    # T1：**从权威 Pack 读回**的材料包。与上一份的分工：那份按来源答「哪一份查了没有」，
    # 这份按材料答「取到的原文是什么、保留还是拒绝、为什么」。两个渲染写自**同一次读回**，
    # 而且都读 Pack 本体——不从 Writer prompt 倒抄，也不止给 ID 与字数。
    material_pack = _material_pack_readback_all(state)
    _write_text(state.run_dir / "material_pack.md", _material_pack_md(state))
    _write_json(state.run_dir / "material_pack.json", material_pack)
    # §二（b）来源归属轴的**读者面**（`srattr-1`）。与材料包读回**同一次**读数（`material_pack`
    # 传进去，不重读 Pack）：归属语的「哪一页」必须与 `material_pack.md` 里那一条材料逐字同源。
    # 它只作对账——不改正文、不改正文指纹、不改任何门的期望值。
    source_attribution = _source_attribution_payload(state, material_pack)
    _write_json(state.run_dir / "source_attribution.json", source_attribution)
    _write_text(state.run_dir / "source_attribution.md", _source_attribution_md(source_attribution))
    pre_gate_draft = _pre_gate_draft_payload(state)
    _write_json(state.run_dir / "pre_gate_draft.json", pre_gate_draft)
    _write_text(state.run_dir / "pre_gate_draft.md", _pre_gate_draft_md(pre_gate_draft))
    # P5：**只读诊断**产物。放在 `manifest.json` 之前，因为清单要引用它的 schema 版本；而它
    # 自己只读现场（含被拒束与待裁决补件），不改写任何判据的期望值。
    diagnostics = _failure_diagnostics_payload(state)
    _write_json(state.run_dir / "failure_diagnostics.json", diagnostics)
    # §三 3（acc-28）：**本轮对外部来源做了什么**。算一次、两处（清单 + 报告顶部诚实性说明）
    # 读同一个结果：同一件事不许在产物里出现两种措辞。
    external_retrieval = _external_retrieval_honesty(state)

    manifest = {
        "milestone": "M930-3",
        "runner_version": RUNNER_VERSION,
        "acceptance_report_schema_version": ACCEPTANCE_REPORT_SCHEMA_VERSION,
        "mode": state.mode, "generated_at": state.generated_at,
        "writer_policy": {
            "policy_version": state.policy.policy_version,
            "prompt_version": state.policy.prompt_version,
            "renderer_version": state.policy.renderer_version,
            "rules_version": state.policy.rules_version,
            "model_policy": state.policy.model_policy,
            "max_llm_retries": state.policy.max_llm_retries},
        # 调用预算：**按轴、再按类别**给出上限与依据，不再只有一个「每节 N 次」的数字——那个
        # 数字是「写作/组织」一类的口径，不是全链总预算（蕴含、可选评估是另外的量）。
        "llm_budget": {
            "policy_version": APPROVED_BUDGET_POLICY_VERSION,
            "approved_model": APPROVED_BUDGET_MODEL,
            "policy": None if state.budget is None else state.budget.policy.to_dict(),
            "enforced": None if state.budget is None else bool(state.budget.enforce),
            "actual": llm_calls,
            "artifact": "llm_call_ledger.json",
            # **研究侧上限单独成行**（不并进写作轴的整轮上限）：读这一段的人必须看得到两条轴
            # 各自的上限与各自的批准状态，以及「镜像是怎么复算出来的」。
            "research_axis": _research_axis_report_block(state),
            # 数字从 `policy.categories[]` **现场求和**，不在文案里写死：写作轴的整轮上限随
            # 结构上界演进过（`m930-3-budget-7`：244 → 250），写死的旧数会让报告印出一个不存在
            # 的上限——而「写作轴上限不是全部 provider 调用总上限」这句话正是靠这个数字读的。
            "note": ("`policy.categories[]` 是**写作轴**逐类上限与批准依据，"
                     "`policy.axes[]` 是**研究轴**上限（每 topic / 整轮）。两条轴的上限互不消耗："
                     f"`total_max_attempts={_writing_axis_total_attempts()}` 是**写作轴**上限，"
                     "**不是全部 provider 调用的总上限**"
                     "（研究轴另有 `research_axis.axis_max_attempts`）。`actual` 里三个计数视图"
                     "必须逐项相等，并按轴分列。")},
        "contract": {"version": str(state.inputs.contract.contract_version),
                     "fingerprint": str(state.inputs.projection.contract_fingerprint)},
        "projection_id": str(state.inputs.projection.projection_id),
        "demo_scope_fingerprint": state.inputs.scope_fingerprint,
        # 时钟与日期（O-11）：`generated_at` 与 `report_as_of` 由**同一次采样**派生，
        # 二者是不同字段。`financial.snapshot.as_of_date` 只是财务期末，不是报告生成日。
        "report_as_of": state.inputs.report_as_of,
        "generated_at_utc": state.inputs.generated_at,
        "report_timezone": state.inputs.report_timezone,
        "report_as_of_derivation": (
            "report_as_of = 本轮唯一时钟瞬间（generated_at_utc）按 report_timezone 换算的"
            "报告生成日；二者同源，不得分别采样。"),
        "company_id": state.inputs.company_id,
        # `subj-1`（P4）：主体是**本次报告输入的声明**，财务库只被用来核对它。这一块让读者
        # 看得出「谁被写」是输入决定的，不是库里排序第一决定的；两个日期也在这里各就各位。
        "subject_declaration": _subject_declaration_block(state),
        "document": {"document_id": state.inputs.document_id,
                     "document_version": state.inputs.document_version,
                     "raw_pdf_sha256": state.inputs.raw_pdf_sha256,
                     "evidence_set_version": state.inputs.evidence_set_version},
        # 一.1–一.5：来源清单（全部已登记材料 + 逐份依据 + 选用记账）。完整清单在
        # `source_manifest.json`，人读版在 `source_manifest.md`。
        "source_manifest": {
            "policy_version": state.inputs.source_manifest.policy_version,
            "artifact": "source_manifest.json",
            "registered_documents": len(state.inputs.source_manifest.entries),
            "selected_document_id": state.inputs.source_manifest.primary_document_id,
            "selection": [{"document_id": s.document_id,
                           "document_version": s.document_version,
                           "selection_state": s.selection_state,
                           "reason_code": s.reason_code}
                          for s in state.inputs.source_manifest.selection],
            "provenance_findings": list(state.inputs.source_manifest.provenance_findings),
            "note": ("清单登记**全部**已登记材料；未选中的材料同样在列并带 reason_code。"
                     "注册类型与有证据支持的类型判断**分列**，注册值原样报告、不暗改 DB。")},
        # §L2/§L4：**跨文档四块只读证据**（逐节）。「登记了三份 ≠ 读了三份」在这里逐份、
        # 逐 `(aspect, 来源)` 摊开：实际消费的源集 / 检索前责任 / 四臂结果 / 材料去向。
        # 读自 Pack 本体与定稿现场，缺项如实记 `unavailable`（不记零命中）。
        "cross_document_evidence": {
            s: _cross_document_evidence(state, s) for s in topic_section_ids()},
        # T1：**权威 Pack 读回的材料包**（逐节逐条，完整原文 + 四轴身份 + locator +
        # topic/aspect + 处置去向与理由）。它与上面的 `cross_document_evidence` 是两本账：
        # 上面按**来源**答「哪一份查了没有」，这里按**材料**答「取到的原文是什么、留还是拒」。
        "material_pack": {
            "schema_version": MATERIAL_PACK_SCHEMA_VERSION,
            "artifact": "material_pack.json",
            "human_readable_artifact": "material_pack.md",
            "status": material_pack.get("status"),
            "entry_count": material_pack.get("entry_count"),
            "sections_readback": material_pack.get("sections_readback"),
            "sections_unavailable": material_pack.get("sections_unavailable"),
            # 全量读数在 `material_pack.json`；报告里只放指针与计数，避免同一份原文在
            # 一个产物里出现两次（两份副本迟早会对不上）。
            "note": material_pack.get("note"),
        },
        # §二（b）来源归属轴（`srattr-1`）：读者凭什么知道「这是哪一份材料、哪个版本、哪一页
        # 披露的」。它与 `source_manifest` 那一块是两件事：那块答「登记了哪些材料、选了哪些」，
        # 这块答「正文里这一句归到哪一份材料的哪一页、那份材料披露日可不可核实」。逐句明细在
        # `source_attribution.json` / `.md`；报告侧只放指针与计数，不重复正文。
        "source_attribution": {
            "schema_version": SOURCE_ATTRIBUTION_ARTIFACT_SCHEMA_VERSION,
            # `readback` / `partial` / `build_failed`。**读不回来不许写成「空」**。
            "status": source_attribution.get("status", "readback"),
            "attribution_version": source_attribution["attribution_version"],
            "artifact": "source_attribution.json",
            "human_readable_artifact": "source_attribution.md",
            "rendered_attributions": len(source_attribution["rendered_attributions"]),
            "materials_with_attribution": len(source_attribution["materials"]),
            "sentences_total": len(source_attribution["sentences"]),
            "sentences_multi_version_without_period": sum(
                1 for s in source_attribution["sentences"]
                if s["multi_version_without_period"]),
            "sections_readback": list(source_attribution.get("sections_readback") or []),
            "sections_unavailable": list(source_attribution.get("sections_unavailable") or []),
            "skipped_by_reason": {
                code: sum(1 for s in source_attribution["skipped_materials"]
                          if s["reason_code"] == code)
                for code in SOURCE_ATTRIBUTION_SKIP_REASONS},
            "note": source_attribution["note"],
        },
        # 指令 D §三：失败侧的只读门前诊断。**逐字标注「未核验、不可发布」**，并与报告里
        # 已有的「本节有没有 `SectionDraft`」分开读：这一块说的是「没成形的那一节，门前当时
        # 写出了什么」，不是「这一节有内容」。
        "pre_gate_draft": {
            "schema_version": PRE_GATE_DRAFT_ARTIFACT_SCHEMA_VERSION,
            "status": pre_gate_draft.get("status", "readback"),
            "label": pre_gate_draft["label"],
            "retention_version": pre_gate_draft["retention_version"],
            "artifact": "pre_gate_draft.json",
            "human_readable_artifact": "pre_gate_draft.md",
            "by_section": {s["section_id"]: s["status"] for s in pre_gate_draft["sections"]},
            "prose_unit_total": pre_gate_draft["prose_unit_total"],
            "note": pre_gate_draft["note"],
        },
        # §三 3（acc-28）：**本轮对外部来源做了什么**。与报告顶部那条诚实性说明读**同一次**
        # 计算（`external_retrieval`），因此不存在两个措辞版本的「本轮未检索」。
        "external_retrieval": external_retrieval,
        "financial": {"snapshot": state.inputs.dims,
                      "artifact_id": str(getattr(
                          state.inputs.financial_phase.artifact, "artifact_id", "")),
                      "facts": len(getattr(
                          state.inputs.financial_phase.artifact, "facts", ()) or ()),
                      "note_state": ("facts" if state.inputs.financial_phase.evidence_note_facts
                                     else "gap")},
        "sections": {s: {"title": state.inputs.tasks[s].title,
                         "topic_ids": list(state.inputs.tasks[s].topic_ids)}
                     for s in SECTION_ORDER},
        "section_errors": dict(state.section_errors),
        # §二 3：被整束拒绝的候选提案集（完整有序身份 + typed 原因 + 指向 logs/llm 的指针）。
        # 迁移期读法：`max_llm_retries` 从 0 变 1 之后，本节**可以**先被拒一束再被采信一束；
        # 这个块区分「本节被拒过几次」与「本节最终有没有内容」——两者不是同一件事。
        "proposal_set_rejections": {
            "schema_version": PROPOSAL_SET_REJECTIONS_SCHEMA_VERSION,
            "artifact": "proposal_set_rejections.json",
            "total_rejected_bundles": _psr_total(state),
            "by_section": {s: len(state.proposal_set_rejections.get(s, ()))
                           for s in SECTION_ORDER},
            "sections_failed": sorted(state.section_errors),
            # 3.4：报告侧只放**判读窗口**（账目 + 词表），逐条明细留在产物里。
            "candidate_reason_vocabulary": list(PW.PROPOSAL_SET_REJECTION_CANDIDATE_REASONS),
            "candidate_audit_total": _candidate_audit_total(state),
            # C3：定向重提案——**排定**与**真的产出下一修订**是两个数，报告侧两个都放。
            # 词表与上限一并落盘，复核者不必回头读 `pack_writer.py` 才能判读去向码。
            "directed_reproposal": {
                **_directed_reproposal_total(state),
                "note_version": PW.HIGH_RISK_REPROPOSAL_NOTE_VERSION,
                "max_passes": PW.MAX_DIRECTED_REPROPOSAL_PASSES,
                "trigger_kinds": list(PW.REPROPOSAL_TRIGGER_KINDS),
                "destinations": list(PW.CANDIDATE_REPROPOSAL_DESTINATIONS),
            },
            "note": ("逐条记录**整束**被拒的候选提案集；被拒束不得被裁剪后冒充原集合，"
                     "也不得被当成 gap（缺口另有 Contract 依据与检索范围）。"
                     "每条另有 `candidate_audit`（逐候选 typed 原因）：它解释**整束**为什么被拒，"
                     "恒带 `whole_set_rejected=true`，**不得**被读成「哪几条可以留用」。"
                     "**一次「尝试」不等于一次调用**：§二 的确定性分批让一轮写作 = 一次分批扫描，"
                     "因此 `narration_call_id` 只是**代表**调用，而「这一轮问过哪几批、哪一批被"
                     "provider 截断、缩小到第几层」逐条读该记录的 `batches`（有序）。"
                     "`narration_call_id` 指向 logs/llm 下的完整调用记录，本产物不复制原文。"
                     "**C3（定向重提案）**：原候选集里被路径 B 的高风险硬事实牵连时，"
                     "本策略**不**在原料里删几条充数，而是把材料按同一份参考基线重新提出的权利"
                     "**定向**交给已有的那一轮写作重试（不新增调用额度），要求模型给出**完整**"
                     "有序候选集并逐条交代：原束每条候选在下一修订里是保留还是未再表达。"
                     "因此被拒记录带 `answered_by_attempt`（排定由第几次生成回答）与 `reproposal`"
                     "（跨修订逐候选去向）；**`reproposal=null` 一律读作「没有可写的跨修订事实」"
                     "（没排定，或排定了但那一轮未成形），**不得**读作「都还在」；"
                     "`destinations` 的键集恒等于该束 `candidate_ids`（完整、有序、不裁剪）。"
                     "**C4（批次形状纠正）**：一批的返回形状不合法时，只对**那一批**按 "
                     "`batch_shape_correction_note_version` 附注重问**一次**（同一批不再纠正"
                     "第二次，再次失败即落成 typed 拒绝），其余批次的请求与返回不动；"
                     "这份额度与截断缩小**共用**（`batch_shape_correction_slack_pool`，"
                     "不抬高任何上限），因此「被重问过一次的批次」不得被读成「这一束被拒了」。"),
        },
        # §二 2.6：相位**无法裁决**的 FollowUpNeed（逐节，待裁决提议）。
        # 这个块与上面的 `proposal_set_rejections` 是两件事：上面说「候选被拒了」，
        # 这里说「模型提过补件诉求、但没人裁决它」。二者都不得被读成 gap，也都不许丢。
        "follow_up_needs": {
            "schema_version": FOLLOW_UP_NEEDS_SCHEMA_VERSION,
            "artifact": "follow_up_needs.json",
            "status": "pending_adjudication",
            "total_pending_needs": _pending_needs_total(state),
            "total_untypeable": sum(len(v.get("follow_up_untypeable", ()))
                                    for v in state.pending_follow_up_needs.values()),
            "by_section": {s: len(state.pending_follow_up_needs[s].get("follow_up_needs", ()))
                           for s in SECTION_ORDER if state.pending_follow_up_needs.get(s)},
            "sections_with_pending": sorted(state.pending_follow_up_needs),
            # §六：**已写出内容**的节里，被采信那一轮中自己不成立的申请（逐条带原因码）。
            # 它与 `total_pending_needs` 是两种场景，不得相加、也不得互相顶替。
            "total_rejected_applications": sum(
                len(rows) for rows in state.follow_up_rejections.values()),
            "by_section_rejected": {s: len(rows) for s, rows in
                                    state.follow_up_rejections.items() if rows},
            "adjudicated_runs": {s: len(tuple(runs))
                                 for s, runs in state.follow_up_runs.items() if runs},
            "note": ("**待裁决、未执行**：是否补件由 Harness 按 Contract、预算与 SourcePolicy 决定，"
                     "本组合根只负责留存，不得读成「已经补过件」。它们不是 gap（缺口另有 Contract "
                     "依据与检索范围）。已执行的补件是另一套身份（FollowUpExecutionResult），"
                     "记在 phase 的 follow-up runs 里，不在本块计数内。"
                     "`total_rejected_applications` 是另一侧：那些节**写出了内容**，只是模型提的"
                     "某几条申请自己不成立（四个 id 未逐字取自同一行）——不是缺口，也不阻断本节。"),
        },
        "report": ({} if state.report is None else {
            "report_id": state.report.report_id,
            "report_version": state.report.report_version,
            "scope_coverage": len(state.report.scope_coverage),
            "gap_index": len(state.report.gap_index),
            "markdown_chars": len(state.report.markdown)}),
        # P5：**失败也可审计**。这份只读诊断逐文档、逐 aspect 摊开「谁被选用、材料在哪一层掉了、
        # 哪些可写事实因为期间/归属被拒、交给 Writer 的精确清单是什么」，无 `SectionDraft` 的节
        # 逐 aspect 写「本 run 未能读回验收」——**不**写成「已证明没有材料」。
        "failure_diagnostics": {
            "schema_version": FAILURE_DIAGNOSTICS_SCHEMA_VERSION,
            "artifact": "failure_diagnostics.json",
            "not_read_back_label": NOT_READ_BACK_THIS_RUN,
            "sections_without_draft": sorted(
                s for s in SECTION_ORDER
                if getattr(state.sections.get(s), "draft", None) is None),
            "sections_read_back_ok": sorted(
                s for s in SECTION_ORDER
                if getattr(state.sections.get(s), "draft", None) is not None),
            "note": ("只读诊断，不参与判据、不写库、不冒充 Pack/SectionDraft/SectionResult。"
                     "`not_used` 不是 gap；没有 `SectionDraft` 的节只是**本 run 未能读回验收**，"
                     "不等于语料里没有可写事实。"),
        },
        "assembly_error": state.assembly_error,
        "disclaimer": ("本目录是 M930-3 的**验收产物**，不是完整报告，也不构成任何发布结论。"
                       "缺口按权威状态原样显示；离线模式的生成器是确定性替身。"),
    }
    _write_json(state.run_dir / "manifest.json", manifest)

    _sm = state.inputs.source_manifest
    _src_set = [s for s in _sm.selection if s.source_role != "not_used"]
    _unsel = [s for s in _sm.selection if s.source_role == "not_used"]
    _role_counts: dict[str, int] = {}
    for _s in _sm.selection:
        _role_counts[_s.source_role] = _role_counts.get(_s.source_role, 0) + 1
    honesty = [
        f"来源清单（`{_sm.policy_version}`）登记了本轮**全部** {len(_sm.entries)} 份已登记材料，"
        f"其中 {len(_src_set)} 份进入**有序源集**（逐份 source_role: "
        f"{ {k: _role_counts[k] for k in sorted(_role_counts)} }），"
        f"另有 {len(_unsel)} 份 `not_used`，逐份带 reason_code 留在 "
        "`source_manifest.json`（登记 ≠ 同权用于当前事实）。"
        f"当前锚 `{_sm.primary_document_id}`（`{_sm.current_state}`）只是**旧单文档接口的兼容"
        "读视图**，不决定各材料事实的期间或权威；同系列的旧期间成员与其他系列成员**同样是"
        "源集成员、同等资格按主题检索**，不是「未被选中」。"
        + (f"注册类型与内容识别不一致的份数：{len(_sm.registration_class_mismatches)}"
           "（typed 审计 `RegistrationContentClassMismatch`，随来源清单进产物；"
           "它**不是** gap，也不影响同类判定）"
           if _sm.registration_class_mismatches else ""),
        "披露/发行日期一律取自文档正文封面；本库 `documents` 没有披露日期列、"
        "`evidence_blocks.published_at` 全为空——因此清单里没有一份材料带可核实到日的"
        "披露日期（月粒度封面日期只作另行标注的线索，不升格、不回填，入库时间另列）。",
        "注册类型与有证据支持的类型判断**分列**：注册值原样报告，差异按 SourcePolicy 的实际"
        "后果（检索加权 / 索引优先级 / 类型标签）写明，不暗改历史 DB、不静默重分类。",
        # §二 3：被拒的候选提案集**不因被拒而消失**。这条必须在报告顶部可见：否则「本节有内容」
        # 会被读成「一次就写对了」，而「本节没内容」会被读成「没有材料」——两个都是错的。
        _psr_honesty(state),
        # §二 2.6：与上一条同理——「模型提过补件诉求」不得被读成「补件已经做过」。
        _follow_up_needs_honesty(state),
        # P5：本轮的失败/缺口面在哪一份产物里逐层可查；以及「没有产出」与「没有材料」的区分。
        (f"逐层诊断在 `failure_diagnostics.json`（`{FAILURE_DIAGNOSTICS_SCHEMA_VERSION}`）："
         f"逐文档选用/未选用、逐 aspect 的原始材料→Pack 材料→可写事实与期间/归属拒绝、"
         f"交给 Writer 的精确清单、被拒提案与待裁决补件。本次未产出 `SectionDraft` 的节："
         f"{sorted(s for s in SECTION_ORDER if getattr(state.sections.get(s), 'draft', None) is None) or '无'}"
         f"；这些节逐 aspect 的状态是「{NOT_READ_BACK_THIS_RUN}」——它**不等于**"
         "「已证明本节没有材料」，也不得据此认为本节不需要补件。"),
        # T1：材料包读回在哪、读的是什么、以及它**不**证明什么。
        (f"**取到的原文**在 `material_pack.md`（人读）/ `material_pack.json`"
         f"（`{MATERIAL_PACK_SCHEMA_VERSION}`）里逐条摊开：共 {material_pack.get('entry_count')} 条，"
         f"逐条给完整原文、文档四轴身份、页/span locator、topic/aspect 归属，以及 Pack 侧的"
         f"处置去向与 typed 理由。读自**权威 Pack 本体**（不是 Writer prompt 的倒抄，"
         f"也不止 ID 与字数）；可读回的节 {material_pack.get('sections_readback') or '无'}，"
         f"不可读回的节 {material_pack.get('sections_unavailable') or '无'}"
         f"（后者是**读不回来**，不是「没有材料」）。"
         "**「取到了原文」不等于「进了正文」**：去向列说的是研究侧处置，进没进正文另见 "
         "`section_drafts.json` 与 `cross_document_evidence.md`。"),
        # §三 3（acc-30）：外部来源要求**已经到达链路**，但本轮**未授权执行**。三侧各自可读：
        # 声明侧（冻结 Contract 说某些 aspect 只能取外部来源）、需要侧（生产构造器把这些要求
        # 真的搬进了正式 `InformationNeed`）、尝试侧（本轮有没有发过外部请求）。写的是
        # 「本轮未检索」，不是「已证明没有外部来源」，也不是「行业没有可写内容」。
        (f"**外部来源要求已到达链路，本轮未检索**（`external-retrieval/2`，见报告 "
         f"`external_retrieval` 块）：冻结 Contract 声明来源类为 `external` 的 aspect 有 "
         f"{len(external_retrieval['aspects_declaring_external'])} 个"
         f"（{external_retrieval['aspects_declaring_external'] or '无'}），"
         f"其中 {len(external_retrieval['aspects_carrying_external_in_need'])} 个的要求"
         f"**已经到达正式 `InformationNeed`**（需要侧逐条回读，两侧一致："
         f"{external_retrieval['need_side']['declared_and_need_sides_agree']}），"
         f"本轮 `external_research_enabled="
         f"{external_retrieval['external_research_enabled']}`，"
         f"注入的 need 构造器是 `{external_retrieval['information_need_builder']}`，"
         f"Pack 侧外部漏斗痕迹：{external_retrieval['attempt_side']}，"
         f"**终态 `{external_retrieval['terminal_state']}`**。"
         "**原因各自独立、不得合并**：本轮开关为关 ⇒ 三个外部动作"
         "（`SEARCH_EXTERNAL` / `FETCH_EXTERNAL` / `SNAPSHOT_EXTERNAL`）在**执行前**被 "
         "`harness.policies.external_research_unauthorized` 拒掉并留痕（与本次授权范围一致："
         "更窄，不是放宽）；`acc-29` 及之前那条「need 构造器丢弃冻结 `source_classes`」的链路"
         "断点**已消除**（原因码 "
         f"{external_retrieval['not_connected_reason_codes']}）。"
         "因此本轮写的是「**本轮未检索**」，**不是**「已证明没有外部来源」；发行人对第三方数字的"
         "转述（年报正文里的 SNE / IEA / SMM 等）是**发行人转录**，不得据此读成已验证的外部事实。"),
        "机器门只证明链路贯通与守恒；「人读内容通过」由 manual_review.md 的人工填写承担。",
        "离线模式使用确定性生成器（文本逐字取自真实材料/权威事实），不构成真实模型产出质量。",
        "本 runner 不写任何真实库、不改任何信任根；A6 的 before/after 哈希是这条的字节级证据。",
    ]
    # 调用计量必须写在报告顶部：旧口径把「已批准的写作预算」当成全链总预算，并且各节计量漏掉
    # 门后组织那一次 —— 两个数字并列时读者无从判断哪个是「真的发出去几次」。
    _recon = (llm_calls or {}).get("reconciliation") or {}
    if _recon:
        _absent = _recon.get("meter_absent_sections") or {}
        _cons = _recon.get("conservation") or {}
        honesty.append(
            f"调用计量（两条预算轴各自守恒，A7 判据）：账本 {_recon.get('ledger_total')} 次尝试 / "
            f"按类别 {_recon.get('ledger_by_category')} / 按状态 {_recon.get('ledger_by_status')} / "
            f"各节计量之和 {_recon.get('meter_sum')}"
            f"（逐节 {_recon.get('meter_by_section')}）；门后组织 {_recon.get('organizer_attempts')} 次。"
            "旧口径的各节计量**不含门后组织**，因此系统性少算，差额恰是门后组织的**尝试次数**"
            "（不是『节数』：一个节可以走两轮组织）——"
            f"这就是历史的『各节之和 + {_recon.get('meter_omitted_before_fix')} = 实际次数』的来源；"
            "本批把组织那一次计回各节。"
            "**逐项相等只对『有产出的节』成立**：第三个视图就是该节的 "
            "`BackboneSectionWriterOutput.llm_calls`，本节抛错时它**不存在**，"
            "于是这些调用按**显式桶**进守恒式："
            f"`{_cons.get('equation')}` = {_cons.get('writing_axis_total')} == "
            f"{_cons.get('meter_sum')} + {_cons.get('attempts_unmetered')} + "
            f"{_cons.get('attempts_unattributed')}（残差 {_cons.get('residual')}，必须为 0）；"
            f"无产出节 {sorted(_absent) or '无'}"
            + (f"，逐节读数 {_absent}" if _absent else "")
            + "。**『无产出』不得被读成『没发过请求』**：那些请求已经发出、已经占了预算，"
            "只是没有落成第三个视图。"
            "**上面三个视图都是写作轴的**：研究调用另有**第四个视图**，按 `topic:<id>` 逐 topic "
            f"归集（本批 {(_recon.get('research') or {}).get('axis_total', 0)} 次尝试，"
            f"逐 topic {(_recon.get('research') or {}).get('topic_totals')}，"
            f"上限逐 topic {(_recon.get('research') or {}).get('per_topic_cap_by_topic')}、"
            f"{(_recon.get('research') or {}).get('axis_max_attempts')} 整轴）；"
            "两轴恒等式是 `" + str((_recon.get('axis_identity') or {}).get('equation')) + "`"
            f"（残差 {(_recon.get('axis_identity') or {}).get('residual')}，必须为 0）。"
            "**`narration` 一类的上限不是全链总预算，研究轴的上限也不是**：逐候选蕴含与可选"
            "章级评估是另外的量，研究调用走**另一条轴**、**不消耗**写作轴的整轮 "
            "`total_max_attempts`。两类上限的**当前数字**一律以那份政策为准"
            "（不在这里抄一遍：抄一遍就会在下次重估时变成第二个真值）。")
        if _recon.get("problems"):
            honesty.append(f"调用计量对账**不成立**：{_recon['problems']}")
    failing = [g.gate_id for g in gates if g.status != "pass"]
    failing_sub = [f"{g.gate_id}/{s['sub_gate_id']}" for g in gates for s in g.sub_gates
                   if s.get("status") != "pass"]
    if failing:
        # 红门必须**在报告顶部**可见地解释，避免被读成环境失败或机制失败。区分留给人工复核，
        # 但「红在哪、为什么红」不能只藏在 gates[].detail 里。
        honesty.append(f"本轮红门：{failing}。红门原因见对应 `detail` 与 `evidence`；"
                       "在人工复核前，本 run 不得当作任何通过结论。")
    if failing_sub:
        honesty.append(f"本轮红子门：{failing_sub}（子门与门级结论同源：任一子门红 ⇒ 门红）。")
    identity_audit = _follow_up_identity_audit(state)
    source_reconciliation = _a1_source_reconciliation(state)
    if source_reconciliation.get("status") == "read_only_diagnostic":
        lost = source_reconciliation["lost_at"]
        honesty.append(
            f"A1 源头对账（只读诊断，读数全部来自**真轨迹事件 + Pack 自己的 gap 记录**）："
            f"公司节真树检视调用 {lost['tree_inspect_calls']} 次 / 有界 Evidence 重切 "
            f"{lost['evidence_recut_calls']} 次；树检视真召回到候选 span "
            f"{lost['recalled_candidates']} 条（覆盖父 Evidence "
            f"{lost['candidate_evidence_total']} 个）/ 重切出材料 "
            f"{lost['recut_material_total']} 份；重切侧自报 gap {lost['tool_gaps_total']} 条、"
            f"受限 {lost['tool_skipped_total']} 条；focused follow-up 真跑了 "
            f"{lost['follow_up_attempts']} 轮（其中因**没有新证据**而收束 "
            f"{lost['follow_up_no_new_evidence']} 轮）；上界原样保留：max_spans_per_aspect "
            f"{lost['bounds_used']['max_spans_per_aspect']}、max_chars_per_span "
            f"{lost['bounds_used']['max_chars_per_span']}、每 aspect 最多 "
            f"{lost['bounds_used']['max_need_rounds_per_aspect']} 轮；"
            f"Pack 侧逐条 gap 的原因分布 {lost['pack_gap_reason_histogram']}、"
            f"其中 span 级现场原因 {lost['pack_gap_layer_reason_histogram']}；"
            + "；".join([
                _count_clause("Writer 精确材料清单成员",
                              (source_reconciliation["writer_manifest"]
                               if lost["writer_side_status"] == "read_back_ok" else None)),
                _count_clause("切不出句子的成员", lost["writer_members_without_sentence"]),
                _count_clause("可授权原子为 0 的成员", lost["writer_members_zero_eligible"]),
                _count_clause("Pack 里有、清单里没有的材料",
                              lost["pack_materials_missing_from_writer_manifest"]),
                f"无任何材料的 aspect {lost['aspects_without_any_material_count']} 个"])
            + "。"
            f"研究侧 LLM = {source_reconciliation['research_llm']['kind']}"
            f"（{source_reconciliation['research_llm']['class']}）。七层清单见 "
            "observations.company_material_source_reconciliation。"
            "**「被丢弃 span 里是否已含合法描述性原子」这一项本轮无从核验**（旧值来自已删除的 "
            "研究替身私存正文），故留空并在 `lost_at` 里写明原因——它**不得**被读成「没有」。")
        # 逐 topic 并列原始计数：对账要能看出「是哪一个主题在召回/保留/Pack 的哪一层掉光」，
        # 而不是只有一个公司节的总数。这里只并列，不替研究运行时断言 span→材料的派生关系。
        honesty.append("A1 源头对账逐主题（真树检视/重切/候选 span/覆盖父 Evidence/重切材料/"
                       "follow-up 轮数/Pack 材料/Pack 事实/Pack 缺口）：" + "；".join(
                           f"{row['topic_id']}={row['tree_inspect_calls']}/"
                           f"{row['evidence_recut_calls']}/{row['recalled_candidates']}/"
                           f"{row['candidate_evidence_ids']}/{row['recut_materials']}/"
                           f"{row['follow_up_attempts']}/"
                           f"{row['pack_materials']}/{row['pack_facts']}/{row['pack_gaps']}"
                           for row in lost["per_topic"]) + "。")
        # A1 为红时的**归因**必须写在报告顶部：否则「链上 claim_candidates 实际 0」会被读成
        # 「链路断了」或「语料没有可写事实」。两种误读都会导致错误的修法（放宽 A1、或改召回
        # 预算）。真实原因写在下面，且**不得**用路径 A 硬事实或机械拼句顶替。
        company_section = state.sections.get("company")
        company_draft = getattr(company_section, "draft", None)
        if ("A1" in failing and company_section is not None and company_draft is not None
                and not company_draft.claim_candidates):
            # 本条只讲**真的产出了** `SectionDraft`、但链上 `claim_candidates=0` 那种 A1 红。
            # 「有节记录、无 `SectionDraft`」是**另一件事**：那段文字断言「材料全部可读、无一被
            # 丢在 Pack→Writer 之间」，而写作侧根本没读回来时没有任何读数支得住这句话。旧代码
            # 在这里直接取 `company_section.draft.claim_candidates`，`draft` 为 `None` 时是
            # `AttributeError`——与 A1 那处 `len(None)` 同属「失败路径上报告自己先炸掉」。
            # 今天生产链**不会**产出这个形状（`_drive_into` 只在相位成功时写 `sections[id]`，
            # 因此真实失败 run 走的是下面那一支的 `company_section is None`，连这段都不进），
            # 但 `sections` 是普通 dict，判据侧三处（组装 / 读回 / 补件审计）都默认它的每个值都有
            # `draft`；把这段写成按有无 `SectionDraft` 分流，是为了让将来任何一处提前写入半个
            # 记录时，报告**照实说「这一层没有读数」而不是先崩**。
            writer_members = lost["writer_members_without_sentence"]
            honesty.append(
                "A1 红的落点（如实归因）：公司节链上 claim_candidates=0 ⇒ 上游 proposal / "
                "aggregate / entailment / accepted binding / Claim / 段落必然全为 0；链路本身"
                "走完了（A4 整本读回重组通过、A5 组装通过、A6 信任根零改动），**不是机制失败**。"
                f"可达的 {len(source_reconciliation['writer_manifest'])} 个精确材料成员全部可读、"
                "无一被丢在 Pack→Writer 之间，但其中没有任何一条**可授权的路径 B 描述性原子**："
                f"{_count_clause('切不出句子的成员', writer_members)}（勾选行 / 表头"
                "标题残片），其余句子全部踩在策略条件上（含数字 ⇒ 只能走路径 A、未绑定期间措辞 ⇒ "
                "期间门、高风险表面 ⇒ 路径 B 不授权），**0 条**只被替身自己的选材条件挡下。"
                "因此「扩大采信面」「放宽预算」都不会带来合法材料（未被采信的候选里同样 0 条含"
                "合法原子的实测见上）。要 A1 转绿，只能由真实模型在真实语料上提出合法的路径 B "
                "候选，或由研究侧取回确实可授权的材料；**不得**放宽 A1 判据，也不得用路径 A 硬"
                "事实或机械拼句代替。")
        elif "A1" in failing and company_section is not None and company_draft is None:
            # 写作相位根本没产出 `SectionDraft`：**不得**沿用上面那段「材料全部可读」的话——
            # 第七层（Writer 精确材料清单）在这一轮是不可读回的，说「无一被丢在 Pack→Writer
            # 之间」是拿一个没有读数的层去充当证据。
            honesty.append(
                "A1 红的落点（如实归因）：公司节**没有产出 `SectionDraft`**（写作相位失败，"
                f"本节错误见 `section_errors.company`），链上的 claim_candidates 不是「算出来为 0」"
                "而是**这一层没有读数**。因此本 run **不得**用 A1 红去断言「材料里没有可写事实」："
                "第七层（Writer 精确材料清单 / 逐成员切句 / 可授权原子）在本轮不可读回"
                f"（{[lost['writer_side_status']]}）。可读回的是前六层——研究真的读过哪些 span、"
                "导航真的开了哪些读集、Pack 真的收了哪些材料（逐层读数见下）；**写作为什么失败"
                "要看 `section_errors` 与 `proposal_set_rejections.json`，不要读成「材料里没有"
                "可写的东西」**。")
    else:
        # 「这一环**未执行**」必须写在报告顶部，不能被静默略过：源头对账是「材料在哪一层掉的」
        # 唯一读回面，它缺失时读回的人会默认「没有丢料」。因此失败原因原样摆出来，并明确它
        # **不等于**「材料到达为零」——缺读回面 ≠ 零命中的实测结果。`observations` 块里同时
        # 保留该块自己的 `status` 与错误串，两处指向同一件事。
        honesty.append(
            "A1 源头对账**未产出**（只读诊断这一环**未执行**）："
            f"{source_reconciliation.get('detail') or source_reconciliation.get('error') or '原因未记'}"
            "。因此本 run 的「原文 → 读集 → ResearchMaterial → Pack → Writer 精确材料清单」"
            "逐层读数**不可得**，不得据此写成任何命中数、也不得读成「材料到达为零」；"
            "`observations.company_material_source_reconciliation` 里记的是它自己的失败读数。")
    if identity_audit["run_count"]:
        # 这条必须写在报告顶部而不只藏在 A5 的 evidence 里：复核者要能一眼看出「本轮真的走过
        # 几次有界补件、每次的 run / need / 后继 Pack 各是什么」，否则「补件」会被读成黑箱。
        honesty.append(
            f"本轮实际执行了 {identity_audit['run_count']} 次有界补件："
            + "；".join(f"{r['section_id']} run={r['follow_up_run_id']} "
                        f"needs={r['need_ids']} → 后继 Pack {r['new_pack_ids']}"
                        for r in identity_audit["runs"])
            + "。每次执行的 trace ref 都指向它自己的 run id（两次执行不得折叠）；"
              "补件运行属操作身份，**不得**改写冻结策略依赖指纹，后继 Pack 的身份进内容/版本"
              "身份。逐条证据见 A5.evidence.follow_up_identity。")
    report = {
        "schema_version": ACCEPTANCE_REPORT_SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
        "milestone": "M930-3",
        "status": status,
        "mode": state.mode,
        "generated_at": state.generated_at,
        "run_dir": state.run_dir.name,
        "gates": [g.to_dict() for g in gates],
        "gate_summary": {g.gate_id: g.status for g in gates},
        "section_errors": dict(state.section_errors),
        "assembly_error": state.assembly_error,
        "trust_roots_before": before,
        "trust_roots_after": after,
        "llm_calls": llm_calls,
        # 观察项（**不是判据**）：多 Claim 自然句的机器判据在 A1 且限定公司节；这里按节摊开实得
        # 证据，供人工复核区分「机制能力」与「该节语料只够一条」。它不参与 status 的推导。
        "observations": {"composed_sentences_by_section": _composed_sentence_observation(state),
                         # 本次 run 用的**是哪一份** demo scope profile、节序由它派生成什么。
                         # 这是观察项不是判据：它只回答「这份报告是按三节还是两节跑的」，
                         # 旧三节 run 与双节 run 因此可以被逐字区分，而不是靠目录名去猜。
                         "demo_scope_profile": {
                             "path": active_profile_path(),
                             "profile_id": _active_profile_id(),
                             "section_order": list(SECTION_ORDER)},
                         # T1：材料包读回的**指针**（全量读数在 `material_pack.json`，人读版在
                         # `material_pack.md`）。这里只记状态与计数，不复制原文——同一份原文在一个
                         # 产物里出现两次，两份副本迟早会对不上。
                         "material_pack": {
                             "schema_version": MATERIAL_PACK_SCHEMA_VERSION,
                             "artifact": "material_pack.json",
                             "human_readable_artifact": "material_pack.md",
                             "status": material_pack.get("status"),
                             "entry_count": material_pack.get("entry_count"),
                             "sections_readback": material_pack.get("sections_readback"),
                             "sections_unavailable": material_pack.get("sections_unavailable")},
                         # §四 1/§六：有界补件是否真的执行、每次执行的 run / need / 后继 Pack、
                         # 旧 Pack 是否原样保留、依赖指纹是否保持冻结。判据在 A5；这条只把
                         # 「补件到底发生了什么」摊成可复核的集合。
                         "follow_up_identity": identity_audit,
                         # A1 的**源头对账**（只读诊断）：Contract 需求 → 标题树召回 → 实际
                         # span/表 → 读取轨迹 → ResearchMaterial → Pack manifest → Writer 精确
                         # 材料清单。它回答「材料在哪一层没有被采用」；不改变任何判据。
                         "company_material_source_reconciliation": source_reconciliation},
        "honesty": honesty,
    }
    _write_json(state.run_dir / "acceptance_report.json", report)
    # 本批新增的**第三个**出口（受阻章节的不可发布预览）。放在报告**之后**两处都是故意的：
    # 它读 `acceptance_report.json` 里的 `assembly_error` / `report_version` / 节标题，
    # 而这正是 `report_preview.md` 写不出来时才需要回答的那一问。它**不**改任何门——
    # 上面那条 `if state.report is not None` 一字未动，报告该被拒仍然被拒。
    _write_blocked_section_preview(state)

    index = []
    for name in ARTIFACTS:
        path = state.run_dir / name
        if not path.exists():
            continue
        body = path.read_bytes()
        index.append({"artifact": name, "bytes": len(body),
                      "sha256": _sha256_bytes(body)})
    _write_json(state.run_dir / "artifact_index.json",
                {"output_dir": state.run_dir.name, "artifacts": index})
    missing = [n for n in ARTIFACTS if not (state.run_dir / n).exists()]
    return {"report": report, "missing": missing}


def _write_run_assembly_fallback(state: RunState, *, before: dict, after: dict,
                                 gates: list[GateOutcome], llm_calls: dict,
                                 status: str, error: str) -> dict:
    """`_write_run` 在**报告装配**阶段抛错时的兜底产物：报告与 artifact index 一定要写出来。

    为什么必须有这一层：装配算的是**报告正文**，它不是主链的任何一环。它抛错时（真实 run r4
    实测：A1 诚实性说明对 Writer 侧 `None` 计数取 `len()`），`manual_review.md` 已经写好，而
    `acceptance_report.json` 与 `artifact_index.json` **一个字都没有**——最需要「这一轮怎么了」
    的现场反而没有报告，读者只能从一堆半成品里倒推。

    兜底报告**不冒充完整报告**，三点必须一起做到：

      * `status="assembly_error"`：既不是 `pass`、也不是 `fail`、也不是 `refused`——装配失败
        不等于任何一条门的结论，用一个新值才不会把「报告自己没装起来」读成「门没过」；
      * `assembly_error` 逐字写出异常，`honesty[0]` 说明这是**降级**报告、判据以
        `failure_diagnostics.json` / 各门 evidence 为准；
      * 已经算出来的门结论**原样列出**：它们是已经发生的事实，不因报告装配失败而作废。

    调用方在本函数之后**重抛**原异常：兜底是「现场一定要留下」，不是「把代码缺陷咽下去」。
    """
    _write_json(state.run_dir / "llm_call_ledger.json",
                _ledger_payload(state, mode=state.mode, generated_at=state.generated_at,
                                run_dir=state.run_dir, status="assembly_error",
                                accounting=llm_calls))
    present = sorted(name for name in ARTIFACTS if (state.run_dir / name).exists())
    report = {
        "schema_version": ACCEPTANCE_REPORT_SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
        "milestone": "M930-3",
        "status": "assembly_error",
        "degraded_report": True,
        "assembly_error": error,
        "gates_were_computed_as": status,
        "mode": state.mode,
        "generated_at": state.generated_at,
        "run_dir": state.run_dir.name,
        "gates": [g.to_dict() for g in gates],
        "gate_summary": {g.gate_id: g.status for g in gates},
        "section_errors": dict(state.section_errors),
        "trust_roots_before": before,
        "trust_roots_after": after,
        "llm_calls": llm_calls,
        "observations": {},
        "artifacts_present_before_index": present,
        "honesty": [
            "**这是降级报告**：完整报告的**装配阶段**抛了异常，因此这份"
            f"`acceptance_report.json` 不是完整报告。异常：{error}。",
            f"各门的结论在抛错**之前**已经算完（按完整路径应当写出的顶层状态是 {status!r}），"
            "下面 `gates` / `gate_summary` / `section_errors` 是那些结论的原样读出；"
            "本文件缺失的观察项（A1 源头对账、材料包指针、补件身份等）**不得**被读成空值或"
            "「确实为零」——它们是**没装进来**。",
            "判据不因这份降级报告而改变：`status` 是 `assembly_error`，既不是 `pass`，"
            "也不是 `fail`/`refused`。要还原现场请看 `failure_diagnostics.json`、"
            "`manual_review.md` 与各门 evidence（若已写出）。",
        ],
        "problems": [f"报告装配失败：{error}"],
    }
    _write_json(state.run_dir / "acceptance_report.json", report)
    # 降级路径同样要写受阻预览：装配抛错时最需要「这一轮到底有没有内容可读」的那一问，
    # 恰恰比正常路径更需要答案。它只读现场，自己失败也不抛（见 `_blocked_section_preview_payload`）。
    _write_blocked_section_preview(state)
    index = []
    for name in ARTIFACTS:
        path = state.run_dir / name
        if not path.exists():
            continue
        body = path.read_bytes()
        index.append({"artifact": name, "bytes": len(body),
                      "sha256": _sha256_bytes(body)})
    _write_json(state.run_dir / "artifact_index.json",
                {"output_dir": state.run_dir.name, "artifacts": index,
                 "degraded_report": True})
    missing = [n for n in ARTIFACTS if not (state.run_dir / n).exists()]
    return {"report": report, "missing": missing}


def _ledger_payload(state: RunState | None, *, mode: str, generated_at: str,
                    run_dir: Path, status: str, accounting: dict | None = None) -> dict:
    """调用账本的机器产物（`llm_call_ledger.json`）：账本 + 政策 + 对账，一处可复核。

    它不是第二个计数来源——是本轮唯一账本（`llm.budget.LLMCallBudget`）的序列化。拒绝路径也要
    写它：**被拒的那一次尝试**（`refusals[]`，含「若发出会是第几次」）与「到拒绝为止已经记了
    几次」必须留下现场，否则「事前拦截」在产物里就只剩一句文字。
    """
    ledger = None if state is None else state.budget
    return {
        "schema_version": "m930-3-llm-call-ledger-1",
        "milestone": "M930-3",
        "runner_version": RUNNER_VERSION,
        "mode": mode,
        "generated_at": generated_at,
        "run_dir": run_dir.name,
        "status": status,
        "budget_policy": _call_budget_policy().to_dict(),
        "enforce": None if ledger is None else bool(ledger.enforce),
        "ledger": None if ledger is None else ledger.summary(),
        # `accounting["ledger"]` 与上面的 `ledger` 是**同一份**序列化。在这份专门用来「逐项对账」
        # 的产物里放两份同样的 attempts，只会让复核者对着两个 49 去猜哪个才算数——本文件里账本
        # 只有一处。报告（`acceptance_report.json`）那边没有顶层账本，那份 `accounting` 保持原样。
        "accounting": None if accounting is None else {
            k: v for k, v in accounting.items() if k != "ledger"},
        "rollback_probe_scope": ROLLBACK_PROBE_SCOPE,
        "how_to_read": [
            "每次请求尝试在**发出之前**记账（`attempts[]`），因此失败的尝试同样占一次；",
            "`refusals[]` 里的尝试**没有到达 provider**：它们不计入 `attempts[]`，只记下限流理由；",
            "`section_id` 来自运行时的节作用域，`category` 来自 prompt_version → 类别的唯一登记表；",
            "共用一个客户端的两个类别（蕴含门复用叙述客户端）各有自己的记录，不会漏计也不会重复计数；",
            "半事务反例探针用确定性替身、且在门之外（" + ROLLBACK_PROBE_SCOPE + "），不进这本账。",
            "本文件里的账本正文只有一处（`ledger`）：`accounting` 是同一批尝试的读视图，"
            "**不含** attempts 副本——两份同样的 49 会被读成两件事。",
        ],
    }


def _subject_declaration_block(state: "RunState") -> dict:
    """`subj-2` 的 `subject_declaration` 块（**正常路径与拒绝路径共用同一份构造**）。

    拒绝报告同样要说清楚「这一轮本来是为谁、用哪一期快照跑的」：一次被拒的 run 若不说主体，
    读回的人连「这份缺口属于哪家公司」都无从判断——那正是「失败也可审计」要防的事。

    装配**已完成**时，四样东西在块里**各自直接可读**，不靠读者去别的块里拼：

      * `subject_id` —— 声明主体（`WHERE company_id = ?` 核对过）；
      * `subject_name` —— **核对到的**读者面名称（与权威来源文档登记逐字一致）；
      * `report_as_of` —— **报告截止日**：本轮唯一时钟瞬间换算出来的报告生成日；
      * `snapshot_as_of_date` —— **财务快照日**：核对到的 current 快照的期末。

    后两者是**两个不同的日期轴**，因此必须是两个平级字段。把它们合成一个字段（或让报告日
    回落到快照期末）正是 `rc-rd-1` 修掉的那件事，这里不得再犯：`report_as_of` 只取
    `inputs.report_as_of`（时钟派生），**不取** `dims["as_of_date"]`。
    确认过的其余读数（快照 id / Evidence Set / 清单主体）在 `verified_against` 里。

    `subj-2` 起 `subject_name` 与 `subject_id` 并列为**读数**：两者都是核对过的。尚未核对时
    名称只以 `subject_name_declared` 出现（见 `_unverified_declaration_block`）——与
    `report_as_of_declared` 同一约定，读数位不补 `None`。
    """
    return {
        "declaration_version": SUBJECT_DECLARATION_VERSION,
        "subject_id": state.inputs.company_id,
        # 读者面名称：核对过的那一个（权威来源文档登记），不是声明原值。
        "subject_name": state.inputs.dims.get("company_name"),
        "declared_by": state.inputs.dims.get("subject_declared_by"),
        # 报告截止日：**时钟派生**的那一个（不是快照期末、也不是声明原值）。
        "report_as_of": state.inputs.report_as_of,
        # 财务快照日：核对到的 current 快照期末。与上面是两条轴。
        "snapshot_as_of_date": state.inputs.dims.get("as_of_date"),
        "verified_against": {
            "financial_snapshot_id": state.inputs.dims.get("snapshot_id"),
            "evidence_set_version": state.inputs.evidence_set_version,
            "source_manifest_company_id": state.inputs.source_manifest.company_id,
        },
        "note": ("主体与主体名称由调用参数声明；财务快照与 current Evidence Set 是按该主体"
                 "**核对**出来的（`WHERE company_id = ?`），名称则与权威来源文档登记逐字核对。"
                 "声明与库不一致、或同一主体有多条 current 快照而声明没收窄到唯一一条时整轮拒绝，"
                 "不回落、不任选；库里没有登记名称同样拒绝——**不**由库替声明挑一个名字。"
                 "`report_as_of`（报告截止日，时钟派生）与 `snapshot_as_of_date`（财务快照日）"
                 "是**两条正交的日期轴**，两者不同是常态，不得互相顶替。"),
    }


def _unverified_declaration_block(declaration) -> dict:
    """**装配尚未发生**时的声明块：只有声明本身，没有任何「已核对」的读数。

    `verified_against` 写 `None`（不是 `{}`）：一个空字典会被读成「核对过、结果为空」。

    与装配完成那一份的**形状差异本身就是信息**：这里**没有** `report_as_of` /
    `snapshot_as_of_date` 两个读数（字段不存在），只有 `report_as_of_declared` 与
    `narrowing.snapshot_as_of` 这两个**已声明、未核对**的值。不得把它们改名成读数，
    也不得给读数位补一个 `None`——`None` 会被读成「核对过、结果是空的」。
    """
    if declaration is None:
        return {
            "declaration_version": None, "subject_id": None, "declared_by": None,
            "verified_against": None,
            "note": ("本次拒绝在报告输入声明之前/之外发生：**没有**声明可报告，"
                     "`verified_against` 是 `None`（不是「核对过、结果为空」）。"),
        }
    return {
        "declaration_version": declaration.declaration_version,
        # 已声明、**未核对**的四样：主体 + 主体名称 + 报告截止日 + 快照收窄条件（可能为空）。
        "subject_id": declaration.subject_id,
        # 名称同样是**已声明、未核对**：字段名带 `_declared` 后缀，不得读成核对结果
        #（核对过的名字在 `_subject_declaration_block` 的 `subject_name` 位上）。
        "subject_name_declared": declaration.subject_name,
        "declared_by": declaration.declared_by,
        "report_as_of_declared": declaration.report_as_of,
        "narrowing": {"scope": declaration.scope, "currency": declaration.currency,
                      "purpose": declaration.purpose,
                      "snapshot_as_of": declaration.snapshot_as_of},
        "verified_against": None,
        "note": ("本次拒绝发生在真实输入装配**之前/之外**：声明本身照实报告，但"
                 "`verified_against` 是 `None`——快照、current Evidence Set 与主体名称尚未核对，"
                 "不得读成「已核对且为空」。这里也**没有** `report_as_of` / "
                 "`snapshot_as_of_date` / `subject_name` 三个读数：那是核对之后的字段，"
                 "缺字段 ≠ 读数为空。"),
    }


def _write_refusal(run_dir: Path, *, mode: str, generated_at: str, profile, before: dict,
                   title: str, detail: str, state: RunState | None = None,
                   budget_gate=None, declaration=None) -> int:
    """整轮被拒时的产物：**如实写明拒绝发生在哪里**，并把账本与现场留下。

    两类拒绝共用它，但语义必须分清（这条区分就是本函数存在的理由）：

      * `A0`「能否构成一次真实验收」：在**任何请求之前**整轮拒绝（例如真实模式的调用预算尚未
        全部获批）。一个请求都没有发出，账本是空的——这是 `0/0`，不是「没统计」。
      * `A0`「调用预算门（事前拦截）」：第 N+1 次尝试在**发出之前**被拒。此前已发出的尝试都在
        `attempts[]` 里，被拒的那一次只在 `refusals[]` 里，并写明「若发出会是第几次」。

    两种失败**都不**伪装成「本节无内容」：报告 `status=refused`，且 `honesty` 顶部逐字说明。
    """
    after = _trust_root_hashes(profile)
    # 账本优先取现场（各节已跑过），其次取建门时那一个（A0 在 `RunState` 建起来之前就拒了）。
    ledger = None if state is None else state.budget
    if ledger is None:
        ledger = budget_gate
    refusals = [] if ledger is None else list(ledger.refusals)
    problems = [detail]
    gate = GateOutcome(
        gate_id="A0", title=title, status="refused", detail=detail,
        evidence={"mode": mode,
                  "budget_policy": _call_budget_policy().to_dict(),
                  "enforce": None if ledger is None else bool(ledger.enforce),
                  "attempts_recorded": 0 if ledger is None else len(ledger.attempts),
                  "refusals": refusals,
                  "sections_reached": [] if state is None else sorted(state.sections),
                  "section_errors": {} if state is None else dict(state.section_errors),
                  "artifact": "llm_call_ledger.json",
                  "note": ("`refusals[]` 里的尝试**没有到达 provider**；`attempts_recorded` 是"
                           "拒绝之前已经真实发出的次数")})
    # 现场：真的跑过的节照常落产物（各节已提交的链、报告草案），不与正常路径共用报告文件。
    payload = None if state is None else _artifacts_payload(state)
    if payload is not None:
        _write_json(run_dir / "section_drafts.json", payload["drafts"])
        _write_json(run_dir / "section_claims.json", payload["claims"])
        _write_json(run_dir / "section_narratives.json", payload["narratives"])
        _write_json(run_dir / "section_results.json", payload["results"])
        _write_json(run_dir / "section_evaluations.json", payload["evaluations"])
        _write_json(run_dir / "section_unresolved.json", payload["unresolved"])
        _write_text(run_dir / "before_after.md", _before_after_md(state))
        _write_text(run_dir / "manual_review.md", _manual_review_md(state, [gate]))
        # 来源清单在整轮被拒时同样要留：清单是本轮**输入身份**的现场，不是「跑成功才有的结论」。
        _write_json(run_dir / "source_manifest.json", _j(state.inputs.source_manifest))
        _write_text(run_dir / "source_manifest.md", _source_manifest_md(state))
        # P5：整轮被拒时同样要留**只读诊断**——「被拒」不是「没有现场可看」。诊断逐节写出
        # 逐文档选用/未选用与逐 aspect 的链；没有 SectionDraft 的节逐 aspect 写「本 run 未能
        # 读回验收」，不写成「没有材料」。
        _write_json(run_dir / "failure_diagnostics.json", _failure_diagnostics_payload(state))
        # T1：被拒的一轮同样要留**材料包读回**——「写作被拒」不等于「研究没取到材料」，
        # 这两件事必须分开看得见。读回入口自己不抛，构建失败也只会写成 `build_failed`。
        _material_pack_block = _material_pack_readback_all(state)
        _write_text(run_dir / "material_pack.md", _material_pack_md(state))
        _write_json(run_dir / "material_pack.json", _material_pack_block)
    ledger_payload = _ledger_payload(state, mode=mode, generated_at=generated_at,
                                     run_dir=run_dir, status="refused")
    _write_json(run_dir / "llm_call_ledger.json", ledger_payload)
    honesty = [
        f"本 run 的状态是 **refused**（不是 fail、更不是 pass）：{title}。",
        detail,
    ]
    if ledger is None:
        honesty.append("拒绝发生在建门之前/之外：本轮没有账本，报告里的调用数为 0 是**真的 0**。")
    elif not ledger.attempts:
        honesty.append("账本为空：**一个请求都没有发出**（0 次尝试、0 次成功）。")
    elif refusals:
        honesty.append(
            f"拒绝之前已经真实发出 {len(ledger.attempts)} 次请求"
            f"（按类别 {ledger.category_counts()}，按节 {ledger.section_counts()}）；"
            f"被拒的那一次在 `refusals[]`，它**没有到达 provider**。")
    else:
        # 有尝试、但**没有**事前拒绝：停止原因不是预算门（例如 provider 截断输出）。此时不得
        # 沿用「被拒的那一次没有到达 provider」——那一句在这里是假的，而且会把「已经花掉的
        # 调用」说成没发生。
        honesty.append(
            f"停止之前已经真实发出 {len(ledger.attempts)} 次请求"
            f"（按类别 {ledger.category_counts()}，按节 {ledger.section_counts()}）："
            "本轮**没有**事前预算拒绝，停止原因见下方 A0 结论；已发出的每一次都如实留在账本里"
            "（含失败与截断），既不重跑也不补救。")
    honesty.append("缺口按权威状态原样显示；本 run 不得当作任何通过结论，也不得据此宣布任何阶段关闭。")
    report = {
        "schema_version": ACCEPTANCE_REPORT_SCHEMA_VERSION,
        "runner_version": RUNNER_VERSION,
        "milestone": "M930-3",
        "status": "refused",
        "refused": True,
        "mode": mode,
        "generated_at": generated_at,
        "run_dir": run_dir.name,
        "gates": [gate.to_dict()],
        "gate_summary": {"A0": "refused"},
        # `subj-1`（P4）：**被拒的一轮同样要说清楚「这一轮本来是为谁跑的」**。装配已经完成的
        # 拒绝（`state` 非空）给**已核对**的读数；装配之前的拒绝只有声明本身，`verified_against`
        # 写 `None`（不是一个会被读成「核对过、结果为空」的 `{}`）。
        "subject_declaration": (_subject_declaration_block(state) if state is not None
                                else _unverified_declaration_block(declaration)),
        "section_errors": {} if state is None else dict(state.section_errors),
        "assembly_error": "" if state is None else state.assembly_error,
        "trust_roots_before": before,
        "trust_roots_after": after,
        "llm_calls": {
            "policy_version": APPROVED_BUDGET_POLICY_VERSION,
            "approved_model": APPROVED_BUDGET_MODEL,
            "policy": ledger_payload["budget_policy"],
            "enforced": ledger_payload["enforce"],
            "ledger": ledger_payload["ledger"],
            "sections": ({} if state is None
                         else {s: state.sections[s].llm_calls for s in state.sections}),
            "reconciliation": None,
            "output_capacity": output_capacity_policy(),
            "rollback_probe_scope": ROLLBACK_PROBE_SCOPE,
            "note": ("本轮未跑完：完整链的逐项对账（账本 ↔ 客户端记录 ↔ 各节计量）没有成立的前提，"
                     "因此这里只有账本一个视图，且如实标为 None；账本本身完整保留。"),
        },
        "observations": {},
        "honesty": honesty,
        "problems": problems,
    }
    _write_json(run_dir / "acceptance_report.json", report)
    manifest = {
        "milestone": "M930-3",
        "runner_version": RUNNER_VERSION,
        "acceptance_report_schema_version": ACCEPTANCE_REPORT_SCHEMA_VERSION,
        "mode": mode, "generated_at": generated_at, "status": "refused",
        "refusal": {"gate_id": "A0", "title": title, "detail": detail,
                    "refusals": refusals},
        "llm_budget": {"policy_version": APPROVED_BUDGET_POLICY_VERSION,
                       "approved_model": APPROVED_BUDGET_MODEL,
                       "policy": ledger_payload["budget_policy"],
                       "enforced": ledger_payload["enforce"],
                       "actual": ledger_payload["ledger"],
                       "artifact": "llm_call_ledger.json"},
        "section_errors": {} if state is None else dict(state.section_errors),
        "sections_reached": [] if state is None else sorted(state.sections),
        # P5：被拒的一轮同样有**只读诊断**（逐文档选用/未选用、逐 aspect 的链、被拒提案与待裁决
        # 补件）。没有 `SectionDraft` 的节逐 aspect 写「本 run 未能读回验收」。`state` 为 None
        # 时（拒绝发生在建门之前/之外）没有现场，如实写明诊断不存在，而不是留一份空壳。
        "failure_diagnostics": (
            {"schema_version": FAILURE_DIAGNOSTICS_SCHEMA_VERSION,
             "artifact": "failure_diagnostics.json",
             "not_read_back_label": NOT_READ_BACK_THIS_RUN}
            if state is not None else
            {"schema_version": FAILURE_DIAGNOSTICS_SCHEMA_VERSION,
             "artifact": None,
             "note": "拒绝发生在建立运行现场之前/之外：本轮没有可读回的装配现场，因此不产出诊断"
                     "产物，也不留空壳冒充「已读过」。"}),
        "disclaimer": ("本目录是 M930-3 的**拒绝产物**（不是验收产物）：它记录的是"
                       "「为什么这一轮不被允许继续」，不是任何通过结论。"),
    }
    _write_json(run_dir / "manifest.json", manifest)
    index = []
    for name in ARTIFACTS:
        path = run_dir / name
        if not path.exists():
            continue
        body = path.read_bytes()
        index.append({"artifact": name, "bytes": len(body),
                      "sha256": _sha256_bytes(body)})
    _write_json(run_dir / "artifact_index.json",
                {"output_dir": run_dir.name, "artifacts": index})
    _print(f"REFUSED：{title} —— {detail}")
    ledger_payload_path = run_dir / "llm_call_ledger.json"
    _print(f"拒绝产物已落盘：{ledger_payload_path if ledger_payload_path.exists() else run_dir}")
    return 1


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def execute_run(*, run_id: str | None, results_root: Path, mode: str,
                model: str | None, subject: str, subject_name: str,
                subject_scope: str | None = None, subject_currency: str | None = None,
                subject_purpose: str | None = None,
                subject_snapshot_as_of: str | None = None) -> int:
    from harness import report_clock as RC

    # 一次运行只捕获**一个时钟瞬间**（O-11）：目录时间戳、`generated_at`、`report_as_of`
    # 全部由这一个瞬间派生。改前这里是三次独立采样，且 `report_as_of` 取自财务快照的
    # `as_of_date`——那既让三个字段可能跨午夜错位，也把「财务数据期末」冒充成了
    # 「报告生成日」。缺时区配置即 fail-closed（在下面这一行就抛）。
    clock = RC.capture_report_clock()
    # 报告输入声明（P4 / `subj-2`）：主体与**主体名称**来自调用参数，报告参考日来自上面那一个
    # 时钟瞬间（O-11：一次运行仍只有一个瞬间）。声明在这里成形，之后全线只读它，不再回头问库
    # 「到底是谁」——库只被用来核对声明的这两项。
    declaration = DeclaredReportInput(
        subject_id=subject, subject_name=subject_name, report_as_of=clock.report_as_of,
        scope=subject_scope, currency=subject_currency, purpose=subject_purpose,
        snapshot_as_of=subject_snapshot_as_of)
    final_run_id = run_id or (RUN_ID_PREFIX + RC.run_stamp(clock))
    if not final_run_id.startswith(RUN_ID_PREFIX):
        raise AcceptanceRefusal(
            f"--run-id 必须以 {RUN_ID_PREFIX!r} 开头（结果目录的归属必须可辨识）")
    run_dir = results_root / final_run_id
    if run_dir.exists():
        raise AcceptanceRefusal(
            f"结果目录已存在，create-only 拒绝覆盖：{run_dir}（fail-closed）")
    generated_at = clock.generated_at

    from llm import budget as LB
    from llm import client as LLC
    from planning import demo_scope as SC
    from sections import narrative_organizer as NO

    profile = SC.load_demo_scope_profile(active_profile_path())
    # 节序**只由所选 profile** 派生，且必须在任何预算派生 / 目录创建之前绑定：下面的
    # `_call_budget` → `_assert_budget_covers_structural_bound` → `_section_aspect_counts()`
    # 都按 `SECTION_ORDER` 推结构性上界；绑晚了就会拿旧三节的上界去卡双节 run。
    bound_order = bind_section_order(profile)
    before = _trust_root_hashes(profile)
    _print(f"[{RUNNER_VERSION}] demo scope profile={profile.profile_id!r}"
           f"（{active_profile_path()}）；节序={list(bound_order)}")
    _print(f"[{RUNNER_VERSION}] 信任根快照 {len(before)} 项；开始构建真实输入（只读）")
    run_dir.mkdir(parents=True)
    llm_calls: dict = {}
    gates: list[GateOutcome] = []
    state: RunState | None = None
    budget_gate = None
    try:
        # 写作策略是纯配置（读常量 + 模型身份预检），先立起来：模型身份不匹配时在这里就拒绝，
        # 不必先建门、更不必先进环境。
        policy = _writer_policy(mode=mode, model=model)
        # **门在进入真实环境之前安装**。研究发生在 `RealEnvironment._build`（`env.inputs`）
        # 内部，晚装一步研究阶段的 `reserve_attempt` 就会看到「没有装门」——既不识别、也不
        # 计数、也不受任何上限。真实模式未全部获批即在此整轮拒绝（A0），一个 provider 请求
        # 都不会发出去；命令行无法抬高任何上限（数字只定义在 `_call_budget_policy`）。
        budget_gate = _call_budget(mode=mode)
        LB.install(budget_gate)
        try:
            with RealEnvironment(clock=clock, mode=mode, declaration=declaration) as env:
                inputs = env.inputs
                _print(f"真实输入就绪：主体={inputs.company_id}"
                       f"（{inputs.dims.get('company_name')}，已与权威来源文档登记核对）"
                       f"（由报告输入声明，{declaration.declared_by}，"
                       f"{declaration.declaration_version}）"
                       f" 快照选择日={inputs.dims.get('as_of_date')} "
                       f"document={inputs.document_id}@{inputs.document_version} "
                       f"report_as_of={inputs.report_as_of}（{clock.report_timezone}，"
                       f"同一瞬间 UTC {inputs.generated_at}）")
                narrator, entailment, sentence = _clients(mode=mode, model=model)
                if mode == MODE_REAL:
                    _print(f"真实模式：model_policy={policy.model_policy!r}；"
                           f"策略 {APPROVED_BUDGET_POLICY_VERSION} 已全部获批"
                           "（本 runner 不做重试、不换模型、不换 prompt）")
                else:
                    _print("离线模式：账本照记（enforce=False），真实上限见报告 budget_policy")
                state = RunState(inputs=inputs, run_dir=run_dir, mode=mode,
                                 generated_at=generated_at, policy=policy)
                state.budget = budget_gate
                _run_dir_db(run_dir, _drive_and_verify, state=state, narrator=narrator,
                            entailment=entailment, sentence=sentence)
                llm_calls = _call_accounting(state, narrator=narrator,
                                             entailment=entailment, sentence=sentence)
                gates = _run_gates(state, run_dir, narrator=narrator, entailment=entailment,
                                   sentence=sentence)
        finally:
            # 即使 `__enter__`/`_build` 抛错也卸载门：**研究期间已经发生的尝试账本留在
            # `budget_gate` 上**（拒绝报告会把它封存进产物），而不是随门一起消失。
            LB.uninstall()
        after = _trust_root_hashes(profile)
        gates.append(_gate_a6_trust_roots(before, after))
        status = ("pass" if all(g.status == "pass" for g in gates)
                  else "fail" if any(g.status == "fail" for g in gates) else "refused")
        try:
            written = _write_run(state, before=before, after=after, gates=gates,
                                 llm_calls=llm_calls, status=status)
        except Exception as exc:  # noqa: BLE001 —— 报告装配失败也**不得**丢掉现场
            # 报告装配是**报告正文**的计算，不是主链的一环：它抛错时已经写好的产物（各节链、
            # 拒绝审计、材料包、诊断、`manual_review.md`）都在，唯独**报告本身**没有——真实 run
            # r4 就是这样：读者拿到的是一堆半成品，而「这一轮怎么了」的那份文件恰好不存在。
            # 兜底写出降级报告 + artifact index 之后**重抛**：现场一定要留下，代码缺陷不得被咽掉。
            detail = f"{type(exc).__name__}: {str(exc)[:400]}"
            _print(f"[报告装配失败] {detail} → 先落降级报告（status=assembly_error）与 "
                   "artifact index，再重抛")
            _write_run_assembly_fallback(state, before=before, after=after, gates=gates,
                                         llm_calls=llm_calls, status=status, error=detail)
            raise
    except AcceptanceRefusal as exc:
        return _write_refusal(run_dir, mode=mode, generated_at=generated_at,
                              profile=profile, before=before,
                              title="能否构成一次真实验收",
                              detail=f"{type(exc).__name__}: {exc}",
                              state=state, budget_gate=budget_gate,
                              declaration=declaration)
    except LB.LLMCallBudgetError as exc:
        # 预算门在**发请求之前**拒绝：本次运行到此为止。产物保留现场（各节已提交的链、
        # 账本、被拒的那一次尝试），并如实写明「第 N+1 次没有到达 provider」。
        return _write_refusal(run_dir, mode=mode, generated_at=generated_at,
                              profile=profile, before=before,
                              title="调用预算门（事前拦截）",
                              detail=f"{type(exc).__name__}: {exc}",
                              state=state, budget_gate=budget_gate,
                              declaration=declaration)
    except LLC.LLMTruncatedResponse as exc:
        # provider 明确表示输出被截断：本次调用**判失败**并停止整轮（不重跑、不换模型、不换
        # prompt）。残缺的提案/段落/蕴含决定不得当成合格内容继续往下走。
        return _write_refusal(run_dir, mode=mode, generated_at=generated_at,
                              profile=profile, before=before,
                              title="provider 截断输出（失败即停止）",
                              detail=(f"{type(exc).__name__}: {exc}；被截断的那一次仍在账本与"
                                      "logs/llm 里（call_id / finish_reason / output_tokens），"
                                      "本轮不自动重跑"),
                              state=state, budget_gate=budget_gate,
                              declaration=declaration)
    for gate in written["report"]["gates"]:
        _print(f"  {gate['gate_id']} {gate['status']:>4}  {gate['title']}"
               + ("" if gate["status"] == "pass" else f"  ← {gate['detail'][:200]}"))
    _print(f"状态 {written['report']['status']}；目录 {run_dir}")
    return 0 if written["report"]["status"] == "pass" else 1


def _run_dir_db(run_dir: Path, fn, **kwargs) -> None:
    """把 `sections.store` 的库路径指向 run 目录内的库，并在结束时还原。

    还原不是礼貌：`ST._db_path` 是模块全局，不还原就会把后续同进程的操作也导到 run 目录。
    """
    from sections import store as ST

    saved = ST._db_path
    ST.init_db(run_dir / "section_chain_v2.db")
    try:
        fn(**kwargs)
    finally:
        ST._db_path = saved


def _drive_and_verify(*, state: RunState, narrator, entailment, sentence) -> None:
    """跑真实输入上的三节主链 → 读回 → 组装 → 半事务反例（顺序有意义）。

    半事务反例**永远**用确定性替身：它验的是事务原子性，不是模型输出。真实模式下若在这里
    再调一次模型，就会把调用数翻倍——超预算，因此这条不能由 `--mode` 决定。

    探针**在预算门之外**（`LB.suspended()`）：它会用替身把三节**再驱动一遍**。若让它进账，
    真实模式下会平白占掉已批准的额度（一次几十次的替身调用足以把一次真运行掐死在半路），
    离线模式下会把每节的账翻倍。替身不发真实请求，因此正确的记账是「不在门内」——这一条在
    A7 的 evidence 里逐字写明，不是默认放行。
    """
    from llm import budget as LB
    from sections import store as ST

    _drive_sections(state, llm_client=narrator, entailment_llm_client=entailment,
                    final_sentence_llm_client=sentence, section_store=ST)
    _readback_sections(state, ST)
    _assemble(state)
    if state.sections:
        with LB.suspended():
            state.rollback = _rollback_drive(
                state, llm_client=OfflineNarrationClient(),
                entailment_llm_client=OfflineEntailmentClient(),
                final_sentence_llm_client=OfflineFinalSentenceClient())


def _client_category_counts(narrator, entailment, sentence) -> dict:
    """客户端侧记录按**类别**归并。

    真实模式下两道语义门都复用叙述客户端：蕴含调用与最终句核验调用都已经出现在
    `narrator.calls` 里，而 `LlmEntailmentClient` / `LlmSentenceFidelityClient` 自己**没有**
    `calls` —— 因此这里既不会漏计，也不会把同一请求记两次。离线模式下三个替身各自带 `calls`，
    三个都要读进来（少读一个，账本与客户端记录就会在某一个类别上不等）。
    """
    from llm import budget as LB

    policy = _call_budget_policy()
    out: dict = {}
    for client in (narrator, entailment, sentence):
        for call in getattr(client, "calls", ()) or ():
            prompt_version = call.get("prompt_version")
            try:
                category = policy.category_of(prompt_version)
            except LB.LLMCallAttributionError:
                category = f"<未登记:{prompt_version}>"
            out[category] = out.get(category, 0) + 1
    return out


def _reconcile_calls(state: RunState, *, narrator, entailment, sentence) -> dict:
    """三个计数视图的**逐项**对账：账本 / 客户端记录 / 各节计量。

    三者必须回答同一个问题——「这一节、这一类，一共发起了几次请求尝试」：

      * **账本**（`llm.budget`）：每次尝试的 `(category, section_id)`，在发请求**之前**记账，
        失败尝试同样占一次；
      * **客户端记录**：`narrator.calls` / `entailment.calls` / `sentence.calls`（真实模式下
        后者没有 `calls`——两道语义门共用叙述适配器，调用记在 `narrator.calls` 里），
        按 `prompt_version → category` 归并；
      * **各节计量**：`BackboneSectionWriterOutput.llm_calls`（门前提案 + 逐候选蕴含 +
        有界重写的各轮 + 门后组织 + 可选章级评估）。

    旧口径里前两者与第三者不是同一个量：`llm_calls` 漏掉门后组织那一次，于是「各节之和」
    系统性地少算。差额是可证的，不是猜的——它恰是门后组织的**尝试次数**（本次实测 2 次，都在
    公司节：有界重写让它走了两轮组织；财务节 0 次，所以逐节 22→24、25→25）。
    本函数把它**算出来并断言**，而不是继续留在报告里当两个互不相干的数字。

    三个视图**不得混用**（`views` 逐条写明各视图各自回答什么）。第三个视图**只对有产出的节
    存在**：本节抛错时压根没有 `BackboneSectionWriterOutput`，因此失败节的调用按**显式桶**
    （`meter_absent_sections`）进入守恒式——账本 = 各节计量之和 + 无产出节尝试 + 无归属尝试，
    残差必须为 0。「无产出」**不得**被读成「没发过请求」：这些请求已经发出、已经占了预算，
    只是没有落成第三个视图（旧口径在这里 `continue` 过去，于是那句「三视图逐项相等」在失败节
    上是**假的**，且没有任何数字能让人看出来）。

    **两条预算轴必须分别守恒**（`CROSS_DOCUMENT_JOINT_RETRIEVAL_PLAN.md` §0.3.1）。研究锚点
    一旦装进同一本账，上面前三个视图就**只是写作轴的**视图：`narrator.calls` /
    `entailment.calls` 里没有研究调用（研究走 `RealResearchLLM`，它的调用记录在**它自己**的
    `calls` 上，不进这两个写作侧适配器），`llm_calls` 也按节归集。拿写作轴的三视图去对一本含
    研究尝试的账，差额会**假**地等于研究调用数——一个看起来像「漏记」的残差。因此这里先把账
    按轴拆开：

      * `axis_identity`：`ledger_total == writing_axis_total + research_axis_total`（残差 0）；
      * 写作轴：原三视图 + 原守恒式，一字不改（只是范围收窄到写作轴）；
      * 研究轴（**第四个视图**）：账本里 `axis == research` 的尝试，按 `topic:<id>` 作用域
        逐 topic 归集，另有 `research_axis_total == 逐 topic 之和`（残差 0），
        并逐项与已批准的研究轴上限比对（`observed ≤ cap`）。

    轴与作用域必须**自洽**：写作尝试的作用域必须是节（`section:<id>`），研究尝试的必须是
    topic（`topic:<id>`）——错配会在请求期被门拒绝，这里再对**落地后的账**复核一遍，
    因为账本读错比没有账本更糟。也正因如此，`stray`（不属于任何一节的作用域）只在写作轴上
    判定：研究作用域 `topic:*` 本来就**不**属于任何一节，把它算作「无归属」是把正确的账读错。
    """
    from llm import budget as LB

    ledger = state.budget
    problems: list[str] = []
    policy = _call_budget_policy()
    meter_by_section = {s: int(state.sections[s].llm_calls) for s in state.sections}
    evidence: dict = {
        # 「三视图不得混用」：每个视图只回答一个问题，键名不得互相顶替。
        "views": {
            "dispatched": ("账本：每次请求尝试的 `(category, section_id)`，在发请求**之前**"
                           "记账；失败的尝试同样占一次"),
            "succeeded": (f"账本里 `status == {LB.STATUS_OK!r}` 的那些尝试；客户端记录的 "
                          "status 只作**单向**判据（见 `client_ok_by_category`），"
                          "不得反过来当成功口径"),
            "category": "`prompt_version → category`（策略登记的唯一口径）：三个视图都按它归并",
            "meter": ("`BackboneSectionWriterOutput.llm_calls`：**只有有产出的节**才有这个视图；"
                      "没有产出的节（抛错）不存在第三个视图，只能按账本读数入守恒式"),
            "axis": ("上面三个视图**都是写作轴的**视图（作用域 `section:<id>`）；研究轴自成"
                     "第四个视图（作用域 `topic:<id>`，见 `research`）。两轴的账不得互相"
                     "顶替：`narrator.calls` / `entailment.calls` / `llm_calls` 里根本没有"
                     "研究调用"),
        },
        "meter_by_section": meter_by_section,
        "meter_sum": sum(meter_by_section.values()),
        "client_category_counts": _client_category_counts(narrator, entailment, sentence),
        "client_calls": {"narrator": len(getattr(narrator, "calls", ()) or ()),
                         "entailment": len(getattr(entailment, "calls", ()) or ()),
                         "sentence": len(getattr(sentence, "calls", ()) or ())},
        "ledger_present": ledger is not None,
    }
    if ledger is None:
        problems.append("本轮没有调用账本：调用计量无从逐项复核")
        evidence["problems"] = problems
        return evidence
    # 先按**轴**拆账：写作轴的三个视图与守恒式只数写作尝试，研究尝试自成第四个视图。
    writing_attempts = [a for a in ledger.attempts if a.axis == LB.AXIS_WRITING]
    research_attempts = [a for a in ledger.attempts if a.axis == LB.AXIS_RESEARCH]
    known_axes = set(LB.AXES)
    for attempt in ledger.attempts:
        if attempt.axis not in known_axes:
            problems.append(
                f"账本里出现未登记的预算轴 {attempt.axis!r}（call_id={attempt.call_id!r}）："
                "未登记的轴既不计入写作轴也不计入研究轴，守恒式会把它算成残差")
        expected_kind = (LB.SCOPE_KIND_TOPIC if attempt.axis == LB.AXIS_RESEARCH
                         else LB.SCOPE_KIND_SECTION)
        if attempt.scope_kind != expected_kind:
            problems.append(
                f"类别 {attempt.category!r}（轴 {attempt.axis!r}）的作用域种类是 "
                f"{attempt.scope_kind!r}，应为 {expected_kind!r}：轴与作用域错配的账读不出"
                "「谁花了多少次」")
        if attempt.scope_kind != LB.scope_kind_of(attempt.section_id):
            problems.append(
                f"尝试 {attempt.call_id!r} 记下的作用域种类 {attempt.scope_kind!r} 与作用域"
                f"字符串 {attempt.section_id!r} 自身的前缀不一致：同一次尝试的归属只有一处真值")
    by_section: dict = {}
    by_section_status: dict = {}
    by_category: dict = {}
    by_category_ok: dict = {}
    statuses_seen: set = set()
    research_by_topic: dict = {}
    research_by_topic_status: dict = {}
    research_by_category: dict = {}
    research_ok_by_category: dict = {}
    for attempt in research_attempts:
        row = research_by_topic.setdefault(attempt.section_id, {})
        row[attempt.category] = row.get(attempt.category, 0) + 1
        srow = research_by_topic_status.setdefault(attempt.section_id, {})
        srow[attempt.status] = srow.get(attempt.status, 0) + 1
        research_by_category[attempt.category] = research_by_category.get(attempt.category, 0) + 1
        if attempt.status == LB.STATUS_OK:
            research_ok_by_category[attempt.category] = \
                research_ok_by_category.get(attempt.category, 0) + 1
    for attempt in writing_attempts:
        by_section.setdefault(attempt.section_id, {})
        row = by_section[attempt.section_id]
        row[attempt.category] = row.get(attempt.category, 0) + 1
        by_section_status.setdefault(attempt.section_id, {})
        srow = by_section_status[attempt.section_id]
        srow[attempt.status] = srow.get(attempt.status, 0) + 1
        by_category[attempt.category] = by_category.get(attempt.category, 0) + 1
        if attempt.status == LB.STATUS_OK:
            by_category_ok[attempt.category] = by_category_ok.get(attempt.category, 0) + 1
        statuses_seen.add(attempt.status)
    # 状态词表是全账的判据（未登记的状态不得被静默归并），因此研究轴的状态也要进来。
    statuses_seen |= {a.status for a in research_attempts}
    organizer_prompt = _organizer_prompt_version()
    organizer_attempts = sum(1 for a in writing_attempts
                             if str(a.prompt_version) == organizer_prompt)
    evidence.update({
        "ledger_by_section": {k: by_section[k] for k in sorted(by_section)},
        "ledger_by_section_status": {k: by_section_status[k] for k in sorted(by_section_status)},
        "ledger_by_category": dict(sorted(by_category.items())),
        "ledger_by_status": {k: sum(1 for a in ledger.attempts if a.status == k)
                             for k in sorted(statuses_seen)},
        "ledger_ok_by_category": dict(sorted(by_category_ok.items())),
        "ledger_total": len(ledger.attempts),
        "ledger_section_totals": {k: sum(by_section[k].values()) for k in sorted(by_section)},
        "organizer_attempts": organizer_attempts,
        "organizer_prompt_version": organizer_prompt,
        # 旧口径的差额：`narrator_calls`（含组织）− 各节之和（不含组织）= 组织次数。
        # 修好之后三个视图相等，这个数就是「原先漏记了多少次」。
        "meter_omitted_before_fix": organizer_attempts,
        "status_vocabulary": [LB.STATUS_OK, LB.STATUS_ERROR, LB.STATUS_RESERVED],
    })
    unknown_status = sorted(statuses_seen - {LB.STATUS_OK, LB.STATUS_ERROR, LB.STATUS_RESERVED})
    if unknown_status:
        problems.append(
            f"账本里出现未登记的状态 {unknown_status}：『已发』只认账本条数、『成功返回』只认 "
            f"{LB.STATUS_OK!r}，未登记的状态不得被静默归并进这两者中的任何一个")
    # 失败节的调用也在场：本节抛错 ⇒ 没有 `BackboneSectionWriterOutput` ⇒ 第三个视图不存在，
    # 但请求确实发出去了、也确实占了预算。逐节列出来，而不是让它们从账上消失。
    meter_absent_sections: dict = {}
    unattributed_attempts = 0
    for section_id, counts in sorted(by_section.items()):
        total = sum(counts.values())
        statuses = by_section_status.get(section_id, {})
        if section_id not in SECTION_ORDER:
            problems.append(f"账本里有不归属于任何一节的尝试：{section_id!r}")
            unattributed_attempts += total
            continue
        if section_id not in meter_by_section:
            if section_id not in state.section_errors:
                problems.append(
                    f"{section_id} 有 {total} 次尝试，但本节既没有产出、"
                    "也没有失败记录（这笔账没有归属）")
                unattributed_attempts += total
                continue
            meter_absent_sections[section_id] = {
                "dispatched": total,
                "returned_ok": int(statuses.get(LB.STATUS_OK, 0)),
                "returned_error": int(statuses.get(LB.STATUS_ERROR, 0)),
                "unsettled": int(statuses.get(LB.STATUS_RESERVED, 0)),
                "by_category": dict(counts),
                "by_status": dict(statuses),
                "error": str(state.section_errors.get(section_id, "")),
                "meter": None,
                "meter_reason": ("本节抛错，不存在 `BackboneSectionWriterOutput`，因此没有"
                                 "第三个视图可对——这些尝试只能按账本入守恒式"),
            }
            continue
        if total != meter_by_section[section_id]:
            problems.append(
                f"{section_id} 的账本尝试数 {total} 与本节计量 "
                f"{meter_by_section[section_id]} 不一致（同一件事必须只有一个数）")
    attempts_unmetered = sum(v["dispatched"] for v in meter_absent_sections.values())
    # 写作轴的守恒式：**范围是写作轴的账**，不是整本账（整本账还含研究尝试，见下面的
    # `axis_identity`）。用整本账会让残差**假**地等于研究调用数——一个看起来像漏记的差额。
    writing_total = len(writing_attempts)
    residual = (writing_total
                - (evidence["meter_sum"] + attempts_unmetered + unattributed_attempts))
    research_by_topic_sum = sum(sum(v.values()) for v in research_by_topic.values())
    research_residual = len(research_attempts) - research_by_topic_sum
    axis_residual = len(ledger.attempts) - (writing_total + len(research_attempts))
    axis_cap = policy.axis_cap_for(LB.AXIS_RESEARCH)
    evidence.update({
        "meter_absent_sections": meter_absent_sections,
        "attempts_unmetered": attempts_unmetered,
        "attempts_unattributed": unattributed_attempts,
        # 守恒式写成一条恒等式，而不是让读者自己去加：它把上面的逐节结论收成**一个数**，
        # 因此残差为 0 就是「没有多出来、也没有漏掉的计数」这一句话的全部证据。
        "conservation": {
            "equation": ("writing_axis_total == meter_sum + attempts_unmetered "
                         "+ attempts_unattributed"),
            "writing_axis_total": writing_total,
            "meter_sum": evidence["meter_sum"],
            "attempts_unmetered": attempts_unmetered,
            "attempts_unattributed": unattributed_attempts,
            "residual": residual,
            "holds": residual == 0,
        },
        # **第四个视图**：研究轴上的一切都按 topic 作用域归集，不与写作轴混算。
        "research": {
            "view": ("账本里 `axis == 'research'` 的尝试，按 `topic:<id>` 作用域逐 topic 归集；"
                     "研究调用不走 `narrator`/`entailment` 两个写作侧适配器，因此这里只有账本"
                     "**这一个**视图（真实模式下另有 logs/llm 的 call_id 对账，见 A7 的 "
                     "`llm_log_reconciliation`）。研究侧 LLM 自己的 `calls`（含截断读数）是"
                     "**另一个**可见面，见 A7 的 `truncation_policy.research_unrecovered`"),
            "by_topic": {k: dict(research_by_topic[k]) for k in sorted(research_by_topic)},
            "by_topic_status": {k: dict(research_by_topic_status[k])
                                for k in sorted(research_by_topic_status)},
            "topic_totals": {k: sum(research_by_topic[k].values())
                             for k in sorted(research_by_topic)},
            "by_category": dict(sorted(research_by_category.items())),
            "ok_by_category": dict(sorted(research_ok_by_category.items())),
            "axis_total": len(research_attempts),
            "by_axis": ledger.axis_counts(),
            "by_scope_kind": ledger.scope_kind_counts(),
            "axis_cap": axis_cap.to_dict(),
            "per_topic_cap": axis_cap.max_attempts_per_scope,
            "per_topic_cap_by_topic": (None if not axis_cap.per_scope_max_attempts
                                       else dict(sorted(axis_cap.per_scope_max_attempts.items()))),
            "axis_max_attempts": axis_cap.max_attempts,
            "conservation": {
                "equation": "research_axis_total == sum(research.topic_totals)",
                "research_axis_total": len(research_attempts),
                "by_topic_sum": research_by_topic_sum,
                "residual": research_residual,
                "holds": research_residual == 0,
            },
            "note": ("研究轴上限（整轴 `axis_max_attempts` / 逐 topic `per_topic_cap_by_topic`；"
                     "`per_topic_cap` 是**单值缺省**，只用于未登记 scope 的退路，读产物时不要把"
                     "它当成逐 topic 的上限）**不并入**写作轴的整轮 `total_max_attempts`："
                     "两轴各有独立上限与独立批准状态，研究调用不消耗已批准的写作额度"),
        },
        # 整本账的第一层恒等式：两条轴**恰好**分完账本，没有第三处可落。
        "axis_identity": {
            "equation": "ledger_total == writing_axis_total + research_axis_total",
            "ledger_total": len(ledger.attempts),
            "writing_axis_total": writing_total,
            "research_axis_total": len(research_attempts),
            "residual": axis_residual,
            "holds": axis_residual == 0,
        },
    })
    if axis_residual != 0:
        problems.append(
            f"两轴恒等式不成立：账本 {len(ledger.attempts)} ≠ 写作轴 {writing_total} + "
            f"研究轴 {len(research_attempts)}（残差 {axis_residual}）："
            "每一笔账必须恰好落在一条轴上")
    if residual != 0:
        # 它是上面逐节结论的**同义重述**（逐节不一致 / 无归属正是残差的两个来源），不新增判据；
        # 留着是为了让「一个数不为 0」本身在报告里可见——将来只改一处循环也会在这里现形。
        problems.append(
            f"写作轴守恒式不成立：写作轴 {writing_total} ≠ 各节计量之和 "
            f"{evidence['meter_sum']} + 无产出节 {attempts_unmetered} + 无归属 "
            f"{unattributed_attempts}（残差 {residual}）")
    if research_residual != 0:
        problems.append(
            f"研究轴守恒式不成立：研究轴 {len(research_attempts)} ≠ 逐 topic 之和 "
            f"{research_by_topic_sum}（残差 {research_residual}）")
    # 研究轴上限是事前门判过的，这里再对**落地后的账**复核一次：本轮的观测值不得超过上限。
    # 离线模式 `enforce=False`（上限不拦），因此这一条在离线模式是**真的判据**而非同义重述。
    # 每个 topic 与**它自己**的上限比（acc-20：逐 topic 自派生）。用单值缺省去比全部 topic
    # 会把「本 topic 超发」判成「某几个大 topic 超发」，而真正越线的那个反而读不出来。
    over = {t: n for t, n in
            ((t, sum(research_by_topic[t].values())) for t in sorted(research_by_topic))
            if axis_cap.limit_for_scope(t) is not None
            and n > int(axis_cap.limit_for_scope(t))}
    if over:
        problems.append(
            "研究轴逐 topic 观测值超过**本 topic** 的已批准上限："
            + "；".join(f"{t} 观测 {n} > 上限 {axis_cap.limit_for_scope(t)}"
                        for t, n in sorted(over.items())))
    if axis_cap.max_attempts is not None and len(research_attempts) > int(axis_cap.max_attempts):
        problems.append(
            f"研究轴观测值 {len(research_attempts)} 超过已批准上限 {axis_cap.max_attempts}")
    # 反向：本节自称发过调用、账本里却一次都没有 ⇒ 计量虚高（另一类漏计/虚记）。
    for section_id, meter in sorted(meter_by_section.items()):
        if meter and section_id not in by_section:
            problems.append(
                f"{section_id} 的计量自称 {meter} 次调用，但账本里没有任何该节的尝试："
                "计量不得记下账本上没有的调用")
    client_counts = evidence["client_category_counts"]
    # 客户端侧记录是**写作轴**的视图。研究类别出现在这里，说明某次研究调用被记到了写作客户端
    # 名下——那会让「写作轴花了多少次」读成含研究调用的数，正是本项要修的读错。
    stray_research = sorted(c for c in client_counts if c in LB.RESEARCH_CATEGORIES)
    if stray_research:
        problems.append(
            f"写作客户端记录里出现研究类别 {stray_research}：研究调用不得记进写作客户端"
            "（两轴各有独立的计数视图）")
    for category in sorted(set(by_category) | set(client_counts)):
        if int(by_category.get(category, 0)) != int(client_counts.get(category, 0)):
            problems.append(
                f"类别 {category!r} 的账本尝试数 {by_category.get(category, 0)} 与客户端侧记录 "
                f"{client_counts.get(category, 0)} 不一致（共用客户端不得漏计或重复计数）")
    # 「已发」与「成功返回」是两个不同的量：上面按类别对的是**已发**，这里对**成功返回**。
    # 判据只有一个方向：客户端记录说某次调用成功了，账本就必须有一次同样成功的尝试。
    # 反方向**不作判据**——`settle` 与「适配器落记录」之间隔着几行生产代码，账本成功而记录
    # 缺失（例如落盘日志抛错）不构成「账本记了不存在的调用」，那是另一条由
    # `_reconcile_with_llm_logs` 按 call_id 判的命题。离线替身的记录不带 status（它不报
    # provider 侧结局），因此这条在离线模式**不适用**——不是「通过了」。
    client_status: dict = {}
    client_ok_by_category: dict = {}
    client_status_unreported = 0
    for client in (narrator, entailment, sentence):
        for call in getattr(client, "calls", ()) or ():
            status = call.get("status")
            if status is None:
                client_status_unreported += 1
                continue
            client_status[str(status)] = client_status.get(str(status), 0) + 1
            if str(status) != LB.STATUS_OK:
                continue
            try:
                category = policy.category_of(call.get("prompt_version"))
            except LB.LLMCallAttributionError:
                category = f"<未登记:{call.get('prompt_version')}>"
            client_ok_by_category[category] = client_ok_by_category.get(category, 0) + 1
    applicable = bool(client_status) and client_status_unreported == 0
    evidence.update({
        "client_status_counts": dict(sorted(client_status.items())),
        "client_ok_by_category": dict(sorted(client_ok_by_category.items())),
        "client_status_unreported_calls": client_status_unreported,
        "client_status_applicable": applicable,
        "client_status_note": ("记录不带 status 时记 `applicable=false`（离线替身不报 provider "
                              "侧结局）：**不适用**不是通过"),
    })
    if applicable:
        for category in sorted(client_ok_by_category):
            ledger_ok = int(by_category_ok.get(category, 0))
            if client_ok_by_category[category] > ledger_ok:
                problems.append(
                    f"类别 {category!r} 的客户端记录里有 {client_ok_by_category[category]} 次"
                    f"『成功返回』，账本里只有 {ledger_ok} 次成功：客户端不得把账本没有记成"
                    "成功的调用说成成功")
    evidence["problems"] = problems
    return evidence


def _organizer_prompt_version() -> str:
    from sections import narrative_organizer as NO

    return str(NO.NARRATIVE_ORGANIZER_PROMPT_VERSION)


def _reconcile_with_llm_logs(ledger, *, logs_dir: Path | None = None) -> dict:
    """真实模式下按 call_id 与 `logs/llm` 对账（离线模式没有日志文件，不做这条）。

    **判据**只有一个方向：账本里 `status='ok'` 的每次都必须在日志目录里有同名记录——「账上
    记了成功、日志里没有」说明账本记的不是真实请求。反过来（日志里多出账本没记的）**不作
    判据**：日志目录是进程级的、不按 run 隔离，别的进程在同一时间也会往那里写；本轮只如实
    报出目录里的记录总数（`log_records`），不把别人的日志算成本轮的请求。
    另：`_log_llm_call` 只在**成功返回之后**落盘，失败尝试没有日志——这正是账本必须独立存在
    的原因：失败请求不进日志，但必须进账。
    """
    from llm import client as LLC

    root = Path(logs_dir) if logs_dir is not None else LLC.LOGS_DIR
    present: set[str] = set()
    if root.exists():
        for path in root.glob("*.jsonl"):
            name = path.name
            if "__" in name:
                present.add(name.rsplit("__", 1)[-1].removesuffix(".jsonl"))
    ledger_ids = {a.call_id for a in ledger.attempts}
    ok_ids = {a.call_id for a in ledger.attempts if a.status == "ok"}
    missing = sorted(ok_ids - present)
    out = {"log_dir": str(root), "log_records": len(present),
           "ledger_ok_without_log": missing[:20],
           "ledger_ok_without_log_count": len(missing),
           "ledger_ids_not_in_logs_count": len(sorted(ledger_ids - present))}
    problems = []
    if missing:
        problems.append(
            f"账本里 {len(missing)} 次「成功」的调用在 logs/llm 里没有对应记录："
            "账本不得记下没有真实请求的调用")
    out["problems"] = problems
    return out


def _call_accounting(state: RunState, *, narrator, entailment, sentence) -> dict:
    """报告里的调用账（策略 / 写作轴三个计数视图 / 研究轴第四个视图 / 逐项对账）。"""
    ledger = state.budget
    reconciliation = _reconcile_calls(state, narrator=narrator, entailment=entailment,
                                      sentence=sentence)
    return {
        "policy_version": APPROVED_BUDGET_POLICY_VERSION,
        "approved_model": APPROVED_BUDGET_MODEL,
        "policy": None if ledger is None else ledger.policy.to_dict(),
        "enforced": None if ledger is None else bool(ledger.enforce),
        "narrator_calls": len(getattr(narrator, "calls", ()) or ()),
        "entailment_calls": len(getattr(entailment, "calls", ()) or ()),
        # 最终句语义门 B 的客户端记录。真实模式下它是**适配器包装**（没有 `calls`），因此这里
        # 读出来是 0——那不是「没核验」，而是「调用记在共用适配器 `narrator.calls` 里」。
        "final_sentence_calls": len(getattr(sentence, "calls", ()) or ()),
        "sections": {s: state.sections[s].llm_calls for s in state.sections},
        "ledger": None if ledger is None else ledger.summary(),
        "reconciliation": reconciliation,
        # 研究轴单列一行：它的上限、批准状态、逐 topic 观测值都不与写作轴混算。
        "research_axis": _research_axis_report_block(state),
        "output_capacity": output_capacity_policy(),
        "truncation_policy": truncation_policy(
            state, narrator=narrator, entailment=entailment, sentence=sentence,
            research=_research_client_of(state)),
        "rollback_probe_scope": ROLLBACK_PROBE_SCOPE,
        "note": ("**写作轴**的三个计数视图在有产出的节上逐项相等：账本（事前记账，失败也占一次）"
                 "/ 客户端侧记录 / 各节计量。本节抛错时**不存在**第三个视图，它的调用按显式桶"
                 "（`meter_absent_sections`）进入守恒式——「无产出」不得被读成「没发过请求」。"
                 "**研究轴是第四个视图**（`reconciliation.research`），按 `topic:<id>` 逐 topic "
                 "归集，它与写作轴的账**互不消耗**：`narrator.calls` / `entailment.calls` / "
                 "`sentence.calls` / `llm_calls` 里根本没有研究调用，拿它们对含研究尝试的整本账"
                 "会得到一个**假**的差额。半事务反例探针在预算门之外（替身、不发真实请求），"
                 "因此不进这本账。"),
    }


def _research_client_of(state) -> object:
    """本轮研究侧 LLM 句柄（`state.inputs.research_llm`）。

    取不到就**如实返回 `None`**，不冒充一个空客户端：`truncation_policy` 会据此把研究轴标成
    「没拿到句柄」（真实模式下 A7 另有一条判据记红），而不是把「没数过」写成「0 次截断」——
    「未执行不得记为零命中」。
    """
    return getattr(getattr(state, "inputs", None), "research_llm", None)


#: 写作轴截断的**谱系判读**词表（A7 的判据输入）。三者互斥，且**都不等于**「该节有没有
#: `SectionDraft`」——真实 run r4 的现场正是「两批被截断、各被缩批答回、该节此后因别的拒绝
#: 类型失败」，按「有没有 Draft」判会把这两次截断全部记成未恢复，读回的人于是把一个**已经
#: 恢复**的截断当成结局缺口去找补。
#:
#:   * `recovered_by_shrink`：产物里的批次谱系**证明**这次截断被更小的请求答回来了
#:     （沿 `parent_batch_id` 走到子树叶子，全部是成功返回，没有一条没有子孙的截断叶）。
#:   * `ended_in_lineage`：谱系**证明**这次截断走到了尽头——子树里存在一条**没有子孙的截断叶**
#:     （缩至单 aspect 仍被截断 / 缩批额度用尽），或子树里没有任何一次成功返回。它进入了结局。
#:   * `unverifiable`：产物里查不到这次截断的可判读谱系（例如被采信的那一轮根本不进拒绝审计，
#:     或载荷早于 `parent_batch_id` 且**按下面的重建规则也定不下来**）。「查不到」既不是恢复也
#:     不是未恢复，**不得**被读成任一者：`truncation_policy` 把它按「该节有没有产出」分成两桶
#:     分列，并写明各自靠的是哪一份证据。
TRUNCATION_LINEAGE_OUTCOMES = ("recovered_by_shrink", "ended_in_lineage", "unverifiable")

#: 谱系**依据**的词表（逐条读数里带上它，读者才知道这条结论建立在哪一份证据上）：
#:
#:   * `recorded_parent`：批记录自带 `parent_batch_id`（`proposal-set-rejections/5` 起）——
#:     身份是**记下来的**。
#:   * `reconstructed_by_label`：载荷早于 `/5`，没有那一列；此时父子关系**唯一地**由同一轮里
#:     已有的三样读数重建：`label` 的家族前缀（`WriterPlanBatch.label()` 由 `index/total` 与
#:     `shrink_depth` 唯一决定）、`shrink_depth` 的层级相邻、以及 `aspect_ids` 的**有序**
#:     包含—划分关系（`split_batch_for_shrink` 按 `ids[:half] / ids[half:]` 保序对半切）。
#:     重建规则是一条**全或无**的判据：本轮有一条批记录套不上，这一轮的所有截断一律
#:     `unverifiable`（见 `_reconstruct_batch_children`）。
LINEAGE_BASES = ("recorded_parent", "reconstructed_by_label")

#: 重建规则的版本号。规则一改必须换号：逐条读数里带上它，历史的判读结论才不会被新规则改写。
BATCH_LINEAGE_RECONSTRUCTION_VERSION = "lineage-by-label-1"


def _batch_family(label: str) -> str:
    """批记录 `label` 的**家族前缀**：`2/4·缩小1` 的家族是 `2/4`。

    `WriterPlanBatch.label()` 的口径是 `f"{index}/{total}"` 加（缩小层）`·缩小{depth}`，而
    缩批子批**继承**原批的 `index/total`（`split_batch_for_shrink` 只改 aspect 范围与深度），
    因此同一家族的批恰好是「同一次划分的原始批与它的全体子孙」。这不是给 label 附会含义：
    它是本仓 `label()` 的唯一实现，且下面每一条重建都要再经层级与 aspect 划分两道核对。
    """
    return str(label or "").split("·缩小")[0]


def _reconstruct_batch_children(round_batches: list[dict]) -> tuple[dict | None, str]:
    """旧载荷（无 `parent_batch_id`）里**能不能**定出父子关系：能返回 `(children, "")`。

    规则全或无，且每一条都是可核对的结构事实：

      1. 每条批记录都要有非空 `label` 与整数 `shrink_depth`（缺任一 ⇒ 不可核验）；
      2. 深度 > 0 的批，其父批必须**恰好一个**：同家族（同 `label` 前缀）、深度恰少一层、
         且 `aspect_ids` 是父批的**保序子序列**。0 个候选或 ≥2 个候选都不可核验——「可能
         是这个、也可能是那个」的父子关系不是谱系；
      3. 每个有子批的批，其子批按记录顺序拼起来必须**逐字等于**它自己的 `aspect_ids`：这正是
         对半切（`ids[:half] / ids[half:]`）留下的痕迹。拼不上就说明这些批不是它的子批。

    返回 `(None, 原因)` 时调用方一律按 `unverifiable` 处理：**「定不下来」不等于「没有子批」**
    ——后者会被读成「缩批走到了尽头」，那是一个**红**的结论。
    """
    parents: dict[str, list[dict]] = {}
    for batch in round_batches:
        label = str(batch.get("label") or "")
        depth = batch.get("shrink_depth")
        if not label or not isinstance(depth, int) or depth < 0:
            return None, (f"批记录 `{batch.get('batch_id')!r}` 缺可判读的 `label` / "
                          "`shrink_depth`：无法重建父子关系")
        if depth == 0:
            continue
        candidates = [other for other in round_batches
                      if other is not batch
                      and _batch_family(other.get("label")) == _batch_family(label)
                      and other.get("shrink_depth") == depth - 1
                      and _ordered_subsequence(batch.get("aspect_ids"),
                                               other.get("aspect_ids"))]
        if len(candidates) != 1:
            return None, (f"批记录 `{batch.get('batch_id')!r}`（{label}）的父批有 "
                          f"{len(candidates)} 个候选（同家族、深度少一层、aspect 保序包含）："
                          + ("**唯一**的父批都不存在" if not candidates
                             else "父子关系不唯一，不能猜是哪一个"))
        parents.setdefault(str(candidates[0].get("batch_id") or ""), []).append(batch)
    for parent_id, kids in parents.items():
        parent = next(b for b in round_batches
                      if str(b.get("batch_id") or "") == parent_id)
        joined = [aspect for kid in kids for aspect in (kid.get("aspect_ids") or ())]
        if joined != list(parent.get("aspect_ids") or ()):
            return None, (f"批记录 `{parent_id}` 的子批 aspect 拼起来与它自己不等"
                          f"（父 {list(parent.get('aspect_ids') or ())} vs 子 {joined}）："
                          "这些批不是它的对半切结果")
    return {parent_id: kids for parent_id, kids in parents.items()}, ""


def _ordered_subsequence(inner, outer) -> bool:
    """`inner` 是否是 `outer` 的**保序**子序列（对半切保留原顺序，因此必须保序）。"""
    inner_list = list(inner or ())
    outer_list = list(outer or ())
    if not inner_list:
        return bool(outer_list)
    position = 0
    for item in outer_list:
        if position < len(inner_list) and item == inner_list[position]:
            position += 1
    return position == len(inner_list)



def _truncation_lineage(rejections: dict | None, section_id: str,
                        call_id: str | None) -> dict:
    """从一次**被截断的写作调用**出发，按批次谱系判它后来有没有被缩批答回来。

    判据全部是**身份**，没有一处用到「该节最终有没有 `SectionDraft`」：

      1. **锚点**：被截断那一次调用的 provider `call_id`（账本里记的就是它），在
         `proposal_set_rejections[section_id][*].batches[]` 里定位到**那一条**批记录；
      2. **链路**：以该批为根，逐层收它的子孙（只在**同一轮**的批记录内，不跨轮——同一个
         `batch_id` 会在不同轮里各出现一次，跨轮连边会把两轮拼成假谱系）。父子关系有两档来源，
         逐条读数里的 `basis` 说明这一条用的是哪一档：`recorded_parent`（批记录自带
         `parent_batch_id`，`/5` 起）或 `reconstructed_by_label`（旧载荷按 label/深度/aspect
         划分重建，见 `_reconstruct_batch_children`——这是一档**有前提**的依据，前提不成立时
         返回 `unverifiable` 而不是猜一个父子关系）；
      3. **结论**：子树里**存在没有子孙的截断叶** ⇒ 缩批已走到尽头（单 aspect 不可再切、或额度
         用尽），这次截断进入了结局；子树里每一次重问都成功返回 ⇒ 它被答回来了。

    「查不到」必须与「确实没恢复」分开：被采信的那一轮不进拒绝审计（那一轮没有拒绝记录），
    它里面若发生过截断，产物侧就没有批记录可判——那只说明**谱系不可核验**。
    """
    rows = list((rejections or {}).get(section_id, ()) or ())
    record = None
    anchor = None
    for candidate_record in rows:
        for batch in (candidate_record.get("batches") or ()):
            if call_id and batch.get("call_id") == call_id:
                record, anchor = candidate_record, batch
                break
        if anchor is not None:
            break
    if anchor is None:
        return {"outcome": "unverifiable", "batch_id": None, "lineage": [], "basis": None,
                "detail": "产物里没有一条批记录的 provider `call_id` 等于这次被截断调用的 "
                          "`call_id`：这次截断要么发生在**被采信的那一轮**（那一轮不进拒绝审计），"
                          "要么发生在产物记录之外"}
    batch_id = str(anchor.get("batch_id") or "")
    if str(anchor.get("status") or "") != "truncated":
        return {"outcome": "unverifiable", "batch_id": batch_id, "lineage": [], "basis": None,
                "detail": f"拒绝审计里那次调用的批记录自报 `status={anchor.get('status')!r}`"
                          "（不是截断）：账本与批记录对不上，谱系不可核验"}
    round_batches = list(record.get("batches") or ())
    if any("parent_batch_id" not in batch for batch in round_batches):
        # 载荷早于 `/5`：父子关系**没有**记在产物里。但它并非无从判定——同一轮里还留着
        # `label`（家族 + 深度）、`shrink_depth`、以及保序的 `aspect_ids`，而对半切在这三样上
        # 留下唯一可核对的痕迹。重建规则**全或无**：这一轮有一条套不上，就整轮不可核验。
        children, reason = _reconstruct_batch_children(round_batches)
        if children is None:
            return {"outcome": "unverifiable", "batch_id": batch_id, "lineage": [],
                    "basis": None,
                    "detail": (f"本次拒绝记录的载荷没有 `parent_batch_id`（"
                               f"`proposal-set-rejections/4` 及更早），按 label/深度/aspect "
                               f"划分的规则（{BATCH_LINEAGE_RECONSTRUCTION_VERSION}）重建父子关系"
                               f"失败：{reason}。谱系**不可核验**——「定不下来」既不等于恢复，"
                               "也不等于没恢复")}
        basis = "reconstructed_by_label"
    else:
        children = {}
        for batch in round_batches:
            children.setdefault(str(batch.get("parent_batch_id") or ""), []).append(batch)
        basis = "recorded_parent"
    stack: list[dict] = [anchor]
    seen: set[str] = set()
    leaves: list[dict] = []
    while stack:
        node = stack.pop()
        node_id = str(node.get("batch_id") or "")
        if node_id in seen:
            continue
        seen.add(node_id)
        kids = children.get(node_id, [])
        if kids:
            stack.extend(kids)
        else:
            leaves.append(node)
    lineage = [{"batch_id": str(batch.get("batch_id") or ""),
                "label": str(batch.get("label") or ""),
                "status": str(batch.get("status") or ""),
                "shrink_depth": int(batch.get("shrink_depth") or 0)}
               for batch in round_batches if str(batch.get("batch_id") or "") in seen]
    truncated_leaves = [b for b in leaves if str(b.get("status") or "") == "truncated"]
    ok_leaves = [b for b in leaves if str(b.get("status") or "") == "ok"]
    if truncated_leaves or not ok_leaves:
        ended = ([f"缩批子树里有 {len(truncated_leaves)} 条**没有子孙的截断叶**"
                  f"（{sorted(str(b.get('label') or '') for b in truncated_leaves)}）"]
                 if truncated_leaves else
                 [f"缩批子树里没有任何一次成功返回（{len(leaves)} 条叶子全非 `ok`）"])
        return {"outcome": "ended_in_lineage", "batch_id": batch_id, "lineage": lineage,
                "basis": basis,
                "detail": "、".join(ended) + "：这次截断走到了尽头"}
    return {"outcome": "recovered_by_shrink", "batch_id": batch_id, "lineage": lineage,
            "basis": basis,
            "detail": (f"缩批子树共 {len(seen)} 批、{len(ok_leaves)} 条成功返回的叶子、"
                       "没有无子孙的截断叶：这次截断被更小的请求答回来了")}


def truncation_policy(state: RunState, *, narrator, entailment, sentence=None,
                      research=None) -> dict:
    """截断处置的现场记录：**两条轴**各自出现过几次被截断的调用、以及处置口径。

    判据不是「相信没人截断」，而是**数出来**，并且按**最终结局**分类：

      * **写作轴**（`narrator` / `entailment` / `sentence`）：适配器 `reject_truncated=True`，
        截断 = 这次
        调用失败（账本按 `error` 落账），该批结果**不解析、不采纳**。法律允许的唯一恢复路径是
        **有界**的确定性缩批重问（每轮至多 `MAX_SWEEP_SHRINK_STEPS` 次对半拆分，切分点由
        Contract aspect 顺序唯一决定）。因此写作轴的截断要逐条判**它自己**有没有被答回：
        判据是**批次谱系**（见 `_truncation_lineage`），**不是**该节最终有没有 `SectionDraft`。
        谱系有两档依据，逐条记在 `lineage_basis` 里（`recorded_parent` / `reconstructed_by_label`）。
        真实 run r4 的现场说明了为什么必须这样判：那两批截断各被两个缩批子请求答回（子树叶子
        全部 `ok`），该节此后因**别的**拒绝类型失败、没有 `SectionDraft`——按「有没有 Draft」
        判会把两次**已经恢复**的截断记成结局缺口。四桶：`recovered`（谱系证明已答回且该节
        产出）、`recovered_downstream_failed`（谱系证明已答回、该节此后因其它原因失败）、
        `recovered_unverified`（谱系不可核验、只以该节产出为证）三者都不为红；
        `unrecovered`（谱系证明走到了尽头，或谱系不可核验**且**该节没有产出）为红。
      * **研究轴**（`research`）：生产适配器 `RealResearchLLM` 同样 `reject_truncated=True`
        （前置修复 3），截断在**调用返回之前**抛出，三个解析入口（动作 / 答案 / 蕴含）都拿不到
        半截正文——「截断内容一律不解析、不采用」在研究轴上自此与写作轴同口径。研究链**没有**
        缩批重问这一层，因此被截断的研究调用就是该次调用失败、该问如实停在既有 typed 终态
        （`MODEL_OUTPUT_INVALID`），`research_fail_closed` 逐条列出来。
        本函数仍**分开**列 `research_unrecovered`：任一句柄自报 `reject_truncated=False`
        （例如离线替身或将来某个新适配器）时，残缺正文会被返回并继续被解析——那是该轴上的
        缺口，A7 据此判红。**两档由句柄自己的属性分辨，不由本函数猜。**

    离线替身不报 provider 参数，因此这些数在离线模式下都是 0（`applicable=False`），
    不是「没统计」。
    """
    from llm import budget as LB
    from llm import client as LLC

    ledger = state.budget
    attempted = [] if ledger is None else [
        a for a in ledger.attempts if a.status == "error" and "truncated" in str(a.error)]
    writing_attempts = [a for a in attempted
                        if getattr(a, "axis", LB.AXIS_WRITING) == LB.AXIS_WRITING]
    research_attempts = [a for a in attempted
                         if getattr(a, "axis", LB.AXIS_WRITING) != LB.AXIS_WRITING]

    client_rows: list[dict] = []
    for name, axis, client in (("narrator", "writing", narrator),
                               ("entailment", "writing", entailment),
                               ("sentence", "writing", sentence),
                               ("research", "research", research)):
        if client is None:
            continue
        fail_closed = bool(getattr(client, "reject_truncated", False))
        for call in getattr(client, "calls", ()) or ():
            if str(call.get("finish_reason")) in LLC.TRUNCATION_STOP_REASONS:
                client_rows.append({"client": name, "axis": axis,
                                    "call_id": call.get("call_id"),
                                    "prompt_version": call.get("prompt_version"),
                                    "finish_reason": call.get("finish_reason"),
                                    "output_tokens": call.get("output_tokens"),
                                    # 这一条读数决定该次截断是否**已经是最终结局**：适配器拒绝
                                    # 截断 ⇒ 它成了一次失败的调用（可被缩批救回）；适配器不拒绝
                                    # ⇒ 残缺正文被返回并继续被解析。
                                    "reject_truncated": fail_closed,
                                    "call_kind": str(call.get("kind") or "")})

    section_errors = getattr(state, "section_errors", None) or {}
    rejections = getattr(state, "proposal_set_rejections", None) or {}

    def _has_draft(section_id: str) -> bool:
        return getattr(state.sections.get(section_id), "draft", None) is not None

    def _typed_batch_truncation(section_id: str) -> bool:
        """该节的失败**被记名**为截断：错误串或 typed 拒绝记录里带 `batch_truncated`。"""
        if "batch_truncated" in str(section_errors.get(section_id, "")):
            return True
        return any(str(rec.get("rejection_kind")) == "batch_truncated"
                   for rec in (rejections.get(section_id, ()) or ()))

    recovered: list[dict] = []
    recovered_downstream_failed: list[dict] = []
    recovered_unverified: list[dict] = []
    unrecovered: list[dict] = []
    for a in writing_attempts:
        section_id = str(getattr(a, "section_id", "") or "")
        lineage = _truncation_lineage(rejections, section_id, a.call_id)
        has_draft = _has_draft(section_id)
        row = {"section_id": section_id, "call_id": a.call_id,
               "prompt_version": a.prompt_version, "error": str(a.error),
               "lineage_outcome": lineage["outcome"],
               "lineage_basis": lineage["basis"],
               "lineage_batch_id": lineage["batch_id"],
               "lineage": lineage["lineage"],
               "lineage_detail": lineage["detail"],
               "typed_batch_truncation": _typed_batch_truncation(section_id)}
        if lineage["outcome"] == "recovered_by_shrink":
            # 谱系证明截断被缩批答回 ⇒ **不进入结局**，因此不为红。该节此后成不成，
            # 由别的门负责：把「截断已恢复」与「这一节成功了」当同一件事，正是本批要拆开的。
            if has_draft:
                row["why"] = (f"批次谱系证明这次截断被有界缩批重问答回（{lineage['detail']}）；"
                              "该节最终也产出了 `SectionDraft`，截断未进入结局")
                recovered.append(row)
            else:
                row["why"] = (f"批次谱系证明这次截断被有界缩批重问答回（{lineage['detail']}）；"
                              "该节此后因**其它原因**失败（截断本身没有进入结局）——该节失败"
                              "由别的判据负责，不在这里再记一次截断缺口")
                recovered_downstream_failed.append(row)
        elif lineage["outcome"] == "unverifiable" and has_draft:
            # 谱系查不到、但该节确实产出了：**只以该节的产出为证**，不冒充谱系证明。
            row["why"] = (f"批次谱系**不可核验**（{lineage['detail']}）；该节最终产出了 "
                          "`SectionDraft`——这一条只以该节产出为证，不是谱系证明，也不为红")
            recovered_unverified.append(row)
        else:
            row["why"] = ("这次截断未被判为恢复"
                          + ("：批次谱系证明它走到了尽头" if lineage["outcome"] == "ended_in_lineage"
                             else "：批次谱系不可核验且该节没有产出（按 fail-closed 计为未恢复）")
                          + f"（{lineage['detail']}）"
                          + ("，且失败被 typed `batch_truncated` 记名"
                             if _typed_batch_truncation(section_id) else
                             "，产物里没有 `batch_truncated` 记名"))
            unrecovered.append(row)
    research_unrecovered = [row for row in client_rows
                            if row["axis"] == "research" and not row["reject_truncated"]]
    research_fail_closed = [row for row in client_rows
                            if row["axis"] == "research" and row["reject_truncated"]]
    return {
        "stop_reasons": list(LLC.TRUNCATION_STOP_REASONS),
        "policy": (
            "截断内容**一律不解析、不采用**。写作轴上 `reject_truncated=True`：截断 = 这次调用"
            "失败，账本按 `error` 落账，该批结果整批丢弃；**唯一合法恢复路径**是**有界**的确定性"
            "缩批重问（每轮至多 `MAX_SWEEP_SHRINK_STEPS` 次对半拆分，切分点由 Contract aspect "
            "顺序唯一决定），**缩至单个 aspect 仍被截断、或缩批额度用尽，即 fail-closed**"
            "（该节 typed `batch_truncated`，该节没有产出）——不自动重跑、不换模型、不换 prompt、"
            "不留半截正文。研究轴**同样拒收截断**（`RealResearchLLM.reject_truncated=True`，门设在"
            "调用返回之前），但**没有**缩批这一层：被截断的研究调用即该次调用失败，该问如实停在"
            "既有 typed 终态，不解析、不采纳；若某个句柄自报 `reject_truncated=False`，残缺正文"
            "会被返回并被继续解析，那是该轴上的缺口，如实计入 `research_unrecovered`。"),
        "applicable": state.mode == MODE_REAL,
        # 研究侧句柄取不到时，下面两个研究轴读数**不是**「0 次截断」，而是「没数过」。
        "research_client_available": research is not None,
        "research_axis_note": (None if research is not None else
                               "未拿到研究侧 LLM 句柄：研究轴的截断**没数过**（不是 0 次）——"
                               "不得把缺失读成干净"),
        "truncated_attempts": len(writing_attempts),
        "truncated_attempts_research_axis": len(research_attempts),
        # 「是否出现过截断」与「截断是否进入结局」是两个读数：前者只作现场，判据用后者。
        "truncated_calls": client_rows,
        "recovered": recovered,
        "recovered_downstream_failed": recovered_downstream_failed,
        "recovered_unverified": recovered_unverified,
        "unrecovered": unrecovered,
        "research_unrecovered": research_unrecovered,
        "research_fail_closed": research_fail_closed,
        "lineage_outcomes": list(TRUNCATION_LINEAGE_OUTCOMES),
        "lineage_bases": list(LINEAGE_BASES),
        "lineage_reconstruction_version": BATCH_LINEAGE_RECONSTRUCTION_VERSION,
        "note": ("A7 判的是**最终结局**，不是「有没有出现过截断」；而「最终结局」的判据是**批次"
                 "谱系**（被截断那一次调用的 `call_id` → 拒绝审计里的那条批记录 → 沿父子链到它的"
                 "缩批子树），**不是**该节最终有没有 `SectionDraft`。"
                 "父子链的来源逐条记在 `lineage_basis` 里：`recorded_parent`（批记录自带 "
                 "`parent_batch_id`）或 `reconstructed_by_label`（旧载荷没有那一列时，按 label "
                 "家族、`shrink_depth` 层级与 `aspect_ids` 的保序划分重建"
                 f"（{BATCH_LINEAGE_RECONSTRUCTION_VERSION}）；重建规则全或无，套不上即整轮"
                 "`unverifiable`，不猜）。"
                 "四桶分列：`recovered`（谱系证明被缩批答回、该节也产出了）、"
                 "`recovered_downstream_failed`（谱系证明被缩批答回、该节此后因**其它原因**失败）"
                 "与 `recovered_unverified`（谱系不可核验、只以该节产出为证）都**不**为红；"
                 "为红的是 `unrecovered`（谱系证明走到了尽头，或谱系不可核验且该节没有产出——"
                 "按 fail-closed 计）与 `research_unrecovered`（该句柄不拒收截断，残缺正文已被"
                 "返回并继续解析）。研究轴上被拒收的截断列在 `research_fail_closed` 里，**不**为红"
                 "——它没有进入任何内容。写作轴的截断同时受账本与客户端记录两处核对。"),
    }


def _probe_result_for_report(probe: dict | None) -> dict:
    """把半事务探针结果投影成可落盘形态。

    探针返回里的 `db` 是**内部句柄**（`Path`），不是报告内容：写进 JSON 会在 `dumps` 时炸掉
    整个 run（本轮实测踩到过）。只留它的文件名——报告要能指认现场，但报告不是句柄的容器。
    """
    out: dict = {}
    for key, value in dict(probe or {}).items():
        out[key] = value.name if isinstance(value, Path) else value
    return out


def _gate_a7_call_budget(state: RunState, *, narrator, entailment, sentence=None,
                         mode: str,
                         research=None,
                         run_dir: Path) -> GateOutcome:
    """A7：调用预算门与账本（事前拦截 + 三个计数视图逐项守恒）。"""
    from llm import budget as LB

    problems: list[str] = []
    ledger = state.budget
    reconciliation = _reconcile_calls(state, narrator=narrator, entailment=entailment,
                                      sentence=sentence)
    problems.extend(reconciliation["problems"])
    log_reconciliation: dict = {"applicable": False,
                                "why": "离线模式：确定性替身不写 logs/llm，无从对账"}
    if ledger is None:
        problems.append("本轮没有安装调用预算门：真实调用数不受事前上限约束")
    else:
        # 「不属于任何一节」只对**写作轴**成立：研究轴的作用域是 `topic:<id>`，它本来就不该
        # 落在任何一节名下。用整本账判 stray 会把正确的账读成错误（`_reconcile_calls` 里
        # 同一条判据也只写作轴）。
        stray = sorted({a.section_id for a in ledger.attempts
                        if a.axis == LB.AXIS_WRITING and a.section_id not in SECTION_ORDER})
        if stray:
            problems.append(f"账本里有不归属于任何一节的**写作**尝试：{stray}")
        ids = [a.call_id for a in ledger.attempts]
        if len(set(ids)) != len(ids):
            problems.append("账本里出现重复 call_id：同一次请求被记了两次")
        if ledger.refusals:
            problems.append(
                f"账本里有 {len(ledger.refusals)} 次**事前拒绝**，但本轮跑完了："
                "被拒的请求不得计入已发出的调用，也不得在不停止的情况下被忽略")
        if mode == MODE_REAL:
            log_reconciliation = _reconcile_with_llm_logs(ledger)
            log_reconciliation["applicable"] = True
            problems.extend(log_reconciliation["problems"])
    truncation = truncation_policy(state, narrator=narrator, entailment=entailment,
                                   sentence=sentence, research=research)
    writing_unrecovered = truncation["unrecovered"]
    research_unrecovered = truncation["research_unrecovered"]
    if mode == MODE_REAL and (writing_unrecovered or research_unrecovered):
        # **判最终结局，不判「有没有出现过截断」**：写作轴上被有界缩批重问答回的截断逐条列在
        # `recovered*` 三桶里，不为红；为红的是截断**进入了结局**的那几类。
        #
        # 两条轴的句子**各自按实际读数成形**，不写死任何一边。本批修的正是「研究轴 0 次却照印
        # 『该句柄自报 `reject_truncated=False` ⇒ 残缺正文已被返回并继续解析』」——那是一句在
        # 写作轴单独判红时必然为假的反话（真实 run r4 的现场：写作轴 2 次未恢复、研究轴 0 次，
        # 红线条件成立，报告里于是出现了一句与研究侧事实相反的话）。
        clauses: list[str] = []
        if writing_unrecovered:
            bases = _count_by(writing_unrecovered, "lineage_outcome")
            clauses.append(
                f"写作轴未被恢复 {len(writing_unrecovered)} 次"
                f"（节：{[row['section_id'] for row in writing_unrecovered]}；逐条谱系判读 "
                f"{bases}——`ended_in_lineage` = 谱系证明这次截断走到了尽头，`unverifiable` = "
                "产物里查不到这次截断的批次谱系且该节没有产出，按 fail-closed 计为未恢复）")
        if research_unrecovered:
            # 这一句只在**真有**自报不拒收的句柄时出现，并逐条点名是哪个客户端。
            clients = sorted({str(row.get("client") or "?") for row in research_unrecovered})
            clauses.append(
                f"研究轴 {len(research_unrecovered)} 次（客户端 {clients} 自报 "
                "`reject_truncated=False` ⇒ 残缺正文已被返回并继续解析；研究链没有缩批恢复"
                f"路径。生产适配器落在 `research_fail_closed` 那一档、本轮实得 "
                f"{len(truncation['research_fail_closed'])} 次，**不为红**）")
        else:
            # 研究轴**没有**未恢复的截断：不说一句「自报不拒收」的反话，只报实际读数。
            clauses.append(
                f"研究轴**没有**未恢复的截断（自报拒收 `reject_truncated=True` 的截断 "
                f"{len(truncation['research_fail_closed'])} 次，残缺正文在调用返回之前就被拒收，"
                "未进入任何解析路径，因此不为红）")
        if truncation["recovered"] or truncation["recovered_downstream_failed"]:
            answered = truncation["recovered"] + truncation["recovered_downstream_failed"]
            bases = _count_by(answered, "lineage_basis")
            clauses.append(
                f"被有界缩批**答回**的另有 {len(truncation['recovered'])} 次（该节也产出了）"
                f"+ {len(truncation['recovered_downstream_failed'])} 次（该节此后因其它原因失败），"
                f"逐条谱系依据 {bases}（`recorded_parent` = 批记录自带 `parent_batch_id`；"
                "`reconstructed_by_label` = 旧载荷没有那一列、按 label 家族/深度/aspect 保序划分"
                "重建，规则见 `truncation_policy.lineage_reconstruction_version`），"
                "逐条列在 `truncation_policy.recovered` / "
                "`truncation_policy.recovered_downstream_failed` 里，不为红")
        if truncation["recovered_unverified"]:
            clauses.append(
                f"另有 {len(truncation['recovered_unverified'])} 次截断的批次谱系**不可核验**"
                "（只以「该节产出了 `SectionDraft`」为证，不是谱系证明），同样不为红、单独列出")
        problems.append("截断进入了最终结局：" + "；".join(clauses))
    # 「研究轴无从计数」只在**这一轮真的有 `inputs`** 时记红：判据是「本轮的研究侧句柄丢了」，
    # 不是「这个只读现场没有那个字段」——最小只读夹具（只给账本与客户端）不该因此变红，而那
    # 种情形产物里已经由 `research_client_available=False` 如实标出「没数过」。
    if mode == MODE_REAL and research is None and getattr(state, "inputs", None) is not None:
        problems.append("真实模式没有拿到研究侧 LLM 句柄：研究轴的截断无从计数，"
                        "「两条轴都在可见面内」这句话本轮不成立")
    evidence = {"reconciliation": reconciliation, "llm_log_reconciliation": log_reconciliation,
                "budget_policy": None if ledger is None else ledger.policy.to_dict(),
                "output_capacity": output_capacity_policy(),
                "truncation_policy": truncation,
                "enforced": None if ledger is None else bool(ledger.enforce),
                "refusals": [] if ledger is None else list(ledger.refusals),
                "rollback_probe": {
                    "scope": ROLLBACK_PROBE_SCOPE,
                    "note": ("半事务反例探针用确定性替身把三节再驱动一遍，且**在预算门之外**："
                             "替身不发真实请求，若让它占账，真实模式会平白掐掉一次真运行、"
                             "离线模式会把每节的账翻倍。这一段不在门内是**显式声明**，"
                             "不是被漏掉的调用。"),
                    "probe_result": _probe_result_for_report(state.rollback)},
                "counting_views": {
                    "ledger": "每次尝试（事前记账，失败也占一次）",
                    "client": "narrator.calls / entailment.calls（真实模式适配器同样记录）",
                    "meter": "BackboneSectionWriterOutput.llm_calls（门前提案 + 蕴含 + 重写 + "
                             "门后组织 + 可选章级评估）",
                    "meter_absent": ("本节抛错时第三个视图**不存在**（没有 "
                                     "`BackboneSectionWriterOutput`）：该节的调用按显式桶"
                                     "`reconciliation.meter_absent_sections` 进入守恒式，"
                                     "「无产出」不等于「没发过请求」"),
                    "research": ("**第四个视图**，研究轴专用：账本里 `axis == 'research'` 的"
                                 "尝试按 `topic:<id>` 逐 topic 归集（`reconciliation.research`）。"
                                 "上面三个视图都是写作轴的，不含研究调用")},
                "note": ("判据是四条守恒：① 整本账恰好分完两条轴 "
                         "（`ledger_total == writing_axis_total + research_axis_total`）；"
                         "② 写作轴：账本 ↔ 客户端记录（按类别，另加单向的「成功返回」判据）、"
                         "账本 ↔ 各节计量（按节）；"
                         "③ 研究轴：`research_axis_total == 逐 topic 之和`，且观测值不超过已批准"
                         "上限；"
                         "④ 真实模式下账本里每一次『成功』都能在 logs/llm 里按 call_id 找到记录。"
                         "**第二条只对有产出的节成立**：失败节没有 `BackboneSectionWriterOutput`，"
                         "它的调用进 `meter_absent_sections` 并计入写作轴守恒式 "
                         "`writing_axis_total == meter_sum + attempts_unmetered + attempts_unattributed`"
                         "（残差必须为 0）。"
                         "两轴互不消耗：研究调用**不**进写作轴的整轮 `total_max_attempts`，"
                         "写作调用也不进研究轴的上限。"
                         "历史 run（acc-5 及更早）的『各节之和』不含门后组织，因此系统性少算；"
                         "差额等于门后组织的**尝试次数**（不是节数：一个节可走两轮组织）——"
                         "本次实测见 reconciliation.organizer_attempts。")}
    return _gate("A7", "调用预算门：事前上限 + 两条预算轴各自守恒", problems, evidence)


def _run_gates(state: RunState, run_dir: Path, *, narrator=None,
               entailment=None, sentence=None) -> list[GateOutcome]:
    """七个验收项（`run_dir` 只在需要复现现场时用）。"""
    gates: list[GateOutcome] = []
    company = state.sections.get("company")
    financial = state.sections.get("financial")
    industry = state.sections.get("industry")
    gates.append(
        _gate_a1_company(company, state.authority_of("company"),
                         state.inputs.tasks["company"], state.inputs.resolver)
        if company is not None else
        _gate("A1", "公司节：真实材料正文 → 合法路径 B Claim + 多 Claim 自然句",
              [f"公司节未产出：{state.section_errors.get('company', '未运行')}"], {}))
    gates.append(
        _gate_a2_financial(financial, state.authority_of("financial"),
                           state.inputs.tasks["financial"])
        if financial is not None else
        _gate("A2", "财务节：同一条主链上的路径 A 事实 + 可读正文 + 数字零计算",
              [f"财务节未产出：{state.section_errors.get('financial', '未运行')}"], {}))
    gates.append(
        _gate_a3_industry(industry, state.inputs.tasks["industry"])
        if industry is not None else
        _gate("A3", "行业节：有合法材料就写，否则 typed 缺口（不伪造覆盖）",
              [f"行业节未产出：{state.section_errors.get('industry', '未运行')}"], {}))
    if state.sections:
        gates.append(_gate_a4_persistence(run_dir / "section_chain_v2.db", state))
    else:
        gates.append(_gate("A4", "持久化：完整 v2 链 + 独立读回重组 + 失败不留半事务",
                           ["没有任何一节产出，落库无从核验"], {}))
    gates.append(_gate_a5_assembly(state))
    # A7 必须拿到**真实用过的**客户端句柄：客户端侧记录（`narrator.calls` /
    # `entailment.calls` / `sentence.calls`）是账本之外唯一能独立对照的视图，拿不到就等于少一个视角。
    gates.append(_gate_a7_call_budget(state, narrator=narrator, entailment=entailment,
                                      sentence=sentence,
                                      mode=state.mode, run_dir=run_dir,
                                      # 研究轴要与写作轴一样进 A7 的可见面：截断读数要能按
                                      # 「哪条轴」分开看，而研究侧的调用记录只在研究 LLM 上。
                                      research=_research_client_of(state)))
    return gates


def _writer_policy(*, mode: str, model: str | None):
    """写作策略：离线 = stub；真实 = 已批准的 provider 默认模型、**有界**一次修正机会。

    `max_llm_retries` 由 0 改为 **1**（本批的显式变更，逐条依据如下）：

      * `WriterPolicy` 自带的取值域就是 `{0, 1}`，注释写明「§四每节最多重试一次」——1 是该
        冻结接口**设计上允许**的最大值，不是新开的口子；
      * 取 0 的实际后果不是「更严格」，而是**一个垃圾候选毁掉整节**：第一次输出里只要有任一
        路径 B 候选命中高风险表面，整束即被拒，节内再没有第二次机会，公司节因此零产出
        （M930-3 的根本业务失败）；
      * 重试**不是**「删掉几个候选再送门」：整束被丢弃，模型必须重新输出**完整** JSON，得到
        一束**新的、完整的**提案集合，再原样过同一套门（路径 B 高风险面判据 + 叙事硬门 +
        聚合绑定门）。被拒的那一束不会被原地裁剪，而是留下 typed 审计（见
        `PW.ProposalSetRejectionRecord`）；
      * 这条变更由 2026-09-23 批次 A 补充裁决授权：「允许为内容质量增加有界 LLM 调用；限制
        只用于防循环、可追踪和故障停止，不以费用压缩材料或文本」。

    调用上限**不在**这里：唯一一处定义是 `_call_budget_policy()`（按类别，命令行抬不高）。
    把「已批准的写作预算」写进策略对象，正是旧口径把「写作预算」当成「全链总预算」的来源。
    """
    from config import LLM_MODEL
    from sections import pack_writer as PW

    if mode == MODE_OFFLINE:
        return PW.WriterPolicy(model_policy=PW.MODEL_POLICY_STUB,
                               max_llm_retries=APPROVED_WRITER_MAX_LLM_RETRIES)
    if model is not None and model != str(LLM_MODEL):
        raise AcceptanceRefusal(
            f"--model={model!r} 与已批准的模型 {LLM_MODEL!r} 不同："
            "本次授权只覆盖已批准模型，不得自行更换（fail-closed）")
    #: 报告里的 `approved_model` 是**常量** `APPROVED_BUDGET_MODEL`，而真实调用取的是
    #: `config.LLM_MODEL`（研究侧 `RT.RealResearchLLM()` 与叙述侧都走这个缺省）。若环境变量把
    #: `LLM_MODEL` 改成别的值，报告会**声称**用了已批准模型、实际却用了另一个 —— 那是一条
    #: 无法从产物里看出来的假声明。因此这里把「声称的」与「要用的」当场对齐，不一致即整轮拒绝
    #: （真实模式专用；离线模式不发起任何真实调用，不受此门约束）。
    if str(LLM_MODEL) != APPROVED_BUDGET_MODEL:
        raise AcceptanceRefusal(
            f"config.LLM_MODEL={str(LLM_MODEL)!r} 与报告的 approved_model "
            f"{APPROVED_BUDGET_MODEL!r} 不同：报告会声称用了已批准模型而实际不是"
            "（fail-closed；请把 LLM_MODEL 恢复为已批准模型再跑真实模式）")
    return PW.WriterPolicy(model_policy=PW.MODEL_POLICY_PROVIDER_DEFAULT,
                           max_llm_retries=APPROVED_WRITER_MAX_LLM_RETRIES)


#: ── 研究侧上限的**推导常量**（不是另定的数字，`CROSS_DOCUMENT_JOINT_RETRIEVAL_PLAN.md` §8.11）──
#:
#: acc-20 起，研究侧上限**逐 topic 自派生**：`每 aspect 上界 × 该 topic 自己的 aspect 数`。
#: 旧口径是**一个共享值** `3 × len(aspects(topic_ids[0]))`，六个 topic 共用——它给 4 个 aspect 的
#: `industry_scale_cycle` 9 次/aspect，却给 18 个 aspect 的 `company_business` 2 次/aspect。
#: 那不是上限，是按 topic 大小分配的配额，且**已经在真实读数上出过事**：a7 里
#: `company_legal_risks` 用了 37、`industry_scale_cycle` 用了 40，双双越过共享的 36。
#:
#: 这几个常量是**从既有代码常量推出**每 aspect 上界的过程，逐项都能回指：
#:   * `harness/topic_runtime.py::BudgetForNeed.build()` 把内层循环的 `max_rounds` 取自
#:     `ResearchBudgetPolicy.max_need_rounds_per_aspect`（本 runner 传 2）；
#:   * `harness/runtime.py` 的 action 循环在 `rounds >= max_rounds` 时，若仍有可引用材料会给
#:     **一次** `force_converge` 机会（`+1` 轮），每轮恰一次 `select_action`；
#:   * 收敛时 `_generate_answer` 一次（`LLM_CATEGORY_ANSWER`）、随后每条 factual candidate
#:     一次蕴含（`LLM_CATEGORY_ENTAILMENT`）——各 `+1`；
#:   * 每个 aspect 的 needs 数 ≤ 首次 1 + `max_need_rounds_per_aspect` 次追补 = 3
#:     （该字段的名字就是「每 aspect 的 need 轮数」，本 runner 传 2）。
#: ⇒ 每 need ≤ (2+1)+1+1 = 5 次，每 aspect ≤ 3 × 5 = **15 次**。
#: 这个 15 是**上界而不是估计值**：实测最坏读数是 10 次/aspect（a7 `industry_scale_cycle`
#: 4 aspect → 40 次），15 高于它，符合「上限必须盖得住结构上界」。
#: 方案 §8.11 正文写的 `3 ×` 已明显低于实测（industry 用了 8 次/aspect），照抄会把它从 36
#: **降到 12** —— 把一个合法跑出过 32 次的 topic 当场卡死，正是本文件反复写明的那条
#: 「上限低于结构上界不是更严格，而是把一次注定失败的运行伪装成可运行」。故取结构上界 15。
RESEARCH_MAX_NEED_ROUNDS_PER_ASPECT = 2
#: 内层每 need 的最大轮数：`max_rounds` 本身 + `force_converge` 给出的那一轮。
RESEARCH_MAX_ROUNDS_PER_NEED = RESEARCH_MAX_NEED_ROUNDS_PER_ASPECT + 1
#: 每 need 的 LLM 调用数：逐轮一次 action，收敛时一次 answer + 一次 entailment。
RESEARCH_LLM_CALLS_PER_NEED = RESEARCH_MAX_ROUNDS_PER_NEED + 2
#: 每 aspect 的 needs 数：首次 1 + 至多 `max_need_rounds_per_aspect` 次追补。
RESEARCH_NEEDS_PER_ASPECT = 1 + RESEARCH_MAX_NEED_ROUNDS_PER_ASPECT
#: **每 aspect 的 LLM 调用上界**（= 15）。
RESEARCH_LLM_CALLS_PER_ASPECT = RESEARCH_NEEDS_PER_ASPECT * RESEARCH_LLM_CALLS_PER_NEED
#: 单次 focused need 的内层工具调用上界。与 `harness/topic_runtime.py::BudgetForNeed.build()`
#: 的 `max_tool_calls=max(2, max_need_rounds_per_aspect * 2)` **同一条式子**：内层账本自己不会
#: 越过它，因此它是「这一次 need 真实可能消耗几次工具调用」的结构上界。写成同一个表达式而不是
#: 抄一个 4，是为了让两侧任何一处改动都在这里现形（不一致即由下面的断言拒绝开跑）。
RESEARCH_TOOL_CALLS_PER_NEED = max(2, RESEARCH_MAX_NEED_ROUNDS_PER_ASPECT * 2)
#: 每 aspect 的**可选**工具调用上界（`toc-1`）：有界 Evidence 兜底重切每 aspect 至多 1 次
#: （`fallback_allowed = not aspect_materials` 保证一栏至多触发一次），加上 focused follow-up
#: 至多 `max_need_rounds_per_aspect`(2) 轮、每轮至多 `RESEARCH_TOOL_CALLS_PER_NEED`(4) 次。
#: **初次必读**不在这条式子里：它是另一项，由本 topic 的责任矩阵现场算出（见下面 `_build`）。
#:
#: 为什么工具轴不能再沿用 `3 ×`：那个 3 是**LLM 轴式子的形状**被照搬到工具轴上，而工具调用
#: 的真实结构与它无关（一次树检索是一次工具调用，不按 action 轮数计）。旧式子在 18 个 aspect 的
#: `company_business` 上给出 54，而**本 topic 的初次必读结构上界**（= Σ_aspect 逐份派发基数，
#: 3 份来源全部 `retrieval_required` 时正是 54）就已经等于它——于是可选的 follow-up 只要花掉
#: 1 次，尾部栏目就再也发不出第一次检索。实测正是如此：`company_business` 后 6 栏各发了 3 次
#: `TREE_NAVIGATION`（导航成功、读根已选中）却**零 `TREE_TOOL_RESULT`**，`company_litigation`
#: 甚至到 55 并留下全节唯一的 `TOPIC_BUDGET_OVERSHOOT`。同文件 :10400 早已判定「上限低于结构
#: 上界不是更严格，而是把一次注定失败的运行伪装成可运行」，只是当时**只改了 LLM 轴**。
RESEARCH_OPTIONAL_TOOL_CALLS_PER_ASPECT = (
    1 + RESEARCH_MAX_NEED_ROUNDS_PER_ASPECT * RESEARCH_TOOL_CALLS_PER_NEED)
#: 冻结 Contract 投影出的研究 topic 个数。**只是一条期望值**，用来让「投影变了」在测试里可见；
#: 真值一律由 `_research_topic_aspects()` 现场读出，镜像复核也按现场 topic 集判。
RESEARCH_TOPIC_COUNT_BASIS = 6


def _research_derivation() -> dict:
    """把每 aspect 上界的**推导过程**摊平成可读字典（报告与预检共用同一份，不各推一遍）。"""
    return {
        "max_need_rounds_per_aspect": RESEARCH_MAX_NEED_ROUNDS_PER_ASPECT,
        "max_rounds_per_need": RESEARCH_MAX_ROUNDS_PER_NEED,
        "llm_calls_per_need": RESEARCH_LLM_CALLS_PER_NEED,
        "needs_per_aspect": RESEARCH_NEEDS_PER_ASPECT,
        "llm_calls_per_aspect": RESEARCH_LLM_CALLS_PER_ASPECT,
        # 工具轴**分两项**推导，不合成一个「每 aspect 上界」：
        "tool_calls_per_need": RESEARCH_TOOL_CALLS_PER_NEED,
        "optional_tool_calls_per_aspect": RESEARCH_OPTIONAL_TOOL_CALLS_PER_ASPECT,
        "mandatory_tool_calls": "由本 topic 的责任矩阵现场派生（逐 aspect 逐份派发基数之和）",
    }


#: `_research_topic_aspects()` 的**进程内**缓存。冻结 Contract 在进程内不会变，而
#: `_call_budget_policy()` 一次运行要建三遍（建门、拒绝产物、报告块），每遍重读一遍 Contract
#: 是纯浪费；缓存键留在函数里，因此「投影变了」只会在**新进程**里现形——这正合适，因为投影
#: 来自冻结资产。夹具若要换一份投影，把它清成 None 再调即可。
_RESEARCH_ASPECTS_CACHE: dict | None = None


def _research_topic_aspects() -> dict:
    """逐 topic 的 aspect 数（**只读** Contract 投影；不写产物、不发请求）。

    为什么可以在进入 `RealEnvironment` **之前**算出来：投影只依赖冻结 Contract + 冻结 profile。
    文档身份**不影响**逐 topic aspect 数（实测：换 document_id/document_version/sha 后六项逐值不变），
    因此这里用一份合成的 manifest identity 取值即可。这一条**不是靠人记**：`_build` 里仍然拿
    **真实**投影算出的逐 topic 预算做逐 topic 镜像复核（`_assert_research_axis_mirrors_research_policy`），
    一旦哪天投影开始依赖文档身份，两处就会不等而**整轮拒绝**，而不是静默地用错上限。
    """
    global _RESEARCH_ASPECTS_CACHE
    if _RESEARCH_ASPECTS_CACHE is not None:
        return dict(_RESEARCH_ASPECTS_CACHE)

    from contracts.loader_v2 import load_contract_v2
    from planning import demo_scope as SC
    from planning import schema as PS

    profile = SC.load_demo_scope_profile(active_profile_path())
    contract = load_contract_v2(str(REPO / profile.contract_asset))
    business = PS.ReportJobInput(
        job_id="job_m930_3_budget_policy_derivation", company_id="__policy_derivation__",
        company_name="__policy_derivation__", credit_type="general",
        report_as_of="1970-01-01", contract_version="v2")
    source_inputs = {
        "case_input_id": "case_m930_3_budget_policy_derivation",
        "document_id": "__policy_derivation__", "document_version": "v0",
        "raw_pdf_sha256": "0" * 64, "current_evidence_set_version": "es-policy-derivation",
        "substrate_dependency_versions": {
            k: f"{k}-v1" for k in SC.SUBSTRATE_DEPENDENCY_KEYS},
        "external_policy_snapshot_id": None,
        "budget_policy_id": profile.budget_policy_id,
        "budget_policy_version": profile.budget_policy_version,
        "model_policy_id": "demo_model_policy_v1", "code_fingerprint": "1" * 64,
    }
    manifest_scope = SC.build_scope_input_manifest(profile, business, source_inputs)
    projection = SC.project_contract_v2_scope(contract, profile, manifest_scope)
    _RESEARCH_ASPECTS_CACHE = {r.topic_id: len(r.aspects) for r in projection.requirements}
    return dict(_RESEARCH_ASPECTS_CACHE)


def _research_topic_caps(aspects_by_topic: dict) -> dict:
    """`topic:<id> → 该 topic 的 LLM 调用上限`（= 每 aspect 上界 × 该 topic 的 aspect 数）。

    scope 字符串**只在这里拼一次**，且用 `llm.budget` 的 `TOPIC_SCOPE_PREFIX` 而不是字面量
    `"topic:"`：作用域字符串与 `scope_kind_of` 的判定必须同源，否则「研究请求算在谁头上」
    会出现两套写法（一处改了另一处没改，读起来还对）。
    """
    from llm import budget as LB

    return {f"{LB.TOPIC_SCOPE_PREFIX}{topic_id}": RESEARCH_LLM_CALLS_PER_ASPECT * int(aspects)
            for topic_id, aspects in sorted(aspects_by_topic.items())}


def _research_axis_cap():
    """研究轴的 `AxisCap`：逐 topic 各一个上限，整轮上限 = 各 topic 上限之和。

    `max_attempts_per_scope`（单值缺省）登记为**最紧**的那个 topic 上限，而不是 0 或最大：
    未登记 scope 退回它时只会更紧、不会更宽，因此「登记表哪天被读空」不会静默放大上限。
    """
    from llm import budget as LB

    aspects_by_topic = _research_topic_aspects()
    caps = _research_topic_caps(aspects_by_topic)
    total = sum(caps.values())
    floor = min(caps.values())
    derivation = _research_derivation()
    per_topic_text = "、".join(
        f"{scope.split(':', 1)[1]} {aspects_by_topic[scope.split(':', 1)[1]]} aspect → "
        f"{cap}" for scope, cap in sorted(caps.items()))
    return LB.AxisCap(
        axis=LB.AXIS_RESEARCH, max_attempts_per_scope=floor, max_attempts=total,
        approved=True, per_scope_max_attempts=caps,
        basis=(f"**已批准**（{APPROVAL_PROVENANCE}）。研究侧上限**逐 topic 自派生**"
               f"（跨文档方案 §8.11；用户于 2026-09-24 明确裁决改走本项）："
               f"每 aspect 上界 `{RESEARCH_LLM_CALLS_PER_ASPECT}` × 该 topic 的 aspect 数。"
               f"`{RESEARCH_LLM_CALLS_PER_ASPECT}` 不是另定的数字，而是从"
               f"既有代码常量推出：内层每 need ≤ `max_need_rounds_per_aspect`("
               f"{derivation['max_need_rounds_per_aspect']}) + `force_converge` 那一轮 = "
               f"{derivation['max_rounds_per_need']} 轮，每轮一次 action，收敛时 +1 answer +1 entailment "
               f"⇒ 每 need ≤ {derivation['llm_calls_per_need']} 次；每 aspect 的 needs ≤ "
               f"{derivation['needs_per_aspect']} ⇒ 每 aspect ≤ {RESEARCH_LLM_CALLS_PER_ASPECT} 次。"
               f"实测**最坏**读数是 10 次/aspect（a7 `industry_scale_cycle`：4 aspect 用了 40），"
               f"低于 {RESEARCH_LLM_CALLS_PER_ASPECT}，故这是盖得住的上界而非估计。"
               f"逐 topic：{per_topic_text}；整轮 = 各 topic 之和 = {total}"
               f"（**不是**「单值 × topic 数」）。单值缺省 `max_attempts_per_scope` 取最紧的 "
               f"{floor}。三个研究类别**共用一个轴**：不是每类各一份。"
               "**方案 §8.11 正文写的 `3 × aspects` 未采用**：它低于实测（industry 用了 8 次/aspect），"
               "照抄会把该 topic 从 36 降到 12 —— 上限低于结构上界不是更严格，而是把一次注定失败的"
               "运行伪装成可运行。研究请求与写作请求一样**先归属、先记账、再发出**；"
               "「未登记归属」或「超出本 topic / 研究整轮上限」都在**发出之前**被拒"),
    )


def _call_budget_policy():
    """已批准的调用预算政策（**唯一**定义处；命令行无法抬高任何上限）。

    每一类的 `approved` / `basis` 就是「这个数字是谁批的」。本次（2026-09-23 一次性真实预览
    授权）批准的是一组**上限**，不是使用目标——上限只用来阻止失控循环：

      * `narration`（门前提案 + 有界修正 + 栏目定向补足 + 有界重写 + 门后自然组织）：
        **每节 ≤62、三节共 ≤144**（`_narration_structural_bound()` 从代码常量 + 冻结 Contract
        推出，并在 `_call_budget` 里**事前**比对）。**这一组数字尚未获批**（`approved=False`）：
        本轮把「一轮写作 = 一次请求」改成了「一轮写作 = 一次**分批扫描**」（§二 确定性分批），
        因此旧批准 8/24 的结构依据已经不存在——8/24 是按「每轮 1 次」推的，而现在的代码每轮
        要逐批请求。**上限低于上界不是「更严格」，而是「把一次注定失败的运行伪装成可运行」**：
        若不重估，预检会通过、真实请求会发出去，然后在第一节中途撞上限整轮失败。
        结构性上界：一节最多走两次写作（第 0 轮 + 有界重写 1 轮，`MAX_FOLLOW_UP_ROUNDS=1`），
        每次写作 = 门前提案至多 2 轮（第 1 轮整束被拒 → 第 2 轮重新输出**完整**提案集）
        + 栏目定向 1 轮 = 3 轮，每轮 = 本节 aspect 的分批数 + 至多 2 次截断后对半缩小重问，
        +（有非表格承载 Claim 时）每轮写作一次门后自然组织 ⇒ 逐节 62/44/38。
        **允许的最大值不是预期值**：模型一次就覆盖全部栏目、每批都不被截断时，每节只有
        「批数 × 轮数 + 组织」那么多次（company 8×3×2+2 = 50）。
      * `claim_entailment`（逐候选蕴含）：**每节 ≤40、共 ≤100**。每次蕴含对应一条通过机械门的
        factual candidate，数量**随内容增长、无法从规范推出**，因此这是带余量的上限，不是需求
        估计。离线替身观察到的 44 次不得读成真实运行的确定需求；真实用完多少次由本次账本读出来。
      * `section_llm_evaluator`（可选章级 LLM 评估）：**已批准，上限 0**。本 runner 未启用它
        （`allow_llm_evaluator` 缺省 False），因此「本轮一次都不发」是一个**已批准的**决定
        （写法是 `approved=True` + 上限 0，而不是不批准——后者会让整轮无法以强制模式运行）。
      * `final_sentence_fidelity`（最终句语义门 B，`nsfid-1`）：**每节 ≤2、三节共 ≤6**，
        上限**由 `_narration_structural_bound()` 推导**（每节写作轮数 × 每轮定稿门至多 1 次 ×
        三节），不在别处另写一个数字。它是 2026-09-27 批次新增的**真实调用点**，因此批准出处
        是**另一句**用户提示词（`FINAL_SENTENCE_APPROVAL_PROVENANCE`），不是 2026-09-26 的
        r6 授权——那次授权里没有这个调用点，「沿用旧出处」等于把新增调用点说成旧批准的一部分。

    **（独立轴）研究侧三类调用共用一个 `AxisCap`（`AXIS_RESEARCH`），逐 topic 各一个上限**：
    acc-20 起上限**逐 topic 自派生** = 每 aspect 上界 10 × 该 topic 的 aspect 数，整轮上限 =
    各 topic 上限之**和**（不是「单值 × topic 数」）。`per_scope_max_attempts` 就是逐 topic 那一份；
    `max_attempts_per_scope` 保留为**缺省**（未登记 scope 退回它），本次登记为最紧的那个
    topic 上限，以免出现「登记表被读空 ⇒ 谁都退回一个更宽的数」。
    登记的是**归属**而不是数值——`research_action_v1` / `research_answer_v1` /
    `research_entailment_v1` 从此可被识别、计数并在发出前受检；三个类别**共用一个轴上限**，
    不是每类各一份（否则等于把研究侧上限悄悄乘 3）。**`approved=True`**：研究侧上限与写作侧
    同样是**已批准用于本次运行**的量；真实模式下预检仍会拒绝——但拒绝的理由是
    「上限低于结构上界」，不是「未批准」（见 `_assert_budget_covers_structural_bound`）。

    **两条轴不合并**：写作轴的 `total_max_attempts` **只数写作轴的尝试**；
    研究侧整轮上限**不并进**它。读产物时 **写作轴的整轮上限不是全部 provider 调用的总上限**
    ——研究侧上限在报告里**单独成行**。

    **本版（`m930-3-budget-7`）相对上一版只增加一项**：新增 B 门的类别与归属登记，并把整轮上限
    从 244 抬到 **250**（= 144 + 100 + 0 + 6）。**抬高的部分恰好是新增调用点的结构上界**，
    不是把任何既有类别放宽：叙述类仍是 62/144、蕴含仍是 40/100、可选章级评估仍是 0/0，
    研究轴一字未动。升版是因为**登记表内容变了就必须升版**（新调用点若不登记，首次调用即被
    `category_of` 拒绝——那会让「跑不起来」伪装成「本节没内容」）。

    预算里**没有**费用型 input/output token 限额：token 不是本次的约束轴；每次请求的实际
    `max_tokens` 按阶段给足容量（`OUTPUT_CAPACITY_*`），逐次记进账本与客户端记录。
    """
    from harness import runtime as RT
    from llm import budget as LB
    from sections import claim_entailment_evaluator as CEE
    from sections import final_sentence_fidelity as FSF
    from sections import narrative_organizer as NO
    from sections import pack_writer as PW
    from sections import rules_evaluator as RE

    # B 的每节 / 整轮上限**从同一套写作轮数模型推出**（同一函数、同一处取值）：上限一旦与
    # 结构上界脱钩，「预检通过、真实请求发出去、中途撞上限整轮失败」这个形状就会出现。
    _bound = _narration_structural_bound()
    return LB.CallBudgetPolicy(
        policy_version=APPROVED_BUDGET_POLICY_VERSION,
        approved_model=APPROVED_BUDGET_MODEL,
        categories=(
            LB.CategoryCap(
                category=LB.CATEGORY_NARRATION, max_attempts_per_section=62,
                max_attempts_total=144, max_output_tokens=None, approved=True,
                basis=(f"**已批准**（{APPROVAL_PROVENANCE}）。本版声明：每节 ≤62、三节共 ≤144。"
                       "**为什么这个数字与旧批准不同**：旧口径把「一轮写作」当成"
                       "「一次请求」，而分批之后一轮写作 = 一次**分批扫描**（逐批请求、逐批解析，"
                       "全部成功才合并成一份完整提案集送门）。旧上限 8/24 正是按「每轮 1 次」推出来"
                       "的，它的结构依据已被代码改动取消——上限低于结构上界不是「更严格」，而是"
                       "「把一次注定失败的运行伪装成可运行」：预检会通过、真实请求会发出去，然后"
                       "撞上限整轮失败，而且已批准的额度已经花掉。**逐项推导**（来源全部是可读"
                       "常量，见 `_narration_structural_bound()`）：每轮调用数 = 本节 aspect 的"
                       "分批数（`aspect_batch_count` = ceil(n/MAX_ASPECTS_PER_BATCH=12)）"
                       "+ `MAX_SWEEP_SHRINK_STEPS`(=2) 次截断后对半缩小重问（每次净增 1 次调用，"
                       "零 aspect 也必须算 1 批，与 `plan_aspect_batches` 的零 aspect 分支逐值一致）；"
                       "每次写作 = `APPROVED_WRITER_MAX_LLM_RETRIES+1`(=2) 轮提案"
                       " + `MAX_SUBSECTION_FOCUS_PASSES`(=1) 轮栏目定向 = 3 轮；"
                       "C3 的定向重提案（`MAX_DIRECTED_REPROPOSAL_PASSES`=1）**不在这三项之外**"
                       "——它专用上面那 1 轮重试，只有在 `attempt <= max_llm_retries` 时才排定，"
                       "故 3 轮这个数**没有变**，本式的每一项也一字未动；"
                       "每节写作轮数 = `MAX_FOLLOW_UP_ROUNDS+1`(=2)；每次写作另有门后自然组织 1 次。"
                       "逐节：company 85 aspect → (8+2)×3×2+2 = 62；financial 54 → (5+2)×3×2+2 = 44；"
                       "industry 38 → (4+2)×3×2+2 = 38；合计 144。上界按 **Contract 投影全集** 算"
                       "（本次选中的 aspect 是数据决定的子集，`selected ⊆ projected`，而"
                       "`aspect_batch_count` 对 aspect 数单调不减），因此对任何一次运行只多不少。"
                       "**这是允许的最大值，不是预期值**：模型一次就覆盖全部栏目、每批都不被截断时，"
                       "company 一节只有 8×3×2+2 = 50 次。**变更依据**仍是 2026-09-23 批次 A"
                       "补充裁决「允许为内容质量增加有界 LLM 调用；限制只用于防循环、可追踪和"
                       "故障停止，不以费用压缩材料或文本」——本次同样不是为多写几段，也**不允许**"
                       "借此压缩材料、候选、补件或正文（分批只限定本批要回答的 aspect 范围，"
                       "每批仍可访问完整、精确的 Writer 材料集合）")),
            LB.CategoryCap(
                category=LB.CATEGORY_CLAIM_ENTAILMENT, max_attempts_per_section=40,
                max_attempts_total=100, max_output_tokens=None, approved=True,
                basis=(f"已批准（2026-09-23 一次性真实预览授权；2026-09-24 再次覆盖："
                       f"{APPROVAL_PROVENANCE}）：每节 ≤40、共 ≤100。"
                       "每次蕴含对应一条通过机械门的 factual candidate，数量随内容增长、"
                       "无法从规范推出，故这是**带余量的上限**而不是需求估计；离线替身观察到"
                       "的 44 次不得读成真实运行的确定需求")),
            LB.CategoryCap(
                category=LB.CATEGORY_SECTION_LLM_EVALUATOR, max_attempts_per_section=0,
                max_attempts_total=0, max_output_tokens=None, approved=True,
                basis=("已批准为**本轮一次都不发**：本 runner 未启用可选章级评估"
                       "（allow_llm_evaluator=False）。用「已批准 + 上限 0」表达，"
                       "而不是「不批准」——后者会让整轮无法以强制模式运行")),
            # 最终句语义门 B（`nsfid-1`）：2026-09-27 批次新增的**真实调用点**。上限**不写死**，
            # 而是从与叙述类**同一套**写作轮数模型推出（每节写作轮数 × 每轮至多一次定稿门）。
            LB.CategoryCap(
                category=LB.CATEGORY_FINAL_SENTENCE_FIDELITY,
                max_attempts_per_section=int(_bound["final_sentence_per_section"]),
                max_attempts_total=int(_bound["final_sentence_total"]),
                max_output_tokens=None, approved=True,
                basis=(f"**已批准**（{FINAL_SENTENCE_APPROVAL_PROVENANCE}）。"
                       f"本版声明：每节 ≤{_bound['final_sentence_per_section']}、三节共 "
                       f"≤{_bound['final_sentence_total']}（= `MAX_FOLLOW_UP_ROUNDS + 1` "
                       f"写作轮数 × 每轮定稿门至多 1 次 × 三节）。"
                       "**上限由一个可读常量式推出，不是拍出来的数**：`_narration_structural_bound()`"
                       "的 `final_sentence_per_section` / `final_sentence_total` 就是这两个数，"
                       "预检再逐项比对一次——改为「每轮更多次」或「多一轮写作」时，上限与预检"
                       "自动跟着变。**这是上界不是预期值**：正文没有承载事实的最终句时，"
                       "`FSF.drive_final_sentence_gate` 直接返回且**不发请求**。"
                       "上限只用于防循环与截断恢复（截断后不重跑，如实落成 `missing` 阻断），"
                       "**不以费用为由削减材料或正文**。")),
        ),
        # 归属表：每个真实调用点的 prompt 版本 → 类别。未登记的 prompt 版本一律拒绝，
        # 「新调用点悄悄绕过预算」因此不可表达。研究侧三个版本从这里开始可被识别——它们
        # **不登记**时的后果不是静态的，而是运行期「首次研究调用即被拒」（`category_of` 与
        # `enforce` 无关），因此「把门提到研究之前」与「登记研究归属」必须同时做。
        prompt_versions={
            PW.NARRATION_PROMPT_VERSION: LB.CATEGORY_NARRATION,
            NO.NARRATIVE_ORGANIZER_PROMPT_VERSION: LB.CATEGORY_NARRATION,
            CEE.CLAIM_ENTAILMENT_PROMPT_VERSION: LB.CATEGORY_CLAIM_ENTAILMENT,
            FSF.FINAL_SENTENCE_FIDELITY_PROMPT_VERSION:
                LB.CATEGORY_FINAL_SENTENCE_FIDELITY,
            RE.NARRATIVE_EVALUATOR_PROMPT: LB.CATEGORY_SECTION_LLM_EVALUATOR,
            RT.RESEARCH_ACTION_PROMPT_VERSION: LB.CATEGORY_RESEARCH_ACTION,
            RT.RESEARCH_ANSWER_PROMPT_VERSION: LB.CATEGORY_RESEARCH_ANSWER,
            RT.RESEARCH_ENTAILMENT_PROMPT_VERSION: LB.CATEGORY_RESEARCH_ENTAILMENT,
        },
        # 研究侧三个类别**共用一个**轴上限（不是每类各一份——那等于把研究侧上限悄悄乘 3）。
        # 但「逐 topic」是**逐 topic 各一个值**：正确形式是「每 aspect 上界 × 该 topic 的 aspect
        # 数」，而各 topic 的 aspect 数本来就不相等。镜像是否仍然成立由
        # `_assert_research_axis_mirrors_research_policy` 用真实 `ResearchBudgetPolicy`
        # 与真实 topic 集**逐 topic** 复算（不一致即整轮拒绝，而不是等人发现）。
        axes=(
            _research_axis_cap(),
        ),
        # 归属表 → 轴的映射（研究三类 → `AXIS_RESEARCH`；写作三类不走这里，缺省 `AXIS_WRITING`）。
        category_axis={
            LB.CATEGORY_RESEARCH_ACTION: LB.AXIS_RESEARCH,
            LB.CATEGORY_RESEARCH_ANSWER: LB.AXIS_RESEARCH,
            LB.CATEGORY_RESEARCH_ENTAILMENT: LB.AXIS_RESEARCH,
        },
        # 整轮总数 = **写作轴**各类上限之和（144 + 100 + 0 + 6）= 250。**研究侧的上限不并进这个
        # 数**：两条轴的上限互不消耗，把它说成「全部 provider 调用的总上限」是读错
        # （研究侧上限在报告里单独成行）。`total_approved` 随本次运行一起为 True：它表达的是
        # 「这一组上限已被批准用于本次运行」，不是「上限本身是理想值」。
        total_max_attempts=250, total_approved=True)


def _writing_axis_total_attempts() -> int:
    """写作轴的整轮尝试上限——**从政策里读**，报告文案里不写死这个数字。

    写死的后果是具体的：`m930-3-budget-7` 把整轮上限从 244 抬到 250（新增最终句语义门 B 的
    2/6），若报告文案仍印 244，读的人会拿一个**不存在的上限**去核对账本。而这个数字又是
    「写作轴上限不是全部 provider 调用总上限」那句话的读数依据，印错比不印更坏。

    与 `_assert_budget_covers_structural_bound` 是**两件事**：那条判「够不够」，这条只回答
    「报告该印哪个数」。两边读的是同一份政策，因此不会各说各话。相等性由
    `evals/test_llm_call_budget` 钉住（整轮上限必须逐值等于各类上限之和），因此这里不需要
    在报告期再算一次和。
    """
    return int(_call_budget_policy().total_max_attempts)


def _assert_budget_covers_structural_bound(policy) -> None:
    """**事前**核对：已批准的上限必须 ≥ 按代码常量推出的结构上界，否则整轮拒绝。

    这一步存在的理由是一个已经发生过的失败形状：上限按「零重试」推导、而链路带着重试跑，
    预检通过 → 真实请求发出去 → 中途撞上限 → 整轮失败，且**已批准的额度已经花掉**。
    上限低于上界不是「更严格」，而是「把一次注定失败的运行伪装成可运行」。因此这里不比数字
    大小，而是把两个推导过程一起报出来。
    """
    from llm import budget as LB

    bound = _narration_structural_bound()
    cap = policy.cap_for(LB.CATEGORY_NARRATION)
    # 逐节的推导一起报出来：只报一个「每节 62」，读的人无法判断它是按哪一节算的，也就无法
    # 判断下一次改了 Contract 投影之后这个数会不会失效。
    derivation = "；".join(
        f"{sid}: {bound['aspects_by_section'][sid]} aspect → "
        f"{bound['batches_by_section'][sid]} 批 +{bound['max_shrink_steps']} 缩小 = "
        f"{bound['calls_per_round_by_section'][sid]} 次/轮 × {bound['rounds_per_pass']} 轮 × "
        f"{bound['writer_passes_per_section']} 轮写作 + {bound['organizer_calls_per_pass']} 组织 = "
        f"{bound['per_section_by_section'][sid]}"
        for sid in SECTION_ORDER)
    problems: list[str] = []
    if int(cap.max_attempts_per_section) < int(bound["per_section"]):
        problems.append(
            f"narration 每节上限 {cap.max_attempts_per_section} < 结构上界 "
            f"{bound['per_section']}（最坏一节；逐节推导：{derivation}）")
    if int(cap.max_attempts_total) < int(bound["total"]):
        problems.append(
            f"narration 整轮上限 {cap.max_attempts_total} < 结构上界 {bound['total']}"
            f"（各节之和，不是「每节 × 节数」）")
    # 最终句语义门 B 走**同一条**判据（2026-09-27 批次新增调用点）。它的上限由同一个
    # `_narration_structural_bound()` 推出，因此这条比对不是同义重述：一旦 B 的上限被改成
    # 别处的字面量、而结构上界因代码改动上升，这里会当场拒绝整轮，而不是等到某一节中途撞限。
    sentence_cap = policy.cap_for(LB.CATEGORY_FINAL_SENTENCE_FIDELITY)
    if int(sentence_cap.max_attempts_per_section) < int(bound["final_sentence_per_section"]) \
            or int(sentence_cap.max_attempts_total) < int(bound["final_sentence_total"]):
        problems.append(
            f"final_sentence_fidelity 上限 "
            f"{sentence_cap.max_attempts_per_section}/"
            f"{sentence_cap.max_attempts_total} < 结构上界 "
            f"{bound['final_sentence_per_section']}/{bound['final_sentence_total']}"
            f"（每节写作轮数 {bound['writer_passes_per_section']} × 每轮定稿门 "
            f"{bound['final_sentence_calls_per_pass']} 次 × {bound['sections']} 节）")
    # 整轮上限同样要盖得住各类上限之和：否则「本节还有额度、整轮已经没了」，截断点会出现在
    # 与节无关的地方，产物里读起来像「某一节被针对」。
    per_category = sum(int(cap.max_attempts_total) for cap in policy.categories)
    if policy.total_max_attempts is not None and int(policy.total_max_attempts) < per_category:
        problems.append(
            f"整轮总上限 {policy.total_max_attempts} < 各类上限之和 {per_category}")
    # 研究轴走**同一条**判据（acc-20 补齐；此前这里只管 narration）。这一步必须在**发出任何
    # 请求之前**：研究发生在 `RealEnvironment._build` 内，而预检在它之前跑完，因此一个低于
    # 结构上界的研究轴上限会在这里被拒，而不是等研究跑到某个 topic 的中途才炸——那时已批准的
    # 额度已经花掉，产物还会带着一个「研究失败」的假象。
    problems.extend(_research_axis_bound_problems(policy))
    if problems:
        raise AcceptanceRefusal(
            "已批准的调用上限低于当前代码的结构上界，本次一个请求都不发："
            + "；".join(problems)
            + "。修正方式是**重估并声明**上限（写进 `_call_budget_policy` 的 basis），"
              "不是放宽聚合门或压缩材料/正文")


def _research_axis_bound_problems(policy) -> list[str]:
    """研究轴的**结构上界覆盖**判据（事前；返回问题清单，不抛）。

    与 narration 那一条同源同理由：上限低于结构上界不是「更严格」，而是「把一次注定失败的
    运行伪装成可运行」。逐 topic 的判据比「整轴够不够大」更强——整轴够大仍可能让某一个
    **小 topic** 被卡住（旧的共享 36 就是这样：整轴 216 看着宽裕，`industry_scale_cycle`
    却在 4 个 aspect 上用到了 40）。
    """
    from llm import budget as LB

    try:
        cap = policy.axis_cap_for(LB.AXIS_RESEARCH)
    except LB.LLMCallAttributionError as exc:
        return [f"策略里没有研究轴上限：研究请求不会被任何上限约束（{exc}）"]
    aspects_by_topic = _research_topic_aspects()
    expected = _research_topic_caps(aspects_by_topic)
    registered = {str(k): int(v) for k, v in (cap.per_scope_max_attempts or {}).items()}
    derivation = _research_derivation()
    problems: list[str] = []
    missing = sorted(set(expected) - set(registered))
    if missing:
        problems.append(
            f"研究轴没有为 {missing} 登记逐 topic 上限：它们会退回单值缺省 "
            f"{cap.max_attempts_per_scope}，而不是各自的结构上界")
    for scope, want in sorted(expected.items()):
        got = registered.get(scope)
        if got is not None and int(got) < int(want):
            problems.append(
                f"{scope} 的研究轴上限 {got} < 结构上界 {want}（= 每 aspect "
                f"{RESEARCH_LLM_CALLS_PER_ASPECT} × "
                f"{aspects_by_topic[scope.split(':', 1)[1]]} aspect）")
    if registered and cap.max_attempts is not None \
            and int(cap.max_attempts) < sum(expected.values()):
        problems.append(
            f"研究轴整轮上限 {cap.max_attempts} < 各 topic 结构上界之和 "
            f"{sum(expected.values())}")
    if problems:
        problems.append(
            "逐项推导：每 need ≤ "
            f"{derivation['max_rounds_per_need']} 轮 + 1 answer + 1 entailment = "
            f"{derivation['llm_calls_per_need']} 次；每 aspect ≤ "
            f"{derivation['needs_per_aspect']} needs ⇒ {RESEARCH_LLM_CALLS_PER_ASPECT} 次")
    return problems


def _research_axis_mirror(*, policy, aspects_by_topic: dict, runtime_budget, topic_ids) -> dict:
    """复算：研究轴的 `AxisCap` 是否与**现场真实投影**逐 topic 一致（返回读数，不抛）。

    这件事必须能被**复算**，而不是靠人记住两处数字一致：`_call_budget_policy()` 在进入环境
    **之前**就要建出研究侧上限（用的是它自己那份只读投影），而现场那份逐 topic aspect 数要到
    `_build` 里才由**真实** Contract 投影算出来。两处一旦漂移，后果是具体的——研究侧的门比
    真实预算**宽**，于是「本 topic 已经超发」不会在任何地方被拒绝；比真实预算**紧**，则一次
    本来合法的运行会在中途撞门失败。两种都不该等人去发现。

    逐项闸门（任一不成立即问题）：

      * 轴存在且已登记逐 topic 上限（`per_scope_max_attempts`）；
      * 轴登记的 topic 集与**真实** topic 集**完全相同**（缺一个 ⇒ 该 topic 退回单值缺省；
        多一个 ⇒ 登记了一个跑不到的 scope，两种都是读错）；
      * 每个 topic：轴上限 == `每 aspect 上界 × 该 topic 的真实 aspect 数`；
      * `max_attempts` == 各 topic 上限之**和**（不是「单值 × topic 数」）；
      * 单值缺省 == 各 topic 上限中的**最小值**（退路只会更紧，不会更宽）；
      * 现场共享的 `max_llm_calls_per_topic` == `每 aspect 上界 × 真实 aspect 数的最大值`，
        且 **≥ 每个 topic 自己的轴上限**（共享运行时预算不得低于逐 topic 的事前门）。
    """
    from llm import budget as LB

    problems: list[str] = []
    try:
        cap = policy.axis_cap_for(LB.AXIS_RESEARCH)
    except LB.LLMCallAttributionError as exc:
        return {"checked": False, "problems": [str(exc)],
                "note": "策略里没有研究轴上限：研究请求不会被任何上限约束"}
    topic_count = len(tuple(topic_ids))
    real = {str(k): int(v) for k, v in aspects_by_topic.items()}
    registered = {str(k): int(v) for k, v in (cap.per_scope_max_attempts or {}).items()}
    expected = _research_topic_caps(real)
    if set(registered) != set(expected):
        problems.append(
            f"研究轴登记的 topic 集与真实 topic 集不同：缺 "
            f"{sorted(set(expected) - set(registered))}、多 "
            f"{sorted(set(registered) - set(expected))}")
    for scope in sorted(set(registered) & set(expected)):
        if registered[scope] != expected[scope]:
            problems.append(
                f"{scope} 的轴上限 {registered[scope]} ≠ 每 aspect 上界 "
                f"{RESEARCH_LLM_CALLS_PER_ASPECT} × 真实 aspect 数 "
                f"{real[scope.split(':', 1)[1]]} = {expected[scope]}")
    if registered and cap.max_attempts != sum(registered.values()):
        problems.append(
            f"研究轴整轮上限 {cap.max_attempts} ≠ 各 topic 上限之和 "
            f"{sum(registered.values())}（不是「单值 × topic 数」）")
    if registered and cap.max_attempts_per_scope != min(registered.values()):
        problems.append(
            f"研究轴单值缺省 {cap.max_attempts_per_scope} ≠ 各 topic 上限的最小值 "
            f"{min(registered.values())}（退路不得比登记的更宽）")
    runtime_cap = getattr(runtime_budget, "max_llm_calls_per_topic", None)
    expected_runtime = RESEARCH_LLM_CALLS_PER_ASPECT * max(real.values()) if real else None
    if expected_runtime is not None and runtime_cap != expected_runtime:
        problems.append(
            f"现场共享 max_llm_calls_per_topic {runtime_cap} ≠ 每 aspect 上界 "
            f"{RESEARCH_LLM_CALLS_PER_ASPECT} × 最大 aspect 数 {max(real.values())} = "
            f"{expected_runtime}")
    if runtime_cap is not None and registered:
        tighter = {s: v for s, v in registered.items() if int(runtime_cap) < v}
        if tighter:
            problems.append(
                f"现场共享运行时预算 {runtime_cap} 低于部分 topic 的逐 topic 事前门 {tighter}："
                "共享预算不得比事前门更紧（否则一次合法运行会在中途撞门失败）")
    return {
        "checked": True,
        "axis": cap.axis,
        "axis_max_attempts_per_scope": cap.max_attempts_per_scope,
        "axis_max_attempts": cap.max_attempts,
        "axis_per_scope_max_attempts": dict(sorted(registered.items())),
        "approved": cap.approved,
        "observed_aspects_by_topic": dict(sorted(real.items())),
        "derived_axis_cap_by_topic": dict(sorted(expected.items())),
        "observed_runtime_max_llm_calls_per_topic": runtime_cap,
        "observed_topic_count": topic_count,
        "problems": problems,
        "note": ("研究侧上限**逐 topic 自派生**（每 aspect 上界 × 该 topic 的 aspect 数），"
                 "不是另定的数字；本块给出登记值与**现场真实投影**的逐 topic 比对结果。"
                 f"研究轴的整轮上限 **不并进**写作轴的 {policy.total_max_attempts}："
                 "两条轴的上限互不消耗。"),
    }


def _assert_research_axis_mirrors_research_policy(policy, *, aspects_by_topic: dict,
                                                  runtime_budget, topic_ids) -> dict:
    """`_research_axis_mirror` 的**事前**版本：不一致即整轮拒绝（不带着漂移继续跑）。"""
    evidence = _research_axis_mirror(policy=policy, aspects_by_topic=aspects_by_topic,
                                     runtime_budget=runtime_budget, topic_ids=topic_ids)
    if evidence["problems"]:
        raise AcceptanceRefusal(
            "研究侧预算轴与现场真实投影不一致，本次一个请求都不发："
            + "；".join(evidence["problems"])
            + "。修正方式是**重新声明**逐 topic 上限（写进 `_call_budget_policy` 的研究轴 basis"
              "与其推导常量），不是放宽研究轴或改写现场预算")
    return evidence


def _research_axis_report_block(state) -> dict:
    """报告里的**研究侧上限块**（单独成行，不并进写作轴的整轮上限）。

    为什么单独成行：写作轴的 `total_max_attempts` 读起来像一个「全链总预算」，但研究调用
    此后由**另一条轴**约束（按 topic 计量、与写作按节计量的账互不消耗）。把两条轴挤成一个数字
    的后果是具体的——读者会以为研究调用也算在写作轴那一整轮上限里，于是「研究侧上限没批」或
    「研究侧已撞上限」在报告里看不出来。本块给出研究轴上限、批准状态、按轴计数，以及镜像与
    现场研究预算的逐值比对（`_research_axis_mirror`，与 `_build` 里的**那一次断言是同一个
    函数**）。

    **文案不写死数字**：写作轴的整轮上限随结构上界演进过（`m930-3-budget-7`：244 → 250，
    新增最终句语义门 B 的 2/6），写死的旧数会让报告继续印一个不存在的上限——而「写作轴上限
    不是全部 provider 调用总上限」这句话正是靠这个数字读的。这里一律从 `policy` 现场读。
    """
    from llm import budget as LB

    policy = _call_budget_policy()
    try:
        axis_cap = policy.axis_cap_for(LB.AXIS_RESEARCH).to_dict()
    except LB.LLMCallAttributionError as exc:
        axis_cap = {"axis": LB.AXIS_RESEARCH, "error": str(exc)}
    inputs = getattr(state, "inputs", None)
    # 报告里复算用的**现场**量：逐 topic 的 aspect 数取自本轮真实需求集，共享运行时预算取自
    # 本轮真实 run_context。两者都不是重新读一遍常量——报告与 `_build` 里的那次断言必须比
    # 同样的东西，否则「报告说一致」与「现场确实一致」就成了两件事。
    aspects_by_topic: dict = {}
    runtime_budget = None
    if inputs is not None:
        for requirement in (getattr(inputs, "requirements", None) or {}).values():
            aspects_by_topic[str(requirement.topic_id)] = len(requirement.aspects)
        for section_id, _task in (getattr(inputs, "tasks", None) or {}).items():
            ctx = (getattr(inputs, "run_contexts", None) or {}).get(section_id)
            if getattr(ctx, "budget_policy", None) is not None:
                runtime_budget = ctx.budget_policy
                break
    mirror = _research_axis_mirror(policy=policy, aspects_by_topic=aspects_by_topic,
                                   runtime_budget=runtime_budget,
                                   topic_ids=tuple(sorted(aspects_by_topic)))
    ledger = getattr(state, "budget", None)
    return {
        "axis": LB.AXIS_RESEARCH,
        "axis_cap": axis_cap,
        "categories": sorted(c for c, a in policy.category_axis.items()
                             if a == LB.AXIS_RESEARCH),
        "measured_by_axis": None if ledger is None else ledger.axis_counts(),
        "measured_by_scope_kind": None if ledger is None else ledger.scope_kind_counts(),
        "mirror": mirror,
        "note": ("研究侧上限**逐 topic 自派生**（每 aspect 上界 × 该 topic 的 aspect 数），"
                 "不是另定的数字；`mirror` 是与**现场真实投影**的逐 topic 复算结果"
                 "（`checked=false` = 本研究入口未受过事前门约束，不得读成「已核对通过」）。"
                 f"研究轴整轮上限**不并进**写作轴的 {policy.total_max_attempts}：把写作轴的"
                 "整轮上限说成全部 provider 调用的总上限是读错。"),
    }


def _call_budget(*, mode: str):
    """建门：真实模式必须在**预检处**全部获批，否则整轮拒绝（一个请求都不发）。"""
    from llm import budget as LB

    policy = _call_budget_policy()
    if mode == MODE_REAL:
        missing = policy.approvable
        if missing:
            raise AcceptanceRefusal(
                f"真实模式的调用预算尚未全部获批：{list(missing)}；"
                "本次不发起任何真实调用（各类上限须先经用户批准，见停止报告里的预算方案）")
        # 先比对上界再建门：上限不足时同样一个请求都不发（见上）。
        _assert_budget_covers_structural_bound(policy)
        return LB.LLMCallBudget(policy, enforce=True)
    # 离线同样比对：离线替身不发真实请求，但**上界与真实模式是同一个**，让不一致在离线阶段
    # 就暴露，而不是等到唯一一次真实运行。
    _assert_budget_covers_structural_bound(policy)
    # 离线：替身不发真实请求，账本照记（`enforce=False`），用来证明「完整链的调用记录可逐项守恒」。
    return LB.LLMCallBudget(policy, enforce=False)


def output_capacity_policy() -> dict:
    """逐阶段的输出容量（发往 provider 的 `max_tokens`）与选择依据——**写进产物**的那一份。

    它不是费用上限：预算政策里的 `max_output_tokens` 一律为 None（见 `_call_budget_policy`）。
    这里的数字是「让该阶段的输出结构能完整出来」的技术参数，按 prompt 版本给；离线替身不发
    请求，因此它的 `max_tokens` 是 None（不伪造 provider 参数）。
    """
    from sections import claim_entailment_evaluator as CEE
    from sections import final_sentence_fidelity as FSF
    from sections import narrative_organizer as NO
    from sections import pack_writer as PW
    from sections import rules_evaluator as RE

    return {
        "unit": "tokens",
        "by_prompt_version": {
            PW.NARRATION_PROMPT_VERSION: OUTPUT_CAPACITY_NARRATION,
            NO.NARRATIVE_ORGANIZER_PROMPT_VERSION: OUTPUT_CAPACITY_NARRATION,
            CEE.CLAIM_ENTAILMENT_PROMPT_VERSION: OUTPUT_CAPACITY_CLAIM_ENTAILMENT,
            FSF.FINAL_SENTENCE_FIDELITY_PROMPT_VERSION:
                OUTPUT_CAPACITY_FINAL_SENTENCE_FIDELITY,
            RE.NARRATIVE_EVALUATOR_PROMPT: 0,
        },
        "basis": OUTPUT_CAPACITY_BASIS,
        "evaluator_note": ("可选章级评估本轮一次都不发（上限 0），因此没有为它下发任何容量；"
                           "0 表示「不发」而不是「发了 0 个 token」"),
        "note": ("预算政策里**没有**费用型 input/output token 限额；每次请求的实际 max_tokens "
                 "逐次记在账本的 attempts[].max_tokens 与客户端记录的 calls[].max_tokens 里，"
                 "报告不再只给一个统一数字"),
    }


def _clients(*, mode: str, model: str | None):
    """生成客户端。离线与真实走**同一**结果接口，返回 `(narrator, entailment, sentence)`。

    三个客户端**不是**三条各自的链：蕴含门与最终句语义门都**复用叙述客户端的传输适配器**
    （真实模式下因此每一次调用只有一条记录，见 A7 的「共用客户端不得漏计或重复计数」）。
    离线模式三个替身各自带 `calls`，所以对账时要把三个都读进来。
    """
    from sections import claim_entailment_evaluator as CEE
    from sections import final_sentence_fidelity as FSF
    from sections import pack_writer as PW

    if mode == MODE_OFFLINE:
        return (OfflineNarrationClient(), OfflineEntailmentClient(),
                OfflineFinalSentenceClient())
    # 真实模式：叙述客户端与两道语义门**共用同一个传输适配器**（这意味着一次蕴含 / 一次最终句
    # 核验各只有一条调用记录，见 A7），但**输出容量按 prompt 版本给足**——提案/组织出完整正文，
    # 逐候选蕴含与逐原子最终句核验各出一条结构化决定。`reject_truncated=True`：被截断的响应算
    # 本次调用失败，不返回残缺正文。
    narrator = PW.LlmNarrationClient(
        model=model, max_tokens=OUTPUT_CAPACITY_CLAIM_ENTAILMENT,
        thinking={"type": "disabled"},
        max_tokens_by_prompt_version=output_capacity_policy()["by_prompt_version"],
        reject_truncated=True)
    return (narrator, CEE.LlmEntailmentClient(narration_client=narrator),
            FSF.LlmSentenceFidelityClient(narration_client=narrator))


def _print(message: str) -> None:
    print(message, file=sys.stderr)


# ---------------------------------------------------------------------------
# 复核一个已存在的 run 目录（只读）
# ---------------------------------------------------------------------------

def _produced_sections(root: dict) -> list[str]:
    """报告里 A4 记下的「真的产出过的节」（`commit_counts` 的键，按节名排序）。"""
    for entry in root.get("gates", ()):
        if entry.get("gate_id") != "A4":
            continue
        counts = (entry.get("evidence", {}) or {}).get("commit_counts") or {}
        return sorted(counts)
    return []


def _draft_root_counts_readonly(db: Path, section_ids: "list[str] | tuple[str, ...]") -> dict:
    """只读打开 run 库，按节数 `current_section_draft_v2` 的当前行数。

    刻意**不**经 `sections.store`：`init_db` 会建表、迁移并切 WAL，那是写操作——复核别人的
    run（尤其是历史 run）时不得留下任何字节。用 `mode=ro` URI 直连，写不进去。
    """
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return {section_id: int(conn.execute(
            "SELECT COUNT(*) FROM current_section_draft_v2 WHERE section_id = ?",
            (section_id,)).fetchone()[0]) for section_id in section_ids}
    finally:
        conn.close()


def validate_only(run_dir: Path) -> int:
    root = json.loads((run_dir / "acceptance_report.json").read_text(encoding="utf-8"))
    index = json.loads((run_dir / "artifact_index.json").read_text(encoding="utf-8"))
    problems: list[str] = []
    if root.get("schema_version") != ACCEPTANCE_REPORT_SCHEMA_VERSION:
        problems.append(f"acceptance_report.schema_version="
                        f"{root.get('schema_version')!r} 不是当前版本")
    if root.get("runner_version") != RUNNER_VERSION:
        problems.append(f"runner_version={root.get('runner_version')!r} "
                        f"不是当前 runner 版本 {RUNNER_VERSION!r}")
    for entry in index.get("artifacts", ()):
        path = run_dir / str(entry["artifact"])
        if not path.exists():
            problems.append(f"{entry['artifact']} 缺失")
            continue
        if _sha256_file(path) != str(entry["sha256"]):
            problems.append(f"{entry['artifact']} 的 sha256 与索引不符（内容被改过）")
    trust = root.get("trust_roots_after")
    if not trust:
        problems.append("报告里没有 trust_roots_after，无法复核信任根零改动")
    else:
        from planning import demo_scope as SC
        # 复核用的是**本进程所选** profile：双节 run 的报告里记的是双节 profile 的哈希，
        # 不带 `--demo-scope-profile` 来复核就会在这里如实报「信任根现在与报告记录不符」，
        # 而不是静默地拿旧三节 profile 去对、然后判它「一致」。
        current = _trust_root_hashes(SC.load_demo_scope_profile(active_profile_path()))
        for name, value in sorted(trust.items()):
            if current.get(name) != value:
                problems.append(f"信任根 {name} 现在与报告记录不符")
    db = run_dir / "section_chain_v2.db"
    if db.exists():
        # 只读复核必须**真的只读**：旧写法先 `store.init_db(db)`（建表 + 迁移 + WAL），
        # 那不是复核、是改历史 run；而且它随后读的 `row_counts["current_section_draft_v2"]`
        # 根本不在 A4 证据里（A4 数的是各 member family，root 不在其中），于是每个 run 都
        # 恒定报 3 条假问题、`ok=false`。这里直连只读，按节直接数 draft root。
        produced = _produced_sections(root)
        if not produced:
            problems.append("报告里没有 A4 的 commit_counts，无法复核 draft root 行数")
        else:
            try:
                counts = _draft_root_counts_readonly(db, produced)
            except Exception as exc:  # noqa: BLE001 — 复核不了如实说，不当成通过
                problems.append(f"只读复核 draft root 失败：{type(exc).__name__}: {exc}")
            else:
                for section_id, rows in sorted(counts.items()):
                    if rows != 1:
                        problems.append(f"{section_id} 的 draft root 行数 {rows} != 1")
    else:
        problems.append("section_chain_v2.db 缺失")
    out = {"run_dir": str(run_dir), "status": root.get("status"),
           "gate_summary": root.get("gate_summary", {}),
           "problems": problems, "ok": not problems}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not problems else 1


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="M930-3 真实写作主链验收 runner（evaluation-only，只读真实库）")
    parser.add_argument("--mode", choices=(MODE_OFFLINE, MODE_REAL), default=MODE_OFFLINE,
                        help="offline = 确定性生成器；real = 真实 LLM（须先离线通过）")
    parser.add_argument("--model", default=None,
                        help="仅 real 模式；必须等于已批准模型，否则拒绝")
    parser.add_argument("--run-id", default=None, help="opaque run 标签；缺省按 UTC 时间戳生成")
    parser.add_argument("--subject", default=None,
                        help="本次报告输入声明的**主体** company_id（必填）：报告写给谁由输入"
                             "决定，不由财务库的排序决定")
    parser.add_argument("--subject-name", default=None,
                        help="本次报告输入声明的**主体名称**（必填，`subj-2`）：读者面要用可核实"
                             "的公司名称，而不是把证券代码当名字。这里只**声明**，权威库负责核对")
    parser.add_argument("--subject-scope", default=None,
                        help="可选收窄：同一主体有多条 current 快照时必须说清楚用哪一条")
    parser.add_argument("--subject-currency", default=None, help="可选收窄（同上）")
    parser.add_argument("--subject-purpose", default=None, help="可选收窄（同上）")
    parser.add_argument("--subject-snapshot-as-of", default=None,
                        help="可选收窄：本次报告用**哪一期**财务快照（快照选择日）")
    parser.add_argument("--results-root", default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--demo-scope-profile", default=None, metavar="PATH",
                        help="本次运行使用的 demo scope profile（缺省 = "
                             "planning/demo_scope.DEFAULT_PROFILE_PATH，即旧三节）。"
                             "节序**只由所选 profile 的 selected_section_ids 派生**；"
                             "复核一个 run 时必须传**它当时用的同一个** profile，"
                             "否则信任根对账会如实报不符")
    parser.add_argument("--validate-only", default=None, metavar="RUN_DIR",
                        help="只读复核一个已存在的 run 目录")
    args = parser.parse_args(argv)
    # 只改本进程内的取值：双节演示入口与旧三节走的是**同一条**运行链，不是第二套运行时。
    global _DEMO_SCOPE_PROFILE_PATH
    if args.demo_scope_profile:
        _DEMO_SCOPE_PROFILE_PATH = str(args.demo_scope_profile)
    results_root = Path(args.results_root)
    if not results_root.is_absolute():
        results_root = REPO / results_root
    try:
        if args.validate_only:
            return validate_only(Path(args.validate_only))
        if not str(args.subject or "").strip():
            # 主体是**输入**：没有声明就没有这次报告。这里拒绝而不是猜一个默认值，否则
            # 「谁被写」会重新由代码的缺省值决定。
            raise AcceptanceRefusal(
                "--subject 必填：本次报告输入必须声明主体（不得由财务库排序或目录名决定）")
        if not str(args.subject_name or "").strip():
            # 名称同样只由输入声明（`subj-2`）：允许「只印代码」就等于允许读者面拿标识冒充
            # 公司名；而替它从库里挑一个名字，又等于让库决定「谁被写」。两者都不做。
            raise AcceptanceRefusal(
                "--subject-name 必填：本次报告输入必须声明主体名称（读者面不得只印证券代码，"
                "也不得由库替声明补一个名字）")
        return execute_run(run_id=args.run_id, results_root=results_root,
                           mode=args.mode, model=args.model, subject=args.subject,
                           subject_name=args.subject_name,
                           subject_scope=args.subject_scope,
                           subject_currency=args.subject_currency,
                           subject_purpose=args.subject_purpose,
                           subject_snapshot_as_of=args.subject_snapshot_as_of)
    except AcceptanceRefusal as exc:
        _print(f"REFUSED：{exc}")
        return 0


if __name__ == "__main__":  # pragma: no cover - 入口
    raise SystemExit(_main())

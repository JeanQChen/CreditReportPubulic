"""M930-3 Pack Writer：把**已封存的权威输入**写成**门前候选提案束**（§四/§六/§16.7.1 P6）。

职责（且只有这些）：
    SectionTask + producer_kind 匹配的封闭 `WorkerAuthorityInput` + ContractProjection
    + WritingSpec + PresentationProfile + 版本化 prompt/model policy
      → ClaimCandidate[] / NarrativeDraftUnit[] / ProposedSupportRef[]
      → exact material manifest + WriterMaterialProcessingDisposition[]
      → SectionUnresolved[] + FollowUpNeed[] + 候选态 SectionDraft + NarrativeGateResult

硬边界（§四 / §0.13）：
1. **先候选与类型化支撑图，后定稿**。每条候选都必须能沿
   `Cluster → proposal → 权威容器 → fact/material → citation/locator`
   回到权威；绝不用文本相似度回填血缘，也绝不事后补引用。**定稿 Claim / final Narrative /
   `SectionResult` / accepted binding / 任何决定都不在本模块产生**——它们是门后（P8/P9/P10 与
   P15 coordinator）的产物，本模块只交出候选与提案。
2. **生产者隔离**：company/industry 只消费 exact-topic-set `VerifiedPackSet`；financial 只消费
   任务绑定的 `FinancialPackArtifact` +（恰有其一）`ValidatedEvidenceNoteFactSet` / `EvidenceNoteGap`；
   derived 只有被 DemoScope 选中且上游身份完整时才成立。混装一律 fail-closed。
3. **不检索**：本模块不 import、不持有、不调用 Router / ToolRegistry / 任何检索入口；
   唯一外部能力是注入的 `NarrationClient`（只做一次文本生成）。需要更多材料时**只能**发出
   `FollowUpNeed`（独立身份、与 draft 并列、不进 draft 的 identity_body），不联网、不自批、
   不回写 Pack。
4. **不计算、不发明**：不产生新事实/数字/主体/期间/结论，不做比率、增速、合计或比较；
   所有数字都必须由权威事实绑定（由 `narrative_schema.gate_draft` 独立复核）。
5. **不丢材料、不美化缺口**：本次写作消费的**精确**材料清单（available）由 authority 确定性
   派生，每个成员**恰一条** `WriterMaterialProcessingDisposition`，且满足
   `processed = available` / `used ∩ not_used = ∅` / `used ∪ not_used = available`；
   `partial/blocked/not_found` 按权威原样保留，不得改写成 covered，也不得把「未取得」写成
   「不存在」。**`not_used` 不是 gap**。
6. **不自评**：本模块只产出候选束与确定性硬门结果 `NarrativeGateResult`；
   `SectionEvaluation`、entailment 决定与最终放行各有独立 owner（§六 / P9 / P11）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from contracts import schema as CS
from contracts import schema_v2 as CS2
from harness import topic_schema as TS
from llm import client as llm
from planning import schema as PS
from sections import common as SC
from sections import financial_pack_artifact as FPA
from sections import material_context as MC
from sections import narrative_schema as NS
from sections import pack_set as PSet
from sections import presentation_profile as PP
from sections import schema as SS
from sections import source_role_scope as SRS
# 只读导入：producer_kind → 章节前缀 的权威映射声明在 frozen WritingSpec 模块里，
# 本模块不重定义（CLAUDE.md「不启用第二套」）。
from sections.writing_spec import _PRODUCER_KIND_PREFIXES
# 只读导入：章节状态的**唯一**派生（§七 P0-4）在 canonical Worker 共享模块里，本模块不得
# 再写第二套（只取 `derive_status` 一个纯函数，不复制任何研究运行时）。
from sections import research_common as RC

logger = logging.getLogger("sections.pack_writer")

#: `pw-3`：写作器改出**门前候选提案束**（`ClaimCandidate` / `NarrativeDraftUnit` /
#: `ProposedSupportRef` + exact manifest/WMPD + 可选 `FollowUpNeed`），不再直接构造定稿
#: `SectionClaim` / `ClaimSupportRef` / final Narrative / `SectionResult`。`writer_policy_version`
#: 参与 Draft 身份，故必须升版，否则「同一个 Draft 身份对应两种不同的产出形态」。
#:
#: `pw-4`（M930-3 §三 A）：写作器收到的 materials 从「只有 material ID 的一行」升级为
#: **真实正文读视图**（`text` / 表格类 `structured` / `locator_ref` / `payload_hash` /
#: `reading_view_fingerprint`）。`writer_policy_version` 参与 Draft 身份，输入面与行为面都变了，
#: 故必须升版：旧的 `pw-3` 身份对应「只看得到 ID 的输入」，新的 `pw-4` 对应「看得到正文的输入」，
#: 两者不得共用一个 Draft 身份。
#: `pw-5`（M930-3.2 §二 / P0）：路径 B 的授权面判据从「高风险 token 在材料正文里逐字在场」
#: 改成「候选文本**不得携带任何**高风险表面」。同一份模型输出在 `pw-4` 与 `pw-5` 下可能一个
#: 成 Draft、一个被打回重写，因此 `writer_policy_version` 必须前进：它参与 Draft 身份，两种
#: 行为面不得共用一个身份。
#: `pw-6`（M930-3 §三 A / 3.1）：请求面里 `projection.aspects` 从**裸 `aspect_id` 列表**升级为
#: **逐 aspect 的要求行**（Contract 逐字的 `requirement_text` + WritingSpec 逐字分配的槽位：
#: `content_role` / `table_schema` / `citation_granularity` / `display_tier` /
#: `gap_display_policy` / `period_language_policy` + 原样状态）。生成器看到的「要写什么」变了，
#: 同一个模型输出在 `pw-5`（只看到 id）与 `pw-6`（看到要求与槽位）下会不一样，因此
#: `writer_policy_version` 必须前进：它参与 Draft 身份，两种输入面不得共用一个身份。
#: `pw-7`（M930-3 §三 A / 3.3）：**按栏目分步写作**。当一束结构合法、身份也闭得上的提案集
#: 对某个 Contract 栏目零产出时，链路会记一条 `subsection_uncovered` typed 拒绝，并发出一次
#: **栏目定向**的完整提案请求（原请求逐字保留 + 逐个列出漏掉的栏目及其逐字要求）。生成器
#: 看到的输入面与第一次不同（多了一段定向说明），拿到同一份模型也应当给出不同的提案集，
#: 因此 `writer_policy_version` 必须前进：它参与 Draft 身份，两种输入面不得共用一个身份。
#: `pw-8`（M930-3 §三 A / 3.6）：**代理口径必须显式标记**。财务权威事实的**权威表面**
#: （`NS.authoritative_fact_surface`）现在把权威自己的口径限定语（`CALCULATED_PROXY` 事实的
#: `note`，如「代理口径（PROXY_INPUT）」）附在断言句之后，`facts_payload` 里那一行的 `text`
#: 因此带着它；模型被要求逐字保留，**本条链**上的硬门是 `narrative_schema.gate_draft` 的
#: `narrative_proxy_fact_unmarked`（blocking，随正文闸一起跑）；`rules_evaluator.proxy_not_marked`
#: 是同一口径的**第二道网**，只对 `service.py` 那条会传 `fact_pack` 的旧链生效，不在本链上。生成器
#: 看到的权威事实行变了，同一份模型输出在 `pw-7`（表面无口径限定语）与 `pw-8` 下会不一样，
#: 因此 `writer_policy_version` 必须前进：它参与 Draft 身份，两种输入面不得共用一个身份。
#: `pw-9`（M930-3 返修 P2 §三 2.4 / §三 2.2）：生成器看到的**资产本身**换成了 v5——路径 A
#: 与栏目覆盖的口径统一为「**有权威事实的**栏目至少一条路径 A」，并明确「路径 A 为空时不得用
#: 路径 B 写数字，改为缺口 + `follow_up_needs`」；候选文本被要求写成**不带句末标点的原子子句**
#: （带句末标点的候选串起来就是 `A。同时B。` 双句容器，已被冻结判据 `ng-8` 拒）。同一份模型
#: 在 `proposals-6` 与 `proposals-7` 两种输入面下会给出不同提案集，因此版本必须前进：
#: 它参与 Draft 身份，两种输入面不得共用一个身份。
#: `pw-10`（M930-3 返修 P3 §二：**分批请求**）：一次生成不再是「整节一次请求」，而是按
#: **确定性的 Contract aspect 范围**分成若干批（`ASPECT_BATCH_POLICY_VERSION`/`wbatch-1`），
#: 每批只回答本批的 aspect，材料与事实目录**完整**不变。生成器看到的输入面因此多了一段
#: `batch`（本批 id / index / total / aspect_ids / 范围规则），同一份模型在 `pw-9`（无批次）
#: 与 `pw-10` 下会给出不同的**提案集划分**，因此版本必须前进：它参与 Draft 身份，
#: `pw-11`（M930-3 返修 ③：**内容形态分流**）：materials 行新增 `content_qualification`——
#: §二 2.3 的形态读法随正文一起到达生成器。`kind=selection_form` 的行连同所问事项/选项/
#: 选中状态/所在节点/来源/尾随内容六列、允许用途（`asked_item_applicability_only`）与排除项
#: 一起投出（`pw-13` 起第六列是 `trailing_content`，`tmr-2` 的读法）；
#: `text` 一格不改（仍是抽取式原文）。生成器由此看到「这一份是表单行，不是叙述材料」，
#: 与 `pw-10`（形态读法到不了这一层）会给出不同提案集，因此版本必须前进：它参与 Draft 身份，
#: 两种输入面不得共用一个身份。
#: `pw-12`（M930-3 返修 §二：**支撑选项短别名 + 本批输出范围**）：materials 行与
#: authority_facts 行各自带一个**预先声明**的短别名 `ref`，支撑边改写成「`ref` + factual 边的
#: `support_role`」，身份由系统从被引用的那一行确定性展开（`SupportAliasTable`，策略
#: `saref-1`）；同时 `narrative_draft_units` / `follow_up_needs` 的**输出范围**收窄到本批
#: aspect 所属的 topic（不再每批重复承担整节），`must_use_facts` 的义务也随批结算。生成器
#: 看到的输入面与输出面都变了，同一份模型在 `pw-11` 与 `pw-12` 下会给出不同的提案集与不同的
#: 输出量，因此版本必须前进：它参与 Draft 身份，两种输入面不得共用一个身份。
#: `pw-13`（M930-3 定点返修 ②：**表单行的支撑资格 + 定向说明 `hrrp-2`**）：materials 行的
#: `content_qualification` 读法变了——勾选表单行的形状判据改为「选项串 + 尾随所述内容」，
#: 选项串之后那段内容以 `trailing_content` 列随行留档并**排除**在支撑之外（`tmr-2`）；同时路径 B
#: 新增一条**支撑资格**判据（`asked_item_applicability_only` 的表单行只支撑逐字限定在其所问
#: 事项内的叙述），被点名时的说明里多一条出路。生成器看到的内容形态与受到的纪律都变了，
#: 同一份模型在 `pw-12` 与 `pw-13` 下会给出不同提案集，因此版本必须前进：它参与 Draft 身份。
#: `pw-14`（M930-3 返修 ④：**来源角色的期间位次进输入面 + O-12 期间纪律**）：materials 行新增
#: `source_role`——`srsc-1` 的期间/来源角色核对里，「同类较旧材料不得写成当前状态」这一条要在
#: **候选**那一侧可执行，就得让生成器看见每一份材料在本系列里的位次（行里原有来源身份与文档
#: 定位，读不出角色）。同时路径 B 新增一条**期间纪律**：全部支撑边落在
#: `history_and_conflict_source` 上、而文本自己又没有期间限定的候选，按 `srsc-1` 打回
#: （`path_b_history_only_current_state`，与门的分级 `hrrp-3` / `cco-2` 同批；那条说明文本随后
#: 又随 `srsc-2` 增列一组点名而升到 `hrrp-4`，见 :data:`HIGH_RISK_REPROPOSAL_NOTE_VERSION`）。
#: 生成器看到的
#: 输入面与受到的纪律都变了，同一份模型在 `pw-13` 与 `pw-14` 下会给出不同提案集，
#: 因此版本必须前进：它参与 Draft 身份，两种输入面不得共用一个身份。
#: `pw-15`（指令 E 第 3 项：**恢复材料驱动写作**）：输出面从「候选清单 + 门前叙述单元」改为
#: **先草稿、后候选**——模型先写一段可保留的**自然散文草稿**（`natural_prose_draft`，逐单元声明
#: 它用了哪几份材料、表达了哪几个事实原子的候选键），再为草稿里的每个事实原子单独提交
#: `ClaimCandidate` + 独立 `ProposedSupportRef`。生成器看到的输出契约变了（多一个**先于**候选的
#: 顶层键与配套纪律），同一份模型在 `proposals-10` 与 `proposals-11` 下会给出不同的输出量、
#: 不同的候选切分，因此版本必须前进：它参与 Draft 身份（`draft_revision` 里还多一项草稿层摘要），
#: 两种输出面不得共用一个身份。
#:
#: **本批（`srsc-2`）不推进它**：这一批**没有**改模型看到的输入面——materials 行、请求体、
#: 提示词文本一字未动，同一份模型在 `pw-14` 下给出的还是同一份提案集。变的只是**我们**收不收
#: 其中几条（新增一条 `path_b_unproven_current_state` 拒绝原因）。`pw-14` 的判据是「同一份模型
#: 在前后两个版本下会不会给出不同提案集」，按这条判据它不动；被拒那几条带来的下游身份变化由
#: 离线重放的**记录值**如实反映，不由版本串承担（`srsc-1` 接线那一次同形）。同时，
#: `HIGH_RISK_REPROPOSAL_NOTE_VERSION` 升到 `hrrp-4`：那一串文字确实多了第四组点名与第 4b 条
#: 出路，而它逐字进下一轮请求，必须可版本化。两个版本串一动一不动，是两条判据各自的结果。
#:
#: `pw-16`（M930-3「真实材料驱动成稿」定点返修 P1-a/P1-b/P1-c）相对 `pw-15` 的差别是
#: **输出面的自相矛盾被消除 + 缺草稿成为 typed failure + 草稿出处的第二条轴**：
#:   ① `pw-15` 的请求体里 `output_schema` **没有列** `natural_prose_draft`，而同一份请求的
#:      `rules` 逐字要求「不得输出 `output_schema` 之外的字段」——于是「先写自然草稿」这条
#:      指令在**同一份请求内部**被判为违规。`pw-16` 把该键（连同它的单元字段与两条出处轴）
#:      列进 `output_schema` 的**第一位**，并就地断言 `tuple(output_schema) == _PLAN_KEYS`：
#:      形状与键序从此由代码而不是由人的记性保证。
#:   ② `pw-15` 的解析对**缺** `natural_prose_draft` 是静默容忍的（`value is None → continue`），
#:      于是「有候选、无草稿」会一路走到「空草稿 + Claim 拼文」。`pw-16` 把这一形状定为
#:      **typed failure**（`natural_prose_draft_missing`），只有显式声明的历史兼容线
#:      （`proposal_wire="proposals-10"`，且仅限离线 stand-in）才回到旧行为。
#:   ③ 财务节的精确材料清单**合法地为空**（`FinancialFactPack` 的事实不经过 Pack 材料），
#:      而 `pw-15` 的草稿单元只有 `source_member_refs` 一条出处轴，于是财务节**不可能**写出
#:      合法草稿。`pw-16` 引入第二条互斥轴 `source_fact_refs`（`pprov-1`，权威事实行出处），
#:      它**只说出处**：事实的授权仍然只来自独立的原子 `ClaimCandidate`、路径 A 支撑边与
#:      后续决定，不由出处轴授权。
#: 三条都是**我们**收什么、请求里写什么的改变（同一份模型在新旧两版下给出的提案集不同），
#: 按 `pw-14` 的同一条判据必须前进。
#:
#: `pw-17`（M930-3 定点返修 P2：源句自带期间/范围限定被截掉）相对 `pw-16` 的差别是
#: **同一条拒绝原因下的分级 + 定向重提案说明的实质变化**：
#:   ① 拒绝原因 `path_b_unproven_current_state` 现在逐条带一个**类型化原因码**
#:      （`unproven_current_state_cause_code`，闭集 `UNPROVEN_CURRENT_STATE_CAUSES`）：
#:      `not_extractive_in_any_current_source`（材料不足）与 `source_period_scope_dropped`
#:      （有材料包含它，但只有**截掉源句自带期间/范围限定**的那种包含）。
#:   ② 定向重提案说明（`hrrp-4` → `hrrp-5`）把后一种单列一条出路：**把限定语一起收进候选**即可，
#:      不必改绑、也不必撤下。这与 `pw-16` 的三条同类——**我们收什么、请求里写什么**都变了，
#:      同一份模型在新旧两版下给出的提案集不同。
#:
#: `pw-18`（M930-3 r8 前的最小请求自洽修正）相对 `pw-17` 的差别是**输出面示例的键值不再与同一份
#: 请求的 rules 相矛盾**，与 `pw-16` ① 是**同一类缺陷的另一处**：
#:   * `pw-17` 的 `output_schema.natural_prose_draft` 示例把 `source_member_refs` 与
#:     `source_fact_refs` **同时**填成非空占位串，而同一份请求的 rules（以及 prompt 资产
#:     `proposals-12` 那一节）逐字要求两条轴**互斥：恰有一条非空**。照抄示例即被同一份请求判
#:     违规（`_build_natural_prose_units` 对「两条轴同时非空」是整批拒绝），不照抄又要自己猜到
#:     该反着写——模型在同一份请求里被要求做一件示例说不能做的事。
#:   * `pw-18` 让示例**按本请求的输入面**选那条合法轴：本节有材料行 ⇒ 材料轴（事实轴取空），
#:     本节只有权威事实行（财务节的常态）⇒ 事实轴（材料轴取空）；两张表都空时不展示任何单元
#:     （那一档没有可用内容，草稿与候选都留空——示例不得给出一条无从展开的轴）。
#:     选择结果在**构造请求时**就地核对（`_assert_prose_example_consistent`）：一份示例与自己的
#:     输入面矛盾的请求同样不该发出去。
#:   * 判据是 `pw-14` 那一条照旧适用：同一份模型在新旧两版下给出的提案集**会**不同——照抄示例
#:     的那一束在 `pw-17` 下整批被拒，在 `pw-18` 下是一条合法草稿单元。因此版本串必须前进。
#:   * 这一次**只有** `pw-*` 前进：prompt 资产的正文一字未改（`proposals-12` 与它的正文指纹
#:     `NARRATION_PROMPT_SHA256` 都不动），线格式键集/键序也不动（`proposals-12`、
#:     `tuple(output_schema) == _PLAN_KEYS` 照旧）——变的是示例的**取值口径**，属于 `pw-*` 面。
#:
#: `pw-19`（M930-3 r8 前最后一次 Writer 请求契约窄修）相对 `pw-18` 的差别是**同一类缺陷的剩余
#: 三处被一次修尽**：`pw-18` 只把 `natural_prose_draft` 的示例按输入面分档，而同一份请求的
#: **其余三格**（候选的 `support` 首边、`narrative_draft_units` 的 `context_support`、补件示例的
#: `budget_hint`）仍按「两张表都存在」写死，于是：
#:   ① 材料轴节（公司/行业只有材料行）里，候选示例给的是一条**不存在的事实行**（`f1`）——
#:      `pw-18` 让草稿走材料轴，却没有让候选边跟着走：照抄示例仍是整批被拒。
#:   ② 事实轴节（财务节，`materials` 合法为空）里，context 示例给的是一条**不存在的材料行**
#:      （`m1`）——context 边必须绑定真实材料，没有材料行就没有可展开的边，合法形状是 `[]`。
#:   ③ 补件示例给的 `budget_hint` 是空串，而同一份请求的 rules 与检索执行门都要求非空；
#:      且 `TS.FollowUpNeed` 的 schema **不查**这一格，于是空值会一路走到 Harness 获批执行时
#:      才抛 `TopicRuntimeError`。本批同时补一条**入站防线**：Writer 侧逐条把它判成 typed 拒绝
#:      （`budget_hint_empty`），保留审计、不牵连同一束里合法的其余诉求、更不丢掉已合法成形的
#:      整节草稿——「一条写错的申请」与「这一节的候选不成立」是两件事（与 §六 同一条纪律）。
#: 这三处都是**我们收什么、请求里写什么**的改变（同一份模型在新旧两版下给出的提案集不同），
#: 按 `pw-14` 的同一条判据必须前进。同时按仓库版本纪律**新建** prompt 资产
#: `pack_section_writer_proposals_v9`（修订号 `proposals-13`），v8 一字不动作只读基线。
#: 线格式**键集/键序未变**（`natural_prose_draft` 仍在首位、`tuple(output_schema) == _PLAN_KEYS`、
#: `_PROSE_KEYS` / `_UNIT_KEYS` / `_FOLLOW_UP_KEYS` 一字不动），因此 `PROPOSAL_WIRE_CURRENT`
#: 仍是 `proposals-12`——**不为凑版本而改 wire**：prompt 修订号说的是「模型被要求输出什么」，
#: 线格式说的是「解析器接受什么形状」，本批改的是前者。
#: `pw-20`（M930-3 r8 后业务纵链收口 §一）与 `pw-19` 的差别是**逐批支撑范围**：
#: ① `build_narration_messages` 在分批时多投出一份**确定性**读数 `batch_support_scope`
#:    （`bscope-1`：本批 topic 的 `material_refs`、每个 aspect 的 `fact_refs` 与
#:    `has_usable_row`、`unbacked_aspect_ids`、`batch_has_usable_rows`）；
#: ② 解析侧新增 `assert_batch_scope_respected`：**本批所有 aspect 都没有可引用的行**时，
#:    三个内容键必须为空数组（`bscope-1`）；且本批任何被引用的行必须属于本批 topic。
#:    它是**确定性核验**，不是宽容化：违反仍走既有的 `schema_invalid` 通道（不新增额度、
#:    不静默删草稿、不伪造引用），只是把「本批无行可引用却写成事实性草稿」这件事从
#:    「靠模型自觉」变成「写侧可判」。
#: ③ 新建 prompt 资产 v10 / 修订号 `proposals-14`（v9 一字不动作只读基线）。
#: 线格式键集与键序**一字未变**，故 `PROPOSAL_WIRE_CURRENT` 仍是 `proposals-12`。
#: `pw-21`（指令 D §二·三条日期轴）与 `pw-20` 的差别**只在提示词那一侧**：新建 prompt 资产
#: v11 / 修订号 `proposals-15`（v10 一字不动作只读基线），新增「材料披露的写法：三条日期轴」
#: 一节（归属语不由写者写、不得不成期间地写成「一直如此」、新旧实质差异不得抹平、新闻事件日
#: 与发布日分开、披露日未知就标未知、`report_as_of` 不写）。**请求面的键集与键序一字未变**、
#: 解析侧判据一字未动：因此线格式仍是 `proposals-12`，而写作策略版本必须前进——它覆盖的是
#: 「本轮写作策略是什么」，提示词资产的身份就是它的一部分（同名同修订号的两份不同正文会得到
#: 同一个 prompt 身份，那是既有纪律禁止的）。
#: `pw-22`（M930-3 r9 后返修 B：门前草稿的「一条原子、多处表达」）与 `pw-21` 的差别是**写作纪律
#: 与闭合口径的同步**：新建 prompt 资产 v12 / 修订号 `proposals-16`（v11 一字不动作只读基线），
#: 把两处「草稿里声明的原子键必须与候选集**一一对上**」改写成「键**集合**相等，但同一条候选
#: **允许**出现在多个草稿单元里」——多 occurrence 不是免检通道，闭合核对逐个 occurrence 拿该
#: 候选**自己**的支撑边去核对这一段声明的出处（材料 ID / 来源身份 / 来源角色，事实轴比事实行），
#: 错来源、错版本照样拒；同一份来源里写两遍仍然没有意义，把两年相似表述并成「始终如此」仍然
#: 不行。**触发这件事的真实现场**：r9 四批保存字节里，材料驱动的公司草稿把同一个原子在两份
#: 材料里各写了一次，`pw-21` 的唯一性口径把这种**正常写法**判成「候选未被任何自然草稿单元声明」，
#: 于是整节在门前闭合失败、连 20 条 `FollowUpNeed` 一起蒸发。本批的解法**不是**删掉唯一性断言
#: （`npr-0` 那条严格读法仍在，作为核对面无从展开时的 fallback），也不是把支撑搬进 Claim 身份，
#: 而是把「一条候选只能有一处表达」换成「每一处表达都要对得上该候选自己的支撑」。
#: 请求面的键集与键序一字未变、解析侧键集一字未动：因此线格式仍是 `proposals-12`，
#: 而写作策略版本必须前进——prompt 资产的正文与修订号都变了，prompt 身份就是写作策略身份的一部分。
PACK_WRITER_POLICY_VERSION = "pw-22"
#: prompt **资产名**（→ `llm/prompts/<name>.txt`）。§16.7.1 P18：**新增资产**，旧的
#: `pack_section_writer_v1`（organizer 系列）**不原位修改**，只作格式与纪律参照。
#: `proposals_v2` 与 `proposals_v1` 的差别是**输入面**：v1 的 materials 行只有身份字段，v2 的
#: 行携带真实正文读视图，并明确「路径 A 为空不妨碍合法的路径 B」。旧资产同样不原位修改。
#: `proposals_v3`（M930-3.2 §二/§三）与 `v2` 的差别是**输出纪律**：路径 B 明确为「非高风险
#: 描述性原子」（高风险表面即使逐字存在于材料正文也不得写入路径 B 候选），并新增**原子性**
#: 一节（一条候选恰一个可独立判断真假的断言；不得用逗号把两个断言拼成一条）。旧资产不原位修改。
#: `proposals_v4`（M930-3 §三 A / 3.6）与 `v3` 的差别是**口径纪律**：路径 A 明确要求逐字保留
#: 权威事实文本里的**口径限定语**（「代理口径（……）」），省略或改写成「精确」「准确」「权威」
#: 都会被硬门打回。旧资产同样不原位修改。
#: `proposals_v5`（M930-3 返修 P2 §三 2.4 / §三 2.2）与 `v4` 的差别是**口径统一 + 子句纪律**：
#: ①「路径 A 可为空不妨碍合法的路径 B」与「每一个栏目都应当至少有一条路径 A」这对自相矛盾的
#: 要求被重写成**按栏目可用内容分档**的一条口径（有权威事实的栏目 ≥1 条路径 A；只有材料的
#: 栏目走路径 B；两边都没有的如实发 `follow_up_needs`），并明确「路径 A 为空时不得用路径 B
#: 写数字——哪怕材料正文里逐字存在、哪怕该栏目是必写正文」；②新增「候选文本的写法」一节：
#: `claim_text` 是不带句末标点的**原子子句**（自带句末标点的候选串起来就是双句容器）。
#: 旧资产同样不原位修改。
#: `proposals_v6`（M930-3 返修 P3 §二）与 `v5` 的差别是**请求面分批**：新增「分批请求
#: （输入里的 `batch`）」一节，明确本批只回答 `projection.aspects` 列出的那些要求、
#: 事实与材料目录仍是**完整**的那一份、`narrative_draft_units` 与 `follow_up_needs` 不受
#: 本批 aspect 范围限制、各批输出会被合并成一份完整有序提案集；并把栏目定向说明下
#: 「输出完整 JSON」的语义收窄为「本批范围内完整」。旧资产同样不原位修改。
#: `proposals_v7`（M930-3 返修 §二）与 `v6` 的差别是**输出面收窄**：新增「支撑选项的短别名
#: （`ref`）」一节——支撑边只写 `{"ref": ...}`（factual 边另加 `support_role`），
#: `authority_kind` / `container_id` / `fact_id` / `material_id` / `support_semantics` /
#: `authorization_path` 一律由系统从被引用的那一行展开，模型自己写这些字段**即被拒**；
#: 并把「分批」那一节里「`narrative_draft_units` 与 `follow_up_needs` 不受本批 aspect 范围
#: 限制」改成本批范围结算（本批只为本批 aspect 所属 topic 写草稿单元与补件诉求，
#: `must_use_facts` 只对本批那一部分负责）。旧资产同样不原位修改。
#: `proposals_v8`（指令 E 第 3 项）与 `v7` 的差别是**写作顺序**：新增「先写自然草稿、再逐原子
#: 提交候选」一节——`natural_prose_draft` 是输出里的**第一个**顶层键，每个单元声明它的材料出处
#: （`materials` 的 `ref`，不得取事实行）与它表达的候选键；候选随之为**原子**提交，且必须
#: 覆盖草稿声明的全部原子（多一条、少一条都会被闭合核对拒）。`narrative_draft_units` 仍是
#: context-only 的衔接单元，两者不得混同。**`proposals-12` 起**，草稿的出处由「只能是材料」
#: 改为**两条互斥的轴**（材料行 / 权威事实行，恰有一条非空），并明确「出处不是授权」；
#: 同时把「`materials` 为空就不要产出任何候选」修正为「**两张表都空**才没有可用内容」——
#: 财务节（`materials` 合法为空而 `authority_facts` 有行）正是被那句话挡住、写不出草稿的。
#: `proposals_v9`（`pw-19`：r8 前最后一次请求契约窄修）与 `v8` 的差别是**输出面示例的剩余三格
#: 按输入面分档**：候选的首条支撑边、`narrative_draft_units` 的 `context_support`、补件示例的
#: `budget_hint`。v8 的示例隐含「两张表都存在」，于是材料轴节照抄会引用不存在的事实行、事实轴
#: 节照抄会引用不存在的材料行、补件照抄会带着空 `budget_hint`。v9 把示例写成**材料轴那一档**
#: （与本节有材料行时逐字可展开），并把事实轴那一档的三处替换逐条写清（`source_fact_refs`、
#: 事实行 `ref`、`context_support: []`）；补件示例的四个 id 改指 `requestable_aspects` 同一行，
#: `budget_hint` 给出非空的合法值。旧资产同样不原位修改。
#: `proposals_v12`（`pw-22`：M930-3 r9 后返修 B）与 `v11` 的差别是**草稿闭合的口径**：两份
#: 「声明的原子键必须与候选集一一对上」改写成「键**集合**相等 + 多 occurrence 逐处核验」。
#: 旧资产一字不动作只读基线。
NARRATION_PROMPT_ASSET = "pack_section_writer_proposals_v12"
#: 同一资产内的**修订号**。输出契约从「组织计划」变为「候选 + 提案 + FollowUpNeed」，
#: 行为面整体改变，因此必须带修订号，且旧资产名与新资产名必须可区分
#: （CLAUDE.md：Prompt 变更必须属获批批次并带版本与回归测试）。
#: `proposals-10` 相对 `proposals-9` 的差别：materials 行的字段说明里多了**来源角色**
#: `source_role`，并新增一条期**间纪律**——全部路径 B 支撑边落在同类较旧/期间不可核实的来源上
#: 时不得写成无期间限定的当前状态（与 `pw-14` 的输入面改动同批）。旧修订号不再使用。
#: `proposals-11` 相对 `proposals-10` 的差别：输出面里 `natural_prose_draft` 先于候选，并新增
#: 「先草稿、后候选」与「草稿的出处只能是材料」两节纪律（与 `pw-15` 的输出面改动同批）。
#: `proposals-12` 相对 `proposals-11` 的差别是**草稿出处的两条轴**（与 `pw-16` 同批）：
#: 「草稿的出处只能是材料」被改写成按**本节材料面是否为空**分档的一条口径——材料行非空时
#: 出处取 `materials` 的 `ref`（`source_member_refs`）；本节**只有权威事实行、没有材料行**
#: （财务节的常态，不是异常）时出处取 `authority_facts` 的 `ref`（`source_fact_refs`）。
#: 两条轴**互斥**：恰有一条非空。并明确「出处不是授权」：`source_fact_refs` 只说这段文字
#: 从哪条事实行长出来，该事实能否被写进正文仍由它自己的原子候选与支撑边决定。
#: `proposals-13` 相对 `proposals-12` 的差别（与 `pw-19` 同批）是**示例的其余三格按输入面分档**：
#: ① 候选示例的首条支撑边不再写死为事实行——样例走**材料行**（材料轴节逐字可展开），事实轴节
#: 换成事实行 `ref` 的替换逐字写明；② `narrative_draft_units` 的 `context_support` 在**没有材料
#: 行**的节里必须写 `[]`（context 边只能绑定真实材料，没有材料行就没有可展开的边）；③ 补件示例
#: 的四个 id 一律取自 `requestable_aspects` 的**同一行**（`authority_facts` 行里的同名
#: `target_requirement_id` 可能是空串，不得作为来源），`budget_hint` 给出**非空**的合法值。
#: 输出面的**键集与键序一字未变**：本批改的是示例的**取值口径**，不是线格式。
#: 旧修订号不再使用：同名同修订号的两份不同正文会得到同一个 prompt 身份。
#: `proposals-14` 相对 `proposals-13` 的差别（与 `pw-20` 同批）是**逐批支撑范围**这一层：
#: ① 新增一节「`batch_support_scope` 是本批的支撑范围声明」——引用限定在本批 topic 的行内，
#:    且**本批无行可引用时三个内容键一律空数组、只留 `follow_up_needs`**；
#: ② 硬性禁止里新增一条「不得把「本轮未取得 / 无法说明 / 未找到」这类**缺失陈述**当作事实性
#:    自然草稿写出来」（其后两条顺延编号）；
#: ③「两张表都为空」那一段补上「或本批 `batch_has_usable_rows=false`」这一档；
#: ④ 栏目覆盖的第三种情况写明「这一档不得产出任何草稿单元或候选」。
#: **触发这件事的真实现场**：r8 的 company 一节 4 批里有 3 批出现「无出处轴的缺失陈述草稿」
#: （`p9` / `p4` / `p1..p3`），前两批各花掉一次纠正额度后过关，第 4 批正好把共享额度用尽 ⇒
#: 整节 typed `schema_invalid` 失败。逐字原文见 `_M930_3_STOP_REPORT.md` §⑩。
#: 输出面的**键集与键序一字未变**：本批改的是**请求面**（多给一份确定性读数、多两条纪律），
#: 不是线格式——因此 `PROPOSAL_WIRE_CURRENT` 不动（不得为了凑版本号把 marker 抬上去）。
#: `proposals-15` 相对 `proposals-14` 的差别（指令 D §二·三条日期轴，与 `pw-21` 同批）是
#: **一般经营描述怎么落笔**这一层，新增一节「材料披露的写法：三条日期轴，一条都不能顶替另一条」：
#: ① **归属语不由写者写**——「据2024年年度报告披露」这类句子由系统在门后按已登记来源确定性
#:    附加（`sections/source_attribution.py`，`srattr-1`），写者补年份/期间/披露日来「说明来源」
#:    仍然是未授权表面；
#: ② **不得把「该材料披露的情况」升格成「一直如此」**——列出普遍化与否定式普遍化的词表，
#:    并写明「要写当前状态必须由 `current_state_source` / `topic_participating_source` 支撑」；
#: ③ **新旧材料存在实质差异时不得用相似文本抹平**；只有旧材料支持的内容须有历史来源归属或留缺口；
#: ④ **新闻类外部来源**：优先事件发生/生效日，只有发布日期时只能写「某日发布的报道提及……」，
#:    日期仍是高风险表面（要写它必须走路径 A）；
#: ⑤ 披露日未知就标未知（不得用上传日 / PDF 元数据 / 财务期末替代）；`report_as_of` 与上面
#:    三条都不是一回事，写者不写它。
#: **触发这件事的真实现场**：路径 B 候选一律不得含期间（高风险表面），于是材料驱动的一般经营
#: 描述只能写成无期间的无时间态措辞，读者读起来就是「一直如此」——这正是指令 D §二 3 点名的
#: 那个冲突。本批的解法**不是**放宽高风险门，而是把归属语从散文里挪到系统渲染的读者面。
#: 输出面的**键集与键序一字未变**：本批改的是**写作纪律**与**读者面归属语**，不是线格式——
#: 因此 `PROPOSAL_WIRE_CURRENT` 不动。
#: `proposals-16` 相对 `proposals-15` 的差别（与 `pw-22` 同批）是**草稿闭合的读法**：
#: ① 「草稿里声明的原子键必须与候选集**一一对上**」改为「键**集合**相等」，并写明同一条候选
#:    **可以**出现在多个草稿单元里——多条材料分别写出同一个原子是材料驱动的常态；
#: ② 多 occurrence 的判据写成**逐 occurrence 核验**：这一段声明的出处必须落在该候选**自己**的
#:    支撑边上（材料 ID、来源身份/报告期、来源角色；事实轴比事实行），错来源、错版本照拒；
#: ③ 「同一句话不要在两个单元里各写一遍」保留但收窄为**同一份来源内**的重复无意义，并补上
#:    「把两年的相似表述并成一句『始终如此』同样不行」。
#: **触发这件事的真实现场**是 r9 四批保存字节：材料驱动的公司草稿把同一个原子在两份材料里各写
#: 一次，`proposals-15` 的唯一性口径把这种正常写法判成闭合失败。本批改的是**写作纪律的表述**，
#: 输出面的键集与键序一字未变——因此 `PROPOSAL_WIRE_CURRENT` 不动（不得为了凑版本号抬 marker）。
NARRATION_PROMPT_REVISION = "proposals-16"
#: 进入产物 / 日志 / 报告版本身份的 prompt 身份 = 资产名@修订号。
NARRATION_PROMPT_VERSION = f"{NARRATION_PROMPT_ASSET}@{NARRATION_PROMPT_REVISION}"
#: 资产**正文**的内容指纹（sha256，行尾归一后）。§16.7.1 P18 要求新资产「必须被
#: `verify_prompt_asset` 的哈希校验覆盖」：只登记「资产名@修订号」不足以证明加载到的就是登记的
#: 那一份文本——同名同修订号的两份不同正文会得到同一个 prompt 身份，于是「记录版本 = 实际加载
#: 版本」变成一句空话。正文改动必须同时改这里，否则 fail-closed。
NARRATION_PROMPT_SHA256 = "2eeb22d4c8a410e59e271951110eea7e1ade29a2b3518b113c057dd5d07e0f67"
#: `pwr-2`：渲染器现在把表格的主体/期间/单位显式渲染进正文（§十一：元数据不得只藏在 JSON）。
#: `pwr-3`（§三 C）：final Narrative 升到 narr-5，出现 `composed` 句（多 Claim 组织成的自然句，
#: 可携带句级 context 绑定）。渲染器仍是**同一台**（`render_final_narrative_markdown` 按段落
#: 逐句拼接，不区分句类），但「同一份 Narrative 在两种句类下渲染」是新的行为面，版本必须跟上。
#: `pwr-4`（M930-3「先证明能成稿」定点批 §二·读者面）：读者看到的表格那一行变了 —— 单位按
#: `NS.reader_unit_text` 写成中文并与单元格显示一致、主体带出可核实公司名称、代理口径在表下
#: 补一句中文口径说明（措辞取自权威自己的公式登记表）。表格元数据里的 `unit` / `entity_scope`
#: 一字未动；**渲染文本变了，正文指纹随之变**，故版本必须前进，两份正文不得共用一个版本号。
#: `pwr-5`（M930-3 定点批 §四·财务来源读回）：读者看到的段落与表格各多一行 `- 来源：…`——
#: 公式**中文名**、公式版本与**中文期间**逐字取自权威自己的公式登记表与期间口径
#: （`NS.citation_source_notes`），期间、单位、代理口径说明的既有渲染一字未动。引用身份串本来
#: 就在 `NarrativeParagraph.citation_ids` / `NarrativeTable.citation_ids` 里，故 wire 未动；
#: **渲染文本变了，正文指纹随之变**，故版本必须前进，两份正文不得共用一个版本号。
#: 印不出中文来源的引用（`evidence:` / `external:` / 登记表查不到的公式）整条不印：读者面宁可
#: 少一行，也不印内部记号。
WRITER_RENDERER_VERSION = "pwr-5"
UNRESOLVED_ID_PREFIX = "unres_"

# ---------------------------------------------------------------------------
# 分批请求的版本化策略（§二：修「输出容量的结构性问题」）
#
# 真实 run `m930_3_acceptance_20260924T005116Z` 的失败形状是**输出容量**，不是材料不足：
# 一节**一次**请求要把整节（company 46 个 Contract aspect / 10 个栏目）的全部候选、草稿单元
# 与补件诉求一次说完，输出 8192 token 用尽被 provider 截断（`finish_reason=max_tokens`），
# 于是一次合法的提案集都形不成。对策**不是**调大 `max_tokens`，也不是截掉 `follow_up_needs`
# /候选/材料——那两种都会把「写不完」变成「看起来写完了」。
#
# 真实对策：**按确定性的 Contract aspect 范围分批请求**。每批只回答本批的 aspect，
# 事实目录与材料清单**完整**不变（分批是**请求面**的切分，不是材料面的裁剪）；各批输出经
# 确定性合并成为**一份**完整有序提案集，聚合门只消费这一份。逐批的输出上界因此从
# 「整节」降到「至多 `MAX_ASPECTS_PER_BATCH` 个 aspect」，而整节的内容总量不变。
# ---------------------------------------------------------------------------

#: 分批策略版本。它进入每批的 `batch_id` 与产物里的 `writer_batch_audit`：同一份 Contract
#: 投影在不同分批策略下会得到不同的批次划分，因此「哪一批回答了什么」必须带版本可复核。
ASPECT_BATCH_POLICY_VERSION = "wbatch-1"

#: 一批最多回答多少个 aspect。取值的依据是**输出容量**而不是费用：真实失败的那一次为
#: 46 个 aspect 输出了 23126 字符仍未写完；按 12 切分后单批的输出量落在该端点**已实测
#: 成功返回过**的量级内，同时把批次数压在个位数。
MAX_ASPECTS_PER_BATCH = 12

#: 一轮生成里允许的**额外调用**次数（上界，不是预期值）。这一份额度由**两件事共用**：
#:
#:   * **批次缩小**：某一批被 provider 截断时，本批次的结果**被拒绝**（不解析、不采纳半截
#:     JSON），改成把该批**对半**拆成两批重问——这是确定性的缩小（切分点由 aspect 顺序唯一
#:     决定），不是让模型自己收缩。额度用尽仍截断、或**单个 aspect** 的批次仍被截断时，整节
#:     typed fail-closed（`batch_truncated`）。
#:   * **批次形状纠正**：某一批自己的返回没通过结构校验（`schema_invalid`）时，对**那一批**
#:     反馈它自己的准确错误并重问一次（`bsc-1`，见 `BATCH_SHAPE_CORRECTION_NOTE_VERSION`
#:     处的逐步推导）。它不新增额度，只是把这一份额度用在「重问失败的那一批」而不是
#:     「整轮重跑」上。
#:
#: 两者**共用**这一份额度因此不是省事，而是**不改预算**：每轮调用的上界仍是
#: `aspect_batch_count + MAX_SWEEP_SHRINK_STEPS`（`_narration_structural_bound` 直接读它）。
#: 各自记账就等于把这一份额度按原因复制成两份，那是加预算换产出——被裁决点名禁止。
#: 额度用尽时的出口一律是 typed fail-closed，绝不留下部分正式 Draft/Claim/Pack 写入。
MAX_SWEEP_SHRINK_STEPS = 2

#: C4：**批次形状纠正**（`bsc-1`）的次数上界——**按批**计，每批至多纠正一次。再次失败即停。
#:
#: 为什么单独一条路（而不是沿用「通用说明 + 整轮重跑」）：整轮重跑的代价不是「多问一次」，
#: 而是**把其余批次已经拿到的合法返回全部丢掉**，然后连它们一起重问。r7b 现场逐字可见：
#: 第 1 批第一次返回 4398 output_tokens、40 条候选结构全通过（`parse_writer_proposals` 通过），
#: 第二轮却换了一个新 call_id 重问同一批（2062 output_tokens、15 条候选），第一轮那份返回
#: 从此不在任何产物里。更糟的是通用说明**不含批次坐标**：它逐字进入每一批，于是第 2 批会
#: 收到「候选 c9 的第一条支撑边必须是 primary」——而 `c9` 只在第 3 批自己的编号里存在
#: （每批都从 `c1` 数起）。因此纠正说明是**批内**的：只追加在失败那一批的请求之后，
#: 其余批次的请求一个字都不变，它们的 call_id / response_hash / 原始返回原样留存。
#:
#: 界（可逐步核对）：
#:   * 每批至多 `MAX_BATCH_SHAPE_CORRECTIONS_PER_BATCH` 次；再次失败即停，**不**再借通用
#:     重试通道把同一件事整轮重问一遍（那正是「一次格式抖动换掉所有批次」的形态）；
#:   * 与截断缩小**共用** `MAX_SWEEP_SHRINK_STEPS` 那一份额外调用额度 ⇒ 每轮调用上界不变。
#: 失败批次的**第一次返回不因纠正成功而消失**：它连同 call_id / response_hash / 错误原文
#: 记进本轮的 `shape_corrections`（`rejection_kind` 取 `schema_invalid`），纠正那一次调用
#: 的去向另记进同一条（`correction.outcome` = `accepted` / `rejected`）。
MAX_BATCH_SHAPE_CORRECTIONS_PER_BATCH = 1

#: 批次形状纠正说明的版本号（说明是**输入面**的一部分：它逐字进入那一批的请求，改一句就是
#: 改一次输入纪律，账本上要能区分）。
BATCH_SHAPE_CORRECTION_NOTE_VERSION = "bsc-1"

#: 批次形状纠正的触发 kind——取**已经登记**的那一个字面量（`PROPOSAL_SET_REJECTION_KINDS`
#: 里的 `schema_invalid`），不新开一类：这条路要处理的事就是「某一批自己的返回形状不合法」，
#: 与已有的那一条语义完全相同。整束级的判定（高风险面 / 硬门 / 栏目零产出）与截断各有各的
#: 通道，不得混入。
BATCH_SHAPE_CORRECTION_TRIGGER_KIND = "schema_invalid"

#: 逐批**支撑范围**的策略版本（`pw-20` / prompt `proposals-14`）。它同时命名请求面那一块
#: （`batch_support_scope`）与解析侧那一条确定性核验，两者必须同版本：请求面声明了什么范围，
#: 写侧就只按**同一个**范围判。分两处各写一套「本批范围」，等于让「模型被告知的范围」与
#: 「被核验的范围」可以漂移。
#:
#: 为什么需要它（**真实现场，不是假想**）：r8 的 company 一节 46 个 aspect 分 4 批。批 4/4 的
#: 10 个栏目里 9 个 `blocked`、1 个 `partial`，模型交出 0 条候选、3 段「本轮未取得…无法作出
#: 说明」的草稿（出处轴与原子账都空）、10 条合法补件诉求。那 3 段草稿被既有的「草稿必须恰好
#: 声明一条出处轴」判据拒掉——**这不是缺陷，是本批要保留的反例**（`无出处草稿必须拒绝`）。
#: 真正的问题是**它本可不必发生**：批 1/4 与批 2/4 已经因为同一种「缺失陈述草稿」各花掉一次
#: 共享的额外调用额度（`MAX_SWEEP_SHRINK_STEPS`），批 4/4 正好把额度用尽 ⇒ 整节 typed
#: `schema_invalid` fail-closed。请求面从来没有告诉过模型「本批哪些栏目**一条可引用的行都没有**」
#: ——`status` 混在一张 12 行的表里，而整节材料目录是**完整**投出的（36 行），「本节有材料」
#: 于是被读成了「本批写得出」。本版把这件事声明出来，并让写侧只按同一份声明判。
BATCH_SCOPE_POLICY_VERSION = "bscope-1"

#: `batch_support_scope` 只声明**能确定性派生**的那几件事，不替模型补一个它自己有判断力的量。
#:
#: 这里刻意**不**给「本栏目有没有可引用的行」这种逐 aspect 的布尔读数。理由是一次实测：材料
#: 在 Pack 侧只有 **topic** 归属（没有 aspect 归属），所以按 topic 派生出的「可引用行」对
#: r8 批 4/4 的 10 个栏目**全部**为真（company_business 这个 topic 下确实有 25 行材料），而
#: 那 10 个栏目里 9 个在研究侧是 `blocked`、模型也确实一条候选都写不出来。那样一个布尔值会
#: 把「整节有材料」再包装成「本批有行可用」，是**反着**的暗示——比不给更坏。因此本块只给
#: 事实：本批各 topic 有哪些可引用的行、每个 aspect 自己记录的 `status` 与绑定它的权威事实行。
#: 「这一栏读下来到底写不写得出来」是**语义判断**，归模型；它的确定性反例由既有的
#: 「草稿必须恰好声明一条出处轴」与 `assert_batch_no_witness_means_empty` 把守，不由本块代言。
BATCH_SCOPE_RULE = (
    "本块是**本批的支撑范围读数**（由系统从本批 topic、aspect 与两张选项表确定性派生，不是你"
    "判断出来的）：`topics[].material_refs` 是本批各 topic 下**可引用的材料行**（短别名 `m1..`，"
    "可回查 materials）；`aspects[]` 逐条给出本批每个栏目的 `topic_id`、研究侧记录的 `status`"
    "（`covered` / `partial` / `not_found` / `blocked` / `not_applicable`，**原样**给出）与绑定"
    "它的权威事实行 `fact_refs`（短别名 `f1..`，可回查 authority_facts；本批没有事实行时为空"
    "数组）。"
    "**引用限定在本批范围内**：草稿单元的出处、候选的支撑边与草稿单元的 context 边，只能引用"
    "本批 `topics` 里列出的那些行；引用本批之外 topic 的行是**别的批次**的写作范围，会被拒。"
    "**本块不替你读材料**：`status` 为 `partial` / `not_found` / `blocked` 的栏目该不该写、"
    "写得出什么，仍由你按材料正文判断；本块只把「这一栏在研究侧是什么状态」「本批能引用哪些行」"
    "如实摆出来，不把它改写成结论。"
    "**本批一条候选都交不出时**，`natural_prose_draft` / `claim_candidates` / "
    "`narrative_draft_units` 三个键一律是空数组，只写 `follow_up_needs`——草稿的每一句都要指得"
    "出一条真实的行，没有行可指时写下的缺失陈述（「本轮未取得 / 无法说明 / 未找到」）不是缺口，"
    "只是让本批被拒。正式缺口由系统按检索轨迹与 Contract 判定，不由你的自述生成。")

#: `fact_topic_map` 的保留值：该 fact 属于**本节之外**的主题（另一财务章节的指标、或只是
#: 指标输入的原始报表项）。它只会得到一条「本节不呈现」的去向记录，绝不会被写进正文，也
#: 不会被硬塞进本节的某个主题下（那是发明归属）。归属由组合根显式声明，不在这里猜测。
OUTSIDE_SECTION_TOPIC = "__outside_section__"
#: 合并阶段对「事实类型在权威侧闭不上」的候选所用的**哨兵事实类型**。它不是一种事实类型：
#: 权威事实类型是一串业务标识，而这里的值带尖括号，不可能与权威自报的值相同。合并用它只为
#: 让那一轮仍然合成出一份**完整有序**的提案集（身份判定随后由 `_build_pre_gate_bundle` 的
#: 权威回查作出，那才是唯一判定点），因此哨兵换不来任何通过。
_UNRESOLVED_FACT_TYPE = "<unresolved>"
#: 「本节权威输入中没有任何事实归入该主题」的缺口标签（必须同时出现在缺口正文里）。
NO_FACT_AUTHORITY_STATUS = "no_authority_fact_for_topic"

AUTHORITY_INPUT_KINDS = ("topic_harness", "financial_workflow", "derived_section")
# producer_kind ↔ 权威输入种类必须严格一致（§四生产者隔离）。
_PRODUCER_TO_AUTHORITY_KIND = {
    "topic_harness": "topic_pack",
    "financial_workflow": "financial_pack",
    "derived_section": "derived_section",
}
_SECTION_TO_AUTHORITY_KIND = {
    "topic_harness": "topic_pack",
    "financial_workflow": "financial_pack",
    "derived_section": "evidence_note",
}

# LLM 返回的**候选提案束**：只允许这些键（出现别的键——包括任何工具/检索意图、任何定稿
# 对象字段——即拒）。
#
# §16.7.1 P18：模型的输出是**选择 + 文本**，不是身份，也不是定稿对象：
#   * `support` / `context_support` 里只声明 `authority_kind` 与它在输入里**看到的那一行**
#     （container_id + branch-specific fact_id，或 material_id）。所有身份字段
#     （proposal ID / source / provenance / content fingerprint / payload / locator /
#     binding subject / draft revision / manifest identity）一律由写入侧**确定性派生**，
#     模型自报不能成为真值（P6：Writer 自报不能单独建立 disposition）。
#   * 输出里**没有** `SectionClaim` / citation / accepted binding / final Narrative /
#     `SectionResult` / disposition / 任何决定字段（P18 的固定契约）。
_PLAN_KEYS = ("natural_prose_draft", "claim_candidates", "narrative_draft_units",
              "follow_up_needs")
_CANDIDATE_KEYS = ("candidate_key", "claim_text", "support")
_UNIT_KEYS = ("unit_key", "unit_kind", "text", "context_support")
#: 门前**自然草稿单元**的线格式（`pw-15` / 指令 E 第 3 项；`pw-16` 起两条出处轴）。它是
#: 「先写自然散文、再为草稿里每个事实原子单独提交候选」这条顺序在**输出面**上的落点：
#:   * 出处是**两条互斥的轴**（`pprov-1`，与 `NS.PROSE_PROVENANCE_POLICY_VERSION` 同一口径）：
#:     `source_member_refs` 与支撑边**同一个**别名口径（`m<N>`，由 `saref-1` 的同一张表展开），
#:     `source_fact_refs` 取 `authority_facts` 行的 `ref`。**恰有一条非空**：本节材料行非空时
#:     用前者，本节只有权威事实行（财务节的常态）时用后者。
#:   * `atom_candidate_keys` 指向**本束内**的候选键——草稿必须先于候选被读出来（键序即声明），
#:     把「哪些原子是它长出来的」写成可复核的映射，而不是让下游去猜。
#: **出处不是授权**：`source_fact_refs` 只说这段文字从哪条事实行长出来，那条事实能不能写进
#: 正文，仍然只由它自己的原子候选、路径 A 支撑边与后续决定判定。删掉这个字段不会让任何一条
#: 事实变得不能写，加上它也不会让任何一条事实变得能写。
#: 它**不**含任何身份字段：`prose_unit_id` / `draft_revision` 一律由写入侧派生。
_PROSE_KEYS = ("prose_key", "text", "source_member_refs", "source_fact_refs",
               "atom_candidate_keys")
#: 提案**线格式**的版本串。它与 prompt 修订号共用一套命名，但**不是同一件东西**：prompt
#: 修订号说的是「模型被要求输出什么」，线格式说的是「解析器接受什么形状」。
#:   * `PROPOSAL_WIRE_CURRENT`：当前线 —— `natural_prose_draft` 是**必备**顶层键，
#:     「有候选、无草稿」是 typed failure（不静默回落到空草稿 + Claim 拼文）；草稿单元的
#:     出处有**两条互斥的轴**（`source_member_refs` / `source_fact_refs`）。
#:   * `LEGACY_PROPOSAL_WIRES`：历史线。`proposals-10` 是**已存在**的离线重放记录所用的形状
#:     （**根本没有**草稿层），只能经**显式标注**的兼容路径读回，且按 §不得做「不把旧离线重放
#:     当作新版本真实成功」——兼容路径只在离线 stand-in 下可用（见 `WriterPolicy.proposal_wire`）。
#: 同一资产名内的修订递增是**原位改写**，因此 marker 与形状必须一一对应（否则读者只能猜）：
#: `proposals-11` 在文档里是**四键**草稿单元（`prose_key / text / source_member_refs /
#: atom_candidate_keys`）那一版，本批给草稿单元加了第二条出处轴 ⇒ 形状变了，marker 随之升到
#: `proposals-12`。它**不**进历史线：历史线的判据是「缺草稿层」，而 `proposals-11` 已经要求
#: 草稿，把它塞进 `LEGACY_PROPOSAL_WIRES` 会顺手把「有候选必须有草稿」也关掉。
#: 另外，`proposals-11` 从未被任何一次真实创建式 run 落盘（`pw-15` 之后没有真实 run），
#: 因此没有需要在政策面上声明这条线的记录——声明面只认当前线与历史线（见
#: `WriterPolicy.__post_init__`）。它的**载荷**则是新形状的真子集（少了那条轴 = 该轴取空），
#: 所以即便真有这样一条记录出现在解析入口，也按当前线原样读得过。
#: **`proposals-13` / `pw-19` 批（r8 前最后一次请求契约窄修）刻意不动这个 marker**：那批改的是
#: **prompt 资产正文**（示例取值口径 + 一条入站防线），而 `natural_prose_draft` 仍是必备首键、
#: 草稿单元仍是那两条互斥轴、`_PROSE_KEYS` / `_UNIT_KEYS` / `_FOLLOW_UP_KEYS` 一个字都没变——
#: 「解析器接受什么形状」没变，就不得为了凑版本号把 marker 抬上去。两个串在本批之后**不再相等**
#: （`proposals-13` vs `proposals-12`），这正是上面那句「不是同一件东西」的具体样子。
PROPOSAL_WIRE_CURRENT = "proposals-12"
LEGACY_PROPOSAL_WIRES = ("proposals-10",)
#: 当前线下「有候选、无草稿」的 typed failure 标签。它是**拒绝原因**，不是缺口。
MISSING_NATURAL_DRAFT_FAILURE = "natural_prose_draft_missing"
#: `bscope-1` 线下「本批零候选、却有草稿或草稿单元」的 typed failure 标签（`pw-20`）。它同样是
#: **拒绝原因**，不是缺口：本批写不出来的内容由系统按检索轨迹与 Contract 判缺口，不由这一句
#: 拒绝消息生成。与上一条是**互反**的两半——合起来把「候选」与「草稿」钉成同生同灭：
#: 有候选必有草稿（`MISSING_NATURAL_DRAFT_FAILURE`），无候选必无草稿、必无草稿单元（本条）。
BATCH_NO_WITNESS_FAILURE = "batch_candidate_witness_missing"
# 两条支撑边各自的键集。**事实身份与材料身份分属两条路径，不得同时出现**：
#   * `support`（factual 边）：路径 A 只给事实坐标（`material_id` 必须缺省——material 锚点由
#     写侧从该权威事实**自己的引用**确定性派生，模型自报不能成为真值）；路径 B 只给
#     `material_id`，且不得携带任何 fact 身份（它在同一张表里就是同一个 `fact_id` 字段的空值）。
#   * `context_support`（context 边）：连 `fact_id` 这个字段都**没有**，因此「context 边携带
#     事实身份」在提案词汇表里不可表达（与 `validate_support_edge_shape` 的 context 分支同向）。
_FACTUAL_SUPPORT_KEYS = ("authority_kind", "container_id", "fact_id", "material_id",
                         "support_role", "support_semantics", "authorization_path")
_CONTEXT_SUPPORT_KEYS = ("authority_kind", "container_id", "material_id", "support_role",
                         "support_semantics", "authorization_path")
#: 本批 context 边只支持 topic_pack material：其余 authority 的 context 载体（financial payload /
#: 附注 locator / snapshot 载体）在**提案词汇表**里无法表达（它们的定位与事实身份共用一个字段，
#: 去掉事实身份后没有可解析的载体）。线格式支持它们，但本批的提案契约不提供入口，一律 fail-closed。
_CONTEXT_AUTHORITY_KINDS = ("topic_pack",)
_FOLLOW_UP_KEYS = ("statement", "target_requirement_id", "topic_id", "question_id",
                   "aspect_id", "requiredness", "expected_source_class", "budget_hint")

# ---------------------------------------------------------------------------
# 支撑选项的**短别名**（M930-3 返修 §二「让合格材料真正形成可读内容」）
#
# r5 的失败形状是**输出容量**：逐批请求已经到位，材料正文也不再是唯一瓶颈——瓶颈在**输出**。
# 每条支撑边当时要逐字回抄 `authority_kind` + 64 位 `container_id` + 64 位
# `fact_id`/`material_id` + 三个恒定字段（`support_semantics` / `authorization_path` /
# context 边的 `support_role`），一条边约 350–400 字符。真实证据：company 节第 2/4 批
# （`call_id=b1278b3a9f29459680409da619821eb7`）在 8192 输出 token 里只写完 37 条候选就被
# provider 截断（`finish_reason=max_tokens`），其中绝大部分字符是身份的**回抄**，不是内容。
#
# 对策不是「让模型少写」，也不是调大 `max_tokens`，而是**把回抄删掉**：请求里逐行给出
# **预先声明**的短别名（`ref`），模型只做**选择**——写 `{"ref": "m7",
# "support_role": "primary"}`——身份由系统从被引用的那一行**确定性展开**，再交给与长格式
# **同一个**校验器。三条不变量见 `SupportAliasTable`。
# ---------------------------------------------------------------------------

#: 支撑选项别名策略版本。它进入请求里的 `support_refs` 块：同一份别名规则在不同版本下会给出
#: 不同的 ref→行 映射，因此「这次请求声明了哪些选项」必须带版本可复核。
SUPPORT_ALIAS_POLICY_VERSION = "saref-1"
#: 两条前缀各自指向哪张选项表（事实行 / 材料行）。前缀进 ref，因此「一个 ref 指向哪一种行」
#: 是**语法可见**的，不需要回查才知道。
FACT_REF_PREFIX = "f"
MATERIAL_REF_PREFIX = "m"
#: 别名形式的支撑边**只允许**这些字段。`support_role` 是模型仍然要判断的那一个语义字段
#: （primary / corroborating，factual 边）；其余字段要么是常量（`support_semantics` /
#: `authorization_path` / context 边的 role），要么由被引用的那一行决定
#: （`authority_kind` / `container_id` / `fact_id` / `material_id`）。
#: 写了长格式字段即**拒**（不是「忽略」）：忽略会让「模型自报的身份」与「系统展开的身份」
#: 在产物里长得一模一样，两者从此无法区分。
_ALIAS_FACTUAL_KEYS = ("ref", "support_role")
_ALIAS_CONTEXT_KEYS = ("ref",)

_ASPECT_STATUSES = tuple(TS.ASPECT_RESULT_STATUSES)
_NON_COVERED_STATUSES = NS.NON_COVERED_ASPECT_STATUSES
# 附注缺口没有 aspect 状态，只有任务级 reason_codes。它的人读标签统一取这个词，并且**必须
# 同时出现在缺口正文里**（否则标签与正文脱节）；标签只表示「本节未能取得附注」，不改写原因。
NOTE_GAP_AUTHORITY_STATUS = "blocked"
#: §十二 3：`period_unresolved` 缺口在人读标签里显示的权威原始状态。它既不是「未取得」
#: 也不是「不存在」，而是「权威记录里没有可核验的期间」——因此单独一个词表值。
PERIOD_UNRESOLVED_AUTHORITY_STATUS = "period_unresolved"
# 缺口原因码：只允许 narrative_schema 声明的集合（不新造词）。
_GAP_REASON_CODES = tuple(NS.DISPOSITION_REASON_CODES)

# ---------------------------------------------------------------------------
# **逐条**补件申请的拒绝词表（M930-3 返修：一条写错的申请不得杀死整节）
#
# 真实 run（r5，financial）的形状是：一节已经通过确定性硬门、Draft 已经成形，却在**门后**
# 构造 `FollowUpNeed` 时因为**一条**申请把 `aspect_id` 与 `topic_id` 交叉填写（申请必须四个
# id 逐字取自同一行）而抛出 `PackWriterError`，于是整节连已经合格的候选一起消失。
#
# 「补件申请不成立」与「这一节的候选不成立」是两件事：前者是**一条**诉求自己的问题，它的
# 正确表达是一条 typed 拒绝记录 + 其余诉求与全部候选照常成立。因此这里给出封闭词表，让
# 「第几条申请、为什么不成立」可逐条复核，而不是退化成一行异常字符串。
# ---------------------------------------------------------------------------

#: 一条补件申请**自己不成立**时的封闭原因码（逐条层面；整节不因它被拒）。
FOLLOW_UP_REJECTION_CODES = (
    #: `topic_id` 不属于本节权威输入。
    "topic_not_in_section",
    #: `question_id` 不是该 topic 下的问题。
    "question_not_under_topic",
    #: `aspect_id` 不在该 topic 的 Contract 投影内（最常见：topic_id 与 aspect_id 交叉填写）。
    "aspect_not_in_topic",
    #: `aspect_id` 不属于该 question（申请必须落在它自己那一项需求上）。
    "aspect_not_in_question",
    #: `target_requirement_id` 不是该主题的真实需求 id（不得凭字符串猜一个需求）。
    "target_requirement_mismatch",
    #: `contract_authorized_scope` 为空（申请范围不得为空）。
    "authorized_scope_empty",
    #: `budget_hint` 为空（`pw-19`）。检索执行门（`TR.build_follow_up_focus`）对空白
    #: `budget_hint` 是 fail-closed 的，而 `TS.FollowUpNeed` 的 schema 不查这一格——
    #: 不在**这里**逐条拦下，这条诉求就会一路走到 Harness 获批执行时才抛 `TopicRuntimeError`，
    #: 而那时它已经进了产物、看起来像一条「已提出的待裁决提议」。**逐条**拒绝（不是整束）：
    #: 一条空预算的申请与同一束里其余合法的申请、以及已经通过硬门的整节草稿是三件事。
    "budget_hint_empty",
    #: `TS.FollowUpNeed` 自己的 schema 校验未过。
    "need_schema_invalid",
    #: 原始诉求缺字段（连结构校验都没过的那条路上取到的原文形态）。
    "spec_field_missing",
)

# `FollowUpNeedRejected` 定义在 `PackWriterError` 之后（它继承它）——见下方「策略与投影」前。

# ---------------------------------------------------------------------------
# 材料**未使用**理由的版本化策略（§6.4.1 第四层 / §0.13）
#
# `not_used` 的理由码是**封闭**的，且每条理由必须携带 typed proof。本模块是这些理由的
# **唯一**派生者：理由不是模型自报的，而是从 manifest × 本次提案集**确定性重算**出来的。
# 每条理由绑定一份版本化、可复核的策略文本（`policy_version` + `policy_fingerprint`），
# 于是「为什么这份材料没用上」这件事本身可以被独立重算，而不是一句自由文本。
# ---------------------------------------------------------------------------

MATERIAL_RELEVANCE_POLICY_VERSION = "mrel-1"

#: 该政策**逐条**的可复核规则（顺序即优先级）。指纹是这段文本的 sha256，因此改动规则必然
#: 改动指纹——旧产物里的 `not_used` 证明不会与新政策共享身份。
_MATERIAL_RELEVANCE_POLICY_RULES = (
    "1. 若某 manifest 成员被本次 draft 的**至少一条** proposal 引用（按 pack_id + material_id），"
    "则该成员 usage=used，并按 proposal ID 的规范序给出 support_usages；未被任何 proposal "
    "引用者一律 usage=not_used（三层等式：used ∪ not_used = available，processed = available）。",
    "2. not_used 的理由按下列优先级**确定性**判定，先命中者胜，不做任何主观取舍。",
    "3. duplicate：存在另一个**已被使用**的成员与之 content fingerprint 相同且同样来自"
    "同一 source_identity 的同一来源；证明指向该成员。",
    "4. covered_by_more_complete_material：存在另一个**已被使用**的成员，其 source_identity "
    "与之相同且其 locator 区间**严格包含**本成员的区间；证明指向该成员与覆盖关系身份。",
    "5. candidate_qualification_failed：本成员的 Pack 侧 ResearchMaterialDisposition 的"
    "admission_state 为 rejected，且在其材料上能唯一解析到一条 (fact_candidate, "
    "qualification_decision) 对；证明指向该对。",
    "6. irrelevant_to_section_goal：以上都不成立时，写入侧为该成员按本政策版本生成一条"
    "**可重算的相关性决定记录**（引用条目为空是它的判定结果本身），证明指向该记录。",
    "7. 任何 not_used 都不是 gap：它不产生 ContractGap / ResearchBlock / SectionUnresolved，"
    "也不得被用来顶替「Contract 必需事实未取得」的缺口。",
)


def material_relevance_policy_fingerprint() -> str:
    """材料未使用理由政策的指纹（版本 + 逐条规则的 sha256，64 位 hex）。

    必须是完整 sha256（`WMPD.reason_proof.policy_fingerprint` 的格式要求）——`NS.content_id`
    只截断 24 位，不满足「证明必须绑定版本化策略字节」的可复核强度，故此处直接用 hash。
    """
    return hashlib.sha256(NS.canonical_json({
        "policy_version": MATERIAL_RELEVANCE_POLICY_VERSION,
        "rules": list(_MATERIAL_RELEVANCE_POLICY_RULES)}).encode("utf-8")).hexdigest()


MATERIAL_RELEVANCE_POLICY_FINGERPRINT = material_relevance_policy_fingerprint()

# aspect 状态（topic runtime 词表）→ canonical `SectionUnresolved.state`（question 状态词表）。
# 这是**词表翻译**，不是状态美化：aspect 的原始状态逐字写进 detail 与 draft 的
# unresolved_projection，且没有任何一种情况会被翻成「已覆盖」。
_STATUS_TO_QUESTION_STATE = {
    "partial": "NOT_PROVIDED",
    "blocked": "NOT_PROVIDED",
    "not_found": "NOT_FOUND_AFTER_SEARCH",
    "not_applicable": "NOT_APPLICABLE",
}


class PackWriterError(Exception):
    """Writer 侧的 fail-closed 错误（宁可不产出章节，也不产出无据正文）。"""


class FollowUpNeedRejected(PackWriterError):
    """**一条**补件申请自己不成立（Contract 校验未过）。

    它是**逐条**层面的拒绝，不是整束/整节层面的：同一份响应里其它诉求照常成形，候选与草稿
    单元完全不受影响。`code` 取 `FOLLOW_UP_REJECTION_CODES`（定义在上方词表区）里的封闭值，
    `spec_index` 是它在**自己那一份响应**里的序号（0 起）——「第几条不成立」与「为什么不成立」
    因此都可复核。

    它继承 `PackWriterError`：所有既有的 `except PackWriterError` 仍然接得住它，语义只是更窄。
    """

    def __init__(self, message: str, *, code: str, spec_index: int) -> None:
        super().__init__(message)
        if code not in FOLLOW_UP_REJECTION_CODES:
            raise PackWriterError(
                f"补件申请拒绝原因码 {code!r} 不在封闭词表 "
                f"{list(FOLLOW_UP_REJECTION_CODES)} 内（不得随手造一个原因码）")
        if spec_index < 0:
            raise PackWriterError("补件申请的序号不得为负")
        self.code = code
        self.spec_index = spec_index


# ---------------------------------------------------------------------------
# 策略与投影
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WriterPolicy:
    """版本化写作策略（prompt / model / renderer / 重试预算）。"""

    policy_version: str = PACK_WRITER_POLICY_VERSION
    prompt_version: str = NARRATION_PROMPT_VERSION
    model_policy: str = "stub"
    renderer_version: str = WRITER_RENDERER_VERSION
    rules_version: str = SC.RULES_VERSION
    max_llm_retries: int = 0
    #: 本轮的**提案线格式**（`pw-16`）。缺省是当前线；历史线只能显式声明，且只允许离线
    #: stand-in（`model_policy == "stub"`）——理由与判据见 `LEGACY_PROPOSAL_WIRES` 处：
    #: 旧的离线重放记录（`proposals-10`，无草稿层）必须仍然读得回来（那不是缺陷，是历史），
    #: 但同一条线**不得**被用来把「有候选、无草稿」的新真实调用记成成功（CLAUDE.md：
    #: 不得把旧离线重放称作新版本真实成功）。因此这条开关与**真实模型**组合即 fail-closed，
    #: 而它进了 `WriterPolicy`，也就进了本轮的策略身份与产物。
    proposal_wire: str = PROPOSAL_WIRE_CURRENT
    #: 本轮是**对留存字节的离线重放**：非空 = 被重放的那一轮记录身份（run id / 现场名）。
    #: 它回答的问题与 `proposal_wire` **正交**：线格式说「这份载荷是什么形状」，本字段说
    #: 「这些字节从哪来」。两者都进策略身份，因此复演记录里逐字可查「读的是哪一轮的字节」。
    #:
    #: 为什么历史线需要它：历史线缺草稿层，它在**真实一轮**里等于把「没有草稿」记成成功；
    #: 但**重放**不是真实一轮——它零网络、零新调用，字节（含它们当初的模型名）都是留存的。
    #: 而重放必须**如实**带着原始模型名（`NarrationResult.model` 的用途就是「这次正文是谁生成的」，
    #: 改写成 `stub` 才是抹掉证据），因此「重放」这个事实只能在策略上说，不能靠把模型名改掉。
    #: 于是历史线有**两条**合格声明：`model_policy == "stub"`（离线 stand-in）或本字段非空
    #: （重放留存字节）。两条都表示「本轮没有发起真实模型调用」，缺了它们历史线一律 fail-closed。
    replay_source: str = ""

    def __post_init__(self) -> None:
        for name in ("policy_version", "prompt_version", "model_policy", "renderer_version",
                     "rules_version"):
            if not getattr(self, name):
                raise PackWriterError(f"WriterPolicy.{name} 不得为空")
        if self.max_llm_retries not in (0, 1):
            raise PackWriterError(
                f"WriterPolicy.max_llm_retries 只能是 0 或 1（§四每节最多重试一次），"
                f"得到 {self.max_llm_retries!r}")
        if self.proposal_wire != PROPOSAL_WIRE_CURRENT:
            if self.proposal_wire not in LEGACY_PROPOSAL_WIRES:
                raise PackWriterError(
                    f"WriterPolicy.proposal_wire={self.proposal_wire!r} 不是已知的提案线"
                    f"（当前线 {PROPOSAL_WIRE_CURRENT!r}，历史线 {list(LEGACY_PROPOSAL_WIRES)}）")
            if self.model_policy != MODEL_POLICY_STUB and not self.replay_source:
                raise PackWriterError(
                    f"WriterPolicy.proposal_wire={self.proposal_wire!r} 是历史线，"
                    f"而 model_policy={self.model_policy!r} 不是离线 stand-in 且 "
                    "replay_source 为空（既不是离线 stand-in、也没声明重放的是哪一轮留存字节）："
                    "历史线缺草稿层，用它跑真实模型等于把「没有草稿」记成成功——"
                    "旧离线重放可以读回来（须显式声明被重放的那一轮记录），"
                    "新的真实调用只能走当前线")
        # §五 4：model policy 必须能确定性解析成实际模型名（否则「记录一个、实际调另一个」
        # 根本无法被发现）。解析不了的策略在这里就 fail-closed，而不是等到调用之后。
        resolve_model_policy(self.model_policy)

    def requires_natural_draft(self) -> bool:
        """本轮是否强制「有候选 ⇒ 有草稿」。

        判据是**这条线有没有草稿层**，而不是「是不是当前线」：历史线（`LEGACY_PROPOSAL_WIRES`）
        的载荷里根本没有 `natural_prose_draft` 这个键，用它要求草稿就是把一条本来欠这个键的
        历史记录判成缺陷。反过来，任何**有**草稿层的线都恒真——包括线格式升版后可能出现的
        中间版本（如 `proposals-11`），它们的载荷照样带草稿，放宽是没有道理的。
        """
        return self.proposal_wire not in LEGACY_PROPOSAL_WIRES


@dataclass(frozen=True)
class ProjectedAspect:
    """WritingSpec 里一条 aspect → 正文槽位的投影（Contract v2 投影的原子项）。"""

    aspect_id: str
    subsection_id: str
    role: str
    content_role: str
    citation_granularity: str
    display_tier: str
    gap_display_policy: str
    period_language_policy: str
    table_schema: str | None = None


@dataclass(frozen=True)
class ContractProjection:
    """本节的 Contract v2 投影：把 frozen WritingSpec 的槽位收窄到当前 section。

    只做「选择」，不做「重新设计」：aspect_id / 槽位 / 颗粒度全部逐字来自 WritingSpec。
    """

    projection_id: str
    writing_spec_id: str
    section_id: str
    subsection_ids: tuple[str, ...]
    contract_version: str
    contract_fingerprint: str
    producer_kinds: tuple[str, ...]
    aspects: tuple[ProjectedAspect, ...]

    @classmethod
    def create(cls, writing_spec: Any, *, section_id: str, contract_version: str,
               contract_fingerprint: str) -> "ContractProjection":
        toc = (writing_spec.toc or {}).get(section_id)
        if not toc:
            raise PackWriterError(f"WritingSpec 没有 section={section_id!r} 的目录槽位")
        # WritingSpec.toc[section] 是 [{"subsection_id": ..., "h2": ...}]，只取槽位 id。
        subsection_ids = tuple(
            str(item["subsection_id"] if isinstance(item, Mapping) else item[0])
            for item in toc)
        prefix = subsection_ids[0].split("-")[0] + "-"
        aspects = tuple(
            ProjectedAspect(
                aspect_id=str(m["aspect_id"]), subsection_id=str(m["subsection_id"]),
                role=str(m.get("role") or ""), content_role=str(m.get("content_role") or ""),
                citation_granularity=str(m.get("citation_granularity") or ""),
                display_tier=str(m.get("display_tier") or ""),
                gap_display_policy=str(m.get("gap_display_policy") or ""),
                period_language_policy=str(m.get("period_language_policy") or ""),
                table_schema=m.get("table_schema"))
            for m in writing_spec.mappings
            if str(m.get("subsection_id")) in set(subsection_ids))
        if not aspects:
            raise PackWriterError(f"WritingSpec 中 section={section_id!r} 没有任何 aspect 槽位")
        producer_kinds = tuple(sorted(
            kind for kind, prefixes in _PRODUCER_KIND_PREFIXES.items() if prefix in prefixes))
        if not producer_kinds:
            raise PackWriterError(f"前缀 {prefix!r} 没有对应的 producer_kind 声明")
        body = {
            "writing_spec_id": writing_spec.writing_spec_id, "section_id": section_id,
            "subsection_ids": list(subsection_ids), "contract_version": contract_version,
            "contract_fingerprint": contract_fingerprint, "producer_kinds": list(producer_kinds),
            "aspects": [a.aspect_id for a in aspects],
        }
        return cls(projection_id=NS.content_id("cproj_", body),
                   writing_spec_id=str(writing_spec.writing_spec_id), section_id=section_id,
                   subsection_ids=subsection_ids, contract_version=contract_version,
                   contract_fingerprint=contract_fingerprint, producer_kinds=producer_kinds,
                   aspects=aspects)

    def aspect(self, aspect_id: str) -> ProjectedAspect | None:
        for item in self.aspects:
            if item.aspect_id == aspect_id:
                return item
        return None

    def require_producer_kind(self, producer_kind: str) -> None:
        if producer_kind not in self.producer_kinds:
            raise PackWriterError(
                f"producer_kind={producer_kind!r} 不属于 section={self.section_id!r} 的槽位权威"
                f"（允许 {list(self.producer_kinds)}）——跨生产者混装已拒")


# ---------------------------------------------------------------------------
# 封闭的 WorkerAuthorityInput 并集
# ---------------------------------------------------------------------------

def _identity_of_pack(pack: Any) -> dict:
    return {
        "company_id": str(getattr(pack, "company_id", "") or ""),
        "report_as_of": str(getattr(pack, "report_as_of", "") or ""),
        "contract_version": str(getattr(pack, "contract_version", "") or ""),
        "contract_fingerprint": str(getattr(pack, "contract_fingerprint", "") or ""),
    }


def _assert_declared(actual: Mapping[str, str], declared: Mapping[str, Any], what: str) -> None:
    for key, value in declared.items():
        if value is None:
            continue
        if str(value) != actual[key]:
            raise PackWriterError(
                f"{what} 与调用方声明不一致：{key}={actual[key]!r}，声明 {value!r}")


def _financial_artifact_identity(artifact: Any) -> dict:
    """财务 artifact 自报的**公司/Contract 身份**（只读类型化字段，不做任何猜测）。

    **这里不再产出 `report_as_of`。** O-11：`report_as_of` 是本次报告生成日（运行级业务
    日期，全部 section 共用同一瞬间），而财务快照的 `as_of_date` 只表示**财务数据期末**。
    用期末顶替结论日就是「从 `FinancialSnapshot.as_of_date` 推导 `report_as_of`」，本次业务
    裁决明令禁止。报告结论日改由调用方按本轮唯一时钟瞬间显式声明（见 `create()`）；财务期末
    由 `_financial_period_identity()` 单独读出并**各自**绑定进输入身份，二者不得互相顶替。

    真实的 `FinancialPackArtifact` **不重复存**公司：它只存在于 `snapshot` 身份里
    （`company_id`）。扁平字段形态只为既有替身保留，取不到就返回空串，由调用方 fail-closed，
    而不是在这里发明一个公司。
    """
    snapshot = getattr(artifact, "snapshot", None)
    return {
        "company_id": str(getattr(artifact, "company_id", "")
                          or getattr(snapshot, "company_id", "") or ""),
        "contract_version": str(getattr(artifact, "contract_version", "") or ""),
        "contract_fingerprint": str(getattr(artifact, "contract_fingerprint", "") or ""),
    }


#: 财务输入的「期间身份」键：快照 id + 财务数据期末。**都不是**报告结论日。
_FINANCIAL_PERIOD_KEYS = ("financial_snapshot_id", "financial_period_end")


def _financial_period_identity(artifact: Any) -> dict:
    """财务 artifact 的**期间身份**：快照 id + 财务数据期末（`snapshot.as_of_date`）。

    期末与快照 id 必须齐备：取不到即由 `_require_complete_identity` fail-closed（不得发明
    一个基准期，也不得退回报告生成日）。只读快照自身字段——真实 artifact 的期末只在这里，
    扁平 `report_as_of` 形态在本节是**期间**语义（`FinancialFactPack.report_as_of` 即期末），
    因此不把它当成结论日读取。
    """
    snapshot = getattr(artifact, "snapshot", None)
    return {
        "financial_snapshot_id": str(getattr(snapshot, "snapshot_id", "") or ""),
        "financial_period_end": str(getattr(snapshot, "as_of_date", "") or ""),
    }


# ---------------------------------------------------------------------------
# §三 P0：公共信任边界——AuthorityInput 不得被直接构造绕过
#
# `__post_init__`、`create()` 与未来的 `from_dict()` 必须走**同一个**验证函数；直接调用
# dataclass 构造器也 fail-closed；`input_id` 一律重算核对。这里不做任何「上游应该验证过」
# 的假设：本模块是公司/行业/财务正文的公共信任边界，替身对象不得靠字段形状混进来。
# ---------------------------------------------------------------------------

AUTHORITY_INPUT_ID_PREFIX = "tainput_"


def _require_real_type(obj: Any, expected: type, what: str) -> None:
    """上游权威对象必须是**真实类型**（拒绝字段形状相同的替身/假对象）。"""
    if not isinstance(obj, expected):
        raise PackWriterError(
            f"{what} 必须是真实的 {expected.__module__}.{expected.__qualname__}，"
            f"得到 {type(obj).__module__}.{type(obj).__qualname__}："
            "公共信任边界不接受形状相同的替身（§三 P0）")


def _content_fingerprint_of(obj: Any, what: str) -> str:
    """上游权威对象**自己**报出的内容指纹；取不到就 fail-closed，绝不在这里重算或发明。"""
    for attr in ("content_fingerprint", "compute_content_fingerprint"):
        value = getattr(obj, attr, None)
        if value is None:
            continue
        text = str(value() if callable(value) else value).strip()
        if text:
            return text
    dump = getattr(obj, "to_dict", None)
    if callable(dump):
        return NS.content_id("fp_", _jsonable(dump()))
    raise PackWriterError(f"{what} 不提供内容指纹，权威身份无法确定（不得写作）")


def _canonical_content_of(obj: Any) -> str:
    """对象的规范内容指纹（附注事实集/缺口的 canonical 内容身份）。"""
    dump = getattr(obj, "to_dict", None)
    return NS.content_id("fp_", _jsonable(dump() if callable(dump) else obj))


def _require_complete_identity(identity: Mapping[str, str], what: str) -> None:
    """身份四元组（公司/基准期/Contract 版本/Contract 指纹）必须齐备：缺一不得写作。"""
    missing = sorted(k for k, v in identity.items() if not str(v or ""))
    if missing:
        raise PackWriterError(
            f"{what} 的权威身份不完整，缺少 {missing}：不得用调用方声明或 section 的 "
            "report_as_of 代填（不发明公司/基准期/Contract）")


def _topic_pack_facts(pack_set: Any) -> tuple[tuple[str, ...], tuple[Any, ...], dict[str, str]]:
    """`VerifiedPackSet` → (topic_ids, packs, 身份)，**唯一**的真实类型/Pack 身份提取口。

    校验：真实类型 → exact task/section/topic set → 每个 Pack 是真实 `TopicResearchPack`、
    自报非空 `pack_id` 且内容身份自洽（`verify_pack_id`）→ 组内身份四元组一致。
    """
    _require_real_type(pack_set, PSet.VerifiedPackSet, "TopicPackAuthorityInput.pack_set")
    if str(pack_set.task_id or "") == "" or str(pack_set.section_id or "") == "":
        raise PackWriterError("VerifiedPackSet.task_id / section_id 必须非空")
    topic_ids = tuple(str(t) for t in (pack_set.topic_ids or ()))
    if not topic_ids:
        raise PackWriterError("VerifiedPackSet 没有声明任何 topic，不得写作")
    if len(set(topic_ids)) != len(topic_ids):
        raise PackWriterError(f"VerifiedPackSet.topic_ids 含重复项：{list(topic_ids)}")
    packs = tuple(pack_set.packs or ())
    if not packs:
        raise PackWriterError("VerifiedPackSet 没有任何 Pack，不得写作")
    if len(packs) != len(topic_ids):
        raise PackWriterError(
            f"VerifiedPackSet 的 Pack 数 {len(packs)} 与 topic 数 {len(topic_ids)} 不一致")
    identity: dict[str, str] = {}
    for pack in packs:
        _require_real_type(pack, TS.TopicResearchPack, "VerifiedPackSet.packs[]")
        if not str(getattr(pack, "pack_id", "") or ""):
            raise PackWriterError(
                "Pack 必须自报非空 pack_id（fresh pack 未 finalize 时不得成为权威输入）")
        try:
            pack.verify_pack_id()
        except Exception as exc:  # noqa: BLE001 —— 上游错误一律转成本边界的拒绝
            raise PackWriterError(f"Pack 内容身份校验失败：{exc}") from exc
        current = _identity_of_pack(pack)
        if not identity:
            identity = current
        elif current != identity:
            raise PackWriterError(
                f"PackSet 内 Pack 的身份字段不一致：{pack.pack_id} 与 {packs[0].pack_id}")
    return topic_ids, packs, identity


def _topic_pack_body(*, task_id: str, section_id: str, topic_ids: Sequence[str],
                     packs: Sequence[Any], identity: Mapping[str, str]) -> dict:
    """`TopicPackAuthorityInput` 的规范身份体（`create()` 与 `__post_init__` 共用）。"""
    return {
        "task_id": task_id, "section_id": section_id,
        "topic_ids": [str(t) for t in topic_ids],
        "pack_ids": [str(p.pack_id) for p in packs],
        "pack_content_fingerprints": [
            _content_fingerprint_of(p, f"Pack {getattr(p, 'pack_id', '')!r}") for p in packs],
        **{k: str(identity[k]) for k in
           ("company_id", "report_as_of", "contract_version", "contract_fingerprint")},
    }


def _validate_topic_pack_authority(obj: "TopicPackAuthorityInput") -> None:
    """`TopicPackAuthorityInput` 的唯一验证：`__post_init__` / `create()` / `from_dict()` 共用。"""
    topic_ids, packs, identity = _topic_pack_facts(obj.pack_set)
    if str(obj.task_id or "") != str(obj.pack_set.task_id):
        raise PackWriterError(
            f"PackSet 不属于本任务：pack_set.task_id={obj.pack_set.task_id!r}，"
            f"input.task_id={obj.task_id!r}")
    if str(obj.section_id or "") != str(obj.pack_set.section_id):
        raise PackWriterError(
            f"PackSet 不属于本节：{obj.pack_set.section_id!r} != {obj.section_id!r}")
    declared_topics = tuple(str(t) for t in obj.topic_ids)
    if declared_topics != topic_ids:
        raise PackWriterError(
            f"input.topic_ids 必须与 PackSet 的 topic 集合完全一致（exact-topic-set 含顺序）："
            f"{list(declared_topics)} != {list(topic_ids)}")
    if tuple(str(p.topic_id) for p in packs) != topic_ids:
        raise PackWriterError(
            f"PackSet 内 Pack 的 topic 顺序必须与 topic_ids 一致："
            f"{[str(p.topic_id) for p in packs]} != {list(topic_ids)}")
    _require_complete_identity(identity, "TopicPackAuthorityInput")
    for key, value in identity.items():
        actual = str(getattr(obj, key, "") or "")
        if actual != value:
            raise PackWriterError(
                f"TopicPackAuthorityInput.{key} 与 Pack 权威身份不一致：{actual!r} != {value!r}")
    body = _topic_pack_body(task_id=obj.task_id, section_id=obj.section_id,
                            topic_ids=topic_ids, packs=packs, identity=identity)
    expected = NS.content_id(AUTHORITY_INPUT_ID_PREFIX, body)
    if str(obj.input_id or "") != expected:
        raise PackWriterError(
            f"TopicPackAuthorityInput.input_id 与输入内容不符：声明 {obj.input_id!r}，"
            f"重算 {expected!r}（身份必须覆盖 Pack 身份与内容指纹）")
    # 被提供面的读回核对：`__post_init__` 已把它派生过一次，这里**重算并比对**。声明面与
    # 重算面不符只可能来自绕过构造器写入（`object.__setattr__`），一律 fail-closed——
    # 一个被声明的「我给他看过这条事实」正是「门要求模型引用从未交给它的事实」的成因。
    # 面为 `None`（扫描自身 fail-closed）时无从核对，跳过：该状态已由
    # `writer_face_unavailable` 记明，且写作链在扫描处即已停止。此处**不能**断言
    # 「面必非空」：本函数在 `__post_init__` 里先于面派生被调用（身份验证必须先于一切
    # 内容级派生，否则假 PackSet 会先撞上派生错误而不是身份错误）。
    declared_face = getattr(obj, "writer_face", None)
    if declared_face is not None:
        derived_face = topic_writer_face(obj)
        if declared_face != derived_face:
            raise PackWriterError(
                f"TopicPackAuthorityInput.writer_face 与重算结果不符（被提供面只能派生，"
                f"不得声明）：声明 {declared_face.to_dict()!r}，重算 {derived_face.to_dict()!r}")


#: 被提供面的派生口径版本（`TopicWriterFace` 的字段解释变了必须升版本）。
WRITER_FACE_VERSION = "writer-face-1"

#: 权威事实在本节**未被提供**给 Writer 的原因：它的 aspect 不在本节 aspect 结果集里。
#: 期间门自己的三个原因码原样沿用，不在这里另造同义词。两者的封闭词表见
#: `WRITER_FACE_EXCLUSION_REASONS`（定义在期间门常量旁边，与那三个码同一处）。
WRITER_FACE_EXCLUSION_ASPECT = "aspect_not_in_section"


@dataclass(frozen=True)
class TopicWriterFace:
    """本节**交给 Writer 的权威事实面**：哪些被提供了、哪些没有、没有的原因是什么。

    不变量（门与提示词载荷必须看到**同一面**）：

    - `provided` = `authority_facts` 载荷逐行的 `(container_id, fact_id)`（升序去重）；
    - `excluded` = `(container_id, fact_id, reason)`，`reason` 取 `WRITER_FACE_EXCLUSION_ASPECT`
      或期间门给出的原因码；
    - 两面严格互斥：一条事实不可能既被提供又被排除。

    它**只能派生**：`TopicPackAuthorityInput` 的构造参数里没有它（`init=False`），由
    `__post_init__` 从 `scan_topic_pack` 的同一份扫描算出，并在 `create()` 里重算核对。
    若允许调用方声明「我给他看过这条事实」，门就会相信一个未经证明的提供面——那正是
    「门要求模型引用一条从未交给它的必需事实」的成因：模型无论怎么重写都满足不了，只能被
    fail-closed 反复打回。
    """

    provided: tuple[tuple[str, str], ...]
    excluded: tuple[tuple[str, str, str], ...]

    def provided_keys(self) -> frozenset[tuple[str, str]]:
        return frozenset(self.provided)

    def exclusion_reason(self, container_id: str, fact_id: str) -> str:
        key = (str(container_id), str(fact_id))
        for container, fact, reason in self.excluded:
            if (container, fact) == key:
                return reason
        return ""

    def to_dict(self) -> dict:
        return {"version": WRITER_FACE_VERSION,
                "provided": [[c, f] for c, f in self.provided],
                "excluded": [[c, f, r] for c, f, r in self.excluded]}


def topic_writer_face(authority: "TopicPackAuthorityInput") -> TopicWriterFace:
    """`authority` → 被提供面（**唯一**实现：与 `build_narration_messages` 用同一份扫描）。

    逐一对照载荷：`facts_payload` 每行写 `container_id=entry.container_identity` 与
    `fact_id=entry.fact_id`，正是这里 `provided` 的两个分量——所以「门看到的面」与「模型
    看到的面」不可能是两份不同的清单。
    """
    scan = scan_topic_pack(authority, None)
    provided = tuple(sorted({(str(e.container_identity), str(e.fact_id)) for e in scan.facts}))
    excluded: dict[tuple[str, str], str] = {}
    for container, fact_id, _topic_id in scan.excluded_facts:
        excluded[(str(container), str(fact_id))] = WRITER_FACE_EXCLUSION_ASPECT
    for item in scan.period_exclusions:
        key = (str(item.get("container_id", "")), str(item.get("fact_id", "")))
        if key in excluded:
            raise PackWriterError(
                f"权威事实 {key[0]}:{key[1]} 同时落在两条互斥的排除通道上"
                f"（{excluded[key]!r} 与 {item.get('reason')!r}）：扫描自相矛盾，fail-closed")
        excluded[key] = str(item.get("reason", ""))
    overlap = set(provided) & set(excluded)
    if overlap:
        raise PackWriterError(
            f"权威事实 {sorted(overlap)} 同时在提供面与排除面（两面必须严格互斥）")
    return TopicWriterFace(
        provided=provided,
        excluded=tuple(sorted((container, fact, reason)
                              for (container, fact), reason in excluded.items())))


@dataclass(frozen=True)
class TopicPackAuthorityInput:
    """company/industry 的封闭权威输入：**恰好**本任务 topic 集合的 `VerifiedPackSet`。"""

    producer_kind: str
    input_id: str
    task_id: str
    section_id: str
    company_id: str
    report_as_of: str
    contract_version: str
    contract_fingerprint: str
    topic_ids: tuple[str, ...]
    pack_set: Any
    #: 被提供面：**派生字段**（`init=False`，构造参数里没有它），由 `__post_init__` 在身份
    #: 验证通过后从同一份 `scan_topic_pack` 扫描派生。不并进 `_topic_pack_body` / `input_id`：
    #: 它是 `pack_set` 内容（已由 `pack_content_fingerprints` 覆盖）与冻结 aspect 元数据的
    #: 确定性函数，并进身份体只会让历史身份被动改号换标识，不增加任何覆盖力。
    #: `None` + 非空 `writer_face_unavailable` 表示**这次扫描自己 fail-closed**（见下）。
    writer_face: "TopicWriterFace | None" = field(init=False, default=None)
    #: 被提供面**派生失败**的原因（扫描本身 fail-closed 时）。空串表示面已成功派生。
    writer_face_unavailable: str = field(init=False, default="")

    def __post_init__(self) -> None:
        _validate_topic_pack_authority(self)
        try:
            face = topic_writer_face(self)
        except PackWriterError as exc:
            # 扫描本身 fail-closed（只可能是 material 候选歧义这一种内容级条件），此时
            # **不存在**被提供面：写作链在扫描处就停了，既走不到提示词也走不到门。这里不把它
            # 伪装成一张空面——空面会被读成「一条也没提供」，那是另一种谎——而是明确记下
            # 「本次没有被提供面」。也不在此处终止构造：**构造身份**（`input_id`）与**内容能
            # 否成面**是两件事，混在一起会让「同一份输入」有时可构造、有时不可构造，而歧义本身
            # 该在扫描处（`scan_authority` / `PW.scan_topic_pack`）以自己的诊断 fail-closed，
            # 那里的失败语义与本次返修前逐字一致。任何**别的** `PackWriterError`（例如提供面
            # 与排除面自相矛盾）都不是内容级条件，必须照常抛出，不得被这里吞掉。
            if not isinstance(exc.__cause__, NS.MaterialBindingAmbiguityError):
                raise
            object.__setattr__(self, "writer_face", None)
            object.__setattr__(self, "writer_face_unavailable", str(exc))
        else:
            object.__setattr__(self, "writer_face", face)

    @classmethod
    def create(cls, task: PS.SectionTask, pack_set: Any, *, company_id: str | None = None,
               report_as_of: str | None = None, contract_version: str | None = None,
               contract_fingerprint: str | None = None) -> "TopicPackAuthorityInput":
        topic_ids, packs, identity = _topic_pack_facts(pack_set)
        if str(pack_set.task_id) != task.task_id:
            raise PackWriterError(
                f"PackSet 不属于本任务：pack_set.task_id={pack_set.task_id!r}，"
                f"task_id={task.task_id!r}")
        if str(pack_set.section_id) != task.section_id:
            raise PackWriterError(
                f"PackSet 不属于本节：{pack_set.section_id!r} != {task.section_id!r}")
        expected = tuple(str(t) for t in task.topic_ids)
        if sorted(topic_ids) != sorted(expected):
            raise PackWriterError(
                f"PackSet 的 topic 集合必须与 SectionTask 完全一致（exact-topic-set）："
                f"{list(topic_ids)} != {list(expected)}")
        _assert_declared(identity, {"company_id": company_id, "report_as_of": report_as_of,
                                    "contract_version": contract_version,
                                    "contract_fingerprint": contract_fingerprint},
                         "TopicPackAuthorityInput")
        body = _topic_pack_body(task_id=task.task_id, section_id=task.section_id,
                                topic_ids=topic_ids, packs=packs, identity=identity)
        obj = cls(producer_kind="topic_harness",
                  input_id=NS.content_id(AUTHORITY_INPUT_ID_PREFIX, body),
                  task_id=task.task_id, section_id=task.section_id, **identity,
                  topic_ids=topic_ids, pack_set=pack_set)
        _validate_topic_pack_authority(obj)
        return obj


#: 附注身份里与 artifact **同义**的三项（公司 / Contract 版本 / Contract 指纹）。
#: 附注自己的 `report_as_of` 在本节表示**快照基准期**（其生产者用 `pack.as_of_date` 填充），
#: 因此按期间身份对齐，见 `_financial_artifact_and_note`。
_NOTE_IDENTITY_KEYS = ("company_id", "contract_version", "contract_fingerprint")


def _financial_artifact_and_note(artifact: Any, task_id: str, note_facts: Any | None,
                                 note_gap: Any | None) -> tuple[Any, Any, str, dict[str, str]]:
    """真实 artifact + 恰有其一 note 状态 → (artifact, holder, note_state, identity, period)。

    `identity` 是 artifact 自报的**公司/Contract 身份**（不含报告结论日），`period` 是它的
    **期间身份**（快照 id + 财务期末）。两者都由 upstream 只读读出，调用方不得声明。

    校验：真实类型 → `verify()` 内容自洽 → 生产者/task 绑定 → 附注 schema/版本与
    task_id/company/基准期/Contract 逐字段对齐 → 身份与期间齐备。
    """
    _require_real_type(artifact, FPA.FinancialPackArtifact, "FinancialAuthorityInput.artifact")
    if (note_facts is None) == (note_gap is None):
        raise PackWriterError(
            "附注状态必须恰有其一：ValidatedEvidenceNoteFactSet 或 EvidenceNoteGap")
    note_state = "facts" if note_facts is not None else "gap"
    if note_facts is not None:
        _require_real_type(note_facts, FPA.ValidatedEvidenceNoteFactSet,
                           "FinancialAuthorityInput.note_facts")
        holder = note_facts
    else:
        _require_real_type(note_gap, FPA.EvidenceNoteGap, "FinancialAuthorityInput.note_gap")
        holder = note_gap
    if str(artifact.task_id) != task_id:
        raise PackWriterError(
            f"FinancialPackArtifact 不属于本任务：{artifact.task_id!r} != {task_id!r}")
    if str(artifact.producer_kind) != FPA.FINANCIAL_ARTIFACT_PRODUCER_KIND:
        raise PackWriterError(
            f"artifact.producer_kind={artifact.producer_kind!r} 不是 "
            f"{FPA.FINANCIAL_ARTIFACT_PRODUCER_KIND!r}（跨生产者混装已拒）")
    try:
        artifact.verify()
    except Exception as exc:  # noqa: BLE001 —— 上游错误一律转成本边界的拒绝
        raise PackWriterError(f"FinancialPackArtifact.verify() 失败：{exc}") from exc
    if str(artifact.schema_version) != FPA.FINANCIAL_PACK_ARTIFACT_SCHEMA_VERSION:
        raise PackWriterError(
            f"artifact.schema_version={artifact.schema_version!r} 不是当前版本 "
            f"{FPA.FINANCIAL_PACK_ARTIFACT_SCHEMA_VERSION!r}")
    if str(getattr(holder, "schema_version", "")) != FPA.EVIDENCE_NOTE_SCHEMA_VERSION:
        raise PackWriterError(
            f"附注{'事实集' if note_state == 'facts' else '缺口'}的 schema_version="
            f"{getattr(holder, 'schema_version', None)!r} 不是当前版本 "
            f"{FPA.EVIDENCE_NOTE_SCHEMA_VERSION!r}")
    if str(holder.task_id) != task_id:
        raise PackWriterError("附注事实/缺口的 task_id 与 SectionTask 不一致")
    identity = _financial_artifact_identity(artifact)
    period = _financial_period_identity(artifact)
    _require_complete_identity({**identity, **period}, "FinancialAuthorityInput(artifact)")
    for key in _NOTE_IDENTITY_KEYS:
        actual = str(getattr(holder, key, "") or "")
        if actual != identity[key]:
            raise PackWriterError(
                f"附注的 {key} 与财务 artifact 权威身份不一致：{actual!r} != {identity[key]!r}"
                "（附注不得挂到别的公司/Contract 上）")
    # 附注自身的 `report_as_of` 在财务侧是**快照基准期**（生产者按 `pack.as_of_date` 填），
    # 因此只能与 artifact 的财务期末对齐：两个字段同名不同义，各自按生产者的语义核对，
    # 既不得用报告生成日顶替期末，也不得用期末冒充报告生成日。
    note_period = str(getattr(holder, "report_as_of", "") or "")
    if note_period != period["financial_period_end"]:
        raise PackWriterError(
            f"附注的基准期与财务 artifact 的财务期末不一致：{note_period!r} != "
            f"{period['financial_period_end']!r}（附注不得挂到别的期间上）")
    return artifact, holder, note_state, identity, period


def _financial_topics(topic_ids: Iterable[str],
                      fact_topic_map: Iterable[tuple[str, str]]) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """财务 topic 归属：`topic_ids` 非空去重；多 topic 必须有规范化 `fact_topic_map`。"""
    topics = tuple(str(t) for t in topic_ids)
    if not topics:
        raise PackWriterError("FinancialAuthorityInput.topic_ids 必须非空")
    if len(set(topics)) != len(topics):
        raise PackWriterError(f"FinancialAuthorityInput.topic_ids 含重复项：{list(topics)}")
    mapping = tuple((str(a), str(b)) for a, b in fact_topic_map)
    normalized = tuple(sorted(set(mapping)))
    if mapping != normalized:
        raise PackWriterError(
            "fact_topic_map 必须是 (fact_id, topic_id) 去重且升序的规范形，不得重复或乱序")
    if len(topics) > 1 and not normalized:
        raise PackWriterError(
            f"财务 task 声明了多个 topic {list(topics)}，而事实没有 topic 归属字段；"
            "必须显式给出 fact_topic_map，否则不得写作（不发明归属）")
    for fact_id, topic_id in normalized:
        if topic_id != OUTSIDE_SECTION_TOPIC and topic_id not in topics:
            raise PackWriterError(
                f"fact_topic_map 把 {fact_id!r} 归到本节声明之外的主题 {topic_id!r}"
                f"（本节主题 {list(topics)}）；确属本节之外必须显式写 "
                f"{OUTSIDE_SECTION_TOPIC!r}，不得随手改写归属")
    return topics, normalized


def _financial_body(*, task_id: str, section_id: str, topic_ids: Sequence[str],
                    fact_topic_map: Sequence[tuple[str, str]], artifact: Any, holder: Any,
                    note_state: str, identity: Mapping[str, str],
                    report_as_of: str, period: Mapping[str, str],
                    company_name: str = "") -> dict:
    """`FinancialAuthorityInput` 的规范身份体（`create()` 与 `__post_init__` 共用）。

    身份必须覆盖：artifact id + artifact 内容指纹 + 附注 canonical 内容指纹 +
    fact_topic_map + task/section/topic + 公司/Contract + **报告结论日 + 财务期间身份**。
    报告结论日与财务期末是两个不同的量，分别入体：只绑其一就等于允许另一项被悄悄换掉
    （换一份快照 / 换一个结论日），而输入 id 看不出来。

    `company_name`（`pwr-4`）同样入体且**不得省略**：它是渲染层会写进读者面的字段，换一个名称
    就换一段正文；把它排除在身份之外会让两份不同的读者面文字共用一个 `input_id`。
    """
    return {
        "task_id": task_id, "section_id": section_id,
        "topic_ids": [str(t) for t in topic_ids],
        "artifact_id": str(artifact.artifact_id),
        "artifact_content_fingerprint": _content_fingerprint_of(
            artifact, "FinancialPackArtifact"),
        "note_state": note_state,
        "note_content_fingerprint": _canonical_content_of(holder),
        "fact_topic_map": [[str(a), str(b)] for a, b in fact_topic_map],
        **{k: str(identity[k]) for k in _NOTE_IDENTITY_KEYS},
        "report_as_of": str(report_as_of),
        "company_name": str(company_name or ""),
        **{k: str(period[k]) for k in _FINANCIAL_PERIOD_KEYS},
    }


def _validate_financial_authority(obj: "FinancialAuthorityInput") -> None:
    """`FinancialAuthorityInput` 的唯一验证：`create()` / `__post_init__` / `from_dict()` 共用。"""
    artifact, holder, note_state, identity, period = _financial_artifact_and_note(
        obj.artifact, str(obj.task_id), obj.note_facts, obj.note_gap)
    if (obj.note_facts is not None) != (note_state == "facts"):
        raise PackWriterError("FinancialAuthorityInput 的 note 状态字段与 note_facts/note_gap 不符")
    topic_ids, fact_topic_map = _financial_topics(obj.topic_ids, obj.fact_topic_map)
    if tuple(str(t) for t in obj.topic_ids) != topic_ids:
        raise PackWriterError("FinancialAuthorityInput.topic_ids 必须是字符串元组")
    # 报告结论日（O-11 的单一瞬间）是**运行级**业务日期，artifact 里没有、也不该有它的权威，
    # 因此这里只要求调用方显式给出（不得留空 → 不得让财务期末顶替），不假装能在本节核对。
    if not str(obj.report_as_of or ""):
        raise PackWriterError(
            "FinancialAuthorityInput.report_as_of 必须是本轮报告生成日（O-11），留空即视为"
            "让财务期末顶替结论日：不得写作")
    for key in _NOTE_IDENTITY_KEYS:
        actual = str(getattr(obj, key, "") or "")
        if actual != identity[key]:
            raise PackWriterError(
                f"FinancialAuthorityInput.{key} 与财务 artifact 权威身份不一致："
                f"{actual!r} != {identity[key]!r}")
    body = _financial_body(task_id=obj.task_id, section_id=obj.section_id, topic_ids=topic_ids,
                           fact_topic_map=fact_topic_map, artifact=artifact, holder=holder,
                           note_state=note_state, identity=identity,
                           report_as_of=str(obj.report_as_of), period=period,
                           company_name=str(getattr(obj, "company_name", "") or ""))
    expected = NS.content_id(AUTHORITY_INPUT_ID_PREFIX, body)
    if str(obj.input_id or "") != expected:
        raise PackWriterError(
            f"FinancialAuthorityInput.input_id 与输入内容不符：声明 {obj.input_id!r}，"
            f"重算 {expected!r}（身份必须覆盖 artifact 与附注内容指纹）")


@dataclass(frozen=True)
class FinancialAuthorityInput:
    """financial 的封闭权威输入：任务绑定 artifact + 恰有其一 note 事实/note 缺口。

    `topic_ids` 只允许**单元素**：财务事实没有 topic 归属字段，多 topic 时若强行归派就是
    发明；此时调用方必须显式给出 `fact_topic_map`，否则 fail-closed。

    `report_as_of` 是**本轮报告生成日**（O-11），与 company/industry 的同一字段同源同值；
    财务数据期末**不是**它，它在 artifact 的期间身份里（`snapshot.snapshot_id` /
    `snapshot.as_of_date`，见 `_financial_period_identity`）。二者同名不同义的情形只存在于
    财务链内部旧字段，本节不以任何方式互相推导或顶替。

    `company_name`（`pwr-4`）是**读者面**的公司名称，由本次报告输入声明带入（`subj-2`：
    声明 + 与权威自己的来源文档 `declared_company_name` 核对，绝不从库里挑一个）。它**入
    身份体**：同一个 `company_id` 配两个不同名称会渲染出两段不同的读者面文字，那是两份不同的
    权威输入，不得共用一个 `input_id`。权威没有名称时它是空串，渲染退回主体标识（旧行为
    逐字保留，历史 run 的只读回放不受影响）。
    """

    producer_kind: str
    input_id: str
    task_id: str
    section_id: str
    company_id: str
    report_as_of: str
    contract_version: str
    contract_fingerprint: str
    topic_ids: tuple[str, ...]
    fact_topic_map: tuple[tuple[str, str], ...]
    artifact: Any
    note_facts: Any | None
    note_gap: Any | None
    #: 读者面名称（显示用；空串 = 权威没给名称，渲染退回主体标识）。
    company_name: str = ""

    def __post_init__(self) -> None:
        _validate_financial_authority(self)

    @classmethod
    def create(cls, task: PS.SectionTask, artifact: Any, *, note_facts: Any | None = None,
               note_gap: Any | None = None, fact_topic_map: Iterable[tuple[str, str]] = (),
               company_id: str | None = None, report_as_of: str | None = None,
               contract_version: str | None = None,
               contract_fingerprint: str | None = None,
               company_name: str = "") -> "FinancialAuthorityInput":
        artifact, holder, note_state, identity, period = _financial_artifact_and_note(
            artifact, task.task_id, note_facts, note_gap)
        topic_ids, fact_topic_map = _financial_topics(task.topic_ids, fact_topic_map)
        if report_as_of is None or str(report_as_of) == "":
            raise PackWriterError(
                "FinancialAuthorityInput.create 必须显式声明本轮报告生成日 report_as_of"
                "（O-11 的单一瞬间）：不得留空，也不得用财务期末顶替它")
        _assert_declared(identity, {"company_id": company_id,
                                    "contract_version": contract_version,
                                    "contract_fingerprint": contract_fingerprint},
                         "FinancialAuthorityInput")
        body = _financial_body(task_id=task.task_id, section_id=task.section_id,
                               topic_ids=topic_ids, fact_topic_map=fact_topic_map,
                               artifact=artifact, holder=holder, note_state=note_state,
                               identity=identity, report_as_of=str(report_as_of),
                               period=period, company_name=str(company_name or ""))
        obj = cls(producer_kind="financial_workflow",
                  input_id=NS.content_id(AUTHORITY_INPUT_ID_PREFIX, body),
                  task_id=task.task_id, section_id=task.section_id,
                  report_as_of=str(report_as_of), **identity,
                  topic_ids=topic_ids, fact_topic_map=fact_topic_map, artifact=artifact,
                  note_facts=note_facts, note_gap=note_gap,
                  company_name=str(company_name or ""))
        _validate_financial_authority(obj)
        return obj

    def topic_for_fact(self, fact_id: str) -> str:
        """fact 的主题归属：只能由显式映射给出（多 topic 时），或单 topic 的唯一归属。

        可能返回 `OUTSIDE_SECTION_TOPIC`——表示该 fact 明示属于本节之外，调用方必须据此把它
        记为「本节不呈现」，不得当成正文材料。
        """
        for mapped_fact, topic_id in self.fact_topic_map:
            if mapped_fact == fact_id:
                return topic_id
        if len(self.topic_ids) != 1:
            raise PackWriterError(
                f"财务 fact {fact_id!r} 没有 topic 归属（fact_topic_map 缺失），不得写作")
        return self.topic_ids[0]


_DERIVED_IDENTITY_KEYS = ("company_id", "report_as_of", "contract_version",
                          "contract_fingerprint")


def _derived_facts(demo_scope_selected: bool, upstream_identity: Mapping[str, str],
                   selected_facts: Iterable[tuple[str, str]],
                   required_fact_ids: Iterable[str]
                   ) -> tuple[dict[str, str], tuple[tuple[str, str], ...],
                              tuple[tuple[str, str], ...], tuple[str, ...]]:
    """derived 章节的唯一提取口：DemoScope 未选中即 fail-closed，上游身份必须齐备。"""
    if not demo_scope_selected:
        raise PackWriterError(
            "derived_section 未被 DemoScope 选中，M930-3 一律 fail-closed（不凭空派生总结）")
    identity: dict[str, str] = {}
    for key in _DERIVED_IDENTITY_KEYS + ("projection_id",):
        value = str(upstream_identity.get(key) or "")
        if not value:
            raise PackWriterError(f"derived 章节的上游身份不完整，缺少 {key!r}（不得绕过）")
        identity[key] = value
    upstream = tuple(sorted((str(k), str(v)) for k, v in upstream_identity.items()))
    selected = tuple(sorted(set((str(a), str(b)) for a, b in selected_facts)))
    if not selected:
        raise PackWriterError("derived 章节没有选中任何上游 fact，不得写作")
    return (identity, upstream, selected, tuple(sorted(str(x) for x in required_fact_ids)))


def _derived_body(*, task_id: str, section_id: str, topic_ids: Sequence[str],
                  upstream_identity: Sequence[tuple[str, str]],
                  selected_facts: Sequence[tuple[str, str]],
                  required_fact_ids: Sequence[str], identity: Mapping[str, str]) -> dict:
    """`DerivedAuthorityInput` 的规范身份体（`create()` 与 `__post_init__` 共用）。"""
    return {
        "task_id": task_id, "section_id": section_id,
        "topic_ids": [str(t) for t in topic_ids],
        "upstream_identity": [[str(k), str(v)] for k, v in upstream_identity],
        "selected_facts": [[str(a), str(b)] for a, b in selected_facts],
        "required_fact_ids": [str(x) for x in required_fact_ids],
        **{k: str(identity[k]) for k in _DERIVED_IDENTITY_KEYS},
    }


def _validate_derived_authority(obj: "DerivedAuthorityInput") -> None:
    """`DerivedAuthorityInput` 的唯一验证：`create()` / `__post_init__` / `from_dict()` 共用。"""
    identity, upstream, selected, required = _derived_facts(
        bool(obj.demo_scope_selected), dict(obj.upstream_identity), obj.selected_facts,
        obj.required_fact_ids)
    if tuple(obj.upstream_identity) != upstream:
        raise PackWriterError(
            "DerivedAuthorityInput.upstream_identity 必须是 (key, value) 去重且升序的规范形")
    if tuple(obj.selected_facts) != selected:
        raise PackWriterError(
            "DerivedAuthorityInput.selected_facts 必须是 (fact_id, source) 去重且升序的规范形")
    if tuple(obj.required_fact_ids) != required:
        raise PackWriterError("DerivedAuthorityInput.required_fact_ids 必须是去重且升序的规范形")
    for key in _DERIVED_IDENTITY_KEYS:
        actual = str(getattr(obj, key, "") or "")
        if actual != identity[key]:
            raise PackWriterError(
                f"DerivedAuthorityInput.{key} 与上游权威身份不一致：{actual!r} != {identity[key]!r}")
    body = _derived_body(task_id=obj.task_id, section_id=obj.section_id,
                         topic_ids=obj.topic_ids, upstream_identity=upstream,
                         selected_facts=selected, required_fact_ids=required,
                         identity=identity)
    expected = NS.content_id(AUTHORITY_INPUT_ID_PREFIX, body)
    if str(obj.input_id or "") != expected:
        raise PackWriterError(
            f"DerivedAuthorityInput.input_id 与输入内容不符：声明 {obj.input_id!r}，重算 {expected!r}")


@dataclass(frozen=True)
class DerivedAuthorityInput:
    """章节派生总结的权威输入。

    M930-3 的 DemoScope（§五）没有选中任何 derived 章节，因此本批**一律 fail-closed**：
    只有显式 `demo_scope_selected=True` 且上游身份字段齐全时才允许构造。
    """

    producer_kind: str
    input_id: str
    task_id: str
    section_id: str
    company_id: str
    report_as_of: str
    contract_version: str
    contract_fingerprint: str
    topic_ids: tuple[str, ...]
    upstream_identity: tuple[tuple[str, str], ...]
    selected_facts: tuple[tuple[str, str], ...]
    required_fact_ids: tuple[str, ...]
    #: DemoScope 是否选中本 derived 章节。**必须存成字段**：否则直接调用 dataclass 构造器
    #: 就能绕开 `create()` 里唯一的那道开关（§三 P0）。缺省 False ⇒ 直接构造一律 fail-closed。
    demo_scope_selected: bool = False

    def __post_init__(self) -> None:
        _validate_derived_authority(self)

    @classmethod
    def create(cls, task: PS.SectionTask, *, demo_scope_selected: bool,
               upstream_identity: Mapping[str, str],
               selected_facts: Iterable[tuple[str, str]] = (),
               required_fact_ids: Iterable[str] = ()) -> "DerivedAuthorityInput":
        identity, upstream, selected, required = _derived_facts(
            demo_scope_selected, upstream_identity, selected_facts, required_fact_ids)
        topic_ids = tuple(str(t) for t in task.topic_ids)
        body = _derived_body(task_id=task.task_id, section_id=task.section_id,
                             topic_ids=topic_ids, upstream_identity=upstream,
                             selected_facts=selected, required_fact_ids=required,
                             identity=identity)
        obj = cls(producer_kind="derived_section",
                  input_id=NS.content_id(AUTHORITY_INPUT_ID_PREFIX, body),
                  task_id=task.task_id, section_id=task.section_id,
                  **{key: identity[key] for key in _DERIVED_IDENTITY_KEYS},
                  topic_ids=topic_ids, upstream_identity=upstream, selected_facts=selected,
                  required_fact_ids=required, demo_scope_selected=True)
        _validate_derived_authority(obj)
        return obj


WorkerAuthorityInput = (TopicPackAuthorityInput | FinancialAuthorityInput | DerivedAuthorityInput)


# ---------------------------------------------------------------------------
# 生成器接口：唯一外部能力，且只有「一次文本生成」
# ---------------------------------------------------------------------------

#: `NarrationResult.status` 的封闭取值。
NARRATION_STATUSES = ("ok", "error")
#: model policy → 实际调用模型：`stub`（脚本替身）与 `provider_default`（`llm.client` 默认模型）。
#: model policy 必须能**确定性解析**成模型名，否则「Draft 里写一个、实际调另一个」无法被发现。
MODEL_POLICY_STUB = "stub"
MODEL_POLICY_PROVIDER_DEFAULT = "provider_default"
MODEL_POLICIES = (MODEL_POLICY_STUB, MODEL_POLICY_PROVIDER_DEFAULT)


def resolve_model_policy(model_policy: str) -> str:
    """model policy → 该策略下**必须**出现的实际模型名（未知策略 fail-closed）。"""
    if model_policy == MODEL_POLICY_STUB:
        return MODEL_POLICY_STUB
    if model_policy == MODEL_POLICY_PROVIDER_DEFAULT:
        return str(llm.LLM_MODEL)
    raise PackWriterError(
        f"未登记的 model policy {model_policy!r}（只允许 {list(MODEL_POLICIES)}）："
        "无法确定性解析实际模型即不得写作")


@dataclass(frozen=True)
class NarrationResult:
    """一次叙事生成的**结构化**结果（§五）：文本 + 完整调用元数据。

    只回传字符串就等于丢掉「这次正文是谁、按哪版 prompt、以什么代价生成」的全部证据。
    stub 与真实 client 必须返回**同一**结构（否则「测试里过的路径」与「真实跑的路径」不同）。
    """

    text: str
    call_id: str
    model: str
    prompt_version: str
    status: str = "ok"
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0
    finish_reason: str | None = None
    error: str = ""

    def __post_init__(self) -> None:
        if self.status not in NARRATION_STATUSES:
            raise PackWriterError(
                f"NarrationResult.status={self.status!r} 不在 {list(NARRATION_STATUSES)} 内")
        for name in ("call_id", "model", "prompt_version"):
            if not str(getattr(self, name) or ""):
                raise PackWriterError(f"NarrationResult.{name} 不得为空（调用元数据必须完整）")
        if not isinstance(self.text, str):
            raise PackWriterError("NarrationResult.text 必须是字符串")
        if self.status == "error" and not self.error:
            raise PackWriterError("NarrationResult.status='error' 必须带 error 说明（不得静默失败）")
        for name in ("input_tokens", "output_tokens"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, int):
                raise PackWriterError(
                    f"NarrationResult.{name} 只能是 int 或 None（provider 未返回时不得记 0）")
        if not isinstance(self.latency_ms, int):
            raise PackWriterError("NarrationResult.latency_ms 必须是整数毫秒")

    @property
    def response_hash(self) -> str:
        """响应内容指纹（64 位 sha256；唯一实现复用 `NS.body_fingerprint_of`）。"""
        return NS.body_fingerprint_of(self.text)

    def to_dict(self) -> dict:
        return {"call_id": self.call_id, "model": self.model,
                "prompt_version": self.prompt_version, "status": self.status,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "latency_ms": self.latency_ms, "finish_reason": self.finish_reason,
                "error": self.error, "response_hash": self.response_hash}


class NarrationClient(Protocol):
    """Writer 能拿到的全部外部能力。没有任何检索/工具入口。"""

    def narrate(self, *, messages: Sequence[Mapping[str, str]], system: str,
                prompt_version: str, model_policy: str) -> NarrationResult: ...


class LlmNarrationClient:
    """把 `llm.client` 适配成 NarrationClient（不带工具、不重试、不缓存）。

    `calls` 逐次记下本适配器**真正发起**的调用（含失败），与离线替身的同名属性对齐：真实模式下
    也必须有客户端侧的调用记录，否则「真实跑了多少次」只能靠别的层自报（§六 计量）。它只记
    结构化元数据，不记 prompt 正文与隐藏推理。

    `max_tokens_by_prompt_version`：各阶段的**输出容量按 prompt 版本**给足（不是全链一个统一
    值）。本链的四个调用点输出结构不同——门前提案与门后自然组织要出完整正文与草稿单元，逐候选
    蕴含只出一条结构化决定——统一值要么浪费容量、要么在某一阶段截断。缺省 `max_tokens` 只作
    兜底；本次实际发出去的数字按次记进 `calls`，产物据此复核而不必读代码。

    `reject_truncated=True`（缺省）：provider 说输出被截断时，这次调用**算失败**——适配器抛出
    `LLMTruncatedResponse`，**不**返回一份残缺的 `NarrationResult`。残缺 JSON 若被当成输出流
    下去，下游只会看到「JSON 解析失败」，真正的原因（截断）就丢了。适配器只如实停止，不重跑、
    不换模型、不换 prompt。
    """

    def __init__(self, *, model: str | None = None, max_tokens: int = 4096,
                 thinking: dict | None = None,
                 max_tokens_by_prompt_version: Mapping[str, int] | None = None,
                 reject_truncated: bool = True) -> None:
        self.model = model
        self.max_tokens = max_tokens
        self.thinking = thinking
        self.max_tokens_by_prompt_version = dict(max_tokens_by_prompt_version or {})
        self.reject_truncated = bool(reject_truncated)
        self.calls: list[dict] = []

    def capacity_for(self, prompt_version: str | None) -> int:
        """本次调用实际发往 provider 的输出上限（按 prompt 版本；产物与账本据此复核）。"""
        return int(self.max_tokens_by_prompt_version.get(str(prompt_version), self.max_tokens))

    def narrate(self, *, messages: Sequence[Mapping[str, str]], system: str,
                prompt_version: str, model_policy: str) -> NarrationResult:
        max_tokens = self.capacity_for(prompt_version)
        try:
            resp = llm.chat_with_usage(list(messages), system=system, model=self.model,
                                       max_tokens=max_tokens, prompt_version=prompt_version,
                                       thinking=self.thinking,
                                       reject_truncated=self.reject_truncated)
        except llm.LLMTruncatedResponse as exc:
            # 截断带 provider 侧身份（call_id / usage），因此**不**用请求内容指纹顶替：客户端
            # 记录必须与账本、`logs/llm` 按同一个 call_id 对上。然后**抛出去**——这次调用失败，
            # 不给调用方留一份可以接着用的残缺正文。
            resp = exc.response
            self.calls.append({"call_id": resp.call_id, "prompt_version": prompt_version,
                               "model_policy": model_policy, "status": "error",
                               "model": resp.model, "max_tokens": max_tokens,
                               "input_tokens": resp.input_tokens,
                               "output_tokens": resp.output_tokens,
                               "finish_reason": resp.finish_reason,
                               "error": f"{type(exc).__name__}: {exc}"})
            raise
        except Exception as exc:  # noqa: BLE001 — 传输失败也要留下**结构化**调用记录
            # 失败没有 provider 侧 call_id，用**请求内容指纹**当这次调用的身份（同一次请求
            # 必得同 id），绝不伪造一个看起来像 provider id 的随机串。
            call_id = "error-" + NS.content_id("", {"messages": list(messages),
                                                    "system": system})[:16]
            self.calls.append({"call_id": call_id, "prompt_version": prompt_version,
                               "model_policy": model_policy, "status": "error",
                               "max_tokens": max_tokens,
                               "error": f"{type(exc).__name__}: {exc}"})
            return NarrationResult(
                text="", call_id=call_id,
                model=str(self.model or llm.LLM_MODEL), prompt_version=prompt_version,
                status="error", error=f"{type(exc).__name__}: {exc}")
        self.calls.append({"call_id": resp.call_id, "prompt_version": prompt_version,
                           "model_policy": model_policy, "status": "ok",
                           "model": resp.model, "max_tokens": max_tokens,
                           "input_tokens": resp.input_tokens,
                           "output_tokens": resp.output_tokens,
                           "latency_ms": resp.latency_ms})
        return NarrationResult(
            text=resp.text, call_id=resp.call_id, model=resp.model,
            prompt_version=prompt_version, status="ok",
            input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
            latency_ms=resp.latency_ms, finish_reason=resp.finish_reason)


# prompt 资产**自报**的 (资产名, 修订号)。资产的修订号与代码里登记的版本必须一致，否则
# 「记录的是 organizer-4、实际加载的是别的修订」会让 prompt 身份变成一句空话（§五 3）。
_PROMPT_DECL_RE = re.compile(
    r"(?P<asset>[A-Za-z0-9_.\-]+)\s*[,，]\s*revision\s+(?P<revision>[A-Za-z0-9_.\-]+)")


def prompt_asset_fingerprint(text: str) -> str:
    """prompt 资产的**正文内容指纹**：行尾先归一（CRLF/CR → LF）再求 sha256（UTF-8）。

    行尾归一不是宽容：同一份资产在 Windows/Unix 检出下字节不同、文本相同；按原始字节固定断言
    会把 EOL 差异误报成「资产被换过」。除此之外不丢弃任何字符——空白、缩进、注释、标点的任何
    改动都会改变指纹。
    """
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    return CS2.sha256_text(normalized)


def verify_prompt_asset(system: str, policy: WriterPolicy) -> None:
    """核对「记录版本 = 加载到的 prompt 资产」，不一致立即拒绝（§五 2/3、§16.7.1 P18）。

    三侧都必须一致：`WriterPolicy.prompt_version` = 代码登记的 `NARRATION_PROMPT_VERSION`；
    **实际加载到的资产文本**自报的就是这个资产名 + 修订号；且其**正文指纹**等于登记的
    `NARRATION_PROMPT_SHA256`。任何一侧漂移都 fail-closed——前两侧可以被照抄，正文不会被照抄。
    """
    if policy.prompt_version != NARRATION_PROMPT_VERSION:
        raise PackWriterError(
            f"WriterPolicy.prompt_version={policy.prompt_version!r} 与当前登记版本 "
            f"{NARRATION_PROMPT_VERSION!r} 不一致（记录版本必须等于实际加载的 prompt 版本）")
    match = _PROMPT_DECL_RE.search(str(system or ""))
    if not match:
        raise PackWriterError(
            f"prompt 资产 {NARRATION_PROMPT_ASSET!r} 未自报 (资产名, revision)，"
            "无法证明记录版本与实际加载版本一致")
    declared = f"{match.group('asset')}@{match.group('revision')}"
    if declared != NARRATION_PROMPT_VERSION:
        raise PackWriterError(
            f"实际加载的 prompt 资产自报 {declared!r}，与记录版本 "
            f"{NARRATION_PROMPT_VERSION!r} 不一致（prompt 资产被换过：不得用旧记录冒充）")
    actual_sha256 = prompt_asset_fingerprint(system)
    if actual_sha256 != NARRATION_PROMPT_SHA256:
        raise PackWriterError(
            f"prompt 资产正文指纹 {actual_sha256!r} 与登记值 {NARRATION_PROMPT_SHA256!r} 不一致："
            f"资产 {NARRATION_PROMPT_ASSET!r} 的正文被改过（资产名与修订号可被照抄，正文不行）")


def verify_narration_result(result: NarrationResult, policy: WriterPolicy) -> None:
    """核对一次生成结果的身份：状态、prompt 版本、实际模型（§五 1/4）。

    这三项都不在「正文文本」里，因此只能靠结构化结果核对——`Draft` 写的是一个 model policy，
    实际跑的是另一个模型时，正文看起来完全一样。
    """
    if not isinstance(result, NarrationResult):
        raise PackWriterError(
            "NarrationClient 必须返回结构化的 NarrationResult（不得只回传文本："
            "调用元数据不可丢）")
    if result.status != "ok":
        raise PackWriterError(f"生成调用失败（status={result.status!r}）：{result.error}")
    if result.prompt_version != policy.prompt_version:
        raise PackWriterError(
            f"实际调用使用的 prompt 版本 {result.prompt_version!r} 与 WriterPolicy 记录 "
            f"{policy.prompt_version!r} 不一致（记录与实际调用不得是两套）")
    expected_model = resolve_model_policy(policy.model_policy)
    if result.model != expected_model:
        raise PackWriterError(
            f"实际调用模型 {result.model!r} 与 model policy {policy.model_policy!r} 解析出的 "
            f"{expected_model!r} 不一致（不得一个写在 Draft、另一个实际调用）")


# ---------------------------------------------------------------------------
# 规划：先把权威事实变成 Claim 与类型化支撑图
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuthorityFactEntry:
    """一条**预验证权威事实**的确定性读视图（§6.2 路径 A 的**唯一**可选集）。

    这是 Writer 侧能绑定的事实全集：它由 authority 自身**确定性派生**（Container / fact 身份 /
    文本 / 期间 / 口径 / 归属主题 / material 与 payload / locator / source 与 provenance
    身份），**没有**任何字段来自模型自报。模型只能在提案里**选择**这里的条目，选择结果由
    写入侧回查本表核验（`_resolve_fact_declaration`）。

    `authority_kind` 是四元 union 的成员；`fact_id` 按该 kind 的 branch-specific 字段取值
    （`external_snapshot` 时它是 `external_fact_id`），因此同一张表里四种事实身份不会互相冒充。
    """

    authority_kind: str
    container_identity: str
    fact_id: str
    text: str
    topic_id: str
    aspect_ids: tuple[str, ...]
    required: bool
    fact_type: str
    period: str
    scope: str
    material_id: str | None = None
    payload_ref: dict | None = None
    locator_ref: dict | None = None
    source_identity: str = ""
    provenance_identity: str = ""
    content_fingerprint: str = ""
    #: 事实**自己声明的逐值身份**（`SupportedFact.value_identity` 的 dict；`nd-2` 起分业务营收
    #: 事实携带）。它**不是**读者面字段，也不是新的坐标：条目的四周身份仍由上面的类型化字段
    #: 承担。它只是把「这条事实授权的是哪个值」原样带到写作侧，使逐句核对不必从 `text` 那段
    #: **逐字前缀**反推（前缀里含前几年的值）。无身份的事实留 `None`（财务／附注／外部/模型 claim）。
    value_identity: dict | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        """唯一的权威事实坐标 `(authority_kind, container_identity, fact_id)`（§16.7.1 P5）。"""
        return (self.authority_kind, self.container_identity, self.fact_id)

    def fact_kwargs(self) -> dict:
        """branch-specific 事实字段（四条 fact 字段里**恰好一条**非空，不混装）。"""
        field_name = NS.FACT_FIELD_BY_AUTHORITY_KIND[self.authority_kind]
        return {field_name: self.fact_id}


def _authority_fact_table(facts: Sequence[AuthorityFactEntry]) -> dict[tuple[str, str, str], Any]:
    """权威事实表：坐标 → 条目。同一坐标出现两次即拒（事实身份必须唯一）。"""
    table: dict[tuple[str, str, str], Any] = {}
    for entry in facts:
        if entry.key in table:
            raise PackWriterError(
                f"权威事实坐标重复：{entry.key}（同一 (kind, container, fact) 只能有一条）")
        table[entry.key] = entry
    return table


@dataclass(frozen=True)
class AuthorityScan:
    """一次权威输入的确定性读视图：aspect 状态、**权威事实目录**、缺口投影。"""

    facts: tuple[AuthorityFactEntry, ...]
    aspect_status: Mapping[str, str]
    aspect_topic: Mapping[str, str]
    aspect_impact: Mapping[str, str]
    aspect_blocking: Mapping[str, str]
    aspect_question: Mapping[str, str]
    excluded_facts: tuple[tuple[str, str, str], ...]   # (container, fact_id, topic_id)
    conflicts: tuple[dict, ...]
    not_found: tuple[dict, ...]
    gaps: tuple[dict, ...]
    coverage_counts: dict
    #: §三 A / 3.1：aspect → Contract **逐字**的中文业务要求（`requirement_text`）。这是投给
    #: 生成器的「这条 aspect 到底要什么」，与 `aspect_question`（问题归属 id）不是一回事：
    #: id 只能定位，要求才能被读。缺它则请求面退化成一张裸 id 表（见 `_pack_aspect_meta`）。
    aspect_requirement: Mapping[str, str] = field(default_factory=dict)
    #: 本节 topic → 其 `TopicResearchRequirement` 的确定性 requirement id。`FollowUpNeed.
    #: target_requirement_id` 的**唯一**口径来自 `harness.topic_runtime.topic_requirement_id`，
    #: 这里只是把它按 topic 索引好，绝不在本模块另写一套拼装。
    requirement_ids: Mapping[str, str] = field(default_factory=dict)
    #: 本节 topic 中**没有任何事实**可用的那些（只能是显式缺口，不得空写或合并进别的主题）。
    topics_without_facts: tuple[str, ...] = ()
    #: 本节之外主题的缺口（如其他财务章节的指标缺口）：只登记备查，不冒充本节缺口。
    out_of_scope_gaps: tuple[dict, ...] = ()
    #: §十二 1/3 + §六：权威 fact 自己**没有**可核验的显式期间，而它的事实类型又要求期间
    #: （时段量/时点量/推断），或它的事实类型无法判定。这类 fact 一律不进入正文（不得用
    #: `report_as_of` 或报告期措辞顶替），只能形成 `period_unresolved` 缺口。
    #: 元素是 (container, fact_id, topic_id)。
    period_unresolved: tuple[tuple[str, str, str], ...] = ()
    #: §六：逐条排除项的**确定性原因**（容器/fact/topic/原因码/判据快照），供人读报告与审计。
    period_exclusions: tuple[dict, ...] = ()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): _jsonable(value[k]) for k in sorted(value, key=str)}
    fields = getattr(value, "__dataclass_fields__", None)
    if fields:
        return {name: _jsonable(getattr(value, name)) for name in sorted(fields)}
    return str(value)


def _citation_of(fact: Any) -> tuple[Any, ...]:
    refs = tuple(getattr(fact, "citation_refs", ()) or ())
    if not refs:
        raise PackWriterError(f"权威 fact 没有 citation_refs，不得成为正文 claim：{fact!r:.120}")
    return refs


def _question_ids_for_topic(task: PS.SectionTask, topic_id: str, what: str) -> tuple[str, ...]:
    """claim 的 question 归属来自 SectionTask 的计划问题（任务侧权威），不自行编号。"""
    ids = tuple(str(q.question_id) for q in task.questions if str(q.topic_id) == topic_id)
    if ids:
        return ids
    if not task.questions:
        raise PackWriterError(
            f"{what} 的 Claim 无法绑定 question_id（SectionTask.questions 为空）；"
            "canonical SectionResult 拒绝无 question 的 fact claim，fail-closed")
    raise PackWriterError(
        f"{what} 的 topic {topic_id!r} 在 SectionTask.questions 里没有对应问题，"
        "不得把 claim 挂到别的主题的问题上")


def _blocking_effects(raw: Any) -> tuple[str, ...]:
    """阻断后果规范化：唯一实现是 `SS.normalize_blocking_effects`（不在此重写第二套）。"""
    return SS.normalize_blocking_effects(raw)


def _question_blocking_policy(task: PS.SectionTask) -> dict[str, tuple[str, ...]]:
    """权威 task 里每个问题的 `blocking_policy`（未解决项后果字段的**唯一**来源）。

    值原样保留（含契约显式写出的 `NONE` 哨兵——它表示「不阻断」，由
    `SS.normalize_blocking_effects` 落成空集合）。真正越界的等级仍然 fail-closed：
    不静默丢弃、不 continue。
    """
    out: dict[str, tuple[str, ...]] = {}
    for question in task.questions:
        values = tuple(str(v) for v in (getattr(question, "blocking_policy", ()) or ()))
        for value in values:
            if value not in CS.BLOCKING_LEVELS:
                raise PackWriterError(
                    f"问题 {question.question_id!r} 的 blocking_policy 含未登记等级 {value!r}"
                    f"（只允许 {list(CS.BLOCKING_LEVELS)}）")
        out[str(question.question_id)] = values
    return out


def _authority_blocking_effects(question_id: str, question_policy: Mapping[str, tuple[str, ...]],
                               aspect_blocking: Any) -> tuple[str, ...]:
    """未解决项的 `blocking_effects` 只能取自**它所登记的那个权威问题**。

    §八 2：既有确定性规则 3（`sections.rules_evaluator._rule_blocking`）把未解决项的
    `blocking_effects` 与 `task.questions[question_id].blocking_policy` 比对，两侧过的是
    **同一套** `SS.normalize_blocking_effects`（`NONE` 哨兵 ≡ 空集合），与
    `research_common._make_unresolved` 的既有约定一致。因此后果字段的来源只能是权威问题
    声明本身：
    * 登记了问题且该问题在权威 task 里 → 取该问题 `blocking_policy` 的规范化值；
    * aspect 自带的那份 `blocking_policy` 是另一套口径，**不得**覆盖问题声明（否则
      「question 说 SECTION_BLOCKED、aspect 只写 NONE」会被静默降级成不阻断）；
    * 没有可登记的问题时保持原样——此时规则 3 的期望值正是空元组，留空即一致。
    """
    if not question_id:
        return _blocking_effects(aspect_blocking)
    declared = question_policy.get(question_id)
    if declared is None:
        # 登记了问题却不在权威 task 内：不发明后果，交给既有规则 3 判定（fail-closed）。
        return _blocking_effects(aspect_blocking)
    return SS.normalize_blocking_effects(declared)


def _unresolved_projections(unresolved: Sequence[Any],
                            authority_status: Mapping[str, str]) -> tuple[dict, ...]:
    """缺口投影的**规范形态**：必须与 draft 内部留存的那一份逐字节一致。

    §五 4：正文由唯一渲染器从 draft 生成。`SectionDraft` 会对投影做规范化排序（顺序不参与
    身份），因此写作器编正文前也必须先用同一个入口规范化，否则「同一批缺口、不同顺序」会让
    正文与 draft 重算结果不一致（组装器逐字节比对时直接拒绝）。
    """
    return NS.canonical_unresolved_projections(tuple({
        "unresolved_id": u.unresolved_id, "topic_id": u.topic_id, "state": u.state,
        "reason_code": u.reason_code, "impact_scope": list(u.impact_scope),
        "blocking_effects": list(u.blocking_effects), "detail": u.detail,
        "authority_status": authority_status.get(u.unresolved_id, "")}
        for u in unresolved))


def _note_citation(fact: Any) -> Any:
    """附注事实的引用锚点。**唯一**实现在 `NS.note_citation`（门按同一套派生核验归属）。"""
    try:
        return NS.note_citation(fact)
    except NS.NarrativeSchemaError as exc:
        raise PackWriterError(str(exc)) from exc


def _pack_aspect_meta(pack_set: Any) -> dict[str, dict]:
    """requirement snapshot 只读投影。

    `impact_scope` / `blocking_policy` 在 `TopicAspectRequirementSnapshot` 里是 **tuple**，
    这里必须原样保留（不得 str() 掉），否则 canonical 词表判定会永远落空。

    `kind` / `time_scope` 是 §六 期间语义的**唯一**判据来源（frozen Contract 的 `ASPECT_KINDS`
    与 `TIME_POLICIES`，与 WritingSpec 的 `period_language_policy` 同一个封闭词表）；不在正文
    里找关键词、也不按公司/行业猜事实类型。

    `requirement_text`（§三 A / 3.1）：Contract **逐字**写着这条 aspect 要什么（中文业务要求，
    例如「列示报告期内主营业务收入的构成」）。它**只在 Contract 里**，WritingSpec 的 mappings
    只有槽位字段，因此不在别处派生、更不由模型或本模块改写。丢掉它，模型只能看到
    `company_business_main.revenue_breakdown` 这样一个英文点号 id——这正是「唯一运行链」里
    投给生成器的**业务要求**之所以曾经只剩裸 id 的原因。
    """
    meta: dict[str, dict] = {}
    for req in tuple(getattr(pack_set, "requirements", ()) or ()):
        for aspect in tuple(getattr(req, "aspects", ()) or ()):
            meta[str(aspect.aspect_id)] = {
                "topic_id": str(getattr(aspect, "topic_id", "") or ""),
                "question_id": str(getattr(aspect, "question_id", "") or ""),
                "requirement_text": str(getattr(aspect, "requirement_text", "") or ""),
                "impact_scope": getattr(aspect, "impact_scope", "") or (),
                "blocking_policy": getattr(aspect, "blocking_policy", "") or (),
                "display_tier": str(getattr(aspect, "display_tier", "") or ""),
                "content_role": str(getattr(aspect, "content_role", "") or ""),
                "kind": str(getattr(aspect, "kind", "") or ""),
                "time_scope": str(getattr(aspect, "time_scope", "") or ""),
            }
    return meta


def _material_binding_of(authority: TopicPackAuthorityInput, pack: Any, container_id: str,
                         citation: Any, fact: Any, *, for_support_edge: bool = True,
                         ) -> tuple[str | None, dict | None]:
    """事实的引用锚点 → 本 Pack 内 material 的绑定（**唯一**入口，§四）。

    `NS.material_binding_for_citation` 用 `TS.citation_source_identity` 把 `CitationRef` 与
    `material.source_identity` 放到**同一个身份域**里比对。这里不再有任何前缀拼接。

    事实自己的**已核验输入材料**（`NS.fact_verified_input_material_ids`，`mbind-1`）随 `fact`
    一起送进同一次解析：唯一时它会先把同来源身份下的候选收窄再按页定位，取不到就完全按旧规则
    办。收窄**不是**开关——它既不放宽 fail-closed，也不绕过 `for_support_edge` 的任何一条。

    找不到 material 时返回 `(None, None)`：这与门自己的判据同向——门只在「该事实的引用**确实**
    能落到本容器内一个 material」时才产生期望（`_authority_material_expectations`：「找不到就不
    产生期望，此时不要求绑定」）。因此「上游没有 material」**不是**漏绑，不得在这里升格成拒绝；
    反过来，能唯一解析却绑错/漏绑，由门以 `support_material_unbound` 阻断。

    歧义（同来源身份下多个候选且 citation 定位无法消歧）与来源身份派生失败仍然 fail-closed：
    前者的 silence 会把血缘变成实现细节，后者说明引用锚点本身不可解释。

    `for_support_edge` 把「这个事实在正文里有没有支撑边」这一个**已经由上游决定**的区别带进来：

    * `True`（默认，扫描读视图）：事实会作为 support edge 的权威面进入正文，正文必须能说出
      「这条 Claim 出自哪份材料」⇒ 歧义 fail-closed，整节不写；
    * `False`（`company_worker._derive_fact_identity`：period_unresolved / 越出本节 aspect
      范围的事实）：这些事实**不进入正文**，只需要一条可回查的事实身份（FND 去向为
      `not_presented_with_reason`），没有任何支撑边要指认材料。此时歧义走「没有 material
      绑定」那一条既有分支——候选本来就是按 citation 的来源身份选出来的，故 `source_identity`
      与绑定成功时**逐字相同**，只有 material 自己的 provenance / content fingerprint 退回到
      事实自己的资格决定与表面指纹。这里同样**一个 material 都不选**（不是任选其一），也不
      放宽任何事实判据：零事实句的 FND 去向仍然逐条在场。

    反过来说：不得把它当开关用来让有支撑边的事实绕过歧义——调用点只有这两处，且扫描侧恒为
    `True`。
    """
    fact_id = str(getattr(fact, "fact_id", "") or "")
    try:
        binding = NS.material_binding_for_citation(authority, container_id, citation,
                                                   fact=fact, pack=pack)
    except NS.MaterialBindingAmbiguityError as exc:
        if not for_support_edge:
            return None, None
        # §四：同来源身份下多个候选且 citation 定位无法消歧 —— 不得任选一个（任选即把血缘
        # 变成实现细节），整节不写。
        raise PackWriterError(
            f"fact {fact_id!r} 的引用在容器 {container_id!r} 内对应多个 material 候选，"
            f"无法唯一确定它出自哪份材料：{exc}") from exc
    except NS.NarrativeSchemaError as exc:
        raise PackWriterError(
            f"fact {fact_id!r} 的引用锚点无法派生来源身份（不得手写前缀）：{exc}") from exc
    if binding is None:
        return None, None
    return binding


def _pack_material_index(pack: Any) -> dict[str, tuple[Any, Any]]:
    """`material_id` → (material, ResearchMaterialDisposition) 的**唯一**配对口（§6.4.1 第 1 层）。

    Writer 消费的材料清单就是本 Pack 的 materials；它们的**Pack 侧去向**必须逐条在场，否则
    「这份材料是怎么进来的」在血缘里断了一节。同一 material 出现两次、或某成员缺/多一条去向，
    一律 fail-closed（不做「最后写入者胜出」）。
    """
    pack_id = str(getattr(pack, "pack_id", "") or "")
    materials: dict[str, Any] = {}
    for material in tuple(getattr(pack, "materials", ()) or ()):
        material_id = str(getattr(material, "material_id", "") or "")
        if not material_id:
            raise PackWriterError(f"pack {pack_id!r} 含 material_id 为空的材料（成员身份必须非空）")
        if material_id in materials:
            raise PackWriterError(
                f"pack {pack_id!r} 内 material_id 重复：{material_id!r}（成员身份必须唯一）")
        materials[material_id] = material
    dispositions: dict[str, Any] = {}
    for rmd in tuple(getattr(pack, "material_dispositions", ()) or ()):
        material_id = str(getattr(rmd, "material_id", "") or "")
        if not material_id:
            raise PackWriterError(
                f"pack {pack_id!r} 含 material_id 为空的 ResearchMaterialDisposition")
        if material_id in dispositions:
            raise PackWriterError(
                f"pack {pack_id!r} 内 material {material_id!r} 有多条 ResearchMaterialDisposition"
                "（每个材料成员恰一条 Pack 侧去向）")
        dispositions[material_id] = rmd
    missing = sorted(set(materials) - set(dispositions))
    extra = sorted(set(dispositions) - set(materials))
    if missing or extra:
        raise PackWriterError(
            f"pack {pack_id!r} 的 materials 与 ResearchMaterialDisposition 不是同一个集合："
            f"缺去向 {missing}，多余去向 {extra}（每个成员都必须可追溯 Pack 侧去向）")
    return {mid: (materials[mid], dispositions[mid]) for mid in sorted(materials)}


def _fact_surface_fingerprint(authority_kind: str, container_identity: str, fact_id: str,
                              texts: Sequence[str]) -> str:
    """**无 material 载体**的事实的 content fingerprint（确定性、非自报）。

    它取自权威侧的事实面文本（`NS.authority_numeric_texts` 的逐 kind 命题/规范值）并与容器与
    事实身份绑定，因此「同一条事实换了内容」必然换指纹。它**不是** material 的 content_hash：
    没有 material 载体时那个值不存在，不得借用、不得编造。
    """
    return hashlib.sha256(NS.canonical_json({
        "authority_kind": authority_kind, "container_identity": container_identity,
        "fact_id": fact_id, "texts": [str(t) for t in texts]}).encode("utf-8")).hexdigest()


def _value_identity_dict(fact: Any) -> dict | None:
    """事实自己声明的逐值身份 → JSON-safe dict；没有就返回 `None`（**不**造一个空壳）。

    「没有逐值身份」与「有但分量空」不是一回事：前者（模型 claim／财务／附注／外部）由写作侧
    按原判据处理，后者在写作侧会被判成「这条事实不授权任何数字」。这里原样透传，不做任何补齐。
    """
    vi = getattr(fact, "value_identity", None)
    if vi is None:
        return None
    to_dict = getattr(vi, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    raise PackWriterError(
        f"事实 {getattr(fact, 'fact_id', '?')!r} 的 value_identity 不是类型化 ValueIdentity"
        "（不得手写身份字典）")


def _external_locator_ref(external: Any) -> dict:
    """`external_snapshot` 路径 A 的 exact locator：`whole_payload` 变体，owner 为 snapshot 容器。

    `ExternalLocator` **没有**字符区间（它只有 snapshot 身份 / URL / domain / 抓取与发布时间），
    而 `validate_support_edge_shape` 要求 external 路径 A 必须带 exact locator。旧 wire 把这件事
    伪装成 `(container, 0, len(body_hash))` 这样一个**看起来像字符区间**的三元组——它不是字符
    区间，那个长度只是 digest 长度（§四：不得靠模糊 tuple 猜类型）。`loc-1` 的 `whole_payload`
    变体把「只指向整份载体、不声称任何字符位置」写成**显式**语义。
    """
    container = NS.external_authority_container_id(external)
    body_hash = str(getattr(external, "body_hash", "") or "")
    if not container or len(body_hash) < 1:
        raise PackWriterError(
            f"ExternalFact {getattr(external, 'external_fact_id', '?')!r} 的 snapshot 载体不完整"
            "（缺 body_hash）：不得成为路径 A 支撑边")
    return NS.whole_payload_locator(container)


def _snapshot_payload_ref(external: Any) -> dict:
    """external 路径 A 的 snapshot 载体：五个字段**逐字**来自该事实自己（门会逐字回查）。

    snapshot 只是**来源载体**：URL / body hash / SourcePolicy / 日期都由外部事实的资格决定与
    快照绑定承担，写入侧不得自报，也不得把它的正文当材料。缺一即 fail-closed。
    """
    payload = {
        "snapshot_id": str(getattr(external, "source_snapshot_id", "") or ""),
        "canonical_url": str(getattr(external, "canonical_url", "") or ""),
        "body_hash": str(getattr(external, "body_hash", "") or ""),
        "source_policy_version": str(getattr(external, "source_policy_version", "") or ""),
        "as_of_date": str(getattr(external, "as_of_date", "") or ""),
    }
    missing = sorted(key for key, value in payload.items() if not value)
    if missing:
        raise PackWriterError(
            f"ExternalFact {getattr(external, 'external_fact_id', '?')!r} 的 snapshot 载体缺"
            f" {missing}：snapshot 只是来源载体，载体不完整不得成为正文支撑")
    return payload


def _topic_requirement_ids(pack_set: Any) -> dict[str, str]:
    """topic → `TopicResearchRequirement` id（**唯一**口径在 `harness.topic_runtime`）。

    延迟导入：`harness.topic_runtime` 不 import `sections.*`，但派生 requirement id 的口径必须
    只有一个，因此这里直接调用它，绝不另写一套 `f"{section}::{topic}"`。
    """
    from harness import topic_runtime as TR

    out: dict[str, str] = {}
    for requirement in tuple(getattr(pack_set, "requirements", ()) or ()):
        topic_id = str(getattr(requirement, "topic_id", "") or "")
        if topic_id and topic_id not in out:
            out[topic_id] = TR.topic_requirement_id(requirement)
    return out


# ---------------------------------------------------------------------------
# §六 期间语义：期间要求由**冻结 Contract 投影的 aspect 类型/time_scope + 事实类型**确定
# ---------------------------------------------------------------------------

#: 期间要求口径的**唯一实现**在 `harness.topic_schema`（研究侧事实构造与写作侧门共用同一份，
#: 避免"同一事实在两侧得到不同期间要求"）。这里的名字保留为同一实现的**再导出**，不重新定义。
_PERIOD_BEARING_ASPECT_KINDS = TS.PERIOD_BEARING_ASPECT_KINDS
PERIOD_REQUIREMENT_EXPLICIT = TS.PERIOD_REQUIREMENT_EXPLICIT
PERIOD_REQUIREMENT_OPTIONAL = TS.PERIOD_REQUIREMENT_OPTIONAL
PERIOD_UNRESOLVED_REASON = TS.PERIOD_UNRESOLVED_REASON
#: 事实**没有**可核验显式期间而被排除时的两个原因码（与 `PERIOD_UNRESOLVED_REASON` 合起来
#: 构成期间门的原因码封闭词表）。它们原先只以字面量出现在排除点，因此「排除原因」这个封闭
#: 词表在代码里并不存在——本批把它显式命名，供审计与 `TopicWriterFace` 共用同一份取值。
PERIOD_EXPLICIT_REQUIRED_REASON = "explicit_period_required"
PERIOD_VAGUE_TEXT_REASON = "vague_period_in_fact_text"

#: 排除面允许出现的**全部**原因码（封闭词表）：aspect 不在本节 + 期间门三个原因码。
#: 取值直接引用上面这几个常量，不另写一份字面量——两份字面量迟早会漂移，而「排除原因漂移」
#: 的表现是审计里出现一个下游谁也不认识的码。
WRITER_FACE_EXCLUSION_REASONS = (
    WRITER_FACE_EXCLUSION_ASPECT,
    PERIOD_UNRESOLVED_REASON,
    PERIOD_EXPLICIT_REQUIRED_REASON,
    PERIOD_VAGUE_TEXT_REASON)


def _fact_explicit_period(fact: Any) -> str:
    """事实**自己**的显式期间（`fact.period`，其次它自己的 `ValueIdentity.period`）。

    委托给 `TS.fact_explicit_period`（唯一实现）：绝不用 `section.report_as_of` 顶替，
    也不用任何正文措辞推断。
    """
    return TS.fact_explicit_period(fact)


def _period_requirement(fact: Any, aspect_kinds: tuple[str, ...],
                        time_scopes: tuple[str, ...]) -> str:
    """事实类型 → 期间要求（三态，全部来自冻结的 typed metadata）。

    委托给 `TS.period_requirement`（唯一实现）：判据顺序为「推断类 / 带类型化数字身份 /
    aspect 类型属指标或事件类 → 必须显式；`time_scope` 全为结构性 → 可选；其余 fail-closed」。
    """
    return TS.period_requirement(
        fact_type=str(getattr(fact, "fact_type", "") or ""),
        has_value_identity=getattr(fact, "value_identity", None) is not None,
        aspect_kinds=tuple(aspect_kinds), time_scopes=tuple(time_scopes))


def scan_topic_pack(authority: TopicPackAuthorityInput,
                    task: PS.SectionTask | None = None) -> AuthorityScan:
    """确定性读一遍 `VerifiedPackSet`：每条可用权威事实 → `AuthorityFactEntry`，aspect 状态原样保留。

    §6.2.3 / §6.4 第 1 项：本节事实目录同时包含**由 current Pack 持久化引用的 formal
    `ExternalFact`**（source_snapshot 容器 + external_fact_id），两者分属不同容器身份，绝不互相
    冒充。这里不构造任何 `SectionClaim` / `ClaimSupportRef`：定稿对象属门后（P8/P9/P10）。

    `task` 是**刻意保留的调用点参数**：本扫描的唯一依据是 `authority` 自己（Pack 内容 +
    冻结 aspect 元数据），不读 `task` 的任何字段。因此 `TopicWriterFace`（被提供面）可以在
    `TopicPackAuthorityInput.__post_init__` 里用 `task=None` 派生，与写作载荷走**同一份**扫描。
    """
    pack_set = authority.pack_set
    meta = _pack_aspect_meta(pack_set)
    required_ids = NS.required_fact_ids(authority)
    facts: list[AuthorityFactEntry] = []
    excluded: list[tuple[str, str, str]] = []
    period_unresolved: list[tuple[str, str, str]] = []
    period_exclusions: list[dict] = []
    status: dict[str, str] = {}
    counts: dict[str, int] = {}
    conflicts: list[dict] = []
    not_found: list[dict] = []
    gaps: list[dict] = []
    for pack in sorted(pack_set.packs, key=lambda p: str(p.pack_id)):
        container = str(pack.pack_id)
        topic_id = str(pack.topic_id)
        # §6.4.1 第 1 层：本 Pack 的 materials 与 Pack 侧去向必须逐条配对（材料清单不是事后补的）。
        materials = _pack_material_index(pack)
        for result in tuple(getattr(pack, "aspect_results", ()) or ()):
            aspect_id = str(result.aspect_id)
            state = str(getattr(result, "status", "") or "")
            if state not in _ASPECT_STATUSES:
                raise PackWriterError(
                    f"aspect {aspect_id!r} 的状态 {state!r} 不在 aspect 状态枚举内（状态不得改写）")
            status[aspect_id] = state
            counts[state] = counts.get(state, 0) + 1
        for fact in sorted(tuple(getattr(pack, "facts", ()) or ()), key=lambda f: str(f.fact_id)):
            fact_id = str(fact.fact_id)
            aspect_ids = tuple(str(a) for a in (getattr(fact, "aspect_ids", ()) or ()))
            missing = sorted(set(aspect_ids) - set(status))
            if missing or not (set(aspect_ids) & set(status)):
                excluded.append((container, fact_id, topic_id))
                continue
            # §十二 1/2 + §六：正文事实的期间只能来自**权威 fact 自己**的显式期间，绝不用本节
            # 基准日（`report_as_of`）整体填充——那是把「这是本节截止日」偷换成「这条事实
            # 发生在这一天」。但「没有显式期间」不等于「这条事实不成立」：判据是**事实类型**
            # （frozen Contract 的 aspect kind / time_scope + 类型化事实元数据）——
            #   * 时段量/时点量（指标、事件、带 ValueIdentity 的事实、推断结论）必须有显式期间，
            #     否则记 `period_unresolved` 缺口；
            #   * 结构性/描述性事实（如「公司主营业务为…」）本就不是时点量，只要**正文自己**不
            #     使用「报告期内」这类含糊期间措辞，就不因 `fact.period` 为空而被自动排除；
            #   * 事实类型无法判定 → fail-closed，同样记 `period_unresolved` 缺口。
            fact_period = _fact_explicit_period(fact)
            text = str(getattr(fact, "text", "") or "")
            if not fact_period:
                aspect_kinds = tuple(meta.get(a, {}).get("kind", "") for a in aspect_ids)
                time_scopes = tuple(meta.get(a, {}).get("time_scope", "") for a in aspect_ids)
                requirement = _period_requirement(fact, aspect_kinds, time_scopes)
                vague_hits = NS.vague_period_hits(text)
                if requirement != PERIOD_REQUIREMENT_OPTIONAL or vague_hits:
                    period_unresolved.append((container, fact_id, topic_id))
                    period_exclusions.append({
                        "container_id": container, "fact_id": fact_id, "topic_id": topic_id,
                        "reason": (PERIOD_UNRESOLVED_REASON if requirement
                                   == PERIOD_UNRESOLVED_REASON
                                   else PERIOD_EXPLICIT_REQUIRED_REASON
                                   if requirement == PERIOD_REQUIREMENT_EXPLICIT
                                   else PERIOD_VAGUE_TEXT_REASON),
                        "period_requirement": requirement,
                        "aspect_kinds": list(aspect_kinds),
                        "time_scopes": list(time_scopes),
                        "has_value_identity": getattr(fact, "value_identity", None) is not None,
                        "fact_type": str(getattr(fact, "fact_type", "") or ""),
                        "vague_period_hits": list(vague_hits)})
                    continue
            if not text:
                raise PackWriterError(f"fact {fact_id!r} 没有可写文本，不得成为正文权威事实")
            citation_refs = _citation_of(fact)
            # §四：引用锚点 → material 的绑定必须走**唯一**的类型化入口
            # （`NS.material_binding_for_citation` → `TS.citation_source_identity`）。绝不拿
            # `CitationRef.evidence_id`（裸 id）去比 `material.source_identity`（`evidence:<id>`）：
            # 两者不同域，比不出结果只会静默丢掉 material 绑定与 payload 锚点。
            material_id, payload_ref = _material_binding_of(
                authority, pack, container, citation_refs[0], fact)
            facts.append(_topic_fact_entry(
                fact, container=container, topic_id=topic_id, fact_id=fact_id, text=text,
                aspect_ids=aspect_ids, fact_period=fact_period, materials=materials,
                material_id=material_id, payload_ref=payload_ref,
                cited=citation_refs[0], required=fact_id in required_ids))
        for external in sorted(tuple(getattr(pack, "external_facts", ()) or ()),
                               key=lambda e: str(getattr(e, "external_fact_id", "") or "")):
            fact_id = str(external.external_fact_id)
            aspect_ids = tuple(str(a) for a in (getattr(external, "aspect_ids", ()) or ()))
            missing = sorted(set(aspect_ids) - set(status))
            if missing or not (set(aspect_ids) & set(status)):
                excluded.append((container, fact_id, topic_id))
                continue
            # 外部事实没有 `period`：它的显式时点是**自己的** `as_of_date`（SourcePolicy 日期），
            # 同样不得用基准期顶替。
            external_period = str(getattr(external, "as_of_date", "") or "")
            if not external_period:
                period_unresolved.append((container, fact_id, topic_id))
                period_exclusions.append({
                    "container_id": container, "fact_id": fact_id, "topic_id": topic_id,
                    "reason": PERIOD_EXPLICIT_REQUIRED_REASON, "period_requirement":
                    PERIOD_REQUIREMENT_EXPLICIT, "aspect_kinds": [], "time_scopes": [],
                    "has_value_identity": False, "fact_type": "fact", "vague_period_hits": []})
                continue
            facts.append(AuthorityFactEntry(
                authority_kind="external_snapshot",
                container_identity=NS.external_authority_container_id(external),
                fact_id=fact_id, text=str(getattr(external, "statement", "") or ""),
                topic_id=topic_id, aspect_ids=aspect_ids,
                required=fact_id in required_ids, fact_type="fact",
                period=external_period, scope="", material_id=None,
                payload_ref=_snapshot_payload_ref(external),
                locator_ref=_external_locator_ref(external),
                # snapshot 容器是来源载体身份；provenance 是该事实自己的资格决定（§16.3 #6）。
                source_identity=NS.external_authority_container_id(external),
                provenance_identity=str(getattr(external, "qualification_decision_id", "") or ""),
                content_fingerprint=str(getattr(external, "content_hash", "") or "")))
        for conflict in tuple(getattr(pack, "conflicts", ()) or ()):
            conflicts.append(_jsonable(conflict))
        for audit in tuple(getattr(pack, "not_found_audits", ()) or ()):
            not_found.append(_jsonable(audit))
        for gap in tuple(getattr(pack, "gaps", ()) or ()):
            gaps.append(_jsonable(gap))
    for entry in facts:
        for name in ("source_identity", "provenance_identity", "content_fingerprint"):
            if not getattr(entry, name):
                raise PackWriterError(
                    f"权威事实 {entry.key} 的 {name} 无法从权威派生（不得手写、不得留空）："
                    f"source/provenance/content 必须是权威自己的类型化身份")
    return AuthorityScan(
        facts=tuple(facts), aspect_status=status,
        aspect_topic={a: v["topic_id"] for a, v in meta.items()},
        aspect_impact={a: v["impact_scope"] for a, v in meta.items()},
        aspect_blocking={a: v["blocking_policy"] for a, v in meta.items()},
        aspect_question={a: v["question_id"] for a, v in meta.items()},
        aspect_requirement={a: v["requirement_text"] for a, v in meta.items()},
        excluded_facts=tuple(excluded), conflicts=tuple(conflicts),
        not_found=tuple(not_found), gaps=tuple(gaps), coverage_counts=counts,
        requirement_ids=_topic_requirement_ids(pack_set),
        period_unresolved=tuple(period_unresolved),
        period_exclusions=tuple(period_exclusions))


def _topic_fact_entry(fact: Any, *, container: str, topic_id: str, fact_id: str, text: str,
                      aspect_ids: tuple[str, ...], fact_period: str,
                      materials: Mapping[str, tuple[Any, Any]], material_id: str | None,
                      payload_ref: dict | None, cited: Any, required: bool) -> AuthorityFactEntry:
    """topic_material 事实 → 权威事实条目。

    身份来源分两种情况，**都不许来自模型**：
      * 有 material 载体 → source/provenance/content 直接取该 material 与其 **matching
        `ResearchMaterialDisposition`** 自己的类型化字段（材料血缘可回查）；
      * 无 material 载体 → source 取 canonical 引用来源身份，provenance 取该事实自己的
        `qualification_decision_id`，content 取权威事实面文本的 sha256。
    """
    if material_id is not None:
        material, rmd = materials.get(material_id, (None, None))
        if material is None:
            raise PackWriterError(
                f"fact {fact_id!r} 绑定到 pack {container!r} 内不存在的 material "
                f"{material_id!r}（material 必须确实存在）")
        source_identity = str(getattr(material, "source_identity", "") or "")
        provenance_identity = str(getattr(rmd, "provenance_identity", "") or "")
        content_fingerprint = str(getattr(material, "content_hash", "") or "")
    else:
        source_identity = NS.citation_source_identity(cited)
        provenance_identity = str(getattr(fact, "qualification_decision_id", "") or "")
        content_fingerprint = _fact_surface_fingerprint(
            "topic_pack", container, fact_id, NS.authority_numeric_texts("topic_pack", fact))
    return AuthorityFactEntry(
        authority_kind="topic_pack", container_identity=container, fact_id=fact_id, text=text,
        topic_id=topic_id, aspect_ids=aspect_ids, required=required,
        fact_type=str(getattr(fact, "fact_type", "") or ""), period=fact_period,
        scope=str(getattr(fact, "scope", "") or ""), material_id=material_id,
        payload_ref=payload_ref, locator_ref=None, source_identity=source_identity,
        provenance_identity=provenance_identity, content_fingerprint=content_fingerprint,
        value_identity=_value_identity_dict(fact))


def _financial_fact_text(fact: Any) -> str:
    """财务 fact → 断言文本（只用权威字段，不做任何计算/换算/补格）。

    §六：表面拼装的唯一实现已收归 `NS.authoritative_fact_surface` —— 硬门要用**同一套**
    口径复核「Claim 是否确实属于该权威 fact」，两套拼装会让这条核验失去意义。
    """
    text = NS.authoritative_fact_surface("financial_pack", fact)
    if not text:
        raise PackWriterError(
            f"财务 fact {getattr(fact, 'fact_id', '?')!r} 没有 display/value_text")
    return text


def _note_fact_text(fact: Any) -> str:
    """附注 fact → 断言文本（唯一实现同上）。

    附注事实没有 `period` 字段（期间由 span locator 指向的原文承担），所以不编造期间；
    没有任何数值的附注事实只陈述其科目名，绝不补一个数字上去。
    """
    text = NS.authoritative_fact_surface("evidence_note", fact)
    if not text:
        raise PackWriterError(
            f"附注 fact {getattr(fact, 'fact_id', '?')!r} 既无数值也无科目名，没有可写文本")
    return text


def _citation_from_mapping(payload: Any) -> Any:
    """财务 citation 映射 → `CitationRef`。**唯一**实现在 `NS.citation_from_mapping`
    （门核验引用归属时必须与写入侧用同一套派生），这里只把错误类型转成写入侧语义。"""
    try:
        return NS.citation_from_mapping(payload)
    except NS.NarrativeSchemaError as exc:
        raise PackWriterError(str(exc)) from exc


def scan_financial(authority: FinancialAuthorityInput, task: PS.SectionTask) -> AuthorityScan:
    """财务权威的读视图。

    财务 workflow **没有** aspect 级状态（那是 topic runtime 的概念），因此这里不编造
    aspect 状态；本节覆盖情况由「选中事实 + artifact 缺口 + 附注缺口」如实表达。
    """
    artifact = authority.artifact
    container = str(artifact.artifact_id)
    required_ids = NS.required_fact_ids(authority)
    facts: list[AuthorityFactEntry] = []
    excluded: list[tuple[str, str, str]] = []
    for fact in sorted(tuple(getattr(artifact, "facts", ()) or ()),
                       key=lambda f: str(f.fact_id)):
        fact_id = str(fact.fact_id)
        topic_id = authority.topic_for_fact(fact_id)
        if topic_id == OUTSIDE_SECTION_TOPIC:
            # 组合根显式声明该 fact 不属于本节（另一财务章节的指标，或只是指标输入的报表项）：
            # 只留下「本节不呈现」的去向记录，不写正文、不塞进本节主题。
            excluded.append((container, fact_id, topic_id))
            continue
        text = _financial_fact_text(fact)
        citation = _citation_from_mapping(getattr(fact, "citation", None))
        facts.append(AuthorityFactEntry(
            authority_kind="financial_pack", container_identity=container, fact_id=fact_id,
            text=text, topic_id=topic_id, aspect_ids=(), required=fact_id in required_ids,
            # 财务事实的时点是 artifact 自己的报告期字段（不是本节基准日的替身）。
            fact_type="fact", period=str(getattr(fact, "period", "") or ""),
            scope=str(getattr(fact, "scope", "") or ""), material_id=None,
            payload_ref=None, locator_ref=None,
            source_identity=NS.citation_source_identity(citation),
            provenance_identity=f"financial_pack:{container}",
            content_fingerprint=_fact_surface_fingerprint(
                "financial_pack", container, fact_id,
                NS.authority_numeric_texts("financial_pack", fact))))
    note_set = authority.note_facts
    if note_set is not None:
        note_container = NS.note_container_id(note_set)
        for fact in sorted(tuple(getattr(note_set, "facts", ()) or ()),
                           key=lambda f: str(f.fact_id)):
            fact_id = str(fact.fact_id)
            topic_id = authority.topic_for_fact(fact_id)
            text = _note_fact_text(fact)
            citation = _note_citation(fact)
            facts.append(AuthorityFactEntry(
                authority_kind="evidence_note", container_identity=note_container,
                fact_id=fact_id, text=text, topic_id=topic_id, aspect_ids=(),
                required=fact_id in required_ids, fact_type="fact",
                # 附注期间由 locator 指向的原文承担，不得编造。
                period="", scope="", material_id=None, payload_ref=None,
                locator_ref=NS.authoritative_locator("evidence_note", fact),
                source_identity=NS.citation_source_identity(citation),
                provenance_identity=f"evidence_note:{note_container}",
                content_fingerprint=_fact_surface_fingerprint(
                    "evidence_note", note_container, fact_id,
                    NS.authority_numeric_texts("evidence_note", fact))))
    # artifact 缺口按**同一个归属坐标系**分流：本节主题的缺口才是本节的缺口；属于其他章节的
    # 缺口只登记备查（既不冒充本节缺口，也不静默消失）。
    gap_topic_by_fact = {str(g.get("fact_id") or ""): authority.topic_for_fact(
        str(g.get("fact_id") or "")) for g in (getattr(artifact, "gaps", ()) or ())}
    in_scope_gaps: list[dict] = []
    out_of_scope_gaps: list[dict] = []
    for gap in (getattr(artifact, "gaps", ()) or ()):
        entry = _jsonable(gap)
        target = (out_of_scope_gaps if gap_topic_by_fact.get(
            str(gap.get("fact_id") or "")) == OUTSIDE_SECTION_TOPIC else in_scope_gaps)
        target.append(entry)
    covered_topics = {entry.topic_id for entry in facts}
    return AuthorityScan(
        facts=tuple(facts), aspect_status={}, aspect_topic={}, aspect_impact={},
        aspect_blocking={}, aspect_question={}, excluded_facts=tuple(excluded), conflicts=(),
        not_found=(), gaps=tuple(in_scope_gaps), coverage_counts={},
        # 财务/附注权威**不携带** Contract requirement 对象，因此不派生 requirement id：
        # 需要 `target_requirement_id` 的 `FollowUpNeed` 在这里没有可回指的真实需求，
        # 只能被如实拒绝（不得凭 section::topic 字符串**猜**一个需求 id 出来）。
        requirement_ids={},
        topics_without_facts=tuple(t for t in authority.topic_ids if t not in covered_topics),
        out_of_scope_gaps=tuple(out_of_scope_gaps))


def _financial_gap_unresolved(section_id: str, gap: Mapping[str, Any],
                              authority: FinancialAuthorityInput) -> Any:
    """artifact 缺口 → SectionUnresolved（状态/原因码逐字取权威，不改写）。"""
    reason = str(gap.get("reason_code") or "unresolved")
    status = str(gap.get("status") or "blocked")
    topic_id = authority.topic_for_fact(str(gap.get("fact_id") or ""))
    # 读者面：只写「哪个指标、为什么不可得」，不写 `formula=` / `status=` / `reason=` 这类内部
    # 字段语法（它们的值原样保留在下面两个参数里，本轮不新增也不改口径）。`（pwr-4`）
    detail = _digits_free(
        f"财务权威缺口：指标「{gap.get('label') or gap.get('fact_id')}」在本轮不可得"
        f"（指标公式：{gap.get('formula_id')}；权威状态：{status}；原因码：{reason}）；"
        "本节据此只呈现缺口，不代替该指标下结论。", "财务缺口",
        str(gap.get("fact_id") or ""), str(gap.get("formula_id") or ""), status, reason)
    unresolved_id = derive_unresolved_id(section_id, topic_id, "", "blocked", reason, detail)
    return SS.SectionUnresolved(
        unresolved_id=unresolved_id, section_id=section_id, topic_id=topic_id,
        question_id="", state="NOT_PROVIDED", reason_code=reason, detail=detail,
        impact_scope=(), blocking_effects=(), attempted_sources=())


# ---------------------------------------------------------------------------
# 缺口 → SectionUnresolved（状态逐字保留，绝不改写成 covered）
# ---------------------------------------------------------------------------
#
# 读者面的统一口径（`pwr-4`）：**标识保留、`key=` 语法不留**。
#
# 权威侧的 id（aspect / topic / question / fact）是读者判读「这条缺口说的是什么事」的唯一线索：
# 人读缺口一行只呈现 `[权威原始状态/状态/原因码] + detail`（`narrative_schema._gap_line`），
# id 若从 detail 里消失，这条缺口就没有主语。但 `topic=…` / `question_id=…` /
# `reason_codes=['…']` 这类**字段语法**是内部 plumbing：读者读不懂，读法还会随字段改名漂移。
# 因此一律写成「主题「x」」「原因码：x」这样的自然措辞。
#
# 边界：类型化字段本身（`topic_id` / `question_id` / `reason_code` / `state` 与它们在
# `SectionUnresolved` 上的取值）**一个都不改**，也不美化任何状态（`blocked` 不得变成别的词）。
# 例外只有一处：附注缺口文案里的 `task=<内部任务 id>` 不保留——那个 id 对读者没有任何含义
# （缺口登记在哪个主题由记录自己的 `topic_id` 承担），保留它只是把内部 plumbing 印在正文里。


def derive_unresolved_id(section_id: str, topic_id: str, question_id: str, state: str,
                         reason_code: str, detail: str) -> str:
    return NS.content_id(UNRESOLVED_ID_PREFIX, {
        "section_id": section_id, "topic_id": topic_id, "question_id": question_id,
        "state": state, "reason_code": reason_code, "detail": detail})


def _reason_code_for(state: str, blocking: str) -> str:
    if state == "not_found":
        return "not_found"
    if state == "blocked":
        return "blocked"
    if state == "partial":
        return "unresolved"
    return "unresolved"


def _digits_free(text: str, what: str, *identifiers: str) -> str:
    """缺口叙述不得携带**未经绑定的数字**——但身份标识（fact_id / task_id / aspect_id…）原样保留。

    判定：叙述里每个连续数字串都必须出现在某个 `identifiers` 里。这样既挡住把数量、比率、
    金额偷偷写进缺口文案（那些数字没有任何权威绑定），又不会因为 id 自带数字而误拒真实 run
    （`f_300750_…`、`task-fin-1` 这类 id 本来就含数字）。
    """
    allowed = "".join(str(i) for i in identifiers)
    for run in re.findall(r"\d+", text):
        if run not in allowed:
            raise PackWriterError(
                f"{what} 的渲染文本含未经绑定的数字 {run!r}（只允许出现身份标识里的数字）："
                f"{text!r}")
    return text


def _impact_text(raw: Any) -> str:
    """影响范围 → 读者面文字：标识原样保留，只把**容器语法**（`('key_financial',)`）去掉。

    与 `_impact_scopes_of` 同一口径（同一个 `raw`，两种用法：一处进类型化字段，一处进文案），
    因此不新增第二套范围语义。空值写「未声明」——不写 `()`，也不假装范围为空。
    """
    values = (raw,) if isinstance(raw, str) else tuple(raw or ())
    scopes = [str(v) for v in values if str(v)]
    return "、".join(scopes) if scopes else "未声明"


def _aspect_unresolved(section_id: str, aspect_id: str, scan: AuthorityScan,
                       question_policy: Mapping[str, tuple[str, ...]]) -> Any:
    topic_id = scan.aspect_topic.get(aspect_id, "")
    raw_status = scan.aspect_status[aspect_id]
    state = _STATUS_TO_QUESTION_STATE.get(raw_status)
    if state is None:
        raise PackWriterError(f"aspect 状态 {raw_status!r} 无法映射成 canonical question 状态")
    impact = scan.aspect_impact.get(aspect_id)
    # 读者面：影响范围写可读并列（`key_financial`），不写 Python 元组 repr；范围标识本身一字不改。
    detail = _digits_free(
        f"aspect「{aspect_id}」在本轮检索中的状态为 {raw_status}（原样保留，未改写）。"
        f"该缺口影响范围：{_impact_text(impact)}。", "aspect 缺口",
        aspect_id, raw_status, *_impact_scopes_of(impact))
    reason_code = _reason_code_for(raw_status, scan.aspect_blocking.get(aspect_id, ""))
    question_id = scan.aspect_question.get(aspect_id, "")
    unresolved_id = derive_unresolved_id(
        section_id, topic_id, question_id, state, reason_code, detail)
    return SS.SectionUnresolved(
        unresolved_id=unresolved_id, section_id=section_id, topic_id=topic_id,
        question_id=question_id, state=state,
        reason_code=reason_code, detail=detail,
        impact_scope=_impact_scopes_of(scan.aspect_impact.get(aspect_id, "")),
        blocking_effects=_authority_blocking_effects(
            question_id, question_policy, scan.aspect_blocking.get(aspect_id, "")),
        attempted_sources=())


def _impact_scopes_of(raw: Any) -> tuple[str, ...]:
    values = (raw,) if isinstance(raw, str) else tuple(raw or ())
    return tuple(v for v in values if v in CS.IMPACT_SCOPES)


def _note_gap_unresolved(section_id: str, authority: FinancialAuthorityInput) -> Any:
    gap = authority.note_gap
    topic_id = authority.topic_ids[0]
    reason_codes = list(getattr(gap, "reason_codes", ()) or ())
    # 读者面：不再写 `task=<内部任务 id>` 与 `reason_codes=['a','b']`（前者是内部plumbing，
    # 后者是 Python 字面量语法）。原因码本身**原样保留**，只是改成读得懂的并列写法。
    detail = _digits_free(
        f"本节的财务附注未取得可用事实：附注缺口状态为 {NOTE_GAP_AUTHORITY_STATUS}"
        f"（原样保留，未改写；原因码：{'、'.join(str(c) for c in reason_codes) or '未登记'}）。"
        "附注缺口是任务级的，登记在本书首个主题下；相关结论以缺口形式呈现，"
        "不补写附注口径的分析。", "附注缺口",
        *(str(c) for c in reason_codes))
    unresolved_id = derive_unresolved_id(section_id, topic_id, "", "NOT_PROVIDED", "blocked", detail)
    return SS.SectionUnresolved(
        unresolved_id=unresolved_id, section_id=section_id, topic_id=topic_id, question_id="",
        state="NOT_PROVIDED", reason_code="blocked", detail=detail, impact_scope=(),
        blocking_effects=(), attempted_sources=())


def _out_of_scope_required_facts(authority: Any,
                                 scan: AuthorityScan) -> dict[str, tuple[str, ...]]:
    """本节之外、但按 `required_fact_ids` 仍属 required 的事实，按归属主题分组。

    判据完全取权威输入自己的类型化字段（`required_fact_ids`），不引入第二套语义；分组键是
    **该 fact 自己的**归属（可能是本节之外的主题或「本节之外」标记），绝不用本节主题顶替。
    """
    required = NS.required_fact_ids(authority)
    grouped: dict[str, list[str]] = {}
    for _container_id, fact_id, topic_id in scan.excluded_facts:
        if fact_id in required:
            grouped.setdefault(topic_id, []).append(fact_id)
    return {topic: tuple(sorted(fact_ids)) for topic, fact_ids in grouped.items()}


def _out_of_scope_facts_unresolved(section_id: str, topic_id: str,
                                   fact_ids: Sequence[str]) -> Any:
    """本节之外的**required 事实**不呈现时的显式缺口（一条缺口顶一组同归属事实）。

    这些 fact 是「被选中的权威事实」（它们就在本节的权威容器里），按 `required_fact_ids`
    还是带数值的 required；不呈现就必须有显式缺口兜住，否则门会判静默不呈现。缺口只陈述
    「本节不呈现哪些归属的事实」，不改写它们的归属，也不说它们不存在。
    """
    count = str(len(fact_ids))
    # 读者面：情况说明用自然措辞点名主题与条数（不写 `topic=` / `count=` 这类字段语法）；
    # 逐条 id 不进文案——它们由 `FactNarrativeDisposition` 逐条承担，这里只给可回查的指针。
    detail = _digits_free(
        f"本节的权威输入中另有 {count} 条带数值的事实，归属主题「{topic_id}」，不属于本节"
        "任何主题（其他财务章节的指标，或仅作为指标输入的原始报表项）：本节不呈现它们，也不据此"
        "下结论；它们的逐条去向见 FactNarrativeDisposition。", "本节之外事实缺口",
        section_id, topic_id, count, *fact_ids)
    unresolved_id = derive_unresolved_id(
        section_id, topic_id, "", "NOT_PROVIDED", "unresolved", detail)
    return SS.SectionUnresolved(
        unresolved_id=unresolved_id, section_id=section_id, topic_id=topic_id, question_id="",
        state="NOT_PROVIDED", reason_code="unresolved", detail=detail, impact_scope=(),
        blocking_effects=(), attempted_sources=())


def _topic_fact_gap_unresolved(section_id: str, topic_id: str) -> Any:
    """本节 topic 在权威输入里没有任何事实 → 显式缺口。

    这是对**权威输入本身**的如实陈述（该主题在本节输入里没有可用事实），不是对现实世界的
    断言，因此不得写成「不存在」，也不得用其他主题的事实顶替。
    """
    detail = _digits_free(
        f"本节主题「{topic_id}」的权威输入中没有任何可引用事实（{NO_FACT_AUTHORITY_STATUS}）："
        "该主题本轮只有缺口，没有正文；本条只陈述本节权威"
        "输入的不足，不断言该事项在现实中是否有发生；受影响结论在此显式标注。",
        "主题无事实缺口", section_id, topic_id, NO_FACT_AUTHORITY_STATUS)
    unresolved_id = derive_unresolved_id(
        section_id, topic_id, "", "NOT_PROVIDED", "unresolved", detail)
    return SS.SectionUnresolved(
        unresolved_id=unresolved_id, section_id=section_id, topic_id=topic_id, question_id="",
        state="NOT_PROVIDED", reason_code="unresolved", detail=detail, impact_scope=(),
        blocking_effects=(), attempted_sources=())


def _question_gap_unresolved(section_id: str, question: Any, topic_id: str) -> Any:
    """权威问题在本轮既无 Claim 覆盖、也无已登记缺口 → 显式缺口（不得静默消失）。

    §七 P0-4 1/2：期望集合必须从权威 task **精确派生** —— 每个声明的问题要么有 Claim 覆盖，
    要么有显式缺口。本条只陈述**本轮权威输入**的不足：问题没有绑定事实、权威侧也没有登记与它
    绑定的缺口；本轮没有可核验的检索范围记录，因此不得改写成「不存在 / 未披露 / 没有」。

    后果字段取自**权威问题自己声明的 `blocking_policy`**（规范化后），因此问题声明阻断时章节
    状态如实落成 SECTION_BLOCKED，不会被静默降级。
    """
    question_id = str(question.question_id)
    # 读者面：问题与主题都用自然措辞点名（不写 `question_id=` / `topic=` 这类字段语法），
    # 否则这一行缺口没有主语——人读缺口只呈现 `[状态/原因码] + detail`。
    detail = _digits_free(
        f"本节问题「{question_id}」（所属主题「{topic_id}」）在本轮权威输入中没有产出任何"
        "可写进正文的 Claim，权威侧也没有登记与该问题绑定的缺口记录；本轮没有可核验的检索范围"
        "记录，因此本节只如实保留这条缺口，不据此对该事项下任何肯定或否定的结论。",
        "问题无覆盖缺口", section_id, question_id)
    unresolved_id = derive_unresolved_id(
        section_id, topic_id, question_id, "NOT_PROVIDED", "unresolved", detail)
    return SS.SectionUnresolved(
        unresolved_id=unresolved_id, section_id=section_id, topic_id=topic_id,
        question_id=question_id, state="NOT_PROVIDED", reason_code="unresolved", detail=detail,
        impact_scope=_impact_scopes_of(getattr(question, "impact_scope", ())),
        blocking_effects=SS.normalize_blocking_effects(
            getattr(question, "blocking_policy", ())),
        attempted_sources=())


def _period_unresolved_gap(section_id: str, topic_id: str, fact_ids: Sequence[str]) -> Any:
    """§十二 1/3：权威 fact 自己没有任何可核验期间 → 该 fact 不进入正文，只留显式缺口。

    缺口只陈述**本节权威输入的不足**（这些事实的期间在权威记录里缺失、无法证明），
    既不得用本节基准日或「报告期」措辞顶替，也不得断言该事项在现实中未发生。
    """
    count = str(len(fact_ids))
    # 读者面：主题与条数点名（不写 `topic=` / `count=` 这类字段语法）；原因码保留为可读措辞，
    # 类型化字段 `reason_code` 的取值一字不改。
    detail = _digits_free(
        f"本节主题「{topic_id}」的权威输入中有 {count} 条事实**没有可核验的显式期间**"
        "（原因码：period_unresolved）：期间无法证明的事实一律不进入正文，也不得用本节基准日或"
        "「报告期」这类未定义指代替代；该事实的存在与来源予以保留，本节不据此下结论。",
        "期间未解决缺口", section_id, topic_id, count, *fact_ids)
    unresolved_id = derive_unresolved_id(
        section_id, topic_id, "", "NOT_PROVIDED", "period_unresolved", detail)
    return SS.SectionUnresolved(
        unresolved_id=unresolved_id, section_id=section_id, topic_id=topic_id, question_id="",
        state="NOT_PROVIDED", reason_code="period_unresolved", detail=detail, impact_scope=(),
        blocking_effects=(), attempted_sources=())


# ---------------------------------------------------------------------------
# LLM 候选提案：严格解析（任何工具/检索意图都不可表达）
#
# 门前**只有一个** LLM 出口：它只**选择**权威目录里的行并给出候选文本，不产生任何身份
# （§16.7.1 P6/P18）。因此本节的解析器只做两件事——封闭词汇表校验，以及把「模型看到的那
# 一行」回查权威目录（`_resolve_fact_declaration` / `_resolve_material_declaration`）。
# 任何定稿对象（SectionClaim / citation / accepted binding / 最终 Narrative / SectionResult）
# 在提案词汇表里**不可表达**。
# ---------------------------------------------------------------------------

def _strict_keys(payload: Mapping[str, Any], allowed: Sequence[str], what: str) -> None:
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise PackWriterError(
            f"{what} 含未登记字段 {unknown}（只允许 {list(allowed)}；工具/检索意图不可表达）")


@dataclass(frozen=True)
class SupportOption:
    """一个**预先声明**的支撑选项：短别名 + 它指向的那一行的完整身份。

    `fact_id` 与 `material_id` **恰好一个非空**：这正是一条支撑边在权威侧的唯一分岔
    （引用事实行 ⇒ 路径 A；引用材料行 ⇒ 路径 B）。路径 A 的 material 锚点**不在这里**：
    它由写入侧从该权威事实**自己的引用**确定性派生，别名表不提供、模型也不得选择。
    """

    ref: str
    authority_kind: str
    container_identity: str
    fact_id: str | None = None
    material_id: str | None = None

    def __post_init__(self) -> None:
        if not self.ref:
            raise PackWriterError("支撑选项的 ref 不得为空")
        if not self.authority_kind or not self.container_identity:
            raise PackWriterError(f"支撑选项 {self.ref!r} 缺 authority_kind / container_id")
        if (self.fact_id is None) == (self.material_id is None):
            raise PackWriterError(
                f"支撑选项 {self.ref!r} 必须恰有一个身份字段（fact_id 或 material_id）")

    @property
    def is_fact(self) -> bool:
        return self.fact_id is not None


@dataclass(frozen=True)
class SupportAliasTable:
    """本次请求**声明的**支撑选项别名表：每一行选项 ↔ 一个短别名（`saref-1`）。

    三条不变量，缺任何一条，「短别名」就会退化成「模型自己编身份的捷径」：

      * **预先声明**：别名在请求里逐行给出（每行一个 `ref`），模型只做**选择**，不构造身份；
      * **完备**：声明的选项与别名**一一对应**——每个 ref 恰好一行、每行恰好一个 ref，
        编号连续无缺口（`f1..fN` / `m1..mM`）、无重复。少了这一层，一个未被声明的 ref
        就无从展开，只能靠猜；
      * **不可变**：ref→行 的映射只由本节**有序**选项表的位置决定（事实表 = `scan.facts`
        顺序，材料表 = `manifest.entries` 顺序），同一修订内跨批恒定——每一批请求带的都是
        同一张表，因此「第 2 批的 `m7`」与「第 4 批的 `m7`」必是同一份材料。

    `expand_*` 是**唯一**的展开点：它把短别名还原成那一行的**完整**身份字段，再交给与长格式
    **同一个**校验器。因此别名形式**不是**第二条合法性口径——`authority_kind` /
    `container_id` / `fact_id` / `material_id` 一律来自被引用的那一行，**不**由
    `authorization_path`（或任何路径名）推断；`support_semantics` 与 `authorization_path`
    是由「引用的是哪一种行」决定的**常量**，同样不来自路径名。
    """

    facts: tuple[SupportOption, ...] = ()
    materials: tuple[SupportOption, ...] = ()
    policy_version: str = SUPPORT_ALIAS_POLICY_VERSION

    def __post_init__(self) -> None:
        if self.policy_version != SUPPORT_ALIAS_POLICY_VERSION:
            raise PackWriterError(
                f"SupportAliasTable.policy_version 必须为 {SUPPORT_ALIAS_POLICY_VERSION!r}")
        self._assert_complete(self.facts, FACT_REF_PREFIX, "事实")
        self._assert_complete(self.materials, MATERIAL_REF_PREFIX, "材料")
        for option in self.materials:
            if option.authority_kind != "topic_pack":
                raise PackWriterError(
                    f"材料选项 {option.ref!r} 的 authority_kind={option.authority_kind!r}："
                    "exact ResearchMaterial 只存在于 Pack（manifest 成员恒为 topic_pack）")

    @staticmethod
    def _assert_complete(options: Sequence[SupportOption], prefix: str, what: str) -> None:
        refs = [o.ref for o in options]
        expected = [f"{prefix}{i + 1}" for i in range(len(refs))]
        if refs != expected:
            raise PackWriterError(
                f"{what}选项别名必须与声明行**一一对应**且编号连续：期望 {expected}，"
                f"实得 {refs}（完备性是别名可展开的前提，不是编号偏好）")

    def fact_refs(self) -> tuple[str, ...]:
        return tuple(o.ref for o in self.facts)

    def material_refs(self) -> tuple[str, ...]:
        return tuple(o.ref for o in self.materials)

    def refs(self) -> tuple[str, ...]:
        return self.fact_refs() + self.material_refs()

    def option(self, ref: str) -> SupportOption:
        """`ref` → 声明的那一行。未声明（或指向另一张表）即 fail-closed，不猜。"""
        key = str(ref or "")
        if key.startswith(FACT_REF_PREFIX):
            for option in self.facts:
                if option.ref == key:
                    return option
        if key.startswith(MATERIAL_REF_PREFIX):
            for option in self.materials:
                if option.ref == key:
                    return option
        raise PackWriterError(
            f"支撑边的 ref={key!r} 不在本次请求声明的选项里"
            f"（已声明 {len(self.facts)} 个事实选项、{len(self.materials)} 个材料选项；"
            f"策略 {self.policy_version}）。别名只能指向**预先声明**的那一行，"
            "指不到即整束被拒——不得凭别名猜一个身份出来")

    def expand_factual(self, ref: str, *, support_role: str) -> dict:
        """factual 边：`ref`（+ 模型判断的 role）→ 与长格式**逐字段同形**的一条边。

        `support_semantics` / `authorization_path` 由**引用的是哪一种行**决定：
        事实行 ⇒ `path_a_prevalidated`；材料行 ⇒ `path_b_material_derived`
        （路径 B 只在 topic_pack 上可表达，材料行的 kind 恒为 topic_pack，故这一点由
        `__post_init__` 与既有校验器共同保证，不在这里另写一份判据）。
        """
        option = self.option(ref)
        return {
            "authority_kind": option.authority_kind,
            "container_id": option.container_identity,
            "fact_id": option.fact_id,
            "material_id": option.material_id,
            "support_role": str(support_role or ""),
            "support_semantics": "factual",
            "authorization_path": ("path_a_prevalidated" if option.is_fact
                                   else "path_b_material_derived"),
        }

    def expand_context(self, ref: str) -> dict:
        """context 边：`ref` → 完整边。事实行**不是** context 载体，指到它即拒。

        展开结果里**没有** `fact_id`：context 边的线格式里不存在这个字段
        （`_CONTEXT_SUPPORT_KEYS` 的固定词表），带上了会被同一条校验器当成未登记字段拒掉。
        「context 不授权事实」在别名形式上就落成「这个键根本不出现」。
        """
        option = self.option(ref)
        if option.is_fact:
            raise PackWriterError(
                f"context 边不得引用事实行（ref={option.ref!r}）：context 只验证背景与衔接，"
                "不授权事实，必须绑定一份真实 material")
        return {"authority_kind": option.authority_kind,
                "container_id": option.container_identity,
                "material_id": option.material_id,
                "support_role": "corroborating", "support_semantics": "context",
                "authorization_path": "context_only"}


def _support_aliases(scan: AuthorityScan, manifest: NS.WriterMaterialManifest
                     ) -> SupportAliasTable:
    """本节**有序**选项表 → 别名表（事实表 = `scan.facts` 顺序，材料表 = `manifest.entries`）。

    只有一处派生：请求侧的 `ref` 字段（`_declare_option_refs`）与解析侧的展开读的是同一张表，
    因此「请求里声明的第 n 行」与「展开出来的第 n 行」不可能是两行。
    """
    facts = tuple(SupportOption(ref=f"{FACT_REF_PREFIX}{i + 1}",
                                authority_kind=str(entry.authority_kind),
                                container_identity=str(entry.container_identity),
                                fact_id=str(entry.fact_id))
                  for i, entry in enumerate(scan.facts))
    materials = tuple(SupportOption(ref=f"{MATERIAL_REF_PREFIX}{i + 1}",
                                    authority_kind=str(entry.authority_kind),
                                    container_identity=str(entry.pack_id),
                                    material_id=str(entry.material_id))
                      for i, entry in enumerate(manifest.entries))
    return SupportAliasTable(facts=facts, materials=materials)


def _declare_option_refs(rows: Sequence[Mapping[str, Any]],
                         options: Sequence[SupportOption], what: str) -> list[dict]:
    """把别名**逐行**贴到请求的可选行上（`ref` 是行的一部分，不是另发一张表）。

    行数与选项数不等即拒：请求里少声明一行、或多贴一个别名，都会让「模型看到的选项集」
    与「写入侧能展开的选项集」不再是同一张表——那时别名形式的边要么指不到，要么指到别的行。
    """
    if len(rows) != len(options):
        raise PackWriterError(
            f"{what}行数与声明的别名数不一致（{len(rows)} vs {len(options)}）："
            "请求侧与展开侧必须是同一张表")
    return [{"ref": options[i].ref, **dict(row)} for i, row in enumerate(rows)]


def _expand_alias_edge(edge: Any, *, aliases: SupportAliasTable | None, what: str,
                       chain: str) -> Any:
    """短别名形式的支撑边 → 长格式同形边（长格式原样返回，交给**同一个**校验器）。

    `chain` 取 `"factual"` 或 `"context"`：两条链允许的别名形式字段不同
    （factual 要 `support_role`，context 一个字段都不要）。别名形式**不得**混入任何身份字段
    ——混了即拒，因为「模型自报的身份」与「系统展开的身份」必须始终可区分。
    """
    if not isinstance(edge, Mapping) or "ref" not in edge:
        return edge
    if aliases is None:
        raise PackWriterError(
            f"{what} 用的是短别名形式（`ref`），但本批没有声明别名表："
            "没有声明就没有可展开的一行，不得凭 ref 猜身份")
    allowed = _ALIAS_FACTUAL_KEYS if chain == "factual" else _ALIAS_CONTEXT_KEYS
    _strict_keys(edge, allowed, f"{what}（短别名形式）")
    if chain == "factual":
        return aliases.expand_factual(str(edge.get("ref") or ""),
                                      support_role=str(edge.get("support_role") or ""))
    return aliases.expand_context(str(edge.get("ref") or ""))


#: JSON 字符串字面量**内部**允许出现的裸控制字符（`<0x20`）——**已证明需要处理**的那一类。
#: 现场只有一个证据：r7b 批 2/4 的返回里一处字符串内裸换行（`…不适用。\n客户集中度方面…`，
#: `Invalid control character at: line 255 column 196 (char 6820)`）让整批返回作废，而它的语义
#: **毫无歧义**——字符串里的字面换行就是换行符本身。回车/制表符与它同类（同一台模型、同一类
#: 序列化缺陷），因此一并按已证明口径容忍。
_RAW_CONTROL_ALLOWED = ("\n", "\r", "\t")


def _raw_control_chars_in_strings(text: str) -> list[str]:
    """**字符串字面量内部**的裸控制字符，按出现顺序逐字返回（词法扫描，不做语法解析）。

    正因为语法已经坏了才需要这一步：`json.loads` 只能报「第几个字节非法」，报不出「非法的是
    哪一类」，而容错必须逐类判定。转义序列（`\\n` 是两个字符）不是裸控制字符，因此反斜杠一
    置位就跳过下一个字符；字符串外的裸控制字符不进结果（那不是本函数的事：它们本来就不是
    合法 JSON 的空白，`json.loads` 自己会拒）。
    """
    found: list[str] = []
    in_string = False
    escaped = False
    for ch in str(text or ""):
        if not in_string:
            in_string = ch == '"'
            continue
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = False
            continue
        if ord(ch) < 0x20:
            found.append(ch)
    return found


def load_json_with_proven_leniency(text: str, what: str) -> Any:
    """LLM 返回文本 → JSON：容错**只**覆盖已证明的那一类序列化缺陷。

    严格解析先走一遍（几乎总是成功，且成功后一个字节都没被解释过）。只有失败**且**字符串内的
    裸控制字符全部落在 `_RAW_CONTROL_ALLOWED` 里时，才用 `strict=False` 重试。

    为什么不能直接调 `strict=False`：它是**布尔参数**，一开就同时放行全部 `0x00–0x1F`——
    NUL / BEL / VT / FF 这类字符没有任何现场证据，也没有无歧义语义（NUL 尤其：它在字符串里
    既可能是模型笔误也可能是被截断的二进制），顺带接受它们就是放宽 fail-closed。容错因此必须
    在这里**逐类收窄**：别的裸控制字符一律按非法 JSON 拒掉。

    反向也不放宽：字符串**外**的裸控制字符、以及任何结构性错误（键集、类型、闭合）照旧失败，
    这类失败由调用方按原有口径处理（整束抛，或补救路径退回 fail-closed）。
    """
    try:
        return json.loads(text)
    except (TypeError, ValueError) as strict_exc:
        illegal = sorted({c for c in _raw_control_chars_in_strings(text)
                          if c not in _RAW_CONTROL_ALLOWED})
        if illegal:
            shown = "、".join(f"{c!r}(U+{ord(c):04X})" for c in illegal)
            raise PackWriterError(
                f"{what}不是合法 JSON：字符串内出现未经证明的裸控制字符 {shown}。"
                f"只容忍字符串内的裸换行/回车/制表符（r7b 现场缺陷），其余控制字符不放行。"
            ) from strict_exc
        try:
            return json.loads(text, strict=False)
        except (TypeError, ValueError) as tolerant_exc:
            raise PackWriterError(f"{what}不是合法 JSON：{tolerant_exc}") from tolerant_exc


def parse_writer_proposals(text: str, *, aliases: SupportAliasTable | None = None,
                           require_natural_draft: bool = True) -> dict:
    """LLM 输出的**门前候选提案束** → 严格结构化结果（§6.4 / P18）。

    只做**结构**判定：顶层键集封闭、三条授权路径与两种语义各自自洽、必填文本非空、
    候选的第一条支撑边必须是 `primary`。「这个坐标在不在本次权威输入里」不在这一层判定，
    而是由写入侧回查权威目录与门完成（不按文本相似度猜）。

    `aliases`（`saref-1`）：本批请求**声明的**支撑选项别名表。给了它，支撑边才可以写短别名
    形式 `{"ref": ...}`，展开后与长格式走**同一个**校验器与**同一套**下游回查；不给它，写了
    `ref` 即 fail-closed（没有声明就没有可展开的一行）。

    `require_natural_draft`（`pw-16` 起缺省 `True`）：当前线下「**有候选、无草稿**」是
    typed failure（`natural_prose_draft_missing`），不是可以静默容忍的缺省。理由是这条形状
    在旧线下会一路走到「空草稿 + 把候选拼成正文」——那是**另一种**成稿路径，读者面看到的
    段落与门审的原子之间的对应关系随之丢失，而失败形状却是「成功」。因此这一形状只有一个
    出口：整批被拒（`schema_invalid`），由既有的批次形状纠正/缩小机制重问。
    置 `False` 只允许历史兼容线（`proposals-10` 的离线重放，见
    `WriterPolicy.proposal_wire`），且该线的调用点必须把线格式显式记进审计。
    """
    # 容错只覆盖**已证明**的那一类序列化缺陷（字符串内的裸换行/回车/制表符，见
    # `load_json_with_proven_leniency`）：其余全部结构判定（顶层键集封闭、边字段互斥、必填
    # 文本非空、首边必须 primary）逐条不变，别的裸控制字符也照旧拒。放开的是「这一个字节怎么
    # 编码」，不是「什么内容算合法」，因此它不是放宽 fail-closed。
    payload = load_json_with_proven_leniency(text, "候选提案")
    if not isinstance(payload, Mapping):
        raise PackWriterError("候选提案顶层必须是对象")
    _strict_keys(payload, _PLAN_KEYS, "候选提案")
    for key in _PLAN_KEYS:
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, list):
            raise PackWriterError(f"{key} 必须是数组")
    parsed = {
        "natural_prose_draft": _parse_prose_units(payload.get("natural_prose_draft") or [],
                                                 aliases=aliases),
        "claim_candidates": _parse_candidates(payload.get("claim_candidates") or [],
                                              aliases=aliases),
        "narrative_draft_units": _parse_units(payload.get("narrative_draft_units") or [],
                                             aliases=aliases),
        "follow_up_needs": _parse_follow_up_specs(payload.get("follow_up_needs") or []),
    }
    if require_natural_draft:
        assert_natural_draft_covers_candidates(parsed, source="候选提案")
    return parsed


def assert_natural_draft_covers_candidates(parsed: Mapping, *, source: str) -> None:
    """当前线的**唯一**一道形状判定：候选非空 ⇒ 草稿非空（`pw-16`）。

    它被三个入口共用——整束解析（`parse_writer_proposals`）、逐束补救
    （`_salvage_batch_plan`）与跨批合并（`write_section` 的合并层）：三处各写一份判定，
    等于给「补救放行、整束不放行」这类口径漂移留门。
    判的是**形状**，不是内容：候选文本是否被草稿覆盖由闭合核对
    （`validate_natural_prose_mapping`）判，两者不互相代言。
    """
    candidates = list(parsed.get("claim_candidates") or ())
    prose = list(parsed.get("natural_prose_draft") or ())
    if candidates and not prose:
        raise PackWriterError(
            f"{source}：{MISSING_NATURAL_DRAFT_FAILURE} —— 本束给了 {len(candidates)} 条候选"
            "却没有任何 natural_prose_draft 单元。当前线（"
            f"{PROPOSAL_WIRE_CURRENT}）要求先写自然草稿、再为草稿里的每个事实原子提交候选；"
            "缺草稿的一条束不得被当成「草稿为空、正文由候选拼成」而放行——那是另一条成稿路径，"
            "会让读者面的段落与门审的原子失去对应关系。请整束重出（含草稿），"
            "或显式改走历史兼容线（仅限离线重放）。")


def assert_batch_no_witness_means_empty(parsed: Mapping, *,
                                        batch: WriterPlanBatch | None = None,
                                        source: str = "") -> None:
    """`bscope-1`：本批**没有任何候选**（= 没有任何行能承重的事实原子）时，草稿与草稿单元都必须是空的。

    这是「整批无合法原子 ⇒ `natural_prose_draft` / `claim_candidates` / `narrative_draft_units`
    三个键一律空数组，只留 `follow_up_needs`」这条纪律的确定性一侧，且它补的是一个**真实的
    形状缺口**，不是把已有判定再写一遍：

      * 草稿侧（`natural_prose_draft`）在今天的线上已经死得掉——`_parse_one_prose_unit` 要求
        恰好一条非空出处轴、且 `atom_candidate_keys` 至少一条；合并层还会判原子是否指向**本批**
        候选。这两条**保留不动**，它们正是 r8 批 4/4 那三段无出处文字的既有反例。
      * **草稿单元侧（`narrative_draft_units`）今天没有任何见证要求**：`_parse_units` 允许
        `context_support` 为空数组（纯财务节确实合法没有材料行），于是一批「0 条候选 + 0 段草稿 +
        3 段『本轮未取得…无法作出说明』的草稿单元」可以**整批通过结构校验**，再把这 3 段缺失陈述
        带进最终自然段。这正是本判据要关掉的那一格。

    「本批有没有合法原子」这件事**不另立口径**：它的见证就是本批**自己声明的候选**——模型写不出
    候选，就说明它没能从本批可引用的行里读出一个原子。因此本判据不读材料、不读 `status`、不看
    文本像不像缺失陈述（那需要关键词规则，本仓禁止），只做一条计数：候选为空 ⇒ 另两个键必须为空。
    零候选零草稿零单元的**合法空批**（如实留 `follow_up_needs`）不受影响。

    调用点两处（与上面那条判据同构）：**逐批**解析之后（`_write_section` 的批次循环）与逐候选
    补救（`_salvage_batch_plan`）——补救那一路若能产出一份零候选带草稿单元的提案集，就会绕过
    本判据，因此两处共用**同一个**函数，不各写一份。违反时与其它形状失败走**同一条**通道
    （既有补救 → `bsc-1` 形状纠正 → typed `schema_invalid`），**不新增额度、不静默删段、
    不伪造引用**。
    """
    if list(parsed.get("claim_candidates") or ()):
        return
    prose = list(parsed.get("natural_prose_draft") or ())
    units = list(parsed.get("narrative_draft_units") or ())
    if not prose and not units:
        return
    where = (f"批次 {batch.label()}（{batch.batch_id}）" if batch is not None else source)
    raise PackWriterError(
        f"{where}：{BATCH_NO_WITNESS_FAILURE} —— 本批"
        f"**0 条候选**（没有任何可引用的行能承重一个事实原子），却交了 {len(prose)} 段 "
        f"natural_prose_draft 与 {len(units)} 条 narrative_draft_units。"
        "草稿与草稿单元都要指得出一条真实的输入行；没有候选时它们只能是空数组"
        "（见 batch_support_scope 与 rules）。缺失陈述（「本轮未取得 / 无法说明 / 未找到」）"
        "不是缺口：正式缺口由系统按检索轨迹与 Contract 判定。请把本批这三个键清空、"
        "只留 follow_up_needs，或改成本批真的读得出的候选。")


def _parse_one_prose_unit(item: Any, *, index: int,
                          aliases: SupportAliasTable | None = None) -> dict:
    """`natural_prose_draft` 的**一项** → 结构化草稿单元（逐单元校验的**唯一**实现）。

    四条边界各自都是「这一层不能表达什么」：
      * 出处是**两条互斥的轴**（`pprov-1`）：`source_member_refs`（材料行）与
        `source_fact_refs`（权威事实行）**恰有一条非空**。没有出处的散文在类型层进不了这一层
        （`NaturalProseDraftUnit` 同样要求非空）；
      * **哪条轴可用由本节的材料面决定，不由模型挑**：本节声明了材料行（`aliases` 里存在
        material 选项）时只准走材料轴——此时走事实轴即被拒，否则「材料正文写成的散文」会
        以事实行做出处，读者面回溯到的不再是那段文字；本节**只有**权威事实行时（财务节的
        常态，精确材料清单合法为空）只准走事实轴——此时走材料轴无从展开；
      * `atom_candidate_keys` 必须**至少一条**：草稿里的每一个事实原子都要指得出候选，否则
        「先写草稿、再逐原子提交候选」就退化成「先写一段散文、候选另说」；
      * 键集封闭：不得出现 `_PROSE_KEYS` 之外的字段（身份字段一律不在这里表达）。
    这里**只**做结构判定：这些键是否真的是本束的候选键、事实轴上的每一行是否真的是本次权威
    输入里的一行，分别由合并后的**闭合核对**（`validate_natural_prose_mapping`）与
    `_build_natural_prose_units` 的权威回查判——两处判同一件事会让「哪一处才是权威」变成噪声。
    """
    if not isinstance(item, Mapping):
        raise PackWriterError("natural_prose_draft 的每一项必须是对象")
    _strict_keys(item, _PROSE_KEYS, "natural_prose_draft 单元")
    label = str(item.get("prose_key") or f"p{index + 1}")
    text = str(item.get("text") or "").strip()
    if not text:
        raise PackWriterError(f"自然草稿单元 {label} 的 text 不得为空（草稿文本是它的内容身份）")
    if aliases is None:
        raise PackWriterError(
            f"自然草稿单元 {label} 用的是短别名形式（`ref`），但本批没有声明别名表："
            "没有声明就没有可展开的一行，不得凭 ref 猜材料身份")
    raw_refs = item.get("source_member_refs")
    raw_facts = item.get("source_fact_refs")
    for name, raw in (("source_member_refs", raw_refs), ("source_fact_refs", raw_facts)):
        if raw is None:
            # 缺省 = 空：两条轴都是「可以整条不写」的，但恰有一条要非空（下一句判）。
            continue
        if not isinstance(raw, list):
            raise PackWriterError(f"自然草稿单元 {label} 的 {name} 必须是数组")
    member_raw = list(raw_refs or [])
    fact_raw = list(raw_facts or [])
    if member_raw and fact_raw:
        raise PackWriterError(
            f"自然草稿单元 {label} 同时声明了 source_member_refs 与 source_fact_refs："
            "草稿的出处是两条**互斥**的轴（材料行 / 权威事实行），恰有一条非空")
    material_alias_refs = aliases.material_refs()
    if member_raw:
        member_refs: list[str] = []
        for pos, ref in enumerate(member_raw):
            option = aliases.option(str(ref or ""))
            if option.is_fact:
                raise PackWriterError(
                    f"自然草稿单元 {label} 的第 {pos + 1} 条出处 {option.ref!r} 指向**事实行**："
                    "source_member_refs 只取材料行（材料才是写作表达面的载体，事实通过候选与"
                    "支撑边进入；事实出处请写 source_fact_refs）")
            member = NS.manifest_member_ref(option.container_identity, option.material_id)
            if member not in member_refs:
                member_refs.append(member)
        fact_refs: list[str] = []
    elif fact_raw:
        if material_alias_refs:
            raise PackWriterError(
                f"自然草稿单元 {label} 用 source_fact_refs 作出处，但本节**有**材料行"
                f"（{len(material_alias_refs)} 条）：本节草稿的出处必须是材料"
                "（materials 的 ref）——事实轴的用处只在材料面合法为空的节（财务节），"
                "在有材料可写的节改用事实轴会让读者面回溯不到你实际写的那段文字")
        fact_refs = []
        for pos, ref in enumerate(fact_raw):
            option = aliases.option(str(ref or ""))
            if not option.is_fact:
                raise PackWriterError(
                    f"自然草稿单元 {label} 的第 {pos + 1} 条出处 {option.ref!r} 指向**材料行**："
                    "source_fact_refs 只取权威事实行（authority_facts 的 ref）")
            prov = NS.fact_provenance_ref(option.authority_kind, option.container_identity,
                                          option.fact_id)
            if prov not in fact_refs:
                fact_refs.append(prov)
        member_refs = []
    else:
        raise PackWriterError(
            f"自然草稿单元 {label} 必须恰好声明一条出处轴：source_member_refs（材料行）或"
            "source_fact_refs（权威事实行）（草稿不得凭空：它的每一句都要指得出一行真实输入）")
    raw_atoms = item.get("atom_candidate_keys")
    if not isinstance(raw_atoms, list) or not raw_atoms:
        raise PackWriterError(
            f"自然草稿单元 {label} 必须至少声明一条 atom_candidate_keys（草稿里的每个事实原子"
            "都要指得出候选；没有候选的散文无法进入审核）")
    atoms: list[str] = []
    for pos, key in enumerate(raw_atoms):
        atom = str(key or "")
        if not atom:
            raise PackWriterError(f"自然草稿单元 {label} 的第 {pos + 1} 条 atom_candidate_keys 为空")
        if atom not in atoms:
            atoms.append(atom)
    return {"prose_key": label, "text": text,
            "source_member_refs": member_refs, "source_fact_refs": fact_refs,
            "atom_candidate_keys": atoms}


def _parse_prose_units(raw: Sequence[Any], *, aliases: SupportAliasTable | None = None
                       ) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        unit = _parse_one_prose_unit(item, index=index, aliases=aliases)
        label = unit["prose_key"]
        if label in seen:
            raise PackWriterError(f"自然草稿单元标签 {label!r} 重复（单元必须在束内唯一可指）")
        seen.add(label)
        out.append(unit)
    return out


def _parse_one_candidate(item: Any, *, index: int, aliases: SupportAliasTable | None = None
                         ) -> dict:
    """`claim_candidates` 的**一项** → 结构化候选（逐候选结构校验的**唯一**实现）。

    整束解析（`_parse_candidates`）与逐候选补救（`_salvage_candidates`）读的是**同一个**函数：
    两条路各写一份校验，等于允许「整束拒了、补救却放行」这种两侧口径漂移。
    """
    if not isinstance(item, Mapping):
        raise PackWriterError("claim_candidates 的每一项必须是对象")
    _strict_keys(item, _CANDIDATE_KEYS, "claim_candidate")
    label = str(item.get("candidate_key") or f"c{index + 1}")
    text = str(item.get("claim_text") or "").strip()
    if not text:
        raise PackWriterError(f"候选 {label} 的 claim_text 不得为空（候选文本是它的内容身份）")
    support = item.get("support")
    if not isinstance(support, list) or not support:
        raise PackWriterError(f"候选 {label} 必须至少有一条 factual 支撑边（无据陈述不得进入）")
    edges = [_parse_factual_edge(edge, label=label, index=pos, aliases=aliases)
             for pos, edge in enumerate(support)]
    if edges[0]["support_role"] != "primary":
        raise PackWriterError(f"候选 {label} 的第一条支撑边必须是 primary（主要权威在前）")
    return {"candidate_key": label, "claim_text": text, "support": edges}


def _parse_candidates(raw: Sequence[Any], *, aliases: SupportAliasTable | None = None
                      ) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        candidate = _parse_one_candidate(item, index=index, aliases=aliases)
        label = candidate["candidate_key"]
        if label in seen:
            raise PackWriterError(f"候选标签 {label!r} 重复（候选必须在束内唯一可指）")
        seen.add(label)
        out.append(candidate)
    return out


def _salvage_candidates(text: str, *, aliases: SupportAliasTable | None = None
                        ) -> tuple[list[dict], list[dict]] | None:
    """整批返回 → `(通过逐候选结构校验的候选, 被逐条拒掉的候选)`；连顶层都读不出则 `None`。

    这是 §一.2 补充裁决的**逐候选裁出**：一束里**只有部分**候选结构非法时，不再把整批返回
    连同合法候选一起丢掉，而是把不合格的那些**逐条**记下、其余照原样继续走链。

    三条边界（缺一条就不做裁出，退回原样 fail-closed）：

      * 顶层必须是合法 JSON 对象、键集封闭、且每个声明过的顶层键真的是数组
        （容错口径与 `parse_writer_proposals` **同一个** `load_json_with_proven_leniency`：
        只容忍字符串内已证明的裸换行/回车/制表符）——连顶层都读不出时**没有**可信的逐候选
        归属，凭文本猜一份就是把猜测写成审计；带上未证明的裸控制字符同样读不出，照此退回；
      * 请求的候选键集必须真的是数组；
      * 至少要有一条候选通过、且至少要有一条被拒（全是好的说明本该走整束解析；全是坏的说明
        整批没有可继续的内容）。

    被拒条目记录的是**模型自己写的** `candidate_key` 与原文：它从未成为结构化对象，因此没有
    可派生的 `ClaimCandidate` 身份可给（与 `ProposalSetRejectionRecord` 对 `schema_invalid`
    的既有口径一致）。
    """
    try:
        payload = load_json_with_proven_leniency(text, "候选提案")
    except PackWriterError:
        return None
    if not isinstance(payload, Mapping):
        return None
    try:
        _strict_keys(payload, _PLAN_KEYS, "候选提案")
    except PackWriterError:
        return None
    for key in _PLAN_KEYS:
        value = payload.get(key)
        if value is not None and not isinstance(value, list):
            return None
    raw = payload.get("claim_candidates")
    if not isinstance(raw, list):
        return None
    kept: list[dict] = []
    rejected: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        label = (str(item.get("candidate_key") or f"c{index + 1}")
                 if isinstance(item, Mapping) else f"c{index + 1}")
        try:
            candidate = _parse_one_candidate(item, index=index, aliases=aliases)
        except PackWriterError as exc:
            rejected.append({"candidate_key": label,
                             "claim_text": (str(item.get("claim_text") or "")
                                            if isinstance(item, Mapping) else ""),
                             "reason": "candidate_structure_invalid",
                             "detail": str(exc)})
            continue
        if candidate["candidate_key"] in seen:
            rejected.append({"candidate_key": candidate["candidate_key"],
                             "claim_text": candidate["claim_text"],
                             "reason": "candidate_key_duplicated",
                             "detail": f"候选标签 {candidate['candidate_key']!r} 在本批内重复"})
            continue
        seen.add(candidate["candidate_key"])
        kept.append(candidate)
    if not kept or not rejected:
        return None
    return kept, rejected


def _salvage_batch_plan(text: str, *, aliases: SupportAliasTable | None = None,
                        require_natural_draft: bool = True
                        ) -> tuple[dict, list[dict]] | None:
    """整批返回 → `(只有部分候选可用的完整提案集, 被逐条拒掉的候选)`；不适用则 `None`。

    这是 §一.2 补充裁决在**批内**那一半的落点：整响应可解析、只是**若干条候选**自己结构非法
    （r7b 现场：批 3 的 `c1..c21` 里 `c9`/`c16` 的首条支撑边不是 `primary`）时，不再把这一批
    连同其余合法的候选一起丢掉。

    它比 `_salvage_candidates` 多一条**硬边界**：`natural_prose_draft`、`narrative_draft_units`
    与 `follow_up_needs` 必须**整体**解析成功。理由是「不得静默丢内容」——候选被逐条拒掉有逐条
    记录，而草稿单元 / 自然草稿 / 补件诉求被整段丢掉**没有**可逐条记名的主体（它们不是候选，
    本裁决也没有授权对它们逐条裁）。因此那三者只要有一处不合法，本函数就返回 `None`，由调用方
    走**既有**的 C4 批次形状纠正 / fail-closed，一个字不改。

    自然草稿在这一条上尤其不能例外：它是**候选的来源**（候选必须从草稿正文长出），把草稿逐条
    裁掉而保留候选，正好把「先草稿、后候选」这条顺序反过来。

    也因此本函数**不**返回「哪些单元/诉求被丢了」——那正是它不接受这类输入的原因。

    `require_natural_draft`（`pw-16` 起缺省 `True`）：**有候选、无草稿**在这一路同样返回
    `None`（该形状由共用的 `assert_natural_draft_covers_candidates` 判，本函数不自写第二份）。
    为什么「返回 `None`」在这里就是 typed fail-closed 而不是静默回落：`None` 把这一批送回
    调用方**既有的** C4 路径，那条路会把 `parse_writer_proposals` 的原始错误串（含
    `natural_prose_draft_missing` 这个词）逐字记进本批的形状纠正条目与随后的
    `schema_invalid` 拒绝记录，并对**这一批**重问一次。补救这一路之所以不能自己吞下这条
    形状，是因为它**恰好**会把「草稿被整段丢掉、候选留下」这个形状做出来——那正是这条判据
    要拦的事。
    """
    salvaged = _salvage_candidates(text, aliases=aliases)
    if salvaged is None:
        return None
    kept, rejected = salvaged
    payload = load_json_with_proven_leniency(text, "候选提案")
    try:
        prose = _parse_prose_units(payload.get("natural_prose_draft") or [], aliases=aliases)
        units = _parse_units(payload.get("narrative_draft_units") or [], aliases=aliases)
        follow_ups = _parse_follow_up_specs(payload.get("follow_up_needs") or [])
    except PackWriterError:
        return None
    plan = {"natural_prose_draft": prose, "claim_candidates": kept,
            "narrative_draft_units": units, "follow_up_needs": follow_ups}
    if require_natural_draft:
        try:
            assert_natural_draft_covers_candidates(plan, source="候选提案（逐候选补救）")
            # `bscope-1`：补救**不得**造出一个「零候选 + 有草稿单元」的提案集——那正是本判据
            # 要拦的形状，而补救这一路如果不判，就成了它的旁路（补救可以放过结构非法的候选，
            # 于是 kept 可以为空而单元仍在）。与批次循环共用同一个函数。
            assert_batch_no_witness_means_empty(plan, source="候选提案（逐候选补救）")
        except PackWriterError:
            return None
    return (plan, rejected)


def _parse_factual_edge(edge: Any, *, label: str, index: int,
                        aliases: SupportAliasTable | None = None) -> dict:
    """一条 factual 支撑边：路径 A 与路径 B 的字段**互斥**（不得同时给出）。

    短别名形式（`{"ref": ..., "support_role": ...}`）在这里先被展开成逐字段同形的长格式，
    随后走**下面同一条**判定路径——因此别名不是第二条口径，它只是「少写身份」的写法。
    """
    what = f"候选 {label} 的第 {index + 1} 条支撑边"
    edge = _expand_alias_edge(edge, aliases=aliases, what=what, chain="factual")
    if not isinstance(edge, Mapping):
        raise PackWriterError(f"{what} 必须是对象")
    _strict_keys(edge, _FACTUAL_SUPPORT_KEYS, what)
    kind = str(edge.get("authority_kind") or "")
    if kind not in NS.AUTHORITY_KINDS:
        raise PackWriterError(
            f"{what} 的 authority_kind={kind!r} 不在 {list(NS.AUTHORITY_KINDS)} 内")
    if not str(edge.get("container_id") or ""):
        raise PackWriterError(f"{what} 缺 container_id（容器身份不得省略）")
    if str(edge.get("support_semantics") or "") != "factual":
        raise PackWriterError(
            f"{what} 的 support_semantics 必须是 'factual'（候选只接受事实性支撑边："
            "context 材料只验证背景与衔接，不授权事实）")
    path = str(edge.get("authorization_path") or "")
    if path not in ("path_a_prevalidated", "path_b_material_derived"):
        raise PackWriterError(
            f"{what} 的 authorization_path={path!r} 不能支撑事实性候选"
            "（只允许 path_a_prevalidated 或 path_b_material_derived）")
    role = str(edge.get("support_role") or "")
    if role not in NS.SUPPORT_ROLES:
        raise PackWriterError(f"{what} 的 support_role={role!r} 不在 {list(NS.SUPPORT_ROLES)} 内")
    fact_id = str(edge.get("fact_id") or "")
    material_id = str(edge.get("material_id") or "")
    if path == "path_a_prevalidated":
        if not fact_id:
            raise PackWriterError(f"{what} 是路径 A，必须给出 fact_id（事实身份不得省略）")
        if material_id:
            raise PackWriterError(
                f"{what} 是路径 A 却自带 material_id：路径 A 的 material 锚点由写入侧从该权威"
                "事实**自己的引用**确定性派生，material 不由模型选择")
    else:
        if kind != "topic_pack":
            raise PackWriterError(
                f"{what} 是路径 B，但 authority_kind={kind!r}："
                "exact ResearchMaterial 只存在于 topic_pack")
        if not material_id:
            raise PackWriterError(f"{what} 是路径 B，必须给出 material_id")
        if fact_id:
            raise PackWriterError(
                f"{what} 是路径 B，不得携带任何事实身份（材料派生的描述性原子不带 fact_id）")
    return {"authority_kind": kind, "container_id": str(edge["container_id"]),
            "fact_id": fact_id or None, "material_id": material_id or None,
            "support_role": role, "support_semantics": "factual", "authorization_path": path}


def _parse_units(raw: Sequence[Any], *, aliases: SupportAliasTable | None = None) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise PackWriterError("narrative_draft_units 的每一项必须是对象")
        _strict_keys(item, _UNIT_KEYS, "narrative_draft_unit")
        label = str(item.get("unit_key") or f"u{index + 1}")
        if label in seen:
            raise PackWriterError(f"草稿单元标签 {label!r} 重复")
        seen.add(label)
        kind = str(item.get("unit_kind") or "")
        if kind not in NS.DRAFT_UNIT_KINDS:
            raise PackWriterError(
                f"草稿单元 {label} 的 unit_kind={kind!r} 不在 {list(NS.DRAFT_UNIT_KINDS)} 内")
        text = str(item.get("text") or "").strip()
        if not text:
            raise PackWriterError(f"草稿单元 {label} 的 text 不得为空")
        raw_context = item.get("context_support")
        if raw_context is None:
            raw_context = []
        if not isinstance(raw_context, list):
            raise PackWriterError(f"草稿单元 {label} 的 context_support 必须是数组")
        out.append({"unit_key": label, "unit_kind": kind, "text": text,
                    "context_support": [
                        _parse_context_edge(edge, label=label, index=pos, aliases=aliases)
                        for pos, edge in enumerate(raw_context)]})
    return out


def _parse_context_edge(edge: Any, *, label: str, index: int,
                        aliases: SupportAliasTable | None = None) -> dict:
    what = f"草稿单元 {label} 的第 {index + 1} 条 context 支撑边"
    edge = _expand_alias_edge(edge, aliases=aliases, what=what, chain="context")
    if not isinstance(edge, Mapping):
        raise PackWriterError(f"{what} 必须是对象")
    _strict_keys(edge, _CONTEXT_SUPPORT_KEYS, what)
    kind = str(edge.get("authority_kind") or "")
    if kind not in _CONTEXT_AUTHORITY_KINDS:
        raise PackWriterError(
            f"{what} 的 authority_kind={kind!r} 在本批的提案契约里不可表达为 context 边"
            f"（只允许 {list(_CONTEXT_AUTHORITY_KINDS)}：其余权威的 context 载体与事实身份共用"
            "定位字段，去掉事实身份后没有可解析的载体）")
    if not str(edge.get("container_id") or ""):
        raise PackWriterError(f"{what} 缺 container_id")
    if not str(edge.get("material_id") or ""):
        raise PackWriterError(f"{what} 必须绑定一份真实 material（context 不得凭空）")
    if str(edge.get("support_semantics") or "") != "context":
        raise PackWriterError(f"{what} 的 support_semantics 必须是 'context'")
    if str(edge.get("authorization_path") or "") != "context_only":
        raise PackWriterError(
            f"{what} 的 authorization_path 必须是 'context_only'"
            "（context 只验证背景/结构/衔接，不授权事实）")
    role = str(edge.get("support_role") or "")
    if role != "corroborating":
        raise PackWriterError(f"{what} 的 support_role 必须是 'corroborating'（context 边一律同向）")
    return {"authority_kind": kind, "container_id": str(edge["container_id"]),
            "material_id": str(edge["material_id"]), "support_role": role,
            "support_semantics": "context", "authorization_path": "context_only"}


def _parse_follow_up_specs(raw: Sequence[Any]) -> list[dict]:
    out: list[dict] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise PackWriterError("follow_up_needs 的每一项必须是对象")
        _strict_keys(item, _FOLLOW_UP_KEYS, "follow_up_need")
        spec = {key: ("" if item.get(key) is None else str(item.get(key)))
                for key in _FOLLOW_UP_KEYS}
        number = index + 1
        spec["statement"] = spec["statement"].strip()
        if not spec["statement"]:
            raise PackWriterError(f"follow_up_need #{number} 的 statement 不得为空")
        for key in ("target_requirement_id", "topic_id", "question_id", "aspect_id"):
            if not spec[key]:
                raise PackWriterError(
                    f"follow_up_need #{number} 缺 {key}（缺口不得无归属：申请必须指回真实需求）")
        if spec["requiredness"] not in TS.FOLLOW_UP_REQUIREDNESS:
            raise PackWriterError(
                f"follow_up_need #{number} 的 requiredness={spec['requiredness']!r} 不在 "
                f"{list(TS.FOLLOW_UP_REQUIREDNESS)} 内")
        if spec["expected_source_class"] not in TS.SOURCE_CLASSES:
            raise PackWriterError(
                f"follow_up_need #{number} 的 expected_source_class="
                f"{spec['expected_source_class']!r} 不在 {list(TS.SOURCE_CLASSES)} 内")
        out.append(spec)
    return out


# ---------------------------------------------------------------------------
# 权威回查 + 门前束构造（身份一律由写入侧派生，模型只能**选择**权威目录里的行）
# ---------------------------------------------------------------------------

def _resolve_fact_declaration(edge: Mapping[str, Any],
                              table: Mapping[tuple[str, str, str], AuthorityFactEntry],
                              *, what: str) -> AuthorityFactEntry:
    """提案声明的事实坐标 → 权威事实条目（找不到即拒，不猜、不模糊匹配）。"""
    key = (str(edge.get("authority_kind") or ""), str(edge.get("container_id") or ""),
           str(edge.get("fact_id") or ""))
    entry = table.get(key)
    if entry is None:
        raise PackWriterError(
            f"{what} 声明的权威事实坐标 {key} 不在本节的权威事实目录内"
            "（路径 A 只能**逐字选择**权威自己列出的事实；事实/容器身份不得自报）")
    return entry


def _resolve_material_declaration(
            edge: Mapping[str, Any], manifest: NS.WriterMaterialManifest,
            *, what: str) -> NS.WriterMaterialManifestEntry:
    """提案声明的 (container, material) → 本次写作精确材料清单里的成员（不在清单即拒）。"""
    container = str(edge.get("container_id") or "")
    material_id = str(edge.get("material_id") or "")
    if not container or not material_id:
        raise PackWriterError(f"{what} 必须同时给出 container_id 与 material_id")
    entry = manifest.entry_for(NS.manifest_member_ref(container, material_id))
    if entry is None:
        raise PackWriterError(
            f"{what} 引用的材料 (container={container!r}, material={material_id!r}) 不在本次写作"
            "的精确材料清单内（材料必须来自本次权威输入，不得自报）")
    return entry


def _candidate_fact_type(spec: Mapping[str, Any], label: str,
                         table: Mapping[tuple[str, str, str], AuthorityFactEntry]) -> str:
    """候选的事实类型：有路径 A 权威事实时**逐字取权威自己声明的那一个**。

    同一候选绑了多条事实类型不同的权威事实 → 无法判定（fail-closed），不猜一个通用值。
    只有路径 B（材料派生）的描述性原子没有权威事实类型，取 `descriptive`。
    """
    declared: set[str] = set()
    for edge in spec["support"]:
        if str(edge.get("authorization_path")) != "path_a_prevalidated":
            continue
        entry = _resolve_fact_declaration(edge, table, what=f"候选 {label} 的路径 A 支撑边")
        if entry.fact_type:
            declared.add(entry.fact_type)
    if not declared:
        return "descriptive"
    if len(declared) > 1:
        raise PackWriterError(
            f"候选 {label} 绑定了事实类型不一致的权威事实 {sorted(declared)}："
            "候选的事实类型必须唯一（不得把不同类事实塞进同一条原子断言）")
    return declared.pop()


# ---------------------------------------------------------------------------
# §二 / P0：路径 B 的授权面判据（写入侧）
# ---------------------------------------------------------------------------
#
# 路径 B 授权的是**非高风险描述性原子**。机械门（`gate_draft`）能独立复核的只有「候选文本里的
# 数字是否有路径 A 权威事实逐字授权」——那条规则对**任何**含数字且无路径 A 支撑的候选直接
# blocking。但高风险表面不止数字：显式否定、勾选/适用状态、法人主体身份、表格行列关系
# （合计/占比/其中）都不是抽取式描述，而是**判断或关系**，它们不该由材料派生路径引入。
#
# **判据是「命中即拒」，不是「逐字在场」**（§二 裁决，`pw-5`）：候选文本里出现任何高风险表面
# 即不可由材料派生授权，与该字面成分是否在绑定材料正文里逐字存在**无关**。旧口径把「token 能在
# 材料里找到」当成资格证明，等于让材料正文给高风险硬事实背书——而正式链里高风险硬事实只有
# authoritative fact identity（路径 A 预验证）一条入口。因此本模块**不再**持有任何词表别名、
# 也不再读材料正文：判据只吃候选文本，词表的唯一实现是 `NS.high_risk_surface_tokens`。
# 同一语义在机械门上另有 `cbg-2` 的 `path_b_high_risk_surface` 独立拒绝（对手工构造的 Draft）。


def _path_b_high_risk_surfaces(*, bundle: _PreGateBundle) -> dict[str, tuple[str, ...]]:
    """逐候选的**路径 B 授权面**判据：返回 `{candidate_id: 它携带的全部高风险表面}`。

    规则（§二 / P0，`pw-5`）：**路径 B 只授权非高风险描述性原子**。候选文本里只要出现任何
    高风险表面（数字/金额/比例/日期/期间/币种、显式否定、勾选与适用状态、法人主体身份、表格
    行列与合计/占比关系、因果或结论连接），该候选就**不能**由材料派生路径授权——**无论**这些
    字面成分是否在绑定材料的正文里逐字存在。

    旧口径（「token 能在材料正文里逐字找到就算被授权」）是**错误语义**：原文逐字存在不等于
    已经资格化，高风险硬事实只有 authoritative fact identity（路径 A 预验证）一条正式入口。
    因此本函数**不再读任何材料正文**，也不需要 `material_context`：判据只吃候选文本自己。

    只检查**含路径 B 支撑边**的候选；纯路径 A 候选的文本来自权威事实自己的文本，其高风险
    表面由 `gate_draft` 的路径 A 授权规则核验。
    """
    problems: dict[str, tuple[str, ...]] = {}
    for candidate in bundle.candidates:
        own = tuple(p for p in bundle.proposals
                    if p.binding_subject_kind == "claim_candidate"
                    and p.binding_subject_id == candidate.candidate_id)
        if not any(p.authorization_path == "path_b_material_derived" for p in own):
            continue
        surfaces = NS.high_risk_surface_tokens(candidate.claim_text)
        if surfaces:
            problems[candidate.candidate_id] = surfaces
    return problems


def _selection_applicability_scope(*, material_context: Any) -> dict[str, dict]:
    """`member_ref → {asked_item, trailing_content}`：**只在所问事项上说话**的表单行（`selappl-1`）。

    收录条件全部是材料**自己的**性质，与候选措辞无关，可逐份复算：

    * `content_qualification.kind == "selection_form"`（它确实是勾选表单行）；
    * `selection.permitted_use == TREE_MATERIAL_SELECTION_PERMITTED_USE`（它的允许用途就是
      「只在自己问的那件事上说话」）。

    这份表**不删任何材料**：它只回答「这条边最多能证明什么」。真正决定一条边能不能通过的是
    :func:`_path_b_ineligible_scope`——它拿这份表去核候选文本。
    """
    from harness import tree_materials as TM

    scope: dict[str, dict] = {}
    for material in tuple(getattr(material_context, "materials", ()) or ()):
        qualification = getattr(material, "content_qualification", None)
        if not isinstance(qualification, Mapping):
            continue
        if str(qualification.get("kind", "") or "") != "selection_form":
            continue
        selection = qualification.get("selection")
        if not isinstance(selection, Mapping):
            continue
        if str(selection.get("permitted_use", "") or "") != TM.TREE_MATERIAL_SELECTION_PERMITTED_USE:
            continue
        scope[str(material.member_ref)] = {
            "asked_item": str(selection.get("asked_item", "") or ""),
            "trailing_content": str(selection.get("trailing_content", "") or ""),
        }
    return scope


def _scope_tight(text: str) -> str:
    """去空白后的可比形式（**唯一**归一化：只去空白，不改写任何字）。

    只做空白归一，是因为「超出所问事项」是**逐字**判据：一旦这里顺手做同义改写或大小写折叠，
    「这条候选是不是只说了那一行问的事」就不再是可复算的了。
    """
    return "".join(str(text or "").split())


def _scope_confined(claim_text: str, *, asked_item: str, trailing_content: str) -> bool:
    """候选文本是否**逐字限定**在该表单行的所问事项内（唯一的资格判据实现）。

    「限定」= 去掉空白之后，候选文本是那一行 `asked_item` 的**子串**。这条判据的语义是：
    表单行的允许用途是「只在自己问的那件事上说话」，因此它最多能支撑「把那件事再说一遍」；
    任何**超出所问事项**的内容（该行所述内容 `trailing_content`、选项标签、选中状态、
    或挂在这行旁边的宽栏目叙述）都不由它承重。

    另外显式排除 `trailing_content`：它是这一行**管着**的那句话，是这份材料里最容易
    被误当成「原文如此、所以有资格」的一段。两者都非空时，无论所问事项怎么切，只要候选
    含了所述内容就不通过——这是 §二 2.3 `no_support_from_trailing_content` 的确定性执行。

    **空的 `asked_item` 一律不通过**（`tmr-3`）：所问事项只取行内前缀，行内没写主语时这一列
    就是空的——那一行读不出「问的是哪件事」，因此它**授权不了任何候选**。这是 fail-closed 的
    那一侧：空所问事项**不是**「没这条判据」，**更不是**「所问事项＝所在节点标题」
    （把子项状态锚到整个栏目上正是这么发生的）。绑定到这种材料上的路径 B factual 边
    会被 :func:`_path_b_ineligible_scope` 逐条点名。
    """
    body = _scope_tight(claim_text)
    governed = _scope_tight(trailing_content)
    if not body:
        return False
    if governed and governed in body:
        return False
    asked = _scope_tight(asked_item)
    if not asked:
        return False
    return body in asked


def _path_b_ineligible_scope(*, bundle: "_PreGateBundle",
                            scope: Mapping[str, Mapping[str, str]]
                            ) -> dict[str, tuple[str, ...]]:
    """逐候选的**支撑资格**判据：`{candidate_id: 不能承重的支撑边所绑材料成员}`。

    只检查**含路径 B factual 支撑边**的候选。context 边不进本判据：它挂的是叙述草稿单元，
    只承担背景/结构/衔接，本来就不授权任何事实原子（§二）。纯路径 A 候选也不进：它绑的是
    预验证权威事实，与表单行的允许用途无关。
    """
    problems: dict[str, tuple[str, ...]] = {}
    if not scope:
        return problems
    for candidate in bundle.candidates:
        offenders: list[str] = []
        for proposal in bundle.proposals:
            if proposal.binding_subject_kind != "claim_candidate" \
                    or proposal.binding_subject_id != candidate.candidate_id:
                continue
            if proposal.authorization_path != "path_b_material_derived":
                continue
            member_ref = NS.manifest_member_ref(
                str(getattr(proposal, "authority_container_id", "") or ""),
                str(getattr(proposal, "material_id", "") or ""))
            row = scope.get(member_ref)
            if row is None:
                continue
            if not _scope_confined(candidate.claim_text, asked_item=row["asked_item"],
                                   trailing_content=row["trailing_content"]):
                if member_ref not in offenders:
                    offenders.append(member_ref)
        if offenders:
            problems[str(candidate.candidate_id)] = tuple(offenders)
    return problems


def _support_documents(*, manifest: Any) -> dict[str, str]:
    """`{member_ref: document_id}`——本节材料清单里**文档系列**材料的来源文档（`srsc-1`）。

    只收能落到来源文档上的成员（`payload_ref.locator` 带 `document_id` 的 evidence 定位）。
    `structured` / `external_snapshot` 材料的 locator 没有「文档系列」这一轴——它们各有自己的
    权威与资格链——因此**不进这张表**：调用方据此把这类支撑边读成「不是文档系列支撑」，
    而不是读成「读不到角色」（后者会 fail-closed 误杀正当的结构化/外部支撑）。

    **成员读的是 `entries`，不是 `members`**：`WriterMaterialManifest` 的**字段**叫 `entries`，
    `members` 只是它的 wire 键（`to_dict` / `from_dict` / `identity_body` 里的那个名字）。
    这里必须读字段本身。读错属性名不会抛错——`getattr(..., ())` 会静默给出空元组，
    于是本函数恒返回 `{}`，`_material_source_roles` 恒返回 `{}`，请求面永远不带 `source_role`，
    `path_b_history_only_current_state` 与 :func:`verify_finalized_current_state_scope` 双双
    变成死代码，而整条链**照样跑完**。这正是本项目禁止的「规则写进文档 ≠ 代码已实现」，
    所以：清单回来了却没有 `entries` 时**当场抛**，不吃默认值。
    """
    if manifest is None:
        return {}
    entries = getattr(manifest, "entries", None)
    if entries is None:
        raise PackWriterError(
            f"材料清单 {type(manifest).__name__} 没有 `entries` 字段，无法读出本节文档系列材料："
            "来源角色核对（`srsc-1`）拿不到台账就只会静默放行，因此这里 fail-closed")
    out: dict[str, str] = {}
    for entry in tuple(entries):
        payload_ref = getattr(entry, "payload_ref", None)
        locator = payload_ref.get("locator") if isinstance(payload_ref, Mapping) else None
        document_id = str((locator or {}).get("document_id") or "") \
            if isinstance(locator, Mapping) else ""
        if document_id:
            out[str(getattr(entry, "member_ref", "") or "")] = document_id
    return {k: v for k, v in out.items() if k}


def _material_source_roles(*, authority: Any, manifest: Any) -> dict[str, str]:
    """`{member_ref: source_role}`——请求面给材料行标的**期间/来源角色**（`srsc-1`）。

    为什么这条标注必须在**请求面**：候选文本是模型写的。「这份材料能不能表达当前状态」这条
    判断只有把来源角色摆在生成器面前才**可执行**。只把规则写进 prompt 是不够的——materials 行
    有 `source_identity` 与 `locator_ref.document_id`，但**没有角色**；模型只能靠文档名去猜
    「哪一份是旧年报」，那正是「按文件名写特例」。角色是来源集台账推出来的**既成事实**，
    与门里判的是同一张表（同一份 :func:`_support_documents` + `SRS.document_roles`）。

    三种「没有角色」在这里**不区分**，都是「这一行不带角色」：权威没有来源文档系列这条轴
    （财务 / 附注 / 外部快照，见 `SRS.has_source_document_series`）、没有文档系列材料、以及
    该文档不在台账里。前两种是**这条轴上本来就没有角色**；第三种是调用方映射不完整——它不会
    被这里静默放过，`_path_b_history_only_current_state` 仍在门里当场 fail-closed（请求面
    只标注，不代替门做判定）。
    """
    documents = _support_documents(manifest=manifest)
    if not documents or not SRS.has_source_document_series(authority):
        return {}
    roles = SRS.document_roles(authority)
    return {ref: roles[doc] for ref, doc in documents.items() if doc in roles}


def _material_aspect_registrations(*, authority: Any, manifest: Any) -> dict[str, tuple[str, ...]]:
    """`{member_ref: 该材料在 Pack 侧登记归属的 aspect_ids}`（材料侧**归属轴**）。

    这条轴回答的问题是逐句核对**第二条**要问的那一个：「这一句引的这份材料，在 Pack 里
    登记归属到哪个 Contract 栏目？」它与三条既有轴**正交**、不可互推：

      * 来源角色（`srsc-1`）说的是「这份材料能不能表达当前状态」；
      * 精确清单（`wmm-2`）说的是「这份材料在不在本节的写作边界内」；
      * 本轴说的是「它在边界内**属于哪一栏**」。

    材料**在清单里**不等于**在本栏目**：清单是节级的，栏目是小节级的。两份材料可以同属本节、
    同一来源角色，却登记在不同的 aspect 上。少了这条轴，「引用存在、字面逐字相同」就会被
    读成「本栏目已被覆盖」——那正是本批要拦的那一种错。

    取值**只**来自 `TopicResearchPack.material_dispositions[*].aspect_ids`（`_pack_material_index`
    的同一配对，缺配对即 fail-closed），不由调用方、prompt 或模型自报。**登记为空**是**结论**
    （这份材料在 Pack 侧没有被认领到任何栏目），不是「读不到」——因此返回空 tuple 而不是
    从映射里省略该成员：省略会让「没登记」与「不在清单里」在下游长得一样。

    非 Pack 权威（财务 / 附注 / 外部快照）没有 `ResearchMaterial` 边界，返回 `{}`；那一支的
    栏目归属由**权威事实行自己**的 `aspect_ids` 承担。
    """
    if not isinstance(authority, TopicPackAuthorityInput):
        return {}
    entries = getattr(manifest, "entries", None)
    if entries is None:
        raise PackWriterError(
            f"材料清单 {type(manifest).__name__} 没有 `entries` 字段，无法读出本节材料的栏目归属："
            "逐句栏目核对拿不到登记轴就只会静默放行，因此这里 fail-closed")
    registered: dict[str, tuple[str, ...]] = {}
    for pack in sorted(tuple(getattr(authority.pack_set, "packs", ()) or ()),
                       key=lambda p: (str(p.topic_id), str(p.pack_id))):
        index = _pack_material_index(pack)
        for material_id in sorted(index):
            _material, disposition = index[material_id]
            member_ref = NS.manifest_member_ref(str(pack.pack_id), material_id)
            registered[member_ref] = tuple(
                dict.fromkeys(str(a) for a in (getattr(disposition, "aspect_ids", ()) or ())
                              if str(a or "")))
    return {str(getattr(entry, "member_ref", "") or ""): registered.get(
        str(getattr(entry, "member_ref", "") or ""), ())
        for entry in tuple(entries)}


def _path_b_history_only_current_state(*, bundle: "_PreGateBundle",
                                       documents: Mapping[str, str],
                                       roles: Mapping[str, str]
                                       ) -> dict[str, tuple[str, ...]]:
    """逐候选的**期间/来源角色**判据（O-12）：`{candidate_id: 那几条不能承重的边绑的成员}`。

    判据（唯一实现 `srsc-1`）：候选的路径 B factual 支撑边**全部**落在「不能表达当前状态」的
    来源角色上（同类较旧 / 同类而期间不可核实），而候选文本自己**没有**期间限定 ⇒ 这条断言
    不能按现在的措辞写出来。材料**留着**、身份不动：被纠正的是「这条边最多能证明到什么期间」，
    与「这条边能不能承重」（`_path_b_ineligible_scope`）是**两条正交的轴**。

    三条边界（与 `sections/source_role_scope.py` 模块头的三条同源）：

      * 只检查含**路径 B factual 边**的候选：纯路径 A 候选绑的是预验证权威事实，它在写入侧
        到不了具体来源文档，硬套会把权威事实整批误杀；
      * 支撑边里只要**有一条**不是文档系列材料（`structured` / `external_snapshot`），这条候选
        整体不进本判据——那些权威各有自己的资格链，不由本判据代言；
      * 一条边都没有的候选不进本判据（无支撑是别的判据的事）。

    查不到角色的支撑文档由 `SRS.unqualified_current_state` 当场抛（fail-closed）：读不到角色
    只说明调用方的映射不完整，不说明「这份材料是历史来源」。
    """
    problems: dict[str, tuple[str, ...]] = {}
    for candidate in bundle.candidates:
        members: list[str] = []
        docs: list[str] = []
        outside_axis = False
        for proposal in bundle.proposals:
            if proposal.binding_subject_kind != "claim_candidate" \
                    or proposal.binding_subject_id != candidate.candidate_id:
                continue
            if proposal.authorization_path != "path_b_material_derived":
                continue
            member_ref = NS.manifest_member_ref(
                str(getattr(proposal, "authority_container_id", "") or ""),
                str(getattr(proposal, "material_id", "") or ""))
            document_id = documents.get(member_ref)
            if not document_id:
                outside_axis = True
                continue
            members.append(member_ref)
            docs.append(document_id)
        if outside_axis or not docs:
            continue
        if SRS.unqualified_current_state(str(candidate.claim_text),
                                         support_document_ids=tuple(docs), roles=roles):
            problems[str(candidate.candidate_id)] = tuple(members)
    return problems


def _current_state_anchor_readings(*, member_refs: Sequence[str],
                                   documents: Mapping[str, str],
                                   axes: Mapping[str, Mapping[str, str]],
                                   manifest: Any,
                                   material_context: Any
                                   ) -> tuple["SRS.CurrentStateSupportReading", ...]:
    """把候选的路径 B factual 边解析成 `srsc-2` 的输入（实际侧 + 声明侧，逐层有出处）。

    实际侧（四轴 / 载荷摘要 / 正文）取自 **manifest 成员 + `wmctx-1` 已解析正文**，不是边自报；
    声明侧只取成员自己的 `locator_ref` 容器（`SRS.declared_axes_from_locator`）。成员查不到、
    正文解析不出来等情形**不跳过**：跳过会让判据读成「这条边不存在」，而它要读的是
    「这条边在，但它的身份/正文闭不上」——两者方向相反，因此这里一律落成**空值**，由判据按
    `axis_missing` / `no_reading_supplied` 落 typed 结论。
    """
    readings: list["SRS.CurrentStateSupportReading"] = []
    for member_ref in member_refs:
        ref = str(member_ref)
        entry = manifest.entry_for(ref)
        axis = dict(axes.get(str(documents.get(ref, "") or ""), {}) or {})
        declared = SRS.declared_axes_from_locator(
            getattr(entry, "locator_ref", None)) if entry is not None else None
        reading_view = ""
        actual_payload_hash = ""
        if entry is not None and material_context is not None:
            resolved = MC.reading_for_manifest_member(
                material_context=material_context, member=entry)
            reading_view = str(resolved.reading_view)
            actual_payload_hash = str(resolved.payload_hash)
        readings.append(SRS.CurrentStateSupportReading(
            company_id=str(axis.get("company_id", "") or ""),
            document_id=str(axis.get("document_id", "") or ""),
            document_version=str(axis.get("document_version", "") or ""),
            evidence_set_version=str(axis.get("evidence_set_version", "") or ""),
            declared_document_id=(declared[0] if declared else ""),
            declared_document_version=(declared[1] if declared else ""),
            declared_payload_hash=(str(getattr(entry, "payload_hash", "") or "")
                                   if entry is not None else ""),
            actual_payload_hash=actual_payload_hash,
            locator_ref=(str(dict(getattr(entry, "locator_ref", {}) or {}).get("owner") or "")
                         if entry is not None else ""),
            reading_view=reading_view))
    return tuple(readings)


#: `path_b_unproven_current_state` 的**原因闭集**（`srsc-3`）。它是
#: :data:`SRS.CURRENT_STATE_SUPPORT_REASONS` 的**子集**，只收「判据真的判了 unproven」的码：
#:
#:   * `not_extractive_in_any_current_source`——压根没有锚边材料包含它（材料不足）；
#:   * `source_period_scope_dropped`——有材料包含它，但**只有**截掉命中那句源文自带期间/
#:     范围限定的那种包含（措辞口径变了）。
#:
#: 不在其中的码各有出口：`extractive_contiguous_containment` 不是 unproven；`axis_missing` 与
#: 三个 `axis_mismatch_*` 是**身份链断裂**（`mismatch`，fail-closed），归各自那条判据；
#: `no_reading_supplied` / `candidate_text_empty` 是调用面缺陷。把它们收进来会让「这条候选为
#: 什么不能承重」多出几个永远不会出现的取值，读的人分不清哪些是真读数、哪些是占位。
UNPROVEN_CURRENT_STATE_CAUSES = (
    "not_extractive_in_any_current_source",
    "source_period_scope_dropped",
)


@dataclass(frozen=True)
class UnprovenCurrentStateFinding:
    """一条「有当前锚边、却证不出来」候选的 typed 结论（`srsc-2` + `srsc-3`）。

    为什么把原因码与成员身份**放在同一条记录里**而不是两张平行表：两者说的是同一件事的两面
    ——「哪几条边引出了这一判」与「这一判是怎么来的」。分成两个 dict 就必须额外维护「键集相同」
    这条口头约定，而两处消费点（整束拒绝的逐候选审计、裁出的逐候选去向）分别读一半时，任一侧
    漏一个键都不会当场失败——那正是本模块在别处用类型层不变式挡掉的那类错误。
    """

    #: 这条候选那几条当前锚边绑定的材料成员（非空）。
    member_refs: tuple[str, ...]
    #: 判据给出的原因码（闭集 :data:`UNPROVEN_CURRENT_STATE_CAUSES`）。
    cause_code: str

    def __post_init__(self) -> None:
        if not self.member_refs:
            raise PackWriterError(
                "UnprovenCurrentStateFinding 必须至少绑定一条当前锚边成员"
                "（没有锚边的候选进不了这条判据，见 _path_b_unproven_current_state）")
        if self.cause_code not in UNPROVEN_CURRENT_STATE_CAUSES:
            raise PackWriterError(
                f"UnprovenCurrentStateFinding.cause_code={self.cause_code!r} 不在 "
                f"{list(UNPROVEN_CURRENT_STATE_CAUSES)} 内（判据的原因码是闭集，"
                "自由文本会让「为什么这条不能承重」有两个答案）")


def _path_b_unproven_current_state(*, bundle: "_PreGateBundle",
                                   documents: Mapping[str, str],
                                   roles: Mapping[str, str],
                                   axes: Mapping[str, Mapping[str, str]],
                                   manifest: Any,
                                   material_context: Any
                                   ) -> dict[str, UnprovenCurrentStateFinding]:
    """逐候选的**独立支撑结论**判据（`srsc-2` + `srsc-3`）：`{candidate_id: 结论记录}`。

    判据（唯一实现 `SRS.current_state_support_is_extractive`）：候选文本**不是**任何一条
    「落在能表达当前状态的来源上」的路径 B factual 边的正文的**严格抽取式子串**（仅空白归一）
    时，这条当前态断言按现在的措辞**证不出来**。

    它把 `_path_b_history_only_current_state` 的边界 2 补完：那条判据只在「一条当前锚边都没有」
    时开火，于是「**有一条**边落在当前锚上」被当成了「较新材料已核实这条命题」。

    锚边集合取 :data:`SRS.CURRENT_STATE_ANCHOR_ROLES`（`current_state_source`），**不是**
    :data:`SRS.CANNOT_ESTABLISH_CURRENT_STATE_ROLES` 的补集——补集会把 `topic_participating_source`
    （**其他系列**成员，例如同一发行人的另一类文件）也读成「较新材料」，与边界 3 「跨系列成员按
    主题与锚同等资格参与检索」直接冲突。代价如实写明：**两条判据互斥但不互补**，一条只由跨系列
    成员支撑、自己又没写期间的候选两条都不进，它由自己那条链的资格判据负责。

    与 `_path_b_history_only_current_state` **互斥**：两者都以「候选自己没有期间限定」为前提，
    而前者要求全部支撑文档都在不能表达当前状态的角色里、后者要求至少一条是当前锚 ⇒ 同一条候选
    不可能同时命中。`srsc-1` 命中时不进本判据（由那条判据负责），命中者**当场断言**互斥，
    不靠调用顺序保证。

    三条边界与 `srsc-1` 逐条同源（只读路径 B factual 边；含非文档系列边的候选整体不适用；
    一条边都没有的候选不适用），另加一条：有**期间限定**的候选不进本判据——它已经不是
    「当前态断言」，判据不为它代言。这一条在本函数里是**显式**的一次
    `SRS.has_period_qualification` 判断，不靠 `unqualified_current_state` 顺带覆盖：后者在
    「支撑边落在当前锚上」时提前返回 `False`（它自己的边界 2），根本走不到期间检查。

    **真的有一条候选需要判**（它有当前锚边、又不是前四类边界）而 `wmctx-1` 正文上下文缺失时
    **当场抛**：那时判据的输入面不存在，任何结论（无论判向哪边）都是猜；静默返回空表会让这条
    判据在真实链上变成死代码。

    抛出点刻意放在**需要正文的那一秒**，不是函数入口：上面那几条边界（只由旧材料支撑 / 跨系列 /
    写了期间 / 路径 A / 含非文档系列边 / 一条边都没有）**本来就不需要正文**，它们的结论与有没有
    正文无关。放在入口会让「这条候选进不进得来」与「缺不缺正文」两件事在调用方眼里粘成一件，
    而那正是本模块最容易被读错的一处（与 `verify_finalized_current_state_scope` 的判法同形）。

    `srsc-3`（M930-3 定点返修 P2）：结论为 `unproven` 时**原因码一并带出去**
    （:class:`UnprovenCurrentStateFinding`）。`srsc-2` 只回答「证不证得出来」，于是一条把源句
    自带的「报告期内」截掉的候选与一条压根没有材料包含它的候选，在产物里长得一模一样——而这两
    件事的**出路不同**：前者可以「把限定语一起收回原文」，后者只能改绑路径 A 事实或撤下。逐条
    带原因码是把这条区别做成**读数**，不是留给读的人去猜。
    """
    problems: dict[str, UnprovenCurrentStateFinding] = {}
    for candidate in bundle.candidates:
        members: list[str] = []
        docs: list[str] = []
        outside_axis = False
        for proposal in bundle.proposals:
            if proposal.binding_subject_kind != "claim_candidate" \
                    or proposal.binding_subject_id != candidate.candidate_id:
                continue
            if proposal.authorization_path != "path_b_material_derived":
                continue
            member_ref = NS.manifest_member_ref(
                str(getattr(proposal, "authority_container_id", "") or ""),
                str(getattr(proposal, "material_id", "") or ""))
            document_id = documents.get(member_ref)
            if not document_id:
                outside_axis = True
                continue
            members.append(member_ref)
            docs.append(document_id)
        if outside_axis or not docs:
            continue
        # 第三条边界之外的那一条（docstring 里的「另加一条」）：候选**自己写明了期间**时，
        # 它已经不是「当前态断言」，本判据不为它代言。
        #
        # 这一条必须**显式**写出来，不能靠 `unqualified_current_state` 顺带覆盖：那条判据在
        # 「支撑边落在当前锚上」时**提前返回 False**（它的边界 2），根本走不到自己的期间检查，
        # 于是「有锚边 + 写了期间」的候选会带着期间限定落进下面的抽取式判定——而抽取式判定
        # 判的是「无期间当前断言被较新材料核实」，对一条历史断言既不该开火也没有意义。
        if SRS.has_period_qualification(str(candidate.claim_text)):
            continue
        if SRS.unqualified_current_state(str(candidate.claim_text),
                                         support_document_ids=tuple(docs), roles=roles):
            continue
        anchors = [member for member, document_id in zip(members, docs)
                   if roles.get(document_id) in SRS.CURRENT_STATE_ANCHOR_ROLES]
        if not anchors:
            continue
        if material_context is None:
            raise PackWriterError(
                f"`srsc-2` 独立支撑结论判据需要 `wmctx-1` 材料正文上下文：候选 "
                f"{str(candidate.candidate_id)!r} 有 {len(anchors)} 条当前锚边，没有正文就"
                "无法判定「较新材料是否确实核实了这条命题」，不得静默跳过"
                "（那会让本判据成为死代码）")
        verdict = SRS.current_state_support_is_extractive(
            str(candidate.claim_text),
            candidate_revision=f"{candidate.draft_revision}#{candidate.candidate_id}",
            readings=_current_state_anchor_readings(
                member_refs=anchors, documents=documents, axes=axes,
                manifest=manifest, material_context=material_context))
        if verdict.result != "extractive":
            # `srsc-3`：原因码**逐条带出去**，不压成一句「证不出来」。两种来由的处置相同
            # （都不写），但读回来意思不同（材料不足 vs 措辞口径变了），且**出路不同**：
            # 前者只能改绑路径 A 或撤下，后者还可以把限定语一起收回原文。
            problems[str(candidate.candidate_id)] = UnprovenCurrentStateFinding(
                member_refs=tuple(anchors), cause_code=str(verdict.reason_code))
    return problems


def verify_finalized_current_state_scope(*, authority: Any, claims: Sequence[Any],
                                         acceptance: Any, manifest: Any,
                                         material_context: Any = None) -> None:
    """**最终句读回**（`srsc-1`）：门后定稿的 Claim 里不得留下「只由同类较旧材料支撑的当前态断言」。

    为什么在门前判过一次之后还要在这里再读一遍：O-12 要求这条核对在**候选、组织与最终句读回**
    三处都在场，而这三处读的**不是同一个对象**——门前是候选文本与提案边，这里是**定稿 Claim**
    与它自己那份完整 factual accepted 集。定稿不改写命题（`finalize_section_claims` 的规则 2），
    因此正常情况下两处结论必然一致；不一致只可能来自上游某条边被换掉或漏记，那正是要在
    `SectionResult` 形成**之前**当场停下的缺陷。判据仍是同一份 `SRS.unqualified_current_state`：
    不在这里另立第二套口径。

    三条边界与门前判据**逐条同源**（`_path_b_history_only_current_state`）：只读路径 B factual 边；
    含非文档系列边（结构化 / 外部快照）的 Claim 整体不适用；一条边都没有的 Claim 不适用。
    查不到角色的支撑文档由判据当场抛（fail-closed），本函数不猜。

    `srsc-2` 在同一处分两段读回，读的仍是**同两条判据**：`srsc-1` 那段之外，`srsc-1` 放过的
    Claim（至少有一条边落在能表达当前状态的来源上）还要过一遍**独立支撑结论**
    （`SRS.current_state_support_is_extractive`）。判向 `extractive` 才算过；`unproven` /
    `mismatch` 都当场停——`mismatch`（同 ID 错版本 / 错 Evidence Set / 身份缺失）尤其不得在
    句读回被放过：它说的是提案自身的身份链断了，不是「这条边弱一点」。为此本函数需要
    `wmctx-1` 正文上下文；缺它而 Claim 又确实带当前锚边时**抛**，不静默跳过。与候选侧同形，
    Claim 自己**写明了期间**时它是历史断言、不进 `srsc-2`，那一步同样是显式判断。
    """
    documents = _support_documents(manifest=manifest)
    if not documents or not SRS.has_source_document_series(authority):
        return
    roles = SRS.document_roles(authority)
    axes = SRS.document_axis_index(authority)
    by_id = {str(getattr(b, "accepted_support_binding_id", "") or ""): b
             for b in tuple(getattr(acceptance, "accepted_bindings", ()) or ())}
    problems: list[tuple[str, tuple[str, ...]]] = []
    unproven: list[tuple[str, tuple[str, ...], str]] = []
    for claim in tuple(claims or ()):
        members: list[str] = []
        docs: list[str] = []
        outside_axis = False
        for binding_id in tuple(getattr(claim, "accepted_binding_ids", ()) or ()):
            binding = by_id.get(str(binding_id))
            if binding is None:
                raise PackWriterError(
                    f"定稿 Claim {getattr(claim, 'claim_id', '')!r} 引用了不在本节 accepted 集里的"
                    f"支撑边 {binding_id!r}：句读回读不到这条边绑的是什么材料，不得当成「没有边」")
            if str(getattr(binding, "support_semantics", "")) != "factual":
                continue
            if str(getattr(binding, "authorization_path", "")) != "path_b_material_derived":
                continue
            member_ref = NS.manifest_member_ref(
                str(getattr(binding, "authority_container_id", "") or ""),
                str(getattr(binding, "material_id", "") or ""))
            document_id = documents.get(member_ref)
            if not document_id:
                outside_axis = True
                continue
            members.append(member_ref)
            docs.append(document_id)
        if outside_axis or not docs:
            continue
        claim_text = str(getattr(claim, "text", "") or "")
        if SRS.unqualified_current_state(claim_text, support_document_ids=tuple(docs),
                                         roles=roles):
            problems.append((str(getattr(claim, "claim_id", "") or ""), tuple(members)))
            continue
        # §`srsc-2`：`srsc-1` 放过的那些（至少有一条边落在**当前锚**上）在这里补上
        # **独立支撑结论**。锚边集合取 `SRS.CURRENT_STATE_ANCHOR_ROLES`，不是 `srsc-1` 的
        # 补集——见 `_path_b_unproven_current_state` 的口径说明与模块头边界 5。
        #
        # 与候选侧**逐字同形**的前置：定稿 Claim 自己写明了期间时它已经不是「当前态断言」。
        # 这里同样要显式判，理由与 `_path_b_unproven_current_state` 里那一段相同——
        # `unqualified_current_state` 在「支撑边落在当前锚上」时提前返回 False，覆盖不到。
        if SRS.has_period_qualification(claim_text):
            continue
        anchors = [member for member, document_id in zip(members, docs)
                   if roles.get(document_id) in SRS.CURRENT_STATE_ANCHOR_ROLES]
        if not anchors:
            continue
        if material_context is None:
            raise PackWriterError(
                f"最终句读回的 `srsc-2` 判据需要 `wmctx-1` 材料正文上下文：定稿 Claim "
                f"{str(getattr(claim, 'claim_id', '') or '')!r} 有 {len(anchors)} 条当前锚边，"
                "没有正文就没有判据输入，不得当成「已核实」")
        verdict = SRS.current_state_support_is_extractive(
            claim_text,
            candidate_revision=f"{str(getattr(claim, 'draft_revision', '') or '')}"
                               f"#{str(getattr(claim, 'claim_id', '') or '')}",
            readings=_current_state_anchor_readings(
                member_refs=anchors, documents=documents, axes=axes,
                manifest=manifest, material_context=material_context))
        if verdict.result != "extractive":
            unproven.append((str(getattr(claim, "claim_id", "") or ""), tuple(anchors),
                             f"{verdict.result}/{verdict.reason_code}"))
    if problems:
        raise PackWriterError(
            f"最终句读回发现只由同类较旧（或期间不可核实）材料支撑、却写成无期间当前断言的定稿 "
            f"Claim：{problems[:6]}——O-12：旧同类型材料只用于历史、变化与冲突核对，不得静默充当"
            "当前状态。这些 Claim 必须在门前就被拦下（`path_b_history_only_current_state`）；"
            "在句读回出现说明候选侧判据被绕过或上游边被换过，不得据此继续组装 SectionResult")
    if unproven:
        raise PackWriterError(
            f"最终句读回发现当前锚边**没有独立支撑结论**的无期间当前断言定稿 Claim："
            f"{unproven[:6]}（逐条给出 `结果/理由码`，成员身份可拿同一份 manifest 与来源集复算）"
            "——`srsc-2`：「支撑边里有一条较新材料」不等于「这条命题被较新材料核实过」。"
            "这些 Claim 必须在门前就被拦下（`path_b_unproven_current_state`）；在句读回出现说明"
            "候选侧判据被绕过或上游边被换过，不得据此继续组装 SectionResult")


def _gate_issue_rules_by_candidate(
        *, bundle: "_PreGateBundle",
        issues: Sequence[NS.NarrativeGateIssue]) -> dict[str, tuple[str, ...]]:
    """把门问题**归属到候选**：`{candidate_id: (rule_id, ...)}`，归属不上的一律不记。

    门的 `location` 是 typed 引用（候选 id / proposal id / 草稿单元 id / `container:fact` /
    容器名列表），因此归属是**查表**而不是文本匹配：

      * `location` 自己就是本节一条候选的 `candidate_id` → 归它；
      * `location` 是一条 **claim_candidate 侧** proposal 的 `proposed_support_id` → 归它绑定的
        那条候选（`binding_subject_id`）；
      * 其余（草稿单元、事实坐标、容器）→ **不归属**。草稿单元问题说的是叙述草稿，不是候选
        自己的断言；把它算到某条候选头上就是发明归属。

    归属不上的问题**不消失**：它仍在 `rejection_detail` 的整束明细里。这里少记一条，只会让
    逐候选审计少一分具体，不会让原因凭空多出来。
    """
    candidate_ids = {str(c.candidate_id) for c in bundle.candidates}
    owner_of_proposal: dict[str, str] = {}
    for proposal in bundle.proposals:
        if str(getattr(proposal, "binding_subject_kind", "")) != "claim_candidate":
            continue
        owner_of_proposal[str(proposal.proposed_support_id)] = str(proposal.binding_subject_id)
    out: dict[str, list[str]] = {}
    for issue in issues:
        location = str(issue.location)
        target = location if location in candidate_ids else owner_of_proposal.get(location)
        if target in candidate_ids:
            rules = out.setdefault(str(target), [])
            rule_id = str(issue.rule_id)
            if rule_id not in rules:
                rules.append(rule_id)
    return {k: tuple(v) for k, v in out.items()}


def _carvable_context_units(
        *, bundle: "_PreGateBundle",
        issues: Sequence[NS.NarrativeGateIssue]) -> dict[str, tuple[str, ...]] | None:
    """`cco-6`：这一束的 blocking 问题里，**能不能**只靠撤下几段 context 衔接文字就全部消掉？

    返回 `{draft_unit_id: (rule_id, ...)}`（可裁）；**只要有一条 blocking 问题不属于可裁形状就返回
    `None`**（不可裁，调用方照旧整束 fail-closed，一个字不改）。

    可裁形状只有一种，四个条件**同时**成立：

      1. `issue.rule_id` 取自封闭词表 `CONTEXT_UNIT_CARVE_OUT_REASONS`（当前只有
         `narrative_vague_period`）——别的门规则问的是「这条断言能不能承重」，不是「这段衔接
         文字该不该出现」，撤下一段文字回答不了它；
      2. `issue.location` 是本束 `bundle.units` 里某个单元的 `draft_unit_id`——**不是**候选。
         这一条是硬边界：候选文本踩同一判据时，出路由模型改绑路径 A 的预验证事实（那类事实自带
         期间文本）或撤下，裁出**不得**替它决定。把候选与衔接文字一起静默撤下，等于用一个删除
         动作回答一条本该由模型改绑的断言；
      3. 那个单元在该束里确实存在（`bundle.units` 是 context 侧的**唯一**合法 target，
         `NaturalProseDraftUnit` 与它不共用身份空间）；
      4. **撤下之后还剩得下东西**——被点名的单元数**严格小于**该束单元数。全部单元都被点名时
         「幸存数为 0」，与整束拒绝是同一件事（§0.15 第 5 条），此时**不裁出**，退回既有路径
         一个字不改。少了这一条，这一支会在裁决对象构造处撞上校验并抛出一条**不带门规则 id** 的
         错误，让「这一束为什么被拒」在产物里读成一个跟 `narrative_vague_period` 无关的原因。

    为什么是「全部可裁才裁」而不是「可裁的裁、其余照旧拒」：这一束的最后命运只有两种——要么被
    采信、要么不被采信。若撤下几个单元之后还有别的 blocking 问题，那些问题**本来**就会让这一束
    再次被拒；先裁一次只是把同一次拒绝拆成两轮，多出一条内容相同的拒绝记录，并让「这一束为什么
    没有产出」在产物里读起来像两件事。因此有一条不可裁即整束退回既有路径。

    返回 `None` 与返回空 dict 是两件不同的事：前者是「不可裁」（整束 fail-closed），后者是
    「没有任何 blocking 问题点名了 context 单元」。`gate_result.blocking` 为真时后者不可能发生，
    因此这里不造两种空值：**不可裁**一律 `None`，`{}` 永不出现在返回值里。
    """
    unit_ids = {str(u.draft_unit_id) for u in bundle.units}
    out: dict[str, list[str]] = {}
    for issue in issues:
        rule_id = str(issue.rule_id)
        location = str(issue.location)
        if rule_id not in CONTEXT_UNIT_CARVE_OUT_REASONS or location not in unit_ids:
            return None
        rules = out.setdefault(location, [])
        if rule_id not in rules:
            rules.append(rule_id)
    if not out:
        return None
    # 条件 4：幸存数为 0 时不裁（`excluded == 全部` 与整束拒绝是同一件事）。
    if len(out) >= len(unit_ids):
        return None
    return {k: tuple(v) for k, v in out.items()}


def _candidate_audit(*, bundle: "_PreGateBundle",
                     high_risk: Mapping[str, Sequence[str]] | None = None,
                     ineligible: Mapping[str, Sequence[str]] | None = None,
                     history_only: Mapping[str, Sequence[str]] | None = None,
                     unproven_current_state: Mapping[str, Sequence[str]] | None = None,
                     gate_issues: Sequence[NS.NarrativeGateIssue] = ()
                     ) -> tuple[RejectedCandidateAudit, ...]:
    """整束被拒时，**逐候选**给出 typed 原因（§二 3 / 3.4）。

    顺序与 `candidate_ids` 一致（按模型输出的原序），键集**完整**——少一条就会让
    `ProposalSetRejectionRecord` 的键集不变式当场失败，而不是悄悄少记一条。

    没有任何单独原因的候选拿到 `not_individually_implicated`（含义见该词条）：它在产物里读作
    「这一束被拒不是因为它」，而**不是**「它可以留用」。因此本函数**不**过滤任何候选，也**不**
    返回「其余那些」——它逐条回答「这条被点了什么名」，不回答「哪几条可以留」。
    """
    rules = _gate_issue_rules_by_candidate(bundle=bundle, issues=gate_issues)
    surfaces_of = {str(k): tuple(v) for k, v in (high_risk or {}).items()}
    ineligible_of = {str(k): tuple(v) for k, v in (ineligible or {}).items()}
    history_only_of = {str(k): tuple(v) for k, v in (history_only or {}).items()}
    unproven_of = {str(k): v for k, v in (unproven_current_state or {}).items()}
    audit: list[RejectedCandidateAudit] = []
    for candidate in bundle.candidates:
        cid = str(candidate.candidate_id)
        reasons: list[str] = []
        surfaces = surfaces_of.get(cid, ())
        if surfaces:
            reasons.append("path_b_high_risk_surface")
        members = ineligible_of.get(cid, ())
        if members:
            reasons.append("path_b_ineligible_material_scope")
        history_members = history_only_of.get(cid, ())
        if history_members:
            reasons.append("path_b_history_only_current_state")
        finding = unproven_of.get(cid)
        unproven_members = tuple(finding.member_refs) if finding is not None else ()
        if unproven_members:
            reasons.append("path_b_unproven_current_state")
        rule_ids = rules.get(cid, ())
        if rule_ids:
            reasons.append("gate_blocking")
        if not reasons:
            reasons.append("not_individually_implicated")
        audit.append(RejectedCandidateAudit(
            candidate_id=cid, reasons=tuple(reasons),
            rule_ids=tuple(rule_ids) if rule_ids else (),
            surfaces=surfaces if surfaces else (),
            ineligible_member_refs=members if members else (),
            history_only_member_refs=history_members if history_members else (),
            unproven_current_state_member_refs=(
                unproven_members if unproven_members else ()),
            unproven_current_state_cause_code=(
                str(finding.cause_code) if finding is not None else "")))
    return tuple(audit)


def _path_b_rejection_detail(*, high_risk: Mapping[str, Sequence[str]],
                             ineligible: Mapping[str, Sequence[str]],
                             history_only: Mapping[str, Sequence[str]] | None = None,
                             unproven_current_state: Mapping[str, "UnprovenCurrentStateFinding"]
                             | None = None
                             ) -> str:
    """整束被拒的 `rejection_detail`：**四组**逐候选点名原样写在一句里（`pbface-4`）。

    为什么合成一句而不是各拒各的：这些触发共用同一份额度、同一次排定（见
    `MAX_DIRECTED_REPROPOSAL_PASSES`），如果按原因分四条记录，产物里就会出现「同一束被拒四次」
    的假账，而实际只排定了一轮。合成一句则四组明细都在，读的人一眼能看到这一束同时踩了什么。
    """
    parts: list[str] = []
    if high_risk:
        parts.append(
            "路径 B 候选携带了高风险表面："
            + json.dumps({k: list(v) for k, v in sorted(high_risk.items())},
                         ensure_ascii=False)
            + "；路径 B 只授权**非高风险**描述性原子，数字/金额/比例/日期/期间/币种、"
              "显式否定、勾选与适用状态、法人主体身份、表格行列与合计占比关系、"
              "因果或结论一律不得由材料派生路径引入——"
              "它们在材料正文里逐字存在**不等于**已经资格化，"
              "高风险硬事实只能走路径 A 预验证权威")
    if ineligible:
        parts.append(
            "路径 B 候选的支撑边绑定了勾选表单行而叙述超出该行所问事项："
            + json.dumps({k: list(v) for k, v in sorted(ineligible.items())},
                         ensure_ascii=False)
            + "；表单行的允许用途是 `asked_item_applicability_only`——它只在**自己问的那件事**"
              "上说话，既不能凭所在父章节替宽栏目作证，选项串之后那段内容也不是给正文用的"
              "原文（`no_support_from_trailing_content`）")
    if history_only:
        parts.append(
            "路径 B 候选的支撑边全部落在**不能表达当前状态**的来源上、而候选自己没写期间："
            + json.dumps({k: list(v) for k, v in sorted(history_only.items())},
                         ensure_ascii=False)
            + "；同类材料按「较新且可核实者优先表达当前状态」——旧同类型材料可以支撑历史分期、"
              "变化与冲突核对，但**不能**支撑一条没有期间的当前态断言（`DESIGN_V2.md` O-12）。"
              "被点名候选的出路只有两条：改绑**路径 A 的预验证权威事实**（那类事实自带自己的"
              "期间文本），或撤下这条断言；不得靠给候选补一个期间词来通过——期间表面在路径 B 上"
              "本就是未授权表面")
    if unproven_current_state:
        parts.append(
            "路径 B 候选有**当前锚边**、却没有一条这样的边能核实这条命题："
            + json.dumps({k: list(v.member_refs)
                          for k, v in sorted(unproven_current_state.items())},
                         ensure_ascii=False)
            # `srsc-3`：原因码与成员身份并列写出。两种来由的**出路不同**，只写成员身份会让
            # 「材料不足」与「措辞口径变了」在拒绝详情里长得一样，模型只能二猜一。
            + "；逐条原因码 "
            + json.dumps({k: str(v.cause_code)
                          for k, v in sorted(unproven_current_state.items())},
                         ensure_ascii=False)
            + "；`srsc-2`：「支撑集里恰好含一份较新的材料」**不等于**「这条命题被较新材料核实过」。"
              "现行能判的只有**严格抽取式**一种形态——候选全文（仅空白归一后）必须是**单一份**"
              "锚边材料正文的**连续子串**，且（`srsc-3`）这次包含**不得**把命中那句源文自带的"
              "期间/范围限定截在候选之外。因此被点名候选的出路有三条：把措辞**收回到该份材料的"
              "原文**（逐字连续，不得拼接两份材料、不得换标点、不得把「已停止 / 不再 / 未」这类"
              "差异改回去；源句自带「报告期内」/ 年份等限定语时**要连它一起收回**，"
              "`source_period_scope_dropped` 说的正是这一步没做）、改绑**路径 A 的预验证权威事实**"
              "（那类事实自带自己的期间文本），或撤下这条断言。**不**接受联合材料"
              "蕴含、最长公共子串/相似度、标点镜像或否定词表这几种「证明」——它们都不是本条要求的"
              "独立支撑结论")
    return "；".join(parts)


def _directed_reproposal_note(*, bundle: "_PreGateBundle", detail: str,
                              surfaces: Mapping[str, Sequence[str]],
                              ineligible: Mapping[str, Sequence[str]] | None = None,
                              history_only: Mapping[str, Sequence[str]] | None = None,
                              unproven_current_state: Mapping[str, "UnprovenCurrentStateFinding"]
                              | None = None
                              ) -> str:
    """C3 定向重提案说明（`hrrp-5`）：逐条写清哪几条候选、踩了什么线、有哪些合法出路。

    为什么不是原来那句通用说明（「上一次输出被拒：<错误串>。请修正后重新输出完整 JSON。」）：
    那句话把**整束**被拒说成一件笼统的事，模型据此最省力的修法是「把出问题的那条删掉」——
    而那正是被裁决点名的反模式（为了通过而减少内容，且产物里看不出少了什么）。定向说明把
    三条要求同时讲明：高风险原子改绑**权威事实**（路径 A）、事实目录里没有的**撤下**或如实
    发出补件诉求、**其余候选逐条照原样保留**。后一条有审计牙齿：下一轮每条原候选的**去向**
    会被逐条记进拒绝记录（`rejection.reproposal.destinations[].destination`），因此「悄悄删掉
    无关候选」在产物里可读回。

    `hrrp-2` 多一组点名（`ineligible`）：支撑边绑定了「只在自己所问事项上说话」的勾选表单行、
    而候选文本超出了那一行的所问事项。它**不是**高风险面：那一行的材料本身留着、身份不动，
    被纠正的是这条边**最多能证明什么**。因此出路也不同——不是「改绑一条事实」，而是「把叙述
    收回该行所问事项之内，或改绑权威事实」。两组点名可能同时出现在同一条候选上：两条都列。

    `hrrp-3` 再多一组点名（`history_only`）：这条候选的支撑边**全部**落在不能表达当前状态的
    来源上，而它自己没写期间（O-12）。它与前两组仍不是同一件事——这里被纠正的是**这条边最多
    能证明到哪个期间**：旧同类型材料可以支撑历史断言，但不能支撑一条无期间的当前态断言。因此
    出路是「改绑路径 A 的预验证权威事实，或撤下」——**不含**「给它补一个期间词」：期间表面在
    路径 B 上本就是未授权表面，靠补词通过等于绕开授权面判据。

    `hrrp-4` 再多一组点名（`unproven_current_state`）：这条候选**有**当前锚边，却没有一条这样的边
    能核实这条命题（`srsc-2`）。它与 `history_only` **互斥**（一侧「一条锚边都没有」，另一侧
    「至少有一条」），因此不会同时出现在同一条候选上。被纠正的是**这条断言今天证不出来**——
    现行能判的只有**严格抽取式**一种形态：候选全文（仅空白归一后）必须是**单一一**份锚边材料
    正文的**连续子串**。出路因此比前三组多一条：**把措辞收回到该份材料的原文**——但仍不得拼接
    两份材料、不得换标点、不得把「已停止 / 不再 / 未」这类差异改回去。联合材料蕴含、最长公共
    子串或相似度、标点镜像、否定词表**都不是**本条要求的独立支撑结论，逐条在说明里点名排除。

    `hrrp-5`：这一组点名**逐条带上原因码**（`srsc-3`）。理由是这一组的两种来由**出路不同**，
    而它们在上一版说明里长得一样：
      * `not_extractive_in_any_current_source`——压根没有锚边材料包含它。出路只有「收回原文」
        （那条原文得**真有**这段话）、改绑路径 A、或撤下；
      * `source_period_scope_dropped`——**有**材料包含它，只是命中那句源文自带期间/范围限定
        （`报告期内…` / `2024年…`），而候选把限定语截掉了。这条路**不需要**改绑：把限定语
        **一起收进候选**（整句连续包含）即可。说明里必须把这条单独讲出来，否则模型最省力的
        读法是「按 4b 把它删掉」，而那会把一条**本来写得出、也核实得了**的句子丢掉。
    两处都保留一个共同底线：期间词只能**来自原文**，不得凭空补——凭空补出来的期间同样是
    路径 B 上的未授权表面。

    说明文本只描述**规则与出路**，不含任何新事实、不替模型做选择、也不放宽任何门：本函数
    返回的字符串逐字追加在完整原请求之后，走的是与返修/栏目定向**同一条**通道。
    """
    ineligible = ineligible or {}
    history_only = history_only or {}
    unproven_current_state = unproven_current_state or {}
    lines = [
        "",
        "",
        f"【定向重提案（{HIGH_RISK_REPROPOSAL_NOTE_VERSION}）】上一次输出被拒：{detail}",
        "",
        "被点名的候选如下（按你上一次输出的原序，文本逐字引用）：",
    ]
    for index, candidate in enumerate(bundle.candidates, start=1):
        cid = str(candidate.candidate_id)
        own = tuple(str(s) for s in (surfaces.get(cid) or ()))
        if own:
            lines.append(f"  - 第 {index} 条「{candidate.claim_text}」携带高风险表面 {list(own)}"
                         f"（candidate_id={cid}）")
        members = tuple(str(m) for m in (ineligible.get(cid) or ()))
        if members:
            lines.append(f"  - 第 {index} 条「{candidate.claim_text}」的支撑边绑定了只在"
                         f"**自己所问事项**上说话的勾选表单行 {list(members)}，"
                         f"而这条候选的文本超出了那一行的所问事项（candidate_id={cid}）")
        history_members = tuple(str(m) for m in (history_only.get(cid) or ()))
        if history_members:
            lines.append(f"  - 第 {index} 条「{candidate.claim_text}」的支撑边**全部**落在"
                         f"不能表达当前状态的来源上 {list(history_members)}"
                         f"（同类较旧，或同类而期间不可核实），而这条候选没有写期间"
                         f"（candidate_id={cid}）")
        finding = unproven_current_state.get(cid)
        if finding is not None:
            unproven_members = tuple(str(m) for m in finding.member_refs)
            if str(finding.cause_code) == "source_period_scope_dropped":
                lines.append(
                    f"  - 第 {index} 条「{candidate.claim_text}」有当前锚边 "
                    f"{list(unproven_members)}，其中**确实**包含这条断言——但包含它的那一句"
                    f"**自己带着期间/范围限定**（例如「报告期内…」或某个年份），而这条候选把"
                    f"限定语截掉了，于是读者只能读成**持续至今的当前状态**。"
                    f"该断言按现在的措辞证不出来（candidate_id={cid}，"
                    f"原因码 `source_period_scope_dropped`）")
            else:
                lines.append(
                    f"  - 第 {index} 条「{candidate.claim_text}」有当前锚边 "
                    f"{list(unproven_members)}，但没有一条这样的边能核实这条命题——"
                    f"该断言不是任何一份锚边材料正文的**连续子串**"
                    f"（candidate_id={cid}，原因码 `{finding.cause_code}`）")
    lines.extend([
        "",
        "路径 B（材料派生）只授权**非高风险**描述性原子。这些成分**逐字出现在材料正文里不等于"
        "已经资格化**：数字/金额/比例/日期/期间/币种、显式否定、勾选与适用状态、法人主体身份、"
        "表格行列与合计占比关系、因果或结论，只能由路径 A 的预验证权威事实授权。",
        "",
        "勾选表单行的允许用途是 `asked_item_applicability_only`：它只在**自己问的那件事**上说"
        "话，既不能凭所在父章节替宽栏目（收入构成、主营业务、成立日期等）作证，选项串之后"
        "那段内容也**不是**给正文用的原文——它是被问事项的内容。",
        "",
        "请**重新输出一份完整的提案集**（不是节选、不是只改那几条）：",
        "  1. 仍然需要这些高风险原子的：把该候选的支撑边改为绑定**本请求事实目录里的权威事实行**"
        "（路径 A）；",
        "  2. 事实目录里**没有**对应权威事实的：撤下该候选，不要在材料派生路径上表达它；若它是"
        "Contract 必需内容，仍按既有通道如实发出 `follow_up_needs`，不要用材料派生绕过；",
        "  3. 支撑边绑定了勾选表单行的：把这条候选的叙述**逐字收进该行所问事项之内**，或改绑"
        "事实目录里的权威事实行（路径 A）；两者都做不到就撤下它，并如实发出补件诉求；",
        "  4. 支撑边**全部**落在同类较旧（或期间不可核实）来源上的：改绑事实目录里的权威事实行"
        "（路径 A）——那类事实自带自己的期间；事实目录里没有对应行的，撤下这条断言。**不要**"
        "给它补一个年份或期间词：期间表面在材料派生路径上同样是未授权表面，补词解决不了这条边"
        "能证明到哪个期间；",
        "  4b. **有当前锚边但证不出来的**：现行只能判「严格抽取式」——把这条候选的措辞**逐字收回到"
        "该份材料的原文**（整句必须是那一段的连续子串，仅空白可不同），或改绑事实目录里的权威"
        "事实行（路径 A）；两条都做不到就撤下它。**不要**靠拼接两份材料、换标点、或靠「大部分"
        "相同」来通过，也**不要**顺手把它写成否定或反义：那几种都不是本条要求的独立支撑结论；"
        "上面标了原因码 `source_period_scope_dropped` 的那几条**另有一条出路**：包含它的那一句"
        "**自己带着期间/范围限定**（「报告期内…」或某个年份），你只是把限定语截掉了——"
        "**把限定语一起收进候选**（整句连续包含）就成立，**不必**改绑、更**不必**删掉这条"
        "本来写得出的句子。限定语必须**逐字来自原文**，**不要**给它现编一个年份或期间词；",
        "  5. 其余候选与叙述单元**逐条照原样保留**：文本逐字不变、支撑边绑定对象不变。",
        "",
        "不得为了通过而删除无关候选，不得把未取得的原子写成已取得，不得改变任何其它支撑边的"
        "绑定对象，也不得删减补件诉求。下一轮会逐条核对每条原候选的去向（保留 / 未再表达）。",
    ])
    return "\n".join(lines)


def _batch_shape_hint(error: str) -> list[str]:
    """按错误原文给出**这一批**该怎么改（提示，不是判定，也不放宽任何门）。

    只认解析器自己那两种口径的错误串；认不出就给通用条目——错误原文已经逐字附在说明里，
    因此认不出不会让模型少知道任何事实，只是少一条**更省力**的修法提示。
    """
    if "必须是 primary" in error:
        return [
            "这一处是「候选的第一条支撑边必须是 primary」。修法只有两种，都是**改标注或撤下**，"
            "不是编一个身份：",
            "  1. 把该候选**真正承重**的那一条支撑边标成 `\"support_role\": \"primary\"`"
            "（同一条**已声明**的行可以标 primary，其余边照旧 `corroborating`）；",
            "  2. 这条候选本来就不承重的：**撤下**它，不要在它上面凑一条 primary。",
            "不得把所有 `corroborating` 一律改写成 `primary`（那会让每条边都声称承重，"
            "把「哪一条承重」这个信息抹掉），也不得引用**没有声明过**的行来当 primary。",
        ]
    if "不是合法 JSON" in error:
        return [
            "这一处是「返回不是合法 JSON」。最常见的原因是**字符串里出现了未转义的换行或"
            "制表符**——JSON 字符串内部必须写成 `\\n` / `\\t`；其次是引号或反斜杠没有转义。",
            "请只输出**严格 JSON**（不要代码块围栏、不要注释、不要在 JSON 前后写说明文字），"
            "字段与取值仍按本请求的 `output_schema`。",
        ]
    return [
        "请按本请求 `output_schema` 的字段与取值重出本批 JSON：把结构不合法的那一处改成合法"
        "形状，不得靠省略字段、减少内容或改写事实来绕过。",
    ]


def _batch_shape_correction_note(*, error: str, batch_label: str, batch_id: str,
                                 aspect_ids: Sequence[str],
                                 declared_fact_refs: int,
                                 declared_material_refs: int) -> str:
    """C4 批次形状纠正说明（`bsc-1`）：只对**失败的那一批**反馈它自己的准确错误。

    与 `_directed_reproposal_note`（`hrrp-2`）的分工：那一条处理的是**整束**被拒（路径 B
    的高风险面 / 支撑资格），它需要逐候选点名，因此整轮每一批都该看到同一段定向说明；
    这一条处理的是**某一批自己的返回**形状不合法——它只对那一批有意义，也只发给那一批。

    说明里**不写**任何新事实、不替模型做选择、不放宽任何门，并且明确给出两条合法出路
    （改标注 / 撤下这条候选），因此它不构成「要求模型编一个 primary」：声明了哪些行、一共
    多少行，逐字写在说明里；已声明的行不够用时的出路是**少写候选**，不是编造身份。
    """
    facts_clause = (f"权威事实行 {declared_fact_refs} 条（短别名 `f1..`）"
                    if declared_fact_refs else
                    "权威事实行 **0 条**（本批没有任何事实行，路径 A 无从声明）")
    lines = [
        "",
        "",
        f"【批次形状纠正（{BATCH_SHAPE_CORRECTION_NOTE_VERSION}）】本批"
        f"（{batch_label}，batch_id={batch_id}，aspects={list(aspect_ids)}）"
        f"上一次输出被拒：{error}",
        "",
        f"请只重出**本批**这一份完整 JSON（本批负责的 aspect 就是上面这几个）。"
        "其余批次的返回**不受影响**，也不需要你替它们再写一遍。",
        "",
        "候选编号 `c1..` 只在**本批**内有效：它们指本批自己上一次输出的那几条候选。"
        "不要按别的批次的编号改，也不要引用本批没有写过的候选。",
        "",
        f"本批请求里**已声明**的支撑选项：{facts_clause}、"
        f"精确材料行 {declared_material_refs} 条（短别名 `m1..`）。"
        "支撑边只能引用这"
        f"{declared_fact_refs + declared_material_refs} 行里的行——"
        "不得写没有声明过的行，也不得自己编一个身份。",
        "",
    ]
    lines.extend(_batch_shape_hint(error))
    lines.extend([
        "",
        "只修正上面这一处；本批其余**已经正确**的内容逐字保留。若某条候选在已声明的选项里"
        "根本没有可承重的一条边，就**不要写这条候选**（若它是 Contract 必需内容，仍按既有通道"
        "如实发出 `follow_up_needs`）——本批可以少写候选，但不得为了通过而编造身份，"
        "也不得把不合格的边硬标成 primary。",
    ])
    return "\n".join(lines)


def _rejection_reproposal_trace(*, previous: "_PreGateBundle", previous_attempt: int,
                                next_bundle: "_PreGateBundle", next_attempt: int,
                                note_version: str) -> RejectionReproposalTrace:
    """C3：把「原束每条候选在**下一修订**里的去向」机械地记下来（判据见该类 docstring）。

    连接键**只有**逐字 `claim_text`：`candidate_id` 把 `draft_revision` 计入身份，跨修订
    不可比。因此这里**不**推测「哪一条被改写了」——只回答三件事：下一修订里有没有同文候选、
    有几条、类型是否相同。这三件事全部可复算，不需要任何相似度阈值。
    """
    by_text: dict[str, list[NS.ClaimCandidate]] = {}
    for candidate in next_bundle.candidates:
        by_text.setdefault(str(candidate.claim_text), []).append(candidate)
    destinations: list[CandidateReproposalDestination] = []
    for candidate in previous.candidates:
        matches = tuple(by_text.get(str(candidate.claim_text)) or ())
        next_ids = tuple(str(m.candidate_id) for m in matches)
        next_types = tuple(str(m.fact_type) for m in matches)
        if not matches:
            destination = "not_reexpressed"
        elif len(matches) > 1:
            destination = "carried_ambiguous"
        elif str(matches[0].fact_type) == str(candidate.fact_type):
            destination = "carried_verbatim"
        else:
            destination = "carried_rebound"
        destinations.append(CandidateReproposalDestination(
            candidate_id=str(candidate.candidate_id), claim_text=str(candidate.claim_text),
            fact_type=str(candidate.fact_type), destination=destination,
            next_candidate_ids=next_ids, next_fact_types=next_types))
    return RejectionReproposalTrace(
        from_attempt=previous_attempt, to_attempt=next_attempt,
        note_version=str(note_version),
        original_candidate_ids=tuple(str(c.candidate_id) for c in previous.candidates),
        next_candidate_ids=tuple(str(c.candidate_id) for c in next_bundle.candidates),
        destinations=tuple(destinations))


def carve_out_candidate_subset(*, plan: Mapping[str, Any], bundle: "_PreGateBundle",
                               high_risk: Mapping[str, Sequence[str]],
                               ineligible: Mapping[str, Sequence[str]],
                               history_only: Mapping[str, Sequence[str]] | None = None,
                               unproven_current_state: Mapping[str, "UnprovenCurrentStateFinding"]
                               | None = None,
                               blocking_context_units: Mapping[str, Sequence[str]] | None = None,
                               manifest: NS.WriterMaterialManifest | None = None,
                               from_attempt: int, to_attempt: int,
                               revision_for_plan: Callable[[Mapping[str, Any]], str]
                               ) -> tuple[dict, CandidateCarveOutDecision] | None:
    """§一.2：把原束里**不合格的那几条候选逐条拒掉**，其余**逐字照原样**继续走链。

    纯函数：不调模型、不改一个字的候选文本与支撑边、不新增调用。返回
    `(裁出后的完整提案集, 裁决)`；**无可裁**（没有任何候选被点名，或没有一条幸存）时返回
    `None`——那时调用方走既有 C3 定向重提案 / 整束 fail-closed，一个字不改。

    为什么幸存候选的 spec **逐字复用**而不是「重新拼一份」：重新拼就等于在这一步引入了第二个
    候选构造器，而它与模型输出之间任何一处细微差异都会让「原样保留」变成一句空话。裁出只做
    **一件事**——从有序列表里去掉被点名的那几项；候选文本、支撑边、narrative 单元、补件诉求
    全部原对象引用。

    `revision_for_plan`（`cco-4` / `pw-15`）：**裁出后的**提案集 → 新修订的推导入口。它不再是
    一个算好的字符串，因为草稿层进了修订：裁出会去掉草稿单元的原子、甚至整段撤下草稿单元，
    因此新修订只能在裁出**做完之后**才算得出来。传一个待算好的修订进来会得到「裁决声明的
    `to_revision` 与那一轮实际的 `draft_revision` 不是同一份」，而两者相等正是跨修订对账
    （`CandidateCarveOutTrace` / `decision.to_revision`）成立的前提。

    边界：`high_risk` / `ineligible` / `history_only` / `unproven_current_state` 里的键必须是本束
    **确实存在**的候选身份。
    出现未知键说明上游判据与束的身份对不上，那是缺陷而不是数据——当场抛错，不得「顺手多删一条」。

    `blocking_context_units`（`cco-6`）是**第二条被裁对象轴**：键是 `draft_unit_id`（门前束
    `bundle.units` 的真实身份，也就是门规则点名的那个坐标），值是点名它的门规则 id。它与上面
    四条**候选轴**刻意分开传：候选轴的原因码取自 `CANDIDATE_CARVE_OUT_REASONS`，这一轴取自
    `CONTEXT_UNIT_CARVE_OUT_REASONS`（单元素、就是那条门规则 id）。理由见
    `CONTEXT_UNIT_CARVE_OUT_REASONS`：两类对象被撤下的**法律后果**不同，混成一张表会让
    「这条原因该出现在哪一侧」变成约定。

    `manifest`（`cco-6`）：被撤单元的**来源**必须按本模块唯一的材料身份口径记录——即
    `(container_id, material_id) → manifest_member_ref`，且必须**真的在本次精确材料清单里**
    （`_resolve_material_declaration`）。不记提案集里那对裸 ID：本模块到处都拒绝把裸 material ID
    当身份读（「材料必须来自本次权威输入，不得自报」），审计面开一个例外就是在产物里留下一个
    从未被确认过的坐标。缺 manifest 时（单元轴没开火）不解析、也不产生任何 refs。

    本轴只做一件事——把被点名的那几个 `narrative_draft_units` spec 从提案集里**逐条删除**
    （连同它们自己的 `context_support`：支撑边由 `_build_pre_gate_bundle` 从 spec 派生，spec 没了
    边自然没了，因此不存在「单元撤下了、它的 context 支撑提案还在」这种半截状态）。原文、来源、
    原因、命中表面一并写进裁决留档。

    本轮裁出的三条判据是**三条正交的轴**（候选自己的表面 / 这条边的适用范围 / 这条边能证明到
    哪个期间），同一条候选可以同时被两条点名：`reasons` 是**列表**而不是单值，逐条列出它自己
    踩的全部线。被点名者一律**排除**：这三条轴上的合法出路都可能是「改绑权威事实」或「撤下」，
    而裁出这一步**不改写任何候选文本**，因此它只能执行「撤下」那一侧——「改绑」由定向重提案
    那一轮去问（两条出口互斥，见 `ProposalSetRejectionRecord.carve_out`）。
    """
    previous = tuple(bundle.candidates)
    specs = tuple(plan["claim_candidates"])
    if len(previous) != len(specs):
        raise PackWriterError(
            f"提案集与门前束的候选数不一致（plan={len(specs)} bundle={len(previous)}）："
            "裁出按下标对齐，两者不同源时不得继续")
    history_only = history_only or {}
    unproven_current_state = unproven_current_state or {}
    named = ({str(k) for k in high_risk} | {str(k) for k in ineligible}
             | {str(k) for k in history_only} | {str(k) for k in unproven_current_state})
    known = {str(c.candidate_id) for c in previous}
    unknown = sorted(named - known)
    if unknown:
        raise PackWriterError(f"判据点名了本束不存在的候选身份 {unknown}")
    excluded: list[CandidateCarveOutDestination] = []
    survivors: list[int] = []
    for index, candidate in enumerate(previous):
        cid = str(candidate.candidate_id)
        surfaces = tuple(str(s) for s in (high_risk.get(cid) or ()))
        members = tuple(str(m) for m in (ineligible.get(cid) or ()))
        history_members = tuple(str(m) for m in (history_only.get(cid) or ()))
        unproven_finding = unproven_current_state.get(cid)
        unproven_members = (tuple(str(m) for m in unproven_finding.member_refs)
                            if unproven_finding is not None else ())
        reasons: list[str] = []
        if surfaces:
            reasons.append("path_b_high_risk_surface")
        if members:
            reasons.append("path_b_ineligible_material_scope")
        if history_members:
            reasons.append("path_b_history_only_current_state")
        if unproven_members:
            reasons.append("path_b_unproven_current_state")
        if not reasons:
            survivors.append(index)
            continue
        excluded.append(CandidateCarveOutDestination(
            candidate_id=cid, claim_text=str(candidate.claim_text),
            reasons=tuple(reasons), surfaces=surfaces,
            ineligible_member_refs=members,
            history_only_member_refs=history_members,
            unproven_current_state_member_refs=unproven_members,
            unproven_current_state_cause_code=(
                str(unproven_finding.cause_code) if unproven_finding is not None else "")))
    # ---- `cco-6`：context 单元那一轴（与候选轴正交，见 docstring） ----
    # 变量名与下面 `cco-4` 的草稿层刻意分开：那一层也有一个 `previous_units`
    # （`bundle.prose_units`），两者是**不同的身份**（草稿层承载事实原子、context 单元承载
    # 衔接），共用名字会让裁决里的「原束单元身份」在无人在意处被换成另一张表。
    context_specs = list(plan.get("narrative_draft_units") or ())
    previous_context_units = tuple(bundle.units)
    if len(previous_context_units) != len(context_specs):
        raise PackWriterError(
            f"裁出对账失败：门前束的 context 单元数 {len(previous_context_units)} 与提案集的"
            f"单元规格数 {len(context_specs)} 不一致（逐单元删除按下标对齐，两者不同源时不得继续）")
    named_units = {str(k): tuple(str(r) for r in v)
                   for k, v in (blocking_context_units or {}).items()}
    known_units = {str(u.draft_unit_id) for u in previous_context_units}
    unknown_units = sorted(set(named_units) - known_units)
    if unknown_units:
        raise PackWriterError(f"判据点名了本束不存在的 context 单元 {unknown_units}")
    excluded_units: list[ContextUnitCarveOutDestination] = []
    kept_units: list[dict] = []
    for position, unit in enumerate(previous_context_units):
        unit_id = str(unit.draft_unit_id)
        rule_ids = named_units.get(unit_id, ())
        if not rule_ids:
            kept_units.append(context_specs[position])
            continue
        unknown_rules = [r for r in rule_ids if r not in CONTEXT_UNIT_CARVE_OUT_REASONS]
        if unknown_rules:
            raise PackWriterError(
                f"context 单元 {unit_id!r} 被点名的原因 {unknown_rules} 不在封闭词表 "
                f"{list(CONTEXT_UNIT_CARVE_OUT_REASONS)} 内：本出口不得替别的判据做决定"
                "（别的门规则问的是「这条断言能不能承重」，不是「这段衔接文字该不该出现」）")
        # 判据与文本的**当场**互核：点名的原因必须在这段文字里有逐字表面。对不上就是取数面的
        # 缺陷，不得写成一条「撤下了、但不知道为什么」的记录。
        hits = NS.vague_period_hits(str(unit.text))
        if not hits:
            raise PackWriterError(
                f"context 单元 {unit_id!r} 被 {list(rule_ids)} 点名，但这段文字里没有任何命中的"
                "逐字表面：撤下的原因必须在这段原文里读得出来")
        spec = context_specs[position]
        refs: list[str] = []
        for edge in (spec.get("context_support") or ()):
            if manifest is None:
                raise PackWriterError(
                    f"context 单元 {unit_id!r} 要撤下，但没有本次写作的精确材料清单可供回查来源："
                    "被撤单元的来源必须逐条可读，缺一条就不得撤下")
            member = _resolve_material_declaration(
                edge, manifest, what=f"被撤下的 context 单元 {unit_id!r} 的 context 边")
            refs.append(str(member.member_ref))
        excluded_units.append(ContextUnitCarveOutDestination(
            draft_unit_id=unit_id, unit_key=str(spec.get("unit_key") or ""),
            unit_kind=str(unit.unit_kind), text=str(unit.text),
            context_member_refs=tuple(refs),
            reasons=tuple(rule_ids), hit_phrases=tuple(hits)))
    if not survivors:
        return None
    # 两条轴**各自**都有话可说时才算裁出：候选一条没排除、单元也一条没撤下 ⇒ 这一束字面上
    # 就是「一条都不该拒」，应走既有判定（与 `cco-1` 的边界一字不变）。
    if not excluded and not excluded_units:
        return None
    carved = dict(plan)
    carved["claim_candidates"] = [specs[i] for i in survivors]
    # 只在**真有**单元被撤下时才换列表：没有触到那一轴时保持原对象引用（`is` 级不变），
    # 否则每一份候选裁出的提案集都会多一份「逐字相同但换了对象」的单元列表，
    # 「这一轴在场但没开火」与「这一轴不在场」就又分不清了。幸存单元一律**逐对象复用**。
    if excluded_units:
        carved["narrative_draft_units"] = kept_units
    # `cco-4` / `pw-15`：裁出**同时**改动草稿层。草稿单元是「从材料写出来的那段话」，被裁候选
    # 只说明**它那几条原子不得保留**，不说明那段文字必须消失——因此：
    #   * 部分原子被裁 ⇒ 该单元仍在，原子账里去掉被裁的那几条（文本逐字保留，由门后组织器
    #     按已接受的原子决定改写还是删除；最终句子保真核对是那里的兜底）；
    #   * 全部原子被裁 ⇒ 该单元整体撤下（草稿层不允许没有原子的单元），原文逐字写进裁决。
    # 两种情形都逐单元留痕：被撤下的那句话**不得无声消失**。
    prose_specs = list(plan.get("natural_prose_draft") or ())
    previous_units = tuple(bundle.prose_units)
    if len(previous_units) != len(prose_specs):
        raise PackWriterError(
            f"裁出对账失败：门前束的草稿单元数 {len(previous_units)} 与提案集的草稿规格数 "
            f"{len(prose_specs)} 不一致（逐单元去向按下标对齐，两者不同源时不得继续）")
    excluded_index = set(range(len(previous))) - set(survivors)
    excluded_keys = {str(specs[i]["candidate_key"]) for i in excluded_index}
    key_to_id = {str(specs[i]["candidate_key"]): str(previous[i].candidate_id)
                 for i in range(len(specs))}
    kept_prose: list[dict] = []
    prose_destinations: list[CandidateCarveOutProseDestination] = []
    for position, spec in enumerate(prose_specs):
        kept_atoms = [str(k) for k in spec["atom_candidate_keys"] if str(k) not in excluded_keys]
        dropped_atoms = [str(k) for k in spec["atom_candidate_keys"] if str(k) in excluded_keys]
        if not dropped_atoms:
            kept_prose.append(spec)
            continue
        unit = previous_units[position]
        prose_destinations.append(CandidateCarveOutProseDestination(
            prose_unit_id=str(unit.prose_unit_id), prose_text=str(spec["text"]),
            source_member_refs=tuple(str(r) for r in spec["source_member_refs"]),
            source_fact_refs=tuple(str(r) for r in (spec.get("source_fact_refs") or ())),
            dropped_atom_candidate_ids=tuple(key_to_id[key] for key in dropped_atoms),
            remaining_atom_count=len(kept_atoms)))
        if not kept_atoms:
            continue
        kept_spec = dict(spec)
        kept_spec["atom_candidate_keys"] = kept_atoms
        kept_prose.append(kept_spec)
    carved["natural_prose_draft"] = kept_prose
    decision = CandidateCarveOutDecision(
        version=CANDIDATE_CARVE_OUT_VERSION, from_attempt=int(from_attempt),
        to_attempt=int(to_attempt), to_revision=str(revision_for_plan(carved)),
        source_candidate_ids=tuple(str(c.candidate_id) for c in previous),
        excluded=tuple(excluded),
        prose_destinations=tuple(prose_destinations),
        # 单元那一轴**整条在场或整条缺席**（`__post_init__` 强制）：这一轴只在真有单元被撤下时
        # 写。写成「总是带上完整身份」会让每一份候选裁出的裁决都多一条空的单元轴，读者得先分辨
        # 「这一轴在场但没开火」与「这一轴不在场」——那正是本轴不该引入的歧义。
        source_draft_unit_ids=(tuple(str(u.draft_unit_id) for u in previous_context_units)
                               if excluded_units else ()),
        excluded_context_units=tuple(excluded_units))
    return carved, decision


def _carve_out_trace(*, decision: CandidateCarveOutDecision,
                     previous_bundle: "_PreGateBundle", bundle: "_PreGateBundle"
                     ) -> CandidateCarveOutTrace:
    """裁出裁决 + 新修订真的产出之后，逐条回填「原身份 → 新身份」（唯一定位判据：顺序 + 逐字文本）。

    为什么用**顺序 + 逐字 `claim_text`** 双判据而不是 `claim_text` 单判据（`RejectionReproposalTrace`
    用的是单判据）：裁出**没有**重出这一步，新修订的候选次序就是原束次序去掉被排除项，因此
    「同序同文」是**可以**要求的；而单判据允许「同文候选在别的位置上」，那对裁出来说意味着
    「有候选没被逐字带过来」，正是要挡住的那件事。任一条对不上即 fail-closed——那时读到的不是
    「裁出没成功」，而是「这条链的某处把候选换了」。
    """
    previous = tuple(previous_bundle.candidates)
    next_candidates = tuple(bundle.candidates)
    expected = decision.surviving_candidate_ids
    if len(next_candidates) != len(expected):
        raise PackWriterError(
            f"裁出后的新修订候选数 {len(next_candidates)} 与裁决的幸存数 {len(expected)} 不符"
            "（裁出只做删除，不重写任何候选）")
    next_ids: dict[str, str] = {}
    for position, candidate in enumerate(next_candidates):
        original_id = expected[position]
        previous_text = next(
            str(c.claim_text) for c in previous if str(c.candidate_id) == original_id)
        if str(candidate.claim_text) != previous_text:
            raise PackWriterError(
                f"裁出后的新修订第 {position + 1} 条候选文本与原束 {original_id!r} 不一致："
                "裁出不得改写任何候选")
        next_ids[original_id] = str(candidate.candidate_id)
    by_id = {e.candidate_id: e for e in decision.excluded}
    destinations: list[CandidateCarveOutDestination] = []
    for candidate in previous:
        cid = str(candidate.candidate_id)
        entry = by_id.get(cid)
        if entry is not None:
            destinations.append(entry)
            continue
        destinations.append(CandidateCarveOutDestination(
            candidate_id=cid, claim_text=str(candidate.claim_text),
            next_candidate_id=next_ids[cid]))
    # `cco-4`：草稿层也必须是原束的**逐字删除子集**。候选那一侧靠「顺序 + 逐字文本」证明，
    # 草稿这一侧靠「文本与出处逐字不变 + 原子按幸存候选的次序一一对应」证明。任一条对不上即
    # fail-closed：那时读到的不是「裁出没成功」，而是「某处把这段文字换过了」。
    by_unit = {d.prose_unit_id: d for d in decision.prose_destinations}
    expected_units = [u for u in previous_bundle.prose_units
                      if str(u.prose_unit_id) not in
                      {d.prose_unit_id for d in decision.prose_destinations if d.dropped}]
    next_units = tuple(bundle.prose_units)
    if len(next_units) != len(expected_units):
        raise PackWriterError(
            f"裁出后的草稿单元数 {len(next_units)} 与「原束减去被撤下的单元」"
            f"（{len(expected_units)}）不符：草稿层只做删除，不重写、不新增")
    for position, (before, after) in enumerate(zip(expected_units, next_units)):
        if str(after.text) != str(before.text) or \
                tuple(after.source_member_refs) != tuple(before.source_member_refs) or \
                tuple(after.source_fact_refs) != tuple(before.source_fact_refs):
            raise PackWriterError(
                f"裁出后的第 {position + 1} 段草稿与原束单元 {before.prose_unit_id!r} 文本或"
                "出处不一致：裁出不得改写草稿（只允许去掉被裁候选的那几条原子）。"
                "出处两条轴一起比（`pprov-1`）：少了事实轴，一段材料出处的散文与一段事实出处的"
                "散文只要正文逐字相同就会被判成同一段")
        want = [next_ids[oid] for oid in before.atom_candidate_ids if oid in next_ids]
        if [str(cid) for cid in after.atom_candidate_ids] != want:
            raise PackWriterError(
                f"裁出后的第 {position + 1} 段草稿的原子账与「原束原子减去被裁候选」不逐位对应"
                f"（原束 {list(before.atom_candidate_ids)} → 应为 {want}，"
                f"实测 {list(after.atom_candidate_ids)}）")
        destination = by_unit.get(str(before.prose_unit_id))
        declared = (destination.remaining_atom_count if destination is not None
                    else len(before.atom_candidate_ids))
        if declared != len(want):
            raise PackWriterError(
                f"草稿单元 {before.prose_unit_id!r} 的去向表声明还剩 {declared} 条原子，"
                f"实际为 {len(want)} 条：去向表与新修订必须对得上")
    # `cco-6`：context 单元那一侧的同一份对账。与草稿层那一段同一条纪律——新修订的单元必须是
    # 原束的**逐字删除子集**（文本逐字不变、次序不变），被撤下的那几段**不得**出现在新修订里。
    # 只在裁决真的动过单元那一轴时执行：候选裁出不动单元，此时多跑一遍只会让「没查」与
    # 「查了、确实一致」在产物里长得一样（两者都由 `next_draft_unit_ids` 的空值表达）。
    context_destinations: tuple[ContextUnitCarveOutDestination, ...] = ()
    next_draft_unit_ids: tuple[str, ...] = ()
    if decision.excluded_context_units:
        previous_units = tuple(previous_bundle.units)
        next_units_all = tuple(bundle.units)
        excluded_unit_ids = {u.draft_unit_id for u in decision.excluded_context_units}
        expected_units = [u for u in previous_units
                          if str(u.draft_unit_id) not in excluded_unit_ids]
        if len(next_units_all) != len(expected_units):
            raise PackWriterError(
                f"裁出后的 context 单元数 {len(next_units_all)} 与「原束减去被撤下的单元」"
                f"（{len(expected_units)}）不符：单元那一轴只做删除，不重写、不新增")
        for position, (before, after) in enumerate(zip(expected_units, next_units_all)):
            if str(after.text) != str(before.text) or \
                    str(after.unit_kind) != str(before.unit_kind):
                raise PackWriterError(
                    f"裁出后的第 {position + 1} 段 context 单元与原束单元 "
                    f"{before.draft_unit_id!r} 文本或类型不一致：裁出不得改写衔接文字")
        next_unit_by_old = {str(before.draft_unit_id): str(after.draft_unit_id)
                            for before, after in zip(expected_units, next_units_all)}
        by_unit = {u.draft_unit_id: u for u in decision.excluded_context_units}
        for unit in previous_units:
            unit_id = str(unit.draft_unit_id)
            entry = by_unit.get(unit_id)
            if entry is not None:
                context_destinations += (entry,)
                continue
            context_destinations += (ContextUnitCarveOutDestination(
                draft_unit_id=unit_id, unit_key="", unit_kind=str(unit.unit_kind),
                text=str(unit.text), context_member_refs=(), hit_phrases=(),
                next_draft_unit_id=next_unit_by_old[unit_id]),)
        next_draft_unit_ids = tuple(str(u.draft_unit_id) for u in next_units_all)
    return CandidateCarveOutTrace(
        decision=decision, destinations=tuple(destinations),
        next_candidate_ids=tuple(str(c.candidate_id) for c in next_candidates),
        model_calls_added=0,
        context_unit_destinations=tuple(context_destinations),
        next_draft_unit_ids=next_draft_unit_ids)


@dataclass(frozen=True)
class _PreGateBundle:
    """一次 LLM 输出构造出的**门前**束（自然草稿 / 候选 / 门前叙述单元 / proposal / 补件申请）。

    `prose_units` 是 `pw-15` 的草稿层：它**先于**候选被写出来（模型侧的写作顺序），在写入侧则
    与候选**同一修订**一起成形。空元组 = 本节没有草稿层，这只在**显式声明的历史线**
    （`WriterPolicy.proposal_wire` = `proposals-10`，仅限离线 stand-in）下可达；当前线下
    「有候选、无草稿」在构造这一束**之前**就被 `assert_natural_draft_covers_candidates` 拒了，
    因此这里拿到空草稿层等于「本节一条候选都没有」。
    """

    candidates: tuple[NS.ClaimCandidate, ...]
    units: tuple[NS.NarrativeDraftUnit, ...]
    proposals: tuple[NS.ProposedSupportRef, ...]
    follow_up_specs: tuple[dict, ...]
    prose_units: tuple[NS.NaturalProseDraftUnit, ...] = ()


def _build_pre_gate_bundle(*, plan: Mapping[str, Any], task: PS.SectionTask, authority: Any,
                           scan: AuthorityScan, policy: WriterPolicy, draft_revision: str,
                           manifest: NS.WriterMaterialManifest,
                           dependency_fingerprint: str) -> _PreGateBundle:
    table = _authority_fact_table(scan.facts)
    candidates: list[NS.ClaimCandidate] = []
    proposals: list[NS.ProposedSupportRef] = []
    by_key: dict[str, NS.ClaimCandidate] = {}
    for spec in plan["claim_candidates"]:
        label = str(spec["candidate_key"])
        if label in by_key:
            # 候选键是草稿原子映射的**唯一**把手：重复键会让「这一段表达哪些原子」变成
            # 歧义的（同一把手指向两条候选）。合并层保证键唯一，这里是它的 fail-closed 复核。
            raise PackWriterError(
                f"候选键 {label!r} 在本束内重复：草稿的原子映射将无法唯一指认")
        candidate = NS.ClaimCandidate.create(
            draft_revision=draft_revision, task_id=task.task_id, section_id=task.section_id,
            company_id=authority.company_id, report_as_of=authority.report_as_of,
            contract_version=authority.contract_version,
            contract_fingerprint=authority.contract_fingerprint, claim_text=spec["claim_text"],
            fact_type=_candidate_fact_type(spec, label, table))
        for edge in spec["support"]:
            proposals.append(_factual_proposal(
                edge, candidate=candidate, manifest=manifest, table=table, label=label,
                dependency_fingerprint=dependency_fingerprint))
        candidates.append(candidate)
        by_key[label] = candidate
    units: list[NS.NarrativeDraftUnit] = []
    for index, spec in enumerate(plan["narrative_draft_units"]):
        unit = NS.NarrativeDraftUnit.create(
            draft_revision=draft_revision, section_id=task.section_id, index=index,
            unit_kind=spec["unit_kind"], text=spec["text"])
        for edge in spec["context_support"]:
            proposals.append(_context_proposal(
                edge, unit=unit, manifest=manifest, label=str(spec["unit_key"]),
                dependency_fingerprint=dependency_fingerprint))
        units.append(unit)
    prose_units = _build_natural_prose_units(
        specs=plan["natural_prose_draft"], by_key=by_key, draft_revision=draft_revision,
        section_id=task.section_id, table=table)
    return _PreGateBundle(candidates=tuple(candidates), units=tuple(units),
                          proposals=tuple(proposals),
                          follow_up_specs=tuple(plan["follow_up_needs"]),
                          prose_units=prose_units)


def _build_natural_prose_units(*, specs: Sequence[Mapping[str, Any]],
                               by_key: Mapping[str, NS.ClaimCandidate], draft_revision: str,
                               section_id: str,
                               table: Mapping[tuple[str, str, str], AuthorityFactEntry]
                               ) -> tuple[NS.NaturalProseDraftUnit, ...]:
    """合并后的草稿规格 → `NaturalProseDraftUnit`（**原子键 → 候选身份**的唯一展开点）。

    键 → 身份的展开只在这里发生：模型写的 `atom_candidate_keys` 只是本批的短把手，身份由写入侧
    从**已经构造出来的**候选对象取。因此草稿与候选的对应关系不是模型自报，而是同一次构造的
    两个投影。指向本束不存在的键即 fail-closed——那说明这一层映射是编出来的。

    事实轴（`pprov-1`）在这一层做**权威回查**：`source_fact_refs` 里的每一个出处都必须能唯一
    还原成**本次权威输入里的一行**（`authority_fact_ref` + `table` 的回查）。这是在解析层做不到
    的一件事——那一层没有权威目录，只有模型自己声明的别名表；两处判同一件事会让「哪一处才是
    权威」变成噪声，因此那一层判**形状与轴**，这一层判**这一行在不在**。
    这里判的仍然是**出处**，不是授权：某条事实能不能写进正文，由它自己的候选、路径 A 支撑边与
    后续决定判。出处回查只是让「这段文字从哪一行长出来」这句话可复核。
    """
    units: list[NS.NaturalProseDraftUnit] = []
    provenance_index = (_fact_provenance_index(table)
                        if any(spec.get("source_fact_refs") for spec in (specs or ()))
                        else {})
    for index, spec in enumerate(specs or ()):
        atoms: list[str] = []
        for key in spec["atom_candidate_keys"]:
            candidate = by_key.get(str(key))
            if candidate is None:
                raise PackWriterError(
                    f"自然草稿单元 {spec['prose_key']} 声明的原子 {str(key)!r} 在本束候选里"
                    "不存在：草稿声明的每个事实原子都要指得出候选（草稿是候选的来源，不是旁白）")
            atoms.append(candidate.candidate_id)
        fact_refs: list[str] = []
        for ref in spec.get("source_fact_refs") or ():
            if str(ref) not in provenance_index:
                raise PackWriterError(
                    f"自然草稿单元 {spec['prose_key']} 的事实出处 {str(ref)!r} 不在本次权威输入"
                    "里：草稿的事实出处只能指向**本次请求逐行声明过**的权威事实行"
                    "（`authority_facts` 的 ref），指不到即 fail-closed——不得凭一个指纹猜一行")
            fact_refs.append(str(ref))
        units.append(NS.NaturalProseDraftUnit.create(
            draft_revision=draft_revision, section_id=section_id, index=index,
            text=spec["text"], source_member_refs=tuple(spec["source_member_refs"]),
            source_fact_refs=tuple(fact_refs),
            atom_candidate_ids=tuple(atoms)))
    return tuple(units)


def _fact_provenance_index(
        table: Mapping[tuple[str, str, str], AuthorityFactEntry]) -> dict[str, tuple[str, str, str]]:
    """权威事实表 → `出处指纹 → 坐标` 的反查索引（只建一次，供整束的草稿回查用）。

    两个坐标映到同一个指纹是身份层的缺陷（`fprov_*` 是三元组的内容指纹），在这里当场暴露，
    而不是让某一处回查「碰巧先撞上哪一行」——那会让同一份输入在不同遍历顺序下给出不同答案。
    """
    index: dict[str, tuple[str, str, str]] = {}
    for key in table:
        ref = NS.fact_provenance_ref(key[0], key[1], key[2])
        if ref in index and index[ref] != key:
            raise PackWriterError(
                f"权威事实出处指纹 {ref!r} 同时对应 {list(index[ref])} 与 {list(key)}："
                "出处指纹必须与权威坐标一一对应")
        index[ref] = key
    return index


def _prose_specs_digest(prose_specs: Sequence[Mapping[str, Any]] | None) -> str:
    """合并后的草稿**规格** → 草稿层摘要（与单元口径**同一实现**，见 `NS.natural_prose_spec_digest`）。

    存在的理由与那个函数同一份：候选身份（`ccand_*`）反过来依赖 `draft_revision`，因此写入侧
    必须在「候选还不存在」的那一刻先定出修订。投影只取合并后的序号（`index`，与
    `_build_natural_prose_units` 给单元的 `index` 同一口径）/ `text` / `source_member_refs` /
    `source_fact_refs`（`pprov-1` 的两条互斥出处轴，`narr-8`），与
    `NS.natural_prose_draft_digest` 对单元的投影逐项相同，因此「先按规格算」与「再按单元算」
    不可能给出两个答案。空规格集返回空串 = 本节没有草稿层。
    """
    return NS.natural_prose_spec_digest(
        ({"index": pos, "text": spec["text"],
          "source_member_refs": spec["source_member_refs"],
          # 规格缺这条轴时报空数组（历史线 `proposals-10` 的规格没有它），与单元侧的
          # `u.source_fact_refs == ()` 逐项同形。
          "source_fact_refs": spec.get("source_fact_refs") or ()}
         for pos, spec in enumerate(prose_specs or ())))


def _factual_proposal(edge: Mapping[str, Any], *, candidate: NS.ClaimCandidate,
                      manifest: NS.WriterMaterialManifest,
                      table: Mapping[tuple[str, str, str], AuthorityFactEntry], label: str,
                      dependency_fingerprint: str) -> NS.ProposedSupportRef:
    """一条 factual 支撑边 → proposal（源/来源/内容指纹一律来自权威，模型不自报）。"""
    what = f"候选 {label} 的支撑边"
    shared = {"binding_subject_kind": "claim_candidate",
              "binding_subject_id": candidate.candidate_id,
              "draft_revision": candidate.draft_revision,
              "manifest_id": manifest.manifest_id, "manifest_fingerprint": manifest.fingerprint(),
              "support_role": str(edge["support_role"]), "support_semantics": "factual",
              "dependency_fingerprint": dependency_fingerprint}
    if str(edge["authorization_path"]) == "path_a_prevalidated":
        entry = _resolve_fact_declaration(edge, table, what=what)
        return NS.ProposedSupportRef.create(
            **shared, authority_kind=entry.authority_kind,
            authority_container_id=entry.container_identity,
            source_identity=entry.source_identity,
            provenance_identity=entry.provenance_identity,
            authorization_path="path_a_prevalidated",
            content_fingerprint=entry.content_fingerprint,
            # material 锚点（含 payload）由该事实**自己的引用**派生，不由模型选。
            material_id=entry.material_id,
            payload_ref=(dict(entry.payload_ref) if entry.payload_ref else None),
            locator_ref=entry.locator_ref, **entry.fact_kwargs())
    member = _resolve_material_declaration(edge, manifest, what=what)
    return NS.ProposedSupportRef.create(
        **shared, authority_kind="topic_pack", authority_container_id=member.pack_id,
        source_identity=member.source_identity, provenance_identity=member.provenance_identity,
        authorization_path="path_b_material_derived",
        content_fingerprint=member.material_content_fingerprint,
        # §四.1：路径 B 的边必须**闭合**——只带 material_id 而把 payload/locator 留给下游回查
        # manifest，等于让「这条边绑定了什么」变成重新拼接的结果。这里逐字带上该成员的
        # payload_ref 与 exact locator（值与 manifest 成员逐字相同，门会拿边**自己**的字段核）。
        material_id=member.material_id,
        payload_ref=dict(member.payload_ref), locator_ref=dict(member.locator_ref))


def _context_proposal(edge: Mapping[str, Any], *, unit: NS.NarrativeDraftUnit,
                      manifest: NS.WriterMaterialManifest, label: str,
                      dependency_fingerprint: str) -> NS.ProposedSupportRef:
    """一条 context 支撑边 → proposal：只挂**门前叙述单元**，绝不挂候选、不带事实身份。"""
    member = _resolve_material_declaration(edge, manifest, what=f"草稿单元 {label} 的 context 边")
    return NS.ProposedSupportRef.create(
        binding_subject_kind="narrative_draft_unit", binding_subject_id=unit.draft_unit_id,
        draft_revision=unit.draft_revision, manifest_id=manifest.manifest_id,
        manifest_fingerprint=manifest.fingerprint(), authority_kind=member.authority_kind,
        authority_container_id=member.pack_id, source_identity=member.source_identity,
        provenance_identity=member.provenance_identity, support_role="corroborating",
        support_semantics="context", authorization_path="context_only",
        content_fingerprint=member.material_content_fingerprint,
        # §四.2：context 边与路径 B 用**同一**闭合口径（同样的载体身份 + payload + locator）。
        dependency_fingerprint=dependency_fingerprint, material_id=member.material_id,
        payload_ref=dict(member.payload_ref), locator_ref=dict(member.locator_ref))


def _derive_material_manifest(authority: Any, *,
                              material_context: Any) -> NS.WriterMaterialManifest:
    """`VerifiedPackSet` × `WriterMaterialContext` → exact manifest（**同一**成员集合）。

    `wmm-2` 起，成员身份包含真实 payload 引用 / exact locator / payload 哈希 / 读视图指纹，
    因此本函数**必须**拿到 `WriterMaterialContext`：**只凭 material ID 构造成员已经不可表达**。
    这样「缺 resolver / 正文解析不出来」在构造期就 fail-closed，而不是让 LLM 对着 ID 列表写。

    topic_harness：成员集合 = 每个当前 Pack 的 materials（逐条配 matching RMD，缺配对即
    fail-closed）。上下文必须与之**逐成员相等**：少一份（漏解析）或多一份（来自别的 PackSet）
    都是 fail-closed，不做「按需裁剪」。其他 producer kind 没有 Pack material 边界，manifest
    与上下文都为空——空集是**合法**状态，不是「无材料也照样写」的许可。
    """
    if not isinstance(authority, TopicPackAuthorityInput):
        materials = tuple(getattr(material_context, "materials", ()) or ())
        if materials:
            raise PackWriterError(
                f"{type(authority).__name__} 不得携带 Pack 材料正文上下文"
                "（非 Pack 权威没有 exact ResearchMaterial 边界）")
        return NS.WriterMaterialManifest.create(members=())
    if material_context is None:
        raise PackWriterError(
            "topic authority 缺材料正文上下文（wmctx-1）：Writer 不得在只拿到 material ID 的"
            "情况下写作。组合根必须注入 `PayloadResolver` 并经 "
            "`material_context.resolve_writer_material_context` 解析真实正文")
    expected_fingerprint = MC.pack_set_fingerprint(authority.pack_set)
    if material_context.pack_set_fingerprint != expected_fingerprint:
        raise PackWriterError(
            "材料正文上下文不属于本节的当前 PackSet（pack_set_fingerprint 不符）："
            "不得把另一份 PackSet 的正文带进本节写作")
    if material_context.task_id != authority.task_id \
            or material_context.section_id != authority.section_id:
        raise PackWriterError(
            f"材料正文上下文不属于本任务/本节：{material_context.task_id!r}/"
            f"{material_context.section_id!r} != {authority.task_id!r}/{authority.section_id!r}")
    expected_refs: list[str] = []
    for pack in sorted(authority.pack_set.packs,
                       key=lambda p: (str(p.topic_id), str(p.pack_id))):
        index = _pack_material_index(pack)
        for material_id in sorted(index):
            expected_refs.append(NS.manifest_member_ref(str(pack.pack_id), material_id))
    got_refs = list(material_context.member_refs())
    missing = sorted(set(expected_refs) - set(got_refs))
    extra = sorted(set(got_refs) - set(expected_refs))
    if missing or extra:
        raise PackWriterError(
            f"材料正文上下文与当前 PackSet 的材料集合不是同一个集合：缺正文 {missing}，"
            f"多余正文 {extra}（每个成员都必须有已解析正文，且不得夹带别处的正文）")
    members = [NS.WriterMaterialManifestEntry.from_material_context(material)
               for material in material_context.materials]
    return NS.WriterMaterialManifest.create(members=members)


def _relevance_decision_ref(member: NS.WriterMaterialManifestEntry) -> str:
    """`not_used` 的**可重算**相关性决定记录（MREL 政策第 6 条）。

    记录体就是判定本身：该成员在本政策版本下「没有被任何 proposal 引用」（引用条目为空是
    判定结果，不是缺字段），因此任何人拿同一成员 + 同一政策版本都能重算出同一个 ref。
    """
    return NS.content_id("mreldec_", {
        "policy_version": MATERIAL_RELEVANCE_POLICY_VERSION,
        "policy_fingerprint": MATERIAL_RELEVANCE_POLICY_FINGERPRINT,
        "member_ref": member.member_ref,
        "material_content_fingerprint": member.material_content_fingerprint,
        "referencing_proposal_ids": [],
    })


def _derive_material_processing(
        *, manifest: NS.WriterMaterialManifest, proposals: Sequence[NS.ProposedSupportRef],
        policy: WriterPolicy) -> tuple[NS.WriterMaterialProcessingDisposition, ...]:
    """manifest × 本次提案集 → 每个成员的处理去向（MREL 政策第 1/6 条的确定性实现）。

    三层集合等式由 `NS.validate_material_processing` 复核：每个成员恰一行、`processed=True`、
    `used ∪ not_used = available`。`not_used` **不是**缺口：它只说明本轮没用到这份材料，
    绝不产生 ContractGap / SectionUnresolved（§0.13）。
    """
    used: dict[str, list[str]] = {}
    for prop in proposals:
        if prop.authority_kind != "topic_pack" or not prop.material_id:
            continue
        member_ref = NS.manifest_member_ref(prop.authority_container_id, prop.material_id)
        if manifest.entry_for(member_ref) is None:
            # 不在清单内的成员：不在这里制造一行「不存在的成员」——构造期/门各自 fail-closed。
            continue
        bucket = used.setdefault(member_ref, [])
        if prop.proposed_support_id not in bucket:
            bucket.append(prop.proposed_support_id)
    rows: list[NS.WriterMaterialProcessingDisposition] = []
    for member in manifest.entries:
        support_usages = tuple(sorted(used.get(member.member_ref, ())))
        common = {
            "manifest_id": manifest.manifest_id, "manifest_fingerprint": manifest.fingerprint(),
            "member_ref": member.member_ref, "pack_id": member.pack_id,
            "material_id": member.material_id,
            "research_material_disposition_id": member.research_material_disposition_id,
            "material_content_fingerprint": member.material_content_fingerprint,
            "processed": True, "writer_policy_version": policy.policy_version}
        if support_usages:
            rows.append(NS.WriterMaterialProcessingDisposition.create(
                **common, usage="used", support_usages=support_usages))
            continue
        rows.append(NS.WriterMaterialProcessingDisposition.create(
            **common, usage="not_used", reason_code="irrelevant_to_section_goal",
            reason_proof={"policy_version": MATERIAL_RELEVANCE_POLICY_VERSION,
                          "policy_fingerprint": MATERIAL_RELEVANCE_POLICY_FINGERPRINT,
                          "relevance_decision_ref": _relevance_decision_ref(member)}))
    return tuple(rows)


def _authorized_scope_of(scan: AuthorityScan, aspect_id: str) -> tuple[str, ...]:
    """`FollowUpNeed.contract_authorized_scope`：该 aspect 的 Contract 授权范围（**同一口径**）。

    与 `harness.topic_runtime._authorized_scope` 同形：本模块只从权威投影里取回
    `(aspect_id, topic_id, question_id, *impact_scope)`，不另写一套范围语义。
    """
    raw = scan.aspect_impact.get(aspect_id, ())
    values = {aspect_id, str(scan.aspect_topic.get(aspect_id, "")),
              str(scan.aspect_question.get(aspect_id, ""))}
    values |= {str(v) for v in ((raw,) if isinstance(raw, str) else tuple(raw or ()))}
    return tuple(sorted(v for v in values if v))


def _requestable_aspects(scan: AuthorityScan, task: PS.SectionTask) -> list[dict]:
    """可申请补件的 aspect 目录（给生成器的**只读**目录，不是授权）。

    逐行给出 `aspect_id / topic_id / question_id / target_requirement_id / status`。存在的理由
    很具体：`follow_up_needs` 必须逐字回指「本主题的 requirement id」与「该问题下已投影的
    aspect」，而这三个 id 分属 `requirement_ids`（按 topic）、`projection.aspects`（扁平 id 表）
    两个输入面——模型**无从**把它们对上。目录只是把这份对应关系摆出来，不新增任何可写面。

    §六：这里**只收录**能通过 `_follow_up_needs` 全部校验的行（topic/question 都属于本任务、
    requirement id 真实存在）。目录不得列出「看起来像但申请必被拒」的行——那等于用一个假目录
    诱导模型发出注定 fail-closed 的诉求。status 取自冻结 Contract 的覆盖判定，只作提示：
    它既不放宽也不收紧申请条件，更不把未覆盖 aspect 变成可授权事实。
    """
    known_questions = {str(q.topic_id): set() for q in task.questions}
    for question in task.questions:
        known_questions[str(question.topic_id)].add(str(question.question_id))
    rows: list[dict] = []
    for aspect_id in sorted(scan.aspect_status):
        topic_id = str(scan.aspect_topic.get(aspect_id, ""))
        question_id = str(scan.aspect_question.get(aspect_id, ""))
        requirement_id = str(scan.requirement_ids.get(topic_id, ""))
        if not topic_id or not question_id or not requirement_id:
            continue
        if question_id not in known_questions.get(topic_id, set()):
            continue
        rows.append({"aspect_id": aspect_id, "topic_id": topic_id,
                     "question_id": question_id,
                     "target_requirement_id": requirement_id,
                     "status": str(scan.aspect_status[aspect_id])})
    return rows


def _follow_up_need(spec: Mapping[str, Any], *, task: PS.SectionTask, authority: Any,
                    scan: AuthorityScan, draft_revision: str, policy: WriterPolicy,
                    spec_index: int = 0) -> Any:
    """**一条**补件申请 → 真类 `FollowUpNeed`；不成立即抛 `FollowUpNeedRejected`（逐条判定）。

    校验一字未减（topic 属于本节 / question 属于该 topic / aspect 落在该 topic 的投影内 /
    aspect 属于该 question / target_requirement_id 是该主题的真实需求 id / 授权范围非空 /
    `TS.FollowUpNeed` 自己的 schema）。变的是**后果的范围**：不成立的只是**这一条**。
    """
    topic_id, question_id = str(spec["topic_id"]), str(spec["question_id"])
    aspect_id = str(spec["aspect_id"])
    number = f"follow_up_need「{spec['statement'][:40]}」"

    def _reject(message: str, code: str) -> FollowUpNeedRejected:
        return FollowUpNeedRejected(message, code=code, spec_index=spec_index)

    topics = {str(t) for t in tuple(getattr(authority, "topic_ids", ()) or ())}
    questions: dict[str, set[str]] = {}
    for question in task.questions:
        questions.setdefault(str(question.topic_id), set()).add(str(question.question_id))
    if topic_id not in topics:
        raise _reject(f"{number} 的 topic_id={topic_id!r} 不属于本节权威输入",
                      "topic_not_in_section")
    if question_id not in questions.get(topic_id, set()):
        raise _reject(f"{number} 的 question_id={question_id!r} 不是 topic {topic_id!r} 下的问题",
                      "question_not_under_topic")
    if scan.aspect_topic.get(aspect_id) != topic_id:
        raise _reject(
            f"{number} 的 aspect_id={aspect_id!r} 不在 topic {topic_id!r} 的 Contract 投影内"
            "（四个 id 必须逐字取自 requestable_aspects 的**同一行**，不得跨行拼）",
            "aspect_not_in_topic")
    if scan.aspect_question.get(aspect_id) != question_id:
        raise _reject(
            f"{number} 的 aspect_id={aspect_id!r} 不属于 question {question_id!r}"
            "（申请必须落在它自己那一项需求上）", "aspect_not_in_question")
    declared = str(spec["target_requirement_id"])
    expected = str(scan.requirement_ids.get(topic_id, ""))
    if not expected or declared != expected:
        raise _reject(
            f"{number} 的 target_requirement_id={declared!r} 不是该主题的真实需求 id"
            f"（权威侧为 {expected or '无'}）；申请不得凭字符串猜一个需求出来",
            "target_requirement_mismatch")
    scope = _authorized_scope_of(scan, aspect_id)
    if not scope:
        raise _reject(f"{number} 的 contract_authorized_scope 为空（申请范围不得为空）",
                      "authorized_scope_empty")
    # `pw-19` 的入站防线：**空 `budget_hint` 在这里逐条被拒**，而不是等 Harness 获批执行时
    # 由 `TR.build_follow_up_focus` 抛 `TopicRuntimeError`。判据与那一处**逐字同源**
    # （`isinstance(value, str) and value.strip()`）——两处要是判得不一样，就会出现
    # 「Writer 侧放行、检索侧炸」或者反过来「Writer 侧拒、检索侧本来受得了」两种不一致。
    # 放在这一条函数里而不是 `_parse_follow_up_specs`：后者抛的是整束异常，会把同一份响应里
    # 其余合法的诉求与已经成形的整节草稿一起带走（正是 r5 financial 的死法）。
    budget_hint = str(spec.get("budget_hint") or "")
    if not budget_hint.strip():
        raise _reject(
            f"{number} 的 budget_hint 为空：诉求要**进入检索**，而检索侧对「空字段的诉求」是 "
            "fail-closed 的（`TR.build_follow_up_focus`）——空串在这里不是「没有偏好」，是"
            "「这条诉求注定执行不了」。请写一个非空的检索预算提示（例如 `tree_inspect:1`），"
            "或不要提出这条申请。",
            "budget_hint_empty")
    try:
        return TS.FollowUpNeed(
            need_id=TS.derive_follow_up_need_id(str(spec["statement"]), declared, aspect_id,
                                               task.section_id, draft_revision),
            need_schema_version=TS.FOLLOW_UP_NEED_SCHEMA_VERSION,
            statement=str(spec["statement"]), target_requirement_id=declared,
            topic_id=topic_id, question_id=question_id, aspect_id=aspect_id,
            section_id=task.section_id, section_draft_revision=draft_revision,
            contract_authorized_scope=scope, requiredness=str(spec["requiredness"]),
            expected_source_class=str(spec["expected_source_class"]),
            budget_hint=budget_hint,
            writer_identity=policy.prompt_version)
    except TS.SchemaValidationError as exc:
        raise _reject(f"{number} 不合法：{exc}", "need_schema_invalid") from exc


def _follow_up_needs(*, specs: Sequence[Mapping[str, Any]], task: PS.SectionTask,
                     authority: Any, scan: AuthorityScan, draft_revision: str,
                     policy: WriterPolicy) -> tuple[Any, ...]:
    """补件申请（§16.5 边界③）：Writer 需要更多材料时的**唯一**出口。

    它是独立身份：不是 draft 的字段、不进 draft 身份、不是 gap、也不是 SectionUnresolved。
    申请必须指回**真实**的 Contract 需求与已投影 aspect，否则 fail-closed（不得凭空要材料）。

    本入口在**任一条**不成立时整体抛出（调用方要么已经逐条隔离，要么需要「全有或全无」）；
    「不成立的那条被 typed 拒绝、其余照常成立」走 `_follow_up_needs_partition`。
    """
    return tuple(_follow_up_need(spec, task=task, authority=authority, scan=scan,
                                 draft_revision=draft_revision, policy=policy,
                                 spec_index=index)
                 for index, spec in enumerate(specs))


def _follow_up_rejection_record(spec: Mapping[str, Any], *, exc: BaseException,
                                attempt: int | None, spec_index: int) -> dict:
    """一条不成立的补件申请 → typed 拒绝记录（**唯一**一处写法）。

    三条路径共用它，因此「为什么这条申请不成立」在成功节、被拒轮、未结构校验那条路上没有
    三种长相。记录里既有**封闭原因码**（`code`，可统计、可比较），也有**可读原因**（`reason`，
    带异常类型与原文），还有**它自己那一份响应里的序号**（`spec_index`）——少了序号，
    「第几条写错了」就只能靠 statement 前缀去猜。
    """
    code = str(getattr(exc, "code", "") or "")
    if code not in FOLLOW_UP_REJECTION_CODES:
        # 非 `FollowUpNeedRejected` 的形态（缺字段 / 类型不符 / 属性缺失）：它同样是「这条申请
        # 不成立」，只是连语义校验都没走到。**不**编一个语义原因码，如实记成缺字段形态。
        code = "spec_field_missing"
    return {"attempt": attempt, "spec_index": spec_index,
            "statement": str(spec.get("statement", ""))[:200],
            "aspect_id": str(spec.get("aspect_id", "")),
            "topic_id": str(spec.get("topic_id", "")),
            "question_id": str(spec.get("question_id", "")),
            "code": code,
            "reason": f"{type(exc).__name__}: {str(exc)[:280]}"}


def _follow_up_needs_partition(*, specs: Sequence[Mapping[str, Any]], task: PS.SectionTask,
                               authority: Any, scan: AuthorityScan, draft_revision: str,
                               policy: WriterPolicy, attempt: int | None = None
                               ) -> tuple[tuple[Any, ...], tuple[dict, ...]]:
    """逐条判定：`(成立的 FollowUpNeed, 不成立的 typed 拒绝记录)`。

    **一条写错的申请不得杀死整节**：本节已经成形的候选、草稿单元与通过硬门的 Draft 与「某一条
    诉求填错了 topic/aspect」是两件事。不成立的那条照原样留档（含原因码与可读原因），而不是
    连同整节一起消失；成立的照常成为 `FollowUpNeed`。
    """
    needs: list[Any] = []
    rejected: list[dict] = []
    for index, spec in enumerate(specs):
        try:
            needs.append(_follow_up_need(spec, task=task, authority=authority, scan=scan,
                                        draft_revision=draft_revision, policy=policy,
                                        spec_index=index))
        except (FollowUpNeedRejected, KeyError, TypeError, AttributeError, ValueError) as exc:
            rejected.append(_follow_up_rejection_record(
                spec, exc=exc, attempt=attempt, spec_index=index))
    return tuple(needs), tuple(rejected)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

def _authority_context(authority: Any) -> dict:
    """给写作器的**权威上下文**：主体、口径、币种、基准期与权威类型。

    全部字段只取自上游权威自己的身份（财务取 artifact 快照身份，topic pack 取 Pack 身份），
    这里不推断、不默认、不补位。它只帮写作器把「期间/单位/主体范围」写清楚（§五 2），
    **不是**新的事实来源：写作器不得据此引入任何输入之外的主体、口径或数字。
    """
    if isinstance(authority, FinancialAuthorityInput):
        snapshot = getattr(authority.artifact, "snapshot", None)
        return {
            "kind": "financial_pack",
            "artifact_id": str(getattr(authority.artifact, "artifact_id", "") or ""),
            "company_id": authority.company_id,
            "report_as_of": authority.report_as_of,
            "scope": str(getattr(snapshot, "scope", "") or ""),
            "currency": str(getattr(snapshot, "currency", "") or ""),
            "periods": [str(p) for p in (getattr(authority.artifact, "periods", ()) or ())],
            "note_state": "facts" if authority.note_facts is not None else "gap",
        }
    if isinstance(authority, DerivedAuthorityInput):
        return {
            "kind": "derived_section",
            "company_id": authority.company_id,
            "report_as_of": authority.report_as_of,
            "topic_ids": list(authority.topic_ids),
            "upstream_identity": [list(x) for x in authority.upstream_identity],
        }
    pack_set = getattr(authority, "pack_set", None)
    return {
        "kind": "topic_pack",
        "pack_ids": [str(getattr(p, "pack_id", "") or "")
                     for p in tuple(getattr(pack_set, "packs", ()) or ())],
        "company_id": str(getattr(authority, "company_id", "") or ""),
        "report_as_of": str(getattr(authority, "report_as_of", "") or ""),
        "topic_ids": list(getattr(authority, "topic_ids", ()) or ()),
    }


def _reading_views_for_manifest(manifest: NS.WriterMaterialManifest,
                                material_context: Any
                                ) -> dict[str, tuple[str, dict | None, dict | None]]:
    """manifest 成员 → `(正文读视图, 表格读视图, 内容形态读法)`；集合必须**逐成员相等**。

    只读已解析的 `ResolvedWriterMaterial`：正文既不是从 material ID 猜的，也不是调用方另带
    的一份（那会让「模型看过的正文」与「清单声明的材料」分叉）。第三项随正文一起走，是
    §二 2.3 的内容形态（勾选表单行连它的所问事项/选项/选中状态/所在节点/来源一起到达），
    不是本模块另算的一份：形态判定在材料构建期就进了 payload 哈希。
    """
    if not manifest.entries:
        return {}
    if material_context is None:
        raise PackWriterError(
            "manifest 非空但没有材料正文上下文（wmctx-1）：不得把只有 ID 的清单交给生成器")
    views: dict[str, tuple[str, dict | None, dict | None]] = {}
    for material in material_context.materials:
        views[material.member_ref] = (material.reading_view, material.structured_view,
                                      getattr(material, "content_qualification", None))
    missing = sorted(ref for ref in manifest.member_refs() if ref not in views)
    extra = sorted(ref for ref in views if ref not in set(manifest.member_refs()))
    if missing or extra:
        raise PackWriterError(
            f"材料清单与正文上下文不是同一个成员集合：缺正文 {missing}，多余正文 {extra}")
    return views


def _projection_aspects_payload(projection: ContractProjection,
                                projection_aspects: Sequence[str],
                                scan: AuthorityScan) -> list[dict]:
    """投给生成器的 Contract 投影行（§三 A / 3.1）。

    一行的语义 = 「**这条 aspect 的原文要求**（Contract 逐字的中文业务要求）」＋「WritingSpec
    为该要求分配的**槽位**（正文角色 / 表格 schema / 引用颗粒度 / 展示层级 / 缺口展示政策 /
    期间语言政策）」。两者**都不是本模块的措辞**：要求逐字取自 frozen Contract 的
    `requirement_text`，槽位逐字取自 frozen WritingSpec 的 mappings（`ProjectedAspect`）。

    为什么必须是**行**而不是 id 列表：只给 `company_business_main.revenue_breakdown` 这样的
    id，生成器既不知道「要写什么」，也不知道「这段是段落还是表格、引用要细到行还是 claim」，
    于是只能按 id 的英文形状猜——这正是「写作质量」在请求面就已经丢掉的地方。

    每个 id 必须能在投影里解析出槽位（`_selected_aspects` 已按投影过滤）；解析不出即为身份
    不一致，fail-closed，不得降级成裸 id 行。`requirement_text` 缺失同样 fail-closed：它不是
    「可选装饰」，而是这一行的主字段（缺了它，这行就只剩 id，等于没改）。
    """
    rows: list[dict] = []
    for aspect_id in projection_aspects:
        projected = projection.aspect(str(aspect_id))
        if projected is None:
            raise PackWriterError(
                f"aspect {aspect_id!r} 不在本节 Contract 投影内，无法给出槽位（投影行不得降级为裸 id）")
        requirement_text = str(scan.aspect_requirement.get(str(aspect_id), "") or "")
        # 全空白与空串同义：都是「这一行没有要求」。判空前先 strip，但**投出去的是逐字原文**
        # （不做任何清洗——Contract 的措辞不得在本模块被改写）。给一段纯空白不算给了要求。
        if not requirement_text.strip():
            raise PackWriterError(
                f"aspect {aspect_id!r} 的 Contract requirement_text 为空（或全空白）：投给生成器"
                "的要求行必须有逐字要求文本，否则这一行退化成只有 id")
        rows.append({
            "aspect_id": projected.aspect_id,
            "subsection_id": projected.subsection_id,
            # Contract 逐字的中文业务要求（唯一来源：frozen AspectV2.requirement_text）。
            "requirement_text": requirement_text,
            "topic_id": str(scan.aspect_topic.get(str(aspect_id), "")),
            "question_id": str(scan.aspect_question.get(str(aspect_id), "")),
            # WritingSpec 逐字分配的槽位（不是本模块的再设计）。
            "role": projected.role,
            "content_role": projected.content_role,
            "table_schema": projected.table_schema,
            "citation_granularity": projected.citation_granularity,
            "display_tier": projected.display_tier,
            "gap_display_policy": projected.gap_display_policy,
            "period_language_policy": projected.period_language_policy,
            # 本条 aspect 的状态：原样来自 topic runtime，不改写、不美化（缺口不得写成完整结论）。
            "status": str(scan.aspect_status.get(str(aspect_id), "")),
        })
    return rows


def _aspects_of_subsection(projection: ContractProjection,
                           projection_aspects: Sequence[str],
                           subsection_id: str) -> tuple[str, ...]:
    """本节投影下属于该栏目的 aspect（按冻结投影顺序）——审计与定向说明共用同一处取值。"""
    return tuple(a for a in projection_aspects
                 if projection.aspect(str(a)) is not None
                 and projection.aspect(str(a)).subsection_id == subsection_id)


def _uncovered_subsections(projection: ContractProjection, projection_aspects: Sequence[str],
                           bundle: _PreGateBundle,
                           scan: AuthorityScan) -> tuple[str, ...]:
    """这一束里**一个候选都没有覆盖到**的 Contract 栏目（按冻结目录顺序）。

    覆盖的判据是**事实侧**：一条 factual 支撑边绑定的权威事实，其 `aspect_ids` 落在该栏目的
    aspect 集合里，才算这个栏目被写到了。为什么不用「候选文本里出现某个词」或「候选属于哪个
    栏目」：`ClaimCandidate` 的身份里**没有**方面/栏目字段（见 `NS.ClaimCandidate.identity_body`），
    所以「这条候选是为哪个栏目写的」在数据上根本不存在——按文本猜栏目就是发明归属。

    因此：

      * 只有**路径 A** 的边能让一个栏目算作被覆盖。这是有意的：Contract 的栏目要求是**事实**
        要求（`requirement_text` 说的是「列示…构成/金额/占比」这类事实），一条纯描述性的路径 B
        候选不构成对该栏目要求的回答；把它算作覆盖，等于用背景材料冒充事实覆盖。
      * 一个栏目没有任何 aspect 落在本节投影里就**不参与**判断（不是「未覆盖」，是不在范围内）。

    返回的栏目按 `projection.subsection_ids` 的冻结顺序，因此这条通道本身是确定性的。
    本函数只回答「哪个栏目没被写到」；「值不值得再问一次」由 `_focusable_subsections` 回答。
    """
    aspects_of = {a: projection.aspect(str(a)).subsection_id for a in projection_aspects}
    table = _authority_fact_table(scan.facts)
    covered_aspects: set[str] = set()
    for proposal in bundle.proposals:
        if str(getattr(proposal, "support_semantics", "")) != "factual":
            continue
        # 边的坐标 → 权威事实条目：每条 authority_kind 的 fact 字段名由 wire 模块给出
        # （不在这里重新拼字段名，也不按前缀猜）。
        field_name = NS.FACT_FIELD_BY_AUTHORITY_KIND.get(str(proposal.authority_kind))
        if not field_name:
            continue
        entry = table.get((str(proposal.authority_kind),
                           str(proposal.authority_container_id),
                           str(getattr(proposal, field_name, "") or "")))
        if entry is not None:
            covered_aspects.update(str(a) for a in entry.aspect_ids)
    return tuple(
        sid for sid in projection.subsection_ids
        if any(aspects_of.get(a) == sid for a in projection_aspects)
        and not any(aspects_of.get(a) == sid for a in covered_aspects))


def _focusable_subsections(projection: ContractProjection, projection_aspects: Sequence[str],
                           scan: AuthorityScan, uncovered: Sequence[str]) -> tuple[str, ...]:
    """未覆盖的栏目里**值得再问一次**的那些：本节权威事实里至少有一条落在该栏目上。

    为什么要有这一层，而不是「只要没覆盖就问一次」：栏目要求是**事实**要求，而路径 A 只能绑
    本节事实目录里的行。若该栏目在本节事实目录里一条事实都没有，那么第二次调用**在原理上**
    就不可能补上它——再一次请求只会得到同一份输出，然后（如果模型很听话）得到一条虚构的候选。
    一个注定失败的请求不是「为质量增加的有界调用」，而是纯粹的循环；而该栏目拿不到材料这件事
    本来就该由 Contract 必需事实那条规则形成缺口/gap，不由这里冒充。

    反过来说，只要材料**在**、模型却没写到，就值得如实问一次——那正是 3.3 要治的形态
    （材料齐、栏目空）。
    """
    wanted = set(uncovered)
    available: set[str] = set()
    for entry in scan.facts:
        available.update(str(a) for a in entry.aspect_ids)
    return tuple(
        sid for sid in uncovered
        if any(a in available
               for a in _aspects_of_subsection(projection, projection_aspects, sid)))


def _focus_content(projection: ContractProjection, projection_aspects: Sequence[str],
                   scan: AuthorityScan, uncovered: Sequence[str]) -> str:
    """栏目定向重写的**追加说明**：把「哪些栏目没有产出」连同它们的**逐字要求**摆出来。

    它不是「同一句话再问一遍」：不给出被点名栏目的 `requirement_text`，第二次调用只能靠猜
    「我上次漏了什么」；给了要求，第二次调用才是一次真正的栏目定向写作。同时明确要求重新输出
    **完整** JSON（而不是只补那几条）——否则第二次产出就不再是一个完整有序提案集，聚合门
    将面对一个残缺的集合。
    """
    wanted = set(uncovered)
    focused = tuple(a for a in projection_aspects
                    if projection.aspect(str(a)) is not None
                    and projection.aspect(str(a)).subsection_id in wanted)
    rows = _projection_aspects_payload(projection, focused, scan)
    lines = [f"- 栏目 {sid}：" for sid in uncovered]
    for row in rows:
        lines.append(
            f"  · aspect_id={row['aspect_id']}（状态 {row['status'] or '未声明'}）"
            f"要求：{row['requirement_text']}"
            f"；正文形态 content_role={row['content_role']}"
            + (f"，表格 schema={row['table_schema']}" if row["table_schema"] else "")
            + f"；引用颗粒度 {row['citation_granularity']}；展示层级 {row['display_tier']}")
    return (
        "\n\n上一次输出的**结构合法**，但它让下面这些 Contract 栏目一个候选都没有：\n"
        + "\n".join(lines) + "\n"
        "请为这些栏目补充候选，并**重新输出完整 JSON**（不是只输出新增的那几条）：\n"
        "1. 每个被点名的栏目，至少要有一条候选由**路径 A**（预验证权威事实）支撑——"
        "这些栏目要的是事实，背景性描述不算回答；\n"
        "2. 仍然遵守全部既有规则：不得引入输入之外的事实/数字/期间/主体，不得计算，"
        "不得把缺口写成完整结论；\n"
        "3. 确实拿不到材料的栏目**不要编**：把它留给缺口，或用 follow_up_needs 如实提出。\n")


def _focus_message_content(base_content: str, focus: str) -> str:
    """栏目定向调用的输入正文 = 原请求（逐字，不裁剪）+ 栏目定向说明。

    原请求**完整保留**：第二次调用看到的仍是同一份权威事实目录、同一份精确材料清单与同一份
    投影行。删掉其中任何一段都会让「按栏目补足」变成「在一个被裁剪过的输入上补足」——那正是
    本批反复拒绝的形态（用缩小输入面来让结果看起来对齐）。这里不裁剪、不换 system、不换模型、
    不换 prompt 版本：换任何一样，账本上就会出现「同一个 prompt 版本对应两种不同的输入纪律」。
    """
    if not base_content.endswith("\n"):
        base_content += "\n"
    return base_content + focus


# ---------------------------------------------------------------------------
# 分批请求：确定性的 Contract aspect 划分（§二）
#
# 划分**只**由「冻结 Contract 投影的 aspect 顺序」与 `MAX_ASPECTS_PER_BATCH` 决定，不看
# 模型输出、不看历史失败、不看本次材料多少：同一份投影在任何一次运行里都得到同一组批次。
# 这样「哪一批回答什么」是可复核的，而不是「上次截断在哪就缩到哪里」。
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriterPlanBatch:
    """**一批**门前提案请求的确定性身份（本批要回答的 aspect 范围）。"""

    batch_id: str
    #: 本批在**本轮**批次序列里的序号（从 1 起）与总数。缩小后的子批沿用父批的 index/total
    #: 并在 `shrink_depth` 上标出层级——它们回答的仍是同一个「第几批」的位置。
    index: int
    total: int
    #: 本批要回答的 aspect（**有序**，取自冻结投影顺序；同一 aspect 只出现在一批里）。
    aspect_ids: tuple[str, ...]
    #: 本批 aspect 归属的 topic（有序、去重；只作读视图，不参与任何判定）。
    topic_ids: tuple[str, ...] = ()
    #: 本批是第几次缩小产生的（0 = 直接来自确定性划分）。
    shrink_depth: int = 0
    #: 本批由哪一批缩小而来（直接划分的批次为 None）。缩小的方向因此可回查。
    parent_batch_id: str | None = None

    def __post_init__(self) -> None:
        if not str(self.batch_id or ""):
            raise PackWriterError("WriterPlanBatch.batch_id 不得为空")
        if self.index < 1 or self.total < 1 or self.index > self.total:
            raise PackWriterError(
                f"WriterPlanBatch 的序号非法：index={self.index} total={self.total}")
        if not self.aspect_ids and self.total != 1:
            raise PackWriterError(
                "空 aspect 列表的批次只能出现在「本节没有可划分的 Contract aspect」的唯一一批上"
                "（财务/附注权威不携带 aspect 状态）：划分出来的批不得是空批")
        if len(set(self.aspect_ids)) != len(self.aspect_ids):
            raise PackWriterError(f"WriterPlanBatch 的 aspect 有重复：{list(self.aspect_ids)}")
        if self.shrink_depth < 0:
            raise PackWriterError("WriterPlanBatch.shrink_depth 不得为负")
        if (self.shrink_depth == 0) != (self.parent_batch_id is None):
            raise PackWriterError(
                "WriterPlanBatch 的 shrink_depth 与 parent_batch_id 必须一致"
                "（直接划分的批次没有父批，缩小产生的批次必须有父批）")

    def label(self) -> str:
        """人读标签（产物与拒绝详情里都用它，两处不得各写一套）。"""
        base = f"{self.index}/{self.total}"
        return base if self.shrink_depth == 0 else f"{base}·缩小{self.shrink_depth}"

    def to_dict(self) -> dict:
        return {"batch_id": self.batch_id, "index": self.index, "total": self.total,
                "label": self.label(), "aspect_ids": list(self.aspect_ids),
                "topic_ids": list(self.topic_ids), "shrink_depth": self.shrink_depth,
                "parent_batch_id": self.parent_batch_id,
                "policy_version": ASPECT_BATCH_POLICY_VERSION}


def aspect_batch_count(aspect_count: int) -> int:
    """`aspect_count` 个 aspect 按本策略要分成几批（组合根据此推预算上界，不另写一份算式）。

    **必须与 `plan_aspect_batches` 逐值一致**，包括零 aspect：本节没有可划分的 Contract
    aspect 时（财务/附注权威）返回的仍是**恰好一批**（见该函数的零 aspect 分支）。若这里
    返回 0，按本数推出来的请求上界就会比实际少一次——预算「覆盖」了结构上界，而实际调用
    恰好越过它，这正是预算门最不该有的方向。
    """
    count = int(aspect_count)
    if count <= 0:
        return 1
    return -(-count // MAX_ASPECTS_PER_BATCH)


def plan_aspect_batches(projection: ContractProjection,
                        projection_aspects: Sequence[str],
                        scan: AuthorityScan) -> tuple[WriterPlanBatch, ...]:
    """本节投影 → **确定性**批次划分（连续切分，顺序即冻结投影顺序）。

    每个 aspect 恰好落在一批里：划分是**划分**（两两不交、并集 = 输入），不是「挑几批」。
    批身份由（策略版本 + 投影身份 + 序号 + 总数 + 本批 aspect 列表）内容寻址，因此
    「批 2 是哪几个 aspect」在任何一次运行里都可复算。

    零 aspect 时返回**恰好一批**（aspect 列表为空）：批次序列仍非空，下游的「一轮 = 一次分批
    扫描」不因本节没有 aspect 而变成特例。
    """
    ids = [str(a) for a in projection_aspects]
    if not ids:
        # 本节没有可划分的 Contract aspect（财务/附注权威的 `scan.aspect_status` 为空，见
        # `_financial_scan`）：没有可划分的东西，但**仍然只有一批**——请求形状与其它节一致
        # （逐批请求 = 本批），只是本批不带 aspect 范围，因此消息体里**不出现** `batch` 块
        # （空 aspect 列表若照发，会被读成「本批什么都不用回答」）。这不是「第二套运行时」：
        # 调用点、入账、合并、拒绝审计全部同一条链。
        return (_make_batch(projection=projection, chunk=(), index=1, total=1,
                            shrink_depth=0, parent_batch_id=None, scan=scan),)
    chunks = [ids[start:start + MAX_ASPECTS_PER_BATCH]
              for start in range(0, len(ids), MAX_ASPECTS_PER_BATCH)]
    total = len(chunks)
    return tuple(
        _make_batch(projection=projection, chunk=chunk, index=index + 1, total=total,
                    shrink_depth=0, parent_batch_id=None, scan=scan)
        for index, chunk in enumerate(chunks))


def _make_batch(*, projection: ContractProjection, chunk: Sequence[str], index: int, total: int,
                shrink_depth: int, parent_batch_id: str | None,
                scan: AuthorityScan) -> WriterPlanBatch:
    body = {"policy_version": ASPECT_BATCH_POLICY_VERSION,
            "projection_id": projection.projection_id, "index": index, "total": total,
            "aspect_ids": list(chunk), "shrink_depth": shrink_depth,
            "parent_batch_id": parent_batch_id}
    topics = tuple(sorted({str(scan.aspect_topic.get(str(a), "") or "") for a in chunk} - {""}))
    return WriterPlanBatch(batch_id=NS.content_id("wpbatch_", body), index=index, total=total,
                           aspect_ids=tuple(str(a) for a in chunk), topic_ids=topics,
                           shrink_depth=shrink_depth, parent_batch_id=parent_batch_id)


def split_batch_for_shrink(batch: WriterPlanBatch, scan: AuthorityScan,
                           projection: ContractProjection) -> tuple[WriterPlanBatch,
                                                                     WriterPlanBatch]:
    """把被截断的那一批**对半**拆成两批（切分点唯一，由本批 aspect 顺序决定）。

    对半而不是「减一个」：截断是**输出量**过大，减一个 aspect 只是把同一件事重问一遍；
    对半才是有界的确定性缩小。单 aspect 的批次不再可拆——调用方必须先判这一条。
    """
    ids = list(batch.aspect_ids)
    if len(ids) < 2:
        raise PackWriterError(
            f"批次 {batch.label()} 只剩 {len(ids)} 个 aspect，不得再缩小"
            "（单 aspect 仍被截断时整节 fail-closed，不得静默放弃该 aspect）")
    half = len(ids) // 2
    parts = (ids[:half], ids[half:])
    return tuple(  # type: ignore[return-value]
        _make_batch(projection=projection, chunk=part, index=batch.index, total=batch.total,
                    shrink_depth=batch.shrink_depth + 1, parent_batch_id=batch.batch_id,
                    scan=scan)
        for part in parts)


@dataclass(frozen=True)
class BatchCallRecord:
    """**一次**分批请求的读数（逐批、有序；成功与截断都要留）。

    「调用与重试逐次入账」在分批链上的落地：每一批实际发出去的请求都有一条记录，
    含 provider 侧 `call_id`（指向 `logs/llm` 的原始输出）、本批的 aspect 范围、缩小时的第几层。
    它**不是**拒绝记录：一轮里被采信的批次同样有记录，只有整轮未采信时才随拒绝审计一起留存。
    """

    batch_id: str
    label: str
    aspect_ids: tuple[str, ...]
    #: "ok" = 正常返回；"truncated" = provider 报截断（该批结果被拒，未解析）；"error" = 其它失败。
    status: str
    call_id: str | None = None
    response_hash: str | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None
    shrink_depth: int = 0
    focus: bool = False
    #: **谱系指针**：这一批是哪一批对半缩小的结果（`None` = 本轮的原始批）。
    #: 没有它，「这次截断后来被缩批答回来了没有」在产物侧只能靠 label 字符串前缀去猜
    #: （`2/4` 与 `2/4·缩小1`）——猜出来的关系不是身份。A7 判「截断有没有进入结局」必须
    #: 走**身份**（`call_id` 定位截断批 → 逐层沿本字段找到它的子孙），因此它是判据的输入，
    #: 不是装饰。`WriterPlanBatch.parent_batch_id` 早已存在，这里只是把它带上产物。
    parent_batch_id: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("ok", "truncated", "error"):
            raise PackWriterError(f"BatchCallRecord.status={self.status!r} 不在词表内")
        if not str(self.batch_id or ""):
            raise PackWriterError("BatchCallRecord.batch_id 不得为空")
        # 空 aspect 列表是**合法读数**而不是「忘了记」：它只可能来自「本节没有可划分的
        # Contract aspect」的唯一那一批（`plan_aspect_batches` 的零 aspect 分支），此时
        # 这次请求回答的就是整节而非某个 aspect 子集，`label` 必是 `1/1`（零 aspect 的批
        # 不可再缩小，见 `split_batch_for_shrink`）。
        if not self.aspect_ids and self.label != "1/1":
            raise PackWriterError(
                f"空 aspect 列表的调用记录只允许是「本节无可划分 aspect」的那一批"
                f"（label=1/1），实际 label={self.label!r}")
        # 谱系指针与层级必须一致：原始批没有父批，缩小批必然有父批（与
        # `WriterPlanBatch` 同一条不变量）。少了这一条，一条缩小子记录可以带着
        # `parent_batch_id=None` 混进产物，A7 就会把它读成「另一批原始请求」。
        if (self.shrink_depth == 0) != (self.parent_batch_id is None):
            raise PackWriterError(
                "BatchCallRecord 的 shrink_depth 与 parent_batch_id 必须一致："
                f"实际 shrink_depth={self.shrink_depth}、"
                f"parent_batch_id={self.parent_batch_id!r}")

    def to_dict(self) -> dict:
        return {"batch_id": self.batch_id, "label": self.label,
                "aspect_ids": list(self.aspect_ids), "status": self.status,
                "call_id": self.call_id, "response_hash": self.response_hash,
                "output_tokens": self.output_tokens, "finish_reason": self.finish_reason,
                "shrink_depth": self.shrink_depth, "focus": self.focus,
                "parent_batch_id": self.parent_batch_id}


def _edge_wire_key(edge: Mapping[str, Any]) -> tuple:
    """一条支撑边的**线格式**身份（合并去重按它判等，不按文本相似度猜）。"""
    return (str(edge.get("authority_kind") or ""), str(edge.get("container_id") or ""),
            str(edge.get("fact_id") or ""), str(edge.get("material_id") or ""),
            str(edge.get("support_role") or ""), str(edge.get("authorization_path") or ""))


def _merge_batch_plans(*, batch_plans: Sequence[tuple[WriterPlanBatch, Mapping[str, Any]]],
                       expected_aspects: Sequence[str], table: Mapping[tuple[str, str, str],
                                                                     AuthorityFactEntry],
                       ) -> tuple[dict, dict]:
    """各批输出 → **一份**完整有序提案集 + 合并审计（确定性，可复核）。

    合并规则（全部是身份判等，没有一处是「内容取舍」）：

      * **aspect 覆盖**：各批 aspect 之并集必须**恰好等于**本节的 aspect 全集，且两两不交。
        缺失或缺口的 aspect 不得靠别批的输出顶替（那会让「谁回答了什么」无从复核）。
      * **候选**：按（`claim_text`, 事实类型）判同一性——这正是 `ClaimCandidate` 内容身份的
        两个可变成分，也就是「同一条原子断言」。同一断言在批间重复出现时**合并为一条**，
        支撑边按线格式去重后**并集**（不丢弃任何一条模型声明过的边，也不发明新的）。
      * **草稿单元**：按（`unit_kind`, `text`）判同一性，context 边同样取并集。
      * **补件诉求**：逐字段完全相同的重复项只留一条；其余原样、按批序保留。
      * 合并后候选与草稿单元的标签**重排**为规范序（`c1..`/`u1..`），因为它们只是本批的
        局部标签，跨批必然冲突；重排映射逐条记进审计，读者仍能追回它是哪一批的哪一条。

    返回值第二项是**审计**（进产物，不进 Draft 身份）：合并前后的条数、每一条合并的来源、
    被合并掉的重复边数、以及逐批的调用读数。
    """
    requested: list[str] = []
    for batch, _plan in batch_plans:
        requested.extend(str(a) for a in batch.aspect_ids)
    if len(set(requested)) != len(requested):
        raise PackWriterError(
            f"批次划分不是划分：同一 aspect 落在两批里（{sorted(requested)}）——不得重复请求")
    if sorted(requested) != sorted(str(a) for a in expected_aspects):
        missing = sorted(set(map(str, expected_aspects)) - set(requested))
        extra = sorted(set(requested) - set(map(str, expected_aspects)))
        raise PackWriterError(
            f"批次覆盖与本节 aspect 全集不一致（缺 {missing}，多 {extra}）："
            "全部批次成功也不得当作完整覆盖")

    candidates: list[dict] = []
    candidate_pos: dict[tuple[str, str], int] = {}
    candidate_edges: list[list[tuple]] = []
    candidate_sources: list[list[dict]] = []
    candidate_merged_edges = 0
    units: list[dict] = []
    unit_pos: dict[tuple[str, str], int] = {}
    unit_edges: list[list[tuple]] = []
    unit_sources: list[list[dict]] = []
    unit_merged_edges = 0
    follow_ups: list[dict] = []
    follow_seen: set[tuple] = set()
    follow_dropped = 0
    #: 自然草稿（`pw-15`）：合并键 = `(prose 文本, 材料出处元组)`，原子集取**并集**。
    #: 合并会**重编号**候选（`c1..cN`，按合并后的位置），而草稿单元声明的是**模型自己写的**
    #: 批内键——因此重编号必须在合并**之内**完成（见 `declared_position`），不能留到解析层：
    #: 那里已经看不到「第 3 批的 c4」与「第 1 批的 c4」是两条不同的候选了。
    prose: list[dict] = []
    prose_pos: dict[tuple, int] = {}
    prose_atom_positions: list[list[int]] = []
    prose_sources: list[list[dict]] = []
    prose_atoms_dropped = 0
    #: 身份在权威侧闭不上的候选的**原标签**（有序）。它们不是「被剔除的候选」——整束照样
    #: 原样送门，由权威回查判定；这里只是把「合并时为什么用了哨兵」如实记进审计。
    unresolved_labels: list[str] = []

    for batch, plan in batch_plans:
        #: 本批「模型标签 → 合并后位置」。它只在本批内有效（同名标签在不同批是不同候选）。
        declared_position: dict[str, int] = {}
        for spec in plan["claim_candidates"]:
            text = str(spec["claim_text"])
            # 身份在权威侧闭不上的候选**照样参与合并**：合并阶段只回答「这几批合起来是一条
            # 完整有序的提案集吗」，身份判定唯一权威在 `_build_pre_gate_bundle` 的权威回查。
            # 在这里直接抛，会把「被拒的是一束什么」从审计里抹掉——那时连合并后的身份都还没有，
            # 记录只能退化成一句错误字符串。哨兵只影响合并分组，永远换不来一次通过。
            try:
                fact_type = _candidate_fact_type(spec, str(spec["candidate_key"]), table)
            except PackWriterError:
                fact_type = _UNRESOLVED_FACT_TYPE
                unresolved_labels.append(str(spec["candidate_key"]))
            key = (text, fact_type)
            position = candidate_pos.get(key)
            if position is None:
                position = len(candidates)
                candidate_pos[key] = position
                candidates.append({"candidate_key": "", "claim_text": text, "support": []})
                candidate_edges.append([])
                candidate_sources.append([])
            declared_position.setdefault(str(spec["candidate_key"]), position)
            candidate_sources[position].append(
                {"batch_id": batch.batch_id, "batch_label": batch.label(),
                 "candidate_key": str(spec["candidate_key"])})
            seen = set(candidate_edges[position])
            for edge in spec["support"]:
                wire = _edge_wire_key(edge)
                if wire in seen:
                    candidate_merged_edges += 1
                    continue
                seen.add(wire)
                candidate_edges[position].append(wire)
                candidates[position]["support"].append(dict(edge))
        for spec in plan["narrative_draft_units"]:
            key = (str(spec["unit_kind"]), str(spec["text"]))
            position = unit_pos.get(key)
            if position is None:
                position = len(units)
                unit_pos[key] = position
                units.append({"unit_key": "", "unit_kind": key[0], "text": key[1],
                              "context_support": []})
                unit_edges.append([])
                unit_sources.append([])
            unit_sources[position].append(
                {"batch_id": batch.batch_id, "batch_label": batch.label(),
                 "unit_key": str(spec["unit_key"])})
            seen = set(unit_edges[position])
            for edge in spec["context_support"]:
                wire = _edge_wire_key(edge)
                if wire in seen:
                    unit_merged_edges += 1
                    continue
                seen.add(wire)
                unit_edges[position].append(wire)
                units[position]["context_support"].append(dict(edge))
        for spec in plan["natural_prose_draft"]:
            # 这一批的草稿单元声明的原子键 → 合并后的候选位置。指不到本批**已解析**的候选
            # 即拒：草稿声明的是「哪些原子是它长出来的」，指向一个本批不存在的键说明这层映射
            # 是编出来的（跨批指认同样落在这里——批外的候选不属于本批的正文）。
            atoms: list[int] = []
            for atom in spec["atom_candidate_keys"]:
                position = declared_position.get(str(atom))
                if position is None:
                    raise PackWriterError(
                        f"自然草稿单元 {spec['prose_key']} 的 atom_candidate_keys 指向本批不存在的"
                        f"候选 {str(atom)!r}：原子只能指向**同一批**内、且已通过结构校验的候选"
                        "（草稿是候选的来源，不是它的旁白）")
                if position not in atoms:
                    atoms.append(position)
            # 同一段草稿的判据是「逐字同文 **且** 同一出处」——出处在这里必须是**两条轴一起**
            # 比（`pprov-1`）：少了事实轴，一段材料出处的散文与一段事实出处的散文只要正文
            # 逐字相同就会被并成一段，而那两段的读者面回溯指向的是两种不同的东西。
            key = (str(spec["text"]), tuple(spec["source_member_refs"]),
                   tuple(spec.get("source_fact_refs") or ()))
            position = prose_pos.get(key)
            if position is None:
                position = len(prose)
                prose_pos[key] = position
                prose.append({"prose_key": "", "text": key[0],
                              "source_member_refs": list(key[1]),
                              "source_fact_refs": list(key[2]), "atom_candidate_keys": []})
                prose_atom_positions.append([])
                prose_sources.append([])
            prose_sources[position].append(
                {"batch_id": batch.batch_id, "batch_label": batch.label(),
                 "prose_key": str(spec["prose_key"])})
            # 原子集取并集（同一句话在另一批里声明了别的事实原子时，两批说的都对：这一句覆盖
            # 那些原子）。**顺序**按首次出现，因此合并结果与批序一致、可复算。
            for atom_position in atoms:
                if atom_position in prose_atom_positions[position]:
                    prose_atoms_dropped += 1
                    continue
                prose_atom_positions[position].append(atom_position)
        for spec in plan["follow_up_needs"]:
            key = tuple(sorted((str(k), _jsonable(v)) for k, v in dict(spec).items()))
            if key in follow_seen:
                follow_dropped += 1
                continue
            follow_seen.add(key)
            follow_ups.append(dict(spec))

    for position, item in enumerate(candidates):
        item["candidate_key"] = f"c{position + 1}"
    for position, item in enumerate(units):
        item["unit_key"] = f"u{position + 1}"
    for position, item in enumerate(prose):
        item["prose_key"] = f"p{position + 1}"
        item["atom_candidate_keys"] = [candidates[p]["candidate_key"]
                                       for p in prose_atom_positions[position]]

    audit = {
        "policy_version": ASPECT_BATCH_POLICY_VERSION,
        "max_aspects_per_batch": MAX_ASPECTS_PER_BATCH,
        "aspects_requested": requested,
        "batch_total": len(batch_plans),
        "batches": [dict(batch.to_dict()) for batch, _plan in batch_plans],
        "candidate_total": len(candidates),
        "candidate_merged_from_duplicates": sum(
            len(sources) - 1 for sources in candidate_sources),
        "candidate_support_edges_dropped_as_duplicate": candidate_merged_edges,
        "candidate_sources": [sources for sources in candidate_sources],
        "unit_total": len(units),
        "unit_merged_from_duplicates": sum(len(sources) - 1 for sources in unit_sources),
        "unit_context_edges_dropped_as_duplicate": unit_merged_edges,
        "unit_sources": [sources for sources in unit_sources],
        "follow_up_total": len(follow_ups),
        "follow_up_dropped_as_duplicate": follow_dropped,
        # `pw-15`：自然草稿的合并读数。`prose_atom_duplicates_dropped` 是同一句在另一批里
        # 又把同一批候选声明了一遍的次数（并集去重，不改变归属）。
        "prose_total": len(prose),
        "prose_merged_from_duplicates": sum(len(s) - 1 for s in prose_sources),
        "prose_atom_duplicates_dropped": prose_atoms_dropped,
        "prose_sources": [list(s) for s in prose_sources],
        # 合并时事实类型闭不上、因而按哨兵分组的候选（原标签）。整束随后由权威回查判定。
        "fact_type_unresolved_labels": list(unresolved_labels),
    }
    return ({"natural_prose_draft": prose, "claim_candidates": candidates,
             "narrative_draft_units": units, "follow_up_needs": follow_ups}, audit)


def _append_note(base_content: str, note: str) -> str:
    """把一段说明**逐字追加**在完整原请求之后（不裁剪输入面、不换 prompt/模型）。

    返修说明与栏目定向说明走的是**同一条**追加通道：两者都是「在完整请求之后补一段说明」，
    因此不裁剪、不换 system、不换 prompt 版本——换任何一样，账本上就会出现「同一个 prompt
    版本对应两种不同的输入纪律」。
    """
    if not note:
        return base_content
    if not base_content.endswith("\n"):
        base_content += "\n"
    return base_content + note


#: 请求里草稿单元的示例文本：它是「读者将要读到的那段话」的**形状**占位，不是任何真实内容
#: （示例里的 `ref` 同样只是占位——模型必须换成本次请求真的声明过的那一行）。
_PROSE_EXAMPLE_TEXT = "<先写出来的自然段落文本（读者将要读到的那段话）>"
_PROSE_EXAMPLE_MEMBER_REF = "<materials 某一行的 ref>"
_PROSE_EXAMPLE_FACT_REF = "<authority_facts 某一行的 ref>"


def natural_prose_example(materials: Sequence[Any], facts: Sequence[Any]) -> list[dict]:
    """请求里 `output_schema.natural_prose_draft` 的示例：**按本请求的输入面选一条合法轴**。

    为什么不能是一份固定示例（`pw-18` 修的就是这个）：两条出处轴**互斥、恰有一条非空**：本节有
    材料行时只能用材料轴，只有权威事实行时（财务节的常态）只能用事实轴。一份「两条轴都填满」的
    固定示例因此必然与其中**一种**请求的 rules 相矛盾——照抄示例即被同一份请求判违规
    （`_build_natural_prose_units` 对「两条轴同时非空」「两条轴都空」都是整批拒绝），而不照抄
    就等于要模型自己猜到示例反着写。示例是模型最可能逐字模仿的那一处，它必须与同一份请求的
    rules、以及本请求真实的输入面**三者一致**。

    三种情形各有唯一合法解，不留「由模型自己挑」的余地：

    * 本节有材料行 ⇒ 材料轴（`source_member_refs` 非空、`source_fact_refs` 空）：材料才是写作
      表达面的载体，事实通过候选与支撑边进入。
    * 本节**一条材料行都没有**、只有权威事实行 ⇒ 事实轴（`source_fact_refs` 非空、
      `source_member_refs` 空）：没有材料行时写材料轴无从展开。
    * 两张表都空 ⇒ 空数组：这一档没有任何可用内容，两条轴都指不到真实输入，示例不得给出一条
      **无从展开**的轴（rules 里那一条要求模型在这一档只发 `follow_up_needs`）。

    `atom_candidate_keys` 在示例里保留一个占位候选键：草稿必须先于候选被读出来（键序即声明），
    示例只表形状，不代表任何真实原子数。
    """
    unit = {"prose_key": "p1", "text": _PROSE_EXAMPLE_TEXT, "source_member_refs": [],
            "source_fact_refs": [], "atom_candidate_keys": ["c1"]}
    if materials:
        unit["source_member_refs"] = [_PROSE_EXAMPLE_MEMBER_REF]
    elif facts:
        unit["source_fact_refs"] = [_PROSE_EXAMPLE_FACT_REF]
    else:
        return []
    return [unit]


#: 请求里候选支撑边 / context 边的示例占位（`pw-19`）。与草稿示例同样的道理：示例的 `ref` 只是
#: **形状**占位，模型必须换成本次请求真的声明过的那一行；但它**指向哪一张表**必须是本请求真的
#: 能展开的那一张——否则照抄示例就是照抄一条注定被拒的边。
_FACTUAL_SUPPORT_EXAMPLE_FACT_REF = "<本节 authority_facts 某一行的 ref>"
_FACTUAL_SUPPORT_EXAMPLE_MATERIAL_REF = "<本节 materials 某一行的 ref>"
_SUPPORT_EXAMPLE_ROLE = "primary|corroborating"


def factual_support_example(materials: Sequence[Any], facts: Sequence[Any]) -> list[dict]:
    """`output_schema.claim_candidates[*].support` 的示例：**按本请求的输入面**选一条能展开的边。

    `pw-18` 只把**草稿**的示例按输入面分档，候选的支撑边还是写死成事实行（`f1`）。于是
    「公司/行业只有材料行」这一档里，同一份请求一边要求草稿走材料轴、一边在示例里示范一条
    **指向事实行**的支撑边——而这一节根本没有事实行别名可展开。照抄示例即整批被拒。

    三档各有唯一合法解（与 `natural_prose_example` 同一条判据）：

    * 本节有权威事实行 ⇒ 事实行示例（路径 A：含数字/日期/期间的断言**只能**走这条路）；
    * 本节没有事实行、有材料行 ⇒ 材料行示例（路径 B：只授权非高风险描述性原子）；
    * 两张表都空 ⇒ 空数组（这一档没有可用内容，候选与草稿都留空）。
    """
    if facts:
        return [{"ref": _FACTUAL_SUPPORT_EXAMPLE_FACT_REF, "support_role": _SUPPORT_EXAMPLE_ROLE}]
    if materials:
        return [{"ref": _FACTUAL_SUPPORT_EXAMPLE_MATERIAL_REF,
                 "support_role": _SUPPORT_EXAMPLE_ROLE}]
    return []


def context_support_example(materials: Sequence[Any]) -> list[dict]:
    """`output_schema.narrative_draft_units[*].context_support` 的示例（`pw-19`）。

    context 边**只能**绑定一份真实 material：本节没有材料行时（纯 `FinancialFactPack` 节，
    精确材料清单合法为空），这一格没有可展开的边，唯一合法形状是**空数组**。写占位的材料行
    （`m1` / `<materials 某一行的 ref>`）在这一档里是示范一条注定被拒的边——那正是本批修掉的
    第二处「示例与自己的输入面矛盾」。
    """
    if materials:
        return [{"ref": _FACTUAL_SUPPORT_EXAMPLE_MATERIAL_REF}]
    return []


def _assert_example_consistent(example: Mapping[str, Any], *, materials: Sequence[Any],
                               facts: Sequence[Any]) -> None:
    """整张 `output_schema` 示例必须是**本请求输入面下合法**的——构造时判，不等模型返回。

    `pw-18` 判草稿那一条轴；这一条把同一判据覆盖到示例的**其余三格**（候选首边、context 边、
    补件示例）。四格共用一句话：**示例指向的表，必须是本请求真的给了行的表**。少判任何一格，
    就留下一条「照抄示例即被同一份请求判违规」的路。
    """
    has_materials, has_facts = bool(materials), bool(facts)
    _assert_prose_example_consistent(example.get("natural_prose_draft") or [],
                                     materials=materials, facts=facts)
    where = "output_schema 的示例"
    support = list((example.get("claim_candidates") or [{}])[0].get("support") or ())
    if has_materials or has_facts:
        if not support:
            raise PackWriterError(
                f"{where}：候选示例必须给出一条支撑边（本请求至少有一张可用的表："
                f"materials {len(materials)} 行 / authority_facts {len(facts)} 行）")
        ref = str(support[0].get("ref") or "") if isinstance(support[0], Mapping) else ""
        if not ref.strip():
            raise PackWriterError(f"{where}：候选示例的首条支撑边没有 ref（空占位等于没写来源）")
        # 判据只有一条：示例指向的那张表，必须是本请求**真的给了行**的那一张。指到空表，
        # 模型照抄就得到一条无从展开的边（`f1` 在没有事实行的节里、`m1` 在没有材料行的节里）。
        if "authority_facts" in ref:
            if not has_facts:
                raise PackWriterError(
                    f"{where}：候选示例的首条支撑边 {ref!r} 指向 authority_facts，而本请求"
                    "`authority_facts` 为空——路径 A 在这里无从展开（示例必须指向有行的那张表）")
        elif "materials" in ref:
            if not has_materials:
                raise PackWriterError(
                    f"{where}：候选示例的首条支撑边 {ref!r} 指向 materials，而本请求 `materials` "
                    "为空——路径 B 在这里无从展开（示例必须指向有行的那张表）")
        else:
            raise PackWriterError(
                f"{where}：候选示例的首条支撑边 {ref!r} 既没有指向 `authority_facts`、也没有"
                "指向 `materials`——支撑边只能绑这两张表里的行")
    elif support:
        raise PackWriterError(
            f"{where}：`materials` 与 `authority_facts` **都**为空时，候选示例不得给出一条"
            "无从展开的支撑边（这一档没有可用内容）")
    context = list((example.get("narrative_draft_units") or [{}])[0].get("context_support") or ())
    if context and not has_materials:
        raise PackWriterError(
            f"{where}：context 示例给了一条 context 边，而本请求 `materials` 为空——"
            "context 边只能绑定一份真实材料，没有材料行就没有可展开的边（这一档写空数组）")
    if context:
        ref = str(context[0].get("ref") or "")
        if not ref.strip():
            raise PackWriterError(f"{where}：context 示例的 ref 是空占位（等于没写来源）")
        if "materials" not in ref:
            raise PackWriterError(
                f"{where}：context 示例的 ref {ref!r} 没有指向 `materials`——context_only 边"
                "只能绑定一份真实 material，不得指向权威事实")
    follow_ups = list(example.get("follow_up_needs") or ())
    for index, spec in enumerate(follow_ups, 1):
        if not isinstance(spec, Mapping):
            raise PackWriterError(f"{where}：补件示例第 {index} 条不是对象")
        budget = spec.get("budget_hint")
        if not (isinstance(budget, str) and budget.strip()):
            raise PackWriterError(
                f"{where}：补件示例第 {index} 条的 budget_hint 为空——诉求要进入检索，而检索"
                "侧对空白字段是 fail-closed 的；示例在这里写空串，等于示范一条注定执行不了"
                "的申请（这是 `pw-19` 修掉的第三处）")
        for key in ("target_requirement_id", "topic_id", "question_id", "aspect_id"):
            value = str(spec.get(key) or "")
            if "requestable_aspects" not in value:
                raise PackWriterError(
                    f"{where}：补件示例第 {index} 条的 {key} 没有指向 `requestable_aspects`"
                    "（四个 id 必须逐字取自该目录的**同一行**；`authority_facts` 行里的同名"
                    "`target_requirement_id` 可能是空串，不得作为来源）")


def _assert_prose_example_consistent(example: Sequence[Any], *, materials: Sequence[Any],
                                     facts: Sequence[Any]) -> None:
    """示例必须是**本请求输入面下合法**的一条草稿单元——在**构造请求时**判，不等模型返回。

    `pw-16` 就地判的是「请求的键集/键序必须与解析面一致」；这一条判的是**键值**：示例声明的
    那条出处轴，必须是本请求真实的输入面能展开的那一条。两者同一条纪律——一份自相矛盾的请求
    根本不该发出去（发出去只能是模型照着写、然后被自己的请求判违规）。
    """
    has_materials, has_facts = bool(materials), bool(facts)
    where = "output_schema.natural_prose_draft 的示例"
    units = list(example or ())
    if not has_materials and not has_facts:
        if units:
            raise PackWriterError(
                f"{where}不得给出一条无从展开的出处轴，而本请求的 `materials` 与 "
                "`authority_facts` **都**为空（这一档没有可用内容：草稿与候选都留空，"
                "所需材料走 follow_up_needs）")
        return
    if not units:
        raise PackWriterError(
            f"{where}不得为空：本请求至少有一张可用的表"
            f"（materials {len(materials)} 行 / authority_facts {len(facts)} 行）")
    for index, unit in enumerate(units, 1):
        if not isinstance(unit, Mapping):
            raise PackWriterError(f"{where} 第 {index} 条不是对象：实为 {type(unit).__name__}")
        members = list(unit.get("source_member_refs") or ())
        fact_refs = list(unit.get("source_fact_refs") or ())
        if bool(members) == bool(fact_refs):
            raise PackWriterError(
                f"{where} 第 {index} 条必须**恰好**声明一条出处轴（两条都写或都空都会被本请求"
                f"自己的解析面整批拒绝），实测 source_member_refs={members!r} / "
                f"source_fact_refs={fact_refs!r}")
        for name, refs in (("source_member_refs", members), ("source_fact_refs", fact_refs)):
            if refs and not all(isinstance(r, str) and r.strip() for r in refs):
                raise PackWriterError(f"{where} 第 {index} 条的 {name} 含空占位：{refs!r}")
        if bool(members) and not has_materials:
            raise PackWriterError(
                f"{where} 第 {index} 条用的是材料轴，而本请求 `materials` 为空："
                "本节只有权威事实行，材料轴在这里无从展开（示例必须与本请求的输入面一致）")
        if bool(fact_refs) and has_materials:
            # rules 逐字写着「本节有材料行时用 `source_member_refs`（materials 的 ref）」：
            # 有材料行时事实轴**不是**合法选择，示例走它同样是在示范一条与 rules 矛盾的路。
            raise PackWriterError(
                f"{where} 第 {index} 条用的是事实轴，而本请求 `materials` 非空"
                f"（{len(materials)} 行）：有材料行时示例必须走材料轴"
                "（示例必须与本请求的输入面一致）")
        if bool(fact_refs) and not has_facts:
            raise PackWriterError(
                f"{where} 第 {index} 条用的是事实轴，而本请求 `authority_facts` 为空："
                "事实轴在这里无从展开（示例必须与本请求的输入面一致）")
        if not list(unit.get("atom_candidate_keys") or ()):
            raise PackWriterError(
                f"{where} 第 {index} 条没有 atom_candidate_keys：草稿必须先于候选被读出来，"
                "示例至少要点出一个占位候选键")


def _batch_support_scope(*, batch: WriterPlanBatch, scan: AuthorityScan,
                         materials_payload: Sequence[Mapping[str, Any]],
                         facts_payload: Sequence[Mapping[str, Any]]) -> dict:
    """本批的**支撑范围**读数（`bscope-1`）——**只**由确定性输入派生，无一格来自模型自报。

    三样东西，各自都有单一的真值来源：
      * `topics[].material_refs`：本批各 topic 下**可引用的材料行**。材料在 Pack/清单侧只有
        topic 归属（`member.topic_id`），因此这一格是 topic 级的——**不是** aspect 级的
        「这一栏有没有可用行」。后者的名称与形状都回不到任何确定性来源（材料没有 aspect
        字段、`status` 说的是研究侧的达成度而非本批的可引用性），硬造一个只会把「整节有材料」
        重新包装成「本批有行可用」；见 `BATCH_SCOPE_RULE` 上面那段实测记录。
      * `aspects[].status`：**逐字**取研究侧自己记录的那一个（`scan.aspect_status`），原样投出。
        它不是本块的判断，也不被本块改写或汇总。
      * `aspects[].fact_refs`：绑定该 aspect 的权威事实行（`AuthorityFactEntry.aspect_ids` 是
        权威自己声明的归属，这一格因此是**逐 aspect** 精确的）。

    `ref` 一律取自两张选项表**已声明**的短别名（`_declare_option_refs` 的产物），所以本块列出的
    每一行都必然可回查、可引用；不在这里另算一套 ref。
    """
    by_topic: dict[str, list[str]] = {}
    for row in materials_payload:
        by_topic.setdefault(str(row.get("topic_id") or ""), []).append(str(row["ref"]))
    by_aspect: dict[str, list[str]] = {}
    for index, entry in enumerate(scan.facts):
        ref = str(facts_payload[index]["ref"])
        for aspect_id in entry.aspect_ids:
            by_aspect.setdefault(str(aspect_id), []).append(ref)
    topic_of_aspect = dict(scan.aspect_topic)
    return {
        "policy_version": BATCH_SCOPE_POLICY_VERSION,
        "rule": BATCH_SCOPE_RULE,
        "topics": [{"topic_id": str(topic_id),
                    "material_refs": sorted(by_topic.get(str(topic_id), []))}
                   for topic_id in batch.topic_ids],
        "aspects": [{"aspect_id": str(aspect_id),
                     "topic_id": str(topic_of_aspect.get(str(aspect_id), "")),
                     "status": str(scan.aspect_status.get(str(aspect_id), "")),
                     "fact_refs": sorted(by_aspect.get(str(aspect_id), []))}
                    for aspect_id in batch.aspect_ids],
    }


def build_narration_messages(*, task: PS.SectionTask, authority: Any, scan: AuthorityScan,
                             projection: ContractProjection, projection_aspects: Sequence[str],
                             unresolved: Sequence[Any], presentation_profile: Any,
                             manifest: NS.WriterMaterialManifest,
                             material_context: Any = None,
                             batch: WriterPlanBatch | None = None,
                             ) -> tuple[list[dict], str]:
    """门前唯一的 LLM 输入：**权威事实目录 + 精确材料清单（含真实正文）+ 缺口** → 候选提案请求。

    输入里只有「可选项」（权威事实的逐行读视图、每份材料的**真实正文**与表格读视图）。所有
    身份字段（proposal/source/provenance/content/payload/locator/manifest）都不在输入里，也
    **不在**输出面里：模型只能选行与给文本，身份由写入侧确定性派生（P6/P18）。

    §三 A.5：材料侧**必须**给正文/表格读视图。只给 material ID 会让路径 B 变成对着一串代号
    编内容——那不是「材料派生」，而是凭空生成（P6 只到接口骨架的直接原因）。

    §二 分批请求：给了 `batch` 时（调用方按 `plan_aspect_batches` 的划分逐批调用），只有
    `projection_aspects` 收窄成**本批**要回答的那些 aspect——`authority_facts` / `materials`
    / `must_use_facts` / `gaps` / `requestable_aspects` 一律是**完整**的那几份。分批是
    **请求面**的切分（这一批回答哪些要求），不是材料面的裁剪；把材料也按批切掉，就是
    「用缩小输入面来让结果看起来对齐」。

    §二（`pw-12`）支撑选项短别名：两张选项表**逐行**带一个 `ref`，支撑边只写 `{"ref": ...}`
    （factual 边另加 `support_role`）。身份字段由 `SupportAliasTable` 从被引用的那一行**确定性
    展开**，与长格式走**同一个**校验器——别名只是「少写身份」的写法，不是第二条合法性口径。
    `must_use_facts` 同样按 `ref` 指认，且**本批**该负责的那一部分在 `batch` 块里单独声明
    （`must_use_fact_refs`）：逐批重复整个义务面会让同一件事在每一批都被写一遍。

    §二 2.4（`pw-14`）**来源角色**：materials 行带 `source_role`（`srsc-1` 的期间位次）。
    O-12 的「同类旧材料不得静默当成当前状态」在生成器那一侧要可执行，角色就必须随行到达——
    这一格由写入侧从来源集台账确定性读出（:func:`_material_source_roles`），不是模型自报，
    也不由模型改写。
    """
    # system 一律取自**版本化 prompt 资产**（`llm/prompts/<prompt_version>.txt`），不在代码里
    # 维护第二份措辞：prompt 的改动必须随批次改版本号并带回归，不能悄悄漂移。
    system = llm.load_prompt(NARRATION_PROMPT_ASSET)
    # 别名表**只有一处派生**：请求侧的 `ref` 与解析侧的展开读同一张表（`_support_aliases`）。
    aliases = _support_aliases(scan, manifest)
    facts_payload = _declare_option_refs([
        {"authority_kind": entry.authority_kind, "container_id": entry.container_identity,
         "fact_id": entry.fact_id, "text": entry.text, "topic_id": entry.topic_id,
         "period": entry.period, "scope": entry.scope, "fact_type": entry.fact_type,
         "required": entry.required, "material_id": entry.material_id,
         "locator_ref": dict(entry.locator_ref) if entry.locator_ref else None,
         "target_requirement_id": str(scan.requirement_ids.get(entry.topic_id, "")),
         # `numbers_in_fact` 只是**该权威事实文本自身的数字**（逐字切片）：把它摆在生成器
         # 面前，是为了让「数字只能逐字照抄」这件事可执行。判定仍在门里，以权威字段为准。
         "numbers_in_fact": list(NS.scan_numeric_tokens(entry.text))}
        for entry in scan.facts], aliases.facts, "authority_facts")
    # 材料侧读视图**逐字**来自已解析正文上下文（`wmctx-1`），字段与 manifest 成员身份一致；
    # 集合不相等即 fail-closed（不得出现「清单里有、正文里没有」的成员）。
    reading_views = _reading_views_for_manifest(manifest, material_context)
    # §二 2.4（`pw-14`）：材料的**来源角色**随行投出（`srsc-1`）。没有它，「同类较旧材料不得写成
    # 当前状态」这条纪律在生成器那一侧不可执行（行里只有来源身份与文档定位，角色读不出来）。
    material_roles = _material_source_roles(authority=authority, manifest=manifest)
    materials_payload = _declare_option_refs([
        {"container_id": member.pack_id, "material_id": member.material_id,
         "member_ref": member.member_ref, "source_identity": member.source_identity,
         # 这一份材料在**本系列里的期间位次**（`current_state_source` / `history_and_conflict_source`
         # / `topic_participating_source` / `not_used`）；不在文档系列这条轴上的材料行为 `None`。
         "source_role": material_roles.get(member.member_ref),
         "topic_id": member.topic_id, "material_type": member.material_type,
         "locator_ref": dict(member.locator_ref), "payload_hash": member.payload_hash,
         "content_fingerprint": member.material_content_fingerprint,
         # §二 2.3：这一份材料**是什么形态**。`kind=text` 是普通叙述材料；`kind=selection_form`
         # 是勾选表单行——它带着所问事项/选项/选中状态/所在节点/来源/尾随内容六列
         # （第六列 `trailing_content` 逐字留档但**不得**用作支撑），以及**允许用途**与
         # **排除项**。`text` 一格不改（仍是抽取式原文，符号字形仍在），但模型由此知道：
         # 这一行只在它自己问的那件事上说话，不得原样充当经营正文，也不得凭父章节替宽栏目作证。
         "content_qualification": reading_views[member.member_ref][2],
         # 正文：模型只能「引用/概括」它，不得新增其中不存在的数字、日期、期间、实体或结论。
         "text": reading_views[member.member_ref][0],
         "structured": reading_views[member.member_ref][1]}
        for member in manifest.entries], aliases.materials, "materials")
    gaps_payload = [{"unresolved_id": u.unresolved_id, "topic_id": u.topic_id,
                     "state": u.state, "reason_code": u.reason_code, "detail": u.detail}
                    for u in unresolved]
    # `pw-18`：`output_schema.natural_prose_draft` 的示例**由本请求的输入面决定**，并在**发出去之前**
    # 就地核对：示例与同一份请求的 rules 矛盾（两条轴同写/同空、指了本请求没声明的那条轴），
    # 与 `pw-16` 的「示例键集与 rules 不同」是同一类「请求自己判自己违规」——都必须在构造侧 fail。
    # `pw-19`：同一判据覆盖示例的**其余三格**（候选首边、context 边、补件预算），四格一起判——
    # 只判草稿那一格的话，模型照着**别的**格子抄，照样是被同一份请求判违规。
    prose_example = natural_prose_example(materials_payload, facts_payload)
    support_example = factual_support_example(materials_payload, facts_payload)
    context_example = context_support_example(materials_payload)
    # §三 A / 3.2：呈现视图**唯一**实现在 `PP.presentation_payload`（门后组织器用同一份）。
    # 本模块不再自己列字段名——列错字段名的后果不是报错，而是投影恒为空却看起来「在做投影」。
    profile_payload = _jsonable(PP.presentation_payload(presentation_profile))
    payload = {
        "task": {"task_id": task.task_id, "section_id": task.section_id,
                 "title": task.title, "purpose": task.purpose,
                 "topic_ids": list(task.topic_ids)},
        "projection": {"projection_id": projection.projection_id,
                       "subsection_ids": list(projection.subsection_ids),
                       "aspects": _projection_aspects_payload(projection, projection_aspects,
                                                              scan)},
        "authority": _authority_context(authority),
        "authority_facts": facts_payload,
        "materials": materials_payload,
        # 支撑选项声明：两张表的别名逐行在此列全（**完备**声明）。模型只按 `ref` 选择；
        # 身份字段由写入侧从被引用的那一行展开（`saref-1`）。把它单独列出来的理由是：
        # 「这次请求声明了哪些选项」本身必须是一份可读、可复核的读数——少了它，产物里就只能
        # 从两份长表里反推「模型当时能选的是什么」。
        "support_refs": {
            "policy_version": SUPPORT_ALIAS_POLICY_VERSION,
            "fact_ref_prefix": FACT_REF_PREFIX, "material_ref_prefix": MATERIAL_REF_PREFIX,
            "declared_fact_refs": list(aliases.fact_refs()),
            "declared_material_refs": list(aliases.material_refs()),
            "complete": True,
            "rule": (
                "支撑边只写 {\"ref\": \"<已声明的某一行>\"}（factual 边另加 "
                "support_role）；authority_kind / container_id / fact_id / material_id / "
                "support_semantics / authorization_path **一律不要自己写**——系统按被引用的"
                "那一行确定性展开；自己写这些字段会被拒（不是被忽略）。ref 指不到已声明的行"
                "同样整批被拒。同一个 ref 在任何一批里都指向同一行。"),
        },
        "must_use_facts": [{"ref": aliases.facts[i].ref,
                            "topic_id": str(entry.topic_id)}
                           for i, entry in enumerate(scan.facts) if entry.required],
        "gaps": gaps_payload,
        # 每个 topic 的真实 `TopicResearchRequirement` id：补件申请必须**逐字**回指它。
        "requirement_ids": dict(sorted(scan.requirement_ids.items())),
        # §六：可申请补件的 aspect 目录（只读）。补件申请必须同时给出 topic_id / question_id /
        # aspect_id / target_requirement_id，而这三者分散在 requirement_ids（按 topic）与
        # projection.aspects（扁平 id 表）里——不给目录，模型只能猜，猜错即 fail-closed。
        # 目录**不含**任何事实、材料或授权：它只说明「可以为什么申请材料」。
        "requestable_aspects": _requestable_aspects(scan, task),
        "presentation_profile": profile_payload,
        # §6.4：输出面里没有 SectionClaim / citation / accepted binding / 最终 Narrative /
        # SectionResult / disposition 的位置，因此「模型产出定稿对象」在结构上不可表达。
        "output_schema": {
            # `pw-16`：草稿层是输出面的**第一个**键（与 `_PLAN_KEYS` 逐位同序）。
            # `pw-18`：它的示例**按本请求的输入面**选那条合法轴（两条轴互斥、恰有一条非空），
            # 理由与三种情形见 `natural_prose_example`；示例本身在下面就地核对——示例与自己的
            # 输入面矛盾，与 `pw-16` 的键集矛盾是同一类「请求自己判自己违规」。
            "natural_prose_draft": prose_example,
            "claim_candidates": [{
                "candidate_key": "c1",
                "claim_text": "<恰好一个原子断言的文本>",
                # 支撑边只写 `ref`（+ factual 边的 support_role）。其余身份字段一律由系统按
                # 被引用的那一行展开——写出来会被拒（见 support_refs.rule 与 rules）。
                # `pw-19`：这一格同样**按本请求的输入面**取（有权威事实行 ⇒ 事实行示例；没有
                # 事实行、有材料行 ⇒ 材料行示例；两张表都空 ⇒ 空数组），见 `factual_support_example`。
                "support": support_example}],
            "narrative_draft_units": [{
                "unit_key": "u1", "unit_kind": "paragraph|table",
                "text": "<草稿单元文本>",
                # `pw-19`：context 边只能绑定一份真实 material；本节没有材料行时（纯
                # `FinancialFactPack` 节）这一格唯一合法形状是空数组，见 `context_support_example`。
                "context_support": context_example}],
            "follow_up_needs": [{
                "statement": "<需要什么材料、用来支撑什么>",
                # 四个 id 必须逐字取自 requestable_aspects 的**同一行**：topic_id /
                # question_id / aspect_id / target_requirement_id 任意一个对不上都会被拒。
                "target_requirement_id": "<requestable_aspects 该行的 target_requirement_id>",
                "topic_id": "<requestable_aspects 该行的 topic_id>",
                "question_id": "<requestable_aspects 该行的 question_id>",
                "aspect_id": "<requestable_aspects 该行的 aspect_id>",
                "requiredness": "required|optional",
                "expected_source_class": "company_industry|structured_db|external",
                # 非空：诉求要**进入检索**，而检索侧对「空字段的诉求」是 fail-closed 的
                # （`TR.build_follow_up_focus`）。空串在这里不是「没有偏好」，是「这条诉求
                # 注定执行不了」——照抄这个例子会白写一条申请。
                "budget_hint": "tree_inspect:1"}],
        },
        "rules": [
            "你不输出定稿对象：你的输出里没有、也不允许有 SectionClaim / citation / "
            "accepted binding / 最终 Narrative（句子/段落/表格行）/ SectionResult / 任何 "
            "disposition 或决定字段；那些由系统在你之后完成。",
            "候选与草稿单元只接受输出面里的字段；**不得输出 output_schema 之外的字段**。",
            "**先写草稿、再逐原子提交候选**：`natural_prose_draft` 是输出的第一个键，它是读者"
            "将要读到的那段话本身；候选是事后为草稿里的每一个事实原子补的账，"
            "`atom_candidate_keys` 指向本束的候选键，必须覆盖草稿声明的全部原子。每个草稿单元"
            "**恰好**声明一条出处轴：本节有材料行时用 `source_member_refs`（materials 的 "
            "ref）；本节**只有权威事实行、没有材料行**时（例如财务节）用 `source_fact_refs`"
            "（authority_facts 的 ref）。两条轴互斥，恰有一条非空。`output_schema` 里"
            "`natural_prose_draft` 的示例**按本节实际给的输入面**取其中一条合法轴，它示范的是"
            "形状而不是内容：照抄它的 ref 没有意义，两条轴都写或都空都会被整批拒绝。"
            "若本节 materials 与 authority_facts **都为空**（没有可写的行），该示例为空数组："
            "此时草稿与候选也都留空，把所需材料写进 `follow_up_needs`——不要凭空写正文。"
            "**出处不是授权**："
            "`source_fact_refs` 只说这段文字从哪条事实行长出来，那条事实能不能写进正文，仍然"
            "只由它自己的候选与支撑边决定。有候选而**没有**草稿的返回会被整批拒绝"
            "（不会按候选拼成正文）。",
            "每条候选**恰好一个原子断言**；一句话可以绑多条权威事实，但不得把两个独立断言"
            "塞进同一条候选。每条候选的 support 至少一条，且第一条必须是 primary。",
            "支撑边只写 {\"ref\": \"<support_refs 已声明的某一行>\"}（factual 边另加 "
            "support_role）。authority_kind / container_id / fact_id / material_id / "
            "support_semantics / authorization_path **一律不要自己写**——系统按被引用的那一行"
            "确定性展开；自己写这些字段会被**拒**（不是被忽略），ref 指不到已声明的行同样"
            "整批被拒。",
            "路径 A（path_a_prevalidated）必须绑定 authority_facts 里的一行（用它的 ref），"
            "**不得**自带 material_id。含数字、日期、币种、比例的断言只能走这条路，且必须逐字"
            "照抄该权威事实的写法——改写写法或自造数值，整个候选束会被拒。",
            "路径 B（path_b_material_derived）只能绑定 materials 里的一行（用它的 ref），"
            "不得携带任何事实身份，也不得携带任何数字/日期/币种。",
            "context_only 只能挂在 narrative_draft_units 上，必须绑定一份真实 material"
            "（用它的 ref），且不得携带事实身份：context 材料只验证背景与衔接，不授权事实，"
            "也不得从中推出数字或结论。本节**没有材料行**时（materials 为空数组，例如只有"
            "权威事实的财务节）这一格**写空数组**——那一档没有任何可展开的 context 边，"
            "占位的材料 ref 会被拒。",
            "候选与草稿单元的文本里不得出现「报告期」「本报告期」「报告期内」这类未绑定权威"
            "期间的含糊措辞；期间只能逐字来自权威事实自己写着的那一个。",
            "must_use_facts 里的每条权威事实都应被某条候选的路径 A 支撑引用"
            "（分批时只对本批那一部分负责，见 batch）；确实无法引用时不要编造——把它如实留在"
            "缺口一侧，或为它发出 follow_up_needs。",
            "materials 是本次写作消费的**精确**材料清单：清单里的每一份材料都会被系统记录"
            "处理去向（用到 / 未用到）。不需要更多材料时给空数组，不要为了「看起来完整」而"
            "提出申请。",
            "follow_up_needs 的四个 id 必须逐字取自 requestable_aspects 的**同一行**，"
            "budget_hint 不得为空（空字段的诉求进不了检索）。",
        ],
    }
    # `pw-16`：请求里的输出面与解析面的**键集与键序**必须逐字一致，就地判、不靠人记。
    # `pw-15` 的现场缺陷正是这一条没人判：prompt 资产要求先输出 `natural_prose_draft`、
    # `_PLAN_KEYS` 也按四键解析，而**这一份请求**的 `output_schema` 只列了三个键，同时它的
    # rules 逐字写着「不得输出 output_schema 之外的字段」——模型照 prompt 写即被同一份请求
    # 判违规，不照 prompt 写就没有草稿。两条路都错，而错的是请求自己。
    # 断言放在**构造请求时**（不是等模型返回）：一份自相矛盾的请求根本不该发出去。
    # `pw-19`：同一处再判一遍示例的**值**——四格（草稿轴、候选首边、context 边、补件预算）
    # 指向的表/目录，必须是本请求真的给过的那一张。键集对了而值指错表，模型照样无路可走。
    _assert_example_consistent(payload["output_schema"], materials=materials_payload,
                               facts=facts_payload)
    if tuple(payload["output_schema"]) != _PLAN_KEYS:
        raise PackWriterError(
            f"请求的 output_schema 键序 {tuple(payload['output_schema'])} 与解析面的 "
            f"_PLAN_KEYS {_PLAN_KEYS} 不一致：同一份请求要求模型输出的键，必须就是解析器"
            "接受的那几个键、且顺序相同（否则 prompt 的指令与 output_schema 的封闭声明"
            "互相判违规，模型无路可走）")
    if batch is not None and batch.aspect_ids:
        # 本批的范围声明。它**只**限定「这一批要回答哪些要求」：事实目录、材料清单、
        # must_use_facts、gaps、requestable_aspects 都在上面原样给足（见 docstring）。
        #
        # 零 aspect 的那一批**不发**这一块：本节没有可划分的要求时，一个空的 `batch.aspect_ids`
        # 会被读成「本批什么都不用回答」，而那正好是当时最不能给模型的暗示。此时请求形状回到
        # 「整节一次」（与分批引入之前的请求同形），提交的仍是同一份完整目录与材料。
        # **按批结算的那一部分**（`pw-12`）：必须事实里属于本批 topic 的那些。
        # 它是一个**声明的子集**（⊆ above 的 must_use_facts），不是让模型自己按 topic 推——
        # 推出来的义务面不可复核，声明出来的可以。「每批只回答本批栏目」与「每批重复承担
        # 全节必用事实」的矛盾就在这里解开：事实目录仍完整（输入面不裁），义务按批结算。
        batch_topic_ids = set(batch.topic_ids)
        payload["batch"] = {
            "policy_version": ASPECT_BATCH_POLICY_VERSION,
            "batch_id": batch.batch_id, "index": batch.index, "total": batch.total,
            "label": batch.label(), "aspect_ids": list(batch.aspect_ids),
            "topic_ids": list(batch.topic_ids),
            "must_use_fact_refs": [row["ref"] for row in payload["must_use_facts"]
                                   if str(row["topic_id"]) in batch_topic_ids],
            "shrink_depth": batch.shrink_depth, "parent_batch_id": batch.parent_batch_id,
            "scope_rule": (
                "本批只回答 projection.aspects 里列出的这些要求（它们就是本批的 aspect）；"
                "authority_facts / materials 仍是本节**完整**的目录与正文，可据它们引用与概括；"
                "本批不要为本批之外的 aspect 输出候选——它们由其它批次回答，你多写或少写都会"
                "让整节的覆盖出现重复或缺口；narrative_draft_units 与 follow_up_needs 也"
                "**按本批结算**：只为 batch.topic_ids 里的 topic 写，不要替其它批次再写一遍"
                "（整节的草稿单元与诉求由系统合并取并集，重复不会让内容更全，只会占掉本批的"
                "输出容量）；must_use_fact_refs 是本批该负责的必须事实，其余必须事实由拥有"
                "它们的批次引用"),
        }
        payload["rules"].append(
            "本请求是**分批**的（见 batch）：只回答 batch.aspect_ids / projection.aspects "
            "列出的那些要求；材料与事实目录是完整的，可以据其引用；不要替其它批次回答它们"
            "的 aspect。你的输出会与其它批次合并成一份完整提案集，因此本批同样要**写全**"
            "（本批每一个 aspect 都要有着落），但不要为了其它批次的范围产出候选。"
            "**每批只回答本批栏目**：narrative_draft_units 与 follow_up_needs 同样只为"
            "batch.topic_ids 里的 topic 写；must_use_fact_refs 是本批的必须事实上限。")
        # `bscope-1` / `pw-20`：本批**能引用什么**的读数（见 `_batch_support_scope`）。它只摆
        # 事实（本批各 topic 的可引用行、各栏目自己的 `status`、绑定它的权威事实行），不替模型
        # 判断「这一栏写不写得出来」——那个判断是语义的，归模型；它的确定性反例由既有出处轴
        # 判据与 `assert_batch_no_witness_means_empty` 把守。**整节**材料目录在这里仍是完整投出
        # 的（输入面不裁），本块只说明**本批**的范围，因此不与「材料面不按批裁剪」相矛盾。
        payload["batch_support_scope"] = _batch_support_scope(
            batch=batch, scan=scan, materials_payload=materials_payload,
            facts_payload=facts_payload)
        payload["rules"].append(
            "**本批的支撑范围见 batch_support_scope**：草稿单元的出处、候选的支撑边与草稿单元"
            "的 context 边**只能引用本批 topics 里列出的那些行**（引用本批之外 topic 的行会被"
            "拒）；`aspects[].status` 是研究侧原样记录的状态，不构成「可以写成确定结论」的许可。"
            "**本批一条候选都交不出时**，natural_prose_draft / claim_candidates / "
            "narrative_draft_units 三个键一律是空数组，只写 follow_up_needs：没有行可指的缺失"
            "陈述（「本轮未取得 / 无法说明 / 未找到」）不是缺口，只会让本批被拒；正式缺口由系统"
            "按检索轨迹与 Contract 判定。")
    user = json.dumps(payload, ensure_ascii=False)
    return [{"role": "user", "content": user}], system


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

#: 候选提案集**被整批拒绝**的封闭原因词表。每个被拒的**候选**都要能归到其中一条，
#: 且每一条都对应一次「整束未被采信」——不是「原地删掉几个候选、把剩下的当成原来的集合」。
#:
#: 词表刻意**不**包含「返修额度已用尽」：那一情形下整束是**被采信**的，只是带着未修的
#: rework 级问题；那些问题已由 `NarrativeGateResult` 如实留在产物里（`gate_result`），
#: 记进本表只会让 `rejections` 的语义（= 产出过零份 Draft 的尝试）失真。
#: §三 A / 3.3：**栏目分步写作**的专用、有界预算（每轮写作最多 1 次栏目定向调用）。
#:
#: 为什么给它一个**独立**的额度，而不是复用 `WriterPolicy.max_llm_retries`：那个额度同时承担
#: 「输出不合格 → 打回重写」。两者竞争同一个槽位时，一次 JSON 格式抖动就会把「按栏目补足」的
#: 机会吃掉，而这两件事的成因完全不同（一个是模型没写对格式，一个是模型没写到某个栏目）。
#: 独立额度让「栏目定向」这一步**真的可达**，同时仍然是**有界**的：上界 = 每轮写作 1 次。
MAX_SUBSECTION_FOCUS_PASSES = 1

#: C3：**路径 B 面**被拒后，**定向重提案**（把被点名的候选、它们逐字携带的表面/不能承重的
#: 支撑边、以及合法出路逐条写清，再要求模型重新给出**完整**提案集）的次数上界。
#:
#: 覆盖 `REPROPOSAL_TRIGGER_KINDS` 里的**四种**触发原因（高风险面、支撑资格、期间位次、独立
#: 支撑结论），它们**共用**这一份额度：换来的是同一样东西——「完整原请求 + 一段定向说明」的
#: 那一轮重试。按原因各记一份额度，等于把同一轮重试按原因复制成四轮，那是加预算换产出。
#:
#: 它与 `max_llm_retries` 的关系是**预算中性**的：定向重提案**不新增调用额度**，它用的就是
#: 本策略已有的那一轮重试——被拒之后本来就会再问一次，区别只在「那一轮收到的是哪一句话」。
#: 因此每节每轮写作的生成轮数上界仍是
#: `(max_llm_retries + 1) + MAX_SUBSECTION_FOCUS_PASSES`，一个字都不用改（可逐步推出：
#: 定向轮只在 `attempt <= max_llm_retries` 时才被排定，即它必然落在第 1..max_llm_retries+1 轮
#: 之内；栏目定向另加恰好一轮）。额度用尽即整节 typed fail-closed，不再借通用重试通道重问。
MAX_DIRECTED_REPROPOSAL_PASSES = 1

#: 定向重提案说明的版本号。说明文本是**输入面**的一部分（它逐字进入下一轮每一批的请求），
#: 因此它必须可版本化：改一句话就是改一次输入纪律，账本上要能区分。
#: `hrrp-2` 相对 `hrrp-1` 的差别：说明里多一组「支撑边绑定了只在自己所问事项上说话的表单行」
#: 的逐候选点名，以及对应的第四条出路（把叙述收回该行所问事项内，或改绑权威事实）。
#: `hrrp-3` 相对 `hrrp-2` 的差别：再多一组「这条候选的支撑边**全部**落在同类较旧（或期间不可
#: 核实）的来源上，而它自己没写期间」的逐候选点名，以及对应的第五条出路（改绑权威事实，或
#: 撤下）——O-12 的期间/来源角色核对。
#: `hrrp-4` 相对 `hrrp-3` 的差别：再多一组「这条候选**有**当前锚边、却没有一条这样的边能核实
#: 这条命题」的逐候选点名（`path_b_unproven_current_state`），以及对应的**第 4b 条出路**——
#: 把措辞**逐字收回到那一份材料的原文**（继续禁止拼接两份材料、换标点、把差异改回去）。这一组
#: 与 `hrrp-3` 那组**互斥**，不会同时出现在同一条候选上（见 `_directed_reproposal_note`）。
#: `hrrp-5` 相对 `hrrp-4` 的差别：那一组点名**逐条带上原因码**，并把
#: `source_period_scope_dropped`（源句自带期间/范围限定、候选把它截掉了）单列一条出路——
#: 「把限定语一起收进候选」即可，**不必**改绑、**不必**撤下。这是输入面的实质变化：`hrrp-4`
#: 下这一形态与「压根没有材料包含它」写在同一条里，模型最省力的读法是按 4b 删掉它。
HIGH_RISK_REPROPOSAL_NOTE_VERSION = "hrrp-5"

#: C3：一条原候选在**下一修订**里的去向，封闭词表。它只回答「还在不在、还是不是同一类型」，
#: 不回答「改成了什么」：
#:   * `carried_verbatim`：下一修订里恰好有一条**逐字同文同类型**的候选（`claim_text` 与
#:     `fact_type` 都相同）——它被原样重新提出；
#:   * `carried_rebound`：下一修订里恰好有一条**逐字同文、但事实类型不同**的候选——同一个原子
#:     被重新提出，但它的支撑边换了一类（例如从材料派生改绑权威事实）；
#:   * `carried_ambiguous`：下一修订里有**两条以上**逐字同文的候选（事实类型不同）——此时
#:     「原样」还是「换了绑定」**不可机械判定**，只如实列出，不猜；
#:   * `not_reexpressed`：下一修订里没有任何逐字同文的候选——它**没有**被重新提出。
#: 「被改写成别的句子」**不在**本词表里：改写与否不是机械可判的（任何不同文本都可能是任何
#: 文本的改写），凭相似度编一档就是把猜测写成审计。
CANDIDATE_REPROPOSAL_DESTINATIONS = (
    "carried_verbatim",
    "carried_rebound",
    "carried_ambiguous",
    "not_reexpressed",
)

#: 触发定向重提案的整束拒绝原因。**恰好两种**，判据是同一条：这一类拒绝有「哪几条候选、
#: 踩了什么线」的逐候选定向内容可写，且被点名的候选**自己**能改（改绑路径 A 或撤下）。
#:
#:   * `path_b_high_risk_surface`：候选**文本自己**带了高风险表面；
#:   * `path_b_ineligible_material_scope`：候选的支撑边绑定了一张只在所问事项上说话的表单行，
#:     而候选文本超出了那一行的所问事项；
#:   * `path_b_history_only_current_state`：候选的支撑边**全部**落在不能表达当前状态的来源
#:     角色上（同类较旧 / 同类而期间不可核实），而候选文本没写期间（O-12）。它同样有可逐条
#:     写出的定向内容与被点名候选**自己**能改的出路（改绑权威事实，或撤下），因此同属本表。
#:
#: 别的 kind 不进本表：`schema_invalid` 没有可信的逐候选身份；栏目零产出、硬门规则、截断
#: 的原因都不在候选自己身上。把它们也算进来，就是借「定向」之名对所有失败重问一遍。
REPROPOSAL_TRIGGER_KINDS = ("path_b_high_risk_surface", "path_b_ineligible_material_scope",
                            "path_b_history_only_current_state",
                            "path_b_unproven_current_state")

#: `cco-6`：可以做**逐项裁出**的整束拒绝原因。它**不是** `REPROPOSAL_TRIGGER_KINDS` 的别名——
#: 两者恰好在这一条上分叉：`narrative_gate_blocking` 有裁出内容可写（哪几个 context 单元、
#: 踩了哪条门规则、命中了哪几个字），却**没有**定向重提案的内容可写。
#:
#: 为什么是分开的两张表而不是一张：`REPROPOSAL_TRIGGER_KINDS` 回答的是「这一类拒绝的出路是不是
#: **再问一轮**」——答案必须是「被点名的对象自己**能改**」（改绑路径 A 或撤下）。硬门 blocking
#: 里被点名的是**一段 context 衔接文字**：它不是一条断言，没有「改绑」这条出路，也没有「下一条
#: 断言」可写，因此把它塞进那张表会凭空长出一条「让模型重写整束」的调用；而它确实有**逐项**的
#: 内容可写，因此它进本表。两张表因为不同的判据分叉，就不得合并成一张「都行」的表。
CARVE_OUT_ELIGIBLE_KINDS = REPROPOSAL_TRIGGER_KINDS + ("narrative_gate_blocking",)

#: §一.2 补充裁决：**逐候选裁出**的规则版本。裁出 = 把原束里不合格的那几条**逐条**拒掉，
#: 让其余合格的候选以**新修订**继续走链（`_build_pre_gate_bundle` 重新派生候选/支撑边/身份）。
#:
#: 它不是定向重提案的别名，两者的因果方向**相反**：
#:   * 定向重提案：整束作废 → **请求**下一轮重出 → 去向按逐字 `claim_text` 机械比对（可能猜不中）；
#:   * 逐候选裁出：整束**不**作废 → 不请求重出 → 每条原候选的去向**必然**确定（幸存 / 带原因排除），
#:     因此不需要任何「可能没被重新提出」的那一档。
#:
#: 三条不变量（`CandidateCarveOutTrace.__post_init__` 逐条强制）：
#:   ① 去向表键集**恰好**等于原束完整有序候选身份；② 每条恰好「带排除原因」或「带新修订身份」；
#:   ③ `model_calls_added == 0`——裁出不新增任何模型调用，被裁出者的原文仍存于原束那次调用日志。
#:
#: 幸存数为 0 时**不走本路径**：那与整束拒绝是同一件事，退回既有 C3 / fail-closed，一个字不改。
#:
#: `cco-2` 相对 `cco-1` 的差别：裁出与逐候选去向多认一类原因
#: （`path_b_history_only_current_state`，O-12 的期间/来源角色核对）。裁出本身一个字没改：
#: 仍然只做「从有序列表里去掉被点名的那几项」，候选文本、支撑边、narrative 单元、补件诉求
#: 全部原对象引用。
#:
#: `cco-3` 相对 `cco-2` 的差别：逐候选去向**再**多认一类原因
#: （`path_b_unproven_current_state`，`srsc-2` 的独立支撑结论核对），并多记一项逐候选证据
#: （`unproven_current_state_member_refs`）。裁出机制本身仍然一个字没改。
#:
#: **为什么必须换版本号**：这个串进 `CandidateCarveOutDecision`，而决定的内容身份含 `excluded`
#: 的逐条原因。两份**原因词表不同**的决定共用一个版本串，读的人就无法从版本号判断
#: 「这条 `path_b_unproven_current_state` 是不是这一版认识的」——判定集变了就必须换号，
#: 这与 `pw-14` 那边「判据没变、只是接线」的结论不冲突：那一条是本批**没有**改判定集，
#: 这一条是**改了**。
#:
#: `cco-4` 相对 `cco-3` 的差别：裁出**连带改动自然草稿层**（`pw-15`）。裁决多一张逐单元的去向
#: 表（`prose_destinations`：哪一段文字少了哪几条原子、还剩几条、整段被撤下时原文逐字留档），
#: 且 `to_revision` 必须由**裁出后的**提案集重算（草稿层进了修订，裁出会改变草稿内容）。
#: 被裁候选**不再只是候选集的一次删除**：它同时是一段已经写出来的散文的部分撤回，因此
#: 「裁了什么」必须逐单元可读。三条判据（高风险面 / 适用范围 / 期间资格）与原因词表一字未变。
#: `cco-5` 相对 `cco-4` 的差别：`path_b_unproven_current_state` 那一项多带一个**类型化原因码**
#: （`unproven_current_state_cause_code`，闭集 `UNPROVEN_CURRENT_STATE_CAUSES`）。理由与上面
#: 「判定集变了就必须换号」同源，只是这次变的不是**词表**而是**同一条原因下的分级**：`srsc-2`
#: 只回答「证不出来」，`srsc-3` 起还要回答「是材料不足，还是措辞口径变了（截掉了源句自带的
#: 期间/范围限定）」。两者在 `cco-4` 的裁决里长得一模一样，而**出路不同**。
#:
#: `cco-6` 相对 `cco-5` 的差别：裁出**多认一类被裁对象**——**context 草稿单元**
#: （`narrative_draft_units`，即 `bundle.units`）。理由与「判定集变了就必须换号」同源：
#: 裁决的内容身份多了一张逐单元去向表（`excluded_context_units` / `source_draft_unit_ids`），
#: 且「至少排除一项」这条不变式的**成立面**从一个（候选）变成两个（候选 ∪ context 单元）——
#: 读的人若继续按 `cco-5` 读，会把一张只有单元去向的裁决读成「一条候选都没被排除」，
#: 那正是 §二 (3) 点名要挡的「旧束删几条后的子集冒充旧束」的镜像形态。
#:
#: **为什么 context 单元需要裁出，而不是继续整束拒**（M930-3 写作链）：`narrative_vague_period`
#: （§十二 4）是一条**文本表面**判据，它同时作用在候选文本与 context 单元文本上。候选文本踩线
#: 说明**一条断言**用了未定义期间顶替——那必须由模型改绑路径 A（预验证权威事实自带期间）或撤下，
#: 裁出不得替它决定，因此**候选一侧一个字不改**（这是「明确否定事实不是背景衔接」那句裁决在
#: 本模块的落点：否定事实只有作为路径 A 的 factual 候选、带上明确主体与期间，才算合法表达）。
#: 而 context 单元按定义只承载背景/结构/衔接、**不承载任何事实身份**（`NarrativeDraftUnit` 没有
#: claim id / fact id / accepted binding id；context 支撑边不授权事实，也不带任何事实身份）。
#: 当 `narrative_vague_period` 点名的是一个 context 单元时，整束拒绝是**过度**的：踩线的是一段
#: 本就不该承载事实的衔接文字，被连带作废的却是同一次返回里完全合格的其他经营描述。此时唯一
#: 合法的出路就是**把这段文字及其 context 支撑提案逐项撤下**——不得只删「报告期」三个字、
#: 不得把否定改写成含糊正面句、也不得让 context 补足 factual 授权（三条在**别处**都是既有的
#: fail-closed 判据，本出口一个都不碰）。
#:
#: 反向边界（缺一条即整束 fail-closed，一个字不改）：**只要还有一条 blocking 问题不是
#: 「`narrative_vague_period` 点名某个 context 单元」这一形状**，就不得裁出。因此候选文本踩线、
#: 任何别的门规则、以及任何非门规则的阻断，全部照旧整束 fail-closed。裁出**不放宽**该项判据：
#: 新修订上重跑的还是同一个门、同一份字面判据，被撤下的只是那几段文字。
CANDIDATE_CARVE_OUT_VERSION = "cco-6"

#: 门前留存的载荷版本（**指令 D §三**）。
#:
#: **它解决的是哪一件事**：一整轮分批扫描里，只要**有一批**的返回不合法、而这一轮的分批缩小／
#: 形状纠正额度又已用尽，整轮就以 `schema_invalid` 终止。终止本身是对的（缺一批 = 提案集不完整
#: ⇒ aggregate 必须 fail），但**终止不得连带抹掉其余批次已经拿到的合法返回**——那些批次与这次
#: 失败毫无关系，而它们承载的正是「模型这一轮到底写出了什么」。真实 run r8 的 company 节正是这样：
#: 4 批里前三批 `status=ok`，第四批的草稿单元没声明出处轴 ⇒ 整轮终止 ⇒ 那三批的草稿正文随异常
#: 一起蒸发，产物里只剩一条 `draft_unit_ids`（还是**失败那一批**的）。
#:
#: **它是什么、不是什么**：它是**失败侧**的一份只读留存（草稿正文 + 逐轴出处 + 标签），随拒绝记录
#: 落盘，人读页逐字标注「未核验、不可发布」。它**不是** `SectionDraft`、**不是**提案集、**不是**
#: 任何判定对象的替代，也**不**参与任何门的判定——被拒的那一束仍然是被拒的。把它读成「这一节其实
#: 有内容」就是把失败读成通过。
PRE_GATE_RETENTION_VERSION = "pgr-1"

#: 留存的**来源**（封闭取值）。三者意思不同，不得合并：
#:   * `merged_bundle`：这一轮的批次**全部**成功并合并成了一份提案集，是**之后**的整束级判定
#:     （高风险面 / 硬门 / 栏目零产出）拒了它。留存的因此是**合并后**的草稿层，逐批归属已不可考。
#:   * `per_batch_before_failure`：某一批的返回不合法（或整轮合并闭合不上），其余批次已合法解析。
#:     留存的是**逐批**的草稿层，批次身份可考。
#:   * `none`：这一轮没有任何一批交出合法返回（首轮即失败、或唯一那批失败）。此时留存为空表——
#:     「无可留存」不是「留存失败」，两者由本字段分开。
PRE_GATE_RETENTION_BASES = ("merged_bundle", "per_batch_before_failure", "none")

#: 「本轮没有任何一批交出合法返回」这一档的**来源码**，单独给一个名字。理由不是省字：
#: 它是**结论性**的那一档（另两档都说「门前有内容可读」，只有它说「没有」），而它是**非空
#: 字符串**——只判真值的读者会把 `"none"` 读成「有留存」，于是把「门前没有可留的东西」印成
#: 「留了一段未核验的内容」。判据必须逐字比这一档，不能只判真假。
PRE_GATE_RETENTION_EMPTY_BASIS = "none"

#: 留存件上**逐字**印出的不可发布标注。只在这一处定义，产物侧不得另写一句措辞。
PRE_GATE_RETENTION_LABEL = "未核验、不可发布"

#: 逐候选裁出的封闭原因词表。刻意**复用** `PROPOSAL_SET_REJECTION_CANDIDATE_REASONS` 的前三项
#: （同一份判据、同一批逐字成分），只**新增**一项：结构非法。
#:
#: 为什么 `candidate_structure_invalid` 在这里可以出现、却不在整束词表里：整束的
#: `schema_invalid` 说的是「整响应连结构化对象都没成形」，那时逐候选归属必须为空；而裁出这一路
#: 是「整响应**已**成为结构化对象（含无损恢复），只有**部分**候选自己结构非法」——那几条候选
#: 的 `claim_text` 与错误串是**可信**的，因此可以逐条记名。两者不是同一条判据的松紧两档，
#: 而是两种不同的失败面：一个在响应层，一个在候选层。
CANDIDATE_CARVE_OUT_REASONS = (
    "path_b_high_risk_surface",
    "path_b_ineligible_material_scope",
    "path_b_history_only_current_state",
    "path_b_unproven_current_state",
    "candidate_structure_invalid",
)

#: `cco-6`：**逐单元**裁出的封闭原因词表（被裁对象是 context 草稿单元，不是候选）。
#:
#: 刻意只有**一项**，而且就是那条门规则 id 本身：本出口要解决的是一件很窄的事——
#: `narrative_vague_period`（§十二 4，无定义期间）点名了一段**不承载任何事实身份**的衔接文字。
#: 词表不开放给别的门规则，也不给「语义相似」留一档：别的规则问的是「这条断言能不能承重」
#: （那必须由候选改绑或撤下回答），不是「这段衔接文字该不该出现」。写成封闭单元素元组的另一个
#: 作用是让「裁出把某条安全门放过去了」在结构上无从表达——能进本表的只有这一条，且它在
#: 新修订上会被**重跑一遍**（裁出不放宽判据，只是撤下文字）。
#:
#: 它与 `CANDIDATE_CARVE_OUT_REASONS` 不共用：原因码各自对应**不同类**的被裁对象，混成一张
#: 表会让「这条原因该出现在哪一侧」变成约定而不是判据。
CONTEXT_UNIT_CARVE_OUT_REASONS = (
    "narrative_vague_period",
)

PROPOSAL_SET_REJECTION_KINDS = (
    #: 输出不是合法 JSON / 顶层键集不符 / 必填空文本 —— 连结构化的候选集都形不成。
    "schema_invalid",
    #: 支撑边在**权威侧闭不上**：容器/材料/事实不在本节权威范围内（束里的身份无法解析）。
    #: 它与 `schema_invalid` 是两件事：那一束的结构是合法的，只是它声明的身份不存在于权威侧。
    "proposal_identity_unresolvable",
    #: 路径 B 候选携带高风险表面（数字/否定/勾选/主体身份/表格关系/因果）。
    "path_b_high_risk_surface",
    #: 路径 B 的支撑边绑定了**只在自己所问事项上说话**的勾选表单行（`asked_item_applicability_only`），
    #: 而这条候选的文本超出了那一行的所问事项。它与 `path_b_high_risk_surface` 是两件事：那一条
    #: 说的是「候选自己带了什么表面」，这一条说的是「这条边本来就不能承重」——材料留着、身份不动，
    #: 被纠正的是**可用作支撑的资格**（§二 2.3；`TREE_MATERIAL_SELECTION_PERMITTED_USE`）。
    "path_b_ineligible_material_scope",
    #: 路径 B 候选的**全部** factual 支撑边都落在「不能表达当前状态」的来源角色上（同类较旧 /
    #: 同类而期间不可核实），而它自己没写期间限定（`DESIGN_V2.md` O-12）。它与上面两条是**三条
    #: 正交的轴**：高风险面问「候选自己带了什么表面」，支撑资格问「这条边本来能不能承重」，
    #: 这一条问「这条边最多能证明到哪个期间」——材料留着、身份不动。
    #:
    #: 为什么它是拒绝而不是「给它补一个期间词」：路径 B 的候选文本里任何期间表面（年份、绝对
    #: 日期）本来就要过上面那条授权面判据，未预验证的期间表面一律打回。在授权面之外另开一条
    #: 「造期间表面」的路，正是本判据不得做的事。因此这张表上的候选只有两条出路：**改绑路径 A
    #: 的预验证权威事实**（那类事实自带自己的期间文本），或**撤下**这条断言。
    "path_b_history_only_current_state",
    #: 路径 B 候选**有**至少一条当前锚边，但候选文本不是任何一条这样的边的正文的严格抽取式
    #: 子串（`srsc-2`）——它与上一条是**互斥**的两侧，且正好把上一条的边界补完：上一条只在
    #: 「一条当前锚边都没有」时开火，于是「**有一条**边落在当前锚上」曾被当成「较新材料已核实
    #: 这条命题」。这一条问的是「那条边真的核实了吗」，判不出来就如实记 `unproven`——材料留着、
    #: 身份不动，被纠正的是这条断言**今天证不出来**，不是候选为假。
    "path_b_unproven_current_state",
    #: 确定性叙事硬门 blocking（含路径 A 授权面）。
    "narrative_gate_blocking",
    #: §三 A / 3.3：这一束**结构合法、身份也闭得上**，但它让某个（些）Contract 投影栏目
    #: 一个候选都没有。它不是「这一束不能采信」，而是「这一束对被点名的栏目没有产出」——
    #: 因此拒它之前必须先把**是哪些栏目、它们的要求是什么**逐条摆出来，第二次调用才可能是
    #: 一次真正的**栏目定向**写作，而不是同一句话再问一遍。
    #:
    #: 记进本表而不是新开一张「soft」表，是为了不让 `rejections = 产出过零份 Draft 的尝试`
    #: 这条语义分裂：第二次调用若被采信，第一次确实零产出。
    "subsection_uncovered",
    #: 草稿闭合核对未通过：这一束**结构合法、身份也闭得上**（候选 / 支撑提案 / 草稿单元
    #: 三份身份齐全），但三者拼不成一份可采信的 `SectionDraft`——原子归属不闭合、草稿单元的
    #: 出处不在本节 manifest 里、单元内部的原子重复、或某个 occurrence 在**它自己那条候选**
    #: 的支撑提案里找不到落点（`NS.validate_natural_prose_mapping`，`npr-1`）。
    #:
    #: 它与 `schema_invalid` 是**两件事**，不得混成一条：那一条说的是「输出还没成为结构化的
    #: 候选集」（连候选身份都没有，逐候选归属只能留空）；这一条的整束**已经是**结构化对象，
    #: 被拒的是「这一束自身不自洽」。也正因如此它**进** `STRUCTURED_PROPOSAL_SET_REJECTION_KINDS`：
    #: 逐候选归属在这里可信且必需。
    #:
    #: 它**不是**「可以裁剪后重试」：裁掉几个单元或删掉重复的声明正是本判据要拦的那一步
    #: （被拦的不是文本写得好不好，而是「候选与草稿对不上」——裁剪只会把对不上的痕迹删掉，
    #: 不会让它对上）。写作侧仍有 `max_llm_retries` 次按问题文本重写的机会，额度用尽即
    #: fail-closed；被拒整束的完整有序身份与模型已提出的补件诉求按原样留作**未核验、不可发布**
    #: 的诊断。
    "natural_draft_not_closed",
    #: §二：某一批的输出被 provider **截断**，且**确定性缩小也救不回来**——额度用尽仍截断，
    #: 或只剩单个 aspect 仍被截断。此时这一轮的结果被拒绝（半截 JSON 永不解析、永不采纳），
    #: 整节 typed fail-closed。它与 `schema_invalid` 是两件事：那一束的文本可能完全合法，
    #: 只是**没写完**；把它们混成一条，产物里就分不清「模型没写对」与「容量不够」。
    #:
    #: 它**不**进 `STRUCTURED_PROPOSAL_SET_REJECTION_KINDS`：截断的输出从未成为结构化对象，
    #: 因此逐候选归属必须为空（不得凭半截文本猜测哪几条候选存在）。「是哪一次调用被截断、
    #: 本批要回答什么、缩到了第几层」由 `batches` 逐条结构化给出。
    "batch_truncated",
)

#: §二 3 / 3.4：**逐候选** typed 审计的封闭原因词表。
#:
#: 为什么要逐候选，而不是「一个 kind 覆盖整束」：真实 run 里一束被拒时，读的人只能看到
#: 一条 `rejection_detail`——于是「20 个候选里**哪几个**踩了高风险面、哪几个被哪条门规则点名」
#: 只能在错误字符串里搜。1 与 20 是两种不同的结论（「整束没写对」 vs 「某几条踩线」），审计
#: 必须能直接回答，而不是让人去解析一段被截断过的文本。
#:
#: 词表刻意**小**且**正交于**整束 kind：这里回答的是「这条候选自己被点了什么名」。
PROPOSAL_SET_REJECTION_CANDIDATE_REASONS = (
    #: 该候选**文本自己**携带高风险表面（路径 B 授权面判据）——`surfaces` 给出逐字成分。
    "path_b_high_risk_surface",
    #: 该候选有一条路径 B 支撑边绑定了「只在自己所问事项上说话」的勾选表单行，而它的文本超出了
    #: 那一行的所问事项——`ineligible_member_refs` 给出是哪几条边。材料本身不动：被纠正的是
    #: 「这条边最多能证明什么」，不是「这份材料存不存在」。
    "path_b_ineligible_material_scope",
    #: 该候选的路径 B factual 支撑边**全部**落在「不能表达当前状态」的来源角色上（同类较旧 /
    #: 同类而期间不可核实），而它的文本没有期间限定——`history_only_member_refs` 给出是哪几条
    #: 边。这条断言按现在的措辞是**旧材料当前化**：读者会把旧期间的状态读成当前状态（O-12）。
    #: 材料本身不动：被纠正的是「这条边最多能证明到哪个期间」。
    "path_b_history_only_current_state",
    #: 该候选**有**至少一条落在能表达当前状态的来源上的路径 B factual 边（因此上一条判据不
    #: 成立），但候选文本**不是**任何一条这样的边的正文的**严格抽取式子串**——`srsc-2`：
    #: 「支撑集里恰好含一份较新的材料」不等于「这条命题被较新材料核实过」。`unproven` 说的是
    #: 「按现行授权判据证不出来」，不是「这条候选是假的」；材料同样留着、身份不动。它与上一条
    #: **互斥**（同一条断言不可能既「一条当前锚边都没有」又「至少有一条」）。
    "path_b_unproven_current_state",
    #: 确定性硬门点名了**这条候选自己**（或它自己的支撑边）——`rule_ids` 给出规则。
    "gate_blocking",
    #: 这条候选**没有被单独点名**。它**不是**「可以保留」：被拒的是**整束**，本原因只表示
    #: 「这一束被拒不是因为它」。例如整束缺了某条候选（栏目零产出）、或整束结构非法/身份闭不上。
    #: 它存在的意义正是把「没有被点名」与「可以留用」在产物里区分开——否则空的 reasons 会被
    #: 读成一张「幸存者名单」，而那正是 §二 (3) 点名的反模式（删掉几个、剩下的冒充原提案集）。
    "not_individually_implicated",
)

#: 这些 `rejection_kind` 下，整束**已经**成为结构化对象，因此逐候选归属**可信且必需**。
#: 其余 kind（`schema_invalid` / `proposal_identity_unresolvable`）下整束从未成为结构化对象，
#: 逐候选归属**不得**凭文本猜测——那时的正确表达是空审计，由 `rejection_kind` 自己说明原因。
STRUCTURED_PROPOSAL_SET_REJECTION_KINDS = (
    "path_b_high_risk_surface",
    "path_b_ineligible_material_scope",
    "path_b_history_only_current_state",
    "path_b_unproven_current_state",
    "narrative_gate_blocking",
    "subsection_uncovered",
    #: 草稿闭合失败时这一束**已经**是结构化对象（候选 / 提案 / 草稿段三份身份都在手上），
    #: 因此逐候选归属必须给全（每束恰好一条，键集恒等于候选身份）。
    "natural_draft_not_closed",
)


def _structured_rejection_kind(kind: str) -> bool:
    """`kind` 是否属于「整束已成结构化对象」的那几种。

    未知 kind **直接抛**，不默认放行任何一侧：默认成 False 会让一条新 kind 悄悄免除逐候选
    审计，默认成 True 会让 `schema_invalid` 那一束被迫编出逐候选原因。两种默认都是猜。
    """
    if kind not in PROPOSAL_SET_REJECTION_KINDS:
        raise PackWriterError(
            f"未知的提案集拒绝原因 {kind!r}，不在 {list(PROPOSAL_SET_REJECTION_KINDS)} 内")
    return kind in STRUCTURED_PROPOSAL_SET_REJECTION_KINDS


@dataclass(frozen=True)
class CandidateReproposalDestination:
    """C3：**一条**原候选在下一修订里的去向（机械可判的那一部分）。

    身份连接**只有**逐字 `claim_text` 相等这一种：`ClaimCandidate.candidate_id` 把
    `draft_revision` 计入身份，而修订是**逐轮不同**的（`_revision_for(attempt)`），因此
    「同一条候选在第 2 次生成里是谁」**不能**用 id 连接。文本相等是唯一确定性、可复算、
    且不需要改动任何身份 wire 的判据——而且这不是新语义：`_merge_batch_plans` 判同一条候选
    用的就是逐字文本（判同一是（`claim_text`, 事实类型））。

    本记录**不**声称「被改写成哪一条」：词表只到「还在不在、类型还一样不一样」为止。
    """

    candidate_id: str
    claim_text: str
    fact_type: str
    destination: str
    #: 下一修订里**逐字同文**的候选（按下一修订原序）。0 条 = 没被重新提出；1 条 = 逐文对应；
    #: ≥2 条 = 同文歧义（此时 destination 必须是 `carried_ambiguous`）。
    next_candidate_ids: tuple[str, ...] = ()
    #: 与 `next_candidate_ids` 逐位对应的事实类型（用来区分「原样」与「换了绑定」）。
    next_fact_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("candidate_id", "claim_text", "fact_type"):
            if not str(getattr(self, name) or ""):
                raise PackWriterError(f"CandidateReproposalDestination.{name} 不得为空")
        if self.destination not in CANDIDATE_REPROPOSAL_DESTINATIONS:
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的跨修订去向 {self.destination!r} 不在 "
                f"{list(CANDIDATE_REPROPOSAL_DESTINATIONS)} 内")
        if len(self.next_candidate_ids) != len(self.next_fact_types):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的同文候选 id 与类型必须逐位对应"
                f"（{len(self.next_candidate_ids)} != {len(self.next_fact_types)}）")
        if len(set(self.next_candidate_ids)) != len(self.next_candidate_ids):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的同文候选 id 有重复：{list(self.next_candidate_ids)}")
        # 去向词与列出的同文候选**双向**绑定：任何一侧单独漂移都会让「这一条到底还在不在」
        # 与它自己的明细互相矛盾——而那正是这张表存在的唯一理由。
        if not self.next_candidate_ids:
            expected = "not_reexpressed"
        elif len(self.next_candidate_ids) > 1:
            expected = "carried_ambiguous"
        else:
            expected = ("carried_verbatim" if self.next_fact_types[0] == self.fact_type
                        else "carried_rebound")
        if self.destination != expected:
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的跨修订去向 {self.destination!r} 与明细不符"
                f"（同文候选 {list(self.next_candidate_ids)}、类型 {list(self.next_fact_types)} ⇒ "
                f"应为 {expected!r}）")

    def to_dict(self) -> dict:
        return {"candidate_id": self.candidate_id, "claim_text": self.claim_text,
                "fact_type": self.fact_type, "destination": self.destination,
                "next_candidate_ids": list(self.next_candidate_ids),
                "next_fact_types": list(self.next_fact_types)}


@dataclass(frozen=True)
class CandidateCarveOutDestination:
    """原束里**一条**候选在裁出后的去向（被逐条排除，或带新身份继续走链）。"""

    candidate_id: str
    claim_text: str
    #: 排除原因（封闭词表 `CANDIDATE_CARVE_OUT_REASONS`）；空 tuple 表示这条候选**幸存**。
    reasons: tuple[str, ...] = ()
    surfaces: tuple[str, ...] = ()
    ineligible_member_refs: tuple[str, ...] = ()
    #: 这条候选那几条**不能表达当前状态**的路径 B 支撑边所绑的材料成员（仅含
    #: `path_b_history_only_current_state` 时非空；`srsc-1`）。记成员身份而不是一句理由：
    #: 读的人要能自己拿同一份 manifest 与来源集复算「这几条边绑的是哪一份文档」。
    history_only_member_refs: tuple[str, ...] = ()
    #: 这条候选那些**当前锚边**（角色不在「不能表达当前状态」闭集里的路径 B factual 边）绑定的
    #: 材料成员（仅含 `path_b_unproven_current_state` 时非空；`srsc-2`）。与上一条**互斥**：
    #: 上一条记的是「一条锚边都没有」时那几条历史边，这一条记的是「有锚边但没证出来」时那几条
    #: 锚边。同样记成员身份：读的人要能拿同一份 manifest 与来源集复算「这几条边绑的是哪一份
    #: 文档、那份文档在本节是什么角色、它的正文里到底有没有这段话」。
    unproven_current_state_member_refs: tuple[str, ...] = ()
    #: `srsc-3`：这条候选**为什么**证不出来（闭集 :data:`UNPROVEN_CURRENT_STATE_CAUSES`）。
    #: 与上一条**双向绑定**：有成员必有原因码，有原因码必有成员。两种来由的**出路不同**
    #: （材料不足 vs 措辞口径变了），只记成员身份会让读的人自己去猜是哪一种。
    unproven_current_state_cause_code: str = ""
    #: 幸存时它在**新修订**里的候选身份（排除时为空串）。非空即是「它接着走链」的凭据。
    next_candidate_id: str = ""
    #: 被排除时的可读细节（结构非法那条路用它记逐字错误串）。
    detail: str = ""

    def __post_init__(self) -> None:
        if not str(self.candidate_id or ""):
            raise PackWriterError("CandidateCarveOutDestination.candidate_id 不得为空")
        if bool(self.reasons) == bool(self.next_candidate_id):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的去向自相矛盾：必须**恰好**是"
                f"「带排除原因」或「带新修订身份」之一"
                f"（reasons={list(self.reasons)} next={self.next_candidate_id!r}）")
        unknown = [r for r in self.reasons if r not in CANDIDATE_CARVE_OUT_REASONS]
        if unknown:
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的排除原因 {unknown} 不在 "
                f"{list(CANDIDATE_CARVE_OUT_REASONS)} 内")
        if len(set(self.reasons)) != len(self.reasons):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的排除原因有重复：{list(self.reasons)}")
        if bool(self.surfaces) != ("path_b_high_risk_surface" in self.reasons):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的 surfaces={list(self.surfaces)} 与排除原因 "
                f"{list(self.reasons)} 不一致（仅 path_b_high_risk_surface 带 surfaces）")
        if bool(self.ineligible_member_refs) != (
                "path_b_ineligible_material_scope" in self.reasons):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的 ineligible_member_refs="
                f"{list(self.ineligible_member_refs)} 与排除原因 {list(self.reasons)} 不一致")
        if bool(self.history_only_member_refs) != (
                "path_b_history_only_current_state" in self.reasons):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的 history_only_member_refs="
                f"{list(self.history_only_member_refs)} 与排除原因 {list(self.reasons)} 不一致"
                "（仅 path_b_history_only_current_state 带 history_only_member_refs）")
        if bool(self.unproven_current_state_member_refs) != (
                "path_b_unproven_current_state" in self.reasons):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的 unproven_current_state_member_refs="
                f"{list(self.unproven_current_state_member_refs)} 与排除原因 "
                f"{list(self.reasons)} 不一致（仅 path_b_unproven_current_state 带 "
                "unproven_current_state_member_refs）")
        if bool(self.unproven_current_state_cause_code) != (
                "path_b_unproven_current_state" in self.reasons):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的 unproven_current_state_cause_code="
                f"{self.unproven_current_state_cause_code!r} 与排除原因 {list(self.reasons)} "
                "不一致（仅 path_b_unproven_current_state 带原因码）")
        if self.unproven_current_state_cause_code and \
                self.unproven_current_state_cause_code not in UNPROVEN_CURRENT_STATE_CAUSES:
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的 unproven_current_state_cause_code="
                f"{self.unproven_current_state_cause_code!r} 不在 "
                f"{list(UNPROVEN_CURRENT_STATE_CAUSES)} 内")

    def to_dict(self) -> dict:
        return {"candidate_id": self.candidate_id, "claim_text": self.claim_text,
                "reasons": list(self.reasons), "surfaces": list(self.surfaces),
                "ineligible_member_refs": list(self.ineligible_member_refs),
                "history_only_member_refs": list(self.history_only_member_refs),
                "unproven_current_state_member_refs": list(
                    self.unproven_current_state_member_refs),
                "unproven_current_state_cause_code": self.unproven_current_state_cause_code,
                "next_candidate_id": self.next_candidate_id, "detail": self.detail}


@dataclass(frozen=True)
class CandidateCarveOutProseDestination:
    """`cco-4` / `pw-15`：裁出连带改动的**一段自然草稿**（哪几条原子没了、还剩几条）。

    为什么必须有这一层：草稿层是门后组织器的**表达底座**。被裁候选说的是「那几条原子不得保留」，
    而草稿单元是一段**已经写出来的话**——它可能还表达了别的原子（那就留下、由组织器按已接受的
    原子改写），也可能整段只说了被裁掉的那件事（那就撤下）。少了这张逐单元的去向表，
    「草稿里那句被拒的话」在产物里读起来就像是从未被提出过——而它确实被写出来过。

    三条不变量（`__post_init__` 逐条强制）：

      * `dropped_atom_candidate_ids` 非空且去重：这一条只在**真的**有原子被裁时出现；
      * `remaining_atom_count == 0` ⟺ 该单元**整体撤下**：草稿层不允许没有原子的单元
        （`NaturalProseDraftUnit` 在类型层就要求非空原子集），因此「原子清零」与「撤下」是
        同一件事，不是两条可选动作；
      * `prose_text` 逐字留档：撤下的那段文字本身也是要可审计的产物，不得无声消失。

    `dropped_atom_candidate_ids` 用的是**原束**（裁出前）的候选身份——与 `decision.excluded`
    同一坐标系，因此「这条原子为什么被撤」在同一个对象里就能查到。
    """

    prose_unit_id: str
    prose_text: str
    #: 撤下那一段的**出处**（`narr-8` / `pprov-1` 的两条互斥轴，与 `NaturalProseDraftUnit`
    #: 同一口径）：材料行非空的节里是 `source_member_refs`，只有权威事实行的节（财务节）里是
    #: `source_fact_refs`。恰有一条非空——留档里少了这一层，「被撤下的那句话是从哪儿写出来的」
    #: 就查不到了，而它正是这条记录存在的理由。
    source_member_refs: tuple[str, ...]
    dropped_atom_candidate_ids: tuple[str, ...]
    remaining_atom_count: int
    source_fact_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.prose_unit_id or ""):
            raise PackWriterError("CandidateCarveOutProseDestination.prose_unit_id 不得为空")
        if not str(self.prose_text or "").strip():
            raise PackWriterError(
                f"草稿单元 {self.prose_unit_id!r} 的 prose_text 不得为空"
                "（撤下的那段文字必须逐字留档，否则它在产物里无声消失）")
        if not self.source_member_refs and not self.source_fact_refs:
            raise PackWriterError(
                f"草稿单元 {self.prose_unit_id!r} 必须恰好声明一条出处轴："
                "source_member_refs（材料行）或 source_fact_refs（权威事实行）")
        if self.source_member_refs and self.source_fact_refs:
            raise PackWriterError(
                f"草稿单元 {self.prose_unit_id!r} 同时声明了 source_member_refs 与 "
                "source_fact_refs：出处是两条**互斥**的轴，恰有一条非空")
        if not self.dropped_atom_candidate_ids:
            raise PackWriterError(
                f"草稿单元 {self.prose_unit_id!r} 没有任何原子被裁："
                "纯改写的单元不得进这张表（它只在真的少了几条原子时出现）")
        if len(set(self.dropped_atom_candidate_ids)) != len(self.dropped_atom_candidate_ids):
            raise PackWriterError(
                f"草稿单元 {self.prose_unit_id!r} 的被裁原子有重复："
                f"{list(self.dropped_atom_candidate_ids)}")
        if not isinstance(self.remaining_atom_count, int) \
                or isinstance(self.remaining_atom_count, bool) \
                or self.remaining_atom_count < 0:
            raise PackWriterError(
                f"草稿单元 {self.prose_unit_id!r} 的 remaining_atom_count 必须是 >= 0 的整数")

    @property
    def dropped(self) -> bool:
        """该单元是否**整体撤下**（原子全被裁掉）。`remaining_atom_count == 0` 的读法。"""
        return self.remaining_atom_count == 0

    def to_dict(self) -> dict:
        return {"prose_unit_id": self.prose_unit_id, "prose_text": self.prose_text,
                "source_member_refs": list(self.source_member_refs),
                "source_fact_refs": list(self.source_fact_refs),
                "dropped_atom_candidate_ids": list(self.dropped_atom_candidate_ids),
                "remaining_atom_count": self.remaining_atom_count,
                "dropped": self.dropped}


@dataclass(frozen=True)
class ContextUnitCarveOutDestination:
    """`cco-6`：裁出撤下的**一个 context 草稿单元**（原文、来源、原因、逐字表面一并留档）。

    它存在的理由与 `CandidateCarveOutProseDestination` 同源，但方向相反：那一条说的是「被裁候选
    让某段草稿少了哪几条原子」，这一条说的是「**这段衔接文字本身**被撤下了」——被撤的不是原子，
    而是承载背景/结构/衔接的那句话。少了这张表，「模型当时写出过这句含无定义期间的话」在产物里
    读起来就像是从未被提出过，而它确实被写出来过（原文可按 `narration_call_id` 在
    `logs/llm/*.jsonl` 逐字回查）。

    两条**行**共用这一个类（与 `CandidateCarveOutDestination` 同一约定），判据只有一条：
    `next_draft_unit_id` 为空 ⟺ 这一行是**被撤下**的那一行（带全部排除证据）；非空 ⟺ 这一行
    **幸存**，只带「原身份 → 新身份」这一对（此时不得带任何排除证据——空的证据字段被读成
    「撤下了、但不知道为什么」正是要挡的事）。

    四条证据字段各自回答一个审计问题，缺一条都会让复核者只能去翻散文：

      * `text`：**逐字原文**——那句话本身（两行都要：幸存者也要证明「文本没被换过」）；
      * `context_member_refs`：这句话当时声明的**来源**（模型写的材料别名，逐字原样保留，
        **不**翻译成内部身份：这一层被撤下的是一段不承载事实身份的衔接文字，它没有、也不该有
        事实/绑定的内部身份可写。允许为空——模型完全可能写一段不声明任何出处的衔接文字）；
      * `reasons`：**为什么**撤下（封闭词表 `CONTEXT_UNIT_CARVE_OUT_REASONS`）；
      * `hit_phrases`：判据命中的**逐字表面**（`NS.vague_period_hits` 的返回值）——「这条原因」
        与「这段文字里的哪几个字」分开记，读者不必自己再跑一遍判据。

    `unit_key` 只在被撤下那一行上有值（它是模型在**本批**里的把手，回填那一侧拿不到提案集规格，
    因此不给幸存行编一个）。它不是可选的装饰：被撤下的那条必须能对回原束的哪一条。
    """

    draft_unit_id: str
    unit_kind: str
    text: str
    next_draft_unit_id: str = ""
    unit_key: str = ""
    context_member_refs: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    hit_phrases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.draft_unit_id or ""):
            raise PackWriterError("ContextUnitCarveOutDestination.draft_unit_id 不得为空")
        if not str(self.unit_kind or ""):
            raise PackWriterError(
                f"context 单元 {self.draft_unit_id!r} 的 unit_kind 不得为空")
        if not str(self.text or "").strip():
            raise PackWriterError(
                f"context 单元 {self.draft_unit_id!r} 的 text 不得为空"
                "（撤下的那段文字必须逐字留档，否则它在产物里无声消失）")
        # 两行**恰好**一侧：带排除原因的是被撤下的那一行，带新身份的是幸存的那一行。
        if bool(self.reasons) == bool(str(self.next_draft_unit_id or "")):
            raise PackWriterError(
                f"context 单元 {self.draft_unit_id!r} 的去向行必须有且只有一个身份："
                f"排除原因={list(self.reasons)}、新身份={self.next_draft_unit_id!r}"
                "（两者都空 = 没交代；两者都有 = 既撤下又幸存）")
        if self.reasons:
            if len(set(self.reasons)) != len(self.reasons):
                raise PackWriterError(
                    f"context 单元 {self.draft_unit_id!r} 的排除原因有重复：{list(self.reasons)}")
            unknown = [r for r in self.reasons if r not in CONTEXT_UNIT_CARVE_OUT_REASONS]
            if unknown:
                raise PackWriterError(
                    f"context 单元 {self.draft_unit_id!r} 的排除原因 {unknown} 不在封闭词表 "
                    f"{list(CONTEXT_UNIT_CARVE_OUT_REASONS)} 内（裁出不得自造原因，"
                    "也不得把别的门规则放行到本出口）")
            if not str(self.unit_key or ""):
                raise PackWriterError(
                    f"context 单元 {self.draft_unit_id!r} 被撤下时必须带上它的 unit_key"
                    "（模型写的把手要原样保留，否则撤下的那条对不回原束）")
            if not self.hit_phrases:
                raise PackWriterError(
                    f"context 单元 {self.draft_unit_id!r} 声明了排除原因 "
                    f"{list(self.reasons)}，却没有一条命中的逐字表面："
                    "判据与文本对不上是取数面的缺陷，不得写成一条无表面的排除记录")
        elif self.hit_phrases or self.unit_key or self.context_member_refs:
            raise PackWriterError(
                f"context 单元 {self.draft_unit_id!r} 是**幸存**行，不得携带排除证据"
                f"（unit_key={self.unit_key!r}、来源 {len(self.context_member_refs)} 条、"
                f"表面 {len(self.hit_phrases)} 条）：幸存行只回答「原身份 → 新身份」")
        if len(set(self.hit_phrases)) != len(self.hit_phrases):
            raise PackWriterError(
                f"context 单元 {self.draft_unit_id!r} 的命中表面有重复：{list(self.hit_phrases)}")
        for phrase in self.hit_phrases:
            if str(phrase) not in str(self.text):
                raise PackWriterError(
                    f"context 单元 {self.draft_unit_id!r} 的命中表面 {phrase!r} 不在这段文字里："
                    "表面必须逐字来自被撤下的那段原文")

    @property
    def excluded(self) -> bool:
        """是否**被撤下**（新修订里不得出现）。判据只有一条：没有新身份即撤下。"""
        return not str(self.next_draft_unit_id or "")

    def to_dict(self) -> dict:
        return {"draft_unit_id": self.draft_unit_id, "unit_key": self.unit_key,
                "unit_kind": self.unit_kind, "text": self.text,
                "context_member_refs": list(self.context_member_refs),
                "reasons": list(self.reasons),
                "hit_phrases": list(self.hit_phrases),
                "next_draft_unit_id": self.next_draft_unit_id,
                "excluded": self.excluded}


@dataclass(frozen=True)
class CandidateCarveOutDecision:
    """§一.2 补充裁决：一次**逐候选裁出**的裁决（在**判定那一刻**成形，那时还没有新束）。

    背景：C3 原先对「束里有几条候选携带高风险面 / 支撑边越权」的反应是**整束拒绝**。整束拒绝
    在这里是**过度**的——不合格的是那几条，被连带作废的却是同一次模型输出里其余完全合法的候选。
    本裁决允许把不合格的那些**逐条**拒掉，让其余的以**新修订**继续走链。

    它与 `RejectionReproposalTrace` 的**根本区别**（也是它存在的理由）：定向重提案是**请求**
    下一轮重出，所以「潜台词」是模型可能把无关候选悄悄删掉，去向只能靠逐字比对**事后**猜；
    逐候选裁出是**确定性**的——不调模型、不重出、不改一个字的候选文本，因此每一条原候选的
    去向**必然**确定，无一处需要猜测。

    两条边界（缺一条即整束拒绝，退回既有 C3 / fail-closed，一个字不改）：

      * `source_candidate_ids` 是原束的**完整有序**身份，`excluded` 是它的一个**真子集**
        （逐条带 typed 原因）。因此「候选无故消失」在结构上无从表达：没被排除的候选即幸存，
        它们必然出现在新修订里（由 `CandidateCarveOutTrace` 逐条回填**证实**，不是承诺）。
      * 幸存数为 0 时**不产生本对象**：那与整束拒绝是同一件事，且会丢掉整束语义。

    被排除候选的**逐条 typed 拒绝记录**有两处、都按原束身份索引：原束那条
    `ProposalSetRejectionRecord` 上的 `candidate_audit`（对**全部**原候选逐条给出
    `path_b_high_risk_surface` / `path_b_ineligible_material_scope` /
    `not_individually_implicated` 及其逐字表面），以及本对象的 `excluded`（对**被排除**的那些
    给出同一份表面 + 新修订去向）。两者都不改写原束：`whole_set_rejected=True` 原样保留。
    """

    version: str
    from_attempt: int
    to_attempt: int
    to_revision: str
    source_candidate_ids: tuple[str, ...]
    excluded: tuple[CandidateCarveOutDestination, ...]
    #: `cco-4`：裁出连带改动的自然草稿单元（有序，按原束草稿顺序）。空 tuple = 本次裁出没有
    #: 触到任何草稿单元（要么本节没有草稿层，要么被裁的候选没有出现在任何草稿单元的原子账里）。
    prose_destinations: tuple[CandidateCarveOutProseDestination, ...] = ()
    #: `cco-6`：原束 **context 草稿单元**（`bundle.units`）的完整有序身份。与
    #: `excluded_context_units` **成对**存在（`__post_init__` 强制同真同假）：单元这一轴要么
    #: 整条在场（有完整身份 + 至少一项被撤下），要么一个字都不写。理由是判定面本身：
    #: 这一轴只在「`narrative_vague_period` 点名了某个 context 单元」时开火，因此
    #: 「声明了身份却没撤下任何单元」与「撤下了单元却没留下完整身份」两种形状都不该存在。
    source_draft_unit_ids: tuple[str, ...] = ()
    #: `cco-6`：被撤下的 context 单元（有序，按原束单元顺序）。它们**不得**出现在新修订里，
    #: 由 `SourceDraftUnit` 一侧的 `_carve_out_trace` 逐条证实（不是承诺）。
    excluded_context_units: tuple[ContextUnitCarveOutDestination, ...] = ()

    def __post_init__(self) -> None:
        if self.version != CANDIDATE_CARVE_OUT_VERSION:
            raise PackWriterError(
                f"候选裁出规则版本 {self.version!r} 不是当前版本 "
                f"{CANDIDATE_CARVE_OUT_VERSION!r}")
        if self.to_attempt <= self.from_attempt:
            raise PackWriterError(
                f"裁出后的新修订必须晚于原束（{self.from_attempt} → {self.to_attempt}）")
        if not self.source_candidate_ids:
            raise PackWriterError("原束一条候选都没有时不得做裁出")
        if not self.excluded and not self.excluded_context_units:
            raise PackWriterError(
                "没有任何候选、也没有任何 context 单元被排除时不得做裁出"
                "（那正是「一条都不该拒」，应走既有判定）")
        # `cco-6`：单元那一轴的**双向**绑定。单向会让两种漂移各漏一种（声明了身份却一条没撤 /
        # 撤了单元却没留下完整身份），而后者正是「无声消失」在裁决层的形状。
        if bool(self.excluded_context_units) != bool(self.source_draft_unit_ids):
            raise PackWriterError(
                f"context 单元那一轴必须整条在场或整条缺席："
                f"source_draft_unit_ids={len(self.source_draft_unit_ids)} 条、"
                f"excluded_context_units={len(self.excluded_context_units)} 条")
        if self.source_draft_unit_ids:
            unit_ids = list(self.source_draft_unit_ids)
            if len(set(unit_ids)) != len(unit_ids):
                raise PackWriterError(f"原束 context 单元身份有重复：{unit_ids}")
            for unit in self.excluded_context_units:
                if unit.draft_unit_id not in unit_ids:
                    raise PackWriterError(
                        f"被撤下的 context 单元 {unit.draft_unit_id!r} 不在原束身份里"
                        "（不得凭空撤下）")
                if unit.next_draft_unit_id:
                    raise PackWriterError(
                        f"裁决阶段（新束尚未产出）不得写新身份：{unit.draft_unit_id!r}")
            if len({u.draft_unit_id for u in self.excluded_context_units}) != \
                    len(self.excluded_context_units):
                raise PackWriterError("被撤下的 context 单元有重复条目")
            if len(self.excluded_context_units) >= len(unit_ids):
                raise PackWriterError(
                    "全部 context 单元都被撤下时不得做裁出：那时「剩下的草稿层」与"
                    "「模型一条衔接文字都没写」在产物里无从区分")
        ids = list(self.source_candidate_ids)
        if len(set(ids)) != len(ids):
            raise PackWriterError(f"原束候选身份有重复：{ids}")
        for entry in self.excluded:
            if entry.candidate_id not in ids:
                raise PackWriterError(
                    f"被排除的候选 {entry.candidate_id!r} 不在原束身份里（不得凭空排除）")
            if entry.next_candidate_id:
                raise PackWriterError(
                    f"裁决阶段（新束尚未产出）不得写新身份：{entry.candidate_id!r}")
        if len({e.candidate_id for e in self.excluded}) != len(self.excluded):
            raise PackWriterError("被排除候选有重复条目")
        if len(self.excluded) >= len(ids):
            raise PackWriterError(
                "全部候选都被排除时不得做裁出（幸存数为 0 与整束拒绝是同一件事）")
        # `cco-4`：草稿层去向与候选集**同坐标系**——被撤下的原子只允许是被排除的那几条候选。
        # 少了这一条，「某个草稿单元少了点什么」可以来自任何地方（改写、换绑、丢失），
        # 而那张表却长得像是裁出的结果。
        excluded_ids = {e.candidate_id for e in self.excluded}
        prose_unit_ids = [d.prose_unit_id for d in self.prose_destinations]
        if len(set(prose_unit_ids)) != len(prose_unit_ids):
            raise PackWriterError(f"草稿单元去向表里有重复条目：{prose_unit_ids}")
        for destination in self.prose_destinations:
            outside = sorted(set(destination.dropped_atom_candidate_ids) - excluded_ids)
            if outside:
                raise PackWriterError(
                    f"草稿单元 {destination.prose_unit_id!r} 撤下的原子 {outside} 并不在本次"
                    "被排除的候选里：草稿层的改动只能来自这次裁出，不得顺手少掉别的原子")

    @property
    def surviving_candidate_ids(self) -> tuple[str, ...]:
        excluded = {e.candidate_id for e in self.excluded}
        return tuple(cid for cid in self.source_candidate_ids if cid not in excluded)

    def to_dict(self) -> dict:
        return {"version": self.version, "from_attempt": self.from_attempt,
                "to_attempt": self.to_attempt, "to_revision": self.to_revision,
                "source_candidate_ids": list(self.source_candidate_ids),
                "excluded": [e.to_dict() for e in self.excluded],
                "prose_destinations": [d.to_dict() for d in self.prose_destinations],
                # `cco-6`：单元那一轴。两条字段**同时**在场或**同时**缺席，读的人不得只读其中一条
                # （只读 `excluded_context_units` 会把「这一束有没有单元被撤下」与「原束一共有
                # 几段衔接文字」拆成两个互不相干的问题）。
                "source_draft_unit_ids": list(self.source_draft_unit_ids),
                "excluded_context_units": [u.to_dict() for u in self.excluded_context_units]}


@dataclass(frozen=True)
class CandidateCarveOutTrace:
    """裁出裁决 + **新修订真的产出之后**逐条回填的去向（原束 → 新修订对账）。

    为什么要有这一层：裁决只说了「哪几条被排除、剩下的**应该**继续走链」。幸存者**是否真的**
    带着新身份出现在新修订里，靠承诺不算数——本对象把每一对
    （原身份 → 新身份）**逐条落实**，并且要求逐字 `claim_text` 相同。因此「旧束删几条后的
    子集冒充旧束」与「候选悄悄消失」两种作弊在产物里都读得出来。

    三条不变量（`__post_init__` 逐条强制）：

      * `destinations` 的键集**恰好**等于原束完整有序身份（不裁剪、不重排、不补）；
      * `next_candidate_ids`（新修订的**完整有序**候选身份，与 `RejectionReproposalTrace` 同义）
        **恰好**等于带 `next_candidate_id` 的那些（有序），且非空——因此「候选悄悄消失」
        与「凭空多出一条」在产物里都读得出来；
      * `model_calls_added == 0`：裁出不新增任何模型调用——这一点写进产物而不是留在注释里。
        被裁出的那几条的原文仍按原束 `narration_call_id` 存于 `logs/llm/*.jsonl`。

    原束一侧的幸存身份在 `decision.surviving_candidate_ids`（那是**旧**身份）；两个字段**不同名
    不同义**，不得互用：旧身份属于裁决，新身份属于回填。
    """

    decision: CandidateCarveOutDecision
    destinations: tuple[CandidateCarveOutDestination, ...]
    next_candidate_ids: tuple[str, ...]
    model_calls_added: int = 0
    #: `cco-6`：context 单元那一侧的回填（有序，按原束单元顺序）。键集恰好等于
    #: `decision.source_draft_unit_ids`；被撤下的那些 `next_draft_unit_id` 为空、幸存者带上新身份。
    #: 空 tuple = 本次裁出没有用到单元那一轴（`__post_init__` 强制与裁决同真同假）。
    context_unit_destinations: tuple[ContextUnitCarveOutDestination, ...] = ()
    #: `cco-6`：新修订的**完整有序** context 单元身份（与候选侧的 `next_candidate_ids` 同义）。
    next_draft_unit_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.model_calls_added != 0:
            raise PackWriterError(
                "逐候选裁出**不新增任何模型调用**（它不向模型重问任何东西）")
        if tuple(d.candidate_id for d in self.destinations) != \
                self.decision.source_candidate_ids:
            raise PackWriterError(
                "裁出去向表的键集必须**恰好**等于原束的完整有序候选身份（不裁剪、不重排、不补）")
        keys = [d.candidate_id for d in self.destinations]
        if len(set(keys)) != len(keys):
            raise PackWriterError(f"裁出去向表里有重复的候选身份：{keys}")
        surviving = tuple(d.next_candidate_id for d in self.destinations
                          if d.next_candidate_id)
        if surviving != self.next_candidate_ids:
            raise PackWriterError(
                f"新修订的完整有序候选身份必须**恰好**等于带 `next_candidate_id` 的那些："
                f"留档 {list(self.next_candidate_ids)} 实为 {list(surviving)}")
        if not surviving:
            raise PackWriterError(
                "一条候选都没幸存时不得做裁出（那与整束拒绝是同一件事，且会丢掉整束语义）")
        if len(surviving) != len(set(surviving)):
            raise PackWriterError(
                f"裁出后的新修订候选身份有重复：{list(surviving)}（新身份必须互不相同）")
        by_id = {d.candidate_id: d for d in self.destinations}
        for entry in self.decision.excluded:
            row = by_id.get(entry.candidate_id)
            if row is None or row.reasons != entry.reasons \
                    or row.surfaces != entry.surfaces \
                    or row.ineligible_member_refs != entry.ineligible_member_refs \
                    or row.next_candidate_id:
                raise PackWriterError(
                    f"裁出裁决与去向表对 {entry.candidate_id!r} 不自洽："
                    f"裁决 reasons={list(entry.reasons)} "
                    f"去向 reasons={list(row.reasons) if row else None}")
        # `cco-6`：单元那一侧与候选侧**同一套判据**（键集恰好等于原束完整有序身份、新身份恰好
        # 等于带 `next_draft_unit_id` 的那些）。有意的差别只有一处：新修订**允许**一段 context
        # 单元都不剩（原束 8 段撤 2 段只剩 6 段是常态，而「剩下 0 段」在裁决层已被
        # `excluded_context_units >= source_draft_unit_ids` 挡掉），因此这里不重复要求非空。
        if bool(self.context_unit_destinations) != bool(self.decision.source_draft_unit_ids):
            raise PackWriterError(
                "context 单元的去向表必须与裁决的单元身份**同真同假**"
                f"（去向 {len(self.context_unit_destinations)} 条、"
                f"裁决身份 {len(self.decision.source_draft_unit_ids)} 条）")
        if self.context_unit_destinations:
            unit_keys = [d.draft_unit_id for d in self.context_unit_destinations]
            if tuple(unit_keys) != self.decision.source_draft_unit_ids:
                raise PackWriterError(
                    "context 单元去向表的键集必须**恰好**等于原束的完整有序单元身份"
                    f"（不裁剪、不重排、不补）：留档 {list(self.decision.source_draft_unit_ids)} "
                    f"实为 {unit_keys}")
            kept_units = tuple(d.next_draft_unit_id for d in self.context_unit_destinations
                               if d.next_draft_unit_id)
            if kept_units != self.next_draft_unit_ids:
                raise PackWriterError(
                    "新修订的完整有序 context 单元身份必须**恰好**等于带 `next_draft_unit_id` "
                    f"的那些：留档 {list(self.next_draft_unit_ids)} 实为 {list(kept_units)}")
            if len(kept_units) != len(set(kept_units)):
                raise PackWriterError(
                    f"裁出后的新修订单元身份有重复：{list(kept_units)}")
            by_unit = {d.draft_unit_id: d for d in self.context_unit_destinations}
            for entry in self.decision.excluded_context_units:
                row = by_unit.get(entry.draft_unit_id)
                if row is None or row.reasons != entry.reasons \
                        or row.hit_phrases != entry.hit_phrases \
                        or row.text != entry.text or row.next_draft_unit_id:
                    raise PackWriterError(
                        f"裁出裁决与单元去向表对 {entry.draft_unit_id!r} 不自洽："
                        f"裁决 reasons={list(entry.reasons)} "
                        f"去向 reasons={list(row.reasons) if row else None}")

    @property
    def surviving_next_candidate_ids(self) -> tuple[str, ...]:
        """同 `next_candidate_ids`；供「原身份 → 新身份」对读的调用点使用。"""
        return self.next_candidate_ids

    def to_dict(self) -> dict:
        return {"decision": self.decision.to_dict(),
                "destinations": [d.to_dict() for d in self.destinations],
                "next_candidate_ids": list(self.next_candidate_ids),
                "model_calls_added": self.model_calls_added,
                "context_unit_destinations": [d.to_dict()
                                              for d in self.context_unit_destinations],
                "next_draft_unit_ids": list(self.next_draft_unit_ids)}


@dataclass(frozen=True)
class RejectionReproposalTrace:
    """C3：一次**定向重提案**的跨修订对账（原束 → 被排定的那一轮）。

    它回答一个此前**无法回答**的问题：被拒的那一束里的每一条候选，在下一修订里**还在不在**。
    没有它，「定向重提案」就只是一句请求——模型完全可以把无关候选悄悄删掉、把整束缩成一两条
    来换取通过，而产物里看不出这件事发生过。

    边界（必须逐条读清）：

      * 只在被排定的那一轮**真的产出了一份结构化提案集**时才存在。那一轮若在合并/截断/身份
        闭不上的阶段就没成形，就没有下一修订可比——此时它的去向由**那一轮自己的**拒绝记录
        （`attempt == answered_by_attempt`）或整节 fail-closed 如实交代，**不得**用一条空 trace
        冒充「逐条都追过了」。
      * `next_candidate_ids` 是下一修订的**完整有序**候选身份（不裁剪、不重排）；`destinations`
        的键集恒等于 `original_candidate_ids`（同样完整有序）。
      * 它**不**说明下一束是否被采信——那是那一轮自己的门与拒绝记录的结论。
    """

    from_attempt: int
    to_attempt: int
    note_version: str
    original_candidate_ids: tuple[str, ...] = ()
    next_candidate_ids: tuple[str, ...] = ()
    destinations: tuple[CandidateReproposalDestination, ...] = ()

    def __post_init__(self) -> None:
        if self.from_attempt < 1 or self.to_attempt != self.from_attempt + 1:
            raise PackWriterError(
                f"定向重提案必须指向**紧接的下一轮**（得到 {self.from_attempt} → "
                f"{self.to_attempt}）：被拒之后那一轮的说明就是定向说明，隔轮再比会把别的轮次"
                "的产物算进来")
        if not str(self.note_version or ""):
            raise PackWriterError("RejectionReproposalTrace.note_version 不得为空")
        if not self.original_candidate_ids or not self.next_candidate_ids:
            raise PackWriterError(
                "定向重提案 trace 的两端都必须有完整有序候选身份（空束无从对账）")
        if len(set(self.original_candidate_ids)) != len(self.original_candidate_ids):
            raise PackWriterError("RejectionReproposalTrace.original_candidate_ids 有重复")
        if len(set(self.next_candidate_ids)) != len(self.next_candidate_ids):
            raise PackWriterError("RejectionReproposalTrace.next_candidate_ids 有重复")
        keys = tuple(d.candidate_id for d in self.destinations)
        if keys != self.original_candidate_ids:
            raise PackWriterError(
                f"逐候选去向的键集必须**恰好**等于原束的完整有序候选身份（不裁剪、不重排、"
                f"不补），得到 {list(keys)} original={list(self.original_candidate_ids)}")
        known = set(self.next_candidate_ids)
        stray = sorted({cid for d in self.destinations for cid in d.next_candidate_ids} - known)
        if stray:
            raise PackWriterError(
                f"逐候选去向指向了不在下一修订候选集内的 id：{stray}——"
                "跨修订去向只能来自下一束自己，不得凭猜测补一条")

    def to_dict(self) -> dict:
        return {"from_attempt": self.from_attempt, "to_attempt": self.to_attempt,
                "note_version": self.note_version,
                "original_candidate_ids": list(self.original_candidate_ids),
                "next_candidate_ids": list(self.next_candidate_ids),
                "destinations": [d.to_dict() for d in self.destinations]}


@dataclass(frozen=True)
class RejectedCandidateAudit:
    """被拒束里**一条**候选的 typed 审计（§二 3 / 3.4）。

    只描述「这条候选被拒时是被什么点名的」，**不**产生任何可保留身份：它不进入任何 Draft、
    不参与任何后续绑定，也没有「通过」这一侧。`reasons` 非空是硬不变式——一条候选在审计里
    「什么原因都没有」与「被采信」在磁盘上不可区分，那正是要避免的歧义。
    """

    candidate_id: str
    reasons: tuple[str, ...]
    #: 点名这条候选的确定性门规则 id（仅含 `gate_blocking` 时非空）。
    rule_ids: tuple[str, ...] = ()
    #: 这条候选文本里**逐字**出现的高风险表面（仅含 `path_b_high_risk_surface` 时非空）。
    surfaces: tuple[str, ...] = ()
    #: 这条候选自己**不能承重**的那几条支撑边绑定的材料成员（仅含
    #: `path_b_ineligible_material_scope` 时非空）。记的是成员身份而不是一句理由：读的人要能
    #: 自己拿同一份 manifest 复算「这条边到底绑了哪张表单行」。
    ineligible_member_refs: tuple[str, ...] = ()
    #: 这条候选那几条**不能表达当前状态**的支撑边绑定的材料成员（仅含
    #: `path_b_history_only_current_state` 时非空；`srsc-1`）。同样记成员身份：读的人要能拿
    #: 同一份 manifest 与来源集复算「这几条边绑的是哪一份文档、那份文档在本节是什么角色」。
    history_only_member_refs: tuple[str, ...] = ()
    #: 这条候选那些**当前锚边**（角色不在「不能表达当前状态」闭集里的路径 B factual 边）绑定的
    #: 材料成员（仅含 `path_b_unproven_current_state` 时非空；`srsc-2`）。与上一条**互斥**，
    #: 记的也是成员身份——读的人要能拿同一份 manifest、来源集与已解析正文复算「这几条边绑的是
    #: 哪一份文档、那份正文里到底有没有这段话」。
    unproven_current_state_member_refs: tuple[str, ...] = ()
    #: `srsc-3`：这条候选**为什么**证不出来（闭集 :data:`UNPROVEN_CURRENT_STATE_CAUSES`）。
    #: 与上一条**双向绑定**。两种来由的**出路不同**（材料不足 vs 措辞口径变了），审计里
    #: 只记成员身份会让「这条为什么被拒、该怎么改」变成一句要靠人猜的话。
    unproven_current_state_cause_code: str = ""

    def __post_init__(self) -> None:
        if not str(self.candidate_id or ""):
            raise PackWriterError("RejectedCandidateAudit.candidate_id 不得为空")
        if not self.reasons:
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的审计必须至少有一条 typed 原因"
                "（无原因是「未被点名」，不是「没有原因」）")
        unknown = [r for r in self.reasons if r not in PROPOSAL_SET_REJECTION_CANDIDATE_REASONS]
        if unknown:
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的审计原因 {unknown} 不在 "
                f"{list(PROPOSAL_SET_REJECTION_CANDIDATE_REASONS)} 内")
        if len(set(self.reasons)) != len(self.reasons):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的审计原因有重复：{list(self.reasons)}")
        individually = tuple(r for r in self.reasons if r != "not_individually_implicated")
        # 「没有被单独点名」与「有具体原因」是**互斥**的两侧：两者同时出现会让读的人分不清
        # 这一条到底踩没踩线；两者都不出现则等于没有原因（上面已拒）。
        if ("not_individually_implicated" in self.reasons) != (not individually):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的审计原因自相矛盾：{list(self.reasons)}")
        # 三个明细字段都**双向**绑定到各自的原因：有原因没明细 = 无法复核；有明细没原因 = 悬空。
        if bool(self.rule_ids) != ("gate_blocking" in self.reasons):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的 rule_ids={list(self.rule_ids)} 与原因 "
                f"{list(self.reasons)} 不一致（仅 gate_blocking 带 rule_ids）")
        if bool(self.surfaces) != ("path_b_high_risk_surface" in self.reasons):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的 surfaces={list(self.surfaces)} 与原因 "
                f"{list(self.reasons)} 不一致（仅 path_b_high_risk_surface 带 surfaces）")
        if bool(self.ineligible_member_refs) != (
                "path_b_ineligible_material_scope" in self.reasons):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的 ineligible_member_refs="
                f"{list(self.ineligible_member_refs)} 与原因 {list(self.reasons)} 不一致"
                "（仅 path_b_ineligible_material_scope 带 ineligible_member_refs）")
        if bool(self.history_only_member_refs) != (
                "path_b_history_only_current_state" in self.reasons):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的 history_only_member_refs="
                f"{list(self.history_only_member_refs)} 与原因 {list(self.reasons)} 不一致"
                "（仅 path_b_history_only_current_state 带 history_only_member_refs）")
        if bool(self.unproven_current_state_member_refs) != (
                "path_b_unproven_current_state" in self.reasons):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的 unproven_current_state_member_refs="
                f"{list(self.unproven_current_state_member_refs)} 与原因 "
                f"{list(self.reasons)} 不一致（仅 path_b_unproven_current_state 带 "
                "unproven_current_state_member_refs）")
        if bool(self.unproven_current_state_cause_code) != (
                "path_b_unproven_current_state" in self.reasons):
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的 unproven_current_state_cause_code="
                f"{self.unproven_current_state_cause_code!r} 与原因 {list(self.reasons)} "
                "不一致（仅 path_b_unproven_current_state 带原因码）")
        if self.unproven_current_state_cause_code and \
                self.unproven_current_state_cause_code not in UNPROVEN_CURRENT_STATE_CAUSES:
            raise PackWriterError(
                f"候选 {self.candidate_id!r} 的 unproven_current_state_cause_code="
                f"{self.unproven_current_state_cause_code!r} 不在 "
                f"{list(UNPROVEN_CURRENT_STATE_CAUSES)} 内")

    def to_dict(self) -> dict:
        return {"candidate_id": self.candidate_id, "reasons": list(self.reasons),
                "rule_ids": list(self.rule_ids), "surfaces": list(self.surfaces),
                "ineligible_member_refs": list(self.ineligible_member_refs),
                "history_only_member_refs": list(self.history_only_member_refs),
                "unproven_current_state_member_refs": list(
                    self.unproven_current_state_member_refs),
                "unproven_current_state_cause_code": self.unproven_current_state_cause_code}


def _retained_prose_rows(units: Sequence[Any]) -> list[dict]:
    """留存用的逐单元行。**两种入参**：门前束的 `NaturalProseDraftUnit` 对象，或**逐批**解析
    结果里的原始 dict（`_parse_prose_units` 的产物）。两者字段名同源，取值一律 `getattr`/`get`，
    因此一份实现同时覆盖两条留存来源，不存在「合并束留存的字段与逐批留存的不一样」。

    返回值是 **list** 而不是 tuple：这一份要落进 JSON 产物，`tuple` 会在
    `json.dumps → json.loads` 往返后变成 list，让「落盘形态可往返」这条判据假红。
    """
    rows: list[dict] = []
    for unit in tuple(units or ()):
        if isinstance(unit, Mapping):
            get = unit.get
        else:
            get = lambda key, default=None, _u=unit: getattr(_u, key, default)  # noqa: E731
        rows.append({
            "prose_unit_id": str(get("prose_unit_id") or ""),
            "index": get("index") if get("index") is not None else len(rows) + 1,
            "text": str(get("text") or ""),
            "source_member_refs": [str(x) for x in (get("source_member_refs") or ())],
            "source_fact_refs": [str(x) for x in (get("source_fact_refs") or ())],
            "atom_candidate_ids": [str(x) for x in (get("atom_candidate_ids") or ())],
        })
    return rows


def _retained_pre_gate(*, source: "_PreGateBundle | None",
                       batch_plans: Sequence[tuple[Any, Mapping[str, Any]]] = ()
                       ) -> "RetainedPreGateDraft":
    """整束被拒时，把门前**已经成形**的内容留一份（指令 D §三）。

    留存**不**改变这次拒绝的任何一部分：被拒的仍然是被拒的，`retained_pre_gate` 没有通过侧、
    不被任何门读取，也不产生 `SectionDraft` 身份。它只回答一个问题——「这一轮模型到底写出了
    什么」。真实 run r8 的 company 节里，这个问题的答案是「前三批各写了几段」，而当时的产物
    只留下一条属于**失败那一批**的 `draft_unit_ids`，前三批的正文随异常消失。

    两条来源按**可用性**择优，不合并：
      * 有合并后的束（`source is not None`）⇒ 用**它**（`merged_bundle`）。此时逐批归属已不可考，
        因此行里如实写 `batch_id=None` 与「本轮合并后的完整提案集」，不编一个批次坐标。
      * 只有逐批的合法返回 ⇒ 用那些批次（`per_batch_before_failure`），逐批带自己的坐标。
      * 两者都没有 ⇒ `none` + 空表。「无可留存」与「留存失败」由 `basis` 分开。
    """
    if source is not None:
        return RetainedPreGateDraft(basis="merged_bundle", batches=({
            "batch_id": None,
            "label": "（本轮合并后的完整提案集）",
            "aspect_ids": [],
            "prose_units": _retained_prose_rows(source.prose_units),
            "candidate_labels": [str(c.candidate_id) for c in source.candidates],
            "unit_labels": [str(u.draft_unit_id) for u in source.units],
            "follow_up_total": len(source.follow_up_specs),
        },))
    rows: list[dict] = []
    for target_batch, plan in tuple(batch_plans or ()):
        rows.append({
            "batch_id": str(getattr(target_batch, "batch_id", "") or ""),
            "label": str(getattr(target_batch, "label", lambda: "")()),
            "aspect_ids": [str(a) for a in (getattr(target_batch, "aspect_ids", ()) or ())],
            "prose_units": _retained_prose_rows(tuple(plan.get("natural_prose_draft") or ())),
            # 这一批**自己给出的**标签（`candidate_key` / `unit_key`）。它们不是内容寻址身份
            # ——留存的每一批都**没有**通过整轮合并，因此不存在可派生的 `candidate_id`。
            "candidate_labels": [str(s.get("candidate_key") or "")
                                 for s in tuple(plan.get("claim_candidates") or ())],
            "unit_labels": [str(s.get("unit_key") or "")
                            for s in tuple(plan.get("narrative_draft_units") or ())],
            "follow_up_total": len(tuple(plan.get("follow_up_needs") or ())),
        })
    return RetainedPreGateDraft(
        basis=("per_batch_before_failure" if rows else PRE_GATE_RETENTION_EMPTY_BASIS),
        batches=tuple(rows))


@dataclass(frozen=True)
class RetainedPreGateDraft:
    """整束被拒时留存的**门前**内容：只读、无通过侧、不参与任何判定（指令 D §三）。

    **不得被读成「这一节其实有内容」**：它存在的前提恰恰是这一次生成**没有**被采信。因此
    它不带任何 `SectionDraft` 身份（没有 `draft_id` / `draft_revision`），也没有任何下游消费者
    会去读它——`batches` 里的每一行都只是「这一段文字当时被写出来了」的留档。把它的正文直接
    引用进任何报告，就是把未核验的门前草稿冒充成核验过的正文。
    """

    #: `PRE_GATE_RETENTION_BASES` 之一。
    basis: str
    #: 逐批（或合并束一行）的留存行。`basis="none"` 时**必须**为空。
    batches: tuple[dict, ...] = ()

    def __post_init__(self) -> None:
        if self.basis not in PRE_GATE_RETENTION_BASES:
            raise PackWriterError(
                f"retained_pre_gate.basis={self.basis!r} 不在 "
                f"{list(PRE_GATE_RETENTION_BASES)} 内")
        if bool(self.batches) != (self.basis != PRE_GATE_RETENTION_EMPTY_BASIS):
            raise PackWriterError(
                f"basis={self.basis!r} 与留存行数 {len(self.batches)} 不一致："
                "「无可留存」（none + 空表）与「有留存」（非 none + 非空表）是两件事，"
                "不得用空表冒充前者、也不得用 none 掩盖后者")
        for row in self.batches:
            if not str(row.get("label") or ""):
                raise PackWriterError("留存的每一行都必须有可读的 label（读到的是哪一批）")
            for unit in row.get("prose_units") or ():
                if not str(unit.get("text") or "").strip():
                    raise PackWriterError(
                        "留存里的草稿单元 text 不得为空——空串会被读成「模型写了空话」，"
                        "而本次事实是「这一单元没有正文」")

    def to_dict(self) -> dict:
        return {
            "version": PRE_GATE_RETENTION_VERSION,
            "label": PRE_GATE_RETENTION_LABEL,
            "basis": self.basis,
            "batches": [dict(row) for row in self.batches],
            "prose_unit_total": sum(len(row.get("prose_units") or ())
                                    for row in self.batches),
            "note": (
                f"**{PRE_GATE_RETENTION_LABEL}**。这一束候选提案集**未被采信**，本字段是它被拒"
                "时门前已经写出来的内容的只读留存（逐批草稿正文 + 逐单元出处轴 + 该批自己的"
                "标签）。它**不是** `SectionDraft`、**不是**提案集、**不是**任何判定对象的"
                "替代，也没有任何门读它；`basis` 说明留存来自**哪一步**："
                "`merged_bundle`（本轮各批全部成功并已合并，是之后的整束级判定拒了它，逐批"
                "归属已不可考）/ `per_batch_before_failure`（其余批次已合法解析、某一批失败或"
                "本轮合并闭合不上，逐批坐标可考）/ `none`（本轮无任何一批交出合法返回）。"
                "**它不改变这次拒绝**：不得因它非空就改判任何门，也不得把它的正文当成正文引用。"),
        }


@dataclass(frozen=True)
class ProposalSetRejectionRecord:
    """一次**被整体拒绝**的候选提案集的 typed 审计（§二 3）。

    这条记录存在的理由是一个已被裁决点名的反模式：把 20 个原始候选里的 3 个**原地删除**，
    再把剩下的 17 个当成「原提案集」送进聚合绑定门。因此每当一束提案不被采信，必须留下：

      * 该束的**完整有序**身份（候选 key / proposal id / 叙述单元 id），**不是**过滤后的子集；
      * 拒绝的 typed 原因（来自封闭词表）与可直接复核的细节；
      * 指回本次生成调用的 `narration_call_id`——原始输出文本按该 id 存于 `logs/llm/*.jsonl`，
        因此「模型当时到底写了什么」可逐字回查，而本记录只留身份与判据，不复制正文。

    §二 3 / 3.4（`pw-7`）追加**逐候选** typed 审计（`candidate_audit`）：每一条候选都被明确
    记成「它自己踩了哪条线」（`path_b_high_risk_surface` / `gate_blocking`）或「这一束被拒
    **不是因为它**」（`not_individually_implicated`）。它**不是**一张幸存者名单：没有通过这一侧，
    也没有任何下游消费者——`candidate_audit` 的唯一读者是复核者与报告，且它的键集恒等于
    `candidate_ids`（完整、有序、不裁剪），因此「从 20 条里挑出几条留下」在结构上无从表达。

    它记录的是**输出侧**被拒，**不产生**任何 Draft 身份（`draft_formed=False`）。修订身份仍是
    Writer **输入**的确定性函数（`NS.derive_draft_revision`），且把 `attempt` 计入其中：
    被拒的这一束与最终被采信的那一束**各自**对上一个修订（`_revision_for(attempt)`），
    因此「被拒的是第几次生成、它当时声明的支撑边是哪几条」在产物里可区分。注意方向——
    不是被拒的输出改写了修订（那才是「换一个身份」），而是修订从一开始就是逐轮不同的，
    本记录的 `attempt` 就是查回它自己那一轮修订的键。
    """

    attempt: int
    rejection_kind: str
    rejection_detail: str
    #: 该束的**完整有序**候选身份。已经通过结构校验的束给内容寻址的
    #: `ClaimCandidate.candidate_id`；连结构校验都没过的束（`schema_invalid`）只能给模型
    #: 自己写的 `candidate_key` 标签——此时 `rejection_kind` 本身就标明了「这一束从未成为
    #: 结构化对象」，不存在可派生的 candidate_id，不得在这里伪造一个。
    candidate_ids: tuple[str, ...] = ()
    proposal_ids: tuple[str, ...] = ()
    draft_unit_ids: tuple[str, ...] = ()
    follow_up_count: int = 0
    #: **代表调用**。`pw-10` 起一次生成不再等于一次调用（一次生成 = 一轮**分批**请求），
    #: 因此这个字段的语义是「最直接说明这次拒绝的那一次调用」：拒绝由单批输出直接造成时
    #: （`batch_truncated` / `schema_invalid`）就是那一次；整束级拒绝（高风险面 / 硬门 /
    #: 栏目零产出）取本轮**第一次**调用。本轮**全部**调用见 `batches`（有序）。
    narration_call_id: str | None = None
    response_hash: str | None = None
    #: §二 3 / 3.4：逐候选 typed 审计（有序，键集恒等于 `candidate_ids`）。在整束**从未**成为
    #: 结构化对象的 kind 下恒为空——那时没有可信的逐候选归属，编一份出来就是猜。
    candidate_audit: tuple[RejectedCandidateAudit, ...] = ()
    #: 被点名的 Contract 栏目（仅 `subsection_uncovered` 非空）。**结构化**给出，不只在
    #: `rejection_detail` 那段散文里——「这一束缺了哪个栏目」是判据本身，复核者要能直接读键。
    named_subsections: tuple[str, ...] = ()
    #: §二：**本轮**逐批的调用读数（有序）。分批请求下「这一轮发了哪几次、每次回答哪些
    #: aspect、哪一次被截断、缩到了第几层」必须结构化给出，而不是只留一个代表 call_id：
    #: 半截输出按 id 存在 `logs/llm/*.jsonl` 里，没有批身份就无从知道它当时在回答什么。
    batches: tuple[BatchCallRecord, ...] = ()
    #: C3：这一次拒绝是否**已排定**由第几次生成做**定向重提案**（`None` = 没有排定）。
    #: 它记的是「排定」而不是「结果」：结果由 `reproposal` 或那一轮自己的记录给出。
    answered_by_attempt: int | None = None
    #: C3：被排定的那一轮**真的产出了一份结构化提案集**时，逐候选的跨修订去向。
    #: 见 `RejectionReproposalTrace` 的三条边界。
    reproposal: RejectionReproposalTrace | None = None
    #: §一.2 补充裁决：这一束**没有被整束作废**，而是把不合格的那几条逐条拒掉、其余以新修订
    #: 继续走链时，逐候选的「原身份 → 被排除原因 / 新身份」对账。见 `CandidateCarveOutTrace`。
    #:
    #: 与 `reproposal` **互斥**：一次拒绝要么被「定向重提案」回答（重问一轮），要么被「逐候选
    #: 裁出」回答（不重问），不可能两者都是——两条路各自有独立的下一轮，同时排定就是凭空多出
    #: 一轮。`answered_by_attempt` 在裁出这条路上**留空**：它不是「排定给第几轮」，它根本
    #: 没有向模型重问任何东西（新修订的轮次由 `to_attempt` 给出）。
    carve_out: CandidateCarveOutTrace | None = None
    #: 指令 D §三：这一束被拒时，门前**已经写出来**的内容的只读留存。见 `RetainedPreGateDraft`。
    #: 它与上面的 `reproposal` / `carve_out` 是三个不同的问题：那两个问「这一束被拒**之后**怎么
    #: 办」，这一个只问「它被拒**的时候**长什么样」。它**不**参与任何判定，也**不**改变这次拒绝。
    retained_pre_gate: RetainedPreGateDraft | None = None

    def __post_init__(self) -> None:
        if self.attempt < 1:
            raise PackWriterError(
                f"ProposalSetRejectionRecord.attempt 必须 ≥1，得到 {self.attempt!r}")
        structured = _structured_rejection_kind(self.rejection_kind)
        if not str(self.rejection_detail or ""):
            raise PackWriterError("ProposalSetRejectionRecord.rejection_detail 不得为空")
        if self.rejection_kind == "batch_truncated" and not self.batches:
            raise PackWriterError(
                "batch_truncated 必须逐条给出本轮的分批调用读数（哪一次被截断不得省略）")
        if self.narration_call_id is not None and self.batches and \
                self.narration_call_id not in {b.call_id for b in self.batches}:
            raise PackWriterError(
                f"代表调用 id {self.narration_call_id!r} 不在本轮的分批调用读数里："
                "两者指向同一次调用，不得各自为政")
        # 逐候选审计与「整束是否曾成为结构化对象」**双向**绑定：结构化 kind 必须有完整审计，
        # 非结构化 kind 必须为空。单向绑定会让两种漂移各漏一种（该有的没有 / 不该有的凭空出现）。
        if structured:
            keys = tuple(a.candidate_id for a in self.candidate_audit)
            if keys != self.candidate_ids:
                raise PackWriterError(
                    f"逐候选审计的键集必须**恰好**等于该束的完整有序候选身份（不裁剪、不重排、"
                    f"不补），得到 audit={list(keys)} candidate_ids={list(self.candidate_ids)}")
        elif self.candidate_audit:
            raise PackWriterError(
                f"rejection_kind={self.rejection_kind!r} 下整束从未成为结构化对象，"
                "逐候选审计必须为空（不得凭输出文本猜测逐候选归属）")
        if bool(self.named_subsections) != (self.rejection_kind == "subsection_uncovered"):
            raise PackWriterError(
                f"named_subsections={list(self.named_subsections)} 与 "
                f"rejection_kind={self.rejection_kind!r} 不一致（仅 subsection_uncovered 带栏目）")
        # C3：定向重提案只对**触发类**拒绝排定，且只能排定到**后面**的某一轮。
        if self.answered_by_attempt is not None:
            if self.rejection_kind not in REPROPOSAL_TRIGGER_KINDS:
                raise PackWriterError(
                    f"rejection_kind={self.rejection_kind!r} 不得排定定向重提案"
                    f"（只有 {list(REPROPOSAL_TRIGGER_KINDS)} 有逐候选的定向内容可写）")
            if self.answered_by_attempt <= self.attempt:
                raise PackWriterError(
                    f"定向重提案必须排定到被拒之后的某一轮（{self.attempt} → "
                    f"{self.answered_by_attempt}）——同一轮回答不了它自己")
        # 逐候选去向**蕴含**排定（单向）：有 trace 就必须有排定、且两端逐值对得上；反过来
        # **不**成立——「已排定但那一轮没能产出结构化提案集」时 trace 必须缺席，不得用一条空
        # trace 冒充「逐条都追过了」。因此这里只查蕴含方向，不查反向等价。
        if self.reproposal is not None and (
                self.reproposal.from_attempt != self.attempt
                or self.reproposal.to_attempt != self.answered_by_attempt):
            raise PackWriterError(
                f"逐候选去向必须正好是「这一束（{self.attempt}）→ 被排定的那一轮"
                f"（{self.answered_by_attempt!r}）」，得到 "
                f"{self.reproposal.from_attempt} → {self.reproposal.to_attempt}")
        # §一.2：逐候选裁出只对**触发类**拒绝存在（与定向重提案同一份判据：这一类拒绝有
        # 「哪几条候选、踩了什么线」的逐候选内容可写），且与定向重提案**互斥**。
        if self.carve_out is not None:
            if self.rejection_kind not in CARVE_OUT_ELIGIBLE_KINDS:
                raise PackWriterError(
                    f"rejection_kind={self.rejection_kind!r} 不得做逐项裁出"
                    f"（只有 {list(CARVE_OUT_ELIGIBLE_KINDS)} 有逐项裁出的内容可写）")
            if self.reproposal is not None or self.answered_by_attempt is not None:
                raise PackWriterError(
                    "逐候选裁出与定向重提案**互斥**：裁出不向模型重问任何东西，因此这一束"
                    "不可能同时又排定了一轮重问（那是凭空多出一轮）")
            if self.carve_out.decision.from_attempt != self.attempt:
                raise PackWriterError(
                    f"裁出裁决的原束轮次必须是这一条记录自己（{self.attempt}），"
                    f"得到 {self.carve_out.decision.from_attempt}")
            if self.carve_out.decision.to_attempt <= self.attempt:
                raise PackWriterError(
                    f"裁出后的新修订必须晚于原束（{self.attempt} → "
                    f"{self.carve_out.decision.to_attempt}）")
            # 这里比的是**原束一侧**的幸存身份（裁决的 `surviving_candidate_ids`），
            # 不是新修订一侧的（trace 的 `next_candidate_ids`）——两者不同名、不同义。
            original_survivors = self.carve_out.decision.surviving_candidate_ids
            # `cco-6`：被裁对象有**两类**（候选 / context 单元）。「真的排除了至少一项」是这条
            # 不变式的本意（这一束字面上不得是「一条都不该拒」），因此成立面从一类扩到两类；
            # 判据本身一个字没改。只写候选那一侧会让一张**只有单元被撤下**的裁决被判成
            # 「什么都没排除」——那正好把本批要修的越权形态读成「不许裁」，与本出口的语义相反。
            if original_survivors == self.candidate_ids and \
                    not self.carve_out.decision.excluded_context_units:
                raise PackWriterError(
                    "裁出必须真的排除了至少一项（候选或 context 单元），"
                    "否则这一束字面上就是「一条都不该拒」")
            carved = set(original_survivors)
            if not carved <= set(self.candidate_ids):
                raise PackWriterError(
                    "裁出的幸存身份必须**全部**来自这一束的完整有序身份（不得凭空引入新候选）")

    def to_dict(self) -> dict:
        return {
            "attempt": self.attempt, "rejection_kind": self.rejection_kind,
            "rejection_detail": self.rejection_detail,
            "candidate_ids": list(self.candidate_ids),
            "proposal_ids": list(self.proposal_ids),
            "draft_unit_ids": list(self.draft_unit_ids),
            "follow_up_count": self.follow_up_count,
            "narration_call_id": self.narration_call_id,
            "response_hash": self.response_hash,
            "candidate_audit": [a.to_dict() for a in self.candidate_audit],
            "named_subsections": list(self.named_subsections),
            # §二：本轮逐批的调用读数（有序）。单批路径下恰好一条，与 narration_call_id 同一次。
            "batches": [b.to_dict() for b in self.batches],
            # C3：这一次拒绝是否被**定向**回答、以及被排定的那一轮里每条原候选的去向。
            # `None` 是**如实**的取值，不是缺省填充：它表示「没有排定」或「排定的那一轮没能
            # 产出结构化提案集」——两种情况下都**没有**跨修订事实可写。
            "answered_by_attempt": self.answered_by_attempt,
            "reproposal": (self.reproposal.to_dict() if self.reproposal is not None else None),
            # §一.2：这一束是否被「逐候选裁出」回答（不合格的逐条拒掉、其余以新修订继续）。
            # 与 `reproposal` 互斥；`None` 表示这一束走的是原有路径（整束重问或整束 fail-closed）。
            "carve_out": (self.carve_out.to_dict() if self.carve_out is not None else None),
            # 指令 D §三：这一束被拒时门前已写出来的内容的只读留存（`None` = 没有留存事实可写，
            # 与「留了一份空表」不同；`basis="none"` 才是「留了、且明确是空的」）。
            "retained_pre_gate": (self.retained_pre_gate.to_dict()
                                  if self.retained_pre_gate is not None else None),
            # 显式写进产物，而不是靠读者从字段形状反推：这份审计说的是「**整束**为什么被拒」、
            # 这次拒绝的判据作用在**整束**上（而不是「某几条候选各自被拒」这种逐候选读数），
            # 因此它**没有通过侧**。§一.2 的裁出**不**改写这一条：那一束作为一束确实被拒了，
            # 被裁掉的是「连带作废其余合法候选」这个**过度**的后果——它发生在拒绝**之后**，
            # 由同一份记录上的 `carve_out` 逐条交代（读的人不得把 `carve_out` 非空读成
            # 「这一束没被拒」，两者说的是不同时刻的不同事实）。
            "whole_set_rejected": True,
        }


class ProposalSetRejectedError(PackWriterError):
    """整束提案**在重试额度用尽后**仍被拒：带着完整的拒绝审计一起抛出。

    审计不放在消息文本里（消息会被上层按长度截断），而是作为一个结构化字段随异常传递，
    由组合根落成版本化产物——「为什么这一节没有内容」必须可回查，不能只剩一行错误字符串。

    `follow_up_needs` / `follow_up_untypeable` 是同一件事的另一半：**被拒的是候选提案集，
    不是模型提出的补件诉求**。诉求随异常带出后，由组合根按**待裁决提议**如实留存——既不得
    因为候选被拒而丢失，也不得被自动执行（裁决权在 Harness，不在本模块）。`untypeable`
    装的是连 Contract 校验都没过的原始诉求，同样不丢：它们不是「没有诉求」，而是「诉求不成立」。
    """

    def __init__(self, message: str,
                 rejections: tuple[ProposalSetRejectionRecord, ...] = (),
                 follow_up_needs: tuple[Any, ...] = (),
                 follow_up_untypeable: tuple[dict, ...] = ()) -> None:
        super().__init__(message)
        self.rejections = tuple(rejections)
        self.follow_up_needs = tuple(follow_up_needs)
        self.follow_up_untypeable = tuple(follow_up_untypeable)


def _pending_follow_up_needs(*, captured: Sequence[tuple[int, Sequence[Mapping[str, Any]]]],
                             task: PS.SectionTask, authority: Any, scan: AuthorityScan,
                             revision_for: Callable[[int], str], policy: WriterPolicy
                             ) -> tuple[tuple[Any, ...], tuple[dict, ...]]:
    """失败轮里模型提出的补件诉求 → (可类型化的 `FollowUpNeed`, 不可类型化的原始诉求)。

    **只做类型化，不做裁决**：返回的 `FollowUpNeed` 是**待裁决提议**，调用方不得据此执行补件，
    也不得把它当成缺口。拒不掉的原始诉求原样带回（带失败原因与提出它的轮次），而不是消失。

    `revision_for(轮次)` 给出**提出该诉求的那一次生成**的 draft 修订：补件身份里的
    `section_draft_revision` / `need_id` 必须指向提出它的那一轮。整节失败时给一个统一的
    修订会把「第 1 轮说的」和「第 3 轮说的」写成同一件事——那正是「被拒的那一束与被采信的
    那一束在产物里无法区分」的翻版。

    这里捕的异常不止 `PackWriterError`：`schema_invalid` 那一路上取到的是**未结构校验**的原文
    （`_best_effort_follow_up_specs`），缺字段是意料之中，`KeyError`/`TypeError` 同样是
    「诉求不成立」的表达。让它们冒出去会把「诉求不成立」变成「整轮崩掉」，与 §二 2.6 相反。
    """
    needs: list[Any] = []
    untypeable: list[dict] = []
    # C4：**同一轮**里同一条诉求可能被说两遍 —— 批次形状纠正会重问那一批，纠正前那一份返回的
    # 诉求已先行留痕（§二 2.6：纠正若成功，它不先取就无处可查），纠正后再失败时 `_reject` 又会
    # 从最后那一份原文里再取一次；两次落在**同一个** `attempt` 上、却是两条捕获记录。逐字相同时
    # 那是同一条诉求在同一轮说了两遍，不是两条诉求：按（轮次，内容）去重，保留首次出现。不去重
    # 的话产物里会出现两条一模一样的 typed 记录，读的人无法分辨那是两条诉求还是一次格式抖动。
    # **只**去掉逐字相同的重复项：内容不同的照旧逐条留档，去重不改变任何一条自己的原因码，也
    # 不新增/减少可成立的诉求。跨轮次不去重——不同轮说的同一句话是两次真实的提出。
    seen: set[tuple[int, str]] = set()
    for attempt_no, specs in captured:
        unique: list[Any] = []
        for spec in specs:
            key = (attempt_no,
                   json.dumps(dict(spec), sort_keys=True, ensure_ascii=False, default=str))
            if key in seen:
                continue
            seen.add(key)
            unique.append(spec)
        if not unique:
            continue
        # 逐条隔离与**成功节**共用同一份实现（`_follow_up_needs_partition`）：同一条写错的申请
        # 在一节成功时与在整节失败时得到的是同一种记录，不存在「成功就悄悄吞掉、失败才留档」。
        typed, rejected = _follow_up_needs_partition(
            specs=unique, task=task, authority=authority, scan=scan,
            draft_revision=revision_for(attempt_no), policy=policy, attempt=attempt_no)
        needs.extend(typed)
        untypeable.extend(dict(record) for record in rejected)
    return tuple(needs), tuple(untypeable)


def _best_effort_proposal_identity(text: str) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    """从**未通过结构校验**的原始输出里尽力抽取该束的完整有序身份。

    只在 `schema_invalid` 这条路上用：此时没有可信的结构化结果可用，但「模型这一束里到底有
    哪些候选项」仍然必须可回查，否则「被拒的是一束什么」就只剩一句错误。抽取**不做任何修正**
    ——认不出就返回空元组，绝不猜测、绝不补齐，也绝不把它当成通过了校验的对象使用。

    只取模型**自己给出的**标签（`claim_candidates[].candidate_key` / `narrative_draft_units[].unit_key`）；
    `candidate_id` / `proposal_id` 是建立在对整束的**结构校验**之上的派生身份，束没通过校验时
    它们根本不存在，不得在这里伪造。
    """
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return (), (), 0
    if not isinstance(payload, Mapping):
        return (), (), 0

    def _labels(field: str, label_field: str) -> tuple[str, ...]:
        value = payload.get(field)
        if not isinstance(value, list):
            return ()
        return tuple(str(item[label_field]) for item in value
                     if isinstance(item, Mapping) and item.get(label_field))

    follow_ups = payload.get("follow_up_needs")
    return (_labels("claim_candidates", "candidate_key"),
            _labels("narrative_draft_units", "unit_key"),
            len(follow_ups) if isinstance(follow_ups, list) else 0)


def _best_effort_follow_up_specs(text: str) -> tuple[dict, ...]:
    """从**未通过结构校验**的原始输出里尽力取出**补件诉求原文**（逐条原样，不修正、不补齐）。

    与 `_best_effort_proposal_identity`（§二 3）挂在一处：那一路上「这一束里有哪些候选项」要
    可回查，同一个理由下「这一束里模型提过哪些补件诉求」也要可回查——否则整束终止时，
    「模型提过诉求」会随候选一起蒸发，产物里只剩一个 `follow_up_count` 计数（§二 2.6）。

    只取 `Mapping` 条目（原样 dict，不改一个键）；是否成立**不在这里判**——它由
    `_follow_up_needs` 的 Contract 校验判定，判不过就如实落到 `follow_up_untypeable`。
    """
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return ()
    if not isinstance(payload, Mapping):
        return ()
    value = payload.get("follow_up_needs")
    if not isinstance(value, list):
        return ()
    return tuple(_jsonable(dict(item)) for item in value if isinstance(item, Mapping))


@dataclass(frozen=True)
class PackWriteOutcome:
    """写入侧在**硬门通过之后**的产物（§6.4：到 `SectionDraft` 为止）。

    这里没有 `SectionClaim` / 最终 Narrative / 接受绑定 / `SectionResult` / 任何 disposition：
    那些都在门后由 3C/3D 的决定链产出。把「门前 Writer 能给出什么」与「门后系统才判定什么」
    混在一个对象里，正是本批要拆掉的东西。
    """
    draft: NS.SectionDraft
    #: 门前唯一的「请求更多材料」出口；与 `SectionDraft` **并列**，不是它的字段，也不进它的
    #: 身份（否则「写不出」会改变 draft 身份，重试就成了换身份而不是同一节的再尝试）。
    follow_up_needs: tuple[Any, ...]
    unresolved: tuple[SS.SectionUnresolved, ...]
    gate_result: NS.NarrativeGateResult
    prompt_version: str
    model_policy: str
    llm_calls: int
    #: §五 5：每次生成调用的结构化 trace（call_id / 实际模型 / prompt 版本 / tokens / 延迟 /
    #: finish_reason / 状态 / 响应指纹），供正式 preview manifest 落盘。没有调用时为空。
    llm_trace: tuple[dict, ...] = ()
    #: §二 3：本次写作里**被整束拒绝**的候选提案集的 typed 审计（含被接受的那一束之外的每一次
    #: 尝试）。被拒的候选**不是**被悄悄删掉，也不是被当成「原本就只有这些」——它们连同完整有
    #: 序身份与 typed 原因一起留在这里。空元组表示第一次尝试即被采信。
    rejections: tuple[ProposalSetRejectionRecord, ...] = ()
    #: §六：本节**被采信**的那一轮里，**自己不成立**的补件申请（`FOLLOW_UP_REJECTION_CODES` 的
    #: 逐条拒绝记录，含序号、原因码与可读原因）。它们与 `follow_up_needs` 是同一件事的两半：
    #: 「模型提过什么、其中哪几条不成立」。少了这一半，一条填错的申请就会让整节连已经通过硬门的
    #: Draft 一起消失（真实 run r5 的 financial），或者被悄悄丢掉（那等于把「诉求不成立」写成
    #: 「没有诉求」）。它**不是** gap、不进 Draft 身份，也不是候选层面的拒绝。
    follow_up_rejections: tuple[dict, ...] = ()
    coverage_summary: dict = field(default_factory=dict)
    #: §二（`wbatch-1`）：本轮写作的**分批审计**——逐轮的逐批调用读数、被采信那一轮的
    #: 批次划分与合并结果（哪些候选由哪几批合并而来、丢掉过哪些重复边、补件是否有重复）。
    #: 它记的是「这一份提案集是怎么从各批拼出来的」，因此不进 Draft 身份（身份仍只由权威 +
    #: 策略派生），只进产物供复核。
    batch_audit: dict = field(default_factory=dict)
    #: §三 A：本次写作消费的**真实材料正文**上下文（`wmctx-1`）。它必须随产物一起传下去：
    #: 蕴含评估器复核的正文与 Writer 看过的正文必须是**同一串字节**，不得各自再解析一次。
    material_context: Any = None

    def sidecar(self) -> dict:
        """§九 需要的可审计产物切片（门前部分；不含正文渲染与门后决定）。"""
        return {
            "section_draft": self.draft.to_dict(),
            "follow_up_needs": [_jsonable(n) for n in self.follow_up_needs],
            # §六：被采信那一轮里**自己不成立**的补件申请（含序号 / 封闭原因码 / 可读原因）。
            # 它与上面的成立诉求并列：只留成立的那一半，就等于把「模型提过一条填错的申请」从
            # 产物里抹掉——而「诉求不成立」与「没有诉求」是两件事。
            "follow_up_rejections": [dict(r) for r in self.follow_up_rejections],
            "section_unresolved": [_jsonable(u) for u in self.unresolved],
            "narrative_gate_result": self.gate_result.to_dict(),
            "prompt_version": self.prompt_version, "model_policy": self.model_policy,
            "llm_calls": self.llm_calls, "llm_trace": [dict(t) for t in self.llm_trace],
            # §二 3：被整束拒绝的提案集的 typed 审计。每一束都保留**完整有序**身份与拒绝原因；
            # 原始输出文本按 `narration_call_id` 存于 `logs/llm/*.jsonl`，可逐字回查。
            "proposal_set_rejections": [r.to_dict() for r in self.rejections],
            "coverage_summary": dict(self.coverage_summary),
            # §二：分批请求的审计（逐轮逐批的调用读数 + 被采信那一轮的合并结果）。
            "writer_batch_audit": _jsonable(self.batch_audit),
            # 正文本身不在这里重复一遍（manifest 成员已带 payload_ref/locator/payload 哈希/
            # 读视图指纹，任何人可重解析回同一串字节）；这里只留它的身份与指纹。
            "writer_material_context": (
                {"context_id": self.material_context.context_id,
                 "context_fingerprint": self.material_context.fingerprint(),
                 "member_refs": list(self.material_context.member_refs())}
                if self.material_context is not None else None),
        }


def _scan(authority: Any, task: PS.SectionTask) -> AuthorityScan:
    if isinstance(authority, TopicPackAuthorityInput):
        return scan_topic_pack(authority, task)
    if isinstance(authority, FinancialAuthorityInput):
        return scan_financial(authority, task)
    raise PackWriterError(
        "derived_section 的写作在本批未启用（DemoScope 未选中），不得在 M930-3 生成派生章节")


def scan_authority(authority: Any, task: PS.SectionTask) -> AuthorityScan:
    """权威输入的确定性读视图（**公开入口**）。

    §七 P0-4 1：期望集合必须从 authority 精确派生；组装器/评估器要复核缺口守恒时，必须走
    **这一个**入口重算，而不是自带第二套扫描实现（否则「权威口径」会被解释两次）。
    """
    return _scan(authority, task)


def expected_aspect_gap_ids(authority: Any, task: PS.SectionTask, *,
                            unprojected: Sequence[str] = ()) -> Mapping[str, dict]:
    """§七 2/3：权威里**非覆盖且已投影**的 aspect 在本节必须留下的缺口身份。

    返回 `{unresolved_id: {"aspect_id", "topic_id", "status"}}`，是 `write_section` 第 1 步
    （选中的非覆盖 aspect 一律登记缺口）的**同一实现** `_aspect_unresolved`；组装器据此按 id
    精确比对，不需要第二套 aspect→缺口语义。`unprojected` 由调用方给出（冻结投影之外、按
    `coverage_summary` 记录的那些 aspect 不产生缺口）。
    """
    scan = _scan(authority, task)
    policy = _question_blocking_policy(task)
    skip = {str(a) for a in unprojected}
    out: dict[str, dict] = {}
    for aspect_id in sorted(scan.aspect_status):
        if aspect_id in skip:
            continue
        status = str(scan.aspect_status[aspect_id])
        if status not in _NON_COVERED_STATUSES:
            continue
        item = _aspect_unresolved(task.section_id, aspect_id, scan, policy)
        out[item.unresolved_id] = {"aspect_id": aspect_id,
                                   "topic_id": str(scan.aspect_topic.get(aspect_id, "")),
                                   "status": status}
    return out


def _authority_container_ids(authority: Any, scan: AuthorityScan) -> tuple[str, ...]:
    """本节 Draft 允许引用的容器全集（`SectionDraft` 会校验 proposal 的容器 ⊆ 这一集合）。

    先按事实条目出现的容器（自然读序），再补齐**权威本身**声明的容器：没有任何事实的
    pack / 外部快照容器同样能承载纯材料的 `path_b_material_derived` 候选，因此它们必须
    在这里，否则「材料清单里有、容器集合里没有」会把合法的描述性候选误杀。
    """
    ids: list[str] = []
    for container in [entry.container_identity for entry in scan.facts]:
        if container not in ids:
            ids.append(container)
    if isinstance(authority, TopicPackAuthorityInput):
        for pack_id in sorted(str(p.pack_id) for p in authority.pack_set.packs):
            if pack_id not in ids:
                ids.append(pack_id)
    if isinstance(authority, FinancialAuthorityInput):
        artifact_id = str(authority.artifact.artifact_id)
        if artifact_id not in ids:
            ids.insert(0, artifact_id)
        if authority.note_facts is not None:
            container = NS.note_container_id(authority.note_facts)
            if container not in ids:
                ids.append(container)
    return tuple(ids)


# 正文渲染已收归唯一渲染器 `NS.render_paragraphs_markdown`（§五）：本模块不得再自带
# 第二套拼装逻辑，否则「写作器渲染的正文」与「draft 内容」可能不一致。


def _derive_status(unresolved: Sequence[SS.SectionUnresolved]) -> str:
    """章节状态派生：**复用** canonical `research_common.derive_status`（§七 P0-4 1/4）。

    以权威缺口为根：无缺口 → `COMPLETED`；任一缺口 state 为 `WAITING_HUMAN` →
    `WAITING_HUMAN`；任一缺口的 `blocking_effects` 含 SECTION/REPORT/JOB_BLOCKED →
    `SECTION_BLOCKED`；其余 → `COMPLETED_WITH_GAPS`。

    本模块**不得**自带第二套状态派生：同一权威缺口集合若因走哪条写作路径而得到两种章节状态，
    「权威状态守恒」就不成立（CLAUDE.md：不启用第二套）。旧实现只看 aspect 计数、完全不看
    `blocking_effects`，会把权威声明为 SECTION_BLOCKED 的缺口降级成 `COMPLETED_WITH_GAPS`。

    M930-3B：章节状态现在由**门后**的 P15 coordinator 在 `SectionResult` 上派生（本模块只到
    `SectionDraft` 为止），因此这里暂无调用点。保留它是为了**唯一口径**：门后不得另写一套
    状态派生，必须复用这一个入口，而不是把这个函数连同它的语义一起搬走。
    """
    return RC.derive_status(tuple(unresolved))


def write_section(task: PS.SectionTask, authority: Any, *, projection: ContractProjection,
                  writing_spec: Any, presentation_profile: Any,
                  llm_client: NarrationClient | None = None,
                  policy: WriterPolicy | None = None,
                  dependency_fingerprint: str,
                  created_at: str | None = None,
                  material_context: Any = None) -> PackWriteOutcome:
    """把一节写成**门前候选提案束**：`ClaimCandidate`/`NarrativeDraftUnit`/`ProposedSupportRef`
    + exact material manifest/WMPD + `SectionUnresolved` + `FollowUpNeed` + 候选态 `SectionDraft`
    + 确定性硬门结果（§6.4）。定稿 Claim / final Narrative / `SectionResult` / 任何决定一律
    **不在本模块产生**（门后 P8/P9/P10 + P15）。

    §三 A：topic authority **必须**带 `material_context`（由组合根注入 resolver 后解析真实
    正文）。缺它即 fail-closed —— 「对着 material ID 写作」不再是一条可走的路径。
    """
    policy = policy or WriterPolicy()
    projection.require_producer_kind(getattr(authority, "producer_kind", None))
    if authority.task_id != task.task_id:
        raise PackWriterError(f"权威输入不属于本任务：{authority.task_id!r} != {task.task_id!r}")
    if authority.section_id != task.section_id:
        raise PackWriterError(
            f"权威输入不属于本节：{authority.section_id!r} != {task.section_id!r}")
    if projection.contract_version != authority.contract_version or \
            projection.contract_fingerprint != authority.contract_fingerprint:
        raise PackWriterError(
            "ContractProjection 与权威输入的 Contract 身份不一致（不得跨 Contract 写作）")
    if projection.writing_spec_id != str(getattr(writing_spec, "writing_spec_id", "")):
        raise PackWriterError("ContractProjection 与传入 WritingSpec 不一致")
    # §16.7.1：`ProposedSupportRef.dependency_fingerprint` 是**结构必需**的非空字段（每条提案都
    # 必须能与它当时依赖的那组版本对得上）。因此空白值不是「缺省」，而是「这次写作没有可追溯的
    # 依赖身份」：不得在此处就地编造一个指纹（编造指纹等于伪造可追溯性），只能 fail-closed，
    # 让调用方把它作为**入参**补齐。
    if not str(dependency_fingerprint or "").strip():
        raise PackWriterError(
            "dependency_fingerprint 为空：每条 ProposedSupportRef 都结构性携带依赖指纹，"
            "不得就地编造，必须由调用方给出本次写作的真实依赖指纹")

    scan = _scan(authority, task)
    # 未解决项的后果字段只能来自权威问题本身（见 `_authority_blocking_effects`）。
    question_policy = _question_blocking_policy(task)
    selected_aspects = tuple(sorted(a for a in scan.aspect_status
                                    if projection.aspect(a) is not None))
    projected_ids = {a.aspect_id for a in projection.aspects}
    unprojected = sorted(a for a in scan.aspect_status if a not in projected_ids)

    # 1) 缺口（状态原样保留）——先登记，正文不得把缺口写成完整结论。
    unresolved: list[SS.SectionUnresolved] = []
    aspect_unresolved: dict[str, SS.SectionUnresolved] = {}
    unresolved_authority_status: dict[str, str] = {}
    out_of_scope_gap_ids: dict[str, str] = {}
    period_gap_ids: dict[str, str] = {}
    if scan.period_unresolved:
        # §十二 1/3：无法证明期间的 fact 不进入正文，按归属主题各留一条显式缺口。
        grouped: dict[str, list[str]] = {}
        for _container_id, fact_id, topic_id in scan.period_unresolved:
            grouped.setdefault(topic_id, []).append(fact_id)
        for topic_id, fact_ids in sorted(grouped.items()):
            item = _period_unresolved_gap(task.section_id, topic_id, tuple(sorted(fact_ids)))
            period_gap_ids[topic_id] = item.unresolved_id
            unresolved_authority_status[item.unresolved_id] = PERIOD_UNRESOLVED_AUTHORITY_STATUS
            unresolved.append(item)
    for aspect_id in selected_aspects:
        if scan.aspect_status[aspect_id] in _NON_COVERED_STATUSES:
            item = _aspect_unresolved(task.section_id, aspect_id, scan, question_policy)
            aspect_unresolved[aspect_id] = item
            unresolved_authority_status[item.unresolved_id] = scan.aspect_status[aspect_id]
            unresolved.append(item)
    if isinstance(authority, FinancialAuthorityInput):
        # 财务缺口没有 aspect 状态：登记的「权威原始标签」必须是缺口自己携带、且**确实写在
        # 缺口正文里**的那一个（artifact 缺口用其 status；附注缺口用统一常量），否则人读标签
        # 会与缺口文本脱节（§五 4：缺口状态不得隐藏在正文之外）。
        for gap in scan.gaps:
            item = _financial_gap_unresolved(task.section_id, gap, authority)
            unresolved_authority_status[item.unresolved_id] = str(
                gap.get("status") or NOTE_GAP_AUTHORITY_STATUS)
            unresolved.append(item)
        if authority.note_gap is not None:
            item = _note_gap_unresolved(task.section_id, authority)
            unresolved_authority_status[item.unresolved_id] = NOTE_GAP_AUTHORITY_STATUS
            unresolved.append(item)
        # 本节某个 topic 在权威输入里**一条事实都没有**：这只能是显式缺口（更不得把别的主题
        # 的事实挪过来填满它，也不得凭空空写）。登记后由正文如实呈现「缺什么、影响哪项结论」。
        for topic_id in scan.topics_without_facts:
            item = _topic_fact_gap_unresolved(task.section_id, topic_id)
            unresolved_authority_status[item.unresolved_id] = NO_FACT_AUTHORITY_STATUS
            unresolved.append(item)
        # 本节之外的 required 事实不呈现时必须留下显式缺口（按归属主题分组）。
        for topic_id, fact_ids in sorted(
                _out_of_scope_required_facts(authority, scan).items()):
            item = _out_of_scope_facts_unresolved(task.section_id, topic_id, fact_ids)
            out_of_scope_gap_ids[topic_id] = item.unresolved_id
            unresolved_authority_status[item.unresolved_id] = ""
            unresolved.append(item)

    # 2) 精确材料清单 + draft revision（**先于**生成能力定型：模型不得改变本次写作的身份）
    #
    # `draft_revision` 依赖 manifest，而候选 / 门前叙述单元 / proposal 三者必须共享同一个
    # revision。若让模型输出参与身份，「重试」就变成了「换一个身份」，而不是同一节的再尝试。
    # `pw-15`：修订还覆盖**草稿层**内容（`prose_digest`），但必须在「候选还不存在」的那一刻
    # 就能算出来——因此它由**规格**（合并后草稿的顺序/文本/出处）算，由
    # `_prose_specs_digest` 与单元口径共用同一个实现。
    manifest = _derive_material_manifest(authority, material_context=material_context)

    def _revision_for(attempt_no: int, *, prose_digest: str = "") -> str:
        """第 `attempt_no` 次生成的 draft revision（`attempt` 进身份，见 `derive_draft_revision`）。

        重写**必须**产出新修订：被拒的那一束与重写后的那一束共享同一个修订，产物里就会出现
        「同一个修订既被拒又被采信」，而重写掉的那几条候选与它们的逐条拒绝原因就再也对不上号。

        `prose_digest`（`pw-15`）是这一轮**草稿层**的内容摘要。空串表示本节没有草稿层（`narr-6`
        及更早的形态），此时修订取值与 `pw-14` **一字不变**；带草稿层时修订还覆盖草稿内容——
        否则「同一修订、两段不同的草稿」在产物里无法区分，草稿层等于没有进身份。
        """
        return NS.derive_draft_revision(
            natural_prose_digest=str(prose_digest or ""),
            task_id=task.task_id, section_id=task.section_id, company_id=authority.company_id,
            report_as_of=authority.report_as_of, contract_version=authority.contract_version,
            contract_fingerprint=authority.contract_fingerprint,
            writer_policy_version=policy.policy_version, prompt_version=policy.prompt_version,
            model_policy=policy.model_policy, manifest_id=manifest.manifest_id,
            manifest_fingerprint=manifest.fingerprint(), attempt=attempt_no)

    def _bundle_revision(bundle: _PreGateBundle, attempt_no: int) -> str:
        """**某一束自己的** draft revision：修订必须覆盖这一束的草稿层（`pw-15`）。

        只按轮次算会把带草稿层的那一轮算成「没有草稿层」的修订：产物里于是出现「`SectionDraft`
        的 revision 是 R，而属于**同一束**的待裁决补件指向 R′」，两者只差 `prose_digest` 一项——
        读的人无法分辨那是两轮还是同一轮，草稿层等于没进身份。凡是「拿一束去问它自己的修订」
        的地方都必须走这里。

        退回 `_revision_for(轮次)` 的只有「这一轮根本没有束」的两种情形：整节失败时待裁决补件
        的归属修订（`draft_revision` 首选），以及**被拒**轮次的诉求归属——被拒的那一束不留
        产物（只留候选身份与逐条原因），草稿层因此不可复算，强行编一个带草稿层的修订就是
        编事实。那两处按 `_revision_for` 的空草稿层取值，并与 `pw-14` 逐字一致。
        """
        return _revision_for(attempt_no,
                             prose_digest=NS.natural_prose_draft_digest(bundle.prose_units))

    #: 首轮修订。它同时是**失败路径**（整节无 Draft）下待裁决补件与未采信轮次的归属修订：
    #: 那种情况没有任何一次被采信，本节写作任务的规范修订就是首轮那一份。
    draft_revision = _revision_for(1)

    # 3) 未被任何权威事实覆盖的 blocking aspect：显式 unresolved（不静默）
    covered_aspects = {a for entry in scan.facts for a in entry.aspect_ids}
    for aspect_id in sorted(a for a in scan.aspect_blocking
                            if scan.aspect_blocking[a] and a not in covered_aspects
                            and a not in aspect_unresolved):
        item = _aspect_unresolved(task.section_id, aspect_id, scan, question_policy)
        unresolved_authority_status[item.unresolved_id] = scan.aspect_status[aspect_id]
        unresolved.append(item)

    # 4) §七 P0-4 1/2：期望集合必须从权威 task **精确派生**。上面每一步缺口登记都可能只覆盖
    # 到 topic / aspect 粒度，因此这里按权威问题做一次**完备性收口**：每个声明的问题要么已有
    # 可用权威事实（与既有覆盖规则 1 的期望集合同源），要么必须留下显式缺口。没有这一步，
    # 「问题既没事实也没缺口」会从 Draft 与 Result **同时**消失（§七 P0-4 4）—— 缺口不是被
    # 发现，而是被隐藏。
    covered_questions = {qid for entry in scan.facts
                         for qid in _question_ids_for_topic(
                             task, entry.topic_id, f"权威事实 {entry.fact_id}")}
    declared_questions = {u.question_id for u in unresolved if u.question_id}
    for question in task.questions:
        question_id = str(question.question_id)
        if question_id in covered_questions or question_id in declared_questions:
            continue
        item = _question_gap_unresolved(task.section_id, question,
                                        str(getattr(question, "topic_id", "") or ""))
        unresolved_authority_status[item.unresolved_id] = ""
        unresolved.append(item)

    # §六：逐 topic 的确定性计数（事实总数 / 因期间要求被排除 / 可用事实），供人读报告核对
    # 「到底是真的没有事实，还是被期间规则挡掉了」—— 两者的缺口含义完全不同。
    topic_fact_counts: dict[str, dict] = {}
    facts_by_authority_kind: dict[str, int] = {}

    def _bump(topic_id: str, key: str) -> None:
        entry = topic_fact_counts.setdefault(
            topic_id, {"facts_total": 0, "facts_available": 0, "excluded_by_period": 0,
                       "excluded_scope": 0})
        entry[key] += 1
        entry["facts_total"] += 1

    for fact_entry in scan.facts:
        _bump(fact_entry.topic_id, "facts_available")
        facts_by_authority_kind[fact_entry.authority_kind] = (
            facts_by_authority_kind.get(fact_entry.authority_kind, 0) + 1)
    for _container_id, _fact_id, topic_id in scan.period_unresolved:
        _bump(topic_id, "excluded_by_period")
    for _container_id, _fact_id, topic_id in scan.excluded_facts:
        _bump(topic_id, "excluded_scope")
    for topic_id in scan.topics_without_facts:
        topic_fact_counts.setdefault(
            topic_id, {"facts_total": 0, "facts_available": 0, "excluded_by_period": 0,
                       "excluded_scope": 0})

    # §6.4：coverage_summary 只允许由**权威**派生（事实目录 / 缺口 / 材料清单 / 投影）。模型
    # 输出不得进入这里 —— 它参与 draft 身份，若掺入生成结果，同一权威在两次重试下会得到不同
    # 的 draft 身份，身份就不再是「这一次写作」而是「这一次输出」。
    coverage_summary = {
        "aspects": [{"aspect_id": a, "topic_id": scan.aspect_topic.get(a, ""),
                     "status": scan.aspect_status[a]} for a in sorted(scan.aspect_status)],
        "counts": dict(sorted(scan.coverage_counts.items())),
        "unprojected_aspects": unprojected,
        "material_available": len(manifest.entries),
        "facts_total": len(scan.facts) + len(scan.excluded_facts),
        "facts_by_authority_kind": dict(sorted(facts_by_authority_kind.items())),
        "required_facts_total": sum(1 for e in scan.facts if e.required),
        "facts_outside_section_topics": len(scan.excluded_facts),
        "topics_without_facts": list(scan.topics_without_facts),
        "topic_fact_counts": topic_fact_counts,
        "period_exclusions": [dict(item) for item in scan.period_exclusions],
        "gaps_outside_section_topics": [
            {"fact_id": g.get("fact_id"), "formula_id": g.get("formula_id"),
             "status": g.get("status"), "reason_code": g.get("reason_code")}
            for g in scan.out_of_scope_gaps],
        "unresolved_total": len(unresolved),
    }
    created = created_at or _utcnow()

    def _draft_fields(bundle: _PreGateBundle, attempt_no: int) -> dict:
        """门前束 + 权威派生状态 → `SectionDraft.create` 的入参（逐次重试可复算）。

        `draft_revision` 与 `writer_attempt` **成对**取自这一束是第几次生成：两者不一致
        （例如修订是首轮的、attempt 却是 3）会在 `SectionDraft.__post_init__` 的修订重算处
        直接 fail-closed——它们不是两个独立字段，是同一次生成的两个表达。
        """
        return dict(
            draft_revision=_bundle_revision(bundle, attempt_no),
            writer_attempt=attempt_no,
            task_id=task.task_id, section_id=task.section_id,
            company_id=authority.company_id, report_as_of=authority.report_as_of,
            contract_version=authority.contract_version,
            contract_fingerprint=authority.contract_fingerprint,
            producer_kind=authority.producer_kind,
            writer_policy_version=policy.policy_version,
            prompt_version=policy.prompt_version, model_policy=policy.model_policy,
            authority_container_ids=_authority_container_ids(authority, scan),
            material_manifest=manifest,
            material_dispositions=_derive_material_processing(
                manifest=manifest, proposals=bundle.proposals, policy=policy),
            claim_candidates=bundle.candidates, narrative_draft_units=bundle.units,
            # `pw-15`：草稿层随束一起进 `SectionDraft`（空元组 = 本节没有草稿层，老路径不变）。
            # 它与候选**同一修订**：`SectionDraft.__post_init__` 会按草稿内容重算修订并核对，
            # 两处算出的东西不同即当场 fail-closed。
            natural_prose_draft=bundle.prose_units,
            proposed_support_refs=bundle.proposals,
            unresolved_ids=tuple(u.unresolved_id for u in unresolved),
            unresolved_projections=_unresolved_projections(unresolved,
                                                           unresolved_authority_status),
            coverage_summary=coverage_summary, conflict_projections=scan.conflicts,
            not_found_projections=scan.not_found,
            dependency_fingerprint=dependency_fingerprint,
            created_at=created, title=task.title,
            # §四：写入侧身份（规则 / 渲染器版本）随产物一起留存，组装器才能从产物本身重算
            # `section_version`，而不是去猜一个版本常量。
            writer_rules_version=policy.rules_version,
            writer_renderer_version=policy.renderer_version)

    def _gate_failure(issues: Sequence[NS.NarrativeGateIssue]) -> str:
        return json.dumps([{"rule": i.rule_id, "location": i.location, "detail": i.detail}
                           for i in issues], ensure_ascii=False)

    def _assemble(bundle: _PreGateBundle,
                  attempt_no: int) -> tuple[NS.SectionDraft, NS.NarrativeGateResult]:
        """门前束 → `SectionDraft` → 确定性硬门（无 LLM、无自评，纯函数）。"""
        candidate_draft = NS.SectionDraft.create(**_draft_fields(bundle, attempt_no))
        return candidate_draft, NS.gate_draft(candidate_draft, authority)

    # 5) 门前唯一的生成调用（唯一外部能力）：候选 + 提案 + 补件申请
    #
    # 既无权威事实、又无材料时**不是**空写：本节按 §五/§七 只能以「仅缺口」形态存在
    # （SECTION_BLOCKED + 缺口清单），该形态在 `SectionDraft` 里是合法表达。此时没有任何可选
    # 对象，因此不调用生成能力，也不向 LLM 索要空正文。既无对象又无缺口才是「凭空空写」，
    # 仍然 fail-closed。
    bundle: _PreGateBundle | None = None
    draft: NS.SectionDraft | None = None
    gate_result: NS.NarrativeGateResult | None = None
    llm_calls = 0
    llm_trace: list[dict] = []
    #: §二 3：整束被拒的 typed 审计。**每一次**未被采信的生成都留一条，含它那一束的完整有序
    #: 身份（不是过滤后的子集）与 typed 原因；成功路径随产物落盘，失败路径随异常带出。
    rejections: list[ProposalSetRejectionRecord] = []
    #: §二 2.6：**被拒那些轮**提出的补件诉求原文（轮次 + 原始 spec）。它们不随候选一起被拒：
    #: 候选提案集被拒说明「这一束候选不能用」，不说明「模型没有要求更多材料」。不在这里取下来，
    #: 一旦整束终止（`ProposalSetRejectedError`）它们就随之蒸发——这正是「模型给了 6 条诉求、
    #: 产物里一条也查不到」的成因。**只留存，不执行**：裁决权在 Harness。
    rejected_follow_up_specs: list[tuple[int, tuple[dict, ...]]] = []
    #: §二 分批：**每一轮**的合并审计（有序，一轮一条）。它记录「这一轮拆成了哪几批、每批回答
    #: 哪些 aspect、哪些候选/单元/诉求在批间重复并被合并」——分批是请求面的切分，因此合并的
    #: 每一步都必须可复核：读的人要能自己走一遍「4 批 → 1 份提案集」。它**不**参与任何判定，
    #: 也不进入 Draft 身份（身份由 `SectionDraft` 自行内容寻址）。
    batch_audit_rounds: list[dict] = []
    #: 本节当前是第几次生成。**两条路径都要有值**：生成路径由循环递增，无事实/无材料那条
    #: 路径只有一次（不调生成能力）——它同样是「第 1 次生成」，因此修订与补件归属都取 1。
    #: 它同时是成功路径上「被采信的是哪一轮」的唯一键。
    attempt = 0
    if not scan.facts and not manifest.entries:
        if not unresolved:
            raise PackWriterError(
                "权威输入既没有产出任何权威事实/材料、也没有任何显式缺口，不得凭空空写章节")
        bundle = _build_pre_gate_bundle(
            # 这一形态没有生成能力参与，也就没有可保留的草稿层：四个键齐备（与
            # `_parse_plan` / `_merge_batch_plans` 的产物同形），草稿层为空元组。
            plan={"natural_prose_draft": (), "claim_candidates": (),
                  "narrative_draft_units": (), "follow_up_needs": ()},
            task=task, authority=authority, scan=scan, policy=policy,
            draft_revision=draft_revision, manifest=manifest,
            dependency_fingerprint=dependency_fingerprint)
        # 没有任何可选对象 ⇒ 本节不生成（不调生成能力）：这一份束就是首轮那一份。
        attempt = 1
        draft, gate_result = _assemble(bundle, attempt)
        if gate_result.blocking:
            raise PackWriterError("narrative 硬门未通过，fail-closed（不产出章节）："
                                  + _gate_failure(gate_result.issues_of("blocking")))
    else:
        if llm_client is None:
            raise PackWriterError("缺少 NarrationClient：Writer 不具备除生成之外的任何能力")
        # §二：**确定性**批次划分。本节 aspect 不超过 `MAX_ASPECTS_PER_BATCH` 时恰好一批，
        # 此时链路与「整节一次请求」逐字等价（只是输入面多一段 `batch` 说明）。
        base_batches = plan_aspect_batches(projection, selected_aspects, scan)
        # 支撑选项别名表（`saref-1`）：请求侧（`build_narration_messages` 内）与解析侧读的是
        # **同一个确定性派生**（同一函数、同一 `scan` 与 `manifest`），因此「请求里声明了哪些
        # 行」与「解析时能展开哪些行」不可能是两张表。
        aliases = _support_aliases(scan, manifest)
        # §二 2.3：**只在所问事项上说话**的勾选表单行的资格范围（`selappl-1`）。它从已解析的
        # 材料正文上下文确定性派生（不是模型自报、也不是本模块另算的一份形态判定），整节算一次：
        # 材料集合在一次写作里不变，因此「这条边能不能承重」在每一轮都按同一份表核。
        selection_scope = _selection_applicability_scope(material_context=material_context)
        #: 追加在**完整原请求之后**的说明（返修说明 / 栏目定向说明走同一条通道，见 `_append_note`）。
        extra_note = ""
        #: C4：**批内**说明（`bsc-1` 批次形状纠正），按 `batch_id` 逐批存放。它与 `extra_note`
        #: 是两回事：`extra_note` 是**整轮**的属性（同一轮每一批看到同一段话），批内说明只发给
        #: 那一个**失败批次**的请求——因为「本批的 c9」在别的批次的编号体系里根本不存在，
        #: 把批内错误广播给每一批，就是让其余批次去修一件它们没有做过的事（r7b 现场如此）。
        batch_notes: dict[str, str] = {}

        def _add_note(note: str) -> None:
            """把一段说明追加到**当前**追加说明之后（同一条通道，逐字增长）。

            返修说明与栏目定向说明都走这里，因此「第九批看到的那段话」与「第一批看到的」是同
            一串文本：说明是**整轮**的属性，不按批各写一份——按批写就会让同一轮的不同批次看到
            不同的请求纪律，账本上再也说不清「这一轮到底问了什么」。唯一的例外是批内纠正
            （`batch_notes`）：它纠正的是**那一批自己的返回**，因此按设计只发给那一批。
            """
            nonlocal extra_note
            extra_note = note if not extra_note else _append_note(extra_note, note)

        def _messages_for(target: WriterPlanBatch) -> tuple[list[dict], str]:
            """一批请求的消息体（每次都从**完整**原请求重新构建，再逐字追加当前说明）。

            重构建而不是缓存第一次的字符串：批次不同，`projection.aspects` 就不同（那正是分批
            的语义），因此「同一份输入」指的是**同一批**的输入，而不是「第一次那份」。

            追加顺序固定为「整轮说明 → 本批的批内纠正说明」：两段都在**完整原请求之后**，
            输入面不裁剪、system 与 prompt 版本都不换。
            """
            messages, system = build_narration_messages(
                task=task, authority=authority, scan=scan, projection=projection,
                projection_aspects=target.aspect_ids, unresolved=unresolved,
                presentation_profile=presentation_profile, manifest=manifest,
                material_context=material_context, batch=target)
            note = extra_note
            local = batch_notes.get(str(target.batch_id), "")
            if local:
                note = _append_note(note, local) if note else local
            return [{"role": "user",
                     "content": _append_note(messages[0]["content"], note)}], system

        # §五 2/3：记录版本必须等于**实际加载到的** prompt 资产版本（加载后立刻核对）。
        # 批次只改变输入正文，system（prompt 资产）对每一批都是同一份，因此核对一次即可。
        verify_prompt_asset(_messages_for(base_batches[0])[1], policy)
        last_error: Exception | None = None
        # 返修级问题只给**一次**额外尝试：返修（rework）不是无限预算，但也不得一次都不给；
        # 预算用尽后剩下的返修问题**如实记录**在返回的 gate_result 里，绝不静默吞掉。
        rework_retried = False
        # `attempt` 已在上面统一声明（两条路径共享同一个「第几次生成」坐标），此处不再重置：
        # 重置会让无事实那条路径已经用掉的轮次与这里重新从 0 开始，两处坐标对不上。
        #: §三 A / 3.3 栏目分步写作：本轮写作里已经用掉了几次**栏目定向**调用（独立额度，
        #: 不与 `max_llm_retries` 竞争，见 `MAX_SUBSECTION_FOCUS_PASSES`）。
        focus_passes = 0
        #: 被点名过的栏目（已请求过栏目定向补足的那些）。第二次栏目定向**不重复点名同一栏目**：
        #: 点过一次仍未产出，说明不是「模型漏看」，再问一遍只是同一句话问第三遍。
        focus_requested: set[str] = set()
        #: 已经进入「栏目定向」那一次调用之后为 True。栏目定向是**恰好一次**的独立调用：
        #: 失败即整节 fail-closed（如实带上 typed 审计），不再借 `max_llm_retries` 的额度
        #: 重发——否则「一次格式抖动」和「栏目真的没材料」会被混成同一类结果。
        focus_mode = False
        #: C3：本轮写作已经排定过几次**定向重提案**（上界 `MAX_DIRECTED_REPROPOSAL_PASSES`）。
        #: 它**不**新增调用额度：定向轮用的就是本策略已有的一轮重试（见该常量处逐步推导）。
        #: 两种触发原因（高风险面 / 支撑资格）共用这一个计数器，见排定处的逐步推导。
        directed_reproposal_passes = 0
        #: C3：被排定的那一轮**还没**产出结构化提案集之前，暂存原束与它的记录下标，
        #: 等下一轮真的合并成功时再算出逐候选去向并**就位**写回那条记录。
        pending_directed: dict | None = None
        #: C3：这一轮已经追加过**定向**说明了。通用说明（「上一次输出被拒：…」）与定向说明
        #: 只能二选一：两句一起发，通用那句的空泛表述会把定向内容的重点稀释掉。
        directed_note_added = False
        #: C3：定向重提案的额度已用尽（或无剩余轮次可排定）。置位后一律 fail-closed，
        #: 不再借通用重试通道把同一件事重问一遍。
        directed_quota_exhausted = False
        #: C4：**批次形状纠正**的停止位。某一批的返回形状不合法、而纠正这条路已经不可用
        #: （该批已纠正过一次，或本轮共用额度已用尽）时置位：此后一律 fail-closed，**不再**
        #: 借通用重试通道把整条扫描重跑一遍。理由是同一个：整轮重跑会丢掉其余批次已经拿到的
        #: 合法返回（那既不是「重问一次」的代价，也不产生任何新信息）。
        batch_shape_stop = False
        #: C4：本节**逐批**已经纠正过几次（`batch_id` → 次数）。按批计的上界是
        #: `MAX_BATCH_SHAPE_CORRECTIONS_PER_BATCH`：再次失败即停。
        shape_corrected: dict[str, int] = {}
        #: C4：已经排定纠正、但纠正那一次的**去向**还没回填的条目（`batch_id` → 那一条
        #: `shape_corrections` 条目本身，**可变 dict**）。存条目而不是下标：下标只在同一轮的
        #: 列表里有意义，而回填可能发生在下一轮（条目跟着自己那一轮的列表走，不会指错地方）。
        pending_correction: dict[str, dict] = {}
        #: §一.2 补充裁决：已经排定、**还没产出**的那一轮**逐候选裁出**（新修订）。它不来自任何
        #: 调用：`plan` 是上一轮判定时从原束裁出的完整提案集，`decision` 是那一刻的裁决，
        #: `bundle` 是原束（回填跨修订对账要用它的逐字候选文本），`record_index` 是原束那条
        #: 拒绝记录在 `rejections` 里的下标。下一轮真的产出结构化提案集时用它回填 `carve_out`。
        pending_carve_out: dict | None = None

        def _resolve_correction(batch_id: str, *, call_id: str | None,
                                response_hash: str | None, outcome: str) -> None:
            """C4：把某一批**纠正那一次调用**的去向就地回填到它那一条条目。

            `outcome` 只有三种，都是**观测到的结局**，不是判定：`accepted`（那一次返回通过了
            结构校验）、`rejected`（那一次返回同样不合法）、`truncated`（那一次返回被 provider
            截断）。三种之外的措辞一律不写——「纠正有没有用」由这三个结局自己说话。
            """
            entry = pending_correction.pop(batch_id, None)
            if entry is None:
                return
            entry["correction"] = {"call_id": call_id, "response_hash": response_hash,
                                   "outcome": outcome}

        def _reject(kind: str, detail: str, *, source: _PreGateBundle | None,
                    text: str, result: NarrationResult | None,
                    high_risk: Mapping[str, Sequence[str]] | None = None,
                    ineligible: Mapping[str, Sequence[str]] | None = None,
                    history_only: Mapping[str, Sequence[str]] | None = None,
                    unproven_current_state: Mapping[str, Sequence[str]] | None = None,
                    gate_issues: Sequence[NS.NarrativeGateIssue] = (),
                    named_subsections: Sequence[str] = (),
                    batches: Sequence[BatchCallRecord] = (),
                    call_id: str | None = None,
                    answered_by_attempt: int | None = None,
                    retained_batches: Sequence[tuple[Any, Mapping[str, Any]]] = ()) -> None:
            """记下**这一整束**被拒（完整有序身份 + typed 原因 + 逐候选归属），再交给上层抛错。

            身份取自**这一束自己**：已经通过结构校验的走 bundle 的完整集合；连结构校验都没过的
            走 `_best_effort_proposal_identity` 的尽力抽取。两条路都**不做**任何过滤——
            「被拒的是哪几个」必须与模型当时输出的那一束逐一对应。

            `source is None`（整束从未成为结构化对象）时逐候选归属**必须**留空：那时没有任何
            可信的候选身份，凭输出文本编一份逐候选原因就是把猜测写成审计。

            `batches` 是**本轮**逐批的调用读数（有序）；`call_id` 是代表调用 id（截断那条路上
            没有 `NarrationResult`，只有 provider 侧响应里的 call id）。

            `answered_by_attempt`（C3）是「这一次拒绝**已排定**由第几次生成做定向重提案」。
            它在拒绝发生的**当下**定下，因此记的是排定而不是结果——结果由那一轮的产物（或那条
            记录上后补的 `reproposal` 逐候选去向）给出。`None` 一律读作「没有排定」。

            `retained_batches`（指令 D §三）是**本轮**已经合法解析的那些批次返回（`batch_plans`）。
            整轮因某一批不合法而终止时，它们与这次失败毫无关系，只是恰好排在同一轮里；不留存就
            会随异常一起蒸发（真实 run r8 的 company 节）。它**只**在 `source is None`（合并束从未
            成形）时参与留存——合并束已经成形时用**它**，逐批坐标反而已不可考（见
            `_retained_pre_gate`）。

            §一.2 的**逐候选裁出**是第三条出口：这一束**不**整束作废，而是把不合格的几条逐条
            拒掉、其余以新修订继续走链。它同样**不**走本函数的参数——裁出的对账
            （`carve_out`）在下一修订真的产出后才由 `replace(...)` 回填，理由与 `reproposal`
            逐字相同：没有跨修订事实就不得写一条空的。裁出这一路上 `answered_by_attempt` 必须
            留 `None`（裁出**没有**向模型重问任何东西，因此没有「排定给第几轮」这回事）。
            """
            if source is not None:
                candidate_ids = tuple(str(c.candidate_id) for c in source.candidates)
                proposal_ids = tuple(str(p.proposed_support_id) for p in source.proposals)
                unit_ids = tuple(str(u.draft_unit_id) for u in source.units)
                follow_ups = len(source.follow_up_specs)
                candidate_audit = _candidate_audit(
                    bundle=source, high_risk=high_risk, ineligible=ineligible,
                    history_only=history_only,
                    unproven_current_state=unproven_current_state, gate_issues=gate_issues)
            else:
                candidate_ids, unit_ids, follow_ups = _best_effort_proposal_identity(text)
                proposal_ids = ()
                candidate_audit = ()
            # 这一轮的诉求原文原样取下来（`_jsonable` 只做可序列化转换，不做裁剪/改写）。
            # **两条拒绝路径都要取**：`source=None` 是「连结构校验都没过」，此时
            # `follow_up_needs` 原文仍在那段 JSON 里，尽最大努力原样取出——取不出才是真的没有。
            specs = (tuple(_jsonable(dict(s)) for s in source.follow_up_specs)
                     if source is not None else _best_effort_follow_up_specs(text))
            if specs:
                rejected_follow_up_specs.append((attempt, specs))
            # 代表调用 id：分批链上「这一轮被拒」由若干次调用构成。由**单批**输出直接造成的
            # 拒绝（`schema_invalid` / `batch_truncated`）传进来的就是那一次；整束级拒绝
            # （高风险面 / 硬门 / 栏目零产出 / 身份闭不上）没有单一肇事批次，按**开篇定义**
            # 取本轮**第一次**带 provider id 的调用。它只是指路牌，完整有序的逐次读数在
            # `batches` 里；取不到就如实留 `None`，不编一个 id。
            representative = (result.call_id if result is not None else call_id)
            if representative is None:
                representative = next(
                    (b.call_id for b in tuple(batches) if b.call_id), None)
            # 响应指纹同理取同一路：有 `NarrationResult` 就用它自己的，否则用代表调用的那一份。
            # 逐批各自的指纹仍在 `batches` 里逐条留痕，不在这里合并。
            representative_hash = (result.response_hash if result is not None else
                                   next((b.response_hash for b in tuple(batches)
                                         if b.call_id == representative and b.response_hash),
                                        None))
            rejections.append(ProposalSetRejectionRecord(
                attempt=attempt, rejection_kind=kind, rejection_detail=detail,
                candidate_ids=candidate_ids, proposal_ids=proposal_ids,
                draft_unit_ids=unit_ids, follow_up_count=follow_ups,
                narration_call_id=representative,
                response_hash=representative_hash,
                candidate_audit=candidate_audit,
                named_subsections=tuple(named_subsections),
                batches=tuple(batches),
                answered_by_attempt=answered_by_attempt,
                retained_pre_gate=_retained_pre_gate(source=source,
                                                     batch_plans=retained_batches)))

        while True:
            # §一.2：这一轮是不是**零调用的裁出轮**——它的提案集逐字来自上一轮从原束裁出的
            # 结果，不向模型重问任何东西。它因此在轮次准入上与 `focus_mode` 同类：都不吃
            # `max_llm_retries` 这份额度，因为**额度约束的是调用**，而这一轮一次调用都不发
            # （`batch_audit_rounds` 里那一轮的 `calls: 0` 可逐轮核对）。有界性也逐步可推：
            # 裁出只在**非**裁出轮上排定（`is_carve_out_round` 参与判据），每次裁出吃掉一个
            # 模型轮，而模型轮的上界就是上面那条准入条件。
            is_carve_out_round = pending_carve_out is not None
            # §一.2 / `cco-6`：这一轮的提案集是**按哪一条轴**裁出来的。禁止「同一条轴连裁两次」
            # 的依据是逐轴成立的，不是整轮的：新束是原束**删掉被点名项**的子集，因此同一条轴上的
            # 判据不可能再点名一个新对象（被点名的那几个已经不在束里了，而且是同一个确定性函数
            # 在同一束上一次算出来的 ⇒ 它当时就把该轴上的对象**全部**点名了）。这条推理对**另一条
            # 轴**不成立：候选轴裁出这一轮根本没跑过 narrative 门（它在高风险面就被拒了），
            # context 单元那一轴在那一轮里是**第一次**被看见，它对这一束的读数与上一条轴无关。
            # 因此守卫写成「同轴不得连裁」而不是「裁出轮不得再裁」——后者会让「候选越权先被裁出、
            # 紧接着单元越权被门发现」这条**正常**序列永远走不通（r9 现场正是这条序列）。
            carve_out_axis = ("" if pending_carve_out is None else
                              ("context_units" if pending_carve_out["decision"]
                               .excluded_context_units else "candidates"))
            # 轮数上限显式写在这里，而不是塞进 `while` 条件：栏目定向那一次是**已获准**的额外
            # 调用（`focus_mode`），它与「重试额度」是两笔账，混在一个条件里就会让「第几次调用」
            # 与「第几次重试」变成同一个数字。
            if not focus_mode and not is_carve_out_round and attempt > policy.max_llm_retries:
                break
            # `attempt` **单调递增**（不因栏目定向归零）：它进入 `ProposalSetRejectionRecord`，
            # 是「被拒的是第几次生成」的审计坐标。归零会让同一节出现两条 `attempt=1` 的记录。
            attempt += 1
            # 这一轮的修订：**每一次生成一份**（含栏目定向那一次）。同一节里两束共享一个修订，
            # 就等于允许「被拒的那一束」与「被采信的那一束」在产物里无法区分。
            #
            # `pw-15`：修订**不在这里**算——它要覆盖本轮的草稿层内容，而草稿层是这一轮的模型
            # 返回，此刻还不存在。改在合并出一份完整提案集之后、构造门前束之前算（见下面
            # `_build_pre_gate_bundle` 的调用点）。若在这里先按「无草稿」算一份、再拿它去构造
            # 带草稿的束，产物里就会出现「决定的 `to_revision` 与那一轮的 `draft_revision`
            # 不是同一份」——而跨修订对账（`decision.to_revision` 对 `draft.draft_revision`）
            # 正是靠这两个值相等才成立的。
            #: C3：这一轮是不是**被排定的定向重提案轮**（上一轮的高风险面拒绝点名了它）。
            #: 它只影响审计标注：这一轮的输入面比别的重试轮多一段定向说明，账本上要看得出来。
            is_directed_round = (pending_directed is not None
                                 and attempt == pending_directed["attempt"] + 1)
            # ---- 本轮 = 一次**分批扫描**：逐批请求，逐批解析；任何一批没成功就没有本轮结果。
            # 逐批的结果先攒在内存里；**全部批次成功后**才合并成一份完整提案集送门。因此
            # 「这一轮失败」不会留下部分正式 Draft/Claim/Pack 写入——本函数只返回门前束，
            # 写入侧的持久化全部发生在返回之后（组合根）。
            # §一.2：裁出轮**没有**要问的批次（`queue` 空 ⇒ 下面那段分批扫描一次都不跑，
            # `round_calls` 因此为 0，`round_records` 为空——「不新增调用」是产物里读得出来的
            # 事实，不是一句承诺）。它的提案集在合并那一步直接取自排定的裁决。
            queue: list[WriterPlanBatch] = [] if is_carve_out_round else list(base_batches)
            shrink_used = 0
            #: C4：本轮**被拒的批次返回**逐条留痕（有序），与 `round_records` 一样是**本轮**的
            #: 读数：`round_records` 记「这一轮调用了几次、每次的 provider 身份」，这里记
            #: 「哪一批的返回为什么没被接受、纠正那一次又是什么下场」。两者互补，不互相替代。
            shape_corrections: list[dict] = []
            #: §一.2：本轮**逐候选补救**过的批次返回（有序）。它与 `shape_corrections` 是两件
            #: 事：那一条记「哪一批的返回被拒了、纠正那一次什么下场」，这一条记「哪一批的返回
            #: **被接受了**、只是其中哪几条候选被逐条拒掉」。两者互斥——同一批的一次返回不会
            #: 既被补救又被拒；`model_calls_added` 恒为 0（补救不发任何调用）。
            batch_salvages: list[dict] = []
            batch_plans: list[tuple[WriterPlanBatch, dict]] = []
            round_records: list[BatchCallRecord] = []
            round_calls = 0
            is_focus_round = focus_mode
            merge_audit: dict = {}
            #: 本轮的批次是否**全部成功并合并成了一份**完整提案集。合并之后的整束级判定
            #: （高风险面 / 硬门 / 栏目零产出）仍可能拒绝它，因此「合并成功 ≠ 被采信」。
            merged = False
            try:
                while queue:
                    target_batch = queue.pop(0)
                    batch_messages, batch_system = _messages_for(target_batch)
                    try:
                        result = llm_client.narrate(
                            messages=batch_messages, system=batch_system,
                            prompt_version=policy.prompt_version, model_policy=policy.model_policy)
                    except llm.LLMTruncatedResponse as exc:
                        # §二：**该批结果被拒绝**——半截 JSON 永不解析、永不采纳。截断带 provider
                        # 侧身份（call_id / usage），因此照样逐次入账，并如实记下它当时在回答什么。
                        truncated = exc.response
                        truncated_call_id = str(getattr(truncated, "call_id", "") or "") or None
                        # C4：这一批上一次被拒的返回排定过一次纠正，而**纠正那一次**被截断：
                        # 如实回填 `truncated`——它不是「没有回应」，也不是「又被拒了一次」。
                        _resolve_correction(
                            str(target_batch.batch_id), call_id=truncated_call_id,
                            response_hash=NS.body_fingerprint_of(
                                str(getattr(truncated, "text", "") or "")),
                            outcome="truncated")
                        llm_calls += 1
                        round_calls += 1
                        round_records.append(BatchCallRecord(
                            batch_id=target_batch.batch_id, label=target_batch.label(),
                            aspect_ids=target_batch.aspect_ids, status="truncated",
                            call_id=truncated_call_id,
                            response_hash=NS.body_fingerprint_of(
                                str(getattr(truncated, "text", "") or "")),
                            output_tokens=getattr(truncated, "output_tokens", None),
                            finish_reason=getattr(truncated, "finish_reason", None),
                            shrink_depth=target_batch.shrink_depth, focus=is_focus_round,
                            parent_batch_id=target_batch.parent_batch_id))
                        llm_trace.append({
                            "call_id": truncated_call_id,
                            "model": getattr(truncated, "model", None),
                            "prompt_version": policy.prompt_version, "status": "error",
                            "input_tokens": getattr(truncated, "input_tokens", None),
                            "output_tokens": getattr(truncated, "output_tokens", None),
                            "latency_ms": getattr(truncated, "latency_ms", None),
                            "finish_reason": getattr(truncated, "finish_reason", None),
                            "error": f"{type(exc).__name__}: {exc}",
                            "response_hash": NS.body_fingerprint_of(
                                str(getattr(truncated, "text", "") or "")),
                            "batch": target_batch.to_dict(), "focus": is_focus_round})
                        reason = (
                            f"批次 {target_batch.label()}（aspects={list(target_batch.aspect_ids)}）的"
                            f"输出被 provider 截断："
                            f"finish_reason={getattr(truncated, 'finish_reason', None)!r}，"
                            f"output_tokens={getattr(truncated, 'output_tokens', None)!r}，"
                            f"call_id={truncated_call_id!r}；该批结果被拒绝（半截 JSON 不解析、不采纳）")
                        if len(target_batch.aspect_ids) <= 1:
                            detail = (
                                ("本节没有可划分的 Contract aspect（零 aspect 的批次），"
                                 "该请求被截断后没有更小的确定性切分，fail-closed："
                                 if not target_batch.aspect_ids else
                                 "缩至**单个 aspect** 仍被截断，fail-closed：")
                                + reason + "。"
                                + ("整节这一份请求已无更小的确定性切分，"
                                   if not target_batch.aspect_ids else
                                   "单 aspect 的请求已无更小的确定性切分，")
                                + "不得静默放弃，也不得把截断输出当结果")
                            _reject("batch_truncated", detail, source=None,
                                    text=str(getattr(truncated, "text", "") or ""), result=None,
                                    batches=round_records, call_id=truncated_call_id,
                                    retained_batches=batch_plans)
                            raise PackWriterError(detail)
                        if (shrink_used + len(shape_corrections)
                                >= MAX_SWEEP_SHRINK_STEPS):
                            detail = (
                                f"本轮的分批缩小/形状纠正**共用**额度"
                                f"（{MAX_SWEEP_SHRINK_STEPS} 次，已用 "
                                f"{shrink_used + len(shape_corrections)} 次）已用尽，仍被截断，"
                                f"fail-closed：{reason}。已缩小的批次见本轮分批审计；"
                                "不得把截断输出当结果，也不得无限次重问同一批")
                            _reject("batch_truncated", detail, source=None,
                                    text=str(getattr(truncated, "text", "") or ""), result=None,
                                    batches=round_records, call_id=truncated_call_id,
                                    retained_batches=batch_plans)
                            raise PackWriterError(detail)
                        # 确定性缩小：把这一批**对半**拆开重问（切分点由 aspect 顺序唯一决定）。
                        shrink_used += 1
                        left, right = split_batch_for_shrink(target_batch, scan, projection)
                        queue[0:0] = [left, right]
                        continue
                    llm_calls += 1
                    round_calls += 1
                    llm_trace.append(({**result.to_dict(), "batch": target_batch.to_dict(),
                                       "focus": is_focus_round}
                                      if isinstance(result, NarrationResult)
                                      else {"status": "malformed",
                                            "error": "NarrationClient 未返回 NarrationResult",
                                            "batch": target_batch.to_dict(),
                                            "focus": is_focus_round}))
                    # §五 1/4：状态、prompt 版本、实际模型都必须与策略一致 —— 这类不一致不是「这次
                    # 输出不合规」，而是调用身份不符，重试没有意义，直接 fail-closed。
                    verify_narration_result(result, policy)
                    round_records.append(BatchCallRecord(
                        batch_id=target_batch.batch_id, label=target_batch.label(),
                        aspect_ids=target_batch.aspect_ids, status="ok",
                        call_id=result.call_id, response_hash=result.response_hash,
                        output_tokens=result.output_tokens, finish_reason=result.finish_reason,
                        shrink_depth=target_batch.shrink_depth, focus=is_focus_round,
                        parent_batch_id=target_batch.parent_batch_id))
                    bid = str(target_batch.batch_id)
                    try:
                        batch_plan = parse_writer_proposals(
                            result.text, aliases=aliases,
                            require_natural_draft=policy.requires_natural_draft())
                        # `bscope-1`（`pw-20`）：本批**零候选**时，草稿与草稿单元都必须为空
                        # （见 `assert_batch_no_witness_means_empty`）。放在**同一个 try** 里，
                        # 是为了让违反走**同一条**既有通道——逐候选补救 → `bsc-1` 形状纠正 →
                        # typed `schema_invalid`：**不**新增调用额度、**不**静默删段、**不**伪造
                        # 引用、**不**改绿状态。它随 `requires_natural_draft()` 一起开关：历史
                        # 兼容线（`proposals-10` 的离线重放）按当时的 wire 原样读，不追溯适用。
                        if policy.requires_natural_draft():
                            assert_batch_no_witness_means_empty(batch_plan,
                                                                batch=target_batch)
                    except PackWriterError as exc:
                        # §一.2：**先试逐候选补救**。整响应可解析、而**只有部分候选**结构非法
                        # 时，把那几条逐条拒掉，其余候选（连同本批的草稿单元与补件诉求）照样
                        # 进入本轮合并——零调用、零改写。它比 C4 的形状纠正更靠前，因为它是
                        # **确定性**的：纠正那一次要多发一次调用，而且模型仍可能只改一半。
                        # 不适用（顶层读不出 / 单元或诉求整体不合法 / 一条都没被拒 / 一条都没
                        # 幸存）时返回 `None`，下面就是**既有**的 C4 路径，一个字不改。
                        salvaged_batch = _salvage_batch_plan(
                            result.text, aliases=aliases,
                            require_natural_draft=policy.requires_natural_draft())
                        if salvaged_batch is not None:
                            salvaged_plan, rejected_candidates = salvaged_batch
                            batch_salvages.append({
                                "version": CANDIDATE_CARVE_OUT_VERSION,
                                "batch_id": bid, "label": target_batch.label(),
                                "aspect_ids": list(target_batch.aspect_ids), "round": attempt,
                                "call_id": result.call_id,
                                "response_hash": result.response_hash,
                                # 逐条被拒的候选：模型自己写的标签 + 原文 + 封闭原因词表里的
                                # 原因 + 逐字错误串。它们**没有**可派生的 `ClaimCandidate`
                                # 身份（从未成为结构化对象）——与整束 `schema_invalid` 的既有
                                # 口径一致，不伪造一个 id。
                                "excluded": [dict(item) for item in rejected_candidates],
                                "excluded_total": len(rejected_candidates),
                                "kept_total": len(salvaged_plan["claim_candidates"]),
                                "unit_total": len(salvaged_plan["narrative_draft_units"]),
                                "follow_up_total": len(salvaged_plan["follow_up_needs"]),
                                "model_calls_added": 0,
                            })
                            # 这一批上一次被拒的返回若排定过纠正，这一刻就是那次纠正的结局：
                            # 返回被接受（部分候选除外），因此是 `accepted`——「纠正有没有用」
                            # 由这三个结局自己说话，逐条被排除的候选另在本条目里如实列出。
                            _resolve_correction(bid, call_id=result.call_id,
                                                response_hash=result.response_hash,
                                                outcome="accepted")
                            batch_plans.append((target_batch, salvaged_plan))
                            continue
                        # C4：**这一批自己的返回**形状不合法。先把它自己这一条留痕（含
                        # call_id / response_hash / 错误原文），再决定「还能不能对**这一批**
                        # 纠正一次」。整轮重跑不在这条路上：它会把其余批次已经拿到的合法返回
                        # 全部丢掉，而它们与这次失败毫无关系。
                        corrected_before = shape_corrected.get(bid, 0)
                        slack_left = (shrink_used + len(shape_corrections)
                                      < MAX_SWEEP_SHRINK_STEPS)
                        # 栏目定向那一次（`focus_mode`）**不**走形状纠正：它是**已获准**的
                        # 额外调用，纪律是「恰好一次、失败即整节 fail-closed」。给它再补一次
                        # 重问，等于把「栏目定向恰一次」这条纪律变成不可核验的，也让
                        # 「格式抖动」与「栏目真的没材料」在那一轮里再次混成一类。
                        can_correct = (corrected_before
                                       < MAX_BATCH_SHAPE_CORRECTIONS_PER_BATCH
                                       and slack_left and not is_focus_round)
                        entry: dict = {
                            "batch_id": bid, "label": target_batch.label(),
                            "aspect_ids": list(target_batch.aspect_ids), "round": attempt,
                            "rejection_kind": BATCH_SHAPE_CORRECTION_TRIGGER_KIND,
                            "detail": str(exc),
                            "rejected_call_id": result.call_id,
                            "rejected_response_hash": result.response_hash,
                            "rejected_output_tokens": result.output_tokens,
                            "note_version": (BATCH_SHAPE_CORRECTION_NOTE_VERSION
                                             if can_correct else None),
                            "correction": None,
                        }
                        shape_corrections.append(entry)
                        if not can_correct:
                            batch_shape_stop = True
                            if is_focus_round:
                                why = ("本轮是**栏目定向**那一次（恰好一次的独立调用）："
                                       "它失败即整节 fail-closed，不借任何通道重发")
                            elif corrected_before >= MAX_BATCH_SHAPE_CORRECTIONS_PER_BATCH:
                                why = (f"本批已按 {BATCH_SHAPE_CORRECTION_NOTE_VERSION} "
                                       "纠正过一次（同一批不再纠正第二次：再次失败即停）")
                            else:
                                why = (f"本轮的额外调用额度（{MAX_SWEEP_SHRINK_STEPS} 次，"
                                       "与截断缩小共用）已用尽")
                            # 原因码在这里写成**字面量**（不经过 `BATCH_SHAPE_CORRECTION_
                            # TRIGGER_KIND`）：静态审计按 `_reject("<kind>")` 的字面量核对
                            # 「已登记 ⟺ 真有出口在用」，写成变量会让这条**活着的**出口在审计
                            # 里消失（读成死码）。那个常量只用于「这条路处理的是哪一类」的
                            # 声明与核对（两者相等由测试逐字断言），不参与这里的字面量。
                            _reject(
                                "schema_invalid",
                                f"批次 {target_batch.label()}"
                                f"（aspects={list(target_batch.aspect_ids)}）：{exc}；{why}，"
                                f"该批覆盖的 aspect 因此没有任何候选进入合并结果"
                                f"（不得原地删候选当成功，也不得整轮重跑：其余批次已经拿到的"
                                f"合法返回没有被重问，逐批读数见本轮 `batch_calls`）",
                                source=None, text=result.text, result=result,
                                batches=round_records, retained_batches=batch_plans)
                            raise
                        # 这一批的返回是**被丢弃**（不是被拒），但它提过的诉求仍然是**真实提出
                        # 过**的诉求：`_reject` 那条路上会顺手取下来，这条路上不取就会凭空消失。
                        # 原样取出、逐条留痕；取不出才是真的没有（不得凭它反推一条需求）。
                        discarded_specs = tuple(
                            _jsonable(dict(s))
                            for s in _best_effort_follow_up_specs(result.text))
                        if discarded_specs:
                            rejected_follow_up_specs.append((attempt, discarded_specs))
                        shape_corrected[bid] = corrected_before + 1
                        pending_correction[bid] = entry
                        batch_notes[bid] = _batch_shape_correction_note(
                            error=str(exc), batch_label=target_batch.label(), batch_id=bid,
                            aspect_ids=target_batch.aspect_ids,
                            declared_fact_refs=len(aliases.facts),
                            declared_material_refs=len(aliases.materials))
                        # 只把**这一批**放回队首重问：其余批次的请求一个字都不改，它们已经
                        # 拿到的返回也不重问。合并因此仍按**同一份** aspect 划分进行。
                        queue.insert(0, target_batch)
                        continue
                    # 结构校验通过：这一批上一次被拒的返回若排定过纠正，这一刻就是那次纠正的
                    # 结局（`accepted`）。回填之后再入 `batch_plans`，两者不会错位。
                    _resolve_correction(bid, call_id=result.call_id,
                                        response_hash=result.response_hash,
                                        outcome="accepted")
                    batch_plans.append((target_batch, batch_plan))
                # 全部批次成功 ⇒ 合并成**一份**完整有序提案集（逐批身份与合并结果记入审计）。
                if is_carve_out_round:
                    # §一.2：裁出轮的提案集**逐字**取自上一轮裁出的结果，不经任何合并。
                    plan = pending_carve_out["plan"]
                    merge_audit = {"carve_out": pending_carve_out["decision"].to_dict()}
                else:
                    plan, merge_audit = _merge_batch_plans(
                        batch_plans=batch_plans, expected_aspects=selected_aspects,
                        table=_authority_fact_table(scan.facts))
                if policy.requires_natural_draft():
                    # `pw-16`：**跨批合并**是这条判据的第三处入口（另两处在
                    # `parse_writer_proposals` 与 `_salvage_batch_plan`）。逐批都过了这一关时
                    # 它不会开火——它开火只在一种情形：某一批的草稿层在合并里被整段丢掉而候选
                    # 留下（那正是「空草稿 + 候选拼文」这条旁路在合并层的形状）。与那两处共用
                    # 同一个函数，因此三处的判据不可能各说各话。
                    try:
                        assert_natural_draft_covers_candidates(plan, source="合并后的提案集")
                    except PackWriterError as exc:
                        # 同样不给它留白：这一轮结构上已成形的提案集若被这一关拒掉，产物里
                        # 必须有一条 typed 原因，否则它会是唯一一条「被拒了却只有一行错误串」
                        # 的路径（与 `proposal_identity_unresolvable` 的那条同形）。
                        detail = (f"合并后的提案集形状不合法：{exc}；这一轮不得被采信，"
                                  "也不得退回按候选拼文——那是另一条成稿路径，会让读者面的"
                                  "段落与门审的原子失去对应关系")
                        _reject("schema_invalid", detail, source=None,
                                text=json.dumps(plan, ensure_ascii=False), result=None,
                                batches=round_records, retained_batches=batch_plans)
                        raise PackWriterError(detail) from exc
                merge_audit["round"] = attempt
                merge_audit["focus"] = is_focus_round
                merge_audit["directed_reproposal"] = is_directed_round
                merge_audit["shrink_steps_used"] = shrink_used
                merge_audit["calls"] = round_calls
                merge_audit["batch_calls"] = [r.to_dict() for r in round_records]
                merged = True
                if is_carve_out_round and attempt != pending_carve_out["decision"].to_attempt:
                    # 裁出轮的修订身份必须**正好**是裁决那一刻算出的那一份：否则下面回填的
                    # 「原身份 → 新身份」对账指的是另一份修订，读起来像是追溯过的，实际没有。
                    raise PackWriterError(
                        f"裁出轮的实际轮次（{attempt}）与裁决声明的目标轮次"
                        f"（{pending_carve_out['decision'].to_attempt}）不一致："
                        "跨修订对账不得指向另一份修订")
                try:
                    bundle = _build_pre_gate_bundle(
                        plan=plan, task=task, authority=authority, scan=scan, policy=policy,
                        # 本轮修订：由**这一轮合并后的提案集**算出（含它的草稿层）。这里也是
                        # 全链上唯一一次为该轮定修订——门前束里的候选、草稿单元、proposal 与
                        # `SectionDraft` 全部取自这一份，因此「同一轮的产物共享一个修订」是
                        # 构造上的事实，不是一句纪律。
                        draft_revision=_revision_for(
                            attempt, prose_digest=_prose_specs_digest(plan["natural_prose_draft"])),
                        manifest=manifest,
                        dependency_fingerprint=dependency_fingerprint)
                except PackWriterError as exc:
                    # §二 3：这一束的结构合法、但声明的身份在权威侧闭不上——它同样是**整束被拒**。
                    # 不给它留审计，它就会成为唯一一条「被拒了却没有 typed 原因」的路径
                    # （产物里只剩最终那行错误字符串，于是「20 个候选里哪些被拒、为什么」无从回查）。
                    # 此时**没有**可信的 bundle 身份，只能走尽力抽取（`source=None`）；合并后的
                    # 提案集已是一份结构化对象，因此身份抽取改从**合并结果**取（它逐字来自各批）。
                    detail = ("候选提案的支撑边身份在权威侧闭不上："
                              f"{exc}；这一束不得被裁剪后重试，也不得降级放行")
                    _reject("proposal_identity_unresolvable", detail, source=None,
                            text=json.dumps(plan, ensure_ascii=False), result=None,
                            batches=round_records, retained_batches=batch_plans)
                    raise PackWriterError(detail) from exc
                # §一.2：裁出轮真的产出了一份结构化提案集 ⇒ 现在可以把「原束每条候选 → 被排除
                # 原因 / 新修订身份」**逐条落实**，并把这份对账写回**那一条**拒绝记录（记录本身
                # 不被改写：只多一个 `carve_out` 字段，`whole_set_rejected=True` 原样保留）。
                # 位置与 `reproposal` 同理在**门之前**：这一轮后面还会被高风险面/硬门/栏目覆盖
                # 各判一次，那些判定说的是「这一束能不能被采信」，与「裁出后各条候选去哪了」
                # 是两件事。
                if is_carve_out_round:
                    index = pending_carve_out["record_index"]
                    rejections[index] = replace(
                        rejections[index],
                        carve_out=_carve_out_trace(
                            decision=pending_carve_out["decision"],
                            previous_bundle=pending_carve_out["bundle"], bundle=bundle))
                    pending_carve_out = None
                # §二 3 / C3：被排定的定向重提案轮**真的**产出了一份结构化提案集 ⇒ 现在可以
                # 逐条回答「原束的每条候选在**下一修订**里还在不在」，并把这份跨修订去向写回
                # **那一条**拒绝记录（记录本身不被改写：只多一个 `reproposal` 字段）。
                # 位置刻意在**门之前**：这一轮后面还会被高风险面/硬门/栏目覆盖各判一次，那些
                # 判定说的是「这一束能不能被采信」，与「这一轮到底产出过哪些候选」是两件事。
                if pending_directed is not None:
                    if attempt == pending_directed["attempt"] + 1:
                        index = pending_directed["record_index"]
                        rejections[index] = replace(
                            rejections[index],
                            reproposal=_rejection_reproposal_trace(
                                previous=pending_directed["bundle"],
                                previous_attempt=pending_directed["attempt"],
                                next_bundle=bundle, next_attempt=attempt,
                                note_version=pending_directed["note_version"]))
                        pending_directed = None
                    elif attempt > pending_directed["attempt"] + 1:
                        # 被排定的那一轮没能产出结构化提案集（合并失败 / 截断 / 身份闭不上）：
                        # 没有下一修订可比。**不**留一条空 trace 冒充「逐条都追过了」——它的
                        # 去向由那一轮自己的拒绝记录（或整节 fail-closed）如实交代。
                        pending_directed = None
                # §二 / P0：路径 B 的授权面判据（数字/期间/否定/勾选/主体身份/表格关系/因果）。
                # 它在**组装 Draft 之前**跑，因此不合格的候选连一份 Draft 都形不成——不会被
                # 门后某一步「顺手放过」，也不会留下一份带高风险 B 面的 Draft 身份。
                # 写入侧这道与机械门那道（`cbg-2` 的 `path_b_high_risk_surface`）是**同一
                # 语义、两处独立执行**：绕过 Writer 手工构造的 Draft 由门拒绝，模型自己产出的
                # 候选在这里就被打回重写。
                high_risk = _path_b_high_risk_surfaces(bundle=bundle)
                # §二 2.3：路径 B 的**支撑资格**判据。它和高风险面是两条正交的轴：那一条问
                # 「候选自己带了什么表面」，这一条问「这条边本来能不能承重」。两者都在组装
                # Draft 之前跑，不合格的候选同样连一份 Draft 都形不成。
                ineligible = _path_b_ineligible_scope(bundle=bundle, scope=selection_scope)
                # §O-12（`srsc-1`）：路径 B 的**期间/来源角色**判据。它是第三条正交的轴：高风险面
                # 问「候选自己带了什么表面」、支撑资格问「这条边本来能不能承重」，这一条问
                # 「这条边最多能证明到哪个期间」——同类较旧（或期间不可核实）的来源不能支撑一条
                # 没写期间的当前态断言。
                #
                # 两道前置条件，都是**这条轴自己不存在**而不是「读不到角色」：
                #   * `documents` 为空（本节材料清单里没有带来源文档定位的成员）⇒ 没有来源文档
                #     系列可查；
                #   * 权威不是 topic Pack（财务/附注/外部快照没有来源集）⇒ 这条轴上没有来源角色，
                #     它们的证据材料**不**带角色，硬套会把整批候选按「读不到角色」误杀。
                # 真进了这条轴却读不到角色（缺陷）时由 `SRS.document_roles` /
                # `SRS.unqualified_current_state` 当场抛——那才是 fail-closed 该生效的地方。
                #
                # §O-12（`srsc-2`）：第四条轴——**独立支撑结论**。上一条判据只在「一条当前锚边
                # 都没有」时开火，于是「**有一条**边落在当前锚上」曾被当成「较新材料已核实了这条
                # 命题」。这一条补上那一步：锚边说「这条路可能走得通」，本判据说「这条路**真的**
                # 走通了吗」。锚边集合是 `current_state_source`（不是上一条的补集，理由见模块头
                # 边界 5）；两条判据**互斥但不互补**，见各自 docstring。
                documents = _support_documents(manifest=manifest)
                history_only = {}
                unproven_current_state = {}
                if documents and SRS.has_source_document_series(authority):
                    roles = SRS.document_roles(authority)
                    history_only = _path_b_history_only_current_state(
                        bundle=bundle, documents=documents, roles=roles)
                    unproven_current_state = _path_b_unproven_current_state(
                        bundle=bundle, documents=documents, roles=roles,
                        axes=SRS.document_axis_index(authority), manifest=manifest,
                        material_context=material_context)
                    overlap = sorted(set(history_only) & set(unproven_current_state))
                    if overlap:
                        raise PackWriterError(
                            f"`srsc-1` 与 `srsc-2` 同时点名了候选 {overlap}：两条判据按构造互斥"
                            "（一条要求「一条当前锚边都没有」，另一条要求「至少有一条」），"
                            "同时命中说明其中一条的取数面出了问题，不得按任一侧继续")
                if high_risk or ineligible or history_only or unproven_current_state:
                    detail = _path_b_rejection_detail(
                        high_risk=high_risk, ineligible=ineligible,
                        history_only=history_only,
                        unproven_current_state=unproven_current_state)
                    # §一.2 补充裁决：**先试逐候选裁出**（确定性、零调用、不改一个字的候选
                    # 文本）。整束拒绝在这里是**过度**的：不合格的是被点名的那几条，被连带
                    # 作废的却是同一次模型输出里其余完全合法的候选。裁出把这件过度的事修掉。
                    #
                    # 三条边界，缺一条就退回下面那条既有路径（一个字不改）：
                    #   * 一条都没被点名、或一条都没幸存 ⇒ `carve_out_candidate_subset` 返回
                    #     `None`。后者与整束拒绝是同一件事，此时再走裁出只会把整束语义丢掉。
                    #   * 本轮**自己**就是**按候选轴**裁出的轮 ⇒ 不得再裁。裁出后的束是原束
                    #     **删掉**被点名项的子集，候选轴上同一条判据不可能再点名一个新候选
                    #     （该轴上的判据是同一束上的同一个确定性函数，判一次就判全了）；
                    #     真出现了就说明这条链在换候选，那不是裁出该处理的事。
                    #   * 已经排定了定向重提案（`pending_directed`）⇒ 不得裁出。那一条原束
                    #     记录**欠着**一份 `reproposal` 跨修订对账，用裁出回答它会让那份对账
                    #     永远空缺——两条出口互斥，见 `ProposalSetRejectionRecord`。
                    carved = (None if (carve_out_axis == "candidates"
                                       or pending_directed is not None)
                              else carve_out_candidate_subset(
                                  plan=plan, bundle=bundle, high_risk=high_risk,
                                  ineligible=ineligible, history_only=history_only,
                                  unproven_current_state=unproven_current_state,
                                  from_attempt=attempt,
                                  to_attempt=attempt + 1,
                                  # 新修订只能由**裁出后**的提案集算：裁出会去掉草稿单元的原子、
                                  # 甚至整段撤下草稿单元，而草稿层进了修订。传一个先算好的修订
                                  # 会得到「裁决声明的 to_revision 与那一轮实际 draft_revision
                                  # 不是同一份」——两者相等正是跨修订对账成立的前提。
                                  revision_for_plan=lambda carved_plan: _revision_for(
                                      attempt + 1,
                                      prose_digest=_prose_specs_digest(
                                          carved_plan["natural_prose_draft"]))))
                    # 整束留档的**唯一**落点：一束被拒的提案集，一轮里**恰好**一条 typed 记录
                    # （完整有序身份 + 逐候选 typed 原因 + 代表调用哈希，一字不改；
                    # `whole_set_rejected=True` 保留）。两条出口各自在自己那一支里写下它，
                    # 因此「这一束为什么被拒」永远只有一份可读的答案——同一次拒绝留下两条
                    # 内容相同、只差一个字段的记录，会让报告里的拒绝束数与逐候选审计条目
                    # **成倍虚增**，也让 `record_index` 指向两条中的哪一条变得不可知。
                    # 原因码写成**字面量**（不经过变量）：静态审计按 `_reject("<kind>")` 的字面量
                    # 核对「已登记 ⟺ 真有出口在用」，写成变量会让这条**活着的**出口在审计里消失。
                    if carved is not None:
                        if high_risk:
                            _reject("path_b_high_risk_surface", detail, source=bundle, text="",
                                    result=None, high_risk=high_risk, ineligible=ineligible,
                                    history_only=history_only,
                                    unproven_current_state=unproven_current_state,
                                    batches=round_records)
                        elif history_only:
                            _reject("path_b_history_only_current_state", detail, source=bundle,
                                    text="", result=None, high_risk=high_risk,
                                    ineligible=ineligible, history_only=history_only,
                                    unproven_current_state=unproven_current_state,
                                    batches=round_records)
                        elif unproven_current_state:
                            _reject("path_b_unproven_current_state", detail, source=bundle,
                                    text="", result=None, high_risk=high_risk,
                                    ineligible=ineligible, history_only=history_only,
                                    unproven_current_state=unproven_current_state,
                                    batches=round_records)
                        else:
                            _reject("path_b_ineligible_material_scope", detail, source=bundle,
                                    text="", result=None, high_risk=high_risk,
                                    ineligible=ineligible, history_only=history_only,
                                    unproven_current_state=unproven_current_state,
                                    batches=round_records)
                        carved_plan, carve_decision = carved
                        pending_carve_out = {"plan": carved_plan, "decision": carve_decision,
                                             "bundle": bundle,
                                             "record_index": len(rejections) - 1}
                        # 与定向重提案同理：这一轮的产物不再可信，不清空就会在下一轮中断时
                        # 把一束**被拒**的束当成被采信的结果返回。
                        bundle = None
                        draft = None
                        gate_result = None
                        continue
                    # 走到这里说明裁出这条路不适用（无可裁 / 本轮自己就是裁出轮 / 已有待回答的
                    # 定向轮）：下面是**既有**的 C3 路径，一个字不改——包括它自己的那一次整束
                    # 留档（上面那条纪律：一束一轮恰好一条记录）。
                    #
                    # §二 3 / C3：**有界定向重提案**。被拒的整束身份与逐候选原因原样留档
                    # （`_reject` 的那一条记录**不因重提案而改变**），随后把「哪几条候选、
                    # 它们逐字携带的表面、有哪些合法出路」写成一段**定向说明**追加在完整原请求
                    # 之后，让**下一轮**（新修订、自己完整的候选集与支撑边集、重新过全部门）
                    # 真正有条件改对——而不是只收到一句「被拒了，请重出」。
                    #
                    # 预算中性（可逐步核对）：定向轮**不新增额度**，它用的就是本策略已有的那一轮
                    # 重试。因此只在「本轮之后确实还有一轮可跑」（`attempt <= max_llm_retries`，
                    # 与 `while` 顶部的准入条件**同一个判据**）且定向额度未用尽时才排定。两者
                    # 任一不满足即整节 typed fail-closed，并**不再**借通用重试通道重问同一件事。
                    #
                    # 两种触发**共用同一份额度**（`MAX_DIRECTED_REPROPOSAL_PASSES`，值仍为 1）：
                    # 它们给的是同一样东西——「原样的完整原请求 + 一段定向说明」的那一轮重试。
                    # 各记一份额度就等于把这一轮重试按原因复制成两轮，那是加预算换产出，不是
                    # 把话说清楚；因此这里额度不因触发原因而增加，只按**已排定过几次**计。
                    budget_left = attempt <= policy.max_llm_retries
                    quota_left = (directed_reproposal_passes
                                  < MAX_DIRECTED_REPROPOSAL_PASSES)
                    will_repropose = bool(budget_left and quota_left)
                    if will_repropose:
                        directed_reproposal_passes += 1
                    else:
                        directed_quota_exhausted = True
                    # 整束的 headline kind 取**更强**的那一条，判据是「谁更直接地说明这一束为什么
                    # 不能被采信」：候选自己携带未经授权的高风险表面最直接；其次是**整条断言**的
                    # 期间资格——这里分两级，「一条当前锚边都没有」（同类较旧来源支撑一条无期间的
                    # 当前态断言）比「有当前锚边但证不出来」更硬，因此 `history_only` 在
                    # `unproven_current_state` 之前；再次是**某一条边**超出它自己所问事项（断言本身
                    # 可能成立，只是这条边不该给它承重）。四组逐候选明细都在 `detail` 与逐候选审计
                    # 里，不因 headline 取哪一条而丢失。裁出那一次留档用的是同一条判据（同一串
                    # `if/elif`），因此两条出口的 headline 逐字一致，读的人不必分辨「这条记录是谁写的」。
                    #
                    # 四条出口各自把原因码写成**字面量**（不经过变量）：静态审计按
                    # `_reject("<kind>")` 的字面量核对「已登记 ⟺ 真有出口在用」，写成变量会让
                    # 审计看不见这个出口——那会把一条**活着的**出口读成死码（反向也一样：
                    # 让字面量消失就等于把这条审计变成摆设）。共用参数抽到 dict 里，出口处只留
                    # 「哪一个 kind」这一个差异。
                    reject_kwargs = dict(
                        source=bundle, text="", result=None, high_risk=high_risk,
                        ineligible=ineligible, history_only=history_only,
                        unproven_current_state=unproven_current_state,
                        batches=round_records,
                        answered_by_attempt=(attempt + 1) if will_repropose else None)
                    if high_risk:
                        _reject("path_b_high_risk_surface", detail, **reject_kwargs)
                    elif history_only:
                        _reject("path_b_history_only_current_state", detail, **reject_kwargs)
                    elif unproven_current_state:
                        _reject("path_b_unproven_current_state", detail, **reject_kwargs)
                    else:
                        _reject("path_b_ineligible_material_scope", detail, **reject_kwargs)
                    if not will_repropose:
                        # 两笔额度各自**如实**报出：只报其中一笔会让读的人以为另一笔还有余量。
                        why: list[str] = []
                        if not budget_left:
                            why.append("本策略的写作重试额度已用尽（定向重提案用的就是那一轮"
                                       "重试，不额外增发调用）")
                        if not quota_left:
                            why.append(f"定向重提案额度"
                                       f"（{MAX_DIRECTED_REPROPOSAL_PASSES} 次）已用尽")
                        raise PackWriterError(
                            detail + "；" + "；".join(why)
                            + "，无法排定定向重提案，整节 fail-closed"
                              "（被拒整束的完整有序身份与逐候选原因已按原样记入 typed 审计；"
                              "本条拒绝记录带出的补件诉求仍是**待裁决提议**，不是已执行的补件）")
                    pending_directed = {"attempt": attempt, "bundle": bundle,
                                        "record_index": len(rejections) - 1,
                                        "note_version": HIGH_RISK_REPROPOSAL_NOTE_VERSION}
                    _add_note(_directed_reproposal_note(
                        bundle=bundle, detail=detail, surfaces=high_risk,
                        ineligible=ineligible, history_only=history_only,
                        unproven_current_state=unproven_current_state))
                    directed_note_added = True
                    # 三个都是**本轮**的产物：不清空，下一轮若因任何原因中断，这里就会把一束
                    # **被拒**的束当成被采信的结果返回（`bundle is None` 那条 fail-closed 分支
                    # 正是靠它们为 None 才成立）。裁出那条出口同理，见上。
                    bundle = None
                    draft = None
                    gate_result = None
                    continue
                try:
                    draft, gate_result = _assemble(bundle, attempt)
                except NS.NarrativeSchemaError as exc:
                    # 草稿闭合核对（`NS.validate_natural_prose_mapping`，`npr-1`）抛的是
                    # `NarrativeSchemaError`——它**不是** `PackWriterError`，因此在本函数下面那个
                    # `except PackWriterError` 眼里根本不算失败：异常直接穿出整条链。r9 现场就是
                    # 这样：这一束既没有 typed 拒绝记录，**模型已提出的 20 条补件诉求也随异常一起
                    # 蒸发**（产物里只剩一个计数），而那一节此前已经付过真实的生成调用。
                    #
                    # 闭合失败与「高风险面 / 身份闭不上 / 硬门不过」是同一件事——这一束候选/草稿/
                    # 支撑边拼不成一份可采信的 `SectionDraft`。因此它走**同一条**通道：typed 拒绝
                    # （含这一束的完整有序身份与逐候选归属）+ 有界重试 + 额度用尽时随
                    # `ProposalSetRejectedError` 带出待裁决的补件诉求。**不放宽任何判据**：
                    # 失败照旧 fail-closed，只是不再「静默地 fail-closed」。
                    #
                    # 为何不在这里直接抛 `ProposalSetRejectedError`：那会让这一轮跳过本策略已有的
                    # 重试额度（模型完全可能写对，例如把一段草稿的出处指到它自己的支撑材料上），
                    # 也会让「第几次尝试」与产物里的轮次读数错位。
                    detail = ("草稿闭合核对未通过（候选 / 草稿 / 支撑提案三者拼不成一份可采信的 "
                              f"SectionDraft）：{exc}；这一束不得裁剪后重试，也不得降级放行"
                              "（被拒整束的完整有序身份与模型提出的补件诉求已按原样记入 typed 审计）")
                    _reject("natural_draft_not_closed", detail, source=bundle, text="",
                            result=None, batches=round_records)
                    raise PackWriterError(detail) from exc
                if gate_result.blocking:
                    detail = ("narrative 硬门未通过，fail-closed（不产出章节）："
                              + _gate_failure(gate_result.issues_of("blocking")))
                    # §一.2 / `cco-6`：**先试逐项裁出**，但这一支只对一种很窄的形状开火——
                    # 全部 blocking 问题都是「`narrative_vague_period` 点名了一段 context
                    # 衔接文字」（判据与边界见 `_carvable_context_units`）。三条边界与候选那一支
                    # 同源，缺一条就退回下面那条既有路径（一个字不改）：
                    #   * 本轮**自己**就是**按 context 单元轴**裁出的轮 ⇒ 不得再裁（新束是原束
                    #     删掉被点名单元的子集，同一条判据不可能再点名一个新单元——这条判据逐单元
                    #     读文本，判一次就把这一束里所有命中的单元都点名了；真出现了说明这条链在
                    #     换对象）。注意守卫是**逐轴**的：候选轴裁出的那一轮里，narrative 门是
                    #     **第一次**看到这一束的 context 单元，它对单元的读数与上一条轴无关
                    #     （r9 现场正是「候选越权先被裁出、紧接着单元越权被门发现」这条序列）。
                    #   * 已排定定向重提案（`pending_directed`）⇒ 不得裁出（那一条原束记录欠着
                    #     一份 `reproposal` 跨修订对账，两条出口互斥）。
                    # `_carvable_context_units` 返回 `None`（有一条不可裁，或撤下之后幸存数为 0）
                    # ⇒ 同样不裁。
                    blocking_issues = gate_result.issues_of("blocking")
                    carve_units = (None if (carve_out_axis == "context_units"
                                            or pending_directed is not None)
                                   else _carvable_context_units(bundle=bundle,
                                                                issues=blocking_issues))
                    carved = (None if carve_units is None else
                              carve_out_candidate_subset(
                                  plan=plan, bundle=bundle, high_risk={}, ineligible={},
                                  blocking_context_units=carve_units, manifest=manifest,
                                  from_attempt=attempt, to_attempt=attempt + 1,
                                  revision_for_plan=lambda carved_plan: _revision_for(
                                      attempt + 1,
                                      prose_digest=_prose_specs_digest(
                                          carved_plan["natural_prose_draft"]))))
                    # 整束留档的**唯一**落点（与候选那一支同一条纪律：一束一轮**恰好**一条
                    # typed 记录）。裁出这一支写的还是这条记录、还是这个 kind——这一束作为一束
                    # 确实被拒了；被裁掉的是「连带作废其余合格内容」这个**过度**的后果，
                    # 它发生在拒绝**之后**，由同一份记录上的 `carve_out` 逐条交代。
                    _reject("narrative_gate_blocking", detail, source=bundle,
                            text="", result=None, batches=round_records,
                            gate_issues=blocking_issues)
                    if carved is not None:
                        carved_plan, carve_decision = carved
                        pending_carve_out = {"plan": carved_plan, "decision": carve_decision,
                                             "bundle": bundle,
                                             "record_index": len(rejections) - 1}
                        # 与候选那一支同理：这一轮的产物不再可信，不清空就会在下一轮中断时
                        # 把一束**被拒**的束当成被采信的结果返回。
                        bundle = None
                        draft = None
                        gate_result = None
                        continue
                    raise PackWriterError(detail)
                rework_issues = gate_result.issues_of("rework")
                if (rework_issues and not rework_retried and not focus_mode
                        and attempt <= policy.max_llm_retries):
                    rework_retried = True
                    detail = "narrative 硬门要求返修：" + _gate_failure(rework_issues)
                    _reject("narrative_gate_blocking", detail, source=bundle,
                            text="", result=None, batches=round_records,
                            gate_issues=rework_issues)
                    raise PackWriterError(detail)
                # §三 A / 3.3：这一束过了硬门，但它可能让某个 Contract 栏目一个候选都没有。
                # 那不是「不可采信」，是「对被点名的栏目零产出」——因此**先**把哪些栏目、它们的
                # 逐字要求摆出来，再要一次**栏目定向**的完整提案集。第二次的输入 = 原请求逐字
                # 保留 + 栏目定向说明（不裁剪输入面、不换 prompt/模型），因此它看到的材料与
                # 第一次完全相同，区别只在「被明确告知漏了哪个栏目、那个栏目要什么」。
                #
                # 「未覆盖」与「值得再问一次」是两件事：本节事实目录里该栏目一条事实都没有时，
                # 第二次调用在原理上补不上（路径 A 只能绑事实目录里的行），再问就是纯循环——
                # 那种栏目留给 Contract 必需事实那条规则去形成缺口，不由这里反复追问。
                uncovered = tuple(
                    sid for sid in _focusable_subsections(
                        projection, selected_aspects, scan,
                        _uncovered_subsections(projection, selected_aspects, bundle, scan))
                    if sid not in focus_requested)
                if (uncovered and focus_passes < MAX_SUBSECTION_FOCUS_PASSES):
                    focus_passes += 1
                    focus_requested.update(uncovered)
                    # 审计里要能读出「是哪个栏目、那个栏目由哪些 aspect 构成」——只留一个
                    # `co-h4` 之类的栏目 id，读的人还得回头翻 Contract 投影才对得上号。
                    detail = (
                        "这一束结构合法、身份也闭得上，但下列 Contract 栏目没有任何候选覆盖："
                        + "；".join(
                            f"{sid}（aspects="
                            f"{list(_aspects_of_subsection(projection, selected_aspects, sid))}）"
                            for sid in uncovered)
                        + "（按冻结栏目顺序）。已发出一次栏目定向的完整提案请求；"
                          "这一束本身零产出，故按整束未采信记入 typed 审计")
                    _reject("subsection_uncovered", detail, source=bundle,
                            text="", result=None, batches=round_records,
                            named_subsections=uncovered)
                    _add_note(_focus_content(projection, selected_aspects, scan, uncovered))
                    # 栏目定向这一次是**已获准**的额外调用：`focus_mode` 让循环再多走一轮
                    # （见 `while` 处的判断），但**不**重开重试额度——该轮失败时 `except` 直接
                    # `break`。因此每节每轮写作的生成调用上界可逐步推出：
                    # (max_llm_retries + 1) 轮提案 + 1 轮栏目定向，每轮都是**一次分批扫描**
                    # （批数由本批 aspect 数唯一决定，见 `aspect_batch_count`）。
                    focus_mode = True
                    bundle = None
                    draft = None
                    gate_result = None
                    continue
                # 返修额度已用尽而仍有 rework 级问题：本束**被采信**（rework 级不阻断），
                # 那些问题不在本表留痕——它们已随 `gate_result` 如实进入产物。此处不记
                # 「拒绝」，否则 `rejections` 就不再等于「产出过零份 Draft 的那些尝试」。
                break
            except PackWriterError as exc:
                last_error = exc
                bundle = None
                draft = None
                gate_result = None
                # §一.2：裁出轮**失败**了（那一轮的提案集没能走到可采信的束）⇒ 不再重试它。
                # 留着它会让下一轮拿同一份 `plan` 再算一遍同一个修订、把同一条失败重复成若干条
                # 记录；而「那一轮裁出失败」这件事已经由本轮的分批审计（`carve_out: True`、
                # `calls: 0`、`merged: False`）如实留痕。原束那条拒绝记录的 `carve_out` 因此
                # **保持缺席**——与 `reproposal` 同一条纪律：没有跨修订事实就不写一条空的。
                pending_carve_out = None
                # 栏目定向那一次不参与重试：失败即整节 fail-closed（`focus_mode` 见上）。
                # 定向重提案同理（C3）：那一轮的额度已经用掉，用尽即停——不再借
                # 通用重试通道把同一件事重问一遍（`directed_quota_exhausted` 见上）。
                # 批次形状纠正同理（C4，`batch_shape_stop` 见上）：那一批自己的错误已经在
                # **批内**纠正过一次（说明只发给那一批），再次失败即停——通用通道会给出一句
                # 不含批次坐标的说明并**重跑整条扫描**，那正是「一次格式抖动换掉所有批次的
                # 合法返回」的形态，也是被裁决点名要改掉的那一步。
                if (focus_mode or directed_quota_exhausted or batch_shape_stop
                        or attempt > policy.max_llm_retries):
                    break
                # 返修说明追加在**完整原请求之后**（与栏目定向同一条通道）：下一轮的每一批都
                # 看到它，因此「被拒的原因」在轮内是同一句话，不因批次不同而不同。
                if directed_note_added:
                    # C3：这一轮已经收到**定向**说明（哪几条候选、踩了什么线、两条合法出路），
                    # 不能再叠一句通用说明：两句一起发，空泛的那句会把定向的重点稀释掉。
                    directed_note_added = False
                else:
                    _add_note(f"\n\n上一次输出被拒：{exc}。请修正后重新输出完整 JSON。")
            finally:
                # 每一轮**恰好一条**分批审计（被采信的那一轮、被拒的各轮、中途失败的各轮都在内）。
                # 「调用与重试逐次入账」在分批链上就是这一条：一轮 = 一次分批扫描，逐轮记录必须
                # 覆盖全部调用。**失败的一轮也留在这里**——被拒各轮的数随拒绝记录的 `batches`
                # 走的是同一批读数的副本（拒绝审计要自成一体），不得在这里再计一遍。
                batch_audit_rounds.append({
                    "round": attempt, "focus": is_focus_round, "merged": merged,
                    "directed_reproposal": is_directed_round,
                    # §一.2：这一轮是不是**零调用的裁出轮**。它与同一行里的 `calls` 成对读：
                    # 裁出轮 `calls` 恒为 0——「逐候选裁出不新增任何模型调用」是逐轮可核的事实，
                    # 不是一句声明。
                    "carve_out": is_carve_out_round,
                    "shrink_steps_used": shrink_used,
                    # C4：本轮的**批次形状纠正**逐条留痕 + 那份额外额度的合计用量。
                    # `slack_steps_used` 是「本轮超出基础分批扫描的调用数」，它的上界就是
                    # `MAX_SWEEP_SHRINK_STEPS`——读的人据此不必相信任何一句话就能自己核预算。
                    "shape_corrections": [dict(e) for e in shape_corrections],
                    # §一.2：本轮的**逐候选补救**逐条留痕（有序）。它**不**计入
                    # `slack_steps_used`——补救不发调用，因此不占用那一份额度；谁想把
                    # 「本轮的发出去的调用数」算出来，用 `calls` 就够了。
                    "batch_salvages": [dict(e) for e in batch_salvages],
                    "slack_steps_used": shrink_used + len(shape_corrections),
                    "calls": round_calls,
                    "batch_calls": [r.to_dict() for r in round_records],
                    **merge_audit})
        if bundle is None or draft is None or gate_result is None:
            # 被拒的轮次里模型提出的补件诉求**先成形再抛**：候选提案集被拒 ≠ 没有诉求。
            # 顺序反了，这些诉求就随异常一起蒸发（产物里只剩一个 `follow_up_count` 计数）。
            pending_needs, untypeable = _pending_follow_up_needs(
                captured=tuple(rejected_follow_up_specs), task=task, authority=authority,
                scan=scan, revision_for=_revision_for, policy=policy)
            raise ProposalSetRejectedError(
                f"候选提案连续被拒，fail-closed：{last_error}；"
                f"本次共 {len(rejections)} 束被拒，逐束 typed 审计随本异常以 "
                f"`rejections` 字段带出"
                f"（每束含完整有序候选集与指向 logs/llm 的 narration_call_id）；"
                f"被拒各轮的补件诉求（{len(pending_needs)} 条可类型化 / "
                f"{len(untypeable)} 条不成立）另以 `follow_up_needs` / `follow_up_untypeable` "
                f"带出——它们是**待裁决提议**，不是已执行的补件，也不是缺口",
                rejections=tuple(rejections),
                follow_up_needs=pending_needs, follow_up_untypeable=untypeable)

    # 被采信的是第 `attempt` 次生成：成功路径的诉求属于**这一轮**的修订，与它自己所在
    # 那一束的候选/单元/支撑边同一身份（被拒各轮走 `_pending_follow_up_needs`，各自归属
    # 提出它的那一次生成）。`pw-15`：走 `_bundle_revision` 而不是 `_revision_for(attempt)`——
    # 这里的 `bundle` 就是**被采信的那一束**，它带草稿层，而上面 `_draft_fields` 已经按草稿层
    # 算出了 `SectionDraft.draft_revision`；两处取值不同，这条诉求就会指向一个不存在的修订。
    #
    # **逐条**判定：本节走到这里意味着 Draft 已经成形并通过确定性硬门——它不因为**某一条**补件
    # 申请填错了 topic/aspect 而消失（真实 run r5 的 financial 正是这样整节死掉的：门都过了，
    # 死在门后的一条申请上）。不成立的那条留成 typed 拒绝记录，成立的照常成为待裁决提议。
    follow_up_needs, follow_up_rejections = _follow_up_needs_partition(
        specs=bundle.follow_up_specs, task=task, authority=authority, scan=scan,
        draft_revision=_bundle_revision(bundle, attempt), policy=policy, attempt=attempt)

    # §五 6：trace 与调用计数必须一致 —— 没有调用就恰好为空，调用一次就恰好一条。
    if len(llm_trace) != llm_calls:
        raise PackWriterError(
            f"生成调用 trace 与调用计数不一致（{len(llm_trace)} != {llm_calls}）："
            "调用记录不得丢失，也不得凭空多条")
    # §二 3（`pw-10` 起按**轮**守恒）：被拒的轮数 + 最终被采信的那一轮 = 本节的生成轮数。少一轮就
    # 说明「有一次尝试既没被采信、也没留审计」——那正是「把候选原地删掉」的形态，必须 fail-closed。
    # 分层是必须的：一轮 = **一次分批扫描**（若干次调用），因此「轮数守恒」与「调用数守恒」是
    # 两笔账，混在一起后分批的每一批都会让这一条误报。
    if llm_calls and len(rejections) + 1 != attempt:
        raise PackWriterError(
            f"生成轮数 {attempt} 与「被拒轮数 {len(rejections)} + 被采信 1」不守恒："
            "每一次未被采信的生成都必须留下恰好一条 typed 拒绝审计")
    # 分批链上「每一次调用都入账」是**逐轮**成立的：每一轮恰好一条分批审计，逐轮调用数之和
    # 必须等于实际调用数。少了哪一轮，就说明那一轮的分批请求没有入账（一次生成 = 多次调用，
    # 这正是分批引入后最容易漏的一笔）。
    if llm_calls and len(batch_audit_rounds) != attempt:
        raise PackWriterError(
            f"分批审计的轮数 {len(batch_audit_rounds)} 与生成轮数 {attempt} 不一致："
            "每一轮都必须留下恰好一条分批审计（成功、被拒、中途失败都一样）")
    audited_calls = sum(int(audit.get("calls", 0)) for audit in batch_audit_rounds)
    if audited_calls != llm_calls:
        raise PackWriterError(
            f"逐轮入账的调用数合计 {audited_calls} 与实际调用数 {llm_calls} 不一致："
            "每一次分批请求都必须逐次入账")
    # 注意：Claim 绑定决定 / 蕴含决定 / accepted binding / 定稿 `SectionClaim` / 最终
    # Narrative / 逐 fact 的 `FactNarrativeDisposition` / `SectionResult` 全部是**门后**
    # （P8/P9/P10 + P15 coordinator）的产物。本模块到此为止：不自评、不代签、不产出章节结果。
    return PackWriteOutcome(
        draft=draft, follow_up_needs=follow_up_needs, unresolved=tuple(unresolved),
        gate_result=gate_result, prompt_version=policy.prompt_version,
        model_policy=policy.model_policy, llm_calls=llm_calls, llm_trace=tuple(llm_trace),
        rejections=tuple(rejections), follow_up_rejections=tuple(follow_up_rejections),
        # 分批审计：整轮的批次划分 + 合并结果。零调用的那一节（无事实无材料）为 `{}`：没有请求
        # 就没有批次，不编一份空审计出来。
        batch_audit=({"policy_version": ASPECT_BATCH_POLICY_VERSION,
                      "max_aspects_per_batch": MAX_ASPECTS_PER_BATCH,
                      "max_shrink_steps": MAX_SWEEP_SHRINK_STEPS,
                      "rounds": batch_audit_rounds} if batch_audit_rounds else {}),
        coverage_summary=coverage_summary, material_context=material_context)



def _source_run_ids(authority: Any) -> tuple[str, ...]:
    if isinstance(authority, TopicPackAuthorityInput):
        return tuple(sorted({str(getattr(p, "run_id", "") or "")
                             for p in authority.pack_set.packs} - {""}))
    if isinstance(authority, FinancialAuthorityInput):
        snapshot = getattr(authority.artifact, "snapshot", None)
        snapshot_id = str(getattr(snapshot, "snapshot_id", "") or "")
        return (snapshot_id,) if snapshot_id else ()
    return ()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

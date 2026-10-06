"""Eval: M930-3 定点批「先证明能成稿」——r7b 留存产物与原始模型返回的**离线同链重放**。

用法: python -X utf8 -m evals.test_m930_3_prewrite_offline_replay

## 本模块证明什么

用 r7b 那一轮**真实留存**的材料、manifest 与**原始模型返回**，在当前代码上走**同一条**写作主链
（公司节：`PW.write_section` → 机械门/语义门/AcceptedSupportBinding → `CW._finalize_section`；
财务节：`CW.run_backbone_writer_phase`），得到本批两条退出条件的**可复算**证据：

  1. 公司节一段**逐句可回查**的经营模式正文：原束 95 条候选 → `cco-5` 逐候选裁出 **83** 条
     （`path_b_high_risk_surface` 37 条 / `path_b_history_only_current_state` 14 条 /
     `path_b_unproven_current_state` 43 条 / `path_b_ineligible_material_scope` 3 条，
     其中 14 条带两个原因）
     → 新修订 12 条候选 → 14 个草稿单元 → 49 条支撑提案 →
     1 条整束拒绝（`path_b_high_risk_surface`）→ 新修订（`writer_attempt=2`）
     → 聚合绑定决定 26 / 12 个蕴含决定 / 49 条接受边 / 0 个被拒 subject → 12 条 Claim →
     2 段 final Narrative（全挂在 `company_business`，共 4 句）。该正文的每条 Claim 都能回查到
     `evidence:<id> p.N` 与带 `block_range` locator 的接受边，且跨**两份**文档、跨 p.14–p.16。
     其中打回最多的原因是 `srsc-2`（独立支撑结论）第一次真正跑在真实数据上：候选有当前锚边，
     但候选文本**不是**那条边所绑材料正文的严格子串——「支撑边里有一条较新材料」不等于
     「这条命题被较新材料核实过」。另有 14 条由 `srsc-1`（O-12 期间/来源角色）打回：它们的
     支撑边**全部**落在 2024 年报（`history_and_conflict_source`）上，而文本自己用无期间的
     当前式措辞。两类候选都**没有被改写、也没有被补一个期间词**，而是带着逐条 typed 去向退出。
     本批 `srsc-2` 里再分出一类：`srsc-3`（`source_period_scope_dropped`）——候选文本**确实**
     逐字落在材料正文里，但它落进去的那一句**自己带期间/范围限定**，而候选把限定语截在了外面
     （真实一例：「公司销售境外的主要产品为电池系统」截自「报告期内，公司销售境外的主要产品为
     电池系统，较上年同期相比未发生明显变化」）。判据只在候选**覆盖到**的那几句源句上开火：
     源句自带限定语才判，源句本身没有限定语的稳定客观描述**照旧**判 `extractive`（指令原文：
     「不要反过来把所有稳定的客观描述都强行加日期」）。打回不走「补一个期间词」，出口是
     **把限定语一起收进候选**（见 `sections/pack_writer.py` `_directed_reproposal_note`）。

     **这一批的代价必须一起读**：排除面 83 条里有 **46 条是零表面集**的描述性候选
     （`path_b_unproven_current_state` 35 条 + `path_b_history_only_current_state` 11 条），
     正文因此从 47 条 Claim / 7 段 / 13 句降到 12 条 Claim / 2 段 / 4 句。这不是「判据变松换来的
     完整」，也不是「内容被连坐」——逐条原因、逐条成员身份都在案；但它**确实是内容损失**，
     且损失的主因是「当前这一轮 Writer 交的是**改写句**、而 `srsc-2` 只认**严格抽取**」。
     指令二的原文允许这个窄口径（「如果目前只能证明严格抽取式窄情形，那么其余记 `unproven`」），
     出路在**指令三**（材料驱动草稿：原子本身接近逐字，抽取式判据就不再空转），
     **不是**把 `srsc-2` 放宽。本模块按事实记录，不把 2 段读成「已达成人读内容门」。
     `srsc-3` 那 3 条（其中 2 条同时带高风险表面）是**指令第二优先级明确要求**的处置：
     它的代价**应当**计入，不是回归——被它挡下的那句若写进正文，读者会读到一条无期间的持续现状。
  2. 财务节一段由**正式财务事实**（`ddf-1` Δpp 派生事实，Path A）承载的比较分析正文 + 4 张财务表：
     draft 修订与 r7b **记录的** `sdrev_2de6a77463b3f1606f46fc97` 逐字相同，24 条表格 Claim
     各只呈现一次，4 张表与记录**逐格相同**，而那一段正文**不是**任何表格 Claim 的副本。

## 离线重放边界（**本文件必须如实标注的四件事**）

  1. **这不是新的真实验收，也不是一次真实模型调用**。零网络、零新 LLM 调用：门前提案束只来自
     r7b 留存的原始返回。自然组织器用验收 harness 自己的**确定性替身**——r7b 那一轮**没有**
     发过组织调用（`logs/llm` 下 `section_narrative_organizer_v2` 只出现在 2026-09-25 的两个
     文件里，不在 r7b 的窗口内），因此不存在「留存的组织返回」可用。
  2. **公司节的材料正文不经 payload 字节复核**。生产组合根走
     `MC.resolve_writer_material_context`，它用注入的 resolver 取真实 payload **字节**并逐份复核
     `sha256(字节) == payload_ref.content_hash`。r7b 留存的是阅读视图与 manifest 记录，**不含**
     那些字节（真实信封由 `harness/tree_materials.py` 构造，带只存在于当轮现场的对象；已实测：
     按信封形状重算的哈希对不上记录的 `content_hash`）。因此本重放改用 r7b 留存的阅读视图，
     经**同一个**入口 `MC.WriterMaterialContext.create` 构造正文上下文，并在此**显式声明**
     跳过的是「字节是否还在库里」这一步——它不是一道内容判据，没有放宽任何资格。材料身份面在
     `_build_company_case()` 的 `anchors` 里逐条与记录对账（内容指纹 / locator / 来源身份 /
     payload 哈希，四项全等才算数）。
  3. **批 4 是测试夹具**。r7b 的 company 在批 3 之后整束被拒，**从未调用批 4**；本重放给批 4 的
     返回是空束夹具（`call_id=offline_fixture_batch4_empty`）。它不是模型返回，任何地方都不得
     当模型返回读。
  4. **三处身份不可复算，如实声明**：demo 投影 id（记录 `proj_2790e83d29df1286c853b015`）、
     `demo_scope_fingerprint`（记录 `0e1780f495db…`）与财务 artifact id
     （记录 `ffpa_29d817577a7d1e125e63adf0`）在本重放里重算后不同。**可复算**的是：财务 draft
     修订（= 记录值）、公司 draft 身份、writer 侧 `ContractProjection`、财务 snapshot，以及
     两节的 Claim / 聚合决定 / 接受边链。
  5. **依赖身份是两组，不得合并**（第五步）。r7b 记录的依赖身份（contract
     `5c45eabc…` / source_policy `v1` / dep `7f6d71f4…` / navigation_profile
     `schema=anps-4;rule=anp-6` / `material_resolver` `tmr-2`）与**当前工作区**的身份
     （dep `5cab58a4…` / `rule=anp-8` / `material_resolver` `tmr-3`）逐键只差
     `navigation_profile` 与 `material_resolver` **两个键**（`R7B_IDENTITY_DELTA` 声明，
     `_dependency_identity_checks` 逐键复核；两键的差异键集一变，本模块当场红）。
     `material_resolver` 这一键是本轮 M930-3 定点业务纠正①（`tmr-2` → `tmr-3`）带出来的，
     历史向量里如实还原成 r7b 当时的 `tmr-2`。**历史身份不可执行**：`anp-6` 的规则实现
     既不在 git HEAD（HEAD 为 `anp-1`）也不在当前工作区，且 18 键向量从未落库
     （产物里连一个 `dependency_versions` 字段都没有，只留指纹）。因此本模块是
     「**当前代码** + r7b 记录的输入面」的离线重放：依赖身份在这里是**被重建的输入面**的一部分
     （与请求载荷里的 `provider_default` / `pw-13` 同类，改写成今天的样子就是把记录抹平），
     而**跑它的代码是当前版本**。夹具因此仍贴 `REAL_DEP`，当前指纹只被**声明**、不被贴。
     任何身份（draft / 候选 / SectionResult）都**不得**被读成 r7b 的记录产物——r7b 公司节
     没有草稿。`r8` / `r9` 两个同族重放共用这一次 `_patch_fixture_identity()` 与这段声明。

## replay ≠ pass（本文件明确不主张的事）

  * r7b 的 company **没有**任何留存草稿/Claim/正文/结果（`section_drafts.json` 只有
    `financial` 与 `industry`），那一轮公司节是**整节失败**。因此公司半场产出的是**当前代码下
    的新产物**，它的身份是「本批代码 + 本夹具下的确定性身份」，**不是**对某份记录产物的复现。
  * 财务的派生事实是**本批新批的规则**（`ddf-1`），r7b 那一轮不存在这条事实；因此别名重映射
    （`f_k → f_{k+1}`，见 §2）是它带来的必然后果，映射依据逐条断言（20 条逐字节相同 / 4 条只差
    限定语前一个标点 / 0 条表面不符）。
  * 本模块**不**宣称 M930-3、TS5、正式树门或后续阶段关闭，也**不**把 `SECTION_BLOCKED` 读成
    通过：两节都是 `SECTION_BLOCKED`，缺口逐条如实列出。

数据依赖：`logs/llm/` 下 4 个真实调用文件、`evaluation/results/m930_3_acceptance_crossdoc_real_r7b/`、
`data/evidence.db`、`data/financial_v2.db`。都不在版本控制里，缺失时本模块 **typed skip**。
"""

import dataclasses
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import loader_v2 as CV2
from harness import source_manifest as SM
from harness import topic_schema as TS
from planning import demo_scope as DS
from planning import schema as PS
from sections import company_worker as CW
from sections import financial_worker as FW
from sections import material_context as MC
from sections import narrative_organizer as NO
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import presentation_profile as PP
from sections import rules_evaluator as RE
from sections import source_role_scope as SRS
from sections import writing_spec as WS

#: 夹具与替身复用既有反例模块，不另搭一套：两份权威一旦分叉，「重放的是同一条链」就不成立。
#: 该模块顶层只有常量与函数定义，导入它不执行任何用例。
from evals import test_demo_pack_writer as FIX  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
R7B = ROOT / "evaluation" / "results" / "m930_3_acceptance_crossdoc_real_r7b"
LLM_LOGS = ROOT / "logs" / "llm"

#: 本模块是**对留存字节的离线重放**：门前提案束逐字取自 r7b 那一轮的原始返回（`proposals-9`
#: 形态，没有草稿层），因此必须**显式声明**重放线与重放来源——当前线的「有候选 ⇒ 有草稿」
#: 判据在这些字节上必然开火，而那不是在报一个缺陷，是把历史形状判成缺陷。
#:
#: 两条声明缺一不可，且它们说的是两件不同的事：`REPLAY_WIRE` 说「这份载荷是什么形状」，
#: `REPLAY_SOURCE` 说「这些字节从哪来」。声明之后 `model_policy` 仍如实记 `provider_default`
#: ——重放**不得**把原始模型名改写成 `stub` 来换个合规身份（那才是抹掉证据），因此这里走的是
#: `WriterPolicy.replay_source` 那一条合格声明，而不是 `model_policy == "stub"` 那一条。
REPLAY_WIRE = "proposals-10"
REPLAY_SOURCE = "m930_3_acceptance_crossdoc_real_r7b"

#: r7b 那一轮的**真实**身份（逐项取自留存请求 payload 与 `source_manifest.json`）。
REAL_CONTRACT_FP = "5c45eabcad4989622aa3d483d9414304b3dbd0a5127db726f513409d14c6b410"
REAL_SP = "v1"
#: r7b 那一轮**记录**下来的依赖指纹。它是**历史记录的读回值**，不是本批的计算结果，
#: 因此不得改写、也不得被本批产出的对象顶替（那正是「把新指纹重钉成原 r7b」）。
#: 它现在只出现在「重建记录产物」的地方（材料载荷引用等）；本批**产出**的对象一律带当前身份。
REAL_DEP = "7f6d71f48d1836297071587c4a194da786df91213e97273c96f2a44b367838c5"

#: ── 两组依赖身份（第五步）─────────────────────────────────────────────────
#:
#: r7b 的 18 键依赖向量与当前向量**只差两个键**，这个差异必须**写在这里被逐键复核**，
#: 而不是靠一句「复算不出来」把整个模块拦在门口：
#: 差异键若多了一个，本模块必须红——那说明历史身份真的落到别处去了，不能默默当 r7b 用。
#: 值逐字取自 r7b 现场（`anp-6` 是 r7b 当时的导航 profile 规则版本；
#: `tmr-2` 是 r7b 当时的**材料解析语义**版本——M930-3 定点业务纠正① 把它升到 `tmr-3`，
#: 因此历史向量里必须还原成 `tmr-2`，这不是重钉，是对账历史记录的取值）。
R7B_IDENTITY_DELTA = {"navigation_profile": "schema=anps-4;rule=anp-6",
                      "material_resolver": "tmr-2"}

#: 历史身份**不可执行**的理由——不是「不方便」，是**缺件**：`anp-6` 的规则实现在 git HEAD
#: 与当前工作区里都不存在（HEAD 为 `anp-1`，工作区为 `anp-7`），且 18 键向量从未被持久化
#: （`topic_schema.DEPENDENCY_VERSIONS_PERSISTED is False`，只留了它的哈希）。
#: 结论：「用留存的历史依赖版本重建历史回放」**做不到**——没有那版代码可跑。
R7B_IDENTITY_UNEXECUTABLE_REASON = (
    "anp-6 的规则实现不在 git HEAD（HEAD 为 anp-1）、也不在当前工作区（anp-7）；"
    "依赖向量只留哈希、不留逐键值（topic_schema.DEPENDENCY_VERSIONS_PERSISTED is False）——"
    "历史身份可**声明**、可**对账**，但不可**执行**")
COMPANY = "300750"
REPORT_AS_OF = "2026-09-26"
PROFILE_PATH = "templates/demo_scopes/interview_backbone_v1.yaml"
DOCUMENT_ID = "NDSD_2025_year"
DOCUMENT_VERSION = "sha256-c15272977147dee7"
#: r7b 真实锚成员文件的 sha256（`source_manifest.json` → `NDSD_2025_year` 那一项）。
REAL_RAW_PDF_SHA256 = "c15272977147dee7e6935a38ea0e4fd6855370aabb106f54cfe20f7cf6048ec9"
REAL_EVIDENCE_SET_VERSION = "set-501395a7ad5a"

#: r7b 那一轮的 **L0 来源清单**：有序源集（`document_keys` 的序位 + `selection` 的
#: `source_role`）唯一由它派生，本模块不另判角色。生产链里
#: （`harness/topic_runtime.py`）把 `run_context.sources`——即同一份清单派生的有序源集——
#: 挂到本次运行的**每一个** Pack 上；重放夹具必须照做，否则来源角色台账（`srsc-1` 的唯一
#: 取角色处）会与实际材料对不上，判据只能 fail-closed。这不是「为过判据改夹具」：派生结果
#: 与 r7b 自己记录的逐节 `source_set_fingerprint` **逐字相同**，见 `_check_company_replay`。
R7B_SOURCE_MANIFEST = R7B / "source_manifest.json"

COMPANY_TASK_ID = "dtask_9063135aa98d2b834e2a83ae"
COMPANY_ASPECT_COUNT = 46

#: r7b company 的三次真实 narration 返回（`logs/llm/<id>.jsonl` 的 `completion`）。
#: 批 4 从未被调用——见模块 docstring 边界 3。
COMPANY_CALLS = {
    1: "20260926T035654739525__d4548a48cea8498888bb806744ae4753",
    2: "20260926T035722060921__7d333f3e1b574d63a5c63f382f9f8a5e",
    3: "20260926T035741980925__6ffe3da21e55446cb4a79b6129ebbbc2",
}
#: **测试夹具，不是模型返回**：r7b 从未调用批 4，空束让它恰好不产出任何候选。
BATCH4_FIXTURE_LABEL = "offline_fixture_batch4_empty"
BATCH4_FIXTURE = json.dumps(
    {"claim_candidates": [], "narrative_draft_units": [], "follow_up_needs": []},
    ensure_ascii=False)
#: r7b financial 那一次真实 narration 返回（§2 的别名重映射只用它）。
FIN_CALL = "20260926T035822522737__6bf041d4275544d89f5b9ec228ea2def"
FIN_TASK_ID = "dtask_83efe8209c5b4efff222aeff"

#: r7b **记录**的财务 draft 修订（`section_drafts.json` → `financial`）。它是本重放里唯一能与
#: 记录逐字对齐的写作侧身份，因此是 §2 的核心断言。
RECORDED_FIN_REVISION = "sdrev_2de6a77463b3f1606f46fc97"
#: r7b 记录里 company **没有**任何 draft 行（整节失败）——§0 断言这一点，§1 因此不冒充复现。
RECORDED_SECTION_DRAFTS = ("financial", "industry")
#: r7b 记录的两条公司节整束拒绝都是**结构**没过（不是本批要处理的 `path_b_high_risk_surface`）。
RECORDED_COMPANY_REJECTION_KINDS = ("schema_invalid", "schema_invalid")
RECORDED_PENDING_NEEDS = 11

#: 本重放**在当前代码与夹具下**确定性产出的公司侧身份。它**不是** r7b 记录的身份
#: （r7b 没有公司草稿），只是「同一批字节 + 当前代码 ⇒ 同一个结果」的钉子。
#: `nrules-11` 定点批之后这三个身份已随「6 条硬事实候选被拒」而前进：候选集变了，
#: draft / revision / SectionResult 的内容身份必然重算（revision 恰好不变，另行断言）。
#: `norg-4` 定点批只改**门后**的组织方式（按主题分段 + 接缝标点），draft 与 revision
#: 因此**不变**——变的只有 SectionResult：它的内容身份含 final Narrative，正文一改必重算。
#: 读者面定点批又把缺口 detail 里的影响范围从 Python 元组 repr 改成可读并列
#: （`pack_writer._impact_text`）：缺口文案进 draft 的内容身份，因此 draft 与 SectionResult
#: 两个身份都**必然重算**（revision 不变，另行断言）。这不是「钉子被动过」而是「文字变了，
#: 内容身份按规则前进」——归因已逐字复核：把 `_impact_text` 临时打回旧口径，两个身份**逐字
#: 回到**改动前的值 `sdraft_33875a6b30482f29c35a6717` / `sr_secver_e759bb813cb045bc9fb2b55d`，
#: 48 条缺口条数不变、只有那条影响范围文案不同。
#: `norg-5`（M930-3 指令三）改了**门后替身组织器的组织方式**：关系性连接语一律改用中性并列
#: （`nrules-13` 判据 10）、发行人自述句加归属语（判据 11）。draft 与 revision 因此**不变**
#: （改的是门后正文，不是候选束与草稿）；SectionResult 的内容身份含 final Narrative，正文一改
#: 必然重算。归因已逐字复核（探针，非交付物）：把**这两处**同时打回旧口径——替身
#: `_organizer_sentence` 轮转整张 `NS.CONNECTORS` 且不加归属语，且把门的两条新判据
#: `NS.unsupported_relation_connectors` / `NS.unattributed_self_description` 打桩为空
#: （等价于 `ng-11` 的判定集，否则旧正文根本过不了门）——SectionResult **逐字回到**
#: `sr_secver_e11d543b30f142f40867a5e2`，而当时的 Claim / 接受边 / 段 / 句 / 缺口计数
#: **一条不变**：差异只在正文文本，不在任何身份或计数。
#: `pw-14` / `proposals-10`（M930-3 指令二：请求面材料行加**来源角色**、新增一条期间纪律）又让
#: 三个身份前进一次。原因不是内容变了：`derive_draft_revision` 的身份体**含**
#: `writer_policy_version` 与 `prompt_version`，两个版本串一动，draft id / revision /
#: SectionResult 必然重算；而 `ClaimCandidate` 的身份体含 `draft_revision`，于是**每一条候选
#: 身份（`ccand_*`）也一起重算**——条数一条不变（原束 95 / 排除 37 / 幸存 58）、文本一字不改，
#: 变的只是身份串。归因**可执行**，不是一句声明：`_check_version_advance_attribution` 用改动前
#: 的两个版本串（`PRE_BATCH_POLICY_VERSION` / `PRE_BATCH_PROMPT_VERSION`）重算同一批输入，公司侧
#: 逐字回到旧钉子 `PRE_BATCH_COMPANY_REVISION`，财务侧逐字回到 r7b **记录值**
#: `RECORDED_FIN_REVISION`（= 那次真实 run 用的就是改动前的那两个版本串）。
#: **`pw-15` / `proposals-11` 草稿层批**：身份再前进一次，机制与 `pw-14` 那一次**同族**
#: （版本串进身份体 ⇒ draft id / revision / SectionResult 与每一条 `ccand_*` 一起重算），
#: 但**多了一维**：本批给 `derive_draft_revision` 加了 `natural_prose_digest`，草稿层非空时
#: 身份还覆盖草稿内容。**本重放不覆盖这一维**——留存的 r7b 返回是 `proposals-10` 形态
#: （没有 `natural_prose_draft` 键），解析出来草稿层为空、摘要为空串、不进身份体；这一维由
#: `evals/test_demo_pack_writer.py` §25 端到端覆盖（含「同一批输入换一版草稿 ⇒ 另一个修订」）。
#: 本模块把「本重放不覆盖它」写成**可执行事实**而不是声明：`_check_version_advance_attribution`
#: 先断言现场草稿层确实为空、只按两个版本串重算即逐字等于现场修订，再给出**反例**——换成
#: 一个非空摘要，同样两个版本串下身份**必变**（否则「只有两个串是变量」就是拿没测到的
#: 东西当结论）。条数与文本一条未改（95 原束 / 48 排除 / 47 幸存 / 94 边 / 7 段）。
#: **`srsc-1` 期间/来源角色批**：这一次三个身份前进的**原因与上面几次不同**——不是版本串动了，
#: 而是**内容真的变了**。`PW._support_documents` 此前读的是 manifest 的 wire 键 `members`
#: 而不是字段 `entries`，于是来源角色台账恒为空、这条核对在生产链上是**死代码**；接线修好
#: 之后它第一次真正跑在 r7b 的真实材料上，逐条打回 14 条候选（11 条只由这条原因、3 条同时
#: 带高风险表面）。因此本夹具下的身份、条数（48 排除 / 47 幸存 / 94 边 / 47 Claim / 13 句）
#: 与 7 段正文都随真实内容改变，不是「同一批内容换了名字」。
#: **`srsc-2` 独立支撑结论批**：原因与 `srsc-1` 同族（内容真的变了，不是版本串动了），
#: 但**核对面又推进了一步**。`srsc-1` 只问「支撑边绑在哪份材料上」（有没有一条当前锚边），
#: `srsc-2` 再问「那份材料的**正文里到底有没有这句话**」：候选有当前锚边，但候选文本不是
#: 该边阅读视图的严格子串时，记 `unproven`，路径 B 不得据此授权。真实数据上这一问打回 40 条
#: （34 条只由这条原因、6 条同时带高风险表面），幸存 47 → 13。
#: 下面这三个身份与全部条数都随真实内容改变；归因**可执行**：
#: `_check_srsc2_attribution` 把这条判据的**两侧**（候选侧 `_path_b_unproven_current_state`
#: 与句读回 `verify_finalized_current_state_scope`）同时打回空集，身份与条数**逐字回到**
#: 上一批的钉子（`PRE_SRSC2_*` / 48 / 47 / 94 / 47 Claim / 7 段）——差异只由这一条判据造成，
#: 不是本批还有别的东西在动。**只打候选侧会被句读回当场拦下**（两侧是各自实现的，不是同一个
#: 函数的两处调用），这本身就是一条独立的防御纵深证据。
#: **`pw-16` / `proposals-12` / `narr-8` 批（M930-3 指令：打通真实写作请求）**：三个身份又前进
#: 一次，而这一次的成因**有两维，必须分开读**——否则「候选身份为什么动」会被归错原因：
#:   (a) **版本串维度**（与 `pw-14` / `pw-15` 同族）：`PACK_WRITER_POLICY_VERSION` `pw-15`→
#:       `pw-16`、`NARRATION_PROMPT_VERSION` `…@proposals-11`→`…@proposals-12`。两个串都在
#:       `derive_draft_revision` 的身份体里 ⇒ draft id / revision / SectionResult 必然重算；
#:       而 `ClaimCandidate.identity_body` 含 `draft_revision` ⇒ 每一条 `ccand_*` 跟着重算。
#:   (b) **schema 维度（本批新增的一维）**：`NARRATIVE_SCHEMA_VERSION` `narr-7`→`narr-8`（草稿层
#:       加第二条**出处轴** `source_fact_refs`）。`ClaimCandidate.identity_body` **也含**
#:       `schema_version`，因此 `ccand_*` 除 (a) 之外**还有第二个独立原因**；而
#:       `derive_draft_revision` 的身份体**不含** `schema_version`，所以 (b) **动不了**
#:       draft id / revision / SectionResult。两个维度的分工因此是**可执行**的，不是声明：
#:       `_check_version_advance_attribution` 既用旧两个版本串重算修订（逐字回到老钉子 ⇒
#:       (b) 确实没进修订身份体），也把一条真实候选的 `identity_body` 只换 `schema_version`
#:       重算内容身份（身份**必变** ⇒ (b) 确实在候选身份体里）。「候选身份为什么动」的完整
#:       解释因此是 (a) + (b)，不能只归给版本串。
#: 内容侧一条未改：条数与文本仍是原束 95 / 排除 82 / 幸存 13 / 51 边 / 13 Claim / 3 段。
#: **`srsc-3` 期间/范围限定批（指令二「逐条检查那三句跨文档版本、无期间的正文句」）**：三个身份
#: 又前进一次，成因是**两维同时动**，与 `pw-16` 那一次同族但读数不同：
#:   (a) **版本串维度**：`PACK_WRITER_POLICY_VERSION` `pw-16`→`pw-17`（`srsc-3` 是写入侧判据，
#:       它的版本串必须进身份体）。`ClaimCandidate.identity_body` 含 `draft_revision` ⇒ 每条
#:       `ccand_*` 跟着重算。版本串维度由 `_check_version_advance_attribution` 照旧证明。
#:   (b) **内容维度（本批真正的成因）**：`srsc-3` 让 3 条候选从 `extractive` 落 `unproven`
#:       ——1 条只由这一条原因、2 条本已因高风险表面被拒而**追加**这条原因。候选集因此真的变了：
#:       排除 82 → **83**、幸存 13 → **12**、边 51 → **49**、Claim 13 → **12**、
#:       段 3 → **2**、句 5 → **4**。这不是「身份被动过」而是「内容真的变了」，
#:       与 `srsc-1` / `srsc-2` 那两次同族（见本文件 §2.5 的归因形状）。
#: **代价必须与结论一起读**：被 `srsc-3` 挡下的那句「公司销售境外的主要产品为电池系统」若写进
#: 正文，读者会读成一条**无期间的持续现状**（源句是「报告期内，公司销售境外的主要产品为电池
#: 系统，较上年同期相比未发生明显变化」）。指令原文因此明确要求它**不得**被截成无期间句
#: （「保留可核验的期间，或改成显式限定范围的表述；若撑不住就不写」）。少一句是**这条要求的结果**，
#: 不是回归；下限仍按结构算，不靠放宽 fail-closed 换条数。
#: **`pw-18` 请求自洽批（M930-3 r8 前的最小请求自洽修正）**：三个身份又前进一次，成因与
#: `srsc-1`/`srsc-2`/`srsc-3` **不同族**——那三次是**内容真的变了**（候选被判据打回），这一次
#: **内容一条没变**（条数与原文在本批读数上逐项相同：排除 83 / 幸存 12 / 边 49 / Claim 12 /
#: 段 2 / 句 4），动的只有**一个版本串**：`PACK_WRITER_POLICY_VERSION` `pw-17`→`pw-18`。
#:   (a) **版本串维度**：`pw-18` 改的是请求里 `output_schema.natural_prose_draft` 的**示例值**
#:       （旧示例把两条互斥的出处轴同时填满，与同一份请求的 rules 逐字矛盾）。它进
#:       `derive_draft_revision` 的身份体 ⇒ draft id / revision / SectionResult 必然重算；
#:       `ClaimCandidate.identity_body` 含 `draft_revision` ⇒ 每一条 `ccand_*` 跟着重算。
#:       这一维由 `_check_version_advance_attribution` 照旧证明（换回旧两个串 ⇒ 逐字回到
#:       上一批的三个钉子 `sdraft_ec7ce7bb…` / `sdrev_e37547ca…` / `sr_secver_2ba019bb…`）。
#:   (b) **没有任何内容维度**：本批没有新判据、没有候选被判据打回、没有材料被换掉，因此
#:       `PRE_SRSC2_*` 那组归因钉子同样**只是版本串前进**（同一个回滚读数、新的一对身份）。
#:       两者必须分开读：拿 (a) 去解释 `PRE_SRSC2_*` 的位置变化是对的，拿它去解释「为什么少了
#:       一条候选」就是错的——本批一条候选都没少。
#: **`pw-19` 请求契约窄修批（M930-3 r8 前最后一次）**：三个身份又前进一次，成因与 `pw-18`
#: 同族——**内容一条没变**（条数与原文在本批读数上逐项相同：原束 95 / 排除 83 / 幸存 12 /
#: 边 49 / Claim 12 / 段 2 / 句 4），动的只有**一个版本串**：
#: `PACK_WRITER_POLICY_VERSION` `pw-18`→`pw-19`（提示词资产同时 `v8@proposals-12` →
#: `v9@proposals-13`，两者一起进身份体）。
#:   (a) **版本串维度**：`pw-19` 改的是同一份请求里**剩下三处**示例（候选支撑边、context
#:       支撑边、补件 `budget_hint`）加一条入站防线——`pw-18` 只修了正文出处轴那一处，本批把
#:       「材料轴节示例引 `f1`」「事实轴节示例引 `m1`」「补件示例空预算」三处一并按**输入面**
#:       生成。它进 `derive_draft_revision` 的身份体 ⇒ draft id / revision / SectionResult 必然
#:       重算；`ClaimCandidate.identity_body` 含 `draft_revision` ⇒ 每一条 `ccand_*` 跟着重算。
#:       这一维由 `_check_version_advance_attribution` 照旧证明。
#:   (b) **入站防线不是内容判据**：空 `budget_hint` 在写入边界逐条 typed 拒绝（`code=
#:       budget_hint_empty`），它只作用于**补件**，本重放的两节都不发补件，因此它在本模块的
#:       读数上一条都没开火——它由 `evals/test_m930_3_rejection_retention.py` §6 与
#:       `evals/test_demo_pack_writer.py` §27d 覆盖，不在本模块冒充已验。
#:   (c) **没有任何内容维度**：本批没有新判据、没有候选被判据打回、没有材料被换掉，因此
#:       `PRE_SRSC2_*` 那组归因钉子同样**只是版本串前进**（同一个回滚读数、新的一对身份）。
#:       拿 (a) 去解释「为什么少了候选」就是错的——本批一条候选都没少。
#: **`pw-20` / `proposals-14` 批（M930-3 r8 后业务纵链收口 §一）**：三个身份又前进一次，
#: 成因与 `pw-18`/`pw-19` 同族——**内容一条没变**（本批读数逐项相同：原束 95 / 排除 83 /
#: 幸存 12 / 边 49 / Claim 12 / 段 2 / 句 4），动的只是**两个版本串**：
#: `PACK_WRITER_POLICY_VERSION` `pw-19`→`pw-20`、`NARRATION_PROMPT_VERSION`
#: `v9@proposals-13`→`v10@proposals-14`。
#:   (a) **版本串维度**：`pw-20` 给请求面多投一份**确定性**读数 `batch_support_scope`
#:       （`bscope-1`），并在解析侧新增 `assert_batch_scope_respected`（本批所有 aspect 都
#:       没有可引用行时三个内容键必须为空数组；被引用的行必须属于本批 topic）——违反仍走既有
#:       `schema_invalid` 通道，**不新增额度、不静默删草稿、不伪造引用**。提示词资产同时
#:       新建 v10 / `proposals-14`（v9 一字不动作只读基线；线格式键集键序未变，wire 仍是
#:       `proposals-12`）。两个串都进 `derive_draft_revision` 的身份体 ⇒ draft id / revision /
#:       SectionResult 必然重算；`ClaimCandidate.identity_body` 含 `draft_revision` ⇒ 每一条
#:       `ccand_*` 跟着重算。这一维由 `_check_version_advance_attribution` 照旧证明。
#:       触发这件事的真实现场是 r8 的 company 一节：4 批里 3 批出现「无出处轴的缺失陈述草稿」，
#:       前两批各花掉一次纠正额度后过关，第 4 批正好把共享额度用尽 ⇒ 整节 typed `schema_invalid`。
#:   (b) **没有任何内容维度**：本批没有新判据、没有候选被判据打回、没有材料被换掉。
#: **`pw-21` / `proposals-15` 批（指令 D §二·三条日期轴）**：三个身份再前进一次，成因同族——
#: 仍是**只动两个版本串**（本批读数逐项相同：95 / 83 / 12 / 49 / 12 / 2 / 4）。
#:   (a) **版本串维度**：`pw-21` 与 `pw-20` 的差别**只在提示词那一侧**——新建 prompt 资产 v11 /
#:       `proposals-15`（v10 一字不动作只读基线），新增「材料披露的写法：三条日期轴」一节
#:       （归属语不由写者写、不得不成期间地写成「一直如此」、新旧实质差异不得抹平、新闻事件日
#:       与发布日分开、披露日未知就标未知、`report_as_of` 不写）。**请求面键集与键序一字未变**、
#:       解析侧判据一字未动（wire 仍是 `proposals-12`），但写作策略版本必须前进——它覆盖的是
#:       「本轮写作策略是什么」，提示词资产的身份就是它的一部分。
#:   (b) **同批的规则版本维度（不进 draft 身份体，进正文判定集）**：`NARRATIVE_RULES_VERSION`
#:       `nrules-15`→`nrules-16`、`NARRATIVE_GATE_VERSION` `ng-14`→`ng-15`——第 12 条判据的
#:       **实现一字未改**，扩的是封闭标记集（当前式措辞新增普遍化组 `一直`/`始终`/… 与否定式
#:       普遍化组 `从未`/`未曾`）。这一维**不**进 `derive_draft_revision` 的身份体（同 `narr-8`
#:       那次的读法），所以它解释不了修订前进；它解释的是「同一份正文在旧版本下通过、在新版本下
#:       被拒」。本批正文里没有这两组标记（本重放读数：句数、段数与 Claim 数一条未变），
#:       因此 SectionResult 的前进同样只是 (a) 的结果——本模块不拿 (b) 去解释身份。
#: **代价与结论一起读**：本次前进**不是**「内容少了」——`srsc-2`/`srsc-3` 之后那 12 条候选、
#: 2 段 4 句正文一条未动（前几批记的内容代价照旧成立，本批既没还债也没新增）。
#: **`RECORDED_FIN_REVISION` 不动**：那是 r7b 那一轮的**历史记录值**，不是本重放的复算读数；
#: 复算读数 `REPLAY_FIN_REVISION` 前进，历史记录仍逐字保留，两者由 §2.3 分别断言。
#: **`pw-22` / `proposals-16` 批（M930-3 r9 后返修 B：草稿闭合读法）**：三个身份再前进一次，
#: 成因同族——仍是**只动两个版本串**（本批读数逐项相同：95 / 83 / 12 / 49 / 12 / 2 / 4）。
#: 新资产 v12 把「一条候选恰好一处表达」改写成「键集合一一对上 + 逐 occurrence 核验出处」，
#: **请求面键集/键序与解析侧判据一字未动**（wire 仍是 `proposals-12`），但提示词资产身份是
#: 写作策略身份的一部分，故两个串都前进。本重放的正文里**没有**一条候选被多个草稿单元声明
#: （`npr-1` 放宽的是那种形状的判据，不是本夹具的形状），因此内容钉值一条未动。
#: **`m930-3-acc-38` 批（M930-3 r11 诊断第 1 步：离线替身的**选材**与**组织**定点修复）**：
#: 本批动的是**离线替身自己**的两处写法，不是任何门、契约或 Pack；它因此有**两个维度**，
#: 而且其中一个在本重放上**完全不参与**——必须分开读：
#:   (a) **选材维度（本重放不覆盖）**：`_slice_atoms` 把「过长但有业务价值的原文句」按**句读**
#:       切成有界原子（每个原子仍必须是来源原文里连续、可定位、且**自带陈述对象**的片段），
#:       承接残片（「并通过…」「能满足…」一类无独立主语的片段）不再进候选。本重放两节的候选束
#:       来自 **r7b 留存的真实返回**（3 次 recorded + 1 次空夹具），**不是**替身选材，因此这一维
#:       在本模块读数上一条都不开火：draft id / revision 与上一批**逐字相同**（下方钉子未动）。
#:       它的正反例由 `evals/test_m930_3_acceptance_gates.py`（`_slice_audit` 正反例）、
#:       `evals/test_demo_topic_runtime.py`、`evals/test_demo_writer_formal_chain.py` §三 与本批
#:       r12 离线替身读数覆盖，不在这里冒充已验。
#:   (b) **组织维度（本重放唯一的移动原因）**：`_organizer_sentence` 改为**逐接缝**各自选一个
#:       中性并列连接语（`NS.NEUTRAL_CONNECTORS`）并按接缝序号轮转，而不是整句共用一个。本重放的
#:       门后正文**就是**替身组织器写的，正文一改，`SectionResult` 的内容身份（含 final Narrative）
#:       必然重算——`REPLAY_COMPANY_SECTION_RESULT` 与 `PRE_SRSC2_COMPANY_SECTION_RESULT` 两个
#:       钉子因此一起前进，而 draft id / revision **不动**（改的是门后，不是候选束与草稿层）。
#:       归因**可执行**（探针，非交付物）：只把这一处打回「整句共用一个连接语」、其余输入一字不动，
#:       SectionResult **逐字回到**旧钉子 `sr_secver_11646209b33ad0af632adf25`；条数、Claim 数、
#:       段数（2）与句数一条不变。差异只在正文文本——旧读数是同一句里 4 次「此外，」的机械串接，
#:       新读数是「此外 / 同时」交替——不在任何身份或计数上。
#:   (b2) **同批另一处改动在本夹具上是空转**：`_organizer_sentence` 还会把首条是**承接残片**的
#:       chunk 抬到第一个自带陈述对象的 Claim 上。本重放的正文里没有这样的 chunk（探针里单独
#:       打回它，身份**逐字不变**），因此它解释不了任何身份移动——它由
#:       `evals/test_m930_3_offline_organizer_sentence.py` 的反例覆盖。这一句写在这里，正是为了
#:       不让「本批动了两处」被读成「两处都让身份动了」。
REPLAY_COMPANY_DRAFT_ID = "sdraft_a3fa3718ff05a5c4d76362c7"
REPLAY_COMPANY_REVISION = "sdrev_9547fe94b07f4dcde12e7da7"
REPLAY_COMPANY_SECTION_RESULT = "sr_secver_6ab0e8f13dbc3d5a21961d3f"
#: 身份前进前的两个版本串与当时两侧的 draft 修订（同上归因，留在文件里当**可执行的反例**）。
#: **一组两个锚点，两侧各自都要逐字回到**（本批起从一组变成两组：`pw-21` 之前的那一对，
#: 以及 r7b 那一轮真实 run 用的那一对——财务侧只有后者能与**记录**逐字对齐，公司侧两者都是
#: 本重放的复算读数）。两组都由 `_check_version_advance_attribution` 实测，不是抄下来的历史：
#:   ① `PRE_BATCH_*`（上一批：`pw-20` / `v10@proposals-14`）；
#:   ② `R7B_ERA_*`（r7b 那一轮真实 run 用的两个串：`pw-13` / `v7@proposals-9`）。
PRE_BATCH_POLICY_VERSION = "pw-21"
PRE_BATCH_PROMPT_VERSION = "pack_section_writer_proposals_v11@proposals-15"
PRE_BATCH_COMPANY_REVISION = "sdrev_277f86cbabb80de0b4cf9d8c"
PRE_BATCH_FIN_REVISION = "sdrev_1bfb9506ca75aafcdc13bfd8"
#: r7b 那一轮**真实 run** 用的两个版本串与当时的读数。公司侧那一个是本重放的复算读数
#: （r7b 没有公司草稿），财务侧那一个**就是**记录值 `RECORDED_FIN_REVISION`（§2.3 的核）。
R7B_ERA_POLICY_VERSION = "pw-13"
R7B_ERA_PROMPT_VERSION = "pack_section_writer_proposals_v7@proposals-9"
R7B_ERA_COMPANY_REVISION = "sdrev_6e5fa99d089450f89a8c901e"
#: **把 `srsc-2` 的两侧打回空集**后的身份与条数（同一批版本串、同一批夹具下的读数）。
#: 它们不是历史注脚，而是**可执行的反例**：`_check_srsc2_attribution` 真的把这条判据的两侧
#: 打回空集跑一遍，下面这组必须逐字回来。
#: **与上一批记录值的关系要分开读**：这组身份串在本批又前进了一次（`pw-16`→`pw-17` 进身份体），
#: 因此它**不再**逐字等于本文件早先记录的那一组（`sdraft_b17818021623d5dd56f15cbd` /
#: `sr_secver_66bc40106d7ddab70f7551e8`）。两个维度各有**独立**归因，不互相借力：
#: 版本串维度由 `_check_version_advance_attribution` 证明（换回旧两个串 ⇒ 逐字回到旧值），
#: `srsc-2` 维度由本函数证明（打回这条判据 ⇒ 逐字回到本组）。若把两组混着读，就会出现
#: 「拿版本串的差异去解释判据的差异」这种归因错误。
#: **`srsc-3` 不干扰本组读数**：`srsc-3` 的判定在 `source_role_scope.current_state_support_is_extractive`
#: 内部，而本函数的回滚把调用它的 `_path_b_unproven_current_state` 整条打空（并同时把句读回打空），
#: 因此回滚读数里 `srsc-2`/`srsc-3` **一起**不参与——这正是「差异只由这一族判据解释」的口径。
#: **`acc-38` 之后本组 SectionResult 又多了一个前进原因，必须与 `srsc-2` 那一维分开读**：
#: 这一组是**两个读数共用一个组织器**的读数，组织器换了，两边一起换。draft 身份因此**不动**
#: （组织器在门后，不碰候选束与草稿层），SectionResult **动**（它的内容身份含 final Narrative）。
#: 两个原因各有独立归因，不互相借力：`srsc-2` 那一维由 `_check_srsc2_attribution` 证明（打回本条
#: 判据 ⇒ 回到本组），组织器那一维由上一条 `acc-38` 注记的探针证明（打回逐接缝轮转 ⇒ 两个
#: SectionResult 钉子同时回到上一批的值）。若把两组混着读，就会出现「拿组织器的差异去解释判据
#: 的差异」这种归因错误。
PRE_SRSC2_COMPANY_DRAFT_ID = "sdraft_59a0212018ce31d41b1bd913"
PRE_SRSC2_COMPANY_SECTION_RESULT = "sr_secver_10790d06c758a94f2154e1bb"
PRE_SRSC2_EXCLUDED_CANDIDATES = 48
PRE_SRSC2_SURVIVING_CANDIDATES = 47
PRE_SRSC2_SUPPORT_PROPOSALS = 94
PRE_SRSC2_COMPANY_CLAIMS = 47
PRE_SRSC2_COMPANY_PARAGRAPHS = 7
#: 打回空集时近义配对**非空**（4 对）——下面 §2.4 那条「两侧配对一致」的断言因此在改动前
#: 读数上不是空转；本批正文只剩逐字抽取的 12 条候选，配对自然归零（归零不是断言被跳过）。
PRE_SRSC2_NEAR_DUPLICATE_PAIRS = 4
#: 财务侧**本重放**的 draft 修订（改动前它逐字等于记录值 `RECORDED_FIN_REVISION`；
#: 两个版本串前进后身份必然重算，归因见 `_check_version_advance_attribution`）。
REPLAY_FIN_REVISION = "sdrev_ec587192f2f2b6a3796f5907"
REPLAY_COMPANY_REJECTION_KIND = "path_b_high_risk_surface"
#: `cco-3` 定下、`cco-4` 与 `cco-5` 沿用同一条排除口径，从原束 95 条候选里逐条排除的 **83** 条
#: 中的两条（身份与表面逐字来自那一轮真实返回；
#: 身份串已随本批夹具与版本串重算，文本未改），定位**按文本**、身份**按钉子**核对。
#: 它们只是**示例**，不是被排除的全集——被排除的每一条都另有独立拒绝记录。
#: 注意：本批它们**各自**还多带了一条原因（`srsc-3` 的 `path_b_unproven_current_state`，
#: 见下面 `PERIOD_SCOPE_DROPPED_NAMED_CANDIDATES`），但仍属「两条被点名的排除示例」。
NAMED_EXCLUDED_STOCK_TEXT = "公司以资本公积金向全体股东每10股转增8股"
NAMED_EXCLUDED_ENTITY_TEXT = "宁德时代新能源科技股份有限公司前身为宁德时代新能源科技有限公司"
NAMED_EXCLUDED_CANDIDATE_IDS = ("ccand_1cdefe090c215ad826025f89",
                                "ccand_b026518979d8c03ded205b2a")
EXCLUDED_STOCK_TOKEN = "10股"
EXCLUDED_ENTITY_NAMES = ("宁德时代新能源科技股份有限公司",
                         "宁德时代新能源科技有限公司")
#: 原束 / 裁出 / 新修订的规模（本重放的确定性事实，逐项断言）。
#: `nrules-11` 定点批：被排除的 31 条 → 37 条（新增 6 条硬事实候选，逐条见
#: `HIGH_RISK_HARD_FACT_CANDIDATES`），原束 95 条不变，幸存随之 64 → 58。
#: **`srsc-1` 期间/来源角色批**：37 → 48 条（新增 11 条只由同类较旧材料支撑的当前态断言；
#: 另有 3 条本已因高风险表面被拒的候选**同时**带上这条原因）。原束 95 条仍不变，幸存 58 → 47。
#: 这不是判据变严，是这条判据此前在**生产链上是死代码**（`_support_documents` 读错属性名，
#: 见 `sections/pack_writer.py` 该函数 docstring）；修好接线后它才第一次真正跑在真实数据上。
#: **`srsc-2` 独立支撑结论批**：48 → 82 条（新增 34 条只由 `path_b_unproven_current_state`
#: 打回的候选，6 条本已因高风险表面被拒的候选**同时**带上这条原因）。原束 95 条仍不变，
#: 幸存 47 → 13。同样不是判据变严：这条判据是本批新接的线，此前生产链上**根本不存在**。
#: 代价要一起读：新增的 34 条是**零表面集**的描述性候选（不是硬事实、也不是高风险措辞），
#: 它们被拒的唯一原因是「当前这一轮 Writer 交的是改写句，`srsc-2` 只认严格抽取」。
#: **`srsc-3` 期间/范围限定批**：82 → **83** 条（新增 1 条只由 `source_period_scope_dropped`
#: 打回的候选 —— 「公司销售境外的主要产品为电池系统」，2 条本已因高风险表面被拒的候选
#: **同时**带上这条原因）。原束 95 条仍不变，幸存 13 → **12**。
#: 它不是「判据变严」而是**判据分细**：此前这一族全部记在 `not_extractive_in_any_current_source`
#: 下（一句「正文里没这句话」），本批把「正文里**有**这句话、但候选把源句自带的期间/范围限定
#: 截在了外面」单独记成 `source_period_scope_dropped`——也就是指令第二优先级点名的那三句。
#: 代价要一起读，而且这一笔**是要求的结果**：那 3 条里 1 条（境外销售）此前**写进过正文**，
#: 本批不再写；剩下 2 条本来就因高风险表面被拒，只是并入本类原因。
BUNDLE_CANDIDATES = 95
EXCLUDED_CANDIDATES = 83
SURVIVING_CANDIDATES = 12
#: 其中带**材料范围不合格**（`path_b_ineligible_material_scope`）的条数（都是双原因）。
INELIGIBLE_SCOPE_CANDIDATES = 3
#: 带**期间/来源角色**（`path_b_history_only_current_state`）的条数，以及其中只带这一条原因的
#: 条数（余下 3 条同时带高风险表面）。逐条文本见 `HISTORY_ONLY_NAMED_CANDIDATES`。
#: 这两个数在 `srsc-2` 前后**不变**（`srsc-2` 与 `srsc-1` 互斥：一条候选要么锚边全是历史角色，
#: 要么有当前锚边而正文里没这句话，不会同时命中），因此那 11 条钉子的身份与原文也一字未改。
HISTORY_ONLY_CANDIDATES = 14
HISTORY_ONLY_SOLE_REASON_CANDIDATES = 11
#: 带**独立支撑结论**（`path_b_unproven_current_state`）的条数，以及其中只带这一条
#: 原因的条数（余下 8 条本已因高风险表面被拒）。`srsc-2` 之后是 40 / 34；
#: `srsc-3` 把其中 3 条**细分成** `source_period_scope_dropped`（1 条只带这一条原因、
#: 2 条另有高风险表面），总数因此 40 → **43**、只带这条原因的 34 → **35**。
#: 两条 `srsc` 判据记的是**同一条原因码** `path_b_unproven_current_state`（同一个「路径 B 不得
#: 据此授权」的结论），细分只体现在 `unproven_current_state_cause_code` 这个 typed 表面上——
#: 所以这里的条数变化是**内容真的变了**（境外销售那句不再授权），不是「换了个名字」。
#: 零表面集的那部分：35 条（43 减去 8 条双原因）。
UNPROVEN_CURRENT_STATE_CANDIDATES = 43
UNPROVEN_CURRENT_STATE_SOLE_REASON_CANDIDATES = 35
#: 双原因候选共 14 条，恰好三类：材料范围不合格 3 + 期间/来源角色 3 + 独立支撑结论 8，
#: 三条原因都另有高风险表面这一条。逐类断言（不是只断言总数）。
#: `srsc-3` 让「独立支撑结论」那一类 6 → 8：新增的 2 条是「A 股上市」与「每 10 股转增 8 股」，
#: 它们本来就被高风险表面拒了，本批**追加**了期间/范围这一条原因。
DUAL_REASON_CANDIDATES = 14
DUAL_WITH_HISTORY_ONLY = 3
DUAL_WITH_UNPROVEN = 8
#: 11 条**只**由这条原因被打回的候选：候选身份、原文（身份串随夹具与版本串重算，文本未改）。
#: 它们逐条来自 r7b 留存的真实返回；这里按原文定位、按钉子核身份。
HISTORY_ONLY_NAMED_CANDIDATES = (
    ("ccand_11d5bd8a1c2f0945b5dc8c38",
     "公司动力电池应用拓展至工程机械、船舶、航空器等新兴应用场景"),
    ("ccand_596ffa57c7317fdd03fd4acb",
     "公司推出滑板底盘、巧克力换电、骐骥换电等创新解决方案"),
    ("ccand_4455b47457cf3352d74a6239",
     "公司产品可应用于BEV、REV、PHEV、HEV等不同细分市场"),
    ("ccand_ae0cd8d5021a5a35901f84bc",
     "公司基于多样的应用场景和产品全周期的经济性开发多款发电侧、输配电侧储能专用电芯以及用户侧系列电芯"),
    ("ccand_88643922813afb031c67725e",
     "公司储能电池广泛应用于公用事业储能、工商业储能及数据中心储能等"),
    ("ccand_387179d4319d31ecec7fedd1",
     "公司动力电池产品包括电芯、模组/电箱及电池包"),
    ("ccand_5bf6cf65fad102d1141c8e9b",
     "公司可提供磷酸铁锂电池、三元高压中镍电池、三元高镍电池、钠离子电池、M3P电池、凝聚态电池等覆盖不同能量密度区间的多种化学体系产品系列"),
    ("ccand_c1cbc2f93d5186f91595c513",
     "公司提供电芯、电池柜、储能集装箱以及交流侧系统等储能产品解决方案"),
    ("ccand_ed7456d7cd5784ec08f30dcc",
     "公司的储能电池广泛应用于表前储能和表后储能领域"),
    ("ccand_bbb8387bf42a43e096e69a78",
     "公司的动力电池应用不断拓展至工程机械、船舶、航空器等新兴应用场景"),
    ("ccand_7ca2d500ee1e1cc114827288",
     "公司持续推出创新解决方案，包括滑板底盘、针对乘用车领域的巧克力换电、针对重卡领域的骐骥换电解决方案等"),
)
#: `norg-4` 定点批之后：公司节正文按**材料标题树派生**的主题分段（不再是把整个
#: `company_business` 主题塞进一段）。`srsc-2` 之前是 7 段（6 段业务主题 + 1 段主体沿革）；
#: 本批幸存 12 条候选仍全部落在 `company_business` 主题上，主体沿革那一段**没有候选可组织**，
#: 因此只剩 2 段业务主题（2 + 2 句）、共 4 句。**这不是「分段逻辑坏了」**：分段规则没动，
#: 是它手里没有别的主题的 Claim 了——若断言 `COMPANY_PARAGRAPHS == 7` 就是拿旧内容当真。
COMPANY_PARAGRAPHS = 2
COMPANY_BUSINESS_PARAGRAPHS = 2
COMPANY_BUSINESS_SENTENCES = 4
#: 主体沿革段本批**为空**：不是「不该有」，是它的候选全被 `srsc-2`/`srsc-1`/`srsc-3` 打回了、
#: 没有 Claim 可组织。空集本身是要断言的事实，不能悄悄把这一条删掉当没发生过。
COMPANY_IDENTITY_SENTENCES = ()
COMPANY_TOTAL_CLAIMS = 12
#: 本批正文逐句回查到的 `(evidence_id, page)` 对数下限。`srsc-2` 之前是 8（断言 `>= 8`），
#: 幸存候选减少后 7，`srsc-3` 之后 5。下限**不写死读数**，写成由结构决定的数——
#: 「每条业务段至少两条边」：本批 2 条业务段 ⇒ 4。条数再变也不会变成假读数，
#: 同时仍要求覆盖多份文档、多页（那两条另在下面断言）。
MIN_EVIDENCE_PAIRS = 4
#: 页码覆盖下限同理：`srsc-3` 之后实得 [14, 15, 16]——**仍是跨页**（不是只剩一页），
#: 因此下限取 3。写死 5 会把一次真实的内容缩减误报成「页码覆盖不够」。
MIN_CITED_PAGES = 3
#: `nrules-11` 定点批要处置的 6 条硬事实候选：候选身份、原文、**期望的**高风险表面集。
#: 它们逐字来自 r7b 留存的真实返回（批 1～3），不是为测试新造的句子（身份串随版本串前进重算
#: ——`pw-19`→`pw-20` 一次、本批 `pw-20`→`pw-21` 又一次，原文与表面一个字都没改
#: ——下面按原文定位、按钉子核身份）。判据是通用的
#: （见 `evals/test_m930_3_risk_surface_semantics.py` 的换主体反例），这里只是拿真实数据复核。
HIGH_RISK_HARD_FACT_CANDIDATES = (
    ("ccand_16edbb28c3dcafcb7dddb96a", "公司法定代表人为曾毓群", ("法定代表人",)),
    ("ccand_7ffba34f77d9a42bf1cc7c69", "公司会计期间采用公历年度",
     ("会计期间", "公历年度")),
    ("ccand_d65ddd837cb5194b9fadb28c", "本公司及境内子公司以人民币为记账本位币",
     ("记账本位币", "本位币")),
    ("ccand_7da1727b1e487e2a203c797d", "境外客户回款情况正常", ("正常",)),
    ("ccand_35fe817e33b629941308547a",
     "公司作为整体，产品和服务的对外交易收入情况详见年度报告财务报告部分", ("详见",)),
    ("ccand_eea94b415142aed2dcc63374", "公司A股股票在深圳证券交易所上市",
     ("上市", "深圳证券交易所")),
)

#: **`srsc-3` 逐条点名的 3 条候选**（指令第二优先级原文：「逐条检查那三句跨文档版本、无期间的
#: 正文句……特别不得把源文的『报告期内，公司销售境外……』截成无期间的持续现状」）。
#: 每条给出：候选身份、原文、**期望的** typed 原因码表面（`unproven_current_state_cause_code`）、
#: 以及该条除本类原因外是否另有高风险表面。
#: 定位按原文、身份按钉子——身份串随版本串前进重算（`pw-19`→`pw-20`→`pw-21`），文本一字未改。
#: 这三条**不是**新造的句子：第 1 条此前**写进过正文**（`srsc-2` 只问「候选文本在不在材料正文
#: 里」，它在，于是 `extractive`）；`srsc-3` 追问「包含它的那一句**自己**带不带期间/范围限定」，
#: 才判它撑不住。另两条本就因高风险表面被拒，本批**追加**这条原因。
PERIOD_SCOPE_DROPPED_NAMED_CANDIDATES = (
    ("ccand_c155c028ed450f906ea30583", "公司销售境外的主要产品为电池系统",
     "source_period_scope_dropped", False),
    ("ccand_eea94b415142aed2dcc63374", "公司A股股票在深圳证券交易所上市",
     "source_period_scope_dropped", True),
    ("ccand_1cdefe090c215ad826025f89", "公司以资本公积金向全体股东每10股转增8股",
     "source_period_scope_dropped", True),
)
#: 源句（2025 年报里**带**限定语的那一句，逐字取自材料阅读视图）。断言两件事：
#: ① 候选文本是它的**真子串**（所以「包含」这一步确实成立，打回不是靠「没找到这句话」）；
#: ② 它自己**带**期间限定（所以「限定语被截在外面」是可复算的事实，不是判读）。
PERIOD_SCOPE_SOURCE_SENTENCE = (
    "报告期内，公司销售境外的主要产品为电池系统，较上年同期相比未发生明显变化。")
#: 对照面（**不写死句子**，在检查里从现场材料现算）：`srsc-3` 对「源句自己不带限定语」的
#: 稳定客观描述**不得**开火——指令原文「不要反过来把所有稳定的客观描述都强行加日期」。
#: 因此检查里对**每一条幸存候选**重算它覆盖到的源句，并断言 `source_period_scope_dropped`
#: 一条都不成立（否则就是拿「加日期」当通过条件，写死的对照句会随候选集一变就成了假读数）。

#: §2 派生事实（`ddf-1`）的权威身份与展示面。
DELTA_FACT_ID = "derived_SOLV_DEBT_RATIO_DELTA_PP_2025-12-31_2024-12-31"
DELTA_SOURCE_CODE = "SOLV_DEBT_RATIO"
DELTA_PERIOD = "2025-12-31|2024-12-31"
DELTA_PERIOD_LABEL = "2025年末较2024年末"
DELTA_DISPLAY = "-3.3个百分点"
DELTA_FORMULA_VERSION = "ddf-1"
DELTA_SHORT = "资产负债率变动"
#: r7b 记录里 financial 有 **0 段**正文、4 张表（§2 的核心对照：本批新增的正是那一段分析）。
RECORDED_FIN_PARAGRAPHS = 0
RECORDED_FIN_TABLES = 4
RECORDED_FIN_CELLS = 24

#: 可见缺口里**不得**出现的内部字段名（§二读者面：缺口文案只讲人读内容，不倒内部字段赋值）。
FORBIDDEN_GAP_FIELDS = ("task=", "topic=", "reason_codes=", "aspect_id=", "unresolved_id=",
                        "claim_id=", "fact_id=")
#: 缺口状态词表（`pack_writer._STATUS_TO_QUESTION_STATE` 的值域）。**不是**「已覆盖」。
GAP_STATES = ("NOT_PROVIDED", "NOT_FOUND_AFTER_SEARCH", "NOT_APPLICABLE")

#: `patch_fixture_identity()` 会改写的 `FIX` 全局。`evals/run_evals.py` 是**同进程**顺序跑模块，
#: 不还原就会把本模块的夹具身份泄漏给后面的模块。
_PATCHED_FIX_GLOBALS = (
    "CONTRACT_VERSION", "CONTRACT_FINGERPRINT", "SOURCE_POLICY_VERSION",
    "DEPENDENCY_VERSIONS", "DEPENDENCY_FINGERPRINT", "COMPANY_ID",
    "REPORT_AS_OF", "DOCUMENT_ID", "DOCUMENT_VERSION", "EVIDENCE_SET_VERSION")


class ReplayExhausted(PW.PackWriterError):
    """留存返回用尽：离线重放不得发起新调用。"""


class ReplayBatchMismatch(RuntimeError):
    """收到的那次请求，与手上这份留存返回**不是同一批**。

    刻意**不是** `PackWriterError`：写入侧把 `PackWriterError` 读成「本束被拒」并重问一次
    （`_write_section` 的 `except PackWriterError` → 通用重试），一条夹具缺陷会被稀释成一条
    业务拒因、甚至被下一次请求掩盖。喂错字节是**重放夹具的缺陷**，必须立刻成为首个阻断。
    """


def _expectation_of(call_id: str, *, order: int) -> dict:
    """一份留存返回**当时回答的是哪一批**——逐字取自那一份请求自己的 payload。

    四条坐标都是那一份请求 payload 里已有的字段，不另立口径：`label` / `batch_id` / 本批
    `aspect_ids`（`batch_id` 由 `_make_batch` 从 index+total+aspect_ids+shrink 派生，因此它
    本身就是范围的身份）。`order` 是本模块声明的**重放序号**，用来判「请求顺序有没有倒退」。
    """
    batch = dict(_payload_of(call_id).get("batch") or {})
    if not batch:
        raise AssertionError(
            f"{call_id} 的请求 repayload 里没有 batch 块：无法核对重放请求与留存返回的对应关系")
    return {"expected_batch_label": batch.get("label"),
            "expected_batch_id": batch.get("batch_id"),
            "expected_aspect_ids": list(batch.get("aspect_ids") or ()),
            "expected_order": order}


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _read_call(call_id: str) -> dict:
    return json.loads((LLM_LOGS / f"{call_id}.jsonl").read_text(encoding="utf-8"))


def _payload_of(call_id: str) -> dict:
    """真实请求 payload。它是 `json.dumps` 的产物，末尾可能带批注，故用 `raw_decode`。"""
    row = _read_call(call_id)
    payload, _end = json.JSONDecoder(strict=False).raw_decode(row["messages"][0]["content"])
    return payload


def _anchors_present() -> tuple[bool, str]:
    """数据依赖：真实日志 + r7b 产物 + 两个库。缺任何一项都不猜、不静默绿。"""
    missing: list[str] = []
    for call_id in list(COMPANY_CALLS.values()) + [FIN_CALL]:
        if not (LLM_LOGS / f"{call_id}.jsonl").exists():
            missing.append(f"logs/llm/{call_id}.jsonl")
    for name in ("material_pack.json", "failure_diagnostics.json", "section_drafts.json",
                 "section_narratives.json", "proposal_set_rejections.json",
                 "follow_up_needs.json", "source_manifest.json"):
        if not (R7B / name).exists():
            missing.append(f"{R7B.name}/{name}")
    for db in ("data/evidence.db", "data/financial_v2.db"):
        if not (ROOT / db).exists():
            missing.append(db)
    return (not missing), "；".join(missing)


def _current_dependency_identity() -> tuple[dict, str]:
    """**当前工作区**的依赖身份：18 键向量 + 它的指纹。每次现算，不缓存、不硬贴。"""
    dv = TS.build_current_dependency_versions(contract_version="v2",
                                              source_policy_version=REAL_SP)
    return dv, TS.compute_dependency_fingerprint(REAL_CONTRACT_FP, REAL_SP, dv)


def _r7b_dependency_vector(current: dict) -> dict:
    """把**当前**向量按 `R7B_IDENTITY_DELTA` 还原成 r7b 那一轮记录的向量。

    只还原 `R7B_IDENTITY_DELTA` 里**点名**的键：没点名的键若也不等，下面的指纹对账会当场
    红（还原值复算不出 `REAL_DEP`），不会悄悄蒙混过去。
    """
    historical = dict(current)
    for key, value in R7B_IDENTITY_DELTA.items():
        if key not in historical:
            raise AssertionError(
                f"声明的历史差异键 {key!r} 不在当前依赖向量里（现有键 {sorted(current)}）："
                "向量的键集变了，`R7B_IDENTITY_DELTA` 必须先按现场重写，不得沿用")
        historical[key] = value
    return historical


def dependency_identity_declaration() -> dict:
    """**两组**身份并列：历史（记录、**不可执行**）与当前（本次重放实际执行的代码身份）。

    两组**不得合并**成一个「r7b 身份」——那正是这一批要收掉的错。判据：
      * `historical_fingerprint_matches_record`：按声明的差异还原出来的向量，复算出的指纹
        是否逐字等于 `REAL_DEP`。为假即「历史身份与当前向量的差异不止声明的那几个键」。
      * `delta_keys`：两组向量逐键相减后的**全部**差异键（应与 `R7B_IDENTITY_DELTA` 的键集相等）。
      * `historical_executable`：恒为假，理由见 `R7B_IDENTITY_UNEXECUTABLE_REASON`。
    """
    current_dv, current_fp = _current_dependency_identity()
    historical_dv = _r7b_dependency_vector(current_dv)
    historical_fp = TS.compute_dependency_fingerprint(REAL_CONTRACT_FP, REAL_SP, historical_dv)
    delta_keys = sorted(k for k in current_dv if current_dv[k] != historical_dv[k])
    return {
        "historical": {
            "label": "r7b（记录身份；可声明、可对账，**不可执行**）",
            "contract_fingerprint": REAL_CONTRACT_FP,
            "source_policy_version": REAL_SP,
            "dependency_fingerprint": historical_fp,
            "dependency_versions": historical_dv,
            "executable": False,
            "unexecutable_reason": R7B_IDENTITY_UNEXECUTABLE_REASON,
        },
        "current": {
            "label": "当前工作区（本次离线重放**实际执行**的代码身份）",
            "contract_fingerprint": REAL_CONTRACT_FP,
            "source_policy_version": REAL_SP,
            "dependency_fingerprint": current_fp,
            "dependency_versions": current_dv,
            "executable": True,
        },
        "declared_delta_keys": sorted(R7B_IDENTITY_DELTA),
        "delta_keys": delta_keys,
        "historical_fingerprint_matches_record": historical_fp == REAL_DEP,
        "delta_matches_declaration": delta_keys == sorted(R7B_IDENTITY_DELTA),
    }


def _patch_fixture_identity() -> dict:
    """把夹具身份对齐到 r7b 的**记录**身份，返还原始全局值的快照供 `finally` 逐字还原。

    为什么夹具仍贴 `REAL_DEP`（而不是当前指纹）：依赖身份在这里是**被重建的输入面**的一部分，
    与请求载荷里的 prompt / 模型串同类——`request_payload` 上写的是 `provider_default`、
    `pw-13`，本模块**不**把它们改写成今天的样子来换个合规身份（见文件头那段），依赖身份同理：
    改写它就是把记录抹平。历史复算钉子（§2.3 归因② 等）也全部建立在这个输入面之上。

    代价说清楚：历史依赖身份**不可执行**（没有 anp-6 的代码），所以这份夹具跑出的是
    **当前代码**的产物，`dependency_identity_declaration()["current"]` 才是本次执行的身份；
    本模块任何身份都**不得**被读成 r7b 的记录产物（r7b 公司节没有草稿）。

    进入重放前**仍然**要证明历史身份能被唯一还原：还原不出 `REAL_DEP`、或差异不止声明的键，
    当场红——那时「只是版本号前进了一个键」这个前提已经不成立，不许照旧重放。
    """
    snapshot = {name: getattr(FIX, name) for name in _PATCHED_FIX_GLOBALS}
    declaration = dependency_identity_declaration()
    if not declaration["historical_fingerprint_matches_record"]:
        raise AssertionError(
            "r7b 记录的依赖身份无法按声明的差异还原："
            f"按 {R7B_IDENTITY_DELTA!r} 还原得到 "
            f"{declaration['historical']['dependency_fingerprint']!r}，记录 {REAL_DEP!r}"
            "——历史身份与当前向量的差异**不止**声明的那几个键，先把这个差异查清再谈重放")
    if not declaration["delta_matches_declaration"]:
        raise AssertionError(
            f"两组依赖向量的实际差异键 {declaration['delta_keys']!r} "
            f"与声明的 {declaration['declared_delta_keys']!r} 不等："
            "差异面变了，`R7B_IDENTITY_DELTA` 必须按现场重写后再重放")
    historical_dv = declaration["historical"]["dependency_versions"]
    FIX.CONTRACT_VERSION = "v2"
    FIX.CONTRACT_FINGERPRINT = REAL_CONTRACT_FP
    FIX.SOURCE_POLICY_VERSION = REAL_SP
    FIX.DEPENDENCY_VERSIONS = historical_dv
    FIX.DEPENDENCY_FINGERPRINT = REAL_DEP
    FIX.COMPANY_ID = COMPANY
    FIX.REPORT_AS_OF = REPORT_AS_OF
    FIX.DOCUMENT_ID = DOCUMENT_ID
    FIX.DOCUMENT_VERSION = DOCUMENT_VERSION
    FIX.EVIDENCE_SET_VERSION = REAL_EVIDENCE_SET_VERSION
    return snapshot


def _restore_fixture_identity(snapshot: dict) -> None:
    for name, value in snapshot.items():
        setattr(FIX, name, value)


# ---------------------------------------------------------------------------
# 公司半场：r7b 留存的 Pack / 材料 / 三次原始返回
# ---------------------------------------------------------------------------

def _build_task_and_specs(profile, contract, fd_aspects, requestable):
    """冻结 Contract + 冻结 DemoScope 选择 → 真实 SectionTask 与 aspect 规格。

    重建的是**请求面**：`task.title/purpose/topic_ids` 与 r7b 记录的第 1 次请求 payload
    逐项相同（§1 断言），因此它不是「另造一份 Contract」。
    """
    section = next(s for s in contract.sections if s.section_id == "company")
    selected = set(profile.selected_topic_ids)
    topics = [t for t in section.topics if t.topic_id in selected]
    questions, specs, topic_ids = [], {}, []
    for topic in topics:
        topic_ids.append(topic.topic_id)
        for q in topic.questions:
            questions.append(PS.PlannedQuestion(
                question_id=q.question_id, question=q.question, priority=q.priority,
                topic_id=topic.topic_id,
                required_aspects=tuple(a.aspect_id for a in q.aspects),
                evidence_requirements=DS._planned_evidence_requirements(
                    q, contract.raw["evidence_requirements"]),
                calculation_requirements=(), analysis_requirements=(),
                missing_policy=q.missing_policy,
                blocking_policy=tuple(q.blocking_policy),
                impact_scope=DS._union_impact_scope(q)))
            for a in q.aspects:
                specs[a.aspect_id] = FIX._AspectReq(
                    aspect_id=a.aspect_id, topic_id=topic.topic_id,
                    question_id=q.question_id, impact_scope=tuple(a.impact_scope),
                    blocking_policy=tuple(a.blocking_policy), display_tier=a.display_tier,
                    content_role=a.content_role, kind=a.kind, time_scope=a.time_scope,
                    complete_set_rule=a.complete_set_rule, requirement_text=a.requirement_text)
    task = PS.SectionTask(
        task_id=COMPANY_TASK_ID, plan_id="dplan_not_recorded", section_id="company",
        title=section.title, purpose=section.purpose,
        research_policy=section.research_policy, topic_ids=tuple(topic_ids),
        questions=tuple(questions), output_requirements=(), evaluation_rule_ids=(),
        allowed_capabilities=tuple(section.allowed_capabilities),
        blocking_rules=DS._blocking_rules([a for t in topics for q in t.questions
                                           for a in q.aspects]))
    if set(specs) != set(fd_aspects) or set(specs) != set(requestable):
        raise AssertionError(
            "重建出的 aspect 规格面与 r7b 记录不符："
            f"contract-only={sorted(set(specs) - set(fd_aspects))[:5]} "
            f"record-only={sorted(set(fd_aspects) - set(specs))[:5]}")
    return task, specs


def _periodless_facts(topic_id, fd_aspects, entries, patched):
    """把 r7b 因期间不可核验而被拒的事实按**记录下来的身份**放回 Pack。

    实文**未**留存，因此文本是显式占位串——它必然再次被期间门拒绝。本模块不据此断言任何内容，
    只让「这些事实存在过、被期间门点名」这一形状在重放里成立。
    """
    by_fact: dict[str, set[str]] = {}
    for aspect_id, row in sorted(fd_aspects.items()):
        if row["topic_id"] != topic_id:
            continue
        for item in ((row.get("rejections") or {}).get("period") or []):
            by_fact.setdefault(item["fact_id"], set()).add(aspect_id)
    mats = sorted((e for e in entries if e["topic_id"] == topic_id),
                  key=lambda e: e["material_id"])
    out = []
    for fact_id in sorted(by_fact):
        member = mats[0]
        raw_id = str(patched[member["material_id"]][1]["source_identity"]).split(":", 1)[1]
        ref = TS.CitationRef(ref_type="evidence", evidence_id=raw_id,
                             page_number=member["locator"]["page"])
        out.append(FIX._Fact(
            fact_id=fact_id,
            text=f"离线重放占位文本：{fact_id} 对应的事实原文未被 r7b 产物留存。",
            aspect_ids=tuple(sorted(by_fact[fact_id])), citation_refs=(ref,), period=""))
    return tuple(out)


def _r7b_source_set() -> TS.DocumentSourceSet:
    """r7b 那一轮的**有序源集**，逐字派生自记录（不在本模块另判角色）。

    为什么必须把真实源集带进夹具：`srsc-1` 的来源角色**唯一**取自 Pack 自己的
    `source_set`（`SRS.document_roles`）。夹具若给 Pack 挂一份自造的单文档来源集，角色台账
    就只有那一份文档，而本节材料实际来自三份——判据查不到角色只能 fail-closed 抛错。那是
    **夹具失真**，不是判据故障：同一份材料在真实 run 里是有角色的。
    """
    document = _read_json(R7B_SOURCE_MANIFEST)
    roles_by_axes = {(s["document_id"], s["document_version"],
                      s.get("evidence_set_version")): s["source_role"]
                     for s in document["selection"]}
    members = []
    for raw in document["document_keys"]:
        key = SM.SourceDocumentKey.from_dict(raw)
        role = roles_by_axes.get((key.document_id, key.document_version,
                                  key.evidence_set_version))
        if not role:
            raise AssertionError(
                f"r7b 来源清单里 {key.document_id}@{key.document_version} 没有 source_role："
                "不得默认它参与检索（fail-closed）")
        members.append((key, role))
    return TS.DocumentSourceSet(members=tuple(members))


def _build_company_case() -> dict:
    """r7b company 的完整写作前输入面（Pack set / 权威 / 材料上下文）。"""
    requests = {n: _payload_of(call) for n, call in COMPANY_CALLS.items()}
    req1 = requests[1]
    pack_doc = _read_json(R7B / "material_pack.json")
    fd = _read_json(R7B / "failure_diagnostics.json")
    entries = pack_doc["sections"]["company"]["entries"]
    by_id = {e["material_id"]: e for e in entries}
    fd_aspects = {a["aspect_id"]: a for a in fd["sections"]["company"]["aspects"]}
    requestable = {a["aspect_id"]: a for a in req1["requestable_aspects"]}

    profile = DS.load_demo_scope_profile(str(ROOT / PROFILE_PATH))
    contract = CV2.load_contract_v2(str(ROOT / profile.contract_asset))
    if profile.contract_fingerprint != REAL_CONTRACT_FP:
        raise AssertionError(
            f"冻结 DemoScope profile 绑定的 Contract 指纹 {profile.contract_fingerprint!r} "
            f"不是 r7b 的 {REAL_CONTRACT_FP!r}")
    task, specs = _build_task_and_specs(profile, contract, fd_aspects, requestable)

    con = sqlite3.connect(ROOT / "data" / "evidence.db")
    try:
        cur = con.cursor()
        patched = {}
        for mrow in req1["materials"]:
            entry = by_id[mrow["material_id"]]
            cur.execute("select content_hash from evidence_blocks where evidence_id=?",
                        (str(mrow["source_identity"]).split(":", 1)[1],))
            srow = cur.fetchone()
            if not srow:
                raise AssertionError(f"Evidence 库里没有 {mrow['source_identity']!r}")
            patched[mrow["material_id"]] = (entry, mrow, srow[0])
    finally:
        con.close()

    anchors = {"content_fingerprint": 0, "locator_ref": 0, "source_identity": 0,
               "payload_hash": 0}

    def factory(material_id, source_identity, page=None):
        entry, mrow, _shash = patched[str(material_id)]
        if str(source_identity) != mrow["source_identity"]:
            raise AssertionError(f"材料 {material_id!r} 的权威身份与记录不符")
        loc = entry["locator"]
        locator = TS.EvidenceLocator(
            document_id=loc["document_id"], document_version=loc["document_version"],
            section_path=loc["section_path"], page=loc["page"],
            table_title=loc.get("table_title"),
            block_range=tuple(loc["block_range"]) if loc.get("block_range") else None,
            offset=loc.get("offset"))
        payload_hash = entry["content_hash"]
        payload_ref = TS.MaterialPayloadRef(
            object_type=entry["material_type"], authority_identity=str(source_identity),
            version="1", content_hash=payload_hash, locator=locator,
            created_dependency_fingerprint=REAL_DEP)
        mat = TS.ResearchMaterial(
            material_id=str(material_id), material_type=entry["material_type"],
            source_identity=str(source_identity), locator=locator, payload_ref=payload_ref,
            content_hash=payload_hash,
            authority_assessment=FIX._evidence_authority(
                str(source_identity).split(":", 1)[1], page=loc["page"]))
        anchors["content_fingerprint"] += (
            TS.material_content_fingerprint(mat) == mrow["content_fingerprint"])
        anchors["locator_ref"] += (MC.material_locator_ref(material=mat) == mrow["locator_ref"])
        anchors["source_identity"] += (
            TS.authority_source_identity(mat.authority_assessment) == mrow["source_identity"])
        anchors["payload_hash"] += (payload_hash == mrow["payload_hash"])
        return mat

    mrow_by_id = {m["material_id"]: m for m in req1["materials"]}
    # 生产链把 run 的有序源集挂到本节每一个 Pack 上（`topic_runtime.py` 的 `source_set=`）；
    # 派生结果与 r7b 记录的逐节 `source_set_fingerprint` 逐字相同（§1 断言）。
    source_set = _r7b_source_set()
    pack_specs, req_specs = [], []
    for topic_id in task.topic_ids:
        mats = tuple(FIX._Material(material_id=e["material_id"],
                                   source_identity=mrow_by_id[e["material_id"]]["source_identity"],
                                   page=e["locator"]["page"])
                     for e in entries if e["topic_id"] == topic_id)
        aspect_ids = sorted(a for a in requestable if specs[a].topic_id == topic_id)
        results = tuple(FIX._AspectResult(aspect_id=a, status=requestable[a]["status"])
                        for a in aspect_ids)
        req_specs.append(FIX._Req(topic_id=topic_id,
                                  aspects=tuple(specs[a] for a in aspect_ids)))
        pack_specs.append(FIX._Pack(
            topic_id=topic_id, materials=mats, aspect_results=results,
            facts=_periodless_facts(topic_id, fd_aspects, entries, patched),
            company_id=COMPANY, report_as_of=REPORT_AS_OF, contract_version="v2",
            contract_fingerprint=REAL_CONTRACT_FP, source_set=source_set))
    pack_set = FIX._pack_set(task, packs=tuple(pack_specs), requirements=tuple(req_specs),
                             material_factory=factory)
    authority = PW.TopicPackAuthorityInput.create(
        task, pack_set, company_id=COMPANY, report_as_of=REPORT_AS_OF,
        contract_version="v2", contract_fingerprint=REAL_CONTRACT_FP)

    # 精确材料清单：逐条与 r7b 记录的 `materials` 行对上身份面。
    pack_of_topic = {p.topic_id: p.pack_id for p in pack_set.packs}
    resolved = []
    for entry in entries:
        mrow = patched[entry["material_id"]][1]
        pack_id = pack_of_topic[entry["topic_id"]]
        resolved.append(MC.ResolvedWriterMaterial.create(
            member_ref=NS.manifest_member_ref(pack_id, entry["material_id"]),
            pack_id=pack_id, material_id=entry["material_id"], topic_id=entry["topic_id"],
            material_type=entry["material_type"],
            research_material_disposition_id=entry["disposition"]["disposition_id"],
            source_identity=mrow["source_identity"],
            provenance_identity=mrow["source_identity"],
            locator_ref=mrow["locator_ref"],
            payload_ref={
                "object_type": entry["material_type"],
                "authority_identity": mrow["source_identity"], "version": "1",
                "content_hash": mrow["payload_hash"], "locator": entry["locator"],
                "created_dependency_fingerprint": REAL_DEP},
            payload_hash=mrow["payload_hash"], content_hash=mrow["payload_hash"],
            material_content_fingerprint=mrow["content_fingerprint"],
            reading_view=mrow["text"], structured_view=mrow["structured"],
            content_qualification=mrow["content_qualification"]))
    resolved.sort(key=MC.material_sort_key)
    # **边界 2**：这里走的是生产链同一个入口 `MC.WriterMaterialContext.create`；跳过的只是
    # `resolve_writer_material_context` 里的 payload **字节**哈希复核（字节未被 r7b 留存）。
    context = MC.WriterMaterialContext.create(
        task_id=COMPANY_TASK_ID, section_id="company",
        pack_set_fingerprint=MC.pack_set_fingerprint(pack_set), materials=tuple(resolved))
    return {"task": task, "pack_set": pack_set, "authority": authority, "req1": req1,
            "requests": requests, "entries": entries, "patched": patched,
            "anchors": anchors, "specs": specs, "profile": profile, "resolved": resolved,
            "context": context}


class CompanyReplayClient:
    """**离线重放替身**：门前提案 = 留存的原始返回；组织器 = harness 的确定性替身。

    **逐次核对坐标，不按位置喂字节**：每一份留存返回都自带「它当时回答的是哪一批」
    （`expected_batch_label` / `expected_batch_id` / `expected_aspect_ids` / `expected_order`，
    见 `_expectation_of`）。收到请求时按这些坐标去配，而不是 `pop(0)`——位置喂法在两种真实
    情形下会把**另一批**的字节当成这一批的返回：批次被形状纠正**重问**过（同一批两次请求）、
    或某一批根本没被请求（序列错位）。那正是离线重放最该排除的错。

    核对不通过（标签相同但 id / 范围对不上，或请求序号倒退）抛 `ReplayBatchMismatch`；
    一个都配不上抛 `ReplayExhausted`（离线重放不得发起新调用）。批 4 的空束是夹具，
    `call_id` 逐字标出这一点。
    """

    def __init__(self, responses, organizer) -> None:
        self.responses = list(responses)
        self.organizer = organizer
        self.calls: list[dict] = []
        #: 已被取用的留存返回位置 + 上一次取用的 `expected_order`（判「请求顺序有没有倒退」）。
        self.served: list[int] = []
        self._last_order = None

    def _take(self, request_batch: dict):
        """按坐标配一份留存返回。配不上**立即报**，不顺着列表往后找一条能过的。"""
        label = request_batch.get("label")
        batch_id = request_batch.get("batch_id")
        aspects = tuple(str(a) for a in (request_batch.get("aspect_ids") or ()))
        for position, item in enumerate(self.responses):
            if position in self.served:
                continue
            want_label = item.get("expected_batch_label")
            if want_label is None:
                raise ReplayBatchMismatch(
                    f"留存返回 {item.get('call_id')!r} 没有声明它回答的是哪一批"
                    "（`expected_batch_label`）：离线重放必须逐次核对，不得按位置喂字节")
            if want_label != label:
                continue
            want_id = item.get("expected_batch_id")
            if want_id is not None and want_id != batch_id:
                raise ReplayBatchMismatch(
                    f"批次 {label} 的请求 id={batch_id!r}，而这一份留存返回 id={want_id!r}"
                    f"（call_id={item.get('call_id')!r}）：不是同一批的字节")
            want_aspects = item.get("expected_aspect_ids")
            if want_aspects is not None and tuple(str(a) for a in want_aspects) != aspects:
                raise ReplayBatchMismatch(
                    f"批次 {label} 的请求 aspect 范围 {list(aspects)}，这一份留存返回记录的范围 "
                    f"{list(want_aspects)}（call_id={item.get('call_id')!r}）：不是同一批的字节")
            order = item.get("expected_order")
            if order is None:
                raise ReplayBatchMismatch(
                    f"留存返回 {item.get('call_id')!r} 没有声明重放序号（`expected_order`）")
            if self._last_order is not None and order < self._last_order:
                raise ReplayBatchMismatch(
                    f"重放请求顺序倒退：批次 {label} 的序号 {order} 排在已答过的 "
                    f"{self._last_order} 之前")
            self.served.append(position)
            self._last_order = order
            return position, item
        raise ReplayExhausted(
            f"离线重放不得发起新调用：批次 {label}（id={batch_id!r}，"
            f"aspects={list(aspects)}）没有可以对应的留存返回；已发出 {len(self.calls)} 次，"
            "尚未用掉的留存返回 = "
            f"{[(i.get('expected_order'), i.get('expected_batch_label')) for p, i in enumerate(self.responses) if p not in self.served]}")

    def narrate(self, *, messages, system, prompt_version, model_policy):
        if prompt_version == NO.NARRATIVE_ORGANIZER_PROMPT_VERSION:
            self.calls.append({"kind": "organizer_stub", "prompt_version": prompt_version})
            call_id = f"offline-organizer-{len(self.calls)}"
            return self.organizer(messages, prompt_version, call_id)
        payload, _end = json.JSONDecoder(strict=False).raw_decode(messages[0]["content"])
        request_batch = dict(payload.get("batch") or {})
        _position, item = self._take(request_batch)
        self.calls.append({"kind": item["kind"], "call_id": item["call_id"],
                           "batch_label": request_batch.get("label"),
                           "batch_index": request_batch.get("index"),
                           "batch_total": request_batch.get("total"),
                           "batch_id": request_batch.get("batch_id"),
                           # 该份返回的批次 id 有没有被逐字核对过。批 4 那种**从未被调用**过
                           # 的夹具没有自己的请求 payload，这一格如实记 False，不编一个 id。
                           "batch_id_checked": item.get("expected_batch_id") is not None,
                           "aspect_ids": list(request_batch.get("aspect_ids") or ()),
                           "aspects_checked": item.get("expected_aspect_ids") is not None,
                           "prompt_version": prompt_version,
                           "model_policy": model_policy})
        return PW.NarrationResult(
            text=item["text"], call_id=item["call_id"], model=item["model"],
            prompt_version=prompt_version, status="ok",
            input_tokens=item.get("input_tokens"), output_tokens=item.get("output_tokens"),
            latency_ms=item.get("latency_ms") or 0, finish_reason=item.get("finish_reason"))


def _company_responses() -> list[dict]:
    out = []
    for n in (1, 2, 3):
        row = _read_call(COMPANY_CALLS[n])
        out.append({"kind": f"recorded_call_{n}", "text": row["completion"],
                    "call_id": COMPANY_CALLS[n].split("__")[-1], "model": row["model"],
                    "input_tokens": row["input_tokens"], "output_tokens": row["output_tokens"],
                    "latency_ms": row["latency_ms"], "finish_reason": row["finish_reason"],
                    **_expectation_of(COMPANY_CALLS[n], order=n)})
    # 夹具的 `model` 必须等于 model policy 解析出的模型名（`NarrationResult` 的**结构身份**，
    # 用于核对「记录模型 = 实际调用模型」）；夹具身份由 `call_id` 与空内容承担——它不是模型返回。
    # **批 4 从未被调用**，因此没有属于它自己的请求 payload：`batch_id` 与 aspect 范围这两格
    # **不可从任何记录取得**，如实声明为 `None`（调用台账里记 `batch_id_checked=False`），
    # 不编一个 id 出来。可核对的坐标是标签与序号。
    out.append({"kind": "fixture", "text": BATCH4_FIXTURE,
                "call_id": BATCH4_FIXTURE_LABEL,
                "model": PW.resolve_model_policy(PW.MODEL_POLICY_PROVIDER_DEFAULT),
                "expected_batch_label": "4/4", "expected_batch_id": None,
                "expected_aspect_ids": None, "expected_order": 4})
    return out


def _company_section_input(case: dict, projection, spec, pprofile):
    return CW.BackboneWriterSectionInput(
        task=case["task"], authority=case["authority"], projection=projection,
        writing_spec=spec, presentation_profile=pprofile,
        dependency_fingerprint=REAL_DEP, requirements=(), run_context=None)


def _drive_company(case: dict, *, proposal_wire: str, replay_source: str) -> dict:
    """公司半场：`PW.write_section` → `RE.evaluate_claim_chain` → `CW._finalize_section`。

    三段都是生产实现，一处不改；生产的第一轮重写就是 `rewrite_round=0`（`_writer_pass` 的
    首次调用），本重放与之同源。

    **两条声明必须由调用方显式给出**（关键字必填，没有默认值）：`proposal_wire` 说「喂进去的
    这些字节是什么形状」，`replay_source` 说「它们从哪来」。给出默认值就等于让另一轮的字节
    隐式继承本模块的声明——那会把「r8 的字节按 r7b 的历史线读」这种错直接读绿（历史线关掉的
    正是当前线的形状判据）。产出的 `policy` 随结果返回，调用方据此断言实际生效的就是它给的那
    两个值。
    """
    from evaluation import run_m930_3_acceptance as ACC
    profile = case["profile"]
    spec = WS.load_writing_spec(str(ROOT / profile.writing_spec_asset))
    projection = PW.ContractProjection.create(
        spec, section_id="company", contract_version="v2",
        contract_fingerprint=REAL_CONTRACT_FP)
    policy = PW.WriterPolicy(policy_version=PW.PACK_WRITER_POLICY_VERSION,
                             prompt_version=PW.NARRATION_PROMPT_VERSION,
                             model_policy=PW.MODEL_POLICY_PROVIDER_DEFAULT,
                             renderer_version=PW.WRITER_RENDERER_VERSION,
                             rules_version="p4-fin-rules-v1", max_llm_retries=1,
                             proposal_wire=proposal_wire, replay_source=replay_source)
    if (policy.proposal_wire != proposal_wire
            or policy.replay_source != replay_source):
        raise AssertionError(
            f"WriterPolicy 没有按调用方声明的重放身份生效："
            f"要求 wire={proposal_wire!r} / source={replay_source!r}，实得 "
            f"wire={policy.proposal_wire!r} / source={policy.replay_source!r}")
    pprofile = PP.load_presentation_profile(str(ROOT / profile.presentation_profile_asset))
    # 留存返回取自 `case`（若给了）：同一份驱动因此能被**另一轮**留存的字节复用
    # （r8 重放模块 `evals/test_m930_3_r8_offline_replay.py`）；本模块的 case 没有这个键，
    # 走的仍是 r7b 那三次返回，逐字不变。
    client = CompanyReplayClient(case.get("responses") or _company_responses(),
                                 ACC.OfflineNarrationClient()._organizer)
    outcome = PW.write_section(
        case["task"], case["authority"], projection=projection, writing_spec=spec,
        presentation_profile=pprofile, llm_client=client, policy=policy,
        dependency_fingerprint=REAL_DEP,
        created_at="2026-09-26T03:46:21Z（离线重放，非新的真实验收）",
        material_context=case["context"])
    chain = RE.evaluate_claim_chain(
        outcome.draft, PW.scan_authority(case["authority"], case["task"]),
        manifest=outcome.draft.material_manifest, material_context=case["context"],
        llm_client=ACC.OfflineEntailmentClient())
    section_input = _company_section_input(case, projection, spec, pprofile)
    output = CW._finalize_section(
        section_input=section_input, authority=case["authority"], outcome=outcome,
        chain=chain, rewrite_round=0, follow_up_run_refs=(),
        evaluated_at="2026-09-26T03:46:21Z", llm_client=client, llm_calls_before=0,
        allow_llm_evaluator=False, allow_targeted_rework=False,
        final_sentence_llm_client=ACC.OfflineFinalSentenceClient())
    return {"client": client, "outcome": outcome, "chain": chain, "output": output,
            "projection": projection, "policy": policy}


# ---------------------------------------------------------------------------
# 财务半场：r7b 留存的财务权威 + 那一次原始返回（含已声明的别名重映射）
# ---------------------------------------------------------------------------

def _declared_company_names(db: Path, company_id: str) -> list[str]:
    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT DISTINCT declared_company_name FROM financial_source_document "
            "WHERE company_id = ? AND subject_match_status = 'matched'",
            (company_id,)).fetchall()
    finally:
        con.close()
    return [str(r[0]).strip() for r in rows]


def _demo_projection(profile, contract, company_name: str):
    business = PS.ReportJobInput(
        job_id="job_m930_3_acceptance_0001", company_id=COMPANY,
        company_name=company_name, credit_type="general",
        report_as_of=REPORT_AS_OF, contract_version="v2")
    source_inputs = {
        "case_input_id": "case_m930_3_acceptance_0001",
        "document_id": DOCUMENT_ID, "document_version": DOCUMENT_VERSION,
        "raw_pdf_sha256": REAL_RAW_PDF_SHA256,
        "current_evidence_set_version": REAL_EVIDENCE_SET_VERSION,
        "substrate_dependency_versions": {
            k: f"{k}-v1" for k in DS.SUBSTRATE_DEPENDENCY_KEYS},
        "external_policy_snapshot_id": None,
        "budget_policy_id": profile.budget_policy_id,
        "budget_policy_version": profile.budget_policy_version,
        "model_policy_id": "demo_model_policy_v1", "code_fingerprint": "1" * 64,
    }
    manifest_scope = DS.build_scope_input_manifest(profile, business, source_inputs)
    return manifest_scope, DS.project_contract_v2_scope(contract, profile, manifest_scope)


def _build_financial_case() -> dict:
    """r7b financial 的写作前输入面：维度 → demo 投影 → Pack artifact → 权威 → 扫描。"""
    from evaluation import run_m930_3_acceptance as ACC
    profile = DS.load_demo_scope_profile(str(ROOT / PROFILE_PATH))
    contract = CV2.load_contract_v2(str(ROOT / profile.contract_asset))
    db = ROOT / "data" / "financial_v2.db"
    names = _declared_company_names(db, COMPANY)
    if not names:
        raise AssertionError(f"financial_v2.db 里没有 {COMPANY!r} 的 matched 声明主体名")
    dims = ACC._financial_dims(db, ACC.DeclaredReportInput(
        subject_id=COMPANY, report_as_of=REPORT_AS_OF, subject_name=names[0]))
    # 真实 runner 用的是**财务维度里的公司名**（不是另一个字面量）：两处不同会改变 scope
    # 指纹 → demo 投影 id → Pack 身份，因此必须同源。
    manifest_scope, projection = _demo_projection(profile, contract, str(dims["company_name"]))
    tasks = {t.section_id: t for t in projection.report_plan.section_tasks}
    task = dataclasses.replace(tasks["financial"], task_id=FIN_TASK_ID)
    requirements = {r.topic_id: r for r in projection.requirements}

    fin_phase = FW.run_backbone_financial_phase(
        task, company_id=dims["company_id"], projection_id=projection.projection_id,
        contract_version="v2", contract_fingerprint=REAL_CONTRACT_FP,
        company_name=str(dims["company_name"]), fin_db=str(db),
        scope=dims["scope"], currency=dims["currency"], purpose=dims["purpose"],
        as_of_date=dims["as_of_date"], snapshot_id=dims["snapshot_id"])
    artifact = fin_phase.artifact
    authority = PW.FinancialAuthorityInput.create(
        task, artifact, note_gap=fin_phase.evidence_note_gap,
        fact_topic_map=ACC._financial_fact_topic_map(artifact, task),
        company_name=str(dims["company_name"]), company_id=COMPANY,
        report_as_of=REPORT_AS_OF, contract_version="v2",
        contract_fingerprint=REAL_CONTRACT_FP)
    spec = WS.load_writing_spec(
        str(ROOT / "templates" / "writing_specs" / "credit_report_v1.yaml"))
    return {
        "profile": profile, "contract": contract, "manifest_scope": manifest_scope,
        "demo_projection": projection, "task": task,
        "requirements": tuple(requirements[t] for t in task.topic_ids),
        "spec": spec,
        "writer_projection": PW.ContractProjection.create(
            spec, section_id="financial", contract_version="v2",
            contract_fingerprint=REAL_CONTRACT_FP),
        "presentation_profile": PP.load_presentation_profile(
            str(ROOT / profile.presentation_profile_asset)),
        "fin_phase": fin_phase, "artifact": artifact, "authority": authority,
        "dims": dims, "scan": PW.scan_financial(authority, task),
    }


def _derived_text(fact) -> str:
    """一条权威事实的候选/Claim 文本：权威表面去掉末尾句读（写入侧的唯一口径）。"""
    return str(fact.text).rstrip(NS.SENTENCE_TERMINATORS).strip()


def _remapped_bundle(scan) -> tuple[dict, dict]:
    """r7b 真实返回 → 本轮请求的提案束（`f_k → f_{k+1}` + 一条派生事实候选）。

    `PW.scan_financial` 按 `fact_id` 升序发放短别名 `f1..fN`。新增的派生事实
    `derived_SOLV_DEBT_RATIO_DELTA_PP_…` 的 `fact_id` 字典序**小于**全部 `metric_*`，因此它落在
    `f1`，r7b 的 24 条 `metric_*` 整体后移为 `f2..f25`。若把原始返回**逐字**喂进去，`f1` 会被
    静默重绑到派生事实上——24 条候选全部指错事实。映射依据逐条断言，不是猜测。
    """
    row = _read_call(FIN_CALL)
    recorded = json.loads(row["completion"])
    facts = list(scan.facts)
    audit = {"recorded_candidates": len(recorded["claim_candidates"]),
             "recorded_units": len(recorded["narrative_draft_units"]),
             "recorded_follow_up_needs": len(recorded["follow_up_needs"]),
             "remap": "f{k} -> f{k+1}", "byte_equal": 0, "punctuation_only": 0,
             "surface_mismatch": []}
    candidates = [{"candidate_key": "d1",
                   "claim_text": _derived_text(facts[0]),
                   "support": [{"ref": "f1", "support_role": "primary"}]}]
    for index, item in enumerate(recorded["claim_candidates"], start=1):
        want = str(item["claim_text"])
        got = _derived_text(facts[index])
        if want == got:
            audit["byte_equal"] += 1
        elif want.replace("，代理口径", "。代理口径") == got:
            # 4 条代理口径候选：那一轮模型把限定语前的句号写成逗号。句本体与限定语逐字相同，
            # 因此重映射无双解；文本按原样保留（不改写真实返回）。
            audit["punctuation_only"] += 1
        else:
            audit["surface_mismatch"].append({"index": index, "recorded": want,
                                              "surface": got})
        candidates.append({"candidate_key": str(item["candidate_key"]),
                           "claim_text": want,
                           "support": [{"ref": f"f{index + 1}", "support_role": "primary"}]})
    return {"claim_candidates": candidates,
            "narrative_draft_units": list(recorded["narrative_draft_units"]),
            "follow_up_needs": list(recorded["follow_up_needs"])}, audit


class FinancialReplayClient:
    """**离线重放替身**：门前提案 = 重映射后的真实返回；组织器 = harness 的确定性替身。

    `call_id` 带 `replay-` 前缀，每次调用的 `model` 报 `PW.resolve_model_policy(model_policy)`
    （与既有替身同一约定），因此产物里任何时候都分得清哪一次是留存的真实返回、哪一次是替身。
    """

    def __init__(self, *, bundle: dict) -> None:
        from evaluation import run_m930_3_acceptance as ACC
        self._bundle = bundle
        self._organizer = ACC.OfflineNarrationClient()._organizer
        self.calls: list[dict] = []

    def narrate(self, *, messages, system, prompt_version, model_policy):
        from llm import budget as LB
        self.calls.append({"prompt_version": prompt_version, "model_policy": model_policy})
        call_id = f"replay-{len(self.calls)}"
        ticket = LB.reserve_attempt(call_id=call_id, prompt_version=prompt_version,
                                    model=None, max_tokens=None)
        try:
            if prompt_version == NO.NARRATIVE_ORGANIZER_PROMPT_VERSION:
                # r7b 那一轮**没有**发过组织调用，因此不存在「留存的组织返回」：只能用替身的
                # 确定性组织。这一条在调用序列里逐次标出。
                result = self._organizer(messages, prompt_version, call_id)
            elif prompt_version == PW.NARRATION_PROMPT_VERSION:
                result = PW.NarrationResult(
                    text=json.dumps(self._bundle, ensure_ascii=False), call_id=call_id,
                    model=PW.resolve_model_policy(model_policy),
                    prompt_version=prompt_version, status="ok", input_tokens=0,
                    output_tokens=0, latency_ms=0, finish_reason="end_turn")
            else:
                raise AssertionError(f"重放替身不认识 prompt 版本 {prompt_version!r}")
        except BaseException as exc:  # noqa: BLE001 — 失败的尝试照样占一次额度
            LB.settle_attempt(ticket, status=LB.STATUS_ERROR,
                              error=f"{type(exc).__name__}: {exc}")
            raise
        LB.settle_attempt(ticket, status=LB.STATUS_OK)
        return result


def _drive_financial(case: dict) -> dict:
    """财务半场：走 `CW.run_backbone_writer_phase`（财务的**唯一**入口，没有第二条链）。"""
    from evaluation import run_m930_3_acceptance as ACC
    bundle, audit = _remapped_bundle(case["scan"])
    client = FinancialReplayClient(bundle=bundle)
    section_input = CW.BackboneWriterSectionInput(
        task=case["task"], authority=case["authority"],
        projection=case["writer_projection"], writing_spec=case["spec"],
        presentation_profile=case["presentation_profile"],
        dependency_fingerprint=REAL_DEP, requirements=case["requirements"],
        run_context=None)
    phase = CW.run_backbone_writer_phase(
        (section_input,), llm_client=client,
        entailment_llm_client=ACC.OfflineEntailmentClient(),
        # 最终句语义门 B 的确定性替身：它只读请求面、逐条认领已定位的原子，不做语义判断。
        # 本重放因此验证的是「B 门在场时这条链仍能贯通」，**不是**「语义门已覆盖」。
        final_sentence_llm_client=ACC.OfflineFinalSentenceClient(),
        policy=PW.WriterPolicy(model_policy=PW.MODEL_POLICY_PROVIDER_DEFAULT,
                               max_llm_retries=1,
                               proposal_wire=REPLAY_WIRE, replay_source=REPLAY_SOURCE),
        store=None, section_store=None,
        created_at="2026-09-26T03:46:21Z", evaluated_at="2026-09-26T03:46:21Z")
    return {"client": client, "audit": audit, "output": phase.sections[0]}


# ---------------------------------------------------------------------------
# 读回打印（人读结果，不是判据）
# ---------------------------------------------------------------------------

#: 疑似重复（近义重复）的**提示**口径：同一主题内、两条 Claim 文本互不逐字包含，且剥掉标点与
#: 空白之后 `difflib` 相似度 ≥ 本阈值。这是**提示，不是判据**——判定「两条 Claim 说的是不是
#: 同一件事」是组织器（模型）的职责（`norg-4` 的输入面把这条写成它的职责），本模块既不替它
#: 下这个结论，也不因此删掉任何一条 Claim：只把候选摆给读的人。
_NEAR_DUP_RATIO = 0.8


def _near_duplicate_pairs(claims) -> list[tuple[str, str, float]]:
    """同主题内疑似近义重复的 Claim 对（**提示**，非判据；逐字包含的那一类不在这里）。"""
    import difflib

    from sections import narrative_schema as NS

    def key(text) -> str:
        return "".join(ch for ch in str(text or "")
                       if ch not in NS.JOIN_RESIDUE_PUNCTUATION and not ch.isspace())

    by_topic: dict[str, list] = {}
    for claim in claims:
        by_topic.setdefault(str(claim.topic_id), []).append(claim)
    pairs: list[tuple[str, str, float]] = []
    for topic in sorted(by_topic):
        group = by_topic[topic]
        keys = [key(c.text) for c in group]
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                left, right = keys[i], keys[j]
                if not left or not right or left in right or right in left:
                    continue
                ratio = difflib.SequenceMatcher(None, left, right).ratio()
                if ratio >= _NEAR_DUP_RATIO:
                    pairs.append((group[i].claim_id, group[j].claim_id, ratio))
    return pairs


def _citation_readback(cite) -> str:
    if cite.ref_type == "evidence":
        page = "" if cite.page_number is None else f" p.{cite.page_number}"
        return f"evidence:{cite.evidence_id}{page}"
    if cite.ref_type == "structured":
        return (f"structured:{cite.snapshot_id}/{cite.formula_id}"
                f"@{cite.formula_version} {cite.period}")
    return f"{cite.ref_type}:{cite.source_snapshot_id}"


def _print_company(replay, case) -> None:
    outcome, chain, output = replay["outcome"], replay["chain"], replay["output"]
    draft = outcome.draft
    print("=" * 78)
    print("【离线重放 · 非新的真实验收】公司节：原束 → 决定 → 新修订 → 绑定/蕴含 → 正文")
    print("=" * 78)
    print("留存真实返回:", json.dumps(COMPANY_CALLS, ensure_ascii=False))
    print("批 4:", BATCH4_FIXTURE_LABEL, "（测试夹具：r7b 从未调用批 4，它不是模型返回）")
    print("调用序列:", json.dumps(
        [{"kind": c["kind"], "call_id": c.get("call_id"), "batch": c.get("batch_label"),
          "prompt_version": c["prompt_version"]} for c in replay["client"].calls],
        ensure_ascii=False))
    print("材料身份锚点（逐条与 r7b 记录对账）:", json.dumps(case["anchors"], ensure_ascii=False))
    print()
    print(f"提案 {len(draft.claim_candidates)} 条；草稿单元 {len(draft.narrative_draft_units)} 个；"
          f"支撑提案 {len(draft.proposed_support_refs)} 条；"
          f"整束拒绝审计 {len(outcome.rejections)} 条")
    print(f"draft_id={draft.draft_id} revision={draft.draft_revision} "
          f"writer_attempt={draft.writer_attempt}")
    for rec in outcome.rejections:
        print(f"   整束拒绝 [{rec.rejection_kind}] attempt={rec.attempt} "
              f"候选={len(rec.candidate_ids)} 逐候选审计={len(rec.candidate_audit)}")
        if rec.carve_out is not None:
            decision = rec.carve_out.decision
            print(f"     {decision.version} 裁出 {decision.from_attempt} → {decision.to_attempt}"
                  f"（新修订 {decision.to_revision}）被排除 {len(decision.excluded)} 条，"
                  f"幸存 {len(decision.surviving_candidate_ids)} 条，"
                  f"新增模型调用 {rec.carve_out.model_calls_added}")
            for entry in decision.excluded:
                print(f"       排除 {entry.candidate_id} 表面={list(entry.surfaces)} "
                      f"原因={list(entry.reasons)} 文本={entry.claim_text[:60]}")
    print(f"聚合绑定决定 {len(chain.aggregate_decisions)}；"
          f"蕴含决定 {len(chain.entailment_decisions)}；"
          f"接受边 {len(chain.acceptance.accepted_bindings)}；"
          f"被拒 subject {len(chain.acceptance.rejected_subjects)}")
    print(f"门后 Claim {len(output.claims)}；final Narrative："
          f"段落 {len(output.narrative.paragraphs)} / 表 {len(output.narrative.tables)}")
    print(f"SectionResult status={output.result.status} id={output.result.section_result_id}")
    print(f"persistence={output.persistence}")
    print()
    claims = {c.claim_id: c for c in output.claims}
    bindings = {b.accepted_support_binding_id: b
                for b in chain.acceptance.accepted_bindings}
    print("-- final Narrative 正文（逐句可回查）--")
    for para in output.narrative.paragraphs:
        print(f"   段落 {para.paragraph_id} topics={list(para.topic_ids)} "
              f"句数={len(para.sentences)}")
        for sentence in para.sentences:
            print(f"     [{sentence.index}] ({sentence.sentence_kind}) {sentence.text}")
            for cid in sentence.claim_ids:
                claim = claims[cid]
                print(f"         ← claim {cid} [{claim.topic_id}/{claim.claim_type}] "
                      f"rev={claim.claim_candidate_revision}")
                print(f"           {claim.text}")
                for cite in claim.citation_refs:
                    print(f"           引用 {_citation_readback(cite)}")
                for bid in claim.accepted_binding_ids:
                    bound = bindings[bid]
                    print(f"           binding {bid} kind={bound.authority_kind} "
                          f"material={bound.material_id} "
                          f"locator={json.dumps(bound.locator_ref, ensure_ascii=False)[:150]}")
    print()
    print(f"-- 疑似近义重复（**提示非判据**，阈值 {_NEAR_DUP_RATIO}）：留着给组织器裁决，本模块不删 --")
    pairs = _near_duplicate_pairs(output.claims)
    if not pairs:
        print("   （无：同主题内没有互不包含却高度相似的 Claim 对）")
    for left, right, ratio in pairs:
        print(f"   相似度 {ratio:.3f}")
        print(f"     {left} | {claims[left].text}")
        print(f"     {right} | {claims[right].text}")
    print()
    print("-- 缺口（如实呈现）--")
    for item in output.result.unresolved:
        print(f"   {item.unresolved_id} [{item.state}/{item.reason_code}] "
              f"topic={item.topic_id} blocking={list(item.blocking_effects)}")
        print(f"      {item.detail[:220]}")


def _print_financial(replay, case) -> None:
    output = replay["output"]
    draft = output.draft
    print()
    print("=" * 78)
    print("【离线重放 · 非新的真实验收】财务节：原束 → 决定 → 新修订 → 绑定/蕴含 → 正文")
    print("=" * 78)
    print("留存真实返回:", FIN_CALL)
    print("重映射审计:", json.dumps(replay["audit"], ensure_ascii=False))
    print("替身调用:", json.dumps(
        [{"i": i + 1, "prompt_version": c["prompt_version"]}
         for i, c in enumerate(replay["client"].calls)], ensure_ascii=False))
    print(f"权威事实 {len(case['scan'].facts)} 条（Δpp 派生事实在 f1）；"
          f"提案 {len(draft.claim_candidates)} 条；"
          f"支撑提案 {len(draft.proposed_support_refs)} 条")
    print(f"draft_id={draft.draft_id} revision={draft.draft_revision}"
          f"（r7b 记录 {RECORDED_FIN_REVISION}：改动前逐字相同；两个版本串前进后身份重算，"
          "归因见 §2.3 的可执行重算）")
    print(f"聚合 {len(output.aggregate_decisions)}；蕴含 {len(output.entailment_decisions)}；"
          f"接受边 {len(output.acceptance.accepted_bindings)}；"
          f"被拒 subject {len(output.acceptance.rejected_subjects)}")
    print(f"narrative_id={output.narrative.narrative_id} "
          f"段落={len(output.narrative.paragraphs)} 表={len(output.narrative.tables)}"
          f"（r7b 记录 段落={RECORDED_FIN_PARAGRAPHS} 表={RECORDED_FIN_TABLES}）")
    for para in output.narrative.paragraphs:
        for sentence in para.sentences:
            print(f"   句 | {sentence.text}")
            for cid in sentence.claim_ids:
                print(f"       ← claim {cid}")
    for table in output.narrative.tables:
        print(f"   表 | unit={table.unit} period={table.period} header={list(table.header)}")
        for row in table.rows:
            print(f"      | {row.label} {list(row.cells)}")
    print(f"SectionResult status={output.result.status} id={output.result.section_result_id}")
    print(f"llm_calls={output.llm_calls}")
    print("-- 缺口（如实呈现）--")
    for item in output.result.unresolved:
        print(f"   {item.unresolved_id} [{item.state}] {item.detail[:170]}")


def _num(text) -> Decimal:
    """展示值 → Decimal（容忍百分号后缀；这是**显示面**的数，正是「不得据它相减」的那一侧）。"""
    return Decimal(str(text).rstrip("%").strip())


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------

def _check_company_recorded_facts(check, check_eq) -> dict:
    """§0 记录侧事实：把「本重放不是复现 r7b」这件事写成断言，而不是写进注释里。"""
    drafts_recorded = _read_json(R7B / "section_drafts.json")
    rejections_recorded = _read_json(R7B / "proposal_set_rejections.json")
    needs_recorded = _read_json(R7B / "follow_up_needs.json")
    recorded_company_rejections = [r for r in rejections_recorded["sections"]["company"]
                                   if isinstance(r, dict)]
    recorded_nar = _read_json(R7B / "section_narratives.json")["financial"]

    check_eq(sorted(drafts_recorded), list(RECORDED_SECTION_DRAFTS),
             "r7b 的 `section_drafts.json` 只有 financial 与 industry：**company 整节失败**，"
             "没有任何公司草稿可复现（因此 §1 的公司半场是新产物，不是复现）")
    check_eq(len(recorded_company_rejections), 2,
             "r7b 的 company 留下 **2** 条整束拒绝记录")
    check_eq(tuple(r["rejection_kind"] for r in recorded_company_rejections),
             RECORDED_COMPANY_REJECTION_KINDS,
             "两条记录都是 schema_invalid（结构没过：primary 序 + 控制字符），**不是**本批要处理的 "
             "`path_b_high_risk_surface`——因此公司半场的拒绝序列与 r7b 不同，如实记录")
    check("ProposalSetRejectedError" in str(
        rejections_recorded["sections_failed"].get("company") or ""),
        "company 的失败文本是一条整束拒绝异常（那一节整节没有产物）")
    check_eq(rejections_recorded["sections_completed"], ["financial", "industry"],
             "r7b 只完成了 financial 与 industry 两节")
    check_eq(len(rejections_recorded["sections"]["financial"]), 0,
             "r7b 的 financial 一次都没被整束拒绝（本重放沿用这一点）")
    check_eq(needs_recorded["total_pending_needs"], RECORDED_PENDING_NEEDS,
             "r7b 留下 11 条待裁决补件诉求（本模块不裁决它们，也不读成已满足）")
    check_eq(tuple(sorted(needs_recorded["sections"])), ("company",),
             "11 条诉求全在 company 上：那一节正是整节失败的那一节")
    check_eq(len(recorded_nar["paragraphs"]), RECORDED_FIN_PARAGRAPHS,
             "r7b 的财务正文是 **0 段**：本批新增的正是那一段比较分析")
    check_eq(len(recorded_nar["tables"]), RECORDED_FIN_TABLES,
             "r7b 的财务产物是 4 张表")
    return {"recorded_fin_narrative": recorded_nar}


def _check_company_replay(check, check_eq, details) -> dict:
    """§1 公司半场：同链重放 + 原束→裁出→新修订的身份对账 + 逐句可回查。"""
    case = _build_company_case()
    pack_mats = sum(len(p.materials) for p in case["pack_set"].packs)
    check_eq(pack_mats, len(case["entries"]),
             "每个 Pack 成员材料各出现一次（材料清单与 Pack 成员一一对应）")
    check_eq(case["anchors"],
             {key: pack_mats for key in ("content_fingerprint", "locator_ref",
                                         "source_identity", "payload_hash")},
             f"{pack_mats} 条材料在**内容指纹 / locator / 权威来源身份 / payload 哈希**四项上"
             "逐条等于 r7b 记录的 members 行（材料身份不是「差不多」）")
    # --- 有序源集：夹具挂的必须是 r7b 那一轮**真实**用过的来源集 ---
    # 这一条不是「夹具自己说自己是真实的」：比的是 r7b 记录下来的逐节 `source_set_fingerprint`。
    recorded_sets = {row["topic_id"]: row["source_set_fingerprint"]
                     for row in _read_json(R7B / "material_pack.json")["sections"]["company"]
                     ["source_set"]}
    check_eq({p.topic_id: p.source_set.fingerprint() for p in case["pack_set"].packs},
             recorded_sets,
             "本节每个 Pack 的来源集指纹逐字等于 r7b 记录的 `source_set_fingerprint`"
             "（来源角色只从这张表读，夹具带错来源集就是夹具失真）")
    roles = SRS.document_roles(case["authority"])
    check_eq(roles, {"NDSD_2025_year": "current_state_source",
                     "NDSD_2024_year": "history_and_conflict_source",
                     "NDSD_KCZ_2026": "topic_participating_source"},
             "来源角色台账逐份落在 r7b 记录的角色上（角色是**记录值**，不是本模块的判断）："
             "2024 年报是同类较旧（不能表达当前状态），2025 年报是当前锚，"
             "跨系列募集说明书按主题同等资格参与")
    material_docs = {str(entry["locator"].get("document_id") or "") for entry in case["entries"]}
    check(material_docs <= set(roles) and "" not in material_docs,
          f"本节 {len(case['entries'])} 条材料定位到的文档**全部**在来源集台账里"
          f"（{sorted(material_docs)}）：一条查不到角色的支撑边会让 `srsc-1` 当场 fail-closed")

    check_eq(list(case["task"].topic_ids), list(case["req1"]["task"]["topic_ids"]),
             "重建的 topic 序与 r7b 第 1 次请求 payload 逐项相同")
    check(case["task"].title == case["req1"]["task"]["title"]
          and case["task"].purpose == case["req1"]["task"]["purpose"],
          "重建的 title/purpose 与记录逐字相同")
    check_eq(len(case["specs"]), COMPANY_ASPECT_COUNT,
             "冻结 Contract 的公司节 aspect 规格面（不增不减）")

    replay = _drive_company(case, proposal_wire=REPLAY_WIRE, replay_source=REPLAY_SOURCE)
    _print_company(replay, case)
    outcome, chain, output = replay["outcome"], replay["chain"], replay["output"]
    draft = outcome.draft

    check_eq(tuple(c["kind"] for c in replay["client"].calls),
             ("recorded_call_1", "recorded_call_2", "recorded_call_3", "fixture",
              "organizer_stub"),
             "调用序列恰为「3 次留存真实返回 + 1 次**夹具**（批 4，r7b 从未调用）+ 1 次替身组织」："
             "多一次都会因留存返回用尽而抛 `ReplayExhausted`")
    check_eq(tuple(c["batch_label"] for c in replay["client"].calls[:4]),
             ("1/4", "2/4", "3/4", "4/4"), "四次提案调用各回答一个批次")
    check(all(c["prompt_version"] == PW.NARRATION_PROMPT_VERSION
              for c in replay["client"].calls[:4]),
          "四次提案调用都用当前 prompt 版本（原始返回是按同一版本问出来的）")
    check_eq(replay["client"].calls[-1]["prompt_version"],
             NO.NARRATIVE_ORGANIZER_PROMPT_VERSION,
             "门后组织那一次是**当前**组织器版本（r7b 那一轮没发生过）")
    check_eq(replay["policy"].renderer_version, PW.WRITER_RENDERER_VERSION,
             "公司半场跑的是当前 renderer 版本（r7b 记录的是 `pwr-3`；读者面批到 `pwr-4`，"
             "定点批 §四 的来源行到 `pwr-5`）")

    check_eq(len(draft.claim_candidates), SURVIVING_CANDIDATES, "新修订的候选条数")
    check_eq(len(draft.narrative_draft_units), 14, "门前草稿单元数")
    # 支撑提案逐边派生：12 条候选共 49 条边。草稿单元数（14）本批**不变**——单元是 context，
    # `srsc-2`/`srsc-3` 只扫 factual 候选，不碰单元（单元既不进正文也不进组织器输入面，见本文件
    # 末尾那条 NOTE）。被拒的候选各自带走自己的边，剩下的候选一条不少。
    check_eq(len(draft.proposed_support_refs), 49, "支撑提案（逐边）条数")
    check_eq(draft.draft_id, REPLAY_COMPANY_DRAFT_ID,
             "公司 draft_id 在本批代码+夹具下可复算（它不是 r7b 记录的身份——r7b 没有公司草稿）")
    check_eq(draft.draft_revision, REPLAY_COMPANY_REVISION,
             "公司 draft 修订在本批代码+夹具下可复算")
    check_eq(draft.writer_attempt, 2, "接受的是第 2 次生成（第 1 次整束被拒，第 2 次由裁出产生）")

    # --- 原束 → 裁出 → 新修订：三者身份逐项对账 ---
    check_eq(len(outcome.rejections), 1, "恰有一条整束拒绝审计（原束**不被改写**地留档）")
    rec = outcome.rejections[0]
    check_eq(rec.rejection_kind, REPLAY_COMPANY_REJECTION_KIND,
             "拒绝原因是路径 B 候选携带高风险表面（写入侧与机械门共用的那个判据）")
    check_eq(rec.attempt, 1, "被拒的是第 1 次生成")
    check_eq(len(rec.candidate_audit), len(rec.candidate_ids),
             "逐候选审计的键集**恒等**于该束完整有序候选身份（不裁剪、不重排）")
    check(rec.reproposal is None and rec.answered_by_attempt is None,
          "这一束走的是**逐候选裁出**而不是「定向重提案」：两条出口互斥，裁出这条路不向模型重问"
          "任何东西，因此 `answered_by_attempt` 留空")
    trace = rec.carve_out
    check(trace is not None,
          f"被拒的这一次排定了 `{PW.CANDIDATE_CARVE_OUT_VERSION}` 逐候选裁出"
          "（`cco-2` 相对 `cco-1`：多认一类原因 `path_b_history_only_current_state`；"
          "`cco-3` 相对 `cco-2`：再认一类 `path_b_unproven_current_state`；"
          "`cco-5` 相对 `cco-4`：这一类原因带上 typed 细分原因码——"
          "本批那 43 条候选正是走这一版裁出的）")
    if trace is not None:
        decision = trace.decision
        check_eq(decision.to_attempt, 2, "裁出的目标轮次是第 2 次生成")
        check_eq(decision.to_revision, draft.draft_revision,
                 "裁出记下的新修订就是**真的被采纳**的那一版（不是承诺）")
        check_eq(decision.source_candidate_ids,
                 tuple(d.candidate_id for d in trace.destinations),
                 "裁出来源的候选身份 = 去向表的键集（原束完整有序身份，一个字都不改）")
        check_eq(len(decision.excluded), EXCLUDED_CANDIDATES,
                 "被逐条排除的候选数")
        check_eq(len(decision.surviving_candidate_ids), SURVIVING_CANDIDATES,
                 "幸存候选数")
        check_eq(len(decision.source_candidate_ids), BUNDLE_CANDIDATES, "原束候选数")
        check_eq(len(decision.excluded) + len(decision.surviving_candidate_ids),
                 len(decision.source_candidate_ids),
                 "**守恒**：被排除数 + 幸存数 = 原束数（「候选无故消失」在结构上无从表达）")
        new_ids = tuple(c.candidate_id for c in draft.claim_candidates)
        check_eq(trace.next_candidate_ids, new_ids,
                 "带 `next_candidate_id` 的那些就是**新修订**的完整有序候选身份（逐条回填证实）")
        check_eq(len(decision.surviving_candidate_ids), len(new_ids),
                 "原束幸存几条，新修订就重新派生几条（条数守恒）")
        check(not (set(new_ids) & set(decision.surviving_candidate_ids)),
              "**反例：旧束被洗白**。新修订的候选身份是**重新派生**的，与旧束的幸存身份集合"
              "**不相交**——「把旧束删几条后的子集冒充原集合」在这里会立刻暴露："
              "新束既不是旧束本身，也不是旧束的切片")
        check_eq(trace.model_calls_added, 0,
                 "逐候选裁出**不新增任何模型调用**（它不向模型重问任何东西）")
        check(len(decision.source_candidate_ids) > len(draft.claim_candidates),
              "原束候选数 **多于**新修订候选数（差额恰是被排除的那些）")
        check_eq(len(rec.candidate_ids), len(decision.source_candidate_ids),
                 "留档的「原束完整有序候选身份」与该束候选数逐项相等（不缩成「有用的那几条」）")

        # --- 误伤词 vs 真实风险词（逐字命中，且没有删任何风险词）---
        # 定位**按原文**、身份**按钉子**核对：候选身份串随版本串重算（见文件头归因），用文本
        # 定位才让「哪一条」是个可读事实；身份对不上则是一条 FAIL，不是一次 KeyError 崩溃。
        surfaces = {e.candidate_id: tuple(e.surfaces) for e in decision.excluded}
        by_text = {str(e.claim_text): e for e in decision.excluded}
        stock_entry = by_text.get(NAMED_EXCLUDED_STOCK_TEXT)
        entity_entry = by_text.get(NAMED_EXCLUDED_ENTITY_TEXT)
        check(stock_entry is not None and entity_entry is not None,
              "**反例：旧束被洗白**。原束里被点名的两条候选在新修订里**确实不在**了"
              f"（按原文定位：{NAMED_EXCLUDED_STOCK_TEXT!r} / {NAMED_EXCLUDED_ENTITY_TEXT!r}）")
        check_eq((getattr(stock_entry, "candidate_id", None),
                  getattr(entity_entry, "candidate_id", None)),
                 NAMED_EXCLUDED_CANDIDATE_IDS,
                 "这两条候选的身份串就是本批代码下重算出来的那个（文本未改、身份随版本串重算）")
        check_eq(getattr(stock_entry, "surfaces", ()), (EXCLUDED_STOCK_TOKEN, "8股"),
                 "一条被点名的表面是股权记号（真实高风险词，逐字命中）")
        check_eq(getattr(entity_entry, "surfaces", ()), EXCLUDED_ENTITY_NAMES,
                 "一条被点名的表面是**完整**法人主体名（主体身份与数字同级，逐字命中；"
                 "点名的 token 是整名，不是被删掉的后缀）")
        check(set(NAMED_EXCLUDED_CANDIDATE_IDS) <= set(surfaces),
              "这两条身份确实在排除集里（不是靠原文另找一条凑数）")

        # --- `nrules-11`：6 条**硬事实**候选逐条被 typed 拒绝（真实数据复核）---
        # 这 6 条在 `nrules-10` 下逐边 `pass`、进了正文；按冻结的高风险硬事实规则，它们
        # 没有预验证事实身份（`fact_id=None`），因此路径 B 不得授权它们。逐条核对身份、
        # 原文与表面集：不是「少了几条」的计数断言，而是「这一条为什么被拒」的逐条对账。
        entries = {e.candidate_id: e for e in decision.excluded}
        for cand_id, text, expected in HIGH_RISK_HARD_FACT_CANDIDATES:
            entry = entries.get(cand_id)
            check(entry is not None, f"{text!r} 必须逐条被排除（候选 {cand_id}）")
            if entry is None:
                continue
            check_eq(str(entry.claim_text), text,
                     f"被排除的候选 {cand_id} 的原文（逐字来自 r7b 留存返回）")
            check_eq(tuple(entry.surfaces), expected,
                     f"{text!r} 的高风险表面集（逐字来自它自己的文本）")
            check(REPLAY_COMPANY_REJECTION_KIND in tuple(entry.reasons),
                  f"{text!r} 的排除原因含 {REPLAY_COMPANY_REJECTION_KIND}（路径 B 授权面）")
        # --- 「境外客户回款情况正常」：两条**各自成立**的拒绝理由，逐条给出原文证据 ---
        # 理由一（判据）：它是无期间绑定的现状评价，`正常` 是 `nrules-11` 登记的状态评价标记。
        # 理由二（材料）：它落在绑定材料自己的**「报告期内」限定块**里——限定语是那一块的，
        # 候选文本把它丢了，于是同一句话从「某段报告期内的观察」升级成一条**无期间**的确定判断，
        # 读者会读成「生成时点的现状」。两者都不依赖这家公司的字面。
        _c32 = "境外客户回款情况正常"
        # 候选在**返回**的 `claim_candidates` 里；它绑定的材料在**请求** payload 的 `materials` 里。
        # 两者是同一份 r7b 留档的两个面，键名不同（上一版把两者当成请求里的同一个键，才 KeyError）。
        _c32_payload = _payload_of(COMPANY_CALLS[2])
        _c32_return, _c32_end = json.JSONDecoder(strict=False).raw_decode(
            _read_call(COMPANY_CALLS[2])["completion"])
        _c32_pos = next(i for i, c in enumerate(_c32_return["claim_candidates"])
                        if str(c["claim_text"]) == _c32)
        _c32_ref = str(_c32_return["claim_candidates"][_c32_pos]["support"][0]["ref"])
        _c32_material = next(m for m in _c32_payload["materials"] if str(m["ref"]) == _c32_ref)
        _c32_block = str(_c32_material["text"])

        def _periodless_within(candidate_text: str, block: str) -> bool:
            """候选文本是否落在该材料的「报告期内」限定块里、而它自己一个期间限定语都不带。

            可复算、且**不**是「材料里出现过『报告期』没有」这种宽松匹配：限定语必须出现在
            候选文本**之前**（即限定的是这一整块），候选文本自己仍必须不带任何期间限定语。
            """
            at = block.find(candidate_text)
            return at >= 0 and "报告期" in block[:at] and "报告期" not in candidate_text

        check(_periodless_within(_c32, _c32_block),
              "**期丢失**：候选落在材料自己「报告期内」限定的那一块里（限定语在它之前 "
              f"{_c32_block.find(_c32)} 字处），而候选文本自己一个期间限定语都没有——同一句话从"
              "「某段报告期内的观察」升级成一条**无期间**的判断"
              f"（材料 {_c32_ref} 的块首：{_c32_block[:60]!r}…）")
        # **反例：误伤**（同一判据的两个负面应用，都用**真实材料**里的字面，不是编造的字面）：
        #   ① 把限定语一起带进候选文本（= 写成「报告期内，境外客户回款情况正常」）时**不**判；
        #   ② 材料**没有**「报告期内」限定块时**不**判——判据看的是候选**之前**有没有限定语，
        #      不是「这个短语在材料里出现过没有」。
        _c32_qualified = next((s.strip() for s in _c32_block.split("。") if "报告期" in s), "")
        check(bool(_c32_qualified) and not _periodless_within(_c32_qualified, _c32_block),
              "反例①：材料里**自带期间限定语**的那一句（逐字取用）不被判「期丢失」——判据要求"
              f"候选文本自己不带限定语（该句：{_c32_qualified[:32]!r}…）")
        _plain = next((m for m in _c32_payload["materials"]
                       if "报告期" not in str(m["text"])), None)
        _plain_clause = next((s.strip() for s in str(_plain["text"]).split("。")
                              if len(s.strip()) >= 8), "") if _plain else ""
        check(bool(_plain) and bool(_plain_clause)
              and not _periodless_within(_plain_clause, str(_plain["text"])),
              "反例②：材料没有「报告期内」限定块时，同一候选字面不被判「期丢失」"
              f"（实取材料 {(_plain or {}).get('ref')!r}：{_plain_clause[:24]!r}）")
        check(any(e.candidate_id == cand_id
                  for cand_id, text, _expect in HIGH_RISK_HARD_FACT_CANDIDATES
                  if text == _c32 for e in decision.excluded),
              "该候选被逐条 typed 拒绝（不写进正文），而不是被写成无期间判断")
        details.append(
            f"NOTE 期间限定语丢失（逐字证据，非判读）：材料 {_c32_ref} 的块以「报告期内」限定"
            f"（{_c32_block[:60]!r}…），候选 {_c32!r} 落在该块内却不带任何期间限定语。"
            "本批按「无预验证事实身份 ⇒ 路径 B 不得授权」拒掉它；期间与资格化两笔都记在案。")

        # **反例：误伤**。排除集必须能由**各条自己的文本**逐条复算，而且只能由它复算——
        # 「为什么被拒」不是一张名单，也不是「这一批少了几条」。同族的描述性候选只要文本里
        # 没有硬事实角色，扫描器就该给它空表面集，因而它**不可能**出现在排除集里。
        check(all(tuple(NS.high_risk_surface_tokens(e.claim_text)) == tuple(e.surfaces)
                  for e in decision.excluded),
              "每条被排除候选的表面集 = 在它自己的文本上重算的结果（逐条可复算，非名单）")
        descriptive = [c.claim_text for c in draft.claim_candidates
                       if NS.high_risk_surface_tokens(c.claim_text) == ()]
        descriptive_excluded = [e for e in decision.excluded
                                if NS.high_risk_surface_tokens(e.claim_text) == ()]
        # **反例：静默连坐**改为**原因可归因**。原先这里断言「幸存束里仍有 ≥20 条零表面集的
        # 描述性候选」，用来证明「这一批只拒硬事实角色，没有把整个主题连坐」。`srsc-2` 之后
        # 这个断言**在事实上不成立**了：本批**确实**拒掉了 46 条零表面集的描述性候选
        # （35 条独立支撑结论 + 11 条期间/来源角色）。把门槛降到「≥12 就算过」是粉饰；
        # 把这条删掉是掩盖。真正的性质不是「描述性候选不会被拒」，而是——
        # **每一条被拒都能从它自己文本上重算的原因与成员身份说清楚，而且幸存集恰好就是
        # 「没有任何一条登记原因成立」的那些**。因此改成下面三条：
        #   (a) 幸存集 = 零表面集 ∩ 未被任何期间/来源判据点名的那些（逐条对齐，非计数）；
        #   (b) 46 条零表面集的排除项**全部**由期间/来源/资格类判据打回，没有一条带
        #       `not_individually_implicated`（那会变成「没理由地被拒」）；
        #   (c) 内容代价如实报数（46 条），并把它逐条可查——**不是**读成「质量变好了」。
        # `srsc-3` 之后这 46 条里多了一种**细分**：独立支撑结论那 35 条中有 1 条的原因码是
        # `source_period_scope_dropped`（境外销售那句），另 34 条是
        # `not_extractive_in_any_current_source`——两者同属「路径 B 不得据此授权」，只是
        # 可核验与否不同（下面单独断言）。
        check_eq(len(descriptive), SURVIVING_CANDIDATES,
                 "幸存束里**全部**候选都是零表面集的描述性候选（含高风险表面的候选本批"
                 "一条都没留下——这正是该拒的那一类）")
        check_eq(len(descriptive_excluded), EXCLUDED_CANDIDATES - sum(
            1 for e in decision.excluded
            if REPLAY_COMPANY_REJECTION_KIND in e.reasons),
            "零表面集却被拒的候选数 = 被排除数 − 带高风险表面的那些（本批 46 条："
            "35 条独立支撑结论 + 11 条期间/来源角色）——**这就是本次的内容代价，如实报数**")
        check(all(not tuple(e.reasons) == ("not_individually_implicated",)
                  for e in descriptive_excluded),
              "这 46 条各自带**成立的原因**（不是「这一束被拒、顺带把它也算上」）："
              "「没被单独点名」只属于幸存下来的候选，不属于被拒的候选")
        # 逐条原因取自**登记过的闭集**，不是自由文本；`srsc-1` / `srsc-2` / `srsc-3` 之后这里不止
        # 一种原因，因此断言「原因非空且逐条在册」，而不是断言每一条都带高风险表面——后者会把
        # 「这条候选为什么被拒」压成单一理由。
        check(all(e.reasons and all(r in PW.CANDIDATE_CARVE_OUT_REASONS for r in e.reasons)
                  for e in decision.excluded),
              "被排除的每一条候选各带 typed 排除原因（每个被排除候选有独立拒绝记录）")
        # 高风险表面覆盖哪些、不覆盖哪些：**不是**「除只由旧材料支撑以外的全部」那么简单了。
        # 现在不覆盖高风险表面的是**两类**只由期间/来源类判据打回的候选（35 + 11 = 46 条）。
        # 断言写成自算形式，不写死算术恒等式（写死的式子会随候选集一变就成了假读数）。
        sole_period_role = [e for e in decision.excluded
                            if tuple(e.reasons) in (
                                ("path_b_history_only_current_state",),
                                ("path_b_unproven_current_state",))]
        check_eq(sum(1 for e in decision.excluded
                     if REPLAY_COMPANY_REJECTION_KIND in e.reasons),
                 len(decision.excluded) - len(sole_period_role),
                 "整束拒绝的原因（高风险表面）覆盖**除「只由期间/来源类判据打回」以外**的每一条"
                 "被排除候选：这一束是**因为**高风险表面才被拒，期间/来源与独立支撑结论是"
                 "逐候选追加或独立成立的那些原因")
        history_of = {e.candidate_id: e for e in decision.excluded
                      if "path_b_history_only_current_state" in e.reasons}
        check_eq(len(history_of), HISTORY_ONLY_CANDIDATES,
                 "带**期间/来源角色**这一条原因的候选数（`srsc-1`：支撑边全部落在同类较旧来源、"
                 "文本自己又没有期间限定）")
        check_eq(sum(1 for e in history_of.values()
                     if tuple(e.reasons) == ("path_b_history_only_current_state",)),
                 HISTORY_ONLY_SOLE_REASON_CANDIDATES,
                 "其中**只**带这一条原因的候选数（余下 3 条已因高风险表面被拒，这里是**追加**"
                 "一条原因，不是改判）")
        claimed_pairs = [(e.candidate_id, str(e.claim_text)) for e in decision.excluded
                         if tuple(e.reasons) == ("path_b_history_only_current_state",)]
        check_eq(set(claimed_pairs), set(HISTORY_ONLY_NAMED_CANDIDATES),
                 "这 11 条的身份与原文逐条等于钉子（按原文定位、按身份核对，不是按条数凑）")
        check(all(len(tuple(e.history_only_member_refs)) > 0 for e in history_of.values()),
              "带这条原因的每条都点名了「不能承重的那几条边绑的成员」"
              "（不是泛化理由，也不是「这条候选不好」）")
        members_of = {r for e in history_of.values() for r in tuple(e.history_only_member_refs)}
        docs_by_ref = PW._support_documents(manifest=draft.material_manifest)
        check({docs_by_ref.get(r) for r in members_of} <= {"NDSD_2024_year"},
              "这 11 条被点名的边**全部**落在同类较旧材料（2024 年报）上——"
              "只要有一条边落在 2025 年报上，这条判据**不成立**（挡的是「旧材料当前化」，"
              "不是「用了旧材料」）")
        # --- `srsc-2` 独立支撑结论：真实数据上的第四类打回（不是又一张名单，是**判据成立**）---
        # 这 43 条候选的共性是：**有**当前锚边，但路径 B 仍证不出这条断言——细分两种原因码：
        # ① `not_extractive_in_any_current_source`（40 条）：候选文本不是那条边所绑材料正文的
        #    严格子串（`srsc-2`）；
        # ② `source_period_scope_dropped`（3 条）：候选文本**在**正文里，但包含它的那一句
        #    自己带期间/范围限定，而候选把限定语截在了外面（`srsc-3`，指令第二优先级点名）。
        # 逐条给出成员身份（`unproven_current_state_member_refs`），可拿同一份 manifest 与来源集
        # 复算；不存在「这条候选不好」这种泛化理由。
        unproven_of = {e.candidate_id: e for e in decision.excluded
                       if "path_b_unproven_current_state" in e.reasons}
        check_eq(len(unproven_of), UNPROVEN_CURRENT_STATE_CANDIDATES,
                 "带**独立支撑结论**这一条原因的候选数（路径 B 有当前锚边但证不出断言："
                 "43 条里 `srsc-2`「正文里没这句话」占 40、`srsc-3`「限定语被截在外面」占 3）")
        check_eq(sum(1 for e in unproven_of.values()
                     if tuple(e.reasons) == ("path_b_unproven_current_state",)),
                 UNPROVEN_CURRENT_STATE_SOLE_REASON_CANDIDATES,
                 "其中**只**带这一条原因的候选数（余下 8 条已因高风险表面被拒，这里是**追加**"
                 "一条原因，不是改判）")
        check(all(len(tuple(e.unproven_current_state_member_refs)) > 0
                  for e in unproven_of.values()),
              "带这条原因的每条都点名了「那条无法承重的当前锚边绑的成员」"
              "（逐条成员身份可复算，不是「这条候选不好」）")
        # 与 `srsc-1` 的**互斥**：一条候选要么锚边全是历史角色，要么有当前锚边而正文里没这
        # 句话——两侧都不可能同时命中。这不是巧合，是两条判据的定义决定的（见模块头边界 3/5）。
        check(not (set(unproven_of) & set(history_of)),
              "**互斥**：没有一条候选同时被期间/来源角色与独立支撑结论打回"
              "（一条候选不可能既「没有任何当前锚边」又「有当前锚边」）")
        # 全部落 `unproven`，**没有一条**落 `mismatch`：四轴读数在真实数据上建得起来，因此
        # 拒绝的理由是「正文里确实没有这句话」，而不是「轴读不到只好 fail-closed」。
        # `reason_code` 是唯一能把这两者分开的表面（`mismatch` 会说 `axis_mismatch_*` /
        # `axis_missing`），而审计里只记成员身份、不记结果码——因此这里**独立复算**一遍判据本身：
        # 拿审计记下的那几条锚边成员重建四轴读数，再跑一次 `current_state_support_is_extractive`。
        # 这不是把判据的结论抄一遍，而是从**成员身份**重新取数重算；成员身份错了、轴读不到，
        # 这里一样会落 `mismatch`。
        _axes = SRS.document_axis_index(case["authority"])

        def _recompute(entry):
            return SRS.current_state_support_is_extractive(
                str(entry.claim_text),
                candidate_revision=f"replay#{entry.candidate_id}",
                readings=PW._current_state_anchor_readings(
                    member_refs=tuple(entry.unproven_current_state_member_refs),
                    documents=docs_by_ref, axes=_axes, manifest=draft.material_manifest,
                    material_context=case["context"]))

        verdicts = {cid: _recompute(e) for cid, e in unproven_of.items()}
        check_eq(sorted({v.result for v in verdicts.values()}), ["unproven"],
                 "这 43 条独立重算**全部**落 unproven（四轴读数建得起来 → 拒绝的理由是"
                 "「正文里没有这句，或那句的限定语被截在外面」，不是「轴读不到只好拒」）")
        check_eq(sorted({v.reason_code for v in verdicts.values()}),
                 ["not_extractive_in_any_current_source", "source_period_scope_dropped"],
                 "理由码逐条落在闭集里（本批**两种**：40 条 `not_extractive_in_any_current_source`"
                 "+ 3 条 `source_period_scope_dropped`），且**没有**任何一条是 `axis_missing` / "
                 "`axis_mismatch_*`（「同 ID 错版本」的反例本身由 "
                 "`evals/test_m930_3_source_role_scope.py` §9 覆盖；这里用真实数据复核"
                 "「它确实没在这批上开火」）")
        # 期间轴：本判据**两侧都覆盖**了。候选侧：候选自己写没写期间（`has_period_qualification`）。
        # 支撑侧（本批新做，`srsc-3`）：候选**覆盖到**的那几句源句**自己**是否被期间/范围限定——
        # 是则记 `source_period_scope_dropped`。下面逐条断言两件事：① 被 `srsc-3` 点名的那 3 条
        # 逐条等于钉子（身份 + 原文 + 原因码表面）；② 幸存下来的每一条候选，它覆盖到的源句都
        # **不**带被截掉的限定语——即「稳定客观描述不被强行加日期」这条保护在真实数据上成立。
        period_dropped = {cid for cid, v in verdicts.items()
                          if v.reason_code == "source_period_scope_dropped"}
        by_reason = {cid: v.reason_code for cid, v in verdicts.items()}
        for cand_id, text, cause, also_high_risk in PERIOD_SCOPE_DROPPED_NAMED_CANDIDATES:
            entry = unproven_of.get(cand_id)
            check(entry is not None, f"`srsc-3` 点名的候选 {text!r} 必须在排除集里（{cand_id}）")
            if entry is None:
                continue
            check_eq(str(entry.claim_text), text,
                     f"被 `srsc-3` 打回的候选 {cand_id} 的原文（逐字来自 r7b 留存返回）")
            check_eq(by_reason.get(cand_id), cause,
                     f"{text!r} 独立重算出的原因码必须是 {cause}（不是笼统的「正文里没有这句」）")
            check_eq(getattr(entry, "unproven_current_state_cause_code", None), cause,
                     f"{text!r} 的**审计记录**里也要带同一个 typed 原因码"
                     "（写规则只写在判据里不算数：可核验性要落在留档上）")
            check_eq(REPLAY_COMPANY_REJECTION_KIND in tuple(entry.reasons), also_high_risk,
                     f"{text!r} 是否另有高风险表面（本批 3 条里 2 条另有；这一条另行断言，"
                     "免得把「追加原因」读成「改判」）")
        check_eq(len(period_dropped), 3,
                 "本批恰 3 条候选由 `srsc-3` 的期间/范围这一轴打回")
        # ① 真子串：候选**确实**逐字在源句里（打回不是靠「没找到这句话」）
        check(PERIOD_SCOPE_DROPPED_NAMED_CANDIDATES[0][1] in PERIOD_SCOPE_SOURCE_SENTENCE,
              "被点名的第一条候选是源句的**真子串**——「包含」这一步确实成立，"
              "打回的理由只可能是「限定语被截在外面」")
        # ② 源句自己带限定语（可复算，不是判读）
        check(SRS.has_period_qualification(PERIOD_SCOPE_SOURCE_SENTENCE)
              and not SRS.has_period_qualification(PERIOD_SCOPE_DROPPED_NAMED_CANDIDATES[0][1]),
              "源句自带期间限定、候选自己一个都不带：这才是 `source_period_scope_dropped` 的形状")
        # ③ 反例（保护）：把限定语收进候选 ⇒ 同一条断言立刻可证（不再被判）。
        #    **读数取自被点名候选自己的当前锚边**，因此它先要那条候选在场：身份前进时它可能
        #    整体换身（钉子未同步），此时**记一条失败并跳过**，不得用 `KeyError` 把整节检查
        #    掀掉——上面的 ①② 不依赖它，仍照跑；「钉子过期」与「判据行为变了」必须是两笔账。
        _named_entry = unproven_of.get(PERIOD_SCOPE_DROPPED_NAMED_CANDIDATES[0][0])
        check(_named_entry is not None,
              "反例③ 需要被点名候选（"
              f"{PERIOD_SCOPE_DROPPED_NAMED_CANDIDATES[0][0]}）的当前锚边读数，"
              "它必须在本批排除集里（身份前进后旧钉子可能整体换身：同步钉子，不要跳过本检查）")
        if _named_entry is not None:
            _qualified_candidate = PERIOD_SCOPE_SOURCE_SENTENCE.rstrip("。")
            _qualified_verdict = SRS.current_state_support_is_extractive(
                _qualified_candidate, candidate_revision="replay#qualified_counterexample",
                readings=PW._current_state_anchor_readings(
                    member_refs=tuple(_named_entry.unproven_current_state_member_refs),
                    documents=docs_by_ref, axes=_axes, manifest=draft.material_manifest,
                    material_context=case["context"]))
            check_eq((_qualified_verdict.result, _qualified_verdict.reason_code),
                     ("extractive", "extractive_contiguous_containment"),
                     "反例（误伤）：把源句**连限定语一起**收进候选，同一条断言立刻判 `extractive`"
                     "——判据挡的是「截掉限定语」，不是「写了境外销售这件事」")
        # ④ 保护：写进正文的每一条候选，拿**同一份 manifest 的全部成员**当阅读视图重跑这条
        #    判据，必须**全部**落 `extractive`。这就是指令那句「不要反过来把所有稳定的客观描述
        #    都强行加日期」的可执行形态：正文里的句子一条都不是靠「补一个期间词」才成立的。
        #    （反例方向也因此被关掉：若某条正文句只能靠截掉限定语才写得出，这里会落 `unproven`
        #    并被列出来。）
        _all_members = tuple(draft.material_manifest.member_refs())
        _unprovable_written: list[tuple[str, str]] = []
        for cand in draft.claim_candidates:
            _written_verdict = SRS.current_state_support_is_extractive(
                str(cand.claim_text),
                candidate_revision=f"replay#written#{cand.candidate_id}",
                readings=PW._current_state_anchor_readings(
                    member_refs=_all_members, documents=docs_by_ref, axes=_axes,
                    manifest=draft.material_manifest, material_context=case["context"]))
            if _written_verdict.result != "extractive":
                _unprovable_written.append((str(cand.claim_text), _written_verdict.reason_code))
        check(not _unprovable_written,
              "**保护**：写进正文的每一条候选，用同一份 manifest 的**全部成员**重跑这条判据都落"
              "`extractive`——没有一条是靠「给稳定的客观描述强行加日期」才写出来的"
              f"（若有，本条列出：{_unprovable_written[:2]}）")
        check(all(not SRS.has_period_qualification(str(e.claim_text))
                  for e in unproven_of.values()),
              "这 43 条自己都**没有**写明期间（写明期间的候选不进本判据——那一步是显式的，"
              "见 `_path_b_unproven_current_state` 里那条 `has_period_qualification` skip）")
        dual = [e for e in decision.excluded if len(tuple(e.reasons)) > 1]
        ineligible_dual = [e for e in dual if "path_b_ineligible_material_scope" in e.reasons]
        history_dual = [e for e in dual
                        if "path_b_history_only_current_state" in e.reasons]
        unproven_dual = [e for e in dual if "path_b_unproven_current_state" in e.reasons]
        check_eq(len(ineligible_dual), INELIGIBLE_SCOPE_CANDIDATES,
                 "其中若干条同时带**两个**原因（高风险表面 + 材料范围不合格），"
                 "且每条的第二因素材在 `ineligible_member_refs` 里有 typed 绑定")
        check(all(len(tuple(e.ineligible_member_refs)) > 0 for e in ineligible_dual),
              "带「材料范围不合格」的每条都点名了不合格的材料成员（不是泛化理由）")
        check_eq(len(history_dual), DUAL_WITH_HISTORY_ONLY,
                 "双原因里带期间/来源角色的那几条（3 条，另有高风险表面）")
        check_eq(len(unproven_dual), DUAL_WITH_UNPROVEN,
                 "双原因里带独立支撑结论的那几条（8 条，另有高风险表面）——它们是本次**追加**"
                 "一条原因，不是从别的类里改判过来的（`srsc-3` 让这一类 6 → 8：新增两条是"
                 "「A 股上市」与「每 10 股转增 8 股」）")
        check_eq(len(dual), DUAL_REASON_CANDIDATES,
                 "双原因候选恰三类：材料范围不合格 3 + 期间/来源角色 3 + 独立支撑结论 8，"
                 "三类都另有高风险表面这条原因，逐条原因可查")
        excluded_ids = {e.candidate_id for e in decision.excluded}
        others = [a for a in rec.candidate_audit if a.candidate_id not in excluded_ids]
        check(all(tuple(a.reasons) == ("not_individually_implicated",) for a in others),
              "其余候选逐条记成「这一束被拒不是因为它」——**不是**一张幸存者名单")
        check_eq(len(others), len(rec.candidate_ids) - EXCLUDED_CANDIDATES,
                 "「没被单独点名」的候选数 = 原束候选数 − 被点名数")
        check_eq(len(others), len(draft.claim_candidates),
                 "没被单独点名的那些正是幸存下来的那些（逐候选审计与幸存序列对齐）")
        check("说明" in NS.MARKER_SUBSTRING_EXEMPTIONS
              and "说明" not in NS.marker_hits("公司募集说明书中列示了主要产品"),
              "**误伤词**：「募集说明书」里的「说明」是文书名，子串豁免使它不命中")
        check("说明" in NS.marker_hits("上述数据说明公司经营稳健"),
              "同一个词在「…说明公司经营稳健」里仍是结论连接词，照旧命中（豁免是逐次判的）")
        check("适用" in NS.HIGH_RISK_SURFACE_MARKERS, "「适用」仍是登记在册的风险标记词")
        check("适用" in NS.high_risk_surface_tokens("该条款适用于本公司"),
              "**真实风险词不删**：「适用于」里的「适用」是适用性声明，照旧是高风险表面")
        check("银行" in NS.ENTITY_SUFFIXES, "「银行」仍是登记的法人主体后缀")
        check(any("银行" in token
                  for token in NS.high_risk_surface_tokens("公司向中国人民银行申请再贷款")),
              "**真实风险词不删**：完整机构名里的「银行」照旧产出主体身份表面"
              "（点名的 token 是完整名，不是被删掉的后缀）")
        check_eq(NS.NARRATIVE_RULES_VERSION, "nrules-18",
                 "上面这几条判据的规则版本（判定集变了就必须换版本号）")
        check_eq(NS.NARRATIVE_GATE_VERSION, "ng-18",
                 "门版本随之前进（`nrules-17`／`nrules-18` 改了**主体名抽取**：`_entity_name_run` "
                 "剥前缀改为不动点、「整串即时间状语」的字号不再产出、且「时间状语 + 自称」那一格"
                 "（`报告期末本公司`）不再产出伪 token——同一份正文在 ng-15 下被拒"
                 "（`截至报告期末公司` 被当成未授权主体名）、ng-17 下通过，二者不得共用一个版本号；"
                 "`ndc-2` 批的 `mbind-1` 收窄支撑边材料判别集，再推到 `ng-18`）")

    # --- 绑定 / 蕴含 / 接受边 ---
    check_eq(len(chain.aggregate_decisions),
             len(draft.claim_candidates) + len(draft.narrative_draft_units),
             "聚合绑定决定数 = 候选数 + 草稿单元数（每个 subject revision 恰一条）")
    check_eq(len({str(d.subject_id) for d in chain.aggregate_decisions}),
             len(chain.aggregate_decisions), "没有两个 subject 共用一条 aggregate 决定")
    check_eq(len(chain.entailment_decisions), len(draft.claim_candidates),
             "**仅** factual candidate 产生蕴含决定（context 只绑定草稿单元，不进这道门）")
    check_eq(len(chain.acceptance.accepted_bindings), len(draft.proposed_support_refs),
             "每条通过的 proposal 各产生一条 typed 接受边（一条不多、一条不少）")
    check_eq(len(chain.acceptance.rejected_subjects), 0,
             "门后没有被拒 subject：两道门 + 接受判定逐条通过")
    check_eq({str(e.claim_candidate_id) for e in chain.entailment_decisions},
             {str(c.candidate_id) for c in draft.claim_candidates},
             "蕴含决定与 factual candidate 一一对应（按 candidate id 键集相等）")
    check(all(bool(b.locator_ref) for b in chain.acceptance.accepted_bindings),
          "每条接受边都带 locator（没有「无定位的支撑」）")
    context_bindings = [b for b in chain.acceptance.accepted_bindings
                        if b.support_semantics == "context"]
    check(bool(context_bindings)
          and all(b.binding_subject_kind == "narrative_draft_unit"
                  and b.entailment_decision_id is None for b in context_bindings),
          "context 接受边只以草稿单元为 target，且不携带蕴含决定（context 不授权事实）")

    # --- 正文：按材料标题树主题组织、逐句可回查的多段经营模式 ---
    # `norg-4` 定点批把「整个 company_business 主题凑成一段」改成「一个业务主题一段」：
    # 段落边界因此有了可复算的来源（材料自己的标题路径），会计政策、财务报告交叉引用与
    # 身份事实各归自己的主题，不再落进经营模式段。
    paragraphs = list(output.narrative.paragraphs)
    check_eq(len(paragraphs), COMPANY_PARAGRAPHS,
             "final Narrative 的段落数（本批 2 段：分段规则本批一字未动，是手里只剩"
             "`company_business` 一个主题的 Claim 可组织——不是「分段坏了」）")
    business = [p for p in paragraphs if "company_business" in tuple(p.topic_ids)]
    check_eq(len(business), COMPANY_BUSINESS_PARAGRAPHS,
             "全部段落都挂在 `company_business` 主题上（同一主题连续、不与别的主题交错）")
    check_eq(sum(len(p.sentences) for p in business), COMPANY_BUSINESS_SENTENCES,
             f"业务主题正文共 {COMPANY_BUSINESS_SENTENCES} 句"
             "（多 Claim 组织出来的句子：4 句承载 12 条 Claim，不是一句话也不是每 Claim 一句）")
    identity = [p for p in paragraphs if "company_identity" in tuple(p.topic_ids)]
    check_eq([len(p.sentences) for p in identity], list(COMPANY_IDENTITY_SENTENCES),
             "主体沿革段本批**为空**——它的候选全被独立支撑结论或期间/来源角色打回，"
             "没有 Claim 可组织。空集本身是要断言的事实（不是把这一条删掉当没发生过）")
    check(all(len(set(p.topic_ids)) == 1 for p in paragraphs),
          "每段的 topic 归属都唯一（段落是从所引用 Claim 确定性派生的，不混主题）")
    claims = {c.claim_id: c for c in output.claims}
    bindings = {b.accepted_support_binding_id: b
                for b in chain.acceptance.accepted_bindings}
    check_eq(len(output.claims), COMPANY_TOTAL_CLAIMS, "门后定稿 Claim 数")
    check_eq(len(output.narrative.tables), 0,
             "公司节无表（表格是财务节的事；本节正文全是组织出来的句子）")

    def _trace(paras) -> tuple[list[str], set[str], set[int], set[tuple]]:
        """逐句逐 Claim 逐边回查一批段落：返回 (坏记录, 文档集, 页码集, 引用对集)。"""
        bad: list[str] = []
        pairs: set[tuple[str, int | None]] = set()
        docs: set[str] = set()
        page_numbers: set[int] = set()
        for para in paras:
            for sentence in para.sentences:
                if sentence.sentence_kind != "composed":
                    bad.append(f"句 {sentence.index} 不是 composed：{sentence.sentence_kind}")
                if not sentence.claim_ids:
                    bad.append(f"句 {sentence.index} 一条 Claim 都没有")
                for cid in sentence.claim_ids:
                    claim = claims.get(cid)
                    if claim is None:
                        bad.append(f"句 {sentence.index} 引用了不存在的 Claim {cid}")
                        continue
                    if not claim.citation_refs:
                        bad.append(f"Claim {cid} 没有引用")
                    if not claim.accepted_binding_ids:
                        bad.append(f"Claim {cid} 没有接受边")
                    for cite in claim.citation_refs:
                        pairs.add((cite.evidence_id, cite.page_number))
                        if cite.page_number is not None:
                            page_numbers.add(int(cite.page_number))
                    for bid in claim.accepted_binding_ids:
                        bound = bindings.get(bid)
                        if bound is None:
                            bad.append(f"Claim {cid} 引用了不存在的接受边 {bid}")
                            continue
                        locator = dict(bound.locator_ref or {})
                        owner = str(locator.get("owner") or "")
                        if locator.get("locator_kind") != "block_range":
                            bad.append(f"接受边 {bid} 的 locator 不是 block_range：{locator}")
                        if "#block_span" not in owner:
                            bad.append(f"接受边 {bid} 的 locator owner 不指向 block_span：{owner}")
                        if ":" in owner and "#" in owner:
                            docs.add(owner.split(":", 1)[1].split("#", 1)[0])
        return bad, docs, page_numbers, pairs

    bad_trace, documents, pages, evidence_pairs = _trace(business)
    check(not bad_trace,
          f"逐句逐 Claim 逐边可回查（无缺引用/缺边/非 composed/坏 locator）：{bad_trace[:3]}")
    check(len(documents) >= 2,
          f"本节正文的接受边跨**两份**文档（实得 {sorted(documents)}）：跨文档、跨页的"
          "逐句可回查")
    check(len(pages) >= MIN_CITED_PAGES,
          f"引用的页码覆盖多页（实得 {sorted(pages)}）")
    # 定点批：6 条硬事实候选被拒后，它们的引用也随之离场，`(evidence_id, page)` 对由 10 降到 8；
    # `srsc-2` 之后 7，`srsc-3` 之后 5。这不是放宽门槛，而是**如实跟着内容走**：下限写成结构
    # 决定的数（`MIN_EVIDENCE_PAIRS` / `MIN_CITED_PAGES`），条数再变也不会变成假读数，
    # 同时仍要求覆盖多份文档、多页。
    check(len(evidence_pairs) >= MIN_EVIDENCE_PAIRS,
          f"正文里逐句回查到的 (evidence_id, page) 对不少于 {MIN_EVIDENCE_PAIRS}"
          f"（实得 {len(evidence_pairs)}）")
    business_text = "".join(s.text for p in business for s in p.sentences)
    for token in ("采购", "生产", "销售", "研发"):
        check(token in business_text, f"业务主题正文含实质性环节词「{token}」")
    # 指令要求的「至少一段实质性的采购/生产/销售模式描述，且带跨页支撑」：**一整段**里同时出现
    # 三个环节词（不是把环节词摊在六段里），并且**该段自己**的逐句引用回查覆盖不止一页。
    substantive = [p for p in business
                   if all(t in "".join(s.text for s in p.sentences)
                          for t in ("采购", "生产", "销售"))]
    check(bool(substantive),
          "至少**一整段**同时给出采购、生产、销售三个环节（实质性的经营模式描述）")
    check(any(len(_trace([p])[2]) >= 2 for p in substantive),
          "该实质段带**跨页**支撑（它自己逐句回查到的页码不止一页）")
    check(any(len(_trace([p])[1]) >= 2 for p in substantive),
          "该实质段带**跨文档**支撑（它自己的接受边指向两份不同的文档）")

    # --- 去向：每条 Claim 恰一个 typed 去向；**没有一条静默消失**（反例：候选/Claim 悄悄不见）---
    dispositions = list(output.claim_narrative_dispositions)
    check_eq(len(dispositions), len(output.claims),
             "每条定稿 Claim 恰有一条去向记录（不多不少）")
    check_eq(len({d.claim_id for d in dispositions}), len(output.claims),
             "去向逐条唯一（同一 Claim 不得有两个去向）")
    selected_ids = {d.claim_id for d in dispositions if d.disposition == "selected"}
    in_text_ids = {cid for p in paragraphs for s in p.sentences for cid in s.claim_ids}
    check_eq(selected_ids, in_text_ids,
             "「selected」的那些正是正文真正引用的那些（去向与正文逐条对齐）")
    omitted = [d for d in dispositions if d.disposition == "omitted"]
    check(all(d.reason_code in NS.CLAIM_OMISSION_REASONS for d in omitted),
          "omitted 的去向码全部取自已登记的封闭词表（不自己发明措辞）")

    def _bare(text: str) -> str:
        return "".join(ch for ch in str(text)
                       if ch not in NS.JOIN_RESIDUE_PUNCTUATION and not ch.isspace())

    unique_lost = [d.claim_id for d in omitted
                   if not any(_bare(claims[d.claim_id].text)
                              and _bare(claims[d.claim_id].text) in _bare(claims[c].text)
                              for c in selected_ids)]
    check(not unique_lost,
          "**反例：静默消失**。每条未写进正文的 Claim，其字面都**逐字包含**在某条被选中的"
          f"Claim 里（去掉标点后是子串）——删它不丢任何字面；否则就是被静默抹掉的独有事实："
          f"{unique_lost[:3]}")

    # --- 接缝：真实正文全部合规；把同一个接缝前面的逗号去掉，立刻被判病句（正反例都用真数据）---
    def _seams_of(paras):
        out = []
        for para in paras:
            for sentence in para.sentences:
                texts = [claims[c].text for c in sentence.claim_ids]
                if len(texts) >= 2:
                    out.append((sentence, NS.unseparated_seam_positions(sentence.text, texts)))
        return out

    open_seams = [(s.index, pos) for s, pos in _seams_of(paragraphs) if pos]
    check(not open_seams,
          f"正文里没有「衔接语直接贴在下一条 Claim 前面」的病句接缝（实得 {open_seams[:3]}）")
    _seam_sentence = next((s for s, _pos in _seams_of(paragraphs)), None)
    check(_seam_sentence is not None, "正文里确有**多 Claim 组织出来**的句子（接缝判据有物可判）")
    _seam_texts = [claims[c].text for c in (_seam_sentence.claim_ids if _seam_sentence else ())]
    _glued = next((_seam_sentence.text.replace("，" + c, c, 1) for c in NS.CONNECTORS
                   if "，" + c in _seam_sentence.text), "") if _seam_sentence else ""
    check(bool(_glued) and NS.composed_organization_defect(
        text=_glued, authorized_texts=_seam_texts) == NS.COMPOSED_DEFECT_UNSEPARATED_SEAM,
        "反例：把真实正文里第一个接缝前面的那个逗号去掉（= r7b 现场的病句形态），同一句"
        "立刻被判 `unseparated_seam`——判据抓的是接缝的**第一个字符**，不是某段字面")
    check(NS.composed_organization_defect(
        text=_seam_sentence.text, authorized_texts=_seam_texts) is None,
        "正例：真实正文的那一句在同一判据下是合规的（不是把它自己一起拒掉）")

    # --- 缺口：如实、可读、不泄露内部字段 ---
    unresolved = list(output.result.unresolved)
    check(len(unresolved) >= 45,
          f"缺口逐条如实列出（实得 {len(unresolved)} 条，含期间缺口与全部未覆盖 aspect）")
    check_eq(output.result.status, "SECTION_BLOCKED",
             "本节状态是 SECTION_BLOCKED（有 blocked aspect 影响关键范围），"
             "**不得**为好看改绿")
    check_eq(output.result.section_result_id, REPLAY_COMPANY_SECTION_RESULT,
             "SectionResult 身份在本批代码+夹具下可复算")
    leaked = [f"{u.unresolved_id}:{field}" for u in unresolved for field in FORBIDDEN_GAP_FIELDS
              if field in str(u.detail)]
    check(not leaked, f"可见缺口文案不泄露内部字段赋值：{leaked[:3]}")
    check(all(str(u.detail).strip() for u in unresolved), "每条缺口都有可读 detail")
    states = sorted({str(u.state) for u in unresolved})
    check(set(states) <= set(GAP_STATES),
          f"缺口状态全部取自已登记的 canonical 状态词表（实得 {states}）——"
          "「未取得 / 检索后未找到 / 不适用」三态都**不是**「已覆盖」")
    period_gaps = [u for u in unresolved if str(u.reason_code) == "period_unresolved"]
    check_eq(len(period_gaps), 2,
             "两条期间缺口（经营模式与法律风险各有一条事实没有可核验的显式期间）")
    check(all("期间" in str(u.detail) for u in period_gaps),
          "期间缺口的文案讲的是「期间无法证明」，不拿基准日顶替")
    aspect_gaps = {str(u.detail).split("」", 1)[0].split("「", 1)[-1]: u
                   for u in unresolved if "aspect「" in str(u.detail)}
    check(len(aspect_gaps) >= 40,
          f"未覆盖 aspect 逐条成缺口（实得 {len(aspect_gaps)} 条，不是只在计数里）")
    check(all(str(u.reason_code) in ("blocked", "not_found", "unresolved")
              for u in aspect_gaps.values()),
          "aspect 缺口的原因码取自 `_reason_code_for` 的封闭值域（blocked 不得被改写）")
    check(any(str(u.reason_code) == "blocked" for u in aspect_gaps.values()),
          "其中确有 blocked 的 aspect：它原样保留为 blocked")
    blocked_effects = sorted({str(effect) for u in unresolved for effect in u.blocking_effects})
    check("SECTION_BLOCKED" in blocked_effects,
          "阻塞效果是结构化字段（SECTION_BLOCKED 逐条给出，不只在散文里）")

    # ============================================================ **读者面自相矛盾已消除**
    # 定点批之前：`legal_rep` 这一 aspect 在材料侧是 blocked 缺口，正文里却有一条
    # 「公司法定代表人为曾毓群」的已定稿 Claim——读者同时读到「是」与「缺」。
    # 按冻结的高风险硬事实规则，该候选没有预验证事实身份（`fact_id=None`），路径 B 不得授权：
    # 因此**正文撤回、缺口保留**。一条不许留、一条不许少。
    legal_rep_gap = [u for u in unresolved
                     if "company_identity_basic.legal_rep" in str(u.detail)]
    legal_rep_claims = [c for c in output.claims if "法定代表人" in str(c.text)]
    check(len(legal_rep_gap) == 1 and not legal_rep_claims,
          "`company_identity_basic.legal_rep` 仍是 blocked 缺口（一条不少），"
          f"但正文里「法定代表人」已定稿 Claim 为 {len(legal_rep_claims)} 条（撤回）："
          "同一个事实不得既写成已授权、又挂成缺失")
    check(not any("法定代表人" in str(p_sentence.text)
                  for p in output.narrative.paragraphs for p_sentence in p.sentences),
          "正文任何一句都不得出现「法定代表人」（撤回是在生成侧，不是门后替换字符串）")
    # 材料与事实仍是两条轴：缺口由**材料侧**状态决定，不因为「正文撤回了」就被改绿/删除。
    check(str(legal_rep_gap[0].state) in GAP_STATES if legal_rep_gap else False,
          "该缺口的 canonical 状态原样保留（撤回正文不等于把 aspect 改成已覆盖）")

    # --- 门前草稿单元上的高风险表面扫描（本模块**报告**，不当通过）---
    claim_texts = {str(c.text) for c in output.claims}
    unit_only = [u.draft_unit_id for u in draft.narrative_draft_units
                 if str(u.text).rstrip(NS.SENTENCE_TERMINATORS).strip() not in claim_texts]
    # 草稿单元**不在**路径 B 扫描面内（它只扫候选与 proposal）。因此必须证明「没扫到」不是
    # 一个真入口：单元文本根本走不到正文里去。三条各自独立的屏障，前两条用真实数据复核，
    # 第三条（不构造成候选）由 `evals/test_m930_3_risk_surface_semantics.py` 的反例覆盖。
    unit_texts = [str(u.text) for u in draft.narrative_draft_units]
    prose_text = "".join(s.text for p_nar in output.narrative.paragraphs for s in p_nar.sentences)
    check(unit_only and len(unit_only) == len(draft.narrative_draft_units),
          f"公司节草稿单元 {len(draft.narrative_draft_units)} 个，全部文本**不来自**任何定稿 "
          "Claim（单元是 context 定位锚点，不是 Claim 的副本）")
    check(not any(t and t in prose_text for t in unit_texts),
          "**屏障 1**：没有任何草稿单元文本出现在正文里（单元不是正文来源）")
    organizer_face = NO.build_organizer_messages(
        claims=tuple(output.claims),
        accepted_context_bindings=tuple(b for b in chain.acceptance.accepted_bindings
                                        if b.support_semantics == "context"),
        unresolved=tuple(unresolved), writing_spec=object(),
        presentation_profile=object(),
        identity={"task_id": "t", "section_id": "s", "section_draft_id": "d",
                  "draft_revision": str(draft.draft_revision)})[0]["content"]
    check(not any(t and t in organizer_face for t in unit_texts),
          "**屏障 2**：组织器的输入面里没有任何草稿单元文本"
          "（context 绑定行只有身份字段，模型看不到单元文本）")
    details.append(
        f"NOTE 公司节草稿单元 {len(draft.narrative_draft_units)} 个："
        "路径 B 扫描面只含候选与 proposal，**不扫**草稿单元——但单元文本既不进正文"
        "（屏障 1，真实数据复核），也不进组织器输入面（屏障 2，真实数据复核），"
        "且合并不把单元变成候选（屏障 3，通用反例）。因此「硬事实从单元溜进正文」这条入口"
        "在结构上不可达，不是一个未覆盖的边界。")
    details.append(
        f"NOTE 公司节量表：原束 {len(rec.candidate_ids)} 条 → `{decision.version}` 排除 "
        f"{len(decision.excluded)} 条（逐原因：高风险表面 "
        f"{sum(1 for e in decision.excluded if REPLAY_COMPANY_REJECTION_KIND in e.reasons)}、"
        f"独立支撑结论 {len(unproven_of)}、期间/来源角色 {len(history_of)}、"
        f"材料范围不合格 {len(ineligible_dual)}；其中 {len(dual)} 条带两个原因）→ 幸存 "
        f"{len(decision.surviving_candidate_ids)} 条 → 定稿 Claim {len(output.claims)} 条；"
        f"支撑提案 {len(draft.proposed_support_refs)} 条、接受边 "
        f"{len(chain.acceptance.accepted_bindings)} 条。"
        f"**排除面里有 {len(descriptive_excluded)} 条是零表面集的描述性候选**——"
        "这是本批的内容代价，逐条原因与成员身份都在案。")
    details.append(
        f"NOTE 缺口状态分布 {states}；阻塞效果词表 {blocked_effects}；"
        f"aspect 缺口 {len(aspect_gaps)} 条、期间缺口 {len(period_gaps)} 条、"
        f"未覆盖项合计 {len(unresolved)} 条。")
    details.append(f"NOTE 公司节 persistence={output.persistence!r}（未注入 section store）。")
    return {"case": case, "replay": replay, "output": output, "chain": chain,
            "draft": draft, "claims": claims, "bindings": bindings}


def _check_financial_replay(check, check_eq, details, recorded_fin_nar) -> dict:
    """§2 财务半场：Δpp 独立复算 + 同链重放 + 表/正文的唯一呈现。"""
    fin_case = _build_financial_case()
    scan = fin_case["scan"]
    artifact = fin_case["artifact"]
    check_eq(len(scan.facts), 25,
             "财务权威扫描出 25 条事实（r7b 记录 24 条 + 本批新增的 1 条 Δpp 派生事实）")
    check_eq(scan.facts[0].fact_id, DELTA_FACT_ID,
             "新增的派生事实落在 f1（它的 fact_id 字典序小于全部 metric_*）")

    # --- **独立复算**：Δpp 只能从两期**未舍入**原值算出来 ---
    derived_proj = [f for f in artifact.facts if f.kind == "derived"]
    check_eq(len(derived_proj), 1, "artifact 里恰有一条 `derived` 事实")
    proj = derived_proj[0]
    check_eq(proj.fact_id, DELTA_FACT_ID, "派生事实的身份是确定性函数（code + 两期）")
    check_eq(proj.label, DELTA_SHORT, "派生事实的标签")
    check_eq(proj.formula_version, DELTA_FORMULA_VERSION, "派生事实绑定公式版本")
    check_eq(proj.period, DELTA_PERIOD, "期间是复合记号（本期|上期）——它不是任何单一期间")
    check_eq(tuple(proj.input_periods), ("2024-12-31", "2025-12-31"),
             "两期输入血缘按 (上期, 本期) 升序")
    check_eq(tuple(proj.derived_from),
             ("metric_SOLV_DEBT_RATIO_2024-12-31", "metric_SOLV_DEBT_RATIO_2025-12-31"),
             "血缘指向的正是那两个年度的同指标事实")
    check_eq(len(tuple(proj.citations)), 2,
             "完整引用集恰好两条（数值是**两条**权威记录的函数，就得有两份可回查引用）")
    check(dict(proj.citation) in [dict(c) for c in proj.citations],
          "主引用是这两条之一（不另立第三条）")
    check_eq(proj.status, "CALCULATED_EXACT",
             "只有两期都精确口径才生成（任一期代理口径即不生成，因此不存在「代理口径的 Δpp」）")
    check(not proj.reason_code and not proj.note, "派生事实自己不携带口径限定语或原因码")
    check_eq(proj.display, DELTA_DISPLAY, "展示值就是「-3.3个百分点」（含单位与方向）")
    check_eq(proj.period_label, DELTA_PERIOD_LABEL, "期间表达（读者可见的「本期较上期」措辞）")
    delta_surface = str(scan.facts[0].text)
    check(proj.period_label in delta_surface and proj.display in delta_surface
          and delta_surface.endswith("。"),
          f"权威表面由期间表达与展示值拼出（实得 {delta_surface!r}），不是手写文本")

    from financial_v2 import derived_facts as DF
    prior_raw = Decimal(proj.input_raw_texts[0])
    current_raw = Decimal(proj.input_raw_texts[1])
    value = Decimal(proj.value_text)
    check_eq(DF.delta_pp(prior_raw, current_raw), value,
             "**独立复算**：`(本期 raw − 上期 raw) × 100` 逐位等于 artifact 里的 `value_text`")
    check_eq(proj.display, DF.render_delta_pp(value),
             "展示值 = 计算结果按 2 位小数四舍五入后渲染（舍入只在展示时发生）")

    # **反例：不得拿已舍入的显示值相减**。两个输入指标在表里是 `65.24%` / `61.94%`，相减恰好
    # 也是 −3.30 → 渲染后与本事实**看起来一样**。因此判据不能看渲染结果，只能看数值与血缘。
    metrics = {f.fact_id: f for f in artifact.facts if f.code == DELTA_SOURCE_CODE}
    check_eq(sorted(metrics),
             ["metric_SOLV_DEBT_RATIO_2023-12-31", "metric_SOLV_DEBT_RATIO_2024-12-31",
              "metric_SOLV_DEBT_RATIO_2025-12-31", "metric_SOLV_DEBT_RATIO_2026-03-31"],
             "表里有四个期间的资产负债率事实（本事实只取相邻两个**完整年度**期末）")
    shown = {p: _num(metrics[f"metric_SOLV_DEBT_RATIO_{p}"].display)
             for p in ("2024-12-31", "2025-12-31")}
    # **如实登记的一处不对称**：指标投影的 `value_text` 就是**显示面**的百分数（`65.24`），
    # 不是未舍入原值；那一对未舍入的数只存活在派生事实自己的 `input_raw_texts` 里。因此「算的是
    # 原值还是显示值」这件事**只能**由 `input_raw_texts` + `derived_from` 证明，指标行证明不了。
    check_eq([Decimal(metrics[f"metric_SOLV_DEBT_RATIO_{p}"].value_text)
              for p in ("2024-12-31", "2025-12-31")],
             [shown["2024-12-31"], shown["2025-12-31"]],
             "指标投影的 `value_text` 等于它自己的显示值（同一条指标事实只留存了**舍入后**那一侧）")
    check([prior_raw * 100, current_raw * 100] != [shown["2024-12-31"], shown["2025-12-31"]],
          "血缘里的两期**未舍入原值**换算到百分数后仍与指标行的可读文本不同——差在舍入位："
          f"{prior_raw * 100} / {current_raw * 100} vs "
          f"{shown['2024-12-31']} / {shown['2025-12-31']}")
    rounded_delta = shown["2025-12-31"] - shown["2024-12-31"]
    check(value != rounded_delta,
          "**反例：已舍入输入**。本事实的数值与「两个显示值相减」的结果**不相等**"
          f"（{value} vs {rounded_delta}）")
    check(value != value.quantize(Decimal("0.01")),
          "本事实的数值在 2 位小数之外仍有有效位——它**不可能**是两个 2 位小数之差的产物")
    check_eq(DF.render_delta_pp(value), DF.render_delta_pp(rounded_delta),
             "但两者**渲染后看起来一样**：这条巧合说明「按显示值核对」不足以证明计算口径，"
             "只有未舍入原值 + 两期引用血缘才能区分")

    fin_replay = _drive_financial(fin_case)
    _print_financial(fin_replay, fin_case)
    fin_out = fin_replay["output"]
    fin_draft = fin_out.draft
    fin_calls = tuple(c["prompt_version"] for c in fin_replay["client"].calls)
    check_eq(fin_calls, (PW.NARRATION_PROMPT_VERSION,
                         NO.NARRATIVE_ORGANIZER_PROMPT_VERSION),
             "财务半场只发两次调用：1 次提案（重映射后的留存返回）+ 1 次替身组织")
    check_eq(fin_replay["audit"]["surface_mismatch"], [],
             "别名重映射逐条对账：0 条表面不符")
    check_eq((fin_replay["audit"]["byte_equal"], fin_replay["audit"]["punctuation_only"]),
             (20, 4),
             "20 条候选与重映射后的权威表面**逐字节相同**；4 条代理口径候选只差限定语前一个"
             "标点（那一轮模型自己的断句），句本体与限定语逐字相同")
    check_eq(fin_replay["audit"]["recorded_candidates"], 24, "重映射的输入是 r7b 记录的 24 条候选")
    check_eq(len(fin_draft.claim_candidates), 25,
             "新修订 25 条候选（24 条重映射 + 1 条新增的派生事实候选）")
    check_eq(fin_draft.draft_revision, REPLAY_FIN_REVISION,
             "财务 draft 修订在本批代码+夹具下可复算（改动前它逐字等于 r7b 记录值；"
             "两个版本串前进后身份必然重算，归因见 `_check_version_advance_attribution`："
             "用改动前的版本串重算同一批输入，逐字回到记录值）")
    check_eq(len(fin_out.aggregate_decisions), 25, "25 个聚合绑定决定（逐 subject 恰一条）")
    check_eq(len(fin_out.entailment_decisions), 25, "25 个蕴含决定（仅 factual candidate）")
    check_eq(len(fin_out.acceptance.accepted_bindings), 25, "25 条接受边")
    check_eq(len(fin_out.acceptance.rejected_subjects), 0, "门后没有被拒 subject")

    # --- 派生事实的完整链 ---
    derived_claims = [c for c in fin_out.claims if c.text == _derived_text(scan.facts[0])]
    check_eq(len(derived_claims), 1, "派生事实恰好定稿为 1 条 Claim")
    derived_claim = derived_claims[0]
    derived_cands = [c for c in fin_draft.claim_candidates if c.claim_text == derived_claim.text]
    check_eq(len(derived_cands), 1, "该 Claim 回指恰 1 条候选")
    derived_cand = derived_cands[0]
    decision = next(d for d in fin_out.aggregate_decisions
                    if str(d.subject_id) == str(derived_cand.candidate_id))
    entailment = next(e for e in fin_out.entailment_decisions
                      if str(e.claim_candidate_id) == str(derived_cand.candidate_id))
    binding = next(b for b in fin_out.acceptance.accepted_bindings
                   if str(b.binding_subject_id) == str(derived_cand.candidate_id))
    check_eq(decision.result, "pass", "聚合绑定决定通过（逐边结果全 pass）")
    check(all(str(e.result) == "pass" for e in decision.edge_results),
          "逐边结果全 pass（不是「整体跑完就算过」）")
    check_eq(entailment.verdict, "entailed", "蕴含决定通过")
    check_eq(entailment.authorization_path, "path_a_prevalidated",
             "派生事实走的是**路径 A**（预验证权威），不是路径 B 的材料蕴含")
    check_eq(str(entailment.binding_decision_id), str(decision.binding_decision_id),
             "蕴含决定绑定唯一通过的聚合决定（同一条，不另立）")
    check_eq(str(binding.binding_decision_id), str(decision.binding_decision_id),
             "接受边引用同一个聚合决定")
    check_eq(str(binding.support_set_digest), str(decision.support_set_digest),
             "接受边与聚合决定共用同一个 support-set digest")
    check_eq(str(binding.binding_subject_id), str(derived_cand.candidate_id),
             "接受边的绑定主体是这条候选")
    check_eq(binding.support_semantics, "factual", "派生事实的接受边是 factual（不是 context）")
    check_eq(binding.financial_fact_id or binding.fact_id, DELTA_FACT_ID,
             "接受边指向的正是那条派生财务事实")
    ref_periods = sorted({
        str(getattr(c, "period", "") or "") for c in derived_claim.citation_refs})
    check(1 <= len(derived_claim.citation_refs) <= 2,
          "派生事实的 Claim 带可回查的引用（artifact 侧两期引用齐备；Claim 侧条数见 NOTE）")
    details.append(
        f"NOTE 派生事实 Claim 的引用期间 {ref_periods}；artifact 侧 `citations` "
        f"{len(tuple(proj.citations))} 条、`input_periods` {tuple(proj.input_periods)} 两期齐备"
        "（「缺双期引用」的反例由 `evals/test_m930_3_derived_delta_fact.py` 覆盖）。")

    # --- 正文：一段由派生事实承载的分析 + 4 张表 ---
    fin_paras = list(fin_out.narrative.paragraphs)
    check_eq(len(fin_paras), 1, "final Narrative 恰好 1 段正文（r7b 记录是 0 段）")
    fin_sentences = [s for p in fin_paras for s in p.sentences]
    # 句末那一个 `。` 由**组织器**给（`norg-4` 的输入面写明「句末标点是你分句的地方」）：
    # 它只加句末标点，不改 Claim 的任何字面——因此这里比的是「Claim 文本逐字在场 + 句末一个句号」，
    # 而不是「正文 == Claim 文本」（那会把组织器的本职当成越权）。
    check_eq([s.text for s in fin_sentences], [derived_claim.text + "。"],
             "该段正文就是那条派生事实的 Claim 文本（逐字在场 + 句末一个句号；"
             "不是把表格 Claim 再抄一遍）")
    check_eq([cid for s in fin_sentences for cid in s.claim_ids], [derived_claim.claim_id],
             "该句子由那条 Claim 承载（引用可逐句回查）")
    check_eq(len(fin_out.narrative.tables), RECORDED_FIN_TABLES, "财务表数不变（4 张）")

    mine_tables = [{"header": list(t.header), "unit": t.unit, "period": t.period,
                    "rows": [{"label": r.label, "cells": list(r.cells)} for r in t.rows]}
                   for t in fin_out.narrative.tables]
    ref_tables = [{"header": list(t["header"]), "unit": t["unit"], "period": t["period"],
                   "rows": [{"label": r["label"], "cells": list(r["cells"])}
                            for r in t["rows"]]}
                  for t in recorded_fin_nar["tables"]]
    check_eq(mine_tables, ref_tables,
             "4 张表的表头 / 单位 / 期间表达 / 行标签 / 逐格数值与 r7b 记录**逐格相同**"
             "（本批不改表格内容；**表题 `caption` 刻意不在比较面内**——它在本批前进了一次："
             "四张表此前表题逐字相同，现由 `financial_table_caption(unit, basis)` 逐表算出，"
             "见 `evals/test_m930_3_financial_face.py` §4。记录里的旧表题作为历史产物原样保留，"
             "不回溯改写）")
    all_cells = [cell for t in fin_out.narrative.tables for r in t.rows for cell in r.cells]
    check_eq(len(all_cells), RECORDED_FIN_CELLS, "4 张表共 24 个数值格")
    # 同一指标行内两个期间取到**同一个值**是正常的时间序列（真实数据里就有），不算重复呈现；
    # 真正的重复呈现是「同一数值被塞进**不同**指标行」。
    seen_cell: dict[str, tuple[str, str]] = {}
    cross_row_dups: list[dict] = []
    for table in fin_out.narrative.tables:
        for row in table.rows:
            for cell in row.cells:
                host = (table.table_id, row.row_id)
                if cell in seen_cell and seen_cell[cell] != host:
                    cross_row_dups.append({"cell": cell, "first": seen_cell[cell],
                                           "again": host})
                else:
                    seen_cell.setdefault(cell, host)
    check(not cross_row_dups,
          f"同一数值不跨**不同指标行**重复呈现：{cross_row_dups[:2]}")
    same_row_dups = sorted({cell for cell in all_cells if all_cells.count(cell) > 1})
    details.append(f"NOTE 表内跨期同值（同一指标行内的不同期间）：{same_row_dups}"
                   "——真实数据里同一指标两期取到同一个值，不是重复呈现。")
    row_claims = [cid for t in fin_out.narrative.tables for r in t.rows for cid in r.claim_ids]
    check_eq(len(row_claims), len(set(row_claims)),
             "24 条表格 Claim 各只呈现**一次**（逐**行**核对，唯一呈现）")
    check(derived_claim.claim_id not in row_claims,
          "承载正文的派生事实 Claim **不进**任何表格（它承载正文，不与表格 Claim 争同一格）")
    check_eq(len(fin_out.claims), 25, "门后定稿 Claim = 24 条表格 Claim + 1 条派生事实 Claim")
    ratio_row = next(r for t in fin_out.narrative.tables for r in t.rows
                     if r.label == "资产负债率")
    check_eq(list(ratio_row.cells),
             [metrics[f"metric_SOLV_DEBT_RATIO_{p}"].display
              for p in ("2023-12-31", "2024-12-31", "2025-12-31", "2026-03-31")],
             "「资产负债率」那一行的四格就是那四个指标事实的**显示值**（读者看到的数与结论"
             "引用的数同源；本事实的数值刻意不与它们相减）")

    # --- 财务缺口：诚实、不泄露 ---
    fin_unresolved = list(fin_out.result.unresolved)
    check_eq(len(fin_unresolved), 4, "财务节 4 条缺口（附注 + 一个空主题 + 两个无产出问题）")
    check_eq(fin_out.result.status, "SECTION_BLOCKED",
             "财务节状态 SECTION_BLOCKED（不得为好看改绿）")
    fin_leaked = [f"{u.unresolved_id}:{field}" for u in fin_unresolved
                  for field in FORBIDDEN_GAP_FIELDS if field in str(u.detail)]
    check(not fin_leaked, f"财务缺口文案不泄露内部字段赋值：{fin_leaked[:3]}")
    check(any("附注" in str(u.detail) for u in fin_unresolved),
          "附注缺口如实说明（不是「公司未披露」）")
    details.append(f"NOTE 财务节 persistence={fin_out.persistence!r}、"
                   f"llm_calls={fin_out.llm_calls}（本重放未注入 section store）。")
    return {"case": fin_case, "replay": fin_replay, "output": fin_out}


def _revision_with_versions(task, authority, draft, attempt, policy_version, prompt_version,
                            prose_digest: str = ""):
    """用**给定的**两个版本串（与草稿层摘要）重算该 draft 的修订：身份体其余字段逐项来自现场。

    这是归因探针的函数形态：只有这三个量是变量，别的输入一个字都不动，因此「重算回到旧值」
    只可能由它们解释。

    `prose_digest` 默认空串 = **不带草稿层**的形态（`pw-14` 及更早、以及本重放的现场形态：留存
    的 r7b 返回没有草稿层键）。非空时才走 `pw-15` 新增的那一维。
    """
    manifest = draft.material_manifest
    return NS.derive_draft_revision(
        natural_prose_digest=str(prose_digest or ""),
        task_id=task.task_id, section_id=task.section_id, company_id=authority.company_id,
        report_as_of=authority.report_as_of, contract_version=authority.contract_version,
        contract_fingerprint=authority.contract_fingerprint,
        writer_policy_version=policy_version, prompt_version=prompt_version,
        model_policy=PW.MODEL_POLICY_PROVIDER_DEFAULT, manifest_id=manifest.manifest_id,
        manifest_fingerprint=manifest.fingerprint(), attempt=attempt)


def _check_version_advance_attribution(check, check_eq, company, financial) -> None:
    """**身份为什么前进：可执行归因**（不是一句声明）。

    版本串那一批（`pw-13` → `pw-14`、`proposals-9` → `proposals-10`）、草稿层那一批
    （`pw-14` → `pw-15`、`proposals-10` → `proposals-11`）与 `pw-16` / `proposals-12` 那一批
    三次前进共用本函数。断言：
    (a) 换成改动前的两个串重算，公司侧逐字回到旧钉子、财务侧逐字回到 r7b **记录值**；
    (b) 用**当前**两个串重算，逐字等于现场身份——因此「身份前进」的充分且必要解释就是这两个串；
    (c) 两个串**都**参与：只回退其中一个，得不到旧值。内容侧一条没变（条数、文本、段落、
    接受边与缺口都在各自半个里另行断言）。

    **本函数只管版本串那一维，而且这一点本身要可执行地说清**（否则会拿它去解释它没测到的
    东西）：`narr-7` → `narr-8` 那次 schema 版本前进**也**让每一条 `ccand_*` 换身（因为
    `ClaimCandidate.identity_body` 含 `schema_version`），但**不动** draft 修订
    （`derive_draft_revision` 的身份体不含 `schema_version`）。因此本节 (a) 在 schema 版本
    已经前进之后**仍然逐字回到老钉子**，这既是版本串维度的归因，也是「schema 版本不在修订
    身份体里」的**证据**；候选身份那一维另由 (d) 单独证。

    **`pw-15` 新增的那一维本重放不覆盖，这一点必须可执行地写清楚**（否则 (b) 就成了拿没测到
    的东西当结论）：现场草稿层为空（留存返回是 `proposals-10` 形态，没有该键），因此摘要为空串、
    不进身份体，(b) 成立。函数因此还断言现场草稿层确实为空，并给出一条**反例**——同样两个
    **当前**版本串，换成非空草稿摘要，身份**必变**（这一维真的在身份里）。草稿层非空时的正例
    （同一批输入换一版草稿 ⇒ 另一个修订、候选 id 跟着走）由 `evals/test_demo_pack_writer.py`
    §25 覆盖，不在这里冒充已验。
    """
    comp_task, comp_auth = company["case"]["task"], company["case"]["authority"]
    fin_task, fin_auth = financial["case"]["task"], financial["case"]["authority"]
    comp_draft, fin_draft = company["draft"], financial["output"].draft
    comp_attempt = int(comp_draft.writer_attempt)

    def _rev(which, policy, prompt, prose_digest=""):
        if which == "company":
            return _revision_with_versions(comp_task, comp_auth, comp_draft, comp_attempt,
                                           policy, prompt, prose_digest)
        return _revision_with_versions(fin_task, fin_auth, fin_draft, 1, policy, prompt,
                                       prose_digest)

    check_eq(NS.natural_prose_draft_digest(comp_draft.natural_prose_draft), "",
             "本重放的草稿层为空：留存的 r7b 返回是 `proposals-10` 形态（没有草稿层键），"
             "因此 `pw-15` 新增的草稿摘要这一维**不参与**本重放的身份——下面「只用两个版本串"
             "就能解释身份」才是可执行事实，而不是跳过")
    check(_rev("company", PW.PACK_WRITER_POLICY_VERSION, PW.NARRATION_PROMPT_VERSION,
               "npdd_counterexample_not_empty")
          != comp_draft.draft_revision,
          "反例（`pw-15` 新增维）：同样两个**当前**版本串、只把草稿摘要换成一个非空值，"
          "身份**必变**——草稿层在场时身份还覆盖草稿内容；这一维由 "
          "`evals/test_demo_pack_writer.py` §25 端到端覆盖")

    for which, expected in (("company", PRE_BATCH_COMPANY_REVISION),
                            ("financial", PRE_BATCH_FIN_REVISION)):
        got = _rev(which, PRE_BATCH_POLICY_VERSION, PRE_BATCH_PROMPT_VERSION)
        check_eq(got, expected,
                 f"归因①：{which} 侧换成上一批的 `{PRE_BATCH_POLICY_VERSION}` / "
                 f"`{PRE_BATCH_PROMPT_VERSION}` 重算，逐字回到上一批的读数"
                 "（内容一条没变，身份只由这两个串解释）")
    for which, expected in (("company", R7B_ERA_COMPANY_REVISION),
                            ("financial", RECORDED_FIN_REVISION)):
        got = _rev(which, R7B_ERA_POLICY_VERSION, R7B_ERA_PROMPT_VERSION)
        check_eq(got, expected,
                 f"归因②：{which} 侧换成 r7b 那一轮真实 run 用的 `{R7B_ERA_POLICY_VERSION}` / "
                 f"`{R7B_ERA_PROMPT_VERSION}` 重算，逐字回到"
                 + ("当时的复算读数" if which == "company" else " r7b **记录值**")
                 + "——两侧各自回到自己的锚点，说明这条链的每一段前进都只由版本串解释；"
                 "同时这也是「schema 版本**不**在修订身份体里」的证据："
                 "`narr-7`→`narr-8` 已经前进，这两条仍逐字回到老读数")
    for which, expected in (("company", REPLAY_COMPANY_REVISION),
                            ("financial", REPLAY_FIN_REVISION)):
        got = _rev(which, PW.PACK_WRITER_POLICY_VERSION, PW.NARRATION_PROMPT_VERSION)
        check_eq(got, expected,
                 f"{which} 侧用**当前**两个版本串重算，逐字等于现场身份")
    check(_rev("company", PRE_BATCH_POLICY_VERSION, PW.NARRATION_PROMPT_VERSION)
          != PRE_BATCH_COMPANY_REVISION,
          f"两个串**都**参与身份：只回退 `policy_version` 到 {PRE_BATCH_POLICY_VERSION}、"
          f"`prompt_version` 仍是当前串，得不到旧钉子（不是「只有一个串起作用」）")
    check(_rev("financial", PW.PACK_WRITER_POLICY_VERSION, PRE_BATCH_PROMPT_VERSION)
          != PRE_BATCH_FIN_REVISION,
          "只回退 `prompt_version`、`policy_version` 仍是当前串，同样得不到上一批的读数")
    check(_rev("company", R7B_ERA_POLICY_VERSION, PW.NARRATION_PROMPT_VERSION)
          != R7B_ERA_COMPANY_REVISION
          and _rev("company", PW.PACK_WRITER_POLICY_VERSION, R7B_ERA_PROMPT_VERSION)
          != R7B_ERA_COMPANY_REVISION,
          "r7b 那一对同样**两个串都参与**：只回退其中一个也回不到当时的读数")

    # (d) 本批新增的那一维：schema 版本进的是**候选**身份体，不是修订身份体。
    # 这一条必须真跑在一条**现场**候选上，而不是拿 `NARRATIVE_SCHEMA_VERSION` 的值说事：
    # 先在真身份口径上认一下「候选 id 就是它 identity_body 的内容寻址 id」，再只换
    # `schema_version` 重算——身份必变。两条合起来才排除了「换的是别的东西」。
    cand = comp_draft.claim_candidates[0]
    cand_body = dict(cand.identity_body())
    check_eq(NS.content_id("ccand_", cand_body), cand.candidate_id,
             "候选身份 = 其 `identity_body()` 的内容寻址 id（下面这条反例因此是在**真**身份口径上做的）")
    check(NS.content_id("ccand_", dict(cand_body, schema_version="narr-7"))
          != cand.candidate_id,
          "反例（本批新增维）：只把 `schema_version` 换回 `narr-7`、其余字段一字不动，"
          "候选身份**必变**——`ClaimCandidate.identity_body` 确实含 schema 版本，"
          "因此本批 `ccand_*` 的前进有**两个独立原因**（版本串 + schema 版本），"
          "不能只归给版本串")
    check(NS.content_id("ccand_", dict(cand_body, schema_version="narr-7"))
          != NS.content_id("ccand_", dict(cand_body, schema_version="narr-9")),
          "两个不同的 schema 版本串给出两个不同的候选身份（不是「只要不是当前串就同归于一个值」"
          "那种把版本串当布尔用的口径）")


def _check_srsc2_attribution(check, check_eq, details) -> None:
    """**`srsc-2` / `srsc-3` 为什么让身份与条数前进：可执行归因**（不是一句声明）。

    与 §2.3 的版本串归因**形状相同、机制不同**：§2.3 换的是两个版本串（内容一字未动，
    身份重算）；这里换的是**判据本身**——把这一族判据的两侧同时打回空集（候选侧
    `_path_b_unproven_current_state` 返回空表、句读回 `verify_finalized_current_state_scope`
    变成 no-op），其余输入一个字都不动（**版本串也一个不动**——两个读数用的是同一批版本串，
    因此这里证明的差异只能是这一族判据造成的，版本串那一维由 §2.3 各自证明）。若身份、条数、
    段落数**逐字回到**本组钉子（`PRE_SRSC2_*` = 同一批版本串下把这一族判据两侧打回空集的读数），
    那么「公司侧这一族判据造成的全部差异」的充分且必要解释就是它，不是本批还有别的
    东西在动。`srsc-3` 是 `source_role_scope.current_state_support_is_extractive` 内部新加的
    一条分支，而函数入口在被打空的 `_path_b_unproven_current_state` 里，因此回滚读数把
    `srsc-2` 与 `srsc-3` **一起**移出——本组钉子因此是「这一族判据全不在场」的读数。

    **只打一侧不算归因，而且会被当场抓住**：这两侧是**各自实现**的（候选侧判提案边、
    句读回判定稿 Claim 与它自己那份完整 factual 接受集），不是同一个函数的两处调用。
    因此这一节还断言：只把候选侧打回空集时，句读回**必须抛**，且抛出的逐条记录里
    带 `unproven/not_extractive_in_any_current_source`——它自己重算出了同一批候选。
    这既是归因的一部分（说明差异不可能只由一侧解释），也是一条独立的防御纵深证据。

    这一节**不**把回滚读数当交付物：它只证明差异的来源，正文与缺口仍以本批读数为准。
    """
    candidate_side = PW._path_b_unproven_current_state
    final_side = PW.verify_finalized_current_state_scope
    no_unproven = lambda **_kw: {}  # noqa: E731 —— 打桩就该是一行，它不是一个判据

    # (a) **只打候选侧**：句读回必须自己发现并当场停。这是「两侧各自实现」的可执行证据。
    only_candidate: object = None
    PW._path_b_unproven_current_state = no_unproven
    snapshot = _patch_fixture_identity()
    try:
        only_candidate = _drive_company(
            _build_company_case(), proposal_wire=REPLAY_WIRE,
            replay_source=REPLAY_SOURCE)["output"].result.section_result_id
    except Exception as error:  # noqa: BLE001 —— 这里要的就是「它抛了」，类型另行断言
        only_candidate = error
    finally:
        _restore_fixture_identity(snapshot)
        PW._path_b_unproven_current_state = candidate_side
    check(isinstance(only_candidate, Exception),
          "只把 `srsc-2` 的**候选侧**打回空集时，句读回当场停下（不得静默继续组装 "
          "SectionResult）——两侧是各自实现的两道门，不是同一条判据的两处调用")
    check(isinstance(only_candidate, Exception)
          and "not_extractive_in_any_current_source" in str(only_candidate),
          "句读回给出的逐条记录带 `unproven/not_extractive_in_any_current_source`："
          "它拿**自己的**那条读回路径重算出了同一批候选（不是把候选侧的结论抄下来）")

    # (b) **两侧同时**打回空集：身份与条数逐字回到上一批的钉子。
    PW._path_b_unproven_current_state = no_unproven
    PW.verify_finalized_current_state_scope = lambda **_kw: None
    snapshot = _patch_fixture_identity()
    try:
        rolled = _drive_company(_build_company_case(), proposal_wire=REPLAY_WIRE,
                                replay_source=REPLAY_SOURCE)
    finally:
        _restore_fixture_identity(snapshot)
        PW._path_b_unproven_current_state = candidate_side
        PW.verify_finalized_current_state_scope = final_side

    rolled_draft = rolled["outcome"].draft
    rolled_out = rolled["output"]
    rolled_decision = rolled["outcome"].rejections[0].carve_out.decision
    check_eq(rolled_draft.draft_id, PRE_SRSC2_COMPANY_DRAFT_ID,
             "归因：两侧同时打回空集后，公司 draft 身份逐字回到本组钉子"
             "（同一批版本串，差异只由这一条判据解释）")
    check_eq(rolled_out.result.section_result_id, PRE_SRSC2_COMPANY_SECTION_RESULT,
             "归因：SectionResult 身份同样逐字回退")
    check_eq(rolled_draft.draft_revision, REPLAY_COMPANY_REVISION,
             "归因：draft 修订**在两侧读数下相同**——两个读数用同一批版本串、同一份草稿层，"
             "打回这条判据不改这两者，所以它不解释身份（身份那一维见 §2.3 的对照面）")
    check_eq((len(rolled_decision.excluded), len(rolled_decision.surviving_candidate_ids)),
             (PRE_SRSC2_EXCLUDED_CANDIDATES, PRE_SRSC2_SURVIVING_CANDIDATES),
             "归因：排除/幸存条数逐条回到打回这条判据之前（48 / 47）")
    check_eq(len(rolled_draft.proposed_support_refs), PRE_SRSC2_SUPPORT_PROPOSALS,
             "归因：支撑提案条数回到打回这条判据之前（94）")
    check_eq(len(rolled_out.claims), PRE_SRSC2_COMPANY_CLAIMS,
             "归因：门后 Claim 数回到打回这条判据之前（47）")
    check_eq(len(rolled_out.narrative.paragraphs), PRE_SRSC2_COMPANY_PARAGRAPHS,
             "归因：段落数回到打回这条判据之前（7 段）——因此 7 → 3 **不是**分段逻辑的变化")
    # (c) 打回前配对数非空：§2.4 那条一致性断言因此不是空转（本批为空是读数，不是跳过）。
    check_eq(len(_near_duplicate_pairs(rolled_out.claims)), PRE_SRSC2_NEAR_DUPLICATE_PAIRS,
             f"归因：打回这条判据之前的定稿集里有 {PRE_SRSC2_NEAR_DUPLICATE_PAIRS} 对近义候选，"
             "§2.4 的两侧一致性断言在那一组读数上**不是空转**")
    details.append(
        "NOTE §2.5 `srsc-2` 可执行归因：把这条判据的**两侧**同时打回空集（版本串与草稿层"
        "一个不动），公司侧的身份与"
        f"条数逐字回到本组钉子（{PRE_SRSC2_COMPANY_DRAFT_ID} / "
        f"{PRE_SRSC2_COMPANY_SECTION_RESULT} / {PRE_SRSC2_EXCLUDED_CANDIDATES} 排除 / "
        f"{PRE_SRSC2_SURVIVING_CANDIDATES} 幸存 / {PRE_SRSC2_SUPPORT_PROPOSALS} 边 / "
        f"{PRE_SRSC2_COMPANY_CLAIMS} Claim / {PRE_SRSC2_COMPANY_PARAGRAPHS} 段）；"
        "只打候选侧则被句读回当场拦下。这组读数**不是交付物**，它只证明差异的来源；"
        "它与早先记录的那一组身份串不同有**两个独立原因**，不得混读：版本串在前几批前进过"
        "（该维由 §2.3 的 `_check_version_advance_attribution` 证明），`acc-38` 又改了门后"
        "替身组织器的组织方式（该维由上面 `acc-38` 注记里的探针证明：打回逐接缝轮转 ⇒ 本组与"
        "现场两个 SectionResult 同时回到上一批的值）。")


def _recorded_dependency_field_counts() -> dict:
    """r7b 留存产物里，依赖**指纹**与依赖**向量**各出现多少次（逐文件、按原文计数）。

    读的是记录本身，不是某个 schema 常量：它要回答的是「历史身份还剩多少可读回」。
    """
    counts: dict[str, int] = {}
    for path in sorted(R7B.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        for field in ("dependency_fingerprint", "dependency_versions"):
            counts[field] = counts.get(field, 0) + text.count(f'"{field}"')
    return counts


def _dependency_identity_checks(check, check_eq) -> dict:
    """**两组依赖身份**的显式对账（第五步：收掉三条 CRASH，但不篡改历史）。

    本模块跑在 r7b 留存的字节上，但执行的是**当前代码**。历史身份（r7b 记录的 anp-6 / tmr-2
    向量）与当前身份（anp-8 / tmr-3）必须**分开**摆出来，且必须证明历史身份**不可执行**——不是「没试」，
    而是缺件：那一版规则实现既不在 git HEAD（`anp-1`）也不在工作区，且 18 键向量从未被持久化
    （`DEPENDENCY_VERSIONS_PERSISTED is False`，只留哈希）。因此本模块是**当前版本的有效重放**，
    **不是**对历史身份的复现；`REAL_DEP` 只在「重建记录产物」处被逐字读回，不贴到本批产出上。
    """
    declaration = dependency_identity_declaration()
    historical = declaration["historical"]
    current = declaration["current"]

    # (a) 历史身份**可还原**：按声明的差异键还原出来的向量，复算出的指纹逐字等于记录值。
    #     这一条若红，说明「只是版本号前进了一个键」这个前提已经不成立——不是夹具小毛病。
    check_eq(historical["dependency_fingerprint"], REAL_DEP,
             "r7b 记录的依赖身份可由「当前向量 + `R7B_IDENTITY_DELTA`」唯一还原"
             "（这才叫「历史身份可声明、可对账」；还原不出就不许说差异只有那一个键）")
    # (b) 差异面**恰好**是声明的那些键。多一个键 ⇒ 当场红，不得把新向量默默当成 r7b。
    check_eq(declaration["delta_keys"], declaration["declared_delta_keys"],
             "两组依赖向量的差异键集逐项等于声明值（差异面一变，这里先红，"
             "不许把新向量默默当 r7b 继续跑）")
    check_eq(declaration["delta_keys"], ["material_resolver", "navigation_profile"],
             "本批的实际差异只有 `navigation_profile`（r7b `anps-4/anp-6` → 当前 `anps-4/anp-8`）"
             "与 `material_resolver`（r7b `tmr-2` → 当前 `tmr-3`，本轮 M930-3 定点业务纠正①）两个键；"
             "两键之外再多一个，这里先红")

    # (c) 历史身份**不可执行**，且理由是可核查的缺件，不是一句托词。
    check(historical["executable"] is False,
          "历史身份被声明为**不可执行**（它可声明、可对账，但不能被读成「本次跑的就是 r7b 身份」）")
    check(bool(historical["unexecutable_reason"]),
          "历史身份不可执行附带了具体理由（缺哪一版代码、以及向量为何读不回来）")
    on_disk = _recorded_dependency_field_counts()
    check(on_disk.get("dependency_fingerprint", 0) > 0,
          "r7b 留存的产物里**有**依赖指纹（历史身份的这一半是可读回的）")
    check_eq(on_disk.get("dependency_versions", 0), 0,
             "r7b 留存的产物里**没有**任何 18 键依赖向量（逐文件扫 `dependency_versions` 得 0）："
             "历史身份连逐键值都读不回来，只能靠「当前向量 + 声明的差异」还原——"
             "这正是它**不可执行**的第二个缺件")

    # (d) 两组身份**确实是两组**，没有被合并成一个「r7b 身份」。
    check(current["dependency_fingerprint"] != REAL_DEP,
          "当前身份与历史身份是**两个不同的指纹**（`REAL_DEP` 与当前值不等）——"
          "若相等，才谈得上「本次就是 r7b 身份」；不相等时任何合并说法都是错的")
    check(historical["dependency_versions"] != current["dependency_versions"],
          "两组依赖**向量**也确实是两组（不只是指纹串不同）")

    # (e) 夹具贴的是**记录**身份（重建输入面），而当前指纹**没有**被贴上去冒充它。
    #     这一对读数是「不把新指纹重钉成原 r7b」的可执行反面：新指纹在这里只被**声明**，
    #     没有被写进任何产物身份。
    check_eq(FIX.DEPENDENCY_FINGERPRINT, REAL_DEP,
             "夹具全局 `DEPENDENCY_FINGERPRINT` 仍是 r7b 的**记录**值：依赖身份是被重建的"
             "输入面的一部分（与请求载荷上的 prompt/模型串同类），不得改写成今天的样子")
    check_eq(FIX.DEPENDENCY_VERSIONS, historical["dependency_versions"],
             "夹具全局 `DEPENDENCY_VERSIONS` 与指纹**同组**（不再是一组当前、一组历史）")
    check(FIX.DEPENDENCY_FINGERPRINT != current["dependency_fingerprint"],
          "当前指纹**没有**被贴到夹具上冒充历史身份（这正是「不把新指纹重钉成原 r7b」）")
    check(current["dependency_fingerprint"] != FIX.DEPENDENCY_FINGERPRINT,
          "被贴到夹具上的指纹与当前指纹不是同一个串（两组身份不互相渗透）")
    return declaration


def declare_dependency_identity(details: list[str]) -> dict:
    """把两组依赖身份与「历史身份不可执行」写进调用方的 `details`（r8/r9 复用同一段话）。

    三个离线重放模块共用同一组常量与同一次 `_patch_fixture_identity`，因此这条声明也必须
    同一份——否则「哪一份重放的身份是什么」会随模块各说各话。
    """
    declaration = dependency_identity_declaration()
    historical = declaration["historical"]
    current = declaration["current"]
    details.append(
        "NOTE 依赖身份**两组并列**（第五步；不得合并成一句「r7b 身份」）："
        f"①历史（r7b **记录**，可声明、可对账、**不可执行**）= contract "
        f"{REAL_CONTRACT_FP[:12]}… / source_policy {REAL_SP} / dep "
        f"{historical['dependency_fingerprint']} / navigation_profile "
        f"{R7B_IDENTITY_DELTA['navigation_profile']!r}；"
        f"②当前（本次重放**实际执行**的代码身份）= dep {current['dependency_fingerprint']} / "
        f"navigation_profile {current['dependency_versions']['navigation_profile']!r}。"
        f"逐键差异**只有** {declaration['delta_keys']}（= 声明值）。历史身份不可执行的理由："
        f"{historical['unexecutable_reason']}。")
    details.append(
        "NOTE 为什么夹具仍贴记录值、而不是把当前指纹贴上去（两条都是**不篡改**）："
        "(1) 依赖身份在重放里是**被重建的输入面**，与请求载荷里的 `provider_default` / `pw-13` "
        "同类——本模块不把记录里的串改写成今天的样子来换合规身份（文件头那段已就 `model_policy` "
        "说过同一件事）；(2) §2.3 归因② 等历史复算钉子整体建立在这个输入面之上，改一个字符"
        "它们就全部换身。因此本次重放的身份是「**当前代码** + r7b 记录的输入面」，"
        "本模块的任何身份都**不得**被读成 r7b 的记录产物（r7b 公司节没有草稿）。")
    details.append(
        "NOTE 本模块的处置**不是** skip、也不是把三条 CRASH 掩盖掉：它照旧执行全部断言，"
        "新增的是「历史身份不可执行」这一条**具名声明**与两组身份对账。"
        "M930-3 / TS5 / 正式树门状态不因本模块变绿而改变。")
    return declaration


def _check_dependency_identity(check, check_eq, details) -> dict:
    declaration = _dependency_identity_checks(check, check_eq)
    declare_dependency_identity(details)
    return declaration


def _run_checks(check, check_eq, details) -> None:
    _check_dependency_identity(check, check_eq, details)
    recorded = _check_company_recorded_facts(check, check_eq)
    company = _check_company_replay(check, check_eq, details)
    financial = _check_financial_replay(check, check_eq, details,
                                        recorded["recorded_fin_narrative"])

    case = company["case"]
    fin_case = financial["case"]

    # ============================================================ §2.3 身份前进的**可执行归因**
    _check_version_advance_attribution(check, check_eq, company, financial)

    # ============================================================ §2.4 近义候选的两侧一致
    # `norg-5` 把近义重复**候选**投进组织器输入面（`NS.redundancy_candidate_pairs`）。本模块
    # 另有一份**自己写**的读者面提示（`_near_duplicate_pairs`）。两侧的**分数**不同是设计如此
    # （组织器侧比原文字符，读者侧先剥标点与空白），但**配对集合**必须一致：读者回查时看到的
    # 每一对，都必须是组织器真的收到过的那一对线索；否则「已提示组织器裁决」就只是读者面的一句
    # 无从核对的声明。两个实现各自写、断言的是同一份定稿 Claim 上的同一条结论。
    organizer_pairs = {(a, b) for a, b, _ in NS.redundancy_candidate_pairs(company["output"].claims)}
    reader_pairs = {(a, b) for a, b, _ in _near_duplicate_pairs(company["output"].claims)}
    # 本批配对集**为空**：定稿只剩 12 条**逐字来自材料**的候选，措辞互不重复，近义配对自然归零。
    # 「空」不等于「这条断言被跳过」——下面的一致性断言在空集下同样要成立；而「这条断言不是空转」
    # 由 §2.5 的**改动前**读数证明（那时 47 条 Claim、4 对近义候选，同样逐对一致）。
    check_eq(sorted(reader_pairs), sorted(organizer_pairs),
             "组织器输入面的近义候选必须与读者面提示逐对一致"
             "（两个实现各自写，归一化不同 ⇒ 分数不同，但配对集合必须相同）")
    check_eq(sorted(organizer_pairs), [],
             "本批配对集为空（12 条逐字抽取的候选之间没有近义重复）——"
             "这一条是**如实读数**，不是「没有配对就等于没问题」；"
             "近义重复这条已知未修项在 §2.5 的改动前读数上仍有 4 对")

    # ============================================================ §2.5 `srsc-2` 的**可执行归因**
    _check_srsc2_attribution(check, check_eq, details)

    # ============================================================ §3 本模块不宣称的事
    details.append(
        "NOTE 离线重放，非新的真实验收：零网络、零新模型调用；company 半场用 r7b 留存的真实材料"
        "与三次原始返回（批 4 是标记过的夹具），financial 半场用那一次原始返回（含已声明的别名"
        "重映射）。两节的 `SECTION_BLOCKED` 与逐条缺口如实保留，未被读成通过。")
    details.append(
        "NOTE 不可复算的身份（已声明）：demo 投影 id（记录 proj_2790e83d29df1286c853b015）、"
        "demo_scope_fingerprint（记录 0e1780f495db…）与财务 artifact id"
        "（记录 ffpa_29d817577a7d1e125e63adf0）在本重放里重算后不同；本重放的财务 artifact 是 "
        f"{fin_case['artifact'].artifact_id}。可复算的是财务 draft 修订、公司 draft 身份与两节的"
        "Claim / 聚合决定 / 接受边链。")
    details.append(
        "NOTE 公司节的「证明材料正文」这一步跳过了 payload **字节**的哈希复核（r7b 未留存字节），"
        "改用留存的阅读视图并经同一入口 `MC.WriterMaterialContext.create` 构造上下文；材料身份"
        f"锚点 {case['anchors']}（逐项等于材料条数 {len(case['entries'])}）。")
    details.append(
        "NOTE 人读内容门（本批**未达成**，如实报数）：正文只剩 2 段 4 句、12 条 Claim，"
        "全部逐字来自材料，因此逐句可回查、不含越界断言——但它**不是**一段完整的经营模式描述："
        "采购/生产/销售的实质段还在，供应商集中度、技术路线、成本结构等 aspect 只剩缺口。"
        "内容为什么少：`srsc-2` 只认严格抽取，而这一轮 Writer 交的是**改写句**（见文件头第 1 条）；"
        "`srsc-3` 又在「能抽取」的那些里拿掉 1 条必须带期间的（见下一条 NOTE）。"
        "因此**不得**把本模块的全绿读成「内容已达标」。")
    details.append(
        "NOTE 读者面（逐项交代，不掩盖也不假装已修）：(1) 无标点拼接（「…销售此外，公司…」）"
        "已被 `nrules-12` 判据 e 拒在门外——本模块的独立复算 `_unseparated_seam_count` 对本次"
        "正文逐句报 0，正文里的接缝一律先落分隔标点（`A，此外，B`）；(2) 缺口 detail 的影响范围"
        "已不再写 Python 元组 repr，改为可读并列（同一 `raw` 的两种用法，见 "
        "`pack_writer._impact_text`）；(3) **仍未修**、且本模块只如实列出不替模型裁决的一项："
        "同一事实的**近义重复**（措辞不同、互不逐字包含）会各写一遍——逐字包含的那一类由替身按 "
        "`redundant_with_selected_claim` 删掉，近义的那一类现在**已作为线索投给组织器**"
        f"（`norg-6` 的 `redundancy_candidates`，本次 {len(organizer_pairs)} 对，逐对见正文之后的"
        "清单；此处**按实际配对集算**，不写死数字——写死的数会随候选集一变就成了假读数），但替身"
        "**不对任何一对下结论**：判定「是不是同一件事」是组织器的职责，本重放的组织器是替身，它"
        "没有这个判断力，因此近义重复在**本次输出里仍然各写一遍**——真实组织器会不会合并，只有"
        "一次真实 run 能回答；(4) 一句挂 2–4 条 Claim 的罗列式长句在 `ng-13` 下**合法**，"
        "它是文风问题不是判据问题（判据只到 `MAX_COMPOSED_CLAIMS_PER_SENTENCE`）。"
        "(5) `norg-5` 要求的两处**本次已经做到**：关系性连接语（`另一方面` / `在此基础上` / "
        "`其中` / `综上`）在替身正文里**一处都没有**（判据 10 要求它们逐字来自材料，替身看不到"
        "材料，只能改用中性并列 `此外` / `同时`）；发行人自述句一律带归属语（判据 11），"
        "例如「此外，据公司自身表述，公司致力于为全球新能源应用提供一流的动力电池和储能电池"
        "产品及相关创新解决方案」。这两处是**替身按新要求改写的结果**，不是判据自己变松。")
    details.append(
        "NOTE 读者面 · 期间/范围轴（本批**已修**，`srsc-3`）：此前这一轴只做在**候选侧**"
        "——候选自己写没写期间（`has_period_qualification`）；**支撑侧**那一轴（候选所在的那句"
        "材料正文**自己是否被期间/范围限定**）本模块在上一批记成「新发现、未修，请裁决」，"
        "本批按指令第二优先级落地为 `source_period_scope_dropped`。真实一例（本批被挡下）："
        "「公司销售境外的主要产品为电池系统」逐字抽取自 2025 年报，而源句是"
        f"「{PERIOD_SCOPE_SOURCE_SENTENCE}」——候选**逐字**落在句内，旧口径给 `extractive`，"
        "读者会把限定语丢掉、读成一条**无期间的持续现状**；本批不再写进正文（该句已不在"
        "final Narrative 的 4 句里，见上面逐条断言）。判据**只在候选覆盖到的源句上开火**："
        "源句自己带限定语才判，「不要反过来把所有稳定的客观描述都强行加日期」由上面那条"
        "保护断言（全部成员重跑都落 `extractive`）在真实数据上把住。"
        "**代价如实报**：本批因此少 1 条候选可写、正文 13 → 12 条 Claim——这是指令要求的结果，"
        "不是回归，也不计入任何「已达标」读数。同一族的下一条「境外客户回款情况正常」本批仍由"
        "**高风险表面**（`正常`）挡下，两条原因各自成立、互不替代。")
    details.append(
        "NOTE 反例覆盖（引用既有专测，不在此重复）：误伤词/真实风险词见 "
        "`evals/test_m930_3_risk_surface_semantics.py`；已舍入输入 / 缺前期 / 错范围口径 / "
        "单期冒充双期 / 缺双期引用见 `evals/test_m930_3_derived_delta_fact.py`；旧束被洗白见 "
        "`evals/test_m930_3_rejection_retention.py`；候选无故消失见 "
        "`evals/test_demo_accepted_binding.py`。本模块在此之上用**真实数据**复核了前三类。")
    details.append(
        "NOTE 公司节的拒绝序列与 r7b **不同**：r7b 记录的是两次 schema_invalid（结构没过），"
        "本重放是 1 次 path_b_high_risk_surface + `cco-3` 裁出。这不是「历史被改写」，而是"
        "「同一批原始返回在当前代码下走出的另一条轨迹」——r7b 那两次拒绝发生在更早的版本上，"
        "本模块不改写它，只把两条轨迹并列记录。")


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

    def check_eq(actual, expected, msg: str) -> None:
        check(actual == expected, f"{msg}（实得 {actual!r}，期望 {expected!r}）")

    ok, missing = _anchors_present()
    if not ok:
        skipped += 1
        details.append(f"SKIP 缺少 r7b 原始调用日志 / 产物 / 两个库：{missing}。"
                       "离线重放需要那一轮真实运行的字节与记录，它们都不在版本控制里")
        return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}

    snapshot = _patch_fixture_identity()
    try:
        _run_checks(check, check_eq, details)
    finally:
        _restore_fixture_identity(snapshot)
    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

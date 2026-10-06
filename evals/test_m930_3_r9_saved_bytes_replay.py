"""Eval: M930-3 **r9 实录的离线回放**——公司节的两个阻断（归属闭合、context 越权）与越权后的出路。

用法: python -X utf8 -m evals.test_m930_3_r9_saved_bytes_replay

## 这个模块要回答什么

r9 那一轮的公司节**没有产出任何正文**，而且失败方式与 r8 不是同一条：它越过了批内形状校验
与合并（40 条候选 / 19 段自然草稿 / 10 条草稿单元 / 20 条补件诉求都成形了），死在
**`SectionDraft` 构造**这一步——`NarrativeSchemaError`（自然草稿不闭合：同一候选被多个自然
草稿单元声明）。本模块用 r9 **自己留存的字节**（`logs/llm` 下 4 份
`pack_section_writer_proposals_v11@proposals-15` 原始返回 + `evaluation/results/.../r9/` 的材料
包、失败诊断、验收报告），在当前代码上**零网络、零新模型调用**地重走同一条链，读出四件事：

  A. **合并面的现场读数**：四批返回逐批解析后按生产 `_merge_batch_plans` 合并，逐批条数、
     合并后条数、以及**被多个草稿单元声明的候选**逐条打印（共享候选的原子命题、它的每一条
     支撑边落在哪一份文档/哪一种来源角色上、声明它的每一段草稿的正文与**各自独有的原子**）。
     这是「两组『同文、不同年报出处』候选重复归属」这句话的**原始对象**，不是对错误串的转述。
  B. **同一条链的复现**：把四份真实返回按坐标喂进生产驱动（`PW.write_section` → 门前束 →
     `SectionDraft` → 门前硬门），逐轮读出这批字节在当前代码下真正走到了哪里。两个阻断各有
     自己的 typed 记录与去向：**候选**携带高风险表面（`path_b_high_risk_surface`，就地按**候选**
     裁出 40 → 7，模型零调用），随后两段 **context 衔接单元**把「未发生变更」「未发生重大诉讼/
     仲裁」这类**明确否定**写成背景衔接并带上无定义期间「报告期」（`narrative_gate_blocking`，
     §十二 4 blocking，就地按**单元**逐项撤下 10 → 8，模型零调用）。第三束因此越过门前硬门，
     走到 `SectionResult`——**但状态是 `SECTION_BLOCKED`**，48 条缺口与 20 条待裁决补件一条没少。
  C. **财务最终句 B 的真实返回被钉住**：r9 财务节那唯一一次 `final_sentence_fidelity_v1@nsfr-1`
     调用的请求面与返回面逐字读回——机械定位出 `2025年` / `2024年` / `-3.3个百分点` 三项，
     模型返回的是**合并**成一条的期间原子（`2025年末较2024年末`），因此两条已定位表面无人认领，
     本门**未能形成决定**。同一事实在 r9 自己的产物里是「`current_section_final_sentence_decision_v2`
     的财务行数为 0」——决定**没有落库**，本模块把这两侧钉成同一件事的两面。
  D. **r9 的逐栏目归属面（只读）**：哪些 aspect 只靠兜底重切立起来、它们的导航读集读数为 0。

## 离线重放边界（**本文件必须如实标注的六件事**）

  1. **这不是新的真实验收，也不是一次真实模型调用**。零网络、零新 LLM 调用：门前提案束逐字
     取自 r9 留存的四份原始返回；组织器用验收 harness 自己的确定性替身
     （`ACC.OfflineNarrationClient()._organizer`）——r9 那一轮公司节从未走到组织器（证据：本节的
     4 份返回之后没有任何 `section_narrative_organizer_*` 公司调用），因此**不存在**公司侧的
     留存组织返回可用。**因此这一节成形的正文不是人工可接受的真实写作**：它只证明链路走通，
     §2 末尾把这一点与正文一起印出来（「仅证明链路」），不得被当成「公司节能写出来」。
  2. **公司节的材料正文不经 payload 字节复核**。理由与 r7b / r8 两次重放逐字相同（真实信封不
     入库）：本重放改用 r9 留存的阅读视图，经**同一个**入口 `MC.WriterMaterialContext.create`
     构造上下文，材料身份面逐条与 r9 记录对账（内容指纹 / locator / 来源身份 / 载荷哈希，
     四项全等才算数）。
  3. **留存字节是**r9 当时的 prompt **问出来的，不是当前 prompt 问出来的**。四份返回的
     `prompt_version` 全部是 `pack_section_writer_proposals_v11@proposals-15`（§0 逐份断言）。
     本重放问的是「**当前规则**遇到 **r9 的字节**会怎么判」，**不是**「当前 prompt 会问出什么」。
     正文规则或 prompt 任一变化，本模块的结论都要重跑。
  4. **可复算的身份如实声明**（与 r7b / r8 两次重放同族）：demo 投影 id、`demo_scope_fingerprint`、
     财务 artifact id 在本重放里重算后与记录不同。可复算的是：公司 draft 身份、写入侧
     `ContractProjection`，以及本节的候选/草稿/支撑边链。
  5. **财务最终句那一段只读、不重放**：本模块**不**调用 `evaluate_final_sentence_fidelity`
     （那需要一次 LLM 调用）。它只用**生产**的机械定位器与**生产**的输出解释器在留存字节上
     复现「哪三条表面被定位、哪一条没有被认领」，并读回 r9 产物里决定行数为 0 的事实。
  6. **依赖身份是两组，不得合并**（第五步；与 r7b/r8 重放同一份声明，见
     `R7.declare_dependency_identity`）。r7b 记录的依赖身份（dep `7f6d71f4…` /
     `navigation_profile = schema=anps-4;rule=anp-6`）与**当前工作区**的身份（dep
     `8fa6dcbe…` / `rule=anp-7`）逐键只差 `navigation_profile` **一个键**。**历史身份
     不可执行**：`anp-6` 的实现不在 git HEAD（HEAD 为 `anp-1`）也不在工作区，且 18 键向量
     从未落库（产物里没有 `dependency_versions` 字段）。本模块因此是「**当前代码** + r9
     记录的输入面」的离线重放：依赖身份是被重建的输入面的一部分（与 `proposals-15` 那个
     版本串同类，不得改写成今天的样子），而跑它的代码是当前版本。夹具仍贴记录值，
     当前指纹只被**声明**、不被贴。

## replay ≠ pass（本文件明确不主张的事）

  * r9 记录里公司节**没有**任何 draft / Claim / 正文 / 结果（`follow_up_needs.json` 的
    `sections_completed` 只有 `financial` 与 `industry`，company 带 `phase_error`；
    `acceptance_report.gates` 的 A1 为 `fail`）。本节那份 `SectionResult` 是**当前代码下的新
    产物**，身份是「本批代码 + 本夹具下的确定性身份」，**不是**对某份记录产物的复现，
    **也不是** r9 那一轮真的写出过正文。
  * 本模块**不**宣称 M930-3、TS5、正式树门或后续阶段关闭；`A1=fail` 与 `status=fail` 如实保留，
    `SECTION_BLOCKED` 与全部缺口如实保留。
  * 组织器与蕴含判定在本重放里都是**替身**：替身的输出只说明「链能不能走通」，不说明
    「真实组织器会怎么组织」，也不说明这一节的内容达到人读门。

数据依赖：`logs/llm/` 下 r9 的 4 份公司写作调用 + 1 份财务最终句调用、
`evaluation/results/m930_3_acceptance_crossdoc_real_r9/`、`data/evidence.db`。
都不在版本控制里，缺失时本模块 **typed skip**（不计通过），不静默绿。
"""

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import loader_v2 as CV2
from harness import source_manifest as SM
from harness import topic_schema as TS
from planning import demo_scope as DS
from sections import final_sentence_fidelity as FSF
from sections import material_context as MC
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import source_role_scope as SRS

#: 夹具、任务重建、驱动与判决**全部**复用 r7b 重放模块：两份权威一旦分叉，「重放的是同一条链」
#: 就不成立。该模块顶层只有常量与函数定义，导入它不执行任何用例。
from evals import test_m930_3_prewrite_offline_replay as R7  # noqa: E402
from evals import test_demo_pack_writer as FIX  # noqa: E402

ROOT = R7.ROOT
R9 = ROOT / "evaluation" / "results" / "m930_3_acceptance_crossdoc_real_r9"
R9_SOURCE_MANIFEST = R9 / "source_manifest.json"

#: r9 公司节 **4 次** narration 调用的有序台账（`llm_call_ledger.json` ordinal 239–242）。
#: 逐条给出：序号、日志文件名、批次标签、批次 id、本批 aspect 数、以及该份返回的四键计数。
#: 这里的数**只**用于把「每一份字节是什么」钉住，不用来判内容（判内容的是 §1/§2 的现场对象）。
R9_COMPANY_CALLS = (
    # (序号, 日志文件名, 批次标签, 批次 id, aspect 数, 候选, 草稿, 单元, 补件)
    (239, "20260927T165623941977__8716d6d9bfc142a8bb626ee1732329c8", "1/4",
     "wpbatch_09407563b7d8856b8acc691a", 12, 22, 11, 4, 0),
    (240, "20260927T165639752150__9969198f5b0c4ae89a8de652416d02a3", "2/4",
     "wpbatch_b1a1466200f760fcc9d143a8", 12, 4, 3, 2, 2),
    (241, "20260927T165658488619__ab787f3535414bb69b0d2e627837968a", "3/4",
     "wpbatch_7e27d3243db46e7aa783d6f9", 12, 16, 5, 4, 8),
    (242, "20260927T165706637542__e4758ad887d24ea4a91f41addf367249", "4/4",
     "wpbatch_3240d2928e585d19c9fa4068", 10, 0, 0, 0, 10),
)

#: r9 财务节那**唯一**一次最终句语义核验调用（ledger ordinal 270）。
R9_FIN_CALL = "20260927T165756855040__78d8076cc92a4a918fc32a1d34d60b66"

#: r9 公司节的真实身份。与 r7b / r8 的**来源面**相同（同一份冻结 Contract / SourcePolicy /
#: 来源清单），但**任务与报告基准日不同**：r9 是 2026-09-28 的那一轮
#: （`authority.report_as_of`，§0 逐字断言）。两者都进候选身份，因此必须逐字取记录值。
R9_TASK_ID = "dtask_eb141c12ff272f7913ca86a8"
R9_REPORT_AS_OF = "2026-09-28"
R9_PROJECTION_ID = "cproj_ea35024fa6d76700086d2a4d"
REAL_CONTRACT_FP = R7.REAL_CONTRACT_FP
REAL_DEP = R7.REAL_DEP
COMPANY = R7.COMPANY
PROFILE_PATH = R7.PROFILE_PATH
R9_COMPANY_ASPECT_COUNT = 46
R9_MATERIAL_COUNT = 40
REPLAY_WIRE = PW.PROPOSAL_WIRE_CURRENT
REPLAY_SOURCE = "m930_3_acceptance_crossdoc_real_r9"

R9_RECORDED_PROMPT_VERSION = "pack_section_writer_proposals_v11@proposals-15"
R9_RECORDED_MODEL = "deepseek-v4-pro"
R9_RECORDED_SECTION_DRAFTS = ("financial", "industry")

#: r9 现场把公司节判死的那**一条**错误串（`failure_diagnostics.json` 的 `sections.company.
#: section_error` = `acceptance_report.section_errors.company`，两处逐字相同）。
R9_RECORDED_ERROR = (
    "SectionDraft 自然草稿不闭合：同一候选被多个自然草稿单元声明："
    "['ccand_38abf601b8fb65c30d13d836', 'ccand_78686b0bfaab33c6495fa7a7']（原子归属必须唯一）")
#: 那两条被点名的候选身份（顺序即记录顺序）。它们是**身份**，不是文本——本模块在 §2 断言
#: 现场复现出的正是这两个 id，而不是「两条长得像的候选」。
R9_RECORDED_SHARED_IDS = ("ccand_38abf601b8fb65c30d13d836",
                          "ccand_78686b0bfaab33c6495fa7a7")

#: §1 合并面上那**两组**共享候选的现场读数（原子命题文本 + 声明它的草稿段 + 各自独有原子）。
#: 全部取自 r9 留存字节在当前代码下的合并结果；本模块逐项断言，不把它们当「期望值」贴上去。
R9_SHARED_GROUPS = (
    {"candidate_text": "公司电池材料产品主要包括锂盐、前驱体及正极材料等",
     "units": ("p4", "p5"),
     "unique_atoms": {"p4": ("c9",), "p5": ("c10",)},
     "roles": {"p4": "current_state_source", "p5": "history_and_conflict_source"}},
    {"candidate_text": "采购方面，公司通过严格的评估和考核程序遴选合格供应商",
     "units": ("p6", "p10"),
     "unique_atoms": {"p6": ("c11", "c13"), "p10": ("c19",)},
     "roles": {"p6": "current_state_source", "p10": "history_and_conflict_source"}},
)
R9_MERGED_COUNTS = {"candidates": 40, "prose": 19, "units": 10, "follow_ups": 20}

#: r9 财务最终句的真实字面（请求面与返回面）。
R9_FIN_PROMPT_VERSION = "final_sentence_fidelity_v1@nsfr-1"
R9_FIN_SENTENCE_ID = "nsent_c0857f17ab828eba76f25a42"
R9_FIN_SENTENCE_TEXT = "2025年末较2024年末的资产负债率变动为-3.3个百分点。"
R9_FIN_CLAIM_ID = "claim_5a24f452f231daa4965ee87b"
R9_FIN_BINDING_ID = "asb_f816b5d3c437aaf8529f6e9e"
#: 机械定位出的三条表面（顺序即定位顺序）。它们**只**说明这些字在句子里出现过。
R9_FIN_LOCATED_SURFACES = ("2025年", "2024年", "-3.3个百分点")
#: 模型返回的三条原子（顺序即返回顺序）：句子的期间被**合成**成一条原子，两条年份表面因此
#: 无人认领。这是 D 段要修的那件事在字节上的样子。
R9_FIN_MODEL_ATOMS = ("2025年末较2024年末", "资产负债率", "-3.3个百分点")

#: 被拒轮次的 typed 记录类型（`oc.rejections` 的元素）。r9 现场的首个阻断是裸
#: `NarrativeSchemaError`「自然草稿不闭合：同一候选被多个自然草稿单元声明」——它由**组装
#: `SectionDraft` 之前**的归属判据抛出，冲出 `write_section`，于是链上已经写出来的 20 条补件
#: 诉求随异常一起蒸发。
#:
#: B 段把归属判据改成**逐 occurrence 与候选自己的支撑提案核对**（`npr-1`：材料轴比
#: 容器 / 材料 ID / 文档版本，事实轴比 `fprov_*` 出处）之后，草稿层闭合得上；被拒的轮次从此走
#: **写侧通道**：整束拒绝记成 `ProposalSetRejectionRecord`（逐束 typed 审计 + 门前留存 +
#: 补件诉求一起带出），链继续往前找合法出口，而不是整节蒸发。
RECORDED_REJECTION_RECORD_TYPE = "ProposalSetRejectionRecord"
#: 当前代码下这批字节真正走过的**三**轮门前束（按现场复现读数钉住，逐条断言，不按期望值倒推）：
#:
#:   1. **首轮那一束**（40 条候选 / 19 段自然草稿 / 10 条 context 单元 / 20 条补件）：多条候选
#:      携带高风险表面 ⇒ `path_b_high_risk_surface`，并就地排定**逐候选裁出**（40 → 7，
#:      模型零调用）；
#:   2. **候选裁出轮那一束**（7 / 7 / 10 / 20）：草稿层闭合**通过**、`SectionDraft` 真的构造
#:      出来了，但门前 `narrative_draft_units`（context 衔接单元）里有两段文本含「报告期」⇒
#:      `narrative_gate_blocking`（`narrative_vague_period`，blocking，§十二 4）。
#:      「不得用含糊期间顶替权威期间」是**正确**的 fail-closed：那两段是 context 单元，
#:      不带任何 claim 身份，也无权承载「控股股东/实际控制人未发生变更」「未发生重大诉讼/仲裁」
#:      这类**明确否定**——按 §十二 4 与 `npr-*` 的纪律它本来就该被打回；
#:   3. **context 单元裁出轮那一束**（7 / 7 / **8** / 20，`cco-6`）：那两段按**逐项 typed 排除**
#:      撤下（原文、来源 member ref、原因码与命中词全部留档），其余 8 段**原样带过**（不改写、
#:      不删字、不把否定改成含糊正面句），修订前进后门前硬门放行 ⇒ 本节第一次走到
#:      `SectionResult`（`SECTION_BLOCKED` 与全部缺口原样保留）。
#:
#: **这不是「公司节现在能成稿」。** 第三束只是越过了那道**越权**的门；正文仍由确定性组织器
#: 替身拼装（见 §2 末「仅证明链路」），48 条缺口与 20 条待裁决补件一条没少。
RECORDED_REJECTION_KINDS = ("path_b_high_risk_surface", "narrative_gate_blocking")
#: 被拒的是**前两轮**（第三轮走到成稿）——因此本重放里 `ProposalSetRejectedError` 不被触发。
RECORDED_REJECTION_ATTEMPTS = (1, 2)
#: 三束的**候选**数：第二道裁出只撤 context 单元，候选一条不动（首轮 40 → 7 → 7）。
RECORDED_CANDIDATE_COUNTS = (40, 7, 7)
#: 三束的 **context 单元**数：10 → 10 → 8（少了的那两条正是被逐项撤下的那两段）。
RECORDED_CONTEXT_UNIT_COUNTS = (10, 10, 8)
#: 裁出后进入第二、第三轮的那一束的现场候选数（首轮 40 → 裁出后 7）。
RECORDED_CARVE_OUT_CANDIDATES = 7
#: 被拒两轮各自提出的补件诉求条数（20 + 20）。它们**不再蒸发**：每条拒绝记录上都带
#: `follow_up_count` 与 `retained_pre_gate`；第三束自己那 20 条则成为 `follow_up_needs`——
#: 全部是**待裁决提议**，不是已执行的补件（本批模型调用总数仍是 r9 那 4 次，见 §2）。
RECORDED_REJECTED_FOLLOW_UPS = (20, 20)
#: `cco-6` 逐项撤下的**两条越权 context 单元**（文本逐字取自 r9 留存字节里那两段
#: `narrative_draft_units`，不是本模块另立的期望值）。它们不是背景/结构/衔接：一条是控股股东
#: 与实际控制人未发生变更的**明确否定**，一条是重大诉讼/仲裁的**明确否定**；两条都带无定义期间
#: 指代「报告期」，而 context 单元既不带 claim 身份、也拿不到任何权威期间——因此它唯一的合法
#: 出口是逐项撤下，而不是把「报告期」三个字删掉了事、更不是把否定改写成含糊的正面句。
RECORDED_CONTEXT_UNIT_EXCLUDED = (
    "控股股东与实际控制人报告期内均未发生变更。",
    "重大诉讼与仲裁方面，报告期内未发生重大诉讼/仲裁事项；"
    "其他已达到披露标准的诉讼/仲裁案件及其涉案金额、影响已作披露。",
)
#: 同一条单元轴上**原样带过**的那一段：它的期间是显式日期（「截至募集说明书签署日」），
#: 不是无定义指代。逐项撤下**不是**整段清空，也不是改写。
RECORDED_CONTEXT_UNIT_SURVIVOR = "债务违约方面，截至募集说明书签署日未发生重大债务违约情况。"
#: 三轮各自的模型调用数（4 / 0 / 0）。裁出是**确定性**的：不调模型、不重出、不改一个字的
#: 候选文本，因此两轮裁出与一次成稿都没有新增任何请求——补件诉求也没有被拿去自动重问。
RECORDED_ROUND_CALLS = (4, 0, 0)
#: 本重放里 `SectionResult` 的状态。**成稿不等于合格**：本节第一次拿到结果，但状态仍是
#: `SECTION_BLOCKED`，48 条缺口与 20 条待裁决补件一条没少。
RECORDED_RESULT_STATUS = "SECTION_BLOCKED"
#: 「报告期」在正文里**唯一**允许出现的地方：`period_unresolved` 那两条缺口各自的说明文字
#: （逐主题一条，原文就是「不得用本节基准日或『报告期』这类未定义指代替代」）。
#: 正文里除此之外不得出现这三个字——这正是那两段越权 context 单元被撤下的理由。
RECORDED_PERIOD_GAP_TOPICS = ("company_business", "company_legal_risks")


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_call(stem: str) -> dict:
    return json.loads((R7.LLM_LOGS / f"{stem}.jsonl").read_text(encoding="utf-8"))


def _parsed_return(stem: str) -> dict:
    """一份留存返回的四键形态（末段可能带批注，故用 `raw_decode`）。"""
    return json.JSONDecoder(strict=False).raw_decode(_read_call(stem)["completion"])[0]


def _payload(stem: str) -> dict:
    return R7._payload_of(stem)


def _aliases_of(payload: dict) -> PW.SupportAliasTable:
    """一份请求 payload → 该批的别名表（与本批返回里的 `cN` / `mN` 短把手逐字对应）。

    `authority_facts=0` 的那些批次（r9 公司节**四批全是**）建出的是一张**只有材料别名**的表，
    这正是要如实重放的那件事：本批没有任何路径 A 事实行可指。
    """
    facts = tuple(PW.SupportOption(ref=r["ref"],
                                   authority_kind=r.get("authority_kind") or "topic_fact",
                                   container_identity=r["container_id"], fact_id=r.get("fact_id"))
                  for r in payload.get("authority_facts") or ())
    mats = tuple(PW.SupportOption(ref=r["ref"], authority_kind="topic_pack",
                                  container_identity=r["container_id"],
                                  material_id=r["material_id"])
                 for r in payload["materials"])
    return PW.SupportAliasTable(facts=facts, materials=mats)


def _batch_of(payload: dict) -> PW.WriterPlanBatch:
    b = payload["batch"]
    return PW.WriterPlanBatch(batch_id=b["batch_id"], index=b["index"], total=b["total"],
                              aspect_ids=tuple(b["aspect_ids"]),
                              topic_ids=tuple(b.get("topic_ids") or ()),
                              shrink_depth=b.get("shrink_depth") or 0,
                              parent_batch_id=b.get("parent_batch_id"))


def _anchors_present() -> tuple[bool, str]:
    """数据依赖：r9 的 5 份原始日志 + 记录产物 + Evidence 库。缺任何一项都不猜、不静默绿。"""
    missing: list[str] = []
    for _n, stem, *_rest in R9_COMPANY_CALLS:
        if not (R7.LLM_LOGS / f"{stem}.jsonl").exists():
            missing.append(f"logs/llm/{stem}.jsonl")
    if not (R7.LLM_LOGS / f"{R9_FIN_CALL}.jsonl").exists():
        missing.append(f"logs/llm/{R9_FIN_CALL}.jsonl")
    for name in ("material_pack.json", "failure_diagnostics.json", "follow_up_needs.json",
                 "source_manifest.json", "llm_call_ledger.json", "acceptance_report.json"):
        if not (R9 / name).exists():
            missing.append(f"{R9.name}/{name}")
    if not (ROOT / "data" / "evidence.db").exists():
        missing.append("data/evidence.db")
    return (not missing), "；".join(missing)


# ---------------------------------------------------------------------------
# §1 合并面：四批留存返回 → 一份完整有序提案集（零网络，纯确定性）
# ---------------------------------------------------------------------------

def _merge_face(case: dict | None = None) -> dict:
    """四批留存返回 → 逐批解析 → 生产 `_merge_batch_plans` → 共享候选/草稿单元诊断。

    全程只用**生产**解析器与**生产**合并器（不重写一份「大概的」合并），因此这里读到的
    「谁和谁合成了一条」就是链上真正发生的那件事。共享候选的判定口径同样取生产判据所关心的
    那一条：**一个候选被几段草稿声明**（`natural_prose_draft[].atom_candidate_keys`），
    而不是文本相似度。

    解析用的别名表在 `case` 在场时取**生产**派生（`_support_aliases(scan, manifest)`，与
    `write_section` 里同一函数），并把「记录请求**自己声明**的第 n 行材料」与它**逐行对账**：
    留存返回里的短把手（`mN`）只有在这一层对得上，才谈得上「喂进去的字节指的还是那份材料」。
    容器（`pack_id`）在本重放里是重算值，与记录不同（见 §1 末尾的如实说明）；材料身份不是。
    """
    aliases = manifest = scan = None
    if case is not None:
        scan = PW.scan_authority(case["authority"], case["task"])
        manifest = PW._derive_material_manifest(case["authority"],
                                               material_context=case["context"])
        aliases = PW._support_aliases(scan, manifest)
    table = (PW._authority_fact_table(scan.facts) if scan is not None else {})
    batch_plans: list[tuple[PW.WriterPlanBatch, dict]] = []
    rows: list[dict] = []
    materials_by_ref: dict[str, dict] = {}
    container_mismatch = 0
    ref_disagreement: list[str] = []
    for ordinal, stem, label, batch_id, aspects, ncand, nprose, nunits, nfu in R9_COMPANY_CALLS:
        payload = _payload(stem)
        if aliases is not None:
            declared_rows = [(str(m["ref"]), str(m["material_id"]), str(m["container_id"]))
                             for m in payload["materials"]]
            live_rows = [(str(o.ref), str(o.material_id), str(o.container_identity))
                         for o in aliases.materials]
            if [r[0] for r in declared_rows] != [r[0] for r in live_rows]:
                ref_disagreement.append(f"{label}: 行数/把手不同 "
                                        f"{len(declared_rows)} vs {len(live_rows)}")
            for (ref, material_id, container), (lref, lmaterial, lcontainer) in zip(
                    declared_rows, live_rows):
                if material_id != lmaterial:
                    ref_disagreement.append(f"{label}: {ref} → {material_id}≠{lmaterial}")
                container_mismatch += (container != lcontainer)
        plan = PW.parse_writer_proposals(_read_call(stem)["completion"],
                                         aliases=aliases or _aliases_of(payload))
        batch = _batch_of(payload)
        batch_plans.append((batch, plan))
        for material in payload["materials"]:
            materials_by_ref[str(material["ref"])] = material
        rows.append({"ordinal": ordinal, "stem": stem, "label": label, "batch_id": batch_id,
                     "aspect_count": aspects,
                     "aspect_ids": tuple(str(a) for a in batch.aspect_ids),
                     "parsed": {"candidates": len(plan["claim_candidates"]),
                                "prose": len(plan["natural_prose_draft"]),
                                "units": len(plan["narrative_draft_units"]),
                                "follow_ups": len(plan["follow_up_needs"])},
                     "recorded": {"candidates": ncand, "prose": nprose, "units": nunits,
                                  "follow_ups": nfu},
                     "authority_facts": len(payload.get("authority_facts") or ()),
                     "material_count": len(payload["materials"])})
    expected = [a for batch, _plan in batch_plans for a in batch.aspect_ids]
    merged, audit = PW._merge_batch_plans(batch_plans=batch_plans, expected_aspects=expected,
                                         table=table)
    declared: dict[str, list[str]] = {}
    for unit in merged["natural_prose_draft"]:
        for key in unit["atom_candidate_keys"]:
            declared.setdefault(str(key), []).append(str(unit["prose_key"]))
    candidates = {str(c["candidate_key"]): c for c in merged["claim_candidates"]}
    prose = {str(p["prose_key"]): p for p in merged["natural_prose_draft"]}
    # 成员出处一律走**生产**读法：`member_ref` → manifest 成员 → `locator_ref` 的文档轴 →
    # `SRS.document_roles(authority)` 的角色。不另判角色、也不拿记录里那一行的字符串冒充
    # （成员键是 `(pack_id, material_id)` 的内容 id，容器一换键就换，见 §1 末尾）。
    live_members = ({str(e.member_ref): e for e in manifest.entries}
                    if manifest is not None else {})
    roles = SRS.document_roles(case["authority"]) if case is not None else {}
    recorded_materials = {str(m["material_id"]) for m in materials_by_ref.values()}
    unknown_members = 0
    off_record_materials = 0
    groups: list[dict] = []
    for key in sorted((k for k, ups in declared.items() if len(ups) > 1),
                      key=lambda k: int(k[1:])):
        candidate = candidates[key]
        group = {"candidate_key": key, "candidate_text": str(candidate["claim_text"]),
                 "support": tuple(dict(edge) for edge in candidate["support"]),
                 "units": []}
        for prose_key in declared[key]:
            unit = prose[prose_key]
            unique = tuple(a for a in unit["atom_candidate_keys"]
                           if len(declared[str(a)]) == 1)
            members = []
            for member_ref in unit["source_member_refs"]:
                entry = live_members.get(str(member_ref))
                unknown_members += (entry is None)
                axes = (SRS.declared_axes_from_locator(entry.locator_ref) if entry else None)
                material_id = str(getattr(entry, "material_id", ""))
                off_record_materials += bool(material_id) and material_id not in recorded_materials
                members.append({
                    "member_ref": str(member_ref),
                    "material_id": material_id,
                    "pack_id": str(getattr(entry, "pack_id", "")),
                    "source_role": str(roles.get(axes[0], "")) if axes else "",
                    "document_axes": axes,
                })
            group["units"].append({"prose_key": prose_key, "text": str(unit["text"]),
                                   "atom_candidate_keys": tuple(unit["atom_candidate_keys"]),
                                   "unique_atoms": unique, "members": tuple(members)})
        groups.append(group)
    face = {"rows": rows, "merged": merged, "audit": audit, "candidates": candidates,
            "shared_groups": tuple(groups),
            "candidate_total": len(merged["claim_candidates"]),
            "prose_total": len(merged["natural_prose_draft"]),
            "unit_total": len(merged["narrative_draft_units"]),
            "follow_up_total": len(merged["follow_up_needs"]),
            "ref_disagreement": tuple(ref_disagreement),
            "container_mismatch": container_mismatch,
            "unknown_members": unknown_members,
            "off_record_materials": off_record_materials,
            "manifest": manifest, "scan": scan, "aliases": aliases}
    if case is not None:
        face.update(_bundle_face(case, merged, manifest=manifest, scan=scan))
    return face


# ---------------------------------------------------------------------------
# §2 驱动面：同一批字节走生产链（门前束 → SectionDraft）
# ---------------------------------------------------------------------------

def _r9_source_set() -> TS.DocumentSourceSet:
    """r9 那一轮的**有序源集**，逐字派生自它自己的 `source_manifest.json`。

    与 r7b / r8 两次重放同源（角色**只**取自 Pack 自己的来源角色台账，不在本模块另判角色）。
    """
    document = _read_json(R9_SOURCE_MANIFEST)
    roles = {(s["document_id"], s["document_version"], s.get("evidence_set_version")):
             s["source_role"] for s in document["selection"]}
    members = []
    for raw in document["document_keys"]:
        key = SM.SourceDocumentKey.from_dict(raw)
        role = roles.get((key.document_id, key.document_version, key.evidence_set_version))
        if not role:
            raise AssertionError(
                f"r9 来源清单里 {key.document_id}@{key.document_version} 没有 source_role："
                "不得默认它参与检索（fail-closed）")
        members.append((key, role))
    return TS.DocumentSourceSet(members=tuple(members))


def _build_company_case() -> dict:
    """r9 company 的完整写作前输入面（Pack set / 权威 / 材料上下文）。

    与 `R7._build_company_case()` 是**同一份构造**，只有「读哪一轮的记录」与「任务/基准日身份」
    两处不同：材料包、失败诊断、来源清单、请求 payload 全部取自 r9 自己的产物；任务 id 与
    `report_as_of` 逐字取 r9 记录值（两者都进候选身份，写错会让本模块复现出**另一批**身份）。
    可复用的部分直接调 `R7._build_task_and_specs` / `R7._periodless_facts`，不另写一份——
    夹具分叉比重复更危险。
    """
    requests = {n: _payload(stem) for n, stem, *_rest in R9_COMPANY_CALLS}
    req1 = requests[239]
    pack_doc = _read_json(R9 / "material_pack.json")
    fd = _read_json(R9 / "failure_diagnostics.json")
    entries = pack_doc["sections"]["company"]["entries"]
    by_id = {e["material_id"]: e for e in entries}
    fd_aspects = {a["aspect_id"]: a for a in fd["sections"]["company"]["aspects"]}
    requestable = {a["aspect_id"]: a for a in req1["requestable_aspects"]}

    profile = DS.load_demo_scope_profile(str(ROOT / PROFILE_PATH))
    contract = CV2.load_contract_v2(str(ROOT / profile.contract_asset))
    if profile.contract_fingerprint != REAL_CONTRACT_FP:
        raise AssertionError(
            f"冻结 DemoScope profile 绑定的 Contract 指纹 {profile.contract_fingerprint!r} "
            f"不是 {REAL_CONTRACT_FP!r}")
    # `R7._build_task_and_specs` 里的任务 id 取自 r7b 的模块常量（公司/财务两侧共用一份构造）。
    # 本重放要用 r9 的任务 id，因此在这一步**临时**把它对齐到 r9 记录值，随后逐字还原——
    # 不做「另写一份重建」（那会让两次重放的 Contract 投影各说各话）。
    saved_task_id = R7.COMPANY_TASK_ID
    R7.COMPANY_TASK_ID = R9_TASK_ID
    try:
        task, specs = R7._build_task_and_specs(profile, contract, fd_aspects, requestable)
    finally:
        R7.COMPANY_TASK_ID = saved_task_id

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
    source_set = _r9_source_set()
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
            facts=R7._periodless_facts(topic_id, fd_aspects, entries, patched),
            company_id=COMPANY, report_as_of=R9_REPORT_AS_OF, contract_version="v2",
            contract_fingerprint=REAL_CONTRACT_FP, source_set=source_set))
    pack_set = FIX._pack_set(task, packs=tuple(pack_specs), requirements=tuple(req_specs),
                             material_factory=factory)
    authority = PW.TopicPackAuthorityInput.create(
        task, pack_set, company_id=COMPANY, report_as_of=R9_REPORT_AS_OF,
        contract_version="v2", contract_fingerprint=REAL_CONTRACT_FP)

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
    context = MC.WriterMaterialContext.create(
        task_id=R9_TASK_ID, section_id="company",
        pack_set_fingerprint=MC.pack_set_fingerprint(pack_set), materials=tuple(resolved))
    return {"task": task, "pack_set": pack_set, "authority": authority, "req1": req1,
            "requests": requests, "entries": entries, "patched": patched,
            "anchors": anchors, "specs": specs, "profile": profile, "resolved": resolved,
            "context": context}


def _gate_issues(detail: str) -> list[dict]:
    """整束级 `narrative_gate_blocking` 的 `rejection_detail` → 逐条问题清单（原样解析）。

    那一格由写侧的 `_gate_failure` 用 `json.dumps` 写出（键集就是 issue 自己的键），因此这里
    `json.loads` 回来的是**同一份**结构化读数——不是从散文里猜出来的，也不另立一份判据。
    解析失败即抛（`json.JSONDecodeError`）：静默返回空表会把「问题清单没了」读成「没有问题」。
    """
    start = str(detail).find("[")
    if start < 0:
        return []
    return json.loads(str(detail)[start:])


def _replay_policy() -> PW.WriterPolicy:
    """本重放喂给链的策略对象：三个版本钉子必须与 `R7._drive_company` 逐字相同。

    它们进 `draft_revision` 的身份体，因此**换一个串就换一份候选身份**——本模块下面
    「错误点名的两条身份 = 共享候选的现场身份」这条断言，同时也在证明这里的三项与驱动里的一致
    （若不一致，两条身份就对不上，断言会红，而不是静默通过）。
    """
    return PW.WriterPolicy(policy_version=PW.PACK_WRITER_POLICY_VERSION,
                           prompt_version=PW.NARRATION_PROMPT_VERSION,
                           model_policy=PW.MODEL_POLICY_PROVIDER_DEFAULT,
                           renderer_version=PW.WRITER_RENDERER_VERSION,
                           rules_version="p4-fin-rules-v1", max_llm_retries=1,
                           proposal_wire=REPLAY_WIRE, replay_source=REPLAY_SOURCE)


def _bundle_face(case: dict, plan: dict, *, manifest=None, scan=None) -> dict:
    """合并后的提案集 → **生产** `_build_pre_gate_bundle` → 活身份映射。

    这一步不是为了「再算一遍」，而是为了把失败串里那两条 `ccand_` 身份接到现场对象上：错误串
    只给 id，不给文本。修订、manifest、事实表全部按生产口径取（修订由**合并后草稿规格**算出，
    与 `write_section` 里那一次定修订的调用同式同参，首次生成即 `attempt=1`），因此这里得到的
    `candidate_id` 与链上那一束是同一批身份。

    身份**不能**与 r9 记录逐字相同：`draft_revision` 的身份体含写入策略与 prompt 版本串，而它们在
    本批之前已经前进过（r9 那两条 `ccand_*` 属于那一版代码）。本模块因此断言的是**机制**——
    「错误点名的正是这两条共享候选的现场身份」——并把记录值与现场值并列打印。
    """
    if manifest is None:
        manifest = PW._derive_material_manifest(case["authority"],
                                               material_context=case["context"])
    if scan is None:
        scan = PW.scan_authority(case["authority"], case["task"])
    policy = _replay_policy()
    draft_revision = NS.derive_draft_revision(
        natural_prose_digest=PW._prose_specs_digest(plan["natural_prose_draft"]),
        task_id=case["task"].task_id, section_id=case["task"].section_id,
        company_id=case["authority"].company_id, report_as_of=case["authority"].report_as_of,
        contract_version=case["authority"].contract_version,
        contract_fingerprint=case["authority"].contract_fingerprint,
        writer_policy_version=policy.policy_version, prompt_version=policy.prompt_version,
        model_policy=policy.model_policy, manifest_id=manifest.manifest_id,
        manifest_fingerprint=manifest.fingerprint(), attempt=1)
    bundle = PW._build_pre_gate_bundle(
        plan=plan, task=case["task"], authority=case["authority"], scan=scan, policy=policy,
        draft_revision=draft_revision, manifest=manifest, dependency_fingerprint=REAL_DEP)
    key_to_id = {str(spec["candidate_key"]): candidate.candidate_id
                 for spec, candidate in zip(plan["claim_candidates"], bundle.candidates)}
    unit_by_key = {str(spec["prose_key"]): unit
                   for spec, unit in zip(plan["natural_prose_draft"], bundle.prose_units)}
    declared: dict[str, list[str]] = {}
    for unit in bundle.prose_units:
        for candidate_id in unit.atom_candidate_ids:
            declared.setdefault(str(candidate_id), []).append(str(unit.prose_unit_id))
    return {"manifest": manifest, "policy": policy, "draft_revision": draft_revision,
            "bundle": bundle, "key_to_id": key_to_id, "unit_by_key": unit_by_key,
            "declared": declared,
            "candidate_total": len(bundle.candidates),
            "prose_total": len(bundle.prose_units)}


def _recorded_responses() -> list[dict]:
    """四份真实返回 → 交给链的 `NarrationResult` 字段（模型名逐字保留，不改写成 stub）。

    除内容外还带上**这一份返回当时回答的是哪一批**（`R7._expectation_of`：批次标签 / 批次 id /
    aspect 范围 / 重放序号，逐字取自那一份请求自己的 payload）。重放驱动据此逐次核对收到的
    请求，不按列表位置喂字节。
    """
    out = []
    for ordinal, stem, _label, _batch_id, *_counts in R9_COMPANY_CALLS:
        row = _read_call(stem)
        out.append({"kind": f"recorded_call_{ordinal}", "text": row["completion"],
                    "call_id": stem.split("__")[-1], "model": row["model"],
                    "input_tokens": row.get("input_tokens"),
                    "output_tokens": row.get("output_tokens"),
                    "latency_ms": row.get("latency_ms"),
                    "finish_reason": row.get("finish_reason"),
                    **R7._expectation_of(stem, order=ordinal)})
    return out


def _drive(case: dict, responses: list[dict]) -> dict:
    """把 case 的返回序列换成 `responses`，走 `R7._drive_company` 那条**生产**驱动。

    驱动本身不复制：同一份 `write_section` → `evaluate_claim_chain` → `_finalize_section`
    在 r7b / r8 两次重放里已经跑过，这里只换喂给它的字节；`case["responses"]` 是它留出的注入面。
    重放身份由本模块显式给出，不继承任何默认值（理由见 r8 重放模块的 `_drive`）。
    """
    c = dict(case)
    c["responses"] = responses
    return R7._drive_company(c, proposal_wire=REPLAY_WIRE, replay_source=REPLAY_SOURCE)


def _outcome_or_error(case: dict, responses: list[dict]):
    """跑一次驱动：返回 `("ok", out)` 或 `("error", exc)`。异常**不吞**，交给调用方如实报。"""
    try:
        return "ok", _drive(case, responses)
    except Exception as exc:  # noqa: BLE001  重放要如实报出任何一个首个阻断
        return "error", exc


def _drive_watching_bundles(case: dict, responses: list[dict]) -> dict:
    """跑一次驱动，并**只读观察**链上真正构造过的门前束（原样调用、原样返回、异常照抛）。

    为什么要看：被拒记录只给候选 **id**，不给文本；而 id 属于「哪一束的修订」并不是从记录里能
    读出来的事实。r9 的字节链上构造了 **三** 束——首轮那一束在组装 `SectionDraft` **之前**就被
    路径 B 的资格/期间判据打回并排定了候选裁出；候选裁出轮那一束真的走到了 `SectionDraft`，
    但被门前含糊期间判据打回并排定了 context 单元裁出；第三束（单元 10 → 8）才走到成稿。
    因此「被点名的是哪两条」必须按**对应那一束**的身份来对，不能拿首轮那一束去对——那会把一条
    id 对不上的读数误读成「机制不对」。

    这个观察层不做任何替换：它调用**同一个**生产函数并把它的返回值原样交回去，只在旁边记一笔。
    生产行为因此与不加观察时逐字相同（首轮那一束的身份另行与独立重算逐条对账，见 §1/§2）。

    同时在 `NS.SectionDraft.create` 上挂第二个观察点：「这一束的草稿层**闭合得上**」这句话
    需要可执行证据（r9 当时整节就死在这里）。记录只说明第一个阻断是什么——它不回答闭合问题——
    所以这里记下**真的被构造出来的 `SectionDraft`**：有、且是那两轮各一份、且段数与候选数对得上，
    才是「闭合通过」的可执行证据（也仅止于此：构造得出来、过得了门，都不等于内容合格）。
    """
    seen: list[dict] = []
    drafts: list[Any] = []
    original = PW._build_pre_gate_bundle
    original_create = NS.SectionDraft.create

    def spy(**kwargs):
        bundle = original(**kwargs)
        seen.append({"bundle": bundle, "draft_revision": kwargs["draft_revision"],
                     "plan": kwargs["plan"]})
        return bundle

    def spy_create(cls, **kwargs):
        draft = original_create(**kwargs)
        drafts.append(draft)
        return draft

    PW._build_pre_gate_bundle = spy
    NS.SectionDraft.create = classmethod(spy_create)
    try:
        kind, value = _outcome_or_error(case, responses)
    finally:
        PW._build_pre_gate_bundle = original
        NS.SectionDraft.create = original_create
    return {"kind": kind, "error": value, "bundles": seen, "drafts": drafts}


def _bundle_rows(bundle) -> dict:
    """一束的现场读数：候选身份 → 文本，草稿段 → 原子身份，以及「被多段声明的候选」。"""
    ids = {str(c.claim_text): str(c.candidate_id) for c in bundle.candidates}
    declared: dict[str, list[str]] = {}
    for unit in bundle.prose_units:
        for candidate_id in unit.atom_candidate_ids:
            declared.setdefault(str(candidate_id), []).append(str(unit.prose_unit_id))
    text_of = {str(c.candidate_id): str(c.claim_text) for c in bundle.candidates}
    unit_text = {str(u.prose_unit_id): str(u.text) for u in bundle.prose_units}
    #: 门前 `narrative_draft_units`（**context 衔接单元**，`ndu_*`）与它们的正文。它们与自然
    #: 草稿段（`npdu_*`）是两种不同的对象：前者只为 context 支撑边定位背景/结构/衔接，
    #: **不承载事实原子**。门前的含糊期间判据同时看这两边，因此读的人必须能分辨被点名的是哪一边。
    gate_unit_text = {str(u.draft_unit_id): str(u.text) for u in bundle.units}
    edges: dict[str, list[dict]] = {}
    for proposal in bundle.proposals:
        edges.setdefault(str(proposal.binding_subject_id), []).append({
            "material_id": str(proposal.material_id or ""),
            "container_id": str(proposal.authority_container_id),
            "support_role": str(proposal.support_role),
            "authorization_path": str(proposal.authorization_path)})
    return {"text_to_id": ids, "declared": declared, "text_of": text_of,
            "unit_text": unit_text, "gate_unit_text": gate_unit_text, "edges": edges,
            "prose_unit_ids": tuple(str(u.prose_unit_id) for u in bundle.prose_units),
            "candidate_count": len(bundle.candidates),
            "prose_count": len(bundle.prose_units),
            "follow_up_count": len(bundle.follow_up_specs),
            "revision": bundle.candidates[0].draft_revision if bundle.candidates else ""}


# ---------------------------------------------------------------------------
# §3 财务最终句 B 的真实返回（只读；不调用模型）
# ---------------------------------------------------------------------------

def _final_sentence_face() -> dict:
    """r9 那一次最终句核验的请求面 + 返回面 + 生产解释器在它上面的读数。

    **零调用**：这里不构造 `SentenceFidelityBundle`、不调 `evaluate_final_sentence_fidelity`。
    用的只有两件生产实现——`FSF.locate_fact_atoms`（机械定位）与
    `FSF.interpret_sentence_fidelity_output`（输出解释与 fail-closed）——它们在留存字节上给出的
    读数，就是「模型这条返回能不能形成决定」这件事在链上的读数。
    """
    record = _read_call(R9_FIN_CALL)
    payload = _payload(R9_FIN_CALL)
    sentence = payload["sentences"][0]
    claims = {c["claim_id"]: c["text"] for c in payload["claims"]}
    located = FSF.locate_fact_atoms(sentence_id=sentence["sentence_id"], text=sentence["text"],
                                    claim_texts=claims)
    completion = json.JSONDecoder(strict=False).raw_decode(record["completion"])[0]
    model_atoms = tuple(str(a["atom_surface"]) for row in completion["per_sentence"]
                        for a in row["atoms"])
    verdicts = tuple(str(a["verdict"]) for row in completion["per_sentence"] for a in row["atoms"])
    try:
        atoms, verdict, reason = FSF.interpret_sentence_fidelity_output(
            completion, sentence_ids=(sentence["sentence_id"],),
            located_by_sentence={sentence["sentence_id"]: tuple(
                a.atom_surface for a in located)})
        interpreted = {"ok": True, "atoms": tuple(a.atom_surface for a in atoms),
                       "verdict": verdict, "reason_code": reason, "error": None}
    except FSF.FinalSentenceFidelityError as exc:
        interpreted = {"ok": False, "atoms": (), "verdict": None, "reason_code": None,
                       "error": f"{type(exc).__name__}: {exc}"}
    return {"record": record, "payload": payload, "sentence": sentence, "claims": claims,
            "located": tuple(a.atom_surface for a in located),
            "located_kinds": tuple(a.atom_kind for a in located),
            "payload_located": tuple(str(a["atom_surface"]) for a in payload["located_atoms"]),
            "model_atoms": model_atoms, "verdicts": verdicts,
            "completion": completion, "interpreted": interpreted}


def _decision_rows(section_id: str) -> int:
    """r9 产物里这一节的最终句决定**落库读回**行数（`gate_summary` 的证据面）。"""
    report = _read_json(R9 / "acceptance_report.json")
    for gate in report.get("gates") or ():
        rows = (gate.get("evidence") or {}).get("row_counts") or {}
        row = rows.get(section_id) or {}
        if "current_section_final_sentence_decision_v2" in row:
            return int(row["current_section_final_sentence_decision_v2"])
    raise AssertionError("r9 验收报告里没有最终句决定的读回行数：不得默认成 0")


# ---------------------------------------------------------------------------
# 判据
# ---------------------------------------------------------------------------

def _check_baseline(check, check_eq, details: list[str]) -> None:
    details.append("## §0 r9 现场基线（逐字取自记录，不做推断）")
    recorded = [_read_call(stem) for _n, stem, *_rest in R9_COMPANY_CALLS]
    check_eq({str(r["prompt_version"]) for r in recorded}, {R9_RECORDED_PROMPT_VERSION},
             "4 份公司留存返回的 `prompt_version` 逐份都是 r9 当时那一版（不是当前版）")
    check_eq({str(r["model"]) for r in recorded}, {R9_RECORDED_MODEL},
             "4 份留存返回的模型逐份都是 r9 当时那个模型")
    check(all(r["completion"] for r in recorded),
          "4 份留存返回都有 `completion` 正文（没有空返回被当成返回）")
    check_eq(tuple(str(_payload(stem)["task"]["task_id"]) for _n, stem, *_r in R9_COMPANY_CALLS),
             (R9_TASK_ID,) * len(R9_COMPANY_CALLS),
             "4 份请求的任务 id 逐份等于 r9 记录值（它进候选身份，写错就复现出另一批身份）")
    check_eq(tuple(str(_payload(stem)["authority"]["report_as_of"])
                   for _n, stem, *_r in R9_COMPANY_CALLS),
             (R9_REPORT_AS_OF,) * len(R9_COMPANY_CALLS),
             "4 份请求的 `report_as_of` 逐份等于 r9 记录值")
    check_eq(tuple(str(_payload(stem)["projection"]["projection_id"])
                   for _n, stem, *_r in R9_COMPANY_CALLS),
             (R9_PROJECTION_ID,) * len(R9_COMPANY_CALLS),
             "4 份请求的投影 id 逐份等于 r9 记录值")
    check_eq(tuple(len(_payload(stem).get("authority_facts") or ())
                   for _n, stem, *_r in R9_COMPANY_CALLS), (0, 0, 0, 0),
             "四批请求的 `authority_facts` **都是 0 条**：本节没有一条路径 A 事实行可指"
             "（这是 r9 的现场读数，不是本模块的产物；C 段要修的就是它）")
    check_eq(tuple(len(_payload(stem)["materials"]) for _n, stem, *_r in R9_COMPANY_CALLS),
             (R9_MATERIAL_COUNT,) * len(R9_COMPANY_CALLS),
             f"四批请求**每一批**都收到全部 {R9_MATERIAL_COUNT} 条材料（整节材料目录不按批裁剪）")

    ledger = [r for r in _read_json(R9 / "llm_call_ledger.json")["ledger"]["attempts"]
              if str(r.get("category")) == "narration" and str(r.get("section_id")) == "company"]
    check_eq(tuple((int(r["ordinal"]), str(r["call_id"]), str(r["prompt_version"]))
                   for r in ledger),
             tuple((ordinal, stem.split("__")[-1], R9_RECORDED_PROMPT_VERSION)
                   for ordinal, stem, *_rest in R9_COMPANY_CALLS),
             "r9 台账里**公司节的 narration 调用恰好就是这 4 次**（序号 239–242、call_id 逐条对上）："
             "本节四批字节的来源是 r9 那一轮的原始记录，既没有掺进别的 run，也没有第五条被合成出来")
    check_eq(tuple(str(r["status"]) for r in ledger), ("ok",) * len(R9_COMPANY_CALLS),
             "这 4 次调用在 r9 台账里逐条都是 `ok`（不是拿失败/空返回当返回）")

    fu = _read_json(R9 / "follow_up_needs.json")
    check_eq(tuple(str(s) for s in fu["sections_completed"]), R9_RECORDED_SECTION_DRAFTS,
             "r9 记录里只有财务与行业两节完成：公司节没有任何记录在案的草稿/Claim/正文/结果")
    check("company" not in fu["sections"],
          "公司节在补件台账里**连一行都没有**：20 条诉求随异常一起蒸发"
          f"（记录在案的 sections = {sorted(fu['sections'])}，B 段要接上的那一格）")
    check_eq(int(fu["total_pending_needs"]), 0,
             "整轮待裁决补件提议总数为 0（公司节那 20 条不在台账里）")
    fd = _read_json(R9 / "failure_diagnostics.json")["sections"]["company"]
    check_eq(str(fd["section_error"]).split(": ", 1)[-1], R9_RECORDED_ERROR,
             "公司节的 `section_error` 逐字等于记录值（同一判据、同样两条候选身份）")
    check_eq(fd["has_section_draft"], False, "公司节确实没有 `SectionDraft`")
    check_eq(dict(fd["authority_coverage"]), {"partial": 25, "blocked": 21},
             "公司节 46 个 aspect 的权威覆盖逐数是 partial 25 / blocked 21")
    report = _read_json(R9 / "acceptance_report.json")
    gates = {g["gate_id"]: g["status"] for g in report["gates"]}
    check_eq(gates.get("A1"), "fail", "验收报告 A1（公司节真实材料 → 正文）为 `fail`（不读成通过）")
    check_eq(report["status"], "fail", "整轮状态 `fail`：本模块不改写、不重标")
    details.append(f"  * 留存字节的 prompt 版本 = `{R9_RECORDED_PROMPT_VERSION}`；当前代码的 "
                   f"`PW.NARRATION_PROMPT_VERSION` = `{PW.NARRATION_PROMPT_VERSION}`")
    details.append(f"  * r9 那一条现场错误（`failure_diagnostics` / `acceptance_report` 两处逐字相同）："
                   f"{R9_RECORDED_ERROR}")


def _check_merge_face(check, check_eq, face: dict, details: list[str]) -> None:
    details.append("")
    details.append("## §1 合并面：四批返回 → 一份完整有序提案集（生产解析器 + 生产合并器）")
    details.append("")
    details.append("| 序号 | 批次 | 批 id | aspect 数 | 本批 authority_facts | 材料行 |"
                   " 返回(候选/草稿/单元/补件) 记录 → 解析 |")
    details.append("|---|---|---|---|---|---|---|")
    for row in face["rows"]:
        rec, parsed = row["recorded"], row["parsed"]
        details.append(
            f"| {row['ordinal']} | {row['label']} | `{row['batch_id']}` | {row['aspect_count']} | "
            f"{row['authority_facts']} | {row['material_count']} | "
            f"{rec['candidates']}/{rec['prose']}/{rec['units']}/{rec['follow_ups']} → "
            f"{parsed['candidates']}/{parsed['prose']}/{parsed['units']}/{parsed['follow_ups']} |")
    for row in face["rows"]:
        check_eq(row["parsed"], row["recorded"],
                 f"批次 {row['label']} 的返回解析结果与 r9 记录的计数逐项相等"
                 "（解析器没有丢条、没有多造）")
        check_eq(row["authority_facts"], 0,
                 f"批次 {row['label']} 的请求里 `authority_facts` 为 0 条（照实读，不补造）")
    check_eq(face["ref_disagreement"], (),
             "四批请求**自己声明**的短把手（`mN`）逐行指向的材料，与生产别名表（`_support_aliases"
             "(scan, manifest)`，与 `write_section` 同一派生）逐行相同——留存返回里的 `mN` 在"
             "本重放里指的还是那份材料（材料**顺序**也相同）")
    check_eq(face["unknown_members"], 0,
             "草稿段点名过的每一个成员引用（`wmmref_*`）在生产 manifest 里都**存在**"
             "（出处闭得上：没有一段草稿指向一份不在本次材料清单里的材料）")
    check_eq(face["off_record_materials"], 0,
             "这些成员指到的材料逐条都在 r9 请求声明的 40 条材料里"
             "（材料集没有在本重放里被换掉）")
    check_eq(face["container_mismatch"], R9_MATERIAL_COUNT * len(R9_COMPANY_CALLS),
             f"{len(R9_COMPANY_CALLS)} 批 × {R9_MATERIAL_COUNT} 行材料的**容器**（`pack_id`）在"
             "本重放里都是重算值、与记录**逐行不同**（这是现场读数，不是断言失败：本重放不主张"
             "容器 id 可离线复现——材料级身份面才是可复现的，见 §0/§2 两项对账）")
    details.append(f"  * 容器差异：{face['container_mismatch']} 行（{len(R9_COMPANY_CALLS)} 批 × "
                   f"{R9_MATERIAL_COUNT} 行）逐行不同；**材料级身份**（材料 id / 内容指纹 / "
                   "locator / 来源身份 / 载荷哈希）逐条相同，且请求里的 `mN` 短把手逐行仍指向"
                   "同一份材料（上面两条断言）")
    counts = {"candidates": face["candidate_total"], "prose": face["prose_total"],
              "units": face["unit_total"], "follow_ups": face["follow_up_total"]}
    check_eq(counts, R9_MERGED_COUNTS,
             "合并后条数与现场读数逐项相等（候选/自然草稿/草稿单元/补件诉求）")
    requested = tuple(str(a) for a in face["audit"]["aspects_requested"])
    check_eq(len(requested), R9_COMPANY_ASPECT_COUNT,
             f"合并审计记下的 aspect 请求共 {R9_COMPANY_ASPECT_COUNT} 条（本节 aspect 全集）")
    check_eq(len(set(requested)), len(requested),
             "这 46 条 aspect 两两不交（没有任何一个 aspect 落在两批里，也就没有一批替另一批"
             "回答了别批的问题）")
    check_eq(requested, tuple(a for row in face["rows"] for a in row["aspect_ids"]),
             "审计里那 46 条请求**逐条逐序**等于四批各自 aspect 的拼接"
             "（既没缺、也没多请求一条；合并不靠「别批的输出顶替」补覆盖）")
    check_eq(tuple(len(row["aspect_ids"]) for row in face["rows"]),
             tuple(row["aspect_count"] for row in face["rows"]),
             "逐批 aspect 数等于 r9 记录的划分（12/12/12/10）")
    details.append("")
    details.append(f"合并：候选 {face['candidate_total']} 条 / 自然草稿 {face['prose_total']} 段 / "
                   f"草稿单元 {face['unit_total']} 条 / 补件诉求 {face['follow_up_total']} 条；"
                   f"被**多个**草稿段声明的候选 {len(face['shared_groups'])} 条")
    check_eq(len(face["shared_groups"]), len(R9_SHARED_GROUPS),
             "被多个草稿段声明的候选恰好两组（这就是「同文、不同年报出处」重复归属的现场）")
    for group, expected in zip(face["shared_groups"], R9_SHARED_GROUPS):
        details.append("")
        details.append(f"### 共享候选 `{group['candidate_key']}`："
                       f"{group['candidate_text']}")
        details.append("")
        details.append("| 支撑边 | 材料 | 来源角色 | 文档 | 文档版本 | 路径 |")
        details.append("|---|---|---|---|---|---|")
        for edge in group["support"]:
            material_id = str(edge.get("material_id") or "")
            unit_members = [m for u in group["units"] for m in u["members"]
                            if m["material_id"] == material_id]
            role = unit_members[0]["source_role"] if unit_members else ""
            axes = unit_members[0]["document_axes"] if unit_members else None
            details.append(
                f"| {str(edge.get('container_id') or '')[:8]}… | `{material_id}` | {role} | "
                f"{axes[0] if axes else ''} | {axes[1] if axes else ''} | "
                f"{edge.get('authorization_path')} |")
        details.append("")
        details.append("| 草稿段 | 该段的材料出处 | 该段声明的原子 | 该段**独有**的原子 | 该段正文 |")
        details.append("|---|---|---|---|---|")
        for unit in group["units"]:
            members = "、".join(f"`{m['material_id']}`（{m['source_role']}）"
                             for m in unit["members"])
            details.append(f"| {unit['prose_key']} | {members} | "
                           f"{'、'.join(unit['atom_candidate_keys'])} | "
                           f"{'、'.join(unit['unique_atoms']) or '—'} | "
                           f"{unit['text']} |")
        check_eq(group["candidate_text"], expected["candidate_text"],
                 f"共享候选 {group['candidate_key']} 的原子命题与现场读数逐字相同")
        check_eq(tuple(u["prose_key"] for u in group["units"]), expected["units"],
                 f"声明该候选的草稿段就是现场那几段（{expected['units']}）")
        for unit in group["units"]:
            check_eq(unit["unique_atoms"], expected["unique_atoms"][unit["prose_key"]],
                     f"草稿段 {unit['prose_key']} 的独有原子逐条保留（B 段不得删掉它们）")
            roles = tuple(m["source_role"] for m in unit["members"])
            check(expected["roles"][unit["prose_key"]] in roles,
                  f"草稿段 {unit['prose_key']} 的材料出处里含 "
                  f"`{expected['roles'][unit['prose_key']]}`（两组共享候选正因出处不同才各有各的段）")
            documents = [m["document_axes"] for m in unit["members"] if m["document_axes"]]
            check(all(doc[0] != "NDSD_2024_year" for doc in documents)
                  or any(doc[0] == "NDSD_2024_year" for doc in documents),
                  f"草稿段 {unit['prose_key']} 的文档轴可读（"
                  f"{sorted({d[0] for d in documents})}）")
    details.append("")
    shared_ids = sorted(f"c{g['candidate_key'][1:]}" for g in face["shared_groups"])
    details.append(f"  * 两组共享候选在合并序里的位置：{shared_ids}；"
                   f"它们各自有 2 条支撑边（两条边指向**不同文档、不同来源角色**），"
                   f"这正是「同一段话在两份年报里都出现」的现场形状——"
                   f"候选按原子命题合并成一条，草稿段却按出处各自成段")


def _material_faces(case: dict) -> dict:
    """材料身份 → 文档轴与来源角色（供支撑边那两列用）。

    两者都取自**记录**（Pack 材料的 locator 与请求里那一行的 `source_role`），不是本模块另判的：
    本模块只负责把「这条边落在哪份文档、哪一种角色上」印出来。
    """
    axes: dict[str, tuple[str, str]] = {}
    for entry in case["entries"]:
        loc = entry["locator"]
        axes[str(entry["material_id"])] = (str(loc["document_id"]), str(loc["document_version"]))
    roles: dict[str, str] = {}
    for row in case["req1"]["materials"]:
        roles.setdefault(str(row["material_id"]), str(row.get("source_role") or ""))
    return {"axes": axes, "roles": roles}


def _check_reproduction(check, check_eq, case: dict, responses: list[dict], face: dict,
                        details: list[str]) -> None:
    details.append("")
    details.append("## §2 复现：同一批字节走生产链"
                   "（三轮门前束 → 两份 `SectionDraft` → `SectionResult`）")
    details.append("")
    watch = _drive_watching_bundles(case, responses)
    seen = watch["bundles"]
    check_eq(len(seen), 3,
             "链上为这批字节构造了**三**束门前束：首轮那一束在组装 `SectionDraft` **之前**就被"
             "路径 B 的资格/期间轴打回，并就地排定**候选**裁出；候选裁出轮那一束草稿层闭合、"
             "`SectionDraft` 真的构造出来了，但被含糊期间判据打回，并就地排定 **context 单元**"
             "裁出；第三束（context 单元 10 → 8、候选与草稿段一条不动）才走到成稿")
    if not seen:
        details.append("  * 链上一束都没构造出来：本模块不猜现场形状")
        return
    if len(seen) != 3:
        details.append(f"  * 链只构造出 {len(seen)} 束：本节以下断言按**三束**的现场形状写，"
                       "读数已变即不再适用——如实红在上面那一条，不静默跳过")
        return
    first = _bundle_rows(seen[0]["bundle"])
    mid = _bundle_rows(seen[1]["bundle"])
    last = _bundle_rows(seen[2]["bundle"])
    reports = (("首轮", first, seen[0]), ("候选裁出轮", mid, seen[1]),
               ("context 单元裁出轮（成稿轮）", last, seen[2]))
    details.append("")
    details.append("| 束 | 修订 | 候选 | 自然草稿段 | context 单元 | 补件诉求 | 被多段声明的候选 |")
    details.append("|---|---|---|---|---|---|---|")
    for label, rows, shape in reports:
        dups = sorted(cid for cid, ups in rows["declared"].items() if len(ups) > 1)
        details.append(f"| {label} | `{rows['revision']}` | {rows['candidate_count']} | "
                       f"{rows['prose_count']} | {len(shape['bundle'].units)} | "
                       f"{rows['follow_up_count']} | "
                       f"{'、'.join(f'`{c}`' for c in dups) or '—'} |")
    check_eq(tuple(rows["candidate_count"] for _l, rows, _s in reports),
             RECORDED_CANDIDATE_COUNTS,
             "三束的候选数按现场读数：首轮 40 → 候选裁出后 7 → context 单元裁出后**仍是 7**"
             "（第二道裁出只撤 context 单元，不碰候选）")
    check_eq(tuple(len(shape["bundle"].units) for _l, _r, shape in reports),
             RECORDED_CONTEXT_UNIT_COUNTS,
             "三束的 context 单元数按现场读数：10 → 10 → 8（少了的那两条正是被逐项撤下的）")

    # (a) 首轮那一束 == 本模块的独立重算（材料/别名/修订三面逐条对账）
    recomputed = [str(c.candidate_id) for c in face["bundle"].candidates]
    observed = [str(c.candidate_id) for c in seen[0]["bundle"].candidates]
    check_eq(observed, recomputed,
             "首轮那一束的候选身份逐条等于本模块**独立重算**的那一份"
             "（同一 plan、同一 manifest、同一修订 ⇒ 同一批身份；这同时证明下面那条 id 对账"
             "不是自说自话）")
    check_eq(first["revision"], face["draft_revision"], "首轮那一束的修订等于独立重算值")

    # (b) 两组共享候选在三束里都在，且各自恰好被 2 段声明（第一道裁出的候选不再连坐它们）
    for label, rows, _shape in reports:
        for text in (g["candidate_text"] for g in R9_SHARED_GROUPS):
            candidate_id = rows["text_to_id"][text]
            check_eq(len(rows["declared"].get(candidate_id) or ()), 2,
                     f"{label}那一束里，候选「{text}」被**恰好 2 段**草稿声明"
                     "（唯一性判据开火的那一格）")

    live_ids = {str(c.candidate_id) for _l, _r, shape in reports
                for c in shape["bundle"].candidates}
    check(not (live_ids & set(R9_RECORDED_SHARED_IDS)),
          "r9 记录的那两个字面 id **不在**任何一束的现场身份里：它们属于 r9 当时那一版代码的"
          "修订（`draft_revision` 的身份体含写入策略与 prompt 版本串），本模块不把历史 id 当"
          "期望值贴上去，也不为了对上它去改身份")

    if watch["kind"] != "ok":
        # 第三次改动之后这条链本该走通。读数一旦变回去，本节以下全部断言（typed 记录、
        # 裁出去向、`SectionResult`、补件去向）说的都是另一件事实。**不静默跳过**：如实红一条，
        # 让人来看现场，而不是把「读数已变」掩盖成一次通过。
        check(False,
              "这批 r9 字节在当前代码下**走通了整节**（链应产出 `SectionResult`）："
              f"实得 kind={watch['kind']!r}——现场读数已变，本节以下断言不再适用，必须重读")
        details.append(f"  * 阻断：{type(watch['error']).__name__}: {watch['error']}")
        return
    out = watch["error"]
    oc = out["outcome"]
    result = out["output"].result
    planned = [c for r in oc.batch_audit["rounds"] for c in (r.get("batch_calls") or ())]
    check_eq(tuple(str(c["call_id"]) for c in planned),
             tuple(stem.split("__")[-1] for _n, stem, *_rest in R9_COMPANY_CALLS),
             "链**自己**记下的本轮批次调用就是 r9 那 4 次（call_id 逐条同序）："
             "现场喂进去的字节与台账对得上，不存在第五批或外来批")
    details.append("")
    details.append(f"  * 链走通：`{type(result).__name__}` "
                   f"`{result.section_result_id}`（状态 `{result.status}`，"
                   f"{len(result.claims)} 条 Claim、{len(result.unresolved)} 条缺口）")

    # (c) r9 现场那一条死因**不再出现**，而且那颗异常也不再是链的终点。
    records = tuple(getattr(oc, "rejections", ()))
    details_text = " ".join(str(r.rejection_detail) for r in records)
    check("不闭合" not in details_text and "原子归属必须唯一" not in details_text,
          "r9 现场那一句「自然草稿不闭合：同一候选被多个自然草稿单元声明」在整条链的任何一处"
          "typed 记录里**都不再出现**：共享候选的两处 occurrence 各自对上了它自己的支撑边"
          "（不同容器/材料/文档、不同来源角色），闭合核对因此判它成立——这正是 B 段要修的那件事")
    check_eq(tuple(int(r.attempt) for r in records), RECORDED_REJECTION_ATTEMPTS,
             "被拒的是**前两轮**（attempt 1、2），第三轮不再被拒 ⇒ 重试额度没有用尽，"
             "`ProposalSetRejectedError`（上一批现场的首个阻断、也是唯一的抛出点）没有被触发："
             "链在记录完两轮 typed 审计之后继续往前找了合法出口，"
             "而不是把整节连同两轮共 40 条补件诉求一起抛出")

    # (d) 草稿层真的闭合了：链上**构造出**两份 `SectionDraft`（被拒的两轮各一份）。
    built = watch["drafts"]
    check_eq(len(built), 2,
             "链上构造出 **2** 份 `SectionDraft`：候选裁出轮那份（10 条 context 单元，被含糊期间"
             "判据打回）与 context 单元裁出轮那份（8 条）。r9 现场是在**第一份之前**就死的"
             "（归属判据），本重放不但走到了组装，还走过了门前硬门")
    if len(built) == 2:
        check_eq(tuple(str(d.draft_revision) for d in built),
                 (mid["revision"], last["revision"]),
                 "两份 `SectionDraft` 的修订按现场顺序：先候选裁出轮、后 context 单元裁出轮")
        check_eq(tuple(len(d.natural_prose_draft) for d in built),
                 (mid["prose_count"], last["prose_count"]),
                 "两份都带着该轮**全部**的草稿段（共享候选的两处 occurrence 都在里面）")
        check_eq(tuple(len(d.narrative_draft_units) for d in built),
                 RECORDED_CONTEXT_UNIT_COUNTS[1:],
                 "两份的 **context 单元**数是 10 → 8（第二道裁出撤下的那两条不再出现在新草稿里）")
    check_eq(len([cid for cid, ups in last["declared"].items() if len(ups) > 1]), 2,
             "成稿轮那一束里仍有 **2** 条候选被两段草稿声明（多 occurrence 不是被删掉的，"
             "而是被逐条核对支撑之后**允许**的）")

    # (e) 两轮各留一条 typed 记录，逐束审计完整、门前留存齐备。
    check_eq(tuple(type(r).__name__ for r in records),
             (RECORDED_REJECTION_RECORD_TYPE,) * len(RECORDED_REJECTION_KINDS),
             "被拒的两轮各留一条**写侧** typed 记录（不是裸 schema 异常：r9 现场那颗直接冲出"
             "`write_section`，20 条补件诉求因此随异常蒸发）")
    check_eq(tuple(str(r.rejection_kind) for r in records), RECORDED_REJECTION_KINDS,
             "两轮的原因码按现场顺序取：先路径 B 高风险表面，后门前含糊期间")
    for record in records:
        check_eq(len(record.candidate_audit), len(record.candidate_ids),
                 f"第 {record.attempt} 轮那条记录的逐候选审计**键集等于该束完整有序候选身份**"
                 f"（{len(record.candidate_ids)} 条，不裁剪、不重排、不补）")
        check(record.retained_pre_gate is not None,
              f"第 {record.attempt} 轮那条记录带着门前已写出来的内容的**只读留存**"
              "（「被拒的时候长什么样」可回查，不需要重跑）")
        check(record.reproposal is None,
              f"第 {record.attempt} 轮不向模型重问任何东西（`reproposal=None`）："
              "被拒的是提案集本身，出路是**确定性裁出**，不是再问一次")
    high_record, gate_record = records

    # (f) 候选裁出轮被打回的那道门：含糊期间。判据、命中词与位置全部取自结构化读数。
    issues = _gate_issues(gate_record.rejection_detail)
    check_eq(sorted({str(i["rule"]) for i in issues}), ["narrative_vague_period"],
             "候选裁出轮那一束在门前硬门上只有**一**条判据开火：未绑定权威期间的含糊措辞"
             "（§十二 4 的 blocking 规则，不是本批新加的判据）")
    vague_units = {uid: text for uid, text in mid["gate_unit_text"].items()
                   if NS.vague_period_hits(text)}
    check_eq(sorted(str(i["location"]) for i in issues), sorted(vague_units),
             "被点名的正是候选裁出轮那两段**文本命中「报告期」的 context 衔接单元**"
             "（判据与命中词取自 `NS.vague_period_hits` 的唯一实现，本模块不另立词表）")
    check(all(uid not in mid["prose_unit_ids"] for uid in vague_units),
          "被点名的两段**不是**自然草稿段（`npdu_*`）：那一轮 7 段草稿里没有一段含含糊期间"
          "——开火的是 `narrative_draft_units` 那一侧（context 单元无权承载"
          "「未发生变更」「未发生重大诉讼/仲裁」这类**明确否定**）")
    for uid, text in sorted(vague_units.items()):
        details.append("")
        details.append(f"  * 被点名单元 `{uid}`（context 衔接单元）：{text}")

    # (g) `cco-6` 的裁决：**逐项 typed 排除**——不是删字、不是改写、也不是连坐。
    high_carve = high_record.carve_out
    gate_carve = gate_record.carve_out
    check(high_carve is not None,
          "首轮那条记录排定的是**逐候选裁出**（不向模型重问任何东西）：携带高风险表面的"
          "那几条被逐条排除，其余以新修订继续——补件诉求因此没有随候选一起被作废")
    check(gate_carve is not None,
          "候选裁出轮那条记录也排定了下一步：`cco-6` 允许裁出的**第二类主体**就是 context 单元"
          "（`narrative_gate_blocking` 因此进了可裁 kind 表；候选那一轴的合法出口仍是重绑到"
          "路径 A 预验证事实或撤回，不是裁出）")
    if high_carve is None or gate_carve is None:
        details.append("  * 有一轮没有排定出路：本节以下裁出读数不再适用")
        return
    high_decision = high_carve.decision
    gate_decision = gate_carve.decision
    details.append("")
    details.append("| 裁出轮 | 版本 | 轴 | 撤下候选 | 留下候选 | 撤下 context 单元 | "
                   "留下 context 单元 | 新增模型调用 |")
    details.append("|---|---|---|---|---|---|---|---|")
    for label, trace, decision, axis in (
            (f"候选裁出（attempt {high_decision.from_attempt} → {high_decision.to_attempt}）",
             high_carve, high_decision, "候选"),
            (f"context 单元裁出（attempt {gate_decision.from_attempt} → "
             f"{gate_decision.to_attempt}）", gate_carve, gate_decision, "context 单元")):
        details.append(f"| {label} | `{decision.version}` | {axis} | "
                       f"{len(decision.excluded)} | {len(decision.surviving_candidate_ids)} | "
                       f"{len(decision.excluded_context_units)} | "
                       f"{len(decision.source_draft_unit_ids)} | {trace.model_calls_added} |")
    check_eq(gate_decision.version, PW.CANDIDATE_CARVE_OUT_VERSION,
             "第二道裁出用的规则版本就是当前版本")
    check_eq(len(high_decision.excluded),
             RECORDED_CANDIDATE_COUNTS[0] - RECORDED_CARVE_OUT_CANDIDATES,
             "候选裁出的现场读数：首轮 40 条里撤下 33 条（都是携高风险表面/资格不符的那些）")
    check_eq(len(high_decision.surviving_candidate_ids), RECORDED_CARVE_OUT_CANDIDATES,
             "候选裁出留下 7 条（没有连坐：同一批模型输出里其余完全合法的候选照常前进）")
    check_eq(len(gate_decision.excluded), 0,
             "context 单元裁出**一条候选都没撤**：第二道裁出只作用于单元那一轴")
    check_eq(len(gate_decision.surviving_candidate_ids), RECORDED_CARVE_OUT_CANDIDATES,
             "第二道裁出之后候选仍是那 7 条（候选文本一字未改）")
    check_eq(gate_decision.source_draft_unit_ids,
             tuple(u.draft_unit_id for u in seen[1]["bundle"].units),
             "裁决里的原束单元身份**恰好**等于候选裁出轮那一束的完整有序单元身份"
             "（不裁剪、不重排、不补）")
    check_eq(tuple(d.draft_unit_id for d in gate_carve.context_unit_destinations),
             gate_decision.source_draft_unit_ids,
             "单元去向表的键集恰好等于原束完整有序身份（「单元悄悄消失」因此无从表达）")
    check_eq(gate_carve.next_draft_unit_ids,
             tuple(u.draft_unit_id for u in seen[2]["bundle"].units),
             "回填出的新束单元身份**恰好**等于成稿轮那一束的现场有序身份（是证实，不是承诺）")
    check_eq(gate_carve.model_calls_added, 0,
             "`cco-6` 不新增任何模型调用——这一点写在产物里，不留在注释里")

    excluded_units = gate_decision.excluded_context_units
    check_eq(tuple(u.text for u in excluded_units), RECORDED_CONTEXT_UNIT_EXCLUDED,
             "被逐项撤下的正是那**两条**越权 context 单元，文本逐字等于 r9 留存字节里那两句")
    check(all(NS.vague_period_hits(t) for t in RECORDED_CONTEXT_UNIT_EXCLUDED),
          "这两句确实命中 `NS.vague_period_hits`（判据的唯一实现）——"
          "不是本模块为了撤它们另立了一条词表")
    for unit in excluded_units:
        check_eq(tuple(unit.reasons), ("narrative_vague_period",),
                 f"单元 `{unit.draft_unit_id}` 的排除原因取自**封闭词表**"
                 "`CONTEXT_UNIT_CARVE_OUT_REASONS`（与候选侧那五个原因码同一约定、各自一张表）")
        check_eq(tuple(unit.hit_phrases), ("报告期",),
                 f"单元 `{unit.draft_unit_id}` 逐字留下了判据命中的表面："
                 "「哪一条原因」与「哪几个字」分开记，复核者不必自己再跑一遍判据")
        check(unit.excluded and unit.next_draft_unit_id == "",
              f"单元 `{unit.draft_unit_id}` 是「被撤下」那一行（`next_draft_unit_id` 为空、"
              "带全部排除证据）")
        check_eq(unit.unit_kind, "paragraph",
                 f"单元 `{unit.draft_unit_id}` 的形态原样留档（`paragraph`）")
        check_eq(len(unit.context_member_refs), 2,
                 f"单元 `{unit.draft_unit_id}` 当时声明的两条 context 出处逐条留档："
                 "撤下的是一段文字，它**当时声明的来源**不能跟着一起消失")
        check(all(str(r).startswith("wmmref_") for r in unit.context_member_refs),
              f"单元 `{unit.draft_unit_id}` 的来源写的是**成员身份**"
              "（`(pack_id, material_id)` 经本次精确 manifest 解析出的 `wmmref_*`），"
              "不是裸的 `(container, material)` 二元组冒充身份")
    original_text = {str(u.draft_unit_id): str(u.text) for u in seen[1]["bundle"].units}
    survivors = [d for d in gate_carve.context_unit_destinations if d.next_draft_unit_id]
    check_eq(len(survivors), len(gate_decision.source_draft_unit_ids) - len(excluded_units),
             "原束 10 条单元里 8 条**原样带过**、2 条撤下——逐项排除不是整段清空")
    check(all(str(d.draft_unit_id) in original_text for d in survivors),
          "幸存行的 `draft_unit_id` 指的是**原束**身份（旧身份归裁决、新身份归回填，两者不同名）")
    check(all(d.text == original_text[str(d.draft_unit_id)] for d in survivors),
          "幸存行的文本逐字等于原束那一段（回填只换身份、不换一个字）")
    check_eq(tuple(d.text for d in survivors)[-1], RECORDED_CONTEXT_UNIT_SURVIVOR,
             "幸存者里那一段同属单元轴的明确否定原样保留：它的期间是**显式日期**"
             "（「截至募集说明书签署日」），不是无定义指代——因此不撤、也不改写")
    check_eq(tuple(str(u.text) for u in seen[2]["bundle"].units),
             tuple(t for t in (str(u.text) for u in seen[1]["bundle"].units)
                   if t not in RECORDED_CONTEXT_UNIT_EXCLUDED),
             "新束的单元正文**恰好**等于原束去掉那两句之后的有序剩余：被撤的只有点名的那两条，"
             "其余独立成形的经营描述一条不少、顺序不变、文本一字不改")

    # (h) 走到 `SectionResult`：`SECTION_BLOCKED` 与全部缺口如实保留，正文里没有那两条否定。
    check_eq(str(result.status), RECORDED_RESULT_STATUS,
             "本节**第一次**产出 `SectionResult`，但状态是 `SECTION_BLOCKED`："
             "有内容不等于合格，48 条缺口一条没有被「成稿了」这件事抹掉")
    check_eq(len(result.claims), RECORDED_CARVE_OUT_CANDIDATES,
             "7 条合法候选一条不少地成为 `SectionClaim`")
    check_eq({str(c.claim_candidate_id) for c in result.claims},
             {str(c.candidate_id) for c in seen[2]["bundle"].candidates},
             "Claim 的候选身份**恰好**等于成稿轮那一束的 7 条候选身份："
             "没有凭空多一条，也没有哪条合法候选被丢掉")
    claim_text = {str(c.candidate_id): str(c.claim_text) for c in seen[2]["bundle"].candidates}
    check(all(str(c.text) == claim_text[str(c.claim_candidate_id)] for c in result.claims),
          "每条 Claim 的正文逐字等于它那条候选的原子命题（审核没有偷偷改写候选文本）")
    body = str(result.markdown)
    for text in RECORDED_CONTEXT_UNIT_EXCLUDED:
        check(text not in body,
              f"越权 context 单元不进正文：`{text[:18]}…` 在正文里**找不到**"
              "（撤下是按单元逐项做的，正文里也没有它的任何残句）")
    for probe in ("未发生变更", "未发生重大诉讼", "未发生重大债务违约",
                  "截至募集说明书签署日"):
        check(probe not in body,
              f"正文里也没有 `{probe}` 这类**明确否定**的痕迹："
              "context 单元不承载事实，撤下它们不会靠改写成正面句来「保住」内容")
    check_eq(body.count("报告期"), len(RECORDED_PERIOD_GAP_TOPICS),
             "正文里剩下的「报告期」**只在缺口文本里**出现：那是 `period_unresolved` 那两条"
             "缺口**自己**的说明文字（「不得用『报告期』这类未定义指代替代」），"
             "不是正文在用它顶替权威期间")
    gap_lines = [line for line in body.splitlines() if line.startswith("- [")]
    check(all("报告期" not in line or line.startswith("- [period_unresolved/")
              for line in gap_lines),
          "逐行核对：含「报告期」的缺口行全部是 `period_unresolved` 那两条"
          "（其余缺口行一个「报告期」都没有）")
    check_eq(sorted({str(getattr(u, "reason_code", "")) for u in result.unresolved}),
             ["blocked", "period_unresolved", "unresolved"],
             "缺口面按权威状态原样保留：`blocked` / `unresolved` / `period_unresolved` 三类都在")
    litigation_gaps = [u for u in result.unresolved
                       if "litigation" in str(getattr(u, "question_id", ""))]
    history_gaps = [u for u in result.unresolved
                    if "identity_history" in str(getattr(u, "question_id", ""))]
    check(bool(litigation_gaps) and bool(history_gaps),
          f"那两条被撤单元所指向的两组主题仍然**各自带缺口**（重大诉讼/仲裁 {len(litigation_gaps)} 条、"
          f"股权与主体沿革 {len(history_gaps)} 条）——撤下的是「越权用 context 承载否定」，"
          "必要的 Contract 事实因此仍缺，缺口照立，没有拿被拒记录去顶替缺口")
    check_eq(result.task_id, R9_TASK_ID, "结果挂回重建的那份 r9 任务")
    check_eq(len(result.unresolved), len(oc.unresolved),
             "结果上的缺口与组合根读数逐条相同（同一批对象，没有在落结果时被裁剪）")

    # (i) 补件去向：三批各 20 条，都只是**待裁决提议**，一条都没自动执行。
    check_eq(tuple(int(r.follow_up_count) for r in records), RECORDED_REJECTED_FOLLOW_UPS,
             "被拒两轮各自提出的补件诉求条数就是现场读数（20 + 20），随 typed 记录留档")
    needs = tuple(oc.follow_up_needs)
    check_eq(len(needs), RECORDED_REJECTED_FOLLOW_UPS[-1],
             "成稿轮那 20 条补件诉求成为 `FollowUpNeed`——不是随异常蒸发，也不是被自动执行")
    check_eq(len(tuple(oc.follow_up_rejections)), 0,
             "没有连 Contract 校验都没过的原始诉求（为空是**有内容的空**，不是「模型没提」）")
    check_eq({str(n.section_draft_revision) for n in needs}, {str(last["revision"])},
             "20 条诉求**逐条**挂在提出它的那一轮修订上（去向可回查，不与别的修订混）")
    check_eq({str(n.section_id) for n in needs}, {"company"},
             "20 条诉求的栏目都是本节，没有一条被悄悄改投别的栏目")
    check_eq(tuple(int(r["calls"]) for r in oc.batch_audit["rounds"]), RECORDED_ROUND_CALLS,
             "三轮的模型调用是 4 / 0 / 0：两轮裁出与一次成稿，**都没有**新增任何模型调用"
             "（补件诉求没有被拿去自动重问，`cco-6` 也不调模型）")
    check_eq(int(oc.llm_calls), sum(RECORDED_ROUND_CALLS),
             "整节的模型调用总数仍等于 r9 留存的那 4 份真实返回——"
             "本重放没有多发一次请求，也没有把补件诉求执行掉")

    # (j) 正文的来源要与「真实写作」分开标识：这 3 段是**确定性组织器替身**拼的。
    client = out["client"]
    stub_calls = [c for c in client.calls if c["kind"] == "organizer_stub"]
    check_eq(len(stub_calls), 1,
             "`SectionResult` 的正文来自验收 harness 的**确定性组织器替身**"
             "（`ACC.OfflineNarrationClient()._organizer`，一次 `organizer_stub` 调用）："
             "r9 那一轮公司节从未走到组织器（本节 4 份返回之后没有任何公司侧组织调用），"
             "因此**不存在**可用的留存组织返回")
    check_eq(sorted({c["kind"] for c in client.calls}),
             ["organizer_stub"] + [f"recorded_call_{n}" for n, *_r in R9_COMPANY_CALLS],
             "整节只发生了「4 份 r9 留存返回 + 1 次替身组织」：没有别的模型调用混进来")
    paragraphs = [line for line in body.splitlines()
                  if line and not line.startswith(("#", "**", "- ["))]
    details.append("")
    details.append(f"  * **仅证明链路**：下面这 {len(paragraphs)} 段正文由确定性组织器替身拼装，"
                   "不是人工可接受的真实模型正文——它证明的是「这批字节能走通到 "
                   "`SectionResult`」，不是「这一节写得好」：")
    for index, paragraph in enumerate(paragraphs, start=1):
        details.append(f"    {index}. {paragraph}")
    check(bool(paragraphs) and all(p in body for p in paragraphs),
          "正文确实成形（段数、内容都对得上），但它的身份是替身输出——报告里必须这样标识")

    faces = _material_faces(case)
    details.append("")
    details.append(f"成稿轮那一束（真正走到 `SectionResult` 的一束，修订 `{last['revision']}`）"
                   "里两组共享候选——它们在**三**束里都成形，一条都没被任何一道裁出连坐：")
    for text in (g["candidate_text"] for g in R9_SHARED_GROUPS):
        candidate_id = last["text_to_id"][text]
        units = last["declared"][candidate_id]
        details.append("")
        details.append(f"### `{candidate_id}`：{text}")
        details.append("")
        details.append("| 支撑边 | 材料 | 文档 | 文档版本 | 来源角色 | 路径 |")
        details.append("|---|---|---|---|---|---|")
        for edge in last["edges"].get(candidate_id) or ():
            material_id = edge["material_id"]
            axes = faces["axes"].get(material_id, ("", ""))
            details.append(f"| `{edge['container_id'][:8]}…` | `{material_id}` | {axes[0]} | "
                           f"{axes[1]} | {faces['roles'].get(material_id, '')} | "
                           f"{edge['authorization_path']} |")
        documents = {faces["axes"].get(e["material_id"], ("", ""))[0]
                     for e in (last["edges"].get(candidate_id) or ())}
        roles = {faces["roles"].get(e["material_id"], "") for e in last["edges"].get(candidate_id) or ()}
        check_eq(len(last["edges"].get(candidate_id) or ()), 2,
                 f"`{candidate_id}` 有 2 条支撑边（两条边就是「同文、两份年报」的两处出处）")
        check_eq(len(documents), 2,
                 f"`{candidate_id}` 的两条边落在**两份不同文档**上（{sorted(documents)}）")
        check_eq(len(roles - {""}), 2,
                 f"`{candidate_id}` 的两条边是**两种不同来源角色**（{sorted(roles)}）")
        details.append("")
        details.append("| 草稿段身份 | 段内原子 | 段内**独有**原子（身份 + 文本） | 该段正文 |")
        details.append("|---|---|---|---|")
        for unit_id in units:
            atoms = [cid for cid, ups in last["declared"].items() if unit_id in ups]
            unique = [cid for cid in atoms if len(last["declared"][cid]) == 1]
            rendered = "、".join(f"`{cid}`（{last['text_of'][cid]}）" for cid in unique)
            details.append(f"| `{unit_id}` | {'、'.join(f'`{c}`' for c in atoms)} | "
                           f"{rendered or '—'} | {last['unit_text'][unit_id]} |")
        check(all(len(last["declared"][cid]) == 1
                  for unit_id in units for cid in last["declared"]
                  if cid != candidate_id and unit_id in last["declared"][cid]),
              f"`{candidate_id}` 之外，这两段里的每一条原子都只被**一段**声明"
              "（多 occurrence 的只有共享候选本身）")
    details.append("")
    details.append("  * 这说明：候选按**原子命题**合并成了一条，而草稿段按**出处**各自成段——"
                   "两段文字几乎相同、各带自己独有的原子，于是同一候选被两段声明。"
                   "B 段之前，这条形状在组装 `SectionDraft` 时被判死（「原子归属必须唯一」）；"
                   "B 段之后，每一处 occurrence 各自核对了候选人自己的支撑边（容器 / 材料 ID / "
                   "文档版本 / 来源角色），两处都对得上 ⇒ 合法。**读者面重复**由门后的组织器"
                   "按已过门的 Claim 选择合格表达，不由这里删段或截字来消除。"
                   "这一次本节走到了 `SectionResult`：拦住它的是那两段 context 衔接单元里的"
                   "「报告期」（含糊期间，§十二 4 blocking）——那是一条**正确**的 fail-closed，"
                   "不是 B 段要修的归属问题；`cco-6` 按单元**逐项**撤下那两条之后才放行，"
                   "而正文本身仍由确定性组织器替身拼装（见上「仅证明链路」）")


def _check_final_sentence(check, check_eq, face: dict, details: list[str]) -> None:
    details.append("")
    details.append("## §3 财务最终句 B：r9 那一次调用的请求面与返回面（只读，零调用）")
    record = face["record"]
    check_eq(str(record["prompt_version"]), R9_FIN_PROMPT_VERSION,
             "这一次调用的 prompt 版本逐字等于 r9 记录值")
    check_eq(str(record["model"]), R9_RECORDED_MODEL, "调用模型逐字等于 r9 记录值")
    sentence = face["sentence"]
    check_eq(str(sentence["sentence_id"]), R9_FIN_SENTENCE_ID, "句子身份逐字相同")
    check_eq(str(sentence["sentence_kind"]), "composed",
             "该句是 `composed`（连接语由系统渲染，`natfid-1` 只比字面）")
    check_eq(str(sentence["text"]), R9_FIN_SENTENCE_TEXT, "句子正文逐字相同")
    check_eq(tuple(sentence["claim_ids"]), (R9_FIN_CLAIM_ID,), "该句声明的 Claim 逐条相同")
    check(R9_FIN_BINDING_ID in tuple(face["payload"]["claims"][0]["accepted_binding_ids"]),
          f"该 Claim 的 factual 支撑边 `{R9_FIN_BINDING_ID}` 在请求面里")
    check_eq(face["located"], R9_FIN_LOCATED_SURFACES,
             "机械定位出的三条表面逐字相同（`2025年` / `2024年` / `-3.3个百分点`）")
    check_eq(face["located"], face["payload_located"],
             "生产定位器的读数与 r9 请求面里 `located_atoms` 逐条相同（同一个定位器）")
    check_eq(face["model_atoms"], R9_FIN_MODEL_ATOMS,
             "模型返回的三条原子逐字相同：期间被**合成**成一条 `2025年末较2024年末`")
    check_eq(set(face["verdicts"]), {"entailed"},
             "模型对**它自己**给出的三条原子都判 `entailed`（缺的不是语义判决，是覆盖面）")
    missing = tuple(s for s in R9_FIN_LOCATED_SURFACES if s not in face["model_atoms"])
    check_eq(missing, ("2025年", "2024年"),
             "两条年份表面**无人认领**——它们在句子里逐字出现，却没有一条返回原子对应它们")
    interpreted = face["interpreted"]
    check_eq(interpreted["ok"], False,
             "生产解释器在**这份返回**上不能形成决定（已定位表面未被逐条认领即 fail-closed）")
    if not interpreted["ok"]:
        details.append(f"  * 解释器读数：{interpreted['error']}")
        check("未被输出认领" in str(interpreted["error"]),
              "失败原因就是「已定位的表面未被逐条认领」，不是别的判据")
    check_eq(_decision_rows("financial"), 0,
             "r9 产物里财务节的最终句决定读回行数为 **0**：决定**没有落库**（本门未能形成决定）")
    check_eq(_decision_rows("industry"), 0,
             "行业节的最终句决定读回行数同样为 0（本轮没有承载事实的最终句）")
    details.append("  * 两侧钉的是同一件事：请求面里三条表面被机械定位、返回面只认领了一条 ——"
                   " 于是本门没有决定，产物里那一行因此读回 0")


#: r9 现场「**只靠兜底重切**立起来」的 aspect（逐字取自 r9 留存字节里 `mar-1` 诊断的
#: `aspects_only_on_fallback_recut`）。这是 `fba-1` 要挡的那一类**栏目归属**在真实产物上的
#: 样子：这些 aspect 的导航读集**一个 node 都没读**（`nav_read_node_total == 0`、
#: `nav_selected_events == 0`、`nav_fallback_events >= 1`），栏目材料全部来自有界 Evidence
#: 重切。名字逐条来自记录，不按主题/页码挑；它是**身份清单**，不是"答案"。
R9_FALLBACK_ONLY_ASPECTS = (
    "company_business_main.app_scenarios",
    "company_business_main.cost_gross_margin",
    "company_business_main.industry_chain_position",
    "company_customer_concentration.customer_anonymity",
    "company_customer_concentration.customer_concentration_change",
    "company_customer_concentration.customer_current_concentration",
    "company_identity_basic.founded_date",
)


def _check_material_attribution_face(check, check_eq, details: list[str]) -> None:
    """§4 r9 的逐栏目归属面（只读留存字节，零调用）。

    两层读数都在 r9 产物里，本模块只**读**不改：

    - `mar-1` 只读诊断**已经把两种归属分开**了（`aspect_tree_read_set` 对
      `evidence_fallback_recut`）——分层没有缺，缺的是**运行侧**据此不把兜底材料登记成
      栏目材料，那条由 `fba-1` 补（见 `evals/test_demo_topic_runtime.py` §G 的正反例）；
    - 本节的判据因此钉在"r9 记录里有多少 aspect 只靠兜底立起来、它们的读集读数为 0"，
      供后续用 r9 字节重走同一生产链时对照**改动前后**的逐栏目差。
    """
    details.append("")
    details.append("## §4 r9 逐栏目归属面：哪些栏目只靠兜底重切立起来（只读，零调用）")
    report = _read_json(R9 / "acceptance_report.json")
    face = (report.get("observations", {})
            .get("company_material_source_reconciliation", {})
            .get("material_attribution") or {})
    check_eq(str(face.get("schema_version")), "material-attribution/1",
             "归属诊断的 schema 版本逐字为 r9 记录值")
    check_eq(str(face.get("rule_version")), "mar-1",
             "归属诊断的规则版本逐字为 r9 记录值")
    check_eq(tuple(face.get("aspects_only_on_fallback_recut") or ()),
             R9_FALLBACK_ONLY_ASPECTS,
             f"r9 里只靠兜底重切立起来的 aspect 共 {len(R9_FALLBACK_ONLY_ASPECTS)} 个，"
             "逐条等于记录值")
    by_aspect = {str(r.get("aspect_id")): r for r in (face.get("by_aspect") or ())}
    for aspect_id in R9_FALLBACK_ONLY_ASPECTS:
        row = by_aspect.get(aspect_id) or {}
        check(row.get("only_fallback_recut") is True
              and int(row.get("nav_read_node_total") or 0) == 0
              and int(row.get("nav_selected_events") or 0) == 0
              and int(row.get("nav_fallback_events") or 0) >= 1,
              f"r9 记录：{aspect_id} 的导航读集为空（读 0 个 node、无一次 selected、"
              "有 fallback 终态）——兜底重切才补的位")
        check(bool(row.get("material_ids"))
              and set(row.get("by_basis") or {}) == {"evidence_fallback_recut"},
              f"r9 记录：{aspect_id} 的 {len(row.get('material_ids') or ())} 份栏目材料"
              "逐条 basis=evidence_fallback_recut（留存字节不改，新规则只对新的 run 生效）")
    tree_only = [str(r.get("aspect_id")) for r in (face.get("by_aspect") or ())
                 if not r.get("only_fallback_recut")
                 and set(r.get("by_basis") or {}) == {"aspect_tree_read_set"}]
    check(bool(tree_only) and not set(tree_only) & set(R9_FALLBACK_ONLY_ASPECTS),
          "对照面非空且不相交：r9 里确有材料全部来自标题树读集的 aspect，"
          "它们与只靠兜底立起来的那批是两拨")
    details.append(f"  * r9 只靠兜底立起来 {len(R9_FALLBACK_ONLY_ASPECTS)} 个 aspect / "
                   f"全树读集立起来 {len(tree_only)} 个；"
                   f"`unattributed` {len(face.get('unattributed_refs') or ())} 条、"
                   f"`recut_materials_not_attributed` "
                   f"{len(face.get('recut_materials_not_attributed') or ())} 条"
                   "（mar-1 解释得动每一条引用）")


def _run_checks(check, check_eq, details: list[str]) -> None:
    ok, missing = _anchors_present()
    check(ok, f"r9 现场台账齐全（缺：{missing}）")
    if not ok:
        return

    _check_baseline(check, check_eq, details)

    snapshot = R7._patch_fixture_identity()
    try:
        # 两组依赖身份（与另两个离线重放同一份声明）：历史（记录，**不可执行**）与当前
        # （本次实际执行的代码）分开摆出来，不得合并成一句「r9/r7b 身份」。夹具已贴好，
        # 因此这里能一并核对「贴上去的是哪一组」。
        R7._dependency_identity_checks(check, check_eq)
        R7.declare_dependency_identity(details)
        case = _build_company_case()
        check_eq(dict(case["anchors"]),
                 {"content_fingerprint": R9_MATERIAL_COUNT, "locator_ref": R9_MATERIAL_COUNT,
                  "source_identity": R9_MATERIAL_COUNT, "payload_hash": R9_MATERIAL_COUNT},
                 f"{R9_MATERIAL_COUNT} 条材料的四项身份面逐条与 r9 记录相等"
                 "（内容指纹/locator/来源/载荷哈希）")
        check_eq(len(case["entries"]), R9_MATERIAL_COUNT,
                 f"r9 公司节 Pack 材料 {R9_MATERIAL_COUNT} 条")
        check_eq(len(case["specs"]), R9_COMPANY_ASPECT_COUNT,
                 f"Contract 投影面 {R9_COMPANY_ASPECT_COUNT} 个 aspect 重建成功")
        check_eq(case["task"].task_id, R9_TASK_ID, "重建任务的任务 id 等于 r9 记录值")
        check_eq(str(case["authority"].report_as_of), R9_REPORT_AS_OF,
                 "重建权威的 `report_as_of` 等于 r9 记录值")
        roles = SRS.document_roles(case["authority"])
        manifest = _read_json(R9_SOURCE_MANIFEST)
        recorded_roles = {str(s["document_id"]): str(s["source_role"])
                          for s in manifest["selection"]}
        check_eq(roles, recorded_roles,
                 "生产角色台账（`SRS.document_roles`）与 r9 来源清单逐文档相同"
                 "（夹具没有另判角色）")
        details.append(f"  * 本节来源角色：{roles}")
        # 合并面与门前束都用**生产**派生（同一 `scan` / 同一 manifest / 同一别名表 /
        # 同一事实表），本节四批的 `authority_facts` 都是 0 条，因此事实表只可能为空——
        # 传真的那一次也走空表，于是「现场读数」不是本模块自己造的口径。
        face = _merge_face(case)
        _check_merge_face(check, check_eq, face, details)
        check_eq(len(face["manifest"].entries), R9_MATERIAL_COUNT,
                 f"生产 manifest 成员 {R9_MATERIAL_COUNT} 条（与 Pack 材料逐条对应）")
        _check_reproduction(check, check_eq, case, _recorded_responses(), face, details)
    finally:
        R7._restore_fixture_identity(snapshot)

    _check_final_sentence(check, check_eq, _final_sentence_face(), details)

    _check_material_attribution_face(check, check_eq, details)


def main() -> dict:
    details: list[str] = []
    passed = failed = 0

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS  {msg}")
        else:
            failed += 1
            details.append(f"FAIL  {msg}")

    def check_eq(actual, expected, msg: str) -> None:
        check(actual == expected, f"{msg}（实得 {actual!r}，应为 {expected!r}）")

    ok, missing = _anchors_present()
    if not ok:
        return {"passed": 0, "failed": 0, "skipped": 1,
                "details": [f"SKIP r9 现场数据缺失，本模块不猜、不静默绿：{missing}"]}
    _run_checks(check, check_eq, details)
    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    result = main()
    for line in result["details"]:
        print(line)
    print(f"\npassed = {result['passed']}, failed = {result['failed']}, "
          f"skipped = {result['skipped']}")

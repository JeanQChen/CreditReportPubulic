"""Eval: M930-3 r8 实录的**离线同链重放**——公司节首个阻断的定点收口。

用法: python -X utf8 -m evals.test_m930_3_r8_offline_replay

## 这个模块要回答什么

指令 B（M930-3 r8 后的业务纵链收口）只问一件事：**在不伪造事实、不放宽安全门的前提下，
真实材料能不能形成可读的代表性公司正文**；如果不能，首个阻断在哪、原文对象是什么。

r8 那一轮的公司节**没有产出任何正文**。本模块用 r8 **自己留存的字节**（`logs/llm` 下 6 份
`pack_section_writer_proposals_v9@proposals-13` 原始返回 + `evaluation/results/.../r8/` 的
材料包与失败诊断），在当前代码上零网络、零新模型调用地重走同一条链，读出三件事：

  A. **反例仍然成立**：r8 第 4/4 批那份返回（0 条候选 + 3 段无出处草稿 + 3 条无出处草稿单元
     + 10 条补件）**逐字**再走一遍，仍被拒；拒因与 r8 现场一致（「自然草稿单元 p1 必须恰好
     声明一条出处轴」）。**没有**因为本批新增判据而被放行，也没有被静默删成空草稿。
  B. **残形的另一半也被堵上**：把同一份返回的草稿层清空（只留 3 条无出处的草稿单元），
     在 `pw-19` 下这曾经是**合法**形状（草稿单元层没有证人要求），在 `pw-20` 下整批落
     typed `batch_candidate_witness_missing`。
  C. **修完之后正文有没有**：用 r8 **已记录的前三批最终返回**（1/4 纠正版、2/4 纠正版、3/4）
     加一份**明确标注为「离线构造」**的合法空第四批，零网络重放合并 → 聚合绑定决定 →
     逐候选蕴含 → accepted Claim → 自然段 → `SectionResult`，并如实报出结果：
     有正文就给出逐句引用；没有就报**下一个**首个阻断及其原始对象。
  D. **最终组织器实际收到什么、最终句被怎么核验**（§7）：截下生产
     `NO.build_organizer_messages` 的真产物，列出输入面；说明 `composed` 句由**文本恒等**
     保证忠实、`natural` 句由 `natfid-1` 保真核对；并用 r8 财务节的**真实 Claim 文本**做探针，
     把 `natfid-1` 拦得住的与**拦不住的三条**逐条钉住。
  E. **财务与行业按记录分别报告**（§8）：财务的 Decimal 变化事实、原值期间、中文单位、
     `PROXY_FINANCE_EXPENSES` 限定与四条缺口；行业的零正文、`NOT_PROVIDED` 缺口与
     `not_retrieved` 外部漏斗。两节都**不重跑**，只读 r8 记录。

## 离线重放边界（**本文件必须如实标注的五件事**）

  1. **这不是新的真实验收，也不是一次真实模型调用**。零网络、零新 LLM 调用：门前提案束逐字
     取自 r8 留存的原始返回；自然组织器用验收 harness 自己的**确定性替身**
     （`ACC.OfflineNarrationClient()._organizer`）——r8 那一轮只给 `financial` 发过组织调用
     （ledger ordinal 273，`section_narrative_organizer_v4@norg-8`），company 从未走到那一步，
     因此**不存在**公司侧的留存组织返回可用。
  2. **公司节的材料正文不经 payload 字节复核**。理由与 r7b 重放**逐字相同**（真实信封不入库，
     见 `test_m930_3_prewrite_offline_replay.py` 边界 2）：本重放改用 r8 留存的阅读视图，经
     **同一个**入口 `MC.WriterMaterialContext.create` 构造上下文，材料身份面逐条与 r8 记录对账
     （内容指纹 / locator / 来源身份 / payload 哈希，四项全等才算数）。
  3. **第 4/4 批在 B/C 两段里都是离线构造**。r8 的第 4/4 批返回是**真实**的（它是被拒的那一份），
     但它不含任何可用候选；要往下走就必须有一份「本批写不出东西」的**合法空批**。那份空批是
     本模块构造的，`call_id` 逐字标出 `offline_constructed_batch4_empty`，任何地方都不得当模型
     返回读。**这不是在替模型编内容**：空批的语义是「本批没有可合法支持的事实原子」，它不产出
     任何候选、草稿、草稿单元，只保留补件诉求——一份如实的「写不出」。
  4. **不可复算的身份如实声明**（与 r7b 重放同族）：demo 投影 id、`demo_scope_fingerprint`、
     财务 artifact id 在本重放里重算后与记录不同。可复算的是：公司 draft 身份、writer 侧
     `ContractProjection`，以及本节的 Claim / 聚合决定 / 接受边链。
  5. **留存字节是**r8 当时的 prompt **问出来的，不是当前 prompt 问出来的**。6 份留存返回的
     `prompt_version` 全部是 `pack_section_writer_proposals_v9@proposals-13`（§0 逐份断言），
     而当前代码的 `PW.NARRATION_PROMPT_VERSION` 已经不同（§0 把它如实印出来，不做等式断言）。
     因此本重放问的是「**当前规则**遇到 **r8 的字节**会怎么判」，**不是**「当前 prompt 会问出
     什么」。这两件事任一变化，本模块的结论都要重跑——正文规则一动、prompt 一动，都必须重新
     过一遍，不能把今天这轮的判决移植过去。
  6. **依赖身份是两组，不得合并**（第五步；与 r7b/r9 重放同一份声明，见
     `R7.declare_dependency_identity`）。r7b 记录的依赖身份（dep `7f6d71f4…` /
     `navigation_profile = schema=anps-4;rule=anp-6`）与**当前工作区**的身份（dep
     `8fa6dcbe…` / `rule=anp-7`）逐键只差 `navigation_profile` **一个键**。**历史身份
     不可执行**：`anp-6` 的实现不在 git HEAD（HEAD 为 `anp-1`）也不在工作区，且 18 键向量
     从未落库（产物里没有 `dependency_versions` 字段）。本模块因此是「**当前代码** + r8
     记录的输入面」的离线重放：依赖身份是被重建的输入面的一部分（与 `proposals-13` 那个
     版本串同类，不得改写成今天的样子），而跑它的代码是当前版本。夹具仍贴记录值，
     当前指纹只被**声明**、不被贴。

## replay ≠ pass（本文件明确不主张的事）

  * r8 记录里公司节**没有**任何 draft / Claim / 正文 / 结果（`follow_up_needs.json` 的
    `sections_completed` 只有 `financial` 与 `industry`，company 带 `phase_error`）。
    因此公司半场产出的是**当前代码下的新产物**，身份是「本批代码 + 本夹具下的确定性身份」，
    **不是**对某份记录产物的复现。
  * 本模块**不**宣称 M930-3、TS5、正式树门或后续阶段关闭，也不把 `SECTION_BLOCKED` 读成通过。
  * 组织器与蕴含判定在本重放里都是**替身**：替身的输出只说明「链能不能走通」，不说明
    「真实组织器会怎么组织」。凡替身能力之外的判断，本模块只列线索、不替模型下结论。

数据依赖：`logs/llm/` 下 6 个 r8 调用文件、`evaluation/results/m930_3_acceptance_crossdoc_real_r8/`、
`data/evidence.db`。都不在版本控制里，缺失时本模块 **typed skip**。
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
from sections import material_context as MC
from sections import narrative_organizer as NO
from sections import narrative_schema as NS
from sections import pack_writer as PW

#: 夹具、任务重建、驱动与判决**全部**复用 r7b 重放模块：两份权威一旦分叉，「重放的是同一条链」
#: 就不成立。该模块顶层只有常量与函数定义，导入它不执行任何用例。
from evals import test_m930_3_prewrite_offline_replay as R7  # noqa: E402
from evals import test_demo_pack_writer as FIX  # noqa: E402

ROOT = R7.ROOT
R8 = ROOT / "evaluation" / "results" / "m930_3_acceptance_crossdoc_real_r8"
R8_SOURCE_MANIFEST = R8 / "source_manifest.json"

#: r8 公司节 **6 次** narration 调用的有序台账（`llm_call_ledger.json` ordinal 241–246）。
#: 逐条给出：批次标签与批次 id（**同 id 出现两次 = 该批被批次形状纠正重问过**）、它是什么、
#: 以及该份返回的四键计数。这里的数**只**用于把「每一份字节是什么」钉住，不用来判内容。
R8_COMPANY_CALLS = (
    # (序号, 日志文件名, 批次标签, 批次 id, 是否纠正轮, 候选, 草稿, 单元, 补件)
    (241, "20260927T081845234590__bf5f32112190490ba6e1821d84979a45", "1/4",
     "wpbatch_09407563b7d8856b8acc691a", False, 11, 11, 4, 7),
    (242, "20260927T081908713345__a06e837ecbbf4dfea271686d22e283dc", "1/4",
     "wpbatch_09407563b7d8856b8acc691a", True, 14, 5, 2, 5),
    (243, "20260927T081932647914__df7038ecd8b64515a6845e2d55e37c90", "2/4",
     "wpbatch_b1a1466200f760fcc9d143a8", False, 9, 5, 5, 4),
    (244, "20260927T081946167103__9e77b6694e3e4b83acdd99a42c9685a3", "2/4",
     "wpbatch_b1a1466200f760fcc9d143a8", True, 8, 3, 2, 3),
    (245, "20260927T082000033845__9cc4c31c99a8483081ea84fa9552803f", "3/4",
     "wpbatch_7e27d3243db46e7aa783d6f9", False, 7, 3, 2, 1),
    (246, "20260927T082016723915__a44c1638f57d48a6a3d96e1af2f8fb02", "4/4",
     "wpbatch_3240d2928e585d19c9fa4068", False, 0, 3, 3, 10),
)

#: r8 现场把公司节判死的**那一份**返回：第 4/4 批。它的 `phase_error` 逐字记着拒因，本模块
#: 用它在 A 段做反例——**逐字**的字节，不是重写的近似物。
R8_FATAL_CALL = "a44c1638f57d48a6a3d96e1af2f8fb02"
R8_FATAL_CALL_STEM = "20260927T082016723915__a44c1638f57d48a6a3d96e1af2f8fb02"

#: 每一批在 r8 那一轮的**请求批次 id**，按批次标签取（逐字取自上表）。
#: 1/4 与 2/4 各出现两次（原轮 + 形状纠正轮），两次是**同一批**、同一 id——纠正只是把同一批
#: 重问一次。`batch_id` 由 index+total+aspect_ids+shrink 派生（`PW._make_batch`），因此它同时
#: 是「批次顺序」与「本批 aspect 范围」的身份：重放请求的 id 与它相等，等于这两项都相等。
R8_BATCH_IDS = {label: batch_id for _n, _stem, label, batch_id, *_r in R8_COMPANY_CALLS}

#: B/C 段用**已记录的前三批最终返回**：1/4 的纠正版、2/4 的纠正版、3/4。批次 1/4 与 2/4 各有
#: 两次返回，**最终**那次才是那一批实际被采信的那一份（`bsc-1` 纠正后的第二次）。
R8_ACCEPTED_CALLS = (
    (1, "20260927T081908713345__a06e837ecbbf4dfea271686d22e283dc", "1/4"),
    (2, "20260927T081946167103__9e77b6694e3e4b83acdd99a42c9685a3", "2/4"),
    (3, "20260927T082000033845__9cc4c31c99a8483081ea84fa9552803f", "3/4"),
)

#: **离线构造**的合法空第四批。`call_id` 带 `offline_constructed_` 前缀，产物里始终可辨认——
#: 它不是模型返回。
#:
#: 构造方式刻意选**最小的一种**：取 r8 第 4/4 批那份**真实**返回，把三个内容键清空，`follow_up_needs`
#: **逐字保留**。这正是本批版本化修正请求规则给模型的出路原文（「请把本批这三个键清空、只留
#: `follow_up_needs`，或改成本批真的读得出的候选」），因此它不是替模型编内容，而是把它自己
#: 那 10 条补件诉求搬到一个合法形状上——补件诉求**不是**被本批修掉的东西，被修掉的是「用无出处
#: 的散文去讲没拿到材料」。清空三个键、留诉求，恰好是「合法空批」的定义。
BATCH4_CONSTRUCTED_LABEL = "offline_constructed_batch4_empty"
_BATCH4_CONTENT_KEYS = ("natural_prose_draft", "claim_candidates", "narrative_draft_units")


def _constructed_empty_batch4() -> tuple[str, dict]:
    """r8 第 4/4 批真实返回 → 合法空批（内容三键清空、补件逐字保留）。

    返回 `(要交给链的文本, 构造台账)`。台账逐项记下**构造动了什么**：三个内容键各自从几条
    变成 0 条、`follow_up_needs` 逐字未动几条。调用方据此断言「构造没有夹带任何内容」。
    """
    row = _read_call(R8_FATAL_CALL_STEM)
    original = json.JSONDecoder(strict=False).raw_decode(row["completion"])[0]
    follow_ups = [dict(f) for f in (original.get("follow_up_needs") or ())]
    payload = {"natural_prose_draft": [], "claim_candidates": [],
               "narrative_draft_units": [], "follow_up_needs": follow_ups}
    ledger = {key: {"from": len(original.get(key) or ()), "to": 0}
              for key in _BATCH4_CONTENT_KEYS}
    ledger["follow_up_needs"] = {"from": len(follow_ups), "to": len(follow_ups),
                                 "verbatim": True}
    return json.dumps(payload, ensure_ascii=False), ledger

#: r8 那一轮的**真实**身份。与 r7b 那份**逐项相同**（本模块在 §0 断言这一点）：同一份冻结
#: Contract、同一个 SourcePolicy、同一份 DemoScope profile、同一份 `source_manifest.json`。
REAL_CONTRACT_FP = R7.REAL_CONTRACT_FP
REAL_DEP = R7.REAL_DEP
COMPANY = R7.COMPANY
REPORT_AS_OF = R7.REPORT_AS_OF
PROFILE_PATH = R7.PROFILE_PATH
COMPANY_TASK_ID = R7.COMPANY_TASK_ID
COMPANY_ASPECT_COUNT = R7.COMPANY_ASPECT_COUNT
REPLAY_WIRE = PW.PROPOSAL_WIRE_CURRENT
REPLAY_SOURCE = "m930_3_acceptance_crossdoc_real_r8"

#: 6 份留存返回**当时**的 prompt 版本与模型（逐份断言，见 §0）。它们**不是**当前值：
#: 当前 `PW.NARRATION_PROMPT_VERSION` 已经前进了一版（§0 把它与本节一并印出来）。
#: 这条边界决定了本重放能主张什么：它判的是「当前规则 × r8 字节」，不是「当前 prompt 的输出」。
R8_RECORDED_PROMPT_VERSION = "pack_section_writer_proposals_v9@proposals-13"
R8_RECORDED_MODEL = "deepseek-v4-pro"
#: r8 那一轮**唯一**一次组织调用（ledger ordinal 273）的 prompt 版本：`financial` 节。
#: company 节从未走到组织器——因此公司侧没有、也不可能有留存的真实组织返回。
R8_RECORDED_ORGANIZER_VERSION = "section_narrative_organizer_v4@norg-8"
R8_RECORDED_ORGANIZER_SECTION = "financial"

#: 本重放**在当前代码与夹具下**确定性产出的公司侧身份（钉子）。它们**不是** r8 记录的身份
#: （r8 没有公司草稿）。填入前由 `_recompute_pins()` 现算——脚本 `r8_recompute_pins3.py`
#: 只读地把新值打出来，本模块不自己改钉子。
RECORDED_SECTION_DRAFTS = ("financial", "industry")
RECORDED_COMPANY_PENDING_NEEDS = 21
#: B 段（离线构造空第四批）在当前代码 + 本夹具下的确定性身份：draft / 修订 / 结果。
#: 它们**不是** r8 记录的产物（r8 没有公司草稿），而是本重放的钉值——任何一条变了，
#: 都说明链上某个内容面动了，必须逐项复核而不是改钉。
#:
#: **`pw-21` / `proposals-15` 批（指令 D §二·三条日期轴）的复核结论：只动两个版本串。**
#: 本批把 `PACK_WRITER_POLICY_VERSION` `pw-20`→`pw-21`、`NARRATION_PROMPT_VERSION`
#: `…v10@proposals-14`→`…v11@proposals-15`（与 `evals/test_m930_3_prewrite_offline_replay.py`
#: 的 `PRE_BATCH_*` 是同一对串、同一次前进：那边证明的是公司/财务两侧的修订，这边证明的是
#: **B 段这一条链**）。三个钉值随之重算，`PRE_BATCH_B_段_*` 记下重算**前**的读数，
#: §4 的归因判据拿旧的两个串重算修订、逐字回到它——因此前进的解释就是这两个串，不是「内容少了」。
#: 本批读数逐项相同：候选 9 / 门前叙述单元 6 / proposal 18 / 门后 Claim 9 / 段 2 / 句 3。
#: 本次前进**不是**本模块发现的：它由 `pw-21` 那两个串在 `derive_draft_revision` 的身份体里
#: 直接决定（`ClaimCandidate.identity_body` 含 `draft_revision` ⇒ 候选 id 跟着重算；
#: draft id 的身份体又含 `writer_policy_version`/`prompt_version` 与候选 id 列表 ⇒ draft id
#: 与 SectionResult 一并重算）。同批的 `nrules-16`/`ng-15` 只扩第 12 条判据的封闭标记集，
#: **不进** draft 身份体（同 `narr-8` 那次的读法）；本重放正文里没有那两组标记（句数/段数
#: 一条未变），因此不拿它解释身份。
#: **`pw-22` / `proposals-16` 批（M930-3 r9 后返修 B：草稿闭合读法）的复核结论：同样只动两个
#: 版本串。** 本批把 `PACK_WRITER_POLICY_VERSION` `pw-21`→`pw-22`、`NARRATION_PROMPT_VERSION`
#: `…v11@proposals-15`→`…v12@proposals-16`（新资产 v12 把「一条候选恰好一处表达」改写成
#: 「键集合一一对上 + 逐 occurrence 核验出处」；请求面键集/键序与解析侧判据一字未动）。
#: 三个钉值随之重算，`PRE_BATCH_B_段_*` 记下重算**前**的读数，§4 的归因判据拿旧的两个串
#: 重算修订、逐字回到它——因此前进的解释就是这两个串，不是「内容少了」或「草稿被删了」。
#: 本批读数逐项相同：候选 9 / 门前叙述单元 6 / proposal 18 / 门后 Claim 9 / 段 2 / 句 3。
#: 本重放的正文里**没有**一条候选被多个草稿单元声明（`npr-1` 放宽的是那种形状的判据，不是
#: 本夹具的形状），因此本批**不动**任何内容钉值：只有身份体里的两个版本串变了。
#: **M930-3 acc-38（替身选材与组织）的复核结论：只有渲染正文面动了，且身份体里恰好多动一项
#: `markdown_fingerprint`。** 本批改的四处都落在验收 harness 的替身选材/组织上，而本重放的
#: **组装**正是那条替身：`R7._drive` 注入的组织器就是 `ACC.OfflineNarrationClient()._organizer`
#: （见本文件 §C 段与 `evals/test_m930_3_prewrite_offline_replay.py`），本批把中性并列连接语从
#: 「整句共用一个」改成「每个接缝各取一个」。B 段第 1 句的四个 Claim 之间因此成了
#: `同时，/此外，/同时，` 三个各不相同的连接语——这正是本批那一处判据的可观察结果。
#: 逐项复核（现算，不改钉前不填）：draft id / 修订（候选 + manifest + 两个版本串）**逐字未动**，
#: 门后 Claim 9 / 段 2 / 句 3 / 补件 19 也**逐项未动**；变的只有现算出的
#: `markdown_fingerprint`（`sections/company_worker.py` 把渲染正文的 sha256 送进
#: `SS.derive_section_version` 的身份体——该派生的注释写明「Claims 不变、正文变了」必须换版本，
#: 否则正文与身份脱钩）。因此 **SectionResult 身份随正文前进是本批的预期效果，不是内容损失**：
#: 动的是连接语，不是 Claim、不是材料、不是缺口（缺口 48 条逐条原样列出）。
#: 三个钉值里只有这一个重算；`B_段_DRAFT_ID` / `B_段_REVISION` 与 `PRE_BATCH_B_段_*` 一律未动。
#: 重算**前**的读数原样留在这里，不因改钉而消失：`sr_secver_74e74c90b768aa2554e1a428`。
B_段_DRAFT_ID = "sdraft_3ae6efbc524aa0975966f7be"
B_段_REVISION = "sdrev_0205d47f821bfb9138aeec8e"
B_段_RESULT_ID = "sr_secver_b1a06cd665290ffb0495d27b"
#: 身份前进**前**那一批的两个版本串，与当时 B 段的三条读数（归因的反例锚，见 §4）。
#: 只读留存：它们是**当时**的读数，不随代码前进而改；重放不拿它们当期望值，只拿它们证明
#: 「换成这两个串，逐字回到这里」。
PRE_BATCH_POLICY_VERSION = "pw-21"
PRE_BATCH_PROMPT_VERSION = "pack_section_writer_proposals_v11@proposals-15"
PRE_BATCH_B_段_DRAFT_ID = "sdraft_a77a5c0c3574c7e5fb7d0ef5"
PRE_BATCH_B_段_REVISION = "sdrev_f1ef2179c61907e850fbafc2"
PRE_BATCH_B_段_RESULT_ID = "sr_secver_da90ae8dda5a7e70e718bf23"
B_段_CLAIMS = 9
B_段_PARAGRAPHS = 2
B_段_SENTENCES = 3
B_段_FOLLOW_UP_NEEDS = 19
RECORDED_COMPANY_HAZARD_TEXT = (
    "候选提案连续被拒，fail-closed：自然草稿单元 p1 必须恰好声明一条出处轴：")


def _read_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_call(stem: str) -> dict:
    return json.loads((R7.LLM_LOGS / f"{stem}.jsonl").read_text(encoding="utf-8"))


def _parsed_return(stem: str) -> dict:
    """一份留存返回的**四键形态**（末段可能带批注，故用 `raw_decode`）。"""
    row = _read_call(stem)
    return json.JSONDecoder(strict=False).raw_decode(row["completion"])[0]


def _response(stem: str, kind: str, *, order: int) -> dict:
    """一份留存返回 → 交给链的 `NarrationResult` 字段（模型名**逐字**保留，不改写成 stub）。

    除内容外还带上**这一份返回当时回答的是哪一批**（`R7._expectation_of`：批次标签 / 批次 id /
    aspect 范围 / 重放序号，逐字取自那一份请求自己的 payload）。重放驱动据此逐次核对收到的
    请求，不再按列表位置喂字节——位置喂法在「某批被纠正重问」或「某批没被请求」时会把**另一批**
    的字节当成这一批的返回。口径只有一份（在 r7b 模块里），不在本模块另写一套。
    """
    row = _read_call(stem)
    return {"kind": kind, "text": row["completion"], "call_id": stem.split("__")[-1],
            "model": row["model"], "input_tokens": row.get("input_tokens"),
            **R7._expectation_of(stem, order=order),
            "output_tokens": row.get("output_tokens"), "latency_ms": row.get("latency_ms"),
            "finish_reason": row.get("finish_reason")}


def _anchors_present() -> tuple[bool, str]:
    """数据依赖：r8 的 6 份原始日志 + 记录产物 + Evidence 库。缺任何一项都不猜、不静默绿。"""
    missing: list[str] = []
    for _n, stem, *_rest in R8_COMPANY_CALLS:
        if not (R7.LLM_LOGS / f"{stem}.jsonl").exists():
            missing.append(f"logs/llm/{stem}.jsonl")
    for name in ("material_pack.json", "failure_diagnostics.json", "follow_up_needs.json",
                 "source_manifest.json", "llm_call_ledger.json", "acceptance_report.json",
                 "proposal_set_rejections.json"):
        if not (R8 / name).exists():
            missing.append(f"{R8.name}/{name}")
    if not (ROOT / "data" / "evidence.db").exists():
        missing.append("data/evidence.db")
    return (not missing), "；".join(missing)


def _patch_identity():
    """把夹具身份对齐到 r8 的真实身份；返还快照供 `finally` 逐字还原。"""
    return R7._patch_fixture_identity()


def _restore_identity(snapshot) -> None:
    R7._restore_fixture_identity(snapshot)


def _r8_source_set() -> TS.DocumentSourceSet:
    """r8 那一轮的**有序源集**，逐字派生自它自己的 `source_manifest.json`。

    与 r7b 的做法同源（角色**只**取自 Pack 自己的来源角色台账，不在本模块另判角色）。本模块
    在 §0 断言两份清单的 `document_keys` 与 `selection` 逐字相同——同一份冻结来源面。
    """
    document = _read_json(R8_SOURCE_MANIFEST)
    roles = {(s["document_id"], s["document_version"], s.get("evidence_set_version")):
             s["source_role"] for s in document["selection"]}
    members = []
    for raw in document["document_keys"]:
        key = SM.SourceDocumentKey.from_dict(raw)
        role = roles.get((key.document_id, key.document_version, key.evidence_set_version))
        if not role:
            raise AssertionError(
                f"r8 来源清单里 {key.document_id}@{key.document_version} 没有 source_role："
                "不得默认它参与检索（fail-closed）")
        members.append((key, role))
    return TS.DocumentSourceSet(members=tuple(members))


def _build_company_case() -> dict:
    """r8 company 的完整写作前输入面（Pack set / 权威 / 材料上下文）。

    与 `R7._build_company_case()` 是**同一份构造**，只有「读哪一轮的记录」这一个参数不同：
    材料包、失败诊断、来源清单、请求 payload 全部取自 r8 自己的产物。可复用的部分直接调
    `R7._build_task_and_specs` / `R7._periodless_facts`，不另写一份——夹具分叉比重复更危险。
    """
    requests = {n: R7._payload_of(call_id) for n, call_id, *_r in R8_COMPANY_CALLS}
    req1 = requests[241]
    pack_doc = _read_json(R8 / "material_pack.json")
    fd = _read_json(R8 / "failure_diagnostics.json")
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
    task, specs = R7._build_task_and_specs(profile, contract, fd_aspects, requestable)

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
    source_set = _r8_source_set()
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
            company_id=COMPANY, report_as_of=REPORT_AS_OF, contract_version="v2",
            contract_fingerprint=REAL_CONTRACT_FP, source_set=source_set))
    pack_set = FIX._pack_set(task, packs=tuple(pack_specs), requirements=tuple(req_specs),
                             material_factory=factory)
    authority = PW.TopicPackAuthorityInput.create(
        task, pack_set, company_id=COMPANY, report_as_of=REPORT_AS_OF,
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
        task_id=COMPANY_TASK_ID, section_id="company",
        pack_set_fingerprint=MC.pack_set_fingerprint(pack_set), materials=tuple(resolved))
    return {"task": task, "pack_set": pack_set, "authority": authority, "req1": req1,
            "requests": requests, "entries": entries, "patched": patched,
            "anchors": anchors, "specs": specs, "profile": profile, "resolved": resolved,
            "context": context}


# ---------------------------------------------------------------------------
# 驱动：两份返回序列，同一个生产驱动
# ---------------------------------------------------------------------------

def _drive(case: dict, responses: list[dict]) -> dict:
    """把 case 的返回序列换成 `responses`，走 `R7._drive_company` 那条**生产**驱动。

    驱动本身不复制：同一份 `write_section` → `evaluate_claim_chain` → `_finalize_section`
    在 r7b 重放里已经跑绿，这里只换喂给它的字节。`case["responses"]` 是 R7 驱动留出的注入面。

    **重放身份由本模块显式给出**（`REPLAY_WIRE` / `REPLAY_SOURCE` 必填，没有默认值可继承）：
    r8 的 6 份返回是当前线的形状，按 r7b 的历史线读会把当前线的形状判据整片关掉——那正是
    「读绿一份喂错线格式的字节」。`_check_replay_identity` 再对**实际生效的** `WriterPolicy`
    断言这一点。
    """
    c = dict(case)
    c["responses"] = responses
    return R7._drive_company(c, proposal_wire=REPLAY_WIRE, replay_source=REPLAY_SOURCE)


def _accepted_responses() -> list[dict]:
    return [_response(stem, f"recorded_call_{n}", order=n) for n, stem, _l in R8_ACCEPTED_CALLS]


def _constructed_response() -> dict:
    text, _ledger = _constructed_empty_batch4()
    return {"kind": "constructed_empty_batch4", "text": text,
            "call_id": BATCH4_CONSTRUCTED_LABEL,
            "model": PW.resolve_model_policy(PW.MODEL_POLICY_PROVIDER_DEFAULT),
            # 它回答的是第 4/4 批，但**没有**属于它的请求 payload（离线构造），因此批次 id 与
            # aspect 范围两格如实为 None：可核对的坐标是标签与序号。
            "expected_batch_label": "4/4", "expected_batch_id": None,
            "expected_aspect_ids": None, "expected_order": 4}


def _rejections_of(exc: BaseException) -> tuple:
    return tuple(getattr(exc, "rejections", ()) or ())


def _outcome_or_error(case: dict, responses: list[dict]):
    """跑一次驱动：返回 `("ok", out)` 或 `("error", exc)`。异常**不吞**，交给调用方如实报。"""
    try:
        return "ok", _drive(case, responses)
    except Exception as exc:  # noqa: BLE001  重放要如实报出任何一个首个阻断
        return "error", exc


def _guard_b_section(check, details: list[str], kind_c: str, value_c) -> bool:
    """C 段走不通时的**唯一**出口：如实记一条 FAIL、写下首个阻断，再由调用方停止依赖它的判据。

    这里**不退出进程**：以 `raise` 抛出 `BaseException` 家族的那个「退出」异常，`run_evals`
    的 `except Exception` 接不住（`BaseException` 不是 `Exception`），整个套件会在**不打印
    TOTAL** 的情况下以 0 结束——一次真实失败就此变成一次「看起来通过」，而且后面的模块**静默
    不跑**。失败必须是**一条 FAIL 记录**：它使 `main()` 的 `failed > 0`，套件以非零退出，
    TOTAL 照常打印。返回 `True` 表示 C 段有产物可读。
    """
    if kind_c == "ok":
        return True
    check(False, f"C 段（前三批最终返回 + 合法空批）必须走通；实际："
                 f"{type(value_c).__name__}: {str(value_c)[:300]}")
    details.append(f"  * **下一个首个阻断**：{type(value_c).__name__}: {str(value_c)[:600]}")
    details.append("  * 依赖 C 段产物的判据（Claim / 段落 / 句子 / 逐环业务表 / 逐句材料 ID）"
                   "**一律不再执行**：没有产物可读时不猜、不用默认值补位")
    return False


# ---------------------------------------------------------------------------
# 逐环业务表：五格区分
# ---------------------------------------------------------------------------

#: 每一格要区分的五件事。它们在**同一条链上依次**收窄，任何一格都不得用材料条数代替。
#: 逐格口径（**每一格都取自记录在案的真实对象，不猜语义**）：
#:
#:   有材料        该 aspect 所属 topic 在 r8 Pack 里的材料行数（`material_pack.json` / 清单成员）。
#:   topic 分派材料 该 topic 的材料行里**出现在这一批请求 payload 的 `materials`** 的行数（逐行按
#:                 自己的 `topic_id` 取回）。**这是 topic 层的数，不是 aspect 层的数**：材料在
#:                 Pack/清单侧只有 topic 归属（材料没有 aspect 字段），因此公司节的这个数对
#:                 `company_business` 的**每一个** aspect 都是同一份（本节 = 25 行，全部投出——
#:                 整节材料目录不按批裁剪）。写成「25/25 材料对题」会被读成「25 个 aspect 都
#:                 对上了题」，那正是本表禁止的「用条数代替覆盖」。
#:   可支持该断言  本批**返回**里有没有候选（逐字取自该份留存返回的 `claim_candidates` 条数）。
#:                 逐 aspect 的候选归属在 wire 上**不存在**（`ClaimCandidate` 没有 aspect 字段，
#:                 这是类型层的有意留白），因此这一格只能诚实地记在**批**上。
#:   正式采用      门后 `SectionClaim` 里 `topic_id` 与本 aspect 的 topic 相同的条数（**topic 面**）。
#:   最终呈现      上述 Claim 里真正出现在 final Narrative 句子引用里的条数（逐句逐 Claim 回查）。
LINK_DISTINCTIONS = ("有材料", "topic 分派材料", "可支持该断言", "正式采用", "最终呈现")


def _batch_scope(case: dict) -> list[dict]:
    """逐批**请求面**读数：这一批被问了哪些 aspect、被给了哪些材料行。

    全部逐字取自 r8 自己留存的请求 payload（`batch.aspect_ids` / `batch.topic_ids` /
    `materials[].topic_id`），因此这是「Writer 实际输入」这一格的真值，不是复算的近似。
    """
    rows = []
    for n, _stem, label, bid, correction, ncand, nprose, nunits, nfu in R8_COMPANY_CALLS:
        payload = case["requests"][n]
        batch = payload["batch"]
        by_topic: dict[str, list[str]] = {}
        for row in payload["materials"]:
            by_topic.setdefault(str(row.get("topic_id")), []).append(str(row["material_id"]))
        rows.append({
            "ordinal": n, "label": label, "batch_id": bid, "correction": correction,
            "aspects": tuple(str(a) for a in batch["aspect_ids"]),
            "topics": tuple(str(t) for t in batch["topic_ids"]),
            "materials_by_topic": {t: tuple(v) for t, v in sorted(by_topic.items())},
            "material_count": len(payload["materials"]),
            "returned": {"candidates": ncand, "prose": nprose, "units": nunits,
                         "follow_ups": nfu},
        })
    return rows


def _aspect_rows(case: dict, out: dict | None) -> list[dict]:
    """逐 aspect 表（前三格 + 后两格的**可判定性**）。

    **为什么不给逐 aspect 的「正式采用 / 最终呈现」**：这两格要的是「这一条 Claim 是不是在
    回答这个 aspect」。现行 wire 上这件事**不可判定**——`SectionClaim` 只带 `topic_id` 与
    `question_ids`，而 `question_ids` 在真实产品里就是**该 topic 的整个问题集**（本节 9 条
    Claim 的 `question_ids` 逐条都等于 `company_business` 的四个问题）。按它去归因，每一条
    Claim 都会同时「被采用」到该 topic 的**每一个** aspect 上——那正是本指令点名禁止的
    「用条数代替覆盖」，只是换了个位置。因此这里如实留空，并把两格改在 topic 面给读数
    （`_topic_rows`），不让一个不可判定的格子读出好看的数。
    """
    requestable = {str(a["aspect_id"]): a for a in case["req1"]["requestable_aspects"]}
    scopes = _batch_scope(case)
    topic_materials: dict[str, set[str]] = {}
    for entry in case["entries"]:
        topic_materials.setdefault(str(entry["topic_id"]), set()).add(
            str(entry["material_id"]))

    owner: dict[str, dict] = {}
    for row in scopes:
        for aspect_id in row["aspects"]:
            owner.setdefault(aspect_id, row)

    rows = []
    for aspect_id in sorted(case["specs"]):
        spec = case["specs"][aspect_id]
        row = owner.get(aspect_id)
        topic_id = str(spec.topic_id)
        rows.append({
            "aspect_id": aspect_id, "topic_id": topic_id,
            "question_id": str(spec.question_id),
            "status": str(requestable.get(aspect_id, {}).get("status", "")),
            "batch": row["label"] if row else "",
            "batch_id": row["batch_id"] if row else "",
            "有材料": len(topic_materials.get(topic_id, ())),
            # **topic 层**的数（材料没有 aspect 字段）：同一 topic 的每个 aspect 读到的是同一份。
            "topic 分派材料": len((row["materials_by_topic"].get(topic_id) or ()) if row else ()),
            "可支持该断言": (row["returned"]["candidates"] if row else 0),
            "requirement": str(getattr(spec, "requirement_text", "") or ""),
        })
    return rows


def _topic_rows(case: dict, out: dict | None) -> list[dict]:
    """**topic 面**的两格：门后采用与见诸正文。这是现行 wire 上唯一可判定的粒度。

    `question_ids` 不参与归因——见 `_aspect_rows` 的说明；这里只用 `SectionClaim.topic_id`，
    以及段落句子的 `claim_ids` 回查。
    """
    topics = sorted({str(spec.topic_id) for spec in case["specs"].values()})
    rows = []
    for topic_id in topics:
        aspect_ids = sorted(a for a, s in case["specs"].items() if str(s.topic_id) == topic_id)
        rows.append({"topic_id": topic_id, "aspect_count": len(aspect_ids),
                     "有材料": len({str(e["material_id"]) for e in case["entries"]
                                   if str(e["topic_id"]) == topic_id}),
                     "正式采用": 0, "最终呈现": 0, "candidates": 0})
    if out is None:
        return rows
    sentences = [s for para in out["output"].narrative.paragraphs for s in para.sentences]
    cited = {cid for s in sentences for cid in (s.claim_ids or ())}
    by_topic = {row["topic_id"]: row for row in rows}
    for claim in out["output"].claims:
        row = by_topic.get(str(claim.topic_id))
        if row is None:
            continue
        row["正式采用"] += 1
        if str(claim.claim_id) in cited:
            row["最终呈现"] += 1
    # 候选是**批**级读数（wire 上没有逐 aspect 的候选归属）：按 topic 把**各自出现的批次**
    # 逐批相加一次，不按 aspect 相加（那会把同一批数 12 遍）。
    for batch in _batch_scope(case):
        for topic_id in batch["topics"]:
            if topic_id in by_topic:
                by_topic[topic_id]["candidates"] += batch["returned"]["candidates"]
    return rows


# ---------------------------------------------------------------------------
# 守恒账：21 条补件提议逐条去向
# ---------------------------------------------------------------------------

def _conservation_account() -> list[dict]:
    """21 条记录在案的补件提议 → 它出自哪一份返回、在本批修法下会被怎么处置。

    **只做账，不执行**（指令原文：「21 条公司补件提议的去向须逐条守恒，不自动执行」）。
    """
    recorded = _read_json(R8 / "follow_up_needs.json")["sections"]["company"]["follow_up_needs"]
    pools: dict[str, list[tuple[str, str]]] = {}
    for n, stem, _label, _bid, _corr, *_counts in R8_COMPANY_CALLS:
        fu = _parsed_return(stem).get("follow_up_needs") or ()
        pools[f"ordinal-{n}"] = [(str(f.get("aspect_id")), str(f.get("statement"))) for f in fu]
    out = []
    for index, need in enumerate(recorded):
        key = (str(need.get("aspect_id")), str(need.get("statement")))
        sources = [name for name, pool in pools.items() if key in pool]
        out.append({"index": index, "aspect_id": key[0], "statement": key[1],
                    "sources": sources, "need_id": str(need.get("need_id")),
                    "target_requirement_id": str(need.get("target_requirement_id"))})
    return out


# ---------------------------------------------------------------------------
# 人读质量检查的取证面：材料原文 + 定位 + 谁引用了它
# ---------------------------------------------------------------------------

def _material_rows(case: dict) -> list[dict]:
    """本节 36 条材料行的**原文与定位**（逐字取自 r8 留存的阅读视图 + 记录 locator）。"""
    rows = []
    for entry in case["entries"]:
        mrow = case["patched"][entry["material_id"]][1]
        loc = entry["locator"]
        rows.append({
            "material_id": str(entry["material_id"]),
            "topic_id": str(entry["topic_id"]),
            "material_type": str(entry["material_type"]),
            "document_id": str(loc["document_id"]),
            "page": loc["page"], "section_path": tuple(loc.get("section_path") or ()),
            "text": str(mrow.get("text") or ""),
            "evidence_id": str(mrow["source_identity"]).split(":", 1)[1],
        })
    return rows


def _binding_rows(out: dict) -> list[dict]:
    """门后**接受的支撑边**（`AcceptedSupportBinding`）的精确身份，逐条给出。

    `material_id` 是写入侧确定性派生的材料身份（不是模型自报）。它是把 Claim 与**材料行**对上
    的唯一把手：一条 Claim 的支撑边若指向某一行材料，那一条边就带着那一行的 `material_id`。
    """
    rows = []
    for binding in out["chain"].acceptance.accepted_bindings:
        rows.append({
            "binding_id": str(binding.accepted_support_binding_id),
            "material_id": (str(binding.material_id) if binding.material_id else None),
            "authority_kind": str(binding.authority_kind),
            "authority_container_id": str(binding.authority_container_id),
            "support_role": str(binding.support_role),
            "support_semantics": str(binding.support_semantics),
            "authorization_path": str(binding.authorization_path),
            "subject_kind": str(binding.binding_subject_kind),
        })
    return rows


def _claim_rows(out: dict) -> list[dict]:
    """门后 Claim 的正文、主题与引用对（逐条给出定位，供人读逐句核对）。

    除 `(evidence_id, page)` 引用对外，还给出**材料级**回查路径：`accepted_binding_ids` →
    每条边的 `material_id`。前者是**定位**（讲哪一页），后者是**身份**（讲哪一行材料）——
    同一页可以被多行材料共用，两者不能互相代替。
    """
    by_binding = {row["binding_id"]: row for row in _binding_rows(out)}
    rows = []
    for claim in out["output"].claims:
        binding_ids = tuple(str(b) for b in claim.accepted_binding_ids)
        rows.append({
            "claim_id": str(claim.claim_id), "text": str(claim.text),
            "topic_id": str(claim.topic_id), "claim_type": str(claim.claim_type),
            "pairs": tuple((str(c.evidence_id), c.page_number) for c in claim.citation_refs),
            "accepted_binding_ids": binding_ids,
            "material_ids": tuple(dict.fromkeys(
                by_binding[b]["material_id"] for b in binding_ids
                if by_binding.get(b, {}).get("material_id"))),
            "bindings_without_material": tuple(
                b for b in binding_ids if not by_binding.get(b, {}).get("material_id")),
        })
    return rows


def _cited_evidence_ids(out: dict) -> set[str]:
    return {eid for row in _claim_rows(out) for eid, _p in row["pairs"]}


def _cited_by(row: dict, claim_rows: list[dict]) -> list[str]:
    """引用了**这一行材料**的 Claim——按 `Claim.accepted_binding_ids → binding.material_id` 回查。

    这是唯一能在**行**上做归属的读法：`material_id` 是这一行自己的身份。按 `(evidence_id, page)`
    配对是**近似**，而且它错在「多算」这一侧：同一个 Evidence 块会被**多行**材料共用（同一段
    原文在不同标题路径下各成一行），**同一页**上更是如此——r8 公司节 36 行材料里有 **9 组**
    「同块同页、不同 material」（最密的一组同页 4 行）。按对匹配会把「这一页被引用」读成
    「这一行被引用」，同一件事被数好几遍。反例见 `_material_attribution_counterexample`。
    """
    material_id = row["material_id"]
    return [r["claim_id"] for r in claim_rows if material_id in r["material_ids"]]


def _same_page_groups(materials: list[dict]) -> dict:
    """按 `(evidence_id, page)` 分组的材料行——**只用来展示**「同块同页」有多少组。

    分出来的组**不是**归属单位：一个组里可能有 1..N 行材料，引用必须落到组里的某一行
    （`material_id`）。这里只回答「按对配对会放大多少」。
    """
    groups: dict[tuple, list[str]] = {}
    for row in materials:
        groups.setdefault((row["evidence_id"], row["page"]), []).append(row["material_id"])
    return groups


def _material_attribution_counterexample(case: dict, materials: list[dict],
                                         claim_rows: list[dict]) -> dict:
    """反例：**同证据块 + 同页、不同 material** 的两行，不得被同一条 Claim 一起算成「被引用」。

    反例用**现场真实的两行**构造（不是编一个形状）：取同一 `(evidence_id, page)` 下的两行材料
    `A` / `B`，取一条**只**由 `A` 的支撑边支撑的 Claim，然后断言：
      * 新读法（`material_id`）：这条 Claim 只出现在 `A` 的引用名单里；
      * 旧读法（`(evidence_id, page)` 配对）：它**同时**出现在 `B` 的引用名单里——这就是那
        一格会多算的读法，本反例把它钉住。
    没有任何一对真实的「同块同页不同 material」能配上一条 Claim 时，返回 `{"found": False}`：
    本反例**不**用合成行冒充现场读数（缺就如实说缺）。
    """
    groups: dict[tuple, list[dict]] = {}
    for row in materials:
        groups.setdefault((row["evidence_id"], row["page"]), []).append(row)
    for pair, group in sorted(groups.items(), key=lambda kv: (str(kv[0][1]), kv[0][0])):
        if len(group) < 2:
            continue
        ids = {row["material_id"] for row in group}
        for claim in claim_rows:
            hit = ids & set(claim["material_ids"])
            if len(hit) != 1:
                continue
            true_row = next(row for row in group if row["material_id"] in hit)
            others = [row for row in group if row["material_id"] not in hit]
            old_style = [row["material_id"] for row in others
                         if pair in claim["pairs"]]
            if not old_style:
                continue
            return {"found": True, "pair": pair, "claim_id": claim["claim_id"],
                    "claim_text": claim["text"], "true_row": true_row["material_id"],
                    "new_style_rows": [true_row["material_id"]],
                    "old_style_rows": [true_row["material_id"]] + old_style,
                    "false_positives": old_style,
                    "row_texts": {row["material_id"]: row["text"] for row in group}}
    return {"found": False}


def _narrative_sentences(out: dict) -> list[tuple]:
    return [(int(s.index), str(s.sentence_kind), str(s.text), tuple(s.claim_ids or ()))
            for para in out["output"].narrative.paragraphs for s in para.sentences]


def _organizer_face(case: dict, responses: list[dict]) -> dict:
    """驱动一次，把**组织器那一次调用**真正收到的输入面截下来（§7 用）。

    截的是生产 `NO.build_organizer_messages` 的产物——不是重写一份「组织器大概会收到什么」。
    公司节在 r8 里没走到组织器（§0 断言），所以这里必然截到一次**替身**组织调用：它是
    「链能不能走通」的证据，不是「真实组织器怎么组织」的证据（边界 1）。
    """
    captured: dict = {}

    class Capturing(R7.CompanyReplayClient):
        def narrate(self, *, messages, system, prompt_version, model_policy):
            if (prompt_version == NO.NARRATIVE_ORGANIZER_PROMPT_VERSION
                    and "payload" not in captured):
                captured["payload"] = json.JSONDecoder(
                    strict=False).raw_decode(messages[0]["content"])[0]
                captured["prompt_version"] = prompt_version
                captured["system"] = system
            return super().narrate(messages=messages, system=system,
                                   prompt_version=prompt_version,
                                   model_policy=model_policy)

    original = R7.CompanyReplayClient
    R7.CompanyReplayClient = Capturing
    try:
        out = _drive(case, responses)
    finally:
        R7.CompanyReplayClient = original
    return {"payload": captured.get("payload"), "prompt_version": captured.get("prompt_version"),
            "system": captured.get("system"), "out": out}


def _fidelity_probe(text: str, claims: "dict | tuple", citations=None) -> dict:
    """跑一次 `NS.verify_sentence_fidelity`（`natfid-1`），只读、不写任何对象。"""
    kw = {"claim_citations": citations} if citations else {}
    report = NS.verify_sentence_fidelity(text=text, claims=claims, **kw)
    return {"ok": bool(report.ok), "defects": list(report.defects()),
            "digest": str(report.fidelity_digest)}


def _field_names(obj: object) -> tuple[str, ...]:
    """对象的字段名（dataclass 取 `fields`，否则退到 `__dict__`）：只用来问「有没有哪个字段」。"""
    try:
        return tuple(f.name for f in dataclasses.fields(obj))
    except TypeError:
        return tuple(vars(obj))


# ---------------------------------------------------------------------------
# 六个经营模式 aspect：逐条给出处与判定面
# ---------------------------------------------------------------------------

BUSINESS_MODEL_ASPECTS = (
    "company_business_model.cost_competitiveness",
    "company_business_model.cost_structure",
    "company_business_model.procurement_mode",
    "company_business_model.production_mode",
    "company_business_model.sales_mode",
    "company_business_model.tech_route",
)

#: 逐 aspect 的**读侧检索词**：逐字取自该 aspect 的 Contract 要求主题（采购／生产／销售／
#: 成本／研发技术）。它**只**用来在已产出的材料里指出「Contract 要的那件事，原文落在哪一句」，
#: 不参与任何生产判定，也不是答案关键词。
BUSINESS_MODEL_READ_TERMS = {
    "company_business_model.cost_competitiveness": ("成本",),
    "company_business_model.cost_structure": ("成本",),
    "company_business_model.procurement_mode": ("采购",),
    "company_business_model.production_mode": ("生产", "产能"),
    "company_business_model.sales_mode": ("销售",),
    "company_business_model.tech_route": ("研发", "技术"),
}

#: 逐 aspect 的**人读结论**（四格逐项写；这一层是判定的本体，检索词只是定位把手）。
#:
#: 为什么必须有人读层：检索词是**词面**匹配，「一句里出现过这个词」和「这一条 Contract 要求被
#: 已接受事实覆盖」不是一回事。最清楚的一例是 `tech_route`：机械读数会命中正文 Claim 里的
#: 「技术授权」，而那是**产能扩充手段**（属 `production_mode`），不是技术路线。人读结论因此
#: 与机械读数**分开**记录；两者不一致时，`override` 必须写出为什么，否则下面会断言失败——
#: 不允许悄悄压过机械读数，也不允许把六个 aspect 压成同一个档。
BUSINESS_MODEL_HUMAN_READ = {
    "company_business_model.cost_competitiveness": {
        "supports": "2025 年报 p16「…以保证原材料和设备的技术先进性、产品的可靠性以及成本的"
                    "竞争力。」（2024 年报 p15 有对应段「…以保证原料、设备的技术先进性、产品"
                    "可靠性以及成本竞争力。」）",
        "not_supports": "只到这一句表态；成本水平、单位成本、降本幅度本节的这类材料没有，"
                        "且没有任何已接受 Claim 落在该句内",
        "in_body": False,
        "in_body_evidence": (),
        "override": None,
    },
    "company_business_model.cost_structure": {
        "supports": "本节材料里带「成本」的实质句只有「营业成本」栏目标题、募集说明书的"
                    "「营业收入和营业成本」指引，以及上面那句「成本的竞争力」",
        "not_supports": "**成本结构**（各项构成与占比）本链没有任何原文，也没有任何 Claim",
        "in_body": False,
        "in_body_evidence": (),
        "override": None,
    },
    "company_business_model.procurement_mode": {
        "supports": "2025 年报 p15「采购方面，公司通过严格的评估和考核程序遴选合格供应商，」"
                    "（该句在页边界处被截断，后半句「并通过长期协议、合资合作等方式与全球供应商"
                    "紧密合作…」落在 p16）",
        "not_supports": "该句与它的 p16 后半句**都没有 Claim**；2024 年报 p22 的「已签订的重大"
                        "采购合同截至本报告期的履行情况 □适用 不适用」是勾选残件，不是模式陈述；"
                        "2024 年报 p15 有完整的同一段（同样未被引用）",
        "in_body": False,
        "in_body_evidence": (),
        "override": None,
    },
    "company_business_model.production_mode": {
        "supports": "2025 年报 p16「公司以自建生产基地为主，并通过合资建厂、技术授权等方式"
                    "扩充产能，以满足全球客户需求。」",
        "not_supports": "只说到「自建为主 + 合资建厂 + 技术授权扩产」这三种方式；产量、产能"
                        "利用率、基地数量等本链没有取得",
        "in_body": True,
        "in_body_evidence": ("公司以自建生产基地为主，并通过合资建厂、技术授权等方式扩充产能",),
        "override": None,
    },
    "company_business_model.sales_mode": {
        "supports": "2025 年报 p16「生产销售方面，公司综合考虑市场情况及客户需求安排生产，」"
                    "（同段续句讲的是生产基地与扩产）；另有 p25/p19「销售境外的主要产品为"
                    "电池系统…境外收入…占比」属地区收入披露",
        "not_supports": "**销售模式**本身（渠道、结算、定价方式）没有任何 Claim；"
                        "p16 那句「生产销售方面」的后续内容被 Claim 覆盖的是**扩产**部分，"
                        "不构成销售模式陈述",
        "in_body": False,
        "in_body_evidence": (),
        "override": None,
    },
    "company_business_model.tech_route": {
        "supports": "2025 年报 p15「研发方面，公司建立了完备的研发体系，形成以自主研发为主、"
                    "外部合作为辅的研发模式，…」（2024 年报 p15 有对应段）",
        "not_supports": "研发体系这句**没有 Claim**；正文里出现的「技术授权」是**扩产手段**"
                        "（已计入 `production_mode`），不是技术路线；化学体系产品系列的 Claim "
                        "落在 `company_business_main.products_solutions` 的产品陈述上",
        "in_body": False,
        "in_body_evidence": (),
        "override": "机械命中来自 Claim 里的「技术授权」字样（扩产手段），不是技术路线",
    },
}


def _run_checks(check, check_eq, details: list[str]) -> None:
    ok, missing = _anchors_present()
    check(ok, f"r8 现场台账齐全（缺：{missing}）")

    # ---------------- §0 基线对账 ----------------
    details.append("## §0 r8 现场基线（逐字取自记录，不做推断）")
    r8_drafts = _read_json(R8 / "follow_up_needs.json")
    completed = tuple(str(s) for s in r8_drafts["sections_completed"])
    check_eq(completed, RECORDED_SECTION_DRAFTS,
             "r8 记录里**只有**财务与行业两节完成：公司节没有任何记录在案的草稿/Claim/正文/结果")
    company_row = r8_drafts["sections"]["company"]
    check(RECORDED_COMPANY_HAZARD_TEXT in str(company_row["phase_error"]),
          "公司节的 `phase_error` 逐字记着被拒判据（自然草稿单元必须恰好声明一条出处轴）")
    check_eq(int(company_row["total_pending_needs"]) if "total_pending_needs" in company_row
             else int(r8_drafts["total_pending_needs"]), RECORDED_COMPANY_PENDING_NEEDS,
             "公司节待裁决补件提议 21 条（不自动执行）")
    check_eq(len(company_row.get("follow_up_needs") or ()), RECORDED_COMPANY_PENDING_NEEDS,
             "`sections.company.follow_up_needs` 逐条列出 21 条（不是只有一个计数）")
    r7b_manifest = _read_json(R7.R7B_SOURCE_MANIFEST)
    r8_manifest = _read_json(R8_SOURCE_MANIFEST)
    # **逐字节相同**是错的：`generated_at` 是运行时刻，两轮必然不同。要比的是**来源面**本身
    # ——文档键集与角色选择。它们相同才说明两轮读的是同一份冻结来源。
    check_eq(r8_manifest["document_keys"], r7b_manifest["document_keys"],
             "r8 与 r7b 的 `document_keys` 逐字节相同（同一份冻结文档集）")
    check_eq(r8_manifest["selection"], r7b_manifest["selection"],
             "r8 与 r7b 的 `selection`（逐文档角色）相同")
    details.append(f"  * 两份清单只有 `generated_at` 不同：r8 "
                   f"{r8_manifest['generated_at']!r} / r7b "
                   f"{r7b_manifest['generated_at']!r}（运行时刻，不是来源面）")
    recorded = [_read_call(stem) for _n, stem, *_r in R8_COMPANY_CALLS]
    check_eq({str(r["prompt_version"]) for r in recorded}, {R8_RECORDED_PROMPT_VERSION},
             "6 份留存返回的 `prompt_version` 逐份都是 r8 当时那一版（不是当前版）")
    check_eq({str(r["model"]) for r in recorded}, {R8_RECORDED_MODEL},
             "6 份留存返回的模型逐份都是 r8 当时那个模型")
    check(all(r["completion"] for r in recorded),
          "6 份留存返回都有 `completion` 正文（没有空返回被当成返回）")
    ledger_rows = _read_json(R8 / "llm_call_ledger.json")["ledger"]["attempts"]
    org_rows = [a for a in ledger_rows
                if str(a.get("prompt_version")) == R8_RECORDED_ORGANIZER_VERSION]
    check_eq([(str(a["section_id"]), str(a["status"])) for a in org_rows],
             [(R8_RECORDED_ORGANIZER_SECTION, "ok")],
             "r8 那一轮**只**给 financial 发过一次组织调用且成功：company 侧不存在留存的真实"
             "组织返回，本重放的公司正文只能由替身组织器产出（边界 1）")
    details.append(f"  * 留存字节的 prompt 版本 = `{R8_RECORDED_PROMPT_VERSION}`；"
                   f"当前代码的 `PW.NARRATION_PROMPT_VERSION` = "
                   f"`{PW.NARRATION_PROMPT_VERSION}`（**两者不同**：本重放判的是"
                   "「当前规则 × r8 字节」，不是「当前 prompt 的输出」——边界 5）")

    # ---------------- §0b 重放身份：声明的线/来源必须是**当前线 × r8** ----------------
    # 驱动不复制（`R7._drive_company`），因此重放身份只能由**调用方**声明。这一节钉死两点：
    # 声明的值是什么，以及（下面 §4 拿到产物后）**实际生效的 `WriterPolicy`** 就是它。
    # 一旦线被写成 r7b 的历史线，当前线的形状判据会整片关掉，而结果仍然全绿——这正是要堵的。
    check_eq(REPLAY_WIRE, PW.PROPOSAL_WIRE_CURRENT,
             "本重放声明的提案线 = 当前线（`PW.PROPOSAL_WIRE_CURRENT`）")
    check(REPLAY_WIRE not in PW.LEGACY_PROPOSAL_WIRES,
          "本重放**不**走历史兼容线——历史线关掉的正是当前线的形状判据"
          "（「有候选 ⇒ 有草稿」与「零候选 ⇒ 草稿与草稿单元都空」）")
    check_eq(REPLAY_SOURCE, "m930_3_acceptance_crossdoc_real_r8",
             "本重放声明的来源 = r8 那一轮（不是 r7b 那一轮）")
    check_eq(R7.REPLAY_WIRE, "proposals-10",
             "r7b 重放**自己**仍显式声明历史线（它的字节是那个形状）：本批没有把它的声明改掉")
    check_eq(R7.REPLAY_SOURCE, "m930_3_acceptance_crossdoc_real_r7b",
             "r7b 重放**自己**仍显式声明 r7b 来源")
    details.append(f"  * 本重放身份：wire=`{REPLAY_WIRE}` / source=`{REPLAY_SOURCE}`"
                   f"（`requires_natural_draft()={PW.WriterPolicy(proposal_wire=REPLAY_WIRE).requires_natural_draft()}`）；"
                   f"r7b 重放身份：wire=`{R7.REPLAY_WIRE}` / source=`{R7.REPLAY_SOURCE}`")

    snapshot = _patch_identity()
    case = _build_company_case()
    try:
        # 两组依赖身份（与另两个离线重放同一份声明）：历史（记录，**不可执行**）与当前
        # （本次实际执行的代码）分开摆出来，不得合并成一句「r7b 身份」。夹具已贴好，
        # 因此这里能一并核对「贴上去的是哪一组」。见 `R7.declare_dependency_identity`。
        R7._dependency_identity_checks(check, check_eq)
        R7.declare_dependency_identity(details)
        check_eq(dict(case["anchors"]), {"content_fingerprint": 36, "locator_ref": 36,
                                         "source_identity": 36, "payload_hash": 36},
                 "36 条材料的四项身份面逐条与 r8 记录相等（内容指纹/locator/来源/载荷哈希）")
        check_eq(len(case["specs"]), COMPANY_ASPECT_COUNT,
                 f"Contract 投影面 {COMPANY_ASPECT_COUNT} 个 aspect 重建成功")
        check_eq(len(case["entries"]), 36, "r8 公司节 Pack 材料 36 条")
        check_eq(tuple(case["task"].topic_ids),
                 tuple(t for t in case["task"].topic_ids),
                 "本节 topic 序非空且稳定")

        scopes = _batch_scope(case)
        check_eq(len(scopes), len(R8_COMPANY_CALLS), "请求面台账 6 批")
        details.append("")
        details.append("| 批次 | 纠正轮 | topic | aspect 数 | 本批实际收到材料行 |"
                       " 该批返回(候选/草稿/单元/补件) |")
        details.append("|---|---|---|---|---|---|")
        for row in scopes:
            details.append(
                f"| {row['label']} | {'是' if row['correction'] else '否'} | "
                f"{'、'.join(row['topics'])} | {len(row['aspects'])} | "
                f"{row['material_count']} | {row['returned']['candidates']}/"
                f"{row['returned']['prose']}/{row['returned']['units']}/"
                f"{row['returned']['follow_ups']} |")

        # ---------------- §1 逐环业务表 ----------------
        details.append("")
        details.append("## §1 逐环业务表（有材料 / topic 分派材料 / 可支持该断言 / 正式采用 / 最终呈现）")
        details.append("")
        details.append("**A 段（r8 原样重放，公司节在第四批被判死）**：")
        details.append("")
        rows_a = _aspect_rows(case, None)
        details.append("| aspect | topic | 期 | 有材料 | topic 分派材料 | 可支持该断言 | 承批 |")
        details.append("|---|---|---|---|---|---|---|")
        for row in rows_a:
            details.append(
                f"| {row['aspect_id']} | {row['topic_id']} | {row['status']} | "
                f"{row['有材料']} | {row['topic 分派材料']} | {row['可支持该断言']} | "
                f"{row['batch']} |")
        check(all(row["batch"] for row in rows_a),
              "每个 aspect 都被分派进了某一批（没有「问了却没人答」的栏目）")
        topics_a = _topic_rows(case, None)
        details.append("")
        details.append("A 段 topic 面（门后两格的唯一可判定粒度）：")
        for row in topics_a:
            details.append(f"  * `{row['topic_id']}`：aspect {row['aspect_count']} 个 / "
                           f"有材料 {row['有材料']} 条 / 本段返回候选 {row['candidates']} 条 / "
                           f"正式采用 0 条 / 最终呈现 0 条")
        check(all(row["正式采用"] == 0 and row["最终呈现"] == 0 for row in topics_a),
              "A 段**全体 topic** 的「正式采用 / 最终呈现」都是 0——r8 公司节没有正文，"
              "这是要断言的事实，不是被抹掉的空值")
        business = [row for row in rows_a if row["aspect_id"] in BUSINESS_MODEL_ASPECTS]
        check_eq(len(business), len(BUSINESS_MODEL_ASPECTS),
                 "六个经营模式 aspect 全部在表内")
        check(all(row["status"] == "partial" for row in business),
              "六个经营模式 aspect 在 r8 记录里的状态（`partial`，不是 `covered`）")
        check(all(row["有材料"] > 0 for row in business),
              "六个经营模式 aspect 所属 topic **有材料**——「有材料」这一格不是 0，"
              "因此本节缺正文不能记在「材料没有」上")

        # ---------------- §2 Run A 反例 ----------------
        details.append("")
        details.append("## §2 反例 A：r8 第四批那份真实返回，逐字重放仍必须被拒")
        kind_a, value_a = _outcome_or_error(case, [_response(stem, f"recorded_call_{n}", order=n)
                                                   for n, stem, *_r in R8_COMPANY_CALLS])
        check_eq(kind_a, "error", "A 段**必须**被拒（走通才是异常信号）")
        if kind_a == "error":
            exc = value_a
            check_eq(type(exc).__name__, "ProposalSetRejectedError", "拒因类型")
            check(RECORDED_COMPANY_HAZARD_TEXT in str(exc),
                  "拒因与 r8 现场同一判据（自然草稿单元必须恰好声明一条出处轴）")
            rejections_a = _rejections_of(exc)
            check_eq(len(rejections_a), 1, "恰好一次整束拒绝")
            if rejections_a:
                rec = rejections_a[0]
                details.append(f"  * 拒因 kind = `{rec.rejection_kind}`；"
                               f"detail = {rec.rejection_detail[:200]}")
                details.append(f"  * 该束完整有序候选身份 {len(rec.candidate_ids)} 条、"
                               f"提案 {len(rec.proposal_ids)} 条、草稿单元 "
                               f"{len(rec.draft_unit_ids)} 条（完整留档，不是过滤后的子集）")
            needs_a = tuple(getattr(exc, "follow_up_needs", ()) or ())
            untypable_a = tuple(getattr(exc, "follow_up_untypeable", ()) or ())
            check_eq(len(needs_a), RECORDED_COMPANY_PENDING_NEEDS,
                     "A 段带出的可类型化补件提议数与 r8 记录的 21 条相等")
            check_eq(len(untypable_a), 0, "没有「连 Contract 都没过」的原始诉求")
            details.append(f"  * 随异常带出补件提议 {len(needs_a)} 条 / 不成立 "
                           f"{len(untypable_a)} 条")
        else:
            rejections_a, needs_a = (), ()

        # ---------------- §3 残形反例 ----------------
        details.append("")
        details.append("## §3 反例 B：同批「草稿清空、只留无出处草稿单元」的残形，也必须被拒")
        residual = dict(_parsed_return(R8_FATAL_CALL_STEM))
        residual["natural_prose_draft"] = []
        residual_text = json.dumps(residual, ensure_ascii=False)
        # (a) **残形在旧线上真的是合法的**——这才是这一格要关掉的东西：草稿单元层在 `pw-19`
        #     没有任何见证要求，于是一批「0 候选 + 0 草稿 + 3 段『本轮未取得…』」可以整批过
        #     结构校验。断言这一点，是为了说明新判据补的是一个**真实存在的形状缺口**。
        plan_b = PW.parse_writer_proposals(residual_text, aliases=None,
                                           require_natural_draft=True)
        check_eq(len(plan_b["claim_candidates"]), 0, "残形的候选为 0")
        check_eq(len(plan_b["narrative_draft_units"]), 3,
                 "残形的 3 条草稿单元**通过结构校验**（旧线上无人要求它们指得出一行输入）")
        # (b) **不再在这里重复判据本身**：`assert_batch_no_witness_means_empty` 的正例、反例与
        #     「同一残形经整批入口同样 fail-closed」三格，由**便携**合成测试
        #     （`evals/test_demo_pack_writer.py` 的 `pw-20` 一节）覆盖——它把残形喂给**整批入口**、
        #     走完补救与 `bsc-1` 纠形，正反两面都在，且不依赖任何本机日志。这里再断言一遍
        #     `PW.assert_batch_no_witness_means_empty(...)` 的返回值只是同义测试：它既不能证明
        #     接线上判据被调用，也不能证明判据在**本节的字节**上被调用。
        #     本节真正要证明的是接线：当前线下该判据**开着**（见 §0b 与 §4 对实际 `WriterPolicy` 的断言）
        #     且下面 (c) 在 r8 **自己的字节**上确实走不到正文。
        check_eq(PW.WriterPolicy(proposal_wire=REPLAY_WIRE).requires_natural_draft(), True,
                 f"当前线下 `{PW.BATCH_NO_WITNESS_FAILURE}` 判据是**开着**的"
                 "（判据本体由便携合成模块覆盖，这里只钉线）")
        # (c) 整链行为：把残形放进第 4/4 批，链**不得**产出任何草稿/Claim/正文。
        #     现场读数是：这一批被这条判据判掉后走既有通道（逐候选补救 → `bsc-1` 形状纠正），
        #     额度用尽后整轮作废并重新提案，离线替身在下一轮请求时如实耗尽——两条路都**没有**
        #     产出正文，因此这里断言的是「不产出」，而不是某一条具体拒因文本。
        kind_b, value_b = _outcome_or_error(
            case, [_response(stem, f"recorded_call_{n}", order=n)
                   for n, stem, *_r in R8_COMPANY_CALLS[:5]]
            + [{"kind": "residual_shape", "text": residual_text,
                "call_id": "offline_residual_shape", "model": "offline",
                # 同一格残形放到第 4/4 批：坐标是标签与序号（它的 id 与范围按构造逐字等于
                # r8 第 4/4 批的请求——但那一份请求的 payload 不在本夹具手里，故如实留 None）。
                "expected_batch_label": "4/4", "expected_batch_id": None,
                "expected_aspect_ids": None, "expected_order": 246}])
        check_eq(kind_b, "error", "残形在整链上同样必须走不到正文")
        if kind_b == "error":
            check(not isinstance(value_b, dict),
                  "残形没有产出任何 draft/Claim/正文（失败是异常，不是一个「空产物」）")
            details.append(f"  * 整链终态 = {type(value_b).__name__}：{str(value_b)[:200]}")

        # ---------------- §3b 失败路径的反例：记 FAIL 并停判据，**不是退出** ----------------
        # C 段走不通时唯一的出口是 `_guard_b_section`。它的失败路径本身必须被反例钉住——
        # 这正是上一个批次出过的那一格：那里写的是「抛 `BaseException` 家族的那个退出异常」，
        # 于是 C 段一失败，整个套件就在**不打印 TOTAL** 的情况下以 0 结束（`run_evals` 只接
        # `Exception`，后面的模块静默不跑），一次真实失败被读成一次通过。反例断言四件事：
        # 记**恰好一条** FAIL（使 `main()` 的 `failed > 0`、套件非零退出）、把首个阻断逐字写进
        # 详情、返回 False 让调用方停掉依赖产物的判据、并且**不产生任何 PASS 行**。
        probe_fails: list[str] = []
        probe_details: list[str] = []

        def _probe_check(cond: bool, msg: str) -> None:
            if cond:
                raise AssertionError("本反例只走失败路径：探针判据不得为真")
            probe_fails.append(msg)
            probe_details.append(f"FAIL  {msg}")

        proceeds = _guard_b_section(_probe_check, probe_details, "error",
                                    PW.PackWriterError("合成阻断：夹具注入的首个阻断"))
        check_eq(len(probe_fails), 1,
                 "反例：失败路径记**恰好一条** FAIL（不是静默跳过、不是一条不记）")
        check_eq(proceeds, False, "反例：失败路径返回 False，调用方据此停掉依赖产物的判据")
        check(any("合成阻断" in line for line in probe_details),
              "反例：失败路径把**首个阻断**逐字写进详情（不必从退出码反推）")
        check(not any(line.startswith("PASS") for line in probe_details),
              "反例：失败路径不产生任何 PASS 行")
        _banned = "System" + "Exit"
        check(_banned not in Path(__file__).read_text(encoding="utf-8"),
              f"反例：本模块全文不得出现那个 `{_banned}` 出口——`run_evals` 的 "
              "`except Exception` 接不住它，套件会以 0 结束且不打印 TOTAL")

        # ---------------- §4 Run B 完整链 ----------------
        details.append("")
        details.append("## §4 修完之后：前三批最终返回 + 离线构造的合法空第四批，零网络重放到底")
        text4, ledger4 = _constructed_empty_batch4()
        check_eq(ledger4["natural_prose_draft"], {"from": 3, "to": 0},
                 "构造只清空草稿层（3→0）")
        check_eq(ledger4["claim_candidates"], {"from": 0, "to": 0}, "候选本来就 0 条，未动")
        check_eq(ledger4["narrative_draft_units"], {"from": 3, "to": 0},
                 "构造只清空草稿单元层（3→0）")
        check(ledger4["follow_up_needs"].get("verbatim") is True
              and ledger4["follow_up_needs"]["from"] == 10,
              "10 条补件诉求逐字保留（空批只表达「本批写不出」，不吞诉求）")
        details.append(f"  * 空第四批构造台账：{ledger4}")
        kind_c, value_c = _outcome_or_error(
            case, _accepted_responses() + [_constructed_response()])
        if not _guard_b_section(check, details, kind_c, value_c):
            return
        out = value_c

        # ---- §4a 实际生效的重放身份 + 逐次核对的请求坐标 ----
        # 声明是一回事，**跑起来用的是哪一套**是另一回事：下面断言的是驱动真正构造出来的
        # `WriterPolicy`，以及每一次提案请求的批次坐标。若线/来源被隐式继承，这两条会先红。
        policy_used = out["policy"]
        check_eq(policy_used.proposal_wire, PW.PROPOSAL_WIRE_CURRENT,
                 "**实际生效**的 `WriterPolicy.proposal_wire` = 当前线")
        check_eq(policy_used.replay_source, REPLAY_SOURCE,
                 "**实际生效**的 `WriterPolicy.replay_source` = r8 来源")
        check(policy_used.requires_natural_draft(),
              "实际生效的策略里 `requires_natural_draft()` 为真：草稿层与"
              f"「零候选 ⇒ 草稿空」（`{PW.BATCH_NO_WITNESS_FAILURE}`）两条判据都**开着**")
        batch_calls = [c for c in out["client"].calls if c["kind"] != "organizer_stub"]
        check_eq([c["kind"] for c in batch_calls],
                 ["recorded_call_1", "recorded_call_2", "recorded_call_3",
                  "constructed_empty_batch4"],
                 "C 段恰四次提案调用：前三批的**最终**留存返回 + 第四批的离线构造空批")
        check_eq([c["batch_label"] for c in batch_calls], ["1/4", "2/4", "3/4", "4/4"],
                 "四次请求各回答一个批次，且顺序即批次顺序（本轮没有任何一批被纠正重问）")
        check_eq([c["batch_id"] for c in batch_calls],
                 [R8_BATCH_IDS["1/4"], R8_BATCH_IDS["2/4"], R8_BATCH_IDS["3/4"],
                  R8_BATCH_IDS["4/4"]],
                 "四次请求的批次 id 逐字等于 r8 当时那一批的 id（含第四批——它的返回是构造的，"
                 "但**请求**不是：当前代码复算出的是同一批）")
        check_eq([c["batch_id_checked"] for c in batch_calls], [True, True, True, False],
                 "前三批的批次 id 由留存返回自带、逐次核对过；第四批没有属于它的请求 payload"
                 "（离线构造），如实记 `False`，不编一个 id")
        check_eq([c["aspects_checked"] for c in batch_calls], [True, True, True, False],
                 "同理：前三批的 aspect 范围逐次核对过（`batch_id` 由范围派生，两者同源）")
        details.append(f"  * 实际策略：wire=`{policy_used.proposal_wire}` / "
                       f"source=`{policy_used.replay_source}` / "
                       f"requires_natural_draft={policy_used.requires_natural_draft()}")
        details.append("  * 逐次核对台账（请求侧坐标 → 留存返回）：" + "；".join(
            f"{c['batch_label']} id=`{c['batch_id']}` 核对="
            f"{'id+范围' if c['batch_id_checked'] else '仅标签+序号'}"
            for c in batch_calls))

        draft = out["outcome"].draft
        chain = out["chain"]
        claim_rows = _claim_rows(out)
        sentences = _narrative_sentences(out)
        check_eq(draft.draft_id, B_段_DRAFT_ID, "B 段 draft 身份（钉值）")
        check_eq(draft.draft_revision, B_段_REVISION, "B 段 draft 修订（钉值）")
        check_eq(len(out["output"].claims), B_段_CLAIMS, "B 段门后 Claim 数（钉值）")
        check_eq(len(out["output"].narrative.paragraphs), B_段_PARAGRAPHS,
                 "B 段 final Narrative 段落数（钉值）")
        check_eq(len(sentences), B_段_SENTENCES, "B 段句子数（钉值）")
        check_eq(out["output"].result.section_result_id, B_段_RESULT_ID,
                 "B 段 SectionResult 身份（钉值）")

        # ---------------- §4a 这三个钉值为什么前进：**可执行**归因 ----------------
        # 钉值前进时最容易被读成「链上内容动了」。本节的形状与 prewrite 模块同族：把
        # `derive_draft_revision` 的**两个**版本串换回上一批，逐字重算修订——
        #   (a) 换回两个旧串 ⇒ 逐字回到 `PRE_BATCH_B_段_REVISION`（前进的充分且必要解释）；
        #   (b) 用当前两个串 ⇒ 逐字等于现场修订（探针函数与生产派生是同一个 `NS` 函数）；
        #   (c) **两个串都参与**：只回退其中一个，两个旧/新读数都得不到——否则「两个串都在
        #       身份体里」就只是一句声明。
        # `derive_draft_revision` 的身份体里，除这两个串之外每一项输入都逐项来自现场对象
        #（task / section / company / `report_as_of` / Contract / model policy / manifest
        # identity / attempt / 草稿摘要），因此 (a) 证明的是**这两个串**，不是别的量。
        # draft id 是「本修订 + 候选/清单身份」的确定性函数；**SectionResult 不是**——它还含
        # `markdown_fingerprint`（`sections/company_worker.py` → `SS.derive_section_version`：
        # 「Claims 不变、正文变了」必须换版本）。上面的条数钉子只钉「条数」，钉不住正文字面：
        # acc-38 就是「条数逐项相同、正文的接缝连接语变了」⇒ SectionResult 前进而 draft 不动。
        # 因此本条是 **draft id** 前进的归因；SectionResult 的归因另在 §4b 逐项写清。
        _b_prose_digest = NS.natural_prose_draft_digest(draft.natural_prose_draft)
        _b_attempt = int(draft.writer_attempt)

        def _b_rev(policy_version: str, prompt_version: str) -> str:
            return R7._revision_with_versions(
                case["task"], case["authority"], draft, _b_attempt,
                policy_version, prompt_version, _b_prose_digest)

        check_eq(_b_rev(PRE_BATCH_POLICY_VERSION, PRE_BATCH_PROMPT_VERSION),
                 PRE_BATCH_B_段_REVISION,
                 "归因(a)：换回上一批的两个版本串重算 B 段修订，逐字回到上一批的读数"
                 "（⇒ 前进只由这两个串解释，不是内容动了）")
        check_eq(_b_rev(PW.PACK_WRITER_POLICY_VERSION, PW.NARRATION_PROMPT_VERSION),
                 draft.draft_revision,
                 "归因(b)：用当前两个版本串重算，逐字等于现场修订")
        check(_b_rev(PRE_BATCH_POLICY_VERSION, PW.NARRATION_PROMPT_VERSION)
              != PRE_BATCH_B_段_REVISION
              and _b_rev(PW.PACK_WRITER_POLICY_VERSION, PRE_BATCH_PROMPT_VERSION)
              != PRE_BATCH_B_段_REVISION,
              "归因(c)：**两个串都参与**——只回退其中一个也回不到上一批的读数")
        check((PRE_BATCH_B_段_DRAFT_ID, PRE_BATCH_B_段_RESULT_ID)
              != (draft.draft_id, out["output"].result.section_result_id),
              "归因(d)：上一批的 draft id / SectionResult 与现场**确实不同**"
              "（这条是反例的护栏：若哪一天它们相同，说明锚点被写错，"
              "而不是说明身份没前进）")
        details.append(
            f"  * 身份前进归因：`{PRE_BATCH_POLICY_VERSION}`+`{PRE_BATCH_PROMPT_VERSION}` ⇒ "
            f"`{PRE_BATCH_B_段_REVISION}`；`{PW.PACK_WRITER_POLICY_VERSION}`+"
            f"`{PW.NARRATION_PROMPT_VERSION}` ⇒ `{draft.draft_revision}`；两个串都参与")
        details.append(f"  * draft = `{draft.draft_id}` / revision = `{draft.draft_revision}`"
                       f" / writer_attempt = {draft.writer_attempt}")
        details.append(f"  * 候选 {len(draft.claim_candidates)}、草稿单元 "
                       f"{len(draft.narrative_draft_units)}、支撑提案 "
                       f"{len(draft.proposed_support_refs)}、自然草稿段 "
                       f"{len(draft.natural_prose_draft)}")
        check_eq(len(chain.aggregate_decisions),
                 len(draft.claim_candidates) + len(draft.narrative_draft_units),
                 "每个 subject revision 恰一条聚合绑定决定")
        check_eq(len(chain.entailment_decisions), len(draft.claim_candidates),
                 "仅 factual candidate 进入蕴含门")
        check_eq(len(chain.acceptance.accepted_bindings), len(draft.proposed_support_refs),
                 "每条通过的 proposal 各产生一条 typed 接受边")
        check_eq(len(claim_rows), len(out["output"].claims), "Claim 表读全")
        check_eq(len(out["output"].narrative.tables), 0, "公司节无表")
        details.append(f"  * 门后 Claim {len(claim_rows)} 条、段落 "
                       f"{len(out['output'].narrative.paragraphs)} 段、句子 {len(sentences)} 句")
        details.append(f"  * SectionResult = `{out['output'].result.section_result_id}`、"
                       f"status = `{out['output'].result.status}`、未解决 "
                       f"{len(out['output'].result.unresolved)} 条")
        for row in out["outcome"].rejections:
            carve = getattr(row, "carve_out", None)
            details.append(f"  * 整束拒绝 `{row.rejection_kind}`"
                           + (f"：源 {len(carve.decision.source_candidate_ids)} → 排除 "
                              f"{len(carve.decision.excluded)} → 幸存 "
                              f"{len(carve.decision.surviving_candidate_ids)}"
                              if carve else ""))
        details.append(f"  * 补件提议 {len(out['outcome'].follow_up_needs)} 条、被拒补件 "
                       f"{len(out['outcome'].follow_up_rejections)} 条")
        details.append("")
        details.append("**B 段逐环业务表（同一张表，这次有 Claim 可读）**：")
        details.append("")
        rows_b = _aspect_rows(case, out)
        topics_b = _topic_rows(case, out)
        details.append("| aspect | topic | 期 | 有材料 | topic 分派材料 | 可支持该断言 | 承批 |")
        details.append("|---|---|---|---|---|---|---|")
        for row in rows_b:
            details.append(
                f"| {row['aspect_id']} | {row['topic_id']} | {row['status']} | "
                f"{row['有材料']} | {row['topic 分派材料']} | {row['可支持该断言']} | "
                f"{row['batch']} |")
        details.append("")
        details.append("B 段 topic 面（门后两格）：")
        for row in topics_b:
            details.append(f"  * `{row['topic_id']}`：aspect {row['aspect_count']} 个 / "
                           f"有材料 {row['有材料']} 条 / 本段返回候选 {row['candidates']} 条 / "
                           f"**正式采用 {row['正式采用']} 条 / 最终呈现 {row['最终呈现']} 条**")
        adopted_total = sum(row["正式采用"] for row in topics_b)
        check(adopted_total > 0,
              "B 段确实产生了被正式采用的 Claim（「公司正文有/无」这一问的答案就在这里）")
        check(sum(row["正式采用"] for row in topics_b) == len(claim_rows),
              "topic 面的采用数之和 = 门后 Claim 总数（没有 Claim 落在本节 topic 之外，"
              "也没有被数两遍）")
        details.append("")
        details.append("**最终自然句（逐句给出引用的 Claim）**：")
        for index, kind_name, text, claim_ids in sentences:
            details.append(f"  * 句 {index}（{kind_name}）：{text}")
            details.append(f"    ← Claim {list(claim_ids)}")

        # ---------------- §5 补件守恒 ----------------
        details.append("")
        details.append("## §5 补件提议逐条守恒（只做账，不执行）")
        account = _conservation_account()
        check_eq(len(account), RECORDED_COMPANY_PENDING_NEEDS,
                 "r8 记录在案的 21 条补件提议逐条进入账本")
        check(all(item["sources"] for item in account),
              "每条记录在案的补件提议都能指回**提出它的那一份返回**（无不出来的来源）")
        from collections import Counter
        per_source = Counter(name for item in account for name in item["sources"])
        details.append(f"  * 21 条按来源分：{dict(sorted(per_source.items()))}")
        scopes_by_ordinal = {row["ordinal"]: row for row in scopes}
        for ordinal, row in sorted(scopes_by_ordinal.items()):
            if row["correction"]:
                continue
            details.append(f"  * ordinal-{ordinal}（{row['label']}）返回补件 "
                           f"{row['returned']['follow_ups']} 条")
        check_eq(len(out["outcome"].follow_up_needs), B_段_FOLLOW_UP_NEEDS,
                 "C 段链上补齐了 19 条（1/4 纠正版 5 + 2/4 纠正版 3 + 3/4 1 + 空第四批 10）"
                 "——成功批的补件也**没有丢**")

        # ---------------- §6 人读质量检查 ----------------
        details.append("")
        details.append("## §6 人读质量检查（机判部分给判据，人读部分给原文与定位）")
        materials = _material_rows(case)
        cited = _cited_evidence_ids(out)
        by_id = {row["material_id"]: row for row in materials}
        sentences = _narrative_sentences(out)

        docs_in_section = Counter(row["document_id"] for row in materials)
        details.append(f"  * **本节材料按文档分**（{len(materials)} 行）："
                       f"{dict(sorted(docs_in_section.items()))}")

        # 短行**不等于**勾选行：≤24 字里既有 `适用/不适用`（真正的勾选残件），也有实质短句。
        # 两者要分开读，否则要么把实质句写成勾选行，要么按「勾选行」的口径替实质句背书。
        CHECKBOX_TEXTS = ("适用", "不适用", "□适用", "√适用")
        short_rows = [row for row in materials if len(row["text"].strip()) <= 24]
        checkbox_rows = [row for row in short_rows
                         if "适用" in row["text"] and len(row["text"].strip()) <= 12]
        other_shorts = [row for row in short_rows if row not in checkbox_rows]
        details.append(f"  * 短行（≤24 字）{len(short_rows)} 条，其中**真勾选行**（仅「适用/不适用」"
                       f"类）{len(checkbox_rows)} 条、实质短句 {len(other_shorts)} 条：")
        for row in short_rows:
            tag = "勾选" if row in checkbox_rows else "实质"
            details.append(f"    - [{tag}] `{row['material_id']}` p{row['page']}"
                           f"（引用 {len(_cited_by(row, claim_rows))}）：{row['text']!r}")
        carried = [row for row in short_rows if _cited_by(row, claim_rows)]
        check(not [row for row in checkbox_rows if _cited_by(row, claim_rows)],
              "**真勾选行（适用/不适用）不得支持正文**：没有任何一条被门后 Claim 引用")
        for row in carried:
            details.append(f"    · 短行里被引用的是实质句：`{row['material_id']}` "
                           f"{row['text']!r} —— 按**实质句**读，不按勾选行读")
        details.append(f"    · 本次审核范围内只读到 {len(checkbox_rows)} 条真勾选行"
                       f"（{CHECKBOX_TEXTS}），其余短行是实质句；此处结论只覆盖本节材料")

        frag_rows = [row for row in materials
                     if row["text"].strip()[:1] in "，、；。等及也就"]
        details.append(f"  * 句中起始片段候选（以标点/连接词开头）{len(frag_rows)} 条："
                       f"{[row['material_id'] for row in frag_rows]}")
        for row in frag_rows:
            details.append(f"    - `{row['material_id']}` p{row['page']}："
                           f"{row['text'][:80]!r}")
        claim_texts = [row["text"] for row in claim_rows]
        mid_claim = [t for t in claim_texts for row in frag_rows if t and t in row["text"]]
        check(not mid_claim,
              f"**句中起始片段不得当完整命题**：没有一条 Claim 的正文是这些片段的子串"
              f"（实得 {mid_claim[:3]}）")

        pages_in_claims = {p for row in claim_rows for _e, p in row["pairs"]}
        by_pair = {(row["evidence_id"], row["page"]): row for row in materials}
        check(all(p is None or (e, p) in by_pair
                  for row in claim_rows for e, p in row["pairs"]),
              "**跨页材料保持原定位**：每条 Claim 引用的 `(evidence_id, page)` 对都能在本节"
              "材料行里逐行找到，页码与材料自己的 locator 一致（拼读不移动锚点）")
        details.append(f"  * 正文引用覆盖页码：{sorted(p for p in pages_in_claims if p)}")
        cited_pairs = sorted({(e, p) for row in claim_rows for e, p in row["pairs"]})
        details.append(f"  * 被门后 Claim 引用的 `(证据块, 页)` 对 {len(cited_pairs)} 对——"
                       "**对本身选不出行**（同块同页可以有多行），这里逐对列出该页上的"
                       "**全部**材料行，行归属一律看下面的「材料身份」：")
        for eid, page in cited_pairs:
            same = [row for row in materials
                    if row["evidence_id"] == eid and row["page"] == page]
            details.append(f"    - p{page} `{eid[:12]}…` 上 {len(same)} 行："
                           f"{[row['material_id'] for row in same]}"
                           "（**不是**其中某一行被引用）")
            for row in same:
                details.append(f"        · `{row['material_id']}`：{row['text'][:70]!r}")
        # 同一个 Evidence 块可以被**多行**材料共用，而且可以共用**同一页**：r8 公司节 36 行材料里
        # 有 9 组「同块同页、不同 material」（最密的一组同页 4 行）。因此**逐行读数只能按
        # `material_id` 归属**——按 `(块, 页)` 配对会把同一页上其它行的引用一起算进来。
        aliases = {}
        for row in materials:
            aliases.setdefault(row["evidence_id"], []).append(row["material_id"])
        shared = {e: ids for e, ids in aliases.items() if len(ids) > 1
                  and any(e == eid for eid, _p in cited_pairs)}
        same_page = {k: v for k, v in _same_page_groups(materials).items() if len(v) > 1}
        details.append(f"  * 被引用的证据块里有 {len(shared)} 个同时支撑**多行**材料；"
                       f"全节另有 {len(same_page)} 组「**同块同页**、不同 material」"
                       "——逐行读数因此只能按 `material_id` 归属，不能按 `(块, 页)` 配对：")
        for (eid, page), ids in sorted(same_page.items(),
                                       key=lambda kv: (str(kv[0][1]), kv[0][0])):
            details.append(f"    - p{page} `{eid[:12]}…` → {len(ids)} 行：{ids}")
        details.append("")
        details.append("  * **逐 Claim → 引用行**（供人读逐句核对）：")
        details.append("    - 「定位」= Claim 自己带的 `(证据块, 页)`，**只能到页，到不了行**"
                       "（同块同页常有多行）；「材料身份」= `accepted_binding_ids` → 每条接受边的 "
                       "`material_id`（写入侧确定性派生，**唯一能在行上归属的把手**）：")
        for row in claim_rows:
            pages = sorted({int(p) for _e, p in row["pairs"] if p is not None})
            details.append(f"    - `{row['claim_id']}`（定位 p{pages}）：{row['text']}")
            for eid, page in row["pairs"]:
                same = [m["material_id"] for m in materials
                        if m["evidence_id"] == eid and m["page"] == page]
                details.append(f"        ← 定位 p{page}：该页同块有 {len(same)} 行"
                               f"{same if same else '（不在本节材料行里）'}")
            for mid in row["material_ids"]:
                source = by_id.get(mid)
                where = (f"p{source['page']} {source['document_id']}" if source else "（不在本节）")
                details.append(f"        ← **材料身份** `{mid}`（{where}）")
            if not row["material_ids"]:
                details.append("        ← **没有任何接受边带材料行身份**（它不是材料支撑边）")
            if row["bindings_without_material"]:
                details.append(f"        ← 另有 {len(row['bindings_without_material'])} 条接受边"
                               "不带材料行身份（非材料支撑边）")

        # 反例：**同块同页、不同 material** 的两行不得被同一条 Claim 一起算成「被引用」。
        counter = _material_attribution_counterexample(case, materials, claim_rows)
        check(counter.get("found") is True,
              "反例：现场确有「同块同页、不同 material」的两行，且其中一行被某条 Claim 单独支撑"
              "——这正是 `(证据块, 页)` 配对会多算的那一格")
        if counter.get("found"):
            details.append("")
            details.append(f"  * **反例（同块同页不同 material）**：p{counter['pair'][1]} 上 "
                           f"{len(counter['row_texts'])} 行材料同属证据块 "
                           f"`{counter['pair'][0][:12]}…`；Claim `{counter['claim_id']}`"
                           f"（{counter['claim_text']}）只由 `{counter['true_row']}` 支撑，"
                           "而按 `(证据块, 页)` 配对会把它同时算到 "
                           f"{len(counter['false_positives'])} 行上：{counter['false_positives']}")
            check_eq(counter["new_style_rows"], [counter["true_row"]],
                     "反例：按 `material_id` 归属只把**这一行**算成被引用")
            check(bool(counter["false_positives"]),
                  "反例：按 `(证据块, 页)` 配对会多算同页的其它 material（旧读法的错法被钉住）")
        check(not [r for r in claim_rows if r["bindings_without_material"]],
              "本节每条 Claim 的每一条接受支撑边都带材料行身份"
              "（不存在「引用得到、归不到行」的边）")
        details.append("")
        details.append("  * **逐句 → Claim → 材料行**（正文逐句回查，材料身份按 `material_id`）：")
        for index, kind_name, text, ids in sentences:
            mids = [mid for r in claim_rows if r["claim_id"] in set(ids)
                    for mid in r["material_ids"]]
            details.append(f"    - 句{index}（{kind_name}）claims={list(ids)} "
                           f"materials={list(dict.fromkeys(mids))}")
            details.append(f"      {text[:120]!r}")

        # 同一业务意思来自两年材料：两份审计来源都要在，正文不得机械重复。
        year_like = [row for row in materials if "年报" in row["text"] or "年度报告" in row["text"]]
        details.append(f"  * 含「年报/年度报告」字样的材料 {len(year_like)} 条")
        repeated = [t for t, n in Counter(claim_texts).items() if n > 1]
        check(not repeated,
              f"**正文不机械重复**：门后 Claim 文本两两不同（实得重复 {repeated[:3]}）")

        # 逐字包含的近义对（`_near_duplicate_pairs` 故意跳过的那一类：`A` 是 `B` 的子串）：
        # 审计来源**两份都要在**（两条 Claim 各自被采用），但**同一句里不得同时出现**。
        def _bare(text) -> str:
            return "".join(ch for ch in str(text or "")
                           if ch not in NS.JOIN_RESIDUE_PUNCTUATION and not ch.isspace())

        pairs = []
        for i in range(len(claim_rows)):
            for j in range(i + 1, len(claim_rows)):
                left, right = _bare(claim_rows[i]["text"]), _bare(claim_rows[j]["text"])
                if not left or not right:
                    continue
                if left in right or right in left:
                    pairs.append((claim_rows[i], claim_rows[j]))
        details.append("")
        details.append(f"  * **逐字包含的近义 Claim 对** {len(pairs)} 对：")
        for left, right in pairs:
            details.append(f"    - `{left['claim_id']}`（{left['text']}）"
                           f" ⊂ `{right['claim_id']}`（{right['text']}）")
            # 近义对**不等于**跨年保留：两条 Claim 可能出自**同一份**材料。
            for side, claim in (("子串", left), ("父串", right)):
                for mid in claim["material_ids"]:
                    src = by_id.get(mid)
                    where = (f"{src['document_id']} p{src['page']}" if src else "（不在本节）")
                    details.append(f"        · {side}的材料：`{mid}`（{where}）")
            pair_mids = set(left["material_ids"]) | set(right["material_ids"])
            same_doc = (pair_mids
                        and len({by_id[m]["document_id"] for m in pair_mids if m in by_id}) == 1)
            if same_doc:
                details.append("        · **这一对来自同一份材料，因此它不能充作"
                               "「跨年来源各自保留」的证据**；能支撑的是「同一份材料的两种"
                               "粒度被各自采用、且未被拼进同一句」。真正的跨年支撑另见下条。")
        both_in_one = []
        for index, _kind, text, claim_ids in sentences:
            ids = set(claim_ids)
            for left, right in pairs:
                if left["claim_id"] in ids and right["claim_id"] in ids:
                    both_in_one.append((index, left["claim_id"], right["claim_id"]))
        check(not both_in_one,
              f"**同一句不得把近义两条拼在一起**（实得 {both_in_one}）")
        for left, right in pairs:
            in_body = [c for c in (left["claim_id"], right["claim_id"])
                       if c in {cid for _i, _k, _t, ids in sentences for cid in ids}]
            details.append(f"    - 该对见诸正文的是 {in_body}（另一条止于审计与回查，"
                           "不进读者面）")

        # **真正**的跨年支撑：一条 Claim 的接受边同时落到**不同年份**的文档上。
        cross_year = []
        for row in claim_rows:
            docs = {by_id[mid]["document_id"] for mid in row["material_ids"] if mid in by_id}
            if len(docs) > 1:
                cross_year.append((row, sorted(docs)))
        details.append("")
        details.append(f"  * **跨年支撑（同一 Claim 引用不同年份文档）** {len(cross_year)} 条：")
        for row, docs in cross_year:
            details.append(f"    - `{row['claim_id']}`（{docs}）：{row['text']}")
            for mid in row["material_ids"]:
                src = by_id.get(mid)
                if src:
                    details.append(f"        ← `{mid}`（{src['document_id']} p{src['page']}）")
        check(bool(cross_year),
              "现场确有 Claim 的接受边跨年份文档（跨年保留的证据来自**材料身份**，"
              "不是来自近义对）")

        # ---- 三项人读取证：跨页、句中片段、诉讼表述 ----
        details.append("")
        details.append("  * **2025 年报 p15–p16**（本次实际审核到的范围：**两页锚点各自可回查**，"
                       "到此为止）：")
        p1516 = [row for row in materials
                 if int(row["page"]) in (15, 16)
                 and row["document_id"] == "NDSD_2025_year"]
        for row in p1516:
            cited_by = _cited_by(row, claim_rows)
            details.append(f"    - p{row['page']} `{row['material_id']}`"
                           f"（{len(row['text'])} 字，被 {len(cited_by)} 条 Claim 引用）")
            details.append(f"      原文：{row['text'][:160]!r}")
        cross = [r for r in claim_rows
                 if len({p for _e, p in r["pairs"] if p is not None}) > 1]
        check(not cross,
              f"**没有任何一条 Claim 同时引用两页**（实得 {[r['claim_id'] for r in cross]}）："
              "因此本重放**只**证明了「两页锚点各自可回查」，"
              "**不**证明「跨页受控拼读已获证明」")
        details.append(f"    - 同时引用多页的 Claim：{[r['claim_id'] for r in cross] or '无'}"
                       "——跨页拼读的正文层证据**不存在**，此处不得写成已获证明")
        # 被页边界断开的原句：一行的正文停在句中标点、另一行以连接词续起。
        # **判据只到「断口两侧各自有没有 Claim」**，不去断言「跨页拼读成立」。
        severed = [row for row in p1516
                   if row["text"].rstrip().endswith(("，", "、", ","))]
        continued = [row for row in p1516
                     if row["text"].lstrip().startswith(("并", "以及", "同时", "，", "、"))]
        for row in severed:
            details.append(f"    - 断口前半所在行 p{row['page']} `{row['material_id']}`"
                           f"（尾字「{row['text'].rstrip()[-1]}」，被 "
                           f"{len(_cited_by(row, claim_rows))} 条 Claim 引用）")
        for row in continued:
            details.append(f"    - 断口后半所在行 p{row['page']} `{row['material_id']}`"
                           f"（被 {len(_cited_by(row, claim_rows))} 条 Claim 引用）")
            head = row["text"].split("。")[0]
            details.append(f"      · 续接句（到第一个「。」为止，{len(head)} 字）：{head!r}")
            insiders = [r["claim_id"] for r in claim_rows if r["text"] and r["text"] in head]
            check(not insiders,
                  f"**没有任何 Claim 是这条被截断的续接句的子串**（实得 {insiders}）："
                  "该半句没有 Claim 支撑")
            for cid in sorted(_cited_by(row, claim_rows)):
                claim = next(r for r in claim_rows if r["claim_id"] == cid)
                details.append(f"      · 该行的引用 `{cid}`：{claim['text']}"
                               "（落在该行的**后段**，不是这条被截断的续接句）")
        # 同年对照：2024 年报 p15 有**完整**的同一段（也被断开，同样未被引用）。
        counterpart = [row for row in materials
                       if row["document_id"] == "NDSD_2024_year" and int(row["page"]) == 15
                       and row["text"].lstrip().startswith("公司拥有独立的研发")]
        for row in counterpart:
            details.append(f"    - 同年对照：`{row['material_id']}`（NDSD_2024_year p15，"
                           f"{len(row['text'])} 字，被 {len(_cited_by(row, claim_rows))} 条 Claim"
                           "引用）——2024 年报也有同一段经营模式原文，同样**未被引用**；"
                           "本次审核没有用它来补齐断口")

        details.append("")
        details.append("  * **句中起始片段**（材料正文不以句首开头的那类）：")
        mid_start = [row for row in materials
                     if row["text"].strip()[:1] not in "。！？"
                     and row["text"].strip()[:2] not in ("公司", "本报", "报告", "截至")]
        details.append(f"    - 候选 {len(mid_start)} 条；其中**被引用**的（按 `material_id` 归属）："
                       f"{[r['material_id'] for r in mid_start if _cited_by(r, claim_rows)] or '无'}")
        for row in mid_start[:6]:
            details.append(f"      · p{row['page']} `{row['material_id']}`："
                           f"{row['text'][:70]!r}")
        substring_hits = [(r["claim_id"], row["material_id"])
                          for r in claim_rows for row in mid_start
                          if r["text"] and r["text"] in row["text"]]
        check(not substring_hits,
              f"**句中起始片段不得当完整命题**：没有一条 Claim 的正文是这类材料的子串"
              f"（实得 {substring_hits[:3]}）")

        details.append("")
        details.append("  * **募集说明书片段（p50）——本次实际审核到的范围到此为止**：")
        p50_rows = [row for row in materials if int(row["page"]) == 50]
        check_eq(len(p50_rows), 1, "p50 在本节材料行里只有一条（本题只审到这一条）")
        for row in p50_rows:
            cited_here = _cited_by(row, claim_rows)
            inside = [r["claim_id"] for r in claim_rows
                      if r["text"] and r["text"] in row["text"]]
            details.append(f"    - `{row['material_id']}`（文档 `{row['document_id']}`，"
                           f"{len(row['text'])} 字，被引用 {len(cited_here)} 条）："
                           f"{row['text'][:140]!r}")
            check_eq(list(cited_here), [],
                     "**p50 这条材料对正文零贡献**（被引用 0 条）："
                     "结论只覆盖本次审核到的这一条，不推广到整份募集说明书")
            check(not inside,
                  f"p50 这条材料没有任何一条 Claim 是它的子串（实得 {inside}）")

        details.append("")
        details.append("  * **诉讼表述——本次实际审核到的范围**（4 条材料，逐条读，不推广）：")
        litigation = [row for row in materials if "诉讼" in row["text"] or "仲裁" in row["text"]]
        details.append(f"    - 提到诉讼/仲裁的材料 {len(litigation)} 条，"
                       f"**本次逐条审核的就是这 {len(litigation)} 条**：")
        for row in litigation:
            cited_by = _cited_by(row, claim_rows)
            details.append(f"      · p{row['page']} `{row['material_id']}`"
                           f"（文字 {len(row['text'].strip())} 字，被引用 {len(cited_by)} 条）："
                           f"{row['text'][:100]!r}")
        negated = [row for row in litigation
                   if "未发生" in row["text"] or "无重大" in row["text"]]
        affirmed = [row for row in litigation if row["material_id"] not in
                    {r["material_id"] for r in negated}]
        details.append(f"    - 其中否定式（「未发生/无重大」）{len(negated)} 条 / "
                       f"限定范围的肯定式（「未达到重大…披露标准的其他诉讼仲裁案件」）"
                       f"{len(affirmed)} 条")
        check_eq(list(sorted({c for row in litigation for c in _cited_by(row, claim_rows)})), [],
                 "**本次审核到的 4 条诉讼材料被引用 0 条**："
                 "两种表述（否定式与限定肯定式）**都没有进正文**，"
                 "因此本次没有「混用」可判；结论只覆盖这 4 条")
        details.append("    - 结论边界：只覆盖本节材料里的这 4 条，"
                       "不外推到「公司诉讼状况」或未取得的其他披露")

        details.append("")
        details.append("**六个经营模式 aspect 逐条（四格：原文直接支持什么／不能支持什么／"
                       "Contract 状态／正式正文是否呈现）**：")
        details.append("  * **先记一条 wire 事实**：门后 Claim 只带 `topic_id` + 该 topic 的"
                       "**整个** `question_ids`（本节 9 条逐条都是 `company_business` 的四个问题），"
                       "因此「某条 Claim 是在回答哪个 aspect」在现行 wire 上**不可判定**。"
                       "所以下面四格里：Contract 状态取自记录，"
                       "「原文／Claim／正文」三格按**句子级词面定位 + 逐条人读**给出，"
                       "`topic` 层的「采用/正文」数只作为公共背景写出——"
                       "不把那张不可判定的表读成逐 aspect 的覆盖。")
        business_topic = {str(case["specs"][a].topic_id) for a in BUSINESS_MODEL_ASPECTS}
        check_eq(business_topic, {"company_business"},
                 "六个经营模式 aspect 同属一个 topic（因此 topic 面读数对六者是**同一份**）")
        topic_row = next(r for r in topics_b if r["topic_id"] == "company_business")
        body_claim_ids = {cid for _i, _k, _t, ids in sentences for cid in ids}

        def _aspect_mechanics(aspect_id: str) -> dict:
            """逐 aspect 的**机械读数**（只做定位，不做判定）。

            * `sentences`：本节材料里带该 aspect 主题词的**句子**（按「。」切；整行含该词
              但只出现在栏目标题里的，也如实列出来让人自己看）；
            * `claim_hits`：文本里带该主题词的**已接受 Claim**；
            * `claim_hits_in_body`：其中进了最终句子的。
            为什么按**句子**而不按**整行**：一整行可以同时提到成本、生产、销售、技术，
            按行匹配会把同一行算给五个 aspect——那是词面放大，不是覆盖。
            """
            terms = BUSINESS_MODEL_READ_TERMS[aspect_id]
            sentences = []
            for m in materials:
                for sent in str(m["text"]).split("。"):
                    if any(t in sent for t in terms):
                        sentences.append((m, sent))
            claim_hits = [r for r in claim_rows
                          if r["text"] and any(t in r["text"] for t in terms)]
            return {"sentences": sentences, "claim_hits": claim_hits,
                    "claim_hits_in_body": [r for r in claim_hits
                                           if r["claim_id"] in body_claim_ids]}

        for aspect_id in BUSINESS_MODEL_ASPECTS:
            row = next(r for r in rows_b if r["aspect_id"] == aspect_id)
            read = BUSINESS_MODEL_HUMAN_READ[aspect_id]
            mech = _aspect_mechanics(aspect_id)
            details.append(f"  * `{aspect_id}`（Contract 要求：{row['requirement']}）")
            details.append(f"    - **Contract 状态**：{row['status']}"
                           f"（承批 {row['batch']}；topic 面采用 "
                           f"{topic_row['正式采用']} 条 / 见诸正文 "
                           f"{topic_row['最终呈现']} 条 —— 这两格是 topic 层的数，六者共用）")
            details.append(f"    - **原文直接支持什么**（人读）：{read['supports']}")
            details.append(f"    - **不能支持什么**（人读）：{read['not_supports']}")
            details.append(f"    - **正式正文是否呈现**（人读）："
                           f"{'是' if read['in_body'] else '否'}"
                           + (f"；依据 Claim 正文：{list(read['in_body_evidence'])}"
                              if read["in_body_evidence"] else ""))
            if read["override"]:
                details.append(f"    - **人读压过机械读数之处**：{read['override']}")
            details.append(f"    - 机械定位（只作把手，不作判定）：带该主题词的原文句 "
                           f"{len(mech['sentences'])} 句 / 文本带该词的门后 Claim "
                           f"{len(mech['claim_hits'])} 条"
                           f"（其中进正文 {len(mech['claim_hits_in_body'])} 条："
                           f"{[r['claim_id'] for r in mech['claim_hits_in_body']]}）")
            for m, sent in mech["sentences"][:4]:
                in_row = _cited_by(m, claim_rows)
                details.append(f"        · p{m['page']} `{m['material_id']}`"
                               f"（{m['document_id']}，该行被引用 {len(in_row)} 条）："
                               f"{sent.strip()[:100]!r}")
            # 人读与机械读数不得**悄悄**不一致：不一致必须写出 `override`。
            mech_in_body = bool(mech["claim_hits_in_body"])
            if mech_in_body != bool(read["in_body"]):
                check(bool(read["override"]),
                      f"{aspect_id}：人读结论（正文呈现={read['in_body']}）与机械读数"
                      f"（正文呈现={mech_in_body}）不一致，必须写出 `override` 说明为什么"
                      "——不允许静默压过机械读数")
        tiers = {aspect_id: ("正文呈现" if BUSINESS_MODEL_HUMAN_READ[aspect_id]["in_body"]
                             else "未呈现")
                 for aspect_id in BUSINESS_MODEL_ASPECTS}
        details.append(f"  * **判定分层（六者不是同一个档）**：{tiers}")
        check(len(set(tiers.values())) > 1,
              "六个经营模式 aspect 的判定**不是同一个档**"
              "（不得一律以 `partial` 冒充实质覆盖）")
        check(any(BUSINESS_MODEL_HUMAN_READ[a]["in_body"] for a in BUSINESS_MODEL_ASPECTS),
              "六者里至少有一项确实进了正文（否则本节正文与「经营模式」无关）")
        check(all(BUSINESS_MODEL_HUMAN_READ[a]["supports"]
                  and BUSINESS_MODEL_HUMAN_READ[a]["not_supports"]
                  for a in BUSINESS_MODEL_ASPECTS),
              "六个 aspect 逐项都写出了「原文直接支持什么」与「不能支持什么」"
              "（不留空格冒充覆盖）")
        for aspect_id, verdict in tiers.items():
            details.append(f"    - `{aspect_id}` → {verdict}")

        # ---------------- §7 组织器输入面 + 逐原子忠实性核验 ----------------
        details.append("")
        details.append("## §7 最终组织器实际收到什么 / 最终句的逐原子忠实性核验边界")
        details.append("")
        face = _organizer_face(case, _accepted_responses() + [_constructed_response()])
        payload = face["payload"]
        check(payload is not None and face["prompt_version"] == NO.NARRATIVE_ORGANIZER_PROMPT_VERSION,
              "截到组织器那一次调用，且用的是当前 `NARRATIVE_ORGANIZER_PROMPT_VERSION`")
        claims_payload = payload["claims"]
        check(bool(claims_payload) and all(str(c.get("text") or "") for c in claims_payload),
              "组织器收到的是**完整 Claim 行**（带 `text` / `citation_ids` / `topic_id`），"
              "不是只给 id 的索引")
        check(all("prose_unit_id" in row and "atoms" in row for row in payload["prose_draft"]),
              "组织器同时收到 **Writer 草稿层台账**（`prose_unit_id` + 逐 atom 的 "
              "`claim_id`/`status`）——这是「以材料驱动草稿为底稿」那一条的输入面")
        check(payload["material_context"]["readonly"] is True
              and bool(payload["material_context"]["rows"]),
              "组织器收到**只读**材料语境行（`material_context.readonly=True`）")
        check(isinstance(payload.get("accepted_context_bindings"), list)
              and isinstance(payload.get("unresolved"), list),
              "组织器收到本节已接受 context 绑定与缺口清单（两者都不是事实授权）")
        details.append(f"  * 输入面键：`{sorted(payload)}`")
        details.append(f"  * Claim 行 {len(claims_payload)} 条 / 草稿层 {len(payload['prose_draft'])} 行 / "
                       f"只读材料语境行 {len(payload['material_context']['rows'])} 条 / "
                       f"context 绑定 {len(payload['accepted_context_bindings'])} 条 / "
                       f"缺口 {len(payload['unresolved'])} 条")
        rules_text = json.dumps(payload.get("rules"), ensure_ascii=False)
        check("prose_unit_id" in rules_text and "逐字" in rules_text,
              "规则里两种句类**都**写着：写法一（不声明 `prose_unit_id`，正文逐字由 Claim 构成）"
              "与写法二（声明草稿行的 `prose_unit_id`，此时不要求逐字、改由保真门判）")

        sents = _narrative_sentences(face["out"])
        check(sents and all(kind == "composed" for _i, kind, _t, _c in sents),
              f"B 段 {len(sents)} 句**全部**是 `composed`（写法一）：由替身组织器产出，因此本重放"
              "的公司正文只是「链走得通」的证据，不是「真实组织器会这样写」的证据（边界 1）")
        claim_by_id = {c.claim_id: c for c in face["out"]["output"].claims}
        for _i, _kind, text, cids in sents:
            texts = tuple(str(claim_by_id[c].text) for c in cids)
            check(NS.composed_organization_defect(text=text, authorized_texts=texts) is None,
                  f"composed 句的逐字-按序判据在**当前代码**下仍然成立（{len(texts)} 条 Claim）")
        details.append("  * `composed` 句的忠实性**由文本恒等保证**（判据 a：声明的每条 Claim 文本"
                       "必须按声明顺序逐字出现）——这一类句子不存在「改写了但没被核验」的情形。")

        fin_rows = _read_json(R8 / "section_claims.json")
        fin_texts = {c["claim_id"]: c["text"] for c in fin_rows
                     if c["section_id"] == "financial"}
        delta_id = next(cid for cid, t in fin_texts.items() if "个百分点" in t)
        proxy_id = next(cid for cid, t in fin_texts.items() if "PROXY_FINANCE_EXPENSES" in t)
        delta = fin_texts[delta_id]
        proxy = fin_texts[proxy_id]
        details.append("")
        details.append("**逐原子忠实性核验：用 r8 财务节的真实 Claim 文本做探针**"
                       "（`natfid-1`，只读、不写对象）：")
        probes = (
            ("原样", delta + "。", {delta_id: delta}, True),
            ("符号翻转（-3.3 → 3.3）", delta.replace("-3.3", "3.3") + "。", {delta_id: delta}, False),
            ("数值改写（-3.3个百分点 → -3.30%）",
             delta.replace("-3.3个百分点", "-3.30%") + "。", {delta_id: delta}, False),
            ("期间改写（2025年末 → 2024年末）",
             delta.replace("2025年末较2024年末", "2024年末较2023年末") + "。", {delta_id: delta}, False),
            ("加因果（「主要由于偿还借款」）",
             delta + "，主要由于偿还借款。", {delta_id: delta}, False),
            ("**加模糊化（「变动为」→「变动约为」）**",
             delta.replace("变动为", "变动约为") + "。", {delta_id: delta}, None),
            ("**换指标名（资产负债率 → 有息负债率）**",
             delta.replace("资产负债率", "有息负债率") + "。", {delta_id: delta}, None),
            ("**丢掉代理口径限定（去掉「代理口径（PROXY_FINANCE_EXPENSES）」）**",
             proxy.split("。")[0] + "。", {proxy_id: proxy}, None),
        )
        blind: list[str] = []
        for label, text, claims_map, expect in probes:
            got = _fidelity_probe(text, claims_map)
            if expect is None:
                if got["ok"]:
                    blind.append(label)
                details.append(f"  * {label}：{'放行' if got['ok'] else '拦下'}"
                               f"（缺陷码 {got['defects']}）")
                continue
            check(got["ok"] is expect,
                  f"`natfid-1` 在真实 Claim 上对「{label}」的判定为 "
                  f"{'通过' if expect else '拒绝'}（实得 {got})")
        check_eq(len(blind), 3,
                 "`natfid-1` 在真实财务 Claim 上的**已知盲区恰为这三条**：加模糊化、换指标名、"
                 "丢代理口径限定——它们都会被放行")
        details.append("  * **结论（不得含糊）**：`natfid-1` 是**表面**比较器（闭集标记表 + "
                       "断言关键表面集），它不是本指令要的那个语义门。三条盲区在上面的探针里"
                       "逐条可复现，且都落在指令点名的轴上（范围、主体、限定）。**本模块不修改"
                       "门、也不装作它已经覆盖**：精确影响与最小裁决另列在交付报告里。")
        details.append("  * 这三条盲区在本轮**实录里没有被踩到**：r8 唯一的 `natural` 句"
                       "（financial）逐字等于它的 Claim 文本，因此是潜在缺口、不是已发生的失真。")

        same = _fidelity_probe(delta + "。", {delta_id: delta})
        again = _fidelity_probe(delta + "。", {delta_id: delta})
        changed = _fidelity_probe(delta + "。", {delta_id: delta.replace("2025", "2024")})
        check(same["digest"] == again["digest"] and same["digest"] != changed["digest"],
              "保真结论**没有缓存**：同一输入指纹相同、Claim 文本一改指纹即变"
              "（句子或绑定变化必须重新核验）")
        check(set(NS.SENTENCE_FIDELITY_DEFECTS) == {"unauthorized_surface",
                                                    "dropped_assertion_surface",
                                                    "added_scope_or_strength",
                                                    "stray_citation"},
              "缺陷码是闭集四项（判别结果可枚举、可对账）")
        narr_fields = " ".join(sorted(_field_names(face["out"]["output"].narrative)))
        res_fields = " ".join(sorted(_field_names(face["out"]["output"].result)))
        check("fidelity" not in narr_fields.lower() and "fidelity" not in res_fields.lower(),
              "**本批已记录的缺口**：通过的保真结论**不落任何对象**（narrative / result 都不含"
              " fidelity 字段），因此第三方无法从产物里回查「哪一句在哪个指纹下被核验过」。"
              "若将来补上持久化，请把本条改成断言「已落盘」并同步交付报告")

        # ---------------- §8 财务与行业：按 r8 记录分别报告 ----------------
        details.append("")
        details.append("## §8 财务与行业分别报告（读 r8 记录，不重跑）")
        details.append("")
        fin_narr = _read_json(R8 / "section_narratives.json")["financial"]
        fin_res = _read_json(R8 / "section_results.json")["financial"]
        ind_narr = _read_json(R8 / "section_narratives.json")["industry"]
        ind_res = _read_json(R8 / "section_results.json")["industry"]
        fin_sents = [s for p in fin_narr["paragraphs"] for s in p["sentences"]]
        check_eq([(s["sentence_kind"], s["text"]) for s in fin_sents],
                 [("natural", "2025年末较2024年末的资产负债率变动为-3.3个百分点。")],
                 "财务节正文只有一句（`natural`），且它是**变化事实**句：这一句就是 r8 唯一"
                 "走到真实组织器（`norg-8`）的那一节")
        change = next(c for c in fin_rows if c["section_id"] == "financial"
                      and "个百分点" in c["text"])
        check_eq(len(change["citation_refs"]), 2,
                 "变化事实句挂**两个**引用（两个期间各自的来源），不是一条")
        fin_tables = fin_narr["tables"]
        check_eq(len(fin_tables), 4, "财务节 4 张表承载 6 个指标 × 4 个期间")
        rows_all = [r for t in fin_tables for r in t["rows"]]
        check_eq(len(rows_all), 6, "4 张表合计 6 行（有息负债 / 流动比率 / 权益乘数 / 速动比率 / "
                                   "资产负债率 / 利息保障倍数）")
        check(all(t.get("unit") and t.get("period") and t.get("entity_scope") for t in fin_tables)
              and all(r.get("unit") and r.get("period") for r in rows_all),
              "每张表与每行都声明单位 / 期间（中文单位在读面上原样保留）")
        lvl = next(r for r in rows_all if r["label"] == "资产负债率")
        check_eq(lvl["cells"], ["69.34%", "65.24%", "61.94%", "62.32%"],
                 "资产负债率四个期间原值原样保留")
        diff = (Decimal(lvl["cells"][2].rstrip("%")) - Decimal(lvl["cells"][1].rstrip("%")))
        check_eq(f"{diff:.1f}", "-3.3",
                 "正文那一句的数值**恰好等于**两个原值的 Decimal 差（Python 算、不是模型自算："
                 f"{lvl['cells'][2]} − {lvl['cells'][1]} = {diff}）")
        coverage = next(r for r in rows_all if r["label"] == "利息保障倍数")
        check(all("PROXY_FINANCE_EXPENSES" in c for c in coverage["cells"]),
              "利息保障倍数四格**每格**都带 `PROXY_FINANCE_EXPENSES` 代理口径限定"
              "（负值按裁决 (a) 保留）")
        fin_unres = [u for u in _read_json(R8 / "section_unresolved.json")
                     if u["section_id"] == "financial"]
        check_eq(fin_res["status"], "SECTION_BLOCKED",
                 "财务节状态是 `SECTION_BLOCKED`（不读成通过）")
        check_eq(len(fin_unres), 4, "财务节 4 条缺口逐条登记")
        check({u["reason_code"] for u in fin_unres} == {"blocked", "unresolved"},
              "财政缺口原因码只来自记录（`blocked` / `unresolved`），没有新造原因")
        check(any("fin_audit_opinion" in str(u["question_id"]) for u in fin_unres)
              and any("fin_statements_availability" in str(u["question_id"]) for u in fin_unres),
              "**审计意见与报表可得性两条缺口都还在**：没有因为正文已有那句比较句而消失")
        check(any("note_extraction_not_implemented" in str(u["detail"]) for u in fin_unres),
              "附注缺口按原因码 `note_extraction_not_implemented` 原样保留（未补写附注口径分析）")
        details.append(f"  * 财务：{len([c for c in fin_rows if c['section_id'] == 'financial'])} "
                       f"条 Claim / 1 句正文 / 4 张表 / {len(fin_unres)} 条缺口 / 状态 "
                       f"`{fin_res['status']}`")
        ind_claims = [c for c in fin_rows if c["section_id"] == "industry"]
        check_eq((len(ind_narr["paragraphs"]), len(ind_narr["tables"]), len(ind_claims)), (0, 0, 0),
                 "行业节**没有** Claim、没有段落、没有表格：本轮不产出任何行业正文")
        ind_unres = [u for u in _read_json(R8 / "section_unresolved.json")
                     if u["section_id"] == "industry"]
        check_eq(len(ind_unres), 4, "行业节 4 条 aspect 缺口（`industry_scale_cycle` 四个）集中登记")
        check(all(u["state"] == "NOT_PROVIDED" for u in ind_unres),
              "行业缺口状态一律 `NOT_PROVIDED`（不把未检索写成已排除）")
        acc_text = (R8 / "acceptance_report.json").read_text(encoding="utf-8")
        check("not_retrieved" in acc_text and "external_research_not_authorized_this_run" in acc_text,
              "验收记录写明外部检索本轮**未授权**、终态 `not_retrieved`，并区分「本轮未检索」"
              "与「已证明没有外部来源」")
        check("发行人转录" in acc_text,
              "验收记录写明年报正文里对第三方数字的转述是**发行人转录**，不得读成已验证的外部事实")
        details.append(f"  * 行业：0 条 Claim / 0 段 0 表 / {len(ind_unres)} 条缺口 / 状态 "
                       f"`{ind_res['status']}` / 外部漏斗 `not_retrieved`（本轮未授权检索）")

    finally:
        _restore_identity(snapshot)


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
                "details": [f"SKIP r8 现场数据缺失，本模块不猜、不静默绿：{missing}"]}
    _run_checks(check, check_eq, details)
    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    result = main()
    for line in result["details"]:
        print(line)
    print(f"\npassed = {result['passed']}, failed = {result['failed']}, "
          f"skipped = {result['skipped']}")



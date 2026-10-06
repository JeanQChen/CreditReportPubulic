"""M930-3 双节纵链：**同版本**业务读回（create-only）。

用法::

    # 离线读回（缺省）：写作与审阅都是替身，装上断网 guard
    python -X utf8 -m scripts.run_m930_3_cited_chain
    python -X utf8 -m scripts.run_m930_3_cited_chain --subject <主体> --subject-name <名称>

    # 真实写作 + 真实独立审阅（**每批逐次单独授权**；模型与上限写在代码里）
    python -X utf8 -m scripts.run_m930_3_cited_chain --mode real --run-id <新 run-id>

两种模式**只**在「谁写、谁审」这一处不同，输入面逐字相同：研究侧一律走离线装配
（本批没有获批研究调用），写作/审阅在 `--mode real` 下换成
`LlmCitedProseClient` / `LlmCitedReviewClient`，并复用同一套共享调用账本
（`sections.cited_budget`，`cited-budget-3`：获批节 `company` 与 `financial`，每节 ≤1 写 +
≤1 审、合计 ≤4、重试 0、模型必须等于 `config.LLM_MODEL`；**返修本批未获批**，请求面根本
不构造）。预算若表达不了这几条上限，或本节集合不在获批名单里，脚本在**第一请求之前**停下。

本脚本做什么
------------
它把本批（§0.19–§0.21）新写作链的**输入与输出**在同一版本里逐项摊开，落成一个新目录。
**单节**时（`--section company`）全部文件落根::

    evaluation/results/<run-id>/
        cited_input_manifest.json   新写作链的精确输入（逐材料原文 + 来源角色 + locator）
        cited_prose.json            实际正文（小节 → 段 → 句 → 逐句引用）
        sentence_checks.json        逐句硬核对（现行判据版本见 `sections.sentence_check`，
                                    含登记轴与呈现轴两条**并行**栏目归属）
        withheld_candidates.json    被替身自己撤下的候选片（逐条带原因码：数字未授权 /
                                     历史来源当前化 / 表单字形；撤下 ≠ 来源里没有）
        fact_placement.json         逐条权威事实的**落栏去向**（`placed` / 路由里没有它 /
                                     那一栏本次请求面没有 / 每栏句数上限；去向 ≠ 覆盖结论）
        presentation_routing.json   呈现层「指标 → 栏目」路由声明（`fpr-2`；**不是**事实权威）
        cited_source_scope__<topic>.json/.md
                                    `fin_source_scope` 的**确定性**来源/口径呈现与 typed 缺口
                                    （**零模型调用**；不是正文，没有引用与审阅）
        cited_balance_structure__<topic>.json/.md
                                    `fin_balance_structure` 的**确定性**资产负债结构／重大科目
                                    变化呈现与 typed 缺口（同上：**零模型调用**，不是正文）
        source_table_display.json   §0.21 路径 b：原 PDF 表区的只读展示集（真实读数）
        source_table_display.md     同上的人读版
        source_display/*.png        逐个区域的**原件像素**（人确认时看的就是这些）
        demo_page.md                **一页**：两条可读路径 + 财务表 + 缺口 + 审阅 + 状态分列
        review_issues.json          审阅意见（`crv-2` / `rvi-2`；离线模式下是**替身回声**）
        cited_report_version.json   四个状态轴的装配结果（版本见 `sections.cited_report` 的两个常量）
        cited_preview.md            **标注「不可发布」**的逐句预览 + 同版指标表
        cited_metric_tables.json    财务节的**确定性**「指标 × 期间」表（`cmt-4`/`cmtr-4`）
        cited_call_ledger.json      本轮写作/审阅的**共享调用账本**（离线模式写明「无真实调用」）；
                                    **失败也写**：`run_outcome` 标 completed/failed，失败时另带
                                    `failure`（异常身份、节、那次调用的 call_id），且原子落盘
        table_proof_matrix.json     图侧逐表完整证明矩阵（**真实文档**，非替身；**文档级**，只一份）
        readback.md                 人读回：四轴分列

**多节**时（缺省 `--section company,financial`）每节的那一批文件落 `run_dir/<section>/`，
根上另写一份 `demo_page.md`：它把各节的同一批产物**原文**并排。它**不**给跨节的合并
`report_version` —— 每节的锚只含该节写作侧输入，跨节是两个不同的锚，合成一个就是发明
一个不存在的身份。`table_proof_matrix.json` 是文档级读数，两种布局下都在根上只一份。

一节里的 topic 从哪来
---------------------
逐节逐字取**本节任务自己的** `topic_ids`（= profile 为这一节选中的那一片），入口只允许
**逐项相等**地重申。财务节本次带**三个** topic（`fin_source_scope` + `fin_balance_structure`
+ `fin_solvency`）：`fin_solvency` 走 cited 写作链（本节**唯一**的写作 topic），另两个走
确定性呈现——它们**不**占用那一个写作名额。不给入口留一个「缺省 topic」——那正是本批修掉的
缺陷：`fin_source_scope` 曾被那个缺省静静跳过，读回里连一行都没有。

两条**必须分清**的轴
--------------------
1. **真实部分**（`table_proof_matrix.json`）：读的是**真实 PDF + 真实只读 evidence 库**，
   走的是 `LiveVerifiedSpanSource` → `build_live_table_source` → `release_graph_tables`
   这条**图侧正式组合根**。这里没有替身，读数就是本机真实文档上的真实读数。
2. **替身部分**：研究侧 LLM 一律是确定性替身（`MODE_OFFLINE`）；写作侧与审阅侧在**缺省
   模式**下是本脚本内的**确定性替身客户端**，不是语言模型，证明的是**链路**，不能被称为
   「真实模型正文」，也不能据此宣称任何内容门通过。`--mode real` 下这两处换成真实模型，
   但**研究侧仍是替身装配**——真实模式也不能被读成「整条链都是真的」。

为什么用 `RealEnvironment` 而不是读历史 run
-------------------------------------------
`VerifiedPackSet` 没有持久化读回入口：它由 `resolve_pack_set` 从**临时** topic 库现读现造，
那个库（`workdir/research.db`）随运行结束被清理，仓库里不存在任何 `research.db` 落地文件；
`section_chain_v2.db` 里也没有 topic pack 表。因此「当前版本的 Pack 身份」只能由当前版本的
正式构建路径现算——本脚本走的正是那一条（`evaluation.run_m930_3_acceptance.RealEnvironment`），
**不**是第二套研究运行时。

本脚本**不做**的事
------------------
不 stage / 不 commit / 不 seal；不覆盖任何历史 run；不写 `data/` 下的任何库
（`RealEnvironment` 只绑定路径、不 `init_db`）；不宣称 M930-3 或 TS5 关闭。

联网与真实调用：**缺省模式下一次都不发**（断网 guard 挂在整段读回上，碰到就当场失败）。
`--mode real` 下**只**发获批的那几次写作/审阅调用，研究侧与外部检索**都不**发；模型、
份数、上限、重试次数都写在 `sections/cited_budget.py` 里，不在命令行可调。

本 run 读的是哪几份 PDF
-----------------------
`--run-input <dir>` 指向**操作者本次上传的原始字节**（`sections/cited_run_input.py` 的
`cri-1` 布局：`objects/<sha256>.pdf` + `run_input_manifest.json`）。给出时，链里读 PDF 的
三个点（`_member_lives`、`build_source_display`、`RealEnvironment`）**全部**按
`(document_id, sha256)` 从该目录取对象，**任何一处都不回退**到 evidence 库里登记的
`source_path`，更不落到 `data/samples`；缺一份、多一份、字节变化、`document_id` 不在上传集里，
都在**建立结果目录之前**停住。运行目录里另写一份 `run_input_binding.json`，读者据此
逐字比对「这次跑的是哪几份字节」。

不给 `--run-input` 时用登记路径——那是历史兼容运行（含全部既有离线重放），不是演示路径。
`--run-input` 同时也开启 `run_progress.jsonl`（`rj-1`）：**真实阶段边界**上的追加式事件日志，
带真实 UTC 时间戳。它**不**声称任何可恢复点（`resumable=false`、`checkpoint_id=""`），因为
本 run 确实没有与同一运行身份绑定的恢复点。

本 run 复用的是哪一期财务权威
----------------------------
`--financial-input <dir>` 指向**操作者本次上传的三份财务 XLSX**（`cited_financial_input.py`
的 `cfi-1` 布局）。给出时，链在**建立结果目录之前**核验：这三份上传与财务库里**当前有效
快照**声明的完整 `source_versions` 逐份**同字节**，且主体、期末、合并口径、币种、用途与
快照 id 全部对得上；装配期再核一次快照没有漂移。任一项不符，整轮在**第一个请求之前**拒绝，
**不以旧快照或库里现成的一份顶替**。本批**不重抽工作簿、不写共享财务库**：财务节照旧从既有
权威快照读事实，这三份上传只用于证明「本次上传核对并复用了同字节的已建立权威快照」。
真实模式下要写财务节却不给 `--financial-input` 即拒。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from assurance import schema as AS                               # noqa: E402
from contracts import schema_v2 as _S2                            # noqa: E402
from document_structure import live_span_source as LSS          # noqa: E402
from document_structure import live_table_source as LTS         # noqa: E402
from document_structure import table_local_proof as TLP         # noqa: E402
from evaluation import run_m930_3_acceptance as ACC              # noqa: E402
from harness import graph_table_release as GTR                   # noqa: E402
from harness import period_extraction as PE                       # noqa: E402
from harness import report_clock as RC                           # noqa: E402
from harness import topic_schema as TS                            # noqa: E402
from llm import budget as LB                                     # noqa: E402
from llm import client as LLC                                    # noqa: E402
from sections import cited_balance_structure as CBS               # noqa: E402
from sections import cited_budget as CB                          # noqa: E402
from sections import cited_call_journal as CCJ                   # noqa: E402
from sections import cited_financial_input as CFI                # noqa: E402
from sections import cited_financial_table as CFT                # noqa: E402
from sections import cited_report as CRP                         # noqa: E402
from sections import cited_rework as CRW                         # noqa: E402
from sections import cited_run_authorization as CRA              # noqa: E402
from sections import cited_run_input as CRI                      # noqa: E402
from sections import cited_run_journal as CRJ                    # noqa: E402
from sections import cited_review as CR                          # noqa: E402
from sections import cited_source_scope as CSS                    # noqa: E402
from sections import cited_value_trace as CVT                    # noqa: E402
from sections import cited_writer as CW                          # noqa: E402
from sections import financial_presentation_routing as FPR        # noqa: E402
from sections import material_context as MC                      # noqa: E402
from sections import narrative_schema as NS                      # noqa: E402
from sections import pack_writer as PW                           # noqa: E402
from sections import sentence_check as SC                        # noqa: E402
from sections import source_role_scope as SRS                    # noqa: E402
from sections import source_table_display as STD                 # noqa: E402
from sections import writing_spec as WS                           # noqa: E402

DEFAULT_RUN_ID = "m930_3_cited_offline_r1"
#: 本脚本的**缺省** profile：阶段 A（`2026-09-30_DEMO_BACKBONE_MILESTONE.md` §16 第 1 条）。
#:
#: 它只选 `company_business` / `fin_source_scope` / `fin_solvency` 三个 topic。缺省值必须与
#: 阶段 A 的范围**同源**：节级写作清单是从本节的 `pack_set` 现造的，而 `pack_set` 的 topic
#: 由 profile 的 `selected_topics` 决定。缺省仍指向旧的三 topic 双节 profile，就会让
#: `company_identity` / `company_legal_risks` 的材料继续混进主营业务清单——范围写在 profile
#: 里，却由脚本的缺省值悄悄放宽回去。
#:
#: 旧 profile **只增不改**，仍可显式传入（历史 run 的可复现性不依赖缺省值）。
DEFAULT_PROFILE = "templates/demo_scopes/phase_a_business_finance_v1.yaml"
#: 本批新增的 v2 profile：相对 v1 **只增** `fin_balance_structure` 一条 `selected_topics`，
#: 使冻结 Contract 里已有的资产负债三栏进入范围（见该文件抬头）。v1 **逐字不动**——它的
#: `profile_fingerprint` 进投影、清单与报告版本身份，改它等于改写历史 run 的身份。
#:
#: 这里**不改 `DEFAULT_PROFILE`**：缺省值必须与阶段 A 的 milestone 范围同源，把新增范围
#: 悄悄变成缺省，正是「范围写在 profile 里、却由脚本缺省放宽回去」那类错法的镜像。
#: 要跑新范围就显式传 `--demo-scope-profile <这个常量>`。
BALANCE_STRUCTURE_PROFILE = "templates/demo_scopes/phase_a_business_finance_v2.yaml"
DEFAULT_SECTION = "company"
#: 本次演示默认写哪些节。逐节的 topic **不由这里给**：它逐字来自本节任务自己的
#: `topic_ids`（见 :func:`_resolve_topic_ids`），入口只允许**逐项等于**地重申，
#: 不允许缩小它。
DEFAULT_SECTIONS: tuple[str, ...] = ("company", "financial")

#: **确定性**呈现的 topic：这些 topic 的选材与缺口由本节权威自己的字段逐条读出，**不写正文、
#: 不发模型请求**（`sections.cited_source_scope`）。没有登记的 topic 一律走 cited 写作链。
#:
#: 为什么要登记而不是「让清单为空时自动退化成确定性呈现」：清单为空还可能意味着主题选材出
#: 了问题。把「这一栏本来就不该由模型写」与「这一栏本该有材料、现在没有」写成同一种处理，
#: 读者就分不出哪一条是真的缺口。
#: 本批新增的确定性呈现 topic（资产负债结构）。写成常量是为了让「登记的键」与「调用方断言
#: 的名字」同源：调用方手打一个字面量，注册表改键时两边会静默错开。
BALANCE_STRUCTURE_TOPIC = "fin_balance_structure"

DETERMINISTIC_TOPIC_PRESENTERS: dict[str, str] = {
    #: `fin_source_scope` 的十条 Contract 要求问的是「报表本身齐不齐、期间/口径/单位是什么、
    #: 审计意见有没有」——答案已经在 `FinancialPackArtifact` 自己的字段里。让模型复述
    #: 「本次快照的 scope 是 consolidated」不会让这句话更可信。
    "fin_source_scope": "cited_source_scope",
    #: `fin_balance_structure` 的三条 Contract 要求（资产负债结构 / 重大科目变化 / 15% 强筛）
    #: 全部能逐条指认到本节权威自己的 `item_*` 科目事实与 `SOLV_DEBT_RATIO` 指标事实，且
    #: 每一条读数都带自己的引用。让它经过一次写作调用只会把可直接回查的权威值变成需要逐句
    #: 复核的生成文本（见 `sections/cited_balance_structure.py` 的模块说明）。
    #:
    #: 它**不占**「一节一个 Writer topic」的名额：那一名额给的是 `fin_solvency`
    #: （`_split_cited_and_deterministic` 只把没登记在这里的 topic 算进 cited）。
    BALANCE_STRUCTURE_TOPIC: "cited_balance_structure",
}

#: 确定性产出者的**唯一**构造函数登记表：名字 → `(*, section_id, topic_id, requirement,
#: authority) → 呈现产物`。两个族读的是**不同**的输入（`css-2` 读 `artifact` 的字段、
#: `cbs-2` 读 `artifact.facts` 逐条事实），因此在这里各写一个适配，而不是把它们塞进同一个
#: 签名里假装同形。
_DETERMINISTIC_PRESENTERS: dict[str, Callable[..., Any]] = {
    "cited_source_scope": lambda *, section_id, topic_id, requirement, authority: (
        CSS.build_cited_source_scope(
            section_id=section_id, topic_id=topic_id, requirement=requirement,
            artifact=getattr(authority, "artifact", None))),
    "cited_balance_structure": lambda *, section_id, topic_id, requirement, authority: (
        CBS.build_cited_balance_structure(
            section_id=section_id, topic_id=topic_id, requirement=requirement,
            authority=authority)),
}

#: 确定性产出者的**唯一**读者面渲染器登记表。键与 `DETERMINISTIC_TOPIC_PRESENTERS` 的取值
#: 同域：落盘文件名与读者面渲染都走**同一个**名字，两者不可能指到不同的族。
_DETERMINISTIC_RENDERERS: dict[str, Callable[[Any], str]] = {
    "cited_source_scope": CSS.render_cited_source_scope_markdown,
    "cited_balance_structure": CBS.render_cited_balance_structure_markdown,
}


def _deterministic_presenter_name(*, topic_id: str, section_id: str) -> str:
    """topic → 已登记的确定性产出者名。没登记即 fail-closed（不猜、不退化成正文）。"""
    name = DETERMINISTIC_TOPIC_PRESENTERS.get(str(topic_id))
    if not name:
        raise SystemExit(
            f"topic {topic_id!r} 在节 {section_id!r} 里被归入确定性呈现，却没有任何登记的产出者"
            f"（已登记 {sorted(DETERMINISTIC_TOPIC_PRESENTERS)}）："
            "不知道用哪套读法就必须停住，不得跳过这一栏")
    if name not in _DETERMINISTIC_PRESENTERS or name not in _DETERMINISTIC_RENDERERS:
        raise SystemExit(
            f"topic {topic_id!r} 登记的产出者 {name!r} 没有同时登记构造与渲染两处"
            f"（构造 {sorted(_DETERMINISTIC_PRESENTERS)} / 渲染 "
            f"{sorted(_DETERMINISTIC_RENDERERS)}）：产物与读者面写在两个地方，缺一个即停")
    return name

#: 本链**写正文**的 topic 只能有一个：一节两栏正文要各带一份清单与各自的身份，那是另一件事，
#: 不在本批范围内。多于一个即 fail-closed，不挑一个写、也不把两栏拼成一份清单。
MAX_CITED_TOPICS_PER_SECTION = 1

#: 离线替身自身的身份声明。它进 `writer_identity` / 审阅调用元数据，**不**冒充模型版本。
OFFLINE_WRITER_IDENTITY = "offline-extractive-stitcher@cwp-1"
#: 无 Pack 材料边界那一支（财务/附注/外部）的离线替身身份：与上面那个**不同**的一个，因为
#: 它读的是权威事实行、写的是事实命题，不是材料正文。两个替身不得共用一个身份。
OFFLINE_FACT_WRITER_IDENTITY = "offline-fact-assertion@cwp-1"
OFFLINE_REVIEW_IDENTITY = "offline-mechanical-echo@crvp-2"
#: 局部返修替身的身份（`cwr-2`）。与上面三个都**不同**：它读的是上一稿 + 逐句硬错，写的是
#: 一份新稿。三个替身共用身份，读回面上「这一稿是谁写的」就分不出来。
OFFLINE_REWORK_IDENTITY = "offline-registration-repair@cwrp-3"

#: 离线返修替身对**每一句点名句**的 typed 去向（闭集）。与 `WITHHELD_REASONS` /
#: `FACT_PLACEMENT_DISPOSITIONS` 同一个用意：替身自己的取舍要逐条可回查。
#:
#: **注意与 `CRW.REWORK_SENTENCE_DISPOSITIONS` 不是同一根轴**，两者取值也不同名：
#: 这里是**替身打算怎么改**（它自己的意图，一份自述）；那边是**点名句在返修稿里落在哪**
#: （从编号台账推出来的对应关系，可被反驳）。把两个词表合并，等于让「它说它改了」充当
#: 「它确实改成了」。读回时两列并排。
REWORK_DISPOSITIONS = ("moved_to_registered_column", "withdrawn")

#: 逐句切分：只在**原文自己的**句读处切，切出来的每一段都是原文的逐字子串。
_SENTENCE_BREAKS = "。！？；\n"
#: 「这一片真的是**一句**」的句末标记。**只**收句末标点，不收 `；` 与换行：
#: `；` 在年报里绝大多数是**列举项之间的分隔**，切在它上面得到的是
#: 「Hyundai、Honda、Volvo、…、小米等；」这样的**片段**。片段不是正文——它没有谓语、
#: 读者读不出任何断言。把它当句子写进正文，就是拿版式碎片冒充业务事实。
#: 换行同理：切在换行上得到的是版式行，不是句子。
_SENTENCE_FINAL_BREAKS = "。！？!?…"
_MIN_SENTENCE_CHARS = 18

#: 抽取式替身在**过滤前**先切出的候选句上限。
#:
#: 替身的句数是**固定预算**（`sentences_per_material`）。它现在要在候选里剔掉那些「只靠
#: 普通材料原文授权金额 / 比率」的句子（`scp-2` 起这类句子必然在资格轴上硬错误），
#: 若先按预算切、再过滤，一份材料的整份预算会被前两句数字句吃光——那会让「过滤」看起来
#: 像「这份材料没内容」。多切一点候选再过滤，替身的产出才如实反映材料里**真的有**的
#: 可持续正文，而不是它自己的切分顺序。
_CANDIDATE_SENTENCES_PER_MATERIAL = 8

#: 被替身**自己**撤下的金额 / 比率句子的去向说明。
#:
#: 这一句是给读者看的：原表已经展示（§0.21 路径 b），正文数字**尚未**取得授权
#: （路径 A 的预验证没做，因为那要触碰冻结 Contract / SourcePolicy）。两者是两件事，
#: 不得合并成「材料里没有这张表」。
WITHHELD_NUMERIC_NOTE = "原表已展示、正文数字尚未授权"

#: `fact_placement.json` 上的一句话：它证明的是**落栏去向**，不是写作质量，也不是覆盖。
#: `placed` 只说明这一条事实被写进了它自己那一栏；`routed_column_cap_reached` 是**本轮**
#: 每栏句数上限，不是归属错——把它读成「这一栏没内容」就是把写作预算当成了覆盖结论。
FACT_PLACEMENT_NOTE = ("本文件记的是**落栏去向**，不是覆盖结论，也不是写作质量："
                       "`placed` 只说明这条事实进了它自己那一栏；`routed_column_cap_reached` "
                       "只是**本轮**每栏句数上限先被同类事实占满，不是这一栏没有内容；"
                       "`fact_not_routed` 要去补的是**呈现路由声明**。")

#: 表单控件**字形**（勾选框 / 对勾 / 空心实心方块）。它是**字形**，不是词表：
#: 只列表单控件本身，刻意**不**收「勾选」「适用」这类汉字——那是句子的成分，删它们就是改写。
#: 年报正文里夹着 `□是 ☑否` / `☑适用 □不适用` 这类模板前缀（r21 现场真实出现过），
#: 它们既不是公司经营事实，也不构成一句可引的正文。
#: 逐项写成显式 `\uXXXX`：私有区字符（Wingdings 对勾）不可见，写字面量会让「表里到底有没有
#: 这一个字形」变成看不出来的事。逐项括号里是该码位对应的字符，供人核对。
_CHECKLIST_GLYPHS = (
    "\u2610",  # 空框
    "\u2611",  # 打勾框
    "\u2612",  # 打叉框
    "\uf052",  # Wingdings 对勾（r21 现场实际出现的是这一个）
    "\u25a1",  # 空心方块
    "\u25a0",  # 实心方块
    "\u25ef",  # 空心圆圈
    "\u2713",  # 细对勾
    "\u2714",  # 粗对勾
    "\u221a",  # 根号形的对勾
    "\u25cb",  # 小空心圆
    "\u25cf",  # 小实心圆
)

#: 撤下原因码（**替身侧**的记账轴，与 `CW.CITED_GAP_REASONS` 是两回事：
#: 前者说「这一片为什么没写出去」，后者说「这一小节为什么没有正文」）。
WITHHELD_REASONS = (
    #: 金额 / 比率只由普通材料原文授权（`scp-2`）——原件已展示，正文数字尚未授权。
    "numeric_not_authorized",
    #: 只由不能表达当前状态的来源角色支撑，且自己没有期间限定（`srsc-1`）。
    "history_source_as_current_state",
    #: 带表单控件字形（勾选 / 模板前缀）。
    "selection_form_surface",
)

#: `OfflineFactAssertionCitedProseClient` 的**去向**闭集：这一份权威事实本轮为什么进了正文、
#: 或者为什么没进。六种取值是**六种不同的事**，不得合并成一句「没写出去」——对下游的含义
#: 完全不同：`fact_not_routed` 要去补路由声明，`routed_column_not_in_request` 是本次请求面
#: 少了一栏，`routed_column_cap_reached` 只是本轮每栏句数上限，`no_column_axis_in_request`
#: 是本支这次根本拿不到任何一栏归属，`diagnostic_slot_not_prose` 则是**按展示角色就不该写进
#: 正文**（`cp-8`）——它不是「没轮上」，而是「栏目对得上也不写」。
FACT_PLACEMENT_DISPOSITIONS = (
    #: 这一份进了正文。
    "placed",
    #: 呈现路由里没有它（`fact_id` 不在 `presentation_routing.fact_columns` 里，或落栏为空）。
    "fact_not_routed",
    #: 路由给了栏目，但本次请求面的小节里没有这一栏。
    "routed_column_not_in_request",
    #: 路由给了栏目、该栏目也在本次请求面里，但每栏句数上限先被别的同类事实占满。
    "routed_column_cap_reached",
    #: 本支这次既没有登记轴也没有路由轴，归属无从判定。
    "no_column_axis_in_request",
    #: 这一份的展示档是 `diagnostic_only`（`cp-8`）：它**只进诊断槽位**，普通正文不得引用，
    #: 因此本轮即使栏目对得上也不写进正文。它与上面四种的区别是：前四种说「为什么没轮上」，
    #: 这一种说的是「**按展示角色就不该写进正文**」——那条数值仍然在诊断表里可读。
    "diagnostic_slot_not_prose",
)

_TEMPLATE_SURFACE_NOTE = ("勾选 / 模板前缀不得冒充公司事实：这一片带表单控件字形，整片撤下"
                          "（替身只删不改，因此不做裁剪）")
_HISTORY_AS_CURRENT_NOTE = ("旧来源不得默认写成当前状态：这一片只由不能表达当前状态的来源"
                            "角色支撑，且自己没有期间限定（没有年份 token、也没有「报告期」），"
                            "整片撤下")


def _piece_refusal(piece: str, material: Mapping[str, Any]) -> "tuple[str, str] | None":
    """这一片按替身**自己**能判的两条纪律应当撤下吗；撤下则返回 `(原因码, 去向说明)`。

    只判两件**不需要判断力**的事，两件都取自既有判据、不另立第二份词表：

      * **表单控件字形**（:data:`_CHECKLIST_GLYPHS`）：勾选符号是表单残留，不是公司事实；
      * **来源角色**：复用 `srsc-1` 的角色闭集（:data:`SRS.CANNOT_ESTABLISH_CURRENT_STATE_ROLES`）
        与期间判据（:func:`SRS.has_period_qualification`）——只由历史 / 冲突来源支撑、且自己
        没有期间限定的句子，读者只能读成持续至今的当前状态。

    刻意**不**在这里做「相关性判断」：替身没有语义判断力，相关性由上面按登记栏目选材那一层
    承担，而那一层只用 Pack 已登记的 `aspect_ids`，不是替身自己猜的。
    """
    text = str(piece or "")
    if any(glyph in text for glyph in _CHECKLIST_GLYPHS):
        return ("selection_form_surface", _TEMPLATE_SURFACE_NOTE)
    role = str(material.get("source_role") or "")
    if role in SRS.CANNOT_ESTABLISH_CURRENT_STATE_ROLES \
            and not SRS.has_period_qualification(text):
        return ("history_source_as_current_state", _HISTORY_AS_CURRENT_NOTE)
    return None


def _fact_row_id(fact: Mapping[str, Any]) -> str:
    """这一行权威事实在**它自己那种权威**下的身份字段值。

    请求面（:func:`sections.cited_writer.build_cited_prose_request`）逐行用
    `NS.FACT_FIELD_BY_AUTHORITY_KIND` 里该 `authority_kind` **自己的**字段名承载 id：
    `topic_pack` → `fact_id`、`financial_pack` → `financial_fact_id`、
    `evidence_note` → `note_fact_id`、`external_snapshot` → `external_fact_id`。
    因此这里**不能**写死 `fact["fact_id"]`（财务节会当场 KeyError）。

    呈现路由 `presentation_routing.fact_columns[].fact_id` 用的是**路由自己的**字段名
    `fact_id`，它承载的值与本函数取到的值是同一批 id——两处字段名不同，值域相同。
    """
    field = NS.FACT_FIELD_BY_AUTHORITY_KIND.get(str(fact.get("authority_kind") or ""), "")
    return str(fact.get(field) or "") if field else ""


def _routing_gap_note(*, declared: Sequence[str], routing: Mapping[str, Any]) -> str:
    """这条缺口在**呈现路由**上的去向说明；本支没有路由轴、或本小节覆盖的栏目里没有一条
    在路由上登记过缺口时返回空串。

    `presentation_routing.column_gaps` 是路由**自己**已经登记的「这一栏本轮没有可呈现的
    指标」声明。把它照抄进缺口文案有两个作用：一是让读者看出「这一栏空着」是呈现层的
    结论而不是正文写作的失误；二是把路由给出的 typed 理由（如
    `no_registered_metric_in_selected_facts`）原样带出来，不在这里另造一套词。
    """
    if not routing:
        return ""
    wanted = {str(a) for a in declared if str(a)}
    notes: list[str] = []
    for row in routing.get("column_gaps") or ():
        if str(row.get("column") or "") not in wanted:
            continue
        reason = str(row.get("reason") or "")
        tier = str(row.get("contract_display_tier") or "")
        note = (f"呈现路由（`{routing.get('routing_version') or '-'}`）已就本栏 "
                f"`{row.get('column')}` 登记：没有可用指标（`{reason}`）")
        if tier:
            note += f"，Contract 展示档位 `{tier}`"
        notes.append(note)
    return "　｜　".join(notes)


def _subsection_gap(*, declared_ids: Sequence[str], usable: Sequence[Any],
                    registered: Sequence[Any], picked: Sequence[Any],
                    reasons: Sequence[str], duplicates: int,
                    notes: Sequence[str] = (), noun: str = "材料",
                    registration_axis: str = "aspect_ids",
                    unusable_axis_note: str = "") -> "dict[str, str]":
    """本小节没有正文时的 typed 缺口（原因码只取 `CW.CITED_GAP_REASONS` 闭集，不新造词）。

    五种情形的**去向各不相同**，因此逐条分开写，不合并成一句「没有材料」：

      * 清单里没有任何可叙述来源 → `no_source_in_manifest`；
      * 有来源，但**没有一条**被登记归属到本栏目 → 仍是 `no_source_in_manifest`，但说明必须
        写成「本轮未取得 / 未获支持」，**不得**写成「上传语料里不存在该内容」——登记归属为空
        说的是「本轮没有来源在这一栏上被认领」，语料里有没有该内容不由替身回答；
      * 本栏目有来源，但本轮已被更早的小节写走 → `source_present_but_not_admissible`；
      * 切出来的候选片**全部**被替身自己撤下（数字未授权 / 历史来源当前化 / 表单字形）→
        `source_present_but_not_admissible`，并把实际撤下原因码逐个列出来；
      * 剩下一种：来源在场，但切不出可引的完整句 → `manifest_partial_for_requirement`。

    `notes` 是各撤下原因自己的**去向说明**（与 `reasons` 同序、可重复）。它只影响文案，
    不影响原因码：缺口必须能回答「东西去哪儿了」，不得让读者把它读成「材料里没有」。

    `noun` 只影响人读措辞：`OfflineExtractiveCitedProseClient` 的来源是**材料**，
    `OfflineFactAssertionCitedProseClient` 的来源是**权威事实**。判据与原因码完全同一份。

    `registration_axis` 只影响文案：本支这次按**哪一条**轴判「这份东西属于哪一栏」
    （`aspect_ids` 登记归属 / `presentation_routing.fact_columns` 呈现路由）。说错轴名会让
    读者去改错地方——「路由里没有这一栏」与「登记归属里没有这一栏」是两件要分别修的事。
    `unusable_axis_note` 是路由自己的去向说明（见 :func:`_routing_gap_note`）。
    """
    if not usable:
        return {"reason": "no_source_in_manifest",
                "detail": f"本次精确清单里没有任何可作叙述正文的{noun}"}
    if not registered:
        label = " / ".join(f"`{a}`" for a in declared_ids)
        where = (f"（{label} 都不在任何{noun}的 `{registration_axis}` 里）"
                 if registration_axis else "")
        detail = (f"本轮精确清单里**没有任何{noun}被登记归属到本栏目**{where}："
                  "这是「本轮未取得 / 未获支持」，**不是**「上传语料里不存在该内容」"
                  f"——归属为空说的是本轮没有{noun}在这一栏上被认领，"
                  "语料里有没有该内容不由本替身回答")
        if str(unusable_axis_note).strip():
            detail += f"　｜　{unusable_axis_note}"
        return {"reason": "no_source_in_manifest", "detail": detail}
    if not picked:
        return {"reason": "source_present_but_not_admissible",
                "detail": (f"本栏目有{noun}，但本轮已被更早的小节写走"
                           f"（一份{noun}的正文本轮只写一次；重复句不算有效正文）")}
    if reasons:
        detail = ("本栏目切出的候选片**全部**被替身自己撤下，撤下原因码："
                  + "、".join(dict.fromkeys(str(r) for r in reasons)))
        # 逐条带上**该原因自己的去向说明**：缺口文案必须能回答「东西去哪儿了」，
        # 否则读者只能把它读成「材料里没有」——那正是本批要堵的误读。
        for note in dict.fromkeys(str(n) for n in notes if str(n).strip()):
            detail += f"　｜　{note}"
        return {"reason": "source_present_but_not_admissible", "detail": detail}
    if duplicates:
        return {"reason": "source_present_but_not_admissible",
                "detail": ("本栏目切出的候选片与已经写出的正文重复（整片撤下）；"
                           "重复句不算有效正文")}
    return {"reason": "manifest_partial_for_requirement",
            "detail": f"清单里有{noun}，但没有任何一份切得出可引的完整句"}

#: 人工同义抽检的句子数下限（本批指令：至少 10 句）。
HUMAN_SPOT_CHECK_MIN = 10

#: §0.21 **路径 b**（原 PDF 表区只读展示）的运行级来源记录。
#:
#: 它是**数据**（本次选了哪些页与区域），不是生产规则：`sections/source_table_display.py`
#: 里没有任何公司名、证券代码或固定页码的判断分支。原文件哈希**不**写在这个文件里，
#: 由本次已登记的电子 PDF 来源清单在运行时给出并实测比对。
SOURCE_DISPLAY_RECORD_PATH = (REPO / "templates" / "source_display"
                              / "m930_3_source_table_display_v1.yaml")

#: 「一页」的文件名：两条可读路径 + 财务权威表 + 缺口 + 审阅意见 + 状态分列。
DEMO_PAGE_FILENAME = "demo_page.md"

#: 本链两个真实客户端**显式**关闭推理。项目模型是推理模型，推理内容计入 `output_tokens`，
#: 而本链要的是「结构化 JSON 正文」这一类输出：推理一旦把 `max_tokens` 吃光，正文一个字符
#: 都还没开始写，产物上却只表现为「这次调用失败了」。
#: 只写在这里、只给这条链用——`llm.client` 的缺省仍是 `None`（不传、由 provider 默认），
#: 改那个缺省会一次性改掉全仓每一个调用点的行为，而它们各自的短输出处置方式并不相同。
CITED_THINKING_DISABLED: dict = {"type": "disabled"}


def _model_backed_review(version) -> bool:
    """这次审阅**是不是模型跑的**——只读 ``CitedReportVersion.review_producer_kind``。

    它只回答「谁审的」，**不**回答「审得对不对」「算不算通过」。两个取值是闭集
    （``sections.cited_review.CITED_REVIEW_PRODUCER_KINDS``）：`independent_llm_review` 是
    模型独立只读语义审阅，`offline_diagnostic_echo` 是离线替身回声。把前者写成后者（或反过来）
    会让读者把「替身的回声」读成「独立审阅通过」——本批 R9 修的就是这一类自相矛盾。

    `version` 为 `None`（这一节还没有版本）时返回 `False`：没有审阅就不是模型审阅。
    """
    return str(getattr(version, "review_producer_kind", "") or "") == "independent_llm_review"


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
                    encoding="utf-8")


def _write_json_atomic(path: Path, payload: object) -> None:
    """先写同目录临时文件再 `os.replace` 换名：读者看到的要么是旧内容，要么是完整新内容。

    只给**调用账本**用。它存在的理由正是「这一轮失败了」——而一条写到一半的账本，恰恰在最
    需要它的时候不可读；非原子写在这里不是风格问题，是把唯一的失败证据变成一次赌博。
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 只读库里的事实：可声明的真实主体（本脚本不是报告输入声明路径）
# ---------------------------------------------------------------------------

def _current_snapshot_subjects(db: Path) -> list[str]:
    """只读列出财务库里**有 current 快照**的主体。

    本脚本不是「报告输入声明」路径（那条路径要求显式声明、库只做核对）。这里做的是**读回
    工具**要挑一个库里真实存在的主体来声明，与 `evals/test_m930_3_real_assembly_smoke.py`
    同一约定：能在代码里写死的只有「读哪张表」，不能写死公司名。
    """
    conn = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT company_id FROM current_snapshot ORDER BY company_id").fetchall()
    finally:
        conn.close()
    return [str(r[0]) for r in rows]


def _registered_company_name(db: Path, company_id: str) -> str | None:
    """只读回该主体**权威自己登记过**的唯一名称；没有唯一名称时返回 `None`（不猜）。"""
    conn = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT declared_company_name FROM financial_source_document "
            "WHERE company_id = ? AND subject_match_status = 'matched' "
            "AND declared_company_name IS NOT NULL AND TRIM(declared_company_name) <> ''",
            (company_id,)).fetchall()
    finally:
        conn.close()
    names = {str(r[0]).strip() for r in rows}
    return names.pop() if len(names) == 1 else None


class _NetworkGuard:
    """读回期间禁止任何 provider 调用：被碰到就抛错，而不是静默联网。

    离线模式的替身本来就不发请求；这条 guard 的作用是让「没发请求」不依赖假设——真有人
    在链路上偷偷调 provider，这里会当场失败。
    """

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._saved: dict[str, object] = {}

    def __enter__(self) -> "_NetworkGuard":
        def _blocked(name: str):
            def _fail(*a, **kw):
                self.attempts.append(name)
                raise AssertionError(
                    f"本脚本不得联网，却发生了 provider 调用（{name}）："
                    "离线读回必须无网络；需要真实调用时另开一次授权 run。")
            return _fail

        for name in ("chat_with_usage", "chat", "get_client"):
            self._saved[name] = getattr(LLC, name, None)
            setattr(LLC, name, _blocked(name))
        return self

    def __exit__(self, *exc) -> None:
        for name, saved in self._saved.items():
            if saved is None:
                delattr(LLC, name)
            else:
                setattr(LLC, name, saved)


# ---------------------------------------------------------------------------
# 第一部分：图侧逐表完整证明矩阵（**真实文档**，无替身）
# ---------------------------------------------------------------------------

def _member_lives(*, company_id: str, entries, resolver=None) -> dict:
    """按**有序源集**逐份成员签发 live 快照（`lts-2` 之后的公开入口）。

    `RealEnvironment._build` 内部为同一批成员也签发过一次；这里是**同入口、同输入的第二次
    签发**，用来覆盖**整份文档**的每一张表（而不只是被消费的那些）。两次签发产生的是同一
    份确定性读数；本文件里两者的读数不混用，且这一点在 `readback.md` 里逐字写明。

    登记 sha256 与磁盘实读不一致即拒绝：读一份「按清单选了、却和真正被解析的不是同一份」
    的 PDF 没有意义。

    `resolver` 是本 run 的**上传输入解析器**（`--run-input`）。给出时，「去哪读这一份 PDF」
    由它**按 `(document_id, sha256)`** 回答，**永不**回落到 `member.source_path`——那条回落
    正是「页面上传了三份、链去读了另外三份」的来源。`resolver` 为 `None` 时才用登记路径，
    那是不带 `--run-input` 的历史兼容运行。
    """
    lives: dict[str, object] = {}
    for member in entries:
        doc_id = str(member.document_id)
        if resolver is not None:
            pdf = resolver.resolve(document_id=doc_id,
                                   file_sha256=str(member.file_sha256))
        else:
            pdf = Path(str(member.source_path))
            if not pdf.exists():
                raise SystemExit(
                    f"源集成员 {doc_id} 的 source_path 不可达：{member.source_path!r}")
        sha = ACC._sha256_file(pdf)
        if sha != str(member.file_sha256):
            raise SystemExit(
                f"源集成员 {doc_id} 的磁盘内容与登记 sha256 不一致："
                f"登记 {member.file_sha256} / 实读 {sha}（fail-closed）")
        if doc_id in lives:
            raise SystemExit(
                f"源集里存在同 id 的多份成员（{doc_id}）：本读回不做「就近取一份」，"
                "请按四轴分别读回")
        lives[doc_id] = LSS.build_live_verified_span_snapshot(LSS.LiveSpanBuildRequest(
            company_id=company_id, document_id=doc_id,
            document_version=str(member.document_version),
            raw_pdf_path=str(pdf), raw_pdf_sha256=sha,
            expected_current_evidence_set_version=str(member.evidence_set_version)))
    return lives


def _table_matrix_for(member_live, entry) -> dict:
    """一份文档的逐表矩阵：同一图侧来源上的逐表证明 + 与快照成员数的对账。

    枚举用 `snapshot_tables`（**已构造**对象，`lts-2` 的读回访问器），不是 `tables`
    （后者只在文档级资格已取得时可取）。理由是 §0.19 的原话：逐表证明要能读**未取得文档级
    资格**状态下的每一张已构造表，否则「这张表自己账不平」这一类判据根本读不到，逐表证明
    就只覆盖了已经过门的表——那等于没做。
    """
    source = LTS.build_live_table_source(member_live)
    base = {
        "document_id": str(entry.document_id),
        "document_version": str(entry.document_version),
        "evidence_set_version": str(getattr(entry, "evidence_set_version", "") or ""),
    }
    try:
        batch = GTR.release_graph_tables(source)
    except Exception as exc:                       # noqa: BLE001 - 读数即异常文本
        # 图侧正式入口按设计会 fail-closed 抛错（输入不是同次签发来源 / 记账不平 / 身份不符）。
        # 抛错本身就是一条读数，逐字带出，而不是让整次读回没有产物。
        base.update({
            "batch_state": "release_raised",
            "release_error_type": type(exc).__name__,
            "release_error": str(exc),
            "declared_table_count": None, "released_table_count": None,
            "refused_table_count": None, "accounting_balanced": None,
            "document_refusal": None, "tables": [],
        })
        return base

    by_table: dict[str, dict] = {}
    for record in batch.get("released") or ():
        by_table[str(record.get("table_id"))] = {"released": True, "record": record}
    for record in batch.get("refusals") or ():
        by_table[str(record.get("table_id"))] = {"released": False, "record": record}

    tables: list[dict] = []
    for table in (source.snapshot_tables or ()):
        item = by_table.get(str(table.table_id))
        record = (item or {}).get("record") or {}
        released = bool(item and item["released"])
        reasons = list(record.get("problems") or ())
        qualification = record.get("content_qualification") or {}
        # 放行记录只接受零缺陷证明（`_release_record` 当场校验），因此放行面的逐表结论由
        # 构造保证为「通过」；拒绝面才带 `local_proof`。两处读的是同一个 `tlp-1`。
        local_proof = record.get("local_proof")
        tables.append({
            "table_id": str(table.table_id),
            "table_locator": str(getattr(table, "table_locator", "") or ""),
            "page_number": getattr(table, "page_number", None),
            "structure_kind": str(getattr(table, "structure_kind", "") or ""),
            "structure_class": str(getattr(table, "structure_class", "") or ""),
            "structure_state": str(getattr(table, "structure_state", "") or ""),
            "structure_state_reason": str(getattr(table, "structure_state_reason", "") or ""),
            "header_absence_reason": str(getattr(table, "header_absence_reason", "") or ""),
            "title_block_count": len(getattr(table, "title_blocks", ()) or ()),
            "column_count": getattr(table, "column_count", None),
            "row_count": len(getattr(table, "rows", ()) or ()),
            "reading_qualified": released,
            #: 阅读资格与数字权威是**两根轴**：`reading_qualified` 只说明「这张表能被读」，
            #: 它**不**把任何数字升格为权威（`gto-3` 的 `numeric_authority` 恒 false）。
            "numeric_authority": bool(qualification.get("numeric_authority")),
            "content_qualification": dict(qualification),
            "local_proof": ({
                "locally_proven": local_proof.get("locally_proven"),
                "defects": list(local_proof.get("defects") or ()),
                "steps": dict(local_proof.get("steps") or {}),
            } if local_proof else {
                "locally_proven": True if released else None,
                "defects": [],
                "steps": {"note": ("放行记录只接受零缺陷证明，构造期已校验"
                                   if released else
                                   "本张表在图侧来源上没有任何记录（fail-closed）")},
            }),
            "primary_reason": (str(record.get("reason") or "") if record else
                               "（这张表在本批图侧来源上没有任何记录）"),
            "defect_codes": reasons,
            "cell_count": record.get("cell_count"),
            "citable_cell_count": record.get("citable_cell_count"),
            "unproven_cell_count": record.get("unproven_cell_count"),
            "not_citable_cell_count": record.get("not_citable_cell_count"),
            "unsupported_columns": list(record.get("unsupported_columns") or ()),
            "gap_basis": record.get("gap_basis"),
            "column_support": list(record.get("column_support") or ()),
        })
    base.update({
        "batch_state": str(batch.get("batch_state") or ""),
        "declared_table_count": batch.get("declared_table_count"),
        "released_table_count": batch.get("released_table_count"),
        "refused_table_count": batch.get("refused_table_count"),
        "accounting_balanced": batch.get("accounting_balanced"),
        "document_refusal": batch.get("document_refusal"),
        "document_refusal_kind": batch.get("document_refusal_kind"),
        "tables": tables,
    })
    return base


def build_table_proof_matrix(member_lives: dict, entries) -> dict:
    """逐份冻结演示文档的**真实**逐表读数。"""
    documents = []
    for entry in entries:
        member_live = member_lives.get(str(entry.document_id))
        if member_live is None:
            documents.append({
                "document_id": str(entry.document_id),
                "document_version": str(entry.document_version),
                "unavailable_reason": "本次读回没有该成员的 live 快照（fail-closed：不猜）",
                "tables": []})
            continue
        documents.append(_table_matrix_for(member_live, entry))
    return {
        "rule_version": GTR.GRAPH_TABLE_RELEASE_RULE_VERSION,
        "schema_version": GTR.GRAPH_TABLE_RELEASE_SCHEMA_VERSION,
        "proof_schema_version": TLP.TABLE_LOCAL_PROOF_SCHEMA_VERSION,
        "livelihood_note": (
            "本文件读的是**真实 PDF + 真实只读 evidence 库**："
            "`LiveVerifiedSpanSource` → `build_live_table_source` → `release_graph_tables`。"
            "逐份成员的 live 快照由本脚本按同一份成员身份**再次签发**"
            "（与 `RealEnvironment` 内部那次同入口、同输入）。"),
        "reading_vs_authority_note": (
            "`reading_qualified=true` 只表示「这张表能被当材料读」，**不**把它的数字升格为"
            "数字权威（`numeric_authority` 是另一根轴，逐条恒 false）。"),
        "documents": documents,
    }


# ---------------------------------------------------------------------------
# 第二部分：新写作链（离线替身）
# ---------------------------------------------------------------------------

def _split_verbatim(text: str, *, limit: int, min_chars: int = _MIN_SENTENCE_CHARS) -> list[str]:
    """把一段原文按**它自己的**句读切成若干段，只保留**真的是句子**的那些。

    每一段都是原文的**逐字子串**（只在边界去掉空白）。离线替身由此可以完全不改写原文，
    这也是它能被逐句硬核对通过的原因——不是因为核对松，而是因为它一个字都没动。

    两个过滤条件都写在**切分**这一步，因为它们判的是「这一片算不算一句」，不是「这一句
    写得好不好」：

      * **必须是句末标点结尾**（:data:`_SENTENCE_FINAL_BREAKS`）。以 `；` 或换行结尾的片
        是列举项与版式行，不是句子——它们进正文会让读者读到一串没有谓语的名单；
      * 长度下限（原样保留）。

    **不做**改写、不补主语、不接标点：替身只删不改。因此「切不出句子」在它这里的结果是
    **少写**（并在缺口里如实记账），不是**硬凑一句**。
    """
    out: list[str] = []
    buf = ""
    for ch in str(text or ""):
        buf += ch
        if ch in _SENTENCE_BREAKS:
            piece = buf.strip()
            buf = ""
            if len(piece) >= min_chars and piece[-1] in _SENTENCE_FINAL_BREAKS:
                out.append(piece)
                if len(out) >= limit:
                    return out
    tail = buf.strip()
    if tail and len(tail) >= min_chars and len(out) < limit \
            and tail[-1] in _SENTENCE_FINAL_BREAKS:
        out.append(tail)
    return out


#: 归一后用于**去重**的键：只删空白。标点、数字、一个字都不动——去重判的是「这一串是不是
#: 已经写过」，不是「意思是不是差不多」。相似度判重会把两件不同的业务事实合成一件。
_WS_RE = re.compile(r"\s+")


def _dedup_norm(text: str) -> str:
    return _WS_RE.sub("", str(text or ""))


def _is_repeat(candidate: str, emitted: list[str]) -> bool:
    """这一片与已经写出去的句子**互相包含**吗（相等、被包含、或包含别人）。

    三个方向都算重复，因为三个方向读者看到的都是**同一串字**：

      * 相等：同一句话写了两次；
      * 被已写句包含：相邻两个 span 的读视图重叠，后一片是前一片的尾巴；
      * 包含已写句：后一片把前一片整个包了进去（更长的滑窗）。这一种**丢的是信息**，
        但保留两句会让读者读到同一段话的两遍——两害相权，重复更坏，且丢的那一点由
        「这一份材料在本小节只出一次」这条既有纪律（`_next_material` 的 `used` 集合）
        另行兜住。

    判据是**字面**的（仅归一空白），不做相似度：相似度会把「营收 100 亿」与「营收 200 亿」
    判成重复，那是删事实，不是去重。
    """
    norm = _dedup_norm(candidate)
    if not norm:
        return True
    for previous in emitted:
        other = _dedup_norm(previous)
        if not other:
            continue
        if norm in other or other in norm:
            return True
    return False


def _unapproved_numeric_surfaces(piece: str) -> tuple[str, ...]:
    """这一段原文里**只靠它自己**授权不了的金额 / 比率表面（`scp-2`）。

    抽取式替身只会给句子挂**材料**引用（`m*`），材料正是「普通材料」那一类来源。因此在
    替身这里，凡是 :func:`SC.is_qualified_numeric_surface` 判为金额 / 比率的表面，都拿不到
    `qualified_fact` 或 `table_cell_source`——它必然在资格轴上硬错误。替身不假装自己拿到了
    格级或事实级授权：它把整段撤下来，并把撤下的原因如实记账。

    判据**复用** `sentence_check` 的那一个函数，不在这里抄第二份——两处各写一份，
    「替身撤了什么」与「核对器拒了什么」迟早对不上。
    """
    return tuple(token for token in NS.scan_numeric_tokens(str(piece or ""))
                 if SC.is_qualified_numeric_surface(token, str(piece or "")))


class OfflineExtractiveCitedProseClient:
    """离线**抽取式**写作替身：只读模型会看到的那一份请求面，按句切原文、逐句挂引用。

    它**不**改写、**不**润色、**不**推理、**不**计算。因此它能证明的只有一件事：这条链的
    输入、引用键、逐句核对、审阅与预览是接得起来的。它**不能**用来评价写作质量，更不能被
    读成「真实模型正文」。

    三处**同源**的纪律（都取自 §0.20 / §0.21 与既有判据，不在这里另立第二份词表）：

    * **选材按登记栏目**：本小节声明的 Contract 栏目必须出现在材料的 `aspect_ids`（Pack 侧
      `ResearchMaterialDisposition.aspect_ids`）里。替身**没有**语义判断力，因此它不假装能判
      「相关性」；但它也**不**再按清单顺序摊派——摊派正是旧读数里把法律风险材料写进「供应商
      集中度」、把无关段落写进别栏目的原因；
    * **撤下而不是硬写**：判据复用 `SC.is_qualified_numeric_surface`（与逐句核对器同一份词表）
      与 `srsc-1` 的角色/期间闭集（`:func:`_piece_refusal``）；撤下的句子进 :attr:`withheld`，
      每条带**原因码**与去向说明；
    * **重复不算正文**：一份材料整轮只写一次，同一片文字被两边覆盖时整片撤下。

    记住它**不是**什么：这份替身写出来的句子与所引原文逐字相同，这是它能过逐句硬核对的原因
    ——不是核对松，而是它一个字都没动。它因此**不**能用来回答「真实写作会不会写得好」。
    """

    def __init__(self, *, materials_per_subsection: int = 2,
                 sentences_per_material: int = 2) -> None:
        self.materials_per_subsection = int(materials_per_subsection)
        self.sentences_per_material = int(sentences_per_material)
        self.calls: list[dict] = []
        #: 被本替身自己撤下的句子（逐条带小节、材料键、**原因码**、表面与去向说明）。
        self.withheld: list[dict] = []

    def compose(self, *, messages, system: str, prompt_version: str,
                model_policy: str) -> CW.CitedProseResult:
        payload = json.loads(str(messages[0]["content"]))
        usable = [m for m in payload["materials"]
                  if not m.get("is_table")
                  and str(m.get("content_kind") or "") not in SC.NON_NARRATIVE_CONTENT_KINDS
                  and len(str(m.get("text") or "")) >= _MIN_SENTENCE_CHARS]

        subsections: list[dict] = []
        gaps: list[dict] = []
        sentence_seq = 0
        withheld_here = 0
        skipped_as_repeat = 0
        #: 已经写出去过的材料键。一份材料在**整轮**里只写一次：它可能同时被登记到多个栏目，
        #: 但同一段话在正文里出现两遍，读者读到的是重复，不是两栏内容。
        used: set[str] = set()
        #: 已经写出去过的句子（**整轮**，不只本小节）。相邻 span 的读视图会重叠，跨材料也会。
        emitted: list[str] = []

        for spec in payload["subsections"]:
            declared = tuple(str(a).strip() for a in (spec.get("declared_aspect_ids") or ())
                             if str(a).strip())
            if not declared:
                raise AssertionError(
                    f"请求面第 {spec.get('subsection_id')!r} 小节没有 `declared_aspect_ids`："
                    "本替身按登记归属选材，没有声明栏目就选不出材料——"
                    "静默退回「按清单顺序摊派」正是本批要修掉的那件事，因此这里当场抛")
            # 选材**只**看登记归属（Pack 已登记的 `aspect_ids`），不看位置、不看顺序。
            # `cwm-6`：小节声明是一个**集合**（冻结 WritingSpec 把 18 个 `company_business*`
            # aspect 映到同一个 `co-h4`），所以判据是**有交集**，不是单值相等。
            declared_set = set(declared)
            registered = [m for m in usable
                          if declared_set & set(m.get("aspect_ids") or ())]
            picked: list[dict] = []
            for material in registered:
                if len(picked) >= self.materials_per_subsection:
                    break
                if material["key"] in used:
                    continue
                used.add(material["key"])
                picked.append(material)

            paragraphs: list[dict] = []
            reasons_here: list[str] = []
            notes_here: list[str] = []
            duplicates_here = 0
            for material in picked:
                pieces = _split_verbatim(
                    material["text"], limit=_CANDIDATE_SENTENCES_PER_MATERIAL)
                sentences = []
                for piece in pieces:
                    if len(sentences) >= self.sentences_per_material:
                        break
                    refusal = _piece_refusal(piece, material)
                    if refusal is not None:
                        reason, note = refusal
                        withheld_here += 1
                        reasons_here.append(reason)
                        notes_here.append(note)
                        self.withheld.append({
                            "subsection_id": spec["subsection_id"],
                            "material_key": material["key"],
                            "reason": reason, "surfaces": [], "note": note})
                        continue
                    missing = _unapproved_numeric_surfaces(piece)
                    if missing:
                        withheld_here += 1
                        reasons_here.append("numeric_not_authorized")
                        notes_here.append(WITHHELD_NUMERIC_NOTE)
                        self.withheld.append({
                            "subsection_id": spec["subsection_id"],
                            "material_key": material["key"],
                            "reason": "numeric_not_authorized",
                            "surfaces": list(missing),
                            "note": WITHHELD_NUMERIC_NOTE})
                        continue
                    if _is_repeat(piece, emitted):
                        duplicates_here += 1
                        skipped_as_repeat += 1
                        continue
                    sentence_seq += 1
                    emitted.append(piece)
                    sentences.append({"sentence_id": f"s{sentence_seq:04d}", "text": piece,
                                      "citations": [material["key"]]})
                if sentences:
                    #: 段自己声明服务哪几栏（`cw-4`）：本段所引材料在**本小节声明集合内**
                    #: 的那部分登记归属。它比小节集合窄——「这一段本来要写哪几栏」是可判的。
                    paragraphs.append({"paragraph_id": f"p{len(paragraphs) + 1}",
                                       "aspect_ids": sorted(
                                           declared_set & set(material.get("aspect_ids") or ())),
                                       "sentences": sentences})
            if not paragraphs:
                gaps.append({"subsection_id": spec["subsection_id"],
                             "requirement_text": spec["requirement_text"],
                             **_subsection_gap(
                                 declared_ids=declared, usable=usable, registered=registered,
                                 picked=picked, reasons=reasons_here, notes=notes_here,
                                 duplicates=duplicates_here)})
            subsections.append({"subsection_id": spec["subsection_id"],
                                "title": spec["title"], "paragraphs": paragraphs})

        text = json.dumps({"subsections": subsections, "gaps": gaps},
                          ensure_ascii=False, sort_keys=True)
        self.calls.append({"call_id": OFFLINE_WRITER_IDENTITY,
                           "model": "offline-extractive", "prompt_version": prompt_version,
                           "status": "ok", "model_policy": model_policy,
                           "sentence_seq": sentence_seq,
                           "withheld_sentences": withheld_here,
                           "skipped_as_repeat": skipped_as_repeat})
        return CW.CitedProseResult(text=text, call_id=OFFLINE_WRITER_IDENTITY,
                                   model="offline-extractive", prompt_version=prompt_version,
                                   status="ok")


class OfflineFactAssertionCitedProseClient:
    """离线**事实直述**写作替身：把权威事实自己的命题文本逐条写成句子，逐句挂事实引用。

    它只服务**没有 Pack 材料边界**的那一支权威（财务 artifact / 附注 / 外部快照）：那一支的
    正文只能由权威事实支撑，不存在可摘录的材料正文，抽取式替身在那支上必然一句都写不出。
    它与 `OfflineExtractiveCitedProseClient` 同一纪律：**不**改写、**不**润色、**不**推理、
    **不**计算——句子是权威表面文本（`NS.authoritative_fact_surface`）的逐字复制（含期间、
    单位与 `CALCULATED_PROXY` 的口径限定语）。因此它证明的仍然只有链路能不能接上，**不能**
    当作「真实写作质量」或「可读分析」，更不能据此宣称财务节内容门通过。

    事实到小节的分配**只**看「这份事实属于哪一栏」这条轴，两条并列、按优先级取：

      1. 事实自己的登记归属 `aspect_ids`（`CitedFactEntry.aspect_ids`，由 Contract 的栏目声明
         给出）——公司 / 行业侧走这条；
      2. **呈现层路由** `presentation_routing.fact_columns` 的 `fact_id → presentation_column`
         ——财务 / 附注侧 `aspect_ids` 恒为空（见 `pack_writer.scan_financial`），这一支按
         路由落栏。

    替身**没有**语义判断力，因此它不假装能判相关性；但它也**不**再在「没有登记轴」时按清单
    顺序轮转——轮转正是旧读数里「流动比率写进净资产水平」这一类错栏的入口：事实本身正确、
    引用也在场，但它不属于这一栏。**没有路由的事实不会被塞进任何一栏**，它们留 typed 去向
    （`FACT_PLACEMENT_DISPOSITIONS`，逐条记进调用账本）。

    一份事实在**整轮**里只写一次（它可能同时被登记到多个栏目，但同一句话写两遍对读者就是
    重复）。
    """

    def __init__(self, *, facts_per_subsection: int = 3) -> None:
        self.facts_per_subsection = int(facts_per_subsection)
        self.calls: list[dict] = []
        #: 本轮**每一条**权威事实的 typed 去向（闭集见 :data:`FACT_PLACEMENT_DISPOSITIONS`）。
        #: 与 `OfflineExtractiveCitedProseClient.withheld` 同一个用意：替身自己做的取舍必须
        #: 有一条**可逐条回查**的记录，而不是只存在于读回正文里。落盘见 `fact_placement.json`。
        self.placement_dispositions: list[dict] = []

    def compose(self, *, messages, system: str, prompt_version: str,
                model_policy: str) -> CW.CitedProseResult:
        payload = json.loads(str(messages[0]["content"]))
        facts = [f for f in payload.get("authority_facts") or ()
                 if str(f.get("text") or "").strip()]
        # 这次输入里**有没有**「哪份事实属于哪一栏」这条轴，逐条判，两条轴并列：
        #
        #   * `authority_facts[].aspect_ids`——Pack/权威侧的**登记归属**（财务支恒为空，
        #     见 `pack_writer.scan_financial`）；
        #   * `presentation_routing.fact_columns`——**呈现层**按 `fact_id` 逐条声明的
        #     事实应属栏目（`fpr-2`）。
        #
        # 两条轴都只回答「这份事实属于哪一栏」，都**不**回答「这句话回答了这一栏」——替身
        # 没有语义判断力，相关性由真实模型承担。两条轴都没有时才退回确定性轮转。
        #
        # **不再**在「有路由轴、只是没有登记轴」时轮转：轮转正是「流动比率写进净资产水平」
        # 这一类错栏的入口（§0.20 逐句错栏硬核对会如实判红）。没有路由的事实**不得**被塞进
        # 任何一栏，它们留 typed 去向（见本方法末尾的 `placement_dispositions`）。
        has_column_axis = any(tuple(f.get("aspect_ids") or ()) for f in facts)
        #: 展示档（`cp-8`）：请求面把「只进诊断槽位」的行逐行标出，替身据它把这些事实**挡在
        #: 正文之外**。这一步不是提示词里的劝告，是替身自己的取material判决——判据取自请求面
        #: 的取值，不重新解析 `routes`（同一份实现见 `CW.presentation_column_tiers`）。
        diagnostic_keys = set(payload.get("diagnostic_slot_fact_keys") or ())
        diagnostic_keys |= {str(f.get("key") or "") for f in facts
                            if str(f.get("display_tier") or "") == "diagnostic_only"}
        #: **已知未对齐项**（`ndc-4`，见交付 §未解决事项）：请求面本版多了一个
        #: `numeric_authorization.withheld`（本版不可写成正文数字的事实键），**替身本批不读它**。
        #: 原因不是遗漏：本替身把「没写出去」的六种情形收在闭集
        #: `FACT_PLACEMENT_DISPOSITIONS` 里，而「这条事实本版不可写数字」既不是
        #: `diagnostic_slot_not_prose`（那是**展示角色**轴，判据完全不同），也不在闭集内——
        #: 给它硬套一个既有值就是把两根轴混记，加一个值则是 `fact_placement.json` 的 wire 变更
        #: （要另开一版、另走一次授权），两样都超出本批「请求面与逐句核对对齐」的范围。
        #: 本项**当前不可达**：撤下判定只对**声明了逐值身份**的事实生效，而这类事实只出现在
        #: 业务节清单里，业务节恒有材料 ⇒ 走的是**抽取式**替身（不写事实句）。真正跑通它需要
        #: 一次真实运行（那正是本批申请的那次）。
        routing = payload.get("presentation_routing") or {}
        routed_column = {str(row.get("fact_id") or ""): str(row.get("presentation_column") or "")
                         for row in routing.get("fact_columns") or ()
                         if str(row.get("fact_id") or "")}
        has_routing_axis = bool(routed_column)
        subsections: list[dict] = []
        gaps: list[dict] = []
        sentence_seq = 0
        used: set[str] = set()
        rotation = 0
        for spec in payload["subsections"]:
            declared = tuple(str(a).strip() for a in (spec.get("declared_aspect_ids") or ())
                             if str(a).strip())
            if not declared:
                raise AssertionError(
                    f"请求面第 {spec.get('subsection_id')!r} 小节没有 `declared_aspect_ids`："
                    "本替身按栏目归属选事实，没有声明栏目就选不出事实——"
                    "静默退回「按清单顺序轮转」正是本批要修掉的那件事，因此这里当场抛")
            declared_set = set(declared)
            #: 可写进**普通正文**的事实 = 全部事实 − 诊断槽位事实（`cp-8`）。这一步只做减法，
            #: 不改判据：登记轴 / 路由轴怎么选材一字未动，只是它们的作用域收窄到「允许写进
            #: 正文的那些行」。诊断槽位事实仍然在清单里、仍然进诊断表，只是不进正文。
            prose_facts = [f for f in facts if str(f.get("key") or "") not in diagnostic_keys]
            if has_column_axis:
                registered = [f for f in prose_facts
                              if declared_set & set(f.get("aspect_ids") or ())]
                registration_axis = "aspect_ids"
            elif has_routing_axis:
                registered = [f for f in prose_facts
                              if routed_column.get(_fact_row_id(f)) in declared_set]
                registration_axis = "presentation_routing.fact_columns"
            else:
                # 本支两条轴都没有：所有事实对小节的适用性无从判定，因此按清单顺序确定性
                # 轮转取事实，保证每个小节都有机会拿到句。
                registered = list(prose_facts)
                registration_axis = ""
            picked: list[dict] = []
            if has_column_axis or has_routing_axis:
                candidates = registered
            else:
                candidates = (registered[rotation:] + registered[:rotation])
                rotation += max(1, self.facts_per_subsection)
            for fact in candidates:
                if len(picked) >= self.facts_per_subsection:
                    break
                if fact["key"] in used:
                    continue
                used.add(fact["key"])
                picked.append(fact)
            sentences = []
            for fact in picked:
                sentence_seq += 1
                sentences.append({"sentence_id": f"s{sentence_seq:04d}",
                                  "text": str(fact["text"]).strip(),
                                  "citations": [fact["key"]]})
            #: 段声明服务哪几栏（`cw-4`）：本段所取事实在**本小节声明集合内**的那部分归属——
            #: 有 `aspect_ids` 就用它（公司 / 行业那一支），没有就用呈现层路由落到的栏目
            #: （财务那一支的权威事实 `aspect_ids` 恒为空，见 `pack_writer.scan_financial`）。
            para_axis: set[str] = set()
            for fact in picked:
                para_axis |= (declared_set & set(fact.get("aspect_ids") or ()))
                route = routed_column.get(_fact_row_id(fact), "")
                if route in declared_set:
                    para_axis.add(route)
            paragraphs = ([{"paragraph_id": "p1", "aspect_ids": sorted(para_axis),
                            "sentences": sentences}] if sentences else [])
            if not sentences:
                if not has_column_axis and not has_routing_axis and facts:
                    gaps.append({
                        "subsection_id": spec["subsection_id"],
                        "requirement_text": spec["requirement_text"],
                        "reason": "manifest_partial_for_requirement",
                        "detail": ("本支这次输入里**两条栏目轴都没有**（既没有任何权威事实携带"
                                   "非空 aspect 归属，也没有 `presentation_routing.fact_columns`），"
                                   "所以「归属对不上」这个理由在本支不成立；"
                                   "这里只说明：本小节按确定性轮转也没取到尚未写过的事实。"
                                   "财务 / 附注的覆盖情况由「选中事实 + artifact 缺口 + 附注缺口」"
                                   "表达，不由 aspect 级状态表达。")})
                else:
                    gaps.append({
                        "subsection_id": spec["subsection_id"],
                        "requirement_text": spec["requirement_text"],
                        **_subsection_gap(declared_ids=declared, usable=prose_facts,
                                          registered=registered, picked=picked,
                                          reasons=(), duplicates=0, noun="权威事实",
                                          registration_axis=registration_axis,
                                          unusable_axis_note=_routing_gap_note(
                                              declared=declared, routing=routing))})
            subsections.append({"subsection_id": spec["subsection_id"],
                                "title": spec["title"], "paragraphs": paragraphs})
        # 没进正文的权威事实的 **typed 去向**：逐条说清「这份事实为什么这一轮没写出去」。
        # 六种取值是闭集（`FACT_PLACEMENT_DISPOSITIONS`），其中五种是**没写出去**的五种
        # 不同原因，不得合并成一句「没用上」——「路由里没有它」「它那一栏本轮请求面里没有」
        # 与「它按展示角色就不该进正文」对下游的含义完全不同。
        declared_columns = {str(a).strip() for s in payload["subsections"]
                            for a in (s.get("declared_aspect_ids") or ()) if str(a).strip()}
        placement_dispositions = []
        for fact in facts:
            fact_id = _fact_row_id(fact)
            column = routed_column.get(fact_id, "")
            if fact["key"] in used:
                placement_dispositions.append(
                    {"fact_id": fact_id, "key": fact["key"],
                     "disposition": "placed", "column": column})
                continue
            if fact["key"] in diagnostic_keys:
                disposition = "diagnostic_slot_not_prose"
            elif not has_routing_axis:
                disposition = "no_column_axis_in_request"
            elif not column:
                disposition = "fact_not_routed"
            elif column not in declared_columns:
                disposition = "routed_column_not_in_request"
            else:
                disposition = "routed_column_cap_reached"
            placement_dispositions.append(
                {"fact_id": fact_id, "key": fact["key"],
                 "disposition": disposition, "column": column})
        self.placement_dispositions = placement_dispositions
        text = json.dumps({"subsections": subsections, "gaps": gaps},
                          ensure_ascii=False, sort_keys=True)
        self.calls.append({"call_id": OFFLINE_FACT_WRITER_IDENTITY,
                           "model": "offline-fact-assertion", "prompt_version": prompt_version,
                           "status": "ok", "model_policy": model_policy,
                           "column_axis_absent": (not has_column_axis),
                           "routing_axis_present": has_routing_axis,
                           "selection_axis": ("aspect_ids" if has_column_axis
                                              else "presentation_routing.fact_columns"
                                              if has_routing_axis else "rotation"),
                           "placement_dispositions": placement_dispositions,
                           "sentence_seq": sentence_seq, "fact_count": len(facts)})
        return CW.CitedProseResult(text=text, call_id=OFFLINE_FACT_WRITER_IDENTITY,
                                   model="offline-fact-assertion",
                                   prompt_version=prompt_version, status="ok")


class OfflineReworkCitedProseClient:
    """离线**局部返修**替身：只做一件**确定性**的事，其余原样交回。

    它**没有**语义判断力，因此它不假装能"重写一句话使它既忠于原文又适合本栏目"。它只做那一
    件不需要判断力、且**可以从请求面的登记表直接算出来**的事：

    **把被点名的句子放到"它的引用本来就登记在"的那一栏去。** 请求面里
    `subsections[].columns` 就是登记表的逐栏投影（`citable_columns` 的同一份），于是
    「这一句的引用登记在哪些栏」是一次集合运算。原稿的 `aspect_attribution` 硬错，全部形态都是
    "段落自己声明服务的栏目 ∉ 该句所引来源的登记归属"——把这一段按登记重切，错误就消失了，
    而**一个字都没改**（句子文本与引用原封不动，只换了段落的 `aspect_ids` 与分段位置）。

    除此之外的硬错（数字未授权、主体不逐字、模板文字、旧年当前化、呈现栏不符）**都不做**：
    它们的正确处置是重写或撤下，而"重写"正是本替身没有能力做的事。这些句子**整句撤下**并
    逐条留缺口，原因码 `source_present_but_not_admissible`——来源在场，但这一句的用法不可采用。

    **记住它不是什么**：它不是"返修质量"的证据。它写出来的东西逐字来自原稿，因此它能证明的
    只有——接口接得上、受保护句逐字保留、改绑要声明、撤下的句子有去向、返修失败不会吞掉原稿。
    真实模型在这一步会不会改得更好，只有真实 run 能回答。
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        #: 本轮**每一句被点名的话**的 typed 去向（闭集见 :data:`REWORK_DISPOSITIONS`）。
        #: 与另两个替身的 `withheld` / `placement_dispositions` 同一个用意：替身自己做的取舍
        #: 必须逐条可回查，而不是只体现在新稿里。
        self.dispositions: list[dict] = []

    def compose(self, *, messages, system: str, prompt_version: str,
                model_policy: str) -> CW.CitedProseResult:
        payload = json.loads(str(messages[0]["content"]))
        base = payload["base_draft"]
        named = [str(s) for s in payload.get("problem_sentence_ids") or ()]
        named += [str(s) for s in payload.get("semantic_sentence_ids") or ()]
        named_set = {s for s in named if s}
        kinds_by_sentence: dict[str, set[str]] = {}
        for row in payload["problems"]:
            kinds_by_sentence.setdefault(str(row["sentence_id"]), set()).add(
                str(row["check_kind"]))

        # ---- 登记表：键 → 登记到它的那些栏目（只从请求面的 columns 算）----------
        registration: dict[str, set[str]] = {}
        declared_by_subsection: dict[str, list[str]] = {}
        requirement_by: dict[tuple[str, str], str] = {}
        for spec in payload["subsections"]:
            declared_by_subsection[spec["subsection_id"]] = [
                str(a) for a in spec.get("declared_aspect_ids") or ()]
            for column in spec["columns"]:
                aspect = str(column["aspect_id"])
                requirement_by[(spec["subsection_id"], aspect)] = str(
                    column.get("requirement_text") or "")
                for key in (list(column.get("citable_material_keys") or ())
                            + list(column.get("citable_fact_keys") or ())):
                    registration.setdefault(str(key), set()).add(aspect)

        subsections: list[dict] = []
        gaps: list[dict] = []
        seen_gaps: set[str] = set()
        withdrawn = 0
        repaired = 0

        for spec in base["subsections"]:
            subsection_id = str(spec["subsection_id"])
            declared = set(declared_by_subsection.get(subsection_id) or ())
            paragraphs: list[dict] = []
            pending: dict | None = None

            def flush() -> None:
                nonlocal pending
                if pending is not None and pending["sentences"]:
                    item = {"paragraph_id": "", "sentences": pending["sentences"]}
                    if pending["aspect_ids"]:
                        item["aspect_ids"] = list(pending["aspect_ids"])
                    paragraphs.append(item)
                pending = None

            for para in spec["paragraphs"]:
                base_aspects = [str(a) for a in para.get("aspect_ids") or ()]
                for sent in para["sentences"]:
                    sid = str(sent["sentence_id"])
                    if sid not in named_set:
                        # **受保护句**：正文与引用一字不动，连它所在段落的栏目声明也照旧。
                        # 替身在这里什么都不做——正是这份"什么都不做"要能被离线证明。
                        target = base_aspects
                    else:
                        kinds = sorted(kinds_by_sentence.get(sid, ()))
                        aspects = (self._repair_placement(sent, declared, registration)
                                   if kinds == ["aspect_attribution"] else [])
                        if aspects:
                            repaired += 1
                            self.dispositions.append(
                                {"sentence_id": sid, "subsection_id": subsection_id,
                                 "disposition": "moved_to_registered_column",
                                 "check_kinds": kinds,
                                 "citations": list(sent.get("citations") or ()),
                                 "from_aspect_ids": base_aspects, "to_aspect_ids": aspects,
                                 "note": "句子文本与引用一字未改，只把它放到引用登记在的栏目上"})
                            target = aspects
                        else:
                            kind = kinds[0] if kinds else "semantic_review_flagged"
                            withdrawn += 1
                            self.dispositions.append(
                                {"sentence_id": sid, "subsection_id": subsection_id,
                                 "disposition": "withdrawn", "check_kinds": kinds,
                                 "citations": list(sent.get("citations") or ()),
                                 "from_aspect_ids": base_aspects, "to_aspect_ids": [],
                                 "note": self._withdraw_note(kind)})
                            gap = {"subsection_id": subsection_id,
                                   "requirement_text": self._requirement_text(
                                       requirement_by, subsection_id, base_aspects, declared),
                                   "reason": "source_present_but_not_admissible",
                                   "detail": (f"返修撤下原句 {sid}（{kind}）："
                                              f"{self._withdraw_note(kind)}")}
                            gap_key = NS.canonical_json(gap)
                            if gap_key not in seen_gaps:
                                seen_gaps.add(gap_key)
                                gaps.append(gap)
                            continue
                    if pending is None or pending["aspect_ids"] != target:
                        flush()
                        pending = {"aspect_ids": list(target), "sentences": []}
                    pending["sentences"].append(sent)
                # 段落边界：原稿的一段写完之后也断一次，免得相邻两段被并成一段
                flush()
            flush()

            for index, paragraph in enumerate(paragraphs, 1):
                paragraph["paragraph_id"] = f"p{index}"
            subsections.append({"subsection_id": subsection_id,
                                "title": str(spec.get("title") or ""),
                                "paragraphs": paragraphs})

        text = json.dumps({"subsections": subsections, "gaps": gaps,
                           "follow_up_needs": [], "citation_changes": []},
                          ensure_ascii=False, sort_keys=True)
        self.calls.append({"call_id": OFFLINE_REWORK_IDENTITY,
                           "model": "offline-registration-repair",
                           "prompt_version": prompt_version, "status": "ok",
                           "model_policy": model_policy,
                           "named_sentence_count": len(named_set),
                           "repaired": repaired, "withdrawn": withdrawn,
                           "dispositions": self.dispositions})
        return CW.CitedProseResult(text=text, call_id=OFFLINE_REWORK_IDENTITY,
                                   model="offline-registration-repair",
                                   prompt_version=prompt_version, status="ok")

    @staticmethod
    def _repair_placement(sentence: Mapping[str, Any], declared: set[str],
                          registration: Mapping[str, set[str]]) -> list[str]:
        """这一句能落到本小节的哪些栏上——**只**看它的引用登记在哪些栏。

        交集：一句引了 `m10` 与 `m26`，只有当某一栏**同时**登记着两者时，这一句才整体属于
        那一栏。返回空表示本小节的每一栏都与它无关：那就没有可落的栏，交给撤下这一支。
        """
        citations = [str(k) for k in sentence.get("citations") or ()]
        if not citations:
            return []
        registered: set[str] | None = None
        for key in citations:
            here = set(registration.get(key) or ())
            registered = here if registered is None else (registered & here)
        return sorted((registered or set()) & declared)

    @staticmethod
    def _requirement_text(requirement_by: Mapping[tuple[str, str], str],
                          subsection_id: str, base_aspects: Sequence[str],
                          declared: set[str]) -> str:
        """撤下的句子该记到**哪一栏**的要求文本上。

        优先用它原来所在段落声明的第一栏（那是原稿自己说过它要写的那一栏）；段没有声明时才
        退回本小节声明的第一栏。**不**去猜一栏——猜出来的要求文本会让缺口指到一条从来没人
        写过的要求上。
        """
        for aspect in (*base_aspects, *sorted(declared)):
            text = requirement_by.get((subsection_id, str(aspect)), "")
            if text:
                return text
        return ""

    @staticmethod
    def _withdraw_note(kind: str) -> str:
        return {
            "aspect_attribution": "本小节的任何一栏都没有登记这一句所引的来源",
            "numeric_qualification": "这一句里的金额/比率没有合格事实或表来源支撑",
            "numeric_surface": "这一句的数字与所引原文对不上（含期间改写）",
            "subject_surface": "这一句的主体不等于所引原文里的主体",
            "template_text": "这一句的文字是勾选/披露模板，不是公司经营事实",
            "current_state_scope": "这一句用旧年来源写了当前状态，且没有可读出的绝对年份",
            "presentation_column_attribution": "这一句引的事实不属于呈现层声明的那一栏",
        }.get(kind, f"离线替身不能处理这一句的硬错（{kind}）：重写需要语义判断力，"
                    "本替身整句撤下")


class OfflineEchoCitedReviewClient:
    """离线审阅替身：**把机械核对的结论照抄成逐句意见**。

    它**没有**独立语义判断能力——这正是必须写清楚的事：真实的独立审阅（§0.20 的九类句义
    问题）只能由真实模型在授权 run 里给出。本替身存在的意义是让「审阅这一环是否接得上、
    逐句覆盖等式是否成立、硬错误会不会被 `supported` 覆盖」这些**结构**判据可被离线验证。
    """

    def __init__(self, *, check_report) -> None:
        self._check_report = check_report
        self.calls: list[dict] = []

    def review(self, *, messages, system: str, prompt_version: str,
               model_policy: str) -> CR.CitedReviewResult:
        payload = json.loads(str(messages[0]["content"]))
        hard = set(self._check_report.blocked_sentence_ids)
        issues = []
        for row in payload["sentences"]:
            citations = list(row.get("citations") or ())
            if not citations:
                raise AssertionError(
                    f"离线替身审阅遇到一句没有任何引用的句子 {row['sentence_id']!r}："
                    "该句在机械层已是硬错误，替身无法为它给出可解析的意见——"
                    "这本身是本轮的真实读数，不应当被静默跳过")
            sid = str(row["sentence_id"])
            if sid in hard:
                issues.append({"sentence_id": sid, "citation_id": citations[0],
                               "category": "insufficient",
                               "semantic_category": "not_supported_by_source",
                               "severity": "high", "blocking": True,
                               "reason": "机械核对已判该句 hard_error（离线替身照转，不独立判断）"})
            else:
                issues.append({"sentence_id": sid, "citation_id": citations[0],
                               "category": "supported", "severity": "none",
                               "blocking": False,
                               "reason": "离线替身未做独立语义判断；该句文本与所引原文逐字一致"})
        text = json.dumps({"issues": issues}, ensure_ascii=False, sort_keys=True)
        self.calls.append({"call_id": OFFLINE_REVIEW_IDENTITY, "model": "offline-echo",
                           "prompt_version": prompt_version, "status": "ok",
                           "model_policy": model_policy, "issue_count": len(issues)})
        return CR.CitedReviewResult(text=text, call_id=OFFLINE_REVIEW_IDENTITY,
                                    model="offline-echo", prompt_version=prompt_version,
                                    status="ok")


#: 冻结 WritingSpec 的**资产路径**与**声明**的内容指纹（`contracts.schema_v2.content_fingerprint`
#: 的自证值）。指纹在这里**重算比对**，不靠「路径对得上」——同一路径换一份内容，写作小节轴就会
#: 悄悄跟着换，而 §「小节轴 = WritingSpec 小节」这条规则恰恰要求它是冻结的那一份。
WRITING_SPEC_PATH = "templates/writing_specs/credit_report_v1.yaml"
WRITING_SPEC_CONTENT_SHA256 = (
    "82941438e1adbc7d0a1c685eab2f63b3b7b243ed70829627f6c2f8cdc8626354")

#: `subsection_id` 前缀 → WritingSpec 的节名。小节轴按**节**取 TOC 顺序，不按「谁先出现」排。
_SUBSECTION_SECTION_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("co-", "company"), ("fin-", "financial"), ("ind-", "industry"))


def _source_classes_of(aspect) -> tuple[str, ...]:
    """该 aspect 在冻结 Contract 里允许的来源类（并集，保持首次出现次序，去重）。"""
    classes: list[str] = []
    for ref in getattr(aspect, "evidence_requirement_ids", ()) or ():
        for source_class in getattr(ref, "source_classes", ()) or ():
            name = str(source_class or "").strip()
            if name and name not in classes:
                classes.append(name)
    return tuple(classes)


def _section_of_subsection(subsection_id: str) -> str:
    for prefix, name in _SUBSECTION_SECTION_BY_PREFIX:
        if subsection_id.startswith(prefix):
            return name
    raise SystemExit(
        f"WritingSpec 的 `subsection_id` {subsection_id!r} 不属于任何已知节"
        f"（前缀表 {[p for p, _ in _SUBSECTION_SECTION_BY_PREFIX]}）："
        "分不出节就取不到该节的 TOC 顺序，不猜一个顺序出来")


def _subsections_from_writing_spec(requirement) -> list[CW.CitedSubsectionSpec]:
    """Contract 栏目 → **冻结 WritingSpec 的小节**（`cwm-6`）。

    这条链以前是「一个 Contract aspect 一个小节」。那把小节轴造得比冻结 WritingSpec **更细**：
    18 个 `company_business*` aspect 在 WritingSpec 里**全部**归属同一个 `co-h4`
    （「主营业务、经营模式与产业链」），6 个 `fin_solvency*` 全部归属 `fin-h2`
    （「资产负债结构分析」）。小节轴更细之后，提示词里「一个小节一条」就被放大成 18 个短栏，
    同一份材料被反复拿来填不同的小节——**这是结构缺陷，不是取材不足**。

    本函数只做**投影**，不改写任何一侧：

    * 小节**取 WritingSpec 的**：`subsection_id` 与 `title` 逐字来自冻结 WritingSpec 的 TOC；
      次序是**节间取前缀表声明的次序、节内取该节 TOC**，全程不看 Contract aspect 的先后。
      于是产出是「这份资产 × 这组栏目」的纯函数——上游把两栏对调，写作结构不跟着换样子。
    * `requirement_text` **取 Contract 的**：该小节覆盖到的每个 Contract `requirement_text`
      逐字、按 Contract 侧次序拼接，**一个不删、一个不改**。所以「一个小节覆盖哪些栏目」在
      写作请求面上仍然逐字可查——收的是**小节数**，不是**要求内容**。
    * `declared_aspect_ids` 就是「这个小节覆盖的那几条 Contract 栏目」，与 `requirement_text`
      一一对位；材料登记归属轴比的就是这个集合。
    * `allowed_source_classes` 仍逐字投影自 Contract（并集）。

    fail-closed 的三处：WritingSpec 读不到、指纹对不上、Contract aspect 在 WritingSpec 里
    **没有**归属小节。第三种意味着「这一栏要求的内容在这份报告结构里没有位置」，那不是可以
    静默丢掉的输入，也不是可以临时造一个小节接住的东西。
    """
    path = Path(__file__).resolve().parents[1] / WRITING_SPEC_PATH
    if not path.exists():
        raise SystemExit(f"冻结 WritingSpec 不存在：{path}——没有它就没有小节轴，不退回按 aspect 造小节")
    ws = WS.load_writing_spec(str(path))
    declared = str((ws.raw or {}).get("content_sha256") or "")
    actual = _S2.content_fingerprint(ws.raw)
    if actual != WRITING_SPEC_CONTENT_SHA256 or declared != WRITING_SPEC_CONTENT_SHA256:
        raise SystemExit(
            f"冻结 WritingSpec 内容指纹不符：声明 {declared!r}、重算 {actual!r}、"
            f"本脚本钉住 {WRITING_SPEC_CONTENT_SHA256!r}——小节轴必须落在冻结那一份上，"
            "指纹不符一律停，不使用这份内容")

    #: `aspect_id` → `subsection_id`，逐字来自 WritingSpec 的 `mappings`（主槽位归属 R1-A §七）。
    home: dict[str, str] = {}
    for row in ws.mappings:
        aspect_id = str(row.get("aspect_id") or "").strip()
        subsection_id = str(row.get("subsection_id") or "").strip()
        if not aspect_id or not subsection_id:
            continue
        home.setdefault(aspect_id, subsection_id)

    #: 逐节收：小节 → [该小节的 Contract aspect]（保持 Contract 侧次序）。
    grouped: dict[str, list[Any]] = {}
    for aspect in getattr(requirement, "aspects", ()) or ():
        text = str(getattr(aspect, "requirement_text", "") or "")
        aspect_id = str(getattr(aspect, "aspect_id", "") or "")
        if not text or not aspect_id:
            continue
        subsection_id = home.get(aspect_id, "")
        if not subsection_id:
            raise SystemExit(
                f"Contract 栏目 {aspect_id!r} 在冻结 WritingSpec 的 `mappings` 里没有归属小节："
                "这一栏要求的内容在这份报告结构里没有位置。不静默丢掉，也不临时造一个小节接住")
        grouped.setdefault(subsection_id, []).append(aspect)

    #: 顺序按**该节 TOC**，标题逐字取 TOC 的 `h2`。
    #:
    #: 节与节之间取**前缀表声明的次序**（company → financial → industry），**不**取「哪个栏目
    #: 先在 Contract 里出现」。取后者的话，请求面的小节次序会跟着 Contract 的栏目次序漂移——
    #: 同一个主题只要上游把两栏对调，写作结构就跟着换一个样子，而这两件事本来无关。节内仍按
    #: 该节 TOC。这样整个输出是**这份资产与这组栏目的纯函数**，与输入次序无关。
    ordered: list[CW.CitedSubsectionSpec] = []
    in_play = {_section_of_subsection(sid) for sid in grouped}
    seen_sections: list[str] = [name for _p, name in _SUBSECTION_SECTION_BY_PREFIX
                                if name in in_play]
    toc_order: list[str] = []
    for name in seen_sections:
        for entry in ws.toc.get(name) or ():
            sid = str(entry.get("subsection_id") or "")
            if sid:
                toc_order.append(sid)
    if set(grouped) - set(toc_order):
        raise SystemExit(
            f"WritingSpec 的 TOC 里没有这些小节的条目 {sorted(set(grouped) - set(toc_order))}："
            "取不到标题与顺序，不猜")

    for subsection_id in [s for s in toc_order if s in grouped]:
        aspects = grouped[subsection_id]
        title = next((str(e.get("h2") or "") for e in (ws.toc.get(
            _section_of_subsection(subsection_id)) or ())
            if str(e.get("subsection_id") or "") == subsection_id), "")
        aspect_ids = tuple(str(getattr(a, "aspect_id", "") or "") for a in aspects)
        classes: list[str] = []
        for aspect in aspects:
            for name in _source_classes_of(aspect):
                if name not in classes:
                    classes.append(name)
        ordered.append(CW.CitedSubsectionSpec(
            subsection_id=subsection_id, title=title or subsection_id,
            requirement_text="\n".join(
                str(getattr(a, "requirement_text", "") or "") for a in aspects),
            declared_aspect_ids=aspect_ids,
            allowed_source_classes=tuple(classes)))
    return ordered


def build_source_display(*, entries, run_dir: Path, section_id: str, run_id: str,
                         paired_report_version: str = "", resolver=None):
    """§0.21 **路径 b**：按运行级来源记录取原件像素与可回查坐标（只读展示）。

    它**不**进 `TopicResearchPack`、不进 Writer 材料身份、不是 `TableObject`、不构成任何
    数字权威，也不证明 Contract `set_complete`。两条路径的身份**不可互换**——路径 b 的展示集
    身份体里因此**没有** `paired_report_version`（它只是给读者看的交叉引用）。

    真实 PDF 与真实登记清单都在场，因此这是本 run 的**真实读数**（没有替身）；但它读的是
    「原件长什么样」，不是「数字对不对」。区域本身是否完整清晰忠实，由**人**确认。

    `resolver`（本 run 的上传输入解析器）给出时，每一份要取像素的 PDF 也**按
    `(document_id, sha256)` 从本 run 的上传对象**取，与写作侧读的是同一份字节；**没有**
    回落到登记路径的分支。为 `None` 时用登记路径（不带 `--run-input` 的历史兼容运行）。
    """
    paths = ({str(e.document_id): str(resolver.resolve(
        document_id=str(e.document_id), file_sha256=str(e.file_sha256)))
        for e in entries} if resolver is not None
        else {str(e.document_id): str(e.source_path) for e in entries})
    specs = STD.load_source_display_specs(SOURCE_DISPLAY_RECORD_PATH)
    registrations = STD.load_source_display_registrations(SOURCE_DISPLAY_RECORD_PATH)
    return STD.build_source_table_display_set(
        regions=specs, task_id=run_id, section_id=section_id,
        source_record_id="m930_3_source_table_display_v1",
        #: 「登记了哪些来源」要进身份体，因此不能写一个常量占位符冒充——
        #: 登记清单换一版（成员、版本或哈希任一变动）展示集身份就必须跟着变。
        registered_source_id=_registered_source_id(entries),
        output_dir=run_dir / "source_display", resolve_source_path=paths,
        registered_sha256=STD.registered_sha256_from_source_entries(entries),
        registered_source_name={str(e.document_id): str(e.source_name) for e in entries},
        registrations=registrations, paired_report_version=paired_report_version)


def _registered_source_id(entries) -> str:
    """登记来源集的身份：对**四轴**（文档、版本、内容哈希、源类型）取摘要。"""
    body = [
        {"document_id": str(e.document_id), "document_version": str(e.document_version),
         "file_sha256": str(e.file_sha256),
         "registered_source_type": str(e.registered_source_type)}
        for e in entries
    ]
    payload = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "rsm_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _emit_section(*, out_dir: Path, run_id: str, section_id: str, topic_id: str, inputs,
                  entries, profile, subject: str, subject_name: str, clock, payload,
                  table_matrix, resolver=None) -> dict:
    """把**一节**的全部产物写进 `out_dir`，返回根页要用的对象。

    只做落盘与调用既有渲染器，不新造任何身份：`source_display` 的 `section_id` 进它自己的
    身份体，因此**每节各建一次**，不跨节复用同一个展示集对象。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    #: 登记清单里**实际参与比对**的哈希：读者面必须印这一个，而不是建区时抄下的那个字段
    #: （后者在登记清单在世时常常是空的，会让「一致」这句话看起来没发生过比对）。
    registered = STD.registered_sha256_from_source_entries(entries)
    #: 路径 b 的展示集：**在**登记清单在场的这一层现建，不与写作链共用任何身份。
    #: 它这里的 `paired_report_version` 只是给读者看的交叉引用，不进它自己的
    #: 身份体（`SourceTableDisplaySet.identity_body` 刻意排除该字段）。
    source_display = build_source_display(
        entries=entries, run_dir=out_dir, section_id=section_id, run_id=run_id,
        paired_report_version=str(payload["preview"].version.report_version),
        resolver=resolver)

    manifest = payload["manifest"]
    outcome = payload["outcome"]
    #: 读者面显示的是**有效草稿**（返修成功即新稿）：`sentence_checks.json`、`demo_page.md`
    #: 与 `cited_preview.md` 三处必须同源，否则「预览里没有硬错、核对文件里还有」无法被人发现。
    check_report = payload.get("active_check_report") or payload["check_report"]
    review_outcome = payload["review_outcome"]
    version = payload["version"]
    preview = payload["preview"]

    _write_json(out_dir / "cited_input_manifest.json", manifest.to_dict())
    _write_json(out_dir / "cited_prose.json", outcome.to_dict())
    # 草稿缺口的**逐条分桶**（`crpp-5`）另落一份**旁挂产物**，而不是往 `CitedReportVersion`
    # 里加字段：加字段会改 `identity_body()` 的字段集合，按 :data:`CITED_REPORT_LEGACY_WIRE_VERSIONS`
    # 的进表条件，全部历史 `cited_report_version.json` 会**当场地**从可读回变成解不开——那是拿
    # 「这一栏是否阻断」这一条读数，去换整批历史产物的可读性，不划算。旁挂这份由控制器按
    # gap_id **逐条**与被读的草稿对账（见 `assurance/cited_controller.py`），不是自说自话。
    _active_draft = payload.get("active_draft") or outcome.draft
    gap_bins = tuple(payload.get("gap_bins") or ())
    _contract_version, _contract_fingerprint = CRP.frozen_contract_identity()
    _write_json(out_dir / "cited_gap_bins.json", {
        "policy_version": CRP.CITED_REPORT_POLICY_VERSION,
        "schema_version": CRP.CITED_REPORT_VERSION_SCHEMA_VERSION,
        # 分桶的**依据身份**（`crpp-6`）：每一条判定的政策串都来自这一份冻结 Contract，
        # 因此把版本与内容指纹一起记下来，读侧（控制器）才能验明「这份分桶是站在哪一份
        # Contract 上做的」。没有它，「行与数自洽」只能证明旁挂跟自己一致。
        "contract_version": _contract_version,
        "contract_fingerprint": _contract_fingerprint,
        "draft_id": _active_draft.draft_id,
        "report_version": version.report_version,
        "bins": list(CRP.CITED_GAP_BINS),
        "blocking_bins": list(CRP.CITED_GAP_BLOCKING_BINS),
        "not_applicable_policies": list(CRP.CITED_GAP_NOT_APPLICABLE_POLICIES),
        "required_gap_count": version.required_gap_count,
        "draft_gap_count": len(_active_draft.gaps),
        # 两条**正交轴**（`crpp-6`，读侧现算）：正文应写未写 / 按冻结 Contract 的
        # `blocking_policy` 阻断放行。与 `required_gap_count` **并排**给出，不合并——
        # 三条指令不同（补内容 / 放行门 / 必需未解决）。
        "axes": CRP.gap_axes_from_bins(gap_bins),
        "rule": ("逐条缺口按**冻结 Contract**的适用性政策与展示档分桶：先看 "
                 "`applicability_policy`（`missing_policies` 明文『合法不适用、不阻断』的那两条），"
                 "再看 `display_tier`。只有 `required` 与 `unresolved` 计入 "
                 "`required_gap_count`；`unresolved`（在冻结 Contract 里找不到这一栏）"
                 "按**必需**处理是 fail-closed，不是宽容。"
                 "`axes.required_body_gap_count`（正文应写未写）与 "
                 "`axes.release_blocking_gap_count`（按 `blocking_policy` 阻断放行）"
                 "是另外两条轴，与 `required_gap_count` 不重合。"),
        "gaps": [b.to_dict() for b in gap_bins],
    })
    # ---- 第三步（局部返修）的落盘：**原稿与新稿并列**，谁都没被覆盖 -------------
    # `cited_prose.json` 照旧是**初写那一稿**（文件名不改、内容不改，历史基线仍逐字可比）；
    # 返修稿另存一份；`sentence_checks.json` 写的是**读者面真正显示的那一稿**的读数，因此
    # 它必须与 `cited_preview.md` 同源——两者不同源正是"预览显示 A 稿、核对读的是 B 稿"这类
    # 无人能察觉的错位。初写那一稿的读数另存 `sentence_checks_initial.json`，于是"返修把它们
    # 改好了多少"是一次两次读数之差，而不是一句自述。
    _write_json(out_dir / "sentence_checks.json", check_report.to_dict())
    rework_outcome = payload.get("rework_outcome")
    #: 「初稿读数是否另存了」= 生效读数**不是**初稿读数。下面的留档说明必须与这一事实一致，
    #: 因此这个布尔值只算一次、两处共用，不允许各写各的。
    checks_have_base = check_report is not payload.get("check_report")
    if checks_have_base:
        _write_json(out_dir / "sentence_checks_initial.json",
                    payload["check_report"].to_dict())
    if rework_outcome is not None and rework_outcome.draft is not None:
        _write_json(out_dir / "cited_prose_reworked.json", rework_outcome.draft.to_dict())
    rework_request = payload.get("rework_request")
    _write_json(out_dir / "cited_rework.json", {
        "policy_version": CRW.CITED_REWORK_POLICY_VERSION,
        "schema_version": CRW.CITED_REWORK_SCHEMA_VERSION,
        "prompt_version": CRW.CITED_REWORK_PROMPT_VERSION,
        "max_rounds": CRW.CITED_REWORK_MAX_ROUNDS,
        "gate": str(payload.get("rework_gate") or ""),
        "rule": ("返修只处理点名句：逐句硬错句、以及调用方**明确**给出的语义待审句。"
                 "没被点名的句子正文与引用必须逐字原样保留；新稿里出现任何基准稿没用过的"
                 "引用键都必须在 `citation_changes` 里逐条声明，且声明要与其在稿里的真实"
                 "引用逐字相符。**最多一次**，没有第二次。"),
        "request": (rework_request.to_dict() if rework_request is not None else None),
        "outcome": (rework_outcome.to_dict() if rework_outcome is not None else None),
        #: 三个终态各自**留什么**，逐条写明。这一段是给读回的人看的：`outcome` 里的数字自己
        #: 不会说「失败时有效草稿是谁」。`failed` 时初稿仍在 `cited_prose.json` 里、它的逐句
        #: 核对在 `sentence_checks.json` 里（生效读数**就是**初稿，所以没有 `_initial` 副本）、
        #: 失败原因在 `outcome.failure_reason` / `failure_detail`，而 `cited_preview.md` 是
        #: **不可发布**的标记稿。`reworked` 时才有 `sentence_checks_initial.json`。
        "retention": _rework_retention_note(rework_outcome, gate=str(
            payload.get("rework_gate") or ""), has_base=checks_have_base),
    })
    # 被替身自己撤下的候选片：**单独落盘**。「这一片为什么没写成正文」必须有一个可回查的
    # 位置，否则它只存在于读回正文里，逐条回查不了。三种撤下原因互不顶替，因此逐条带
    # 原因码，并在文件里给出按原因码的计数。
    _withheld_records = list(getattr(payload["prose_client"], "withheld", ()) or ())
    _write_json(out_dir / "withheld_candidates.json", {
        "policy_version": SC.SENTENCE_CHECK_POLICY_VERSION,
        "rule": ("金额与比率的授权只有两条路：合格事实（路径 A）或表材料的某一格；"
                 "普通材料原文里逐字出现**不**构成授权。此外，只由不能表达当前状态的来源"
                 "角色支撑且自身没有期间限定的片、以及带表单控件字形的片，都不得写成正文。"),
        "note": WITHHELD_NUMERIC_NOTE,
        "reasons": list(WITHHELD_REASONS),
        "by_reason": {reason: sum(1 for row in _withheld_records
                                  if str(row.get("reason", "")) == reason)
                      for reason in WITHHELD_REASONS},
        "sentences": _withheld_records,
    })
    # 权威事实的 typed **落栏去向**：也与 `withheld_candidates.json` 同一个用意——「这一条事实
    # 本轮为什么进了正文 / 为什么没进」必须有一条可逐条回查的记录。没有它，读者只能从正文反推
    # 「少了几条」，而「路由里根本没有它」（要去补路由声明）与「它那一栏本轮请求面里没有」
    # （本次少了一栏）与「每栏句数上限先占满了」（本轮写作预算）是三件**要分别处理**的事。
    _fact_placements = list(getattr(payload["prose_client"], "placement_dispositions", ()) or ())
    _write_json(out_dir / "fact_placement.json", {
        "policy_version": SC.SENTENCE_CHECK_POLICY_VERSION,
        "rule": ("事实到栏目的归属只有两条轴：事实自己的登记归属 `aspect_ids`（公司 / 行业侧），"
                 "或呈现层路由 `presentation_routing.fact_columns`（财务 / 附注侧）。两条轴都"
                 "没有时替身按清单顺序确定性轮转。**没有路由的事实不得被塞进任何一栏**，"
                 "它的去向逐条记在这里。"),
        "note": FACT_PLACEMENT_NOTE,
        "dispositions": list(FACT_PLACEMENT_DISPOSITIONS),
        "by_disposition": {d: sum(1 for row in _fact_placements
                                  if str(row.get("disposition", "")) == d)
                           for d in FACT_PLACEMENT_DISPOSITIONS},
        "facts": _fact_placements,
    })
    # 审阅这一环**失败也要有落点**：`review_issues.json` 照旧写，但写的是"没有意见产出"
    # 这件事本身（`outcome: null` + typed 原因），**不是**一份空的意见集——后者会被读成
    # "审阅跑了，一条意见都没有"。
    _review_failure = str(payload.get("review_failure") or "")
    _write_json(out_dir / "review_issues.json", (
        review_outcome.to_dict() if review_outcome is not None else {
            "outcome": None,
            "review_failure": _review_failure,
            "note": ("本轮独立审阅**没有产出意见**：正文、逐句机械核对与缺口照旧落盘并可见，"
                     "缺的是独立审阅这一环。这一条不等于「审阅看过、没问题」。"),
            #: **受控诊断 + 调用身份**（`crr-9`）。原因码本身不足以复核：读者还得知道错在
            #: 哪一条意见（`sentence_id` / `citation_id`）、原始可见回复在哪。`call_id` 指向
            #: `logs/llm/<时间戳>__<call_id>.jsonl`——**这一份运行目录自己**就够走完这条 join，
            #: 不必先去翻账本。诊断里不放隐藏推理、不放回复正文；它只回答"为什么没跑完"。
            "diagnostic": {
                "call_id": _review_attempt_call_id(payload.get("budget"),
                                                   section_id=section_id),
                **dict(payload.get("review_failure_detail") or {}),
            },
        }))
    _write_json(out_dir / "cited_report_version.json", version.to_dict())
    (out_dir / "cited_preview.md").write_text(
        CRP.render_cited_preview_markdown(preview, gap_bins=gap_bins), encoding="utf-8")
    # 财务节的确定性指标表：**成功与失败都要落盘**（失败时写原因码与消息，不写一张空表）。
    # 「本节没有表」是 `metric_tables.refusals` 里的 typed 记录；「表没建出来」是这里的
    # `error` 块 —— 两者在文件里就是两个不同的字段，读的人不可能把它们混成同一件事。
    metric_tables = payload["metric_tables"]
    _write_json(out_dir / "cited_metric_tables.json", (
        metric_tables.to_dict() if metric_tables is not None
        else {"outcome": None, "error": payload["metric_table_error"]}))
    # 呈现层路由声明：它**不是**事实权威，但要能被逐条回查（哪条事实按声明属于哪一栏、
    # 哪一栏本次没有任何指标落进去）。落盘的是**同一份**进清单身份体的内容。
    routing = payload["presentation_routing"]
    _write_json(out_dir / "presentation_routing.json", (
        routing.to_dict() if routing is not None
        else {"routing": None,
              "note": "本节本次没有 `fin_solvency` 主题，因此没有呈现层路由声明。"}))
    # 确定性呈现（`fin_source_scope` 的来源/口径栏、`fin_balance_structure` 的资产负债结构栏）：
    # **零模型调用**，逐条取值与逐条缺口。它们各自单独落盘，而不是混进 `cited_preview.md`——
    # 它们没有草稿、没有引用、没有审阅，塞进路径 a 的产物里会让读者以为那一栏也经过了写作与
    # 核对。**文件名取自登记表**：落盘与读者面渲染共用同一个名字，两者不可能指到不同的族。
    source_scopes = tuple(payload.get("source_scopes") or ())
    for scope in source_scopes:
        name = _deterministic_presenter_name(topic_id=scope.topic_id, section_id=section_id)
        _write_json(out_dir / f"{name}__{scope.topic_id}.json", scope.to_dict())
        (out_dir / f"{name}__{scope.topic_id}.md").write_text(
            _DETERMINISTIC_RENDERERS[name](scope), encoding="utf-8")

    # ---- 路径 b：原 PDF 表区只读展示（**真实**读数，无替身） -----------------
    _write_json(out_dir / "source_table_display.json", source_display.to_dict())
    (out_dir / "source_table_display.md").write_text(
        STD.render_source_table_display_markdown(
            source_display, registered_sha256=registered,
            image_base="source_display"),
        encoding="utf-8")

    readback = _render_readback(
        run_id=run_id, profile=profile, subject=subject, subject_name=subject_name,
        clock=clock, section_id=section_id, topic_id=topic_id, inputs=inputs,
        payload=payload, table_matrix=table_matrix, source_display=source_display)
    (out_dir / "readback.md").write_text(readback, encoding="utf-8")

    # ---- 一页：两条可读路径 + 财务权威表 + 缺口 + 审阅意见，**同一版本** --------
    page = _render_demo_page(
        run_id=run_id, section_id=section_id, subject_name=subject_name,
        preview=preview, check_report=check_report, version=version,
        source_display=source_display, metric_tables=metric_tables,
        withheld=list(getattr(payload["prose_client"], "withheld", ()) or ()),
        registered_sha256=registered, source_scopes=source_scopes,
        presentation_routing=routing, topic_ids=tuple(payload.get("topic_ids") or ()),
        subsection_count=len(payload["manifest"].subsections),
        column_counts=_contract_column_counts(payload, source_scopes))
    (out_dir / DEMO_PAGE_FILENAME).write_text(page, encoding="utf-8")
    return {"source_display": source_display, "metric_tables": metric_tables,
            "withheld": list(getattr(payload["prose_client"], "withheld", ()) or ()),
            "registered_sha256": registered, "out_dir": out_dir,
            "source_scopes": source_scopes, "presentation_routing": routing}


def run(*, results_root: Path, run_id: str, profile_path: str, subject: str | None,
        subject_name: str | None, section_ids: tuple[str, ...] = (DEFAULT_SECTION,),
        topics: dict[str, tuple[str, ...]] | None = None,
        mode: str = ACC.MODE_OFFLINE, model: str | None = None,
        run_input: Path | None = None, financial_input: Path | None = None,
        authorization_root: Path | None = None) -> int:
    """一次读回：同一份输入面（`RealEnvironment`）下**逐节**走完整条链。

    产物布局：单节时逐字沿用旧布局（全部文件落 `run_dir` 根）；多于一节时每节落
    `run_dir/<section>/`，并在根上另写一份 `demo_page.md` —— 它把各节的同一批产物
    **原文**并排，逐节列出各自的版本锚。**「同一版本」这句话在节内成立**：每节的
    `report_version` 只含该节写作侧输入，跨节是两个不同的锚，因此根页不合并它们、
    也不给出一个总版本号（那会是一个不存在的身份）。

    `run_input` 是**本次上传的原始字节**所在目录（`cited_run_input` 的 `cri-1` 布局）。
    给出时，链里读 PDF 的**三个点**全部改为按 `(document_id, sha256)` 从本 run 的上传对象
    取，**一处都不回落到登记路径**；运行目录里另写一份 `run_input_binding.json`，说明这一轮
    读的是哪几份字节。**不给**时用登记路径——那是历史兼容运行，不是本批的演示路径。

    `financial_input` 是**本次上传的三份 XLSX** 所在目录（`cited_financial_input` 的 `cfi-1`
    布局）。给出时，链在**建立结果目录之前**核验它们与财务库里**当前有效快照**声明的
    `source_versions` 逐份同字节、主体/期末/口径/币种/用途/快照 id 全部对得上，并在装配期
    再核一次快照没有漂移。本批**不重抽工作簿、不写共享财务库**：财务节照旧从既有权威快照
    读事实，这三份上传只用于证明「本次核对并复用的是同字节的已建立权威快照」。
    **财务节在真实模式下不给 `financial_input` 即拒**——那正是「本次上传核对过权威」这句话
    唯一可核对的对象。
    """
    from planning import demo_scope as SC_scope

    ACC._DEMO_SCOPE_PROFILE_PATH = profile_path
    profile = SC_scope.load_demo_scope_profile(ACC.active_profile_path())
    bound_order = ACC.bind_section_order(profile)
    print(f"demo scope profile={profile.profile_id!r}；节序={list(bound_order)}")
    if not section_ids:
        raise SystemExit("至少要有**一个**节：没有节就没有材料边界、没有正文、没有版本锚")
    for section_id in section_ids:
        if section_id not in bound_order:
            raise SystemExit(f"节 {section_id!r} 不在本 profile 的节序 {list(bound_order)} 内")
    multi = len(section_ids) > 1

    fin_db = REPO / "data" / "financial_v2.db"
    if not subject or not subject_name:
        if not fin_db.exists():
            raise SystemExit("缺 data/financial_v2.db：没有可声明的真实主体（不拿合成输入冒充）")
        if not subject:
            subjects = _current_snapshot_subjects(fin_db)
            if not subjects:
                raise SystemExit("财务库里没有任何 current 快照：没有可声明的真实主体")
            subject = subjects[0]
        if not subject_name:
            subject_name = _registered_company_name(fin_db, subject)
        if not subject_name:
            raise SystemExit(f"主体 {subject} 在财务库里没有唯一一个 matched 的名称登记："
                             "`subj-2` 的名称核对无从发生（不拿证券代码冒充名称）")

    run_dir = results_root / run_id
    if run_dir.exists():
        raise SystemExit(f"结果目录已存在，create-only 拒绝覆盖：{run_dir}")

    #: 节集合是**上限的一部分**（见 `sections.cited_budget` 模块头）。真实模式下它必须在
    #: **建立运行目录之前、任何请求之前**成立：一个没被批过的节若走到这里，它花掉的是一次
    #: 没发生过的授权，而账本事后只能证明「用了 N 次」。离线模式不过这道门——那里没有可记账
    #: 的尝试，而历史离线 run（含财务节）的重放必须照旧可跑。
    if mode != ACC.MODE_OFFLINE:
        try:
            CB.assert_sections_approved(section_ids)
        except CB.CitedBudgetError as exc:  # noqa: BLE001 - 事前拒绝，带着原因停
            raise SystemExit(f"真实模式下本节集合未获批，停止在第一请求之前：{exc}") from exc

    #: 真实写作/返修/审阅的**预算门**：模型身份 + 四条上限能不能被共享预算实现表达。
    #: 它在建立运行目录**之前**判，因此这一次拒绝不留任何半成品。
    cited_gate, approved_model = _cited_run_gate(mode=mode, model=model)
    if cited_gate is not None:
        rework_line = (f" + ≤{CB.CITED_REWORK_MAX_PER_SECTION} 修"
                       if CB.cited_rework_approved() else " + 返修**本次未获批**")
        print(f"真实写作/审阅预算门已就位：model={approved_model!r}，"
              f"政策={CB.CITED_BUDGET_POLICY_VERSION}，"
              f"获批节={list(CB.CITED_BUDGET_SECTIONS)}，每节 ≤"
              f"{CB.CITED_WRITE_MAX_PER_SECTION} 写{rework_line} + "
              f"≤{CB.CITED_REVIEW_MAX_PER_SECTION} 审，合计 ≤{CB.CITED_RUN_MAX_ATTEMPTS}，"
              f"自动重试 {CB.CITED_AUTOMATIC_RETRIES}")

    #: 运行输入在**建立结果目录之前**核验：一份读不开的上传输入不该留下一个半成品运行目录。
    #: `declared` 取的是当前 Evidence 登记，因此这一步同时证明「上传的三份 == 登记的三份」。
    resolver = None
    run_input_binding: dict | None = None
    if run_input is not None:
        declared = CRI.registered_documents(REPO / "data" / "evidence.db", subject)
        try:
            resolver = CRI.RunInputResolver.from_dir(run_input, declared=declared)
        except CRI.CitedRunInputError as exc:
            raise SystemExit(f"运行输入无法建立（fail-closed，不回退到登记路径）：{exc}") from exc
        if resolver.binding.run_id != str(run_id):
            raise SystemExit(
                f"运行输入的 run_id 是 {resolver.binding.run_id!r}，本次运行是 {run_id!r}："
                "不接受把另一次上传的字节当作本 run 的输入")
        run_input_binding = resolver.binding.to_dict()
        print(f"运行输入已核验：{len(resolver.binding.documents)} 份上传对象"
              f"（清单指纹 {resolver.binding.manifest_sha256[:16]}…）")

    #: **财务输入**：同样在建立结果目录**之前**核验。它证明的不是「上传了三个 xlsx」，而是
    #: 「本次上传的三份 == 财务库里当前有效快照声明的三条 `source_version`，逐份同字节」。
    #: 先取当前有效快照、再要求上传与它逐份对上；反过来（先信上传、再找一条"差不多"的快照）
    #: 会把「A 快照被 B 快照顶替」写成一次成功的核对。
    financial_binding = None
    if financial_input is not None:
        try:
            current_snapshot = CFI.current_snapshot_identity(
                fin_db, subject=subject, subject_name=subject_name)
            declared_sources = CFI.declared_financial_sources(
                fin_db, version_ids=[v for v, _ in current_snapshot.source_versions])
            financial_binding = CFI.load_financial_input(
                financial_input, declared=declared_sources, snapshot=current_snapshot)
        except CFI.CitedFinancialInputError as exc:
            raise SystemExit(f"财务输入无法核验（fail-closed，不回退到旧快照或 data/samples）："
                             f"{exc}") from exc
        if financial_binding.run_id != str(run_id):
            raise SystemExit(
                f"财务输入的 run_id 是 {financial_binding.run_id!r}，本次运行是 {run_id!r}："
                "不接受把另一次上传的字节当作本 run 的财务输入")
        print(f"财务输入已核验：{len(financial_binding.sources)} 份上传 XLSX 与快照 "
              f"{financial_binding.snapshot.snapshot_id} 的 source_versions 逐份同字节"
              f"（绑定指纹 {financial_binding.binding_sha256[:16]}…）")
    elif mode != ACC.MODE_OFFLINE:
        #: 本批的**真实**运行一律是双节的：一次性授权把财务快照身份与三条来源哈希也绑进去了，
        #: 因此一份真实凭据在结构上就带着财务那一栏。缺上传时这里停住，而不是让链在授权门那里
        #: 报一句「没有财务输入」——那会把一件**本批范围**的事说成一个链上的缺件。
        raise SystemExit(
            "真实模式必须给出 `--financial-input`：本批的真实运行是双节的，一次性授权同时绑定"
            "公司节的三份上传 PDF 与财务节的三份上传 XLSX + 被复用的权威快照身份。"
            "没有上传，「本次上传核对并复用的是同字节的已建立权威快照」就没有可核对的对象。"
            "本 run 在第一个请求之前拒绝，不拿库里现成的一份顶替")

    #: **一次性授权门**：真实模式在第一个模型请求之前必须持有一份逐字段对上的批准凭据。
    #: 预算门管的是「上限」，本门管的是「这一次有没有被批过」——两者都不能代替对方。
    #: 顺序上它排在运行输入之后：授权绑定了三份上传对象的哈希，所以要先能把输入读出来才谈
    #: 得上比对；消费又排在**所有静态判据之后、建目录之前**，这样一次消费对应的是一次真的
    #: 会跑起来的运行，而不是一次「因为别的原因失败」的空转。
    if mode != ACC.MODE_OFFLINE:
        authorization_root = (Path(authorization_root) if authorization_root is not None
                              else CRA.default_root(REPO))
        try:
            authorization = CRA.load_authorization(authorization_root, run_id=run_id)
            live = CRA.describe_live(
                run_id=run_id, mode=mode, subject=subject, section_ids=section_ids,
                model=approved_model,
                policy=CB.cited_call_budget_policy(approved_model=approved_model),
                binding=resolver.binding if resolver is not None else None,
                financial_binding=financial_binding, profile=profile)
            CRA.check_authorization(authorization, live=live)
            #: 消费也在这一个 `try` 里：一份**已经被用掉**的凭据要和「没有凭据」走出同一种
            #: 拒绝形态——带原因的 `SystemExit` 加一条可读留痕，而不是一个没接住的异常。
            consumed = CRA.consume_authorization(authorization_root, authorization)
        except CRA.CitedAuthorizationError as exc:  # noqa: BLE001 - 事前拒绝，带着原因停
            CRA.write_refusal_trace(run_input=run_input, run_id=run_id,
                                    stage="run_authorization", reason=str(exc))
            raise SystemExit(
                f"真实模式缺少可用的一次性运行授权，停止在第一请求之前：{exc}") from exc
        print(f"一次性授权已核验并消费：{authorization.describe()}")
        print(f"消费标记：{consumed}")

    run_dir.mkdir(parents=True)
    if run_input_binding is not None:
        _write_json(run_dir / CRI.BINDING_NAME, run_input_binding)
    if financial_binding is not None:
        #: 落一份**副本**在运行目录里：读者据此在同一个目录内就能看到「这次财务节读的是哪一期
        #: 快照的哪几份同字节来源」，不必另去上传目录找。它是副本，原件仍是上传目录里那一份。
        _write_json(run_dir / CFI.BINDING_COPY_NAME, financial_binding.to_dict())

    journal = CRJ.RunProgressJournal(run_dir, run_id=run_id)
    if resolver is not None:
        journal.record("run_input_verified", "completed",
                       detail=(f"{len(resolver.binding.documents)} 份上传对象；清单指纹 "
                               f"{resolver.binding.manifest_sha256[:16]}…"))
    if financial_binding is not None:
        journal.record("financial_input_verified", "completed",
                       detail=(f"{len(financial_binding.sources)} 份上传 XLSX；快照 "
                               f"{financial_binding.snapshot.snapshot_id}"))

    clock = RC.capture_report_clock()
    declaration = ACC.DeclaredReportInput(
        subject_id=subject, subject_name=subject_name, report_as_of=clock.report_as_of)
    print(f"主体声明：{subject}／{subject_name}；report_as_of={clock.report_as_of}")

    #: 研究侧一律走 `MODE_OFFLINE`：本批**不重做研究、不外部检索**，输入从已核实的当前 Pack 与
    #: 财务权威现读。`--mode real` 换的只是**写作/审阅**两个客户端与它们头上那道门，研究侧的
    #: 装配逐字不变——把 `MODE_REAL` 交给 `RealEnvironment` 会真的发起研究调用，那是本批
    #: 明确没有获批的事（`evals/test_m930_3_real_assembly_smoke` 已实测过这一点）。
    gate = ACC._call_budget(mode=ACC.MODE_OFFLINE)
    previous = LB.install(gate)
    #: `--mode offline`：装上断网 guard —— 「没发请求」不靠假设，被碰到就当场失败。
    #: `--mode real`：**不装**它（本批获批的正是真实的写作与审阅两次调用），改由**预算门**保证
    #: 「只发了批过的那几次」：份数写死、模型写死、重试为 0。
    guard = _NetworkGuard() if mode == ACC.MODE_OFFLINE else None
    sections: list[dict] = []
    #: 两条「本轮未完成」的读法在 `try` 之外**先声明**：成功分支会重写它们，失败分支原样抛出。
    #: 预先给 `None` 是为了让「读到未初始化变量」不可能冒充成一条判据。
    incomplete_failure: dict | None = None
    missing_failure: dict | None = None
    #: 失败记录里要写「哪一节把它停住了」，因此节号在进入该节**之前**就记下（不是失败后回填）。
    context: dict[str, str] = {"section_id": ""}
    try:
        with guard if guard is not None else _no_guard():
            with ACC.RealEnvironment(clock=clock, mode=ACC.MODE_OFFLINE,
                                     declaration=declaration,
                                     source_path_resolver=resolver) as env:
                inputs = env.inputs
                entries = inputs.source_manifest.source_set_entries()
                journal.record("environment_built", "completed",
                               detail=(f"主体 {inputs.company_id}；源集 {len(entries)} 份成员"))
                # 整份文档的逐表矩阵要用**全部**成员的 live 快照，而不只是被消费的那些；
                # 这里按同一份成员身份再签发一次（同入口、同输入）。它是**文档级**读数，
                # 与节无关，因此整批只算一次、只落一份。
                lives = _member_lives(company_id=inputs.company_id, entries=entries,
                                      resolver=resolver)
                table_matrix = build_table_proof_matrix(lives, entries)
                _write_json(run_dir / "table_proof_matrix.json", table_matrix)
                journal.record("table_proof_matrix", "completed",
                               detail=f"{len(entries)} 份成员")
                #: **快照漂移再核一次**：核验发生在建立结果目录之前，而财务事实是在这之后
                #: 装配的。若期间 current 指针换了或快照被隔离，前半句「本次复用的是那份快照」
                #: 就不成立——本 run 拒绝继续，而不是拿一个已经漂掉的权威写完财务节。
                if financial_binding is not None:
                    try:
                        CFI.verify_snapshot_unchanged(fin_db, binding=financial_binding)
                    except CFI.CitedFinancialInputError as exc:
                        raise SystemExit(f"财务快照在装配期间发生漂移，停止在第一请求之前："
                                         f"{exc}") from exc
                    journal.record("financial_snapshot_rechecked", "completed",
                                   detail=financial_binding.snapshot.snapshot_id)
                for section_id in section_ids:
                    context["section_id"] = section_id
                    journal.record("section_started", "started", section_id=section_id)
                    #: 本节要呈现的 topic 集合**逐字来自本节任务**（= profile 为这一节选中的
                    #: 那一片），入口不再自带缺省 topic：`fin_source_scope` 就是被那一层缺省
                    #: 悄悄跳过的。
                    topic_ids = _resolve_topic_ids(
                        inputs=inputs, section_id=section_id,
                        topic_ids=(topics or {}).get(section_id))
                    cited_ids, deterministic_ids = _split_cited_and_deterministic(
                        section_id=section_id, topic_ids=topic_ids)
                    #: 本节产物目录在**写作之前**就定下来：留存簿要落在同一个目录里，
                    #: 而它必须在调用**前**就能写（否则失败时又只剩 `logs/llm` 那一份）。
                    section_dir = (run_dir / section_id) if multi else run_dir
                    payload = _build_cited_payload(
                        inputs=inputs, section_id=section_id, topic_ids=topic_ids,
                        cited_topic_ids=cited_ids, deterministic_topic_ids=deterministic_ids,
                        mode=mode, approved_model=approved_model, budget=cited_gate,
                        journal_dir=section_dir, stage_journal=journal)
                    emitted = _emit_section(
                        out_dir=section_dir,
                        run_id=run_id, section_id=section_id,
                        topic_id=(cited_ids[0] if cited_ids else ""),
                        inputs=inputs, entries=entries, profile=profile, subject=subject,
                        subject_name=subject_name, clock=clock, payload=payload,
                        table_matrix=table_matrix, resolver=resolver)
                    journal.record("section_emitted", "completed", section_id=section_id,
                                   detail=f"{len(emitted['source_display'].regions)} 个原 PDF 区域")
                    print(f"节 {section_id}／topic {list(topic_ids)} 写出 {emitted['out_dir']}"
                          f"（正文 {list(cited_ids) or '无'}，确定性呈现 "
                          f"{list(deterministic_ids) or '无'}）")
                    sections.append({"section_id": section_id, "topic_ids": list(topic_ids),
                                     "topic_id": (cited_ids[0] if cited_ids else ""),
                                     "payload": payload, **emitted})
        if guard is not None and guard.attempts:
            raise SystemExit(f"离线读回期间发生了 provider 调用：{guard.attempts}")
    except BaseException as exc:
        # 失败**也要落账**：账本存在的理由恰恰是「这一轮失败了」，而异常逃逸正是它此前
        # 整份丢失的那条路径（已占额的调用只在 `logs/llm` 留痕，运行目录里什么都没有）。
        # 这里只做两件事：写账本、打印一行；原异常**原样**再抛（不回吞、不换成别的异常）。
        failure = _failure_record(exc, section_id=context["section_id"])
        journal.record("run_failed", "failed", section_id=context["section_id"],
                       detail=f"{failure['error_type']}")
        try:
            summary = _write_cited_call_ledger(run_dir=run_dir, cited_gate=cited_gate,
                                               approved_model=approved_model,
                                               failure=failure)
        except Exception as write_exc:  # noqa: BLE001 - 落账失败不得顶掉原异常
            summary = None
            print(f"警告：失败账本未能落盘（{type(write_exc).__name__}: {write_exc}）；"
                  "原异常照常抛出")
        _print_cited_ledger(summary, failure=failure)
        raise
    else:
        # 「跑完了」与「跑成功了」不是同一件事：审阅被一个格式/调用错误挡住时，产物**全部**
        # 照旧落盘（含降级诊断预览），但账本必须记 `failed`、退出码必须非零。否则一次
        # 半途而废的 run 会因为"文件都在"而被当成完成品——真实 r28 的教训正是这个方向：
        # 它失败得太彻底（什么都没留下），修正不能走到另一个极端（失败也报成功）。
        incomplete = [(s["section_id"], str(s["payload"].get("review_failure") or ""))
                      for s in sections if s["payload"].get("review_failure")]
        incomplete_failure = _incomplete_review_failure(incomplete=incomplete,
                                                        cited_gate=cited_gate)
        #: 「每一节都产出了吗、财务节的 A2 落盘了吗」是**另外**一条判据，不能由
        #: 「审阅有没有报错」代替：一次 run 完全可能在**没有** review_failure 的情况下少一节
        #: （比如某一节被跳过）。**没有这一条，`run_outcome=completed` 会与「双节都在」脱钩**——
        #: 页面于是会对着一次缺节的运行显示双节成功。它排在账本判 `run_outcome` **之前**。
        missing_failure = _missing_section_failure(
            requested=tuple(section_ids),
            emitted=tuple(str(s["section_id"]) for s in sections),
            run_dir=run_dir, multi=multi)
        summary = _write_cited_call_ledger(run_dir=run_dir, cited_gate=cited_gate,
                                           approved_model=approved_model,
                                           failure=(incomplete_failure
                                                    or missing_failure))
        _print_cited_ledger(summary, failure=(incomplete_failure or missing_failure))
    finally:
        LB.uninstall()
        if previous is not None:
            LB.install(previous)

    if multi:
        # ---- 根页：多节并排的**一页**（各节各自的版本锚分别列出） ------------------
        (run_dir / DEMO_PAGE_FILENAME).write_text(
            _render_multi_section_page(run_id=run_id, subject_name=subject_name,
                                       sections=sections, run_dir=run_dir),
            encoding="utf-8")

    print(f"写出 {run_dir}")
    run_failure = incomplete_failure or missing_failure
    if run_failure is not None:
        print(f"本轮**未完成**：{run_failure['error']}")
        return 1
    return 0


@contextlib.contextmanager
def _no_guard():
    """`--mode real` 的占位上下文：**不**拦截 provider 调用（那是本批获批的事），
    但也**不**意味着没有约束——约束在预算门上（类别上限 1/节、整轮 4、模型写死、重试 0）。"""
    yield None


def _resolve_topic_ids(*, inputs, section_id: str,
                       topic_ids: Sequence[str] | None = None) -> tuple[str, ...]:
    """本节本次要呈现的**全部** topic，按 profile 的次序。

    唯一来源是**本节任务自己的 `topic_ids`**——它就是 profile 为这一节选中的 topic 集合
    （`planning.demo_scope` 由 `selected_topics` 切出每节那一片）。不设「入口自带一个缺省
    topic」这一层：那正是本批要修的缺陷——财务节的入口缺省曾是 `fin_solvency`，于是 profile
    选中的 `fin_source_scope` 在**没有任何读回行**的情况下被跳过，读者面看不出少了什么。

    显式给出的 `topic_ids` 必须**逐项等于**本节选中的集合：少一项就是静默缩小已选范围，多一项
    就是写一节没有选过的内容，两种都不接受（要改范围就改 profile，不在这里放宽）。
    """
    declared = tuple(str(t) for t in (getattr(inputs.tasks[section_id], "topic_ids", ()) or ()))
    if not declared:
        raise SystemExit(
            f"节 {section_id!r} 的任务没有声明任何 topic：没有选中的 topic 就没有要呈现的内容，"
            "本链拒绝以空范围开跑")
    if topic_ids is None:
        return declared
    asked = tuple(str(t) for t in topic_ids)
    if sorted(asked) != sorted(declared):
        missing = sorted(set(declared) - set(asked))
        extra = sorted(set(asked) - set(declared))
        raise SystemExit(
            f"节 {section_id!r} 本次 profile 选中的 topic 是 {list(declared)}，"
            f"`--topic` 给的是 {list(asked)}（缺 {missing}，多 {extra}）："
            "静默跳过已选 topic 正是本链要防的事；要改范围请改 profile，不在入口缩小它")
    return declared


def _split_cited_and_deterministic(*, section_id: str,
                                   topic_ids: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """把本节的 topic 分成「写正文的」与「确定性呈现的」两拨。

    写正文的至多一个（`MAX_CITED_TOPICS_PER_SECTION`）。**不**在超过一个时挑一个写：那会让
    另一栏在读者面上凭空消失，而且消失的理由不落在任何产物上。
    """
    cited: list[str] = []
    deterministic: list[str] = []
    for topic_id in topic_ids:
        (deterministic if topic_id in DETERMINISTIC_TOPIC_PRESENTERS else cited).append(topic_id)
    if len(cited) > MAX_CITED_TOPICS_PER_SECTION:
        raise SystemExit(
            f"节 {section_id!r} 选中了 {len(cited)} 个需要写正文的 topic {cited}："
            f"本链一节至多写 {MAX_CITED_TOPICS_PER_SECTION} 栏正文，多于一个时必须另行裁决"
            "（一栏一份清单与各自的身份，不能拼成一份）")
    return tuple(cited), tuple(deterministic)


def _failure_record(exc: BaseException, *, section_id: str) -> dict:
    """一次运行失败的最小可复核记录：异常身份、把它停住的节、以及那次调用的 `call_id`。

    `call_id` 直接取自异常（`LLMTruncatedResponse.response.call_id`），**不**参与任何求和、
    也不用来推断「发了几次」——它只有一件事要做：把 `logs/llm` 里的那一条原始日志与账本里
    的那一条尝试对上。取不到就留空串，不编一个。
    """
    return {"error_type": type(exc).__name__, "error": str(exc),
            "section_id": str(section_id or ""),
            "call_id": str(getattr(getattr(exc, "response", None), "call_id", "") or "")}


#: `CitedReviewError.reason` → `CITED_REVIEW_FAILURE_KINDS` 的**唯一**映射。
#:
#: 它存在的意义是让"审阅没跑完"这条读数**有类型**：读者要能分清"回复根本不是一个 JSON"、
#: "回复是 JSON 但不合约"、"这次调用本身失败了"三件不同的事——它们的下一步动作完全不同
#: （改提示词 / 改协议 / 查 provider）。没登记的 reason 一律落到 `review_reply_unparsable`，
#: 而不是被丢掉：多一条可归类的失败也好过一条无声的失败。
#:
#: 键是 `CitedReviewError.reason` —— 即 `sections/cited_review.py` **抛错时写下的那个字符串**，
#: 不是本表的值。两侧名字相近（`response_not_json` vs `review_reply_not_json`）正是此前出错的
#: 地方：这一侧曾登记 `"review_reply_not_json"`，而解析口实际抛的是 `response_not_json`，
#: 于是「回复根本不是 JSON」被记成了「回复不合约」——下一步动作完全不同（改提示词 vs 改协议）
#: 的两件事，读数上却长得一样。
_REVIEW_FAILURE_BY_REASON = {
    #: 解析期：`json.loads` 失败（`cited_review.parse_cited_review` 的 `response_not_json`）。
    "response_not_json": "review_reply_not_json",
    #: 解析期：回复为空 —— 它是**不合约**（没有可解析的意见），不是"不是一个 JSON 值"。
    "empty_response": "review_reply_unparsable",
    #: 调用期：这一次调用本身失败了（`review_cited_prose` 拿到 `status != "ok"`）。
    "review_call_failed": "review_call_failed",
}


def _review_no_object_failure(draft) -> str:
    """审阅**对象为空**时的原因码（空串 = 有可审的句子，正常往下走）。

    一句带引用的话都没有时，逐句覆盖等式在数学上无意义（0 == 0），一次审阅调用也审不出
    任何东西。这里**直接不发调用**：既省下一次真实调用额度，也让"没有可审对象"与"审阅跑了
    但失败"分成两条不同的读数。
    """
    return "" if any(s.citations for s in draft.sentences()) else "review_no_reviewable_sentence"


def _review_failure_kind(exc: BaseException) -> str:
    """审阅失败 → typed 原因码（`:data:`sections.cited_report.CITED_REVIEW_FAILURE_KINDS``）。

    `AssuranceSchemaError` 没有 `reason` 字段：它来自 `ReviewIssue` 的构造期约束（真实 r28 撞的
    正是 `ReviewIssue.reason 不得为空字符串`），形状上就是"回复不合约"。

    `LLMTruncatedResponse` 也没有 `reason`，但它**不是**"回复不合约"：provider 明确说了这次输出
    是被截断的，半截 JSON 连"一个回复"都不算。它归 `review_call_failed`——封闭词表里这一档
    本来就写着"provider 错误、截断"。不在这里特判的话，它会顺着默认值落成"不合约"，
    把一次**调用**失败说成一次**协议**问题，下一步就查错了地方。
    """
    if isinstance(exc, LLC.LLMTruncatedResponse):
        return "review_call_failed"
    reason = str(getattr(exc, "reason", "") or "")
    return _REVIEW_FAILURE_BY_REASON.get(reason, "review_reply_unparsable")


def _review_failure_detail(exc: BaseException) -> dict:
    """审阅失败时**落进运行目录**的受控诊断（`crr-9`）。

    此前失败原因只 `print` 到 stdout：`review_issues.json` 里只剩一个原因码，运行目录的读者
    看不到**是哪一条意见**、**哪一句 / 哪个引用**出的问题，也回不到原始回复。真实
    `cp-20` 公司节正是如此——`review_reply_unparsable` 摆在那里，而"`ReviewIssue.category`
    得到空串"这句话只活在终端里。

    这里只记**受控诊断**：异常类型、异常消息、以及异常自己带的定位。`CitedReviewError` 从
    `crr-9` 起为意见级的失败带上 `sentence_id` / `citation_id`（见
    `sections.cited_review._issue_from_payload`）。**不保存隐藏推理**，也不保存原始回复正文
    ——原始可见回复本来就有自己的落点（`logs/llm/<时间戳>__<call_id>.jsonl`），这里只负责让
    运行目录能**指到**它。
    """
    return {
        "error_type": type(exc).__name__,
        "message": str(exc),
        "sentence_id": str(getattr(exc, "sentence_id", "") or ""),
        "citation_id": str(getattr(exc, "citation_id", "") or ""),
    }


def _incomplete_review_failure(*, incomplete: Sequence[tuple[str, str]],
                               cited_gate) -> dict | None:
    """「审阅没跑完」这条失败记录（没有没跑完的节时返回 `None`）。

    正文、逐句机械核对、缺口与降级诊断预览照旧落盘；这一条只负责把「本轮不计为完成」这件事
    连同**可回查的调用身份**写进账本。`call_id` 取该节**审阅**那次调用（见
    :func:`_review_attempt_call_id`）：读者凭运行目录里这一份文件就能回到
    `logs/llm/<时间戳>__<call_id>.jsonl` 读原始可见回复。
    """
    if not incomplete:
        return None
    return {
        "error_type": "CitedReviewIncomplete",
        "error": ("独立审阅未完成：" + "、".join(f"{sid}（{kind}）"
                                             for sid, kind in incomplete)
                  + "——正文、逐句机械核对、缺口与降级诊断预览已落盘，"
                    "但本轮不计为完成"),
        "section_id": incomplete[0][0],
        "call_id": _review_attempt_call_id(cited_gate, section_id=incomplete[0][0]),
    }


def _missing_section_failure(*, requested: Sequence[str], emitted: Sequence[str],
                             run_dir: Path, multi: bool) -> dict | None:
    """「请求要写的节没全产出」或「财务节的 A2 没落盘」这条失败记录（都齐了返回 `None`）。

    它补的是**一条独立的缺口**：`run_outcome` 由账本算，而账本只知道「有没有调用失败」，
    不知道「该产出的节产出了没有」。少了这一条判据，一次**缺节**的运行照样会被读成 completed。

    两件事一起判、但**分开写**在 `error` 里：

    * 缺节：请求了 `requested`，实际只发出了 `emitted`；
    * 缺 A2：财务节请求了，却找不到本节的 `cited_balance_structure__<topic>.json`
      （确定性呈现，零模型调用；它**不**经过审阅，因此不会被 `review_failure` 覆盖到）。

    A2 的路径与 :func:`_emit_section` 的落盘规则同源：多节时落 `run_dir/<section>/`，
    单节时落 `run_dir/` 根。这里用**同一条** `multi` 判据推导，不另立一套。
    """
    emitted_set = {str(s) for s in emitted}
    absent = [str(s) for s in requested if str(s) not in emitted_set]
    parts: list[str] = []
    if absent:
        parts.append("请求的节未全部产出：" + "、".join(absent)
                     + f"（实际产出 {sorted(emitted_set)}）")
    if "financial" in {str(s) for s in requested} and "financial" not in absent:
        section_dir = (run_dir / "financial") if multi else run_dir
        a2 = section_dir / f"cited_balance_structure__{BALANCE_STRUCTURE_TOPIC}.json"
        if not a2.is_file():
            parts.append(f"财务节的确定性 A2 呈现未落盘：{a2}（零模型调用的资产负债结构栏；"
                         "它不经过审阅，因此不会被审阅失败记录覆盖到）")
    if not parts:
        return None
    return {
        "error_type": "CitedSectionsIncomplete",
        "error": "；".join(parts) + "——本轮**不计为完成**，也不得显示为双节成功",
        "section_id": absent[0] if absent else "financial",
        "call_id": "",
    }


def _review_attempt_call_id(cited_gate, *, section_id: str) -> str:
    """本轮该节**审阅**那次调用的 `call_id`（对不上就空串）。

    「审阅未完成」有两种成因，账本里的形状**不同**：

    * 调用本身失败（provider 错、回复被截断）——账本里是一条 `status="error"` 的占额记录；
    * 调用成功、**回复不合约**（cp-14 的 `review_reply_unparsable` 正是这一类）——账本里那条
      记录的 `status` 是 `"ok"`，因为请求确实成功了，不合约是回来之后才判出来的。

    两种情况下 `call_id` 都躺在账本上，可失败记录此前一律写成空串：读者只做得一次
    「运行目录 → 账本当次记录 → `logs/llm/<时间戳>__<call_id>.jsonl`」的 join 才够得着原始
    回复。把它抄进来，是为了「失败也保留可回查的原始可见回复」这条要求只凭运行目录就能兑现。

    **不编 ID**：账本里没有这一节的审阅尝试（离线替身、或失败发生在审阅之前的环节）时返回
    空串——空串的读数是「这次没有可指的审阅调用」，不能用一个凑出来的 ID 冒充。
    """
    if cited_gate is None:
        return ""
    try:
        summary = cited_gate.summary()
    except Exception:                                                # noqa: BLE001
        return ""
    for row in reversed(list(summary.get("attempts") or ())):
        if (str(row.get("category") or "") == "cited_prose_review"
                and str(row.get("section_id") or "") == section_id):
            return str(row.get("call_id") or "")
    return ""


def _draft_sentence_pairs(draft) -> dict[str, tuple[str, tuple[str, ...]]]:
    """草稿每句的 `(正文, 引用键元组)`。**只**这两个面——不含段落与行序。

    与 `sections.cited_rework._sentence_pairs` 同一个定义，这里单独写一份是因为读回页要在
    **落盘之后**重算一次：读回若复用返修模块的内部读数，那读回就不再是独立的一遍。
    """
    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    for subsection in draft.subsections:
        for paragraph in subsection.paragraphs:
            for sentence in paragraph.sentences:
                out[sentence.sentence_id] = (sentence.text,
                                             tuple(str(c) for c in sentence.citations))
    return out


def _render_rework_quality_readback(payload: dict) -> list[str]:
    """§5.6：返修结果的**质量**读数——与 §5.5 的**去向**读数是两件事。

    §5.5 回答「这一句还在不在、落在哪」；§5.6 回答「值不值得」。三栏：

    1. **保留与删除**：整稿层面按 `(正文, 引用)` 多重集对账，逐条列出新稿里多出来的与少掉的
       正文——「删掉了哪些有价值的句子」必须看得见，否则「13 条硬错减到 0」可以用「删掉半节」
       换到；
    2. **每次改栏**：旧栏 → 新栏 + 该句引用的**登记栏目集** + 机械判定（新栏是否落在登记集
       内），以及一列**显式的「是否符合句义：未判」**——这一列是刻意的：句义是否贴合需要人读，
       机器只能判「新栏认不认这句话的来源」，把它写成判定就是拿一个弱判据冒充另一个；
    3. **仍未被回答的 Contract 栏**：本节声明过的 aspect 里，没有任何一句话所在段落申报过的
       那些。它们在**生效稿**上算，因此「返修之后还剩几栏没答」是一次真实读数。

    **不**把三栏合成一个分数：本页到此为止没有任何「返修质量 = X」。离线替身曾把句子挪到同时
    声明七个栏目的段落并撤下七句，那只能证明机械接口可工作。
    """
    outcome = payload.get("outcome")
    rework = payload.get("rework_outcome")
    manifest = payload.get("manifest")
    if outcome is None or manifest is None:
        return []
    base_pairs = _draft_sentence_pairs(outcome.draft)
    L = ["## 5.6 返修结果的质量读数（**不是**验收，也不合成一个分数）", ""]
    if rework is None or rework.draft is None:
        L += ["- 本次没有可对账的返修稿（没有发起返修，或返修失败没有产出可解析的新稿）："
              "下面三栏都不适用。**生效稿仍是初稿**，其去向见 §5.5。", ""]
        return L

    new_pairs = _draft_sentence_pairs(rework.draft)

    # ---- 5.6.1 保留与删除（整稿**多重集**对账） ----------------------------
    # 多重集而不是集合：同一段文字在初稿里出现两次时，新稿只留一次也是「少了一句」。
    pool = list(new_pairs.values())
    removed: list[tuple[str, tuple[str, tuple[str, ...]]]] = []
    for sid, pair in base_pairs.items():
        if pair in pool:
            pool.remove(pair)
        else:
            removed.append((sid, pair))
    removed_ids = {sid for sid, _ in removed}
    kept_from_base = [sid for sid in base_pairs if sid not in removed_ids]
    base_pool = list(base_pairs.values())
    added: list[str] = []
    for sid, pair in new_pairs.items():
        if pair in base_pool:
            base_pool.remove(pair)
        else:
            added.append(sid)
    L += ["### 5.6.1 保留了哪些、删掉了哪些（整稿层面，按 `(正文, 引用)` 逐字对账）", ""]
    L += [f"- 初稿 {len(base_pairs)} 句 → 返修稿 {len(new_pairs)} 句；"
          f"**逐字保留 {len(kept_from_base)} 句**、**少掉 {len(removed)} 句**、"
          f"**新出现 {len(added)} 句**。", ""]
    if removed:
        L += ["**少掉的句子**（新稿里 `(正文, 引用)` 不再逐字出现；"
              "「少掉」只说明它不在了，**不**说明删得对不对）：", "",
              "| 初稿句 | 引用 | 正文 |", "|---|---|---|"]
        for sid, pair in removed:
            L.append(f"| `{sid}` | "
                     f"{'、'.join('`' + k + '`' for k in pair[1]) or '—'} | "
                     f"{_md_cell(pair[0])} |")
        L += ["",
              "**要人读的是这一列**：上面每一句里有没有**有价值但赶不回来**的内容。"
              "一次返修若靠删掉半节换到「零硬错」，本表就是它唯一的痕迹。", ""]
    if added:
        L += ["**新出现的句子**（初稿 `(正文, 引用)` 多重集里没有的新内容）：", "",
              "| 新稿句 | 引用 | 正文 |", "|---|---|---|"]
        for sid in added:
            pair = new_pairs[sid]
            L.append(f"| `{sid}` | "
                     f"{'、'.join('`' + k + '`' for k in pair[1]) or '—'} | "
                     f"{_md_cell(pair[0])} |")
        L.append("")

    # ---- 5.6.2 每次改栏：只判「登记认不认」，句义**未判** -------------------
    moved = [row for row in (rework.sentence_dispositions or ())
             if row.disposition == "kept" and row.final_sentence_ids]
    L += ["### 5.6.2 每次改栏：机械判定 vs 句义判定", ""]
    if not moved:
        L += ["- 本次没有可对账的改栏（没有点名句留在返修稿里）。", ""]
    else:
        L += ["- 下表只做**一个**机械判定：该句引用的材料在**登记侧**有没有认领到它所在段落的"
              "栏目（`scp-7` 的 `aspect_attribution` 轴，与逐句硬核对同一个判据）。"
              "**「改栏是否符合句义」这一列一律是「未判」**：它需要人读那句话，机器给不了。"
              "把「登记一致」当成「改对了」，是用一个弱判据替掉一个强判据。", ""]
        L += ["| 句 | 引用 | 它引用的材料登记在 | 返修稿段落申报的栏目 | 机械判定 | 是否符合句义 |",
              "|---|---|---|---|---|---|"]
        for row in moved:
            fid = row.final_sentence_ids[0]
            pair = new_pairs.get(fid)
            if pair is None:
                continue
            keys = pair[1]
            registered = SC.registered_aspects(manifest=manifest, citation_keys=keys)
            declared = _paragraph_aspects_for(rework.draft, fid)
            ok = set(registered) & set(declared)
            L.append(
                f"| `{row.base_sentence_id}` → `{fid}` | "
                f"{'、'.join('`' + k + '`' for k in keys) or '—'} | "
                f"{'、'.join('`' + a + '`' for a in registered) or '（登记侧没有认领任何栏目）'} | "
                f"{'、'.join('`' + a + '`' for a in declared) or '—'} | "
                f"{'落栏在登记集内' if ok else '**落栏不在登记集内**'} | **未判（需人工）** |")
        L += ["",
              "- 段落申报的栏目数本身是一条读数：一次返修把句子挪进一个**同时申报了很多栏目**"
              "的段落，机械判定会全绿，而句义上它可能只回答了一栏。"
              f"本次落栏最多的一句申报了 **{_max_declared_columns(rework.draft, moved)}** 栏。",
              ""]

    # ---- 5.6.3 仍未被回答的 Contract 栏 ------------------------------------
    declared_aspects: list[str] = []
    for subsection in manifest.subsections:
        for aspect in subsection.declared_aspect_ids:
            if aspect not in declared_aspects:
                declared_aspects.append(aspect)
    claimed = set()
    for subsection in rework.draft.subsections:
        for paragraph in subsection.paragraphs:
            if paragraph.sentences:
                claimed |= {str(a) for a in getattr(paragraph, "aspect_ids", ()) or ()}
    unanswered = [a for a in declared_aspects if a not in claimed]
    L += ["### 5.6.3 仍未被回答的 Contract 栏（在**生效稿**上算）", "",
          f"- 本节 Contract 声明 **{len(declared_aspects)}** 栏；生效稿里有正文的段落申报了 "
          f"**{len(declared_aspects) - len(unanswered)}** 栏；**仍未回答 "
          f"{len(unanswered)}** 栏。", ""]
    if unanswered:
        L += ["| 仍未回答的栏 |", "|---|"]
        L += [f"| `{a}` |" for a in unanswered]
        L += ["",
              "「未被任何有正文的段落申报」**不**等于「这一栏没有内容可写」：它也可能意味着"
              "材料、导航或授权没到位。两者的区别见 §2 与逐句核对表，不在这里下结论。", ""]
    return L


def _paragraph_aspects_for(draft, sentence_id: str) -> tuple[str, ...]:
    """这一句所在段落申报的栏目。找不到（不该发生）返回空 tuple，不猜。"""
    for subsection in draft.subsections:
        for paragraph in subsection.paragraphs:
            if any(s.sentence_id == sentence_id for s in paragraph.sentences):
                return tuple(str(a) for a in getattr(paragraph, "aspect_ids", ()) or ())
    return ()


def _max_declared_columns(draft, rows) -> int:
    """这些句中，所在段落申报栏目的**最大**数量。空集合返回 0。"""
    counts = [len(_paragraph_aspects_for(draft, row.final_sentence_ids[0]))
              for row in rows if row.final_sentence_ids]
    return max(counts, default=0)


def _rework_retention_note(outcome, *, gate: str, has_base: bool) -> str:
    """三个终态各自**留了什么**。一句话写清，读回时不必从文件布局反推。

    `reworked` 不是「正文好了」，`failed` 也不是「这一节没了」——后者留下的是一份**有效草稿**
    （初稿）+ 它的逐句核对 + 返修为什么没成。把两种终态都写成「返修没成功」，会让一次真实
    尝试的全部证据消失。

    `has_base` 是**调用方实测出来的事实**，不是修辞：`sentence_checks_initial.json` 只在
    「生效读数 ≠ 初稿读数」时才落盘。返修成功时生效读数是新稿的（≠ 初稿），所以它存在；
    返修失败时生效读数**就是**初稿本身，所以它**不存在**。正文里只能提真正存在的文件——
    否则读回的人会去找一份没有的文件，把「没落盘」误读成「证据丢了」。
    """
    base_note = ("它的逐句核对在 `sentence_checks.json`（与预览同源）与 "
                 "`sentence_checks_initial.json`（初稿独立读数）。" if has_base else
                 "它的逐句核对在 `sentence_checks.json`（与预览同源，**就是初稿的读数**；"
                 "生效读数与初稿相同，因此没有另存初稿的独立读数文件）。")
    if outcome is None:
        return ("本段没有发起返修调用（" + (gate or "未装配请求面") + "）："
                "初稿就是有效草稿，`sentence_checks.json` 是它的读数。")
    if outcome.outcome == "reworked":
        return ("返修**成功**：原稿留在 `cited_prose.json`（逐字未改），新稿另存 "
                "`cited_prose_reworked.json`；" + base_note +
                "**有效草稿是新稿**。"
                "「成功」只表示它没有破坏原稿且改绑声明与实情相符，**不**表示新稿质量更好"
                "——新稿保留了什么、撤下了什么、每次改栏是否符合句义，见 `readback.md` §5.6。")
    if outcome.outcome == "not_needed":
        return ("**没有发起返修调用**：初稿没有被点名的句子（无机械硬错、也没有调用方给出的"
                "语义待审句）。为一个没有问题的草稿花掉一次调用是拿预算换一个恒真的动作。")
    return (f"返修**失败**（`{outcome.failure_reason or '未分类'}`）：初稿仍是**有效草稿**且"
            "逐字留在 `cited_prose.json`；" + base_note +
            "失败原因与调用身份在 `outcome.failure_reason` "
            "/ `failure_detail` / `call`；`cited_preview.md` 是**不可发布**的标记稿。"
            "失败**不**吃掉独立审阅，也**不**清零整节。")


def _write_cited_call_ledger(*, run_dir: Path, cited_gate, approved_model: str | None,
                             failure: dict | None) -> dict | None:
    """把本轮的调用账本**原子**写进运行目录——成功写，失败**也**写。

    两种模式都要落盘，且**分开写**：「离线模式没有发过真实调用」与「真实模式发过几次、每次
    属于哪个类别/哪一节」是两件不同的事，写成同一个空 dict 就等于让读者以为真实模式也没发过。
    账本里的每一次尝试都是**发请求之前**记的（失败也算占额），因此失败路径上这份账本里已经
    有那条被截断的尝试（`status="error"`）与它的 `call_id`。

    返回可打印的账本摘要（离线模式返回 `None`：本链没有可记的真实尝试）。
    """
    if cited_gate is None:
        payload: dict = {
            "budget_policy_version": CB.CITED_BUDGET_POLICY_VERSION,
            "mode": ACC.MODE_OFFLINE, "ledger": None,
            "run_outcome": "failed" if failure else "completed",
            "note": ("离线读回：本轮的写作与审阅都由替身产出，**没有**任何真实模型调用，"
                     "因此没有可记的调用账本。这一条**不**等于一次成功或失败的运行。"),
        }
        if failure:
            payload["failure"] = failure
        _write_json_atomic(run_dir / "cited_call_ledger.json", payload)
        return None
    summary = cited_gate.summary()
    payload = {"budget_policy_version": CB.CITED_BUDGET_POLICY_VERSION,
               "mode": ACC.MODE_REAL, "approved_model": approved_model,
               "run_outcome": "failed" if failure else "completed",
               "call_budget": summary}
    if failure:
        payload["failure"] = failure
    _write_json_atomic(run_dir / "cited_call_ledger.json", payload)
    return summary


def _print_cited_ledger(summary: dict | None, *, failure: dict | None) -> None:
    """账本的一行可读回执（失败时把原因与 call ID 一并打出来）。"""
    if failure is not None:
        print(f"本轮**失败**并已落账：{failure['error_type']}"
              f"（节 {failure['section_id'] or '未进入任何节'}，"
              f"call_id {failure['call_id'] or '无'}）；原异常照常抛出")
    if summary is None:
        return
    print(f"调用账本：尝试 {summary['attempt_total']} 次"
          f"（上限 {CB.CITED_RUN_MAX_ATTEMPTS}），拒绝 "
          f"{summary['refusal_total']} 次；按类别 {summary['attempts_by_category']}，"
          f"按节 {summary['attempts_by_section']}")


def _cited_run_gate(*, mode: str, model: str | None):
    """真实模式的**事前**预检：模型身份 + 四条上限能不能被共享预算实现表达。

    两件事都在**第一个请求发出之前**判：一旦请求发出，即使结果是失败，本次授权也算用掉了。
    因此这里宁可停住，也不「先发一个看看」。

    项目写作模型取**当前获批配置**（`config.LLM_MODEL`），不取别的任何来源：把 Claude Code
    自己用的模型写进来，会以「这次跑的就是项目模型」的形态污染本 run 的全部读数。
    """
    if mode == ACC.MODE_OFFLINE:
        return None, None
    from config import LLM_MODEL as PROJECT_WRITER_MODEL

    approved = str(PROJECT_WRITER_MODEL or "").strip()
    if not approved:
        raise SystemExit(
            "配置里没有已批准的写作模型（`config.LLM_MODEL` 为空）：没有模型身份就没有"
            "「谁被批准了」这一读数，真实模式在**第一个请求之前**停止")
    if model is not None and str(model).strip() != approved:
        raise SystemExit(
            f"`--model {model}` 与项目当前获批的写作模型 {approved!r} 不一致：本批只批了"
            "「按当前获批配置跑一次」，换模型属于另一次裁决")
    try:
        return CB.cited_call_budget_gate(approved_model=approved), approved
    except CB.CitedBudgetError as exc:  # noqa: BLE001 - 事前拒绝，带着原因停
        raise SystemExit(
            f"共享预算实现无法表达本批获批的四条上限，停止在第一请求之前：{exc}") from exc


def _stage(journal, stage: str, status: str, *, section_id: str = "",
           detail: str = "") -> None:
    """给**可选**阶段日志记一条（`rj-1`）。

    `journal is None` 时是一个**空动作**，不是一个静默失败：`--run-input` 之外的历史兼容
    运行（以及所有既有测试）不产生阶段日志，这条路径必须照旧可跑。阶段名与状态由
    `cited_run_journal` 的白名单把关，拼错当场抛错，不会写进一份读不回来的日志。
    """
    if journal is not None:
        journal.record(stage, status, section_id=section_id, detail=detail)


@contextlib.contextmanager
def _cited_call_scope(budget, *, section_id: str):
    """把**本节的**写作/审阅调用放进已安装的 cited 预算门里。

    安装范围只包住这两次调用，**不**包住研究侧装配：`RealEnvironment` 的装配走的是另一套
    类别（研究轴按 topic 计量），把 cited 门装在它头上会让那些尝试归不了类而当场被拒——
    「本链只批了写作与审阅」这句话不该变成「研究侧一律跑不了」。
    """
    if budget is None:
        yield
        return
    previous = LB.install(budget)
    try:
        with LB.section_scope(section_id):
            yield
    finally:
        LB.uninstall()
        if previous is not None:
            LB.install(previous)


def _presentation_routing_facts(authority, scan):
    """呈现层路由声明的输入事实：本节事实行里**带指标 code** 的那一批（按事实身份对齐）。

    `AuthorityFactEntry` 是扫描读视图，按 schema **不带**财务专属的指标 code；`code` 只写在
    权威自己的 `FinancialFactProjection` 上。直接把扫描读视图喂进路由声明，会让声明对**每一条
    本节事实**都报「没有为这个指标声明栏目」——于是 `fact_columns` 恒为空，而「这一段引的事实在
    哪一栏」这条轴在真实数据上**一次也不会触发**：产物看起来齐全，读数却是假的。

    因此这里按事实身份把两边对上，取到的仍是本节这次真的要投递的那批事实，而**不是**整只
    artifact——声明要进本节清单的身份体，也要被本节逐句核对，多带本节看不到的指标会让
    `unrouted_facts` 里出现一批本节根本没有的指标，读者无法判断那个读数意味着什么。

    本节事实行在权威投影里对不上时**跳过**（返回更少，不编一个 code）：那是另一条轴（事实身份）
    该说的话。
    """
    artifact = getattr(authority, "artifact", None)
    by_id = {str(getattr(fact, "fact_id", "") or ""): fact
             for fact in getattr(artifact, "facts", ()) or ()}
    rows = []
    for entry in getattr(scan, "facts", ()) or ():
        projection = by_id.get(str(getattr(entry, "fact_id", "") or ""))
        if projection is not None:
            rows.append(projection)
    return tuple(rows)


def _build_cited_payload(*, inputs, section_id: str, topic_ids: tuple[str, ...],
                         cited_topic_ids: tuple[str, ...],
                         deterministic_topic_ids: tuple[str, ...],
                         mode: str = ACC.MODE_OFFLINE, approved_model: str | None = None,
                         budget=None, journal_dir: Path | None = None,
                         stage_journal=None) -> dict:
    """新写作链的一次完整执行。

    输入只按**本节权威的材料边界**分流，不按节的名字猜：

    * 有 Pack 材料边界（topic Pack 权威）→ 材料正文由当前 `VerifiedPackSet` 逐份解析；
    * 无 Pack 材料边界（财务 artifact + 附注这一支）→ 空材料边界，正文只能引本权威的事实行。

    两条支路都不许「为了统一字段」伪造另一半：Pack 支不得凭空多出事实，
    无 Pack 支不得伪造一份 `VerifiedPackSet`（见 `material_context` 里那两个构造入口）。

    `mode` 只换**写作 / 审阅两个客户端与它们头上那道预算门**：`offline` 用本脚本内的确定性
    替身（替身就是替身，不算模型正文），`real` 用 `LlmCitedProseClient` /
    `LlmCitedReviewClient`——复用既有适配器，不重写 Writer/Reviewer。
    """
    if section_id not in inputs.authorities:
        raise SystemExit(
            f"本节 {section_id!r} 不在本轮权威集合 {sorted(inputs.authorities)} 内："
            "没有权威就没有材料边界与事实读视图，本链拒绝以未登记节开跑")
    authority = inputs.authorities[section_id]
    task = inputs.tasks[section_id]
    if len(cited_topic_ids) != MAX_CITED_TOPICS_PER_SECTION:
        raise SystemExit(
            f"节 {section_id!r} 本次选中 {list(topic_ids)}，其中需要写正文的是 "
            f"{list(cited_topic_ids)}：本链一节恰写一栏正文（`MAX_CITED_TOPICS_PER_SECTION`）。"
            "零栏正文的「纯确定性节」与两栏正文的节都必须另行裁决，不在本批范围内")
    topic_id = cited_topic_ids[0]
    requirement = inputs.requirements.get(topic_id)
    if requirement is None:
        raise SystemExit(
            f"topic {topic_id!r} 不在冻结 Contract 投影的需求集 "
            f"{sorted(inputs.requirements)} 内：没有栏目要求就没有正文，不伪造小节")
    pack_set = getattr(authority, "pack_set", None)
    if pack_set is not None:
        material_context = MC.resolve_writer_material_context(
            pack_set=pack_set, resolver=inputs.resolver, section_id=section_id)
    else:
        material_context = MC.empty_material_context_for_authority(authority)
    scan = PW.scan_authority(authority, task)
    #: 呈现层路由声明（`fpr-2`）：写正文的那个 topic 提供缺口账的栏目集合，**同一节里确定性呈现的
    #: topic**（`deterministic_topic_ids`）只提供「它的栏目也认得出来」——两者都由本节的真实投影
    #: 给出，不写死 `fin_solvency` 这个名字。它**不**写进任何事实的 `aspect_ids`，
    #: 只在写作清单、确定性指标表与逐句核对三处被**核对**（见三个模块的模块头）。
    presentation_routing = FPR.build_presentation_routing(
        gap_scope_topic=topic_id, requirement=inputs.requirements.get(topic_id),
        extra_route_requirements={t: inputs.requirements.get(t)
                                  for t in deterministic_topic_ids},
        facts=_presentation_routing_facts(authority, scan))
    #: 确定性呈现（各族见 `cited_source_scope` / `cited_balance_structure` 的 schema 版本常量）：
    #: 逐条读本节财务权威自己的字段或合格事实，**零模型调用**。
    #: 它不复制 `fin_solvency` 的偿债表——`fin_source_scope` 问的是「这些数值的来源与口径」、
    #: `fin_balance_structure` 问的是「资产/负债的总量与结构」，用偿债指标表充它们
    #: 等于用答案替掉问题。
    source_scopes = _build_source_scopes(
        inputs=inputs, section_id=section_id, authority=authority,
        deterministic_topic_ids=deterministic_topic_ids)
    subsections = _subsections_from_writing_spec(requirement)
    if not subsections:
        raise SystemExit(
            f"topic {topic_id!r} 在冻结 Contract 投影里没有任何带 `requirement_text` 的 aspect："
            "没有栏目就没有正文，不伪造小节")
    section_title = str(getattr(task, "title", "") or section_id)
    manifest = CW.build_cited_writer_input(
        authority=authority, material_context=material_context, subsections=subsections,
        facts=scan.facts, section_title=section_title,
        presentation_routing=(None if presentation_routing is None
                              else presentation_routing.to_dict()))

    # 写作客户端按**模式**选；离线替身再按**清单的内容**选（有材料行就摘材料原文，没有材料行
    # 但有事实行就直述事实命题）——「写得出什么」是输入面的纯函数，不需要脚本知道这一节是不是
    # 财务。真实模式用既有适配器 `LlmCitedProseClient`：不重写 Writer，只是换一个 client。
    if mode == ACC.MODE_REAL:
        prose_client = CW.LlmCitedProseClient(model=approved_model,
                                              thinking=CITED_THINKING_DISABLED)
        prose_model_policy = f"real:{approved_model}"
    else:
        prose_client = (OfflineExtractiveCitedProseClient() if manifest.materials
                        else OfflineFactAssertionCitedProseClient())
        prose_model_policy = "offline_stub"
    #: **失败前留存**：调用**前**落输入清单身份与请求面原文，收到回复后、解析**前**落可见回复
    #: 与解析状态（见 `sections.cited_call_journal`）。真实 r2 缺的正是这一份，导致那一轮只能
    #: 做离线派生诊断；这里补上，且它对**离线替身同样生效**——留存不是模型专属的义务。
    journal = CCJ.journal_for(journal_dir) if journal_dir is not None else None
    #: **一次**写作调用（`cited-budget-3` 的每节上限为 1，第二次在类别内就已超限）。
    #: 这里没有任何重试入口：失败即如实抛出，由上层停住。
    _stage(stage_journal, "writer_requested", "started", section_id=section_id,
           detail=f"清单 {len(manifest.materials)} 份材料 / {len(manifest.facts)} 条事实")
    with _cited_call_scope(budget, section_id=section_id):
        outcome = CW.write_cited_section(manifest=manifest, client=prose_client,
                                         model_policy=prose_model_policy, journal=journal)
    _stage(stage_journal, "writer_replied", "completed", section_id=section_id,
           detail=f"{len(outcome.draft.sentence_ids())} 句；产出者策略 {prose_model_policy}")
    # 财务节的**确定性**指标表（§0.19/§0.20；版本见 `cited_financial_table` 的常量）：只由权威事实 + 本节输入清单
    # 构造，不发起任何模型调用、不做任何计算，**也不看正文草稿**——「表里有没有这一行」不再
    # 由写作结果决定。**本脚本不吞掉构造期的数据伤**：模块本身 fail-closed 抛错，这里把原因码
    # 与消息原样记下来，读回里按「硬失败」单列（「表没建出来」与「本节没有表」必须能被读的人
    # 分开）。注意区分三种「没有表」：非财务节（`metric_tables is None`）、财务节但被 typed
    # 拒绝（有 `refusals`）、构造期抛错（有 `metric_table_error`）。
    metric_tables = None
    metric_table_error = None
    try:
        metric_tables = CFT.build_cited_metric_tables(
            section_id=section_id, authority=authority, manifest=manifest,
            presentation_routing=presentation_routing)
    except CFT.CitedMetricTableError as exc:
        metric_table_error = {"reason": str(exc.reason or "unspecified"), "detail": str(exc)}
    # 非财务节的构造器产出是一条 `authority_not_financial` 拒绝（那是给读回用的**诊断**记录），
    # 不是「本节没有表」——所以预览只在**本节权威就是财务权威**时才接收它：否则公司节的读者面上
    # 会凭空多出一节「财务指标表：本节没有生成任何指标表」，读的人会以为这一节本该有财务表。
    is_financial_section = (
        metric_tables is not None
        and str(getattr(metric_tables, "authority_kind", "")) == "financial_workflow")
    check_report = SC.check_cited_prose(draft=outcome.draft, manifest=manifest)
    _stage(stage_journal, "sentence_checked", "completed", section_id=section_id,
           detail=(f"{len(check_report.hard_error_sentence_ids)} 句机械硬错 / "
                   f"{len(check_report.blocked_sentence_ids)} 句阻断"))

    # ---- 第三步：**有界**局部返修（最多一次；`cwr-2` / `cwrp-3` / prompt `@crw-2`）------
    # 位置**在这里**：逐句硬核对之后、独立审阅之前。理由是审阅的对象应当是**改过之后**的
    # 那一稿——让审阅去审一稿已经被判为有硬错的正文，等于把两次读数花在同一批错上。
    # 也因此本批**不**用「审阅判非 supported 的句子」当返修输入（那要求先审后修、再审一次，
    # 是**两次**审阅调用，本批没有获批）。`semantic_sentence_ids` 这个入口留给那一次。
    #
    # `cited-budget-3`（本批）**没有**批返修这一类：它的类别集里没有它，prompt 归属表里
    # 也没有它的 prompt 版本。因此真实模式下这一轮**连请求面都不构造**——守卫在这里，而不是
    # 「让预算在发出去那一刻拦」：后者意味着请求已经装配完、归属表也已经被问过一次，而且
    # 本版归属表里根本没有 `crw-2` 这一条，真发到那一步会是一个**归属错误**（`LLMCallAttribution
    # Error`），不是一次干净的「本批没批」。离线模式不过这道门：那里没有可记账的调用，
    # 历史离线 run（含返修段）的重放必须照旧可跑。
    #
    # 门在**发请求之前**：`run_bounded_cited_rework` 自己先判「点名集合是否为空」，
    # 为空就返回 `not_needed` 且**不碰客户端**——初稿没有点名问题时不花这次调用。
    rework_blocked_by_policy = (mode == ACC.MODE_REAL) and not CB.cited_rework_approved()
    rework_client = None
    rework_request = None
    rework_outcome = None
    rework_gate = ""
    if not outcome.draft.sentence_ids():
        rework_gate = "no_prose_to_rework"
    elif rework_blocked_by_policy:
        rework_gate = "rework_not_approved_in_this_batch"
    elif mode == ACC.MODE_REAL:
        rework_client = CRW.LlmCitedReworkClient(model=approved_model,
                                                 thinking=CITED_THINKING_DISABLED)
        rework_model_policy = f"real:{approved_model}"
    else:
        rework_client = OfflineReworkCitedProseClient()
        rework_model_policy = "offline_stub"
    if not rework_gate:
        # 请求面装配是**纯函数**，两种模式下都做：它同时也是一次事前校验（草稿与清单、报告与
        # 草稿两条等式对不上就在这里停住，不会拖到真发出去之后才发现）。
        rework_request = CRW.build_cited_rework_request(
            draft=outcome.draft, check_report=check_report, manifest=manifest)
    if rework_client is not None and rework_request is not None:
        _stage(stage_journal, "rework_requested", "started", section_id=section_id,
               detail=f"点名 {len(rework_request.problem_sentence_ids)} 句")
        with _cited_call_scope(budget, section_id=section_id):
            rework_outcome = CRW.run_bounded_cited_rework(
                draft=outcome.draft, check_report=check_report, manifest=manifest,
                client=rework_client, model_policy=rework_model_policy)
        _stage(stage_journal, "rework_replied", "completed", section_id=section_id,
               detail=f"终态 {rework_outcome.outcome}")
        if rework_outcome.outcome == "not_needed":
            rework_gate = "rework_not_needed"
    #: **有效草稿**：返修成功就用新稿，否则（不必返修 / 返修失败 / 返修后仍有硬错）一律是
    #: 原稿。第三种情况是刻意的——返修把 11 条硬错减到 3 条，那 3 条照旧逐句标出，而 8 条的
    #: 改善**照收**；"没洗干净就全盘退回"会让这一轮唯一的改善白做。
    #:
    #: 但「照收改善」**不**等于「新稿更好」：新稿的真实去向由 `cwr-2` 的逐句台账给出
    #: （保留/撤下/歧义），它的**内容质量**由读回页 §5.6 单独列，且明确标注为未判。
    active_draft = (rework_outcome.draft
                    if rework_outcome is not None and rework_outcome.outcome == "reworked"
                    else outcome.draft)
    active_check_report = (rework_outcome.check_report
                           if active_draft is not outcome.draft else check_report)
    report_version = CRP.derive_report_version(draft=active_draft, manifest=manifest)
    report_id = f"cited-{section_id}-{manifest.fingerprint()[:16]}"
    if mode == ACC.MODE_REAL:
        review_client = CR.LlmCitedReviewClient(model=approved_model,
                                                thinking=CITED_THINKING_DISABLED)
        review_model_policy = f"real:{approved_model}"
    else:
        review_client = OfflineEchoCitedReviewClient(check_report=active_check_report)
        review_model_policy = "offline_stub"
    #: **一次**独立审阅调用。真实模式下它读的是正文与所引原文（请求由
    #: `cited_review.build_cited_review_request` 装配），独立给语义意见；机械层的结论照旧
    #: 作为 `check_report` 附在旁边，两者**并列不合并**。
    #:
    #: **审阅失败不再吃掉整轮**（`crpv-3`）：真实 r28 里审阅回复 22/23 行 `reason` 为空串，
    #: 构造期抛错，于是 23 句正文在人读出口一节不剩。这里把失败**接下来**，记成一条 typed
    #: 读数，正文与机械核对照旧落盘；失败仍会由 `run()` 记进账本并以非零码结束——"这一轮
    #: 没跑完"照样是真的，只是不再用"什么都看不见"来表达它。
    #:
    #: `LLMTruncatedResponse` 和另外两条**并列**接在这里：provider 把审阅回复截断，与"回复
    #: 不合约"是两类失败，但**接下来之后的处置逐字相同**——半截回复不当意见、草稿与机械核对
    #: 照旧落盘、降级预览照旧生成、账本照旧记 `failed`。客户端已经为自己那次调用留下了失败
    #: 流水（含 `call_id`），这里只把它归成同一条 typed 读数（`review_call_failed`）。
    #: 截断**不**自动重试、**不**提高额度，也不改变写作侧的截断行为（写作侧照旧直接抛出）。
    review_outcome = None
    review_failure = _review_no_object_failure(active_draft)
    #: 失败时`review_issues.json` 里的**受控诊断**（`crr-9`）。它不是"半份意见"：`outcome`
    #: 仍是 `null`，它只是让运行目录自己就说得清"为什么没跑完、错在哪一条"。
    review_failure_detail: dict = {}
    if review_failure:
        print(f"节 {section_id}：正文里没有一句带引用的话，本次没有可审的对象——"
              f"不发审阅调用（原因码 {review_failure!r}）")
    else:
        _stage(stage_journal, "review_requested", "started", section_id=section_id,
               detail=f"产出者策略 {review_model_policy}")
        try:
            with _cited_call_scope(budget, section_id=section_id):
                review_outcome = CR.review_cited_prose(
                    draft=active_draft, manifest=manifest, client=review_client,
                    report_version=report_version, report_id=report_id,
                    check_report=active_check_report, model_policy=review_model_policy)
        except (CR.CitedReviewError, AS.AssuranceSchemaError,
                LLC.LLMTruncatedResponse) as exc:
            review_failure = _review_failure_kind(exc)
            review_failure_detail = _review_failure_detail(exc)
            print(f"节 {section_id}：独立审阅未完成（{review_failure}）：{exc}")
        _stage(stage_journal, "review_replied",
               "failed" if review_failure else "completed", section_id=section_id,
               detail=(review_failure or
                       f"{len(review_outcome.issues)} 条意见；产出者 "
                       f"{review_outcome.review_producer_kind}"))
    #: 草稿缺口的**分桶**（`crpp-5`）：**先分桶、再数必需**，`required_gap_count` 取的是
    #: :func:`sections.cited_report.required_gap_count_from_bins`，不再是 `len(draft.gaps)`。
    #: 口径逐字来自**冻结 Contract**（适用性政策 / 展示档），本处只做投影，不解释 Contract：
    #: 写作 topic 的那一份 `requirement.aspects` 就是本节草稿的栏目范围（`co-h4` 这一个
    #: WritingSpec 小节覆盖 `company_business` 的全部 18 个栏目）。
    gap_bins = CRP.classify_draft_gaps(
        gaps=active_draft.gaps, aspects=tuple(getattr(requirement, "aspects", ()) or ()))
    version = CRP.build_cited_report_version(
        draft=active_draft, manifest=manifest, check_report=active_check_report,
        review_outcome=review_outcome, report_id=report_id,
        required_gap_count=CRP.required_gap_count_from_bins(gap_bins),
        metric_tables=(metric_tables.tables if is_financial_section else ()),
        review_failure=review_failure)
    preview = CRP.build_cited_section_preview(
        draft=active_draft, manifest=manifest, check_report=active_check_report,
        review_outcome=review_outcome, version=version,
        metric_table_outcome=(metric_tables if is_financial_section else None))
    _stage(stage_journal, "report_version_written", "completed", section_id=section_id,
           detail=f"report_version {version.report_version}")
    return {
        "authority": authority, "task": task, "requirement": requirement,
        "material_context": material_context, "subsections": subsections,
        "scan": scan, "manifest": manifest, "outcome": outcome,
        "metric_tables": metric_tables, "metric_table_error": metric_table_error,
        "check_report": check_report, "review_outcome": review_outcome,
        "review_failure": review_failure,
        "review_failure_detail": review_failure_detail,
        #: 第三步（局部返修）的四件产物。`active_draft` / `active_check_report` 是**读者面真正
        #: 显示的那一稿**（返修成功即新稿，否则原稿）；`rework_outcome` 带着两份身份、逐句去向
        #: 与失败原因；`rework_request` 是**请求面原文**（真实模式本批不发这次调用，预算申请报的
        #: 就是它的真实大小）；`rework_gate` 说明"为什么这次没修/没发"。
        "active_draft": active_draft, "active_check_report": active_check_report,
        "rework_outcome": rework_outcome, "rework_request": rework_request,
        "rework_gate": rework_gate, "rework_client": rework_client,
        "version": version, "preview": preview, "report_version": report_version,
        #: 逐条缺口分桶（`crpp-5`）：读者面据它把「必需未覆盖」与「可选/不适用/诊断」分开，
        #: 落盘为 `cited_gap_bins.json`（不进任何既有 wire 的字段集）。
        "gap_bins": gap_bins,
        "report_id": report_id, "prose_client": prose_client,
        "review_client": review_client, "section_title": section_title,
        "topic_ids": tuple(topic_ids), "cited_topic_ids": tuple(cited_topic_ids),
        "deterministic_topic_ids": tuple(deterministic_topic_ids),
        "presentation_routing": presentation_routing, "source_scopes": source_scopes,
        "mode": mode, "approved_model": approved_model, "budget": budget,
        "journal": journal, "journal_dir": journal_dir,
    }


def _build_source_scopes(*, inputs, section_id: str, authority,
                         deterministic_topic_ids: tuple[str, ...]) -> tuple[Any, ...]:
    """本节**确定性呈现** topic 的逐条读数（各族见 `CSS.CITED_SOURCE_SCOPE_SCHEMA_VERSION` /
    `CBS.CITED_BALANCE_STRUCTURE_SCHEMA_VERSION`）。

    按 `DETERMINISTIC_TOPIC_PRESENTERS` + `_DETERMINISTIC_PRESENTERS` 分派；没有登记产出者的
    topic 直接 fail-closed——「这一栏不知道怎么读」必须在构造期说出来，不能悄悄少呈现一栏。
    """
    scopes: list[Any] = []
    for topic_id in deterministic_topic_ids:
        producer = _deterministic_presenter_name(topic_id=topic_id, section_id=section_id)
        requirement = inputs.requirements.get(topic_id)
        if requirement is None:
            raise SystemExit(
                f"topic {topic_id!r} 不在冻结 Contract 投影的需求集内：没有要求就没有呈现，"
                "不伪造条目")
        artifact = getattr(authority, "artifact", None)
        if artifact is None:
            raise SystemExit(
                f"节 {section_id!r} 的权威没有财务 artifact，topic {topic_id!r} 的每一条要求"
                "逐条都要读它：没有权威事实就不得用别的来源凑")
        scopes.append(_DETERMINISTIC_PRESENTERS[producer](
            section_id=section_id, topic_id=topic_id, requirement=requirement,
            authority=authority))
    return tuple(scopes)


# ---------------------------------------------------------------------------
# 人读回（`readback.md`）
# ---------------------------------------------------------------------------

#: 「财务内容完整吗」在人读页上必须拆成两条**不可互推**的读数（M930-3 定点业务纠正②）。
#:
#: * **读数甲 —— 已选财务事实的指标表缺格数**：它读的是「**已选中的合格财务事实**之间有没有
#:   空格」。这张表由选中事实确定性成表，缺值的格**留空**、绝不回填，所以「0」只对这批事实
#:   成立；它**不**读 Contract，也**不**读原 PDF 表区。
#: * **读数乙 —— 冻结 Contract 的栏目覆盖**：Contract 的每一栏**各自**有去向（成文 / typed
#:   缺口），乙读的是「Contract 要的栏目有没有被证明」。
#:
#: 两者读的对象不同、结论不能互推：**甲为 0 不证明财务内容完整**。把两者并成一句「栏目缺口
#: 0」，正是本批要修掉的那种暗示——读的人会把「选中事实之间没有空格」读成「财务完整」。
FINANCIAL_COMPLETENESS_READINGS_HEADING = "**财务完整性：两条互不顶替的读数**（本页**不**合成一句）"


def _financial_empty_cell_count(metric_tables) -> int:
    """读数甲：已选财务事实成表后的**空格数**。

    缺格逐字是空串（``CFT.CITED_METRIC_TABLE_EMPTY_CELL``），因此这里数的就是「选中事实之间
    有几个格没值」。**不**补齐、不外推、不拿别的栏的数值顶。
    """
    if metric_tables is None:
        return 0
    empty = CFT.CITED_METRIC_TABLE_EMPTY_CELL
    return sum(1 for table in (getattr(metric_tables, "tables", ()) or ())
               for row in table.rows for cell in row.cells if cell == empty)


def _contract_column_counts(payload, source_scopes) -> tuple[tuple[str, int], ...]:
    """本节的 **Contract 栏目数**（`topic_id`, 栏目数）——**不是**写作小节数。

    两处都是冻结 Contract 投影的读数，各有其出处：

    * 写作 topic：取该 topic `requirement.aspects` 的条数；
    * 确定性呈现 topic：取呈现产物**自报**的 `contract_topic_aspects`（它与 Contract
      逐条相等，见 `build_cited_source_scope`）。

    原先读数乙把 `len(manifest.subsections)`（**小节**数，财务节恰为 1）印成「本主题 N 栏」，
    与同页 §A3 的 7 条路由 + 2 条 typed 栏目缺口自相矛盾——**小节**是冻结 WritingSpec 的
    分段轴，**栏目**是冻结 Contract 的要求轴，两者数目不同，不得互相顶替。
    """
    rows: list[tuple[str, int]] = []
    cited = tuple(payload.get("cited_topic_ids") or ())
    aspects = tuple(getattr(payload.get("requirement"), "aspects", ()) or ())
    if cited and aspects:
        rows.append((str(cited[0]), len(aspects)))
    for scope in tuple(source_scopes or ()):
        topic = str(getattr(scope, "topic_id", "") or "")
        columns = tuple(getattr(scope, "contract_topic_aspects", ()) or ())
        if topic:
            rows.append((topic, len(columns)))
    return tuple(rows)


def _contract_columns_line(*, payload, source_scopes, subsection_count: int) -> list[str]:
    """§1 表下的那两行：**写作小节数**与**冻结 Contract 栏目数**分开印（`cfread-1`）。

    原先只印一行「栏目数：`len(manifest.subsections)`」。财务节的小节数恰为 1，于是读回在
    「本主题 1 栏」的同时，同页又列着同 topic 的 6 条 / 10 条 Contract 栏目要求与 2 条 typed
    栏目缺口——两句自相矛盾。**小节**是冻结 WritingSpec 的分段轴，**栏目**是冻结 Contract 的
    要求轴，两个数不同、不得互相顶替。这里把两个数各自说清，口径与 §0 的读数乙逐字一致。
    """
    columns = "、".join(f"`{topic}` **{count}** 栏"
                       for topic, count in _contract_column_counts(payload, source_scopes))
    if not columns:
        columns = "（本次没有可读的 Contract 栏目投影）"
    return [
        f"- 本表**一行 = 一个写作小节**：**{subsection_count}** 个小节"
        "（冻结 WritingSpec 的分段轴；`requirement_text` 取自冻结 Contract 投影，"
        "不增、不减、不改写）。",
        f"- 同一 topic 的冻结 Contract **栏目**数**另计**：{columns}"
        "——**栏目数与小节数是两个不同的数**，不得互相顶替；逐栏去向见本节 §0 读数乙、"
        "§4 与 `cited_source_scope__*.md` / `cited_balance_structure__*.md` 的 typed 缺口。",
    ]


def _financial_completeness_readings(*, metric_tables, column_counts,
                                     subsection_count: int,
                                     region_count: int, confirmed_count: int,
                                     routing_column_gap_count: int | None = None) -> list[str]:
    """两条读数的读者面（M930-3 定点业务纠正②）。**只在财务节**调用。

    `column_counts` 是**冻结 Contract 的栏目数**（逐 topic），`subsection_count` 是
    冻结 WritingSpec 的**写作小节数**：两者分开印，读者才看得见「栏目 6/10、小节 1」这三
    个不同的数，而不会把小节数读成栏目数。
    """
    tables = tuple(getattr(metric_tables, "tables", ()) or ())
    empty = _financial_empty_cell_count(metric_tables)
    if routing_column_gap_count is None:
        gap_clause = "本节呈现层路由未另列栏目缺口（见本节 §A3）"
    else:
        gap_clause = (f"本节呈现层路由另有 typed 栏目缺口 "
                      f"**{routing_column_gap_count}** 条（见本节 §A3）")
    columns_clause = "、".join(f"`{topic}` **{count}** 栏"
                              for topic, count in tuple(column_counts or ()))
    if not columns_clause:
        columns_clause = "（本次没有可读的 Contract 栏目投影）"
    return [
        FINANCIAL_COMPLETENESS_READINGS_HEADING, "",
        f"- **读数甲 —— 已选财务事实的指标表缺格数**：**{empty}**。"
        f"表由**已选中的合格财务事实**确定性成表（{len(tables)} 张），缺值的格**留空**、"
        "绝不回填，所以这个数只读「**这批选中事实**之间有没有空格」——它**不**读 Contract，"
        "也**不**读原 PDF 表区。",
        f"- **读数乙 —— 冻结 Contract 的栏目覆盖**：Contract 的**栏目**逐栏**各自**有去向"
        f"（本节 Contract 栏目：{columns_clause}；写作**小节** **{subsection_count}** 个"
        "——栏目数与小节数是两个不同的数，不得互相顶替；逐栏见 `readback.md` §4 与 "
        f"`cited_source_scope__*.md` / `cited_balance_structure__*.md` 的 typed 缺口；"
        f"{gap_clause}）。"
        "乙读的是「**Contract 要的栏目有没有被证明**」。",
        f"- **甲 = 0 不证明财务内容完整**：甲只对「选中事实」成立——它既**不**说 Contract 的"
        f"每一栏都被证明，也**不**代表原 PDF 表区的数字已获资格化：那 {region_count} 个表区仍"
        f"**待人工确认**（其中已取得人工确认的：**{confirmed_count}**）。",
        "",
    ]


def _render_demo_page(*, run_id: str, section_id: str, subject_name: str, preview,
                      check_report, version, source_display, metric_tables,
                      withheld: list[dict], registered_sha256=None,
                      source_scopes: "Sequence[Any]" = (),
                      presentation_routing=None,
                      topic_ids: "Sequence[str]" = (),
                      subsection_count: int = 0,
                      column_counts: "Sequence[tuple[str, int]]" = ()) -> str:
    """**一页**：同一版本上两条可读路径 + 财务权威表 + 缺口 + 审阅意见 + 状态分列。

    它不是第二套产物——每一节都是同一批已落盘产物的**原文嵌入**（逐字，不摘要、不改写）：

    * §A 主营业务正文（逐句引用、逐句机械结论、逐句审阅意见、中文缺口、同版指标表）
      ← :func:`CRP.render_cited_preview_markdown` 的**原文**；
    * §A2 财务**确定性呈现**（`fin_source_scope` 来源与口径、`fin_balance_structure`
      资产负债结构）← 确定性呈现的**原文**，**零模型调用**；
    * §A3 指标→栏目路由声明（呈现层，**不是**事实权威）← 本节路由声明的取值；
    * §B 原 PDF 表区（只读展示、待人工确认）← :func:`STD.render_source_table_display_markdown`
      的**原文**；
    * §C 被替身自己撤下的候选片 ← `withheld_candidates.json` 的**同一份**记录；
    * §D 状态分列 ← 版本记录里那几根轴**各自**的取值，不合并成一个「好／坏」。

    两条路径的**身份不可互换**：§A 是路径 a（材料 / 事实 → 正文），§B 是路径 b
    （已登记原件 → 精确页区域 → 人确认）。§B 的存在**不**证明 Contract `set_complete`、
    **不**证明 TS5、**不**把任何数字升格为权威。
    """
    version = preview.version
    L: list[str] = [
        f"# M930-3 演示页 · 节 `{section_id}` · 主体 {subject_name}",
        "",
        "> **不可发布、未经人工接受。** 本页把同一版本上的两条可读路径、财务权威表、中文缺口"
        "与审阅意见并排摊开，供人逐项核对；它**不是**验收报告，也不宣称任何门通过。",
        *(["> 本页正文与审阅**各自**标明产出者：正文出自本次写作调用，审阅是独立只读语义审阅；"
           "两者**不是**同一件事，也不互相顶替。"] if _model_backed_review(version) else
          ["> 离线替身正文与真实独立审阅**不是**同一件事；本页在每一处都标明产出者。"]),
        "",
        f"- run：`{run_id}`　节：`{section_id}`"
        + (f"　本次选中主题：{ '、'.join('`' + str(t) + '`' for t in topic_ids) }"
           if topic_ids else ""),
        f"- report_version：`{version.report_version}`（路径 a 的版本锚）",
        f"- 展示集：`{source_display.display_set_id()}`"
        f"（路径 b 的身份，**不含** report_version——两条路径身份不可互换）",
        "",
        "## 0. 这一页里有什么，各自证明了什么",
        "",
        "| 段 | 内容 | 它是哪条路径 | 它**不**证明什么 |",
        "|---|---|---|---|",
        "| A | 主营业务正文（逐句引用 + 机械结论 + 审阅意见 + 中文缺口 + 同版财务指标表） | "
        "路径 a：Contract → Pack/合格事实 → 精确 Writer 清单 → 带引用正文 | "
        "它**不**证明语义已被支持（机械层无此资格） |",
        "| A2 | 财务确定性呈现（`fin_source_scope` 来源与口径；`fin_balance_structure` "
        "资产负债结构与重大科目变化） | "
        "确定性呈现：冻结 Contract 要求 → 权威字段／合格事实，**零模型调用** | "
        "它**不是**正文，没有草稿、没有引用、没有审阅；取不到的逐条留 typed 缺口 |",
        "| A3 | 指标 → 栏目路由声明 | 呈现层声明（`fpr-2`），**不是**权威登记 | "
        "它**不**证明任何一栏的 Contract 要求已被满足；只说明「哪个指标本来属于哪一栏」 |",
        "| B | 原 PDF 表区（页 + 区域 + 渲染 + 待人工确认） | "
        "路径 b：已登记原件 → 精确页区域 → 只读渲染 | "
        "它**不**进 Pack/Writer 材料身份，不是数字权威，不证明 `set_complete`/TS5 |",
        "| C | 被替身自己撤下的候选片 | 需求侧按 `scp-2`（数字）/ `srsc-3`（来源角色）自查 | "
        "撤下 ≠ 材料里没有那张表；它只说明正文数字尚未授权 |",
        "| D | 六个状态**各自**的取值 | 装配读数 | 它们互不顶替，任何一个为真都不能代表另一个 |",
        "",
        "## A. 主营业务正文（路径 a）",
        "",
    ]
    L += CRP.render_cited_preview_markdown(preview).splitlines()
    L += ["",
          "## A2. 财务确定性呈现（**零模型调用**）",
          "",
          "> 这一段**没有**经过任何模型：它逐条读冻结 Contract 的要求，逐条从本节财务权威的"
          "字段或合格事实里取值，取不到的逐条留 typed 缺口。因此它既不是正文，也不受"
          "「正文必须有引用」那条规则的约束——把一段确定性呈现塞进写作链的产物里，"
          "会让读者以为它也经过评审。",
          "> 它与 §A 的偿债栏是**不同的**财务主题，各自如实呈现本主题的内容与缺口；"
          "本段**不**复制 §A 的整套偿债表充数。",
          ""]
    if source_scopes:
        for _i, scope in enumerate(source_scopes, start=1):
            name = _deterministic_presenter_name(
                topic_id=scope.topic_id, section_id=section_id)
            L += [f"### A2.{_i} 主题 `{scope.topic_id}`（产出者 `{name}`，"
                  f"`{scope.schema_version}`）", ""]
            L += _DETERMINISTIC_RENDERERS[name](scope).splitlines()
            L += [""]
    else:
        L += ["- 本节本次没有确定性来源／口径主题。", ""]
    L += ["## A3. 指标 → 栏目路由声明（呈现层，**不是**事实权威）", ""]
    if presentation_routing is None:
        L += ["- 本节本次没有 `fin_solvency` 主题，因此没有呈现层路由声明。", ""]
    else:
        L += [
            "> 本声明回答「读者在某一栏里读到的那个指标，本来是不是这一栏的」。它是**声明**"
            f"（`{presentation_routing.routing_version}`，一行一个映射），"
            "不是从指标名猜出来的，也不是权威自己认领的："
            "`authority_facts[].aspect_ids` 在本链上**始终为空**。",
            "",
            "| 指标 code | 本声明路由到的栏 | 该栏在冻结 Contract 里的展示层级 |",
            "|---|---|---|",
        ]
        for route in presentation_routing.routes:
            L.append(f"| `{route.metric_code}` | `{route.presentation_column}` | "
                     f"`{route.contract_display_tier}` |")
        L += [
            "",
            f"- 本节本次投递事实：路由到栏 **{len(presentation_routing.fact_columns)}** 条，"
            f"未路由 **{len(presentation_routing.unrouted_facts)}** 条。",
            f"- 路由声明指纹：`{presentation_routing.fingerprint()}`"
            f"（版本 `{presentation_routing.routing_version}`）",
            "",
        ]
        if presentation_routing.column_gaps:
            L += ["**栏目缺口**（冻结 Contract 有这一栏，本次选中事实里没有任何指标能证明它；"
                  "**不**拿别的栏的事实去顶）：", "",
                  "| 栏目 | 冻结 Contract 的原文要求 | 原因 |", "|---|---|---|",
                  *[f"| `{gap.column}` | {gap.contract_requirement_text} | `{gap.reason}` |"
                    for gap in presentation_routing.column_gaps], ""]
        else:
            L += ["- 本主题**选中事实**的栏目缺口：**0**（本次选中的事实里，没有「落在某栏却"
                  "没有指标可证」的情形）。**注意**：这是**已选事实**的读数，**不**等于冻结 "
                  "Contract 的栏目覆盖完整——Contract 每一栏各自另有去向，见本节 §1 与 §D 的"
                  "两条读数。", ""]
    L += ["",
          "## B. 原 PDF 表区（路径 b，只读展示）",
          "",
          "> 这一段是**原件长什么样**，不是「数字对不对」。原表是否完整、清晰、忠实于原件，"
          "由**人**确认；本页与代码都不代替人签署确认。",
          ""]
    #: 图片基准 = `source_display/`：本节页面与 PNG 都在**同一个** `out_dir` 下。
    L += STD.render_source_table_display_markdown(
        source_display, registered_sha256=registered_sha256,
        image_base="source_display").splitlines()
    L += ["",
          "## C. 被替身自己撤下的候选片（撤下 ≠ 来源里没有）",
          "",
          *WITHHELD_SECTION_INTRO]
    L += _withheld_table(withheld) if withheld else ["- 撤下片数：**0**（本次替身没有撤下任何候选片）", ""]

    metric_line = ("**（本节无指标表）**" if metric_tables is None else
                   f"{len(metric_tables.tables)} 张（`"
                   + "`, `".join(t.table_id for t in metric_tables.tables) + "`）")
    L += ["## D. 状态分列（互不顶替。任何一个为真都不能代表另一个）", "",
          "| 状态 | 取值 | 谁才能动它 |", "|---|---|---|",
          f"| ① 预览可读 | `{version.preview_state}` | 装配层（本 run 已完成） |",
          f"| ② 机械核对 | `{version.mechanical_state}`"
          f"（{check_report.sentence_count} 句 / 硬错误 "
          f"{len(check_report.blocked_sentence_ids)} 句） | 不调模型的底线核对 |",
          f"| ③ 独立审阅 | `{version.system_review_state}`"
          f"（产出者 `{version.review_producer_kind or '（无审阅）'}`） | "
          "**只有**真实独立语义审阅够格写成通过 |",
          f"| ④ 系统放行 | `{version.publishability}` | 正式放行流程（本批未进入） |",
          "| ⑤ 人工接受 | `人为未接受` | **只有人**（本页不代替签署） |",
          f"| ⑥ 正式 TS5 / 阶段关闭 | `未进入` | 正式阶段流程（本批未进入） |",
          "",
          f"- 财务节指标表：{metric_line}",
          f"- 路径 b 展示集：{len(source_display.regions)} 个区域，"
          f"其中可展示 {len(source_display.displayable_regions)}、"
          f"缺陷 {len(source_display.regions) - len(source_display.displayable_regions)}；"
          f"**已取得人工确认的区域：{len(source_display.confirmed_regions)}**",
          ""]
    #: 财务节的「内容完整吗」必须拆成两条互不顶替的读数（M930-3 定点业务纠正②）：甲 = 已选
    #: 财务事实成表的空格数，乙 = 冻结 Contract 的栏目覆盖。甲为 0 **不**证明财务完整。
    if (metric_tables is not None
            and str(getattr(metric_tables, "authority_kind", "")) == "financial_workflow"):
        L += _financial_completeness_readings(
            metric_tables=metric_tables,
            column_counts=column_counts,
            subsection_count=subsection_count,
            region_count=len(source_display.regions),
            confirmed_count=len(source_display.confirmed_regions),
            routing_column_gap_count=(len(presentation_routing.column_gaps)
                                      if presentation_routing is not None else None))
    L += [
        "> 本页把两条路径并排给出，是为了让人**同时**看到「正文说了什么」与「原件长什么样」。"
        "它们**不**互相顶替：正文里的数字不会因为原表看得见就变成已授权的数字；"
        "原表看得见也**不**让 Contract 的 `set_complete` 成立。",
        "",
    ]
    return "\n".join(L) + "\n"


def _render_multi_section_page(*, run_id: str, subject_name: str,
                               sections: list[dict], run_dir: Path) -> str:
    """run 根上的**一页**：多节并排。每一节都是同一批已落盘产物的**原文嵌入**。

    它**不**给出一个总版本号。每节的 `report_version` 只含该节写作侧的输入与身份
    （`crpv-2` 的锚），跨节压根不是一个锚；写一个「合并版本」就是发明一个不存在的身份。
    因此本页逐节列出各自的锚，并把「这是几节、各节的锚分别是什么」摆在最前面。

    段位与单节页一一对应，只是**按节展开**：

    * §A 各节正文（路径 a）—— `CRP.render_cited_preview_markdown` 的原文，逐节一段；
    * §B 原 PDF 表区（路径 b）—— `STD.render_source_table_display_markdown` 的原文，**只一次**
      （展示集按节建，但区域集合与原件是同一份；逐节重复只会让人以为有两份原件）；
    * §C 被撤下的金额 / 比率句 —— 各节并集，逐条标出它属于哪一节；
    * §D 状态分列 —— **逐节**六轴，外加本页自己的一句话说明它们互不顶替。
    """
    L: list[str] = [
        f"# M930-3 演示页 · 多节 · 主体 {subject_name}",
        "",
        "> **不可发布、未经人工接受。** 本页把各节**同一批**已落盘产物原文并排，供人逐项核对；"
        "它**不是**验收报告，也不宣称任何门通过。",
        *(["> 本页正文与审阅**各自**标明产出者：正文出自本次写作调用，审阅是独立只读语义审阅；"
           "两者**不是**同一件事，也不互相顶替。"] if _model_backed_review(
               (sections[0].get("payload") or {}).get("version") if sections else None) else
          ["> 离线替身正文与真实独立审阅**不是**同一件事；本页在每一处都标明产出者。"]),
        "",
        f"- run：`{run_id}`　节数：**{len(sections)}**",
        "- **没有**跨节的合并 `report_version`：每节的锚只含该节写作侧输入，逐节列在下面。"
        "两条路径（正文 / 原件）的身份同样不可互换。",
        "",
        "## 0. 节目录（各自的锚各自列，不合成一个总版本）",
        "",
        "| 节 | topic | report_version | 路径 a 预览 | 路径 b 展示集 | 指标表 |",
        "|---|---|---|---|---|---|",
    ]
    for item in sections:
        payload = item["payload"]
        version = payload["version"]
        display = item["source_display"]
        tables = item["metric_tables"]
        table_text = ("（本节非财务节：**不接收**指标表）" if tables is None
                      else f"{len(tables.tables)} 张")
        L.append(
            f"| `{item['section_id']}` | `{item['topic_id']}` | `{version.report_version}` "
            f"| `{_md_cell(str(item['out_dir']))}/cited_preview.md` "
            f"| `{display.display_set_id()}`（{len(display.regions)} 区域） "
            f"| {table_text} |")
    L += ["",
          "> 节内一页（`demo_page.md`）与逐节读回（`readback.md`）落在上表各自的目录里；"
          "本页是它们的**并排版**，不是替代品。",
          ""]

    for index, item in enumerate(sections, start=1):
        L += [f"## A{index}. 节 `{item['section_id']}`（topic `{item['topic_id']}`）"
              "—— 正文（路径 a，逐句引用）",
              "",
              f"- 版本锚：`{item['payload']['version'].report_version}`"
              f"　节内一页：`{_md_cell(str(item['out_dir']))}`",
              ""]
        L += CRP.render_cited_preview_markdown(item["payload"]["preview"]).splitlines()
        L += [""]

    L += ["## B. 原 PDF 表区（路径 b，只读展示；**全部节共用同一份原件读数**）", "",
          "> 这一段是**原件长什么样**，不是「数字对不对」。原表是否完整、清晰、忠实于原件，"
          "由**人**确认；本页与代码都不代替人签署确认。",
          "> 展示集身份按节计算（`section_id` 进它的身份体），因此它的 id 属于**建它的那一节**；"
          "这里只印一次，避免让人以为有两份原件。",
          ""]
    if sections:
        first = sections[0]
        L += [f"- 展示集：`{first['source_display'].display_set_id()}`"
              f"（建自节 `{first['section_id']}`）", ""]
        #: 图片基准 = 该节目录相对**本页**（run 根）的路径：区域 PNG 落在
        #: `run_dir/<section>/source_display/`，而本页在 `run_dir/` 上。
        _display_section_dir = os.path.relpath(
            Path(first["out_dir"]), run_dir).replace(os.sep, "/")
        L += STD.render_source_table_display_markdown(
            first["source_display"],
            registered_sha256=first.get("registered_sha256"),
            image_base=f"{_display_section_dir}/source_display").splitlines()
    L += [""]

    L += ["## C. 被替身自己撤下的候选片（撤下 ≠ 来源里没有）", "",
          *WITHHELD_SECTION_INTRO]
    rows = [(item["section_id"], row) for item in sections for row in item["withheld"]]
    if rows:
        L += [f"- 撤下片数：**{len(rows)}**", "",
              "| 节 | 小节 | 材料键 | 原因码 | 未授权表面 | 去向 |",
              "|---|---|---|---|---|---|"]
        for section_id, row in rows:
            surfaces = "、".join(str(s) for s in (row.get("surfaces") or ()))
            L.append(f"| `{section_id}` | `{row.get('subsection_id', '')}` | "
                     f"`{row.get('material_key', '')}` | `{row.get('reason', '')}` | "
                     f"{_md_cell(surfaces or '（无）')} | {_md_cell(row.get('note', ''))} |")
    else:
        L += ["- 撤下片数：**0**（本次替身没有撤下任何候选片）"]
    L += [""]

    L += ["## D. 状态分列（**逐节**；六个轴互不顶替，任何一个为真都不能代表另一个）", "",
          "| 节 | ① 预览可读 | ② 机械核对 | ③ 独立审阅 | ④ 系统放行 | ⑤ 人工接受 "
          "| ⑥ 正式 TS5 / 阶段关闭 |",
          "|---|---|---|---|---|---|---|"]
    for item in sections:
        payload = item["payload"]
        version = payload["version"]
        check_report = payload.get("active_check_report") or payload["check_report"]
        L.append(
            f"| `{item['section_id']}` | `{version.preview_state}` "
            f"| `{version.mechanical_state}`（{check_report.sentence_count} 句 / 硬错误 "
            f"{len(check_report.blocked_sentence_ids)} 句） "
            f"| `{version.system_review_state}`（产出者 `{version.review_producer_kind or '（无审阅）'}`） "
            f"| `{version.publishability}` | 人为未接受 | 未进入 |")
    L += ["",
          "> ③ 轴只有**真实独立语义审阅**够格写成通过；离线替身回声不是。④⑤⑥ 三轴本批都未进入。",
          ""]
    #: 财务节的「内容完整吗」逐节拆成两条互不顶替的读数（M930-3 定点业务纠正②）。只对财务节
    #: 展开；非财务节没有这条轴。
    for item in sections:
        tables = item["metric_tables"]
        if tables is None or str(getattr(tables, "authority_kind", "")) != "financial_workflow":
            continue
        display = item["source_display"]
        routing = item["payload"].get("presentation_routing")
        readings = _financial_completeness_readings(
            metric_tables=tables,
            column_counts=_contract_column_counts(
                item["payload"], item["payload"].get("source_scopes")),
            subsection_count=len(item["payload"]["manifest"].subsections),
            region_count=len(display.regions),
            confirmed_count=len(display.confirmed_regions),
            routing_column_gap_count=(len(routing.column_gaps)
                                      if routing is not None else None))
        readings[0] = f"**节 `{item['section_id']}`** · " + readings[0]
        L += [*readings, ""]

    L += [
        "> 本页把各节与两条路径并排给出，是为了让人**同时**看到「正文说了什么」与"
        "「原件长什么样」。它们**不**互相顶替：正文里的数字不会因为原表看得见就变成已授权的"
        "数字；原表看得见也**不**让 Contract 的 `set_complete` 成立；一节的状态也**不**能"
        "代表另一节。",
        "",
    ]
    return "\n".join(L) + "\n"


_BANNER = (
    "> **本文件不是验收报告，也不宣称任何门通过。**\n"
    "> 它把**同一版本**上这条链的输入与输出逐项摊开，供人逐项核对。\n"
    "> 真实部分与离线替身部分在下文分栏并列，**不得互相顶替**。\n"
)


def _pct(part, whole) -> str:
    if not whole:
        return "—"
    return f"{100.0 * part / whole:.1f}%"


def _md_cell(value: object) -> str:
    """把任意文本放进 markdown 表格单元格：转义竖线、压平换行。

    原文里出现一个 `|` 就会把整行**静默**拆成两格，后面的列全体错位——而读的人不会察觉。
    宁可转义得难看，也不让一张表悄悄错位。
    """
    text = str(value if value is not None else "")
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _pack_set_facts(inputs, section_id: str) -> tuple:
    """本节 Pack 集里的**合格事实**（`SupportedFact`，`packs[*].facts`）。

    与 `inputs.topic_results` **不在同一处**：运行时结果只带**候选**与**资格决定**，合格事实
    落在 Pack 里。少了这一路，逐值台账里「已合格但未送达」这一档永远判不出来（它会退成
    `no_candidate`，把「合格了、但没送进 Writer」说成「压根没合格」——正是这一步最不能犯的错）。

    财务节没有 `pack_set`（它走 `FinancialFactPack`，事实由清单事实轴承载），返回空元组是它
    的**合法状态**，不是「读不到」。
    """
    authority = (getattr(inputs, "authorities", None) or {}).get(section_id)
    pack_set = getattr(authority, "pack_set", None)
    if pack_set is None:
        return ()
    return tuple(
        fact
        for pack in (getattr(pack_set, "packs", ()) or ())
        for fact in (getattr(pack, "facts", ()) or ()))


def _continuity_cell(continuity: object) -> str:
    """「续接」列的一格：`第i/n片（前 mXX／后 mYY）`；没有被切过的材料写 `—`。

    `—` 是**结论**——这段原文没有跨块边界，不是「续接读法没读出来」。真正读不出来的续接
    在构造入口就 fail-closed（见 `sections/material_context.py` 的 `_checked_continuity`），
    不会走到这里变成一个空单元格。
    """
    if not continuity:
        return "—"
    cont = dict(continuity)
    prev_ref = str(cont.get("continued_from") or "")
    next_ref = str(cont.get("continued_in") or "")
    prev_txt = f"前 {prev_ref}" if prev_ref else "前 —"
    next_txt = f"后 {next_ref}" if next_ref else "后 —"
    return (f"第{cont.get('piece_index')}/{cont.get('piece_count')}片"
            f"（{_md_cell(prev_txt)}／{_md_cell(next_txt)}）")


#: 撤下候选片这一段的**统一导语**：三种撤下原因（数字未授权 / 历史来源当前化 / 表单字形）
#: 是**不同**的事，逐条按原因码分列，不合并成一句「没有材料」。三种都**不**等于「材料里
#: 没有这张表」或「上传语料里不存在该内容」。
WITHHELD_SECTION_INTRO = (
    "被撤下的**候选片**逐条列出，每条带**原因码**与去向。三种原因互不顶替：",
    "",
    f"- `numeric_not_authorized`（`scp-2`）：金额 / 比率只由普通材料原文授权 ⇒ "
    f"**{WITHHELD_NUMERIC_NOTE}**；",
    "- `history_source_as_current_state`（`srsc-1`）：这一片只由不能表达当前状态的来源角色"
    "支撑、且自己没有期间限定 ⇒ 写出去读者会读成持续至今的当前状态；",
    "- `selection_form_surface`：这一片带表单控件字形（勾选 / 模板前缀）⇒ 不是公司经营事实。",
    "",
    "三者都**不**等于「材料里没有这张表」，也**不**等于「上传语料里不存在该内容」：",
    "撤下说的是「按本轮判据这一片不能写成正文」，不是「来源里没有」。",
    "",
)


#: 逐轴读法：**每一根轴的失败各自说明**，不合并成一句「正文有问题」。
#: 键是 `SC.CHECK_KINDS` 里的轴名；未登记的轴走 :func:`_axis_note` 的兜底句。
_AXIS_NOTES: "dict[str, tuple[str, ...]]" = {
    "current_state_scope": (
        "- `current_state_scope`：判据说的是「本句**只**由不能表达当前状态的来源角色支撑，"
        "且自己没有任何期间限定」——写出去读者只能读成持续至今的当前状态。"
        "它抓的正是 §0.20 点名的「旧材料当前化」，而且**没有**任何语义模型参与。",
    ),
    "aspect_attribution": (
        "- `aspect_attribution`：判据说的是「本小节**声明**的那个 Contract 栏目，不在所引来源"
        "**登记归属**的栏目集合里」。引用存在、字面可能逐字相同，都**不**构成本栏目的覆盖。"
        "它抓的是「写了别栏的内容却被记成这一栏已写」。",
    ),
    "template_text": (
        "- `template_text`：勾选 / 模板 / 版式碎片不得冒充公司经营事实。",
    ),
    "numeric_qualification": (
        "- `numeric_qualification`：金额与比率只由两条路授权（合格事实，或表材料的某一格）；"
        "「在被引普通材料原文里逐字出现」**不**构成授权。",
    ),
    "display_role": (
        "- `display_role`（`scp-6`）：判据说的是「本句所引事实经**呈现层声明**落在"
        "`contract_display_tier=diagnostic_only` 的栏目」。这一档按展示角色边界**只进**"
        "附录 / 脚注 / 诊断表：数值本身照样可读，但写进普通正文会被读者当成正文结论。"
        "它抓的是「栏目对得上、展示层用错」，与栏目归属轴**正交**（两轴不互相顶替）。",
    ),
}


def _axis_note(kind: str) -> list[str]:
    """这一根轴的失败**是什么意思**（列在该轴名下，不替别的轴说话）。"""
    lines = _AXIS_NOTES.get(str(kind))
    if lines is None:
        return [f"- `{kind}`：本轴判据见 `sections/sentence_check.py` 的对应条目"
                "（这里不给它编一段解释）。"]
    return list(lines)


def _withheld_table(rows: "Sequence[Mapping[str, Any]]") -> list[str]:
    """撤下记录 → 一张表（小节 / 材料键 / 原因码 / 未授权表面 / 去向）。"""
    out = [f"- 撤下片数：**{len(rows)}**", "",
           "| 小节 | 材料键 | 原因码 | 未授权表面 | 去向 |", "|---|---|---|---|---|"]
    for row in rows:
        surfaces = "、".join(str(s) for s in (row.get("surfaces") or ()))
        out.append(f"| `{row.get('subsection_id', '')}` | `{row.get('material_key', '')}` | "
                   f"`{row.get('reason', '')}` | {_md_cell(surfaces or '（无）')} | "
                   f"{_md_cell(row.get('note', ''))} |")
    out.append("")
    return out


def _render_metric_tables(L: list[str], tables, *, heading: str) -> None:
    """把一批确定性指标表逐张写进读回：表体 + 逐格事实坐标/引用键 + 表 id 与指纹。"""
    if not tables:
        L += [f"（{heading}：**本档没有表**——不是漏印，是本节这一档确实一张都没成。）", ""]
        return
    for table in tables:
        L += [CFT.render_cited_metric_table_markdown(table), ""]
        L += ["| 行 | 事实坐标（逐格） | 引用键（逐格） |", "|---|---|---|"]
        for row in table.rows:
            facts = "、".join(f"`{f}`" for f in row.fact_ids) or "—"
            keys = "、".join(f"`{k}`" for k in row.citation_keys) or "—"
            L.append(f"| {_md_cell(row.label)} | {facts} | {keys} |")
        L += ["",
              f"- 展示层级：`{table.display_tier}`"
              f"　表 id：`{table.table_id}`；指纹：`{table.fingerprint()}`"
              f"；单位记号：`{table.unit}`；期间口径：`{table.period_basis}`",
              ""]


#: 材料落在哪份文档上取不到时的**显式**桶名。绝不静默丢掉这一格：一张表把材料藏进看不见的
#: 键里，比印一张全零的表更坏——读的人会把「被我藏了」读成「本来就没有」。
_UNLABELLED_DOCUMENT = "（未标注文档）"
_UNRESOLVED_DOCUMENT = "（不在 Pack 材料表内）"


#: §4.2 逐栏的**字段缺口**码（`cfgap-1`）。这一栏在冻结 Contract 里声明了 `required_fields`
#: （例如客户当期集中度要的是「前五大合计占比 / 关联方 / 披露范围」三件），而**本栏名下**
#: 一条**合格事实**都没有 ⇒ 这不是「来源称不适用」，也**不是**「用户缺件」，而是
#: **现有来源在这一栏上的读取 / 资格 / 交付缺口**：材料可能在、原表可能已在上传件里，
#: 但这一轮没有把这三件取得为合格事实。三种说法对下游是三条完全不同的指令，不得互相顶替。
#: 原表在原 PDF 表区的只读展示（§0.21 路径 b）**不**改变本读数。
ASPECT_FIELD_GAP_REQUIRED_FIELDS_WITHOUT_FACT = "required_fields_without_qualified_fact"


#: 栏目级「写到什么程度」的**下界**读数（读侧面；**不是**「实质已答」的判定）。
#:
#: 它回答的问题是：「这一段标签 / 一句泛泛的话」到底算不算把这一栏答了？机器**不许**宣布
#: 「实质已答」——那要人对着原件读；但机器**可以**给出下界，把「只有标签」「只有一句」
#: 「两句以上但只引了一条材料」逐档分开，读者据此判断要不要去看原件。
#:
#: 取值是**互斥**的，从「材料都没有」一路收紧到「多句多源」，每一档的判据只用**已落盘**的
#: 轴（Pack 登记 / 送达 / 段级声明 / 逐句引用），不做语义判断、不读正文意思。
ASPECT_SUBSTANTIVE_STATES = (
    #: 本栏名下**一条 Pack 材料都没有**被认领。
    "no_pack_material",
    #: 有材料进 Pack，但**没有一条送达**本节写作清单。
    "material_not_delivered",
    #: 材料送达了，但**没有任何段落**声明服务本栏（一个字都没往这一栏写）。
    "not_written",
    #: 有段落声明服务本栏，却**没有一句**的引用材料登记在本栏 —— 标签不等于回答。
    "label_only",
    #: 只**一句**落在本栏（一句是一句，不构成一段可读的内容）。
    "single_sentence",
    #: 两句以上，但引用材料的**登记归属**只落在**一条**材料上。
    "single_source",
    #: 两句以上且**两条以上**材料 —— 这是**下界**：仍**不**等于「实质已答」。
    "multi_sentence_multi_source",
)


def _substantive_state(*, in_pack: int, delivered: int, paragraphs_declaring: int,
                       sentences_for_aspect: int, materials_for_aspect: int) -> str:
    """按上表逐档判一个栏目写到哪一步。顺序即优先级（先问「有没有材料」，再问「写没写」）。"""
    if not in_pack:
        return "no_pack_material"
    if not delivered:
        return "material_not_delivered"
    if not paragraphs_declaring:
        return "not_written"
    if not sentences_for_aspect:
        return "label_only"
    if sentences_for_aspect == 1:
        return "single_sentence"
    if materials_for_aspect <= 1:
        return "single_source"
    return "multi_sentence_multi_source"


def _aspect_material_matrix(*, inputs, section_id: str, manifest, draft, check_report):
    """业务 aspect × 上传材料：找到 / 进入 Pack / 送达 Writer / **算不算本栏目正文** / 缺口。

    四处读数**各自取自已落盘的产物**，不互相推算：

    * **进入 Pack**：`TopicResearchPack.material_dispositions` 里每条材料**自报**的
      `aspect_ids`（材料侧归属轴；`harness/topic_runtime.build_material_dispositions` 是它的
      唯一构造入口，本函数只读不算）；材料落在哪份上传文档上，取
      `ResearchMaterial.locator.document_id`；
    * **送达 Writer**：本节写作清单里材料的 `material_id` 与 Pack 侧材料 id 的**交集**
      —— Pack 里有、清单里没有的材料不算送达（清单是读者面的边界）；
    * **写出**：草稿里该小节实际的句数；
    * **本栏要什么、取得了多少**（`cfgap-1`）：逐栏取冻结 Contract 那一栏自己的
      `required_fields`（**逐字**，是**要求**），与本栏名下的**合格事实**条数（是**取得**）
      并排。要了字段而一条事实都没有 ⇒ 逐栏报出 `required_fields_without_qualified_fact`：
      那是**读取 / 资格 / 交付**缺口，不是「来源称不适用」，也不是「用户缺件」。字段级
      支撑（哪一件各有几条）本表**判不出来**，不给编出来的逐字段对勾。
    * **算不算本小节正文**：现行 `sc-` 判据的栏目归属轴（`aspect_attribution`）逐句的结果。**写出**
      与**算数**是两件事：一句写着别的栏目内容的话照样能写出来，它只是在 `sc-3` 起**不**计入
      本小节的覆盖。**三列**并排给出（算数 / 不算数 / **本轴没判**），读者既不会把「有句子」
      读成「本栏已覆盖」，也不会把「本轴没判」读成「都算对」。

    **这三列的粒度是小节，不是本栏**：`aspect_attribution` 的判据是「本句所引来源登记在本
    小节声明的**某一栏**里」（`sections/sentence_check.py` 轴 7：`covered = registered &
    set(declared_set)`），它**判不出**「这一句服务的是哪一栏」，因此桶只能按 `subsection_id`
    归档，同一小节里十几行拿到同一个数。渲染时表头据实写明「（**本小节**，非本栏）」，
    并另有一段把这层粒度差说清楚；栏目级的分辨力只在「声明本栏的段数 / 这些段里的句数」
    两列上（`cw-4` 段级声明轴）。把这里的 3 读成「本栏被 3 句覆盖」，就是把小节级读数
    冒充成栏目级结论。

    **范围必须对齐**：写作清单是**节级**的（本节的每个 topic 一份 Pack，清单是其并集），
    因此 Pack 侧也取**本节 pack_set 全体 topic 的并集**。只拿 `topic_id` 那一份 Pack 去
    对一张节级清单，会把别的 topic 的材料记成「Pack 里没有」——那是范围错，不是缺材料。

    `disposition.material_id` 只出现在 `pack.materials` 里（Pack 的 exact-set 门保证），
    因此文档一律解析得出；万一解析不出，也落进显式桶名，不静默丢弃。

    返回 `None` = 本节**没有** Pack 材料边界（财务 artifact 那一支）。那时这张表不适用，
    调用方如实写「不适用」，而不是印一张全零的表。
    """
    authority = inputs.authorities.get(section_id)
    pack_set = getattr(authority, "pack_set", None)
    if pack_set is None:
        return None
    packs = tuple(getattr(pack_set, "packs", ()) or ())
    documents = [(str(e.document_id), str(e.document_version))
                 for e in inputs.source_manifest.source_set_entries()]
    material_ids: set[str] = set()
    doc_of_material: dict[str, str] = {}
    for pack in packs:
        for material in tuple(getattr(pack, "materials", ()) or ()):
            material_id = str(material.material_id)
            material_ids.add(material_id)
            doc_of_material[material_id] = str(
                getattr(getattr(material, "locator", None), "document_id", "") or "")
    writer_material_ids = {str(m.material_id) for m in manifest.materials
                           if str(m.material_id or "")}
    sentences_by_subsection: dict[str, int] = {}
    for subsection in draft.subsections:
        sentences_by_subsection[str(subsection.subsection_id)] = sum(
            len(paragraph.sentences) for paragraph in subsection.paragraphs)
    # `scp-5` 栏目归属轴：**算数** / **不算数** / **这一轴没判**——三桶，不两桶。
    # 只按 `verdict` 分两桶会把「本轴不适用」的 `pass` 记成「算本栏目」：那正是「没有这条轴」
    # 被读成「栏目全对上」的假门。第三桶不是零头，它是这一节到底有没有被这条轴看过的**唯一**
    # 读数，因此单独一列给出。
    in_column: dict[str, int] = {}
    out_of_column: dict[str, int] = {}
    axis_not_applicable: dict[str, int] = {}
    for record in tuple(getattr(check_report, "records", ()) or ()):
        if record.check_kind != "aspect_attribution":
            continue
        if not getattr(record, "applicable", True):
            bucket = axis_not_applicable
        else:
            bucket = (in_column if record.verdict == "pass" else out_of_column)
        key = str(record.subsection_id)
        bucket[key] = bucket.get(key, 0) + 1
    gaps_by_subsection: dict[str, list[str]] = {}
    for gap in draft.gaps:
        gaps_by_subsection.setdefault(str(gap.subsection_id), []).append(str(gap.reason))

    # ---- 段级声明账（`cw-4`／`scp-5`）--------------------------------------
    # `cwm-6` 起一个小节覆盖多个 Contract 栏目（冻结 WritingSpec 把 18 个 `company_business*`
    # 映到同一个 `co-h4`），「本小节写了几段」因此**不再**等于「本栏目写了几段」。真正有分辨
    # 力的是**段自己声明服务哪几栏**：段声明集合 ∩ 该段所引材料的登记归属 ≠ ∅ 才作数。
    # 这里逐栏清点「有几段声明了它、这些段里一共有几句」，与 Pack 侧登记数并排，构成覆盖账。
    paragraphs_declaring: dict[str, int] = {}
    sentences_in_declaring: dict[str, int] = {}
    for subsection in draft.subsections:
        for paragraph in subsection.paragraphs:
            for aspect in (getattr(paragraph, "aspect_ids", ()) or ()):
                name = str(aspect)
                paragraphs_declaring[name] = paragraphs_declaring.get(name, 0) + 1
                sentences_in_declaring[name] = (sentences_in_declaring.get(name, 0)
                                                + len(paragraph.sentences))

    #: 冻结 Contract 的逐栏 `required_fields`（**逐字**）与那一栏的展示档。它是**栏目要求的
    #: 字段名**——不是证据，也不是「本栏写了什么」。把它摆进这张表，是为了让「本栏要三件事」
    #: 与「本栏名下零条合格事实」并排可读：那正是本表要如实报出的**字段缺口**。
    required_fields: dict[str, tuple[str, ...]] = {}
    tier_by_aspect: dict[str, str] = {}
    for requirement in (getattr(inputs, "requirements", None) or {}).values():
        for aspect in getattr(requirement, "aspects", ()) or ():
            aspect_id = str(getattr(aspect, "aspect_id", "") or "")
            if not aspect_id:
                continue
            fields = tuple(str(f) for f in (getattr(aspect, "required_fields", ()) or ()))
            if fields:
                required_fields.setdefault(aspect_id, fields)
            tier = str(getattr(aspect, "display_tier", "") or "")
            if tier:
                tier_by_aspect.setdefault(aspect_id, tier)

    #: 本栏名下的**合格事实**（路径 A）条数：取自本节写作清单的事实行，按它们自己的
    #: `aspect_ids` 归属清点。它**不**等于「本栏覆盖已成立」（覆盖还要引用、还要蕴含），
    #: 也**不**能回答「三件事各有几条」——事实行只声明它属于哪一栏，不声明它对应哪一件**事**。
    #: 字段级支撑因此**判不出来**，本表只给栏目级的条数，不给一个编出来的逐字段对勾。
    qualified_facts: dict[str, int] = {}
    for fact in manifest.facts:
        for aspect in (getattr(fact, "aspect_ids", ()) or ()):
            name = str(aspect)
            qualified_facts[name] = qualified_facts.get(name, 0) + 1

    #: Pack 侧「哪条材料被认领到哪个栏目」：逐 (aspect, document) 清点，一次遍历算完。
    pack_by_aspect: dict[str, dict[str, int]] = {}
    delivered_by_aspect: dict[str, int] = {}
    for pack in packs:
        for disposition in tuple(getattr(pack, "material_dispositions", ()) or ()):
            material_id = str(disposition.material_id)
            if material_id not in doc_of_material:
                document = _UNRESOLVED_DOCUMENT
            else:
                document = doc_of_material[material_id] or _UNLABELLED_DOCUMENT
            delivered = material_id in writer_material_ids
            for aspect in (disposition.aspect_ids or ()):
                name = str(aspect)
                bucket = pack_by_aspect.setdefault(name, {})
                bucket[document] = bucket.get(document, 0) + 1
                if delivered:
                    delivered_by_aspect[name] = delivered_by_aspect.get(name, 0) + 1

    #: 逐句「这句话服务的是**哪一栏**」的**现算**读数（`cw-5` 读侧面，**不改**任何判据）。
    #:
    #: 判据与 `scp-5` 的栏目归属轴**同源**、只是从**小节级**下到**栏目级**：一句算作服务栏目 A
    #: ⇔ （它所在段声明了 A）**且**（它所引材料登记的栏目里含 A）。小节级那条轴说的是「本句
    #: 引的来源登记在本小节声明的**某一栏**里吗」，因此同小节十几行拿到同一个数；这里要回答的
    #: 是「**这一栏**到底有几句话在说它」——否则「一段挂七栏」这种写法在账上永远看不出来。
    #:
    #: 它**不是**判据、**不**产生 verdict、**不**进 `sentence_checks.json`：判据的取值域一旦
    #: 多出一档，全部历史逐句读数当场作废。这里是读回现算的分类读数。
    aspects_by_material: dict[str, set[str]] = {}
    key_to_material: dict[str, str] = {}
    for pack in packs:
        for disposition in tuple(getattr(pack, "material_dispositions", ()) or ()):
            bucket = aspects_by_material.setdefault(str(disposition.material_id), set())
            for aspect in (disposition.aspect_ids or ()):
                bucket.add(str(aspect))
    for member in manifest.materials:
        key_to_material[str(member.citation_key)] = str(member.material_id)
    sentences_for_aspect: dict[str, int] = {}
    materials_for_aspect: dict[str, set[str]] = {}
    for subsection in draft.subsections:
        for paragraph in subsection.paragraphs:
            declared_here = {str(a) for a in (getattr(paragraph, "aspect_ids", ()) or ())}
            if not declared_here:
                continue
            for sentence in paragraph.sentences:
                registered: set[str] = set()
                cited_materials: set[str] = set()
                for key in (getattr(sentence, "citations", ()) or ()):
                    material_id = key_to_material.get(str(key))
                    if material_id is None:
                        continue
                    cited_materials.add(material_id)
                    registered |= aspects_by_material.get(material_id, set())
                for aspect in declared_here & registered:
                    sentences_for_aspect[aspect] = sentences_for_aspect.get(aspect, 0) + 1
                    materials_for_aspect.setdefault(aspect, set()).update(cited_materials)

    #: 一行 = 一个 **Contract 栏目**（不是一个小节）。小节名与它的句子桶另列一栏，
    #: 让「这一栏有没有材料／有没有段落声明写它」与「它所在小节整体写了多少」并排可读。
    rows: list[dict] = []
    for spec in manifest.subsections:
        subsection_id = str(spec.subsection_id)
        # 不写 `or (subsection_id,)` 那种兜底：`CitedSubsectionSpec` 已保证声明集合非空，
        # 兜底只会在真出问题时**造出一栏没人认识的「栏目」**（名字还长得像小节名），
        # 把「这一节没声明栏目」印成「这一栏有材料、有段落」。空集合就该在构造期被拒。
        for aspect_id in spec.declared_aspect_ids:
            name = str(aspect_id)
            in_pack = pack_by_aspect.get(name, {})
            delivered = delivered_by_aspect.get(name, 0)
            declaring = paragraphs_declaring.get(name, 0)
            sentences_here = sentences_for_aspect.get(name, 0)
            materials_here = len(materials_for_aspect.get(name, ()))
            rows.append({
                "aspect_id": name, "subsection_id": subsection_id,
                "requirement_text": spec.requirement_text,
                "in_pack": in_pack,
                "delivered": delivered,
                "paragraphs_declaring": declaring,
                "sentences_declared": sentences_in_declaring.get(name, 0),
                "sentences": sentences_by_subsection.get(subsection_id, 0),
                "sentences_in_column": in_column.get(subsection_id, 0),
                "sentences_out_of_column": out_of_column.get(subsection_id, 0),
                "sentences_axis_not_applicable": axis_not_applicable.get(
                    subsection_id, 0),
                "required_fields": required_fields.get(name, ()),
                "required_field_count": len(required_fields.get(name, ())),
                "contract_display_tier": tier_by_aspect.get(name, ""),
                "qualified_facts": qualified_facts.get(name, 0),
                # ---- 栏目级「写到什么程度」的下界（读侧面现算）------------------
                "sentences_for_aspect": sentences_here,
                "cited_materials_for_aspect": materials_here,
                "substantive_state": _substantive_state(
                    in_pack=sum(in_pack.values()), delivered=delivered,
                    paragraphs_declaring=declaring, sentences_for_aspect=sentences_here,
                    materials_for_aspect=materials_here),
                "field_gaps": ((ASPECT_FIELD_GAP_REQUIRED_FIELDS_WITHOUT_FACT,)
                               if required_fields.get(name)
                               and not qualified_facts.get(name) else ()),
                "gaps": gaps_by_subsection.get(subsection_id, [])})
    return {"documents": documents, "rows": rows,
            "topic_ids": tuple(str(p.topic_id) for p in packs),
            "pack_material_count": len(material_ids),
            "writer_material_count": len(manifest.materials),
            "subsection_count": len(tuple(manifest.subsections))}


#: 逐份材料去向账里**材料粒度能观测到**的四态（互斥、穷尽）。另外两态——「源中未定位」与
#: 「已定位未读」——的主语不是「一份材料」而是「(栏目 × 来源)」：一份文档在某一栏上没被读到，
#: 不等于这份文档里没有材料。把它们塞进这张表，就会把「这一栏这次没读到」写成「这份材料不
#: 存在」——那正是本批要堵掉的那句话。它们由 §4.3.2 的四臂台账回答，两处各自说各自的话。
MATERIAL_DESTINATION_STATES = (
    "read_not_admitted",       # 读到内容并留了登记，但材料侧未获准入
    "admitted_not_delivered",  # 获准进 Pack，但没有进本节写作清单
    "delivered_not_used",      # 进了写作清单，Writer 判定本次未采用
    "used",                    # 采用：正文里有句子引用它
)

#: 材料粒度上的**缺陷态**：不是「一种去向」，是账对不上。单独列出来，不和四态混在一列。
MATERIAL_DESTINATION_DEFECT_STATES = (
    "delivered_without_writer_disposition",  # 进了清单，却没有 Writer 侧处理去向
    "in_manifest_without_pack_material",     # 在写作清单里，却在本节 Pack 里找不到
)


def _material_destination_ledger(*, inputs, section_id: str, manifest, draft,
                                 check_report):
    """逐份材料的**去向账**（`wmdl-1`）：从「进了 Pack」到「正文里引用了它」逐条可回查。

    这条账回答的是「这份材料最后去哪了」，与 §4.2 那张「栏目 × 材料」的账**不是**同一件事：
    那张表的主语是**栏目**（这一栏有没有材料），这张表的主语是**材料**（这份材料去了哪一栏、
    有没有被写出去）。同一份材料可以同时挂在几个栏目下，但它在**这张表里只有一行**。

    逐行给出四个轴，**各自取自已落盘的产物，互不推算**：

    * **研究侧登记**（`ResearchMaterialDisposition`，Pack 侧）：准入 / 保留 / 载体校验三轴与
      typed `reason_code` + `reason_proof`、四元身份（容器 / 来源 / 出处 / 内容指纹）；
    * **送达**：本节写作清单里有没有这条 `(pack_id, material_id)`；
    * **Writer 侧处理去向**（`WriterMaterialProcessingDisposition`，Writer 侧）：`used` /
      `not_used` 与它的封闭理由码 + 证明。**这是另一套身份**，不和研究侧那条混填；
    * **正文里的引用**：草稿里真正引用了这条材料的句子数，以及这些句子上 §6 那条轴判出的
      硬错数——「被采用」与「写得对」是两件事，这里并排给出，不合并。

    `not_used` 的理由码逐条照抄 Writer 侧登记的那一个，**不**在这里翻译成一句自由文本
    （「本轮没用到」与「Contract 必需事实未取得」是两条不同的轴，也不得互相顶替）。

    **四态的判据只用「已落盘、可观测」的那几轴**（研究侧登记 / 送达 / 正文引用），**不**要求
    Writer 侧自报：

    * `used` ⇔ 草稿里**确有句子引用它**——这是直接观测，不需要谁来自报「我用了」；
    * `delivered_not_used` ⇔ 进了清单、正文里没有句子引用它。**这一态只说「没写出去」**，
      至于为什么，要看 Writer 侧那一列——那一列**取不到**时读回如实说取不到（见下），
      不把「这一轴没被记录」印成「未采用」。

    因此本账单独返回 `writer_axis_present`：**这一份草稿到底有没有** Writer 侧处理去向这一
    轴。`CitedProseDraft` 今天**没有**这个字段（`getattr` 会静默给空元组），若不明说，
    整节 29 份材料就会各自被印成一条「进清单却没有去向」的缺陷——那是**读回自己造出来的
    二十九个假缺陷**，掩盖的恰恰是唯一的真事实：这条写作链上没有这一轴。

    返回 `None` = 本节**没有** Pack 材料边界（财务 artifact 那一支），与 §4.2 同一约定。
    """
    authority = inputs.authorities.get(section_id)
    pack_set = getattr(authority, "pack_set", None)
    if pack_set is None:
        return None
    packs = tuple(getattr(pack_set, "packs", ()) or ())

    #: 研究侧登记：逐 Pack 取 RMD，按材料 id 索引（Pack 门保证「每个 material 恰一条」）。
    rmd_by_key: dict[tuple[str, str], Any] = {}
    pack_material: dict[tuple[str, str], Any] = {}
    for pack in packs:
        pack_id = str(pack.pack_id)
        for disposition in tuple(getattr(pack, "material_dispositions", ()) or ()):
            rmd_by_key[(pack_id, str(disposition.material_id))] = disposition
        for material in tuple(getattr(pack, "materials", ()) or ()):
            pack_material[(pack_id, str(material.material_id))] = material

    #: 写作清单：按 (pack_id, material_id) 索引，并保留引用键（正文引用是按键找的）。
    manifest_member: dict[tuple[str, str], Any] = {}
    key_of_member: dict[tuple[str, str], str] = {}
    for member in manifest.materials:
        key = (str(member.pack_id), str(member.material_id))
        manifest_member[key] = member
        key_of_member[key] = str(member.citation_key)

    #: Writer 侧处理去向（另一套身份）。**先问「这一轴在不在」**，再去取它的值：
    #: 直接 `getattr(draft, "material_dispositions", ())` 会把「这条链没有这一轴」和
    #: 「这一节一份都没登记」读成同一件事，而二者要印的话完全不同。
    writer_axis_present = hasattr(draft, "material_dispositions")
    wmpd_by_key: dict[tuple[str, str], Any] = {}
    for disposition in tuple(getattr(draft, "material_dispositions", ()) or ()):
        wmpd_by_key[(str(disposition.pack_id), str(disposition.material_id))] = disposition

    #: 逐引用键数句子：引用了几次、其中几条带硬错。硬错取 §6 那条逐句读数本身，
    #: 不在这里重判一次（重判就等于多一个判据来源）。
    sentences_by_key: dict[str, int] = {}
    hard_by_key: dict[str, int] = {}
    sentence_hard: dict[str, bool] = {}
    for record in tuple(getattr(check_report, "records", ()) or ()):
        if str(getattr(record, "verdict", "")) == "hard_error":
            sentence_hard[str(getattr(record, "sentence_id", ""))] = True
    for subsection in draft.subsections:
        for paragraph in subsection.paragraphs:
            for sentence in paragraph.sentences:
                is_hard = sentence_hard.get(str(sentence.sentence_id), False)
                for cite in sentence.citations:
                    key = str(cite)
                    sentences_by_key[key] = sentences_by_key.get(key, 0) + 1
                    if is_hard:
                        hard_by_key[key] = hard_by_key.get(key, 0) + 1

    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for pack in packs:
        pack_id = str(pack.pack_id)
        for material in tuple(getattr(pack, "materials", ()) or ()):
            key = (pack_id, str(material.material_id))
            seen.add(key)
            rows.append(_material_ledger_row(
                pack_id=pack_id, topic_id=str(pack.topic_id), key=key, material=material,
                rmd=rmd_by_key.get(key), member=manifest_member.get(key),
                citation_key=key_of_member.get(key, ""), wmpd=wmpd_by_key.get(key),
                sentences_by_key=sentences_by_key, hard_by_key=hard_by_key,
                writer_axis_present=writer_axis_present))
    for key, member in manifest_member.items():
        if key in seen:
            continue
        #: 清单里有、本节 Pack 里没有：账对不上，如实列出来，不静默吞掉。
        rows.append({
            "pack_id": key[0], "topic_id": str(getattr(member, "topic_id", "")),
            "material_id": key[1], "document_id": str(getattr(member, "document_id", "")),
            "source_identity": str(getattr(member, "source_identity", "")),
            "provenance_identity": str(getattr(member, "provenance_identity", "")),
            "container_identity": "", "material_type": str(getattr(member, "material_type", "")),
            "locator": dict(getattr(member, "locator_ref", None) or {}),
            "content_fingerprint": "",
            "admission_state": "", "retention_state": "", "source_validation": "",
            "rmd_reason_code": "", "rmd_reason_proof": "",
            "aspect_ids": tuple(str(a) for a in (getattr(member, "aspect_ids", ()) or ())),
            "in_manifest": True, "citation_key": str(getattr(member, "citation_key", "")),
            "writer_usage": "", "writer_reason_code": None, "writer_reason_proof": None,
            "support_usages": (), "cited_sentences": sentences_by_key.get(
                str(getattr(member, "citation_key", "")), 0),
            "cited_hard_errors": hard_by_key.get(
                str(getattr(member, "citation_key", "")), 0),
            "state": "in_manifest_without_pack_material", "defect": True})

    _annotate_delivered_not_used(
        rows, text_by_key={str(m.citation_key): str(getattr(m, "reading_view", "") or "")
                           for m in manifest.materials})

    counts: dict[str, int] = {}
    defects: dict[str, int] = {}
    for row in rows:
        bucket = defects if row["defect"] else counts
        bucket[row["state"]] = bucket.get(row["state"], 0) + 1
    return {"rows": rows, "counts": counts, "defects": defects,
            "topic_ids": tuple(str(p.topic_id) for p in packs),
            "writer_axis_present": writer_axis_present,
            "pack_material_count": len(pack_material),
            "writer_material_count": len(manifest.materials)}


#: 数字表面台账的**互斥态**（书写顺序即优先级）。主语是**一个数字表面**，不是材料、不是栏目。
#: 「在不在 Pack 原文里」是另一根**正交**的轴（行里的 `in_pack`），不塞进这一列——两者组合
#: 才有信息量：`written_numeric_axis_clean` **且** `in_pack=False` 就是「写了、判据没拦它、
#: 可本节的 Pack 原文里查不到这个数字」这一格，它比单独任何一个值都值得看。
NUMERIC_SURFACE_STATES = (
    #: 写进了正文，且**数字轴**（`numeric_surface` / `numeric_qualification`）判出硬错。
    "written_numeric_axis_hard_error",
    #: 写进了正文，数字轴**没有**判硬错。**不**等于这个数字已获格级资格：数字轴只判
    #: 「这个表面在它引的来源面里逐字出现吗」，不判「来源面本身有没有数字权威」。
    "written_numeric_axis_clean",
    #: 出现在**本节 Pack 材料的原文**里，正文一句都没有写它 —— 这是「已送达未写出」。
    "in_pack_material_not_written",
)

#: 本链路**观察不到**的四段（写进读回，免得读者把「查不到」读成「被拒」）。
NUMERIC_CHAIN_UNOBSERVED_SEGMENTS = (
    ("原电子 PDF 位置 / 文字 / 格值", "原件只读展示（§6c）给的是区域像素与坐标哈希，逐格值不在本链产物里"),
    ("导航与工具结果", "研究与取材发生在 Harness 内，本链只消费它交出来的 Pack，不重放它的工具调用"),
    ("事实候选的生成", "本链产物里没有「候选」这一层记录"),
    ("逐条资格的拒绝原因", "材料级有 typed 理由码（§4.3 左列），**数字级**没有"),
)


def _numeric_surface_ledger(*, inputs, section_id: str, manifest, draft, check_report):
    """逐**数字表面**的最早丢失点台账（读侧面**现算**，不进 wire、不改判据）。

    主语是**一个数字表面**（`NS.scan_numeric_tokens` 扫出的 token，单位已含在 token 里；
    千分位与空白按 `NS` 的既有归一），不是「一份材料」也不是「一栏」。要回答的是：
    **这个数字最早在哪一步不见了**。

    本链路**可观测**的四段，逐段取自已落盘产物、互不推算：

    1. **Pack 材料原文**：`CitedMaterialEntry.reading_view`（清单里逐字可读的那一段）；
    2. **送达**：Pack 材料**全部**进本节写作清单（`manifest.materials`），因此「进了 Pack
       却没进清单」在这一链上是缺陷态，由 §4.3 单独报，不在本条账里；
    3. **正文**：`draft` 的逐句文本与逐句引用；
    4. **数字轴判定**：`check_report` 里 `numeric_surface` / `numeric_qualification` 两轴的
       逐句 `verdict` 与 `failure_reason`，**逐字取用、不在这里重判**。

    本链路**观察不到**的四段见 `NUMERIC_CHAIN_UNOBSERVED_SEGMENTS`。本台账**不**为那四段
    给任何一档：把「没有落盘记录」写成「候选没生成」「资格被拒」「扫描排除」「未进 Pack」，
    是本批要堵掉的那类推断 —— cp22 的产物里既没有候选层也没有逐条资格决定，**不能**据此
    宣布「全部被拒」。

    「未获准入」这一档在本账里**有**，但它取的是**材料级**的 typed 理由码（`§4.3` 那一列），
    **不是**数字级：一份材料未获准入时，它里面的每个数字都跟着记同一档，并在说明里写明
    「这是**材料**的理由，不是这个数字各自的理由」。

    返回 `None` = 本节没有 Pack 材料边界（财务 artifact 那一支），与 §4.2 / §4.3 同一约定。
    """
    authority = inputs.authorities.get(section_id)
    pack_set = getattr(authority, "pack_set", None)
    if pack_set is None:
        return None
    packs = tuple(getattr(pack_set, "packs", ()) or ())

    #: 材料级去向（含 typed 理由码）：`citation_key -> (admission_state, reason_code)`。
    #: 只用来给「Pack 里没写出去」的数字附上**材料级**的理由，不给数字级理由（那一层没有）。
    rmd_by_key: dict[tuple[str, str], Any] = {}
    for pack in packs:
        pack_id = str(pack.pack_id)
        for disposition in tuple(getattr(pack, "material_dispositions", ()) or ()):
            rmd_by_key[(pack_id, str(disposition.material_id))] = disposition
    material_reason: dict[str, tuple[str, str]] = {}
    for member in manifest.materials:
        disposition = rmd_by_key.get((str(member.pack_id), str(member.material_id)))
        material_reason[str(member.citation_key)] = (
            str(getattr(disposition, "admission_state", "") or ""),
            str(getattr(disposition, "reason_code", "") or ""))

    #: Pack 材料原文里的数字表面 —— 逐材料扫，记下「哪个数字出现在哪些引用键里」。
    #: 同时记下**首次出现它的那段原文**：`SC.is_qualified_numeric_surface` 判裸缩放词
    #: （`…千` 还是 `…千元`）时要回看 token 后面紧跟的那个字，没有这段原文就判不出。
    pack_tokens: dict[str, set[str]] = {}
    shape_text: dict[str, str] = {}
    for member in manifest.materials:
        key = str(member.citation_key)
        text = str(getattr(member, "reading_view", "") or "")
        for token in NS.scan_numeric_tokens(text):
            pack_tokens.setdefault(token, set()).add(key)
            shape_text.setdefault(token, text)

    #: 数字轴逐句硬错，**按判据自己给的表面串归属**：一条记录带 `surfaces` 时只记在那几个
    #: 表面上；不带时才退回整句归属，并在行里标出来。少了这一步，「同一句里另一个数字」
    #: 会被这条账一起判成硬错——那是读回自己造出来的假错，正是本批要分开的东西。
    numeric_hard_sentence: dict[str, list[str]] = {}
    numeric_hard_surface: dict[str, list[str]] = {}
    for record in tuple(getattr(check_report, "records", ()) or ()):
        if str(getattr(record, "verdict", "")) != "hard_error":
            continue
        if str(getattr(record, "check_kind", "")) not in ("numeric_surface",
                                                          "numeric_qualification"):
            continue
        reason = str(getattr(record, "failure_reason", ""))
        sid = str(getattr(record, "sentence_id", ""))
        surfaces = tuple(str(s) for s in (getattr(record, "surfaces", ()) or ()))
        if not surfaces:
            numeric_hard_sentence.setdefault(sid, []).append(reason)
            continue
        for surface in surfaces:
            numeric_hard_surface.setdefault(surface, []).append(reason)

    #: 正文里的数字表面 —— 逐句扫，记下「这个数字写在哪些句子里、那些句子引了什么」。
    draft_tokens: dict[str, dict[str, Any]] = {}
    for subsection in draft.subsections:
        for paragraph in subsection.paragraphs:
            for sentence in paragraph.sentences:
                sid = str(sentence.sentence_id)
                for token in NS.scan_numeric_tokens(str(sentence.text or "")):
                    row = draft_tokens.setdefault(
                        token, {"sentences": [], "citation_keys": set(), "hard": [],
                                "scope": ""})
                    row["sentences"].append(sid)
                    row["citation_keys"].update(str(c) for c in (sentence.citations or ()))
                    by_surface = numeric_hard_surface.get(token)
                    if by_surface:
                        row["hard"].extend(by_surface)
                        row["scope"] = "surface"
                    elif sid in numeric_hard_sentence:
                        #: 那条记录**没给表面串**（判据按整句判的），这里如实跟着整句归属，
                        #: 并在行里标成 `sentence` —— 「这句有个数字没来源」与「就是这个数字
                        #: 没来源」是两句话，读者要能分清自己在读哪一句。
                        row["hard"].extend(numeric_hard_sentence[sid])
                        row["scope"] = "sentence"
                    shape_text.setdefault(token, str(sentence.text or ""))

    #: 表体里凡是「必须拿格级来源或合格事实才能授权」的金额 / 比率一律**逐条列出**（这是本批
    #: 真正关心的那一小撮）；普通数量（`541GWh` 这类由已引来源文本直接承载的）只有**写进正文**
    #: 时才逐条列，没写的并成一条合计 —— 否则一张账会被几百个年份与台数淹没，真正要看的那几行
    #: 反而找不着。这条取舍**写在读回里**，不靠读者猜。
    listed: list[str] = []
    aggregate_only: list[str] = []
    for token in sorted(set(pack_tokens) | set(draft_tokens)):
        in_draft = token in draft_tokens
        if in_draft or SC.is_qualified_numeric_surface(token, shape_text.get(token, "")):
            listed.append(token)
        else:
            aggregate_only.append(token)

    rows: list[dict] = []
    for token in listed:
        draft_row = draft_tokens.get(token)
        keys = sorted(pack_tokens.get(token, ()))
        if draft_row is not None:
            state = ("written_numeric_axis_hard_error" if draft_row["hard"]
                     else "written_numeric_axis_clean")
        else:
            state = "in_pack_material_not_written"
        reasons = sorted(set(
            f"{material_reason.get(k, ('', ''))[0]}／{material_reason.get(k, ('', ''))[1]}"
            for k in keys)) if state == "in_pack_material_not_written" else []
        rows.append({
            "surface": token,
            "state": state,
            #: **正交轴**：这个表面在不在本节 Pack 材料的原文里。它与 `state` 组合才有信息量。
            "in_pack": bool(keys),
            "pack_keys": keys,
            "sentences": list(draft_row["sentences"]) if draft_row else [],
            "citation_keys": sorted(draft_row["citation_keys"]) if draft_row else [],
            "numeric_failure_reasons": sorted(set(draft_row["hard"])) if draft_row else [],
            #: 数字轴那条记录的归属范围：`surface` = 判据点名了这个表面；`sentence` = 判据
            #: 没给表面串、只能按整句记；空 = 数字轴没判它。
            "numeric_failure_scope": (draft_row["scope"] if draft_row else ""),
            "material_reasons": reasons,
        })

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    #: 最值得看的一格：写进正文、判据没拦它、**可本节 Pack 原文里查不到这个数字**。
    written_not_in_pack = [r["surface"] for r in rows
                           if r["state"].startswith("written_") and not r["in_pack"]]
    return {"rows": rows, "counts": counts, "aggregate_only": aggregate_only,
            "written_not_in_pack": written_not_in_pack,
            "pack_token_total": len(pack_tokens), "draft_token_total": len(draft_tokens),
            "unobserved_segments": NUMERIC_CHAIN_UNOBSERVED_SEGMENTS,
            "material_reason": material_reason}


def _material_ledger_row(*, pack_id, topic_id, key, material, rmd, member, citation_key,
                         wmpd, sentences_by_key, hard_by_key,
                         writer_axis_present: bool) -> dict:
    """一行材料去向。判定次序**写在下面**，不靠读者从几个字段反推。

    次序的用意：**缺陷先于去向**（对不上账的行不该被读成一种正常去向），而**观测先于自报**
    （正文里有没有句子引用它，是看得见的；`usage` 是别人自报的）。
    """
    locator = getattr(material, "locator", None)
    document_id = str(getattr(locator, "document_id", "") or "")
    admission = str(getattr(rmd, "admission_state", "") or "")
    in_manifest = member is not None
    writer_usage = str(getattr(wmpd, "usage", "") or "")
    cited = sentences_by_key.get(citation_key, 0)
    #: 「进了清单却没有去向」只有在**这一轴确实存在**时才算缺陷。轴根本不在（今天的
    #: `CitedProseDraft` 就是如此）时，缺的不是这一份的去向，是整条链的这一轴——那件事由
    #: `writer_axis_present` 在表头说一次，不逐份重复 29 遍。
    missing_disposition = writer_axis_present and in_manifest and wmpd is None
    if admission == "rejected":
        state, defect = "read_not_admitted", False
    elif not in_manifest:
        state, defect = "admitted_not_delivered", False
    elif missing_disposition:
        state, defect = "delivered_without_writer_disposition", True
    elif cited > 0:
        state, defect = "used", False
    else:
        state, defect = "delivered_not_used", False
    return {
        "pack_id": pack_id, "topic_id": topic_id, "material_id": key[1],
        "document_id": document_id,
        "source_identity": str(getattr(material, "source_identity", "") or ""),
        #: 出处身份与研究侧容器身份**同源**：两者都是 Pack 侧算出并登记在 RMD 上的派生串。
        #: `ResearchMaterial` 自己**没有** `provenance_identity` 字段（出处是从 locator +
        #: payload 派生出来的），从 material 上取只会静默得到空串，把「逐条可回查」印成一片「—」。
        "provenance_identity": str(getattr(rmd, "provenance_identity", "") or ""),
        "container_identity": str(getattr(rmd, "container_identity", "") or ""),
        "material_type": str(getattr(material, "material_type", "") or ""),
        "locator": dict(getattr(locator, "to_dict", lambda: {})() or {}),
        "content_fingerprint": str(getattr(rmd, "content_fingerprint", "") or ""),
        "admission_state": admission,
        "retention_state": str(getattr(rmd, "retention_state", "") or ""),
        "source_validation": str(getattr(rmd, "source_validation", "") or ""),
        "rmd_reason_code": str(getattr(rmd, "reason_code", "") or ""),
        "rmd_reason_proof": str(getattr(rmd, "reason_proof", "") or ""),
        "aspect_ids": tuple(str(a) for a in (getattr(rmd, "aspect_ids", ()) or ())),
        "in_manifest": in_manifest, "citation_key": citation_key,
        "writer_usage": writer_usage,
        "writer_reason_code": getattr(wmpd, "reason_code", None),
        "writer_reason_proof": getattr(wmpd, "reason_proof", None),
        "support_usages": tuple(getattr(wmpd, "support_usages", ()) or ()),
        "cited_sentences": cited, "cited_hard_errors": hard_by_key.get(citation_key, 0),
        "state": state, "defect": defect}


#: `delivered_not_used` 三种**读时派生**读数的字段名（`munr-1`）。它们**不是** Writer 侧登记：
#: `MaterialAdoptionRecord`（`sections/cited_writer.py:2417`）是严格 wire 类型且
#: `record_id` 对其 `identity_body()` 取哈希——**加字段 = 换掉全部历史 `cadopt_*` id**。
#: 因此「为什么没写出去」只在这里**现算**，不落盘、不进任何身份体。
MATERIAL_UNUSED_DERIVED_KEYS = ("duplicate_of", "sole_carrier_sentences", "derived_note")


def _compact_for_duplicate(text: str) -> str:
    """只用于**重复判定**的归一（去掉全部空白，`\\s` 在 str 模式下含全角空格）。不改写产物。"""
    return re.sub(r"\s+", "", str(text or ""))


def _annotate_delivered_not_used(rows: list[dict], *, text_by_key: dict[str, str]) -> None:
    """给材料台账逐行补**读时派生**的三列（`munr-1`），只对 `delivered_not_used` 有意义。

    回答的是「这份材料送了却没写出去，**和别的材料比它特殊在哪**」——正文里既然没有它的句子，
    就只能拿**可见的东西**比：它的正文是不是与**已被引用**的某一份逐字重复、它有多少句是
    **任何已被引用材料里都没有的**。三条都是**观测**，都**不落盘**（见
    :data:`MATERIAL_UNUSED_DERIVED_KEYS` 的说明）。

    **它绝不冒充 Writer 侧的理由码**：`WriterMaterialProcessingDisposition` 这一轴在本链上不
    存在（`writer_axis_present` 已在表头说清），这三个值不是它的替代品，也不解释动机。
    其余状态的行一律补空值——「这一列对这份材料不适用」与「算了但为零」在产物上不能长得一样。
    """
    used_keys = [str(row["citation_key"]) for row in rows if row["state"] == "used"]
    used_compact = {k: _compact_for_duplicate(text_by_key.get(k, "")) for k in used_keys}
    haystack = "\u0000".join(t for t in used_compact.values() if t)
    for row in rows:
        if row["state"] != "delivered_not_used":
            row.update({"duplicate_of": "", "sole_carrier_sentences": 0, "derived_note": ""})
            continue
        text = text_by_key.get(str(row["citation_key"]), "")
        compact = _compact_for_duplicate(text)
        duplicate_of = next((k for k in used_keys
                             if used_compact[k] and used_compact[k] == compact), "")
        sole = sum(1 for start, end in PE.split_sentence_spans(text)
                   if _compact_for_duplicate(text[start:end])
                   and _compact_for_duplicate(text[start:end]) not in haystack)
        row.update({"duplicate_of": duplicate_of, "sole_carrier_sentences": sole,
                    "derived_note": "与已被引用材料逐字重复" if duplicate_of else ""})


def _source_arm_ledger(*, inputs, section_id: str):
    """逐 `(栏目 × 来源)` 的**四臂结果**（`sarm-1`）——「源里有没有、读到没有」在这一级才有主语。

    这一级与 §4.3 的**材料粒度**账是两件事，因此分两张表：材料粒度只能回答「这份材料去了
    哪一栏」；「这一栏在这份来源上有没有被读到」的主语是**格子**，不是材料。把两者混成一张，
    就会把「这一栏这次没查到」印成「这份材料不存在」——那是本读回要堵的那句话。

    四臂的取值域与投影终态逐字取自 `harness.topic_schema`（**不在这里另立一套词**）：
    `A` 产出材料、`B` 搜索过（带终态）、`C1` 本就不要求、`C2` 要求检索但本轮未查成。
    逐条记录**刚好一条** `(栏目, 来源)`，由 Pack 自身的门保证。

    **每一格还并排一条登记轴读数**（`cfgap-1` 同批）：这一格在 Pack 的材料登记
    （`material_dispositions[].aspect_ids` × `locator.document_id`）里**有没有材料**。
    它必须与臂并排，因为两者会打架而这正是要如实印出来的事：`C2` 说的是「**没有检索痕迹**」，
    它**不**说「没有材料」——一只按 `if traces and materials` 判定臂别的实现会把「有材料、
    只是没留下检索痕迹」的那些格统统记成 `not_dispatched`。把登记轴摆在旁边，
    「已有材料、只是没有检索痕迹」与「既无材料也无痕迹」就不会再被读成同一句话。
    本函数**不**改臂的取值（那是 `harness/topic_runtime` 的判定），只把两条轴并排。

    返回 `None` = 本节没有 Pack 材料边界（财务 artifact 那一支）。
    """
    authority = inputs.authorities.get(section_id)
    pack_set = getattr(authority, "pack_set", None)
    if pack_set is None:
        return None
    #: 登记轴：逐 (栏目, 文档) 清点 Pack 侧材料**自报**的归属。文档取材料自己的
    #: `locator.document_id`，取不到就落显式桶名（与 §4.2 同一约定），不静默丢弃。
    doc_of_material: dict[str, str] = {}
    registered: dict[tuple[str, str], int] = {}
    for pack in tuple(getattr(pack_set, "packs", ()) or ()):
        for material in tuple(getattr(pack, "materials", ()) or ()):
            doc_of_material[str(material.material_id)] = str(
                getattr(getattr(material, "locator", None), "document_id", "") or "")
        for disposition in tuple(getattr(pack, "material_dispositions", ()) or ()):
            material_id = str(disposition.material_id)
            if material_id not in doc_of_material:
                document = _UNRESOLVED_DOCUMENT
            else:
                document = doc_of_material[material_id] or _UNLABELLED_DOCUMENT
            for aspect in (disposition.aspect_ids or ()):
                key = (str(aspect), document)
                registered[key] = registered.get(key, 0) + 1
    rows: list[dict] = []
    documents: list[str] = []
    aspects: list[str] = []
    arm_counts: dict[str, int] = {}
    for pack in tuple(getattr(pack_set, "packs", ()) or ()):
        for outcome in tuple(getattr(pack, "source_aspect_outcomes", ()) or ()):
            key = getattr(outcome, "source_document_key", None)
            document_id = str(getattr(key, "document_id", "") or "")
            arm = str(getattr(outcome, "arm", "") or "")
            record = getattr(outcome, "search_record", None)
            row = {
                "aspect_id": str(getattr(outcome, "aspect_id", "") or ""),
                "document_id": document_id, "arm": arm,
                "material_ids": tuple(str(m) for m in (getattr(outcome, "material_ids", ()) or ())),
                "projected_terminal": str(getattr(record, "projected_terminal", "") or ""),
                "synthesized_stop_reason": str(
                    getattr(record, "synthesized_stop_reason", "") or ""),
                "unfulfilled_reason": str(getattr(record, "unfulfilled_reason", "") or ""),
                "qualified": bool(getattr(record, "qualified", False)),
                "not_required_basis": tuple(
                    f"{a}:{b}" for a, b in (getattr(outcome, "not_required_basis", ()) or ())),
            }
            #: 登记轴读数（与本行同一格）：这一格在 Pack 材料登记里有没有材料。
            row["registered_materials"] = registered.get(
                (row["aspect_id"], document_id), 0)
            rows.append(row)
            arm_counts[arm] = arm_counts.get(arm, 0) + 1
            if document_id and document_id not in documents:
                documents.append(document_id)
            if row["aspect_id"] and row["aspect_id"] not in aspects:
                aspects.append(row["aspect_id"])
    return {"rows": rows, "documents": tuple(documents), "aspect_ids": tuple(aspects),
            "arm_counts": arm_counts, "record_count": len(rows)}


def _gap_bin_readback(*, payload, version) -> "list[str]":
    """草稿缺口**逐条分桶**的读者面（`crpp-5`）。

    改这一块要修的误读很具体：旧读回在这里只印一行「必需缺口：N」，而 N 取的是
    `len(draft.gaps)`——**可选栏与合法不适用栏被一起算成「Contract 要求的覆盖未达」**。
    于是同一页上，「这一栏本轮没有可比期间、Contract 自己判不适用」与「这一栏该写而没写」
    长得一模一样，读的人无从分辨。

    现在**数与桶一起印**，并且逐条给出每一格是**按哪一条 Contract 声明**分进去的
    （`policy_basis` + Contract 栏 id + 适用性政策原文）。桶不是数的注释，是数的**来源**：
    读者能拿这份表当场反驳分桶，而不是只能接受一个数字。
    """
    bins = tuple(payload.get("gap_bins") or ())
    by_bin: dict[str, list[Any]] = {name: [] for name in CRP.CITED_GAP_BINS}
    for item in bins:
        by_bin.setdefault(item.gap_bin, []).append(item)
    L: list[str] = [
        "**草稿缺口逐条分桶**（`crpp-5`：口径是**冻结 Contract**的适用性政策与展示档，"
        "不是「凡缺口皆必需」）：",
        "",
        "| 桶 | 条数 | 计不计入「必需覆盖未达」 | 凭什么分到这一档 |",
        "|---|---|---|---|",
    ]
    meaning = {
        "required": ("计入", "Contract 要求写，且没有任何可豁免声明"),
        "unresolved": ("**计入**（fail-closed）",
                       "**在冻结 Contract 里找不到这一栏** —— 查不到政策时按必需处理，"
                       "不是宽容"),
        "optional": ("不计入", "`display_tier: optional_body`"),
        "not_applicable": ("不计入",
                           "`applicability_policy` 落在冻结 Contract `missing_policies` 明文"
                           "『合法不适用（NOT_APPLICABLE）、不阻断』的那两条上"),
        "diagnostic": ("不计入", "`display_tier: diagnostic_only`"),
    }
    for name in CRP.CITED_GAP_BINS:
        counted, why = meaning[name]
        L.append(f"| `{name}` | {len(by_bin.get(name) or ())} | {counted} | {why} |")
    L += [
        "",
        f"草稿缺口共 **{len(bins)}** 条，计入必需的是 **{version.required_gap_count}** 条"
        f"（= `required` + `unresolved`）。**不**计入必需不等于「已经写到」：那些栏目同样"
        f"一条都没写出来，只是他们的缺席不是「Contract 要求的覆盖未达」。",
        "",
    ]
    if bins:
        L += ["| 缺口 id | Contract 栏 | 桶 | 分桶依据 | 展示档 | 适用性政策 | 要求文本 |",
              "|---|---|---|---|---|---|---|"]
        for item in bins:
            L.append(
                f"| `{item.gap_id}` | `{item.contract_aspect_id or '（查不到）'}` | "
                f"`{item.gap_bin}` | `{item.policy_basis}` | "
                f"`{item.display_tier or '—'}` | `{item.applicability_policy or '—'}` | "
                f"{_md_cell(item.requirement_text)} |")
        L.append("")
    return L


_NUMERIC_STATE_LABEL = {
    "written_numeric_axis_hard_error": "写进正文·数字轴判硬错",
    "written_numeric_axis_clean": "写进正文·数字轴未判硬错",
    "in_pack_material_not_written": "Pack 原文里有·正文没写",
}


def _numeric_surface_ledger_readback(*, ledger) -> "list[str]":
    """逐**数字表面**的最早丢失点台账（读侧面现算）。

    要修的误读：一个「0 条合格事实」被读成「所有数字都被拒了」。数字的路径与材料的路径
    **不是同一条**，材料级的理由码也**不是**数字级的理由。本表把「Pack 原文里有没有这个
    数字」「正文写没写它」「数字轴判没判它」三件事分开列，并把**这条链观察不到的那几段**
    逐段写明——把「没有记录」写成「被拒绝了」正是本批要堵掉的推断。
    """
    L: list[str] = ["## 6d. 逐数字表面的最早丢失点台账（读侧面现算；**不是**判据）", "",
                    "**主语是一个数字表面**（`scan_numeric_tokens` 的 token，单位已含在内；"
                    "千分位与空白按既有归一），不是材料、不是栏目。要回答的是：**这个数字最早"
                    "在哪一步不见了**。", ""]
    if ledger is None:
        L += ["（本节**没有** Pack 材料边界：本条账因此**不适用**——不是全零，是不适用。）", ""]
        return L
    counts = ledger["counts"]
    L += ["| 数字表面 | **状态** | 在 Pack 原文里 | 引用键 | 正文句 | 数字轴原因码（归属） | "
          "材料级理由（**材料**的理由，不是这个数字各自的） |",
          "|---|---|---|---|---|---|---|"]
    for row in ledger["rows"]:
        label = _NUMERIC_STATE_LABEL.get(row["state"], row["state"])
        surfaces = "、".join(f"`{_md_cell(s)}`" for s in row["sentences"]) or "—"
        keys = "、".join(f"`{_md_cell(k)}`" for k in row["pack_keys"]) or "—"
        cites = "、".join(f"`{_md_cell(k)}`" for k in row["citation_keys"]) or "—"
        reasons = "、".join(f"`{_md_cell(r)}`" for r in row["numeric_failure_reasons"]) or "—"
        if row["numeric_failure_scope"] == "sentence":
            reasons += "（**整句归属**：判据没点名表面串）"
        elif row["numeric_failure_scope"] == "surface":
            reasons += "（判据点名了这个表面）"
        mat_reasons = "、".join(f"`{_md_cell(r)}`" for r in row["material_reasons"]) or "—"
        L.append(f"| `{_md_cell(row['surface'])}` | **{label}** | "
                 f"{'是' if row['in_pack'] else '否'} | {keys} | {surfaces}（引 {cites}） | "
                 f"{reasons} | {mat_reasons} |")
    L += [
        "",
        "**逐档计数**："
        + "、".join(f"{_NUMERIC_STATE_LABEL[s]} **{counts.get(s, 0)}**"
                    for s in NUMERIC_SURFACE_STATES)
        + "。",
        "",
        "**「在 Pack 原文里」是**另一根正交的轴**，不与状态合一**。最值得看的一格是两者"
        "组合：**写进正文、数字轴没拦它、可本节 Pack 原文里查不到那个数字** —— "
        + (("本次这样的表面有 **" + str(len(ledger["written_not_in_pack"])) + "** 个："
            + "、".join(f"`{_md_cell(s)}`" for s in ledger["written_not_in_pack"]))
           if ledger["written_not_in_pack"] else
           "本次**没有**这样的表面。")
        + " 这一格不是「数字已合格」，也不是「数字是编的」——它说的是**本链的可见范围内**"
        "找不到它的原文出处，下一步要么补上材料、要么按无资格撤下。",
        "",
        f"**列入口径（写下来免得读者猜）**：Pack 材料原文里一共扫出 **{ledger['pack_token_total']}** "
        f"个数字表面，正文里一共 **{ledger['draft_token_total']}** 个。本表**逐条列出**两类："
        "①**写进正文的**（读者的读数直接受它影响）；②**形状上必须拿格级来源或合格事实才能"
        f"授权的**（金额 / 比率，`is_qualified_numeric_surface` 判定）。其余 **{len(ledger['aggregate_only'])}** "
        "个是普通数量（年份、台数、家数这类），它们**没有**逐条列出——不是被藏起来，"
        "是挤在一张表里会把真正要看的那几行淹掉；其中凡**写进正文**的仍在上表里逐条在场。",
        "",
        "**这张表观察不到的四段（逐段写明，不给任何一档）**：",
        "",
        "| 那一段 | 为什么本链看不到 |",
        "|---|---|",
    ]
    for name, why in ledger["unobserved_segments"]:
        L.append(f"| {_md_cell(name)} | {_md_cell(why)} |")
    L += [
        "",
        "**由此推出的读法（四句，缺一不可）**：",
        "- 「Pack 原文里有、正文没写」= **已送达未写出**，它**不是**「被拒了」："
        "本表左列写明这个数字确实出现在已登记的原文里；",
        "- 「Pack 原文里**查不到**这个数字」（`在 Pack 原文里 = 否`）= **本链可见范围内"
        "没有它的出处**，它**不是**「来源里没有」，更**不是**「候选没生成 / 资格被拒 / "
        "扫描排除」——那四档各自要有 typed 记录才谈得上，而本链产物里**没有**这几段；"
        "它指的只是：要么该取材而没取到，要么这个数字本来就不该由这条链写出来；",
        "- 「写进正文·数字轴未判硬错」**不等于**这个数字已获**格级数字权威**：数字轴判的是"
        "「这个表面在它引的来源面里逐字出现吗」，它**不**判「来源面本身有没有数字资格」。"
        "把这一档读成「数字已合格」，就是把判据的覆盖面放大了一整段；",
        "- 原因码后面那个**归属括号**要读：`判据点名了这个表面` 才是「**这个数字**被判的」；"
        "`整句归属` 说的是「**这一句**里有数字没来源」，判据没点名是哪一个——"
        "把后者读成前者，就会把同句里另一个有来源的数字一起误伤。",
        "",
        "**材料级理由码那一列的主语是材料**：一份材料本次未获准入时，它里面的每个数字都跟着"
        "记同一档。**数字级**的逐条资格理由在本链产物里**没有**（那是另一套接口，见上表第三行）。",
        "",
    ]
    return L


def _check_family_readback(*, check_report) -> "list[str]":
    """逐句机械结论**按族分列**的读者面（`crpp-5`）。

    改这一块要修的误读同样具体：旧读回只给一个「硬错误 N 条」，读者无法从它分出
    「**来源撑不住这句话**」与「**来源撑得住、只是它服务的是别的一栏**」——而这两件事的
    下一步动作完全不重叠（前者要撤数或补资格，后者要补一次正确的取材 / 改栏）。第三个数
    「这一轴**这次没判**」更不能混进去：它既不是通过也不是失败。

    三个数**并列印出、互不推算**，每一族的逐条原因码也一并给出，读者能拿这份表回到
    `sentence_checks.json` 当场复核。
    """
    split = CRP.classify_check_report(check_report)
    L: list[str] = [
        "**硬错误按族分列**（`crpp-5`；族由 `sentence_checks.json` 里已持久化的原因码"
        "**现算**，不写进任何 wire，也不改任何判据）：",
        "",
        "| 族 | 记录数 | 句数 | 这一族说的是什么 |",
        "|---|---|---|---|",
    ]
    meaning = {
        "fact_safety": ("**来源 / 资格撑不住这句话**（数字无资格、期间/主体/否定无来源、"
                        "历史材料当当前态、模板文字当公司事实、表数字无格级来源…）"
                        "——按硬错阻断"),
        "column_coverage": ("**字与来源都成立，错在「它服务的是别的一栏」**"
                            "（`aspect_attribution`／`presentation_column_attribution`）"
                            "——处置是**补一次正确的取材 / 改栏**，"
                            "**不是**「原文事实是假的」"),
    }
    for family in SC.CHECK_FAMILIES:
        row = (split["hard"] or {}).get(family) or {}
        L.append(f"| `{family}` | {row.get('records', 0)} | {row.get('sentences', 0)} | "
                 f"{meaning.get(family, '')} |")
    diagnostic = split.get("diagnostic") or {}
    fact = (split.get("hard") or {}).get("fact_safety") or {}
    column = (split.get("hard") or {}).get("column_coverage") or {}
    L += [
        f"| `{split['diagnostic_family']}` | **{diagnostic.get('records', 0)}** | — | "
        "**不是失败**：这一轴这次**没判**（`applicable=False`：本句没有数字 / 没有引用表材料 / "
        "本节没有栏目登记轴…）。它既不通也不过，两个相反方向都不许拿它当自己那一侧的证据 |",
        "",
        f"- 事实安全失败：**{fact.get('sentences', 0)}** 句"
        f"（{fact.get('records', 0)} 条记录）；"
        f"栏目覆盖失败：**{column.get('sentences', 0)}** 句"
        f"（{column.get('records', 0)} 条记录）；"
        f"判据没判：**{diagnostic.get('records', 0)}** 条记录。",
        f"- 三个数的关系：**句可以同时落在两族**（一句既引错栏、又有无资格数字时两族各记一次），"
        f"因此两族句数之和 ≥ `blocked_sentence_ids` 的 {len(split['blocked_sentence_ids'])} 句；"
        f"记录数之和则**恰好等于**全部硬错记录数 {split['hard_record_total']}（与 §6 的轴归并表同源）。",
        "- **栏目覆盖失败不得冒充数字编造**：它可能意味着冻结 Contract 的这一点尚未回答，"
        "但它**不是**「这句写了假数字」，两类失败的返修动作不重叠；把两者并成一个数，"
        "读者会去做错的返修。",
        "",
    ]
    for family in SC.CHECK_FAMILIES:
        reasons = ((split.get("hard") or {}).get(family) or {}).get("reasons") or {}
        if not reasons:
            continue
        L.append(f"- `{family}` 的原因码：" + "、".join(
            f"`{code}` {count}" for code, count in sorted(reasons.items(), key=lambda kv: -kv[1])))
    if diagnostic.get("by_kind"):
        L.append("- 判据没判（`applicable=False`）按轴：" + "、".join(
            f"`{kind}` {count}" for kind, count in sorted(
                diagnostic["by_kind"].items(), key=lambda kv: -kv[1])))
    L.append("")
    if split["sentences"]:
        L += ["| 句 id | 落在哪几族 | 原因码 |", "|---|---|---|"]
        for row in split["sentences"]:
            L.append(f"| `{row['sentence_id']}` | "
                     + "／".join(f"`{f}`" for f in row["families"]) + " | "
                     + "、".join(f"`{r}`" for r in row["reasons"]) + " |")
        L.append("")
    return L


def _readings_separation_note(*, manifest, draft, version) -> "list[str]":
    """把**五个不同的读数**分开写（`crpp-5`）。这一块不产出新数字，它只做一件事：
    **不许把一个读数读成另一个**。

    本批在 `cp22` 上真实发生过的误读，正是把这五个数并成一句：

    1. **Pack 材料数**（「清单成员数」）：本节 Pack 里有多少条材料身份；
    2. **合格事实数**（「合格事实数」）：本节清单里有多少条**权威事实行**。公司节这一支
       **恒为 0** 是本链的设计（业务材料走 Path B 的 `ResearchMaterial`，不走权威事实行），
       它**不是**「材料全都不可用」；
    3. **栏目级字段缺口**（§4.2 的「字段缺口」）：冻结 Contract 在**某一栏**点名要的那几件
       字段，本轮**在本栏名下**一条合格事实都没有。它**不是**「这一栏没有正文」——
       描述性栏目完全可以由**已定位的 Pack 原文**支撑（材料本身带 `reading_view` 与
       locator），而字段级对勾本表判不出来；
    4. **草稿缺口数**（`cited_gaps` / `cited_prose.json` 的 `gaps`）：**写作侧**这一稿报了几条
       「这一小节为什么没写」的缺口。它的主语是**小节**，不是栏目；多个栏目共处一个小节时，
       同一个数会印在十几行上；
    5. **真正必需的缺口数**（`required_gap_count`，`crpp-5` 起）：把上面第 4 项按**冻结
       Contract 的适用性政策与展示档**逐条分桶后，只剩「Contract 要求写且无可豁免声明」的
       那几条（`required` + `unresolved`）。**可选栏、Contract 自己判不适用栏、诊断栏不计入。**

    第 4 项与第 5 项**不是同一个数**（`cp22` 上是 8 与 5），第 2 项与第 3 项也不是。
    """
    gaps = tuple(getattr(draft, "gaps", ()) or ())
    return [
        "**五个读数，互不换算**（同一页上它们都出现，把其中任何一个读成另一个都是误读）：",
        "",
        "| # | 读数 | 本节的数 | 它的主语 / 它**不**说明什么 |",
        "|---|---|---|---|",
        f"| 1 | Pack 材料数 | **{len(manifest.materials)}** | 材料身份。不是「有多少内容被写出来」|",
        f"| 2 | 权威事实行 | **{len(manifest.facts)}** | 本节清单的事实轴。公司节这一支的"
        "业务材料走 Pack 原文（Path B），**恒为 0 是设计**，不是「材料全都不可用」|",
        "| 3 | 栏目级字段缺口 | 见 §4.2 | **栏目**：Contract 点名要的字段本轮有几栏一件都没取得。"
        "**不是**「这一栏没有正文」——描述性栏目可由已定位的 Pack 原文支撑 |",
        f"| 4 | 草稿缺口 | **{len(gaps)}** | **写作小节**：这一稿报了几条「为什么没写」。"
        "同一小节下多个栏目会印同一个数 |",
        f"| 5 | 必需缺口（`required_gap_count`） | **{version.required_gap_count}** | "
        "第 4 项按冻结 Contract 的适用性政策与展示档分桶后**剩下的必需项**；"
        "可选 / 合法不适用 / 诊断栏**不计入**（逐条依据见 §8 的分桶表与 "
        "`cited_gap_bins.json`）|",
        "",
        "第 4 项与第 5 项**不相等是正常的**（可选栏与 Contract 自己判不适用的栏照旧如实列着，"
        "只是不再冒充「Contract 要求的覆盖未达」）；第 2 项为 0 与第 3 项为满，说的也是"
        "**两件不同的事**：前者是「这一支不走权威事实行」，后者是「这一栏的字段本轮没取得为"
        "合格事实」。原 PDF 表区的只读展示（§6c）**不**改变其中任何一个读数。",
        "",
    ]


def _render_readback(*, run_id, profile, subject, subject_name, clock, section_id,
                     topic_id, inputs, payload, table_matrix, source_display) -> str:
    manifest = payload["manifest"]
    outcome = payload["outcome"]
    #: 读回读的是**有效草稿**（返修成功即新稿）。原稿的读数没有被覆盖：它逐字留在
    #: `cited_prose.json` 里；**返修成功时**初稿的逐句读数另存 `sentence_checks_initial.json`
    #: （生效读数与初稿不同才落盘），两者之差就是这一步的效果。返修失败时生效读数**就是**
    #: 初稿读数，因此没有 `_initial` 副本——留档说明由 `checks_have_base` 如实分流。
    draft = payload.get("active_draft") or outcome.draft
    check_report = payload.get("active_check_report") or payload["check_report"]
    checks_have_base = check_report is not payload["check_report"]
    review_outcome = payload["review_outcome"]
    version = payload["version"]
    scan = payload["scan"]
    mode = str(payload.get("mode") or ACC.MODE_OFFLINE)
    approved_model = payload.get("approved_model")
    budget = payload.get("budget")
    source_scopes = tuple(payload.get("source_scopes") or ())
    routing = payload.get("presentation_routing")
    topic_ids = tuple(payload.get("topic_ids") or (topic_id,))

    _real = mode == ACC.MODE_REAL
    _prose_src = (f"**真实模型写作**（`{approved_model}`，`{CW.CITED_WRITER_PROMPT_VERSION}`）"
                  if _real else "离线抽取式替身客户端")
    _review_src = (f"**真实独立审阅**（`{approved_model}`，`{CR.CITED_REVIEW_PROMPT_VERSION}`）"
                   if _real else "离线回声替身（`offline_diagnostic_echo`）")

    L: list[str] = [f"# M930-3 双节纵链：同版本业务读回（`{run_id}`）", "", _BANNER, ""]
    L += [
        "## 0. 这份读回的坐标",
        "",
        f"- demo scope profile：`{profile.profile_id}`（指纹 `{profile.profile_fingerprint}`）",
        f"- 节：`{section_id}`；本次选中 topic（**逐字取自本节任务**）："
        + "、".join(f"`{t}`" for t in topic_ids)
        + f"；其中写正文的是 `{topic_id or '（无）'}`"
        + (f"，确定性呈现的是 "
           + "、".join(f"`{s.topic_id}`" for s in source_scopes) if source_scopes else ""),
        f"- 主体声明：`{subject}`／`{subject_name}`；`report_as_of={clock.report_as_of}`",
        f"- 主体来源：{'命令行显式声明' if subject else '只读列出财务库候选后取第一个'
          }（本脚本是读回工具，不是报告输入声明路径）",
        f"- 模式：`{mode}`；研究侧 LLM：**确定性替身**（`MODE_OFFLINE`，"
        f"{'真实模式下也不变' if _real else '本模式全程'}）；写作侧／审阅侧："
        f"{_prose_src}／{_review_src}",
        (f"- 联网：`--mode real` **不装** `_NetworkGuard`（获批的正是那几次真实写作/审阅），"
         f"约束改由共享预算门承担：模型写死、份数写死、重试 0；研究侧仍逐字走离线装配。"
         if _real else
         f"- 联网：全程 `_NetworkGuard` 拦截 provider 入口，实测被拦截次数 "
         f"`0`（被拦到即抛错，不会静默联网）"),
        "",
        "### 0.1 真实部分与替身部分的边界（逐项）",
        "",
        "| 产物 | 依据 | 是不是真实读数 |",
        "|---|---|---|",
        "| `table_proof_matrix.json` | 真实 PDF + 真实只读 evidence 库 → "
        "`build_live_table_source` → `release_graph_tables` | **是**（无替身） |",
        "| `cited_input_manifest.json` | 前述研究链产出的 Pack（**研究侧** LLM 是替身） | "
        "材料身份与原文真实；**检索过程**是替身 |",
        f"| `cited_prose.json` | {_prose_src} | "
        + ("**是**（给定输入与提示下模型写出的正文；不代表语义已被支持）" if _real
           else "**否**（不是模型正文）") + " |",
        f"| `sentence_checks.json` | `{SC.SENTENCE_CHECK_SCHEMA_VERSION}` 机械核对"
        f"（`{SC.SENTENCE_CHECK_POLICY_VERSION}`；确定性，含登记轴与呈现轴两条**并行**"
        "栏目归属；段级声明的栏目由段自己给出） | 是对**上文那份正文**的真实核对 |",
        f"| `review_issues.json` | {_review_src} | "
        + ("**是**（独立只读语义意见；仍不代替人工接受）" if _real
           else "**否**（不具备独立语义判断；预览里也据此标成「未进行真实独立语义审阅」）")
        + " |",
        f"| `cited_preview.md` / `cited_report_version.json` | "
        f"`{CRP.CITED_REPORT_VERSION_SCHEMA_VERSION}`／`{CRP.CITED_REPORT_POLICY_VERSION}` 装配 | "
        "装配真实，正文来源见上一行 |",
        f"| `cited_gap_bins.json` | `{CRP.CITED_REPORT_POLICY_VERSION}` 逐条分桶"
        "（**确定性查表**：冻结 Contract 的 `applicability_policy` / `display_tier`，"
        "不做语义判断、不读正文） | **是**（依据逐条带在产物里，可当场复核） |",
        "| `cited_metric_tables.json` | `cmt-4`/`cmtr-4` **确定性**构造（只用 Python/Decimal "
        "取权威字段，无模型参与） | **是**（数值与期间逐字取自权威事实的渲染，不经模型改写） |",
        "| `presentation_routing.json` | `fpr-2` 呈现层声明（确定性查表） | "
        "**是**（但它**不是**事实权威，只说明「哪个指标本来属于哪一栏」） |",
        f"| `cited_source_scope__*.json/.md` / `cited_balance_structure__*.json/.md` | "
        f"`{CSS.CITED_SOURCE_SCOPE_SCHEMA_VERSION}` / "
        f"`{CBS.CITED_BALANCE_STRUCTURE_SCHEMA_VERSION}` "
        "确定性呈现（逐条读权威字段或合格事实） | "
        "**是**（**零模型调用**；它不是正文，没有引用与审阅） |",
        f"| `cited_call_ledger.json` | 共享调用账本（`{CB.CITED_BUDGET_POLICY_VERSION}`） | "
        + ("**是**（每次尝试在发请求**之前**记账，失败也占额）" if _real
           else "**否**（本模式没有真实调用可记；文件里写明这一条）") + " |",
        "| `cited_call_journal.json` | `ccj-1` 失败前留存：调用**前**落输入清单身份与请求面"
        "原文，收到回复后、解析**前**落可见回复与解析状态 | 留存是真的（离线替身同样留）；"
        "但里面的正文**未经硬核对与独立审阅，`publishable` 恒为 false** |",
        "",
    ]

    # ---- 0.2 本次的调用预算（这一节自己那几条） ------------------------------
    if budget is None:
        L += ["### 0.2 本次的调用预算",
              "",
              f"- 离线模式：写作与审阅都由替身产出，**零真实调用**；"
              f"本条政策 `{CB.CITED_BUDGET_POLICY_VERSION}` 只在 `--mode real` 下生效。",
              ""]
    else:
        summary = budget.summary()
        L += ["### 0.2 本次的调用预算（共享账本，发请求**之前**记账）", "",
              f"- 政策：`{CB.CITED_BUDGET_POLICY_VERSION}`；获批模型：`{approved_model}`",
              f"- 上限：每节 ≤{CB.CITED_WRITE_MAX_PER_SECTION} 写 + "
              f"≤{CB.CITED_REVIEW_MAX_PER_SECTION} 审；整轮 ≤{CB.CITED_RUN_MAX_ATTEMPTS}；"
              f"自动重试 {CB.CITED_AUTOMATIC_RETRIES}",
              f"- 本**节**实际：写作轴 **{summary['attempts_by_section'].get(section_id, 0)}** 次，"
              f"按类别 **{summary['attempts_by_category'] or '{}'}**（账本是**整轮**的，"
              f"这里同一份计数在两节上都印出来，不按节切分它）",
              f"- 整轮尝试：**{summary['attempt_total']}**；拒绝：**{summary['refusal_total']}**",
              ""]
        for attempt in summary["attempts"]:
            L.append(f"  - `{attempt.get('category')}` · 节 `{attempt.get('section_id')}` · "
                     f"`{attempt.get('status')}` · 模型 `{attempt.get('model')}` · "
                     f"prompt `{attempt.get('prompt_version')}`")
        if summary["refusals"]:
            L += ["", "**被门拒绝的尝试（没有发出去）：**", ""]
            for refusal in summary["refusals"]:
                L.append(f"  - {_md_cell(json.dumps(refusal, ensure_ascii=False))}")
        L += [""]

    # ---- 0.3 本次的呈现层路由与确定性来源栏 ----------------------------------
    L += ["### 0.3 本次的栏目归属与确定性来源栏", ""]
    if routing is None:
        L += ["- 本节本次没有 `fin_solvency` 主题，因此没有呈现层路由声明。", ""]
    else:
        L += [f"**呈现层「指标 → 栏目」路由声明**（`{routing.routing_version}`；**不是**事实权威，"
              "`authority_facts[].aspect_ids` 全程为空）：", "",
              "| 指标 code | 路由到的栏 | 该栏在冻结 Contract 的展示层级 |", "|---|---|---|",
              *[f"| `{r.metric_code}` | `{r.presentation_column}` | "
                f"`{r.contract_display_tier}` |" for r in routing.routes],
              "",
              f"- 本节投递事实：路由 **{len(routing.fact_columns)}** 条，"
              f"未路由 **{len(routing.unrouted_facts)}** 条；"
              f"声明指纹 `{routing.fingerprint()}`", ""]
        if routing.column_gaps:
            L += ["**栏目缺口**（Contract 有这一栏，本次选中事实没有任何指标可证明它；"
                  "**不**拿别的栏顶）：", "",
                  "| 栏目 | Contract 原文要求 | 原因 |", "|---|---|---|",
                  *[f"| `{g.column}` | {_md_cell(g.contract_requirement_text)} | `{g.reason}` |"
                    for g in routing.column_gaps], ""]
        else:
            L += ["- 栏目缺口：**0**。", ""]
    if source_scopes:
        for scope in source_scopes:
            name = _deterministic_presenter_name(
                topic_id=scope.topic_id, section_id=section_id)
            L += [f"**确定性呈现 · topic `{scope.topic_id}`**"
                  f"（产出者 `{name}`，`{scope.schema_version}`／`{scope.producer_kind}`，"
                  f"模型调用 **{scope.model_calls_issued}** 次）：", "",
                  f"- 逐条读数见 `{name}__{scope.topic_id}.md`；"
                  f"契约栏目 **{len(scope.contract_topic_aspects)}** 条，"
                  f"本次给出 **{sum(1 for i in scope.items if i.state == 'determined')}** 条读数、"
                  f"**{sum(1 for i in scope.items if i.state == 'gap')}** 条 typed 缺口。", ""]
    else:
        L += ["- 本节本次没有确定性呈现主题。", ""]

    # ---- 1. Contract 栏目 ---------------------------------------------------
    L += [f"## 1. Contract 栏目（冻结投影，`requirement_text` 逐字）", ""]
    L += ["| # | `subsection_id` | Contract 逐字要求文本 |", "|---|---|---|"]
    for index, spec in enumerate(manifest.subsections, start=1):
        L.append(f"| {index} | `{spec.subsection_id}` | "
                 f"{_md_cell(spec.requirement_text)} |")
    #: `cfread-1` 定点纠正：这里的表**一行 = 一个写作小节**（列头就是 `subsection_id`），
    #: 原先却把 `len(manifest.subsections)` 印成「栏目数」——见
    #: :func:`_contract_columns_line` 的说明。
    L += ["", *_contract_columns_line(
        payload=payload, source_scopes=source_scopes,
        subsection_count=len(manifest.subsections)), ""]

    # ---- 2. 三份材料的实际读取 ---------------------------------------------
    L += ["## 2. 三份材料的实际读取（有序源集）", ""]
    L += ["| # | 文档 | 版本 | 证据集版本 | 磁盘实读 sha256（前 12） |",
          "|---|---|---|---|---|"]
    for index, entry in enumerate(inputs.source_manifest.source_set_entries(), start=1):
        L.append(f"| {index} | `{entry.document_id}` | `{entry.document_version}` | "
                 f"`{entry.evidence_set_version}` | `{str(entry.file_sha256)[:12]}` |")
    L += ["", "登记 sha256 与磁盘实读**逐份核对**：不一致即拒绝（见本脚本 "
          "`_member_lives`）。「登记了三份」不等于「三份都真读到了」。", ""]

    # ---- 3. Pack 原文与逐表状态 --------------------------------------------
    L += ["## 3. Pack 阅读材料（原文在本清单里逐条可读）", ""]
    L += ["| 键 | `member_ref` | topic | 类型 | 内容形态 | 来源角色 | 正文字符 | 是表 | "
          "续接 | `payload_hash`（前 12） |", "|---|---|---|---|---|---|---|---|---|---|"]
    for material in manifest.materials:
        L.append(f"| `{material.citation_key}` | `{material.member_ref}` | "
                 f"`{material.topic_id}` | `{material.material_type}` | "
                 f"`{material.content_kind}` | `{material.source_role}` | "
                 f"{len(material.reading_view)} | "
                 f"{'是' if material.structured_view is not None else '否'} | "
                 f"{_continuity_cell(material.span_continuity)} | "
                 f"`{str(material.payload_hash)[:12]}` |")
    L += ["", f"- 清单成员数：**{len(manifest.materials)}**；合格事实数："
          f"**{len(manifest.facts)}**", ""]
    L += _readings_separation_note(manifest=manifest, draft=draft, version=version)
    paired = [m for m in manifest.materials if m.span_continuity]
    if paired:
        L += [
            "**续接读法（`cwm-7`）**：上表「续接」列非 `—` 的行是**同一段原文**被来源块边界"
            "切开的片。列表顺序是身份序（哈希序），**不是**原文顺序——要按片序把同一 `span_id` "
            "的片连起来读。每片仍带**自己**的 `reading_view` 与 `locator`：续接只陈述「这两片"
            "同属一段原文」，**不**把两片合并成一个位置（合并即伪造 locator）。", "",
            "| `span_id` | 片序 | 本片字符区间 | 本片 `ref` | 前一片 | 后一片 |",
            "|---|---|---|---|---|---|",
        ]
        for material in sorted(paired,
                               key=lambda m: (str(m.span_continuity.get("span_id")),
                                              int(m.span_continuity.get("piece_index") or 0))):
            cont = material.span_continuity
            rng = cont.get("span_local_char_range") or [None, None]
            L.append(
                f"| `{_md_cell(str(cont.get('span_id'))[:12])}` | "
                f"{cont.get('piece_index')}/{cont.get('piece_count')} | "
                f"`{rng[0]}…{rng[1]}`（全文 {cont.get('span_char_length')}） | "
                f"`{material.citation_key}` | "
                f"`{_md_cell(cont.get('continued_from')) or '—'}` | "
                f"`{_md_cell(cont.get('continued_in')) or '—'}` |")
        L += [""]
    if not manifest.materials:
        L += [
            "**本节的清单材料行为空**：这一支权威（财务 artifact / 附注 / 外部快照）"
            "**没有** `ResearchMaterial` 材料边界，空集是它的**合法状态**，不是"
            "「漏解析了正文」——它的 `pack_set_fingerprint` 是空材料边界的领域分离指纹"
            f"（`{manifest.pack_set_fingerprint[:16]}…`），与任何 Pack 指纹不共用取值域。"
            "正文只能由本权威自己的**权威事实行**支撑（下面逐条列出）。",
            "",
        ]
    if manifest.facts:
        L += ["### 3.0 权威事实读视图（本清单的事实轴，`authority_facts`）", ""]
        L += ["| 键 | `authority_kind` | 容器 | `fact_id` | 期间 | 主体 | 事实字符 | "
              "引用锚点身份 |", "|---|---|---|---|---|---|---|---|"]
        for fact in manifest.facts:
            L.append(f"| `{fact.citation_key}` | `{fact.authority_kind}` | "
                     f"`{_md_cell(fact.container_identity)}` | `{_md_cell(fact.fact_id)}` | "
                     f"{_md_cell(fact.period) or '（无期间轴）'} | "
                     f"{_md_cell(fact.scope) or '（无主体轴）'} | {len(fact.text)} | "
                     f"`{_md_cell(fact.source_identity) or '（无）'}` |")
        L += ["",
              "「（无期间轴）／（无主体轴）」是**本权威没有这一轴**（写入侧按「宁可留空、不得"
              "编造」如实留空），不是缺字段：期间与主体只在该权威自己的字段里，读不到就不写。",
              ""]

    L += ["### 3.1 图侧逐表完整证明（`" + str(TLP.TABLE_LOCAL_PROOF_SCHEMA_VERSION)
          + "`，**真实文档**）", ""]
    L += ["| 文档 | 批次状态 | 声明表数 | 放行 | 拒发 | 记账平 |", "|---|---|---|---|---|---|"]
    for doc in table_matrix["documents"]:
        if "unavailable_reason" in doc:
            L.append(f"| `{doc['document_id']}` | — | — | — | — | 读回缺席"
                     f"（{doc['unavailable_reason']}） |")
            continue
        if doc.get("batch_state") == "release_raised":
            L.append(f"| `{doc['document_id']}` | `release_raised` | — | — | — | "
                     f"{doc['release_error_type']}：{doc['release_error']} |")
            continue
        L.append(f"| `{doc['document_id']}` | `{doc['batch_state']}` | "
                 f"{doc['declared_table_count']} | {doc['released_table_count']} | "
                 f"{doc['refused_table_count']} | "
                 f"{'是' if doc['accounting_balanced'] else '否'} |")
    L.append("")
    accounted = [d for d in table_matrix["documents"]
                 if isinstance(d.get("declared_table_count"), int)]
    total_declared = sum(d["declared_table_count"] for d in accounted)
    total_released = sum((d.get("released_table_count") or 0) for d in accounted)
    L += [f"- 全部演示文档合计：声明 **{total_declared}** 张，"
          f"放行 **{total_released}** 张，"
          f"拒发 **{total_declared - total_released}** 张。", ""]
    if total_released == 0 and total_declared:
        L += [
            "#### **系统能力缺陷（不是来源缺口）**",
            "",
            f"本机真实文档上，**{total_declared}** 张已声明的表**一张都没有**通过 "
            f"`tlp-1` 的逐表完整证明。逐张的 typed 缺陷码与七步读数在 "
            "`table_proof_matrix.json` 的 `documents[].tables[].local_proof` 里。",
            "",
            "**为什么这是系统能力缺陷而不是来源缺口**：这些表**在被上传的材料里确实存在**"
            "（快照声明了它们、逐格有原文），却被拒发在**构造侧**——不是「来源里没有这张表」。"
            "按 `DESIGN_V2.md` §0.17 的退出条件，材料里确有表格却因识别、续表、资格或工具"
            "接线失败而未呈现 ⇒ 系统能力缺陷 ⇒ **演示主营业务内容门不得通过**。",
            "",
            "**由此推出的内容结论（只讲这一条轴，不与路径 b 互相顶替）**："
            "图侧**结构化**合格原表为 **0** 张 ⇒ 「营收构成、成本与毛利率的**合格 "
            "`TableObject`**」本次**没有签发**；它们因此不进展示表材料、不构成格值权威，"
            "业务表数字**不得**据此写进正文。正文里不出现这些数字是**正确的行为**，不是遗漏。",
            "",
            "**同一件事的另一条轴（§0.21 路径 b，另行读数）**：这些表的**原件**在"
            "「原 PDF 表区只读展示」里**已经展示**——区域数、逐区域坐标与渲染哈希见 §6c。"
            "两句话**同时成立、不得互相顶替**：`结构化表未签发` ≠ `原件未呈现`，"
            "`原件已展示` ≠ `格值已获资格`。把前者写成后者，读的人会以为材料里没有这张表；"
            "把后者写成前者，读的人会以为数字已经可以写。两条都不许。",
            "",
        ]
        reason_counts: dict[str, int] = {}
        for doc in table_matrix["documents"]:
            for table in doc.get("tables") or ():
                key = table["structure_state_reason"] or "（无）"
                reason_counts[key] = reason_counts.get(key, 0) + 1
        L += [
            f"**这批拒发不是「{total_declared} 个各自独立的问题」，而是 "
            f"{len(reason_counts)} 条同源原因**（按 `structure_state_reason` 归并，"
            "逐表读数见下表与 JSON）：",
            "",
        ]
        L += ["| `structure_state_reason` | 张数 | 占全部声明表 |", "|---|---|---|"]
        for key, count in sorted(reason_counts.items(), key=lambda kv: -kv[1]):
            L.append(f"| `{key}` | {count} | {_pct(count, total_declared)} |")
        L.append("")
        # 逐表七步里唯一在 100% 的表上取同一值的读数，就是这套缺陷的**共同根**。
        step_counts: dict[tuple[str, str], int] = {}
        header_absent = 0
        table_total = 0
        for doc in table_matrix["documents"]:
            for table in doc.get("tables") or ():
                table_total += 1
                if TLP.DEFECT_HEADER_ROW_ABSENT in (table.get("defect_codes") or ()):
                    header_absent += 1
                for name, value in (table["local_proof"]["steps"] or {}).items():
                    step_counts[(name, f"{value}")] = \
                        step_counts.get((name, f"{value}"), 0) + 1
        for name in ("constructed_complete", "self_proven"):
            for (step, value), count in step_counts.items():
                if step == name and count:
                    L.append(f"- 七步读数 `{step}` 取 `{value}` 的表：**{count}** 张"
                             f"（{_pct(count, total_declared)}）")
        L.append("")
        L += [
            f"`constructed_complete=False` 出现在**每一张**已构造表上；"
            f"`{TLP.DEFECT_HEADER_ROW_ABSENT}` 出现在 **{header_absent} / {table_total}** "
            "张上——因此这不是六张目标表各自「识别失败」，而是"
            "**构造完成性这一道判据在这三份冻结文档上几乎从不成立**。"
            "修它要动的是那条判据本身（属于结构构造层），不是逐表打补丁。",
            "",
        ]

    L += ["#### 逐表读数（每张一张一行，含 typed 缺陷码）", ""]
    for doc in table_matrix["documents"]:
        L.append(f"**`{doc['document_id']}`**（{len(doc.get('tables') or ())} 张）")
        L.append("")
        if not doc.get("tables"):
            L.append("（本份文档没有已构造的表对象）")
            L.append("")
            continue
        L += ["| 表对象 | 页 | 形态 / 状态 | 逐表证明 | 主理由 | 缺陷码 | 可引格 / 总格 | "
              "无来源格 |", "|---|---|---|---|---|---|---|---|"]
        for table in doc["tables"]:
            L.append(f"| `{table['table_id']}` | {table['page_number']} | "
                     f"`{table['structure_kind']}`/`{table['structure_state']}` | "
                     f"{'通过' if table['local_proof']['locally_proven'] else '未通过'} | "
                     f"`{table['primary_reason']}` | "
                     f"{'、'.join('`' + c + '`' for c in table['defect_codes']) or '—'} | "
                     f"{table['citable_cell_count']} / {table['cell_count']} | "
                     f"{table['not_citable_cell_count']} |")
        L.append("")
        L += ["逐表状态原因（`structure_state_reason`；只在与前面重复时省略）：", ""]
        for table in doc["tables"]:
            if table["structure_state_reason"]:
                L.append(f"- `{table['table_id']}`：{table['structure_state_reason']}")
        L.append("")

    # ---- 3.2 数字逐值五态台账 -----------------------------------------------
    L += [f"### 3.2 数字逐值五态台账（`{CVT.VALUE_TRACE_POLICY_VERSION}`，只读）", ""]
    L += [
        "§6 的逐句核对说得清「这一句里有没有未授权的数字」，说不出「**同一个数字**卡在链条的"
        "哪一步」。本节把链条摊到**值**上：正文里写了的数字 ∪ 材料里的金额 / 比率表面，逐值给一个"
        "**阶段**（七档互斥）与一个**授权**（四档互斥）。",
        "",
        "- **「写了」不等于「授权到了」**：被引普通材料原文里逐字出现过 ⇒ "
        "`material_surface_only`——它能让这一句过 `unsourced_number_surface`，**不能**授权"
        "金额或比率；只有 `qualified_fact` / `table_cell_source` 才是数字权威。",
        "- **「材料里有这个数字」不等于「有一条被拒的候选」**：`no_candidate`（上游交进来了、"
        "没有候选）、`candidate_rejected`（有候选、被拒并带 typed 原因）、"
        "`absent_from_material_spans`（材料 span 里也没有）三档两两不同。",
        "- 上游对象本次没被交进来时整份标 `not_observable`——**读不到不写成没有**；"
        "`qualified_not_delivered` 若确实没有 typed 排除记录，台账写「**无 typed 排除记录**」"
        "而不是「没被排除」。",
        "",
    ]
    trace_research_facts = _pack_set_facts(inputs, section_id)
    trace_topic_results = (getattr(inputs, "topic_results", None) or {}).get(section_id)
    trace_report = CVT.trace_cited_values(
        draft=draft, materials=manifest.materials, facts=manifest.facts,
        scan=scan, topic_results=trace_topic_results, research_facts=trace_research_facts)
    L += CVT.render_value_trace_lines(trace_report)
    L += [""]
    if not trace_report.upstream_observed:
        L += [
            "> 本节**没有**可观察的研究侧候选 / 资格决定对象（本节权威不是 Pack 集，或本链"
            "这一支没把它交进来）。因此除「已在正文里」与「材料表面里有」之外的值，一律落"
            "`not_observable`（**本链观察不到**），**不得**读成「上游没有产生候选」。",
            "",
        ]

    # ---- 4. Writer 的精确输入 ----------------------------------------------
    L += [f"## 4. Writer 的精确输入（`{CW.CITED_WRITER_MANIFEST_SCHEMA_VERSION}`）", ""]
    L += [
        f"- 输入清单 id：`{manifest.manifest_id}`",
        f"- 清单指纹：`{manifest.fingerprint()}`",
        f"- 材料上下文 id：`{manifest.material_context_id}`"
        f"（指纹 `{manifest.material_context_fingerprint}`）",
        f"- Pack 集指纹：`{manifest.pack_set_fingerprint}`",
        f"- 材料清单身份：`{manifest.material_manifest_id}`"
        f"（指纹 `{manifest.material_manifest_fingerprint}`）",
        f"- 小节数：{len(manifest.subsections)}；材料行：{len(manifest.materials)}；"
        f"事实行：{len(manifest.facts)}",
        "",
        "投给写作者的请求面**逐字**包含每一条材料的原文（`materials[].text`）与每一条事实的"
        "命题文本（`authority_facts[].text`）——模型拿到的与复核者拿到的是同一串字节。",
        "",
    ]

    # ---- 4.1 财务节的确定性指标表 ------------------------------------------
    # 这张表是**读者面产物**，不是本链的通过条件；它由一个确定性构造器给出（只用 Python/
    # Decimal 取权威字段，不含任何模型排布或计算）。三态必须分开显示：
    #   ① 硬失败（构造期撞上数据伤，模块 fail-closed 抛错）——「表没建出来」；
    #   ② 合法缺席（typed 拒绝）——「本节本来就没有可成表的指标」；
    #   ③ 成表。
    L += ["## 4.1 财务节的确定性「指标 × 期间」表"
          f"（`{CFT.CITED_METRIC_TABLE_SCHEMA_VERSION}` / "
          f"`{CFT.CITED_METRIC_TABLE_RULE_VERSION}`）", ""]
    metric_tables = payload["metric_tables"]
    metric_error = payload["metric_table_error"]
    if metric_tables is not None and metric_tables.authority_kind != "financial_workflow":
        L += [
            f"（**本节不是财务节**：本节的权威 `producer_kind="
            f"{metric_tables.authority_kind}`，财务指标表这一支不适用。构造器照常给出一条"
            "`authority_not_financial` 的 typed 拒绝作为**诊断**记录，但它不是「本节没有表」——"
            "读者预览里因此不出现「财务指标表」这一节。）",
            "",
        ]
    elif metric_error is not None:
        L += [
            f"**硬失败**：本节的指标表**没有建出来** —— 构造期撞上数据伤，模块 fail-closed 抛错。",
            "",
            f"- 原因码：`{metric_error['reason']}`",
            f"- 消息：{_md_cell(metric_error['detail'])}",
            "",
            "> 这一态**不是**「本节没有表」。它表示权威事实之间有一处不一致"
            "（例如同一格归属两条事实、某条事实的期间口径未声明或不在封闭取值内）。"
            "这种不一致不得被排版抹平，也不得降级成一张空表。",
            "",
        ]
    elif metric_tables is not None and metric_tables.refusals:
        L += ["**合法缺席**（typed，不是失败）：本节没有可成表的指标。", ""]
        L += ["| 原因码 | 说明 |", "|---|---|"]
        for refusal in metric_tables.refusals:
            L.append(f"| `{refusal.reason}` | {_md_cell(refusal.detail)} |")
        L += ["",
              "> 表格的候选集是**进入本节事实清单的合格财务事实**，而清单来自权威扫描——"
              f"**写作结果不是候选集的判据**（`{CFT.CITED_METRIC_TABLE_RULE_VERSION}`）。"
              "某条事实这一行有没有出现在表里，"
              "与 Writer 有没有在某句话里引用它无关。",
              ""]
    elif metric_tables is not None and metric_tables.tables:
        body_tables = tuple(t for t in metric_tables.tables
                            if t.display_tier != "diagnostic_only")
        diagnostic_tables = tuple(t for t in metric_tables.tables
                                 if t.display_tier == "diagnostic_only")
        L += [
            f"- 表数：{len(metric_tables.tables)}（正文指标表 {len(body_tables)}、"
            f"诊断槽位 {len(diagnostic_tables)}）；"
            f"权威种类：`{metric_tables.authority_kind}`；"
            f"规则版本：`{metric_tables.rule_version}`",
            "",
        ]
        if diagnostic_tables:
            L += [
                "### 4.1.0 展示层级核对（`display_tier`）",
                "",
                "> 冻结 Contract 把 **代理口径** 的财务 aspect 写成 `display_tier: "
                "diagnostic_only` / `content_role: audit_only`（本节投影里就是 "
                "`fin_solvency.interest_expense_proxy`，Contract 逐字：「利息费用代理（不可靠，"
                "仅进入诊断槽位）」），`FORMULA_REVIEW.md` §1 也写明代理结果「不参与『精确口径』"
                "比较」。**「格子上带了限定语」不是入场券**：只由代理口径输入算出来的指标，"
                "不得因为自己带了 `PROXY_FINANCE_EXPENSES` 标记就进正文指标表。",
                "",
                "> 因此分层判据有**两条**，两条都要在（`cmtr-4`；`cmtr-3` 曾只按前一条）："
                "契约把这一栏写成 `diagnostic_only`（`contract_display_tier`）**且**权威事实"
                "自己带代理标记（`authority_proxy_fact_marker`）。只凭标记、或只凭契约单方面"
                "说了算，都答不出「这条路由指向的那一栏的档位与事实自己的口径是不是同一件事」。"
                "下面正文指标表里只有精确口径的行；代理口径的行整行移入「诊断槽位」块，"
                "**代理限定语照旧逐格呈现**（没有抹平、没有删格、没有改值）。分层只换读者面的块，"
                "不改任何一格的值 / 期间 / 单位 / 引用。",
                "",
            ]
        _render_metric_tables(L, body_tables, heading="正文指标表")
        if diagnostic_tables:
            L += ["#### 诊断槽位（`display_tier=diagnostic_only`，**不并入正文指标表**）", ""]
            _render_metric_tables(L, diagnostic_tables, heading="诊断槽位")
        L += [
            "> **每一格都逐字等于该权威事实自己的渲染**（`financial_table_cell`），且是它"
            "权威表面的连续子串：表里不可能出现权威没说过的数值，本行也不做任何求和、换算或"
            "推算。缺值的格是**留空**，不是补齐。",
            "> 逐格核对的对象是**权威事实自己的字段**（数值、期间表达对列、量纲对行、代理限定语"
            "确实渲染进格、引用键非空）——这与「逐句核对」是两根轴：正文句归机械核对，表格格归"
            "这里。",
            "> 这张表**已并入** `CitedReportVersion` 的身份体（`crpv-2` 的 `metric_table_ids` /"
            "`metric_tables_fingerprint`）：表格内容一变，记录身份就变，预览页里印出的指纹"
            "就是读者核对「我读的这一版」的凭据。",
            "",
        ]

    else:
        L += ["（本节未产生指标表，也没有记下原因：这是本脚本自身的状态缺失，应按缺陷处理。）",
              ""]

    # Contract 必需指标 × 期间：逐条去哪了。三种「没有表」都要带上它，否则读回只有一句
    # 「没有表」，读的人无从知道**哪几个**必需指标缺了、缺在哪一档。非财务节没有这条轴。
    if metric_tables is not None and metric_tables.authority_kind == "financial_workflow":
        L += ["### 4.1.1 必需财务指标 × 期间覆盖对账"
              f"（`{CFT.CITED_METRIC_TABLE_SCHEMA_VERSION}`）", ""]
        L += [CFT.render_cited_metric_coverage_markdown(metric_tables), ""]

    # ---- 4.2 业务 aspect × 上传材料：逐项对账 --------------------------------
    L += ["## 4.2 业务 aspect × 上传材料：找到 / 进入 Pack / 送达 Writer / 写出 / "
          "**算不算本栏目正文** / **这一栏写到什么程度（下界）** / 缺口", ""]
    matrix = _aspect_material_matrix(inputs=inputs, section_id=section_id,
                                     manifest=manifest, draft=draft,
                                     check_report=check_report)
    if matrix is None:
        axis_present = bool(SC.manifest_has_aspect_axis(manifest))
        # 两条栏目归属轴**并行**：登记轴读材料/事实自己声明的 `aspect_ids`（财务那一支恒为空），
        # 呈现轴读呈现层声明（`fpr-2` 的 `fact_columns`）。只读前一条会把「财务正文没被核对过」
        # 写成结论——而本次真实读数恰恰相反：呈现轴在财务节逐句判过，并报出错栏。两轴分别读出。
        presentation_present = bool(
            SC.manifest_has_presentation_column_axis(manifest))
        L += ["（本节**没有** Pack 材料边界：这一支权威不消费 `ResearchMaterial`，"
              "「材料 × aspect」这张表因此**不适用**——不是全零，是不适用。"
              "它的正文只能由本权威自己的权威事实行支撑，见 §3.0 与 §4。）",
              f"- 本次清单里**有没有**栏目登记轴（至少一条来源带非空 `aspect_ids`）："
              f"**{'有' if axis_present else '没有'}**。",
              f"- 本次清单里**有没有**栏目呈现轴（呈现层声明给出了「事实 → 栏目」）："
              f"**{'有' if presentation_present else '没有'}**。这两条轴**并行**，"
              "一条缺席不代表另一条也缺席。"]
        if not axis_present:
            L += [
                "- **这不是「已核对通过」**：财务 / 附注 / 外部快照那一支本就不带 aspect 级状态"
                "（`pack_writer.scan_financial` 明确**不**编造 aspect 状态，覆盖由「选中事实 + "
                "artifact 缺口 + 附注缺口」表达），因此本节的正文**没有被**"
                f"`{SC.SENTENCE_CHECK_SCHEMA_VERSION}` 的"
                "**登记轴**（`aspect_attribution`，读材料/事实的 `aspect_ids`）核对过——"
                "把「没有这条轴」读成「栏目都对上了」是同一枚假门的另一面，本读回不那样写。",
            ]
        if presentation_present:
            L += [
                "- **但这不等于「财务正文没有栏目核对」**：本节清单带了**呈现轴**"
                "（`presentation_column_attribution`，读呈现层声明的 `fact_columns`），"
                "它逐句判「这句引的事实，按声明属于哪一栏，与本小节是不是同一栏」，"
                "判不上的落 `sentence_presentation_column_mismatch` ——"
                "「流动比率写进净资产水平」这一类错栏由**这条**轴拦住（逐句结论见 §6）。",
            ]
        else:
            L += [
                "- **且本次没有呈现轴**：呈现层声明缺席时，本节的错栏既没有登记轴也没有呈现轴"
                "可判——这一条是如实读数，不是「栏目都对上了」。",
            ]
        L += [""]
    else:
        docs = matrix["documents"]
        declared = [d for d, _v in docs]
        #: 除已声明文档之外的**任何**落点（含两个显式桶名）另开列：宁可可读性差，
        #: 也不让一格材料藏在表外看不见。
        extras = sorted({key for row in matrix["rows"] for key in row["in_pack"]}
                        - set(declared))
        columns = declared + extras
        L += [f"- Pack 材料总数：**{matrix['pack_material_count']}**"
              f"（本节 `pack_set` 内 **{len(matrix['topic_ids'])}** 个 topic："
              + "、".join(f"`{_md_cell(t)}`" for t in matrix["topic_ids"]) + "）"
              f"　送达本节 Writer 清单：**{matrix['writer_material_count']}**"
              f"　写作小节：**{matrix['subsection_count']}**"
              f"　Contract 栏目：**{len(matrix['rows'])}**", ""]
        L += ["**一行 = 一个 Contract 栏目。** 小节只有 "
              f"{matrix['subsection_count']} 个（冻结 WritingSpec 的小节轴），栏目有 "
              f"{len(matrix['rows'])} 个——`cwm-6` 起一个小节覆盖多个栏目，所以"
              "「本小节写了几段」**不**等于「本栏目写了几段」，这一节里每一行都带自己的"
              "段级声明账。", ""]
        L += ["| # | Contract 栏目 | 所属小节 | "
              + " | ".join(f"`{_md_cell(d)}` 进 Pack" for d in declared)
              + (" | " + " | ".join(f"{_md_cell(d)} 进 Pack" for d in extras) if extras else "")
              + " | 送达 Writer | **声明本栏的段数** | **这些段里的句数** | "
              "**服务本栏的句数**（现算） | **服务本栏的材料数**（现算） | "
              "**写到什么程度（下界，不是「实质已答」）** | "
              "本小节写出句数 | **算出数**（**本小节**，非本栏） | "
              "**不算数**（**本小节**，非本栏） | **本轴没判**（**本小节**） | "
              "Contract `required_fields`（逐字） | 本栏合格事实（条） | "
              "**字段缺口** | 缺口（**本小节**，非本栏） |",
              "|" + "---|" * (len(columns) + 16)]
        for index, row in enumerate(matrix["rows"], start=1):
            cells = " | ".join(str(row["in_pack"].get(d, 0)) for d in columns)
            gaps = "、".join(f"`{g}`" for g in row["gaps"]) or "—"
            fields = "；".join(_md_cell(f) for f in row["required_fields"]) or "—"
            field_gaps = "、".join(f"`{g}`" for g in row["field_gaps"]) or "—"
            L.append(f"| {index} | `{row['aspect_id']}` | `{row['subsection_id']}` | "
                     f"{cells} | "
                     f"{row['delivered']} | {row['paragraphs_declaring']} | "
                     f"{row['sentences_declared']} | "
                     f"{row['sentences_for_aspect']} | "
                     f"{row['cited_materials_for_aspect']} | "
                     f"`{row['substantive_state']}` | "
                     f"{row['sentences']} | "
                     f"{row['sentences_in_column']} | {row['sentences_out_of_column']} | "
                     f"{row['sentences_axis_not_applicable']} | "
                     f"{fields} | {row['qualified_facts']} | "
                     f"{field_gaps} | "
                     f"{gaps} |")
        never_declared = [r["aspect_id"] for r in matrix["rows"]
                          if not r["paragraphs_declaring"]]
        no_material = [r["aspect_id"] for r in matrix["rows"]
                       if not sum(r["in_pack"].values())]
        #: 字段缺口那一列逐栏清点：Contract 要了字段、而本栏名下零条合格事实。这不是
        #: 「材料里没有」的结论，也不是「用户缺件」，是**本轮的读取 / 资格 / 交付缺口**。
        field_gap_rows = [r for r in matrix["rows"] if r["field_gaps"]]
        L += [
            "",
            f"**字段缺口（`{ASPECT_FIELD_GAP_REQUIRED_FIELDS_WITHOUT_FACT}`）"
            f"**：**{len(field_gap_rows)}** / {len(matrix['rows'])} 个栏目"
            + ("" if not field_gap_rows else
               "——" + "、".join(
                   f"`{r['aspect_id']}`（要 "
                   + " / ".join(f"「{_md_cell(f)}」" for f in r["required_fields"])
                   + "，本栏合格事实 **0** 条）" for r in field_gap_rows))
            + "。这一列说的是「**本栏**在冻结 Contract 里点名要的那几件，本轮一条都没有取得"
            "为**合格事实**」——它是**读取 / 资格 / 交付**这个环节的缺口，"
            "**不是**「来源称不适用」，也**不是**「用户缺件」：材料可能在、原表可能就夹在上传件里，"
            "先把这三句话**分开说**，才谈得上分别去修。原表在原 PDF 表区的只读展示"
            "（§0.21 路径 b）**不**改变本读数，它也**不**构成这一栏已被写出。",
            "**「本栏合格事实（条）」是栏目级的条数，不是「那几件事各有几条」**：事实行只声明"
            "它属于哪一栏（`aspect_ids`），**不**声明它对应 `required_fields` 里的哪一件——"
            "字段级的对勾本表**判不出来**，因此不给。0 就是 0：它说的是「本栏名下一件都没有」，"
            "不是「三件里缺两件」。",
            "**最后一列「缺口」的主语是**小节**，不是本栏**（表头已写明）：缺口存在草稿的"
            "**小节**上（本小节那个 `source_present_but_not_admissible` 挂在小节上），同一小节"
            "下的十几行因此印的是**同一条**缺口。它不是「这一栏各自缺了一条」——把同一个小节的"
            "一条缺口读成十八栏各自的证明，正是本批要堵掉的那句话。**栏目级**的缺口只有前一列"
            "（**字段缺口**，判据是「本栏 Contract 要了字段 ∩ 本栏零条合格事实」）。",
            "**「缺口」与「字段缺口」不是同一条轴**：前者说**这一小节**为什么没写出正文"
            "（此处是材料未获准入，去向是「本轮未取得 / 未获支持」，**不是**「上传语料里不存在"
            "该内容」），后者说**本栏**在 Contract 里点名要的那几件没取得为合格事实。"
            "两列都非空，才是「既有写作侧阻断、又有字段级未取得」。",
            f"**覆盖账（这一节里「全节内容计划」的机器对账）**："
            f"没有任何段落声明服务它的栏目 **{len(never_declared)}** / "
            f"{len(matrix['rows'])}"
            + ("：" + "、".join(f"`{a}`" for a in never_declared) if never_declared else "")
            + f"；Pack 侧一条材料都没有被认领到它名下的栏目 **{len(no_material)}** / "
            f"{len(matrix['rows'])}"
            + ("：" + "、".join(f"`{a}`" for a in no_material) if no_material else "")
            + "。左列是**写作侧**的缺口，右列是**取材侧**的缺口——两列都为空的栏目才是"
            "「材料到位且已被某一段认领」，两列同时非空才是「材料也没有、也没写」。",
            "**这张表把六处**各自独立**的读数并排**，不互相推算：「进 Pack」逐条取 "
            "`TopicResearchPack.material_dispositions` 里材料**自报**的 `aspect_ids`（材料侧"
            "的归属轴）；「送达 Writer」取本节写作清单里材料的 `material_id` 与 Pack 侧 id 的"
            "交集（Pack 有而清单没有的就不算送达）；「声明本栏的段数 / 这些段里的句数」取草稿里"
            "**段自己声明**的 `aspect_ids`（`cw-4` 段级声明轴）；「本小节写出句数」取该小节实际的"
            "句数；「算出数 / 不算数 / 本轴没判」取 `scp-5` 栏目归属轴（`aspect_attribution`）"
            "逐句的结果，**三桶并列、互不推算**；「Contract `required_fields`」逐字取自冻结 "
            "Contract 那一栏自己的声明，「本栏合格事实」取本节写作清单的事实行按它们自己的 "
            "`aspect_ids` 归属清点——**前者是要求，后者是取得**，两者并排才看得出字段缺口。",
            "**这三列的**主语是本小节，不是本栏**，表头已写明：`aspect_attribution` 的判据是"
            "「本句所引来源登记在本小节声明的**某一栏**里」（`sections/sentence_check.py` 轴 7："
            "`covered = registered & set(declared_set)`），它**不**逐栏判、也判不出「这一句服务"
            "的是哪一栏」。因此同属一个小节的十几行里这三列是**同一个数**。把一个 3 读成"
            "「本栏被 3 句覆盖」，就是把小节级的读数冒充成栏目级的结论——本表不那样用。"
            "栏目级的读数只有左边那两列（**声明本栏的段数 / 这些段里的句数**，取自 `cw-4` 段级"
            "声明轴）；它是 0 就是 0，不许由右边这三列补上。",
            "**「声明本栏的段数」是 `cwm-6` 新增的那一列，它补的正是「小节太宽」这个洞**："
            "18 个栏目同属一个小节时，「本小节有 6 段」对每一栏都成立、因而对哪一栏都没有信息量；"
            "「有 3 段声明服务本栏」才是可判的。段声明与引用**同时**对不上时，"
            "`scp-5` 在 `aspect_attribution` 轴上直接判硬错（不是只记一笔覆盖账）。",
            "**「写出句数」与「算出数」是两件事，不得混用**：占位、切题与否、栏目归属是三根"
            "不同的轴。一句写着别的栏目内容的话照样能写出来（计入「写出句数」），但从 `sc-3` "
            "起它**不**计入本小节覆盖（计入「不算数」）。把「有句子」读成「本栏已覆盖」，正是"
            "本批要堵掉的那条捷径。",
            "**「本轴没判」不是零头，也不是「都算对」**：它在 `sc-4` 起单独成列。这一支说的是"
            "「本节这次输入里**没有**栏目登记轴」（财务 / 附注 / 外部快照那一支本就没有 aspect "
            "级归属；本句没有引用时也不判），因此「算出数」与「不算数」**同时为 0**。两个 0 "
            "在这一支上与「每一句都算本栏目」在产物上长得一模一样——差别全在这一列。拿两个 0 "
            "当栏目全对上的证据，就是把 §0.17 那道假门装回去。",
            "**两侧范围对齐到节**：写作清单是节级的，Pack 侧因此取本节 `pack_set` **全体 "
            "topic 的并集**，不是只拿本节缺省那一个 topic 的 Pack 去对——后者会把别的 topic "
            "的材料记成「Pack 里没有」。",
            "**0 不等于「没有这张表」**：Pack 侧 0 说明该 aspect 本次没有材料被认领（缺口列若"
            "写着 `no_source_in_manifest`，其去向说明写的是「本轮未取得 / 未获支持」，"
            "**不是**「上传语料里不存在该内容」）；传递到 Writer 的句数为 0 时才是真正的"
            "写作缺口，两件事在这一行里相邻但不同列。",
            "**「声明本栏的段数」为 0 的主语是**这一份草稿**，不是「这一栏写不出来」**：它随"
            "写作者而变（抽取式替身只写少数几段、每段只声明它引的那条材料所登记的栏目；真实"
            "写作侧按全节内容计划分段）。读到一片 0 时该问的是「这份草稿的分段账长什么样」，"
            "而不是「本栏目没有材料可写」——后者要看左边「进 Pack／送达 Writer」那几列。",
            "**新增的三列（服务本栏的句数 / 服务本栏的材料数 / 写到什么程度）是**栏目级**的"
            "下界读数，现算、不进 wire、不改任何判据**：前面那三列（本小节写出句数 / 算出数 / "
            "不算数）的主语是**小节**，同小节十几行同一个数；这三列才把「这一栏到底有几句话"
            "在说它、引了几条材料」单独算出来。判据与小节级那条**同源、只下到栏目级**："
            "一句算作服务本栏 ⇔ （它所在段声明了本栏）**且**（它所引材料在 Pack 侧登记的栏目里"
            "含本栏）——两个条件都要，是因为只要一边就能造出假覆盖：只取段声明，一段挂七栏就"
            "凭空得七栏句数；只取材料登记，同一份材料被几段复用就重复计数。",
            "**「写到什么程度」这一列**是**下界分档，不是「实质已答」的判定**，七档互斥："
            + "、".join(f"`{s}`" for s in ASPECT_SUBSTANTIVE_STATES)
            + "。它只回答「机器从已落盘的轴上看到哪一步」：`no_pack_material`（本栏名下一条 "
            "Pack 材料都没有）→ `material_not_delivered`（有材料但没送达本节写作清单）→ "
            "`not_written`（送达了但没有任何段落声明服务本栏）→ `label_only`（有段声明、却"
            "没有一句的引用材料登记在本栏——**标签不是回答**）→ `single_sentence`（只一句）→ "
            "`single_source`（多句但只落在一条材料上）→ `multi_sentence_multi_source`"
            "（多句多材料）。**最后一档是下界，机器不据此宣布「本栏已实质答上」**——"
            "「一个段落标签」「一句泛泛的话」「只引一条材料的句子」都**不**构成可读的回答，"
            "要判「实质已答」得有人对着原件读。本列的作用是把「哪几栏需要人去看原件」"
            "圈出来，不是替人签字。",
            "**这一列与「本栏合格事实（条）」是两条轴，不得互相顶替**：事实条数是**数字权威**"
            "的读数，`label_only`／`single_sentence` 是**内容充分性**的下界。描述性栏目"
            "（`display_tier=optional_body` 等）本来就可以由已定位的 Pack 原件支撑、"
            "不需要合格事实行，因此「本栏合格事实 0 条」**不**等于「本栏没有正文」；"
            "反过来 `multi_sentence_multi_source` 也**不**等于「本栏有合格数字」。"
            "把这两个读数混成一个「完整度」，就是本批要分开的那两件事。",
            "",
        ]

    # ---- 4.3 逐份材料的去向账（**材料粒度**） --------------------------------
    L += ["## 4.3 逐份材料的去向账：从上传文档到正文里的引用（`wmdl-1`）", ""]
    ledger = _material_destination_ledger(inputs=inputs, section_id=section_id,
                                          manifest=manifest, draft=draft,
                                          check_report=check_report)
    if ledger is None:
        L += ["（本节**没有** Pack 材料边界：这张账因此**不适用**——不是全零，是不适用。"
              "这一支的正文由它自己的权威事实行支撑，见 §3.0 与 §4。）", ""]
    else:
        counts = ledger["counts"]
        L += [f"- 本节 Pack 材料 **{ledger['pack_material_count']}** 份"
              f"（`pack_set` 内 {len(ledger['topic_ids'])} 个 topic："
              + "、".join(f"`{_md_cell(t)}`" for t in ledger["topic_ids"]) + "）"
              f"　送达本节写作清单 **{ledger['writer_material_count']}** 份"
              f"　本表逐份一行：**{len(ledger['rows'])}** 行", ""]
        L += ["**逐份去向（材料粒度）**："
              + "、".join(f"`{s}` {counts.get(s, 0)}" for s in MATERIAL_DESTINATION_STATES)
              + "。", ""]
        if ledger["writer_axis_present"]:
            L += ["- 本节的写作草稿**带** Writer 侧材料处理去向这一轴：右边的「Writer 去向／"
                  "理由码」两列是本节逐份材料的**作者侧自报**。", ""]
        else:
            L += ["- **本节写作草稿不带 Writer 侧材料处理去向这一轴**（本节草稿类型上就没有"
                  "这个字段）：右边「Writer 去向／理由码」两列**本链取不到**，一律印 `—`。"
                  "这不是「29 份材料各自缺了一条去向」，是**这一条写作链上没有这一轴**——"
                  "本表因此**不**逐份报「缺去向」缺陷（那会是读回自己造出来的假缺陷），"
                  "只在这里说一次。", ""]
        L += ["| # | 材料 | 来源文档 | 类型 | 研究侧准入 | 研究侧理由码 | 登记栏目数 | "
              "进清单 | Writer 去向 | Writer 理由码 | 引用它的句数 | 其中带硬错 | **去向** |",
              "|" + "---|" * 13]
        for index, row in enumerate(ledger["rows"], start=1):
            state = row["state"]
            mark = f"**`{state}`**" if row["defect"] else f"`{state}`"
            L.append(
                f"| {index} | `{_md_cell(row['material_id'])}` | "
                f"`{_md_cell(row['document_id']) or '（无文档系列）'}` | "
                f"`{_md_cell(row['material_type'])}` | "
                f"`{_md_cell(row['admission_state']) or '—'}` | "
                f"`{_md_cell(row['rmd_reason_code']) or '—'}` | "
                f"{len(row['aspect_ids'])} | "
                f"{'是' if row['in_manifest'] else '否'} | "
                f"`{_md_cell(row['writer_usage']) or '—'}` | "
                f"`{_md_cell(row['writer_reason_code']) or '—'}` | "
                f"{row['cited_sentences']} | {row['cited_hard_errors']} | {mark} |")
        L += [
            "",
            "**这张表的主语是「一份材料」，§4.2 那张表的主语是「一个栏目」**——两份账各说各的"
            "话：一份材料可以同时登记在几个栏目下，但它在这一张表里**只有一行**。",
            "**四态互斥且穷尽（材料粒度）**：`read_not_admitted`（读到内容并留了登记，研究侧"
            "未获准入，理由码在左列）→ `admitted_not_delivered`（获准进 Pack，没有进本节写作"
            "清单）→ `delivered_not_used`（进了清单，**正文里没有句子引用它**）→ `used`"
            "（正文里确有句子引用它）。",
            "**四态的判据只用看得见的那几轴**（研究侧登记 / 送达 / 正文引用）：`used` 是"
            "「草稿里确实有句子引用了它」这条**观测**，不是谁自报「我用了」。`delivered_not_used`"
            "只说「没写出去」；至于**为什么**没写出去，看右边 Writer 侧那一列——那一列取不到时"
            "本表如实说取不到（见上面的表头说明），**不**把它读成「未采用」的结论。",
            "**另外两态不在这个粒度上，本表**不**替它们说话**：`源中未定位` 与 `已定位未读` 的"
            "主语是「(栏目 × 来源)」——一份文档在某一栏上没有被读到，**不等于**这份文档里没有"
            "材料。把它们压进这张表，就会把「这一栏这次没读到」印成「这份材料不存在」，"
            "而那正是本批要堵掉的那句话。这两态由 §4.3.2 的四臂台账逐 `(栏目, 来源)` 回答。",
            "**「进清单=否」不等于「这份材料没用」**：它说的是**本次写作没有拿到它**，"
            "**不是**「语料里没有这条内容」。要派人的活是「为什么没送达」，不是「再去搜一遍」。",
            "**「引用它的句数」与「其中带硬错」是两列**：被采用与写得对是两件事——"
            "一句引用了这条材料、却在别的轴上判了硬错，两列会同时非零，读者能直接看到"
            "「用上了但用错了」。一句同时引用几条材料时，它的硬错会记到它引的**每条**材料上；"
            "逐句结论以 §6 为准（那张表的主语是句子）。",
            "**本行的排序理由码逐条照抄登记侧那一个**，不在读回里翻译成自由文本："
            "`not_used` 的理由必须落在 Writer 侧那张封闭表里，"
            "它是「本轮没用到」，**不是**「Contract 必需事实未取得」——两条轴不得互相顶替。",
            "",
        ]
        if ledger["defects"]:
            L += ["**本表里对不上的行（缺陷态，不是一种去向）**："
                  + "、".join(f"`{s}` {n}" for s, n in sorted(ledger["defects"].items()))
                  + "——这一类是账与账之间对不上，应按缺陷处理，不得读成某种正常去向。", ""]
        else:
            L += ["**本表没有缺陷态的行**：每份 Pack 材料都恰好落在四态之一，"
                  "清单里也没有 Pack 侧找不到的材料。", ""]
        #: `delivered_not_used` 的**派生读数**（`munr-1`）：正文里没有它的句子，就只能拿
        #: 可见的东西比。这三列**读时计算、不落盘**，也**不是** Writer 侧登记的理由码——
        #: 那一轴在本链上不存在（上面已经说了一次），本块不替它说话。
        unused = [r for r in ledger["rows"] if r["state"] == "delivered_not_used"]
        if unused:
            role_by_key = {str(m.citation_key): str(getattr(m, "source_role", "") or "")
                           for m in manifest.materials}
            L += ["**`delivered_not_used` 的派生读数——读时计算，不是 Writer 侧登记**", "",
                  "| # | 材料 | 来源角色 | 与已被引用材料逐字重复于 | 独有句数 |",
                  "|" + "---|" * 5]
            for index, row in enumerate(unused, start=1):
                L.append(f"| {index} | `{_md_cell(row['citation_key']) or '—'}` "
                         f"（`{_md_cell(row['material_id'])}`） | "
                         f"`{_md_cell(role_by_key.get(row['citation_key'], '')) or '—'}` | "
                         f"`{_md_cell(row['duplicate_of']) or '—'}` | "
                         f"{row['sole_carrier_sentences']} |")
            L += [
                "**这三列不解释动机**：它们只报「与哪一份逐字重复」「有多少句是已被引用材料里"
                "没有的」。重复**不等于**该材料没用（两份来源可以讲同一件事），"
                "独有句数非零也**不等于**该句可写（它仍要过登记、第 4 条与第 9 条）。"
                "**更不得把这里的任何一格读成「Contract 必需事实未取得」**——那是另一条轴。", ""]
        L += ["**逐份材料的身份与位置（四条身份轴 + 精确定位，逐条可回查）**", "",
              "| # | 材料 | 容器身份 | 来源身份 | 出处身份 | 内容指纹 | 定位 |",
              "|" + "---|" * 7]
        for index, row in enumerate(ledger["rows"], start=1):
            locator = row["locator"] or {}
            where = "、".join(
                f"{k}={_md_cell(v)}" for k, v in sorted(locator.items()) if v not in ("", None))
            fingerprint = str(row["content_fingerprint"] or "")
            L.append(f"| {index} | `{_md_cell(row['material_id'])}` | "
                     f"`{_md_cell(row['container_identity'])[:20] or '—'}` | "
                     f"`{_md_cell(row['source_identity'])[:20] or '—'}` | "
                     f"`{_md_cell(row['provenance_identity'])[:20] or '—'}` | "
                     f"`{fingerprint[:20] or '—'}` | {where or '—'} |")
        L += ["",
              "四轴与定位逐条取自 Pack 侧登记与材料本身（不复算、不补全）；身份串按前 20 位显示，"
              "完整值在对应的落盘产物里。", ""]

    # ---- 4.3.2 逐 (栏目 × 来源) 的四臂台账 -----------------------------------
    L += ["## 4.3.2 逐（栏目 × 来源）的四臂结果："
          "「源里有没有、读到没有」在这一级才有主语", ""]
    arms = _source_arm_ledger(inputs=inputs, section_id=section_id)
    if arms is None:
        L += ["（本节没有 Pack 材料边界：这一级不适用。）", ""]
    else:
        L += [f"- 本节 `pack_set` 内四臂记录 **{arms['record_count']}** 条，"
              f"覆盖 **{len(arms['aspect_ids'])}** 个栏目 × **{len(arms['documents'])}** 份来源"
              f"（逐 `(栏目, 来源)` **恰好一条**）。", ""]
        L += ["**四臂的读数**：" + "、".join(
            f"臂 `{arm}` {arms['arm_counts'].get(arm, 0)}"
            for arm in TS.SOURCE_ASPECT_OUTCOME_ARMS) + "。", ""]
        L += ["| 臂 | 含义（这一格说的是什么） |", "|---|---|",
              "| `A` | 这一栏在这份来源上**产出了材料**（材料 id 逐条在册）|",
              "| `B` | 这一栏在这份来源上**搜索过**，按终态如实记录 |",
              "| `C1` | 这一栏**本就不要求**在这份来源上检索（带 typed 依据）|",
              "| `C2` | **要求检索，但本轮没有查成**（带未履行原因）|", ""]
        #: 登记轴 vs 臂的**交叉清点**：这两条轴可以打架，而打起来这件事本身要印出来。
        #: `C2` 的定义是「**没有检索痕迹**」，不是「没有材料」；一只按「有没有 trace」判臂的
        #: 实现，会把所有**由确定性归属**（树节点 / 表对象）拿到材料的格记成 `C2 not_dispatched`。
        #: 所以这里逐格并排：这一格「没有查成」的同时，Pack 侧是不是**已经有材料**了。
        non_a = [r for r in arms["rows"] if r["arm"] != "A"]
        material_without_trace = [r for r in non_a if r["registered_materials"]]
        no_material_no_trace = [r for r in non_a if not r["registered_materials"]]
        L += [
            f"- **登记轴 × 臂的交叉清点**：非 A 臂 **{len(non_a)}** 格里，"
            f"Pack 侧**已有材料**的 **{len(material_without_trace)}** 格"
            f"（「已有材料、只是没有检索痕迹」），"
            f"**既无材料也无检索痕迹**的 **{len(no_material_no_trace)}** 格。"
            "这两个数**必须分开**：前者的缺口在**工具接线 / 痕迹**上，后者才谈得上"
            "「这一栏在这份来源上没有内容」。", ""]
        if non_a:
            L += ["**非 A 臂逐条在册**（`A` 臂的读数已在 §4.2 的「进 Pack」列里，"
                  "这里只列需要解释的那些）：", "",
                  "| # | 栏目 | 来源文档 | 臂 | **本格 Pack 材料（登记轴）** | **C2 细分** | "
                  "投影终态 | 停止原因 | 未履行原因 | 合格搜索 |",
                  "|" + "---|" * 10]
            for index, row in enumerate(non_a, start=1):
                registered_cell = row["registered_materials"]
                if row["arm"] != "C2":
                    c2_kind = "—"
                elif registered_cell:
                    c2_kind = "**已有材料、只是没有检索痕迹**"
                else:
                    c2_kind = "既无材料也无痕迹"
                L.append(f"| {index} | `{_md_cell(row['aspect_id'])}` | "
                         f"`{_md_cell(row['document_id'])}` | `{row['arm']}` | "
                         f"{registered_cell} | {c2_kind} | "
                         f"`{_md_cell(row['projected_terminal']) or '—'}` | "
                         f"`{_md_cell(row['synthesized_stop_reason']) or '—'}` | "
                         f"`{_md_cell(row['unfulfilled_reason']) or '—'}` | "
                         f"{'是' if row['qualified'] else '否'} |")
        else:
            L += ["**没有非 A 臂**：本节的每一个 `(栏目, 来源)` 格都产出了材料。", ""]
        L += [
            "",
            "**这一级回答的正是 §4.3 不回答的那两态**：`B` 臂带 `NOT_FOUND_AFTER_SEARCH` 才"
            "说得上「源中未定位」；`C2` 臂说的是「**这一栏在这份来源上本轮没有查成**」"
            "（能力、预算或派发所致），**不得**读成「这份来源里没有该内容」——它连一次合格的"
            "搜索都还没发生。`C1` 是「本就不要求」，与前三者又是另一件事。四种读数分开印在"
            "一行里，谁也不能替谁说话。",
            "**`C2` 这一臂自己还有两档，不能合并**（「本格 Pack 材料」那一列就是用来分的）："
            "本格**已有材料**的 `C2`，缺口在**工具接线 / 检索痕迹**上——材料已经由确定性归属"
            "（树节点 / 表对象）取得，只是没有留下一条检索记录；本格**既无材料也无痕迹**的 "
            "`C2`，才谈得上「这一栏在这份来源上没有内容」，而那也要等一次**合格**检索才能下"
            "结论。把两档合成一句「本轮没有查成」，读者就分不清该去修工具还是该去补来源。",
            "**`B` 臂而不合格（`UNQUALIFIED_SEARCH_OBSERVATION`）也不是「没有内容」**："
            "它是「搜过，但这次的观察条件不齐」，合格与否逐条在右列。",
            "",
        ]

    # ---- 5. 实际正文与引用 --------------------------------------------------
    L += ["## 5. 实际正文与逐句引用", ""]
    L.append(f"- 写作产出者身份：`{draft.writer_identity}`"
             + ("（**真实模型**写作）" if _real else "（**离线替身**，不是模型）")
             + "　——这一行只回答「谁写的」；写得对不对由第 6／7 节各自回答，不由本行代言。")
    L.append(f"- 草稿 id：`{draft.draft_id}`；句数：{len(draft.sentence_ids())}；"
             f"缺口：{len(draft.gaps)}")
    L.append("")
    L += ["**逐栏目盘点**（一句话里的引用来路，是判断「是否分层/切题」的最低限度读数）：", ""]
    L += ["| 小节 | Contract 要求 | 段数 | 句数 | 引到的文档 |", "|---|---|---|---|---|"]
    role_by_key = {m.citation_key: m.document_id for m in manifest.materials}
    #: 事实轴的来路是**权威种类**（不是文件名）：财务/附注/外部快照三支本来就没有材料文档，
    #: 让它们在这一列显示成自己的键名，读的人分不清「引了一条事实」与「引了个不存在的键」。
    role_by_key.update({f.citation_key: f"{f.authority_kind}" for f in manifest.facts})
    for subsection in draft.subsections:
        n_par = len(subsection.paragraphs)
        n_sent = sum(len(p.sentences) for p in subsection.paragraphs)
        docs = sorted({role_by_key.get(c, c) for p in subsection.paragraphs
                       for s in p.sentences for c in s.citations})
        req = next((s.requirement_text for s in manifest.subsections
                    if s.subsection_id == subsection.subsection_id), "")
        L.append(f"| `{subsection.subsection_id}` | {_md_cell(req)} | {n_par} | "
                 f"{n_sent} | {'、'.join('`' + d + '`' for d in docs) or '—'} |")
    L += ["", "**这份盘点只能说明「有没有写、写了几段」，不能说明「写得对不对」**——"
          "「切题」要人对着 Contract 要求逐条读，机器不代替这一步。", ""]
    for subsection in draft.subsections:
        L.append(f"### {subsection.title}")
        L.append("")
        L.append(f"`{subsection.subsection_id}`")
        L.append("")
        for paragraph in subsection.paragraphs:
            for sentence in paragraph.sentences:
                cites = "、".join(f"`{c}`" for c in sentence.citations) or "（无引用）"
                L.append(f"- `{sentence.sentence_id}`｜引用 {cites}｜{sentence.text}")
            L.append("")
    if draft.gaps:
        L += ["### 缺口（Writer 自己发出的）", ""]
        L += ["| 小节 | 原因码 | 说明 | Contract 要求 |", "|---|---|---|---|"]
        for gap in draft.gaps:
            L.append(f"| `{gap.subsection_id}` | `{gap.reason}` | {_md_cell(gap.detail)} | "
                     f"{_md_cell(gap.requirement_text)} |")
        L.append("")
    if draft.follow_up_needs:
        L += ["### `FollowUpNeed`", ""]
        for need in draft.follow_up_needs:
            L.append(f"- `{need.need_id}`｜{need.statement}")
        L.append("")

    # ---- 5.1 人工同义抽检位 ------------------------------------------------
    # 先把**机器侧可定**的那一半定死：每句是不是它所引**材料正文**或**权威事实命题文本**的
    # 逐字子串。两条轴分列读数：财务/附注这一支没有材料行，只有事实行，把两者合成一个数
    # 会让「这一节根本没有材料可摘」被读成「摘录失败」。
    # 这一步不代替同义性判断；它决定同义性判断在这一轮**是否可能**给出有信息量的结论。
    all_sentences = [s for sub in draft.subsections
                     for para in sub.paragraphs for s in para.sentences]
    verbatim_material = 0
    verbatim_fact = 0
    no_cite = 0
    for sentence in all_sentences:
        if not sentence.citations:
            no_cite += 1
            continue
        material = manifest.material_for_key(sentence.citations[0])
        if material is not None and sentence.text in material.reading_view:
            verbatim_material += 1
            continue
        fact = manifest.fact_for_key(sentence.citations[0])
        if fact is not None and sentence.text == fact.text:
            verbatim_fact += 1
    verbatim = verbatim_material + verbatim_fact
    cited = len(all_sentences) - no_cite

    L += ["## 5.1 人工同义抽检（**未完成**：这是留给人填的位，不是结论）", "",
          f"本批要求至少抽检 {HUMAN_SPOT_CHECK_MIN} 句的原文同义性。", "",
          "**机器侧可定的那一半（下面是读数，不是同义性结论）**：", "",
          f"- 句子总数 {len(all_sentences)}；无引用的 {no_cite} 句；",
          f"- 其余 {cited} 句中，**{verbatim_material} 句**是其所引材料 `reading_view` 的"
          f"逐字子串，**{verbatim_fact} 句**与其所引权威事实的命题文本逐字相同"
          f"（两条轴分列：本节材料行 {len(manifest.materials)} / 事实行 {len(manifest.facts)}）。",
          ""]
    if verbatim == cited and no_cite == 0:
        L += ["**这句读数决定了本节的第 5.1 项在离线替身这一轮做不出有信息量的结论。** "
              "离线写作替身只做抽取式拼接，它写出的每一句都是原文子串——抽样核对只能核对到"
              "「一模一样」，核对不到「改写后是否仍然同义」。同义性判断的对象必须是**同义改写**，"
              "而这一轮根本没有产生改写。", "",
              "因此本节保留并排表与留空列，但**不得**把「10 句逐字相同」记成「抽检通过」；"
              "该项连同 §0.1 的替身边界一起，移交授权真实 run。", ""]
    else:
        L += [f"（存在 {len(all_sentences) - no_cite - verbatim} 句不是逐字子串——这些是本节"
              "同义抽检真正要人看的部分。）", ""]

    L += ["下表按批要求给出前 %d 句并排（左侧机器读数已知逐字相同，右侧列留给同义性判断）："
          % HUMAN_SPOT_CHECK_MIN, ""]
    L += ["| # | 句 id | 正文句 | 所引来源的原文（材料正文节选 / 权威事实命题） | 人工结论 |",
          "|---|---|---|---|---|"]
    shown = 0
    for subsection in draft.subsections:
        for paragraph in subsection.paragraphs:
            for sentence in paragraph.sentences:
                if shown >= HUMAN_SPOT_CHECK_MIN:
                    break
                key = sentence.citations[0] if sentence.citations else ""
                material = manifest.material_for_key(key)
                fact = manifest.fact_for_key(key)
                excerpt = ""
                if material is not None:
                    idx = material.reading_view.find(sentence.text)
                    excerpt = (material.reading_view[max(0, idx - 40): idx + len(sentence.text) + 40]
                               if idx >= 0 else "（**该句在所引材料原文里找不到逐字位置**）")
                elif fact is not None:
                    # 事实轴：并排的「原文」就是这条权威事实自己的命题文本 + 它的坐标。
                    # 这里**不**去别的文档里找一段「看起来像出处」的话来当原文——那会让
                    # 「这句话出自哪条权威事实」变成一次猜测。
                    mark = "逐字相同" if sentence.text == fact.text else \
                        "**与权威命题文本不逐字相同**"
                    excerpt = (f"（{mark}）`{fact.authority_kind}` / "
                               f"`{fact.container_identity}` / `{fact.fact_id}`：{fact.text}")
                L.append(f"| {shown + 1} | `{sentence.sentence_id}` | "
                         f"{_md_cell(sentence.text)} | "
                         f"{_md_cell(excerpt) or '（无引用）'} | |")
                shown += 1
    if shown < HUMAN_SPOT_CHECK_MIN:
        L.append("")
        L.append(f"**本次可抽检的句子只有 {shown} 句，少于要求的 {HUMAN_SPOT_CHECK_MIN} 句**："
                 "这本身是一条读数（正文太短），不应当被记成「抽检通过」。")
    L.append("")

    # ---- 5.5 第三步：局部返修（`cwr-2`；**最多一次**） ----------------------
    # 政策版本由 `CRW.CITED_REWORK_POLICY_VERSION` 现取（`ndc-4` 起是 `cwrp-3`），不写死。
    rework = payload.get("rework_outcome")
    rework_request = payload.get("rework_request")
    gate = str(payload.get("rework_gate") or "")
    L += [f"## 5.5 第三步：局部返修（`{CRW.CITED_REWORK_SCHEMA_VERSION}` / "
          f"`{CRW.CITED_REWORK_POLICY_VERSION}`，最多 {CRW.CITED_REWORK_MAX_ROUNDS} 次）", ""]
    if gate == "rework_not_approved_in_this_batch":
        #: 这一条**不是**「没有可返修的对象」，而是「本批没有批返修这一类调用」。两者混成
        #: 一句话，读者会把一次**未获批**读成一次**不适用**——前者要重新裁决，后者不需要。
        L += ["- 本批**未获批**返修这一类调用（`cited-budget-3`）：请求面未构造、未发出。"
              "这不是「没有可返修的对象」，而是本次授权的类别集里没有它。", ""]
    elif gate == "no_prose_to_rework" or rework is None:
        L += ["- 本稿没有可返修的对象（不适用，不是失败）："
              + ("没有正文句" if gate == "no_prose_to_rework" else "没有可解析的正文句") + "。", ""]
    else:
        initial = payload["check_report"]
        counts = rework.disposition_counts
        L += [
            f"- 结论：`{rework.outcome}`（点名 "
            f"{len(rework.problem_sentence_ids) + len(rework.semantic_sentence_ids)} 句；"
            f"**受保护句 {rework.protected_sentence_count} 句**）",
            f"- 原稿：`{rework.base_draft_id[:24]}…`，硬错句 "
            f"**{len(initial.blocked_sentence_ids)}**",
            f"- 新稿：`{rework.reworked_draft_id[:24] or '（没有新稿）'}…`，硬错句 "
            f"**{len(rework.hard_error_sentence_ids)}**",
            "",
        ]
        if rework.draft is None:
            # `failed` 的第一种：回复不可解析 / 调用失败 ⇒ 根本没有新稿可对账。
            L += ["- **没有新稿可对账**：这一轮没有产出可解析的返修稿，因此"
                  "「保留了什么、撤下了什么」两栏都是空的——空**不是**「什么都没变」，"
                  "而是「这次返修没有结果」。初稿是有效草稿。", ""]
        else:
            L += [
                f"- 逐句去向（`cwr-2`，判据是**归一化编号台账**，不是两个编号空间求交集）："
                f"保留 **{counts['kept']}** 句、撤下 **{counts['withdrawn']}** 句、"
                f"**歧义 {counts['ambiguous']}** 句",
                "",
            ]
            L += ["| 点名句 | 去向 | 返修稿最终编号 | 歧义原因 |",
                  "|---|---|---|---|"]
            for row in rework.sentence_dispositions:
                L.append(
                    f"| `{row.base_sentence_id}` | `{row.disposition}` | "
                    f"{'、'.join('`' + fid + '`' for fid in row.final_sentence_ids) or '—'} | "
                    f"{('`' + row.ambiguity_reason + '`') if row.ambiguity_reason else '—'} |")
            L += ["",
                  "`kept` 只说明「这一句还在返修稿里」，**不**说明它被改好了；`withdrawn` 说明"
                  "这一句的 id 与正文都从新稿里消失了；`ambiguous` 是不可唯一对应的情形"
                  "（拆句/同文换号/合句），**没有**被算进前两个计数里。", ""]
        L += [
            "- 生效草稿：**"
            + ("新稿" if rework.outcome == "reworked" else "原稿（未生效）") + "**",
            f"- 留存：{_md_cell(_rework_retention_note(rework, gate=gate, has_base=checks_have_base))}",
            "",
        ]
        if rework.failure_detail:
            L += [f"- 失败说明：{_md_cell(rework.failure_detail)}", ""]
        declared = list(rework.declared_citation_changes or ())
        L += [f"- 引用改绑声明：**{len(declared)}** 条"
              + ("（没有改绑：新稿里没有出现基准稿之外的引用键）" if not declared else ""), ""]
        if declared:
            L += ["| 句（返修稿最终编号） | 模型临时编号 | 原引用 | 新引用 |", "|---|---|---|---|"]
            for row in declared:
                L.append(f"| `{row.get('sentence_id', '')}` | "
                         f"`{row.get('model_sentence_id', '')}` | "
                         f"{'、'.join('`' + str(k) + '`' for k in row.get('from') or ()) or '—'} | "
                         f"{'、'.join('`' + str(k) + '`' for k in row.get('to') or ()) or '—'} |")
            L.append("")
        dispositions = list(getattr(payload.get("rework_client"), "dispositions", ()) or ())
        if dispositions:
            L += ["**替身自报的改法**（`REWORK_DISPOSITIONS`，是**意图**不是**结果**："
                  "「它说它把这一句挪到了哪一栏」；上面那张表才是「这一句实际落在哪」）：", "",
                  "| 句 | 它说它会 | 轴 | 原栏目 | 新栏目 |", "|---|---|---|---|---|"]
            for row in dispositions:
                L.append(
                    f"| `{row.get('sentence_id', '')}` | `{row.get('disposition', '')}` | "
                    f"{'、'.join('`' + str(k) + '`' for k in row.get('check_kinds') or ()) or '—'} | "
                    f"{'、'.join(str(a) for a in row.get('from_aspect_ids') or ()) or '—'} | "
                    f"{'、'.join(str(a) for a in row.get('to_aspect_ids') or ()) or '—'} |")
            L.append("")
    if rework_request is not None:
        # 请求面大小是**预算申请要报的实数**，不是估计：它由 `NS.canonical_json` 的长度直接量出。
        body = NS.canonical_json(dict(rework_request.payload))
        L += [f"- 返修请求面：{len(rework_request.problem_sentence_ids)} 条机械硬错、"
              f"{len(rework_request.semantic_sentence_ids)} 条语义待审；"
              f"请求体 **{len(body)}** 字符（`{rework_request.request_id[:24]}…`）。", ""]
    L += ["- **这一步改不了什么**：它只收点名句，没被点名的句子正文与引用必须逐字原样；"
          "任何新引用键都要在 `citation_changes` 里逐条声明并与其在稿里的真实引用逐字相符。"
          "「返修过了」不是「正文好了」——本表不构成任何质量结论。", ""]

    L += _render_rework_quality_readback(payload)

    # ---- 6. 逐句硬检查 ------------------------------------------------------
    L += [f"## 6. 逐句硬核对（`{SC.SENTENCE_CHECK_SCHEMA_VERSION}`，含登记轴与呈现轴"
          "两条**并行**栏目归属）", ""]
    L += [
        f"- 核对报告 id：`{check_report.report_id}`",
        f"- 机械结论：`{check_report.mechanical_verdict}`",
        f"- 句数：{check_report.sentence_count}；被阻断句："
        f"{len(check_report.blocked_sentence_ids)}",
        "",
    ]
    L += ["| 句 id | 轴 | 结论 | 失败原因 | 表面 | 引用键 |",
          "|---|---|---|---|---|---|"]
    hard_total = 0
    na_total = 0
    for record in check_report.records:
        if record.verdict == "hard_error":
            hard_total += 1
        # 现行判据（`sc-4` 起）：`pass` 里混着「判过并通过」与「这一轴这次没判」两类。后者**不是**结论，
        # 因此在人读表里也换个词印出来——把「不适用」印成「通过」，就是在读回里把假门装回去。
        applicable = bool(getattr(record, "applicable", True))
        if not applicable:
            na_total += 1
        verdict_cell = ("**硬错误**" if record.verdict != "pass"
                        else ("通过" if applicable else "不适用（没判）"))
        L.append(f"| `{record.sentence_id}` | `{record.check_kind}` | "
                 f"{verdict_cell} | "
                 f"`{record.failure_reason or ''}` | "
                 f"{_md_cell('、'.join(record.surfaces)) or '—'} | "
                 f"{'、'.join('`' + c + '`' for c in record.citation_keys) or '—'} |")
    L += ["",
          f"- 记录数：{len(check_report.records)}；其中 **硬错误 {hard_total}** 条、"
          f"**不适用（没判）{na_total}** 条。",
          "- 「不适用」这一档（`sc-4` 起单独可读）说的是**这一轴这次没有可判的东西**"
          "（本句没有数字 / 没有引用表材料 / 本节没有栏目登记轴…），它既不是通过、也不是失败："
          "两个相反的读数都可能把它当成自己那一侧的证据。",
          ""]
    axis_counts: dict[tuple[str, str], int] = {}
    for record in check_report.records:
        if record.verdict == "hard_error":
            key = (record.check_kind, record.failure_reason)
            axis_counts[key] = axis_counts.get(key, 0) + 1
    if axis_counts:
        L += ["**硬错误按轴归并**（同一句可以在多轴上失败；本表是**记录数**，不是句数）：", ""]
        L += ["| 轴 | 失败原因码 | 记录数 |", "|---|---|---|"]
        for (kind, reason), count in sorted(axis_counts.items(), key=lambda kv: -kv[1]):
            L.append(f"| `{kind}` | `{reason}` | {count} |")
        L.append("")
        L += [
            f"- 失败句数（去重）：**{len(check_report.blocked_sentence_ids)}** / "
            f"{check_report.sentence_count} 句。",
            "",
        ]
        L += ["", "**逐轴读法**（每一根轴的失败各自说明，不合并成一句「正文有问题」）：", ""]
        for (kind, _reason) in sorted(axis_counts):
            L += _axis_note(kind)
        L += [
            ("记住谁是产出者：**这些硬错误是对**真实模型**正文的真实核对结果**。它们是"
             "确定性判据的读数，不是审阅意见，也不因审阅说「supported」而消失。"
             if _real else
             "记住谁是产出者：**这些硬错误是对离线替身正文的真实核对结果**，不是模型"
             "写作的读数。真实模型写作会不会踩同一批轴，**只能由一次授权 run 回答**——"
             "本读回不下结论。"),
            "",
        ]
    L += _check_family_readback(check_report=check_report)

    # ---- 6b. 被替身自己撤下的候选片 -----------------------------------------
    withheld = list(getattr(payload.get("prose_client"), "withheld", ()) or ())
    L += ["## 6b. 被替身自己撤下的候选片：**撤下 ≠ 来源里没有**", "",
          *WITHHELD_SECTION_INTRO]
    L += _withheld_table(withheld) if withheld else [
        "- 撤下片数：**0**（本次替身没有撤下任何候选片）", ""]

    # ---- 6c. 原 PDF 表区（路径 b） ------------------------------------------
    display = source_display
    L += ["## 6c. 原 PDF 表区（§0.21 路径 b，只读展示；**真实读数，无替身**）", ""]
    if display is None:
        L += ["**本次没有构建展示集**（fail-closed：不拿一张空表充当「已展示」）。", ""]
    else:
        L += [
            "**这一节证明的只有「原件长什么样」**，它**不**进 Pack / Writer 材料身份、"
            "**不**是 `TableObject`、**不**构成任何数字权威、**不**证明 Contract "
            "`set_complete`、**不**证明 TS5。两条路径的身份不可互换：展示集的身份体里"
            "没有 `report_version`。",
            "",
            f"- 展示集 id：`{display.display_set_id()}`；区域 {len(display.regions)} 个，"
            f"可展示 {len(display.displayable_regions)}、缺陷 "
            f"{len(display.regions) - len(display.displayable_regions)}",
            f"- **已取得人工确认的区域：{len(display.confirmed_regions)}**"
            "（本 run 不代替人签署：确认只能由人给出）",
            "",
            "| 区域 | 文档 | 版本 | 页 | 区域(点) | 表题 | 期间 | 单位 | 状态 | 渲染哈希 |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for record in display.regions:
            r = record.region
            L.append(
                f"| `{r.region_key}` | `{r.document_id}` | `{r.document_version}` | "
                f"{r.page_number} | `{tuple(r.region_points)}` | {_md_cell(r.title)} | "
                f"{_md_cell(r.period_label)} | {_md_cell(r.unit_label)} | "
                f"`{record.display_state}` | "
                f"{('`' + record.render_sha256[:16] + '…`') if record.render_sha256 else '—'} |")
        L.append("")
        #: 原表图**就地嵌入**（只印文件名等于让读者打不开）。顺序 = 登记顺序，
        #: 续表紧随其主表；本页与 PNG 同在 `out_dir`，故基准是 `source_display/`。
        _rendered = [r for r in display.regions if r.render_relpath]
        if _rendered:
            L += ["**原表区原件像素**（只读展示：证明「原件长什么样」，"
                  "**不**给区域里的数字任何格级或事实级资格，**不**证明 `set_complete`）：",
                  ""]
            for record in _rendered:
                r = record.region
                L.append(
                    f"- {r.rail}／第 {r.page_number} 页"
                    + ("（续表）" if r.continuation_of else "")
                    + f"：![{r.rail} 第{r.page_number}页 区域]"
                      f"(source_display/{record.render_relpath})")
            L.append("")
        defects = [d for record in display.regions for d in record.defects]
        if defects:
            L += ["**逐条缺陷**（「材料里明明有表却没展示」= 系统展示能力缺陷，"
                  "不得写成「上传材料没有表」）：", ""]
            for record in display.regions:
                for defect in record.defects:
                    L.append(f"- `{record.region.region_key}` `[{defect.kind}]` "
                             f"{_md_cell(defect.detail)}")
            L.append("")
        else:
            L += ["- 逐区域缺陷：**0**（本机原件上这些区域都取到了像素与可回查坐标）", ""]
        L += ["**候选表区登记**（「考察过但没采用」也必须给理由——否则「没展示」会被读成"
              "「材料里没有」）：", "",
              "| 文档 | 表区 | 决定 | 理由 |", "|---|---|---|---|"]
        for reg in display.registrations:
            L.append(f"| `{reg.document_id}` | {_md_cell(reg.area_label)} | "
                     f"`{reg.decision}` | {_md_cell(reg.reason)} |")
        L.append("")
        L += [
            "> 这些区域**尚未**由人确认：完整、清晰、忠实于原件这三件事只有人能判。"
            "本 run 不代替签署，也不把「已渲染」写成「已确认」。",
            "",
        ]

    # ---- 6d. 逐数字表面的最早丢失点台账 ------------------------------------
    L += _numeric_surface_ledger_readback(ledger=_numeric_surface_ledger(
        inputs=inputs, section_id=section_id, manifest=manifest, draft=draft,
        check_report=check_report))

    # ---- 7. 独立审阅意见 ----------------------------------------------------
    L += [f"## 7. 审阅意见（`{CR.CITED_REVIEW_SCHEMA_VERSION}` / `rvi-2`）", ""]
    if review_outcome is None:
        # 审阅没跑完：**正文照旧在第 5 节可读**，这里只报"缺了这一环"。把一份空意见表印出来
        # 会被读成"审阅跑了、一条意见都没有"，那正是本批要避免的那种误读。
        L += [
            f"> **本轮独立审阅未完成**（`{version.review_failure}`）。这一节**没有**任何独立"
            "审阅意见——不是「审阅看过、没问题」，而是这一环没有产出。",
            "> 正文、逐句机械核对、中文缺口与补件需求都在上文照旧可读；本次的交付物因此是"
            "**降级诊断预览**，不是一份完成品的正式报告版本。",
            "",
        ]
    else:
        L += [
            ("> **这一节是真实模型的独立只读语义审阅。** 它**只**提出逐句意见：不覆盖第 6 节的"
             "确定性硬错误、不改写正文、不新增写作前整节阻断门。它的结论与机械层**并列留档**，"
             "两者不一致时以机械硬错误为准。"
             if _real else
             "> **这一节是离线替身的回声，不是独立语义审阅。** 本脚本的审阅替身只做一件事："
             "把第 6 节的机械结论照抄成逐句意见。它**没有**、也**不能**做出 §0.20 的九类句义判断"
             "（不支持／夸大越界／因果误写／局部推整体／选择性取材／矛盾／旧材料当前化／"
             "重要限制遗漏／跑题）。真实的独立审阅必须在授权 run 里由真实模型产出。"),
            "> 产出者身份进身份体：这份产出在 `CitedReportVersion.review_producer_kind` 上记为"
            f"`{version.review_producer_kind or '（无审阅）'}`，③ 轴因此被**强制**停在"
            f"`{version.system_review_state}` —— 无论它给出多少条非 blocking 意见，都不够格写成"
            "「系统审阅已通过」。",
            "",
            f"- 审阅 bundle id：`{review_outcome.bundle_id}`；绑定版本："
            f"`{review_outcome.report_version}`",
            f"- 逐句覆盖：{len(review_outcome.sentence_ids)} 句，意见 "
            f"{len(review_outcome.issues)} 条（**逐句一条**，覆盖等式见 "
            f"`cited_review._check_sentence_coverage`）",
            f"- blocking 意见：{len(review_outcome.blocking_issue_ids)} 条",
            f"- 不在审阅对象内的句子（零引用，给不出合法的审阅单元）："
            f"{len(review_outcome.excluded_uncited_sentence_ids)} 句 "
            f"{list(review_outcome.excluded_uncited_sentence_ids)}",
            f"- 审阅与机械层**分歧**（审阅说 supported、机械层判硬错误）："
            f"{len(review_outcome.hard_error_override_sentence_ids)} 句 "
            f"{list(review_outcome.hard_error_override_sentence_ids)}"
            "　——两条轴各自留档，机械硬错误优先，不因审阅一句话而消失",
            "",
        ]
        L += ["| 句 id | 引用键 | 粗类 | 句义类 | 严重度 | 阻断 | 理由 |",
              "|---|---|---|---|---|---|---|"]
        # 逐句意见按句 id 排序后再渲染：审阅意见的容器不保证迭代顺序，
        # 而本表就是用来逐句对照的（同版本读回、跨 run 比对）。
        # 顺序不稳定会让读者把「同一句换了个位置」误读成「意见变了」。
        for issue in sorted(review_outcome.issues, key=lambda i: i.sentence_id):
            L.append(f"| `{issue.sentence_id}` | `{issue.citation_id}` | `{issue.category}` | "
                     f"`{issue.semantic_category or '—'}` | `{issue.severity}` | "
                     f"{'是' if issue.blocking else '否'} | {_md_cell(issue.reason)} |")
        # 分歧**逐处**摊开——只给一串句 id，读者回不到那句理由上。左边是机械侧的轴与原因码，
        # 右边是审阅自己写下的理由（逐字）。本表只并列，不裁决。
        #
        # 这份表是**读回时现算**的，不是 `CitedReviewOutcome` 的字段：它的两个输入
        # （`review_outcome.issues` 与本行上方的 `check_report`，亦即 `sentence_checks.json`）
        # 都已经落盘，做成字段就要升 `schema_version`，而升版会让每一份历史
        # `review_issues.json` 都解不出来（含已冻结的演示 run）。现算的结果逐字相同，
        # 还能回溯到旧 run 上——见 `sections/cited_review.hard_error_divergence_rows`。
        divergences = CR.hard_error_divergence_rows(
            issues=review_outcome.issues, check_report=check_report)
        if divergences:
            L += ["", "### 7b. 审阅与机械层的分歧（逐处：机械轴 × 审阅理由）", "",
                  "审阅面对机械结论是**故意盲的**，所以这不是越权，是一处**并列留档**：",
                  "机械硬错误优先，本节仍不可发布；这一表只回答「双方各自说的是什么」。", "",
                  "| 句 id | 机械轴 | 机械原因码 | 机械表面串 | 审阅引用键 | 审阅严重度 | "
                  "审阅理由（逐字） |", "|---|---|---|---|---|---|---|"]
            for row in divergences:
                surfaces = "、".join(row.get("surfaces") or ()) or "—"
                L.append(
                    f"| `{row['sentence_id']}` | `{row['check_kind']}` | "
                    f"`{row['failure_reason']}` | {_md_cell(surfaces)} | "
                    f"`{row['citation_id']}` | `{row['review_severity']}` | "
                    f"{_md_cell(row['review_reason'])} |")
        # 反向的一支：审阅**提出**了意见、而机械层 18 条轴**一条都没拦住**。这一支必须单独
        # 印出来，否则「机械绿」会被读成「这一句语义上没问题」——而确定性判据**看不见**
        # 「把发行人自述写成客观结论」「旧年材料当当期」「跨栏拼接」「重要限制遗漏」这几类事。
        review_only = CR.review_only_divergence_rows(
            issues=review_outcome.issues, check_report=check_report)
        if review_only:
            semantic_only = [r for r in review_only
                             if r["review_semantic_category"] != "supported"]
            L += ["", "### 7c. 审阅提出、机械层**没有**拦住的语义意见（逐处）", "",
                  "这一支的方向与 7b 相反：7b 是「机械说有错、审阅说 supported」（审阅对机械"
                  "结论是**故意盲的**）；本支是「**审阅说有语义问题、机械层的判据一条都没判出来**」。"
                  "两支都**不**裁决谁对，但「机械绿」在这里**不等于**「语义通过」："
                  "18 条轴里没有一条判「这句是不是把公司自述当成了客观结论」，也没有一条判"
                  "「这句是不是只引了对发行人有利的那一半」。本表列的是**只有独立语义审阅能提出**的"
                  "那一类问题，机械层必须原样留给它。",
                  "",
                  f"共 **{len(review_only)}** 条（其中带语义类的 **{len(semantic_only)}** 条；"
                  "`supported` 类意见不进本表——那不是意见）。"
                  "**这些句子在机械层是「没判出错」，不是「判过且语义通过」**：两件事不同，"
                  "本表存在就是为了不让它们长得一样。",
                  "",
                  "| 句 id | 机械侧判定 | 审阅粗类 | 审阅语义类 | 严重度 | 阻断 | "
                  "审阅理由（逐字） |", "|---|---|---|---|---|---|---|"]
            for row in review_only:
                kinds = row["mechanical_kinds"]
                verdicts = row["mechanical_verdicts"]
                if not kinds:
                    mech = "（本句**没有**任何机械记录）"
                else:
                    mech = ("、".join(f"`{v}`" for v in verdicts)
                            + ("" if all(row["mechanical_applicable"])
                               else "（含**本轴没判**：`applicable=false`，一个 pass "
                                    "不是结论）"))
                L.append(
                    f"| `{row['sentence_id']}` | {mech} | `{row['review_category']}` | "
                    f"`{row['review_semantic_category']}` | `{row['review_severity']}` | "
                    f"{'是' if row['review_blocking'] else '否'} | "
                    f"{_md_cell(row['review_reason'])} |")
        else:
            L += ["", "### 7c. 审阅提出、机械层**没有**拦住的语义意见（逐处）", "",
                  "**本表为空**。这句话有两种读法，必须分开：",
                  "- 若本次审阅**跑完了**且逐句给了意见：空表说的是「审阅没有提出任何机械层"
                  "拦不住的语义问题」——**不**等于正文语义上没有这类问题，只等于这一轮审阅"
                  "没提出来；",
                  "- 若意见表里**全是** `supported`：那是「审阅对每一句都说没问题」——"
                  "在机械层同时判出硬错误的场合，这本身就是**审阅没发挥作用**的证据"
                  "（见 7b），不得读成「正文没问题」。",
                  ""]
    L.append("")

    # ---- 8. 不可发布预览 ----------------------------------------------------
    L += ["## 8. 不可发布预览（四个状态轴并列，不合一）", ""]
    L += [
        f"- `report_version`：`{version.report_version}`",
        f"- `report_id`：`{version.report_id}`",
        f"- `process_state`：`{version.process_state}`",
        f"- `preview_state`：`{version.preview_state}`",
        f"- `mechanical_state`：`{version.mechanical_state}`",
        f"- `system_review_state`：`{version.system_review_state}`",
        f"- `human_review_state`：`{version.human_review_state}`",
        f"- `publishability`：`{version.publishability}`（**不可发布**）",
        f"- `review_failure`：`{version.review_failure or '（审阅这一环没有失败）'}`"
        "（它只说审阅**跑没跑完**，不说审阅**通没通过**——那是 ③ 轴的事）",
        f"- 阻断句：{version.blocking_sentence_count}；阻断意见："
        f"{version.blocking_issue_count}；必需缺口：{version.required_gap_count}",
    ]
    L += _gap_bin_readback(payload=payload, version=version)
    L += [
        "逐句预览（含机械结论与审阅意见两栏并列）在 `cited_preview.md`。",
        "",
        "**四个轴彼此独立**：流程跑完（`flow_complete`）不等于系统审阅通过，"
        "系统审阅通过也不等于人工接受，四者中任何一个都**不**等于可发布。",
        "反过来，`flow_incomplete`（审阅未跑完）也**不**等于「正文不可读」：正文、"
        "逐句硬核对与缺口照旧在 `cited_preview.md` 里逐句可见。",
        "",
    ]

    # ---- 9. 结论与待办 ------------------------------------------------------
    L += ["## 9. 本读回能说什么、不能说什么", "", "**能说：**", ""]
    L += [
        "- 冻结 Contract 的栏目逐条进了请求面，且 `requirement_text` 与契约逐字一致；",
        "- 输入面的每一条都带原文与可回查坐标，写作侧与复核侧读到的是同一串字节：材料行"
        "带 `reading_view` + locator，事实行带权威命题文本 + 它自己那一支的坐标"
        "（财务/附注/外部三支各按各的坐标，不统一成 Pack 材料的形状）；",
        "- 逐句引用可解析、可回查；逐句机械核对与逐句审阅都按 **一句一条** 覆盖；",
        "- 四个状态轴彼此独立地装配出来了，预览明确标注不可发布；",
        "- 真实文档上的逐表证明**逐张**有 typed 读数（在 `table_proof_matrix.json`）。",
        "  财务节若**建出了**确定性指标表，那么每一格的数值/期间/口径都逐字来自该权威事实自己的"
        "渲染，逐格核对的基准是**该事实自己的字段**（不再由「哪句正文引用了它」决定表格行），"
        "且这张表与正文在**同一条记录身份**下（§4.1）；表格成与不成**各有** typed 读数，"
        "「没建出来」与「本节没有表」是两件事；",
        "",
        "**不能说：**", "",
        ("- 不能说这份正文已被证明「语义上被支持」——真实模型写出来的句子仍需逐句机械核对与"
         "人工接受两条分开的读数，本节只给前者；"
         if _real else
         "- 不能说这是「真实模型写出来的正文」——写作侧与审阅侧都是离线替身；"),
        ("- 不能说主营业务正文达到了人读内容门——是否达门由**人**对着原件读，机器读数不代替它；"
         if _real else
         "- 不能说主营业务正文达到了人读内容门——替身只做抽取式拼接；"),
        ("- 不能说人工同义抽检已通过——§5.1 是**留给人填的位**；机器逐字核对只能得到"
         "「一模一样」，同义性判断的对象是**改写**。"
         if _real else
         "- 不能说人工同义抽检已通过——逐字核对只能得到「一模一样」（见 §5.1 的机器读数），"
         "同义性判断的对象是**改写**，这一轮没有产生改写；"),
        "- 不能说业务表数字已获权威——图侧**结构化**合格原表为 **0** 张，且读表资格从不等于"
        "数字权威；**也不**能反过来说「原表没展示」——§6c 的 12 个原件区域已经在只读面上"
        "展示，缺的是格级资格，不是原件；",
        "- 不能说原 PDF 表区**已经可以照着写数字**——区域展示只满足可见性；在人工确认之前"
        "（当前确认数见 §6c），它既不是合格 `TableObject`，也不是格值权威；",
        "- 不能说财务指标覆盖已判过——§4.1.1 只**逐条列出**必需指标的去向，不给「通过」结论；"
        "表格的输入是**真实**财务权威事实（`financial_workflow`，非替身），但它是按"
        "**离线**这一轮的选中事实集构造的，真实写作模型下的同一张表尚未跑过；",
        (("- 不能说系统审阅「通过」——③ 轴的状态是"
          f"`{version.system_review_state}`：独立审阅**有没有跑过**与**跑过之后算不算通过**是"
          "两件事，非 blocking 不等于通过；")
         if _real else
         ("- 不能说任何系统审阅通过——替身回声（`offline_diagnostic_echo`）称不上独立审阅，"
          "③ 轴因此被强制记成 `system_review_not_run`。")),
        "",
        "**留给下一步（需要逐次单独授权）：**", "",
        "- 一次真实 run：真实研究 LLM + 真实写作 LLM + 真实审阅 LLM，"
        "在同一批真实材料上重复本读回的每一步；",
        "- 该 run 要回答的问题、预算与预检结果在批次报告里单列，此处不预设；",
        "- **已落地**：财务指标表并入记录身份（`crpv-2` 的 `metric_table_ids` /"
        "`metric_tables_fingerprint`）——表内容一变，`record_id` 与预览版本随之变；版本锚"
        "`report_version` 仍只含写作侧输入（锚不随表变，两件事分开）。并入换掉了 `crpv-1` 的"
        "全部记录身份钉子，因此旧 run 的记录只作历史对照。",
        "",
    ]
    return "\n".join(L)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="M930-3 双节纵链同版本业务读回（离线，create-only）")
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--results-root", default=str(REPO / "evaluation" / "results"))
    parser.add_argument("--demo-scope-profile", default=DEFAULT_PROFILE)
    parser.add_argument("--subject", default=None,
                        help="报告输入声明的**主体**；缺省 = 只读列出财务库候选后取第一个")
    parser.add_argument("--subject-name", default=None,
                        help="主体名称（`subj-2`）；缺省 = 读库里唯一登记过的名称")
    parser.add_argument("--section", default=",".join(DEFAULT_SECTIONS),
                        help="要写的节，逗号分隔（默认 " + ",".join(DEFAULT_SECTIONS) + "）。"
                             "多于一个时节产物落 `run_dir/<section>/`，根上另写一份并排的 "
                             "`demo_page.md`")
    parser.add_argument("--topic", default=None,
                        help="要写的 topic，逗号分隔的 `节=topic[+topic]`（例如 "
                             "`company=company_business,financial=fin_source_scope"
                             "+fin_solvency`）。缺省 = 逐字取本节任务自己的 `topic_ids`"
                             "（推荐）；显式给出时必须与它**逐项相等**，少一项即 fail-closed。")
    parser.add_argument("--mode", choices=(ACC.MODE_OFFLINE, ACC.MODE_REAL),
                        default=ACC.MODE_OFFLINE,
                        help="`offline`（缺省）：写作与审阅都由替身产出，并装上断网 guard。"
                             "`real`：**只**把写作与审阅换成真实模型客户端，复用共享调用账本"
                             "与预算门（`cited-budget-3`：公司节与财务节各 ≤1 写 + ≤1 审，"
                             "合计 ≤4，自动重试 0；**返修本次未获批**，请求面根本不构造；"
                             "其余节在第一个请求之前拒绝）；研究侧仍逐字走离线装配——"
                             "本批没有获批研究调用。")
    parser.add_argument("--model", default=None,
                        help="`--mode real` 时的写作/审阅模型。缺省 = 取 `config.LLM_MODEL`"
                             "（项目当前获批的写作模型）。显式给出的值必须与它相等，"
                             "否则在**第一请求之前**停下——替项目换模型不是本批的事。")
    parser.add_argument("--run-input", default=None,
                        help="本次上传的原始字节所在目录（`cri-1` 布局，由演示页在点击「开始"
                             "生成」时落盘）。给出时，链里读 PDF 的三个点**全部**按 "
                             "`(document_id, sha256)` 从本目录取对象，**不回退**到登记路径；"
                             "缺一份、多一份、字节变化或 run_id 不符都在建立结果目录之前停下。"
                             "缺省 = 用登记路径（历史兼容运行，不是演示路径）。")
    parser.add_argument("--financial-input", default=None,
                        help="本次上传的**三份财务 XLSX** 所在目录（`cfi-1` 布局，"
                             "`financial_input_binding.json` + `financial_objects/`）。给出时链在"
                             "**建立结果目录之前**核验：这三份上传与财务库**当前有效快照**声明"
                             "的 `source_versions` 逐份同字节，且主体/期末/口径/币种/用途/快照 id "
                             "全部对得上，并在装配期再核一次快照没有漂移。本批**不重抽工作簿、"
                             "不写共享库**：财务节照旧从既有权威快照读事实，这三份上传只用于"
                             "证明「本次核对并复用的是同字节的已建立权威快照」。缺省 = 无财务"
                             "输入（真实模式下且要写财务节即拒）。")
    parser.add_argument("--authorization-root", default=None,
                        help="一次性运行授权（`cra-2`）所在目录。**只有真实模式会读它**："
                             "离线运行不发请求，因此不需要授权。缺省 = "
                             f"`data/{CRA.AUTHORIZATION_DIRNAME}/`。授权由人用 "
                             "`scripts/authorize_cited_run.py` 写，链自己从不写授权。")
    args = parser.parse_args(argv)
    section_ids = tuple(s for s in (x.strip() for x in str(args.section).split(",")) if s)
    return run(results_root=Path(args.results_root), run_id=args.run_id,
               profile_path=args.demo_scope_profile, subject=args.subject,
               subject_name=args.subject_name, section_ids=section_ids,
               topics=_parse_topic_map(args.topic, section_ids=section_ids),
               mode=args.mode, model=args.model,
               run_input=(Path(args.run_input) if args.run_input else None),
               financial_input=(Path(args.financial_input) if args.financial_input else None),
               authorization_root=(Path(args.authorization_root)
                                   if args.authorization_root else None))


def _parse_topic_map(raw: str | None, *, section_ids: tuple[str, ...]
                     ) -> dict[str, tuple[str, ...]]:
    """`--topic` 的解析：`节=topic[+topic...]` 逐节给；单节时也允许只给裸 topic。

    一节可以有**多于一个**选中 topic（本批的财务节就是两个：`fin_source_scope` +
    `fin_solvency`），因此值是元组而不是单值。节内用 `+` 分隔是因为 `,` 已经被用来分隔节。
    裸值在多节时一律拒 —— 「这个 topic 属于哪一节」这时无从派生，猜一个就是把一节的需求
    条文按到另一节头上（:func:`_resolve_topic_ids` 的同一条纪律）。

    这里**只解析**，不判断哪些 topic 该写：真正决定写什么的是本节任务自己的 `topic_ids`，
    解析结果与它逐项不符时 `_resolve_topic_ids` 会在开跑前停下。
    """
    text = str(raw or "").strip()
    if not text:
        return {}
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if all("=" not in p for p in parts):
        if len(section_ids) != 1:
            raise SystemExit(
                f"`--topic` 给的是裸 topic {text!r}，但本次有 {len(section_ids)} 个节："
                "请用 `节=topic` 的形式指明它属于哪一节")
        return {section_ids[0]: tuple(parts)}
    mapping: dict[str, tuple[str, ...]] = {}
    for part in parts:
        section_id, sep, topic_text = part.partition("=")
        if not sep or not section_id.strip() or not topic_text.strip():
            raise SystemExit(f"`--topic` 的片段 {part!r} 不是 `节=topic` 的形状")
        ids = tuple(t.strip() for t in topic_text.split("+") if t.strip())
        if not ids:
            raise SystemExit(f"`--topic` 的片段 {part!r} 里没有 topic")
        seen: list[str] = []
        for topic_id in ids:
            if topic_id in seen:
                raise SystemExit(f"`--topic` 的片段 {part!r} 里 topic {topic_id!r} 出现了两次")
            seen.append(topic_id)
        mapping[section_id.strip()] = tuple(seen)
    unknown = sorted(set(mapping) - set(section_ids))
    if unknown:
        raise SystemExit(f"`--topic` 指了本次不写的节 {unknown}（本次节 {list(section_ids)}）")
    return mapping


if __name__ == "__main__":  # pragma: no cover - 入口
    raise SystemExit(_main())

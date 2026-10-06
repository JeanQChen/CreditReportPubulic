"""Eval: M930-3 新 prompt 资产的暴露名与内容指纹（§16.7.1 P18；3C 追加 P19）。

用法: python -m evals.test_demo_writer_prompt_assets

本文件只钉**资产的现状与内容**，不跑生成链：资产名是否就是暴露名、资产自报的 (资产名,
revision) 是否等于代码登记值、正文指纹是否被 `verify_prompt_asset` 真覆盖、资产的输出契约
是否与解析层词汇表一致、以及旧资产 `pack_section_writer_v1.txt` **未被原位修改**。

`verify_prompt_asset` 的哈希校验是 P18 的硬要求（「必须被 `verify_prompt_asset` 的哈希校验
覆盖」/「资产名/哈希不符即拒」）：只登记「资产名@修订号」时，同名同修订号的两份不同正文会得到
同一个 prompt 身份，于是「记录版本 = 实际加载版本」变成一句空话。本文件用**单点正文变异**证明
这条校验真的在链上，而不是只写在文档里。

如实登记的边界：
1. 两个资产（新/旧）在 git 里都是**未跟踪**文件，因此「未被原位修改」无法用 `git diff HEAD`
   证明。本文件改用**在本批编码开始前捕获并钉住的正文指纹**作基线：此后任何原位修改都会让
   本文件失败。这是基线，不是「历史从未被改过」的证明。
2. 指纹按**行尾归一**（CRLF/CR → LF）后计算：同一份资产在 Windows/Unix 检出下字节不同、
   文本相同，按原始字节固定断言会把 EOL 差异误报成「资产被换过」。除行尾外不丢弃任何字符。
3. 「资产名 → `load_prompt` 的真实调用点」用 AST 静态核对（唯一加载点传入登记资产名），
   不伪装成运行时证明；真实链的 prompt 版本回填由 `evals/test_demo_pack_writer.py` 覆盖。

不调 LLM、不联网、不写任何文件。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import client as llm
from sections import claim_entailment_evaluator as CEE
from sections import narrative_schema as NS
from sections import pack_writer as PW

REPO = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO / "llm" / "prompts"

#: P18 的精确路径与暴露名（§16.7.1 P18：新增资产，精确路径）。M930-3 返修 P2（§三 2.4）之后
#: 登记的是 `proposals_v5`：**口径统一 + 子句纪律**前进——「路径 A 可为空」与「每栏目至少一条
#: 路径 A」这对自相矛盾的要求被重写成按栏目可用内容分档的一条口径（并明确「路径 A 为空时不
#: 得用路径 B 写数字」），且候选被要求写成**不带句末标点的原子子句**（带句末标点的候选串起来
#: 就是 `A。同时B。` 双句容器，已被冻结判据 `ng-8` 拒）。
#: M930-3 定点返修第二轮（§二）之后登记的是 `proposals_v6`：**分批请求**前进——本节的要求按
#: 确定性的 Contract aspect 范围分批发给模型，`projection.aspects` 只是**本批**要回答的那些；
#: 分批是**请求面**的切分，不是材料面的裁剪（`authority_facts` / `materials` / `must_use_facts`
#: / `gaps` 仍是完整的），各批输出由系统合并成一份完整有序的提案集再送门。
#: §三 A 明确要求资产变更走**显式版本递增 + 旧版只读保留**，不得在原资产上原位改写，
#: 也不得静默重新解释旧记录。
#: M930-3 返修（§二「让合格材料真正形成可读内容」）之后登记的是 `proposals_v7`：**输出面
#: 收窄**前进——支撑边只写短别名 `ref`（+ factual 边的 `support_role`），身份字段由系统从
#: 被引用的那一行确定性展开（`saref-1`），自己写身份字段即被拒；`narrative_draft_units` /
#: `follow_up_needs` / `must_use_facts` 的义务改成本批结算（不再每批重复整节）。
#: 指令 E 第 3 项（恢复材料驱动写作）之后登记的是 `proposals_v8`：**写作顺序**前进——
#: `natural_prose_draft` 先于候选，每个单元声明它的材料出处与它表达的事实原子；候选随之为
#: **原子**提交，且草稿声明的原子键与候选键必须一一对上（闭合核对）。
#: `pw-19`（r8 前最后一次请求契约窄修）之后登记的是 `proposals_v9`：**示例的剩余三格按输入面
#: 分档**前进——`pw-18` 只把草稿那一格按输入面分档，候选的首条支撑边、`narrative_draft_units`
#: 的 `context_support`、补件示例的 `budget_hint` 仍按「两张表都存在」写死，于是材料轴节照抄
#: 会引用**不存在的事实行**、事实轴节照抄会引用**不存在的材料行**、补件照抄会带着**空
#: `budget_hint`**（而它在检索执行门 `TR.build_follow_up_focus` 上是 fail-closed 的）。
#: v9 的示例写成**材料轴那一档**（在本节有材料行时逐字可展开），并把其余两档的替换逐条写清
#: （事实轴节：`source_fact_refs` + 事实行 `ref` + `context_support: []`；两张表都有行：候选边
#: 按断言性质挑路径 A 或路径 B）。输出面的**键集与键序一字未变**，因此 wire 仍是 `proposals-12`。
#: `pw-20`（M930-3 r8 后业务纵链收口 §一）之后登记的是 `proposals_v10`：**逐批支撑范围**前进
#: ——请求面新增 `batch_support_scope`（本批各 topic 的**可引用材料行**、每个 aspect 研究侧原样
#: 记录的 `status`、绑定它的权威事实行），并把「整节材料目录非空**不等于**本批每个栏目都写得出」
#: 与「本批一条候选都交不出时三个内容键一律空数组」写成纪律。真实现场是 r8 的 company 一节：
#: 46 个 aspect 分 4 批，第 4 批 9 个 `blocked` + 1 个 `partial`，模型交出 0 条候选与 3 段
#: 「本轮未取得…无法作出说明」的无出处草稿；批 1/4 与 2/4 已因同一形状各花掉一次共享额度，
#: 批 4/4 把额度用尽 ⇒ 整节 typed `schema_invalid`。输出面的**键集与键序一字未变**，
#: 因此 wire 仍是 `proposals-12`。
#: `pw-21`（指令 D §二·三条日期轴）之后登记的是 `proposals_v11`：**一般经营描述的落笔纪律**
#: 前进——新增一节「材料披露的写法：三条日期轴，一条都不能顶替另一条」：归属语（「据某年年度
#: 报告披露」）**不由写者写**，它由系统在门后按已登记来源确定性附加；不得把「该材料披露的情况」
#: 升格成「一直如此」（普遍化与否定式普遍化词表逐条点名）；新旧材料存在实质差异时不得用相似
#: 文本抹平；只有旧材料支持的内容须有历史来源归属或留缺口；新闻优先事件发生/生效日、只有发布
#: 日期时只能写「某日发布的报道提及……」；披露日未知就标未知（不得用上传日 / PDF 元数据 /
#: 财务期末替代）；`report_as_of` 与上面三条都不是一回事。真实现场：路径 B 候选一律不得含期间
#: （高风险表面），于是材料驱动的一般经营描述只能写成无期间的无时间态措辞——读者读起来就是
#: 「一直如此」。本批的解法**不是**放宽高风险门，而是把归属语从散文挪到系统渲染的读者面
#: （`sections/source_attribution.py`，`srattr-1`）。输出面的**键集与键序一字未变**，
#: 因此 wire 仍是 `proposals-12`。
#: `pw-22`（M930-3 r9 后返修 B）之后登记的是 `proposals_v12`：**草稿闭合的读法**前进——
#: 「草稿里声明的原子键必须与候选集一一对上」被改写成「键**集合**一一对上，但同一条候选
#: **可以**出现在多个草稿单元里」，并写明多 occurrence 不是免检通道（闭合核对**逐个 occurrence**
#: 拿该候选自己的支撑边核对出处：材料 ID、来源身份/报告期、来源角色；事实轴比事实行，错来源、
#: 错版本照拒）。真实现场是 r9 的四批保存字节：材料驱动的公司草稿把同一个原子在两份材料里各
#: 写了一次，`proposals-15` 的唯一性口径把这种正常写法判成闭合失败。本批**没有**删掉键集合相等
#: 这条断言、没有把支撑搬进 Claim 身份、没有删段或截断草稿文字。输出面的**键集与键序一字未变**，
#: 因此 wire 仍是 `proposals-12`。
PROPOSALS_ASSET_PATH = PROMPTS_DIR / "pack_section_writer_proposals_v12.txt"
PROPOSALS_ASSET_NAME = "pack_section_writer_proposals_v12"
PROPOSALS_ASSET_REVISION = "proposals-16"

#: 被 v7 取代的旧资产：必须**仍在原位且逐字节不变**，但**不再**是登记资产（按新→旧排列）。
#: 指纹于各自被取代的那一批编码时捕获，作基线用；它不证明「历史上从未被改过」，
#: 只证明「自那一批起未被原位修改」，也不得被登记成当前资产。
SUPERSEDED_ASSETS = (
    (PROMPTS_DIR / "pack_section_writer_proposals_v11.txt",
     "pack_section_writer_proposals_v11", "proposals-15",
     "df5c1bfe0162f822d14c87214bb023d372045ff7d8f700ddf03079da22e0a42e"),
    (PROMPTS_DIR / "pack_section_writer_proposals_v10.txt",
     "pack_section_writer_proposals_v10", "proposals-14",
     "40592ceeb6e569c130a0c035d0aaae5ad56666e43fd60c13b95e974b6fe18c6e"),
    (PROMPTS_DIR / "pack_section_writer_proposals_v9.txt",
     "pack_section_writer_proposals_v9", "proposals-13",
     "e887f03245f5d419015bba97ef77503f3f548a9d803df4828b16efb1cc67e9a1"),
    (PROMPTS_DIR / "pack_section_writer_proposals_v8.txt",
     "pack_section_writer_proposals_v8", "proposals-12",
     "e6234ea6156ddef85e15b3b8a500b099925a186184c2a9f7d0967b37711d6a5f"),
    (PROMPTS_DIR / "pack_section_writer_proposals_v7.txt",
     "pack_section_writer_proposals_v7", "proposals-10",
     "4a76968b096a45b1d13a7387918020ea22cb38f36d24d991aacbca59d7de30b4"),
    (PROMPTS_DIR / "pack_section_writer_proposals_v6.txt",
     "pack_section_writer_proposals_v6", "proposals-8",
     "966456662a1d8c70ba209746b347939afe332417e7f1b303a44eae5f472fe00b"),
    (PROMPTS_DIR / "pack_section_writer_proposals_v5.txt",
     "pack_section_writer_proposals_v5", "proposals-7",
     "7e28a2975c31801401228c7fa7302c970aad645ec9c389a280ad0e669ad03370"),
    (PROMPTS_DIR / "pack_section_writer_proposals_v4.txt",
     "pack_section_writer_proposals_v4", "proposals-6",
     "63df830179a3317f0c1e150cb5de6239a9349cb69c7f60618d2fa49107f3aabd"),
    (PROMPTS_DIR / "pack_section_writer_proposals_v3.txt",
     "pack_section_writer_proposals_v3", "proposals-5",
     "a53316c90adefc0e5caaa07b135535852867fc0b6c7957b46f111bbd79d4457d"),
    (PROMPTS_DIR / "pack_section_writer_proposals_v2.txt",
     "pack_section_writer_proposals_v2", "proposals-2",
     "c62109bc64f10f76f4e00b0995872151aa3c42d9feddb9878b920011d0f7624f"),
    (PROMPTS_DIR / "pack_section_writer_proposals_v1.txt",
     "pack_section_writer_proposals_v1", "proposals-1",
     "c0d036e39d4c725cef4b6e0b3488da337fc4b0b35246222dac56df846352253f"),
)
#: v3 相对 v2 的**行为面**证据：这两段纪律必须出现在后继资产里。它们不是措辞偏好——
#: 前者是 §二 的授权面（高风险表面不得由材料派生路径引入），后者是 §三 的原子性要求。
V3_ONLY_TOKENS = ("非高风险描述性", "原子性（每条候选只能是")
#: v4 相对 v3 的**行为面**证据（§三 A / 3.6）：代理口径的限定语必须逐字保留。这段措辞
#: 必须出现在 v4 及后继 —— 否则这轮「版本递增」只是换了名字，模型看到的行为面没有变。
V4_ONLY_TOKENS = ("口径限定语是这条事实自己的一部分",)
#: v5 相对 v4 的**行为面**证据（M930-3 返修 P2 §三 2.4）：口径统一与原子子句纪律。
#: 这两段措辞必须**只**出现在 v5 —— v4 里也有了，说明这轮差异并不存在；v5 里没有，说明
#: 「版本递增」只是换了名字。第一条钉住「路径 A 为空时不得用路径 B 写数字」，第二条钉住
#: 「候选是不带句末标点的原子子句」。
V5_ONLY_TOKENS = ("不得用路径 B 写数字", "原子子句")
#: v6 相对 v5 的**行为面**证据（M930-3 定点返修 §二）：分批请求面。
#: 这段措辞必须**只**出现在 v6 —— v5 里也有了，说明这轮差异并不存在；v6 里没有，说明
#: 「版本递增」只是换了名字。前两条钉住「有 `batch` 时 `projection.aspects` 就是**本批**要
#: 回答的那些」，第三条钉住「分批是请求面的切分、不是材料面的裁剪」，第四条钉住「多写/少写
#: 都会让整节覆盖出现缺口或重复」，第五条钉住「各批合并成一份完整有序的提案集再送门」。
V6_ONLY_TOKENS = ("分批请求（输入里的 `batch`）",
                  "本批只回答 `projection.aspects` 里列出的要求",
                  "切分，不是材料面的裁剪",
                  "**多写**或**少写**",
                  "合并成一份完整有序的提案集")
#: v7 相对 v6 的**行为面**证据（M930-3 返修 §二）：支撑选项短别名 + 本批输出范围。
#: 这段措辞必须**只**出现在 v7 —— v6 里也有了，说明这轮差异并不存在；v7 里没有，说明
#: 「版本递增」只是换了名字。第一条钉住「支撑边只写 `ref`」；第二条钉住「身份字段由系统
#: 展开、自己写会被拒」；第三条钉住「每批只回答本批栏目」——它正是 v6 里
#: 「`narrative_draft_units` 与 `follow_up_needs` 不受本批 aspect 范围限制」的反面。
V7_ONLY_TOKENS = ("支撑选项的短别名（`ref`）",
                  "一律不要自己写",
                  "每批只回答本批栏目")
#: v8 相对 v7 的**行为面**证据（指令 E 第 3 项：恢复材料驱动写作）：先草稿、后候选。
#: 这些措辞必须**只**出现在 v8 —— v7 里也有了，说明这轮差异并不存在；v8 里没有，说明
#: 「版本递增」只是换了名字。第一条钉住那一节的标题（写作顺序本身）；第二条钉住「草稿的
#: 出处只能是材料行、不得取事实行」；第三条钉住「草稿声明的原子与候选集必须一一对上」；
#: 第四条钉住那条纪律的实质——**先想这一段材料能写出什么话，再登记原子**，不是攒候选拼段。
V8_ONLY_TOKENS = ("先写自然草稿，再为草稿里的每个事实原子单独提交候选",
                  "不得取 `authority_facts` 的 `ref`",
                  "一一对上",
                  "候选拼接，不是从材料写出来的正文")
#: `proposals-12` 相对 `proposals-11` 的**行为面**证据（`pw-16` 定点返修 P1-a/P1-c）：
#: 草稿的出处从「只能是材料」改为**两条互斥的轴**，并把「`materials` 为空 ⇒ 不产出候选」
#: 修正为「**两张表都空**才没有可用内容」。这些措辞必须**只**出现在 `proposals-12` ——
#: `proposals-11` 里也有了，说明这轮差异并不存在；`proposals-12` 里没有，说明「版本递增」
#: 只是换了名字。第一条钉住第二条出处轴的字段名本身；第二条钉住两条轴**恰有一条非空**；
#: 第三条钉住「出处不是授权」这条实质（否则事实轴会被读成一条新的授权捷径）；
#: 第四条钉住那句被修正的判据（只按 `materials` 空判，会把一个本来能写的节写成空的）。
V12_ONLY_TOKENS = ("source_fact_refs",
                   "恰有一条非空",
                   "出处不是授权",
                   "两张表都空")
#: `proposals-13`（`pw-19`）相对 `proposals-12` 的**行为面**证据：示例的剩余三格按输入面分档。
#: 这些措辞必须**只**出现在 v9 —— v8 里也有了，说明这轮差异并不存在；v9 里没有，说明「版本
#: 递增」只是换了名字。第一条钉住事实轴那一档的 `context_support` 合法形状（空数组）；第二条
#: 钉住「本节**没有材料行**时补件之外的整段替换」；第三条钉住两张表都有行时候选边按断言性质
#: 挑路径；第四条钉住补件四个 id 的来源目录（不是 `authority_facts` 行里那个可能为空的同名
#: 字段）；第五条钉住 `budget_hint` 非空这条硬要求。
V13_ONLY_TOKENS = ("**写空数组 `[]`**",
                   "上面三处**逐处**换掉",
                   "候选的支撑边要按断言的性质挑",
                   "`authority_facts` 的行里也有",
                   "`budget_hint` **不得为空**")
#: `proposals-14`（`pw-20`）相对 `proposals-13` 的**行为面**证据：逐批支撑范围。
#: 这些措辞必须**只**出现在 v10 —— v9 里也有了，说明这轮差异并不存在；v10 里没有，说明
#: 「版本递增」只是换了名字。第一条钉住那一块的存在本身（`batch_support_scope`）；第二条钉住
#: **引用限定在本批范围内**（这是本批新增的边界，v9 里只有「只为 batch.topic_ids 里的 topic 写」
#: 这句面向**输出**的话，没有面对**引用**的限定）；第三条钉住「本批能引用哪些行」是**逐 topic**
#: 给出的（不是逐 aspect 的布尔结论）；第四条钉住那句被否掉的暗示——整节目录非空不等于本批
#: 写得出；第五条钉住本批零候选时三个内容键一律空数组；第六条钉住缺失陈述**不是缺口**。
V14_ONLY_TOKENS = ("`batch_support_scope`",
                   "引用限定在本批范围内",
                   "本批各 topic 下**可引用的材料\n  行**",
                   "整节材料目录非空**不等于**本批每个栏目都写得出",
                   "本批一条候选都交不出时，三个内容键一律是空数组",
                   "不会成为缺口，只会让整批被拒")

#: `proposals-15`（`pw-21`，指令 D §二·三条日期轴）**只应出现在 v11** 的措辞。它们逐条对应
#: 指令 D §二 的四点：归属语不由写者写（系统渲染）、一般经营描述不得写成「一直如此」、
#: 新闻事件日与发布日分开、披露日未知就标未知、`report_as_of` 不写。
V15_ONLY_TOKENS = ("材料披露的写法：三条日期轴，一条都不能顶替另一条",
                   "不要写归属语",
                   "不要把「该材料披露的情况」升格成「一直如此」",
                   "新旧材料存在实质差异时不得用相似文本抹平",
                   "某日发布的报道提及",
                   "披露日未知就标未知",
                   "报告生成日（`report_as_of`）与上面三条都不是一回事")
#: `proposals-16`（`pw-22`，M930-3 r9 后返修 B）**只应出现在 v12** 的措辞：草稿闭合从「按键
#: 唯一」改为「按键集合相等 + 逐 occurrence 核验」。它们逐条对应本批的四点——键集合仍须
#: 一一对上（不是取消这条断言）、同一条候选可以在多个草稿单元里出现、多 occurrence 要逐处
#: 对得上该候选自己的支撑边、同一份来源内的重复无意义且不得把两年相似表述并成「始终如此」。
#: 与 `proposals-12` 那一轮不同，这一轮的直接前驱 v11 **在盘上可读**（`SUPERSEDED_ASSETS`），
#: 因此这一节的「只应出现在 v12」是**可对账**的：逐条在 v11 正文里找得到就说明这轮差异不存在。
V16_ONLY_TOKENS = ("同一条候选可以出现在多个草稿单元里",
                   "不是重复报废",
                   "逐个 occurrence",
                   "多 occurrence 不是免检通道",
                   "把两年的相似表述并成一句「始终如此」同样不行",
                   "判的是两边的**键**集合相等，不是「一条候选只能出现一次」")

#: 旧资产（organizer 系列）：只作格式与纪律参照，**不得原位修改**，也不得被改写成新契约。
LEGACY_ASSET_PATH = PROMPTS_DIR / "pack_section_writer_v1.txt"
LEGACY_ASSET_NAME = "pack_section_writer_v1"
LEGACY_ASSET_REVISION = "organizer-4"
#: 于本批（M930-3B）编码开始前捕获的旧资产正文指纹（行尾归一后）。它只是**基线**：
#: 一旦旧资产被原位改动，本断言即失败。
LEGACY_ASSET_SHA256 = "ea4f9cf1507a1729c80952cbfb8e0dc1121f2e6bcac464f64f5f80b2627c1330"

#: §16.7.1 P19：Claim 级语义核验资产（精确路径）。它由 P9 **唯一**消费。
#: `cer-2`（M930-3.2 §三）：rubric 新增 `non_atomic_claim` 与「原子性先于蕴含」。
#: `cer-3`（M930-3 返修 ⑤）：原子性判据拆成两支——一般语义判据不变；候选**逐字镜像恰好一条**
#: 权威事实自身文本（含该事实自己的口径限定语）时不判 `non_atomic_claim`。两个旧资产
#: （`claim_entailment_evaluator_v1` / `..._v2`）都**不原位修改**，只作只读基线。
#: `cer-4`（M930-3 定点返修 r6 后 ①）：镜像读数从**逐字相等**改成**标点归一读视图上的内容
#: 相等**（`mir-2`）——真实 run r6 的 24 条财务候选 `match_count` 全为 `0`（写作侧契约要求
#: 候选不带句末标点），`cer-3` 的豁免一次都没生效，同一形状判出相反结论、必需事实无 Claim
#: 可引、整节中止。旧资产 `claim_entailment_evaluator_v3`（`cer-3`）**不原位修改**。
ENTAILMENT_ASSET_PATH = PROMPTS_DIR / "claim_entailment_evaluator_v4.txt"
ENTAILMENT_ASSET_NAME = "claim_entailment_evaluator_v4"
ENTAILMENT_ASSET_REVISION = "cer-4"
#: `cer-3` 资产（`claim_entailment_evaluator_v3.txt`）的冻结指纹：本批只**新增** `cer-4`，
#: 不改 `cer-3` 一个字（它承载 r6 的 24 条历史判定结论与「逐字镜像」这一版判据）。
ENTAILMENT_CER3_PATH = PROMPTS_DIR / "claim_entailment_evaluator_v3.txt"
ENTAILMENT_CER3_REVISION = "cer-3"
ENTAILMENT_CER3_SHA256 = (
    "4290b4b7b8876dde2f38b146d2462beba5ab00a0db7f6efef9acb3a5e6046fed")
#: `cer-4` 相对 `cer-3` 的**行为面**证据（①）：镜像读数在**标点归一读视图**上比较。
#: 这段措辞必须**只**出现在 `cer-4` —— `cer-3` 里也有了，说明这轮差异并不存在；`cer-4` 里
#: 没有，说明「版本递增」只是换了名字。它同时钉住「只动句读标点、实词逐字参与」这条边界。
CER4_ONLY_TOKENS = ("标点归一", "内容相等")
#: 被 cer-3 取代的资产（原位不变基线，不得再被登记）：`cer-1` 与 `cer-2`。
ENTAILMENT_SUPERSEDED_PATH = PROMPTS_DIR / "claim_entailment_evaluator_v1.txt"
ENTAILMENT_SUPERSEDED_REVISION = "cer-1"
ENTAILMENT_SUPERSEDED_SHA256 = (
    "d2b73059c64ad8acb2d002b19d1339414b1779220c15e0a0d003e875290d7622")
#: `cer-2` 资产（`claim_entailment_evaluator_v2.txt`）的冻结指纹：本批只**新增** `cer-3`，
#: 不改 `cer-2` 一个字（`cer-2` 下的历史判定结论必须仍可复现）。
ENTAILMENT_CER2_PATH = PROMPTS_DIR / "claim_entailment_evaluator_v2.txt"
ENTAILMENT_CER2_REVISION = "cer-2"
ENTAILMENT_CER2_SHA256 = (
    "935a31cb823ca3397ec45cd9140d5b8641cafc7c7879015754b2d478a54424b7")
#: legacy 章节级评估资产：M930 current Claim entailment **不得**复用它。
SECTION_EVALUATOR_ASSET = "section_evaluator"

#: 定稿对象与决定类字段：新资产的输出面里**一个都不许有**（P18 的固定契约）。
FORBIDDEN_OUTPUT_KEYS = (
    "SectionClaim", "SectionResult", "section_result_id", "claim_id", "citation",
    "accepted_binding", "accepted_binding_ids", "evaluation", "disposition",
    "narrative", "final_narrative", "review_issue",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _json_example(text: str) -> str:
    """取资产里那段**输出 JSON 样例**（「只输出 JSON」标记之后的第一段**配平**的 JSON 对象）。

    用配平扫描而不是「切到下一个章节标题」：样例之后紧跟哪些小节是资产自己的排版选择，
    按标题切会在新增小节时把说明文字混进 JSON 里（解析直接失败），而样例本身并没有变。
    """
    start_marker = "不要 Markdown 代码块围栏）："
    start = text.index(start_marker) + len(start_marker)
    begin = text.index("{", start)
    depth = 0
    in_string = False
    escaped = False
    for pos in range(begin, len(text)):
        char = text[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[begin:pos + 1]
    raise ValueError("资产的输出样例必须是一段配平的 JSON 对象")


#: §3 用的**最小但真实**的支撑选项别名表：让资产自带的输出样例能被解析层**真的**解析出来
#: （「prompt 与解析器不得各说一套」这条断言要真跑一遍展开，而不是只对键名）。它只服务于本
#: 文件，不冒充任何真实 run 的材料集合。
_SAMPLE_PACK_ID = "tp-sample-0001"
_SAMPLE_FACT_ID = "fact-sample-0001"
_SAMPLE_MATERIAL_ID = "mat-sample-0001"


def _sample_aliases() -> PW.SupportAliasTable:
    return PW.SupportAliasTable(
        facts=(PW.SupportOption(ref="f1", authority_kind="topic_pack",
                                container_identity=_SAMPLE_PACK_ID,
                                fact_id=_SAMPLE_FACT_ID),),
        materials=(PW.SupportOption(ref="m1", authority_kind="topic_pack",
                                    container_identity=_SAMPLE_PACK_ID,
                                    material_id=_SAMPLE_MATERIAL_ID),))


def _fenced_json(text: str) -> str:
    """取资产里**第一段** ```json 围栏内容（P19 资产的输出样例写法）。"""
    start = text.index("```json") + len("```json")
    end = text.index("```", start)
    return text[start:end].strip()


def _all_keys(obj) -> set[str]:
    """递归收集 JSON 里出现过的**全部键名**（对象键，不含取值）。"""
    found: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            found.add(str(key))
            found |= _all_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            found |= _all_keys(item)
    return found


def _load_prompt_call_sites(module_path: Path) -> list[ast.Call]:
    """静态收集 `load_prompt(...)` 的调用点（用于核对是否有第二处字面量资产名）。"""
    tree = ast.parse(_read(module_path), filename=str(module_path))
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "load_prompt"]


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

    def expect_error(fn, exc, msg: str, *, needle: str = "") -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc as e:
            if needle and needle not in str(e):
                failed += 1
                details.append(f"FAIL {msg}：原因不符（{str(e)[:160]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:160]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    # ============================================================ §1 资产路径与暴露名
    if not PROPOSALS_ASSET_PATH.exists():
        skipped += 1
        details.append(f"SKIP 缺资产 {PROPOSALS_ASSET_PATH.name}")
        return {"passed": passed, "failed": failed, "skipped": skipped,
                "details": details}

    system = _read(PROPOSALS_ASSET_PATH)
    check(PW.NARRATION_PROMPT_ASSET == PROPOSALS_ASSET_NAME,
          f"登记的资产名必须等于精确路径里的资产名（实际 {PW.NARRATION_PROMPT_ASSET!r}）")
    check(PW.NARRATION_PROMPT_REVISION == PROPOSALS_ASSET_REVISION,
          f"登记的修订号必须是 {PROPOSALS_ASSET_REVISION!r}"
          f"（实际 {PW.NARRATION_PROMPT_REVISION!r}）")
    check(PW.NARRATION_PROMPT_VERSION
          == f"{PROPOSALS_ASSET_NAME}@{PROPOSALS_ASSET_REVISION}",
          f"prompt 身份必须是 `<资产名>@<revision>`（实际 {PW.NARRATION_PROMPT_VERSION!r}）")
    # 暴露名经**唯一加载入口**解析到同一份文本（资产名即 `llm/prompts/<name>.txt`）。
    check(llm.load_prompt(PW.NARRATION_PROMPT_ASSET) == system,
          "暴露名必须经 `llm.load_prompt` 解析到该精确路径的同一份文本")
    declarations = PW._PROMPT_DECL_RE.findall(system)
    check(len(declarations) == 1,
          f"资产自报身份必须**恰好一处**（`search` 只认第一处，多处即存在被忽略的第二身份；"
          f"实测 {len(declarations)} 处）")
    declared = (f"{declarations[0][0]}@{declarations[0][1]}" if len(declarations) == 1 else "")
    check(declared == PW.NARRATION_PROMPT_VERSION,
          f"资产自报身份 {declared!r} 必须等于登记身份 {PW.NARRATION_PROMPT_VERSION!r}")

    # ============================================================ §2 内容指纹被真覆盖
    check(len(PW.NARRATION_PROMPT_SHA256) == 64
          and all(c in "0123456789abcdef" for c in PW.NARRATION_PROMPT_SHA256),
          f"登记的正文指纹必须是 64 位小写十六进制（实际 {PW.NARRATION_PROMPT_SHA256!r}）")
    actual_sha256 = PW.prompt_asset_fingerprint(system)
    check(actual_sha256 == PW.NARRATION_PROMPT_SHA256,
          f"资产正文指纹 {actual_sha256!r} 必须等于登记值 {PW.NARRATION_PROMPT_SHA256!r}"
          "（正文改动必须同时改登记值，否则链上 fail-closed）")
    PW.verify_prompt_asset(system, PW.WriterPolicy())
    passed += 1

    # 行尾归一：CRLF / CR 检出下文本相同，不得被误报成「资产被换过」。
    for eol, variant in (("CRLF", system.replace("\n", "\r\n")),
                         ("CR", system.replace("\n", "\r"))):
        check(PW.prompt_asset_fingerprint(variant) == PW.NARRATION_PROMPT_SHA256,
              f"{eol} 检出的同一份文本必须得到同一指纹")
        try:
            PW.verify_prompt_asset(variant, PW.WriterPolicy())
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {eol} 检出的同一份文本必须通过校验（抛 {type(e).__name__}）")
        else:
            passed += 1

    def _mutation(msg: str, *, needle: str, mutate) -> None:
        mutated = mutate(system)
        check(mutated != system, f"前置条件：该变异必须真的改动了正文（{msg}）")
        expect_error(lambda: PW.verify_prompt_asset(mutated, PW.WriterPolicy()),
                     PW.PackWriterError, msg, needle=needle)

    # 单点正文变异：资产名与修订号都照抄，只有正文变了一个字 → 必须被哈希校验拒。
    _mutation("正文改一个字（资产名/修订号照抄）必须被哈希校验拒",
              needle="正文指纹", mutate=lambda t: t.replace("候选提案器", "候选提案机"))
    _mutation("只加一个尾随换行也必须被拒（指纹不看内容多少，只看是否同一份文本）",
              needle="正文指纹", mutate=lambda t: t + "\n")
    _mutation("只在一行内插一个空格也必须被拒",
              needle="正文指纹", mutate=lambda t: t.replace("硬性禁止", "硬性 禁止"))
    _mutation("自报修订号漂移必须被拒（哈希之外的第一道身份核对）",
              needle="自报", mutate=lambda t: t.replace("proposals-16", "proposals-99"))
    _mutation("资产未自报身份必须被拒",
              needle="未自报", mutate=lambda t: "你是章节写作器（没有自报身份）")
    expect_error(
        lambda: PW.verify_prompt_asset(
            system, PW.WriterPolicy(
                prompt_version="pack_section_writer_proposals_v8@proposals-1")),
        PW.PackWriterError, "WriterPolicy 记录版本与登记版本不一致必须被拒",
        needle="记录版本必须等于")

    # AST 静态核对：写作器对 prompt 资产的**唯一**加载点传入的就是登记资产名，
    # 不存在第二处字面量资产名（否则「登记版本 = 实际加载资产」可被绕过）。
    load_sites = _load_prompt_call_sites(REPO / "sections" / "pack_writer.py")
    check(len(load_sites) == 1,
          f"写作器必须只有一处 `load_prompt` 调用（实测 {len(load_sites)} 处）")
    if load_sites:
        args = load_sites[0].args
        check(len(args) == 1 and isinstance(args[0], ast.Name)
              and args[0].id == "NARRATION_PROMPT_ASSET",
              "唯一加载点的第一个实参必须是 `NARRATION_PROMPT_ASSET`（不得是字面量资产名）")

    # ============================================================ §3 输出契约 ↔ 解析层
    block = _json_example(system)
    try:
        example = json.loads(block)
    except ValueError as exc:
        failed += 1
        details.append(f"FAIL 资产的输出样例必须是合法 JSON：{exc}")
        example = None
    if isinstance(example, dict):
        passed += 1
        check(tuple(example) == PW._PLAN_KEYS,
              f"输出样例顶层键必须恰是解析层登记的 {list(PW._PLAN_KEYS)}（实测 {tuple(example)}）")
        candidate = example["claim_candidates"][0]
        unit = example["narrative_draft_units"][0]
        follow_up = example["follow_up_needs"][0]
        # `pw-15`：样例的**第一个**顶层键必须是自然草稿单元——顶层的键序就是写作顺序的
        # 声明（先草稿、后候选）。它同时钉住「解析层认识这个键」与「样例给了这个键」。
        prose = example[tuple(PW._PLAN_KEYS)[0]]
        check(tuple(PW._PLAN_KEYS)[0] == "natural_prose_draft",
              "顶层第一键必须是 natural_prose_draft（写作顺序：先草稿、后候选）")
        check(tuple(prose[0]) == PW._PROSE_KEYS,
              f"自然草稿单元键必须恰是 {list(PW._PROSE_KEYS)}（实测 {tuple(prose[0])}）")
        check(tuple(candidate) == PW._CANDIDATE_KEYS,
              f"候选键必须恰是 {list(PW._CANDIDATE_KEYS)}（实测 {tuple(candidate)}）")
        check(tuple(unit) == PW._UNIT_KEYS,
              f"草稿单元键必须恰是 {list(PW._UNIT_KEYS)}（实测 {tuple(unit)}）")
        check(tuple(follow_up) == PW._FOLLOW_UP_KEYS,
              f"FollowUpNeed 键必须恰是 {list(PW._FOLLOW_UP_KEYS)}（实测 {tuple(follow_up)}）")
        factual_edge = candidate["support"][0]
        context_edge = unit["context_support"][0]
        # v7 之后样例给的是**短别名形式**：`ref` + factual 边的 `support_role`。身份字段
        # 一个都不写（写了即被拒），因此这里对账的是别名词表，而不是长格式词表。
        check(set(factual_edge) <= set(PW._ALIAS_FACTUAL_KEYS),
              f"样例的 factual 边必须只含别名形式的键 {list(PW._ALIAS_FACTUAL_KEYS)}"
              f"（实测 {tuple(factual_edge)}）")
        # `pw-19`：样例写的是**材料轴那一档**（本节有材料行）。候选的首条支撑边因此引用
        # **材料行**，而不再是事实行——v8 的样例在同一个「草稿走材料轴」的节里示范了一条
        # 指向事实行的支撑边，而公司/行业节可能**一条事实行都没有**（照抄即整批被拒）。
        # 事实轴那一档的三处替换由分支说明覆盖，本节下面逐一钉住那三段措辞确实在资产里。
        check(str(factual_edge.get("ref") or "").startswith(PW.MATERIAL_REF_PREFIX)
              and factual_edge.get("support_role") in NS.SUPPORT_ROLES,
              "样例第一条 factual 边必须给出**本档真的能展开**的那张表的 ref（材料行 `m<n>`）"
              "与 support_role")
        check(set(context_edge) <= set(PW._ALIAS_CONTEXT_KEYS),
              f"样例的 context 边必须**只有** ref（实测 {tuple(context_edge)}）")
        check(str(context_edge.get("ref") or "").startswith(PW.MATERIAL_REF_PREFIX),
              "样例的 context 边必须引用材料行（`m<n>`）")
        # 示例的**其余两档**必须在正文里逐条写清：只改样例本身而不写替换规则，模型在另一档
        # 里仍然只能靠猜（那正是 `pw-19` 要修的「示例与自己的输入面矛盾」）。
        for token in V13_ONLY_TOKENS:
            check(token in system,
                  f"资产必须写明事实轴那一档的替换 {token!r}（否则样例只在材料档可用）")
        # 样例必须真能被解析层接受（prompt 与解析器不得各说一套）——用**声明的别名表**展开。
        sample_aliases = _sample_aliases()
        try:
            parsed = PW.parse_writer_proposals(block, aliases=sample_aliases)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL 资产自带的输出样例必须能被 parse_writer_proposals 接受："
                           f"{type(exc).__name__}: {str(exc)[:160]}")
        else:
            passed += 1
            check((len(parsed["natural_prose_draft"]), len(parsed["claim_candidates"]),
                   len(parsed["narrative_draft_units"]), len(parsed["follow_up_needs"]))
                  == (1, 1, 1, 1),
                  "样例应解析出各恰一项（1 草稿单元 / 1 候选 / 1 草稿单元 / 1 申请）")
            # 草稿单元的出处**只能**是材料行：样例给的是 `m1`，展开后必须落到 material 的
            # manifest 成员引用上（不是事实行）——草稿的出处与支撑边指着同一批行。
            parsed_prose = parsed["natural_prose_draft"][0]
            check(parsed_prose["source_member_refs"]
                  == [NS.manifest_member_ref(_SAMPLE_PACK_ID, _SAMPLE_MATERIAL_ID)],
                  f"样例草稿单元的 source_member_refs 必须展开成材料行的成员引用"
                  f"（实测 {parsed_prose['source_member_refs']}）")
            check(parsed_prose["atom_candidate_keys"] == ["c1"]
                  and parsed_prose["text"] == str(prose[0]["text"]).strip(),
                  "样例草稿单元的原子键必须指向样例里的那条候选（c1），文本逐字保留")
            # 展开后必须与**长格式**逐字段同形：别名不是第二条口径，它只是少写了身份。
            # `pw-19`：样例引用的是材料行，因此展开成**路径 B**（材料派生）的 factual 边——
            # 身份字段仍全部来自被引用的那一行，模型一个都没给。事实行那一档由分支说明覆盖。
            expanded = parsed["claim_candidates"][0]["support"][0]
            check(set(expanded) == set(PW._FACTUAL_SUPPORT_KEYS)
                  and expanded["support_semantics"] == "factual"
                  and expanded["authorization_path"] == "path_b_material_derived"
                  and expanded["material_id"] == _SAMPLE_MATERIAL_ID
                  and not str(expanded["fact_id"] or ""),
                  "别名必须展开成与长格式**逐字段同形**的 factual 边（引用材料行 ⇒ 路径 B；"
                  "identity 来自被引用的那一行，fact 锚点不由模型给）")
            # 同一个 `m1` 在事实行那一档会被替换成 `f1`：把那条替换**真跑一遍**，证明事实轴
            # 那一档的形状在本表上也真的可展开（不是只写在分支说明里的一句话）。
            fact_block = json.dumps({
                "natural_prose_draft": [{
                    "prose_key": "p1", "text": "<事实轴草稿>",
                    "source_member_refs": [], "source_fact_refs": ["f1"],
                    "atom_candidate_keys": ["c1"]}],
                "claim_candidates": [{
                    "candidate_key": "c1", "claim_text": "<原子断言>",
                    "support": [{"ref": "f1", "support_role": "primary"}]}],
                "narrative_draft_units": [{
                    "unit_key": "u1", "unit_kind": "paragraph", "text": "<草稿>",
                    "context_support": []}],
                "follow_up_needs": []}, ensure_ascii=False)
            # 事实轴那一档的**输入面**是「本节一条材料行都没有」，因此展开用的别名表也必须
            # 只声明事实行——草稿的轴由**本请求的输入面**决定，不是由模型挑的。
            facts_only_aliases = PW.SupportAliasTable(
                facts=(PW.SupportOption(ref="f1", authority_kind="topic_pack",
                                        container_identity=_SAMPLE_PACK_ID,
                                        fact_id=_SAMPLE_FACT_ID),),
                materials=())
            try:
                parsed_fact = PW.parse_writer_proposals(fact_block,
                                                        aliases=facts_only_aliases)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                details.append("FAIL 事实轴那一档的形状（source_fact_refs + 事实行 ref + "
                               f"context_support 空数组）必须能被解析层接受：{exc}")
            else:
                passed += 1
                expanded_fact = parsed_fact["claim_candidates"][0]["support"][0]
                check(expanded_fact["authorization_path"] == "path_a_prevalidated"
                      and expanded_fact["fact_id"] == _SAMPLE_FACT_ID
                      and parsed_fact["narrative_draft_units"][0]["context_support"] == [],
                      "事实轴那一档必须展开成路径 A，且 context_support 为空数组")
            expanded_ctx = parsed["narrative_draft_units"][0]["context_support"][0]
            check(set(expanded_ctx) == set(PW._CONTEXT_SUPPORT_KEYS)
                  and expanded_ctx["support_semantics"] == "context"
                  and expanded_ctx["authorization_path"] == "context_only"
                  and expanded_ctx["support_role"] == "corroborating",
                  "别名必须展开成同形 context 边（corroborating + context + context_only）")
        # 别名形式**不得**混入身份字段：混了即拒（不是被忽略）。
        mixed = json.loads(block)
        mixed["claim_candidates"][0]["support"][0]["material_id"] = "m-1"
        expect_error(lambda: PW.parse_writer_proposals(json.dumps(mixed, ensure_ascii=False),
                                                       aliases=sample_aliases),
                     PW.PackWriterError, "别名形式自带 material_id 必须被解析层拒",
                     needle="短别名形式")
        # 长格式仍然合法：它走**同一个**校验器，因此路径 A/B 互斥在这里照样成立。
        long_block = json.dumps({
            "claim_candidates": [{
                "candidate_key": "c1", "claim_text": "<路径 A 断言>",
                "support": [{"authority_kind": "topic_pack", "container_id": _SAMPLE_PACK_ID,
                             "fact_id": _SAMPLE_FACT_ID, "material_id": None,
                             "support_role": "primary", "support_semantics": "factual",
                             "authorization_path": "path_a_prevalidated"}]}],
            "narrative_draft_units": [], "follow_up_needs": []}, ensure_ascii=False)
        # `pw-16`：本节测的是**候选边的长格式**（别名是新增写法，不是替换旧写法），与草稿层
        # 无关，因此这里**显式**声明不要求草稿——那是当前线唯一的破例入口（历史线），
        # 「有候选无草稿必被拒」这一条另有一节单独证。不显式声明的调用一律按当前线判。
        check(len(PW.parse_writer_proposals(long_block,
                                            require_natural_draft=False)["claim_candidates"]) == 1,
              "长格式支撑边必须仍然合法（别名是新增写法，不是替换旧写法）")
        both = json.loads(long_block)
        both["claim_candidates"][0]["support"][0]["material_id"] = "m-1"
        expect_error(lambda: PW.parse_writer_proposals(json.dumps(both, ensure_ascii=False)),
                     PW.PackWriterError, "路径 A 自带 material_id 必须被解析层拒",
                     needle="不由模型选择")
        # 反向：路径 B 同时带上 fact_id 必须被拒。
        both_b = json.dumps({
            "claim_candidates": [{
                "candidate_key": "c1", "claim_text": "<路径 B 断言>",
                "support": [{"authority_kind": "topic_pack", "container_id": _SAMPLE_PACK_ID,
                             "fact_id": _SAMPLE_FACT_ID, "material_id": "m-1",
                             "support_role": "primary", "support_semantics": "factual",
                             "authorization_path": "path_b_material_derived"}]}],
            "narrative_draft_units": [], "follow_up_needs": []}, ensure_ascii=False)
        expect_error(lambda: PW.parse_writer_proposals(both_b),
                     PW.PackWriterError, "路径 B 携带 fact_id 必须被解析层拒",
                     needle="不得携带任何事实身份")

    # ============================================================ §4 不得输出定稿对象
    example_keys = _all_keys(example) if isinstance(example, dict) else set()
    hit = sorted(set(FORBIDDEN_OUTPUT_KEYS) & example_keys)
    check(not hit,
          f"输出样例里不得出现任何定稿对象/决定字段（实测 {hit}）")
    check("你不输出定稿对象" in system,
          "资产必须显式声明「不输出定稿对象」")
    for token in ("SectionClaim", "SectionResult", "accepted binding"):
        check(token in system, f"资产的禁止清单必须点名 {token}（否则禁止是含糊的）")
    for token in ("禁止检索", "禁止做任何计算", "follow_up_needs"):
        check(token in system, f"资产必须显式禁止/给出该纪律：{token}")

    # ============================================================ §5 旧资产未被原位修改
    if not LEGACY_ASSET_PATH.exists():
        failed += 1
        details.append(f"FAIL 旧资产 {LEGACY_ASSET_PATH.name} 必须仍在原位（删掉它也是原位修改）")
    else:
        legacy = _read(LEGACY_ASSET_PATH)
        legacy_decls = PW._PROMPT_DECL_RE.findall(legacy)
        legacy_declared = (f"{legacy_decls[0][0]}@{legacy_decls[0][1]}" if legacy_decls else "")
        check(legacy_declared == f"{LEGACY_ASSET_NAME}@{LEGACY_ASSET_REVISION}",
              f"旧资产必须仍自报 {LEGACY_ASSET_NAME}@{LEGACY_ASSET_REVISION}"
              f"（实测 {legacy_declared!r}）：新契约必须走新资产名")
        legacy_sha256 = PW.prompt_asset_fingerprint(legacy)
        check(legacy_sha256 == LEGACY_ASSET_SHA256,
              f"旧资产正文指纹 {legacy_sha256!r} 必须等于本批基线 {LEGACY_ASSET_SHA256!r}"
              "（原位修改旧资产必须失败：新契约不得覆盖旧资产）")
        check(legacy_sha256 != actual_sha256,
              "新旧资产必须是两份不同文本（同文本换名不构成新资产）")
        legacy_gain = [tok for tok in ("claim_candidates", "narrative_draft_units",
                                      "follow_up_needs", "support_semantics",
                                      "authorization_path")
                       if tok in legacy]
        check(not legacy_gain,
              f"旧资产不得被改写成新契约（实测已含 {legacy_gain}）")

    # ============================================================ §5b 版本递增取代旧资产的方式
    # 每次资产前进都必须走**显式版本递增 + 旧版只读保留**：旧资产仍在原位且逐字节不变、
    # 不再被登记、且新一轮的差异是**真实的**（不是换个文件名）。这里对每一个被取代的版本
    # 逐一核验，而不是只核最新那一次。
    for sup_path, sup_name, sup_rev, sup_sha in SUPERSEDED_ASSETS:
        if not sup_path.exists():
            failed += 1
            details.append(f"FAIL 被取代的 {sup_path.name} 必须仍在原位"
                           "（删掉它也是原位修改：旧记录必须可被审计读取）")
            continue
        superseded = _read(sup_path)
        sup_decls = PW._PROMPT_DECL_RE.findall(superseded)
        sup_declared = (f"{sup_decls[0][0]}@{sup_decls[0][1]}" if sup_decls else "")
        check(sup_declared == f"{sup_name}@{sup_rev}",
              f"被取代的资产必须仍自报 {sup_name}@{sup_rev}"
              f"（实测 {sup_declared!r}）：版本递增不得就地改写旧资产")
        sup_sha256 = PW.prompt_asset_fingerprint(superseded)
        check(sup_sha256 == sup_sha,
              f"被取代的 {sup_name} 正文指纹 {sup_sha256!r} 必须等于本批基线 {sup_sha!r}"
              "（旧版只读，不得原位修改）")
        check(PW.NARRATION_PROMPT_ASSET != sup_name
              and PW.NARRATION_PROMPT_VERSION != f"{sup_name}@{sup_rev}",
              f"被取代的 {sup_name} 不得仍是登记资产（新旧登记并存会让「实际加载版本」"
              "失去唯一性）")
        check("path_b_material_derived" in superseded,
              f"前置条件：{sup_name} 也是同一套提案契约（差异只在本轮声明的那一面，"
              "不是换了职责）")
    # v3 的**输出纪律**证据：§二 的授权面与 §三 的原子性都必须写进资产，且只在 v3 出现
    # （v2 里没有 ⇒ 版本递增对应的确实是新的行为面）。
    for token in V3_ONLY_TOKENS:
        check(token in system,
              f"v6 必须保留 v3 的纪律 {token!r}（后继资产不得悄悄收回）：真版本递增，不是改名")
    for token in ("不得携带任何", "逐字存在"):
        check(token in system,
              f"v6 必须保留「高风险表面**即使逐字存在**也不得进入路径 B」这一条：缺 {token!r}")
    # 被取代版本的正文：按**名字**取，不按位置取 —— 列表每加一版都会让下标错位。
    superseded_text = {name: _read(path) for path, name, _, _ in SUPERSEDED_ASSETS}
    v5_text = superseded_text["pack_section_writer_proposals_v5"]
    v4_text = superseded_text["pack_section_writer_proposals_v4"]
    v3_text = superseded_text["pack_section_writer_proposals_v3"]
    v2_text = superseded_text["pack_section_writer_proposals_v2"]
    for token in V3_ONLY_TOKENS:
        check(token in v3_text and token not in v2_text,
              f"前置条件：{token!r} 必须在 v3 出现、在 v2 不出现（这轮差异才真实）")
    # v4 的**口径纪律**证据（§三 3.6）：必须出现在 v4 及后继 —— v3 里也有了，说明这轮递增
    # 没改行为面，只是改了名字。
    for token in V4_ONLY_TOKENS:
        check(token in system,
              f"v6 必须保留 v4 的代理口径纪律 {token!r}（后继资产不得悄悄收回）")
        check(token not in v3_text,
              f"前置条件：{token!r} 必须只在 v4 及之后出现——v3 里也有了，说明这轮差异并不存在")
    # v5 的**口径统一 + 子句纪律**证据（M930-3 返修 P2 §三 2.4）：必须**只**出现在 v5 及之后
    # —— v4 是它的直接前驱，v4 里也有了说明这轮递增没改行为面；v5 里没有说明只是换了名字。
    for token in V5_ONLY_TOKENS:
        check(token in system,
              f"v6 必须写明 v5 那一轮的口径统一/子句纪律 {token!r}（后继资产不得悄悄收回）")
        check(token not in v4_text,
              f"前置条件：{token!r} 必须只在 v5 及之后出现——v4 里也有了，说明这轮差异并不存在")
    # v6 的**分批请求面**证据（M930-3 定点返修 §二）：必须**只**出现在 v6 —— v5 是它的直接
    # 前驱，v5 里也有了说明这轮递增没改行为面；v6 里没有说明只是换了名字。
    for token in V6_ONLY_TOKENS:
        check(token in system,
              f"v6 资产必须写明本轮的分批请求面 {token!r}（真版本递增，不是改名）")
        check(token not in v5_text,
              f"前置条件：{token!r} 必须只在 v6 出现——v5 里也有了，说明这轮差异并不存在")
    # v7 的**输出面收窄**证据（M930-3 返修 §二）：支撑边短别名 + 本批结算。必须**只**出现在
    # v7 —— v6 是它的直接前驱，v6 里也有了说明这轮递增没改行为面；v7 里没有说明只是换了名字。
    v6_text = superseded_text["pack_section_writer_proposals_v6"]
    for token in V7_ONLY_TOKENS:
        check(token in system,
              f"v7 资产必须写明本轮的输出面收窄 {token!r}（真版本递增，不是改名）")
        check(token not in v6_text,
              f"前置条件：{token!r} 必须只在 v7 出现——v6 里也有了，说明这轮差异并不存在")
    # v8 的**写作顺序**证据（指令 E 第 3 项：先草稿、后候选）：必须**只**出现在 v8 ——
    # v7 是它的直接前驱，v7 里也有了说明这轮递增没改行为面；v8 里没有说明规则只停在代码里。
    v7_text = superseded_text["pack_section_writer_proposals_v7"]
    for token in V8_ONLY_TOKENS:
        check(token in system,
              f"v8 资产必须写明本轮的写作顺序 {token!r}（真版本递增，不是改名）")
        check(token not in v7_text,
              f"前置条件：{token!r} 必须只在 v8 出现——v7 里也有了，说明这轮差异并不存在")
    # 写作顺序是**硬性**的先后，不是措辞：草稿先，候选后，且两者必须互相指得到。
    for token in ("natural_prose_draft", "prose_key", "atom_candidate_keys",
                  "source_member_refs"):
        check(token in system and token not in v7_text,
              f"v8 资产必须给出自然草稿的字段名 {token!r}（v7 里不该有）")
    # `proposals-12` 的**第二条出处轴**证据：这些措辞必须出现在当前资产、且 v7 那份正文里
    # 一个字都没有——否则「两条互斥的轴」这轮差异只是换了名字。
    #
    # 对账基准取 v7 而不是 `proposals-11`：同一资产名内的**修订递增是原位改写**（前一轮
    # `proposals-10 → proposals-11` 就是这样做的），退下来的那版正文不在盘上，没有任何可读
    # 的副本。因此这一节能证明的是「相对上一个**资产**，行为面确实变了」，不能证明「相对
    # `proposals-11` 变了」——后者的身份由 `NARRATION_PROMPT_SHA256` 钉住（正文改一字即
    # fail-closed），但它的**内容**已不可读。这是本批**记录在案、未修**的一条审计缺口：
    # 修它要给同资产名的每个修订留一份只读副本，那是上游的版本保留策略，不在本批授权内。
    for token in V12_ONLY_TOKENS:
        check(token in system,
              f"当前资产必须写明本轮的出处轴 {token!r}（真版本递增，不是改名）")
        check(token not in v7_text,
              f"前置条件：{token!r} 必须只在当前修订出现——v7 里也有了，"
              "说明这轮差异并不存在")
    # `proposals-13`（`pw-19`）的**示例剩余三格**证据：这些措辞必须出现在 v9、且直接前驱 v8
    # 那份正文里一个字都没有——否则这轮「版本递增」只是换了名字（示例的第三格是**真**行为面：
    # 模型照抄样例里的 ref 与 budget_hint 会被同一份请求判违规）。
    v8_text = superseded_text["pack_section_writer_proposals_v8"]
    for token in V13_ONLY_TOKENS:
        check(token in system,
              f"v9 资产必须写明本轮示例剩余三格的分档 {token!r}（真版本递增，不是改名）")
        check(token not in v8_text,
              f"前置条件：{token!r} 必须只在 v9 出现——v8 里也有了，"
              "说明这轮差异并不存在")
    # 直接前驱 v8 的**缺陷本身**必须还在 v8 里（本轮修的就是它），否则「修了什么」无从对账：
    # v8 的样例把候选边写成事实行、补件预算写成空串——这两处正是 `pw-19` 修掉的。
    check('"ref": "f1"' in v8_text and '"budget_hint": ""' in v8_text,
          "前置条件：v8 的样例必须就是「候选边用 f1、补件预算为空串」那份（本轮要修的对象）")
    # 判的是**样例块本身**（`block`）而不是全文：分支说明里当然要写出 `f1` 那条替换，
    # 那是「另一档怎么写」，与样例示范哪一档是两件事。
    check('"ref": "f1"' not in block and '"budget_hint": ""' not in block,
          "v9 的样例不得再示范一条可能无从展开的事实行/一个空的补件预算")
    check('"ref": "m1"' in block and '"budget_hint": "tree_inspect:1"' in block,
          "v9 的样例必须给出材料行 ref 与非空的 budget_hint（材料轴那一档的合法形状）")
    check('"source_fact_refs": ["f1"]' in system,
          "v9 必须写出事实轴那一档的出处替换（`source_fact_refs` 引事实别名）")
    # `proposals-14`（`pw-20`）的**逐批支撑范围**证据：这些措辞必须出现在 v10、且直接前驱 v9
    # 那份正文里一个字都没有——否则这轮「版本递增」只是换了名字。这一轮的行为面是**真**的：
    # 请求里多了一块 `batch_support_scope`，而它约束的是「哪些行可以引用」——v9 只在 `batch`
    # 块的 `scope_rule` 里写过「只为 batch.topic_ids 里的 topic 写」，那是对**输出**的要求，
    # 没有对**引用**的限定，也没有回答「本批写不写得出来」这件事。
    v9_text = superseded_text["pack_section_writer_proposals_v9"]
    for token in V14_ONLY_TOKENS:
        check(token in system,
              f"v10 资产必须写明本轮的逐批支撑范围 {token!r}（真版本递增，不是改名）")
        check(token not in v9_text,
              f"前置条件：{token!r} 必须只在 v10 出现——v9 里也有了，说明这轮差异并不存在")
    # 逐批支撑范围**不是**逐 aspect 的布尔结论：材料在 Pack 侧只有 topic 归属，按 topic 派生的
    # 「有没有可引用的行」对 r8 批 4/4 的 10 个栏目**全部**为真（该 topic 下确有 25 行材料），
    # 而那 10 栏里 9 栏在研究侧是 `blocked`。那样的读数会把「整节有材料」重新包装成「本批有行
    # 可用」——是反着的暗示。因此请求面只摆事实，判断留给模型；这条把「不得写成逐 aspect 布尔」
    # 钉在**同一处**（真回归时改了措辞也会被它拦住）。
    for token in ("has_usable_row", "unbacked_aspect_ids", "batch_has_usable_rows"):
        check(token not in system,
              f"v10 不得给出逐 aspect 的「有没有可引用行」布尔读数 {token!r}："
              "那个粒度在 Pack 侧没有真值来源（材料只有 topic 归属），"
              "硬造一个会把「整节有材料」读成「本批有行可用」")
    # 「缺失陈述不是缺口」这条实质：缺口由系统按检索轨迹与 Contract 判定，不由模型自述生成。
    check("正式缺口由系统按检索轨迹与 Contract 判定" in system,
          "v10 必须写明正式缺口的判定权在系统（不由模型的「未取得」文字生成）")
    # 分批的输入面仍是**完整**的：本块只声明本批的范围，不裁剪材料/事实目录（§二 的立场）。
    check("完整、精确" in system and "切分，不是材料面的裁剪" in system,
          "v10 不得收回「分批只切请求面、材料面不裁」这条立场（本块是**额外**声明的范围读数）")
    # `proposals-15`（`pw-21`，指令 D §二·三条日期轴）的**一般经营描述落笔纪律**证据：这些
    # 措辞必须出现在 v11、且直接前驱 v10 那份正文里一个字都没有——否则这轮「版本递增」只是
    # 换了名字。这一轮的行为面是**真**的：v10 只写了「路径 B 候选里不得含期间」与「较旧材料
    # 不得写成当前状态」，没有回答「**较新材料**的一般经营描述应当怎么落笔」——那正是 r8 现场
    # 读起来像「一直如此」的那一处。v11 把归属语判归系统、把「当前状态」的举证责任写清、
    # 并把新旧实质差异与新闻日期的两条边界补上。
    v10_text = superseded_text["pack_section_writer_proposals_v10"]
    for token in V15_ONLY_TOKENS:
        check(token in system,
              f"v11 资产必须写明本轮的三条日期轴纪律 {token!r}（真版本递增，不是改名）")
        check(token not in v10_text,
              f"前置条件：{token!r} 必须只在 v11 出现——v10 里也有了，说明这轮差异并不存在")
    # v11 **不得**放宽高风险门：归属语挪到系统渲染，恰恰是为了让「路径 B 候选不得含期间」
    # 这条继续成立，而不是靠让模型自己写日期来「说明来源」。
    check("路径 B 候选里不得含期间" in system,
          "v11 不得收回「路径 B 候选里不得含期间」：本轮的解法是把归属语挪到系统渲染的读者面，"
          "不是给写者开一条自造日期/期间的路")
    # 归属语的**作者**必须是系统：这条把「模型不写、系统渲染」钉在同一处，防止将来有人把
    # 归属语又挪回提示词里当「写作要求」。
    check("由**系统**在门后确定性地附在正文旁边" in system,
          "v11 必须写明归属语由系统在门后确定性附加（作者是系统，不是写者）")
    # `proposals-16`（`pw-22`，M930-3 r9 后返修 B）的**草稿闭合读法**证据：这些措辞必须出现在
    # v12、且直接前驱 v11 那份正文里一个字都没有。这一轮的行为面是**真**的：v11 的
    # 「草稿里声明的原子键必须与候选集一一对上」+「同一句话不要在两个单元里各写一遍」合起来
    # 读成「一条候选恰好一处表达」，而材料驱动的草稿本来就是同一件事在两份材料里各写一次
    # （r9 四批保存字节的现场）——照 v11 写就会被自己的闭合核对拒掉。
    v11_text = superseded_text["pack_section_writer_proposals_v11"]
    for token in V16_ONLY_TOKENS:
        check(token in system,
              f"v12 资产必须写明本轮的草稿闭合读法 {token!r}（真版本递增，不是改名）")
        check(token not in v11_text,
              f"前置条件：{token!r} 必须只在 v12 出现——v11 里也有了，说明这轮差异并不存在")
    # 键**集合**一一对上这条断言**不许**被删掉：多 occurrence 放宽的是「一条候选能出现几次」，
    # 不是「草稿声明的键要不要有对应候选 / 候选要不要有草稿声明它」。缺了它，旁路塞进来的候选
    # 与没有原子账的草稿句都会失去拦截点。
    check("一一对上" in system,
          "v12 不得删掉「草稿声明的原子键集合必须与候选键集合一一对上」这条断言"
          "（本轮放宽的是 occurrence 数，不是键集合相等）")
    # 放宽**不得**变成「随便挂在哪份来源上都行」：逐 occurrence 的核对面必须写明。
    for token in ("材料轴比材料行", "事实轴比事实行"):
        check(token in system,
              f"v12 必须写明多 occurrence 的核对面 {token!r}（否则多写一遍就等于免检）")
    check("来源角色" in system and "来源身份/报告期" in system,
          "v12 必须写明逐 occurrence 核对的是材料 ID / 来源身份（报告期）/ 来源角色")
    # 旧读法**必须**在 v11 里仍然读得到（本轮修的就是它），否则「修了什么」无从对账。
    check("同一句话不要在两个单元里各写一遍" in v11_text,
          "前置条件：v11 必须就是那句「同一句话不要在两个单元里各写一遍」的资产"
          "（本轮要修的对象）")
    # 别名可展开的**前提**：请求侧必须逐行声明这批 ref（`support_refs`）与两张表上的 `ref`
    # 一一对应。缺了声明，「短别名」就只是模型自己写的一串字符。
    check("support_refs" in system and "support_refs" not in v6_text,
          "v7 必须声明请求侧的支撑选项声明块 support_refs（它是别名可展开的前提）")
    check("逐行声明" in system or "把这两张表逐行声明出来" in system,
          "v7 必须讲清 ref 是**请求里逐行声明**的（不是模型自造的编号）")
    for token in ("authority_kind` / `container_id` / `fact_id` / `material_id`",
                  "确定性展开", "会被直接拒绝"):
        check(token in system,
              f"v7 必须写明身份字段由系统展开、自己写即被拒（缺 {token!r}）")
    # v6 的「每批重复承担整节」必须在 v7 里被**收回**：这正是本轮收窄的那一面。
    check("不受本批 aspect 范围限制" not in system,
          "v7 不得保留 v6 那句「`narrative_draft_units` 与 `follow_up_needs` 不受本批 aspect "
          "范围限制」——本轮正是把它改成本批结算")
    check("不受本批 aspect 范围限制" in v6_text,
          "前置条件：v6 必须就是那句「不受本批 aspect 范围限制」的资产（本轮收窄的对象）")
    # 分批**不得**被写成材料面的裁剪：材料/权威/必需事实/缺口四份仍是完整的。这是 §二
    # 「每批仍可访问完整、精确的 Writer 材料集合」在提示词上的落点。
    for token in ("authority_facts", "materials", "must_use_facts", "完整、精确"):
        check(token in system,
              f"v6 必须声明分批只切请求面（{token!r} 仍在完整输入面里）")
    # 「路径 A 可为空」与「每栏目至少一条路径 A」这对自相矛盾的要求，在 v4 里**并存**、
    # 在 v5 里被重写成按栏目可用内容分档的一条口径。这是 P2 要修的那处提示词冲突本身。
    old_conflict = "- **路径 A 为空不妨碍合法的路径 B**"
    check(old_conflict in v4_text,
          f"前置条件：v4 必须就是那处自相矛盾的资产（缺 {old_conflict!r}）")
    check("每一个栏目都应当至少有一条路径 A" in v4_text,
          "前置条件：v4 必须还带着「每一个栏目都应当至少有一条路径 A」这句绝对要求")
    check("每一个栏目都应当至少有一条路径 A" not in system,
          "v6 不得保留那处自相矛盾的绝对要求（否则冲突没有被真正统一）")
    check("真实适用范围" in system and "所有栏目" in system,
          "v6 必须把「每栏至少一条路径 A」的适用范围显式收窄（说清它管的是哪一类栏目）")
    # v2 的输入面证据仍在（v6 是它的后继，不得把它去掉）。
    for token in ("`text`", "`structured`", "payload 哈希", "真实正文"):
        check(token in system,
              f"v6 必须保留 v2 的输入面声明 {token!r}（后继资产不得悄悄收回输入面）")

    # ============================================================ §5c 来源角色的期间纪律
    # M930-3 返修 ④（`pw-14`）：`source_role` 进输入面 + 一条期间纪律。这是 v7 资产内的
    # `proposals-10` 修订，行为面证据必须**只在**这一版出现——v6 里也有了，说明这轮修订
    # 没改行为面；v7 里没有，说明规则只停在代码里而没到生成器那一侧。
    for token in ("source_role", "current_state_source", "history_and_conflict_source",
                  "topic_participating_source", "来源角色"):
        check(token in system,
              f"v7 资产必须把材料行的来源角色读法写给生成器（缺 {token!r}）："
              "角色不随行到达，这条期间纪律在生成器那一侧不可执行")
        check(token not in v6_text,
              f"前置条件：{token!r} 必须只在 v7 出现（v6 里也有了，说明这轮修订并不存在）")
    # 出口必须是**可执行**的那两条：不得被写成「补一个期间词」——路径 B 上期间表面本来
    # 就是未授权表面（`PW._path_b_high_risk_surfaces` 不看材料正文里有没有），
    # 把它写成出路就是在授权面之外另开一条造表面的路。
    check("不要**给它补一个年份或期间词" in system or "不要**给它补一个年份" in system,
          "资产必须写明「不得靠补期间词来满足这条纪律」（那正是授权面禁止的动作）")
    check("同等资格" in system,
          "资产必须写明其他系列材料按主题**同等资格**参与（否则会把跨系列材料误当旧材料绕开）")
    check("`null`" in system,
          "资产必须写明 `source_role` 为 `null` 的材料不在文档系列轴上（本条限制不适用）")

    # ============================================================ §6 P19 Claim 语义核验资产
    # §16.10 第 42 项：资产名/字节/hash 漂移但仍发起 LLM 调用，或决定未绑定 prompt/model/call id
    # —— 资产侧的断言落在这里，调用次数与决定绑定的断言落在
    # `evals/test_demo_claim_entailment_gate.py`。
    if not ENTAILMENT_ASSET_PATH.exists():
        failed += 1
        details.append(f"FAIL 缺资产 {ENTAILMENT_ASSET_PATH.name}（P19 是 new 文件，必须存在）")
    else:
        ent_system = _read(ENTAILMENT_ASSET_PATH)
        check(CEE.CLAIM_ENTAILMENT_PROMPT_ASSET == ENTAILMENT_ASSET_NAME,
              f"登记的资产名必须等于精确路径里的资产名（实际 {CEE.CLAIM_ENTAILMENT_PROMPT_ASSET!r}）")
        check(CEE.CLAIM_ENTAILMENT_PROMPT_REVISION == ENTAILMENT_ASSET_REVISION,
              f"登记的修订号必须是 {ENTAILMENT_ASSET_REVISION!r}"
              f"（实际 {CEE.CLAIM_ENTAILMENT_PROMPT_REVISION!r}）")
        check(CEE.CLAIM_ENTAILMENT_PROMPT_VERSION
              == f"{ENTAILMENT_ASSET_NAME}@{ENTAILMENT_ASSET_REVISION}",
              f"prompt 身份必须是 `<资产名>@<revision>`"
              f"（实际 {CEE.CLAIM_ENTAILMENT_PROMPT_VERSION!r}）")
        check(PW.prompt_asset_fingerprint(ent_system) == CEE.CLAIM_ENTAILMENT_PROMPT_SHA256,
              "登记的正文指纹必须等于资产当前正文的指纹（正文改动必须同时改登记值）")
        check(CEE.CLAIM_ENTAILMENT_RULES_VERSION == ENTAILMENT_ASSET_REVISION
              == NS.CLAIM_ENTAILMENT_RULES_VERSION,
              "rubric 版本必须与资产修订号同源，且字面量唯一在 wire 模块")
        check(llm.load_prompt(CEE.CLAIM_ENTAILMENT_PROMPT_ASSET) == ent_system,
              "暴露名必须经 `llm.load_prompt` 解析到该精确路径的同一份文本")
        ent_decls = PW._PROMPT_DECL_RE.findall(ent_system)
        check(len(ent_decls) == 1,
              f"资产自报身份必须**恰好一处**（实测 {len(ent_decls)} 处）")
        ent_declared = (f"{ent_decls[0][0]}@{ent_decls[0][1]}" if len(ent_decls) == 1 else "")
        check(ent_declared == CEE.CLAIM_ENTAILMENT_PROMPT_VERSION,
              f"资产自报身份 {ent_declared!r} 必须等于登记身份 "
              f"{CEE.CLAIM_ENTAILMENT_PROMPT_VERSION!r}")
        # 验签函数必须真的覆盖正文（不是只比对自报名）。
        try:
            CEE.verify_claim_entailment_prompt_asset(ent_system)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL 登记资产必须通过验签（抛 {type(exc).__name__}: {str(exc)[:120]}）")
        else:
            passed += 1

        def _ent_mutation(msg: str, *, needle: str, mutate) -> None:
            mutated = mutate(ent_system)
            check(mutated != ent_system, f"前置条件：该变异必须真的改动了正文（{msg}）")
            expect_error(lambda: CEE.verify_claim_entailment_prompt_asset(mutated),
                         CEE.ClaimEntailmentError, msg, needle=needle)

        _ent_mutation("正文改一个字（资产名/修订号照抄）必须被拒",
                      needle="正文指纹", mutate=lambda t: t.replace("原子语义核验器",
                                                                   "原子语义核验机"))
        _ent_mutation("只加一个尾随换行也必须被拒",
                      needle="正文指纹", mutate=lambda t: t + "\n")
        _ent_mutation("自报修订号漂移必须被拒（哈希之外的第一道身份核对）",
                      needle="自报", mutate=lambda t: t.replace("cer-4", "cer-9"))
        _ent_mutation("资产未自报身份必须被拒",
                      needle="未自报", mutate=lambda t: "你是核验器（没有自报身份）")

        # §三 / ⑤：cer-2 与 cer-3 的**新行为面**必须写在资产里，且各自只在**自己那一版**里
        # （旧版不原位修改，只作只读基线）。原子性判据必须是语义判据，不得被写成「出现逗号即拒」
        # 的字符串捷径；`cer-3` 的镜像分支也不得被写成「看到代理口径就放行」的字符串捷径。
        check(ENTAILMENT_SUPERSEDED_PATH.exists(),
              f"被取代的 {ENTAILMENT_SUPERSEDED_PATH.name} 必须仍在原位（删除也是原位修改）")
        if ENTAILMENT_SUPERSEDED_PATH.exists():
            ent_v1 = _read(ENTAILMENT_SUPERSEDED_PATH)
            check(PW.prompt_asset_fingerprint(ent_v1) == ENTAILMENT_SUPERSEDED_SHA256,
                  "被取代的 cer-1 资产正文指纹必须等于基线（旧版只读，不得原位修改）")
            v1_decls = PW._PROMPT_DECL_RE.findall(ent_v1)
            check(bool(v1_decls)
                  and f"{v1_decls[0][0]}@{v1_decls[0][1]}"
                  == f"claim_entailment_evaluator_v1@{ENTAILMENT_SUPERSEDED_REVISION}",
                  "被取代的 cer-1 资产必须仍自报自己的身份（不得被改写成 cer-3）")
            check("non_atomic_claim" not in ent_v1,
                  "前置条件：`non_atomic_claim` 必须只在 cer-2 起出现——cer-1 里也有了，"
                  "说明这轮新增的判定并不存在")
        # `cer-2` 的冻结基线：本批只**新增** cer-3，不改 cer-2 一个字（历史判定必须可复现）。
        check(ENTAILMENT_CER2_PATH.exists(),
              f"{ENTAILMENT_CER2_PATH.name} 必须仍在原位（它承载 r4 的 24 条历史判定结论）")
        if ENTAILMENT_CER2_PATH.exists():
            ent_v2 = _read(ENTAILMENT_CER2_PATH)
            check(PW.prompt_asset_fingerprint(ent_v2) == ENTAILMENT_CER2_SHA256,
                  "cer-2 资产正文指纹必须等于冻结基线（旧版只读，不得原位修改）")
            v2_decls = PW._PROMPT_DECL_RE.findall(ent_v2)
            check(bool(v2_decls)
                  and f"{v2_decls[0][0]}@{v2_decls[0][1]}"
                  == f"claim_entailment_evaluator_v2@{ENTAILMENT_CER2_REVISION}",
                  "cer-2 资产必须仍自报自己的身份（不得被改写成 cer-3）")
            check("authority_fact_mirror" not in ent_v2,
                  "前置条件：`authority_fact_mirror` 必须只在 cer-3 出现——cer-2 里也有了，"
                  "说明这轮新增的判定并不存在")
        # `cer-3` 的冻结基线：本批只**新增** cer-4，不改 cer-3 一个字（r6 的历史判定必须可复现）。
        check(ENTAILMENT_CER3_PATH.exists(),
              f"{ENTAILMENT_CER3_PATH.name} 必须仍在原位（它承载 r6 的 24 条历史判定结论）")
        if ENTAILMENT_CER3_PATH.exists():
            ent_v3 = _read(ENTAILMENT_CER3_PATH)
            check(PW.prompt_asset_fingerprint(ent_v3) == ENTAILMENT_CER3_SHA256,
                  "cer-3 资产正文指纹必须等于冻结基线（旧版只读，不得原位修改）")
            v3_decls = PW._PROMPT_DECL_RE.findall(ent_v3)
            check(bool(v3_decls)
                  and f"{v3_decls[0][0]}@{v3_decls[0][1]}"
                  == f"claim_entailment_evaluator_v3@{ENTAILMENT_CER3_REVISION}",
                  "cer-3 资产必须仍自报自己的身份（不得被改写成 cer-4）")
            check("authority_fact_mirror" in ent_v3 and "match_count == 1" in ent_v3,
                  "前置条件：镜像分支必须自 cer-3 起存在——cer-3 里没有，说明这轮修的并不是它")
            for token in CER4_ONLY_TOKENS:
                check(token not in ent_v3,
                      f"前置条件：cer-4 的读视图措辞 {token!r} 必须只在 cer-4 出现——"
                      "cer-3 里也有了，说明这轮「版本递增」只是换了名字")
        for token in CER4_ONLY_TOKENS:
            check(token in ent_system,
                  f"cer-4 资产必须写明镜像读数在**标点归一读视图**上比较：缺 {token!r}——"
                  "否则真实 run r6 里 match_count 恒为 0 的缺陷没有被真正描述")
        check("句读标点" in ent_system and "实词" in ent_system,
              "cer-4 资产必须写明边界：只动句读标点，数字/单位/期间/主体/口径限定语等实词"
              "逐字参与（否则「归一会放宽实词」这一读法在资产里没有反面约束）")
        check("non_atomic_claim" in ent_system,
              "cer-3 资产必须给出原子性拒绝码 non_atomic_claim")
        check("原子性先于蕴含" in ent_system,
              "cer-3 资产必须写明原子性**先于**蕴含（否则两个断言会被当作一个命题判蕴含）")
        check("出现逗号不是拒绝" in ent_system,
              "cer-3 的原子性判据必须是**语义**判据：资产必须显式排除「出现逗号即拒」这种"
              "字符串捷径（否则模型会把含逗号的单断言也判成非原子）")
        # ⑤ 的镜像分支：只在「逐字等于**恰好一条**权威事实自己的文本」时成立，且必须写明
        # 「> 1 也不放行」，否则它会退化成一条「碰巧等于某条就放过」的旁路。
        check("authority_fact_mirror" in ent_system
              and "match_count == 1" in ent_system,
              "cer-3 资产必须写明镜像分支成立的条件是 `authority_fact_mirror.match_count == 1`")
        check("match_count > 1" in ent_system or "match_count == 0" in ent_system,
              "cer-3 资产必须写明 0 条与多条时**回到**语义判据（否则镜像分支成了旁路）")
        check("口径" in ent_system and "同一个断言" in ent_system,
              "cer-3 资产必须讲清为什么：权威自己附在同一句后的口径限定语是同一个断言的一部分")

        # AST 静态核对：P9 对 prompt 资产的**唯一**加载点就是登记资产名。
        ent_sites = _load_prompt_call_sites(REPO / "sections" / "claim_entailment_evaluator.py")
        check(len(ent_sites) == 1,
              f"语义核验器必须只有一处 `load_prompt` 调用（实测 {len(ent_sites)} 处）")
        if ent_sites:
            ent_args = ent_sites[0].args
            check(len(ent_args) == 1 and isinstance(ent_args[0], ast.Name)
                  and ent_args[0].id == "CLAIM_ENTAILMENT_PROMPT_ASSET",
                  "唯一加载点的第一个实参必须是 `CLAIM_ENTAILMENT_PROMPT_ASSET`")

        # 输出契约 ↔ 解析层词汇表：资产里列出的原因码闭集必须**逐字**等于 wire 的闭集。
        listed = {code for code in NS.ENTAILMENT_REJECTION_REASONS if code in ent_system}
        check(listed == set(NS.ENTAILMENT_REJECTION_REASONS),
              f"资产必须逐条列出全部封闭原因码（缺 {sorted(set(NS.ENTAILMENT_REJECTION_REASONS) - listed)}）")
        for token in ("entailed", "rejected"):
            check(f'"{token}"' in ent_system, f"资产必须给出 verdict 取值 {token!r}")
        ent_keys = _all_keys(json.loads(_fenced_json(ent_system)))
        forbidden_ent = sorted((set(FORBIDDEN_OUTPUT_KEYS) - {"claim_id"}) & ent_keys)
        check(not forbidden_ent,
              f"语义核验输出面里不得出现定稿对象/决定字段（实测 {forbidden_ent}）")
        check(ent_keys <= {"verdict", "reason_code", "rationale"},
              f"输出样例的键必须恰是 verdict/reason_code/rationale（实测 {sorted(ent_keys)}）")
        for token in ("禁止检索", "禁止联网", "context", "只降级不升级"):
            check(token in ent_system, f"资产必须显式声明该纪律：{token}")
        # current Claim entailment 不得复用 legacy 章节级资产（P12/P19 的资产边界）。
        check(SECTION_EVALUATOR_ASSET not in ent_system
              and CEE.CLAIM_ENTAILMENT_PROMPT_ASSET != SECTION_EVALUATOR_ASSET,
              "语义核验不得复用 legacy 章节级评估资产")
        check(ent_system != system,
              "两份新资产必须是两份不同文本（同文本换名不构成新资产）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

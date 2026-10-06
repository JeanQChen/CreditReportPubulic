"""§0.20 第一步：Pack → **带逐句引用的自然正文**的新写作接口（`cwm-2` / `cw-1` / `cwp-1`）。

本模块回答的问题是旧的 `ClaimCandidate → 聚合绑定 → 逐候选蕴含 → AcceptedSupportBinding
→ Claim 拼句` 链**没有**回答的那一个：「读者读到的这一段自然文字，每一句各自是从本次 Pack 的
哪一条材料（或哪一条具名权威事实）长出来的？」

旧链把「正文」表达成**由若干条原子 Claim 反向拼装出来的句子**：写作者先提候选、门再判候选
能不能绑、句子只在全部候选都过门之后才被允许存在。于是三件事被绑死在一根线上——(a) 模型有
没有提出某条候选，(b) 那条候选过没过聚合门与蕴含门，(c) 读者能不能看到那半句话。任何一环
fail-closed，正文里对应的**那一部分**就整块消失，而消失的理由沉淀在门记录里、不沉淀在句子上。

§0.20 把这三件事拆开，本模块只负责其中**写作**这一段：

* **输入**是既有的东西，一个字节都不新造：`VerifiedPackSet` 的精确材料清单（
  `WriterMaterialManifest`，`wmm-2`）与已解析正文上下文（`WriterMaterialContext`，
  `wmctx-1`），加上权威事实读视图（`AuthorityFactEntry`，四条 `authority_kind` 各保各的身份）。
* **输出**只有「按小节组织的自然段落 + 逐句引用 + 缺口 + 补件需求」。**不要求**模型同步产出
  原子 Claim、支撑提案或审核决定——那三样是旧链的中间物，不是读者要看的东西。
* **每一条正文句都引本清单里的具名对象**（材料键 `m01…` 或事实键 `f01…`），引用键的命名空间
  由本清单独占，模型无法引用本次输入之外的东西：键不存在就是**结构违规**，在解析期当场抛出，
  不进入下游。

三套身份的边界在本模块是**可核验**的，不是约定：

* 材料键 `m…` 只能落在 `topic_pack`（`ResearchMaterial` 是 Packet 侧的正式材料边界）；
* 事实键 `f…` 四选一 `authority_kind`，`fact_id` 按该 kind 的 branch-specific 字段取值
  （`external_snapshot` 时它是 `external_fact_id`）——与 `NS.fact_provenance_ref` 同一口径，
  因此财务口径 / 附注 / 外部快照**不会**被伪装成一条普通 Pack 材料；
* 两轴的键**前缀不同**（`m` / `f`），任何一侧都产不出另一侧的取值。

本模块**不**检索、**不**联网、**不**计算、**不**回写 Pack；需要更多材料只能发
`CitedFollowUpNeed`（只登记，本期不执行——面试版只读展示）。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol, Sequence

from harness import topic_schema as TS
from sections import narrative_schema as NS

__all__ = [
    "CITED_WRITER_MANIFEST_SCHEMA_VERSION",
    "CITED_WRITER_DRAFT_SCHEMA_VERSION",
    "CITED_WRITER_POLICY_VERSION",
    "CITED_WRITER_PROMPT_ASSET",
    "CITED_WRITER_PROMPT_REVISION",
    "CITED_WRITER_PROMPT_VERSION",
    "CITED_MATERIAL_KEY_PREFIX",
    "CITED_FACT_KEY_PREFIX",
    "CITED_ADOPTION_DISPOSITIONS",
    "CITED_GAP_REASONS",
    "CITED_GAP_REASON_ASSIGNMENTS",
    "CITED_SOURCE_CLASS_DERIVATIONS",
    "CITED_FOLLOW_UP_REQUIREDNESS",
    "CitedWriterError",
    "CitedSubsectionSpec",
    "CitedMaterialEntry",
    "CitedFactEntry",
    "CitedWriterInputManifest",
    "CitedSentence",
    "CitedParagraph",
    "CitedSubsection",
    "CitedProseGap",
    "CitedFollowUpNeed",
    "CitedProseDraft",
    "MaterialAdoptionRecord",
    "CitedProseResult",
    "CitedProseOutcome",
    "CitedProseClient",
    "LlmCitedProseClient",
    "truncated_call_record",
    "cited_material_key",
    "cited_fact_key",
    "citation_key_axis",
    "cited_input_identity_body",
    "draft_identity_body",
    "derive_draft_id",
    "derive_gap_id",
    "derive_adoption_id",
    "derive_follow_up_need_id",
    "manifest_spec_for_subsection",
    "registered_material_keys",
    "registered_fact_keys",
    "registered_source_keys",
    "withheld_numeric_fact_keys",
    "writable_fact_keys",
    "requirement_lines_by_aspect",
    "citable_columns",
    "assign_gap_reason",
    "derive_expected_source_classes",
    "derive_prose_revision",
    "validate_citations",
    "load_cited_writer_prompt",
    "declared_prompt_identity",
    "build_cited_prose_messages",
    "build_cited_writer_input",
    "build_cited_prose_request",
    "parse_cited_prose",
    "parse_cited_prose_with_normalization",
    "empty_shell_context",
    "derive_material_adoptions",
    "write_cited_section",
]

#: 输入清单 wire 版本。字段增删/语义变化必须改名，旧对象只经 legacy reader 只读回放。
#:
#: `cwm-2`：材料行增加 `document_id`（来源文档身份，「旧来源不得写成当前状态」那条逐句判据
#: 的输入轴）、`structured_view`（表材料的**结构化**读视图：逐句格级核对只认它，不认压平的
#: 正文）与 `content_qualification`（内容形态：勾选表单行不是叙述正文）。后两者由
#: `reading_view_fingerprint` / `payload_hash` 钉住（`wmctx-1` 已如此），作用域**不进**
#: `identity_body`——它们是正文的读法，不是新的身份轴；`document_id` 是身份轴，进身份体。
#: `cwm-2` 从未落盘过任何产物（本批不含真实 run），因此不存在需要 legacy reader 的旧对象。
#:
#: `cwm-3`：小节行增加 `declared_aspect_id`（本小节**声明**要写的那个 Contract 栏目），材料行
#: 增加 `aspect_ids`（该材料在 Pack 侧**登记**归属的栏目）。两者都是**身份轴**，进身份体：
#: 逐句栏目核对要比的就是这两个值，而登记不同的两份清单不得共用同一个 id。缺了这一对字段，
#: 「引用存在、字面逐字相同」就会被读成「本栏目已被覆盖」——本节正是为拦这一种错而升版。
#: 如实记下影响：`cwm-2` 的清单身份因此全部作废，历史 `cited_input_manifest.json` 只作对照。
#:
#: `cwm-4`：清单增加一个可选的 `presentation_routing`（**呈现层**路由声明，财务节才有）。
#: 它进身份体，理由与 `cwm-3` 同一条：换一份路由就是换了一份输入，两份清单不得共用同一个 id。
#: 它**不是**事实权威，也**不**写进任何 `aspect_ids`——`cwm-3` 的登记轴由权威自己填，本字段
#: 是另一条轴（谁声明它是呈现层，见该字段自己的注释）。空 `None` 时身份体逐字节沿用 `cwm-3`
#: 的取值方式（多出来的是键本身，不是内容），因此没有路由的节（公司节）只是换了一个版本号。
#:
#: `cwm-5`：小节行增加 `allowed_source_classes`（**该 Contract 栏目允许的检索来源类**，逐字取自
#: 冻结 Contract 的 `evidence_requirements` 登记，由组合根投影，**不由本模块解释 Contract**）。
#: 它进身份体，理由与 `cwm-3` / `cwm-4` 同一条：允许来源不同的小节不是同一个写作要求，两份
#: 清单不得共用同一个 id。它**不**授权任何材料，也**不**保证那一类来源本次真的在清单里——它是
#: 「补件该往哪一类来源去找」的唯一依据（见 :func:`derive_expected_source_classes`），
#: 缺了它，补件来源类就只能由生成器自由发明。如实记下影响：`cwm-4` 的清单身份全部作废，
#: 历史 `cited_input_manifest.json` 只作对照。
#:
#: `cwm-6`：小节行上的 `declared_aspect_id: str` 换成 `declared_aspect_ids: tuple[str, ...]`，
#: 并且**没有单数版**——旧字段直接移除。这是一次**语义收窄的对偶**：`cwm-3` 假设「一个小节
#: 恰好声明一个 Contract 栏目」，而冻结 WritingSpec 的小节轴**不是**这样（18 个
#: `company_business*` aspect 全部归属 `co-h4`，6 个 `fin_solvency*` 全部归属 `fin-h2`）。
#: 上游据此把写作小节从「一个 aspect 一节」改成「一个 WritingSpec 小节一节」（`cwm-6` 的
#: `subsection_id` 因此变成 `co-h4` / `fin-h2` 这类稳定短名），小节**覆盖**的栏目就成了一个
#: 集合。留一个「取集合首元」的兼容读法，等于让下游继续把「只声明了一条」与「声明了十八条」
#: 当成同一件事，因此不设。同时新增 `CitedParagraph.aspect_ids`（**段级**声明，`cw-4` 同步）：
#: 小节集合在覆盖十几栏时没有分辨力，段自己声明的才是「这一段本来要写哪几栏」。两个字段都是
#: **身份轴**，进身份体。如实记下影响：`cwm-5` 的清单身份全部作废，历史
#: `cited_input_manifest.json` 只作对照。
#:
#: `cwm-7`：材料行增加 `span_continuity`（跨块切分读法：本片与相邻片同属一个 `span_id`、各自
#: 片序、本片在 span 本地区的精确区间、前后片 ref）。它是**派生**字段——原件是 payload 信封里
#: 已有的 `span_split`，本就进 `payload_hash`，因此与 `structured_view` / `content_qualification`
#: 同一纪律：**进序列化、不进 `identity_body`**（同一片原文换了个「看得见邻居」的读法，不是换了
#: 一份清单）。但它的**出现**改变了 wire 形状，按本文件第一条规则（字段增删必须改名）升版。
#: 如实记下影响：`cwm-6` 的清单身份全部作废，历史 `cited_input_manifest.json`（含 r30/d4）只作
#: 对照；`_material_from_dict` 仍按 dataclass 字段集读回，缺该键的旧行落到默认 `None`，只是旧行
#: 不再能与现行身份体对齐。
CITED_WRITER_MANIFEST_SCHEMA_VERSION = "cwm-7"
#: 正文草稿 wire 版本。
#:
#: `cw-2`：字段集**逐字未变**，变的是 `sentence_id` 的**含义**——`cw-1` 里它是「写入方自己给的
#: 编号」，`cw-2` 里它是「解析边界按 小节→段落→句子 稳定顺序分配的**全节唯一**编号」
#: （见 :mod:`sections.cited_reply_normalize`，`crn-1`）。两份产物在 JSON 上长得一模一样，
#: 意义却不同；不升版就会让人把 `cw-1` 的重复 ID 当成合格编号读。升版如实记下影响：
#: `cw-1` 的 `draft_id` 全部作废，历史 `cited_prose.json` 只作对照（仓内没有任何代码从盘上
#: 把旧草稿读回成对象，`from_dict` 是唯一读回口，它按版本号 fail-closed）。
#:
#: `cw-3`：**缺口理由**与**补件来源类**从「生成器自述」改成「系统按清单／Contract 判定」，
#: 生成器说的那两句原样留在审计字段里。逐条列清：
#:
#: * `CitedProseGap` 增加 `reason_assignment`（判定来源，封闭三值）与 `claimed_reason`
#:   （生成器原话，只作审计）；`reason` 改由 :func:`assign_gap_reason` 判定。
#:   身份体**逐字未变**（仍是 `subsection_id` / `requirement_text` / `reason` / `detail`），
#:   因此同一句缺口在「判定来源不同、结论相同」时身份不变——这是有意的：判定来源是这句话
#:   **怎么来的**，不是这句话**是什么**。
#: * `CitedFollowUpNeed.expected_source_class: str` 换成 `expected_source_classes: tuple[str,…]`
#:   （**由 Contract 栏目推导**，见 :func:`derive_expected_source_classes`），并增加
#:   `source_class_derivation` 与 `writer_claimed_source_class`（生成器原话，只作审计）。
#:   单值换成多元不是排版：`er_business` 一类要求本来就声明了**两个**允许来源类，用一个字符串
#:   表示它，只能靠「挑一个」，而挑出来的那一个会被下游读成「这一类足以结案」。身份体因此变了，
#:   `cw-2` 的 `cfun_` 身份全部作废；历史 `cited_prose.json` 照旧留盘对照，不重写。
#:
#: `cw-4`：`CitedParagraph` 增加 `aspect_ids: tuple[str, ...]`（**段级**栏目声明，出参侧）。
#: 它随 `cwm-6` 一起来：写作小节覆盖多条 Contract 要求之后，「本小节写了几段」对每一条要求
#: 都成立、因而对哪一条都没有信息量；「这一段本来要服务哪几条」才有分辨力。段级声明是
#: **组织**声明，不是**支撑**声明——空值合法（这一段只是承接或背景），但它**不放宽**任何判据：
#: 声明了服务某栏而所引来源在该栏没有登记时，`scp-5` 的 `aspect_attribution` 直接判硬错
#: （由 :func:`_check_paragraph_aspects` 在解析期先拦越界值）。它进身份体，理由与 `cw-3`
#: 一致：段的服务对象不同，草稿就不是同一份草稿。
CITED_WRITER_DRAFT_SCHEMA_VERSION = "cw-4"
#: 写作政策版本（引用键口径 + 「逐句必须引用」这条硬约束的版本）。
CITED_WRITER_POLICY_VERSION = "cwp-1"
#: prompt 资产名与修订号；`prompt_version` 是两者的组合（与既有 `*_vN@rev` 口径一致）。
#:
#: `cp-2` 相对 `cp-1` 只改了一件事：`sentence_id` 从「要求模型保证全节唯一」改成「给一个**非空
#: 临时** ID，最终全节唯一编号由程序按遍历顺序生成」（见 :mod:`sections.cited_reply_normalize`）。
#: 旧口径让 27 句共用 5 个模型 ID，再靠程序二次重编——提示词承诺的东西与程序实际依赖的东西不是
#: 同一件，这种不一致不算「实现细节」，它是**输入合同**的一部分，所以要升修订号、由
#: :func:`load_cited_writer_prompt` 把资产头声明的修订号与本常量钉在一起。
#:
#: `cp-3` 相对 `cp-2` 改了两件事，都属于**输入合同**而不是措辞润色：
#:
#: 1. 输出模板里**删掉** `follow_up_needs[].expected_source_class`——来源类别改由系统按该栏
#:    Contract 允许来源推导（原因见 :data:`CITED_WRITER_DRAFT_SCHEMA_VERSION` 的 `cw-3` 段）。
#:    留着它等于继续请生成器发明一个会被下游当作「取材指令」的字段。
#: 2. 硬约束里补上「先答本栏 Contract 问题、再选材料」与几条**业务**纪律（按业务的收入 ≠ 按
#:    地区的收入；采购／生产／销售是三个过程；跨小节同一事实只写一次；旧年材料不得无年份地
#:    冒充当前态；发行人自评不等于客观结论）。它们都只谈**怎么写**，不含任何公司专例。
#:
#: `cp-4` 相对 `cp-3` 只加了一件事，同属**输入合同**：输入说明与硬约束里显式写出
#: `presentation_routing` 的**用法**。这份声明从 `cwm-4` 起就摆在请求面上
#: （见 :func:`build_cited_prose_request`），但提示词一个字都没说它是什么、该怎么用——
#: 「拿流动比率去答净资产水平」这一类错栏，入口正是「收到了路由、却没有被告知按路由写作」。
#: 新增文字只描述**字段语义与落栏纪律**（一条事实只能进它自己那一栏；没有路由的事实不得被
#: 塞进任何一栏；`column_gaps` 已声明无指标的栏目不得用别处指标填），并明确**路由不构成数字
#: 权威**，因此它既不改变第 4 条的从严口径，也不含任何公司专例。
#: `cp-5` 相对 `cp-4` 改的也是**输入合同**，而且改的是同一类东西的反面：`cp-4` 说明「路由怎么
#: 用」，`cp-5` 说明「写不出来时产出**什么**」。真实 r28 暴露的缺陷是**提示词自己**给出的出口：
#: 旧文（第 10 条第三小点）写着「正文里就准确地说本次未取得合格数字」，而第 2 条要求每句话都
#: 引用——两句直接冲突，模型选了前者，于是 23 句里有 7 句是零引用的「本次未取得…」。这类句子
#: 在机械层必然判 `uncited_sentence` 硬错误，但**它不是模型乱答**：它是照第 10 条写的。修法不是
#: 加一道门拦住它，而是把那个出口拆掉：
#:
#: 1. 第 10 条第三小点重写——缺合格数字事实写成 `gaps`，**不再**往正文里补一句「本次未取得」；
#: 2. 第 7 条补上**空段落**这一档：没有可支持的正文时 `paragraphs` 写 `[]` 是**合格**产出，
#:    并明确「本次未取得／暂未披露」这类自述只能在 `gaps` 里；
#: 3. 第 2 条补一句「这条没有例外」，把「零引用句」与「缺口」的分界写在引用规则自己身上；
#: 4. 第 9 条补两条**栏目分工**纪律（销售模式问的是怎么卖、不是扩产；采购模式与成本竞争力
#:    的同一段描述只写一次），对着 r28 里「用生产扩产充销售模式」与跨小节重复那两处；
#: 5. 输出说明里点明 `paragraphs` 可为空数组。
#:
#: 新增文字全部只谈**产出形态与栏目分工**，不含任何公司、年份、页码或答案关键词。
#:
#: `cp-6` 相对 `cp-5` 改的是**小节轴本身**，因此是这一组修订号里改动面最大的一次。真实 r30
#: 暴露的缺陷是结构性的：写作小节被造得比冻结 WritingSpec 更细（一个 Contract aspect 一个
#: 小节 ⇒ 公司节 18 个小节），而本提示词第 1 条又写着「正好一个小节一条」——两者叠加，18 条
#: 要求就必然被写成 18 段浅话，同一份材料被反复拿来填空。修法是先把小节轴收回到 WritingSpec
#: 的小节（`cwm-6`），再改提示词：
#:
#: 1. `subsections` 的说明改为「一个小节覆盖**多条** Contract 要求，`declared_aspect_ids`
#:    是它覆盖的那些栏目身份」，并点明「一个小节覆盖多条是常态」；
#: 2. 第 1 条补一句：一个小节对应输入里的**一行**，不是一条要求——不要一条要求写一个独立小节；
#: 3. 第 3 条重写为「先做**整节内容计划**，再写连贯自然段落；**不是**一条要求一个段落」，
#:    并给出典型展开顺序与「信息密度比栏数重要」；
#: 4. 输出模板与说明里加入段级 `aspect_ids`（取值只能来自本小节的 `declared_aspect_ids`，
#:    越界会被当场拒绝；空数组合法），并写明「没有任何段落声明服务的栏目 = 漏答，要按第 7 条
#:    留缺口」；
#: 5. 第 5 条补**期间双向纪律**：不得把「报告期末／本期」自行写成具体年份，也不得反过来把
#:    年份抹成「报告期」；
#: 6. 第 9 条补一条**手段不是结果**：集中采购之类的**措施**不得被写成「因此形成了成本优势」。
#:
#: 新增文字仍然只谈**产出形态、叙述组织与栏目分工**，不含任何公司、年份、页码或答案关键词。
#:
#: `cp-7` 相对 `cp-6` 只改**一件事**：把跨块切分的相邻关系摆到生成器面前（材料行新增
#: `span_continuity`，`cwm-7`）。真实 r30/d4 暴露的是**读不到完整语境**：一段连续原文（如
#: 经营模式）跨 Evidence 块边界时，Pack 侧按块切成两片，manifest 顺序又是哈希序而非文档序——
#: 生成器看到的是「两条互不相干的来源」，于是要么只用其中一片、要么把两片当重复材料。修法是
#: **陈述关系而非合并来源**：`span_continuity` 声明本片与相邻片同属一个 `span_id`、各自是
#: 第几片、本片在 span 本地区的精确区间，并给出前后片的 ref；`text` / `locator` 仍是本片
#: 自己的，**不得**把两片伪造成一个 locator。提示词据此补一条：同一 span 的相邻片要连起来读
#: 成一个连续叙述，但引用仍按片、按各自的 locator。
#:
#: `cp-8` 相对 `cp-7` 也只改**一件事**，但改在**输入分层**上，不是措辞：呈现层声明里
#: `contract_display_tier=diagnostic_only` 的事实（冻结 Contract 的
#: `fin_solvency.interest_expense_proxy` 一类代理口径）**不进普通正文**。真实 r31 把代理
#: 利息保障倍数写进了偿债能力普通正文——那违反的是 `DESIGN_V2.md` §0.21 的展示角色边界，
#: 只在提示词里劝告是拦不住的（提示词不是判据）。因此本版做两件**可执行**的事：
#:
#: * 请求面把档位**摆在每一行上**（`authority_facts[].display_tier`，空串=查不到档），并单列
#:   `diagnostic_slot_fact_keys`；「这一条只能进诊断槽位」于是成为生成器看得见、下游读得出的
#:   取值，而不是一句需要模型自己去 `routes` 里对的话；
#: * 新增提示词第 12 条：被标为诊断槽位的事实**不得**出现在普通正文里（数值本身仍进诊断表）；
#:   原第 12 条（只输出那一个 JSON 对象）顺延为第 13 条，判据一字未改。
#:
#: 两层都只是**输入面**的改动；真正的门在 `scp-6` 的 `display_role` 轴（确定性、不经模型）。
#: 本版同时收紧了正文组织那几条（产品与应用 / 研发·采购·生产·销售各自有位、不重复应用场景、
#: 不把发行人优势自述写成已验证结论），理由见该资产自己的修订记录。
#:
#: `cp-9` 相对 `cp-8` 改**两件事**，都来自真实 r1 的逐句证据：
#:
#: 1. **把第 4 条与 `scp-6` 的 `numeric_qualification` 轴对齐**。`cp-8` 的第 4 条写着数字可以
#:    取自「`authority_facts` 的 `text` **或某份非表材料原文里的原值**」，而确定性门只认
#:    **合格事实（路径 A）或表格一格的格级来源**——材料原文里逐字出现**不**授权金额与比率。
#:    两处口径相反：真实 r1 的四句（s0010–s0013）正是照提示词写的（材料原文里确有那些
#:    `…万元`），随后被门判 `numeric_basis_not_qualified`。本版把第 4 条改成与门**逐字一致**
#:    的两分口径：金额 / 比率只认合格事实（或表材料某一格），普通材料原文出现**不**构成授权；
#:    不带金额单位的一般数量（`GWh` / `座` / `家` / `项`）仍可逐字引用材料原值，两类都不许换算。
#: 2. **按来源文档选材、本期优先**（第 3c 条）。`cp-8` 的材料行有 `source_role` 与 `locator`，
#:    但**没有** `document_id` 这一列可读的文档身份；真实 r1 因此把「当前业务」写成了一份
#:    募集说明书对年报的**转述**，而不是最新年报自己的主营业务正文，并把同一段「独立的研发、
#:    采购、生产和销售体系」在四段里反复充当支撑。本版把 `document_id` 摆进材料行（请求面
#:    可读），并新增第 3c 条：动笔前先按 `document_id` 分组、当前态取最新一期的主营业务正文、
#:    同一份材料只在一处充当支撑。另加第 12b 条，为「`materials` 为空、`authority_facts`
#:    非空」的财务 / 附注支规定按偿债分析的组织顺序（指标含义 → 期间变化 → 风险与限制），
#:    并禁止无出处的评价句。
#:
#: 新增文字仍只谈**产出形态、叙述组织、栏目分工与数字授权口径**，不含任何公司、年份、页码或
#: 答案关键词；第 4 条的收紧只是把已经在 `scp-6` 门里的判据**搬到提示词面前**，不新增判定。
#:
#: `cp-10` 相对 `cp-9` 只改第 3c 条**一处**，但这一处改的是**判据本身**，不是措辞：`cp-9` 的
#: 3c 把两件**不同轴**的事绑在了一句话里——「来源角色」（`source_role`：这份材料在本节充当
#: 当前态还是历史来源）与「事实适用期间」（这段原文讲的是哪一期）。于是它写出了两句站不住的
#: 话：(a)「取材顺序取**最新一期**那份文档」——`document_id` 只说明「这几行来自同一份文档」，
#: **不**说明那份文档是哪一期，把最新一期认成「`document_id` 排在哪」是从文档身份**推测**期间；
#: (b)「同一份材料只在一处充当支撑」——这条去重规则压的是**材料**，而真正会骗读者的重复是
#: **同一件事**跨段再说一遍；按材料去重会让采购、生产、销售各自**确实不同**的原文再也写不出来。
#: 本版把 3c 拆成两问：角色按 `source_role` 判、期间**只**从所引原文逐字读，并明说
#: `document_id` **不**携带期间、文档间的转述关系本次输入根本没给（不许自排先后）；去重规则
#: 改成「同一件**事**不跨段重复；同一份材料含**不同**事实时可以在不同段分别引用，每句仍指向
#: 它依据的那段原文」。数字底线（第 4 条）与其余各条一字未动。
#:
#: `cp-11` 对第 3c 条再做一处边界校正：`cp-10` 的「没有原文显式期间就留缺口」会把
#: **非数值一般经营描述**也拦掉，与 `AGENTS.md` §5 / `DESIGN_V2.md` §0.20 不符。本版允许
#: 当前态来源支持这种描述，同时不让作者猜具体年份、宣称永续有效或把 `report_as_of` 当事实期；
#: 金额、比率、具体时点及跨期变化的合格事实与期间底线不变。输入输出字段与硬核对器均未动。
#:
#: `cp-12` 按 cp-11 真实双节 run 的逐句硬错做四处**规则面**定点收紧（不动输入输出字段、不动
#: 硬核对器、不动数字底线）：
#: (a) 新增第 3d 条——段落的 `aspect_ids` 声明必须能被**所引来源的登记归属**核对到，把
#: 「名义挂甲栏、依据来自乙栏登记」这种段级硬错在写作面拦住；
#: (b) 第 3c / 第 4 条补齐 `topic_participating_source` 的定义与它的**非数字权威**边界
#: （cp-11 只定义了前两种角色，另一种角色在提示词里从未出现，材料正文里的金额 / 比率因此被
#: 抄成正文结论）；
#: (c) 第 7 条明确「自述缺口句只进 `gaps`」的段级写法与「未见披露」的越界边界，并区分
#: `manifest_partial_for_requirement` 与 `source_present_but_not_admissible`；
#: (d) 第 12b 条补财务小节「每一栏都要有明确去向」与「解释口径必须与已核实口径一致，
#: 否则只写走势」（速动比率口径以 `FORMULA_REVIEW.md` 为准）。
#:
#: `cp-13` 修掉 cp-12 自己留下的一处**规则冲突**（不新增门、不放宽任何一条轴、不动输入输出
#: 字段、不动硬核对器，`CITED_WRITER_PROMPT_REVISION` 之外没有别的代码面变更）。
#: cp-12 第 12b 条要求「期间变化：这条指标**怎么走**（升 / 降 / 平稳）」，而 `sentence_check`
#: 的高风险表面守恒轴判的是**逐字在场**：句子里出现的 `上升` / `下降` / `占比` / `反映`
#: 必须能在它**所引事实**的文本里原样找到。财务节的权威事实只给各期数值（「2024年末的有息
#: 负债为1,364.02亿元。」），两条规则因此正面冲突。cp-12 真实运行财务节 3 句硬错全部是这一
#: 冲突的产物；而 cp-11 之所以 0 硬错，只是因为它恰好挑了不在标记表里的 `降至` / `回升`——
#: 通过与否取决于同义词运气，不是规则可遵守。本版在 12b 写明：方向词只在所引事实 `text`
#: 逐字含它时才能用，否则改成只罗列数值的句式；变动类事实连符号一起逐字引用（不抽成
#: 「下降3.3个百分点」）。第 10 条补一句动笔前自查：`authority_facts` 为空时一个金额 / 比率
#: 都不许写，把 `numeric_basis_not_qualified` 的判据在写作面说清。
#: **口径限制：本次 cp-12 真实 run 验证的是 cp-12，本版未经任何真实运行验证。**
#:
#: `cp-14` 按 cp-12 真实公司节的逐句证据再做**规则面**补强（不加字段、不放宽任何一条轴、不动
#: 硬核对器；`CITED_WRITER_PROMPT_REVISION` 之外没有别的代码面变更），五处，都是「把判据在
#: 写作面说清」而不是「新增判定」：
#: (a) 第 3d 条补齐「段级错栏」的**处置动作**：一句话内容真正回答的是甲栏、所引来源却登记在
#:     乙栏（另一栏）时，正确做法是**把这句话挪到回答甲栏的段落**，不是改来源的登记，也不是把
#:     整段的 `aspect_ids` 改挂到别栏（那会让本段原本那一栏漏答）；挪不过去（本小节没有回答
#:     那一栏的段落、或挪过去与已有的同一件事重复）就撤掉这句，并在它本想回答的那一栏写缺口。
#:     真实 cp-12 里 s0016（引 m04）与 s0018（引 m25）正是「名义挂甲栏、来源登记在乙栏」；
#: (b) 第 9 条新增「境外收入是收入结构，不是销售模式」：境外收入 / 销售境外的产品变化回答的是
#:     地区 / 业务构成，不得写进销售模式那一段；其金额、比率仍须过第 4 条（无合格事实或格级
#:     来源时同样不许进正文）。真实 cp-12 的 s0018 就是这一类；
#: (c) 第 9 条末与第 3b 条对齐：发行人自评（「拥有核心技术优势」「前瞻性研发布局」一类）**即使
#:     在所引来源里逐字出现**，也不得落成客观结论句——要么写出依据，要么写成归属句，要么不写。
#:     逐字在场只授权「引用」，不把自评升格为已核实事实。真实 cp-12 的 s0010 是这一类；
#: (d) 第 5 条新增：「报告期内」「期末」「本期」这类**没有绝对年份**的相对期间不等于「当前
#:     状态」，不得与「目前 / 当前 / 截至报告生成日」连用，读者要能看出它是**哪一份来源**的
#:     报告期（真实 cp-12 的 s0017 用「报告期内锂电池产能 772GWh，期末在建产能 321GWh」）；
#: (e) 第 10 条动笔前自查补一句：撤下无授权数字**不等于**把整节压成产品清单，有材料支撑的
#:     定性内容（业务构成、应用、研发/采购/生产/销售方式、产业链位置）照第 3、3b 条写足；
#:     第 12b 条补「一条比较句只覆盖它所引事实覆盖的那几个期间」，禁止由一次两期变动概括整个
#:     报告期或外推到报告期之后的季度（真实 cp-12 财务节 s0004 的「权益占比逐步上升」）。
#: **口径限制：cp-12 真实 run 验证的是 cp-12；cp-13 / cp-14 均未经任何真实运行验证。**
#:
#: `cp-15` 按 cp-14 真实公司节与财务节的**逐句证据**再补四处写作面约束（不加字段、不放宽任何
#: 一条轴、不动硬核对器；`CITED_WRITER_PROMPT_REVISION` 之外没有别的代码面变更）。四处针对的
#: 都是「判据/词表已在，模型没照着做」这一类，而不是新增判定：
#: (a) 第 3d 条新增「**登记归属优先于来源角色与文档新旧**」。cp-14 公司节 9 句硬错里 8 句是
#:     `sentence_aspect_not_registered`；逐句对账（`_m930_3_probe/cp14_hard_sentence_audit.txt`）
#:     证明这些**不是材料登记过窄**：同一段原文在清单里另有一行**登记到了**该栏——s0006–s0008
#:     声明的 `app_scenarios` 有登记行 m02（原文逐字含「乘用车应用领域……商业应用领域……船舶、
#:     航空器、电动工具、电动两轮车」），而模型引的是只登记到 `products_solutions` 的 m47；
#:     s0013 声明的 `industry_chain_position` 有登记行 m57（逐字含「上游关键资源……锂、镍、钴、
#:     磷」），模型引的是 m40。3d 原有条目说了「本栏要用的，是登记到本栏的那一行」，但没有说
#:     **当同一内容另有一行来自更新的文档时谁优先**——补上这条优先级即可，不动任何登记。
#: (b) 第 9 条新增两条栏目边界：**具名客户名单不是销售模式**（cp-14 的 s0017–s0019 把 2024 年
#:     客户名录写进 `sales_mode` 段，既错栏又把历史披露写成当前）；**「公司靠什么赚钱」不是收入
#:     构成**（cp-14 的 s0011 把 m36 的经营模式总述放进 `revenue_breakdown` 段，而 m36 登记在
#:     `sales_mode` 一侧）。同时把第 9 条原有措辞里「怎么卖、**卖给谁**」的「卖给谁」删掉——
#:     它本身就在**邀请**把客户名录写进销售模式，改成「客户的**类型**」。
#: (c) 第 4 条末补充「同位语 / 定义式改写同样算未授权主体表面」。cp-14 的 s0001 把来源 m43 的
#:     「公司是**全球领先的**零碳新能源科技公司」改写成「公司是**一家**零碳新能源科技公司」，
#:     被 `unsourced_subject_surface` 判硬错。**这条硬错是真的**：来源里没有 `一家`。
#:     抽取器另有一处真实边界（`公司是<名词短语>` 会粘出带主语的表面，病灶在
#:     `narrative_schema._ENTITY_RUN_STOP` 缺 `是`），但那一处**不改变本句的结论**，
#:     且修它要动判定集与三处版本，已登记为待裁决事项，本批不实施——见 `_m930_3_probe/
#:     M930_3_CP15_RECONCILIATION.md` §1 s0001 行。
#: (d) 第 12b 条末尾补一条**层级可写性**：`指标含义` 与 `风险与限制` 两层只在所引事实/材料
#:     自己的文字里有依据时才写，事实只给数值且 `scope` 为空时**只写数值列示**。cp-14 财务节
#:     6 句全是数值列示、18 条机械轴全绿且**没有** 4 个分析层里的两层——读起来像「复述表格」。
#:     逐条对账（`_m930_3_probe/cp15_counterfactual_replay.txt` §6）表明：这不是写作侧偷懒，
#:     而是**授权面**如此——本链只有 `authority_facts` 给的数值，方向词（上升/下降）与派生差额
#:     都不在授权面内（`FORMULA_REVIEW.md` §5.1 只批了一条 `SOLV_DEBT_RATIO_DELTA_PP`）。
#:     补这一条是为了消掉原模板「要三层」与第 2/4 条「无来源不得成句」之间的**自相矛盾**：
#:     少写这两层不算漏答，凭空补上才是越权。
#: **口径限制：cp-14 真实 run 验证的是 cp-14；`cp-15` 未经任何真实运行验证。**
#:
#: `cp-16` 收回 `cp-15` 第 3d 条里**写反了的一句优先级**，并把缺口理由的选法改为「按本栏真实
#: 清单定」（不加字段、不放宽任何一条轴、不动硬核对器；`CITED_WRITER_PROMPT_REVISION` 之外只有
#: 一处代码面变更——`assign_gap_reason` 的**反向**改判与它的两轴计数，见下）。两处都由 cp-14 真实
#: 留存字节的离线反事实重放（`_m930_3_probe/cp15_counterfactual_replay.txt`、`cp16_*`）支撑：
#: (a) **第 3d 条**：`cp-15` 写的「**登记归属优先于来源角色与文档新旧**」把 `aspect_ids` 登记
#:     抬成了通行证。重放证明它**只是必要条件**：s0006–s0008 换引 m02、s0013 换引 m57 后
#:     `sentence_aspect_not_registered` 确实消失，**但** m02 / m57 是 `topic_participating_source`
#:     （募集说明书的主题参与来源），m47 / m40 才是 2025 年报的 `current_state_source`——栏目轴
#:     通了**不等于**这四句的现在式陈述已获来源支持。本版把 3d 改成「必要条件，不是充分条件」，
#:     并给出三条**逐句**出路：本栏另有当前态登记行就引它；只有主题参与来源就把话写成**归属句**
#:     （点明是哪一份来源的披露，不写「目前 / 当前 / 现阶段仍然如此」）；两条都做不到就**撤句**，
#:     按第 7 条按本栏真实清单留缺口。**不**把任何一句硬编码到某个 material ID，也不新增公司、
#:     页码特判。
#: (b) **第 7 条缺口理由**：原文把 `source_present_but_not_admissible` 写成了第 4、10、3d 条里
#:     「写不出数字就留缺口」的**默认词**。本版改成先看本栏真实清单（材料行 ∪ 事实行，财务侧含
#:     `presentation_routing.fact_columns`）再选：零登记 ⇒ `no_source_in_manifest`，并**明说**
#:     零登记时不得写成「有材料但不合格」，也不得写成「公司没有披露 / PDF 里不存在」。与之配对，
#:     `assign_gap_reason` 补一条**反向**改判（自述「只取到一部分 / 来源在场但不可用」而该栏登记
#:     数为 0 ⇒ 改回 `no_source_in_manifest`），并把计数从「材料行」扩到「材料行 ∪ 事实行 ∪ 呈现层
#:     路由」——供应商两栏（0 份）与客户三栏（各 3 份）从此不可能被记成同一条。
#: **口径限制：cp-14 真实 run 验证的是 cp-14；`cp-15` 与 `cp-16` 均未经任何真实运行验证。**
#:
#: `cp-17` 按 cp-16 真实双节 run 的读者页证据做一处**输入面**改动加三处规则面改动。它**不**
#: 放宽任何一条轴、**不**动硬核对器、**不**动任何词表、**不**碰缺口理由的判定：
#: (a) **输入面（本版唯一的代码面改动）**：`build_cited_prose_request` 的每个小节多一个
#:     `citable_columns`——逐栏给出「登记到**本栏**的材料键与事实键」（唯一实现在
#:     :func:`citable_columns`，与缺口理由用的是同一条 `registered_material_keys` /
#:     `registered_fact_keys`，不另立一份口径）。动因是 cp-16 真实公司节的**最大一处正文损失**：
#:     六条经营模式栏（采购模式 / 生产模式 / 销售模式 / 技术路线 / 成本结构 / 成本竞争能力）
#:     各自登记着三行材料（m24 / m36 / m39），模型却把六栏全部自述成「未单独登记到本栏」并
#:     只留缺口——`m36` 的原文逐字含「研发方面……形成以自主研发为主、外部合作为辅的研发模式」
#:     「采购方面……遴选合格供应商」，正是那几栏的内容。请求面此前只有**逐行**的 `aspect_ids`，
#:     要得到「本栏有哪些候选」得先做一次 18 栏 × 65 行的求交；本版把这一步的结果直接摊开。
#:     它与既有字段**并列而不替代**：`materials[].aspect_ids` 仍是登记轴本身（逐行），
#:     `citable_columns` 只是它的按栏视图。**空数组是结论**（本栏零登记），不是缺字段。
#: (b) **第 3d 条**：把 (a) 摆到写作次序的最前面——为某一栏动笔前先看该栏的 `citable_columns`：
#:     一个键都没有 ⇒ 按第 7 条留缺口；有键 ⇒ 只能在这些键里挑，**不得**再自述「本栏未登记」。
#:     这与 `cp-16` 的「登记是必要条件不是通行证」正交：本版只说「候选从哪来」，不说「候选
#:     一定合格」（来源角色 / 期间 / 句义的另外三关一字未动）。
#: (c) **第 9 条**：「「公司靠什么赚钱」不是收入构成」这一条的**去哪栏**写错了——原文说它
#:     「属于经营模式 / 主营业务那几栏」，于是 cp-16 真实公司节的 s0002（引 m36 的「公司拥有
#:     独立的研发、采购、生产和销售体系，主要通过销售……实现盈利」）被挪进了
#:     `main_business` 一侧的段落，而 m36 登记的是那六条**经营模式**栏，`main_business` 不在
#:     其中 ⇒ `sentence_aspect_not_registered`。本版改成指向经营模式的六栏本身，并明说**不要**
#:     顺手挪进「主营业务构成」（那一栏问的是业务线构成，不是怎么赚钱）。
#: (d) **第 12b 条**（财务）三处：期间必须逐句点明（写哪个数就点明它自己的 `period`，不得用
#:     无绝对年份的「报告期内」把四个期间罩在一句里——cp-16 财务 s0001 的 `unsourced_period_surface`
#:     即此）；四个数值并排**不等于**「保持相对稳定 / 基本持平」（判断口径未获批准，且「稳定」
#:     本身是高风险表面词——s0001 的 `unsourced_negation_surface` 即此）；跨期比较**只能**引用
#:     已给定的两期变动事实（如 `2025年末较2024年末……变动为-3.3个百分点`），**不得**自造
#:     「自 A 降至 B」的跨期句式（cp-16 财务 s0003 的 s0003 句）。
#: **登记为待裁决、本批不实施**：`TABLE_RELATION_SURFACE_MARKERS` 里的 `其中` 会在只作罗列连接词
#: 的句子里被 `sentence_check` 的 `negation_surface` 轴记成 `unsourced_negation_surface`
#: （cp-16 财务 s0003 被报的那一面正是它）。它确是**误报**（`其中` 不是否定词），但改它要动
#: `HIGH_RISK_SURFACE_MARKERS` 的判定集 ⇒ `FROZEN_MARKERS`（`evals/test_m930_3_risk_surface_semantics.py`
#: 明令「不得删词」）、`TABLE_RELATION_SURFACE_MARKERS ⊆ HIGH_RISK_SURFACE_MARKERS` 与七处
#: `nrules-16` 钉住的重放一起变，属大范围冻结版本迁移。按本批边界**记录并暂缓**，不让它拖住
#: 业务正文返修；本版改写的正文里也不再出现该词，**不**依赖这个误报被放宽。
#: **口径限制：cp-14 真实 run 验证的是 cp-14；`cp-15`／`cp-16`／`cp-17` 均未经任何真实运行验证。**
#:
#: `cp-18` 只做**一处输入面改动**：材料行在清单与请求面上的**顺序**。规则条文一字未改
#: ——不加规则、不删规则、不放松第 4 条、不动第 3c／第 3d 条的语义；硬核对器、词表、
#: 缺口理由判定、冻结 Contract 与任何预算都未触碰。
#:
#: 动因是 cp-17 真实公司节的**最大一处正文损失**：2025 年报里最完整的那一段
#: （`mat-tm-f2e6f405…`，1034 字：动力电池销量与市占率、二代神行超充／神行 Pro／骁遥双核／
#: 钠新／超混产品、商用车坤势底盘、福布斯榜单、一汽解放／北汽福田／东风商用车战略合作）
#: 与它的续片（286 字：海外售后网络 75 个国家或地区、约 1 200 家服务站）**登记到全部 7 栏
#: 却零引用**。这两片并不缺，它们缺的是**被看到的机会**：请求面此前把 65 行材料按**身份序**
#: （hash 序）交错摆开，三份文档、三种来源角色混在一起，上面那一段排在**第 61 行**。
#: 提示词第 3c 条要求「先按文档分组、当前态取最新一期」，而这一步在请求面上**不可执行**
#: ——与 `cp-17` 的 `citable_columns` 取代「18 栏 × 65 行求交」是同一类修法：把提示词要求的
#: 那一步在请求面**做出来**，而不是留给生成器自己从 65 行里分组。
#:
#: 因此本版把清单与请求面的材料行改成**呈现序**（唯一实现 :func:`material_presentation_order`：
#: 来源角色 → 文档身份 → 材料正文字数降序，同长按原序兜底），引用键按新序**重编**——键的
#: 定义本来就是「按清单序连续编号」。**只重排**：不删行、不改行内容、不改登记、不改来源角色、
#: 不改任何资格。清单身份随之改变（`cwm-7` 的 `identity_body` 含材料行与引用键），历史
#: `cited_input_manifest.json` 一律只作对照、不重写。
#:
#: **本版未经任何真实运行验证。** 它证明不了真实 Writer 会不会用上那两片；能证明的只是
#: 「这两片现在排在生成器最先看到的位置」——是否真的被写出来，要靠一次真实运行回答。
#:
#: `cp-19` 是**纯提示词条文**改动：输入面一个字段不加、一条判据不放松、硬核对器／词表／
#: 缺口理由／冻结 Contract／预算全不动，`material_presentation_order`（`cp-18` 的呈现序）
#: 原样保留。改的是**写作指令本身**，因为 `cp-18` 之后的现场读数把病根指到了这里：
#: 真实 cp17 草稿引了 65 行里的 **10** 份（2025 年报 9、2024 年报 1、**募集说明书 0**），
#: 而登记覆盖到 1,394 字的「神行系列」**一份没引**；`cp-18` 解决的是「排在哪儿」，
#: 解决不了「只读最前面两份就收工」。四处改动：
#: (a) **第 3 条**的典型顺序改成指令给的那条业务导航线（产品与技术 → 应用场景 →
#:     研发／采购／生产／销售模式 → **业务经营表现**），补上此前缺失的收尾栏；
#: (b) **新增第 3e 条**：`materials` 是本次**全部**材料，呈现序只决定先看到哪一片；
#:     明令不得「读了前两份就收工」、不得把全节压在一两份材料上，段落数量由内容决定；
#: (c) **第 3c 条**补一条「文档之间的优先次序」：同一件事多份文档都有依据时以**期次最新**的
#:     为据，期次只由**原文写明的期间**判断（不得由 `document_id` 推断，与同条既有规定一致）；
#:     募集说明书一类按主题参与的文档只作**口径补充**，较旧年报只作**显式历史对比**；
#: (d) **输入面说明**把「按来源文档分组、同一份文档连成一块」改成**「按来源角色分组、组内按
#:     文档连块」**——`cp-18` 的排序主键是角色，跨组时同一份文档可以再次出现，旧措辞在
#:     这个形状下**不准确**。
#:
#: **本版与它所依赖的 `cp-18` 一样，未经任何真实运行验证。** 离线替身只在清单前两位取材
#: （`materials_per_subsection=2`），它的句数变化**不能**当作本版的效果。
#: 本版的效果只能由一次**新的真实双节 run** 回答。
#: `cp-20` 是**写作组织**改动：输入面加**一个**版本化的阅读顺序提纲 `content_outline`
#: （`co-1`），提示词同步说明它。一条判据不放松、硬核对器／词表／缺口理由／冻结 Contract／
#: 预算／呈现序（`cp-18`）全不动，**不新增任何 fail-closed 门**。
#:
#: 动因是 v2_r1 真实公司节里**最大的一处「送达了却没写」**：65 份材料里 10 份被采用，
#: 未采用的 2025 年报材料里 `m01`（换电生态，1115 字）、`m03`（储能销量与系统方案，869 字）、
#: `m06`（ESG 与回收）、`m09`（海外与售后）、`m12`（巧克力换电）、`m16`（境外收入）、
#: `m19`（低空·船舶·数据中心）这一整批**全是「报告期内经营表现」**，合计约 3 千字当期正文，
#: **一句没写**。它们不是没送到（请求面 102,300 字符、`request_face_truncated=false`、65 行全投），
#: 也不是没授权（都是非数字经营描述）——是这一节 18 个 Contract 栏里**没有一栏叫「经营表现」**：
#: `cp-19` 在第 3 条里发明了「业务经营表现」这个收尾步，却**没给它栏位锚点**，模型按第 3d 条
#: 只能把它挂到别的栏上去，挂不上就整批跳过。同一根因的另一面就是 `s0015`：把
#: 「深化战略客户合作……一汽解放、北汽福田、东风商用车」（`m02` 末段，本就是经营表现内容）
#: 挂到 `sales_mode`，而 `m02` 没登记到该栏 ⇒ `sentence_aspect_not_registered`。
#:
#: 修法：把第 3 条那条**只在提示词里存在**的阅读顺序，按冻结 Contract 的**栏位身份后缀**
#: 派生成一份看得见的提纲（:func:`build_content_outline`），摆在每个小节行上。提纲把
#: 「报告期内经营表现」**锚到 `main_business` 栏**（该栏的 49 份候选正含上面那一整批），
#: 于是那一步**有处落地**。它只组织写作：不改登记、不判资格、不加门，未落在提纲里的声明栏
#: 原样保留在 `unmapped_aspect_ids` 里照常可写。公司名、材料 ID、页码、答案关键词一个都不出现
#: ——匹配用的是冻结 Contract 的栏位身份。
#:
#: `cp-21` 在 `cp-20` 的提纲上**只加一件事**：每一步带 `columns`（`co-2`），把「这一步可引
#: 哪些键」摆到那一步自己身上。动因是 `cp-20` 真实公司节（`m930_3_cited_real_company_cp20_r1`）
#: 的四条 `sentence_aspect_not_registered`：正文在所引材料里都有 24–40 字逐字串，**没有一句
#: 虚构**，但模型按语义挑行、挑中的行恰好没登记到该句声明的栏，而清单里**另有一行**登记到了
#: 那一栏（应用场景挑到 `m10`/`m05` 而非登记本栏的那一行；产业链挑到 `m11` 而非整句逐字在的
#: 那一行；`sales_mode` 那一条挑到只登记业务栏的 `m02`/`m09`）。`co-1` 只给 `aspect_ids`，
#: 那一跳（步 → aspect_id → 栏 → 键表）在 106,039 字符的请求面上**没被做出来**。
#: `columns` 是 :func:`citable_columns` 的逐字投影：同一份清单、同一套登记轴，**不新增判定、
#: 不新增候选、不放松任何门**。同时升 `co-1`→`co-2`，并同步补三条 step guidance（应用场景／
#: 产业链／经营模式／客户／供应商）把「本句所引那一行」这条要求落到该步上。
#:
#: `cp-22` 在 `cp-21` 上**也只加一件事**：给**栏**一个显式**内容目标**与**引用机会**
#: （`COLUMN_CONTENT_OBJECTIVES`，逐栏投影进 `co-3` 的 `columns` 与 `citable_columns`）。
#: 动因是 `cp-21` 真实公司节（`m930_3_cited_real_company_cp22_r1`）的
#: `company_business_model.sales_mode`：它有正文（`s0015`「公司拥有独立的销售体系，主要通过
#: 销售动力电池、储能电池和电池材料等产品和解决方案实现盈利」）、有出处（`m13`）、有登记核对，
#: `draft.gaps` 里却**连一条它的缺口都没有**——渠道类型、直销还是经销、结算方式一项都没写，
#: 账上也看不出来。根因是 `co-2` 只摆「这一栏有哪些候选」，从没说过「这一栏要答什么」，
#: 而 `operating_model` 步的 `guidance` **全是禁则**。`co-3` 把这一栏该答的几点摆到该栏自己
#: 身上，并要求：写不出的点按第 7 条**逐点**记缺口。它仍然不判资格、不新增候选、不放松任何门
#: （登记仍是必要条件、数字仍过第 4 条、缺口仍由模型产生）。
#:
#: `cp-23` 在 `cp-22` 上**只纠两条过度推断**（`co-4`）：`co-3` 的 `sales_mode` 内容目标把两件
#: **不由本栏材料支持**的东西写成了要答的点——「销售体系是**自建的还是靠经销／代理**」（本批三份
#: 材料只有「公司**拥有独立的**研发、采购、生产和销售体系」，「独立」≠ 自建、更 ≠ 直销）与
#: 「销售与生产的衔接（**订单／客户需求怎么牵引排产**）」（出自「综合考虑市场情况及客户需求安排
#: 生产」，**属生产模式**，且与同一段文本自己的「不写」清单正面冲突）。`cp-23` 不动键集、不动
#: 分步、不动任何门，只改 `COLUMN_CONTENT_OBJECTIVES["sales_mode"]` 的两段文本。
#:
#: `cp-24` 在 `cp-23` 上**给另外两栏补上同一对文本**（`co-5`）：`production_mode` 与
#: `revenue_breakdown`。动因是 `cp-23` 真实公司节（`m930_3_cited_real_company_cp23_qrework1_r1`）
#: 的两条读数——① `revenue_breakdown` 在请求面上带着 **49 个候选键**、`citable_fact_keys` 为空、
#: `content_objective` 也是空串，于是这一栏**一段正文都没有**（10 段 31 句里没有一段声明本栏），
#: 而三份载有分业务收入的材料**全部** `delivered_not_used`（0 引用句）；② `production_mode` 的
#: `citable_material_keys` 恰是 `['m13','m14','m44']`，正文却引了一份**只登记到主营业务栏**的
#: 材料去写本栏，三条句子全判 `sentence_aspect_not_registered`（`s0014`/`s0016`/`s0020`）。
#: `cp-24` **不动键集**（仍是 `(内容目标, 引用机会)` 两元组）、**不动分步**、**不动任何门**：
#: 登记仍是必要条件（第 3d 条）、数字仍过第 4 条、缺口仍由模型产生。同一次修订里把提示词第 9 条
#: 那句「没有合格金额或比率事实时，收入构成按第 7 条留缺口」**收窄到金额与占比两项**——
#: 定性的业务线描述照常可写，免得请求面上的一个新目标文本与它自己打架。
#:
#: `cp-25`（`ndc-4` 批）相对 `cp-24` 只改**收入金额与占比怎么处置**，不动任何别的规则：
#:
#: 1. 输入说明里补上两个新读数——`citable_columns[].writable_fact_keys` 与顶层
#:    `numeric_authorization` 块——并写清它们与 `citable_fact_keys` 的**分工**（后者是 Pack 侧
#:    登记，前者是本版可作正文数字授权）；
#: 2. 第 4 条补一句：被 `numeric_authorization.withheld` 标出的事实键不得写成正文数字结论；
#: 3. 第 9 条与 `revenue_breakdown` 的 `content_objective` 第 ② 点把金额与占比**分列**——
#:    金额按 `writable_fact_keys` 里的合格金额事实写（业务／年份／单位／来源逐字），占比在分母
#:    尚不可核时**逐项留结构化缺口**，**不得**改用普通材料原文或只读 PDF 展示区里的一个百分比
#:    顶上，也**不得**因为占比写不了就把金额与不带数字的业务叙述一起撤掉。
#:
#: 动因是**请求面与逐句核对正面冲突**：`cp-24` 的营收栏目标要求「有合格事实就写占比」，而
#: `scp-13` 对占比句一律判 `denominator_unverified` 硬错——模型按请求面写就必然红。`cp-25`
#: 不放松任何判据（`scp-13` 一字未动），只让请求面**说实话**。
#:
#: `cp-26`（M930-5 全过程实录批）相对 `cp-25` 只改**空段落怎么写**，不动任何别的规则、
#: 不动任何判据（`empty_paragraph` 一字未改）：
#:
#: 1. 「你要产出什么」一节把「`paragraphs` 可以是空数组」与「`sentences` 可以是空数组」
#:    **分开写死**：前者合格，后者是**硬错误**，会让整个小节作废；
#: 2. 第 7 条段级那一条补上同一句，并写明正确处置只有一种（整段不生成、缺口写进 `gaps`）。
#:
#: 动因是 M930-5 那次真实 run `m930_3_cited_upload_20261005T091926Z`：模型写作**成功返回**
#: （4212 输出 token、合法 JSON、10 段 30 句），但 `p10` 是
#: `{"paragraph_id": "p10", "aspect_ids": [...], "sentences": []}` 这样一个**空壳段**，
#: 于是 `CitedParagraph.__post_init__` 抛 `empty_paragraph`，**整节零产出**。旧提示词已经说了
#: 「没有正文就留缺口、不要占位」，但没有把「空数组」这个词**逐字**钉在 `paragraphs` 上——
#: 模型于是把它读成了「这个段落没有句子」。这一版补的就是这句逐字的区分。
#:
#: **本版与 `cp-18`…`cp-25` 一样，未经任何真实运行验证。** 离线替身只在清单前两位取材、
#: 也不读提示词，它的句数变化**不能**当作本版的效果；效果只能由一次新的真实公司节 run 回答。
#: 另外，解析侧对它另有一道**独立**的兜底（`crn-3` 的纯空壳段归一，见
#: :mod:`sections.cited_reply_normalize`）——两道各自成立：提示词这一道是「让它别写」，
#: 归一那一道是「写了也不让整节陪葬」，且只在**可证明是同一件记账**时才生效。
#:
#: **术语冲突的收尾（同属 `cp-26`，未另起修订号）**：改了上面两处之后，正文别处仍有 3 处
#: 写「写空段落」，而那个词在本版**第 103 行已被逐字定义为 `"sentences": []` 的硬错误**——
#: 同一份资产里同一个词指两件相反的事。旧稿（`cp-19`）里「空段落」指的其实是
#: `paragraphs: []`，本次把这三处的**用词**改成「把那一段整段不生成 / 写成 `paragraphs: []`」，
#: 语义一字未动、判据一字未动，只是不再让一个被禁止的写法顶着合规写法的名字出现。
#: `cp-26` 从未被任何真实运行使用过（盘上没有任何产物声明它），因此这是**同一版内的收尾**，
#: 不是一次新的提示词修订；这也是它不另起修订号的理由。
CITED_WRITER_PROMPT_ASSET = "cited_company_prose_v1"
CITED_WRITER_PROMPT_REVISION = "cp-26"
CITED_WRITER_PROMPT_VERSION = f"{CITED_WRITER_PROMPT_ASSET}@{CITED_WRITER_PROMPT_REVISION}"

#: 资产第 1 行声明修订号时用的**唯一**句式（`（<asset>，revision <rev>）`）。
_PROMPT_HEADER_RE = re.compile(r"（\s*([A-Za-z0-9_]+)\s*[，,]\s*revision\s+([A-Za-z0-9_.-]+)\s*）")

#: 两轴引用键前缀。**不相交**是「材料不会冒充事实、事实不会冒充材料」可核验的前提。
CITED_MATERIAL_KEY_PREFIX = "m"
CITED_FACT_KEY_PREFIX = "f"

#: 一个 manifest 成员在本次写作中的采用去向（**按句引用**记，不按旧链的 proposal ID 记）。
CITED_ADOPTION_DISPOSITIONS = ("adopted", "not_used")

#: 缺口原因的**封闭**词表（typed；不得塞进泛化字符串，也不得与事实缺口混用）。
CITED_GAP_REASONS = (
    #: 本小节要求的东西在本次精确材料清单里没有任何来源。
    "no_source_in_manifest",
    #: 有来源，但它只支撑一部分（例如只有分地区合计、没有分产品明细）。
    "manifest_partial_for_requirement",
    #: 来源存在但本次不可用（例如表未获放行、数字未获格级授权）。
    "source_present_but_not_admissible",
    #: 要求本身与本节的权威输入无关（不应写）。
    "not_applicable_to_authority",
)

#: 补件诉求的可辨识性等级（与 `harness.topic_schema.FOLLOW_UP_REQUIREDNESS` **同一**词汇：
#: 复用既有闭集，不新增「preferred」这类第三档）。
CITED_FOLLOW_UP_REQUIREDNESS = TS.FOLLOW_UP_REQUIREDNESS

#: 一条缺口理由**是怎么定下来的**（封闭三值；这不是新的原因码，原因码仍是
#: :data:`CITED_GAP_REASONS`）。把判定来源单独记一轴，是因为「生成器说没有材料」与「系统按
#: 清单核对后说没有材料」在产物上长得一样，而对下游的含义完全不同：前者是一句自述，后者是
#: 一条查得动的事实。两个字段都不做语义判断，只做**能从清单证实或证伪**的那一小部分。
CITED_GAP_REASON_ASSIGNMENTS = (
    #: 生成器自述的理由，与清单核对的结论一致（或本模块没有依据反驳它）。
    "writer_declared",
    #: 生成器自述的理由被**清单核对证伪**，已改判；原话留在 `claimed_reason` 里。
    "system_reassigned_from_manifest",
    #: 缺口指向的小节不在本次请求面里 ⇒ 无法核对，原样保留并标出（不做语义猜测）。
    "uncheckable_subsection_not_in_manifest",
)

#: 一条补件需求的 `expected_source_classes` **是从哪来的**（封闭三值）。它是审计轴：读回时
#: 要能一眼看出这一串来源类**不是**生成器写的，而是从冻结 Contract 的 `evidence_requirements`
#: 登记逐字投影出来的。
CITED_SOURCE_CLASS_DERIVATIONS = (
    #: 该栏在 Contract 里声明了证据要求 ⇒ 来源类逐字取自那些要求的 `source_classes`。
    "contract_aspect_evidence_requirements",
    #: 该栏**没有任何证据要求**（`allowed_source_classes` 为空）⇒ 推不出来源类。空 tuple 是
    #: **结论**（这一栏的补件来源类别不可由 Contract 推导），不是「读不到」。
    "contract_aspect_without_evidence_requirements",
    #: 缺口/补件指向的小节不在本次请求面里 ⇒ 连它是哪一栏都不知道，推不出来源类。
    "aspect_not_in_manifest",
)


class CitedWriterError(Exception):
    """新写作链在**构造 / 解析 / 校验**任一步的 fail-closed。

    `reason` 用本模块的封闭原因码；`sentence_id` / `citation_key` 指向出问题的具体位置，
    使「哪一句、引用哪一个键」在异常里就能读出来，而不必回读整份清单。
    """

    def __init__(self, message: str, *, reason: str = "", sentence_id: str = "",
                 citation_key: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.sentence_id = sentence_id
        self.citation_key = citation_key


# ---------------------------------------------------------------------------
# 引用键
# ---------------------------------------------------------------------------

def cited_material_key(index: int) -> str:
    """材料引用键：`m01` / `m02` …（两位定长，使键的字典序 == 清单序）。

    定长是**可读性**要求，不是排序要求：请求面里材料可能上百行，模型复述键时的抄写负担
    直接决定引用错误的多少。两位足够覆盖本节任何真实材料清单，超出时按需加宽（加宽即换键
    口径，必须同时改 `CITED_WRITER_POLICY_VERSION`）。
    """
    if not isinstance(index, int) or index < 0:
        raise CitedWriterError(f"材料引用序号必须是非负整数，得到 {index!r}")
    return f"{CITED_MATERIAL_KEY_PREFIX}{index + 1:02d}"


def cited_fact_key(index: int) -> str:
    """事实引用键：`f01` / `f02` …（同上）。"""
    if not isinstance(index, int) or index < 0:
        raise CitedWriterError(f"事实引用序号必须是非负整数，得到 {index!r}")
    return f"{CITED_FACT_KEY_PREFIX}{index + 1:02d}"


def citation_key_axis(key: Any) -> str:
    """一个引用键落在**哪一条轴**上（`material` / `fact`）；前缀对不上即 fail-closed。

    与 `NS.provenance_axis_of` 是同一纪律的另一处实例：判据只有一处，任何调用方不得各自再写
    一遍 `startswith`。两轴的键形状（`m`+两位 / `f`+两位）由上面两个构造函数独占。
    """
    value = str(key or "")
    if len(value) >= 3 and value[0] == CITED_MATERIAL_KEY_PREFIX and value[1:].isdigit():
        return "material"
    if len(value) >= 3 and value[0] == CITED_FACT_KEY_PREFIX and value[1:].isdigit():
        return "fact"
    raise CitedWriterError(
        f"引用键 {value!r} 不属于任何一条登记过的引用轴"
        f"（材料侧 {CITED_MATERIAL_KEY_PREFIX!r} / 事实侧 {CITED_FACT_KEY_PREFIX!r}）",
        reason="citation_key_malformed", citation_key=value)


# ---------------------------------------------------------------------------
# 小节规格（Contract 侧唯一输入面；本模块不解释 Contract）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitedSubsectionSpec:
    """一个小节的**写作要求**：id + 标题 + Contract 逐字要求文本 + **声明的栏目**。

    `requirement_text` 必须**逐字**来自冻结 Contract 的 `requirement_text`，由调用方（组合根）
    从既有的 Contract 读视图取，**不由本模块**解释或改写。本模块只负责把它交给模型、并要求
    返回的草稿小节与它一一对应。

    `declared_aspect_ids` 是本小节声明**要覆盖的那些** Contract 栏目（`aspect_id`）的有序集合。
    它**必须**显式给出，不得由「`subsection_id` 恰好等于 aspect id」这个约定隐式承担：一旦某个
    调用方用别的名字当小节 id，那条约定就会静默失效，而失效的表现是「每一句的栏目核对都跳过」
    ——那与「每一句都对上了」在产物上长得一模一样。它与材料行的 `aspect_ids`（材料**登记**归属）
    配对使用，是逐句栏目核对的**唯一**输入面。

    **为什么是集合而不是一个 id（`cwm-6`）**：本节的写作小节由**冻结 WritingSpec 的小节**决定，
    而一个 WritingSpec 小节可以覆盖多个 Contract 栏目——`company_business*` 的 18 个栏目**全部**
    映到 `co-h4`「主营业务、经营模式与产业链」，`fin_solvency.*` 的 6 个栏目**全部**映到 `fin-h2`
    「资产负债结构分析」（`templates/writing_specs/credit_report_v1.yaml`）。旧口径按 **aspect**
    造小节，等于在读者面上发明了一个比冻结规范更细、且与它不一致的章节结构：写出来的是 18 个
    各写一句的短栏，同一份材料被反复复用，去向也读不出「这一段服务于哪一个 Contract 栏目」。
    集合口径把两件事分开：**写作单位**是 WritingSpec 小节，**责任单位**仍是 Contract 栏目
    （逐段的 `aspect_ids` 声明 + 覆盖账对账）。

    `allowed_source_classes` 是该 Contract 栏目**允许的检索来源类**（`cwm-5`）。它逐字来自冻结
    Contract 的 `evidence_requirements` 登记（`TopicAspectRequirementSnapshot
    .evidence_requirement_ids[].source_classes`），由调用方投影，**本模块不解释 Contract**。
    它**不**授权任何材料，也**不**保证那一类来源本次真的在清单里：它唯一的用途，是让「补件该
    往哪一类来源去找」有一个**可追溯**的答案，而不是由生成器当场发明一个。空 tuple 是**结论**
    （该栏在 Contract 里没有证据要求），不是「读不到」。它**不**在这里对照
    `harness.topic_schema.SOURCE_CLASSES` 做封闭校验：Contract 声明的来源类与「本轮可检索来源
    类」是两个集合（前者还可能含 `financial` / `project` 这类只在权威侧出现的值），在这里
    fail-closed 会把 Contract 侧的事实误判成本模块的输入错误；越界与否由读回**标出**
    （见 :mod:`sections.cited_reply_readback`），不由本模块静默改写。
    """

    subsection_id: str
    title: str
    requirement_text: str
    #: 本小节声明覆盖的 Contract 栏目（有序、无重复、非空）。**没有单数版**：旧字段
    #: `declared_aspect_id` 已随 `cwm-6` 移除——留一个「取集合首元」的兼容读法，等于让
    #: 「这一节负责哪些栏目」重新退化成一条隐式约定，而那正是本版要堵掉的东西。
    declared_aspect_ids: tuple[str, ...] = ()
    #: 该小节覆盖的各 Contract 栏目允许的检索来源类（逐字取自 Contract；见类注释）。
    allowed_source_classes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("subsection_id", "title", "requirement_text"):
            if not str(getattr(self, name) or "").strip():
                raise CitedWriterError(f"CitedSubsectionSpec.{name} 必须非空")
        aspects = tuple(str(a or "").strip() for a in (self.declared_aspect_ids or ()))
        if not aspects:
            raise CitedWriterError(
                "CitedSubsectionSpec.declared_aspect_ids 不得为空"
                "（声明的 Contract 栏目不得留空：留空会让这个小节的逐句栏目核对整体跳过，"
                "而「跳过了」与「都对上了」在产物上无法区分）")
        if any(not a for a in aspects):
            raise CitedWriterError(
                "CitedSubsectionSpec.declared_aspect_ids 不得含空串"
                "（空串会被读成一个栏目 id，而「这一栏没有名字」是不可执行的覆盖声明）")
        if len(set(aspects)) != len(aspects):
            raise CitedWriterError(
                "CitedSubsectionSpec.declared_aspect_ids 不得有重复项"
                "（重复项会让覆盖账的分子与分母取值不同，两栏共用同一个 id）")
        object.__setattr__(self, "declared_aspect_ids", aspects)
        classes = tuple(str(c or "") for c in (self.allowed_source_classes or ()))
        if any(not c.strip() for c in classes):
            raise CitedWriterError(
                "CitedSubsectionSpec.allowed_source_classes 不得含空串"
                "（空串会被读成一个来源类，而「这个类没有名字」是不可执行的补件指令）")
        if len(set(classes)) != len(classes):
            raise CitedWriterError(
                "CitedSubsectionSpec.allowed_source_classes 不得有重复项"
                "（重复项会让补件的来源类集合与其取值不同，两份清单共用同一个 id）")
        object.__setattr__(self, "allowed_source_classes", classes)

    def identity_body(self) -> dict:
        return {"subsection_id": self.subsection_id, "title": self.title,
                "requirement_text": self.requirement_text,
                "declared_aspect_ids": list(self.declared_aspect_ids),
                "allowed_source_classes": list(self.allowed_source_classes)}


# ---------------------------------------------------------------------------
# 输入清单：材料行 / 事实行
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitedMaterialEntry:
    """输入清单的**材料行**：一份当前 Pack material 的原文 + 来源角色 + 精确位置。

    `reading_view` 是 `wmctx-1` 解析出来的**抽取式**正文投影（不增删任何实词），因此「模型
    看过的正文」与「复核复核的正文」是同一串字节。`source_role` 只对带来源文档系列的权威
    有意义（财务 / 附注 / 外部快照没有这条轴），此时为空串是**结论**而不是缺失。
    """

    citation_key: str
    member_ref: str
    pack_id: str
    material_id: str
    topic_id: str
    material_type: str
    source_identity: str
    provenance_identity: str
    source_role: str
    locator_ref: dict
    payload_hash: str
    reading_view: str
    reading_view_fingerprint: str
    authority_kind: str = "topic_pack"
    #: 来源**文档**身份（`srsc-1` 台账的键）。只有落在来源文档上的 evidence 定位才有这一轴；
    #: 结构化 / 外部快照材料没有文档系列，此时为空串是**结论**而不是缺失。
    document_id: str = ""
    #: 表材料的**结构化**读视图（`table_object_reading_view` 那一层）。写作只读压平正文，
    #: 逐句核对要按格核对，因此这一层必须随清单一起进 Writer 输入面并落盘可回放。
    structured_view: dict | None = None
    #: 内容形态读法（`kind`；`selection_form` 时另带 `selection`）。它是**派生**字段：原件在
    #: payload 里，且 `content_qualification` 本来就进 payload 哈希，故已被 `payload_hash` 钉住。
    content_qualification: dict | None = None
    #: **跨块切分读法**（`cwm-7`）：同一 `OutlineSpan` 每跨一个 Evidence 块产出一份材料，切片形状
    #: （片序 / 共片数 / 全文长度 / 本片在 span 本地区的精确区间）与**前一片 / 后一片的 member_ref**
    #: 一起交给生成器。它与 `content_qualification` 同一纪律：**派生**字段（原件在 payload 里、
    #: 已被 `payload_hash` 钉住），**不进** `identity_body()`、进序列化。它**不**合并任何两片：
    #: `reading_view` / `locator` 仍是本片自己的——续接只是「这两片是同一段原文」的**关系**陈述。
    span_continuity: dict | None = None
    #: 这份材料在 Pack 侧**登记归属**的 Contract 栏目（`ResearchMaterialDisposition.aspect_ids`）。
    #:
    #: 它是**身份轴**（进 `identity_body`），与 `source_role` / `document_id` 同类：登记不同的
    #: 两份清单不得共用同一个 id。空 tuple 是**结论**——这份材料在 Pack 侧没有被认领到任何
    #: 栏目——不是「读不到」（读不到由构造入口 fail-closed）。它**不**授权任何事实：一条材料
    #: 登记在某一栏，只说明检索阶段把它认领给了那一栏，不说明它的正文支持那一栏的每一句话。
    aspect_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if citation_key_axis(self.citation_key) != "material":
            raise CitedWriterError(
                f"CitedMaterialEntry.citation_key={self.citation_key!r} 不是材料轴键",
                reason="citation_key_wrong_axis", citation_key=self.citation_key)
        if self.authority_kind != "topic_pack":
            raise CitedWriterError(
                "CitedMaterialEntry.authority_kind 只能是 'topic_pack'：exact ResearchMaterial "
                "是 Pack 侧的正式材料边界，财务 / 附注 / 外部快照各有自己的权威身份，"
                "不得伪装成一条普通 Pack 材料",
                reason="material_authority_forged")
        for name in ("member_ref", "pack_id", "material_id", "topic_id", "material_type",
                     "source_identity", "provenance_identity", "reading_view"):
            if not str(getattr(self, name) or "").strip():
                raise CitedWriterError(f"CitedMaterialEntry.{name} 必须非空")
        for name in ("payload_hash", "reading_view_fingerprint"):
            if not NS._is_sha256_hex(getattr(self, name)):
                raise CitedWriterError(f"CitedMaterialEntry.{name} 必须是 64 位 sha256 hex")
        expected_ref = NS.manifest_member_ref(self.pack_id, self.material_id)
        if self.member_ref != expected_ref:
            raise CitedWriterError(
                f"CitedMaterialEntry.member_ref 与容器/材料身份不符：声明 {self.member_ref!r}，"
                f"应为 {expected_ref!r}", reason="member_ref_mismatch")
        locator = NS.validate_locator(self.locator_ref, "CitedMaterialEntry")
        if locator is None:
            raise CitedWriterError(
                "CitedMaterialEntry.locator_ref 不得为空（有正文的材料必须有精确位置）",
                reason="locator_missing")
        object.__setattr__(self, "locator_ref", locator)
        if not isinstance(self.source_role, str):
            raise CitedWriterError("CitedMaterialEntry.source_role 必须是字符串（无角色时用空串）")
        if not isinstance(self.document_id, str):
            raise CitedWriterError("CitedMaterialEntry.document_id 必须是字符串（无文档系列时用空串）")
        object.__setattr__(self, "aspect_ids",
                           tuple(dict.fromkeys(
                               str(a) for a in (self.aspect_ids or ()) if str(a or ""))))
        if not isinstance(self.structured_view, (Mapping, type(None))):
            raise CitedWriterError("CitedMaterialEntry.structured_view 只能是 mapping 或 None")
        object.__setattr__(self, "structured_view",
                           dict(self.structured_view) if self.structured_view else None)
        qualification = self._validated_qualification()
        object.__setattr__(self, "content_qualification", qualification)
        object.__setattr__(self, "span_continuity", self._validated_continuity())
        # 读视图指纹**当场重算**：`structured_view` 一旦被改动，压平正文可以一字不变而逐句
        # 核对读到的「格」被换掉——只信 `reading_view_fingerprint` 而不重算，正是本模块禁止的
        # 「声明即事实」。重算复用 `material_context` 的**同一**实现（`wmctx-1` 的唯一口径）。
        from sections import material_context as MC  # 延迟导入：避免 sections 内部循环
        expected_fp = MC._reading_view_fingerprint(
            payload_hash=self.payload_hash, object_type=self.material_type,
            reading_view=self.reading_view, structured_view=self.structured_view)
        if self.reading_view_fingerprint != expected_fp:
            raise CitedWriterError(
                "CitedMaterialEntry.reading_view_fingerprint 与读视图（含结构化层）不符："
                "正文或结构化读视图已被改写", reason="reading_view_forged")

    def _validated_qualification(self) -> dict | None:
        """内容形态读法的封闭校验（与 `wmctx-1` 同表，不另立一份词表）。"""
        if self.content_qualification is None:
            return None
        if not isinstance(self.content_qualification, Mapping):
            raise CitedWriterError("CitedMaterialEntry.content_qualification 只能是 mapping 或 None")
        from harness import tree_materials as TM  # 延迟导入：词表只有一份
        qualification = dict(self.content_qualification)
        kind = str(qualification.get("kind", "") or "")
        if kind not in TM.TREE_MATERIAL_CONTENT_KINDS:
            raise CitedWriterError(
                f"CitedMaterialEntry.content_qualification.kind 不在封闭词表内：{kind!r}",
                reason="content_qualification_kind_unknown")
        if qualification.get("is_material") is False:
            raise CitedWriterError(
                "CitedMaterialEntry.content_qualification 声明「不是材料」，"
                "但本对象只承载已成为材料的正文", reason="content_qualification_not_material")
        if kind == "selection_form" and not isinstance(qualification.get("selection"), Mapping):
            raise CitedWriterError(
                "content_qualification.kind='selection_form' 必须带 selection 结构化读法"
                "（表单行不得只留一个形态名）",
                reason="content_qualification_selection_missing")
        return qualification

    def _validated_continuity(self) -> dict | None:
        """跨块切分读法的校验：复用 `material_context` 的**同一**形状判据（不另写一份）。

        续接读法只在**真正被切过**的材料上出现（共片数 ≥ 2）；读不懂即 fail-closed，而不是
        当成「整段材料」放行——那会让「同一段原文的两片」在生成器眼里退化成两条互不相干的来源。
        """
        if self.span_continuity is None:
            return None
        from sections import material_context as MC  # 延迟导入：与 `wmctx-1` 同一口径
        return MC._checked_continuity(self.span_continuity, member_ref=self.member_ref)

    @property
    def content_kind(self) -> str:
        """内容形态名（无形态读法时为空串 = 「没有这条读法」，不是「是叙述正文」）。"""
        return str((self.content_qualification or {}).get("kind", "") or "")

    def to_dict(self) -> dict:
        return {"citation_key": self.citation_key, "member_ref": self.member_ref,
                "pack_id": self.pack_id, "material_id": self.material_id,
                "topic_id": self.topic_id, "material_type": self.material_type,
                "source_identity": self.source_identity,
                "provenance_identity": self.provenance_identity,
                "source_role": self.source_role, "document_id": self.document_id,
                "locator_ref": dict(self.locator_ref),
                "payload_hash": self.payload_hash, "reading_view": self.reading_view,
                "reading_view_fingerprint": self.reading_view_fingerprint,
                "structured_view": dict(self.structured_view) if self.structured_view else None,
                "content_qualification": (dict(self.content_qualification)
                                          if self.content_qualification else None),
                "span_continuity": (dict(self.span_continuity)
                                    if self.span_continuity else None),
                "aspect_ids": list(self.aspect_ids),
                "authority_kind": self.authority_kind}

    def identity_body(self) -> dict:
        """身份体：**不含**正文与结构化读视图本体，只含 `reading_view_fingerprint`。

        正文或结构化层变一个字节，`reading_view_fingerprint` 必变（上文当场重算，`wmctx-1`
        同一实现），因此身份不丢任何可核验性；而把上百万字的正文塞进 `identity_body` 会让每
        一次 id 重算都要哈希全库。`document_id`（来源文档身份）与 `aspect_ids`（Pack 侧登记
        的栏目归属）都是**身份轴**，因此留在体内：改任一条都属于「换了一份清单」，不是
        「同一份清单换了个读法」。
        """
        body = self.to_dict()
        body.pop("reading_view")
        body.pop("structured_view")
        body.pop("content_qualification")
        # 续接读法同样派生自 payload（信封里的 `span_split` 已被 `payload_hash` 钉住），
        # 不进身份体：同一片原文换了个「看得见邻居」的读法，不是换了一份清单。
        body.pop("span_continuity")
        return body


@dataclass(frozen=True)
class CitedFactEntry:
    """输入清单的**事实行**：一条**预验证权威事实**的确定性读视图。

    `authority_kind` 是四元 union 的成员；`fact_id` 按该 kind 的 branch-specific 字段取值。
    事实行**不进**材料行所在的命名空间：它带的是权威事实身份，不是 `ResearchMaterial`。
    """

    citation_key: str
    authority_kind: str
    container_identity: str
    fact_id: str
    text: str
    topic_id: str
    aspect_ids: tuple[str, ...]
    fact_type: str
    period: str
    scope: str
    required: bool
    source_identity: str = ""
    provenance_identity: str = ""
    locator_ref: dict | None = None
    material_id: str | None = None
    payload_ref: dict | None = None
    #: 事实**自己声明的逐值身份**（`SupportedFact.value_identity`）。`nd-2` 起由确定性抽取出的
    #: 分业务营收事实携带；财务／附注／外部与旧夹具为 `None`。它不参与事实**坐标**（坐标仍是
    #: `(authority_kind, container_identity, fact_id)`），只把「这条事实授权的是哪个值」原样
    #: 交给逐句核对，使那边不必从 `text` 那段逐字前缀反推。
    #:
    #: **写出是有条件的**（见 `to_dict`）：`None` 时整条键不出现，因此历史清单里每条事实行的
    #: 身份体**逐字不变**，`cwm-7` / `cwp-1` 不升版、历史 `manifest_id` 照旧可复验。
    value_identity: dict | None = None

    def __post_init__(self) -> None:
        if citation_key_axis(self.citation_key) != "fact":
            raise CitedWriterError(
                f"CitedFactEntry.citation_key={self.citation_key!r} 不是事实轴键",
                reason="citation_key_wrong_axis", citation_key=self.citation_key)
        if self.authority_kind not in NS.AUTHORITY_KINDS:
            raise CitedWriterError(
                f"CitedFactEntry.authority_kind={self.authority_kind!r} 不在 "
                f"{list(NS.AUTHORITY_KINDS)} 内", reason="authority_kind_unknown")
        #: 必填 = **坐标**（`container_identity` + `fact_id`）与句子必须能读出来的三样
        #: （文本、主题归属、事实类别）。这四样缺任何一样，这条事实行都无法被引用或无法被定位。
        for name in ("container_identity", "fact_id", "text", "topic_id", "fact_type"):
            if not str(getattr(self, name) or "").strip():
                raise CitedWriterError(f"CitedFactEntry.{name} 必须非空")
        #: `period` / `scope` 是**读者面**字段（§十一：表格的主体/期间两列），**不**是坐标：
        #: 四种 `authority_kind` 里有三种在权威侧本来就没有这一轴，写入侧读视图按「宁可留空、
        #: 不得编造」如实留空——`scan_financial` 给财务事实的 `scope` 是 `""`（该权威没有这个
        #: 字段）、给外部快照事实的 `scope` 也是 `""`（同上）、给附注事实的 `period` 是 `""`
        #: （期间由 locator 指向的原文承担，`_note_fact_text` 的注释写着「不得编造」）。
        #: 把这两个字段也判成必填，等于要求写入侧为三种权威各编一个期间/主体出来——那正是
        #: 本模块开头「一个字节都不新造」要禁的事。留空 = 本权威**没有**这一轴，不是缺字段。
        for name in ("period", "scope"):
            object.__setattr__(self, name, str(getattr(self, name) or "").strip())
        object.__setattr__(self, "aspect_ids",
                           tuple(str(x) for x in (self.aspect_ids or ()) if str(x or "")))
        if not isinstance(self.required, bool):
            raise CitedWriterError("CitedFactEntry.required 必须是 bool")
        if self.locator_ref is not None:
            locator = NS.validate_locator(self.locator_ref, "CitedFactEntry")
            object.__setattr__(self, "locator_ref", locator)
        if self.payload_ref is not None and not isinstance(self.payload_ref, Mapping):
            raise CitedWriterError("CitedFactEntry.payload_ref 只能是 mapping 或 None")
        object.__setattr__(self, "payload_ref",
                           dict(self.payload_ref) if self.payload_ref else None)
        if self.value_identity is not None and not isinstance(self.value_identity, Mapping):
            raise CitedWriterError(
                "CitedFactEntry.value_identity 只能是 mapping 或 None",
                reason="value_identity_not_mapping")
        object.__setattr__(self, "value_identity",
                           dict(self.value_identity) if self.value_identity else None)

    @property
    def key(self) -> tuple[str, str, str]:
        """唯一的权威事实坐标 `(authority_kind, container_identity, fact_id)`。"""
        return (self.authority_kind, self.container_identity, self.fact_id)

    @property
    def fact_field(self) -> str:
        """该 kind 自己的 fact 字段名（由 `NS.FACT_FIELD_BY_AUTHORITY_KIND` 唯一映射）。"""
        return NS.FACT_FIELD_BY_AUTHORITY_KIND[self.authority_kind]

    @classmethod
    def from_authority_fact(cls, fact: Any, *, citation_key: str) -> "CitedFactEntry":
        """从写作侧既有的 `AuthorityFactEntry` **确定性**派生一行事实读视图。

        逐字段直取，不做任何推断：缺字段即 fail-closed（说明调用方给的不是权威事实读视图）。
        这样「新链的事实行」与「旧链看到的事实行」在**同一次扫描**上取值，两链不会对同一份
        权威给出不同的期间 / 口径 / 文本。
        """
        missing = [name for name in ("authority_kind", "container_identity", "fact_id", "text",
                                     "topic_id", "aspect_ids", "required", "fact_type",
                                     "period", "scope")
                   if not hasattr(fact, name)]
        if missing:
            raise CitedWriterError(
                f"权威事实读视图缺字段 {missing}（必须来自 `scan_authority` 的 AuthorityFactEntry）",
                reason="authority_fact_unreadable")
        return cls(
            citation_key=citation_key,
            authority_kind=str(fact.authority_kind),
            container_identity=str(fact.container_identity),
            fact_id=str(fact.fact_id),
            text=str(fact.text),
            topic_id=str(fact.topic_id),
            aspect_ids=tuple(str(x) for x in (fact.aspect_ids or ())),
            fact_type=str(fact.fact_type),
            period=str(fact.period),
            scope=str(fact.scope),
            required=bool(fact.required),
            source_identity=str(getattr(fact, "source_identity", "") or ""),
            provenance_identity=str(getattr(fact, "provenance_identity", "") or ""),
            locator_ref=getattr(fact, "locator_ref", None),
            material_id=(str(getattr(fact, "material_id", "") or "") or None),
            payload_ref=getattr(fact, "payload_ref", None),
            value_identity=getattr(fact, "value_identity", None),
        )

    def to_dict(self) -> dict:
        body = {"citation_key": self.citation_key, "authority_kind": self.authority_kind,
                "container_identity": self.container_identity, "fact_id": self.fact_id,
                "text": self.text, "topic_id": self.topic_id,
                "aspect_ids": list(self.aspect_ids), "fact_type": self.fact_type,
                "period": self.period, "scope": self.scope, "required": self.required,
                "source_identity": self.source_identity,
                "provenance_identity": self.provenance_identity,
                "locator_ref": dict(self.locator_ref) if self.locator_ref else None,
                "material_id": self.material_id,
                "payload_ref": dict(self.payload_ref) if self.payload_ref else None}
        #: **条件写出**：`None` 时整条键缺席。写入清单的身份体就是这里，于是「本来没有逐值
        #: 身份」的历史事实行逐字保持原样——`cwm-7` / `cwp-1` 不必升版，盘上历史的
        #: `manifest_id` 仍能按它自己写时的字段集复验通过。
        if self.value_identity is not None:
            body["value_identity"] = dict(self.value_identity)
        return body

    def identity_body(self) -> dict:
        return self.to_dict()


# ---------------------------------------------------------------------------
# 输入清单
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitedWriterInputManifest:
    """一次新写作的**完整输入面**（`cwm-1`）：材料原文 + 事实读视图 + 小节要求。

    成员集合**逐条等于**本次 `VerifiedPackSet` 的精确材料清单（由
    `pack_writer._derive_material_manifest` 派生），少一份（漏解析）或多一份（来自别的
    PackSet）都 fail-closed——不做「按需裁剪」。
    """

    manifest_id: str
    schema_version: str
    task_id: str
    section_id: str
    section_title: str
    policy_version: str
    pack_set_fingerprint: str
    material_manifest_id: str
    material_manifest_fingerprint: str
    material_context_id: str
    material_context_fingerprint: str
    subsections: tuple[CitedSubsectionSpec, ...]
    materials: tuple[CitedMaterialEntry, ...]
    facts: tuple[CitedFactEntry, ...]
    #: **呈现层**路由声明（`cwm-4`；财务节才有，其他节为 `None`）。
    #:
    #: 它说的是「本节每一个指标事实可以出现在哪一栏」，来源标记由声明自己带
    #: （`source = presentation_layer_declaration`）。三件事因此可核验而不靠约定：
    #:
    #: * 它与 `CitedFactEntry.aspect_ids` 是**两条轴**：后者是权威登记，本字段是呈现层声明。
    #:   本字段**不**回写 `aspect_ids`——把路由写进登记轴，等于替权威认领栏目。
    #: * 它进身份体：换一份路由就是换了一份输入（同 `cwm-3` 的理由）。
    #: * 它**不**证明任何一栏的 Contract 要求已满足；未被任何指标落到的栏只能留缺口。
    presentation_routing: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.presentation_routing is not None:
            if not isinstance(self.presentation_routing, Mapping):
                raise CitedWriterError(
                    "CitedWriterInputManifest.presentation_routing 只能是 mapping 或 None")
            #: 规范形由本类自己钉死（JSON round-trip + 键排序），因此「同一份声明」不同来源
            #: 构造出来的身份是同一个；不规范化会让字典插入顺序进哈希。
            try:
                normalized = json.loads(json.dumps(dict(self.presentation_routing),
                                                   ensure_ascii=False, sort_keys=True))
            except (TypeError, ValueError) as exc:
                raise CitedWriterError(
                    f"presentation_routing 必须是 JSON-safe 的 mapping：{exc}") from exc
            object.__setattr__(self, "presentation_routing", normalized)

        if self.schema_version != CITED_WRITER_MANIFEST_SCHEMA_VERSION:
            raise CitedWriterError(
                f"CitedWriterInputManifest.schema_version 必须为 "
                f"{CITED_WRITER_MANIFEST_SCHEMA_VERSION!r}")
        if self.policy_version != CITED_WRITER_POLICY_VERSION:
            raise CitedWriterError(
                f"CitedWriterInputManifest.policy_version 必须为 "
                f"{CITED_WRITER_POLICY_VERSION!r}")
        for name in ("task_id", "section_id", "section_title", "pack_set_fingerprint",
                     "material_manifest_id", "material_manifest_fingerprint",
                     "material_context_id", "material_context_fingerprint"):
            if not str(getattr(self, name) or "").strip():
                raise CitedWriterError(f"CitedWriterInputManifest.{name} 必须非空")
        for name in ("material_manifest_fingerprint", "material_context_fingerprint"):
            if not NS._is_sha256_hex(getattr(self, name)):
                raise CitedWriterError(
                    f"CitedWriterInputManifest.{name} 必须是 64 位 sha256 hex")
        materials = tuple(self.materials or ())
        facts = tuple(self.facts or ())
        subsections = tuple(self.subsections or ())
        for item, want in ((materials, CitedMaterialEntry), (facts, CitedFactEntry),
                           (subsections, CitedSubsectionSpec)):
            for entry in item:
                if not isinstance(entry, want):
                    raise CitedWriterError(
                        f"CitedWriterInputManifest 的成员只能是 {want.__name__}，"
                        f"得到 {type(entry).__name__}")
        object.__setattr__(self, "materials", materials)
        object.__setattr__(self, "facts", facts)
        object.__setattr__(self, "subsections", subsections)
        self._check_key_uniqueness(materials, facts)
        self._check_key_order(materials, facts)
        self._check_fact_coordinates(facts)
        ids = [s.subsection_id for s in subsections]
        if len(set(ids)) != len(ids):
            raise CitedWriterError("CitedWriterInputManifest 含重复小节 id")
        expected_id = NS.content_id("cwm_", self.identity_body())
        if self.manifest_id != expected_id:
            raise CitedWriterError(
                f"CitedWriterInputManifest.manifest_id 与内容不符：声明 {self.manifest_id!r}，"
                f"应为 {expected_id!r}", reason="manifest_id_mismatch")

    @staticmethod
    def _check_key_uniqueness(materials: Sequence[CitedMaterialEntry],
                              facts: Sequence[CitedFactEntry]) -> None:
        keys = [m.citation_key for m in materials] + [f.citation_key for f in facts]
        if len(set(keys)) != len(keys):
            raise CitedWriterError(
                "CitedWriterInputManifest 含重复引用键（键的命名空间由本清单独占）",
                reason="citation_key_duplicate")

    @staticmethod
    def _check_key_order(materials: Sequence[CitedMaterialEntry],
                         facts: Sequence[CitedFactEntry]) -> None:
        """键必须按清单序**连续**编号：`m01, m02, …` / `f01, f02, …`。

        连续性不是审美：请求面里键是模型唯一的回指手段，一旦允许空洞（`m01, m03`），
        「模型引用了一个不存在的 m02」与「本清单本来就没有 m02」就无法从键本身区分，
        引用真实性只能靠回查字典——那正是本清单要消掉的不确定性。
        """
        for axis, items, builder in (("material", materials, cited_material_key),
                                     ("fact", facts, cited_fact_key)):
            expected = [builder(i) for i in range(len(items))]
            actual = [x.citation_key for x in items]
            if actual != expected:
                raise CitedWriterError(
                    f"{axis} 轴引用键必须按清单序连续编号：得到 {actual}，应为 {expected}",
                    reason="citation_key_gap")

    @staticmethod
    def _check_fact_coordinates(facts: Sequence[CitedFactEntry]) -> None:
        keys = [f.key for f in facts]
        if len(set(keys)) != len(keys):
            raise CitedWriterError(
                "权威事实坐标重复（同一 (kind, container, fact) 只能有一条）",
                reason="fact_coordinate_duplicate")

    def material_keys(self) -> tuple[str, ...]:
        return tuple(m.citation_key for m in self.materials)

    def fact_keys(self) -> tuple[str, ...]:
        return tuple(f.citation_key for f in self.facts)

    def all_keys(self) -> tuple[str, ...]:
        return self.material_keys() + self.fact_keys()

    def material_for_key(self, key: str) -> CitedMaterialEntry | None:
        for entry in self.materials:
            if entry.citation_key == key:
                return entry
        return None

    def fact_for_key(self, key: str) -> CitedFactEntry | None:
        for entry in self.facts:
            if entry.citation_key == key:
                return entry
        return None

    def identity_body(self) -> dict:
        """身份体（JSON-safe）：**唯一**实现，构造与读回共用（见 `cited_input_identity_body`）。"""
        return cited_input_identity_body(
            schema_version=self.schema_version, task_id=self.task_id,
            section_id=self.section_id, section_title=self.section_title,
            policy_version=self.policy_version,
            pack_set_fingerprint=self.pack_set_fingerprint,
            material_manifest_id=self.material_manifest_id,
            material_manifest_fingerprint=self.material_manifest_fingerprint,
            material_context_id=self.material_context_id,
            material_context_fingerprint=self.material_context_fingerprint,
            subsections=self.subsections, materials=self.materials, facts=self.facts,
            presentation_routing=self.presentation_routing)

    def fingerprint(self) -> str:
        return hashlib.sha256(
            NS.canonical_json(self.identity_body()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {"manifest_id": self.manifest_id,
                "manifest_fingerprint": self.fingerprint(),
                "schema_version": self.schema_version, "task_id": self.task_id,
                "section_id": self.section_id, "section_title": self.section_title,
                "policy_version": self.policy_version,
                "pack_set_fingerprint": self.pack_set_fingerprint,
                "material_manifest_id": self.material_manifest_id,
                "material_manifest_fingerprint": self.material_manifest_fingerprint,
                "material_context_id": self.material_context_id,
                "material_context_fingerprint": self.material_context_fingerprint,
                "subsections": [s.identity_body() for s in self.subsections],
                # 正文本体**落盘**（读者面与复核面要读同一串字节），但**不进** `identity_body`。
                "materials": [m.to_dict() for m in self.materials],
                "facts": [f.to_dict() for f in self.facts],
                "presentation_routing": self.presentation_routing}

    @classmethod
    def create(cls, **kwargs: Any) -> "CitedWriterInputManifest":
        """构造入口：身份体**唯一**由 `cited_input_identity_body` 产出（与读回同一实现）。"""
        parts = {
            "task_id": str(kwargs.get("task_id", "") or ""),
            "section_id": str(kwargs.get("section_id", "") or ""),
            "section_title": str(kwargs.get("section_title", "") or ""),
            "pack_set_fingerprint": str(kwargs.get("pack_set_fingerprint", "") or ""),
            "material_manifest_id": str(kwargs.get("material_manifest_id", "") or ""),
            "material_manifest_fingerprint": str(
                kwargs.get("material_manifest_fingerprint", "") or ""),
            "material_context_id": str(kwargs.get("material_context_id", "") or ""),
            "material_context_fingerprint": str(
                kwargs.get("material_context_fingerprint", "") or ""),
            "subsections": tuple(kwargs.get("subsections") or ()),
            "materials": tuple(kwargs.get("materials") or ()),
            "facts": tuple(kwargs.get("facts") or ()),
            "presentation_routing": kwargs.get("presentation_routing")}
        body = {"schema_version": CITED_WRITER_MANIFEST_SCHEMA_VERSION,
                "policy_version": CITED_WRITER_POLICY_VERSION, **parts}
        body["manifest_id"] = NS.content_id("cwm_", cited_input_identity_body(**body))
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "CitedWriterInputManifest":
        d = NS._reject_unknown(
            d, {"manifest_id", "manifest_fingerprint", "schema_version", "task_id",
                "section_id", "section_title", "policy_version", "pack_set_fingerprint",
                "material_manifest_id", "material_manifest_fingerprint", "material_context_id",
                "material_context_fingerprint", "subsections", "materials", "facts",
                "presentation_routing"},
            "CitedWriterInputManifest")
        manifest = cls(
            manifest_id=str(d.get("manifest_id") or ""),
            schema_version=d.get("schema_version"),
            task_id=str(d.get("task_id") or ""),
            section_id=str(d.get("section_id") or ""),
            section_title=str(d.get("section_title") or ""),
            policy_version=d.get("policy_version"),
            pack_set_fingerprint=str(d.get("pack_set_fingerprint") or ""),
            material_manifest_id=str(d.get("material_manifest_id") or ""),
            material_manifest_fingerprint=str(d.get("material_manifest_fingerprint") or ""),
            material_context_id=str(d.get("material_context_id") or ""),
            material_context_fingerprint=str(d.get("material_context_fingerprint") or ""),
            subsections=tuple(CitedSubsectionSpec(**s) for s in (d.get("subsections") or ())),
            materials=tuple(_material_from_dict(x) for x in (d.get("materials") or ())),
            facts=tuple(_fact_from_dict(x) for x in (d.get("facts") or ())),
            presentation_routing=d.get("presentation_routing"))
        declared = d.get("manifest_fingerprint")
        if declared is not None and str(declared) != manifest.fingerprint():
            raise CitedWriterError(
                f"CitedWriterInputManifest.manifest_fingerprint 与内容不符：声明 {declared!r}，"
                f"实际 {manifest.fingerprint()!r}", reason="manifest_fingerprint_mismatch")
        return manifest


def _material_from_dict(d: Any) -> CitedMaterialEntry:
    d = NS._reject_unknown(d, set(CitedMaterialEntry.__dataclass_fields__),
                           "CitedMaterialEntry")
    return CitedMaterialEntry(**d)


def _fact_from_dict(d: Any) -> CitedFactEntry:
    d = NS._reject_unknown(d, set(CitedFactEntry.__dataclass_fields__), "CitedFactEntry")
    if not isinstance(d.get("aspect_ids", ()), (list, tuple)):
        raise CitedWriterError("CitedFactEntry.aspect_ids 必须是数组")
    return CitedFactEntry(**{**d, "aspect_ids": tuple(d.get("aspect_ids") or ())})


def cited_input_identity_body(*, schema_version: str, task_id: str, section_id: str,
                              section_title: str, policy_version: str,
                              pack_set_fingerprint: str, material_manifest_id: str,
                              material_manifest_fingerprint: str, material_context_id: str,
                              material_context_fingerprint: str,
                              subsections: Sequence[Any], materials: Sequence[Any],
                              facts: Sequence[Any],
                              presentation_routing: Mapping[str, Any] | None = None) -> dict:
    """输入清单身份体（JSON-safe）：`create()` 与 `identity_body()` 的**同一**实现。

    两处各写一遍的后果是「构造时算的 id」与「读回时校验的 id」在字段增删后悄悄分叉，而分叉
    的表现是「同一份清单有时可构造、有时不可」——本模块把这条实现收在一处。
    """
    return {
        "schema_version": schema_version, "task_id": task_id, "section_id": section_id,
        "section_title": section_title, "policy_version": policy_version,
        "pack_set_fingerprint": pack_set_fingerprint,
        "material_manifest_id": material_manifest_id,
        "material_manifest_fingerprint": material_manifest_fingerprint,
        "material_context_id": material_context_id,
        "material_context_fingerprint": material_context_fingerprint,
        "subsections": [s.identity_body() for s in subsections],
        "materials": [m.identity_body() for m in materials],
        "facts": [f.identity_body() for f in facts],
        #: 呈现层路由声明（`cwm-4`）。`None` 与「空声明」**不是**同一件事：前者是「本节没有
        #: 这条轴」（公司节），后者是「有声明但一条映射也没有」（那不成立，见该字段的注释）。
        #: 因此这里原样保留 `None`，不减成一个空 dict。
        "presentation_routing": (None if presentation_routing is None
                                 else json.loads(json.dumps(dict(presentation_routing),
                                                            ensure_ascii=False,
                                                            sort_keys=True))),
    }


# ---------------------------------------------------------------------------
# 唯一构造入口：既有 PackSet / 精确清单 / 精确正文 → 输入清单
# ---------------------------------------------------------------------------

#: 请求面上材料的**呈现优先序**（`cp-18`）。角色取值取自 `harness.source_manifest.SOURCE_ROLES`
#: 这一个封闭词表，不在这里另造角色名。
#:
#: 它是**呈现**序，不是资格序、不是相关性判断：它只决定「生成器先看到哪一类来源」，**不**改
#: 任何一行材料的可用性，**不**删任何一行，**不**放宽任何判据（登记仍是必要条件，`scp-3`／
#: `scp-5` 照判）。为什么要这一条：提示词第 3c 条要求「先按文档分组、当前态取最新一期」，
#: 而 cp-17 的请求面把材料按**身份序**（hash 序）交错摆开——2025 年报里最完整的那一段
#: （1034 字，登记到全部 7 栏）落在 65 行之末。「按文档分组」这一步因此在请求面上**不可执行**，
#: 只能靠生成器自己从 65 行里分组。这与 `cp-17` 的 `citable_columns` 取代「18 栏 × 65 行求交」
#: 是同一类修法：把提示词要求的那一步在请求面**做出来**，而不是让它留给生成器猜。
#:
#: `history_and_conflict_source` **排在最后**：按来源角色判据它本来就**不能**单独表达当前状态
#: （`source_role_scope.CANNOT_ESTABLISH_CURRENT_STATE_ROLES`），把它摆在最前面只会诱导
#: 生成器拿旧料起头——那正是 `history_material_as_current_state` 这条硬错误的入口。
MATERIAL_ROLE_PRESENTATION_ORDER = (
    "current_state_source",
    "topic_participating_source",
    "history_and_conflict_source",
    "not_used",
)


def material_presentation_order(
        entries: Sequence["CitedMaterialEntry"]) -> "tuple[CitedMaterialEntry, ...]":
    """把材料行排成请求面上的**呈现序**（`cp-18`）；只重排，不改行内容、不丢行。

    排序键，逐轴给出理由：

    * **来源角色**（:data:`MATERIAL_ROLE_PRESENTATION_ORDER` 的位次）——当前态来源先看；
      未在该表内的角色一律排到表尾，按角色名字典序兜底（出现未知角色时仍是全序，不报错
      ——角色是不是合法由 `srsc-1` 那条轴判，不在这里换一件事判）；
    * **文档身份** `document_id` 升序——同一份来源文档的材料连成一块，这就是提示词第 3c 条
      说的「按文档分组」。文档身份**本身不含新旧主张**：新旧由上面那条角色轴承担，这里不拿
      文件名去猜年次；
    * **材料正文字数降序**——同一文档内先摆内容更完整的那一段。这是**可用性代理**，不是相关
      性判断：替身与生成器都拿不到相关性分值，而「先看到 1034 字还是先看到 2 字」是它们真
      能感觉到的差别。字数相同按原序（`index`）兜底，保证**确定性**：同一份清单任何时候排出
      同一个序。
    """
    rank = {role: i for i, role in enumerate(MATERIAL_ROLE_PRESENTATION_ORDER)}
    fallback = len(MATERIAL_ROLE_PRESENTATION_ORDER)

    def key(item: tuple[int, "CitedMaterialEntry"]) -> tuple:
        index, entry = item
        role = str(entry.source_role or "")
        return (rank.get(role, fallback), role, str(entry.document_id or ""),
                -len(str(entry.reading_view or "")), index)

    return tuple(entry for _, entry in sorted(enumerate(entries), key=key))


def build_cited_writer_input(*, authority: Any, material_context: Any,
                             subsections: Sequence[CitedSubsectionSpec],
                             facts: Sequence[Any] = (),
                             section_title: str = "",
                             presentation_routing: Mapping[str, Any] | None = None,
                             ) -> CitedWriterInputManifest:
    """本节权威 + `WriterMaterialContext` + 合格事实读视图 → 输入清单（**唯一**入口）。

    「清单不等于 Pack 成员 ⇒ fail-closed」这条约束**不在本函数里另写一遍**：它复用
    `pack_writer._derive_material_manifest`（`wmm-2` 起成员身份已含真实 payload 引用 / exact
    locator / payload 哈希 / 读视图指纹，因此「成员存在」与「正文已解析」在构造期就是同一件
    事），再与 `material_context` 逐成员对账。两处若各写一份，第二处就会成为唯一的真值。

    `material_context` 是**本节权威的材料边界**的正文侧，两支各自合法：
    topic Pack 权威 → `material_context.resolve_writer_material_context`（逐份材料正文）；
    其他 producer kind（financial/note/external）→ `material_context.empty_material_context_for_authority`
    （**空集**，因为该权威本来就没有 exact `ResearchMaterial` 边界）。清单里的
    `pack_set_fingerprint` 取两支**共用**的 `MC.authority_material_boundary_fingerprint`，
    所以「上下文属于哪一份权威」在清单一侧是可复核的，而不是靠约定。

    `facts` 接受写作侧既有的 `AuthorityFactEntry` 序列（`scan_authority` 的产物）；逐条
    `CitedFactEntry.from_authority_fact` 直取字段，**不**在此重新解释权威。
    """
    from sections import material_context as MC  # 延迟导入：避免 sections 内部循环
    from sections import pack_writer as PW     # 延迟导入：同一份清单派生实现（无环）

    if authority is None:
        raise CitedWriterError(
            "新写作链必须拿到本节的权威输入（topic Pack 权威或等价的权威读视图）："
            "缺权威时材料清单无从派生，不得降级成「无材料也照样写」",
            reason="authority_missing")
    if material_context is None:
        raise CitedWriterError(
            "缺材料正文上下文（`wmctx-1`）：Writer 不得在只拿到 material ID 的情况下写作",
            reason="material_context_missing")
    if not isinstance(material_context, MC.WriterMaterialContext):
        raise CitedWriterError(
            f"material_context 必须是真实 `WriterMaterialContext`，得到 "
            f"{type(material_context).__name__}（字段形状替身一律不得进入权威输入）",
            reason="material_context_not_real")

    manifest = PW._derive_material_manifest(authority, material_context=material_context)
    roles = PW._material_source_roles(authority=authority, manifest=manifest)
    #: 来源**文档**身份：与 `srsc-1` 的台账**同一**实现（`_support_documents` 读的同样是
    #: `payload_ref.locator.document_id`）。逐句判「旧材料是否被写成当前状态」要的是这一轴，
    #: 不是文件名——因此这里不解析命名、不推断，读不到就是空串（这条轴上本来没有文档系列）。
    documents = PW._support_documents(manifest=manifest)
    #: 材料侧的**栏目归属**轴（Pack 侧登记，不由本模块解释）：逐句栏目核对要比的就是它。
    #: 它与 `documents` / `roles` 同一纪律——读不出来就 fail-closed（下面显式查缺），
    #: 不静默退化成「这份材料没有归属」。
    aspect_registrations = PW._material_aspect_registrations(authority=authority,
                                                             manifest=manifest)

    by_ref = {m.member_ref: m for m in material_context.materials}
    material_entries: list[CitedMaterialEntry] = []
    for index, entry in enumerate(manifest.entries):
        resolved = by_ref.get(entry.member_ref)
        if resolved is None:
            raise CitedWriterError(
                f"精确清单成员 {entry.member_ref!r} 在正文上下文里没有对应记录："
                "「清单里有 ID」不等于「正文在场」，fail-closed",
                reason="member_not_in_context")
        if resolved.reading_view_fingerprint != entry.reading_view_fingerprint:
            raise CitedWriterError(
                f"成员 {entry.member_ref!r} 的读视图指纹与清单不符："
                f"{resolved.reading_view_fingerprint!r} != {entry.reading_view_fingerprint!r}",
                reason="member_reading_view_mismatch")
        material_entries.append(CitedMaterialEntry(
            citation_key=cited_material_key(index),
            member_ref=entry.member_ref, pack_id=entry.pack_id,
            material_id=entry.material_id, topic_id=entry.topic_id,
            material_type=entry.material_type, source_identity=entry.source_identity,
            provenance_identity=entry.provenance_identity,
            source_role=str(roles.get(entry.member_ref, "") or ""),
            document_id=str(documents.get(entry.member_ref, "") or ""),
            aspect_ids=tuple(aspect_registrations.get(entry.member_ref, ()) or ()),
            locator_ref=dict(entry.locator_ref), payload_hash=entry.payload_hash,
            reading_view=resolved.reading_view,
            reading_view_fingerprint=resolved.reading_view_fingerprint,
            structured_view=resolved.structured_view,
            content_qualification=resolved.content_qualification,
            span_continuity=resolved.span_continuity))

    #: **呈现序**（`cp-18`）：清单按呈现序构造，因此请求面与清单一序——「模型看到的那一列」
    #: 与「清单身份里那一列」不是两回事。凡是依赖清单序的东西都跟着走，而不是各自再排一遍。
    #: 排序只读已经摆在本函数里的三个轴（来源角色 / 文档身份 / 读视图长度），并**重编引用键**
    #: ——键的定义本来就是「按清单序连续编号」（:meth:`CitedWriterInputManifest._check_key_order`），
    #: 序变了而键不重编，键就不再能当回指手段用。详见 :func:`material_presentation_order`。
    material_entries = [
        replace(entry, citation_key=cited_material_key(index))
        for index, entry in enumerate(material_presentation_order(material_entries))]

    fact_entries = tuple(
        CitedFactEntry.from_authority_fact(fact, citation_key=cited_fact_key(index))
        for index, fact in enumerate(facts or ()))

    return CitedWriterInputManifest.create(
        task_id=str(getattr(authority, "task_id", "") or ""),
        section_id=str(getattr(authority, "section_id", "") or ""),
        section_title=section_title or str(getattr(authority, "section_id", "") or ""),
        pack_set_fingerprint=MC.authority_material_boundary_fingerprint(authority),
        material_manifest_id=manifest.manifest_id,
        material_manifest_fingerprint=manifest.fingerprint(),
        material_context_id=material_context.context_id,
        material_context_fingerprint=material_context.fingerprint(),
        subsections=tuple(subsections or ()), materials=tuple(material_entries),
        facts=fact_entries, presentation_routing=presentation_routing)


# ---------------------------------------------------------------------------
# 正文草稿（`cw-1`）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitedSentence:
    """一个自然句：文本 + 它引用本清单里的哪些键。

    **空引用是合法的 wire 取值**（否则模型漏一个引用就会让整节无法构造），由第二步的逐句核对
    判 `uncited_sentence` —— 这样「一处错误只标记那一句」在**结构层**就成立，而不是靠下游
    小心翼翼地不清零。
    """

    sentence_id: str
    text: str
    citations: tuple[str, ...] = ()
    numeric_tokens: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.sentence_id or "").strip():
            raise CitedWriterError("CitedSentence.sentence_id 必须非空")
        if not str(self.text or "").strip():
            raise CitedWriterError(
                f"句子 {self.sentence_id!r} 的文本为空", reason="empty_sentence",
                sentence_id=str(self.sentence_id or ""))
        citations = tuple(str(x) for x in (self.citations or ()))
        for key in citations:
            citation_key_axis(key)
        if len(set(citations)) != len(citations):
            raise CitedWriterError(
                f"句子 {self.sentence_id!r} 的引用键重复", reason="citation_key_duplicate",
                sentence_id=self.sentence_id)
        object.__setattr__(self, "citations", citations)
        scanned = NS.scan_numeric_tokens(self.text)
        declared = tuple(str(x) for x in (self.numeric_tokens or ()))
        if declared and declared != scanned:
            raise CitedWriterError(
                f"句子 {self.sentence_id!r} 声明的 numeric_tokens 与文本扫描结果不符："
                f"声明 {declared}，扫描 {scanned}（数字必须由文本本身决定）",
                reason="numeric_tokens_mismatch", sentence_id=self.sentence_id)
        object.__setattr__(self, "numeric_tokens", scanned)

    def to_dict(self) -> dict:
        return {"sentence_id": self.sentence_id, "text": self.text,
                "citations": list(self.citations),
                "numeric_tokens": list(self.numeric_tokens)}

    def identity_body(self) -> dict:
        return self.to_dict()


@dataclass(frozen=True)
class CitedParagraph:
    """一个自然段：若干句（句序即读者面序）+ 本段**声明服务**的 Contract 栏目（`cw-4`）。

    `aspect_ids` 是本段自己的**内容计划**声明——「这一段是用来写哪几栏的」。它是**组织**声明，
    不是**支持**声明：一段声明它服务 `app_scenarios`，并不证明段里的句子被材料支持（那是逐句
    机械核对与独立审阅各自的事），也不证明那一栏的 Contract 要求已被满足（那是覆盖账的事）。
    它唯一的作用，是把「一个小节覆盖十几栏」这件在集结结构上无法逐句分辨的事，落到**可核对**
    的段级声明上：段的声明集合必须与本段所引材料的**登记**归属相交，否则读者会把「拿 A 栏的
    材料写 B 栏」读成「B 栏已写出来」。

    空 tuple **合法**且有意义：本段不对应任何 Contract 栏目（例如纯背景/衔接段）。它既不是
    「读不到」，也不是「漏声明」——与引用为空的句子同一条纪律。
    """

    paragraph_id: str
    sentences: tuple[CitedSentence, ...] = ()
    aspect_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.paragraph_id or "").strip():
            raise CitedWriterError("CitedParagraph.paragraph_id 必须非空")
        sentences = tuple(self.sentences or ())
        if not sentences:
            raise CitedWriterError(
                f"段落 {self.paragraph_id!r} 没有任何句子", reason="empty_paragraph")
        for s in sentences:
            if not isinstance(s, CitedSentence):
                raise CitedWriterError(
                    f"段落 {self.paragraph_id!r} 的成员只能是 CitedSentence，"
                    f"得到 {type(s).__name__}")
        object.__setattr__(self, "sentences", sentences)
        aspects = tuple(str(a or "").strip() for a in (self.aspect_ids or ()))
        if any(not a for a in aspects):
            raise CitedWriterError(
                f"段落 {self.paragraph_id!r} 的 aspect_ids 不得含空串")
        if len(set(aspects)) != len(aspects):
            raise CitedWriterError(
                f"段落 {self.paragraph_id!r} 的 aspect_ids 不得有重复项")
        object.__setattr__(self, "aspect_ids", aspects)

    def sentence_ids(self) -> tuple[str, ...]:
        return tuple(s.sentence_id for s in self.sentences)

    def text(self) -> str:
        return "".join(s.text for s in self.sentences)

    def to_dict(self) -> dict:
        return {"paragraph_id": self.paragraph_id,
                "aspect_ids": list(self.aspect_ids),
                "sentences": [s.to_dict() for s in self.sentences]}


@dataclass(frozen=True)
class CitedSubsection:
    """一个小节：标题 + 若干自然段。`subsection_id` 必须回指请求面里的同 id 小节。"""

    subsection_id: str
    title: str
    paragraphs: tuple[CitedParagraph, ...] = ()

    def __post_init__(self) -> None:
        for name in ("subsection_id", "title"):
            if not str(getattr(self, name) or "").strip():
                raise CitedWriterError(f"CitedSubsection.{name} 必须非空")
        paragraphs = tuple(self.paragraphs or ())
        for p in paragraphs:
            if not isinstance(p, CitedParagraph):
                raise CitedWriterError(
                    f"小节 {self.subsection_id!r} 的成员只能是 CitedParagraph，"
                    f"得到 {type(p).__name__}")
        object.__setattr__(self, "paragraphs", paragraphs)

    def sentences(self) -> tuple[CitedSentence, ...]:
        return tuple(s for p in self.paragraphs for s in p.sentences)

    def sentence_ids(self) -> tuple[str, ...]:
        return tuple(s.sentence_id for s in self.sentences())

    def to_dict(self) -> dict:
        return {"subsection_id": self.subsection_id, "title": self.title,
                "paragraphs": [p.to_dict() for p in self.paragraphs]}


@dataclass(frozen=True)
class CitedProseGap:
    """新写作链的**缺口**：这一次写不出来什么、为什么。

    缺口**不是** `not_used`：某份材料没被用上是**结论**（它对本小节的要求没有用），不是缺口。
    只有「本小节要求的东西在本次输入里根本拿不到（或拿到了但不可用）」才形成缺口。

    `reason` 由系统判定（`cw-3`；见 :func:`assign_gap_reason`），`reason_assignment` 记下它是
    怎么定下来的，`claimed_reason` 原样留住生成器当时写的那一个。三者都进产物，因为「这一栏
    没有材料」与「这一栏有材料、只是没有合格的数字事实」对下游是两条完全不同的指令。
    """

    gap_id: str
    subsection_id: str
    requirement_text: str
    reason: str
    detail: str = ""
    #: 判定来源（封闭三值，见 :data:`CITED_GAP_REASON_ASSIGNMENTS`）。**不进身份体**：它是
    #: 这句话**怎么来的**，不是这句话**是什么**。
    reason_assignment: str = "writer_declared"
    #: 生成器当时自述的理由（原话，只作审计）。空串是**结论**——旧记录里没有这一轴——不是
    #: 「生成器没说」。它与 `reason` 不同时，`reason_assignment` 必为
    #: `system_reassigned_from_manifest`。
    claimed_reason: str = ""

    def __post_init__(self) -> None:
        for name in ("subsection_id", "requirement_text", "reason"):
            if not str(getattr(self, name) or "").strip():
                raise CitedWriterError(f"CitedProseGap.{name} 必须非空")
        if self.reason not in CITED_GAP_REASONS:
            raise CitedWriterError(
                f"CitedProseGap.reason={self.reason!r} 不在封闭词表 "
                f"{list(CITED_GAP_REASONS)} 内", reason="gap_reason_unknown")
        if self.reason_assignment not in CITED_GAP_REASON_ASSIGNMENTS:
            raise CitedWriterError(
                f"CitedProseGap.reason_assignment={self.reason_assignment!r} 不在 "
                f"{list(CITED_GAP_REASON_ASSIGNMENTS)} 内", reason="gap_reason_assignment_unknown")
        if self.reason_assignment == "writer_declared" and self.claimed_reason \
                and self.claimed_reason != self.reason:
            raise CitedWriterError(
                f"CitedProseGap.claimed_reason={self.claimed_reason!r} 与 reason={self.reason!r} "
                f"不同，却标成 writer_declared（改判必须留下判定来源）",
                reason="gap_reason_assignment_mismatch")
        expected = derive_gap_id(self)
        if self.gap_id != expected:
            raise CitedWriterError(
                f"CitedProseGap.gap_id 与内容不符：声明 {self.gap_id!r}，应为 {expected!r}",
                reason="gap_id_mismatch")

    def identity_body(self) -> dict:
        return {"subsection_id": self.subsection_id,
                "requirement_text": self.requirement_text,
                "reason": self.reason, "detail": self.detail}

    def to_dict(self) -> dict:
        return {"gap_id": self.gap_id, **self.identity_body(),
                "reason_assignment": self.reason_assignment,
                "claimed_reason": self.claimed_reason}

    @classmethod
    def create(cls, **kwargs: Any) -> "CitedProseGap":
        body = {"subsection_id": str(kwargs.get("subsection_id", "") or ""),
                "requirement_text": str(kwargs.get("requirement_text", "") or ""),
                "reason": str(kwargs.get("reason", "") or ""),
                "detail": str(kwargs.get("detail", "") or "")}
        return cls(gap_id=NS.content_id("cgap_", body), **body,
                   reason_assignment=str(
                       kwargs.get("reason_assignment", "") or "writer_declared"),
                   claimed_reason=str(kwargs.get("claimed_reason", "") or ""))

    @classmethod
    def from_dict(cls, d: Any) -> "CitedProseGap":
        d = NS._reject_unknown(d, {"gap_id", "subsection_id", "requirement_text", "reason",
                                   "detail", "reason_assignment", "claimed_reason"},
                               "CitedProseGap")
        return cls(gap_id=str(d.get("gap_id") or ""),
                   subsection_id=str(d.get("subsection_id") or ""),
                   requirement_text=str(d.get("requirement_text") or ""),
                   reason=str(d.get("reason") or ""), detail=str(d.get("detail") or ""),
                   reason_assignment=str(d.get("reason_assignment") or "writer_declared"),
                   claimed_reason=str(d.get("claimed_reason") or ""))


def derive_gap_id(gap: Any) -> str:
    return NS.content_id("cgap_", gap.identity_body())


@dataclass(frozen=True)
class CitedFollowUpNeed:
    """新写作链的**补件需求**：需要更多材料时的**独立身份**。

    **为什么不复用 `harness.topic_schema.FollowUpNeed`**：那个对象的必填字段里有一条
    `section_draft_revision`——它绑的是旧链的 `SectionDraft` 修订号。新链**不产生** `SectionDraft`
    （§0.20 明文要求新正文不得塞进旧 wire），于是「绑定一个不存在的修订号」是做不到的：
    要么新链伪造一个旧 wire 身份，要么这里另立一个绑新正文修订号的同语义身份。选后者，并把
    这条边界写在此处：**本对象只登记，不裁决、不执行**（面试版只读展示状态与信息缺口），
    任何 Harness 侧的 `FollowUpNeed` 执行路径都不读它。

    `expected_source_classes` **由系统推导**（`cw-3`；见 :func:`derive_expected_source_classes`），
    生成器不得自选。理由不是洁癖：这个字段一旦被下游当成**取材指令**，它写错一个类别就等于
    把下一次检索直接指挥到错误的方向。而它的正确取值在本次输入里已经存在——冻结 Contract
    逐栏声明了允许来源（`CitedSubsectionSpec.allowed_source_classes`），生成器只是没被要求
    去读它。生成器当时自述的那一个原样留在 `writer_claimed_source_class` 里作审计。
    """

    need_id: str
    statement: str
    subsection_id: str
    target_requirement_text: str
    topic_id: str
    aspect_id: str
    section_id: str
    prose_revision: str
    requiredness: str
    writer_identity: str
    #: 该 Contract 栏目允许的检索来源类（**系统推导**，取自本小节规格；见类注释）。
    expected_source_classes: tuple[str, ...] = ()
    #: 这条推导**是怎么来的**（封闭三值，见 :data:`CITED_SOURCE_CLASS_DERIVATIONS`）。
    source_class_derivation: str = "aspect_not_in_manifest"
    #: 生成器当时自述的来源类（原话，只作审计，**不**进身份体，也**不**被下游当指令读）。
    writer_claimed_source_class: str = ""

    def __post_init__(self) -> None:
        for name in ("statement", "subsection_id", "target_requirement_text", "topic_id",
                     "aspect_id", "section_id", "prose_revision", "writer_identity"):
            if not str(getattr(self, name) or "").strip():
                raise CitedWriterError(f"CitedFollowUpNeed.{name} 必须非空")
        if self.requiredness not in CITED_FOLLOW_UP_REQUIREDNESS:
            raise CitedWriterError(
                f"CitedFollowUpNeed.requiredness={self.requiredness!r} 不在 "
                f"{list(CITED_FOLLOW_UP_REQUIREDNESS)} 内")
        classes = tuple(str(c or "") for c in (self.expected_source_classes or ()))
        if any(not c.strip() for c in classes):
            raise CitedWriterError(
                f"CitedFollowUpNeed.expected_source_classes 不得含空串（补件的来源类不能没有名字）")
        if len(set(classes)) != len(classes):
            raise CitedWriterError(
                "CitedFollowUpNeed.expected_source_classes 不得有重复项")
        object.__setattr__(self, "expected_source_classes", classes)
        if self.source_class_derivation not in CITED_SOURCE_CLASS_DERIVATIONS:
            raise CitedWriterError(
                f"CitedFollowUpNeed.source_class_derivation="
                f"{self.source_class_derivation!r} 不在 "
                f"{list(CITED_SOURCE_CLASS_DERIVATIONS)} 内",
                reason="source_class_derivation_unknown")
        if self.source_class_derivation != "aspect_not_in_manifest" and not classes:
            raise CitedWriterError(
                f"CitedFollowUpNeed 声明了推导来源 {self.source_class_derivation!r} 却推不出任何"
                f"来源类：空取值只允许出现在 `aspect_not_in_manifest` 这一档",
                reason="source_class_derivation_mismatch")
        expected = derive_follow_up_need_id(self)
        if self.need_id != expected:
            raise CitedWriterError(
                f"CitedFollowUpNeed.need_id 与内容不符：声明 {self.need_id!r}，"
                f"应为 {expected!r}", reason="need_id_mismatch")

    def identity_body(self) -> dict:
        return {"statement": self.statement, "subsection_id": self.subsection_id,
                "target_requirement_text": self.target_requirement_text,
                "topic_id": self.topic_id, "aspect_id": self.aspect_id,
                "section_id": self.section_id, "prose_revision": self.prose_revision,
                "requiredness": self.requiredness,
                "expected_source_classes": list(self.expected_source_classes),
                "source_class_derivation": self.source_class_derivation,
                "writer_identity": self.writer_identity}

    def to_dict(self) -> dict:
        return {"need_id": self.need_id, **self.identity_body(),
                "writer_claimed_source_class": self.writer_claimed_source_class}

    @classmethod
    def create(cls, **kwargs: Any) -> "CitedFollowUpNeed":
        fields = ("statement", "subsection_id", "target_requirement_text", "topic_id",
                  "aspect_id", "section_id", "prose_revision", "requiredness",
                  "writer_identity", "source_class_derivation")
        body = {name: str(kwargs.get(name, "") or "") for name in fields}
        body["expected_source_classes"] = list(kwargs.get("expected_source_classes") or ())
        return cls(need_id=NS.content_id("cfun_", body), **body,
                   writer_claimed_source_class=str(
                       kwargs.get("writer_claimed_source_class", "") or ""))

    @classmethod
    def from_dict(cls, d: Any) -> "CitedFollowUpNeed":
        d = NS._reject_unknown(d, {"need_id", "statement", "subsection_id",
                                   "target_requirement_text", "topic_id", "aspect_id",
                                   "section_id", "prose_revision", "requiredness",
                                   "expected_source_classes", "source_class_derivation",
                                   "writer_claimed_source_class", "writer_identity"},
                               "CitedFollowUpNeed")
        scalar = {k: str(v or "") for k, v in d.items()
                  if k not in ("need_id", "expected_source_classes")}
        return cls(need_id=str(d.get("need_id") or ""),
                   expected_source_classes=tuple(d.get("expected_source_classes") or ()),
                   **scalar)


def derive_follow_up_need_id(need: Any) -> str:
    return NS.content_id("cfun_", need.identity_body())


# ---------------------------------------------------------------------------
# 判定：生成器说的理由/来源类 → 系统按清单与 Contract 定下来的取值
# ---------------------------------------------------------------------------
#
# 这一节只做**能从本次输入证实或证伪**的那一小部分判断，不做任何语义理解：
#
# * 缺口理由：清单里**有没有**登记到该栏的材料，是能查的；「那批材料够不够写出要求的内容」
#   不是本模块能判的（那是语义审阅的活）。所以这里只反驳一类自述——自称「清单里没有来源」，
#   而清单里明明有登记到该栏的材料。
# * 补件来源类：该栏在冻结 Contract 里声明了哪些允许来源，是**已定**的；生成器不需要、也不
#   应该重新发明一个。这里只是把那个已定的取值取出来。
#
# 两者都**不**触碰冻结 Contract、**不**新增原因码、**不**改身份历史。

def manifest_spec_for_subsection(
        manifest: CitedWriterInputManifest, subsection_id: str) -> CitedSubsectionSpec | None:
    """按 `subsection_id` 取回本小节的规格；取不到返回 `None`（调用方据此标 `uncheckable`）。"""
    for spec in manifest.subsections:
        if spec.subsection_id == subsection_id:
            return spec
    return None


def aspect_for_requirement(
        spec: "CitedSubsectionSpec | None", requirement_text: str) -> str | None:
    """一条缺口的 `requirement_text` **逐字**该归到哪**一个** Contract 栏目上；对不上返回 `None`。

    **为什么需要它**：改判要回答的问题是「**这一栏**在清单里有没有来源」，而一个写作小节现在
    覆盖**多条** Contract 要求（`cwm-6`）。若拿整节的 `declared_aspect_ids` 去数登记数，那么
    「本小节**别处**有材料」就会被读成「**这一栏**有材料」——把两件不同的事记成同一条查得动的
    事实，正是 `no_source_in_manifest` 与 `source_present_but_not_admissible` 要分开的原因。

    依据是规格自己那份**位置对位**（`requirement_text` 的第 i 行 ↔ `declared_aspect_ids` 的第 i 个）。
    它与审阅侧 `cited_review._aspect_requirement_pairs` 用的是**同一份**对位，不新增任何登记轴。

    对不上（行数与栏数不等、或这条文本不逐字等于其中一行）⇒ `None`。调用方据此**保持原有
    较宽口径**（见 :func:`registered_material_keys` 的 `scope` 用法），而不是猜一个栏目：
    「查不出来」与「查出来是 0」在产物上不能长得一样。
    """
    if spec is None:
        return None
    lines = [ln.strip() for ln in str(spec.requirement_text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    aspects = tuple(str(a) for a in (spec.declared_aspect_ids or ()))
    if not lines or len(lines) != len(aspects):
        return None
    wanted = str(requirement_text or "").strip()
    if not wanted:
        return None
    for aspect, line in zip(aspects, lines):
        if line == wanted:
            return aspect
    return None


def registered_material_keys(
        manifest: CitedWriterInputManifest,
        aspect_ids: "str | Sequence[str]") -> tuple[str, ...]:
    """清单里**登记归属**到这些 Contract 栏目**之一**的材料引用键（按清单顺序，不去重不改序）。

    「登记」是 Pack 侧的认领（`CitedMaterialEntry.aspect_ids`），**不是**作者归属，也**不是**
    「这份材料的每一句都回答该栏」。它在这里唯一的用途，是让「清单里有没有这一栏的来源」
    这句话有一个确定的答案。

    入参接受**单个 id 或一组 id**（`cwm-6`：一个写作小节现在覆盖多个 Contract 栏目）。
    传空集合返回空 tuple——那是结论「没有栏目要问」，不是「读不到」。
    """
    if isinstance(aspect_ids, str):
        wanted = (aspect_ids,) if aspect_ids.strip() else ()
    else:
        wanted = tuple(str(a or "").strip() for a in (aspect_ids or ()) if str(a or "").strip())
    if not wanted:
        return ()
    wanted_set = set(wanted)
    return tuple(m.citation_key for m in manifest.materials
                 if wanted_set & set(m.aspect_ids))


def _routed_fact_columns(manifest: CitedWriterInputManifest) -> dict[str, tuple[str, ...]]:
    """`presentation_routing.fact_columns` 里 `fact_id` → 它被声明落到的那一栏（`cwm-4`）。

    财务 / 附注事实的 `aspect_ids` 在权威侧本来就没有这一轴（常常是空 tuple），落栏的唯一依据
    就是这份呈现层声明。这里只取「哪一栏」，不取档位、不做 fail-closed：读不到就是空表，调用方
    据此退回材料口径——「读不出来」与「读出来是 0」在产物上不能长得一样。
    """
    routing = manifest.presentation_routing
    if not isinstance(routing, Mapping):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for row in (routing.get("fact_columns") or ()):
        if not isinstance(row, Mapping):
            continue
        fact_id = str(row.get("fact_id") or "").strip()
        column = str(row.get("presentation_column") or "").strip()
        if fact_id and column:
            out[fact_id] = out.get(fact_id, ()) + (column,)
    return out


def registered_fact_keys(
        manifest: CitedWriterInputManifest,
        aspect_ids: "str | Sequence[str]") -> tuple[str, ...]:
    """清单里**登记归属**到这些 Contract 栏目**之一**的权威事实引用键。

    与 :func:`registered_material_keys` 是同一条问题、另一根轴：材料侧只看 `aspect_ids`，事实侧
    既看事实行自己的 `aspect_ids`，也看呈现层路由（`presentation_routing.fact_columns`）——财务
    节的事实行 `aspect_ids` 常常为空，唯一的落栏依据就是路由，只数前者会把这三种权威一律读成
    「本栏没有来源」。
    """
    if isinstance(aspect_ids, str):
        wanted = (aspect_ids,) if aspect_ids.strip() else ()
    else:
        wanted = tuple(str(a or "").strip() for a in (aspect_ids or ()) if str(a or "").strip())
    if not wanted:
        return ()
    wanted_set = set(wanted)
    routed = _routed_fact_columns(manifest)
    out: list[str] = []
    for fact in manifest.facts:
        columns = {str(a) for a in (fact.aspect_ids or ())}
        columns.update(routed.get(str(fact.fact_id or ""), ()))
        if wanted_set & columns:
            out.append(fact.citation_key)
    return tuple(out)


def registered_source_keys(
        manifest: CitedWriterInputManifest,
        aspect_ids: "str | Sequence[str]") -> tuple[str, ...]:
    """清单里登记到这些栏目**之一**的来源（材料行 ∪ 权威事实行），材料序在前、事实序在后。

    它只回答一个问题：「**这一栏**在本次清单里到底有没有来源」。这正是
    :func:`assign_gap_reason` 两个方向改判都要用的那一条分界线。它**不**回答「这份材料有没有
    被引用」，也**不**回答「这一栏写得出来吗」——后者是写作侧的事。
    """
    return (registered_material_keys(manifest, aspect_ids)
            + registered_fact_keys(manifest, aspect_ids))


def withheld_numeric_fact_keys(
        *, manifest: CitedWriterInputManifest) -> tuple[tuple[str, str], ...]:
    """清单里**本版不可作为正文数字授权**的事实键（`ndc-4`；判据是 `scp-13`）。

    返回 `((引用键, 状态串), …)`，键序即清单序。状态串是
    :func:`sections.sentence_check.fact_numeric_writability` 的返回值——**同一处实现**，
    请求面与逐句核对读的是它、不是第二份词表。

    **为什么需要它**：`ndc-3` 把句子侧收紧到 `scp-13`（已声明的占比事实在分母可核之前一律
    不授权）之后，请求面上**没有任何字段**能表达这件事——12 条事实（9 金额 + 3 占比）在
    `authority_facts` 与 `citable_fact_keys` 里长得一模一样，而那两处只回答「Pack 侧有没有
    这条事实」。请求面于是**承诺**了逐句核对**拒绝**的东西。这一个读数补的就是那根缺失的轴：
    它**不**删除任何事实（12 条仍全在清单里、仍逐行出现在请求面上），只把「哪几条本版写不了」
    摆到生成器面前。

    **空 tuple 是结论**：本清单没有一条被本版整条挡下的事实（财务／附注／外部那一支没有
    `value_identity`，因此逐条走原判据，**行为一字未变**），不是「读不到」。
    """
    #: 延迟导入：`sections.sentence_check` 在模块级 `import cited_writer`，模块级反向 import
    #: 会成环（与 :func:`build_cited_writer_input` 里的 `material_context` / `pack_writer` 同一
    #: 处置）。判据只有这一处实现，请求面**不**复制一份 `value_kind` 判断。
    from sections import sentence_check as SC
    out: list[tuple[str, str]] = []
    for entry in manifest.facts:
        state = SC.fact_numeric_writability(entry)
        if state != SC.FACT_NUMERIC_AUTHORIZED:
            out.append((str(entry.citation_key), str(state)))
    return tuple(out)


def writable_fact_keys(
        manifest: CitedWriterInputManifest,
        aspect_ids: "str | Sequence[str]") -> tuple[str, ...]:
    """本栏**登记**且**本版可作为正文数字授权**的事实键（`ndc-4`）。

    = :func:`registered_fact_keys` **减去** :func:`withheld_numeric_fact_keys`（保序、不去重）。

    两根轴的分工是这份读视图存在的全部理由：

    * `citable_fact_keys`（`cp-17`）答的是「**本栏在 Pack 侧登记了几条事实**」——Pack 资格；
    * 本函数答的是「**这几条里本版能写哪几条**」——正文数字授权。

    `ndc-2` 之前这两件事恰好重合，所以一直没被分开；`scp-13` 是第一次让它们分叉，而分叉处
    没有字段可以承载。**本函数不改登记轴**：它只做一次减法，`citable_fact_keys` 的语义与取值
    一个字不动——把登记键表换成可写集，等于把「这一栏有没有来源」这句可核对的话悄悄改掉。

    **它也不授权任何东西**：返回的键仍要过句子侧四轴（期间／指标／单位类／业务作用域——错误
    年度、错误业务、金额与比率混用照旧判硬错），仍受 `diagnostic_slot_fact_keys` 的展示角色轴
    约束，也仍受第 3d 条的登记必要条件约束。两条轴互不替代：一条事实可以「本版可写」但
    「只进诊断槽位」。
    """
    registered = registered_fact_keys(manifest, aspect_ids)
    if not registered:
        return ()
    withheld = {key for key, _ in withheld_numeric_fact_keys(manifest=manifest)}
    return tuple(key for key in registered if key not in withheld)


def requirement_lines_by_aspect(spec: "CitedSubsectionSpec | None") -> dict[str, str]:
    """小节的 `declared_aspect_ids` 第 i 个 ↔ `requirement_text` 第 i 行（`cp-17`）。

    位置对位是**规格自己的**结构（`cwm-6` 起：一个小节的多条要求按次序拼在一起），与
    :func:`aspect_for_requirement`、审阅侧 `cited_review._aspect_requirement_pairs` 用的是
    **同一份**对位，这里只是把它翻成正向的一张表。行数与栏数不等时返回空表——「对不上」与
    「对上了」在产物上不能长得一样（与 :func:`aspect_for_requirement` 同一条纪律）。
    """
    if spec is None:
        return {}
    lines = [ln.strip() for ln in str(spec.requirement_text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    aspects = [str(a) for a in (spec.declared_aspect_ids or ())]
    if not lines or len(lines) != len(aspects):
        return {}
    return dict(zip(aspects, lines))


#: 逐栏的**内容目标**与**引用机会**（`co-3` 立、`co-4` 纠偏；`cp-22` 立、`cp-23` 纠偏）。
#:
#: 键是冻结 Contract 的**栏位身份后缀**（`aspect_id` 最后一个 `.` 之后那一段），与
#: :data:`CONTENT_OUTLINE_STEPS` **同一条派生轴**——与公司名、行业名、材料 ID、页码、
#: 答案关键词无关。值是一个二元组 ``(content_objective, citation_opportunity)``。
#:
#: 它**只**回答两件事：这一栏要答什么（分点）、这一栏的候选键怎么用。它**不**判资格、
#: **不**放宽第 4 条、**不**改第 3d 条的登记必要条件，也**不**是新的覆盖账：缺口仍按第 7 条
#: 由模型在 `gaps` 里产生，本表一个字都不改那条通路。
#:
#: **未登记的后缀返回 `("", "")`**——空串是**结论**（本栏没有额外目标，按该步 `guidance`
#: 与第 3/4/7 条办），不是「读不到」。新增条目必须同时满足三条：该栏在冻结 Contract 里确有
#: `required_fields`；该栏的边界在第 9 条里已写明；且能举出一个真实公司节里「该写没写、且账上
#: 看不出来」的实例。**动因**（`cp-22`）：`cp-21` 真实公司节的 `company_business_model.sales_mode`
#: 只有一句正文（`s0015`），`draft.gaps` 里**没有**它的缺口，而渠道类型／直销还是经销／结算方式
#: 一项都没有——`co-2` 的 `operating_model` 步 `guidance` 全是禁则，没告诉模型这一栏要答什么。
#:
#: **`co-4`（`cp-23`）纠两条过度推断**。`co-3` 的 `sales_mode` 目标文本把两件**不由本栏材料支持**
#: 的东西写成了要答的点：①「销售体系是**自建的还是靠经销／代理**」——本批三份材料只有
#: 「公司**拥有独立的**研发、采购、生产和销售体系」，「独立」讲的是自成体系、不整体外包，
#: **不**等于自建、更**不**等于直销；照原文本读，模型会把「独立体系」直接写成渠道形态。
#: ②「销售与生产的衔接（**订单／客户需求怎么牵引排产**）」——它出自「综合考虑市场情况及客户需求
#: 安排生产」，**属生产模式**，且与**同一段文本自己的「不写」清单**（「产能扩张与排产安排…
#: 那属生产模式」）正面冲突。`co-4` 把 ① 降级为「只有原文明说才写」并写明「独立 ≠ 自建／直销」，
#: 把 ② 移进「不写」。**本栏身份不变**（仍是 `company_business_model.sales_mode`），
#: 也**不**新增/删除 `columns` 的键。
#:
#: **`co-5`（`cp-24`）**再登记两栏：`production_mode` 与 `revenue_breakdown`。动因是 `cp-23`
#: 真实公司节的读数（`m930_3_cited_real_company_cp23_qrework1_r1`）：
#: ① `revenue_breakdown` 在请求面上**带着 49 个候选键、`citable_fact_keys` 为空**，而它的
#:    `content_objective` 也是空串——模型既没拿到「这一栏要答什么」，也没拿到「没有合格事实时
#:    金额与占比怎么处置」，于是这一栏**一段正文都没有**（10 段 31 句里没有一段声明本栏），
#:    而 `m28`/`m30`/`m32` 三份载有分业务收入的材料**全部** `delivered_not_used`（0 引用句）。
#: ② `production_mode` 的 `citable_material_keys` 恰是 `['m13','m14','m44']`，正文却引了
#:    一份**只登记到 `company_business_main.*`** 的材料去写本栏，三条句子全判
#:    `sentence_aspect_not_registered`（`s0014`/`s0016`/`s0020`）——它没有被告知
#:    「本栏的候选键只从这张表里挑，别的栏登记的行顶替不了」。
#: `co-5` 仍然**只**组织写作：两个键的**键集不变**（仍是 `(内容目标, 引用机会)` 两元组），
#: `columns` 的五个键不变，`CONTENT_OUTLINE_STEPS` 一个成员都不加、次序不动。
COLUMN_CONTENT_OBJECTIVES: dict[str, tuple[str, str]] = {
    "production_mode": (
        "本栏回答**怎么造**。逐点写，**每一点都要能指回本句所引那一行的原文**；写不出的点不要"
        "绕开，按第 7 条把**那一点**记成缺口（不要整栏留白，也不要拿别栏的内容顶上）："
        "① 排产的**依据**（依据什么安排生产——原文明说市场情况、客户需求这类表述才写）；"
        "② 生产的**组织方式**（自建生产基地为主／委外／合资建厂——只有原文明说才写）；"
        "③ **扩产方式**（合资建厂、技术授权这类，原文逐字支持才写）。"
        "一般数量（产能、座数这类计数）按第 4 条办：逐字出现在**本句所引**那一行里时可写；"
        "比率与金额类本节一件都没有授权，原文逐字写着也按第 7 条留缺口。"
        "**不写**：销售渠道与客户类型、具名客户名录（第 9 条，那属**销售模式**）；"
        "产能数字与基地名单**只有**在该行**登记到本栏**时才可写——只登记到别的栏的同一份材料"
        "**不得**记到本栏（第 3d 条）。",
        "写本栏之前先读本步 `columns` 里 `aspect_id` 以 `.production_mode` 结尾的那一项的 "
        "`citable_material_keys`：**逐句**读它们指向的原文，按句判断哪一句回答上面 ①–③ 的哪一点。"
        "**本栏的候选键只从这张表里挑**：一份来源通篇讲产能、建厂、供应链，但登记归属不含本栏时，"
        "它不是本栏的候选（第 3d 条）——按第 7 条为本栏留缺口，不要拿它顶替，也不要因为它讲的"
        "确实是生产的事就把它记到本栏。"),
    "sales_mode": (
        "本栏回答**怎么卖**。逐点写，**每一点都要能指回本句所引那一行的原文**；写不出的点不要"
        "绕开，按第 7 条把**那一点**记成缺口（不要整栏留白，也不要拿别栏的内容顶上）："
        "① 靠销售哪些产品或解决方案实现收入（产品口径逐字取自本句所引原文）；"
        "② 这些产品**通过什么渠道**卖出去（直销／经销／代理）——**只有原文明说才写**；"
        "「拥有**独立的**销售体系」「拥有完整的销售体系」讲的是自成体系、不整体外包，"
        "**不**等于自建，更**不**等于直销，**不得**据此推定渠道形态；"
        "③ 客户的**类型或范围**（不是具名名录，第 9 条）。"
        "**不写**：销售与生产的衔接／订单或客户需求牵引排产（第 9 条，那属**生产模式**）；"
        "产能扩张与建厂方式（第 9 条，那属**生产模式**）；具名客户名录（第 9 条）；"
        "境外收入／地区收入结构（第 9 条）；结算方式与账期（原文没有就按第 7 条记缺口，"
        "不得由「独立体系」推定）；渠道形态（同上，不得由「独立体系」推定）。",
        "写本栏之前先读本步 `columns` 里 `aspect_id` 以 `.sales_mode` 结尾的那一项的 "
        "`citable_material_keys`：**逐句**读它们指向的原文，按句判断哪一句回答上面 ①–③ 的哪一点。"
        "键表非空而某一点没人回答 ⇒ 那一点是缺口，不是「本节没材料」；键表为空才是本栏零来源。"
        "**当期来源与历史来源要分开读**：`source_role` 为 `history_and_conflict_source` 的那一行"
        "只能写成带年份的历史披露，不得用它承诺当前状况（第 5、10 条）。"),
    "revenue_breakdown": (
        "本栏回答**各业务线各贡献多少**。逐点写，**每一点都要能指回本句所引那一行的原文**；"
        "写不出的点不要绕开，按第 7 条把**那一点**记成缺口（不要整栏留白，也不要拿别栏的内容顶上）："
        "① 逐**业务线**（板块／产品线）各自成句，说清这一块做什么——不要把几块业务压成一句，"
        "也不要用一句合计代替分业务；"
        "② 各业务线的**收入金额**与**占总收入比重**要**分开处置**（`cp-25`）："
        "**金额**——**只有**本栏 `writable_fact_keys` 里列出的**合格金额事实**（第 4 条）才可写，"
        "按业务、年份、单位、来源逐字写；本栏可写键表为空时**逐项**按第 7 条记缺口，**不得**因为"
        "金额逐字出现在本句所引原文里就写进正文；"
        "**占比**——分母（是全年营业收入还是别的口径）在本版的身份字段里无处承载与核验，因此"
        "本版**一律不可写**（顶层 `numeric_authorization.withheld` 会把这些键标成 "
        "`denominator_unverified`，逐句核对据此判它不授权）：逐项按第 7 条留**结构化缺口**，"
        "**不得**改用普通材料原文或只读 PDF 展示区里的一个百分比顶上；"
        "**占比写不了不等于这一栏可以留白**——金额与 ③ 的定性内容照写，"
        "**不得**把本栏的金额与业务叙述一起撤掉；"
        "③ **不带数字**的定性内容（各业务线之间的关系、收入结构的变化方向、来源自己给出的"
        "收入变动原因）可以写，但仍不得夹带 ② 里的金额与占比。"
        "**不写**：公司靠什么赚钱、研发—采购—生产—销售怎么组织的**经营模式总述**"
        "（第 9 条，那属经营模式那几栏）；按**地区**的收入结构（第 9 条，那是另一条轴）；"
        "同一段收入分解在别栏再写一遍（第 10 条）。",
        "写本栏之前先读本步 `columns` 里 `aspect_id` 以 `.revenue_breakdown` 结尾的那一项："
        "`citable_material_keys` 是材料候选、`writable_fact_keys` 是**本版可写的金额事实键**"
        "（`citable_fact_keys` 是 Pack 侧登记，两者不是一回事）。**逐句**读材料键指向的原文，"
        "按句判断哪一句回答上面 ①–③ 的哪一点。键表非空而某一点没人回答 ⇒ 那一点是缺口，"
        "不是「本节没材料」；键表为空才是本栏零来源。"
        "**当期来源与历史来源要分开读**：`source_role` 为 `history_and_conflict_source` 的那一行"
        "只能写成带年份的历史披露，不得用它承诺当前状况（第 5、10 条）。"),
}


def column_content_objective(aspect_id: str) -> tuple[str, str]:
    """该栏的 ``(内容目标, 引用机会)``；未登记的后缀返回 ``("", "")``（空串是结论）。

    只看 **Contract 的栏位身份后缀**——`company_business_model.sales_mode` 与任何别的主题下的
    `.sales_mode` 得到同一条目标，因为这条目标讲的是**栏位本身**该回答什么，不是哪一家公司。
    """
    return COLUMN_CONTENT_OBJECTIVES.get(str(aspect_id).rsplit(".", 1)[-1], ("", ""))


def citable_columns(manifest: CitedWriterInputManifest,
                    spec: "CitedSubsectionSpec") -> list[dict]:
    """本小节**逐栏**列出登记到该栏的来源键（`cp-17`，只进请求面）。

    回答的是写作面最先要问的那一句——「**这一栏**手里有哪些登记到本栏的来源」。它把
    :func:`registered_material_keys` / :func:`registered_fact_keys` 逐栏摊开，模型不必再自己拿
    逐行的 `aspect_ids` 去做一次「18 栏 × 65 行」的交叉。**空数组是结论**：本栏本次一件来源都
    没有登记（按第 7 条留缺口），不是「读不到」。

    它**不**授权任何一条来源写成正文结论，也不放宽第 4 条：登记归属始终是必要条件而非通行证
    （见 `cp-16` 对第 3d 条的收回）。它只是把「本栏有哪些候选」从一次隐含的集合运算变成一份
    看得见的清单——`cp-16` 真实公司节里六条经营模式栏被自述成「未单独登记到本栏」而实际上
    各登记着三行（m24 / m36 / m39），正是那一步交叉没做出来的产物。

    **`co-3`（`cp-22`）**每项再多两个键：`content_objective`（这一栏要答什么，分点）与
    `citation_opportunity`（这一栏的候选键怎么用）。它们逐字取自
    :data:`COLUMN_CONTENT_OBJECTIVES`，未登记的后缀为 `""`（结论）。它们与上面两个键表**同级**：
    只组织写作，不判资格。**`co-4`（`cp-23`）**只改那一份文本里的 `sales_mode` 一条。

    **`co-6`（`cp-25`，`ndc-4` 批）**每项再多一个 `writable_fact_keys`（**第 7 个键**）：
    :func:`writable_fact_keys` 的逐字投影，即「本栏登记的事实键里，**本版可作为正文数字授权**的
    那些」。它**不**取代 `citable_fact_keys`（后者的登记语义与取值一字不动），也不新增任何判定
    ——它只是把 `scp-13` **已经在执行**的那条判据（已声明的占比事实在分母可核之前一律不授权）
    摆到生成器面前。加它的动因是请求面与核对口径正面冲突：`ndc3` 离线 run 的请求面上 9 条金额
    与 3 条占比在 `citable_fact_keys` 里长得一模一样，营收栏目标又要求「有合格事实就写占比」，
    而逐句核对对占比句一律判 `denominator_unverified` 硬错。
    """
    lines = requirement_lines_by_aspect(spec)
    out: list[dict] = []
    for aspect in (spec.declared_aspect_ids or ()):
        key = str(aspect)
        objective, opportunity = column_content_objective(key)
        out.append({"aspect_id": key,
                    "requirement_text": lines.get(key, ""),
                    "content_objective": objective,
                    "citation_opportunity": opportunity,
                    "citable_material_keys": list(registered_material_keys(manifest, key)),
                    "citable_fact_keys": list(registered_fact_keys(manifest, key)),
                    "writable_fact_keys": list(writable_fact_keys(manifest, key))})
    return out


#: 阅读顺序提纲的版本号（`cp-23`）。它只组织**写作**，不判资格、不加门；改一步的成员或次序
#: 就是改这一节的写作组织，因此跟着提示词一起升版（`cp-20` 立 `co-1`，`cp-21` 升 `co-2`，
#: `cp-22` 升 `co-3`，`cp-23` 升 `co-4`——后者只改 `sales_mode` 那一栏的**内容目标文本**）。
#:
#: `co-2` 相对 `co-1` 只加一件事：把每一步**可引的键**摆到那一步自己身上（`columns`）。`co-1`
#: 只给 `aspect_ids`，模型得自己做「步 → aspect_id → 栏 → 键表」这一跳；`cp-20` 真实公司节里
#: 那一跳**没被做出来**——四条硬错（`s0008`/`s0009`/`s0012`/`s0016`）全是模型按语义挑行、挑中
#: 的行恰好没登记到该句声明的栏，而清单里**另有一行**登记到了那一栏。`co-2` 不新增任何判定：
#: `columns` 是 :func:`citable_columns` 的**逐字投影**，同一份清单、同一个函数、同一套登记轴。
#:
#: `co-3` 相对 `co-2` 也只加一件事：给**栏**一个显式**内容目标**与**引用机会**
#: （:data:`COLUMN_CONTENT_OBJECTIVES`，逐栏投影进 `columns` 与 `citable_columns`）。
#: `co-2` 摆出了「这一栏有哪些候选」，但没说过「这一栏要答什么」——`operating_model` 步的
#: `guidance` 全是**禁则**。`cp-21` 真实公司节的 `company_business_model.sales_mode` 因此只有
#: 一句正文（`s0015`）、`draft.gaps` 里连一条它的缺口都没有：答得薄与答得全在账上长得一样。
#: `co-3` 仍然**不**新增任何判定：目标文本只讲这一栏该回答哪几点，缺口仍按第 7 条由模型产生。
#:
#: `co-4` 相对 `co-3` **一个键都不加、一个成员都不动**：只改 `sales_mode` 那一栏**内容目标文本**
#: 的两处过度推断（「独立体系」≠ 自建／直销；「客户需求牵引排产」属生产模式，移进「不写」）。
#: 详情见 :data:`COLUMN_CONTENT_OBJECTIVES` 的模块注释。
#:
#: `co-5`（`cp-24`）相对 `co-4` **同样一个键都不加、一个成员都不动**：只往
#: :data:`COLUMN_CONTENT_OBJECTIVES` 里再登记两栏（`production_mode` / `revenue_breakdown`），
#: 并在提示词第 9 条把那句「没有合格金额或比率事实时收入构成按第 7 条留缺口」收窄到**金额与占比
#: 两项**。分步、`columns` 的键集与投影轴一个字节都不动。
#:
#: `co-6`（`cp-25`）相对 `co-5` 只加**一个键**：`columns` 每项多一个 `writable_fact_keys`
#: （**五个 → 六个**，键集仍是等号）。分步、`CONTENT_OUTLINE_STEPS` 的成员与次序、
#: `COLUMN_CONTENT_OBJECTIVES` 的键、`citable_material_keys` / `citable_fact_keys` 的取值
#: 一个字节都不动。
#:
#: 加它的动因是**请求面与逐句核对正面冲突**（`ndc-4` 批）：`ndc-3` 把句子侧收紧到 `scp-13`
#: 之后，`ndc3` 离线 run 的请求面上 12 条事实（9 金额 + 3 占比）在 `citable_fact_keys` 里长得
#: 一模一样，而 `revenue_breakdown` 的 `content_objective` 第 ② 点又要求「有合格事实就写占比」
#: ——模型照请求面写出来的占比句必然被逐句核对判 `numeric_basis_not_qualified` 硬错。
#: `co-6` 加的不是新判定：`scp-13` 的那条判据**已经在执行**，这里只是把它摆出来（确定性投影，
#: 同一处实现见 :func:`withheld_numeric_fact_keys`），并把 `revenue_breakdown` 的第 ② 点改成
#: 金额与占比**分列**（金额走可写键，占比在分母可核之前留结构化缺口）。
#: 因此这一版的 `columns` 仍然**不新增任何门**：登记（第 3d 条）、数字授权（第 4 条）、逐句
#: 硬核对器、缺口词表、预算、冻结 Contract 一律未动。
CONTENT_OUTLINE_VERSION = "co-6"

#: 这张提纲的档案名。**不是**公司名、不是行业名——它标的是「这一套栏位身份属于哪一类业务小节」。
CONTENT_OUTLINE_PROFILE = "company_business"

#: 由冻结 Contract 的**栏位身份后缀**派生的阅读顺序提纲（`co-1`，`cp-20`）。
#:
#: 每一项是 `(step_id, 步骤标题, 承接的栏位身份后缀, 该步的写作提示)`。匹配只看 `aspect_id`
#: 最后一个 `.` 之后那一段——那是**冻结 Contract 的栏位身份**，与公司、行业、材料 ID、页码、
#: 答案关键词无关。步骤的**次序**就是提示词第 3 条那条业务导航线（产品与技术 → 应用场景 →
#: 产业链位置 → 研发／采购／生产／销售模式 → 成本与毛利 → 收入构成 → **主营业务与报告期内
#: 经营表现** → 报告期与口径 → 客户 → 供应商）。
#:
#: 为什么最后那个「经营表现」步并入 `main_business` 栏：这一节 18 个栏里**没有**一栏叫
#: 「经营表现」，而产销量、市场地位、网络与渠道、合作与落地、海外进展这类结果面事实，在
#: 冻结 Contract 里唯一对得上的是「主营业务构成（分板块／分部）」——本节讲的主营业务就是
#: 动力电池／储能电池／电池材料这几块。把这一步**锚到 `main_business`**，模型才有地方声明它，
#: 也才不会像 `s0015` 那样把客户合作名录挂到销售模式栏上。这一步不做任何资格判断：段落仍要
#: 逐句过第 3d 条的登记核对与第 4 条的数字授权。
CONTENT_OUTLINE_STEPS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("products_and_technology", "产品与技术",
     ("products_solutions", "tech_route"), ""),
    ("application_scenarios", "应用场景",
     ("app_scenarios",),
     "这一步只写**应用场景**本身：哪些领域、哪些细分市场、哪些新兴场景。产品规格与技术路线属于"
     "上一步。场景里点到产品名可以，但那些名字必须逐字出现在**本句所引**的那一行原文里。"
     "若一份来源通篇讲应用场景、登记却不在本栏，它不是本步的候选（`co-1` 真实公司节里四条"
     "「引用未登记本栏」有两条出在这一步）：要么改用本步 `columns[].citable_material_keys` 里"
     "逐字写着同一批场景名的那一行，要么把这句话挪去真正登记它的那一步。"),
    ("industry_chain_position", "产业链位置",
     ("industry_chain_position",),
     "这一步写**产业链位置**：上游资源与材料、自建／参股／合资、向下游的延伸。同一段原文若"
     "在别处还有一行，引**登记到本栏**的那一行；拿只登记产品栏的那一行顶替，本句就会被判"
     "「引用未登记本栏」。"),
    ("operating_model", "研发／采购／生产／销售模式",
     ("procurement_mode", "production_mode", "sales_mode"),
     "四条模式各写各的：研发模式、采购模式、生产模式、销售模式是四件事，不要压成一句。"
     "第 9 条那条边界在这里同样成立：具名客户／战略合作**名录**不是销售模式的内容——它属于"
     "客户栏，或属于『主营业务与报告期内经营表现』这一步。"),
    ("cost_and_margin", "成本结构与成本竞争能力",
     ("cost_structure", "cost_competitiveness", "cost_gross_margin"), ""),
    ("revenue_breakdown", "收入构成",
     ("revenue_breakdown",), ""),
    ("main_business_and_performance", "主营业务与报告期内经营表现",
     ("main_business",),
     "本节收尾：报告期内的产销量、市场地位、网络与渠道铺开、合作与落地进展、海外进展这类"
     "结果面事实写在这一步，不要塞回产品段或模式段。第 9 条那条边界在这里同样成立：具名客户／"
     "战略合作**名录**不是销售模式的内容，它属于客户栏或这一步。"
     "数字按第 4 条办：本节 `authority_facts` 为空，所以**一般数量**（产能、销量、座数、"
     "艘数、家数、国家数这类计数）只在它逐字出现在**本句所引**的那一行里时可写；**比率类"
     "（百分比、千分比、百分点）与金额类数字本节一件都没有授权**，原文逐字写着也按第 7 条"
     "留缺口。不得把不同来源的数量拼成「合计／其中／分别为」——那是自造的部分—整体关系，"
     "任一份来源都没写。报告期内的经营进展**只在这一步写一次**：同一组建站数、同一组运营"
     "规模不要既写在这里又写进应用场景步。"),
    ("period_and_caliber", "报告期与口径",
     ("period_unit_caliber",), ""),
    ("customer_concentration", "客户集中度",
     ("customer_current_concentration", "customer_concentration_change",
      "customer_anonymity"),
     "本栏 `columns[].citable_material_keys` 里的行若都是较旧期来源（`history_and_conflict_source`），"
     "名录本身**不是**当前集中度的答案：按**该来源自己的**期间措辞写成历史披露，或按第 7 条"
     "留缺口；不得写成「公司披露的客户包括……」这种持续至今的当前状况。要证明当前集中度，"
     "还得是本栏里有当期来源，或者有合格事实。"),
    ("supplier_concentration", "供应商集中度",
     ("supplier_current_concentration", "supplier_concentration_change"),
     "本栏若一份来源都没登记，就按第 7 条留缺口——不要拿供应方式的描述（采购模式）去顶"
     "供应商集中度。"),
)


def build_content_outline(spec: "CitedSubsectionSpec | None", *,
                          manifest: "CitedWriterInputManifest | None" = None) -> dict | None:
    """本小节的**阅读顺序提纲**（`co-2`，只进请求面；`cp-20` 立 `co-1`，`cp-21` 升 `co-2`）。

    它把 `cp-19` 第 3 条里**只存在于提示词**的那条业务导航线，按冻结 Contract 的栏位身份
    派生成一份看得见的计划：每一步给出它的 `aspect_ids`（逐字取自本小节的 `declared_aspect_ids`）、
    该步各栏**逐字**的 `requirement_text`，以及该步的写作提示。**这一步不是新的资格判定**——
    每一步的候选来源仍由 :func:`citable_columns` 给出，登记仍是必要条件（第 3d 条），数字仍过
    第 4 条。它回答的只是「这一节按什么顺序、分几步写，每一步落到哪几栏」。

    **`columns`（`co-2`；`co-3` 起多两个键）**：给了 `manifest` 时，每一步再多一个 `columns`
    ——该步每个声明栏的 ``{aspect_id, content_objective, citation_opportunity,
    citable_material_keys, citable_fact_keys}``，**逐字投影**自 :func:`citable_columns`
    （同一函数、同一份登记轴，这里不新造任何判定、不新增任何候选）。
    `co-1` 只给 `aspect_ids`，模型得自己在 10 万字符的请求面上做「步 → aspect_id → 栏 → 键表」
    这一跳；`cp-20` 真实公司节证明那一跳**没被做出来**——四条硬错全是模型按语义挑行、挑中的
    行恰好没登记到该句声明的栏，而清单里**另有一行**登记到了那一栏。`columns` 把那一跳从一次
    集合运算摆成请求面上看得见的东西，仅此而已。
    **`co-3`（`cp-22`）**再加 `content_objective` / `citation_opportunity`：`co-2` 只说「这一栏
    有哪些候选」，没说「这一栏要答什么」——`cp-21` 真实公司节的 `sales_mode` 因此只写了一句、
    连缺口都没有。这两个键仍然只组织写作：缺口仍按第 7 条产生。
    **`co-4`（`cp-23`）**只改那份目标文本里 `sales_mode` 一条（纠两条过度推断），
    提纲的键集、分步与 `columns` 形状一个字节都不动。
    **`co-6`（`cp-25`，`ndc-4` 批）**每项再加一个 `writable_fact_keys`
    （:func:`writable_fact_keys` 的逐字投影 = 本栏登记的事实键里本版可作正文数字授权的那些）。
    它**不**取代 `citable_fact_keys`（登记语义与取值一字不动），也**不**新增判定——`scp-13`
    的那条判据已经在执行，这里只是把它摆到生成器面前，消掉「请求面要求写占比、逐句核对必然
    判硬错」这处自相矛盾。`citable_columns` 同步加同一个键，两张表仍是同一份数据的两种切法。

    **没给 `manifest` 就不给 `columns` 键**——那是「这一跳需要清单才能算」的结论，**不是**
    「本步零候选」。键**缺席**与键为 `[]` 在产物上不能长得一样：后者是一句关于登记轴的断言
    （本栏本次没有一行登记到本栏），前者只是没算。这条纪律同时保证：只拿 `spec` 调出来的提纲
    **依旧一件材料键都不含**，它不是第二份材料清单。

    **不匹配就不发**：本小节声明的栏与这张提纲**一条都不沾**时（财务小节的 `fin_solvency.*` /
    `fin_balance_structure.*` 等），返回 `None`，请求面上**没有**这个字段——提示词里写明
    「本输入可能没有这个字段」。半匹配时，未落在任何一步里的声明栏原样收进
    `unmapped_aspect_ids`：**照常可写**，只是没有阅读顺序建议，不报错、不隐藏、不留缺口
    ——「对不上」与「对上了」在产物上不能长得一样（与 :func:`requirement_lines_by_aspect`
    同一条纪律）。
    """
    if spec is None:
        return None
    aspects = tuple(str(a) for a in (spec.declared_aspect_ids or ()))
    if not aspects:
        return None
    suffix_of = {a: a.rsplit(".", 1)[-1] for a in aspects}
    lines = requirement_lines_by_aspect(spec)
    #: 逐栏键表只从 :func:`citable_columns` 取一次，各步再从这份投影里筛自己那几栏——这样
    #: 「一步里的键」与「栏上的键」是同一份数据的两种切法，不可能对不上。
    by_aspect = ({c["aspect_id"]: c for c in citable_columns(manifest, spec)}
                 if manifest is not None else {})
    steps: list[dict] = []
    used: set[str] = set()
    for step_id, title, suffixes, guidance in CONTENT_OUTLINE_STEPS:
        wanted = set(suffixes)
        members = [a for a in aspects if suffix_of[a] in wanted]
        if not members:
            continue
        used.update(members)
        #: 栏位身份重复出现在两步里会让「这一步落到哪几栏」变成一句无法核对的话。构造期就拒绝，
        #: 不留给运行期去猜——这张表是模块自己的常量，落成重复是**写错了**，不是数据形状。
        duplicated = [a for a in members if a in {x for s in steps for x in s["aspect_ids"]}]
        if duplicated:
            raise CitedWriterError(
                f"阅读顺序提纲 `{CONTENT_OUTLINE_VERSION}` 把栏位 "
                f"{duplicated} 同时挂到了两步上：每一步承接的栏必须互不相交",
                reason="content_outline_overlap")
        step = {"step_id": step_id, "step_title": title,
                "aspect_ids": members,
                "requirement_texts": [lines.get(a, "") for a in members],
                "guidance": guidance}
        if manifest is not None:
            step["columns"] = [
                {"aspect_id": a,
                 #: `co-3`：逐字投影自同一份 :func:`citable_columns`，与键表同级。
                 "content_objective": str(by_aspect[a].get("content_objective", "")),
                 "citation_opportunity": str(by_aspect[a].get("citation_opportunity", "")),
                 "citable_material_keys": list(by_aspect[a]["citable_material_keys"]),
                 "citable_fact_keys": list(by_aspect[a]["citable_fact_keys"]),
                 #: `co-6`：同一份数据的第三种切法——「这一栏登记的事实里本版能写哪几条」。
                 #: 两张表（`columns` 与 `citable_columns`）都投影它，是因为它们是**同一份数据
                 #: 的两种切法**：只让其中一张读得出可写集合，等于把「本步能引什么」又变成一次
                 #: 需要跨表对的运算（`co-2` 要消掉的正是这种事）。
                 "writable_fact_keys": list(by_aspect[a]["writable_fact_keys"])}
                for a in members if a in by_aspect]
        steps.append(step)
    if not steps:
        return None
    return {"outline_version": CONTENT_OUTLINE_VERSION,
            "profile": CONTENT_OUTLINE_PROFILE,
            "reader_order": [s["step_id"] for s in steps],
            "steps": steps,
            "unmapped_aspect_ids": [a for a in aspects if a not in used]}


def assign_gap_reason(*, claimed_reason: str, spec: CitedSubsectionSpec | None,
                      registered_count: int) -> tuple[str, str]:
    """定下一条缺口的**系统判定**理由，返回 `(reason, reason_assignment)`。

    两条改判规则，是**同一条业务事实**（「这一栏本次到底有没有来源」）的两面。`registered_count`
    就是这条事实的读数：该栏登记的**材料行数 ∪ 事实行数**（见 :func:`registered_source_keys`；
    财务侧的事实行 `aspect_ids` 常为空，靠呈现层路由认栏）。

    * 自述 `no_source_in_manifest`，而该栏登记**不止零份** ⇒ 改判为
      `source_present_but_not_admissible`。`no_source_in_manifest` 在下游是「这一节本来就没什么
      可写」，而真实情形是「来源在，只是它里面的**合格数字事实**本次没有授权」。两者要补的东西
      不同——前者去检索，后者去补事实资格。
    * 自述 `manifest_partial_for_requirement` / `source_present_but_not_admissible`，而该栏登记数
      为 **0** ⇒ 改判回 `no_source_in_manifest`。这两句**都预设「本栏至少有一条来源」**：「只取到
      一部分」以「取到过」为前提，「来源在场」直接断言在场，零登记把两句同时证伪。不改判的话，
      「供应商栏一份来源都没有」会被记成「有材料但不合格」——下游据此去补事实资格，而真正要做的
      是去检索。**来源缺口与取材失败要补的东西不同，不得合并记。**

    其余自述一律**原样保留**（`writer_declared`）：`not_applicable_to_authority` **不**预设任何
    来源（它说的是「这条要求与本节的权威输入无关」），本模块没有依据反驳它。规格取不到时标
    `uncheckable_subsection_not_in_manifest`——连是哪一栏都不知道，不改判也不假装核过。

    **`registered_count` 数的是「这一条要求所属的那一栏」，不是整个小节**（本函数是纯函数，
    由调用方 :func:`_gap_from_payload` 按 :func:`aspect_for_requirement` 定好栏位后传入）。
    小节覆盖多栏时，用整节计数会把「本小节别处有材料」读成「这一栏有材料」——那正是本规则
    要分开的两件事（例如客户栏有 3 份上年历史来源、供应商栏一份都没有，两者不能同记一条）。
    """
    claimed = str(claimed_reason or "")
    if spec is None:
        return claimed, "uncheckable_subsection_not_in_manifest"
    count = int(registered_count or 0)
    if claimed == "no_source_in_manifest" and count > 0:
        return "source_present_but_not_admissible", "system_reassigned_from_manifest"
    if claimed in ("manifest_partial_for_requirement",
                   "source_present_but_not_admissible") and count == 0:
        return "no_source_in_manifest", "system_reassigned_from_manifest"
    return claimed, "writer_declared"


def unique_subsection_gap_reasons(
        rows: Sequence[tuple[str, str, str]]) -> dict[str, dict[str, str]]:
    """`(subsection_id, column, system_reason)` 三元组 → 「同小节内**可唯一对应**的缺口理由」。

    返回 `{subsection_id: {aspect_id: reason}}`，**只登记恰好一条**的：同一栏在同一小节里被
    0 条或 ≥2 条缺口指认时一律不进表。

    为什么必须唯一：这条读数的用途是「用一条**确定的**顶层缺口替代一次整段删除的判定」
    （`crn-4` 的缺口支持）。同一栏被两条缺口指认时，那两条的**系统判定**理由未必相同
    （一条可能是 `no_source_in_manifest`、另一条是 `manifest_partial_for_requirement`），
    此时「这一栏的依据是哪一条」没有唯一答案——交不出唯一的那条，就不许拿它当依据。
    「没证明就不许删」这条纪律在这里表现为：**认不出唯一的那条，就别登记它**。

    计数按**缺口条数**（不是按理由去重）：两条内容相同的缺口仍是两条，仍然不唯一——
    宁可少删一段，不拿「理由反正一样」当唯一性。

    生产侧（:func:`empty_shell_context`）与离线读回侧
    （`sections.cited_reply_readback._empty_shell_context_for_face`）**共用本函数**：同一串字节
    在两处必须判出同一个答案，因此「唯一」这条口径只写在这里一份。
    """
    seen: dict[tuple[str, str], list[str]] = {}
    for subsection_id, column, reason in rows:
        subsection_id = str(subsection_id or "")
        column = str(column or "")
        reason = str(reason or "")
        if not subsection_id or not column or not reason:
            continue
        seen.setdefault((subsection_id, column), []).append(reason)
    out: dict[str, dict[str, str]] = {}
    for (subsection_id, column), reasons in seen.items():
        if len(reasons) != 1:
            continue
        out.setdefault(subsection_id, {})[column] = reasons[0]
    return out


def derive_expected_source_classes(
        *, spec: CitedSubsectionSpec | None) -> tuple[tuple[str, ...], str]:
    """补件的期望来源类 = **该 Contract 栏目允许的来源类**，返回 `(classes, derivation)`。

    取值**逐字**来自 `spec.allowed_source_classes`（组合根从冻结 Contract 的
    `evidence_requirements` 投影），本函数不增删、不排序、不做「挑一个」。

    为什么要多值：`er_business` 一类要求同时声明 `company_industry` 与 `structured_db`。
    用单值表示它，就只能挑一个，而挑出来的那一个在下游会被读成「这一类足以结案」——那是
    `_M930_3_E_BATCH_CHANGELIST.md` §7 已经记过一次的隐患，不在这里重犯。
    """
    if spec is None:
        return (), "aspect_not_in_manifest"
    classes = tuple(spec.allowed_source_classes)
    if not classes:
        return (), "contract_aspect_without_evidence_requirements"
    return classes, "contract_aspect_evidence_requirements"


@dataclass(frozen=True)
class CitedProseDraft:
    """一次新写作的**正文草稿**（`cw-1`）：小节 → 段落 → 句 → 逐句引用。

    `input_manifest_id` 把草稿钉在**唯一**一份输入清单上：草稿里的每个引用键都必须在
    **那一份**清单里存在（`__post_init__` 逐句核对），因此「引用了本次输入之外的东西」在
    构造期就不可能存在。
    """

    draft_id: str
    schema_version: str
    task_id: str
    section_id: str
    input_manifest_id: str
    writer_identity: str
    subsections: tuple[CitedSubsection, ...] = ()
    gaps: tuple[CitedProseGap, ...] = ()
    follow_up_needs: tuple[CitedFollowUpNeed, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != CITED_WRITER_DRAFT_SCHEMA_VERSION:
            raise CitedWriterError(
                f"CitedProseDraft.schema_version 必须为 "
                f"{CITED_WRITER_DRAFT_SCHEMA_VERSION!r}")
        for name in ("task_id", "section_id", "input_manifest_id", "writer_identity"):
            if not str(getattr(self, name) or "").strip():
                raise CitedWriterError(f"CitedProseDraft.{name} 必须非空")
        subsections = tuple(self.subsections or ())
        gaps = tuple(self.gaps or ())
        needs = tuple(self.follow_up_needs or ())
        for item, want in ((subsections, CitedSubsection), (gaps, CitedProseGap),
                           (needs, CitedFollowUpNeed)):
            for entry in item:
                if not isinstance(entry, want):
                    raise CitedWriterError(
                        f"CitedProseDraft 的成员只能是 {want.__name__}，"
                        f"得到 {type(entry).__name__}")
        object.__setattr__(self, "subsections", subsections)
        object.__setattr__(self, "gaps", gaps)
        object.__setattr__(self, "follow_up_needs", needs)
        ids = [s.subsection_id for s in subsections]
        if len(set(ids)) != len(ids):
            raise CitedWriterError("CitedProseDraft 含重复小节 id", reason="subsection_duplicate")
        #: 全节唯一性在这里**再断一次**（解析边界已断过一次）：不同句共用同一个 ID 之后，
        #: 核对与审阅会把两句当成一句读，而那种错误在结论里看不出来。放进 `__post_init__`
        #: 意味着**任何**构造路径都绕不过它，不只是 `parse_cited_prose` 那一条。
        sentence_ids = [sid for sub in subsections for sid in sub.sentence_ids()]
        if len(set(sentence_ids)) != len(sentence_ids):
            counts: dict[str, int] = {}
            for sid in sentence_ids:
                counts[sid] = counts.get(sid, 0) + 1
            duplicates = sorted(sid for sid, n in counts.items() if n > 1)
            raise CitedWriterError(
                f"草稿的全节句子 ID 不唯一：{duplicates}（共 {len(sentence_ids)} 句）",
                reason="sentence_id_duplicate")
        expected = derive_draft_id(self)
        if self.draft_id != expected:
            raise CitedWriterError(
                f"CitedProseDraft.draft_id 与内容不符：声明 {self.draft_id!r}，应为 {expected!r}",
                reason="draft_id_mismatch")

    def sentences(self) -> tuple[CitedSentence, ...]:
        return tuple(s for sub in self.subsections for s in sub.sentences())

    def sentence_ids(self) -> tuple[str, ...]:
        return tuple(s.sentence_id for s in self.sentences())

    def identity_body(self) -> dict:
        return draft_identity_body(task_id=self.task_id, section_id=self.section_id,
                                   input_manifest_id=self.input_manifest_id,
                                   writer_identity=self.writer_identity,
                                   subsections=self.subsections, gaps=self.gaps,
                                   follow_up_needs=self.follow_up_needs)

    def fingerprint(self) -> str:
        return hashlib.sha256(
            NS.canonical_json(self.identity_body()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {"draft_id": self.draft_id, "draft_fingerprint": self.fingerprint(),
                **self.identity_body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "CitedProseDraft":
        parts = {"task_id": str(kwargs.get("task_id", "") or ""),
                 "section_id": str(kwargs.get("section_id", "") or ""),
                 "input_manifest_id": str(kwargs.get("input_manifest_id", "") or ""),
                 "writer_identity": str(kwargs.get("writer_identity", "") or ""),
                 "subsections": tuple(kwargs.get("subsections") or ()),
                 "gaps": tuple(kwargs.get("gaps") or ()),
                 "follow_up_needs": tuple(kwargs.get("follow_up_needs") or ())}
        body = {"schema_version": CITED_WRITER_DRAFT_SCHEMA_VERSION, **parts}
        body["draft_id"] = NS.content_id("cwd_", draft_identity_body(**parts))
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "CitedProseDraft":
        d = NS._reject_unknown(
            d, {"draft_id", "draft_fingerprint", "schema_version", "task_id", "section_id",
                "input_manifest_id", "writer_identity", "subsections", "gaps",
                "follow_up_needs"}, "CitedProseDraft")
        draft = cls(
            draft_id=str(d.get("draft_id") or ""),
            schema_version=d.get("schema_version"),
            task_id=str(d.get("task_id") or ""),
            section_id=str(d.get("section_id") or ""),
            input_manifest_id=str(d.get("input_manifest_id") or ""),
            writer_identity=str(d.get("writer_identity") or ""),
            subsections=tuple(_subsection_from_dict(s) for s in (d.get("subsections") or ())),
            gaps=tuple(CitedProseGap.from_dict(x) for x in (d.get("gaps") or ())),
            follow_up_needs=tuple(CitedFollowUpNeed.from_dict(x)
                                  for x in (d.get("follow_up_needs") or ())))
        declared = d.get("draft_fingerprint")
        if declared is not None and str(declared) != draft.fingerprint():
            raise CitedWriterError(
                f"CitedProseDraft.draft_fingerprint 与内容不符：声明 {declared!r}，"
                f"实际 {draft.fingerprint()!r}", reason="draft_fingerprint_mismatch")
        return draft


def draft_identity_body(*, task_id: str, section_id: str, input_manifest_id: str,
                        writer_identity: str, subsections: Sequence[Any],
                        gaps: Sequence[Any], follow_up_needs: Sequence[Any]) -> dict:
    """草稿身份体（JSON-safe）：`create()` 与 `identity_body()` 的**同一**实现。

    两处各写一遍会让「构造时算的 id」与「读回时校验的 id」在字段增删后悄悄分叉，而分叉的
    表现是「同一份草稿有时可构造、有时不可」。
    """
    return {"schema_version": CITED_WRITER_DRAFT_SCHEMA_VERSION, "task_id": task_id,
            "section_id": section_id, "input_manifest_id": input_manifest_id,
            "writer_identity": writer_identity,
            "subsections": [s.to_dict() for s in subsections],
            "gaps": [g.to_dict() for g in gaps],
            "follow_up_needs": [n.to_dict() for n in follow_up_needs]}


def derive_prose_revision(*, input_manifest_id: str, writer_identity: str) -> str:
    """一次写作的**修订号**：`(输入清单身份, 写作调用身份)` 的确定性编码。

    补件需求要绑「我是在哪一版正文上提的」，而 **草稿自己的 id 不能当这个修订号**——补件需求
    是草稿身份体的一部分，用草稿 id 去填它就成了自指（算 id 需要它、它需要 id）。修订号因此
    取「输入清单 + 写作调用」这一对，它们**不含** gaps / follow_up_needs，无环。
    """
    return NS.content_id("cwrev_", {"input_manifest_id": str(input_manifest_id or ""),
                                    "writer_identity": str(writer_identity or "")})


def _subsection_from_dict(d: Any) -> CitedSubsection:
    d = NS._reject_unknown(d, {"subsection_id", "title", "paragraphs"}, "CitedSubsection")
    paragraphs = []
    for p in (d.get("paragraphs") or ()):
        #: `aspect_ids` 是本段**声明服务**的 Contract 栏目（`cw-4`）。它是可选的：不给就是
        #: 空 tuple——「本段不对应任何栏目」是合法取值，与「引用为空的句子」同一条纪律。
        #: 越界由 :func:`_check_paragraph_aspects` 在知道本小节声明集合之后判定。
        p = NS._reject_unknown(p, {"paragraph_id", "sentences", "aspect_ids"}, "CitedParagraph")
        sentences = []
        for s in (p.get("sentences") or ()):
            s = NS._reject_unknown(
                s, {"sentence_id", "text", "citations", "numeric_tokens"}, "CitedSentence")
            sentences.append(CitedSentence(**s))
        paragraphs.append(CitedParagraph(paragraph_id=str(p.get("paragraph_id") or ""),
                                         sentences=tuple(sentences),
                                         aspect_ids=tuple(p.get("aspect_ids") or ())))
    return CitedSubsection(subsection_id=str(d.get("subsection_id") or ""),
                           title=str(d.get("title") or ""), paragraphs=tuple(paragraphs))


def _check_paragraph_aspects(*, draft: CitedProseDraft,
                             manifest: CitedWriterInputManifest) -> None:
    """每段声明的栏目必须落在**本小节声明的覆盖集合**内（越界即 fail-closed）。

    这条等式与「草稿小节必须与请求面同集合」同源：段声明了一个本小节不负责的栏目，读者面会
    把它读成「这一栏已经有人写了」，而实际上请求面根本没要求写它——两者的**产物**长得一样，
    差别只在这道门。空声明**不**在此判（它由段级归属轴与覆盖账各自处理）。
    """
    specs = {s.subsection_id: s for s in manifest.subsections}
    for subsection in draft.subsections:
        spec = specs.get(subsection.subsection_id)
        if spec is None:
            continue  # 小节集合不匹配由 `_check_subsection_coverage` 负责，不在这里重判
        allowed = set(spec.declared_aspect_ids)
        for paragraph in subsection.paragraphs:
            extra = sorted(set(paragraph.aspect_ids) - allowed)
            if extra:
                raise CitedWriterError(
                    f"小节 {subsection.subsection_id!r} 的段落 {paragraph.paragraph_id!r} "
                    f"声明了本小节不负责的栏目 {extra}（本小节声明覆盖 "
                    f"{sorted(allowed)}）：声明一个请求面没要求的栏目，会让读者把「没写」"
                    "读成「写了」",
                    reason="paragraph_aspect_out_of_subsection")


def derive_draft_id(draft: Any) -> str:
    return NS.content_id("cwd_", draft.identity_body())


# ---------------------------------------------------------------------------
# 采用去向（**按句引用**记，不按 proposal ID）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MaterialAdoptionRecord:
    """一个输入清单成员在本次写作中的**采用去向**。

    §0.20 明文要求「记下每份材料有没有被采用、被哪一句采用」——旧链按 proposal ID 记，新链没有
    proposal，因此按**句引用**记。`disposition='not_used'` 是**结论**（这份材料对本节的要求没有
    用），**不是**缺口：缺口只在「本小节要求的东西拿不到」时形成（见 `CitedProseGap`）。
    """

    record_id: str
    member_ref: str
    pack_id: str
    material_id: str
    citation_key: str
    sentence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("member_ref", "pack_id", "material_id", "citation_key"):
            if not str(getattr(self, name) or "").strip():
                raise CitedWriterError(f"MaterialAdoptionRecord.{name} 必须非空")
        if citation_key_axis(self.citation_key) != "material":
            raise CitedWriterError(
                f"MaterialAdoptionRecord.citation_key={self.citation_key!r} 不是材料轴键",
                reason="citation_key_wrong_axis", citation_key=self.citation_key)
        object.__setattr__(self, "sentence_ids",
                           tuple(str(x) for x in (self.sentence_ids or ())))
        expected = derive_adoption_id(self)
        if self.record_id != expected:
            raise CitedWriterError(
                f"MaterialAdoptionRecord.record_id 与内容不符：声明 {self.record_id!r}，"
                f"应为 {expected!r}", reason="adoption_id_mismatch")

    @property
    def disposition(self) -> str:
        return "adopted" if self.sentence_ids else "not_used"

    def identity_body(self) -> dict:
        return {"member_ref": self.member_ref, "pack_id": self.pack_id,
                "material_id": self.material_id, "citation_key": self.citation_key,
                "sentence_ids": list(self.sentence_ids)}

    def to_dict(self) -> dict:
        return {"record_id": self.record_id, "disposition": self.disposition,
                **self.identity_body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "MaterialAdoptionRecord":
        body = {"member_ref": str(kwargs.get("member_ref", "") or ""),
                "pack_id": str(kwargs.get("pack_id", "") or ""),
                "material_id": str(kwargs.get("material_id", "") or ""),
                "citation_key": str(kwargs.get("citation_key", "") or ""),
                "sentence_ids": [str(x) for x in (kwargs.get("sentence_ids") or ())]}
        return cls(record_id=NS.content_id("cadopt_", body),
                   **{**body, "sentence_ids": tuple(body["sentence_ids"])})

    @classmethod
    def from_dict(cls, d: Any) -> "MaterialAdoptionRecord":
        d = NS._reject_unknown(d, {"record_id", "disposition", "member_ref", "pack_id",
                                   "material_id", "citation_key", "sentence_ids"},
                               "MaterialAdoptionRecord")
        return cls(record_id=str(d.get("record_id") or ""),
                   member_ref=str(d.get("member_ref") or ""),
                   pack_id=str(d.get("pack_id") or ""),
                   material_id=str(d.get("material_id") or ""),
                   citation_key=str(d.get("citation_key") or ""),
                   sentence_ids=tuple(d.get("sentence_ids") or ()))


def derive_adoption_id(record: Any) -> str:
    return NS.content_id("cadopt_", record.identity_body())


def derive_material_adoptions(*, draft: CitedProseDraft,
                              manifest: CitedWriterInputManifest
                              ) -> tuple[MaterialAdoptionRecord, ...]:
    """草稿 × 输入清单 → **逐成员**的采用去向（每个成员恰一条）。

    「每个成员恰一条」是等式而不是约定：结果长度恒等于清单的材料行数，且 `not_used` 的那一份
    带空 `sentence_ids`。这样「材料有没有被用上」不必靠读正文去猜。
    """
    if draft.input_manifest_id != manifest.manifest_id:
        raise CitedWriterError(
            f"草稿绑的输入清单 {draft.input_manifest_id!r} 不是本次清单 "
            f"{manifest.manifest_id!r}：采用去向不得跨清单记账",
            reason="draft_manifest_mismatch")
    by_key: dict[str, list[str]] = {m.citation_key: [] for m in manifest.materials}
    for sentence in draft.sentences():
        for key in sentence.citations:
            entry = manifest.material_for_key(key)
            if entry is None:
                # 事实轴的键在材料表里查不到是**正常**的；只有键本身不属于本清单才是缺陷
                # （那已在 `validate_citations` 里逐句拦下）。
                continue
            by_key[entry.citation_key].append(sentence.sentence_id)
    return tuple(MaterialAdoptionRecord.create(
        member_ref=m.member_ref, pack_id=m.pack_id, material_id=m.material_id,
        citation_key=m.citation_key, sentence_ids=tuple(by_key[m.citation_key]))
        for m in manifest.materials)


# ---------------------------------------------------------------------------
# 引用核验（逐句；**唯一**一处判「这个键在不在本次输入里」）
# ---------------------------------------------------------------------------

def validate_citations(*, draft: CitedProseDraft, manifest: CitedWriterInputManifest) -> None:
    """逐句核对引用键**属于本次输入**；不属于即 fail-closed（带句子与键）。

    这是解析期的**结构**校验，与第二步的逐句**底线核对**（`sections/sentence_check.py`）不是
    同一件事：这里只问「这个键存在吗、轴对吗」，不问「这条引用支不支持这句话」。后者需要读
    材料原文并做确定性比对，属于第二步。
    """
    if draft.input_manifest_id != manifest.manifest_id:
        raise CitedWriterError(
            f"草稿绑的输入清单 {draft.input_manifest_id!r} 不是本次清单 "
            f"{manifest.manifest_id!r}", reason="draft_manifest_mismatch")
    known = set(manifest.all_keys())
    for sentence in draft.sentences():
        for key in sentence.citations:
            if key not in known:
                raise CitedWriterError(
                    f"句子 {sentence.sentence_id!r} 引用了本次输入清单里不存在的键 {key!r}"
                    f"（清单材料键 {list(manifest.material_keys())}，"
                    f"事实键 {list(manifest.fact_keys())}）",
                    reason="citation_not_in_input", sentence_id=sentence.sentence_id,
                    citation_key=key)


# ---------------------------------------------------------------------------
# 请求面 / 客户端 / 编排
# ---------------------------------------------------------------------------

#: 「只进诊断槽位」的展示档取值（与冻结 Contract 的 `DISPLAY_TIERS` 同名同义）。本链只认这
#: **一档**：其余取值一律按「可写进正文」处理，不为它编造第二档「半进正文」。
DISPLAY_TIER_DIAGNOSTIC_ONLY = "diagnostic_only"


def presentation_column_tiers(*, presentation_routing: Mapping[str, Any] | None,
                              fact_ids: Sequence[str]
                              ) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """这些事实经**呈现层声明**落到的展示档——返回 `(档位集合, 查不到档的栏目集合)`。

    两跳，且两跳都**不重新推导**，只读清单自己的声明：

    1. `presentation_routing["fact_columns"]`：`fact_id` → 它被声明落到的那一栏 /
       `metric_code`（这一行**没有**展示档字段）；
    2. `presentation_routing["routes"]`：那一栏 / 那个 `metric_code` → `contract_display_tier`。

    第 2 跳是必需的，不是冗余：展示档**只**声明在 `routes` 上。只看第 1 跳会得到「全都没有
    档」，那会把「诊断槽位事实进了正文」整类漏掉——而 `fin_solvency.interest_expense_proxy`
    这一类代理口径正是靠这一档才被挡在正文之外的。

    查得到栏目却查不到档的栏目**不贡献结论**，单独返回：调用方必须在自己的产物里如实写出
    「本轴不为它代言，也不假装它已核过」。这里**只**给结论，谁用它由调用方决定——请求面用它
    给事实行打档位，逐句核对用它判硬错，两处读的必须是同一份实现。
    """
    if not isinstance(presentation_routing, Mapping):
        return (), ()
    column_by_fact: dict[str, str] = {}
    metric_by_fact: dict[str, str] = {}
    for row in (presentation_routing.get("fact_columns") or ()):
        if not isinstance(row, Mapping):
            continue
        fact_id = str(row.get("fact_id") or "")
        column = str(row.get("presentation_column") or "")
        metric = str(row.get("metric_code") or "")
        if fact_id and column:
            column_by_fact[fact_id] = column
        if fact_id and metric:
            metric_by_fact[fact_id] = metric
    tier_by_metric: dict[str, str] = {}
    tier_by_column: dict[str, str] = {}
    for row in (presentation_routing.get("routes") or ()):
        if not isinstance(row, Mapping):
            continue
        tier = str(row.get("contract_display_tier") or "")
        if not tier:
            continue
        metric = str(row.get("metric_code") or "")
        column = str(row.get("presentation_column") or "")
        if metric:
            tier_by_metric[metric] = tier
        if column:
            tier_by_column[column] = tier
    tiers: list[str] = []
    unresolved: list[str] = []
    for raw in fact_ids:
        fact_id = str(raw or "")
        if not fact_id:
            continue
        tier = tier_by_metric.get(metric_by_fact.get(fact_id, ""))
        if tier is None:
            column = column_by_fact.get(fact_id, "")
            tier = tier_by_column.get(column) if column else None
        if tier:
            tiers.append(tier)
        elif column_by_fact.get(fact_id):
            unresolved.append(column_by_fact[fact_id])
    return tuple(dict.fromkeys(tiers)), tuple(dict.fromkeys(unresolved))


def _fact_display_tier(manifest: CitedWriterInputManifest,
                       entry: "CitedFactEntry") -> str:
    """一条事实行经呈现层声明落到的展示档；查不到（或本节没有这条轴）返回空串。"""
    tiers, _ = presentation_column_tiers(presentation_routing=manifest.presentation_routing,
                                         fact_ids=(entry.fact_id,))
    return tiers[0] if len(tiers) == 1 else ("" if not tiers else ",".join(tiers))


def diagnostic_slot_fact_keys(*, manifest: CitedWriterInputManifest) -> tuple[str, ...]:
    """清单里**只进诊断槽位**的那些权威事实的引用键（`display_tier=diagnostic_only`）。

    这是 §0.21 展示角色边界在**请求面**的落点：这些事实仍然在清单里、仍然可回查、仍然会进
    诊断槽位，但它们不得被写成普通正文结论，所以写作输入要把它们与正文可用事实**分层**摆在
    生成器面前，而不是只靠提示词里的一句劝告。判据只读清单自己的呈现层声明。
    """
    diagnostics: list[str] = []
    for entry in manifest.facts:
        tiers, _ = presentation_column_tiers(presentation_routing=manifest.presentation_routing,
                                             fact_ids=(entry.fact_id,))
        if DISPLAY_TIER_DIAGNOSTIC_ONLY in tiers:
            diagnostics.append(entry.citation_key)
    return tuple(diagnostics)


def numeric_authorization(*, manifest: CitedWriterInputManifest) -> dict:
    """清单级**正文数字授权**读视图（`cp-25`，`ndc-4` 立）。

    它回答一个此前请求面**答不出**的问题：这份清单里，**本版**哪几条事实可以作为正文数字
    授权、哪几条不可以、不可以的叫什么。在此之前请求面只有一根事实键轴（`citable_fact_keys`
    ＝ Pack 侧登记），而 `scp-13` 起「登记」与「本版可写」第一次分叉——分叉的那一处没有字段
    可承载，于是请求面**承诺**的东西正是逐句核对**拒绝**的东西（模型按目标写占比 ⇒ 必判
    `denominator_unverified` 硬错）。

    判据不是这里新造的：`withheld` 逐条来自 :func:`withheld_numeric_fact_keys`，而后者调的是
    `sections/sentence_check.py::fact_numeric_writability` —— 逐句核对自己那份实现。所以
    「请求面说能写」与「核对说能写」不可能各说各话。

    与 `diagnostic_slot_fact_keys` **同一先例**：与 `authority_facts` 并列而不混装，下游
    （含离线替身）不必再解析 `routes` 或自己判断 `value_kind`。`complete=True` 是结论——本块
    覆盖清单里**全部**事实键（`authorizable ∪ withheld ＝ 全部`），不是抽样。
    """
    from sections import sentence_check as SC
    withheld = withheld_numeric_fact_keys(manifest=manifest)
    withheld_keys = {key for key, _ in withheld}
    return {
        #: 判据的**政策版本**（`scp-*`）。请求面带上它，是为了让「这份面按哪一版判据说能写」
        #: 在产物上可分辨——旧 run 的面按它自己那一版读。
        "policy_version": SC.SENTENCE_CHECK_POLICY_VERSION,
        "authorizable_fact_keys": [str(e.citation_key) for e in manifest.facts
                                   if str(e.citation_key) not in withheld_keys],
        "withheld": [{"key": key, "state": state} for key, state in withheld],
        "complete": True,
        #: 一句话写清两个键轴的分工与撤回项的处置，避免下游把本块读成第二份登记表。
        "rule": ("authorizable_fact_keys 是 citable_fact_keys 的子集（本版可作正文数字授权）；"
                 "withheld 里的事实**仍在** authority_facts 与 citable_fact_keys 里、照旧可回查、"
                 "照旧要出现在审计去向中，但**不得**写成正文数字结论；其对应栏目按栏目目标留缺口。"),
    }


def build_cited_prose_request(*, manifest: CitedWriterInputManifest) -> dict:
    """输入清单 → 投给模型的 JSON（**逐字**原文与事实，不做任何摘要或改写）。

    `materials[].text` 是材料正文本身（`wmctx-1` 的读视图），`facts[].text` 是权威事实的
    命题文本。两者都**原样**出现：模型拿到的就是复核者拿到的同一串字节。
    """
    return {
        "policy_version": manifest.policy_version,
        "prompt_version": CITED_WRITER_PROMPT_VERSION,
        "task_id": manifest.task_id,
        "section_id": manifest.section_id,
        "section_title": manifest.section_title,
        "subsections": [
            {"subsection_id": s.subsection_id, "title": s.title,
             "requirement_text": s.requirement_text,
             #: 本小节声明覆盖的 Contract 栏目（`cwm-6`，至少一个）：模型必须能看出「这一节
             #: 要写的是哪几件事」，而它的 id 与标题都只是可读标签，栏目身份要显式给出。
             #: 每段还要在返回里声明它服务这几栏中的哪几栏（`cw-4` 的 `paragraphs[].aspect_ids`），
             #: 否则「一个小节覆盖十几栏」在产物上就退化成一句无法核对的话。
             "declared_aspect_ids": list(s.declared_aspect_ids),
             #: 该栏允许的检索来源类（`cwm-5`，逐字取自冻结 Contract）。摆在请求面上有两个
             #: 用途：一是让「写不出来时该往哪一类来源补」有据可依（补件的来源类由系统按这里
             #: 推导，模型不需要、也不该自选一个）；二是让「本栏要的东西是不是本来就该由这一
             #: 类来源给出」在生成时可见。空数组是**结论**——该栏在 Contract 里没有证据要求。
             "allowed_source_classes": list(s.allowed_source_classes),
             #: **逐栏**的可引用来源键（`cp-17`）。把「本栏有哪些登记到本栏的候选」从一次
             #: 隐含的集合运算摆成一份看得见的清单：`aspect_id` + 它那一行逐字要求 + 登记到
             #: 该栏的**材料键**与**事实键**。它取代的那一步交叉（18 栏 × 65 行的 `aspect_ids`
             #: 求交）在 cp-16 真实公司节里没被做出来——六条经营模式栏被自述成「未单独登记到
             #: 本栏」，而每栏其实都登记着三行。**空数组是结论**（本栏本次零登记）。
             #: 它不授权任何一条来源写成结论，也不放宽第 4 条：登记始终是必要条件。
             "citable_columns": citable_columns(manifest, s),
             #: **阅读顺序提纲**（`co-4`，`cp-20` 立 `co-1`）。把提示词第 3 条的业务导航线按冻结
             #: Contract 的栏位身份派生成看得见的分步计划：每一步的 `aspect_ids`、各栏逐字的
             #: `requirement_text`、该步的写作提示，以及**该步各栏的目标与可引的键** `columns`
             #: （`co-3` 起含 `content_objective` / `citation_opportunity`，逐字投影自上行
             #: `citable_columns`，同一份登记轴，不新增判定、不新增候选；`co-4` 只改其中
             #: `sales_mode` 一条目标文本）。
             #: 它是**写作组织**，不是资格判定——登记仍是必要条件，数字仍过第 4 条。
             #: **可能为 `null`**：本小节声明的栏与这张提纲一条都不沾时（财务小节）不发该字段。
             #: `null` 是结论（这一节不适用这张业务提纲），不是「读取失败」。
             "content_outline": build_content_outline(s, manifest=manifest)}
            for s in manifest.subsections],
        "materials": [
            {"key": m.citation_key, "ref": m.member_ref, "topic_id": m.topic_id,
             "material_id": m.material_id, "material_type": m.material_type,
             #: 这份材料在 Pack 侧**登记归属**的栏目。它摆在请求面上，是因为「拿这一栏的材料
             #: 写这一栏的字」这条要求只有把登记轴摆在生成器面前才可执行；只写进 prompt 而
             #: 不给出取值，等于让模型猜哪份材料属于哪一栏。
             "aspect_ids": list(m.aspect_ids),
             "source_identity": m.source_identity, "source_role": m.source_role,
             #: **来源文档身份**（`cp-9`）。同一 `document_id` 的材料来自同一份来源文档；把它摆到
             #: 请求面上，第 3c 条「先按文档分组、当前态取最新一期、同一份材料只在一处充当支撑」
             #: 才可执行。无文档系列时为空串——那是**结论**（这份来源没有可归的文档），不是缺字段。
             "document_id": m.document_id,
             #: 内容形态与「这是表吗」在**请求面**就要可读：模型必须能看出这一行是勾选表单行
             #: 而不是叙述正文、这一行是压平的表文本（其数字本次不获格级授权）。让模型靠正文
             #: 长相去猜，等于把「不得冒充事实」这条硬约束变成一次赌博。
             "content_kind": m.content_kind, "is_table": m.structured_view is not None,
             #: 跨块切分读法（`cwm-7`）：非空表示本行与相邻行是**同一段原文**被 Evidence 块
             #: 边界切开的相邻两片（片序 / 共片数 / 本片在 span 本地区的精确区间 + 前后片 ref）。
             #: 摆在请求面上，是因为「这两片要连起来读」这条要求只有把相邻关系摆到生成器面前
             #: 才可执行；而 `text` / `locator` 仍是**本片自己的**——续接只陈述关系，不合并来源。
             "span_continuity": (dict(m.span_continuity) if m.span_continuity else None),
             "locator": dict(m.locator_ref), "payload_hash": m.payload_hash,
             "authority_kind": m.authority_kind, "text": m.reading_view}
            for m in manifest.materials],
        "authority_facts": [
            {"key": f.citation_key, "authority_kind": f.authority_kind,
             "container_id": f.container_identity, f.fact_field: f.fact_id,
             "text": f.text, "topic_id": f.topic_id, "aspect_ids": list(f.aspect_ids),
             "fact_type": f.fact_type, "period": f.period, "scope": f.scope,
             "required": f.required, "locator": f.locator_ref,
             #: 本行经**呈现层声明**落到的展示档（`cp-8`）。空串是**结论**——本行查不到档，
             #: 不是「档位是空」。它摆在请求面上，是因为「这一条只能进诊断槽位」这条硬约束
             #: 只有把档位摆在生成器面前才可执行；只写进提示词而不给出取值，等于让模型自己
             #: 去 `routes` 里对——那正是代理口径被写进正文那一次的形状。
             "display_tier": _fact_display_tier(manifest, f)}
            for f in manifest.facts],
        #: 只进诊断槽位的那些事实的**引用键**（`cp-8`，与上行 `display_tier` 同一份实现）。
        #: 与 `authority_facts` **并列而不混装**：那一块是「这份事实是什么」，这一块是「这一
        #: 份不得被写成正文结论」。把它单列，下游（含离线替身）不必自己再解析一遍 `routes`。
        "diagnostic_slot_fact_keys": list(diagnostic_slot_fact_keys(manifest=manifest)),
        #: 正文数字授权读视图（`cp-25`，`ndc-4` 立）。与上行同一先例：与 `authority_facts`
        #: 并列而不混装。它不新增候选、不放宽任何判据——`authorizable_fact_keys` 逐条是清单里
        #: **已有**的事实键，`withheld` 逐条来自逐句核对那份 `scp-13` 判定（同一实现）。
        #: **它不改变 `citable_fact_keys` 的登记语义**：那根轴照旧是「Pack 侧登记到本栏」，
        #: 本块只是在其旁边摆出「其中本版能写哪几条」。
        "numeric_authorization": numeric_authorization(manifest=manifest),
        #: 呈现层路由声明（`cwm-4`）。它与 `authority_facts[].aspect_ids` **并列而不混装**：
        #: 后者是权威登记（本链上财务事实恒为空），前者是一条显式声明、带自己的版本号与
        #: 来源标记。摆在请求面上，是因为「这个指标只能写进这一栏」这条要求在只写进 prompt
        #: 而拿不到取值时不可执行——那正是「拿流动比率去充净资产水平」这一类错栏的入口。
        "presentation_routing": manifest.presentation_routing,
    }


@dataclass(frozen=True)
class CitedProseResult:
    """一次生成调用的**结构化**结果（文本 + 完整调用元数据）。

    与 `pack_writer.NarrationResult` 同一纪律：只回传字符串等于丢掉「这次正文是谁、按哪版
    prompt、以什么代价生成」的全部证据；替身与真实 client 必须返回**同一**结构。
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
        if self.status not in ("ok", "error"):
            raise CitedWriterError(
                f"CitedProseResult.status={self.status!r} 只能是 'ok' / 'error'")
        for name in ("call_id", "model", "prompt_version"):
            if not str(getattr(self, name) or ""):
                raise CitedWriterError(f"CitedProseResult.{name} 不得为空（调用元数据必须完整）")
        if not isinstance(self.text, str):
            raise CitedWriterError("CitedProseResult.text 必须是字符串")
        if self.status == "error" and not self.error:
            raise CitedWriterError("CitedProseResult.status='error' 必须带 error 说明")
        if not isinstance(self.latency_ms, int):
            raise CitedWriterError("CitedProseResult.latency_ms 必须是整数毫秒")

    @property
    def response_hash(self) -> str:
        return NS.body_fingerprint_of(self.text)

    def to_dict(self) -> dict:
        return {"call_id": self.call_id, "model": self.model,
                "prompt_version": self.prompt_version, "status": self.status,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "latency_ms": self.latency_ms, "finish_reason": self.finish_reason,
                "error": self.error, "response_hash": self.response_hash}


class CitedProseClient(Protocol):
    """新写作链能拿到的全部外部能力。没有任何检索/工具入口。"""

    def compose(self, *, messages: Sequence[Mapping[str, str]], system: str,
                prompt_version: str, model_policy: str) -> CitedProseResult: ...


def truncated_call_record(exc: Any, *, model: str | None, prompt_version: str,
                          model_policy: str) -> dict:
    """一次**被截断**的调用在客户端自己的失败记录里长什么样。

    与成功记录的差别是刻意的：这里**没有** `text` / `response_hash`——被截断的半截内容不是
    正文，给它算一个「响应哈希」等于把残缺内容升格成可引用的产物。留下的只有「这次调用确实
    发生过」所需的身份：call_id、模型、prompt 版本、prompt 归属策略、`finish_reason`、usage、
    延迟与错误文本。

    本函数**只**构造记录，不吞异常：调用方拿到记录后仍须把原异常抛出去（见两个 client）。
    """
    resp = getattr(exc, "response", None)
    return {"call_id": str(getattr(resp, "call_id", "") or ""),
            "model": str(getattr(resp, "model", "") or "") or str(model or ""),
            "prompt_version": prompt_version, "model_policy": model_policy,
            "status": "error",
            "finish_reason": getattr(resp, "finish_reason", None),
            "input_tokens": getattr(resp, "input_tokens", None),
            "output_tokens": getattr(resp, "output_tokens", None),
            "latency_ms": int(getattr(resp, "latency_ms", 0) or 0),
            "error": f"{type(exc).__name__}: {exc}"}


class LlmCitedProseClient:
    """把 `llm.client` 适配成 `CitedProseClient`（不带工具、不重试、不缓存）。

    `calls` 逐次记下本适配器**真正发起**的调用（含失败），与离线替身的同名属性对齐。
    `reject_truncated=True`（缺省）：provider 说输出被截断时这次调用**算失败**，适配器抛出
    `LLMTruncatedResponse`，**不**返回一份残缺结果——半截 JSON 被当成输出流下去，下游只会
    看到「JSON 解析失败」，真正的原因（截断）就丢了。

    截断这条路上，「抛出去」与「记下来」是**两件都要做**的事：异常向上停住整轮（不重试、
    不换模型），而这次失败也必须在 `calls` 里留一条——只抛不记，等于把一次真实发生的、
    已经占掉额度的调用从客户端自己的流水里抹掉。
    """

    def __init__(self, *, model: str | None = None, max_tokens: int = 8192,
                 thinking: dict | None = None, reject_truncated: bool = True) -> None:
        self.model = model
        self.max_tokens = int(max_tokens)
        self.thinking = thinking
        self.reject_truncated = bool(reject_truncated)
        self.calls: list[dict] = []

    def compose(self, *, messages: Sequence[Mapping[str, str]], system: str,
                prompt_version: str, model_policy: str) -> CitedProseResult:
        from llm import client as llm  # 延迟导入：离线测试不必加载 provider 依赖

        try:
            resp = llm.chat_with_usage(list(messages), system=system, model=self.model,
                                       max_tokens=self.max_tokens, prompt_version=prompt_version,
                                       thinking=self.thinking,
                                       reject_truncated=self.reject_truncated)
        except llm.LLMTruncatedResponse as exc:
            self.calls.append(truncated_call_record(
                exc, model=self.model, prompt_version=prompt_version,
                model_policy=model_policy))
            raise
        except Exception as exc:  # noqa: BLE001 - 任何 provider 失败都如实记为 error 结果
            record = {"call_id": "", "model": self.model or "", "status": "error",
                      "prompt_version": prompt_version, "error": f"{type(exc).__name__}: {exc}"}
            self.calls.append(record)
            return CitedProseResult(text="", call_id=self._fallback_call_id(),
                                    model=self.model or "", prompt_version=prompt_version,
                                    status="error", error=record["error"])
        result = CitedProseResult(
            text=str(getattr(resp, "text", "") or ""),
            call_id=str(getattr(resp, "call_id", "") or "") or self._fallback_call_id(),
            model=str(getattr(resp, "model", "") or "") or (self.model or ""),
            prompt_version=prompt_version,
            status="ok",
            input_tokens=getattr(resp, "input_tokens", None),
            output_tokens=getattr(resp, "output_tokens", None),
            latency_ms=int(getattr(resp, "latency_ms", 0) or 0),
            finish_reason=getattr(resp, "finish_reason", None))
        self.calls.append({**result.to_dict(), "model_policy": model_policy})
        return result

    @staticmethod
    def _fallback_call_id() -> str:
        import uuid
        return f"local-{uuid.uuid4().hex[:16]}"


def build_cited_prose_messages(*, manifest: CitedWriterInputManifest,
                               system: str) -> tuple[list[dict], str]:
    """请求面：**一段** user 消息（完整 JSON 输入）+ system prompt。"""
    payload = build_cited_prose_request(manifest=manifest)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
    return ([{"role": "user", "content": text}], system)


def declared_prompt_identity(text: str) -> tuple[str, str] | None:
    """从资产第 1 行取出它**自称**的 `(asset, revision)`；取不到返回 `None`。

    只认 :data:`_PROMPT_HEADER_RE` 那一种句式。取不到不是「版本对」——见
    :func:`load_cited_writer_prompt`。
    """
    first_line = (text or "").splitlines()[0] if (text or "").splitlines() else ""
    match = _PROMPT_HEADER_RE.search(first_line)
    if not match:
        return None
    return match.group(1), match.group(2)


def load_cited_writer_prompt() -> str:
    """加载本链的 system prompt（`llm/prompts/cited_company_prose_v1.txt`）。

    **加载即对账**：资产头声明的 `(asset, revision)` 必须与
    :data:`CITED_WRITER_PROMPT_REVISION` 一致。理由不是洁癖——`prompt_version` 会随每次调用
    落进调用账本与草稿身份体，而修订号改了、头部没改（或反过来）时，账本上写的是一个**没有
    任何资产与之对应**的版本号：事后回查时没有任何字节能证明当时发出去的提示词长什么样。
    取不到声明句式同样拒绝——「头部格式漂移」与「版本对得上」在这条线上不可区分，按 fail-closed
    处理。
    """
    from llm import client as llm
    text = llm.load_prompt(CITED_WRITER_PROMPT_ASSET)
    declared = declared_prompt_identity(text)
    if declared is None:
        raise CitedWriterError(
            f"prompt 资产 {CITED_WRITER_PROMPT_ASSET!r} 第 1 行没有声明的 "
            f"`（<asset>，revision <rev>）`，无法与 CITED_WRITER_PROMPT_REVISION 对账",
            reason="prompt_asset_identity_missing")
    asset, revision = declared
    if (asset, revision) != (CITED_WRITER_PROMPT_ASSET, CITED_WRITER_PROMPT_REVISION):
        raise CitedWriterError(
            f"prompt 资产自称 {asset}@{revision}，本链声明的是 "
            f"{CITED_WRITER_PROMPT_VERSION}：账本上的 prompt_version 将不对应任何字节",
            reason="prompt_asset_identity_mismatch")
    return text


# ---------------------------------------------------------------------------
# 解析：模型返回 → 草稿（唯一一处把文本变成结构化正文）
# ---------------------------------------------------------------------------

def parse_cited_prose(text: Any, *, manifest: CitedWriterInputManifest,
                      task_id: str = "", section_id: str = "",
                      writer_identity: str = "") -> CitedProseDraft:
    """模型返回的 JSON → `CitedProseDraft`（逐句核验引用键属于本次输入）。

    只接受**一个完整 JSON 值**（允许前后空白）。解析失败、结构不符、引用键不属于本次输入，
    任一即 `CitedWriterError`——**不**做「尽力解析出半份草稿」：半份草稿会让读者面分不清
    「模型没写」与「解析丢了」。

    进构造器**之前**先过一次结构归一（`crn-1`，见
    :mod:`sections.cited_reply_normalize`）：删掉可证明是同一件事第二份副本的小节内缺口、
    由程序分配全节唯一的句子编号。归一化本身也是 fail-closed 的——不一致就拒绝，不猜。

    本函数是 :func:`parse_cited_prose_with_normalization` 的**薄包装**：签名与返回类型逐字
    不变，需要那份编号/去重台账的调用方（留存、读回）走那一个。
    """
    return parse_cited_prose_with_normalization(
        text, manifest=manifest, task_id=task_id, section_id=section_id,
        writer_identity=writer_identity)[0]


def empty_shell_context(*, payload: Any, manifest: CitedWriterInputManifest) -> Any:
    """本轮的**空壳段判定读数**（`crn-4` 第三条规则）：归属 + 有没有来源 + 有没有那条缺口。

    四项都**只**由清单侧算出来，不读模型的说法：

    * 登记数走 :func:`registered_source_keys`——与 :func:`_gap_from_payload` 的改判、
      请求面 `citable_columns` 用的是**同一个**函数，不另造一份「这一栏有没有来源」的口径。
    * 「顶层有没有 `no_source_in_manifest` 缺口」走 :func:`aspect_for_requirement`
      （要求文本 → 挨着的那一栏）加 :func:`assign_gap_reason`（**系统判定**，不是模型自述的
      那个字）。缺口一条都落不到栏上时，那一栏**不**被算作「已声明无来源」。
    * **归属**（这一栏是不是这一段自己那一节的栏目）走 :func:`manifest_spec_for_subsection`
      → `declared_aspect_ids`：与请求面 `citable_columns`、下游
      :func:`_check_paragraph_aspects` 读的是**同一份**声明，不另造一份口径。
    * **缺口支持**（`crn-4` 新增）：同一小节内**可唯一对应**的那条顶层缺口的系统理由，走
      :func:`unique_subsection_gap_reasons`——本函数与离线读回侧共用同一份口径。

    **本函数是 fail-soft 的，而且方向是单向的**：读不动的缺口、读不动的规格一律**跳过**，
    于是 `EmptyShellContext` 只会**少**认出「已声明无来源」的栏、只会**多**报「有来源」的栏、
    只会在**查不到该小节**时不认归属、只会在**缺口读不动或对不上栏**时少一条缺口支持。
    前三种情况都让空壳段**留下来**；第四种是 `crn-4` 那条新依据**取不到即不成立**（保守方向与
    前三项一致），因此它同样只可能**少删**。四种情况都不抢在归一的
    `reply_field_malformed` / `gap_field_malformed` 之前报出另一个原因码——那一类输入在这里
    只是没被读懂，随后仍由 :mod:`sections.cited_reply_normalize` 用它自己的话拒绝。

    删一段的依据**不是**「这一栏登记了材料」：登记数只决定这一栏走到哪个码上（有来源 →
    `source_present_but_not_admissible`，零来源 → `no_source_in_manifest`），
    真正放行的是那条**系统判定为「本栏本次没有可写来源」的缺口**；被删的段零句零引用，
    不进栏目覆盖、事实资格、审阅通过与系统放行。
    """
    from sections import cited_reply_normalize as CRN

    counts: dict[str, int] = {}
    declared_by_subsection: dict[str, frozenset[str]] = {}
    subs = payload.get("subsections") if isinstance(payload, dict) else None
    for sub in (subs if isinstance(subs, (list, tuple)) else ()):
        if not isinstance(sub, dict):
            continue
        spec = manifest_spec_for_subsection(manifest, str(sub.get("subsection_id") or ""))
        if spec is None:
            continue
        #: 只有**这一次返回真的写了的小节**才登记归属：没写到的小节与「查不到」在判定上同形，
        #: 而后者一律不许删（见 `EmptyShellContext` 的类注释），因此不必、也不该替它补一条。
        declared_by_subsection[str(sub.get("subsection_id") or "")] = frozenset(
            str(a) for a in spec.declared_aspect_ids)
        for aspect in spec.declared_aspect_ids:
            aspect = str(aspect)
            if aspect in counts:
                continue
            counts[aspect] = len(registered_source_keys(manifest, (aspect,)))

    unsourced: set[str] = set()
    gap_rows: list[tuple[str, str, str]] = []
    gaps = payload.get("gaps") if isinstance(payload, dict) else None
    for gap in (gaps if isinstance(gaps, (list, tuple)) else ()):
        if not isinstance(gap, dict):
            continue
        subsection_id = str(gap.get("subsection_id") or "")
        spec = manifest_spec_for_subsection(manifest, subsection_id)
        column = aspect_for_requirement(spec, str(gap.get("requirement_text") or ""))
        if not column:
            continue
        reason, _assignment = assign_gap_reason(
            claimed_reason=str(gap.get("reason") or ""), spec=spec,
            registered_count=counts.get(column, len(registered_source_keys(manifest, (column,)))))
        if reason == "no_source_in_manifest":
            unsourced.add(column)
        #: 第四项读数（`crn-4`）的原料：**逐条**缺口的三元组。「可唯一对应」由
        #: :func:`unique_subsection_gap_reasons` 一处定义，生产侧与离线读回侧共用同一份口径。
        gap_rows.append((subsection_id, column, reason))

    return CRN.EmptyShellContext(
        registered_source_counts=counts,
        no_source_gap_aspects=frozenset(unsourced),
        subsection_aspect_ids=declared_by_subsection,
        subsection_gap_reason=unique_subsection_gap_reasons(gap_rows))


def parse_cited_prose_with_normalization(
        text: Any, *, manifest: CitedWriterInputManifest, task_id: str = "",
        section_id: str = "", writer_identity: str = "") -> tuple[CitedProseDraft, Any]:
    """同 :func:`parse_cited_prose`，但把归一化台账（`crn-1`）一并交回。

    台账**不进**草稿身份体（`draft_id` 是内容寻址，不该因模型当初用什么 ID 而变），它属于
    留存证据：`write_cited_section` 把它写进 `cited_call_journal.json`。
    """
    import json

    from sections import cited_reply_normalize as CRN

    raw = str(text if text is not None else "").strip()
    if not raw:
        raise CitedWriterError("模型返回为空（没有可解析的正文）", reason="empty_response")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CitedWriterError(
            f"模型返回不是一个完整 JSON 值：{exc}", reason="response_not_json") from exc
    if not isinstance(payload, dict):
        raise CitedWriterError("模型返回的 JSON 顶层必须是对象", reason="response_not_object")
    normalized = CRN.normalize_cited_reply(
        payload, empty_shell=empty_shell_context(payload=payload, manifest=manifest))
    payload = normalized.payload

    identity = writer_identity or CITED_WRITER_PROMPT_VERSION
    revision = derive_prose_revision(input_manifest_id=manifest.manifest_id,
                                     writer_identity=identity)
    draft = CitedProseDraft.create(
        task_id=task_id or manifest.task_id,
        section_id=section_id or manifest.section_id,
        input_manifest_id=manifest.manifest_id,
        writer_identity=identity,
        subsections=tuple(_subsection_from_dict(s) for s in (payload.get("subsections") or ())),
        gaps=tuple(_gap_from_payload(g, manifest=manifest)
                   for g in (payload.get("gaps") or ())),
        follow_up_needs=tuple(_follow_up_need_from_payload(
            n, prose_revision=revision, manifest=manifest, writer_identity=identity)
            for n in (payload.get("follow_up_needs") or ())))
    _check_subsection_coverage(draft=draft, manifest=manifest)
    _check_paragraph_aspects(draft=draft, manifest=manifest)
    validate_citations(draft=draft, manifest=manifest)
    return draft, normalized


def _gap_from_payload(payload: Any, *,
                      manifest: CitedWriterInputManifest) -> CitedProseGap:
    """返回里的一条缺口 → `CitedProseGap`，**理由由系统判定**。

    生成器写的那一个原样留在 `claimed_reason` 里。判定规则见 :func:`assign_gap_reason`。
    """
    payload = NS._reject_unknown(
        payload, {"subsection_id", "requirement_text", "reason", "detail"},
        "CitedProseGap 返回")
    subsection_id = str(payload.get("subsection_id") or "")
    requirement_text = str(payload.get("requirement_text") or "")
    spec = manifest_spec_for_subsection(manifest, subsection_id)
    # 数登记数要数**这一条要求所属的那一栏**，不是整节。`aspect_for_requirement` 能把这条
    # 要求的文本逐字对到栏位上时就用那一个；对不上（或本小节没有规格）才退回整节口径——
    # 退回是**保持本模块原有的较宽口径**，不是「猜一个栏目」。
    # 计的是**来源**（材料行 ∪ 事实行），不是只有材料行：财务节 `materials` 为空、落栏靠
    # `presentation_routing.fact_columns`，只数材料会把它一律读成「本栏没有来源」。
    column = aspect_for_requirement(spec, requirement_text)
    scope: "str | Sequence[str]" = (column,) if column else (
        spec.declared_aspect_ids if spec else ())
    registered = registered_source_keys(manifest, scope)
    claimed = str(payload.get("reason") or "")
    reason, assignment = assign_gap_reason(
        claimed_reason=claimed, spec=spec, registered_count=len(registered))
    return CitedProseGap.create(
        subsection_id=subsection_id,
        requirement_text=requirement_text,
        reason=reason, detail=str(payload.get("detail") or ""),
        reason_assignment=assignment, claimed_reason=claimed)


def _follow_up_need_from_payload(payload: Any, *, prose_revision: str,
                                 manifest: CitedWriterInputManifest,
                                 writer_identity: str) -> CitedFollowUpNeed:
    """返回里的一条补件需求 → `CitedFollowUpNeed`，**来源类由 Contract 推导**。

    返回里的 `expected_source_class` 若还在（旧修订号的模型可能仍会写），只作审计收进
    `writer_claimed_source_class`，**不**参与取值、**不**进身份体、**不**被下游当指令读。
    """
    payload = NS._reject_unknown(
        payload, {"statement", "subsection_id", "aspect_id", "topic_id", "requiredness",
                  "expected_source_class"}, "CitedFollowUpNeed 返回")
    subsection_id = str(payload.get("subsection_id") or "")
    spec = manifest_spec_for_subsection(manifest, subsection_id)
    classes, derivation = derive_expected_source_classes(spec=spec)
    return CitedFollowUpNeed.create(
        statement=str(payload.get("statement") or ""), subsection_id=subsection_id,
        target_requirement_text=spec.requirement_text if spec else subsection_id,
        topic_id=str(payload.get("topic_id") or ""), aspect_id=str(payload.get("aspect_id") or ""),
        section_id=manifest.section_id, prose_revision=prose_revision,
        requiredness=str(payload.get("requiredness") or "optional"),
        expected_source_classes=classes, source_class_derivation=derivation,
        writer_claimed_source_class=str(payload.get("expected_source_class") or ""),
        writer_identity=writer_identity)


def _check_subsection_coverage(*, draft: CitedProseDraft,
                               manifest: CitedWriterInputManifest) -> None:
    """草稿小节必须与请求面小节**同集合、同顺序**（少一个、多一个、换个位置都 fail-closed）。

    与「清单不等于 Pack 成员即拒」同一纪律：请求了 5 个栏目、只写了 3 个，读者的第一反应是
    「另外 2 个没有内容」——而真实原因可能是「模型漏了」。两者在**没有这条等式**时不可区分。

    顺序是**另一条**等式，不能被集合比较吸收：提示词第 1 条硬约束要求 `subsections` 的内容与
    顺序逐一对应输入，而下游的栏目读回、缺口定位、人读页都是**按位置**解释这份草稿的——集合
    相同而顺序不同的草稿，会让「第 3 个栏目写了什么」这句话从第一个错位处起整段失效。所以这里
    实际比顺序，而不是声明同序却先排序再比。
    """
    asked = [s.subsection_id for s in manifest.subsections]
    got = [s.subsection_id for s in draft.subsections]
    if sorted(asked) != sorted(got):
        missing = sorted(set(asked) - set(got))
        extra = sorted(set(got) - set(asked))
        raise CitedWriterError(
            f"草稿小节与请求面小节不一一对应：缺 {missing}，多 {extra}",
            reason="subsection_coverage_mismatch")
    if asked != got:
        index = next(i for i, (a, b) in enumerate(zip(asked, got)) if a != b)
        raise CitedWriterError(
            f"草稿小节与请求面小节集合相同但顺序不同：第 {index + 1} 个位置请求 "
            f"{asked[index]!r}，草稿写的是 {got[index]!r}"
            f"（提示词要求逐一对应）",
            reason="subsection_order_mismatch")


@dataclass(frozen=True)
class CitedProseOutcome:
    """一次新写作的**完整**结果：草稿 + 逐成员采用去向 + 调用元数据。"""

    draft: CitedProseDraft
    adoptions: tuple[MaterialAdoptionRecord, ...]
    call: dict

    def to_dict(self) -> dict:
        return {"draft": self.draft.to_dict(),
                "adoptions": [a.to_dict() for a in self.adoptions],
                "call": dict(self.call)}


def write_cited_section(*, manifest: CitedWriterInputManifest, client: CitedProseClient,
                        system: str | None = None, model_policy: str = "stub",
                        prompt_version: str = CITED_WRITER_PROMPT_VERSION,
                        max_tokens: int | None = None,
                        journal: Any | None = None) -> CitedProseOutcome:
    """新写作链的**唯一**执行入口：输入清单 → 调用 → 解析 → 采用去向。

    解析失败时**抛出**（不返回半份结果）：草稿是读者面与复核面的共同输入，「一半的草稿」
    无法被任何下游诚实地解释。客户端每次真正发起的调用记在 `client.calls` / `call` 上。

    `journal` 是**失败前留存**的可选口（见 :mod:`sections.cited_call_journal`）：发请求**之前**
    落输入清单身份与请求面原文，收到回复之后、解析**之前**落可见回复与解析状态。它**不改变**
    本函数的成败判定，也**不吞**异常：留存写不进去时它自己记录写失败，原异常照常抛出
    （`M930-3` r2 缺的正是这份留存，导致该轮只能做离线派生诊断）。
    """
    messages, system_text = build_cited_prose_messages(
        manifest=manifest, system=system if system is not None else load_cited_writer_prompt())
    if journal is not None:
        journal.record_input(
            section_id=manifest.section_id, manifest=manifest,
            prompt_version=prompt_version, model_policy=model_policy,
            model=str(getattr(client, "model", "") or ""),
            thinking=getattr(client, "thinking", None),
            request_face=str(messages[0].get("content", "")) if messages else "",
            system_text=system_text)
    if max_tokens is not None and isinstance(client, LlmCitedProseClient):
        client.max_tokens = int(max_tokens)
    try:
        result = client.compose(messages=messages, system=system_text,
                                prompt_version=prompt_version, model_policy=model_policy)
    except Exception as exc:  # noqa: BLE001 - 客户端自己抛的那条路（截断、传输失败）也要留痕
        #: 客户端在**没有可交出的可见回复**时会直接抛（真实 r1 那次截断就是这样）。这时没有
        #: `result` 可记，但「这一轮死在收到回复之前」本身必须留在同一份留存里——否则事后只能
        #: 从 `logs/llm` 反推，而那正是 r2 缺失的那一半。记完原样抛出，不改变成败判定。
        if journal is not None:
            journal.record_reply(
                call_id=str(getattr(exc, "call_id", "") or ""),
                status="client_error", error=f"{type(exc).__name__}: {exc}", text="",
                finish_reason=str(getattr(exc, "finish_reason", "") or ""))
        raise
    if journal is not None:
        journal.record_reply(
            call_id=str(getattr(result, "call_id", "") or ""),
            status=str(getattr(result, "status", "") or ""),
            error=str(getattr(result, "error", "") or ""), text=result.text,
            finish_reason=str(getattr(result, "finish_reason", "") or ""),
            input_tokens=getattr(result, "input_tokens", None),
            output_tokens=getattr(result, "output_tokens", None),
            latency_ms=getattr(result, "latency_ms", None),
            model=str(getattr(result, "model", "") or ""))
    if result.status != "ok":
        raise CitedWriterError(
            f"写作调用失败：{result.error or result.status}", reason="write_call_failed")
    try:
        draft, normalization = parse_cited_prose_with_normalization(
            result.text, manifest=manifest, writer_identity=result.call_id)
    except Exception as exc:  # noqa: BLE001 - 解析期的任何拒绝都要落进留存，再原样抛出
        #: 这里刻意抓宽：漏掉某一种异常类型只会让留存里少一句「解析状态」，而**不**改变
        #: 成败判定（下面的 `raise` 让原异常原样逃逸）。可见回复在进 `try` 之前就已落盘，
        #: 因此即使这里一条都没记成，「模型到底返回了什么」也不会丢。
        if journal is not None:
            journal.record_parse(ok=False, error_type=type(exc).__name__, error=str(exc),
                                 reason=str(getattr(exc, "reason", "") or ""))
        raise
    if journal is not None:
        journal.record_parse(ok=True, draft_id=draft.draft_id,
                             sentence_count=len(draft.sentence_ids()),
                             subsection_count=len(draft.subsections),
                             normalization=normalization.to_dict())
    adoptions = derive_material_adoptions(draft=draft, manifest=manifest)
    return CitedProseOutcome(draft=draft, adoptions=adoptions, call=result.to_dict())

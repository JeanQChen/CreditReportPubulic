"""§0.20 第二步（后半）：**独立只读语义审阅**（`crv-3` / `crvp-3` / prompt `@crr-2`）。

本模块只做审阅面的**输入装配**与**响应解析**，一个字都不改稿，也**不发起**任何调用：

* **它不调用模型。** 与 `sections/cited_writer.py` 同一分工：请求面在这里组装，client 由调用方
  注入（`CitedReviewClient`），本模块只负责把返回的 JSON 变成 `ReviewIssue[]`。真正的调用发生在
  运行脚本里，且本批没有真实 run。
* **它不改正文。** 响应里任何承载"改写后文本"的字段（`rewritten_text` / `revised_text` /
  `new_text` / `final_text` …）都落进 `assurance.schema.REVIEWER_FORBIDDEN_FIELDS`，在
  `ReviewIssue.from_dict` 构造期就被指名拒绝；本模块的响应信封也只登记 `issues` 一个键，
  多一个键即 `未登记字段`。
* **它不放行。** 本模块的任何对象都不带 `publishable` / `passed` / `released` 之类的字段；
  信封层另有一份指名禁列（见 ``_reject_release_fields``），因为"没有发现问题"最容易被顺手写成
  "通过"。
* **它不覆盖硬错误，但它的一票也换不掉硬错误。** 机械核对（`sections/sentence_check.py`）判为
  `hard_error` 的句子收到审阅的 `category="supported"` 时，**两条轴各自留档**：审阅意见照记
  （`hard_error_override_sentence_ids`），硬错误照记（`hard_error_sentence_ids`），下游聚合时
  机械硬错误**优先**——有硬错误就不得出现"系统审阅通过"。`crv-3` 起这里**不再抛错**：审阅面
  是**故意盲的**（请求面只含正文与来源，见下），要求一个看不到机械结论的审阅者"不得说 supported"
  是一条它无从遵守的规则；把分歧记下来、由聚合裁决，才是它真正能承担的义务。
* **分歧的"逐处长什么样"不进 wire，由读回时现算。** 只留一串句 id，读者看到"两边不一致"也
  回不到**为什么**不一致那一句上，所以还需要一份"机械轴／原因码／表面串 × 审阅逐字理由"的
  并排表。它**不写进 `CitedReviewOutcome`**：那是两份**已经落盘**的读数（`issues` 与
  `sentence_checks.json`）的对齐结果，加字段就要升 `schema_version`，而升版会让**每一份**
  历史 `review_issues.json` 都解不出来——包括已冻结的演示 run，等于用一条纯派生的读数去作废
  全部历史归档。因此它由 `hard_error_divergence_rows()` 在**读回时**从两份既有产物现算，
  行序按 `(sentence_id, check_kind, review_issue_id)` 定死。这一栏**只是并排留档**，
  不裁决、不放行，也不新增"全判 supported 即失败"之类的粗暴规则。

## 审阅面长什么样

审阅对象是**一句话**，不是整节：`ReviewIssue` 走 `rvi-2`，`sentence_id` 与 `citation_id` 必填。
输入侧走 `rib-2`：

* `sentence_inventory` = 草稿里**带引用的每一句**的 id。审阅必须逐句表态——"某句没表态"与
  "某句没问题"在没有这条等式时不可区分，而读者恰恰要靠这个区分去看"哪几句被独立看过"。
  **没有引用的句子不在这个集合里**（`crv-3`）：审阅的最小单元是"一句话 + 它引的来源"
  （`rvi-2` 的 `citation_id` 非空），零引用句给不出合法的审阅单元；何况它必然已是
  `uncited_sentence` 硬错误，那是**写作侧**的缺陷，不是审阅能表态的对象。它们的 id 逐条记在
  `CitedReviewOutcome.excluded_uncited_sentence_ids` 里，读者面上标成"不在独立审阅对象内"——
  这个第三档是**刻意**的：把"审阅没看"与"审阅看了没问题"混成一档，正是这条等式要排除的事。
* `unit_inventory` / `excerpts` = 本次正文**真正引到**的每一条来源（材料原文或具名事实的命题
  文本 + 其定位）。只给被引到的：把整个 Pack 倒进去，审阅就会去评论正文没写过的东西。
* `excluded_context` 逐项声明 §7.5 的五类禁止来源（写作 prompt、自评、期望结论、人工 verdict、
  Writer hidden history）。审阅必须**看不到**"写的人想表达什么"，只看到"写出来的字与来源原文"。

## 独立性与"最多有界返修一次"

独立性靠**输入**保证（上面那一组排除声明），不靠"另开一个模型"这种口头承诺。返修的**次数**不归
本模块管：本模块只产出一次审阅的结论；"改一次再重新核对与审阅"是编排层的事，且本模块不提供任何
"再跑一次就自动放过"的入口。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from assurance import schema as AS
from sections import cited_writer as CW
from sections import narrative_schema as NS
from sections.sentence_check import SentenceCheckReport

__all__ = [
    "CITED_REVIEW_SCHEMA_VERSION",
    "CITED_REVIEW_POLICY_VERSION",
    "CITED_REVIEW_PROMPT_ASSET",
    "CITED_REVIEW_PROMPT_REVISION",
    "CITED_REVIEW_PROMPT_VERSION",
    "CITED_REVIEW_UNIT_KIND",
    "CITED_REVIEW_OUTCOMES",
    "CITED_REVIEW_PRODUCER_KINDS",
    "review_producer_kind_of",
    "CITED_REVIEW_FORBIDDEN_ENVELOPE_FIELDS",
    "CitedReviewError",
    "CitedReviewRequest",
    "build_cited_review_request",
    "build_cited_review_bundle",
    "build_cited_review_messages",
    "load_cited_review_prompt",
    "CitedReviewResult",
    "CitedReviewClient",
    "LlmCitedReviewClient",
    "CitedReviewOutcome",
    "parse_cited_review",
    "review_cited_prose",
]

#: 一次审阅的**结果** wire 版本（`CitedReviewOutcome`）。
#:
#: `crv-2`：结果对象增加 `review_producer_kind`（这份意见是**谁**给的）。离线回声替身产出的
#: 意见与真实独立 LLM 审阅在下游必须可区分 —— 否则「系统审阅已通过、等人确认」这句读者可见的
#: 话会被一次机械回声冒充。字段增删即升版。
#:
#: `crv-3`：结果对象增加 `excluded_uncited_sentence_ids`（**不在**审阅对象内的句子：零引用句
#: 给不出合法的 `rvi-2` 单元）与 `hard_error_override_sentence_ids`（审阅对机械硬错误句给出了
#: `supported` 的句子——`crv-3` 起这是**记下来的分歧**，不再当场抛错）。两条都是新增字段，
#: 旧记录走只读解码。
#:
#: **关于"分歧逐处长什么样"**：`hard_error_override_sentence_ids` 只答"哪几句出现分歧"，答不了
#: **分歧长什么样**。要做那份逐处并排表（机械侧 `check_kind`/`failure_reason`/`surfaces`/
#: `citation_keys` × 审阅侧 `review_reason` **逐字**/`review_severity`/`citation_id`/
#: `review_issue_id`），**没有**加字段、**没有**升版本，理由同上：它是 `issues` 与
#: `sentence_checks.json` 两份既有产物的对齐结果，写进 wire 就等于用派生读数作废全部历史归档。
#: 见 `hard_error_divergence_rows()`。**这仍是留档，不是判决**：审阅面对机械结论是盲的，
#: 说 `supported` 不越权；本轮不新增任何"全判 supported 即失败"的规则。
CITED_REVIEW_SCHEMA_VERSION = "crv-3"
#: 审阅政策版本（审核单元划分、逐句覆盖等式、输入隔离面、硬错误**分歧留档**、产出者身份，
#: 任一变化即升版）。
#:
#: `crvp-3`：覆盖等式的作用域从「草稿的全部句子」收成「草稿里**带引用**的句子」，且硬错误
#: 覆盖从抛错改为留档（理由见模块头）。
CITED_REVIEW_POLICY_VERSION = "crvp-3"
#: prompt 资产名与修订号；与既有 `*_vN@rev` 口径一致。
#:
#: `crr-2`：`supported` 行也必须给出**非空、指向所审材料**的 `reason`（真实 r28 里 23 行有
#: 22 行 `reason` 为空串——提示词的"没有问题就表态"一行没有列 `reason`，模型照做）；
#: 并说明 `uncited_sentence_ids` 不在审阅对象内。
#:
#: `crr-3`：两条判据来自真实 r1——真实 r1 的公司节 20 句**全部**被判 `supported`，而其中
#: 同时存在「把一份旧年报的客户名单写成当前客户」「把数字写到它并无所指的那一栏」这类**机械
#: 硬错误**：整节全绿恰恰暴露了审阅**没有在定所指**。本版新增两条可执行判据（都只是把审阅
#: 本来就该做的读法写清，不新增任何机械判定，也不给它放行权）：
#:
#: 1. **第 7b 条：判「当前态」先认来源文档**。`sources[]` 早已给出 `source_role` 与
#:    `document_id`（本次把 `document_id` 也补进材料来源行，见 `_source_row`），据此判「这句话
#:    只靠较旧来源、又没逐字带上那一期」→ `stale_material_as_current`。
#: 2. **第 7c 条：来源里没有的具体所指不得放过**。判 `supported` 要落到**具体所指**（客户 /
#:    供应商名称、数字、期间、方向），**不是**「整句话题与来源一致」；把泛称写成具体所指按越界
#:    处理。这一条正对真实 r1 里「20 句全 supported」的失效形状。
#:
#: 两条都**不改**审阅的产出形态（仍只出 `ReviewIssue[]`、不给放行、不改写），也**不**替机械层
#: 判错栏——错栏仍由 `scp-6` 的确定性轴独立判定；本版只是让独立审阅**别再放过**它读得出的
#: 那两类问题。
#:
#: `crr-4`：只对齐 7b 里一句**判据本身写歪了**的话。`crr-3` 的 7b 末条写着「判的是**来源文档
#: 是哪一期**」——那是拿 `document_id`（文档身份）**推**期间，与写作侧 `cp-10` 的第 3c 条
#: 正好相反：`document_id` 只说「这几条来自同一份文档」，**不**携带期间。本版把该条改成
#: 「期间只能从来源**原文逐字**读出，判的是**来源角色**，不是句子用没用现在时」。判据的**触发
#: 条件**（只引较旧来源 + 未逐字带期间 → `stale_material_as_current`）与 7c 一字未改，产出
#: 形态与放行权也不变。
#:
#: `crr-5` 按 cp-11 真实双节 run 的审阅漏报 / 误报加两条**读法**规则（不改产出形态、不加字段、
#: 不动放行权）：
#: (a) 新 7d——一句话引了多条来源时，**全部**所引来源都要读到，判「无出处」取的是**并集**；
#:     只有第一条对不上就出 `not_supported_by_source`，会把本来有出处的话误报成无出处
#:     （真实 r1 里已出现过这种误报）；`citation_id` 填实际据以判断的那一条；
#: (b) 新 7e——「未披露 / 未取得 / 未见」也是可证伪的**具体所指**：所引来源任何一条里逐字
#:     写到了它说「没有」的东西，就判 `contradiction`；确实都没有时，这类自述缺口句按
#:     `off_topic` / `missing_content` 提意见，因为它没有回答小节的 Contract 要求。
#:
#: `crr-6` 按 cp-12 真实双节 run 的审阅漏报做两件事——**一件是输入面**，**一件是读法**。
#: (a) **输入面**（请求 payload 增两个**只读**字段，不改产出形态、不加放行权、不改改写边界）：
#:     `sentences[].paragraph_aspect_ids`（这一段**草稿自己声明**服务的栏目）与
#:     `subsections[].aspect_requirements`（`declared_aspect_ids` 与 `requirement_text` 各行的
#:     **逐条对位**表）。`crr-5` 之前审阅只拿得到小节级的 `requirement_text`，看不到「这一句
#:     所在段落声称要答哪一栏」，于是**判不了「答非所问」**——真实 cp-12 里
#:     「境外收入…占本期营业收入30.60%」写进了销售模式那一段，审阅照旧判 `supported`。
#:     两个字段都取自草稿与清单，**不**是审阅者的判断，也**不**含任何生成过程痕迹。
#:     对位不成立（两串长度不等）时 `aspect_requirements` 留空，**不猜**。
#: (b) **读法**：把 `part_to_whole_generalization`（由两期/局部变动概括整个报告期或外推到别的
#:     期间，真实 cp-12 财务的「权益占比逐步上升」）与 `off_topic`（**答非所问**：这句话答的是
#:     本小节另一条 Contract 要求、而不是它所在段落 `paragraph_aspect_ids` 声明的那几条）
#:     写清；「发行人自评被写成独立结论」（真实 cp-12 的「拥有核心技术优势及前瞻性研发布局」）
#:     归到既有的 `not_supported_by_source` 之下（见 crr-7）。
#:     审阅仍**只给意见**：不重复机械数字门、不改写正文、不覆盖机械硬错。
#:
#: `crr-7` 是一次**纠错**，不是新读法。crr-6 曾在提示词表里自造了一个句义类
#: `issuer_self_assessment_as_conclusion`，但 `assurance.schema.REVIEW_SEMANTIC_CATEGORIES`
#: 是 §0.20 由用户裁决的**封闭词表**，加一格就是改冻结词汇，**越权**。真实 cp-14 双节 run 的
#: 公司节因此整次返回作废：
#: `ReviewIssue.semantic_category 必须属于 (…九值…)，得到 'issuer_self_assessment_as_conclusion'`
#: ——赔付的不只是那一条意见，而是**该节每一句**的独立审阅结论（`review_reply_unparsable`）。
#: 修法是**收回自造类名**、把该判断落到既有值上，并在提示词里把词表的封闭性写明白：
#: (a) 删去 `issuer_self_assessment_as_conclusion` 一行，该判断并入 `not_supported_by_source`
#:     的释义（`category` 用 `insufficient`：来源支持的是那句自述本身，不支持去掉归属之后的
#:     那个结论）；
#: (b) 表后加一段**封闭词表守则**：`semantic_category` 只能取表内九值，表外写法使**整次返回
#:     作废**（不只丢那一条），拿不准时选最接近的一类并在 `reason` 里写清情形，不得自造类名；
#: (c) 7f 第一条改指 `not_supported_by_source`。
#: 产出形态、字段、放行权与 crr-6 一字不差。**crr-7 未被任何真实 run 验证过**（修完之后不再发
#: 模型请求）；它修的是 crr-6 实跑暴露的**解析面**问题，不是「审阅质量已改善」。
#:
#: `crr-8` 修 7d 的**判据方向**（不改产出形态、不加字段、不动放行权、不碰机械轴）。
#: `crr-5` 立 7d 时写的是「只有当这句话的**每一个**具体所指在**所有**所引来源里都**找不到**时，
#: 才判 `not_supported_by_source`」——那是拿「并集里连一个所指都没有」当无出处的**门槛**，于是
#: 一句话只要**任意一个**所指（或任意一条来源）对得上，整句就落 `supported`。真实 cp-14 公司节
#: 的正面证据：审阅只报了 s0012 的自评与 s0017–s0019 的旧料当前化，**漏掉** s0006–s0008、s0011、
#: s0013 五句栏目错位——它读得出「话题相符」，读不出「整句里有一个所指站不住」。本版把判据改成
#: **逐个所指**：每一个所指都要在**全部所引来源的合并证据**里找得到，**只要有一个**找不到，这句
#: 就**没有得到完全支持**（`not_supported_by_source` / `category` 用 `insufficient`），并在
#: `reason` 里点明是哪一个所指。同时写明「所指都在场也不够」——语义、范围与期间仍按 7b / 7f 判，
#: `supported` 不是整句在别的轴上的免检。
#: `crr-9` 修的是**协议面**，不是判据面：`cp-20` 真实公司节（`m930_3_cited_real_company_cp20_r1`，
#: 审阅 `call_id b3328e284ee14912b5de83fa0bfe5d88`）HTTP ok、回复完整、20 条意见齐全，其中
#: `s0002` 那一条把 `citation_id` 写了**两遍**、`category` 一个都没有；`json.loads` 静默压掉
#: 重复键，`_reject_unknown` 因此看不到缺口，空串一路走到 `AS.ReviewIssue.create` 才抛
#: `AssuranceSchemaError`——那条异常**不带**是哪一条意见出错，整条审阅轴随之丢失。
#: 本版两处定点修改：**提示词**明写「每一行的九个键一个都不能少，`category` 缺键与空串同样作废」；
#: **解析器**（:func:`_issue_from_payload`）把这一条意见的任何不合约收敛成**带 `sentence_id` /
#: `citation_id` 的 `CitedReviewError`**，原因码仍是 `review_reply_unparsable`，**不猜、不降级、
#: 不返回半份意见**。判据、字段、放行权、机械轴一律不动。
#: `crr-10` 是**判据收紧 + 四族正反例**，产出形态、字段、放行权、机械轴一律不动。
#:
#: (a) **修 7b 一个会把旧期读成当前期的空子**：`crr-4` 起 7b 写的是"没有逐字带上那一份文档
#:     自己的期间（年份 / **「报告期末」等**）"——把相对期间与绝对年份并列，等于允许一句
#:     "截至报告期末"照抄自较旧文档就算带上了期间。真实 cp-21 公司节的形态正是这样。本版把
#:     条件收成**绝对年份**（形如「2023 年」），并明写：较旧文档里的"报告期末"指的是**那一份
#:     文档自己的**期间，脱离文档年份就是一句当前期断言 ⇒ `stale_material_as_current`。
#:     这与机械侧 `srsc-4` 的 `has_absolute_period_qualification`（只认 `\d{4}\s*年`）**同一口径**：
#:     两边对"带没带期间"必须给出同一个答案，否则一处说旧料当前化、一处说带够了期间。
#: (b) **7g 四族正反例**（`template_or_checklist_as_business_fact` /
#:     `document_period_as_report_period` / `selective_omission` / `unsupported_evaluation`），
#:     每族一对形状 + 反例类名，并明写两条边界："来源逐字有"不解除任何一族；机械核对是另一条
#:     独立的轴，你既不替它判、也不因为它会拦就放过。四族里**只**出现冻结词表内的类名。
#:
#: `crr-11` 修的是**两级字段的混称措辞**，不是判据、不是词表、不是解析器。
#: 动因是一次真实双节 run（`m930_3_cited_upload_20261005T161431Z`）：公司节审阅回复第 16 条把
#: `off_topic` 填进了 `category`，`AS.ReviewIssue.create` 当场以「不在 `REVIEW_CATEGORIES` 里」
#: 拒收，包成 `review_reply_unparsable`，**该节每一句**的独立审阅结论一并丢失、整轮
#: `run_outcome=failed`。**解析器拒收是正确行为**：`category`（四粗类）与 `semantic_category`
#: （九句义类）是 §7.3 的两条正交轴，`REVIEW_CATEGORY_BY_SEMANTIC["off_topic"] == "missing_content"`
#: 早就把合法配对定成 `category="missing_content"` + `semantic_category="off_topic"`。本版因此
#: **只改提示词**，不放宽枚举、不静默映射越界值、不把失败意见降级成 `supported`。
#:
#: 三处定点修改（都在提示词文件里）：
#: (a) **输出字段块后加一段**「两个类名字段不要混填」：`category` 只写那四个粗类值，
#:     `off_topic` 一类**句义类名绝不能写进 `category`**；反过来 `missing_content` 是粗类取值，
#:     不要写进 `semantic_category`。原文没有任何一句把这条禁令放在输出字段旁边。
#: (b) **7e 末条**原文是「你可以按 `off_topic` 或 `missing_content` 提意见」——把**两级**名并列
#:     成二选一，却不点字段名，这正是 `category='off_topic'` 的措辞来源。改成显式三元组：
#:     `category` 用 `missing_content`、`semantic_category` 用 `off_topic`，并说明
#:     `suggested_target` 用 `{"target_kind": "contract_aspect", "target_ref": …}` 指回
#:     这句话**本该回答**、却没有回答的那一条 Contract 要求。
#: (c) **7f 第三条**（栏目文字答非所问）同样把 `semantic_category` 用 `off_topic` 与
#:     `suggested_target` 的指向写明。
#:
#: 其余八类映射、逐句一对一覆盖等式、`reason` / `evidence_refs` 出处要求、九键约束与
#: `suggested_target` 的既有口径**一字未动**；未升 `ReviewIssue` schema、未动
#: `assurance/schema.py` 的粗类/句义类词表与映射、未动解析器与放行权。
#:
#: **未经真实运行验证，且不能宣称这是那次失败的唯一原因**——同一份 `crr-10` 也有成功的真实 run。
#: 本版只消除一处**读得出**的两级字段混称措辞；真实模型下次会不会照做，离线证不了
#: （守卫见 `evals/test_m930_3_cited_review.py` §22：它钉的是「两级名不得并列成二选一」
#: 与「粗类取值清单里不得出现句义类名」这两条**关系**，不钉任何一句措辞）。
#:
#: `crr-12` 修的是 **`contract_aspect` 的 `target_ref` 到底填什么**，不是判据、不是词表、
#: 不是解析器、不是 wire。
#:
#: 动因是**读得出来的**一处不一致（零模型只读核对得出，不需要真实 run 才能看见）：
#:
#: * `assurance.schema.SuggestedTarget` 的类注释写着「`target_ref` 只能是**身份引用或 aspect 名**」，
#:   它的封闭词表把 `contract_aspect` 定义为「指向**应该补哪个 Contract aspect**」；
#: * 章节面的请求面**已经**把身份名摆在模型眼前：`subsections[].aspect_requirements[]` 是
#:   `{"aspect_id": …, "requirement_text": …}` 的逐条对位表（`_aspect_requirement_pairs`），
#:   句子行另带 `paragraph_aspect_ids`；
#: * 但 `crr-11` 的三处 `contract_aspect` 占位符写的都是「**那一条要求**」
#:   （`<缺哪个要求>` / `<它本该回答的那一条要求>` / `<…却没有回答的那一条要求>`）——
#:   要求文本是一句整段中文，**不是身份名**。照着这三个占位符写，最自然的落笔就是粘一段要求原文。
#: * 读者面已经按**身份名**在用这个字段：`scripts/cited_demo_app.py` 的「建议补到」列直接印
#:   `target_ref`，其旁边的读法说明逐字写着三条 `high` 「指向**同一个**栏目
#:   `fin_solvency.net_asset_level`」——那是 aspect id，不是要求文本。
#:
#: 因此本版把三处占位符改写成**栏目身份名**，并给出一条**确定**的退路：请求面里两处都取不到
#: `aspect_id` 时改用 `{"target_kind": "citation", "target_ref": "<该句 citations 里的键>"}`——
#: 那是 `SUGGESTED_TARGET_KINDS` 里已有的合法目标、指向一个请求面里真的出现的键，
#: **不**需要新词表、**不**需要放宽解析器、**不**需要模型自己发明名字。
#:
#: **本版不动**：`ReviewIssue` / `SuggestedTarget` wire、`assurance/schema.py` 的词表与真值表、
#: 解析器 `_issue_from_payload` 与放行权、`build_cited_review_request` 的请求面字段、
#: `crr-11` 的两级字段分工与 `crr-6` 起的所有判据。**没有**放宽任何一处：本版只把「填什么」
#: 写准，并把取不到时的合法退路收窄到词表内已有的 `citation` 目标。
#:
#: **如实记下解析器管不到的地方**：`SuggestedTarget.from_dict` 只校验 `target_kind` 在词表内、
#: `target_ref` **非空**（该类注释明说「是否存在由硬门与 Controller 用真实产物核对」）。
#: 因此**填一段要求原文、或一个自造的字符串，解析器照收**——本版能钉住的只是**提示词面**的
#: 取向与退路（守卫见 `evals/test_m930_3_cited_review.py` §23），**不能**宣称「`target_ref`
#: 一定是真 aspect_id」已被系统强制。要真强制，得另立一个下游核对（本版不含）。
#:
#: **口径限制：cp-14 真实 run 验证的是 `crr-6`（且当场因表外类名整份作废）；`crr-7`、`crr-8`、
#: `crr-9`、`crr-10`、`crr-11` 与 `crr-12` 均未经任何真实运行验证。** 提示词改动**离线证不了**——离线替身不进
#: `load_cited_review_prompt()` 的判据面，重放同一条回复只会得到同一份意见。本批能钉住的只有
#: 提示词与冻结词表、与机械侧期间口径的**对账关系**（见 `evals/test_m930_3_cited_review.py` §17）、
#: 两级字段的分工（§22）与 `target_ref` 的取向（§23）。
CITED_REVIEW_PROMPT_ASSET = "cited_prose_review_v1"
CITED_REVIEW_PROMPT_REVISION = "crr-12"
CITED_REVIEW_PROMPT_VERSION = f"{CITED_REVIEW_PROMPT_ASSET}@{CITED_REVIEW_PROMPT_REVISION}"

#: 审阅单元的 `unit_kind`。复用 §7.3 冻结的引用轴，**不**新增 `sentence`：句子身份由
#: `rib-2` 的 `sentence_inventory` 承载，来源单元仍是 `citation`，两根轴各司其职。
CITED_REVIEW_UNIT_KIND = "citation"

#: 一次审阅的完成状态（与 `assurance.schema.REVIEWER_OUTCOMES` 同一词汇，不另立第三档）。
CITED_REVIEW_OUTCOMES = AS.REVIEWER_OUTCOMES

#: 意见的**产出者身份**（封闭两档）。
#:
#: * `independent_llm_review`：由 `LlmCitedReviewClient` 真正发起的一次独立只读审阅。
#: * `offline_diagnostic_echo`：离线替身（回放夹具 / 机械回声）。它证明的是**接口与保留关系**
#:   ——逐句覆盖等式、引用挂载、硬错误不可覆盖都真的会被检查 —— 而**不是**「有人独立读过这段
#:   正文」。读者面上因此不得出现「系统审阅已通过、等待人工确认」这类话：那会把一次机械回声
#:   读成一次独立判断。
CITED_REVIEW_PRODUCER_KINDS = ("independent_llm_review", "offline_diagnostic_echo")


def review_producer_kind_of(client: Any) -> str:
    """**由 client 本身**推出产出者身份（唯一实现，调用方无从谎报）。

    规则只有一条：只有 `LlmCitedReviewClient` 产出的意见才算独立 LLM 审阅。让调用方用参数
    「声明」自己是哪种，等于给了把机械回声标成独立审阅的入口；身份必须从**能发起真实调用的
    那个对象**推出来。
    """
    return ("independent_llm_review" if isinstance(client, LlmCitedReviewClient)
            else "offline_diagnostic_echo")

#: 响应信封层的**指名**禁列：这些词一旦出现，说明审阅在给自己发通行证。
CITED_REVIEW_FORBIDDEN_ENVELOPE_FIELDS = (
    "publishable", "passed", "pass", "released", "release", "approved", "accepted",
    "verdict", "decision", "ready", "final_text", "rewritten_text", "prose",
)


class CitedReviewError(Exception):
    """审阅面在**装配 / 解析 / 校验**任一步的 fail-closed。"""

    def __init__(self, message: str, *, reason: str = "", sentence_id: str = "",
                 citation_id: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.sentence_id = sentence_id
        self.citation_id = citation_id


# ---------------------------------------------------------------------------
# 来源读视图：被引到的每一条，原文 + 定位
# ---------------------------------------------------------------------------

def _fact_location(fact: CW.CitedFactEntry) -> str:
    """事实行的可回查位置：`loc-1` locator 优先，否则退到**权威锚**。

    与 `sentence_check.check_sentence` 的 `citation_locator` 轴是**同一判据**，且**按事实自己的
    authority kind 分别判**，不统一成一种：

    * `topic_pack` / `external_snapshot` / `evidence_note`：`loc-1` locator，或
      `material_id` / `payload_ref` 权威锚，三者之一；
    * `financial_pack`：结构化库**没有**字符区间，一条财务事实的定位是它自己的坐标
      `(容器身份, fact_id)` 加上该事实引用锚点的类型化身份（`financial_snapshot:<snapshot_id>`，
      由 `scan_financial` 从它自己的 `CitationRef` 派生）。三项齐备即「按该权威自己的坐标回查
      得到这一条事实」。该支**只**认这三项：真实财务包的事实 `locator_ref` / `material_id` /
      `payload_ref` 本来就都是空——若在这里按那三者判，全部合格财务事实都永远无法被审阅。

    两处若各写一份，「这条来源能不能按位置读回」就会在机械层与审阅层给出不同答案；**更严**
    的那一版会让一侧合法的事实被另一侧永久拒绝。
    这里只做**读取**，不替它编造 `loc-1`、也不给财务事实编一个 PDF 字符区间：拿不到该 kind
    自己的定位时返回空串，由调用方 fail-closed。
    """
    if fact.authority_kind == "financial_pack":
        container = str(fact.container_identity or "").strip()
        fact_id = str(fact.fact_id or "").strip()
        source_identity = str(fact.source_identity or "").strip()
        if container and fact_id and source_identity:
            return NS.canonical_json({"anchor_kind": "financial_pack_coordinate",
                                      "container_identity": container,
                                      "fact_id": fact_id,
                                      "source_identity": source_identity})
        return ""
    located = _locator_text(fact.locator_ref)
    if located:
        return located
    if fact.material_id:
        return NS.canonical_json({"anchor_kind": "material_id",
                                  "anchor": str(fact.material_id)})
    if fact.payload_ref:
        return NS.canonical_json({"anchor_kind": "payload_ref",
                                  "anchor": dict(fact.payload_ref)})
    return ""


def _source_row(*, manifest: CW.CitedWriterInputManifest, key: str) -> dict:
    """一条被引来源的**审阅侧**读视图：原文（或命题文本）+ 精确位置 + 身份。

    刻意与 `cited_writer.build_cited_prose_request` 的材料行取**同一**字段来源：审阅看到的
    来源字符串必须与写作看到的**同一串字节**，否则"复核者拿到的和作者拿到的不是一份东西"，
    独立性就成了一句空话。
    """
    material = manifest.material_for_key(key)
    if material is not None:
        return {
            "citation_id": key, "axis": "material", "material_id": material.material_id,
            "material_type": material.material_type, "source_role": material.source_role,
            "source_identity": material.source_identity,
            #: 来源文档身份（`crr-3`）：判「这句话是不是拿较旧文档冒充当前态」时，`source_role`
            #: 说是不是旧来源，`document_id` 说它来自哪一份。与写作面材料行取**同一**字段来源
            #: （`cited_writer` 的 `document_id`），审阅看到的与作者看到的仍是同一串身份。
            "document_id": material.document_id,
            "locator": _locator_text(material.locator_ref),
            "payload_hash": material.payload_hash, "text": material.reading_view,
        }
    fact = manifest.fact_for_key(key)
    if fact is not None:
        return {
            "citation_id": key, "axis": "fact", "authority_kind": fact.authority_kind,
            "container_identity": fact.container_identity, fact.fact_field: fact.fact_id,
            "locator": _fact_location(fact), "period": fact.period,
            "scope": fact.scope, "fact_type": fact.fact_type, "text": fact.text,
        }
    raise CitedReviewError(
        f"引用键 {key!r} 不在本次输入清单里（清单材料键 {list(manifest.material_keys())}，"
        f"事实键 {list(manifest.fact_keys())}）",
        reason="citation_not_in_input", citation_id=key)


def _locator_text(locator: Any) -> str:
    """定位的**可回查**文本形式。

    材料行的 `locator_ref` 是 `loc-1` 联合体（mapping），事实行的可能是 mapping 也可能是
    已经是字符串。零定位不在这里造一个假值出来：没有定位就是空串，下游按空串处理（
    `ReviewExcerpt` 会当场拒绝空定位，而不是替它编一个"见某处"）。
    """
    if locator is None:
        return ""
    if isinstance(locator, Mapping):
        return NS.canonical_json(dict(locator))
    return str(locator)


# ---------------------------------------------------------------------------
# 请求面
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitedReviewRequest:
    """审阅请求面：**逐句正文 + 逐句引用 + 被引来源全文**，外加逐项排除声明。

    `payload` 就是投给模型的那一个 JSON 对象，`fingerprint` 是它的稳定指纹；bundle 的
    `allowed_content_fingerprint` 取同一个值，于是"审阅看到的正文"与"bundle 声明的可读内容"
    是同一件事，而不是两处各说各的。
    """

    payload: dict
    fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.payload, dict):
            raise CitedReviewError("CitedReviewRequest.payload 必须是对象")
        want = hashlib.sha256(
            NS.canonical_json(self.payload).encode("utf-8")).hexdigest()
        if self.fingerprint != want:
            raise CitedReviewError(
                f"CitedReviewRequest.fingerprint 与 payload 不符：声明 {self.fingerprint!r}，"
                f"应为 {want!r}", reason="request_fingerprint_mismatch")


def _aspect_requirement_pairs(spec: CW.CitedSubsectionSpec) -> tuple[dict, ...]:
    """小节的「栏目 → Contract 逐字要求」对位表（`crr-6`）。

    一个小节的 `requirement_text` 是一条 Contract 要求一行拼起来的，而 `declared_aspect_ids`
    按**同一次序**声明了它覆盖的那几栏（`cwm-6` 的集合口径）。审阅要判「这句话答的是不是它所在
    段落**声称**的那一栏」，就必须能看到段落声明的那几栏**各自**是要求什么——所以请求面把这份
    对位摆出来，由调用方（这里）从清单投影，**不解释** Contract。

    两串长度不相等时**不猜**：返回空，请求面只保留小节级的 `requirement_text`，段落声明的那几栏
    退回成没有对应的要求文本（模型据此只能判小节级跑题，不会替系统补一个错的对位）。
    """
    lines = [ln.strip() for ln in str(spec.requirement_text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    aspects = tuple(str(a) for a in (spec.declared_aspect_ids or ()))
    if not lines or len(lines) != len(aspects):
        return ()
    return tuple({"aspect_id": a, "requirement_text": ln} for a, ln in zip(aspects, lines))


def build_cited_review_request(*, draft: CW.CitedProseDraft,
                               manifest: CW.CitedWriterInputManifest) -> CitedReviewRequest:
    """草稿 + 输入清单 → 审阅请求面（**只含正文与来源，不含任何生成过程痕迹**）。

    逐句给出 `sentence_id` / `subsection_id` / `paragraph_id` / **该段声明的栏目
    `paragraph_aspect_ids`** / `text` / `citations`，外加一个小节清单（标题 + Contract 逐字
    要求文本 + **栏目→要求对位表 `aspect_requirements`**，用于判"跑题"与"答非所问"）。
    **不加**材料里没写的东西，也不加"作者想表达什么"：段落声明的栏目是**草稿自己写的**、
    `aspect_requirements` 是**清单里 Contract 逐字要求**的投影，两者都不是审阅者的判断。

    `crv-3` 起 `sentences` **只装带引用的句子**，零引用句的 id 另列在 `uncited_sentence_ids`：
    审阅对象的最小单元是"一句话 + 它引的来源"（`rvi-2` 的 `citation_id` 必填），零引用句给不出
    这个单元；把它塞进 `sentences` 只会让模型为它编一个不存在的 `citation_id`（真实 r28 的
    第二道解析期失败正是这个形状）。给出 id 而不给正文，是让模型明确知道自己的**边界在哪**，
    不至于把"你没给我看的句子"当成"这一节只有这几句"。
    """
    if draft.input_manifest_id != manifest.manifest_id:
        raise CitedReviewError(
            f"草稿绑的输入清单 {draft.input_manifest_id!r} 不是本次清单 "
            f"{manifest.manifest_id!r}", reason="draft_manifest_mismatch")

    sentences: list[dict] = []
    cited: list[str] = []
    uncited: list[str] = []
    for subsection in draft.subsections:
        for paragraph in subsection.paragraphs:
            for sentence in paragraph.sentences:
                if not sentence.citations:
                    uncited.append(sentence.sentence_id)
                    continue
                sentences.append({
                    "sentence_id": sentence.sentence_id,
                    "subsection_id": subsection.subsection_id,
                    "paragraph_id": paragraph.paragraph_id,
                    "paragraph_aspect_ids": list(paragraph.aspect_ids),
                    "text": sentence.text,
                    "citations": list(sentence.citations),
                })
                for key in sentence.citations:
                    if key not in cited:
                        cited.append(key)

    payload = {
        "policy_version": CITED_REVIEW_POLICY_VERSION,
        "prompt_version": CITED_REVIEW_PROMPT_VERSION,
        "task_id": draft.task_id,
        "section_id": draft.section_id,
        "section_title": manifest.section_title,
        "subsections": [
            {"subsection_id": s.subsection_id, "title": s.title,
             "requirement_text": s.requirement_text,
             "aspect_requirements": list(_aspect_requirement_pairs(s))}
            for s in manifest.subsections],
        "sentences": sentences,
        "uncited_sentence_ids": uncited,
        "sources": [_source_row(manifest=manifest, key=key) for key in cited],
        "excluded_context": list(AS.REQUIRED_EXCLUDED_CONTEXT),
    }
    return CitedReviewRequest(
        payload=payload,
        fingerprint=hashlib.sha256(
            NS.canonical_json(payload).encode("utf-8")).hexdigest())


def build_cited_review_bundle(*, draft: CW.CitedProseDraft,
                              manifest: CW.CitedWriterInputManifest,
                              request: CitedReviewRequest,
                              report_version: str, report_id: str,
                              model_policy_id: str,
                              prompt_version: str = CITED_REVIEW_PROMPT_VERSION
                              ) -> AS.ReviewInputBundle:
    """请求面 → **隔离的只读审阅输入** `rib-2`。

    `unit_inventory` 只含本次真正引到的来源（每条一个 `citation:<key>` 单元），
    `excerpts` 逐条给出该来源的全文与定位，`sentence_inventory` 是必须逐句表态的句子集
    ——`crv-3` 起它是**请求面实际给出去的**那批句子（带引用者），而不是草稿的全部句子：
    "必须表态的集合"与"能表态的集合"必须是同一个，否则覆盖等式会要求审阅对自己看不见的句子
    表态。零引用句照样在 `CitedReviewOutcome.excluded_uncited_sentence_ids` 里留痕。
    `allowed_content_fingerprint` 取请求面指纹：bundle 声明的"允许给审阅看的内容"与
    "实际给出去的 payload"因此可对账。
    """
    if not str(report_version or "").strip():
        raise CitedReviewError(
            "审阅必须绑定一个**真实**的 report_version：没有版本锚的意见无法被回查",
            reason="report_version_missing")
    if not str(report_id or "").strip():
        raise CitedReviewError("report_id 必须非空", reason="report_id_missing")

    units: list[AS.ReviewUnitRef] = []
    excerpts: list[AS.ReviewExcerpt] = []
    for row in request.payload["sources"]:
        key = str(row["citation_id"])
        ref = AS.ReviewUnitRef.create(unit_kind=CITED_REVIEW_UNIT_KIND, unit_id=key)
        units.append(ref)
        locator = str(row.get("locator") or "")
        text = str(row.get("text") or "")
        if not locator:
            raise CitedReviewError(
                f"被引来源 {key!r} 既没有定位、也没有可回查的权威锚"
                f"（材料：locator；事实：locator 或 material_id / payload_ref）："
                f"审阅无法按位置读回原文，fail-closed（不得替它编一个位置）",
                reason="citation_locator_unretrievable", citation_id=key)
        if not text:
            raise CitedReviewError(
                f"被引来源 {key!r} 的原文为空：没有原文的审阅只能是猜想",
                reason="citation_source_empty", citation_id=key)
        excerpts.append(AS.ReviewExcerpt.create(unit_ref=ref, text=text,
                                                source_locator=locator, citation_id=key))

    return AS.ReviewInputBundle.create(
        report_version=report_version, report_id=report_id,
        unit_inventory=tuple(units), excerpts=tuple(excerpts),
        citation_ids=tuple(str(r["citation_id"]) for r in request.payload["sources"]),
        allowed_content_fingerprint=request.fingerprint,
        prompt_version=prompt_version, model_policy_id=model_policy_id,
        schema_version=AS.REVIEW_INPUT_BUNDLE_SCHEMA_VERSION,
        sentence_inventory=tuple(str(row["sentence_id"])
                                 for row in request.payload["sentences"]))


def build_cited_review_messages(*, request: CitedReviewRequest,
                                system: str) -> tuple[list[dict], str]:
    """请求面：**一段** user 消息（完整 JSON）+ system prompt。"""
    import json
    text = json.dumps(request.payload, ensure_ascii=False, sort_keys=True, indent=1)
    return ([{"role": "user", "content": text}], system)


def load_cited_review_prompt() -> str:
    """加载本链的审阅 prompt（`llm/prompts/cited_prose_review_v1.txt`）。

    **加载即对账**，与写作侧 `cited_writer.load_cited_writer_prompt` 同一纪律：资产头声明的
    `(asset, revision)` 必须与 :data:`CITED_REVIEW_PROMPT_REVISION` 逐字相同。理由是同一个——
    `prompt_version` 会随每次审阅调用落进调用账本与审阅产出身份体，修订号改了而头部没改
    （或反过来）时，账本上写的是一个**没有任何资产与之对应**的版本号：事后回查时没有任何字节
    能证明当时发给审阅者的提示词长什么样。取不到声明句式同样拒绝——「头部格式漂移」与
    「版本对得上」在这条线上不可区分，按 fail-closed 处理。
    """
    from llm import client as llm
    text = llm.load_prompt(CITED_REVIEW_PROMPT_ASSET)
    declared = CW.declared_prompt_identity(text)
    if declared is None:
        raise CitedReviewError(
            f"prompt 资产 {CITED_REVIEW_PROMPT_ASSET!r} 第 1 行没有声明的 "
            f"`（<asset>，revision <rev>）`，无法与 CITED_REVIEW_PROMPT_REVISION 对账",
            reason="prompt_asset_identity_missing")
    asset, revision = declared
    if (asset, revision) != (CITED_REVIEW_PROMPT_ASSET, CITED_REVIEW_PROMPT_REVISION):
        raise CitedReviewError(
            f"prompt 资产自称 {asset}@{revision}，本链声明的是 "
            f"{CITED_REVIEW_PROMPT_VERSION}：账本上的 prompt_version 将不对应任何字节",
            reason="prompt_asset_identity_mismatch")
    return text


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitedReviewResult:
    """一次审阅调用的**结构化**结果（文本 + 完整调用元数据）。与 `CitedProseResult` 同纪律。"""

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
            raise CitedReviewError(
                f"CitedReviewResult.status={self.status!r} 只能是 'ok' / 'error'")
        for name in ("call_id", "model", "prompt_version"):
            if not str(getattr(self, name) or ""):
                raise CitedReviewError(
                    f"CitedReviewResult.{name} 不得为空（调用元数据必须完整）")
        if not isinstance(self.text, str):
            raise CitedReviewError("CitedReviewResult.text 必须是字符串")
        if self.status == "error" and not self.error:
            raise CitedReviewError("CitedReviewResult.status='error' 必须带 error 说明")

    @property
    def response_hash(self) -> str:
        return NS.body_fingerprint_of(self.text)

    def to_dict(self) -> dict:
        return {"call_id": self.call_id, "model": self.model,
                "prompt_version": self.prompt_version, "status": self.status,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "latency_ms": self.latency_ms, "finish_reason": self.finish_reason,
                "error": self.error, "response_hash": self.response_hash}


class CitedReviewClient(Protocol):
    """审阅链能拿到的全部外部能力。**没有**检索入口、没有工具入口。"""

    def review(self, *, messages: Sequence[Mapping[str, str]], system: str,
               prompt_version: str, model_policy: str) -> CitedReviewResult: ...


class LlmCitedReviewClient:
    """把 `llm.client` 适配成 `CitedReviewClient`（不带工具、不重试、不缓存）。

    截断（`reject_truncated=True`）时**先记一条失败流水再抛**：记录形状见
    `cited_writer.truncated_call_record`（没有 `text` / `response_hash`——半截内容不是意见）。
    """

    def __init__(self, *, model: str | None = None, max_tokens: int = 8192,
                 thinking: dict | None = None, reject_truncated: bool = True) -> None:
        self.model = model
        self.max_tokens = int(max_tokens)
        self.thinking = thinking
        self.reject_truncated = bool(reject_truncated)
        self.calls: list[dict] = []

    def review(self, *, messages: Sequence[Mapping[str, str]], system: str,
               prompt_version: str, model_policy: str) -> CitedReviewResult:
        from llm import client as llm  # 延迟导入：离线测试不必加载 provider 依赖

        try:
            resp = llm.chat_with_usage(list(messages), system=system, model=self.model,
                                       max_tokens=self.max_tokens,
                                       prompt_version=prompt_version, thinking=self.thinking,
                                       reject_truncated=self.reject_truncated)
        except llm.LLMTruncatedResponse as exc:
            # 与写作侧同一条纪律：**抛出去**（停住整轮）与**记下来**（`calls` 留一条失败流水）
            # 都要做。只抛不记，等于把一次真实发生、已经占掉额度的审阅调用从流水里抹掉。
            self.calls.append(CW.truncated_call_record(
                exc, model=self.model, prompt_version=prompt_version,
                model_policy=model_policy))
            raise
        except Exception as exc:  # noqa: BLE001 - 任何 provider 失败都如实记为 error 结果
            record = {"call_id": "", "model": self.model or "", "status": "error",
                      "prompt_version": prompt_version,
                      "error": f"{type(exc).__name__}: {exc}"}
            self.calls.append(record)
            return CitedReviewResult(text="", call_id=self._fallback_call_id(),
                                     model=self.model or "", prompt_version=prompt_version,
                                     status="error", error=record["error"])
        result = CitedReviewResult(
            text=str(getattr(resp, "text", "") or ""),
            call_id=str(getattr(resp, "call_id", "") or "") or self._fallback_call_id(),
            model=str(getattr(resp, "model", "") or "") or (self.model or ""),
            prompt_version=prompt_version, status="ok",
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


# ---------------------------------------------------------------------------
# 解析：模型返回 → ReviewIssue[]（唯一一处把文本变成结构化意见）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitedReviewOutcome:
    """一次独立审阅的**完整**结果：逐句意见 + 调用元数据 + 覆盖证据。

    它**没有**任何"通过/放行/可发布"字段，这是刻意的：§0.20 里"审阅完成且 blocking 已解决"
    只是正式发布的**条件之一**，发布本身还要走独立的系统放行与人工接受。把放行塞进这个对象，
    等于让"没发现问题"自动变成"可以发布"。
    """

    schema_version: str
    policy_version: str
    outcome: str
    review_producer_kind: str
    report_version: str
    report_id: str
    bundle_id: str
    draft_id: str
    input_manifest_id: str
    sentence_ids: tuple[str, ...]
    """**审阅对象**的句子 id（`crv-3`：草稿里带引用的句子）。不是草稿的全部句子——
    两者之差逐条记在 `excluded_uncited_sentence_ids`，因此全句集可复原（`reviewed + excluded`）。"""
    issues: tuple[AS.ReviewIssue, ...]
    hard_error_sentence_ids: tuple[str, ...]
    """机械核对判为 `hard_error` 的句子（**写作侧**的读数，与本对象里的意见是两条轴）。"""
    excluded_uncited_sentence_ids: tuple[str, ...]
    """草稿里**没有引用**、因而不在审阅对象内的句子（必然也是 `uncited_sentence` 硬错误）。"""
    hard_error_override_sentence_ids: tuple[str, ...]
    """审阅对机械硬错误句给出了 `category="supported"` 的句子。

    `crv-3` 起这是**记录下来的分歧**，不是解析错误：审阅面是故意盲的，看不见机械结论，
    因此"说 supported"是它的独立意见，不是越权。聚合时机械硬错误优先（见 `cited_report`），
    所以这份记录的作用恰恰是让读者看见"审阅在这里和机械层不一致"。

    只知道"哪几句"，不知道"分歧长什么样"——后者由 `hard_error_divergence_rows()` 在读回时
    现算（见该函数与 `CITED_REVIEW_SCHEMA_VERSION` 上方注释）。**不写进本对象**是有意的：
    它为这条 wire 升版会让全部历史 `review_issues.json` 解不出来。
    """
    call: dict

    def __post_init__(self) -> None:
        if self.schema_version != CITED_REVIEW_SCHEMA_VERSION:
            raise CitedReviewError(
                f"CitedReviewOutcome.schema_version 必须为 {CITED_REVIEW_SCHEMA_VERSION!r}，"
                f"得到 {self.schema_version!r}")
        if self.review_producer_kind not in CITED_REVIEW_PRODUCER_KINDS:
            raise CitedReviewError(
                f"CitedReviewOutcome.review_producer_kind 必须属于 "
                f"{CITED_REVIEW_PRODUCER_KINDS}，得到 {self.review_producer_kind!r}"
                "（这份意见是谁给的必须写清楚，否则下游分不清独立审阅与离线回声）")
        if self.outcome not in CITED_REVIEW_OUTCOMES:
            raise CitedReviewError(
                f"CitedReviewOutcome.outcome 必须属于 {CITED_REVIEW_OUTCOMES}，"
                f"得到 {self.outcome!r}")
        for name in ("report_version", "report_id", "bundle_id", "draft_id",
                     "input_manifest_id"):
            if not str(getattr(self, name) or "").strip():
                raise CitedReviewError(f"CitedReviewOutcome.{name} 必须非空")
        issues = tuple(self.issues or ())
        for issue in issues:
            if not isinstance(issue, AS.ReviewIssue):
                raise CitedReviewError(
                    f"CitedReviewOutcome.issues 只能是 ReviewIssue，得到 {type(issue).__name__}")
            if issue.report_version != self.report_version:
                raise CitedReviewError(
                    f"意见 {issue.issue_id!r} 绑的是 report_version="
                    f"{issue.report_version!r}，不是本次 {self.report_version!r}"
                    f"（意见必须绑在真实版本上）", reason="issue_report_version_mismatch")
        object.__setattr__(self, "issues", issues)
        ids = [i.issue_id for i in issues]
        if len(set(ids)) != len(ids):
            raise CitedReviewError("CitedReviewOutcome.issues 含重复意见", reason="issue_duplicate")

        # 审阅对象 / 圈外句 / 硬错误覆盖三者的自洽。任一条不成立，这份结果就无法回答
        # 「哪几句真的被看过」——而那正是审阅唯一能提供的东西。
        reviewed = tuple(str(x) for x in self.sentence_ids)
        excluded = tuple(str(x) for x in self.excluded_uncited_sentence_ids)
        overrides = tuple(str(x) for x in self.hard_error_override_sentence_ids)
        object.__setattr__(self, "sentence_ids", reviewed)
        object.__setattr__(self, "excluded_uncited_sentence_ids", excluded)
        object.__setattr__(self, "hard_error_override_sentence_ids", overrides)
        for name, values in (("sentence_ids", reviewed),
                             ("excluded_uncited_sentence_ids", excluded)):
            if len(set(values)) != len(values):
                raise CitedReviewError(
                    f"CitedReviewOutcome.{name} 不得有重复", reason="sentence_id_duplicate")
        overlap = sorted(set(reviewed) & set(excluded))
        if overlap:
            raise CitedReviewError(
                f"CitedReviewOutcome：{overlap} 同时出现在审阅对象与圈外句里——"
                "一句要么可审、要么不可审，不能两者都是",
                reason="review_scope_overlap")
        for issue in issues:
            if issue.sentence_id not in set(reviewed):
                raise CitedReviewError(
                    f"意见 {issue.issue_id!r} 指向的句子 {issue.sentence_id!r} "
                    f"不在本次审阅对象内（对象 {list(reviewed)}）",
                    reason="issue_sentence_outside_review_scope",
                    sentence_id=issue.sentence_id)
        stray = sorted(set(overrides) - set(self.hard_error_sentence_ids))
        if stray:
            raise CitedReviewError(
                f"CitedReviewOutcome.hard_error_override_sentence_ids 含 {stray}，"
                "但它们不在机械硬错误集里：覆盖记录只能挂在真的被判过硬错误的句子上",
                reason="hard_error_override_not_a_hard_error")

    # -- 派生视图（只读，不产生新结论） -----------------------------------

    @property
    def reviewed_sentence_ids(self) -> tuple[str, ...]:
        """本次**真的**被独立看过、并被要求表态的句子集（= `sentence_ids`）。"""
        return self.sentence_ids

    @property
    def blocking_issue_ids(self) -> tuple[str, ...]:
        return tuple(i.issue_id for i in self.issues if i.blocking)

    @property
    def blocking_sentence_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(i.sentence_id for i in self.issues if i.blocking))

    @property
    def sentence_verdicts(self) -> tuple[tuple[str, str], ...]:
        """`(sentence_id, 最重类别)`——**没有**表态的句子给空串，不给默认值。

        "没表态"与"没问题"必须可区分：默认成 `supported` 会让一次漏看伪装成一次通过。
        同一句有多条意见时按 `severity` 取最重者；同 severity 再按句义类名取字典序，
        因此结果与响应里的顺序无关。
        """
        rank = {name: index for index, name in enumerate(AS.REVIEW_SEVERITIES)}
        out: list[tuple[str, str]] = []
        for sentence_id in self.sentence_ids:
            rows = [i for i in self.issues if i.sentence_id == sentence_id]
            if not rows:
                out.append((sentence_id, ""))
                continue
            rows.sort(key=lambda i: (-rank.get(i.severity, 0), i.semantic_category))
            out.append((sentence_id, rows[0].semantic_category or "supported"))
        return tuple(out)

    def semantic_counts(self) -> tuple[tuple[str, int], ...]:
        counts: dict[str, int] = {}
        for issue in self.issues:
            key = issue.semantic_category or "supported"
            counts[key] = counts.get(key, 0) + 1
        return tuple(sorted(counts.items()))

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version, "policy_version": self.policy_version,
            "outcome": self.outcome, "review_producer_kind": self.review_producer_kind,
            "report_version": self.report_version,
            "report_id": self.report_id, "bundle_id": self.bundle_id,
            "draft_id": self.draft_id, "input_manifest_id": self.input_manifest_id,
            "sentence_ids": list(self.sentence_ids),
            "issues": [i.to_dict() for i in self.issues],
            "blocking_issue_ids": list(self.blocking_issue_ids),
            "hard_error_sentence_ids": list(self.hard_error_sentence_ids),
            "excluded_uncited_sentence_ids": list(self.excluded_uncited_sentence_ids),
            "hard_error_override_sentence_ids": list(self.hard_error_override_sentence_ids),
            "semantic_counts": dict(self.semantic_counts()), "call": dict(self.call),
        }


def _reject_release_fields(payload: Any, what: str) -> dict:
    """信封层**指名**拒绝"自我放行"字段（先于未登记字段检查，给出可读理由）。"""
    if not isinstance(payload, dict):
        raise CitedReviewError(f"{what} 必须是对象", reason="response_not_object")
    hit = sorted(set(payload) & set(CITED_REVIEW_FORBIDDEN_ENVELOPE_FIELDS))
    if hit:
        raise CitedReviewError(
            f"{what} 含放行/改写类字段 {hit}：审阅只提意见，不放行、不改稿"
            f"（这些字段在 assurance.schema 里也未登记）", reason="reviewer_release_attempt")
    return payload


def parse_cited_review(text: Any, *, draft: CW.CitedProseDraft,
                       bundle: AS.ReviewInputBundle,
                       report_version: str,
                       review_producer_kind: str,
                       check_report: SentenceCheckReport | None = None,
                       call: Mapping[str, Any] | None = None) -> CitedReviewOutcome:
    """模型返回的 JSON → `CitedReviewOutcome`（逐句意见，`rvi-2`）。

    `review_producer_kind` 是**必需**参数、**没有**默认值：本函数手上只有一段文本，看不出这段
    文本是一个独立模型写的还是替身回放的，因此必须由调用方如实交代；给个默认值等于允许沉默地
    把回声标成独立审阅。

    四条**解析期**硬约束，任一不满足即 `CitedReviewError`：

    1. 顶层只能是 `{"issues": [...]}` 一个键；出现放行/改写类字段当场拒绝；
    2. **逐句覆盖等式**：`issues` 的句子集合必须与 `bundle.sentence_inventory`（= 请求面实际
       发出去的**带引用**句集）**逐条相等**（少一个 = 某句没被独立看过，多一个 = 它在评论
       不存在的句子）；
    3. `citation_id` 必须属于**该句自己的**引用集，不是"清单里随便哪一条"，且必须在本次
       bundle 里；
    4. 每条意见的 `report_version` 必须等于本次 `report_version`。

    给了机械核对报告时，另做一件**不抛错**的事：把"审阅对硬错误句说了 `supported`"的句子
    逐条记进 `hard_error_override_sentence_ids`（理由见模块头——审阅面是故意盲的，这条不是
    它能遵守的规则）。机械硬错误本身原样进 `hard_error_sentence_ids`，两条轴并列。

    不满足时不返回"半份意见"：半份意见会让读者面分不清"没问题"与"没看"。
    """
    import json

    raw = str(text if text is not None else "").strip()
    if not raw:
        raise CitedReviewError("审阅返回为空（没有可解析的意见）", reason="empty_response")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CitedReviewError(
            f"审阅返回不是一个完整 JSON 值：{exc}", reason="response_not_json") from exc
    payload = _reject_release_fields(payload, "CitedReviewOutcome 返回")
    payload = NS._reject_unknown(payload, {"issues"}, "CitedReviewOutcome 返回")

    if bundle.report_version != report_version:
        raise CitedReviewError(
            f"审阅 bundle 绑的 report_version={bundle.report_version!r} 不是本次 "
            f"{report_version!r}", reason="bundle_report_version_mismatch")

    by_sentence = {s.sentence_id: s for s in draft.sentences()}
    reviewed = tuple(str(x) for x in bundle.sentence_inventory)
    excluded = _review_scope(draft=draft, reviewed=reviewed, sentences=by_sentence)
    covered: list[str] = []
    issues: list[AS.ReviewIssue] = []
    for position, row in enumerate(payload.get("issues") or (), start=1):
        row = AS._reject_forbidden(row, AS.REVIEWER_FORBIDDEN_FIELDS, "审阅意见")
        issue = _issue_from_payload(row, draft=draft, bundle=bundle,
                                    report_version=report_version, position=position)
        if issue.sentence_id not in by_sentence:
            raise CitedReviewError(
                f"审阅意见指向本次正文里不存在的句子 {issue.sentence_id!r}"
                f"（本次句子 {sorted(by_sentence)}）",
                reason="issue_sentence_not_in_draft", sentence_id=issue.sentence_id)
        sentence = by_sentence[issue.sentence_id]
        if issue.citation_id not in sentence.citations:
            raise CitedReviewError(
                f"审阅意见把句子 {issue.sentence_id!r} 的问题挂到了它并没有引用的 "
                f"{issue.citation_id!r} 上（该句引用 {list(sentence.citations)}）",
                reason="issue_citation_not_cited_by_sentence",
                sentence_id=issue.sentence_id, citation_id=issue.citation_id)
        if issue.citation_id not in bundle.citation_ids:
            raise CitedReviewError(
                f"审阅意见引用的 {issue.citation_id!r} 不在本次审阅输入里",
                reason="issue_citation_not_in_bundle",
                sentence_id=issue.sentence_id, citation_id=issue.citation_id)
        covered.append(issue.sentence_id)
        issues.append(issue)

    # 覆盖等式的作用域是**审阅对象**（bundle 声明的那一批），不是草稿的全部句子。
    _check_sentence_coverage(reviewed=reviewed, covered=covered)

    hard_error_ids: tuple[str, ...] = ()
    overrides: tuple[str, ...] = ()
    if check_report is not None:
        if check_report.draft_id != draft.draft_id:
            raise CitedReviewError(
                f"机械核对报告绑的草稿 {check_report.draft_id!r} 不是本次草稿 "
                f"{draft.draft_id!r}：用别份报告的硬错误去判这份审阅是错的",
                reason="check_report_draft_mismatch")
        #: `CitedReviewOutcome.hard_error_sentence_ids` 是 wire 字段，语义是「机械层一共标了
        #: 哪几句」⇒ 取**两族并集**（与 `scp-10` 之前 `blocked_sentence_ids` 的逐字口径相同，
        #: 因此本字段的含义与历史产物一致，没有悄悄换义）。
        hard_error_ids = check_report.hard_error_sentence_ids
        #: 只记 id：分歧**长什么样**由 `hard_error_divergence_rows()` 在读回时现算，不进 wire。
        overrides = tuple(sorted(_collect_hard_error_overrides(
            issues=issues, hard_error_sentence_ids=hard_error_ids)))

    issues.sort(key=lambda i: i.issue_id)
    return CitedReviewOutcome(
        schema_version=CITED_REVIEW_SCHEMA_VERSION,
        policy_version=CITED_REVIEW_POLICY_VERSION, outcome="reviewed",
        review_producer_kind=review_producer_kind,
        report_version=report_version, report_id=bundle.report_id,
        bundle_id=bundle.bundle_id, draft_id=draft.draft_id,
        input_manifest_id=draft.input_manifest_id,
        sentence_ids=reviewed, issues=tuple(issues),
        hard_error_sentence_ids=tuple(sorted(hard_error_ids)),
        excluded_uncited_sentence_ids=excluded,
        hard_error_override_sentence_ids=overrides,
        call=dict(call) if call else {"prompt_version": CITED_REVIEW_PROMPT_VERSION})


def _issue_from_payload(payload: Any, *, draft: CW.CitedProseDraft,
                        bundle: AS.ReviewInputBundle,
                        report_version: str, position: int = 0) -> AS.ReviewIssue:
    """一条意见对象 → `AS.ReviewIssue`（`crr-9`）。

    **每条意见的失败都必须带位置**。此前这里直接调 `AS.ReviewIssue.create`：模型漏写
    `category`（或把它写成空串）时，抛的是 `AS.AssuranceSchemaError`，消息里**只有**那个取值、
    **没有**是哪一条意见出错——运行目录因此只剩一句无定位的原因，读者回不到出问题的那一行。
    `crr-9` 真实公司节（`m930_3_cited_real_company_cp20_r1`，`call_id
    b3328e284ee14912b5de83fa0bfe5d88`）就是这样丢掉了整条审阅轴：回复完整、HTTP ok、20 条意见
    齐全，其中一条把 `citation_id` 写了**两遍**、`category` 一个都没有——`json.loads` 静默压掉
    重复键，`_reject_unknown` 便看不到缺口，空串一路走到 schema 才炸。

    本函数把**这一条意见的**任何不合约都收敛成 `CitedReviewError`，并带上 `sentence_id` /
    `citation_id`，让原因码与定位一起落到运行目录里。**不猜、不降级**：`category` 没有可推断的
    默认值，这里绝不把它补成 `supported`，也绝不返回"半份意见"——少一条就是整次作废。
    """
    where = f"审阅意见第 {position} 条" if position else "审阅意见"
    if not isinstance(payload, Mapping):
        raise CitedReviewError(f"{where}不是一个对象，得到 {type(payload).__name__}："
                               f"整次返回作废（不返回半份意见）",
                               reason="review_reply_unparsable")
    try:
        payload = NS._reject_unknown(
            payload, {"sentence_id", "citation_id", "category", "semantic_category", "severity",
                      "blocking", "reason", "evidence_refs", "suggested_target"},
            "审阅意见")
    except NS.NarrativeSchemaError as exc:
        raise CitedReviewError(f"{where}字段集不合约：{exc}",
                               reason="review_reply_unparsable") from exc
    sentence_id = str(payload.get("sentence_id") or "")
    citation_id = str(payload.get("citation_id") or "")
    where = f"{where}（句 {sentence_id or '?'}／引用 {citation_id or '?'}）"
    #: **缺键**与**空串**是两种不同的形状，分开报。`category` 尤其：它在 schema 里是一份封闭
    #: 词表、没有任何可推断的默认值，缺了它既不能猜成 `supported`（那是把"没看过"记成"没问题"），
    #: 也不能只留一个 grep 不到的 `''`。`reason` 同理——没有理由的"没问题"与"没看过"在下游不可区分。
    for name in ("category", "reason"):
        if name not in payload:
            raise CitedReviewError(
                f"{where}缺字段 {name!r}：审阅意见的字段必须逐条明文写出，缺键与空串同样使整次"
                f"返回作废（`category` 没有可推断的默认值，程序不会把它猜成 supported）",
                reason="review_reply_unparsable",
                sentence_id=sentence_id, citation_id=citation_id)
    target = payload.get("suggested_target")
    try:
        return AS.ReviewIssue.create(
            report_version=report_version,
            unit_ref=AS.ReviewUnitRef.create(unit_kind=CITED_REVIEW_UNIT_KIND,
                                             unit_id=citation_id or "?"),
            category=str(payload.get("category") or ""),
            severity=str(payload.get("severity") or ""),
            blocking=bool(payload.get("blocking")),
            reason=str(payload.get("reason") or ""),
            evidence_refs=tuple(str(x) for x in (payload.get("evidence_refs") or ())),
            suggested_target=(None if target is None else AS.SuggestedTarget.from_dict(target)),
            schema_version=AS.REVIEW_ISSUE_SCHEMA_VERSION,
            sentence_id=sentence_id, citation_id=citation_id,
            semantic_category=str(payload.get("semantic_category") or ""))
    except AS.AssuranceSchemaError as exc:
        raise CitedReviewError(f"{where}不合约：{exc}", reason="review_reply_unparsable",
                               sentence_id=sentence_id, citation_id=citation_id) from exc


def _review_scope(*, draft: CW.CitedProseDraft, reviewed: Sequence[str],
                  sentences: Mapping[str, Any]) -> tuple[str, ...]:
    """圈定**审阅对象**，返回**不在对象内**的句子（草稿里的零引用句）。

    三条当场核对，缺一不可：

    * 对象里的每个 id 都得是草稿里真实存在的句子；
    * 对象**恰好**等于草稿里带引用的那些句子——少一句是"审阅被喂了残缺的稿"，多一句是
      "把一句没有合法审阅单元的句子塞进了对象里"（`rvi-2` 的 `citation_id` 必填）；
    * 圈外句逐个都真的没有引用。

    返回的圈外句进 `CitedReviewOutcome.excluded_uncited_sentence_ids`，读者面上标成"不在独立
    审阅对象内"。它与"审阅看了、没问题"必须分得开——这正是覆盖等式存在的理由。
    """
    draft_ids = set(sentences)
    unknown = sorted(set(reviewed) - draft_ids)
    if unknown:
        raise CitedReviewError(
            f"审阅对象的 sentence_inventory 含草稿里不存在的句子 {unknown}"
            f"（草稿共 {len(draft_ids)} 句）", reason="review_inventory_not_in_draft")
    cited_ids = tuple(s for s in draft.sentence_ids() if sentences[s].citations)
    if sorted(reviewed) != sorted(cited_ids):
        missing = sorted(set(cited_ids) - set(reviewed))
        extra = sorted(set(reviewed) - set(cited_ids))
        raise CitedReviewError(
            f"审阅对象与草稿里带引用的句子不相等：缺 {missing}，多 {extra}——"
            "审阅对象只能是「一句话 + 它引的来源」这个最小单元",
            reason="review_scope_mismatch")
    excluded = tuple(s for s in draft.sentence_ids() if not sentences[s].citations)
    leaked = sorted(s for s in excluded if s in set(reviewed))
    if leaked:  # pragma: no cover - 上面那条等式已排除，留作双保险
        raise CitedReviewError(
            f"零引用句 {leaked} 出现在审阅对象里：它给不出合法的审阅单元",
            reason="uncited_sentence_in_review_scope")
    return excluded


def _check_sentence_coverage(*, reviewed: Sequence[str], covered: Sequence[str]) -> None:
    """逐句覆盖等式：审阅必须对**对象里的每一句**表态，且不得对别的句子表态。

    与 `cited_writer._check_subsection_coverage` 同一纪律，理由也一样：请求了 N 句、只回了
    N-2 句，读者第一反应是"那两句没问题"——而真实原因可能是"模型漏看了"。没有这条等式，
    两者不可区分，而"哪几句被独立看过"恰恰是审阅**唯一**能提供的东西。
    """
    got = list(covered)
    if len(set(got)) != len(got):
        dup = sorted({x for x in got if got.count(x) > 1})
        raise CitedReviewError(
            f"同一句话收到了多条审阅意见 {dup}：逐句表态是「一句一条」，重复会让覆盖等式失真",
            reason="issue_sentence_duplicate")
    want = sorted(reviewed)
    if sorted(got) != want:
        missing = sorted(set(want) - set(got))
        extra = sorted(set(got) - set(want))
        raise CitedReviewError(
            f"审阅未逐句表态：缺 {missing}，多 {extra}（审阅对象共 {len(want)} 句）",
            reason="sentence_coverage_mismatch")


#: 分歧行的固定键集。多一个少一个都拒绝：读者面按这十个键摊开这张表。
_DIVERGENCE_FIELDS = ("sentence_id", "check_kind", "failure_reason", "surfaces",
                      "citation_keys", "review_issue_id", "citation_id",
                      "review_severity", "review_semantic_category", "review_reason")


def _normalize_divergences(rows: Sequence[Mapping[str, Any]]
                           ) -> tuple[dict[str, Any], ...]:
    """把分歧行收敛成**固定键集 + 固定行序**的纯数据。

    键集不合约即拒（不补空键、不丢多键），行序按 `(sentence_id, check_kind,
    review_issue_id)` 定死——否则同一份意见换个响应顺序就换一张表，读者会以为发生了两次
    不同的分歧。
    """
    normalized: list[dict[str, Any]] = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            raise CitedReviewError(
                f"分歧行的每一行必须是对象，得到 {type(row).__name__}",
                reason="hard_error_divergence_malformed")
        missing = sorted(set(_DIVERGENCE_FIELDS) - set(row))
        extra = sorted(set(row) - set(_DIVERGENCE_FIELDS))
        if missing or extra:
            raise CitedReviewError(
                f"分歧行的行字段不合约：缺 {missing}、多 {extra}"
                f"（固定键集 {list(_DIVERGENCE_FIELDS)}）",
                reason="hard_error_divergence_malformed")
        normalized.append({
            "sentence_id": str(row["sentence_id"]),
            "check_kind": str(row["check_kind"]),
            "failure_reason": str(row["failure_reason"]),
            "surfaces": [str(x) for x in (row["surfaces"] or ())],
            "citation_keys": [str(x) for x in (row["citation_keys"] or ())],
            "review_issue_id": str(row["review_issue_id"]),
            "citation_id": str(row["citation_id"]),
            "review_severity": str(row["review_severity"]),
            "review_semantic_category": str(row["review_semantic_category"]),
            "review_reason": str(row["review_reason"]),
        })
    normalized.sort(key=lambda r: (r["sentence_id"], r["check_kind"], r["review_issue_id"]))
    return tuple(normalized)


def _collect_hard_error_overrides(*, issues: Sequence[AS.ReviewIssue],
                                  hard_error_sentence_ids: Sequence[str]
                                  ) -> tuple[str, ...]:
    """审阅对机械硬错误句说 `supported` 的句子——**记录下来，不抛错**。

    `crv-2` 之前这里抛 `review_overrides_hard_error`。真实 r28 证明那条规则自相矛盾：审阅面
    是**故意盲的**（`build_cited_review_request` 只给正文与来源，不给任何机械结论），一个看不到
    硬错误的审阅者"不得说 supported"是它无从遵守的规则；它照自己的读法说 supported，链子却
    在解析期整轮停住，23 句草稿一节不剩。真正的保护不在解析期，而在**聚合**：机械硬错误优先，
    有硬错误就不得出现"系统审阅通过"（见 `cited_report`）。于是这条分歧被如实记下、并列显示。
    """
    blocked = set(str(x) for x in hard_error_sentence_ids)
    return tuple(dict.fromkeys(
        i.sentence_id for i in issues
        if i.sentence_id in blocked and i.category == "supported"))


def hard_error_divergence_rows(*, issues: Sequence[AS.ReviewIssue],
                               check_report: SentenceCheckReport
                               ) -> tuple[dict[str, Any], ...]:
    """把"审阅与机械层的分歧"逐处摊成可读的行——**派生读数，不是 wire 字段**。

    真实 cp-21 的形态正是"审阅给了理由，而理由与来源对不上"：只记一个 `s0006` 的 id，读者
    回不到那句理由上。于是这里把**机械侧**（`check_kind` / `failure_reason` / `surfaces` /
    `citation_keys`，逐字取自 `sections/sentence_check.py` 那条 `hard_error` 记录）与**审阅侧**
    （`review_issue_id` / `citation_id` / `review_severity` / `review_semantic_category` /
    `review_reason` **逐字**）按 `(sentence_id, check_kind, review_issue_id)` 对齐抄下来，
    每处一行。只收 `category == "supported"` 的意见：这是**分歧**记录（审阅说没问题、机械层说
    硬错误），普通意见不在此列。

    **为什么不做成 `CitedReviewOutcome` 的字段**：本函数的两个输入（`issues` 与
    `sentence_checks.json`）都**已经落盘**，加字段就要升 `schema_version`，而升版会让每一份
    历史 `review_issues.json` 都解不出来——包括已冻结的演示 run。用一条纯派生的读数作废全部
    历史归档，代价与收益不成比例；读回时现算的结果**逐字相同**，还能回溯到旧 run 上。

    这里**不做**任何新判断，也不裁决、不放行：机械硬错误优先仍是 `cited_report` 的事。
    """
    #: 这条读数是「机械层标了硬错、审阅说没问题」的**分歧**榜，因此筛的是**两族并集**
    #: （`hard_error_sentence_ids`）：栏目覆盖族的分歧同样要露出来。若用 `scp-10` 起的
    #: `blocked_sentence_ids`（只收事实安全族），这些分歧会从榜上静默消失。
    blocked = {str(x) for x in check_report.hard_error_sentence_ids}
    by_sentence: dict[str, list[AS.ReviewIssue]] = {}
    for issue in issues:
        if issue.sentence_id in blocked and issue.category == "supported":
            by_sentence.setdefault(issue.sentence_id, []).append(issue)
    rows: list[dict[str, Any]] = []
    for record in check_report.records:
        if record.verdict != "hard_error" or record.sentence_id not in by_sentence:
            continue
        for issue in by_sentence[record.sentence_id]:
            rows.append({
                "sentence_id": str(record.sentence_id),
                "check_kind": str(record.check_kind),
                "failure_reason": str(record.failure_reason),
                "surfaces": [str(x) for x in (record.surfaces or ())],
                "citation_keys": [str(x) for x in (record.citation_keys or ())],
                "review_issue_id": str(issue.issue_id),
                "citation_id": str(issue.citation_id),
                "review_severity": str(issue.severity),
                "review_semantic_category": str(issue.semantic_category or "supported"),
                "review_reason": str(issue.reason or ""),
            })
    return _normalize_divergences(rows)


#: §7c 那一支的固定键集：与 §7b 的分歧行**不是同一个形状**（那一支还有机械侧的
#: `check_kind`/`failure_reason`/`surfaces`，本支里这些**本来就为空**——机械层没判出东西），
#: 因此另立一张表、另用一个收敛器，不硬塞进 `_DIVERGENCE_FIELDS` 里补空键。
_REVIEW_ONLY_FIELDS = (
    "sentence_id", "review_issue_id", "citation_id", "review_category",
    "review_semantic_category", "review_severity", "review_blocking",
    "review_reason", "mechanical_kinds", "mechanical_verdicts",
    "mechanical_applicable",
)


def _normalize_review_only_rows(rows: Sequence[Mapping[str, Any]]
                                ) -> tuple[dict[str, Any], ...]:
    """同 `_normalize_divergences` 的纪律：固定键集、固定行序（不合约即拒）。"""
    normalized: list[dict[str, Any]] = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            raise CitedReviewError(
                f"审阅独有意见行的每一行必须是对象，得到 {type(row).__name__}",
                reason="review_only_divergence_malformed")
        missing = sorted(set(_REVIEW_ONLY_FIELDS) - set(row))
        extra = sorted(set(row) - set(_REVIEW_ONLY_FIELDS))
        if missing or extra:
            raise CitedReviewError(
                f"审阅独有意见行的行字段不合约：缺 {missing}、多 {extra}"
                f"（固定键集 {list(_REVIEW_ONLY_FIELDS)}）",
                reason="review_only_divergence_malformed")
        normalized.append({
            "sentence_id": str(row["sentence_id"]),
            "review_issue_id": str(row["review_issue_id"]),
            "citation_id": str(row["citation_id"]),
            "review_category": str(row["review_category"]),
            "review_semantic_category": str(row["review_semantic_category"]),
            "review_severity": str(row["review_severity"]),
            "review_blocking": bool(row["review_blocking"]),
            "review_reason": str(row["review_reason"]),
            "mechanical_kinds": [str(x) for x in (row["mechanical_kinds"] or ())],
            "mechanical_verdicts": [str(x) for x in (row["mechanical_verdicts"] or ())],
            "mechanical_applicable": [bool(x) for x in (row["mechanical_applicable"] or ())],
        })
    normalized.sort(key=lambda r: (r["sentence_id"], r["review_issue_id"]))
    return tuple(normalized)


def review_only_divergence_rows(*, issues: Sequence[AS.ReviewIssue],
                                check_report: SentenceCheckReport
                                ) -> tuple[dict[str, Any], ...]:
    """审阅**提出了**意见、而机械层**没有**判硬错的句子——逐处并列。**派生读数，非 wire 字段**。

    方向与 `hard_error_divergence_rows` 恰好相反：那一支是「机械说有错、审阅说 supported」，
    本支是「审阅说有语义问题、机械层 18 条轴一条都没拦住」。两支都**不**裁决谁对，
    但都必须能读到，否则读者会把「机械绿」当成「这一句没问题」：

    * 前者说明**审阅面对机械结论是盲的**（`build_cited_review_request` 故意不给机械结论）；
    * 后者说明**确定性判据看不见的那几类事**——把发行人自述写成客观结论、旧年材料当成当期、
      跨栏拼接、重要限制遗漏——**只能**由独立语义审阅提出。18 条轴里没有一条判「这句话的
      口径是不是公司自夸」，也没有一条判「这句只引了有利的一半」。机械绿 **不等于**语义通过。

    只收**真意见**：`category == "supported"` 不进本表（那不是意见，是「没问题」）。
    同句多条意见逐条列出。这里**不做**新判断、不改任何 verdict、不改发布门。
    """
    #: 与 7b（机械说硬错、审阅说没问题）互补：凡是机械层标了硬错的句子——**两族都算**——
    #: 都归那一支。用 `scp-10` 起的 `blocked_sentence_ids`（只收事实安全族）会让栏目覆盖族
    #: 的句子同时出现在两张表里，读者读成两条互不相容的结论。
    blocked = {str(x) for x in check_report.hard_error_sentence_ids}
    verdicts: dict[str, list[tuple[str, str, bool]]] = {}
    for record in check_report.records:
        verdicts.setdefault(str(record.sentence_id), []).append(
            (str(record.check_kind), str(record.verdict), bool(record.applicable)))
    rows: list[dict[str, Any]] = []
    for issue in issues:
        sentence_id = str(issue.sentence_id)
        if str(issue.category) == "supported":
            continue
        if sentence_id in blocked:
            # 已经有硬错误的句子归 7b 那一支（机械侧优先）；本表只收机械层**没**拦住的。
            continue
        checked = verdicts.get(sentence_id, [])
        rows.append({
            "sentence_id": sentence_id,
            "review_issue_id": str(issue.issue_id),
            "citation_id": str(issue.citation_id),
            "review_category": str(issue.category),
            "review_semantic_category": str(issue.semantic_category or "supported"),
            "review_severity": str(issue.severity),
            "review_blocking": bool(issue.blocking),
            "review_reason": str(issue.reason or ""),
            #: 机械侧把这一句判成了什么：`pass` = 判过且通过；`not_applicable` = 这一轴这次没判
            #: （`applicable=False`，一个 pass 不等于结论）；空 = 这一句**没有任何**机械记录。
            "mechanical_kinds": [k for k, _v, _a in checked],
            "mechanical_verdicts": sorted({v for _k, v, _a in checked}),
            "mechanical_applicable": sorted({a for _k, _v, a in checked}),
        })
    return _normalize_review_only_rows(rows)


# ---------------------------------------------------------------------------
# 编排：唯一入口（本模块**不**自己调用模型，client 由调用方注入）
# ---------------------------------------------------------------------------

def review_cited_prose(*, draft: CW.CitedProseDraft,
                       manifest: CW.CitedWriterInputManifest,
                       client: CitedReviewClient, report_version: str, report_id: str,
                       check_report: SentenceCheckReport | None = None,
                       system: str | None = None, model_policy: str = "stub",
                       prompt_version: str = CITED_REVIEW_PROMPT_VERSION,
                       max_tokens: int | None = None) -> CitedReviewOutcome:
    """审阅链的**唯一**执行入口：装配隔离输入 → 调用 → 解析 → 逐句意见。

    产出者身份由 `client` 本身推出（`review_producer_kind_of`），调用方**没有**声明它的入口：
    用参数声明自己是哪种审阅，就等于给自己发了一张「把回声标成独立审阅」的通行证。

    解析失败时**抛出**（不返回半份意见）。客户端每次真正发起的调用记在 `client.calls` 上。
    本函数**不修改**草稿、**不写**任何产物、**不**产生放行状态。
    """
    request = build_cited_review_request(draft=draft, manifest=manifest)
    bundle = build_cited_review_bundle(
        draft=draft, manifest=manifest, request=request,
        report_version=report_version, report_id=report_id,
        model_policy_id=model_policy, prompt_version=prompt_version)
    messages, system_text = build_cited_review_messages(
        request=request, system=system if system is not None else load_cited_review_prompt())
    if max_tokens is not None and isinstance(client, LlmCitedReviewClient):
        client.max_tokens = int(max_tokens)
    producer_kind = review_producer_kind_of(client)
    result = client.review(messages=messages, system=system_text,
                           prompt_version=prompt_version, model_policy=model_policy)
    if result.status != "ok":
        raise CitedReviewError(f"审阅调用失败：{result.error or result.status}",
                               reason="review_call_failed")
    return parse_cited_review(result.text, draft=draft, bundle=bundle,
                              report_version=report_version,
                              review_producer_kind=producer_kind,
                              check_report=check_report,
                              call={**result.to_dict(), "model_policy": model_policy,
                                    "review_producer_kind": producer_kind})

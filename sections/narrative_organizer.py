"""门**后** final Narrative 的**自然组织**（§三 C；narr-8 / norg-8）。

定位（与 §0.13 的单向链一致）：

    … → AcceptedSupportBinding → SectionClaim → **final Narrative** → SectionResult（单向引用 draft）

本模块**不是**第二个写作器，也不是第二个 LLM runtime：它复用注入的同一个 `NarrationClient`
（`sections/pack_writer` 定义的那一个协议），只做**门后**的组织——输入是已定稿的
`SectionClaim`、已接受的 context 支撑、typed 缺口、WritingSpec、PresentationProfile，以及
（`norg-7` 起，且仅在 `draft` 在场时）**门前自然草稿**里**过了门的那些原子**，还有（`norg-8`
起，`orgmc-1`）草稿**点名过的**成员的**材料正文**（只读依据面，见 `organizer_material_context`），
输出是一份 narr-8 `SectionNarrative`。它拿不到候选束本身、更拿不到**没过门**的原子（`norg-7`
的输入面只投 `kept` 原子的台账与所属草稿文本），因此不可能绕过两道门；`norg-8` 多投的材料
正文也**不授权任何字面**（表面核验仍只认这一句声明的 Claim 文本）。它也不产出任何决定、评估
或审批：组织器**不能批准自己的输出**。组织语与句类也都是系统判的：句子里声明 `prose_unit_id`
才是自然改写写法，`sentence_kind` 一律是被忽略的模型字段。

表格（§七 2）不是模型的产物：门后**确定性**构造器（`NS.build_metric_period_tables`）把
「重复的指标 × 期间」事实陈列成 `NarrativeTable`，被它承载的 Claim **不进入**本次调用，
它们的去向由本模块确定性补齐。因此「模型把同一批事实又写成句子」在两侧都不可表达。

为什么必须补一道确定性核验：自然组织把正文文本从「确定性派生」变成了模型产物。文本不再可
复算，但三件事仍然可机械判定，且都由 `sections.narrative_schema` 的**唯一**实现给出：

* 引用的 Claim 必须存在且当前（`verify_section_narrative` 第 1 条）；
* 高风险表面必须逐字来自**它自己声明的** Claim 文本（第 2/3 条：组合句走
  `unauthorized_surfaces_within_claims`，表面只在单个组织段内合成、不跨接缝）；
* context 只有背景权，且必须是本节已接受的绑定（第 4 条）；
* 每条已定稿 Claim 恰好一条 selected/omitted 去向，且去向与正文真实引用一致
  （第 5 条，`verify_claim_narrative_dispositions` + `verify_section_narrative_claims_selected`）。

组装器读回后用**同一函数**复算，因此「组织器认为合规」与「组装器认为合规」不可能有两套口径。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Mapping, Sequence

from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import presentation_profile as PP

logger = logging.getLogger("sections.narrative_organizer")


class NarrativeOrganizerError(Exception):
    """门后自然组织的 fail-closed 失败（不可部分接受）。"""


#: prompt **资产名**（→ `llm/prompts/<name>.txt`）。§三 C 要求「新的、版本化的 prompt 资产」，
#: 因此这是**新文件**：`pack_section_writer_proposals_v*` 是门**前**提案器，职责完全不同，
#: 不得复用（让一份资产承担两种职责，等于把两个门的输入面混成一份契约）。
NARRATIVE_ORGANIZER_PROMPT_ASSET = "section_narrative_organizer_v4"
#: 同一资产内的修订号。`norg-2`：把「多 Claim 自然句」写进资产正文（§七 1）——声明的 Claim
#: 文本必须**原样、完整**按声明顺序逐字出现，组织语只允许插在 Claim 文本**之间**，一句最多 4 条。
#: `norg-3`（M930-3 返修 P2 §三 2.4）：新增「一句就是一个句子」一节——句末标点至多出现一次且
#: 只能在末尾，`A。同时B。` 是两句相接而不是一条多 Claim 自然句；自带句末标点的 Claim
#: **只能单独成句**。同时写明表面核验按**文本分段**进行（衔接区里自造主体名/数字照样被拒，
#: 但不跨接缝合成表面）。旧资产不原位修改。
#: `norg-4`（M930-3 写作主链定点批 §二）：新增三节——「按 `theme_key` 分主题组织」（同一主题
#: 归同段、段落顺序按 `theme_order`、主题键只是组织线索不进正文）、「接缝必须先落分隔标点」
#: （`A，此外，B` 合法 / `A此外，B` 会被第 8 条新判据 e 判为病句）、「同段内的同义重复要合并
#: 成 `redundant_with_selected_claim`，但不得因此丢掉任何独有事实」。旧修订号不原位复用：
#: 资产正文与规则集都变了，`norg-3` 的产物必须仍能被识别为旧口径。
#: `norg-5`（M930-3 指令三：让正文业务上能读）：新增三节，与门 `ng-12` 的第 10/11 条**同源**——
#:   * 「衔接语只能并列，不能断言关系」：`另一方面`/`在此基础上`/`其中`/`综上` 断言两条断言之间
#:     的对照/递进/从属/归纳关系，材料没做过这个判断，写出来就是替材料下结论；只有该连接语
#:     **逐字出现在本句声明的 Claim 文本里**才可用，否则一律用中性并列 `此外`/`同时`；
#:   * 「发行人自述必须标明出处」：`一流`/`核心优势`/`前瞻性`… 逐字就在 Claim 里，第 1 条判据
#:     必然放行，但它们**是发行人说的**；正文必须在**该条 Claim 紧前的组织段**给出封闭归属语，
#:     否则读者会把发行人自述读成授信分析结论；
#:   * 「近义重复候选必须逐对裁决」：输入的 `redundancy_candidates` 是**字符相似度**筛出的**候选**，
#:     不是判定；逐对二选一（真同一断言 → 保留更全的一条；差任何一项内容 → 两条都保留），
#:     且明确写出「高相似度不等于重复」的反例（同一词干下的交流侧/直流侧系统）。
#: 三节都**只新增要求、不放宽**旧要求：第 1～4 条「你可以做」与既有的硬要求全部原样保留。
#: `norg-6`（M930-3 返修 ④：O-12 的期间纪律在**组织**侧落地）：新增一节「不得把断言挪到当前
#: 时点」，与门 `ng-13` 的第 12 条**同源**——`目前` / `当前` / `仍` / `持续` / `现有` 这类词不含
#: 任何数字与期间（所以第 1 条必然放行），但它们把断言从材料说的那个时点搬到了现在；一条只由
#: 同类较旧材料支撑的断言被这样一写，读者读到的是材料没说过的一件事。判据与既有的第 9/10 条
#: 同形：这些词必须**逐字出现在本句声明的 Claim 文本里**，材料没写而组织语写了即拒。
#: 只新增要求、不放宽旧要求：`norg-5` 的三节与更早的各节一字未改。
#: `norg-7`（M930-3 指令 E 第 3 项：恢复材料驱动写作，**门后**那一半）：新增「以门前草稿为表达
#: 基础」的**第二种句子写法**——句子里声明 `prose_unit_id` 时，句子按**保真**核验（七轴，
#: `natfid-1` / `ng-14`），不再要求 Claim 文本逐字按序出现；不声明时逐字写法与既有各节的
#: 要求**一字未改**。配套三件事必须一起读：
#:   * **表达基础是草稿文本本身**，`material_member_refs` 只是出处标注、不是授权：草稿文本里
#:     没有的说法照样不得出现（否则「自然改写」会退化成「自己另写一段」）；
#:   * **`kept` / `dropped` 原子台账**：上游两道门逐条打回的原子（`dropped`）没有 `claim_id`，
#:     草稿文本里讲这些原子的分句必须**删掉或改写掉**；一句的 `claim_ids` 只能是它所声明那一段
#:     草稿里 `kept` 的那些。原子全部被丢弃的草稿**不进输入**，因此不得被表达；
#:   * **句类由系统判**：写法由「有没有声明 `prose_unit_id`」决定，`sentence_kind` 仍是被忽略的
#:     模型字段——写它不会把一句变成自然改写句，也不会让一句躲开逐字判据。
#: `norg-8`（M930-3 指令「打通真实写作请求」P1-d）：新增一份**只读**依据面 `material_context`
#: （`orgmc-1`）——`prose_draft` 每行点名的成员的材料正文 + 出处身份。改的是**输入面**（模型
#: 能看到什么），输出面一个键不增，判定集也不动，因此 `narr-8` 与 `norg-7` 都不随之前进。
#: 旧资产 `section_narrative_organizer_v2`（`norg-6`）与 `section_narrative_organizer_v3`
#: （`norg-7`）原样留在盘上、不再被登记（见 `evals/test_demo_narrative_organizer.py` 的取代面
#: 断言），不得原位改写——「登记版本 = 实际加载版本」这条纪律靠的是**新文件**，不是就地改字。
NARRATIVE_ORGANIZER_PROMPT_REVISION = "norg-8"
#: 进入产物 / 报告身份的 prompt 身份 = 资产名@修订号。
NARRATIVE_ORGANIZER_PROMPT_VERSION = (
    f"{NARRATIVE_ORGANIZER_PROMPT_ASSET}@{NARRATIVE_ORGANIZER_PROMPT_REVISION}")
#: 资产**正文**的内容指纹（sha256，行尾归一后）。资产名与修订号都可被照抄，正文不行。
NARRATIVE_ORGANIZER_PROMPT_SHA256 = (
    "552f3e0877a71241b1199c16f98be267ce44a5f2a6b83bd6eda74e42bddd33fa")
#: 组织**规则**版本（与 prompt 修订号同源，字面量唯一在这里）。
#: `norg-3`：规则集随门 `ng-8` 前进——组合句不得含句中句末标点，且表面核验边界感知。
#: `norg-4`：规则集随门 `ng-11` 前进——组合句的接缝必须先落分隔标点（第 8 条判据 e），
#: 且正文按材料标题派生的 `theme_key` 分主题组织。
#: `norg-5`：规则集随门 `ng-12` 前进——关系性连接语必须有材料逐字支持（第 10 条）、发行人
#: 自述必须有归属（第 11 条）、`redundancy_candidates` 必须逐对裁决。这三条**不是**新的输出面
#: 字段：逐对裁决的载体就是既有的 `claim_dispositions`（每条 Claim 恰好一次），因此 wire
#: （`narr-6`）不随它前进——变的只是「模型被告知要遵守什么」。
#: `norg-6`：规则集随门 `ng-13` 前进——组织语不得把断言挪到当前时点（第 12 条）。同样**不是**
#: 新的输出面字段：判据只读既有的句子文本与它声明的 Claim，因此 wire（`narr-6`）不随它前进。
#: `norg-7`：规则集新增**句级来源闭合**两条——(i) 声明了 `prose_unit_id` 的句子，其 `claim_ids`
#: 必须是该段草稿 `kept` 原子所对应 Claim 的**子集**（不得借一段草稿之名去声明别的 Claim）；
#: (ii) 未在本轮输入里的 `prose_unit_id`（不存在的、`kept` 原子为空的、属于别的修订的）一律拒。
#: 两条都由系统在 `_sentence_specs` 处判定，**不是**新的 wire 字段（`prose_unit_id` 是**输入面**
#: 的声明被回的键，句类仍由系统标注），因此 `narr-7` 的字段形状不随它前进；变的是**判定集**：
#: 同一份组织器输出在 `norg-6` 下没有这两条，在 `norg-7` 下可能被拒，故不得共用版本号。
#: `norg-8`（`orgmc-1`）**故意不前进本版本**：本批只给输入面多投一份只读的材料正文
#: （`material_context`），**判定集一个字没改**——同一份组织器输出在 `norg-8` 下与在 `norg-7`
#: 下得到的裁决完全相同（表面核验仍只认这一句声明的 Claim 文本，`prose_unit_id` 的句级来源
#: 闭合仍是 `_sentence_specs` 那两条）。若把「模型被告知要遵守什么」的变化记成本版本的变化，
#: 就无法再从版本号判断「同一份输出会不会得到不同裁决」——那正是本版本存在的唯一理由。
NARRATIVE_ORGANIZER_RULES_VERSION = "norg-7"
#: **有界定向重组织**的版本（⑥）。它与上面三个版本**不是**同一件事，因此必须各有各的字面量：
#:   * `norg-4`（= prompt 修订号）说的是「模型被告知要遵守什么」；
#:   * `narr-6` / `nrules-13` / `ng-12` 说的是「wire 形状」与「判据集」；
#:   * 本版本说的是「**同一份模型输出**在进入核验之前，系统对它的组织形态做了什么**确定性补救**」。
#: 补救不改任何判据（补救后的正文仍要整份过 `ng-12`），也不改任何 wire 字段，因此上面三个版本
#: 不随它前进；但它确实会改变「同一份模型输出得到哪一份 Narrative」，所以它必须自己有版本号、
#: 并且**进 trace**（进不了 trace 的版本号等于没有）。
NARRATIVE_ORGANIZER_REORGANIZATION_VERSION = "norg-reorg-1"

#: 有界定向重组织的**封闭补救码**（每次补救恰好一个，逐条进 trace）：
#:   * `split_at_sentence_terminators`：按模型**自己的**句中句末标点切段并逐段重绑 Claim
#:     （文本逐字不变，模型的衔接语跟着它原本相邻的那一段留下）；
#:   * `per_claim_factual_sentence`：切不出全合法的段时，改为**每条 Claim 各自成一句**逐字事实
#:     句（`NS.render_factual_text` 的唯一渲染口径），只丢弃纯标点的接缝。
REORGANIZATION_SPLIT_AT_TERMINATORS = "split_at_sentence_terminators"
REORGANIZATION_PER_CLAIM_FACTUAL = "per_claim_factual_sentence"
REORGANIZATION_REMEDIES = (
    REORGANIZATION_SPLIT_AT_TERMINATORS, REORGANIZATION_PER_CLAIM_FACTUAL)

#: 补救后的句计划里标注句类的键。**系统内部键，故意不叫 `sentence_kind`**：
#: `sentence_kind` 在组织器的输出面上是**被接受但被忽略**的键（`_sentence_specs` 一律按
#: `composed` 组装），为的是模型不能自称「我这句是 factual」来绕过第 8 条。补救产物走的是同一个
#: `_sentence_specs`，所以它的句类必须来自一个模型**写不出来**的键：本键不在
#: `_ADDITIONAL_SENTENCE_KEYS` 里，模型输出带它会被 `parse_organizer_plan` 直接拒。
REPAIRED_SENTENCE_KIND_KEY = "_repaired_sentence_kind"

#: 输出面：顶层与段落的键集（封闭）。段落的 `topic_ids` 与句子的 citation **不在这里**：它们由
#: 系统从模型声明的 Claim 确定性派生，模型自报即拒（`parse_organizer_plan` 的措辞就是这件事）。
_PLAN_KEYS = ("paragraphs", "claim_dispositions")
_PARAGRAPH_KEYS = ("sentences",)

#: 输出面：只允许这些键（出现别的键——包括任何定稿对象、决定、评估字段——即拒）。
#: `prose_unit_id`（`norg-7`）是句子面上的**第五个**键，也是**唯一**一个可选键：声明它就把这一句
#: 切到自然改写写法（保真核验），不声明就是逐字写法。它是**声明来源**，不是**声明句类**——
#: 句类仍由系统从「有没有声明它」推导（`_sentence_specs`），因此模型既不能靠写它把一句没有
#: 来源的话变成自然改写句（那一段草稿的 `kept` 台账会在同一个函数里逐条对上），也不能靠写
#: `sentence_kind` 让逐字写法变成别的写法。
_SENTENCE_KEYS = ("text", "claim_ids", "context_binding_ids", "prose_unit_id")
_ADDITIONAL_SENTENCE_KEYS = _SENTENCE_KEYS + ("sentence_kind",)
_DISPOSITION_KEYS = ("claim_id", "disposition", "reason_code")

#: 组织器使用的 model policy 槽位。它与门前的候选提案器**不同槽**：同一槽位会让「本次调用的
#: 实际模型策略」在一份产物里出现两个真值。
NARRATIVE_ORGANIZER_MODEL_POLICY = "narrator"

#: `orgmc-1`：门后组织器改写时的**材料正文上下文**版本（`organizer_material_context` 的产物）。
#: 它是**输入面**的一条政策，不是 wire 字段：组织器的输出面（`_PLAN_KEYS` / `_SENTENCE_KEYS`）
#: 一个键都不增，句子仍只声明 `claim_ids` / `context_binding_ids` / `prose_unit_id`。因此
#: `narr-8` 的字段形状与 `norg-7` 的判定集都不随它前进——变的只有「模型能看到什么」。
ORGANIZER_MATERIAL_CONTEXT_VERSION = "orgmc-1"

#: `orgmc-1` 的**封闭失败原因**（每条 fail-closed 恰好一个，措辞进异常消息、可被调用方逐条认读）。
#: 与「缺口文案」不同：它不进任何产物字段，只描述**这条输入面为什么没建成**。
ORGANIZER_MATERIAL_CONTEXT_REASONS = (
    # 台账点名了一个 manifest 里没有的成员（草稿声明的出处不在本节材料清单内）。
    "declared_member_not_in_manifest",
    # 台账点名的成员在 manifest 里，但没有已解析正文（清单非空却没有 `wmctx-1` 上下文）。
    "declared_member_not_in_context",
    # 两侧都有这个成员，但身份字段不逐字相等（同一 member_ref 指的不是同一版材料）。
    "member_identity_mismatch",
)

#: 投给组织器的材料行**键序**（封闭）。身份字段在前、正文在后：读者先看到「这是哪一版材料」，
#: 再看到「它的正文是什么」。
_ORGANIZER_MATERIAL_ROW_KEYS = (
    "member_ref", "topic_id", "material_type", "source_identity", "locator_ref",
    "payload_hash", "reading_view_fingerprint", "content_qualification", "text", "structured")

#: `manifest` 成员与已解析材料**共有**的身份字段：两侧逐字相等才允许把正文投出去。
#: 这组字段的并集覆盖了「同一 member_ref 的同一版材料」的全部可核验身份（容器/材料 id、
#: 来源与出处身份、内容指纹、期间位次、载体类型、payload 哈希、读视图指纹）。
_ORGANIZER_MATERIAL_IDENTITY_FIELDS = (
    "pack_id", "material_id", "research_material_disposition_id", "source_identity",
    "provenance_identity", "material_content_fingerprint", "topic_id", "material_type",
    "payload_hash", "reading_view_fingerprint")

#: 资产自报的 (资产名, revision) 形态；与写入侧同一个正则口径（同一份资产纪律）。
_PROMPT_DECL_RE = re.compile(
    r"(?P<asset>[A-Za-z0-9_.\-]+)\s*[,，]\s*revision\s+(?P<revision>[A-Za-z0-9_.\-]+)")


def verify_organizer_prompt_asset(system: str) -> None:
    """核对「登记版本 = 实际加载到的资产」：自报身份 + 正文指纹两侧都必须一致。"""
    match = _PROMPT_DECL_RE.search(str(system or ""))
    if not match:
        raise NarrativeOrganizerError(
            f"prompt 资产 {NARRATIVE_ORGANIZER_PROMPT_ASSET!r} 未自报 (资产名, revision)，"
            "无法证明记录版本与实际加载版本一致")
    declared = f"{match.group('asset')}@{match.group('revision')}"
    if declared != NARRATIVE_ORGANIZER_PROMPT_VERSION:
        raise NarrativeOrganizerError(
            f"实际加载的 prompt 资产自报 {declared!r}，与登记版本 "
            f"{NARRATIVE_ORGANIZER_PROMPT_VERSION!r} 不一致（资产被换过：不得用旧记录冒充）")
    actual = PW.prompt_asset_fingerprint(system)
    if actual != NARRATIVE_ORGANIZER_PROMPT_SHA256:
        raise NarrativeOrganizerError(
            f"prompt 资产正文指纹 {actual!r} 与登记值 {NARRATIVE_ORGANIZER_PROMPT_SHA256!r} "
            f"不一致：资产 {NARRATIVE_ORGANIZER_PROMPT_ASSET!r} 的正文被改过")


def _jsonable(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _claim_citation_ids(claim: Any) -> tuple[str, ...]:
    """Claim 自己的 citation ID（派生，不由模型给；**唯一**口径见 `NS.claim_citation_ids`）。

    组织器与门后核验（`NS.verify_section_narrative` 第 6 条）必须共用这一个派生实现：两边各写
    一份「什么算合法引用」，两句之差正好是伪造引用的容身处。
    """
    return NS.claim_citation_ids(claim)


def parse_organizer_plan(text: Any) -> dict:
    """严格解析组织器的输出（未登记键、缺字段、类型不符一律拒）。"""
    if not isinstance(text, str) or not text.strip():
        raise NarrativeOrganizerError("正文组织器的输出为空：不得在无输出时凭空组织正文")
    raw = text.strip()
    # 容忍整段被 ``` 围栏包住（模型常见写法），但**不**容忍围栏之外的任何解释性文字：
    # 正文身份取自文本，多一段散文会让「解析出来的计划」不再等于「模型说的计划」。
    if raw.startswith("```"):
        first_newline = raw.find("\n")
        last_fence = raw.rfind("```")
        if first_newline < 0 or last_fence <= first_newline:
            raise NarrativeOrganizerError("正文组织器的输出围栏不完整，无法确定其 JSON 边界")
        raw = raw[first_newline + 1:last_fence].strip()
    try:
        plan = json.loads(raw)
    except ValueError as exc:
        raise NarrativeOrganizerError(
            f"正文组织器的输出不是合法 JSON：{exc}（不得从自由文本里猜结构）") from exc
    if not isinstance(plan, dict):
        raise NarrativeOrganizerError("正文组织器的输出必须是 JSON 对象")
    unknown = sorted(set(plan) - set(_PLAN_KEYS))
    if unknown:
        raise NarrativeOrganizerError(
            f"正文组织器的输出含未登记字段 {unknown}：只认 {list(_PLAN_KEYS)}")
    missing = sorted(set(_PLAN_KEYS) - set(plan))
    if missing:
        raise NarrativeOrganizerError(f"正文组织器的输出缺字段 {missing}")
    if not isinstance(plan["paragraphs"], list) or not plan["paragraphs"]:
        raise NarrativeOrganizerError("正文组织器的 paragraphs 必须是非空数组")
    if not isinstance(plan["claim_dispositions"], list):
        raise NarrativeOrganizerError("正文组织器的 claim_dispositions 必须是数组")
    for pos, paragraph in enumerate(plan["paragraphs"]):
        if not isinstance(paragraph, dict):
            raise NarrativeOrganizerError(f"paragraphs[{pos}] 必须是对象")
        p_unknown = sorted(set(paragraph) - set(_PARAGRAPH_KEYS))
        if p_unknown:
            raise NarrativeOrganizerError(
                f"paragraphs[{pos}] 含未登记字段 {p_unknown}：只认 {list(_PARAGRAPH_KEYS)}"
                "（topic 归属与 citation 由系统从你声明的 Claim 派生，不得自报）")
        if "sentences" not in paragraph:
            raise NarrativeOrganizerError(f"paragraphs[{pos}] 缺 sentences")
        sentences = paragraph["sentences"]
        if not isinstance(sentences, list) or not sentences:
            raise NarrativeOrganizerError(f"paragraphs[{pos}].sentences 必须是非空数组")
        for s_pos, sentence in enumerate(sentences):
            if not isinstance(sentence, dict):
                raise NarrativeOrganizerError(f"paragraphs[{pos}].sentences[{s_pos}] 必须是对象")
            s_unknown = sorted(set(sentence) - set(_ADDITIONAL_SENTENCE_KEYS))
            if s_unknown:
                raise NarrativeOrganizerError(
                    f"paragraphs[{pos}].sentences[{s_pos}] 含未登记字段 {s_unknown}："
                    f"只认 {list(_ADDITIONAL_SENTENCE_KEYS)}")
            if not str(sentence.get("text") or "").strip():
                raise NarrativeOrganizerError(
                    f"paragraphs[{pos}].sentences[{s_pos}].text 不得为空")
            claim_ids = sentence.get("claim_ids")
            if not isinstance(claim_ids, list) or not claim_ids:
                raise NarrativeOrganizerError(
                    f"paragraphs[{pos}].sentences[{s_pos}].claim_ids 必须是至少一条的数组"
                    "（没有 Claim 的句子就是无据陈述，context 不授权事实）")
            ctx = sentence.get("context_binding_ids", [])
            if ctx is None:
                ctx = []
            if not isinstance(ctx, list):
                raise NarrativeOrganizerError(
                    f"paragraphs[{pos}].sentences[{s_pos}].context_binding_ids 必须是数组")
    for pos, disp in enumerate(plan["claim_dispositions"]):
        if not isinstance(disp, dict):
            raise NarrativeOrganizerError(f"claim_dispositions[{pos}] 必须是对象")
        d_unknown = sorted(set(disp) - set(_DISPOSITION_KEYS))
        if d_unknown:
            raise NarrativeOrganizerError(
                f"claim_dispositions[{pos}] 含未登记字段 {d_unknown}："
                f"只认 {list(_DISPOSITION_KEYS)}")
        if not str(disp.get("claim_id") or ""):
            raise NarrativeOrganizerError(f"claim_dispositions[{pos}].claim_id 不得为空")
    return plan


#: 主题键取**材料自己的标题路径**里的第几段（1 基，`nrules-12` / `norg-4`）。
#:
#: 为什么是**确定性的**、又为什么取第 3 段：正文要按经营模式 / 产品与应用这类**业务主题**组织，
#: 而 `topic_id`（`company_business`）只有一个粒度，无法表达「这一段讲经营模式、那一段讲产品」。
#: 唯一可复算的主题来源是材料自己声明的标题树（`payload_ref.locator.section_path`），它是
#: **导航级**信息（同一棵树已经用在标题导航与 `WriterMaterialProcessingDisposition` 的可追溯性
#: 上），因此拿它当组织线索不需要新增任何 wire 字段、也不引入新证据。取第 3 段是因为年报的
#: 层级恰好是「第 N 节 / 一、二级标题 / 1、三级标题 /（1）四级标题」：第 3 段就是
#: 「1、主要业务」「2、主要产品及其用途」「3、经营模式」这一级，正好是要组织的那一级业务主题；
#: 更浅会退化成「第 N 节」（一个主题），更深会把同一主题按来源文档劈开（两份年报的四级标题
#: 编号与措辞不同）。
THEME_DEPTH = 3

#: 标题段里的**编号前缀**（`1、` / `（3）` / `三、` / `(2).`）。剥掉它，两份年报里同一主题的
#: 标题才归到同一个键（`（3）电池材料、回收及矿产资源` 与 `（4）电池材料和回收` 是两条不同的
#: 标题，它们的**词**不同、因此仍是两个键——这里只剥编号，不做任何同义归并：归并同义标题是
#: 语义判断，不是本层的职责）。
_HEADING_NUMBERING_RE = re.compile(
    r"^\s*[（(]?\s*[0-9０-９一二三四五六七八九十百]+\s*[）)、.．]?\s*")


def theme_key_from_section_path(section_path: str) -> str:
    """材料标题路径 → **确定性**主题键；取不到时返回空串（调用方据此退回 `topic_id`）。

    只做三件事：按标题树的分隔符切段（`" / "`，与 `harness.schema.section_path_text` 同形）、
    取第 `THEME_DEPTH` 段（不足则取最后一段）、剥掉编号前缀。不做同义归并、不做词频统计、
    不查任何词表——同一个标题路径在任何一次运行里都得到同一个键。
    """
    segments = [seg.strip() for seg in str(section_path or "").split(" / ") if seg.strip()]
    if not segments:
        return ""
    segment = segments[min(THEME_DEPTH, len(segments)) - 1]
    return _HEADING_NUMBERING_RE.sub("", segment).strip() or segment


def claim_theme_keys(*, claims: Sequence[Any],
                     accepted_bindings: Sequence[Any]) -> dict[str, str]:
    """逐条已定稿 Claim 的**主题键**（`claim_id → 键`）。

    键来源是这条 Claim 的 factual 已接受绑定所指向的**材料**的标题路径：同一个 Claim 可以绑定
    来自两份年报的两条材料，两条材料的标题路径可能落在不同的段（两份年报的章节编排不同），
    因此取**多数**（并列取声明序第一个）——它是「这条断语主要讲哪件事」的确定性答案，不是
    对内容的判断。一条绑定都解析不出标题时退回该 Claim 的 `topic_id`（旧行为，不改任何判据）。
    """
    by_id: dict[str, Any] = {}
    for binding in (accepted_bindings or ()):
        key = str(getattr(binding, "accepted_support_binding_id", "") or "")
        if key:
            by_id.setdefault(key, binding)
    keys: dict[str, str] = {}
    for claim in (claims or ()):
        claim_id = str(getattr(claim, "claim_id", "") or "")
        if not claim_id:
            continue
        counts: dict[str, int] = {}
        seen: list[str] = []
        for binding_id in (getattr(claim, "accepted_binding_ids", ()) or ()):
            binding = by_id.get(str(binding_id))
            if binding is None:
                continue
            payload = getattr(binding, "payload_ref", None)
            locator = payload.get("locator") if isinstance(payload, Mapping) else None
            section_path = str((locator or {}).get("section_path") or "")
            theme = theme_key_from_section_path(section_path)
            if not theme:
                continue
            if theme not in counts:
                seen.append(theme)
                counts[theme] = 0
            counts[theme] += 1
        if counts:
            best = max(counts.values())
            keys[claim_id] = next(theme for theme in seen if counts[theme] == best)
        else:
            keys[claim_id] = str(getattr(claim, "topic_id", "") or "")
    return keys


#: `norg-7` 输入面 `prose_draft` 的封闭键集（**输入**面：登记它们是为了「同一份台账在两个面
#: 上各有各的写法」这类漂移不会发生，不是为了解析模型输出——那个方向由 `_SENTENCE_KEYS` 管）。
_PROSE_UNIT_KEYS = ("prose_unit_id", "text", "material_member_refs", "atoms")
_PROSE_ATOM_KEYS = ("atom_candidate_id", "atom_text", "status", "claim_id")
#: 原子的两种状态（封闭）。`kept` = 这条原子对应的候选**过了两道门**、有 `claim_id`；
#: `dropped` = 没过门（没有 `claim_id`），草稿文本里讲它的分句必须被删掉或改写掉。
PROSE_ATOM_KEPT = "kept"
PROSE_ATOM_DROPPED = "dropped"


def _ordered_prose_row(row: Mapping[str, Any], keys: Sequence[str]) -> dict:
    """按**封闭键集**重排一行（缺键即抛）：台账的键集是声明过的，行是就地拼的——不按声明键集
    过一遍，两处就会各自漂移（多一个键没人拒、少一个键没人发现），而输入面的形状是资产里写死的。
    """
    missing = [key for key in keys if key not in row]
    if missing:
        raise NarrativeOrganizerError(
            f"草稿台账行缺声明键 {missing}：输入面形状只能由 {list(keys)} 决定")
    return {key: row[key] for key in keys}


def prose_draft_ledger(*, draft: Any, claims: Sequence[Any], tabled: Sequence[str] = (),
                       ) -> tuple[list[dict], dict]:
    """门前自然草稿 → 组织器输入面的**原子台账**（`norg-7`），返回 `(payload, 读数)`。

    **为什么是台账而不是整段草稿原文。** 草稿文本是模型写的、它是**表达基础**；但草稿里声明的
    事实原子是逐条送过门前两道门的，其中没过门的那些**没有任何来源支撑**。只把草稿原文投过去，
    组织器（也是模型）会照着原文把没过门的断言一并写回来——那等于用一次自然改写把两道门的
    结论洗掉。因此每一段草稿连同它自己的原子台账一起投：哪几条原子过了门（`kept`，带
    `claim_id`）、哪几条没过（`dropped`，没有 `claim_id`，必须在改写时删掉）。

    **原子全部没过门的草稿不进 payload**（读数里单独计数）：它在正文里没有任何可表达的断言，
    投过去只会诱使模型把它写回来。**由门后表格承载的 Claim 不算 `kept`**（读数里单列
    `atoms_kept_but_tabled`）：那些事实的呈现位置是表格行，正文句子再绑一次就是重复陈列。

    **这不是授权面。** 台账只声明「这段草稿表达的是哪些已经过门的原子」，它不授权任何字面
    成分：`kept` 原子的 `claim_id` 指的是一条**已定稿 Claim**，句子文本仍要过门（`ng-14` 的
    保真核对 + 第 2/3 条表面核验）。`material_member_refs` 是出处标注，也不是授权。

    读数进 trace（`prose_draft`），因此「这一轮到底有没有草稿层可用」在产物里可查——**零草稿
    层**与「草稿层存在但原子全没过门」是两件事，读数必须分开（把后者读成前者会掩盖一次真实的
    内容代价）。
    """
    claims_by_candidate: dict[str, Any] = {}
    for claim in (claims or ()):
        candidate_id = str(getattr(claim, "claim_candidate_id", "") or "")
        if candidate_id:
            claims_by_candidate.setdefault(candidate_id, claim)
    tabled_ids = {str(c) for c in (tabled or ())}
    units = tuple(getattr(draft, "natural_prose_draft", ()) or ())
    payload: list[dict] = []
    readings = {"prose_units_total": len(units), "prose_units_offered": 0,
                "prose_units_without_kept_atom": 0, "atoms_total": 0,
                "atoms_kept": 0, "atoms_dropped": 0, "atoms_kept_but_tabled": 0}
    for unit in units:
        atoms_payload: list[dict] = []
        kept_ids: list[str] = []
        for candidate_id in tuple(getattr(unit, "atom_candidate_ids", ()) or ()):
            candidate_id = str(candidate_id)
            claim = claims_by_candidate.get(candidate_id)
            claim_id = str(getattr(claim, "claim_id", "") or "") if claim is not None else ""
            readings["atoms_total"] += 1
            if claim_id and claim_id not in tabled_ids:
                readings["atoms_kept"] += 1
                if claim_id not in kept_ids:
                    kept_ids.append(claim_id)
                atoms_payload.append({"atom_candidate_id": candidate_id,
                                      "atom_text": str(getattr(claim, "text", "") or ""),
                                      "status": PROSE_ATOM_KEPT, "claim_id": claim_id})
                continue
            if claim_id:
                readings["atoms_kept_but_tabled"] += 1
            readings["atoms_dropped"] += 1
            atoms_payload.append({"atom_candidate_id": candidate_id, "atom_text": "",
                                  "status": PROSE_ATOM_DROPPED, "claim_id": None})
        if not kept_ids:
            readings["prose_units_without_kept_atom"] += 1
            continue
        readings["prose_units_offered"] += 1
        payload.append(_ordered_prose_row({
            "prose_unit_id": str(getattr(unit, "prose_unit_id", "") or ""),
            "text": str(getattr(unit, "text", "") or ""),
            "material_member_refs": [str(r) for r in tuple(
                getattr(unit, "source_member_refs", ()) or ())],
            "atoms": [_ordered_prose_row(atom, _PROSE_ATOM_KEYS) for atom in atoms_payload],
        }, _PROSE_UNIT_KEYS))
    return payload, readings


def prose_ledger_claim_ids(payload: Sequence[Mapping[str, Any]]) -> dict[str, tuple[str, ...]]:
    """台账 → `prose_unit_id → 这一段草稿里 `kept` 原子所对应的 claim_id`（**唯一**读取口径）。

    `_sentence_specs` 的句级来源闭合与 `prose_draft_ledger` 的构造必须读**同一份**口径，
    否则「什么算这一段草稿的 Claim」会出现两个答案——而两个答案之差正好是「借一段草稿之名
    去声明别的 Claim」的容身处。
    """
    out: dict[str, tuple[str, ...]] = {}
    for row in (payload or ()):
        unit_id = str(row.get("prose_unit_id") or "")
        if not unit_id:
            continue
        kept: list[str] = []
        for atom in (row.get("atoms") or ()):
            if str(atom.get("status") or "") != PROSE_ATOM_KEPT:
                continue
            claim_id = str(atom.get("claim_id") or "")
            if claim_id and claim_id not in kept:
                kept.append(claim_id)
        if unit_id not in out or kept:
            out[unit_id] = tuple(kept)
    return out


def organizer_material_context(*, prose_payload: Sequence[Mapping[str, Any]], manifest: Any,
                               material_context: Any) -> tuple[list[dict], dict]:
    """门前草稿台账**点名的**成员 → 组织器可读的**只读**材料正文行（`orgmc-1`）。

    **为什么需要这一面。** `norg-7` 的输入面给了组织器两样东西——草稿文本（表达基础）与逐原子
    的 `kept`/`dropped` 台账（哪几条断言过了门）——但**没有材料原文**。于是「照材料原文的用词
    改写这一段草稿」这件事在模型那一侧不可执行：它只能拿草稿自己的措辞，或者退回去把 Claim
    文本一条条重新拼一遍（那正是写法一要拦的东西）。本函数补的就是这一面。

    **为什么是「点名的成员」而不是整份清单。** `prose_payload` 的每一行带
    `material_member_refs`（那一段草稿自己声明的出处）。只有这些成员与本次改写有关：投整份
    清单会把本节所有材料的正文（46 条）一起送进去，其中绝大多数与这一段草稿无关，而输入面
    越大、「照着别处的材料另写一句」的诱因越强。这一条同时是**有界性**：投出去的行数 <= 台账
    点名过的成员数。

    **为什么必须逐成员核身份（「同版本」在这里的确切含义）。** 只把 `reading_view` 投出去是不够
    的：同一 `member_ref`（= 容器 id + 材料 id）在两版材料里可以指向不同的正文，而本函数手里的
    正文来自 `wmctx-1` 上下文、出处来自 draft 的 `wmm-2` 清单，是**两份独立的对象**。因此每个
    点名成员在两侧都要在，且 `_ORGANIZER_MATERIAL_IDENTITY_FIELDS` 逐字相等——否则投出去的正文
    就不是清单声明的那一版材料，模型会照着一份**没有**经过本届两道门的材料写句子。任何一项对不上
    即 fail-closed（`member_identity_mismatch`），不降级、不跳过这一条。

    **这不是授权面。** 行里的正文是**只读**的：它不授权任何字面成分，句子仍要过 `ng-14` 的
    保真核对与第 1/2/3 条表面核验（表面只认**这一句声明的 Claim 文本**）。给出材料正文的收益
    是**表达**（用词、语序、衔接贴近原文），不是**事实**。正因如此，本函数**不**把材料正文折成
    任何可被引用的对象：行里的 `member_ref` 只在输入面出现，输出面的句子仍只能声明
    `prose_unit_id`（其出处由 `prose_draft` 那一行携带）。

    **财务（纯 `FinancialFactPack`）天然走空集。** 财务节的草稿单元声明的是权威事实出处
    （`source_fact_refs`），`material_member_refs` 为空 ⇒ 点名的成员为空 ⇒ 本函数返回空表，
    **不**为凑出非空输入而伪造 `ResearchMaterial`。空表本身就是正确读数。

    读数进 trace（`organizer_material_context`），因此「这一轮到底投了几行材料正文」在产物里
    可查；`identity_checks` 单列逐成员对账的次数（空表时为 0，不是「查过了没查」）。
    """
    payload = tuple(prose_payload or ())
    ordered: list[str] = []
    for row in payload:
        for ref in tuple(row.get("material_member_refs") or ()):
            ref = str(ref or "")
            if ref and ref not in ordered:
                ordered.append(ref)
    readings = {"policy_version": ORGANIZER_MATERIAL_CONTEXT_VERSION,
                "requested_members": len(ordered), "rows": 0, "identity_checks": 0}
    if not ordered:
        return [], readings

    entries = getattr(manifest, "entries", None)
    if entries is None:
        raise NarrativeOrganizerError(
            f"orgmc-1:{ORGANIZER_MATERIAL_CONTEXT_REASONS[0]} —— 逐段草稿点名了 "
            f"{len(ordered)} 个材料成员，但本节没有 wmm-2 材料清单（manifest.entries 读不到）："
            "不核身份就把正文投出去，等于让改写依据与清单声明分叉，因此 fail-closed")
    by_ref: dict[str, Any] = {}
    for entry in tuple(entries):
        by_ref[str(getattr(entry, "member_ref", "") or "")] = entry
    if material_context is None:
        raise NarrativeOrganizerError(
            f"orgmc-1:{ORGANIZER_MATERIAL_CONTEXT_REASONS[1]} —— 草稿点名了 "
            f"{len(ordered)} 个材料成员，但没有 wmctx-1 材料正文上下文："
            "不得把只有 ID 的出处当成「模型看过材料正文」")
    resolved: dict[str, Any] = {}
    for material in tuple(getattr(material_context, "materials", ()) or ()):
        resolved[str(getattr(material, "member_ref", "") or "")] = material

    rows: list[dict] = []
    for ref in ordered:
        entry = by_ref.get(ref)
        if entry is None:
            raise NarrativeOrganizerError(
                f"orgmc-1:{ORGANIZER_MATERIAL_CONTEXT_REASONS[0]} —— 草稿点名的成员 {ref!r} "
                f"不在本节 wmm-2 材料清单里（清单共 {len(by_ref)} 个成员）："
                "草稿的出处标注必须能在清单里逐字对上，否则它是在引用一份本节没有的材料")
        material = resolved.get(ref)
        if material is None:
            raise NarrativeOrganizerError(
                f"orgmc-1:{ORGANIZER_MATERIAL_CONTEXT_REASONS[1]} —— 成员 {ref!r} 在材料清单里，"
                "但已解析正文上下文里没有它的正文（清单与上下文必须是同一成员集合）")
        mismatched = [name for name in _ORGANIZER_MATERIAL_IDENTITY_FIELDS
                      if getattr(entry, name, None) != getattr(material, name, None)]
        readings["identity_checks"] += 1
        if mismatched:
            raise NarrativeOrganizerError(
                f"orgmc-1:{ORGANIZER_MATERIAL_CONTEXT_REASONS[2]} —— 成员 {ref!r} 在清单与已解析"
                f"正文里的身份字段不一致：{sorted(mismatched)}（同一 member_ref 指的不是同一版"
                "材料）。投出去的正文必须就是清单声明的那一版，否则改写依据与两道门看过的材料"
                "不是同一份，因此 fail-closed")
        text, structured, qualification = NS.material_reading_view(material)
        rows.append(_ordered_prose_row({
            "member_ref": ref,
            "topic_id": str(getattr(entry, "topic_id", "") or ""),
            "material_type": str(getattr(entry, "material_type", "") or ""),
            "source_identity": str(getattr(entry, "source_identity", "") or ""),
            "locator_ref": dict(getattr(entry, "locator_ref", None) or {}),
            "payload_hash": str(getattr(entry, "payload_hash", "") or ""),
            "reading_view_fingerprint": str(
                getattr(entry, "reading_view_fingerprint", "") or ""),
            "content_qualification": qualification,
            "text": text,
            "structured": structured,
        }, _ORGANIZER_MATERIAL_ROW_KEYS))
    readings["rows"] = len(rows)
    return rows, readings


def build_organizer_messages(*, claims: Sequence[Any], accepted_context_bindings: Sequence[Any],
                             unresolved: Sequence[Any], writing_spec: Any,
                             presentation_profile: Any, identity: Mapping[str, str],
                             theme_keys: Mapping[str, str] | None = None,
                             prose_draft: Sequence[Mapping[str, Any]] = (),
                             material_context_rows: Sequence[Mapping[str, Any]] = (),
                             ) -> list[dict[str, str]]:
    """构造**唯一**一次组织调用的输入面（已定稿对象 + 规格 + 门前草稿台账）。

    `theme_keys`（`claim_id → 主题键`）由 `claim_theme_keys` 从**材料自己的标题路径**确定性地
    派生，它只是**组织线索**（哪几条断语讲的是同一件业务），不是事实、也不是证据：正文里一个字
    都不会因为它而变化（正文仍必须逐字由声明 Claim 构成）。缺省 `None` 时按 `topic_id` 兜底，
    因此旧调用方（测试夹具）的行为一字不变。

    `norg-5` 起多投一份 `redundancy_candidates`（`NS.redundancy_candidate_pairs`）：机械的字符
    相似度**候选**清单。它与 `theme_key` 同一性质——都是**线索**，不是事实、不是判定、也不改
    任何 wire 字段；组织器对每一对给出处置的载体就是既有的 `claim_dispositions`（每条 Claim 恰好
    一次），因此 `narr-6` 不随它前进。

    `norg-7` 起多投一份 `prose_draft`（`prose_draft_ledger` 的产物）：门前自然草稿 + 逐原子
    的 `kept`/`dropped` 台账。缺省空元组 = 本节没有可用的草稿层（`narr-6` 及更早、或草稿原子
    全没过门）：此时只有逐字写法可用，输入面的其余字段与 `norg-6` 逐字相同。

    `norg-8` 起多投一份 `material_context`（`organizer_material_context` 的产物，`orgmc-1`）：
    **`prose_draft` 每一行点名的那些成员**的材料正文行（出处身份 + exact 定位 + 正文 + 形态）。
    它与 `theme_key` / `redundancy_candidates` 同一性质地**不是证据**——它是**只读的依据面**：
    让写法二能照原文的用词与语序改写，而不是另编一句。缺省空元组 = 本轮没有材料正文可投
    （财务节的草稿单元声明的是权威事实出处、或本节根本没有草稿层）：此时写法二仍只有草稿文本
    可用，除多出一个恒在场的 `material_context.rows: []` 外，输入面与 `norg-7` 逐字相同。
    """
    theme = dict(theme_keys or {})
    claims_payload = [
        {"claim_id": str(getattr(c, "claim_id", "") or ""),
         "topic_id": str(getattr(c, "topic_id", "") or ""),
         "theme_key": theme.get(str(getattr(c, "claim_id", "") or ""),
                                str(getattr(c, "topic_id", "") or "")),
         "text": str(getattr(c, "text", "") or ""),
         "claim_type": str(getattr(c, "claim_type", "") or ""),
         "citation_ids": list(_claim_citation_ids(c))}
        for c in claims]
    theme_order: list[str] = []
    for row in claims_payload:
        if row["theme_key"] and row["theme_key"] not in theme_order:
            theme_order.append(row["theme_key"])
    context_payload = [
        {"context_binding_id": str(getattr(b, "accepted_support_binding_id", "") or ""),
         "material_id": str(getattr(b, "material_id", "") or ""),
         "authority_container_id": str(getattr(b, "authority_container_id", "") or ""),
         "support_role": str(getattr(b, "support_role", "") or "")}
        for b in (accepted_context_bindings or ())]
    unresolved_payload = [
        {"unresolved_id": str(getattr(u, "unresolved_id", "") or ""),
         "topic_id": str(getattr(u, "topic_id", "") or ""),
         "state": str(getattr(u, "state", "") or ""),
         "reason_code": str(getattr(u, "reason_code", "") or ""),
         "detail": str(getattr(u, "detail", "") or "")}
        for u in (unresolved or ())]
    spec_payload: dict = {}
    for name in ("writing_spec_id", "schema_version", "narrative_style", "tone",
                 "max_sentences_per_paragraph", "forbidden_phrases"):
        if hasattr(writing_spec, name):
            spec_payload[name] = _jsonable(getattr(writing_spec, name))
    # §三 A / 3.2：与门前写作器共用**同一份**呈现视图（唯一实现在 `PP.presentation_payload`）。
    # 本模块此前自带一份字段名表，且与 pack_writer 那份一样取的是不存在的字段名，于是这里
    # 投给门后组织器的 `presentation_profile` 也恒为 `{"schema_version": ...}`——同一份规格、
    # 两个投影、都投影成空。分开写必然漂移，所以只留一份。
    profile_payload = _jsonable(PP.presentation_payload(presentation_profile))
    # `norg-5`：近义重复**候选**（唯一实现 `NS.redundancy_candidate_pairs`）。它只做机械的字符
    # 相似度筛查，**不做判定**——判定「说的是不是同一件事」是组织器（模型）的职责，系统替它下这个
    # 结论就等于替它写正文。因此这里投的是**线索**，不是「该合并」的指令：组织器可以对每一对
    # 判「是同一件事」（保留更全的一条）或「不是」（两条都保留），两种都不违反任何判据。
    # 不投这份线索的代价是实打实的：r7b 正文里有 3 对 >= 0.80 的近义 Claim 被原样写了两遍。
    redundancy_payload = [
        {"claim_id_a": cid_a, "claim_id_b": cid_b, "score": score}
        for cid_a, cid_b, score in NS.redundancy_candidate_pairs(claims)]
    # `norg-7`：门前自然草稿的**原子台账**（唯一构造口径 `prose_draft_ledger`，调用方已算好）。
    # 只投 `kept` 原子至少一条的草稿段；`dropped` 原子照投但**没有 claim_id**——它是「这一段里
    # 有哪几条断言没过门」的账，组织器据此把讲那些断言的分句删掉。
    prose_payload = [dict(row) for row in (prose_draft or ())]
    # `norg-8` / `orgmc-1`：材料正文行**只读**投出（唯一构造口径 `organizer_material_context`，
    # 调用方已算好并核过身份）。恒在场、可以为空数组——「本轮没有材料正文」本身是一条读数。
    material_payload = {
        "policy_version": ORGANIZER_MATERIAL_CONTEXT_VERSION,
        "readonly": True,
        "rows": [dict(row) for row in (material_context_rows or ())],
    }
    user = json.dumps({
        "identity": {"task_id": identity.get("task_id", ""),
                     "section_id": identity.get("section_id", ""),
                     "section_draft_id": identity.get("section_draft_id", ""),
                     "draft_revision": identity.get("draft_revision", "")},
        "claims": claims_payload,
        "theme_order": theme_order,
        "redundancy_candidates": redundancy_payload,
        "prose_draft": prose_payload,
        "material_context": material_payload,
        "accepted_context_bindings": context_payload,
        "unresolved": unresolved_payload,
        "writing_spec": spec_payload,
        "presentation_profile": profile_payload,
        "output_schema": {
            "paragraphs": [{"sentences": [{
                "text": "<这一句的正文>",
                "claim_ids": ["<输入 claims 里某一行的 claim_id>"],
                "context_binding_ids": ["<accepted_context_bindings 里某一行的 id>"],
                "prose_unit_id": "<可选：prose_draft 里某一行的 prose_unit_id>"}]}],
            "claim_dispositions": [{"claim_id": "<输入 claims 里某一行的 claim_id>",
                                    "disposition": "selected", "reason_code": None}],
        },
        "rules": [
            "你只组织，不新增：正文里的每个数字 / 金额 / 比例 / 日期 / 期间 / 币种 / 法人主体名 / "
            "显式否定 / 勾选与适用状态 / 表格行列关系，都必须**逐字**出现在你这一句绑定的 "
            "claim_ids 对应的文本里。",
            "不得新增因果、推断或结论（「因此」「说明」「表明」等把并列升级为推理的表达），"
            "也不得放大或收窄任何断言的强度、范围、期间或口径。",
            "claim_ids 只能取自输入 claims 的 claim_id；不得引用输入里没有的对象。",
            "每个句子至少绑定一条 claim_id；context 支撑只作背景与衔接，不授权事实。",
            "**按 theme_key 分主题组织**：同一个 theme_key 的 Claim 组织在同一段（或相邻且连续"
            "的几段）里，段落顺序按 theme_order；不要把两个主题的 Claim 混进同一句，也不要把同一"
            "个主题的 Claim 拆到互不相邻的段落。theme_key 只是**组织线索**（材料自己标题路径里的"
            "那一级标题）：它不进正文，也不授权任何事实。",
            "**多条 Claim 组成一句时，两条 Claim 文本之间必须先落一个逗号类标点**，连接语只能"
            "写在它后面：`A，此外，B` 合法，`A此外，B` 会被门判为病句（读者读到的是"
            "「…研发、生产、销售此外，公司产品…」）。连接语本身不得编造事实。",
            "同一段里若两条 Claim 逐字或近义地断言**同一件事**（只是措辞或来源不同），保留信息"
            "更全的那一条，另一条记 `omitted / redundant_with_selected_claim`；只要有一项内容"
            "不同，两条都必须保留。**不得**因为「写不下」而丢弃一条独有的事实。",
            "`redundancy_candidates` 是系统用**字符相似度**筛出的**候选**，不是判定，也不是"
            "「该合并」的指令：相似度不知道两条说的是不是同一件事。你必须对清单里的**每一对**"
            "给出明确处置，处置的载体就是这两条各自在 claim_dispositions 里的去向——真同一原子"
            "断言就保留信息更全的一条、另一条记 omitted/redundant_with_selected_claim；只差"
            "任何一项内容（不同产品 / 期间 / 口径 / 范围 / 数值）就是两件事，两条都必须保留；"
            "两条都由本节表格承载就都记 omitted/presented_as_table_row（不要为了「回应清单」"
            "把本该进表格的 Claim 改写成正文句子）。高相似度不等于重复（例如同一词干下的交流侧"
            "系统与直流侧系统），但也不得为了段落短而把一条独有事实记为 redundant。",
            "**衔接语只能并列，不能断言关系**：`另一方面` / `在此基础上` / `其中` / `综上` 分别"
            "断言两条断言之间的对照 / 递进 / 从属 / 归纳关系，材料没做过这个判断，写出来就是替"
            "材料下结论。只有该连接语**逐字出现在你这一句声明的 Claim 文本里**才可用；其余一律"
            "改用中性并列 `此外` / `同时`（它们只表示「还有这一条」，不改变任何断言的含义）。",
            "**发行人自述必须标明出处**：Claim 文本里若含评价语（一流 / 核心优势 / 前瞻性 / "
            "领先 / 卓越 / 优质 等），它逐字就在 Claim 里，所以「不得新增事实表面」必然放行——"
            "但它是**发行人自己说的**。你必须在**该条 Claim 紧前的组织语位置**写出一个归属语，"
            "取值只能来自：据公司自身表述 / 据公司披露 / 公司自述 / 公司披露 / 公司称 / "
            "年度报告披露 / 年报披露。归属语不引入任何数字、期间、主体或结论，因此你可以自行"
            "写出（与 `此外` / `同时` 同权）。在一句里给别处的 Claim 写了归属，不能说明这一条。",
            "claim_dispositions 必须**恰好一次**覆盖输入的每一条 Claim；omitted 只能取封闭码 "
            "outside_section_topic / redundant_with_selected_claim / presented_as_table_row。",
            "**两种句子写法只能选一种**。写法一：句子里**不写** `prose_unit_id`，正文全部照旧"
            "逐字由本句 claim_ids 的 Claim 文本构成。写法二：句子声明 `prose_unit_id`（取自"
            "输入 prose_draft 里某一行的 `prose_unit_id`），此时**输入 prose_draft 里那一行的 "
            "`text` 才是这一句的表达基础**——自然行文、语序、衔接都可以沿用它，事实内容的取舍"
            "由 `atoms` 决定：`status = kept` 的原子（带 `claim_id`）可以保留，其表述按对应"
            "Claim 的文本写；`status = dropped` 的原子（`claim_id` 为 null）**没有来源支撑**，"
            "草稿里讲它的分句必须删掉或改写到不再断言它。写法二的 `claim_ids` 必须**落在**所"
            "声明那一行的 kept 原子集合之内（不得借这一行的名去声明别的 Claim）；`prose_draft` "
            "为空时只有写法一可用。",
            "**写法二同样不得新增**：草稿里的事实表面若在本句声明的 Claim 里没有对应物，一律"
            "删除——尤其是**时点**（「目前」「截至报告期末」「2025 年」）：草稿写了、而本句声明的"
            "Claim 文本里没有该时点，那个时点必须删掉；草稿的句尾归属语、评价语同理，只在声明的"
            "Claim 里逐字存在时才可保留。`material_member_refs` 只是出处标注，**不是授权**：它"
            "不能用来放行草稿里的任何字面。",
            "**`material_context.rows` 是只读的依据面，同样不是授权**：每一行是本节材料清单里"
            "**草稿点名过的**成员的材料原文（含来源身份、exact 定位、正文与形态），用途只有一个——"
            "写法二改写时**照原文的用词与语序**写，而不是另编一句，让正文读起来像报告里的原话。"
            "它不授权任何字面：句子里的每个事实表面仍必须逐字来自你这一句声明的 claim_ids，"
            "**材料正文里有、而声明的 Claim 文本里没有**的数字 / 金额 / 比例 / 日期 / 期间 / 币种 / "
            "主体名 / 否定 / 勾选状态 / 表格关系，一律不得写进句子。行是只读的：只可按在输入里"
            "逐字出现的 `member_ref` 理解它们，不得把它们当作可引用的对象写进输出，也不得引用"
            "`rows` 之外的成员。`rows` 为空数组时本节没有可投的材料正文（例如本节材料清单为空）——"
            "此时写法二只有草稿文本本身可用。",
            "不得输出定稿对象或决定字段（SectionClaim / citation / accepted binding / "
            "SectionResult / decision / evaluation）。",
            "禁止检索、禁止联网、禁止做任何计算：数值一律逐字沿用 Claim 的写法。",
        ],
    }, ensure_ascii=False)
    return [{"role": "user", "content": user}]


def _sentence_specs(plan: Mapping[str, Any], *, known_claims: Mapping[str, Any],
                    prose_ledger: Mapping[str, tuple[str, ...]] | None = None) -> list[dict]:
    """把解析后的计划折成 `NarrativeParagraph.create` 的句规格（citation 由系统派生）。

    句类只认系统内部键 `REPAIRED_SENTENCE_KIND_KEY`（由 ⑥ 的补救写入）：模型自己在 `sentence_kind`
    里自称什么都不算数，一律按 `composed` 组装——否则模型只要自称 factual 就能躲开第 8 条。补救写
    的 `composed` 段与模型原本的句子**同一口径**，仍要过第 8 条。

    `norg-7` 的第二种句类 `natural` 同样**由系统判定**，判据只有一个：这一句是否声明了
    `prose_unit_id`（且该 id 在本次输入面的台账里）。模型不能自称 `natural`，也不能靠写一个
    不存在的 id 换取它——那两条路都会在这里抛 `NarrativeOrganizerError`。

    写法二的**句级来源闭合**（读唯一口径 `prose_ledger_claim_ids`）：声明的 Claim 必须是那一行
    草稿 `kept` 原子之内的。少了这一条，「借一段草稿之名声明任意 Claim」就是合法输入——组织器
    可以拿一段讲生产的草稿去声明一条讲销售的数字断言，而每一道下游判据看到的都是一条「有草稿
    出处」的自然句。
    """
    ledger = dict(prose_ledger or {})
    specs: list[dict] = []
    for s_pos, sentence in enumerate(plan.get("sentences") or ()):
        claim_ids = tuple(dict.fromkeys(str(c) for c in sentence.get("claim_ids") or ()))
        unknown = [cid for cid in claim_ids if cid not in known_claims]
        if unknown:
            raise NarrativeOrganizerError(
                f"句子 #{s_pos} 引用了本次定稿集之外的 Claim：{unknown[:6]}"
                "（引用必须是**当前**的已定稿 Claim，不得引用上一版或输入里没有的对象）")
        kind = str(sentence.get(REPAIRED_SENTENCE_KIND_KEY) or "composed")
        if kind not in NS.SENTENCE_KINDS:
            raise NarrativeOrganizerError(
                f"句子 #{s_pos} 的句类 {kind!r} 不在 {list(NS.SENTENCE_KINDS)} 内"
                "（句类只能由系统标注，不得自报）")
        prose_unit_id = str(sentence.get("prose_unit_id") or "").strip()
        if prose_unit_id and sentence.get(REPAIRED_SENTENCE_KIND_KEY) is not None:
            raise NarrativeOrganizerError(
                f"句子 #{s_pos} 同时声明了 prose_unit_id 与系统句类：系统补救产出的句子不接受"
                "草稿出处声明（补救过的文本不再是草稿里那一句，挂上出处就是描述一件没发生过的事）")
        if prose_unit_id:
            if not claim_ids:
                raise NarrativeOrganizerError(
                    f"句子 #{s_pos} 声明了草稿出处却没有绑定任何 Claim：自然句承载事实，"
                    "必须逐条绑定它保留的已定稿 Claim（空绑定的句子没有事实来源）")
            if prose_unit_id not in ledger:
                raise NarrativeOrganizerError(
                    f"句子 #{s_pos} 声明了本次输入面里没有的 prose_unit_id {prose_unit_id!r}："
                    "草稿出处必须指向本次投给组织器的草稿行，不得自造")
            allowed = ledger[prose_unit_id]
            stray = [cid for cid in claim_ids if cid not in set(allowed)]
            if stray:
                raise NarrativeOrganizerError(
                    f"句子 #{s_pos} 借草稿行 {prose_unit_id!r} 声明了那一行**没有**的 Claim："
                    f"{stray[:6]}（写法二的 claim_ids 必须落在所声明草稿行的 kept 原子之内，"
                    "否则草稿出处就成了任意 Claim 的通行证）")
            kind = "natural"
        citations: list[str] = []
        for cid in claim_ids:
            for citation in _claim_citation_ids(known_claims[cid]):
                if citation not in citations:
                    citations.append(citation)
        specs.append({
            "text": str(sentence.get("text") or ""),
            "sentence_kind": kind,
            "claim_ids": claim_ids,
            "citation_ids": tuple(citations),
            "context_binding_ids": tuple(
                dict.fromkeys(str(c) for c in sentence.get("context_binding_ids") or ())),
        })
    return specs


def _repaired_specs(*, sentence: Mapping[str, Any], known_claims: Mapping[str, Any]
                    ) -> "tuple[list[dict], dict | None]":
    """对**一条**句计划做有界定向重组织（⑥）：返回 `(新句计划, 补救记录或 None)`。

    只在第 8 条的**机械缺陷**上动作（判据的唯一实现在 `NS.composed_organization_defect`），
    其余一律**不动**——尤其是：

    * `claims_not_present_in_order`（声明的 Claim 文本没按序出现在正文里）：**不修**。补救不能
      把一条没出现在正文里的 Claim 变出来，那正是「绑定与正文各说各话」，只能 fail-closed。
    * `unseparated_seam`（接缝没有分隔标点起头，`nrules-12`）：**不修**。唯一能修它的动作是往接
      缝里**补一个逗号**——那是系统替模型改正文，与第二级的理由完全一样（改写过的正文不能再被
      当成「模型自己写的那句话」）。它的正确去向是 fail-closed，模型据此被告知（`norg-4`）要写
      `A，此外，B` 而不是 `A此外，B`。
    * 第 2/3/9 条（高风险表面、系统自述）：**不在这里判、也不在这里修**。补救只修**组织形态**；
      补救后的正文仍要整份过 `NS.verify_section_narrative`，衔接区里自造的主体名 / 数字 / 因果 /
      「本系统已联网核验」照样被那一遍拦住。
    * `norg-7` 写法二（声明了 `prose_unit_id`）的句子：**整句跳过**。这一级的两级补救都以
      「Claim 文本必须逐字出现在正文里」为前提，而写法二正当的写法**就是**不逐字——送进来只会
      被逐句判成 `claims_not_present_in_order` 然后 fail-closed，把一节本来合格的正文清零。自然句
      的对应判据在门后（`ng-14` 第 8 条的自然分支），不在这里，也不能由这里代判。

    两级阶梯（都不发 LLM 调用、都不改任何预算，一次遍历，逐句最多补救一次）：

    1. **保留原文**（`split_at_sentence_terminators`）：按模型自己的句中句末标点切段并逐段重绑
       Claim。切出来的每段都逐字是模型写的字，模型的衔接语跟着它原本相邻的那段留下。切不出
       （有条 Claim 自己含句末标点、或某段没有 Claim、或切完仍有段不合法）就落第二级。
    2. **各自成句**（`per_claim_factual_sentence`）：每条 Claim 各自渲染成一条逐字事实句
       （`NS.render_factual_text` 的唯一口径）。被丢弃的只有纯标点的接缝——**没有**任何组织语被
       系统补写（用固定连接语词表替模型补组织语，等于系统替模型写正文，那是「让门机械变绿」，
       不是补救）。它**只对「Claim 文本 + 纯标点接缝」的句子生效**（判据唯一实现在
       `NS.is_punctuation_only_join`）：接缝上若有模型自己写的组织语（`A，同时，B`）或自造表面
       （`A，42%，B`），这一级**禁止**——丢掉那一段会把「模型自造了一个数字」洗成一份不含该数字
       的合法正文；拒掉的版本没被修好，只是被洗掉了。这两种情形都 fail-closed。

    两级都**不承接**原句声明的 context 绑定：补救后的文本不再是模型自己写的那一句话，把「本句用了
    哪一处背景材料」挂在系统改写的文本上，会让那个声明描述一件没有发生过的事。context 只作背景与
    衔接、不授权任何事实，因此丢掉的只是「这一句用过它」这一条声明（节级已接受全集不受影响），
    而 `factual` 句本来就不允许携带 context（第 4 条），不丢也会被拒。

    citation **不在这里派生**：补出来的规格只给 text / 句类 / claim_ids，引用仍由
    `_claim_citation_ids` 这一个口径在 `_sentence_specs` 里派生。理由：两条腿各派一份「什么算合法
    引用」，两句之差正好是伪造引用的容身处。若某条 Claim 派生不出引用（原本就建不出合法句子），
    补救不承接该句——留给核验按原样拒，而不是先改写成一条注定不合法的句子。
    """
    if str(sentence.get("prose_unit_id") or "").strip():
        # `norg-7` 写法二的句子**不进退化阶梯**：这一级的两个补救动作都以「Claim 文本必须逐字
        # 出现在正文里」为判据（第 8 条），而写法二正当的做法恰恰是**不逐字**——照旧送进来，每
        # 一条自然句都会被判成 `claims_not_present_in_order`，然后 fail-closed 把整节清零。自然句
        # 的相应判据是门后的**末句保真核对**（`ng-14` 第 8 条的自然分支），不是这一级。
        return ([dict(sentence)], None)
    text = str(sentence.get("text") or "")
    claim_ids = tuple(dict.fromkeys(str(c) for c in sentence.get("claim_ids") or ()))
    dropped = len(tuple(dict.fromkeys(
        str(c) for c in sentence.get("context_binding_ids") or ())))
    if not claim_ids or any(cid not in known_claims for cid in claim_ids):
        return ([dict(sentence)], None)
    claim_texts = tuple(str(getattr(known_claims[cid], "text", "") or "") for cid in claim_ids)
    defect = NS.composed_organization_defect(text=text, authorized_texts=claim_texts)
    if defect is None or defect == NS.COMPOSED_DEFECT_NOT_PRESENT_IN_ORDER:
        return ([dict(sentence)], None)
    if any(not _claim_citation_ids(known_claims[cid]) for cid in claim_ids):
        return ([dict(sentence)], None)

    def record(remedy: str, specs: list[dict]) -> "tuple[list[dict], dict]":
        return (specs, {"defect": defect, "remedy": remedy,
                        "claim_count_before": len(claim_ids),
                        "claim_ids": list(claim_ids),
                        "sentence_count_after": len(specs),
                        "context_bindings_dropped": dropped})

    parts = NS.split_composed_sentence_at_terminators(
        text=text, authorized_texts=claim_texts)
    if parts:
        return record(REORGANIZATION_SPLIT_AT_TERMINATORS, [
            {REPAIRED_SENTENCE_KIND_KEY: "composed",
             "text": part_text,
             "claim_ids": tuple(claim_ids[pos] for pos in inside),
             "context_binding_ids": ()}
            for part_text, inside in parts])
    if not NS.is_punctuation_only_join(text, claim_texts):
        # 接缝上有模型自己写的组织语或自造表面：**不做**第二级补救。那一级会丢掉接缝上的字符，
        # 于是「模型自造了一个数字」会被改写成一份不含该数字的合法正文——拒掉的版本不是被修好，
        # 而是被**洗掉**了。这种句子的正确去向是 fail-closed（判据拒了就是拒了）。
        return ([dict(sentence)], None)
    rendered: list[dict] = []
    for cid, claim_text in zip(claim_ids, claim_texts):
        if not str(claim_text or "").strip("。；;"):
            # 空白 Claim 渲染不出事实句（渲染器的既有 fail-closed）；留给核验按原样拒。
            return ([dict(sentence)], None)
        rendered.append({REPAIRED_SENTENCE_KIND_KEY: "factual",
                         "text": NS.render_factual_text([claim_text]),
                         "claim_ids": (cid,),
                         "context_binding_ids": ()})
    return record(REORGANIZATION_PER_CLAIM_FACTUAL, rendered)


def _bounded_reorganization(plan: Mapping[str, Any], *, known_claims: Mapping[str, Any]
                            ) -> tuple[dict, list[dict]]:
    """对整份句计划做一次有界定向重组织，返回 `(新计划, 逐条补救记录)`。

    重组织后的计划整体只有两件事变了：句子被拆/被重写，以及句类被系统显式标注（
    `REPAIRED_SENTENCE_KIND_KEY`）。`claim_dispositions` 原样带过——补救不改任何 Claim 的去向，
    去向是核验依据（第 5 条），不是补救能碰的东西。

    记录里**不落正文原文**（trace 只记调用与补救的元数据，不记正文）：每条只记段落/句子序号、
    封闭缺陷码、补救码、原句声明的 Claim **id 列表**（id 是引用不是正文）、补救后的句数与被丢弃的
    context 绑定数。
    """
    paragraphs: list[dict] = []
    records: list[dict] = []
    for p_pos, paragraph in enumerate(plan.get("paragraphs") or ()):
        if not isinstance(paragraph, Mapping):
            return (dict(plan), [])
        specs: list[dict] = []
        for s_pos, sentence in enumerate(paragraph.get("sentences") or ()):
            if not isinstance(sentence, Mapping):
                return (dict(plan), [])
            repaired, record = _repaired_specs(sentence=sentence, known_claims=known_claims)
            if record is not None:
                records.append({"paragraph_index": p_pos, "sentence_index": s_pos, **record})
            specs.extend(repaired)
        if not specs:
            return (dict(plan), [])
        paragraphs.append({"sentences": specs})
    return ({"paragraphs": paragraphs,
             "claim_dispositions": list(plan.get("claim_dispositions") or ())}, records)


def _table_dispositions(*, ordered_claims: Sequence[Any], tabled: Sequence[str],
                        section_id: str, draft_revision: str) -> tuple[Any, ...]:
    """表格行的 Claim 的**确定性**去向（§七 2）：`omitted / presented_as_table_row`。

    只补这一件事，**不**在这里重复「去向是否恰好覆盖每条 Claim」的判据：那条判据的唯一所有者
    是 `NS.verify_claim_narrative_dispositions`（同一条件在两处各有各的措辞与类型，调用方就得
    同时认识两种）。表格行的 Claim 不可能同时被模型处置——它们不在本次调用的输入面里。
    """
    tabled_set = set(tabled)
    return tuple(
        NS.ClaimNarrativeDisposition.create(
            section_id=section_id, draft_revision=draft_revision,
            claim_id=str(getattr(claim, "claim_id", "") or ""),
            disposition="omitted", reason_code="presented_as_table_row")
        for claim in tuple(ordered_claims or ())
        if str(getattr(claim, "claim_id", "") or "") in tabled_set)


def organize_section_narrative(*, claims: Sequence[Any],
                               accepted_context_bindings: Sequence[Any],
                               unresolved: Sequence[Any], writing_spec: Any,
                               presentation_profile: Any, identity: Mapping[str, str],
                               llm_client: Any,
                               tables: Sequence[Any] = (),
                               accepted_bindings: Sequence[Any] = (),
                               draft: Any = None,
                               material_context: Any = None) -> tuple[NS.SectionNarrative, dict]:
    """**唯一**的自然组织入口：一次 LLM 调用 → 严格解析 → 确定性核验 → narr-7 Narrative。

    返回 `(narrative, trace)`。`trace` 只记录调用元数据（call_id / 模型 / 状态 / 指纹），
    不落正文原文。任何一步失败抛 `NarrativeOrganizerError`：组织器不接受部分结果。

    `tables`（§七 2）是门后**确定性**构造的表格（如财务的「指标 × 期间」矩阵）。它不由模型
    产生，也**不进入**这次调用的输入：被表格行承载的 Claim 只出现在表格里，组织器看不到它们，
    因此不可能把同一批事实既写成句子又陈列一遍。它们的去向由本模块确定性补齐为
    `omitted / presented_as_table_row`，而「谁在表格里」只有一个读取入口
    （`NS.tabled_claim_ids`）。

    `accepted_bindings` 只用于**派生主题键**（`claim_theme_keys`：材料的标题路径 → 业务主题），
    它是组织线索、不是证据：它进不了正文（正文仍必须逐字由声明的 Claim 构成），也不改任何判据。
    缺省空元组时每条 Claim 的主题键退回 `topic_id`（旧行为）。

    `draft`（`norg-7`）是门后仍可用的 `SectionDraft`：只读它的 `natural_prose_draft`（门前自然
    草稿）。草稿不是素材、不是授权面，它是**表达基础 + 原子台账**（见 `prose_draft_ledger`）：
    组织器可以自然改写它表达的**已过门**内容，但不得借它把没过门的断言写回来。缺省 `None`
    （旧调用方、`narr-6` 及更早的产物）时输入面与 `norg-6` 逐字相同。

    `material_context`（`norg-8` / `orgmc-1`）是已解析的 `wmctx-1` 材料正文上下文：本函数只从
    它取**草稿台账点名过的**成员正文（`organizer_material_context`，逐成员与 `draft` 的清单核
    身份）。它同样是**只读依据面**，不是授权：句子仍要过 `ng-14` 的保真核对与第 1/2/3 条表面
    核验。缺省 `None` 时投出空 `material_context.rows`——这是财务节（纯 `FinancialFactPack`，
    草稿单元声明的是权威事实出处而非 Pack 材料）与「本节没有草稿层」两种情形的正确读数，
    不为凑出非空输入而伪造材料。
    """
    from llm import client as _llmc

    ordered_claims = tuple(claims or ())
    if not ordered_claims:
        raise NarrativeOrganizerError(
            "自然组织至少需要一条已定稿 Claim：没有 Claim 的正文没有事实来源"
            "（本节的空正文情形走确定性构造器，不走组织器）")
    tables = tuple(tables or ())
    known_claims = {}
    for claim in ordered_claims:
        claim_id = str(getattr(claim, "claim_id", "") or "")
        if not claim_id or claim_id in known_claims:
            raise NarrativeOrganizerError(
                f"已定稿 Claim 集含空 id 或重复 id（{claim_id!r}）：组织器不得面对歧义输入")
        known_claims[claim_id] = claim
    tabled = NS.tabled_claim_ids(tables)
    stray = sorted(set(tabled) - set(known_claims))
    if stray:
        raise NarrativeOrganizerError(
            f"表格行引用了本次定稿集之外的 Claim：{stray[:6]}"
            "（表格不得自带正文，也不得指向别的章节的 Claim）")
    if len(set(tabled)) != len(tabled):
        raise NarrativeOrganizerError("同一 Claim 出现在多行表格里：重复陈列必须被拦住")
    prose_claims = tuple(claim for claim in ordered_claims
                         if str(claim.claim_id) not in set(tabled))
    if not prose_claims:
        raise NarrativeOrganizerError(
            "本节所有已定稿 Claim 都由表格承载：没有可组织的正文素材，"
            "不得为此发起一次没有素材的模型调用（该情形走确定性构造路径）")

    theme_keys = claim_theme_keys(claims=prose_claims, accepted_bindings=accepted_bindings)
    # `norg-7`：门前草稿 → 原子台账。台账按**全部**已定稿 Claim 查（表格承载的那些也要入账，
    # 否则它们会以「原子没过门」的形态出现在输入面里——同一件事在读数上是两种含义），但表格承载
    # 的 Claim 不算 `kept`：它们不在本次调用的 claims 面里，组织器写不了它们，正文句子再绑一次
    # 就是重复陈列。
    prose_payload, prose_readings = prose_draft_ledger(
        draft=draft, claims=ordered_claims, tabled=tabled)
    prose_ledger = prose_ledger_claim_ids(prose_payload)
    # `norg-8` / `orgmc-1`：只投草稿台账**点名过**的成员正文，且逐成员核身份。空表是合法读数
    # （财务节的草稿单元声明权威事实出处、或本节没有草稿层），**不**为凑非空输入伪造材料。
    material_rows, material_readings = organizer_material_context(
        prose_payload=prose_payload,
        manifest=getattr(draft, "material_manifest", None), material_context=material_context)
    system = _llmc.load_prompt(NARRATIVE_ORGANIZER_PROMPT_ASSET)
    verify_organizer_prompt_asset(system)
    messages = build_organizer_messages(
        claims=prose_claims, accepted_context_bindings=accepted_context_bindings,
        unresolved=unresolved, writing_spec=writing_spec,
        presentation_profile=presentation_profile, identity=identity,
        theme_keys=theme_keys, prose_draft=prose_payload,
        material_context_rows=material_rows)
    call = llm_client.narrate(messages=messages, system=system,
                              prompt_version=NARRATIVE_ORGANIZER_PROMPT_VERSION,
                              model_policy=NARRATIVE_ORGANIZER_MODEL_POLICY)
    trace: dict = {
        "prompt_version": NARRATIVE_ORGANIZER_PROMPT_VERSION,
        "rules_version": NARRATIVE_ORGANIZER_RULES_VERSION,
        "model_policy": NARRATIVE_ORGANIZER_MODEL_POLICY,
        "llm_calls": 1,
        # `norg-7` 草稿层读数（只记计数，不落草稿正文）：「本节有没有草稿可用」与「草稿里的原子
        # 有几条过了门」是两件事，读数分开——把「原子全没过门」读成「没有草稿层」会掩盖一次真实
        # 的内容代价（草稿白写、两道门把它的内容全否了），而那正是「局部坏候选」的真实形态。
        "prose_draft": dict(prose_readings),
        # `norg-8` / `orgmc-1` 材料正文读数（只记计数，不落材料正文）：「投了几行」「点名的成员
        # 有几个」「逐成员核身份了几次」是三件事——把「投了 0 行」读成「没核身份」会掩盖一次
        # 真实的输入面退化。
        "organizer_material_context": dict(material_readings),
    }
    if hasattr(call, "to_dict") and not isinstance(call, str):
        trace.update({k: v for k, v in dict(call.to_dict()).items()
                      if k in ("call_id", "model", "status", "input_tokens", "output_tokens",
                               "latency_ms", "finish_reason", "response_fingerprint")})
        status = str(getattr(call, "status", "") or "")
        text = str(getattr(call, "text", "") or "")
    else:
        trace.update({"status": "malformed", "error": "llm_client 未返回结构化 NarrationResult"})
        status = "malformed"
        text = ""
    if status != "ok":
        raise NarrativeOrganizerError(
            f"正文组织调用未成功（status={status!r}，call_id={trace.get('call_id')!r}）："
            "没有成功调用就没有正文，不得沿用上一版或凭空组织")

    plan = parse_organizer_plan(text)
    # ⑥ 有界定向重组织：模型这一版的句计划里若有**机械缺陷**（第 8 条），就地按**确定性**两级阶梯
    # 补救（拆分原文 / 各自成句），**不**再发一次模型调用（预算按「每节写作 1 次 + 门后自然组织 1 次」
    # 推导，多发一次必然撑破结构上界）。判据本身一字未改：补救后的正文仍要整份过
    # `NS.verify_section_narrative`；修不了（如 Claim 没出现在正文里）就照旧 fail-closed。
    plan, reorganizations = _bounded_reorganization(plan, known_claims=known_claims)
    trace["reorganization_version"] = NARRATIVE_ORGANIZER_REORGANIZATION_VERSION
    trace["reorganizations"] = len(reorganizations)
    if reorganizations:
        trace["reorganization_details"] = [_jsonable(r) for r in reorganizations]
        trace["reorganization_remedies"] = sorted({str(r["remedy"]) for r in reorganizations})
    paragraphs: list[NS.NarrativeParagraph] = []
    topic_order: list[str] = []
    for index, paragraph in enumerate(plan["paragraphs"]):
        specs = _sentence_specs(paragraph, known_claims=known_claims,
                                prose_ledger=prose_ledger)
        used_topics: list[str] = []
        for spec in specs:
            for cid in spec["claim_ids"]:
                try:
                    topic_id = NS.claim_topic_for_conservation(known_claims[cid])
                except NS.NarrativeSchemaError as exc:
                    raise NarrativeOrganizerError(
                        f"paragraphs[{index}] 引用的 Claim {cid!r} 没有可派生的 topic 归属："
                        f"{exc}（段落 topic 只能由所引用 Claim 派生，不得自报）") from exc
                if topic_id not in used_topics:
                    used_topics.append(topic_id)
        if not used_topics:
            raise NarrativeOrganizerError(
                f"paragraphs[{index}] 的句子没有可归属的 topic：段落必须落在它引用的 Claim 的"
                "主题上（topic 归属由 Claim 派生，不得自报）")
        for topic_id in used_topics:
            if topic_id not in topic_order:
                topic_order.append(topic_id)
        paragraphs.append(NS.NarrativeParagraph.create(
            section_id=str(identity.get("section_id") or ""), topic_ids=tuple(used_topics),
            index=index, sentence_specs=specs))

    dispositions = tuple(
        NS.ClaimNarrativeDisposition.create(
            section_id=str(identity.get("section_id") or ""),
            draft_revision=str(identity.get("draft_revision") or ""),
            claim_id=str(d.get("claim_id") or ""),
            disposition=str(d.get("disposition") or ""),
            reason_code=d.get("reason_code") or None)
        for d in plan["claim_dispositions"])

    accepted_context_ids = tuple(
        str(getattr(b, "accepted_support_binding_id", "") or "")
        for b in (accepted_context_bindings or ()))
    # §七 2：表格行的 Claim 由**表格**呈现，去向是确定的（omitted / presented_as_table_row），
    # 不由模型自报；模型也写不了它们的句子——它们不在本次调用的输入面里。
    dispositions = dispositions + _table_dispositions(
        ordered_claims=ordered_claims, tabled=tabled,
        section_id=str(identity.get("section_id") or ""),
        draft_revision=str(identity.get("draft_revision") or ""))
    narrative = NS.SectionNarrative.create(
        task_id=str(identity.get("task_id") or ""),
        section_id=str(identity.get("section_id") or ""),
        section_draft_id=str(identity.get("section_draft_id") or ""),
        draft_revision=str(identity.get("draft_revision") or ""),
        paragraphs=tuple(paragraphs), tables=tables,
        # 节级声明 = 本节**已接受**的 context binding 全集（完备性），句级只声明实际使用的子集。
        context_binding_ids=accepted_context_ids)

    # 确定性核验：组织器是第一个撞上它的人，但**不是**它的所有者（组装器用同一函数复算）。
    NS.verify_section_narrative(
        narrative=narrative, claims=ordered_claims,
        accepted_context_binding_ids=accepted_context_ids, dispositions=dispositions)
    NS.verify_section_narrative_claims_selected(
        narrative=narrative, claims=ordered_claims, dispositions=dispositions)
    trace["claim_narrative_dispositions"] = [d.to_dict() for d in dispositions]
    trace["topic_order"] = list(topic_order)
    # 主题分组证据（**不是正文**）：逐主题只记键与条数。它证明「同一主题的 Claim 被放进同一段」，
    # 而不是把标题正文抄进 trace（标题是导航级信息，正文一个字都不留）。
    trace["theme_groups"] = [
        {"theme_key": theme, "claim_count": sum(1 for cid, key in theme_keys.items() if key == theme)}
        for theme in dict.fromkeys(theme_keys.values())]
    trace["sentence_count"] = sum(len(p.sentences) for p in narrative.paragraphs)
    trace["composed_sentence_count"] = sum(
        1 for p in narrative.paragraphs for s in p.sentences if s.sentence_kind == "composed")
    # `norg-7`：自然句计数。它只回答「这一节的正文有多少句是以草稿为表达基础写的」——**不是**
    # 内容质量读数，也不是「材料被用上了」的证明（自然句的判据在门后保真核对里，不在这里）。
    trace["natural_sentence_count"] = sum(
        1 for p in narrative.paragraphs for s in p.sentences if s.sentence_kind == "natural")
    return narrative, trace


def build_final_narrative(*, claims: Sequence[Any], accepted_context_bindings: Sequence[Any],
                          unresolved: Sequence[Any], writing_spec: Any,
                          presentation_profile: Any, identity: Mapping[str, str],
                          tables: Sequence[Any] = (), llm_client: Any = None,
                          draft: Any = None,
                          accepted_bindings: Sequence[Any] = (),
                          material_context: Any = None) -> tuple[NS.SectionNarrative, dict]:
    """门后 final Narrative 的**唯一**入口：有可组织的 Claim 走自然组织，否则走确定性构造。

    两条路径**共用同一条链的同一个位置**（都在 `SectionResult` 之前、都在两道门之后），差别只
    在于「有没有可组织的正文素材」：没有任何 Claim 时不存在可组织的素材，或者所有 Claim 都由
    门后**确定性**表格（§七 2）承载时正文里没有可组织的句子——两种情形自然组织都无从谈起，
    此时仍由 `NS.build_section_narrative` 用表格/空段落的确定性构造收口（它不做任何自然改写）。

    `draft` 是门后仍可用的 `SectionDraft`：自然组织路径把它的 `natural_prose_draft` 折成**原子
    台账**投给组织器（`norg-7` 的表达基础），退化路径只用它取身份与表格锚点。

    `accepted_bindings` 只被自然组织路径用于派生**主题键**（见 `claim_theme_keys`）：它不进正文、
    不改判据，缺省空元组时主题键退回 `topic_id`（旧行为）。

    `material_context`（`norg-8` / `orgmc-1`）是已解析的 `wmctx-1` 材料正文上下文，只被自然组织
    路径用于投出**草稿点名过的**成员正文（只读依据面，不授权任何事实）。退化路径（无 Claim 或
    全部由表格承载）不发起模型调用，因此用不到它。
    """
    ordered_claims = tuple(claims or ())
    tables = tuple(tables or ())
    tabled = set(NS.tabled_claim_ids(tables))
    stray = sorted(tabled - {str(getattr(c, "claim_id", "") or "") for c in ordered_claims})
    if stray:
        raise NarrativeOrganizerError(
            f"表格行引用了本节未定稿的 Claim {stray[:6]}：表格不得自带正文")
    prose_claims = tuple(c for c in ordered_claims
                         if str(getattr(c, "claim_id", "") or "") not in tabled)
    if prose_claims and llm_client is not None:
        return organize_section_narrative(
            claims=ordered_claims, accepted_context_bindings=accepted_context_bindings,
            unresolved=unresolved, writing_spec=writing_spec,
            presentation_profile=presentation_profile, identity=identity,
            llm_client=llm_client, tables=tables, accepted_bindings=accepted_bindings,
            draft=draft, material_context=material_context)
    if prose_claims and llm_client is None:
        raise NarrativeOrganizerError(
            "本节有已定稿 Claim 但没有可用的 llm_client：自然组织不得被静默跳过"
            "（跳过会让同一份 Claim 集在两种运行时得到两种正文，且无人发现）")
    if draft is None:
        raise NarrativeOrganizerError("退化路径必须给出 draft（用于取本节身份与表格锚点）")
    # 「既没有 Claim 也没有表格」**不在这里**判：那条判据的唯一所有者是
    # `NS.build_section_narrative`，它抛出的 `NarrativeSchemaError` 就是该情形的既有
    # typed 失败。在这里另立一个错误类型，等于让同一个条件在两处各有各的措辞与类型，
    # 调用方（以及「定稿处 fail-closed」的断言）就得同时认识两种。
    narrative = NS.build_section_narrative(
        draft=draft, claims=(), context_binding_ids=(), tables=tables)
    # 表格唯一承载 Claim 时，去向是确定的：这些 Claim 的呈现位置就是表格行。
    dispositions = tuple(NS.ClaimNarrativeDisposition.create(
        section_id=str(identity.get("section_id") or ""),
        draft_revision=str(identity.get("draft_revision") or ""),
        claim_id=str(getattr(c, "claim_id", "") or ""),
        disposition="omitted", reason_code="presented_as_table_row")
        for c in ordered_claims if str(getattr(c, "claim_id", "") or "") in tabled)
    if len(dispositions) != len(ordered_claims):
        raise NarrativeOrganizerError(
            "退化路径只允许「零 Claim」或「全部 Claim 由表格承载」两种情形："
            f"本次 {len(ordered_claims)} 条 Claim 里只有 {len(dispositions)} 条在表格里")
    return narrative, {"prompt_version": NARRATIVE_ORGANIZER_PROMPT_VERSION,
                       "rules_version": NARRATIVE_ORGANIZER_RULES_VERSION,
                       "llm_calls": 0,
                       "path": ("deterministic_tables_only" if tables
                                else "deterministic_no_claims"),
                       "claim_narrative_dispositions": [d.to_dict() for d in dispositions]}


__all__ = [
    "NarrativeOrganizerError",
    "THEME_DEPTH",
    "theme_key_from_section_path",
    "claim_theme_keys",
    "NARRATIVE_ORGANIZER_PROMPT_ASSET",
    "NARRATIVE_ORGANIZER_PROMPT_REVISION",
    "NARRATIVE_ORGANIZER_PROMPT_VERSION",
    "NARRATIVE_ORGANIZER_PROMPT_SHA256",
    "NARRATIVE_ORGANIZER_RULES_VERSION",
    "NARRATIVE_ORGANIZER_MODEL_POLICY",
    "verify_organizer_prompt_asset",
    "parse_organizer_plan",
    "prose_draft_ledger",
    "prose_ledger_claim_ids",
    "ORGANIZER_MATERIAL_CONTEXT_VERSION",
    "ORGANIZER_MATERIAL_CONTEXT_REASONS",
    "organizer_material_context",
    "PROSE_ATOM_KEPT",
    "PROSE_ATOM_DROPPED",
    "build_organizer_messages",
    "organize_section_narrative",
    "build_final_narrative",
]

#: 让 `from sections.narrative_organizer import *` 之外的使用者也能看到类型别名。
SectionNarrative = NS.SectionNarrative

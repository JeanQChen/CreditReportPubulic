"""最终句语义门 B（`nsfid-1` / `nsfr-1`）：逐**事实原子**的版本化核验。

用法：`from sections import final_sentence_fidelity as FSF`

职责（且仅此）：对一个 draft revision 的**全部承载事实的最终句**输出**恰好一个**版本化
`FinalSentenceFidelityDecision`。

* 覆盖范围：`natural` 与 `composed` **一视同仁**（2026-09-27 的范围勘误，指令 D 第四项）。
  `composed` 句的「Claim 文本逐字在场」只回答「字有没有抄错」，**不**回答连接语有没有新增
  因果、时间、范围或结论——那正是本门要挡的越权，因此本门**不因 Claim 逐字在场而放行**。
* 输入：最终 Narrative（句子集）+ 这些句子声明的**已定稿** Claim + 它们的 **factual** 已接受
  支撑边 + 这些边解析出的权威事实与**真实材料正文**。
* 输出：逐原子 `atoms[]`（每行给出句子里的表面、声明它的 Claim、承载它的支撑边、verdict 与
  typed `reason_code`）+ 一个由逐原子结果**推出**的聚合 verdict。
* 本门**不改写正文**、**不检索**、**不联网**、**不写库**、**不产生第二套 research runtime**；
  它只给结构化决定。真正的「人工接受」与系统 Assurance 是另外两件事。

**与 `natfid-1` 的分工（不许含糊）。** `natfid-1`（`sections/narrative_schema.py` 的
`verify_sentence_fidelity`）是**只读表面比较器**：它比对最终句与声明 Claim 文本的**字面**，
只回答「多字/少字」。它对三类越权两方向都没有读数（实测放行，缺陷码 `[]`）：改写里加了模糊语
（「变动为」→「变动约为」）、换了主体或指标名（「资产负债率」→「有息负债率」）、删了代理口径
限定语（`代理口径（PROXY_FINANCE_EXPENSES）`）。本门不看字面是否在场，而是要求逐原子给出
「这条断言由谁授权」。**本门不放宽也不替换 `natfid-1`**：两者并存，各自 fail-closed。

**确定性定位是辅助，不是判据（这条是本门的核心纪律）。** `locate_fact_atoms` 复用既有抽取器
（`scan_numeric_tokens` / `marker_hits` / `vague_period_hits` / `entity_head_nouns` /
范围·强度·因果补语表），**不新造任何词表**（词表化指标名或答案关键词是明令禁止的）。它只做
「这些表面在文本里出现过」这一件事，**不做**语义判断。它的输出按**句**分组作为**预填槽位**
（`located_atoms: sentence_id -> [待答行]`，判定字段一律留空、`null` 不是「通过」）进输入面，
并且**每一条都必须被模型的输出逐行认领**——漏认领即 fail-closed。这样「机械抽取器没发现原子」
**不可能**被读成「这句没有事实」：位置由机械给出，语义由判定面给出，两者缺一都不能成为决定。

**为什么要预填成槽位（`nsfr-2`，r9 后的定点返修）。** 旧输入面把定位读数摊成一张平表，模型
因此可以把它读成「这一句大致说了什么」，再把两个年份**合成一条期间原子**（r9 财务节现场：
已定位 `2025年` / `2024年` / `-3.3个百分点`，返回只认领了 `2025年末较2024年末` 与另外两条，
两条年份表面无人认领 ⇒ 本门未形成决定）。「这句里有几个独立成分」不该由模型去发现：现在每个
已定位表面各占一行、各要一个答案，合并即漏答（`nsfr-2` 的规则 3 写明了不合并、不拆分、不留空）。
**这条改动只改输入面与 prompt 词条**：判定面（`interpret_sentence_fidelity_output` 的精确逐条
认领与 fail-closed）、聚合推导、wire 与落库形状一个字都没动。

fail-closed 清单：

| 情形 | 行为 |
|---|---|
| bundle 的句子集/Claim 集/支撑边集自相矛盾（缺 Claim、非 factual 边、跨 draft revision） | 抛 `FinalSentenceFidelityError` |
| prompt 资产自报 (资产名, revision) 或正文指纹与登记值不符 | **调用前**抛（不消耗调用预算） |
| 材料绑定了但没有 `wmctx-1` 正文上下文，或正文/哈希/locator 与 manifest 成员不符 | **调用前**抛 |
| 传输失败 / 输出不是合法 JSON / verdict、reason_code、atom_kind 越界 | 抛（**不**伪造一条 rejected：封闭原因码里没有「调用失败」，把机械故障写成语义判决就是伪造证据） |
| `per_sentence` 的句子集与 bundle 不等、顺序不同、有重复 | 抛 |
| 某条 `located_atoms` 没有被输出认领 | 抛（「未发现原子」不得自动通过） |
| 输出的聚合 verdict 与逐原子结果不符 | 抛（聚合必须由逐原子推出） |
| 每次调用 | 至多一次、只降级不升级（拿不准一律 `rejected`）；不重试 |
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

# 与 `claim_entailment_evaluator` 同一句法：LLM 传输只有 `llm/client.py` 一份实现。
from llm import client as llm
from sections import claim_binding_gate as CBG
from sections import material_context as MC
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import schema as SS

#: prompt 资产名 / 修订号 / 正文指纹。三侧（登记版本 = 资产自报 = 正文指纹）必须一致。
#:
#: `nsfr-2`（r9 后的定点返修，指令 D 第一项）：输入面把机械定位读数改成**逐句的预填槽位**
#: （`located_atoms: <sentence_id> -> [待答行]`），判定字段一律留空。r9 的真实返回正是在
#: 旧输入面上**把两个年份合成了一条期间原子**（`2025年末较2024年末`），于是两条已定位表面
#: 无人认领、本门未能形成决定——「请你自己去发现有几个表面」这件事不该由模型答。`v1` 的资产
#: 文件与其登记指纹**不动**（历史 run 的 `prompt_version` 必须仍能在登记表里对上号）。
FINAL_SENTENCE_FIDELITY_PROMPT_ASSET = "final_sentence_fidelity_v2"
FINAL_SENTENCE_FIDELITY_PROMPT_REVISION = "nsfr-2"
FINAL_SENTENCE_FIDELITY_PROMPT_VERSION = (
    f"{FINAL_SENTENCE_FIDELITY_PROMPT_ASSET}@{FINAL_SENTENCE_FIDELITY_PROMPT_REVISION}")
FINAL_SENTENCE_FIDELITY_PROMPT_SHA256 = (
    "7fc042d190d96913b000542d586ca1b749d530e9236d1ffc17063e85ab8c7f8d")

#: 语义核验规则版本 = wire 的 `FinalSentenceFidelityDecision.rubric_version` 缺省值。
#: 它**唯一**定义在 wire 模块；本模块是它的规则所有者，只 re-export（避免两处版本漂移）。
FINAL_SENTENCE_FIDELITY_RULES_VERSION: str = NS.FINAL_SENTENCE_RULES_VERSION

#: 本门的 model policy：缺省与 Writer / 蕴含门同口径，可被调用方按批次覆盖（离线桩用 `stub`）。
FINAL_SENTENCE_FIDELITY_MODEL_POLICY = PW.MODEL_POLICY_PROVIDER_DEFAULT

_PROMPT_DECL_RE = PW._PROMPT_DECL_RE


class FinalSentenceFidelityError(Exception):
    """最终句语义核验的输入/资产/调用 fail-closed（**不是**一条决定）。"""


class FinalSentenceFidelityUnavailable(FinalSentenceFidelityError):
    """本门**未能形成决定**（调用失败 / 输出不可解析 / 已定位原子未被认领）——**不是**判决。

    这一档之所以单独成型，是因为定稿路径必须把两种「没通过」分开记账：

    * **判决档**（`verdict=rejected`）：模型读过正文，逐原子给出了未通过的读数。它是一条决定。
    * **未能形成决定档**（本类）：连一条决定都没有。此时若把它写成 `rejected`，就等于**伪造**
      一条语义判决（封闭原因码里没有「调用失败」，见模块 docstring 的 fail-closed 表）；
      若让它静默通过，则正文根本没被核验过。

    因此它上抛时**只**被定稿路径接住，并如实落成 `final_sentence_decision_missing` 的 typed
    block（正文标为未核验、章节 `SECTION_BLOCKED`）。输入面自身的缺陷（跨 revision、缺 Claim、
    材料正文对不上、prompt 资产漂移）**不**走这一档——那些是接线缺陷，必须当场抛出。
    """


class SentenceFidelityClient(Protocol):
    """本门能拿到的全部外部能力：一次结构化生成调用。没有检索/工具入口。"""

    def evaluate(self, *, messages: Sequence[Mapping[str, str]], system: str,
                 prompt_version: str, model_policy: str) -> PW.NarrationResult: ...


class LlmSentenceFidelityClient:
    """把既有 Writer 传输适配器接成本门的 client（**不**新建第二套 LLM runtime）。"""

    def __init__(self, *, model: str | None = None, max_tokens: int = 4096,
                 thinking: dict | None = None,
                 narration_client: PW.NarrationClient | None = None) -> None:
        self._inner = narration_client or PW.LlmNarrationClient(
            model=model, max_tokens=max_tokens, thinking=thinking)

    def evaluate(self, *, messages: Sequence[Mapping[str, str]], system: str,
                 prompt_version: str, model_policy: str) -> PW.NarrationResult:
        return self._inner.narrate(messages=messages, system=system,
                                   prompt_version=prompt_version, model_policy=model_policy)


def verify_final_sentence_fidelity_prompt_asset(text: str | None = None) -> str:
    """核对「记录版本 = 实际加载的 prompt 资产」，返回资产正文；任何一侧漂移即 fail-closed。"""
    if text is None:
        try:
            text = llm.load_prompt(FINAL_SENTENCE_FIDELITY_PROMPT_ASSET)
        except FileNotFoundError as exc:
            raise FinalSentenceFidelityError(
                f"最终句语义核验 prompt 资产缺失：{exc}") from exc
    match = _PROMPT_DECL_RE.search(str(text or ""))
    if not match:
        raise FinalSentenceFidelityError(
            f"prompt 资产 {FINAL_SENTENCE_FIDELITY_PROMPT_ASSET!r} 未自报 (资产名, revision)，"
            "无法证明记录版本与实际加载版本一致")
    declared = f"{match.group('asset')}@{match.group('revision')}"
    if declared != FINAL_SENTENCE_FIDELITY_PROMPT_VERSION:
        raise FinalSentenceFidelityError(
            f"实际加载的 prompt 资产自报 {declared!r}，与登记版本 "
            f"{FINAL_SENTENCE_FIDELITY_PROMPT_VERSION!r} 不一致（不得用旧记录冒充）")
    actual = PW.prompt_asset_fingerprint(text)
    if actual != FINAL_SENTENCE_FIDELITY_PROMPT_SHA256:
        raise FinalSentenceFidelityError(
            f"prompt 资产正文指纹 {actual!r} 与登记值 "
            f"{FINAL_SENTENCE_FIDELITY_PROMPT_SHA256!r} 不一致：资产 "
            f"{FINAL_SENTENCE_FIDELITY_PROMPT_ASSET!r} 的正文被改过"
            "（资产名与修订号可被照抄，正文不行）")
    return text


# ---------------------------------------------------------------------------
# 第 2 步：确定性原子定位（只读、辅助；**不做**语义判断）
# ---------------------------------------------------------------------------
#
# 六类原子的抽取**全部复用既有实现**，一个字表都没有新造：
#
# | atom_kind | 抽取器 | 复用自 |
# |---|---|---|
# | `numeric` | `NS.scan_numeric_tokens` | 数字门（`narr-*` 一直在用） |
# | `period` | `NS.vague_period_hits` | 含糊期间表 |
# | `negation_or_state` | `NS.marker_hits` | 高风险表面标记（否定 / 勾选状态 / 表格关系） |
# | `entity_head` | `NS.entity_head_nouns` | 实体头部名词表 |
# | `scope_or_strength` | `NS.SCOPE_QUALIFIER_MARKERS` ∪ `NS.CONCLUSION_STRENGTH_MARKERS` ∪ `NS.CAUSAL_ASSERTION_MARKERS` | `natfid-1` 的三张补语表 |
# | `table_relation` | `NS.marker_hits` 里属于 `NS.TABLE_RELATION_SURFACE_MARKERS` 的那一组 | 同一份封闭表的**具名切片**（不另立副本） |
#
# **`scope_or_strength` 为什么把因果连接语并进来**：`natfid-1` 的 `added_scope_or_strength`
# 本来就「含因果补语」（它自己的 docstring 表里写明的口径）。本门沿用同一个折叠，不新开第七类
# ——「因果」与「范围/强度」要挡的是同一件事：把并列升级成更强的断言。
#
# **单字补语为什么也照 `natfid-1` 一样跳过**：`故` / `均` / `各` / `仅` 作为**子串**会大量误伤
# （`平均` / `不仅` / `故事` / `各地`）。`natfid-1` 在「不得新增」一侧按
# `FIDELITY_SINGLE_CHAR_MARKERS` 跳过它们，本定位器沿用**同一条**规则——两处若不同，同一份正文
# 会得到两个读数。跳过的代价与那里一样是**有界且已知**的：这些字仍可由判定面在
# `per_sentence[].atoms[]` 里自行补出（prompt 第 3 条明确要求「可以、也应当补充抽取器没抓到的
# 原子」），因此覆盖不会丢，只是不由机械抽取器给出。
#: 逐句定位时的**去重键**：同一句里同一 (kind, surface) 只算一条原子。
#:
#: 不去重会让「本表…其中…」这类同 surface 重复出现的文本产生多条同形原子，而它们在任何
#: 判据上都是同一条读数——重复只增加模型要认领的行数，不增加任何覆盖面。
AtomKey = tuple[str, str]


@dataclass(frozen=True)
class LocatedAtom:
    """机械定位出来的一条候选原子（**只说明这些字在文本里出现过**，不含任何语义判断）。"""

    atom_ref: str
    sentence_id: str
    atom_kind: str
    atom_surface: str
    #: 声明这条表面的已接受 Claim id（多条声明时取**最小 id**，确定性）；没有一条声明时为 None。
    #: 它是**提示**：`None` 强烈指向 `atom_not_located`，但判定权在判定面。
    declared_by_claim_id: str | None

    def to_dict(self) -> dict:
        return {"atom_ref": self.atom_ref, "sentence_id": self.sentence_id,
                "atom_kind": self.atom_kind, "atom_surface": self.atom_surface,
                "declared_by_claim_id": self.declared_by_claim_id}


def _surface_kinds(text: Any) -> tuple[tuple[str, str], ...]:
    """文本里的 `(atom_kind, atom_surface)`（有序、按首次出现、按 (kind, surface) 去重）。

    六类各取各的抽取器；`table_relation` 与 `negation_or_state` 同源于 `marker_hits`，
    因此一次遍历把它命中的表面按**两张封闭子表**分流（表格关系那一组进 `table_relation`，
    其余进 `negation_or_state`）——两张子表是**同一份** `HIGH_RISK_SURFACE_MARKERS` 的切片，
    不是第二份词表。
    """
    body = str(text or "")
    out: list[tuple[str, str]] = []

    def add(kind: str, surface: str) -> None:
        if surface and (kind, surface) not in out:
            out.append((kind, surface))

    for token in NS.scan_numeric_tokens(body):
        add("numeric", token)
    for phrase in NS.vague_period_hits(body):
        add("period", phrase)
    table_relations = set(NS.TABLE_RELATION_SURFACE_MARKERS)
    for marker in NS.marker_hits(body):
        add("table_relation" if marker in table_relations else "negation_or_state", marker)
    for head in NS.entity_head_nouns(body):
        add("entity_head", head)
    for markers in (NS.SCOPE_QUALIFIER_MARKERS, NS.CONCLUSION_STRENGTH_MARKERS,
                    NS.CAUSAL_ASSERTION_MARKERS):
        for marker in NS._fidelity_marker_hits(body, markers):
            if len(marker) < 2 and marker in NS.FIDELITY_SINGLE_CHAR_MARKERS:
                continue  # 与 `natfid-1` 同一条跳过规则（见上方注释）
            add("scope_or_strength", marker)
    return tuple(out)


def locate_fact_atoms(*, sentence_id: str, text: Any,
                      claim_texts: "Mapping[str, str] | None" = None) -> tuple[LocatedAtom, ...]:
    """一句最终文本里**机械可定位**的候选原子（有序、去重、确定性）。

    `claim_texts` 是 `claim_id -> text` 的已接受 Claim 文本映射，只用于填
    `declared_by_claim_id`（哪条 Claim 也含这条表面）。**为空映射与不传是同一件事**：定位照做，
    只是每条的 `declared_by_claim_id` 都为 `None`（那时调用方必须自己知道「没有声明面」这个
    前提不成立——本函数不替它判断）。

    本函数**不抛**（文本不是字符串时按空处理），也**不**对任何表面下语义结论。
    """
    sid = str(sentence_id or "")
    if not sid:
        raise FinalSentenceFidelityError("locate_fact_atoms.sentence_id 不得为空")
    declarations: dict[AtomKey, list[str]] = {}
    for claim_id, claim_text in (claim_texts or {}).items():
        for key in _surface_kinds(claim_text):
            declarations.setdefault(key, []).append(str(claim_id))
    out: list[LocatedAtom] = []
    for index, (kind, surface) in enumerate(_surface_kinds(text)):
        declaring = sorted(declarations.get((kind, surface), ()))
        out.append(LocatedAtom(
            atom_ref=f"la-{index + 1}", sentence_id=sid, atom_kind=kind,
            atom_surface=surface,
            declared_by_claim_id=(declaring[0] if declaring else None)))
    return tuple(out)


# ---------------------------------------------------------------------------
# 输入束：最终 Narrative + 已定稿 Claim + factual 已接受支撑边
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SentenceFidelityBundle:
    """本门的完整输入面（一个 draft revision 的最终句集 + 它们的授权面）。

    `authority` 是权威的**确定性读视图**（有 `.facts` 条目序列），`manifest` 是本次写作的
    exact material manifest，`material_context` 是 `wmctx-1` 正文上下文——三者与
    `ClaimEntailmentDecision` 用的是**同一批**入口，本门不另建一套材料解析。
    """

    narrative: NS.SectionNarrative
    claims: tuple[Any, ...]
    accepted_bindings: tuple[NS.AcceptedSupportBinding, ...]
    authority: Any = None
    manifest: NS.WriterMaterialManifest | None = None
    material_context: Any = None
    #: 已解析的真实材料正文（`init=False` 派生字段，调用方**无法注入**；唯一来路是
    #: `MC.readings_for_manifest_members` 的逐项复核）。
    resolved_materials: tuple[Any, ...] = field(init=False, default=())

    def __post_init__(self) -> None:
        if not isinstance(self.narrative, NS.SectionNarrative):
            raise FinalSentenceFidelityError(
                "SentenceFidelityBundle.narrative 必须是 SectionNarrative")
        claims = tuple(self.claims or ())
        for claim in claims:
            if not isinstance(claim, SS.SectionClaim):
                raise FinalSentenceFidelityError(
                    f"claims 只接受已定稿 SectionClaim，得到 {type(claim).__name__}")
            if str(claim.section_id) != str(self.narrative.section_id):
                raise FinalSentenceFidelityError(
                    f"Claim {claim.claim_id} 不属于本节 {self.narrative.section_id!r}："
                    "跨节借证不得进入最终句核验")
        object.__setattr__(self, "claims", claims)
        bindings = tuple(self.accepted_bindings or ())
        for binding in bindings:
            if not isinstance(binding, NS.AcceptedSupportBinding):
                raise FinalSentenceFidelityError(
                    "accepted_bindings 只接受 AcceptedSupportBinding，"
                    f"得到 {type(binding).__name__}")
            if binding.support_semantics != "factual":
                raise FinalSentenceFidelityError(
                    f"支撑边 {binding.accepted_support_binding_id} 的 semantics 不是 factual："
                    "context 边不授权事实，也不进入本门")
            if str(binding.draft_revision) != str(self.narrative.draft_revision):
                raise FinalSentenceFidelityError(
                    "支撑边的 draft_revision 与最终 Narrative 不一致："
                    "不得在换过的正文上做语义核验")
            if binding.binding_subject_kind != "claim_candidate":
                raise FinalSentenceFidelityError(
                    "factual 支撑边的 subject 必须是 claim_candidate")
        expected = sorted(b.accepted_support_binding_id for b in bindings)
        if [b.accepted_support_binding_id for b in bindings] != expected:
            raise FinalSentenceFidelityError(
                "accepted_bindings 必须按 accepted_support_binding_id 升序（canonical order）")
        object.__setattr__(self, "accepted_bindings", bindings)
        if self.manifest is not None and not isinstance(self.manifest,
                                                        NS.WriterMaterialManifest):
            raise FinalSentenceFidelityError(
                "SentenceFidelityBundle.manifest 必须是 WriterMaterialManifest")
        self._assert_claim_coverage()
        object.__setattr__(self, "resolved_materials",
                           _resolved_materials(self))

    # -- 覆盖与一致性的结构断言（不调用任何模型） --

    def _assert_claim_coverage(self) -> None:
        """句子声明的 Claim 必须**恰好**是束里给出的那些：缺一条、多一条都是 fail-closed。

        「句子引用了一条本节没有的 Claim」与「束里带了一条没有任何句子引用的 Claim」是两类
        不同的事故，但两者都让「授权面」这个词失去意义：前者是无据陈述，后者是把没被使用的
        Claim 当成授权池（模型可以据此给一句话配一条它其实没引用的 Claim）。

        束的 `claims` 由唯一入口 `NS.gate_claim_surface(narrative, ...)` 收窄而来，因此
        「多一条」这一档现在是**派生纪律的结构断言**：它不是「正文漏写了某条已接受 Claim」
        （那**不是**本门的要求，见 `gate_claim_surface`），而是「有人绕开了那个入口、把整节
        Claim 直接塞了进来」。缺项方向照旧是真判据：正文引用而本束没有 ⇒ 拒。
        """
        declared = {c.claim_id: c for c in self.claims}
        referenced: list[str] = []
        for unit in self.narrative.paragraphs:
            for sentence in unit.sentences:
                for claim_id in sentence.claim_ids:
                    if claim_id not in referenced:
                        referenced.append(str(claim_id))
        missing = [cid for cid in referenced if cid not in declared]
        if missing:
            raise FinalSentenceFidelityError(
                f"最终句引用了本束未给出的已接受 Claim：{missing[:5]}"
                "（本门只在完整授权面上做核验，缺一条即拒）")
        unused = sorted(set(declared) - set(referenced))
        if unused:
            raise FinalSentenceFidelityError(
                f"本束带了没有任何句子引用的 Claim：{unused[:5]}"
                "（授权面必须恰好是本 revision 实际使用的那些 Claim）")

    def _bindings_by_claim(self) -> dict[str, tuple[NS.AcceptedSupportBinding, ...]]:
        by_id = {b.accepted_support_binding_id: b for b in self.accepted_bindings}
        out: dict[str, tuple[NS.AcceptedSupportBinding, ...]] = {}
        for claim in self.claims:
            rows: list[NS.AcceptedSupportBinding] = []
            for binding_id in claim.accepted_binding_ids:
                binding = by_id.get(str(binding_id))
                if binding is None:
                    raise FinalSentenceFidelityError(
                        f"Claim {claim.claim_id} 引用的 factual 支撑边 {binding_id} "
                        "不在本束的 accepted_bindings 里：授权面不完整即拒")
                rows.append(binding)
            out[str(claim.claim_id)] = tuple(rows)
        return out

    def claim_texts(self) -> dict[str, str]:
        return {str(c.claim_id): str(c.text) for c in self.claims}

    def sentences(self) -> tuple[Any, ...]:
        """承载事实的最终句（有序，与 `NS.fact_bearing_sentence_ids` 同一口径）。"""
        wanted = set(NS.fact_bearing_sentence_ids(self.narrative))
        out: list[Any] = []
        for unit in self.narrative.paragraphs:
            for sentence in unit.sentences:
                if sentence.sentence_id in wanted:
                    out.append(sentence)
        return tuple(out)

    def sentence_ids(self) -> tuple[str, ...]:
        return tuple(s.sentence_id for s in self.sentences())

    def factual_binding_ids(self) -> tuple[str, ...]:
        """本 revision 最终句所依赖的**全部** factual 支撑边 id（有序、去重）。"""
        out: list[str] = []
        for claim in self.claims:
            for binding_id in claim.accepted_binding_ids:
                if str(binding_id) not in out:
                    out.append(str(binding_id))
        return tuple(out)

    def support_set_digest(self) -> str:
        return NS.sentence_support_set_digest(
            draft_id=self.narrative.section_draft_id,
            draft_revision=self.narrative.draft_revision,
            narrative_id=self.narrative.narrative_id,
            sentence_ids=self.sentence_ids(),
            accepted_binding_ids=self.factual_binding_ids())


def _resolved_materials(bundle: SentenceFidelityBundle) -> tuple[Any, ...]:
    """束里支撑边指向的**全部**精确材料成员 → 已解析真实正文（缺上下文/不一致即 fail-closed）。

    与 `claim_entailment_evaluator` 走**同一个**唯一正文核验入口
    （`MC.readings_for_manifest_members`），所以「同 member_ref + 任意正文 + 自洽重算指纹」
    这条缝在本门同样不存在。
    """
    members = material_members(bundle)
    if not members:
        return ()
    if bundle.material_context is None:
        raise FinalSentenceFidelityError(
            "本 revision 的支撑边绑定了精确材料，却没有 wmctx-1 正文上下文："
            "语义核验看不到真实正文时不得调用 LLM（只有身份字段的清单不构成证据）")
    if bundle.manifest is None:
        raise FinalSentenceFidelityError(
            "绑定了精确材料却没有 exact manifest：无法证明正文属于本次写作")
    try:
        readings = MC.readings_for_manifest_members(material_context=bundle.material_context,
                                                    members=members)
    except MC.MaterialContextError as exc:
        raise FinalSentenceFidelityError(
            f"支撑边材料的正文无法复核（{exc.reason}，成员 {exc.member_ref!r}）：{exc}；"
            "缺正文或 payload/locator/hash 不一致时在调用 LLM 前拒绝") from exc
    expected = tuple(m.member_ref for m in members)
    actual = tuple(r.member_ref for r in readings)
    if actual != expected:
        raise FinalSentenceFidelityError(
            f"已解析正文与材料成员不逐位对应：得到 {actual}，应为 {expected}")
    return tuple(readings)


def material_members(bundle: SentenceFidelityBundle
                     ) -> tuple[NS.WriterMaterialManifestEntry, ...]:
    """束里支撑边解析出的**精确材料成员**（按 `member_ref` 升序、去重）。

    解析走的是 P8/P9 的**同一套**口径（`CBG.resolve_proposal_sources`：路径 A → 权威事实、
    路径 B / context → manifest 成员），因此「哪条边指向哪份材料」三处不会漂移。
    """
    manifest = bundle.manifest
    if manifest is None:
        return ()
    fact_table = CBG.authority_fact_table(bundle.authority) if bundle.authority is not None else {}
    out: dict[str, NS.WriterMaterialManifestEntry] = {}
    for binding in bundle.accepted_bindings:
        _entry, member = CBG.resolve_proposal_sources(binding, fact_table=fact_table,
                                                      manifest=manifest)
        if member is not None:
            out[member.member_ref] = member
    return tuple(out[key] for key in sorted(out))


def authority_facts(bundle: SentenceFidelityBundle) -> tuple[PW.AuthorityFactEntry, ...]:
    """束里路径 A 边解析出的**权威事实条目**（按 `(kind, container, fact_id)` 升序、去重）。"""
    if bundle.authority is None:
        return ()
    fact_table = CBG.authority_fact_table(bundle.authority)
    out: dict[tuple[str, str, str], PW.AuthorityFactEntry] = {}
    for binding in bundle.accepted_bindings:
        entry, _member = CBG.resolve_proposal_sources(binding, fact_table=fact_table,
                                                      manifest=bundle.manifest
                                                      or _empty_manifest())
        if entry is not None:
            out[(entry.authority_kind, entry.container_identity, entry.fact_id)] = entry
    return tuple(out[key] for key in sorted(out))


def _empty_manifest() -> NS.WriterMaterialManifest:
    """空 manifest（`entries=()`）。**只在取路径 A 权威事实的那条解析里**当占位符用。

    空 manifest 是这种 wire 的**合法状态**（见 `WriterMaterialManifest` 自己的 docstring），
    它**不是**「本节没有材料」的断言：任何真的指向 material 的边在这张清单里查不到成员，于是
    解析出的 member 是 `None`（那条边在材料面上**缺席**，而不是被安上一份凭空的材料）。有材料的
    现场仍然必须自带**真实** manifest——`material_members()` 在没有 manifest 时直接返回空元组，
    再把成员数拿去与已解析正文数对齐，因此「有材料却拿空清单」在这里读不出任何东西。
    """
    return NS.WriterMaterialManifest.create(entries=())


# ---------------------------------------------------------------------------
# 输入渲染（身份与正文都在输入面内，模型只能判语义）
# ---------------------------------------------------------------------------

def atom_slots(atom: LocatedAtom) -> dict:
    """一条**预填槽位**：只给机械定位到的表面与类别提示，判定字段一律留空。

    「留空」是**没有答案**，不是「通过」：`verdict` 是 `None`（不是任何一档 verdict），
    `claim_id` / `accepted_binding_ids` / `reason_code` 同样空着。判定面要求每一行都有
    真正的读数，照抄空值即 fail-closed——因此这份预填**不可能**替模型放行任何一条原子。
    槽位的键就是输出行的七个字段，模型不必猜形状；`atom_kind` 是类别**提示**，可按语义改正。
    """
    return {"atom_kind": atom.atom_kind, "atom_surface": atom.atom_surface,
            "claim_id": None, "accepted_binding_ids": [], "verdict": None,
            "reason_code": None, "rationale": ""}


def build_sentence_fidelity_messages(bundle: SentenceFidelityBundle
                                     ) -> tuple[list[dict], str]:
    """本门**唯一**的 LLM 输入：最终句 + 声明 Claim + factual 支撑边 + 权威/材料面 + 预填槽位。

    定位读数按**句**分组进输入面（`located_atoms: sentence_id -> [待答行]`）：每一条已定位的
    表面都是模型必须逐行回填的一行。分组是刻意的——旧输入面把定位读数摊成一张平表，模型因此
    可以（也确实会）把它读成「这一句大致说了什么」，再把两个年份合成一条期间原子（r9 现场），
    于是两条预填行无人认领。分组后每一行都必须有自己的答案，合并即漏答。
    """
    system = verify_final_sentence_fidelity_prompt_asset()
    claim_texts = bundle.claim_texts()
    sentences: list[dict] = []
    located: dict[str, list[dict]] = {}
    for sentence in bundle.sentences():
        sentences.append({
            "sentence_id": sentence.sentence_id, "sentence_kind": sentence.sentence_kind,
            "text": sentence.text, "claim_ids": list(sentence.claim_ids),
            "citation_ids": list(sentence.citation_ids),
            "context_binding_ids": list(sentence.context_binding_ids),
        })
        located[sentence.sentence_id] = [
            atom_slots(atom)
            for atom in locate_fact_atoms(sentence_id=sentence.sentence_id, text=sentence.text,
                                          claim_texts=claim_texts)]
    if sorted(located) != sorted(s.sentence_id for s in bundle.sentences()):
        raise FinalSentenceFidelityError(
            "预填槽位的句子集与本次核验的句子集不等：槽位不得漏句或多句")
    if len(bundle.resolved_materials) != len(material_members(bundle)):
        raise FinalSentenceFidelityError(
            "材料成员数与已解析正文数不一致：输入面必须逐位对应")
    rows = []
    for member, reading in zip(material_members(bundle), bundle.resolved_materials):
        if member.member_ref != reading.member_ref:
            raise FinalSentenceFidelityError(
                f"材料成员 {member.member_ref!r} 配到 {reading.member_ref!r} 的正文：身份错位即拒")
        if not reading.reading_view.strip():
            raise FinalSentenceFidelityError(
                f"材料成员 {member.member_ref!r} 的正文为空：空正文不得作为语义依据")
        rows.append({"container_id": member.pack_id, "material_id": member.material_id,
                     "member_ref": member.member_ref, "source_identity": member.source_identity,
                     "provenance_identity": member.provenance_identity,
                     "material_type": member.material_type,
                     "locator_ref": dict(member.locator_ref),
                     "payload_hash": member.payload_hash, "text": reading.reading_view})
    narrative = bundle.narrative
    user = json.dumps({
        "narrative": {
            "task_id": narrative.task_id, "section_id": narrative.section_id,
            "draft_id": narrative.section_draft_id, "draft_revision": narrative.draft_revision,
            "narrative_id": narrative.narrative_id,
        },
        "sentences": sentences,
        "claims": [{"claim_id": c.claim_id, "text": c.text,
                    "accepted_binding_ids": list(c.accepted_binding_ids),
                    "citation_refs": [SS.citation_identity(r) for r in (c.citation_refs or ())]}
                   for c in bundle.claims],
        "accepted_bindings": [{
            "accepted_support_binding_id": b.accepted_support_binding_id,
            "authority_kind": b.authority_kind,
            "container_id": b.authority_container_id, "fact_id": b.fact_id,
            "financial_fact_id": b.financial_fact_id, "note_fact_id": b.note_fact_id,
            "external_fact_id": b.external_fact_id, "material_id": b.material_id,
            "support_role": b.support_role, "semantics": b.support_semantics,
            "authorization_path": b.authorization_path,
            "payload_ref": (dict(b.payload_ref) if b.payload_ref else None),
            "locator_ref": (dict(b.locator_ref) if b.locator_ref else None)}
            for b in bundle.accepted_bindings],
        "authority_facts": [
            {"authority_kind": e.authority_kind, "container_id": e.container_identity,
             "fact_id": e.fact_id, "text": e.text, "topic_id": e.topic_id,
             "period": e.period, "scope": e.scope, "fact_type": e.fact_type}
            for e in authority_facts(bundle)],
        "materials": rows,
        "located_atoms": located,
        "output_schema": {
            "per_sentence": [{"sentence_id": "...", "atoms": [
                {"atom_kind": " | ".join(NS.FINAL_SENTENCE_ATOM_KINDS),
                 "atom_surface": "<句子里逐字出现的字>", "claim_id": "<已接受 Claim id 或 null>",
                 "accepted_binding_ids": ["<factual 支撑边 id>"],
                 "verdict": " | ".join(NS.FINAL_SENTENCE_ATOM_VERDICTS),
                 "reason_code": "null | " + " | ".join(NS.FINAL_SENTENCE_ATOM_REASON_CODES),
                 "rationale": "<一句话>"}]}],
            "verdict": " | ".join(NS.FINAL_SENTENCE_DECISION_VERDICTS),
            "reason_code": "null | " + " | ".join(NS.FINAL_SENTENCE_DECISION_REJECTION_REASONS),
        },
    }, ensure_ascii=False, indent=2)
    return [{"role": "user", "content": user}], system


# ---------------------------------------------------------------------------
# 判定面
# ---------------------------------------------------------------------------

def _parse_json_object(text: str, *, what: str) -> Mapping:
    """严格解析模型输出：只容忍**整整一层** markdown 围栏，其余一律 fail-closed。"""
    stripped = str(text or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(?P<body>.*?)\s*```", stripped, re.DOTALL)
    if fenced is not None:
        stripped = fenced.group("body")
    try:
        payload = json.loads(stripped)
    except (TypeError, ValueError) as exc:
        raise FinalSentenceFidelityError(
            f"{what}输出不是合法 JSON（fail-closed，不伪造决定）：{str(exc)[:120]}") from exc
    if not isinstance(payload, Mapping):
        raise FinalSentenceFidelityError(f"{what}输出必须是 JSON 对象")
    return payload


def _parse_atom_row(raw: Any, *, sentence_id: str, what: str) -> NS.SentenceAtomReading:
    if not isinstance(raw, Mapping):
        raise FinalSentenceFidelityError(f"{what} 的原子行必须是 JSON 对象")
    unknown = sorted(set(raw) - {"atom_kind", "atom_surface", "claim_id", "accepted_binding_ids",
                                 "verdict", "reason_code", "rationale"})
    if unknown:
        raise FinalSentenceFidelityError(f"{what} 的原子行含未登记字段 {unknown}（不得自造字段）")
    try:
        return NS.SentenceAtomReading(
            sentence_id=sentence_id, atom_kind=str(raw.get("atom_kind") or ""),
            atom_surface=str(raw.get("atom_surface") or ""), claim_id=raw.get("claim_id"),
            accepted_binding_ids=tuple(raw.get("accepted_binding_ids") or ()),
            verdict=str(raw.get("verdict") or ""), reason_code=raw.get("reason_code"))
    except NS.NarrativeSchemaError as exc:
        raise FinalSentenceFidelityError(f"{what} 的原子行不合法：{exc}") from exc


def interpret_sentence_fidelity_output(
        payload: Mapping, *, sentence_ids: Sequence[str],
        located_by_sentence: Mapping[str, Sequence[str]],
        ) -> tuple[tuple[NS.SentenceAtomReading, ...], str, str | None]:
    """把模型输出**严格**解释成 `(逐原子读数, 聚合 verdict, 聚合 reason_code)`。

    **纯函数**：只读输入，不调用模型、不写任何东西、不碰 bundle。它的每一处失败都是
    `FinalSentenceFidelityError`（fail-closed），因为本门没有「机械故障」这一档判决——
    把它写成语义 `rejected` 就是伪造一条审核结论。

    `located_by_sentence` 是机械定位读数（`sentence_id -> 表面序列`，**只作辅助**）。它的作用
    只有一条、不可省：**已定位的表面必须被逐条认领**。因此「机械抽取器没发现原子」不会被读成
    「这一句没有事实」——但反过来，模型**必须自己补出**抽取器看不见的原子（`约` / 换掉的主体
    或指标 / 丢掉的口径限定语），这一条只能由判定面承担，因为给它加词表就是明令禁止的
    「写死指标名与答案关键词」。
    """
    wanted = [str(s) for s in sentence_ids]
    expected_located = {str(k): [str(v) for v in vals]
                        for k, vals in located_by_sentence.items()}
    if sorted(expected_located) != sorted(wanted):
        raise FinalSentenceFidelityError(
            "定位读数的句子集与本次核验的句子集不等：辅助读数不得漏句或多句")
    per_sentence = payload.get("per_sentence")
    if not isinstance(per_sentence, Sequence) or isinstance(per_sentence, (str, bytes)):
        raise FinalSentenceFidelityError("最终句语义核验输出的 per_sentence 必须是数组")
    got_ids: list[str] = []
    atoms: list[NS.SentenceAtomReading] = []
    for row in per_sentence:
        if not isinstance(row, Mapping):
            raise FinalSentenceFidelityError("per_sentence 的每一项必须是 JSON 对象")
        sid = str(row.get("sentence_id") or "")
        if not sid:
            raise FinalSentenceFidelityError("per_sentence 的每一项必须给出 sentence_id")
        if sid not in expected_located:
            raise FinalSentenceFidelityError(
                f"per_sentence 出现未被本 revision 覆盖的句子 {sid!r}："
                "决定只能对它所覆盖的句子集下判断")
        if sid in got_ids:
            raise FinalSentenceFidelityError(f"per_sentence 的句子 {sid!r} 出现多次")
        got_ids.append(sid)
        raw_atoms = row.get("atoms")
        if not isinstance(raw_atoms, Sequence) or isinstance(raw_atoms, (str, bytes)):
            raise FinalSentenceFidelityError(f"句子 {sid!r} 的 atoms 必须是数组")
        rows = [_parse_atom_row(raw, sentence_id=sid, what=f"句子 {sid!r}")
                for raw in raw_atoms]
        # 「未发现原子」不得自动通过：机械定位出的每一条表面都必须被逐条认领。
        claimed = {a.atom_surface for a in rows}
        unclaimed = [s for s in expected_located[sid] if s not in claimed]
        if unclaimed:
            raise FinalSentenceFidelityError(
                f"句子 {sid!r} 有 {len(unclaimed)} 条机械定位出的原子未被输出认领"
                f"（{unclaimed[:5]}）：定位读数只作辅助，但已定位的表面不得静默消失"
                "——「未发现原子」不是「这一句没有事实」")
        atoms.extend(rows)
    if tuple(got_ids) != tuple(wanted):
        raise FinalSentenceFidelityError(
            f"最终句核验的覆盖面与当前正文不等：输出 {got_ids[:5]}…，"
            f"应为 {wanted[:5]}…（漏审/多审/顺序不同一律 fail-closed）")

    bad = [a for a in atoms if a.verdict != "entailed"]
    located_only = [a for a in bad if a.verdict == "atom_not_located"]
    derived_verdict = "entailed" if not bad else "rejected"
    derived_reason: str | None = None
    if bad:
        derived_reason = ("atom_not_located" if len(located_only) == len(bad)
                          else "atom_not_entailed")
    declared_verdict = payload.get("verdict")
    declared_reason = payload.get("reason_code")
    if declared_verdict != derived_verdict or declared_reason != derived_reason:
        raise FinalSentenceFidelityError(
            f"输出的聚合结论（{declared_verdict!r}/{declared_reason!r}）与逐原子结果推出的"
            f"（{derived_verdict!r}/{derived_reason!r}）不符：聚合必须由逐原子结果推出，"
            "不得与它们相反（也不得用一个更宽松的聚合掩盖未通过的原子）")
    return tuple(atoms), derived_verdict, derived_reason


def evaluate_final_sentence_fidelity(
        bundle: SentenceFidelityBundle, *, llm_client: SentenceFidelityClient,
        model_policy: str = FINAL_SENTENCE_FIDELITY_MODEL_POLICY,
        ) -> NS.FinalSentenceFidelityDecision:
    """对**一个** draft revision 的最终句集输出**恰好一个**语义决定（至多一次 LLM 调用）。"""
    if not isinstance(bundle, SentenceFidelityBundle):
        raise FinalSentenceFidelityError(
            "evaluate_final_sentence_fidelity 的 bundle 必须是 SentenceFidelityBundle")
    if model_policy not in PW.MODEL_POLICIES:
        raise FinalSentenceFidelityError(
            f"model policy {model_policy!r} 不在 {list(PW.MODEL_POLICIES)} 内")
    sentence_ids = bundle.sentence_ids()
    if not sentence_ids:
        raise FinalSentenceFidelityError(
            "本 revision 没有任何承载事实的最终句：本门无对象可核，不得凭空产出决定"
            "（空正文的合法性由 draft 的缺口登记与组装器裁决，不由本门代替）")
    # 调用前把材料正文逐项复核一次（束可能被手工构造）。
    if material_members(bundle):
        if len(bundle.resolved_materials) != len(material_members(bundle)):
            raise FinalSentenceFidelityError(
                "本 revision 绑定了精确材料但没有已解析正文：不得在看不到正文时调用语义核验")
        try:
            recheck = MC.readings_for_manifest_members(
                material_context=bundle.material_context, members=material_members(bundle))
        except MC.MaterialContextError as exc:
            raise FinalSentenceFidelityError(
                f"支撑边材料的正文在调用前复核失败（{exc.reason}，成员 {exc.member_ref!r}）："
                f"{exc}") from exc
        if tuple(recheck) != tuple(bundle.resolved_materials):
            raise FinalSentenceFidelityError(
                "已解析正文与调用前复核结果不是同一串：材料上下文在构造之后被改动，"
                "拒绝在换过的正文上做语义核验")

    messages, system = build_sentence_fidelity_messages(bundle)
    result = llm_client.evaluate(messages=messages, system=system,
                                 prompt_version=FINAL_SENTENCE_FIDELITY_PROMPT_VERSION,
                                 model_policy=model_policy)
    if not isinstance(result, PW.NarrationResult):
        raise FinalSentenceFidelityError(
            "最终句核验 client 必须返回结构化的 NarrationResult（调用元数据不可丢）")
    if result.status != "ok":
        raise FinalSentenceFidelityUnavailable(
            f"最终句核验调用失败（status={result.status!r}）：{result.error}；"
            "不伪造决定（封闭原因码里没有『调用失败』）")
    if result.prompt_version != FINAL_SENTENCE_FIDELITY_PROMPT_VERSION:
        raise FinalSentenceFidelityUnavailable(
            f"实际调用使用的 prompt 版本 {result.prompt_version!r} 与记录 "
            f"{FINAL_SENTENCE_FIDELITY_PROMPT_VERSION!r} 不一致")
    expected_model = PW.resolve_model_policy(model_policy)
    if result.model != expected_model:
        raise FinalSentenceFidelityUnavailable(
            f"实际调用模型 {result.model!r} 与 model policy {model_policy!r} 解析出的 "
            f"{expected_model!r} 不一致（不得一个写在决定、另一个实际调用）")

    # 输出面（解析、认领、聚合一致性）的失败**同样**意味着「本门未能形成决定」：模型给出的
    # 不是一份可核验的读数。它们是调用档而不是判决档——判决只有 `rejected` 一条，且必须由
    # 逐原子读数承载。
    try:
        atoms, verdict, reason_code = interpret_sentence_fidelity_output(
            _parse_json_object(result.text, what="最终句语义核验"), sentence_ids=sentence_ids,
            located_by_sentence=located_readings(bundle))
    except FinalSentenceFidelityError as error:
        if isinstance(error, FinalSentenceFidelityUnavailable):
            raise
        raise FinalSentenceFidelityUnavailable(
            f"最终句核验输出不可用：{error}") from error
    return NS.FinalSentenceFidelityDecision.create(
        task_id=bundle.narrative.task_id, section_id=bundle.narrative.section_id,
        draft_id=bundle.narrative.section_draft_id,
        draft_revision=bundle.narrative.draft_revision,
        narrative_id=bundle.narrative.narrative_id, sentence_ids=sentence_ids,
        atoms=atoms, support_set_digest=bundle.support_set_digest(),
        rubric_version=FINAL_SENTENCE_FIDELITY_RULES_VERSION,
        prompt_version=FINAL_SENTENCE_FIDELITY_PROMPT_VERSION, model_policy=model_policy,
        verdict=verdict, reason_code=reason_code, call_id=result.call_id)


def located_readings(bundle: SentenceFidelityBundle) -> dict[str, tuple[str, ...]]:
    """本束逐句的机械定位读数（`sentence_id -> 表面序列`）。

    它**只**用于两件事：进输入面供模型认领、以及判定面复核「已定位的表面是否被逐条认领」。
    它不产生任何判定，也不参与聚合。
    """
    claim_texts = bundle.claim_texts()
    return {sentence.sentence_id: tuple(
        a.atom_surface for a in locate_fact_atoms(sentence_id=sentence.sentence_id,
                                                  text=sentence.text, claim_texts=claim_texts))
        for sentence in bundle.sentences()}


def map_decisions_to_narratives(
        narratives: Sequence[NS.SectionNarrative],
        decisions: Sequence[NS.FinalSentenceFidelityDecision],
        ) -> tuple[tuple[NS.SectionNarrative, NS.FinalSentenceFidelityDecision], ...]:
    """批量协调器的**确定性映射**：每个 draft revision 配到它**唯一**的决定。

    断言（任一不成立即拒）：每个 narrative 恰有一个决定；没有第二个决定；决定不**过期**
    （锚点与覆盖面都比）；决定不指向别的 revision。对应关系只按 `draft_id` 建立，**不**按
    顺序配对（顺序配对会在丢一条时静默错配）。
    """
    keys = [str(n.section_draft_id) for n in narratives]
    if len(set(keys)) != len(keys):
        raise FinalSentenceFidelityError("输入里有重复的 draft_id：同一 revision 只能核验一次")
    by_draft: dict[str, list[NS.FinalSentenceFidelityDecision]] = {}
    for decision in decisions:
        by_draft.setdefault(str(decision.draft_id), []).append(decision)
    leftover = sorted(set(by_draft) - set(keys))
    if leftover:
        raise FinalSentenceFidelityError(
            f"决定指向的 draft {leftover} 不在输入里：不得把无对应正文的决定静默丢掉")
    out: list[tuple[NS.SectionNarrative, NS.FinalSentenceFidelityDecision]] = []
    for narrative in narratives:
        rows = by_draft.get(str(narrative.section_draft_id), [])
        if len(rows) != 1:
            raise FinalSentenceFidelityError(
                f"draft {narrative.section_draft_id} 配到 {len(rows)} 条最终句决定："
                "每个 draft revision 必须恰有一条")
        decision = rows[0]
        if decision.is_stale_for(
                draft_id=narrative.section_draft_id, section_id=narrative.section_id,
                draft_revision=narrative.draft_revision, narrative_id=narrative.narrative_id,
                sentence_ids=NS.fact_bearing_sentence_ids(narrative)):
            raise FinalSentenceFidelityError(
                f"draft {narrative.section_draft_id} 的最终句决定已过期："
                "改句、改 Claim、换绑定都会改句子身份，过期结论不得当作有效")
        out.append((narrative, decision))
    return tuple(out)


def evaluate_final_sentence_fidelities(
        bundles: Sequence[SentenceFidelityBundle], *,
        llm_client: SentenceFidelityClient,
        model_policy: str = FINAL_SENTENCE_FIDELITY_MODEL_POLICY,
        on_call: Callable[[SentenceFidelityBundle], None] | None = None,
        ) -> tuple[NS.FinalSentenceFidelityDecision, ...]:
    """对一条 Draft 的全部承载事实最终句逐个 revision 核验（每个至多一次调用）。

    `on_call` 只用于观测（每次调用前回调），不参与判定。
    """
    seen: set[str] = set()
    out: list[NS.FinalSentenceFidelityDecision] = []
    for bundle in bundles:
        key = str(bundle.narrative.section_draft_id)
        if key in seen:
            raise FinalSentenceFidelityError(
                f"同一 draft {key} 在输入束里出现多次：会产出两条最终句决定，"
                "而每个 draft revision 恰好一条")
        seen.add(key)
        if on_call is not None:
            on_call(bundle)
        out.append(evaluate_final_sentence_fidelity(bundle, llm_client=llm_client,
                                                    model_policy=model_policy))
    return tuple(out)


# ---------------------------------------------------------------------------
# 定稿路径接线（§12.4.4 第 4 步）：drive → 状态派生 → typed block
# ---------------------------------------------------------------------------

#: 本门 block 的原因码**唯一**定义在 wire 模块（与 `rubric_version` 同一纪律：只 re-export）。
FINAL_SENTENCE_BLOCK_REASONS: tuple[str, ...] = NS.FINAL_SENTENCE_BLOCK_REASONS

#: block 的状态档：它陈述的是「本节权威输入 / 核验结果不足以支撑定稿」，**不是**对现实世界的
#: 断言（与写入侧 `period_unresolved` 同类缺口同一档）。
FINAL_SENTENCE_BLOCK_STATE = "NOT_PROVIDED"


def drive_final_sentence_gate(
        *, narrative: NS.SectionNarrative, claims: Sequence[Any],
        accepted_bindings: Sequence[NS.AcceptedSupportBinding], authority: Any = None,
        manifest: NS.WriterMaterialManifest | None = None, material_context: Any = None,
        llm_client: SentenceFidelityClient,
        model_policy: str = FINAL_SENTENCE_FIDELITY_MODEL_POLICY,
        on_call: Callable[[SentenceFidelityBundle], None] | None = None,
        ) -> tuple[tuple[NS.FinalSentenceFidelityDecision, ...], str]:
    """定稿路径的**唯一**驱动入口：对一份 final Narrative 产出至多一条决定。

    返回 `(decisions, undecided_reason)`：

    * 正文里**没有**承载事实的最终句 ⇒ 返回 `((), "")`，**且不发起任何调用**——本门无对象可核，
      空正文的合法性由 Draft 的缺口登记与组装器裁决（判定面同样拒绝为空的句子集产出决定）。
    * 形成了决定 ⇒ `(一条决定, "")`。
    * **未能形成决定**（调用失败 / 输出不可解析 / 已定位原子未被认领）⇒ 返回 `((), 原因)`，
      由调用方落成 `final_sentence_decision_missing` 的 typed block。它**不**伪造判决，也**不**
      静默通过。

    输入面自身的缺陷（跨 revision、缺 Claim、材料正文对不上、prompt 资产漂移）**不**接住，
    照常上抛：那是接线缺陷，不是「这一轮没核出来」。
    """
    if llm_client is None:
        # 与 `RE.evaluate_claim_chain` 拒绝 None 同一纪律：本门**在场**才谈得上「核过了没有」。
        # 缺注入不是「本节无需核验」，也不能落成一条 `missing` 的业务阻断结论——那会把一次
        # 接线缺陷写成业务事实。接线缺陷当场抛出。
        raise FinalSentenceFidelityError(
            "最终句语义门必须注入 llm_client：缺注入时本门无法形成决定，"
            "而「没有决定」不得被读成「已核验」（fail-closed）")
    if not NS.fact_bearing_sentence_ids(narrative):
        return (), ""
    factual = tuple(sorted(
        (b for b in (accepted_bindings or ()) if str(b.support_semantics) == "factual"),
        key=lambda b: str(b.accepted_support_binding_id)))
    bundle = SentenceFidelityBundle(
        # 授权面收窄由唯一入口 `NS.gate_claim_surface` 做（与 `NS.final_sentence_gate_state`、
        # 组装器、Store 读回同一个面）：本门核的是**正文**，不是调用方手里的整节 Claim 集。
        narrative=narrative, claims=NS.gate_claim_surface(narrative, claims),
        accepted_bindings=factual,
        authority=authority, manifest=manifest, material_context=material_context)
    try:
        decisions = evaluate_final_sentence_fidelities(
            (bundle,), llm_client=llm_client, model_policy=model_policy, on_call=on_call)
    except FinalSentenceFidelityUnavailable as error:
        return (), str(error)
    return decisions, ""


def final_sentence_block_unresolved(
        *, section_id: str, narrative: NS.SectionNarrative,
        decisions: Sequence[NS.FinalSentenceFidelityDecision],
        claims: Sequence[Any], accepted_bindings: Sequence[NS.AcceptedSupportBinding],
        ) -> SS.SectionUnresolved | None:
    """门后 typed block：节正文的最终句门**没有**一份有效决定时，如实登记一条阻断缺口。

    判据**不**在本函数里：它与组装器、Store 读回共用 `NS.final_sentence_gate_state`（唯一实现）。
    本函数只做两件事：把 `(reason_code, detail)` 变成 `SectionUnresolved`，并把后果字段钉成
    `SECTION_BLOCKED`（因此章节状态由唯一口径 `derive_status` 派生为 `SECTION_BLOCKED`，不是靠
    本函数自己宣称）。

    **`detail` 与 `unresolved_id` 只能由确定性状态派生给出**（它只吃正文、决定、Claim 与支撑边）。
    这一点是硬约束而不是风格：本 block 会在**三个**地方各重算一次（定稿协调器 / 组装器 / Store
    读回），`unresolved_id` 是内容寻址的（`detail` 是它的输入之一），只要有一处多写一句「本轮
    为什么没核出来」，三处就会算出三条不同的 id，而「同一个缺口集合」这句话随即失去含义。

    因此「未形成决定」的调用现场记录**不**进缺口：它随 `drive_final_sentence_gate` 的第二个
    返回值单独带出（`BackboneSectionWriterOutput.final_sentence_gate_note`），在只读展示里如实
    呈现为「核验未完成」的现场记录，而不是缺口文案或任何事实断言。
    """
    if not isinstance(narrative, NS.SectionNarrative):
        raise FinalSentenceFidelityError(
            "final_sentence_block_unresolved.narrative 必须是 SectionNarrative")
    reason, detail = NS.final_sentence_gate_state(
        narrative=narrative, decisions=decisions, claims=claims,
        accepted_bindings=accepted_bindings)
    if reason is None:
        return None
    if reason not in FINAL_SENTENCE_BLOCK_REASONS:
        raise FinalSentenceFidelityError(
            f"最终句门状态派生返回了未登记的原因码 {reason!r}："
            f"block 原因码是封闭集合 {list(FINAL_SENTENCE_BLOCK_REASONS)}")
    # 缺口文案不得携带**未经绑定的数字**（与写入侧缺口同一纪律）：`detail` 由 wire 层的唯一
    # 状态派生给出（它自己就不写条数与 id），因此这里的数字只允许来自**身份标识**（版本号）。
    try:
        PW._digits_free(detail, "最终句门 block", section_id,
                        NS.FINAL_SENTENCE_RULES_VERSION,
                        NS.FINAL_SENTENCE_DECISION_SCHEMA_VERSION)
    except PW.PackWriterError as error:
        raise FinalSentenceFidelityError(f"最终句门 block 的文案不合格：{error}") from error
    return SS.SectionUnresolved(
        unresolved_id=PW.derive_unresolved_id(
            section_id, "", "", FINAL_SENTENCE_BLOCK_STATE, reason, detail),
        section_id=section_id, topic_id="", question_id="",
        state=FINAL_SENTENCE_BLOCK_STATE, reason_code=reason, detail=detail,
        impact_scope=(), blocking_effects=("SECTION_BLOCKED",), attempted_sources=())

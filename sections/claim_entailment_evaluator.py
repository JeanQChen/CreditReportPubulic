"""P9 Claim 级原子语义核验（`DESIGN_V2.md` §0.13 第 6 条 / §6.3.2，**唯一所有者**裁决 P1-6）。

用法：`from sections import claim_entailment_evaluator as CEE`

职责（且仅此）：对每个**已通过机械门**的 factual `(claim_candidate_id, draft_revision)`
输出**恰好一个**版本化 `ClaimEntailmentDecision`。

* 输入：factual candidate revision（候选 + 其完整 factual 支撑边 + 边解析出的权威事实/精确
  材料）+ 该 subject **唯一通过的** aggregate `ClaimBindingDecision`；
* 输出：`verdict ∈ {entailed, rejected}` 与封闭 `reason_code`，绑定同一 `support_set_digest`、
  rubric/prompt/model 版本与 call id；
* context `NarrativeDraftUnit` **不进入**本门（零条决定），aggregate 未通过的 factual
  candidate 也是**零条**（失败 aggregate 自身就是该机械拒绝的 typed audit）；
* 本模块**不**重复机械 ID/hash/locator/manifest 校验（那是 P8 的职责），**不**新建 LLM
  runtime（复用 `llm/client.py`，经 `pack_writer.LlmNarrationClient` 这一**唯一**传输适配器），
  也**不**改写候选、不写 Pack、不写库。

fail-closed 清单：

| 情形 | 行为 |
|---|---|
| aggregate `result != "pass"` | 不调用 LLM、不形成决定，抛 `ClaimEntailmentError` |
| aggregate 的 digest 与本地重算不一致 | 拒绝运行，**不得**伪造决定（抛） |
| candidate 不是 factual subject（context 挂到候选上） | 抛 |
| prompt 资产自报 (资产名, revision) 或正文指纹与登记值不符 | **调用前**抛（不消耗调用预算） |
| 传输失败 / 输出不是合法 JSON / verdict、reason_code 越界 | 抛（**不**伪造一条 rejected：封闭原因码里没有「调用失败」，把机械故障写成语义判决就是伪造证据） |
| 每次调用 | 至多一次、只降级不升级（拿不准一律 rejected）；不重试 |

`authority_fact_keys` 的编码：与 FND 的集合键同一形状 `(authority_kind, container_identity,
authority_specific_fact_id)`，经 `content_id` 编码为不自描述但**唯一**的键；**禁止**裸 fact id
跨容器/kind 去重（同一 fact id 落在两个容器就是两条不同的授权事实）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence

# 与 `pack_writer` 同一句法：LLM 传输只有 `llm/client.py` 一份实现（不新建 runtime）。
from llm import client as llm
from sections import claim_binding_gate as CBG
from sections import material_context as MC
from sections import narrative_schema as NS
from sections import pack_writer as PW

#: prompt 资产名与修订号（资产**自报**的值必须与它们逐字一致）。
#: `cer-2`（M930-3.2 §三）：新增 `non_atomic_claim` 拒绝码并要求**原子性先于蕴含**——
#: 一条候选里出现两个可分别判断真假的断言时，本门必须拒绝而不是替它做蕴含判断。
#: 旧资产 `claim_entailment_evaluator_v1` 不原位修改。
#: `cer-3`（M930-3 返修 ⑤）：**逐字镜像分支**。真实 run r4 里，同形的四个利息保障倍数候选
#: （c17 2023 / c18 2024 / c19 2025 / c20 2026Q1，均走路径 A、文本逐字等于**单条**权威事实
#: 自身的 `text`，含权威自己附上的口径限定语「代理口径（PROXY_FINANCE_EXPENSES）」）被判出
#: 两种相反结论：2023 / 2025 / 2026Q1 `entailed`，2024 `rejected` + `non_atomic_claim`
#: （理由：数值断言与代理口径标注是两个可独立判断真假的命题）。该拒绝使 2024 的必需事实
#: 无 Claim 可引用，整节随之中止。判据本身没有错（拆得开就是拆得开），错在把**权威自己**的
#: 口径限定语当成候选添加的第二个断言：候选逐字镜像单条权威事实时，限定语是在给同一个数字
#: 定口径。因此 `cer-3` 的原子性规则拆成两支：一般语义判据不变；`match_count == 1`
#: （候选逐字等于**恰好一条** `authority_facts[].text`）时不判 `non_atomic_claim`，
#: 其余原因码与 fail-closed 一律不变。`-> 0` 与 `> 1` 都**不**触发该分支（不指向唯一一条时
#: 回到语义判据）。旧资产 `claim_entailment_evaluator_v2`（`cer-2`）不原位修改。
#: `cer-4`（M930-3 定点返修 r6 后 ①）：**镜像读数的标点归一**。真实 run r6 里这条豁免
#: **一次都没有生效**：财务节 24 条候选的 `authority_fact_mirror.match_count` 全是 `0`
#: （日志逐条复核），因为 Writer 资产按自己的纪律**要求候选不带句末标点**（`proposals-5`
#: 起即如此），于是
#:    权威 `2023年度的利息保障倍数为-9.94。代理口径（PROXY_FINANCE_EXPENSES）。`
#:    候选 `2023年度的利息保障倍数为-9.94，代理口径（PROXY_FINANCE_EXPENSES）`
#: 这类**只差句读标点**的镜像被判成「不是镜像」，落回一般语义判据；同一形状在这个判据下
#: 判出了两种相反结论（r6 的 23 条 `entailed` + 2023 一条 `rejected`/`non_atomic_claim`，
#: 而 2025 同形候选 `entailed`），必需事实因而无 Claim 可引，整节中止——正是 `cer-3` 要
#: 消掉的不一致。`cer-3` 的判据没错（拆得开就是拆得开），错在镜像读数用**逐字**相等去比一条
#: 被契约要求「不带句末标点」的候选。因此 `cer-4` 把镜像读数的比较口径改成**标点归一读视图**：
#: 两侧各自先把**句读标点**（`。` `，` `、` `；` `：` 与半角写法）归一，再逐字比较；**实词**
#: （数字、单位、期间、主体、括号与口径限定语）仍必须逐字相同。错误数值、错误期间、遗漏代理
#: 口径限定语一律仍不相等 ⇒ `match_count != 1` ⇒ 回到一般语义判据。`cer-3` 资产
#: `claim_entailment_evaluator_v3` 不原位修改，只作只读基线。
CLAIM_ENTAILMENT_PROMPT_ASSET = "claim_entailment_evaluator_v4"
CLAIM_ENTAILMENT_PROMPT_REVISION = "cer-4"
CLAIM_ENTAILMENT_PROMPT_VERSION = (
    f"{CLAIM_ENTAILMENT_PROMPT_ASSET}@{CLAIM_ENTAILMENT_PROMPT_REVISION}")
#: 资产正文指纹（行尾归一后 sha256）：资产名与修订号可以被照抄，正文不会。
CLAIM_ENTAILMENT_PROMPT_SHA256 = (
    "09e1a576fde0a567133917f436f7b217c194ee7ec723185ce551b548e7d86fac")

#: `cer-4` 的镜像读数（`mir-2`）唯一的标点归一集合：**句读标点**，全角与半角写法都算。
#: 刻意只收句读标点：括号（`（PROXY_FINANCE_EXPENSES）` 这类口径限定语的载体）、引号、
#: 百分号、数字与字母一律不在此列，必须逐字相同才配叫镜像。ASCII `.` 同样**不**在此列——
#: 它在正文里是小数点的一部分（`-9.94`），把它当分隔符会把 `-9.94` 与 `-9，94` 抹成同一个
#: 读数，正是「错误数值仍须拒绝」最容易被绕开的地方。
_MIRROR_SENTENCE_PUNCTUATION = frozenset("。，、；：,;:！？!?…")

#: 语义核验规则版本 = wire 的 `ClaimEntailmentDecision.rubric_version` 缺省值。
#: 它**唯一**定义在 wire 模块；本模块是它的规则所有者，只 re-export（避免两处版本漂移）。
CLAIM_ENTAILMENT_RULES_VERSION: str = NS.CLAIM_ENTAILMENT_RULES_VERSION

#: 本门的 model policy：缺省与 Writer 同口径（provider_default），可被调用方按批次覆盖
#: （离线/桩测试用 `stub`）。它**不**复用 `WriterPolicy` 状态，只是同一个封闭词表的取值。
CLAIM_ENTAILMENT_MODEL_POLICY = PW.MODEL_POLICY_PROVIDER_DEFAULT

#: 资产自报的 `(资产名, revision)` 声明（与 Writer 资产同一约定）。
_PROMPT_DECL_RE = PW._PROMPT_DECL_RE


class ClaimEntailmentError(Exception):
    """语义核验的输入/资产/调用 fail-closed（**不是**一条决定）。"""


class EntailmentClient(Protocol):
    """本门能拿到的全部外部能力：一次结构化生成调用。没有检索/工具入口。"""

    def evaluate(self, *, messages: Sequence[Mapping[str, str]], system: str,
                 prompt_version: str, model_policy: str) -> PW.NarrationResult: ...


class LlmEntailmentClient:
    """把既有 Writer 传输适配器接成本门的 client（**不**新建第二套 LLM runtime）。

    传输、结构化调用元数据与失败记账（`NarrationResult`）只有一份实现；本类只换名字与
    语义用途，不复制 `llm/client.py` 的调用路径。
    """

    def __init__(self, *, model: str | None = None, max_tokens: int = 4096,
                 thinking: dict | None = None,
                 narration_client: PW.NarrationClient | None = None) -> None:
        self._inner = narration_client or PW.LlmNarrationClient(
            model=model, max_tokens=max_tokens, thinking=thinking)

    def evaluate(self, *, messages: Sequence[Mapping[str, str]], system: str,
                 prompt_version: str, model_policy: str) -> PW.NarrationResult:
        return self._inner.narrate(messages=messages, system=system,
                                   prompt_version=prompt_version, model_policy=model_policy)


# ---------------------------------------------------------------------------
# prompt 资产：每次调用前从**资产字节**重算指纹
# ---------------------------------------------------------------------------

def verify_claim_entailment_prompt_asset(text: str | None = None) -> str:
    """核对「记录版本 = 实际加载的 prompt 资产」，并返回资产正文。

    `text=None` 时从 `llm/prompts/<asset>.txt` 现读（复用 `llm.load_prompt`，不另建加载器）。
    三侧必须一致：登记版本 = 资产自报的 (资产名, revision) = 正文指纹。任何一侧漂移都
    fail-closed，且发生在**任何 LLM 调用之前**。
    """
    if text is None:
        try:
            text = llm.load_prompt(CLAIM_ENTAILMENT_PROMPT_ASSET)
        except FileNotFoundError as exc:
            raise ClaimEntailmentError(
                f"语义核验 prompt 资产缺失：{exc}") from exc
    match = _PROMPT_DECL_RE.search(str(text or ""))
    if not match:
        raise ClaimEntailmentError(
            f"prompt 资产 {CLAIM_ENTAILMENT_PROMPT_ASSET!r} 未自报 (资产名, revision)，"
            "无法证明记录版本与实际加载版本一致")
    declared = f"{match.group('asset')}@{match.group('revision')}"
    if declared != CLAIM_ENTAILMENT_PROMPT_VERSION:
        raise ClaimEntailmentError(
            f"实际加载的 prompt 资产自报 {declared!r}，与登记版本 "
            f"{CLAIM_ENTAILMENT_PROMPT_VERSION!r} 不一致（不得用旧记录冒充）")
    actual = PW.prompt_asset_fingerprint(text)
    if actual != CLAIM_ENTAILMENT_PROMPT_SHA256:
        raise ClaimEntailmentError(
            f"prompt 资产正文指纹 {actual!r} 与登记值 {CLAIM_ENTAILMENT_PROMPT_SHA256!r} "
            f"不一致：资产 {CLAIM_ENTAILMENT_PROMPT_ASSET!r} 的正文被改过"
            "（资产名与修订号可被照抄，正文不行）")
    return text


# ---------------------------------------------------------------------------
# 输入束：factual candidate revision + 指定权威/材料集合
# ---------------------------------------------------------------------------

def authority_fact_key(authority_kind: str, container_identity: str, fact_id: str) -> str:
    """授权事实的集合键：与 FND 同一四元形状 `(kind, container, authority-specific fact)`。"""
    for name, value in (("authority_kind", authority_kind),
                        ("container_identity", container_identity), ("fact_id", fact_id)):
        if not isinstance(value, str) or not value:
            raise ClaimEntailmentError(f"authority_fact_key.{name} 不得为空")
    return NS.content_id("afk_", {"authority_kind": authority_kind,
                                  "container_identity": container_identity,
                                  "fact_id": fact_id})


@dataclass(frozen=True)
class FactualCandidateRevision:
    """一条 factual candidate revision 及其**已解析**的授权来源（本门的完整输入面）。"""

    candidate: NS.ClaimCandidate
    proposals: tuple[NS.ProposedSupportRef, ...]
    authority_facts: tuple[PW.AuthorityFactEntry, ...]
    materials: tuple[NS.WriterMaterialManifestEntry, ...]
    # 解析不到来源的边（权威目录与 exact manifest 都没有它）。这不是「本门可以放过的边」：
    # 一个**通过**机械门的边必然可解析，所以这里的非空只可能出现在机械门已拒绝的候选上，
    # 那时本门不调用 LLM、不生成决定（计划 P9 fail-closed 原文），映射器再作断言。
    unresolved_proposal_ids: tuple[str, ...] = ()
    #: §五：本束材料正文的**唯一**来源——`wmctx-1` 的 `WriterMaterialContext`。绑定 material
    #: carrier 的候选**必须**带着它进场；本类不接受任何自报正文（见 `resolved_materials`）。
    material_context: Any = None
    #: §三 A.6 + §五：`materials` 里每个成员**已解析的真实正文**（`wmctx-1` 的
    #: `ResolvedWriterMaterial`），顺序与 `materials` 一致。本字段是**派生**的（`init=False`），
    #: 调用方**无法注入**：它的唯一来源是 `material_context` 经唯一正文核验入口
    #: `MC.reading_for_manifest_member` 逐项复核 payload / hash / locator / 读视图指纹之后的
    #: 返回值。公开入口因此不再存在「同 `member_ref` + 任意正文 + 自洽重算指纹」这条缝。
    resolved_materials: tuple[Any, ...] = field(init=False, default=())

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, NS.ClaimCandidate):
            raise ClaimEntailmentError(
                "FactualCandidateRevision.candidate 必须是 ClaimCandidate："
                "context NarrativeDraftUnit 不进入语义门")
        object.__setattr__(self, "proposals", tuple(self.proposals))
        object.__setattr__(self, "authority_facts", tuple(self.authority_facts))
        object.__setattr__(self, "materials", tuple(self.materials))
        object.__setattr__(self, "unresolved_proposal_ids",
                           tuple(self.unresolved_proposal_ids))
        object.__setattr__(self, "resolved_materials",
                           _resolved_materials_for(self.materials, self.material_context))
        if self.materials:
            expected_refs = tuple(m.member_ref for m in self.materials)
            actual_refs = tuple(r.member_ref for r in self.resolved_materials)
            if actual_refs != expected_refs:
                raise ClaimEntailmentError(
                    "已解析正文必须与 materials 逐位对应（顺序与 member_ref 都要一致）："
                    f"得到 {actual_refs}，应为 {expected_refs}")
        if not self.proposals:
            raise ClaimEntailmentError(
                "FactualCandidateRevision.proposals 不得为空：没有支撑集的候选没有可核验的授权")
        for proposal in self.proposals:
            if (proposal.binding_subject_kind != "claim_candidate"
                    or proposal.binding_subject_id != self.candidate.candidate_id):
                raise ClaimEntailmentError(
                    "支撑边的 binding subject 与候选不一致（不得跨候选借证）")
            if proposal.support_semantics != "factual":
                raise ClaimEntailmentError(
                    "语义门的支撑边必须全部是 factual：context 边不授权事实，也不进入本门")
        expected = sorted(p.proposed_support_id for p in self.proposals)
        if [p.proposed_support_id for p in self.proposals] != expected:
            raise ClaimEntailmentError(
                "支撑边必须按 proposed_support_id 升序（canonical order）")
        if self.unresolved_proposal_ids != tuple(sorted(set(self.unresolved_proposal_ids))):
            raise ClaimEntailmentError(
                "unresolved_proposal_ids 必须升序去重（集合身份不得取决于调用顺序）")
        if not set(self.unresolved_proposal_ids) <= set(expected):
            raise ClaimEntailmentError(
                "unresolved_proposal_ids 必须是本束支撑边的子集（不得引用别的候选的边）")

    @property
    def subject_revision(self) -> CBG.BindingSubjectRevision:
        return CBG.BindingSubjectRevision.from_subject(self.candidate)

    def proposal_ids(self) -> tuple[str, ...]:
        return tuple(p.proposed_support_id for p in self.proposals)

    def proposal_hashes(self) -> tuple[str, ...]:
        return tuple(p.content_hash() for p in self.proposals)

    def support_set_digest(self, manifest: NS.WriterMaterialManifest) -> str:
        """与 P8 在同一批输入上的**同一** digest（本函数不另立算法）。"""
        return CBG.support_set_digest(
            subject_revision=self.subject_revision, proposal_ids=self.proposal_ids(),
            proposal_hashes=self.proposal_hashes(), manifest_id=manifest.manifest_id,
            manifest_fingerprint=manifest.fingerprint())

    def authority_fact_keys(self) -> tuple[str, ...]:
        """路径 A 边的授权事实键（canonical order、无重复）；路径 B 边不产生事实键。"""
        keys = [authority_fact_key(e.authority_kind, e.container_identity, e.fact_id)
                for e in self.authority_facts]
        if len(set(keys)) != len(keys):
            raise ClaimEntailmentError("authority_fact_keys 含重复（集合身份失效）")
        return tuple(sorted(keys))

    def governing_authorization_path(self) -> str:
        """主导边的授权路径（单值 wire 字段；与 P8 的 `authority_kind` 同一口径）。"""
        for proposal in self.proposals:
            if proposal.support_role == "primary":
                return proposal.authorization_path
        return self.proposals[0].authorization_path


def factual_candidate_revisions(draft: NS.SectionDraft, authority: Any, *,
                                manifest: NS.WriterMaterialManifest | None = None,
                                material_context: Any = None,
                                ) -> tuple[FactualCandidateRevision, ...]:
    """Draft → factual candidate revision 束（canonical order，确定性）。

    每条边都**回查**权威目录与 exact manifest 解析它真正引用的来源。解析不到的边记进
    `unresolved_proposal_ids`（**不**为它猜一个来源，也**不**在这里整链抛出）：一个通过机械门的
    边必然可解析，所以非空只可能出现在机械门已拒绝的候选上，而那条候选按计划 P9 不调用 LLM、
    不生成决定；真正的 fail-closed 断言在 `map_aggregates_to_candidates`（通过的决定配到带未解析
    边的束即拒）与 `evaluate_entailment` 里，这样「机械拒绝 audit」不会被一次本地解析失败吞掉。
    """
    if not isinstance(draft, NS.SectionDraft):
        raise ClaimEntailmentError("factual_candidate_revisions 的 draft 必须是 SectionDraft")
    manifest = manifest if manifest is not None else draft.material_manifest
    fact_table = CBG.authority_fact_table(authority)
    grouped: dict[str, list[NS.ProposedSupportRef]] = {}
    for proposal in draft.proposed_support_refs:
        if proposal.support_semantics != "factual":
            continue
        grouped.setdefault(proposal.binding_subject_id, []).append(proposal)
    out: list[FactualCandidateRevision] = []
    for candidate in sorted(draft.claim_candidates, key=lambda c: c.candidate_id):
        proposals = sorted(grouped.get(candidate.candidate_id, ()),
                           key=lambda p: p.proposed_support_id)
        if not proposals:
            # `SectionDraft` 已经要求每个候选至少一条 factual proposal；这里只作防御。
            raise ClaimEntailmentError(
                f"候选 {candidate.candidate_id} 没有任何 factual 支撑边：不得进入语义门")
        facts: list[PW.AuthorityFactEntry] = []
        materials: list[NS.WriterMaterialManifestEntry] = []
        unresolved: list[str] = []
        for proposal in proposals:
            entry, member = CBG.resolve_proposal_sources(
                proposal, fact_table=fact_table, manifest=manifest)
            if entry is None and member is None:
                unresolved.append(proposal.proposed_support_id)
                continue
            if entry is not None:
                facts.append(entry)
            if member is not None:
                materials.append(member)
        out.append(FactualCandidateRevision(
            candidate=candidate, proposals=tuple(proposals),
            authority_facts=tuple(facts), materials=tuple(materials),
            unresolved_proposal_ids=tuple(sorted(unresolved)),
            material_context=material_context))
    return tuple(out)


def _assert_members_belong_to_manifest(
        materials: Sequence[NS.WriterMaterialManifestEntry],
        manifest: NS.WriterMaterialManifest) -> None:
    """束里的每个材料成员必须**逐字段**等于本次 exact manifest 里的那一条（§五 收口）。

    这是「正文属于本次写作」的最后一颗钉子：`factual_candidate_revisions` 的成员本来就取自
    manifest，但**手工构造**的束可以自带一份自造成员 + 一份与它自洽的自造正文上下文——
    单看「成员 ↔ 正文一致」两者完全自洽，只有把它们与本次 manifest 对齐才能发现那份材料
    从来不属于这次写作。
    """
    for member in materials:
        entry = manifest.entry_for(member.member_ref)
        if entry is None:
            raise ClaimEntailmentError(
                f"材料成员 {member.member_ref!r} 不在本次 exact manifest "
                f"{manifest.manifest_id!r} 里：候选绑定了不属于本次写作的材料，"
                "不得在它上面做语义核验")
        if entry.identity_body() != member.identity_body():
            raise ClaimEntailmentError(
                f"材料成员 {member.member_ref!r} 与本次 exact manifest 里的同名成员不一致"
                "（同一 member_ref、不同的来源/定位/指纹）：拒绝在换过的材料上做语义核验")


def _resolved_materials_for(materials: Sequence[NS.WriterMaterialManifestEntry],
                            material_context: Any) -> tuple[MC.ResolvedWriterMaterial, ...]:
    """束里每个材料成员 → **已解析正文**（缺上下文/正文不一致一律 fail-closed）。

    §三 A.6 + §五：语义门必须拿到候选**实际绑定材料**的同一份真实正文、payload 哈希与
    locator。这是本类里正文的**唯一**来路——它走的正是全链共用的唯一正文核验入口
    `MC.readings_for_manifest_members`（内含 `reading_for_manifest_member` 的逐项比对），
    所以「手工拿一份自洽指纹的伪造正文顶替」在类型层就无从下手（`resolved_materials` 是
    `init=False` 的派生字段）。「拿不到正文」不是一个可以继续运行的降级状态——那会把一条
    从未被任何正文支持过的断言记成 `entailed`。因此这里在**任何 LLM 调用之前**拒绝。
    """
    if not materials:
        return ()
    if material_context is None:
        raise ClaimEntailmentError(
            "候选绑定了 material carrier，但本门没有拿到 wmctx-1 材料正文上下文："
            "语义核验看不到真实正文时不得调用 LLM（只有身份字段的清单不构成证据）")
    try:
        return MC.readings_for_manifest_members(material_context=material_context,
                                                members=tuple(materials))
    except MC.MaterialContextError as exc:
        raise ClaimEntailmentError(
            f"候选绑定材料的正文无法复核（{exc.reason}，成员 {exc.member_ref!r}）：{exc}；"
            "缺正文或 payload/locator/hash 不一致时在调用 LLM 前拒绝") from exc



# ---------------------------------------------------------------------------
# 输入渲染（所有身份字段都在输入面内，模型只能判语义）
# ---------------------------------------------------------------------------

def _entailment_material_rows(bundle: FactualCandidateRevision) -> list[dict]:
    """材料输入行：身份 + payload 哈希 + 精确定位 + **真实正文**（§三 A.6）。

    逐位对应由 `FactualCandidateRevision.__post_init__` 保证；这里再断言一次，因为
    「输入面少了一行正文」会让模型在**看不见的正文**上做语义判断，而输入面自己不会报错。
    """
    if len(bundle.materials) != len(bundle.resolved_materials):
        raise ClaimEntailmentError(
            f"材料成员数与已解析正文数不一致（{len(bundle.materials)} vs "
            f"{len(bundle.resolved_materials)}）：输入面必须逐位对应")
    rows: list[dict] = []
    for member, reading in zip(bundle.materials, bundle.resolved_materials):
        if member.member_ref != reading.member_ref:
            raise ClaimEntailmentError(
                f"材料成员 {member.member_ref!r} 配到 {reading.member_ref!r} 的正文："
                "身份错位即拒（不得张冠李戴）")
        if not reading.reading_view.strip():
            raise ClaimEntailmentError(
                f"材料成员 {member.member_ref!r} 的正文为空：空正文不得作为语义依据")
        rows.append({
            "container_id": member.pack_id, "material_id": member.material_id,
            "member_ref": member.member_ref, "source_identity": member.source_identity,
            "provenance_identity": member.provenance_identity,
            "material_type": member.material_type,
            "locator_ref": dict(member.locator_ref), "payload_hash": member.payload_hash,
            "content_fingerprint": member.material_content_fingerprint,
            "text": reading.reading_view,
            "structured": (dict(reading.structured_view)
                           if reading.structured_view is not None else None),
        })
    return rows


def _mirror_comparison_view(text: Any) -> str:
    """镜像读数专用的**标点归一读视图**（§cer-4，读视图版本 `mir-2`）。

    在 `MC.normalize_reading_view`（换行归一 + 去首尾空白，**不改任何字符**）之上，只多做一件
    事：把**句读标点**归一成同一个分隔符，并去掉首尾的分隔符。理由不是宽容，而是口径：

    * 写作侧的契约（`proposals-5` 起）要求候选写成**不带句末标点**的原子子句；一条权威事实
      自带 `A。B。` 两个句子时，候选按该契约只能写成 `A，B`。用**逐字**相等去比一条被契约
      要求去掉句末标点的候选，会让「逐字镜像」这个读数在真实输出上恒为 0（r6 实测 24/24）。
    * 归一**只动句读标点**：数字、单位、期间、主体、括号、口径限定语等实词一个都不动，因此
      错误数值、错误期间、遗漏 `PROXY_FINANCE_EXPENSES` 限定语一律仍**不**相等。
    * 标点被换成同一个分隔符（而不是直接删除）是刻意的：直接删除会让小数点的差异消失，
      例如 `-9.94` 与 `-9，94` 都会被抹成 `-994` 而假相等。
    """
    view = MC.normalize_reading_view(text)
    out: list[str] = []
    for char in view:
        out.append("\u0001" if char in _MIRROR_SENTENCE_PUNCTUATION else char)
    normalized = "".join(out)
    while "\u0001\u0001" in normalized:
        normalized = normalized.replace("\u0001\u0001", "\u0001")
    return normalized.strip("\u0001")


def _authority_fact_mirror(bundle: FactualCandidateRevision) -> dict:
    """候选文本 vs `authority_facts[].text` 的**内容相等**读数（§cer-3 / §cer-4）。

    这是机械读数，**不是**语义判断：它在**标点归一读视图**（`_mirror_comparison_view`）上做
    逐字比较，回答「候选与几条权威事实自己的文本**内容相同**（只可能差句读标点）」。

    * `match_count == 1`：唯一指向一条权威事实（给出 `verbatim_mirror_of_fact_id` 与它的
      kind / container）。`cer-3` 的原子性规则据此免去**原子性拒绝码**——候选没有添加
      权威文本之外的任何内容（句读标点的增删不增加任何断言），权威附在同一句后的口径限定语
      是**同一个断言**的一部分。`cer-4` 把「逐字」改成「标点归一后内容相等」，因为写作侧的
      契约要求候选不带句末标点，逐字比较在真实输出上恒不成立。
    * `match_count == 0`：候选与任何权威事实都不内容相等 ⇒ 回到一般语义判据。
    * `match_count > 1`：与多条内容相同 ⇒ 指向不唯一，**不给** fact_id，也**不**触发该分支
      （否则「碰巧等于某条」会变成一条可被利用的旁路）。
    """
    candidate = _mirror_comparison_view(getattr(bundle.candidate, "claim_text", ""))
    matches = [e for e in bundle.authority_facts
               if _mirror_comparison_view(getattr(e, "text", "")) == candidate]
    row: dict = {"match_count": len(matches)}
    if len(matches) == 1:
        hit = matches[0]
        row["verbatim_mirror_of_fact_id"] = str(hit.fact_id)
        row["verbatim_mirror_of_authority_kind"] = str(hit.authority_kind)
        row["verbatim_mirror_of_container_id"] = str(hit.container_identity)
    else:
        row["verbatim_mirror_of_fact_id"] = None
        row["verbatim_mirror_of_authority_kind"] = None
        row["verbatim_mirror_of_container_id"] = None
    return row


def build_entailment_messages(bundle: FactualCandidateRevision,
                              aggregate_decision: NS.ClaimBindingDecision,
                              manifest: NS.WriterMaterialManifest) -> tuple[list[dict], str]:
    """本门**唯一**的 LLM 输入：候选 + 唯一通过的机械决定 + 指定权威/材料集合。"""
    system = verify_claim_entailment_prompt_asset()
    candidate = bundle.candidate
    user = json.dumps({
        "claim_candidate": {
            "claim_candidate_id": candidate.candidate_id,
            "draft_revision": candidate.draft_revision,
            "task_id": candidate.task_id, "section_id": candidate.section_id,
            "company_id": candidate.company_id, "report_as_of": candidate.report_as_of,
            "contract_version": candidate.contract_version,
            "contract_fingerprint": candidate.contract_fingerprint,
            "claim_text": candidate.claim_text, "fact_type": candidate.fact_type,
        },
        "binding_decision": {
            "binding_decision_id": aggregate_decision.binding_decision_id,
            "support_set_digest": aggregate_decision.support_set_digest,
            "subject_kind": aggregate_decision.subject_kind,
            "subject_id": aggregate_decision.subject_id,
            "draft_revision": aggregate_decision.draft_revision,
            "manifest_id": aggregate_decision.manifest_id,
            "manifest_fingerprint": aggregate_decision.manifest_fingerprint,
            "proposal_ids": list(aggregate_decision.proposal_ids),
            "edge_results": [e.to_dict() for e in aggregate_decision.edge_results],
            "result": aggregate_decision.result,
            "rules_version": aggregate_decision.rules_version,
        },
        "support_edges": [
            {"proposed_support_id": p.proposed_support_id, "authority_kind": p.authority_kind,
             "container_id": p.authority_container_id, "fact_id": p.fact_id,
             "financial_fact_id": p.financial_fact_id, "note_fact_id": p.note_fact_id,
             "external_fact_id": p.external_fact_id, "material_id": p.material_id,
             "support_role": p.support_role, "support_semantics": p.support_semantics,
             "authorization_path": p.authorization_path}
            for p in bundle.proposals],
        # 权威事实的**逐字文本**是本门唯一的授权事实来源；identity 只用于回查。
        "authority_facts": [
            {"authority_kind": e.authority_kind, "container_id": e.container_identity,
             "fact_id": e.fact_id, "text": e.text, "topic_id": e.topic_id,
             "period": e.period, "scope": e.scope, "fact_type": e.fact_type,
             "required": e.required}
            for e in bundle.authority_facts],
        # §cer-3 / §cer-4：候选文本与权威事实文本的**内容相等**读数（机械算出，不是语义判断）。
        # 它只回答「候选是不是镜像了唯一一条权威事实自己的文本」这个问题——`cer-4` 起在
        # **标点归一读视图**上比较（句读标点不参与，实词逐字参与）；指向不唯一（0 条或多条
        # 相同）时不给出 fact_id。判语义仍是本门 LLM 的职责，本字段不代替它。
        "authority_fact_mirror": _authority_fact_mirror(bundle),
        # §三 A.6：材料以**真实正文**呈现——身份字段只用于回查，语义判断必须看正文本身。
        # `text` 是抽取式读视图（与 Writer 输入面、渲染面同一串字节）；表格类材料另给
        # `structured`。`payload_hash` / `locator_ref` 随之入场，使「模型看过的那一份」与
        # 「本门复核的那一份」可以被逐字对上。
        "materials": _entailment_material_rows(bundle),
        # 输出面：没有 SectionClaim / 最终 Narrative / accepted binding / 决定 ID 的位置。
        "output_schema": {"verdict": "entailed | rejected",
                          "reason_code": "null | <封闭原因码>",
                          "rationale": "<一句话，仅供调用日志>"},
    }, ensure_ascii=False, indent=2)
    return [{"role": "user", "content": user}], system


# ---------------------------------------------------------------------------
# 语义核验
# ---------------------------------------------------------------------------

def _parse_verdict(text: str) -> tuple[str, str | None]:
    """严格解析模型输出：越界、缺字段、非法 JSON 一律 fail-closed 抛出。

    只容忍**整整一层** markdown 围栏（```json … ```）；除此之外不做任何「找 JSON」宽容——
    在多余文本里捞出片段会把「模型答了别的东西」当成有效判定。
    """
    stripped = str(text or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(?P<body>.*?)\s*```", stripped, re.DOTALL)
    if fenced is not None:
        stripped = fenced.group("body")
    try:
        payload = json.loads(stripped)
    except (TypeError, ValueError) as exc:
        raise ClaimEntailmentError(
            f"语义核验输出不是合法 JSON（fail-closed，不伪造决定）：{str(exc)[:120]}") from exc
    if not isinstance(payload, Mapping):
        raise ClaimEntailmentError("语义核验输出必须是 JSON 对象")
    verdict = payload.get("verdict")
    if verdict not in NS.ENTAILMENT_VERDICTS:
        raise ClaimEntailmentError(
            f"verdict={verdict!r} 不在 {list(NS.ENTAILMENT_VERDICTS)} 内")
    reason = payload.get("reason_code")
    if verdict == "entailed":
        if reason is not None:
            raise ClaimEntailmentError("verdict=entailed 不得携带 reason_code")
        return verdict, None
    if reason not in NS.ENTAILMENT_REJECTION_REASONS:
        raise ClaimEntailmentError(
            f"verdict=rejected 的 reason_code={reason!r} 不在 "
            f"{list(NS.ENTAILMENT_REJECTION_REASONS)} 内")
    return verdict, reason


def evaluate_entailment(candidate_revision: FactualCandidateRevision,
                        aggregate_decision: NS.ClaimBindingDecision, *,
                        manifest: NS.WriterMaterialManifest,
                        llm_client: EntailmentClient,
                        model_policy: str = CLAIM_ENTAILMENT_MODEL_POLICY,
                        ) -> NS.ClaimEntailmentDecision:
    """对**一条** factual candidate revision 输出**恰好一个**语义决定（至多一次 LLM 调用）。

    `manifest` 是本次写作的 exact material manifest：digest 必须在**同一** manifest 上重算，
    否则「同一 support-set digest」就是空话。
    """
    if not isinstance(candidate_revision, FactualCandidateRevision):
        raise ClaimEntailmentError(
            "evaluate_entailment 的 candidate_revision 必须是 FactualCandidateRevision")
    if not isinstance(aggregate_decision, NS.ClaimBindingDecision):
        raise ClaimEntailmentError(
            "evaluate_entailment 的 aggregate_decision 必须是 ClaimBindingDecision")
    if aggregate_decision.result != "pass":
        raise ClaimEntailmentError(
            f"aggregate 决定 {aggregate_decision.binding_decision_id} 未通过"
            f"（result={aggregate_decision.result!r}）：不得调用 LLM、不得生成语义决定；"
            "失败 aggregate 本身就是该机械拒绝的 typed audit")
    if candidate_revision.unresolved_proposal_ids:
        raise ClaimEntailmentError(
            "通过的 aggregate 决定配到含未解析支撑边的束"
            f"（{list(candidate_revision.unresolved_proposal_ids)}）：通过的边必然可解析，"
            "这里不一致，拒绝运行而不是用残缺集合做语义核验")
    if (aggregate_decision.subject_kind != "claim_candidate"
            or aggregate_decision.subject_id != candidate_revision.candidate.candidate_id
            or aggregate_decision.draft_revision != candidate_revision.candidate.draft_revision):
        raise ClaimEntailmentError(
            "aggregate 决定与候选 revision 不是同一 subject（跨候选配决定即拒）")
    local_digest = candidate_revision.support_set_digest(manifest)
    if local_digest != aggregate_decision.support_set_digest:
        raise ClaimEntailmentError(
            f"support-set digest 不一致：决定声明 {aggregate_decision.support_set_digest!r}，"
            f"本地重算 {local_digest!r}（拒绝运行，不得伪造决定）")
    if aggregate_decision.manifest_id != manifest.manifest_id:
        raise ClaimEntailmentError("aggregate 决定的 manifest 与本次写作的 exact manifest 不一致")
    if model_policy not in PW.MODEL_POLICIES:
        raise ClaimEntailmentError(
            f"model policy {model_policy!r} 不在 {list(PW.MODEL_POLICIES)} 内")
    # §五：**调用前**重新调用唯一正文核验入口，逐项复核 payload / hash / locator / 读视图指纹。
    # 束可能被手工构造（测试、协调器），所以本门不假设构造期已经查过；而「只比较 member_ref 与
    # 正文非空」不足以证明正文就是 manifest 成员声明的那一份——一份同 `member_ref`、任意正文、
    # 自洽重算指纹的伪造读视图两条都能满足。因此这里把束自带的 `material_context` 再喂回唯一
    # 入口重算一次，任何不一致（含上下文在构造之后被换掉）都在模型调用之前拒绝。
    if candidate_revision.materials:
        if not candidate_revision.resolved_materials:
            raise ClaimEntailmentError(
                "候选绑定 materials 但没有已解析正文：不得在看不到正文的情况下调用语义核验")
        # 成员必须**就是**本次 exact manifest 里的那一条（逐字段相同）。只比对 member_ref 不够：
        # 一个自造成员可以带着同样的 member_ref 与一份自造的（自洽）正文上下文，绕过
        # `factual_candidate_revisions` 直接构造束。把成员钉回 manifest 之后，正文才真正
        # 落在「本次写作消费的那份材料」上。
        _assert_members_belong_to_manifest(candidate_revision.materials, manifest)
        try:
            recheck = MC.readings_for_manifest_members(
                material_context=candidate_revision.material_context,
                members=candidate_revision.materials)
        except MC.MaterialContextError as exc:
            raise ClaimEntailmentError(
                f"候选绑定材料的正文在调用前复核失败（{exc.reason}，成员 {exc.member_ref!r}）："
                f"{exc}；正文、payload、hash、locator 或读视图指纹与 manifest 成员不符时"
                "不得调用 LLM") from exc
        if tuple(recheck) != tuple(candidate_revision.resolved_materials):
            raise ClaimEntailmentError(
                "候选的已解析正文与调用前复核结果不是同一串：材料上下文在构造之后被改动，"
                "拒绝在换过的正文上做语义核验")
        _entailment_material_rows(candidate_revision)

    messages, system = build_entailment_messages(candidate_revision, aggregate_decision, manifest)
    result = llm_client.evaluate(messages=messages, system=system,
                                 prompt_version=CLAIM_ENTAILMENT_PROMPT_VERSION,
                                 model_policy=model_policy)
    if not isinstance(result, PW.NarrationResult):
        raise ClaimEntailmentError(
            "语义核验 client 必须返回结构化的 NarrationResult（不得只回传文本：调用元数据不可丢）")
    if result.status != "ok":
        raise ClaimEntailmentError(
            f"语义核验调用失败（status={result.status!r}）：{result.error}；"
            "不伪造决定（封闭原因码里没有『调用失败』）")
    if result.prompt_version != CLAIM_ENTAILMENT_PROMPT_VERSION:
        raise ClaimEntailmentError(
            f"实际调用使用的 prompt 版本 {result.prompt_version!r} 与记录 "
            f"{CLAIM_ENTAILMENT_PROMPT_VERSION!r} 不一致")
    expected_model = PW.resolve_model_policy(model_policy)
    if result.model != expected_model:
        raise ClaimEntailmentError(
            f"实际调用模型 {result.model!r} 与 model policy {model_policy!r} 解析出的 "
            f"{expected_model!r} 不一致（不得一个写在决定、另一个实际调用）")
    verdict, reason = _parse_verdict(result.text)
    return NS.ClaimEntailmentDecision.create(
        claim_candidate_id=candidate_revision.candidate.candidate_id,
        draft_revision=candidate_revision.candidate.draft_revision,
        binding_decision_id=aggregate_decision.binding_decision_id,
        support_set_digest=aggregate_decision.support_set_digest,
        authorization_path=candidate_revision.governing_authorization_path(),
        authority_fact_keys=candidate_revision.authority_fact_keys(),
        rubric_version=CLAIM_ENTAILMENT_RULES_VERSION,
        prompt_version=CLAIM_ENTAILMENT_PROMPT_VERSION, model_policy=model_policy,
        verdict=verdict, reason_code=reason, call_id=result.call_id)


def map_aggregates_to_candidates(
        candidate_revisions: Sequence[FactualCandidateRevision],
        aggregate_decisions: Sequence[NS.ClaimBindingDecision], *,
        manifest: NS.WriterMaterialManifest,
        ) -> tuple[tuple[FactualCandidateRevision, NS.ClaimBindingDecision], ...]:
    """批量协调器的**确定性映射**：每个 factual candidate 配到它**唯一**通过的 aggregate。

    断言（任一不成立即拒）：每个 candidate revision 恰有一个 aggregate；没有第二个决定；
    digest 与本地重算完全相同；context subject 的决定不进入本门。candidate 与决定的对应关系
    只按 subject 身份建立，**不**按顺序配对（顺序配对会在丢一条时静默错配）。
    """
    bundle_keys: list[tuple[str, str]] = [
        (b.candidate.candidate_id, b.candidate.draft_revision) for b in candidate_revisions]
    if len(set(bundle_keys)) != len(bundle_keys):
        raise ClaimEntailmentError(
            "同一 candidate revision 在输入束里出现多次：会为它调用两次并产出两条 entailment "
            "决定，而同一 candidate revision 恰好一条")
    by_subject: dict[tuple[str, str], list[NS.ClaimBindingDecision]] = {}
    for decision in aggregate_decisions:
        if decision.subject_kind != "claim_candidate":
            continue
        by_subject.setdefault((decision.subject_id, decision.draft_revision), []).append(decision)
    leftover = sorted(set(by_subject) - set(bundle_keys))
    if leftover:
        raise ClaimEntailmentError(
            f"aggregate 决定的 subject {leftover} 不在本批 factual candidate 束里："
            "决定的 subject 集与候选集必须相等，不得把一条无对应候选的决定静默丢掉")
    out: list[tuple[FactualCandidateRevision, NS.ClaimBindingDecision]] = []
    for bundle in candidate_revisions:
        key = (bundle.candidate.candidate_id, bundle.candidate.draft_revision)
        decisions = by_subject.get(key, [])
        if len(decisions) != 1:
            raise ClaimEntailmentError(
                f"候选 {key[0]}（revision {key[1]}）配到 {len(decisions)} 条 aggregate 决定："
                "每个 subject revision 必须恰有一条")
        decision = decisions[0]
        if decision.result != "pass":
            continue    # 机械门未通过：0 条语义决定（不调用 LLM）
        if decision.support_set_digest != bundle.support_set_digest(manifest):
            raise ClaimEntailmentError(
                f"候选 {key[0]} 的 aggregate digest 与本地重算不一致：拒绝运行")
        if bundle.unresolved_proposal_ids:
            raise ClaimEntailmentError(
                f"候选 {key[0]} 的 aggregate 通过，但束里有解析不到来源的边"
                f"（{list(bundle.unresolved_proposal_ids)}）：拒绝运行")
        out.append((bundle, decision))
    return tuple(out)


def evaluate_entailments(
        candidate_revisions: Sequence[FactualCandidateRevision],
        aggregate_decisions: Sequence[NS.ClaimBindingDecision], *,
        manifest: NS.WriterMaterialManifest, llm_client: EntailmentClient,
        model_policy: str = CLAIM_ENTAILMENT_MODEL_POLICY,
        on_call: Callable[[FactualCandidateRevision], None] | None = None,
        ) -> tuple[NS.ClaimEntailmentDecision, ...]:
    """对一条 Draft 的全部 factual candidate revision 逐条核验（每条至多一次调用）。

    `on_call` 只用于观测（每次调用前回调），不参与判定。
    """
    pairs = map_aggregates_to_candidates(candidate_revisions, aggregate_decisions,
                                        manifest=manifest)
    out: list[NS.ClaimEntailmentDecision] = []
    for bundle, decision in pairs:
        if on_call is not None:
            on_call(bundle)
        out.append(evaluate_entailment(bundle, decision, manifest=manifest,
                                       llm_client=llm_client, model_policy=model_policy))
    return tuple(out)

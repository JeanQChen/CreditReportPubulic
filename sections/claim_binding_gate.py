"""P8 确定性 aggregate Claim Binding Gate（`DESIGN_V2.md` §0.13 第 5 条 / §6.3.2）。

用法：`from sections import claim_binding_gate as CBG`

本模块是**机械门**的唯一实现：对每个 `(subject_kind, subject_id, draft_revision)` 只输出
**恰好一条** `ClaimBindingDecision`，绑定该 subject **完整、有序**的 proposal IDs/hashes、
support-set digest、manifest identity、规则版本与逐边结果。

职责边界（越界即缺陷）：

* 它**不**做长文语义判断、**不**调用任何 review LLM（本模块不 import `llm`）、**不**冒充
  蕴含决定（输出类型里根本没有 `entailment_decision_id` 字段）；
* 它**不**担任语义裁判：材料是否真的支持断言由 `claim_entailment_evaluator`（P9）回答；
* 它**不**在 Harness 之外另建材料预选：事实目录只来自 authority 自身的确定性读视图
  （`pack_writer.AuthorityScan.facts`），材料清单一律回查传入的 exact manifest；
* 它**不**写库、不写 Pack、不改任何输入对象。

逐边机械校验（一条失败即该边 fail，取**首个**失败原因码）：

| 轴 | 拒绝码 |
|---|---|
| subject 身份 | `subject_mismatch` |
| authority kind / fact 字段混装 / 路径 B 非 topic | `authority_kind_mismatch` |
| role / semantics / 声明路径自洽性 | `support_role_mismatch`、`support_semantics_mismatch`、`authorization_path_mismatch` |
| manifest identity | `manifest_identity_mismatch` |
| 路径 A 权威事实坐标 | `missing_authority_fact`（坐标不存在）、`authority_container_mismatch`（同 kind+fact 但容器不同）、`authority_kind_mismatch`（同容器+fact 但 kind 不同） |
| source / provenance 身份 | `source_identity_mismatch`（该轴只有一个封闭码） |
| content 指纹 | `fingerprint_mismatch` |
| material / payload / exact locator | `missing_material_or_locator` |
| 路径 B / context 成员的**正文解析结果**（§三 A.7） | `material_payload_unresolved` |
| 路径 B 的**授权面**（§二 / P0，`cbg-2`）：候选文本含任何高风险表面即不可由材料派生授权 | `path_b_high_risk_surface` |
| 分支禁止字段（future 身份、无载体的 payload、非 topic kind 的 material） | `forbidden_field_present` |

聚合级（structural）问题只用于**集合本身**的问题：空集、重复 id、非 canonical 顺序，以及
协调器层面的「声明集 ≠ 实际集」。它们一律 **fail-closed 抛出**（`ClaimBindingGateError`，
消息以封闭结构码开头），**不**伪装成一条决定 —— 原因见下面的 wire 容量发现。

**canonical order**：proposal 集必须按 `proposed_support_id` 升序呈现。`proposed_support_id`
是内容寻址的，因此这个顺序是 proposal 内容的确定性函数，不依赖任何调用方的排列。非
canonical 顺序即拒（`proposal_order_drift`），本门不替调用方「顺手排好」：排序会静默改变
决定所绑定的有序集合。

**`authority_kind` 单值口径**：本合同 wire 的 `ClaimBindingDecision.authority_kind` 是**单值**
字段，而一个 subject 的边可以合法地跨 authority kind（§0.13 第 3 条：三条轴正交组合，
`DESIGN_V2.md` §0.13 / 计划 §6.2.3 允许 `corroborating + factual` 落在另一类权威上）。本门
因此**不**要求 kind 唯一，而是确定性地记录**主导边**的 kind：canonical order 中第一条
`support_role='primary'` 的边；若该集合没有 primary 边（全 corroborating 合法），则取
canonical order 首条。逐边 authority kind 由各 `ProposedSupportRef` 自身携带，本字段只是
可审计的主导标签，**不构成**授权判断。

**wire 容量发现（`narr-4` 未修改）**：`BINDING_STRUCTURAL_REASONS` 的五个码
（`proposal_set_empty` / `proposal_set_incomplete` / `proposal_set_has_unknown` /
`proposal_order_drift` / `proposal_duplicate`）在 `narr-4` 上**无法**作为决定发出：

* `ClaimBindingDecision.__post_init__` 要求 `proposal_ids` 非空、无重复、且
  `proposal_hashes` 与之**一一对应**（同长度）——「少一条」的那条没有对象、因此没有 hash，
  伪造一个 hash 是编造身份，绝不允许；
* 同一构造器还规定「逐边全 pass 且集合完整 ⇒ 必须 `result='pass'`」——因此「多一条 / 顺序
  漂移 / 重复」在**逐边全通过**时也不可表达为 fail。

本门因此对这些集合级问题直接抛出（`proposal_set_empty` / `proposal_duplicate` /
`proposal_order_drift` / `proposal_set_incomplete` / `proposal_set_has_unknown` 只作为
异常消息前缀出现），`structural_reason_code` 在任何真实决定上都是 `None`。subject 宇宙因此
是「**拥有 ≥1 条 proposal** 的 subject」：零 proposal 的 subject 拿不到决定，也不该拿到
（它没有可绑定的支撑集）。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from harness import topic_schema as TS
from sections import material_context as MC
from sections import narrative_schema as NS
from sections import pack_writer as PW

#: 机械门规则版本。字面量**唯一**定义在 wire 模块（`narrative_schema.
#: CLAIM_BINDING_GATE_VERSION` 同时是 `ClaimBindingDecision.rules_version` 的缺省值）；
#: 本模块是它的规则所有者，只 re-export，不再写第二份字面量（避免两处版本号漂移）。
CLAIM_BINDING_GATE_VERSION: str = NS.CLAIM_BINDING_GATE_VERSION

#: §6.3.1 门前 proposal 的**禁止字段**：提案不得引用尚未形成的未来身份。真实 wire 类里根本
#: 没有这些属性（类型层不可表达）；本门对**任何**传入对象（含未过 schema 的鸭子类型）逐一
#: 复核，出现即 `forbidden_field_present`。
FORBIDDEN_PROPOSAL_FIELDS: tuple[str, ...] = (
    "accepted_support_binding_id",
    "binding_decision_id",
    "entailment_decision_id",
    "section_claim_id",
    "section_result_id",
    "narrative_sentence_id",
    "narrative_paragraph_id",
    "narrative_table_id",
)

#: structural 失败码的固定判定优先级（顺序即语义：先集合唯一性，再完整性/未知，最后顺序）。
STRUCTURAL_PRIORITY: tuple[str, ...] = (
    "proposal_duplicate",
    "proposal_set_incomplete",
    "proposal_set_has_unknown",
    "proposal_order_drift",
)


class ClaimBindingGateError(Exception):
    """机械门输入/协调层 fail-closed（不是逐边或聚合的**决定**，而是无法形成决定）。"""


@dataclass(frozen=True)
class BindingSubjectRevision:
    """一个聚合单位的 subject 身份：`(subject_kind, subject_id, draft_revision)`。"""

    subject_kind: str
    subject_id: str
    draft_revision: str

    def __post_init__(self) -> None:
        if self.subject_kind not in NS.BINDING_SUBJECT_KINDS:
            raise ClaimBindingGateError(
                f"BindingSubjectRevision.subject_kind={self.subject_kind!r} 不在 "
                f"{list(NS.BINDING_SUBJECT_KINDS)} 内")
        for name in ("subject_id", "draft_revision"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ClaimBindingGateError(f"BindingSubjectRevision.{name} 不得为空")

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.subject_kind, self.subject_id, self.draft_revision)

    @classmethod
    def from_subject(cls, subject: Any) -> "BindingSubjectRevision":
        """从门前 subject（`ClaimCandidate` / `NarrativeDraftUnit`）派生，不猜类型。"""
        if isinstance(subject, NS.ClaimCandidate):
            return cls("claim_candidate", subject.candidate_id, subject.draft_revision)
        if isinstance(subject, NS.NarrativeDraftUnit):
            return cls("narrative_draft_unit", subject.draft_unit_id, subject.draft_revision)
        raise ClaimBindingGateError(
            "BindingSubjectRevision.from_subject 只接受 ClaimCandidate 或 NarrativeDraftUnit，"
            f"得到 {type(subject).__name__}")


# ---------------------------------------------------------------------------
# support-set digest（本门对「同一 support set」的唯一口径）
# ---------------------------------------------------------------------------

def support_set_digest(*, subject_revision: BindingSubjectRevision,
                       proposal_ids: Sequence[str], proposal_hashes: Sequence[str],
                       manifest_id: str, manifest_fingerprint: str,
                       rules_version: str | None = None) -> str:
    """support-set digest：**完整、有序**的 proposal 身份 + subject + manifest + 规则版本。

    P9 的 `ClaimEntailmentDecision.support_set_digest` 必须与**本函数**在同一批输入上的输出
    逐字节相同（§6.3.2「same support-set digest」）。digest 只吃内容寻址的身份，不吃时间戳、
    不吃调用序，因此「文本或 bindings 任一改变即产生新 digest」是可断言的。
    """
    ids = tuple(proposal_ids)
    hashes = tuple(proposal_hashes)
    if len(ids) != len(hashes):
        raise ClaimBindingGateError(
            "support_set_digest：proposal_ids 与 proposal_hashes 必须一一对应（同长度）")
    return NS.content_id("ssd_", {
        "subject_kind": subject_revision.subject_kind,
        "subject_id": subject_revision.subject_id,
        "draft_revision": subject_revision.draft_revision,
        "manifest_id": manifest_id,
        "manifest_fingerprint": manifest_fingerprint,
        "proposal_ids": list(ids),
        "proposal_hashes": list(hashes),
        "rules_version": rules_version or CLAIM_BINDING_GATE_VERSION,
    })


# ---------------------------------------------------------------------------
# 逐边机械校验
# ---------------------------------------------------------------------------

def _proposal_hash(proposal: Any) -> str:
    """proposal content hash。真实 wire 类自带 `content_hash()`；鸭子类型对象按其
    `identity_body()` 现算（同一 canonical JSON + sha256 口径，不另立哈希方案）。"""
    fn = getattr(proposal, "content_hash", None)
    if callable(fn):
        value = fn()
        if isinstance(value, str) and value:
            return value
    body = getattr(proposal, "identity_body", None)
    if callable(body):
        raw = body()
        if isinstance(raw, Mapping):
            return hashlib.sha256(
                NS.canonical_json(raw).encode("utf-8")).hexdigest()
    raise ClaimBindingGateError(
        "proposal 既没有可用的 content_hash() 也没有 identity_body()，无法机械绑定")


def authority_fact_table(authority: Any) -> dict[tuple[str, str, str], PW.AuthorityFactEntry]:
    """权威事实目录：只来自 authority 自身的确定性读视图（**不**触发任何扫描或模型调用）。

    复用 `pack_writer._authority_fact_table`：坐标重复即拒的规则只允许有一份实现，否则同一
    事实坐标在两处的去重口径可能漂移。P9/P10 也用本函数取目录，不各自再建一张表。
    """
    facts = getattr(authority, "facts", None)
    if facts is None or isinstance(facts, (str, bytes, Mapping)):
        raise ClaimBindingGateError(
            "Binding Gate 的 authority 必须是确定性读视图（`AuthorityScan`：有 `.facts` "
            "条目序列）；本门不扫描权威、不构造材料、不调用模型")
    return PW._authority_fact_table(tuple(facts))


def resolve_proposal_sources(
        proposal: Any, *, fact_table: Mapping[tuple[str, str, str], PW.AuthorityFactEntry],
        manifest: NS.WriterMaterialManifest,
        ) -> tuple[PW.AuthorityFactEntry | None, NS.WriterMaterialManifestEntry | None]:
    """proposal → 它真正引用的**权威事实条目**与**精确材料成员**（找不到即 `None`）。

    只做**解析**，不做判定：路径 A 解析成权威事实条目，路径 B / context 解析成 manifest 成员。
    P8 用它做逐边机械核验，P9/P10 用它取「指定权威/材料集合」，三处**同一套**解析口径。
    """
    kind = getattr(proposal, "authority_kind", None)
    path = getattr(proposal, "authorization_path", None)
    container = str(getattr(proposal, "authority_container_id", "") or "")
    if path == "path_a_prevalidated" and kind in NS.AUTHORITY_KINDS:
        fact_id = str(getattr(proposal, NS.FACT_FIELD_BY_AUTHORITY_KIND[kind], "") or "")
        if fact_id:
            return fact_table.get((kind, container, fact_id)), None
        return None, None
    material = getattr(proposal, "material_id", None)
    if not material:
        return None, None
    return None, _member_of(manifest, container, material)


def _member_of(manifest: NS.WriterMaterialManifest, container: Any,
               material: Any) -> NS.WriterMaterialManifestEntry | None:
    container_id = str(container or "")
    material_id = str(material or "")
    if not container_id or not material_id:
        return None
    return manifest.entry_for(NS.manifest_member_ref(container_id, material_id))


def _closure_mismatch(*, member: NS.WriterMaterialManifestEntry, payload: Any,
                      locator: Any) -> str | None:
    """路径 B / context 边的**自闭合**复核：边**自己**的 payload 引用与 exact locator 是否与
    它声称绑定的 manifest 成员逐字相同。

    §四.1/§四.2 的要点正是「不得仅从 manifest 重新拼接后声称 edge 合格」：判据必须吃边上的
    字段本身。两条轴分别比：

    * `payload_ref` 先按 `TS.MaterialPayloadRef` 规范化再逐字比（顺序/缺省字段不构成差异）；
    * `locator_ref` 用 `NS.locator_sort_key` 比（`loc-1` tagged union 的变体与各字段全部入键，
      因此「把块区间偷偷换成字符区间」这种换类型也必然不等）。

    边上的值形态**非法**（不是 Mapping / 不是 `loc-1`）同样返回不匹配：闭合的前提是两边都能
    被解释成同一种定位，读不出来的东西不构成闭合。
    """
    try:
        canonical = TS.MaterialPayloadRef.from_dict(dict(payload)).to_dict()
    except Exception:
        return "missing_material_or_locator"
    if canonical != dict(getattr(member, "payload_ref", None) or {}):
        return "missing_material_or_locator"
    try:
        if NS.locator_sort_key(locator) != NS.locator_sort_key(member.locator_ref):
            return "missing_material_or_locator"
    except NS.NarrativeSchemaError:
        return "missing_material_or_locator"
    return None


def _identity_matches(entry: Any, proposal: Any) -> str | None:
    """source **与** provenance 身份轴（该轴只有一个封闭拒绝码）。"""
    if (str(getattr(entry, "source_identity", "") or "")
            != str(getattr(proposal, "source_identity", "") or "")
            or str(getattr(entry, "provenance_identity", "") or "")
            != str(getattr(proposal, "provenance_identity", "") or "")):
        return "source_identity_mismatch"
    return None


def _payload_resolution_reason(member: Any, *, material_context: Any = None) -> str | None:
    """§三 A.7：manifest 成员**存在**不等于它的正文被解析过。

    路径 B 与 context 边的事实基础是「材料正文里可读出的东西」。如果机械门只看
    `material_id`、`source_identity`、`content_fingerprint` 三个字段，那么一个「只有 ID」的
    wmm-1 成员（或任何被手工拼出来的成员）就能让提案通过绑定，而下游 entailment 与渲染
    手里根本没有正文——绑定门于是替一条它从未见过的正文背书。

    因此本函数**独立复检**该成员是否携带真实 payload 解析结果：payload 载体身份、精确定位、
    payload 内容哈希、正文读视图指纹。任一缺失或形态不合法 → `material_payload_unresolved`
    （封闭拒绝码，不猜、不降级、不用别的字段顶替）。
    """
    payload_ref = getattr(member, "payload_ref", None)
    if not isinstance(payload_ref, Mapping) or not payload_ref:
        return "material_payload_unresolved"
    try:
        canonical = TS.MaterialPayloadRef.from_dict(dict(payload_ref)).to_dict()
    except Exception:
        return "material_payload_unresolved"
    if canonical != dict(payload_ref):
        return "material_payload_unresolved"
    try:
        # §四.5：成员定位必须是 `loc-1` tagged union 且非空（半开字符区间要求 `start < end`，
        # 闭块区间允许单块 `first == last`；旧裸三元组一律不认）。
        if NS.validate_locator(getattr(member, "locator_ref", None),
                              "WriterMaterialManifestEntry") is None:
            return "material_payload_unresolved"
    except NS.NarrativeSchemaError:
        return "material_payload_unresolved"
    for name in ("payload_hash", "reading_view_fingerprint"):
        value = str(getattr(member, name, "") or "")
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            return "material_payload_unresolved"
    if not str(getattr(member, "material_type", "") or ""):
        return "material_payload_unresolved"
    if material_context is not None:
        # 组合根注入了 wmctx-1 正文上下文时，进一步**逐字**比对成员字段与已解析正文
        # （payload 引用、定位、payload 哈希、内容指纹、读视图指纹、正文非空）。
        # 缺上下文时上面的字段级复核仍成立；「正文是否真的在场」在语义门
        # （`claim_entailment_evaluator`，无条件要求上下文）与渲染侧再各查一次。
        try:
            MC.reading_for_manifest_member(material_context=material_context, member=member)
        except MC.MaterialContextError:
            return "material_payload_unresolved"
    return None


def _edge_reason(proposal: Any, *, subject_revision: BindingSubjectRevision,
                 fact_table: Mapping[tuple[str, str, str], PW.AuthorityFactEntry],
                 manifest: NS.WriterMaterialManifest,
                 material_context: Any = None,
                 subject_text: str | None = None) -> str | None:
    """一条 support edge 的机械结论：`None` = pass，否则是封闭拒绝码（首个失败原因）。

    「边没有身份」由聚合层率先 fail-closed 抛出（没有身份的边不进聚合签名），因此本函数
    只处理**有身份**的边。

    `subject_text` 是**该 subject 自己的文本**（claim candidate 的 `claim_text`；draft unit
    的文本只有 `narrative_draft_unit` 才有）。它只被路径 B 的授权面判据使用（§二 / P0），
    且**必须**由本门从 Draft 里取，不由调用方传入（调用方传入等于让被审对象自带判据）。
    """
    if (getattr(proposal, "binding_subject_kind", None) != subject_revision.subject_kind
            or getattr(proposal, "binding_subject_id", None) != subject_revision.subject_id
            or getattr(proposal, "draft_revision", None) != subject_revision.draft_revision):
        return "subject_mismatch"

    kind = getattr(proposal, "authority_kind", None)
    semantics = getattr(proposal, "support_semantics", None)
    role = getattr(proposal, "support_role", None)
    path = getattr(proposal, "authorization_path", None)
    if kind not in NS.AUTHORITY_KINDS:
        return "authority_kind_mismatch"
    if semantics not in NS.SUPPORT_SEMANTICS:
        return "support_semantics_mismatch"
    if role not in NS.SUPPORT_ROLES:
        return "support_role_mismatch"
    if path not in NS.AUTHORIZATION_PATHS:
        return "authorization_path_mismatch"

    # 三条轴必须自洽（与 wire 同一口径，但本门**自己**复核，不假设上游已校验）。
    if semantics == "context":
        if path != "context_only":
            return "authorization_path_mismatch"
        if subject_revision.subject_kind != "narrative_draft_unit":
            return "support_semantics_mismatch"
    else:
        if path == "context_only":
            return "authorization_path_mismatch"
        if subject_revision.subject_kind != "claim_candidate":
            return "support_semantics_mismatch"
    if path == "path_b_material_derived" and kind != "topic_pack":
        return "authority_kind_mismatch"

    # §6.3.1 禁止字段：proposal 不得引用未来身份。
    for name in FORBIDDEN_PROPOSAL_FIELDS:
        if getattr(proposal, name, None) is not None:
            return "forbidden_field_present"

    fact_fields = {name: getattr(proposal, name, None) for name in NS.ALL_FACT_FIELDS}
    present_facts = {name for name, value in fact_fields.items() if value}
    expected_field = NS.FACT_FIELD_BY_AUTHORITY_KIND[kind]
    if present_facts - {expected_field}:
        # fact 身份与 authority kind 不是同一类（含 fact_id 与 financial_fact_id 混装）。
        return "authority_kind_mismatch"
    if path == "context_only" and present_facts:
        # context 不得携带任何 fact identity（它只验证背景/结构/衔接）。
        return "authorization_path_mismatch"
    if path == "path_b_material_derived" and present_facts:
        # 路径 B 只授权材料派生的描述性原子，不得声称已有预验证事实身份。
        return "authorization_path_mismatch"
    if path == "path_b_material_derived" and subject_revision.subject_kind == "claim_candidate":
        # §二 / P0（`cbg-2`）：路径 B 的授权面是**非高风险描述性原子**。候选文本里出现任何
        # 高风险表面（数字/金额/比例/日期/期间/币种、显式否定、勾选与适用状态、法人主体身份、
        # 表格行列与合计/占比关系、因果或结论连接）即该边不可由材料派生路径授权。
        #
        # 判据**只**看候选文本自己（`subject_text`），**不看**该字面成分是否在材料正文里
        # 逐字存在：原文逐字存在不等于已经资格化，高风险硬事实只能走路径 A 的预验证权威。
        # 本检查有意排在成员解析之前：它判的是「这条路径**能否**授权这个 subject」，逻辑上
        # 先于「这条边绑定的成员是否可解析」，因此拒绝码必须是 `path_b_high_risk_surface`。
        if NS.high_risk_surface_tokens(str(subject_text or "")):
            return "path_b_high_risk_surface"

    material = getattr(proposal, "material_id", None) or None
    payload = getattr(proposal, "payload_ref", None) or None
    locator = getattr(proposal, "locator_ref", None) or None
    if kind == "topic_pack" and payload is not None and material is None:
        # topic_pack 的 payload_ref 只在绑定 material 载体时才能解析（其余三类 kind 的
        # payload 是它们**自己**的载体，不要求 material）。
        return "forbidden_field_present"
    if kind != "topic_pack" and material is not None:
        # 非 Pack 权威不得虚构一条 Topic material（§6.2.3）。
        return "forbidden_field_present"

    if (getattr(proposal, "manifest_id", None) != manifest.manifest_id
            or getattr(proposal, "manifest_fingerprint", None) != manifest.fingerprint()):
        return "manifest_identity_mismatch"

    if path == "path_a_prevalidated":
        fact_id = str(fact_fields[expected_field])
        container = str(getattr(proposal, "authority_container_id", "") or "")
        entry = fact_table.get((kind, container, fact_id))
        if entry is None:
            if any(key[0] == kind and key[2] == fact_id for key in fact_table):
                return "authority_container_mismatch"
            if any(key[1] == container and key[2] == fact_id for key in fact_table):
                return "authority_kind_mismatch"
            return "missing_authority_fact"
        mismatch = _identity_matches(entry, proposal)
        if mismatch is not None:
            return mismatch
        if (not entry.content_fingerprint
                or entry.content_fingerprint != str(
                    getattr(proposal, "content_fingerprint", "") or "")):
            return "fingerprint_mismatch"
        if (entry.material_id or None) != material:
            return "missing_material_or_locator"
        if (dict(entry.payload_ref) if entry.payload_ref else None) != (
                dict(payload) if payload else None):
            return "missing_material_or_locator"
        if NS.locator_sort_key(entry.locator_ref) != NS.locator_sort_key(locator):
            return "missing_material_or_locator"
        return None

    if path == "path_b_material_derived":
        if material is None:
            return "missing_material_or_locator"
        # §四.1：路径 B 的边必须**自闭合**——载体身份（material_id）、payload 引用与 exact
        # locator 必须与 manifest 成员**逐字**相同。旧口径把 payload/locator 一律判为
        # `forbidden_field_present`（「manifest 成员上已有」），于是「这条边到底绑定了什么」
        # 只能靠下游拿 material_id 回查 manifest 重新拼接：边上少字段没人看得出来，验收也无法
        # 逐边复核。现在两个字段必须在场且与成员相等。
        if payload is None or locator is None:
            return "missing_material_or_locator"
        member = _member_of(manifest, getattr(proposal, "authority_container_id", None), material)
        if member is None:
            return "missing_material_or_locator"
        mismatch = _identity_matches(member, proposal)
        if mismatch is not None:
            return mismatch
        if member.material_content_fingerprint != str(
                getattr(proposal, "content_fingerprint", "") or ""):
            return "fingerprint_mismatch"
        if _closure_mismatch(member=member, payload=payload, locator=locator) is not None:
            return "missing_material_or_locator"
        # §三 A.7：**清单里有这个成员 ≠ 它的正文被解析过**。路径 B 的全部授权基础是
        # 「该材料真实正文里可读出的东西」，所以这里必须独立复检该成员携带真实 payload
        # 解析结果（payload 身份 + 精确定位 + payload 哈希 + 读视图指纹）。
        unresolved = _payload_resolution_reason(member, material_context=material_context)
        if unresolved is not None:
            return unresolved
        return None

    # context_only（本批提案契约里 context 边只能是 topic_pack + 真实 material）。
    if kind != "topic_pack":
        return "authority_kind_mismatch"
    if payload is None or locator is None:
        # §四.2：context 边与路径 B 用**同一**闭合口径（载体身份 + payload + locator 三者同在
        # 一条边上），区别只在它没有事实身份、也不产生蕴含决定。
        return "missing_material_or_locator"
    if material is None:
        return "missing_material_or_locator"
    member = _member_of(manifest, getattr(proposal, "authority_container_id", None), material)
    if member is None:
        return "missing_material_or_locator"
    mismatch = _identity_matches(member, proposal)
    if mismatch is not None:
        return mismatch
    if member.material_content_fingerprint != str(
            getattr(proposal, "content_fingerprint", "") or ""):
        return "fingerprint_mismatch"
    if _closure_mismatch(member=member, payload=payload, locator=locator) is not None:
        return "missing_material_or_locator"
    # §三 A.7：context 边同样只能用「真实可读正文」做背景/结构/衔接论证；只凭 material ID
    # 一律拒绝（`material_payload_unresolved`）。
    unresolved = _payload_resolution_reason(member, material_context=material_context)
    if unresolved is not None:
        return unresolved
    return None


# ---------------------------------------------------------------------------
# 单 subject 决定
# ---------------------------------------------------------------------------

def _governing_authority_kind(ordered: Sequence[Any]) -> str:
    """主导 authority kind：canonical order 中第一条 primary 边；无 primary 边则取首条。"""
    for proposal in ordered:
        if getattr(proposal, "support_role", None) == "primary":
            return str(getattr(proposal, "authority_kind", "") or "")
    return str(getattr(ordered[0], "authority_kind", "") or "")


def decide_bindings(subject_revision: BindingSubjectRevision, proposals_for_subject: Sequence[Any],
                    authority: Any, *, manifest: NS.WriterMaterialManifest,
                    material_context: Any = None,
                    subject_text: str | None = None,
                    ) -> NS.ClaimBindingDecision:
    """对一个 `(subject_kind, subject_id, revision)` 输出**恰好一条** aggregate 决定。

    `proposals_for_subject` 就是该 subject **完整、有序**的 proposal 集（协调器从 Draft 取，
    不从模型或调用方顺序取）。集合级问题（空 / 重复 / 非 canonical 顺序）**不**产生决定，
    而是 fail-closed 抛出，抛出消息以封闭结构码开头（见模块 docstring 的 wire 容量发现）。

    `subject_text` 是该 subject 的文本（claim candidate 的 `claim_text`），只供路径 B 的
    高风险表面判据使用。它是**被审对象自己的内容**，因此不允许调用方在批量入口之外自报：
    批量入口 `decide_draft_bindings` 一律从 Draft 取。
    """
    if not isinstance(subject_revision, BindingSubjectRevision):
        raise ClaimBindingGateError(
            "decide_bindings 的 subject_revision 必须是 BindingSubjectRevision")
    if not isinstance(manifest, NS.WriterMaterialManifest):
        raise ClaimBindingGateError(
            "decide_bindings 的 manifest 必须是 WriterMaterialManifest（材料清单一律回查它）")
    proposals = list(proposals_for_subject)
    if not proposals:
        raise ClaimBindingGateError(
            "proposal_set_empty: 不形成决定。narr-4 的 ClaimBindingDecision 无法表达空 proposal "
            "集（proposal_ids 非空是构造期硬约束），协调器的 subject 宇宙只能是「拥有 ≥1 条 "
            "proposal 的 subject」")

    passed_ids = [str(getattr(p, "proposed_support_id", "") or "") for p in proposals]
    if any(not pid for pid in passed_ids):
        raise ClaimBindingGateError(
            "proposal_missing: 集合里有边没有身份（proposed_support_id 为空）："
            "没有身份的边不得进入聚合签名")
    if len(set(passed_ids)) != len(passed_ids):
        raise ClaimBindingGateError(
            f"proposal_duplicate: 同一 proposal 出现多次 {sorted(passed_ids)}："
            "重复 id 会让 cardinality 断言静默失效")
    ordered_ids = tuple(sorted(passed_ids))
    if tuple(passed_ids) != ordered_ids:
        # canonical order 是内容寻址 id 的升序（proposal 内容的确定性函数）。
        raise ClaimBindingGateError(
            f"proposal_order_drift: proposal 集必须按 proposed_support_id 升序呈现，"
            f"得到 {passed_ids}，canonical 为 {list(ordered_ids)}")

    fact_table = authority_fact_table(authority)
    by_id = dict(zip(passed_ids, proposals))
    edge_results_list: list[NS.BindingEdgeResult] = []
    for pid in ordered_ids:
        reason = _edge_reason(by_id[pid], subject_revision=subject_revision,
                              fact_table=fact_table, manifest=manifest,
                              material_context=material_context,
                              subject_text=subject_text)
        edge_results_list.append(NS.BindingEdgeResult(
            proposed_support_id=pid, proposal_content_hash=_proposal_hash(by_id[pid]),
            result="pass" if reason is None else "fail", reason_code=reason))
    edge_results = tuple(edge_results_list)
    all_pass = all(e.result == "pass" for e in edge_results)
    hashes = tuple(e.proposal_content_hash for e in edge_results)
    # 集合完整（按构造）且逐边全 pass ⇒ wire 要求 pass；任一逐边失败 ⇒ fail（失败本身就是
    # typed audit，逐边 reason_code 已记录，不需要 structural 码）。
    return NS.ClaimBindingDecision.create(
        subject_kind=subject_revision.subject_kind, subject_id=subject_revision.subject_id,
        draft_revision=subject_revision.draft_revision,
        authority_kind=_governing_authority_kind([by_id[pid] for pid in ordered_ids]),
        manifest_id=manifest.manifest_id, manifest_fingerprint=manifest.fingerprint(),
        support_set_digest=support_set_digest(
            subject_revision=subject_revision, proposal_ids=ordered_ids,
            proposal_hashes=hashes, manifest_id=manifest.manifest_id,
            manifest_fingerprint=manifest.fingerprint()),
        proposal_ids=ordered_ids, proposal_hashes=hashes, edge_results=edge_results,
        result="pass" if all_pass else "fail",
        rules_version=CLAIM_BINDING_GATE_VERSION, structural_reason_code=None)


# ---------------------------------------------------------------------------
# 批量协调器：Draft 的 exact subject set
# ---------------------------------------------------------------------------

def _proposals_by_subject(draft: NS.SectionDraft) -> dict[BindingSubjectRevision, list[Any]]:
    grouped: dict[BindingSubjectRevision, list[Any]] = {}
    for proposal in draft.proposed_support_refs:
        kind = proposal.binding_subject_kind
        subject = BindingSubjectRevision(kind, proposal.binding_subject_id,
                                         proposal.draft_revision)
        grouped.setdefault(subject, []).append(proposal)
    for proposals in grouped.values():
        proposals.sort(key=lambda p: p.proposed_support_id)
    return grouped


def decide_draft_bindings(draft: NS.SectionDraft, authority: Any, *,
                          manifest: NS.WriterMaterialManifest | None = None,
                          material_context: Any = None,
                          ) -> tuple[NS.ClaimBindingDecision, ...]:
    """对整份 Draft 确定性 map：subject 宇宙 = **拥有 ≥1 条 proposal** 的 subject。

    协调器必须让「输出 key 集」与「subject key 集」**完全相等**：本函数在返回前断言
    proposal 集没有被丢掉或凭空多出，并断言每个 proposal 的 subject 都在 Draft 里存在
    （引用未知 subject 的 proposal 一律 fail-closed，不静默成一条无主决定）。
    """
    if not isinstance(draft, NS.SectionDraft):
        raise ClaimBindingGateError("decide_draft_bindings 的 draft 必须是 SectionDraft")
    manifest = manifest if manifest is not None else draft.material_manifest
    if not isinstance(manifest, NS.WriterMaterialManifest):
        raise ClaimBindingGateError(
            "decide_draft_bindings 的 manifest 必须是 WriterMaterialManifest")
    if (manifest.manifest_id != draft.material_manifest.manifest_id
            or manifest.fingerprint() != draft.material_manifest.fingerprint()):
        raise ClaimBindingGateError(
            "传入 manifest 与 Draft 自带的 exact material manifest 不是同一份"
            "（身份或指纹不符）：材料清单不得就地替换")

    known_subject_keys = {BindingSubjectRevision.from_subject(x).key
                          for x in (*draft.claim_candidates, *draft.narrative_draft_units)}
    declared_ids = set(draft.proposed_support_ids)
    # §二 / P0：路径 B 的授权面判据吃**候选自己的文本**，只能从 Draft 里取（不让调用方
    # 自报，否则「被审对象自带判据」）。draft unit 没有这条判据（context 不授权事实），
    # 因此这里只登记 claim candidate 的文本。
    subject_texts = {BindingSubjectRevision("claim_candidate", c.candidate_id,
                                            c.draft_revision).key: str(c.claim_text or "")
                     for c in draft.claim_candidates}

    grouped = _proposals_by_subject(draft)
    for subject in grouped:
        if subject.key not in known_subject_keys:
            raise ClaimBindingGateError(
                f"proposal 指向 Draft 里不存在的 subject {subject.key}："
                "聚合决定必须有真实 subject，不得为无主 proposal 伪造一条")

    decisions: list[NS.ClaimBindingDecision] = []
    bound_ids: set[str] = set()
    for subject in sorted(grouped, key=lambda s: s.key):
        # 该 subject 的**完整、有序** proposal 集：直接取自 Draft（不从调用方排列取）。
        decision = decide_bindings(subject, grouped[subject], authority, manifest=manifest,
                                   material_context=material_context,
                                   subject_text=subject_texts.get(subject.key))
        decisions.append(decision)
        bound_ids.update(decision.proposal_ids)

    if bound_ids != declared_ids:
        raise ClaimBindingGateError(
            "协调器输出与 Draft 声明不一致：决定集未覆盖 Draft 的 exact proposal 集"
            f"（缺失 {sorted(declared_ids - bound_ids)}，多出 {sorted(bound_ids - declared_ids)}）")
    return tuple(decisions)

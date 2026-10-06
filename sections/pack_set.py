"""完整 Pack set 门（M930-2 §5.9）：把 `SectionTask.topic_ids` 精确解析成一组权威 Pack。

本模块是**只读门**：不提交、不改写、不切换 current，也不发明第二套 Store。

为什么需要它，而不是「让 Writer 自己去 Store 取」：
Store 的写时校验（`commit_pack` → `_validate_requirement_matches`）只证明**写这一刻**的
requirement 与 pack 一致。消费这一刻可能已经跨了 run、跨了 Contract/SourcePolicy 版本，
库里也可能存在旧版/错公司/错日期的 current 行（它们落在**不同的 identity key** 上，因此
`load_current(正确 identity)` 只会安静地返回 no_current，不会报错）。所以消费方必须重新、
独立地证明「这一组就是该 task 的完整权威集」，且**不许挑方便的 Pack**。

本模块的检查项与 Store 写时门**逐条对应但不复用其私有函数**：读门面对的是可能被篡改/遗留的
行，必须自己重算（与 `harness.topic_runtime._coverage_gate_reason` 对 Store covered 分支的
处理同一立场）。任何一项不成立都产生**明确 block**，绝不降级为「部分可用」。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from harness import topic_schema as TS
from planning import schema as PS

__all__ = [
    "PACK_SET_GATE_VERSION",
    "PackSetBlock",
    "PackSetBlocked",
    "PackSetError",
    "PackSetCurrentLoad",
    "PackSetStore",
    "PackSetBinding",
    "SqlitePackSetStore",
    "VerifiedPackSet",
    "resolve_pack_set",
    "resolver_of",
]

#: 读门版本。断言只按此版本号复现；提高门槛必须同时升版本。
#: `psg-2`（M930-3B / P4）：新增「独立重算 successor exact-set 关系」——
#: material↔RMD、candidate↔资格决定、eligible/rejected↔qualified result、
#: decision 输入身份与 digest、`ExternalFact` 闭合、gap/block 的 Contract basis。
PACK_SET_GATE_VERSION = "psg-2"

#: block 原因封闭集合（调用方/UI 只按这些码解释，未知码即视为门失效）。
BLOCK_REASONS = (
    "task_topic_set_invalid",
    "requirements_topic_set_invalid",
    "requirement_task_mismatch",
    "requirement_section_mismatch",
    "requirement_binding_not_unanimous",
    "requirement_dependency_stale",
    "store_contract_unavailable",
    "no_current_pack",
    "current_pack_invalidated",
    "current_pack_identity_mismatch",
    "current_pack_fingerprint_mismatch",
    "current_pack_dependency_mismatch",
    "aspect_set_mismatch",
    "aspect_snapshot_mismatch",
    "question_set_mismatch",
    "payload_unresolvable",
    "material_authority_invalid",
    "fact_backing_missing",
    "material_disposition_mismatch",
    "candidate_decision_mismatch",
    "qualified_result_mismatch",
    "decision_input_identity_mismatch",
    "external_fact_closure_mismatch",
    "gap_basis_mismatch",
    "coverage_status_inconsistent",
    "unresolved_record_missing",
    "not_found_audit_missing",
    "foreign_current_for_topic",
    "current_set_not_unique",
)

#: `as_of_date` / 报告基准日的**日期前缀**形状（只比 `YYYY-MM-DD`；时区/时刻不参与比较）。
_DATE_PREFIX_LEN = 10


def _date_prefix(value: str) -> str | None:
    """取可比较的 ISO 日期前缀；形状不符返回 None（调用方决定是拒还是跳过）。"""
    if len(value) < _DATE_PREFIX_LEN:
        return None
    prefix = value[:_DATE_PREFIX_LEN]
    if prefix[4] != "-" or prefix[7] != "-":
        return None
    if not prefix.replace("-", "").isdigit():
        return None
    return prefix


class PackSetError(Exception):
    """调用方用法/依赖不合法（不是「业务 block」）：门无法被满足时 fail-closed。"""


@dataclass(frozen=True)
class PackSetBlock:
    """一条明确 block：定位到具体 topic（可为空）+ 封闭原因码 + 人读细节。"""

    reason: str
    detail: str
    topic_id: str | None = None

    def __post_init__(self) -> None:
        if self.reason not in BLOCK_REASONS:
            raise PackSetError(
                f"未登记的 PackSetBlock.reason={self.reason!r}（允许 {BLOCK_REASONS}）")

    def to_dict(self) -> dict:
        return {"topic_id": self.topic_id, "reason": self.reason, "detail": self.detail}


class PackSetBlocked(Exception):
    """存在一个或多个 block：携带**全部** block（不截断、不挑第一条）。"""

    def __init__(self, blocks: Sequence[PackSetBlock]) -> None:
        self.blocks = tuple(blocks)
        head = "; ".join(
            f"[{b.topic_id or '-'}] {b.reason}: {b.detail}" for b in self.blocks[:6])
        more = f"（共 {len(self.blocks)} 条）" if len(self.blocks) > 6 else ""
        super().__init__(f"Pack set 未通过：{head}{more}")

    def to_dict(self) -> dict:
        return {"blocks": [b.to_dict() for b in self.blocks]}


@dataclass(frozen=True)
class PackSetCurrentLoad:
    """current 读取结果：`pack` 为 None 时 `reason` 必为封闭原因码（绝不返回失效对象）。"""

    pack: TS.TopicResearchPack | None
    reason: str | None = None


class PackSetStore(Protocol):
    """读门所需的最小 Store 面（由 composition root 用现有 topic_store 薄封装提供）。

    **不**提供写入口：读门不得提交、不得切换 current。`resolver` 必须存在——缺它就等于
    放弃「payload 可解析」，那是静默放宽门槛，故 `resolve_pack_set` 直接 fail-closed。
    """

    @property
    def resolver(self) -> TS.PayloadResolver | None: ...

    def load_current(self, identity: TS.PackIdentity) -> PackSetCurrentLoad: ...

    def list_current(self) -> tuple[TS.TopicResearchPack, ...]: ...


@dataclass(frozen=True)
class PackSetBinding:
    """本组的绑定身份：company/report_as_of 不来自 `SectionTask`（它在 `ReportPlan` 上）。

    因此这里的 company/report_as_of 只由 expected_requirements 的**全体一致**推出；
    调用方（Worker）必须再把本对象与自己的 Plan 逐字段核对——`resolve_pack_set` 的
    三参数签名里没有 Plan，越权替调用方断言「与 Plan 一致」就是伪造证明。
    """

    task_id: str
    section_id: str
    company_id: str
    report_as_of: str | None
    contract_version: str
    contract_fingerprint: str
    source_policy_version: str
    dependency_fingerprint: str
    dependency_versions: dict[str, str]

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "section_id": self.section_id,
            "company_id": self.company_id, "report_as_of": self.report_as_of,
            "contract_version": self.contract_version,
            "contract_fingerprint": self.contract_fingerprint,
            "source_policy_version": self.source_policy_version,
            "dependency_fingerprint": self.dependency_fingerprint,
            "dependency_versions": dict(self.dependency_versions),
        }


@dataclass(frozen=True)
class VerifiedPackSet:
    """通过全部门禁的一组 current Pack：topic ids 与 task 精确等集，顺序同 `SectionTask.topic_ids`。

    缺口/冲突/未找到记录**原样保留**（逐 Pack 拼接，不去重、不筛选、不摘要）。
    """

    task_id: str
    section_id: str
    topic_ids: tuple[str, ...]
    packs: tuple[TS.TopicResearchPack, ...]
    requirements: tuple[TS.TopicResearchRequirement, ...]
    binding: PackSetBinding
    gate_version: str = PACK_SET_GATE_VERSION

    def __post_init__(self) -> None:
        if self.gate_version != PACK_SET_GATE_VERSION:
            raise PackSetError(
                f"VerifiedPackSet.gate_version={self.gate_version!r} 与当前门 "
                f"{PACK_SET_GATE_VERSION!r} 不一致（旧门结论不得在消费方复活）")
        if tuple(p.topic_id for p in self.packs) != tuple(self.topic_ids):
            raise PackSetError("VerifiedPackSet.packs 顺序/成员与 topic_ids 不一致")
        if len(self.requirements) != len(self.packs):
            raise PackSetError("VerifiedPackSet.requirements 与 packs 数量不一致")

    # -- 只读视图 ----------------------------------------------------------

    def pack_for(self, topic_id: str) -> TS.TopicResearchPack:
        """按 topic 取**唯一** current Pack；未知 topic 直接报错（不是返回 None）。

        故意不提供 `get`/`getattr` 式宽松入口：Writer 不得「挑方便的 Pack」。
        """
        for pack in self.packs:
            if pack.topic_id == topic_id:
                return pack
        raise PackSetError(f"VerifiedPackSet 不含 topic_id={topic_id!r}")

    def materials(self) -> tuple[TS.ResearchMaterial, ...]:
        return tuple(m for p in self.packs for m in p.materials)

    def facts(self) -> tuple[TS.SupportedFact, ...]:
        return tuple(f for p in self.packs for f in p.facts)

    def gaps(self) -> tuple[TS.ResearchGap, ...]:
        return tuple(g for p in self.packs for g in p.unresolved)

    def conflicts(self) -> tuple[TS.ResearchConflict, ...]:
        return tuple(c for p in self.packs for c in p.conflicts)

    def not_found_audits(self) -> tuple[TS.NotFoundAudit, ...]:
        return tuple(n for p in self.packs for n in p.not_found_audits)

    def aspect_ids(self) -> tuple[str, ...]:
        return tuple(r.aspect_id for p in self.packs for r in p.aspect_results)

    def to_dict(self) -> dict:
        return {
            "gate_version": self.gate_version,
            "task_id": self.task_id, "section_id": self.section_id,
            "topic_ids": list(self.topic_ids),
            "binding": self.binding.to_dict(),
            "packs": [
                {"pack_id": p.pack_id, "topic_id": p.topic_id,
                 "aspect_count": len(p.aspect_results),
                 "material_count": len(p.materials), "fact_count": len(p.facts),
                 "process_status": p.process_status.to_dict(),
                 "coverage_status": p.coverage_status.to_dict()}
                for p in self.packs
            ],
        }


# ---------------------------------------------------------------------------
# 门实现
# ---------------------------------------------------------------------------

def _identity_of(requirement: TS.TopicResearchRequirement) -> TS.PackIdentity:
    return TS.PackIdentity(
        task_id=requirement.task_id, company_id=requirement.company_id,
        report_as_of=requirement.report_as_of,
        contract_fingerprint=requirement.contract_fingerprint,
        source_policy_version=requirement.source_policy_version,
        section_id=requirement.section_id, topic_id=requirement.topic_id)


def _slot(identity: TS.PackIdentity) -> tuple:
    """topic 槽位：不带 Contract/SourcePolicy 版本，用于发现「同槽异版本」的 foreign current。"""
    return (identity.task_id, identity.company_id, identity.report_as_of or "",
            identity.section_id, identity.topic_id)


def _check_requirement_binding(task: PS.SectionTask,
                               requirements: Sequence[TS.TopicResearchRequirement],
                               blocks: list[PackSetBlock]) -> PackSetBinding | None:
    """task ↔ requirement 身份、requirement 之间一致性、依赖集新鲜度。"""
    for r in requirements:
        if r.task_id != task.task_id:
            blocks.append(PackSetBlock(
                "requirement_task_mismatch",
                f"requirement.topic_id={r.topic_id!r} 的 task_id={r.task_id!r} 与 "
                f"SectionTask.task_id={task.task_id!r} 不一致", r.topic_id))
        if r.section_id != task.section_id:
            blocks.append(PackSetBlock(
                "requirement_section_mismatch",
                f"requirement.topic_id={r.topic_id!r} 的 section_id={r.section_id!r} 与 "
                f"SectionTask.section_id={task.section_id!r} 不一致", r.topic_id))

    first = requirements[0]
    axes = {
        "company_id": [(r.topic_id, r.company_id) for r in requirements],
        "report_as_of": [(r.topic_id, r.report_as_of) for r in requirements],
        "contract_version": [(r.topic_id, r.contract_version) for r in requirements],
        "contract_fingerprint": [(r.topic_id, r.contract_fingerprint) for r in requirements],
        "source_policy_version": [(r.topic_id, r.source_policy_version) for r in requirements],
        "dependency_fingerprint": [(r.topic_id, r.dependency_fingerprint()) for r in requirements],
        "dependency_versions": [(r.topic_id, tuple(sorted(r.dependency_versions.items())))
                                for r in requirements],
    }
    unanimous = True
    for axis, values in axes.items():
        distinct = {v for _, v in values}
        if len(distinct) > 1:
            unanimous = False
            shown = "; ".join(f"{t}={v!r}" for t, v in values[:4])
            blocks.append(PackSetBlock(
                "requirement_binding_not_unanimous",
                f"requirement 之间 {axis} 不一致：{shown}"))
    if not unanimous:
        return None

    # 过期/错配依赖集不得解析 current（与 harness.topic_runtime 用同一公共工厂，非第二套构造）。
    expected_deps = TS.build_current_dependency_versions(
        contract_version=first.contract_version,
        source_policy_version=first.source_policy_version)
    for r in requirements:
        if r.dependency_versions != expected_deps:
            stale = sorted(k for k in set(r.dependency_versions) | set(expected_deps)
                           if r.dependency_versions.get(k) != expected_deps.get(k))
            blocks.append(PackSetBlock(
                "requirement_dependency_stale",
                f"requirement.topic_id={r.topic_id!r} 的 dependency_versions 不是当前依赖集，"
                f"差异键 {stale}", r.topic_id))

    return PackSetBinding(
        task_id=task.task_id, section_id=task.section_id, company_id=first.company_id,
        report_as_of=first.report_as_of, contract_version=first.contract_version,
        contract_fingerprint=first.contract_fingerprint,
        source_policy_version=first.source_policy_version,
        dependency_fingerprint=first.dependency_fingerprint(),
        dependency_versions=dict(sorted(first.dependency_versions.items())))


def _check_foreign_current(task: PS.SectionTask,
                           requirements: Sequence[TS.TopicResearchRequirement],
                           store: PackSetStore,
                           blocks: list[PackSetBlock]) -> None:
    """同 topic 槽位上存在**异版本** current → 明确 block（旧版/错合同/错政策不得被当 current）。"""
    try:
        current = tuple(store.list_current())
    except Exception as e:  # noqa: BLE001
        raise PackSetError(f"store.list_current() 失败（读门无法完成，fail-closed）: {e}") from e

    slots = {_slot(_identity_of(r)) for r in requirements}
    by_slot: dict[tuple, list[TS.TopicResearchPack]] = {}
    for pack in current:
        key = (pack.task_id, pack.company_id, pack.report_as_of or "",
               pack.section_id, pack.topic_id)
        if key in slots:
            by_slot.setdefault(key, []).append(pack)

    expected_ids = {_identity_of(r).key(): r.topic_id for r in requirements}
    for key, packs in by_slot.items():
        for pack in packs:
            identity = pack.identity()
            if identity.key() not in expected_ids:
                blocks.append(PackSetBlock(
                    "foreign_current_for_topic",
                    f"topic={pack.topic_id!r} 存在异版本 current Pack {pack.pack_id[:16]}…"
                    f"（contract_fingerprint={pack.contract_fingerprint[:16]}…, "
                    f"source_policy_version={pack.source_policy_version!r}）；"
                    f"本 task 期望的是当前 Contract/SourcePolicy 下的 current", pack.topic_id))


def _check_pack_against_requirement(pack: TS.TopicResearchPack,
                                    requirement: TS.TopicResearchRequirement,
                                    resolver: TS.PayloadResolver,
                                    blocks: list[PackSetBlock]) -> None:
    """逐 Pack 重算身份 / 指纹 / aspect 集合 / 冻结投影 / question 集合 / 依赖指纹。"""
    topic_id = requirement.topic_id
    # 与 Store `_validate_requirement_matches` 同名同义的 8 项身份 + 3 项集合 + 依赖指纹。
    ident_checks = {
        "topic_id": (requirement.topic_id, pack.topic_id),
        "section_id": (requirement.section_id, pack.section_id),
        "task_id": (requirement.task_id, pack.task_id),
        "company_id": (requirement.company_id, pack.company_id),
        "report_as_of": (requirement.report_as_of, pack.report_as_of),
        "contract_version": (requirement.contract_version, pack.contract_version),
        "contract_fingerprint": (requirement.contract_fingerprint, pack.contract_fingerprint),
        "source_policy_version": (requirement.source_policy_version, pack.source_policy_version),
    }
    for field_name, (want, got) in ident_checks.items():
        if want != got:
            blocks.append(PackSetBlock(
                "current_pack_identity_mismatch",
                f"Pack {pack.pack_id[:16]}… 的 {field_name}={got!r} 与 requirement 的 {want!r} 不一致",
                topic_id))

    try:
        pack.verify_pack_id()
    except TS.SchemaValidationError as e:
        blocks.append(PackSetBlock(
            "current_pack_fingerprint_mismatch",
            f"Pack {pack.pack_id[:16]}… 的 pack_id 与内容/依赖指纹不符：{e}", topic_id))

    expected_dep = requirement.dependency_fingerprint()
    if pack.dependency_fingerprint != expected_dep:
        blocks.append(PackSetBlock(
            "current_pack_dependency_mismatch",
            f"Pack {pack.pack_id[:16]}… 的 dependency_fingerprint 与 requirement 重算不一致",
            topic_id))

    req_ids = set(requirement.aspect_ids())
    pack_ids = {r.aspect_id for r in pack.aspect_results}
    if req_ids != pack_ids:
        blocks.append(PackSetBlock(
            "aspect_set_mismatch",
            f"topic={topic_id!r} 的 selected aspect 集合不一致：缺失 {sorted(req_ids - pack_ids)}，"
            f"混入 {sorted(pack_ids - req_ids)}", topic_id))
    frozen_by_id = {a.aspect_id: a for a in requirement.aspects}
    for r in pack.aspect_results:
        frozen = frozen_by_id.get(r.aspect_id)
        if frozen is not None and r.requirement_snapshot.to_dict() != frozen.to_dict():
            blocks.append(PackSetBlock(
                "aspect_snapshot_mismatch",
                f"topic={topic_id!r} aspect {r.aspect_id!r} 的 requirement_snapshot 与冻结投影不一致",
                topic_id))
    if set(requirement.question_ids) != set(pack.question_ids):
        blocks.append(PackSetBlock(
            "question_set_mismatch",
            f"topic={topic_id!r} 的 question 集合不一致：requirement={sorted(requirement.question_ids)}，"
            f"pack={sorted(pack.question_ids)}", topic_id))

    # payload 必须能从 Evidence/verified snapshot 独立重切（dangling/版本/hash 一律 fail-closed）。
    try:
        TS.verify_pack_payloads(pack, resolver)
    except TS.SchemaValidationError as e:
        blocks.append(PackSetBlock(
            "payload_unresolvable",
            f"Pack {pack.pack_id[:16]}… 含不可解析 payload：{e}", topic_id))


def _check_material_and_fact_authority(pack: TS.TopicResearchPack,
                                       resolver: TS.PayloadResolver,
                                       blocks: list[PackSetBlock]) -> None:
    """材料 authority 必须可**重算**且自洽；事实的来源必须权威且真有同源材料背书。

    M930-3B（P4）起本函数**独立重算**全部 successor exact-set 关系，不采信 Pack 自报：

    1. material ↔ `ResearchMaterialDisposition`：每个 material **恰一条** RMD，且 RMD 的
       container / source / provenance / content fingerprint 与 `disposition_id` 全部由
       material 用**同一**唯一构造实现（`TS.build_material_disposition`）重算得出；
    2. candidate ↔ `FactQualificationDecision`：每个 candidate **恰一条**决定，
       revision / source kind / 输入形状（material 集合或 snapshot）逐条一致；
    3. eligible/rejected ↔ qualified result：eligible 决定按 source kind **恰一条**
       `SupportedFact`（topic_material）或 `ExternalFact`（external_source），rejected 决定
       **零条**；每条 fact 反向解析到**恰一个** matching candidate + eligible 决定；
    4. decision 声明的输入必须**存在**于本 Pack，且 locator / payload / content-hash /
       source-identity / identity digest 可**重算**；
    5. formal `ExternalFact` 的 snapshot / body hash / SourcePolicy / 日期 / locator 逐项闭合，
       来源权威由 `recompute_authority_verdict` 独立重算（自报 authoritative 不作证据）；
    6. 每个 gap / block 必须绑定 **current** Contract basis（version + 可重算的
       requirement fingerprint）与非空 required-unmet 证明。

    另保留既有权威重算：

    - material：`recompute_authority_verdict` 必须等于自报 verdict（拒绝自填权威），
      但**不**要求每条 material 都是 authoritative——被拒材料是保留的诊断，不得静默丢弃；
    - fact：来源权威必须 authoritative 且可重算，并存在**同源 material**（否则事实的材料
      背书已经丢失，Pack 不该继续充当权威输入）。

    已登记的边界（不假装已闭合）：`SupportedFact.fact_id` 由 runtime 以 `claim_id` 等
    Pack 外输入派生，读门只能证明唯一性与资格链闭合，无法重算其字面值。
    """
    topic_id = pack.topic_id
    _check_material_dispositions(pack, blocks)
    candidates = _index_unique(pack.fact_candidates, "candidate_id", "FactCandidate",
                               "candidate_decision_mismatch", topic_id, blocks)
    decisions = _check_candidate_decision_chain(pack, blocks, candidates)
    _check_qualified_results(pack, blocks, candidates, decisions)
    _check_decision_inputs(pack, blocks, candidates)
    _check_external_fact_closure(pack, resolver, blocks, candidates)
    _check_gap_and_block_basis(pack, blocks)
    material_sources = {m.source_identity for m in pack.materials}
    for m in pack.materials:
        try:
            recomputed = TS.recompute_authority_verdict(m.authority_assessment)
        except TS.SchemaValidationError as e:
            blocks.append(PackSetBlock(
                "material_authority_invalid",
                f"material {m.material_id!r} 的 authority 不可重算：{e}", topic_id))
            continue
        if recomputed != m.authority_assessment.verdict:
            blocks.append(PackSetBlock(
                "material_authority_invalid",
                f"material {m.material_id!r} 自报 verdict={m.authority_assessment.verdict!r} "
                f"与重算 {recomputed!r} 不一致", topic_id))

    for f in pack.facts:
        try:
            recomputed = TS.recompute_authority_verdict(f.source_authority)
        except TS.SchemaValidationError as e:
            blocks.append(PackSetBlock(
                "material_authority_invalid",
                f"fact {f.fact_id!r} 的 source_authority 不可重算：{e}", topic_id))
            continue
        if recomputed != f.source_authority.verdict or recomputed != "authoritative":
            blocks.append(PackSetBlock(
                "material_authority_invalid",
                f"fact {f.fact_id!r} 的来源权威不合格：自报={f.source_authority.verdict!r}、"
                f"重算={recomputed!r}（事实来源必须 authoritative）", topic_id))
        identity = TS.authority_source_identity(f.source_authority)
        if identity not in material_sources:
            blocks.append(PackSetBlock(
                "fact_backing_missing",
                f"fact {f.fact_id!r} 的来源 {identity!r} 在 Pack 内没有同源 material 背书",
                topic_id))


# ---------------------------------------------------------------------------
# M930-3B（P4）独立重算：successor exact-set 关系（§16.8.1 四道门 + 输入身份闭合）
#
# 立场与本模块开头一致：读门面对的是**可能被篡改/遗留的行**，因此不复用
# `TopicResearchPack.__post_init__` 的结论、也不相信 Pack 自报的派生字段；能重算的一律重算。
# ---------------------------------------------------------------------------

def _index_unique(items: Sequence[Any], key: str, label: str, reason: str,
                  topic_id: str, blocks: list[PackSetBlock]) -> dict[str, Any]:
    """按 `key` 建索引；**重复即 block**（同一身份出现两次不得静默取一）。"""
    index: dict[str, Any] = {}
    for item in items:
        value = getattr(item, key)
        if value in index:
            blocks.append(PackSetBlock(
                reason, f"Pack 内含重复 {label}.{key}={value!r}（重复身份即拒）", topic_id))
            continue
        index[value] = item
    return index


def _check_material_dispositions(pack: TS.TopicResearchPack,
                                 blocks: list[PackSetBlock]) -> None:
    """门 1 重算：每个 material **恰一条** RMD，且 RMD 的派生身份由 material 重算得出。"""
    topic_id = pack.topic_id
    material_by_id = _index_unique(pack.materials, "material_id", "ResearchMaterial",
                                   "material_disposition_mismatch", topic_id, blocks)
    dispositions = _index_unique(pack.material_dispositions, "disposition_id",
                                 "ResearchMaterialDisposition", "material_disposition_mismatch",
                                 topic_id, blocks)
    by_material: dict[str, list[TS.ResearchMaterialDisposition]] = {}
    for d in dispositions.values():
        by_material.setdefault(d.material_id, []).append(d)
    for material_id in sorted(set(by_material) - set(material_by_id)):
        blocks.append(PackSetBlock(
            "material_disposition_mismatch",
            f"ResearchMaterialDisposition 引用了本 Pack 不存在的 material {material_id!r}"
            f"（孤儿处置不得被当可用）", topic_id))
    for material_id, material in material_by_id.items():
        got = by_material.get(material_id, [])
        if len(got) != 1:
            blocks.append(PackSetBlock(
                "material_disposition_mismatch",
                f"material {material_id!r} 必须恰有一条 ResearchMaterialDisposition，"
                f"得到 {len(got)} 条（缺失/重复即拒）", topic_id))
            continue
        disposition = got[0]
        try:
            # 与 runtime 同一**唯一**构造实现重算：container / source / provenance /
            # content fingerprint 与 disposition_id 全部由 material 决定，不采信自报。
            recomputed = TS.build_material_disposition(
                material, aspect_ids=disposition.aspect_ids,
                admission_state=disposition.admission_state,
                retention_state=disposition.retention_state,
                source_validation=disposition.source_validation,
                reason_code=disposition.reason_code, reason_proof=disposition.reason_proof,
                policy_version=disposition.policy_version)
        except TS.SchemaValidationError as e:
            blocks.append(PackSetBlock(
                "material_disposition_mismatch",
                f"material {material_id!r} 的 RMD 不可重算：{e}", topic_id))
            continue
        if recomputed != disposition:
            blocks.append(PackSetBlock(
                "material_disposition_mismatch",
                f"material {material_id!r} 的 RMD 与重算不一致：自报 disposition_id="
                f"{disposition.disposition_id!r}，重算 {recomputed.disposition_id!r}"
                f"（container/source/provenance/content 身份不得自报）", topic_id))


def _check_candidate_decision_chain(pack: TS.TopicResearchPack,
                                    blocks: list[PackSetBlock],
                                    candidates: dict[str, TS.FactCandidate],
                                    ) -> dict[str, TS.FactQualificationDecision]:
    """门 2 重算：每个 candidate **恰一条**资格决定，revision / kind / 输入形状逐条一致。"""
    topic_id = pack.topic_id
    decisions = _index_unique(pack.fact_qualification_decisions, "decision_id",
                              "FactQualificationDecision", "candidate_decision_mismatch",
                              topic_id, blocks)
    by_candidate: dict[str, list[TS.FactQualificationDecision]] = {}
    for d in decisions.values():
        by_candidate.setdefault(d.candidate_id, []).append(d)
    for candidate_id in sorted(set(by_candidate) - set(candidates)):
        blocks.append(PackSetBlock(
            "candidate_decision_mismatch",
            f"资格决定引用了本 Pack 不存在的 candidate {candidate_id!r}（孤儿决定）", topic_id))
    for candidate_id, candidate in candidates.items():
        got = by_candidate.get(candidate_id, [])
        if len(got) != 1:
            blocks.append(PackSetBlock(
                "candidate_decision_mismatch",
                f"candidate {candidate_id!r} 必须恰有一条 FactQualificationDecision，"
                f"得到 {len(got)} 条（缺失/重复即拒）", topic_id))
            continue
        decision = got[0]
        if (decision.candidate_revision != candidate.candidate_revision
                or decision.candidate_source_kind != candidate.candidate_source_kind):
            blocks.append(PackSetBlock(
                "candidate_decision_mismatch",
                f"candidate {candidate_id!r} 的资格决定 revision/kind "
                f"({decision.candidate_revision!r}/{decision.candidate_source_kind!r}) "
                f"与候选 ({candidate.candidate_revision!r}/"
                f"{candidate.candidate_source_kind!r}) 不一致", topic_id))
            continue
        try:
            recomputed = TS.build_qualification_decision(
                candidate, verdict=decision.verdict,
                input_identity_digest=decision.input_identity_digest,
                input_source_identity=decision.input_source_identity,
                input_locator_digest=decision.input_locator_digest,
                input_payload_digest=decision.input_payload_digest,
                input_snapshot_id=decision.input_snapshot_id,
                rejection_reason=decision.rejection_reason,
                rules_version=decision.rules_version)
        except TS.SchemaValidationError as e:
            blocks.append(PackSetBlock(
                "candidate_decision_mismatch",
                f"candidate {candidate_id!r} 的资格决定不可重算：{e}", topic_id))
            continue
        if recomputed != decision:
            blocks.append(PackSetBlock(
                "candidate_decision_mismatch",
                f"candidate {candidate_id!r} 的资格决定与重算不一致（撤销/伪造输入形状即拒）："
                f"自报 decision_id={decision.decision_id!r}，重算 {recomputed.decision_id!r}",
                topic_id))
    return decisions


def _check_qualified_results(pack: TS.TopicResearchPack, blocks: list[PackSetBlock],
                             candidates: dict[str, TS.FactCandidate],
                             decisions: dict[str, TS.FactQualificationDecision]) -> None:
    """门 3+4 重算：eligible 恰一条按 kind 的 qualified result、rejected 零条、反向唯一闭合。"""
    topic_id = pack.topic_id
    facts = _index_unique(pack.facts, "fact_id", "SupportedFact", "qualified_result_mismatch",
                          topic_id, blocks)
    external_facts = _index_unique(pack.external_facts, "external_fact_id", "ExternalFact",
                                   "qualified_result_mismatch", topic_id, blocks)
    facts_by_candidate: dict[str, list[TS.SupportedFact]] = {}
    for f in facts.values():
        facts_by_candidate.setdefault(f.candidate_id, []).append(f)
    ext_by_candidate: dict[str, list[TS.ExternalFact]] = {}
    for f in external_facts.values():
        ext_by_candidate.setdefault(f.candidate_id, []).append(f)
    for candidate_id in sorted((set(facts_by_candidate) | set(ext_by_candidate))
                               - set(candidates)):
        blocks.append(PackSetBlock(
            "qualified_result_mismatch",
            f"qualified result 引用了本 Pack 不存在的 candidate {candidate_id!r}"
            f"（孤儿结果不得被当合格事实）", topic_id))
    for f in facts.values():
        candidate = candidates.get(f.candidate_id)
        if candidate is None:
            continue
        if candidate.candidate_source_kind != "topic_material":
            blocks.append(PackSetBlock(
                "qualified_result_mismatch",
                f"SupportedFact {f.fact_id!r} 不得由 external_source 候选产生（跨 kind）", topic_id))
        if f.candidate_revision != candidate.candidate_revision:
            blocks.append(PackSetBlock(
                "qualified_result_mismatch",
                f"SupportedFact {f.fact_id!r} 的 candidate_revision 与候选不一致", topic_id))
        if f.aspect_ids != candidate.aspect_ids:
            blocks.append(PackSetBlock(
                "qualified_result_mismatch",
                f"SupportedFact {f.fact_id!r} 的 aspect_ids 与候选不一致（事实不得换 aspect）",
                topic_id))
    for f in external_facts.values():
        candidate = candidates.get(f.candidate_id)
        if candidate is None:
            continue
        if candidate.candidate_source_kind != "external_source":
            blocks.append(PackSetBlock(
                "qualified_result_mismatch",
                f"ExternalFact {f.external_fact_id!r} 不得由 topic_material 候选产生（跨 kind）",
                topic_id))
        if f.candidate_revision != candidate.candidate_revision:
            blocks.append(PackSetBlock(
                "qualified_result_mismatch",
                f"ExternalFact {f.external_fact_id!r} 的 candidate_revision 与候选不一致", topic_id))
        if f.aspect_ids != candidate.aspect_ids:
            blocks.append(PackSetBlock(
                "qualified_result_mismatch",
                f"ExternalFact {f.external_fact_id!r} 的 aspect_ids 与候选不一致", topic_id))
        if f.statement != candidate.statement:
            blocks.append(PackSetBlock(
                "qualified_result_mismatch",
                f"ExternalFact {f.external_fact_id!r} 的命题与候选不一致", topic_id))
    for decision in decisions.values():
        topic_results = facts_by_candidate.get(decision.candidate_id, [])
        ext_results = ext_by_candidate.get(decision.candidate_id, [])
        if decision.verdict == "rejected":
            if topic_results or ext_results:
                blocks.append(PackSetBlock(
                    "qualified_result_mismatch",
                    f"rejected 决定 {decision.decision_id!r} 必须**零条** qualified result，"
                    f"得到 {len(topic_results)}/{len(ext_results)}", topic_id))
            continue
        if decision.candidate_source_kind == "topic_material":
            if len(topic_results) != 1 or ext_results:
                blocks.append(PackSetBlock(
                    "qualified_result_mismatch",
                    f"eligible topic_material 决定 {decision.decision_id!r} 必须恰有一条 "
                    f"SupportedFact 且零条 ExternalFact，得到 {len(topic_results)}/"
                    f"{len(ext_results)}", topic_id))
            elif topic_results[0].qualification_decision_id != decision.decision_id:
                blocks.append(PackSetBlock(
                    "qualified_result_mismatch",
                    f"SupportedFact {topic_results[0].fact_id!r} 未回指其 eligible 决定 "
                    f"{decision.decision_id!r}", topic_id))
        else:
            if len(ext_results) != 1 or topic_results:
                blocks.append(PackSetBlock(
                    "qualified_result_mismatch",
                    f"eligible external_source 决定 {decision.decision_id!r} 必须恰有一条 "
                    f"ExternalFact 且零条 SupportedFact，得到 {len(ext_results)}/"
                    f"{len(topic_results)}", topic_id))
            elif ext_results[0].qualification_decision_id != decision.decision_id:
                blocks.append(PackSetBlock(
                    "qualified_result_mismatch",
                    f"ExternalFact {ext_results[0].external_fact_id!r} 未回指其 eligible 决定 "
                    f"{decision.decision_id!r}", topic_id))
    for f in facts.values():
        decision = decisions.get(f.qualification_decision_id)
        if (decision is None or decision.verdict != "eligible"
                or decision.candidate_id != f.candidate_id):
            blocks.append(PackSetBlock(
                "qualified_result_mismatch",
                f"SupportedFact {f.fact_id!r} 必须反解到**同一** candidate 的 eligible 决定，"
                f"实得 {None if decision is None else decision.decision_id!r}", topic_id))
    for f in external_facts.values():
        decision = decisions.get(f.qualification_decision_id)
        if (decision is None or decision.verdict != "eligible"
                or decision.candidate_id != f.candidate_id):
            blocks.append(PackSetBlock(
                "qualified_result_mismatch",
                f"ExternalFact {f.external_fact_id!r} 必须反解到**同一** candidate 的 eligible "
                f"决定，实得 {None if decision is None else decision.decision_id!r}", topic_id))


def _check_decision_inputs(pack: TS.TopicResearchPack, blocks: list[PackSetBlock],
                           candidates: dict[str, TS.FactCandidate]) -> None:
    """决定声明的输入必须**存在**于本 Pack，且 locator/payload/content/identity digest 可重算。

    口径：三个 digest 一律按决定**自己声明**的 `input_material_ids`（排序、去重）顺序重算——
    citation 解析顺序不在 wire 里，若按它派生，校验方就无从复算。runtime 侧原按 citation 顺序
    派生，真实 run 实证：一条 claim 引两份材料且 citation 顺序与 sorted 相反时，本门以三条
    `decision_input_identity_mismatch` 挡下整个 topic 的 Pack set（`company_legal_risks`）。
    现已把派生侧改成同一规范序（[harness/topic_runtime.py](harness/topic_runtime.py) `_adopt_facts`），
    本门的重算**照旧逐条生效**：不匹配仍是明确 block，不静默放行。
    """
    topic_id = pack.topic_id
    material_by_id = {m.material_id: m for m in pack.materials}
    for decision in pack.fact_qualification_decisions:
        candidate = candidates.get(decision.candidate_id)
        if candidate is None:
            continue  # 已在 `_check_candidate_decision_chain` 登记为孤儿
        if decision.candidate_source_kind == "external_source":
            if decision.input_snapshot_id != candidate.source_snapshot_id:
                blocks.append(PackSetBlock(
                    "decision_input_identity_mismatch",
                    f"决定 {decision.decision_id!r} 的 input_snapshot_id="
                    f"{decision.input_snapshot_id!r} 与候选的 source_snapshot_id="
                    f"{candidate.source_snapshot_id!r} 不一致", topic_id))
            elif decision.input_source_identity != (
                    f"external_snapshot:{decision.input_snapshot_id}"):
                blocks.append(PackSetBlock(
                    "decision_input_identity_mismatch",
                    f"决定 {decision.decision_id!r} 的 input_source_identity="
                    f"{decision.input_source_identity!r} 与 snapshot 来源身份不一致", topic_id))
            continue
        material_ids = decision.input_material_ids
        if len(set(material_ids)) != len(material_ids):
            blocks.append(PackSetBlock(
                "decision_input_identity_mismatch",
                f"决定 {decision.decision_id!r} 的 input_material_ids 含重复："
                f"{list(material_ids)}", topic_id))
            continue
        missing = [mid for mid in material_ids if mid not in material_by_id]
        if missing:
            blocks.append(PackSetBlock(
                "decision_input_identity_mismatch",
                f"决定 {decision.decision_id!r} 声明的输入 material {missing} 不在本 Pack 内"
                f"（输入不存在即拒）", topic_id))
            continue
        inputs = [material_by_id[mid] for mid in material_ids]
        locator_digest = TS.sha256_canonical(
            {"locators": [m.locator.to_dict() for m in inputs]})
        payload_digest = TS.sha256_canonical(
            {"payloads": [m.payload_ref.to_dict() for m in inputs]})
        if locator_digest != decision.input_locator_digest:
            blocks.append(PackSetBlock(
                "decision_input_identity_mismatch",
                f"决定 {decision.decision_id!r} 的 input_locator_digest 与输入 material 的 "
                f"locator 重算不一致", topic_id))
        if payload_digest != decision.input_payload_digest:
            blocks.append(PackSetBlock(
                "decision_input_identity_mismatch",
                f"决定 {decision.decision_id!r} 的 input_payload_digest 与输入 material 的 "
                f"payload_ref 重算不一致", topic_id))
        identity_digest = TS.sha256_canonical({
            "candidate_id": candidate.candidate_id,
            "candidate_revision": candidate.candidate_revision,
            "material_ids": list(material_ids),
            "material_content_hashes": sorted({m.content_hash for m in inputs}),
            "source_identity": decision.input_source_identity,
            "locator_digest": locator_digest,
            "payload_digest": payload_digest,
        })
        if identity_digest != decision.input_identity_digest:
            blocks.append(PackSetBlock(
                "decision_input_identity_mismatch",
                f"决定 {decision.decision_id!r} 的 input_identity_digest 与"
                f"（候选 + 输入材料内容哈希 + 来源身份 + 两个 digest）重算不一致", topic_id))
        if decision.verdict != "eligible":
            # rejected 决定可能**正好**因为来源身份不闭合而被拒，故不在此追加来源身份断言。
            continue
        for material in inputs:
            actual = TS.authority_source_identity(material.authority_assessment)
            if actual != decision.input_source_identity:
                blocks.append(PackSetBlock(
                    "decision_input_identity_mismatch",
                    f"决定 {decision.decision_id!r} 的 input_source_identity="
                    f"{decision.input_source_identity!r} 与输入 material "
                    f"{material.material_id!r} 的来源身份 {actual!r} 不一致"
                    f"（eligible 决定必须来源闭合）", topic_id))
        for f in pack.facts:
            if f.candidate_id != decision.candidate_id:
                continue
            for citation in f.citation_refs:
                try:
                    citation_identity = TS.citation_source_identity(citation)
                except TS.SchemaValidationError as e:
                    blocks.append(PackSetBlock(
                        "decision_input_identity_mismatch",
                        f"SupportedFact {f.fact_id!r} 的 citation 不可判定来源身份：{e}", topic_id))
                    continue
                if citation_identity != decision.input_source_identity:
                    blocks.append(PackSetBlock(
                        "decision_input_identity_mismatch",
                        f"SupportedFact {f.fact_id!r} 的 citation 来源身份 "
                        f"{citation_identity!r} 与 eligible 决定的 input_source_identity="
                        f"{decision.input_source_identity!r} 不一致", topic_id))


def _check_external_fact_closure(pack: TS.TopicResearchPack,
                                 resolver: TS.PayloadResolver,
                                 blocks: list[PackSetBlock],
                                 candidates: dict[str, TS.FactCandidate]) -> None:
    """formal `ExternalFact` 闭合：身份重算 + snapshot/body hash/SourcePolicy/日期/locator + payload 可解析。"""
    topic_id = pack.topic_id
    for fact in pack.external_facts:
        candidate = candidates.get(fact.candidate_id)
        expected_id = TS.external_fact_id_for(fact.source_snapshot_id, fact.statement, fact.locator)
        if expected_id != fact.external_fact_id:
            blocks.append(PackSetBlock(
                "external_fact_closure_mismatch",
                f"ExternalFact.external_fact_id={fact.external_fact_id!r} 与"
                f"（snapshot + 命题 + locator）重算值 {expected_id!r} 不符", topic_id))
        if fact.source_policy_version != pack.source_policy_version:
            blocks.append(PackSetBlock(
                "external_fact_closure_mismatch",
                f"ExternalFact {fact.external_fact_id!r} 的 source_policy_version="
                f"{fact.source_policy_version!r} 与 Pack 的 {pack.source_policy_version!r} 不一致",
                topic_id))
        try:
            recomputed = TS.recompute_authority_verdict(fact.source_authority)
        except TS.SchemaValidationError as e:
            blocks.append(PackSetBlock(
                "external_fact_closure_mismatch",
                f"ExternalFact {fact.external_fact_id!r} 的 source_authority 不可重算：{e}",
                topic_id))
            recomputed = None
        if recomputed is not None and (recomputed != fact.source_authority.verdict
                                       or recomputed != "authoritative"):
            blocks.append(PackSetBlock(
                "external_fact_closure_mismatch",
                f"ExternalFact {fact.external_fact_id!r} 的来源权威不合格：自报="
                f"{fact.source_authority.verdict!r}、重算={recomputed!r}", topic_id))
        as_of = _date_prefix(fact.as_of_date)
        if as_of is None:
            blocks.append(PackSetBlock(
                "external_fact_closure_mismatch",
                f"ExternalFact {fact.external_fact_id!r} 的 as_of_date={fact.as_of_date!r} "
                f"不是可比较的 ISO 日期前缀", topic_id))
        else:
            report_prefix = _date_prefix(pack.report_as_of or "")
            if report_prefix is not None and as_of > report_prefix:
                blocks.append(PackSetBlock(
                    "external_fact_closure_mismatch",
                    f"ExternalFact {fact.external_fact_id!r} 的 as_of_date={fact.as_of_date!r} "
                    f"晚于 Pack 的 report_as_of={pack.report_as_of!r}", topic_id))
        # locator 闭合：事实、payload_ref 与 snapshot 身份三方必须指向同一个 snapshot。
        if fact.payload_ref.locator.to_dict() != fact.locator.to_dict():
            blocks.append(PackSetBlock(
                "external_fact_closure_mismatch",
                f"ExternalFact {fact.external_fact_id!r} 的 locator 与 payload_ref.locator 不一致",
                topic_id))
        if fact.payload_ref.authority_identity != f"external_snapshot:{fact.source_snapshot_id}":
            blocks.append(PackSetBlock(
                "external_fact_closure_mismatch",
                f"ExternalFact {fact.external_fact_id!r} 的 payload_ref.authority_identity 与 "
                f"snapshot 身份不一致", topic_id))
        if fact.locator.canonical_url != fact.canonical_url:
            blocks.append(PackSetBlock(
                "external_fact_closure_mismatch",
                f"ExternalFact {fact.external_fact_id!r} 的 locator.canonical_url 与 "
                f"canonical_url 不一致", topic_id))
        if fact.source_authority.content_hash != fact.body_hash:
            blocks.append(PackSetBlock(
                "external_fact_closure_mismatch",
                f"ExternalFact {fact.external_fact_id!r} 的 body_hash 与 source_authority."
                f"content_hash 不一致", topic_id))
        if candidate is not None and candidate.source_snapshot_id != fact.source_snapshot_id:
            blocks.append(PackSetBlock(
                "external_fact_closure_mismatch",
                f"ExternalFact {fact.external_fact_id!r} 的 source_snapshot_id 与候选不一致",
                topic_id))
        try:
            TS.verify_material_payload_ref(fact.payload_ref, resolver)
        except TS.SchemaValidationError as e:
            blocks.append(PackSetBlock(
                "payload_unresolvable",
                f"ExternalFact {fact.external_fact_id!r} 的 payload_ref 不可解析：{e}", topic_id))


def _basis_fingerprints(aspect: TS.AspectResearchResult | None) -> set[str]:
    """与 runtime 同一口径重算 aspect 的 Contract basis fingerprint（三个候选值取集合）。"""
    if aspect is None:
        return set()
    snapshot = aspect.requirement_snapshot
    basis: set[str] = set()
    if snapshot.canonical_fingerprint:
        basis.add(snapshot.canonical_fingerprint)
    if snapshot.contract_sha256:
        basis.add(snapshot.contract_sha256)
    basis.add(TS.sha256_canonical(snapshot.to_dict()))
    return basis


def _check_gap_and_block_basis(pack: TS.TopicResearchPack,
                               blocks: list[PackSetBlock]) -> None:
    """每个 gap / block 必须绑定 **current** Contract basis 与非空 required-unmet 证明。

    - basis = `contract_version` 与 aspect 冻结投影重算出的 requirement fingerprint；
    - 不产出新对象、不把 rejected 决定当 gap：`rejected_candidate_ids`（若有）必须逐个解析到
      本 Pack 内**确实被拒**且覆盖该 aspect 的 candidate，只是回指，不构成 gap 的成因。
    """
    topic_id = pack.topic_id
    aspects = {r.aspect_id: r for r in pack.aspect_results}
    question_ids = set(pack.question_ids)
    candidates = {c.candidate_id: c for c in pack.fact_candidates}
    rejected = {d.candidate_id for d in pack.fact_qualification_decisions
                if d.verdict == "rejected"}

    def _basis_and_proof(label: str, obj: Any) -> None:
        basis = _basis_fingerprints(aspects.get(obj.aspect_id))
        if obj.topic_id != pack.topic_id:
            blocks.append(PackSetBlock(
                "gap_basis_mismatch",
                f"{label} {obj.aspect_id!r} 的 topic_id={obj.topic_id!r} 与 Pack 不一致", topic_id))
        if obj.aspect_id not in aspects:
            blocks.append(PackSetBlock(
                "gap_basis_mismatch",
                f"{label} 引用了本 Pack 之外的 aspect {obj.aspect_id!r}", topic_id))
        if obj.contract_version != pack.contract_version:
            blocks.append(PackSetBlock(
                "gap_basis_mismatch",
                f"{label} {obj.aspect_id!r} 的 contract_version={obj.contract_version!r} "
                f"与 Pack 的 {pack.contract_version!r} 不一致（不得用历史 Contract basis）",
                topic_id))
        if obj.requirement_fingerprint not in basis:
            blocks.append(PackSetBlock(
                "gap_basis_mismatch",
                f"{label} {obj.aspect_id!r} 的 requirement_fingerprint 与 aspect 冻结投影重算"
                f"不一致（current Contract basis 不成立）", topic_id))
        extra_questions = sorted(set(obj.question_ids) - question_ids)
        if extra_questions:
            blocks.append(PackSetBlock(
                "gap_basis_mismatch",
                f"{label} {obj.aspect_id!r} 引用了本 Pack 之外的 question {extra_questions}",
                topic_id))
        if not obj.unmet_required_items or any(
                not str(item).strip() for item in obj.unmet_required_items):
            blocks.append(PackSetBlock(
                "gap_basis_mismatch",
                f"{label} {obj.aspect_id!r} 的 unmet_required_items 为空或含空白项"
                f"（无 Contract-required 未满足项即不得形成 gap/block）", topic_id))
        if not obj.required_unmet_proof.strip():
            blocks.append(PackSetBlock(
                "gap_basis_mismatch",
                f"{label} {obj.aspect_id!r} 缺少非空 required-unmet 证明", topic_id))

    for gap in pack.contract_gaps:
        _basis_and_proof("ContractGap", gap)
        for candidate_id in gap.rejected_candidate_ids:
            candidate = candidates.get(candidate_id)
            if candidate is None or candidate_id not in rejected:
                blocks.append(PackSetBlock(
                    "gap_basis_mismatch",
                    f"ContractGap {gap.gap_id!r} 回指的 rejected candidate "
                    f"{candidate_id!r} 不是本 Pack 内被拒的候选", topic_id))
            elif gap.aspect_id not in candidate.aspect_ids:
                blocks.append(PackSetBlock(
                    "gap_basis_mismatch",
                    f"ContractGap {gap.gap_id!r} 回指的 candidate {candidate_id!r} 不覆盖 "
                    f"aspect {gap.aspect_id!r}", topic_id))
    for block in pack.research_blocks:
        _basis_and_proof("ResearchBlock", block)
        try:
            expected = TS.derive_research_block_id(
                block.topic_id, block.aspect_id, block.block_kind, block.unmet_required_items)
        except TS.SchemaValidationError as e:
            blocks.append(PackSetBlock(
                "gap_basis_mismatch",
                f"ResearchBlock {block.block_id!r} 身份不可重算：{e}", topic_id))
            continue
        if expected != block.block_id:
            blocks.append(PackSetBlock(
                "gap_basis_mismatch",
                f"ResearchBlock.block_id={block.block_id!r} 与确定性重算值 {expected!r} 不符",
                topic_id))


def _check_unfiltered_records(pack: TS.TopicResearchPack,
                              blocks: list[PackSetBlock]) -> None:
    """双轴状态自洽 + gap/not-found/conflict 没有被过滤。"""
    topic_id = pack.topic_id
    results = {r.aspect_id: r for r in pack.aspect_results}
    all_ids = set(results)
    cov = pack.coverage_status

    groups = {
        "covered": set(cov.covered_aspect_ids),
        "gap": set(cov.gap_aspect_ids),
        "not_applicable": set(cov.not_applicable_aspect_ids),
    }
    flat = [i for g in groups.values() for i in g]
    if len(flat) != len(set(flat)):
        blocks.append(PackSetBlock(
            "coverage_status_inconsistent",
            f"topic={topic_id!r} 的 coverage 分组重叠：{sorted(cov.to_dict().items())}", topic_id))
    if set(flat) != all_ids:
        blocks.append(PackSetBlock(
            "coverage_status_inconsistent",
            f"topic={topic_id!r} 的 coverage 分组未精确覆盖全部 aspect："
            f"缺 {sorted(all_ids - set(flat))}，多 {sorted(set(flat) - all_ids)}", topic_id))

    declared_covered = {aid for aid, r in results.items() if r.status == "covered"}
    if declared_covered != groups["covered"]:
        blocks.append(PackSetBlock(
            "coverage_status_inconsistent",
            f"topic={topic_id!r} coverage.covered_aspect_ids 与逐 aspect status 不一致："
            f"coverage={sorted(groups['covered'])}，status=covered 的={sorted(declared_covered)}",
            topic_id))
    declared_na = {aid for aid, r in results.items() if r.status == "not_applicable"}
    if declared_na != groups["not_applicable"]:
        blocks.append(PackSetBlock(
            "coverage_status_inconsistent",
            f"topic={topic_id!r} coverage.not_applicable_aspect_ids 与逐 aspect status 不一致",
            topic_id))
    residual = all_ids - declared_covered - declared_na
    if residual != groups["gap"]:
        blocks.append(PackSetBlock(
            "coverage_status_inconsistent",
            f"topic={topic_id!r} coverage.gap_aspect_ids 未覆盖全部非 covered/not_applicable "
            f"aspect：应含 {sorted(residual)}，实为 {sorted(groups['gap'])}", topic_id))

    # 缺口记录不得丢：每条 aspect 的 unresolved_ids 必须真在 Pack.unresolved 里。
    gap_ids = {g.unresolved_id for g in pack.unresolved}
    for aid, r in results.items():
        missing = [u for u in r.unresolved_ids if u not in gap_ids]
        if missing:
            blocks.append(PackSetBlock(
                "unresolved_record_missing",
                f"topic={topic_id!r} aspect {aid!r} 引用的 unresolved {missing} 不在 Pack.unresolved 中",
                topic_id))
        if r.not_found_audit_id is not None:
            known = {n.audit_id for n in pack.not_found_audits}
            if r.not_found_audit_id not in known:
                blocks.append(PackSetBlock(
                    "not_found_audit_missing",
                    f"topic={topic_id!r} aspect {aid!r} 引用的 not_found_audit "
                    f"{r.not_found_audit_id!r} 不在 Pack.not_found_audits 中", topic_id))


def resolver_of(store: Any) -> Any:
    """从 Pack store 取**重切解析器**，能力不符时抛 `PackSetError`（§三 G）。

    `store.resolver` 是契约字段，但「契约要求它存在」不等于「传进来的对象一定有它」：
    直接属性访问在越界对象上会抛裸 `AttributeError`，把一次**能力不符**伪装成一次实现崩溃，
    调用方既拿不到 `PackSetError` 的语义（「读门不得静默放宽」），也无法在下游按类型收敛。
    这里把「缺 store」「缺 resolver 能力」「resolver 为 None」三种情形统一成同一个 typed 拒绝，
    且**不**提供任何降级路径（没有解析器就无法证明 payload 可解析）。
    """
    if store is None:
        raise PackSetError("resolve_pack_set 需要 PackSetStore")
    if not hasattr(store, "resolver"):
        raise PackSetError(
            f"PackSetStore 能力不符：{type(store).__name__} 没有 `resolver` 字段"
            "（没有重切解析器就无法证明 payload 可解析；读门不得静默放宽，fail-closed）")
    resolver = store.resolver
    if resolver is None:
        raise PackSetError(
            "store.resolver 缺失：没有重切解析器就无法证明 payload 可解析，"
            "读门不得静默放宽（fail-closed）")
    return resolver


def resolve_pack_set(task: PS.SectionTask,
                     expected_requirements: Sequence[TS.TopicResearchRequirement],
                     store: PackSetStore) -> VerifiedPackSet:
    """把 `SectionTask` 的 topic ids 解析成一组**完整、current、同绑定**的权威 Pack。

    - `SectionTask.topic_ids` 与 expected_requirements 的 topic 集合必须**精确等集**；
    - 每 topic 恰一 current Pack，且必须落在本 task 当前 Contract/SourcePolicy 的 identity 上；
    - selected aspect 精确覆盖（不缺、不重、不混入他 topic/aspect 的投影）；
    - payload 可解析、材料 authority 可重算、事实有同源材料背书；
    - gap / conflict / not-found 记录原样保留，未过滤。

    任何一项不成立 → `PackSetBlocked`（携带全部 block）。调用方用法/依赖不合法 →
    `PackSetError`（例如 `store.resolver` 缺失、`list_current` 不可用）。
    """
    if not isinstance(task, PS.SectionTask):
        raise PackSetError(f"resolve_pack_set 需要 SectionTask，得到 {type(task).__name__}")
    requirements = tuple(expected_requirements)
    if not requirements:
        raise PackSetError("expected_requirements 不得为空（空集不得冒充『完整 Pack set』）")
    for r in requirements:
        if not isinstance(r, TS.TopicResearchRequirement):
            raise PackSetError(
                f"expected_requirements 只接受 TopicResearchRequirement，得到 {type(r).__name__}")
    if store is None:
        raise PackSetError("resolve_pack_set 需要 PackSetStore")
    resolver = resolver_of(store)

    blocks: list[PackSetBlock] = []

    # --- 1. topic 集合精确等集（重复也是 block，不是「取一个」）---------------
    task_topics = tuple(task.topic_ids)
    if len(set(task_topics)) != len(task_topics):
        blocks.append(PackSetBlock(
            "task_topic_set_invalid",
            f"SectionTask.topic_ids 含重复：{list(task_topics)}"))
    req_topics = tuple(r.topic_id for r in requirements)
    if len(set(req_topics)) != len(req_topics):
        blocks.append(PackSetBlock(
            "requirements_topic_set_invalid",
            f"expected_requirements 含重复 topic_id：{list(req_topics)}"))
    if set(task_topics) != set(req_topics):
        blocks.append(PackSetBlock(
            "requirements_topic_set_invalid",
            f"topic 集合不精确等集：SectionTask 有 {sorted(set(task_topics))}，"
            f"requirements 有 {sorted(set(req_topics))}；"
            f"缺 {sorted(set(task_topics) - set(req_topics))}，"
            f"多 {sorted(set(req_topics) - set(task_topics))}"))
    if blocks:
        raise PackSetBlocked(blocks)

    # --- 2. task ↔ requirement 绑定、一致性、依赖新鲜度 --------------------
    binding = _check_requirement_binding(task, requirements, blocks)

    # --- 3. 同槽异版本 current 检测（在解析之前，先证明「没有别的 current 冒充」）--
    _check_foreign_current(task, requirements, store, blocks)

    # --- 4. 逐 topic 读 current 并重算全部身份/内容门 ----------------------
    by_topic = {r.topic_id: r for r in requirements}
    ordered_reqs = tuple(by_topic[t] for t in task_topics)
    packs: list[TS.TopicResearchPack] = []
    for requirement in ordered_reqs:
        topic_id = requirement.topic_id
        identity = _identity_of(requirement)
        load = store.load_current(identity)
        if load.pack is None:
            reason = load.reason or "no_current"
            if reason == "no_current":
                blocks.append(PackSetBlock(
                    "no_current_pack",
                    f"topic={topic_id!r} 在 identity "
                    f"(contract_fingerprint={identity.contract_fingerprint[:16]}…, "
                    f"source_policy_version={identity.source_policy_version!r}) 上没有 current Pack",
                    topic_id))
            else:
                blocks.append(PackSetBlock(
                    "current_pack_invalidated",
                    f"topic={topic_id!r} 的 current Pack 已被标记 {reason!r}（失效不得被当可用）",
                    topic_id))
            continue
        pack = load.pack
        _check_pack_against_requirement(pack, requirement, resolver, blocks)
        _check_material_and_fact_authority(pack, resolver, blocks)
        _check_unfiltered_records(pack, blocks)
        packs.append(pack)

    if blocks:
        raise PackSetBlocked(blocks)

    # --- 5. 每 topic 恰一 current（按 pack_id 去重后仍须逐 topic 唯一）-------
    seen_ids: set[str] = set()
    for pack in packs:
        if pack.pack_id in seen_ids:
            raise PackSetBlocked([PackSetBlock(
                "current_set_not_unique",
                f"同一个 current Pack {pack.pack_id[:16]}… 被多个 topic 解析到", pack.topic_id)])
        seen_ids.add(pack.pack_id)

    assert binding is not None  # 无 block 即证明绑定一致（_check_requirement_binding 已返回）
    return VerifiedPackSet(
        task_id=task.task_id, section_id=task.section_id, topic_ids=task_topics,
        packs=tuple(packs), requirements=ordered_reqs, binding=binding)


# ---------------------------------------------------------------------------
# 读适配器（composition root 用；与 harness.topic_runtime.SqliteTopicStoreAdapter
# 同一「薄封装既有 topic_store 公共函数」立场，不新建 schema、不新建 Table、不发明第二 Store）
# ---------------------------------------------------------------------------

@dataclass
class SqlitePackSetStore:
    """把既有 `harness.topic_store` 的**只读**公共函数绑到一个确定的库路径。

    只读：本适配器不提供 commit / 切换 current 的任何入口。
    路径由调用方给定，绝不在此 init/迁移库（真实库的迁移由 composition root 显式负责）。
    """

    path: Any
    resolver: TS.PayloadResolver | None = None

    def _require_bound_path(self) -> None:
        """证明本适配器读的就是 `path` 指向的那个库。

        既有 `topic_store` 的公共函数按**模块级绑定路径**工作（由 composition root 的
        `init_topic_store` 设定）。若两者不一致，本适配器会静默读到**另一个**库的 current
        ——那正是「读门在看别的证据」这类最危险的错觉，故 fail-closed。
        """
        from harness import topic_store as Store
        bound = Store._db_path
        if bound is None:
            raise PackSetError(
                "topic_store 尚未绑定库路径（composition root 必须先 init_topic_store）；"
                "读门不得自己猜库位置")
        if Path(bound).resolve() != Path(self.path).resolve():
            raise PackSetError(
                f"SqlitePackSetStore.path={str(self.path)!r} 与 topic_store 已绑定的库 "
                f"{str(bound)!r} 不一致（拒绝读别的库，fail-closed）")

    def load_current(self, identity: TS.PackIdentity) -> PackSetCurrentLoad:
        self._require_bound_path()
        from harness import topic_store as Store
        load = Store.load_current_pack(identity)
        if load.available:
            return PackSetCurrentLoad(pack=load.pack)
        # reason 原样传出（no_current / stale / invalidated / quarantined），
        # 读门据此区分「还没有」与「有但已失效」——两者都 block，但原因不同。
        return PackSetCurrentLoad(pack=None, reason=load.reason)

    def list_current(self) -> tuple[TS.TopicResearchPack, ...]:
        self._require_bound_path()
        from harness import topic_store as Store
        return tuple(Store.list_current_packs())

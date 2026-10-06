"""Phase 4 章节产物数据模型（Claim / Citation / Unresolved / Result / Evaluation）。

只定义声明式数据结构、状态枚举与确定性身份/版本派生，不含 I/O、不含业务计算。
- CitationRef 直接复用 Phase 3 harness.schema.CitationRef（任务书 §7.5），不造第二套引用。
- claim_type 第一阶段仅 fact | calculation | inference；不生成 recommendation（授信建议属 Phase 5）。
- COMPLETED_WITH_GAPS ≠ 严格通过；SECTION_BLOCKED 不阻止其他章节。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field

from contracts import schema as _CS  # 只读：阻断等级白名单来自契约，不在此重定义
from harness.schema import CitationRef  # 复用，禁止重定义

# 章节工作流状态（任务书 §7.7）
SECTION_STATUSES = (
    "PLANNED",
    "RESEARCHING",
    "DRAFT_READY",
    "EVALUATING",
    "REWORK_REQUIRED",
    "COMPLETED",
    "COMPLETED_WITH_GAPS",
    "SECTION_BLOCKED",
    "WAITING_HUMAN",
    "FAILED",
)

# 第一阶段 claim 类型（fact 事实 / calculation 计算值 / inference 研判；
# 不含 recommendation —— 授信建议属 Phase 5）
CLAIM_TYPES = ("fact", "calculation", "inference")

# 评估决策
EVALUATION_DECISIONS = ("PASS", "PASS_WITH_GAPS", "REWORK", "BLOCKED", "FAILED")

# 置信度（与 harness 对齐）
CONFIDENCE_LEVELS = ("high", "low")

# 引用类别（与 harness.schema.CITATION_TYPES 对齐，不重复定义）
CITATION_TYPES = ("evidence", "structured", "external")

# Claim / 表格 wire 的结构版本。它们**不是**第二套报告版本：报告版本只有唯一权威根
# `sections.backbone_schema.ReportVersionIdentity`，这两个常量只是该身份白名单里
# `claim_schema_version` / `table_schema_version` 两个字段的**声明值**来源。
CLAIM_SCHEMA_VERSION = "claim-2"
TABLE_SCHEMA_VERSION = "table-1"
#: `SectionResult` wire 的**强制** schema marker（migration·readback 真值表 §16.8）。
#: 旧形状没有 marker，因此只能经 `load_legacy_section_result_for_audit` 只读回放。
SECTION_RESULT_SCHEMA_VERSION = "section-result-2"
#: 登记过的 legacy wire 版本。current reader **必须**拒绝它们；历史对象只走显式 legacy reader。
LEGACY_CLAIM_SCHEMA_VERSIONS = ("claim-1",)
#: 旧 `SectionResult` 形状**没有** marker，故空串（缺 marker）也是登记过的 legacy 形态。
LEGACY_SECTION_RESULT_SCHEMA_VERSIONS = ("", "section-result-1")


class SectionSchemaError(Exception):
    """章节 wire 的构造期 fail-closed 错误（身份与内容必须自洽）。"""


def normalize_blocking_effects(raw: object) -> tuple[str, ...]:
    """把「声明的阻断后果」规范化成 `SectionUnresolved.blocking_effects` 的合法形态。

    为什么必须有这套规范化（且**只能有一份**）：
    * 契约会把「不阻断」显式写成字面量 `NONE`（`templates/contracts/standard_v3.yaml`
      实际如此；`contracts.blocking.blocking_label([])` 也把空集合渲染成 `NONE`），
      因此权威问题声明里的 `NONE` 与「空集合」是**同一件事**；
    * 但 `blocking_effects` 是后果**集合**，`sections.validator.validate_unresolved`
      明确拒绝 `NONE`（`b not in BLOCKING_LEVELS or b == "NONE"` → 非法）；
    * 于是「问题声明的后果」与「未解决项登记的后果」在比对前必须过同一套规范化，
      否则 `NONE` 问题永远无法产出自洽产物。

    规范化 = 只保留 canonical 阻断等级、丢掉 `NONE` 哨兵、保序去重、剔除越界值。
    它是 `status`（`SectionResult` 的字段，参与内容寻址身份）的唯一判据来源，因此放在
    schema 层：生产端（写作器）与校验端（规则评估器）都必须复用它，不得各写一套。
    """
    values = (raw,) if isinstance(raw, str) else tuple(raw or ())
    out: list[str] = []
    for value in values:
        text = str(value)
        if text in _CS.BLOCKING_LEVELS and text != "NONE" and text not in out:
            out.append(text)
    return tuple(out)


def sha256_json(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def citation_identity(ref: CitationRef) -> str:
    """一条引用的身份字符串（供 claim_id 派生与去重）。"""
    if ref.ref_type == "evidence":
        return f"evidence:{ref.evidence_id}:{ref.page_number or ''}"
    if ref.ref_type == "structured":
        return (f"structured:{ref.snapshot_id}:{ref.item_code or ''}:"
                f"{ref.formula_id or ''}:{ref.formula_version or ''}:{ref.period or ''}")
    if ref.ref_type == "external":
        return f"external:{ref.source_snapshot_id}"
    return f"{ref.ref_type}:?"


def derive_claim_id(claim_type: str, topic_id: str, question_ids, text: str,
                    citation_refs, claim_candidate_id: str = "",
                    claim_candidate_revision: str = "",
                    accepted_binding_ids=()) -> str:
    """claim_id 派生（任务书 §8.3）：claim 类型 + 主题/问题 + 规范正文 + 引用身份。

    `claim-2`（M930-3A）把**来源候选 revision** 与**完整 factual accepted-binding 集**
    一并纳入派生：换 candidate、换 revision 或换 binding 集都必须换出新 claim_id，否则
    「同一 claim_id 对应两条不同血缘」将无法区分。输入集**已前进**，不得声称不变。
    """
    qids = sorted(list(question_ids))
    cids = sorted(citation_identity(r) for r in citation_refs)
    bindings = sorted(str(b) for b in accepted_binding_ids)
    digest = sha256_json([CLAIM_SCHEMA_VERSION, claim_type, topic_id, qids, text, cids,
                          claim_candidate_id, claim_candidate_revision, bindings])
    return f"claim_{digest[:24]}"


def derive_citation_id(claim_id: str, ref: CitationRef) -> str:
    """citation_id 稳定派生：claim_id + 引用身份哈希（同内容幂等）。

    用于 section_citation 落盘与 validator 的同一结果内重复检测。不把 section_result_id
    混入（保持内容身份），跨 SectionResult 相同内容合法（Store 用复合归属键）。
    """
    digest = sha256_json([claim_id, citation_identity(ref)])
    return f"cite_{digest[:24]}"


def _claim_keys(claims) -> list[str]:
    """Claim 身份：既接受 `SectionClaim` 对象，也接受已派生的 claim_id 字符串。"""
    out = []
    for item in claims:
        key = getattr(item, "claim_id", None)
        if key is None:
            key = str(item)
        out.append(str(key))
    return sorted(out)


def _unresolved_keys(unresolved) -> list[str]:
    out = []
    for item in unresolved:
        key = getattr(item, "unresolved_id", None)
        if key is None:
            key = str(item)
        out.append(str(key))
    return sorted(out)


def derive_section_version(task_id: str, claims, unresolved, *,
                           section_draft_id: str, renderer_version: str, rules_version: str,
                           dependency_fingerprint: str = "",
                           markdown_fingerprint: str = "") -> str:
    """section_version 派生（任务书 §8.4）：task + Claims + Unresolved + 版本 + 依赖指纹。

    dependency_fingerprint 编码 snapshot_id / required_formula_versions / 契约与任务依赖 /
    prompt/renderer/rules/worker 版本，必须进入派生 —— 同内容但任一依赖版本变化 → 新版本；
    完全相同输入 → 严格复用。缺省空串保持对旧调用方向后兼容。

    §五：`markdown_fingerprint` 是正文字节的 sha256 —— **版本必须覆盖 Markdown**，否则
    「Claims 不变、正文变了」会得到同一个 section_version，正文就与身份脱钩了。
    缺省空串只用于不产正文的调用方（缺口清单等），产正文的调用方必须显式传入。

    M930-3A：`section_draft_id` 为**必填关键字**（无默认值）—— current Result 必须由它
    所引用的 Draft 决定身份；省略即等于「版本不覆盖 Draft」，属 fail-open，故不给默认值。
    """
    if not isinstance(section_draft_id, str) or not section_draft_id.strip():
        raise SectionSchemaError(
            "derive_section_version 必须传入非空 section_draft_id（版本必须覆盖 Draft identity）")
    claim_hashes = _claim_keys(claims)
    unresolved_hashes = _unresolved_keys(unresolved)
    digest = sha256_json([CLAIM_SCHEMA_VERSION, SECTION_RESULT_SCHEMA_VERSION,
                          task_id, claim_hashes, unresolved_hashes,
                          renderer_version, rules_version, section_draft_id,
                          dependency_fingerprint, markdown_fingerprint])
    return f"secver_{digest[:24]}"


def derive_legacy_section_version(task_id: str, claims, unresolved, *,
                                  renderer_version: str, rules_version: str,
                                  dependency_fingerprint: str = "") -> str:
    """**legacy** section_version 派生（P25–P32 历史 producer 专用）。

    与 current `derive_section_version` 的关键差别：legacy 形状没有 Draft、没有 candidate
    revision、没有 accepted binding，因此**不可能**也**不得**把 Draft identity 混进派生。
    本函数逐字节复刻 M930-3 之前的派生输入集（`task_id` + 排序 claim_id 序列 + 排序
    unresolved_id 序列 + renderer/rules 版本 + 依赖指纹），使既有 Phase 4 产物的
    `section_version` / `section_result_id` **保持不变**——历史 run、gold 与 split 的
    身份都锚在这些 ID 上，重算一次就等于改写历史。

    current 链**不得**调用本函数：`SectionResult`(result-2) 的身份必须覆盖 Draft identity。
    """
    claim_ids = sorted(c.claim_id for c in claims)
    unresolved_ids = sorted(u.unresolved_id for u in unresolved)
    digest = sha256_json([task_id, claim_ids, unresolved_ids,
                          renderer_version, rules_version, dependency_fingerprint])
    return f"secver_{digest[:24]}"


def derive_section_result_id(section_version: str) -> str:
    """section_result_id 由 section_version 稳定派生（内容寻址，幂等复用）。

    section_version 已编码 task + claims + unresolved + renderer/rules 版本，故
    同内容恒得同 section_result_id，可复用；任何依赖变化 → 新版本 → 新 result id。
    """
    return f"sr_{section_version}"


def derive_issue_id(rule_id: str, location: str, detail: str, severity: str) -> str:
    """issue_id 内容寻址（同规则 + 同定位 + 同详情 → 同 id，幂等）。"""
    return f"iss_{sha256_json([rule_id, location, detail, severity])[:24]}"


def derive_rework_target_id(target_kind: str, target_ref: str, reason: str) -> str:
    """rework_target_id 内容寻址（同目标 + 同原因 → 同 id，幂等）。"""
    return f"tgt_{sha256_json([target_kind, target_ref, reason])[:24]}"


def derive_evaluation_id(section_result_id: str, decision: str, rules_version: str,
                         evaluator_prompt_version: str, issues, rework_targets,
                         llm_evaluator_calls: int) -> str:
    """evaluation_id 内容寻址（同内容 → 同 id，幂等复用；任何决策/issue/target/调用次
    数变化 → 新 id）。issues/rework_targets 就地序列化（自包含，避免依赖后文序列化函数）。
    """
    issues_sig = [{"issue_id": i.issue_id, "rule_id": i.rule_id, "severity": i.severity,
                   "location": i.location, "detail": i.detail,
                   "suggested_action": i.suggested_action} for i in issues]
    targets_sig = [{"target_id": t.target_id, "target_kind": t.target_kind,
                    "target_ref": t.target_ref, "reason": t.reason} for t in rework_targets]
    digest = sha256_json([section_result_id, decision, rules_version,
                          evaluator_prompt_version, issues_sig, targets_sig,
                          llm_evaluator_calls])
    return f"eval_{digest[:24]}"


def derive_rework_run_id(from_section_result_id: str, section_result_id: str,
                         batch_no: int) -> str:
    """rework_run_id 内容寻址：父结果 + 返工后新结果 + 批次（本阶段恒 batch 0）。"""
    digest = sha256_json([from_section_result_id, section_result_id, batch_no])
    return f"rr_{digest[:24]}"


def derive_manifest_id(job_id: str, run_id: str, code_fingerprint: str,
                       phase3_closure_fingerprint: str, batch_versions: dict,
                       frozen: dict) -> str:
    """manifest_id 内容寻址：job + run + 代码指纹 + Phase 3 关闭指纹 + 批次版本 + 冻结输入。"""
    digest = sha256_json([job_id, run_id, code_fingerprint,
                          phase3_closure_fingerprint, batch_versions, frozen])
    return f"manifest_{digest[:24]}"


@dataclass(frozen=True)
class CommitResult:
    """commit_plan 的返回（不可变）。"""

    plan_id: str
    reused: bool
    current_switched: bool
    task_count: int


@dataclass(frozen=True)
class SectionCommitResult:
    """commit_section_result 的返回（不可变）。"""

    section_result_id: str
    reused: bool
    current_switched: bool
    claim_count: int
    unresolved_count: int


@dataclass(frozen=True)
class SectionClaim:
    """章节中的一个断言（任务书 §7.4；citation_refs 复用 harness CitationRef）。

    `claim-2`（M930-3A/3B）：current Claim 必须**单向回指**它来源的 candidate revision，并
    携带**完整**的 factual accepted-binding 集。这些字段**没有**默认值 —— 缺 `claim-2`
    marker 或缺 candidate/binding 的旧载荷只能走 `load_legacy_claim_claim1_for_audit`，
    不得被当成 current Claim 静默读出。

    方向规则：本对象**后于** accepted binding 形成，因此它引用 binding ID；binding 侧
    （`AcceptedSupportBinding`）**没有** `section_claim_id` 字段，反向边不可表达。
    """

    claim_id: str
    schema_version: str
    section_id: str
    topic_id: str
    question_ids: tuple[str, ...]
    text: str
    claim_type: str
    citation_refs: tuple[CitationRef, ...]
    claim_candidate_id: str
    claim_candidate_revision: str
    accepted_binding_ids: tuple[str, ...]
    derived_from_claim_ids: tuple[str, ...] = ()
    confidence: str = "low"
    as_of_date: str | None = None
    impact_scope: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != CLAIM_SCHEMA_VERSION:
            raise SectionSchemaError(
                f"SectionClaim.schema_version 必须为 {CLAIM_SCHEMA_VERSION!r}，得到 "
                f"{self.schema_version!r}（claim-1 只经 load_legacy_claim_claim1_for_audit 只读回放）")
        for name in ("claim_candidate_id", "claim_candidate_revision"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise SectionSchemaError(
                    f"SectionClaim.{name} 不得为空（current Claim 必须回指来源 candidate revision）")
        bindings = tuple(self.accepted_binding_ids or ())
        for binding in bindings:
            if not isinstance(binding, str) or not binding.strip():
                raise SectionSchemaError("SectionClaim.accepted_binding_ids 含空值")
        if len(set(bindings)) != len(bindings):
            raise SectionSchemaError(
                "SectionClaim.accepted_binding_ids 含重复（accepted set 必须是集合）")
        if self.claim_type == "fact" and not bindings:
            raise SectionSchemaError(
                "factual SectionClaim 必须携带至少一条 factual accepted_binding_id"
                "（缺 binding 的断言不得进入 current Claim）")
        object.__setattr__(self, "accepted_binding_ids", bindings)
        expected = derive_claim_id(self.claim_type, self.topic_id, self.question_ids, self.text,
                                   self.citation_refs, self.claim_candidate_id,
                                   self.claim_candidate_revision, bindings)
        if self.claim_id != expected:
            raise SectionSchemaError(
                f"SectionClaim.claim_id 与内容不符：声明 {self.claim_id!r}，应为 {expected!r}")


@dataclass(frozen=True)
class SectionUnresolved:
    """章节中一个未解决的问题缺口（任务书 §7.6）。"""

    unresolved_id: str
    section_id: str
    topic_id: str
    question_id: str | None
    state: str
    reason_code: str
    detail: str
    impact_scope: tuple[str, ...] = ()
    blocking_effects: tuple[str, ...] = ()
    attempted_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class SectionIssue:
    """评估发现的问题（任务书 §7.8 局部）。"""

    issue_id: str
    rule_id: str
    severity: str
    location: str
    detail: str
    suggested_action: str = ""


@dataclass(frozen=True)
class ReworkTarget:
    """有界返工目标（任务书 §7.8 局部）。"""

    target_id: str
    target_kind: str     # question | topic | claim
    target_ref: str
    reason: str


def canonicalize_rework_targets(
        targets: "tuple[ReworkTarget, ...] | list[ReworkTarget]") -> "tuple[tuple[ReworkTarget, ...], int]":
    """按 ``target_id`` 稳定去重（保留首次出现顺序），返回 ``(去重后, 重复条数)``。

    复用边界：Rules Evaluator 会对同一 claim 的多条失败引用各自 emit 相同
    ``(target_kind, target_ref, reason)`` → 内容寻址 ``target_id`` 相同。若不去重，
    ``commit_evaluation`` 逐条写 ``section_rework`` 会撞 ``rework_id`` 主键（
    ``_rework_row_id`` 只按 ``(evaluation_id, target_id)`` 隔离，同一 Evaluation 内
    target_id 重复即冲突）。本函数只折叠**完全相同**的 ReworkTarget，issues 明细由调用方
    全量保留，不经此处。
    """
    seen: set[str] = set()
    deduped: list[ReworkTarget] = []
    duplicate_count = 0
    for t in targets:
        if t.target_id in seen:
            duplicate_count += 1
            continue
        seen.add(t.target_id)
        deduped.append(t)
    return tuple(deduped), duplicate_count


@dataclass(frozen=True)
class SectionEvaluation:
    """章节评估结论（任务书 §7.8）。"""

    evaluation_id: str
    section_result_id: str
    rules_version: str
    evaluator_prompt_version: str
    rules_passed: bool
    llm_passed: bool | None
    decision: str
    issues: tuple[SectionIssue, ...] = ()
    rework_targets: tuple[ReworkTarget, ...] = ()
    evaluated_at: str = ""
    # 本评估步骤实际调用的 LLM Evaluator 次数，必须 ∈ {0,1}；>1 → fail-closed。
    llm_evaluator_calls: int = 0


@dataclass(frozen=True)
class SectionResult:
    """一个章节的最终产物（任务书 §7.5/§7.7）。

    `section-result-2`（M930-3A）：current Result **必须**单向引用 `section_draft_id`，且
    **禁止**内嵌 `evaluation` —— 旧 Result 把 `SectionEvaluation`（其自身回指
    `section_result_id`）装进 Result，使 post-gate 身份成环（C8/C15/C16）。本类**没有**
    `evaluation` 字段，该环在类型层不可表达；`SectionEvaluation` 与 Result 的绑定由
    `NarrativeEvaluationBinding`（draft + result + evaluation）承载。
    """

    section_result_id: str
    schema_version: str
    section_version: str
    section_draft_id: str
    task_id: str
    section_id: str
    status: str
    claims: tuple[SectionClaim, ...] = ()
    unresolved: tuple[SectionUnresolved, ...] = ()
    markdown: str = ""
    #: §五：正文的 sha256。`section_version` **必须覆盖** Markdown —— 只改正文不改 Claims
    #: 也必须换出新的 section_version/section_result_id，否则正文与身份脱钩。
    #: 缺省空串只用于不产正文的历史调用方（向后兼容），一旦给出就必须与 markdown 自洽。
    markdown_fingerprint: str = ""
    source_run_ids: tuple[str, ...] = ()
    source_question_ids: tuple[str, ...] = ()
    dependency_fingerprint: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != SECTION_RESULT_SCHEMA_VERSION:
            raise SectionSchemaError(
                f"SectionResult.schema_version 必须为 {SECTION_RESULT_SCHEMA_VERSION!r}，得到 "
                f"{self.schema_version!r}（旧形状只经 load_legacy_section_result_for_audit "
                "只读回放）")
        if not isinstance(self.section_draft_id, str) or not self.section_draft_id.strip():
            raise SectionSchemaError(
                "SectionResult.section_draft_id 不得为空（current Result 必须单向引用 Draft）")
        if self.markdown_fingerprint:
            expected = hashlib.sha256(self.markdown.encode("utf-8")).hexdigest()
            if self.markdown_fingerprint != expected:
                raise SectionSchemaError(
                    "SectionResult.markdown_fingerprint 与 markdown 不符："
                    f"声明 {self.markdown_fingerprint!r}，实际 {expected!r}"
                    "（正文与身份不得脱钩）")


@dataclass(frozen=True)
class SectionReworkRun:
    """一次定向返工批次（任务书 §13.3；本阶段恒 batch_no=0，至多一批）。

    Evaluation 是 SectionResult 的关联对象：本表记录「父结果 → 新结果」的返工事实，
    不修改、不回写父 SectionResult 的不可变内容身份。
    """

    rework_run_id: str
    job_id: str
    section_result_id: str          # 返工后新 SectionResult（目标结果的 section_result_id）
    from_section_result_id: str     # 被评估的父 SectionResult
    evaluation_id: str              # 触发返工的那次评估
    batch_no: int
    llm_evaluator_calls: int        # 本 cycle 累计 LLM Evaluator 调用次数（∈ {0,1}）
    targets: tuple[ReworkTarget, ...] = ()
    created_at: str = ""


@dataclass(frozen=True)
class SectionRunManifest:
    """一次运行的环境冻结清单（任务书 §14.3）。

    manifest 历史不可变（append-only）；current_manifest 是独立可切换指针，不把
    append-only 历史表本身当指针。frozen 冻结 contract/prompt/renderer/evaluator
    rules/Harness/Evidence inventory/FinancialSnapshot/external policy/model/budget。
    """

    manifest_id: str
    job_id: str
    run_id: str
    code_fingerprint: str
    phase3_closure_fingerprint: str
    batch_versions: dict = field(default_factory=dict)
    frozen: dict = field(default_factory=dict)
    created_at: str = ""


@dataclass(frozen=True)
class EvaluationCommitResult:
    """commit_evaluation 的返回（不可变）。"""

    evaluation_id: str
    reused: bool
    rework_target_count: int


@dataclass(frozen=True)
class ReworkRunCommitResult:
    """commit_rework_run 的返回（不可变）。"""

    rework_run_id: str
    reused: bool


@dataclass(frozen=True)
class ManifestCommitResult:
    """commit_manifest 的返回（不可变）。"""

    manifest_id: str
    reused: bool
    current_switched: bool


# ---------------------------------------------------------------------------
# 序列化（Store 落盘用；显式 tuple/list 边界转换，asdict 仅用于扁平 CitationRef）
# ---------------------------------------------------------------------------

def citation_to_dict(ref: CitationRef) -> dict:
    """CitationRef（扁平 dataclass）→ dict（asdict 全字段，None 保留）。"""
    return asdict(ref)


def citation_from_dict(d: dict) -> CitationRef:
    """dict → CitationRef（字段全可选，缺省 None）。"""
    return CitationRef(**{k: v for k, v in d.items()
                          if k in CitationRef.__dataclass_fields__})


def claim_to_dict(c: SectionClaim) -> dict:
    """current `claim-2` writer：**只**写出 current `SectionClaim`。

    P7 / §16.10 #43：legacy `LegacySectionClaimV1` 不得经此处宽松序列化 —— 它没有
    `schema_version` / candidate revision / accepted binding 字段，宽松读会把它
    静默标成 current wire。legacy 对象只能用自己的 `to_dict()`。
    """
    if not isinstance(c, SectionClaim):
        raise SectionSchemaError(
            "current claim-2 writer 只写出 current SectionClaim；"
            f"legacy/其他对象必须用自己的 to_dict()（实际 {type(c).__name__}）")
    return {
        "schema_version": c.schema_version,
        "claim_id": c.claim_id,
        "section_id": c.section_id,
        "topic_id": c.topic_id,
        "question_ids": list(c.question_ids),
        "text": c.text,
        "claim_type": c.claim_type,
        "citation_refs": [citation_to_dict(r) for r in c.citation_refs],
        "claim_candidate_id": c.claim_candidate_id,
        "claim_candidate_revision": c.claim_candidate_revision,
        "accepted_binding_ids": list(c.accepted_binding_ids),
        "derived_from_claim_ids": list(c.derived_from_claim_ids),
        "confidence": c.confidence,
        "as_of_date": c.as_of_date,
        "impact_scope": list(c.impact_scope),
    }


def claim_from_dict(d: dict) -> SectionClaim:
    """current `claim-2` reader：**拒绝** `claim-1` / 缺 marker / 缺 candidate-binding 的载荷。

    不得以「旧 reader 默认宽松读取」充当合法 current readback；历史对象必须走
    `load_legacy_claim_claim1_for_audit`。
    """
    if not isinstance(d, dict):
        raise SectionSchemaError("claim payload 必须是对象")
    marker = d.get("schema_version")
    if marker != CLAIM_SCHEMA_VERSION:
        raise SectionSchemaError(
            f"current claim-2 reader 拒绝 schema_version={marker!r} 的载荷："
            f"只有 {CLAIM_SCHEMA_VERSION!r} 是 current wire；claim-1/缺 marker 只经 "
            "load_legacy_claim_claim1_for_audit 只读回放")
    for required in ("claim_candidate_id", "claim_candidate_revision", "accepted_binding_ids"):
        if required not in d:
            raise SectionSchemaError(
                f"claim-2 载荷缺 {required!r}：不得静默当作 current Claim 读出")
    return SectionClaim(
        claim_id=d["claim_id"],
        schema_version=marker,
        section_id=d["section_id"],
        topic_id=d["topic_id"],
        question_ids=tuple(d.get("question_ids") or []),
        text=d.get("text") or "",
        claim_type=d["claim_type"],
        citation_refs=tuple(citation_from_dict(r)
                            for r in (d.get("citation_refs") or [])),
        claim_candidate_id=d["claim_candidate_id"],
        claim_candidate_revision=d["claim_candidate_revision"],
        accepted_binding_ids=tuple(d.get("accepted_binding_ids") or ()),
        derived_from_claim_ids=tuple(d.get("derived_from_claim_ids") or []),
        confidence=d.get("confidence") or "low",
        as_of_date=d.get("as_of_date"),
        impact_scope=tuple(d.get("impact_scope") or []),
    )


def unresolved_to_dict(u: SectionUnresolved) -> dict:
    return {
        "unresolved_id": u.unresolved_id,
        "section_id": u.section_id,
        "topic_id": u.topic_id,
        "question_id": u.question_id,
        "state": u.state,
        "reason_code": u.reason_code,
        "detail": u.detail,
        "impact_scope": list(u.impact_scope),
        "blocking_effects": list(u.blocking_effects),
        "attempted_sources": list(u.attempted_sources),
    }


def unresolved_from_dict(d: dict) -> SectionUnresolved:
    return SectionUnresolved(
        unresolved_id=d["unresolved_id"],
        section_id=d["section_id"],
        topic_id=d["topic_id"],
        question_id=d.get("question_id"),
        state=d["state"],
        reason_code=d["reason_code"],
        detail=d.get("detail") or "",
        impact_scope=tuple(d.get("impact_scope") or []),
        blocking_effects=tuple(d.get("blocking_effects") or []),
        attempted_sources=tuple(d.get("attempted_sources") or []),
    )


def issue_to_dict(i: SectionIssue) -> dict:
    return {
        "issue_id": i.issue_id,
        "rule_id": i.rule_id,
        "severity": i.severity,
        "location": i.location,
        "detail": i.detail,
        "suggested_action": i.suggested_action,
    }


def issue_from_dict(d: dict) -> SectionIssue:
    return SectionIssue(
        issue_id=d["issue_id"],
        rule_id=d["rule_id"],
        severity=d["severity"],
        location=d["location"],
        detail=d.get("detail") or "",
        suggested_action=d.get("suggested_action") or "",
    )


def rework_target_to_dict(t: ReworkTarget) -> dict:
    return {"target_id": t.target_id, "target_kind": t.target_kind,
            "target_ref": t.target_ref, "reason": t.reason}


def rework_target_from_dict(d: dict) -> ReworkTarget:
    return ReworkTarget(target_id=d["target_id"], target_kind=d["target_kind"],
                        target_ref=d["target_ref"], reason=d.get("reason") or "")


def evaluation_to_dict(e: SectionEvaluation) -> dict:
    return {
        "evaluation_id": e.evaluation_id,
        "section_result_id": e.section_result_id,
        "rules_version": e.rules_version,
        "evaluator_prompt_version": e.evaluator_prompt_version,
        "rules_passed": e.rules_passed,
        "llm_passed": e.llm_passed,
        "decision": e.decision,
        "issues": [issue_to_dict(i) for i in e.issues],
        "rework_targets": [rework_target_to_dict(t) for t in e.rework_targets],
        "evaluated_at": e.evaluated_at,
        "llm_evaluator_calls": e.llm_evaluator_calls,
    }


def evaluation_from_dict(d: dict) -> SectionEvaluation:
    return SectionEvaluation(
        evaluation_id=d["evaluation_id"],
        section_result_id=d["section_result_id"],
        rules_version=d["rules_version"],
        evaluator_prompt_version=d["evaluator_prompt_version"],
        rules_passed=bool(d["rules_passed"]),
        llm_passed=d.get("llm_passed"),
        decision=d["decision"],
        issues=tuple(issue_from_dict(i) for i in (d.get("issues") or [])),
        rework_targets=tuple(rework_target_from_dict(t)
                             for t in (d.get("rework_targets") or [])),
        evaluated_at=d.get("evaluated_at") or "",
        llm_evaluator_calls=int(d.get("llm_evaluator_calls") or 0),
    )


def rework_run_to_dict(r: SectionReworkRun) -> dict:
    return {
        "rework_run_id": r.rework_run_id,
        "job_id": r.job_id,
        "section_result_id": r.section_result_id,
        "from_section_result_id": r.from_section_result_id,
        "evaluation_id": r.evaluation_id,
        "batch_no": r.batch_no,
        "llm_evaluator_calls": r.llm_evaluator_calls,
        "targets": [rework_target_to_dict(t) for t in r.targets],
        "created_at": r.created_at,
    }


def rework_run_from_dict(d: dict) -> SectionReworkRun:
    return SectionReworkRun(
        rework_run_id=d["rework_run_id"],
        job_id=d["job_id"],
        section_result_id=d["section_result_id"],
        from_section_result_id=d["from_section_result_id"],
        evaluation_id=d["evaluation_id"],
        batch_no=int(d["batch_no"]),
        llm_evaluator_calls=int(d.get("llm_evaluator_calls") or 0),
        targets=tuple(rework_target_from_dict(t) for t in (d.get("targets") or [])),
        created_at=d.get("created_at") or "",
    )


def manifest_to_dict(m: SectionRunManifest) -> dict:
    return {
        "manifest_id": m.manifest_id,
        "job_id": m.job_id,
        "run_id": m.run_id,
        "code_fingerprint": m.code_fingerprint,
        "phase3_closure_fingerprint": m.phase3_closure_fingerprint,
        "batch_versions": dict(m.batch_versions),
        "frozen": dict(m.frozen),
        "created_at": m.created_at,
    }


def manifest_from_dict(d: dict) -> SectionRunManifest:
    return SectionRunManifest(
        manifest_id=d["manifest_id"],
        job_id=d["job_id"],
        run_id=d["run_id"],
        code_fingerprint=d["code_fingerprint"],
        phase3_closure_fingerprint=d["phase3_closure_fingerprint"],
        batch_versions=dict(d.get("batch_versions") or {}),
        frozen=dict(d.get("frozen") or {}),
        created_at=d.get("created_at") or "",
    )


def section_result_to_dict(r: SectionResult) -> dict:
    """current `section-result-2` writer：**只**写出 current `SectionResult`。

    P7 / §16.10 #43：legacy `LegacySectionResultV1` 不得经此处宽松序列化（否则等于
    把 legacy Result 冒充 current result-2 写出）；它只能用自己的 `to_dict()`。
    """
    if not isinstance(r, SectionResult):
        raise SectionSchemaError(
            "current section-result-2 writer 只写出 current SectionResult；"
            f"legacy/其他对象必须用自己的 to_dict()（实际 {type(r).__name__}）")
    return {
        "schema_version": r.schema_version,
        "section_result_id": r.section_result_id,
        "section_version": r.section_version,
        "section_draft_id": r.section_draft_id,
        "task_id": r.task_id,
        "section_id": r.section_id,
        "status": r.status,
        "claims": [claim_to_dict(c) for c in r.claims],
        "unresolved": [unresolved_to_dict(u) for u in r.unresolved],
        "markdown": r.markdown,
        "markdown_fingerprint": r.markdown_fingerprint,
        "source_run_ids": list(r.source_run_ids),
        "source_question_ids": list(r.source_question_ids),
        "dependency_fingerprint": r.dependency_fingerprint,
        "created_at": r.created_at,
    }


def section_result_from_dict(d: dict) -> SectionResult:
    """current `section-result-2` reader：**拒绝**旧形状 Result。

    拒绝条件（任一即拒）：缺 `schema_version` marker、marker 非 `section-result-2`、
    缺 `section_draft_id`、内嵌 `evaluation`。旧 Result 只经
    `load_legacy_section_result_for_audit` 只读回放。
    """
    if not isinstance(d, dict):
        raise SectionSchemaError("SectionResult payload 必须是对象")
    marker = d.get("schema_version")
    if marker != SECTION_RESULT_SCHEMA_VERSION:
        raise SectionSchemaError(
            f"current section-result-2 reader 拒绝 schema_version={marker!r} 的载荷："
            "旧形状 Result（无 marker）只经 load_legacy_section_result_for_audit 只读回放")
    if "evaluation" in d:
        raise SectionSchemaError(
            "current Result 禁止内嵌 evaluation（旧 Result 把回指自身的 SectionEvaluation "
            "装进 Result，使 post-gate 身份成环）；绑定由 NarrativeEvaluationBinding 承载")
    if not d.get("section_draft_id"):
        raise SectionSchemaError(
            "current Result 缺 section_draft_id（Result→Draft 单向引用必须存在）")
    return SectionResult(
        section_result_id=d["section_result_id"],
        schema_version=marker,
        section_version=d["section_version"],
        section_draft_id=d["section_draft_id"],
        task_id=d["task_id"],
        section_id=d["section_id"],
        status=d["status"],
        claims=tuple(claim_from_dict(c) for c in (d.get("claims") or [])),
        unresolved=tuple(unresolved_from_dict(u) for u in (d.get("unresolved") or [])),
        markdown=d.get("markdown") or "",
        markdown_fingerprint=d.get("markdown_fingerprint") or "",
        source_run_ids=tuple(d.get("source_run_ids") or []),
        source_question_ids=tuple(d.get("source_question_ids") or []),
        dependency_fingerprint=d.get("dependency_fingerprint") or "",
        created_at=d.get("created_at") or "",
    )


# ---------------------------------------------------------------------------
# legacy 只读回放（claim-1 / 旧 Result 形状）—— **不得**冒充 current
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LegacySectionClaimV1:
    """`claim-1` 形状的 Claim（无 marker、无 candidate revision、无 accepted binding）。

    只供 legacy producer（P25–P32）与历史载荷只读回放使用。它的类型与 current
    `SectionClaim` **不同**，因此无法进入 current validator / store / assembler 的
    current 路径。
    """

    claim_id: str
    section_id: str
    topic_id: str
    question_ids: tuple[str, ...]
    text: str
    claim_type: str
    citation_refs: tuple[CitationRef, ...]
    derived_from_claim_ids: tuple[str, ...] = ()
    confidence: str = "low"
    as_of_date: str | None = None
    impact_scope: tuple[str, ...] = ()

    LEGACY_SCHEMA_VERSION = "claim-1"

    def to_dict(self) -> dict:
        return {
            "schema_version": self.LEGACY_SCHEMA_VERSION,
            "claim_id": self.claim_id,
            "section_id": self.section_id,
            "topic_id": self.topic_id,
            "question_ids": list(self.question_ids),
            "text": self.text,
            "claim_type": self.claim_type,
            "citation_refs": [citation_to_dict(r) for r in self.citation_refs],
            "derived_from_claim_ids": list(self.derived_from_claim_ids),
            "confidence": self.confidence,
            "as_of_date": self.as_of_date,
            "impact_scope": list(self.impact_scope),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LegacySectionClaimV1":
        return cls(
            claim_id=d["claim_id"],
            section_id=d["section_id"],
            topic_id=d["topic_id"],
            question_ids=tuple(d.get("question_ids") or []),
            text=d.get("text") or "",
            claim_type=d["claim_type"],
            citation_refs=tuple(citation_from_dict(r)
                                for r in (d.get("citation_refs") or [])),
            derived_from_claim_ids=tuple(d.get("derived_from_claim_ids") or []),
            confidence=d.get("confidence") or "low",
            as_of_date=d.get("as_of_date"),
            impact_scope=tuple(d.get("impact_scope") or []),
        )


def load_legacy_claim_claim1_for_audit(payload: dict) -> LegacySectionClaimV1:
    """把 `claim-1` 载荷**只读**还原为 legacy 视图；missing/其它 marker 一律拒。"""
    if not isinstance(payload, dict):
        raise SectionSchemaError("legacy claim 载荷必须是对象")
    marker = payload.get("schema_version", LegacySectionClaimV1.LEGACY_SCHEMA_VERSION)
    if marker not in LEGACY_CLAIM_SCHEMA_VERSIONS:
        raise SectionSchemaError(
            f"不是登记的 claim-1 legacy 载荷：schema_version={marker!r}"
            "（不得以 legacy reader 读取 current 载荷）")
    return LegacySectionClaimV1.from_dict(payload)


@dataclass(frozen=True)
class LegacySectionResultV1:
    """旧形状 `SectionResult`（无 marker、内嵌 `SectionEvaluation`、无 `section_draft_id`）。

    只供 legacy producer 与历史载荷只读回放使用；类型与 current `SectionResult` 不同，
    因此不可能被 current 链当作 current Result 消费。
    """

    section_result_id: str
    section_version: str
    task_id: str
    section_id: str
    status: str
    claims: tuple[LegacySectionClaimV1, ...] = ()
    unresolved: tuple[SectionUnresolved, ...] = ()
    markdown: str = ""
    markdown_fingerprint: str = ""
    evaluation: SectionEvaluation | None = None
    source_run_ids: tuple[str, ...] = ()
    source_question_ids: tuple[str, ...] = ()
    dependency_fingerprint: str = ""
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "section_result_id": self.section_result_id,
            "section_version": self.section_version,
            "task_id": self.task_id,
            "section_id": self.section_id,
            "status": self.status,
            "claims": [c.to_dict() for c in self.claims],
            "unresolved": [unresolved_to_dict(u) for u in self.unresolved],
            "markdown": self.markdown,
            "markdown_fingerprint": self.markdown_fingerprint,
            "evaluation": (evaluation_to_dict(self.evaluation)
                           if self.evaluation is not None else None),
            "source_run_ids": list(self.source_run_ids),
            "source_question_ids": list(self.source_question_ids),
            "dependency_fingerprint": self.dependency_fingerprint,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LegacySectionResultV1":
        ev = d.get("evaluation")
        return cls(
            section_result_id=d["section_result_id"],
            section_version=d["section_version"],
            task_id=d["task_id"],
            section_id=d["section_id"],
            status=d["status"],
            claims=tuple(LegacySectionClaimV1.from_dict(c)
                         for c in (d.get("claims") or [])),
            unresolved=tuple(unresolved_from_dict(u) for u in (d.get("unresolved") or [])),
            markdown=d.get("markdown") or "",
            markdown_fingerprint=d.get("markdown_fingerprint") or "",
            evaluation=evaluation_from_dict(ev) if ev is not None else None,
            source_run_ids=tuple(d.get("source_run_ids") or []),
            source_question_ids=tuple(d.get("source_question_ids") or []),
            dependency_fingerprint=d.get("dependency_fingerprint") or "",
            created_at=d.get("created_at") or "",
        )


def load_legacy_section_result_for_audit(payload: dict) -> LegacySectionResultV1:
    """把旧形状 Result 载荷**只读**还原为 legacy 视图。

    fail-closed：`schema_version` 若存在且不是登记的 legacy 值（或 current marker）→ 拒；
    当前 `section-result-2` 载荷**不得**经本函数读回（那会把 current 冒充 legacy）。
    """
    if not isinstance(payload, dict):
        raise SectionSchemaError("legacy SectionResult 载荷必须是对象")
    marker = payload.get("schema_version", "")
    if marker not in LEGACY_SECTION_RESULT_SCHEMA_VERSIONS:
        raise SectionSchemaError(
            f"不是登记的 legacy SectionResult 载荷：schema_version={marker!r}"
            "（current section-result-2 载荷不得经 legacy reader 读回）")
    return LegacySectionResultV1.from_dict(payload)

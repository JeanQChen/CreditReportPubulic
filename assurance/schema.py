# -*- coding: utf-8 -*-
"""M930-4 独立审查与 Assurance 的线格式（`DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` §7.3）。

本模块只做一件事：把 **Review/Assurance 身份集**（`DESIGN_V2.md` §0.13 第 4.2 条、
§11.2）的对象定义成内容寻址、版本化、拒绝 unknown 字段的冻结值对象。

三条必须在这里被结构性守住、而不是靠调用方自觉的规则：

1. **第三套 identity**。本模块的每个 id 都由自身内容派生；`report_version` 是唯一外部
   身份锚。本包的对象**不写入 Pack**，也不进入 Pack content identity。
2. `ReviewIssue.category` 是**上位设计的封闭词表** `supported / contradicted /
   insufficient / missing_content`。`unsupported` 与 `clarity` 不是合法值，而且拒绝
   时必须给出可执行的替代指引（证据不足 → `insufficient`；已有反证 → `contradicted`；
   应有内容缺失 → `missing_content`；纯表达质量不归本 wire）。`supported` 是对**被审核
   单元**的正向结构化结果，不是 Reviewer 的 PASS/发布决定，也不能覆盖 hard gate。
3. **Reviewer 的越权输出在这里被结构性拒绝**，而不是等调用方检查：`decision` /
   `verdict` / `pass` / `ready` / `publish` 与任何正文改写字段（`rewritten_text` 等）
   在 `ReviewIssue` 与 Reviewer 响应信封里都是**未登记字段**，一律抛错。

另有一条确定性约定贯穿本模块：**所有 id/键元组在构造期即被要求「有序且唯一」**，对象
列表按各自 id 排序。聚合结果因此不依赖输入顺序，也不需要调用方额外排序——这是 §7.6
「deterministic aggregate」的线级保证，而不是某个函数的自觉。

本模块**不**注册进 `document_structure/versions.py`：那是 TS4/TS5 的版本双射轴，
`sections/` 的 schema 版本常量同样是本包自持（见 `sections/backbone_schema.py`）。

## §0.20 的句级扩展（`rvi-2` / `rib-2`）——**版本化扩展，不是新 wire，也不是运行时**

`DESIGN_V2.md` §0.20 的写作主线把审阅面从「旧 Draft/Claim 单元」搬到「**逐句**带引用
的自然正文」。旧对象缺两样东西：句级定位（哪一句、引的是哪条）与**句义级**的语义类别。
本模块因此对**同一个** `ReviewIssue` 做版本化扩展，而不是另立第二套审阅 wire：

* `REVIEW_CATEGORIES`（§7.3 的四值粗类）**一个字节都不改**——它是冻结词表，且
  `unsupported`/`clarity` 等写法仍按原样被拒。新增的是**另一根轴** `semantic_category`：
  §0.20 点名的九类句义问题（不支持、夸大/越界、因果误写、局部推整体、选择性取材、矛盾、
  旧材料当前化、重要限制遗漏、跑题）。两根轴不是同义反复：粗类决定「这条问题算哪一类
  处置」，句义类决定「这句话到底哪里错了」，二者的对应关系在
  `REVIEW_CATEGORY_BY_SEMANTIC` 里**一一写死**，因此审阅方无法给出「粗类是 contradicted、
  句义类却是不支持」这种自相矛盾的结果。
* 句级定位是 `sentence_id` + `citation_id` 两个字段，`rvi-2` 上**必填**；`rvi-1` 上
  **不得出现**（旧 payload 里多出一个键即按未登记字段拒绝）。
* **旧身份逐字节保持**：`create()` 默认仍发射 `rvi-1`，`identity_body()` 按版本分支，
  `rvi-1` 的身份体与新字段无关。历史产物引用的 `rvi_*` id 因此不会因为本扩展而改变。
* `ReviewInputBundle` 同法扩到 `rib-2`，新增 `sentence_inventory`（本条 bundle 覆盖的
  句子 id 集）。

**本包仍然只有 schema。** 上面每一条都只是「对象长什么样、什么组合不被接受」。谁去调用
模型、谁把意见聚合起来、谁决定放行，本模块一概不实现：`assurance/` 里没有 Reviewer 运行时，
也没有 Controller。把这里写成「审阅已经跑起来了」是错的——真实审阅的输入装配与响应解析在
`sections/cited_review.py`，它同样不发起调用。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

# ---------------------------------------------------------------------------
# 版本常量（本包自持，见模块 docstring 末段）
# ---------------------------------------------------------------------------

ASSURANCE_SCHEMA_VERSION = "asu-2"
"""本包线格式的伞版本。任一对象形状变化都要推进它（§0.20 句级扩展 ⇒ `asu-2`）。"""

REVIEW_ISSUE_SCHEMA_VERSION = "rvi-2"
"""`ReviewIssue` **当前**版本：§0.20 句级扩展后由 `create()` 发射的版本。"""
REVIEW_ISSUE_SCHEMA_VERSION_V1 = "rvi-1"
"""历史版本。`create()` 的**默认**仍是它：旧调用点的身份 id 因此逐字节不变。"""
REVIEW_ISSUE_SCHEMA_VERSIONS = (REVIEW_ISSUE_SCHEMA_VERSION_V1, REVIEW_ISSUE_SCHEMA_VERSION)
"""可解码的版本集（只读兼容）。集合之外的版本一律 fail-closed。"""

REVIEW_INPUT_BUNDLE_SCHEMA_VERSION = "rib-2"
REVIEW_INPUT_BUNDLE_SCHEMA_VERSION_V1 = "rib-1"
REVIEW_INPUT_BUNDLE_SCHEMA_VERSIONS = (REVIEW_INPUT_BUNDLE_SCHEMA_VERSION_V1,
                                       REVIEW_INPUT_BUNDLE_SCHEMA_VERSION)
HARD_GATE_RESULT_SCHEMA_VERSION = "hgr-1"
REVIEWER_RUN_RECORD_SCHEMA_VERSION = "rrr-1"
ASSURANCE_RESULT_SCHEMA_VERSION = "asr-1"

HARD_GATE_RULES_VERSION = "ahg-1"
"""确定性硬门规则集版本（§7.4 十一项）。规则变化 → 全部旧 HardGateResult 失效。"""

ASSURANCE_AGGREGATE_RULES_VERSION = "aag-1"
"""Controller 聚合规则版本（§7.6）。变化 → 全部旧 AssuranceResult 失效。"""

INDEPENDENT_REVIEWER_PROMPT_NAME = "independent_report_reviewer_v1"
INDEPENDENT_REVIEWER_PROMPT_VERSION = "independent_report_reviewer_v1@irv-2"
"""`irv-2`（2026-10-03）：报告级审阅的输出契约从「逐单元表态」改为「只报发现」，
并修正了正文里非法的 `out_of_scope` 指示、收紧了 `blocking` 与 `evidence_refs`。
`irv-1` 的产物只读保留，不回填——版本号变了就必须换新版本身份。"""

# ---------------------------------------------------------------------------
# 封闭词表
# ---------------------------------------------------------------------------

REVIEW_UNIT_KINDS = ("section", "paragraph", "table", "row", "claim", "citation", "locator")
"""`DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` §7.3 列举的审核单元轴。"""

REVIEW_CATEGORIES = ("supported", "contradicted", "insufficient", "missing_content")
"""上位设计的精确封闭词表。顺序即优先级，供 Controller 确定性排序使用。"""

REVIEW_CATEGORY_REPLACEMENTS = {
    "unsupported": ("证据不足用 'insufficient'；已有反证用 'contradicted'；"
                    "应有内容缺失用 'missing_content'"),
    "clarity": "纯表达质量由 Section Evaluator 负责，不属于本 wire",
    "not_supported": "证据不足用 'insufficient'；已有反证用 'contradicted'",
    "unclear": "证据不足用 'insufficient'；应有内容缺失用 'missing_content'",
    "missing": "应有内容缺失用 'missing_content'",
    "contradiction": "已有反证用 'contradicted'",
}
"""被拒绝的常见同义写法与其**可执行**替代（§7.3 明文要求 `unsupported`/`clarity` 非法）。"""

REVIEW_SEMANTIC_CATEGORIES = (
    #: 这句话说的东西，被引来源并不支持（含「来源里根本没这句意思」）。
    "not_supported_by_source",
    #: 夸大或越界：把小范围说成大范围、把局部说成整体之外的强度。
    "exaggeration_or_overreach",
    #: 因果误写：把相关、并列或时序写成了因果。
    "causality_misstatement",
    #: 局部推整体：以部分样本/部分期间/部分业务推出全称结论。
    "part_to_whole_generalization",
    #: 选择性取材：只取有利片段，隐去同源中相反或限定的部分。
    "selective_sourcing",
    #: 与本句自己的引用、同节他句或已知权威事实相互矛盾。
    "contradiction",
    #: 旧材料当前化：以历史期来源叙述当前状态。
    "stale_material_as_current",
    #: 重要限制遗漏：省略了来源里对结论有实质约束的限定。
    "omitted_limitation",
    #: 跑题：与本小节的 Contract 要求无关。
    "off_topic",
)
"""§0.20 点名的**句义级**问题类别（用户裁决文本：「不支持、夸大/越界、因果误写、局部推整体、
选择性取材、矛盾、旧材料当前化和重要限制遗漏等」，另加本轮任务书点名的「跑题」）。

它与 `REVIEW_CATEGORIES` 是**两根正交轴**，不是同一词表的两种写法：粗类（四值）决定处置
口径，句义类（九值）说明这句话到底哪里错。`REVIEW_CATEGORY_REPLACEMENTS` 里
`not_supported` 仍被拒——那个别名指向的是**粗类**词表，不是这里的 `not_supported_by_source`。
"""

REVIEW_CATEGORY_BY_SEMANTIC = {
    "not_supported_by_source": "insufficient",
    "exaggeration_or_overreach": "contradicted",
    "causality_misstatement": "contradicted",
    "part_to_whole_generalization": "contradicted",
    "selective_sourcing": "insufficient",
    "contradiction": "contradicted",
    "stale_material_as_current": "contradicted",
    "omitted_limitation": "missing_content",
    "off_topic": "missing_content",
}
"""句义类 → 粗类的一一对应。`ReviewIssue` 在构造期校验二者一致，因此不存在
「粗类说 contradicted、句义类说 not_supported」这种无法回查的组合。"""

REVIEW_SEVERITIES = ("none", "low", "medium", "high", "critical")

SUGGESTED_TARGET_KINDS = REVIEW_UNIT_KINDS + ("contract_aspect",)
"""`contract_aspect` 是给 `missing_content` 用的：指向「应该补哪个 Contract aspect」，
而不是指向一段要改写的正文。"""

HARD_GATE_KINDS = (
    "report_version_and_dependencies",
    "demo_scope_contract_boundary",
    "pack_fact_claim_retention",
    "citation_authority_locator",
    "evidence_current",
    "financial_period_unit_currency_entity_scope",
    "required_gap_retention",
    "ts5_refused_material_exclusion",
    "cross_section_exact_conflict",
    "writer_prompt_model_version",
    "artifact_index_integrity",
)
"""§7.4 十一项确定性硬门，逐项对应一条 gate_kind。"""

REVIEWER_OUTCOMES = ("reviewed", "skipped_reviewability_blocked", "failed")

PROCESS_STATES = ("flow_incomplete", "flow_complete")
PREVIEW_STATES = ("preview_unavailable", "draft_previewable")
SYSTEM_REVIEW_STATES = ("system_review_not_run", "system_review_not_passed",
                        "system_review_passed_awaiting_human")
HUMAN_REVIEW_STATES = ("human_not_reviewed", "human_reviewed")
"""人工接受始终独立于系统 Assurance：Controller 只把它**原样带过**，不产生、不修改。
最高自动状态止于 `system_review_passed_awaiting_human`（`DESIGN_V2.md` §11.3）。"""

REQUIRED_EXCLUDED_CONTEXT = (
    "writer_hidden_history",
    "writer_prompt",
    "writer_self_evaluation",
    "assurance_expected_verdict",
    "human_verdict",
)
"""§7.5：Reviewer 上下文**必须**声明并核验已排除这些来源。bundle 少声明任何一个即拒绝。"""

REVIEWER_FORBIDDEN_FIELDS = (
    "decision",
    "verdict",
    "pass",
    "passed",
    "fail",
    "failed",
    "ready",
    "publish",
    "publishable",
    "release",
    "approved",
    "rewrite",
    "rewritten_text",
    "revised_text",
    "suggested_text",
    "new_text",
    "final_text",
)
"""§7.7：Reviewer 输出这些字段即被拒绝（它只找问题，不放行、不改正文）。"""

ASSURANCE_FORBIDDEN_FIELDS = (
    "formal_closure",
    "closure_state",
    "stage_closed",
    "sealed",
    "human_acceptance_override",
    "human_acceptance",
    "human_verdict",
    "writer_decision",
    "run_id",
)
"""§7.7：Controller 不能产生这些。它们只出现在治理层，不进 `AssuranceResult`。

`run_id` 一并禁止：`report_version` 才是本身份集的外部锚，run 归属改动不得使
Assurance 结论看起来仍然有效。
"""

MAX_REASON_CHARS = 600

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AssuranceSchemaError(ValueError):
    """Review/Assurance 线格式违规。fail-closed：一律不猜、不补默认值。"""


# ---------------------------------------------------------------------------
# 确定性工具（与 `sections/narrative_schema.py` 同构，故本包零内部依赖）
# ---------------------------------------------------------------------------

def canonical_json(obj: Any) -> str:
    """稳定 JSON 序列化（排序键 + 紧凑分隔符 + 转义非 ASCII），供内容寻址使用。"""
    return json.dumps(obj, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def content_id(prefix: str, body: Any) -> str:
    """内容寻址 id：同内容必得同 id，改一个字节必得新 id。"""
    digest = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    return prefix + digest[:24]


def sha256_text(text: str) -> str:
    """文本指纹（hex）。用于 `allowed_content_fingerprint` / `response_fingerprint`。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.match(value))


def _require_nonempty(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise AssuranceSchemaError(f"{what} 必须是字符串，得到 {type(value).__name__}")
    if value == "":
        raise AssuranceSchemaError(f"{what} 不得为空字符串")
    return value


def _require_bool(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise AssuranceSchemaError(f"{what} 必须是布尔值，得到 {value!r}")
    return value


def _require_int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssuranceSchemaError(f"{what} 必须是整数，得到 {value!r}")
    if value < 0:
        raise AssuranceSchemaError(f"{what} 不得为负，得到 {value!r}")
    return value


def _require_one_of(value: Any, allowed: Iterable[str], what: str) -> str:
    allowed = tuple(allowed)
    if value not in allowed:
        raise AssuranceSchemaError(f"{what} 必须属于 {allowed}，得到 {value!r}")
    return value


def _reject_forbidden(d: Any, forbidden: Iterable[str], what: str) -> dict:
    """先于 `_reject_unknown` 运行：命中禁列时给出**指名**的拒绝理由。"""
    if not isinstance(d, dict):
        raise AssuranceSchemaError(f"{what} 必须是对象")
    hit = sorted(set(d) & set(forbidden))
    if hit:
        raise AssuranceSchemaError(
            f"{what} 含被禁止字段 {hit}：Reviewer 只输出结构化 ReviewIssue，"
            f"不放行、不判定、不改写正文（§7.5/§7.7）")
    return d


def _reject_unknown(d: Any, allowed: Iterable[str], what: str) -> dict:
    if not isinstance(d, dict):
        raise AssuranceSchemaError(f"{what} 必须是对象")
    unknown = sorted(set(d) - set(allowed))
    if unknown:
        raise AssuranceSchemaError(f"{what} 含未登记字段 {unknown}")
    return d


def _req(d: Mapping[str, Any], key: str, what: str, *, allow_none: bool = False) -> Any:
    if key not in d:
        raise AssuranceSchemaError(f"{what} 缺字段 {key!r}")
    value = d[key]
    if value is None and not allow_none:
        raise AssuranceSchemaError(f"{what}.{key} 不得为 null")
    return value


def _sorted_unique(values: Any, what: str) -> tuple[str, ...]:
    """非空字符串元组，且**有序唯一**。聚合的确定性在这里被强制，而不是靠调用方排序。"""
    if not isinstance(values, (list, tuple)):
        raise AssuranceSchemaError(f"{what} 必须是字符串列表")
    out: list[str] = []
    for item in values:
        out.append(_require_nonempty(item, f"{what} 元素"))
    if len(set(out)) != len(out):
        dup = sorted({x for x in out if out.count(x) > 1})
        raise AssuranceSchemaError(f"{what} 含重复元素 {dup}")
    if out != sorted(out):
        raise AssuranceSchemaError(f"{what} 必须有序（升序），得到 {out}")
    return tuple(out)


def _maybe_none(value: Any, build) -> Any:
    return None if value is None else build(value)


def _normalized(values: Any, what: str) -> tuple[str, ...]:
    """**构造器**用的归一：先排序再按 `_sorted_unique` 校验。

    分工是刻意的——`create` 负责把调用方的无序输入归一成规范形状，`__post_init__` 与
    `from_dict` 负责**严格**要求已经有序。于是线格式上「顺序」是有意义且被校验的，而内部
    构造不必逼调用方先排序。
    """
    return _sorted_unique(tuple(sorted(values)), what)


def normalize_review_category(value: Any, what: str) -> str:
    """`category` 的唯一入口：合法值直通，常见同义词给出**可执行**替代后拒绝。

    §7.3 明文：`unsupported`/`clarity` 不是本 wire 合法值。把这条规则放在**构造期**，
    而不是 Reviewer 后处理里，是为了让「静默改写成 insufficient」这种路径根本不存在。
    """
    if value in REVIEW_CATEGORIES:
        return value
    hint = REVIEW_CATEGORY_REPLACEMENTS.get(value)
    if hint:
        raise AssuranceSchemaError(f"{what} 非法取值 {value!r}（本 wire 不接受该写法）：{hint}")
    raise AssuranceSchemaError(f"{what} 必须属于 {REVIEW_CATEGORIES}，得到 {value!r}")


def unit_ref_key(unit_kind: str, unit_id: str) -> str:
    """审核单元在集合运算中的规范键。"""
    return f"{unit_kind}:{unit_id}"


def normalize_semantic_category(value: Any, what: str) -> str:
    """`semantic_category` 的唯一入口：只认 §0.20 的九类，不做同义猜写。"""
    if value in REVIEW_SEMANTIC_CATEGORIES:
        return value
    raise AssuranceSchemaError(
        f"{what} 必须属于 {REVIEW_SEMANTIC_CATEGORIES}，得到 {value!r}")


#: `rvi-2` 相对 `rvi-1` 新增的字段。`rvi-1` payload 里出现其中任何一个即按未登记字段拒绝。
REVIEW_ISSUE_V2_FIELDS = ("sentence_id", "citation_id", "semantic_category")

#: `ReviewIssue` 各版本**允许**的字段集（`from_dict` 按声明的版本选一套）。
_REVIEW_ISSUE_V1_FIELDS = frozenset(
    {"issue_id", "schema_version", "report_version", "unit_ref", "category", "severity",
     "blocking", "reason", "evidence_refs", "suggested_target"})
_REVIEW_ISSUE_V2_FIELDS = frozenset(_REVIEW_ISSUE_V1_FIELDS | set(REVIEW_ISSUE_V2_FIELDS))

#: `rib-2` 相对 `rib-1` 新增的字段。
REVIEW_INPUT_BUNDLE_V2_FIELDS = ("sentence_inventory",)

_REVIEW_INPUT_BUNDLE_V1_FIELDS = frozenset(
    {"bundle_id", "schema_version", "report_version", "report_id", "unit_inventory",
     "excerpts", "claim_ids", "citation_ids", "gap_refs", "conflict_refs", "rubric_refs",
     "allowed_content_fingerprint", "excluded_context", "prompt_version", "model_policy_id"})
_REVIEW_INPUT_BUNDLE_V2_FIELDS = frozenset(
    _REVIEW_INPUT_BUNDLE_V1_FIELDS | set(REVIEW_INPUT_BUNDLE_V2_FIELDS))


# ---------------------------------------------------------------------------
# ReviewUnitRef / SuggestedTarget：被审核单元与建议去向
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReviewUnitRef:
    """一个**被审核单元**（§7.3 的 section/paragraph/table/row/claim/citation/locator）。

    它不是身份对象，没有自己的 id：它就是「指向别处某个已存在身份」的引用。因此
    `from_dict` 只校验形状与词表，不校验被指对象是否存在——**是否存在由硬门与
    Controller 用真实产物核对**，本层没有足够上下文替它猜。
    """

    unit_kind: str
    unit_id: str

    def __post_init__(self) -> None:
        _require_one_of(self.unit_kind, REVIEW_UNIT_KINDS, "ReviewUnitRef.unit_kind")
        _require_nonempty(self.unit_id, "ReviewUnitRef.unit_id")

    @property
    def key(self) -> str:
        return unit_ref_key(self.unit_kind, self.unit_id)

    def to_dict(self) -> dict:
        return {"unit_kind": self.unit_kind, "unit_id": self.unit_id}

    @classmethod
    def create(cls, *, unit_kind: str, unit_id: str) -> "ReviewUnitRef":
        return cls(unit_kind=unit_kind, unit_id=unit_id)

    @classmethod
    def from_dict(cls, d: Any) -> "ReviewUnitRef":
        d = _reject_unknown(d, {"unit_kind", "unit_id"}, "ReviewUnitRef")
        return cls(
            unit_kind=_req(d, "unit_kind", "ReviewUnitRef"),
            unit_id=_require_nonempty(_req(d, "unit_id", "ReviewUnitRef"),
                                      "ReviewUnitRef.unit_id"))


@dataclass(frozen=True)
class SuggestedTarget:
    """`suggested target`——**指向应补/应查的位置，不是要写进正文的文本**（§7.3）。

    这里刻意没有任何承载正文的字段：`target_ref` 只能是身份引用或 aspect 名。
    """

    target_kind: str
    target_ref: str

    def __post_init__(self) -> None:
        _require_one_of(self.target_kind, SUGGESTED_TARGET_KINDS, "SuggestedTarget.target_kind")
        _require_nonempty(self.target_ref, "SuggestedTarget.target_ref")

    def to_dict(self) -> dict:
        return {"target_kind": self.target_kind, "target_ref": self.target_ref}

    @classmethod
    def create(cls, *, target_kind: str, target_ref: str) -> "SuggestedTarget":
        return cls(target_kind=target_kind, target_ref=target_ref)

    @classmethod
    def from_dict(cls, d: Any) -> "SuggestedTarget":
        d = _reject_unknown(d, {"target_kind", "target_ref"}, "SuggestedTarget")
        return cls(
            target_kind=_req(d, "target_kind", "SuggestedTarget"),
            target_ref=_require_nonempty(_req(d, "target_ref", "SuggestedTarget"),
                                         "SuggestedTarget.target_ref"))


# ---------------------------------------------------------------------------
# ReviewIssue
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReviewIssue:
    """Reviewer 对一个**预定义审核单元**的结构化结果（§7.3）。

    `category == "supported"` 是该单元的正向结果，**不是**发布决定：它既不放行报告，
    也覆盖不了 hard gate。因此 `supported` 被强制为「无严重度、不阻断」，非
    `supported` 被强制为「有严重度」——一个既不支持又不给严重度的结果没有意义，
    只能是构造错误。

    真值表（构造期强制）：

    - `category == "supported"` ⇒ `severity == "none"` 且 `blocking is False`
      且 `suggested_target is None`；
    - `category != "supported"` ⇒ `severity != "none"`；
    - `category == "contradicted"` ⇒ `evidence_refs` 非空（反证必须给出处）；
    - `category == "missing_content"` ⇒ `suggested_target` 存在（缺什么要说清去哪补）。

    **§0.20 句级扩展（`rvi-2`）**在同一条真值表上再加三行：

    - `rvi-2` ⇒ `sentence_id` / `citation_id` 非空（审阅面是**具体某一句话**，不是节）；
    - `rvi-2` 且 `category == "supported"` ⇒ `semantic_category == ""`（没有问题就没有句义类，
      不允许给一条正向结果硬安一个「问题类别」）；
    - `rvi-2` 且 `category != "supported"` ⇒ `semantic_category` 属于
      `REVIEW_SEMANTIC_CATEGORIES`，且 `REVIEW_CATEGORY_BY_SEMANTIC[该值] == category`；
    - `rvi-1` ⇒ 三个新字段**必须为空串**（旧 payload 里出现它们会被 `from_dict` 先拒）。
    """

    schema_version: str
    issue_id: str
    report_version: str
    unit_ref: ReviewUnitRef
    category: str
    severity: str
    blocking: bool
    reason: str
    evidence_refs: tuple[str, ...]
    suggested_target: SuggestedTarget | None
    #: `rvi-2`：这句话的身份。`rvi-1` 恒为空串。
    sentence_id: str = ""
    #: `rvi-2`：这句话引到的**具体**那条材料/事实键（不是「看了某个 Pack」）。
    citation_id: str = ""
    #: `rvi-2`：§0.20 的句义类；`category == "supported"` 时为空串。
    semantic_category: str = ""

    def __post_init__(self) -> None:
        if self.schema_version not in REVIEW_ISSUE_SCHEMA_VERSIONS:
            raise AssuranceSchemaError(
                f"ReviewIssue.schema_version 必须属于 {REVIEW_ISSUE_SCHEMA_VERSIONS}，"
                f"得到 {self.schema_version!r}")
        _require_nonempty(self.report_version, "ReviewIssue.report_version")
        if not isinstance(self.unit_ref, ReviewUnitRef):
            raise AssuranceSchemaError("ReviewIssue.unit_ref 必须是 ReviewUnitRef")
        _require_one_of(self.category, REVIEW_CATEGORIES, "ReviewIssue.category")
        _require_one_of(self.severity, REVIEW_SEVERITIES, "ReviewIssue.severity")
        _require_bool(self.blocking, "ReviewIssue.blocking")
        _require_nonempty(self.reason, "ReviewIssue.reason")
        if len(self.reason) > MAX_REASON_CHARS:
            raise AssuranceSchemaError(
                f"ReviewIssue.reason 超过 {MAX_REASON_CHARS} 字符（{len(self.reason)}），"
                f"「简短 evidence-grounded reason」不接受长文")
        _sorted_unique(self.evidence_refs, "ReviewIssue.evidence_refs")
        if self.suggested_target is not None and not isinstance(self.suggested_target,
                                                                SuggestedTarget):
            raise AssuranceSchemaError("ReviewIssue.suggested_target 必须是 SuggestedTarget 或 none")

        if self.category == "supported":
            if self.severity != "none":
                raise AssuranceSchemaError(
                    "ReviewIssue：category='supported' 时 severity 必须为 'none'"
                    f"（supported 是正向结果，不是带伤通过），得到 {self.severity!r}")
            if self.blocking:
                raise AssuranceSchemaError(
                    "ReviewIssue：category='supported' 不得 blocking=True")
            if self.suggested_target is not None:
                raise AssuranceSchemaError(
                    "ReviewIssue：category='supported' 不得带 suggested_target")
        else:
            if self.severity == "none":
                raise AssuranceSchemaError(
                    f"ReviewIssue：category={self.category!r} 必须给出 severity，不得为 'none'")
            if self.category == "contradicted" and not self.evidence_refs:
                raise AssuranceSchemaError(
                    "ReviewIssue：category='contradicted' 必须给出 evidence_refs（反证出处）")
            if self.category == "missing_content" and self.suggested_target is None:
                raise AssuranceSchemaError(
                    "ReviewIssue：category='missing_content' 必须给出 suggested_target")

        self._check_sentence_extension()

        expected = content_id("rvi_", self.identity_body())
        if self.issue_id != expected:
            raise AssuranceSchemaError(
                f"ReviewIssue.issue_id 与内容不符：声明 {self.issue_id!r}，应为 {expected!r}")

    def _check_sentence_extension(self) -> None:
        """`rvi-2` 的三条句级真值 + `rvi-1` 的「新字段必须缺席」。"""
        for name in REVIEW_ISSUE_V2_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, str):
                raise AssuranceSchemaError(
                    f"ReviewIssue.{name} 必须是字符串（缺省为空串），得到 {value!r}")
        filled = [n for n in REVIEW_ISSUE_V2_FIELDS if getattr(self, n) != ""]

        if self.schema_version == REVIEW_ISSUE_SCHEMA_VERSION_V1:
            if filled:
                raise AssuranceSchemaError(
                    f"ReviewIssue：schema_version={REVIEW_ISSUE_SCHEMA_VERSION_V1!r} 上不得出现"
                    f"句级扩展字段 {sorted(filled)}（旧版本要么缺省为空，要么就是伪造的）")
            return

        for name in ("sentence_id", "citation_id"):
            if getattr(self, name) == "":
                raise AssuranceSchemaError(
                    f"ReviewIssue：schema_version={REVIEW_ISSUE_SCHEMA_VERSION!r}（§0.20 句级审阅）"
                    f"必须给出 {name}——审阅对象是**具体某一句话**及其引用，不是整节")
        if self.category == "supported":
            if self.semantic_category != "":
                raise AssuranceSchemaError(
                    "ReviewIssue：category='supported' 时 semantic_category 必须为空串"
                    f"（没有问题就没有问题类别），得到 {self.semantic_category!r}")
            return
        sem = normalize_semantic_category(self.semantic_category,
                                          "ReviewIssue.semantic_category")
        want = REVIEW_CATEGORY_BY_SEMANTIC[sem]
        if want != self.category:
            raise AssuranceSchemaError(
                f"ReviewIssue：semantic_category={sem!r} 对应粗类 {want!r}，"
                f"与声明的 category={self.category!r} 不符（两根轴不得自相矛盾）")

    def identity_body(self) -> dict:
        body = {
            "schema_version": self.schema_version,
            "report_version": self.report_version,
            "unit_ref": self.unit_ref.to_dict(),
            "category": self.category,
            "severity": self.severity,
            "blocking": self.blocking,
            "reason": self.reason,
            "evidence_refs": list(self.evidence_refs),
            "suggested_target": (None if self.suggested_target is None
                                 else self.suggested_target.to_dict()),
        }
        if self.schema_version == REVIEW_ISSUE_SCHEMA_VERSION:
            for name in REVIEW_ISSUE_V2_FIELDS:
                body[name] = getattr(self, name)
        return body

    def to_dict(self) -> dict:
        return {"issue_id": self.issue_id, **self.identity_body()}

    @classmethod
    def create(cls, *, report_version: str, unit_ref: ReviewUnitRef, category: str,
               severity: str, blocking: bool, reason: str,
               evidence_refs: Iterable[str] = (),
               suggested_target: SuggestedTarget | None = None,
               schema_version: str = REVIEW_ISSUE_SCHEMA_VERSION_V1,
               sentence_id: str = "", citation_id: str = "",
               semantic_category: str = "") -> "ReviewIssue":
        """默认发射 `rvi-1`。

        默认值刻意**停在历史版本**：`create()` 在过去只被旧链调用，那些调用的产物 id 已经
        写进了历史 run 与验收报告；把默认提到 `rvi-2` 会让同一份输入产出新 id，等于静默
        改写历史。要走句级审阅，调用方**显式**传 `schema_version="rvi-2"` 与三个新字段。
        """
        cat = normalize_review_category(category, "ReviewIssue.category")
        refs = _normalized(evidence_refs, "ReviewIssue.evidence_refs")
        if schema_version not in REVIEW_ISSUE_SCHEMA_VERSIONS:
            raise AssuranceSchemaError(
                f"ReviewIssue.schema_version 必须属于 {REVIEW_ISSUE_SCHEMA_VERSIONS}，"
                f"得到 {schema_version!r}")
        if schema_version == REVIEW_ISSUE_SCHEMA_VERSION_V1 and any(
                (sentence_id, citation_id, semantic_category)):
            raise AssuranceSchemaError(
                f"ReviewIssue：schema_version={schema_version!r} 时不接受句级扩展字段；"
                f"要走 §0.20 句级审阅，请显式使用 schema_version="
                f"{REVIEW_ISSUE_SCHEMA_VERSION!r}")
        payload = {
            "schema_version": schema_version,
            "report_version": report_version,
            "unit_ref": unit_ref.to_dict(),
            "category": cat,
            "severity": severity,
            "blocking": blocking,
            "reason": reason,
            "evidence_refs": list(refs),
            "suggested_target": (None if suggested_target is None
                                 else suggested_target.to_dict()),
        }
        if schema_version == REVIEW_ISSUE_SCHEMA_VERSION:
            payload["sentence_id"] = sentence_id
            payload["citation_id"] = citation_id
            payload["semantic_category"] = semantic_category
        return cls(issue_id=content_id("rvi_", payload), schema_version=schema_version,
                   report_version=report_version, unit_ref=unit_ref, category=cat,
                   severity=severity, blocking=blocking, reason=reason, evidence_refs=refs,
                   suggested_target=suggested_target, sentence_id=sentence_id,
                   citation_id=citation_id, semantic_category=semantic_category)

    @classmethod
    def from_dict(cls, d: Any) -> "ReviewIssue":
        what = "ReviewIssue"
        d = _reject_forbidden(d, REVIEWER_FORBIDDEN_FIELDS, what)
        version = d.get("schema_version") if isinstance(d, dict) else None
        if version not in REVIEW_ISSUE_SCHEMA_VERSIONS:
            raise AssuranceSchemaError(
                f"{what}.schema_version 必须属于 {REVIEW_ISSUE_SCHEMA_VERSIONS}，"
                f"得到 {version!r}")
        allowed = (_REVIEW_ISSUE_V1_FIELDS if version == REVIEW_ISSUE_SCHEMA_VERSION_V1
                   else _REVIEW_ISSUE_V2_FIELDS)
        d = _reject_unknown(d, allowed, what)
        target = d.get("suggested_target")
        return cls(
            schema_version=version,
            issue_id=_require_nonempty(_req(d, "issue_id", what), f"{what}.issue_id"),
            report_version=_require_nonempty(_req(d, "report_version", what),
                                             f"{what}.report_version"),
            unit_ref=ReviewUnitRef.from_dict(_req(d, "unit_ref", what)),
            category=normalize_review_category(_req(d, "category", what), f"{what}.category"),
            severity=_req(d, "severity", what),
            blocking=_req(d, "blocking", what),
            reason=_req(d, "reason", what),
            evidence_refs=_req(d, "evidence_refs", what),
            suggested_target=_maybe_none(target, SuggestedTarget.from_dict),
            sentence_id=d.get("sentence_id", ""),
            citation_id=d.get("citation_id", ""),
            semantic_category=d.get("semantic_category", ""))


# ---------------------------------------------------------------------------
# ReviewExcerpt / ReviewInputBundle
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReviewExcerpt:
    """Reviewer 可读的**最小**来源片段：仅「独立解析出的出处文本 + 定位」。

    刻意没有「作者说明」「自评」「历史」之类字段：Reviewer 上下文要隔离于生成过程
    （§7.5），这条约束落在**线形状**上比落在调用方检查上更牢。
    """

    excerpt_id: str
    unit_ref: ReviewUnitRef
    text: str
    source_locator: str
    citation_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.unit_ref, ReviewUnitRef):
            raise AssuranceSchemaError("ReviewExcerpt.unit_ref 必须是 ReviewUnitRef")
        _require_nonempty(self.text, "ReviewExcerpt.text")
        _require_nonempty(self.source_locator, "ReviewExcerpt.source_locator")
        _require_nonempty(self.citation_id, "ReviewExcerpt.citation_id")
        expected = content_id("rve_", self.identity_body())
        if self.excerpt_id != expected:
            raise AssuranceSchemaError(
                f"ReviewExcerpt.excerpt_id 与内容不符：声明 {self.excerpt_id!r}，"
                f"应为 {expected!r}")

    def identity_body(self) -> dict:
        return {"unit_ref": self.unit_ref.to_dict(), "text": self.text,
                "source_locator": self.source_locator, "citation_id": self.citation_id}

    def to_dict(self) -> dict:
        return {"excerpt_id": self.excerpt_id, **self.identity_body()}

    @classmethod
    def create(cls, *, unit_ref: ReviewUnitRef, text: str, source_locator: str,
               citation_id: str) -> "ReviewExcerpt":
        payload = {"unit_ref": unit_ref.to_dict(), "text": text,
                   "source_locator": source_locator, "citation_id": citation_id}
        return cls(excerpt_id=content_id("rve_", payload), unit_ref=unit_ref, text=text,
                   source_locator=source_locator, citation_id=citation_id)

    @classmethod
    def from_dict(cls, d: Any) -> "ReviewExcerpt":
        what = "ReviewExcerpt"
        d = _reject_unknown(d, {"excerpt_id", "unit_ref", "text", "source_locator",
                                "citation_id"}, what)
        return cls(
            excerpt_id=_require_nonempty(_req(d, "excerpt_id", what), f"{what}.excerpt_id"),
            unit_ref=ReviewUnitRef.from_dict(_req(d, "unit_ref", what)),
            text=_req(d, "text", what),
            source_locator=_req(d, "source_locator", what),
            citation_id=_req(d, "citation_id", what))


@dataclass(frozen=True)
class ReviewInputBundle:
    """隔离的只读审查输入（§7.5）。

    `unit_inventory` 是**预定义**审核单元集——Reviewer 必须对其中每一个单元给出结果，
    不能用空数组冒充「全部 supported」（§7.3）。`excluded_context` 必须**逐项声明**
    §7.5 的五类禁止来源；未声明即拒绝，而不是「默认没给就等于没看」。

    `rib-2`（§0.20）再加一个 `sentence_inventory`：本次审阅覆盖的**句子** id 集。它与
    `unit_inventory` 分工不同——后者回答「哪些来源单元可读」，前者回答「哪几句话必须逐句
    表态」。两者都要覆盖，`Unit`/`Sentence` 谁都不许用空集冒充全通过；覆盖检查在
    `sections/cited_review.py` 里对着真实响应做（本层只有形状，没有响应）。
    """

    schema_version: str
    bundle_id: str
    report_version: str
    report_id: str
    unit_inventory: tuple[ReviewUnitRef, ...]
    excerpts: tuple[ReviewExcerpt, ...]
    claim_ids: tuple[str, ...]
    citation_ids: tuple[str, ...]
    gap_refs: tuple[str, ...]
    conflict_refs: tuple[str, ...]
    rubric_refs: tuple[str, ...]
    allowed_content_fingerprint: str
    excluded_context: tuple[str, ...]
    prompt_version: str
    model_policy_id: str
    #: `rib-2`：本条 bundle 必须逐句表态的句子 id（`rib-1` 恒为空元组）。
    sentence_inventory: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version not in REVIEW_INPUT_BUNDLE_SCHEMA_VERSIONS:
            raise AssuranceSchemaError(
                f"ReviewInputBundle.schema_version 必须属于 "
                f"{REVIEW_INPUT_BUNDLE_SCHEMA_VERSIONS}，得到 {self.schema_version!r}")
        if not isinstance(self.sentence_inventory, tuple):
            raise AssuranceSchemaError("ReviewInputBundle.sentence_inventory 必须是元组")
        _sorted_unique(self.sentence_inventory, "ReviewInputBundle.sentence_inventory")
        if (self.schema_version == REVIEW_INPUT_BUNDLE_SCHEMA_VERSION_V1
                and self.sentence_inventory):
            raise AssuranceSchemaError(
                f"ReviewInputBundle：schema_version={REVIEW_INPUT_BUNDLE_SCHEMA_VERSION_V1!r} "
                f"上不得出现 sentence_inventory（须显式使用 "
                f"{REVIEW_INPUT_BUNDLE_SCHEMA_VERSION!r}）")
        _require_nonempty(self.report_version, "ReviewInputBundle.report_version")
        _require_nonempty(self.report_id, "ReviewInputBundle.report_id")
        _require_nonempty(self.prompt_version, "ReviewInputBundle.prompt_version")
        _require_nonempty(self.model_policy_id, "ReviewInputBundle.model_policy_id")
        if not _is_sha256_hex(self.allowed_content_fingerprint):
            raise AssuranceSchemaError(
                "ReviewInputBundle.allowed_content_fingerprint 必须是 sha256 hex")

        if not isinstance(self.unit_inventory, tuple) or not self.unit_inventory:
            raise AssuranceSchemaError("ReviewInputBundle.unit_inventory 必须是非空元组")
        keys = [u.key for u in self.unit_inventory]
        for u in self.unit_inventory:
            if not isinstance(u, ReviewUnitRef):
                raise AssuranceSchemaError("ReviewInputBundle.unit_inventory 含非 ReviewUnitRef")
        if len(set(keys)) != len(keys):
            raise AssuranceSchemaError(
                f"ReviewInputBundle.unit_inventory 含重复单元 {sorted(keys)}")
        if keys != sorted(keys):
            raise AssuranceSchemaError(
                "ReviewInputBundle.unit_inventory 必须按单元键升序（确定性聚合的前提）")
        self._check_children(keys)

        for name in ("claim_ids", "citation_ids", "gap_refs", "conflict_refs", "rubric_refs"):
            _sorted_unique(getattr(self, name), f"ReviewInputBundle.{name}")

        declared = _sorted_unique(self.excluded_context, "ReviewInputBundle.excluded_context")
        missing = sorted(set(REQUIRED_EXCLUDED_CONTEXT) - set(declared))
        if missing:
            raise AssuranceSchemaError(
                f"ReviewInputBundle.excluded_context 未声明必须排除的上下文 {missing}"
                f"（§7.5：上下文不得包含 Writer hidden history / prompt / 自评 / "
                f"Assurance 期望结论 / 人工 verdict）")

        expected = content_id("rib_", self.identity_body())
        if self.bundle_id != expected:
            raise AssuranceSchemaError(
                f"ReviewInputBundle.bundle_id 与内容不符：声明 {self.bundle_id!r}，"
                f"应为 {expected!r}")

    def _check_children(self, unit_keys: list[str]) -> None:
        if not isinstance(self.excerpts, tuple):
            raise AssuranceSchemaError("ReviewInputBundle.excerpts 必须是元组")
        ids = []
        for ex in self.excerpts:
            if not isinstance(ex, ReviewExcerpt):
                raise AssuranceSchemaError("ReviewInputBundle.excerpts 含非 ReviewExcerpt")
            if ex.unit_ref.key not in unit_keys:
                raise AssuranceSchemaError(
                    f"ReviewInputBundle.excerpts 引用了审核单元集之外的单元 "
                    f"{ex.unit_ref.key!r}")
            ids.append(ex.excerpt_id)
        if len(set(ids)) != len(ids):
            raise AssuranceSchemaError("ReviewInputBundle.excerpts 含重复片段")
        if ids != sorted(ids):
            raise AssuranceSchemaError(
                "ReviewInputBundle.excerpts 必须按 excerpt_id 升序（确定性聚合的前提）")

    def unit_keys(self) -> tuple[str, ...]:
        return tuple(u.key for u in self.unit_inventory)

    def identity_body(self) -> dict:
        body = {
            "schema_version": self.schema_version,
            "report_version": self.report_version,
            "report_id": self.report_id,
            "unit_inventory": [u.to_dict() for u in self.unit_inventory],
            "excerpts": [e.to_dict() for e in self.excerpts],
            "claim_ids": list(self.claim_ids),
            "citation_ids": list(self.citation_ids),
            "gap_refs": list(self.gap_refs),
            "conflict_refs": list(self.conflict_refs),
            "rubric_refs": list(self.rubric_refs),
            "allowed_content_fingerprint": self.allowed_content_fingerprint,
            "excluded_context": list(self.excluded_context),
            "prompt_version": self.prompt_version,
            "model_policy_id": self.model_policy_id,
        }
        if self.schema_version == REVIEW_INPUT_BUNDLE_SCHEMA_VERSION:
            body["sentence_inventory"] = list(self.sentence_inventory)
        return body

    def to_dict(self) -> dict:
        return {"bundle_id": self.bundle_id, **self.identity_body()}

    @classmethod
    def create(cls, *, report_version: str, report_id: str,
               unit_inventory: Iterable[ReviewUnitRef],
               excerpts: Iterable[ReviewExcerpt] = (), claim_ids: Iterable[str] = (),
               citation_ids: Iterable[str] = (), gap_refs: Iterable[str] = (),
               conflict_refs: Iterable[str] = (), rubric_refs: Iterable[str] = (),
               allowed_content_fingerprint: str,
               excluded_context: Iterable[str] = REQUIRED_EXCLUDED_CONTEXT,
               prompt_version: str = INDEPENDENT_REVIEWER_PROMPT_VERSION,
               model_policy_id: str,
               schema_version: str = REVIEW_INPUT_BUNDLE_SCHEMA_VERSION_V1,
               sentence_inventory: Iterable[str] = ()) -> "ReviewInputBundle":
        """默认发射 `rib-1`，理由同 `ReviewIssue.create`：旧调用点的 bundle id 不变。"""
        if schema_version not in REVIEW_INPUT_BUNDLE_SCHEMA_VERSIONS:
            raise AssuranceSchemaError(
                f"ReviewInputBundle.schema_version 必须属于 "
                f"{REVIEW_INPUT_BUNDLE_SCHEMA_VERSIONS}，得到 {schema_version!r}")
        units = tuple(sorted(unit_inventory, key=lambda u: u.key))
        exs = tuple(sorted(excerpts, key=lambda e: e.excerpt_id))
        claims = _normalized(claim_ids, "claim_ids")
        cits = _normalized(citation_ids, "citation_ids")
        gaps = _normalized(gap_refs, "gap_refs")
        conflicts = _normalized(conflict_refs, "conflict_refs")
        rubrics = _normalized(rubric_refs, "rubric_refs")
        excluded = _normalized(excluded_context, "excluded_context")
        sentences = _normalized(sentence_inventory, "sentence_inventory")
        if schema_version == REVIEW_INPUT_BUNDLE_SCHEMA_VERSION_V1 and sentences:
            raise AssuranceSchemaError(
                f"ReviewInputBundle：schema_version={schema_version!r} 时不接受 "
                f"sentence_inventory；要做 §0.20 句级审阅请显式使用 "
                f"{REVIEW_INPUT_BUNDLE_SCHEMA_VERSION!r}")
        payload = {
            "schema_version": schema_version,
            "report_version": report_version,
            "report_id": report_id,
            "unit_inventory": [u.to_dict() for u in units],
            "excerpts": [e.to_dict() for e in exs],
            "claim_ids": list(claims),
            "citation_ids": list(cits),
            "gap_refs": list(gaps),
            "conflict_refs": list(conflicts),
            "rubric_refs": list(rubrics),
            "allowed_content_fingerprint": allowed_content_fingerprint,
            "excluded_context": list(excluded),
            "prompt_version": prompt_version,
            "model_policy_id": model_policy_id,
        }
        if schema_version == REVIEW_INPUT_BUNDLE_SCHEMA_VERSION:
            payload["sentence_inventory"] = list(sentences)
        return cls(bundle_id=content_id("rib_", payload),
                   schema_version=schema_version, report_version=report_version,
                   report_id=report_id, unit_inventory=units, excerpts=exs,
                   claim_ids=claims, citation_ids=cits, gap_refs=gaps,
                   conflict_refs=conflicts, rubric_refs=rubrics,
                   allowed_content_fingerprint=allowed_content_fingerprint,
                   excluded_context=excluded, prompt_version=prompt_version,
                   model_policy_id=model_policy_id, sentence_inventory=sentences)

    @classmethod
    def from_dict(cls, d: Any) -> "ReviewInputBundle":
        what = "ReviewInputBundle"
        version = d.get("schema_version") if isinstance(d, dict) else None
        if version not in REVIEW_INPUT_BUNDLE_SCHEMA_VERSIONS:
            raise AssuranceSchemaError(
                f"{what}.schema_version 必须属于 {REVIEW_INPUT_BUNDLE_SCHEMA_VERSIONS}，"
                f"得到 {version!r}")
        allowed = (_REVIEW_INPUT_BUNDLE_V1_FIELDS
                   if version == REVIEW_INPUT_BUNDLE_SCHEMA_VERSION_V1
                   else _REVIEW_INPUT_BUNDLE_V2_FIELDS)
        d = _reject_unknown(d, allowed, what)
        units = _req(d, "unit_inventory", what)
        if not isinstance(units, (list, tuple)):
            raise AssuranceSchemaError(f"{what}.unit_inventory 必须是列表")
        excerpts = _req(d, "excerpts", what)
        if not isinstance(excerpts, (list, tuple)):
            raise AssuranceSchemaError(f"{what}.excerpts 必须是列表")
        return cls(
            schema_version=version,
            bundle_id=_require_nonempty(_req(d, "bundle_id", what), f"{what}.bundle_id"),
            report_version=_require_nonempty(_req(d, "report_version", what),
                                             f"{what}.report_version"),
            report_id=_require_nonempty(_req(d, "report_id", what), f"{what}.report_id"),
            unit_inventory=tuple(ReviewUnitRef.from_dict(u) for u in units),
            excerpts=tuple(ReviewExcerpt.from_dict(e) for e in excerpts),
            claim_ids=_req(d, "claim_ids", what),
            citation_ids=_req(d, "citation_ids", what),
            gap_refs=_req(d, "gap_refs", what),
            conflict_refs=_req(d, "conflict_refs", what),
            rubric_refs=_req(d, "rubric_refs", what),
            allowed_content_fingerprint=_req(d, "allowed_content_fingerprint", what),
            excluded_context=_req(d, "excluded_context", what),
            prompt_version=_req(d, "prompt_version", what),
            model_policy_id=_req(d, "model_policy_id", what),
            sentence_inventory=tuple(d.get("sentence_inventory", ())))


# ---------------------------------------------------------------------------
# HardGateIssue / HardGateResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HardGateIssue:
    """一条确定性硬门失败（§7.4）。

    两个布尔**正交**，不是一个枚举的两个取值：

    - `reviewability_blocking`：身份/解析/locator/authority 坏到 Reviewer **无法安全
      读取**，于是阻止 Reviewer 运行；
    - `release_blocking`：报告不可通过系统 Assurance，但已持久化的可预览内容仍然可以
      交给 Reviewer 检查。

    hash 篡改、locator 无法解析、report identity 不闭合属前者兼后者；selected/full
    Contract 范围缺口、诚实 unresolved、覆盖不足只属后者。把这两者合成一个字段，
    Demo 就只剩「整份跳过」和「假装通过」两种行为——即 §7.4 要避免的那件事。
    """

    schema_version: str
    gate_issue_id: str
    gate_kind: str
    reviewability_blocking: bool
    release_blocking: bool
    field: str
    declared: str
    observed: str
    reason: str
    unit_ref: ReviewUnitRef | None

    def __post_init__(self) -> None:
        if self.schema_version != HARD_GATE_RESULT_SCHEMA_VERSION:
            raise AssuranceSchemaError(
                f"HardGateIssue.schema_version 必须为 {HARD_GATE_RESULT_SCHEMA_VERSION!r}")
        _require_one_of(self.gate_kind, HARD_GATE_KINDS, "HardGateIssue.gate_kind")
        _require_bool(self.reviewability_blocking, "HardGateIssue.reviewability_blocking")
        _require_bool(self.release_blocking, "HardGateIssue.release_blocking")
        if not (self.reviewability_blocking or self.release_blocking):
            raise AssuranceSchemaError(
                "HardGateIssue 必须至少阻断一个轴（reviewability_blocking / "
                "release_blocking）；两者皆否的记录不构成硬门失败")
        for name in ("field", "declared", "observed", "reason"):
            _require_nonempty(getattr(self, name), f"HardGateIssue.{name}")
        if len(self.reason) > MAX_REASON_CHARS:
            raise AssuranceSchemaError(f"HardGateIssue.reason 超过 {MAX_REASON_CHARS} 字符")
        if self.unit_ref is not None and not isinstance(self.unit_ref, ReviewUnitRef):
            raise AssuranceSchemaError("HardGateIssue.unit_ref 必须是 ReviewUnitRef 或 none")

        expected = content_id("hgi_", self.identity_body())
        if self.gate_issue_id != expected:
            raise AssuranceSchemaError(
                f"HardGateIssue.gate_issue_id 与内容不符：声明 {self.gate_issue_id!r}，"
                f"应为 {expected!r}")

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "gate_kind": self.gate_kind,
            "reviewability_blocking": self.reviewability_blocking,
            "release_blocking": self.release_blocking,
            "field": self.field,
            "declared": self.declared,
            "observed": self.observed,
            "reason": self.reason,
            "unit_ref": None if self.unit_ref is None else self.unit_ref.to_dict(),
        }

    def to_dict(self) -> dict:
        return {"gate_issue_id": self.gate_issue_id, **self.identity_body()}

    @classmethod
    def create(cls, *, gate_kind: str, reviewability_blocking: bool, release_blocking: bool,
               field: str, declared: str, observed: str, reason: str,
               unit_ref: ReviewUnitRef | None = None) -> "HardGateIssue":
        payload = {
            "schema_version": HARD_GATE_RESULT_SCHEMA_VERSION,
            "gate_kind": gate_kind,
            "reviewability_blocking": reviewability_blocking,
            "release_blocking": release_blocking,
            "field": field,
            "declared": declared,
            "observed": observed,
            "reason": reason,
            "unit_ref": None if unit_ref is None else unit_ref.to_dict(),
        }
        return cls(gate_issue_id=content_id("hgi_", payload),
                   schema_version=payload["schema_version"], gate_kind=gate_kind,
                   reviewability_blocking=reviewability_blocking,
                   release_blocking=release_blocking, field=field, declared=declared,
                   observed=observed, reason=reason, unit_ref=unit_ref)

    @classmethod
    def from_dict(cls, d: Any) -> "HardGateIssue":
        what = "HardGateIssue"
        d = _reject_unknown(d, {"gate_issue_id", "schema_version", "gate_kind",
                                "reviewability_blocking", "release_blocking", "field",
                                "declared", "observed", "reason", "unit_ref"}, what)
        return cls(
            schema_version=_require_nonempty(_req(d, "schema_version", what),
                                             f"{what}.schema_version"),
            gate_issue_id=_require_nonempty(_req(d, "gate_issue_id", what),
                                            f"{what}.gate_issue_id"),
            gate_kind=_req(d, "gate_kind", what),
            reviewability_blocking=_req(d, "reviewability_blocking", what),
            release_blocking=_req(d, "release_blocking", what),
            field=_req(d, "field", what),
            declared=_req(d, "declared", what),
            observed=_req(d, "observed", what),
            reason=_req(d, "reason", what),
            unit_ref=_maybe_none(d.get("unit_ref"), ReviewUnitRef.from_dict))


@dataclass(frozen=True)
class HardGateResult:
    """一次确定性硬门全跑的结果（§7.4）。

    `declared` 计数必须等于由 `issues` 重算的计数——与 `report_version` 等身份字段一样，
    「声明值必须等于派生值」，否则篡改计数就能改变 Controller 的分支。
    """

    schema_version: str
    result_id: str
    report_version: str
    gate_rules_version: str
    issues: tuple[HardGateIssue, ...]
    reviewability_blocking_count: int
    release_blocking_count: int

    def __post_init__(self) -> None:
        if self.schema_version != HARD_GATE_RESULT_SCHEMA_VERSION:
            raise AssuranceSchemaError(
                f"HardGateResult.schema_version 必须为 {HARD_GATE_RESULT_SCHEMA_VERSION!r}")
        _require_nonempty(self.report_version, "HardGateResult.report_version")
        if self.gate_rules_version != HARD_GATE_RULES_VERSION:
            raise AssuranceSchemaError(
                f"HardGateResult.gate_rules_version 必须为 {HARD_GATE_RULES_VERSION!r}，"
                f"得到 {self.gate_rules_version!r}")
        if not isinstance(self.issues, tuple):
            raise AssuranceSchemaError("HardGateResult.issues 必须是元组")
        ids = []
        for it in self.issues:
            if not isinstance(it, HardGateIssue):
                raise AssuranceSchemaError("HardGateResult.issues 含非 HardGateIssue")
            ids.append(it.gate_issue_id)
        if len(set(ids)) != len(ids):
            raise AssuranceSchemaError("HardGateResult.issues 含重复条目")
        if ids != sorted(ids):
            raise AssuranceSchemaError(
                "HardGateResult.issues 必须按 gate_issue_id 升序（确定性聚合的前提）")

        _require_int(self.reviewability_blocking_count,
                     "HardGateResult.reviewability_blocking_count")
        _require_int(self.release_blocking_count, "HardGateResult.release_blocking_count")
        want_rv = sum(1 for it in self.issues if it.reviewability_blocking)
        want_rl = sum(1 for it in self.issues if it.release_blocking)
        if self.reviewability_blocking_count != want_rv:
            raise AssuranceSchemaError(
                f"HardGateResult.reviewability_blocking_count 与 issues 重算不符："
                f"声明 {self.reviewability_blocking_count}，应为 {want_rv}")
        if self.release_blocking_count != want_rl:
            raise AssuranceSchemaError(
                f"HardGateResult.release_blocking_count 与 issues 重算不符："
                f"声明 {self.release_blocking_count}，应为 {want_rl}")

        expected = content_id("hgr_", self.identity_body())
        if self.result_id != expected:
            raise AssuranceSchemaError(
                f"HardGateResult.result_id 与内容不符：声明 {self.result_id!r}，"
                f"应为 {expected!r}")

    @property
    def reviewability_blocked(self) -> bool:
        """为真时 Controller **跳过** LLM Reviewer（§7.4）。"""
        return self.reviewability_blocking_count > 0

    @property
    def release_blocked(self) -> bool:
        return self.release_blocking_count > 0

    def issue_ids(self) -> tuple[str, ...]:
        return tuple(it.gate_issue_id for it in self.issues)

    def identity_body(self) -> dict:
        return {"schema_version": self.schema_version,
                "report_version": self.report_version,
                "gate_rules_version": self.gate_rules_version,
                "issues": [it.to_dict() for it in self.issues],
                "reviewability_blocking_count": self.reviewability_blocking_count,
                "release_blocking_count": self.release_blocking_count}

    def to_dict(self) -> dict:
        return {"result_id": self.result_id, **self.identity_body()}

    @classmethod
    def create(cls, *, report_version: str,
               issues: Iterable[HardGateIssue] = ()) -> "HardGateResult":
        ordered = tuple(sorted(issues, key=lambda it: it.gate_issue_id))
        rv = sum(1 for it in ordered if it.reviewability_blocking)
        rl = sum(1 for it in ordered if it.release_blocking)
        payload = {
            "schema_version": HARD_GATE_RESULT_SCHEMA_VERSION,
            "report_version": report_version,
            "gate_rules_version": HARD_GATE_RULES_VERSION,
            "issues": [it.to_dict() for it in ordered],
            "reviewability_blocking_count": rv,
            "release_blocking_count": rl,
        }
        return cls(result_id=content_id("hgr_", payload),
                   schema_version=payload["schema_version"], report_version=report_version,
                   gate_rules_version=HARD_GATE_RULES_VERSION, issues=ordered,
                   reviewability_blocking_count=rv, release_blocking_count=rl)

    @classmethod
    def from_dict(cls, d: Any) -> "HardGateResult":
        what = "HardGateResult"
        d = _reject_unknown(d, {"result_id", "schema_version", "report_version",
                                "gate_rules_version", "issues",
                                "reviewability_blocking_count",
                                "release_blocking_count"}, what)
        issues = _req(d, "issues", what)
        if not isinstance(issues, (list, tuple)):
            raise AssuranceSchemaError(f"{what}.issues 必须是列表")
        return cls(
            schema_version=_require_nonempty(_req(d, "schema_version", what),
                                             f"{what}.schema_version"),
            result_id=_require_nonempty(_req(d, "result_id", what), f"{what}.result_id"),
            report_version=_require_nonempty(_req(d, "report_version", what),
                                             f"{what}.report_version"),
            gate_rules_version=_require_nonempty(_req(d, "gate_rules_version", what),
                                                 f"{what}.gate_rules_version"),
            issues=tuple(HardGateIssue.from_dict(it) for it in issues),
            reviewability_blocking_count=_req(d, "reviewability_blocking_count", what),
            release_blocking_count=_req(d, "release_blocking_count", what))


# ---------------------------------------------------------------------------
# ReviewerRunRecord
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReviewerRunRecord:
    """Reviewer 的**唯一一次**调用的审计记录（§7.5「严格一次结构化调用」）。

    `llm_call_count` 被强制与 `outcome` 一致，且上限为 1：多次重试「刷过」在这个对象上
    根本无法被表达出来，因此也不需要靠调用方自律。
    """

    schema_version: str
    record_id: str
    report_version: str
    bundle_id: str
    prompt_version: str
    model_policy_id: str
    outcome: str
    llm_call_count: int
    covered_unit_keys: tuple[str, ...]
    issue_ids: tuple[str, ...]
    response_fingerprint: str

    def __post_init__(self) -> None:
        if self.schema_version != REVIEWER_RUN_RECORD_SCHEMA_VERSION:
            raise AssuranceSchemaError(
                f"ReviewerRunRecord.schema_version 必须为 "
                f"{REVIEWER_RUN_RECORD_SCHEMA_VERSION!r}")
        for name in ("report_version", "prompt_version", "model_policy_id"):
            _require_nonempty(getattr(self, name), f"ReviewerRunRecord.{name}")
        _require_one_of(self.outcome, REVIEWER_OUTCOMES, "ReviewerRunRecord.outcome")
        _require_int(self.llm_call_count, "ReviewerRunRecord.llm_call_count")

        _sorted_unique(self.covered_unit_keys, "ReviewerRunRecord.covered_unit_keys")
        _sorted_unique(self.issue_ids, "ReviewerRunRecord.issue_ids")

        if self.outcome == "reviewed":
            if self.llm_call_count != 1:
                raise AssuranceSchemaError(
                    f"ReviewerRunRecord：outcome='reviewed' 时 llm_call_count 必须为 1，"
                    f"得到 {self.llm_call_count}")
            _require_nonempty(self.bundle_id, "ReviewerRunRecord.bundle_id（reviewed 必须绑定 bundle）")
            if not _is_sha256_hex(self.response_fingerprint):
                raise AssuranceSchemaError(
                    "ReviewerRunRecord：已调用时 response_fingerprint 必须是 sha256 hex")
        else:
            if self.llm_call_count != 0:
                raise AssuranceSchemaError(
                    f"ReviewerRunRecord：outcome={self.outcome!r} 时 llm_call_count 必须为 0，"
                    f"得到 {self.llm_call_count}（不得以重试掩盖失败）")
            if self.response_fingerprint != "":
                raise AssuranceSchemaError(
                    "ReviewerRunRecord：未调用时 response_fingerprint 必须为空串")

        expected = content_id("rrr_", self.identity_body())
        if self.record_id != expected:
            raise AssuranceSchemaError(
                f"ReviewerRunRecord.record_id 与内容不符：声明 {self.record_id!r}，"
                f"应为 {expected!r}")

    @property
    def called(self) -> bool:
        return self.llm_call_count == 1

    def identity_body(self) -> dict:
        return {"schema_version": self.schema_version,
                "report_version": self.report_version,
                "bundle_id": self.bundle_id,
                "prompt_version": self.prompt_version,
                "model_policy_id": self.model_policy_id,
                "outcome": self.outcome,
                "llm_call_count": self.llm_call_count,
                "covered_unit_keys": list(self.covered_unit_keys),
                "issue_ids": list(self.issue_ids),
                "response_fingerprint": self.response_fingerprint}

    def to_dict(self) -> dict:
        return {"record_id": self.record_id, **self.identity_body()}

    @classmethod
    def create(cls, *, report_version: str, bundle_id: str, prompt_version: str,
               model_policy_id: str, outcome: str,
               covered_unit_keys: Iterable[str] = (), issue_ids: Iterable[str] = (),
               response_fingerprint: str = "") -> "ReviewerRunRecord":
        body = {
            "schema_version": REVIEWER_RUN_RECORD_SCHEMA_VERSION,
            "report_version": report_version,
            "bundle_id": bundle_id,
            "prompt_version": prompt_version,
            "model_policy_id": model_policy_id,
            "outcome": outcome,
            "llm_call_count": 1 if outcome == "reviewed" else 0,
            "covered_unit_keys": list(_normalized(covered_unit_keys, "covered_unit_keys")),
            "issue_ids": list(_normalized(issue_ids, "issue_ids")),
            "response_fingerprint": response_fingerprint,
        }
        return cls(record_id=content_id("rrr_", body), **body)

    @classmethod
    def from_dict(cls, d: Any) -> "ReviewerRunRecord":
        what = "ReviewerRunRecord"
        d = _reject_unknown(d, {"record_id", "schema_version", "report_version", "bundle_id",
                                "prompt_version", "model_policy_id", "outcome",
                                "llm_call_count", "covered_unit_keys", "issue_ids",
                                "response_fingerprint"}, what)
        return cls(
            schema_version=_require_nonempty(_req(d, "schema_version", what),
                                             f"{what}.schema_version"),
            record_id=_require_nonempty(_req(d, "record_id", what), f"{what}.record_id"),
            report_version=_req(d, "report_version", what),
            bundle_id=_req(d, "bundle_id", what, allow_none=False),
            prompt_version=_req(d, "prompt_version", what),
            model_policy_id=_req(d, "model_policy_id", what),
            outcome=_req(d, "outcome", what),
            llm_call_count=_req(d, "llm_call_count", what),
            covered_unit_keys=_req(d, "covered_unit_keys", what),
            issue_ids=_req(d, "issue_ids", what),
            response_fingerprint=_req(d, "response_fingerprint", what),
        )


# ---------------------------------------------------------------------------
# AssuranceResult
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AssuranceResult:
    """绑定 `report_version` 的确定性聚合状态（§7.6）。

    四个状态轴彼此独立，**不得压成单一 `success`**（`DESIGN_V2.md` §11.3）：面试 Demo
    的合法状态是「流程完成 + 草稿可预览 + 系统审核未通过 + 人工未复核」——即下面四个字段
    同时出现。页面若只显示一个布尔，这一层的表达能力就被丢掉了。

    `human_review_state` 由调用方**带过**，不参与 `system_release_eligible` 的计算；
    `formal_closure` **不是**本对象的字段（见 `ASSURANCE_FORBIDDEN_FIELDS`）。
    """

    schema_version: str
    assurance_id: str
    report_version: str
    hard_gate_result_id: str
    reviewer_run_record_id: str
    issue_ids: tuple[str, ...]
    blocking_issue_count: int
    process_state: str
    preview_state: str
    system_review_state: str
    human_review_state: str
    system_release_eligible: bool
    aggregate_rules_version: str

    def __post_init__(self) -> None:
        if self.schema_version != ASSURANCE_RESULT_SCHEMA_VERSION:
            raise AssuranceSchemaError(
                f"AssuranceResult.schema_version 必须为 {ASSURANCE_RESULT_SCHEMA_VERSION!r}")
        _require_nonempty(self.report_version, "AssuranceResult.report_version")
        _require_nonempty(self.hard_gate_result_id, "AssuranceResult.hard_gate_result_id")
        if self.aggregate_rules_version != ASSURANCE_AGGREGATE_RULES_VERSION:
            raise AssuranceSchemaError(
                f"AssuranceResult.aggregate_rules_version 必须为 "
                f"{ASSURANCE_AGGREGATE_RULES_VERSION!r}，得到 "
                f"{self.aggregate_rules_version!r}")
        _sorted_unique(self.issue_ids, "AssuranceResult.issue_ids")
        _require_int(self.blocking_issue_count, "AssuranceResult.blocking_issue_count")
        if self.blocking_issue_count > len(self.issue_ids):
            raise AssuranceSchemaError(
                f"AssuranceResult.blocking_issue_count={self.blocking_issue_count} "
                f"超过 issue_ids 数 {len(self.issue_ids)}")
        _require_one_of(self.process_state, PROCESS_STATES, "AssuranceResult.process_state")
        _require_one_of(self.preview_state, PREVIEW_STATES, "AssuranceResult.preview_state")
        _require_one_of(self.system_review_state, SYSTEM_REVIEW_STATES,
                        "AssuranceResult.system_review_state")
        _require_one_of(self.human_review_state, HUMAN_REVIEW_STATES,
                        "AssuranceResult.human_review_state")
        _require_bool(self.system_release_eligible, "AssuranceResult.system_release_eligible")
        self._check_state_coherence()

        expected = content_id("asr_", self.identity_body())
        if self.assurance_id != expected:
            raise AssuranceSchemaError(
                f"AssuranceResult.assurance_id 与内容不符：声明 {self.assurance_id!r}，"
                f"应为 {expected!r}")

    def _check_state_coherence(self) -> None:
        if self.system_review_state == "system_review_not_run":
            if self.reviewer_run_record_id != "":
                raise AssuranceSchemaError(
                    "AssuranceResult：system_review_not_run 时不得绑定 reviewer_run_record_id")
        else:
            _require_nonempty(self.reviewer_run_record_id,
                              "AssuranceResult.reviewer_run_record_id")

        if self.system_review_state == "system_review_passed_awaiting_human":
            if not self.system_release_eligible:
                raise AssuranceSchemaError(
                    "AssuranceResult：system_review_passed_awaiting_human 蕴含 "
                    "system_release_eligible=True")
            if self.blocking_issue_count != 0:
                raise AssuranceSchemaError(
                    "AssuranceResult：系统审核通过时不得存在 blocking issue")
        elif self.system_review_state == "system_review_not_passed":
            if self.system_release_eligible:
                raise AssuranceSchemaError(
                    "AssuranceResult：system_review_not_passed 蕴含 "
                    "system_release_eligible=False（未通过系统审核却可放行是矛盾状态）")

        if self.process_state == "flow_incomplete" and self.system_release_eligible:
            raise AssuranceSchemaError(
                "AssuranceResult：流程未完成时 system_release_eligible 必须为 False")

    def identity_body(self) -> dict:
        return {"schema_version": self.schema_version,
                "report_version": self.report_version,
                "hard_gate_result_id": self.hard_gate_result_id,
                "reviewer_run_record_id": self.reviewer_run_record_id,
                "issue_ids": list(self.issue_ids),
                "blocking_issue_count": self.blocking_issue_count,
                "process_state": self.process_state,
                "preview_state": self.preview_state,
                "system_review_state": self.system_review_state,
                "human_review_state": self.human_review_state,
                "system_release_eligible": self.system_release_eligible,
                "aggregate_rules_version": self.aggregate_rules_version}

    def to_dict(self) -> dict:
        return {"assurance_id": self.assurance_id, **self.identity_body()}

    @classmethod
    def create(cls, *, report_version: str, hard_gate_result_id: str,
               reviewer_run_record_id: str, issue_ids: Iterable[str],
               blocking_issue_count: int, process_state: str, preview_state: str,
               system_review_state: str, human_review_state: str,
               system_release_eligible: bool) -> "AssuranceResult":
        body = {
            "schema_version": ASSURANCE_RESULT_SCHEMA_VERSION,
            "report_version": report_version,
            "hard_gate_result_id": hard_gate_result_id,
            "reviewer_run_record_id": reviewer_run_record_id,
            "issue_ids": list(_normalized(issue_ids, "issue_ids")),
            "blocking_issue_count": blocking_issue_count,
            "process_state": process_state,
            "preview_state": preview_state,
            "system_review_state": system_review_state,
            "human_review_state": human_review_state,
            "system_release_eligible": system_release_eligible,
            "aggregate_rules_version": ASSURANCE_AGGREGATE_RULES_VERSION,
        }
        return cls(assurance_id=content_id("asr_", body), **body)

    @classmethod
    def from_dict(cls, d: Any) -> "AssuranceResult":
        what = "AssuranceResult"
        d = _reject_forbidden(d, ASSURANCE_FORBIDDEN_FIELDS, what)
        d = _reject_unknown(d, {"assurance_id", "schema_version", "report_version",
                                "hard_gate_result_id", "reviewer_run_record_id",
                                "issue_ids", "blocking_issue_count", "process_state",
                                "preview_state", "system_review_state",
                                "human_review_state", "system_release_eligible",
                                "aggregate_rules_version"}, what)
        return cls(
            schema_version=_require_nonempty(_req(d, "schema_version", what),
                                             f"{what}.schema_version"),
            assurance_id=_require_nonempty(_req(d, "assurance_id", what),
                                           f"{what}.assurance_id"),
            report_version=_req(d, "report_version", what),
            hard_gate_result_id=_req(d, "hard_gate_result_id", what),
            reviewer_run_record_id=_req(d, "reviewer_run_record_id", what),
            issue_ids=_req(d, "issue_ids", what),
            blocking_issue_count=_req(d, "blocking_issue_count", what),
            process_state=_req(d, "process_state", what),
            preview_state=_req(d, "preview_state", what),
            system_review_state=_req(d, "system_review_state", what),
            human_review_state=_req(d, "human_review_state", what),
            system_release_eligible=_req(d, "system_release_eligible", what),
            aggregate_rules_version=_req(d, "aggregate_rules_version", what),
        )

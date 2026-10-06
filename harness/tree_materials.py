"""M930-2 §5.6：verified OutlineSpan → `ResearchMaterial` + 版本化 `tree-material-payload-v1` 信封。

**本模块不新建材料类型、不新建权威来源、不新建 Store。** 它只做一件事：把一个**已经过
live 验签**的 `OutlineSpan` 投影为现有 `harness.topic_schema.ResearchMaterial`
（`material_type="evidence_span"`），并把"这个 span 到底是哪一段真实 Evidence"写成一个
可版本化、可独立重切的 payload 信封。

身份分层（与 R2 一致，不发明第三层）：

- ``source_content_hash`` = **父 Evidence 块**的 `content_hash`（来源层）；
- ``payload_hash`` = ``sha256(payload_bytes)``（载体层，``payload_id == payload_hash ==
  MaterialPayloadRef.content_hash == ResearchMaterial.content_hash``）；
- ``authority_identity == "evidence:{父块 evidence_id}"``，与
  ``authority_source_identity(EvidenceAuthorityAssessment)`` 同域。

**正式消费单位是 `OutlineSpan`；而"可成为材料的 span"每跨一个 Evidence 块就产出一份材料。**
这是当前公共 payload 信封的硬约束（`harness/topic_store._validate_envelope` 要求
``authority_identity == f"evidence:{evidence_id}"`` 并用 ``evidence.ids.make_evidence_id``
按 page/block_index/source_content_hash 正式重算），也是 R2 基线的既有要求
（一个正式 material = 一个真实 `EvidenceBlock`，不得用某个 seed/首块的 evidence_id 冒名跨块）。
因此跨多个 Evidence 块的 span（跨页接续、跨表接续等）在这里被拆成**逐块片段材料**：每个片段
只保留自己那一块的 page / block_range / 来源哈希与**精确字符界**（`locator.offset` 记录本段
在归一块坐标下的截断点），**不**锚定到首块、**不**弱化 locator、**不**把多块内容压成单一来源。
拆分的形状写进信封的 ``span_split``（第几段 / 共几段 / 全文长度 / 本段在 span 本地区的精确
区间），所以"若干片段恰好拼回全文"这件事可读回、可验证：同一 span 的全部片段本地区间恰好铺满
``[0, len(normalized_text))``，且每段的紧文本逐字符等于其父块在对应 `evidence_char_range`
上的紧文本（:func:`_recut_problem`）。

- 未发生拆分（只有一个父块）的材料信封**逐字节不变**：不新增键，历史 payload 哈希不动。
- 只有**同一块内的本地区间不连续**（块序交错，逐块片段无法表达）时才仍登记 gap
  （``multi_evidence_block_span``；该原因保留在闭集里，为的是能读回历史 gap）。

任何被产出的材料都使用**精确定位**的 locator：真实父块 page/block_range + 精确字符界，
精确区间另由信封独立记录并可由解析器逐字符重切复现。

**唯一独立重切解析器** :class:`TreeMaterialPayloadResolver` 从不读已落盘 payload：它从
Evidence + verified snapshot **重算**每一份 payload 字节再建索引。因此"改写磁盘上的
payload 却把 ref 指向旧哈希"只会得到 dangling（上层 fail-closed），不会静默通过。

**内容资格（§二 2.3，M930-3 批次 B）**：能不能重切是一件事，切出来的**内容**是不是业务
材料是另一件事。人口内的候选 span 还要过一次内容判定（:func:`classify_span_content`）：

- **孤立标题 / 纯版式碎片**（文本与所在节点标题逐字相同、编号标题形、或没有句读的短标签、
  或去掉勾选字形后没有任何可读内容）**不是**独立业务事实，也**不足以**充当写作材料：
  它们不产出 material，而是产出一条 typed ``TreeMaterialContentDisposition``（原文片段 +
  locator + 内容指纹一并留档），**既不是 gap、也不静默丢弃**。
- **勾选表单行**（`□适用 不适用` 一类）**不得一律删除**：必须连同**问题**（行内前缀或所在
  节点标题）、**选中状态**（空框/空圈=未选，其余符号字形=已选）、**来源边界**（父块 / 页 /
  节点标题路径）按结构化表单规则判断。真实勾选行的形状是``⟨选项串⟩ ⟨该行所述内容⟩``：选项串
  之后那段内容是这一行**管着**的所述内容（``trailing_content``），随行留档但**不得**被读成
  选项、也**不得**用作支撑（见 ``TREE_MATERIAL_SELECTION_EXCLUSIONS``）。判得出确定状态的，
  仍作为材料照常进入重切与权威链；判不出的，才落 typed 处置并写明是哪一项读不出（缺问题 /
  选项切不开 / 状态不可判）。

判定只依赖 span 自身文本、它所在节点的标题与父 Evidence 边界：没有公司名 / 文件名 / 页码 /
固定年份规则。材料信封里另带 ``content_qualification``，使"这份材料的内容形态"也进内容哈希。

纯转换模块：不 I/O、不建库、不写库、不调 LLM/网络，不持有 live capability。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping

from document_structure import versions as V
from document_structure.canonical import sha256_canonical
from harness import topic_schema as TS
from harness.topic_store import MaterialPayloadRecord

#: 信封种类（§5.6 的版本化过渡接口名，不宣称完成正式 TS7B locator migration）。
TREE_MATERIAL_ENVELOPE_KIND = "tree-material-payload-v1"

#: 现有公共信封版本键的值：``topic_store._validate_envelope`` 严格比对**整数** 1。
MATERIAL_PAYLOAD_VERSION = 1

#: 本模块产出的材料类型（复用现有 `MATERIAL_TYPES` 成员，不新增类型）。
TREE_MATERIAL_TYPE = "evidence_span"

#: 未被压成 material 的候选 span 的登记原因（封闭集合，不得自由字符串）。
#:
#: 注意：**这些是结构原因**（重切不出、权威不成立、跨块不可表达），与内容资格无关。
#: 内容不合格的 span 走 ``TREE_MATERIAL_CONTENT_REASONS``，两者都不冒充 Contract 缺口。
#: ``multi_evidence_block_span`` 现在只用于**块序交错**（同一块内的本地区间不连续）这一
#: 确实无法用逐块片段表达的跨块形状；一般跨块 span 已改为逐块片段材料（见模块 docstring）。
TREE_MATERIAL_GAP_REASONS = (
    "no_admitted_component",
    "non_admitted_component_present",
    "component_span_local_missing",
    "span_local_not_tiling",
    "evidence_range_recheck_failed",
    "multi_evidence_block_span",
    "evidence_block_unavailable",
    "authority_not_authoritative",
)

# ---------------------------------------------------------------------------
# 内容资格（§二 2.3）：人口内的候选 span 的**内容形态**
# ---------------------------------------------------------------------------

#: 候选 span 的内容形态（封闭集合）。``text`` / ``selection_form`` 是**材料**；
#: ``isolated_heading`` / ``layout_fragment`` 不是（见模块 docstring）。
TREE_MATERIAL_CONTENT_KINDS = (
    #: 可读正文。符号字形**落在句读中间**的片段仍是正文（前缀带分句标点 / 选项串超长 /
    #: 单个标签超长 ⇒ 形状不成立，不按表单行处理）；相反，``⟨选项串⟩ ⟨所述内容⟩`` 这种
    #: 头串 + 尾随句的形状是**表单行**——尾随那句是这一行管着的内容，不是选项标签。
    "text",
    #: 勾选表单行（带勾选框字形、按选项行形状切得开；选项串之后的内容另记 ``trailing_content``）。
    "selection_form",
    #: 孤立标题：与所在节点标题逐字相同，或编号标题形。
    "isolated_heading",
    #: 纯版式碎片：去掉勾选字形后没有可读内容，或没有句读的短标签（表头 / 表题残段）。
    "layout_fragment",
)

#: 内容不合格的封闭原因（typed 审计用；每一条都对应一个可复算的判据）。
TREE_MATERIAL_CONTENT_REASONS = (
    "isolated_heading_matches_node_title",
    "isolated_heading_numbered_label",
    "layout_fragment_no_readable_content",
    "layout_fragment_bare_label",
    "selection_form_unresolved",
)

#: 内容形态 → 允许的处置原因。``text`` 不在表里：它是材料，不可能落处置记录。
TREE_MATERIAL_CONTENT_REASONS_BY_KIND = {
    "isolated_heading": ("isolated_heading_matches_node_title",
                         "isolated_heading_numbered_label"),
    "layout_fragment": ("layout_fragment_no_readable_content",
                        "layout_fragment_bare_label"),
    "selection_form": ("selection_form_unresolved",),
}

#: 可以**成为材料**的内容形态。孤立标题与版式碎片永远不是材料：形态本身就说明它不是
#: 业务内容，"孤立标题被当成材料"和"标签被当成材料"是同一类错误的两个方向。
TREE_MATERIAL_CONTENT_MATERIAL_KINDS = ("text", "selection_form")

#: 处置记录里留档的原文片段上限（留档 ≠ 搬运全文；全文由 content_fingerprint 与 locator 钉住）。
CONTENT_DISPOSITION_EXCERPT_CHARS = 120

#: 表单行读不出结构化事实的封闭子原因（只用于 ``selection_form_unresolved``）。
#:
#: ``question_not_resolvable`` 已在 ``tmr-3`` 退出本词表：所问事项取不到**不再是**读不定——
#: 行内没写主语时这一列留空，行仍是材料（见 :func:`_selection_reading`）。留着这个词会
#: 诱使下游把"没有主语"读成"这一行作废"，而真正要防的是**给没有主语的行硬安一个主语**。
TREE_MATERIAL_SELECTION_UNRESOLVED_REASONS = (
    #: 选项切不开（标记字形不够 / 某个选项标签过长 ⇒ 这不是一张纯选项行）。
    "options_not_segmented",
    #: 选项切得开，但选中状态不可判（零个或多个"已选"字形）。
    "state_not_determinable",
)

#: 勾选表单行的**允许用途**（封闭词表，只有一条）。可判定的表单行只在它**自己问的那件事**
#: 上说话：它既不是叙述材料，也不能凭所在父章节就替「主营业务」「收入构成」这类宽栏目作证。
#: 这是一条**范围**判据，不是形态判据：形态说「它是什么」，这里说「它最多能证明什么」。
TREE_MATERIAL_SELECTION_PERMITTED_USE = "asked_item_applicability_only"

#: 表单行**不得**被读成的结论（封闭词表）：逐条写明「这一列读不出什么」，供读者与下游分流。
TREE_MATERIAL_SELECTION_EXCLUSIONS = (
    #: 不得由「本行标为不适用」推成「公司没有该事项」（否定性结论不在行内）。
    "no_negation_beyond_marked_state",
    #: 不得因所在父章节是某个宽栏目，就当作该栏目已覆盖。
    "not_a_broad_column_coverage_proof",
    #: 不得原样充当经营/业务正文（它不是叙述文本）。
    "not_narrative_prose",
    #: `trailing_content`（选项串之后那段内容——这一行"管着"的那句话）**不得**用作支撑：
    #: 它是被问事项的所述内容，不是叙述正文，也不因为"逐字在原文里"就取得资格。要写它，
    #: 只能走预验证权威事实那条路。
    "no_support_from_trailing_content",
)

#: 勾选框 / 符号字形（**封闭集合** + Unicode 私用区 U+E000–U+F8FF）。PDF 里的勾选记号常以
#: 字体私用字形落纸（实测文档里"已勾"就是这个区的码位），所以私用区整体算作符号字形：
#: 私用码位不是任何文字内容，不该被当作可读正文。
TREE_MATERIAL_SYMBOL_GLYPHS = "□☐◻○◯☑√✓✔■●◆◇⭕"

#: 「未勾选」的空框 / 空圈标记：Unicode 语义即「空」。**不在**此集合的符号字形视为
#: 「已勾 / 已选」——因此不需要枚举每份文档各自用了哪个私用字形。
TREE_MATERIAL_HOLLOW_MARKERS = "□☐◻○◯◇"

#: 表单行 / 标签的形状阈值（与公司、文档、页码无关的形状参数）。
SELECTION_ROW_MAX_CHARS = 40          #: 超过这个长度就不是"一张选项行"
SELECTION_OPTION_MAX_CHARS = 12       #: 单个选项标签的上限
BARE_LABEL_MAX_CHARS = 20             #: 无句读短标签的上限
NUMBERED_LABEL_MAX_CHARS = 40         #: 编号标题形的上限
SELECTION_TRAILING_CLAUSE_MAX = 1     #: 尾随内容里分句标点的上限（"一句话"的形状上限）

#: 分句 / 句末标点：出现即认为该片段含句子（正式文本里没有句读的行是标签，不是句子）。
_CLAUSE_PUNCT = "。；！？，、：:;!"
_SENTENCE_END = "。；！？"
_CJK = re.compile(r"[一-鿿]")
_NUMBERED_LABEL = re.compile(r"^[（(]?[0-9０-９]+[）)、.．]\s*\S")


class TreeMaterialError(TS.SchemaValidationError):
    """tree material 构建/解析的 fail-closed 错误（越权调用、非法输入、身份漂移）。"""


# ---------------------------------------------------------------------------
# 1. 输入：current Evidence 绑定（显式、可测、不隐藏信任）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CurrentEvidenceBinding:
    """构建 tree material 时**显式声明**的 current Evidence 身份。

    live 路径必须经 :func:`evidence_binding_from_snapshot` 从已签发的
    `EvidenceSetSnapshot` 派生；测试可以直接构造同一组值（合成快照）。本对象只承载
    identity，不承载任何资格：它不能把非 current 的内容变成 current，因为父块自身的
    company/document/version/set 仍会被逐项复核（见 ``_parent_binding_ok``）。
    """

    company_id: str
    document_id: str
    document_version: str
    evidence_set_version: str
    is_current: bool
    validator_version: str = V.VERIFIED_CURRENT_EVIDENCE_AUTHORITY_VERSION

    def __post_init__(self) -> None:
        for name in ("company_id", "document_id", "document_version",
                     "evidence_set_version", "validator_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or value == "":
                raise TreeMaterialError(
                    f"CurrentEvidenceBinding.{name} 必须为非空字符串，得到 {value!r}")
        if not isinstance(self.is_current, bool):
            raise TreeMaterialError("CurrentEvidenceBinding.is_current 必须为 bool")

    def to_dict(self) -> dict:
        return {
            "company_id": self.company_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "evidence_set_version": self.evidence_set_version,
            "is_current": self.is_current,
            "validator_version": self.validator_version,
        }


def evidence_binding_from_snapshot(evidence_snapshot) -> CurrentEvidenceBinding:
    """从已签发的 `EvidenceSetSnapshot` 派生绑定；非 current 一律 fail-closed。"""
    status = getattr(evidence_snapshot, "status", None)
    if status != "current":
        raise TreeMaterialError(
            f"EvidenceSetSnapshot.status 必须为 'current'，得到 {status!r}"
            f"（非 current 快照不得产出正式材料，fail-closed）")
    return CurrentEvidenceBinding(
        company_id=str(getattr(evidence_snapshot, "company_id", "") or ""),
        document_id=str(getattr(evidence_snapshot, "document_id", "") or ""),
        document_version=str(getattr(evidence_snapshot, "document_version", "") or ""),
        evidence_set_version=str(getattr(evidence_snapshot, "evidence_set_version", "") or ""),
        is_current=True,
    )


# ---------------------------------------------------------------------------
# 2. 输出：gap 与批量结果
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TreeMaterialGap:
    """一个**候选正文 span** 未能成为正式材料的显式登记（不静默丢弃）。"""

    span_id: str
    node_id: str | None
    reason: str
    detail: str

    def __post_init__(self) -> None:
        if self.reason not in TREE_MATERIAL_GAP_REASONS:
            raise TreeMaterialError(
                f"未登记的 tree material gap 原因 {self.reason!r}"
                f"（合法值 {TREE_MATERIAL_GAP_REASONS}）")

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "node_id": self.node_id,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SpanContentQualification:
    """一个候选 span 的**内容判定**（纯判定结果，不含定位/原文）。

    ``is_material=False`` 时 ``reason`` 必须落在 ``TREE_MATERIAL_CONTENT_REASONS`` 里；
    ``selection`` 只在 ``kind == "selection_form"`` 时非空——它就是"按结构化表单规则读出
    的那张表单"：问题文本与问题出处、逐选项的字形/标签/选中状态。
    """

    kind: str
    is_material: bool
    reason: str = ""
    selection: dict | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.kind not in TREE_MATERIAL_CONTENT_KINDS:
            raise TreeMaterialError(
                f"未登记的内容形态 {self.kind!r}（合法值 {TREE_MATERIAL_CONTENT_KINDS}）")
        if not isinstance(self.is_material, bool):
            raise TreeMaterialError("SpanContentQualification.is_material 必须为 bool")
        if self.is_material:
            if self.kind not in TREE_MATERIAL_CONTENT_MATERIAL_KINDS:
                raise TreeMaterialError(
                    f"内容形态 {self.kind!r} 永远不是材料"
                    f"（只有 {TREE_MATERIAL_CONTENT_MATERIAL_KINDS} 可以）")
            if self.reason != "":
                raise TreeMaterialError("内容合格时不得带不合格原因")
        elif self.reason not in TREE_MATERIAL_CONTENT_REASONS_BY_KIND.get(self.kind, ()):
            raise TreeMaterialError(
                f"内容形态 {self.kind!r} 不得以 {self.reason!r} 落处置"
                f"（允许 {TREE_MATERIAL_CONTENT_REASONS_BY_KIND.get(self.kind, ())}）")
        if (self.kind == "selection_form") != (self.selection is not None):
            raise TreeMaterialError(
                "selection 必须**当且仅当**内容形态为 selection_form 时存在")
        if self.selection is not None:
            reason = str(self.selection.get("unresolved_reason") or "")
            resolved = bool(self.selection.get("resolved"))
            if resolved:
                if reason != "":
                    raise TreeMaterialError("表单行已读出确定状态时不得带 unresolved_reason")
            elif reason not in TREE_MATERIAL_SELECTION_UNRESOLVED_REASONS:
                raise TreeMaterialError(
                    f"未登记的表单行未决子原因 {reason!r}"
                    f"（合法值 {TREE_MATERIAL_SELECTION_UNRESOLVED_REASONS}）")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "is_material": self.is_material,
                "reason": self.reason, "detail": self.detail,
                "selection": dict(self.selection) if self.selection else None}


@dataclass(frozen=True)
class TreeMaterialContentDisposition:
    """一个**内容不合格**的候选 span 的显式登记（原文 + 定位一并留档，不静默丢弃）。

    它与 :class:`TreeMaterialGap` 是两条轴，不得互相冒充：``gap`` 说的是"这个 span 重切不出
    合法 payload / 权威不成立"，本类说的是"它切得出来，但它的内容不是业务材料（孤立标题 /
    版式碎片 / 判不出的勾选行）"。**两者都不是 Contract 缺口**——缺口另有 Contract 依据与
    检索范围，`not_used` 不是 gap。
    """

    span_id: str
    node_id: str | None
    heading_path: tuple
    page_range: tuple
    span_locator: str
    char_range: tuple
    content_fingerprint: str
    text_excerpt: str
    text_char_length: int
    kind: str
    reason: str
    detail: str
    selection: dict | None = None

    def __post_init__(self) -> None:
        if self.kind not in TREE_MATERIAL_CONTENT_KINDS:
            raise TreeMaterialError(
                f"未登记的内容形态 {self.kind!r}（合法值 {TREE_MATERIAL_CONTENT_KINDS}）")
        allowed = TREE_MATERIAL_CONTENT_REASONS_BY_KIND.get(self.kind, ())
        if self.reason not in allowed:
            raise TreeMaterialError(
                f"内容形态 {self.kind!r} 不得以 {self.reason!r} 落处置"
                f"（允许 {allowed}）")
        if len(self.text_excerpt) > CONTENT_DISPOSITION_EXCERPT_CHARS:
            raise TreeMaterialError(
                f"处置片段不得超过 {CONTENT_DISPOSITION_EXCERPT_CHARS} 字符"
                f"（留档用，不是搬运全文）")
        if (self.kind == "selection_form") != (self.selection is not None):
            raise TreeMaterialError(
                "selection 必须**当且仅当**内容形态为 selection_form 时存在")

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id,
            "node_id": self.node_id,
            "heading_path": list(self.heading_path),
            "page_range": list(self.page_range),
            "span_locator": self.span_locator,
            "char_range": list(self.char_range),
            "content_fingerprint": self.content_fingerprint,
            "text_excerpt": self.text_excerpt,
            "text_char_length": self.text_char_length,
            "kind": self.kind,
            "reason": self.reason,
            "detail": self.detail,
            "selection": dict(self.selection) if self.selection else None,
        }


@dataclass(frozen=True)
class TreeMaterialBatch:
    """一次构建的完整产物：材料 + payload 记录 + 显式 gap + 可持久化身份投影。

    ``materials`` / ``payload_records`` / ``span_ids`` / ``material_ids`` **逐位同序**。
    """

    materials: tuple
    payload_records: tuple
    gaps: tuple
    span_ids: tuple
    material_ids: tuple
    identity: dict
    #: 内容不合格的候选 span 的 typed 处置（与 ``gaps`` 是两条轴）。缺省空集，便于既有
    #: 调用方不改构造顺序。
    content_dispositions: tuple = ()

    def __post_init__(self) -> None:
        if len(self.materials) != len(self.payload_records) \
                or len(self.materials) != len(self.span_ids) \
                or len(self.materials) != len(self.material_ids):
            raise TreeMaterialError(
                "TreeMaterialBatch.materials/payload_records/span_ids/material_ids 必须等长")
        material_spans = set(self.span_ids)
        gap_spans = {gap.span_id for gap in self.gaps}
        disposition_spans = {d.span_id for d in self.content_dispositions}
        if material_spans & gap_spans or material_spans & disposition_spans \
                or gap_spans & disposition_spans:
            raise TreeMaterialError(
                "同一条候选 span 不得同时出现在 material / gap / 内容处置里"
                "（三条轴互斥，否则守恒账对不上）")
        if len(disposition_spans) != len(self.content_dispositions):
            raise TreeMaterialError("同一条 span 不得有两条内容处置（一次判定一条）")

    def material_ids_for_span(self, span_id: str) -> tuple:
        return tuple(mid for sid, mid in zip(self.span_ids, self.material_ids)
                     if sid == span_id)

    def material_by_id(self, material_id: str):
        for material in self.materials:
            if material.material_id == material_id:
                return material
        return None

    def payload_bytes_by_id(self, payload_id: str) -> bytes | None:
        for record in self.payload_records:
            if record.payload_id == payload_id:
                return record.payload_bytes
        return None

    def gap_by_span(self, span_id: str) -> tuple:
        return tuple(gap for gap in self.gaps if gap.span_id == span_id)

    def gap_reason_counts(self) -> dict:
        counts: dict = {}
        for gap in self.gaps:
            counts[gap.reason] = counts.get(gap.reason, 0) + 1
        return counts

    def content_disposition_reason_counts(self) -> dict:
        counts: dict = {}
        for disposition in self.content_dispositions:
            counts[disposition.reason] = counts.get(disposition.reason, 0) + 1
        return counts

    def content_dispositions_for_span(self, span_id: str) -> tuple:
        return tuple(d for d in self.content_dispositions if d.span_id == span_id)

    def to_dict(self) -> dict:
        """JSON 安全摘要（含材料 typed 投影；不含 payload 字节）。"""
        return {
            "envelope_kind": TREE_MATERIAL_ENVELOPE_KIND,
            "resolver_version": TS.TREE_MATERIAL_RESOLVER_VERSION,
            "material_count": len(self.materials),
            "payload_count": len(self.payload_records),
            "gap_count": len(self.gaps),
            "gap_reason_counts": self.gap_reason_counts(),
            "gaps": [gap.to_dict() for gap in self.gaps],
            "content_disposition_count": len(self.content_dispositions),
            "content_disposition_reason_counts": self.content_disposition_reason_counts(),
            "content_dispositions": [d.to_dict() for d in self.content_dispositions],
            "materials": [
                {"span_id": sid, "material_id": mid, "material": material.to_dict()}
                for sid, mid, material in
                zip(self.span_ids, self.material_ids, self.materials)
            ],
            "identity": dict(self.identity),
        }


# ---------------------------------------------------------------------------
# 3. 身份与信封
# ---------------------------------------------------------------------------

def _tight(text: str) -> str:
    """与 `document_structure.normalization.tight` 同语义（对齐度量形态）。"""
    return "".join(text.split())


def tree_material_dependency_fingerprint(*, snapshot, qualification_policy) -> str:
    """材料**创建期**依赖指纹：确定性、内容寻址、不含 run_id / 时间戳。

    它记录"这份材料是在哪一组树/布局/对齐/策略版本下算出来的"；跨 run 幂等（相同权威
    内容 → 相同指纹 → 相同 material_id），因此可以安全地参与 Pack 内容身份。
    """
    payload = {
        "envelope_kind": TREE_MATERIAL_ENVELOPE_KIND,
        "resolver_version": TS.TREE_MATERIAL_RESOLVER_VERSION,
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_schema_version": snapshot.schema_version,
        "span_builder_version": snapshot.span_builder_version,
        "synopsis_version": snapshot.synopsis_version,
        "qualification_policy_version": snapshot.qualification_policy_version,
        "alignment_schema_version": snapshot.alignment_schema_version,
        "alignment_id": snapshot.alignment_id,
        "structure_snapshot_id": snapshot.structure_snapshot_id,
        "document_id": snapshot.document_id,
        "document_version": snapshot.document_version,
        "page_layout_id": snapshot.page_layout_id,
        "outline_id": snapshot.outline_id,
        "input_fingerprint": snapshot.input_fingerprint,
        "content_fingerprint": snapshot.content_fingerprint,
        "policy_id": qualification_policy.policy_id,
        "policy_version": qualification_policy.policy_version,
        "policy_fingerprint": qualification_policy.policy_fingerprint,
        "policy_stage": qualification_policy.stage,
    }
    return sha256_canonical(payload)


def compute_tree_material_id(*, span_id: str, snapshot_id: str, evidence_id: str,
                             source_identity: str, document_version: str,
                             evidence_set_version: str, evidence_char_range: tuple,
                             span_local_char_range: tuple, payload_hash: str) -> str:
    """材料身份规范形 → ``material_id``（不含 run_id / 时间戳 / call_id）。

    与 `harness.topic_materials.compute_material_id` 同属"§9.1 规范形"家族，但显式把
    **span 身份**放进身份：同一父 Evidence 块里的两个不同 span 即使文本相同，也必须是
    两份材料（``span_id`` 与 ``payload_hash`` 同时进入），因此 span 身份不藏在 locator
    字符串里。
    """
    identity = [
        TREE_MATERIAL_ENVELOPE_KIND,
        TS.TREE_MATERIAL_RESOLVER_VERSION,
        TREE_MATERIAL_TYPE,
        span_id,
        snapshot_id,
        evidence_id,
        source_identity,
        document_version,
        evidence_set_version,
        list(evidence_char_range),
        list(span_local_char_range),
        payload_hash,
    ]
    return "mat-tm-" + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(",", ":"),
                   sort_keys=True).encode("utf-8")).hexdigest()[:32]


def _component_envelope(component) -> dict:
    return {
        "component_id": component.component_id,
        "evidence_char_range": list(component.evidence_char_range),
        "span_local_char_range": (list(component.span_local_char_range)
                                  if component.span_local_char_range is not None else None),
        "landing": component.landing,
        "admission_reason": component.admission_reason,
        "verdict": component.verdict,
        "layout_hits": [
            {"page_number": hit.page_number, "line_index": hit.line_index,
             "layout_span_index": hit.layout_span_index,
             "span_char_range": list(hit.span_char_range),
             "line_char_range": list(hit.line_char_range),
             "bbox": list(hit.bbox)}
            for hit in component.layout_hits
        ],
    }


def _coverage_envelope(coverage) -> dict | None:
    if coverage is None:
        return None
    return {
        "coverage_id": coverage.coverage_id,
        "span_local_length": coverage.span_local_length,
        "required_content_chars": coverage.required_content_chars,
        "citable_source_chars": coverage.citable_source_chars,
        "effective_citable_chars": coverage.effective_citable_chars,
        "non_citable_source_chars": coverage.non_citable_source_chars,
        "uncovered_source_chars": coverage.uncovered_source_chars,
        "normalization_only_chars": coverage.normalization_only_chars,
        "problem_count": len(tuple(coverage.problems or ())),
    }


# ---------------------------------------------------------------------------
# 4. 候选判定与重切核验
# ---------------------------------------------------------------------------

def is_selection_marker(ch: str) -> bool:
    """该字符是不是勾选/符号字形（封闭集合 + 私用区）。

    私用区（U+E000–U+F8FF）整体算符号字形：实测文档里"已勾"就是这个区的码位，而私用码位
    本身不是任何文字内容。这样判定**不需要**枚举每份文档各自用了哪个私用字形。
    """
    return ch in TREE_MATERIAL_SYMBOL_GLYPHS or 0xE000 <= ord(ch) <= 0xF8FF


def _readable_remainder(text: str) -> str:
    """去掉符号字形 / 空白 / 数字 / 标点后剩下的可读内容（空 ⇒ 纯版式碎片）。"""
    stripped = "".join("" if is_selection_marker(ch) else ch for ch in _tight(text))
    return re.sub(r"[\s0-9\W_]+", "", stripped)


def _option_label_end(text: str, start: int) -> int:
    """从 `start` 起，**选项标签**在哪一个字符处结束（形状判据，不做语义判断）。

    标签 = 到**最早**的空白 / 分句标点 / 记号字形为止的那一段。真实文档里勾选行的形状是
    `⟨选项串⟩ ⟨该行所述内容⟩`：`□适用 不适用 公司报告期控股股东未发生变更。` 的前半是选项串，
    后半是这一行**管着**的那句话。旧读法把后半当成"最后一个选项的标签"，于是一条长度超过
    ``SELECTION_OPTION_MAX_CHARS`` 就被整行判成"不像表单行"、退化成普通正文——那正是
    「勾选行看起来是叙述正文」的成因。
    """
    for index in range(start, len(text)):
        ch = text[index]
        if ch.isspace() or ch in _CLAUSE_PUNCT or is_selection_marker(ch):
            return index
    return len(text)


def _single_statement_shape(tail: str) -> bool:
    """选项串之后那段内容是否**只承载一句话**（形状判据，不做语义判断）。

    真实文档里勾选行的形状是 `⟨选项串⟩ ⟨这一行所述内容⟩`——选项串之后那句话**属于这一行**。
    但同一形状也出现在**混合片段**里：勾选行后面接着一整段披露正文。两者必须分开：前者的
    所述内容由这一行管着、不得当正文用；后者的正文是**独立材料**，不能被一张勾选行吞掉资格。

    判据只看那段内容自己的形状，三条都是可复算的：

    * 含句末标点（``_SENTENCE_END``）时：**恰好一个**，且在**末尾**——多于一个或不在末尾
      就是两句话以上，那是正文；
    * 不含句末标点时：紧文本不超过 ``SELECTION_ROW_MAX_CHARS``——没有句读的长串是段落/小标题，
      不是"这一行管着的那句话"（同一条长度判据本模块已用于"超过它就不是一张选项行"）；
    * 句读**总数**（句末标点之间的分句标点 ``，、：``）不超过 ``SELECTION_TRAILING_CLAUSE_MAX``：
      只数句读不够——一整段披露正文可以通篇用逗号连成**一个**句号的长句（实测 176 / 189 字的
      混合片段正是这个形状，句末标点只有一个）。"这一行管着的那句话"是**一句话**，不是一整个
      用逗号串起来的段落：超过上限即判为正文，整段按 ``text`` 处理。
    """
    tight = _tight(tail)
    if not tight:
        return False
    ends = [i for i, ch in enumerate(tight) if ch in _SENTENCE_END]
    if ends:
        if len(ends) != 1 or ends[0] != len(tight) - 1:
            return False
        body = tight[:-1]
    else:
        if len(tight) > SELECTION_ROW_MAX_CHARS:
            return False
        body = tight
    return sum(1 for ch in body if ch in _CLAUSE_PUNCT) <= SELECTION_TRAILING_CLAUSE_MAX


def _selection_reading(text: str) -> dict | None:
    """把一段文本按符号字形切成**选项行**；形状不像选项行则 ``None``（按普通正文处理）。

    形状条件（全部只看形状，无公司 / 文档 / 页码 / 固定词规则）：

    - 文本里有符号字形，且**记号落在正文中间**时不算选项行：第一个记号之前的前缀不含分句
      标点、且不超过 ``SELECTION_ROW_MAX_CHARS``；
    - 记号之间与最后一个记号之后的**标签**都不含分句标点、且不超过
      ``SELECTION_OPTION_MAX_CHARS``；
    - **选项串**（前缀 + 各标签，到最后一个标签结束为止）不超过 ``SELECTION_ROW_MAX_CHARS``：
      长度约束落在选项串上，而不是整段文本上——选项串之后的内容是这一行的宾语，不是选项；
    - 选项串之后若还有内容，它必须**只承载一句话**（:func:`_single_statement_shape`）；
      否则整段是「勾选行 + 正文」的混合片段，按普通正文处理——那段正文是独立材料，
      不得因为挨着勾选框就被降格为「只读适用性」。

    通过后，选项串之后那段内容逐字记为 ``trailing_content``（这一行管着的那句话 / 紧随其后
    的小标题）：随行留档、按 ``TREE_MATERIAL_SELECTION_EXCLUSIONS`` 排除，**不**当作选项标签。

    少于两个记号时不引入尾随内容读法（逐字沿用旧的整段约束）：形状尚未成立时，"末尾那段
    是不是尾随内容"无从谈起，宁可按正文处理。

    **所问事项（``tmr-3`` 定点业务纠正①）**：``question`` **只**取行内前缀。行内没写主语时
    ``question`` / ``question_scope`` 都留空，这一行读出的是「某个**行内未具名**的事项
    适用 / 不适用」——**不**拿所在节点标题顶上去。理由是可证的归属错误，不是措辞洁癖：
    ``tmr-2`` 的回指会把**子项**的勾选状态锚到整个**栏目**上。实测 NDSD_2025 第 28 页那行
    ``□适用 √不适用`` 所在节点是 ``（8） 主要销售客户和主要供应商情况``——那是一个栏目，
    原件的勾选框属于其下「主要客户其他情况说明」「主要供应商其他情况说明」两个子项；
    同一页紧跟着披露的集中度表（前五名合计销售额 / 占比）在原件里真实存在，与那行勾选框无关。
    回指把「集中度这一栏不适用」写成了可由表单行证明的结论，而同一材料既没有该栏的合格数字、
    也没有任何一句话支持这个否定。行内前缀是**唯一**有据的主语来源；节点标题是 locator 上的
    导航坐标，不是主语。空所问事项是 fail-closed（``pack_writer._scope_confined`` 对空
    ``asked_item`` 一律返回 ``False``，该行授权不了任何支撑），行本身仍是材料：原文、locator、
    content 指纹、选项与选中状态全部留档。
    """
    if not any(is_selection_marker(ch) for ch in text):
        return None
    markers = [(i, ch) for i, ch in enumerate(text) if is_selection_marker(ch)]
    if len(markers) < 2:
        if len(text) > SELECTION_ROW_MAX_CHARS or any(ch in text for ch in _CLAUSE_PUNCT):
            return None
    prefix_raw = text[:markers[0][0]]
    if any(ch in prefix_raw for ch in _CLAUSE_PUNCT) \
            or len(_tight(prefix_raw)) > SELECTION_ROW_MAX_CHARS:
        return None
    options = []
    trailing = ""
    run_end = markers[0][0]
    for index, (pos, ch) in enumerate(markers):
        if index + 1 < len(markers):
            raw = text[pos + 1:markers[index + 1][0]]
            end = markers[index + 1][0]
        else:
            end = _option_label_end(text, pos + 1)
            raw = text[pos + 1:end]
            tail = text[end:].strip()
            # 纯标点的"尾随"不是所述内容（`□适用 不适用，` 这类只是行尾标点）。
            if _readable_remainder(tail):
                if not _single_statement_shape(tail):
                    return None
                trailing = tail
        label = raw.strip()
        if any(ch in raw for ch in _CLAUSE_PUNCT) \
                or len(_tight(label)) > SELECTION_OPTION_MAX_CHARS:
            return None
        run_end = end
        options.append({"marker": ch,
                        "marker_state": "hollow" if ch in TREE_MATERIAL_HOLLOW_MARKERS
                                        else "marked",
                        "label": label})
    if run_end > SELECTION_ROW_MAX_CHARS:
        return None
    prefix = prefix_raw.strip()
    # 所问事项**只**取行内前缀（`tmr-3`）：行内没写主语时这一列留空，不回指节点标题。
    question, question_scope = (prefix, "in_span") if prefix else ("", "")
    selected = [o for o in options if o["marker_state"] == "marked"]
    resolved = False
    unresolved_reason = ""
    if len(options) < 2:
        unresolved_reason = "options_not_segmented"
    elif len(selected) != 1:
        unresolved_reason = "state_not_determinable"
    else:
        resolved = True
    return {
        "resolved": resolved,
        "unresolved_reason": unresolved_reason,
        "question": question,
        "question_scope": question_scope,
        "options": options,
        "selected_labels": [o["label"] for o in selected],
        "trailing_content": trailing,
    }


def selection_form_columns(*, selection: Mapping, locator: Mapping | None = None,
                           source_identity: str = "") -> dict:
    """可判定表单行 → **六列**派生读法（所问事项 / 选项 / 选中状态 / 所在节点 / 来源 /
    尾随内容 ``trailing_content``）。

    这是**派生说明**，不是原文：原文与精确落点仍在 payload ``content.text`` 与 locator 里，
    本函数只把同一段文本按「问了什么、有哪些选项、当前选中什么、在哪个节点、出自哪份来源、
    选项串之后那句话是什么」逐列写出来，供材料读回与 Writer 输入面**分流**使用。

    第六列是 ``tmr-2`` 新增的：真实勾选行的形状是 ``⟨选项串⟩ ⟨这一行管着的那句话⟩``，这句话
    过去被当成"最后一个选项的标签"，于是整行随长度约束退化成普通正文——正文身份**看起来**能
    支撑成立日期或业务构成，实则不能。它现在逐字留档，但**不是**可承重的列：
    ``TREE_MATERIAL_SELECTION_EXCLUSIONS`` 里的 ``no_support_from_trailing_content`` 明文禁止
    拿它作支撑。列数从五到六是**读法变了**（不是多了个字段），因此
    ``TREE_MATERIAL_RESOLVER_VERSION`` 同步升到 ``tmr-2``。

    第一列（所问事项）在 ``tmr-3`` 改了**取值来源**：只取行内前缀，行内没写主语时
    ``asked_item`` / ``asked_item_scope`` **都为空串**，且**不再**用所在节点标题顶替——
    节点标题可能是这一行的**栏目**，拿它当所问事项就是把子项状态锚到整个栏目上（读数与反例
    见 :func:`_selection_reading`）。因此本列**允许为空**：空的 ``asked_item`` 表示"这一行
    指向不了任何栏目"，不是"这一行无效"。与之配套，``_scope_confined`` 对空所问事项一律
    拒绝授权（fail-closed）。「所在节点」这一列照旧保留，它现在是纯粹的**导航坐标**。

    「所在节点」取 locator 上**原件记的** `section_path`（不重排、不折算、不按分隔符再切）；
    「来源」取 material 的权威身份与同一份 locator。「允许用途/排除」是范围判据，不是措辞装饰：
    它逐条写明这一列读**不出**什么（不得推否定性结论、不得替宽栏目作证、不得当叙述正文）。

    ``resolved=False`` 时返回 ``None``：读不定的表单行不产出结构化事实（它连材料都不是）。
    """
    if not isinstance(selection, Mapping) or not selection.get("resolved"):
        return None
    loc = dict(locator or {})
    options = [{"marker": str(o.get("marker", "")),
                "marker_state": str(o.get("marker_state", "")),
                "label": str(o.get("label", "")),
                "selected": str(o.get("marker_state", "")) == "marked"}
               for o in tuple(selection.get("options") or ())]
    return {
        "asked_item": str(selection.get("question", "") or ""),
        "asked_item_scope": str(selection.get("question_scope", "") or ""),
        "options": options,
        "selected_labels": [str(x) for x in tuple(selection.get("selected_labels") or ())],
        # 选项串之后那段内容（这一行"管着"的那句话，或紧随其后的小标题）：逐字留档，
        # **不**进 options、**不**可用作支撑（``no_support_from_trailing_content``）。
        "trailing_content": str(selection.get("trailing_content", "") or ""),
        # 所在节点：原件 locator 记下的 `section_path`（外层→内层），逐字保留。
        "node": {"section_path": str(loc.get("section_path", "") or "")},
        # 来源：原件身份与精确落点（不折算、不重写）。
        "source": {
            "source_identity": str(source_identity or ""),
            "document_id": str(loc.get("document_id", "") or ""),
            "document_version": str(loc.get("document_version", "") or ""),
            "page": loc.get("page"),
            "block_range": list(loc.get("block_range") or []) or None,
            "offset": loc.get("offset"),
        },
        "permitted_use": TREE_MATERIAL_SELECTION_PERMITTED_USE,
        "exclusions": list(TREE_MATERIAL_SELECTION_EXCLUSIONS),
    }


def classify_span_content(span, *, node_title: str | None = None) -> SpanContentQualification:
    """候选 span 的**内容判定**（§二 2.3）。纯函数，只看文本 / 节点标题。

    顺序即优先级：勾选表单行 → 孤立标题 → 纯版式碎片 → 正文。判据全部是可复算的形状规则
    （无公司名 / 文件名 / 页码 / 固定年份），因此换一份文档结论可复现：

    - 文本与所在节点标题逐字相同 ⇒ 它是导航标签，不是正文（``isolated_heading``）；
    - 编号标题形（`1）…`）且无句末标点且不超 ``NUMBERED_LABEL_MAX_CHARS`` ⇒ 标题残段；
    - 没有分句标点、没有数字、不超 ``BARE_LABEL_MAX_CHARS`` 的短标签 ⇒ 表头/表题残段
      （正式文本里的句子有句读，没有句读的行是标签）；
    - 去掉符号字形后没有可读内容 ⇒ 纯版式碎片。
    """
    text = getattr(span, "normalized_text", "") or ""
    tight = _tight(text)
    title_tight = _tight(node_title or "")
    if tight == "":
        return SpanContentQualification("layout_fragment", False,
                                        "layout_fragment_no_readable_content",
                                        detail="空文本（没有可读内容）")
    if _readable_remainder(text) == "":
        return SpanContentQualification("layout_fragment", False,
                                        "layout_fragment_no_readable_content",
                                        detail="去掉符号字形/数字/标点后没有可读内容")
    reading = _selection_reading(text)
    if reading is not None:
        if reading["resolved"]:
            detail = ("勾选表单行：行内所问事项与选中状态均已读定"
                      if reading["question"] else
                      "勾选表单行：行内未写所问事项，这一行不指向任何栏目；"
                      "选项与选中状态已读定（原文与来源留档，所问事项一列留空）")
            return SpanContentQualification("selection_form", True, "", reading, detail)
        return SpanContentQualification(
            "selection_form", False, "selection_form_unresolved", reading,
            f"勾选表单行读不出确定状态（{reading['unresolved_reason']}）")
    if title_tight and tight == title_tight:
        return SpanContentQualification(
            "isolated_heading", False, "isolated_heading_matches_node_title",
            detail=f"与所在节点标题逐字相同（{title_tight[:40]!r}）")
    if _NUMBERED_LABEL.match(tight) and not any(ch in text for ch in _SENTENCE_END) \
            and len(tight) <= NUMBERED_LABEL_MAX_CHARS:
        return SpanContentQualification(
            "isolated_heading", False, "isolated_heading_numbered_label",
            detail="编号标题形且无句末标点")
    if not any(is_selection_marker(ch) for ch in text) \
            and not any(ch in text for ch in _CLAUSE_PUNCT) \
            and not re.search(r"\d", text) \
            and len(tight) <= BARE_LABEL_MAX_CHARS and _CJK.search(tight):
        return SpanContentQualification(
            "layout_fragment", False, "layout_fragment_bare_label",
            detail="没有句读的短标签（表头 / 表题残段）")
    return SpanContentQualification("text", True, "", None, "")


def is_tree_material_candidate(span) -> bool:
    """该 span 是否属于"可能成为正式材料的正文 span"人口。

    与 `document_structure.span_verifier` 的正式正文材料判据同源（角色 / 归属 / 算法
    版本），但这里只做**人口**筛选：`unassigned` / fallback / 跨标题 / 非当前算法的 span
    本来就不是正式正文材料，不登记为 gap（其处置已在快照自身的 disposition 里）。
    """
    if getattr(span, "role", None) != "body":
        return False
    if getattr(span, "node_id", None) is None:
        return False
    if getattr(span, "unassigned_reason", None) is not None:
        return False
    if getattr(span, "is_fallback", None) is not False:
        return False
    if getattr(span, "is_cross_heading", None) is not False:
        return False
    if getattr(span, "span_builder_version", None) != V.TS4_BODY_SPAN_BUILDER_VERSION:
        return False
    return isinstance(getattr(span, "normalized_text", None), str)


def _parent_binding_ok(block, binding: CurrentEvidenceBinding) -> bool:
    """父块身份必须与显式绑定逐项一致（跨公司 / 文档 / 版本 / set 一律拒）。"""
    return (block.company_id == binding.company_id
            and block.document_id == binding.document_id
            and block.document_version == binding.document_version
            and block.evidence_set_version == binding.evidence_set_version)


@dataclass(frozen=True)
class _MaterialUnit:
    """一个 span 里**恰好属于同一个 Evidence 块**的那一段（逐块片段材料的构建单元）。

    ``local_range`` 是 span 本地坐标（`normalized_text` 的半开区间），``evidence_range`` 是
    父块**紧文本**坐标；两者都已被 :func:`_recut_problem` 的闭式关系核验过。
    """

    block: object
    components: tuple
    local_range: tuple
    evidence_range: tuple


def _recut_problem(span, admitted, blocks_by_id) -> tuple:
    """独立重切核验：从 Evidence 逐字符重算这个 span。返回 ``(problem, units)``。

    闭式关系（与 `span_builder._RunProjector.to_span_local` 的"真实切片不等"同族）：

    1. **逐 component**：``tight(span 本地区间) == tight(它自己的父块)[精确 Evidence 区间]``。
       位置感知比较强于全局拼接比较：它同时验证了"哪一段落在哪个块的哪个位置"。
    2. **全局铺满**：全部 component 的本地区间恰好铺满 ``[0, len(span.normalized_text))``，
       相邻区间之间最多一个由行连接/空白折叠产生的空格（§18.5.3-2）。
    3. **逐块**：把同一父块的 component 收成一段；**块内本地区间必须连续**（否则块序交错，
       逐块片段表达不了，落 ``multi_evidence_block_span``），且该段整体再满足一次
       ``tight(span 本地区间) == tight(该块)[该段精确 Evidence 区间]``。第 3 条是第 1 条的
       加强形式：它另外证明了"跨段之间在块里只剩空白"，所以逐块片段是**重切出来的精确片段**，
       不是拼接猜测。
    """
    span_text = span.normalized_text
    tight_cache: dict = {}
    local_ranges = []
    rows = []
    for component in admitted:
        block_id = component.evidence_block_id
        block = blocks_by_id.get(block_id)
        if block is None:
            return "evidence_block_unavailable", ()
        if block_id not in tight_cache:
            tight_cache[block_id] = _tight(block.text)
        tight_block = tight_cache[block_id]
        local = component.span_local_char_range
        if local is None:
            return "component_span_local_missing", ()
        s0, s1 = int(local[0]), int(local[1])
        e0, e1 = int(component.evidence_char_range[0]), int(component.evidence_char_range[1])
        if not (0 <= s0 < s1 <= len(span_text)):
            return "evidence_range_recheck_failed", ()
        if not (0 <= e0 < e1 <= len(tight_block)):
            return "evidence_range_recheck_failed", ()
        if _tight(span_text[s0:s1]) != tight_block[e0:e1]:
            return "evidence_range_recheck_failed", ()
        local_ranges.append((s0, s1))
        rows.append((block_id, component, s0, s1, e0, e1))
    local_ranges.sort()
    if local_ranges[0][0] != 0 or local_ranges[-1][1] != len(span_text):
        return "span_local_not_tiling", ()
    for i in range(len(local_ranges) - 1):
        if local_ranges[i][1] + 1 < local_ranges[i + 1][0]:
            return "span_local_not_tiling", ()

    order: list = []
    grouped: dict = {}
    for block_id, component, s0, s1, e0, e1 in rows:
        if block_id not in grouped:
            grouped[block_id] = []
            order.append(block_id)
        grouped[block_id].append((component, s0, s1, e0, e1))
    units = []
    for block_id in sorted(order, key=lambda b: min(r[1] for r in grouped[b])):
        items = sorted(grouped[block_id], key=lambda r: (r[1], r[2]))
        previous_end = None
        for _, s0, s1, _, _ in items:
            if previous_end is not None and previous_end + 1 < s0:
                # 块序交错：同一块的两段之间还夹着别的块 → 无法用"每块一段"表达。
                return "multi_evidence_block_span", ()
            previous_end = s1
        lo = items[0][1]
        hi = items[-1][2]
        e_lo = min(r[3] for r in items)
        e_hi = max(r[4] for r in items)
        if _tight(span_text[lo:hi]) != tight_cache[block_id][e_lo:e_hi]:
            return "evidence_range_recheck_failed", ()
        units.append(_MaterialUnit(
            block=blocks_by_id[block_id],
            components=tuple(r[0] for r in items),
            local_range=(lo, hi),
            evidence_range=(e_lo, e_hi)))
    return None, tuple(units)


# ---------------------------------------------------------------------------
# 5. 构建
# ---------------------------------------------------------------------------

def _build_one(span, unit, coverage, *, binding, build_context,
               qualification, split=None) -> tuple:
    """成功 → ``(material, payload_record)``；权威不成立 → ``(None, reason)``。

    ``unit`` 是**一个父 Evidence 块**对应的那一段（单块 span 时就是整段）。``split`` 非空
    时把拆分段身份写进信封（第几段 / 共几段 / 全文长度），**只有真正拆分的材料**才带这个键，
    所以单块材料的 payload 字节与历史逐字节相同。
    """
    block = unit.block
    admitted = unit.components
    slice_start, slice_end = unit.local_range
    text = span.normalized_text[slice_start:slice_end]
    tight_block = _tight(block.text)
    ev_start, ev_end = unit.evidence_range
    whole_block = (ev_start == 0 and ev_end == len(tight_block))
    source_identity = f"evidence:{block.evidence_block_id}"

    placeholder = TS.EvidenceAuthorityAssessment(
        evidence_id=block.evidence_block_id,
        document_id=block.document_id,
        document_version=block.document_version,
        company_id=block.company_id,
        is_current_document=binding.is_current,
        is_current_set=binding.is_current,
        page=block.page_number,
        block_range=(block.block_index, block.block_index),
        fetched_inspected_nonempty=bool(block.text),
        content_hash=block.content_hash,
        verdict="rejected",  # 占位；下面由 recompute_authority_verdict 确定性重算
        reason="",
        validator_version=binding.validator_version,
    )
    verdict = TS.recompute_authority_verdict(placeholder)
    if verdict != "authoritative":
        return None, "authority_not_authoritative"
    authority = TS.EvidenceAuthorityAssessment(
        evidence_id=placeholder.evidence_id,
        document_id=placeholder.document_id,
        document_version=placeholder.document_version,
        company_id=placeholder.company_id,
        is_current_document=placeholder.is_current_document,
        is_current_set=placeholder.is_current_set,
        page=placeholder.page,
        block_range=placeholder.block_range,
        fetched_inspected_nonempty=placeholder.fetched_inspected_nonempty,
        content_hash=placeholder.content_hash,
        verdict=verdict,
        reason="" if binding.is_current else "非 current document/set",
        validator_version=binding.validator_version,
    )

    node = build_context["outline_by_id"].get(span.node_id)
    heading_path = list(getattr(node, "structural_path", ()) or ())
    locator = TS.EvidenceLocator(
        document_id=block.document_id,
        document_version=block.document_version,
        section_path=" / ".join(heading_path),
        page=block.page_number,
        block_range=(block.block_index, block.block_index),
        # 片段语义：content.text 是**块内片段**（非完整块正文），offset 记录该片段在
        # 归一块坐标下的**精确结束界**；父块的完整 source_content_hash 因此保持不变。
        offset=ev_end,
    )
    envelope = {
        "material_payload_version": MATERIAL_PAYLOAD_VERSION,
        "envelope_kind": TREE_MATERIAL_ENVELOPE_KIND,
        "object_type": TREE_MATERIAL_TYPE,
        "authority_identity": source_identity,
        "document_identity": {
            "company_id": block.company_id,
            "document_id": block.document_id,
            "document_version": block.document_version,
            "evidence_set_version": block.evidence_set_version,
        },
        "locator": locator.to_dict(),
        "evidence_id": block.evidence_block_id,
        "source_content_hash": block.content_hash,
        "content": {
            "text": text,
            "structured_payload": block.structured_payload if whole_block else None,
            "evidence_type": block.evidence_type,
        },
        # §二 2.3：这份材料的内容形态（以及勾选表单行读出的问题/选项/选中状态）进内容哈希——
        # 形态判定变了，材料身份就变，不会出现"同一份 payload 两种读法"。
        "content_qualification": {
            "kind": qualification.kind,
            "selection": dict(qualification.selection) if qualification.selection else None,
        },
        "created_dependency_fingerprint": build_context["dependency_fingerprint"],
        "tree_material": {
            "resolver_version": TS.TREE_MATERIAL_RESOLVER_VERSION,
            "current_evidence": binding.to_dict(),
            "snapshot": dict(build_context["snapshot"]),
            "policy": dict(build_context["policy"]),
            "span": {
                "span_id": span.span_id,
                "span_locator": span.span_locator,
                "node_id": span.node_id,
                "heading_path": heading_path,
                "role": span.role,
                "page_range": list(span.page_range),
                "char_range": list(span.char_range),
                "confidence": span.confidence,
                "content_fingerprint": span.content_fingerprint,
                "alignment_ids": list(span.alignment_ids),
                "layout_line_refs": [list(ref) for ref in span.layout_line_refs],
            },
            "parent_evidence": {
                "evidence_block_id": block.evidence_block_id,
                "page_number": block.page_number,
                "block_index": block.block_index,
                "evidence_type": block.evidence_type,
                "structured_payload_present": block.structured_payload is not None,
                "block_char_length": len(tight_block),
            },
            # 精确 Evidence 区间（归一块坐标）与 span 本地区间：§5.6 要求的两项。
            # 跨块 span 拆成逐块片段时，这两个区间都是**本段的**精确区间（不是整个 span 的）。
            "evidence_char_range": [ev_start, ev_end],
            "span_local_char_range": [slice_start, slice_end],
            "whole_block": whole_block,
            "components": [_component_envelope(c) for c in admitted],
            "coverage": _coverage_envelope(coverage),
            "provenance": {
                "is_fallback": span.is_fallback,
                "fallback_derivation": span.fallback_derivation,
                "is_cross_heading": span.is_cross_heading,
            },
        },
    }
    # 只有**真正拆过**的材料才带这个键：单块材料的信封因此与历史逐字节相同（哈希不动），
    # 也不会把"整段就是全部"和"这一段是全文的一段"混成同一种读法。
    if split is not None:
        envelope["span_split"] = dict(split)
    payload_bytes = json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
    payload_hash = hashlib.sha256(payload_bytes).hexdigest()

    payload_ref = TS.MaterialPayloadRef(
        object_type=TREE_MATERIAL_TYPE,
        authority_identity=source_identity,
        version=TS.TREE_MATERIAL_RESOLVER_VERSION,
        content_hash=payload_hash,
        locator=locator,
        created_dependency_fingerprint=build_context["dependency_fingerprint"],
    )
    material = TS.ResearchMaterial(
        material_id=compute_tree_material_id(
            span_id=span.span_id,
            snapshot_id=build_context["snapshot"]["snapshot_id"],
            evidence_id=block.evidence_block_id,
            source_identity=source_identity,
            document_version=block.document_version,
            evidence_set_version=block.evidence_set_version,
            evidence_char_range=(ev_start, ev_end),
            span_local_char_range=(slice_start, slice_end),
            payload_hash=payload_hash),
        material_type=TREE_MATERIAL_TYPE,
        source_identity=source_identity,
        locator=locator,
        payload_ref=payload_ref,
        content_hash=payload_hash,
        authority_assessment=authority,
    )
    record = MaterialPayloadRecord(
        payload_id=payload_hash,
        object_type=TREE_MATERIAL_TYPE,
        authority_identity=source_identity,
        version=TS.TREE_MATERIAL_RESOLVER_VERSION,
        locator_json=json.dumps(locator.to_dict(), ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")),
        source_content_hash=block.content_hash,
        payload_hash=payload_hash,
        payload_bytes=payload_bytes,
        created_dependency_fingerprint=build_context["dependency_fingerprint"],
    )
    return (material, record), None


def _build_context(snapshot, policy, current_evidence, outline_by_id) -> dict:
    return {
        "dependency_fingerprint": tree_material_dependency_fingerprint(
            snapshot=snapshot, qualification_policy=policy),
        "snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "schema_version": snapshot.schema_version,
            "span_builder_version": snapshot.span_builder_version,
            "synopsis_version": snapshot.synopsis_version,
            "qualification_policy_version": snapshot.qualification_policy_version,
            "alignment_schema_version": snapshot.alignment_schema_version,
            "alignment_id": snapshot.alignment_id,
            "structure_snapshot_id": snapshot.structure_snapshot_id,
            "outline_id": snapshot.outline_id,
            "page_layout_id": snapshot.page_layout_id,
            "document_id": snapshot.document_id,
            "document_version": snapshot.document_version,
            "input_fingerprint": snapshot.input_fingerprint,
            "content_fingerprint": snapshot.content_fingerprint,
        },
        "policy": {
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "policy_schema_version": policy.schema_version,
            "policy_fingerprint": policy.policy_fingerprint,
            "stage": policy.stage,
        },
        "current_evidence": current_evidence.to_dict(),
        "outline_by_id": outline_by_id,
    }


def build_tree_materials(*, snapshot, blocks, current_evidence,
                         qualification_policy=None, outline=None) -> TreeMaterialBatch:
    """把一个 verified `SpanBuildSnapshot` 投影为材料批次（纯函数，不写库）。

    - ``blocks``：该文档的 Evidence 块（`document_structure.aligner.EvidenceBlockInput`
      或同形对象），其 ``evidence_block_id`` / ``content_hash`` 已在构造期正式重算；
    - ``current_evidence``：current Evidence 绑定（live 路径经
      :func:`evidence_binding_from_snapshot` 派生）；
    - ``qualification_policy`` 缺省取 snapshot 自载策略；
    - ``outline`` 可选，只用于给材料补 ``heading_path`` 导航溯源。
    """
    if not isinstance(current_evidence, CurrentEvidenceBinding):
        raise TreeMaterialError(
            f"build_tree_materials 需要 CurrentEvidenceBinding，得到 "
            f"{type(current_evidence).__name__}")
    spans = tuple(getattr(snapshot, "spans", ()))
    if not spans:
        raise TreeMaterialError("snapshot.spans 为空：不接受空树快照构建材料")
    policy = qualification_policy if qualification_policy is not None \
        else getattr(snapshot, "qualification_policy", None)
    if policy is None:
        raise TreeMaterialError("缺少 qualification policy（snapshot 未自载且未显式提供）")

    outline_by_id = {getattr(n, "node_id", None): n
                     for n in tuple(getattr(outline, "nodes", ()) or ())}
    context = _build_context(snapshot, policy, current_evidence, outline_by_id)

    blocks_by_id: dict = {}
    for block in blocks:
        bid = getattr(block, "evidence_block_id", None)
        if not isinstance(bid, str) or bid == "":
            raise TreeMaterialError("Evidence 块必须携带非空 evidence_block_id")
        if bid in blocks_by_id:
            raise TreeMaterialError(f"重复的 Evidence 块身份 {bid!r}（fail-closed）")
        blocks_by_id[bid] = block
    components_by_span: dict = {}
    for component in tuple(getattr(snapshot, "components", ())):
        sid = getattr(component, "span_id", None)
        if sid is not None:
            components_by_span.setdefault(sid, []).append(component)
    coverage_by_span = {getattr(c, "span_id", None): c
                        for c in tuple(getattr(snapshot, "coverages", ()))}

    materials: list = []
    records: list = []
    gaps: list = []
    dispositions: list = []
    span_ids: list = []
    material_ids: list = []
    candidates = 0
    for span in spans:
        if not is_tree_material_candidate(span):
            continue
        candidates += 1
        span_components = tuple(components_by_span.get(span.span_id, ()))
        admitted = tuple(c for c in span_components if getattr(c, "admitted", False))
        if not admitted:
            gaps.append(TreeMaterialGap(span.span_id, span.node_id,
                                        "no_admitted_component",
                                        "该 span 没有任何已准入 component"))
            continue
        if len(admitted) != len(span_components):
            gaps.append(TreeMaterialGap(
                span.span_id, span.node_id, "non_admitted_component_present",
                f"{len(span_components) - len(admitted)}/{len(span_components)} "
                f"个 component 未准入"))
            continue
        # 跨块 span 拆成**逐块片段**（R2：一份 material = 一个真实 EvidenceBlock）。重切核验
        # 连同拆分形状一起算出来：一段对不上，整个 span 都不出材料（同一 span 的三条去路互斥，
        # 不允许"半段成材料、半段落 gap"）。
        problem, units = _recut_problem(span, admitted, blocks_by_id)
        if problem is not None:
            if problem == "evidence_block_unavailable":
                missing = sorted({c.evidence_block_id for c in admitted}
                                 - set(blocks_by_id))
                detail = f"父块 {missing} 不在本次 Evidence 集合中"
            else:
                detail = ("独立重切核验未通过（逐 component 字符切片、本地区间铺满性或"
                          "逐块片段连续性不符）")
            gaps.append(TreeMaterialGap(span.span_id, span.node_id, problem, detail))
            continue
        unavailable = [unit.block.evidence_block_id for unit in units
                       if not _parent_binding_ok(unit.block, current_evidence)]
        if unavailable:
            gaps.append(TreeMaterialGap(
                span.span_id, span.node_id, "evidence_block_unavailable",
                f"父块 {unavailable} 的 company/document/version/set 与显式 current "
                f"Evidence 绑定不一致"))
            continue
        # §二 2.3：结构过关之后才问"它的内容是不是业务材料"。孤立标题 / 纯版式碎片 /
        # 读不出状态的勾选行不产出材料，落一条 typed 处置（原文片段 + locator + 内容指纹
        # 一并留档）：它**既不是 gap，也不静默丢弃**。
        node = outline_by_id.get(span.node_id)
        qualification = classify_span_content(
            span, node_title=getattr(node, "title", None) if node is not None else None)
        if not qualification.is_material:
            dispositions.append(TreeMaterialContentDisposition(
                span_id=span.span_id,
                node_id=span.node_id,
                heading_path=tuple(getattr(node, "structural_path", ()) or ()),
                page_range=tuple(span.page_range),
                span_locator=span.span_locator,
                char_range=tuple(span.char_range),
                content_fingerprint=span.content_fingerprint,
                text_excerpt=span.normalized_text[:CONTENT_DISPOSITION_EXCERPT_CHARS],
                text_char_length=len(span.normalized_text),
                kind=qualification.kind,
                reason=qualification.reason,
                detail=qualification.detail,
                selection=qualification.selection))
            continue
        multi = len(units) > 1
        span_length = len(span.normalized_text)
        staged: list = []
        failure = None
        for index, unit in enumerate(units):
            # 拆分段身份：全文长度 + 本段在 span 本地区的精确区间 + 第几段 / 共几段。
            # 有了这三项，"两段的区间恰好并成 [0, 全文长度)"（或"这一段就是全文"）是可读回的。
            split = None
            if multi:
                split = {
                    "piece_index": index + 1,
                    "piece_count": len(units),
                    "span_id": span.span_id,
                    "span_char_length": span_length,
                    "span_local_char_range": list(unit.local_range),
                    "parent_block_count": len(units),
                }
            built, reason = _build_one(
                span, unit, coverage_by_span.get(span.span_id), binding=current_evidence,
                build_context=context, qualification=qualification, split=split)
            if built is None:
                failure = reason
                break
            staged.append(built)
        if failure is not None:
            gaps.append(TreeMaterialGap(span.span_id, span.node_id, failure,
                                        f"权威重算为 {failure}（不采信自填 verdict）"))
            continue
        for material, record in staged:
            materials.append(material)
            records.append(record)
            span_ids.append(span.span_id)
            material_ids.append(material.material_id)

    repeats: dict = {}
    for span_id in span_ids:
        repeats[span_id] = repeats.get(span_id, 0) + 1
    identity = {
        "envelope_kind": TREE_MATERIAL_ENVELOPE_KIND,
        "resolver_version": TS.TREE_MATERIAL_RESOLVER_VERSION,
        "dependency_fingerprint": context["dependency_fingerprint"],
        "snapshot": dict(context["snapshot"]),
        "policy": dict(context["policy"]),
        "current_evidence": context["current_evidence"],
        "candidate_span_count": candidates,
        # 材料的**条数**与**产出材料的 span 数**是两件事：跨块 span 一份 span 出多份逐块片段
        # 材料。守恒账按 span 算（三条轴互斥、合计等于候选数），所以这里必须分开登记，否则
        # "候选 = 材料 + gap + 处置"这条账在跨块文档上会假性不闭合。
        "material_span_count": len(set(span_ids)),
        "material_count": len(materials),
        "multi_block_span_count": sum(1 for count in repeats.values() if count > 1),
        "multi_block_material_count": sum(count for count in repeats.values() if count > 1),
        "gap_count": len(gaps),
        # 三条轴互斥且**合计等于候选数**：材料 / 结构 gap / 内容处置。少了这一项，
        # "被处置掉的候选"就会被读成"从没进过人口"，账就对不平。
        "content_disposition_count": len(dispositions),
        "content_disposition_reason_counts": dict(sorted({
            reason: sum(1 for d in dispositions if d.reason == reason)
            for reason in {d.reason for d in dispositions}}.items())),
        # 节点标题是"孤立标题"判据的一部分：没有标题树时该判据不可用，必须如实登记，
        # 否则"本轮没有孤立标题"会被误读成"文档里没有"。
        "outline_available": bool(outline_by_id),
    }
    return TreeMaterialBatch(
        materials=tuple(materials),
        payload_records=tuple(records),
        gaps=tuple(gaps),
        span_ids=tuple(span_ids),
        material_ids=tuple(material_ids),
        identity=identity,
        content_dispositions=tuple(dispositions),
    )


# ---------------------------------------------------------------------------
# 6. 独立重切解析器（§5.6：不得只信 payload 自报）
# ---------------------------------------------------------------------------

class TreeMaterialPayloadResolver:
    """`harness.topic_schema.PayloadResolver` 的 tree material 实现。

    **它从不读已落盘的 payload。** 每次构造都在内存里从 Evidence + verified snapshot
    重切一遍（:func:`build_tree_materials`），再按**重算出来的** payload 哈希建索引：

    - ref 指向一个重算不出来的哈希（被改写、被拼接、或从未由本模块产出）→ ``None``
      （dangling；上层 fail-closed，不把伪造伪装成"未找到"）；
    - ref 命中 → 逐项复核 authority_identity / created_dependency_fingerprint /
      locator，任何不一致立即 ``TreeMaterialError``；
    - 非 tree material（其它 object_type 或其它解析语义版本）→ ``None``（不越界代答）。
    """

    def __init__(self, *, snapshot, blocks, current_evidence,
                 qualification_policy=None, outline=None) -> None:
        self._batch = build_tree_materials(
            snapshot=snapshot, blocks=blocks, current_evidence=current_evidence,
            qualification_policy=qualification_policy, outline=outline)
        self._by_hash: dict = {}
        for material, record in zip(self._batch.materials, self._batch.payload_records):
            self._by_hash[record.payload_hash] = (material, record)

    @property
    def batch(self) -> TreeMaterialBatch:
        return self._batch

    def resolve(self, payload_ref) -> TS.ResolvedPayload | None:
        if payload_ref.object_type != TREE_MATERIAL_TYPE:
            return None
        if payload_ref.version != TS.TREE_MATERIAL_RESOLVER_VERSION:
            return None
        entry = self._by_hash.get(payload_ref.content_hash)
        if entry is None:
            return None
        material, record = entry
        if record.authority_identity != payload_ref.authority_identity:
            raise TreeMaterialError(
                f"payload {record.payload_id} authority_identity 与 ref 不符")
        if record.created_dependency_fingerprint != payload_ref.created_dependency_fingerprint:
            raise TreeMaterialError(
                f"payload {record.payload_id} created_dependency_fingerprint 与 ref 不符")
        if material.locator.to_dict() != payload_ref.locator.to_dict():
            raise TreeMaterialError(
                f"payload {record.payload_id} locator 与 ref 不一致（重切结果为准）")
        return TS.ResolvedPayload(
            object_type=record.object_type,
            authority_identity=record.authority_identity,
            version=record.version,
            locator=material.locator,
            content_hash=record.payload_hash,
            payload_bytes=record.payload_bytes,
        )


__all__ = [
    "TREE_MATERIAL_ENVELOPE_KIND",
    "MATERIAL_PAYLOAD_VERSION",
    "TREE_MATERIAL_TYPE",
    "TREE_MATERIAL_GAP_REASONS",
    "TREE_MATERIAL_CONTENT_KINDS",
    "TREE_MATERIAL_CONTENT_REASONS",
    "TREE_MATERIAL_CONTENT_REASONS_BY_KIND",
    "TREE_MATERIAL_CONTENT_MATERIAL_KINDS",
    "TREE_MATERIAL_SELECTION_UNRESOLVED_REASONS",
    "TREE_MATERIAL_SYMBOL_GLYPHS",
    "TREE_MATERIAL_HOLLOW_MARKERS",
    "CONTENT_DISPOSITION_EXCERPT_CHARS",
    "TreeMaterialError",
    "CurrentEvidenceBinding",
    "evidence_binding_from_snapshot",
    "TreeMaterialGap",
    "SpanContentQualification",
    "TreeMaterialContentDisposition",
    "TreeMaterialBatch",
    "tree_material_dependency_fingerprint",
    "compute_tree_material_id",
    "is_selection_marker",
    "classify_span_content",
    "is_tree_material_candidate",
    "build_tree_materials",
    "TreeMaterialPayloadResolver",
]

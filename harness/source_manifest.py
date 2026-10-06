"""用户上传材料的来源清单（M930-3 批次 A 一.1–一.5 / DESIGN_V2.md O-12）。

业务裁决要点：

1. 为**全部**当前、政策允许的用户上传材料建立可回查的来源清单，取消「按 PDF 路径
   排序取 [0]」这种任意选择。**「全部登记、可检索」不等于「全部同权用于当前事实」**，
   更不等于把全文无筛选送给 Writer——登记与选用是两件事，本模块把两者都显式化。
2. 同类型材料「较新且可核实者优先表达当前状态」；旧同类型材料**保留索引与来源身份**，
   用于历史分期、变化、补充与冲突核对，不静默丢弃。年报与募集说明书是**不同文档系列**，
   按具体主题、披露日期与事实期间共同检索，一份不得无条件替代另一份。
2b. `sm-2`（跨文档联合检索 L0）：登记层的产出由「**选一个 primary**」改成「**产出一个
   有序源集**」。全局恰一份 `current_state_source` 只是**旧单文档接口的兼容读视图**，
   它**不决定**各材料事实的期间或权威；同系列的旧期间成员是 `history_and_conflict_source`，
   其他系列成员是 `topic_participating_source`——**两者都留在源集里按主题检索**。
   注意两个最容易混的点：**选「较新」用的是内容报告期间**（不是披露日期、不是入库时间），
   而**同类判定**用的是内容识别的系列 + 内容报告期间（不是注册类型、不是文件名）。
3. 类型判断**只依据文档正文**（封面/标题页原文），不依据文件名、不依据路径、不依据
   入库时间。DB 里 `source_type` 的记录值**原样报告**，另列有证据支持的判断与
   SourcePolicy 影响；绝不暗改历史 DB、不静默重分类。
4. 披露/发行日期必须**可核实**：优先取公告/文档正文。PDF 元数据、入库时间、最新财务
   指标日**不得冒充**披露日期；不可核实即标 `unknown`。文档封面只给出月粒度的日期时，
   登记为**另行标注的月粒度线索**，不升格为日粒度披露日期，也不回填。
5. 来源清单分别登记披露/发行日期及依据、事实/财务期间、入库时间；三者不得互相顶替。
   网络事件另行保留事件日/来源发布日期/抓取时间（本模块只登记上传材料的三个字段）。

公司无关、文件名无关、页码无关：本模块的规则只匹配**中文文档类型名词**与**文档自述的
期间标记**，不含任何公司名、case_id、固定年份或固定页码规则。

只读：本模块只通过 `evidence/store.py` 的既有 `*_ro` 只读接口读库，不写任何库、
不建库、不改历史 DB。
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from evidence import store as ESTORE
from evidence.schema import DocumentRecord, EvidenceBlock

logger = logging.getLogger("harness.source_manifest")

#: 清单规则版本（判断规则、取值表、选择规则任一变化都必须升版）。
#:
#: `sm-1` → `sm-2`（跨文档联合检索 L0）：**选择规则变了**——由「选一个 primary」改成
#: 「产出一个有序源集」，并新增逐份 `source_role` / `retrieval_order`、唯一当前锚的歧义判定
#: 与注册类型/内容识别不一致的 typed 审计。旧版读数按 `sm-1` 的产物身份保留，不得被静默重解释。
MANIFEST_POLICY_VERSION = "sm-2"

#: 源集成员角色的闭集（`DocumentSelection.source_role` 的取值域）。
#:
#: - `current_state_source`：全局唯一的「当前状态」兼容锚（旧单文档接口的读视图）。
#:   它**不**决定各材料事实的期间或权威，只是既有读视图的落点。
#: - `history_and_conflict_source`：与锚**同系列**且期间较旧——历史分期、变化与冲突核对。
#: - `topic_participating_source`：其他系列，按主题**同等资格**参与检索。
#: - `not_used`：不满足 eligibility，本 run 不作为事实来源（登记身份保留）。
SOURCE_ROLES = (
    "current_state_source",
    "history_and_conflict_source",
    "topic_participating_source",
    "not_used",
)

#: 当前锚的解析状态（`SourceManifest.current_state`）。
CURRENT_STATE_RESOLVED = "resolved"
CURRENT_STATE_AMBIGUOUS = "ambiguous_current_state"
CURRENT_STATE_NONE = "no_eligible_current"

#: 类型判断只读**封面/标题页**范围内的块：正文里「年度报告」这个词到处都是，
#: 只有文档自己的封面才构成类型证据。页码无关（不写死具体页），只限定「文档前若干页」。
COVER_MAX_PAGE = 2
#: 期间标记（「报告期 指 …」）通常出现在释义表里，通常也在前几页；给一个有界的早期窗口。
PERIOD_SCAN_MAX_PAGE = 15
PERIOD_SCAN_MAX_BLOCKS = 80

# ---------------------------------------------------------------------------
# 文档类型标记表（仅中文文档类型名词；最长标记优先，见 _ORDERED_MARKERS）
#
# 映射列说明：`evidence_source_type` 对应 `evidence/schema.py::SOURCE_TYPES`
# （annual_report | debt_circular | other），`local_source_class` 对应
# `routing/router.py::LOCAL_SOURCE_CLASSES`。两张表都是既有词汇表，本模块不新增词汇。
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _TypeMarker:
    marker: str
    document_class: str
    evidence_source_type: str
    local_source_class: str


_TYPE_MARKERS = (
    _TypeMarker("半年度报告", "半年度报告", "annual_report", "annual_report"),
    _TypeMarker("年度报告", "年度报告", "annual_report", "annual_report"),
    _TypeMarker("季度报告", "季度报告", "annual_report", "annual_report"),
    _TypeMarker("募集说明书", "债券募集说明书", "debt_circular", "prospectus"),
    _TypeMarker("发行公告", "发行公告", "debt_circular", "announcement"),
    _TypeMarker("受托管理报告", "受托管理报告", "other", "announcement"),
)
#: 最长标记优先：否则「半年度报告」会被「年度报告」抢先匹配成年度报告。
_ORDERED_MARKERS = tuple(sorted(_TYPE_MARKERS, key=lambda m: len(m.marker), reverse=True))

# ---------------------------------------------------------------------------
# SourcePolicy 影响取值表
#
# 权威位置（本模块不 import 它们，避免拉入 embedding/reportlab 等重依赖；
# 由聚焦测试逐值对账防止漂移）：
#   retrieval/retriever.py::_PRIORITY_BOOST      检索排序加权
#   retrieval/indexer.py::_infer_doc_type         索引优先级
#   sections/publishable_report.py::SOURCE_TYPE_LABELS  人类可读类型标签（legacy 层，
#       其中 "other" 明确映射为 ""，注释写明「不猜测为年报」）
# ---------------------------------------------------------------------------

RETRIEVAL_BOOST_BY_SOURCE_TYPE: dict[str, float] = {
    "debt_circular": 1.25,
    "annual_report": 1.05,
    "other": 0.95,
}
INDEX_PRIORITY_BY_SOURCE_TYPE: dict[str, int] = {
    "debt_circular": 2,
    "annual_report": 1,
    "other": 0,
}
#: 不在表中时的加权（retriever._apply_priority_boost 的 .get(..., 1.0)）。
RETRIEVAL_BOOST_UNKNOWN = 1.0
#: 人类可读标签：只有已知值才有标签，绝不猜测（与 SOURCE_TYPE_LABELS 同语义）。
SOURCE_TYPE_LABELS: dict[str, str] = {
    "annual_report": "年度报告",
    "debt_circular": "债项说明书",
    "other": "",
}

# ---------------------------------------------------------------------------
# 期间/日期抽取（只认文档自述的标记，不做通用日期猜测）
# ---------------------------------------------------------------------------

#: 文档自述的「当期期间」标记。只认「报告期」这一项名词 + 「指」引导，避免误抓正文里的
#: 任意日期区间。同义标记按需扩展时必须升 MANIFEST_POLICY_VERSION。
_PERIOD_MARKERS = ("报告期",)
_PERIOD_RANGE_RE = re.compile(
    r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})?\s*日?\s*至\s*"
    r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})?\s*日?")
_COVER_DAY_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_COVER_MONTH_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")


def _norm(text: str) -> str:
    """只用于**匹配**的空白归一；登记的依据一律保留原文。"""
    return re.sub(r"\s+", "", text or "")


@dataclass(frozen=True)
class CoverEvidence:
    """一小段可定位的文档正文证据（用于类型/期间判断）。"""

    text: str
    locator: str   # 形如 "p1 b0"
    page_number: int
    block_index: int


@dataclass(frozen=True)
class DocumentTypeJudgment:
    """有证据支持的文档类型判断（与 DB 注册值分列，互不覆盖）。"""

    document_class: str | None
    matched_marker: str | None
    evidence_source_type: str | None
    local_source_class: str | None
    basis: CoverEvidence | None
    confidence: str          # "document_cover" | "unresolved"

    @property
    def resolved(self) -> bool:
        return self.document_class is not None


@dataclass(frozen=True)
class DisclosureDateState:
    """披露/发行日期的可核实状态（O-12）。

    - `date` 只在**可核实到日**时才有值；否则 None + `state="unknown"`。
    - `period_hint` 是文档封面给出的**月粒度**线索，单独标注 precision，**不**升格为
      `date`，也**不**回填。
    - `ingestion_time` 是入库时间，登记但**绝不**作为披露日期。
    """

    date: str | None
    state: str                       # "verified" | "unknown"
    period_hint: str | None
    period_hint_precision: str | None    # "month"
    basis: str
    ingestion_time: str


@dataclass(frozen=True)
class ContentReportPeriod:
    """内容报告期间（文档自述）。"""

    period: str | None               # "YYYY-MM-DD..YYYY-MM-DD"
    period_end: str | None
    state: str                       # "verified" | "unknown"
    basis: str
    scanned_blocks: int
    scan_bound: str


@dataclass(frozen=True)
class SourcePolicyEffect:
    """注册类型 `source_type` 造成的实际政策后果（可由代码取值表复算）。"""

    registered_source_type: str
    retrieval_boost: float
    index_priority: int
    display_label: str
    judged_source_type: str | None
    judged_retrieval_boost: float | None
    judged_index_priority: int | None
    note: str


@dataclass(frozen=True)
class SourceManifestEntry:
    """一份当前、政策允许的上传材料的来源身份。"""

    company_id: str
    document_id: str
    document_version: str
    file_sha256: str
    file_size: int
    page_count: int | None
    source_name: str
    source_path: str | None
    registry_status: str
    material_group: str
    registered_source_type: str
    evidence_set_version: str | None
    type_judgment: DocumentTypeJudgment
    disclosure: DisclosureDateState
    content_report_period: ContentReportPeriod
    policy_effect: SourcePolicyEffect
    eligibility: str
    eligibility_reason: str


@dataclass(frozen=True)
class SourceDocumentKey:
    """全链唯一的文档键（`§L0.6`）。

    它在 L1（每个文档一个有身份的树会话）/ L2（跨源导航决策）/ L3（工具精确分派）/
    L4（跨源 Pack 的责任与四臂）反复使用，因此必须是**一个类型**而不是四个裸串。

    内容寻址（`§L0.8`）：四个轴全部来自材料登记与内容识别，**不含**路径、`created_at`、
    `ingestion_time`、run_id。`__post_init__` 逐项校验非空与主体一致；主体不一致
    fail-closed，**不取多数票**。
    """

    company_id: str
    document_id: str
    document_version: str
    evidence_set_version: str

    def __post_init__(self) -> None:
        for name in ("company_id", "document_id", "document_version",
                     "evidence_set_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"SourceDocumentKey.{name} 必须是非空字符串（内容寻址身份不完整），"
                    f"实际 {value!r}")
        if "/" in self.document_id or "\\" in self.document_id \
                or self.document_id.lower().endswith(".pdf"):
            raise ValueError(
                f"SourceDocumentKey.document_id 不得是路径或文件名（内容寻址），"
                f"实际 {self.document_id!r}")

    def to_dict(self) -> dict:
        return {
            "company_id": self.company_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "evidence_set_version": self.evidence_set_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SourceDocumentKey":
        if not isinstance(d, dict):
            raise ValueError("SourceDocumentKey 必须是 mapping")
        expected = {"company_id", "document_id", "document_version", "evidence_set_version"}
        extra = set(d) - expected
        if extra:
            raise ValueError(f"SourceDocumentKey 含未知字段 {sorted(extra)}")
        missing = expected - set(d)
        if missing:
            raise ValueError(f"SourceDocumentKey 缺字段 {sorted(missing)}")
        return cls(**{name: d[name] for name in expected})

    def same_subject_as(self, other: "SourceDocumentKey") -> bool:
        """主体一致性：不同主体**不得**混在同一个源集里（fail-closed，不取多数票）。"""
        return self.company_id == other.company_id


def source_key_axes(key: "SourceDocumentKey") -> tuple[str, str, str, str]:
    """四轴身份元组——**全链唯一的键比较单位**（§L0.6，定点返修 T4）。

    任何「某个来源键是不是台账成员」的判断都必须用**这个元组**相等，不得只比
    `document_id`：同 `document_id`、不同 `document_version` 或
    `evidence_set_version` 是**两份不同的文档**（内容寻址），按 id 就近匹配会把
    「声明了 B、拿到了 A」变成静默错配。
    """
    if not isinstance(key, SourceDocumentKey):
        raise ValueError(f"source_key_axes 只接受 SourceDocumentKey，实际 {type(key).__name__}")
    return (key.company_id, key.document_id, key.document_version, key.evidence_set_version)


def same_document_id_only(left: "SourceDocumentKey",
                          right: "SourceDocumentKey") -> bool:
    """**只**比 `document_id` 的弱判据——仅供在 fail-closed 时报出「同 id 错身份」的诊断。

    它**不是**匹配判据：任何成员归属判断都必须用 :func:`source_key_axes`。保留成具名函数
    是为了让「这里故意比得弱、只是为了让错误信息说得清」在读代码时一眼可见，而不是散落的
    `a.document_id == b.document_id`。
    """
    return left.document_id == right.document_id


@dataclass(frozen=True)
class RegistrationContentClassMismatch:
    """注册类型与内容识别类型不一致的 typed 审计（`§L0.7`，口径 1）。

    现状实例：某份年报的注册类型是 `other`，而封面内容识别为「年度报告」。这是**已登记的真实
    不一致**：本轮**不得**为让流程顺畅去改数据库行或重标注册类型。本记录随源集成员进 Pack
    与验收报告，读回时必须可见。

    它**不**影响同级判定（同类判定按内容系列 + 期间，不按 `registered_class`），也
    **不得**被写成 gap、塞进 `semantic_tags` 或自由文本 `detail`（§0.13 第 9 条）。
    """

    document_key: "SourceDocumentKey"
    registered_class: str
    content_identified_class: str
    identification_basis: str
    policy_version: str

    def to_dict(self) -> dict:
        return {
            "document_key": self.document_key.to_dict(),
            "registered_class": self.registered_class,
            "content_identified_class": self.content_identified_class,
            "identification_basis": self.identification_basis,
            "policy_version": self.policy_version,
        }


@dataclass(frozen=True)
class DocumentSelection:
    """选用决定（登记 ≠ 选用，这里把两者分开记账）。"""

    document_id: str
    document_version: str
    selection_state: str    # "selected_source_set" | "registered_not_selected"
    reason_code: str
    reason: str
    #: `§L0.2`：闭集 `SOURCE_ROLES`；`§L0.3` 由源集清单层判定。
    source_role: str = "not_used"
    #: 确定性序位（同 role 内也确定）；只用内容派生的排序键派生。
    retrieval_order: int = -1
    #: T4：**第四轴**。选用决定与来源条目在这条路径上同样要四轴可比，否则 `role_of` 只能按
    #: `(document_id, document_version)` 就近匹配——同 id 同版本、不同证据集的键会被**当成**
    #: 这条选用决定的成员并拿到一个角色。缺值时留 `None`（此时该行的四轴身份不完整，见
    #: `role_of` 的核对规则），不得用别的行顶替。
    evidence_set_version: str | None = None


@dataclass(frozen=True)
class SourceManifest:
    """一次运行的来源清单。"""

    policy_version: str
    company_id: str
    generated_at: str
    report_as_of: str
    report_timezone: str
    financial_data_cutoff: str | None
    entries: tuple[SourceManifestEntry, ...]
    provenance_findings: tuple[str, ...]
    selection: tuple[DocumentSelection, ...]
    primary_document_id: str | None
    primary_document_version: str | None
    #: `§L0.3` 的**有序源集**（`source_role != "not_used"`，按 `retrieval_order` 升序）。
    #: 旧字段 `primary_document_id/version` 只是它的兼容读视图。
    document_keys: tuple[SourceDocumentKey, ...] = ()
    #: 当前锚的解析状态：`resolved` / `ambiguous_current_state` / `no_eligible_current`。
    current_state: str = CURRENT_STATE_RESOLVED
    #: 当前锚无法解析时的 typed 理由（`ambiguous_current_state` / `no_eligible_current`）。
    current_state_reason: str | None = None
    #: `§L0.7` 注册类型与内容识别不一致的逐份审计（不得写成 gap）。
    registration_class_mismatches: tuple[RegistrationContentClassMismatch, ...] = ()

    def eligible(self) -> tuple[SourceManifestEntry, ...]:
        return tuple(e for e in self.entries if e.eligibility == "eligible_current")

    def by_document_id(self, document_id: str) -> SourceManifestEntry | None:
        for e in self.entries:
            if e.document_id == document_id:
                return e
        return None

    def by_document_key(self, document_id: str, document_version: str,
                        evidence_set_version: str | None = None
                        ) -> SourceManifestEntry | None:
        """`§L0.5`：同 id 多版本时不再歧义。

        T4：给了 `evidence_set_version` 就必须**四轴全同**才算命中。前三轴相同而第四轴不同的
        键在这条路径上**不得**命中——内容寻址身份下那是两份不同的文档，`None` 是「不在台账里」
        的正确答案，而不是「就近取一份」。
        """
        for e in self.entries:
            if e.document_id == document_id \
                    and e.document_version == document_version:
                if evidence_set_version is not None \
                        and e.evidence_set_version != evidence_set_version:
                    continue
                return e
        return None

    def source_set_entries(self) -> tuple[SourceManifestEntry, ...]:
        """有序源集的成员条目（与 `document_keys` 同序）。"""
        order = {k.document_id + "\x00" + k.document_version: i
                 for i, k in enumerate(self.document_keys)}
        picked = [e for e in self.entries
                  if e.document_id + "\x00" + e.document_version in order]
        picked.sort(key=lambda e: order[e.document_id + "\x00" + e.document_version])
        return tuple(picked)

    def role_of(self, document_id: str, document_version: str,
                evidence_set_version: str | None = None) -> str | None:
        """台账里这份文档的 `source_role`（T4：给了第四轴就必须**四轴全同**才算命中）。

        给了 `evidence_set_version` 时，前三轴相同而第四轴不同的选用决定一律跳过——内容寻址
        身份下那是两份不同的文档，`None`（「台账里没有这一份」）是正确答案，而不是「就近取
        一行来顶」。查得越窄越好：`None` 会让调用方 fail-closed，就近匹配则会把「声明了 B、
        拿到了 A」变成静默错配。
        """
        for s in self.selection:
            if s.document_id == document_id \
                    and s.document_version == document_version:
                if evidence_set_version is not None \
                        and s.evidence_set_version != evidence_set_version:
                    continue
                return s.source_role
        return None

    def key_of(self, entry: "SourceManifestEntry") -> SourceDocumentKey | None:
        if not entry.evidence_set_version:
            return None
        return SourceDocumentKey(
            company_id=entry.company_id,
            document_id=entry.document_id,
            document_version=entry.document_version,
            evidence_set_version=entry.evidence_set_version)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


# ---------------------------------------------------------------------------
# 纯判断函数
# ---------------------------------------------------------------------------

def judge_document_type(cover_blocks: list[CoverEvidence]) -> DocumentTypeJudgment:
    """只看封面范围原文判断文档类型。

    只用文档自己的封面块作为依据：正文里「年度报告」等词会大量出现，不能作为类型证据。
    """
    for block in cover_blocks:
        norm = _norm(block.text)
        for m in _ORDERED_MARKERS:
            if m.marker in norm:
                return DocumentTypeJudgment(
                    document_class=m.document_class,
                    matched_marker=m.marker,
                    evidence_source_type=m.evidence_source_type,
                    local_source_class=m.local_source_class,
                    basis=block,
                    confidence="document_cover",
                )
    return DocumentTypeJudgment(
        document_class=None, matched_marker=None, evidence_source_type=None,
        local_source_class=None, basis=None, confidence="unresolved")


def judge_content_report_period(
    blocks: list[EvidenceBlock],
) -> ContentReportPeriod:
    """从文档自述的期间标记抽取内容报告期间；抽不到就是 unknown，不猜。"""
    scanned = 0
    for block in blocks:
        if block.page_number is None or block.page_number > PERIOD_SCAN_MAX_PAGE:
            break
        if scanned >= PERIOD_SCAN_MAX_BLOCKS:
            break
        scanned += 1
        norm = _norm(block.text)
        if not any(mk in norm for mk in _PERIOD_MARKERS):
            continue
        m = _PERIOD_RANGE_RE.search(norm)
        if not m:
            continue
        y1, mo1, d1, y2, mo2, d2 = m.groups()
        start = "%04d-%02d-%02d" % (int(y1), int(mo1), int(d1 or 1))
        end = "%04d-%02d-%02d" % (int(y2), int(mo2), int(d2 or 1))
        return ContentReportPeriod(
            period=f"{start}..{end}",
            period_end=end,
            state="verified",
            basis=f"文档正文 {_locator(block)} 原文：{block.text.strip()[:120]}",
            scanned_blocks=scanned,
            scan_bound=(f"page<={PERIOD_SCAN_MAX_PAGE}, "
                        f"blocks<={PERIOD_SCAN_MAX_BLOCKS}"),
        )
    return ContentReportPeriod(
        period=None, period_end=None, state="unknown",
        basis=(f"文档前 {PERIOD_SCAN_MAX_PAGE} 页 / 前 {PERIOD_SCAN_MAX_BLOCKS} 块内"
              f"未找到自述期间标记 {list(_PERIOD_MARKERS)}"),
        scanned_blocks=scanned,
        scan_bound=(f"page<={PERIOD_SCAN_MAX_PAGE}, blocks<={PERIOD_SCAN_MAX_BLOCKS}"),
    )


#: `§L0.4`（O-12）：`state == "unknown"` 时 `basis` 必须**显式**写出这两条，防止后人把
#: 「不可核实」的理由悄悄换成一句空话，也防止入库时间被回填成披露日期。用存在性检查而不是
#: 文本哈希——这里要保证的是「理由里真的说了这件事」，不是「理由逐字等于某串」。
_DISCLOSURE_UNKNOWN_BASIS_REQUIRED = ("入库时间", "不得冒充")


def assert_disclosure_state(state: DisclosureDateState) -> None:
    """校验披露日期状态自身的纪律（O-12）。

    - `verified` 必须带得出日的 `date`；
    - `unknown` 的 `basis` 必须显式包含 `_DISCLOSURE_UNKNOWN_BASIS_REQUIRED` 的每一条，
      且 `date` 必须为空——**月粒度线索不得冒充满日期**。
    """
    if state.state == "verified":
        if not state.date:
            raise ValueError("DisclosureDateState.state='verified' 但 date 为空")
        return
    if state.state != "unknown":
        raise ValueError(f"未知的披露日期状态 {state.state!r}")
    if state.date is not None:
        raise ValueError(
            f"DisclosureDateState.state='unknown' 但 date={state.date!r}："
            "月粒度线索不得升格为披露日期")
    missing = [w for w in _DISCLOSURE_UNKNOWN_BASIS_REQUIRED if w not in (state.basis or "")]
    if missing:
        raise ValueError(
            f"DisclosureDateState.state='unknown' 的 basis 未显式说明 {missing!r}："
            "不可核实的理由必须写明「入库时间 / PDF 元数据不得冒充」，"
            f"实际 basis={state.basis!r}")


def judge_disclosure_date(
    *,
    ingestion_time: str,
    titled_blocks: tuple[CoverEvidence, ...] = (),
) -> DisclosureDateState:
    """判断披露/发行日期的可核实状态。

    只在**带类型标记的封面块**里找封面日期：封面日期必须与文档标题同块才构成证据，
    否则前几页正文里的任意日期都会被误抓成披露日期。

    封面只给出「YYYY年M月」时登记为月粒度线索，`date` 保持 None（unknown），
    绝不把月粒度日期升格成日粒度披露日期。
    """
    for block in titled_blocks:
        norm = _norm(block.text)
        m = _COVER_DAY_RE.search(norm)
        if m:
            y, mo, d = (int(x) for x in m.groups())
            state = DisclosureDateState(
                date="%04d-%02d-%02d" % (y, mo, d),
                state="verified",
                period_hint=None,
                period_hint_precision=None,
                basis=f"文档封面 {_locator(block)} 原文：{block.text.strip()[:120]}",
                ingestion_time=ingestion_time,
            )
            assert_disclosure_state(state)
            return state
        m = _COVER_MONTH_RE.search(norm)
        if m:
            y, mo = (int(x) for x in m.groups())
            state = DisclosureDateState(
                date=None,
                state="unknown",
                period_hint="%04d-%02d" % (y, mo),
                period_hint_precision="month",
                basis=(f"文档封面 {_locator(block)} 仅给出月粒度日期，原文："
                       f"{block.text.strip()[:120]}；月粒度线索不升格为披露日期、不回填，"
                       f"PDF 元数据、入库时间、最新财务指标日亦不得冒充"),
                ingestion_time=ingestion_time,
            )
            assert_disclosure_state(state)
            return state
    state = DisclosureDateState(
        date=None,
        state="unknown",
        period_hint=None,
        period_hint_precision=None,
        basis=("文档封面未给出可核实到日的披露/发行日期；"
               "PDF 元数据、入库时间、最新财务指标日不得冒充，入库时间不当披露日期用"),
        ingestion_time=ingestion_time,
    )
    assert_disclosure_state(state)
    return state


def judge_policy_effect(
    registered_source_type: str,
    judgment: DocumentTypeJudgment,
) -> SourcePolicyEffect:
    """按代码既有取值表复算注册类型带来的实际政策后果。"""
    boost = RETRIEVAL_BOOST_BY_SOURCE_TYPE.get(
        registered_source_type, RETRIEVAL_BOOST_UNKNOWN)
    priority = INDEX_PRIORITY_BY_SOURCE_TYPE.get(registered_source_type, 0)
    label = SOURCE_TYPE_LABELS.get(registered_source_type, "")

    judged = judgment.evidence_source_type
    judged_boost = (RETRIEVAL_BOOST_BY_SOURCE_TYPE.get(judged)
                    if judged else None)
    judged_priority = (INDEX_PRIORITY_BY_SOURCE_TYPE.get(judged)
                       if judged else None)

    if judged and judged != registered_source_type:
        note = (f"DB 注册类型 {registered_source_type!r} 与有证据支持的类型判断 "
                f"{judged!r} 不一致：注册值使该材料在检索排序中取 "
                f"×{boost}（判断类型应为 ×{judged_boost}）、索引优先级 "
                f"{priority}（判断类型应为 {judged_priority}）、人类可读类型标签 "
                f"{label!r}。本报告原样报告注册值，不暗改历史 DB、不静默重分类；"
                f"该差异是来源政策影响，不是事实内容。")
    else:
        note = (f"DB 注册类型 {registered_source_type!r} 与类型判断一致；"
                f"检索加权 ×{boost}，索引优先级 {priority}，类型标签 {label!r}。")
    return SourcePolicyEffect(
        registered_source_type=registered_source_type,
        retrieval_boost=boost,
        index_priority=priority,
        display_label=label,
        judged_source_type=judged,
        judged_retrieval_boost=judged_boost,
        judged_index_priority=judged_priority,
        note=note,
    )


def _locator(block) -> str:
    page = block.page_number if block.page_number is not None else "?"
    idx = block.block_index if block.block_index is not None else "?"
    return f"p{page} b{idx}"


# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------

def _cover_blocks(blocks: list[EvidenceBlock]) -> list[CoverEvidence]:
    out: list[CoverEvidence] = []
    for b in blocks:
        if b.page_number is None or b.page_number > COVER_MAX_PAGE:
            break
        out.append(CoverEvidence(
            text=b.text or "", locator=_locator(b),
            page_number=b.page_number, block_index=b.block_index))
    return out


def build_source_manifest(
    *,
    db_path: str | Path,
    company_id: str,
    generated_at: str,
    report_as_of: str,
    report_timezone: str,
    financial_data_cutoff: str | None = None,
) -> SourceManifest:
    """对某公司全部已登记文档建立来源清单（只读）。

    全部已登记文档都进入 `entries`（「全部登记、可检索」）；能否**用于当前事实**
    另由 `eligibility` 表达，二者不混同。
    """
    docs: list[DocumentRecord] = ESTORE.list_documents_ro(db_path, company_id)

    entries: list[SourceManifestEntry] = []
    null_provenance_docs: list[str] = []
    for doc in docs:
        set_version = ESTORE.current_evidence_set_ro(
            db_path, doc.company_id, doc.document_id, doc.document_version)
        blocks = (ESTORE.list_document_evidence_ro(
            db_path, doc.company_id, doc.document_id, doc.document_version,
            set_version) if set_version else [])

        cover = _cover_blocks(blocks)
        judgment = judge_document_type(cover)
        titled = tuple(b for b in cover
                       if judgment.matched_marker
                       and judgment.matched_marker in _norm(b.text))
        disclosure = judge_disclosure_date(
            ingestion_time=doc.created_at, titled_blocks=titled)
        period = judge_content_report_period(blocks)
        effect = judge_policy_effect(doc.source_type, judgment)

        # 资格：登记状态 + 是否有 current evidence set。注意这里不按「类型是否为
        # annual_report」筛掉 other——那会变成静默重分类；other 的政策代价由
        # policy_effect 如实表达，是否可用另见 eligibility_reason。
        if doc.status != "current":
            eligibility = "not_current"
            reason = f"documents.status={doc.status!r}（非 current），不作为当前材料"
        elif not set_version:
            eligibility = "no_evidence_set"
            reason = "没有 status=current 的 evidence set，无可检索材料"
        else:
            eligibility = "eligible_current"
            reason = (f"documents.status=current 且有 current evidence set "
                      f"{set_version}（{len(blocks)} 块）")

        if set_version and blocks and not any(
                b.published_at or b.report_period for b in blocks):
            null_provenance_docs.append(doc.document_id)

        entries.append(SourceManifestEntry(
            company_id=doc.company_id,
            document_id=doc.document_id,
            document_version=doc.document_version,
            file_sha256=doc.file_sha256,
            file_size=doc.file_size,
            page_count=doc.page_count,
            source_name=doc.source_name,
            source_path=doc.source_path,
            registry_status=doc.status,
            material_group=doc.material_group,
            registered_source_type=doc.source_type,
            evidence_set_version=set_version,
            type_judgment=judgment,
            disclosure=disclosure,
            content_report_period=period,
            policy_effect=effect,
            eligibility=eligibility,
            eligibility_reason=reason,
        ))

    findings: list[str] = [
        (f"documents 表没有披露日期列；evidence_blocks.published_at / report_period / "
         f"source_uri 在本库为非空来源，"
         f"{len(null_provenance_docs)}/{len(entries)} 份材料的 published_at 与 "
         f"report_period 全为空——披露日期只能来自文档正文封面，取不到即 unknown。")
        if null_provenance_docs else
        "documents 表没有披露日期列；有无 block 级 published_at/report_period 需逐份核对。",
        ("documents.created_at 是**入库时间**，本清单原样登记为 ingestion_time，"
         "不得当作披露/发行日期。"),
    ]

    selection, primary = select_documents(entries)

    # `§L0.3` 当前锚的解析状态：由 selection 与全 eligible 集的期间可核实性复算，不另立判断。
    eligible_with_period = [e for e in entries
                            if e.eligibility == "eligible_current"
                            and e.content_report_period.period_end]
    if primary is None:
        # select_documents 已区分「无锚」与「歧义」；这里按同一口径复算 typed 状态。
        anchor_candidate = _sorted_eligible(entries)
        if not anchor_candidate:
            current_state = CURRENT_STATE_NONE
            current_state_reason = _NO_ELIGIBLE_CURRENT_REASON
        else:
            current_state = CURRENT_STATE_AMBIGUOUS
            tentative = anchor_candidate[0]
            series = derive_series_identity(tentative)
            group_unknown = [
                e for e in entries
                if e.eligibility == "eligible_current"
                and not e.content_report_period.period_end
                and same_series_as(derive_series_identity(e), series)]
            current_state_reason = _AMBIGUOUS_CURRENT_REASON.format(
                anchor=f"{tentative.document_id}@{tentative.document_version}",
                series=series.series or "未判定",
                unknown=", ".join(
                    f"{e.document_id}@{e.document_version}" for e in group_unknown))
    else:
        current_state = CURRENT_STATE_RESOLVED
        current_state_reason = None

    # `§L0.8` 有序源集：role != not_used 的成员，按 retrieval_order 升序；每份都必须能由
    # `(company_id, document_id, document_version, evidence_set_version)` 复算。
    by_pair = {(e.document_id, e.document_version): e for e in entries}
    document_keys: list[SourceDocumentKey] = []
    for s in sorted((s for s in selection if s.source_role != "not_used"),
                    key=lambda s: (s.retrieval_order, s.document_id, s.document_version)):
        entry = by_pair.get((s.document_id, s.document_version))
        if entry is None or not entry.evidence_set_version:
            continue
        key = SourceDocumentKey(
            company_id=entry.company_id,
            document_id=entry.document_id,
            document_version=entry.document_version,
            evidence_set_version=entry.evidence_set_version)
        # 主体一致性：整个 run 的源集只能是同一主体的材料（fail-closed，不取多数票）。
        for prev in document_keys:
            if not prev.same_subject_as(key):
                raise ValueError(
                    f"源集含不同主体的材料：{prev.to_dict()} vs {key.to_dict()}"
                    "（主体一致性 fail-closed，不取多数票）")
        document_keys.append(key)

    # `§L0.7` 注册类型与内容识别不一致 → 保留**审计**，不得擅改数据库、不得写成 gap。
    mismatches: list[RegistrationContentClassMismatch] = []
    for e in entries:
        judged = e.type_judgment.evidence_source_type
        if not judged or judged == e.registered_source_type:
            continue
        if not e.evidence_set_version:
            continue
        basis = e.type_judgment.basis
        mismatches.append(RegistrationContentClassMismatch(
            document_key=SourceDocumentKey(
                company_id=e.company_id,
                document_id=e.document_id,
                document_version=e.document_version,
                evidence_set_version=e.evidence_set_version),
            registered_class=e.registered_source_type,
            content_identified_class=judged,
            identification_basis=(
                f"封面 {basis.locator} 原文：{basis.text.strip()[:120]}"
                if basis is not None else "封面未给出可核实的类型标记"),
            policy_version=MANIFEST_POLICY_VERSION,
        ))

    return SourceManifest(
        policy_version=MANIFEST_POLICY_VERSION,
        company_id=company_id,
        generated_at=generated_at,
        report_as_of=report_as_of,
        report_timezone=report_timezone,
        financial_data_cutoff=financial_data_cutoff,
        entries=tuple(entries),
        provenance_findings=tuple(findings),
        selection=selection,
        primary_document_id=primary[0] if primary else None,
        primary_document_version=primary[1] if primary else None,
        document_keys=tuple(document_keys),
        current_state=current_state,
        current_state_reason=current_state_reason,
        registration_class_mismatches=tuple(mismatches),
    )


# ---------------------------------------------------------------------------
# 选择规则（O-12：同类「较新且可核实者优先表达当前状态」；跨类型联合检索）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _NotSelected:
    """未被选中的记账模板：reason_code + 理由（理由里说明「为什么没选」而非「材料不好」）。"""

    reason_code: str
    template: str

    def reason(self, **kw) -> str:
        return self.template.format(**kw)


_NOT_SELECTED_STATE = "registered_not_selected"

_NOT_SELECTED_HISTORY = _NotSelected(
    "same_series_older_period",
    "本材料与当前锚 {anchor} **同系列**（{series}）而内容报告期间较旧 "
    "（{period} < {anchor_period}）⇒ `history_and_conflict_source`：保留在源集里按主题"
    "检索，供历史分期、变化与冲突核对使用，未静默丢弃；**不得**用它冒充较新材料的当前事实。",
)

_NOT_SELECTED_OTHER_SERIES = _NotSelected(
    "different_series_topic_participating",
    "本材料的文档系列为 {series}，与当前锚 {anchor} 的系列（{anchor_series}）不同 ⇒ "
    "`topic_participating_source`：与锚**同等资格**按主题检索，不由任何一份无条件替代"
    "另一份；这是来源角色，不是内容结论。",
)

_NOT_SELECTED_NO_PERIOD = _NotSelected(
    "no_verifiable_period_lower_rank",
    "本材料的内容报告期间无法核实到（O-12 要求可核实），无法参与「较新且可核实者优先」"
    "的比较，因此在本次选择中排在可核实期间的材料之后；本材料仍保持登记与可检索身份。",
)

_NOT_SELECTED_NOT_ELIGIBLE = _NotSelected(
    "not_eligible_current",
    "本材料不是当前可用材料（{reason}），本 run 不作为事实来源；登记身份保留。",
)

#: 当前锚无法解析（`ambiguous_current_state` / `no_eligible_current`）时的源集成员。
#:
#: `history_and_conflict_source` 与「与锚同系列」都是**相对锚**定义的谓词——锚不存在时它们
#: 无从成立。此时把成员谎称「与锚不同系列」会读成「它被判定为另一系列」，那是**倒填**。
#: 因此单列一个理由码，角色统一取 `topic_participating_source`：成员仍在源集里、仍逐份派生
#: 责任记录，但**没有任何一份**是当前锚（清单层准入闸会直接阻断，零 tree 调用、零 Pack）。
_NOT_SELECTED_NO_ANCHOR = _NotSelected(
    "current_state_unresolved_source_set_member",
    "本 run 的当前锚无法解析（{state}）：锚不存在时「与锚同系列 / 与锚异系列」两个谓词都"
    "无从成立，故不判系列关系、不冒充当前锚。本材料仍是**有序源集成员**（按主题检索、逐份"
    "派生责任记录），但本 run 在源集清单层即被 `ScopeAdmissionBlocked` 阻断——"
    "零 tree 调用、零 Pack 提交。",
)

#: `ambiguous_current_state`：暂定全局锚的**同系列组内**存在期间不可核实成员，因此无法证明
#: 该锚是该组最新者。此时当前锚与 `primary_document_id` 均留空并附 typed 理由（`§L0.3`）。
_AMBIGUOUS_CURRENT_REASON = (
    "暂定全局锚 {anchor} 的同系列（{series}）组内存在内容报告期间**不可核实**的成员 "
    "{unknown}：无法证明该锚是该组的最新者，按 §L0.3 不得凭同类关系、文件名或入库时间推定谁新，"
    "故 `ambiguous_current_state`——当前锚与 `primary_document_id` 均留空。源集成员仍逐份按"
    "同类/跨系列规则给出角色，责任记录另按 `undetermined` 处理。"
)

#: `no_eligible_current`：全源集没有任何可核实期间的可用成员。
_NO_ELIGIBLE_CURRENT_REASON = (
    "全源集没有一份当前可用（eligible_current）材料带**可核实**的内容报告期间"
    "（O-12 要求可核实）：不得凭文件名或入库时间推定谁新，故 `no_eligible_current`——"
    "当前锚与 `primary_document_id` 均留空。"
)


def _sorted_eligible(entries: tuple[SourceManifestEntry, ...] | list) -> list:
    """可核实期间者优先（期间较新在前），其余按内容版本确定性排序。

    `§L0.4`：排序键**不变**——只用内容派生的 `content_report_period.period_end` 与
    `document_version`。**不得**引入文件名、路径或 `created_at`。披露日期（`§L0.4` 的
    O-12）既不参与排序、也不决定当前锚、更不用于在冲突中判谁赢：选「较新」用的是
    **内容报告期间**，不是披露日期——这两者最容易混，故在此重复一次。
    """
    eligible = [e for e in entries if e.eligibility == "eligible_current"]
    with_period = [e for e in eligible if e.content_report_period.period_end]
    without_period = [e for e in eligible if not e.content_report_period.period_end]
    with_period.sort(
        key=lambda e: (e.content_report_period.period_end, e.document_version),
        reverse=True)
    # 排序键只用内容派生的 document_version，不用路径/文件名：路径排序正是被裁决删除的规则。
    without_period.sort(key=lambda e: e.document_version, reverse=True)
    return with_period + without_period


@dataclass(frozen=True)
class DocumentSeriesIdentity:
    """文档系列身份（`§L0.3` 的同类判定输入）。

    三个轴**全部**来自登记与内容识别：主体（材料登记的主体轴）、系列（**内容识别**得到的
    文档系列，不是文件名）、期间（**内容**报告期间）。**不读**文件名、路径、`created_at`、
    `ingestion_time`。
    """

    issuer: str
    series: str | None      # None = 内容识别不出系列 ⇒ 同类关系无法成立
    period_end: str | None


def derive_series_identity(entry: SourceManifestEntry) -> DocumentSeriesIdentity:
    """从**内容识别**结果派生系列身份（`§L0.3`）。

    系列取 `type_judgment.document_class`——它由**封面原文**判定得到（见
    `judge_document_type`），不是文件名或注册类型。识别不出（`unresolved`）即 `None`：
    **同类关系无法成立**，该成员按跨系列处理（fail-closed，不猜）。
    """
    return DocumentSeriesIdentity(
        issuer=entry.company_id,
        series=entry.type_judgment.document_class,
        period_end=entry.content_report_period.period_end,
    )


def same_series_as(a: DocumentSeriesIdentity, b: DocumentSeriesIdentity) -> bool:
    """同类判定（口径 1，**收紧**）：同一发行主体 + 同一文档系列 + 仅年份/期间不同。

    三者缺一即**不同类**。任一方的系列识别不出即不同类（不猜、不按注册类型代替）。
    `a is not b`（不同材料）由调用方保证；本函数只判定「同类」这一层关系，
    **不**判定谁新——谁新由 `period_end` 在 `select_documents` 里单独比较。
    """
    if a.issuer != b.issuer:
        return False
    if a.series is None or b.series is None:
        return False
    return a.series == b.series


def select_documents(
    entries: tuple[SourceManifestEntry, ...] | list,
) -> tuple[tuple[DocumentSelection, ...], tuple[str, str] | None]:
    """产出一个**有序源集**（`§L0.3`；取代旧的「选一个 primary」语义）。

    返回 (逐份材料的选用记账, 唯一当前锚的 (document_id, document_version) 或 None)。
    未被选入源集的材料一律留在 `entries` 里并带 reason_code + 理由。

    步骤（全部只吃内容派生量，可复算）：

    1. 用 `_sorted_eligible` 的全体 eligible 材料排序，最高者是唯一 `current_state_source`
       ——**不限年报类型**：只上传一份可核实期间的募集说明书时，它就是当前锚且照常检索。
    2. 与锚**同系列**且期间较旧者 → `history_and_conflict_source`；
       其他系列 → `topic_participating_source`；二者都**保留在源集里按主题检索**。
    3. 不 eligible 者 → `not_used`。
    4. 歧义与无锚两种情形按 `§L0.3` 先记独立阻断（`current_state` + `current_state_reason`），
       **不伪造 proof 域**；研究前的准入由源集清单层的 `_admit_source_set` 承担。
    """
    ordered = _sorted_eligible(entries)
    anchor = ordered[0] if ordered else None
    assert_candidates = [e for e in entries if e.eligibility == "eligible_current"]
    unknown_period = [e for e in assert_candidates if not e.content_report_period.period_end]

    current_state = CURRENT_STATE_RESOLVED
    current_state_reason: str | None = None
    if anchor is None:
        current_state = CURRENT_STATE_NONE
        current_state_reason = _NO_ELIGIBLE_CURRENT_REASON
        anchor = None
    else:
        anchor_series = derive_series_identity(anchor)
        group_unknown = [e for e in unknown_period
                         if same_series_as(derive_series_identity(e), anchor_series)]
        if group_unknown:
            # 暂定锚的**同系列组内**有未知期间成员 ⇒ 无法证明锚是该组最新者。
            current_state = CURRENT_STATE_AMBIGUOUS
            current_state_reason = _AMBIGUOUS_CURRENT_REASON.format(
                anchor=f"{anchor.document_id}@{anchor.document_version}",
                series=anchor_series.series or "未判定",
                unknown=", ".join(
                    f"{e.document_id}@{e.document_version}" for e in group_unknown))
            anchor = None

    anchor_label = (f"{anchor.document_id}@{anchor.document_version}"
                    if anchor is not None else "(无)")
    anchor_series = derive_series_identity(anchor) if anchor is not None else None

    # 有序源集：先按 `_sorted_eligible` 的确定性顺序取 eligible 成员，再补上排序外的
    # eligible 成员（其间置 role=not_used，理由见下）。
    ranked = list(ordered)
    for e in assert_candidates:
        if e not in ranked:
            ranked.append(e)
    order_index = {id(e): i for i, e in enumerate(ranked)}

    selections: list[DocumentSelection] = []
    for e in entries:
        identity = derive_series_identity(e)
        same_series = (anchor is not None and anchor_series is not None
                       and same_series_as(identity, anchor_series))

        if e.eligibility != "eligible_current":
            verdict = _NOT_SELECTED_NOT_ELIGIBLE
            selections.append(DocumentSelection(
                document_id=e.document_id,
                document_version=e.document_version,
                selection_state=_NOT_SELECTED_STATE,
                reason_code=verdict.reason_code,
                reason=verdict.reason(reason=e.eligibility_reason),
                source_role="not_used",
                retrieval_order=-1,
                evidence_set_version=e.evidence_set_version,
            ))
            continue

        is_anchor = (anchor is not None and e.document_id == anchor.document_id
                     and e.document_version == anchor.document_version)
        retrieval_order = order_index.get(id(e), len(order_index))

        if is_anchor:
            selections.append(DocumentSelection(
                document_id=e.document_id,
                document_version=e.document_version,
                selection_state="selected_source_set",
                reason_code="current_state_source_anchor",
                reason=(
                    f"可核实内容报告期间 {e.content_report_period.period or 'unknown'}；"
                    f"全体 eligible 材料中按内容派生的期间与内容版本排序最高 ⇒ "
                    f"唯一 `current_state_source`（兼容锚，不决定各材料事实的期间或权威）"
                    if e.content_report_period.period_end else
                    "无可核实期间的可用材料中按内容版本确定性选出 ⇒ 唯一 "
                    "`current_state_source`（兼容锚）"),
                source_role="current_state_source",
                retrieval_order=retrieval_order,
                evidence_set_version=e.evidence_set_version,
            ))
            continue

        if anchor is None:
            # 锚不存在 ⇒ 「同系列 / 异系列」两个相对谓词都无从成立，见 `_NOT_SELECTED_NO_ANCHOR`。
            role = "topic_participating_source"
            verdict = _NOT_SELECTED_NO_ANCHOR
            reason = verdict.reason(state=current_state)
        elif same_series:
            role = "history_and_conflict_source"
            if e.content_report_period.period_end:
                verdict = _NOT_SELECTED_HISTORY
                reason = verdict.reason(
                    anchor=anchor_label, series=identity.series,
                    period=e.content_report_period.period_end,
                    anchor_period=anchor.content_report_period.period_end)
            else:
                verdict = _NOT_SELECTED_NO_PERIOD
                reason = verdict.reason()
        else:
            role = "topic_participating_source"
            verdict = _NOT_SELECTED_OTHER_SERIES
            reason = verdict.reason(
                series=identity.series or "未判定",
                anchor=anchor_label,
                anchor_series=(anchor_series.series if anchor_series else "未判定"))

        selections.append(DocumentSelection(
            document_id=e.document_id,
            document_version=e.document_version,
            selection_state="selected_source_set",
            reason_code=verdict.reason_code,
            reason=reason,
            source_role=role,
            retrieval_order=retrieval_order,
            evidence_set_version=e.evidence_set_version,
        ))

    if anchor is None:
        return tuple(selections), None
    return tuple(selections), (anchor.document_id, anchor.document_version)


# ---------------------------------------------------------------------------
# 源集清单准入闸（§L0.3）
# ---------------------------------------------------------------------------

#: 准入规则的版本（判定口径变化必须升版）。
SCOPE_ADMISSION_RULE_VERSION = "sasc-1"


@dataclass(frozen=True)
class ScopeAdmissionBlocked:
    """源集清单层准入阻断（`§L0.3`）：**没有合法当前锚**时，在研究之前停。

    它是**独立阻断**，不是 gap、不是材料拒绝、不是 conflict。后果（调用方必须遵守）：
    **零 tree 调用、零 Pack 提交**——不得为了让流程往下走而拿一份材料凑成锚。
    已有源集成员仍逐 aspect × 逐来源派生责任记录；空源集则只记逐 aspect 阻断与 gap。
    """

    company_id: str
    current_state: str
    reason: str
    source_set_size: int
    rule_version: str

    def to_dict(self) -> dict:
        return {
            "company_id": self.company_id,
            "current_state": self.current_state,
            "reason": self.reason,
            "source_set_size": self.source_set_size,
            "rule_version": self.rule_version,
        }


def _admit_source_set(manifest: "SourceManifest") -> ScopeAdmissionBlocked | None:
    """源集清单层准入（`§L0.3`，**在研究之前**调用）。

    这是**唯一**的首次拦截点：上下文层的 `_admit_scope` 只对已准入对象复核一致性，
    不承担首次拦截。清单无合法当前锚（`ambiguous_current_state` / `no_eligible_current`）
    即返回 typed 阻断对象；调用方必须在构造非空会话或 `TopicRunContext` **之前**处理它。

    返回 `None` 表示已准入（此时 `primary_document_id` 必非空，即唯一当前锚存在）。
    """
    if manifest.current_state == CURRENT_STATE_RESOLVED \
            and manifest.primary_document_id:
        return None
    return ScopeAdmissionBlocked(
        company_id=manifest.company_id,
        current_state=manifest.current_state,
        reason=(manifest.current_state_reason
                or "源集清单没有可用的当前锚（无 typed 理由即视为阻断，fail-closed）"),
        source_set_size=len(manifest.document_keys),
        rule_version=SCOPE_ADMISSION_RULE_VERSION,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建用户上传材料来源清单（只读）")
    parser.add_argument("--db", default="data/evidence.db")
    parser.add_argument("--company", required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--report-as-of", required=True)
    parser.add_argument("--report-timezone", required=True)
    parser.add_argument("--financial-cutoff", default=None)
    args = parser.parse_args(argv)

    manifest = build_source_manifest(
        db_path=args.db, company_id=args.company,
        generated_at=args.generated_at, report_as_of=args.report_as_of,
        report_timezone=args.report_timezone,
        financial_data_cutoff=args.financial_cutoff)

    print(f"来源清单 {manifest.policy_version}：{len(manifest.entries)} 份材料，"
          f"有序源集 {len(manifest.document_keys)} 份，当前锚 {manifest.primary_document_id}"
          f"（{manifest.current_state}）")
    for e in manifest.entries:
        print(f"  - {e.document_id} [{e.eligibility}] "
              f"注册类型={e.registered_source_type!r} "
              f"判断类型={e.type_judgment.document_class!r} "
              f"披露日期={e.disclosure.date or 'unknown'}"
              f"（月粒度线索={e.disclosure.period_hint or 'none'}）"
              f" 期间={e.content_report_period.period or 'unknown'} "
              f"入库={e.disclosure.ingestion_time}")
        print(f"      政策影响: {e.policy_effect.note}")
    for s in manifest.selection:
        print(f"  * {s.document_id}@{s.document_version} [{s.source_role}] "
              f"#{s.retrieval_order} {s.reason_code}: {s.reason}")
    for m in manifest.registration_class_mismatches:
        print(f"  ~ 注册/识别不一致 {m.document_key.document_id}: "
              f"注册={m.registered_class!r} 识别={m.content_identified_class!r} "
              f"（{m.identification_basis}）")
    admitted = _admit_source_set(manifest)
    if admitted is not None:
        print(f"  X 源集清单准入阻断 {admitted.current_state}: {admitted.reason}")
    for f in manifest.provenance_findings:
        print(f"  ! {f}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())

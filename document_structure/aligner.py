"""TS3 生产对齐器：`PageLayout` 原文 ↔ 存量 `EvidenceBlock` 的**唯一**生产实现。

本模块把 TS2 最终关闭轮（2026-09-17）已验收的对齐算法**逐条提取**为生产核心，
不是"再写一套近似算法"：常量、搜索顺序、失败分类与措辞依据都来自 TS2 的那一份
实现，`evaluation/run_tree_layout_acceptance.py` 改为调用本模块（生产代码**不**
反向 import `evaluation`）。

冻结语义（不得改写、不得重选）：

- `ALIGN_MIN = 0.90`（`versions.ALIGN_MIN`，单一文本对齐阈值，不设正文页 / 表格页
  两套阈值）；
- `ALIGNER_VERSION`（`versions.ALIGNER_VERSION`）；TS2 关闭轮的 `al-2` 与更早的
  `al-1` 都是已退役算法，本模块**不受理**（由 `versions.legacy_versions()` 登记，
  读回旧载荷得到的是"须经显式迁移/重算"的专门错误）；
- 判定真值表由 `schema.compute_alignment_verdict` **重算**：`coverage <= 0` 或
  `coverage < 0.90` ⇒ `unaligned`；`>= 0.90` 且含 `unexplained` ⇒
  `partially_aligned`；`>= 0.90` 且无 `unexplained` ⇒ `aligned`。只有 `aligned`
  可引用（`TextAlignmentRecord.is_citable()`）。
- `engine_artifact` 是**封闭白名单**（`blank` / `invisible_codepoint` 两条）；
  TS2 最终关闭轮已删除 `width_fold`（全角/半角等价、事后全页搜索式消除残差），
  本模块**不得恢复**：`engine_artifact_subkind()` 刻意**不接受**任何页面文本参数。
- 同一 source occurrence **不得被重复消费**：残差绑定必须由**注入式、两两不重叠**
  的 occurrence 分配证明（`_nonoverlapping_assignment`，预算
  `MAX_ALIGNMENT_SEARCH_NODES`，耗尽即保守判 `unexplained`）。
- **不得猜测字符 offset**：`char_map` 的每个映射段都由真实 `LayoutLine` 的字符区间
  与真实 `LayoutSpan` 的行内偏移推导，映射不出即不产出记录。

量化边界 fail-closed（TS3 实测发现，见报告）：`als-1` 的 `TextAlignmentRecord` 只能
把覆盖率表示成"量化到 `FLOAT_PRECISION = 3` 位的展示值"，verdict 再由**量化后**的
coverage 重算。因此存在一个极窄区间：
`exact_coverage < ALIGN_MIN <= quantize(exact_coverage)` —— 展示值不低于阈值，而按
冻结规则该块 `unaligned` 且**不可引用**。`versions.ALIGN_MIN` 的批准原文明确写着
「`0.899999 < 0.90` ⇒ `unaligned`，低于阈值的块**不可引用**」，把这样一个块升格为
可引用是**扩大**可引用面，与 fail-closed 方向相反。

TS3 §五 已在记录层消除该张力：**当时**把 `ALIGN_SCHEMA_VERSION` 升到 `als-2`，
`TextAlignmentRecord` 新增精确分子 `matched_chars`，verdict 由
`compute_alignment_verdict_exact` 按**精确整数比值**重算（不再"先四舍五入再决定
`ALIGN_MIN`"）。同时，凡是"展示值 ≥ 阈值而精确比值 < 阈值"的块，其正式终态一律收敛
为**拒绝终态** `AlignmentRefusalRecord`（原因码
`RECORD_REFUSAL_QUANTIZATION_BOUNDARY`）：不产出 `TextAlignmentRecord`，块保持
`unaligned` / 不可引用。理由：展示字段在产物里必然出现，一个"显示 0.900 却不可引用"
的记录条目会让人工复核与自动判定互相矛盾；拒绝终态把这个矛盾显式表达为
"该块的精确覆盖率为 `matched_chars / block_char_length`，低于冻结阈值"。三份真实 PDF
的 769 个块中命中 1 个（`NDSD_2024_year` 第 208 页 block 1，exact coverage
`0.8997722095671982`），因此 769 = 768 条正式记录 + 1 条拒绝终态。
（收口轮 §七 把分区不变量提为公共验证器 `apv-1` 后，对齐 wire 版本随之升版：
`als-3` / `alr-2`；含精确分子但不校验分区的 `als-2` / `alr-1` 登记为只读兼容旧版本。）

每个块**恰好一个**正式终态（§五）：`BlockAlignment.terminal` 给出那一个，
`alignment_terminals()` / `alignment_terminal_report()` 用于逐条对账，
`alignment_summary()` 给出 `terminals_missing`（缺终态的块在此暴露，不会被总数掩盖）。

身份信任边界（TS3 §四，**不得放宽**）：输入只能是携带完整身份的
`EvidenceBlockInput`；`content_hash` / `evidence_id` 由 `evidence.ids` 的正式接口
**重算**并逐字符比对（伪造 evidence_id、篡改文本或 structured payload 连输入对象都
构造不出来），公司 / 文档 / 版本再与 `PageLayout` 严格核对，缺失 / 空值 / 不一致一律
fail-closed。本模块**不**建立第二套 Evidence 身份算法，也**不**修改任何历史
Evidence ID：历史库里的 id 与重算值一致（真实 769 块逐块核对通过）才可能通过身份门。

**全集身份边界（TS3 §八 / P1-F，收口轮）**：单块身份门只能证明"这一块自洽"，不能
证明"这一块属于本文档**当前**的整份证据集"。因此正式集合入口 `align_evidence_set`
要求一个由**受信任只读 gateway 注入**的 `EvidenceSetSnapshot`（版本化 typed 类型：
公司 / 文档 / 版本 / set 版本 / `current` 状态 / 规范顺序的全成员五项身份 /
`block_count` / 快照指纹 / gateway 版本身份），并要求提交成员与快照**精确相等** ——
少一个拒绝、多一个拒绝、重复拒绝、type / hash / page / block 不符拒绝、非 current 或
身份不符拒绝；`evidence_type` 只能取自 Evidence 架构的**冻结封闭集合**。
`align_block` 保留为内部算法与单元测试入口，但它**不**构成"完成了整个 EvidenceSet"
的证明。分区闭合不变量（§七 / P1-E）由 `schema.validate_alignment_partition` 这一份
公共验证器统一执行：构造即校验，`from_dict` 读回同样 fail-closed。

只读边界：本模块不接触任何数据库、不 init / migrate / 写任何库、不联网；块文本与权威
成员清单都由调用方以只读方式取得后交给本模块（本模块**不**自行查询数据库，因此
"谁是权威成员"与"如何对齐"两个事实来源互相独立）。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Protocol

from evidence.ids import content_hash as evidence_content_hash
from evidence.ids import make_evidence_id
from evidence.schema import EVIDENCE_SET_STATUSES, EVIDENCE_TYPES

from document_structure import versions as V
from document_structure.canonical import SchemaValidationError, sha256_canonical
from document_structure.normalization import (
    NORMALIZATION_RULE_VERSION,
    strip_invisible,
    tight,
)
from document_structure.schema import (
    ALIGNMENT_REFUSAL_REASONS,
    RESIDUE_CLASS_SEVERITY,
    AlignmentRefusalRecord,
    PageLayout,
    TextAlignmentRecord,
    compute_alignment_coverage,
    compute_alignment_verdict,
    compute_alignment_verdict_exact,
    quantize,
    validate_alignment_partition,
)

#: TS4 运行时能力登记表。`span_schema` 的顶层 import 只有 stdlib 与
#: `versions`/`canonical`/`normalization`/`schema`，对 `outline_builder` 是**惰性**
#: 依赖，因此这里不会形成 import 环（`aligner → span_schema` 单向成立）。
from document_structure.span_schema import _issue_capability, issued_capability

# ---------------------------------------------------------------------------
# 对齐算法常量（TS2 已验收值的**逐条**提取；不得就地调参）
# ---------------------------------------------------------------------------

#: 片段长度上限（列重排 / occurrence 分配的单片段最大长度）。
MAX_PIECE_LEN: int = 24

#: 残差长度上限：超过即拒绝进入位移搜索（确定性上界，防指数爆炸）。
MAX_DISPLACEMENT_DP_LEN: int = 2000

#: `column_reorder` / `scattered_pieces` 成立所需的**最少片段数**。
COLUMN_REORDER_MIN_PIECES: int = 2

#: 默认单片段最小长度（高信息量残差）。
COLUMN_REORDER_MIN_PIECE_LEN: int = 4

#: 低信息量字符集（数字 / 千分位 / 百分号 / 全半角括号与斜杠 / 年月日）。
NUMERIC_LIKE_CHARS: frozenset = frozenset("0123456789,.-%()／/：:年月日")

#: 低信息量占比达到该值即要求更长的片段才算"可定位"（防巧合命中）。
NUMERIC_CHAR_RATIO: float = 0.80

#: 低信息量残差的最小片段长度。
NUMERIC_PIECE_MIN_LEN: int = 8

#: 非重叠 occurrence 分配的状态空间预算；耗尽即保守判 `unexplained`。
MAX_ALIGNMENT_SEARCH_NODES: int = 20000

#: 残差分类的严重度序（升序；越靠后越严重）。**复用** `schema` 的唯一一份定义，
#: 本模块不重写第二份残差类别序（重写就会分叉）。
RESIDUE_CLASSES: tuple = tuple(RESIDUE_CLASS_SEVERITY)

#: `engine_artifact` 的**封闭白名单**。每一项都必须有明确、**版本化**的转换依据，
#: 且依据只来自本仓库已定义的归一化原语：
#:
#: - `blank`：`tight()` 已把空白差异吸收（依据 `NORMALIZATION_VERSION`）；
#: - `invisible_codepoint`：残差全部由 `normalization.INVISIBLE_CODEPOINTS`
#:   （软连字符 U+00AD、零宽 U+200B/C/D、BOM U+FEFF）构成。
#:
#: **不在此白名单里的一律 `unexplained`**。TS2 最终关闭轮**删除** `width_fold`
#: （全角/半角等价）：旧实现把残差 `fold()` 之后在**本页任意位置**搜一次，命中即判
#: engine artifact。那是事后、全页任意搜索式消除残差 —— 不得恢复。
ENGINE_ARTIFACT_RULES: tuple = ("blank", "invisible_codepoint")

#: 记录拒绝原因码：量化会把"低于阈值"的块抬到阈值之上，见模块 docstring。
#: 该码是 `schema.ALIGNMENT_REFUSAL_REASONS` 里的成员（拒绝原因集合在公共类型层
#: 封闭定义，本模块只引用，不另立一套）。
RECORD_REFUSAL_QUANTIZATION_BOUNDARY: str = "quantization_boundary_refused"


class AlignmentError(SchemaValidationError):
    """对齐输入 / 版式自洽性错误（fail-closed，不降级、不猜偏移）。"""


# ---------------------------------------------------------------------------
# 输入与结果类型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceBlockInput:
    """一个待对齐的存量 `EvidenceBlock` 的**完整身份**输入（版本化、typed）。

    身份**一律重算，不采信调用方自报**：`content_hash` 与 `evidence_block_id` 由本类
    在构造时用 `evidence.ids` 的正式接口（`content_hash()` / `make_evidence_id()`）
    重算并逐字符比对，不一致 / 缺失 / 空值一律 fail-closed。因此"同文同页但伪造
    `evidence_id`"或"篡改文本 / structured payload"都**构造不出**输入对象；
    公司 / 文档 / 版本身份再由 `align_block` / `align_evidence_set` 与 `PageLayout`
    严格核对（跨公司、跨文档、跨版本一律拒绝）。

    `text` 是 Evidence 的**原始**文本；本模块内部按 TS2 的度量做 `tight()` 归一
    （`block_char_length` = `len(tight(text))`），因此 `char_map` / 残差坐标都是
    **归一后块坐标**。
    """

    company_id: str
    document_id: str
    document_version: str
    evidence_set_version: str
    evidence_block_id: str
    content_hash: str
    evidence_type: str
    page_number: int
    block_index: int
    text: str
    structured_payload: dict | None = None
    input_version: str = V.EVIDENCE_BLOCK_INPUT_VERSION

    def __post_init__(self) -> None:
        if self.input_version != V.EVIDENCE_BLOCK_INPUT_VERSION:
            raise AlignmentError(
                f"input_version 必须为 {V.EVIDENCE_BLOCK_INPUT_VERSION!r}，得到 "
                f"{self.input_version!r}；输入契约不同即不是同一个块（fail-closed）")
        for name in ("company_id", "document_id", "document_version",
                     "evidence_set_version", "evidence_block_id", "content_hash",
                     "evidence_type"):
            value = getattr(self, name)
            if not isinstance(value, str) or value == "":
                raise AlignmentError(f"{name} 必须为非空字符串，得到 {value!r}")
        for name in ("page_number", "block_index"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool):
                raise AlignmentError(f"{name} 必须为 int，得到 {v!r}")
        if self.page_number < 1:
            raise AlignmentError(f"page_number 必须从 1 开始，得到 {self.page_number}")
        if self.block_index < 0:
            raise AlignmentError(f"block_index 不得小于 0，得到 {self.block_index}")
        if not isinstance(self.text, str):
            raise AlignmentError(f"text 必须为字符串，得到 {type(self.text).__name__}")
        if self.structured_payload is not None \
                and not isinstance(self.structured_payload, dict):
            raise AlignmentError(
                f"structured_payload 必须为 dict 或 None，得到 "
                f"{type(self.structured_payload).__name__}")
        self.assert_identity()

    def recomputed_content_hash(self) -> str:
        """按 `evidence.ids.content_hash()` 重算的内容哈希（正文 + payload）。"""
        return evidence_content_hash(self.text, self.structured_payload)

    def recomputed_evidence_id(self) -> str:
        """按 `evidence.ids.make_evidence_id()` 重算的 evidence_id。"""
        return make_evidence_id(
            self.company_id, self.document_id, self.document_version,
            self.evidence_set_version, self.page_number, self.block_index,
            self.recomputed_content_hash())

    def assert_identity(self) -> None:
        """重算身份并要求与自报逐字符一致（伪造 / 篡改一律 fail-closed）。"""
        expected_hash = self.recomputed_content_hash()
        if self.content_hash != expected_hash:
            raise AlignmentError(
                f"content_hash 与按 evidence.ids.content_hash() 重算的值不一致："
                f"{self.content_hash!r} != {expected_hash!r}；不得采信调用方自报的"
                f"内容身份（fail-closed）")
        expected_id = self.recomputed_evidence_id()
        if self.evidence_block_id != expected_id:
            raise AlignmentError(
                f"evidence_id 与按 evidence.ids.make_evidence_id() 重算的值不一致："
                f"{self.evidence_block_id!r} != {expected_id!r}；不得采信调用方自报的"
                f"证据身份（fail-closed）")

    def assert_bound_to(self, layout: PageLayout) -> None:
        """公司 / 文档 / 版本必须与 `PageLayout` **严格相等**（缺失 / 空值 / 不一致
        一律 fail-closed）。只核对文档号会放过"同一份版式挂到另一家公司"的错配。"""
        for name, mine, theirs in (
                ("company_id", self.company_id, layout.company_id),
                ("document_id", self.document_id, layout.document_id),
                ("document_version", self.document_version,
                 layout.document_version)):
            if mine != theirs:
                raise AlignmentError(
                    f"{name} 与 PageLayout 不一致：{mine!r} != {theirs!r}；"
                    f"Evidence 不得跨公司 / 跨文档 / 跨版本对齐（fail-closed）")


# ---------------------------------------------------------------------------
# §八（P1-F）：正式**集合入口**的权威来源身份（EvidenceSetSnapshot）
# ---------------------------------------------------------------------------
#
# 单块输入的身份门（`EvidenceBlockInput`）只能证明"这一块自洽"，不能证明"这一块
# 属于本文档**当前**的整份证据集"：调用方提交 1 个块、提交 292 个块里去掉中间一个、
# 或者补一个"数学上自洽"的伪块，`align_evidence_set` 原先都无从分辨。因此正式集合
# 入口要求调用方交出**受信任只读 gateway 注入**的 `EvidenceSetSnapshot`，并逐成员
# 精确比对。
#
# 三条边界（不得放宽）：
#
# 1. **本模块不查库**。Snapshot 由外部只读 gateway（`evidence.store` 的 `?mode=ro`
#    只读原语）构造后注入；aligner 只做比对，因此"对齐结论"与"谁是权威成员"两个
#    事实来源相互独立；
# 2. **比对是精确相等**，不是包含：少一个、多一个、重复、type / hash / page / block
#    任一不符、非 current、身份（公司 / 文档 / 版本 / set）不符，全部 fail-closed；
# 3. **Snapshot 身份进入集合级身份**：`EvidenceSetAlignment` 携带 snapshot 指纹与
#    版本，下游（终态报告 / 产物 / 依赖指纹）因此能分辨"旧的自报输入"与"新权威
#    Snapshot 输入"，不必等 run manifest 才看出来。

#: `evidence_type` 的正式**封闭集合**：复用 Evidence 架构的冻结枚举
#: （`evidence.schema.EVIDENCE_TYPES`），本模块不另立第二套类型表。
EVIDENCE_TYPE_CLOSED_SET: frozenset = frozenset(EVIDENCE_TYPES)


@dataclass(frozen=True)
class EvidenceSetMember:
    """权威证据集里**一个成员的不可变身份**（不含正文）。

    只承载 §八 要求的五项：`evidence_id` / `content_hash` / `evidence_type` /
    `page_number` / `block_index`。正文与 payload **不**进入快照 —— 快照是成员身份
    的清单，不是第二份证据库（也因此不存在"快照文本与库文本不一致"的隐患）。
    """

    evidence_id: str
    content_hash: str
    evidence_type: str
    page_number: int
    block_index: int

    def __post_init__(self) -> None:
        for name in ("evidence_id", "content_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or value == "":
                raise AlignmentError(
                    f"EvidenceSetMember.{name} 必须为非空字符串，得到 {value!r}；"
                    f"身份缺项不得被静默补齐（fail-closed）")
        if self.evidence_type not in EVIDENCE_TYPE_CLOSED_SET:
            raise AlignmentError(
                f"evidence_type 必须属于正式封闭集合 "
                f"{sorted(EVIDENCE_TYPE_CLOSED_SET)}，得到 {self.evidence_type!r}")
        for name in ("page_number", "block_index"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise AlignmentError(f"EvidenceSetMember.{name} 必须为 int，得到 {value!r}")
        if self.page_number < 1:
            raise AlignmentError(
                f"EvidenceSetMember.page_number 必须从 1 开始，得到 {self.page_number}")
        if self.block_index < 0:
            raise AlignmentError(
                f"EvidenceSetMember.block_index 不得小于 0，得到 {self.block_index}")

    @property
    def sort_key(self) -> tuple:
        """规范顺序键：`(page_number, block_index)`，与对齐结果的排序口径一致。"""
        return (self.page_number, self.block_index)

    @property
    def identity(self) -> tuple:
        """五项身份的**规范元组**（精确比对用的唯一形态）。"""
        return (self.evidence_id, self.content_hash, self.evidence_type,
                self.page_number, self.block_index)

    def to_dict(self) -> dict:
        return {"evidence_id": self.evidence_id, "content_hash": self.content_hash,
                "evidence_type": self.evidence_type,
                "page_number": self.page_number, "block_index": self.block_index}


def snapshot_fingerprint(*, company_id: str, document_id: str,
                         document_version: str, evidence_set_version: str,
                         status: str, gateway_version: str,
                         members: tuple) -> str:
    """`EvidenceSetSnapshot` 的确定性指纹（身份 + 状态 + gateway 版本 + 全成员身份）。

    成员按**规范顺序**入指纹（调用方顺序不影响结果），因此指纹可以当作"这份证据集
    的全集身份"使用：任何成员增删、身份改动、set / 文档 / 版本变化都会改变它。
    """
    ordered = sorted(members, key=lambda m: m.sort_key)
    return sha256_canonical({
        "snapshot_version": V.EVIDENCE_SET_SNAPSHOT_VERSION,
        "company_id": company_id, "document_id": document_id,
        "document_version": document_version,
        "evidence_set_version": evidence_set_version,
        "status": status,
        "gateway_version": gateway_version,
        "block_count": len(ordered),
        "members": [m.identity for m in ordered],
    })


@dataclass(frozen=True)
class EvidenceSetSnapshot:
    """**权威证据集快照**：本文档 current 证据集的全部成员身份（§八）。

    构造即校验（因此 `from_dict` 式反序列化同样 fail-closed）：版本、身份字段、
    状态、成员类型 / 规范顺序 / 唯一性、`block_count` 与成员数一致、指纹可重算。
    成员**必须**已经按 `(page_number, block_index)` 升序且互不重复 —— 指纹依赖顺序，
    容忍乱序等于让同一份证据集有两个指纹。
    """

    company_id: str
    document_id: str
    document_version: str
    evidence_set_version: str
    status: str
    members: tuple
    block_count: int
    fingerprint: str
    gateway_version: str
    snapshot_version: str = V.EVIDENCE_SET_SNAPSHOT_VERSION

    def __post_init__(self) -> None:
        if self.snapshot_version != V.EVIDENCE_SET_SNAPSHOT_VERSION:
            legacy = V.legacy_versions("EVIDENCE_SET_SNAPSHOT_VERSION")
            if self.snapshot_version in legacy:
                raise AlignmentError(
                    f"EvidenceSetSnapshot 的旧 wire 版本 {self.snapshot_version!r} "
                    f"须经显式迁移/重算，不得静默按 "
                    f"{V.EVIDENCE_SET_SNAPSHOT_VERSION!r} 解释（fail-closed）")
            raise AlignmentError(
                f"EvidenceSetSnapshot.snapshot_version 必须为 "
                f"{V.EVIDENCE_SET_SNAPSHOT_VERSION!r}，得到 {self.snapshot_version!r}")
        for name in ("company_id", "document_id", "document_version",
                     "evidence_set_version", "gateway_version"):
            value = getattr(self, name)
            if not isinstance(value, str) or value == "":
                raise AlignmentError(
                    f"EvidenceSetSnapshot.{name} 必须为非空字符串，得到 {value!r}；"
                    f"权威快照的身份缺项不得由调用方补齐（fail-closed）")
        if self.status not in EVIDENCE_SET_STATUSES:
            raise AlignmentError(
                f"EvidenceSetSnapshot.status 必须属于 {EVIDENCE_SET_STATUSES}，"
                f"得到 {self.status!r}")
        if not isinstance(self.members, tuple):
            raise AlignmentError(
                f"EvidenceSetSnapshot.members 必须为 tuple，得到 "
                f"{type(self.members).__name__}")
        if not self.members:
            raise AlignmentError(
                "EvidenceSetSnapshot.members 不得为空：空证据集无法构成『本文档当前"
                "证据集』，也不得把『0 条终态』表述为对齐通过（fail-closed）")
        for member in self.members:
            if not isinstance(member, EvidenceSetMember):
                raise AlignmentError(
                    f"members 必须为 EvidenceSetMember，得到 {type(member).__name__}")
        keys = [m.sort_key for m in self.members]
        if keys != sorted(keys):
            raise AlignmentError(
                "EvidenceSetSnapshot.members 必须按 (page_number, block_index) "
                "升序排列（指纹依赖规范顺序）")
        if len(set(keys)) != len(keys):
            raise AlignmentError("EvidenceSetSnapshot.members 不得重复 (页, 块)")
        ids = [m.evidence_id for m in self.members]
        if len(set(ids)) != len(ids):
            raise AlignmentError(
                f"EvidenceSetSnapshot.members 不得重复 evidence_id"
                f"（重复出现即无法判断提交的是哪一份）")
        if not isinstance(self.block_count, int) or isinstance(self.block_count, bool) \
                or self.block_count != len(self.members):
            raise AlignmentError(
                f"EvidenceSetSnapshot.block_count ({self.block_count!r}) 必须等于成员数 "
                f"{len(self.members)}（守恒：身份清单与块数不得分叉）")
        expected = self.recomputed_fingerprint()
        if self.fingerprint != expected:
            raise AlignmentError(
                f"EvidenceSetSnapshot.fingerprint 与按全成员身份重算的值不一致："
                f"{self.fingerprint!r} != {expected!r}；权威快照指纹不得自报")

    def recomputed_fingerprint(self) -> str:
        return snapshot_fingerprint(
            company_id=self.company_id, document_id=self.document_id,
            document_version=self.document_version,
            evidence_set_version=self.evidence_set_version, status=self.status,
            gateway_version=self.gateway_version, members=self.members)

    def member_identities(self) -> tuple:
        """全成员的五项身份（规范顺序），精确比对用。"""
        return tuple(m.identity for m in self.members)

    def assert_current(self) -> None:
        """非 `current` 的证据集不得作为正式对齐输入（§八：非 current 拒绝）。"""
        if self.status != "current":
            raise AlignmentError(
                f"EvidenceSetSnapshot.status 为 {self.status!r} 而非 'current'；"
                f"只有 current 证据集可以作为正式对齐输入（fail-closed）")

    def assert_bound_to(self, layout: PageLayout) -> None:
        """公司 / 文档 / 版本必须与 `PageLayout` 严格相等（错配不得对齐）。"""
        for name, mine, theirs in (
                ("company_id", self.company_id, layout.company_id),
                ("document_id", self.document_id, layout.document_id),
                ("document_version", self.document_version,
                 layout.document_version)):
            if mine != theirs:
                raise AlignmentError(
                    f"EvidenceSetSnapshot.{name} 与 PageLayout 不一致："
                    f"{mine!r} != {theirs!r}；权威快照与本文档版式不是同一份"
                    f"（fail-closed）")

    def identity(self) -> dict:
        """快照身份（进入集合级身份 / 依赖指纹 / 产物）。**不含**成员明细。"""
        return {
            "snapshot_version": self.snapshot_version,
            "gateway_version": self.gateway_version,
            "company_id": self.company_id, "document_id": self.document_id,
            "document_version": self.document_version,
            "evidence_set_version": self.evidence_set_version,
            "status": self.status,
            "block_count": self.block_count,
            "fingerprint": self.fingerprint,
        }

    def to_dict(self) -> dict:
        out = self.identity()
        out["members"] = [m.to_dict() for m in self.members]
        return out


class EvidenceSetGateway(Protocol):
    """**受信任只读** Evidence gateway 的接口（aligner 只消费它的输出）。

    具体实现由调用方提供（生产/评测用 `evidence.store` 的 `?mode=ro` 只读原语构造
    自己文档的 current 证据集），因此对齐核心既不 import 数据库层、也无法被诱导去
    "自己查一个更方便的证据集"。
    """

    gateway_version: str

    def load_snapshot(self, *, company_id: str, document_id: str,
                      document_version: str) -> EvidenceSetSnapshot:
        """返回该文档版本的 **current** 证据集快照（缺 set / 非 current 即拒）。"""


def snapshot_from_gateway(gateway, *, company_id: str, document_id: str,
                          document_version: str) -> EvidenceSetSnapshot:
    """从只读 gateway 注入权威快照，并核对 gateway 自报版本与快照记录一致。

    这是**唯一**推荐的快照获取方式：调用方不自行拼成员清单，也就无法"顺手"提交一个
    与库里不同的集合。
    """
    declared = getattr(gateway, "gateway_version", None)
    if not isinstance(declared, str) or declared == "":
        raise AlignmentError(
            f"Evidence gateway 必须声明非空 gateway_version，得到 {declared!r}；"
            f"无法分辨权威成员的来源版本（fail-closed）")
    loader = getattr(gateway, "load_snapshot", None)
    if not callable(loader):
        raise AlignmentError("Evidence gateway 必须实现 load_snapshot(...)")
    snapshot = loader(company_id=company_id, document_id=document_id,
                      document_version=document_version)
    if not isinstance(snapshot, EvidenceSetSnapshot):
        raise AlignmentError(
            f"gateway.load_snapshot 必须返回 EvidenceSetSnapshot，得到 "
            f"{type(snapshot).__name__}")
    if snapshot.gateway_version != declared:
        raise AlignmentError(
            f"快照记录的 gateway_version {snapshot.gateway_version!r} 与 gateway 自报的 "
            f"{declared!r} 不一致；来源版本不可核验即不得作为权威成员")
    for name, value in (("company_id", company_id), ("document_id", document_id),
                        ("document_version", document_version)):
        if getattr(snapshot, name) != value:
            raise AlignmentError(
                f"gateway 返回的快照 {name} 为 {getattr(snapshot, name)!r}，"
                f"与请求的 {value!r} 不一致（fail-closed）")
    return snapshot


def _member_of(block: EvidenceBlockInput) -> EvidenceSetMember:
    """把提交的块投影成**成员身份**（不含正文），用于与快照精确比对。"""
    return EvidenceSetMember(
        evidence_id=block.evidence_block_id, content_hash=block.content_hash,
        evidence_type=block.evidence_type, page_number=block.page_number,
        block_index=block.block_index)


def assert_members_exact(submitted: tuple, snapshot: EvidenceSetSnapshot) -> None:
    """要求**提交成员与权威快照精确相等**（少 / 多 / 重复 / 字段不符一律拒绝）。

    比对口径：先把两侧投影成规范顺序的五项身份元组，再逐位置比对，因此
    "数量相同但内容不同"（例如把一个块换成另一个数学自洽的伪块）也会被逐字段指出，
    而不是被总数掩盖。
    """
    authority = snapshot.members
    if len(submitted) != len(authority):
        missing = [m.sort_key for m in authority
                   if m.sort_key not in {s.sort_key for s in submitted}]
        extra = [m.sort_key for m in submitted
                 if m.sort_key not in {m2.sort_key for m2 in authority}]
        raise AlignmentError(
            f"提交成员数与权威快照不相等：提交 {len(submitted)}，快照 {len(authority)}"
            f"（block_count={snapshot.block_count}）；缺失 {missing}，多出 {extra}；"
            f"正式集合入口要求**整份**证据集，不得只对齐一部分（fail-closed）")
    problems = []
    for got, want in zip(submitted, authority):
        if got.sort_key != want.sort_key:
            problems.append(f"位置 {(got.page_number, got.block_index)} 处应为 "
                            f"{(want.page_number, want.block_index)}（成员序列不符）")
            continue
        for field in ("evidence_id", "content_hash", "evidence_type"):
            if getattr(got, field) != getattr(want, field):
                problems.append(
                    f"{(got.page_number, got.block_index)} 的 {field} 与权威记录不一致："
                    f"{getattr(got, field)!r} != {getattr(want, field)!r}")
    if problems:
        raise AlignmentError(
            "提交成员与权威 EvidenceSet 快照不一致（前若干项）："
            + "；".join(problems[:8])
            + "；不得用调用方自报的身份替换权威成员（fail-closed）")


@dataclass(frozen=True)
class ResidueSegment:
    """块内一段**未被顺序匹配覆盖**的残差及其可核实归因。"""

    start: int
    end: int
    residue_class: str
    subkind: str
    reason: str
    text: str


@dataclass(frozen=True)
class BlockAlignment:
    """单个 `EvidenceBlock` 的对齐结论（含判定所需的全部可复核事实）。

    - `verdict` / `is_citable` 来自**终态**（若产出）或 fail-closed 兜底；
    - `exact_coverage` 是**未量化**的顺序覆盖率（与 TS2 度量逐位一致）；
    - 每个块**恰好一个**正式终态：`record`（`TextAlignmentRecord`）或
      `refusal_record`（`AlignmentRefusalRecord`）；`terminal` 属性给出那一个。
      两者的**当前** wire 版本分别由 `versions.ALIGN_SCHEMA_VERSION` /
      `versions.ALIGN_REFUSAL_SCHEMA_VERSION` 给出（本处不写版本字面量），
      `terminal` 只按"哪个字段非 None"判定，不按版本号判定。
      `record_refusal` 是原因码（拒绝时非空），供快速筛选。
    """

    evidence_block_id: str
    page_number: int
    block_index: int
    evidence_set_version: str
    page_layout_id: str
    aligner_version: str
    block_char_length: int
    matched_chars: int
    unmatched_chars: int
    exact_coverage: float
    residue_segments: tuple = ()
    residue_char_counts: tuple = ()
    residue_subkind_counts: tuple = ()
    residue_class: str = "none"
    verdict_residue_class: str = "none"
    verdict: str = "unaligned"
    is_citable: bool = False
    record: TextAlignmentRecord | None = None
    record_refusal: str | None = None
    refusal_record: AlignmentRefusalRecord | None = None
    char_map: tuple = ()
    unlocatable_chars: int = 0
    lenient_explained_chars: int = 0
    unexplained_duplicate_chars: int = 0
    unexplained_search_exhausted_chars: int = 0

    @property
    def terminal(self) -> TextAlignmentRecord | AlignmentRefusalRecord | None:
        """该块的**唯一**正式终态（记录或拒绝记录）；两者同时存在不可能构造出来。"""
        if self.record is not None and self.refusal_record is not None:
            raise AlignmentError(
                f"块 {(self.page_number, self.block_index)} 同时存在记录与拒绝记录；"
                f"每个块必须且只能有一个正式终态")
        return self.record if self.record is not None else self.refusal_record

    @property
    def has_terminal(self) -> bool:
        return self.terminal is not None

    @property
    def has_unexplained(self) -> bool:
        return dict(self.residue_char_counts).get("unexplained", 0) > 0

    @property
    def unexplained_chars(self) -> int:
        return dict(self.residue_char_counts).get("unexplained", 0)

    @property
    def empty_block(self) -> bool:
        return self.block_char_length == 0

    @property
    def quantization_boundary_refused(self) -> bool:
        return self.record_refusal == RECORD_REFUSAL_QUANTIZATION_BOUNDARY

    def to_dict(self) -> dict:
        """可复核的 JSON 视图（**不含**记录自身的 wire 形式）。"""
        return {
            "evidence_block_id": self.evidence_block_id,
            "page_number": self.page_number,
            "block_index": self.block_index,
            "evidence_set_version": self.evidence_set_version,
            "page_layout_id": self.page_layout_id,
            "aligner_version": self.aligner_version,
            "block_char_length": self.block_char_length,
            "matched_chars": self.matched_chars,
            "unmatched_chars": self.unmatched_chars,
            "exact_coverage": self.exact_coverage,
            "coverage": quantize(self.exact_coverage),
            "residue_class": self.residue_class,
            "verdict_residue_class": self.verdict_residue_class,
            "verdict": self.verdict,
            "is_citable": self.is_citable,
            "record_refusal": self.record_refusal,
            "terminal_schema_type": (None if self.terminal is None
                                     else type(self.terminal).__name__),
            "alignment_id": None if self.record is None else self.record.alignment_id,
            "refusal_id": (None if self.refusal_record is None
                           else self.refusal_record.refusal_id),
            "has_unexplained": self.has_unexplained,
            "unexplained_chars": self.unexplained_chars,
            "unlocatable_chars": self.unlocatable_chars,
            "lenient_explained_chars": self.lenient_explained_chars,
            "unexplained_duplicate_chars": self.unexplained_duplicate_chars,
            "unexplained_search_exhausted_chars":
                self.unexplained_search_exhausted_chars,
            "residue_char_counts": dict(sorted(self.residue_char_counts)),
            "residue_subkind_counts": dict(sorted(self.residue_subkind_counts)),
            "residue_segments": [
                {"start": s.start, "end": s.end, "class": s.residue_class,
                 "subkind": s.subkind, "reason": s.reason, "length": len(s.text)}
                for s in self.residue_segments],
            "char_map": [list(seg) for seg in self.char_map],
        }


@dataclass(frozen=True)
class EvidenceSetAlignment:
    """一组（同一文档 / 同一 evidence set）块的正式对齐结论。

    `snapshot` 是**权威全集身份**（`EvidenceSetSnapshot`）：它让"本次对齐到底消费了
    哪一份证据集"成为结果自身的属性，而不是调用方的一句自报 —— §九 要求正式 terminal
    身份能分辨"旧的自报输入"与"新的权威 Snapshot 输入"，这就是那一半。
    """

    page_layout_id: str
    evidence_set_version: str
    aligner_version: str
    blocks: tuple
    summary: dict
    snapshot: EvidenceSetSnapshot

    @property
    def snapshot_identity(self) -> dict:
        """权威快照身份（进入产物 / 依赖指纹 / 终态报告）。"""
        return self.snapshot.identity()


# ---------------------------------------------------------------------------
# 残差分类的汇总口径（TS2 唯一实现；报告与门槛判定共用）
# ---------------------------------------------------------------------------

def dominant_residue_class(counts: dict) -> str:
    """按**字符数**取描述性主类（只用于报告阅读，不用于判定）。

    平局时按 `RESIDUE_CLASSES` 的严重度序、再按类别名排序，因此结果确定。
    """
    if not counts:
        return "none"
    order = {name: i for i, name in enumerate(RESIDUE_CLASSES)}
    return sorted(counts.items(),
                  key=lambda kv: (-kv[1], order.get(kv[0], 99), kv[0]))[0][0]


def verdict_residue_class(counts: dict) -> str:
    """喂给 `compute_alignment_verdict` 的**块级门槛类**（判定一律用它）。

    与 `dominant_residue_class()` 不同：只要块内存在任何 `unexplained` 残差，门槛类
    就是 `unexplained`；否则才取描述性主类。否则"描述性主类掩盖未解释残差"的块会在
    `coverage >= ALIGN_MIN` 时被误升为 `aligned`——业务规则禁止。
    """
    if counts.get("unexplained"):
        return "unexplained"
    return dominant_residue_class(counts)


# ---------------------------------------------------------------------------
# 覆盖率与残差（TS2 度量的逐条提取）
# ---------------------------------------------------------------------------

def _page_of(layout: PageLayout, page_number: int):
    for page in layout.pages:
        if page.page_number == page_number:
            return page
    return None


def page_layout_text(layout: PageLayout, page_number: int) -> str:
    """`tight(同页 PageLayout 全部行文本)` —— 计划 §3.1 的对齐度量形态。"""
    page = _page_of(layout, page_number)
    if page is None:
        return ""
    return tight("".join(line.text for line in page.lines))


def page_lines(layout: PageLayout, page_number: int) -> list:
    page = _page_of(layout, page_number)
    return [] if page is None else list(page.lines)


def page_text_spans(lines: list) -> tuple:
    """该页 `tight` 拼接文本 + 每条**非空**真实版式行在其中的 `[start, end)`。"""
    pieces = []
    spans = []
    cursor = 0
    for line in lines:
        flat = tight(line.text)
        if flat:
            spans.append((line.line_index, cursor, cursor + len(flat)))
            cursor += len(flat)
        pieces.append(flat)
    return "".join(pieces), spans


def _line_of_offset(offset: int, spans: list):
    for line_index, start, end in spans:
        if start <= offset < end:
            return line_index
    return None


def coverage_record(block_text: str, page_text: str) -> dict:
    """顺序连续片段覆盖率 + 残差片段（块侧坐标）。"""
    matcher = difflib.SequenceMatcher(None, block_text, page_text, autojunk=False)
    blocks = matcher.get_matching_blocks()
    matched = sum(b.size for b in blocks)
    coverage = matched / len(block_text) if block_text else 0.0
    residues = []
    cursor = 0
    for b in blocks:
        if b.a > cursor:
            residues.append({"start": cursor, "end": b.a,
                             "text": block_text[cursor:b.a]})
        cursor = b.a + b.size
    if cursor < len(block_text):
        residues.append({"start": cursor, "end": len(block_text),
                         "text": block_text[cursor:]})
    return {
        "coverage": coverage,
        "matched_chars": matched,
        "block_len": len(block_text),
        "page_len": len(page_text),
        "matching_blocks": tuple(blocks),
        "residues": residues,
        "unmatched_chars": sum(len(r["text"]) for r in residues),
    }


def numeric_like_ratio(text: str) -> float:
    """数字/千分位/日期等低信息量字符的占比。"""
    if not text:
        return 0.0
    return sum(1 for ch in text if ch in NUMERIC_LIKE_CHARS) / len(text)


def _min_piece_len(compact: str) -> int:
    """低信息量残差要求更长的片段才算"可定位"（防巧合命中）。"""
    if numeric_like_ratio(compact) >= NUMERIC_CHAR_RATIO:
        return NUMERIC_PIECE_MIN_LEN
    return COLUMN_REORDER_MIN_PIECE_LEN


def _occurrences(piece: str, page_text: str, cache: dict) -> tuple:
    """该片段在页面文本中的**全部**真实 occurrence（升序、去重，按片段缓存）。"""
    found = cache.get(piece)
    if found is not None:
        return found
    offsets = []
    start = page_text.find(piece)
    while start != -1:
        offsets.append(start)
        start = page_text.find(piece, start + 1)
    found = tuple(offsets)
    cache[piece] = found
    return found


def _partitionable(compact: str, page_text: str, cache: dict,
                   min_piece: int) -> tuple:
    """能否把 `compact` **完整**切成若干段、每段都在同页出现过（只为诊断措辞）。

    这是"存在注入式非重叠映射"的**必要**条件（不是充分条件）：它忽略"同一
    occurrence 被重复消费"与"片段相互重叠"，因此**绝不**单独作为解释依据。
    """
    length = len(compact)
    reachable = [False] * (length + 1)
    reachable[0] = True
    previous = [None] * (length + 1)
    for index in range(length):
        if not reachable[index]:
            continue
        upper = min(MAX_PIECE_LEN, length - index)
        for size in range(upper, min_piece - 1, -1):
            end = index + size
            if reachable[end]:
                continue
            if _occurrences(compact[index:end], page_text, cache):
                reachable[end] = True
                previous[end] = (index, size)
    if not reachable[length]:
        return False, []
    pieces = []
    cursor = length
    while cursor > 0:
        start, size = previous[cursor]
        pieces.append(compact[start:cursor])
        cursor = start
    pieces.reverse()
    return True, pieces


def _nonoverlapping_assignment(compact: str, page_text: str, spans: list,
                               cache: dict, min_piece: int) -> dict:
    """求一份**注入式、两两不重叠**的 occurrence 分配（存在性 + 见证）。

    约束（`DESIGN_V2.md`：字符范围不得重叠冲突）：

    - 每个片段必须绑定到页面文本中一个**真实** occurrence；
    - 被选中的源区间**两两不重叠**，同一个 occurrence **不得被重复消费**；
    - 允许片段顺序与块内顺序不同（列顺序重排），因此不做顺序约束；
    - 片段长度限 `[min_piece, MAX_PIECE_LEN]`，且必须**完整覆盖** `compact`。

    搜索是确定性的：片段长度**由长到短**、同一长度内 occurrence **由前到后**，
    不使用任何 dict 迭代顺序。搜索是**保守**的 —— 找不到就返回失败，绝不猜测字符
    偏移；节点预算耗尽时返回 `exhausted=True`（调用方必须判 unexplained）。
    """
    length = len(compact)
    state = {"nodes": 0, "exhausted": False}
    failed: set = set()
    chosen: list = []

    def dfs(position: int, used: list) -> bool:
        if position == length:
            return True
        if state["nodes"] >= MAX_ALIGNMENT_SEARCH_NODES:
            state["exhausted"] = True
            return False
        key = (position, tuple(used))
        if key in failed:
            return False
        state["nodes"] += 1
        upper = min(MAX_PIECE_LEN, length - position)
        for size in range(upper, min_piece - 1, -1):
            piece = compact[position:position + size]
            for offset in _occurrences(piece, page_text, cache):
                low, high = offset, offset + size
                if any(not (high <= a or low >= b) for a, b in used):
                    continue  # 与已选源区间重叠 -> 该 occurrence 已被消费
                chosen.append({
                    "text": piece, "length": size,
                    "block_start": position, "block_end": position + size,
                    "page_start": low, "page_end": high,
                    "offset": low,
                    "line_index": _line_of_offset(low, spans),
                })
                used.append((low, high))
                used.sort()
                if dfs(position + size, used):
                    return True
                used.remove((low, high))
                chosen.pop()
                if state["exhausted"]:
                    return False
        failed.add(key)
        return False

    ok = dfs(0, [])
    if not ok:
        return {"ok": False, "pieces": [], "exhausted": state["exhausted"],
                "nodes": state["nodes"]}
    return {"ok": True, "pieces": list(chosen), "exhausted": False,
            "nodes": state["nodes"]}


def _occurrence_deficit(pieces: list, page_text: str, cache: dict) -> dict:
    """重复文本的 occurrence 缺口诊断（`{"片段": {"needed": n, "available": m}}`）。"""
    needed: dict = {}
    for piece in pieces:
        needed[piece] = needed.get(piece, 0) + 1
    deficit = {}
    for piece, count in needed.items():
        available = len(_occurrences(piece, page_text, cache))
        if count > available:
            deficit[short_fragment(piece, 20)] = {"needed": count,
                                                  "available": available}
    return dict(sorted(deficit.items()))


def displaced_evidence(compact: str, page_text: str, spans: list,
                       min_piece: int | None = None,
                       cache: dict | None = None) -> dict:
    """残差能否**完整**绑定到同页一组**两两不重叠**的真实 occurrence。

    1. 枚举每个片段在页面里的**全部** occurrence；
    2. 求一份**注入式非重叠**分配（允许列顺序重排）；
    3. **无法建立**这样的映射时 `leftover` 保持为整段残差、`pieces` 为空，
       调用方必须判 `unexplained`——"alignment 不能唯一确认时 fail-closed，
       禁止猜测字符偏移"。
    """
    length = len(compact)
    if min_piece is None:
        min_piece = _min_piece_len(compact)
    if cache is None:
        cache = {}
    if length > MAX_DISPLACEMENT_DP_LEN:
        return {"pieces": [], "leftover": compact, "source_lines": [],
                "longest": 0, "too_long": True, "min_piece_len": min_piece,
                "no_injective_assignment": True, "search_exhausted": False,
                "reason": "too_long"}
    partitionable, draft = _partitionable(compact, page_text, cache, min_piece)
    if not partitionable:
        return {"pieces": [], "leftover": compact, "source_lines": [],
                "longest": 0, "min_piece_len": min_piece,
                "locatable_upper_bound": _locatable_positions(
                    compact, page_text, cache, min_piece),
                "no_injective_assignment": False, "search_exhausted": False,
                "reason": "no_partition", "occurrence_deficit": {}}
    found = _nonoverlapping_assignment(compact, page_text, spans, cache, min_piece)
    if not found["ok"]:
        return {"pieces": [], "leftover": compact, "source_lines": [],
                "longest": 0, "min_piece_len": min_piece,
                "locatable_upper_bound": _locatable_positions(
                    compact, page_text, cache, min_piece),
                "no_injective_assignment": True,
                "search_exhausted": found["exhausted"],
                "reason": ("search_exhausted" if found["exhausted"]
                           else "no_injective_assignment"),
                "search_nodes": found["nodes"],
                "occurrence_deficit": _occurrence_deficit(draft, page_text, cache)}
    pieces = found["pieces"]
    located_lines = sorted({p["line_index"] for p in pieces
                            if p["line_index"] is not None})
    return {
        "pieces": pieces,
        "leftover": "",
        "source_lines": located_lines,
        "longest": max((p["length"] for p in pieces), default=0),
        "min_piece_len": min_piece,
        "no_injective_assignment": False,
        "search_exhausted": False,
        "reason": "assigned",
        "search_nodes": found["nodes"],
        "occurrence_deficit": {},
    }


def _locatable_positions(compact: str, page_text: str, cache: dict,
                         min_piece: int) -> int:
    """**至多**多少个字符能落在某个同页可定位片段里（真判定的**上界**，仅诊断）。"""
    length = len(compact)
    marked = [False] * length
    for index in range(length):
        upper = min(MAX_PIECE_LEN, length - index)
        for size in range(upper, min_piece - 1, -1):
            if _occurrences(compact[index:index + size], page_text, cache):
                for position in range(index, index + size):
                    marked[position] = True
                break
    return sum(1 for flag in marked if flag)


def _lenient_only_explained(compact: str, page_text: str, spans: list) -> bool:
    """宽松片段长度下能否被整体解释（只用于敏感性对照，不参与正式分类）。"""
    evidence = displaced_evidence(compact, page_text, spans,
                                 min_piece=COLUMN_REORDER_MIN_PIECE_LEN)
    pieces = evidence["pieces"]
    return bool(evidence["leftover"] == "" and pieces and (
        len(pieces) >= COLUMN_REORDER_MIN_PIECES
        or evidence["longest"] == len(compact)))


def engine_artifact_subkind(compact: str) -> str | None:
    """`engine_artifact` 的**封闭白名单**判定：返回子类名，或 `None`。

    只有具备明确、**版本化**转换依据的差异才可能被解释为 engine artifact：

    - `blank`：`tight()` 已吸收全部空白差异（依据 `NORMALIZATION_VERSION`）；
    - `invisible_codepoint`：残差全部由 `INVISIBLE_CODEPOINTS` 构成。

    **不在白名单里的一律返回 `None`**：普通汉字、数字、英文字母、标点、私用区
    字形、未知符号，哪怕只有 1 个字符，也必须由调用方判 `unexplained`。特别地，
    全角字符（`Ａ` / `１２３４`）**不在**白名单里。

    本函数**故意**不接受任何页面文本参数 —— 一旦能拿到全页文本，就又会回到
    "把残差变形后到全页碰运气"的路径上。没有页面文本就没有事后消除的可能，
    这是接口层面的保证，不只是注释里的约定。
    """
    if compact == "":
        return "blank"
    if strip_invisible(compact) == "":
        return "invisible_codepoint"
    return None


def short_fragment(text: str, limit: int = 40) -> str:
    """给人工看的最短片段（**不泄露整段原文**）。"""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[:limit] + "…"


def classify_residue(layout: PageLayout, page_number: int, page_text: str,
                     spans: list, residue: str, lines: list,
                     cache: dict | None = None) -> tuple:
    """给一段残差归因，返回 `(class, reason, subkind)`。

    只用**版式层可核实的**证据，不做语义猜测，也不按残差长度猜类别；证据强度
    从高到低：

    - `page_furniture`：残差整体落在某条**已标记家具行**里；
    - `engine_artifact`：命中 `ENGINE_ARTIFACT_RULES` 封闭白名单（两条，均有
      版本化依据）。**没有**全角/半角等价规则（TS2 最终关闭轮删除）；
    - `column_reorder`：内容确实在同一页，只是切分/顺序与块内不一致。两种可核实
      的子情形：`line_segment`（残差是某条版式行的连续片段）、`scattered_pieces`
      （残差可完整绑定到一组**两两不重叠**的同页 occurrence，且满足段数/长度下界）；
    - `unexplained`：以上都不成立 —— 必须原样上报。子类区分失败原因：
      `residue_duplicated`、`search_exhausted`、`unresolved`。

    这里对 `page_text` / `spans` 的使用都受**非重叠完整绑定**约束：要么残差整体
    落在某条已登记的版式行里，要么残差被**完整**绑定到一组两两不重叠的同页
    occurrence。**不存在**任何"残差变换后在整页任意位置命中即算解释"的分支。
    """
    compact = tight(residue)
    if cache is None:
        cache = {}
    for line in lines:
        if not line.is_furniture:
            continue
        if compact in tight(line.text):
            return ("page_furniture",
                    f"残差落在已标记家具行（{line.furniture_kind}）",
                    "furniture_line")
    subkind = engine_artifact_subkind(compact)
    if subkind is not None:
        return ("engine_artifact",
                f"残差命中 engine artifact 白名单（{subkind}，依据 "
                f"{NORMALIZATION_RULE_VERSION}）；{len(compact)} 字符", subkind)
    for line in lines:
        if line.is_furniture:
            continue
        if compact in tight(line.text):
            return ("column_reorder",
                    f"残差 {len(compact)} 字符是版式行 {line.line_index} 的连续"
                    f"片段（同页内容、切分不同）", "line_segment")
    evidence = displaced_evidence(compact, page_text, spans, cache=cache)
    pieces = evidence["pieces"]
    if evidence["leftover"] == "" and pieces and (
            len(pieces) >= COLUMN_REORDER_MIN_PIECES
            or evidence["longest"] == len(compact)):
        return ("column_reorder",
                f"残差 {len(compact)} 字符完整绑定到 {len(pieces)} 个**两两不重叠**"
                f"的同页 occurrence（最长 {evidence['longest']} 字符 / 跨 "
                f"{len(evidence['source_lines'])} 条版式行），顺序与块内不一致",
                "scattered_pieces")
    located = evidence.get("locatable_upper_bound", 0)
    if evidence.get("search_exhausted"):
        return ("unexplained",
                f"非重叠分配搜索预算耗尽（{evidence.get('search_nodes')} 节点）："
                f"{len(compact)} 字符未能在预算内证明存在非重叠映射，保守判未解释",
                "search_exhausted")
    if evidence.get("no_injective_assignment"):
        deficit = evidence.get("occurrence_deficit") or {}
        detail = ("；重复文本的 occurrence 缺口 "
                  + "、".join(f"{k} 需 {v['needed']} 次/仅有 {v['available']} 次"
                             for k, v in sorted(deficit.items())[:3])) if deficit else ""
        return ("unexplained",
                f"无法建立**注入式非重叠**映射：{len(compact)} 字符虽可切成同页可定位"
                f"片段，但无法为它们分配两两不重叠的真实 occurrence"
                f"（至多 {located} 字符可定位）{detail}", "residue_duplicated")
    return ("unexplained",
            f"无法用版式证据解释：{len(compact)} 字符既不是某条版式行的连续片段，"
            f"也切不成同页可定位的片段（至多 {located} 字符可定位）",
            "unresolved")


# ---------------------------------------------------------------------------
# char_map：把顺序匹配块按真实版式行 / 行内 span 切成可定位映射段
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _SpanEntry:
    span_index: int
    tight_start: int          # 行内 tight 坐标起点
    tight_end: int
    kept_start: int           # 在"该行非空白字符原始偏移"列表中的起始下标
    char_start: int           # LayoutSpan 在该行原文中的起始偏移


@dataclass(frozen=True)
class _LineEntry:
    line_index: int
    tight_start: int
    tight_end: int
    spans: tuple
    kept: tuple               # 该行非空白字符的行内原始偏移（升序）


def _page_tight_index(page) -> tuple:
    """该页的 `tight` 拼接文本 + 逐行的 tight→span→原文偏移索引。

    只依赖真实 `LayoutLine` / `LayoutSpan` 的字符区间：行内相邻 span 之间的空隙
    只允许空白（`LayoutLine` 构造期强制），因此 `tight(行文本)` 等于各 span
    `tight` 文本的顺序拼接。任何不满足即 fail-closed（不猜偏移）。
    """
    text_parts = []
    entries = []
    cursor = 0
    for line in page.lines:
        flat = tight(line.text)
        kept = tuple(i for i, ch in enumerate(line.text) if not ch.isspace())
        if "".join(line.text[i] for i in kept) != flat:
            raise AlignmentError(
                f"行的 tight 归一与逐字符去空白不一致：页 {page.page_number} / 行 "
                f"{line.line_index}")
        span_entries = []
        position = 0
        for span_index, span in enumerate(line.spans):
            span_flat = tight(span.text)
            if not span_flat:
                continue
            if flat[position:position + len(span_flat)] != span_flat:
                raise AlignmentError(
                    f"行内 span 的文本与行文本在 tight 坐标下不连续：页 "
                    f"{page.page_number} / 行 {line.line_index} / span {span_index}")
            span_entries.append(_SpanEntry(
                span_index=span_index, tight_start=cursor + position,
                tight_end=cursor + position + len(span_flat),
                kept_start=position, char_start=span.char_start))
            position += len(span_flat)
        if position != len(flat):
            raise AlignmentError(
                f"行内 span 未完整覆盖行文本：页 {page.page_number} / 行 "
                f"{line.line_index}（{position} vs {len(flat)}）")
        entries.append(_LineEntry(line_index=line.line_index, tight_start=cursor,
                                  tight_end=cursor + len(flat),
                                  spans=tuple(span_entries), kept=kept))
        text_parts.append(flat)
        cursor += len(flat)
    return "".join(text_parts), tuple(entries)


def _locate_tight_offset(entries: tuple, offset: int) -> tuple | None:
    """把 tight 偏移定位到 `(line_index, span_index, char_offset, span_end)`。

    `char_offset` 是**该 `LayoutSpan` 文本内**的偏移（即 `span.char_start +
    char_offset` 就是行内真实字符位置）。定位不到（落到空白缝隙之外的未覆盖区间）
    返回 `None` —— 调用方必须 fail-closed，不得猜偏移。
    """
    for entry in entries:
        if not (entry.tight_start <= offset < entry.tight_end):
            continue
        for span in entry.spans:
            if span.tight_start <= offset < span.tight_end:
                raw = entry.kept[span.kept_start + (offset - span.tight_start)]
                return (entry.line_index, span.span_index,
                        raw - span.char_start, span.tight_end)
        return None
    return None


def build_char_map(block_text: str, page_number: int, entries: tuple,
                   matching_blocks: tuple) -> tuple:
    """由顺序匹配块推导 `char_map`（`(块起, 块止, 页, 行, span, span 内偏移)`）。

    每个匹配块在页面上是一个**连续的 tight 区间**；按真实版式行与行内 span 的边界
    把它切成若干段，每段绑定到一条真实 `LayoutSpan` 的**真实字符偏移**上。切不开
    （落到未被任何 span 覆盖的区间）即抛错：宁可没有对齐记录，也不猜字符位置。
    """
    segments: list = []
    for match in matching_blocks:
        if match.size <= 0:
            continue
        block_position = match.a
        page_position = match.b
        page_end = match.b + match.size
        if not (0 <= block_position and page_position >= 0
                and block_position + match.size <= len(block_text)):
            raise AlignmentError("顺序匹配块越出块文本范围")
        while page_position < page_end:
            located = _locate_tight_offset(entries, page_position)
            if located is None:
                raise AlignmentError(
                    f"匹配块在页 {page_number} 的 tight 偏移 {page_position} 处无法定位到"
                    f"任何真实 LayoutSpan；不得猜测字符偏移")
            line_index, span_index, char_offset, span_end = located
            page_stop = min(span_end, page_end)
            length = page_stop - page_position
            if length <= 0:
                raise AlignmentError(
                    f"匹配块切分为零长度段：页 {page_number} / tight 偏移 "
                    f"{page_position}")
            segments.append((block_position, block_position + length, page_number,
                             line_index, span_index, char_offset))
            block_position += length
            page_position = page_stop
    return tuple(segments)


def _check_char_map_closure(block_char_length: int, char_map: tuple,
                            residue_segments: tuple,
                            matched_chars: int | None = None) -> int:
    """P1-E：写路径上的分区闭合校验，**复用公共校验器**（不再另立一套规则）。

    收口前这里是一份私有实现，只覆盖"并集连续"这一条，且只在 `align_block` 里被
    调用：`TextAlignmentRecord` / `AlignmentRefusalRecord` 的构造与反序列化路径
    都不经过它，于是"持久化对象反序列化本身 fail-closed"这条要求没有落点。
    现在两侧共用 `schema.validate_alignment_partition`（版本 `apv-1`）：同一份
    载荷在写路径、构造路径、读回路径上被**同一个**权威判定。

    返回值：由 `char_map` 重算的匹配字符数。
    """
    try:
        return validate_alignment_partition(
            block_char_length=block_char_length, char_map=tuple(char_map),
            residue=tuple((seg.start, seg.end, seg.residue_class)
                          for seg in residue_segments),
            matched_chars=matched_chars, where="align_block")
    except SchemaValidationError as error:
        raise AlignmentError(
            f"char_map 与残差未能互补覆盖块坐标：{error}；不得产出不一致的对齐记录"
        ) from None


# ---------------------------------------------------------------------------
# 单块对齐
# ---------------------------------------------------------------------------

def align_block(layout: PageLayout, block: EvidenceBlockInput) -> BlockAlignment:
    """把一个 `EvidenceBlock` 对齐到 `layout` 的真实版式（确定性、可复核）。

    产出物：

    1. `coverage` / 残差 / `char_map` 事实（供下游复核与重算）；
    2. 由 `TextAlignmentRecord.create()` 生成的记录 —— 调用方**无法**自报
       verdict / coverage / residue_class（`__post_init__` 会重算比对）；
    3. 拒绝产出记录时的稳定原因码（唯一情形：量化会越过冻结阈值，见模块 docstring）。
    """
    if not isinstance(layout, PageLayout):
        raise AlignmentError(f"align_block 需要真实 PageLayout 对象，"
                             f"得到 {type(layout).__name__}")
    if not isinstance(block, EvidenceBlockInput):
        raise AlignmentError(f"align_block 需要 EvidenceBlockInput，"
                             f"得到 {type(block).__name__}")
    # 身份在**正式对齐入口**再核一次：即便有人绕过构造期校验，也不得凭自报身份
    # 生成正式记录。
    block.assert_identity()
    block.assert_bound_to(layout)
    page = _page_of(layout, block.page_number)
    if page is None:
        raise AlignmentError(
            f"页 {block.page_number} 在真实 PageLayout（{layout.page_layout_id}）上"
            f"不存在；不得把不存在的页当成空页判 unaligned")

    block_text = tight(block.text)
    lines = list(page.lines)
    text, entries = _page_tight_index(page)
    spans_text, spans = page_text_spans(lines)
    if spans_text != text:
        raise AlignmentError(
            f"版式行拼接与该页 tight 文本不一致：页 {block.page_number}"
            f"（layout {layout.page_layout_id}）")

    result = coverage_record(block_text, text)
    block_char_length = len(block_text)

    classes: dict = {}
    subkinds: dict = {}
    segments: list = []
    unlocatable = 0
    lenient = 0
    duplicated = 0
    exhausted = 0
    occurrence_cache: dict = {}
    for residue in result["residues"]:
        klass, reason, subkind = classify_residue(
            layout, block.page_number, text, spans, residue["text"], lines,
            cache=occurrence_cache)
        length = residue["end"] - residue["start"]
        if tight(residue["text"]) != residue["text"]:
            # 块文本已 tight 归一，残差必然不含空白；不一致即度量前提被破坏。
            raise AlignmentError("残差文本不是 tight 归一形态（度量前提被破坏）")
        classes[klass] = classes.get(klass, 0) + length
        subkinds[subkind] = subkinds.get(subkind, 0) + length
        if klass == "unexplained":
            flat = residue["text"]
            unlocatable += len(flat) - displaced_evidence(
                flat, text, spans,
                cache=occurrence_cache).get("locatable_upper_bound", 0)
            if subkind == "residue_duplicated":
                duplicated += len(flat)
            if subkind == "search_exhausted":
                exhausted += len(flat)
            if _lenient_only_explained(flat, text, spans):
                lenient += len(flat)
        segments.append(ResidueSegment(start=residue["start"], end=residue["end"],
                                       residue_class=klass, subkind=subkind,
                                       reason=reason, text=residue["text"]))

    char_map = build_char_map(block_text, block.page_number, entries,
                              result["matching_blocks"])
    residue_segments = tuple(segments)
    _check_char_map_closure(block_char_length, char_map, residue_segments,
                            result["matched_chars"])

    exact_coverage = result["coverage"]
    matched_chars = result["matched_chars"]
    descriptive = dominant_residue_class(classes)
    gate = verdict_residue_class(classes)
    residue_arg = tuple((s.start, s.end, s.residue_class) for s in residue_segments)

    refusal = _record_refusal_reason(block_char_length, char_map, matched_chars)
    if refusal is not None:
        # 拒绝终态：不是"没有结论"，而是一个 typed、版本化、带完整身份的正式结论。
        record = None
        refusal_record = AlignmentRefusalRecord.create(
            page_layout_id=layout.page_layout_id,
            evidence_set_version=block.evidence_set_version,
            page_number=block.page_number, block_index=block.block_index,
            evidence_block_id=block.evidence_block_id,
            block_char_length=block_char_length, matched_chars=matched_chars,
            residue=residue_arg, char_map=char_map, refusal_reason=refusal)
        verdict = "unaligned"
        is_citable = False
    else:
        refusal_record = None
        record = TextAlignmentRecord.create(
            page_layout_id=layout.page_layout_id,
            evidence_set_version=block.evidence_set_version,
            page_number=block.page_number, block_index=block.block_index,
            evidence_block_id=block.evidence_block_id,
            block_char_length=block_char_length,
            residue=residue_arg, char_map=char_map)
        verdict = record.verdict
        is_citable = record.is_citable()
        # 记录的 `residue_class`（最严重类别）与块级**门槛类**（含任何 unexplained
        # 即 unexplained）在描述上可以不同，但在"是否含 unexplained"上必须同源，
        # 否则"记录说 aligned 而块说 partially_aligned"会同时出现在产物里。
        if (record.residue_class == "unexplained") != (gate == "unexplained"):
            raise AlignmentError(
                f"记录 residue_class {record.residue_class!r} 与块级门槛类 {gate!r} "
                f"在「是否含 unexplained」上不一致；对齐判定不得与公共真值表分叉")
        expected = compute_alignment_verdict_exact(
            matched_chars, block_char_length, record.residue_class, V.ALIGN_MIN)
        if verdict != expected:
            raise AlignmentError(
                f"记录 verdict {verdict!r} 与按精确比值重算的 {expected!r} 不一致；"
                f"对齐判定不得与公共真值表分叉")

    return BlockAlignment(
        evidence_block_id=block.evidence_block_id,
        page_number=block.page_number, block_index=block.block_index,
        evidence_set_version=block.evidence_set_version,
        page_layout_id=layout.page_layout_id, aligner_version=V.ALIGNER_VERSION,
        block_char_length=block_char_length, matched_chars=matched_chars,
        unmatched_chars=result["unmatched_chars"], exact_coverage=exact_coverage,
        residue_segments=residue_segments,
        residue_char_counts=tuple(sorted(classes.items())),
        residue_subkind_counts=tuple(sorted(subkinds.items())),
        residue_class=descriptive, verdict_residue_class=gate, verdict=verdict,
        is_citable=is_citable, record=record, record_refusal=refusal,
        refusal_record=refusal_record,
        char_map=char_map, unlocatable_chars=unlocatable,
        lenient_explained_chars=lenient,
        unexplained_duplicate_chars=duplicated,
        unexplained_search_exhausted_chars=exhausted)


def _record_refusal_reason(block_char_length: int, char_map: tuple,
                           matched_chars: int) -> str | None:
    """该块的正式终态是否为**拒绝**（fail-closed 规则）。

    唯一拒绝情形（TS3 实测发现，见模块 docstring）：`ALS-1` 记录只能把覆盖率表示成
    "量化到 `FLOAT_PRECISION` 位的展示值"，于是存在
    `exact < ALIGN_MIN <= quantize(exact)` 的窄区间 —— 展示值不低于阈值，而按冻结
    规则（`0.899999 < 0.90 ⇒ unaligned`、低于阈值**不可引用**）该块不可引用。
    展示与判定相反，这是真实存在的表示冲突。

    自 `als-2` 起记录已能携带精确分子，判定不再依赖展示值；但**展示字段依然存在**，
    因此只要一个块的展示值 `coverage` 恰好不低于阈值、而精确比值低于阈值，产物里就
    会出现"看起来够、其实不可引用"的自相矛盾条目。本轮按 §五 要求，把这类块收敛为
    **拒绝终态**：不产出 `TextAlignmentRecord`，产出 typed 的
    `AlignmentRefusalRecord`（原因码即本函数的返回值）。

    判定用**精确整数比值**（`matched_chars / block_char_length`，见
    `compute_alignment_verdict_exact`），绝不"先四舍五入再决定 `ALIGN_MIN`"。

    `ALIGN_MIN is None`（兼容档）下不存在该张力（任何覆盖率都不会产出
    `aligned`），因此不拒绝。
    """
    if V.ALIGN_MIN is None:
        return None
    quantized = compute_alignment_coverage(block_char_length, char_map)
    verdict_exact = compute_alignment_verdict_exact(
        matched_chars, block_char_length, None, V.ALIGN_MIN)
    verdict_quantized = compute_alignment_verdict(quantized, None, V.ALIGN_MIN)
    if verdict_exact != verdict_quantized:
        return RECORD_REFUSAL_QUANTIZATION_BOUNDARY
    return None


# ---------------------------------------------------------------------------
# evidence set 全量对齐
# ---------------------------------------------------------------------------

def align_evidence_set(layout: PageLayout, blocks, *,
                       snapshot: EvidenceSetSnapshot) -> EvidenceSetAlignment:
    """对同一文档 / 同一 evidence set 的**整份**块做对齐（确定性、与输入顺序无关）。

    结果按 `(page_number, block_index)` 升序，因此**调用方给出的候选顺序不影响**
    结论与顺序；同一 `(page, block)` 出现两次即拒绝（不得重复消费同一来源位置）。

    身份门（fail-closed，§八）：

    - 必须提供**权威** `EvidenceSetSnapshot`（由受信任只读 gateway 注入；本函数不接受
      "提交清单自身自洽"作为证据集身份）；
    - 快照必须为 `current`，且公司 / 文档 / 版本与 `layout` 严格相等；
    - 每个块必须闭合到 `layout`（公司 / 文档 / 版本严格相等）并重算身份；
    - 每个块的 `evidence_set_version` 必须等于快照记录的那一个（块的自报值不得覆盖
      权威来源）；
    - **提交成员与快照成员精确相等**：少一个 / 多一个 / 重复 / type、hash、page、
      block 任一不符一律拒绝。

    单块算法入口 `align_block` 仍保留（内部算法与单元测试用），但它**不**构成
    "完成了整个 EvidenceSet"的证明 —— 那句结论只能由本函数给出。
    """
    if not isinstance(layout, PageLayout):
        raise AlignmentError(f"align_evidence_set 需要真实 PageLayout 对象，"
                             f"得到 {type(layout).__name__}")
    if not isinstance(snapshot, EvidenceSetSnapshot):
        raise AlignmentError(
            f"align_evidence_set 需要权威 EvidenceSetSnapshot（由只读 gateway 注入），"
            f"得到 {type(snapshot).__name__}；提交清单自身自洽不能证明"
            f"『这是本文档当前证据集的全部成员』（fail-closed）")
    snapshot.assert_current()
    snapshot.assert_bound_to(layout)
    ordered = sorted(blocks, key=lambda b: (b.page_number, b.block_index))
    seen: set = set()
    for block in ordered:
        if not isinstance(block, EvidenceBlockInput):
            raise AlignmentError(f"blocks 必须为 EvidenceBlockInput，"
                                 f"得到 {type(block).__name__}")
        block.assert_identity()
        block.assert_bound_to(layout)
        key = (block.page_number, block.block_index)
        if key in seen:
            raise AlignmentError(f"同一 (页, 块) 被重复提交：{key}；"
                                 f"来源位置不得被重复消费")
        seen.add(key)
    versions = {b.evidence_set_version for b in ordered}
    if len(versions) > 1:
        raise AlignmentError(f"同一次全量对齐不得混用多个 evidence_set_version："
                             f"{sorted(versions)}")
    if versions and versions != {snapshot.evidence_set_version}:
        raise AlignmentError(
            f"提交块的 evidence_set_version {sorted(versions)} 与权威快照的 "
            f"{snapshot.evidence_set_version!r} 不一致；不得把别的证据集挂到本文档上"
            f"（fail-closed）")
    assert_members_exact(tuple(_member_of(b) for b in ordered), snapshot)
    results = tuple(align_block(layout, block) for block in ordered)
    # §五 守恒律：每个块恰好一个正式终态。在**集合级入口**再核一次，因为下游产物
    # 的对账正是按集合做的（"769 个 block = 769 条终态"）。
    for result in results:
        if result.terminal is None:
            raise AlignmentError(
                f"块 {(result.page_number, result.block_index)} 没有正式终态；"
                f"每个块必须且只能有一个 TextAlignmentRecord 或 "
                f"AlignmentRefusalRecord（fail-closed）")
    return EvidenceSetAlignment(
        page_layout_id=layout.page_layout_id,
        evidence_set_version=snapshot.evidence_set_version,
        aligner_version=V.ALIGNER_VERSION, blocks=results,
        summary=alignment_summary(results), snapshot=snapshot)


def alignment_summary(alignments) -> dict:
    """三态分布 + 可引用面 + **终态守恒**（确定性字典）。

    §五 要求"每个 block 恰好一个正式终态、769 = 769"：因此汇总必须同时给出
    `records_emitted + records_refused == total_blocks`，以及每个终态的
    schema 版本分布。任何"某个块既没有记录也没有拒绝记录"的情形都会让
    `terminals_missing` 非空，而不是被一个总数掩盖。
    """
    blocks = tuple(alignments)
    verdicts = {"aligned": 0, "partially_aligned": 0, "unaligned": 0}
    for block in blocks:
        verdicts[block.verdict] = verdicts.get(block.verdict, 0) + 1
    refused = [b for b in blocks if b.refusal_record is not None]
    missing = [b for b in blocks if b.terminal is None]
    terminal_versions: dict = {}
    for block in blocks:
        terminal = block.terminal
        if terminal is None:
            continue
        key = f"{type(terminal).__name__}@{terminal.schema_version}"
        terminal_versions[key] = terminal_versions.get(key, 0) + 1
    return {
        "aligner_version": V.ALIGNER_VERSION,
        "align_min": V.ALIGN_MIN,
        "align_schema_version": V.ALIGN_SCHEMA_VERSION,
        "align_refusal_schema_version": V.ALIGN_REFUSAL_SCHEMA_VERSION,
        "total_blocks": len(blocks),
        "aligned": verdicts["aligned"],
        "partially_aligned": verdicts["partially_aligned"],
        "unaligned": verdicts["unaligned"],
        "citable_blocks": sum(1 for b in blocks if b.is_citable),
        "blocks_with_unexplained": sum(1 for b in blocks if b.has_unexplained),
        "empty_blocks": sum(1 for b in blocks if b.empty_block),
        "records_emitted": sum(1 for b in blocks if b.record is not None),
        "records_refused": len(refused),
        "terminals_emitted": sum(1 for b in blocks if b.terminal is not None),
        "terminals_missing": [{"page_number": b.page_number,
                               "block_index": b.block_index,
                               "evidence_block_id": b.evidence_block_id}
                              for b in missing],
        "terminal_schema_versions": dict(sorted(terminal_versions.items())),
        "refusals": [{"page_number": b.page_number, "block_index": b.block_index,
                      "evidence_block_id": b.evidence_block_id,
                      "reason": b.record_refusal,
                      "refusal_id": (None if b.refusal_record is None
                                     else b.refusal_record.refusal_id),
                      "matched_chars": b.matched_chars,
                      "block_char_length": b.block_char_length,
                      "exact_coverage": b.exact_coverage,
                      "quantized_coverage": quantize(b.exact_coverage)}
                     for b in refused],
        "residue_class_chars": _sum_class_chars(blocks),
    }


def _sum_class_chars(blocks) -> dict:
    totals: dict = {}
    for block in blocks:
        for klass, chars in block.residue_char_counts:
            totals[klass] = totals.get(klass, 0) + chars
    return dict(sorted(totals.items()))


def alignment_records(alignments) -> tuple:
    """全量对齐结果里**已产出**的 `TextAlignmentRecord`（保持输入顺序）。

    拒绝终态的块（见模块 docstring）在这里**没有**记录：它保持不可引用，
    不会被任何下游当成已对齐材料。
    """
    return tuple(b.record for b in alignments if b.record is not None)


def alignment_terminals(alignments) -> tuple:
    """全量对齐结果里**每个块**的正式终态（`TextAlignmentRecord` 或
    `AlignmentRefusalRecord`，保持输入顺序）。

    §五 的守恒律在这一个函数里落地：`len(alignment_terminals(x))` 必须等于
    `len(tuple(x))`。缺终态的块会**原样暴露**为 `None`（而不是被过滤掉），
    因此"少了几条"不可能被一个漂亮的总数掩盖。
    """
    return tuple(b.terminal for b in alignments)


def alignment_terminal_report(alignments) -> dict:
    """终态对账视图：逐块给出 `(页, 块, 终态类型, 身份, 原因)`，供产物逐条核对。"""
    rows = []
    for block in alignments:
        terminal = block.terminal
        rows.append({
            "page_number": block.page_number,
            "block_index": block.block_index,
            "evidence_block_id": block.evidence_block_id,
            "terminal_schema_type": (None if terminal is None
                                     else type(terminal).__name__),
            "terminal_schema_version": (None if terminal is None
                                        else terminal.schema_version),
            "terminal_id": (None if terminal is None else
                            getattr(terminal, "alignment_id",
                                    getattr(terminal, "refusal_id", None))),
            "verdict": block.verdict,
            "is_citable": block.is_citable,
            "refusal_reason": block.record_refusal,
            "matched_chars": block.matched_chars,
            "block_char_length": block.block_char_length,
            "exact_coverage": block.exact_coverage,
            "coverage": quantize(block.exact_coverage),
        })
    return {
        "total_blocks": len(rows),
        "terminals_emitted": sum(1 for r in rows if r["terminal_schema_type"]),
        "terminals_missing": sum(1 for r in rows if not r["terminal_schema_type"]),
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def self_check() -> dict:
    """模块自检（不依赖任何真实文档；与 `layout_builder.self_check` 同风格）。"""
    out: dict = {"module": "document_structure.aligner",
                 "aligner_version": V.ALIGNER_VERSION, "checks": []}

    def ok(cond: bool, msg: str) -> None:
        out["checks"].append(("PASS " if cond else "FAIL ") + msg)
        if not cond:
            out["ok"] = False

    out["ok"] = True
    ok(V.classify_schema_version("ALIGNER_VERSION", V.ALIGNER_VERSION)
       == "current"
       and V.ALIGNER_VERSION not in V.legacy_versions("ALIGNER_VERSION"),
       "ALIGNER_VERSION 为当前权威版本（历史版本按政策登记为 legacy）")
    ok(V.ALIGN_MIN == 0.90, "ALIGN_MIN 为冻结的 0.90")
    ok("width_fold" not in ENGINE_ARTIFACT_RULES,
       "engine artifact 白名单不含 width_fold（不得恢复全页搜索式消除残差）")
    ok(engine_artifact_subkind(chr(0xFF21)) is None,
       "全角字符残差不得被判成 engine artifact")
    ok(engine_artifact_subkind(chr(0x200B)) == "invisible_codepoint",
       "零宽字符残差判 invisible_codepoint")
    ok(dominant_residue_class({"engine_artifact": 99, "unexplained": 18})
       == "engine_artifact", "描述性主类按字符数取")
    ok(verdict_residue_class({"engine_artifact": 99, "unexplained": 18})
       == "unexplained", "门槛类：含任何 unexplained 即 unexplained")
    ok(compute_alignment_verdict(0.0, None, V.ALIGN_MIN) == "unaligned",
       "coverage<=0 一律 unaligned")
    ok(V.ALIGN_SCHEMA_VERSION in V.ALIGN_EXACT_RATIO_VERSIONS,
       "ALIGN_SCHEMA_VERSION 属于携带精确分子的 wire 版本登记表")
    ok(V.classify_schema_version("ALIGN_SCHEMA_VERSION",
                                 V.legacy_versions("ALIGN_SCHEMA_VERSION")[0])
       == "legacy", "旧对齐 wire 版本登记为只读兼容（须显式迁移/重算）")
    ok(V.EVIDENCE_SET_SNAPSHOT_VERSION not in
       V.legacy_versions("EVIDENCE_SET_SNAPSHOT_VERSION"),
       "EvidenceSetSnapshot 版本为当前权威版本（首个快照 wire 版本）")
    ok(frozenset(EVIDENCE_TYPES) == EVIDENCE_TYPE_CLOSED_SET,
       "evidence_type 封闭集合复用 Evidence 架构的冻结枚举")
    ok(validate_alignment_partition(
        block_char_length=4, char_map=((0, 2, 1, 0, 0, 0),),
        residue=((2, 4, "unexplained"),), matched_chars=2) == 2,
       "公共分区验证器：正常对齐分区通过并返回精确分子")
    try:
        validate_alignment_partition(
            block_char_length=4, char_map=((0, 3, 1, 0, 0, 0),),
            residue=((2, 4, "unexplained"),), matched_chars=3)
    except SchemaValidationError:
        ok(True, "公共分区验证器：char_map 与 residue 交叉重叠被拒")
    else:
        ok(False, "公共分区验证器：char_map 与 residue 交叉重叠被拒")
    ok(RECORD_REFUSAL_QUANTIZATION_BOUNDARY in ALIGNMENT_REFUSAL_REASONS,
       "拒绝原因码属于公共类型层的封闭集合")
    # 精确比值判定：0.8997 (<0.90) 不得因为量化到 0.9 而变成 aligned
    ok(compute_alignment_verdict_exact(8997, 10000, None, V.ALIGN_MIN)
       == "unaligned",
       "精确 0.8997 判 unaligned（不得先四舍五入再判阈值）")
    ok(compute_alignment_verdict_exact(9000, 10000, None, V.ALIGN_MIN)
       == "aligned", "精确 0.9000 达到阈值判 aligned")
    ok(compute_alignment_verdict_exact(9000, 10000, "unexplained", V.ALIGN_MIN)
       == "partially_aligned", "达到阈值但含 unexplained 仍不可引用")
    ok(compute_alignment_verdict_exact(0, 10000, None, V.ALIGN_MIN)
       == "unaligned", "精确分子为 0 一律 unaligned")
    ok(compute_alignment_verdict_exact(10, 10, None, None) != "aligned",
       "阈值未裁决的兼容档永不产出 aligned")
    ok(RESIDUE_CLASSES == ("page_furniture", "engine_artifact", "column_reorder",
                           "unexplained"),
       "残差类别序复用 schema 的唯一一份定义")
    out["ok"] = all(line.startswith("PASS ") for line in out["checks"])
    return out


def _main(argv=None) -> int:  # pragma: no cover - 仅供人工排查
    import json
    print(json.dumps(self_check(), ensure_ascii=False, indent=1))
    return 0 if self_check()["ok"] else 1


# ---------------------------------------------------------------------------
# TS4：受信对齐 capability（`VerifiedEvidenceSetAlignment`，计划 §18.3.2 / §18.11.2）
# ---------------------------------------------------------------------------
#
# TS4 正式链路的第二步要回答：**"这份对齐结论是刚才那一次正式全量对齐当场产出的
# 吗，还是调用方把一份普通的 `EvidenceSetAlignment` 递过来？"** 前者与后者字段完全
# 同形，唯一可靠的差别是"本进程的签发登记表里有没有它"。因此这里不新增任何判断
# 算法（`align_evidence_set` 仍是**唯一**对齐实现），只做三件事：
#
# 1. 强制上游 capability：版式必须是 `build_verified_page_layout` 签发的实例，证据
#    权威必须是 `bind_current_evidence_authority` 签发的实例；两者同为 `live` 域；
# 2. 由版式（真实根对象）而不是调用方参数决定公司 / 文档 / 版本，再去权威里取
#    **同一次**快照与全量块，调用 `align_evidence_set`；
# 3. 逐终态核对 `page_layout_id` / `evidence_set_version` / `aligner_version` /
#    schema 版本 / `block_char_length`（用真实 Evidence 文本重算），全部通过后才签发。
#
# `to_dict` / copy / deepcopy / pickle 全部显式拒绝：序列化出去再读回来，就不再是
# "本进程签发的那一个"。


class VerifiedAlignmentError(SchemaValidationError):
    """受信对齐签发 / 使用失败（上游能力不符 / 终态不闭合 / 跨 scope）。"""


@dataclass(frozen=True)
class AlignmentTerminal:
    """一个块的**正式终态视图**（typed，只读派生量，不是新的 wire 类型）。

    它把 `BlockAlignment` 上的终态与其对应输入块的身份并成一条记录，供 TS4 做
    全终态闭合、逐段 walk 与组件构造。所有字段都可在对齐结果 + `EvidenceBlockInput`
    上重算，因此它**不引入第二套事实来源**。
    """

    page_number: int
    block_index: int
    evidence_block_id: str
    evidence_set_version: str
    terminal_kind: str
    terminal_id: str
    terminal_locator: str
    terminal_schema_version: str
    aligner_version: str
    partition_validator_version: str
    verdict: str | None
    refusal_reason: str | None
    residue_class: str | None
    block_char_length: int
    matched_chars: int | None
    exact_coverage: float
    char_map: tuple
    residue: tuple

    @property
    def sort_key(self) -> tuple:
        return (self.page_number, self.block_index, self.terminal_kind,
                self.terminal_id)

    def identity(self) -> tuple:
        """§18.3.5 `terminals` 的定型身份（排序后入 input fingerprint）。"""
        return (self.terminal_kind, self.terminal_id, self.terminal_locator,
                self.terminal_schema_version, self.aligner_version,
                self.partition_validator_version,
                V.ALIGNMENT_TERMINAL_PROVIDER_VERSION)

    def to_dict(self) -> dict:
        return {
            "page_number": self.page_number, "block_index": self.block_index,
            "evidence_block_id": self.evidence_block_id,
            "evidence_set_version": self.evidence_set_version,
            "terminal_kind": self.terminal_kind, "terminal_id": self.terminal_id,
            "terminal_locator": self.terminal_locator,
            "terminal_schema_version": self.terminal_schema_version,
            "aligner_version": self.aligner_version,
            "partition_validator_version": self.partition_validator_version,
            "verdict": self.verdict, "refusal_reason": self.refusal_reason,
            "residue_class": self.residue_class,
            "block_char_length": self.block_char_length,
            "matched_chars": self.matched_chars,
            "exact_coverage": self.exact_coverage,
            "char_map": [list(seg) for seg in self.char_map],
            "residue": [list(seg) for seg in self.residue],
        }


def alignment_terminal_views(alignment: EvidenceSetAlignment,
                             blocks: tuple) -> tuple:
    """把对齐结果投影成 `AlignmentTerminal` 元组（按 `(页,块)` 顺序）。

    身份全部取自**终态对象本身**：`verdict` / `refusal_reason` 由"哪一个字段非空"
    决定，不采信任何字符串自报；`AlignmentRefusalRecord` 是**必须提供**的正式终态，
    不是"记录缺失"。
    """
    by_key = {(b.page_number, b.block_index): b for b in blocks}
    out = []
    for ba in alignment.blocks:
        terminal = ba.terminal
        if terminal is None:
            raise VerifiedAlignmentError(
                f"块 {(ba.page_number, ba.block_index)} 没有正式终态；"
                f"每个块必须且只能有一个终态（fail-closed）")
        block = by_key.get((ba.page_number, ba.block_index))
        if block is None:
            raise VerifiedAlignmentError(
                f"终态 {(ba.page_number, ba.block_index)} 找不到对应输入块（fail-closed）")
        if ba.evidence_block_id != block.evidence_block_id:
            raise VerifiedAlignmentError(
                f"块 {(ba.page_number, ba.block_index)} 的终态 evidence_block_id "
                f"{ba.evidence_block_id!r} 与输入块 {block.evidence_block_id!r} 不一致")
        is_record = ba.record is not None
        out.append(AlignmentTerminal(
            page_number=ba.page_number, block_index=ba.block_index,
            evidence_block_id=ba.evidence_block_id,
            evidence_set_version=ba.evidence_set_version,
            terminal_kind=("alignment" if is_record else "refusal"),
            terminal_id=(terminal.alignment_id if is_record else terminal.refusal_id),
            terminal_locator=terminal.alignment_locator,
            terminal_schema_version=terminal.schema_version,
            aligner_version=terminal.aligner_version,
            partition_validator_version=V.ALIGNMENT_PARTITION_VALIDATOR_VERSION,
            verdict=(terminal.verdict if is_record else None),
            refusal_reason=(None if is_record else terminal.refusal_reason),
            residue_class=terminal.residue_class,
            block_char_length=terminal.block_char_length,
            matched_chars=(terminal.matched_chars if is_record
                           else terminal.matched_chars),
            exact_coverage=(terminal.coverage if is_record
                            else terminal.exact_coverage),
            char_map=tuple(tuple(seg) for seg in terminal.char_map),
            residue=tuple(tuple(seg) for seg in terminal.residue),
        ))
    return tuple(out)


def terminal_set_sha256(terminals: tuple) -> str:
    """全终态集合的定型身份哈希（按 `(页,块,种类,id)` 规范排序后重算）。"""
    ordered = sorted(terminals, key=lambda t: t.sort_key)
    return sha256_canonical([list(t.identity()) for t in ordered])


def alignment_input_fingerprint(*, page_layout_id: str, evidence_set_version: str,
                                snapshot_fingerprint_value: str,
                                blocks: tuple) -> str:
    """本次正式对齐的输入指纹（版本 + 权威快照 + 全量块身份，规范排序）。"""
    ordered = sorted(blocks, key=lambda b: (b.page_number, b.block_index))
    return sha256_canonical({
        "page_layout_id": page_layout_id,
        "evidence_set_version": evidence_set_version,
        "aligner_version": V.ALIGNER_VERSION,
        "partition_validator_version": V.ALIGNMENT_PARTITION_VALIDATOR_VERSION,
        "snapshot_fingerprint": snapshot_fingerprint_value,
        "blocks": [[b.page_number, b.block_index, b.evidence_block_id, b.content_hash,
                    b.evidence_type] for b in ordered],
    })


def _check_terminal_closure(alignment: EvidenceSetAlignment, blocks: tuple,
                            layout: PageLayout,
                            snapshot: EvidenceSetSnapshot) -> tuple:
    """逐终态闭合（§18.3.2-7/-8）：身份、版本、集合、真实文本长度全部重核。"""
    text_by_id = {b.evidence_block_id: b.text for b in blocks}
    for ba in alignment.blocks:
        terminal = ba.terminal
        if terminal is None:
            raise VerifiedAlignmentError(
                f"块 {(ba.page_number, ba.block_index)} 缺正式终态（fail-closed）")
        if terminal.page_layout_id != layout.page_layout_id:
            raise VerifiedAlignmentError(
                f"终态 page_layout_id {terminal.page_layout_id!r} 与真实版式 "
                f"{layout.page_layout_id!r} 不一致（fail-closed）")
        if terminal.evidence_set_version != snapshot.evidence_set_version:
            raise VerifiedAlignmentError(
                f"终态 evidence_set_version {terminal.evidence_set_version!r} 与权威快照的 "
                f"{snapshot.evidence_set_version!r} 不一致（fail-closed）")
        if terminal.aligner_version != V.ALIGNER_VERSION:
            raise VerifiedAlignmentError(
                f"终态 aligner_version 必须为 {V.ALIGNER_VERSION!r}，得到 "
                f"{terminal.aligner_version!r}（fail-closed）")
        expected_schema = (V.ALIGN_SCHEMA_VERSION if ba.record is not None
                           else V.ALIGN_REFUSAL_SCHEMA_VERSION)
        if terminal.schema_version != expected_schema:
            raise VerifiedAlignmentError(
                f"终态 schema_version 必须为 {expected_schema!r}，得到 "
                f"{terminal.schema_version!r}（fail-closed）")
        text = text_by_id.get(ba.evidence_block_id)
        if text is None:
            raise VerifiedAlignmentError(
                f"终态 {ba.evidence_block_id!r} 不对应任何输入块（fail-closed）")
        # §18.3.2-8：block_char_length 用**真实 Evidence 文本**重算，不采信终态自报。
        expected_len = len(tight(text))
        if terminal.block_char_length != expected_len:
            raise VerifiedAlignmentError(
                f"终态 block_char_length {terminal.block_char_length!r} 与按真实 "
                f"Evidence 文本重算的 {expected_len} 不一致；自报长度不得覆盖真实文本"
                f"（fail-closed）")
        if terminal.evidence_block_id != ba.evidence_block_id:
            raise VerifiedAlignmentError(
                f"终态 evidence_block_id 与块不一致（fail-closed）")
        if terminal.page_number != ba.page_number \
                or terminal.block_index != ba.block_index:
            raise VerifiedAlignmentError(
                f"终态的 (page, block) 与块不一致（fail-closed）")
    return alignment_terminal_views(alignment, blocks)


def _is_non_ascending_char_map(terminal) -> bool:
    """终态 `char_map` 的存储序是否**非**升序（§18.5.5：TS4 正式输入边界拒绝）。"""
    previous = None
    for seg in terminal.char_map:
        if len(seg) < 2:
            return True
        if previous is not None and seg[0] < previous:
            return True
        previous = seg[0]
    return False


class VerifiedEvidenceSetAlignment:
    """**已签发**的同次正式对齐能力（运行时对象，不可序列化）。"""

    __slots__ = ("_alignment", "_terminals", "_layout", "_snapshot", "_scope",
                 "_source_kind", "_issuer_version", "_terminals_sha256",
                 "_input_fingerprint", "_authority_fingerprint", "__weakref__")

    def __init__(self, *, alignment, terminals, layout, snapshot, scope,
                 source_kind, issuer_version, terminals_sha256, input_fingerprint,
                 authority_fingerprint) -> None:
        self._alignment = alignment
        self._terminals = terminals
        self._layout = layout
        self._snapshot = snapshot
        self._scope = scope
        self._source_kind = source_kind
        self._issuer_version = issuer_version
        self._terminals_sha256 = terminals_sha256
        self._input_fingerprint = input_fingerprint
        self._authority_fingerprint = authority_fingerprint

    @property
    def alignment(self) -> EvidenceSetAlignment:
        return self._alignment

    @property
    def terminals(self) -> tuple:
        return self._terminals

    @property
    def page_layout(self) -> PageLayout:
        return self._layout

    @property
    def evidence_snapshot(self) -> EvidenceSetSnapshot:
        return self._snapshot

    @property
    def issuer_scope(self) -> str:
        return self._scope

    @property
    def source_kind(self) -> str:
        return self._source_kind

    @property
    def issuer_version(self) -> str:
        return self._issuer_version

    @property
    def terminal_set_sha256(self) -> str:
        return self._terminals_sha256

    @property
    def input_fingerprint(self) -> str:
        return self._input_fingerprint

    @property
    def authority_fingerprint(self) -> str:
        return self._authority_fingerprint

    def identity(self) -> dict:
        return {
            "issuer_version": self._issuer_version,
            "scope": self._scope,
            "source_kind": self._source_kind,
            "provider_version": V.ALIGNMENT_TERMINAL_PROVIDER_VERSION,
            "page_layout_id": self._layout.page_layout_id,
            "evidence_set_version": self._snapshot.evidence_set_version,
            "snapshot_fingerprint": self._snapshot.fingerprint,
            "alignment_input_fingerprint": self._input_fingerprint,
            "terminal_set_sha256": self._terminals_sha256,
            "terminal_count": len(self._terminals),
            "authority_fingerprint": self._authority_fingerprint,
        }

    def to_dict(self) -> dict:
        raise VerifiedAlignmentError(
            "VerifiedEvidenceSetAlignment 是运行时能力，不得序列化；"
            "从磁盘读回的对象不是本进程签发的那一个（fail-closed）")

    def __copy__(self):
        raise VerifiedAlignmentError(
            "VerifiedEvidenceSetAlignment 不可 copy：副本未在签发登记表中")

    def __deepcopy__(self, memo):
        raise VerifiedAlignmentError(
            "VerifiedEvidenceSetAlignment 不可 deepcopy：副本未在签发登记表中")

    def __reduce__(self):
        raise VerifiedAlignmentError(
            "VerifiedEvidenceSetAlignment 不可 pickle：反序列化出的对象"
            "不是本进程签发的那一个")

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return (f"<VerifiedEvidenceSetAlignment scope={self._scope!r} "
                f"terminals={len(self._terminals)} "
                f"fingerprint={self._authority_fingerprint[:12]}…>")


def _align_verified(verified_layout, evidence_authority, *, scope: str,
                    issuer_version: str) -> VerifiedEvidenceSetAlignment:
    """共享的私有算法核：受信版式 + 受信证据权威 => 受信同次对齐结果。

    三个 scope（live / pinned_acceptance / testing）只在这一层分流 gate，之后**共用**
    同一个核，不存在第二套对齐实现。
    """
    issued_capability(verified_layout, "VerifiedPageLayout", (scope,))
    issued_capability(evidence_authority, "VerifiedCurrentEvidenceAuthority", (scope,))
    layout = verified_layout.layout
    company_id, document_id = layout.company_id, layout.document_id
    snapshot, blocks = evidence_authority.load_snapshot_and_blocks(
        company_id=company_id, document_id=document_id,
        document_version=layout.document_version)
    alignment = align_evidence_set(layout, blocks, snapshot=snapshot)
    if alignment.page_layout_id != layout.page_layout_id:
        raise VerifiedAlignmentError("对齐结果的 page_layout_id 与真实版式不一致")
    terminals = _check_terminal_closure(alignment, blocks, layout, snapshot)
    for terminal in terminals:
        if _is_non_ascending_char_map(terminal):
            raise VerifiedAlignmentError(
                f"终态 {terminal.terminal_id!r} 的 char_map 存储序非升序；"
                f"TS4 正式输入边界拒绝乱序终态（fail-closed）")
    infp = alignment_input_fingerprint(
        page_layout_id=layout.page_layout_id,
        evidence_set_version=snapshot.evidence_set_version,
        snapshot_fingerprint_value=snapshot.fingerprint, blocks=blocks)
    tsha = terminal_set_sha256(terminals)
    fp = sha256_canonical({
        "provider_version": V.ALIGNMENT_TERMINAL_PROVIDER_VERSION,
        "scope": scope, "issuer_version": issuer_version,
        "page_layout_id": layout.page_layout_id,
        "evidence_set_version": snapshot.evidence_set_version,
        "snapshot_fingerprint": snapshot.fingerprint,
        "alignment_input_fingerprint": infp,
        "terminal_set_sha256": tsha,
        "aligner_version": V.ALIGNER_VERSION,
        "partition_validator_version": V.ALIGNMENT_PARTITION_VALIDATOR_VERSION,
        "layout_authority_fingerprint": verified_layout.authority_fingerprint,
        "evidence_authority_fingerprint": evidence_authority.authority_fingerprint,
    })
    obj = VerifiedEvidenceSetAlignment(
        alignment=alignment, terminals=terminals, layout=layout, snapshot=snapshot,
        scope=scope, source_kind=verified_layout.source_kind,
        issuer_version=issuer_version, terminals_sha256=tsha,
        input_fingerprint=infp, authority_fingerprint=fp)
    return _issue_capability(obj, "VerifiedEvidenceSetAlignment", scope)


# ---------------------------------------------------------------------------
# 8b. 历史验收：由**冻结终态**还原逐块对齐结论（§18.14.2-3）
# ---------------------------------------------------------------------------
#
# 真实验收**禁止**重跑 `align_block` / `align_evidence_set`：冻结产物才是那一刻的
# 事实，重跑等于用一个"可能不同"的新结论替换历史。因此这里只做**还原 + 逐项重核**：
# 终态 dict 由 `TextAlignmentRecord.from_dict` / `AlignmentRefusalRecord.from_dict`
# 走它们自己的构造校验（因此"改一个字段再重算 id"过不了），随后逐项对回冻结行的
# 自报值，再把同样的闭合检查（`_check_terminal_closure`）跑一遍。
#
# 还原出的 `BlockAlignment` 只携带冻结行**确实记录**的字段与还原出的终态；残差明细
# 一律留空并从 TS4 的正式输入中排除（§18.14.2-7）。因此这里**不**假装重建了一个
# 完整的对齐诊断对象。

def restore_pinned_alignment_from_frozen_rows(*, rows, blocks, layout: PageLayout,
                                              snapshot: EvidenceSetSnapshot) -> tuple:
    """由冻结 `rows` 还原 `(BlockAlignment 元组, 终态元组)`（fail-closed）。"""
    if not isinstance(layout, PageLayout):
        raise VerifiedAlignmentError("还原冻结终态需要真实 PageLayout 对象")
    if not isinstance(snapshot, EvidenceSetSnapshot):
        raise VerifiedAlignmentError("还原冻结终态需要权威 EvidenceSetSnapshot")
    snapshot.assert_current()
    snapshot.assert_bound_to(layout)
    if not isinstance(rows, (list, tuple)) or not rows:
        raise VerifiedAlignmentError("冻结 rows 必须为非空序列（fail-closed）")
    ordered_blocks = sorted(blocks, key=lambda b: (b.page_number, b.block_index))
    ordered_rows = sorted(rows, key=lambda r: (r["page_number"], r["block_index"]))
    if len(ordered_rows) != len(ordered_blocks):
        raise VerifiedAlignmentError(
            f"冻结 rows 数 {len(ordered_rows)} 与 Evidence 成员数 "
            f"{len(ordered_blocks)} 不一致；终态不得缺失或多余（fail-closed）")
    out: list = []
    for row, block in zip(ordered_rows, ordered_blocks):
        where = f"rows[(page={row['page_number']}, block={row['block_index']})]"
        block.assert_identity()
        block.assert_bound_to(layout)
        if (row["page_number"], row["block_index"], row["evidence_block_id"]) != (
                block.page_number, block.block_index, block.evidence_block_id):
            raise VerifiedAlignmentError(
                f"{where} 的块身份与 Evidence 成员不一致："
                f"{row['evidence_block_id']!r} != {block.evidence_block_id!r}"
                f"（fail-closed）")
        if row.get("evidence_set_version") != snapshot.evidence_set_version:
            raise VerifiedAlignmentError(
                f"{where} 的 evidence_set_version 与权威快照不一致（fail-closed）")
        if row.get("page_layout_id") != layout.page_layout_id:
            raise VerifiedAlignmentError(
                f"{where} 的 page_layout_id 与真实版式不一致（fail-closed）")
        raw_terminal = row.get("terminal")
        if not isinstance(raw_terminal, dict):
            raise VerifiedAlignmentError(
                f"{where} 缺正式终态 dict；缺失不得被表述为对齐结论（fail-closed）")
        kind = raw_terminal.get("schema_type")
        if kind == "TextAlignmentRecord":
            terminal = TextAlignmentRecord.from_dict(raw_terminal)
            record, refusal = terminal, None
        elif kind == "AlignmentRefusalRecord":
            terminal = AlignmentRefusalRecord.from_dict(raw_terminal)
            record, refusal = None, terminal
        else:
            raise VerifiedAlignmentError(
                f"{where} 的终态 schema_type={kind!r} 不是正式终态类型（fail-closed）")
        if row.get("terminal_schema_type") != kind:
            raise VerifiedAlignmentError(
                f"{where} 的 terminal_schema_type 与终态自身的 schema_type 不一致"
                f"（fail-closed）")
        # 逐项对回行自报值：行是**诊断投影**，只要它和终态说的不是同一件事就停。
        # `verdict` 只存在于 `TextAlignmentRecord`（拒绝终态按定义无 verdict），
        # `exact_coverage` 只存在于 `AlignmentRefusalRecord`；两者各查各的字段，
        # 不把一侧的语义套到另一侧。
        checks = [("matched_chars", terminal.matched_chars),
                  ("block_char_length", terminal.block_char_length),
                  ("quantized_coverage", terminal.coverage),
                  ("is_citable", terminal.is_citable()),
                  ("evidence_set_version", terminal.evidence_set_version),
                  ("page_layout_id", terminal.page_layout_id)]
        # `exact_coverage` 是**未量化**比值；记录侧只有量化后的 `coverage`，
        # 因此这里按 `matched_chars / block_char_length` 重算，不拿量化值顶替。
        exact = terminal.matched_chars / terminal.block_char_length
        if abs(row.get("exact_coverage", -1.0) - exact) > 1e-12:
            raise VerifiedAlignmentError(
                f"{where} 的 exact_coverage={row.get('exact_coverage')!r} 与按精确分子"
                f"重算的 {exact!r} 不一致（fail-closed）")
        if record is not None:
            checks.append(("verdict", record.verdict))
        else:
            checks.append(("refusal_reason", refusal.refusal_reason))
            # 拒绝终态自身没有 `verdict`；行里的 verdict 是块级判定，因此只能查它
            # 是否与"不可引用"自洽（aligned ⟺ 可引用），不能假装它与终态字段同源。
            if row.get("verdict") == "aligned":
                raise VerifiedAlignmentError(
                    f"{where} 是拒绝终态却把块级 verdict 写成 'aligned'；"
                    f"拒绝不得同时自称已对齐（fail-closed）")
        for name, expected in checks:
            if row.get(name) != expected:
                raise VerifiedAlignmentError(
                    f"{where} 的 {name}={row.get(name)!r} 与终态重算值 {expected!r} "
                    f"不一致（fail-closed）")
        expected_len = len(tight(block.text))
        if terminal.block_char_length != expected_len:
            raise VerifiedAlignmentError(
                f"{where} 的 block_char_length {terminal.block_char_length} 与按真实 "
                f"Evidence 文本重算的 {expected_len} 不一致（fail-closed）")
        out.append(BlockAlignment(
            evidence_block_id=block.evidence_block_id,
            page_number=block.page_number, block_index=block.block_index,
            evidence_set_version=snapshot.evidence_set_version,
            page_layout_id=layout.page_layout_id,
            aligner_version=terminal.aligner_version,
            block_char_length=terminal.block_char_length,
            matched_chars=terminal.matched_chars,
            unmatched_chars=terminal.block_char_length - terminal.matched_chars,
            exact_coverage=row["exact_coverage"],
            # 块级 verdict 直接取冻结行的**确定值**（记录侧已与 `record.verdict`
            # 逐一比对过），不从拒绝终态读一个它没有的字段。
            verdict=row["verdict"], is_citable=terminal.is_citable(),
            record=record, refusal_record=refusal,
            record_refusal=(None if refusal is None else refusal.refusal_reason),
            char_map=tuple(getattr(terminal, "char_map", ()) or ())))
    return tuple(out), tuple(ba.terminal for ba in out)


def _issue_pinned_alignment_from_frozen_rows(
        verified_layout, evidence_authority, *, rows,
        issuer_version: str) -> VerifiedEvidenceSetAlignment:
    """**验收专用**：由冻结 rows 还原并按 `issuer_version` 签发终态能力。

    与 `_align_verified` 共用同一身份公式与同一条 `_check_terminal_closure`；唯一区别
    是终态来自冻结产物而不是当场重跑。
    """
    issued_capability(verified_layout, "VerifiedPageLayout", ("pinned_acceptance",))
    issued_capability(evidence_authority, "VerifiedCurrentEvidenceAuthority",
                      ("pinned_acceptance",))
    layout = verified_layout.layout
    snapshot, blocks = evidence_authority.load_snapshot_and_blocks(
        company_id=layout.company_id, document_id=layout.document_id,
        document_version=layout.document_version)
    restored, terminals = restore_pinned_alignment_from_frozen_rows(
        rows=rows, blocks=blocks, layout=layout, snapshot=snapshot)
    alignment = EvidenceSetAlignment(
        page_layout_id=layout.page_layout_id,
        evidence_set_version=snapshot.evidence_set_version,
        aligner_version=V.ALIGNER_VERSION, blocks=restored,
        summary={"restored_from_frozen_rows": True,
                 "block_count": len(restored)},
        snapshot=snapshot)
    terminals = _check_terminal_closure(alignment, blocks, layout, snapshot)
    for terminal in terminals:
        if _is_non_ascending_char_map(terminal):
            raise VerifiedAlignmentError(
                f"终态 {terminal.terminal_id!r} 的 char_map 存储序非升序；"
                f"TS4 正式输入边界拒绝乱序终态（fail-closed）")
    infp = alignment_input_fingerprint(
        page_layout_id=layout.page_layout_id,
        evidence_set_version=snapshot.evidence_set_version,
        snapshot_fingerprint_value=snapshot.fingerprint, blocks=blocks)
    tsha = terminal_set_sha256(terminals)
    fp = sha256_canonical({
        "provider_version": V.ALIGNMENT_TERMINAL_PROVIDER_VERSION,
        "scope": "pinned_acceptance", "issuer_version": issuer_version,
        "page_layout_id": layout.page_layout_id,
        "evidence_set_version": snapshot.evidence_set_version,
        "snapshot_fingerprint": snapshot.fingerprint,
        "alignment_input_fingerprint": infp,
        "terminal_set_sha256": tsha,
        "aligner_version": V.ALIGNER_VERSION,
        "partition_validator_version": V.ALIGNMENT_PARTITION_VALIDATOR_VERSION,
        "layout_authority_fingerprint": verified_layout.authority_fingerprint,
        "evidence_authority_fingerprint": evidence_authority.authority_fingerprint,
        "restored_from_frozen_rows": True,
    })
    obj = VerifiedEvidenceSetAlignment(
        alignment=alignment, terminals=terminals, layout=layout, snapshot=snapshot,
        scope="pinned_acceptance", source_kind=verified_layout.source_kind,
        issuer_version=issuer_version, terminals_sha256=tsha,
        input_fingerprint=infp, authority_fingerprint=fp)
    return _issue_capability(obj, "VerifiedEvidenceSetAlignment", "pinned_acceptance")


def align_evidence_set_verified(verified_layout, evidence_authority
                                ) -> VerifiedEvidenceSetAlignment:
    """**唯一**生产对齐签发入口：同次正式对齐结果当场签发。

    只接受 `build_verified_page_layout` 与 `bind_current_evidence_authority` 签发的
    `live` 能力；公司 / 文档 / 版本一律取自**真实版式对象**，不接受调用方参数。
    普通的 `EvidenceSetAlignment` / terminal tuple / 自造 gateway 没有注入路径。
    """
    return _align_verified(
        verified_layout, evidence_authority, scope="live",
        issuer_version=V.VERIFIED_ALIGNMENT_ISSUER_VERSION)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())

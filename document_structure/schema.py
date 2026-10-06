"""树结构调整 TS1：文档结构层公共类型、内容寻址身份与构造期不变量。

**范围（严格限定 TS1）**：只声明式地定义公共类型、版本引用、确定性身份派生、
严格序列化/反序列化与 fail-closed 不变量。本模块**不做**任何 I/O：
不读 PDF、不建 PageLayout、不识别标题、不对齐 Evidence、不切 span、不识别表格、
不读 Contract、不写数据库、不调 LLM / 网络、不注册 Retriever / ToolRegistry。
具体算法分属 TS2–TS7B（见 `TREE_STRUCTURE_IMPLEMENTATION_PLAN.md` §13.2）。

设计约定（计划 §1.0）：

1. 全部 `@dataclass(frozen=True)`；`__post_init__` 违反不变量即抛
   `SchemaValidationError`（来自最低层的 `document_structure.canonical`，
   结构层**不**依赖 `harness` 的 Topic runtime schema 模块）。
2. 浮点一律在构造期量化到 `versions.FLOAT_PRECISION` 后才进入哈希与持久化，
   且全部必须 `math.isfinite`（NaN / ±Inf 不得通过比较、更不得进入 JSON）。
3. `from_dict` 严格：未知字段、缺必填字段、类型错误、非法枚举、越界数值、坏身份、
   旧 wire format 版本一律拒绝。

## 两种身份（TS1 修正轮 P1-1）

本模块为每个**正式持久化/交换类型**同时提供两个不同的身份字符串：

| | 字段 | 形状 | 由什么决定 | 用途 |
|---|---|---|---|---|
| **稳定定位身份** | `<x>_locator` | `loc-<kind>-<hex16>` | 只由"对象是谁 / 在哪"决定，**不含**随内容变化的字段 | 跨修订定位"同一逻辑对象的最新修订" |
| **不可变 revision / content identity** | `<x>_id` | `<kind>-<hex16>` | locator + schema version + builder/algorithm/rule/normalization version + 上游对象身份与来源版本 + **全部会改变业务含义的规范化内容** | Store、引用、current/stale、依赖指纹 |

因此：内容一改，`*_id` 必变而 `*_locator` 不变；`__post_init__` 重算二者并比对，
"同一 `*_id` 对应两种规范形"在构造期就 fail-closed，不依赖"调用方理应确定性生成"。

引用规则（三条，均由构造期校验强制）：

1. **包含关系内的对象引用容器 `*_locator`**（`OutlineNode` / `OutlineSpan` /
   `ReferenceEdge` 在 `DocumentOutline` 内），否则容器身份会自指成环；
2. **非包含的派生对象引用来源 `*_id`**（`DocumentOutline.page_layout_id`、
   `TableObject.document_outline_id`），从而获得"来源内容变化即 stale"的性质；
3. **跨对象同级引用用 `*_locator`**（`TableObject.continuation_of_locator` /
   `continuation_locators`）：同级引用若用 `*_id`，则 A 的身份含 B 的 id、B 的身份
   含 A 的 id，两者互相依赖而**不可构造**（`续表` 链条正是这种情形）。定位与内容
   无关，可先于对方内容确定，故不成环；同时它进入规范形，因此续表关系变化时
   `table_id` 仍必须变化。

## 不由本层承载的语义

- `EvidenceBlock` 是不可变来源/引用锚点，不是正式业务材料边界；
- 正式 Pack 只有 `harness.topic_schema.TopicResearchPack`；
- 财务主表金额权威只能来自 `FinancialSnapshot` / `FinancialFactPack`；
  `TableObject.structure_class` 只是**结构分类**，不是 authority verdict；
- `NavigationSynopsis` 用有逐字符来源的 `snippets[]`，只导航、不作证据；
- 父节点的全部后代只能作为**候选 / context 范围**，不得自动全部成为某 aspect
  的正式 source；
- `superseded_version` 只能指同一逻辑文档的旧解析版本（本模块因此**不定义**
  任何 `superseded_version` 字段）；
- 表格层只表达**可验证的物理与来源结构**；授信/财务/业务口径由后续事实层派生，
  因此 `TableRow` 不含 `semantics` 之类业务解释字段。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Any, Callable, Sequence

from contracts.schema_v2 import CONTENT_ROLES, DISPLAY_TIERS

from document_structure import versions as V
from document_structure.canonical import (
    SchemaValidationError,
    canonical_text,
    identity,
    is_finite,
    locator,
    sha256_canonical,
    to_json_value,
)

# ---------------------------------------------------------------------------
# 已裁决语义的机器可读声明（供测试断言；不是文档注释）
# ---------------------------------------------------------------------------

# 父节点全部后代只作候选/context；不得自动成为某 aspect 的正式 source。
DESCENDANTS_ARE_CANDIDATES_ONLY = True
# `superseded_version` 只能指同一逻辑文档的旧解析版本。
SUPERSEDED_VERSION_SAME_LOGICAL_DOCUMENT_ONLY = True
# 金额权威来源（`structure_class` 不得被当作 authority verdict）。
AMOUNT_AUTHORITY_SOURCES = ("FinancialSnapshot", "FinancialFactPack")
TABLE_STRUCTURE_CLASS_IS_AUTHORITY_VERDICT = False

# TS1 **不得**提前实现的类型（推迟到 TS7A，见计划 §7.1）。
DEFERRED_TO_TS7A = (
    "AspectMaterialAggregationInput",
    "AspectMaterialOccurrence",
    "MaterialDedupCluster",
    "MaterialConflictGroup",
    "AspectMaterialAggregationResult",
)

# ---------------------------------------------------------------------------
# 枚举白名单（封闭；非法值一律拒绝）
# ---------------------------------------------------------------------------

ROTATIONS = (0, 90, 180, 270)
FURNITURE_KINDS = ("header", "footer", "page_number", "watermark", "other")
CANDIDATE_SOURCES = (
    "pdf_bookmarks",
    "toc_page",
    "body_numbering",
    "layout_style",
    "repeated_noise_exclusion",
    "caption_recurrence",
)
SYNOPSIS_STATUSES = ("available", "synopsis_unavailable")
# TS4 §18.4.4（`nss-1 → nss-2`）：追加 `table_only_pending_ts5`。
#
# 一个节点的内容**全部**落在 `table_inside` / `table_adjacency`（TS5 决议前既不进段落
# 也不进简介）时，它的简介必须显式说明"等 TS5"，**不得**与真正的 `no_span`（该节点
# 没有任何正文内容）混淆 —— 否则表格延期造成的缺口在产物里不可审计。
# 词表是 wire format 的一部分（`from_dict` 按封闭集合拒绝），因此扩充即升版。
# TS5 §19.0.1（方案 C）：本词表是 **TS4 的冻结词表**，**恒为五值**，不随 TS5 变化。
#
# TS5 首轮编码曾在此追加 `table_material_available_no_text_synopsis`。那是错误的隔离
# 方向：该原因描述的是 **TS5 final** 节点的状态（"有正式 TableObject、却没有 final 可
# 引用 paragraph"），而本词表描述的是 **TS4** 节点的状态（"内容还在等 TS5 决议"）。
# 两者各有一个对方不允许出现的专属值：
#
# - `table_only_pending_ts5` 只在 TS4 词表里（final 之后不得再产出"还在等 TS5"）；
# - `table_material_available_no_text_synopsis` 只在 final 词表里（见
#   `table_schema.FINAL_SYNOPSIS_REASON_CODES`）。
#
# 合并两者会让同一 wire 版本出现两套接受集合，也正是"TS4 身份漂移"的来源。因此本表
# 保持五值不动，final 用另一个类型 + 另一个版本轴 + 另一个词表。
SYNOPSIS_REASON_CODES = ("no_span", "empty_text", "length_exceeded",
                         "alignment_failed", "table_only_pending_ts5")
ALIGNMENT_VERDICTS = ("aligned", "partially_aligned", "unaligned")
# 对齐**拒绝终态**的封闭原因码集合（TS3 §五）。"拒绝产出记录"不是一个自由文本
# 理由，而是一个必须可枚举、可对账、可测试的终态原因：
#
# - `quantization_boundary_refused`：精确比值低于冻结阈值，而量化展示值不低——
#   记录类型的展示字段会与判定结论相反，故该块的正式终态是拒绝记录。
ALIGNMENT_REFUSAL_REASONS = ("quantization_boundary_refused",)
# 残差分类严重度升序（索引即严重度）：记录级 `residue_class` 取**最高**严重度。
RESIDUE_CLASS_SEVERITY = ("page_furniture", "engine_artifact", "column_reorder", "unexplained")
SPAN_ROLES = ("body", "list", "table_caption", "table_note", "unassigned")
# `OutlineSpan` 的 os-4 **兼容读层**接受的算法版本集合（§18.11.2③ / §18.12.2）。
# 值全部来自 `versions`（本模块不得内联版本字面量）：
# `sb-1`（TS3：历史已归属 + 232 个 unassigned）、当前 TS4 正文算法，以及被替换掉的
# TS4 正文算法历史值（`TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS`）——历史载荷必须仍可
# 逐字节读回，否则冻结产物无法读回、无法 canonical 重建。
# 角色级真值表由 TS4 正式 builder/verifier 强制，不在 base wire class 上做：
# TS4 正式记录仍**只**接受当前 TS4 正文算法（`span_schema._bind_span_builder_version`），
# 旧值在这里合法只意味着"可读回"，不意味着"可被当作当前产物消费"。
SPAN_BUILDER_VERSIONS: tuple[str, ...] = tuple(dict.fromkeys((
    V.SPAN_BUILDER_VERSION,
    V.TS4_BODY_SPAN_BUILDER_VERSION,
    V.OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION,
    *V.TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS,
)))
# 显式未归属的**原因**必须可审计（不得只写"未归属"而不写为什么）。
#
# TS3 已知 P1 收口轮（`os-3 → os-4`）：原先正文 / 目录 / 书签三种**来源完全不同**的
# 未归属候选被压成同一个 `boundary_ambiguous`，于是"为什么没成为节点"在正式产物里
# 不可执行地区分（无法按原因统计、无法定位是召回问题还是层级问题）。下面 5 个值把
# 来源与原因分开：正文证据不足 / 编号列表歧义 / 目录未命中 / 书签未命中 / 层级冲突。
# 旧值保留（封闭集合只增不改），因此旧载荷仍可读。
UNASSIGNED_REASONS = (
    "toc_only_candidate",
    "no_heading_context",
    "boundary_ambiguous",
    "below_last_heading",
    "cross_heading_orphan",
    "outside_any_outline_node",
    # --- os-4 新增：可执行的细分原因 ---
    "insufficient_heading_evidence",
    "numbered_list_ambiguity",
    "toc_unmatched",
    "bookmark_unmatched",
    "hierarchy_conflict",
)
STRUCTURE_CLASSES = ("financial_main_statement", "note_table", "ordinary_business_table")
# 结构信号词表**复用**既有原语 `harness.table_structure.table_body_signals` 的输出词汇，
# 禁止在本层另造同义名（否则 TS5 必须在两套名字间翻译，等于复制表格规则）。
TABLE_STRUCTURE_SIGNALS = ("unit", "columnar", "data", "closure", "continuation")
# 列边界一致性证据（计划 §1.2：没有该证据时不得创建 TableObject）。
COLUMN_BOUNDARY_SIGNAL = "columnar"
ROW_KINDS = ("header", "body", "total", "subtotal")
TITLE_SOURCES = ("outline_node_title", "caption_line", "none")
EDGE_KINDS = ("parent_child", "cross_reference", "table_continuation", "toc_to_body")
REF_KINDS = ("node", "span", "table", "toc", "evidence")

# 对象定位前缀（`loc-<kind>-`，见 canonical.locator）。跨对象**同级**引用一律用定位
# 而不是 revision id，否则两个对象的身份会互相依赖而无法构造。
LOCATOR_PREFIXES = {
    "pl": "loc-pl-", "do": "loc-do-", "os": "loc-os-", "to": "loc-to-",
    "ns": "loc-ns-", "anp": "loc-anp-", "al": "loc-al-", "re": "loc-re-",
    # TS1.3 §四.4：`TocSource` 的位置身份前缀（运行时对象，不进 wire format）。
    "toc": "loc-toc-",
}
TABLE_LOCATOR_PREFIX = LOCATOR_PREFIXES["to"]
TOC_SOURCE_LOCATOR_PREFIX = LOCATOR_PREFIXES["toc"]

# 按 edge kind 规定合法端点类型（**不是**宽松前缀检查，且下游仍要做对象存在性检查）。
# 值形如 `(允许的来源类型集合, 允许的目标类型集合)`。TS1.2 起 `cross_reference`
# 的目标可以是 `node` / `span` / `table`（任务书 §4.6：source occurrence →
# target node/span/table），因此端点不能再用"单一类型对"表达。
EDGE_ENDPOINT_KINDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "parent_child": (("node",), ("node",)),
    # 来源可以是正文 span：引用文字真正出现在哪里，边就从哪里出发
    # （P1-3.4 的"按 source occurrence → target 重新划分来源绑定"）。
    "cross_reference": (("node", "span"), ("node", "span", "table")),
    "table_continuation": (("table",), ("table",)),
    # TS1.3 §四.5：`toc_to_body` 的目标收窄为 `node`。目录项指向的是**正文标题**，
    # 解析路径的最后一步必须"目录项声明标题 ↔ 目标节点真实 `title`"可确定性核对，
    # 只有 `node` 有被本层类型化的标题。收窄是安全的：端点类型只在 `to_ref` 非空时
    # 校验，未解析的 `toc_to_body` 边仍然允许缺目标。
    "toc_to_body": (("toc",), ("node",)),
}

# 未解析边必须给出的稳定原因码（封闭词表）。
UNRESOLVED_EDGE_REASONS = (
    "deferred_to_later_stage",
    "target_not_in_scope",
    "target_object_not_available",
    "ambiguous_candidates",
    "no_textual_evidence",
    # TS1.3 §四.6：目录页声明页码与 PDF 物理页码之间的偏移**无法确定**时，
    # 保持 unresolved 并给出这个稳定原因码——不得猜最近页面、不得用固定偏移。
    "toc_page_label_mapping_unproven",
)

# 本层**自身持有**对象集合、因而无需外部上下文即可核验端点存在性的引用类型。
# `node` → `DocumentOutline.nodes`；`span` → `DocumentOutline.unassigned`
# （本层持有的**未归属** span；节点内正文 span 由调用方经核验上下文提供）。
OUTLINE_OWNED_REF_KINDS = ("node", "span")

# 本层**能给出真实对象**的引用类型全集：resolved 资格只能由这些类型的真实对象证明
# （node/span 见上；table 必须由 `ReferenceValidationContext.tables` 提供真实
# `TableObject`；toc 必须由 `ReferenceValidationContext.toc_sources` 提供真实
# `TocSource`）。除此之外的引用类型见 `UNRESOLVABLE_REF_KINDS`。
OBJECT_BACKED_REF_KINDS = ("node", "span", "table", "toc")

# 本层**没有**正式对象类型、因而任何边都不得标为 resolved 的引用类型：
# `evidence:` 块由上游 Evidence 层产生，`document_structure` 不持有它们的对象。
# 上一轮用"调用方传入的字符串清单"证明它们存在，等于把"对象存在"退化成一句自述
# （TS1.2 P1-2）。因此改为 fail-closed：这类端点只允许出现在**未解析**边上
# （带 reason_code 与原文声明目标）。
#
# TS1.3 §四.1：`toc:` **移出**本清单。上一轮把"toc 在本层没有类型化文本载体"扩大成
# 一条**永久结构禁令**（`toc_to_body` 一律不得 resolved），与已批准的树结构方向冲突：
# 目录项必须是重要导航候选，证据充分时应存在对象级解析路径。本轮为它补上真实来源
# 对象 `TocSource`（由真实 PageLayout 上的真实 LayoutLine 重算身份），于是 toc 从
# "不可证明"变成"可对象级证明"——但**只有**真实对象能证明它，字符串 id 依然不行。
UNRESOLVABLE_REF_KINDS = ("evidence",)

# **本层能给出真实原文载体**、因而存在 resolved 路径的引用类型：
# `node` → `OutlineNode.title` + `source_anchor`；`span` → `OutlineSpan` 的
# `layout_line_refs`；`toc` → `TocSource` 绑定的真实 `LayoutLine` 行内字符区间。
# 三者的 occurrence 都必须落到**真实 LayoutLine** 上并逐字符核对，见
# `_occurrence_layout_line`。
TEXT_SOURCE_REF_KINDS = ("node", "span", "toc")

# **本层存在对象级核验路径**的边类型：
# - `parent_child`：树内派生关系，由 `nodes` 的父子与 `child_ids` 对称性证明；
# - `cross_reference`：来源是真实文本载体的 occurrence，目标是真实对象；
# - `table_continuation`：两端都是真实 `TableObject`，由其自报的
#   `continuation_of_locator` / `continuation_locators` 与页序**互相印证**；
# - `toc_to_body`：来源是真实 `TocSource`，目标是真实节点，且目录项声明标题与目标
#   节点标题、目录页标签与 PDF 物理页码都必须对象级成立。
RESOLVABLE_EDGE_KINDS = (
    "parent_child", "cross_reference", "table_continuation", "toc_to_body",
)

# 必须绑定真实文本 occurrence 的边类型（否则"同一来源对同一目标的多处 详见 /
# 如下表"会被并成一条）。`parent_child` 是纯结构边，无文本来源；
# `table_continuation` 的绑定证据是**对象级**的续接自报关系（两个真实
# `TableObject` 互相印证），不是一段可选文本——上一轮要求它携带 `table:` 来源的
# occurrence，实际上没有任何真实原文切片可核对（"（续表）"不是表格对象的文本），
# 只能靠自报字符串通过。本轮按 P1-3.4 重新划分：续表关系的证据留在对象级核验。
OCCURRENCE_REQUIRED_EDGE_KINDS = ("cross_reference", "toc_to_body")

# 页面允许范围容差（pt）：坐标已量化到 3 位小数，跨引擎会有亚 pt 级越界。
BBOX_PAGE_TOLERANCE_PT = 1.0

# 目录行的**点线填充**字符（"第一节标题 ......... 1" 里的引导点）。它们属于版式
# 装饰，不属于标题文字，因此标题归一（`_normalize_toc_title`）里一律丢弃。
# 这是一张**结构**字符表，与公司、文档、语言无关；不包含任何自然语言同义词。
_TOC_TITLE_FILLER_CHARS = (".", "·", "…", "．")

_RE_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_RE_REF = re.compile(r"^(node|span|table|toc|evidence):.+$")
_WORKSPACE_FORBIDDEN_HINTS = ("  ", "\n", "\t")


def _err(typename: str, msg: str) -> None:
    raise SchemaValidationError(f"{typename}: {msg}")


def quantize(v: float) -> float:
    """浮点确定性量化（计划 §1.0）：参与哈希/持久化的浮点先 round(x, FLOAT_PRECISION)。

    NaN / ±Inf 一律拒绝：`round(nan, 3)` 仍是 nan，会让比较全部失效并污染 JSON。
    """
    if not is_finite(v):
        raise SchemaValidationError(f"数值必须为有限实数（不得 NaN/±Inf），得到 {v!r}")
    return round(float(v), V.FLOAT_PRECISION)


# ---------------------------------------------------------------------------
# 严格取值助手（fail-closed；不猜测、不降级、不静默补默认值）
# ---------------------------------------------------------------------------

def _reject_unknown(d: Any, allowed: set[str], typename: str) -> dict:
    """先核对判别符，再拒绝未知字段。

    判别符先行：把 A 类型的字典喂给 B 类型时必须得到"schema_type 不符"这一
    准确诊断，而不是一串"未知字段"（否则跨类型混淆会被误读为字段增删问题）。
    """
    if not isinstance(d, dict):
        _err(typename, f"from_dict 需要 dict，得到 {type(d).__name__}")
    st = d.get("schema_type")
    if st is not None and st != typename:
        _err(typename, f"schema_type 必须为 {typename!r}，得到 {st!r}"
                       f"（跨类型字典混淆）")
    unknown = set(d) - allowed
    if unknown:
        _err(typename, f"含未知字段: {sorted(unknown)}")
    return d


def _need_str(d: dict, key: str, typename: str, *, none_ok: bool = False,
              empty_ok: bool = False) -> str | None:
    v = d.get(key)
    if v is None:
        if none_ok:
            return None
        _err(typename, f"缺必填字段: {key}")
    if not isinstance(v, str):
        _err(typename, f"{key} 必须为字符串，得到 {type(v).__name__}")
    if v == "" and not empty_ok:
        _err(typename, f"{key} 必须为非空字符串")
    return v


def _need_int(d: dict, key: str, typename: str, *, none_ok: bool = False,
              lo: int | None = None, hi: int | None = None) -> int | None:
    v = d.get(key)
    if v is None:
        if none_ok:
            return None
        _err(typename, f"缺必填字段: {key}")
    if not isinstance(v, int) or isinstance(v, bool):
        _err(typename, f"{key} 必须为 int，得到 {v!r}")
    if lo is not None and v < lo:
        _err(typename, f"{key} 不得小于 {lo}，得到 {v}")
    if hi is not None and v > hi:
        _err(typename, f"{key} 不得大于 {hi}，得到 {v}")
    return v


def _need_bool(d: dict, key: str, typename: str) -> bool:
    v = d.get(key)
    if not isinstance(v, bool):
        _err(typename, f"{key} 必须为 bool，得到 {v!r}")
    return v


def _need_num(d: dict, key: str, typename: str, *, lo: float | None = None,
              hi: float | None = None) -> float:
    v = d.get(key)
    if not is_finite(v):
        _err(typename, f"{key} 必须为有限实数（不得 NaN/±Inf），得到 {v!r}")
    f = float(v)
    if lo is not None and f < lo:
        _err(typename, f"{key} 不得小于 {lo}，得到 {f}")
    if hi is not None and f > hi:
        _err(typename, f"{key} 不得大于 {hi}，得到 {f}")
    return f


def _need_enum(d: dict, key: str, typename: str, allowed: Sequence[Any]) -> Any:
    v = d.get(key)
    if v not in allowed:
        _err(typename, f"{key} 必须属于 {tuple(allowed)}，得到 {v!r}")
    return v


def _need_str_tuple(d: dict, key: str, typename: str, *, default: tuple = (),
                    item_max_len: int | None = None) -> tuple[str, ...]:
    v = d.get(key)
    if v is None:
        return default
    if not isinstance(v, list):
        _err(typename, f"{key} 必须为 list[str]，得到 {type(v).__name__}")
    out: list[str] = []
    for x in v:
        if not isinstance(x, str):
            _err(typename, f"{key} 含非字符串元素: {x!r}")
        if x == "":
            _err(typename, f"{key} 含空字符串元素")
        if item_max_len is not None and len(x) > item_max_len:
            _err(typename, f"{key} 元素超过 {item_max_len} 字符: {x!r}")
        out.append(x)
    return tuple(out)


def _need_children(d: dict, key: str, typename: str,
                   decoder: Callable[[Any], Any],
                   *, default: tuple = ()) -> tuple:
    v = d.get(key)
    if v is None:
        return default
    if not isinstance(v, list):
        _err(typename, f"{key} 必须为 list，得到 {type(v).__name__}")
    out: list[Any] = []
    for i, item in enumerate(v):
        try:
            out.append(decoder(item))
        except SchemaValidationError as e:
            _err(typename, f"{key}[{i}] 解码失败：{e}")
    return tuple(out)


def _need_bbox(d: dict, key: str, typename: str) -> tuple[float, float, float, float]:
    v = d.get(key)
    if not isinstance(v, list) or len(v) != 4:
        _err(typename, f"{key} 必须为长度 4 的数值数组 [x0,y0,x1,y1]，得到 {v!r}")
    out = []
    for i, x in enumerate(v):
        if not is_finite(x):
            _err(typename, f"{key}[{i}] 必须为有限实数（不得 NaN/±Inf），得到 {x!r}")
        out.append(quantize(x))
    _check_bbox(out, f"{typename}.{key}")
    return (out[0], out[1], out[2], out[3])


def _need_anchor(d: dict, key: str, typename: str) -> tuple[int, int, tuple]:
    v = d.get(key)
    if not isinstance(v, list) or len(v) != 3:
        _err(typename, f"{key} 必须为 [page_number, line_index, bbox]，得到 {v!r}")
    if v[0] is None or v[1] is None:
        _err(typename, f"{key} 的页码/行号不得为空")
    page = _need_int({key: v[0]}, key, typename, lo=1)
    line = _need_int({key: v[1]}, key, typename, lo=0)
    bbox = _need_bbox({key: v[2]}, key, typename)
    return (page, line, bbox)


def _need_int_pair(d: dict, key: str, typename: str, *, lo: int = 0,
                   strict: bool = False) -> tuple[int, int]:
    v = d.get(key)
    if not isinstance(v, list) or len(v) != 2:
        _err(typename, f"{key} 必须为长度 2 的 int 数组，得到 {v!r}")
    for x in v:
        if not isinstance(x, int) or isinstance(x, bool):
            _err(typename, f"{key} 元素必须为 int，得到 {x!r}")
    if v[0] < lo or v[1] < lo:
        _err(typename, f"{key} 不得小于 {lo}，得到 {tuple(v)}")
    if strict and v[1] <= v[0]:
        _err(typename, f"{key} 必须满足 end > start（正长度），得到 {tuple(v)}")
    if not strict and v[1] < v[0]:
        _err(typename, f"{key} 必须满足 end >= start，得到 {tuple(v)}")
    return (v[0], v[1])


def _check_bbox(bbox: tuple, where: str) -> None:
    """bbox 必须全部分量有限且 `x1 > x0`、`y1 > y0`（严格非退化）。"""
    for x in bbox:
        if not is_finite(x):
            _err(where, f"bbox 必须为有限实数（不得 NaN/±Inf），得到 {tuple(bbox)!r}")
    x0, y0, x1, y1 = bbox
    if x1 <= x0:
        _err(where, f"bbox 必须满足 x1 > x0（零宽不接受），得到 {tuple(bbox)}")
    if y1 <= y0:
        _err(where, f"bbox 必须满足 y1 > y0（零高不接受），得到 {tuple(bbox)}")


def _check_bbox_in_page(bbox: tuple, width: float, height: float, where: str) -> None:
    """bbox 必须落在所属页面的允许范围内（含 `BBOX_PAGE_TOLERANCE_PT` 容差）。"""
    _check_bbox(bbox, where)
    x0, y0, x1, y1 = bbox
    t = BBOX_PAGE_TOLERANCE_PT
    if x0 < -t or y0 < -t or x1 > width + t or y1 > height + t:
        _err(where, f"bbox 越出页面范围 [0,{width}]x[0,{height}]（容差 {t}pt）："
                    f"{tuple(bbox)}")


def _need_sha256(d: dict, key: str, typename: str) -> str:
    v = _need_str(d, key, typename)
    if not _RE_SHA256_HEX.match(v):
        _err(typename, f"{key} 必须为 64 位小写十六进制 sha256，得到 {v!r}")
    return v


def _need_ref(d: dict, key: str, typename: str, *, none_ok: bool = False):
    v = _need_str(d, key, typename, none_ok=none_ok)
    if v is None:
        return None
    if not _RE_REF.match(v):
        _err(typename, f"{key} 必须形如 <kind>:<id> 且 kind ∈ {REF_KINDS}，得到 {v!r}")
    return v


def _check_version(typename: str, field_name: str, value: Any, expected: str,
                   constant_name: str | None = None) -> str:
    """算法/引擎/规则版本字段必须**恒等于**集中定义的当前版本：不猜、不兼容旧值。

    `constant_name` 给出该字段对应的版本常量名时，已登记的旧值会得到专门的
    "须经显式迁移/重算"错误，而不是笼统的"未知值"。两条路径都 fail-closed。
    """
    if not isinstance(value, str) or value == "":
        _err(typename, f"{field_name} 必须为非空字符串，得到 {value!r}")
    if value != expected:
        if constant_name is not None and V.classify_schema_version(
                constant_name, value) == "legacy":
            _err(typename,
                 f"{field_name}={value!r} 为已退役的旧规则版本，不得静默按当前版本 "
                 f"{expected!r} 解释；必须显式迁移或在当前规则下重算")
        _err(typename, f"{field_name} 必须为当前版本 {expected!r}，得到 {value!r}")
    return value


def _need_schema_version(d: dict, key: str, typename: str, constant_name: str) -> str:
    """wire format 版本字段：区分 `legacy`（旧版，须显式迁移）与 `unknown`（未知）。"""
    expected = V.VERSION_CONSTANTS[constant_name]
    v = d.get(key)
    if not isinstance(v, str) or v == "":
        _err(typename, f"缺必填字段: {key}（当前应为 {expected!r}）")
    kind = V.classify_schema_version(constant_name, v)
    if kind == "legacy":
        _err(typename, f"{key}={v!r} 为旧 wire format 版本，不得静默解释为新版本；"
                       f"必须先经显式迁移（当前版本 {expected!r}）")
    if kind == "unknown":
        _err(typename, f"{key} 为未知版本 {v!r}（当前版本 {expected!r}）")
    return v


# --- TS5 §19.11.2 / §19.15.1：**历史 reader** 的版本校验 -------------------------
#
# `document_structure.schema.TableObject` / `TableRow` / `TableCell` 是 `to-3` 的
# **历史实现**，不是 current。全局 current 升到 `to-4` 之后，它们必须用**类内固定**的
# legacy 常量校验自己的版本字段：
#
# - 若继续读 `V.TABLE_SCHEMA_VERSION`，升版会把历史类自己变成 `to-4` 的**错误解释器**
#   （`to-3` 载荷会被判成"未知/不合法"，`to-4` 载荷会被历史类接受）；
# - 若接受除 `to-3` 之外的任何值，又会把历史类变成"静默兼容新版"的旁路。
#
# 因此下面两个 helper 是这两个语义的**唯一**实现：读回历史载荷时接受且仅接受固定的
# legacy 字面量；同时把"这是被替换掉的旧版本"与"这是打错的字符串"分开报错。


def _check_legacy_version(typename: str, field_name: str, value: Any,
                          expected: str, constant_name: str) -> str:
    """历史 reader 的**算法 / builder 版本**字段：必须恰为类内固定的 legacy 常量。"""
    if not isinstance(value, str) or value == "":
        _err(typename, f"{field_name} 必须为非空字符串，得到 {value!r}")
    if value != expected:
        current = V.VERSION_CONSTANTS.get(constant_name, "<undeclared>")
        _err(typename,
             f"{field_name}={value!r} 不是本历史 reader 固定的历史版本 "
             f"{expected!r}（current 为 {current!r}）；历史对象必须经显式迁移/重建，"
             f"不得按当前版本解释")
    return value


def _need_legacy_schema_version(d: dict, key: str, typename: str,
                                constant_name: str, expected: str) -> str:
    """历史 reader 的 **wire 版本**字段：区分三种情形并分别报错。

    - `expected`（类内固定 legacy 字面量）：接受；
    - 其他**已登记的 legacy / 其他版本轴上的当前值**：报"本类只读历史版本"；
    - 形状非法或未登记：报"未知版本"。
    """
    current = V.VERSION_CONSTANTS.get(constant_name, "<undeclared>")
    v = d.get(key)
    if not isinstance(v, str) or v == "":
        _err(typename, f"缺必填字段: {key}（本历史 reader 只接受 {expected!r}）")
    if v == expected:
        return v
    if V.classify_schema_version(constant_name, v) == "unknown":
        _err(typename, f"{key} 为未知版本 {v!r}"
                       f"（历史 reader 接受 {expected!r}，current 为 {current!r}）")
    _err(typename, f"{key}={v!r} 不是本历史 reader 接受的历史 wire 版本 "
                   f"{expected!r}；{v!r} 必须由当前实现（current {current!r}）显式"
                   f"迁移或重建后读取")
    return v


def _need_alignment_schema_version(d: dict, key: str, typename: str) -> str:
    """对齐终态的 wire 版本：当前值取 `versions.ALIGN_SCHEMA_VERSION`，或**只读兼容**
    的已登记旧版本（`versions.legacy_versions("ALIGN_SCHEMA_VERSION")`）。

    与 `_need_schema_version` 的唯一区别：已登记的旧版本被**显式受理**而不是一律拒绝。
    这不是"放宽旧版解释"，而是把旧版的读取规则写死：旧版载荷是否携带精确分子由
    `versions.ALIGN_EXACT_RATIO_VERSIONS` 判定，缺精确分子的版本其 `verdict` 必须按
    历史语义（量化 coverage）重算（见 `TextAlignmentRecord.__post_init__` 的版本分支）。
    `unknown` 仍然 fail-closed。
    """
    expected = V.ALIGN_SCHEMA_VERSION
    v = d.get(key)
    if not isinstance(v, str) or v == "":
        _err(typename, f"缺必填字段: {key}（当前应为 {expected!r}）")
    kind = V.classify_schema_version("ALIGN_SCHEMA_VERSION", v)
    if kind == "current":
        return v
    if kind == "legacy":
        return v
    _err(typename, f"{key} 为未知版本 {v!r}（当前版本 {expected!r}）")


def _need_ordered_pairs(d: dict, key: str, typename: str) -> tuple[tuple[int, int], ...]:
    """升序、互不重叠的 [start,end) 区间列表。"""
    v = d.get(key)
    if v is None:
        return ()
    if not isinstance(v, list):
        _err(typename, f"{key} 必须为 list，得到 {type(v).__name__}")
    out: list[tuple[int, int]] = []
    prev_end = -1
    for i, item in enumerate(v):
        if not isinstance(item, list) or len(item) != 2:
            _err(typename, f"{key}[{i}] 必须为长度 2 的 int 数组，得到 {item!r}")
        a, b = item
        for x in (a, b):
            if not isinstance(x, int) or isinstance(x, bool):
                _err(typename, f"{key}[{i}] 元素必须为 int，得到 {x!r}")
        if a < 0 or b <= a:
            _err(typename, f"{key}[{i}] 必须满足 0 <= start < end，得到 {(a, b)}")
        if a < prev_end:
            _err(typename, f"{key} 必须升序且不重叠，{i} 处得到 {(a, b)}")
        prev_end = max(prev_end, b)
        out.append((a, b))
    return tuple(out)


def _need_residue(d: dict, key: str, typename: str,
                  block_char_length: int) -> tuple[tuple[int, int, str], ...]:
    """残差段 `(start, end, class)`：每段都**必须**自带分类，否则记录级分类无法重算。"""
    v = d.get(key)
    if v is None:
        return ()
    if not isinstance(v, list):
        _err(typename, f"{key} 必须为 list，得到 {type(v).__name__}")
    out: list[tuple[int, int, str]] = []
    prev_end = -1
    for i, item in enumerate(v):
        if not isinstance(item, list) or len(item) != 3:
            _err(typename, f"{key}[{i}] 必须为 [start, end, residue_class]，得到 {item!r}")
        a, b, cls = item
        for x in (a, b):
            if not isinstance(x, int) or isinstance(x, bool):
                _err(typename, f"{key}[{i}] 的区间元素必须为 int，得到 {x!r}")
        if a < 0 or b <= a:
            _err(typename, f"{key}[{i}] 必须满足 0 <= start < end，得到 {(a, b)}")
        if b > block_char_length:
            _err(typename, f"{key}[{i}] 越出块长度 {block_char_length}")
        if cls not in RESIDUE_CLASS_SEVERITY:
            _err(typename, f"{key}[{i}] 的分类必须属于 {RESIDUE_CLASS_SEVERITY}，"
                           f"得到 {cls!r}")
        if a < prev_end:
            _err(typename, f"{key} 必须升序且不重叠，{i} 处得到 {(a, b)}")
        prev_end = max(prev_end, b)
        out.append((a, b, cls))
    return tuple(out)


def _need_char_map(d: dict, key: str, typename: str, block_char_length: int,
                   page_number: int) -> tuple[tuple[int, ...], ...]:
    """`[block_char_start, block_char_end, page, line, span, char_offset]` 逐段映射。"""
    v = d.get(key)
    if v is None:
        return ()
    if not isinstance(v, list):
        _err(typename, f"{key} 必须为 list，得到 {type(v).__name__}")
    out: list[tuple[int, ...]] = []
    prev_end = -1
    for i, item in enumerate(v):
        if not isinstance(item, list) or len(item) != 6:
            _err(typename, f"{key}[{i}] 必须为长度 6 的 int 数组，得到 {item!r}")
        for x in item:
            if not isinstance(x, int) or isinstance(x, bool):
                _err(typename, f"{key}[{i}] 元素必须为 int，得到 {x!r}")
        if item[0] < 0 or item[1] <= item[0]:
            _err(typename, f"{key}[{i}] 必须满足 0 <= start < end，得到 {tuple(item)}")
        if item[1] > block_char_length:
            _err(typename, f"{key}[{i}] 越出块长度 {block_char_length}")
        if item[0] < prev_end:
            _err(typename, f"{key} 必须升序且不重叠，{i} 处得到 {tuple(item)}")
        if item[2] != page_number:
            _err(typename, f"{key}[{i}] 的页码必须等于记录页码 {page_number}")
        if item[3] < 0 or item[4] < 0 or item[5] < 0:
            _err(typename, f"{key}[{i}] 的 line/span/char_offset 不得为负: {tuple(item)}")
        prev_end = max(prev_end, item[1])
        out.append(tuple(item))
    return tuple(out)


def _need_line_refs(d: dict, key: str, typename: str) -> tuple[tuple[int, int], ...]:
    v = d.get(key)
    if v is None:
        return ()
    if not isinstance(v, list):
        _err(typename, f"{key} 必须为 list")
    out: list[tuple[int, int]] = []
    for i, item in enumerate(v):
        if not isinstance(item, list) or len(item) != 2:
            _err(typename, f"{key}[{i}] 必须为 [page_number, line_index]")
        for x in item:
            if not isinstance(x, int) or isinstance(x, bool):
                _err(typename, f"{key}[{i}] 元素必须为 int")
        out.append((item[0], item[1]))
    return tuple(out)


def _need_evidence_refs(d: dict, key: str, typename: str) -> tuple[tuple[str, int, int], ...]:
    v = d.get(key)
    if v is None:
        return ()
    if not isinstance(v, list):
        _err(typename, f"{key} 必须为 list")
    out: list[tuple[str, int, int]] = []
    for i, item in enumerate(v):
        if not isinstance(item, list) or len(item) != 3:
            _err(typename, f"{key}[{i}] 必须为 [evidence_id, char_start, char_end]")
        eid, cs, ce = item
        if not isinstance(eid, str) or eid == "":
            _err(typename, f"{key}[{i}] 的 evidence_id 必须为非空字符串")
        for x in (cs, ce):
            if not isinstance(x, int) or isinstance(x, bool):
                _err(typename, f"{key}[{i}] 的字符偏移必须为 int")
        if cs < 0 or ce <= cs:
            _err(typename, f"{key}[{i}] 必须满足 0 <= start < end，得到 {(cs, ce)}")
        out.append((eid, cs, ce))
    return tuple(out)


def _json(v: Any) -> Any:
    """对象图 → JSON 安全值（tuple → list；嵌套对象交给其 to_dict）。"""
    return to_json_value(v)


# ---------------------------------------------------------------------------
# 1. 版式类型：LayoutSpan / LayoutLine / LayoutPage / PageLayout
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LayoutSpan:
    """排版片段（**不是** `OutlineSpan`）：一行内同字体属性的连续文本。

    寻址：`(page_layout_id, page_number, line_index, span_index)`；无独立 id。
    `char_start` / `char_end` 是**行内**字符偏移：`text` 必须**逐字符等于**
    行文本在该区间的切片（P1-2），因此"文本被改写"在构造期直接失败。
    """

    text: str
    bbox: tuple[float, float, float, float]
    font: str
    size: float
    is_bold: bool
    char_start: int
    char_end: int

    def __post_init__(self) -> None:
        t = "LayoutSpan"
        if not isinstance(self.text, str) or self.text == "":
            _err(t, "text 必须为非空字符串")
        if not isinstance(self.font, str) or self.font == "":
            _err(t, "font 必须为非空字符串")
        if not isinstance(self.is_bold, bool):
            _err(t, "is_bold 必须为 bool")
        object.__setattr__(self, "bbox", tuple(quantize(x) for x in self.bbox))
        if not is_finite(self.size):
            _err(t, f"size 必须为有限实数（不得 NaN/±Inf），得到 {self.size!r}")
        object.__setattr__(self, "size", quantize(self.size))
        _check_bbox(self.bbox, f"{t}.bbox")
        if self.size <= 0:
            _err(t, f"size 必须 > 0，得到 {self.size}")
        if not isinstance(self.char_start, int) or isinstance(self.char_start, bool):
            _err(t, "char_start 必须为 int")
        if not isinstance(self.char_end, int) or isinstance(self.char_end, bool):
            _err(t, "char_end 必须为 int")
        if self.char_start < 0:
            _err(t, f"char_start 不得小于 0，得到 {self.char_start}")
        if self.char_end <= self.char_start:
            _err(t, f"char_end 必须大于 char_start（零长度区间不接受），"
                    f"得到 {(self.char_start, self.char_end)}")
        if self.char_end - self.char_start != len(self.text):
            _err(t, "char_end - char_start 必须等于 text 长度"
                    f"（{(self.char_start, self.char_end)} vs {len(self.text)}）")

    def to_dict(self) -> dict:
        return {
            "schema_type": "LayoutSpan",
            "text": self.text,
            "bbox": _json(self.bbox),
            "font": self.font,
            "size": self.size,
            "is_bold": self.is_bold,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "LayoutSpan":
        t = "LayoutSpan"
        d = _reject_unknown(d, {
            "schema_type", "text", "bbox", "font", "size", "is_bold",
            "char_start", "char_end"}, t)
        _need_enum(d, "schema_type", t, ("LayoutSpan",))
        return cls(
            text=_need_str(d, "text", t),
            bbox=_need_bbox(d, "bbox", t),
            font=_need_str(d, "font", t),
            size=_need_num(d, "size", t, lo=0.0),
            is_bold=_need_bool(d, "is_bold", t),
            char_start=_need_int(d, "char_start", t, lo=0),
            char_end=_need_int(d, "char_end", t, lo=1),
        )


@dataclass(frozen=True)
class LayoutLine:
    """页面内一行（含 bbox、顺序、栏号、家具标记与其排版片段）。

    寻址：`(page_layout_id, page_number, line_index)`。

    行内文本自洽（P1-2）：spans 严格升序、互不重叠、不越界，每个 span 的 `text`
    必须逐字符等于 `line.text[char_start:char_end]`，相邻 spans 之间的**空隙只允许
    空白**。因此"行文本与 span 文本不一致""span 越界/重叠/逆序"在构造期失败。
    """

    line_index: int
    bbox: tuple[float, float, float, float]
    spans: tuple[LayoutSpan, ...]
    text: str
    is_furniture: bool
    furniture_kind: str | None
    reading_order: int
    column_index: int

    def __post_init__(self) -> None:
        t = "LayoutLine"
        if not isinstance(self.line_index, int) or isinstance(self.line_index, bool):
            _err(t, "line_index 必须为 int")
        if self.line_index < 0:
            _err(t, f"line_index 不得小于 0，得到 {self.line_index}")
        object.__setattr__(self, "bbox", tuple(quantize(x) for x in self.bbox))
        _check_bbox(self.bbox, f"{t}.bbox")
        if not isinstance(self.text, str) or self.text == "":
            _err(t, "text 必须为非空字符串")
        if "\n" in self.text:
            _err(t, "text 不得包含换行（行是最小单位）")
        if not isinstance(self.is_furniture, bool):
            _err(t, "is_furniture 必须为 bool")
        if self.is_furniture and self.furniture_kind is None:
            _err(t, "is_furniture=True 时 furniture_kind 不得为空")
        if not self.is_furniture and self.furniture_kind is not None:
            _err(t, "is_furniture=False 时 furniture_kind 必须为空")
        if self.furniture_kind is not None and self.furniture_kind not in FURNITURE_KINDS:
            _err(t, f"furniture_kind 必须属于 {FURNITURE_KINDS}，得到 {self.furniture_kind!r}")
        for name in ("reading_order", "column_index"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                _err(t, f"{name} 必须为非负 int，得到 {v!r}")
        if not isinstance(self.spans, tuple) or not self.spans:
            _err(t, "spans 必须为非空元组")
        prev_end = 0
        for i, sp in enumerate(self.spans):
            if not isinstance(sp, LayoutSpan):
                _err(t, f"spans[{i}] 必须为 LayoutSpan，得到 {type(sp).__name__}")
            if sp.char_end > len(self.text):
                _err(t, f"spans[{i}] 越出行的字符范围"
                        f"（{sp.char_end} > {len(self.text)}）")
            if sp.char_start < prev_end:
                _err(t, f"spans 必须升序且不重叠，{i} 处得到 "
                        f"{(sp.char_start, sp.char_end)}")
            if sp.text != self.text[sp.char_start:sp.char_end]:
                _err(t, f"spans[{i}].text 必须逐字符等于 line.text"
                        f"[{sp.char_start}:{sp.char_end}]"
                        f"（{sp.text!r} vs "
                        f"{self.text[sp.char_start:sp.char_end]!r}）")
            gap = self.text[prev_end:sp.char_start]
            if gap.strip() != "":
                _err(t, f"spans 之间的空隙只允许空白，{i} 处空隙为 {gap!r}")
            prev_end = sp.char_end

    def to_dict(self) -> dict:
        return {
            "schema_type": "LayoutLine",
            "line_index": self.line_index,
            "bbox": _json(self.bbox),
            "spans": [_json(s) for s in self.spans],
            "text": self.text,
            "is_furniture": self.is_furniture,
            "furniture_kind": self.furniture_kind,
            "reading_order": self.reading_order,
            "column_index": self.column_index,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "LayoutLine":
        t = "LayoutLine"
        d = _reject_unknown(d, {
            "schema_type", "line_index", "bbox", "spans", "text", "is_furniture",
            "furniture_kind", "reading_order", "column_index"}, t)
        _need_enum(d, "schema_type", t, ("LayoutLine",))
        return cls(
            line_index=_need_int(d, "line_index", t, lo=0),
            bbox=_need_bbox(d, "bbox", t),
            spans=_need_children(d, "spans", t, LayoutSpan.from_dict),
            text=_need_str(d, "text", t),
            is_furniture=_need_bool(d, "is_furniture", t),
            furniture_kind=_need_str(d, "furniture_kind", t, none_ok=True),
            reading_order=_need_int(d, "reading_order", t, lo=0),
            column_index=_need_int(d, "column_index", t, lo=0),
        )


@dataclass(frozen=True)
class LayoutPage:
    """一页的 canonical layout（尺寸、旋转、阅读顺序、家具行、是否有文本层）。

    寻址：`(page_layout_id, page_number)`。无文本层的页面**必须**没有行
    （扫描件不得伪造文本行）。页内所有 line / span 的 bbox 必须落在页面范围内。
    """

    page_number: int
    width: float
    height: float
    rotation: int
    lines: tuple[LayoutLine, ...]
    has_text_layer: bool

    def __post_init__(self) -> None:
        t = "LayoutPage"
        if not isinstance(self.page_number, int) or isinstance(self.page_number, bool):
            _err(t, "page_number 必须为 int")
        if self.page_number < 1:
            _err(t, f"page_number 必须从 1 开始，得到 {self.page_number}")
        for name in ("width", "height"):
            v = getattr(self, name)
            if not is_finite(v):
                _err(t, f"{name} 必须为有限实数（不得 NaN/±Inf），得到 {v!r}")
            object.__setattr__(self, name, quantize(v))
        for name in ("width", "height"):
            v = getattr(self, name)
            if v <= 0:
                _err(t, f"{name} 必须 > 0（零宽/零高不接受），得到 {v}")
        if self.rotation not in ROTATIONS:
            _err(t, f"rotation 必须属于 {ROTATIONS}，得到 {self.rotation!r}")
        if not isinstance(self.has_text_layer, bool):
            _err(t, "has_text_layer 必须为 bool")
        if not isinstance(self.lines, tuple):
            _err(t, "lines 必须为元组")
        if not self.has_text_layer and self.lines:
            _err(t, "has_text_layer=False 时不得存在文本行（不得伪造）")
        prev_order = -1
        for i, ln in enumerate(self.lines):
            if not isinstance(ln, LayoutLine):
                _err(t, f"lines[{i}] 必须为 LayoutLine，得到 {type(ln).__name__}")
            if ln.line_index != i:
                _err(t, f"lines[{i}].line_index 必须等于其页内位置 {i}，得到 {ln.line_index}")
            if ln.reading_order <= prev_order:
                _err(t, f"lines 的 reading_order 必须严格递增，{i} 处得到 {ln.reading_order}")
            prev_order = ln.reading_order
            _check_bbox_in_page(ln.bbox, self.width, self.height,
                                f"{t}.lines[{i}].bbox")
            for j, sp in enumerate(ln.spans):
                _check_bbox_in_page(sp.bbox, self.width, self.height,
                                    f"{t}.lines[{i}].spans[{j}].bbox")

    def to_dict(self) -> dict:
        return {
            "schema_type": "LayoutPage",
            "page_number": self.page_number,
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
            "lines": [_json(x) for x in self.lines],
            "has_text_layer": self.has_text_layer,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "LayoutPage":
        t = "LayoutPage"
        d = _reject_unknown(d, {
            "schema_type", "page_number", "width", "height", "rotation", "lines",
            "has_text_layer"}, t)
        _need_enum(d, "schema_type", t, ("LayoutPage",))
        return cls(
            page_number=_need_int(d, "page_number", t, lo=1),
            width=_need_num(d, "width", t, lo=0.0),
            height=_need_num(d, "height", t, lo=0.0),
            rotation=_need_int(d, "rotation", t, lo=0),
            lines=_need_children(d, "lines", t, LayoutLine.from_dict),
            has_text_layer=_need_bool(d, "has_text_layer", t),
        )


@dataclass(frozen=True)
class PageLayout:
    """一份电子 PDF 的版本化只读版式层（构建于 TS2）。

    - **稳定定位身份** `page_layout_locator`：
      `loc-pl-<h(company_id, document_id, document_version, engine, engine_version,
      LAYOUT_SCHEMA_VERSION, NORMALIZATION_VERSION)>` —— "哪家公司的哪份文档、
      用哪个引擎版本产出的版式层"的指针，与"谁在何时跑"无关
      （不含 run_id / timestamp / 日志路径 / 页面内容），内容修订后不变。
    - **不可变 revision 身份** `page_layout_id`：在 locator 之上再绑定
      `source_file_sha256`、`page_count` 与**全部页面内容**，因此"换了源文件哈希、
      改了任何一页"都必须是另一个 id。

    身份范围（TS1.1 P1-A）：`company_id` 与 `document_id` **必须**进入 locator。
    "公司无关"指的是**算法与规则**不得因公司而异，不是指不同公司的存储身份必须
    相等：上一轮把 `company_id` / `document_id` 排除在 locator 之外，导致不同公司、
    不同文档的 PageLayout 共用同一个 locator 与同一个 id，下游无法区分。如果将来
    确实需要把"同一 PDF 的纯物理版式"跨公司复用，那属于**独立的注册 / 绑定层**
    （可变元数据不得与不可变 id 混在一起），不属于本类型。

    另强制（P1-1.5）：`document_version` 必须等于现行文档身份规则
    `"sha256-" + source_file_sha256[:16]`（与 `evidence/ids.py:32` 一致），
    不允许"替换源文件哈希却仍构造同一有效布局"。
    """

    page_layout_locator: str
    page_layout_id: str
    document_id: str
    document_version: str
    company_id: str
    engine: str
    engine_version: str
    normalization_version: str
    schema_version: str
    source_file_sha256: str
    page_count: int
    pages: tuple[LayoutPage, ...]

    def __post_init__(self) -> None:
        t = "PageLayout"
        for name in ("page_layout_locator", "page_layout_id", "document_id",
                     "document_version", "company_id", "engine", "engine_version"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串，得到 {v!r}")
        _check_version(t, "schema_version", self.schema_version, V.LAYOUT_SCHEMA_VERSION)
        _check_version(t, "normalization_version", self.normalization_version,
                       V.NORMALIZATION_VERSION)
        if not _RE_SHA256_HEX.match(self.source_file_sha256):
            _err(t, "source_file_sha256 必须为 64 位小写十六进制 sha256")
        expected_dv = "sha256-" + self.source_file_sha256[:16]
        if self.document_version != expected_dv:
            _err(t, "document_version 必须等于现行文档身份规则 "
                    f"'sha256-' + source_file_sha256[:16]（{self.document_version!r} vs "
                    f"{expected_dv!r}）；不得替换源文件哈希而仍构造同一布局")
        if not isinstance(self.page_count, int) or isinstance(self.page_count, bool):
            _err(t, "page_count 必须为 int")
        if self.page_count < 1:
            _err(t, f"page_count 必须 >= 1，得到 {self.page_count}")
        if not isinstance(self.pages, tuple):
            _err(t, "pages 必须为元组")
        if len(self.pages) != self.page_count:
            _err(t, f"len(pages) 必须等于 page_count（{len(self.pages)} vs {self.page_count}）")
        for i, pg in enumerate(self.pages):
            if not isinstance(pg, LayoutPage):
                _err(t, f"pages[{i}] 必须为 LayoutPage，得到 {type(pg).__name__}")
            if pg.page_number != i + 1:
                _err(t, f"页序必须从 1 开始连续递增，pages[{i}].page_number = {pg.page_number}")

        expected_loc = derive_page_layout_locator(
            company_id=self.company_id, document_id=self.document_id,
            document_version=self.document_version, engine=self.engine,
            engine_version=self.engine_version, schema_version=self.schema_version,
            normalization_version=self.normalization_version)
        if self.page_layout_locator != expected_loc:
            _err(t, "page_layout_locator 与派生定位身份不一致："
                    f"{self.page_layout_locator!r} != {expected_loc!r}")
        expected_id = derive_page_layout_id(
            page_layout_locator=self.page_layout_locator,
            source_file_sha256=self.source_file_sha256, pages=self.pages)
        if self.page_layout_id != expected_id:
            _err(t, "page_layout_id 与派生 revision 身份不一致（内容或版本已变）："
                    f"{self.page_layout_id!r} != {expected_id!r}")

    def line_at(self, page_number: int, line_index: int) -> "LayoutLine | None":
        """按 `(page_number, line_index)` 取**真实** `LayoutLine`；不存在返回 `None`。

        TS1.3 §三.1：引用 occurrence 的坐标必须精确指向一条真实行，因此这是唯一的
        按坐标取行入口。核验路径**只能**通过本方法取行——回归测试依赖这一点：
        用"页码/行号字符串清单"冒充真实版式无法通过（见 `_occurrence_layout_line`）。
        """
        if not isinstance(page_number, int) or isinstance(page_number, bool):
            return None
        if not isinstance(line_index, int) or isinstance(line_index, bool):
            return None
        for pg in self.pages:
            if pg.page_number == page_number:
                if 0 <= line_index < len(pg.lines):
                    return pg.lines[line_index]
                return None
        return None

    def furniture_lines(self, furniture_kind: str) -> tuple[tuple[int, "LayoutLine"], ...]:
        """全部页上属于 `furniture_kind` 的版式行，返回 `(page_number, line)` 序列。

        TS1.3 §四.5/§四.6：目录页标签 → PDF 物理页码的映射**只能**由真实版式行推出。
        `page_number` 类页眉/页脚是版式层**已经记录**的物理页标记，因此这条映射是
        "重算"而不是"猜"：不需要固定偏移、不需要最近页匹配，也不会随公司变化。
        """
        if furniture_kind not in FURNITURE_KINDS:
            _err("PageLayout", f"未知 furniture_kind {furniture_kind!r}")
        out: list[tuple[int, LayoutLine]] = []
        for pg in self.pages:
            for line in pg.lines:
                if line.is_furniture and line.furniture_kind == furniture_kind:
                    out.append((pg.page_number, line))
        return tuple(out)

    def to_dict(self) -> dict:
        return {
            "schema_type": "PageLayout",
            "page_layout_locator": self.page_layout_locator,
            "page_layout_id": self.page_layout_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "company_id": self.company_id,
            "engine": self.engine,
            "engine_version": self.engine_version,
            "normalization_version": self.normalization_version,
            "schema_version": self.schema_version,
            "source_file_sha256": self.source_file_sha256,
            "page_count": self.page_count,
            "pages": [_json(x) for x in self.pages],
        }

    @classmethod
    def from_dict(cls, d: Any) -> "PageLayout":
        t = "PageLayout"
        d = _reject_unknown(d, {
            "schema_type", "page_layout_locator", "page_layout_id", "document_id",
            "document_version", "company_id", "engine", "engine_version",
            "normalization_version", "schema_version", "source_file_sha256",
            "page_count", "pages"}, t)
        _need_enum(d, "schema_type", t, ("PageLayout",))
        return cls(
            page_layout_locator=_need_str(d, "page_layout_locator", t),
            page_layout_id=_need_str(d, "page_layout_id", t),
            document_id=_need_str(d, "document_id", t),
            document_version=_need_str(d, "document_version", t),
            company_id=_need_str(d, "company_id", t),
            engine=_need_str(d, "engine", t),
            engine_version=_need_str(d, "engine_version", t),
            normalization_version=_need_str(d, "normalization_version", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                "LAYOUT_SCHEMA_VERSION"),
            source_file_sha256=_need_sha256(d, "source_file_sha256", t),
            page_count=_need_int(d, "page_count", t, lo=1),
            pages=_need_children(d, "pages", t, LayoutPage.from_dict),
        )

    @classmethod
    def create(cls, *, document_id: str, document_version: str, company_id: str,
               source_file_sha256: str, pages: tuple[LayoutPage, ...],
               engine: str = V.LAYOUT_ENGINE,
               engine_version: str = V.LAYOUT_ENGINE_VERSION) -> "PageLayout":
        """按派生身份构造（调用方不自行填写 locator / id）。"""
        loc = derive_page_layout_locator(
            company_id=company_id, document_id=document_id,
            document_version=document_version, engine=engine,
            engine_version=engine_version, schema_version=V.LAYOUT_SCHEMA_VERSION,
            normalization_version=V.NORMALIZATION_VERSION)
        return cls(
            page_layout_locator=loc,
            page_layout_id=derive_page_layout_id(
                page_layout_locator=loc, source_file_sha256=source_file_sha256,
                pages=pages),
            document_id=document_id, document_version=document_version,
            company_id=company_id, engine=engine, engine_version=engine_version,
            normalization_version=V.NORMALIZATION_VERSION,
            schema_version=V.LAYOUT_SCHEMA_VERSION,
            source_file_sha256=source_file_sha256, page_count=len(pages), pages=pages,
        )


def derive_page_layout_locator(*, company_id: str, document_id: str,
                               document_version: str, engine: str,
                               engine_version: str, schema_version: str,
                               normalization_version: str) -> str:
    """`loc-pl-<sha256[:16]>`：稳定定位。

    输入必须覆盖：`company_id`、`document_id`、`document_version`、引擎与引擎版本、
    layout schema 版本、归一化版本。**不含**页面内容（内容修订后 locator 不变）、
    也不含 run_id / timestamp / 日志路径。

    `company_id` / `document_id` 进入定位身份是刻意的：公司无关指的是**算法与规则**
    不得因公司而异，而不是"不同公司的存储身份必须相等"。
    """
    return locator("pl", {
        "company_id": company_id,
        "document_id": document_id,
        "document_version": document_version,
        "engine": engine,
        "engine_version": engine_version,
        "schema_version": schema_version,
        "normalization_version": normalization_version,
    })


def derive_page_layout_id(*, page_layout_locator: str, source_file_sha256: str,
                          pages: tuple) -> str:
    """`pl-<sha256[:16]>`：不可变 revision 身份（locator + 全文件哈希 + 全部页面内容）。"""
    return identity("pl", {
        "page_layout_locator": page_layout_locator,
        "source_file_sha256": source_file_sha256,
        "pages": [_json(p) for p in pages],
    })


# ---------------------------------------------------------------------------
# 2. 标题树：OutlineNode / DocumentOutline
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OutlineNode:
    """标题树节点。同名标题**必须**以完整路径 + 源位置区分。

    身份：`on-<sha256[:16]>(document_outline_locator, structural_path,
    source_anchor, title)`。`structural_path` 含全部祖先的规范化标题、
    `source_anchor` 含 `(page_number, line_index, bbox)`，因此节点的**全部语义内容**
    都在身份里：标题改写、位置移动、层级变化都必然换 id。
    `ordinal` / `child_ids` / `parent_id` / `level` 是**派生记账字段**，
    由 `DocumentOutline` 对整棵树逐项核验（同级序号、子表对称、层级连续），
    因此不需要再进 id；`parent_id` 使用父节点的 `node_id`，全树无环。
    """

    node_id: str
    document_outline_locator: str
    parent_id: str | None
    level: int
    title: str
    title_normalized: str
    structural_path: tuple[str, ...]
    source_anchor: tuple[int, int, tuple]
    child_ids: tuple[str, ...]
    ordinal: int

    def __post_init__(self) -> None:
        t = "OutlineNode"
        for name in ("node_id", "document_outline_locator"):
            if not isinstance(getattr(self, name), str) or getattr(self, name) == "":
                _err(t, f"{name} 必须为非空字符串")
        if self.parent_id is not None and (not isinstance(self.parent_id, str)
                                           or not self.parent_id):
            _err(t, "parent_id 必须为非空字符串或 None")
        if not isinstance(self.title, str) or self.title == "":
            _err(t, "title 必须为非空字符串")
        if len(self.title) > 60:
            _err(t, f"title 不得超过 60 字符，得到 {len(self.title)}")
        if not isinstance(self.title_normalized, str) or self.title_normalized == "":
            _err(t, "title_normalized 必须为非空字符串")
        for hint in _WORKSPACE_FORBIDDEN_HINTS:
            if hint in self.title_normalized:
                _err(t, f"title_normalized 不得含 {hint!r}（规范化文本的形状要求；"
                        f"完整归一化规则由 TS2 的 NORMALIZATION_VERSION 实施）")
        if not isinstance(self.structural_path, tuple) or not self.structural_path:
            _err(t, "structural_path 必须为非空元组")
        for i, seg in enumerate(self.structural_path):
            if not isinstance(seg, str) or seg == "":
                _err(t, f"structural_path[{i}] 必须为非空字符串")
        if self.structural_path[-1] != self.title_normalized:
            _err(t, "structural_path[-1] 必须等于 title_normalized")
        if not isinstance(self.level, int) or isinstance(self.level, bool):
            _err(t, "level 必须为 int")
        if self.level != len(self.structural_path) - 1:
            _err(t, f"level 必须等于 len(structural_path)-1（{self.level} vs "
                    f"{len(self.structural_path) - 1}）")
        if self.level < 0:
            _err(t, f"level 不得小于 0，得到 {self.level}")
        if (self.parent_id is None) != (self.level == 0):
            _err(t, "父节点与层级必须一致：parent_id=None 当且仅当 level==0")
        if not isinstance(self.child_ids, tuple):
            _err(t, "child_ids 必须为元组")
        for i, cid in enumerate(self.child_ids):
            if not isinstance(cid, str) or cid == "":
                _err(t, f"child_ids[{i}] 必须为非空字符串")
        if len(set(self.child_ids)) != len(self.child_ids):
            _err(t, "child_ids 不得重复")
        if self.node_id in self.child_ids:
            _err(t, "child_ids 不得包含自身")
        if not isinstance(self.ordinal, int) or isinstance(self.ordinal, bool) \
                or self.ordinal < 0:
            _err(t, f"ordinal 必须为非负 int，得到 {self.ordinal!r}")
        if not isinstance(self.source_anchor, tuple) or len(self.source_anchor) != 3:
            _err(t, "source_anchor 必须为 (page_number, line_index, bbox)")
        if any(not isinstance(x, int) or isinstance(x, bool)
               for x in self.source_anchor[:2]):
            _err(t, f"source_anchor 的页码/行号必须为 int，得到 {self.source_anchor[:2]!r}")
        if self.source_anchor[0] < 1:
            _err(t, f"source_anchor 的页码必须从 1 开始，得到 {self.source_anchor[0]}")
        if self.source_anchor[1] < 0:
            _err(t, f"source_anchor 的行号不得为负，得到 {self.source_anchor[1]}")
        _check_bbox(self.source_anchor[2], f"{t}.source_anchor")
        expected = derive_node_id(
            document_outline_locator=self.document_outline_locator,
            structural_path=self.structural_path, source_anchor=self.source_anchor,
            title=self.title)
        if self.node_id != expected:
            _err(t, f"node_id 与派生身份不一致：{self.node_id!r} != {expected!r}")

    def to_dict(self) -> dict:
        return {
            "schema_type": "OutlineNode",
            "node_id": self.node_id,
            "document_outline_locator": self.document_outline_locator,
            "parent_id": self.parent_id,
            "level": self.level,
            "title": self.title,
            "title_normalized": self.title_normalized,
            "structural_path": _json(self.structural_path),
            "source_anchor": _json(self.source_anchor),
            "child_ids": _json(self.child_ids),
            "ordinal": self.ordinal,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "OutlineNode":
        t = "OutlineNode"
        d = _reject_unknown(d, {
            "schema_type", "node_id", "document_outline_locator", "parent_id", "level",
            "title", "title_normalized", "structural_path", "source_anchor",
            "child_ids", "ordinal"}, t)
        _need_enum(d, "schema_type", t, ("OutlineNode",))
        return cls(
            node_id=_need_str(d, "node_id", t),
            document_outline_locator=_need_str(d, "document_outline_locator", t),
            parent_id=_need_str(d, "parent_id", t, none_ok=True),
            level=_need_int(d, "level", t, lo=0),
            title=_need_str(d, "title", t),
            title_normalized=_need_str(d, "title_normalized", t),
            structural_path=_need_str_tuple(d, "structural_path", t),
            source_anchor=_need_anchor(d, "source_anchor", t),
            child_ids=_need_str_tuple(d, "child_ids", t),
            ordinal=_need_int(d, "ordinal", t, lo=0),
        )

    @classmethod
    def create(cls, *, document_outline_locator: str, parent_id: str | None, title: str,
               title_normalized: str, structural_path: tuple[str, ...],
               source_anchor: tuple[int, int, tuple],
               child_ids: tuple[str, ...] = (), ordinal: int = 0) -> "OutlineNode":
        return cls(
            node_id=derive_node_id(document_outline_locator=document_outline_locator,
                                   structural_path=structural_path,
                                   source_anchor=source_anchor, title=title),
            document_outline_locator=document_outline_locator, parent_id=parent_id,
            level=len(structural_path) - 1, title=title,
            title_normalized=title_normalized, structural_path=structural_path,
            source_anchor=source_anchor, child_ids=child_ids, ordinal=ordinal,
        )


def derive_node_id(*, document_outline_locator: str, structural_path: tuple[str, ...],
                   source_anchor: tuple, title: str) -> str:
    """`on-<sha256[:16]>`：同名标题由完整路径 + 源位置区分；标题改写即换 id。"""
    return identity("on", {
        "document_outline_locator": document_outline_locator,
        "structural_path": list(structural_path),
        "source_anchor": _json(source_anchor),
        "title": title,
    })


@dataclass(frozen=True)
class DocumentOutline:
    """只读标题树（构建于 TS3）：节点、引用边、显式 `unassigned` span、候选来源。

    - **稳定定位身份** `outline_locator`：`loc-do-<h(page_layout_id, document_id,
      OUTLINE_ALGORITHM_VERSION, OUTLINE_SCHEMA_VERSION)>`。
    - **不可变 revision 身份** `outline_id` == `do-<content_fingerprint[:16]>`：
      文档身份、节点、边、未归属 span、候选来源中的任何语义变化都换 id。

    **引用核验上下文不进入内容身份（TS1.2 P1-1）**：本 outline 的 JSON /
    `content_fingerprint` / `outline_id` 只由**文档内容**决定 —— 布局、节点、边、
    未归属 span、候选来源。调用方为核验引用而交进来的真实对象（
    `ReferenceValidationContext`）是**运行时依赖**，不是内容：同一份内容在不同但
    等价的核验上下文下必须得到逐字节相同的 JSON 与相同身份。上一轮把目标字符串
    清单持久化进 outline，导致"仅增加一个没有任何边使用的目标"就换 id。

    身份范围（TS1.1 P1-A）：`document_id` 显式进入 locator，`company_id` 经
    `page_layout_id` 间接绑定（`page_layout_id` 的 locator 已覆盖公司）。这样一个
    outline 不可能"换一家公司的同名文档而身份不变"。`document_version` 也必须与上游
    page_layout 一致 —— 由 `verify_upstream(layout=...)` 做**对象级**核对，
    而不是只检查非空字符串。

    构造期强制（结构层）：节点源位置按文档顺序**严格递增**；子节点 `structural_path`
    必须继承父节点路径；父引用必须存在、层级连续、全图无环；`parent_child` 必须与
    真实父子关系及 `child_ids` 对称性一致；未解析边必须给出稳定 `reason_code` 与原始
    声明目标（`occurrence.declared_target`），且不得编造目标 id。

    **「结构合法」与「引用已验证」是两件事（TS1.2 §三）**：`is_resolved=True` 只是
    边**自称**已解析（见 `ReferenceEdge` 真值表）。真正的"已验证"必须由
    `verify_references(context)` 在**真实对象**上核验并返回 `VerifiedReferences`。
    为了让本对象不可能持有无法证明的已解析边，正式入口 `create` / `from_dict`
    强制：**已解析边必须要么能在本 outline 自持对象集合内证明（`node:` → `nodes`，
    `span:` → `unassigned`，`parent_child` 的父子关系），要么调用方必须提供
    `reference_context` 供对象级核验**；二者都不满足即拒绝构造（fail-closed）。

    边界说明（不得含糊）：`ReferenceValidationContext` 只是"把真实对象交进来"的
    容器，**Python 类型或类名本身不证明任何外部对象真实存在**；本层能证明的只有
    (a) 本 outline 自持的对象，(b) 上下文里被逐个核对了稳定定位、修订身份与
    文档 / 布局 / outline 归属之后的对象。
    """

    outline_locator: str
    outline_id: str
    document_id: str
    document_version: str
    page_layout_id: str
    algorithm_version: str
    schema_version: str
    nodes: tuple[OutlineNode, ...]
    edges: tuple["ReferenceEdge", ...]
    unassigned: tuple["OutlineSpan", ...]
    candidate_sources: tuple[str, ...]
    content_fingerprint: str

    def __post_init__(self) -> None:
        t = "DocumentOutline"
        for name in ("outline_locator", "outline_id", "document_id",
                     "document_version", "page_layout_id"):
            if not isinstance(getattr(self, name), str) or getattr(self, name) == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_version(t, "algorithm_version", self.algorithm_version,
                       V.OUTLINE_ALGORITHM_VERSION)
        _check_version(t, "schema_version", self.schema_version,
                       V.OUTLINE_SCHEMA_VERSION)
        for name in ("nodes", "edges", "unassigned", "candidate_sources"):
            if not isinstance(getattr(self, name), tuple):
                _err(t, f"{name} 必须为元组")
        for i, s in enumerate(self.candidate_sources):
            if s not in CANDIDATE_SOURCES:
                _err(t, f"candidate_sources[{i}] 必须属于 {CANDIDATE_SOURCES}，得到 {s!r}")
        if len(set(self.candidate_sources)) != len(self.candidate_sources):
            _err(t, "candidate_sources 不得重复")

        # 节点：唯一 id、属于本 outline、父链无环、父存在且层级连续。
        by_id: dict[str, OutlineNode] = {}
        for i, n in enumerate(self.nodes):
            if not isinstance(n, OutlineNode):
                _err(t, f"nodes[{i}] 必须为 OutlineNode，得到 {type(n).__name__}")
            if n.document_outline_locator != self.outline_locator:
                _err(t, f"nodes[{i}].document_outline_locator 与 outline_locator 不一致")
            if n.node_id in by_id:
                _err(t, f"node_id 必须全局唯一，重复: {n.node_id}")
            by_id[n.node_id] = n
        for i, n in enumerate(self.nodes):
            if n.parent_id is not None:
                parent = by_id.get(n.parent_id)
                if parent is None:
                    _err(t, f"nodes[{i}].parent_id={n.parent_id!r} 不存在于 nodes")
                if parent.level != n.level - 1:
                    _err(t, f"nodes[{i}] 的层级必须比父节点深 1")
                if tuple(n.structural_path[:-1]) != tuple(parent.structural_path):
                    _err(t, f"nodes[{i}].structural_path 必须继承父节点路径"
                            f"（{n.structural_path[:-1]!r} vs "
                            f"{tuple(parent.structural_path)!r}）")
            seen = {n.node_id}
            cur = n
            steps = 0
            while cur.parent_id is not None:
                steps += 1
                if steps > len(self.nodes):
                    _err(t, f"nodes[{i}] 的父链成环")
                if cur.parent_id in seen:
                    _err(t, f"nodes[{i}] 的父链成环")
                seen.add(cur.parent_id)
                cur = by_id[cur.parent_id]

        # 源位置按文档顺序严格递增（计划 §1.2 "nodes 按文档顺序"）。
        for i in range(1, len(self.nodes)):
            prev_anchor = self.nodes[i - 1].source_anchor
            cur_anchor = self.nodes[i].source_anchor
            if (cur_anchor[0], cur_anchor[1]) <= (prev_anchor[0], prev_anchor[1]):
                _err(t, "节点源位置必须按文档顺序严格递增，"
                        f"nodes[{i}] 得到 {(cur_anchor[0], cur_anchor[1])} <= "
                        f"{(prev_anchor[0], prev_anchor[1])}")

        # 兄弟顺序：文档顺序 = nodes 列表顺序；ordinal 必须等于同级内位置；child_ids 对称。
        sibling_counter: dict[str | None, int] = {}
        children_in_order: dict[str | None, list[str]] = {}
        for i, n in enumerate(self.nodes):
            key = n.parent_id
            idx = sibling_counter.get(key, 0)
            if n.ordinal != idx:
                _err(t, f"nodes[{i}].ordinal 必须等于同级文档序号 {idx}，得到 {n.ordinal}")
            sibling_counter[key] = idx + 1
            children_in_order.setdefault(key, []).append(n.node_id)
        for i, n in enumerate(self.nodes):
            expected = tuple(children_in_order.get(n.node_id, ()))
            if tuple(n.child_ids) != expected:
                _err(t, f"nodes[{i}].child_ids 必须等于其子节点的文档顺序 {expected}")

        # 未归属 span：必须是显式未归属、带原因、可回溯来源与范围。
        span_ids: set[str] = set()
        for i, s in enumerate(self.unassigned):
            if not isinstance(s, OutlineSpan):
                _err(t, f"unassigned[{i}] 必须为 OutlineSpan，得到 {type(s).__name__}")
            if s.document_outline_locator != self.outline_locator:
                _err(t, f"unassigned[{i}].document_outline_locator 与 outline_locator 不一致")
            if s.node_id is not None:
                _err(t, f"unassigned[{i}].node_id 必须为 None（显式未归属）")
            if s.role != "unassigned":
                _err(t, f"unassigned[{i}].role 必须为 'unassigned'，得到 {s.role!r}")
            if s.unassigned_reason is None:
                _err(t, f"unassigned[{i}] 必须给出未归属原因（unassigned_reason）")
            if not s.layout_line_refs:
                _err(t, f"unassigned[{i}] 必须保留来源行的定位（layout_line_refs 不得为空）")
            if s.span_id in span_ids:
                _err(t, f"unassigned span_id 必须唯一，重复: {s.span_id}")
            span_ids.add(s.span_id)

        # 引用边：唯一 id、属于本 outline、端点可核验。
        edge_ids: set[str] = set()
        for i, e in enumerate(self.edges):
            if not isinstance(e, ReferenceEdge):
                _err(t, f"edges[{i}] 必须为 ReferenceEdge，得到 {type(e).__name__}")
            if e.document_outline_locator != self.outline_locator:
                _err(t, f"edges[{i}].document_outline_locator 与 outline_locator 不一致")
            if e.edge_id in edge_ids:
                _err(t, f"edge_id 必须唯一，重复: {e.edge_id}")
            edge_ids.add(e.edge_id)
            _check_edge_static(t, i, e, by_id, span_ids)

        expected_fp = derive_outline_content_fingerprint(
            outline_locator=self.outline_locator, document_version=self.document_version,
            nodes=self.nodes, edges=self.edges, unassigned=self.unassigned,
            candidate_sources=self.candidate_sources)
        if self.content_fingerprint != expected_fp:
            _err(t, "content_fingerprint 与内容不一致"
                    f"（{self.content_fingerprint!r} != {expected_fp!r}）")
        expected_id = "do-" + self.content_fingerprint[:16]
        if self.outline_id != expected_id:
            _err(t, f"outline_id 必须等于 do-<content_fingerprint[:16]>"
                    f"（{self.outline_id!r} != {expected_id!r}）")
        expected_loc = derive_document_outline_locator(
            page_layout_id=self.page_layout_id, document_id=self.document_id,
            algorithm_version=self.algorithm_version,
            schema_version=self.schema_version)
        if self.outline_locator != expected_loc:
            _err(t, "outline_locator 与派生定位身份不一致："
                    f"{self.outline_locator!r} != {expected_loc!r}")

    def node_by_id(self, node_id: str) -> OutlineNode | None:
        """按 id 取节点（纯查表，不猜测、不模糊匹配）。"""
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def span_by_id(self, span_id: str) -> "OutlineSpan | None":
        """按 id 取未归属 span（本层只持有 unassigned 集合）。"""
        for s in self.unassigned:
            if s.span_id == span_id:
                return s
        return None

    def unresolved_edges(self) -> tuple["ReferenceEdge", ...]:
        """未解析边必须显式可见（带 reason_code 与原始声明目标）。"""
        return tuple(e for e in self.edges if not e.is_resolved)

    def verify_references(self, context: "ReferenceValidationContext | None" = None
                          ) -> "VerifiedReferences":
        """**对象级引用核验**：本层唯一可以对外声称"引用已验证"的入口。

        逐个核对每一条边（TS1.3 起 `create()` / `from_dict()` 也必须经过本入口，见
        `_enforce_reference_boundary`）。核验**分层**进行，来源侧与目标侧各只有一处
        实现：

        1. **来源侧（所有边，无论是否已解析）** occurrence 必须落到**真实
           `LayoutLine`** 上：`(page_number, line_index)` 必须在核验上下文交出的真实
           `PageLayout` 上存在对应行，且该行必须与来源对象一致（`span:` 属于
           `layout_line_refs`、`node:` 与标题锚点一致、`toc:` 与真实 `TocSource`
           绑定位置一致）。`char_start` / `char_end` 是**该行 `text` 内**的区间，切片
           必须与 `normalized_text` **逐字符**相同，marker 与 `declared_target` 必须
           真的出现在该切片里。自报的 `normalized_text` / `resolution_evidence`
           **单独不构成证明**。
           **TS1.4**：`is_resolved=False` 只表示"**目标**尚未解析"，**不**表示"来源
           occurrence 可以自报"。因此未解析边同样走本层来源核验；未解析只登记
           `unresolved`，不要求目标对象存在，也**不**去猜目标。
        2. **目标侧（仅已解析边）** 目标必须解析成**真实对象**：`node:` → 本 outline
           的节点；`span:` → 本 outline 的未归属 span 或上下文提供的真实 span，且归属
           同一 outline；`table:` → 上下文提供的真实 `TableObject`，且 `document_id` /
           `document_version` / `page_layout_id` / `document_outline_locator` 与本
           outline 一致。
        3. `parent_child` 与实际父子关系和 `child_ids` 对称性一致。
        4. `table_continuation` 两端都必须是真实 `TableObject`，且其自报的
           `continuation_of_locator` / `continuation_locators` 与页序**互相印证**。
        5. `toc_to_body` 的来源必须是上下文提供的真实 `TocSource`（由真实版式重算
           身份），目录项声明标题与目标节点标题必须按版本化归一规则一致，页标签必须
           由真实版式的页码 furniture **唯一**映射到目标节点所在物理页，且**目标节点
           自身必须通过对象级锚点核验**（TS1.4 P1-2：标题锚点指向的真实行、锚点 bbox
           落在该行内、节点标题真实出现在该行里）——只凭"目录标题与 `node.title`
           字符串相同 + 页码相同"不得判 resolved。页标签映射不成立即拒绝（不猜最近
           页面、不用固定偏移），调用方想保留未解析必须显式给出
           `reason_code="toc_page_label_mapping_unproven"`。

        任何一条无法证明即抛错（不得降级为"尽力而为"）。全部通过时返回
        `VerifiedReferences` —— 它才是"引用已验证"的载体（下游必须能用
        `assert_reproducible` 在相同的 Outline + context 上重算比对）。
        """
        ctx = context if context is not None else ReferenceValidationContext()
        if not isinstance(ctx, ReferenceValidationContext):
            _err("DocumentOutline", "verify_references 的 context 必须为 "
                                    f"ReferenceValidationContext 或 None，"
                                    f"得到 {type(ctx).__name__}")
        verified: list["ReferenceEdge"] = []
        unresolved: list["ReferenceEdge"] = []
        for i, e in enumerate(self.edges):
            # 来源侧：已解析与未解析**共用**同一次核验（每条边只核验一次）。
            _verify_edge_source("DocumentOutline", i, e, outline=self, context=ctx)
            if not e.is_resolved:
                unresolved.append(e)
                continue
            _verify_edge_target_object_level("DocumentOutline", i, e,
                                             outline=self, context=ctx)
            verified.append(e)
        return VerifiedReferences(
            outline_locator=self.outline_locator, outline_id=self.outline_id,
            resolver_version=ctx.resolver_version,
            context_fingerprint=ctx.fingerprint(),
            verified_edges=tuple(verified), unresolved_edges=tuple(unresolved),
        )

    def _enforce_reference_boundary(self, context: "ReferenceValidationContext | None"
                                    ) -> None:
        """正式构造路径的引用边界（`create` / `from_dict` 共用）。

        TS1.3 P1-A：这里**只有一条**路径 —— 一律调用 `verify_references(context)`
        这一对象级核验入口。上一轮存在一条旁路：没有上下文时改用
        `_edge_statically_provable`（只检查端点字符串能否在本 outline 自持集合里
        "对上号"）来"证明"已解析边。那个函数不校验 occurrence、不校验真实版式，
        于是 `page_number=999 / line_index=999 / normalized_text='详见伪造目标'` 这样
        的伪造对象可以成功进入 Outline，只要调用者以后不去主动调 `verify_references`
        就永远不被发现。

        现在没有旁路：`context=None` 时按空上下文核验，任何需要真实对象（含真实
        `PageLayout`）的已解析边都会在这里被拒绝。`create()` 与 `from_dict()` 共用
        本方法，因此不存在"一个严格、一个宽松"。
        """
        self.verify_references(context)

    def verify_upstream(self, *, layout: PageLayout) -> None:
        """把本 outline 的文档身份与上游 PageLayout 做**对象级**核对。

        只比较非空字符串是不够的：必须比对 `page_layout_id`（revision 身份，含全部
        页面内容）与 `document_id` / `document_version`。核对不通过即拒绝，
        不做"尽力而为"的降级。
        """
        t = "DocumentOutline"
        if not isinstance(layout, PageLayout):
            _err(t, "verify_upstream 需要 PageLayout 对象，"
                    f"得到 {type(layout).__name__}")
        if layout.page_layout_id != self.page_layout_id:
            _err(t, "outline.page_layout_id 与上游 layout.page_layout_id 不一致："
                    f"{self.page_layout_id!r} != {layout.page_layout_id!r}")
        if layout.document_id != self.document_id:
            _err(t, "outline.document_id 与上游 layout.document_id 不一致："
                    f"{self.document_id!r} != {layout.document_id!r}")
        if layout.document_version != self.document_version:
            _err(t, "outline.document_version 与上游 layout.document_version 不一致："
                    f"{self.document_version!r} != {layout.document_version!r}")

    def to_dict(self) -> dict:
        return {
            "schema_type": "DocumentOutline",
            "outline_locator": self.outline_locator,
            "outline_id": self.outline_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "page_layout_id": self.page_layout_id,
            "algorithm_version": self.algorithm_version,
            "schema_version": self.schema_version,
            "nodes": [_json(x) for x in self.nodes],
            "edges": [_json(x) for x in self.edges],
            "unassigned": [_json(x) for x in self.unassigned],
            "candidate_sources": _json(self.candidate_sources),
            "content_fingerprint": self.content_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any, *,
                  reference_context: "ReferenceValidationContext | None" = None
                  ) -> "DocumentOutline":
        """反序列化（wire format 不含任何核验上下文）。

        `reference_context` 是**运行时**参数而不是被反序列化的内容：格式里没有它的
        位置，因此 round-trip 的内容身份不依赖任何序列化下来的核验上下文（P1-1）。
        """
        t = "DocumentOutline"
        d = _reject_unknown(d, {
            "schema_type", "outline_locator", "outline_id", "document_id",
            "document_version", "page_layout_id", "algorithm_version",
            "schema_version", "nodes", "edges", "unassigned", "candidate_sources",
            "content_fingerprint"}, t)
        _need_enum(d, "schema_type", t, ("DocumentOutline",))
        obj = cls(
            outline_locator=_need_str(d, "outline_locator", t),
            outline_id=_need_str(d, "outline_id", t),
            document_id=_need_str(d, "document_id", t),
            document_version=_need_str(d, "document_version", t),
            page_layout_id=_need_str(d, "page_layout_id", t),
            algorithm_version=_need_str(d, "algorithm_version", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                "OUTLINE_SCHEMA_VERSION"),
            nodes=_need_children(d, "nodes", t, OutlineNode.from_dict),
            edges=_need_children(d, "edges", t, ReferenceEdge.from_dict),
            unassigned=_need_children(d, "unassigned", t, OutlineSpan.from_dict),
            candidate_sources=_need_str_tuple(d, "candidate_sources", t),
            content_fingerprint=_need_sha256(d, "content_fingerprint", t),
        )
        obj._enforce_reference_boundary(reference_context)
        return obj

    @classmethod
    def create(cls, *, document_id: str, document_version: str, page_layout_id: str,
               nodes: tuple[OutlineNode, ...],
               edges: tuple["ReferenceEdge", ...] = (),
               unassigned: tuple["OutlineSpan", ...] = (),
               candidate_sources: tuple[str, ...] = (),
               reference_context: "ReferenceValidationContext | None" = None
               ) -> "DocumentOutline":
        """按派生身份与内容指纹构造（调用方不自行填写 locator / id / fingerprint）。

        `reference_context` 只用于引用核验，**不进入** locator / fingerprint / id；
        它的存在与否不改变同一份内容的规范形。
        """
        loc = derive_document_outline_locator(
            page_layout_id=page_layout_id, document_id=document_id,
            algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
            schema_version=V.OUTLINE_SCHEMA_VERSION)
        fp = derive_outline_content_fingerprint(
            outline_locator=loc, document_version=document_version, nodes=nodes,
            edges=edges, unassigned=unassigned, candidate_sources=candidate_sources)
        obj = cls(
            outline_locator=loc, outline_id="do-" + fp[:16], document_id=document_id,
            document_version=document_version, page_layout_id=page_layout_id,
            algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
            schema_version=V.OUTLINE_SCHEMA_VERSION, nodes=nodes, edges=edges,
            unassigned=unassigned, candidate_sources=candidate_sources,
            content_fingerprint=fp,
        )
        obj._enforce_reference_boundary(reference_context)
        return obj


def _check_edge_static(typename: str, index: int, edge: "ReferenceEdge",
                       by_id: dict, span_ids: set) -> None:
    """**上下文无关**的结构检查：本 outline 能否在自己的对象集合内自证这条已解析边。

    这里只做两件事：
      (a) 端点在**本 outline 自持集合**内解析（`node:` → nodes，`span:` →
          unassigned）时的一致性检查；
      (b) `parent_child` 与实际父子关系、`cross_reference` 自环等纯结构不变量。

    它**不做**对象存在性核验 —— 需要外部真实对象的端点留给
    `_enforce_reference_boundary` / `verify_references`（对象级核验）。
    因此"已解析"在这里**不**因为字符串长得像 id 就成立。
    """
    if not edge.is_resolved:
        return
    from_kind, from_val = edge.from_ref.split(":", 1)
    to_kind, to_val = edge.to_ref.split(":", 1)
    if from_kind == "node" and from_val not in by_id:
        _err(typename, f"edges[{index}].from_ref={edge_ref(from_kind, from_val)!r} 不能在 "
                       f"nodes 中解析；不得接受悬空的 resolved edge")
    if to_kind == "node" and to_val not in by_id:
        _err(typename, f"edges[{index}].to_ref={edge_ref(to_kind, to_val)!r} 不能在 "
                       f"nodes 中解析；不得接受悬空的 resolved edge")
    # `span:` 端点**不在这里**判定：节点内正文 span 不在本 outline 自持集合内，
    # 由调用方经 ReferenceValidationContext 交出真实对象后在对象级核验，
    # 本层不得凭字符串形状接受、也不得凭"不在 unassigned 里"拒绝。
    if edge.edge_kind == "parent_child":
        child = by_id[to_val]
        if child.parent_id != from_val:
            _err(typename, f"edges[{index}] 的 parent_child 必须与实际父子关系一致"
                           f"（{to_val!r} 的 parent_id 为 {child.parent_id!r}）")
    if edge.edge_kind == "cross_reference" and from_val == to_val:
        _err(typename, f"edges[{index}] 的 cross_reference 两端不得是同一对象")


def _occurrence_layout_line(typename: str, index: int,
                            occurrence: "ReferenceOccurrence",
                            outline: "DocumentOutline",
                            context: "ReferenceValidationContext") -> "LayoutLine":
    """把 occurrence 定位到**真实 `LayoutLine`**，返回该行。

    TS1.3 §三 的坐标语义：`(page_number, line_index)` 必须精确定位当前
    `PageLayout` 上的一条真实行，`char_start` / `char_end` 是**该行 `text` 内**的
    字符区间。上一轮把 occurrence 的坐标解释成"来源对象归一文本里的全局偏移"，
    于是：一个覆盖第 2–3 行的 span，引用其实落在第 3 行时会被错误拒绝；而伪造的
    `page=999 / line=999` 反而因为"只要不越界就对得上"而通过。

    来源类型各自的额外约束（§三.5/§三.6）：
    - `span:`：该 `(页, 行)` 必须属于 `span.layout_line_refs`，且 span 属于本
      outline、本 layout；occurrence **不要求**落在 span 的第一行；
    - `node:`：该 `(页, 行)` 必须与该节点的真实标题锚点一致，锚点 bbox 必须落在该
      行 bbox 内，节点标题必须真实出现在该行文本里；
    - `toc:`：该 `(页, 行)` 必须与真实 `TocSource` 绑定的位置一致。

    缺任何一项即 fail-closed —— 不猜最近页面、不按字符串清单放行。
    """
    kind, val = occurrence.source_ref.split(":", 1)
    if kind not in TEXT_SOURCE_REF_KINDS:
        _err(typename, f"edges[{index}] 的 occurrence 来源类型 {kind!r} 在本层没有正式"
                       f"类型化的真实原文载体（本层可核验的来源仅 "
                       f"{TEXT_SOURCE_REF_KINDS}）；不得用自报文本冒充来源")

    layout = _context_layout(typename, index, outline, context)
    line = layout.line_at(occurrence.page_number, occurrence.line_index)
    if line is None:
        _err(typename,
             f"edges[{index}] 的 occurrence 页码 {occurrence.page_number} / 行号 "
             f"{occurrence.line_index} 在核验上下文的真实 PageLayout 上不存在对应的"
             f" LayoutLine；occurrence 的坐标必须精确定位一条真实行，不得指向不存在的"
             f"页码或行号")

    if kind == "span":
        span = outline.span_by_id(val) or context.span_by_id(val)
        if span is None:
            _err(typename, f"edges[{index}].from_ref=span:{val} 既不在本 outline 的"
                           f" unassigned spans 中，也不在 ReferenceValidationContext."
                           f"spans 中；不得只凭字符串形状接受已解析边")
        if span.document_outline_locator != outline.outline_locator:
            _err(typename, f"edges[{index}] 的来源 span {span.span_id!r} 属于另一个"
                           f" outline（{span.document_outline_locator!r} != "
                           f"{outline.outline_locator!r}）")
        refs = _as_line_refs(span.layout_line_refs)
        if refs is None:
            _err(typename, f"edges[{index}] 的来源 span {span.span_id!r} 的 "
                           f"layout_line_refs 形状非法")
        if (occurrence.page_number, occurrence.line_index) not in refs:
            _err(typename,
                 f"edges[{index}] 的 occurrence 位于页码 {occurrence.page_number} / 行号 "
                 f"{occurrence.line_index}，不在来源 span {span.span_id!r} 的 "
                 f"layout_line_refs {sorted(refs)} 内；occurrence 必须落在该 span 真实"
                 f"覆盖的行上（可以是中间行或末行）")
    elif kind == "node":
        node = outline.node_by_id(val)
        if node is None:
            _err(typename, f"edges[{index}].from_ref=node:{val} 不能在 outline.nodes 中"
                           f"解析成真实对象；不得接受悬空的已解析边")
        anchor_page, anchor_line = node.source_anchor[0], node.source_anchor[1]
        if (occurrence.page_number, occurrence.line_index) != (anchor_page, anchor_line):
            _err(typename,
                 f"edges[{index}] 的 occurrence 位于页码 {occurrence.page_number} / 行号 "
                 f"{occurrence.line_index}，与节点 {node.node_id!r} 的真实标题锚点"
                 f"（页码 {anchor_page} / 行号 {anchor_line}）不一致")
        # TS1.4：锚点 / bbox / 标题是否出现在真实行里，统一由对象级锚点核验回答。
        _verify_node_anchor(typename, index, "来源", node, layout=layout)
    elif kind == "toc":
        toc = context.toc_source_by_id(val)
        if toc is None:
            _err(typename,
                 f"edges[{index}].from_ref=toc:{val} 不能在 ReferenceValidationContext."
                 f"toc_sources 中解析成真实 TocSource 对象；字符串 toc id、自报文本与"
                 f" resolution_evidence 单独都不是目录来源存在的证明")
        toc.verify_against_layout(layout)
        if (toc.page_number, toc.line_index) != \
                (occurrence.page_number, occurrence.line_index):
            _err(typename,
                 f"edges[{index}] 的 occurrence 位于页码 {occurrence.page_number} / 行号 "
                 f"{occurrence.line_index}，与真实 TocSource {toc.toc_source_id!r} 绑定的"
                 f"位置（页码 {toc.page_number} / 行号 {toc.line_index}）不一致")
        if (occurrence.char_start, occurrence.char_end) != (toc.char_start, toc.char_end):
            _err(typename,
                 f"edges[{index}] 的 occurrence 字符区间 "
                 f"[{occurrence.char_start}, {occurrence.char_end}) 与真实 TocSource "
                 f"{toc.toc_source_id!r} 绑定的区间 [{toc.char_start}, {toc.char_end}) "
                 f"不一致")
    return line


def _context_layout(typename: str, index: int, outline: "DocumentOutline",
                    context: "ReferenceValidationContext") -> "PageLayout":
    """取出**真实 `PageLayout`** 并做对象级归属核对（不是"页码/行号字符串清单"）。

    §三.10 明确要求核验上下文交出真实 `PageLayout` 对象或等价的对象级 resolver：
    页码 / 行号是否存在、该行文本是什么、行内字符区间切出什么，都只能在真实版式上
    重算。因此这里不做任何字符串层面的替代。
    """
    layout = context.layout
    if layout is None:
        _err(typename,
             f"edges[{index}] 的 occurrence 必须在**真实 PageLayout** 上核验，但核验"
             f"上下文 ReferenceValidationContext 没有交出任何 layout；必须在构造或"
             f"反序列化时就提供它，不得靠自报页码 / 行号 / 文本通过")
    if not isinstance(layout, PageLayout):
        _err(typename, f"edges[{index}] 的核验上下文 layout 必须为真实 PageLayout 对象，"
                       f"得到 {type(layout).__name__}")
    if layout.page_layout_id != outline.page_layout_id:
        _err(typename,
             f"edges[{index}] 的核验上下文 PageLayout 与本 outline 的上游 layout 不一致"
             f"（{layout.page_layout_id!r} != {outline.page_layout_id!r}）")
    if layout.document_id != outline.document_id:
        _err(typename,
             f"edges[{index}] 的核验上下文 PageLayout 属于另一文档"
             f"（{layout.document_id!r} != {outline.document_id!r}）")
    if layout.document_version != outline.document_version:
        _err(typename,
             f"edges[{index}] 的核验上下文 PageLayout 的文档版本与本 outline 不一致"
             f"（{layout.document_version!r} != {outline.document_version!r}）")
    return layout


def _as_line_refs(refs) -> "set[tuple[int, int]] | None":
    """把 `layout_line_refs` 归一成 `{(页, 行)}`；形状非法返回 None。"""
    if not isinstance(refs, tuple):
        return None
    out: set[tuple[int, int]] = set()
    for item in refs:
        if not isinstance(item, tuple) or len(item) != 2:
            return None
        p, ln = item
        for v in (p, ln):
            if not isinstance(v, int) or isinstance(v, bool):
                return None
        out.add((p, ln))
    return out


def _bbox_contains(outer, inner) -> bool:
    """`inner` 是否落在 `outer` 内（容差 `BBOX_PAGE_TOLERANCE_PT`）。

    两个 bbox 都必须是 4 元有限浮点组；形状不对一律返回 False（fail-closed）。
    """
    if not isinstance(outer, tuple) or not isinstance(inner, tuple):
        return False
    if len(outer) != 4 or len(inner) != 4:
        return False
    for v in outer + inner:
        if not is_finite(v):
            return False
    tol = BBOX_PAGE_TOLERANCE_PT
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def _verify_occurrence_source(typename: str, index: int, edge: "ReferenceEdge",
                              occurrence: "ReferenceOccurrence",
                              outline: "DocumentOutline",
                              context: "ReferenceValidationContext") -> "LayoutLine":
    """已解析边的 occurrence 必须**真的**落在一条真实 `LayoutLine` 的行内区间上。

    返回该真实行，供目标侧（例如目录项标题）继续核对。
    """
    line = _occurrence_layout_line(typename, index, occurrence, outline, context)
    # 用**真实行的原文**逐字符复核该区间：切片必须与自报归一文本**逐字符**相同，
    # 且 marker 与原文声明的目标文字都必须真的出现在该切片里。
    try:
        occurrence.verify_source_text(line.text)
    except SchemaValidationError as e:
        _err(typename, f"edges[{index}] 的 occurrence 未通过真实 LayoutLine "
                       f"（页码 {occurrence.page_number} / 行号 "
                       f"{occurrence.line_index}）复核：{e}")
    return line


def _verify_table_target(typename: str, index: int, role: str, value: str,
                         outline: "DocumentOutline",
                         context: "ReferenceValidationContext") -> "TableObject":
    """`table:` 端点必须解析成**真实 TableObject**，且归属与本 outline 一致。"""
    table = context.table_by_locator(value)
    if table is None:
        _err(typename, f"edges[{index}].{role}=table:{value} 不能在 "
                       f"ReferenceValidationContext.tables 中解析成真实 TableObject；"
                       f"不得用字符串清单或同值回填冒充目标对象存在")
    if table.document_id != outline.document_id:
        _err(typename, f"edges[{index}].{role}=table:{value} 属于另一文档"
                       f"（{table.document_id!r} != {outline.document_id!r}）")
    if table.document_version != outline.document_version:
        _err(typename, f"edges[{index}].{role}=table:{value} 的文档版本与本 outline "
                       f"不一致（{table.document_version!r} != "
                       f"{outline.document_version!r}）")
    if table.document_outline_locator != outline.outline_locator:
        _err(typename, f"edges[{index}].{role}=table:{value} 不属于本 outline"
                       f"（{table.document_outline_locator!r} != "
                       f"{outline.outline_locator!r}）")
    if table.page_layout_id != outline.page_layout_id:
        _err(typename, f"edges[{index}].{role}=table:{value} 的上游 layout 与本 outline "
                       f"不一致（{table.page_layout_id!r} != {outline.page_layout_id!r}）")
    return table


def _verify_edge_source(typename: str, index: int, edge: "ReferenceEdge", *,
                        outline: "DocumentOutline",
                        context: "ReferenceValidationContext") -> "LayoutLine | None":
    """**来源侧**核验：occurrence 必须真的落在一条真实 `LayoutLine` 的行内区间上。

    TS1.4 P1-1：本函数对**已解析与未解析**的边一视同仁。未解析只表示"**目标**尚未
    解析"，不表示"**来源** occurrence 可以自报"；上一轮 `verify_references()` 对
    `is_resolved=False` 直接 `unresolved.append(e)` 就 `continue`，于是
    `page=999 / line=999` 这样的虚假 occurrence 可以进入结构树。

    `verify_references()` 对每条边只调用本函数**一次**；已解析边随后进入
    `_verify_edge_target_object_level()` 做目标侧核验，来源侧不重复核验。
    """
    if edge.occurrence is not None:
        return _verify_occurrence_source(typename, index, edge, edge.occurrence,
                                        outline, context)
    if edge.edge_kind in OCCURRENCE_REQUIRED_EDGE_KINDS:
        _err(typename, f"edges[{index}] 的 {edge.edge_kind!r} 必须有真实文本 occurrence "
                       f"才能核验来源原文")
    return None


def _verify_node_anchor(typename: str, index: int, role: str, node: "OutlineNode",
                        *, layout: "PageLayout") -> None:
    """`OutlineNode` 的**对象级锚点核验**（TS1.4 P1-2 提取的唯一实现）。

    节点标题不是自证事实：`node.title` 只是一个字符串，同页普通正文行上的节点可以
    声称自己就是标题节点。因此凡是把"某个节点"当成真实标题节点使用的地方，都必须
    在本函数上核验：

    1. `source_anchor` 的 `(page_number, line_index)` 必须在**真实 `PageLayout`** 上
       存在对应的 `LayoutLine`；
    2. 锚点 bbox 必须落在该真实行的 bbox 内；
    3. 节点标题必须**真实出现在**该行文本里。

    `role` 只用于错误信息（`from_ref` 来源 / `toc_to_body` 目标），规则完全相同——
    调用方不得各写一套。
    """
    page_number, line_index = node.source_anchor[0], node.source_anchor[1]
    line = layout.line_at(page_number, line_index)
    if line is None:
        _err(typename,
             f"edges[{index}] 的{role}节点 {node.node_id!r} 的标题锚点（页码 "
             f"{page_number} / 行号 {line_index}）在真实 PageLayout 上不存在对应的"
             f" LayoutLine；节点必须由真实版式上的真实标题行支撑")
    if not _bbox_contains(line.bbox, node.source_anchor[2]):
        _err(typename,
             f"edges[{index}] 的{role}节点 {node.node_id!r} 标题锚点 bbox "
             f"{node.source_anchor[2]!r} 不在真实 LayoutLine 的 bbox {line.bbox!r} 内；"
             f"节点标题锚点与真实版式必须能确定性核对")
    if node.title not in line.text:
        _err(typename,
             f"edges[{index}] 的{role}节点 {node.node_id!r} 的标题 {node.title!r} 没有"
             f"真实出现在页码 {page_number} / 行号 {line_index} 的 LayoutLine 文本 "
             f"{line.text!r} 中；节点标题与真实版式行必须能确定性核对")


def _verify_edge_target_object_level(typename: str, index: int, edge: "ReferenceEdge", *,
                                     outline: "DocumentOutline",
                                     context: "ReferenceValidationContext") -> None:
    """在**真实对象**上核验一条**已解析**边的**目标侧**；任何一条核验不过即拒绝。

    来源侧已由 `_verify_edge_source()` 核验过（TS1.4 分层），本函数不再重复。
    """
    from_val = edge.from_ref.split(":", 1)[1]
    to_kind, to_val = edge.to_ref.split(":", 1)
    if to_kind in UNRESOLVABLE_REF_KINDS:
        _err(typename, f"edges[{index}] 的目标类型 {to_kind!r} 在本层没有正式类型化"
                       f"对象，不得标为 resolved")
    if edge.edge_kind not in RESOLVABLE_EDGE_KINDS:
        _err(typename, f"edges[{index}] 的 edge_kind={edge.edge_kind!r} 在本层没有"
                       f"对象级核验路径，不得标为 resolved")
    by_id = {n.node_id: n for n in outline.nodes}
    span_ids = {s.span_id for s in outline.unassigned}
    _check_edge_static(typename, index, edge, by_id, span_ids)

    # 目标侧：必须是真实对象。
    if to_kind == "node":
        if to_val not in by_id:
            _err(typename, f"edges[{index}].to_ref=node:{to_val} 不能在 nodes 中解析")
    elif to_kind == "span":
        span = outline.span_by_id(to_val) or context.span_by_id(to_val)
        if span is None:
            _err(typename, f"edges[{index}].to_ref=span:{to_val} 既不在本 outline 的"
                           f" unassigned spans 中，也不在上下文的真实 spans 中；"
                           f"不得接受悬空的 resolved edge")
        if span.document_outline_locator != outline.outline_locator:
            _err(typename, f"edges[{index}].to_ref=span:{to_val} 属于另一个 outline"
                           f"（{span.document_outline_locator!r} != "
                           f"{outline.outline_locator!r}）")
    elif to_kind == "table":
        _verify_table_target(typename, index, "to_ref", to_val, outline, context)

    if edge.edge_kind == "parent_child":
        child = by_id[to_val]
        if child.parent_id != from_val:
            _err(typename, f"edges[{index}] 的 parent_child 与实际父子关系不符"
                           f"（{to_val!r} 的 parent_id 为 {child.parent_id!r}）")
        siblings = tuple(n.node_id for n in outline.nodes if n.parent_id == from_val)
        parent = by_id[from_val]
        if tuple(parent.child_ids) != siblings:
            _err(typename, f"edges[{index}] 的 parent_child 与 {from_val!r}.child_ids "
                           f"不对称（{tuple(parent.child_ids)!r} != {siblings!r}）")
    if edge.edge_kind == "table_continuation":
        prev = _verify_table_target(typename, index, "from_ref", from_val,
                                    outline, context)
        nxt = _verify_table_target(typename, index, "to_ref", to_val,
                                   outline, context)
        if nxt.continuation_of_locator != prev.table_locator:
            _err(typename, f"edges[{index}] 的续表目标 {nxt.table_locator!r} 自报的前置为 "
                           f"{nxt.continuation_of_locator!r}，与来源表 "
                           f"{prev.table_locator!r} 不一致")
        if nxt.table_locator not in tuple(prev.continuation_locators):
            _err(typename, f"edges[{index}] 的来源表 {prev.table_locator!r} 未把 "
                           f"{nxt.table_locator!r} 记为自己的续接片段")
        if nxt.page_number <= prev.page_number:
            _err(typename, f"edges[{index}] 的续表片段必须位于更后的页面"
                           f"（{nxt.page_number} <= {prev.page_number}）")
    if edge.edge_kind == "toc_to_body":
        _verify_toc_body_target(typename, index, edge, to_val, outline, context)



def _normalize_toc_title(title: str) -> str:
    """目录项标题 / 节点标题的**确定性、公司无关**归一（版本 `V.TOC_TITLE_MATCH_VERSION`）。

    只做结构性归一：去掉全部空白与目录行常见的点线填充字符。**不做**同义词替换、
    编号改写、子串近似或模糊匹配——"目录项指向哪个标题"是候选生成问题（TS2/TS3），
    本层只回答"声明的标题与真实节点标题能否确定性对上"。
    """
    return "".join(ch for ch in title
                   if not ch.isspace() and ch not in _TOC_TITLE_FILLER_CHARS)


_RE_PAGE_LABEL_TOKEN = re.compile(r"\d+")


def _page_label_of_furniture_text(text: str) -> "str | None":
    """从真实页码 furniture 行的文本里取出该页的**页标签**（最后一段数字）。

    目录页写的"第 3 页 / 3 / 3-1"与 PDF 物理页序号往往有偏移（封面、插图不编号）。
    本层不猜偏移：`page_number` 类 furniture 是版式层**已经记录**在真实物理页上的
    印刷标记，因此"页标签 → 物理页"是从真实版式**重算**出来的，不是估出来的。
    """
    hits = _RE_PAGE_LABEL_TOKEN.findall(text)
    return hits[-1] if hits else None


def _resolve_page_label(layout: "PageLayout", label: str) -> "int | None":
    """把目录项声明的页标签映射到**唯一**的物理页码；无法唯一确定时返回 None。

    返回 None 的情形（都必须 fail-closed，见 §四.6）：真实版式上没有任何一页的
    页码 furniture 带该标签，**或**有多于一页带同一标签（歧义不得消解）。
    """
    matched: set[int] = set()
    for page_number, line in layout.furniture_lines("page_number"):
        if _page_label_of_furniture_text(line.text) == label:
            matched.add(page_number)
    if len(matched) != 1:
        return None
    return next(iter(matched))


def _verify_toc_body_target(typename: str, index: int, edge: "ReferenceEdge",
                            to_val: str, outline: "DocumentOutline",
                            context: "ReferenceValidationContext") -> None:
    """`toc_to_body` 目标侧的对象级核验（§四.5 / §四.6 + TS1.4 P1-2）。

    到这一步为止已经成立：来源是真实 `TocSource`、绑定的真实 `LayoutLine` 上确实
    存在该目录项、occurrence 的行内切片逐字符等于自报文本。本函数再加三条：

    1. **标题**：目录项声明标题与目标节点的真实 `title` 必须按版本化、公司无关的
       归一规则一致；
    2. **页码**：目录项页标签必须能由真实版式的页码 furniture **唯一**映射到一个
       物理页，且该物理页必须就是目标节点标题所在的真实页码。映射不成立即拒绝，
       **不猜**最近页面、**不用**固定偏移；
    3. **目标节点锚点（TS1.4 P1-2）**：目标节点自身必须通过
       `_verify_node_anchor` —— 它的 `source_anchor` 要指向一条真实 `LayoutLine`、
       锚点 bbox 要落在该行内、节点标题要**真实出现在**该行里。上一轮只比较
       "目录标题字符串 == `node.title`" 与"页标签映射的物理页 == 锚点页"，于是同一
       物理页上**普通正文行**里的节点可以冒充正文标题节点：它自报标题与目录项一致、
       页码一致，却没有任何真实标题行支撑。字符串与页码都不构成标题节点的证明。
    """
    occurrence = edge.occurrence
    layout = _context_layout(typename, index, outline, context)
    target = outline.node_by_id(to_val)
    if target is None:
        _err(typename, f"edges[{index}].to_ref=node:{to_val} 不能在 nodes 中解析")
    declared = occurrence.declared_target
    if declared is None:
        _err(typename, f"edges[{index}] 的 toc_to_body 必须保留目录项声明的标题文字，"
                       f"否则无法与目标节点标题确定性核对")
    if _normalize_toc_title(declared) != _normalize_toc_title(target.title):
        _err(typename,
             f"edges[{index}] 的目录项声明标题 {declared!r} 与目标节点 "
             f"{target.node_id!r} 的标题 {target.title!r} 不一致"
             f"（标题归一规则版本 {V.TOC_TITLE_MATCH_VERSION}）；目录项必须指向它"
             f"真正声明的那个标题")
    kind, val = occurrence.source_ref.split(":", 1)
    toc = context.toc_source_by_id(val)
    if toc is None:
        _err(typename, f"edges[{index}].from_ref=toc:{val} 不能在 "
                       f"ReferenceValidationContext.toc_sources 中解析成真实 "
                       f"TocSource 对象")
    physical = _resolve_page_label(layout, toc.declared_page_label)
    if physical is None:
        _err(typename,
             f"edges[{index}] 的目录项页标签 {toc.declared_page_label!r} 无法在真实 "
             f"PageLayout 上唯一映射到一个物理页码（无对应页或对应多页）：必须保持"
             f"未解析并给出原因码 toc_page_label_mapping_unproven，不得猜最近页面、"
             f"不得使用固定偏移（页标签映射规则版本 "
             f"{V.TOC_PAGE_LABEL_MATCH_VERSION}）")
    if physical != target.source_anchor[0]:
        _err(typename,
             f"edges[{index}] 的目录项页标签 {toc.declared_page_label!r} 映射到的物理页 "
             f"{physical} 与目标节点 {target.node_id!r} 标题所在的真实页码 "
             f"{target.source_anchor[0]} 不一致；目录项页码证据必须与目标对象一致")
    # TS1.4 P1-2：目标节点必须自己站得住 —— 标题锚点指向的真实行、bbox 落在行内、
    # 标题真实出现在该行里。只凭字符串与页码一致不得判 resolved。
    _verify_node_anchor(typename, index, "目标", target, layout=layout)


def edge_ref(kind: str, value: str) -> str:
    """构造 `<kind>:<id>` 引用字符串（唯一入口，避免各处手写前缀）。"""
    return f"{kind}:{value}"


def derive_document_outline_locator(*, page_layout_id: str, document_id: str,
                                    algorithm_version: str,
                                    schema_version: str) -> str:
    """`loc-do-<sha256[:16]>`：稳定定位。

    绑定 `page_layout_id`（因此间接绑定 company 与 document_version）并显式覆盖
    `document_id`：不同文档、不同公司的 outline 定位身份必须不同。
    """
    return locator("do", {
        "page_layout_id": page_layout_id,
        "document_id": document_id,
        "algorithm_version": algorithm_version,
        "schema_version": schema_version,
    })


def derive_outline_content_fingerprint(*, outline_locator: str, document_version: str,
                                       nodes: tuple, edges: tuple, unassigned: tuple,
                                       candidate_sources: tuple) -> str:
    """`DocumentOutline` 内容指纹：文档版本 + 树 + 边 + 未归属 + 候选来源。

    `document_version` 显式进入指纹（`document_id` 经 `outline_locator` 进入）：
    "同一 id 对应两种规范形"必须在构造期就被拦下，因此凡是会改变 outline **内容**
    的字段都必须参与指纹。

    **核验上下文不在这里**（TS1.2 P1-1）：`ReferenceValidationContext` 是运行时
    依赖，不是文档内容。上一轮把"调用方提供的目标清单"持久化并计入指纹，导致
    "仅增加一个没有任何边使用的目标"就换 `content_fingerprint` / `outline_id`。
    引用核验的结论由 `VerifiedReferences` 承载，同样不进入内容身份。
    """
    return sha256_canonical({
        "outline_locator": outline_locator,
        "document_version": document_version,
        "nodes": [_json(x) for x in nodes],
        "edges": [_json(x) for x in edges],
        "unassigned": [_json(x) for x in unassigned],
        "candidate_sources": _json(candidate_sources),
    })


def derive_document_outline_id(*, outline_locator: str, document_version: str,
                               nodes: tuple, edges: tuple, unassigned: tuple,
                               candidate_sources: tuple) -> str:
    """`do-<sha256[:16]>`：不可变 revision 身份（= 内容指纹前 16 位）。"""
    return "do-" + derive_outline_content_fingerprint(
        outline_locator=outline_locator, document_version=document_version,
        nodes=nodes, edges=edges, unassigned=unassigned,
        candidate_sources=candidate_sources)[:16]


# ---------------------------------------------------------------------------
# 3. 导航简介：SynopsisSnippet / NavigationSynopsis
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SynopsisSnippet:
    """**单个来源 span** 的连续原文片段（逐字符相等；禁止跨 span 拼接）。

    寻址：`(synopsis_id, snippet_index)`。
    """

    span_id: str
    snippet_index: int
    char_start: int
    char_end: int
    text: str

    def __post_init__(self) -> None:
        t = "SynopsisSnippet"
        if not isinstance(self.span_id, str) or self.span_id == "":
            _err(t, "span_id 必须为非空字符串")
        for name in ("snippet_index", "char_start", "char_end"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool):
                _err(t, f"{name} 必须为 int，得到 {v!r}")
        if self.snippet_index < 0:
            _err(t, f"snippet_index 不得小于 0，得到 {self.snippet_index}")
        if self.char_start < 0:
            _err(t, f"char_start 不得小于 0，得到 {self.char_start}")
        if self.char_end <= self.char_start:
            _err(t, f"char_end 必须大于 char_start，得到 "
                    f"{(self.char_start, self.char_end)}")
        if not isinstance(self.text, str) or self.text == "":
            _err(t, "text 必须为非空字符串")
        if self.char_end - self.char_start != len(self.text):
            _err(t, "char_end - char_start 必须等于 text 长度"
                    f"（{(self.char_start, self.char_end)} vs {len(self.text)}）")

    def to_dict(self) -> dict:
        return {
            "schema_type": "SynopsisSnippet",
            "span_id": self.span_id,
            "snippet_index": self.snippet_index,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "SynopsisSnippet":
        t = "SynopsisSnippet"
        d = _reject_unknown(d, {
            "schema_type", "span_id", "snippet_index", "char_start", "char_end",
            "text"}, t)
        _need_enum(d, "schema_type", t, ("SynopsisSnippet",))
        return cls(
            span_id=_need_str(d, "span_id", t),
            snippet_index=_need_int(d, "snippet_index", t, lo=0),
            char_start=_need_int(d, "char_start", t, lo=0),
            char_end=_need_int(d, "char_end", t, lo=1),
            text=_need_str(d, "text", t),
        )


@dataclass(frozen=True)
class NavigationSynopsis:
    """逐可导航节点的**抽取式**导航简介：`snippets[]`，每个 snippet 可逐字符回溯。

    - **稳定定位身份** `synopsis_locator`：`loc-ns-<h(node_id, SYNOPSIS_VERSION,
      source_span_ids)>`。
    - **不可变 revision 身份** `synopsis_id`：在 locator 之上绑定 schema version、
      `status`、`reason_code` 与全部 `snippets` —— 因此"原文来源不变但摘录被改写/
      状态翻转"同样是另一个身份（P1-1）。

    简介与标题**只作候选导航**，永不是事实 / 引用 / 覆盖证明：下游的 `set_complete`、
    覆盖与权威判定不得读取本对象（见计划 §4）。
    """

    synopsis_locator: str
    synopsis_id: str
    node_id: str
    schema_version: str
    synopsis_version: str
    status: str
    reason_code: str | None
    snippets: tuple[SynopsisSnippet, ...]
    source_span_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        t = "NavigationSynopsis"
        for name in ("synopsis_locator", "synopsis_id", "node_id"):
            if not isinstance(getattr(self, name), str) or getattr(self, name) == "":
                _err(t, f"{name} 必须为非空字符串")
        # TS4 §18.11.2 / C1：**必须绑定常量名**。不绑定的话，已退役的 `ns-1` /
        # `nss-1` 只能得到笼统的"版本不符"，无法与"打错的字符串"区分，也就无法给出
        # "这是被替换掉的旧规则，须显式迁移或重算"这一专门结论（§18.12.3）。
        #
        # §19.0.1 方案 C：`nss-2` / `ns-2` 是**本类的 current 版本**，无历史分支、无例外
        # 入口。TS5 的 `nss-3` / `ns-3` 属于另一个类型（`FinalNavigationSynopsis`），
        # 因此在这里被**显式**拒绝，报"你拿的是另一个类型的版本轴"，而不是笼统的
        # "未知版本"——否则调用方会以为那是本类的一个未来版本，从而去"补一个 reader"。
        _reject_foreign_final_synopsis_version(
            t, self.schema_version, self.synopsis_version)
        _check_version(t, "synopsis_version", self.synopsis_version,
                       V.SYNOPSIS_VERSION, "SYNOPSIS_VERSION")
        _check_version(t, "schema_version", self.schema_version,
                       V.SYNOPSIS_SCHEMA_VERSION, "SYNOPSIS_SCHEMA_VERSION")
        if self.status not in SYNOPSIS_STATUSES:
            _err(t, f"status 必须属于 {SYNOPSIS_STATUSES}，得到 {self.status!r}")
        if not isinstance(self.snippets, tuple):
            _err(t, "snippets 必须为元组")
        if not isinstance(self.source_span_ids, tuple):
            _err(t, "source_span_ids 必须为元组")
        for i, sid in enumerate(self.source_span_ids):
            if not isinstance(sid, str) or sid == "":
                _err(t, f"source_span_ids[{i}] 必须为非空字符串")
        if len(set(self.source_span_ids)) != len(self.source_span_ids):
            _err(t, "source_span_ids 不得重复（去重后按源顺序）")

        if self.status == "available":
            if not self.snippets:
                _err(t, "status='available' 时 snippets 不得为空")
            if self.reason_code is not None:
                _err(t, "status='available' 时 reason_code 必须为 None")
            ordered: list[str] = []
            for i, sn in enumerate(self.snippets):
                if not isinstance(sn, SynopsisSnippet):
                    _err(t, f"snippets[{i}] 必须为 SynopsisSnippet，得到 {type(sn).__name__}")
                if sn.snippet_index != i:
                    _err(t, f"snippets[{i}].snippet_index 必须等于其位置 {i}")
                if sn.span_id not in ordered:
                    ordered.append(sn.span_id)
            if tuple(ordered) != self.source_span_ids:
                _err(t, "source_span_ids 必须等于 snippets 的 span_id 按源顺序去重"
                        f"（{tuple(ordered)} vs {self.source_span_ids}）")
        else:
            if self.snippets:
                _err(t, "status='synopsis_unavailable' 时 snippets 必须为空")
            if self.reason_code not in SYNOPSIS_REASON_CODES:
                _err(t, f"status='synopsis_unavailable' 时 reason_code 必须属于 "
                        f"{SYNOPSIS_REASON_CODES}，得到 {self.reason_code!r}")

        expected_loc = derive_synopsis_locator(
            node_id=self.node_id, synopsis_version=self.synopsis_version,
            source_span_ids=self.source_span_ids)
        if self.synopsis_locator != expected_loc:
            _err(t, "synopsis_locator 与派生定位身份不一致："
                    f"{self.synopsis_locator!r} != {expected_loc!r}")
        expected = derive_synopsis_id(
            synopsis_locator=self.synopsis_locator, schema_version=self.schema_version,
            status=self.status, reason_code=self.reason_code, snippets=self.snippets)
        if self.synopsis_id != expected:
            _err(t, f"synopsis_id 与派生身份不一致：{self.synopsis_id!r} != {expected!r}")

    def is_navigation_only(self) -> bool:
        """恒为 True：本对象的唯一合法用途是候选导航（不得充当证据）。"""
        return True

    def to_dict(self) -> dict:
        return {
            "schema_type": "NavigationSynopsis",
            "synopsis_locator": self.synopsis_locator,
            "synopsis_id": self.synopsis_id,
            "node_id": self.node_id,
            "schema_version": self.schema_version,
            "synopsis_version": self.synopsis_version,
            "status": self.status,
            "reason_code": self.reason_code,
            "snippets": [_json(x) for x in self.snippets],
            "source_span_ids": _json(self.source_span_ids),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "NavigationSynopsis":
        t = "NavigationSynopsis"
        d = _reject_unknown(d, {
            "schema_type", "synopsis_locator", "synopsis_id", "node_id",
            "schema_version", "synopsis_version", "status", "reason_code", "snippets",
            "source_span_ids"}, t)
        _need_enum(d, "schema_type", t, ("NavigationSynopsis",))
        return cls(
            synopsis_locator=_need_str(d, "synopsis_locator", t),
            synopsis_id=_need_str(d, "synopsis_id", t),
            node_id=_need_str(d, "node_id", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                "SYNOPSIS_SCHEMA_VERSION"),
            synopsis_version=_need_str(d, "synopsis_version", t),
            status=_need_enum(d, "status", t, SYNOPSIS_STATUSES),
            reason_code=_need_str(d, "reason_code", t, none_ok=True),
            snippets=_need_children(d, "snippets", t, SynopsisSnippet.from_dict),
            source_span_ids=_need_str_tuple(d, "source_span_ids", t),
        )

    @classmethod
    def _build(cls, *, node_id: str, status: str, reason_code: str | None,
               snippets: tuple[SynopsisSnippet, ...],
               source_span_ids: tuple[str, ...]) -> "NavigationSynopsis":
        loc = derive_synopsis_locator(
            node_id=node_id, synopsis_version=V.SYNOPSIS_VERSION,
            source_span_ids=source_span_ids)
        return cls(
            synopsis_locator=loc,
            synopsis_id=derive_synopsis_id(
                synopsis_locator=loc, schema_version=V.SYNOPSIS_SCHEMA_VERSION,
                status=status, reason_code=reason_code, snippets=snippets),
            node_id=node_id, schema_version=V.SYNOPSIS_SCHEMA_VERSION,
            synopsis_version=V.SYNOPSIS_VERSION, status=status,
            reason_code=reason_code, snippets=snippets,
            source_span_ids=source_span_ids,
        )

    @classmethod
    def available(cls, *, node_id: str,
                  snippets: tuple[SynopsisSnippet, ...]) -> "NavigationSynopsis":
        ordered: list[str] = []
        for sn in snippets:
            if sn.span_id not in ordered:
                ordered.append(sn.span_id)
        return cls._build(node_id=node_id, status="available", reason_code=None,
                          snippets=snippets, source_span_ids=tuple(ordered))

    @classmethod
    def unavailable(cls, *, node_id: str, reason_code: str,
                    source_span_ids: tuple[str, ...] = ()) -> "NavigationSynopsis":
        return cls._build(node_id=node_id, status="synopsis_unavailable",
                          reason_code=reason_code, snippets=(),
                          source_span_ids=source_span_ids)


def _reject_foreign_final_synopsis_version(typename: str, schema_version: Any,
                                           synopsis_version: Any) -> None:
    """TS4 `NavigationSynopsis` 对 TS5 final 版本轴的**显式**拒绝（§19.0.1 方案 C）。

    `nss-3` / `ns-3` 不是本类的"未来版本"，而是另一个 public wire type
    （`table_schema.FinalNavigationSynopsis`）的版本轴。若只报"未知版本"，调用方会
    以为该"补一个 reader"即可，于是两个类型又被合回一个——正是方案 C 要禁止的。
    因此这里单独给出方向性错误：**你拿的是另一个类型**。

    只拒绝**精确**的 final 版本对：其余非法值仍走 `_check_version` 的常规路径
    （已登记 legacy → "须显式迁移"；其他 → "必须为当前版本"），两条都 fail-closed。
    """
    if (schema_version, synopsis_version) != (
            V.FINAL_SYNOPSIS_SCHEMA_VERSION, V.FINAL_SYNOPSIS_VERSION):
        return
    _err(typename,
         f"schema_version={schema_version!r} / synopsis_version={synopsis_version!r} "
         f"是 TS5 `FinalNavigationSynopsis` 的版本轴，不是 TS4 `NavigationSynopsis` 的；"
         f"本类只接受 "
         f"{(V.SYNOPSIS_SCHEMA_VERSION, V.SYNOPSIS_VERSION)}。两个类型不得 alias、"
         f"不得互 cast、不得按 child 猜 reader——final 简介必须由 "
         f"`table_schema.FinalNavigationSynopsis` 读取")


def derive_synopsis_locator(*, node_id: str, synopsis_version: str,
                            source_span_ids: tuple[str, ...]) -> str:
    """`loc-ns-<sha256[:16]>`：稳定定位（原文来源变化才换 locator）。"""
    return locator("ns", {
        "node_id": node_id,
        "synopsis_version": synopsis_version,
        "source_span_ids": list(source_span_ids),
    })


def derive_synopsis_id(*, synopsis_locator: str, schema_version: str, status: str,
                       reason_code: str | None,
                       snippets: tuple) -> str:
    """`ns-<sha256[:16]>`：不可变 revision 身份（含状态、原因码与全部片段）。"""
    return identity("ns", {
        "synopsis_locator": synopsis_locator,
        "schema_version": schema_version,
        "status": status,
        "reason_code": reason_code,
        "snippets": [_json(s) for s in snippets],
    })


# ---------------------------------------------------------------------------
# 4. 导航 profile：AspectNavigationEntry / AspectNavigationProfile
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AspectNavigationEntry:
    """单条 Contract aspect 的导航候选派生（**只排序，不改状态**）。

    寻址：`(profile_id, aspect_id)`。`nav_keys` 允许为空：无命中候选时下游必须
    显式记 gap，**不得**反推"无覆盖"（计划 §5.3）。

    `parent_keys`（`anps-3` 新增）是与 `nav_keys` **不同层**的键：`nav_keys` 来自该
    aspect 自己的声明字段，`parent_keys` 来自它在冻结 Contract 里的**祖先声明**
    （topic 标题 / question 文本的枚举项），用于"子项在本树没有独立标题或正文"时沿真实
    标题树找到那个持有正文的父节点。因此两键集**必须互斥**：把 aspect 自己的键混进祖先层
    就等于凭空造出一层"父节点"，这正是本字段要防的静默泛化。

    `subject_head`（`anps-4` 新增）是该 aspect 的**主体标签**：`requirement_text` 里
    括号之前的头部（无括号即 `requirement_text` 自身）规范化后的形态；推导不出非空头部时
    为 `""`（如实表示"这条 aspect 没有可用的主体标签"，**不是**静默留空）。它**不参与
    打分**（`nav_keys` 才是打分键），只用于读根资格的**主体邻接**判据
    （`NAV_ROOT_SUBJECT_RULE_ID`）：读根必须与该主体标签共享 ≥2 字最长公共子串。
    正因为它与 `nav_keys` 是两层，`关联方`（某 aspect 的声明字段）单独命中
    `十三、关联方及关联交易` 时不再能把那一章读成"客户当前集中度"的材料。
    """

    aspect_id: str
    question_id: str
    topic_id: str
    content_role: str
    display_tier: str
    nav_keys: tuple[str, ...]
    parent_keys: tuple[str, ...]
    subject_head: str
    expected_forms: tuple[str, ...]
    derivation: tuple[str, ...]

    def __post_init__(self) -> None:
        t = "AspectNavigationEntry"
        for name in ("aspect_id", "question_id", "topic_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        if self.content_role not in CONTENT_ROLES:
            _err(t, f"content_role 必须属于冻结 Contract 的 CONTENT_ROLES，"
                    f"得到 {self.content_role!r}")
        if self.display_tier not in DISPLAY_TIERS:
            _err(t, f"display_tier 必须属于冻结 Contract 的 DISPLAY_TIERS，"
                    f"得到 {self.display_tier!r}")
        if not isinstance(self.subject_head, str):
            _err(t, "subject_head 必须为字符串（推导不出主体标签时为空串，如实表示无标签，"
                    "不得为 None）")
        if self.subject_head != self.subject_head.strip():
            _err(t, "subject_head 不得带首尾空白（推导侧已规范化，出现空白即表示"
                    "上游未经 normalize_navigation_text 处理）")
        for name in ("nav_keys", "parent_keys", "expected_forms", "derivation"):
            v = getattr(self, name)
            if not isinstance(v, tuple):
                _err(t, f"{name} 必须为元组")
            for i, x in enumerate(v):
                if not isinstance(x, str) or x == "":
                    _err(t, f"{name}[{i}] 必须为非空字符串")
            if len(set(v)) != len(v):
                _err(t, f"{name} 不得重复")
        overlap = sorted(set(self.nav_keys) & set(self.parent_keys))
        if overlap:
            _err(t, f"parent_keys 与 nav_keys 必须互斥（祖先层不得复用 aspect 自己的键）："
                    f"{overlap}")
        if not self.derivation:
            _err(t, "derivation 不得为空：每个 nav_key 必须可回溯到 Contract 字段 + 规则条目"
                    "（禁止按 aspect 手写特例）")

    def to_dict(self) -> dict:
        return {
            "schema_type": "AspectNavigationEntry",
            "aspect_id": self.aspect_id,
            "question_id": self.question_id,
            "topic_id": self.topic_id,
            "content_role": self.content_role,
            "display_tier": self.display_tier,
            "nav_keys": _json(self.nav_keys),
            "parent_keys": _json(self.parent_keys),
            "subject_head": self.subject_head,
            "expected_forms": _json(self.expected_forms),
            "derivation": _json(self.derivation),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "AspectNavigationEntry":
        t = "AspectNavigationEntry"
        d = _reject_unknown(d, {
            "schema_type", "aspect_id", "question_id", "topic_id", "content_role",
            "display_tier", "nav_keys", "parent_keys", "subject_head", "expected_forms",
            "derivation"}, t)
        _need_enum(d, "schema_type", t, ("AspectNavigationEntry",))
        return cls(
            aspect_id=_need_str(d, "aspect_id", t),
            question_id=_need_str(d, "question_id", t),
            topic_id=_need_str(d, "topic_id", t),
            content_role=_need_enum(d, "content_role", t, CONTENT_ROLES),
            display_tier=_need_enum(d, "display_tier", t, DISPLAY_TIERS),
            nav_keys=_need_str_tuple(d, "nav_keys", t),
            parent_keys=_need_str_tuple(d, "parent_keys", t),
            subject_head=_need_str(d, "subject_head", t, empty_ok=True),
            expected_forms=_need_str_tuple(d, "expected_forms", t),
            derivation=_need_str_tuple(d, "derivation", t),
        )


@dataclass(frozen=True)
class AspectNavigationProfile:
    """由**冻结 Contract + 公司无关规则文件**派生的版本化导航资产（构建于 TS6）。

    - **稳定定位身份** `profile_locator`：`loc-anp-<h(contract_version,
      contract_fingerprint, PROFILE_RULE_VERSION)>`；`company_id` **不得**进入任何
      派生输入，因此两个公司输入产出逐字节相同的结果。
    - **不可变 revision 身份** `profile_id`：在 locator 之上绑定 schema version 与
      全部条目（P1-1）。

    本对象只做 node/table **候选**排序，不能改变 aspect 状态、不产生 coverage 或
    authority（`is_profile()` 恒为 True，`produces_coverage()` 恒为 False）。
    """

    profile_locator: str
    profile_id: str
    contract_version: str
    contract_fingerprint: str
    rule_version: str
    schema_version: str
    entries: tuple[AspectNavigationEntry, ...]

    def __post_init__(self) -> None:
        t = "AspectNavigationProfile"
        for name in ("profile_locator", "profile_id"):
            if not isinstance(getattr(self, name), str) or getattr(self, name) == "":
                _err(t, f"{name} 必须为非空字符串")
        if not isinstance(self.contract_version, str) or self.contract_version == "":
            _err(t, "contract_version 必须为非空字符串")
        if not _RE_SHA256_HEX.match(self.contract_fingerprint):
            _err(t, "contract_fingerprint 必须为 64 位小写十六进制 sha256")
        _check_version(t, "rule_version", self.rule_version, V.PROFILE_RULE_VERSION)
        _check_version(t, "schema_version", self.schema_version,
                       V.PROFILE_SCHEMA_VERSION)
        if not isinstance(self.entries, tuple) or not self.entries:
            _err(t, "entries 必须为非空元组")
        seen: set[str] = set()
        for i, e in enumerate(self.entries):
            if not isinstance(e, AspectNavigationEntry):
                _err(t, f"entries[{i}] 必须为 AspectNavigationEntry，"
                        f"得到 {type(e).__name__}")
            if e.aspect_id in seen:
                _err(t, f"entries 必须对每条 aspect 恰好一条，重复: {e.aspect_id}")
            seen.add(e.aspect_id)
        expected_loc = derive_profile_locator(
            contract_version=self.contract_version,
            contract_fingerprint=self.contract_fingerprint,
            rule_version=self.rule_version)
        if self.profile_locator != expected_loc:
            _err(t, "profile_locator 与派生定位身份不一致："
                    f"{self.profile_locator!r} != {expected_loc!r}")
        expected = derive_profile_id(
            profile_locator=self.profile_locator, schema_version=self.schema_version,
            entries=self.entries)
        if self.profile_id != expected:
            _err(t, f"profile_id 与派生身份不一致：{self.profile_id!r} != {expected!r}")

    def is_profile(self) -> bool:
        """类型标记：这不是 Contract，也不得被当作 Contract 读取。"""
        return True

    def produces_coverage(self) -> bool:
        """恒为 False：profile 只排序候选，不产生 coverage / authority。"""
        return False

    def to_dict(self) -> dict:
        return {
            "schema_type": "AspectNavigationProfile",
            "profile_locator": self.profile_locator,
            "profile_id": self.profile_id,
            "contract_version": self.contract_version,
            "contract_fingerprint": self.contract_fingerprint,
            "rule_version": self.rule_version,
            "schema_version": self.schema_version,
            "entries": [_json(x) for x in self.entries],
        }

    @classmethod
    def from_dict(cls, d: Any) -> "AspectNavigationProfile":
        t = "AspectNavigationProfile"
        d = _reject_unknown(d, {
            "schema_type", "profile_locator", "profile_id", "contract_version",
            "contract_fingerprint", "rule_version", "schema_version", "entries"}, t)
        _need_enum(d, "schema_type", t, ("AspectNavigationProfile",))
        return cls(
            profile_locator=_need_str(d, "profile_locator", t),
            profile_id=_need_str(d, "profile_id", t),
            contract_version=_need_str(d, "contract_version", t),
            contract_fingerprint=_need_sha256(d, "contract_fingerprint", t),
            rule_version=_need_str(d, "rule_version", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                "PROFILE_SCHEMA_VERSION"),
            entries=_need_children(d, "entries", t, AspectNavigationEntry.from_dict),
        )

    @classmethod
    def create(cls, *, contract_version: str, contract_fingerprint: str,
               entries: tuple[AspectNavigationEntry, ...],
               rule_version: str = V.PROFILE_RULE_VERSION) -> "AspectNavigationProfile":
        loc = derive_profile_locator(
            contract_version=contract_version,
            contract_fingerprint=contract_fingerprint, rule_version=rule_version)
        return cls(
            profile_locator=loc,
            profile_id=derive_profile_id(
                profile_locator=loc, schema_version=V.PROFILE_SCHEMA_VERSION,
                entries=entries),
            contract_version=contract_version,
            contract_fingerprint=contract_fingerprint, rule_version=rule_version,
            schema_version=V.PROFILE_SCHEMA_VERSION, entries=entries,
        )


def derive_profile_locator(*, contract_version: str, contract_fingerprint: str,
                           rule_version: str) -> str:
    """`loc-anp-<sha256[:16]>`：输入**不含** company_id / 页码 / 表号 / 人工答案词。"""
    return locator("anp", {
        "contract_version": contract_version,
        "contract_fingerprint": contract_fingerprint,
        "rule_version": rule_version,
    })


def derive_profile_id(*, profile_locator: str, schema_version: str,
                      entries: tuple) -> str:
    """`anp-<sha256[:16]>`：不可变 revision 身份（含 schema version 与全部条目）。"""
    return identity("anp", {
        "profile_locator": profile_locator,
        "schema_version": schema_version,
        "entries": [_json(e) for e in entries],
    })


# ---------------------------------------------------------------------------
# 5. 对齐：确定性重算 + TextAlignmentRecord
# ---------------------------------------------------------------------------

def compute_alignment_coverage(block_char_length: int,
                               char_map: tuple[tuple[int, ...], ...]) -> float:
    """**确定性重算** coverage：已映射的块字符数 / 块字符总长度。

    - `block_char_length == 0` ⇒ `0.0`（零长度块不得产生任何 coverage）；
    - 只统计 `char_map` 实际覆盖的块字符区间，因此"自报 `coverage=1.0` 但 char_map
      只覆盖极少字符"会被 `TextAlignmentRecord` 的重算比对直接拒绝。

    调用方提交的 `coverage` 必须与返回值**逐位相等**（量化到 `FLOAT_PRECISION`）。
    """
    if not isinstance(block_char_length, int) or isinstance(block_char_length, bool) \
            or block_char_length <= 0:
        return 0.0
    matched = 0
    for seg in char_map:
        matched += int(seg[1]) - int(seg[0])
    if matched <= 0:
        return 0.0
    return quantize(min(matched, block_char_length) / block_char_length)


def validate_alignment_partition(*, block_char_length: int, char_map: tuple,
                                 residue: tuple, matched_chars: int | None,
                                 where: str = "") -> int:
    """`char_map` / `residue` 的**公共分区不变量校验器**（TS3 §七 / P1-E）。

    这是对齐终态**唯一**的分区权威：`TextAlignmentRecord` 与
    `AlignmentRefusalRecord` 的 `__post_init__`（以及 `from_dict` 经构造路径）、
    正式写路径 `aligner.align_block` 全部调用它。校验规则（逐条 fail-closed）：

    1. `block_char_length` 必须为非负 `int`；
    2. `char_map` 每段必须满足 `0 <= start < end <= block_char_length`，且**内部升序、
       不重叠**；
    3. `residue` 每段同上（并要求残差分类属于 `RESIDUE_CLASS_SEVERITY`）；
    4. `char_map` 与 `residue` **彼此不重叠**；
    5. 两者**并集恰好覆盖** `[0, block_char_length)`：不允许首部 / 中部 / 尾部空洞，
       不允许重复覆盖（重复覆盖会让 `matched_chars` 虚高而不被发现）；
    6. `matched_chars`（若给出）必须**精确等于** `char_map` 的段长之和。

    返回值：由 `char_map` 重算出的匹配字符数（调用方**必须**用它，不得自报）。

    为什么必须是公共校验器而不是私有写路径检查：只要闭合性只在 `aligner` 的写路径
    上验证，**持久化对象反序列化本身**就不 fail-closed —— 一份手工构造或损坏的
    载荷可以带着"有空洞 / 交叉重叠 / `matched_chars` 虚高"的分区被读回并当作合法
    终态使用。`apv-1` 起两个记录类型共用本函数，构造即校验、读回即校验。
    """
    scope = f"{where}：" if where else ""
    if not isinstance(block_char_length, int) or isinstance(block_char_length, bool) \
            or block_char_length < 0:
        raise SchemaValidationError(
            f"{scope}block_char_length 必须为非负 int，得到 {block_char_length!r}")

    def _segments(value, name, width):
        if not isinstance(value, tuple):
            raise SchemaValidationError(f"{scope}{name} 必须为元组，得到 {type(value).__name__}")
        out = []
        for index, seg in enumerate(value):
            if not isinstance(seg, tuple) or len(seg) != width:
                raise SchemaValidationError(
                    f"{scope}{name}[{index}] 形状不合法（期望 {width} 元组）：{seg!r}")
            start, end = seg[0], seg[1]
            if not isinstance(start, int) or isinstance(start, bool) \
                    or not isinstance(end, int) or isinstance(end, bool):
                raise SchemaValidationError(
                    f"{scope}{name}[{index}] 的区间端点必须为 int：{seg!r}")
            if start < 0 or end <= start:
                raise SchemaValidationError(
                    f"{scope}{name}[{index}] 必须满足 0 <= start < end：{(start, end)!r}")
            if end > block_char_length:
                raise SchemaValidationError(
                    f"{scope}{name}[{index}] 越出块长度 {block_char_length}：{(start, end)!r}")
            out.append((start, end, index))
        ordered = sorted(out)
        for previous, current in zip(ordered, ordered[1:]):
            if current[0] < previous[1]:
                raise SchemaValidationError(
                    f"{scope}{name} 必须升序且不重叠：第 {previous[2]} 段 "
                    f"{(previous[0], previous[1])} 与第 {current[2]} 段 "
                    f"{(current[0], current[1])} 重叠")
        return ordered

    char_segments = _segments(char_map, "char_map", 6)
    residue_segments = _segments(residue, "residue", 3)
    for index, seg in enumerate(residue):
        if seg[2] not in RESIDUE_CLASS_SEVERITY:
            raise SchemaValidationError(
                f"{scope}residue[{index}] 的分类必须属于 {RESIDUE_CLASS_SEVERITY}，"
                f"得到 {seg[2]!r}")

    # ---- 交叉不重叠 + 并集恰好覆盖 [0, block_char_length) ----------------------
    combined = sorted([(a, b, "char_map") for a, b, _ in char_segments]
                      + [(a, b, "residue") for a, b, _ in residue_segments])
    cursor = 0
    for start, end, owner in combined:
        if start > cursor:
            raise SchemaValidationError(
                f"{scope}分区存在空洞：[{cursor}, {start}) 既不在 char_map 也不在 "
                f"residue 中（下一个区间来自 {owner}）")
        if start < cursor:
            raise SchemaValidationError(
                f"{scope}char_map 与 residue（或同侧重复覆盖）彼此重叠：区间 "
                f"({start}, {end}) 来自 {owner}，与已覆盖到 {cursor} 的前驱重叠")
        cursor = end
    if cursor != block_char_length:
        raise SchemaValidationError(
            f"{scope}分区未覆盖到块尾：[{cursor}, {block_char_length}) 是空洞；"
            f"char_map ∪ residue 必须恰好覆盖 [0, {block_char_length})")

    recomputed = sum(end - start for start, end, _ in char_segments)
    if matched_chars is not None:
        if not isinstance(matched_chars, int) or isinstance(matched_chars, bool) \
                or matched_chars < 0:
            raise SchemaValidationError(
                f"{scope}matched_chars 必须为非负 int，得到 {matched_chars!r}")
        if matched_chars != recomputed:
            raise SchemaValidationError(
                f"{scope}matched_chars 必须精确等于 char_map 段长之和 {recomputed}"
                f"（提交 {matched_chars}）；重复覆盖或自报分子一律拒绝")
    return recomputed


def compute_alignment_residue_class(
        residue: tuple[tuple[int, int, str], ...]) -> str | None:
    """记录级残差分类 = 各段分类中**严重度最高**者（`RESIDUE_CLASS_SEVERITY` 升序）。"""
    if not residue:
        return None
    worst = 0
    for seg in residue:
        worst = max(worst, RESIDUE_CLASS_SEVERITY.index(seg[2]))
    return RESIDUE_CLASS_SEVERITY[worst]


def compute_alignment_verdict(coverage: float, residue_class: str | None,
                              align_min: float | None) -> str:
    """**确定性重算** verdict（调用方提交的 verdict 必须与之相等）。

    生产真值表（TS2 最终关闭轮，用户 + Codex 于 2026-09-17 批准，分支顺序即优先级）：

    | 条件 | verdict | 可引用 |
    |---|---|---|
    | `coverage <= 0` | `unaligned` | 否 |
    | `align_min is None`（阈值未裁决） | `partially_aligned` | 否 |
    | `coverage < align_min` | `unaligned` | 否 |
    | `coverage >= align_min` 且 `residue_class == "unexplained"` | `partially_aligned` | 否 |
    | `coverage >= align_min` 且无 `unexplained` | `aligned` | **是（唯一）** |

    批准的业务规则（不得改写、不得再选择）：

    1. **低于阈值一律 `unaligned`**：`0.899999 < 0.90` ⇒ `unaligned`；
       `0.90 >= 0.90` ⇒ 进入下一档。低于阈值的块**不可引用**。
    2. **达到阈值但含 `unexplained` ⇒ `partially_aligned`**：即使 coverage 很高
       也不可引用 —— "达到阈值"只说明覆盖率够，不说明残差已被解释。
    3. **达到阈值且无 `unexplained` ⇒ `aligned`**：唯一的可引用状态。
    4. 生产常量 `V.ALIGN_MIN` 已冻结为 `0.90`；`align_min is None` 这一档只作为
       通用函数的**兼容档**保留（阈值未裁决时**永不产出 `aligned`**：`aligned`
       的定义就是"已通过阈值认证"，阈值不存在时该断言不可满足）。调用方**不得**
       借 `align_min=None` 或自报 verdict 绕过重算。
    5. 不设正文页 / 表格页两套阈值；表格结构由未来的 `TableObject` 路径承接。

    本函数**只**接受调用方给的 `(coverage, residue_class)` 事实，verdict 永远由
    上述分支**重算**得出，因此"自报 aligned"不可能通过 `__post_init__` 的比对。

    `coverage` / `align_min` 必须是有限实数：NaN 与任何比较的关系都是 False，
    若不显式拒绝，NaN 会穿过全部比较分支落进兜底档，从而把"数值非法"伪装成
    "对齐程度不足"。
    """
    if not is_finite(coverage):
        raise SchemaValidationError(
            f"coverage 必须为有限实数（不得 NaN/±Inf），得到 {coverage!r}")
    if align_min is not None and not is_finite(align_min):
        raise SchemaValidationError(
            f"align_min 必须为有限实数或 None，得到 {align_min!r}")
    if coverage <= 0:
        return "unaligned"
    if align_min is None:
        return "partially_aligned"
    if coverage < align_min:
        return "unaligned"
    if residue_class == "unexplained":
        return "partially_aligned"
    return "aligned"


def compute_alignment_verdict_exact(matched_chars: int, block_char_length: int,
                                    residue_class: str | None,
                                    align_min: float | None) -> str:
    """按**精确整数比值** `matched_chars / block_char_length` 重算 verdict。

    与 `compute_alignment_verdict` 的批准真值表**逐分支一致**（TS3 §五），区别只在
    "覆盖率"是什么：本函数用精确有理数 `Fraction(matched_chars, block_char_length)`
    与 `Fraction(Decimal(repr(align_min)))` 比较，**不做任何量化 / 四舍五入**。

    为什么必须有一条精确路径：`als-1` 记录只能携带"已量化到 `FLOAT_PRECISION` 位"
    的 `coverage`，于是存在 `exact < ALIGN_MIN <= quantize(exact)` 的窄区间——此时
    "先四舍五入再判阈值"会把一个**低于冻结阈值、按批准规则不可引用**的块判成
    `aligned`。`als-2` 起记录携带精确分子 `matched_chars` 与分母
    `block_char_length`，阈值判定改由本函数完成，该分歧在记录层不再可能发生。

    `align_min is None`（阈值未裁决）这一兼容档与 `compute_alignment_verdict`
    完全同义：永不产出 `aligned`。
    """
    if not isinstance(block_char_length, int) or isinstance(block_char_length, bool) \
            or block_char_length < 0:
        raise SchemaValidationError(
            f"block_char_length 必须为非负 int，得到 {block_char_length!r}")
    if not isinstance(matched_chars, int) or isinstance(matched_chars, bool) \
            or matched_chars < 0:
        raise SchemaValidationError(
            f"matched_chars 必须为非负 int，得到 {matched_chars!r}")
    if matched_chars > block_char_length:
        raise SchemaValidationError(
            f"matched_chars 不得大于块长度（{matched_chars} > {block_char_length}）")
    if align_min is not None and not is_finite(align_min):
        raise SchemaValidationError(
            f"align_min 必须为有限实数或 None，得到 {align_min!r}")
    if matched_chars <= 0 or block_char_length <= 0:
        return "unaligned"
    if align_min is None:
        return "partially_aligned"
    # 精确比较：分子 / 分母都是整数，阈值取 `repr()` 的十进制字面量（不是二进制
    # 浮点近似），因此 `0.899999 < 0.90`、`0.90 >= 0.90` 与批准原文逐字一致。
    if Fraction(matched_chars, block_char_length) < Fraction(Decimal(repr(align_min))):
        return "unaligned"
    if residue_class == "unexplained":
        return "partially_aligned"
    return "aligned"


@dataclass(frozen=True)
class TextAlignmentRecord:
    """PageLayout 原文与存量 Evidence 规范化文本之间的**版本化可验证对齐**（TS3 构建）。

    - **稳定定位身份** `alignment_locator`：`loc-al-<h(page_layout_id,
      evidence_set_version, ALIGNER_VERSION, page_number, block_index)>`。
      TS2 最终关闭轮把 `ALIGNER_VERSION` 升到 `al-2`，因此同一 `(PageLayout,
      evidence_set_version, page_number, block_index)` 的 alignment 身份**必然**
      与 `al-1` 不同（PageLayout 身份本身不含 `ALIGNER_VERSION`，不受影响）。
    - **不可变 revision 身份** `alignment_id`：在 locator 之上绑定 schema version、
      Evidence 块身份、块长度、verdict / coverage / 残差分类 / 残差 / char_map
      （P1-1、P1-4）。

    判据是"顺序覆盖率 + 残差分类"（计划 §3.2），不是文本相等（实测相等会失败
    93–97%）。`coverage` / `residue_class` / `verdict` 三者都由 char_map 与残差
    **确定性重算**并逐一比对，调用方不能自报成功；因此**不存在**"调用方自报
    verdict 绕过 `is_citable()`"的路径。

    **TS3 §五 起的版本化 successor**（`versions.ALIGN_EXACT_RATIO_VERSIONS`
    列出的版本 = 携带精确分子；更早的已登记旧版本 = 只读兼容）：

    - 当前写路径版本（`versions.ALIGN_SCHEMA_VERSION`）新增 `matched_chars`
      （精确分子，与 `block_char_length` 构成精确整数比值），`verdict` 由
      `compute_alignment_verdict_exact` 按**未量化**比值重算；`coverage` 降级为
      **展示值**（仍必须等于 `compute_alignment_coverage` 的量化结果）。
      这样 `exact < ALIGN_MIN <= quantize(exact)` 的块不会再被"先四舍五入再判阈值"
      升格为可引用。
    - 不含精确分子的旧版本（**只读兼容**，已登记于
      `versions.legacy_versions("ALIGN_SCHEMA_VERSION")`）没有 `matched_chars`：
      读入后 `verdict` 仍按历史语义——由**量化后**的 `coverage` 重算——因此旧载荷的
      含义**一字不变**，既不会被静默按新语义解释，也不会被当成未知版本。
    """

    alignment_locator: str
    alignment_id: str
    page_layout_id: str
    evidence_set_version: str
    schema_version: str
    aligner_version: str
    page_number: int
    block_index: int
    evidence_block_id: str
    block_char_length: int
    verdict: str
    coverage: float
    residue_class: str | None
    residue: tuple[tuple[int, int, str], ...]
    char_map: tuple[tuple[int, ...], ...]
    #: `als-2` 起的**精确分子**：`matched_chars / block_char_length` 是未量化的
    #: 覆盖率，阈值判定必须用它。`als-1` 载荷没有这个字段（只读兼容时为 None）。
    matched_chars: int | None = None

    def __post_init__(self) -> None:
        t = "TextAlignmentRecord"
        for name in ("alignment_locator", "alignment_id", "page_layout_id",
                     "evidence_set_version", "evidence_block_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_version(t, "aligner_version", self.aligner_version, V.ALIGNER_VERSION,
                       "ALIGNER_VERSION")
        if self.schema_version not in (
                V.ALIGN_SCHEMA_VERSION,) + V.legacy_versions("ALIGN_SCHEMA_VERSION"):
            _err(t, f"schema_version 必须为当前版本 {V.ALIGN_SCHEMA_VERSION!r} 或只读"
                    f"兼容的旧版本 {V.legacy_versions('ALIGN_SCHEMA_VERSION')}，"
                    f"得到 {self.schema_version!r}")
        for name in ("page_number", "block_index", "block_char_length"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool):
                _err(t, f"{name} 必须为 int，得到 {v!r}")
        if self.page_number < 1:
            _err(t, f"page_number 必须从 1 开始，得到 {self.page_number}")
        if self.block_index < 0:
            _err(t, f"block_index 不得小于 0，得到 {self.block_index}")
        if self.block_char_length < 0:
            _err(t, f"block_char_length 不得小于 0，得到 {self.block_char_length}")
        if not is_finite(self.coverage):
            _err(t, f"coverage 必须为有限实数（不得 NaN/±Inf），得到 {self.coverage!r}")
        object.__setattr__(self, "coverage", quantize(self.coverage))
        if not (0.0 <= self.coverage <= 1.0):
            _err(t, f"coverage 必须在 [0,1]，得到 {self.coverage}")
        if self.verdict not in ALIGNMENT_VERDICTS:
            _err(t, f"verdict 必须属于 {ALIGNMENT_VERDICTS}，得到 {self.verdict!r}")

        if not isinstance(self.residue, tuple):
            _err(t, "residue 必须为元组")
        prev_end = -1
        for i, seg in enumerate(self.residue):
            if not isinstance(seg, tuple) or len(seg) != 3:
                _err(t, f"residue[{i}] 必须为 (start, end, residue_class)")
            a, b, cls = seg
            if not isinstance(a, int) or not isinstance(b, int):
                _err(t, f"residue[{i}] 的区间元素必须为 int")
            if a < 0 or b <= a:
                _err(t, f"residue[{i}] 必须满足 0 <= start < end，得到 {(a, b)}")
            if b > self.block_char_length:
                _err(t, f"residue[{i}] 越出块长度 {self.block_char_length}")
            if cls not in RESIDUE_CLASS_SEVERITY:
                _err(t, f"residue[{i}] 的分类必须属于 {RESIDUE_CLASS_SEVERITY}，"
                        f"得到 {cls!r}")
            if a < prev_end:
                _err(t, f"residue 必须升序且不重叠，{i} 处得到 {(a, b)}")
            prev_end = max(prev_end, b)
        if self.residue and self.residue_class is None:
            _err(t, "residue 非空时 residue_class 不得为空")
        if not self.residue and self.residue_class is not None:
            _err(t, "residue 为空时 residue_class 必须为 None")
        if self.residue_class is not None \
                and self.residue_class not in RESIDUE_CLASS_SEVERITY:
            _err(t, f"residue_class 必须属于 {RESIDUE_CLASS_SEVERITY}，"
                    f"得到 {self.residue_class!r}")

        if not isinstance(self.char_map, tuple):
            _err(t, "char_map 必须为元组")
        prev_end = -1
        for i, seg in enumerate(self.char_map):
            if not isinstance(seg, tuple) or len(seg) != 6:
                _err(t, f"char_map[{i}] 必须为 (block_start, block_end, page, line, "
                        f"span, char_offset)")
            if any(not isinstance(x, int) or isinstance(x, bool) for x in seg):
                _err(t, f"char_map[{i}] 元素必须为 int，得到 {seg}")
            if seg[0] < 0 or seg[1] <= seg[0]:
                _err(t, f"char_map[{i}] 必须满足 0 <= start < end，得到 {seg}")
            if seg[1] > self.block_char_length:
                _err(t, f"char_map[{i}] 越出块长度 {self.block_char_length}")
            if seg[0] < prev_end:
                _err(t, f"char_map 必须升序且不重叠，{i} 处得到 {seg}")
            if seg[2] != self.page_number:
                _err(t, f"char_map[{i}] 的页码必须等于记录页码 {self.page_number}")
            if seg[3] < 0 or seg[4] < 0 or seg[5] < 0:
                _err(t, f"char_map[{i}] 的 line/span/char_offset 不得为负: {seg}")
            prev_end = max(prev_end, seg[1])

        # ---- P1-E：公共分区不变量（构造与反序列化**本身** fail-closed） --------
        expected_matched = validate_alignment_partition(
            block_char_length=self.block_char_length, char_map=self.char_map,
            residue=self.residue, matched_chars=self.matched_chars,
            where=f"{t}[{self.page_number}/{self.block_index}]")

        # ---- 确定性重算（P1-4）：调用方自报值必须与重算值完全一致 ----
        expected_coverage = compute_alignment_coverage(self.block_char_length,
                                                       self.char_map)
        if self.coverage != expected_coverage:
            _err(t, f"coverage 必须由 char_map 与 block_char_length 确定性重算得到 "
                    f"{expected_coverage}（提交 {self.coverage}）；不得自报覆盖率")
        expected_class = compute_alignment_residue_class(self.residue)
        if self.residue_class != expected_class:
            _err(t, f"residue_class 必须为重算结果 {expected_class!r}"
                    f"（提交 {self.residue_class!r}）")
        if self.schema_version in V.ALIGN_EXACT_RATIO_VERSIONS:
            # `als-2` 起的版本：精确分子必须存在、必须由 char_map 重算得到，且 verdict
            # 必须由**未量化**的精确比值重算 —— 这是"不得先四舍五入再决定 ALIGN_MIN"
            # 的构造期强制。
            if not isinstance(self.matched_chars, int) \
                    or isinstance(self.matched_chars, bool) \
                    or self.matched_chars < 0:
                _err(t, f"schema_version={self.schema_version!r} 必须携带精确分子 "
                        f"matched_chars（非负 int），得到 {self.matched_chars!r}")
            # `matched_chars == expected_matched` 已由 `validate_alignment_partition`
            # （唯一权威）在构造 / 反序列化路径上强制，这里不再重复一套判定。
            expected_verdict = compute_alignment_verdict_exact(
                self.matched_chars, self.block_char_length, self.residue_class,
                V.ALIGN_MIN)
        else:
            # `als-1`（只读兼容）：不得携带 `als-2` 才有的精确分子，verdict 按历史
            # 语义（量化 coverage）重算 —— 旧载荷的含义一字不变。
            if self.matched_chars is not None:
                _err(t, f"schema_version={self.schema_version!r} 不得携带 "
                        f"matched_chars（该字段自 {V.ALIGN_SCHEMA_VERSION!r} 起才存在）")
            expected_verdict = compute_alignment_verdict(
                self.coverage, self.residue_class, V.ALIGN_MIN)
        if self.verdict != expected_verdict:
            _err(t, f"verdict 必须为重算结果 {expected_verdict!r}"
                    f"（提交 {self.verdict!r}）")
        if self.block_char_length == 0 and self.verdict != "unaligned":
            _err(t, "block_char_length=0 不得产生 aligned/partially_aligned")

        expected_loc = derive_alignment_locator(
            page_layout_id=self.page_layout_id,
            evidence_set_version=self.evidence_set_version,
            aligner_version=self.aligner_version, page_number=self.page_number,
            block_index=self.block_index)
        if self.alignment_locator != expected_loc:
            _err(t, "alignment_locator 与派生定位身份不一致："
                    f"{self.alignment_locator!r} != {expected_loc!r}")
        expected = derive_alignment_id(
            alignment_locator=self.alignment_locator, schema_version=self.schema_version,
            evidence_block_id=self.evidence_block_id,
            block_char_length=self.block_char_length, verdict=self.verdict,
            coverage=self.coverage, residue_class=self.residue_class,
            residue=self.residue, char_map=self.char_map,
            matched_chars=self.matched_chars)
        if self.alignment_id != expected:
            _err(t, f"alignment_id 与派生身份不一致：{self.alignment_id!r} != {expected!r}")

    def is_citable(self) -> bool:
        """是否可被下游当作可引证材料。

        唯一充要条件：`verdict == "aligned"`，即"coverage 达到 `ALIGN_MIN` **且**
        不含 `unexplained` 残差"（TS2 最终关闭轮批准）。

        这意味着：`unaligned`（含 coverage 低于阈值者）不可引证；
        `partially_aligned`（含"达到阈值但仍有 unexplained"）不可引证。

        保留 `ALIGN_MIN is None ⇒ False` 这一层 fail-closed：生产常量已冻结为
        `0.90`，该分支不再触发，但通用/未来配置下"阈值未裁决 ⇒ 全部不可引证"
        的语义不变。

        因为 `__post_init__` 已强制 `verdict` 等于确定性重算结果（携带精确分子的版本
        用精确比值、已登记的旧版本用历史量化语义，分支读
        `V.ALIGN_EXACT_RATIO_VERSIONS`），本方法**不可能**被调用方自报的 `verdict`
        绕过。
        """
        if V.ALIGN_MIN is None:
            return False
        return self.verdict == "aligned"

    def exact_ratio(self) -> tuple[int, int] | None:
        """精确覆盖率的有理数表示 `(分子, 分母)`；`als-1` 载荷**没有**精确分子，
        返回 `None`（这正是它需要只读兼容、而不是被当成当前版本的原因）。"""
        if self.matched_chars is None:
            return None
        return (self.matched_chars, self.block_char_length)

    def to_dict(self) -> dict:
        d = {
            "schema_type": "TextAlignmentRecord",
            "alignment_locator": self.alignment_locator,
            "alignment_id": self.alignment_id,
            "page_layout_id": self.page_layout_id,
            "evidence_set_version": self.evidence_set_version,
            "schema_version": self.schema_version,
            "aligner_version": self.aligner_version,
            "page_number": self.page_number,
            "block_index": self.block_index,
            "evidence_block_id": self.evidence_block_id,
            "block_char_length": self.block_char_length,
            "verdict": self.verdict,
            "coverage": self.coverage,
            "residue_class": self.residue_class,
            "residue": _json(self.residue),
            "char_map": _json(self.char_map),
        }
        # 追加式：只有携带精确分子的版本（`V.ALIGN_EXACT_RATIO_VERSIONS`）才写
        # `matched_chars`；不含该字段的旧载荷的 wire 形态**逐字段不变**
        # （读入再写出仍与原文一致，不静默升级历史载荷）。
        if self.matched_chars is not None:
            d["matched_chars"] = self.matched_chars
        return d

    @classmethod
    def from_dict(cls, d: Any) -> "TextAlignmentRecord":
        t = "TextAlignmentRecord"
        d = _reject_unknown(d, {
            "schema_type", "alignment_locator", "alignment_id", "page_layout_id",
            "evidence_set_version", "schema_version", "aligner_version", "page_number",
            "block_index", "evidence_block_id", "block_char_length", "verdict",
            "coverage", "residue_class", "residue", "char_map", "matched_chars"}, t)
        _need_enum(d, "schema_type", t, ("TextAlignmentRecord",))
        block_len = _need_int(d, "block_char_length", t, lo=0)
        page_number = _need_int(d, "page_number", t, lo=1)
        return cls(
            alignment_locator=_need_str(d, "alignment_locator", t),
            alignment_id=_need_str(d, "alignment_id", t),
            page_layout_id=_need_str(d, "page_layout_id", t),
            evidence_set_version=_need_str(d, "evidence_set_version", t),
            schema_version=_need_alignment_schema_version(d, "schema_version", t),
            aligner_version=_need_str(d, "aligner_version", t),
            page_number=page_number,
            block_index=_need_int(d, "block_index", t, lo=0),
            evidence_block_id=_need_str(d, "evidence_block_id", t),
            block_char_length=block_len,
            verdict=_need_enum(d, "verdict", t, ALIGNMENT_VERDICTS),
            coverage=_need_num(d, "coverage", t, lo=0.0, hi=1.0),
            residue_class=_need_str(d, "residue_class", t, none_ok=True),
            residue=_need_residue(d, "residue", t, block_len),
            char_map=_need_char_map(d, "char_map", t, block_len, page_number),
            matched_chars=_need_int(d, "matched_chars", t, lo=0, none_ok=True),
        )

    @classmethod
    def create(cls, *, page_layout_id: str, evidence_set_version: str, page_number: int,
               block_index: int, evidence_block_id: str, block_char_length: int,
               residue: tuple[tuple[int, int, str], ...] = (),
               char_map: tuple[tuple[int, ...], ...] = (),
               aligner_version: str = V.ALIGNER_VERSION) -> "TextAlignmentRecord":
        """按重算结果构造（写出 `V.ALIGN_SCHEMA_VERSION` = 当前 wire 版本）：
        `coverage` / `residue_class` / `matched_chars` / `verdict` 全部由本方法推导。

        调用方**无法**提交自定义 verdict / coverage —— 这正是 P1-4 要求的
        "不能自报成功"。阈值 `V.ALIGN_MIN` 已冻结为单一文本对齐阈值 `0.90`
        （用户 + Codex，2026-09-17），且自携带精确分子的版本起由**精确整数比值**
        `matched_chars / block_char_length` 判定：只有该比值 `>= 0.90` 且无
        `unexplained` 残差才重算为 `aligned`；`align_min is None` 这一兼容档下
        永不产出 `aligned`。`coverage` 只是展示值（量化到 `FLOAT_PRECISION`）。
        """
        matched_chars = sum(int(seg[1]) - int(seg[0]) for seg in char_map)
        coverage = compute_alignment_coverage(block_char_length, char_map)
        residue_class = compute_alignment_residue_class(residue)
        verdict = compute_alignment_verdict_exact(
            matched_chars, block_char_length, residue_class, V.ALIGN_MIN)
        loc = derive_alignment_locator(
            page_layout_id=page_layout_id, evidence_set_version=evidence_set_version,
            aligner_version=aligner_version, page_number=page_number,
            block_index=block_index)
        return cls(
            alignment_locator=loc,
            alignment_id=derive_alignment_id(
                alignment_locator=loc, schema_version=V.ALIGN_SCHEMA_VERSION,
                evidence_block_id=evidence_block_id,
                block_char_length=block_char_length, verdict=verdict,
                coverage=coverage, residue_class=residue_class, residue=residue,
                char_map=char_map, matched_chars=matched_chars),
            page_layout_id=page_layout_id, evidence_set_version=evidence_set_version,
            schema_version=V.ALIGN_SCHEMA_VERSION, aligner_version=aligner_version,
            page_number=page_number, block_index=block_index,
            evidence_block_id=evidence_block_id, block_char_length=block_char_length,
            verdict=verdict, coverage=coverage, residue_class=residue_class,
            residue=residue, char_map=char_map, matched_chars=matched_chars,
        )


def derive_alignment_locator(*, page_layout_id: str, evidence_set_version: str,
                             aligner_version: str, page_number: int,
                             block_index: int) -> str:
    """`loc-al-<sha256[:16]>`：以 (页, 块) 为单位，与 run_id / 时间无关。"""
    return locator("al", {
        "page_layout_id": page_layout_id,
        "evidence_set_version": evidence_set_version,
        "aligner_version": aligner_version,
        "page_number": page_number,
        "block_index": block_index,
    })


def derive_alignment_id(*, alignment_locator: str, schema_version: str,
                        evidence_block_id: str, block_char_length: int, verdict: str,
                        coverage: float, residue_class: str | None, residue: tuple,
                        char_map: tuple, matched_chars: int | None = None) -> str:
    """`al-<sha256[:16]>`：不可变 revision 身份（绑定源 Evidence、版本与全部对齐内容）。

    `matched_chars`（精确分子）自 `als-2` 起进入身份，因此"同一展示覆盖率、不同精确
    比值"的两个终态不可能共享 `alignment_id`。它**只在存在时**进入哈希输入：`als-1`
    载荷的 `alignment_id` 因此**逐位不变**（追加式 schema successor 不得静默改写历史
    载荷的身份）。
    """
    payload = {
        "alignment_locator": alignment_locator,
        "schema_version": schema_version,
        "evidence_block_id": evidence_block_id,
        "block_char_length": block_char_length,
        "verdict": verdict,
        "coverage": coverage,
        "residue_class": residue_class,
        "residue": _json(residue),
        "char_map": _json(char_map),
    }
    if matched_chars is not None:
        payload["matched_chars"] = matched_chars
    return identity("al", payload)


def derive_alignment_refusal_locator(*, page_layout_id: str, evidence_set_version: str,
                                     aligner_version: str, page_number: int,
                                     block_index: int) -> str:
    """拒绝终态与对齐记录**共享同一个定位槽位** `loc-al-…`。

    拒绝不是"另一个对象"，而是"同一 `(版式, evidence set, 页, 块)` 上的另一个终态"：
    若给拒绝单独一套 locator，"某个槽位有记录还是有拒绝"就要靠跨文件比对才能回答，
    而 §五 要求的恰恰是"每个 block 恰好一个正式终态、可逐条对账"。
    """
    return derive_alignment_locator(
        page_layout_id=page_layout_id, evidence_set_version=evidence_set_version,
        aligner_version=aligner_version, page_number=page_number,
        block_index=block_index)


def derive_alignment_refusal_id(*, alignment_locator: str, schema_version: str,
                                evidence_block_id: str, block_char_length: int,
                                matched_chars: int, coverage: float,
                                residue_class: str | None, residue: tuple,
                                char_map: tuple, refusal_reason: str) -> str:
    """`alr-<sha256[:16]>`：拒绝终态的不可变修订身份（绑定精确分子、版本与原因）。"""
    return identity("alr", {
        "alignment_locator": alignment_locator,
        "schema_version": schema_version,
        "evidence_block_id": evidence_block_id,
        "block_char_length": block_char_length,
        "matched_chars": matched_chars,
        "coverage": coverage,
        "residue_class": residue_class,
        "residue": _json(residue),
        "char_map": _json(char_map),
        "refusal_reason": refusal_reason,
    })


@dataclass(frozen=True)
class AlignmentRefusalRecord:
    """一个 `EvidenceBlock` 的**正式拒绝终态**（TS3 §五）。

    当前 wire 版本由 `versions.ALIGN_REFUSAL_SCHEMA_VERSION` 给出（本处不写版本
    字面量）；不含分区闭合不变量校验的初版登记在 `versions.LEGACY_SCHEMA_VERSIONS`。

    为什么拒绝也必须是一个 typed 对象：`normalization_alignment.json` 必须让"该文档
    全部 Evidence block 的正式对齐终态"**守恒**——769 个 block 就是 769 条终态，
    不接受"少一条记录 + 一句 wrapper 文案"。拒绝因此不能表示为"记录缺失"，而要表示
    为一个带完整身份、原因码与精确数值的正式对象。

    它**不是**可引用材料的降级版本：`is_citable()` 恒为 `False`，且它不携带
    `verdict` 之外的任何"看起来像已对齐"的字段。它比 `TextAlignmentRecord` 多的
    恰恰是**为什么不能产出记录**：

    - `matched_chars` / `block_char_length`：精确分子与分母（判定依据是精确比值，
      不是展示值）；
    - `exact_coverage`：未量化的比值；
    - `coverage`：会被写进报告的**展示值**（量化到 `FLOAT_PRECISION`）；
    - `refusal_reason`：封闭原因码，见 `ALIGNMENT_REFUSAL_REASONS`。

    构造期不变量（全部 fail-closed，调用方**无法**伪造一个"其实不该拒绝"的拒绝）：

    1. `matched_chars` / `coverage` / `residue_class` 必须由 `char_map` / `residue`
       确定性重算得到；`exact_coverage` 必须恰等于 `matched_chars / block_char_length`；
    2. `refusal_reason` 必须属于封闭集合；
    3. 原因必须**真的成立**：`quantization_boundary_refused` 要求"精确比值低于
       `ALIGN_MIN`，而量化展示值不低于 `ALIGN_MIN`"，即该块在两种判据下结论相反。
       因此拒绝既不能用来掩盖一个已经完全对齐的块，也不能当成通用的"我不想算"出口。
    """

    alignment_locator: str
    refusal_id: str
    page_layout_id: str
    evidence_set_version: str
    schema_version: str
    aligner_version: str
    page_number: int
    block_index: int
    evidence_block_id: str
    block_char_length: int
    matched_chars: int
    exact_coverage: float
    coverage: float
    residue_class: str | None
    residue: tuple[tuple[int, int, str], ...]
    char_map: tuple[tuple[int, ...], ...]
    refusal_reason: str

    def __post_init__(self) -> None:
        t = "AlignmentRefusalRecord"
        for name in ("alignment_locator", "refusal_id", "page_layout_id",
                     "evidence_set_version", "evidence_block_id"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        _check_version(t, "aligner_version", self.aligner_version, V.ALIGNER_VERSION,
                       "ALIGNER_VERSION")
        _check_version(t, "schema_version", self.schema_version,
                       V.ALIGN_REFUSAL_SCHEMA_VERSION, "ALIGN_REFUSAL_SCHEMA_VERSION")
        for name in ("page_number", "block_index", "block_char_length", "matched_chars"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool):
                _err(t, f"{name} 必须为 int，得到 {v!r}")
        if self.page_number < 1:
            _err(t, f"page_number 必须从 1 开始，得到 {self.page_number}")
        if self.block_index < 0:
            _err(t, f"block_index 不得小于 0，得到 {self.block_index}")
        if self.block_char_length < 0:
            _err(t, f"block_char_length 不得小于 0，得到 {self.block_char_length}")
        if self.refusal_reason not in ALIGNMENT_REFUSAL_REASONS:
            _err(t, f"refusal_reason 必须属于 {ALIGNMENT_REFUSAL_REASONS}，"
                    f"得到 {self.refusal_reason!r}")
        if not isinstance(self.residue, tuple):
            _err(t, "residue 必须为元组")
        for i, seg in enumerate(self.residue):
            if not isinstance(seg, tuple) or len(seg) != 3:
                _err(t, f"residue[{i}] 必须为 (start, end, residue_class)")
            a, b, cls = seg
            if not isinstance(a, int) or not isinstance(b, int):
                _err(t, f"residue[{i}] 的区间元素必须为 int")
            if a < 0 or b <= a or b > self.block_char_length:
                _err(t, f"residue[{i}] 必须满足 0 <= start < end <= 块长度，"
                        f"得到 {(a, b)}")
            if cls not in RESIDUE_CLASS_SEVERITY:
                _err(t, f"residue[{i}] 的分类必须属于 {RESIDUE_CLASS_SEVERITY}，"
                        f"得到 {cls!r}")
        if self.residue and self.residue_class is None:
            _err(t, "residue 非空时 residue_class 不得为空")
        if not self.residue and self.residue_class is not None:
            _err(t, "residue 为空时 residue_class 必须为 None")
        if not isinstance(self.char_map, tuple):
            _err(t, "char_map 必须为元组")
        for i, seg in enumerate(self.char_map):
            if not isinstance(seg, tuple) or len(seg) != 6:
                _err(t, f"char_map[{i}] 必须为 (block_start, block_end, page, line, "
                        f"span, char_offset)")
            if any(not isinstance(x, int) or isinstance(x, bool) for x in seg):
                _err(t, f"char_map[{i}] 元素必须为 int，得到 {seg}")
            if seg[0] < 0 or seg[1] <= seg[0] or seg[1] > self.block_char_length:
                _err(t, f"char_map[{i}] 越界或倒序：{seg}")
            if seg[2] != self.page_number:
                _err(t, f"char_map[{i}] 的页码必须等于记录页码 {self.page_number}")

        # ---- P1-E：**与 `TextAlignmentRecord` 共用同一个公共分区校验器** --------
        # 拒绝记录原先连"residue 自身升序不重叠"都没有校验，更没有交叉重叠与并集
        # 覆盖校验；同一个终态集合里的两个类型用两套松紧不同的不变量，等于没有不变量。
        validate_alignment_partition(
            block_char_length=self.block_char_length, char_map=self.char_map,
            residue=self.residue, matched_chars=self.matched_chars,
            where=f"{t}[{self.page_number}/{self.block_index}]")

        # ---- 确定性重算：拒绝理由必须是**真的**（不得滥用拒绝出口）----
        if self.block_char_length > 0 and self.matched_chars > self.block_char_length:
            _err(t, "matched_chars 不得大于块长度")
        expected_exact = (self.matched_chars / self.block_char_length
                          if self.block_char_length > 0 else 0.0)
        if self.exact_coverage != expected_exact:
            _err(t, f"exact_coverage 必须恰为 matched_chars / block_char_length = "
                    f"{expected_exact!r}（提交 {self.exact_coverage!r}）")
        expected_coverage = compute_alignment_coverage(self.block_char_length,
                                                       self.char_map)
        if self.coverage != expected_coverage:
            _err(t, f"coverage（展示值）必须由 char_map 与 block_char_length 重算得到 "
                    f"{expected_coverage}（提交 {self.coverage}）")
        expected_class = compute_alignment_residue_class(self.residue)
        if self.residue_class != expected_class:
            _err(t, f"residue_class 必须为重算结果 {expected_class!r}"
                    f"（提交 {self.residue_class!r}）")
        if self.refusal_reason == "quantization_boundary_refused":
            if V.ALIGN_MIN is None:
                _err(t, "阈值未裁决时不存在量化边界张力，不得据此拒绝")
            if not (self.exact_coverage < V.ALIGN_MIN <= self.coverage):
                _err(t, f"拒绝理由不成立：量化边界被拒要求 exact_coverage < "
                        f"ALIGN_MIN <= coverage，得到 "
                        f"{self.exact_coverage} / {V.ALIGN_MIN} / {self.coverage}；"
                        f"不得用拒绝掩盖一个不存在张力的块")
        expected_loc = derive_alignment_refusal_locator(
            page_layout_id=self.page_layout_id,
            evidence_set_version=self.evidence_set_version,
            aligner_version=self.aligner_version, page_number=self.page_number,
            block_index=self.block_index)
        if self.alignment_locator != expected_loc:
            _err(t, "alignment_locator 与派生定位身份不一致："
                    f"{self.alignment_locator!r} != {expected_loc!r}")
        expected_id = derive_alignment_refusal_id(
            alignment_locator=self.alignment_locator, schema_version=self.schema_version,
            evidence_block_id=self.evidence_block_id,
            block_char_length=self.block_char_length, matched_chars=self.matched_chars,
            coverage=self.coverage, residue_class=self.residue_class,
            residue=self.residue, char_map=self.char_map,
            refusal_reason=self.refusal_reason)
        if self.refusal_id != expected_id:
            _err(t, f"refusal_id 与派生身份不一致：{self.refusal_id!r} != {expected_id!r}")

    def is_citable(self) -> bool:
        """拒绝终态**恒不可引用**（没有任何配置能让它变成可引用材料）。"""
        return False

    def exact_ratio(self) -> tuple[int, int]:
        """精确覆盖率的有理数表示 `(分子, 分母)`，与 `TextAlignmentRecord` 同形。

        拒绝的**依据**必须能被外部逐字复算：`(matched_chars, block_char_length)`
        就是判定用的那对整数，调用方不必（也不得）从展示用浮点反推。
        """
        return (self.matched_chars, self.block_char_length)

    def to_dict(self) -> dict:
        return {
            "schema_type": "AlignmentRefusalRecord",
            "alignment_locator": self.alignment_locator,
            "refusal_id": self.refusal_id,
            "page_layout_id": self.page_layout_id,
            "evidence_set_version": self.evidence_set_version,
            "schema_version": self.schema_version,
            "aligner_version": self.aligner_version,
            "page_number": self.page_number,
            "block_index": self.block_index,
            "evidence_block_id": self.evidence_block_id,
            "block_char_length": self.block_char_length,
            "matched_chars": self.matched_chars,
            "exact_coverage": self.exact_coverage,
            "coverage": self.coverage,
            "residue_class": self.residue_class,
            "residue": _json(self.residue),
            "char_map": _json(self.char_map),
            "refusal_reason": self.refusal_reason,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "AlignmentRefusalRecord":
        t = "AlignmentRefusalRecord"
        d = _reject_unknown(d, {
            "schema_type", "alignment_locator", "refusal_id", "page_layout_id",
            "evidence_set_version", "schema_version", "aligner_version", "page_number",
            "block_index", "evidence_block_id", "block_char_length", "matched_chars",
            "exact_coverage", "coverage", "residue_class", "residue", "char_map",
            "refusal_reason"}, t)
        _need_enum(d, "schema_type", t, ("AlignmentRefusalRecord",))
        block_len = _need_int(d, "block_char_length", t, lo=0)
        page_number = _need_int(d, "page_number", t, lo=1)
        return cls(
            alignment_locator=_need_str(d, "alignment_locator", t),
            refusal_id=_need_str(d, "refusal_id", t),
            page_layout_id=_need_str(d, "page_layout_id", t),
            evidence_set_version=_need_str(d, "evidence_set_version", t),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                "ALIGN_REFUSAL_SCHEMA_VERSION"),
            aligner_version=_need_str(d, "aligner_version", t),
            page_number=page_number,
            block_index=_need_int(d, "block_index", t, lo=0),
            evidence_block_id=_need_str(d, "evidence_block_id", t),
            block_char_length=block_len,
            matched_chars=_need_int(d, "matched_chars", t, lo=0),
            exact_coverage=_need_num(d, "exact_coverage", t, lo=0.0, hi=1.0),
            coverage=_need_num(d, "coverage", t, lo=0.0, hi=1.0),
            residue_class=_need_str(d, "residue_class", t, none_ok=True),
            residue=_need_residue(d, "residue", t, block_len),
            char_map=_need_char_map(d, "char_map", t, block_len, page_number),
            refusal_reason=_need_enum(d, "refusal_reason", t,
                                      ALIGNMENT_REFUSAL_REASONS),
        )

    @classmethod
    def create(cls, *, page_layout_id: str, evidence_set_version: str, page_number: int,
               block_index: int, evidence_block_id: str, block_char_length: int,
               matched_chars: int, residue: tuple[tuple[int, int, str], ...],
               char_map: tuple[tuple[int, ...], ...], refusal_reason: str,
               aligner_version: str = V.ALIGNER_VERSION) -> "AlignmentRefusalRecord":
        """按重算结果构造拒绝终态：`exact_coverage` / `coverage` / `residue_class`
        / locator / id 全部由本方法推导，调用方只能给"事实"与原因码。

        拒绝理由是否成立由 `__post_init__` 复核（理由不成立即抛错），因此本入口
        不能被用来把一个正常块"拒绝掉"。
        """
        exact = (matched_chars / block_char_length) if block_char_length > 0 else 0.0
        coverage = compute_alignment_coverage(block_char_length, char_map)
        residue_class = compute_alignment_residue_class(residue)
        loc = derive_alignment_refusal_locator(
            page_layout_id=page_layout_id, evidence_set_version=evidence_set_version,
            aligner_version=aligner_version, page_number=page_number,
            block_index=block_index)
        return cls(
            alignment_locator=loc,
            refusal_id=derive_alignment_refusal_id(
                alignment_locator=loc, schema_version=V.ALIGN_REFUSAL_SCHEMA_VERSION,
                evidence_block_id=evidence_block_id,
                block_char_length=block_char_length, matched_chars=matched_chars,
                coverage=coverage, residue_class=residue_class, residue=residue,
                char_map=char_map, refusal_reason=refusal_reason),
            page_layout_id=page_layout_id, evidence_set_version=evidence_set_version,
            schema_version=V.ALIGN_REFUSAL_SCHEMA_VERSION,
            aligner_version=aligner_version, page_number=page_number,
            block_index=block_index, evidence_block_id=evidence_block_id,
            block_char_length=block_char_length, matched_chars=matched_chars,
            exact_coverage=exact, coverage=coverage, residue_class=residue_class,
            residue=residue, char_map=char_map, refusal_reason=refusal_reason,
        )


# ---------------------------------------------------------------------------
# 6. 材料单元：OutlineSpan
# ---------------------------------------------------------------------------

def alignment_closure_errors(span: "OutlineSpan",
                             records: Sequence[TextAlignmentRecord]) -> tuple[str, ...]:
    """`span.alignment_ids` 与调用方提供的 alignment 记录的闭合性差错（空 = 闭合）。

    逐项检查：**缺少**（span 引用但未提供）、**重复**（同一 id 给两次）、
    **多余**（提供了 span 未引用的记录）、**身份不匹配**（记录的 locator 与 span 的
    来源不一致，或记录属于别的 evidence set）。
    """
    errors: list[str] = []
    wanted = list(span.alignment_ids)
    provided = [r.alignment_id for r in records]
    if len(set(provided)) != len(provided):
        errors.append("alignment 记录必须唯一，不得重复提供同一 alignment_id")
    missing = [a for a in wanted if a not in set(provided)]
    if missing:
        errors.append(f"缺少 span 引用的 alignment 记录: {missing}")
    extra = [a for a in provided if a not in set(wanted)]
    if extra:
        errors.append(f"提供了 span 未引用的多余 alignment 记录: {extra}")
    for r in records:
        if r.evidence_set_version != span.evidence_set_version:
            errors.append(f"alignment {r.alignment_id!r} 属于另一个 evidence set"
                          f"（{r.evidence_set_version!r}）")
        if r.aligner_version != V.ALIGNER_VERSION:
            errors.append(f"alignment {r.alignment_id!r} 的 aligner 版本不符")
    eids = {eid for eid, _cs, _ce in span.component_evidence_refs}
    if eids:
        rec_eids = {r.evidence_block_id for r in records}
        uncovered = sorted(eids - rec_eids)
        if uncovered:
            errors.append(f"component Evidence 缺少对应 alignment 记录: {uncovered}")
        foreign = sorted(rec_eids - eids)
        if foreign:
            errors.append(f"alignment 记录引用了非 component Evidence: {foreign}")
    return tuple(errors)


@dataclass(frozen=True)
class OutlineSpan:
    """标题范围内的正文材料单元（**正式本地消费单位**之一，构建于 TS4）。

    - **稳定定位身份** `span_locator`：`loc-os-<h(document_outline_locator,
      evidence_set_version, start_anchor, end_anchor, SPAN_BUILDER_VERSION)>`。
    - **不可变 revision 身份** `span_id`：在 locator 之上绑定 schema version、
      **document id**、document version、归属节点、角色、页/字符范围、行引用、
      component 引用、alignment 引用、fallback / cross-heading / confidence 与
      规范化正文（P1-1）。
      `document_id` 显式进入身份（TS1.1 P1-A）：同一份材料挂在不同 `document_id`
      下必须是两个不同的 span，不能只靠上游 outline locator 间接区分。

    - `node_id is None` **仅**表示显式 `unassigned`（此时 `role` 必须为 `'unassigned'`
      且必须给出 `unassigned_reason`）；
    - 有 component Evidence 时**必须**有版本化 alignment 记录（计划 §4.3）。
    - 上游身份的核对走 `verify_upstream(outline=..., layout=...)` 的**对象级**比对。
    """

    span_locator: str
    span_id: str
    document_outline_locator: str
    node_id: str | None
    document_id: str
    document_version: str
    evidence_set_version: str
    role: str
    schema_version: str
    span_builder_version: str
    unassigned_reason: str | None
    start_anchor: tuple[int, int, tuple]
    end_anchor: tuple[int, int, tuple]
    page_range: tuple[int, int]
    char_range: tuple[int, int]
    layout_line_refs: tuple[tuple[int, int], ...]
    component_evidence_refs: tuple[tuple[str, int, int], ...]
    alignment_ids: tuple[str, ...]
    is_fallback: bool
    fallback_derivation: str | None
    is_cross_heading: bool
    confidence: float
    normalized_text: str
    content_fingerprint: str

    def __post_init__(self) -> None:
        t = "OutlineSpan"
        for name in ("span_locator", "span_id", "document_outline_locator", "document_id",
                     "document_version", "evidence_set_version"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        if self.node_id is not None and (not isinstance(self.node_id, str)
                                         or not self.node_id):
            _err(t, "node_id 必须为非空字符串或 None")
        # TS4 §18.11.2③ / §18.12.2：`OutlineSpan` 的 os-4 是**兼容读层**，必须同时接受
        # 两个已登记的算法版本，且**不得**按 `role` 拒绝历史已归属的 `sb-1`：
        #
        # - `sb-1` 是 TS3 的现行算法（冻结产物里 232 个 unassigned span 全是它），
        #   不是失效 legacy；把历史已归属 `sb-1` 一并拒掉会让旧载荷无法读回。
        # - `TS4_BODY_SPAN_BUILDER_VERSION`（当前 `sb-7`）是 TS4 正文算法；它的历史值
        #   `TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS`（`sb-2` / `sb-3` / `sb-4` / `sb-5` /
        #   `sb-6`）也必须留在兼容读层里，否则历史 `sb-2` / `sb-3` / `sb-4` / `sb-5` /
        #   `sb-6` 的 `OutlineSpan` 载荷无法逐字节读回。
        #
        # 「TS3 unassigned 只能用 sb-1、TS4 正文只能用当前 TS4 版本」这条角色真值表**不在**
        # base wire class 上强制（那会无迁移地改变旧接受集合），而由 TS4 正式
        # builder/verifier 另行强制（见 `span_verifier`）。
        if self.span_builder_version not in SPAN_BUILDER_VERSIONS:
            _check_version(t, "span_builder_version", self.span_builder_version,
                           V.SPAN_BUILDER_VERSION, "SPAN_BUILDER_VERSION")
        _check_version(t, "schema_version", self.schema_version, V.SPAN_SCHEMA_VERSION)
        if self.role not in SPAN_ROLES:
            _err(t, f"role 必须属于 {SPAN_ROLES}，得到 {self.role!r}")
        if (self.node_id is None) != (self.role == "unassigned"):
            _err(t, "node_id=None 当且仅当 role=='unassigned'（显式未归属）")
        if self.role == "unassigned":
            if self.unassigned_reason not in UNASSIGNED_REASONS:
                _err(t, f"显式未归属必须给出登记原因，得到 {self.unassigned_reason!r}"
                        f"（合法值 {UNASSIGNED_REASONS}）")
        elif self.unassigned_reason is not None:
            _err(t, "已归属 span 不得携带 unassigned_reason")
        if not isinstance(self.is_fallback, bool):
            _err(t, "is_fallback 必须为 bool")
        if self.is_fallback and self.fallback_derivation is None:
            _err(t, "is_fallback=True 时 fallback_derivation 不得为空")
        if not self.is_fallback and self.fallback_derivation is not None:
            _err(t, "is_fallback=False 时 fallback_derivation 必须为空")
        if not isinstance(self.is_cross_heading, bool):
            _err(t, "is_cross_heading 必须为 bool")
        if not is_finite(self.confidence):
            _err(t, f"confidence 必须为有限实数（不得 NaN/±Inf），得到 {self.confidence!r}")
        object.__setattr__(self, "confidence", quantize(self.confidence))
        if not (0.0 <= self.confidence <= 1.0):
            _err(t, f"confidence 必须在 [0,1]，得到 {self.confidence}")
        if not isinstance(self.normalized_text, str) or self.normalized_text == "":
            _err(t, "normalized_text 必须为非空字符串")
        if not _RE_SHA256_HEX.match(self.content_fingerprint):
            _err(t, "content_fingerprint 必须为 64 位小写十六进制 sha256")
        expected_cf = derive_span_content_fingerprint(self.normalized_text)
        if self.content_fingerprint != expected_cf:
            _err(t, "content_fingerprint 必须等于 normalized_text 的 sha256"
                    f"（{self.content_fingerprint!r} != {expected_cf!r}）")

        for name, anchor in (("start_anchor", self.start_anchor),
                             ("end_anchor", self.end_anchor)):
            if not isinstance(anchor, tuple) or len(anchor) != 3:
                _err(t, f"{name} 必须为 (page_number, line_index, bbox)")
            if any(not isinstance(x, int) or isinstance(x, bool) for x in anchor[:2]):
                _err(t, f"{name} 的页码/行号必须为 int")
            if anchor[0] < 1:
                _err(t, f"{name} 的页码必须从 1 开始，得到 {anchor[0]}")
            if anchor[1] < 0:
                _err(t, f"{name} 的行号不得为负，得到 {anchor[1]}")
            _check_bbox(anchor[2], f"{t}.{name}")
        if (self.start_anchor[0], self.start_anchor[1]) > (self.end_anchor[0],
                                                           self.end_anchor[1]):
            _err(t, "start_anchor 必须不晚于 end_anchor")
        if not isinstance(self.page_range, tuple) or len(self.page_range) != 2:
            _err(t, "page_range 必须为 (start_page, end_page)")
        if self.page_range[0] != self.start_anchor[0] \
                or self.page_range[1] != self.end_anchor[0]:
            _err(t, "page_range 必须等于 (start_anchor.page, end_anchor.page)")
        if self.page_range[1] < self.page_range[0]:
            _err(t, "page_range 必须满足 end >= start")

        # char_range：正长度，且与 normalized_text 一致（P1-5）。
        if not isinstance(self.char_range, tuple) or len(self.char_range) != 2:
            _err(t, "char_range 必须为 (start, end)")
        if not all(isinstance(x, int) and not isinstance(x, bool)
                   for x in self.char_range):
            _err(t, "char_range 元素必须为 int")
        if self.char_range[0] < 0:
            _err(t, "char_range 起点不得为负")
        if self.char_range[1] <= self.char_range[0]:
            _err(t, f"char_range 必须为正长度（end > start），得到 {self.char_range}")
        if self.char_range[1] - self.char_range[0] != len(self.normalized_text):
            _err(t, "char_range 的长度必须等于 normalized_text 的长度"
                    f"（{self.char_range[1] - self.char_range[0]} vs "
                    f"{len(self.normalized_text)}）")

        # layout_line_refs：来源范围必须非空，且首尾与起止锚点一致。
        if not isinstance(self.layout_line_refs, tuple) or not self.layout_line_refs:
            _err(t, "layout_line_refs 不得为空：材料单元必须能回溯到来源行")
        prev = (-1, -1)
        for i, ref in enumerate(self.layout_line_refs):
            if not isinstance(ref, tuple) or len(ref) != 2:
                _err(t, f"layout_line_refs[{i}] 必须为 (page_number, line_index)")
            if any(not isinstance(x, int) or isinstance(x, bool) for x in ref):
                _err(t, f"layout_line_refs[{i}] 元素必须为 int")
            if ref[0] < 1 or ref[1] < 0:
                _err(t, f"layout_line_refs[{i}] 越界: {ref}")
            if i > 0 and ref <= prev:
                _err(t, f"layout_line_refs 必须按源顺序严格递增，{i} 处得到 {ref}")
            prev = ref
        if self.layout_line_refs[0] != (self.start_anchor[0], self.start_anchor[1]):
            _err(t, "layout_line_refs 首项必须等于 start_anchor 的 (页, 行)")
        if self.layout_line_refs[-1] != (self.end_anchor[0], self.end_anchor[1]):
            _err(t, "layout_line_refs 末项必须等于 end_anchor 的 (页, 行)")

        if not isinstance(self.component_evidence_refs, tuple):
            _err(t, "component_evidence_refs 必须为元组")
        seen_ev: set[tuple[str, int, int]] = set()
        for i, ref in enumerate(self.component_evidence_refs):
            if not isinstance(ref, tuple) or len(ref) != 3:
                _err(t, f"component_evidence_refs[{i}] 必须为 (evidence_id, char_start, "
                        f"char_end)")
            eid, cs, ce = ref
            if not isinstance(eid, str) or eid == "":
                _err(t, f"component_evidence_refs[{i}] 的 evidence_id 必须为非空字符串")
            if not isinstance(cs, int) or not isinstance(ce, int) \
                    or isinstance(cs, bool) or isinstance(ce, bool):
                _err(t, f"component_evidence_refs[{i}] 的字符偏移必须为 int")
            if cs < 0 or ce <= cs:
                _err(t, f"component_evidence_refs[{i}] 必须满足 0 <= start < end，得到 "
                        f"{(cs, ce)}")
            if ref in seen_ev:
                _err(t, f"component_evidence_refs[{i}] 重复: {ref}")
            seen_ev.add(ref)
        if not isinstance(self.alignment_ids, tuple):
            _err(t, "alignment_ids 必须为元组")
        for i, aid in enumerate(self.alignment_ids):
            if not isinstance(aid, str) or aid == "":
                _err(t, f"alignment_ids[{i}] 必须为非空字符串")
        if len(set(self.alignment_ids)) != len(self.alignment_ids):
            _err(t, "alignment_ids 不得重复")
        if self.component_evidence_refs and not self.alignment_ids:
            _err(t, "存在 component Evidence 时必须提供版本化 alignment 记录"
                    "（禁止猜字符 offset）")

        expected_loc = derive_span_locator(
            document_outline_locator=self.document_outline_locator,
            evidence_set_version=self.evidence_set_version,
            start_anchor=self.start_anchor, end_anchor=self.end_anchor,
            span_builder_version=self.span_builder_version)
        if self.span_locator != expected_loc:
            _err(t, "span_locator 与派生定位身份不一致："
                    f"{self.span_locator!r} != {expected_loc!r}")
        expected = derive_span_id(
            span_locator=self.span_locator, schema_version=self.schema_version,
            document_id=self.document_id, document_version=self.document_version,
            node_id=self.node_id,
            role=self.role, unassigned_reason=self.unassigned_reason,
            page_range=self.page_range, char_range=self.char_range,
            layout_line_refs=self.layout_line_refs,
            component_evidence_refs=self.component_evidence_refs,
            alignment_ids=self.alignment_ids, is_fallback=self.is_fallback,
            fallback_derivation=self.fallback_derivation,
            is_cross_heading=self.is_cross_heading, confidence=self.confidence,
            normalized_text=self.normalized_text)
        if self.span_id != expected:
            _err(t, f"span_id 与派生身份不一致：{self.span_id!r} != {expected!r}")

    # -- 资格判定：局部结构资格 与 完整闭合资格（两层命名与语义分开） ------------

    def is_structurally_eligible(self) -> bool:
        """**局部结构资格**：只看 span 自身的结构条件，不判定集合是否闭合。

        要求：非 fallback、已归属具体节点、角色不是 unassigned、不跨标题、
        `confidence > 0` 且达到 `SPAN_CONFIDENCE_MIN`。后者**仍未裁决**（与已冻结
        为 `0.90` 的 `ALIGN_MIN` 不是同一条裁决），因此本轮恒返回 False
        —— fail-closed。
        """
        if self.is_fallback or self.node_id is None or self.role == "unassigned":
            return False
        if self.is_cross_heading:
            return False
        if self.confidence <= 0:
            return False
        if V.SPAN_CONFIDENCE_MIN is None:
            return False
        return self.confidence >= V.SPAN_CONFIDENCE_MIN

    def verify_alignment_closure(self,
                                 records: Sequence[TextAlignmentRecord]) -> None:
        """校验 `alignment_ids` 与调用方提供的记录**精确闭合**：不闭合即拒绝。"""
        errors = alignment_closure_errors(self, records)
        if errors:
            _err("OutlineSpan", "alignment 记录与 alignment_ids 不闭合："
                                + "；".join(errors))

    def alignment_citable(self, records: Sequence[TextAlignmentRecord]) -> bool:
        """本 span 引用的对齐记录是否全部可引证。

        先做闭合校验（缺/重/多/错绑一律 `SchemaValidationError`），再要求至少一条
        记录且全部 `is_citable()` —— 即全部为 `aligned`（达到冻结阈值 `0.90`
        且不含 `unexplained`）。阈值未裁决时恒为 False。
        """
        self.verify_alignment_closure(records)
        if not self.alignment_ids:
            return False
        return all(r.is_citable() for r in records)

    def can_support_set_complete(self, *,
                                 alignment_records: Sequence[TextAlignmentRecord],
                                 boundary_verified: bool) -> bool:
        """**完整闭合资格**：是否可参与集合型 aspect 的 `set_complete` 证明。

        必须同时满足（缺一即 False，且 alignment 不闭合会直接抛错）：

        1. `is_structurally_eligible()`（非 fallback / 已归属 / 不跨标题 / 置信度达标）；
        2. alignment 记录与 `alignment_ids` 精确闭合，且至少一条、全部可引证；
        3. 有 component Evidence（否则只是导航材料，不能充当引用锚点）；
        4. 调用方显式确认 `boundary_verified=True`（边界未被未核验的标题切开）。

        本方法**刻意不提供无参便利版本**：不接收 alignment 与边界上下文的判定
        不可能是最终判定。
        """
        if not self.is_structurally_eligible():
            return False
        if not boundary_verified:
            return False
        if not self.component_evidence_refs:
            return False
        return self.alignment_citable(alignment_records)

    def verify_upstream(self, *, outline: "DocumentOutline", layout: PageLayout) -> None:
        """把本 span 的文档身份与上游 outline / PageLayout 做**对象级**核对。

        必须比对对象，而不是"字段非空"：
        `document_outline_locator` 必须命中上游 outline，
        `document_id` / `document_version` 必须与上游 outline 和 PageLayout 逐一相等。
        """
        t = "OutlineSpan"
        if not isinstance(outline, DocumentOutline):
            _err(t, "verify_upstream 需要 DocumentOutline 对象，"
                    f"得到 {type(outline).__name__}")
        if not isinstance(layout, PageLayout):
            _err(t, f"verify_upstream 需要 PageLayout 对象，得到 {type(layout).__name__}")
        if outline.outline_locator != self.document_outline_locator:
            _err(t, "span.document_outline_locator 与上游 outline.outline_locator 不一致："
                    f"{self.document_outline_locator!r} != {outline.outline_locator!r}")
        if outline.document_id != self.document_id:
            _err(t, "span.document_id 与上游 outline.document_id 不一致："
                    f"{self.document_id!r} != {outline.document_id!r}")
        if layout.document_id != self.document_id:
            _err(t, "span.document_id 与上游 layout.document_id 不一致："
                    f"{self.document_id!r} != {layout.document_id!r}")
        if layout.document_version != self.document_version:
            _err(t, "span.document_version 与上游 layout.document_version 不一致："
                    f"{self.document_version!r} != {layout.document_version!r}")
        if outline.page_layout_id != layout.page_layout_id:
            _err(t, "上游 outline.page_layout_id 与 layout.page_layout_id 不一致："
                    f"{outline.page_layout_id!r} != {layout.page_layout_id!r}")

    def to_dict(self) -> dict:
        return {
            "schema_type": "OutlineSpan",
            "span_locator": self.span_locator,
            "span_id": self.span_id,
            "document_outline_locator": self.document_outline_locator,
            "node_id": self.node_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "evidence_set_version": self.evidence_set_version,
            "role": self.role,
            "schema_version": self.schema_version,
            "span_builder_version": self.span_builder_version,
            "unassigned_reason": self.unassigned_reason,
            "start_anchor": _json(self.start_anchor),
            "end_anchor": _json(self.end_anchor),
            "page_range": _json(self.page_range),
            "char_range": _json(self.char_range),
            "layout_line_refs": _json(self.layout_line_refs),
            "component_evidence_refs": _json(self.component_evidence_refs),
            "alignment_ids": _json(self.alignment_ids),
            "is_fallback": self.is_fallback,
            "fallback_derivation": self.fallback_derivation,
            "is_cross_heading": self.is_cross_heading,
            "confidence": self.confidence,
            "normalized_text": self.normalized_text,
            "content_fingerprint": self.content_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "OutlineSpan":
        t = "OutlineSpan"
        d = _reject_unknown(d, {
            "schema_type", "span_locator", "span_id", "document_outline_locator",
            "node_id", "document_id", "document_version", "evidence_set_version",
            "role", "schema_version", "span_builder_version", "unassigned_reason",
            "start_anchor", "end_anchor", "page_range", "char_range",
            "layout_line_refs", "component_evidence_refs", "alignment_ids",
            "is_fallback", "fallback_derivation", "is_cross_heading", "confidence",
            "normalized_text", "content_fingerprint"}, t)
        _need_enum(d, "schema_type", t, ("OutlineSpan",))
        return cls(
            span_locator=_need_str(d, "span_locator", t),
            span_id=_need_str(d, "span_id", t),
            document_outline_locator=_need_str(d, "document_outline_locator", t),
            node_id=_need_str(d, "node_id", t, none_ok=True),
            document_id=_need_str(d, "document_id", t),
            document_version=_need_str(d, "document_version", t),
            evidence_set_version=_need_str(d, "evidence_set_version", t),
            role=_need_enum(d, "role", t, SPAN_ROLES),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                "SPAN_SCHEMA_VERSION"),
            span_builder_version=_need_str(d, "span_builder_version", t),
            unassigned_reason=_need_str(d, "unassigned_reason", t, none_ok=True),
            start_anchor=_need_anchor(d, "start_anchor", t),
            end_anchor=_need_anchor(d, "end_anchor", t),
            page_range=_need_int_pair(d, "page_range", t, lo=1),
            char_range=_need_int_pair(d, "char_range", t, lo=0, strict=True),
            layout_line_refs=_need_line_refs(d, "layout_line_refs", t),
            component_evidence_refs=_need_evidence_refs(d, "component_evidence_refs", t),
            alignment_ids=_need_str_tuple(d, "alignment_ids", t),
            is_fallback=_need_bool(d, "is_fallback", t),
            fallback_derivation=_need_str(d, "fallback_derivation", t, none_ok=True),
            is_cross_heading=_need_bool(d, "is_cross_heading", t),
            confidence=_need_num(d, "confidence", t, lo=0.0, hi=1.0),
            normalized_text=_need_str(d, "normalized_text", t),
            content_fingerprint=_need_sha256(d, "content_fingerprint", t),
        )

    @classmethod
    def create(cls, *, document_outline_locator: str, node_id: str | None,
               document_id: str, document_version: str, evidence_set_version: str,
               role: str, start_anchor: tuple[int, int, tuple],
               end_anchor: tuple[int, int, tuple], normalized_text: str,
               layout_line_refs: tuple[tuple[int, int], ...] = (),
               component_evidence_refs: tuple[tuple[str, int, int], ...] = (),
               alignment_ids: tuple[str, ...] = (), is_fallback: bool = False,
               fallback_derivation: str | None = None, is_cross_heading: bool = False,
               confidence: float = 1.0, char_range: tuple[int, int] | None = None,
               unassigned_reason: str | None = None,
               span_builder_version: str = V.SPAN_BUILDER_VERSION) -> "OutlineSpan":
        if char_range is None:
            char_range = (0, len(normalized_text))
        # `__post_init__` 会把 `confidence` 量化后再派生身份（`quantize`），因此
        # 这里**必须**用同一个量化值参与派生，否则"按未量化值算出的 span_id"会在
        # `__post_init__` 的比对里判为不一致 —— 那是身份算法内部不自洽，而不是
        # 调用方造假。允许的输入面是"任意有限实数"，身份只对量化后的存储值负责。
        confidence = quantize(confidence)
        loc = derive_span_locator(
            document_outline_locator=document_outline_locator,
            evidence_set_version=evidence_set_version, start_anchor=start_anchor,
            end_anchor=end_anchor, span_builder_version=span_builder_version)
        return cls(
            span_locator=loc,
            span_id=derive_span_id(
                span_locator=loc, schema_version=V.SPAN_SCHEMA_VERSION,
                document_id=document_id,
                document_version=document_version, node_id=node_id, role=role,
                unassigned_reason=unassigned_reason,
                page_range=(start_anchor[0], end_anchor[0]), char_range=char_range,
                layout_line_refs=layout_line_refs,
                component_evidence_refs=component_evidence_refs,
                alignment_ids=alignment_ids, is_fallback=is_fallback,
                fallback_derivation=fallback_derivation,
                is_cross_heading=is_cross_heading, confidence=confidence,
                normalized_text=normalized_text),
            document_outline_locator=document_outline_locator, node_id=node_id,
            document_id=document_id, document_version=document_version,
            evidence_set_version=evidence_set_version, role=role,
            schema_version=V.SPAN_SCHEMA_VERSION,
            span_builder_version=span_builder_version,
            unassigned_reason=unassigned_reason, start_anchor=start_anchor,
            end_anchor=end_anchor,
            page_range=(start_anchor[0], end_anchor[0]), char_range=char_range,
            layout_line_refs=layout_line_refs,
            component_evidence_refs=component_evidence_refs,
            alignment_ids=alignment_ids, is_fallback=is_fallback,
            fallback_derivation=fallback_derivation, is_cross_heading=is_cross_heading,
            confidence=confidence, normalized_text=normalized_text,
            content_fingerprint=derive_span_content_fingerprint(normalized_text),
        )


def derive_span_locator(*, document_outline_locator: str, evidence_set_version: str,
                        start_anchor: tuple, end_anchor: tuple,
                        span_builder_version: str) -> str:
    """`loc-os-<sha256[:16]>`：稳定定位（outline locator + evidence set + 起止锚点）。"""
    return locator("os", {
        "document_outline_locator": document_outline_locator,
        "evidence_set_version": evidence_set_version,
        "start_anchor": _json(start_anchor),
        "end_anchor": _json(end_anchor),
        "span_builder_version": span_builder_version,
    })


def derive_span_id(*, span_locator: str, schema_version: str, document_id: str,
                   document_version: str,
                   node_id: str | None, role: str, unassigned_reason: str | None,
                   page_range: tuple, char_range: tuple, layout_line_refs: tuple,
                   component_evidence_refs: tuple, alignment_ids: tuple,
                   is_fallback: bool, fallback_derivation: str | None,
                   is_cross_heading: bool, confidence: float,
                   normalized_text: str) -> str:
    """`os-<sha256[:16]>`：不可变 revision 身份（绑定文档身份、正文与全部材料资格字段）。

    `document_id` **显式**进入身份（不只是经 outline locator 间接进入）：
    同一份材料在两个不同 `document_id` 下必须是两个不同的 span。
    """
    return identity("os", {
        "span_locator": span_locator,
        "schema_version": schema_version,
        "document_id": document_id,
        "document_version": document_version,
        "node_id": node_id,
        "role": role,
        "unassigned_reason": unassigned_reason,
        "page_range": _json(page_range),
        "char_range": _json(char_range),
        "layout_line_refs": _json(layout_line_refs),
        "component_evidence_refs": _json(component_evidence_refs),
        "alignment_ids": _json(alignment_ids),
        "is_fallback": is_fallback,
        "fallback_derivation": fallback_derivation,
        "is_cross_heading": is_cross_heading,
        "confidence": confidence,
        "normalized_text": normalized_text,
    })


def derive_span_content_fingerprint(normalized_text: str) -> str:
    """span 规范化文本的内容哈希（UTF-8 sha256 全 64 位）。"""
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 7. 表格：TableRow / TableCell / TableObject
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TableRow:
    """表格的一行（物理表头行 / 表体行 / 合计或小计行）。

    `cells` 是**从本行起始**的物理单元格文本，按列顺序排列；由上一行的 rowspan
    续接到本行的单元格**不在**本行重复出现（它的文本已经在起始行给出，重复会让同一
    事实出现两处）。因此 `cells` 与 `TableObject.cell_grid` 中 `row == row_index`
    的单元格一一对应；整行都被上行 rowspan 覆盖时 `cells` 可以为**空元组**。

    **不含** `semantics` 之类业务口径字段：授信 / 财务 / 公司业务语义必须由后续
    事实层派生，TS1 的表格层只表达可验证的**物理与来源结构**。
    """

    row_index: int
    kind: str
    cells: tuple[str, ...]
    label: str | None

    def __post_init__(self) -> None:
        t = "TableRow"
        if not isinstance(self.row_index, int) or isinstance(self.row_index, bool) \
                or self.row_index < 0:
            _err(t, f"row_index 必须为非负 int，得到 {self.row_index!r}")
        if self.kind not in ROW_KINDS:
            _err(t, f"kind 必须属于 {ROW_KINDS}，得到 {self.kind!r}")
        if not isinstance(self.cells, tuple):
            _err(t, "cells 必须为元组（整行由上行 rowspan 覆盖时可为空元组）")
        for i, c in enumerate(self.cells):
            if not isinstance(c, str):
                _err(t, f"cells[{i}] 必须为字符串，得到 {type(c).__name__}")
        if self.kind in ("total", "subtotal"):
            if not isinstance(self.label, str) or self.label == "":
                _err(t, f"kind={self.kind!r} 的行必须有非空 label（合计/小计的物理标签）")
        if self.label is not None and (not isinstance(self.label, str)
                                       or self.label == ""):
            _err(t, "label 必须为非空字符串或 None")

    def to_dict(self) -> dict:
        return {
            "schema_type": "TableRow",
            "row_index": self.row_index,
            "kind": self.kind,
            "cells": _json(self.cells),
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TableRow":
        t = "TableRow"
        d = _reject_unknown(d, {"schema_type", "row_index", "kind", "cells", "label"}, t)
        _need_enum(d, "schema_type", t, ("TableRow",))
        return cls(
            row_index=_need_int(d, "row_index", t, lo=0),
            kind=_need_enum(d, "kind", t, ROW_KINDS),
            cells=_need_str_tuple_allow_empty(d, "cells", t),
            label=_need_str(d, "label", t, none_ok=True),
        )


@dataclass(frozen=True)
class TableCell:
    """表格网格中的一个单元格（物理结构 + 来源定位）。

    寻址：`(table_id, row, column)`。`rowspan` / `colspan` ≥ 1；`source_locator` 为
    `(page_number, line_index, span_index)`，指向该单元格**首个**来源排版片段，
    因此每个单元格都可回查到 PageLayout。
    """

    row: int
    column: int
    rowspan: int
    colspan: int
    text: str
    bbox: tuple[float, float, float, float]
    source_locator: tuple[int, int, int]

    def __post_init__(self) -> None:
        t = "TableCell"
        for name in ("row", "column"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                _err(t, f"{name} 必须为非负 int，得到 {v!r}")
        for name in ("rowspan", "colspan"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                _err(t, f"{name} 必须为 >= 1 的 int，得到 {v!r}")
        if not isinstance(self.text, str):
            _err(t, "text 必须为字符串（允许为空串：表格可以有空白单元格）")
        if "\n" in self.text:
            _err(t, "text 不得包含换行")
        object.__setattr__(self, "bbox", tuple(quantize(x) for x in self.bbox))
        _check_bbox(self.bbox, f"{t}.bbox")
        if not isinstance(self.source_locator, tuple) or len(self.source_locator) != 3:
            _err(t, "source_locator 必须为 (page_number, line_index, span_index)")
        for name, v in zip(("page_number", "line_index", "span_index"),
                           self.source_locator):
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                _err(t, f"source_locator 的 {name} 必须为非负 int，得到 {v!r}")
        if self.source_locator[0] < 1:
            _err(t, f"source_locator 的页码必须从 1 开始，得到 {self.source_locator[0]}")

    def to_dict(self) -> dict:
        return {
            "schema_type": "TableCell",
            "row": self.row,
            "column": self.column,
            "rowspan": self.rowspan,
            "colspan": self.colspan,
            "text": self.text,
            "bbox": _json(self.bbox),
            "source_locator": _json(self.source_locator),
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TableCell":
        t = "TableCell"
        d = _reject_unknown(d, {
            "schema_type", "row", "column", "rowspan", "colspan", "text", "bbox",
            "source_locator"}, t)
        _need_enum(d, "schema_type", t, ("TableCell",))
        v = d.get("source_locator")
        if not isinstance(v, list) or len(v) != 3:
            _err(t, "source_locator 必须为 [page_number, line_index, span_index]")
        for x in v:
            if not isinstance(x, int) or isinstance(x, bool):
                _err(t, "source_locator 元素必须为 int")
        return cls(
            row=_need_int(d, "row", t, lo=0),
            column=_need_int(d, "column", t, lo=0),
            rowspan=_need_int(d, "rowspan", t, lo=1),
            colspan=_need_int(d, "colspan", t, lo=1),
            text=_need_str(d, "text", t, empty_ok=True),
            bbox=_need_bbox(d, "bbox", t),
            source_locator=(v[0], v[1], v[2]),
        )


def _need_str_tuple_allow_empty(d: dict, key: str, typename: str) -> tuple[str, ...]:
    v = d.get(key)
    if v is None:
        _err(typename, f"缺必填字段: {key}")
    if not isinstance(v, list):
        _err(typename, f"{key} 必须为 list[str]，得到 {type(v).__name__}")
    for x in v:
        if not isinstance(x, str):
            _err(typename, f"{key} 含非字符串元素: {x!r}")
    return tuple(v)


def _need_cell_grid(d: dict, key: str, typename: str) -> tuple[TableCell, ...]:
    return _need_children(d, key, typename, TableCell.from_dict)


@dataclass(frozen=True)
class TableObject:
    """独立表达的表格对象（构建于 TS5）：表题、单位、表头、表体、合计、续表与 provenance。

    **单页物理片段模型**（TS1.1 P1-C）：一个 `TableObject` **就是**一页上的一个物理
    表格片段，`page_number` 是它唯一的物理页边界。跨页续表由**多个单页片段**通过
    `continuation_of_locator` / `continuation_locators` 连接表达；一个 bbox 不得冒充
    多页表格的边界（上一轮的 `page_range=(start, end)` 允许一个 bbox 声称横跨多页）。
    跨页片段的**逻辑表聚合**属于 TS5/TS7A，本层不实现、不假装实现。

    - **稳定定位身份** `table_locator`：`loc-to-<h(page_layout_id, page_number,
      table_index_on_page, table_bbox, TABLE_BUILDER_VERSION)>`。
    - **不可变 revision 身份** `table_id`：在 locator 之上绑定 schema version、
      上游身份（outline revision 与 locator、node、document/evidence set 版本）、
      全部行与 `cell_grid`、表题/单位/结构分类/结构证据、续表关系、component 引用。
      身份计算时文本一律经 `canonical_text` 空白折叠：**只有空白规范化差异时身份
      保持稳定**，企业名 / 比例 / 表体行 / 合计 / 表头 / 来源范围任一变化必然换 id。

    **没有真实结构信号时不得创建本对象**：`structure_evidence` 非空且必须含列边界
    一致性证据（`COLUMN_BOUNDARY_SIGNAL`），否则普通段落会伪装成表格。
    `structure_class` **只是结构分类，不是 authority verdict**：金额权威一律来自
    `FinancialSnapshot` / `FinancialFactPack`（`is_financial_authority()` 恒 False）。

    provenance 是**对象级**闭合（`verify_provenance(spans=..., alignment_records=...)`）：
    调用方不能再用"把同一串 id 传回来"证明对象存在。几何完整性由
    `verify_layout(layout)` 用真实 PageLayout 核验（未核验前不得声称完整）。
    """

    table_locator: str
    table_id: str
    schema_version: str
    page_layout_id: str
    document_outline_id: str
    document_outline_locator: str
    node_id: str | None
    document_id: str
    document_version: str
    evidence_set_version: str
    page_number: int
    table_index_on_page: int
    table_bbox: tuple[float, float, float, float]
    title: str | None
    title_source: str
    unit: str | None
    structure_class: str
    structure_evidence: tuple[str, ...]
    column_count: int
    header_rows: tuple[TableRow, ...]
    body_rows: tuple[TableRow, ...]
    total_rows: tuple[TableRow, ...]
    cell_grid: tuple[TableCell, ...]
    component_span_ids: tuple[str, ...]
    component_evidence_refs: tuple[tuple[str, int, int], ...]
    alignment_ids: tuple[str, ...]
    # 续表关系指向同级的**另一张表**，因此必须用稳定定位（`loc-to-…`）而不是
    # revision id：`table_id` 是内容寻址的，若续表关系引用对方的 `table_id`，
    # 则 A 的指纹要含 B 的 id、B 的指纹要含 A 的 id，链条不可构造
    # （与 ReferenceEdge 的包含规则同一类问题的同级版本，见模块 docstring）。
    # 定位身份与内容无关，可在不知道对方内容的前提下自行计算，故无循环。
    # 续表关系属于**规范形**：它变化时 `table_id` 必须随之变化。
    continuation_of_locator: str | None
    continuation_locators: tuple[str, ...]
    table_builder_version: str
    content_fingerprint: str

    def __post_init__(self) -> None:
        t = "TableObject"
        for name in ("table_locator", "table_id", "page_layout_id",
                     "document_outline_id", "document_outline_locator",
                     "document_id", "document_version", "evidence_set_version"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        if self.node_id is not None and (not isinstance(self.node_id, str)
                                         or not self.node_id):
            _err(t, "node_id 必须为非空字符串或 None")
        # TS5 §19.11.2：本类是 `to-3` 的**历史 reader**，校验目标必须是类内固定的
        # legacy 常量，不得再读全局 current（否则 current 升到 `to-4` 后本类会把自己
        # 变成 `to-4` 的错误解释器）。
        _check_legacy_version(t, "table_builder_version", self.table_builder_version,
                              V.LEGACY_TABLE_BUILDER_VERSION, "TABLE_BUILDER_VERSION")
        _check_legacy_version(t, "schema_version", self.schema_version,
                              V.LEGACY_TABLE_SCHEMA_VERSION, "TABLE_SCHEMA_VERSION")
        if not isinstance(self.page_number, int) or isinstance(self.page_number, bool):
            _err(t, f"page_number 必须为 int，得到 {self.page_number!r}")
        if self.page_number < 1:
            _err(t, f"page_number 必须从 1 开始（本对象只表示单页物理片段），"
                    f"得到 {self.page_number}")
        if not isinstance(self.table_index_on_page, int) \
                or isinstance(self.table_index_on_page, bool) \
                or self.table_index_on_page < 0:
            _err(t, f"table_index_on_page 必须为非负 int，得到 {self.table_index_on_page!r}")
        object.__setattr__(self, "table_bbox",
                           tuple(quantize(x) for x in self.table_bbox))
        _check_bbox(self.table_bbox, f"{t}.table_bbox")
        if self.title is not None and (not isinstance(self.title, str)
                                       or self.title == ""):
            _err(t, "title 必须为非空字符串或 None")
        if self.title_source not in TITLE_SOURCES:
            _err(t, f"title_source 必须属于 {TITLE_SOURCES}，得到 {self.title_source!r}")
        if (self.title is None) != (self.title_source == "none"):
            _err(t, "title 与 title_source 必须一致：title=None 当且仅当 title_source=='none'")
        if self.unit is not None and (not isinstance(self.unit, str)
                                      or self.unit == ""):
            _err(t, "unit 必须为非空字符串或 None")
        if self.structure_class not in STRUCTURE_CLASSES:
            _err(t, f"structure_class 必须属于 {STRUCTURE_CLASSES}，得到 "
                    f"{self.structure_class!r}")
        if not isinstance(self.structure_evidence, tuple) or not self.structure_evidence:
            _err(t, "structure_evidence 必须为非空元组（无结构信号不得创建 TableObject）")
        for i, s in enumerate(self.structure_evidence):
            if s not in TABLE_STRUCTURE_SIGNALS:
                _err(t, f"structure_evidence[{i}] 必须属于既有结构信号词表 "
                        f"{TABLE_STRUCTURE_SIGNALS}，得到 {s!r}")
        if len(set(self.structure_evidence)) != len(self.structure_evidence):
            _err(t, "structure_evidence 不得重复")
        if COLUMN_BOUNDARY_SIGNAL not in self.structure_evidence:
            _err(t, f"structure_evidence 必须包含列边界一致性证据 "
                    f"{COLUMN_BOUNDARY_SIGNAL!r}")
        if not isinstance(self.column_count, int) or isinstance(self.column_count, bool) \
                or self.column_count < 2:
            _err(t, f"column_count 必须 >= 2，得到 {self.column_count!r}")

        if not self.header_rows:
            _err(t, "header_rows 不得为空：无法稳定恢复物理表头时不得创建 TableObject")
        if not self.body_rows:
            _err(t, "body_rows 不得为空：只有表头的区域不构成表格")

        groups = (("header", self.header_rows), ("body", self.body_rows),
                  ("total", self.total_rows))
        all_rows: list[TableRow] = []
        for kind, rows in groups:
            if not isinstance(rows, tuple):
                _err(t, f"{kind}_rows 必须为元组")
            for i, r in enumerate(rows):
                if not isinstance(r, TableRow):
                    _err(t, f"{kind}_rows[{i}] 必须为 TableRow，得到 {type(r).__name__}")
                if kind == "header" and r.kind != "header":
                    _err(t, f"header_rows[{i}].kind 必须为 'header'，得到 {r.kind!r}")
                if kind == "body" and r.kind != "body":
                    _err(t, f"body_rows[{i}].kind 必须为 'body'，得到 {r.kind!r}")
                if kind == "total" and r.kind not in ("total", "subtotal"):
                    _err(t, f"total_rows[{i}].kind 必须为 'total' 或 'subtotal'，"
                            f"得到 {r.kind!r}")
                all_rows.append(r)
        for i, r in enumerate(all_rows):
            if r.row_index != i:
                _err(t, f"行的 row_index 必须按物理顺序从 0 连续递增到 "
                        f"{len(all_rows) - 1}（{i} 处得到 {r.row_index}）")

        self._check_cell_grid(all_rows)

        if not isinstance(self.component_span_ids, tuple) or not self.component_span_ids:
            _err(t, "component_span_ids 不得为空：表格必须是 Evidence-backed 结构对象，"
                    "必须给出其 component OutlineSpan 身份")
        for i, sid in enumerate(self.component_span_ids):
            if not isinstance(sid, str) or sid == "":
                _err(t, f"component_span_ids[{i}] 必须为非空字符串")
        if len(set(self.component_span_ids)) != len(self.component_span_ids):
            _err(t, "component_span_ids 不得重复")
        if not isinstance(self.component_evidence_refs, tuple):
            _err(t, "component_evidence_refs 必须为元组")
        for i, ref in enumerate(self.component_evidence_refs):
            if not isinstance(ref, tuple) or len(ref) != 3:
                _err(t, f"component_evidence_refs[{i}] 必须为 (evidence_id, start, end)")
            eid, cs, ce = ref
            if not isinstance(eid, str) or eid == "":
                _err(t, f"component_evidence_refs[{i}] 的 evidence_id 必须为非空字符串")
            if not isinstance(cs, int) or not isinstance(ce, int) \
                    or isinstance(cs, bool) or isinstance(ce, bool):
                _err(t, f"component_evidence_refs[{i}] 的字符偏移必须为 int")
            if cs < 0 or ce <= cs:
                _err(t, f"component_evidence_refs[{i}] 必须满足 0 <= start < end")
        if not isinstance(self.alignment_ids, tuple) or not self.alignment_ids:
            _err(t, "alignment_ids 不得为空：无版本化对齐记录的表格不得作为正式材料")
        for i, aid in enumerate(self.alignment_ids):
            if not isinstance(aid, str) or aid == "":
                _err(t, f"alignment_ids[{i}] 必须为非空字符串")
        if len(set(self.alignment_ids)) != len(self.alignment_ids):
            _err(t, "alignment_ids 不得重复")

        if self.continuation_of_locator is not None:
            if not isinstance(self.continuation_of_locator, str) \
                    or not self.continuation_of_locator.startswith(
                        TABLE_LOCATOR_PREFIX):
                _err(t, f"continuation_of_locator 必须为 {TABLE_LOCATOR_PREFIX!r} "
                        f"开头的稳定定位或 None，得到 {self.continuation_of_locator!r}"
                        "（续表关系不得引用 revision id：那会让两张表互相依赖而不可构造）")
            if self.continuation_of_locator == self.table_locator:
                _err(t, "continuation_of_locator 不得指向自身定位")
        if not isinstance(self.continuation_locators, tuple):
            _err(t, "continuation_locators 必须为元组")
        for i, cid in enumerate(self.continuation_locators):
            if not isinstance(cid, str) or not cid.startswith(TABLE_LOCATOR_PREFIX):
                _err(t, f"continuation_locators[{i}] 必须为 {TABLE_LOCATOR_PREFIX!r} "
                        f"开头的稳定定位，得到 {cid!r}")
            if cid == self.table_locator:
                _err(t, "continuation_locators 不得包含自身定位")
        if len(set(self.continuation_locators)) != len(self.continuation_locators):
            _err(t, "continuation_locators 不得重复")

        expected_cf = derive_table_content_fingerprint(
            schema_version=self.schema_version,
            document_outline_id=self.document_outline_id,
            document_outline_locator=self.document_outline_locator,
            node_id=self.node_id,
            document_id=self.document_id, document_version=self.document_version,
            evidence_set_version=self.evidence_set_version,
            page_number=self.page_number, table_index_on_page=self.table_index_on_page,
            table_bbox=self.table_bbox, title=self.title,
            title_source=self.title_source, unit=self.unit,
            structure_class=self.structure_class,
            structure_evidence=self.structure_evidence,
            column_count=self.column_count, header_rows=self.header_rows,
            body_rows=self.body_rows, total_rows=self.total_rows,
            cell_grid=self.cell_grid, component_span_ids=self.component_span_ids,
            component_evidence_refs=self.component_evidence_refs,
            alignment_ids=self.alignment_ids,
            continuation_of_locator=self.continuation_of_locator,
            continuation_locators=self.continuation_locators)
        if self.content_fingerprint != expected_cf:
            _err(t, "content_fingerprint 与内容不一致：表格内容已变，身份必须随之改变"
                    f"（{self.content_fingerprint!r} != {expected_cf!r}）")
        expected_loc = derive_table_locator(
            page_layout_id=self.page_layout_id, page_number=self.page_number,
            table_index_on_page=self.table_index_on_page, table_bbox=self.table_bbox,
            table_builder_version=self.table_builder_version)
        if self.table_locator != expected_loc:
            _err(t, "table_locator 与派生定位身份不一致："
                    f"{self.table_locator!r} != {expected_loc!r}")
        expected = derive_table_id(
            table_locator=self.table_locator, content_fingerprint=self.content_fingerprint)
        if self.table_id != expected:
            _err(t, f"table_id 与派生身份不一致：{self.table_id!r} != {expected!r}")

    def _check_cell_grid(self, all_rows: tuple[TableRow, ...] | list[TableRow]) -> None:
        """cell_grid 不变量（P1-6 / TS1.1 P1-C）。

        rowspan / colspan 语义：`TableRow.cells` 只列**从本行起始**的单元格；由上一行
        rowspan 续接到本行的单元格不在本行重复。因此：

        - 已删除上一轮那条错误规则"本行起始单元格 colspan 之和 == column_count"
          （rowspan 续接会让该和**有意**小于 column_count，合法表格会被误拒）；
        - 改为**全局活跃网格覆盖**：每一物理行的活跃单元格列覆盖必须恰好等于
          `[0, column_count)`，多一个列 → 重叠、少一个列 → 空洞，两者都拒绝；
        - 每个单元格的来源页必须**等于**本表页（单页片段模型，不存在"跨页单元格"）；
        - 每个单元格 bbox 必须落在表格 bbox 内。
        """
        t = "TableObject"
        if not isinstance(self.cell_grid, tuple) or not self.cell_grid:
            _err(t, "cell_grid 不得为空：表格必须有可回查的单元格网格")
        for i, c in enumerate(self.cell_grid):
            if not isinstance(c, TableCell):
                _err(t, f"cell_grid[{i}] 必须为 TableCell，得到 {type(c).__name__}")
            if c.column + c.colspan > self.column_count:
                _err(t, f"cell_grid[{i}] 的列范围 [{c.column},{c.column + c.colspan}) "
                        f"越出 column_count={self.column_count}")
            if c.source_locator[0] != self.page_number:
                _err(t, f"cell_grid[{i}] 的来源页 {c.source_locator[0]} 必须等于本表页 "
                        f"{self.page_number}（本对象是单页物理片段）")
            self._check_cell_bbox(i, c)

        row_count = max(c.row + c.rowspan for c in self.cell_grid)
        if len(all_rows) != row_count:
            _err(t, f"行分区必须覆盖 cell_grid 的全部 {row_count} 行"
                    f"（实际 {len(all_rows)} 行）")
        # 网格无冲突：每一行的列覆盖必须恰好等于 [0, column_count)。
        for r in range(row_count):
            covered: list[int] = []
            for c in self.cell_grid:
                if c.row <= r < c.row + c.rowspan:
                    covered.extend(range(c.column, c.column + c.colspan))
            if sorted(covered) != list(range(self.column_count)):
                _err(t, f"cell_grid 在第 {r} 行存在重叠、空洞或越界"
                        f"（覆盖 {sorted(covered)}，应为 {list(range(self.column_count))}）")
        # 单元格与行文本必须一一对应（起始行语义）。
        for row in all_rows:
            row_cells = sorted(
                (c for c in self.cell_grid if c.row == row.row_index),
                key=lambda c: c.column)
            if tuple(c.text for c in row_cells) != tuple(row.cells):
                _err(t, f"row_index={row.row_index} 的 cells 必须等于 cell_grid 中"
                        f"**起始于本行**的单元格文本（{tuple(row.cells)!r} vs "
                        f"{tuple(c.text for c in row_cells)!r}）")

    def _check_cell_bbox(self, index: int, cell: TableCell) -> None:
        """单元格 bbox 必须落在表格 bbox 内（含容差）。"""
        tb = self.table_bbox
        cb = cell.bbox
        tol = BBOX_PAGE_TOLERANCE_PT
        if (cb[0] < tb[0] - tol or cb[1] < tb[1] - tol
                or cb[2] > tb[2] + tol or cb[3] > tb[3] + tol):
            _err("TableObject", f"cell_grid[{index}] 的 bbox {cb} 必须落在表格 bbox "
                                f"{tb} 内（容差 {tol}pt）")

    def is_financial_authority(self) -> bool:
        """恒为 False：`structure_class` 不是 authority verdict（金额权威另属）。"""
        return False

    def all_rows(self) -> tuple[TableRow, ...]:
        """物理顺序的全部行（表头 → 表体 → 合计/小计）。"""
        return tuple(self.header_rows) + tuple(self.body_rows) + tuple(self.total_rows)

    def verify_provenance(self, *, spans: Sequence["OutlineSpan"],
                          alignment_records: Sequence[TextAlignmentRecord]) -> None:
        """核验 component span / Evidence / alignment 三者**对象级**闭合：不闭合即拒绝。

        上一轮的签名是 `span_ids: Sequence[str]`，于是只要调用方把同一串
        `component_span_ids` 传回来，**伪造的 span id** 就能通过——"证明对象存在"退化成了
        "重复一遍自己说的话"。现在必须传**真实 `OutlineSpan` 对象**，逐条核对：

        1. span 的实际 `span_id` 集合必须**精确等于** `component_span_ids`；
        2. 每个 span 的 `document_id` / `document_version` / `evidence_set_version` /
           `document_outline_locator` 必须与本表格一致；
        3. 每条 alignment 记录的 `page_layout_id` / `evidence_set_version` /
           `evidence_block_id` 必须与本表格、span 及 component Evidence 引用一致；
        4. span 的 component Evidence 引用**并集**必须与表格的引用**精确闭合**
           （同一 Evidence ID 但字符区间不同即不闭合，必须拒绝）；
        5. span 的 `alignment_ids` 并集必须与 `TableObject.alignment_ids` 精确闭合；
        6. 缺少 / 多余 / 重复 / 跨文档 / 跨 outline / 跨 layout / 跨 evidence set 一律
           fail-closed；
        7. 调用方**不能**再靠传任意字符串集合证明对象存在。
        """
        errors: list[str] = []
        span_list = list(spans)
        for i, s in enumerate(span_list):
            if not isinstance(s, OutlineSpan):
                _err("TableObject", f"spans[{i}] 必须为 OutlineSpan 对象，得到 "
                                    f"{type(s).__name__}（不得用字符串 id 代替对象）")
        if not isinstance(alignment_records, Sequence):
            _err("TableObject", "alignment_records 必须为序列")

        provided_span_ids = [s.span_id for s in span_list]
        if len(set(provided_span_ids)) != len(provided_span_ids):
            errors.append("component span 不得重复提供")
        want_spans = set(self.component_span_ids)
        have_spans = set(provided_span_ids)
        if want_spans != have_spans:
            if want_spans - have_spans:
                errors.append(f"缺少 component OutlineSpan: {sorted(want_spans - have_spans)}")
            if have_spans - want_spans:
                errors.append(f"提供了非 component OutlineSpan: "
                              f"{sorted(have_spans - want_spans)}")

        span_evidence: set[tuple[str, int, int]] = set()
        span_alignments: set[str] = set()
        for s in span_list:
            if s.document_id != self.document_id:
                errors.append(f"span {s.span_id!r} 属于另一个文档"
                              f"（document_id={s.document_id!r}）")
            if s.document_version != self.document_version:
                errors.append(f"span {s.span_id!r} 属于另一个文档版本"
                              f"（document_version={s.document_version!r}）")
            if s.evidence_set_version != self.evidence_set_version:
                errors.append(f"span {s.span_id!r} 属于另一个 evidence set"
                              f"（{s.evidence_set_version!r}）")
            if s.document_outline_locator != self.document_outline_locator:
                errors.append(f"span {s.span_id!r} 属于另一个 outline"
                              f"（{s.document_outline_locator!r}）")
            span_evidence |= set(s.component_evidence_refs)
            span_alignments |= set(s.alignment_ids)

        table_evidence = set(self.component_evidence_refs)
        if span_evidence != table_evidence:
            only_span = sorted(span_evidence - table_evidence)
            only_table = sorted(table_evidence - span_evidence)
            errors.append("component Evidence 引用必须与 component span 的引用精确闭合"
                          f"（span 独有 {only_span}，表格独有 {only_table}；"
                          "同一 Evidence ID 但字符区间不同也算不闭合）")

        provided_alignments = [r.alignment_id for r in alignment_records]
        if len(set(provided_alignments)) != len(provided_alignments):
            errors.append("alignment 记录不得重复")
        want_alignments = set(self.alignment_ids)
        have_alignments = set(provided_alignments)
        if want_alignments != have_alignments:
            if want_alignments - have_alignments:
                errors.append(f"缺少引用的 alignment 记录: "
                              f"{sorted(want_alignments - have_alignments)}")
            if have_alignments - want_alignments:
                errors.append(f"提供了未引用的 alignment 记录: "
                              f"{sorted(have_alignments - want_alignments)}")
        if span_alignments != want_alignments:
            errors.append("component span 的 alignment_ids 并集必须与表格的 "
                          "alignment_ids 精确闭合"
                          f"（span 独有 {sorted(span_alignments - want_alignments)}，"
                          f"表格独有 {sorted(want_alignments - span_alignments)}）")

        evidence_ids = {eid for eid, _cs, _ce in self.component_evidence_refs}
        for r in alignment_records:
            if r.page_layout_id != self.page_layout_id:
                errors.append(f"alignment {r.alignment_id!r} 属于另一个 page_layout"
                              f"（{r.page_layout_id!r}）")
            if r.evidence_set_version != self.evidence_set_version:
                errors.append(f"alignment {r.alignment_id!r} 属于另一个 evidence set"
                              f"（{r.evidence_set_version!r}）")
            if evidence_ids and r.evidence_block_id not in evidence_ids:
                errors.append(f"alignment {r.alignment_id!r} 引用了非 component Evidence"
                              f"（{r.evidence_block_id!r}）")
        if errors:
            _err("TableObject", "provenance 不闭合：" + "；".join(errors))

    def is_evidence_backed(self, *, spans: Sequence["OutlineSpan"],
                           alignment_records: Sequence[TextAlignmentRecord]) -> bool:
        """是否是可回查的 Evidence-backed 表格。

        **必须**走完整的对象级 provenance 校验（缺一即抛错，而不是静默 False），
        且只有在全部 alignment 记录都可引证时才为 True —— 即全部为 `aligned`
        （`TextAlignmentRecord.is_citable()` 的 fail-closed 语义：达到冻结阈值
        `0.90` 且不含 `unexplained`；`align_min is None` 的兼容档恒为 False）。
        """
        self.verify_provenance(spans=spans, alignment_records=alignment_records)
        if not self.component_evidence_refs:
            return False
        return all(r.is_citable() for r in alignment_records)

    def verify_upstream(self, *, outline: "DocumentOutline", layout: PageLayout) -> None:
        """把本表格的文档身份与上游 outline / PageLayout 做**对象级**核对。"""
        t = "TableObject"
        if not isinstance(outline, DocumentOutline):
            _err(t, f"verify_upstream 需要 DocumentOutline 对象，得到 {type(outline).__name__}")
        if not isinstance(layout, PageLayout):
            _err(t, f"verify_upstream 需要 PageLayout 对象，得到 {type(layout).__name__}")
        if outline.outline_id != self.document_outline_id:
            _err(t, "table.document_outline_id 与上游 outline.outline_id 不一致："
                    f"{self.document_outline_id!r} != {outline.outline_id!r}")
        if outline.outline_locator != self.document_outline_locator:
            _err(t, "table.document_outline_locator 与上游 outline.outline_locator 不一致："
                    f"{self.document_outline_locator!r} != {outline.outline_locator!r}")
        if outline.document_id != self.document_id:
            _err(t, "table.document_id 与上游 outline.document_id 不一致："
                    f"{self.document_id!r} != {outline.document_id!r}")
        if layout.document_id != self.document_id:
            _err(t, "table.document_id 与上游 layout.document_id 不一致："
                    f"{self.document_id!r} != {layout.document_id!r}")
        if layout.document_version != self.document_version:
            _err(t, "table.document_version 与上游 layout.document_version 不一致："
                    f"{self.document_version!r} != {layout.document_version!r}")
        if outline.page_layout_id != layout.page_layout_id:
            _err(t, "上游 outline.page_layout_id 与 layout.page_layout_id 不一致")
        if layout.page_layout_id != self.page_layout_id:
            _err(t, "table.page_layout_id 与上游 layout.page_layout_id 不一致："
                    f"{self.page_layout_id!r} != {layout.page_layout_id!r}")
        if self.node_id is not None and outline.node_by_id(self.node_id) is None:
            _err(t, f"table.node_id={self.node_id!r} 不在上游 outline 的节点中")

    def verify_layout(self, layout: PageLayout) -> None:
        """用真实 PageLayout 核验几何完整性（未核验前**不得**声称完整）。

        核验：layout 身份必须等于 `page_layout_id`；本表页必须存在于 layout；
        表格 bbox 必须落在该页 bbox 内；每个单元格的来源 `(page, line, span)`
        必须真实存在，且其 bbox 必须**包含**对应排版片段的 bbox（单元格是来源文本的
        视觉容器，容差 `BBOX_PAGE_TOLERANCE_PT`）。

        本方法刻意不自动执行：TS1 拿不到 PageLayout，构造期无法完成这一步，
        所以把"是否核验过"的决定权显式交给调用方，而不是假装已经核验。
        """
        t = "TableObject"
        if not isinstance(layout, PageLayout):
            _err(t, f"verify_layout 需要 PageLayout 对象，得到 {type(layout).__name__}")
        if layout.page_layout_id != self.page_layout_id:
            _err(t, "table.page_layout_id 与传入 layout.page_layout_id 不一致："
                    f"{self.page_layout_id!r} != {layout.page_layout_id!r}")
        if not (1 <= self.page_number <= layout.page_count):
            _err(t, f"本表页 {self.page_number} 不在 layout 的 1..{layout.page_count} 内")
        page = layout.pages[self.page_number - 1]
        tol = BBOX_PAGE_TOLERANCE_PT
        tb = self.table_bbox
        if (tb[0] < -tol or tb[1] < -tol
                or tb[2] > page.width + tol or tb[3] > page.height + tol):
            _err(t, f"表格 bbox {tb} 必须落在页面 bbox "
                    f"{(0.0, 0.0, page.width, page.height)} 内（容差 {tol}pt）")
        for i, c in enumerate(self.cell_grid):
            pg, line_index, span_index = c.source_locator
            if pg != self.page_number:
                _err(t, f"cell_grid[{i}] 的来源页 {pg} 必须等于本表页 {self.page_number}")
            if line_index >= len(page.lines):
                _err(t, f"cell_grid[{i}] 的来源行 {line_index} 不在第 {pg} 页的 "
                        f"{len(page.lines)} 行内")
            line = page.lines[line_index]
            if span_index >= len(line.spans):
                _err(t, f"cell_grid[{i}] 的来源 span {span_index} 不在第 {pg} 页第 "
                        f"{line_index} 行的 {len(line.spans)} 个 span 内")
            sb = line.spans[span_index].bbox
            cb = c.bbox
            if (sb[0] < cb[0] - tol or sb[1] < cb[1] - tol
                    or sb[2] > cb[2] + tol or sb[3] > cb[3] + tol):
                _err(t, f"cell_grid[{i}] 的 bbox {cb} 必须包含其来源排版片段的 bbox "
                        f"{sb}（来源 {c.source_locator}，容差 {tol}pt）")

    def verify_continuation_edges(self, edges: Sequence["ReferenceEdge"]) -> None:
        """`TableObject.continuation_*` 与 `ReferenceEdge.table_continuation` 必须互相印证。

        真相分工：`continuation_*` 字段是**物理续表关系**的载体；`ReferenceEdge` 是
        同一关系在图上的**可审计投影**。二者是同一事实的两种视图，**任何一方都不得
        单独自报**：

        - 表格列出的出边目标集合必须等于图中以本表为 `from_ref` 的边目标集合；
        - 表格自报的 `continuation_of_locator` 必须与图中以本表为 `to_ref` 的边一致；
        - 图中有入边而表格自报无前置、或表格自报前置而图中无入边，都拒绝。

        入边**最多一条**：一页片段只能有一个物理前驱，多个前驱意味着关系没被真正确定。
        """
        t = "TableObject"
        table_edges = [
            e for e in edges
            if isinstance(e, ReferenceEdge) and e.edge_kind == "table_continuation"
            and e.is_resolved
        ]
        self_prefix = f"table:{self.table_locator}"
        outgoing = {e.to_ref for e in table_edges if e.from_ref == self_prefix}
        incoming = {e.from_ref for e in table_edges if e.to_ref == self_prefix}
        declared_out = {f"table:{loc}" for loc in self.continuation_locators}
        if outgoing != declared_out:
            _err(t, "表格自报的续表目标必须与 ReferenceEdge.table_continuation 的出边"
                    f"精确一致（自报 {sorted(declared_out)}，图中 {sorted(outgoing)}）")
        if len(incoming) > 1:
            _err(t, f"续表入边最多一条，图中本表有 {len(incoming)} 条前驱："
                    f"{sorted(incoming)}")
        if self.continuation_of_locator is None:
            if incoming:
                _err(t, "图中有续表入边，但表格自报无前置（不得只由一方声称关系）："
                        f"{sorted(incoming)}")
            return
        declared_in = f"table:{self.continuation_of_locator}"
        if incoming != {declared_in}:
            _err(t, "表格自报的续表前置必须与 ReferenceEdge.table_continuation 的入边精确"
                    f"一致（自报 {declared_in!r}，图中 {sorted(incoming)}）")

    def verify_continuation_fragments(self, fragments: Sequence["TableObject"]) -> None:
        """用真实的续表片段核验方向与页序：不得自指、不得跨文档、页序必须一致。

        续表是"同一张表在后一页的继续"，因此：

        - 被引用的每个定位都必须能在传入片段中解析（缺失即拒绝，不得只凭定位字符串
          相信对方存在）；
        - `continuation_of_locator` 指向的**前驱**必须在更小的页码上；
        - `continuation_locators` 指向的**后继**必须在更大的页码上；
        - 双方必须对称（前驱的后继列表里必须有本表）且属于同一文档；
        - 定位只用于**导航**，不构成对对方内容的权威：本表的单元格仍必须来自本表页
          （见 `_check_cell_grid`），不得把对方的行/cell 当作自己的内容。
        """
        t = "TableObject"
        by_locator: dict[str, TableObject] = {}
        for i, f in enumerate(fragments):
            if not isinstance(f, TableObject):
                _err(t, f"fragments[{i}] 必须为 TableObject，得到 {type(f).__name__}")
            by_locator[f.table_locator] = f
        wanted: list[str] = []
        if self.continuation_of_locator is not None:
            wanted.append(self.continuation_of_locator)
        wanted.extend(self.continuation_locators)
        for loc in wanted:
            if loc == self.table_locator:
                _err(t, "续表关系不得指向自身")
            if loc not in by_locator:
                _err(t, f"续表目标片段缺失：{loc!r} 不在传入的片段集合中"
                        "（不得只凭定位字符串相信对方存在）")

        if self.continuation_of_locator is not None:
            prev = by_locator[self.continuation_of_locator]
            if prev.document_id != self.document_id \
                    or prev.document_version != self.document_version:
                _err(t, "续表不得跨文档：前置片段属于另一个文档")
            if prev.page_number >= self.page_number:
                _err(t, f"续表页序倒置：前置片段在第 {prev.page_number} 页，"
                        f"本表在第 {self.page_number} 页（前置必须更早）")
            if self.table_locator not in prev.continuation_locators:
                _err(t, "续表关系必须对称：前置片段的后继列表必须包含本表定位")
        for loc in self.continuation_locators:
            nxt = by_locator[loc]
            if nxt.document_id != self.document_id \
                    or nxt.document_version != self.document_version:
                _err(t, f"续表不得跨文档：后继片段 {loc!r} 属于另一个文档")
            if nxt.page_number <= self.page_number:
                _err(t, f"续表页序倒置：后继片段在第 {nxt.page_number} 页，"
                        f"本表在第 {self.page_number} 页（后继必须更晚）")
            if nxt.continuation_of_locator != self.table_locator:
                _err(t, "续表关系必须对称：后继片段的前置必须指向本表定位")

    def to_dict(self) -> dict:
        return {
            "schema_type": "TableObject",
            "table_locator": self.table_locator,
            "table_id": self.table_id,
            "schema_version": self.schema_version,
            "page_layout_id": self.page_layout_id,
            "document_outline_id": self.document_outline_id,
            "document_outline_locator": self.document_outline_locator,
            "node_id": self.node_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "evidence_set_version": self.evidence_set_version,
            "page_number": self.page_number,
            "table_index_on_page": self.table_index_on_page,
            "table_bbox": _json(self.table_bbox),
            "title": self.title,
            "title_source": self.title_source,
            "unit": self.unit,
            "structure_class": self.structure_class,
            "structure_evidence": _json(self.structure_evidence),
            "column_count": self.column_count,
            "header_rows": [_json(x) for x in self.header_rows],
            "body_rows": [_json(x) for x in self.body_rows],
            "total_rows": [_json(x) for x in self.total_rows],
            "cell_grid": [_json(x) for x in self.cell_grid],
            "component_span_ids": _json(self.component_span_ids),
            "component_evidence_refs": _json(self.component_evidence_refs),
            "alignment_ids": _json(self.alignment_ids),
            "continuation_of_locator": self.continuation_of_locator,
            "continuation_locators": _json(self.continuation_locators),
            "table_builder_version": self.table_builder_version,
            "content_fingerprint": self.content_fingerprint,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TableObject":
        t = "TableObject"
        d = _reject_unknown(d, {
            "schema_type", "table_locator", "table_id", "schema_version",
            "page_layout_id", "document_outline_id", "document_outline_locator",
            "node_id", "document_id",
            "document_version", "evidence_set_version", "page_number",
            "table_index_on_page", "table_bbox", "title", "title_source", "unit",
            "structure_class", "structure_evidence", "column_count", "header_rows",
            "body_rows", "total_rows", "cell_grid", "component_span_ids",
            "component_evidence_refs", "alignment_ids", "continuation_of_locator",
            "continuation_locators", "table_builder_version", "content_fingerprint"},
            t)
        _need_enum(d, "schema_type", t, ("TableObject",))
        return cls(
            table_locator=_need_str(d, "table_locator", t),
            table_id=_need_str(d, "table_id", t),
            schema_version=_need_legacy_schema_version(
                d, "schema_version", t, "TABLE_SCHEMA_VERSION",
                V.LEGACY_TABLE_SCHEMA_VERSION),
            page_layout_id=_need_str(d, "page_layout_id", t),
            document_outline_id=_need_str(d, "document_outline_id", t),
            document_outline_locator=_need_str(d, "document_outline_locator", t),
            node_id=_need_str(d, "node_id", t, none_ok=True),
            document_id=_need_str(d, "document_id", t),
            document_version=_need_str(d, "document_version", t),
            evidence_set_version=_need_str(d, "evidence_set_version", t),
            page_number=_need_int(d, "page_number", t, lo=1),
            table_index_on_page=_need_int(d, "table_index_on_page", t, lo=0),
            table_bbox=_need_bbox(d, "table_bbox", t),
            title=_need_str(d, "title", t, none_ok=True),
            title_source=_need_enum(d, "title_source", t, TITLE_SOURCES),
            unit=_need_str(d, "unit", t, none_ok=True),
            structure_class=_need_enum(d, "structure_class", t, STRUCTURE_CLASSES),
            structure_evidence=_need_str_tuple(d, "structure_evidence", t),
            column_count=_need_int(d, "column_count", t, lo=2),
            header_rows=_need_children(d, "header_rows", t, TableRow.from_dict),
            body_rows=_need_children(d, "body_rows", t, TableRow.from_dict),
            total_rows=_need_children(d, "total_rows", t, TableRow.from_dict),
            cell_grid=_need_cell_grid(d, "cell_grid", t),
            component_span_ids=_need_str_tuple(d, "component_span_ids", t),
            component_evidence_refs=_need_evidence_refs(d, "component_evidence_refs", t),
            alignment_ids=_need_str_tuple(d, "alignment_ids", t),
            continuation_of_locator=_need_str(d, "continuation_of_locator", t,
                                              none_ok=True),
            continuation_locators=_need_str_tuple(d, "continuation_locators", t),
            table_builder_version=_need_str(d, "table_builder_version", t),
            content_fingerprint=_need_sha256(d, "content_fingerprint", t),
        )

    @classmethod
    def create(cls, *, page_layout_id: str, document_outline_id: str,
               document_outline_locator: str,
               node_id: str | None, document_id: str, document_version: str,
               evidence_set_version: str, page_number: int, table_index_on_page: int,
               table_bbox: tuple[float, float, float, float], title: str | None,
               title_source: str, unit: str | None, structure_class: str,
               structure_evidence: tuple[str, ...], column_count: int,
               header_rows: tuple[TableRow, ...], body_rows: tuple[TableRow, ...],
               cell_grid: tuple[TableCell, ...],
               component_span_ids: tuple[str, ...],
               alignment_ids: tuple[str, ...],
               total_rows: tuple[TableRow, ...] = (),
               component_evidence_refs: tuple[tuple[str, int, int], ...] = (),
               continuation_of_locator: str | None = None,
               continuation_locators: tuple[str, ...] = (),
               table_builder_version: str = V.LEGACY_TABLE_BUILDER_VERSION,
               ) -> "TableObject":
        loc = derive_table_locator(
            page_layout_id=page_layout_id, page_number=page_number,
            table_index_on_page=table_index_on_page, table_bbox=table_bbox,
            table_builder_version=table_builder_version)
        cf = derive_table_content_fingerprint(
            schema_version=V.LEGACY_TABLE_SCHEMA_VERSION,
            document_outline_id=document_outline_id,
            document_outline_locator=document_outline_locator, node_id=node_id,
            document_id=document_id, document_version=document_version,
            evidence_set_version=evidence_set_version, page_number=page_number,
            table_index_on_page=table_index_on_page, table_bbox=table_bbox,
            title=title, title_source=title_source, unit=unit,
            structure_class=structure_class, structure_evidence=structure_evidence,
            column_count=column_count, header_rows=header_rows, body_rows=body_rows,
            total_rows=total_rows, cell_grid=cell_grid,
            component_span_ids=component_span_ids,
            component_evidence_refs=component_evidence_refs,
            alignment_ids=alignment_ids,
            continuation_of_locator=continuation_of_locator,
            continuation_locators=continuation_locators)
        return cls(
            table_locator=loc,
            table_id=derive_table_id(table_locator=loc, content_fingerprint=cf),
            schema_version=V.LEGACY_TABLE_SCHEMA_VERSION,
            page_layout_id=page_layout_id,
            document_outline_id=document_outline_id,
            document_outline_locator=document_outline_locator, node_id=node_id,
            document_id=document_id, document_version=document_version,
            evidence_set_version=evidence_set_version, page_number=page_number,
            table_index_on_page=table_index_on_page, table_bbox=table_bbox,
            title=title, title_source=title_source, unit=unit,
            structure_class=structure_class, structure_evidence=structure_evidence,
            column_count=column_count, header_rows=header_rows, body_rows=body_rows,
            total_rows=total_rows, cell_grid=cell_grid,
            component_span_ids=component_span_ids,
            component_evidence_refs=component_evidence_refs,
            alignment_ids=alignment_ids,
            continuation_of_locator=continuation_of_locator,
            continuation_locators=continuation_locators,
            table_builder_version=table_builder_version, content_fingerprint=cf,
        )


def derive_table_locator(*, page_layout_id: str, page_number: int,
                         table_index_on_page: int, table_bbox: tuple,
                         table_builder_version: str) -> str:
    """`loc-to-<sha256[:16]>`：稳定定位（页 + 页内序号 + bbox + builder 版本）。"""
    return locator("to", {
        "page_layout_id": page_layout_id,
        "page_number": page_number,
        "table_index_on_page": table_index_on_page,
        "table_bbox": _json(tuple(quantize(x) for x in table_bbox)),
        "table_builder_version": table_builder_version,
    })


def derive_table_id(*, table_locator: str, content_fingerprint: str) -> str:
    """`to-<sha256[:16]>`：不可变 revision 身份（= 内容指纹前 16 位）。"""
    return identity("to", {
        "table_locator": table_locator,
        "content_fingerprint": content_fingerprint,
    })


def _canon_cells(rows: tuple) -> list:
    """行的身份规范化形：文本空白折叠（只有空白差异时身份不变）。"""
    return [
        {"row_index": r.row_index, "kind": r.kind,
         "cells": [canonical_text(c) for c in r.cells],
         "label": None if r.label is None else canonical_text(r.label)}
        for r in rows
    ]


def _canon_grid(grid: tuple) -> list:
    return [
        {"row": c.row, "column": c.column, "rowspan": c.rowspan, "colspan": c.colspan,
         "text": canonical_text(c.text), "bbox": _json(c.bbox),
         "source_locator": _json(c.source_locator)}
        for c in grid
    ]


def derive_table_content_fingerprint(*, schema_version: str, document_outline_id: str,
                                     node_id: str | None, document_id: str,
                                     document_version: str, evidence_set_version: str,
                                     page_number: int, table_index_on_page: int,
                                     table_bbox: tuple, title: str | None,
                                     document_outline_locator: str,
                                     title_source: str, unit: str | None,
                                     structure_class: str, structure_evidence: tuple,
                                     column_count: int, header_rows: tuple,
                                     body_rows: tuple, total_rows: tuple,
                                     cell_grid: tuple, component_span_ids: tuple,
                                     component_evidence_refs: tuple,
                                     alignment_ids: tuple,
                                     continuation_of_locator: str | None,
                                     continuation_locators: tuple) -> str:
    """表格内容指纹：全部业务内容（文本经空白折叠）+ 上游身份与来源版本。

    续表关系用**定位**参与指纹：它属于该表的规范形（变了身份就必须变），
    但定位与对方内容无关，因此两张表可以互相指向而不产生构造循环。

    `document_outline_locator` 与 `document_outline_id` 同时进入指纹：前者是稳定定位
    （用于与 span 的 `document_outline_locator` 做对象级核对），后者是 revision 身份
    （用于与 outline 的 `outline_id` 核对）；两者缺一都会漏掉一类上游变化。
    """
    return sha256_canonical({
        "schema_version": schema_version,
        "document_outline_id": document_outline_id,
        "document_outline_locator": document_outline_locator,
        "node_id": node_id,
        "document_id": document_id,
        "document_version": document_version,
        "evidence_set_version": evidence_set_version,
        "page_number": page_number,
        "table_index_on_page": table_index_on_page,
        "table_bbox": _json(tuple(quantize(x) for x in table_bbox)),
        "title": None if title is None else canonical_text(title),
        "title_source": title_source,
        "unit": None if unit is None else canonical_text(unit),
        "structure_class": structure_class,
        "structure_evidence": _json(structure_evidence),
        "column_count": column_count,
        "header_rows": _canon_cells(header_rows),
        "body_rows": _canon_cells(body_rows),
        "total_rows": _canon_cells(total_rows),
        "cell_grid": _canon_grid(cell_grid),
        "component_span_ids": _json(component_span_ids),
        "component_evidence_refs": _json(component_evidence_refs),
        "alignment_ids": _json(alignment_ids),
        "continuation_of_locator": continuation_of_locator,
        "continuation_locators": _json(continuation_locators),
    })


# ---------------------------------------------------------------------------
# 8. 引用核验上下文与核验结论
# ---------------------------------------------------------------------------
# TS1.2 P1-2：上一轮的 `ReferenceTargetRegistry` 是"调用方传入的字符串清单"，
# 把任意伪造目标字符串回填进集合就能让边变成 `is_resolved=True`。字符串清单**不是**
# 对象存在证明。本轮把它替换成三个职责分离的运行时类型：
#
#   `TocSource`                  —— 目录项的**真实来源身份**（绑定真实 LayoutLine）；
#   `ReferenceValidationContext` —— 输入：调用方交进来的**真实对象**（含真实 PageLayout）；
#   `VerifiedReferences`         —— 输出：对象级核验的结论（"引用已验证"的唯一载体）。
#
# 三者都**不**持久化，都不进入 `DocumentOutline` 的 JSON / fingerprint / id。


@dataclass(frozen=True)
class TocSource:
    """一个**目录项来源**的真实身份：绑定真实 `PageLayout` 上的真实 `LayoutLine`。

    TS1.3 §四.3/§四.4：上一轮把"目录项在本层没有类型化文本载体"扩大成一条永久结构
    禁令（`toc_to_body` 一律不得 resolved），与已批准的树结构方向冲突——目录项必须是
    重要导航候选。本轮为它补上**由真实版式重算**的来源身份，并且刻意**不**造
    "只包字符串的假 Toc 对象"、也**不**恢复字符串 `toc_ids` 注册表：

      toc_source_locator = loc-toc-<h(page_layout_id, document_id, document_version,
                                      page_number, line_index, char_start, char_end)>
      toc_source_id      = toc-<h(toc_source_locator, toc_builder_version,
                                  line_text_sha256, entry_text, declared_page_label)>

    位置的每一部分、来源行的内容摘要、目录项文本与声明的页标签**全部**来自真实
    `LayoutLine`，因此"调用方自报同值字符串"无法通过：行内容变了、区间挪了、标签
    改了，身份都随之改变（`verify_against_layout` 会逐项重算比对）。

    `declared_page_label` 是目录行里**真实出现**的页标签（必须逐字符出现在
    `entry_text` 中）。它与 PDF 物理页码之间的映射由本层在真实版式的页码 furniture
    上重算（`_resolve_page_label`），不做"最近页面 / 固定偏移"猜测；无法唯一确定时
    边必须保持未解析。

    本类型是**运行时核验输入**：不携带 `schema_version`，不进入任何 wire format。
    """

    toc_source_locator: str
    toc_source_id: str
    page_layout_id: str
    document_id: str
    document_version: str
    page_number: int
    line_index: int
    char_start: int
    char_end: int
    line_text_sha256: str
    entry_text: str
    declared_page_label: str
    toc_builder_version: str

    def __post_init__(self) -> None:
        t = "TocSource"
        for name in ("toc_source_locator", "toc_source_id", "page_layout_id",
                     "document_id", "document_version", "entry_text",
                     "declared_page_label"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串，得到 {v!r}")
        if not self.toc_source_locator.startswith(TOC_SOURCE_LOCATOR_PREFIX):
            _err(t, f"toc_source_locator 必须以 {TOC_SOURCE_LOCATOR_PREFIX!r} 开头，"
                    f"得到 {self.toc_source_locator!r}")
        _check_version(t, "toc_builder_version", self.toc_builder_version,
                       V.TOC_SOURCE_BUILDER_VERSION)
        for name in ("page_number", "line_index", "char_start", "char_end"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool):
                _err(t, f"{name} 必须为 int，得到 {v!r}")
        if self.page_number < 1:
            _err(t, f"页码必须从 1 开始，得到 {self.page_number}")
        if self.line_index < 0:
            _err(t, f"line_index 必须为非负，得到 {self.line_index}")
        if self.char_start < 0:
            _err(t, f"char_start 必须为非负，得到 {self.char_start}")
        if self.char_end <= self.char_start:
            _err(t, "目录项字符区间必须为正长度："
                    f"[{self.char_start}, {self.char_end})")
        if self.char_end - self.char_start != len(self.entry_text):
            _err(t, "字符区间长度必须等于目录项文本长度："
                    f"[{self.char_start}, {self.char_end}) 长度 "
                    f"{self.char_end - self.char_start} vs {len(self.entry_text)}")
        if not _RE_SHA256_HEX.match(self.line_text_sha256):
            _err(t, "line_text_sha256 必须为该目录项所在真实 LayoutLine 文本的 "
                    "64 位小写十六进制 sha256")
        if self.declared_page_label not in self.entry_text:
            _err(t, f"declared_page_label {self.declared_page_label!r} 必须**逐字符**"
                    f"出现在真实目录项文本 {self.entry_text!r} 中；"
                    f"自报一个原文里没有的页标签不是来源证据")
        expected_loc = derive_toc_source_locator(
            page_layout_id=self.page_layout_id, document_id=self.document_id,
            document_version=self.document_version, page_number=self.page_number,
            line_index=self.line_index, char_start=self.char_start,
            char_end=self.char_end)
        if self.toc_source_locator != expected_loc:
            _err(t, "toc_source_locator 与派生定位身份不一致："
                    f"{self.toc_source_locator!r} != {expected_loc!r}")
        expected_id = derive_toc_source_id(
            toc_source_locator=self.toc_source_locator,
            toc_builder_version=self.toc_builder_version,
            line_text_sha256=self.line_text_sha256, entry_text=self.entry_text,
            declared_page_label=self.declared_page_label)
        if self.toc_source_id != expected_id:
            _err(t, "toc_source_id 与派生修订身份不一致（行内容 / 文本 / 页标签已变）："
                    f"{self.toc_source_id!r} != {expected_id!r}")

    @classmethod
    def create(cls, *, layout: "PageLayout", page_number: int, line_index: int,
               char_start: int, char_end: int, declared_page_label: str,
               toc_builder_version: str = V.TOC_SOURCE_BUILDER_VERSION
               ) -> "TocSource":
        """由**真实 `PageLayout`** 上的真实行重算目录项来源身份。

        这里不接受任何"页码 / 行号 / 文本"的自报参数：`entry_text`、
        `line_text_sha256` 与身份全部由 `layout.line_at(...)` 取到的真实行算出。
        """
        t = "TocSource"
        if not isinstance(layout, PageLayout):
            _err(t, f"create 需要真实 PageLayout 对象，得到 {type(layout).__name__}；"
                    f"目录项来源身份必须由真实版式重算")
        line = layout.line_at(page_number, line_index)
        if line is None:
            _err(t, f"页码 {page_number} / 行号 {line_index} 在给定 PageLayout 上不存在"
                    f"对应 LayoutLine；不得凭空声明一个目录项来源")
        if not isinstance(char_start, int) or isinstance(char_start, bool) \
                or not isinstance(char_end, int) or isinstance(char_end, bool):
            _err(t, "char_start / char_end 必须为 int")
        if char_start < 0 or char_end <= char_start:
            _err(t, f"目录项字符区间必须为正长度：[{char_start}, {char_end})")
        if char_end > len(line.text):
            _err(t, f"目录项字符区间 [{char_start}, {char_end}) 超出真实 LayoutLine "
                    f"文本长度 {len(line.text)}")
        entry_text = line.text[char_start:char_end]
        line_text_sha256 = sha256_canonical(line.text)
        source_locator = derive_toc_source_locator(
            page_layout_id=layout.page_layout_id, document_id=layout.document_id,
            document_version=layout.document_version, page_number=page_number,
            line_index=line_index, char_start=char_start, char_end=char_end)
        return cls(
            toc_source_locator=source_locator,
            toc_source_id=derive_toc_source_id(
                toc_source_locator=source_locator,
                toc_builder_version=toc_builder_version,
                line_text_sha256=line_text_sha256, entry_text=entry_text,
                declared_page_label=declared_page_label),
            page_layout_id=layout.page_layout_id, document_id=layout.document_id,
            document_version=layout.document_version, page_number=page_number,
            line_index=line_index, char_start=char_start, char_end=char_end,
            line_text_sha256=line_text_sha256, entry_text=entry_text,
            declared_page_label=declared_page_label,
            toc_builder_version=toc_builder_version)

    def verify_against_layout(self, layout: "PageLayout") -> None:
        """在真实 `PageLayout` 上逐项重算本来源身份；任一项不一致即拒绝。"""
        t = "TocSource"
        if not isinstance(layout, PageLayout):
            _err(t, f"verify_against_layout 需要真实 PageLayout 对象，"
                    f"得到 {type(layout).__name__}")
        if layout.page_layout_id != self.page_layout_id:
            _err(t, "本目录项来源绑定的 page_layout_id 与给定真实 PageLayout 不一致："
                    f"{self.page_layout_id!r} != {layout.page_layout_id!r}")
        if layout.document_id != self.document_id:
            _err(t, "本目录项来源属于另一文档："
                    f"{self.document_id!r} != {layout.document_id!r}")
        if layout.document_version != self.document_version:
            _err(t, "本目录项来源的文档版本与给定真实 PageLayout 不一致："
                    f"{self.document_version!r} != {layout.document_version!r}")
        line = layout.line_at(self.page_number, self.line_index)
        if line is None:
            _err(t, f"页码 {self.page_number} / 行号 {self.line_index} 在给定真实 "
                    f"PageLayout 上不存在对应 LayoutLine")
        if sha256_canonical(line.text) != self.line_text_sha256:
            _err(t, "本目录项来源记录的来源行内容摘要与真实 LayoutLine 不一致"
                    "（该行的文本已改变，来源身份必须重算）")
        actual = line.text[self.char_start:self.char_end]
        if actual != self.entry_text:
            _err(t, "本目录项来源记录的目录项文本与真实 LayoutLine 的字符区间"
                    f"**逐字符**不一致：{actual!r} != {self.entry_text!r}")

    def to_dict(self) -> dict:
        return {
            "schema_type": "TocSource",
            "toc_source_locator": self.toc_source_locator,
            "toc_source_id": self.toc_source_id,
            "page_layout_id": self.page_layout_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "page_number": self.page_number,
            "line_index": self.line_index,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "line_text_sha256": self.line_text_sha256,
            "entry_text": self.entry_text,
            "declared_page_label": self.declared_page_label,
            "toc_builder_version": self.toc_builder_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "TocSource":
        t = "TocSource"
        d = _reject_unknown(d, {
            "schema_type", "toc_source_locator", "toc_source_id", "page_layout_id",
            "document_id", "document_version", "page_number", "line_index",
            "char_start", "char_end", "line_text_sha256", "entry_text",
            "declared_page_label", "toc_builder_version"}, t)
        _need_enum(d, "schema_type", t, ("TocSource",))
        return cls(
            toc_source_locator=_need_str(d, "toc_source_locator", t),
            toc_source_id=_need_str(d, "toc_source_id", t),
            page_layout_id=_need_str(d, "page_layout_id", t),
            document_id=_need_str(d, "document_id", t),
            document_version=_need_str(d, "document_version", t),
            page_number=_need_int(d, "page_number", t, lo=1),
            line_index=_need_int(d, "line_index", t, lo=0),
            char_start=_need_int(d, "char_start", t, lo=0),
            char_end=_need_int(d, "char_end", t, lo=1),
            line_text_sha256=_need_sha256(d, "line_text_sha256", t),
            entry_text=_need_str(d, "entry_text", t),
            declared_page_label=_need_str(d, "declared_page_label", t),
            toc_builder_version=_need_str(d, "toc_builder_version", t),
        )


def derive_toc_source_locator(*, page_layout_id: str, document_id: str,
                              document_version: str, page_number: int,
                              line_index: int, char_start: int,
                              char_end: int) -> str:
    """`loc-toc-<sha256[:16]>`：目录项来源的**稳定定位身份**。

    只描述"这个目录项在真实版式的哪里"（哪份 layout、哪一页、哪一行、行内哪一段），
    与目录项文本内容无关，因此同一目录项的重解析（文本有细微差异）仍能定位回同一处。
    """
    return locator("toc", {
        "page_layout_id": page_layout_id,
        "document_id": document_id,
        "document_version": document_version,
        "page_number": page_number,
        "line_index": line_index,
        "char_start": char_start,
        "char_end": char_end,
    })


def derive_toc_source_id(*, toc_source_locator: str, toc_builder_version: str,
                         line_text_sha256: str, entry_text: str,
                         declared_page_label: str) -> str:
    """`toc-<sha256[:16]>`：目录项来源的**不可变修订身份**。

    在 locator 之上绑定来源构建规则版本、**真实行内容摘要**、目录项文本与页标签：
    行内容、区间、文本或标签任一改变，来源身份必须改变——这正是"自报同值字符串"
    不能冒充真实来源的原因。
    """
    return identity("toc", {
        "toc_source_locator": toc_source_locator,
        "toc_builder_version": toc_builder_version,
        "line_text_sha256": line_text_sha256,
        "entry_text": entry_text,
        "declared_page_label": declared_page_label,
    })


@dataclass(frozen=True)
class ReferenceValidationContext:
    """**运行时**引用核验上下文：本层可核验的真实对象集合。

    为什么需要它：`table:` 端点的对象由 TS5 产生，本层构造不出来；`node:` / `span:`
    端点中，节点内正文 span 也不在 `DocumentOutline` 自持集合里。调用方一次把真实
    对象交进来，本层才可能做**对象级**核验。

    信任边界（逐条，TS1.2 §三 + TS1.3 §五）：

    1. 它**不是** `DocumentOutline` 的内容：不进入 `to_dict` / `from_dict`，不进入
       `content_fingerprint` / `outline_id`。同一 layout / nodes / edges /
       unassigned / candidate_sources 在不同但等价的上下文下必须得到逐字节相同的
       outline JSON 与相同身份（P1-1）。
    2. 它只接受**真实对象**，不接受任何字符串清单，因此"同值回填"无法伪造存在性。
    3. 它只能证明自己**持有**的对象：`evidence:` 在本层没有对象类型
       （`UNRESOLVABLE_REF_KINDS`），因此带该端点的边一律不得 resolved。
    4. 本层不构建、不推断、不联网获取任何对象；同一 `table_locator` 对应两个不同
       revision、同一 `span_id` 重复出现、同一 `toc_source_id` 重复出现时一律
       fail-closed（歧义不得消解）。
    5. `resolver_version` 使"结论是用哪一版核验规则得出的"可审计；`fingerprint()`
       使"结论是在哪组对象、哪几版子规则上得出的"可复核。
    6. **TS1.3 §三.10**：`layout` 必须是真实 `PageLayout` 对象。occurrence 的
       `(页, 行)` 是否存在、该行文本是什么、行内字符区间切出什么，只能在真实版式上
       重算；用"页码 / 行号字符串清单"代替它一律不接受。
    7. **TS1.3 §五**：持有本对象（或持有 `VerifiedReferences` 实例）**本身**不是
       "引用已验证"的证明。正式结论只能由 `DocumentOutline.verify_references(this)`
       确定性派生，并可用 `VerifiedReferences.assert_reproducible(outline, context)`
       在相同的 Outline + context 上重算比对。
    """

    tables: tuple["TableObject", ...] = ()
    spans: tuple["OutlineSpan", ...] = ()
    toc_sources: tuple["TocSource", ...] = ()
    layout: "PageLayout | None" = None
    resolver_version: str = V.REFERENCE_RESOLVER_VERSION

    def __post_init__(self) -> None:
        t = "ReferenceValidationContext"
        if not isinstance(self.resolver_version, str) or self.resolver_version == "":
            _err(t, "resolver_version 必须为非空字符串")
        for name, cls in (("tables", TableObject), ("spans", OutlineSpan),
                          ("toc_sources", TocSource)):
            v = getattr(self, name)
            if not isinstance(v, tuple):
                _err(t, f"{name} 必须为真实对象的元组，得到 {type(v).__name__}")
            for i, x in enumerate(v):
                if not isinstance(x, cls):
                    _err(t, f"{name}[{i}] 必须为真实 {cls.__name__} 对象，"
                            f"得到 {type(x).__name__}（字符串清单不得证明对象存在）")
        if self.layout is not None and not isinstance(self.layout, PageLayout):
            _err(t, "layout 必须为真实 PageLayout 对象或 None，"
                    f"得到 {type(self.layout).__name__}（页码/行号字符串清单不是版式）")
        seen_loc: dict[str, str] = {}
        for tb in self.tables:
            prev = seen_loc.get(tb.table_locator)
            if prev is not None and prev != tb.table_id:
                _err(t, f"table_locator {tb.table_locator!r} 同时对应两个 revision "
                        f"（{prev!r} / {tb.table_id!r}）：歧义必须 fail-closed")
            seen_loc[tb.table_locator] = tb.table_id
        seen_span: set[str] = set()
        for sp in self.spans:
            if sp.span_id in seen_span:
                _err(t, f"spans 中 span_id 不得重复：{sp.span_id!r}")
            seen_span.add(sp.span_id)
        seen_toc: set[str] = set()
        for ts in self.toc_sources:
            if ts.toc_source_id in seen_toc:
                _err(t, f"toc_sources 中 toc_source_id 不得重复：{ts.toc_source_id!r}")
            seen_toc.add(ts.toc_source_id)

    def table_by_locator(self, locator_value: str) -> "TableObject | None":
        for tb in self.tables:
            if tb.table_locator == locator_value:
                return tb
        return None

    def span_by_id(self, span_id: str) -> "OutlineSpan | None":
        for sp in self.spans:
            if sp.span_id == span_id:
                return sp
        return None

    def toc_source_by_id(self, toc_source_id: str) -> "TocSource | None":
        """按**修订身份**取真实目录项来源；不存在返回 None（不得靠字符串形状接受）。"""
        for ts in self.toc_sources:
            if ts.toc_source_id == toc_source_id:
                return ts
        return None

    def provided_kinds(self) -> tuple[str, ...]:
        """本上下文持有真实对象的引用类型（用于诊断"哪些对象还没有提供"）。"""
        kinds = []
        if self.spans:
            kinds.append("span")
        if self.tables:
            kinds.append("table")
        if self.toc_sources:
            kinds.append("toc")
        return tuple(kinds)

    def fingerprint(self) -> str:
        """本核验上下文的可复核指纹。

        组成：核验器版本 + 三套 TOC 子规则的版本 + 所持真实对象的稳定定位与修订身份
        + 真实 `PageLayout` 的定位与修订身份。

        `node:` 由 outline 自持，不属于上下文；因此这里只列上下文持有的对象。
        加上子规则版本是刻意的：同一个 context 在不同标题归一 / 页标签映射规则下可能
        得到不同结论，因此"结论是在哪几版规则 + 哪组真实对象上得出的"必须一起绑定。
        """
        return sha256_canonical({
            "resolver_version": self.resolver_version,
            "toc_rule_versions": (V.TOC_SOURCE_BUILDER_VERSION,
                                  V.TOC_TITLE_MATCH_VERSION,
                                  V.TOC_PAGE_LABEL_MATCH_VERSION),
            "layout": None if self.layout is None else
                      (self.layout.page_layout_locator, self.layout.page_layout_id),
            "spans": sorted(((sp.span_id, sp.span_locator) for sp in self.spans)),
            "tables": sorted(((tb.table_locator, tb.table_id) for tb in self.tables)),
            "toc_sources": sorted(((ts.toc_source_locator, ts.toc_source_id)
                                   for ts in self.toc_sources)),
        })


@dataclass(frozen=True)
class VerifiedReferences:
    """**对象级核验结论**：`DocumentOutline.verify_references` 的唯一返回值。

    这是"引用已验证"的唯一载体：`ReferenceEdge.is_resolved` 只是结构层**声明**
    （见其真值表），只有在真实对象上核验通过、并由本对象登记之后，才可以对外
    声称该引用已解析。本对象同样不持久化、不进入内容身份。
    """

    outline_locator: str
    outline_id: str
    resolver_version: str
    context_fingerprint: str
    verified_edges: tuple["ReferenceEdge", ...]
    unresolved_edges: tuple["ReferenceEdge", ...]

    def __post_init__(self) -> None:
        t = "VerifiedReferences"
        for name in ("outline_locator", "outline_id", "resolver_version",
                     "context_fingerprint"):
            v = getattr(self, name)
            if not isinstance(v, str) or v == "":
                _err(t, f"{name} 必须为非空字符串")
        if not _RE_SHA256_HEX.match(self.context_fingerprint):
            _err(t, "context_fingerprint 必须为 64 位小写十六进制 sha256")
        for name in ("verified_edges", "unresolved_edges"):
            v = getattr(self, name)
            if not isinstance(v, tuple):
                _err(t, f"{name} 必须为元组")
            for i, e in enumerate(v):
                if not isinstance(e, ReferenceEdge):
                    _err(t, f"{name}[{i}] 必须为 ReferenceEdge，得到 {type(e).__name__}")
        for e in self.verified_edges:
            if not e.is_resolved:
                _err(t, f"verified_edges 只能包含已解析边，得到 {e.edge_id!r}")
        for e in self.unresolved_edges:
            if e.is_resolved:
                _err(t, f"unresolved_edges 只能包含未解析边，得到 {e.edge_id!r}")

    def verified_edge_ids(self) -> tuple[str, ...]:
        return tuple(e.edge_id for e in self.verified_edges)

    def unresolved_edge_ids(self) -> tuple[str, ...]:
        return tuple(e.edge_id for e in self.unresolved_edges)

    def is_verified(self, edge_id: str) -> bool:
        """该边是否在本次核验中**被真实对象证明**过（不是"它自称已解析"）。"""
        return any(e.edge_id == edge_id for e in self.verified_edges)

    def assert_reproducible(self, outline: "DocumentOutline",
                            context: "ReferenceValidationContext | None" = None) -> None:
        """下游信任边界（TS1.3 §五.3/§五.4）：用 Outline + context **重算**并比对。

        持有本对象（或"存在一个 `VerifiedReferences` 实例"）**本身**不是证明：它只是
        一次核验调用的返回值，可以被构造、被传递、被张冠李戴。因此接收方必须能用
        `outline` 与 `context` 重新得出同一结论；任何一项对不上（outline 定位 / 修订
        身份、核验器版本、上下文指纹、verified / unresolved 边集合）即拒绝。

        本方法把这条信任边界做成**可调用、可测试**的检查，而不是一句文档承诺。
        """
        t = "VerifiedReferences"
        if not isinstance(outline, DocumentOutline):
            _err(t, "assert_reproducible 需要真实 DocumentOutline 对象，"
                    f"得到 {type(outline).__name__}")
        fresh = outline.verify_references(context)
        for name in ("outline_locator", "outline_id", "resolver_version",
                     "context_fingerprint"):
            mine, theirs = getattr(self, name), getattr(fresh, name)
            if mine != theirs:
                _err(t, f"结论无法在给定的 Outline + 核验上下文上重现：{name} 不一致"
                        f"（{mine!r} != {theirs!r}）")
        if self.verified_edge_ids() != fresh.verified_edge_ids():
            _err(t, "结论无法重现：verified 边集合不一致"
                    f"（{self.verified_edge_ids()!r} != {fresh.verified_edge_ids()!r}）")
        if self.unresolved_edge_ids() != fresh.unresolved_edge_ids():
            _err(t, "结论无法重现：unresolved 边集合不一致"
                    f"（{self.unresolved_edge_ids()!r} != "
                    f"{fresh.unresolved_edge_ids()!r}）")

    def to_dict(self) -> dict:
        return {
            "schema_type": "VerifiedReferences",
            "outline_locator": self.outline_locator,
            "outline_id": self.outline_id,
            "resolver_version": self.resolver_version,
            "context_fingerprint": self.context_fingerprint,
            "verified_edge_ids": list(self.verified_edge_ids()),
            "unresolved_edge_ids": list(self.unresolved_edge_ids()),
        }


# ---------------------------------------------------------------------------
# 9. 引用出现位置：ReferenceOccurrence
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReferenceOccurrence:
    """一次**真实文本出现**的精确位置（`ReferenceEdge` 的正式组成部分）。

    为什么必须是正式子结构：上一轮只用一个 `occurrence_anchor=(page, line)` 粗略锚点，
    于是"同一节点对同一目标的两处 详见"会得到**相同的 locator 与相同的 id**，
    两条真实引用被并成一条（这正是早期"多个 如下表 / 详见被合并"缺陷的成因）。
    现在位置精度是字符级：`char_start` / `char_end` 一起进入 edge locator。

    区间语义（TS1.3 §三.1–§三.3 统一后的坐标）：
    `(page_number, line_index)` **精确定位一条真实 `LayoutLine`**；
    `[char_start, char_end)` 是**该 `LayoutLine.text` 内**的字符区间，不是整个
    `OutlineSpan` 全文的全局偏移。因此：

    - `char_end - char_start` 必须恰好等于 `normalized_text` 的长度；
    - `normalized_text` 必须**逐字符**等于 `layout_line.text[char_start:char_end]`；
    - `reference_marker` 与 `declared_target` 必须出现在**这个真实切片**里。

    上一轮把区间解释成"来源对象归一文本里的全局偏移"，导致两个方向都错：覆盖
    第 2–3 行的 span 中，引用其实落在第 3 行时会被**错误拒绝**；而
    `page=999 / line=999` 的伪造坐标反而能通过（因为只做长度自洽检查）。
    只检查长度、不检查 marker 与真实行文本的关系，也会让"偏移与 marker 不匹配"的
    错误对象照样通过。

    `declared_target` 保留原文里声明的目标文字（如"第一节"、"下表"）。未解析边**必须**
    保留它，且**不得**把它猜成一个 id。
    """

    source_ref: str
    page_number: int
    line_index: int
    char_start: int
    char_end: int
    occurrence_index: int
    reference_marker: str
    reference_kind: str
    declared_target: str | None
    normalized_text: str

    def __post_init__(self) -> None:
        t = "ReferenceOccurrence"
        if not isinstance(self.source_ref, str) or not _RE_REF.match(self.source_ref):
            _err(t, f"source_ref 必须形如 <kind>:<id> 且 kind ∈ {REF_KINDS}，"
                    f"得到 {self.source_ref!r}")
        for name in ("page_number", "line_index", "char_start", "char_end",
                     "occurrence_index"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool):
                _err(t, f"{name} 必须为 int，得到 {v!r}")
        if self.page_number < 1:
            _err(t, f"页码必须从 1 开始，得到 {self.page_number}")
        if self.line_index < 0:
            _err(t, f"line_index 必须为非负，得到 {self.line_index}")
        if self.occurrence_index < 0:
            _err(t, f"occurrence_index 必须为非负，得到 {self.occurrence_index}")
        if self.char_start < 0:
            _err(t, f"char_start 必须为非负，得到 {self.char_start}")
        if not isinstance(self.normalized_text, str) or self.normalized_text == "":
            _err(t, "normalized_text 必须为非空字符串")
        if self.char_end <= self.char_start:
            _err(t, "字符区间必须为**正长度**（char_end 必须大于 char_start）："
                    f"[{self.char_start}, {self.char_end})")
        if self.char_end - self.char_start != len(self.normalized_text):
            _err(t, "字符区间长度必须等于该区间归一文本长度（offset 与文本必须一致）："
                    f"[{self.char_start}, {self.char_end}) 长度 "
                    f"{self.char_end - self.char_start} vs {len(self.normalized_text)}")
        if not isinstance(self.reference_marker, str) or self.reference_marker == "":
            _err(t, "reference_marker 必须为非空字符串")
        marker = canonical_text(self.reference_marker)
        text = canonical_text(self.normalized_text)
        if marker not in text:
            _err(t, "reference_marker 必须出现在该 occurrence 的归一文本区间中："
                    f"{self.reference_marker!r} 不在 {self.normalized_text!r} 中")
        if self.reference_kind not in EDGE_KINDS:
            _err(t, f"reference_kind 必须属于 {EDGE_KINDS}，得到 {self.reference_kind!r}")
        if self.declared_target is not None:
            if not isinstance(self.declared_target, str) or self.declared_target == "":
                _err(t, "declared_target 必须为非空字符串或 None")

    def verify_source_text(self, source_text: str) -> None:
        """用**该 occurrence 所定位的真实 `LayoutLine.text`** 复核它。

        TS1.3 §三.2/§三.3：`char_start` / `char_end` 是**这一行文本内**的字符区间，
        切片必须与 `normalized_text` **逐字符**相等——不得只做 canonical /
        空白折叠后相等（那会把"详见 第一节标题"与"详见第一节标题"混为一谈，也会让
        整段被重新排版过的伪造文本通过）。marker 与原文声明的目标文字必须出现在
        **这个真实切片**里。

        本方法只是可复核的辅助入口；**强制**核验发生在
        `DocumentOutline.verify_references`（已解析边的唯一正式入口），由它负责先
        把 `(page_number, line_index)` 落到真实行上再调用本方法。
        """
        t = "ReferenceOccurrence"
        if not isinstance(source_text, str):
            _err(t, f"verify_source_text 需要字符串来源原文，得到 {type(source_text).__name__}")
        if self.char_end > len(source_text):
            _err(t, f"来源原文长度 {len(source_text)} 不足以覆盖 occurrence 的**行内**字符"
                    f"区间 [{self.char_start}, {self.char_end})")
        actual = source_text[self.char_start:self.char_end]
        if actual != self.normalized_text:
            _err(t, "occurrence 的行内字符区间在来源行原文中对应的文本必须与 "
                    f"normalized_text **逐字符**相等：{actual!r} != "
                    f"{self.normalized_text!r}（不得只做归一化 / 空白折叠后相等）")
        if canonical_text(self.reference_marker) not in canonical_text(actual):
            _err(t, "reference_marker 未出现在来源行的该字符区间中："
                    f"{self.reference_marker!r} 不在 {actual!r} 中")
        if self.declared_target is not None \
                and canonical_text(self.declared_target) not in canonical_text(actual):
            _err(t, "declared_target 是**原文里声明的目标文字**，必须出现在来源行的"
                    f"该字符区间中：{self.declared_target!r} 不在 {actual!r} 中")

    def to_dict(self) -> dict:
        return {
            "schema_type": "ReferenceOccurrence",
            "source_ref": self.source_ref,
            "page_number": self.page_number,
            "line_index": self.line_index,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "occurrence_index": self.occurrence_index,
            "reference_marker": self.reference_marker,
            "reference_kind": self.reference_kind,
            "declared_target": self.declared_target,
            "normalized_text": self.normalized_text,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ReferenceOccurrence":
        t = "ReferenceOccurrence"
        if d is None:
            _err(t, "缺少 occurrence")
        d = _reject_unknown(d, {
            "schema_type", "source_ref", "page_number", "line_index", "char_start",
            "char_end", "occurrence_index", "reference_marker", "reference_kind",
            "declared_target", "normalized_text"}, t)
        _need_enum(d, "schema_type", t, ("ReferenceOccurrence",))
        return cls(
            source_ref=_need_ref(d, "source_ref", t),
            page_number=_need_int(d, "page_number", t, lo=1),
            line_index=_need_int(d, "line_index", t, lo=0),
            char_start=_need_int(d, "char_start", t, lo=0),
            char_end=_need_int(d, "char_end", t, lo=0),
            occurrence_index=_need_int(d, "occurrence_index", t, lo=0),
            reference_marker=_need_str(d, "reference_marker", t),
            reference_kind=_need_enum(d, "reference_kind", t, EDGE_KINDS),
            declared_target=_need_str(d, "declared_target", t, none_ok=True),
            normalized_text=_need_str(d, "normalized_text", t),
        )

    def position(self) -> dict:
        """进入 edge locator 的**位置**部分（不含 marker / 声明目标等文本内容）。"""
        return {
            "source_ref": self.source_ref,
            "page_number": self.page_number,
            "line_index": self.line_index,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "occurrence_index": self.occurrence_index,
        }


def _need_occurrence(d: dict, key: str, typename: str,
                     *, none_ok: bool) -> "ReferenceOccurrence | None":
    if key not in d:
        _err(typename, f"缺少字段 {key!r}")
    v = d[key]
    if v is None:
        if not none_ok:
            _err(typename, f"{key} 不得为 null")
        return None
    if not isinstance(v, dict):
        _err(typename, f"{key} 必须为对象或 null，得到 {type(v).__name__}")
    return ReferenceOccurrence.from_dict(v)


# ---------------------------------------------------------------------------
# 10. 引用边：ReferenceEdge
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReferenceEdge:
    """显式引用边：`详见/参见/如下表/续表` 等 source occurrence → target 的确定性绑定。

    - **稳定定位身份** `edge_locator`：`loc-re-<h(document_outline_locator, from_ref,
      edge_kind, occurrence.position())>` —— 与"是否已解析、目标是什么、证据是什么"
      **无关**，但**包含 occurrence 的位置**。"同一来源、同一类型"的两处真实引用
      位置不同即两条边，不得塌缩成一条。

      这里引用容器的 **locator** 而非 `outline_id`：`ReferenceEdge` **包含在**
      `DocumentOutline` 内，而 outline 的内容身份又由它自己的边派生；若边引用
      `outline_id` 就会与容器身份互相自指、无法构造同一逻辑环（TS1 修正轮已固化
      该规则：包含关系内引用容器 locator，非包含的派生对象引用来源 `*_id`）。

      **`to_ref` 不进入带 occurrence 的边的定位身份**（TS1.2 P1-3.6/3.7）：同一个
      occurrence 从"未解析"变成"已解析"时 `to_ref` 由 `None` 变成目标 id，若定位
      绑定 `to_ref`，定位身份就会随解析结论漂移。定位只回答"这条引用出现在哪里"。
      不带 occurrence 的边（`parent_child`、无文本来源的 `table_continuation`）
      仍绑定 `to_ref`，否则同一父节点的多条子边会塌缩。
    - **不可变 revision 身份** `edge_id`：在 locator 之上绑定 schema version、
      引用边版本、`from_ref` / `to_ref` / `is_resolved`、解析证据、原因码与
      **整个 occurrence**。同一 occurrence 换了目标、换了证据、或由未解析变为
      已解析，都是**另一条边**（P1-3.8/3.10）。

    端点合法性按 `edge_kind` 分别规定（`EDGE_ENDPOINT_KINDS` 的**集合**成员关系），
    **不是**宽松的字符串前缀检查。**对象存在性不在这一层证明**：`is_resolved=True`
    只是本条边的**声明**；"引用已验证"只能由 `DocumentOutline.verify_references(
    context)` 在真实对象上核验并返回 `VerifiedReferences` 得出（P1-2/P1-3）。

    真值表（TS1.3 §四：`toc_to_body` 的**永久结构禁令**已删除，改为对象级可达）：

    | edge_kind | occurrence | is_resolved | to_ref | 已解析的额外前提 |
    |---|---|---|---|---|
    | `parent_child` | 可省略 | **必须为 True** | 必须给出 | 真实父子关系 + `child_ids` 对称 |
    | `cross_reference` | **必须** | True / False | 已解析必须给出 | 目标必须是真实对象 |
    | `table_continuation` | 可省略 | True / False | 已解析必须给出 | 两端都是真实 `TableObject` |
    | `toc_to_body` | **必须** | True / False | 已解析必须给出 | 来源是真实 `TocSource` 且目标标题 / 页码映射成立 |
    | 任意边 | 见上 | False | **必须为 None**（不得声称目标 id） | 必须给出 `reason_code` |

    任何目标类型属于 `UNRESOLVABLE_REF_KINDS`（`evidence:`）的边**一律**不得
    `is_resolved=True`：本层没有这些对象的正式类型，"同值回填的字符串清单"不是对象
    存在证明（P1-2.1/2.5）。

    注意本层**只做声明**：`is_resolved=True` 在这里不校验任何真实对象，因此
    `create()` 不会因为"来源是伪造的 `toc:` 字符串"而拒绝。拒绝发生在正式构造边界
    ——`DocumentOutline.create()` / `DocumentOutline.from_dict()` 一律调用
    `verify_references(context)`（TS1.3 P1-A 取消了无上下文旁路），在那里伪造对象
    必然失败。

    未解析边保留原文声明目标 `occurrence.declared_target`，**禁止**用"第一张表 /
    最近页面 / 自报 target"冒充绑定。
    """

    edge_locator: str
    edge_id: str
    document_outline_locator: str
    from_ref: str
    to_ref: str | None
    edge_kind: str
    resolution_evidence: str | None
    is_resolved: bool
    reason_code: str | None
    occurrence: ReferenceOccurrence | None
    schema_version: str
    reference_edge_version: str

    def __post_init__(self) -> None:
        t = "ReferenceEdge"
        for name in ("edge_locator", "edge_id"):
            if not isinstance(getattr(self, name), str) or getattr(self, name) == "":
                _err(t, f"{name} 必须为非空字符串")
        if not isinstance(self.document_outline_locator, str) \
                or self.document_outline_locator == "":
            _err(t, "document_outline_locator 必须为非空字符串")
        _check_version(t, "reference_edge_version", self.reference_edge_version,
                       V.REFERENCE_EDGE_VERSION)
        _check_version(t, "schema_version", self.schema_version,
                       V.REFERENCE_EDGE_SCHEMA_VERSION)
        if not isinstance(self.from_ref, str) or not _RE_REF.match(self.from_ref):
            _err(t, f"from_ref 必须形如 <kind>:<id> 且 kind ∈ {REF_KINDS}，"
                    f"得到 {self.from_ref!r}")
        if self.to_ref is not None:
            if not isinstance(self.to_ref, str) or not _RE_REF.match(self.to_ref):
                _err(t, f"to_ref 必须形如 <kind>:<id> 或 None，得到 {self.to_ref!r}")
            if self.from_ref == self.to_ref:
                _err(t, "from_ref 与 to_ref 不得相同")
        if self.edge_kind not in EDGE_KINDS:
            _err(t, f"edge_kind 必须属于 {EDGE_KINDS}，得到 {self.edge_kind!r}")
        if not isinstance(self.is_resolved, bool):
            _err(t, "is_resolved 必须为 bool")
        if self.edge_kind == "parent_child" and not self.is_resolved:
            _err(t, "parent_child 是树内派生关系，必须为已解析（本层持有 trees/unassigned "
                    "对象集合，不得自报未解析）")
        if self.occurrence is not None:
            if not isinstance(self.occurrence, ReferenceOccurrence):
                _err(t, "occurrence 必须为 ReferenceOccurrence 或 None")
            if self.occurrence.source_ref != self.from_ref:
                _err(t, "occurrence.source_ref 必须等于 from_ref（出现位置必须属于该边的"
                        f"来源对象）：{self.occurrence.source_ref!r} != {self.from_ref!r}")
            if self.occurrence.reference_kind != self.edge_kind:
                _err(t, "occurrence.reference_kind 必须等于 edge_kind："
                        f"{self.occurrence.reference_kind!r} != {self.edge_kind!r}")
        if self.edge_kind in OCCURRENCE_REQUIRED_EDGE_KINDS and self.occurrence is None:
            _err(t, f"{self.edge_kind!r} 边无论是否已解析都必须绑定真实 occurrence"
                    f"（否则同一来源对同一目标的多处引用会被并成一条）")

        from_kind = self.from_ref.split(":", 1)[0]
        from_kinds, to_kinds = EDGE_ENDPOINT_KINDS[self.edge_kind]
        if from_kind not in from_kinds:
            _err(t, f"edge_kind={self.edge_kind!r} 的来源类型必须属于 {from_kinds}，"
                    f"得到 {from_kind!r}")
        if self.to_ref is not None:
            to_kind = self.to_ref.split(":", 1)[0]
            if to_kind not in to_kinds:
                _err(t, f"edge_kind={self.edge_kind!r} 的目标类型必须属于 {to_kinds}，"
                        f"得到 {to_kind!r}")

        if self.is_resolved:
            # P1-2：`evidence:` 在本层**没有正式类型化对象**，任何"字符串清单里同值
            # 回填"都不得让它变成 resolved。这里是**结构性拒绝**：这类端点根本不允许
            # 出现在已解析边里。
            if self.to_ref is not None \
                    and self.to_ref.split(":", 1)[0] in UNRESOLVABLE_REF_KINDS:
                _err(t, f"目标类型 {self.to_ref.split(':', 1)[0]!r} 在本层没有正式"
                        f"类型化对象（{UNRESOLVABLE_REF_KINDS}），因此**不得**出现在"
                        f"已解析边中：不得用字符串清单或同值回填冒充目标对象存在")
            # P1-3：已解析边的**来源**必须是本层能对象级证明其真实原文的对象。
            # TS1.3 §四.1：`toc_to_body` 已从"永久不得 resolved"改为对象级可达——
            # 只要来源是真实 `TocSource`（由真实 PageLayout 上的真实 LayoutLine
            # 重算身份），它就有与本表中其它边同等的解析路径。**边层不做对象核验**，
            # 因此这里只拒绝本层确实没有任何核验路径的边类型。
            if self.edge_kind not in RESOLVABLE_EDGE_KINDS:
                _err(t, f"edge_kind={self.edge_kind!r} 在本层没有对象级核验路径，"
                        f"因此不得 resolved（本层可解析的边类型仅 "
                        f"{RESOLVABLE_EDGE_KINDS}）")

        if self.is_resolved:
            if not isinstance(self.resolution_evidence, str) \
                    or self.resolution_evidence == "":
                _err(t, "is_resolved=True 时 resolution_evidence 必须为确定的文本/位置证据")
            if self.reason_code is not None:
                _err(t, "is_resolved=True 时 reason_code 必须为 None")
            if self.to_ref is None:
                _err(t, "is_resolved=True 时必须给出 to_ref（已解析边必须有目标）")
        else:
            if self.resolution_evidence is not None:
                _err(t, "is_resolved=False 时 resolution_evidence 必须为 None"
                        "（未解析不得携带自报证据）")
            if self.reason_code not in UNRESOLVED_EDGE_REASONS:
                _err(t, f"is_resolved=False 时必须给出登记的原因码 "
                        f"{UNRESOLVED_EDGE_REASONS}，得到 {self.reason_code!r}")
            if self.to_ref is not None:
                _err(t, "is_resolved=False 时不得声称目标 id（不得编造不存在的目标）："
                        f"to_ref 必须为 None，得到 {self.to_ref!r}")
            if self.edge_kind in OCCURRENCE_REQUIRED_EDGE_KINDS \
                    and self.occurrence.declared_target is None:
                _err(t, "is_resolved=False 时 occurrence.declared_target 必须保留原文声明"
                        "目标（未解析不得丢掉原始引用文字）")

        expected_loc = derive_reference_edge_locator(
            document_outline_locator=self.document_outline_locator,
            from_ref=self.from_ref, to_ref=self.to_ref, edge_kind=self.edge_kind,
            occurrence=self.occurrence)
        if self.edge_locator != expected_loc:
            _err(t, "edge_locator 与派生定位身份不一致："
                    f"{self.edge_locator!r} != {expected_loc!r}")
        expected = derive_reference_edge_id(
            edge_locator=self.edge_locator, schema_version=self.schema_version,
            reference_edge_version=self.reference_edge_version,
            from_ref=self.from_ref, to_ref=self.to_ref, is_resolved=self.is_resolved,
            resolution_evidence=self.resolution_evidence, reason_code=self.reason_code,
            occurrence=self.occurrence)
        if self.edge_id != expected:
            _err(t, f"edge_id 与派生身份不一致：{self.edge_id!r} != {expected!r}")

    def to_dict(self) -> dict:
        return {
            "schema_type": "ReferenceEdge",
            "edge_locator": self.edge_locator,
            "edge_id": self.edge_id,
            "document_outline_locator": self.document_outline_locator,
            "from_ref": self.from_ref,
            "to_ref": self.to_ref,
            "edge_kind": self.edge_kind,
            "resolution_evidence": self.resolution_evidence,
            "is_resolved": self.is_resolved,
            "reason_code": self.reason_code,
            "occurrence": None if self.occurrence is None else self.occurrence.to_dict(),
            "schema_version": self.schema_version,
            "reference_edge_version": self.reference_edge_version,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "ReferenceEdge":
        t = "ReferenceEdge"
        d = _reject_unknown(d, {
            "schema_type", "edge_locator", "edge_id", "document_outline_locator",
            "from_ref", "to_ref", "edge_kind", "resolution_evidence", "is_resolved",
            "reason_code", "occurrence", "schema_version",
            "reference_edge_version"}, t)
        _need_enum(d, "schema_type", t, ("ReferenceEdge",))
        return cls(
            edge_locator=_need_str(d, "edge_locator", t),
            edge_id=_need_str(d, "edge_id", t),
            document_outline_locator=_need_str(d, "document_outline_locator", t),
            from_ref=_need_ref(d, "from_ref", t),
            to_ref=_need_ref(d, "to_ref", t, none_ok=True),
            edge_kind=_need_enum(d, "edge_kind", t, EDGE_KINDS),
            resolution_evidence=_need_str(d, "resolution_evidence", t, none_ok=True),
            is_resolved=_need_bool(d, "is_resolved", t),
            reason_code=_need_str(d, "reason_code", t, none_ok=True),
            occurrence=_need_occurrence(d, "occurrence", t, none_ok=True),
            schema_version=_need_schema_version(d, "schema_version", t,
                                                "REFERENCE_EDGE_SCHEMA_VERSION"),
            reference_edge_version=_need_str(d, "reference_edge_version", t),
        )

    @classmethod
    def create(cls, *, document_outline_locator: str, from_ref: str,
               to_ref: str | None, edge_kind: str,
               resolution_evidence: str | None,
               reason_code: str | None = None,
               occurrence: ReferenceOccurrence | None = None,
               reference_edge_version: str = V.REFERENCE_EDGE_VERSION) -> "ReferenceEdge":
        is_resolved = resolution_evidence is not None
        if not is_resolved and reason_code is None:
            raise SchemaValidationError(
                "ReferenceEdge: 未解析边必须显式给出 reason_code（不得留空）")
        loc = derive_reference_edge_locator(
            document_outline_locator=document_outline_locator, from_ref=from_ref,
            to_ref=to_ref, edge_kind=edge_kind, occurrence=occurrence)
        return cls(
            edge_locator=loc,
            edge_id=derive_reference_edge_id(
                edge_locator=loc, schema_version=V.REFERENCE_EDGE_SCHEMA_VERSION,
                reference_edge_version=reference_edge_version,
                from_ref=from_ref, to_ref=to_ref, is_resolved=is_resolved,
                resolution_evidence=resolution_evidence, reason_code=reason_code,
                occurrence=occurrence),
            document_outline_locator=document_outline_locator, from_ref=from_ref,
            to_ref=to_ref, edge_kind=edge_kind,
            resolution_evidence=resolution_evidence, is_resolved=is_resolved,
            reason_code=reason_code, occurrence=occurrence,
            schema_version=V.REFERENCE_EDGE_SCHEMA_VERSION,
            reference_edge_version=reference_edge_version,
        )


def derive_reference_edge_locator(*, document_outline_locator: str, from_ref: str,
                                  to_ref: str | None, edge_kind: str,
                                  occurrence: "ReferenceOccurrence | None" = None) -> str:
    """`loc-re-<sha256[:16]>`：稳定定位。

    绑定容器 locator、来源对象、edge kind，**并包含 occurrence 的位置**：
    "同一来源、同一目标、同一类型但出现位置不同"必须是两条不同的边，
    否则真正的多处引用（多个 详见 / 如下表）会被并成一条。

    **带 occurrence 的边不绑定 `to_ref`**（TS1.2 P1-3.6/3.7）：定位身份必须只描述
    "这条引用出现在哪里"，不得随"它解析成了什么"变化。若把 `to_ref` 放进定位，
    同一个 occurrence 从 unresolved 变成 resolved（`to_ref` 由 `None` 变为目标 id）
    就会换一个 locator —— 那是把解析结果混进定位身份。解析结果属于 `edge_id`。

    **不带 occurrence 的边**（`parent_child`，以及不带文本来源的 `table_continuation`）
    仍然绑定 `to_ref`：否则同一父节点的多个子边 / 同一表的多个续表边会塌缩成
    同一条定位。
    """
    payload = {
        "document_outline_locator": document_outline_locator,
        "from_ref": from_ref,
        "edge_kind": edge_kind,
        "has_occurrence": occurrence is not None,
        "occurrence": None if occurrence is None else occurrence.position(),
    }
    if occurrence is None:
        payload["to_ref"] = to_ref
    return locator("re", payload)


def derive_reference_edge_id(*, edge_locator: str, schema_version: str,
                             reference_edge_version: str,
                             from_ref: str, to_ref: str | None,
                             is_resolved: bool,
                             resolution_evidence: str | None,
                             reason_code: str | None,
                             occurrence: "ReferenceOccurrence | None") -> str:
    """`re-<sha256[:16]>`：不可变 revision 身份。

    在定位身份之上绑定**解析结论**（`from_ref` / `to_ref` / `is_resolved` /
    证据 / 原因码 / 整个 occurrence）：同一 occurrence 由未解析变为已解析、或换了
    目标、或换了证据，都是另一条边（P1-3.7/3.8/3.10），尽管它的定位身份不变。
    """
    return identity("re", {
        "edge_locator": edge_locator,
        "schema_version": schema_version,
        "reference_edge_version": reference_edge_version,
        "from_ref": from_ref,
        "to_ref": to_ref,
        "is_resolved": is_resolved,
        "resolution_evidence": resolution_evidence,
        "reason_code": reason_code,
        "occurrence": None if occurrence is None else _json(occurrence),
    })


# ---------------------------------------------------------------------------
# 类型登记与自检（纯函数；无 I/O）
# ---------------------------------------------------------------------------

PUBLIC_TYPES: tuple[type, ...] = (
    LayoutSpan, LayoutLine, LayoutPage, PageLayout,
    OutlineNode, DocumentOutline,
    SynopsisSnippet, NavigationSynopsis,
    AspectNavigationEntry, AspectNavigationProfile,
    TextAlignmentRecord, AlignmentRefusalRecord, OutlineSpan,
    TableRow, TableCell, TableObject, ReferenceEdge,
    ReferenceOccurrence, TocSource, ReferenceValidationContext, VerifiedReferences,
)

PUBLIC_TYPE_NAMES: tuple[str, ...] = tuple(c.__name__ for c in PUBLIC_TYPES)


def self_check() -> dict:
    """确定性自检：类型清单、延迟类型缺席、已裁决语义声明、版本字段生效情况。

    本函数不读文件、不连数据库、不调网络；它只回答"本模块是否仍与已裁决结论一致"。
    """
    import document_structure.schema as _self

    deferred_present = [n for n in DEFERRED_TO_TS7A if hasattr(_self, n)]
    superseded_fields = sorted({
        f.name for c in PUBLIC_TYPES for f in c.__dataclass_fields__.values()
        if "superseded" in f.name
    })
    declared = set(V.VERSIONED_OBJECT_SCHEMA_FIELDS)
    # 公共类型减去"不带版本的子结构"与"只在运行时存在的类型"（核验上下文 / 核验结论）
    # 之后，剩下的每一个都必须且只能对应一个 wire format 版本。
    actual = ({c.__name__ for c in PUBLIC_TYPES}
              - set(V.SUBSTRUCTURE_TYPE_NAMES) - set(V.RUNTIME_ONLY_TYPE_NAMES))
    return {
        "public_type_count": len(PUBLIC_TYPES),
        "public_types": list(PUBLIC_TYPE_NAMES),
        "deferred_to_ts7a_present": deferred_present,
        "superseded_version_fields": superseded_fields,
        "descendants_are_candidates_only": DESCENDANTS_ARE_CANDIDATES_ONLY,
        "superseded_version_same_logical_document_only":
            SUPERSEDED_VERSION_SAME_LOGICAL_DOCUMENT_ONLY,
        "table_structure_class_is_authority_verdict":
            TABLE_STRUCTURE_CLASS_IS_AUTHORITY_VERDICT,
        "amount_authority_sources": list(AMOUNT_AUTHORITY_SOURCES),
        "versioned_types_declared": sorted(declared),
        "versioned_types_missing": sorted(declared - actual),
        "versioned_types_undeclared": sorted(actual - declared),
        "align_min": V.ALIGN_MIN,
        "align_min_decided": V.ALIGN_MIN is not None,
        "span_confidence_min": V.SPAN_CONFIDENCE_MIN,
        "span_confidence_min_decided": V.SPAN_CONFIDENCE_MIN is not None,
        "versions": dict(V.VERSION_CONSTANTS),
    }


if __name__ == "__main__":
    import json
    import sys

    _sc = self_check()
    print(json.dumps(_sc, ensure_ascii=False, indent=2, default=str))
    _bad = (_sc["deferred_to_ts7a_present"] or _sc["versioned_types_missing"]
            or _sc["versioned_types_undeclared"])
    sys.exit(0 if not _bad else 1)

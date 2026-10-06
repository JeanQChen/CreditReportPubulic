"""M930-3 §0.19／§18.1 A6：**图侧**合格表对象的版本化放行（`gto-3`）。

本模块补的是「正式图表对象（`to-4`／`to-5` 的 `TableObjectV4`）已经构建出来，却**没有任何
通道**能把它交给工具与 Pack」这个缺口。文本侧平行通道是 `harness/table_object_release.py`
（`tobj-*`），它从**已定位块的原文**重切出一张表；`DESIGN_V2.md` §0.18 W8 已裁决：**目标表
不得由 `tobj-*` 继续作为独立签发／投递来源**（历史对象保持只读兼容）。本模块**不**重切、
不重解析，它只把**图侧已经构建**的表对象，在**同一份图侧来源**上逐表完整证明后放行。

`gto-2` → `gto-3`（2026-09-29，§0.19 裁决）：**资格粒度换轴**
--------------------------------------------------------------------------
`gto-2` 的判据是**文档级**的：图侧资格一旦在文档级被拒，就返回 `document_refused` 批次、
零放行对象。§0.19 把这个粒度换掉：

- **无关页面上的整文档问题仍留在账上**（`document_refusal` 逐字进批次，读的人删不掉），
  但它**不再自动否决**一张已被**独立、完整证明**的目标表；
- 逐表证明的判据在 `document_structure/table_local_proof.py`（`tlp-1`）里**只有一处**；
  本模块不再自己判「逐格来源够不够」，只负责**签发**与**记账**；
- **没有对象可证明**（builder 自身抛错、快照未诞生）是一种**独立**的批次状态
  （`no_table_objects`），不是"文档被拒"的另一种说法。

同一张 `structure_state="partial"` 的表在两版下的结论**相同**（都拒），但两版对"文档级
被拒时某张自证完整的表"给出**相反**结论，且批次状态词表整体改变 —— 这是判据换轴，因此
按门规必须显式升轴，且**旧 `gto-1` / `gto-2` 记录一律 fail-closed**（版本比对不通过），
不得静默按 `gto-3` 解释。

放行判据（全部来自 `tlp-1` 的逐表证明；零公司／页码／关键词）
--------------------------------------------------------------------------
一张表要放行，`prove_table_locally` 必须给出**零缺陷**。缺陷逐条对应 §0.19 的七步验收：

- ②「已构造但不完整」：`structure_not_complete`（非 `complete`；`partial` 一律拒发）、
  `cell_source_not_citable`（有片段却一条不可引用）、`cell_provenance_incomplete`
  （无来源也无缺口声明）、`structure_state_contradicts_cell_gaps`；
- ③「表自身证明」：身份/宿主绑定、表题与主体绑定、完整物理表头与行列标签、逐列实质来源、
  合并格可引用、续表关系、**本表范围内字符与区域的守恒与唯一归属**。

**缺口在册不是放行条件**：它是「这张表还没闭合」的诚实记录，不是"已正式延期"。

读取材料 ≠ 数字权威（硬边界，与 `tobj-*` 同一语义、各自声明）
--------------------------------------------------------------------------
放行对象逐字声明 ``reading_material=True`` / ``numeric_authority=False`` /
``financial_authority_claimed=False``，并且**不出现**任何金额字段名；`TableObjectV4`
本身也**永远**不是财务权威（其 `is_financial_authority()` 恒为 `False`）。表内数字要成为
可引用事实仍必须另走路径 A（预验证权威）或路径 B（exact material + 蕴含决定）。本模块
既不计算、也不呈现数字，更不把 `FinancialFactPack` 的权威搬进来。

纯转换模块：不 I/O、不建库、不写库、不调 LLM／网络；不发起任何检索。
"""

from __future__ import annotations

import json
from typing import Mapping, Sequence

from document_structure import live_table_source as LTS
# 图侧 TS5 wire（`document_structure.table_schema`）与版本登记表；harness 侧错误/工具用 `TS`。
from document_structure import table_schema as TS5
from document_structure import table_local_proof as TLP
from document_structure import versions as DSV
from document_structure.canonical import identity, sha256_canonical
from harness import topic_schema as TS

# 放行规则版本 / 批次 payload schema 版本（两者同步升版；版本不符一律 fail-closed）。
#
# 与 `harness/table_object_release.py` 的 `tobj-*` **并列**而非替换：`tobj-*` 是「块原文
# 重切」通道，本模块是「图侧已构建对象放行」通道。两者身份、逐格来源键、拒绝码各自成一体，
# 任一方的版本前进都不改变另一方的 wire。
#
# `gto-1` → `gto-2`（2026-09-29，指令裁决）：封闭拒发理由码词表已变，`partial` 的放行语义
# 整体翻转（`gto-1` 下「`partial` ＋缺口依据」是放行正例，`gto-2` 下无条件拒发）。
#
# `gto-2` → `gto-3`（2026-09-29，§0.19 裁决）：**资格粒度换轴**。`gto-2` 是文档级判据
# （文档级被拒 ⇒ 零放行对象）；`gto-3` 改为**逐表完整证明**（`tlp-1`），文档级问题仍逐字
# 进账但不再自动否决一张已独立证明的表。同一张表在两版下可能得到**相反**结论（自证完整、
# 但宿主文档被拒的那一张），批次状态词表也整体改变，因此按门规必须显式升轴，且
# **旧 `gto-1` / `gto-2` 记录一律 fail-closed**（版本比对不通过），不得静默按 `gto-3` 解释。
# 与 `harness/table_object_release.py:107-113` 的 `tobj-1` → `tobj-2` 同一政策：harness 侧版本
# **不**并入 `document_structure/versions.py` 的版本表（该表的"唯一字面量来源"约束只覆盖
# `document_structure/`，见 `versions.py:1379` 的既有先例），legacy 在本模块内显式登记。
GRAPH_TABLE_RELEASE_RULE_VERSION = "gto-3"
GRAPH_TABLE_RELEASE_SCHEMA_VERSION = "gto-3"

#: 被取代的图侧放行规则版本（**只显式识别，绝不静默按新轴解释**）。
LEGACY_GRAPH_TABLE_RELEASE_RULE_VERSIONS: tuple[str, ...] = ("gto-1", "gto-2")

#: 被取代的批次 payload schema 版本。
LEGACY_GRAPH_TABLE_RELEASE_SCHEMA_VERSIONS: tuple[str, ...] = ("gto-1", "gto-2")


def classify_graph_table_release_rule_version(value: object) -> str:
    """把读到的放行规则版本分类为 `current` / `legacy` / `unknown`。

    与 `document_structure/versions.py:classify_schema_version` 同一形状：`legacy` 与
    `unknown` 都 fail-closed。读回侧（`record_is_reading_material`）据此**拒绝**任何
    非 current 记录，因此磁盘上的 `gto-1` 放行记录**不会**被当成合格的阅读材料。
    """
    if value == GRAPH_TABLE_RELEASE_RULE_VERSION:
        return "current"
    if value in LEGACY_GRAPH_TABLE_RELEASE_RULE_VERSIONS:
        return "legacy"
    return "unknown"


def classify_graph_table_release_schema_version(value: object) -> str:
    """同上，用于批次 payload 的 `schema_version`。"""
    if value == GRAPH_TABLE_RELEASE_SCHEMA_VERSION:
        return "current"
    if value in LEGACY_GRAPH_TABLE_RELEASE_SCHEMA_VERSIONS:
        return "legacy"
    return "unknown"

#: 批次状态（封闭）：
#:
#: - `no_table_objects`：**没有对象可证明**——`build_final_material_snapshot` 自身抛错、
#:   快照从未诞生。它不是"文档被拒"的另一种说法：文档级被拒时快照**存在**，逐表证明照做。
#: - `accounted`：快照存在（无论文档级是否取得资格），逐表证明结果与
#:   `snapshot.table_count` **严格对账**；文档级拒发读数在该状态下另有
#:   `document_refusal` 一栏逐字带出。
GRAPH_TABLE_BATCH_NO_TABLE_OBJECTS = "no_table_objects"
GRAPH_TABLE_BATCH_ACCOUNTED = "accounted"

GRAPH_TABLE_BATCH_STATES = (GRAPH_TABLE_BATCH_NO_TABLE_OBJECTS,
                            GRAPH_TABLE_BATCH_ACCOUNTED)

#: 被取代的批次状态（**只显式识别**）：`gto-2` 的 `document_refused` 在 `gto-3` 下**不再
#: 产生**。读回侧遇到它必须知道这是"旧轴在文档级一票否决"的读数，而不是本轴的结果。
LEGACY_GRAPH_TABLE_BATCH_STATES: tuple[str, ...] = ("document_refused",)


def classify_graph_table_batch_state(value: object) -> str:
    """批次状态分类 `current` / `legacy` / `unknown`（后两者都 fail-closed）。"""
    if value in GRAPH_TABLE_BATCH_STATES:
        return "current"
    if value in LEGACY_GRAPH_TABLE_BATCH_STATES:
        return "legacy"
    return "unknown"


#: 放行 / 拒绝理由码（闭集；拒绝也留 typed 记录，绝不静默丢弃）。
#:
#: `gto-3` 把理由码**收粗**成两个：签发与否。**逐表的具体缺陷**不再是本层的词表，而是
#: `tlp-1` 的 `defects`（逐条在记录里，改不动、删不掉）。这样做是因为 `gto-2` 的七个码
#: 与逐表证明的缺陷码在语义上大面积重叠 —— 两套词表并存必然漂移（同一个事实有两个名字，
#: 迟早只有一个被更新）。**判据只有一处**：缺陷由 `tlp-1` 判，本层只判"有没有缺陷"。
RELEASE_REASON_RELEASED = "released"
RELEASE_REASON_PROOF_NOT_PASSED = "table_local_proof_not_passed"

GRAPH_TABLE_RELEASE_REASONS = (RELEASE_REASON_RELEASED,
                               RELEASE_REASON_PROOF_NOT_PASSED)

#: 被取代的**拒发理由码**词表（`gto-1`／`gto-2` 的七个码）。历史记录里的 `reason` 仍在
#: 这个闭集内，因此**保留**为只读识别表：读回时必须知道那是旧轴的具体码，不得当成
#: `gto-3` 的 `table_local_proof_not_passed` 来解释（两者粒度不同）。
LEGACY_GRAPH_TABLE_RELEASE_REASONS: tuple[str, ...] = (
    "structure_state_unregistered",
    "structure_state_contradicts_cell_gaps",
    "partial_without_typed_gap",
    "partial_structure_not_releasable",
    "cell_provenance_incomplete",
    "cell_source_not_citable",
)

#: 逐格来源状态（封闭）。**唯一实现**在 `document_structure.table_local_proof`（与逐表证明
#: 读的是同一件事）：`citable` = 至少一条可引用片段；`unproven_only` = 无可用片段但**逐条
#: 声明**了缺口；`not_citable` = 有片段却一条都不可引用；`no_source` = 什么都没有。
CELL_STATE_CITABLE = TLP.CELL_STATE_CITABLE
CELL_STATE_UNPROVEN_ONLY = TLP.CELL_STATE_UNPROVEN_ONLY
CELL_STATE_NOT_CITABLE = TLP.CELL_STATE_NOT_CITABLE
CELL_STATE_NO_SOURCE = TLP.CELL_STATE_NO_SOURCE

CELL_STATES = TLP.CELL_STATES

#: typed 缺口来源（封闭）：台账条目优先，其次逐格缺口声明。放行记录与拒绝记录**同义**使用
#: 该字段；两者都没有缺口时写 `None` —— "没有缺口"不借用任何缺口依据码来表示。
GAP_BASIS_TYPED_GAP_ENTRY = "typed_gap_entry"
GAP_BASIS_CELL_UNPROVEN_FRAGMENTS = "cell_unproven_fragments"

GAP_BASES = (GAP_BASIS_TYPED_GAP_ENTRY, GAP_BASIS_CELL_UNPROVEN_FRAGMENTS)

#: 表对象的**允许用途**（封闭词表，只有一条）。它只在「这一栏读得到这张表」这件事上说话：
#: 既不是数字权威，也不能凭表体里的数字替 Contract 事实作证。
GRAPH_TABLE_PERMITTED_USE = "navigable_reading_material_only"

#: 拒绝记录里逐格问题样例的条数上限（审计面有界，且必须**显式**说明被截断）。
_MAX_PROBLEM_EXAMPLES = 3
_MAX_GAP_DETAIL_CODES = 12


class GraphTableReleaseError(TS.SchemaValidationError):
    """图侧放行的 fail-closed 错误（越权调用、身份漂移、快照自身不自洽）。"""


# ---------------------------------------------------------------------------
# 1. 逐格读取（**只读**，不重切、不重解析、不猜格）
# ---------------------------------------------------------------------------


def _qualification(*, reading_material: bool) -> dict:
    """三条权威声明（封闭、逐字进 record，读的人删不掉也改不了）。"""
    return {
        "reading_material": bool(reading_material),
        "numeric_authority": False,
        "financial_authority_claimed": False,
        "permitted_use": GRAPH_TABLE_PERMITTED_USE,
    }


def _source_view(ref: TS5.TableCellSourceRef) -> dict:
    """一条 cell 来源（含精确 Layout 定位、Evidence 区间与 terminal 身份）。"""
    return {
        "interval": ref.interval.to_dict(),
        "fragment_key": [ref.interval.page_number, ref.interval.line_index,
                         ref.interval.span_index],
        "span_char_range": list(ref.interval.span_char_range),
        "component_id": ref.component_id,
        "component_locator": ref.component_locator,
        "evidence_block_id": ref.evidence_block_id,
        "evidence_char_range": list(ref.evidence_char_range),
        "terminal_kind": ref.terminal_kind,
        "terminal_id": ref.terminal_id,
        "terminal_locator": ref.terminal_locator,
        "terminal_schema_version": ref.terminal_schema_version,
        "alignment_id": ref.alignment_id,
        "verdict": ref.verdict,
        "refusal_reason": ref.refusal_reason,
        "citable": ref.citable,
        "citable_reason": ref.citable_reason,
    }


def _cell_state(cell: TS5.TableCellV4) -> str:
    """逐格来源状态（**唯一实现**在 `tlp-1`；本层只转发，不再各写一份）。"""
    return TLP.cell_state(cell)


def _cell_view(cell: TS5.TableCellV4) -> dict:
    sources = [_source_view(r) for r in cell.source_refs]
    keys = sorted({tuple(s["fragment_key"]) for s in sources
                   if s["citable"]})
    return {
        "row": cell.row,
        "column": cell.column,
        "rowspan": cell.rowspan,
        "colspan": cell.colspan,
        "bbox": list(cell.bbox),
        "text": cell.text,
        "cell_state": _cell_state(cell),
        "citable_source_count": cell.citable_ref_count,
        "non_citable_source_count": cell.non_citable_ref_count,
        "fragment_keys": [list(k) for k in keys],
        "unproven_fragments": [list(x) for x in cell.unproven_fragments],
        "sources": sources,
    }


def _block_text(blocks: Sequence[TS5.TableCellBlock]) -> str:
    """表题 / 单位 / 附注的**逐字**文本（块按序、以 cell 内同一冻结分隔符拼接）。"""
    return TS5.CELL_BLOCK_SEPARATOR.join(b.text for b in blocks)


def _header_rows(table: TS5.TableObjectV4) -> list[list[str]]:
    """表头行的**逐字**单元格文本（按列展开；跨列 cell 在其覆盖的每列重复写出）。

    这里**不**做任何逻辑表头重建（不做列绑定、不猜期间）：表头只是逐字事实，期间属于
    数字资格（B 段）的裁决范围，不是本模块的产物。
    """
    rows: list[list[str]] = []
    for row in table.rows:
        if row.role != "header":
            continue
        expanded: list[str | None] = [None] * table.column_count
        for cell in row.cells:
            for dc in range(cell.colspan):
                c = cell.column + dc
                if 0 <= c < table.column_count and expanded[c] is None:
                    expanded[c] = cell.text
        rows.append([x if x is not None else "" for x in expanded])
    return rows


def _column_support(table: TS5.TableObjectV4) -> list[dict]:
    """逐列支撑读数（**唯一实现**在 `tlp-1`；本层只转发，不再各写一份）。"""
    return TLP.column_support_readings(table)


def _cell_problem_examples(table: TS5.TableObjectV4) -> tuple[list[dict], bool]:
    """拒绝记录里的逐格问题样例（**有界**）+ "是否被截断"（必须显式声明）。

    判据不在这里：哪些 cell 算**缺陷**由 `tlp-1` 判（`defects`）；本函数只是把**同一份**
    逐格状态读出来给人看，因此它读的是 `tlp-1` 的 `cell_state`，不另立标准。

    `unproven_only` **不**进样例：逐条在册的缺口是"这张表还没闭合"的记录（表级缺陷
    `structure_not_complete` 已经说清），`unproven_fragment_keys` 会把它们逐条带出；
    把它塞进"逐格问题样例"会让"未闭合"读成"缺陷格"。
    """
    examples: list[dict] = []
    truncated = False
    for row in table.rows:
        for cell in row.cells:
            state = _cell_state(cell)
            if state in (CELL_STATE_CITABLE, CELL_STATE_UNPROVEN_ONLY):
                continue
            if len(examples) < _MAX_PROBLEM_EXAMPLES:
                examples.append({
                    "row": cell.row, "column": cell.column,
                    "cell_state": state, "text": cell.text,
                    "non_citable_reasons": sorted(
                        {r.citable_reason for r in cell.source_refs
                         if not r.citable}),
                })
            else:
                truncated = True
    return examples, truncated


# ---------------------------------------------------------------------------
# 2. 身份（通道内）
# ---------------------------------------------------------------------------


def graph_release_id_payload(*, final_material_snapshot_id: str,
                             verified_span_snapshot_id: str,
                             upstream_dependency_fingerprint: str,
                             table_id: str, table_locator: str,
                             structure_state: str,
                             local_proof_id: str,
                             fragment_keys: Sequence[Sequence[int]]) -> dict:
    """`release_id` 的 canonical payload（**外部可复算**，见 :func:`validate_release_id`）。

    它绑定：这一次图侧签发（快照身份 + 上游依赖指纹）、这一张表（图侧两套身份）、它的结构
    状态、**这一次逐表完整证明的身份**（`tlp-1` 的 `proof_id`），以及**逐格真实来源键**
    （去重、排序后的 `(页, 行, 片段索引)`）。

    `local_proof_id` 必须在 payload 里：`gto-3` 的放行结论**就是**"这张表独立证明了"，
    若不绑定它，两张逐格来源相同、但**范围守恒读数不同**（例如残余区间不同）的表会得到
    同一个 `release_id`——那等于把"证明"从身份里抹掉。
    """
    keys = sorted({tuple(int(x) for x in k) for k in fragment_keys})
    return {
        "schema_version": GRAPH_TABLE_RELEASE_SCHEMA_VERSION,
        "release_rule_version": GRAPH_TABLE_RELEASE_RULE_VERSION,
        "final_material_snapshot_id": final_material_snapshot_id,
        "verified_span_snapshot_id": verified_span_snapshot_id,
        "upstream_dependency_fingerprint": upstream_dependency_fingerprint,
        "table_id": table_id,
        "table_locator": table_locator,
        "structure_state": structure_state,
        "local_proof_id": local_proof_id,
        "fragment_keys": [list(k) for k in keys],
    }


def graph_release_id(**kwargs) -> str:
    """通道内放行身份 `gtr-...`（内容寻址；与 `table_id` / `released_object_id` 均不同）。"""
    return identity("gtr", graph_release_id_payload(**kwargs))


def graph_batch_id_payload(*, document_id: str, document_version: str,
                           batch_state: str,
                           member_ids: Sequence[str]) -> dict:
    """批次身份 payload：宿主文档 + 批次状态 + **全部**成员（放行与拒绝各自的有序身份）。"""
    return {
        "schema_version": GRAPH_TABLE_RELEASE_SCHEMA_VERSION,
        "release_rule_version": GRAPH_TABLE_RELEASE_RULE_VERSION,
        "document_id": document_id,
        "document_version": document_version,
        "batch_state": batch_state,
        "member_ids": list(member_ids),
    }


def graph_batch_id(**kwargs) -> str:
    return identity("gtb", graph_batch_id_payload(**kwargs))


def _refusal_digest(record: Mapping) -> str:
    """拒绝记录的身份摘要（拒绝也是正式读数，必须进批次身份）。"""
    return sha256_canonical({k: v for k, v in record.items()
                             if k != "schema_type"})


# ---------------------------------------------------------------------------
# 3. 主入口
# ---------------------------------------------------------------------------


def _assert_live_table_source(source) -> LTS.LiveTableSource:
    """唯一合法的入口形态：**已签发**的 `LiveTableSource`（反自证）。"""
    if not isinstance(source, LTS.LiveTableSource):
        raise GraphTableReleaseError(
            "release_graph_tables 只接受 document_structure.live_table_source"
            f".LiveTableSource（图侧正式组合根的签发结果），得到 "
            f"{type(source).__name__}")
    return source


def _snapshot_binding(snapshot: TS5.FinalMaterialStructureSnapshot, t: str) -> dict:
    return {
        "document_id": snapshot.document_id,
        "document_version": snapshot.document_version,
        "evidence_set_version": snapshot.evidence_set_version,
        "page_layout_id": snapshot.page_layout_id,
        "outline_id": snapshot.outline_id,
        "verified_span_snapshot_id": snapshot.verified_span_snapshot_id,
        "upstream_dependency_fingerprint":
            snapshot.upstream_dependency_fingerprint,
    }


_TABLE_BINDING_FIELDS = (
    "document_id", "document_version", "evidence_set_version", "page_layout_id",
    "outline_id", "verified_span_snapshot_id", "upstream_dependency_fingerprint",
)


def _assert_member(table: TS5.TableObjectV4, *,
                   snapshot: TS5.FinalMaterialStructureSnapshot,
                   seen: set[str]) -> None:
    """G1 的逐项落地：表必须与宿主快照**逐项同源**，且在册、不重复。"""
    for name in _TABLE_BINDING_FIELDS:
        if getattr(table, name) != getattr(snapshot, name):
            raise GraphTableReleaseError(
                f"表 {table.table_id} 的 {name} 与宿主快照不一致："
                f"{getattr(table, name)!r} != {getattr(snapshot, name)!r}")
    if table.table_id in seen:
        raise GraphTableReleaseError(f"表 {table.table_id} 在本次放行中重复出现")
    seen.add(table.table_id)
    if table.table_id not in {m.table_id for m in snapshot.tables}:
        raise GraphTableReleaseError(
            f"表 {table.table_id} 不在宿主快照的成员集中（不得越出快照放行）")


def _release_record(table: TS5.TableObjectV4, *,
                    snapshot: TS5.FinalMaterialStructureSnapshot,
                    proof: TLP.TableLocalProof) -> dict:
    """一张**已独立证明**的表 → 放行记录（逐格来源 + 逐列支撑 + 证明 + 三条权威声明）。

    `proof` 是必需入参：`gto-3` 里"放行"与"这张表自己证明通过"是同一件事，调用方拿不到
    零缺陷的证明就构造不出放行记录（不是靠一个 bool 参数自觉）。
    """
    if not proof.locally_proven:
        raise GraphTableReleaseError(
            f"放行记录只接受**零缺陷**的逐表证明，表 {table.table_id} 有 "
            f"{len(proof.defects)} 项缺陷（fail-closed）")
    if proof.table_id != table.table_id:
        raise GraphTableReleaseError(
            f"逐表证明属于表 {proof.table_id!r}，却拿来放行表 {table.table_id!r}")
    gap_entries = [e for e in snapshot.gaps.entries
                   if e.table_id == table.table_id]
    rows = [{
        "row_index": row.row_index,
        "role": row.role,
        "label": row.label,
        "is_repeated_header": row.is_repeated_header,
        "cells": [_cell_view(c) for c in row.cells],
    } for row in table.rows]
    cells = [c for row in rows for c in row["cells"]]
    keys = sorted({tuple(k) for cell in cells for k in cell["fragment_keys"]})
    unproven_keys = sorted({tuple(x) for cell in cells
                            for x in cell["unproven_fragments"]})
    column_support = _column_support(table)
    unsupported = [c["column"] for c in column_support if not c["supported"]]
    blocks = sorted({s["evidence_block_id"] for cell in cells
                     for s in cell["sources"] if s["citable"]})
    body_rows = sum(1 for r in table.rows if r.role != "header")
    record = {
        "schema_type": "GraphTableRelease",
        "release_rule_version": GRAPH_TABLE_RELEASE_RULE_VERSION,
        "release_schema_version": GRAPH_TABLE_RELEASE_SCHEMA_VERSION,
        "released": True,
        "reason": RELEASE_REASON_RELEASED,
        "document_id": table.document_id,
        "document_version": table.document_version,
        "evidence_set_version": table.evidence_set_version,
        "page_layout_id": table.page_layout_id,
        "outline_id": table.outline_id,
        "verified_span_snapshot_id": table.verified_span_snapshot_id,
        "final_material_snapshot_id": snapshot.snapshot_id,
        "final_material_snapshot_locator": snapshot.snapshot_locator,
        "upstream_dependency_fingerprint":
            table.upstream_dependency_fingerprint,
        "table_id": table.table_id,
        "table_locator": table.table_locator,
        "table_schema_version": table.schema_version,
        "table_builder_version": table.table_builder_version,
        "page_number": table.page_number,
        "page_bbox": list(table.page_bbox),
        "source_order_index": table.source_order_index,
        "owner": table.owner.to_dict(),
        "structure_kind": table.structure_kind,
        "structure_class": table.structure_class,
        "structure_state": table.structure_state,
        "structure_state_reason": table.structure_state_reason,
        "header_absence_reason": table.header_absence_reason,
        "missing_or_uncertain_fields":
            list(table.missing_or_uncertain_fields),
        "column_count": table.column_count,
        "row_count": len(table.rows),
        "body_row_count": body_rows,
        "cell_count": len(cells),
        "title_text": _block_text(table.title_blocks),
        "unit_text": table.unit_text or "",
        "note_text": _block_text(table.note_blocks),
        "header_rows": _header_rows(table),
        "rows": rows,
        "content_fingerprint": table.content_fingerprint,
        "structure_fingerprint": table.structure_fingerprint,
        "provenance_fingerprint": table.provenance_fingerprint,
        # 这一次逐表完整证明的**逐条读数**（判据的唯一出处，逐字进记录）。
        # `local_proof_id` 另**单列**写在顶层：`release_id` 由它派生，外部复算
        # `release_id` 时不必（也不该）去解析 `local_proof` 的内部结构。
        "local_proof_id": proof.proof_id,
        "local_proof": proof.to_dict(),
        "fragment_keys": [list(k) for k in keys],
        "unproven_fragment_keys": [list(k) for k in unproven_keys],
        "evidence_block_ids": blocks,
        # 放行谓词已强制 `complete` 且逐格缺口为 0（见 `_table_problems`）⇒ 放行记录**不可能**
        # 以逐格声明为缺口依据；两者都没有时如实写 `None`，不得给一张零缺口的表贴
        # `cell_unproven_fragments`。
        "gap_basis": (GAP_BASIS_TYPED_GAP_ENTRY if gap_entries else None),
        "typed_gap_entry_count": len(gap_entries),
        "typed_gap_detail_codes": sorted({e.detail_code for e in gap_entries})[
            :_MAX_GAP_DETAIL_CODES],
        "unproven_cell_count": sum(
            1 for c in cells if c["cell_state"] == CELL_STATE_UNPROVEN_ONLY),
        "column_support": column_support,
        "unsupported_columns": unsupported,
        "all_columns_supported": not unsupported,
        "content_qualification": _qualification(reading_material=True),
    }
    record["release_id"] = graph_release_id(
        final_material_snapshot_id=snapshot.snapshot_id,
        verified_span_snapshot_id=table.verified_span_snapshot_id,
        upstream_dependency_fingerprint=table.upstream_dependency_fingerprint,
        table_id=table.table_id,
        table_locator=table.table_locator,
        structure_state=table.structure_state,
        local_proof_id=proof.proof_id,
        fragment_keys=record["fragment_keys"])
    return record


def _refusal_record(table: TS5.TableObjectV4, *,
                    snapshot: TS5.FinalMaterialStructureSnapshot,
                    proof: TLP.TableLocalProof) -> dict:
    """一张**未通过自身证明**的表 → 拒绝记录（缺陷逐条带出，绝不静默丢弃）。"""
    if proof.locally_proven:
        raise GraphTableReleaseError(
            f"拒绝记录只接受**有缺陷**的逐表证明，表 {table.table_id} 的证明零缺陷"
            "（fail-closed）")
    if proof.table_id != table.table_id:
        raise GraphTableReleaseError(
            f"逐表证明属于表 {proof.table_id!r}，却拿来说表 {table.table_id!r}")
    gap_entries = [e for e in snapshot.gaps.entries
                   if e.table_id == table.table_id]
    cells = [c for row in table.rows for c in row.cells]
    column_support = _column_support(table)
    examples, examples_truncated = _cell_problem_examples(table)
    # `reason` 取**缺陷码**里优先级最先的一条（优先级即 `tlp-1` 的 `TABLE_LOCAL_PROOF_DEFECTS`
    # 顺序）；`defects` 逐条列全。本层不再有自己的第二套码表。
    primary = next((d for d in TLP.TABLE_LOCAL_PROOF_DEFECTS
                    if d in proof.defects), proof.defects[0])
    return {
        "schema_type": "GraphTableReleaseRefusal",
        "release_rule_version": GRAPH_TABLE_RELEASE_RULE_VERSION,
        "release_schema_version": GRAPH_TABLE_RELEASE_SCHEMA_VERSION,
        "released": False,
        "reason": primary,
        "problems": [d for d in TLP.TABLE_LOCAL_PROOF_DEFECTS
                     if d in proof.defects],
        "local_proof": proof.to_dict(),
        "document_id": table.document_id,
        "document_version": table.document_version,
        "verified_span_snapshot_id": table.verified_span_snapshot_id,
        "final_material_snapshot_id": snapshot.snapshot_id,
        "upstream_dependency_fingerprint":
            table.upstream_dependency_fingerprint,
        "table_id": table.table_id,
        "table_locator": table.table_locator,
        "page_number": table.page_number,
        "structure_kind": table.structure_kind,
        "structure_class": table.structure_class,
        "structure_state": table.structure_state,
        "structure_state_reason": table.structure_state_reason,
        "cell_count": len(cells),
        "citable_cell_count": sum(1 for c in cells if c.citable_ref_count > 0),
        "unproven_cell_count": sum(1 for c in cells if c.unproven_fragments),
        # `partial` 只留诊断 ⇒ 诊断面必须把逐格缺口键**逐条**带出（不能只剩一个计数）：
        # 这是「它为什么进不了 Pack」以及「缺的是哪一段原文」的可回查依据。
        "unproven_fragment_keys": [list(k) for k in sorted(
            {tuple(x) for c in cells for x in c.unproven_fragments})],
        "not_citable_cell_count": sum(
            1 for c in cells
            if c.citable_ref_count == 0 and not c.unproven_fragments),
        "cell_problem_examples": [dict(e) for e in examples],
        "cell_problem_examples_truncated": examples_truncated,
        "typed_gap_entry_count": len(gap_entries),
        "typed_gap_detail_codes": sorted({e.detail_code for e in gap_entries})[
            :_MAX_GAP_DETAIL_CODES],
        # 诊断面与放行面**同义**标出缺口来源（台账优先，其次逐格声明，都没有则 `None`）。
        # `partial` 只留诊断，所以这条读数正是「它为什么不能进 Pack」的可回查依据。
        "gap_basis": (GAP_BASIS_TYPED_GAP_ENTRY if gap_entries
                      else (GAP_BASIS_CELL_UNPROVEN_FRAGMENTS
                            if any(c.unproven_fragments for c in cells)
                            else None)),
        "column_support": column_support,
        "unsupported_columns": [c["column"] for c in column_support
                                if not c["supported"]],
        "content_qualification": _qualification(reading_material=False),
    }


def release_graph_tables(table_source) -> dict:
    """图侧正式放行：**唯一**入口，只接受已签发的 `LiveTableSource`。

    三种结果**互不顶替**（`gto-3`）：

    1. **没有对象可证明**（快照从未诞生）⇒ `batch_state="no_table_objects"`：零放行、
       零逐表拒绝、`accounting_balanced=False`，拒绝读数逐字带出。这不是"某张表不合格"。
    2. 快照存在 ⇒ 在**同一份图侧来源**上逐表完整证明（`tlp-1`），逐表结果与
       `snapshot.table_count` **严格对账**（`accounted`）。**文档级**的资格终态另在
       `document_refusal` 一栏逐字带出：它不再一票否决，但**绝不隐藏**。
    3. 输入/身份/记账自身不自洽（含读数与成员不等集）⇒ 抛错，不放行任何东西。
    """
    source = _assert_live_table_source(table_source)
    document_id = source.document_id
    document_version = source.document_version
    snapshot = source.snapshot
    if snapshot is None:
        refusal = source.refusal
        batch = {
            "schema_type": "GraphTableReleaseBatch",
            "release_rule_version": GRAPH_TABLE_RELEASE_RULE_VERSION,
            "release_schema_version": GRAPH_TABLE_RELEASE_SCHEMA_VERSION,
            "batch_state": GRAPH_TABLE_BATCH_NO_TABLE_OBJECTS,
            "document_id": document_id,
            "document_version": document_version,
            "document_refusal": refusal.to_dict(),
            "declared_table_count": None,
            "released_table_count": 0,
            "refused_table_count": 0,
            "accounting_balanced": False,
            "released": [],
            "refusals": [],
            "content_qualification": _qualification(reading_material=False),
        }
        batch["batch_id"] = graph_batch_id(
            document_id=document_id, document_version=document_version,
            batch_state=batch["batch_state"],
            member_ids=[f"document_refusal:{refusal.refusal_kind}"])
        return batch
    is_qualified = source.is_qualified
    document_refusal = None if is_qualified else source.refusal.to_dict()
    if is_qualified:
        # 资格已签发时 `verified` 必须可取；取不到就是来源自身不自洽（不是文档被拒）。
        source.verified
    if snapshot.schema_version != TS5.FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION:
        raise GraphTableReleaseError(
            f"快照 schema 版本必须为 {TS5.FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION!r}，"
            f"得到 {snapshot.schema_version!r}")
    if snapshot.builder_version != DSV.FINAL_MATERIAL_BUILDER_VERSION:
        raise GraphTableReleaseError(
            f"快照 builder 版本必须为 {DSV.FINAL_MATERIAL_BUILDER_VERSION!r}，"
            f"得到 {snapshot.builder_version!r}")
    if snapshot.table_count != len(snapshot.tables):
        # 成员账不平时**不放行任何东西**：这不是"某张表不合格"，是快照自身不自洽。
        raise GraphTableReleaseError(
            "宿主快照的 table_count 与成员数不一致："
            f"{snapshot.table_count} != {len(snapshot.tables)}")
    seen: set[str] = set()
    released: list[dict] = []
    refusals: list[dict] = []
    # **先**核对成员自身是否自洽（逐项同源、在册、不重复），**再**算逐表证明。
    # 顺序有实质意义：成员集重复是"快照自身不自洽"，属于"不放行任何东西"那一类；
    # 若让逐表证明先跑，它会因为"读数与成员不等集"而抛另一种错，读的人会以为问题
    # 出在范围读数上。
    for table in snapshot.tables:
        _assert_member(table, snapshot=snapshot, seen=seen)
    # 逐表证明：范围读数在同一份图侧来源上重算，且必须与快照成员**精确等集**
    # （不等即抛错 —— 那是来源不自洽，不是"某张表不合格"）。
    readings = source.table_scope_readings()
    proofs = TLP.prove_tables_locally(
        host=snapshot, readings=readings, gap_entries=snapshot.gaps.entries,
        host_document_state=("qualified" if is_qualified else "refused"),
        host_document_refusal_kind=(
            None if is_qualified else source.refusal.refusal_kind))
    for table, proof in zip(snapshot.tables, proofs):
        if proof.locally_proven:
            released.append(_release_record(table, snapshot=snapshot,
                                            proof=proof))
        else:
            refusals.append(_refusal_record(table, snapshot=snapshot,
                                            proof=proof))
    if len(released) + len(refusals) != snapshot.table_count:
        # 逐表读数与成员数不对账 ⇒ 不放行任何东西（这也不是"某张表不合格"）。
        raise GraphTableReleaseError(
            "逐表放行结果与快照成员数不对账："
            f"{len(released)} 放行 + {len(refusals)} 拒绝 != "
            f"{snapshot.table_count}")
    batch = {
        "schema_type": "GraphTableReleaseBatch",
        "release_rule_version": GRAPH_TABLE_RELEASE_RULE_VERSION,
        "release_schema_version": GRAPH_TABLE_RELEASE_SCHEMA_VERSION,
        "batch_state": GRAPH_TABLE_BATCH_ACCOUNTED,
        "document_id": snapshot.document_id,
        "document_version": snapshot.document_version,
        # 文档级拒发读数：`gto-3` 里它**不再**一票否决，但也**绝不隐藏** —— 一条"文档被拒、
        # 某张表仍被独立证明"的批次如果隐去这一栏，读回就会读成"整份文档都过了"。
        "document_refusal": document_refusal,
        "document_qualified": is_qualified,
        "declared_table_count": snapshot.table_count,
        "released_table_count": len(released),
        "refused_table_count": len(refusals),
        "accounting_balanced": True,
        "released": released,
        "refusals": refusals,
        "content_qualification": _qualification(reading_material=bool(released)),
    }
    # 批次身份必须同时绑定**文档级终态**与逐表成员：同一批表在"文档已取得资格"与"文档级
    # 被拒但逐表独立证明通过"两种情形下是**不同**的批次，读回不能把两者当成同一次签发。
    document_state_digest = sha256_canonical({
        "document_qualified": bool(is_qualified),
        "document_refusal_kind": (
            None if document_refusal is None else document_refusal["refusal_kind"]),
        "document_refusal_detail": (
            None if document_refusal is None else document_refusal["detail"]),
    })
    batch["batch_id"] = graph_batch_id(
        document_id=snapshot.document_id,
        document_version=snapshot.document_version,
        batch_state=batch["batch_state"],
        member_ids=([f"document_state:{document_state_digest}"]
                    + [r["release_id"] for r in released]
                    + [_refusal_digest(r) for r in refusals]))
    return batch


# ---------------------------------------------------------------------------
# 4. 消费侧复核（不持有能力也能验）
# ---------------------------------------------------------------------------


def validate_release_id(record: Mapping) -> str:
    """**外部复算** `release_id`：不采信记录自报的身份。

    返回重算出的 id；与记录不符即抛错（记录被改写过，或逐格来源被换过）。
    """
    if not isinstance(record, Mapping):
        raise GraphTableReleaseError(
            f"放行记录必须为映射，得到 {type(record).__name__}")
    for name in ("final_material_snapshot_id", "verified_span_snapshot_id",
                 "upstream_dependency_fingerprint", "table_id", "table_locator",
                 "structure_state", "local_proof_id", "fragment_keys",
                 "release_id"):
        if name not in record:
            raise GraphTableReleaseError(f"放行记录缺字段 {name!r}")
    expected = graph_release_id(
        final_material_snapshot_id=record["final_material_snapshot_id"],
        verified_span_snapshot_id=record["verified_span_snapshot_id"],
        upstream_dependency_fingerprint=record["upstream_dependency_fingerprint"],
        table_id=record["table_id"],
        table_locator=record["table_locator"],
        structure_state=record["structure_state"],
        local_proof_id=record["local_proof_id"],
        fragment_keys=record["fragment_keys"])
    if expected != record["release_id"]:
        raise GraphTableReleaseError(
            f"release_id 与逐格来源复算不一致：{record['release_id']!r} != "
            f"{expected!r}")
    return expected


def record_is_reading_material(record: Mapping) -> bool:
    """放行记录是否**只是阅读材料**（三条权威声明必须齐备且方向正确）。

    **版本门在最先**（`gto-3`）：`release_rule_version` 与 `release_schema_version`
    都必须是 **current**。`gto-1` / `gto-2` 的放行记录一律 **fail-closed** —— `gto-1` 下
    「`partial` ＋缺口依据 ⇒ 放行」曾是正例，`gto-2` 下文档级被拒 ⇒ 零放行对象；两版的
    记录按 `gto-3` 读都会得到与产生时不同的结论，正是门规禁止的"静默按新轴解释"。
    因此版本比对**不通过就一律 `False`**，与三条权威声明是否齐备无关。

    此外还要求 `local_proof` 在场且**逐表证明的版本门也通过**：一条 `released=True` 却
    拿不出本次证明的记录不是 gto-3 的放行记录（放行结论**就是**那张证明）。
    """
    if classify_graph_table_release_rule_version(
            record.get("release_rule_version")) != "current":
        return False
    if classify_graph_table_release_schema_version(
            record.get("release_schema_version")) != "current":
        return False
    q = record.get("content_qualification")
    if not isinstance(q, Mapping):
        return False
    return (record.get("released") is True
            and TLP.record_is_locally_proven(record.get("local_proof") or {})
            and q.get("reading_material") is True
            and q.get("numeric_authority") is False
            and q.get("financial_authority_claimed") is False)


def released_fragment_keys(record: Mapping) -> tuple[tuple[int, int, int], ...]:
    """放行记录的逐格来源键（去重、排序），供材料侧构 locator。"""
    return tuple(sorted({tuple(int(x) for x in k)
                         for k in record.get("fragment_keys", ())}))


def to_json(batch: Mapping) -> str:
    """批次的确定性 JSON（审计/落盘用；不参与任何身份计算）。"""
    return json.dumps(batch, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


__all__ = [
    "GRAPH_TABLE_RELEASE_RULE_VERSION",
    "GRAPH_TABLE_RELEASE_SCHEMA_VERSION",
    "LEGACY_GRAPH_TABLE_RELEASE_RULE_VERSIONS",
    "LEGACY_GRAPH_TABLE_RELEASE_SCHEMA_VERSIONS",
    "classify_graph_table_release_rule_version",
    "classify_graph_table_release_schema_version",
    "GRAPH_TABLE_BATCH_STATES",
    "LEGACY_GRAPH_TABLE_BATCH_STATES",
    "classify_graph_table_batch_state",
    "GRAPH_TABLE_BATCH_NO_TABLE_OBJECTS",
    "GRAPH_TABLE_BATCH_ACCOUNTED",
    "GRAPH_TABLE_RELEASE_REASONS",
    "LEGACY_GRAPH_TABLE_RELEASE_REASONS",
    "CELL_STATES",
    "GAP_BASES",
    "GRAPH_TABLE_PERMITTED_USE",
    "GraphTableReleaseError",
    "release_graph_tables",
    "graph_release_id",
    "graph_release_id_payload",
    "graph_batch_id",
    "graph_batch_id_payload",
    "validate_release_id",
    "record_is_reading_material",
    "released_fragment_keys",
    "to_json",
]

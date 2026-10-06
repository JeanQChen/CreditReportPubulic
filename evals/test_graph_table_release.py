# -*- coding: utf-8 -*-
"""M930-3 §0.19 / §18.1 A6：`harness/graph_table_release.py`（`gto-3`）的聚焦正反例。

本模块补的是一条**从未被任何回归执行过**的路径：图侧已构建的合格表对象
（`to-4` 的 `TableObjectV4`）如何经版本化放行进入工具与 Pack。该模块此前在磁盘上
存在、却没有任何导入方、也没有登记进 `evals/run_evals.py`——因此"它是绿的"这句话
在登记之前没有任何证据。本模块逐条给出正例与反例，并**只**断言被测对象的公开行为。

`gto-2` → `gto-3`（§0.19）后，本模块多覆盖一条**判据换轴**：文档级被拒**不再**一票
否决一张已被独立完整证明的表；放行结论**就是**那张逐表证明（`tlp-1`）。

覆盖（分组）：

- **G1 版本与封闭词表**：规则/载荷版本、批次状态、理由码、逐格状态、缺口依据、允许
  用途、审计面上限。期望值一律用**本模块自带字面量**，不复用生产常量当期望（否则
  常量被改时测试跟着绿）。
- **G2 入口与反自证**：`release_graph_tables` 只接受 `LiveTableSource`；普通对象、
  同形 duck 对象一律 fail-closed。
- **G3 没有对象可证明**：快照从未诞生（builder 自身抛错）⇒ `no_table_objects` 批次、
  零放行、零逐表拒绝、`accounting_balanced=False`，拒绝读数逐字进入批次身份。
- **G3b 文档级被拒但逐表自证通过**：`accounted` 批次、该表**放行**、`document_refusal`
  逐字带出、`document_qualified=False` —— 全文档问题在账上，不再自动否决这张表。
- **G4 放行正例**：三条件齐备 ⇒ `released` 记录；逐格来源键、逐列支撑、逐表证明、
  三条权威声明、`release_id` 可**外部复算**（且绑定 `proof_id`）。
- **G5 结构状态**：`complete` 带逐格缺口（自相矛盾）；`partial` **一律拒发**——空口
  partial 与带真实缺口的 partial 都只留诊断、零放行，缺陷逐条在 `defects` 里。
- **G6 逐格来源**：只有不可引用片段 ⇒ `cell_source_not_citable`；样例条数上限与
  "被截断"必须**显式**声明。
- **G7 宿主身份与对账**：七个宿主绑定字段逐项漂移、成员重复、`table_count` 不平、
  快照版本漂移 ⇒ 一律 fail-closed（不是"某张表不合格"）；读数与成员**不等集**同样
  抛错（不得跳过任何一张表）。
- **G8 消费侧复核**：`validate_release_id` 外部重算、`record_is_reading_material`、
  `released_fragment_keys`、批次身份复算、确定性 JSON、与 `tobj-*` 身份**不得互认**。
- **G9 真实 wire 不可达的状态**：`no_source` 与 `structure_state_unregistered` 经
  **真实** `tc-3` / `to-4` 构造期即被拒绝，`gto-3` 里它们是第二道 fail-closed。
  不把它们当成"日常会发生的缺陷"，也不因为够不到就删掉判据。
- **G10 逐表范围守恒（§0.19 核心）**：本表矩形里的**残余字符**（没人主张、也没正式
  延期）与**跨表重叠**字符一律使该表拒发，并带出**精确区间**；非语义空白单列；被本表
  typed 缺口精确覆盖的字符记作"已延期"（诚实未决），**不**使表拒发。

夹具的真伪边界（必须先说清）
--------------------------------------------------------------------------
本模块的**表格对象是真的**：全部经生产 `TS.TableCellSourceRef.create()` /
`TableCellV4.create()` / `TableRowV4(...)` / `TableObjectV4.create()` 构造，`tc-3`
的每一条构造期不变量都真的跑过。它们是**合成**的，只用于 wire 与放行判据，不主张
任何真实文档内容。

快照是**结构性替身**（`_Snapshot`）：`gto-3` 只按属性读取快照（宿主绑定七字段、
`tables`、`table_count`、`gaps.entries`、两个身份字段、两个版本字段），因此本模块
只提供这些读数，而**不**重造 `fms-1` 的构造期不变量（那由 `test_tree_table_schema`
与 `test_tree_final_material` 负责）。这里也**没有**任何"资格"被伪造：资格只由
`LiveTableSource` 的终端状态给出，本模块用 `_FixtureSource`（`LiveTableSource` 的
子类，只提供终端读数）把两种终态分别摆出来，并在 G2 里单独钉死"入口只认
`LiveTableSource`"这一条反自证约束。

`gto-3` 另需**逐表范围读数**。真实链上它由
`LiveTableSource.table_scope_readings()` 在同一份图侧来源上重算；本模块的夹具是合成
表、没有真实 PageLayout，因此 `_FixtureSource.table_scope_readings()` **自己声明**
读数（每个可引用 cell 一段、全归本表所有），并允许注入残余／跨表原子做反例。这是**夹具
的合成输入**，不是对真实判据的替代：真实链上的读数是 `_layout_text_scan` 算的，其
分区判据由 `test_tree_final_material` 与 TS5 验收负责。

为什么不拿冻结的真实快照当夹具：`evaluation/results/tree_table_ts5_*/` 下的
`fms-1` 快照其 cells 是 **`tc-2`**，当前 wire 是 `tc-3`；`from_dict` 明确拒绝
"不得静默解释为新版本"。要让它们可读必须先做显式迁移，那是另一件事（会动冻结
读数的解释），不在本模块内做，也不在本模块内假装做过。

只读：不写任何文件、不建库、不联网、不调 LLM。
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
import time

from document_structure import final_material_builder as FMB
from document_structure import live_table_source as LTS
from document_structure import table_local_proof as TLP
from document_structure import table_schema as TS
from document_structure import versions as V
from document_structure.canonical import SchemaValidationError

from harness import graph_table_release as GTR
from harness import table_object_release as TOR
from harness import topic_schema as HTS

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def raises(fn, exc, substr, msg):
    try:
        fn()
    except exc as e:
        text = str(e)
        if substr in text:
            _results["passed"] += 1
            _results["details"].append("PASS " + msg)
            return True
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 异常信息不含 {substr!r}：{text!r}")
        return False
    except Exception as e:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 抛出 {type(e).__name__} 而非 {exc.__name__}：{e}")
        return False
    _results["failed"] += 1
    _results["details"].append(f"FAIL {msg} —— 未抛出 {exc.__name__}")
    return False


# ---------------------------------------------------------------------------
# 0. 测试自带真值表（**不复用生产常量作为期望**）
# ---------------------------------------------------------------------------

_GTO_AXIS = ("gto-3", "gto-3")
#: `gto-1` / `gto-2` 是**被取代**的轴（`gto-1` 下 `partial` 曾可放行；`gto-2` 下文档级被拒
#: ⇒ 零放行对象）。两者都必须被**显式识别**并让读回 fail-closed，绝不能被静默按 `gto-3`
#: 解释 —— 否则旧记录会被读成"与产生时相同的结论"。
_GTO_LEGACY_RULE_AXIS = ("gto-1", "gto-2")
_GTO_LEGACY_SCHEMA_AXIS = ("gto-1", "gto-2")
#: `gto-2` 产生、`gto-3` **不再产生**的批次状态（只读识别）。
_LEGACY_BATCH_STATES = ("document_refused",)
_TOBJ_AXIS = ("tobj-2", "tobj-2")

_BATCH_STATES = ("no_table_objects", "accounted")
#: `gto-3` 把理由码收粗成"签发与否"两个；逐表缺陷改由 `tlp-1` 的 `defects` 逐条承载。
_RELEASE_REASONS = ("released", "table_local_proof_not_passed")
#: `gto-1` / `gto-2` 的七个拒发理由码（只读识别表）。
_LEGACY_RELEASE_REASONS = (
    "structure_state_unregistered",
    "structure_state_contradicts_cell_gaps",
    "partial_without_typed_gap",
    "partial_structure_not_releasable",
    "cell_provenance_incomplete",
    "cell_source_not_citable",
)
_CELL_STATES = ("citable", "unproven_only", "not_citable", "no_source")
_GAP_BASES = ("typed_gap_entry", "cell_unproven_fragments")
_PERMITTED_USE = "navigable_reading_material_only"
_MAX_PROBLEM_EXAMPLES = 3
_MAX_GAP_DETAIL_CODES = 12
#: `tlp-1` 的逐表证明规则/载荷版本（本模块自带字面量，不复用生产常量当期望）。
_TLP_AXIS = ("tlp-1", "tlp-1")

#: 放行记录里**不得**出现的金额/权威字段名（子串匹配，大小写不敏感）。
_FORBIDDEN_VALUE_FIELD_HINTS = (
    "amount", "金额", "currency", "numeric_value", "number_value", "figure",
    "authority_amount", "money",
)
_QUALIFICATION_KEYS = frozenset({
    "reading_material", "numeric_authority", "financial_authority_claimed",
    "permitted_use",
})

_SHA = "a" * 64
_DOC_ID = "gtr-fixture-doc"
_DOC_VERSION = "gtr-fixture-dv-1"
_EVIDENCE_SET_VERSION = "gtr-fixture-es-1"
_PAGE_LAYOUT_ID = "gtr-fixture-pl-1"
_OUTLINE_ID = "gtr-fixture-ol-1"
_VSS_ID = "gtr-fixture-vss-1"
_DEP_FP = "b" * 64
_SNAPSHOT_ID = "fms-fixture-1"
_SNAPSHOT_LOCATOR = "fms-fixture-locator-1"


# ---------------------------------------------------------------------------
# 1. 夹具：真实 wire 的合成表对象
# ---------------------------------------------------------------------------


def _cell_bbox(row: int, column: int) -> tuple:
    x0 = 100.0 + column * 80.0
    y0 = 300.0 + row * 20.0
    return (x0, y0, x0 + 70.0, y0 + 18.0)


def _ref(*, page: int, line: int, span: int, text_len: int, citable: bool):
    lo, hi = 0, max(1, text_len)
    return TS.TableCellSourceRef.create(
        interval=TS.TableSourceInterval(
            page_number=page, line_index=line, span_index=span,
            span_char_range=(lo, hi),
            bbox=(100.0 + span * 10.0, 300.0 + line * 10.0,
                  190.0 + span * 10.0, 308.0 + line * 10.0)),
        component_id=f"gtr-cmp-{line}-{span}",
        component_locator=f"gtr-cmp-{line}-{span}",
        # 块 id 是**文档级**身份（生产里由 Evidence 给出），因此必须带页：同一份文档里的两张
        # 表若都从行 0 起，不带页就会把两张表折进同一个块 id，夹具本身就不自洽。
        evidence_block_id=f"gtr-blk-{page}-{line}",
        evidence_char_range=(lo, hi),
        terminal_kind="alignment",
        terminal_id=f"gtr-aln-{line}-{span}",
        terminal_locator=f"gtr-aln-{line}-{span}",
        terminal_schema_version=V.VERSION_CONSTANTS["ALIGN_SCHEMA_VERSION"],
        verdict="aligned" if citable else "unaligned")


def _cell(*, row: int, column: int, spec, page: int):
    """一个真实 `TableCellV4`。

    `spec` 为字符串（可引用 cell）或字典：`{"text", "citable", "unproven"}`。
    带 `unproven` 的 cell 必须是空 refs / 空 blocks（`tc-3`：缺口必须逐条在册）。
    """
    s = spec if isinstance(spec, dict) else {"text": spec}
    unproven = tuple(sorted(tuple(x) for x in s.get("unproven", ())))
    if unproven:
        return TS.TableCellV4.create(
            row=row, column=column, rowspan=1, colspan=1,
            bbox=_cell_bbox(row, column), blocks=(), source_refs=(),
            unproven_fragments=unproven)
    text = str(s.get("text", ""))
    ref = _ref(page=page, line=row, span=column, text_len=len(text),
               citable=bool(s.get("citable", True)))
    block = TS.TableCellBlock.create(
        block_index=0, role="line", text=text,
        source_ref_ids=(ref.source_ref_id,))
    return TS.TableCellV4.create(
        row=row, column=column, rowspan=1, colspan=1,
        bbox=_cell_bbox(row, column), blocks=(block,), source_refs=(ref,))


def _text_block(*, page: int, line: int, span: int, text: str, block_index: int = 0):
    """表级文本块（单位 / 表题 / 附注）+ 它**唯一**消费的那条来源片段。

    `to-4` 要求表级块与 cell 不共用来源片段，且 `unit_text` 必须由 `unit_blocks` 以冻结
    分隔符重构。这里给出一条**自身自洽**的最小构造：一段文本、一条片段、一个 block。
    """
    ref = _ref(page=page, line=line, span=span, text_len=len(text), citable=True)
    block = TS.TableCellBlock.create(
        block_index=block_index, role="line", text=text,
        source_ref_ids=(ref.source_ref_id,))
    return block, ref


def _owner(*, page: int) -> TS.TableOwnerRef:
    boundary = f"gtr-boundary-{page}"
    return TS.TableOwnerRef(
        owner_kind="outline_node",
        node_id="gtr-node-1", outline_locator="gtr-node-1",
        source_boundary_kind="ts4_body_disposition",
        source_boundary_locator=boundary, source_boundary_id=boundary,
        source_boundary_start_page=page, source_boundary_end_page=page,
        unassigned_ref_kind=None, unassigned_ref_locator=None,
        unassigned_ref_id=None)


def _table(*, grid, page: int = 1, structure_state: str = "complete",
           structure_state_reason=None,
           structure_kind: str = "headered_grid",
           structure_class: str = "ordinary_business_table",
           source_order_index: int = 0, unit_text: str | None = None,
           **identity_overrides):
    """真实 `TableObjectV4`：头一行 header、其余 body，全部 identity 由生产 `create()` 派生。

    `unit_text` 是图侧读到的**逐字**单位（缺省 `None` = 读不到）。它必须与 `unit_blocks`
    同时存在，因此给单位时本函数会在表格数据行的**下一行**（`line = len(grid)`）补一条
    自洽的表级文本块 —— 与任何 cell 都不共用片段，也不参与逐格范围（那是 cell 的事）。
    """
    rows = []
    all_refs: list = []
    for r, spec_row in enumerate(grid):
        cells = tuple(_cell(row=r, column=c, spec=spec, page=page)
                      for c, spec in enumerate(spec_row))
        for cell in cells:
            all_refs.extend(cell.source_refs)
        rows.append(TS.TableRowV4(
            row_index=r, role="header" if r == 0 else "body", label=None,
            is_repeated_header=False, cells=cells))
    unit_blocks: tuple = ()
    if unit_text is not None:
        block, ref = _text_block(page=page, line=len(grid), span=0, text=unit_text)
        unit_blocks = (block,)
        all_refs.append(ref)
    identity = {
        "document_id": _DOC_ID, "document_version": _DOC_VERSION,
        "evidence_set_version": _EVIDENCE_SET_VERSION,
        "page_layout_id": _PAGE_LAYOUT_ID, "outline_id": _OUTLINE_ID,
        "verified_span_snapshot_id": _VSS_ID,
        "upstream_dependency_fingerprint": _DEP_FP,
    }
    unknown = sorted(set(identity_overrides) - set(identity))
    if unknown:  # 夹具自身的用法错误，不得静默忽略
        raise AssertionError(f"未知的宿主绑定覆盖字段：{unknown}")
    identity.update(identity_overrides)
    return TS.TableObjectV4.create(
        schema_version=V.VERSION_CONSTANTS["TABLE_SCHEMA_VERSION"],
        table_builder_version=V.VERSION_CONSTANTS["TABLE_BUILDER_VERSION"],
        cell_schema_version=V.VERSION_CONSTANTS["TABLE_CELL_SCHEMA_VERSION"],
        geometry_version=V.VERSION_CONSTANTS["TABLE_GEOMETRY_VERSION"],
        geometry_settings_fingerprint=_SHA,
        classification_profile_version=V.VERSION_CONSTANTS[
            "TABLE_CLASSIFICATION_PROFILE_VERSION"],
        classification_profile_file_fingerprint=_SHA,
        classification_profile_content_fingerprint=_SHA,
        cell_block_profile_version=V.VERSION_CONSTANTS[
            "TABLE_CELL_BLOCK_PROFILE_VERSION"],
        cell_block_profile_file_fingerprint=_SHA,
        cell_block_profile_content_fingerprint=_SHA,
        document_id=identity["document_id"],
        document_version=identity["document_version"],
        evidence_set_version=identity["evidence_set_version"],
        page_layout_id=identity["page_layout_id"],
        outline_id=identity["outline_id"],
        verified_span_snapshot_id=identity["verified_span_snapshot_id"],
        upstream_dependency_fingerprint=identity[
            "upstream_dependency_fingerprint"],
        page_number=page, page_bbox=(0.0, 0.0, 600.0, 800.0),
        source_order_index=source_order_index, owner=_owner(page=page),
        structure_kind=structure_kind, structure_class=structure_class,
        structure_state=structure_state,
        structure_state_reason=structure_state_reason,
        header_absence_reason=None, missing_or_uncertain_fields=(),
        column_count=len(grid[0]), title_blocks=(), unit_text=unit_text,
        unit_blocks=unit_blocks, note_blocks=(), rows=tuple(rows),
        source_refs=tuple(sorted(all_refs,
                                 key=lambda x: x.interval.sort_key())),
        continuation_anchor_locator=None, continuation_candidate_locators=())


def _gap_entry(*, table_id, page: int = 1, kind: str = "unsupported_table_structure",
               detail_code: str = "candidate_rejected:grid_not_closed"):
    return TS.TableGapEntry(
        gap_kind=kind, detail_code=detail_code, page_number=page,
        table_id=table_id, component_id=None, disposition_id=None,
        blocks_document_capability=kind in TS.GAP_BLOCKING_KINDS)


@dataclasses.dataclass(frozen=True)
class _Gaps:
    entries: tuple


@dataclasses.dataclass(frozen=True)
class _Snapshot:
    """`gto-1` 实际读取的快照读数（**不是** `fms-1` 构造期对象，见模块 docstring）。"""

    tables: tuple
    table_count: int
    gaps: _Gaps = dataclasses.field(default_factory=lambda: _Gaps(()))
    snapshot_id: str = _SNAPSHOT_ID
    snapshot_locator: str = _SNAPSHOT_LOCATOR
    schema_version: str = TS.FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION
    builder_version: str = V.FINAL_MATERIAL_BUILDER_VERSION
    document_id: str = _DOC_ID
    document_version: str = _DOC_VERSION
    evidence_set_version: str = _EVIDENCE_SET_VERSION
    page_layout_id: str = _PAGE_LAYOUT_ID
    outline_id: str = _OUTLINE_ID
    verified_span_snapshot_id: str = _VSS_ID
    upstream_dependency_fingerprint: str = _DEP_FP


def _snapshot(*tables, **overrides) -> _Snapshot:
    fields = {"tables": tuple(tables),
              "table_count": len(tables)}
    fields.update(overrides)
    return _Snapshot(**fields)


@dataclasses.dataclass(frozen=True)
class _Rel:
    """合成续表关系的**只读**替身（`gto-3` 只读 `relation_kind` 与两个 endpoint）。"""

    relation_kind: str
    source_endpoint: object
    target_endpoint: object


@dataclasses.dataclass(frozen=True)
class _Endpoint:
    table_locator: str
    table_id: str


def _fixture_rel(*, kind: str, source_locator: str, source_id: str,
                 target_locator: str, target_id: str) -> _Rel:
    return _Rel(relation_kind=kind,
                source_endpoint=_Endpoint(source_locator, source_id),
                target_endpoint=_Endpoint(target_locator, target_id))


def _fixture_readings(tables, *, extra_atoms=(), foreign_owners=None):
    """合成表 → `tlp-1` 要读的**范围读数**（测试自带的合成输入，见模块 docstring）。

    每个可引用 cell 的片段（`_ref` 给的 `(页, 行, 列)`）是一段本表范围内的原子，默认
    归**本表**所有（`owner_kind="table"`）。`unproven_fragments` 的片段**不**进范围——
    它们没有来源主张，这正是"缺口"的意思。

    两个反例注入点（都只改**读数**，不改表对象）：

    - `extra_atoms`：`(片段键, 起, 止, 逐字文本, owner_kind)` 的**无人主张**原子
      （`owner_kind="none"` 即残余；`"paragraph"` 即本表矩形里有别人的正文）；
    - `foreign_owners`：片段键 → `owner_kind`，把本表**自己主张**的原子改判为
      `"other_table"`（两张表都声称拥有同一段字符）。键相同即"同一段字符"，因此两张
      表若取同一个 `(页, 行, 列)` 片段，这个注入点正好建模真实的跨表重叠。
    """
    foreign = dict(foreign_owners or {})
    readings = []
    for table in tables:
        claims: set = set()
        atoms: list = []
        keys: set = set()
        for row in table.rows:
            for cell in row.cells:
                for ref in cell.source_refs:
                    iv = ref.interval
                    key = (iv.page_number, iv.line_index, iv.span_index)
                    lo, hi = int(iv.span_char_range[0]), int(iv.span_char_range[1])
                    claims.add((key[0], key[1], key[2], lo, hi, "cell"))
                    keys.add(key)
                    text = cell.text.ljust(hi - lo)[:hi - lo]
                    atoms.append(FMB.ScopeAtomReading(
                        fragment_key=key, start=lo, end=hi, kind="cell",
                        text=text, owners=(),
                        owner_kind=foreign.get(key, "table"),
                        in_region=True,
                        is_whitespace=bool(TS.is_non_semantic_whitespace(text))))
        for (key, lo, hi, text, owner_kind) in extra_atoms:
            keys.add(key)
            atoms.append(FMB.ScopeAtomReading(
                fragment_key=key, start=lo, end=hi, kind="free", text=text,
                owners=(), owner_kind=owner_kind, in_region=True,
                is_whitespace=bool(TS.is_non_semantic_whitespace(text))))
        readings.append(FMB.TableScopeReading(
            table_id=table.table_id, table_locator=table.table_locator,
            page_number=table.page_number,
            page_bbox=tuple(float(v) for v in table.page_bbox),
            region_fragment_keys=tuple(sorted(keys)),
            claims=tuple(sorted(claims)),
            atoms=tuple(sorted(atoms, key=lambda a: (a.fragment_key, a.start))),
            scan_problems=()))
    return tuple(readings)


class _FixtureSource(LTS.LiveTableSource):
    """两种终端态的**只读**摆件（本进程内的测试接缝）。

    它只提供 `release_graph_tables` 实际读取的那几个读数（含 `gto-3` 新增的
    `table_scope_readings`），因此"放行判据"被测到的是**生产代码**。生产资格仍然只由
    `LiveTableSource` 自己的终端态给出——本类不改写 `LiveTableSource.__init__`／
    `_assert_live` 的语义，G2 另有一条断言钉死"入口只认 `LiveTableSource`"。
    """

    def __init__(self, *, snapshot=None, refusal=None, readings=None,
                 document_id: str = _DOC_ID,
                 document_version: str = _DOC_VERSION) -> None:
        self._fixture_snapshot = snapshot
        self._fixture_refusal = refusal
        self._fixture_readings = readings
        self._fixture_document_id = document_id
        self._fixture_document_version = document_version

    @property
    def is_qualified(self) -> bool:
        # 与 `LiveTableSource` 同义：恰有**一种**终态。`gto-3` 的第一类情形正是
        # "快照在、但文档级被拒"（`is_qualified=False` 且 `snapshot is not None`）。
        return self._fixture_snapshot is not None and self._fixture_refusal is None

    @property
    def snapshot(self):
        return self._fixture_snapshot

    @property
    def verified(self):
        return {"fixture": "verified-capability"}

    @property
    def refusal(self):
        return self._fixture_refusal

    def table_scope_readings(self):
        if self._fixture_readings is None:
            raise AssertionError(
                "夹具未提供范围读数；正例必须显式给出（`gto-3` 的放行结论就是那份证明）")
        return self._fixture_readings

    @property
    def document_id(self) -> str:
        return self._fixture_document_id

    @property
    def document_version(self) -> str:
        return self._fixture_document_version


def _source(tables, *, refusal=None, extra_atoms=(), foreign_owners=None,
            snapshot=None, **overrides):
    """一个带**自洽范围读数**的已签发夹具来源。

    `snapshot` 与 `rejection` 互斥用法：只给 `rejection`（不给 `snapshot`）就是"快照
    从未诞生"的路径（`no_table_objects`）。
    """
    snap = snapshot if snapshot is not None else _snapshot(*tables, **overrides)
    readings = _fixture_readings(snap.tables, extra_atoms=extra_atoms,
                                foreign_owners=foreign_owners)
    return _FixtureSource(snapshot=snap, readings=readings, refusal=refusal)


def _unbuilt_source(*, kind="final_material_build_failed"):
    """快照**从未诞生**的来源（builder 自身抛错或复核前失败）：没有对象可证明。

    它**没有** `snapshot`，因此 `table_scope_readings()` 也不该被调用 —— 这正是
    `no_table_objects` 与"文档级被拒"两种情形的分界。
    """
    return _FixtureSource(refusal=_refusal(kind=kind))


def _refused_source(tables, *, kind="final_material_conservation_ineligible",
                    extra_atoms=(), foreign_owners=None, **overrides):
    """**文档级被拒但快照存在**的来源（§0.19 的核心情形）。"""
    snap = _snapshot(*tables, **overrides)
    readings = _fixture_readings(snap.tables, extra_atoms=extra_atoms,
                                foreign_owners=foreign_owners)
    return _FixtureSource(snapshot=snap, readings=readings,
                          refusal=_refusal(kind=kind))


def _document_state_member(*, qualified: bool, refusal=None) -> str:
    """批次身份里"文档级终态"那一位成员（`gto-3`）。

    外部复算时**不复用**生产函数：直接按同一份 canonical 摘要重算，才能证明这一位
    真的随文档级终态变化，而不是碰巧恒等于某个常量。
    """
    from document_structure.canonical import sha256_canonical
    digest = sha256_canonical({
        "document_qualified": bool(qualified),
        "document_refusal_kind": (
            None if refusal is None else refusal["refusal_kind"]),
        "document_refusal_detail": (
            None if refusal is None else refusal["detail"]),
    })
    return f"document_state:{digest}"


def _field_names(value, _out=None) -> set:
    """记录里出现过的**字段名**集合（递归；只看键，不看内容文本）。"""
    out = set() if _out is None else _out
    if isinstance(value, dict):
        for k, v in value.items():
            out.add(str(k))
            _field_names(v, out)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _field_names(item, out)
    return out


def _refusal(*, kind: str = "final_material_conservation_ineligible"):
    return LTS.LiveTableSourceRefusal(
        document_id=_DOC_ID, document_version=_DOC_VERSION, refusal_kind=kind,
        detail="夹具：图侧资格拒绝（合成读数，不主张任何真实文档内容）",
        error_type="SyntheticRefusal", blocking_kinds={}, layer_readings=(),
        problem_count=0, problem_examples=(),
        builder_table_count=3,
        builder_version=V.FINAL_MATERIAL_BUILDER_VERSION,
        verifier_version=V.VERIFIED_FINAL_MATERIAL_ISSUER_VERSION,
        source_version=LTS.LIVE_TABLE_SOURCE_VERSION)


#: 一行表头 + 一行表体，四格全部可引用：三条硬条件齐备的最小正例。
_GRID_OK = (("项目", "本期金额"), ("营业收入", "100"))


# ---------------------------------------------------------------------------
# G1 版本与封闭词表
# ---------------------------------------------------------------------------


def _g1_versions(_ctx) -> None:
    check((GTR.GRAPH_TABLE_RELEASE_RULE_VERSION,
           GTR.GRAPH_TABLE_RELEASE_SCHEMA_VERSION) == _GTO_AXIS,
          f"放行规则 / 载荷版本必须恰为 {_GTO_AXIS}，得到 "
          f"{(GTR.GRAPH_TABLE_RELEASE_RULE_VERSION, GTR.GRAPH_TABLE_RELEASE_SCHEMA_VERSION)}")
    check(GTR.GRAPH_TABLE_BATCH_STATES == _BATCH_STATES,
          f"批次状态封闭词表必须恰为 {_BATCH_STATES}，得到 {GTR.GRAPH_TABLE_BATCH_STATES}")
    check(GTR.LEGACY_GRAPH_TABLE_BATCH_STATES == _LEGACY_BATCH_STATES,
          f"被取代的批次状态必须**显式登记**为 {_LEGACY_BATCH_STATES}，得到 "
          f"{GTR.LEGACY_GRAPH_TABLE_BATCH_STATES}")
    check(GTR.classify_graph_table_batch_state(_BATCH_STATES[0]) == "current"
          and GTR.classify_graph_table_batch_state(_LEGACY_BATCH_STATES[0]) == "legacy"
          and GTR.classify_graph_table_batch_state(None) == "unknown"
          and GTR.classify_graph_table_batch_state("accounted_v2") == "unknown",
          "批次状态分类必须是 current / legacy / unknown 三态，未知值一律 unknown"
          "（fail-closed）")
    check((GTR.LEGACY_GRAPH_TABLE_RELEASE_RULE_VERSIONS,
           GTR.LEGACY_GRAPH_TABLE_RELEASE_SCHEMA_VERSIONS)
          == (_GTO_LEGACY_RULE_AXIS, _GTO_LEGACY_SCHEMA_AXIS),
          "被取代的两个轴都必须**显式登记**为 "
          f"{(_GTO_LEGACY_RULE_AXIS, _GTO_LEGACY_SCHEMA_AXIS)}，得到 "
          f"{(GTR.LEGACY_GRAPH_TABLE_RELEASE_RULE_VERSIONS, GTR.LEGACY_GRAPH_TABLE_RELEASE_SCHEMA_VERSIONS)}")
    check(GTR.classify_graph_table_release_rule_version(_GTO_AXIS[0]) == "current"
          and all(GTR.classify_graph_table_release_rule_version(v) == "legacy"
                  for v in _GTO_LEGACY_RULE_AXIS)
          and GTR.classify_graph_table_release_rule_version("gto-9") == "unknown"
          and GTR.classify_graph_table_release_rule_version(None) == "unknown",
          "版本分类必须是 current / legacy / unknown 三态，未知值一律 unknown（fail-closed）")
    check(GTR.classify_graph_table_release_schema_version(_GTO_AXIS[1]) == "current"
          and all(GTR.classify_graph_table_release_schema_version(v) == "legacy"
                  for v in _GTO_LEGACY_SCHEMA_AXIS),
          "载荷 schema 轴的分类必须与规则轴同形")
    check(GTR.GRAPH_TABLE_RELEASE_REASONS == _RELEASE_REASONS,
          f"理由码必须恰为 {_RELEASE_REASONS}（`gto-3` 收粗为'签发与否'），得到 "
          f"{GTR.GRAPH_TABLE_RELEASE_REASONS}")
    check(GTR.LEGACY_GRAPH_TABLE_RELEASE_REASONS == _LEGACY_RELEASE_REASONS,
          f"旧轴的七个拒发理由码必须**显式登记**为 {_LEGACY_RELEASE_REASONS}，得到 "
          f"{GTR.LEGACY_GRAPH_TABLE_RELEASE_REASONS}")
    check(not (set(GTR.GRAPH_TABLE_RELEASE_REASONS)
               & set(GTR.LEGACY_GRAPH_TABLE_RELEASE_REASONS)),
          "新旧理由码词表不得有交集：同一串既是新码又是旧码就必然被按错轴读")
    check(GTR.CELL_STATES == _CELL_STATES,
          f"逐格状态封闭词表必须恰为 {_CELL_STATES}，得到 {GTR.CELL_STATES}")
    check(GTR.CELL_STATES == TLP.CELL_STATES,
          "逐格状态词表必须与逐表证明（`tlp-1`）的同名词表**是同一个对象**："
          "两处各写一份必然漂移")
    check((TLP.TABLE_LOCAL_PROOF_RULE_VERSION, TLP.TABLE_LOCAL_PROOF_SCHEMA_VERSION)
          == _TLP_AXIS,
          f"逐表证明的规则 / 载荷版本必须恰为 {_TLP_AXIS}，得到 "
          f"{(TLP.TABLE_LOCAL_PROOF_RULE_VERSION, TLP.TABLE_LOCAL_PROOF_SCHEMA_VERSION)}")
    check(len(set(TLP.TABLE_LOCAL_PROOF_DEFECTS)) == len(TLP.TABLE_LOCAL_PROOF_DEFECTS)
          == len(TLP.DEFECT_STEP),
          "缺陷码闭集不得有重复，且每一个都必须有**恰好一个**七步归属"
          f"（{len(TLP.TABLE_LOCAL_PROOF_DEFECTS)} 个码）")
    check(set(TLP.DEFECT_STEP.values()) == {"constructed_complete", "self_proven"},
          "缺陷只能归属到本层能判的那两步：不得把缺陷挂到越界步骤上")
    check(TLP.STEP_LAYER_OWNERSHIP["numeric_authority_granted"] == "numeric_layer"
          and TLP.STEP_LAYER_OWNERSHIP["in_pack"] == "release_layer",
          "七步验收里非本层的步骤必须显式标出归属层，不得由本层代答")
    check(GTR.GAP_BASES == _GAP_BASES,
          f"缺口依据封闭词表必须恰为 {_GAP_BASES}，得到 {GTR.GAP_BASES}")
    check(GTR.GRAPH_TABLE_PERMITTED_USE == _PERMITTED_USE,
          f"允许用途必须恰为 {_PERMITTED_USE!r}，得到 "
          f"{GTR.GRAPH_TABLE_PERMITTED_USE!r}")
    check(TLP.TABLE_LOCAL_PROOF_PERMITTED_USE == _PERMITTED_USE,
          "逐表证明的允许用途必须与放行记录**同一措辞**（否则读回会以为是两种许可）")
    check(GTR._MAX_PROBLEM_EXAMPLES == _MAX_PROBLEM_EXAMPLES
          and GTR._MAX_GAP_DETAIL_CODES == _MAX_GAP_DETAIL_CODES,
          "审计面必须是**有界且已声明**的：样例上限 "
          f"{_MAX_PROBLEM_EXAMPLES}、缺口码上限 {_MAX_GAP_DETAIL_CODES}")
    # 错误族：`harness` 侧与 `document_structure` 侧是**两族并列**类型（都不是彼此的子类），
    # 都只是 `ValueError`。消费方只 catch 一族就会漏掉另一族——这条事实必须留在回归里。
    check(issubclass(GTR.GraphTableReleaseError, HTS.SchemaValidationError),
          "图侧放行错误必须属于 harness 侧结构校验错误族（fail-closed）")
    check(issubclass(GTR.GraphTableReleaseError, ValueError)
          and issubclass(HTS.SchemaValidationError, ValueError)
          and issubclass(SchemaValidationError, ValueError),
          "两族错误都必须是可捕获的 ValueError")
    check(not issubclass(HTS.SchemaValidationError, SchemaValidationError)
          and not issubclass(SchemaValidationError, HTS.SchemaValidationError),
          "harness 侧与 document_structure 侧错误族互相**不**是子类："
          "放行模块抛出的错误不可能被只 catch 文档侧那一个的调用方捕获")
    for name in ("RELEASE_REASON_RELEASED", "RELEASE_REASON_PROOF_NOT_PASSED",
                 "CELL_STATE_CITABLE", "GAP_BASIS_TYPED_GAP_ENTRY",
                 "GAP_BASIS_CELL_UNPROVEN_FRAGMENTS", "CELL_STATE_NO_SOURCE",
                 "CELL_STATE_NOT_CITABLE", "GRAPH_TABLE_BATCH_NO_TABLE_OBJECTS",
                 "GRAPH_TABLE_BATCH_ACCOUNTED",
                 "LEGACY_GRAPH_TABLE_RELEASE_REASONS",
                 "LEGACY_GRAPH_TABLE_BATCH_STATES",
                 "classify_graph_table_batch_state"):
        check(hasattr(GTR, name), f"缺少已声明的封闭码常量 {name}")

    # 纯转换模块：不 I/O、不联网。
    for module in (GTR, TLP):
        src = inspect.getsource(module)
        for token in ("sqlite3", "requests", "urllib", "subprocess", "http.client",
                      "socket", "open("):
            check(token not in src,
                  f"`{module.__name__}` 必须是纯转换模块：源码不得出现 {token!r}")


# ---------------------------------------------------------------------------
# G2 入口与反自证
# ---------------------------------------------------------------------------


def _g2_entry_gate(_ctx) -> None:
    table = _table(grid=_GRID_OK)
    snapshot = _snapshot(table)
    raises(lambda: GTR.release_graph_tables({"snapshot": snapshot}),
           GTR.GraphTableReleaseError, "LiveTableSource",
           "普通映射不得作为图侧来源（唯一入口只接受已签发的 LiveTableSource）")
    raises(lambda: GTR.release_graph_tables(None),
           GTR.GraphTableReleaseError, "LiveTableSource",
           "None 不得作为图侧来源")

    shape_snapshot = snapshot

    class _SameShape:
        """形状完全一致、类型不同的 duck 对象：字段一样不产生资格。"""

        document_id = _DOC_ID
        document_version = _DOC_VERSION
        is_qualified = True
        snapshot = shape_snapshot
        verified = {"fixture": True}
        refusal = None

    raises(lambda: GTR.release_graph_tables(_SameShape()),
           GTR.GraphTableReleaseError, "LiveTableSource",
           "同形 duck 对象不得通过入口（字段长得一样不产生资格）")

    source = _source((table,))
    check(isinstance(source, LTS.LiveTableSource),
          "测试接缝本身必须是 LiveTableSource 的子类（否则 G2 测的就不是同一件事）")
    batch = GTR.release_graph_tables(source)
    check(batch["batch_state"] == "accounted",
          "已签发来源必须产生 accounted 批次")

    # `gto-3` 的反自证再收紧一条：来源必须拿得出**与成员等集**的范围读数。
    # 不提供读数而谈放行，等于凭"对象说自己完整"放行 —— 正是 §0.19 要堵的那条路。
    no_readings = _FixtureSource(snapshot=snapshot)
    raises(lambda: GTR.release_graph_tables(no_readings), AssertionError,
           "夹具未提供范围读数",
           "没有范围读数的来源必须在下游失败（本夹具用断言模拟真实链上"
           "`table_scope_readings()` 的 fail-closed）")


# ---------------------------------------------------------------------------
# G3 没有对象可证明（快照从未诞生）
# ---------------------------------------------------------------------------


def _g3_no_table_objects(_ctx) -> None:
    refusal = _refusal(kind="final_material_build_failed")
    source = _unbuilt_source()
    check(source.is_qualified is False, "拒绝态来源不得自称已取得资格")
    check(source.snapshot is None,
          "`final_material_build_failed` 的来源**没有**快照（快照从未诞生）")
    batch = GTR.release_graph_tables(source)
    check(batch["batch_state"] == "no_table_objects",
          f"快照从未诞生必须返回 no_table_objects，得到 {batch['batch_state']!r}")
    check(batch["released"] == [] and batch["refusals"] == [],
          "没有对象可证明时不得产出任何逐表放行或逐表拒绝（没有快照就没有'逐表'）")
    check(batch["released_table_count"] == 0 and batch["refused_table_count"] == 0,
          "没有对象可证明时放行 / 拒绝计数必须都是 0")
    check(batch["declared_table_count"] is None,
          "不得自报表数（builder 读数不构成资格，因此不写进声明位）")
    check(batch["accounting_balanced"] is False,
          "不得自称已对账（没有任何逐表读数，无从对账）")
    check(batch["document_refusal"] == refusal.to_dict(),
          "拒绝读数必须**逐字**进入批次（不是概括成一句话）")
    check(batch["content_qualification"]["reading_material"] is False
          and batch["content_qualification"]["numeric_authority"] is False
          and batch["content_qualification"]["financial_authority_claimed"] is False,
          "被拒批次的三条权威声明必须全部为否")
    check(batch["content_qualification"]["permitted_use"] == _PERMITTED_USE,
          "被拒批次也必须带允许用途（读的人不必去猜）")
    expected = GTR.graph_batch_id(
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        batch_state="no_table_objects",
        member_ids=[f"document_refusal:{refusal.refusal_kind}"])
    check(batch["batch_id"] == expected,
          "批次身份必须可由外部按 (文档, 状态, 拒绝种类) 复算")

    for kind in LTS.GRAPH_TABLE_REFUSAL_KINDS:
        check(_refusal(kind=kind).refusal_kind == kind,
              f"四类图侧拒绝 {kind!r} 都是可构造的正式读数")
    raises(lambda: _refusal(kind="something_else"), LTS.LiveTableSourceError,
           "未登记的图侧拒绝种类",
           "未登记的拒绝种类必须在构造期被拒绝（拒绝也不是自由字符串）")


# ---------------------------------------------------------------------------
# G3b 文档级被拒、但某张表自证通过（§0.19 换轴的核心正例）
# ---------------------------------------------------------------------------


def _g3b_document_refused_table_independently_proven(_ctx) -> None:
    table = _table(grid=_GRID_OK)
    source = _refused_source((table,))
    check(source.is_qualified is False and source.snapshot is not None,
          "该情形必须同时满足：文档级被拒（is_qualified False）**且**快照存在")
    batch = GTR.release_graph_tables(source)
    check(batch["batch_state"] == "accounted",
          "快照存在就必须逐表证明并对账（accounted），而不是退回文档级一票否决")
    check(batch["document_qualified"] is False,
          "批次必须逐字记录**文档级没有取得资格**（不得因为逐表通过就抹掉这一事实）")
    check(batch["document_refusal"] is not None
          and batch["document_refusal"]["refusal_kind"]
          == "final_material_conservation_ineligible",
          "文档级拒发读数必须逐字在场（全文档问题留在账上）")
    check(len(batch["released"]) == 1 and batch["refusals"] == [],
          "自证完整的表必须被放行：无关页面的整文档问题不再自动否决它")
    record = batch["released"][0]
    check(record["local_proof"]["host_document_state"] == "refused"
          and record["local_proof"]["host_document_refusal_kind"]
          == "final_material_conservation_ineligible",
          "放行记录里的逐表证明必须带上**宿主文档级终态**："
          "否则读回会把'文档被拒'读成'整份文档都过了'")
    check(record["local_proof"]["numeric_authority"] is False
          and record["local_proof"]["numeric_authority_reason"]
          == TLP.NUMERIC_AUTHORITY_REASON,
          "逐表证明**不**授予数字权威，且必须写明原因")
    check(record["local_proof"]["steps"]["numeric_authority_granted"] is False
          and record["local_proof"]["steps"]["in_pack"] == "out_of_layer",
          "七步验收里非本层的步骤如实写 out_of_layer，不得由本层代答")
    # 批次身份必须把**文档级终态**绑进去：同一批表在两种文档态下是不同批次。
    same_batch_qualified = GTR.release_graph_tables(_source((table,)))
    check(same_batch_qualified["batch_id"] != batch["batch_id"],
          "同一批表在'文档已取得资格'与'文档级被拒'两种情形下必须是**不同**的批次"
          "（否则读回无法区分两次签发）")


# ---------------------------------------------------------------------------
# G4 放行正例
# ---------------------------------------------------------------------------


def _g4_released(_ctx) -> None:
    table = _table(grid=_GRID_OK)
    batch = GTR.release_graph_tables(_source((table,)))
    check(batch["batch_state"] == "accounted" and len(batch["released"]) == 1
          and batch["refusals"] == [],
          "三条件齐备的表必须被放行，且恰好一条放行、零条拒绝")
    record = batch["released"][0]
    check(record["reason"] == GTR.RELEASE_REASON_RELEASED and record["released"] is True,
          "放行记录的理由必须是 released")
    check(record["table_id"] == table.table_id
          and record["table_locator"] == table.table_locator,
          "放行记录必须带回图侧两套身份（table_id / table_locator）")
    check(record["final_material_snapshot_id"] == _SNAPSHOT_ID
          and record["final_material_snapshot_locator"] == _SNAPSHOT_LOCATOR,
          "放行记录必须绑定**哪一次**图侧签发")
    check(record["typed_gap_entry_count"] == 0
          and record["unproven_cell_count"] == 0
          and record["gap_basis"] is None,
          "无缺口的完整表：台账条目 0、逐格缺口 0，缺口依据**如实为 None**"
          "（不得借用任何一个缺口依据码来表示'没有缺口'）")
    check(record["unproven_fragment_keys"] == [],
          "放行记录不得带任何逐格缺口声明（partial 已一律拒发）")
    check(record["content_fingerprint"] == table.content_fingerprint
          and record["structure_fingerprint"] == table.structure_fingerprint
          and record["provenance_fingerprint"] == table.provenance_fingerprint,
          "三枚指纹必须逐字来自表对象，不由本模块重算")

    # 逐格回查：四格各自可引用，来源键就是四段真实片段。
    expected_keys = sorted({(r.interval.page_number, r.interval.line_index,
                             r.interval.span_index)
                            for c in table.all_cells for r in c.source_refs})
    check(record["fragment_keys"] == [list(k) for k in expected_keys],
          f"逐格来源键必须等于全部可引用片段的去重升序集："
          f"{record['fragment_keys']} != {[list(k) for k in expected_keys]}")
    check(GTR.released_fragment_keys(record)
          == tuple((int(a), int(b), int(c)) for a, b, c in expected_keys),
          "released_fragment_keys 必须与记录内的逐格来源键一致")
    check(all(c["cell_state"] == GTR.CELL_STATE_CITABLE
              for row in record["rows"] for c in row["cells"]),
          "正例里每一格都必须是 citable")
    check(record["cell_count"] == 4 and record["row_count"] == 2
          and record["body_row_count"] == 1 and record["column_count"] == 2,
          "逐表计数必须与真实行 / 格结构一致")
    check(record["header_rows"] == [[_GRID_OK[0][0], _GRID_OK[0][1]]],
          f"表头行必须逐字写出，得到 {record['header_rows']}")
    check(record["all_columns_supported"] is True
          and record["unsupported_columns"] == []
          and [c["supported"] for c in record["column_support"]] == [True, True],
          "两列都必须有**表体侧**可引用支撑（只由表头撑不算）")
    check(record["evidence_block_ids"] == ["gtr-blk-1-0", "gtr-blk-1-1"],
          f"引用的 Evidence 块必须逐条列出，得到 {record['evidence_block_ids']}")

    # 读取材料 ≠ 数字权威。
    q = record["content_qualification"]
    check(set(q) == set(_QUALIFICATION_KEYS),
          f"权威声明必须是封闭四条，得到 {sorted(q)}")
    check(q["reading_material"] is True and q["numeric_authority"] is False
          and q["financial_authority_claimed"] is False
          and q["permitted_use"] == _PERMITTED_USE,
          "放行记录必须逐字声明：是阅读材料、不是数字权威、不是财务权威")
    check(GTR.record_is_reading_material(record) is True,
          "record_is_reading_material 必须认这条记录")
    check(table.is_financial_authority() is False,
          "TableObjectV4 本身永远不是财务权威")

    field_names = _field_names(record)
    hits = sorted({h for h in _FORBIDDEN_VALUE_FIELD_HINTS
                   for name in field_names if h in name})
    check(not hits, f"放行记录不得出现任何金额 / 权威**字段名**，命中 {hits}")

    # 外部复算。
    check(GTR.validate_release_id(record) == record["release_id"],
          "release_id 必须可由外部按记录字段复算")
    tampered = dict(record)
    tampered["fragment_keys"] = record["fragment_keys"][:-1]
    raises(lambda: GTR.validate_release_id(tampered),
           GTR.GraphTableReleaseError, "复算不一致",
           "逐格来源被换掉后，release_id 复算必须失败")
    relocated = dict(record)
    relocated["table_locator"] = "to4-something-else"
    raises(lambda: GTR.validate_release_id(relocated),
           GTR.GraphTableReleaseError, "复算不一致",
           "表定位被换掉后，release_id 复算必须失败")
    stripped = {k: v for k, v in record.items() if k != "release_id"}
    raises(lambda: GTR.validate_release_id(stripped),
           GTR.GraphTableReleaseError, "缺字段",
           "缺 release_id 的记录必须被拒绝复核")
    raises(lambda: GTR.validate_release_id("not-a-mapping"),
           GTR.GraphTableReleaseError, "映射",
           "非映射不得进入复核")
    # 同一张表、不同逐格来源 ⇒ 不同 release_id（身份绑定来源，不只是绑定表）。
    other = _table(grid=_GRID_OK, source_order_index=1)
    check(other.table_locator != table.table_locator
          and other.table_id != table.table_id,
          "不同源序的同形表必须得到不同 locator / table_id（夹具自身可区分）")


# ---------------------------------------------------------------------------
# G5 结构状态
# ---------------------------------------------------------------------------


def _g5_structure_states(_ctx) -> None:
    # ① complete 却带逐格缺口：自相矛盾。
    contradicted = _table(grid=(("项目", "本期金额"),
                                ("营业收入", {"unproven": ((1, 1, 1),)})),
                          structure_state="complete")
    batch = GTR.release_graph_tables(_source((contradicted,)))
    check(len(batch["refusals"]) == 1 and batch["released"] == [],
          "自相矛盾的表不得放行")
    rec = batch["refusals"][0]
    check(rec["reason"] == TLP.DEFECT_STRUCTURE_CONTRADICTS_CELL_GAPS,
          f"理由必须是结构状态矛盾，得到 {rec['reason']!r}")
    check(list(rec["local_proof"]["defects"])
          == [TLP.DEFECT_STRUCTURE_CONTRADICTS_CELL_GAPS,
              TLP.DEFECT_COLUMN_WITHOUT_BODY_SOURCE],
          f"缺陷必须逐条在 `defects` 里（按封闭优先级），得到 "
          f"{rec['local_proof']['defects']}")
    check(rec["structure_state"] == "complete" and rec["unproven_cell_count"] == 1,
          "拒绝记录必须同时给出结构状态与逐格缺口计数（读的人不必猜）")
    check(rec["released"] is False
          and GTR.record_is_reading_material(rec) is False,
          "拒绝记录不得被当成阅读材料")
    check(rec["local_proof"]["steps"]["constructed_complete"] is False
          and rec["local_proof"]["steps"]["self_proven"] is False,
          "七步验收里②③都必须判否（两个缺陷分属这两步）")

    # ② 空口 partial：无台账缺口、无逐格缺口 ⇒ 结构状态本身就不是 complete。
    empty_partial = _table(grid=_GRID_OK, structure_state="partial",
                           structure_state_reason="candidate_grid_open")
    batch = GTR.release_graph_tables(_source((empty_partial,)))
    check(batch["refusals"][0]["reason"] == TLP.DEFECT_STRUCTURE_NOT_COMPLETE,
          "空口 partial 必须拒发（'partial' 一句话不是缺口在册），"
          f"得到 {batch['refusals'][0]['reason']!r}")

    # ③ partial + 台账条目（真实 typed 缺口）⇒ **仍然拒发**：缺口在册不是放行条件。
    gap_table = _table(grid=_GRID_OK, structure_state="partial",
                       structure_state_reason="candidate_grid_open")
    gaps = _Gaps((_gap_entry(table_id=gap_table.table_id),))
    batch = GTR.release_graph_tables(
        _source((gap_table,), gaps=gaps))
    check(batch["released"] == [] and len(batch["refusals"]) == 1,
          "带真实 typed 缺口的 partial 表**不得**放行——缺口在册是诚实未决，"
          "不是'已正式延期成完整表'")
    rec = batch["refusals"][0]
    check(rec["reason"] == TLP.DEFECT_STRUCTURE_NOT_COMPLETE
          and rec["problems"] == [TLP.DEFECT_STRUCTURE_NOT_COMPLETE],
          f"理由必须是结构状态不是 complete，得到 {rec['reason']!r} / {rec['problems']}")
    check(rec["gap_basis"] == GTR.GAP_BASIS_TYPED_GAP_ENTRY
          and rec["typed_gap_entry_count"] == 1
          and rec["typed_gap_detail_codes"] == ["candidate_rejected:grid_not_closed"],
          f"诊断面必须如实给出缺口依据（台账条目），得到 {rec['gap_basis']!r} / "
          f"{rec['typed_gap_detail_codes']}")
    check(rec["released"] is False
          and GTR.record_is_reading_material(rec) is False,
          "partial 的诊断记录不得被当成阅读材料／正式放行对象")

    # ④ partial + 逐格缺口声明（无台账条目）⇒ 同样拒发，诊断依据是逐格声明，
    #    且逐格缺口键必须**逐条**留在诊断面上（不能只剩一个计数）。
    cell_partial = _table(grid=(("项目", "本期金额"),
                                ("营业收入", {"unproven": ((1, 1, 2),)})),
                          structure_state="partial",
                          structure_state_reason="cell_interior_unclosed")
    batch = GTR.release_graph_tables(_source((cell_partial,)))
    check(batch["released"] == [], "带逐格缺口声明的 partial 表同样不得放行")
    rec = batch["refusals"][0]
    check(rec["reason"] == TLP.DEFECT_STRUCTURE_NOT_COMPLETE
          and rec["gap_basis"] == GTR.GAP_BASIS_CELL_UNPROVEN_FRAGMENTS
          and rec["unproven_cell_count"] == 1
          and rec["unproven_fragment_keys"] == [[1, 1, 2]],
          f"诊断面依据必须是逐格声明且带出缺口键，得到 {rec['reason']!r} / "
          f"{rec['gap_basis']!r} / {rec['unproven_fragment_keys']}")

    # ⑤ 多条缺陷同时命中：`defects` 按封闭优先级列全，单值 `reason` 取最先命中者。
    both = _table(grid=(("项目", "本期金额"),
                        ("营业收入", {"text": "100", "citable": False})),
                  structure_state="partial",
                  structure_state_reason="candidate_grid_open")
    batch = GTR.release_graph_tables(_source((both,)))
    rec = batch["refusals"][0]
    check(rec["problems"] == [TLP.DEFECT_STRUCTURE_NOT_COMPLETE,
                              TLP.DEFECT_CELL_SOURCE_NOT_CITABLE,
                              TLP.DEFECT_COLUMN_WITHOUT_BODY_SOURCE],
          f"problems 必须按封闭优先级列出全部命中缺陷，得到 {rec['problems']}")
    check(rec["reason"] == TLP.DEFECT_STRUCTURE_NOT_COMPLETE,
          f"单值 reason 必须取最先命中者，得到 {rec['reason']!r}")
    check([d for d in TLP.TABLE_LOCAL_PROOF_DEFECTS
           if d in rec["local_proof"]["defects"]]
          == rec["problems"],
          "拒绝记录的 problems 必须恰是缺陷码按优先级排序，不得增删")


# ---------------------------------------------------------------------------
# G6 逐格来源
# ---------------------------------------------------------------------------


def _g6_cell_sources(_ctx) -> None:
    table = _table(grid=(("项目", "本期金额"),
                         ("营业收入", {"text": "100", "citable": False})))
    batch = GTR.release_graph_tables(_source((table,)))
    rec = batch["refusals"][0]
    check(rec["reason"] == TLP.DEFECT_CELL_SOURCE_NOT_CITABLE,
          f"只有不可引用来源的 cell 必须得到 cell_source_not_citable，得到 {rec['reason']!r}")
    check(TLP.DEFECT_COLUMN_WITHOUT_BODY_SOURCE in rec["problems"],
          "该列已无**可引用**的正文格来源，必须同时记一条列级缺陷"
          "（逐格不可引用与整列无来源不是同一件事），"
          f"得到 {rec['problems']}")
    check(rec["citable_cell_count"] == 3 and rec["not_citable_cell_count"] == 1
          and rec["unproven_cell_count"] == 0,
          f"逐格计数必须是 3 可引用 / 1 不可引用 / 0 缺口，得到 "
          f"{rec['citable_cell_count']} / {rec['not_citable_cell_count']} / "
          f"{rec['unproven_cell_count']}")
    check(len(rec["cell_problem_examples"]) == 1
          and rec["cell_problem_examples_truncated"] is False,
          "样例未达上限时必须显式声明**没有**被截断")
    example = rec["cell_problem_examples"][0]
    check(example["cell_state"] == TLP.CELL_STATE_NOT_CITABLE
          and example["text"] == "100"
          and example["non_citable_reasons"] == ["verdict_not_aligned"],
          f"样例必须给出格状态与逐条不可引用理由，得到 {example}")

    # 上限必须显式生效：4 个不可引用格 ⇒ 只留 3 条并且声明已截断。
    wide = _table(grid=(("项目", "甲", "乙", "丙", "丁"),
                        ({"text": "a", "citable": False},
                         {"text": "b", "citable": False},
                         {"text": "c", "citable": False},
                         {"text": "d", "citable": False},
                         {"text": "e", "citable": False})))
    rec = GTR.release_graph_tables(_source((wide,)))[
        "refusals"][0]
    check(len(rec["cell_problem_examples"]) == _MAX_PROBLEM_EXAMPLES
          and rec["cell_problem_examples_truncated"] is True,
          f"样例必须截到 {_MAX_PROBLEM_EXAMPLES} 条并显式声明被截断，得到 "
          f"{len(rec['cell_problem_examples'])} / "
          f"{rec['cell_problem_examples_truncated']}")

    # 无来源格的**逐格状态**判据本身（真实 wire 构造不出这种 cell，见 G9）。
    class _BareCell:
        source_refs: tuple = ()
        unproven_fragments: tuple = ()
        citable_ref_count = 0

    check(TLP.cell_state(_BareCell()) == TLP.CELL_STATE_NO_SOURCE,
          "既无来源片段又无缺口声明的格必须判为 no_source")
    check(GTR.CELL_STATES is TLP.CELL_STATES,
          "逐格状态词表只有一处判据（tlp-1），放行层不得自带第二份")


# ---------------------------------------------------------------------------
# G7 宿主身份与对账
# ---------------------------------------------------------------------------


def _g7_host_binding(_ctx) -> None:
    for name, other in (
            ("document_id", "gtr-other-doc"),
            ("document_version", "gtr-fixture-dv-2"),
            ("evidence_set_version", "gtr-fixture-es-2"),
            ("page_layout_id", "gtr-fixture-pl-2"),
            ("outline_id", "gtr-fixture-ol-2"),
            ("verified_span_snapshot_id", "gtr-fixture-vss-2"),
            ("upstream_dependency_fingerprint", "c" * 64)):
        drifted = _table(grid=_GRID_OK, **{name: other})
        raises(lambda t=drifted: GTR.release_graph_tables(
                   _source((t,))),
               GTR.GraphTableReleaseError, name,
               f"宿主绑定 {name} 漂移必须 fail-closed（不放行、也不算'某张表不合格'）")

    table = _table(grid=_GRID_OK)
    raises(lambda: GTR.release_graph_tables(
               _source((table,), table_count=2)),
           GTR.GraphTableReleaseError, "table_count",
           "成员账不平（table_count 与成员数不一致）必须不放行任何东西")
    raises(lambda: GTR.release_graph_tables(
               _source((table, table), table_count=2)),
           GTR.GraphTableReleaseError, "重复出现",
           "同一张表在本次放行中重复出现必须 fail-closed")
    raises(lambda: GTR.release_graph_tables(
               _source((table,), schema_version="fms-9")),
           GTR.GraphTableReleaseError, "schema 版本",
           "快照 schema 版本漂移必须 fail-closed")
    raises(lambda: GTR.release_graph_tables(
               _source((table,), builder_version="fmb-9")),
           GTR.GraphTableReleaseError, "builder 版本",
           "快照 builder 版本漂移必须 fail-closed")

    empty = _snapshot(table_count=0)
    raises(lambda: GTR._assert_member(table, snapshot=empty, seen=set()),
           GTR.GraphTableReleaseError, "不在宿主快照的成员集中",
           "越出快照成员集的表不得被放行（对象级闸门）")
    raises(lambda: GTR._assert_member(table, snapshot=_snapshot(table),
                                      seen={table.table_id}),
           GTR.GraphTableReleaseError, "重复出现",
           "已在册的表不得被第二次放行")


# ---------------------------------------------------------------------------
# G8 消费侧复核与身份隔离
# ---------------------------------------------------------------------------


def _g8_consumer_checks(_ctx) -> None:
    table = _table(grid=_GRID_OK)
    batch = GTR.release_graph_tables(_source((table,)))
    check(batch["accounting_balanced"] is True
          and batch["declared_table_count"] == 1
          and batch["released_table_count"] + batch["refused_table_count"] == 1,
          "已签发批次的逐表读数必须与声明表数严格对账")
    expected = GTR.graph_batch_id(
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        batch_state="accounted",
        member_ids=[_document_state_member(qualified=True)]
        + [r["release_id"] for r in batch["released"]]
        + [GTR._refusal_digest(r) for r in batch["refusals"]])
    check(batch["batch_id"] == expected,
          "批次身份必须可由外部按 (文档, 状态, 文档级终态, 全部成员身份) 复算")
    without_state = GTR.graph_batch_id(
        document_id=_DOC_ID, document_version=_DOC_VERSION,
        batch_state="accounted",
        member_ids=[r["release_id"] for r in batch["released"]]
        + [GTR._refusal_digest(r) for r in batch["refusals"]])
    check(batch["batch_id"] != without_state,
          "批次身份必须把文档级终态绑进去：否则'文档已取得资格'与"
          "'文档级被拒但逐表独立证明通过'会算出同一个 batch_id")

    refusal_only = _table(grid=(("项目", "本期金额"),
                                ("营业收入", {"text": "100", "citable": False})))
    batch2 = GTR.release_graph_tables(
        _source((refusal_only,)))
    check(batch2["accounting_balanced"] is True
          and batch2["content_qualification"]["reading_material"] is False,
          "只有拒绝成员时批次自身仍对账，但整批不得自称阅读材料")

    # 只有 `partial` 成员的批次：对账成立、零放行、整批**不得**自称阅读材料。
    # 这是「`partial` 只能留诊断、不能进入正式 Pack」在**批次粒度**上的钉子。
    partial_only = _table(grid=_GRID_OK, structure_state="partial",
                          structure_state_reason="candidate_grid_open")
    batch3 = GTR.release_graph_tables(
        _source((partial_only,)))
    check(batch3["accounting_balanced"] is True
          and batch3["released"] == []
          and batch3["released_table_count"] == 0
          and batch3["refused_table_count"] == 1
          and batch3["content_qualification"]["reading_material"] is False,
          "只有 partial 成员的批次必须零放行、对账成立且整批不自称阅读材料")
    check(GTR.released_fragment_keys(batch3["refusals"][0]) == (),
          "拒绝记录不得被 released_fragment_keys 当成放行对象取出逐格来源键")

    rec = batch["released"][0]
    check(GTR.record_is_reading_material(rec) is True, "正例记录是阅读材料")
    tampered = json.loads(json.dumps(rec))
    tampered["content_qualification"]["numeric_authority"] = True
    check(GTR.record_is_reading_material(tampered) is False,
          "把 numeric_authority 翻成 True 后不得再被认作阅读材料")
    released_false = json.loads(json.dumps(rec))
    released_false["released"] = False
    check(GTR.record_is_reading_material(released_false) is False,
          "released=False 的记录不得被认作阅读材料")
    check(GTR.record_is_reading_material({"released": True}) is False,
          "缺 content_qualification 的记录不得被认作阅读材料")

    # **被取代轴的 fail-closed**（`gto-2` 新增，反例）：把一条**三条声明全部正确**的放行记录
    # 的 `release_rule_version` 换成 `gto-1`，它必须**立刻**不再是阅读材料 —— 因为 `gto-1`
    # 词表下 `partial` ＋缺口依据曾是**放行**正例，静默按 `gto-2` 读会把旧记录升格成合格表。
    legacy_rec = json.loads(json.dumps(rec))
    legacy_rec["release_rule_version"] = _GTO_LEGACY_RULE_AXIS[0]
    check(GTR.record_is_reading_material(legacy_rec) is False,
          "被取代轴（gto-1）的放行记录必须 fail-closed，不得被当成阅读材料")
    check(GTR.record_is_reading_material(rec) is True,
          "同一份记录在 current 轴下仍是阅读材料（上一条不得是靠别的字段碰巧为假）")
    unknown_rec = json.loads(json.dumps(rec))
    unknown_rec["release_rule_version"] = "gto-9"
    check(GTR.record_is_reading_material(unknown_rec) is False,
          "未登记版本必须 fail-closed（unknown 与 legacy 同等拒绝）")
    no_ver_rec = json.loads(json.dumps(rec))
    del no_ver_rec["release_rule_version"]
    check(GTR.record_is_reading_material(no_ver_rec) is False,
          "缺 release_rule_version 的记录必须 fail-closed（不得默认按 current 解释）")

    check(GTR.released_fragment_keys({"fragment_keys": [[2, 0, 0], [1, 3, 4],
                                                        [1, 3, 4]]})
          == ((1, 3, 4), (2, 0, 0)),
          "released_fragment_keys 必须去重并按 (页, 行, 片段) 升序")
    check(GTR.released_fragment_keys({}) == (),
          "缺 fragment_keys 时必须给出空元组，而不是抛错或造假")

    text_a = GTR.to_json(batch)
    text_b = GTR.to_json(json.loads(text_a))
    check(text_a == text_b and json.loads(text_a) == batch,
          "批次 JSON 必须是确定性的且可无损读回")

    # 两条并列通道的身份**不得互认**。
    check((TOR.RELEASE_RULE_VERSION, TOR.RELEASE_SCHEMA_VERSION) == _TOBJ_AXIS,
          f"文本侧重切通道的版本轴必须仍是 {_TOBJ_AXIS}（本模块不得顶替它）")
    gto_payload = GTR.graph_release_id_payload(
        final_material_snapshot_id=_SNAPSHOT_ID, verified_span_snapshot_id=_VSS_ID,
        upstream_dependency_fingerprint=_DEP_FP, table_id=table.table_id,
        table_locator=table.table_locator, structure_state=table.structure_state,
        local_proof_id=rec["local_proof_id"],
        fragment_keys=[(1, 1, 3)])
    check(gto_payload["schema_version"] == GTR.GRAPH_TABLE_RELEASE_SCHEMA_VERSION
          and gto_payload["release_rule_version"]
          == GTR.GRAPH_TABLE_RELEASE_RULE_VERSION,
          "放行身份 payload 必须自报本通道的两个版本")
    check(gto_payload["fragment_keys"] == [[1, 1, 3]],
          "放行身份 payload 的逐格来源键必须去重升序后逐字写出")
    check(gto_payload["local_proof_id"] == rec["local_proof_id"],
          "放行身份必须绑定**本次**逐表证明：放行结论就是那张证明，"
          "同一张表换一份证明必须是另一个 release_id")
    check(GTR.graph_release_id(
              final_material_snapshot_id=_SNAPSHOT_ID,
              verified_span_snapshot_id=_VSS_ID,
              upstream_dependency_fingerprint=_DEP_FP,
              table_id=table.table_id, table_locator=table.table_locator,
              structure_state=table.structure_state,
              local_proof_id=rec["local_proof_id"],
              fragment_keys=[(1, 1, 3)])
          .startswith("gtr-"),
          "通道内放行身份必须以 gtr- 前缀派生（与 to4- / tobj- 各自成一体）")
    check(GTR.graph_release_id(
              final_material_snapshot_id=_SNAPSHOT_ID,
              verified_span_snapshot_id=_VSS_ID,
              upstream_dependency_fingerprint=_DEP_FP,
              table_id=table.table_id, table_locator=table.table_locator,
              structure_state=table.structure_state,
              local_proof_id=rec["local_proof_id"],
              fragment_keys=[(1, 1, 3), (1, 1, 4)])
          != GTR.graph_release_id(
              final_material_snapshot_id=_SNAPSHOT_ID,
              verified_span_snapshot_id=_VSS_ID,
              upstream_dependency_fingerprint=_DEP_FP,
              table_id=table.table_id, table_locator=table.table_locator,
              structure_state=table.structure_state,
              local_proof_id=rec["local_proof_id"],
              fragment_keys=[(1, 1, 3)]),
          "同一张表、逐格来源不同必须得到不同 release_id")
    check(GTR.graph_release_id(
              final_material_snapshot_id=_SNAPSHOT_ID,
              verified_span_snapshot_id=_VSS_ID,
              upstream_dependency_fingerprint=_DEP_FP,
              table_id=table.table_id, table_locator=table.table_locator,
              structure_state=table.structure_state,
              local_proof_id="tlpp-0000000000000000",
              fragment_keys=[(1, 1, 3)])
          != GTR.graph_release_id(
              final_material_snapshot_id=_SNAPSHOT_ID,
              verified_span_snapshot_id=_VSS_ID,
              upstream_dependency_fingerprint=_DEP_FP,
              table_id=table.table_id, table_locator=table.table_locator,
              structure_state=table.structure_state,
              local_proof_id=rec["local_proof_id"],
              fragment_keys=[(1, 1, 3)]),
          "同一张表、逐格来源相同但逐表证明不同，release_id 必须不同"
          "（否则同一份放行身份可以指两份互不相同的证明）")


# ---------------------------------------------------------------------------
# G9 真实 wire 不可达的两条状态
# ---------------------------------------------------------------------------


def _g9_unreachable_states(_ctx) -> None:
    # 真实 wire 的构造期就拒绝这两件事：判据层里的对应缺陷是第二道 fail-closed。
    raises(lambda: TS.TableCellV4.create(
               row=0, column=0, rowspan=1, colspan=1, bbox=_cell_bbox(0, 0),
               blocks=(), source_refs=()),
           SchemaValidationError, "非空 cell 必须有真实来源片段",
           "真实 tc-3 wire 不允许既无来源片段又无缺口声明的 cell")
    raises(lambda: _table(grid=_GRID_OK, structure_state="broken"),
           SchemaValidationError, "structure_state 必须属于",
           "真实 to-4 wire 不允许未登记的结构状态")
    raises(lambda: _table(grid=_GRID_OK, structure_state="partial"),
           SchemaValidationError, "partial 必须给出 structure_state_reason",
           "真实 to-4 wire 不允许空口 partial（缺口必须逐条在册）")

    # 未登记的结构状态：判据层不得因为"构造期够不到"就把它当成 complete。
    # 判据只有一处（`tlp-1`），所以直接问判据层，而不是自己再比一次字符串。
    class _Unregistered:
        structure_state = "legacy_v0"
        rows: tuple = ()
        table_id = "to4-legacy-1"

    check(TLP._structure_defects(_Unregistered())
          == [TLP.DEFECT_STRUCTURE_NOT_COMPLETE],
          "未登记结构状态必须落成 structure_not_complete（不是'可放行'），"
          f"得到 {TLP._structure_defects(_Unregistered())}")

    # 缺陷词表是**封闭**的：真实 wire 够不到不等于可以删掉判据。
    for code in (TLP.DEFECT_CELL_PROVENANCE_INCOMPLETE,
                 TLP.DEFECT_STRUCTURE_NOT_COMPLETE,
                 TLP.DEFECT_CELL_SOURCE_NOT_CITABLE):
        check(code in TLP.TABLE_LOCAL_PROOF_DEFECTS,
              f"真实 wire 够不到不等于可以删掉判据：{code!r} 必须仍在缺陷词表里")
    check(TLP.DEFECT_CELL_PROVENANCE_INCOMPLETE in TLP.TABLE_LOCAL_PROOF_DEFECTS,
          "逐格来源不完整（无来源格）的判据必须仍在")
    src = inspect.getsource(TLP)
    for code in (TLP.DEFECT_CELL_PROVENANCE_INCOMPLETE,
                 TLP.DEFECT_STRUCTURE_NOT_COMPLETE):
        check(re.search(rf'"{re.escape(code)}"', src) is not None,
              f"该判据必须仍在判据层里可执行：{code!r}")

    # 逐格状态词表在**放行层**也必须可判：放行层只借用判据层的同一份实现。
    class _BareCell:
        source_refs: tuple = ()
        unproven_fragments: tuple = ()
        citable_ref_count = 0

    check(TLP.cell_state(_BareCell()) == TLP.CELL_STATE_NO_SOURCE,
          "无来源格必须判为 no_source（这是 cell_provenance_incomplete 的触发条件）")


# ---------------------------------------------------------------------------
# G10 逐表范围守恒（§0.19 核心：同一图侧来源上的逐表证明）
# ---------------------------------------------------------------------------


def _intervals(values) -> list:
    """区间序列 → 可比较的 `[[页, 行, 片段, 起, 止], ...]`（记录里是元组，JSON 里是列表）。"""
    return [list(x) for x in values]


def _gap_with_interval(*, table_id, page=1, key=(1, 0, 9),
                       char_range=(0, 4), kind="unsupported_table_structure"):
    """带**精确** `source_intervals` 的本表 typed 缺口条目（"已正式延期"的唯一依据）。"""
    return TS.TableGapEntry(
        gap_kind=kind, detail_code="candidate_rejected:cell_interior_unclosed",
        page_number=page, table_id=table_id, component_id=None,
        disposition_id=None,
        blocks_document_capability=kind in TS.GAP_BLOCKING_KINDS,
        source_intervals=(TS.TableGapSourceInterval(
            page_number=key[0], line_index=key[1], span_index=key[2],
            span_char_range=char_range),))


def _g10_scope_conservation(_ctx) -> None:
    # ⓪ 正例基线：范围内每个原子都被**本表**主张 ⇒ 无范围缺陷，逐项字符账可加。
    table = _table(grid=_GRID_OK)
    batch = GTR.release_graph_tables(_source((table,)))
    check(len(batch["released"]) == 1, "自洽范围读数必须放行该表")
    proof = batch["released"][0]["local_proof"]
    check(not proof["defects"] and proof["residual_char_count"] == 0
          and proof["deferred_char_count"] == 0
          and proof["whitespace_char_count"] == 0,
          f"基线范围里既无残余、也无延期、也无空白，得到 "
          f"{proof['defects']} / {proof['residual_char_count']}")
    check(proof["owned_char_count"] == proof["region_char_count"],
          "基线里'已被本表主张的字符数'必须等于范围内字符数"
          f"（{proof['owned_char_count']} vs {proof['region_char_count']}）")
    check(proof["numeric_authority"] is False
          and proof["steps"]["numeric_authority_granted"] is False
          and proof["steps"]["in_pack"] == "out_of_layer",
          "逐表证明**不**授予数字权威，也**不**代答④⑤⑥三步")

    # ① 残余：范围内一段字符既没被本表主张、也没有本表 typed 缺口覆盖 ⇒ 拒发，
    #    且**精确区间**必须逐条留在记录里（读的人要能回查是哪一段原文）。
    residue_key = (1, 7, 0)
    residue = _table(grid=_GRID_OK)
    batch = GTR.release_graph_tables(_source(
        (residue,), extra_atoms=((residue_key, 0, 5, "未主张正文", "none"),)))
    check(batch["released"] == [] and len(batch["refusals"]) == 1,
          "带残余字符的表**不得**放行（'没人管'的字符不得被写成已消费）")
    rec = batch["refusals"][0]
    check(rec["reason"] == TLP.DEFECT_SCOPE_RESIDUAL
          and TLP.DEFECT_SCOPE_RESIDUAL in rec["local_proof"]["defects"],
          f"理由必须是范围残余，得到 {rec['reason']!r}")
    proof = rec["local_proof"]
    check(_intervals(proof["residual_intervals"]) == [[1, 7, 0, 0, 5]]
          and proof["residual_char_count"] == 5,
          f"残余必须带**精确全量**区间与字符数，得到 "
          f"{proof['residual_intervals']} / {proof['residual_char_count']}")
    check(proof["residual_examples"]
          and proof["residual_examples"][0]["text"] == "未主张正文",
          "残余样例必须带逐字原文，否则'缺的是哪一段'不可回查")
    check(rec["local_proof"]["steps"]["self_proven"] is False,
          "残余属第③步，该步必须判否")

    # ② 非语义空白**单列**：它既不是残余也不是已延期，不得被算进任一项，
    #    也不得因此拒发（排版片段不承载语义）。
    blank_key = (1, 8, 0)
    blank = _table(grid=_GRID_OK)
    batch = GTR.release_graph_tables(_source(
        (blank,), extra_atoms=((blank_key, 0, 3, "   ", "none"),)))
    check(len(batch["released"]) == 1,
          "整段恰为空白的片段不得让表拒发（它不是'没人管的正文'）")
    proof = batch["released"][0]["local_proof"]
    check(proof["whitespace_char_count"] == 3
          and proof["residual_char_count"] == 0
          and not proof["residual_intervals"],
          f"非语义空白必须单列且不进残余，得到 "
          f"{proof['whitespace_char_count']} / {proof['residual_char_count']}")

    # ③ 跨表重叠：两张表都声称拥有同一段字符 ⇒ 谁都不许按优先级吞掉它。
    first = _table(grid=_GRID_OK)
    second = _table(grid=_GRID_OK, page=1, source_order_index=1)
    check(first.table_id != second.table_id,
          "反例夹具要求两张不同身份的表")
    shared_key = (1, 1, 1)
    batch = GTR.release_graph_tables(_source(
        (first, second), foreign_owners={shared_key: "other_table"}))
    check(batch["released"] == [] and len(batch["refusals"]) == 2,
          "两张表都主张同一段字符时，两张都不得放行（不许用重叠优先级吞字符）")
    check(all(TLP.DEFECT_SCOPE_CROSS_TABLE_OVERLAP in r["local_proof"]["defects"]
              for r in batch["refusals"]),
          "跨表重叠必须记在**两张**表的逐表证明上，"
          f"得到 {[r['local_proof']['defects'] for r in batch['refusals']]}")

    # ④ 本表矩形里落着别人的正文段（layout hit）⇒ 本表范围不唯一 ⇒ 拒发。
    foreign_key = (1, 9, 0)
    foreign = _table(grid=_GRID_OK)
    batch = GTR.release_graph_tables(_source(
        (foreign,), extra_atoms=((foreign_key, 0, 6, "别处的正文", "paragraph"),)))
    rec = batch["refusals"][0]
    check(rec["reason"] == TLP.DEFECT_SCOPE_FOREIGN_CLAIM_IN_REGION,
          f"本表矩形里的他人正文必须记 scope_foreign_claim_in_region，"
          f"得到 {rec['reason']!r}")

    # ⑤ **已延期**（诚实未决）：残余原子被**本表**一条非阻断 typed 缺口精确覆盖
    #    ⇒ 记作已延期、逐条留区间，**不**使表拒发；这不是"缺口在册就放行"，而是
    #    "这一段字符的去向已被正式记账"。
    deferred_key = (1, 6, 0)
    deferred = _table(grid=_GRID_OK)
    gaps = _Gaps((_gap_with_interval(table_id=deferred.table_id,
                                     key=deferred_key, char_range=(0, 7)),))
    batch = GTR.release_graph_tables(_source(
        (deferred,), gaps=gaps,
        extra_atoms=((deferred_key, 0, 7, "待补的格子", "none"),)))
    check(len(batch["released"]) == 1,
          "被本表 typed 缺口**精确覆盖**的字符不得让表拒发（这是已正式延期），"
          f"得到 {[r['reason'] for r in batch['refusals']]}")
    proof = batch["released"][0]["local_proof"]
    check(proof["deferred_char_count"] == 7
          and proof["residual_char_count"] == 0
          and _intervals(proof["deferred_intervals"]) == [[1, 6, 0, 0, 7]]
          and not proof["defects"],
          f"已延期必须逐条留精确区间且不入残余，得到 "
          f"{proof['deferred_intervals']} / {proof['residual_char_count']}")

    # ⑥ 阻断性缺口**不**算正当延期：它表示"尚未解释的上游冲突"，与文档级守恒同判据。
    blocking_key = (1, 5, 0)
    blocking = _table(grid=_GRID_OK)
    blocking_gaps = _Gaps((_gap_with_interval(
        table_id=blocking.table_id, key=blocking_key, char_range=(0, 7),
        kind="upstream_table_scope_miss"),))
    batch = GTR.release_graph_tables(_source(
        (blocking,), gaps=blocking_gaps,
        extra_atoms=((blocking_key, 0, 7, "未解释的冲突", "none"),)))
    rec = batch["refusals"][0]
    check(rec["reason"] == TLP.DEFECT_SCOPE_RESIDUAL,
          "阻断性缺口覆盖的字符仍算**残余**（不得当正当延期），"
          f"得到 {rec['reason']!r}")

    # ⑦ 缺口必须**精确**覆盖：同片段但区间不够宽 ⇒ 依然算残余（不得靠'沾边'延期）。
    narrow_key = (1, 4, 0)
    narrow = _table(grid=_GRID_OK)
    narrow_gaps = _Gaps((_gap_with_interval(table_id=narrow.table_id,
                                            key=narrow_key,
                                            char_range=(0, 3)),))
    batch = GTR.release_graph_tables(_source(
        (narrow,), gaps=narrow_gaps,
        extra_atoms=((narrow_key, 0, 9, "更宽的未闭合段", "none"),)))
    rec = batch["refusals"][0]
    check(rec["reason"] == TLP.DEFECT_SCOPE_RESIDUAL,
          "缺口区间窄于原子 ⇒ 原子仍算残余（'沾边'不是延期）")

    # ⑧ 分区**问题码**逐字来自同一次扫描：本层不自己再比较一遍区间。
    scan_key = (1, 3, 0)
    scan = _table(grid=_GRID_OK)
    snap = _snapshot(scan)
    readings = list(_fixture_readings(snap.tables))
    assert len(readings) == 1
    readings[0] = dataclasses.replace(
        readings[0], scan_problems=(f"layout_text_duplicate_claim:{scan_key}",))
    batch = GTR.release_graph_tables(
        _FixtureSource(snapshot=snap, readings=tuple(readings)))
    rec = batch["refusals"][0]
    check(TLP.DEFECT_SCOPE_CLAIM_DUPLICATE in rec["local_proof"]["defects"],
          "扫描问题码必须逐字翻译成缺陷（判据只有一处：本层不得另写区间比较），"
          f"得到 {rec['local_proof']['defects']}")
    check(list(rec["local_proof"]["scan_problems"])
          == [f"layout_text_duplicate_claim:{scan_key}"],
          "扫描问题码必须逐字进记录（读回能看到分区层到底报了什么）")

    # ⑨ 证明身份绑定范围读数：残余区间换了，proof_id 必须跟着换（不得同证两读）。
    same = _table(grid=_GRID_OK)
    proof_a = TLP.prove_table_locally(same, host=_snapshot(same),
                                      reading=_fixture_readings(
                                          (same,),
                                          extra_atoms=(((1, 2, 0), 0, 5,
                                                        "甲段残余", "none"),))[0])
    proof_b = TLP.prove_table_locally(same, host=_snapshot(same),
                                      reading=_fixture_readings(
                                          (same,),
                                          extra_atoms=(((1, 2, 0), 0, 4,
                                                        "甲段残余", "none"),))[0])
    check(proof_a.proof_id != proof_b.proof_id,
          "同一张表、不同残余区间必须得到不同 proof_id"
          f"（否则'证明通过'与'残余在哪'可以各自漂移）")
    check(TLP.validate_proof_id(proof_a.to_dict()) == proof_a.proof_id,
          "证明身份必须可由外部按记录复算")
    check(TLP.record_is_locally_proven(proof_a.to_dict()) is False,
          "带残余的证明记录不得被外部读成'已通过'")

    # ⑩ 记录里不得出现任何金额／数字权威字段（阅读资格 ≠ 数字权威）。
    fields = _field_names(batch["refusals"][0])
    check(not {"amount", "value_text", "value_text_raw", "numeric_authority_value"}
          & fields,
          f"逐表证明记录不得携带金额或数字值字段，得到 {sorted(fields)}")
    last_proof = batch["refusals"][0]["local_proof"]
    check(last_proof["numeric_authority"] is False
          and last_proof["numeric_authority_reason"]
          == TLP.NUMERIC_AUTHORITY_REASON,
          "证明记录必须**逐字**声明'阅读资格不等于数字权威'及其理由")


_GROUPS = (
    ("G1-versions", _g1_versions),
    ("G2-entry-gate", _g2_entry_gate),
    ("G3-no-table-objects", _g3_no_table_objects),
    ("G3b-refused-doc-proven-table",
     _g3b_document_refused_table_independently_proven),
    ("G4-released", _g4_released),
    ("G5-structure-states", _g5_structure_states),
    ("G6-cell-sources", _g6_cell_sources),
    ("G7-host-binding", _g7_host_binding),
    ("G8-consumer-checks", _g8_consumer_checks),
    ("G9-unreachable-states", _g9_unreachable_states),
    ("G10-scope-conservation", _g10_scope_conservation),
)


def _run_group(name, fn, ctx) -> None:
    started = time.time()
    try:
        fn(ctx)
    except Exception as e:  # noqa: BLE001
        import traceback
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {name} 分组异常：{type(e).__name__}: {e}\n"
            f"{traceback.format_exc()[-1200:]}")
    _results["details"].append(f"__{name}__ {time.time() - started:.1f}s")


def main() -> dict:
    started = time.time()
    for name, fn in _GROUPS:
        _run_group(name, fn, None)
    _results["seconds"] = round(time.time() - started, 1)
    return _results


if __name__ == "__main__":
    _res = main()
    print(json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

"""M930-3 §0.19：**演示目标表的逐表完整证明**（`tlp-1`）。

`DESIGN_V2.md` §0.19 把"整份文档被拒 ⇒ 所有目标表一律归零"这一**资格粒度**换掉：全文档
守恒问题仍是正式 TS5 的未解决缺陷（原始拒发结果、问题清单与信任根一律保留），但它**不再**
因无关页面的缺陷一票否决一张已被**独立、完整证明**的目标表。本模块就是"逐表证明"这件事的
唯一实现。

三条边界，先写清楚，免得被读成"放水"
--------------------------------------------------

1. **不是豁免**。`partial`、未构造、诊断平铺文本、以及**表自身账不平**的表一律不通过。证明
   的条件是**加**上去的，不是减下来的：文档级放行看的是"快照整体自洽"，逐表证明看的是
   "**这张表自己**在它的物理范围内把字符、行列、格值来源与身份都交代清楚"。前者失效不等于
   后者成立。
2. **阅读资格 ≠ 数字权威**。本模块只判"这一栏读得到这张表"；表内数字要成为可引用事实仍须
   另走路径 A（预验证权威）或路径 B（exact material + 蕴含决定）。证明记录里逐字写着
   ``numeric_authority=False`` / ``numeric_authority_granted=False``，且**不出现**任何金额
   字段：拿逐表证明去当数字授权是**另一条**判据的事，本模块不给。
3. **判据只有一处**。范围原子的分区判据不在这里：它来自
   `final_material_builder.build_table_scope_readings`（守恒层消费的**同一份**原子分区，
   含"什么算非语义空白"的唯一实现）。本模块只做**政策**：哪些读数构成"证明通过"、哪些缺陷
   对应哪个 typed 码。任何一处让本模块自己重切字符区间，都会让"哪些字符算已被主张"在守恒层
   与逐表证明之间漂移。

七步验收（§0.19）与拒发原因
--------------------------------------------------

`DESIGN_V2.md` §0.19 要求逐张列出「未构造／已构造但不完整／表自身证明通过／已进 Pack／
Writer 已收到／读者面已呈现／格级数字已授权」七步及拒绝原因。本模块**只**拥有前两步的真实
判据（①由调用方按"这张表有没有构造出来"给出，②③在这里判）与第⑦步的**否定**读数；④⑤⑥属于
放行／Pack／Writer／读者面的读回（`harness/graph_table_material*` 与业务读回脚本）。因此
:meth:`TableLocalProof.steps` 如实给出七步里**本层能判的**那几步，并把不能判的写成
`"out_of_layer"`——不越界代答，也不拿别的步骤冒充。

纯转换模块：不 I/O、不建库、不写库、不调 LLM / 网络；不发起任何检索；不持有任何 capability。
"""

from __future__ import annotations

import dataclasses
from typing import Mapping, Sequence

from document_structure import final_material_builder as FMB
from document_structure import table_schema as TS
from document_structure import versions as V
from document_structure.canonical import identity

#: 逐表证明规则版本（唯一字面量来源是 `versions.py`）。
TABLE_LOCAL_PROOF_RULE_VERSION = V.TABLE_LOCAL_PROOF_RULE_VERSION

#: 逐表证明记录的 schema 版本（与规则版本同步；读回侧按它选字段集）。
TABLE_LOCAL_PROOF_SCHEMA_VERSION = "tlp-1"

#: 被取代的逐表证明版本（**只显式识别，绝不静默按新轴解释**）。
LEGACY_TABLE_LOCAL_PROOF_RULE_VERSIONS: tuple[str, ...] = ()


def classify_table_local_proof_rule_version(value: object) -> str:
    """把读到的规则版本分类为 `current` / `legacy` / `unknown`（后两者都 fail-closed）。"""
    if value == TABLE_LOCAL_PROOF_RULE_VERSION:
        return "current"
    if value in LEGACY_TABLE_LOCAL_PROOF_RULE_VERSIONS:
        return "legacy"
    return "unknown"


# ---------------------------------------------------------------------------
# 1. 缺陷码（封闭；顺序即优先级）
# ---------------------------------------------------------------------------

#: ②「已构造但不完整」：结构状态不是 `complete`，或表内存在**格值来源未闭合**的 cell。
DEFECT_STRUCTURE_NOT_COMPLETE = "structure_not_complete"
DEFECT_STRUCTURE_CONTRADICTS_CELL_GAPS = "structure_state_contradicts_cell_gaps"
DEFECT_CELL_PROVENANCE_INCOMPLETE = "cell_provenance_incomplete"
DEFECT_CELL_SOURCE_NOT_CITABLE = "cell_source_not_citable"

#: ③「表自身证明」：身份与宿主绑定。
DEFECT_HOST_BINDING_MISMATCH = "host_binding_mismatch"
DEFECT_NOT_MEMBER_OF_HOST = "not_member_of_host"

#: ③ 表题来源 / 主体绑定（§0.19「表题与所属主体、业务口径及适用期间」）。
DEFECT_TITLE_NOT_SOURCED = "title_not_sourced"
DEFECT_SUBJECT_NOT_BOUND = "subject_not_bound"

#: ③ 表头 / 行列标签（§0.19「完整物理表头、行列标签、单位、合并单元格」）。
DEFECT_HEADER_ROW_ABSENT = "header_row_absent"
DEFECT_ROW_LABEL_COLUMN_ABSENT = "row_label_column_absent"
DEFECT_COLUMN_WITHOUT_BODY_SOURCE = "column_without_body_source"
DEFECT_MERGED_CELL_NOT_CITABLE = "merged_cell_not_citable"

#: ③ 续表对应关系（§0.19「续表对应关系」）。
DEFECT_CONTINUATION_ANCHOR_UNRESOLVED = "continuation_anchor_unresolved"
DEFECT_CONTINUATION_RELATION_ABSENT = "continuation_relation_absent"
DEFECT_CONTINUATION_SHAPE_MISMATCH = "continuation_shape_mismatch"

#: ③ 本表范围内字符/区域的守恒与唯一归属（§0.19 的核心一项）。
DEFECT_SCOPE_REGION_EMPTY = "scope_region_empty"
DEFECT_SCOPE_RESIDUAL = "scope_residual"
DEFECT_SCOPE_GAP_OVERLAP = "scope_gap_overlap"
DEFECT_SCOPE_FOREIGN_CLAIM_IN_REGION = "scope_foreign_claim_in_region"
DEFECT_SCOPE_CROSS_TABLE_OVERLAP = "scope_cross_table_overlap"

#: ③ 同一份扫描自己报出来的分区问题（**不在这里重新推导**）。问题码是
#: `final_material_builder._layout_text_scan` 的原生输出码，逐字对应一个缺陷：本层只把
#: "同一片段的扫描问题"翻译成 typed 缺陷，绝不另写一套区间比较逻辑（判据只有一处）。
DEFECT_SCOPE_CLAIM_OUT_OF_SPAN = "scope_claim_out_of_span"
DEFECT_SCOPE_CLAIM_DUPLICATE = "scope_claim_duplicate"
DEFECT_SCOPE_CLAIM_OVERLAP = "scope_claim_overlap"
DEFECT_SCOPE_HIT_OUT_OF_SPAN = "scope_hit_out_of_span"
SCAN_PROBLEM_DEFECTS: dict = {
    "layout_text_ref_out_of_span": DEFECT_SCOPE_CLAIM_OUT_OF_SPAN,
    "layout_text_duplicate_claim": DEFECT_SCOPE_CLAIM_DUPLICATE,
    "layout_text_overlap": DEFECT_SCOPE_CLAIM_OVERLAP,
    "layout_text_hit_out_of_span": DEFECT_SCOPE_HIT_OUT_OF_SPAN,
}

#: 全部缺陷码（闭集；顺序即优先级）。
TABLE_LOCAL_PROOF_DEFECTS: tuple[str, ...] = (
    DEFECT_STRUCTURE_NOT_COMPLETE,
    DEFECT_STRUCTURE_CONTRADICTS_CELL_GAPS,
    DEFECT_CELL_PROVENANCE_INCOMPLETE,
    DEFECT_CELL_SOURCE_NOT_CITABLE,
    DEFECT_HOST_BINDING_MISMATCH,
    DEFECT_NOT_MEMBER_OF_HOST,
    DEFECT_TITLE_NOT_SOURCED,
    DEFECT_SUBJECT_NOT_BOUND,
    DEFECT_HEADER_ROW_ABSENT,
    DEFECT_ROW_LABEL_COLUMN_ABSENT,
    DEFECT_COLUMN_WITHOUT_BODY_SOURCE,
    DEFECT_MERGED_CELL_NOT_CITABLE,
    DEFECT_CONTINUATION_ANCHOR_UNRESOLVED,
    DEFECT_CONTINUATION_RELATION_ABSENT,
    DEFECT_CONTINUATION_SHAPE_MISMATCH,
    DEFECT_SCOPE_REGION_EMPTY,
    DEFECT_SCOPE_RESIDUAL,
    DEFECT_SCOPE_CLAIM_OUT_OF_SPAN,
    DEFECT_SCOPE_CLAIM_DUPLICATE,
    DEFECT_SCOPE_CLAIM_OVERLAP,
    DEFECT_SCOPE_HIT_OUT_OF_SPAN,
    DEFECT_SCOPE_GAP_OVERLAP,
    DEFECT_SCOPE_FOREIGN_CLAIM_IN_REGION,
    DEFECT_SCOPE_CROSS_TABLE_OVERLAP,
)

#: 缺陷码 → 七步里的第几步（**唯一**真值表；不得在调用方另写一份）。
DEFECT_STEP: dict[str, str] = {
    DEFECT_STRUCTURE_NOT_COMPLETE: "constructed_complete",
    DEFECT_STRUCTURE_CONTRADICTS_CELL_GAPS: "constructed_complete",
    DEFECT_CELL_PROVENANCE_INCOMPLETE: "constructed_complete",
    DEFECT_CELL_SOURCE_NOT_CITABLE: "constructed_complete",
    DEFECT_HOST_BINDING_MISMATCH: "self_proven",
    DEFECT_NOT_MEMBER_OF_HOST: "self_proven",
    DEFECT_TITLE_NOT_SOURCED: "self_proven",
    DEFECT_SUBJECT_NOT_BOUND: "self_proven",
    DEFECT_HEADER_ROW_ABSENT: "self_proven",
    DEFECT_ROW_LABEL_COLUMN_ABSENT: "self_proven",
    DEFECT_COLUMN_WITHOUT_BODY_SOURCE: "self_proven",
    DEFECT_MERGED_CELL_NOT_CITABLE: "self_proven",
    DEFECT_CONTINUATION_ANCHOR_UNRESOLVED: "self_proven",
    DEFECT_CONTINUATION_RELATION_ABSENT: "self_proven",
    DEFECT_CONTINUATION_SHAPE_MISMATCH: "self_proven",
    DEFECT_SCOPE_REGION_EMPTY: "self_proven",
    DEFECT_SCOPE_RESIDUAL: "self_proven",
    DEFECT_SCOPE_CLAIM_OUT_OF_SPAN: "self_proven",
    DEFECT_SCOPE_CLAIM_DUPLICATE: "self_proven",
    DEFECT_SCOPE_CLAIM_OVERLAP: "self_proven",
    DEFECT_SCOPE_HIT_OUT_OF_SPAN: "self_proven",
    DEFECT_SCOPE_GAP_OVERLAP: "self_proven",
    DEFECT_SCOPE_FOREIGN_CLAIM_IN_REGION: "self_proven",
    DEFECT_SCOPE_CROSS_TABLE_OVERLAP: "self_proven",
}

#: 七步验收里的每一步在本层是"能判"还是"越界"（**唯一**真值表）。
STEP_LAYER_OWNERSHIP: dict[str, str] = {
    "constructed": "caller",              # ① 未构造 / 已构造（调用方按成员集给）
    "constructed_complete": "proof",      # ② 已构造但不完整
    "self_proven": "proof",               # ③ 表自身证明通过
    "in_pack": "release_layer",           # ④ 已进 Pack
    "writer_received": "writer_layer",    # ⑤ Writer 已收到
    "reader_presented": "reader_layer",   # ⑥ 读者面已呈现
    "numeric_authority_granted": "numeric_layer",  # ⑦ 格级数字已授权
}

#: ⑧ 之外的两条**否定**读数（逐字进记录，读的人删不掉）。
NUMERIC_AUTHORITY_REASON = "table_reading_qualification_is_not_numeric_authority"

#: 逐表证明里**允许的用途**（封闭、只有一条；与图侧放行模块同一措辞）。
TABLE_LOCAL_PROOF_PERMITTED_USE = "navigable_reading_material_only"

#: 记录里逐条读数样例的条数上限（审计面有界，且必须**显式**说明被截断）。
_MAX_EXAMPLES = 5


class TableLocalProofError(TS.SchemaValidationError):
    """逐表证明的 fail-closed 用法错误（输入不是本层能判的东西 / 读数与表对不上）。"""


# ---------------------------------------------------------------------------
# 2. 证明记录
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class TableLocalProof:
    """**一张表**的逐表完整证明记录（`tlp-1`；只读、可外部复算身份）。

    `locally_proven` 是唯一结论位：全部缺陷为 0 时为真。缺陷**逐条**保留（不静默丢弃，
    也不截断——样例有界但计数全量）。
    """

    proof_rule_version: str
    proof_schema_version: str
    table_id: str
    table_locator: str
    document_id: str
    document_version: str
    evidence_set_version: str
    page_layout_id: str
    outline_id: str
    verified_span_snapshot_id: str
    upstream_dependency_fingerprint: str
    #: 宿主快照身份（放行侧据此确认"这份证明是在哪份快照上算的"）。
    host_snapshot_id: str | None
    #: 宿主**文档级**的资格终态读数（`qualified` / typed 拒绝种类 / `None`）。
    #: 逐表证明**不看**它做判据，但它必须如实带着：一条"文档被拒、某表仍被独立证明"
    #: 的记录如果隐去文档级拒发，读回就会把它读成"整份文档都过了"。
    host_document_state: str | None
    host_document_refusal_kind: str | None
    structure_state: str
    structure_kind: str
    structure_class: str
    page_number: int
    column_count: int
    row_count: int
    body_row_count: int
    header_row_count: int
    merged_cell_count: int
    citable_cell_count: int
    cell_count: int
    #: 本表物理矩形内的片段数、原子字符数、以及**已归属/残余/已延期**三类字符数。
    region_fragment_count: int
    region_char_count: int
    owned_char_count: int
    deferred_char_count: int
    whitespace_char_count: int
    residual_char_count: int
    #: 范围读数的**两个原始入参**（本表矩形内的片段键、本表主张的格级区间）。
    #: 它们进证明身份，因此必须逐条进记录：否则外部**无法**复算 `proof_id`
    #: （"身份可外部复算"就会退化成"采信记录自报"）。
    region_fragment_keys: tuple
    claims: tuple
    #: 逐条读数。区间**全量**（它们进证明身份，截断会让身份对不上），样例**有界**
    #: （只供人读；"被截断"必须显式写在记录里）。
    residual_intervals: tuple
    deferred_intervals: tuple
    residual_examples: tuple
    deferred_examples: tuple
    examples_truncated: bool
    #: 同一份扫描自报的分区问题码（逐字；它们是四个 `scope_*` 缺陷的来源）。
    scan_problems: tuple
    defects: tuple
    typecodes_by_step: Mapping
    title_text: str
    #: 表题读数（是否在场 / 逐字文本 / 块数与有来源块数）。**读数**，不是结论位。
    title_reading: Mapping
    unit_text: str
    continuation_anchor_locator: str | None
    content_fingerprint: str
    provenance_fingerprint: str
    numeric_authority: bool
    numeric_authority_reason: str
    permitted_use: str
    proof_id: str

    # -- 只读结论 ---------------------------------------------------------

    @property
    def locally_proven(self) -> bool:
        """表自身证明是否通过（缺陷为零）。它**不**等于已进 Pack，也不等于可发布。"""
        return not self.defects

    def steps(self) -> dict:
        """七步验收里**本层能判**的那几步 + 一步否定读数。

        非本层的步如实写 ``"out_of_layer"``：既不代答，也不把别的步冒充成它。
        """
        complete_defects = [d for d in self.defects
                            if DEFECT_STEP.get(d) == "constructed_complete"]
        self_defects = [d for d in self.defects
                        if DEFECT_STEP.get(d) == "self_proven"]
        return {
            "constructed": "caller",
            "constructed_complete": not complete_defects,
            "self_proven": not self_defects,
            "in_pack": "out_of_layer",
            "writer_received": "out_of_layer",
            "reader_presented": "out_of_layer",
            "numeric_authority_granted": False,
        }

    def defects_for_step(self, step: str) -> tuple:
        return tuple(d for d in self.defects if DEFECT_STEP.get(d) == step)

    def to_dict(self) -> dict:
        """记录载荷（**JSON 原生类型**：元组一律落成列表）。

        记录是 wire，不是内部对象：`json.dumps` 会把元组写成列表，读回就不再相等——
        那样"确定性 JSON 可无损读回"在带元组的记录上必然为假，而它恰恰是审计与
        外部复算的前提。因此这里一次性把元组转成列表，读回与写出逐字一致。
        """
        payload = dataclasses.asdict(self)
        for name, value in payload.items():
            if isinstance(value, tuple):
                payload[name] = [list(x) if isinstance(x, tuple) else x
                                 for x in value]
        payload["typecodes_by_step"] = {
            step: list(codes)
            for step, codes in self.typecodes_by_step.items()}
        payload["schema_type"] = "TableLocalProof"
        payload["locally_proven"] = self.locally_proven
        payload["steps"] = self.steps()
        return payload


def _proof_payload(*, table, region_fragment_keys: Sequence[Sequence[int]],
                   claims: Sequence[Sequence], defects: Sequence[str],
                   residual_intervals: Sequence[Sequence],
                   deferred_intervals: Sequence[Sequence]) -> dict:
    """证明身份的 canonical payload（**外部可复算**，见 :func:`validate_proof_id`）。"""
    return {
        "proof_rule_version": TABLE_LOCAL_PROOF_RULE_VERSION,
        "proof_schema_version": TABLE_LOCAL_PROOF_SCHEMA_VERSION,
        "table_id": table.table_id,
        "table_locator": table.table_locator,
        "document_id": table.document_id,
        "document_version": table.document_version,
        "evidence_set_version": table.evidence_set_version,
        "page_layout_id": table.page_layout_id,
        "outline_id": table.outline_id,
        "verified_span_snapshot_id": table.verified_span_snapshot_id,
        "upstream_dependency_fingerprint": table.upstream_dependency_fingerprint,
        "content_fingerprint": table.content_fingerprint,
        "provenance_fingerprint": table.provenance_fingerprint,
        "structure_state": table.structure_state,
        "region_fragment_keys": [list(k) for k in region_fragment_keys],
        "claims": [list(c) for c in claims],
        "residual_intervals": [list(x) for x in residual_intervals],
        "deferred_intervals": [list(x) for x in deferred_intervals],
        "defects": list(defects),
    }


def table_local_proof_id(**kwargs) -> str:
    """逐表证明身份 `tlpp-...`（内容寻址；与 `table_id` / `release_id` 都不同）。"""
    return identity("tlpp", _proof_payload(**kwargs))


# ---------------------------------------------------------------------------
# 3. 逐项判据（全部来自 `TableScopeReading` 与对象自身；不重切字符）
# ---------------------------------------------------------------------------


#: 逐格来源状态（封闭；与 `graph_table_release.CELL_STATES` **同名同义**）。
#: `citable` = 至少一条可引用片段；`unproven_only` = 无可用片段但逐条在册；
#: `not_citable` = 有片段却一条都不可引用；`no_source` = 什么都没有。
CELL_STATE_CITABLE = "citable"
CELL_STATE_UNPROVEN_ONLY = "unproven_only"
CELL_STATE_NOT_CITABLE = "not_citable"
CELL_STATE_NO_SOURCE = "no_source"
CELL_STATES: tuple[str, ...] = (CELL_STATE_CITABLE, CELL_STATE_UNPROVEN_ONLY,
                                CELL_STATE_NOT_CITABLE, CELL_STATE_NO_SOURCE)


def cell_state(cell) -> str:
    """一个 cell 的逐格来源状态（**唯一**实现；放行记录与本模块都读它）。"""
    if not cell.source_refs and not cell.unproven_fragments:
        return CELL_STATE_NO_SOURCE
    if cell.citable_ref_count > 0:
        return CELL_STATE_CITABLE
    if cell.unproven_fragments:
        return CELL_STATE_UNPROVEN_ONLY
    return CELL_STATE_NOT_CITABLE


def column_support_readings(table) -> list:
    """逐列支撑读数（按**网格覆盖**计；跨行/跨列 cell 在其覆盖的每一格都算）。

    两个口径分开写，因为它们回答不同问题：`covering_*` 是这一列在结构上被多少 cell
    覆盖；`body_*` 只数表体侧（`role != "header"`）——一列**只**由表头 cell 撑起来
    不算实质支撑。`supported` 取 `body_citable_covering_cells > 0`，即"这一列有**实质**
    来源"的唯一读数。

    **唯一实现**：`harness/graph_table_release` 的发布记录复用本函数，不再各写一份。
    """
    cover: dict = {c: {"covering_cells": 0, "citable_covering_cells": 0,
                       "body_covering_cells": 0,
                       "body_citable_covering_cells": 0}
                   for c in range(table.column_count)}
    for row in table.rows:
        is_body = row.role != "header"
        for cell in row.cells:
            citable = cell.citable_ref_count > 0
            for dc in range(cell.colspan):
                c = cell.column + dc
                if c not in cover:
                    continue
                slot = cover[c]
                slot["covering_cells"] += 1
                if citable:
                    slot["citable_covering_cells"] += 1
                if is_body:
                    slot["body_covering_cells"] += 1
                    if citable:
                        slot["body_citable_covering_cells"] += 1
    out: list = []
    for c in range(table.column_count):
        slot = {"column": c, **cover[c]}
        slot["supported"] = slot["body_citable_covering_cells"] > 0
        out.append(slot)
    return out


def prioritise_defects(defects: Sequence[str]) -> tuple:
    """按**封闭优先级**去重（`TABLE_LOCAL_PROOF_DEFECTS` 的顺序即优先级）。

    不得按字典序排：记录里的单值 `reason` 取首个，字典序会让
    `column_without_body_source` 盖过 `structure_state_contradicts_cell_gaps`——
    读的人会以为问题出在列支撑，而真正不可放行的是更上游的结构自相矛盾。
    未登记的缺陷码一律 fail-closed（不静默丢弃）。
    """
    seen = set(defects)
    unknown = sorted(seen - set(TABLE_LOCAL_PROOF_DEFECTS))
    if unknown:
        raise TableLocalProofError(
            f"未登记的缺陷码 {unknown}（fail-closed；不得混入缺陷词表）")
    return tuple(d for d in TABLE_LOCAL_PROOF_DEFECTS if d in seen)


def _host_binding_defects(table, host) -> list:
    """身份与宿主绑定逐项相等（跨文档 / 跨版本拼接一律拒绝）。"""
    defects: list = []
    for name in ("document_id", "document_version", "evidence_set_version",
                 "page_layout_id", "outline_id", "verified_span_snapshot_id",
                 "upstream_dependency_fingerprint"):
        if getattr(table, name) != getattr(host, name):
            defects.append(DEFECT_HOST_BINDING_MISMATCH)
            break
    if table.table_id not in {t.table_id for t in host.tables}:
        defects.append(DEFECT_NOT_MEMBER_OF_HOST)
    return defects


def _structure_defects(table) -> list:
    """②：结构状态与**逐格来源**（不是"对象说自己 complete"就算过）。

    逐格来源**只有** `citable` 一种状态算闭合。`unproven_only` **不**算：逐条在册的
    缺口是"这一段我没闭合"的记录，不是"已正式延期成完整表"——与放行层口径一致。
    """
    defects: list = []
    if table.structure_state != "complete":
        defects.append(DEFECT_STRUCTURE_NOT_COMPLETE)
    for row in table.rows:
        for cell in row.cells:
            state = cell_state(cell)
            if state in (CELL_STATE_CITABLE, CELL_STATE_UNPROVEN_ONLY):
                # `unproven_only` 在**结构完整**的表里不可能出现（放行层同判据），
                # 但这里不重复判它：那是 `structure_state_contradicts_cell_gaps`
                # 的分工——`complete` + 逐条缺口由 `_claims_citable` 一并拦下。
                continue
            if state == CELL_STATE_NOT_CITABLE:
                defects.append(DEFECT_CELL_SOURCE_NOT_CITABLE)
            else:
                defects.append(DEFECT_CELL_PROVENANCE_INCOMPLETE)
    # 声明"结构完整"却有格子明说"这一段我没闭合"：两者不可能同真。
    if table.structure_state == "complete" and any(
            cell_state(c) == CELL_STATE_UNPROVEN_ONLY
            for row in table.rows for c in row.cells):
        defects.append(DEFECT_STRUCTURE_CONTRADICTS_CELL_GAPS)
    return sorted(set(defects))


def _header_defects(table) -> list:
    """③：完整物理表头 + 行列标签。

    `headerless_grid` 一词在 schema 里**只能** `partial`，因此它到不了这里；本判据只处理
    "声明了 `headered_grid` / `key_value_form` 却拿不出表头行"这一种形态。
    """
    defects: list = []
    header_rows = [r for r in table.rows if r.role == "header"]
    if not header_rows:
        defects.append(DEFECT_HEADER_ROW_ABSENT)
    else:
        header_columns = {c.column for r in header_rows for c in r.cells}
        if not header_columns:
            defects.append(DEFECT_HEADER_ROW_ABSENT)
    # 行列标签：每一条**表体**行要么有自己的 label，要么它在首列有可引用 cell
    # （首列就是这一行的标签来源）。两者都拿不到 ⇒ 这一行的"行标签"无从证明。
    unlabelled = 0
    for row in table.rows:
        if row.role == "header":
            continue
        has_label = bool((row.label or "").strip())
        first_column = row.cell_at(0)
        if not has_label and (first_column is None
                              or first_column.citable_ref_count == 0):
            unlabelled += 1
    if unlabelled:
        defects.append(DEFECT_ROW_LABEL_COLUMN_ABSENT)
    # 逐列实质支撑：一列**只**由表头 cell 撑起来不算实质支撑（列值区无来源）。
    for column in column_support_readings(table):
        if not column["supported"]:
            defects.append(DEFECT_COLUMN_WITHOUT_BODY_SOURCE)
            break
    for row in table.rows:
        for cell in row.cells:
            if cell.rowspan * cell.colspan > 1 and cell.citable_ref_count == 0:
                defects.append(DEFECT_MERGED_CELL_NOT_CITABLE)
                break
    return list(prioritise_defects(defects))


def table_block_text(blocks: Sequence) -> str:
    """表级块的**逐字**文本（按序、以冻结分隔符拼接；不重排、不补全）。"""
    return TS.CELL_BLOCK_SEPARATOR.join(b.text for b in blocks)


def _title_reading(table) -> dict:
    """表题读数（**不是**判据结论）：有没有、逐字文本、来源片段数。

    逐表矩阵第 ③ 列要求"表题"这一项**可读**，而不是从"这张表有没有过证明"去推。因此
    把它作为读数放在记录里，与结论位（`defects`）分开。
    """
    text = table_block_text(table.title_blocks)
    return {
        "title_present": bool(text.strip()),
        "title_text": text,
        "block_count": len(table.title_blocks),
        "sourced_block_count": sum(1 for b in table.title_blocks
                                   if b.source_ref_ids),
    }


def _title_defects(table) -> list:
    """③：表题**来源**与所属主体绑定（§0.19 的第一项）。

    两条判据分开写，因为"表题不在"与"表题在但来路不明"是两件事：

    - **主体绑定（硬门）**：`owner` 必须是在册的 outline 节点或已验证 unassigned 边界
      （`owner_kind` 在封闭词表内），且它的边界页**覆盖**本表物理页。不按标题像不像去
      猜归属——归属由 schema 在构造期确定，这里只复核它在场且区间包含本表。
    - **表题来源（条件门）**：表题**不是放行前提** —— 很多真实业务表把题注排成相邻段落，
      对象里 `title_blocks` 就是空的，"没有 title block"因此**不**能当成"表不完整"。
      但**一旦**对象声明了表级块（题注／单位／附注），这些块就必须**有来源**：一个声称
      有题注、却指不出任何来源片段的块，是本表内部来路不明的一段文字
      （`title_not_sourced`）。表题文本与"在不在"另作**读数**逐字写进证明记录，供逐表矩阵
      第 ③ 列直接引用，而不是从整表 bool 去猜。
    """
    defects: list = []
    owner = table.owner
    if getattr(owner, "owner_kind", None) not in TS.OWNER_KINDS:
        defects.append(DEFECT_SUBJECT_NOT_BOUND)
    elif not (owner.source_boundary_start_page <= table.page_number
              <= owner.source_boundary_end_page):
        defects.append(DEFECT_SUBJECT_NOT_BOUND)
    for blocks in (table.title_blocks, table.unit_blocks, table.note_blocks):
        for block in blocks:
            if not block.source_ref_ids:
                defects.append(DEFECT_TITLE_NOT_SOURCED)
                break
    return defects


def _continuation_defects(table, host) -> list:
    """③：续表对应关系（有锚点就必须**证明**这段对应关系，而不是自报一个 locator）。

    锚点指向的表必须①在宿主成员内、②有一条 `continued_by` 关系把本表连到它、③列数与
    本表一致。三者缺一，这段"续读"就只是一句话，不是对应关系——**不**因为"跨页了"
    就自动放行，也不因为没有锚点就自动判缺（没有锚点 = 没有续读主张）。
    """
    anchor = table.continuation_anchor_locator
    if anchor is None:
        return []
    defects: list = [DEFECT_CONTINUATION_RELATION_ABSENT]
    by_locator = {t.table_locator: t for t in host.tables}
    target = by_locator.get(anchor)
    if target is None:
        return [DEFECT_CONTINUATION_ANCHOR_UNRESOLVED,
                DEFECT_CONTINUATION_RELATION_ABSENT]
    for relation in getattr(host, "relations", ()) or ():
        if getattr(relation, "relation_kind", None) != "continued_by":
            continue
        src = getattr(relation, "source_endpoint", None)
        dst = getattr(relation, "target_endpoint", None)
        if (getattr(src, "table_locator", None) == table.table_locator
                and getattr(dst, "table_locator", None) == anchor):
            defects = [d for d in defects
                       if d != DEFECT_CONTINUATION_RELATION_ABSENT]
            break
    if target.column_count != table.column_count:
        defects.append(DEFECT_CONTINUATION_SHAPE_MISMATCH)
    return defects


def _scope_defects(reading, *, table, gap_entries: Sequence) -> tuple:
    """③：本表范围内字符/区域的**守恒与唯一归属**。

    一个区域原子恰好归一类（**不取优先级**）：

    * 被**本表**主张（`owner_kind == "table"`）⇒ 已归属；
    * 被**另一张表**主张（`other_table`）⇒ 跨表重叠：两张表都声称拥有同一段字符，
      谁都不许按优先级吞掉它；
    * 落在已成立正文段落的 hit 里（`paragraph`）⇒ 本表矩形里有别人的正文；
    * 无人主张（`none`）⇒ 要么被**本表自己的**一条 typed 缺口（非阻断种类、且带精确
      `source_intervals`）覆盖（`已延期`，诚实未决）；要么就是**残余**：既没人管，也没
      正式延期 —— 这种字符**不得**被写成"已消费"。

    三类缺陷各自带出**精确区间与逐字文本**，使"缺的是哪一段原文"可回查。非语义空白按
    `table_schema` 的**唯一**实现单列（它既不是残余也不是已延期，而是不承载语义的排版片段）。

    分区本身的**问题码**（越界主张、重复主张、同区间被两类来源主张、layout hit 越界）由
    同一次扫描给出、逐字翻译成缺陷：本层**不**再自己比较一遍区间（判据只有一处）。
    """
    defects: list = [SCAN_PROBLEM_DEFECTS[prefix]
                     for prefix in sorted(SCAN_PROBLEM_DEFECTS)
                     if any(p.startswith(prefix + ":")
                            for p in reading.scan_problems)]
    if not reading.region_fragment_keys:
        # 矩形里一个片段都没有 ⇒ "本表范围守恒"无从证明（空集上的守恒不是证明）。
        defects.append(DEFECT_SCOPE_REGION_EMPTY)
    residual: list = []
    deferred: list = []
    foreign: list = []
    cross: list = []
    residuals: set = set()
    owned = 0
    whitespace = 0
    for atom in reading.atoms:
        if not atom.in_region:
            continue
        if atom.owner_kind == "table":
            owned += atom.length
            continue
        if atom.owner_kind == "other_table":
            cross.append(atom)
            continue
        if atom.owner_kind == "paragraph":
            foreign.append(atom)
            continue
        if atom.is_whitespace:
            whitespace += atom.length
            continue
        covers = _gap_covers(gap_entries, atom)
        if len(covers) > 1:
            defects.append(DEFECT_SCOPE_GAP_OVERLAP)
            residual.append(atom)
            continue
        if len(covers) == 1:
            deferred.append(atom)
            continue
        residual.append(atom)
        residuals.add(atom.fragment_key)
    if foreign:
        defects.append(DEFECT_SCOPE_FOREIGN_CLAIM_IN_REGION)
    if cross:
        defects.append(DEFECT_SCOPE_CROSS_TABLE_OVERLAP)
    if residual:
        defects.append(DEFECT_SCOPE_RESIDUAL)
    readings = {
        "owned_char_count": owned,
        "deferred_char_count": sum(a.length for a in deferred),
        "whitespace_char_count": whitespace,
        "residual_char_count": sum(a.length for a in residual),
        "residual_fragments": tuple(sorted(residuals)),
        "residual": tuple(residual),
        "deferred": tuple(deferred),
        "foreign": tuple(foreign),
        "cross": tuple(cross),
    }
    return list(prioritise_defects(defects)), readings


def _gap_covers(gap_entries: Sequence, atom) -> list:
    """覆盖本原子的**本表** typed 缺口条目（只看非阻断种类、且带精确 `source_intervals`）。

    阻断性种类（`upstream_table_scope_miss` / `root_identity_mismatch`）**不**算正当延期：
    它们表示"尚未解释的上游冲突"，与文档级守恒层同一判据（判据只有一处）。
    """
    out: list = []
    for index, entry in enumerate(gap_entries):
        if entry.gap_kind in TS.GAP_BLOCKING_KINDS:
            continue
        if not entry.source_intervals:
            continue
        for interval in entry.source_intervals:
            if (interval.page_number, interval.line_index, interval.span_index) \
                    != atom.fragment_key:
                continue
            lo, hi = interval.span_char_range
            if lo <= atom.start and atom.end <= hi:
                out.append(index)
                break
    return out


def _interval_of(atom) -> list:
    """原子的**全量**区间读数 `[页, 行, 片段, 起, 止]`（进证明身份，不截断）。"""
    return [int(atom.fragment_key[0]), int(atom.fragment_key[1]),
            int(atom.fragment_key[2]), int(atom.start), int(atom.end)]


def _examples(atoms: Sequence, *, limit: int = _MAX_EXAMPLES) -> tuple:
    """原子的**有界**人读样例（区间本身另有全量字段；这里只为可回查原文）。"""
    rows: list = []
    for atom in atoms:
        if len(rows) >= limit:
            break
        rows.append({
            "fragment_key": list(atom.fragment_key),
            "span_char_range": [atom.start, atom.end],
            "kind": atom.kind,
            "owner_kind": atom.owner_kind,
            "text": atom.text,
        })
    return tuple(rows)


# ---------------------------------------------------------------------------
# 4. 主入口
# ---------------------------------------------------------------------------


def prove_table_locally(table, *, host, reading,
                        gap_entries: Sequence = (),
                        host_document_state: str | None = None,
                        host_document_refusal_kind: str | None = None
                        ) -> TableLocalProof:
    """**一张已构造的**图侧表对象 → 逐表完整证明记录（唯一入口）。

    `host` 是它所属的 `fms-1` 快照（**不是**放行批次）；`reading` 是同一份图侧来源上的
    范围读数（`final_material_builder.build_table_scope_readings`）；`gap_entries` 是
    **本表**的 typed 缺口条目（调用方按 `table_id` 过滤后给出——本层不替它去猜归属）。

    宿主文档级的资格终态由 `host_document_state` / `host_document_refusal_kind` 逐字带入
    记录：逐表证明**不读**它们，但一条"文档被拒、某表仍被独立证明"的记录必须带着这两项，
    否则读回会把它读成"整份文档都过了"。
    """
    if not isinstance(reading, FMB.TableScopeReading):
        raise TableLocalProofError(
            "reading 必须是 final_material_builder.TableScopeReading（同一份图侧来源上的"
            f"范围读数），得到 {type(reading).__name__}")
    if reading.table_id != table.table_id:
        raise TableLocalProofError(
            f"范围读数属于表 {reading.table_id!r}，却拿来证明表 {table.table_id!r}"
            "（fail-closed；不得跨表借用读数）")
    for entry in gap_entries:
        if entry.table_id not in (None, table.table_id):
            raise TableLocalProofError(
                f"缺口条目 {entry.detail_code!r} 属于表 {entry.table_id!r}，"
                f"不属于 {table.table_id!r}（fail-closed；不得借用别表的延期依据）")

    defects: list = []
    defects.extend(_host_binding_defects(table, host))
    defects.extend(_structure_defects(table))
    defects.extend(_title_defects(table))
    defects.extend(_header_defects(table))
    defects.extend(_continuation_defects(table, host))
    scope_defects, scope = _scope_defects(reading, table=table,
                                          gap_entries=gap_entries)
    defects.extend(scope_defects)
    defects = list(prioritise_defects(defects))

    cells = [c for row in table.rows for c in row.cells]
    header_rows = [r for r in table.rows if r.role == "header"]
    body_rows = [r for r in table.rows if r.role != "header"]
    residual_intervals = tuple(_interval_of(a) for a in scope["residual"])
    deferred_intervals = tuple(_interval_of(a) for a in scope["deferred"])
    examples = tuple(scope["residual"]) + tuple(scope["deferred"])
    return TableLocalProof(
        proof_rule_version=TABLE_LOCAL_PROOF_RULE_VERSION,
        proof_schema_version=TABLE_LOCAL_PROOF_SCHEMA_VERSION,
        table_id=table.table_id, table_locator=table.table_locator,
        document_id=table.document_id, document_version=table.document_version,
        evidence_set_version=table.evidence_set_version,
        page_layout_id=table.page_layout_id, outline_id=table.outline_id,
        verified_span_snapshot_id=table.verified_span_snapshot_id,
        upstream_dependency_fingerprint=table.upstream_dependency_fingerprint,
        host_snapshot_id=getattr(host, "snapshot_id", None),
        host_document_state=host_document_state,
        host_document_refusal_kind=host_document_refusal_kind,
        structure_state=table.structure_state,
        structure_kind=table.structure_kind,
        structure_class=table.structure_class,
        page_number=table.page_number, column_count=table.column_count,
        row_count=len(table.rows), body_row_count=len(body_rows),
        header_row_count=len(header_rows),
        merged_cell_count=sum(1 for c in cells if c.rowspan * c.colspan > 1),
        citable_cell_count=sum(1 for c in cells if c.citable_ref_count > 0),
        cell_count=len(cells),
        region_fragment_count=len(reading.region_fragment_keys),
        region_char_count=sum(a.length for a in reading.atoms if a.in_region),
        owned_char_count=scope["owned_char_count"],
        deferred_char_count=scope["deferred_char_count"],
        whitespace_char_count=scope["whitespace_char_count"],
        residual_char_count=scope["residual_char_count"],
        region_fragment_keys=tuple(tuple(int(v) for v in k)
                                   for k in reading.region_fragment_keys),
        claims=tuple(tuple(c) for c in reading.claims),
        residual_intervals=residual_intervals,
        deferred_intervals=deferred_intervals,
        residual_examples=_examples(scope["residual"]),
        deferred_examples=_examples(scope["deferred"]),
        examples_truncated=len(examples) > _MAX_EXAMPLES,
        scan_problems=tuple(reading.scan_problems),
        defects=tuple(defects),
        typecodes_by_step={step: tuple(d for d in defects
                                       if DEFECT_STEP.get(d) == step)
                           for step in ("constructed_complete", "self_proven")},
        title_text=table_block_text(table.title_blocks),
        title_reading=_title_reading(table),
        unit_text=table.unit_text or "",
        continuation_anchor_locator=table.continuation_anchor_locator,
        content_fingerprint=table.content_fingerprint,
        provenance_fingerprint=table.provenance_fingerprint,
        numeric_authority=False,
        numeric_authority_reason=NUMERIC_AUTHORITY_REASON,
        permitted_use=TABLE_LOCAL_PROOF_PERMITTED_USE,
        # 身份只由**这五项**派生，与 `validate_proof_id` 的复算入参逐字同一组——
        # 两侧不可能各算一套。
        proof_id=table_local_proof_id(
            table=table,
            region_fragment_keys=reading.region_fragment_keys,
            claims=reading.claims, defects=defects,
            residual_intervals=residual_intervals,
            deferred_intervals=deferred_intervals))


def prove_tables_locally(*, host, readings: Sequence,
                         gap_entries: Sequence = (),
                         host_document_state: str | None = None,
                         host_document_refusal_kind: str | None = None
                         ) -> tuple[TableLocalProof, ...]:
    """**全部**宿主成员表的逐表证明（顺序跟宿主成员顺序，与读数/缺口的归属逐表核对）。

    成员与读数必须**精确等集**：少一张读数或多一张都一样 fail-closed——"某张没读数就跳过"
    会让它静默变成"证明不了但也没缺陷"，那是最危险的读法。
    """
    by_table = {r.table_id: r for r in readings}
    host_ids = [t.table_id for t in host.tables]
    if set(by_table) != set(host_ids) or len(by_table) != len(readings):
        missing = sorted(set(host_ids) - set(by_table))
        extra = sorted(set(by_table) - set(host_ids))
        raise TableLocalProofError(
            f"逐表证明的读数与宿主成员不等集（缺 {missing}；多 {extra}）——"
            "fail-closed；不得跳过任何一张表")
    gaps_by_table: dict = {}
    for entry in gap_entries:
        gaps_by_table.setdefault(entry.table_id, []).append(entry)
    out: list = []
    for table in host.tables:
        out.append(prove_table_locally(
            table, host=host, reading=by_table[table.table_id],
            gap_entries=tuple(gaps_by_table.get(table.table_id, ())),
            host_document_state=host_document_state,
            host_document_refusal_kind=host_document_refusal_kind))
    return tuple(out)


# ---------------------------------------------------------------------------
# 5. 消费侧复核
# ---------------------------------------------------------------------------


def validate_proof_id(record: Mapping) -> str:
    """**外部复算**证明身份：不采信记录自报的 `proof_id`。"""
    if not isinstance(record, Mapping):
        raise TableLocalProofError(
            f"证明记录必须为映射，得到 {type(record).__name__}")
    for name in ("proof_rule_version", "proof_schema_version", "table_id",
                 "table_locator", "region_fragment_keys", "claims",
                 "residual_intervals", "deferred_intervals", "defects",
                 "proof_id"):
        if name not in record:
            raise TableLocalProofError(f"证明记录缺字段 {name!r}")
    if classify_table_local_proof_rule_version(
            record["proof_rule_version"]) != "current":
        raise TableLocalProofError(
            f"证明规则版本不是 current：{record['proof_rule_version']!r}"
            "（fail-closed；旧轴记录不得按新轴解释）")
    if record["proof_schema_version"] != TABLE_LOCAL_PROOF_SCHEMA_VERSION:
        raise TableLocalProofError(
            f"证明载荷版本必须为 {TABLE_LOCAL_PROOF_SCHEMA_VERSION!r}，得到 "
            f"{record['proof_schema_version']!r}（fail-closed）")
    table = _RecordTable(record)
    expected = table_local_proof_id(
        table=table, region_fragment_keys=record["region_fragment_keys"],
        claims=record["claims"], defects=record["defects"],
        residual_intervals=record["residual_intervals"],
        deferred_intervals=record["deferred_intervals"])
    if expected != record["proof_id"]:
        raise TableLocalProofError(
            f"proof_id 与记录内容复算不一致：{record['proof_id']!r} != {expected!r}")
    return expected


@dataclasses.dataclass(frozen=True)
class _RecordTable:
    """从**记录**读出的证明身份所需字段（只作复算入参，不是资格对象）。"""

    table_id: str
    table_locator: str
    document_id: str
    document_version: str
    evidence_set_version: str
    page_layout_id: str
    outline_id: str
    verified_span_snapshot_id: str
    upstream_dependency_fingerprint: str
    content_fingerprint: str
    provenance_fingerprint: str
    structure_state: str

    def __init__(self, record: Mapping) -> None:
        for name in ("table_id", "table_locator", "structure_state"):
            object.__setattr__(self, name, str(record.get(name) or ""))
        for name in ("document_id", "document_version", "evidence_set_version",
                     "page_layout_id", "outline_id", "verified_span_snapshot_id",
                     "upstream_dependency_fingerprint", "content_fingerprint",
                     "provenance_fingerprint"):
            if name not in record:
                raise TableLocalProofError(f"证明记录缺字段 {name!r}")
            object.__setattr__(self, name, str(record[name]))


def record_is_locally_proven(record: Mapping) -> bool:
    """记录是否是一份**当前版本**且缺陷为零的逐表证明（版本门在最先）。"""
    if classify_table_local_proof_rule_version(
            record.get("proof_rule_version")) != "current":
        return False
    if record.get("proof_schema_version") != TABLE_LOCAL_PROOF_SCHEMA_VERSION:
        return False
    return not tuple(record.get("defects") or ())


def render_proof_line(record: Mapping) -> str:
    """一行可读结论（不替代记录本身）。"""
    state = "独立证明通过" if record_is_locally_proven(record) else \
        f"未通过（{len(record.get('defects') or ())} 项）"
    return (f"{record.get('table_id')} p{record.get('page_number')} "
            f"{record.get('structure_state')}：{state}")


__all__ = [
    "TABLE_LOCAL_PROOF_RULE_VERSION",
    "TABLE_LOCAL_PROOF_SCHEMA_VERSION",
    "LEGACY_TABLE_LOCAL_PROOF_RULE_VERSIONS",
    "classify_table_local_proof_rule_version",
    "TABLE_LOCAL_PROOF_DEFECTS",
    "DEFECT_STEP",
    "STEP_LAYER_OWNERSHIP",
    "SCAN_PROBLEM_DEFECTS",
    "prioritise_defects",
    "NUMERIC_AUTHORITY_REASON",
    "TABLE_LOCAL_PROOF_PERMITTED_USE",
    "CELL_STATES",
    "cell_state",
    "column_support_readings",
    "TableLocalProofError",
    "TableLocalProof",
    "prove_table_locally",
    "prove_tables_locally",
    "table_local_proof_id",
    "validate_proof_id",
    "record_is_locally_proven",
    "render_proof_line",
]

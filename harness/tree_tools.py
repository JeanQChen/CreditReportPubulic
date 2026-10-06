"""M930-2 §5.5：`inspect_outline_materials` —— 现有 ToolRegistry 的 tree inspection ToolSpec。

本模块**只向既有 `tools.registry.ToolRegistry` 注册一个 ToolSpec**，不新建 registry、不新建
执行入口、不绕过 `ToolRegistry.execute` 的参数校验 / 路由门控 / 超时熔断 / audit。

硬约束（§5.5）：

- 输入只接受 document/current identity、**二选一** selector（`node_ids` 正常树导航 /
  `evidence_ids` 有界 Evidence fallback）、aspect/need identity 与有界 limits；
  `additionalProperties=False`，因此"自报 ``verified=true``"根本不通过参数校验，
  "传任意文件路径或 raw snapshot"同样没有字段可传；
- 候选只能来自 **run-bound** 的 `document_structure.live_span_source.LiveVerifiedSpanSource`
  （在注册时注入，不在调用参数里）；
- 输出是 typed span candidates + 精确 locator + payload ref + gap + **内容处置** + 版本，
  由 `harness.tree_materials` 的独立重切解析器产出（不采信任何自报字段）；
- 低置信 / 结构不可用 / 显式跨引用先走既有 bounded Evidence 工具取得候选父 Evidence，
  再用 `evidence_ids` selector **重新定位并重切为 OutlineSpan**——本模块不提供"整块
  Evidence 升格"的捷径，重切不出来就登记显式 gap。
- 候选的三条互斥去路分开报（§二 2.3）：成为材料 / 结构 gap / 内容处置。孤立标题、
  纯版式碎片与读不出选中状态的勾选行走**内容处置**，带 typed reason、原文摘录与 locator，
  不写成 gap，也不静默丢弃。

`TreeInspectionSession` 同时是 runtime 的合取点：它暴露 :attr:`resolver`，让 runtime 用
同一份重切结果提交 payload（同一份字节，同一次重切）。
"""

from __future__ import annotations

import json
from typing import Sequence

from tools import contracts as C

from document_structure import live_table_source as LTS

from harness import graph_table_materials as GTM
from harness import graph_table_release as GTR
from harness import tree_materials as TM

#: §5.5 固定的公开 tool name。
TREE_INSPECT_TOOL_NAME = "inspect_outline_materials"

#: 与工具契约同层的版本。v2：新增 `evidence_ids` selector（wire 变更必须升版本）。
#: v3：候选粒度由 **span** 改为 **material**（一份材料 = 一个真实 Evidence 块）。跨块 span
#: 现在产出逐块片段材料，候选因此必须逐段列出，否则候选的 `text`（整段 span 正文）与它的
#: material_id / parent_evidence_id / 精确区间会指向两份不同的东西。`span_split` 随候选
#: 一起返回，"这一段是全文的第几段"可读回。
#: **v4**（跨文档联合检索 §L3）：分派语义变了——executor 由「一个闭包会话」改为「按调用参数
#: 里的四轴精确查表取会话」，并新增一个 typed 错误码 `SOURCE_NOT_BOUND`（源集里没有这份文档
#: 的会话时 fail-closed，**绝不回退到源集里任何一份**）。工具名、参数 schema、返回体形状都
#: 没变，变的是"哪一份会话来回答"——这正是必须升版本的那种变。
#:
#: **与方案的偏差（需在完成报告里明说）**：方案 §L3 第 6 条写的是 `v2 → v3`。该方案成文时
#: v3 尚未被「候选粒度 span→material」占用；现场 HEAD 上 `v3` 已经是那一变更的版本号，因此
#: 本批改用 **v4**，而不是把两个不同的 wire 变更塞进同一个版本号。
#:
#: **v5**：返回体新增**合格表对象**分支（`table_objects` + `table_refusals`，见
#: `harness/table_object_release.py`）。这是 wire 变更：正文人口（只收 `body` span）天然
#: 拿不到「只以表格存在」的栏目材料（营业收入构成 / 营业成本构成 / 毛利率），本版把
#: **已定位块**里已放行的表对象按同一 selector 的定位结果一并返回。三条硬边界：
#: ①人口只含**本次候选材料 host 的 Evidence 块**（不按整篇文档扫表），跨块续读只允许紧邻的
#: 一块；②表对象是**阅读材料**（`reading_material=True` / `numeric_authority=False`），
#: 与正文材料是**不同身份**（另有 `released_object_id` / `table_content_id`），绝不混用；
#: ③表题形态退化（单位行 / 勾选残句 / 期间碎片）的对象如实带 `target_title_verifiable=False`，
#: 消费方不得把它当成具名表材料。`candidates` / `gaps` / `content_dispositions` 的形状与语义
#: **不变**，状态判定仍只由正文材料决定（表对象是增项，不是限制项）。
#:
#: **v6**：v5 只交出**表对象**（诊断人口），于是「只以表格存在的栏目」在本链上仍然零材料——
#: 对象不是 Pack 材料。本版让每个已定位块里的合格表对象**连带**产出正式 Pack 材料
#: （`material_type=table_context`，解析语义版本 `tom-1`，见 `harness/table_object_materials.py`），
#: 并在 `table_materials` 键下与对象**并列**返回（对象身份、材料身份仍是两条不同的账）。
#: 三条硬边界照旧：①阅读材料 ≠ 数字权威（声明进 payload 哈希）；②未获资格的对象逐条留
#: typed 拒绝记录，`table_object_refusal`（放行门）与 `table_object_material_refusal`（材料门）
#: 是两套原因；③「有表材料」≠「Contract 事实已取得」。`candidates` 形状与状态判定不变。
#:
#: **v7**（§0.18 W8 单通道）：表的来源由**文本侧重解**（`tobj-*` + `tom-1`）换成
#: **图侧逐表证明**（`gto-3` + `gtm-1`，见 `harness/graph_table_release.py` /
#: `harness/graph_table_materials.py`）。这是判定轴的变更，不只是换实现：`tobj-*` 是"从
#: Evidence 块原文当场重解一张表"，文档级一票否决；`gto-3` 是"在同一份图侧来源上逐表独立
#: 证明"，文档级终态**不再**自动否决一张已独立证明的表。四条边界同时改：
#: ① 对象的身份键由 `released_object_id`（`tobj-*`）换成 `release_id`（`gtr-*`）＋
#:    `table_id`（逐表证明）＋`local_proof_id`（`tlp-1`）——三者是不同的账，同名会混用；
#: ② 材料读视图从**压平整行文本**换成**逐格列表**（`cells`），因为 §0.19 要求格级来源，
#:    而一行字符串里没有"格"这个对象；
#: ③ `table_refusals` 是**文档级**的（逐表证明在整份快照上做，被拒的表可能一个可读来源都
#:    没有，图侧无从把它归属到某一块），逐条带 `population_scope="document"` 明标；
#: ④ `tobj-*` / `tom-1` 不再有生产调用方，历史对象与历史材料保持**只读兼容**（各自的
#:    模块与 resolver 一字未删，只是本工具不再走它们）。
#: `candidates` / `gaps` / `content_dispositions` 的形状与语义**不变**。
#: `v7` → **`v8`**（`tim-2`，M930-3 读取计划批）：新增**可选**入参 `span_cursor`
#: 与 `over_max_spans` 条目上的续读读数。这是一条**纯增量**的版本化接口：
#:  - **首次调用**（不带 `span_cursor`）的入参与返回体与 `v7` **逐字相同**；
#:  - 只在调用方显式给出 cursor 时才从读集内的某个 span 位置续读，且必须落在
#:    `node_ids` 里已被请求的节点上（越界 / 错节点 / 与 `evidence_ids` 同用一律
#:    fail-closed）。因此「要不要续读」这件事仍然只由调用方决定，工具不会自己循环。
TREE_INSPECT_TOOL_VERSION = "v8"

#: 表对象的工具级有界上界（超限只减少返回，绝不截断对象内部原文；命中上界如实登记）。
#: 材料是**被返回对象**的子集，因此共用同一上界，不另设第二个预算。
MAX_TABLE_OBJECTS = 20

#: 表对象在返回体里的类型标记（与正文材料的 `material` 身份**不同**，不得混用）。
GRAPH_TABLE_OBJECT_KIND = "released_graph_table"

#: `table_refusals` 里逐条记录的**人口口径**（封闭词表）：图侧的逐表证明在整份快照上做，
#: 因此拒绝记录是**文档级**的，不按本次 selector 的落点过滤——被拒的表可能一个可读来源都
#: 没有，图侧无从把它归属到某一块。标注出来是为了让读的人不会把"这份文档里某张表没过"
#: 误读成"本次选中的这一块里某张表没过"。
REFUSAL_POPULATION_DOCUMENT = "document"
OBJECT_POPULATION_LOCATED_HOSTS = "located_hosts"

#: 工具面保留的**逐表证明读数**（`local_proof` 的完整记录带全量区间与片段键，可达数万字符；
#: 工具面只带判据所需的这些读数，完整记录留在交付物 `table_proof_matrix.json` 里。
#: 截断的是**呈现**，不是结论：`defects` 逐条全量带出，一个都不少）。
_PROOF_SUMMARY_KEYS = (
    "proof_rule_version", "proof_schema_version", "proof_id", "table_id",
    "page_number", "structure_state", "structure_kind", "column_count",
    "row_count", "body_row_count", "header_row_count", "cell_count",
    "citable_cell_count", "merged_cell_count",
    "region_char_count", "owned_char_count", "residual_char_count",
    "deferred_char_count", "whitespace_char_count",
    "title_text", "unit_text", "continuation_anchor_locator",
    "scan_problems", "defects", "numeric_authority",
    "numeric_authority_reason", "permitted_use",
    "host_document_state", "host_document_refusal_kind",
    # `to_dict` 另给出这两项（结论位与七步读数），它们是读的人最先要看的两栏。
    "locally_proven", "steps",
)


def _proof_summary(proof) -> dict:
    """`tlp-1` 证明记录 → 工具面读数（见 :data:`_PROOF_SUMMARY_KEYS`）。

    只取键、不重算判据：结论位 `locally_proven` 与七步读数 `steps` 由 `tlp-1` 的
    `to_dict` 给出，本层**不**从缺陷去反推它们（那会变成第二处判据）。
    """
    if not isinstance(proof, dict):
        return {}
    summary = {name: proof.get(name) for name in _PROOF_SUMMARY_KEYS
               if name in proof}
    #: 片段键与区间**不进**工具面（它们进证明身份，但呈现上是数万字符的键列表）；
    #: 计数与样例有界读数保留，使"缺的是哪一段"仍可回查。
    summary["region_fragment_key_count"] = len(proof.get("region_fragment_keys") or ())
    summary["residual_interval_count"] = len(proof.get("residual_intervals") or ())
    summary["deferred_interval_count"] = len(proof.get("deferred_intervals") or ())
    summary["residual_examples"] = list(proof.get("residual_examples") or ())
    summary["deferred_examples"] = list(proof.get("deferred_examples") or ())
    summary["examples_truncated"] = bool(proof.get("examples_truncated"))
    summary["typecodes_by_step"] = dict(proof.get("typecodes_by_step") or {})
    return summary

#: 两个 selector 名字（恰用一个；缺失或同时出现都拒绝）。
TREE_INSPECT_SELECTORS = ("node_ids", "evidence_ids")

#: 有界上限（工具级；超限只减少候选，绝不截断 span 文本）。
DEFAULT_MAX_SPANS = 20
MAX_MAX_SPANS = 50
DEFAULT_MAX_CHARS_PER_SPAN = 4000
MAX_MAX_CHARS_PER_SPAN = 20000

#: `evidence_ids` fallback 的登记原因（封闭集合；不含"整块 Evidence"这一项）。
EVIDENCE_FALLBACK_GAP_REASONS = (
    # 该 evidence id 在本 run-bound verified 快照里找不到任何 component 归属
    "evidence_not_in_verified_snapshot",
    # 该 evidence id 有归属 span，但没有任何 span 成为合格材料（原因在被引用的 material gap 里）
    "evidence_has_no_qualified_span",
    # 归属 span 不在正文材料人口内（跨标题 / 非 body / 非当前 span builder）
    "not_a_material_candidate",
    # 归属 span 是 unassigned / fallback（其处置在快照 disposition 里，不作为正式材料）
    "unassigned_or_fallback_span",
)

TREE_INSPECT_SPEC = C.ToolSpec(
    name=TREE_INSPECT_TOOL_NAME,
    version=TREE_INSPECT_TOOL_VERSION,
    description=(
        "在 run-bound verified 标题树上按**二选一** selector 返回**已重切核验**的 OutlineSpan "
        "材料（node_ids 正常导航 / evidence_ids 有界 Evidence fallback 重切），"
        "并同批返回**已定位块**里已放行的合格表对象（阅读材料，无数字权威）；"
        "精确 Evidence locator + payload ref + 显式 gap；不返回整块 Evidence，不产生事实"),
    input_schema={
        "type": "object",
        "additionalProperties": False,
        "required": ["company_id", "document_id", "document_version",
                     "evidence_set_version", "need_id"],
        # 恰一个 selector：由公共 `validate_arguments` 的 `exactlyOneOf` 强制
        # （缺失或同时出现都在参数校验层被拒，executor 无法被绕过）。
        "exactlyOneOf": list(TREE_INSPECT_SELECTORS),
        "properties": {
            "company_id": {"type": "string", "minLength": 1},
            "document_id": {"type": "string", "minLength": 1},
            "document_version": {"type": "string", "minLength": 1},
            "evidence_set_version": {"type": "string", "minLength": 1},
            "need_id": {"type": "string", "minLength": 1},
            "aspect_id": {"type": "string"},
            "topic_id": {"type": "string"},
            "node_ids": {
                "type": "array", "minItems": 1, "maxItems": 40,
                "items": {"type": "string", "minLength": 1},
            },
            "evidence_ids": {
                "type": "array", "minItems": 1, "maxItems": 40,
                "items": {"type": "string", "minLength": 1},
            },
            "max_spans": {"type": "integer", "minimum": 1, "maximum": MAX_MAX_SPANS},
            "max_chars_per_span": {"type": "integer", "minimum": 1,
                                   "maximum": MAX_MAX_CHARS_PER_SPAN},
            # `tim-2`：**可选**续读位。`{"node_id": <node_ids 里的一个>, "span_index": k}`
            # 表示「跳过该节点的前 k 个 span 位置，从第 k+1 个继续」。只在 `node_ids`
            # selector 下有意义；缺省即从读集第一个位置开始（= `v7` 行为）。
            "span_cursor": {
                "type": "object", "additionalProperties": False,
                "required": ["node_id", "span_index"],
                "properties": {
                    "node_id": {"type": "string", "minLength": 1},
                    "span_index": {"type": "integer", "minimum": 0},
                },
            },
        },
    },
    output_schema={"type": "object"},
    allowed_routes=("DIRECT_EVIDENCE", "STANDARD_RAG", "DEEP_RETRIEVAL"),
    max_results=MAX_MAX_SPANS, timeout_ms=30000, retry_policy="none", cost_class="local",
)


class TreeToolError(TM.TreeMaterialError):
    """tree inspection 会话/工具层的 fail-closed 错误。"""


# ---------------------------------------------------------------------------
# run-bound 会话
# ---------------------------------------------------------------------------

class TreeInspectionSession:
    """一个 run 的 tree inspection 会话：绑定 runtime 已签发的 live span source。

    会话**不缓存内容**、不落盘、不签发任何东西：每个属性访问都经
    `LiveVerifiedSpanSource` 重新复核能力（签发登记表里不再有它即 fail-closed）。
    重切批次（材料 + payload 记录 + gap）按需构建一次并缓存于本会话，供工具与 runtime
    共享同一份字节。
    """

    __slots__ = ("_source", "_binding", "_batch", "_resolver", "_spans_by_node",
                 "_spans_by_id", "_spans_by_evidence", "_live_table_source",
                 "_graph_release_batch", "_graph_table_batch", "_table_resolver",
                 "_payload_resolver")

    def __init__(self, live_source) -> None:
        from document_structure.live_span_source import LiveVerifiedSpanSource
        if not isinstance(live_source, LiveVerifiedSpanSource):
            raise TreeToolError(
                f"tree inspection 会话只接受 LiveVerifiedSpanSource，得到 "
                f"{type(live_source).__name__}")
        self._source = live_source
        self._binding = TM.evidence_binding_from_snapshot(live_source.evidence_snapshot)
        self._batch = None
        self._resolver = None
        self._spans_by_node = None
        self._spans_by_id = None
        self._spans_by_evidence = None
        self._live_table_source = None
        self._graph_release_batch = None
        self._graph_table_batch = None
        self._table_resolver = None
        self._payload_resolver = None

    # -- 惰性重切 ---------------------------------------------------------

    def _ensure(self) -> None:
        if self._batch is not None:
            return
        snapshot = self._source.snapshot
        blocks = self._source.evidence_blocks
        outline = self._source.document_outline
        # 重切只做一次：resolver 内部已构建批次，会话直接复用同一份字节。
        self._resolver = TM.TreeMaterialPayloadResolver(
            snapshot=snapshot, blocks=blocks, current_evidence=self._binding,
            outline=outline)
        self._batch = self._resolver.batch
        by_node: dict = {}
        by_id: dict = {}
        by_evidence: dict = {}
        for span in snapshot.spans:
            by_node.setdefault(span.node_id, []).append(span.span_id)
            by_id[span.span_id] = span
            # 归属关系取自 verified 快照自身的 component 引用（**不猜、不反查文本**）。
            for evidence_id in sorted({ref[0] for ref in span.component_evidence_refs}):
                by_evidence.setdefault(evidence_id, []).append(span.span_id)
        self._spans_by_node = by_node
        self._spans_by_id = by_id
        self._spans_by_evidence = by_evidence

    @property
    def resolver(self) -> TM.TreeMaterialPayloadResolver:
        self._ensure()
        return self._resolver

    @property
    def batch(self) -> TM.TreeMaterialBatch:
        self._ensure()
        return self._batch

    @property
    def current_evidence(self) -> TM.CurrentEvidenceBinding:
        return self._binding

    def document_identity(self) -> dict:
        self._ensure()
        identity = self._batch.identity
        return {
            "company_id": identity["current_evidence"]["company_id"],
            "document_id": identity["current_evidence"]["document_id"],
            "document_version": identity["current_evidence"]["document_version"],
            "evidence_set_version": identity["current_evidence"]["evidence_set_version"],
        }

    # -- 只读检查 ---------------------------------------------------------

    def node_ids(self) -> tuple:
        self._ensure()
        outline = self._source.document_outline
        return tuple(node.node_id for node in outline.nodes)

    # -- 合格表对象（v5）--------------------------------------------------

    def ordered_evidence_blocks(self) -> list:
        """本次 run-bound 源的 Evidence 块，按（页号, 块序）确定性排序（唯一顺序来源）。"""
        return sorted(self._source.evidence_blocks,
                      key=lambda block: (block.page_number, block.block_index))

    def _table_heading_paths(self, blocks: Sequence) -> dict:
        """块 → 标题路径（来自**本树**：块上的 span → 节点 → `structural_path`）。

        表块常常没有正文 span，取不到就**不写**（不编造路径）。导航只作阅读坐标。
        """
        self._ensure()
        outline = self._source.document_outline
        node_by_id = {node.node_id: node for node in outline.nodes}
        paths: dict = {}
        for block in blocks:
            bid = str(getattr(block, "evidence_block_id", "") or "")
            for span_id in self._spans_by_evidence.get(bid, ()):
                span = self._spans_by_id.get(span_id)
                node = node_by_id.get(getattr(span, "node_id", None))
                path = tuple(getattr(node, "structural_path", ()) or ())
                if path:
                    paths[bid] = path
                    break
        return paths

    def live_table_source(self):
        """本份文档的**图侧正式来源**（`lts-2`；由同次 live TS4 能力当场取得，不落盘）。

        「文档级拒发」**不是**异常：它返回一个携带 typed 拒绝的来源，逐表证明仍在
        `snapshot_tables`（已构造的对象）上做 —— §0.19 要的正是"文档级问题不自动否决一张
        已独立证明的目标表"。builder 自身失败（快照从未诞生）才没有对象可证明。
        """
        source = self._live_table_source
        if source is None:
            from document_structure import live_table_source as LTS
            source = LTS.build_live_table_source(self._source)
            self._live_table_source = source
        return source

    def graph_release_batch(self) -> dict:
        """本份文档的**逐表放行批次**（`gto-3`；在同一份图侧来源上逐表完整证明）。

        求解**全篇一次**（判据只有一份快照）；人口过滤由调用方按块做。批次里同时带着
        文档级终态（`document_qualified` / `document_refusal`）与逐表结果，读的人能分清
        「整份文档没过」与「某一张表没过」。
        """
        batch = self._graph_release_batch
        if batch is None:
            batch = GTR.release_graph_tables(self.live_table_source())
            self._graph_release_batch = batch
        return batch

    def graph_table_batch(self) -> GTM.GraphTableMaterialBatch:
        """本份文档的**图侧表材料批次**（`gtm-1`；放行记录 → 正式 Pack 材料）。

        与 span 材料同样的惰性单次构建：批次同时是 `table_resolver` 的字节来源，使
        「工具返回的材料」与「runtime 提交的 payload」出自同一份构建结果。
        """
        batch = self._graph_table_batch
        if batch is None:
            blocks = self.ordered_evidence_blocks()
            batch = GTM.build_graph_table_material_batch(
                release_batch=self.graph_release_batch(), blocks=blocks,
                binding=self._binding,
                heading_paths=self._table_heading_paths(blocks))
            self._graph_table_batch = batch
        return batch

    @property
    def table_resolver(self) -> GTM.GraphTablePayloadResolver:
        """图侧表材料的独立重切解析器（runtime 提交 payload 时用同一份字节）。"""
        resolver = self._table_resolver
        if resolver is None:
            resolver = GTM.GraphTablePayloadResolver(self.graph_table_batch())
            self._table_resolver = resolver
        return resolver

    @property
    def payload_resolver(self):
        """本份来源**全部**材料的唯一只读解析入口（span 材料 + 图侧表材料）。

        两个解析器是两种解析语义（`tmr-2` / `gtm-1`），任一 ref 只有本语义的那一个会命中
        （另一个按 object_type/version 返回 None），因此组合器不会出现"同一 payload 被两套
        来源同时认领"。取这个入口而不是 `resolver`：只给 span 解析器会让 Pack 里的表材料
        在写作侧整批 dangling。
        """
        resolver = self._payload_resolver
        if resolver is None:
            from harness import topic_runtime as TR
            resolver = TR.CombinedPayloadResolver((self.resolver, self.table_resolver))
            self._payload_resolver = resolver
        return resolver

    def located_table_hosts(self, selector: str,
                            selector_ids: Sequence[str]) -> tuple[tuple, dict]:
        """本次派发**已定位**的表宿主块（有界）＋ 逐块的组件归属节点。

        返回 ``(block_ids, nodes_by_block)``。两条 selector 各有自己的"已定位"语义：

        * ``evidence_ids``：本次**选中**的那些块本身就是宿主——**不再**要求块里出过
          `role="body"` 候选（块里只有表、没有可成材料的正文，是最常见的情形）。
        * ``node_ids``：本次**选中节点**在 `snapshot.components`（`spc-1`）上的归属块，
          即"这条 Evidence 的哪一段落在哪个节点"这条**准入事实本身**（`evidence_block_id`
          ＋ `node_id`）。它不是文本反查，也不是标题相似度。

        四条边界：

        1. **不按整篇扫**：块集只来自本次 selector 的落点；与本次无关的块一个都不进
           （`evals/test_m930_3_tree_table_branch` 的反例就钉在这条上）。
        2. **不猜归属**：`node_id` 为空的表块在 node selector 下**不可归属**，不进人口
           ——不得按标题像不像、正文提没提来补一条假归属（三份演示文档里有 3 个这样的块）。
        3. 块序以 `ordered_evidence_blocks()`（页号, 块序）为**唯一顺序来源**，可复现；
           不在块序里的 id 排在最后并按 id 定序（确定性，不依赖集合迭代顺序）。
        4. 这是**人口**，不是"返回过什么"：与候选产没产出**无关**，所以 span 截断
           （`max_spans`）与字符上界（`max_chars_per_span`）都**不得**影响表对象。
        """
        index = {str(getattr(block, "evidence_block_id", "") or ""): ordinal
                 for ordinal, block in enumerate(self.ordered_evidence_blocks())}
        wanted: dict[str, list] = {}
        if selector == "evidence_ids":
            # 选中的块**本身就是**本次定位到的宿主：块里有没有正文候选不改变这一点。
            for block_id in selector_ids:
                bid = str(block_id or "")
                if bid:
                    wanted.setdefault(bid, [])
        else:
            selected = {str(node_id) for node_id in selector_ids}
            nodes_by_block: dict[str, set] = {}
            # 归属关系①：准入事实（component → 块 / 节点）。
            for component in self._source.snapshot.components:
                node_id = str(getattr(component, "node_id", "") or "")
                if node_id not in selected:
                    continue
                bid = str(getattr(component, "evidence_block_id", "") or "")
                if bid:
                    nodes_by_block.setdefault(bid, set()).add(node_id)
            # 归属关系②：span 落点（span → 块 / 节点）。两条关系都取自 run-bound verified
            # 快照自身的引用，**不**做文本反查。取并集是为了不因某一条关系缺项而漏块；
            # 实测①是②的严格超集（"只有 span 关系"的含表块三份文档均为 0）。
            for bid, span_ids in self._spans_by_evidence.items():
                for span_id in span_ids:
                    span = self._spans_by_id.get(span_id)
                    node_id = str(getattr(span, "node_id", "") or "")
                    if node_id in selected and bid:
                        nodes_by_block.setdefault(bid, set()).add(node_id)
            wanted = {bid: sorted(nodes) for bid, nodes in nodes_by_block.items()}
        block_ids = sorted(wanted, key=lambda b: (index.get(b, len(index)), b))
        return tuple(block_ids), wanted

    def release_tables_for(self, candidates: Sequence[dict], *,
                           host_blocks: Sequence[str] | None = None,
                           host_node_ids: dict | None = None,
                           max_objects: int = MAX_TABLE_OBJECTS
                           ) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
        """把**本次已定位的 Evidence 宿主块**里的**已放行图侧表**及其 Pack 材料取出来。

        返回 ``(对象, 逐条拒绝记录, 材料, skipped)``。六条边界：

        1. **对象与材料的人口**由调用方给出的**本次派发已定位块**（`host_blocks`，由
           `located_table_hosts` 按 selector 语义定出）决定；**候选所在块恒在人口内**
           （兜底并集，与旧行为一致）。`host_blocks=None` 时退回"本次候选材料的宿主块"
           这一旧口径（直接调用者用）。绝不按整篇文档扫表 —— 一份年报里有几十张表，
           整篇返回等于把无关材料倒进 Pack。
        2. **一张表属于承载了它可引用片段的每一条块**（`gtm-1` 的 `records_for_hosts`），
           不是"挑一个主宿主"：同一张跨块表不会因为 selector 落在这块还是那块而时进时出。
        3. 表对象与正文材料是**不同身份**：对象带 `release_id`（`gtr-*` 放行身份）/
           `table_id`（逐表证明身份）/ `local_proof_id`（`tlp-1` 证明身份），材料
           另有自己的内容寻址 `material_id`。三者在返回体里放在不同的键下
           （`table_objects` ≠ `table_materials` ≠ `candidates[].material`）。
        4. **拒绝记录是文档级**的，不按本次落点过滤（`population_scope="document"`）：
           逐表证明在整份快照上做，被拒的表可能一个可读来源都没有，图侧无从把它归属到
           某一块。它**不**因为"与本次 selector 无关"而消失——那正是 §0.19 说的"无关页面
           上的整文档问题仍留在账上"。分两种：图侧就没放行的（`gtr-*` 逐表证明未过）与
           放行了却成不了材料的（`gtm-1` 材料门），前者逐条带 `problems`。
        5. 材料是**被返回对象**的子集，共用同一个上界：超限只减少返回（对象与材料一起
           不返回），并把截断如实登记在 `skipped`。
        6. `node_ids` 是**导航回指**（谁把这块定为宿主），不是材料身份：候选侧的落点节点
           与组件侧的归属节点**取并集**，`host_node_ids` 只补候选侧为空的那部分。
        """
        block_ids: list[str] = []
        nodes_by_block: dict[str, list[str]] = {}
        for block_id in host_blocks or ():
            bid = str(block_id or "")
            if not bid or bid in nodes_by_block:
                continue
            nodes_by_block[bid] = []
            block_ids.append(bid)
        for bid, node_ids in (host_node_ids or {}).items():
            bid = str(bid or "")
            if bid not in nodes_by_block:
                continue
            nodes_by_block[bid] = [str(n) for n in sorted(node_ids) if str(n)]
        for cand in candidates or ():
            bid = str(cand.get("parent_evidence_id") or "")
            if not bid:
                continue
            if bid not in nodes_by_block:
                nodes_by_block[bid] = []
                block_ids.append(bid)
            node_id = str(cand.get("node_id") or "")
            if node_id and node_id not in nodes_by_block[bid]:
                nodes_by_block[bid].append(node_id)
        if not block_ids:
            return [], [], [], []

        batch = self.graph_table_batch()
        records = batch.records_for_hosts(block_ids)
        # 两种拒绝是**两本账**：①图侧就没放行（`gtr-*` 逐表证明未过）；②放行了却成不了
        # 材料（`gtm-1` 材料门）。两者都逐条带出，读的人才能分清「表没被证明」与
        # 「表证明了但进不了 Pack」。
        refusals: list[dict] = [
            {**dict(record), "population_scope": REFUSAL_POPULATION_DOCUMENT}
            for record in self.graph_release_batch().get("refusals", ())]
        for refusal in batch.refusals:
            refusals.append({
                **dict(refusal),
                "population_scope": REFUSAL_POPULATION_DOCUMENT,
                "kind": "graph_table_material_refusal",
            })
        objects: list[dict] = []
        materials: list[dict] = []
        skipped: list[dict] = []
        for record in records:
            if len(objects) >= max_objects:
                skipped.append({
                    "reason": "over_max_table_objects", "limit": max_objects,
                    "detail": "本次已定位块里的已放行表超过工具级上界：其余对象与其材料"
                              "**未返回**；未返回不等于该块没有表",
                })
                break
            material = batch.material_for_release(record)
            if material is None:
                continue
            host_ids = batch.release_hosts.get(str(record.get("release_id") or ""), ())
            bid = str(host_ids[0] if host_ids else "")
            # 导航回指取本次**已定位**的那一条宿主（不然"谁把这块定为宿主"会在跨块表上
            # 指向一块本次根本没选中的块）。
            located = [str(b) for b in host_ids if str(b) in nodes_by_block]
            navigable = located[0] if located else bid
            # 对象身份与材料身份分开落盘：对象上只加**导航**回指（node_ids），
            # 材料身份在批次里已经算好，绝不再受 node 影响。
            wire = self._table_object_view(record)
            wire["host_evidence_id"] = bid
            wire["node_ids"] = list(nodes_by_block.get(navigable, ()))
            objects.append(wire)
            materials.append({
                **material.to_dict(),
                "release_id": str(record.get("release_id") or ""),
                "host_evidence_id": bid,
                "node_ids": list(nodes_by_block.get(navigable, ())),
            })
        return objects, refusals, materials, skipped

    @staticmethod
    def _table_object_view(record) -> dict:
        """放行记录 → 工具面的**表对象投影**（有界；完整记录在交付物里）。

        对象只带**身份 + 导航 + 资格读数**，不带逐格全表：逐格读数是**材料**的内容
        （它已经进 payload 哈希），对象再抄一份就等于给同一件事准备两个真值。
        投影里没有任何一处是从记录外推的：每一项都是记录里逐字有的键。
        """
        return {
            "kind": GRAPH_TABLE_OBJECT_KIND,
            "population_scope": OBJECT_POPULATION_LOCATED_HOSTS,
            # 三套身份（放行 / 逐表证明 / 表本身），互不顶替。
            "release_id": str(record.get("release_id") or ""),
            "local_proof_id": str(record.get("local_proof_id") or ""),
            "table_id": str(record.get("table_id") or ""),
            "table_locator": str(record.get("table_locator") or ""),
            "final_material_snapshot_id": str(record.get("final_material_snapshot_id") or ""),
            "release_rule_version": str(record.get("release_rule_version") or ""),
            "release_schema_version": str(record.get("release_schema_version") or ""),
            "document_id": str(record.get("document_id") or ""),
            "document_version": str(record.get("document_version") or ""),
            "evidence_set_version": str(record.get("evidence_set_version") or ""),
            "page_number": int(record.get("page_number") or 0),
            "title_text": str(record.get("title_text") or ""),
            "unit_text": str(record.get("unit_text") or ""),
            "structure_kind": str(record.get("structure_kind") or ""),
            "structure_class": str(record.get("structure_class") or ""),
            "structure_state": str(record.get("structure_state") or ""),
            "column_count": int(record.get("column_count") or 0),
            "row_count": int(record.get("row_count") or 0),
            "body_row_count": int(record.get("body_row_count") or 0),
            "cell_count": int(record.get("cell_count") or 0),
            "fragment_keys": [list(k) for k in (record.get("fragment_keys") or ())],
            "evidence_block_ids": [str(x) for x in (record.get("evidence_block_ids") or ())],
            "all_columns_supported": bool(record.get("all_columns_supported")),
            "unsupported_columns": list(record.get("unsupported_columns") or ()),
            "content_qualification": dict(record.get("content_qualification") or {}),
            "local_proof_summary": _proof_summary(record.get("local_proof") or {}),
        }

    def inspect(self, arguments: dict) -> C.ToolResult:
        """按已校验的参数返回 typed span candidates；不写库、不调 LLM。"""
        self._ensure()
        declared = {
            "company_id": arguments["company_id"],
            "document_id": arguments["document_id"],
            "document_version": arguments["document_version"],
            "evidence_set_version": arguments["evidence_set_version"],
        }
        actual = self.document_identity()
        if declared != actual:
            return _fail("SOURCE_UNTRUSTED",
                         f"声明的 document/current identity 与 run-bound verified 源不一致："
                         f"声明 {declared}，实际 {actual}")

        selector, selector_ids, selector_error = _select(arguments)
        if selector_error is not None:
            # 公共 `exactlyOneOf` 已在参数校验层拦过一次；这里是**直接调用**也不放行的第二道门。
            return _fail("INVALID_ARGUMENTS", selector_error)
        if selector == "node_ids":
            known = set(self.node_ids())
            unknown = [nid for nid in selector_ids if nid not in known]
            if unknown:
                return _fail("INVALID_ARGUMENTS",
                             f"node_ids 含不在本树上的节点: {unknown[:5]}")

        # `tim-2` 续读位：把**读集内的 span 位置序列**（node 顺序 × 该节点 span 序，含零材料
        # 位置）里已经消费掉的前缀切掉。之所以按**全量位置**而不是「有材料的位置」计：
        # 上一轮 `break` 就停在某个位置之后，若下一轮从「下一个有材料的位置」起，夹在中间的
        # 零材料位置（它们带 gap / 内容处置）会被整段跳过而**再也不会被登记**。按全量位置续读
        # 则位置严格前进、既不漏也不重。
        span_cursor = arguments.get("span_cursor")
        positions: tuple[tuple[str, str], ...] = ()
        resumed_at = 0
        if selector == "node_ids":
            positions = tuple(
                (str(node_id), str(span_id)) for node_id in selector_ids
                for span_id in self._spans_by_node.get(node_id, ()))
            if span_cursor is not None:
                requested_node = str(span_cursor.get("node_id"))
                requested_index = int(span_cursor.get("span_index"))
                if requested_node not in {str(n) for n in selector_ids}:
                    return _fail(
                        "INVALID_ARGUMENTS",
                        f"span_cursor.node_id {requested_node!r} 不在本次 node_ids 里："
                        "续读只能在**本次读集内**前进，不得借续读扩大读集")
                seen = 0
                found = None
                for index, (node_id, _sid) in enumerate(positions):
                    if node_id != requested_node:
                        continue
                    if seen == requested_index:
                        found = index
                        break
                    seen += 1
                if found is None:
                    return _fail(
                        "INVALID_ARGUMENTS",
                        f"span_cursor 越界：节点 {requested_node!r} 本次只有 {seen} 个 span "
                        f"位置，请求从第 {requested_index + 1} 个起（位置序列长度 "
                        f"{len(positions)}）")
                resumed_at = found
        elif span_cursor is not None:
            return _fail(
                "INVALID_ARGUMENTS",
                "span_cursor 只对 node_ids selector 有意义：evidence_ids 重切没有可续读的"
                "有界位置序列")

        max_spans = int(arguments.get("max_spans", DEFAULT_MAX_SPANS))
        max_chars = int(arguments.get("max_chars_per_span", DEFAULT_MAX_CHARS_PER_SPAN))

        # 表对象的人口在 span 循环**之前**按 selector 定完：表是"本次派发定位到的宿主块"里的
        # 表，不是"正文候选碰巧落在的块"里的表。因此 span 截断 / 字符上界 / 零正文候选都
        # **不得**影响表对象——那些是 span 通道自己的上界。
        table_host_blocks, table_host_nodes = self.located_table_hosts(selector, selector_ids)

        # 一个 span 可以有多份材料（跨块 → 逐块片段）：所以这里建的是**列表**索引，不是
        # 覆盖式的单值索引。位置序列就是块序（构建期按 span 本地顺序产出）。
        material_by_span: dict = {}
        for span_id, material in zip(self._batch.span_ids, self._batch.materials):
            material_by_span.setdefault(span_id, []).append(material)
        gaps_by_span: dict = {}
        for gap in self._batch.gaps:
            gaps_by_span.setdefault(gap.span_id, []).append(gap)
        # §二 2.3：人口内但内容不合格（孤立标题 / 纯版式碎片 / 读不出状态的勾选行）的候选
        # **不是** gap——它是第三种 typed 去路，原文与定位仍在批次与审计里。若把它并进
        # `gaps`，读回的人会以为「重切失败」，而真正的原因是内容形态。
        dispositions_by_span: dict = {}
        for disposition in self._batch.content_dispositions:
            dispositions_by_span.setdefault(disposition.span_id, []).append(disposition)
        outline = self._source.document_outline
        node_by_id = {node.node_id: node for node in outline.nodes}

        candidates: list = []
        gaps: list = []
        content_dispositions: list = []
        skipped: list = []
        if selector == "node_ids":
            stream = [(self._spans_by_id.get(sid), sid, None)
                      for _node_id, sid in positions[resumed_at:]]
        else:
            stream = self._evidence_stream(selector_ids, gaps, content_dispositions,
                                           skipped, dispositions_by_span)
        budget_exhausted = False
        spans_read = 0
        consumed_through = resumed_at - 1   # 最后一个**已消费**的绝对位置
        for offset, (span, span_id, allowed_blocks) in enumerate(stream):
            # 位置**严格前进**：无论这个位置是产出候选、被字符上界挡下、还是零材料（只登记
            # gap / 内容处置），只要进入过循环体就算消费过。续读位因此不会回退。
            consumed_through = resumed_at + offset
            node_id = getattr(span, "node_id", None)
            pieces = material_by_span.get(span_id, ())
            if not pieces:
                for gap in gaps_by_span.get(span_id, ()):
                    gaps.append(gap.to_dict())
                for disposition in dispositions_by_span.get(span_id, ()):
                    content_dispositions.append(disposition.to_dict())
                continue
            # 字符上界按**整段 span** 判（比逐片段更保守）：上界不得因为拆分而变松。
            if len(span.normalized_text) > max_chars:
                skipped.append({
                    "span_id": span_id, "node_id": node_id,
                    "reason": "over_max_chars_per_span",
                    "chars": len(span.normalized_text), "limit": max_chars,
                })
                continue
            node = node_by_id.get(node_id)
            heading_path = list(getattr(node, "structural_path", ()) or ())
            before = len(candidates)
            for piece in pieces:
                parent_evidence_id = piece.authority_assessment.evidence_id
                if allowed_blocks is not None and parent_evidence_id not in allowed_blocks:
                    # evidence_ids selector：本次只选了某些父块。同一 span 落在**未选中**
                    # 的父块上的那一段不算本次召回——如实登记，不静默略过，也不冒充被选中。
                    skipped.append({
                        "span_id": span_id, "node_id": node_id,
                        "evidence_id": parent_evidence_id,
                        "reason": "piece_parent_not_selected",
                        "detail": "同一 span 的其余片段属于本次未选中的父 Evidence 块",
                    })
                    continue
                envelope = _load_envelope(
                    self._batch.payload_bytes_by_id(piece.content_hash)) or {}
                tree = envelope.get("tree_material") or {}
                text = (envelope.get("content") or {}).get("text")
                if not isinstance(text, str):
                    raise TreeToolError(
                        f"材料 {piece.material_id} 的 payload 信封缺 content.text：候选文本"
                        f"必须逐字来自该材料自己的载荷，不得回退成整段 span 正文")
                candidates.append({
                    "node_id": node_id,
                    "heading_path": heading_path,
                    "span_id": span_id,
                    "material_id": piece.material_id,
                    "material": piece.to_dict(),
                    "evidence_char_range": tree.get("evidence_char_range"),
                    "span_local_char_range": tree.get("span_local_char_range"),
                    # 跨块拆分的段身份（第几段 / 共几段 / 全文长度）。单块材料没有这一项。
                    "span_split": tree.get("span_split"),
                    "coverage": tree.get("coverage"),
                    "parent_evidence_id": parent_evidence_id,
                    # 候选文本 = **这份材料**的精确片段（不是整段 span 正文）：候选与被引用的
                    # 材料必须是同一段文字，否则"claim 逐字来自材料"这句话就不成立。
                    "text": text,
                })
            if len(candidates) > before:
                spans_read += 1
            if spans_read >= max_spans:
                # 与 v2 同一条语义：上界以 **span** 计、**读到上界即停**，并把上界如实登记
                # （截断不得被读成零命中）。一个 span 的逐块片段**一起**读出——拆分是同一段
                # 正文的形状，不是额外预算；所以候选条数可能略多于 span 上界（至多一个 span
                # 的块数）。若按候选截，跨块 span 会把上界挤早，等于用拆分偷偷收紧预算。
                budget_exhausted = True
                break
        if budget_exhausted:
            # `tim-2`：把**停在哪一个位置**如实登记出来，续读才有可核对的起点。位置是
            # 读集内的**全量 span 位置序**（node 顺序 × 该节点 span 序）的下标，与
            # `span_cursor` 同一坐标系；`next_cursor=None` 只在读集位置已走完时出现
            # （那时没有「未读」，不该报续读位）。
            next_index = consumed_through + 1
            next_cursor = (
                _cursor_at(positions, next_index) if next_index < len(positions) else None)
            skipped.append({
                "reason": "over_max_spans", "limit": max_spans,
                "detail": "达到 max_spans 上界即停（按 span 计）：其后 span 的候选"
                          "**未读**；本次未读不等于没有材料。本条只限**正文 span "
                          "通道**：本次已定位宿主块里的表对象仍按自己的上界返回",
                "next_cursor": next_cursor,
                "unread_span_positions": max(len(positions) - next_index, 0),
                "total_span_positions": len(positions),
                "resumed_at_span_position": resumed_at,
            })

        # v7：同批返回**本次已定位宿主块**里的已放行图侧表**及其 Pack 材料**（有界；不按
        # 整篇文档扫表，也不受上面的 span 上界影响）。
        table_objects, table_refusals, table_materials, table_skipped = \
            self.release_tables_for(candidates, host_blocks=table_host_blocks,
                                    host_node_ids=table_host_nodes,
                                    max_objects=MAX_TABLE_OBJECTS)
        skipped.extend(table_skipped)
        release_batch = self.graph_release_batch()
        table_batch_result = self.graph_table_batch()

        data = {
            "document": actual,
            "snapshot": dict(self._batch.identity["snapshot"]),
            "policy": dict(self._batch.identity["policy"]),
            "request": {
                "need_id": arguments["need_id"],
                "aspect_id": arguments.get("aspect_id"),
                "topic_id": arguments.get("topic_id"),
                "selector": selector,
                "node_ids": list(selector_ids) if selector == "node_ids" else None,
                "evidence_ids": list(selector_ids) if selector == "evidence_ids" else None,
            },
            "candidates": candidates,
            "table_objects": table_objects,
            "table_materials": table_materials,
            "table_refusals": table_refusals,
            # 文档级表读数：**文档被拒 ≠ 没有表可证**。`no_table_objects`（快照从未诞生）
            # 与"文档级被拒但逐表独立证明通过"是两种不同的情形，读的人必须能分清。
            "table_batch": {
                "release_batch_id": str(release_batch.get("batch_id") or ""),
                "batch_state": str(release_batch.get("batch_state") or ""),
                "document_qualified": bool(release_batch.get("document_qualified")),
                "document_refusal": release_batch.get("document_refusal"),
                "declared_table_count": release_batch.get("declared_table_count"),
                "released_table_count": int(release_batch.get("released_table_count") or 0),
                "refused_table_count": int(release_batch.get("refused_table_count") or 0),
                "accounting_balanced": bool(release_batch.get("accounting_balanced")),
                "material_count": len(table_batch_result.materials),
                "material_refusal_reason_counts":
                    table_batch_result.refusal_reason_counts(),
            },
            "gaps": gaps,
            "content_dispositions": content_dispositions,
            "skipped": skipped,
            "limits": {"max_spans": max_spans, "max_chars_per_span": max_chars,
                       "max_table_objects": MAX_TABLE_OBJECTS},
            "versions": {
                "tool": TREE_INSPECT_TOOL_VERSION,
                "resolver": TM.TS.TREE_MATERIAL_RESOLVER_VERSION,
                "envelope_kind": TM.TREE_MATERIAL_ENVELOPE_KIND,
                "content_kinds": list(TM.TREE_MATERIAL_CONTENT_KINDS),
                "table_release_rule_version": GTR.GRAPH_TABLE_RELEASE_RULE_VERSION,
                "table_release_schema_version": GTR.GRAPH_TABLE_RELEASE_SCHEMA_VERSION,
                "graph_table_material_version": GTM.GRAPH_TABLE_MATERIAL_VERSION,
                "graph_table_material_envelope_kind": GTM.GRAPH_TABLE_MATERIAL_ENVELOPE_KIND,
                "live_table_source_version": LTS.LIVE_TABLE_SOURCE_VERSION,
                "dependency_fingerprint": self._batch.identity["dependency_fingerprint"],
                "table_dependency_fingerprint":
                    self.graph_table_batch().identity["dependency_fingerprint"],
            },
        }
        # `evidence_ids` = 本次真正交出材料的**来源块**（正文 span 的父块 ∪ 表材料的宿主块）。
        # 只由候选派生会把"只有表"的那次调用报成空（见下面 `not candidates` 分支）。
        table_host_ids = sorted({str(m.get("host_evidence_id") or "") for m in table_materials}
                                - {""})
        evidence_ids = sorted({c["parent_evidence_id"] for c in candidates}
                              | set(table_host_ids))
        disposition_note = (f"；{len(content_dispositions)} 条内容处置（非 gap，原文与定位见条目）"
                            if content_dispositions else "")
        table_note = (f"；{len(table_materials)} 份表材料（{len(table_objects)} 张已放行表、"
                      f"{len(table_refusals)} 条逐条拒绝）：表通道与正文 span 通道"
                      f"**分开计**，两者都不是对方的证据"
                      if (table_materials or table_objects or table_refusals) else "")
        if not candidates and not table_materials and not table_objects:
            status = "EMPTY"
            error_code = "RETRIEVAL_EMPTY"
            message = ("候选材料下没有任何已重切核验的 OutlineSpan 材料，"
                       "本次已定位宿主块里也没有已放行的表（不升格整块 Evidence）"
                       + disposition_note + table_note)
        elif not candidates:
            # 只有表：这**不是**一次"未命中"的空结果。报 EMPTY/RETRIEVAL_EMPTY 会让
            # 「表存在、已放行、已交出材料」被读成「这里什么都没有」。
            status = "PARTIAL" if (skipped or gaps or content_dispositions) else "SUCCESS"
            error_code = None
            message = ("0 份正文 span 材料（已放行表的宿主块本来就可能没有正文材料）"
                       + disposition_note + table_note)
        elif skipped or gaps or content_dispositions:
            status = "PARTIAL"
            error_code = None
            message = (f"{len(candidates)} 份材料；{len(gaps)} 条 gap、"
                       f"{len(skipped)} 条受限{disposition_note}")
        else:
            status = "SUCCESS"
            error_code = None
            message = f"{len(candidates)} 份材料"
        return C.ToolResult(
            call_id="", tool_name=TREE_INSPECT_TOOL_NAME,
            tool_version=TREE_INSPECT_TOOL_VERSION, status=status, data=data,
            evidence_ids=evidence_ids, error_code=error_code, message=message)

    # -- evidence selector：有界 Evidence → 重新定位并重切为 span ------------

    def _evidence_stream(self, evidence_ids: tuple, gaps: list, content_dispositions: list,
                         skipped: list, dispositions_by_span: dict) -> list:
        """把父 Evidence ID 映射为 verified 快照里**可证明归属**的 span 流。

        只返回该快照里确有 component 归属的 span；不返回整块 Evidence 正文，不做文本
        反查，不猜"最像"的 span。归属 span 未成为材料的逐条登记，且**按去路分开记**：

        * 结构上过不了重切核验 → material gap（`evidence_has_no_qualified_span`，
          指向同一 span 的 material gap）；
        * 内容上不合格（§二 2.3：孤立标题 / 纯版式碎片 / 读不出状态的勾选行）→
          typed content disposition，**不是** gap；原文与定位仍在审计链里；
        * 本就不在材料人口内 → `unassigned_or_fallback_span` / `not_a_material_candidate`。

        产出的是 ``(span, span_id, allowed_blocks)``：``allowed_blocks`` 是本次选中的父块集合，
        调用方只对该集合内**那一段**（片段材料）产出候选——跨块 span 的另一段属于别的父块，
        不在本次 selector 的选定范围内。
        """
        material_spans = set(self._batch.span_ids)
        stream: list = []
        for evidence_id in evidence_ids:
            span_ids = tuple(self._spans_by_evidence.get(evidence_id, ()))
            if not span_ids:
                gaps.append({
                    "reason": "evidence_not_in_verified_snapshot",
                    "evidence_id": evidence_id, "span_id": None, "node_id": None,
                    "detail": "该 Evidence 在本 run-bound verified 快照里没有任何 component "
                              "归属（不按文本反查、不升格整块 Evidence）",
                })
                continue
            qualified = 0
            disposed = 0
            for span_id in span_ids:
                span = self._spans_by_id.get(span_id)
                if TM.is_tree_material_candidate(span):
                    if span_id in material_spans:
                        stream.append((span, span_id, frozenset((evidence_id,))))
                        qualified += 1
                    else:
                        found = dispositions_by_span.get(span_id, ())
                        if found:
                            for disposition in found:
                                entry = disposition.to_dict()
                                entry["evidence_id"] = evidence_id
                                content_dispositions.append(entry)
                            disposed += 1
                            continue
                        # 人口内、结构上过不了重切核验：原因由批次自己的 material gap 给出
                        # （不覆盖）。
                        gaps.append({
                            "reason": "evidence_has_no_qualified_span",
                            "evidence_id": evidence_id, "span_id": span_id,
                            "node_id": getattr(span, "node_id", None),
                            "detail": "归属 span 未通过重切核验（见同一 span 的 material gap）",
                        })
                    continue
                unassigned = (getattr(span, "node_id", None) is None
                              or getattr(span, "unassigned_reason", None) is not None
                              or bool(getattr(span, "is_fallback", False)))
                gaps.append({
                    "reason": ("unassigned_or_fallback_span" if unassigned
                               else "not_a_material_candidate"),
                    "evidence_id": evidence_id, "span_id": span_id,
                    "node_id": getattr(span, "node_id", None),
                    "detail": "归属 span 不在正式正文材料人口内（跨标题 / 非 body / "
                              "非当前 span builder / unassigned / fallback）",
                })
            if qualified == 0:
                # 只有"内容不合格"挡路时，读回必须能分清它**不是**重切失败。
                skipped.append({"reason": "evidence_without_material",
                                "evidence_id": evidence_id,
                                "span_count": len(span_ids),
                                "content_disposition_count": disposed})
        return stream


def _select(arguments: dict) -> tuple[str, tuple, str | None]:
    """两种互斥 selector 的判定：返回 ``(selector, ids, error)``。

    恰好一个 → ``(name, tuple(ids), None)``；缺失或同时出现 → 具名拒绝原因。绝不
    "两种都要"或"缺一个就猜另一个"。
    """
    present = [name for name in TREE_INSPECT_SELECTORS
               if arguments.get(name) not in (None, [])]
    if len(present) != 1:
        return "", (), (f"必须且只能提供 {list(TREE_INSPECT_SELECTORS)} 中的一个 selector，"
                        f"实际提供 {present}")
    name = present[0]
    ids = arguments[name]
    if not isinstance(ids, (list, tuple)) or not ids:
        return "", (), f"{name} 必须为非空数组"
    return name, tuple(ids), None


def _cursor_at(positions: tuple, index: int) -> dict | None:
    """把读集内第 `index` 个 span 位置写成 `span_cursor`（越界 → `None`）。

    位置序列按 node 顺序分段连续，因此「本节点内的第几个位置」= 下标减去本节点首个位置的
    下标；**只由位置序列本身推出**，不看任何结果字段，也不含公司/页码/答案词。
    """
    if index < 0 or index >= len(positions):
        return None
    node_id = positions[index][0]
    first = index
    while first > 0 and positions[first - 1][0] == node_id:
        first -= 1
    return {"node_id": node_id, "span_index": index - first}


def _load_envelope(payload_bytes) -> dict | None:
    if payload_bytes is None:
        return None
    return json.loads(payload_bytes.decode("utf-8"))


def _fail(error_code: str, message: str) -> C.ToolResult:
    return C.ToolResult(
        call_id="", tool_name=TREE_INSPECT_TOOL_NAME,
        tool_version=TREE_INSPECT_TOOL_VERSION, status="FATAL_ERROR",
        data={"document": None, "candidates": [], "gaps": [], "skipped": []},
        error_code=error_code, message=message)


# ---------------------------------------------------------------------------
# 注册（只向既有 registry 注册，不新建 registry）
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 源集会话（§L1）：N 份文档 = N 个有身份的会话，只做精确查表
# ---------------------------------------------------------------------------

class SourceSetSpanSessions:
    """一个 run 的**有序源集**会话集合（`§L1`）。

    每一份源集成员各有一个 `TreeInspectionSession`——粒度与 `TreeInspectionSession` 自己
    的语义一致（「一次 run 一份 live 源」，`§L1.2` 不改它）。构造期逐项 fail-closed：

    * `key` 唯一（同一 `SourceDocumentKey` 出现两次 → 拒绝）；
    * 每个 session 的 `document_identity()` 与它被登记的 key **逐字相同**（四轴全等）；
    * 全源集 `company_id` 一致；
    * 源集非空。

    `session_for(key)` **只做精确查表，没有任何回退**。「只有一份就默认用它」是明确禁止的
    （见 `§L3.3`）：回退会让「声明了 B、拿到了 A」变成静默错配，而
    `TreeInspectionSession.inspect` 的身份复核会把它判成 `SOURCE_UNTRUSTED`——读起来像
    「文档不对」，实际是「分派写错了」。
    """

    __slots__ = ("_sessions",)

    def __init__(self, sessions) -> None:
        from harness.source_manifest import SourceDocumentKey
        if not isinstance(sessions, dict) or not sessions:
            raise TreeToolError("SourceSetSpanSessions 需要非空的 "
                                "dict[SourceDocumentKey, TreeInspectionSession]")
        ordered: dict = {}
        company_ids: set[str] = set()
        for key, session in sessions.items():
            if not isinstance(key, SourceDocumentKey):
                raise TreeToolError(
                    f"源集会话的键必须是 SourceDocumentKey，得到 {type(key).__name__}")
            if not isinstance(session, TreeInspectionSession):
                raise TreeToolError(
                    f"源集会话的值必须是 TreeInspectionSession，得到 "
                    f"{type(session).__name__}（key={key.to_dict()}）")
            if key in ordered:
                raise TreeToolError(f"源集会话含重复文档键：{key.to_dict()}")
            actual = session.document_identity()
            declared = key.to_dict()
            # 四轴逐字比对：**不**做"主体对了就算"的宽松匹配，也不取多数票。
            if actual != declared:
                raise TreeToolError(
                    f"源集会话的登记键与它自己的 verified 身份不一致："
                    f"登记 {declared}，实际 {actual}")
            company_ids.add(key.company_id)
            ordered[key] = session
        if len(company_ids) != 1:
            raise TreeToolError(
                f"源集会话必须同属一个主体，得到 {sorted(company_ids)}"
                "（主体不一致 fail-closed，不取多数票）")
        self._sessions = ordered

    def __len__(self) -> int:
        return len(self._sessions)

    def keys(self) -> tuple:
        return tuple(self._sessions.keys())

    def sessions(self) -> tuple:
        return tuple(self._sessions.values())

    def session_for(self, key):
        """**精确查表，无回退**：源集里没有这份 key 就返回 `None`。"""
        return self._sessions.get(key)


#: 可以**归因**的树工具状态（`§L3.3b`）：只对**完成实际检查**的结果归因。
#:
#: `_fail` 路径是 `status="FATAL_ERROR"` 且 `data["document"] is None`，**不得**归因。
#: `EMPTY`（`error_code="RETRIEVAL_EMPTY"`）**是**一次完成过的检查，**必须**归因——它正是
#: 「要求检索但未命中」这一臂的候选来源。
ATTRIBUTABLE_TREE_STATUSES = ("SUCCESS", "PARTIAL", "EMPTY")


def attempted_source_documents(*traces) -> tuple:
    """从**真实运行轨迹对象**派生「实际被检查过的文档」（`§L3.3b`）。

    只读 `ToolResult.data["document"]`（四轴）+ `status`：状态不在
    `ATTRIBUTABLE_TREE_STATUSES` 内即跳过；`data` 里没有 `document` 的（非树工具）自然跳过
    ——**不得**给非树工具补造一个身份，也**不**按工具名猜测。

    返回有序去重的 `((SourceDocumentKey, status), ...)`（同一文档多状态取**首个**见到者，
    即按轨迹顺序的第一次可归因检查）。该身份取自运行轨迹对象，**不进**任何内容指纹
    （与 `trace_id`/`call_id` 同类）。
    """
    from harness.source_manifest import SourceDocumentKey
    out: list = []
    seen: set = set()
    for trace in traces:
        status = getattr(trace, "status", None)
        if status not in ATTRIBUTABLE_TREE_STATUSES:
            continue
        data = getattr(trace, "data", None)
        if not isinstance(data, dict):
            continue
        actual = data.get("document")
        if not isinstance(actual, dict):
            continue
        try:
            key = SourceDocumentKey(
                company_id=str(actual.get("company_id") or ""),
                document_id=str(actual.get("document_id") or ""),
                document_version=str(actual.get("document_version") or ""),
                evidence_set_version=str(actual.get("evidence_set_version") or ""),
            )
        except ValueError:
            # 身份不完整的结果**不得**被归因到任何文档：宁可少记一条，也不编造归属。
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append((key, status))
    return tuple(out)


def register_tree_inspection_set_tool(registry, *, sessions: SourceSetSpanSessions) -> None:
    """把 `inspect_outline_materials` 注册进**既有** `ToolRegistry`（源集版，`§L3.3`）。

    仍是**一个**工具名、**一个** registry、**一个** `run_id`：N 个会话共用同一条工具审计
    序列（`logs/tools/<run_id>/<call_id>.jsonl`），因此「分派到哪一份、被哪个 call_id 请求」
    在同一个地方可查。**不得**为每份文档各建一个 registry 或各起一个 `run_id`。

    executor 用参数里既有的四轴（线格式本来就带，见 `§L3.2`）构造 `SourceDocumentKey` 精确
    查表；取不到即 `SOURCE_NOT_BOUND`，**绝不回退到源集里的任何一份**。取到后**仍走**
    `inspect` 既有的「声明 ↔ 实际逐字复核」作为第二道门——两道门都要。
    """
    from harness.source_manifest import SourceDocumentKey

    def _executor(args: dict) -> C.ToolResult:
        try:
            key = SourceDocumentKey(
                company_id=str(args["company_id"]),
                document_id=str(args["document_id"]),
                document_version=str(args["document_version"]),
                evidence_set_version=str(args["evidence_set_version"]),
            )
        except (KeyError, ValueError) as exc:
            return _fail("SOURCE_NOT_BOUND",
                         f"调用参数里的四轴无法构成文档键：{exc}")
        session = sessions.session_for(key)
        if session is None:
            known = [k.to_dict() for k in sessions.keys()]
            return _fail(
                "SOURCE_NOT_BOUND",
                f"本 run 的源集里没有文档 {key.to_dict()} 的树会话；"
                f"源集成员共 {len(known)} 份：{known}。**不**回退到其中任何一份——"
                "回退会把「声明了 B、拿到了 A」变成静默错配")
        return session.inspect(args)

    registry.register(TREE_INSPECT_SPEC, _executor)


def register_tree_inspection_tool(registry, *, live_source) -> TreeInspectionSession:
    """把 `inspect_outline_materials` 注册进**既有** `ToolRegistry` 并返回 run-bound 会话。

    单元素的 `register_tree_inspection_set_tool` 薄封装（`§L3.4`）：签名与返回值不变，
    历史调用点不动。
    """
    session = TreeInspectionSession(live_source)
    register_tree_inspection_set_tool(
        registry,
        sessions=SourceSetSpanSessions({_session_key(session): session}))
    return session


def _session_key(session: TreeInspectionSession):
    """由会话自己的 verified 身份构造文档键（单文档封装用）。"""
    from harness.source_manifest import SourceDocumentKey
    identity = session.document_identity()
    return SourceDocumentKey(
        company_id=str(identity["company_id"]),
        document_id=str(identity["document_id"]),
        document_version=str(identity["document_version"]),
        evidence_set_version=str(identity["evidence_set_version"]),
    )


__all__ = [
    "TREE_INSPECT_TOOL_NAME",
    "TREE_INSPECT_TOOL_VERSION",
    "TREE_INSPECT_SELECTORS",
    "TREE_INSPECT_SPEC",
    "EVIDENCE_FALLBACK_GAP_REASONS",
    "DEFAULT_MAX_SPANS",
    "DEFAULT_MAX_CHARS_PER_SPAN",
    "MAX_MAX_SPANS",
    "MAX_MAX_CHARS_PER_SPAN",
    "ATTRIBUTABLE_TREE_STATUSES",
    "TreeToolError",
    "TreeInspectionSession",
    "SourceSetSpanSessions",
    "attempted_source_documents",
    "register_tree_inspection_tool",
    "register_tree_inspection_set_tool",
]

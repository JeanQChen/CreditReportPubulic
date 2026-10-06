"""树结构调整 TS1：文档结构层的**唯一**版本常量来源。

规则（`TREE_STRUCTURE_IMPLEMENTATION_PLAN.md` §1.1 + TS1 指令 + TS1 修正轮 P1-7）：

1. 本模块是 `document_structure/` 内**唯一**允许出现版本字面量的文件；
   其它模块一律 `from document_structure import versions as V` 引用，
   禁止把 `"pl-2"`、`"do-2"` 之类的字符串散落在 builder / schema / 测试里。
2. 版本变化必须改变对象身份：所有 id 派生都把这些常量作为哈希输入，
   因此"同一输入产生同一 id、版本变化即身份变化"由构造保证，而非人工承诺。
3. **不允许装饰性版本**：每个正式持久化/交换类型都必须有一个 `schema_version`
   字段携带它的 schema 版本，见 `VERSIONED_OBJECT_SCHEMA_FIELDS`；测试逐类型核对
   "常量已声明 ⇒ 字段存在 ⇒ 进入身份 ⇒ from_dict 校验"。
4. 本模块只声明常量，不含 I/O、不读数据库、不读 Contract、不调 LLM / 网络。

阈值状态（TS2 最终关闭轮起）：

- `ALIGN_MIN`：**已冻结为 `0.90`**。批准来源为**用户 + Codex**，批准日期 2026-09-17，
  依据 TS2 / TS2.1 的全量 coverage 归因与三态正交统计。它是**单一**文本对齐阈值
  （不设正文页 / 表格页两套阈值）；表格结构由未来的 `TableObject` 路径承接。
- `SPAN_CONFIDENCE_MIN`：低置信 span 的资格门槛，仍**未裁决**（`None`）。

在该门槛获批前 `set_complete` 资格恒为否；`is_citable()` 则只由 `verdict == "aligned"`
决定（即"达到 `ALIGN_MIN` 且不含 `unexplained` 残差"）。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# schema 版本（每个正式持久化/交换类型的 wire format 版本）
# ---------------------------------------------------------------------------
# TS1 修正轮改变了全部版本化类型的 wire format（新增 locator、schema_version、
# TableCell/cell_grid、unresolved reason_code 等），按指令"改变 wire format 必须
# 提升相应 schema version"，下表 5 项由 `-1` 升到 `-2`；新增的 3 项从 `-1` 起。
#
# TS1.1 收口轮再次改变 wire format / 派生身份的输入集合，按同一规则升版：
#
# - `LAYOUT_SCHEMA_VERSION` `pl-2 → pl-3`：`page_layout_locator` 的输入新增
#   company_id / document_id（身份范围闭合），全部 layout 定位身份取值改变。
# - `OUTLINE_SCHEMA_VERSION` `do-2 → do-3`：新增 `document_version` 字段，
#   `document_id` 进入 outline locator，新增 `reference_targets` 字段。
# - `SPAN_SCHEMA_VERSION` `os-2 → os-3`：`derive_span_id` 输入新增 `document_id`。
# - `TABLE_SCHEMA_VERSION` `to-2 → to-3`：`page_range` 改为单页 `page_number`，
#   新增 `document_outline_locator`，provenance 改为对象级闭合。
# - `REFERENCE_EDGE_SCHEMA_VERSION` `res-1 → res-2`：`occurrence_anchor` 被正式的
#   `ReferenceOccurrence` 取代，`to_ref` 允许为 None（未解析边不得声称目标身份），
#   occurrence 位置进入 edge locator。
#
# TS1.2 引用绑定信任边界定点修复再次改变 wire format / 派生身份的输入集合：
#
# - `OUTLINE_SCHEMA_VERSION` `do-3 → do-4`：**删除** `reference_targets` 持久化字段。
#   该字段是"调用方提供的目标字符串清单"，既不是文档内容也不是对象存在证明，
#   保留它会让"仅增加一个无任何边使用的目标"就改变 `content_fingerprint` /
#   `outline_id`（P1-1），也会让"同值回填的字符串清单"冒充目标对象存在（P1-2）。
#   它被运行时、不持久化的 `ReferenceValidationContext` 取代。
# - `REFERENCE_EDGE_SCHEMA_VERSION` `res-2 → res-3`：`edge_locator` 不再绑定 `to_ref`
#   （否则同一 occurrence 从 unresolved 变为 resolved 会改变定位身份，P1-3.7/3.10）；
#   `edge_id` 改为绑定 `from_ref` / `to_ref` / `is_resolved`；`is_resolved` 必须由
#   `resolution_evidence` 决定，且 resolved 只能在对象级验证入口中成立。
#
# TS1.3 引用 occurrence 坐标定点修复再次改变 wire format 的**语义**（字段名不变，
# 但同一字段的坐标含义变了），按同一条规则升版：
#
# - `REFERENCE_EDGE_SCHEMA_VERSION` `res-3 → res-4`：`ReferenceOccurrence` 的
#   `char_start` / `char_end` 由"来源对象归一文本里的**全局**偏移"改为
#   "`(page_number, line_index)` 定位到的**真实 `LayoutLine.text` 行内**偏移"。
#   字段名与形状没变、语义变了，正是"改变坐标语义必须升版"的情形：`res-3` 的
#   载荷若按新语义读取，同一个整数会被解释成完全不同的字符区间。
#
# 旧值全部登记在 `LEGACY_SCHEMA_VERSIONS`：只做显式识别与拒绝，绝不静默按新版本解释。

# TS3 §五 再次改变 wire format，按同一规则升版：
#
# - `ALIGN_SCHEMA_VERSION` `als-1 → als-2`：`TextAlignmentRecord` 新增
#   `matched_chars`（精确分子）字段，verdict 改由**精确整数比值**
#   `matched_chars / block_char_length` 推导，`coverage` 降级为**展示值**。
#   这是真实存在的 schema 冲突：`als-1` 只能表示"已量化"的覆盖率，因此
#   `exact < ALIGN_MIN <= round(exact, 3)` 的块在 `als-1` 里无法忠实表达
#   （展示 0.900 却按冻结规则不可引用）。升版后该冲突在记录层消失。
#   `als-1` 登记为 legacy：**只读兼容**（读入后仍按历史语义——量化 coverage——
#   重算 verdict），本次升版后的写路径只写 `als-2`（`als-2` 本身在下一轮
#   `als-2 → als-3` 里也登记为 legacy，见下条），绝不静默按新语义解释旧载荷。
#
# TS3 已知 P1 一次性机制收口轮（P1-E / P1-C）第三次改变 wire format 的**语义**：
#
# - `ALIGN_SCHEMA_VERSION` `als-2 → als-3`、`ALIGN_REFUSAL_SCHEMA_VERSION`
#   `alr-1 → alr-2`：`char_map` / `residue` 的**分区不变量**（两侧各自升序不重叠、
#   彼此不重叠、并集恰好覆盖 `[0, block_char_length)`）由私有 aligner 写路径的
#   单点检查，升格为**两个记录类型共用的公共校验器**，并且**在构造与反序列化路径
#   本身** fail-closed 执行。同一份载荷在 `als-2` 下可以被读出（只要它恰好自洽），
#   在 `als-3` 下必须**证明**自洽才允许存在；"反序列化即校验"改变了持久化语义，
#   因此必须升版。
# - `SPAN_SCHEMA_VERSION` `os-3 → os-4`：`OutlineSpan.unassigned_reason` 的封闭
#   词表由 1 个笼统值（`boundary_ambiguous`）细分为可执行的来源分类
#   （`insufficient_heading_evidence` / `numbered_list_ambiguity` /
#   `toc_unmatched` / `bookmark_unmatched` / `hierarchy_conflict`）。词表是
#   wire format 的一部分（校验按封闭集合拒绝非法值），扩充词表即改变格式语义。
# TS4 §18.12.3 第四次改变 wire format 的**接受集合**：
#
# - `SYNOPSIS_SCHEMA_VERSION` `nss-1 → nss-2`：`SYNOPSIS_REASON_CODES` 是 wire format
#   的一部分（`from_dict` 按封闭集合拒绝非法值），TS4 追加 `table_only_pending_ts5`
#   即改变格式语义。`nss-1` 登记为 legacy：只做显式识别与拒绝（需重算），**不**承认
#   它可以作为 current synopsis 被读回消费 —— 否则同一 wire 版本会有两套接受集合。
LAYOUT_SCHEMA_VERSION = "pl-3"
OUTLINE_SCHEMA_VERSION = "do-4"
SPAN_SCHEMA_VERSION = "os-4"
# TS5 §19.4.1（`to-3 → to-4`）：单页物理表格片段从"只有单行 text + 一个首 locator 的
# cell"升级为"携带完整 `source_fragments`、typed rows、mandatory `TableOwnerRef` 与
# upstream dependency 的单页对象"。**接受集合与字段集都变了**，因此是 wire 版本升级，
# 不是原位补字段。
#
# `to-3` 登记为 legacy：`document_structure.schema.TableObject` 作为**历史 reader** 保留
# （`to-3`/`tb-1` 由类内固定常量校验，不再读全局 current 常量），可被审计读回，但
# **不得**成为 current、不得被适配成 `to-4`、不得进入 final snapshot/capability。
# 读到 `to-3` 的 current 路径必须给出"已识别旧版、须重建"的专门错误，而不是
# "未知版本"或静默按 `to-4` 解释（`classify_schema_version` 保证这一区分）。
TABLE_SCHEMA_VERSION = "to-4"
# TS5 §19.0.1（方案 C）：`SYNOPSIS_SCHEMA_VERSION` / `SYNOPSIS_VERSION` **保持
# `nss-2` / `ns-2` 不动**，是 TS4 的**冻结版本轴**。
#
# TS5 首轮编码曾把它直接升为 `nss-3`，那会让 TS4 的 `NavigationSynopsis`、
# `SpanBuildSnapshot spn-1` 与 `SynopsisSourceValidation nsv-1` 随全局常量一起改变
# canonical 内容与 snapshot ID，使 TS4-B 已封存快照与 `test_tree_span_policy_b` B6 的
# 完整读回契约失效。这不是"TS4 需要历史兼容"，而是"TS5 不该改 TS4 的版本轴"：
# 该轴描述 `OutlineSpan → NavigationSynopsis` 这条 TS4 链，与 TS5 的 final 输出无关。
#
# TS5 的 final synopsis 是**另一个 public wire type**（`FinalNavigationSynopsis`），
# 走自己的版本轴 `FINAL_SYNOPSIS_SCHEMA_VERSION=nss-3` / `FINAL_SYNOPSIS_VERSION=ns-3`，
# 登记在 TS5 登记表里。二者不得 alias、不得继承式自动适配、不得由 aggregate 按 child
# 猜 reader、不得混装同一容器（§19.4.1 / §19.10）。
SYNOPSIS_SCHEMA_VERSION = "nss-2"
ALIGN_SCHEMA_VERSION = "als-3"
# TS3 §五：每个 Evidence block 必须且只能有**一个正式终态**。除 `als-3` 记录外的
# 另一条合法终态是"拒绝记录"——它不是"缺失记录"，而是一个 typed、版本化、
# fail-closed 的正式对象：失败也必须被表示，且必须能被逐条对账。
ALIGN_REFUSAL_SCHEMA_VERSION = "alr-2"
# M930-3 业务纵链补充门升版 `anps-2 → anps-3`：条目 wire **新增一个字段**
# （`AspectNavigationEntry.parent_keys`），因此旧载荷缺该字段、不得静默按"父键为空"解释
# （"父键为空"在新语义下是"该 aspect 没有可用的 Contract 祖先声明"这一**结论**，而旧载荷
# 从未表达过它）。`anps-2` 因此登记为 legacy：只显式识别并要求显式重算。
#
# r4 后定点返修升版 `anps-3 → anps-4`：条目 wire 再**新增一个字段**
# （`AspectNavigationEntry.subject_head`，该 aspect 的**主体标签** = `requirement_text`
# 里括号之前的头部；无括号时即 `requirement_text` 自身）。它与 `nav_keys` **不是**同一层：
# `nav_keys` 是"用哪些词去找标题"的**打分**键，`subject_head` 是"这个 aspect 讲的是哪件事"
# 的**主体**，只用于读根资格里的**主体邻接**判据（`NAV_ROOT_SUBJECT_RULE_ID`）。旧载荷
# （`anps-3`）没有这个读数，缺它与"主体标签恰为空"不可区分——而后者在新语义下会让
# 主体邻接判据静默失效（等于退回旧行为）。故 `anps-3` 同样登记为 legacy：只显式识别、
# 要求显式重算，绝不静默按 `anps-4` 解释。
PROFILE_SCHEMA_VERSION = "anps-4"
REFERENCE_EDGE_SCHEMA_VERSION = "res-4"

# TS4 §18.12.4：`OutlineSpan + NavigationSynopsis` 批新增的 **8 个顶层 wire 类型**
# 各自的 schema 版本。它们与上面 9 项**同等权威**，只是登记在各自的具名表里
# （`SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS`），并通过
# `ALL_VERSIONED_OBJECT_SCHEMA_FIELDS` 与既有 9 项组成**无重名并集**，由
# `self_check()` 与 `span_schema.self_check()` 共同验证"公共类型 ↔ schema 常量"严格双射。
#
# 为什么不在原表里追加：既有 9 项是 TS1 冻结的 wire 契约，其条数（9）已被冻结测试
# 直接断言；TS4 类型放在新模块、新登记表，二者都不改动旧表内容，同时**不得**被
# 当作 runtime-only 逃避登记。
OUTLINE_STRUCTURE_SNAPSHOT_SCHEMA_VERSION = "obs-1"
SPAN_QUALIFICATION_POLICY_SCHEMA_VERSION = "sqp-1"
BODY_RANGE_DISPOSITION_SCHEMA_VERSION = "sps-1"
SPAN_EVIDENCE_COMPONENT_SCHEMA_VERSION = "spc-1"
SPAN_CITABLE_COVERAGE_SCHEMA_VERSION = "spv-1"
SPAN_CONSERVATION_SCHEMA_VERSION = "spr-1"
SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION = "spn-1"
SYNOPSIS_SOURCE_VALIDATION_SCHEMA_VERSION = "nsv-1"

# ---------------------------------------------------------------------------
# 算法 / 引擎 / 规则版本（进入身份，但**不是** wire format 版本）
# ---------------------------------------------------------------------------

LAYOUT_ENGINE = "pymupdf"
LAYOUT_ENGINE_VERSION = "pymupdf-1"
NORMALIZATION_VERSION = "norm-1"
# TS3 已知 P1 收口轮升版 `oa-1 → oa-2`：**层级算法语义变了**，wire 字段没变。
#
# (a) 标题树的层级由"编号层级逐级夹紧"（`level > len(stack)` ⇒ 夹到 `len(stack)`）
#     改为**按编号层级的显式栈装配**：同一局部结构域内 `declared_level → 有效深度`
#     是稳定映射，同级兄弟不再因第一个标题被压缩而串成父子链。
# (b) `level_layout`（同级候选共享版式类）不再是**全文件**互相自证的主证据：
#     被真实版面折行信号（本页栏右边缘）判为正文行的候选，不得仅凭版式相似被采纳，
#     也不得向他人出借该信号。
# (c) `paragraph_boundary` 由"上一行文本任意位置出现句末标点"改为**真实行尾 /
#     真实版面分隔**。
#
# TS3 标题资格定点返修轮升版 `oa-2 → oa-3`：**标题资格的证据模型变了**，wire 字段
# 没有变。
#
# (d) 取消**全文件** `by_level` 候选互证：`level_layout` 不再出现在主证据词表里，
#     只在**辅助邻近窗口**（同页或相邻页）内作为辅证据；该窗口**不是**已验证的结构域
#     （旧名 `LOCAL_DOMAIN_PAGE_SPAN` 已按诚实命名删除）。一条候选的标题资格必须由
#     **候选自身**可复核的结构证据支撑。
# (e) 新增候选自身的主证据 `standalone_line`（本行自身是完整、未折行、未截断、
#     且不落在通用表格/网格区域内的标题行）与通用 `table_like_region` 资格判断。
#
# 四者都改变"同一份 PageLayout 能建出什么树"，而 `OUTLINE_ALGORITHM_VERSION` 是
# `derive_document_outline_locator` 的哈希输入，因此新旧算法下同一份版式必然得到
# 不同的 outline 身份：旧 `oa-1` 的 808 节点语义、`oa-2` 的全文件互证语义与新语义
# **不可能**共用同一版本号。二者登记为 legacy：只做显式识别与拒绝（需显式重算），
# 绝不静默按新语义解释。
OUTLINE_ALGORITHM_VERSION = "oa-3"
# 标题资格判据本身的版本（§二.2 要求"具体组合必须确定性、公司无关、版本化"）：
# 主 / 辅证据词表、`standalone_line` 的成立条件、邻近窗口语义。
# 换组合 ⇒ 换本版本号（结论必须能标明是被哪版资格判据得出的）。
#
# `hq-1 → hq-2`（表格边界收口轮 §三.1/§三.2）：表格内部的**强证据穿透**从"任意一条
# 强主证据无条件放行"收紧为"必须通过安全穿透资格"—— 已验证的 TOC / bookmark 正文
# landing，或 ≥2 项相互独立、可重算的版式强证据且**不是**被列宽截断 / 折行的单元格
# 片段。同时 `standalone_line` 只在**非表格成员**上成立。同一份 PageLayout 在 `hq-1`
# 下会采纳表格单元格里的编号承诺正文、在 `hq-2` 下不采纳，两者节点集不同，因此必须
# 分版本登记，绝不静默按新语义解释旧结论。
#
# `hq-2 → hq-3`（表格穿透收口轮 §二/§三）：**完整**标题资格语义再次变化 —— 穿透条件
# 从"来源佐证**或** ≥2 项版式强证据"收紧为"**只有**对象级验证过的来源 landing"。
# 两项 / 三项版式样式证据（`size_above_body` / `centered` / `bold_majority`）不再能
# 证明"这不是表格内容"：已被几何证明属于表格的候选仍可能是居中、放大或加粗的表头 /
# 强调单元格。同一份 PageLayout 在 `hq-2` 下会把这类表内候选采纳成节点、在 `hq-3` 下
# 把它们按 `table_region_candidate` 降级为正式未归属，节点集不同；`hq-2` 结论**不得**
# 按 `hq-3` 语义读取。虽然 `hq` 的值没有进入任何 wire 字段，但"同一份版式能建出什么
# 树"确实变了，因此**不**因 wire 未变而复用旧版本号。
#
# `hq-3 → hq-4`（Bookmark 信任边界 + TOC→正文 landing 收口轮 §二/§三）：资格语义的
# **两侧同时**变化，节点集因此不同。
#
# (a) **Bookmark 退出标题资格**：`hq-3` 仍把 `toc_or_bookmark` 当作强主证据，书签命中
#     即可参与资格算术、并可充当 `inside_table` 的安全穿透依据。但书签来自 PDF 大纲，
#     **不在** `PageLayout` 里：复核方只能重算"声明的页码 + 标题 ↔ 目标行"两条，书签
#     序号与来源身份无法由版式重建。因此 `hq-4` 只接受**能由真实版式重建身份**的
#     `toc_body_landing`（`TocSource` 对象链），书签降为**导航候选 / 审计信息**
#     （`bookmark_navigation_only`），不参与资格、不穿透。
# (b) **TOC→正文 landing 恢复为正式资格**：`hq-3` 里目录项只用于"表格穿透"与召回
#     守卫，正文 landing 行本身拿不到任何证据（无编号的正文 landing 一律因
#     `style_only_no_structure` 被拒）。`hq-4` 让**已验证的 TOC 对象链 landing** 成为
#     一条强主证据，并把目录项声明的深度作为该行的导航层级来源。
#
# 同一份 PageLayout 在 `hq-3` 下会把书签命中的行采纳成节点、把 TOC 指向的正文 landing
# 拒之门外，在 `hq-4` 下恰好相反；`hq-3` 结论**不得**按 `hq-4` 语义读取。
HEADING_QUALIFICATION_PROFILE_VERSION = "hq-4"
# 通用表格 / 网格区域资格判断的规则版本（§二.3）：只用 `PageLayout` 几何，不依赖
# 页码、表号、标题原文、evidence_id 或任何公司专有规则；不实现完整 TS5 `TableObject`。
#
# `trg-2`（表格边界收口轮 §三.1）：资格从**一层**`table_region_reason` 拆成**两层状态**
# `inside_table`（有成员资格证明：多字段水平行内的一格 / 已验证列锚点 + 同格字号的
# 行带成员 / 同一表格族闭合的 label band / 被列宽截断的单元格片段 / 上下由同一表格
# 对象的结构行闭合）与 `adjacent_to_table`（只是垂直邻近，无任何成员资格证明）。
# **邻近本身不再构成表格成员资格**；列锚点可达距离与"仅邻近距离"解耦。
#
# `trg-2 → trg-3`（表格穿透收口轮 §四）：**同一份 `PageLayout` 在本版本下产出的产物
# 已与 `trg-2` 不同** —— 判据 E（标签带）在同一 `trg-2` 版本号下经历过实现语义修正
# （同族多字段行之间夹着其它结构行时不再闭合成一个表格对象，否则整页正文都会被圈成
# 表格成员），期间产出过中间产物。因此 `trg-2` 不能继续代表当前判据：旧 `trg-2` 载荷
# **不得静默按 `trg-3` 解释**，必须显式拒绝 / 要求重算。
TABLE_REGION_QUALIFICATION_VERSION = "trg-3"
# `sb-1` **不得改变**：TS3 已用它产出 **232** 个正式 unassigned `OutlineSpan`
# （`FIXTURE_BOND_2026` 1 / `NDSD_2024_year` 97 / `NDSD_2025_year` 113 /
# `NDSD_KCZ_2026` 21），且 `outline_builder._unassigned_span` 依赖它。升全局常量会
# 令冻结树无法读回、无法 canonical 重建。TS4 正文改走独立常量（见下）。
SPAN_BUILDER_VERSION = "sb-1"
# TS4 正文 span 的算法版本（§18.12.2）：正文 run 切分、四域坐标投影、覆盖与组件
# 归属的规则与 TS3 的 unassigned 语义**不同**，因此**不得**复用同一个常量。
# 与 `sb-1` 的关系是"角色 / 写路径真值表"，不是"新旧替代"：`sb-1` 既不是失效 legacy，
# 也不是 TS4 正文可用值；`sb-7` 也不得用于重建历史 unassigned 对象。
#
# TS4-A 两个 P1 收口轮升版 `sb-2 → sb-3`：**同一份 `PageLayout` 在本版本下产出的
# 正文边界已与 `sb-2` 不同** —— 表格 provisional 邻接闭包（`table_leading_closure`）
# 会把已证明表格区域紧邻的表格前导行（表题 / 单位行）从 `regular` 正文移入
# `table_adjacency` provisional 处置，因而这些行不再成为 span、不再进入材料库与
# 简介、也不再计入可引用覆盖。
#
# TS4-A 表格前导残余污染收口轮再升版 `sb-3 → sb-4`：闭包的**停止条件与吸收条件**都
# 变了 —— 删除"与上一行同左边界 ⇒ 续行"的机械停止（同左边界不是正文的证据），改为
# "宽度达到声明栏宽上界的一定比例 ⇒ 满栏正文"这条纯几何负证明，并新增两处吸收依据
# （宽度装不下字宽的退化碎片；整行落在表格区域右侧且相对栏宽窄排的行）。因此同一份
# `PageLayout` 在本版本下被吸收的表格前导行集合与 `sb-3` 不同（更多真实前导行被移出
# 正式正文）。旧 `sb-2` / `sb-3` 载荷**不得静默按 `sb-4` 解释**，必须显式识别为
# legacy 并要求重算。
#
# 最后一次窄范围机制修复再升版 `sb-4 → sb-5`：闭包新增一条**折行续行**停止条件 ——
# 候选的源序上一条（非家具行）若是同节点、同左边界、且**自身对该区域没有任何锚定
# 证据**的 `regular` 行，则候选只是它的折行续行，必须停止。`sb-4` 删掉旧的同左边界
# 机械停止后，真实文档里有两条**窄的正文折行残段**（宽度很窄，恰好又与窄表格区域的
# 水平中心对齐）被"居中"这一条单一版式特征误吸收；`sb-5` 只切断这两类行，真实表格
# 前导（表题 / 单位行 / 表注）与 `sb-4` 的其余吸收集合逐行相同。因此同一份
# `PageLayout` 在本版本下的正文边界与 `sb-4` 不同，旧 `sb-2` / `sb-3` / `sb-4` 载荷
# **不得静默按 `sb-5` 解释**。
#
# 表后残余污染收口轮再升版 `sb-5 → sb-6`：闭包的**方向从单向变成双向** —— 原先只在
# 已证明表格区域的**前导**（向上）形成 provisional 邻接范围，现在同一组原语也从区域
# **尾部**（向下）形成受限的 provisional 邻接范围，把表格结束后、在严格结构边界内与
# 该表格紧邻的有限相邻行从 `regular` 正文移入 `table_adjacency` provisional 处置，
# 交给 TS5 的 `TableRangeDecision` 最终裁决（`absorbed_as_note` / `kept_as_paragraph` /
# `absorbed_into_body` / `unresolved_geometry`）。这些行因此不再成为 span、不再进入
# 材料库与简介、也不再计入可引用覆盖；`sb-5` 下它们仍是正式 `regular` 正文。两向共用
# 同一份版式基准与同一组几何原语，且尾部只处理**被结构边界封口**的段（内容形态停止
# 即整段不暂存），因此同一份 `PageLayout` 在本版本下的正文边界与 `sb-5` 不同，旧
# `sb-2` / `sb-3` / `sb-4` / `sb-5` 载荷**不得静默按 `sb-6` 解释**。
#
# 表后 provisional 入口收口轮再升版 `sb-6 → sb-7`：尾部闭包的**语义从"内容形态判定"
# 换成"物理邻接判定"**。`sb-6` 在向下走查时用一组内容形态负证明（字号大于正文基准
# 字号、满栏正文负证明成立、超过上限）决定"这不是残段"，并规定**命中任一即整段不
# 暂存**；结果是：一条满栏的表后表注（"说明：…"）会让它**前面**已经通过结构封口的
# 候选一起退回正式正文。`sb-7` 删除了这条"整段丢弃"语义，判据改为纯物理邻接 ——
# 区域含 `inside_table`、同节点、同物理页、与区域水平跨度交叠（= 同一正文栏）、与
# 上一行源序直接相邻、垂直间距不超过 `_TABLE_TRAILING_PITCH_FACTOR` × 由本页本栏
# 真实 `bbox` 确定性派生的**局部行距**；**任何**停止都只保留已经成立的 staged prefix。
# 满栏与不满栏在起始资格与续行判据上完全同权。
#
# 同一份 `PageLayout` 在 `sb-7` 下的 provisional 集合与 `sb-6` 不同（`sb-6` 会因满栏
# 表注而整段放弃、`sb-7` 不会；`sb-7` 还会因物理邻接而暂存 `sb-6` 从未看过的行），
# 且历史 `sb-6` 产物里有一部分行是**被 `sb-7` 判为不满足物理邻接的普通正文**。因此
# 旧 `sb-2` / `sb-3` / `sb-4` / `sb-5` / `sb-6` 载荷**不得静默按 `sb-7` 解释**，也
# 不得按 `sb-7` 重新解释历史产物。
# `sb-8`：跨页 `table_inside` 在**构造期按页分段**成逐页的单页 `BodyRangeDisposition`
# （每段 `start_page == end_page`），因此 `BodyRangeDisposition` 的**基数**与
# `disposition_id`（由 `create` 的内容身份派生，载荷含本常量）都与 `sb-7` 不同。同一份
# `PageLayout` 在 `sb-7` 下会给一条跨页表范围一条跨页处置，下游 §19.5.1 冻结范围通道
# 因"跨页无唯一物理矩形"而整段不可回查（`frozen_range_bbox` 返回 `None`），于是这些
# 字符既不建表也不进表相关文本域，只能落 `residual_gap`。`sb-8` **不**放宽
# `frozen_range_bbox` 的单页不变式：该函数对任何跨页范围仍返回 `None`。
TS4_BODY_SPAN_BUILDER_VERSION = "sb-8"
# 该常量的历史值：`sb-2` 是"表格前导行仍按普通正文成 span"的旧正文算法；`sb-3` 是
# "同左边界即停止闭包"的上一版闭包；`sb-4` 是"已删除同左边界机械停止、但还没挡住
# 窄折行续行"的上一版闭包；`sb-5` 是"只做前导（向上）闭包、没有任何尾部（向下）
# provisional 范围"的上一版闭包；`sb-6` 是"尾部按内容形态判定、命中即整段丢弃"的
# 上一版闭包；`sb-7` 是"跨页 `table_inside` 仍建成一条跨页处置、不按页分段"的上一版
# 处置构成。登记它们使读回路径能把"被替换掉的旧规则"与
# "打错的字符串"区分开（`classify_schema_version` 因此对 `sb-2` / `sb-3` / `sb-4` /
# `sb-5` / `sb-6` / `sb-7` 返回 `legacy` 而不是 `unknown`），同时**不移除**它们在
# `schema.SPAN_BUILDER_VERSIONS` 兼容读层里的合法取值 —— 历史 `sb-2` / `sb-3` /
# `sb-4` / `sb-5` / `sb-6` / `sb-7` 的 `OutlineSpan` 载荷必须仍可逐字节读回，但
# **不得**被 TS4 正文复核层当作当前产物接受。
TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS: tuple[str, ...] = (
    "sb-2", "sb-3", "sb-4", "sb-5", "sb-6", "sb-7")
# TS3 unassigned span 的显式写路径版本：`outline_builder._unassigned_span` 必须显式
# 传入它，而不是依赖"恰好还没变的默认值"。
OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION = "sb-1"
# 资格策略（`SpanQualificationPolicy`）的**算法**版本。它与 `sqp-1` wire schema 版本
# 分开命名、分别登记：同一份策略载荷在不同规则版本下可能得到不同的资格结论。
SPAN_QUALIFICATION_POLICY_VERSION = "sqpr-1"

# TS4 四个受信边界（provider / gateway / issuer）的规则版本（§18.3.5 / §18.12.4）。
# 它们全部进入 `VERSION_CONSTANTS` 与算法版本自检，不得只在业务代码里写字符串。
#
# 三个 issuer scope **严格隔离**，因此三套版本号互不相同：
# - live：正式生产链（`document_structure` 内公开入口唯一接受它）；
# - pinned：历史验收（只经不导出的 `_build_from_pinned_handoff`）；
# - fixture：版本化正向夹具（属 `pinned_acceptance`，但 `source_kind=versioned_fixture`）。
OUTLINE_STRUCTURE_PROVIDER_VERSION = "osp-1"
EVIDENCE_GATEWAY_PROVIDER_VERSION = "egp-1"
ALIGNMENT_TERMINAL_PROVIDER_VERSION = "atp-1"
QUALIFICATION_POLICY_PROVIDER_VERSION = "qpp-1"
VERIFIED_PAGE_LAYOUT_ISSUER_VERSION = "vpli-1"
VERIFIED_ALIGNMENT_ISSUER_VERSION = "vai-1"
VERIFIED_CURRENT_EVIDENCE_AUTHORITY_VERSION = "vea-1"
VERIFIED_TS3_HANDOFF_VERSION = "vth-1"
PINNED_PAGE_LAYOUT_ISSUER_VERSION = "vplip-1"
PINNED_ALIGNMENT_ISSUER_VERSION = "vaip-1"
PINNED_EVIDENCE_AUTHORITY_VERSION = "veap-1"
PINNED_TS3_HANDOFF_VERSION = "vthp-1"
FIXTURE_PAGE_LAYOUT_ISSUER_VERSION = "vplif-1"
FIXTURE_ALIGNMENT_ISSUER_VERSION = "vaif-1"
FIXTURE_EVIDENCE_AUTHORITY_VERSION = "veaf-1"
FIXTURE_TS3_HANDOFF_VERSION = "vthf-1"
TESTING_TS3_HANDOFF_VERSION = "vtht-1"
# TS4 复核层（`span_verifier`）的结论版本。它标记"这份 `VerifiedSpanSnapshot` 的
# 复核规则是哪一版"：复核是**独立重建 + canonical 全等**，因此复核规则的任何变化
# （重建哪些成员、比对哪些指纹、完成资格怎么判）都会改变同一份快照的复核结论。
# 它与 `sb-7`（产物算法版本）分开命名：产物没变但复核口径变时，只有这一个动。
#
# TS4-A 两个 P1 收口轮升版 `vss-1 → vss-2`：完成判据 `is_completion_eligible()`
# 由"A 恒假 + B 显式拒绝"改为**完整的 `threshold_enabled` 判定**。完成判据没有独立的
# 版本常量，而它的语义变化会改变同一份快照的复核结论，因此由本常量承载（这正是它
# 存在的理由：产物算法版本没动、复核口径动了）。历史 `vss-1` 的复核结论**不得**被
# 静默按 `vss-2` 解释 —— `VerifiedSpanSnapshot` 是运行时能力对象、不落盘，因此这里
# 只需保证"读到的版本与当前不一致即显式拒绝"。
VERIFIED_SPAN_SNAPSHOT_VERSION = "vss-2"
# TS3 正式 `unassigned` span 的 `evidence_set_version`。
#
# 目录 / 书签 / 正文标题的**未归属候选**没有 `EvidenceBlock`：它们的来源是真实
# `LayoutLine`（`layout_line_refs` + `start_anchor` / `end_anchor`），不是某个
# Evidence 集。`OutlineSpan.evidence_set_version` 要求非空字符串并进入 span 身份，
# 因此这里用一个**显式、版本化、保留**的值表示"不附属于任何 Evidence 集"，
# 而不是借用某个真实 set 版本号去冒充归属（伪造归属会让下游误以为该 span 已经被
# 正式对齐过）。该值同样进入 `derive_span_locator` → `span_id`，可被读回核验。
OUTLINE_UNASSIGNED_EVIDENCE_SET_VERSION = "no-evidence-set"
# TS3 §四：formal aligner 的输入必须是**版本化、typed、携带完整身份**的
# `EvidenceBlockInput`（company / document / version / set / evidence_id / 类型 /
# 页块定位 / 文本 / structured payload / content_hash）。升版即表示"输入契约变了"：
# 身份由 `evidence.ids` 的正式接口**重算**而不是采信调用方自报，因此同一份文本在
# 不同输入契约下不再可能被当成同一个块。
#
# TS3 已知 P1 收口轮升版 `ebi-1 → ebi-2`（P1-F）：单块输入的信任边界从"调用方
# 自报身份 + 自洽重算"升级为"必须与**权威 `EvidenceSetSnapshot`** 的对应成员
# **逐字段精确相等**"。同一份块文本在 `ebi-1` 下可以在任意进程里凭自洽字段被
# 接受，在 `ebi-2` 下必须能对上受信任只读 gateway 给出的全集快照。
EVIDENCE_BLOCK_INPUT_VERSION = "ebi-2"
# TS3 §八（P1-F）：正式**集合入口**的权威来源身份。`align_evidence_set` 原先只能
# 证明"调用者提交的列表内部一致"（少一个块、多一个自洽伪块、删中间块都发现不了）。
# 快照把 company / document / version / evidence_set_version / current 状态 /
# 按规范顺序排列的**全部**成员身份（evidence_id、content_hash、evidence_type、
# page_number、block_index）/ block_count / fingerprint / gateway 版本绑成一个
# 版本化 typed 对象；正式集合入口要求提交成员与快照**精确相等**。
EVIDENCE_SET_SNAPSHOT_VERSION = "ess-1"
# 权威 Evidence 只读 gateway 的规则版本（进快照身份：换 gateway 规则 ⇒ 换身份）。
EVIDENCE_SET_GATEWAY_VERSION = "esg-1"
# TS3 §七（P1-E）：`char_map` / `residue` 分区不变量的**公共校验器**版本。
# 它进入 alignment 记录的派生身份，使"某个终态是被哪版分区规则验证过的"可审计：
# 同一份载荷在 `apv-1` 与后续版本下可能得到不同的接受/拒绝结论。
ALIGNMENT_PARTITION_VALIDATOR_VERSION = "apv-1"
# TS5 §19.4.1（`tb-2 → tb-3`）：to-4 表格的构造规则版本。它绑定候选并集与去重顺序、
# 统一结构验证门、row/column/header 判定、cell 内部 blocks 组装、caption/unit/note
# 归属与 continuation 判定。
#
# `tb-2 → tb-3` 的唯一改动是**统一结构验证门**多了一条有界裁定：当候选自带网格骨架、
# 且**恰好包含**其唯一冻结 `table_inside` 范围时，允许放宽"单范围交占比 ≥ 0.95"
# 这一条（`CANDIDATE_ADMISSION_BASES` 的 `contained_frozen_owner`）。形状门、其余
# 拒绝码与守恒口径**不变**。同一份 `PageLayout` + 冻结 TS4 根在 `tb-2` 与 `tb-3`
# 下**可能得到不同的表集**，因此 `tb-2` 按既有政策登记为 legacy。
#
# `tb-3 → tb-4` 的唯一改动是**冻结侧候选人口**多了一条有界形态：同一页 `table_inside`
# / `table_adjacency` 范围中满足"**连续相邻**"（真实矩形垂直间隙 <=
# `UNION_ADJACENCY_GAP_PT`、横向有重叠、同属一个已验证标题节点、并集不碰同页冻结
# `regular` 正文）的一串范围，除逐范围各出一个候选外，**另出一个并集候选**（矩形 =
# 该串真实矩形的并集，来源通道仍是 `frozen_range`，无网格骨架）。准入依据码
# `contiguous_frozen_union`。形状门、其余拒绝码、逐范围候选与守恒口径**不变**：并集
# 候选若网格不闭合，就与逐范围候选一样被如实拒绝。
# 同一份 `PageLayout` + 冻结 TS4 根在 `tb-3` 与 `tb-4` 下**可能得到不同的表集**，
# 因此 `tb-3` 按既有政策登记为 legacy。
#
# `tb-1` 也是 legacy（history-only）：它只描述
# `document_structure.schema.TableObject` 那套单页物理片段构造，**不得**被静默当作
# 后续版本解释，也不得用来给 to-4 对象背书。
# `tb-5`：`sb-8` 让跨页 `table_inside` 变成逐页单页处置后，§19.5.1 的冻结范围通道
# **首次看得到**这些范围（`tb-4` 的输入集里它们整条不存在），于是 `_frozen_runs` 的串
# 聚合、逐范围候选与 `tb-4` 并集候选都可能多出成员。变化只能经由**输入**表现，但
# "同一份 `PageLayout` + 冻结 TS4 根在 `tb-4` 与 `tb-5` 下得到不同表集"这一点成立，
# 故按既有政策升版。
#
# `tb-5 → tb-6`：两处**冻结侧**改动同时收口，都让"同一份输入可能得到不同的表集"：
# （1）`_union_extends` 判据③由"两端 `node_id` 相等（`None == None` 也算相等）"收紧为
# "两端都**非 `None`** 且相等"。`None` 只表示"该范围未归属到任何已验证标题节点"，两个
# `None` 之间**没有任何同节点证据**；旧口径据此把同页**相邻的两张不同表**并成一串、
# 并集候选因此横跨两张表。收紧后这样的串在缺口处分段，每张表各自成候选。
# （2）`CANDIDATE_ADMISSION_BASES` 新增 `contained_frozen_run`：当候选自带网格骨架、
# 且同一页 `table_inside` / `table_adjacency` 范围中恰有一串**连续**范围（≥2 段）每段
# 自身面积的 ≥ `CANDIDATE_CONTAINED_RATIO` 都落在候选内、其余同页范围与候选**零重叠**
# （有范围只落进来一半 ⇒ 拒绝，实现 §0.19"不得截取一半"）、且该集合恰为 `_frozen_runs`
# 某一条 run 的连续子序列时，允许放宽"单范围交占比 ≥ 0.95"这一条。形状门、其余拒绝码
# 与守恒口径**不变**。于是整张物理表（表题、完整物理表头、期间列、合计行、分块行与
# 主体行同在一条候选里）不再被切成半张；被否的候选仍按原样拒绝。
# 两者都只看真实几何与已验证节点，与文档、公司、语言、页码、表号无关。同一份
# `PageLayout` + 冻结 TS4 根在 `tb-5` 与 `tb-6` 下**可能得到不同的表集**，故按既有政策
# 升版，`tb-5` 登记为 legacy。
TABLE_BUILDER_VERSION = "tb-6"
# TS4 §18.12.3：`ns-1 → ns-2`。片段选取规则（`MIN_SNIPPET_CHARS` 硬下界、句末 token
# 长度降序匹配、闭引号收录）、reason 判定函数与 `navigation_admissible` 口径都由本版
# 定义；`ns-1` 登记入 legacy，只用于给出"须显式重算"的确定性错误，**不**作为 current
# 读取（同一份载荷在 `ns-1` 下会缺少 `table_only_pending_ts5` 分支）。
#
# TS4-A 两个 P1 收口轮、表格前导残余污染收口轮与表后残余污染收口轮都**不**再升本常量：
# 这几轮的改动都是
# **正文边界**（`sb-2 → sb-3`、`sb-3 → sb-4`、`sb-4 → sb-5` 的表格前导闭包，
# `sb-5 → sb-6` 把同一套闭包延伸到区域尾部的表格邻接闭包，`sb-6 → sb-7` 把尾部闭包
# 的判据从内容形态换成物理邻接），简介的选片规则、reason
# 判定与 `navigation_admissible` 口径一个字都没改。变化只能经由**输入**表现：被闭包
# 改桶的行不再是 span，也就不再出现在
# `sources` / `coverage` 里，于是简介集合随之变化并被 `SpanBuildSnapshot` 的
# content_fingerprint 完整承载（同版本、不同输入 ⇒ 不同指纹，可审计、可比对）。
# 因此这里保留 `ns-2`，而不是制造一个"语义未变却换了版本号"的假身份。
#
# TS5 §19.0.1（方案 C）：`ns-2` **保持为 TS4 的 current 算法版本**，不随 TS5 升版。
# TS5 的 final synopsis 换了来源口径（只从 final 可引用 paragraph span 抽取）并新增
# 不可用原因 `table_material_available_no_text_synopsis`——但那是**另一个类型**的算法，
# 登记为 `FINAL_SYNOPSIS_VERSION=ns-3`。让 TS4 也换号等于把"TS5 改了 final"错记成
# "TS4 改了语义"，并会让已经冻结的 TS4 快照身份漂移。
SYNOPSIS_VERSION = "ns-2"
# TS2 最终关闭轮升版 `al-1 → al-2`：**对齐规则语义变了**，不是 wire 字段变了。
#
# (a) 生产判定语义改为批准规则（`schema.compute_alignment_verdict`）：
#     `coverage < ALIGN_MIN` ⇒ `unaligned`（`al-1` 下是 `partially_aligned`）；
#     `coverage >= ALIGN_MIN 且含 unexplained` ⇒ `partially_aligned`；
#     `coverage >= ALIGN_MIN 且无 unexplained` ⇒ `aligned`。
# (b) `ALIGN_MIN` 由"未裁决"变为冻结的 `0.90`，因此同一 coverage 在不同
#     aligner 版本下可能得到**不同的 verdict**。
#
# 两者都改变 alignment 的身份：`derive_alignment_locator` 把 `aligner_version`
# 作为哈希输入，`alignment_locator` 再进入 `alignment_id`，因此 `al-1` 与 `al-2`
# 的同一 `(page_layout_id, evidence_set_version, page_number, block_index)` 必然
# 得到不同的 alignment 身份。旧 `al-1` 载荷因此**不可能**被静默按 `al-2` 解释：
# `_check_version` 对不等于当前值的 `aligner_version` 一律 fail-closed。
#
# TS3 已知 P1 收口轮升版 `al-2 → al-3`（P1-E / P1-F）：**输入信任边界与终态
# 校验边界都变了**。
#
# (a) 单块的 `char_map` / `residue` 分区由私有写路径检查改为公共校验器，并在
#     记录类型的**构造与反序列化**路径本身执行（`apv-1`）；
# (b) 集合入口必须绑定权威 `EvidenceSetSnapshot`（`ess-1`）并要求提交成员与快照
#     精确相等；`al-2` 只能证明"提交列表内部一致"，一个 292 块的集合只提交 1 块
#     在 `al-2` 下也能通过。
#
# 同一份输入在 `al-2` 与 `al-3` 下可能得到不同的可接受结论，因此结论必须标明是
# 哪版 aligner 得出的。`al-2` 登记为 legacy：只做显式识别与拒绝（需显式重算）。
ALIGNER_VERSION = "al-3"
# M930-3 任务一升版 `anp-1 → anp-2`：导航规则本身变了，同一份 Contract + 同一棵标题树
# 在两版下会得出不同的候选与读集，因此结论必须标明规则版本。
#
# (a) **并列子形态**（`navsubform-declared-label-conjunction`）：Contract 声明的复合
#     字段名（"产品与方案"）在文档里只写了一半时，整体标签在树上不存在，`anp-1` 的
#     打分分母因此为 0、该 aspect 连候选都产生不了；`anp-2` 允许在整体缺席时退回
#     子形态参与匹配（子形态必须真的出现在树上），而**分母单位仍是声明键**。
# (b) **近分带**（`tieband-one-synopsis-unit`）取代 `anp-1` 的"选定子树按比例过滤
#     后代"：同分或仅差一个最弱导航信号（简介命中）的候选必须一起进入**有界读集**，
#     不得由文档先后静默决定唯一子树；被 node 预算截断的范围如实记为未读。
# (c) **读根上提**（`readroot-topmost-matched-ancestor`）取代同一次改动里的"下潜"：
#     路径轴会给"祖先标题命中"加分，所以命中父章节的**子节点**得分天然更高；只读
#     得分最高的叶子，会把明明也命中、且挂满正文的父章节整段丢掉。读根改为从近分带
#     根沿祖先链上提到**最上层命中祖先**（上级不在候选里即止），读集由读根子树展开，
#     仍受同一 node 预算约束；`read_root_node_ids` 与 `band_node_ids` 都进决策，供逐层
#     对账。三项合起来才是 `anp-2` 的完整语义。
#
# M930-3 业务纵链补充门升版 `anp-2 → anp-3`：**子项没有独立标题时沿 Contract 层级找父节点**。
# 真实年报里「采购模式 / 生产模式 / 销售模式 / 技术路线 / 成本结构 / 成本竞争能力」这类子项
# 声明字段**不构成任何节点的标题**（`anp-2` 下 6 个子项里 5 个连候选都产生不了），而同一份
# 业务正文挂在父节点（例如"经营模式"那一节）上；同时 `anp-2` 的子串包含匹配会把**长句披露
# 标题里夹带的同词**当成命中（"占公司营业收入或营业利润10%以上的行业、产品、地区、销售模式
# 的情况"整句含"销售模式"，于是 `sales_mode` 的读根落在了一个只写「适用 □不适用」+ 披露要求
# 的模板节点上）。两项合起来是同一份 Contract + 同一棵树在 `anp-3` 下得到**不同候选与读集**：
#
# (a) **完整标签段**（`navsegment-complete-label-segment`）：一个声明键只在本树标题的某个
#     **完整标签段**上与标题相等时才算"命中该标题"。标签段由标题的**原始**文本按枚举符 /
#     并列连词切出（并剥掉编号前缀），因此"整句里夹带该词"只算**片段命中**——它在候选里
#     留痕（`fragment_title_only`），但不能作读根。不做中文分词，不引入任何具体词。
# (b) **父节点回退**（`navparent-contract-ancestor-label`）：当该 aspect 在本树**没有任何
#     完整标签段命中**（没有独立标题，或只有同词片段命中）且 Contract 的**祖先声明**
#     （topic 标题 / question 文本的枚举项）给出了键时，改用祖先键在同一套候选 / 近分带 /
#     读根上提机制上重跑，读到的就是那个**真实父节点的有界正文**。祖先键仍只来自冻结
#     Contract，`company_id` 不参与；命中不了就退回 `anp-2` 行为（不猜）。
#
# 二者都不改变覆盖判定：读到父节点正文只证明材料到达。
#
# M930-3 C1 定点返修升版 `anp-3 → anp-4`：**祖先层键的来源被收窄到 question 归属**。
# 真实 Contract 里 topic 标题（"主营业务、经营模式、产业链、收入成本毛利构成、客户与供应商
# 集中度"）本身就是该 topic 名下**多个 question 的并列概括**，`anp-3` 把它的每一段整段交给
# 该 topic 的**每个** aspect，于是 `收入构成 / 毛利率 / 产品与方案 / 应用场景 / 产业链位置`
# 这些子项都凭共享的「经营模式」父节点读到同一段经营模式正文——同一份文档、同一份 Contract
# 在 `anp-4` 下候选、读根与读集因此不同（真三份文档已复现：`收入成本毛利构成` 与
# `客户与供应商集中度` 尤其明显）。改动只有一处：
#
# (a) **topic 标题段归属**（`navtopic-segment-question-ownership`）：topic 标题的每个完整
#     标签段先按**分层多信号**判据（整段相等 → 互为子串 → 与 question 文本的最长公共子串
#     ≥2 字 → 与各 aspect `required_fields` 的同类公共子串）归属到**唯一一个** question；
#     多个 question 在同一最强层争用、或全无信号时**不强选**，该段被丢弃并留下可审计原因
#     （`topic-segment-contended` / `topic-segment-no-signal`）。归属只读冻结 Contract 的
#     topic 标题 / question 文本 / aspect 声明字段，公司、页码、文档标题、答案词都不参与。
#     `NAV_PARENT_RULE_ID` 的机制本身（完整标签段命中、近分带、读根上提、失败即退回本层）
#     一个字未改，变的只是"祖先层**有哪些**键"。
#
# r4 后定点返修升版 `anp-4 → anp-5`：**读根资格**（而不是打分）再加两条纯字符串判据。
# 两份真实文档（`NDSD_2024_year` / `NDSD_2025_year` / `NDSD_KCZ_2026`）在 `anp-4` 下已复现
# 三类逐栏目误召，全部是"**声明键的片段命中了无关标题**"，而 `anp-4` 只把完整标签段判据
# 施加在祖先层，本层的读根仍可由**子串**命中产生：
#
# (a) **贴标签**（`navroot-label-anchored`）：一个候选要作读根，至少得有一个键的**匹配形态**
#     落在它（或它某个祖先）标题的某个**完整短标签段**上——形态等于该标签段，或是它的
#     前缀 / 后缀（短标签段 = 长度 ≤ `_LABEL_MAX_CHARS` 的标签段；整段相等时长度不限）。
#     于是 `产品与方案` 的子形态 `产品` 命中 `主要产品及其用途`（`产品` 是短标签段
#     `主要产品` 的后缀）仍成立，而命中 `衍生产品情况` / `金融衍生产品名称`（`产品` 只在
#     长标签**内部**）不成立；`报告期` 命中 `报告期内的内部控制制度建设及实施情况`
#     （`报告期` 只在长标签内部）同样不成立。
# (b) **主体邻接**（`navroot-subject-adjacency`）：候选的标题 / 路径文本必须与该 aspect 的
#     **主体标签**（`subject_head`）共享长度 ≥2 的最长公共子串。于是 `客户当前集中度`
#     不会因为 `关联方` 这一 `required_fields` 项整段命中 `十三、关联方及关联交易` 而把
#     关联方章节读成"客户集中度"的材料；而 `主营业务构成（分板块/分部）` 与 `收入与成本`
#     共享 `收入`，`2、收入与成本` 仍作读根。（`anp-7` 起：**读根不变**，但同一 aspect 的
#     **读集**按主体补读另加「1、主要业务」那类真正的主体正文——见下方 `anp-7` 升版说明；
#     这一条判据本身在 `anp-7` 下逐字未改。）两条判据都不含公司名、页码、表号或答案词，
#     也不做中文分词；判据只决定"哪几个候选可作读根"，**不**改打分、**不**改近分带宽度，
#     因此 `anp-4` 下被判"命中"的候选在 `anp-5` 下仍逐条出现在候选审计里（只是多一条
#     舍弃原因 `not_label_anchored`）。整层没有任何候选可用时终态是**新增的**
#     `no_anchored_read_root` fallback（读集为空），不再退化成"读了无关章节"。
# r5 后业务内容收口升版 `anp-5 → anp-6`：**祖先层键的归属再下沉一级到 aspect**。
# `anp-4` 已把 topic 标题段的归属下沉到 question 一级，但 Contract 的 **question 文本本身**
# 同样是它名下各 aspect 子项的**并列枚举**（真实 Contract：
# `客户当前集中度、前五大合计占比、关联方、集中度跨期变化、披露范围` 是三个 aspect 的字段
# 拼在一起），`anp-5` 仍把每个枚举项整段交给该 question 的**每个** aspect。于是
# `客户集中度跨期变化` 凭**兄弟字段** `关联方` 拿到祖先键 `关联方`，命中
# `十三、关联方及关联交易` 并把它读成自己的材料——`anp-5` 的读根主体邻接判据只施加在
# **本层**读根上（祖先键按定义被视为该 aspect 的祖先声明，故不重复判定），这条误召因此
# 在 `anp-5` 下照旧发生。真实 r5 产物逐条复现：`customer_concentration_change` /
# `customer_anonymity` 的读根是该章（13 个 node 全进读集），产出的 8 条关联方材料挂到了
# 客户集中度两栏上。改动只有一处：**兄弟项排除**
# （`navparent-sibling-item-exclusion`）——一个候选祖先键若与**同 question 内某个兄弟
# aspect** 的 `required_fields` / `requirement_text` 派生键**规范化后整键相等**，就不作
# 本 aspect 的父节点键；判据不做子串、不做中文分词，输入只有冻结 Contract 的声明字段。
# 打分、近分带宽度、读根上提、失败即退回本层、完整标签段判据一个字未改，变的只是"祖先层
# **有哪些**键"；同一份 Contract + 同一棵树在两版下的祖先键集合、读根与读集因此不同。
# M930-3 业务返修补读升版 `anp-6 → anp-7`：新增**主体补读**
# （`navsupp-nearest-ancestor-subject-body`），处理"本层定位**有缺陷**"的第三种形态——
# 不是零候选、不是低置信，而是**选中了、却选错了栏目那一节**（前两种形态在 `anp-6` 下
# 已经是显式 fallback，因此可修、可审计）。缺陷判据只有一条：**读集里没有任何一节贴上
# 祖先声明键**（`navroot-label-anchored` 对祖先键逐节点为空）。真实反例固定在两份年报的
# `main_business` 上：本层键 `业务板块/收入/收入占比` 把它稳定送到
# `四、主营业务分析 / 2、收入与成本`，而「报告期内公司从事的主要业务 / 1、主要业务」
# （2025 年 441 字、2024 年 348 字自有正文）一个字都没进读集——`anp-6` 的注释把"`2、收入与成本`
# 仍作读根"写成本层判据的**预期**形态，那一层判据确实照旧成立，缺的不是读根，是**读集漏了
# 主体节**。补读的候选判据与缺陷判据**不是同一条**，且比它严格得多：一个未读节点要成为
# 补读根，必须同时（a）是某个祖先键的**按序近似形态**（首字符与末字符都对得上，按序出现
# 的公共字符数 ≥ `max(2, ⌈len(键)/2⌉)`，且键长 ≥3 时内部字符至少对上 1 个）、（b）标题是
# **字段名形态**（规范化后 ≤ `_LABEL_MAX_CHARS`）、（c）**自带实质正文**
# ≥ `_SUPPLEMENT_MIN_BODY_CHARS` 且非显式跨引用来源。三条缺一不可，
# 因此"几十个近似标题"进不来：(a) 挡掉 `25、合同成本`（对 `收入成本毛利构成` 连首字都对不上）
# 与 `1、报告期内利润分配政策…`（对 `报告期与口径` 末字对不上——只数公共字的话这一个键会一次
# 拉起三十余节无关章节）；"内部字至少对上 1 个"这一层挡掉**只共享首末两字**的四字词——
# 募集说明书上 `主营业务` 与 `（二）主动债务管理` 首字同 `主`、末字同 `务`，只看首末会把一节
# **债务管理**披露读成主营业务的材料，正是"相邻但无关的披露冒充主营业务材料"；
# (b) 挡掉句子型长标题（句子里凑出几个字不构成"这节是关于那个键的"）；
# (c) 挡掉空标题，也把**父章节本身**挡在外面（`四、主营业务分析` 与
# `一、报告期内公司从事的主要业务` 都**没有**自有正文，正文在它们的子节上）。合格者排序后
# 取前 `_SUPPLEMENT_MAX_ROOTS` 个，**排在**本层读根之后展开，因此预算截断先满足本层；补读根
# 与"上提出来的读根"分开记账（`supplement_root_node_ids`），逐条记进候选审计，并另带
# `supplement_rule_status` 说明补读**为什么**没做成——`no_candidate`（判了但没有合格节）、
# `body_measure_unavailable`（调用方没给逐节点自有正文量，判不了，**不猜**）与 `over_budget`
# （有合格根但预算没让它进读集）三者对下游含义完全不同，不得互相顶替。判据输入只有冻结
# Contract 的祖先声明 + 树上的**标题**与**自有正文量**（计数，不是文本，索引仍拿不到任何证据
# 内容），不含公司名、页码、表号或答案词，也不做中文分词；打分、近分带宽度、读根上提、
# 兄弟项排除、失败即退回本层全部一字未改——`anp-6` 下已 `selected` 的 aspect 在 `anp-7` 下
# `selected_node_id` / `band_node_ids` / `read_root_node_ids` 的前缀完全相同，读集只做加法。
# M930-3 业务返修第二轮升版 `anp-7 → anp-8`：新增**并列连词兜底**
# （`navparent-conjunction-fallback`）。`anp-4` 起的祖先层键是按 `_ENUM_SPLIT`（枚举分隔符
# `、，,；;／/|或`）从祖先声明文本里切出来的；切法切不开**并列连词**（`以及 / 与 / 及 / 和 /
# 或`）。真实 Contract 的 `company_supplier_concentration` 两条 aspect 的祖先声明恰好是
# `供应商当前集中度与集中度跨期变化`——一条被 `与` 连起来的复合标签，枚举层一个键都切不出，
# 于是 `parent_keys` 为空。而**空 `parent_keys` 同时关掉两道门**：祖先层回退
# （`navparent-contract-ancestor-label`）以 `if entry.parent_keys and ...` 为前提，主体补读
# （`anp-7` 的 `navsupp-nearest-ancestor-subject-body`）以 `if not keys: return
# "no_ancestor_keys"` 为前提。两份年报上该 aspect 因此停在 `no_anchored_read_root`
# （`供应商当前集中度`）／`low_confidence`（`集中度跨期变化`），而**兄弟** `customer_*`
# 两条在同两棵树上有非空 `parent_keys`——差异只来自"祖先声明用没用连词"，与公司、文档、
# 页码、答案词无关。改法只有一处：枚举层**零键、且整条声明里没有枚举分隔符**时，按
# `_LABEL_JOIN`（标题分段 `anp-1` 起就在用的那条连词规则）再切一层。"没有枚举分隔符"这道
# 门是实测出来的：声明里带 `、` 是文档在用**枚举清单**写法，清单项由枚举层负责，末尾那种
# `……对利润和资产质量的影响`式**从句尾巴**不是并列标签。真实反例 `fin_asset_quality` 的三条
# aspect 祖先声明带 `、`，放开这道门就会凭空造出 `开发支出对利润` / `资产质量的影响` 这种从句
# 片段键（既不是任何树节点的完整标签段，又把 `anp-7` 主体补读的门推开）。收门后全 Contract
# 落到兜底层的**只剩** `company_supplier_concentration` 两条，其余 185 条（含上述 fin 三条）
# 的 `nav_keys` / `parent_keys` / 读根 / 读集逐字不变；打分权重、近分带宽度、读根上提、
# 读根资格两条判据、兄弟项排除、失败即退回本层一字未改。同一份 Contract + 同一棵树在两版下
# 祖先层键集合不同，故按版本轴显式升版、`anp-7` 进 legacy。
# M930-3 业务内容质量收口升版 `anp-8 → anp-9`：新增**上提补读**这条独立规则
# （`navsupp-lift-ancestor-subject-section`）。`anp-7` 的主体补读要求补读根**自带**准入
# 正文 ≥ 80 字符，这条有意把"正文挂在子节上"的父章节挡在外面——真实反例是两份年报的
# `四、主营业务分析`：自有正文 8 个字符（一个模板复选框），`（1）动力业务` 1276 字 /
# `（2）储能业务` 844 字 / `（3）新兴领域` 1436 字 / `（4）供应链及产能` 317 字全在它的子树里。
# 那些子节标题（`动力业务`…）对不上任何祖先键的按序近似形态（`主营业务` 对 `动力业务`
# 连首字都对不上），于是整棵子树一个字都进不了读集——本层键 `收入` 把 `main_business`
# 送到 `四、主营业务分析 / 2、收入与成本` 之后，"再往上看一层"这条路在 `anp-8` 下是关着的；
# 本层零候选的 `app_scenarios` / `revenue_breakdown` 更只有 `1、主要业务` 那 441 字。
#
# 新规则与 `anp-7` **分账**，不共用字段：候选须同时满足 (a) **落在已读节点的祖先链上**
# （`_lift_targets`；读基为空时不产生候选，因此 `anp-8` 下 fallback 的 aspect 在 `anp-9` 下
# 仍 fallback，逐字不变）、(b) 标题**整段贴上**某个祖先键（与读根贴标签同一条
# `navroot-label-anchored` 判据与短标题口径）、(c) **有界子树**（≤ 24 节点）准入正文
# ≥ 80 字符（新量 `subtree_body_chars`，与 `anp-7` 用的自有正文量是两个量，不得互相顶替）、
# (d) 非显式跨引用来源；排序只看文档顺序。结果落在 `NavigationDecision.lift_root_node_ids` /
# `lift_rule_status`（`anp-7` 仍用 `supplement_root_node_ids` / `supplement_rule_status`），
# 候选审计逐行写本规则自己的 id——两条规则的合格条件不同（本规则**不要求**根自带正文），
# 合成一个字段会让 `anp-7` 的"补读根都自带实质正文"这条保证悄悄失效。
# 打分权重、近分带宽度、读根上提、读根资格两条判据、兄弟项排除、祖先层回退、`anp-7` 的
# 候选判据与状态 全部一字未改；变更只经由"多了一条只做加法的补读规则"表现：同一份 Contract +
# 同一棵树，`anp-8` 下的读出结果（`selected` / `band` / 读根前缀 / `read_node_ids` 前缀）
# 逐位不变，读集只做加法。故按版本轴显式升版、`anp-8` 进 legacy。
PROFILE_RULE_VERSION = "anp-9"
REFERENCE_EDGE_VERSION = "re-1"
# 引用核验器版本：`ReferenceValidationContext` 与 `VerifiedReferences` 携带它，
# 使"某个 resolved 结论是被哪个版本的核验规则、在哪组真实对象上得出的"可审计。
#
# TS1.3 升版 `rr-1 → rr-2`：核验规则本身变了——occurrence 的坐标语义由"来源对象
# 归一文本全局偏移"改为"真实 `LayoutLine` 行内偏移"，并新增 `toc_to_body` 的对象级
# 可达路径与页码映射规则。同一个 context 在 `rr-1` 与 `rr-2` 下可能得到不同的
# verified 集合，因此结论必须标明是哪个规则版本得出的。
#
# TS1.4 升版 `rr-2 → rr-3`：**信任边界变了**，而不是 wire 字段或坐标语义变了——
# (a) 未解析边（`is_resolved=False`）的来源 occurrence 也必须落到真实 `LayoutLine`
#     上核验（`rr-2` 直接登记 unresolved，`page=999 / line=999` 之类虚假来源可进入
#     结构树）；(b) `toc_to_body` 的目标节点必须通过对象级锚点核验（锚点真实行、
#     bbox 落在行内、标题真实出现在行里），`rr-2` 只比较标题字符串与物理页码。
# 同一个 context 在 `rr-2` 与 `rr-3` 下可能得到不同的 verified / unresolved 集合，
# 因此结论必须标明是哪个规则版本得出的。
#
# 本轮**没有**改动 `ReferenceEdge` 的 wire 字段、occurrence 的坐标语义或
# `TocSource` 身份模型，因此 `REFERENCE_EDGE_SCHEMA_VERSION` 保持 `res-4` 不动：
# wire schema 版本描述"格式能否被读出"，核验器版本描述"结论是用哪版信任边界得出的"。
REFERENCE_RESOLVER_VERSION = "rr-3"

# TS3 目录来源 / 正文落地对账（`toc_body_reconciliation`）的**状态机版本**。
#
# 它不是 wire format 版本（对账结果是复核产物，不是正式持久化对象类型），而是
# **同一份对账输出在不同版本下含义相反**的规则版本，因此必须按算法版本的同一政策
# 登记：分版本声明、旧值显式识别、绝不静默按新语义解释。
#
# `tocr-1`（初版，五桶）：把"目录项在真实版式上确已闭合到一条**合格正文 landing 行**、
# 但该行没有成为正式 `OutlineNode`"这一状态记成 `toc_source_only`，并**显式否定**它
# 是「真标题漏收」，理由是"目录来源仅有导航价值"。这是 **fail-open**：该行是**正文行**
# （页标签唯一映射到物理页、文本一致、非 furniture、非普通表格内容），不是目录页上的
# 来源行；它的缺席恰恰是"可能存在正文标题漏收"的直接证据。把它写成"不是漏收"会让
# 文档级与整轮 TS3 关闭门在真实漏收上**照样通过**。`tocr-1` 的五桶名称是
# `toc_body_resolved` / `toc_source_only` / `toc_target_unresolved` /
# `bookmark_navigation_only` / `body_heading_unassigned`。
#
# `tocr-2`（现行，六桶）：该状态改名为 `toc_body_unassigned`，语义**反转** ——
# 它是**阻断项**，必须进入 blocking / manual-review，令所在文档与整轮 TS3 关闭门
# **失败**，并逐条保留可复核身份（`TocSource` 身份、声明页标签、物理页、landing 行
# 坐标与正文文本、候选拒绝原因、可关联的未归属 span / 候选身份、来源是否可重建）。
# 同时**不得**为了清零该状态而自动把正文行加入树：只有既有标题资格规则本身足以采纳
# 时才生成节点，否则如实阻断。`toc_target_unresolved`（没有形成有效正文 landing）、
# `bookmark_navigation_only`（只作导航）、`body_heading_unassigned`（广义正文候选）
# 三者语义不变，且与 `toc_body_unassigned` **不得**互相合并。
#
# 版本字段本身也是对账输出的一部分：任何**没有**携带本版本的旧载荷（含 `tocr-1`
# 及更早的"无版本字段"产物，例如真实运行
# `evaluation/results/tree_structure_ts3_outline_ts3_outline_20260918T030000Z`）
# 一律按 legacy 识别并显式阻断，不得被新验收器静默当成当前版本。
TOC_BODY_RECONCILIATION_VERSION = "tocr-2"

# TS1.3 §四.4/§四.5：`toc_to_body` 的对象级可达路径需要三个**确定、版本化、公司无关**
# 的规则版本。它们进入 `TocSource` 的修订身份，因此规则一变、身份即变。
#
# - `TOC_SOURCE_BUILDER_VERSION`：目录来源身份（locator / id）的派生规则；
# - `TOC_TITLE_MATCH_VERSION`：目录项标题 ↔ 目标节点标题的归一与匹配规则；
# - `TOC_PAGE_LABEL_MATCH_VERSION`：目录页标签 ↔ PDF 物理页码的映射规则。
TOC_SOURCE_BUILDER_VERSION = "tsb-1"
TOC_TITLE_MATCH_VERSION = "ttm-1"
TOC_PAGE_LABEL_MATCH_VERSION = "tpl-1"

# ---------------------------------------------------------------------------
# TS5 §19.4.1：TableObject `to-4` 批的 wire / 算法 / capability 版本轴
# ---------------------------------------------------------------------------
#
# 本批**不新增**第二套研究运行时，也不改任何 DB migration 版本：这里登记的全部是
# `document_structure` 内的 wire format、规则与 capability 版本。登记规则与 TS1/TS4
# 完全一致：**本模块是 `document_structure/` 内唯一允许出现版本字面量的文件**，
# 业务模块必须引用常量名，不得散落裸字符串。
#
# 与 TS4 同样的分表理由：既有 9 项 wire 契约表（及其条数断言）与 TS4 的 8 项登记表
# 都**不动**，TS5 的登记放在各自的具名表里，通过 `ALL_*` 并集参与双射自检。

# --- TS5 顶层 wire 类型各自的 schema 版本 ---

# 单页物理表格片段内**每个 cell** 的 wire 版本。TS5 之前 cell 只是 `to-3` 父对象
# 的形式子结构（自身不带版本、只有 "单行 text + 一个首 locator"）。TS5 的 cell 携带
# 有序 `blocks` 与逐来源的 `source_fragments`，因此它有了自己的 wire 契约。
#
# `tc-1` 不是"某个曾发布过的独立 wire 版本"，而是**历史形态**的显式名字：cell 随
# `to-3` 父对象携带版本、自身没有 `schema_version` 也没有 `source_fragments`。登记它
# 使读回路径能把"旧 cell 只能随 to-3 history 读取"与"未知/打错的版本"区分开。
# `tc-3`：cell 允许**带 typed 缺口**——当某个片段无法闭合到真实 terminal source 时，
# cell 记录 `unproven_fragments`（`(页, 行, 片段索引)` 三元组，封闭形状）而不再让整张表
# 被丢弃。空 refs / 空 blocks **仅当** `unproven_fragments` 非空时合法，因此"静默空
# cell"仍不可能；表侧随之降为 `structure_state=partial`（`_structure_state` 原有判据）。
TABLE_CELL_SCHEMA_VERSION = "tc-3"

# TS5 final span（由 `sbf-1` 重建的正文 span）。它**不**复用 `OutlineSpan`：
# `span_schema._bind_span_builder_version` 把 `OutlineSpan.span_builder_version`
# 硬绑到 TS4 正文算法 `sb-7`，放宽它就会改写 TS4 已冻结的 span 语义。因此 final span
# 有自己的 wire 版本，与 `OutlineSpan` 并集共存、互不解释。
FINAL_SPAN_SCHEMA_VERSION = "fos-1"

# `TableRangeDecision`：一条 `table_inside/table_adjacency` provisional 范围的**恰好一个**
# 最终裁决（absorbed_as_caption / absorbed_as_unit / absorbed_as_note /
# absorbed_as_table_body / kept_as_paragraph / absorbed_into_body /
# unsupported_table_structure / unresolved_geometry）。
TABLE_RANGE_DECISION_SCHEMA_VERSION = "trd-1"
# `TableRelation`：final snapshot overlay 里的 typed 关系（不改冻结的 `DocumentOutline`
# /`ReferenceEdge`）。关系种类封闭为
# introduces / caption_of / unit_of / explains / footnote_of / continued_by / references；
# `reconciles_with` 仅保留 enum 扩展位，本批不得产生 resolved 关系。
TABLE_RELATION_SCHEMA_VERSION = "trl-1"
# `FinalComponentBinding`：覆盖**全部** TS4 components 的精确等集终态
# （final_span / table_object / pending / rejected）。
FINAL_COMPONENT_BINDING_SCHEMA_VERSION = "fcb-1"
# `TableCitableCoverage`：按 table→row→cell→source ref 逐段记录可引用与不可引用区间。
TABLE_CITABLE_COVERAGE_SCHEMA_VERSION = "tcc-1"
# `TableStructureGap`：诚实缺口（unsupported_table_structure / unresolved_geometry /
# upstream_table_scope_miss / visual_object_not_table / ambiguous_continuation /
# provenance_incomplete / root_identity_mismatch）。
#
# TS5 表格生产机制最终返修轮升版 `tsg-1 → tsg-2`：`TableGapEntry` 新增
# `source_intervals`（精确的、与 `TableSourceInterval` 同域的 `(页, 行, 片段,
# 片段内字符区间)` 列表）。这是 **wire format 变化**：`tsg-1` 的缺口载荷里
# "覆盖了哪些真实字符"完全无法表达，于是"这个字符已被正式延期"与"这个字符谁都
# 没管"在载荷上同形；`tsg-2` 让它们可区分。`tsg-1` 登记为 legacy：current reader
# 一律 fail-closed 拒绝，读历史产物走显式历史入口
# `table_schema.read_legacy_structure_gap`。
TABLE_STRUCTURE_GAP_SCHEMA_VERSION = "tsg-2"
# `FinalMaterialConservation`：TS4 disposition/component 与 final span/table/pending/
# rejected 的逐项分区与四层守恒（处置 / 组件 / Layout 文本 + Evidence 区间）。
#
# TS5 三个 P1 机制级定点返修轮升版 `fmc-1 → fmc-2`：`layout_text` 层的**接受集合**
# 变了 —— 新增一个明确终态分项 `registered_deferred`，用于承载"所属 component 已有
# 正式 pending/rejected 终态、且台账里有精确对应的 typed gap"的字符。同一份
# `layout_text` 载荷在 `fmc-1` 下只有四个分项（`residual_gap` 把"已延期"与"无归属"
# 混在一起），在 `fmc-2` 下必须把它们分开表示；分项词表是 wire format 的一部分
# （`ConservationLayer.from_dict` 按封闭集合与固定顺序拒绝非法分项），因此这是
# **格式语义**变化，不是原位补字段。
#
# `fmc-1` 登记为 legacy：current reader 一律 fail-closed 拒绝，**不得**静默按 `fmc-2`
# 解释（旧载荷的 `residual_gap` 里混着两类字符，按新语义读会把"未解释残余"错读成
# 0）。需要读回历史产物时走**显式历史入口** `table_schema.read_legacy_conservation`，
# 它按当时的四项分项表校验并标成 history-only。
#
# TS5 表格生产机制最终返修轮再升版 `fmc-2 → fmc-3`：`registered_deferred` 的**资格
# 语义**变了。`fmc-2` 下它只承认"所属 component 有正式 pending/rejected 终态 +
# 台账里有 typed gap"；`fmc-3` 下它还承认"该字符区间被**恰好一条** `tsg-2` 缺口的
# `source_intervals` 精确覆盖、且该缺口种类属于可延期集合"。同一份 `layout_text`
# 载荷在两种语义下会把同一个 `residual_gap` 字符分别算作"未解释残余"和"已正式
# 延期"，即**接受集合随版本不同**，因此是格式语义变化而不是原位补字段。
# `fmc-2` 同样登记为 legacy（current reader fail-closed；历史回读走
# `table_schema.read_legacy_conservation`）。
FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION = "fmc-3"
# `FinalMaterialStructureSnapshot`：TS5 的**唯一终端聚合节点**。所有成员身份先于它
# 存在；它的 content_fingerprint 不得反向进入任何成员或上游依赖束。
FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION = "fms-1"

# `FinalNavigationSynopsis`（§19.0.1 方案 C / T13）：TS5 的 final 节点简介，是 TS5 的
# **第 11 个**顶层 public wire type，与 TS4 的 `NavigationSynopsis` 是**两个类型**。
#
# 为什么必须是独立版本轴而不是让 TS4 轴升版：TS4 `NavigationSynopsis` 的 reason 词表是
# `no_span / empty_text / length_exceeded / alignment_failed / table_only_pending_ts5`，
# final 的是 `no_span / empty_text / length_exceeded / alignment_failed /
# table_material_available_no_text_synopsis`——**两个词表各有专属值**（前者不得出现
# "有表格无文本"，后者不得出现"还在等 TS5"），且来源类型不同（TS4 `OutlineSpan` vs
# `FinalOutlineSpan fos-*`）。把两者压成同一类型 + 同一版本轴，就只能靠"同一 wire 版本
# 有两套接受集合"实现，那是本计划一贯拒绝的；aggregate 按 child 猜 reader 同样被禁。
#
# 因此：`nss-3` / `ns-3` 只属于本常量对与 `FinalNavigationSynopsis`；`nss-2` / `ns-2`
# 只属于 TS4 `NavigationSynopsis`。二者不得 alias、不得继承式适配、不得互相 cast。
FINAL_SYNOPSIS_SCHEMA_VERSION = "nss-3"

# --- TS5 规则 / builder / capability 版本 ---

# 几何规则版本：绑定 PDF page width/height、CropBox/MediaBox、rotation、pdfplumber
# top-left 坐标 ↔ PageLayout bbox 的变换公式、量子化/容差、ruled/text strategy 的
# 固定 settings，以及 pdfplumber/PyMuPDF 的**实际**版本与 settings fingerprint。
# `tgeo-2`：把 `CellGridPlan` **自己声明的**闭合条件（"覆盖不重叠、不越界、无未解释
# 空洞"）真正实现——`derive_cell_grid` 此前只在 `problems` 里报空洞与列带问题，重叠的
# 覆盖**不报**，于是"`problems` 为空 ⇔ 网格闭合"这条文档化等价在无框线行带路径上不成立
# （实测：多列表头聚出的 `(r0,c0)/(r0,c1)/(r0,c2)` 各带 `colspan=2`，覆盖互相重叠）。
# `tgeo-3`：无框线行带路径的**列带**判据修正。此前列以"片段**左边界**"一维聚类，
# 右对齐的数字列因各行位数不同而左边界漂移（实测同列 183.3 / 192.3），漂移超过 3 pt
# 就被拆成两个各自只出现在 1 个行带里的簇，两簇都因"至少 2 个行带"被整列丢弃，两个
# 片段随后落到同一个 `(band, col)` 上以 `fragment_consumed_twice` 被静默吞掉——整列
# 凭空消失，网格出现 `unexplained_hole`（实测 p24 并集候选 2×4 而非 2×6；修前该页
# 一张表也建不出来）。本版把同一条真实列的两半并回来：行带集合不相交、右边界**代表值**
# 相差在容差内、左边界之差不超过较窄簇内最大片段宽度，三条同时成立才并。三条都只看
# 真实几何，与文档、公司、语言、页码、表号无关；被否的候选仍按原样拒绝。
#
# `tgeo-4`：`lines` 通道增加**第二路**检测——除原有的"页面**全部**描边都是行/列边界"
# 之外，再跑一路"只有**细长描边**（`RULE_STROKE_MAX_THICKNESS_PT`，一维 ≤ 0.5 pt、
# 另一维更大）才是边界"的显式格架（`_ruled_lattice_settings`）。同一份 PDF 在
# `tgeo-3` 与 `tgeo-4` 下**得到不同的网格与不同的表集**：真实电子 PDF 里的文字框
# （包住某个词的矩形）与合并表头**内缩框**的边都落在真规线之间，`tgeo-3` 把它们当成
# 额外列，于是整张物理表被判成 10 行 × 17 列、102 个 `None`，骨架被丢弃、**一
# 张表也建不出来**（实测 2025 年报 p24）；`tgeo-4` 下同一页面得到 10 行 × 6 列、
# 46 个真实 cell（含 4 个跨行/跨列合并）的**整张物理表**候选，表题行、完整物理表头、
# 期间列、合计行、分块行与主体行同在一条候选里。逐段描边**原样**传入，因此"缺一段内线"的
# 合并格依旧成立；判据只看本页描边的量纲分布，与文档、公司、语言、页码、表号无关，
# 对任何电子 PDF 同样成立。同一政策：只显式识别、要求显式重算，绝不静默按 `tgeo-4`
# 解释 `tgeo-3` 的网格。
TABLE_GEOMETRY_VERSION = "tgeo-4"
# `structure_class` 的封闭规则版本，与
# `policies/table_classification_profile_v1.json` 的 file SHA256 共同绑定。
TABLE_CLASSIFICATION_PROFILE_VERSION = "tcp-1"
# cell 内 block role（heading/paragraph/list_item/line/unclassified）的封闭规则版本，
# 与 `policies/table_cell_block_profile_v1.json` 的 file SHA256 共同绑定。
TABLE_CELL_BLOCK_PROFILE_VERSION = "tcbp-1"
# `TableRelation` 的派生规则版本（端点 algebra、额外硬门与 continuation 真值表）。
TABLE_RELATION_BUILDER_VERSION = "trb-1"
# `FinalMaterialStructureSnapshot` 的组装规则版本（overlay 顺序、final span 重建、
# component binding 分区、coverage/conservation/synopsis 生成）。
# `fmb-2`：coverage 生成里那条"来源片段的片段内区间必须**等于**整段 `LayoutSpan`
# 文本长度"的断言被换成真正的子区间不变量（区间落在片段内 ＋ 区间之外只是非语义
# 空白，判据与守恒层**同一**词表）。旧规则把右对齐补白、缩排空白与行末折行空隙
# 当成"来源不完整"，于是同一份 `PageLayout` 在 `fmb-1` 下直接抛
# `FinalMaterialBuildError`，在 `fmb-2` 下正常组装出快照——两张快照的存在性不同，
# 因此必须换版本并登记 legacy，不得静默按 `fmb-2` 解释旧载荷。
FINAL_MATERIAL_BUILDER_VERSION = "fmb-2"
# TS5 复核层（`final_verifier`）签发 runtime capability 的 issuer 版本。复核是
# **独立重建 + canonical 全等**，因此重建哪些成员、比对哪些指纹的变化都会改变同一份
# 输入的复核结论，必须由本常量承载。
VERIFIED_FINAL_MATERIAL_ISSUER_VERSION = "vfmi-1"
# TS5 final 正文 span 的构造算法版本（§18.7.3 已冻结）。它重新计算冻结 TS4-B 的
# 12 个边界因子与 0.85 必要阈值；`decision` 本身**不**构成 citable 资格。
TS5_FINAL_SPAN_BUILDER_VERSION = "sbf-1"
# TS5 `FinalNavigationSynopsis` 的生成规则版本（§19.10）。它绑定：片段选取规则、reason
# 判定函数、`source_final_span_ids` 的派生，以及"来源只能是 final 可引用 paragraph span"
# 这一硬约束。与 TS4 的 `SYNOPSIS_VERSION=ns-2` 是**两条互不相干的算法轴**：同一份输入
# 在两者下产出的是不同类型、不同 reason 词表的对象。
FINAL_SYNOPSIS_VERSION = "ns-3"

# M930-3 §18.1 A5：**demo run 内的图侧正式组合根**规则版本（`lts-1`）。
#
# 它描述的是"由同次 live TS4 能力取得**正式资格**"这条组合链的规则：调用顺序
# （`verified_snapshot()` → `final_material_builder.build_final_material_snapshot`
# → `final_verifier.verify_final_material_snapshot`）、拒绝的传播方式（typed 拒绝值，
# **不**吞掉、**不**降级、**不**把 builder 的返回值当资格），以及反自证边界。
#
# 它**不**改任何 wire（复用 `fms-1` 与既有 capability 种类），因此不进任何 wire
# 登记表；也不在 `harness/topic_schema` 的 18 键依赖指纹内（§18.7 已逐键核对）。
#
# `lts-1` → `lts-2`（2026-09-29，§0.19 逐表完整证明）：**改的是"读得到什么"这条判据**。
# `lts-1` 下拒绝态的取用面只有 `refusal`（快照虽被保留，但没有**明示**的读入口），因此
# "逐表证明"在构造上无法从拒绝态读到任何东西；`lts-2` 明示了 `table_scope_readings()`
# 这一**诊断读数**入口（逐表范围原子的归属），同时逐字保留"读数不是资格、拒绝不变、
# `tables` 在拒绝态仍抛错"三条边界。同一份输入在两版下对"能不能取到逐表读数"给出
# **相反**答案，因此这是判据换轴而非实现细节：按门规显式升轴，且旧 `lts-1` 投影一律
# fail-closed（`reprove_live_table_source` 逐项比对 identity 投影，版本不同即漂移）。
LIVE_TABLE_SOURCE_VERSION = "lts-2"

# M930-3 §0.19：**演示目标表的逐表完整证明**规则版本（`tlp-1`）。
#
# 它描述的是"一张**已构造**的图侧表对象，能不能在**同一份图侧来源**上被独立地、
# 完整地证明"这条规则：表题与主体绑定、完整物理表头与行列标签、合并单元格展开、
# 续表对应关系、本表范围内字符/区域的守恒与**唯一归属**（不得按任意优先级吞掉重叠、
# 不得把未知字符标为已消费）。
#
# 它**不**是资格结论的替代品：文档级守恒拒发仍然留在账上（§0.19 只把"整份文档被拒
# 则所有目标表归零"这一粒度换掉，不豁免 `partial`/未构造/表自身账不平的表）。也**不**
# 授予任何数字权威：阅读资格与格级数字权威是两条独立资格。
TABLE_LOCAL_PROOF_RULE_VERSION = "tlp-1"

# ---------------------------------------------------------------------------
# 数值阈值
# ---------------------------------------------------------------------------

# 参与哈希与持久化前一律量化到该小数位（计划 §1.0）。
# 目的：跨平台/跨库浮点尾差不得改变对象身份。
FLOAT_PRECISION = 3


# ---------------------------------------------------------------------------
# 阈值
# ---------------------------------------------------------------------------

# PageLayout ↔ Evidence alignment 的 coverage 阈值。**已冻结**。
#
# 批准来源：用户 + Codex
# 批准日期：2026-09-17
#
# 批准的业务规则（TS2 最终关闭轮）：
#   - `coverage <= 0`                        ⇒ `unaligned`
#   - `coverage < ALIGN_MIN`                 ⇒ `unaligned`
#   - `coverage >= ALIGN_MIN` 且含 `unexplained` ⇒ `partially_aligned`
#   - `coverage >= ALIGN_MIN` 且无 `unexplained` ⇒ `aligned`
#   只有 `aligned` 可引用（`TextAlignmentRecord.is_citable()`）。
#
# 冻结语义（批准原文，不得再讨论、不得再重新选择）：
#   - 这是**单一**文本对齐阈值：**不设**正文页 / 表格页两套阈值。
#   - 表格结构（列序重排等）不靠阈值承接，由**未来的 `TableObject` 路径**承接。
#   - `partially_aligned` 与 `unaligned` **一律不可引用**；达到阈值但仍含
#     `unexplained` 的块属于 `partially_aligned`，同样不可引用。
#   - 阈值以上的 partial 块**不阻塞** TS2：它们已被 fail-closed 隔离。
#
# 数值依据：TS2 / TS2.1 全量（769 块，三份真实电子 PDF）coverage 归因与三态正交
# 统计；`evaluation/results/tree_structure_ts2_layout_ts2_4_*` 中的候选敏感度表
# 仅作**诊断留存**，不再是推荐，也不再等待裁决。
ALIGN_MIN: float = 0.90

# `OutlineSpan` 参与集合型 aspect `set_complete` 的最低置信度。
# 与 ALIGN_MIN **不同**：该值由用户 + Codex 的人工门批准（TS4-A 的 8 项 verdict 全 pass、
# 12 项因子整体接受、threshold=0.85），经 create-once `review_attestation.json` 封存后，
# 由 TS4-B 的 approval/frozen 记录**确定性导出**并与 frozen policy 同步发布。
#
# 它只是**必要条件**：`confidence >= 0.85` 不使任何 span / aspect 自动完成，也不得由
# 阶段开关自动取得 `set_complete`；覆盖、gap、fallback / 跨标题 / unassigned 仍按事实与
# authority 判定。A 阶段（`None`）与 B 阶段（有限值）的快照绑定**不同** policy identity；
# 历史 A 快照不得因本常量变化而被重新解释。
SPAN_CONFIDENCE_MIN: float | None = 0.85


# ---------------------------------------------------------------------------
# 登记表（自检与测试用；集中登记，避免"新增常量却忘了进审计"）
# ---------------------------------------------------------------------------

# 版本常量名 → 值（顺序即声明顺序，便于人读 diff）。
VERSION_CONSTANTS: dict[str, object] = {
    "LAYOUT_SCHEMA_VERSION": LAYOUT_SCHEMA_VERSION,
    "OUTLINE_SCHEMA_VERSION": OUTLINE_SCHEMA_VERSION,
    "SPAN_SCHEMA_VERSION": SPAN_SCHEMA_VERSION,
    "TABLE_SCHEMA_VERSION": TABLE_SCHEMA_VERSION,
    "SYNOPSIS_SCHEMA_VERSION": SYNOPSIS_SCHEMA_VERSION,
    "ALIGN_SCHEMA_VERSION": ALIGN_SCHEMA_VERSION,
    "ALIGN_REFUSAL_SCHEMA_VERSION": ALIGN_REFUSAL_SCHEMA_VERSION,
    "PROFILE_SCHEMA_VERSION": PROFILE_SCHEMA_VERSION,
    "REFERENCE_EDGE_SCHEMA_VERSION": REFERENCE_EDGE_SCHEMA_VERSION,
    "LAYOUT_ENGINE": LAYOUT_ENGINE,
    "LAYOUT_ENGINE_VERSION": LAYOUT_ENGINE_VERSION,
    "NORMALIZATION_VERSION": NORMALIZATION_VERSION,
    "OUTLINE_ALGORITHM_VERSION": OUTLINE_ALGORITHM_VERSION,
    "HEADING_QUALIFICATION_PROFILE_VERSION": HEADING_QUALIFICATION_PROFILE_VERSION,
    "TABLE_REGION_QUALIFICATION_VERSION": TABLE_REGION_QUALIFICATION_VERSION,
    "SPAN_BUILDER_VERSION": SPAN_BUILDER_VERSION,
    "EVIDENCE_BLOCK_INPUT_VERSION": EVIDENCE_BLOCK_INPUT_VERSION,
    "EVIDENCE_SET_SNAPSHOT_VERSION": EVIDENCE_SET_SNAPSHOT_VERSION,
    "EVIDENCE_SET_GATEWAY_VERSION": EVIDENCE_SET_GATEWAY_VERSION,
    "ALIGNMENT_PARTITION_VALIDATOR_VERSION": ALIGNMENT_PARTITION_VALIDATOR_VERSION,
    "TABLE_BUILDER_VERSION": TABLE_BUILDER_VERSION,
    "SYNOPSIS_VERSION": SYNOPSIS_VERSION,
    "ALIGNER_VERSION": ALIGNER_VERSION,
    "PROFILE_RULE_VERSION": PROFILE_RULE_VERSION,
    "REFERENCE_EDGE_VERSION": REFERENCE_EDGE_VERSION,
    "REFERENCE_RESOLVER_VERSION": REFERENCE_RESOLVER_VERSION,
    "TOC_SOURCE_BUILDER_VERSION": TOC_SOURCE_BUILDER_VERSION,
    "TOC_TITLE_MATCH_VERSION": TOC_TITLE_MATCH_VERSION,
    "TOC_PAGE_LABEL_MATCH_VERSION": TOC_PAGE_LABEL_MATCH_VERSION,
    "TOC_BODY_RECONCILIATION_VERSION": TOC_BODY_RECONCILIATION_VERSION,
    # TS4 §18.12.4：8 个新 wire 类型 + 20 个新算法 / issuer 版本常量。
    "OUTLINE_STRUCTURE_SNAPSHOT_SCHEMA_VERSION":
        OUTLINE_STRUCTURE_SNAPSHOT_SCHEMA_VERSION,
    "SPAN_QUALIFICATION_POLICY_SCHEMA_VERSION":
        SPAN_QUALIFICATION_POLICY_SCHEMA_VERSION,
    "BODY_RANGE_DISPOSITION_SCHEMA_VERSION":
        BODY_RANGE_DISPOSITION_SCHEMA_VERSION,
    "SPAN_EVIDENCE_COMPONENT_SCHEMA_VERSION":
        SPAN_EVIDENCE_COMPONENT_SCHEMA_VERSION,
    "SPAN_CITABLE_COVERAGE_SCHEMA_VERSION":
        SPAN_CITABLE_COVERAGE_SCHEMA_VERSION,
    "SPAN_CONSERVATION_SCHEMA_VERSION": SPAN_CONSERVATION_SCHEMA_VERSION,
    "SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION": SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION,
    "SYNOPSIS_SOURCE_VALIDATION_SCHEMA_VERSION":
        SYNOPSIS_SOURCE_VALIDATION_SCHEMA_VERSION,
    "TS4_BODY_SPAN_BUILDER_VERSION": TS4_BODY_SPAN_BUILDER_VERSION,
    "OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION":
        OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION,
    "SPAN_QUALIFICATION_POLICY_VERSION": SPAN_QUALIFICATION_POLICY_VERSION,
    "OUTLINE_STRUCTURE_PROVIDER_VERSION": OUTLINE_STRUCTURE_PROVIDER_VERSION,
    "EVIDENCE_GATEWAY_PROVIDER_VERSION": EVIDENCE_GATEWAY_PROVIDER_VERSION,
    "ALIGNMENT_TERMINAL_PROVIDER_VERSION": ALIGNMENT_TERMINAL_PROVIDER_VERSION,
    "QUALIFICATION_POLICY_PROVIDER_VERSION":
        QUALIFICATION_POLICY_PROVIDER_VERSION,
    "VERIFIED_PAGE_LAYOUT_ISSUER_VERSION": VERIFIED_PAGE_LAYOUT_ISSUER_VERSION,
    "VERIFIED_ALIGNMENT_ISSUER_VERSION": VERIFIED_ALIGNMENT_ISSUER_VERSION,
    "VERIFIED_CURRENT_EVIDENCE_AUTHORITY_VERSION":
        VERIFIED_CURRENT_EVIDENCE_AUTHORITY_VERSION,
    "VERIFIED_TS3_HANDOFF_VERSION": VERIFIED_TS3_HANDOFF_VERSION,
    "PINNED_PAGE_LAYOUT_ISSUER_VERSION": PINNED_PAGE_LAYOUT_ISSUER_VERSION,
    "PINNED_ALIGNMENT_ISSUER_VERSION": PINNED_ALIGNMENT_ISSUER_VERSION,
    "PINNED_EVIDENCE_AUTHORITY_VERSION": PINNED_EVIDENCE_AUTHORITY_VERSION,
    "PINNED_TS3_HANDOFF_VERSION": PINNED_TS3_HANDOFF_VERSION,
    "FIXTURE_PAGE_LAYOUT_ISSUER_VERSION": FIXTURE_PAGE_LAYOUT_ISSUER_VERSION,
    "FIXTURE_ALIGNMENT_ISSUER_VERSION": FIXTURE_ALIGNMENT_ISSUER_VERSION,
    "FIXTURE_EVIDENCE_AUTHORITY_VERSION": FIXTURE_EVIDENCE_AUTHORITY_VERSION,
    "FIXTURE_TS3_HANDOFF_VERSION": FIXTURE_TS3_HANDOFF_VERSION,
    "TESTING_TS3_HANDOFF_VERSION": TESTING_TS3_HANDOFF_VERSION,
    "VERIFIED_SPAN_SNAPSHOT_VERSION": VERIFIED_SPAN_SNAPSHOT_VERSION,
    # TS5 §19.4.1：10 个新 wire 版本 + 8 个新规则 / builder / capability 版本。
    "TABLE_CELL_SCHEMA_VERSION": TABLE_CELL_SCHEMA_VERSION,
    "FINAL_SPAN_SCHEMA_VERSION": FINAL_SPAN_SCHEMA_VERSION,
    "FINAL_SYNOPSIS_SCHEMA_VERSION": FINAL_SYNOPSIS_SCHEMA_VERSION,
    "TABLE_RANGE_DECISION_SCHEMA_VERSION": TABLE_RANGE_DECISION_SCHEMA_VERSION,
    "TABLE_RELATION_SCHEMA_VERSION": TABLE_RELATION_SCHEMA_VERSION,
    "FINAL_COMPONENT_BINDING_SCHEMA_VERSION":
        FINAL_COMPONENT_BINDING_SCHEMA_VERSION,
    "TABLE_CITABLE_COVERAGE_SCHEMA_VERSION":
        TABLE_CITABLE_COVERAGE_SCHEMA_VERSION,
    "TABLE_STRUCTURE_GAP_SCHEMA_VERSION": TABLE_STRUCTURE_GAP_SCHEMA_VERSION,
    "FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION":
        FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION,
    "FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION":
        FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION,
    "TABLE_GEOMETRY_VERSION": TABLE_GEOMETRY_VERSION,
    "TABLE_CLASSIFICATION_PROFILE_VERSION": TABLE_CLASSIFICATION_PROFILE_VERSION,
    "TABLE_CELL_BLOCK_PROFILE_VERSION": TABLE_CELL_BLOCK_PROFILE_VERSION,
    "TABLE_RELATION_BUILDER_VERSION": TABLE_RELATION_BUILDER_VERSION,
    "FINAL_MATERIAL_BUILDER_VERSION": FINAL_MATERIAL_BUILDER_VERSION,
    "VERIFIED_FINAL_MATERIAL_ISSUER_VERSION":
        VERIFIED_FINAL_MATERIAL_ISSUER_VERSION,
    "TS5_FINAL_SPAN_BUILDER_VERSION": TS5_FINAL_SPAN_BUILDER_VERSION,
    "FINAL_SYNOPSIS_VERSION": FINAL_SYNOPSIS_VERSION,
    "LIVE_TABLE_SOURCE_VERSION": LIVE_TABLE_SOURCE_VERSION,
    "TABLE_LOCAL_PROOF_RULE_VERSION": TABLE_LOCAL_PROOF_RULE_VERSION,
    "FLOAT_PRECISION": FLOAT_PRECISION,
}

# TS1 指令逐字强制的 12 个版本常量名（用于测试断言"清单不得缺失"）。
MANDATED_VERSION_CONSTANT_NAMES: tuple[str, ...] = (
    "LAYOUT_SCHEMA_VERSION",
    "LAYOUT_ENGINE_VERSION",
    "NORMALIZATION_VERSION",
    "OUTLINE_SCHEMA_VERSION",
    "OUTLINE_ALGORITHM_VERSION",
    "SYNOPSIS_VERSION",
    "ALIGNER_VERSION",
    "SPAN_BUILDER_VERSION",
    "TABLE_BUILDER_VERSION",
    "PROFILE_SCHEMA_VERSION",
    "PROFILE_RULE_VERSION",
    "REFERENCE_EDGE_VERSION",
)

# wire format 版本（9 个）：每个正式持久化/交换类型恰好一个。
# TS3 §五 新增第 9 个：对齐**拒绝终态**的 wire 版本（`AlignmentRefusalRecord`）。
SCHEMA_VERSION_CONSTANT_NAMES: tuple[str, ...] = (
    "LAYOUT_SCHEMA_VERSION",
    "OUTLINE_SCHEMA_VERSION",
    "SPAN_SCHEMA_VERSION",
    "TABLE_SCHEMA_VERSION",
    "SYNOPSIS_SCHEMA_VERSION",
    "ALIGN_SCHEMA_VERSION",
    "ALIGN_REFUSAL_SCHEMA_VERSION",
    "PROFILE_SCHEMA_VERSION",
    "REFERENCE_EDGE_SCHEMA_VERSION",
)

# 算法 / 引擎 / 规则版本（20 个）：进入身份，但不是 wire format 版本。
ALGORITHM_VERSION_CONSTANT_NAMES: tuple[str, ...] = (
    "LAYOUT_ENGINE_VERSION",
    "NORMALIZATION_VERSION",
    "OUTLINE_ALGORITHM_VERSION",
    "SPAN_BUILDER_VERSION",
    "TABLE_BUILDER_VERSION",
    "SYNOPSIS_VERSION",
    "ALIGNER_VERSION",
    "PROFILE_RULE_VERSION",
    "REFERENCE_EDGE_VERSION",
    "REFERENCE_RESOLVER_VERSION",
    # TS1.3 §四.4/§四.5：目录来源身份、标题匹配、页码映射三套规则。
    "TOC_SOURCE_BUILDER_VERSION",
    "TOC_TITLE_MATCH_VERSION",
    "TOC_PAGE_LABEL_MATCH_VERSION",
    # TS3 已知 P1 收口轮：对齐输入契约、EvidenceSet 权威快照、只读 gateway 规则、
    # 分区校验器规则。四者都进入正式对象身份 / 终态身份 / 依赖指纹。
    "EVIDENCE_BLOCK_INPUT_VERSION",
    "EVIDENCE_SET_SNAPSHOT_VERSION",
    "EVIDENCE_SET_GATEWAY_VERSION",
    "ALIGNMENT_PARTITION_VALIDATOR_VERSION",
    # TS3 标题资格定点返修轮：标题资格判据与表格区域资格判断各自的规则版本。
    "HEADING_QUALIFICATION_PROFILE_VERSION",
    "TABLE_REGION_QUALIFICATION_VERSION",
    # TS3 TOC 正文落地关闭门轮：目录来源 / 正文落地对账的**状态机版本**。它不改
    # wire format、不改节点集，但改变同一份对账输出的**阻断语义**（`tocr-1` 下
    # 该状态是"不是漏收"的留痕，`tocr-2` 下是阻断项），因此与标题资格同一政策登记。
    "TOC_BODY_RECONCILIATION_VERSION",
)

# 正式持久化/交换类型名 → (其 schema_version 字段名, 其 schema 版本常量名)。
# 子结构类型（LayoutSpan / LayoutLine / LayoutPage / OutlineNode / SynopsisSnippet /
# AspectNavigationEntry / TableRow / TableCell / ReferenceOccurrence）**不**自带版本：
# 它们的版本由父对象或调用上下文携带并校验，避免同一事实两处版本（计划 §1.0）。
VERSIONED_OBJECT_SCHEMA_FIELDS: dict[str, tuple[str, str]] = {
    "PageLayout": ("schema_version", "LAYOUT_SCHEMA_VERSION"),
    "DocumentOutline": ("schema_version", "OUTLINE_SCHEMA_VERSION"),
    "OutlineSpan": ("schema_version", "SPAN_SCHEMA_VERSION"),
    "TableObject": ("schema_version", "TABLE_SCHEMA_VERSION"),
    "NavigationSynopsis": ("schema_version", "SYNOPSIS_SCHEMA_VERSION"),
    "TextAlignmentRecord": ("schema_version", "ALIGN_SCHEMA_VERSION"),
    "AlignmentRefusalRecord": ("schema_version", "ALIGN_REFUSAL_SCHEMA_VERSION"),
    "AspectNavigationProfile": ("schema_version", "PROFILE_SCHEMA_VERSION"),
    "ReferenceEdge": ("schema_version", "REFERENCE_EDGE_SCHEMA_VERSION"),
}

# 无独立版本、由父对象携带版本的子结构类型名。
SUBSTRUCTURE_TYPE_NAMES: tuple[str, ...] = (
    "LayoutSpan", "LayoutLine", "LayoutPage", "OutlineNode",
    "SynopsisSnippet", "AspectNavigationEntry", "TableRow", "TableCell",
    # TS1.1 收口轮新增：ReferenceOccurrence 是 ReferenceEdge 的形式子结构，
    # 由父对象携带版本。
    "ReferenceOccurrence",
)

# **运行时**类型名：参与公共 API，但既不是持久化对象也不是子结构，因此既不携带
# 也不参与任何 wire format 版本。
#
# - `ReferenceValidationContext`：调用方提供的真实对象核验上下文（TS1.2 起取代
#   上一轮的字符串清单注册表），只在一次核验调用内存在；
# - `VerifiedReferences`：对象级核验入口的**结论**对象——"引用已验证"的唯一载体。
# - `TocSource`：TS1.3 §四.3/§四.4 新增的**目录项真实来源身份**。它绑定真实
#   `PageLayout` 上的真实 `LayoutLine`（页码、行号、行内字符区间、行内容摘要），
#   身份由真实行重算，因此"调用方自报同值字符串"不能通过。它是核验输入，
#   和 `ReferenceValidationContext` 一样只在一次核验调用内存在，**不进入任何
#   wire format、fingerprint 或 id**。
RUNTIME_ONLY_TYPE_NAMES: tuple[str, ...] = (
    "ReferenceValidationContext", "VerifiedReferences", "TocSource",
)

# 历史 wire / 规则常量：**不是** current 值，因而不进 `VERSION_CONSTANTS`
# （否则会被 `all_version_literals()` 当成"当前字面量"扫描，也会让
# `classify_schema_version` 失去"这是被替换掉的旧版本"的结论）。
#
# 它们存在的唯一理由是让历史 reader（`schema.TableObject` 的历史路径、TS5 的
# history dispatch）能**引用常量名**而不是散落裸字符串，同时保证全局 current 升版后
# 旧类不会把自己变成"错误解释器"（§19.11.2 明确要求）。定义必须早于
# `LEGACY_SCHEMA_VERSIONS` / `LEGACY_ALGORITHM_VERSIONS` 的使用点。
LEGACY_TABLE_SCHEMA_VERSION = "to-3"
LEGACY_TABLE_BUILDER_VERSION = "tb-1"
LEGACY_TABLE_CELL_SCHEMA_VERSION = "tc-1"
LEGACY_TABLE_CELL_SCHEMA_VERSION_2 = "tc-2"

# §19.0.1 方案 C 明确**不**需要 `LEGACY_SYNOPSIS_SCHEMA_VERSION` /
# `LEGACY_SYNOPSIS_VERSION`：`nss-2` / `ns-2` 是 TS4 的 **current** 版本轴（见上文
# `SYNOPSIS_SCHEMA_VERSION`），不是被替换掉的旧值。首轮编码把它们当成 history-only
# 才会需要这两个常量；那是错误的隔离方向，已删除。TS4 的 legacy 仍只有 `nss-1` / `ns-1`。

# 旧 wire format 版本：**必须显式识别并拒绝**，禁止静默当作新版本解释。
# 一旦出现真实持久化对象，迁移函数必须读取这些值并显式转换（TS1 无持久化层，
# 故本轮只提供识别与拒绝，见 `classify_schema_version`）。
LEGACY_SCHEMA_VERSIONS: dict[str, tuple[str, ...]] = {
    "LAYOUT_SCHEMA_VERSION": ("pl-1", "pl-2"),
    "OUTLINE_SCHEMA_VERSION": ("do-1", "do-2", "do-3"),
    "SPAN_SCHEMA_VERSION": ("os-1", "os-2", "os-3"),
    # TS5 §19.15.1：`to-3` 是**单页物理片段 + 单行 cell + 一个首 locator** 的旧表格
    # wire。它在 TS5 之后只能由 `document_structure.schema.TableObject`（历史 reader）
    # 审计读回，不得成为 current、不得适配成 `to-4`、不得进入 final snapshot/capability。
    "TABLE_SCHEMA_VERSION":
        ("to-1", "to-2", LEGACY_TABLE_SCHEMA_VERSION),
    # `tc-1` 是 cell **没有自己 wire 版本**的历史形态：它随 `to-3` 父对象携带版本，
    # 且只有 "单行 text + 一个首 locator"。TS5 的 `TableCellV4.from_dict` 读到这种
    # 形态必须给出"旧 cell 只能随 to-3 history 读取、须重建"的专门错误。
    "TABLE_CELL_SCHEMA_VERSION": (LEGACY_TABLE_CELL_SCHEMA_VERSION,
                                  LEGACY_TABLE_CELL_SCHEMA_VERSION_2),
    # TS3 §五：`als-1` 是**只读兼容**的旧对齐 wire format（无精确分子/分母，
    # verdict 只能由量化 coverage 推导）。登记它，是为了让读回路径能把它
    # **显式识别**并按历史语义重算，而不是当成"未知版本"或静默按 `als-3` 解释。
    # 收口轮再加入 `als-2`（有精确分子，但分区闭合不变量只在私有写路径检查）。
    "ALIGN_SCHEMA_VERSION": ("als-1", "als-2"),
    # `alr-1`：拒绝记录的初版 wire format（无分区闭合不变量校验）。
    "ALIGN_REFUSAL_SCHEMA_VERSION": ("alr-1",),
    # M930-3 业务纵链补充门：`anps-2` 的条目 wire 没有 `parent_keys`。旧载荷读回时缺该字段
    # 与"父键恰为空"不可区分（后者在新语义下是一条**结论**），因此只显式识别并要求重算。
    # r4 后定点返修再加入 `anps-3`：它的条目 wire 没有 `subject_head`。缺该字段与
    # "主体标签恰为空"不可区分，后者会让 `navroot-subject-adjacency` 静默失效（= 退回
    # `anp-4` 行为），因此同样只显式识别、要求显式重算。
    "PROFILE_SCHEMA_VERSION": ("anps-1", "anps-2", "anps-3"),
    "REFERENCE_EDGE_SCHEMA_VERSION": ("res-1", "res-2", "res-3"),
    # TS4 §18.12.3：`nss-1` 的 reason 词表只有 4 值。它**只能**被显式识别并拒绝
    # （要求重算），不得作为 current synopsis 读回消费。
    #
    # §19.0.1 方案 C：`nss-2` **不在此列**。它是 TS4 的 current 版本轴，必须能被
    # 原样读回；把它降为 legacy 会使 TS4-B 已封存快照无法走正常路径。TS5 的 `nss-3`
    # 属于另一个类型（`FinalNavigationSynopsis`），不是本轴的"更新版本"。
    "SYNOPSIS_SCHEMA_VERSION": ("nss-1",),
    # TS5 三个 P1 机制级定点返修轮：`fmc-1` 的 `layout_text` 层只有四个分项，
    # `residual_gap` 同时表示"已由正式 typed gap 延期的字符"与"无任何归属的字符"。
    # 同一份载荷在 `fmc-1` 与 `fmc-2` 下含义不同（前者无法表达"未解释残余是否为 0"），
    # 因此登记为 legacy：只显式识别并要求显式重算，绝不静默按新语义解释。
    #
    # TS5 表格生产机制最终返修轮再登记 `fmc-2`：`fmc-3` 扩大了 `registered_deferred`
    # 的资格集合（承认被 `tsg-2` 缺口 `source_intervals` 精确覆盖的区间），同一份
    # 载荷的接受集合随之变化，同样不得静默按新语义解释。
    "FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION": ("fmc-1", "fmc-2"),
    # TS5 表格生产机制最终返修轮：`tsg-1` 的 `TableGapEntry` **没有**源区间字段，
    # 无法区分"已正式延期"与"无人认领"。旧载荷登记为 legacy：只显式识别并要求
    # 显式重算，读历史产物走 `table_schema.read_legacy_structure_gap`。
    "TABLE_STRUCTURE_GAP_SCHEMA_VERSION": ("tsg-1",),
}

# 携带**精确分子** `matched_chars` 的对齐 wire 版本（自 `als-2` 起的后继版本）。
#
# `als-1` 只能表达"已量化"的覆盖率，没有精确分子；`als-2` 引入它，`als-3` 沿用。
# 这条登记表让 schema 层不必在代码里散落版本字面量（本模块是 `document_structure/`
# 内唯一允许出现版本字面量的文件）：判定分支读 `V.ALIGN_EXACT_RATIO_VERSIONS`，
# 而不是写 `== "als-2"`。
ALIGN_EXACT_RATIO_VERSIONS: tuple[str, ...] = (
    "als-2",
    ALIGN_SCHEMA_VERSION,
)

# ---------------------------------------------------------------------------
# TS4 §18.12.4：`OutlineSpan + NavigationSynopsis` 批新增类型的登记表
# ---------------------------------------------------------------------------
#
# 与上面两表**同等权威**，只是分开登记：既有 9 项 wire 契约表（及其条数断言）不动，
# 新表通过 `ALL_VERSIONED_OBJECT_SCHEMA_FIELDS` 与之组成无重名并集。任何"新增正式
# 序列化类型却不登记"的做法都会被 `self_check()` 与 `span_schema.self_check()` 的
# 双射校验抓住。

#: TS4 新增顶层 wire 类型（8 个）。**不是** runtime-only：它们全部有 `to_dict`/`from_dict`。
SPAN_RECORD_PUBLIC_TYPES: tuple[str, ...] = (
    "OutlineStructureSnapshot",
    "SpanQualificationPolicy",
    "BodyRangeDisposition",
    "SpanEvidenceComponent",
    "SpanCitableCoverage",
    "SpanConservation",
    "SpanBuildSnapshot",
    "SynopsisSourceValidation",
)

#: TS4 顶层类型名 → (其 schema_version 字段名, 其 schema 版本常量名)。
SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS: dict[str, tuple[str, str]] = {
    "OutlineStructureSnapshot": (
        "schema_version", "OUTLINE_STRUCTURE_SNAPSHOT_SCHEMA_VERSION"),
    "SpanQualificationPolicy": (
        "schema_version", "SPAN_QUALIFICATION_POLICY_SCHEMA_VERSION"),
    "BodyRangeDisposition": (
        "schema_version", "BODY_RANGE_DISPOSITION_SCHEMA_VERSION"),
    "SpanEvidenceComponent": (
        "schema_version", "SPAN_EVIDENCE_COMPONENT_SCHEMA_VERSION"),
    "SpanCitableCoverage": (
        "schema_version", "SPAN_CITABLE_COVERAGE_SCHEMA_VERSION"),
    "SpanConservation": (
        "schema_version", "SPAN_CONSERVATION_SCHEMA_VERSION"),
    "SpanBuildSnapshot": (
        "schema_version", "SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION"),
    "SynopsisSourceValidation": (
        "schema_version", "SYNOPSIS_SOURCE_VALIDATION_SCHEMA_VERSION"),
}

# ---------------------------------------------------------------------------
# TS5 §19.4.1：`TableObject to-4 + final material` 批的类型登记表
# ---------------------------------------------------------------------------
#
# 与上面两张表**同等权威**、同样分开登记。三张表通过
# `ALL_VERSIONED_OBJECT_SCHEMA_FIELDS` 组成无重名并集，由 `versions.self_check()`、
# `table_schema.self_check()` 与 `schema.self_check()` 共同验证
# "公共类型 ↔ schema 常量"严格双射。

#: TS5 §19.4.4：身份层声明（静态、可审计、**不含业务数据**）。
#:
#: 身份生成顺序是固定的；每一层只能引用**严格更早**的层。`table_schema` 的
#: `create/from_dict` 与 `self_check()` 共用这张表来拒绝回指：TableObject 不得绑定
#: relation / decision / binding / coverage / final snapshot 的任何 ID 或 fingerprint；
#: final snapshot 的 content_fingerprint 不得进入 `upstream_dependency_fingerprint`。
IDENTITY_LAYER_ORDER: tuple[str, ...] = (
    "upstream_dependency",
    "table_object",
    "final_span",
    "range_decision",
    "component_binding",
    "citable_coverage",
    "relation",
    "conservation",
    "synopsis",
    "gap",
    "final_snapshot",
    "capability",
)

#: 每层**允许**引用的更早层（闭包不含自身）。`relation` 是唯一允许引用两个同代成员的层
#: （两个已完成端点），因此它引用 `table_object` 与 `final_span`——二者都严格早于它。
IDENTITY_LAYER_REFERENCES: dict[str, tuple[str, ...]] = {
    "upstream_dependency": (),
    "table_object": ("upstream_dependency",),
    "final_span": ("upstream_dependency",),
    "range_decision": ("upstream_dependency", "table_object", "final_span"),
    "component_binding": ("upstream_dependency", "table_object", "final_span",
                          "range_decision"),
    "citable_coverage": ("upstream_dependency", "table_object", "final_span",
                         "component_binding"),
    "relation": ("upstream_dependency", "table_object", "final_span",
                 "component_binding"),
    "conservation": ("upstream_dependency", "table_object", "final_span",
                     "range_decision", "component_binding", "citable_coverage",
                     "relation"),
    "synopsis": ("upstream_dependency", "table_object", "final_span"),
    "gap": ("upstream_dependency", "table_object", "final_span",
            "range_decision", "component_binding"),
    "final_snapshot": IDENTITY_LAYER_ORDER[:IDENTITY_LAYER_ORDER.index(
        "final_snapshot")],
    "capability": ("final_snapshot", "upstream_dependency"),
}

IDENTITY_LAYERS_ARE_ACYCLIC: bool = all(
    IDENTITY_LAYER_ORDER.index(reference) < IDENTITY_LAYER_ORDER.index(layer)
    for layer, references in IDENTITY_LAYER_REFERENCES.items()
    for reference in references
)

#: TS5 新增顶层 wire 类型（11 个）。每个都有自己的 `schema_version` 与 schema 常量。
#:
#: 第 11 项 `FinalNavigationSynopsis`（§19.0.1 方案 C）与 TS1 的 `NavigationSynopsis`
#: 是**两个类型、两条版本轴**：`nss-3`/`ns-3` vs `nss-2`/`ns-2`。两条登记表各有一项、
#: 指向不同常量——这与 `TableObject`/`TableObjectV4` 共用一条轴的情形相反，是有意的：
#: TS4 synopsis 是**冻结轴**（TS5 不改它），final synopsis 是**新轴**（TS5 新加它）。
TABLE_RECORD_PUBLIC_TYPES: tuple[str, ...] = (
    "TableObjectV4",
    "TableCellV4",
    "FinalOutlineSpan",
    "TableRangeDecision",
    "TableRelation",
    "FinalComponentBinding",
    "TableCitableCoverage",
    "TableStructureGap",
    "FinalMaterialConservation",
    "FinalMaterialStructureSnapshot",
    "FinalNavigationSynopsis",
)

#: TS5 顶层类型名 → (其 schema_version 字段名, 其 schema 版本常量名)。
#:
#: 注意 `TableObjectV4` 与 TS1 的 `TableObject` **共用同一条版本轴**
#: （`TABLE_SCHEMA_VERSION`）：它们描述同一个物理事实的先后两版 wire format，而
#: `TableObject` 现在只是该轴上的历史 reader。两条登记表各有一项、指向同一常量，
#: 这是有意的——它让"旧类仍在登记、但不再代表 current"这件事在自检里可见。
TABLE_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS: dict[str, tuple[str, str]] = {
    "TableObjectV4": ("schema_version", "TABLE_SCHEMA_VERSION"),
    "TableCellV4": ("schema_version", "TABLE_CELL_SCHEMA_VERSION"),
    "FinalOutlineSpan": ("schema_version", "FINAL_SPAN_SCHEMA_VERSION"),
    "TableRangeDecision": (
        "schema_version", "TABLE_RANGE_DECISION_SCHEMA_VERSION"),
    "TableRelation": ("schema_version", "TABLE_RELATION_SCHEMA_VERSION"),
    "FinalComponentBinding": (
        "schema_version", "FINAL_COMPONENT_BINDING_SCHEMA_VERSION"),
    "TableCitableCoverage": (
        "schema_version", "TABLE_CITABLE_COVERAGE_SCHEMA_VERSION"),
    "TableStructureGap": ("schema_version", "TABLE_STRUCTURE_GAP_SCHEMA_VERSION"),
    "FinalMaterialConservation": (
        "schema_version", "FINAL_MATERIAL_CONSERVATION_SCHEMA_VERSION"),
    "FinalMaterialStructureSnapshot": (
        "schema_version", "FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION"),
    "FinalNavigationSynopsis": (
        "schema_version", "FINAL_SYNOPSIS_SCHEMA_VERSION"),
}

#: TS5 新增的 schema 版本常量名（11 个）。
TABLE_RECORD_SCHEMA_VERSION_CONSTANT_NAMES: tuple[str, ...] = tuple(
    TABLE_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS[type_name][1]
    for type_name in TABLE_RECORD_PUBLIC_TYPES
)

#: TS5 新增的算法 / 规则 / capability 版本常量名（8 个）。
#:
#: `FINAL_SYNOPSIS_VERSION` 在这里而**不**在 TS4 的 `SYNOPSIS_VERSION` 位置上：它是
#: final 类型的算法轴，与 TS4 的 `ns-2` 并行，不取代它。
TABLE_RECORD_ALGORITHM_VERSION_CONSTANT_NAMES: tuple[str, ...] = (
    "TABLE_GEOMETRY_VERSION",
    "TABLE_CLASSIFICATION_PROFILE_VERSION",
    "TABLE_CELL_BLOCK_PROFILE_VERSION",
    "TABLE_RELATION_BUILDER_VERSION",
    "FINAL_MATERIAL_BUILDER_VERSION",
    "VERIFIED_FINAL_MATERIAL_ISSUER_VERSION",
    "TS5_FINAL_SPAN_BUILDER_VERSION",
    "FINAL_SYNOPSIS_VERSION",
)

#: 既有 9 项 wire 契约表、TS4 8 项登记表与 TS5 11 项登记表的**无重名并集**（28 项）。
#: 项目级自检必须检查 union，不得只看其中一张表。
ALL_VERSIONED_OBJECT_SCHEMA_FIELDS: dict[str, tuple[str, str]] = {
    **VERSIONED_OBJECT_SCHEMA_FIELDS,
    **SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS,
    **TABLE_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS,
}

#: TS4 新增的 schema 版本常量名（8 个），与既有 9 项组成
#: `ALL_SCHEMA_VERSION_CONSTANT_NAMES`。
SPAN_RECORD_SCHEMA_VERSION_CONSTANT_NAMES: tuple[str, ...] = tuple(
    SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS[type_name][1]
    for type_name in SPAN_RECORD_PUBLIC_TYPES
)

#: 全项目 schema 版本常量名（既有 9 + TS4 8 + TS5 11 − 1 条共用轴 = 27）。
#:
#: 唯一的重复是 `TABLE_SCHEMA_VERSION`：TS1 的 `TableObject`（历史 reader）与 TS5 的
#: `TableObjectV4`（current）登记在**同一条版本轴**上。这不是漏登记，也不允许蔓延成
#: 惯例，因此这里显式去重，并由 `self_check()` 断言"重复集合恰为
#: {TABLE_SCHEMA_VERSION}"——多出任何一条重复都会重新触发 P1。
_ALL_SCHEMA_NAMES_RAW: tuple[str, ...] = (
    SCHEMA_VERSION_CONSTANT_NAMES
    + SPAN_RECORD_SCHEMA_VERSION_CONSTANT_NAMES
    + TABLE_RECORD_SCHEMA_VERSION_CONSTANT_NAMES
)
SHARED_SCHEMA_VERSION_CONSTANT_NAMES: tuple[str, ...] = ("TABLE_SCHEMA_VERSION",)
ALL_SCHEMA_VERSION_CONSTANT_NAMES: tuple[str, ...] = tuple(
    dict.fromkeys(_ALL_SCHEMA_NAMES_RAW))

#: TS4 新增的算法 / 规则版本常量名。全部进入 `VERSION_CONSTANTS` 与自检。
SPAN_RECORD_ALGORITHM_VERSION_CONSTANT_NAMES: tuple[str, ...] = (
    "TS4_BODY_SPAN_BUILDER_VERSION",
    "OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION",
    "SPAN_QUALIFICATION_POLICY_VERSION",
    "OUTLINE_STRUCTURE_PROVIDER_VERSION",
    "EVIDENCE_GATEWAY_PROVIDER_VERSION",
    "ALIGNMENT_TERMINAL_PROVIDER_VERSION",
    "QUALIFICATION_POLICY_PROVIDER_VERSION",
    "VERIFIED_PAGE_LAYOUT_ISSUER_VERSION",
    "VERIFIED_ALIGNMENT_ISSUER_VERSION",
    "VERIFIED_CURRENT_EVIDENCE_AUTHORITY_VERSION",
    "VERIFIED_TS3_HANDOFF_VERSION",
    "PINNED_PAGE_LAYOUT_ISSUER_VERSION",
    "PINNED_ALIGNMENT_ISSUER_VERSION",
    "PINNED_EVIDENCE_AUTHORITY_VERSION",
    "PINNED_TS3_HANDOFF_VERSION",
    "FIXTURE_PAGE_LAYOUT_ISSUER_VERSION",
    "FIXTURE_ALIGNMENT_ISSUER_VERSION",
    "FIXTURE_EVIDENCE_AUTHORITY_VERSION",
    "FIXTURE_TS3_HANDOFF_VERSION",
    "TESTING_TS3_HANDOFF_VERSION",
    "VERIFIED_SPAN_SNAPSHOT_VERSION",
)

#: M930-3 §18.1 A5 / §0.19：图侧正式组合根与逐表完整证明的规则版本常量名（2 个）。
#:
#: 它**不并入** `ALL_MANDATED_VERSION_CONSTANT_NAMES`：该并集的条数
#: （12 + 29 + 19 = 60）被 TS1 / TS4 / TS5 的冻结测试直接断言，按"新批次放新登记表"
#: 的既有先例（见 TS4 8 项与 TS5 11 项的登记理由），本批在此单独登记，仍进
#: `VERSION_CONSTANTS` 与 `ALL_ALGORITHM_VERSION_CONSTANT_NAMES`，由 `self_check()`
#: 一并核对"已声明、字面量形状合法"。
#:
#: `harness/graph_table_release.py` 的 `gto-1` 与 `harness/table_object_materials.py`
#: 的 `tom-1` **不在此表**（各自的 harness 侧版本不并入本表）：本模块的"唯一字面量来源"
#: 约束只覆盖 `document_structure/`，
#: 那是 TS1 §1.1 的模块范围限制，不是"版本必须集中到本文件"的全局要求
#: （既有先例：`harness/table_object_release.RELEASE_RULE_VERSION = "tobj-2"`、
#: `harness/table_object_materials.TABLE_OBJECT_MATERIAL_VERSION = "tom-1"`）。
LIVE_GRAPH_TABLE_ALGORITHM_VERSION_CONSTANT_NAMES: tuple[str, ...] = (
    "LIVE_TABLE_SOURCE_VERSION",
    "TABLE_LOCAL_PROOF_RULE_VERSION",
)

#: 全项目算法 / 规则版本常量名（既有 20 + TS4 21 + TS5 8 + M930-3 图侧 2 = 51）。
ALL_ALGORITHM_VERSION_CONSTANT_NAMES: tuple[str, ...] = (
    ALGORITHM_VERSION_CONSTANT_NAMES
    + SPAN_RECORD_ALGORITHM_VERSION_CONSTANT_NAMES
    + TABLE_RECORD_ALGORITHM_VERSION_CONSTANT_NAMES
    + LIVE_GRAPH_TABLE_ALGORITHM_VERSION_CONSTANT_NAMES
)

#: 全项目被强制登记的版本常量名（既有 12 + TS4 29 + TS5 19 = 60）。
#: `MANDATED_VERSION_CONSTANT_NAMES` 本身保持 12 项不动（冻结测试依赖其内容），
#: TS4/TS5 的强制项并入本并集，由 `self_check()` 一并核对"已声明、形状合法"。
SPAN_RECORD_MANDATED_VERSION_CONSTANT_NAMES: tuple[str, ...] = (
    SPAN_RECORD_SCHEMA_VERSION_CONSTANT_NAMES
    + SPAN_RECORD_ALGORITHM_VERSION_CONSTANT_NAMES
)
TABLE_RECORD_MANDATED_VERSION_CONSTANT_NAMES: tuple[str, ...] = (
    TABLE_RECORD_SCHEMA_VERSION_CONSTANT_NAMES
    + TABLE_RECORD_ALGORITHM_VERSION_CONSTANT_NAMES
)
ALL_MANDATED_VERSION_CONSTANT_NAMES: tuple[str, ...] = (
    MANDATED_VERSION_CONSTANT_NAMES
    + SPAN_RECORD_MANDATED_VERSION_CONSTANT_NAMES
    + TABLE_RECORD_MANDATED_VERSION_CONSTANT_NAMES
)

#: TS4 新增的**子结构**类型名：由父对象携带版本，自身不带 `schema_version`，
#: 但**不得**退化为自由 `dict` / `list`（每个都有冻结字段与顺序 / 唯一键规则）。
SPAN_RECORD_SUBSTRUCTURE_TYPE_NAMES: tuple[str, ...] = (
    "LineStructureState",
    "BoundaryFactor",
    "LayoutHit",
    "ClosedInterval",
    "EvidenceConservationRow",
    "ConservationGap",
    "SnippetCheck",
)

#: 不参与序列化的 TS4 runtime capability 类型名。它们**没有** wire 版本，因为
#: 它们根本不可序列化；把它们写进 wire 登记表才是错误。
SPAN_RECORD_RUNTIME_ONLY_TYPE_NAMES: tuple[str, ...] = (
    "VerifiedTS3Handoff",
    "VerifiedPageLayout",
    "VerifiedCurrentEvidenceAuthority",
    "VerifiedEvidenceSetAlignment",
    "VerifiedSpanSnapshot",
    "SpanBuildInput",
)

#: TS5 **子结构**类型名：由父对象携带版本，自身不带 `schema_version`。
#: 它们同样**不得**退化为自由 `dict` / `list`。
TABLE_RECORD_SUBSTRUCTURE_TYPE_NAMES: tuple[str, ...] = (
    "TableRowV4",
    "TableOwnerRef",
    "TableCellSourceRef",
    "TableCellBlock",
    "TableSourceInterval",
    "TableEndpointRef",
    "FinalSpanEndpointRef",
    "ComponentEndpointRef",
    "ReferenceOccurrenceEndpointRef",
    "TableGapEntry",
    "TableCandidateAuditRow",
    "CitableInterval",
    "ConservationLayer",
    "ConservationTerm",
)

#: TS5 runtime capability 类型名。它们**不可序列化**，因此没有 wire 版本；
#: 把它们写进 wire 登记表才是错误。
TABLE_RECORD_RUNTIME_ONLY_TYPE_NAMES: tuple[str, ...] = (
    "VerifiedFinalMaterialStructureSnapshot",
    "FinalMaterialBuildInput",
)

def check_version_registry(public_types: tuple[str, ...],
                           schema_fields: dict[str, tuple[str, str]],
                           substructure_names: tuple[str, ...] = (),
                           runtime_only_names: tuple[str, ...] = ()) -> tuple[str, ...]:
    """给定一份"公共类型 ↔ schema 版本字段"登记，返回规范问题清单（空 = 通过）。

    这是 `versions.self_check()` 与 `span_schema.self_check()` **共用**的双射校验器：
    两边不得各写一套判定，否则"登记表增了、校验没跟"会静默通过。
    """
    problems: list[str] = []
    public = tuple(public_types)
    if len(set(public)) != len(public):
        problems.append("public_types_duplicated")
    declared = set(schema_fields)
    registered = set(public) - set(substructure_names) - set(runtime_only_names)
    for name in sorted(registered - declared):
        problems.append(f"public_type_without_schema_field:{name}")
    for name in sorted(declared - registered):
        problems.append(f"schema_field_without_public_type:{name}")
    for name, pair in schema_fields.items():
        if not isinstance(pair, tuple) or len(pair) != 2:
            problems.append(f"schema_field_pair_shape:{name}")
            continue
        field_name, constant_name = pair
        if not isinstance(field_name, str) or not field_name:
            problems.append(f"schema_field_name_empty:{name}")
        if constant_name not in VERSION_CONSTANTS:
            problems.append(f"schema_version_constant_undeclared:{name}:{constant_name}")
    return tuple(problems)


# 旧**算法 / 规则**版本：与旧 wire format 版本同理，必须显式识别并拒绝。
#
# 算法版本与 schema 版本是两条独立的改动轴：TS2 最终关闭轮改变的是**对齐规则语义**
# （判定分支顺序 + `ALIGN_MIN` 冻结），wire 字段一个都没变，因此当时
# `ALIGN_SCHEMA_VERSION` 仍是 `als-1`，而 `ALIGNER_VERSION` 升到 `al-2`。
# （TS3 §五 才改 wire：`als-1 → als-2` 新增精确分子字段，见 `LEGACY_SCHEMA_VERSIONS`。）
# 若不登记 `al-1`，旧载荷会被当成
# "未知值"拒绝 —— 仍然是 fail-closed，但无法把"这是被替换掉的旧规则"与"这是打错的
# 字符串"区分开。登记后 `al-1` 得到专门的"须经显式迁移/重算"错误。
LEGACY_ALGORITHM_VERSIONS: dict[str, tuple[str, ...]] = {
    "ALIGNER_VERSION": ("al-1", "al-2"),
    # M930-3 业务纵链补充门：`anp-1` / `anp-2` 是"没有完整标签段判据、没有父节点回退"的
    # 旧导航规则。同一份 Contract + 同一棵标题树在两版下的候选、读根与读集都不同（真实
    # 年报已复现：子项读不到父节点正文、销售模式读根落在披露模板节点上），因此按同一政策
    # 登记：只显式识别并要求显式重算，绝不静默按 `anp-3` 解释。
    #
    # C1 定点返修轮再加入 `anp-3`：它是"topic 标题整段交给该 topic 的**每个** aspect"的
    # 旧祖先层键来源 —— 即"共享父节点即有效材料"的入口。同一份 Contract + 同一棵树在
    # `anp-3` 与 `anp-4` 下，收入构成 / 毛利率 / 产品与方案 / 应用场景 / 产业链位置这些
    # 子项的候选与读集都不同，因此同样只显式识别、要求显式重算，绝不静默按 `anp-4` 解释。
    # r4 后定点返修轮再加入 `anp-4`：它是"本层读根可由**子串**命中产生、祖先层才要完整
    # 标签段"的旧规则 —— 即 `衍生产品情况`／`报告期内的内部控制…`／`关联方及关联交易`
    # 会被当作 `产品与方案`／`期间口径`／`客户当前集中度` 的读根的入口。同一份 Contract +
    # 同一棵树在两版下的候选舍弃原因、读根与读集都不同，因此同样只显式识别、要求显式重算。
    # 业务内容收口轮再加入 `anp-5`：它的祖先层键来源是"question 文本的**每个枚举项**整段
    # 交给该 question 的每个 aspect"，即"兄弟栏目名可充当本栏父节点"的入口
    # （`客户集中度跨期变化` 凭 `关联方` 读走 `十三、关联方及关联交易`）。同一份 Contract +
    # 同一棵树在两版下的祖先层键集合、读根与读集都不同，因此同样只显式识别、要求显式重算。
    # 业务返修第二轮再加入 `anp-7`：它是"祖先层键只按枚举分隔符切、切不开并列连词"的旧
    # 来源 —— 祖先声明是 `供应商当前集中度与集中度跨期变化` 这类**并列复合标签**时
    # `parent_keys` 为空，祖先层回退与主体补读两道门一起关掉。同一份 Contract + 同一棵树
    # 在两版下的祖先层键集合、读根与读集都不同，因此同样只显式识别、要求显式重算。
    # 业务内容质量收口轮再加入 `anp-8`：它是"没有 `navsupp-lift-ancestor-subject-section`
    # 这条上提补读规则"的旧来源 —— 正文挂在子节上的父章节（`四、主营业务分析`）自有正文近零，
    # 加上 `anp-7` 的近似形态判据对 `动力业务` 这类子节标题一个字都对不上，于是连同子树里
    # `（1）动力业务`…那些整段漏读；`anp-9` 正是补这一类。同一份 Contract + 同一棵树在两版下
    # 多出上提补读根与它们展开的读集节点，因此同样只显式识别、要求显式重算。
    "PROFILE_RULE_VERSION": ("anp-1", "anp-2", "anp-3", "anp-4", "anp-5", "anp-6",
                             "anp-7", "anp-8"),
    # 收口轮：`oa-1` 是"逐级夹紧"层级算法 + 全文件 `level_layout` 自证 + 任意位置
    # 句末标点即段落边界的旧规则；`ebi-1` 是"调用方自报身份即可"的旧单块输入契约。
    # 两者按同一政策登记：显式识别、给出"须经显式重算/迁移"的错误，绝不静默解释。
    #
    # 标题资格定点返修轮再加入 `oa-2`：它是"全文件 `by_level` 候选互证 + `level_layout`
    # 可单独充当主证据 + 无通用表格区域资格门"的旧资格模型。它与 `oa-3` 在同一份
    # PageLayout 上得到**不同的节点集**（真实三份文档：681/674/334 → 603/592/320），
    # 因此同样按 legacy 登记：只显式识别与拒绝，绝不静默按新语义解释。
    "OUTLINE_ALGORITHM_VERSION": ("oa-1", "oa-2"),
    "EVIDENCE_BLOCK_INPUT_VERSION": ("ebi-1",),
    # 表格边界收口轮：`hq-1` 是"任意一条强主证据都能无条件穿透表格区域软理由 +
    # `standalone_line` 不看表格成员资格"的旧资格判据；`trg-1` 是"一层
    # `table_region_reason`、邻近即成员资格（判据 B 的可达距离等于仅邻近距离）"的旧
    # 区域资格。两者都会在同一份 PageLayout 上把表格单元格正文收进正式树（真实产物
    # 已复现），因此按同一政策登记：显式识别、要求显式重算，绝不静默按新语义解释。
    # 表格穿透收口轮再加入 `hq-2`：它是"来源佐证**或** ≥2 项版式强证据都能穿透
    # `inside_table`"的旧资格判据 —— 两项样式证明不了"它不是表格内容"，因此同一份
    # PageLayout 在 `hq-2` 下会把居中 / 放大 / 加粗的表头候选采纳成节点。`trg-2` 则是
    # 判据 E 语义仍在同一版本号下变化期间的旧区域资格（产出过中间产物）。两者同样
    # 只显式识别、要求显式重算，绝不静默按新语义解释。
    # 封面/表内信任边界收口轮再加入 `hq-3`：它是"书签（PDF 大纲）与目录项**同等**充当
    # 来源佐证、可穿透 `inside_table`，且正文 landing 行本身不给证据"的旧资格判据 ——
    # 书签序号与来源身份无法由真实版式重建，复核方无从独立验证；同时 TOC 指向的正文
    # landing 行（无编号者）一律被 `style_only_no_structure` 拒收。同一份 PageLayout 在
    # `hq-3` 下采纳的行与 `hq-4` 下采纳的行**不同**（不是只多不少：书签命中的行会退出、
    # TOC landing 行会进入），因此与 `hq-1`/`hq-2` 同一政策：只显式识别、要求显式重算，
    # 绝不静默按新语义解释。`trg-3` 仍是现行值，本轮不改表格区域几何判据。
    "HEADING_QUALIFICATION_PROFILE_VERSION": ("hq-1", "hq-2", "hq-3"),
    "TABLE_REGION_QUALIFICATION_VERSION": ("trg-1", "trg-2"),
    # TOC 正文落地关闭门轮：`tocr-1` 是"目录项确已找到合格正文 landing 行、但该行
    # 没有成为节点 ⇒ 记为 `toc_source_only`，并显式否定『真标题漏收』"的旧状态机。
    # 同一份对账载荷在 `tocr-1` 下表示"目录来源仅有导航价值"、在 `tocr-2` 下表示
    # "可能存在正文标题漏收（阻断）"，含义相反，因此登记为 legacy：只显式识别并
    # 要求显式重算，绝不静默按新语义解释。
    "TOC_BODY_RECONCILIATION_VERSION": ("tocr-1",),
    # TS4 §18.12.3：`ns-1` 是"4 值 reason 词表 + 旧片段选取口径"下的 synopsis 算法。
    # 同一份载荷在 `ns-1` 与 `ns-2` 下可能得到不同的状态与片段，因此登记为 legacy：
    # 只显式识别并要求重算，绝不静默按 `ns-2` 解释。
    #
    # §19.0.1 方案 C：`ns-2` **不在此列**，它是 TS4 的 current 算法轴。TS5 的 `ns-3`
    # 属于另一个类型（`FINAL_SYNOPSIS_VERSION`），不是本轴的"更新版本"，因此也不登记
    # 在这里——把别的类型的版本当成"本类型旧版"会制造一条不存在的迁移路径。
    "SYNOPSIS_VERSION": ("ns-1",),
    # TS5 §19.4.1：`tb-1` 只描述 `to-3` 那套"单页物理片段 + 单行 cell"的历史构造规则。
    # `tb-2` 是"统一结构验证门**没有**相邻冻结范围裁定"的上一版构造规则：同一份
    # `PageLayout` + 冻结 TS4 根在它下面可能少判出表，因此与 `tb-1` 同规登记。
    # `tb-3` 是"冻结侧只有逐范围候选、**没有**连续相邻范围并集候选"的上一版构造规则：
    # 同一份输入在它下面同样可能少判出表（并集候选缺失），因此同样登记为 legacy。
    # `tb-4` 是"冻结范围通道看不到跨页 `table_inside`（`sb-7` 把整条跨页 run 建成一条
    # 跨页处置，`frozen_range_bbox` 对它返回 `None`）"的上一版构造规则：同一份输入在
    # 它下面同样会少判出表（跨页表逐页的对象全缺）。
    # `tb-5` 是"`_union_extends` 允许两个 `None` 节点相等（同页相邻的两张不同表会被并成
    # 一串）且冻结侧没有 `contained_frozen_run` 准入依据"的上一版构造规则：同一份输入在
    # 它下面会把整张物理表切成半张，同样少判出表。
    # 五者都不得被静默当作 `tb-6` 解释，也不得用来给 to-4 对象背书。
    "TABLE_BUILDER_VERSION": (LEGACY_TABLE_BUILDER_VERSION, "tb-2", "tb-3", "tb-4",
                              "tb-5"),
    # TS5 §19.5.1：`tgeo-1` 只报"空洞 / 列带"两类网格问题，**不报**覆盖重叠，因此同一份
    # `PageLayout` 在它下面会把本不闭合的网格当成闭合候选放进建表（随后必然在建对象时
    # 撞上 cell 覆盖重叠）。同一政策：只显式识别、要求显式重算，绝不静默按 `tgeo-2` 解释。
    #
    # `tgeo-2` 再加入本条：它是"无框线行带路径按**左边界**一维聚类列带"的上一版列带判据。
    # 同一份 `PageLayout` 在它下面会把右对齐数字列拆成两个各自只出现在一个行带里的簇，
    # 两簇都被"至少 2 个行带"丢弃，两个片段随后无声地落在同一个 `(band, col)` 上——
    # 网格少列并出现 `unexplained_hole`（实测 2025 年报 p24 一张表也建不出来）。因此
    # 同一份输入在 `tgeo-2` 与 `tgeo-3` 下**得到不同的网格与不同的表集**，只能显式识别、
    # 要求显式重算，绝不静默按 `tgeo-3` 解释。
    #
    # `tgeo-3` 再加入本条：它是"`lines` 通道只有『页面**全部**描边都是行/列边界』一路
    # 检测、没有『只**细长描边**（一维 ≤ `RULE_STROKE_MAX_THICKNESS_PT`、另一维更大）
    # 才是边界』的显式格架第二路"的上一版几何算法。真实电子 PDF 里包住某个词的矩形与
    # 合并表头**内缩框**的边都落在真规线之间，`tgeo-3` 把它们当成额外列，整张物理表被
    # 判成列数虚高、大量 `None` 的骨架并被丢弃（实测 2025 年报 p24 一张表也建不出来；
    # `tgeo-4` 下同页得到 10 行 × 6 列、46 个真实 cell 的整张物理表候选）。因此同一份
    # `PageLayout` 在 `tgeo-3` 与 `tgeo-4` 下**得到不同的网格与不同的表集**，只能显式
    # 识别、要求显式重算，绝不静默按 `tgeo-4` 解释。
    "TABLE_GEOMETRY_VERSION": ("tgeo-1", "tgeo-2", "tgeo-3"),
    # `fmb-1` 是"来源片段区间必须等于整段 `LayoutSpan` 文本长度"的上一版 coverage 生成
    # 规则。它把不存在的排版空白当成来源不完整而直接抛错，因此同一份 `PageLayout` 在
    # `fmb-1` 下**没有快照**、在 `fmb-2` 下有快照。同一政策登记：显式识别、要求显式重算。
    "FINAL_MATERIAL_BUILDER_VERSION": ("fmb-1",),
    # TS4-A 两个 P1 收口轮与三轮表格残余污染收口轮：`sb-2` 是"表格前导行（表题 /
    # 单位行）仍按普通正文成 span 并进入简介"的旧正文算法，`sb-3` 是"与上一行同左边界
    # 即停止闭包"的上一版闭包，`sb-4` 是"已删除同左边界机械停止、但窄折行续行仍会被
    # 单一版式标记误吸收"的上一版闭包，`sb-5` 是"只做前导（向上）闭包、表格结束后的
    # 相邻行仍按普通正文成 span"的上一版闭包，`sb-6` 是"尾部闭包按内容形态判定、命中
    # 即整段丢弃"的上一版闭包，`sb-7` 是"跨页 `table_inside` 仍建成一条跨页处置、不按页
    # 分段"的上一版处置构成。同一份 `PageLayout` 在这七个版本下得到
    # **不同的 span 集**（真实产物已复现），因此按同一政策登记：显式识别、要求显式重算，
    # 绝不静默按 `sb-8` 解释。注意它与 `SPAN_BUILDER_VERSION` 是两条独立的轴：`sb-1`
    # 不是失效 legacy（TS3 unassigned 仍以它为准）。
    "TS4_BODY_SPAN_BUILDER_VERSION": TS4_BODY_SPAN_BUILDER_LEGACY_VERSIONS,
}

# 版本字面量本身的形状约束：`<前缀>-<序号>`，前缀为 2–12 位小写字母
# （引擎类版本如 `pymupdf-1` 的前缀较长），序号为 1–3 位十进制。
# 集中在此，供测试做"无散落字面量"扫描。
VERSION_LITERAL_PATTERN = r"^[a-z]{2,12}-[0-9]{1,3}$"


def all_version_literals() -> tuple[str, ...]:
    """本模块声明的全部版本字面量（不含数值型的 FLOAT_PRECISION / 引擎名）。"""
    return tuple(
        v for k, v in VERSION_CONSTANTS.items()
        if isinstance(v, str) and k != "LAYOUT_ENGINE"
    )


def legacy_versions(constant_name: str) -> tuple[str, ...]:
    """该版本常量的历史值（空元组表示本版本是首个 wire format / 首个算法版本）。

    同时覆盖 wire format 版本与算法/规则版本这两个登记表。
    """
    return LEGACY_SCHEMA_VERSIONS.get(
        constant_name, LEGACY_ALGORITHM_VERSIONS.get(constant_name, ()))


def classify_schema_version(constant_name: str, value: object) -> str:
    """把读到的版本值分类为 `current` / `legacy` / `unknown`。

    `from_dict` 用它对 `legacy` 给出"必须显式迁移"的专门错误，
    对 `unknown` 给出"未知版本"错误；两者都 fail-closed，绝不静默解释。
    """
    current = VERSION_CONSTANTS.get(constant_name)
    if value == current:
        return "current"
    if value in legacy_versions(constant_name):
        return "legacy"
    return "unknown"


def span_confidence_stage() -> str:
    """两门结构（§18.1.1 / §18.14.5）的**唯一**阶段读取点。

    `SPAN_CONFIDENCE_MIN is None` ⇔ TS4-A（`distribution_only`，completion 恒 False）；
    有限值 ⇔ TS4-B（必须与 trusted frozen policy 的 `threshold` 相等）。

    该函数不做任何策略解析（那需要 registry，属于 `span_policy`）：它只回答"当前全局
    阈值处于哪一阶段"，供 A/B 真值表测试与 fail-closed 判定使用。
    """
    if SPAN_CONFIDENCE_MIN is None:
        return "TS4-A"
    return "TS4-B"


def self_check() -> dict:
    """确定性自检（纯函数，无 I/O）：返回常量清单与形状校验结果。

    覆盖范围是**并集**（既有 9 + TS4 8 类型的 schema 登记、既有 20 + TS4 21 的算法
    常量），不得只看其中一张表。
    """
    import re

    pat = re.compile(VERSION_LITERAL_PATTERN)
    bad = [k for k in ALL_MANDATED_VERSION_CONSTANT_NAMES
           if not pat.match(str(VERSION_CONSTANTS.get(k, "<undeclared>")))]
    missing = [k for k in ALL_MANDATED_VERSION_CONSTANT_NAMES
               if k not in VERSION_CONSTANTS]
    # 算法 / 规则版本并集（含 M930-3 图侧组合根）里**每一个**名字都必须真的被声明：
    # 只在某个 `*_NAMES` 里列名、却没在 `VERSION_CONSTANTS` 里给值，会让
    # `legacy_versions()` / 读回路径静默拿到 `None` 并把它当成"未知版本"。
    undeclared_algorithm = [k for k in ALL_ALGORITHM_VERSION_CONSTANT_NAMES
                            if k not in VERSION_CONSTANTS]
    union_problems = list(check_version_registry(
        tuple(ALL_VERSIONED_OBJECT_SCHEMA_FIELDS), ALL_VERSIONED_OBJECT_SCHEMA_FIELDS))
    if undeclared_algorithm:
        union_problems.append(
            f"algorithm_version_undeclared:{sorted(undeclared_algorithm)}")
    if len(ALL_VERSIONED_OBJECT_SCHEMA_FIELDS) != (
            len(VERSIONED_OBJECT_SCHEMA_FIELDS)
            + len(SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS)
            + len(TABLE_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS)):
        union_problems.append("versioned_object_union_name_collision")
    if len(set(ALL_SCHEMA_VERSION_CONSTANT_NAMES)) != len(
            ALL_SCHEMA_VERSION_CONSTANT_NAMES):
        union_problems.append("schema_version_constant_name_collision")
    # TS5：允许的**唯一**共用轴是 `TABLE_SCHEMA_VERSION`（`TableObject` 历史 reader 与
    # `TableObjectV4` current 共享同一版本轴）。再多出任何一条重复即是登记错误。
    duplicated_axes = tuple(sorted(
        name for name in set(_ALL_SCHEMA_NAMES_RAW)
        if _ALL_SCHEMA_NAMES_RAW.count(name) > 1))
    if duplicated_axes != tuple(sorted(SHARED_SCHEMA_VERSION_CONSTANT_NAMES)):
        union_problems.append(f"unexpected_shared_schema_axis:{duplicated_axes}")
    base_names = set(VERSIONED_OBJECT_SCHEMA_FIELDS)
    span_names = set(SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS)
    table_names = set(TABLE_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS)
    overlap = (base_names & span_names) | (base_names & table_names) \
        | (span_names & table_names)
    if overlap:
        union_problems.append(f"wire_type_registered_twice:{sorted(overlap)}")
    for name in SPAN_RECORD_PUBLIC_TYPES:
        if name in SUBSTRUCTURE_TYPE_NAMES or name in RUNTIME_ONLY_TYPE_NAMES \
                or name in SPAN_RECORD_SUBSTRUCTURE_TYPE_NAMES:
            union_problems.append(f"ts4_type_registered_as_substructure:{name}")
    for name in TABLE_RECORD_PUBLIC_TYPES:
        if name in SUBSTRUCTURE_TYPE_NAMES or name in RUNTIME_ONLY_TYPE_NAMES \
                or name in SPAN_RECORD_SUBSTRUCTURE_TYPE_NAMES \
                or name in TABLE_RECORD_SUBSTRUCTURE_TYPE_NAMES \
                or name in TABLE_RECORD_RUNTIME_ONLY_TYPE_NAMES:
            union_problems.append(f"ts5_type_registered_as_substructure:{name}")
    # TS5 §19.4.4：身份 DAG 的静态形状断言。上游依赖指纹、成员身份与 final 终端
    # 指纹分三层，任何一层回填另一层都是 P1。这里只能做**结构**断言（业务对象还没
    # 被构造），真正的环检测在 `table_schema.self_check()` 与 schema 测试里。
    if not IDENTITY_LAYERS_ARE_ACYCLIC:
        union_problems.append("identity_layers_not_acyclic")
    return {
        "version_constant_count": len(VERSION_CONSTANTS),
        "mandated_count": len(MANDATED_VERSION_CONSTANT_NAMES),
        "schema_version_constant_count": len(SCHEMA_VERSION_CONSTANT_NAMES),
        "versioned_object_count": len(VERSIONED_OBJECT_SCHEMA_FIELDS),
        "substructure_type_count": len(SUBSTRUCTURE_TYPE_NAMES),
        "runtime_only_type_count": len(RUNTIME_ONLY_TYPE_NAMES),
        "span_record_public_type_count": len(SPAN_RECORD_PUBLIC_TYPES),
        "span_record_versioned_object_count": len(
            SPAN_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS),
        "table_record_public_type_count": len(TABLE_RECORD_PUBLIC_TYPES),
        "table_record_versioned_object_count": len(
            TABLE_RECORD_VERSIONED_OBJECT_SCHEMA_FIELDS),
        "table_record_substructure_type_count": len(
            TABLE_RECORD_SUBSTRUCTURE_TYPE_NAMES),
        "all_versioned_object_count": len(ALL_VERSIONED_OBJECT_SCHEMA_FIELDS),
        "all_schema_version_constant_count": len(
            ALL_SCHEMA_VERSION_CONSTANT_NAMES),
        "all_mandated_count": len(ALL_MANDATED_VERSION_CONSTANT_NAMES),
        "missing_mandated": missing,
        "malformed_literals": bad,
        "registry_problems": union_problems,
        "align_min": ALIGN_MIN,
        "align_min_decided": ALIGN_MIN is not None,
        "span_confidence_min": SPAN_CONFIDENCE_MIN,
        "span_confidence_min_decided": SPAN_CONFIDENCE_MIN is not None,
        "span_confidence_stage": span_confidence_stage(),
        "float_precision": FLOAT_PRECISION,
    }


if __name__ == "__main__":
    import json
    import sys

    print(json.dumps(self_check(), ensure_ascii=False, indent=2))
    sys.exit(0)

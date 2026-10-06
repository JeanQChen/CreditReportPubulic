"""TS3 生产标题树构建器：由真实 `PageLayout` + 显式来源上下文构建 `DocumentOutline`。

职责边界（`TREE_STRUCTURE_IMPLEMENTATION_PLAN.md` §13.2 的 TS3 行）：

- **S3 正文排版标题是唯一主路径**。S1（目录页条目）与 S2（PDF bookmarks）**只能佐证**
  S3 已识别的真实正文标题、提供"声明页码"提示、形成待审候选。**S1/S2 绝不能直接
  创建 `OutlineNode`**：目录 / 书签找不到真实正文锚点时，一律记为
  `toc_only_candidate` / `bookmark_only_candidate`，**不得**采用最近页面、最相似
  标题或第一处同名文本。
- **扫描全部页面、全部非家具 `LayoutLine`**，不是只扫页首：页中的三级 / 四级 /
  五级小标题必须能被识别（`harness.heading_structure` 的编号层级序覆盖到 5 级）。
- **编号与层级复用 `harness.heading_structure`**（`leading_heading_level` /
  `iter_heading_spans`）。本模块**不**再写一套编号层级规则：层级序复制第二份就是
  第二套真相。
- **层级与身份**：编号深度是主要层级依据；字号秩只作并列打破或无编号标题的辅助
  证据。父节点是**最近的可信前序低层级标题**（栈），不为凑漂亮树补猜父节点。
  编号跳级 / 层级冲突 / 证据不足都**显式记录原因**。同名标题在不同父路径下不合并
  （`structural_path` 进身份）；同页同名但不同 source anchor 不合并（`source_anchor`
  进身份）。`node_id` 由公共类型从完整 structural path + 真实 source anchor 派生。
- **构建信息进 typed audit**，不塞进正式 wire schema：`OutlineBuildResult` 的
  `candidates` / `line_assignments` / `pending` 都是确定性类型化记录，落盘为
  `outline_candidate_audit.json` / `line_assignment_audit.json`。**不得**把构建证据
  写进 `DocumentOutline` 的自由文本字段，**不得**用执行者自报的 confidence 代替真实
  结构信号（本模块没有 confidence 字段，只有可复核的信号与原因码）。
- **内容不得静默消失**：每个非家具 `LayoutLine` 恰好属于一个桶
  （`accepted_heading` / `ordinary_content` / `toc_candidate` /
  `bookmark_candidate` / `unassigned_ambiguous`），守恒性由
  `OutlineBuildResult.assert_conserved` 断言。既不为提升标题精度删除低置信正文，
  也不为提高召回把低置信正文强升为节点。
- **编号只是候选信号，不是采纳依据**。编号格式本身**不构成**标题证据：一行有编号
  只说明它**可能**是标题。采纳必须由可复核的结构证据组合支持 —— 字号高于正文基准、
  居中、与真实目录项 / 书签交叉印证、与同级候选共享版式类（主证据），加上相对正文栏
  的缩进、段落边界（上一行句末结束或本行起页）、处于已采纳标题的上下文（辅证据）；
  并要求"主证据 ≥1 且 主+辅 ≥2"。只含编号与符号的行、排到栏右边缘的折行正文、
  与同基线行并存的表格单元格行、紧邻同层编号列表上下文，全部有**通用**反例守卫，
  证据不足的候选进入显式 `ambiguous` 而非被静默丢弃。
- **正式未归属必须落到 `DocumentOutline.unassigned`**：所有能定位到真实
  `PageLayout` 行、但尚不能形成正式标题节点的编号候选（证据不足 / 同层编号列表
  上下文）必须以权威 `OutlineSpan(role="unassigned", unassigned_reason=...)` 进入
  正式树，并因此参与 `content_fingerprint` / `outline_id` / 读回校验。sidecar 审计
  只是人工视图，**不是**保存未解析结构状态的载体。正文材料切分仍属 TS4：本轮只为
  未归属候选建**单行**显式未归属 span，不构造任何正文材料 span、不做 TS4 的
  `OutlineSpan` 正文边界。

`cross_reference` 为什么在本轮只登记 `pending_ts4`、不产出边：一条 `cross_reference`
边的 occurrence 必须落在**真实 `LayoutLine` 的行内字符区间**上，而它的来源载体只能是
`TEXT_SOURCE_REF_KINDS = ("node", "span", "toc")` 中的真实对象。正文里的"详见第三节"
既不在标题锚点行上（`node:` 要求 occurrence 与标题锚点**同页同行**），也不属于任何
目录项（`toc:`）；它真正的类型化载体是 `OutlineSpan`，而 `OutlineSpan` 的正文边界是
**TS4** 的工作。因此本轮**任何** `cross_reference` 边（无论 resolved 还是 unresolved）
都无法在不伪造来源锚点的前提下表示。本轮的处理是**登记而不伪造**：把扫描到的引用标记
写进 `pending`，附页码 / 行号 / 标记，交给 TS4 在有 `OutlineSpan` 之后正式生成。被禁止
的做法一条不做：不猜目标、不猜最近标题、不把字符串相同当 resolved、不用调用者自报
target。`table_continuation` 属 TS5，本轮同样不生成。

只读边界：本模块只读真实 PDF（`load_source_context` 打开一次、算 sha256、读书签、立即
关闭）与调用方交进来的真实 `PageLayout`。不接触任何数据库、不 init / migrate / 写库、
不联网、不上 LLM。`build_document_outline` 的**纯构建部分不自行寻找工作区文件**：所有
来源都从显式的 `OutlineSourceContext` 取。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from dataclasses import dataclass, replace

from harness.heading_structure import (
    iter_heading_spans,
    leading_heading_level,
    strip_leading_numbering,
)

from document_structure import versions as V
from document_structure.aligner import NUMERIC_LIKE_CHARS
from document_structure.canonical import SchemaValidationError
from document_structure.normalization import tight
from document_structure.schema import (
    CANDIDATE_SOURCES,
    EDGE_KINDS,
    DocumentOutline,
    LayoutLine,
    OutlineNode,
    OutlineSpan,
    PageLayout,
    ReferenceEdge,
    ReferenceOccurrence,
    ReferenceValidationContext,
    TocSource,
    derive_document_outline_locator,
    edge_ref,
    quantize,
)
from document_structure.schema import _normalize_toc_title as normalize_toc_title
from document_structure.schema import _resolve_page_label

# ---------------------------------------------------------------------------
# 常量（全部公司无关；不得出现公司名 / 案例号 / 固定页码 / 表号 / 答案关键词）
# ---------------------------------------------------------------------------

#: `OutlineNode.title` 的公共上限（与公共类型一致，不另立一套）。
MAX_TITLE_LEN: int = 60
#: 标题的最小长度（1 个字符的行不构成标题结构证据）。
MIN_TITLE_LEN: int = 2
#: **无编号**标题的长度上限：无编号时"短行"本身就是一条结构证据。
MAX_UNNUMBERED_TITLE_LEN: int = 40
#: 判定"字号放大"的最小相对差（pt）。
TITLE_SIZE_DELTA_PT: float = 0.75
#: 无编号标题的层级上限（字号秩只作辅助证据，不得无限深）。
MAX_STYLE_LEVEL: int = 4
#: 居中判定容差（pt）。
CENTER_TOLERANCE_PT: float = 12.0
#: 表格区域判定：旧的"连续 ≥ N 行里出现 ≥2 条多栏行"整块排除法已删除（§二.3）——
#: 本版式引擎把每个单元格输出为独立单 span 行，该判据在本引擎上恒不成立。通用网格
#: 资格改为逐行几何判据 `table_region_scopes`（`trg-3`：`inside_table` 成员资格 vs
#: `adjacent_to_table` 邻近，两层状态，见常量区）。
#: 目录页判定：页内出现"目录"类标题行，或页内可解析出 ≥ N 条目录项。
TOC_PAGE_TITLE_TOKENS: tuple = ("目录", "目次", "contents")
TOC_PAGE_TITLE_MAX_LEN: int = 8
TOC_MIN_ENTRIES: int = 3
TOC_MIN_TITLE_LEN: int = 2
#: 重复噪声：同一规范化文本出现在 ≥ N 个不同页且无编号、也无 S1/S2 佐证。
REPEATED_NOISE_MIN_PAGES: int = 3
#: 句末形态：出现这些字符的行按普通正文处理（标题不以句末标点结尾）。
SENTENCE_FINAL_CHARS: str = "。；！？;!?"
#: 表格数据行的排除：低信息量字符占比达到该值即按数据行处理。
NUMERIC_ROW_RATIO: float = 0.80
#: 年份 / 日期型整行（`2025年`、`2024-12-31`、`2025年第3季度`）单独成行即拒。
_RE_YEAR_LIKE = re.compile(
    r"^[12]\d{3}\s*年?\s*[-–~/至]?\s*\d{0,2}\s*[月日]?\s*$"
    r"|^[12]\d{3}\s*年\s*第?\s*[1-4一二三四]\s*季度?\s*$")
#: 金额 / 比例 / 单位型整行（`1,234.56`、`12.34%`、`1,000万元`）。
_RE_AMOUNT_ONLY = re.compile(
    r"^[（(\[【]?\s*[-+]?\s*[¥￥$]?\s*\d{1,3}(,\d{3})*(\.\d+)?\s*"
    r"(%|％|‰|万元|亿元|万|亿|元|股|倍|天|个|百分点)?\s*[）)\]】]?\s*$")
#: 页码 / 页眉页脚形态（`第 3 页`、`3 / 229`、`- 3 -`）。家具行已被排除，这里是
#: 兜底：未被家具检测标记、但形态显然是页码的行不得成为标题。
_RE_PAGE_LABEL_LIKE = re.compile(
    r"^[-—–\s]*(第\s*)?\d{1,4}\s*(页|/\s*\d{1,4}|共\s*\d{1,4}\s*页)?[-—–\s]*$")
#: 目录行的**点线填充**字符（"第一节标题 ......... 1" 里的引导点）。它们属于版式
#: 装饰，不属于标题文字。标题归一**一律**复用 schema 的版本化
#: `_normalize_toc_title`，这里只用于在目录页内切分"标题部分 / 页码部分"。
TOC_FILLER_CHARS: str = ".·…．"
#: 目录项的**结构判据**：标题部分与尾部页标签之间必须有一段 ≥ N 个字符的点线填充。
#: 三条真实 PDF 的目录项全部满足（`..................... 110`），而正文行不会出现
#: 这种填充；因此它把"以数字结尾的正文行"与"真正的目录项"分开，不靠关键词。
TOC_MIN_LEADER_CHARS: int = 3
#: 引用标记的**封闭词表**：只用于登记 `pending_ts4` 候选，本轮不生成任何边。
REFERENCE_MARKERS: tuple = ("详见", "参见", "见第", "如下表", "见下表", "见附图")
REFERENCE_MARKER_MAX_PER_LINE: int = 1
#: 低信息量字符（数字 / 单位 / 年月日 / 序数词）：占比过高即为数据行。
_EXTRA_NUMERIC_CHARS: str = "万亿股元倍天个期度年月日第至"

#: "低置信 / 歧义"候选的原因码（人工复核视图专列一组）：这些候选**有**标题结构
#: 迹象但证据不足以采纳 —— 未采纳即未采纳，既不得删除其正文（它仍按普通正文行
#: 守恒记账），也不得为提高召回把它强升为节点。
LOW_CONFIDENCE_REASON_CODES: tuple = (
    "style_only_no_structure", "repeated_noise", "numbering_style_conflict",
    "insufficient_structural_evidence", "numbered_list_run_context",
)

# ---------------------------------------------------------------------------
# P1-A：编号候选的**结构证据**词表与判定参数
#
# 编号本身**不是**证据。这里把"可能说明这是标题"的版式 / 来源信号分成两层：
# 主证据（版式级或来源级，单独出现即有意义）与辅证据（上下文级，只能加强）。
# 采纳判据是**确定性真值表**：主证据 ≥1 **且** 主+辅 ≥2；不满足的候选一律进入
# 显式 `ambiguous`（→ 正式 `DocumentOutline.unassigned`），既不静默丢弃，也不
# 为提高召回强升。**不要求**每个标题都字号大或加粗：字号与基准相同的小标题通过
# "同级版式类 + 上下文结构"确认。
# ---------------------------------------------------------------------------

#: §二/§三（`hq-4`）：**TOC → 正文 landing** 强主证据码。它取代 `hq-3` 的
#: `toc_or_bookmark`，语义面收窄为一条：候选行是某条**真实目录项**在真实版式上的
#: landing，且该目录项的来源身份、目标行、页标签映射**全部**能由 `PageLayout` 重算。
#:
#: 为什么必须收窄：
#:
#: - 目录项（`TocEntry` + `TocSource`）在版式里**有真实载体**（真实行 + 真实字符区间
#:   + 页标签 furniture），复核方可以逐字段重算身份，因此它是一条可独立复核的来源；
#: - 书签来自 PDF 大纲，**不在** `PageLayout` 里。复核方只能重算"声明的页码 + 标题 ↔
#:   目标行"两条，书签序号与来源身份无从重建。`hq-3` 让书签**同等**充当这条证据并
#:   穿透 `inside_table`，等于把一条不可重建的身份当成了表格成员资格的免罪符。
#:
#: `hq-4` 起书签只作**导航候选 / 审计信息**（`BOOKMARK_NAVIGATION_ONLY_REASON`），
#: 既不进资格算术，也不穿透表格。
TOC_BODY_LANDING_EVIDENCE: str = "toc_body_landing"
#: 主证据：**候选自身**可复核的版式 / 来源级信号（可单独说明"这不是正文行"）。
#:
#: §二.1：`level_layout`（同级候选共享版式类）**不在**本词表里。它曾经可以单独充当
#: 一条候选的主证据，于是"整份文档里另一条同层、字号与 x0 相近的候选"就能给当前
#: 候选提供标题资格 —— 这是**循环自证**，也正是表格行标签 / 截断单元格被误收的入口。
#: 现在它只是辅证据，且只在**同页或相邻页**这一辅助邻近窗口内计算（见
#: `NEARBY_PAGE_WINDOW_PAGES`：那只是一个邻近窗口，**不是**已验证的结构域）。
#: 一条候选的标题资格必须由下面 5 条**它自己**的结构事实之一支撑。
HEADING_PRIMARY_EVIDENCE: tuple = (
    "size_above_body", "centered", "bold_majority", TOC_BODY_LANDING_EVIDENCE,
    "standalone_line",
)
#: 其中**强**主证据：与"位置 / 上下文"无关、单行版面即可确证的版式或来源事实。
#: `standalone_line` 是**弱**主证据：它说明"这一行形态完整、未被截断、也不在通用
#: 表格 / 网格区域内"，但不含任何版式提升。落在表格 / 网格区域内的候选因此**不能**
#: 靠它取得资格（fail-closed 到 `formal_unassigned`）。
HEADING_STRONG_PRIMARY_EVIDENCE: tuple = (
    "size_above_body", "centered", "bold_majority", TOC_BODY_LANDING_EVIDENCE,
)
#: 辅证据：上下文级信号（只能加强主证据，不单独采纳）。
#:
#: `level_layout` 降格到这里（§二.1 允许的用途之一：**同一邻近窗口内的辅助信号**；
#: 这个窗口只是"同页或相邻页"，不是已验证的结构域）。它既不能单独成为主证据，也不能
#: （在没有主证据时）与任一辅证据拼成标题资格 —— 采纳判据始终要求 ≥1 条自身主证据。
HEADING_SUPPORTING_EVIDENCE: tuple = (
    "column_indent", "paragraph_boundary", "heading_context", "level_layout",
)
#: 采纳所需的最小证据数（主 ≥1 且 主+辅 ≥ 此值）。
MIN_HEADING_EVIDENCE: int = 2
#: `level_layout` 的**同页或相邻页窗口**：两条候选必须落在同一页或**相邻**页
#: （`|Δ页| ≤ 此值`）才允许互相出借该辅证据。窗口外候选一律不参与 —— 这是"取消
#: 全文件互证"的可执行定义。
#:
#: **诚实命名**：本常量只能证明"同页或相邻页"，它**不是**任何已被验证的结构域
#: （本模块没有按章节 / 标题族还原结构域的机制）。旧名 `LOCAL_DOMAIN_PAGE_SPAN`
#: 自称"局部结构域"，已删除。
NEARBY_PAGE_WINDOW_PAGES: int = 1

#: 折行正文的**版面**判据（TS3 §四 P1-B）：一行的右端是否已经排到该页正文栏的最右
#: 观测边缘。`level_layout` 是候选之间**互相**提供的证据，因此最容易被"两条彼此相似的
#: 普通编号正文"循环自证；而"排到栏边缘"是**单行自身的真实版面事实**，不依赖任何其它
#: 候选。
#:
#: 判据：`page_right_edge - line.bbox[2]` 落在 `[-WRAP_EDGE_TOL_PT,
#: max(WRAP_EDGE_MIN_PT, WRAP_EDGE_SIZE_RATIO * font_size)]` 内即为折行行。下界的
#: 作用是排除"该行明显比本页最右行还靠右"的情形（两栏页面上，正文行会比另一栏的
#: 标题行更靠右），上界的作用是让"差一点到边缘"的短行不被误判。
WRAP_EDGE_TOL_PT: float = 1.0

#: `paragraph_boundary` 的**真实版面**判据之一：上一非家具行与本行之间的垂直间距
#: （pt）达到该值即视为段落分隔。另一条判据是上一行的**真实行尾**字符（不再是"文本
#: 任意位置出现句末标点"——那会让"上一行中间有句号"的表格 / 履历行凭空获得边界证据）。
PARAGRAPH_GAP_MIN_PT: float = 6.0
#: "字号高于正文基准"的最小差（pt）。只比正文大一点点也算**版式信号**，但**不**等于
#: 明显的排版放大（后者由 `TITLE_SIZE_DELTA_PT` 与粗体一起用于无编号排版路径）。
SIZE_ABOVE_BODY_EPS_PT: float = 0.25
#: 正文栏左 / 右边界估计：只用长度足够的正文行（短行不是栏宽的载体）。
BODY_LEFT_MIN_TIGHT_LEN: int = 10
COLUMN_RIGHT_MIN_TIGHT_LEN: int = 25
#: 缩进判定：行首相对正文栏左边界的**内缩量**上限。
COLUMN_INDENT_MIN_PT: float = 12.0
#: 折行正文判定：行右端距栏右边界 ≤ max(此值, 比例 × 字号) 即"排到栏边缘"。
WRAP_EDGE_MIN_PT: float = 3.0
WRAP_EDGE_SIZE_RATIO: float = 0.5
#: 同基线（表格行）判定：同一基线上还存在起点不同的另一条非家具行。
TABLE_ROW_BASELINE_TOL_PT: float = 3.0
TABLE_ROW_MIN_X_GAP_PT: float = 1.0
#: 同级版式类判定：同一编号层级上存在另一条**字号相同、行首相近**的候选。
LAYOUT_CLASS_SIZE_TOL_PT: float = 0.01
LAYOUT_CLASS_X0_TOL_PT: float = 3.0

# ---------------------------------------------------------------------------
# §二.3：通用表格 / 网格区域资格（`trg-3`：两层状态）
#
# 只用 `PageLayout` 几何（真实 `bbox`），**不依赖**页码、表号、标题原文、
# evidence_id、任何公司专有规则；**不**实现完整 TS5 `TableObject`，也不做表格图谱。
# 本引擎把每个表格单元格输出为**独立单 span 行**，因此不能靠"一行里有多个 span"
# 判表；可用的通用几何事实是：**同一水平带里存在多个不同起点的字段，且这些起点在
# 多行上重复出现**（列锚点）。
#
# `trg-1` 的单一"表格区域"状态把"**是**表格成员"与"**只是**靠近表格"混成一个
# `table_region_reason`，于是两个方向的错误同时出现：表格单元格里的普通承诺正文凭
# 单项版式强证据被无条件穿透，而表格之后的真实标题被当成表内候选。`trg-2` 因此把
# 状态**拆成两层**，逐行只落在其中之一：
#
#   `inside_table`（表格**成员资格**证明；五条判据，任一成立）：
#     A `table_row_own`          —— 本行自己所在的行就是多字段行（本行是其中一格）；
#     B `table_row_anchor_member`—— 邻近（≤ `TABLE_REGION_COLUMN_ANCHOR_REACH_PT`）存在
#                                   多字段行，且**本行起点落在该行的列锚点上**，且本行
#                                   字号等于该行某格的**单元格字号**（列成员资格）；
#     C `table_label_column`     —— 本行是**固定宽度行标签列**的一段（同列折行片段，
#                                   宽度止于该网格第二列起点之前）；
#     D `table_row_band`         —— 本行**上下两侧**都被多字段行夹住（同一表格对象的
#                                   结构行闭合），且字号等于夹住它的行的单元格字号；
#     E `table_label_band`       —— 本行处在两行**同族**多字段行之间的**纯标签带**里
#                                   （同族闭合），且字号等于该表某格的单元格字号。
#                                   实测形态：固定资产 / 无形资产附注表把
#                                   `一、账面原值 / 二、累计折旧 / 三、减值准备 /
#                                   四、账面价值` 排成一条**左侧悬出**的标签带。
#
#   `adjacent_to_table`（**仅**邻近，无任何成员资格证明）：
#     `table_row_adjacent`       —— 本行与多字段行的垂直间距 ≤ `TABLE_REGION_PROXIMITY_PT`，
#                                   但五条成员资格判据一条都不成立。它涵盖"表格结束后
#                                   出现的独立标题""紧接着要出现新表的标题"等形态；
#                                   **单独的距离关系不是 `inside_table` 的充分条件**。
#
# 两层状态在候选、审计记录与持久化候选信息里都**分别**表达（`table_scope`），
# 生产资格只对 `inside_table` 收紧、对 `adjacent_to_table` 不追加任何表格软理由。
# 判据只描述"这一行的局部几何"，不把"文字短"单独当拒绝理由，也不整体排除财务附注
# 小标题：附注小标题若确实带字号提升 / 居中 / 书签佐证（强主证据），仍可采纳；只有
# **无法与表内标签区分**的候选才 fail-closed 到 `formal_unassigned`。
# ---------------------------------------------------------------------------

#: 同一"行"（水平带）判定：两行的 y 区间重叠超过此容差即视为同一带。
TABLE_REGION_ROW_OVERLAP_TOL_PT: float = 2.0
#: 多字段行判定：该带内**起点**（x0）经此间距分簇后 ≥ `TABLE_REGION_MIN_COLUMNS` 簇。
TABLE_REGION_COLUMN_GAP_PT: float = 20.0
TABLE_REGION_MIN_COLUMNS: int = 2
#: 判据 D 的垂直邻近上限（pt）：本行与多字段行的 y 区间间距 ≤ 此值即"被结构行夹住"。
#: 它同时是 `adjacent_to_table` 的**唯一**距离判据（仅邻近，不给成员资格）。
TABLE_REGION_PROXIMITY_PT: float = 8.0
#: 判据 B 的列锚点对齐容差（pt）：本行起点必须落在多字段行的某条列锚点上。
TABLE_REGION_COLUMN_ANCHOR_TOL_PT: float = 3.0
#: 判据 B：列成员资格的字号容差（pt）：本行字号必须等于该多字段行**某格**的字号。
#: 这一条把"行首恰好落在他表列锚点上的真实标题"（字号与单元格不同族）挡在
#: `inside_table` 之外 —— 只有行首对齐、没有字号重合时，行首对齐不再是成员资格证明。
TABLE_CELL_SIZE_TOL_PT: float = 0.5
#: 判据 B 的垂直可达距离。实测依据（三份真实年报，逐条复核）：取
#: `TABLE_REGION_PROXIMITY_PT`(8pt) 时，被宽表单元格列宽截断的编号承诺正文（行首落在
#: 该表第二列锚点上、字号等于单元格字号）离最近的列锚点多字段行 8–30pt，判据 B 够不着，
#: 于是它们凭"恰好页面居中"这一单项版式证据进入正式标题树。把可达距离放到 30pt 并
#: **同时**要求字号等于该行某格字号之后，这些行被正确判为表格成员，而"自成一表的真实
#: 小标题"因为字号与单元格不同族仍不被误收（它们在旧规则下是靠放宽距离被误删的）。
#: 旧注释声称"放宽会把真实小标题一并删掉"，那是在**没有字号成员资格**的规则下的实测；
#: 本轮改的是判据本身（加字号），不是继续放宽容差换节点数。
TABLE_REGION_COLUMN_ANCHOR_REACH_PT: float = 30.0
#: 判据 C 的扩展邻近上限（pt）：标签列片段与其所属网格的行距离可比 B 宽。
TABLE_REGION_EXTENDED_PT: float = 30.0
#: 判据 C：同列折行片段的垂直间距上限与行首 x0 容差。
TABLE_CELL_FRAGMENT_GAP_PT: float = 4.0
TABLE_CELL_FRAGMENT_X0_TOL_PT: float = 12.0
#: 判据 C：片段右端必须止于所属网格**第二列起点**之前（含此容差）。
TABLE_CELL_FRAGMENT_EDGE_TOL_PT: float = 2.0
#: 判据 E：夹住标签带的上下两行多字段行必须**同族**——列数相同，且逐列起点相差不超过
#: 此值（同一张表跨页 / 跨行续排时数值列起点会随位数小幅漂移，故容差比判据 B 宽）。
TABLE_REGION_FAMILY_TOL_PT: float = 12.0
#: 判据 E：标签带里至少要有这么多行，才认定"整段带都是行标签列"。取 3 是为了排除
#: "两行多字段行之间恰好夹了一条短标题行"这种正常排版（那不属于网格内部的标签带）。
TABLE_REGION_BAND_MIN_LINES: int = 3
#: `standalone_line` 的折行续写判据：紧随其后的一行若与之同列（x0 容差）、紧邻
#: （间距上限）且自身不是编号行，则本行是**被截断的折行标题上半行**，不是完整标题行。
WRAP_CONTINUATION_GAP_PT: float = 4.0
WRAP_CONTINUATION_X0_TOL_PT: float = 12.0
#: 只含编号与符号的行：去掉编号记号与标点后剩余的正文字符数下限。
# 去掉编号与标点后**一个实义字符都不剩**才算"只含编号和符号的行"。阈值取 1：
# 单字标题（真实存在）不得因为这个守卫被删除，而 `（3）`、`103、`、`一、` 之类
# 去编号后为空的行走的是**全空**判定，不受阈值影响。
NUMBERING_ONLY_MIN_CHARS: int = 1
#: 编号记号与标点（判定"只有编号和符号"时剔除；不做语义判断）。
NUMBERING_ONLY_STRIP_CHARS: str = (
    "、．.，,：:；;（）()【】[]｛｝{}《》<>「」『』“”\"'’‘·—–-_…~～ \t　/\\|=+*"
    "．·''"
)

#: 硬守卫原因码：形态即决定，不可能成为标题（evidence 不足以否决它）。
NUMBERING_ONLY_REASON: str = "numbering_only_line"
WRAPPED_PARAGRAPH_REASON: str = "wrapped_paragraph_line"
TABLE_CELL_REASON: str = "table_cell_line"
#: §二.3 通用表格 / 网格区域资格的两层状态（`trg-3`）。
#:
#: `TABLE_SCOPE_INSIDE` = 表格**成员资格**已被几何证明（判据 A–E 之一）；
#: `TABLE_SCOPE_ADJACENT` = **仅仅**靠近表格，没有任何成员资格 / 同族闭合 / 行带成员 /
#: 单元格续写证明；`TABLE_SCOPE_NONE` = 与表格无关。两层状态在候选、审计记录与持久化
#: 候选信息（`table_scope=`）里分别表达。
TABLE_SCOPE_INSIDE: str = "inside_table"
TABLE_SCOPE_ADJACENT: str = "adjacent_to_table"
TABLE_SCOPE_NONE: str = "none"
TABLE_SCOPES: tuple = (TABLE_SCOPE_INSIDE, TABLE_SCOPE_ADJACENT, TABLE_SCOPE_NONE)
#: `inside_table` 的五条**成员资格**判据码（进候选审计，用于逐条复核）。
TABLE_REGION_OWN_ROW_REASON: str = "table_row_own"
TABLE_REGION_ANCHOR_MEMBER_REASON: str = "table_row_anchor_member"
TABLE_REGION_LABEL_COLUMN_REASON: str = "table_label_column"
TABLE_REGION_ROW_BAND_REASON: str = "table_row_band"
TABLE_REGION_LABEL_BAND_REASON: str = "table_label_band"
#: `adjacent_to_table` 的判据码：**仅**邻近，不含任何成员资格证明。它**不是**采纳
#: 判据的一部分 —— 邻近表格既不授权穿透，也不构成拒绝理由。
TABLE_REGION_ADJACENT_REASON: str = "table_row_adjacent"
TABLE_REGION_INSIDE_REASONS: tuple = (
    TABLE_REGION_OWN_ROW_REASON, TABLE_REGION_ANCHOR_MEMBER_REASON,
    TABLE_REGION_LABEL_COLUMN_REASON, TABLE_REGION_ROW_BAND_REASON,
    TABLE_REGION_LABEL_BAND_REASON,
)
TABLE_REGION_REASONS: tuple = (
    TABLE_REGION_INSIDE_REASONS + (TABLE_REGION_ADJACENT_REASON,)
)
#: §四 P1-B(3)（`hq-4`）：**目录项 landing 的目标行**若是"普通表格内容"，就不具备成为
#: 正文标题候选的资格 —— 目录项指向它也不得让它进树、更不得让它穿透 `inside_table`。
#:
#: "普通表格内容"取判据 A（`table_row_own`）：它是"本行**自己**就是一条多字段表格行的
#: 一格"这一**单行事实**，来源对象链证明的只是"这条目录项指向这一行"，**不是**"这一行是
#: 标题"。判据 B–E（列锚点成员 / 标签列片段 / 结构行闭合 / 纯标签带）**不**在排除之列：
#: 它们说明的是本行落在表格结构附近，而真实正文标题紧贴表格、甚至正好排在列锚点上正是
#: 常态（§三.3 明令禁止因邻近表格漏收真实标题；§三.4 的"出借方"就是这种形态）。
TOC_LANDING_TABLE_CELL_REASON: str = TABLE_REGION_OWN_ROW_REASON
#: §二（`hq-4`）：`inside_table` 候选的**唯一**穿透依据 —— 经过**对象级验证**的
#: **目录项 → 正文 landing**。已被几何证明属于表格的候选仍然可能是居中、放大或加粗的
#: 表头 / 强调单元格，因此版式样式（`size_above_body` / `centered` / `bold_majority`）
#: 无论出现一项、两项还是三项，都不能单独或组合穿透；普通 supporting 证据更不参与。
#:
#: `hq-4` 相对 `hq-3` 的唯一收窄：**书签不再算数**。书签来自 PDF 大纲，不在
#: `PageLayout` 里，复核方无权重建它的身份（序号 / 来源对象），因此它不能成为
#: "这不是表格内容"的免罪符。只有能由真实版式重建 `TocSource` 身份的目录项 landing
#: 才放行；书签命中照常作为**导航候选**记录，但不穿透。
TABLE_OVERRIDE_SOURCE_LANDING: str = TOC_BODY_LANDING_EVIDENCE
#: 候选审计记录里持久化来源 landing 载荷的字段名（`source_landing=<canonical-json>`）。
#: 独立验收门**不采信**生产侧自报的 `toc_body_landing` 证据码，而是从这个载荷里的
#: 来源对象上下文（`TocSource` 身份、目标行、页标签映射）**自己重算**一遍。
SOURCE_LANDING_FIELD: str = "source_landing"
#: §二.2（`hq-4`）：书签命中的**确定性**登记原因码。书签降为导航候选 / 审计信息：
#: 它**不进**资格算术、**不**使 `safe_table_override()` 为真、**不**使复核侧
#: `available=True`。若生产侧按书签放行了一条复核侧无法重建的节点，两侧分叉即阻断。
BOOKMARK_NAVIGATION_ONLY_REASON: str = "bookmark_navigation_only"
#: 歧义原因码（→ 正式 unassigned；既不采纳也不冒充"已拒绝"）。
AMBIGUOUS_INSUFFICIENT_REASON: str = "insufficient_structural_evidence"
AMBIGUOUS_LIST_RUN_REASON: str = "numbered_list_run_context"
#: §四 P1-B：候选**自身**已排到正文栏右边缘（折行正文的续写行），且它**没有**任何
#: 强主证据（字号抬升 / 居中 / 加粗 / 目录书签佐证）——一律不得采纳，降级为显式未归属。
AMBIGUOUS_WRAPPED_PROSE_REASON: str = "wrapped_numbered_prose"
#: §四 P1-B：候选**自身**的文字形态已经是普通编号正文（子句链 / 定义冒号 + 定义谓词），
#: 而它自身版面又没有任何独立强信号（字号抬升 / 居中 / 目录书签佐证）——采纳它只能靠
#: 弱主证据。判定只看**本条**文本形态，不看其它候选、不看业务词。
AMBIGUOUS_PROSE_SHAPE_REASON: str = "numbered_prose_shape"
#: §二.3：候选落在通用表格 / 网格区域内，且它**没有**强主证据 —— 无法确定它是标题
#: 还是表内行标签 / 被列宽截断的单元格，因此 fail-closed 到 `formal_unassigned`。
#: 诚实的未归属优于污染正式标题树。
AMBIGUOUS_TABLE_REGION_REASON: str = "table_region_candidate"

#: 正式 `OutlineSpan.unassigned_reason` 的**可执行细分原因**（§五 P1-C，公共封闭词表内）。
#: 每一条都对应一个**可判定的**来源，而不是把所有未归属压成一个 `boundary_ambiguous`：
#:
#: - `insufficient_heading_evidence`：正文编号候选，结构证据未达采纳门槛；
#: - `numbered_list_ambiguity`：正文编号候选，处于同层编号列表 / 折行正文上下文；
#: - `toc_unmatched`：目录项（S1）有真实行，但未能在正文里对应到正式节点；
#: - `bookmark_unmatched`：书签（S2）在版式上找到一个真实 landing 行，但未对应到节点；
#: - `hierarchy_conflict`：正文编号候选的声明层级相对当前局部结构域发生**跳级**，
#:   在不猜父节点的前提下无法确定其层级归属。
UNASSIGNED_INSUFFICIENT_EVIDENCE: str = "insufficient_heading_evidence"
UNASSIGNED_NUMBERED_LIST_AMBIGUITY: str = "numbered_list_ambiguity"
UNASSIGNED_TOC_UNMATCHED: str = "toc_unmatched"
UNASSIGNED_BOOKMARK_UNMATCHED: str = "bookmark_unmatched"
UNASSIGNED_HIERARCHY_CONFLICT: str = "hierarchy_conflict"

#: 候选级歧义码 → 正式未归属细分原因的**唯一映射**（确定性、无隐藏分支）。
UNASSIGNED_REASON_BY_SOFT_CODE: dict = {
    AMBIGUOUS_LIST_RUN_REASON: UNASSIGNED_NUMBERED_LIST_AMBIGUITY,
    AMBIGUOUS_WRAPPED_PROSE_REASON: UNASSIGNED_NUMBERED_LIST_AMBIGUITY,
    AMBIGUOUS_PROSE_SHAPE_REASON: UNASSIGNED_NUMBERED_LIST_AMBIGUITY,
    # §二.3：表格 / 网格区域内的候选不是"编号列表歧义"，而是**证据不足以判定它是
    # 标题还是表内标签**，因此归入 `insufficient_heading_evidence`。
    AMBIGUOUS_TABLE_REGION_REASON: UNASSIGNED_INSUFFICIENT_EVIDENCE,
}
#: 未归属 span 的正式原因码全集（本地封闭性自检用）。
UNASSIGNED_FORMAL_REASONS: tuple = (
    UNASSIGNED_INSUFFICIENT_EVIDENCE, UNASSIGNED_NUMBERED_LIST_AMBIGUITY,
    UNASSIGNED_TOC_UNMATCHED, UNASSIGNED_BOOKMARK_UNMATCHED,
    UNASSIGNED_HIERARCHY_CONFLICT,
)

#: §六 P1-D：正式逐行结构归属的**四态封闭集合**。每条可消费正文行必须**确定性**
#: 落入其中之一；四态计数与逐行记录、非家具行数三者必须守恒（不得用旧的
#: `accepted_heading` / `ordinary_content` / `toc_candidate` 桶冒充正式四态）。
LINE_STRUCTURE_HEADING_NODE: str = "heading_node"
LINE_STRUCTURE_FORMAL_UNASSIGNED: str = "formal_unassigned"
LINE_STRUCTURE_BODY_UNDER_NODE: str = "body_under_node"
LINE_STRUCTURE_NON_CONTENT: str = "non_content"
LINE_STRUCTURE_STATES: tuple = (
    LINE_STRUCTURE_HEADING_NODE, LINE_STRUCTURE_FORMAL_UNASSIGNED,
    LINE_STRUCTURE_BODY_UNDER_NODE, LINE_STRUCTURE_NON_CONTENT,
)

#: 本模块使用的算法版本（与公共类型同源；不另立第二套版本常量）。
OUTLINE_BUILDER_VERSION: str = V.OUTLINE_ALGORITHM_VERSION


class OutlineBuildError(SchemaValidationError):
    """来源上下文 / 构建前提错误（fail-closed：不降级、不猜锚点、不猜层级）。"""


# ---------------------------------------------------------------------------
# 来源上下文与其条目类型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BookmarkEntry:
    """S2：一个 PDF 书签。`declared_level` 由 PDF 的层级号减 1 得到（0 基）。"""

    title: str
    declared_level: int
    page_number: int
    ordinal: int

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or self.title == "":
            raise OutlineBuildError("书签标题必须为非空字符串")
        if self.declared_level < 0:
            raise OutlineBuildError(f"书签层级不得为负：{self.declared_level}")
        if self.page_number < 1:
            raise OutlineBuildError(f"书签页码必须从 1 开始：{self.page_number}")
        if self.ordinal < 0:
            raise OutlineBuildError(f"书签序号不得为负：{self.ordinal}")


@dataclass(frozen=True)
class TocEntry:
    """S1：一条**可解析**的目录项（真实行 + 真实字符区间 + 声明的页标签）。"""

    page_number: int
    line_index: int
    char_start: int
    char_end: int
    entry_text: str
    title_part: str
    declared_page_label: str
    declared_depth: int
    ordinal: int

    @property
    def title_normalized(self) -> str:
        return normalize_toc_title(self.title_part)


@dataclass(frozen=True)
class OutlineSourceContext:
    """显式、只读的来源上下文（**唯一**允许进入构建的来源入口）。

    信任边界（TS3 §三.B.2）：

    1. `pdf_sha256` 必须**逐字符等于** `layout.source_file_sha256`，否则立即
       fail-closed —— 书签集与 `PageLayout` 必须来自同一 PDF 字节流；
    2. `document_id` / `company_id` / `document_version` 身份必须闭合到 `layout` 上，
       不得跨文档；
    3. `bookmarks` 可以为空（两份真实 PDF 没有书签），但**不得静默忽略**：空集与
       "有书签"是两种都被显式记录的状态；
    4. `build_document_outline` **不**自行打开任何工作区文件：PDF 只在
       `load_source_context` 里被读一次。
    """

    layout: PageLayout
    pdf_sha256: str
    company_id: str
    document_id: str
    document_version: str
    bookmarks: tuple = ()

    def __post_init__(self) -> None:
        if not isinstance(self.layout, PageLayout):
            raise OutlineBuildError("layout 必须为真实 PageLayout 对象")
        if self.pdf_sha256 != self.layout.source_file_sha256:
            raise OutlineBuildError(
                f"PDF sha256 与 PageLayout.source_file_sha256 不一致："
                f"{self.pdf_sha256!r} != {self.layout.source_file_sha256!r}；"
                f"书签来源与版式必须来自同一 PDF 字节流（fail-closed）")
        for name in ("company_id", "document_id", "document_version", "pdf_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or value == "":
                raise OutlineBuildError(f"{name} 必须为非空字符串")
        if self.document_id != self.layout.document_id:
            raise OutlineBuildError(
                f"document_id 与 PageLayout 不一致：{self.document_id!r} != "
                f"{self.layout.document_id!r}")
        # 公司身份必须**严格相等**：只核对文档号会放过"同一份版式挂到另一家公司"
        # 的错配（FOREIGN_COMPANY）。缺失 / 空值 / 不一致一律 fail-closed。
        if self.company_id != self.layout.company_id:
            raise OutlineBuildError(
                f"company_id 与 PageLayout 不一致：{self.company_id!r} != "
                f"{self.layout.company_id!r}；标题树不得跨公司复用版式"
                f"（fail-closed）")
        if self.document_version != self.layout.document_version:
            raise OutlineBuildError(
                f"document_version 与 PageLayout 不一致：{self.document_version!r} "
                f"!= {self.layout.document_version!r}")
        if not isinstance(self.bookmarks, tuple):
            raise OutlineBuildError("bookmarks 必须为 BookmarkEntry 元组")
        page_count = len(self.layout.pages)
        for index, bookmark in enumerate(self.bookmarks):
            if not isinstance(bookmark, BookmarkEntry):
                raise OutlineBuildError(
                    f"bookmarks[{index}] 必须为 BookmarkEntry，得到 "
                    f"{type(bookmark).__name__}")
            if bookmark.page_number > page_count:
                raise OutlineBuildError(
                    f"bookmarks[{index}] 的页码 {bookmark.page_number} 超出 "
                    f"PageLayout 的 {page_count} 页：书签集与版式不属于同一个 PDF"
                    f"（fail-closed）")


# ---------------------------------------------------------------------------
# 构建结果类型（typed build result / audit；不进 wire schema）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CandidateAuditRecord:
    """一个候选（标题行 / 目录项 / 书签）的**可追溯**审计记录。"""

    kind: str
    page_number: int
    line_index: int
    ordinal: int
    text: str
    accepted: bool
    evidence: tuple
    reason_codes: tuple
    node_id: str | None = None
    declared_level: int | None = None
    applied_level: int | None = None
    toc_declared_page_label: str | None = None
    toc_declared_page_physical: int | None = None
    bookmark_declared_page: int | None = None
    page_conflict: bool = False

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "page_number": self.page_number,
            "line_index": self.line_index,
            "ordinal": self.ordinal,
            "text": self.text,
            "accepted": self.accepted,
            "node_id": self.node_id,
            "declared_level": self.declared_level,
            "applied_level": self.applied_level,
            "evidence": list(self.evidence),
            "reason_codes": list(self.reason_codes),
            "toc_declared_page_label": self.toc_declared_page_label,
            "toc_declared_page_physical": self.toc_declared_page_physical,
            "bookmark_declared_page": self.bookmark_declared_page,
            "page_conflict": self.page_conflict,
        }


@dataclass(frozen=True)
class LineAssignment:
    """一条**非家具** `LayoutLine` 的归属（守恒记账的最小单位）。"""

    page_number: int
    line_index: int
    assignment: str
    node_id: str | None
    reason: str

    def to_dict(self) -> dict:
        return {"page_number": self.page_number, "line_index": self.line_index,
                "assignment": self.assignment, "node_id": self.node_id,
                "reason": self.reason}


@dataclass(frozen=True)
class PendingItem:
    """本轮**明确不做**、交给后续阶段的事项（不得静默丢弃）。"""

    kind: str
    stage: str
    page_number: int
    line_index: int
    detail: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "stage": self.stage,
                "page_number": self.page_number, "line_index": self.line_index,
                "detail": self.detail}


@dataclass(frozen=True)
class OutlineBuildResult:
    """构建结果：正式 `DocumentOutline` + 全部构建证据（typed）。"""

    outline: DocumentOutline
    candidates: tuple
    line_assignments: tuple
    pending: tuple
    toc_sources: tuple
    resolved_edges: tuple
    unresolved_edges: tuple
    stats: dict
    #: 构建所用的 `PageLayout`：**独立验收方**（`structural_review_findings`）必须能
    #: 从持久化版式重新复核每条节点的几何事实，而不是复用生产侧最终的布尔结论。
    #: `None` = 未附着（构造方未提供）—— 此时独立验收 **fail-closed**，不得报 clean。
    layout: "PageLayout | None" = None

    def line_assignment_counts(self) -> dict:
        counts: dict = {}
        for item in self.line_assignments:
            counts[item.assignment] = counts.get(item.assignment, 0) + 1
        return dict(sorted(counts.items()))

    def assert_conserved(self, layout: PageLayout) -> None:
        """每个非家具行恰好归属一个桶（守恒；不静默丢失任何正文行）。"""
        expected = {(page.page_number, line.line_index)
                    for page in layout.pages for line in page.lines
                    if not line.is_furniture}
        actual = {(item.page_number, item.line_index)
                  for item in self.line_assignments}
        if len(actual) != len(self.line_assignments):
            raise OutlineBuildError("行归属记账里出现了重复的 (页, 行)")
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing or extra:
            raise OutlineBuildError(
                f"非家具行记账不守恒：缺失 {missing[:8]} / 多出 {extra[:8]}")


# ---------------------------------------------------------------------------
# 只读来源上下文入口
# ---------------------------------------------------------------------------

def load_source_context(pdf_path, layout: PageLayout, *, company_id: str,
                        document_id: str) -> OutlineSourceContext:
    """**唯一**允许读 PDF 的入口：算 sha256、核对版式、读书签，然后立即关闭。

    - SHA 与 `layout.source_file_sha256` 不一致 → `OutlineBuildError`（fail-closed）；
    - 页数与 `PageLayout` 不一致 → `OutlineBuildError`（书签集不属于同一版式）；
    - 无书签 → `bookmarks=()`（**不是**错误，也不是"忽略"：它被显式记录）；
    - PyMuPDF 不可用 → `OutlineBuildError`（不得退回"没有书签"这种猜测）。
    """
    path = pathlib.Path(pdf_path)
    if not path.is_file():
        raise OutlineBuildError(f"PDF 不存在：{path}")
    return load_source_context_from_bytes(
        path.read_bytes(), layout, company_id=company_id, document_id=document_id,
        source_label=str(path))


def load_source_context_from_bytes(raw_pdf, layout: PageLayout, *, company_id: str,
                                   document_id: str,
                                   source_label: str = "<内存字节>") -> OutlineSourceContext:
    """由**已在内存中的 PDF 字节**构建来源上下文（与 `load_source_context` 同一套核对）。

    TS4 的正式交接必须由"构建这份版式时用的那**同一份字节**"重建大纲：若先验版式、
    后再从磁盘按路径重读，两次读取之间源文件被替换就有一个无法观测的窗口。因此这里
    只接受字节，不再接受路径；sha256 / 页数 / 书签的核对与 `load_source_context`
    逐条一致（后者现在就是本函数的薄封装）。
    """
    data = bytes(raw_pdf)
    sha = hashlib.sha256(data).hexdigest()
    if sha != layout.source_file_sha256:
        raise OutlineBuildError(
            f"PDF sha256 与 PageLayout.source_file_sha256 不一致：{sha!r} != "
            f"{layout.source_file_sha256!r}；不得用另一份 PDF 的书签建构版式的树")
    # 延迟导入：`layout_builder` 只在真正需要读 PDF 时才被加载。
    from document_structure import layout_builder as LB
    if LB._fitz is None:  # pragma: no cover - 环境缺少 PyMuPDF
        raise OutlineBuildError(
            "PyMuPDF 不可用，无法读取 PDF bookmarks；"
            "不得把「读不到」当成「没有书签」")
    doc = LB._fitz.open(stream=data, filetype="pdf")
    try:
        page_count = doc.page_count
        if page_count != len(layout.pages):
            raise OutlineBuildError(
                f"PDF 页数 {page_count} 与 PageLayout 页数 {len(layout.pages)} "
                f"不一致：书签集与版式不是同一份文档")
        raw = doc.get_toc() or []
    finally:
        doc.close()
    bookmarks = []
    for ordinal, item in enumerate(raw):
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        level, title, page = item[0], item[1], item[2]
        if not isinstance(level, int) or level < 1:
            continue
        if not isinstance(page, int) or page < 1 or page > page_count:
            continue
        if not isinstance(title, str) or tight(title) == "":
            continue
        bookmarks.append(BookmarkEntry(title=title, declared_level=level - 1,
                                       page_number=page, ordinal=ordinal))
    return OutlineSourceContext(
        layout=layout, pdf_sha256=sha, company_id=company_id,
        document_id=document_id, document_version=layout.document_version,
        bookmarks=tuple(bookmarks))


# ---------------------------------------------------------------------------
# S1：目录页与目录项
# ---------------------------------------------------------------------------

def _page_is_toc_title(text: str) -> bool:
    flat = tight(text).lower()
    if flat == "" or len(flat) > TOC_PAGE_TITLE_MAX_LEN:
        return False
    return flat in TOC_PAGE_TITLE_TOKENS


def parse_toc_entry(text: str, page_number: int, line_index: int,
                    ordinal: int) -> TocEntry | None:
    """把一条目录行切成（标题部分, 声明的页标签）；切不出即返回 `None`。

    **结构判据**（不靠关键词、不靠公司）：行尾是一段**真实出现的**数字（页标签），
    它与标题部分之间必须有一段 ≥ `TOC_MIN_LEADER_CHARS` 个字符的点线填充。三条真实
    PDF 的目录项全部满足该形态；以数字结尾的**正文行**（如"...P2"）不满足，因此不会
    被误当目录项，也不会把普通页误判成目录页。

    标题部分与页标签都是**原文切片**（因此必然是该行的真实子串），不做任何改写。
    """
    if not isinstance(text, str) or text == "":
        return None
    hits = [(m.start(), m.end()) for m in re.finditer(r"\d+", text)]
    if not hits:
        return None
    start, end = hits[-1]
    label = text[start:end]
    before = text[:start]
    cursor = len(before)
    while cursor > 0 and before[cursor - 1].isspace():
        cursor -= 1
    leader = 0
    while cursor - leader > 0 and before[cursor - leader - 1] in TOC_FILLER_CHARS:
        leader += 1
    if leader < TOC_MIN_LEADER_CHARS:
        return None
    title_part = before[:cursor - leader].strip()
    if len(tight(title_part)) < TOC_MIN_TITLE_LEN:
        return None
    if title_part.endswith(tuple(SENTENCE_FINAL_CHARS)):
        return None
    if len(title_part) > MAX_TITLE_LEN:
        return None
    depth = leading_heading_level(title_part)
    declared_depth = 0 if depth is None else depth - 1
    return TocEntry(page_number=page_number, line_index=line_index, char_start=0,
                    char_end=len(text), entry_text=text, title_part=title_part,
                    declared_page_label=label, declared_depth=declared_depth,
                    ordinal=ordinal)


def collect_toc_entries(layout: PageLayout) -> tuple:
    """返回 `(目录页集合, 目录项元组)`。

    页面判定：页内出现"目录"类标题行，或页内可解析出 ≥ `TOC_MIN_ENTRIES` 条**带点线
    填充**的目录项；从带标题的目录页起，向后**连续**吸收仍有目录项的页面（真实目录
    常跨页，第 2 页往往没有再写"目录"）。**判定成立时页面本身不被丢弃**：它的行仍然
    进入逐行记账（归 `toc_candidate` 桶），只是不参与正文标题识别。
    """
    per_page: dict = {}
    titled_pages: list = []
    for page in layout.pages:
        entries = []
        titled = False
        for line in page.lines:
            if line.is_furniture:
                continue
            if _page_is_toc_title(line.text):
                titled = True
            entry = parse_toc_entry(line.text, page.page_number, line.line_index,
                                    len(entries))
            if entry is not None:
                entries.append(entry)
        per_page[page.page_number] = entries
        if titled:
            titled_pages.append(page.page_number)

    numbers = [page.page_number for page in layout.pages]
    toc_pages: list = []
    for page_number in numbers:
        entries = per_page[page_number]
        if page_number in titled_pages or len(entries) >= TOC_MIN_ENTRIES:
            toc_pages.append(page_number)
    # 目录跨页：带标题目录页之后的连续页若仍有目录项，一并算目录页。
    for page_number in titled_pages:
        index = numbers.index(page_number) + 1
        while index < len(numbers) and per_page[numbers[index]]:
            if numbers[index] not in toc_pages:
                toc_pages.append(numbers[index])
            index += 1
    toc_pages = sorted(set(toc_pages))
    flat: list = []
    for page_number in toc_pages:
        flat.extend(per_page[page_number])
    return tuple(toc_pages), tuple(flat)


# ---------------------------------------------------------------------------
# 版式信号（字号 / 粗体 / 居中 / 表格区域）
# ---------------------------------------------------------------------------

def _line_span_sizes(line: LayoutLine) -> tuple:
    return tuple(quantize(span.size) for span in line.spans
                 if tight(span.text) != "")


def _line_size(line: LayoutLine) -> float:
    sizes = _line_span_sizes(line)
    return max(sizes) if sizes else 0.0


def _line_is_bold(line: LayoutLine) -> bool:
    spans = [span for span in line.spans if tight(span.text) != ""]
    return bool(spans) and all(span.is_bold for span in spans)


def body_font_size(layout: PageLayout) -> float:
    """正文字号 = 非家具行 span 字号按**字符数**加权的众数（确定性）。

    权重用字符数而不是行数：正文行远多于标题行，因此众数就是正文基准。平局取更小
    的字号。没有任何非家具行时返回 0.0（调用方据此认为"无字号证据"）。
    """
    weights: dict = {}
    for page in layout.pages:
        for line in page.lines:
            if line.is_furniture:
                continue
            for span in line.spans:
                length = len(tight(span.text))
                if length == 0:
                    continue
                size = quantize(span.size)
                weights[size] = weights.get(size, 0) + length
    if not weights:
        return 0.0
    return sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _layout_rows(lines: list) -> list:
    """把一页的非家具行按**真实 y 重叠**聚成水平带（"行"），并给出每带的列锚点。

    与旧 `table_zone_lines` 的关键区别：不再要求"一行里有多个 span"。本版式引擎把
    每个表格单元格输出为**独立单 span 行**，`_is_multicolumn` 在这里恒为假，那个
    判据在本引擎上是死代码。通用几何事实是：**同一水平带里有多个不同起点的字段**，
    且这些起点在多行上重复出现。因此这里按 y 重叠聚带、按 x0 间距分列。
    """
    rows: list = []
    for line in sorted(lines, key=lambda item: (item.bbox[1], item.bbox[0])):
        if rows and line.bbox[1] < rows[-1]["y1"] - TABLE_REGION_ROW_OVERLAP_TOL_PT:
            row = rows[-1]
            row["lines"].append(line)
            row["y1"] = max(row["y1"], line.bbox[3])
        else:
            rows.append({"lines": [line], "y0": line.bbox[1], "y1": line.bbox[3]})
    for row in rows:
        starts = sorted(line.bbox[0] for line in row["lines"])
        columns = [starts[0]]
        for value in starts[1:]:
            if value - columns[-1] > TABLE_REGION_COLUMN_GAP_PT:
                columns.append(value)
        row["columns"] = columns
        row["multi"] = len(columns) >= TABLE_REGION_MIN_COLUMNS
    return rows


def _row_vertical_gap(bbox, row: dict) -> float:
    """一个 `bbox` 与一条水平带之间的**真实垂直间距**（重叠记为 0）。"""
    return max(0.0, max(bbox[1] - row["y1"], row["y0"] - bbox[3]))


def _row_cell_sizes(row: dict) -> tuple:
    """一条多字段行里各格的**真实字号**（去重、确定性升序）。"""
    return tuple(sorted({quantize(_line_size(line)) for line in row["lines"]}))


def _row_size_member(line, row: dict) -> bool:
    """本行字号是否等于该多字段行**某格**的字号（列成员资格的字号那一半）。"""
    size = quantize(_line_size(line))
    return any(abs(size - cell) <= TABLE_CELL_SIZE_TOL_PT
               for cell in _row_cell_sizes(row))


def table_region_scopes(layout: PageLayout) -> dict:
    """通用表格 / 网格区域资格（`trg-3`）：返回 `{(页, 行): (状态, 判据码或 None)}`。

    只用真实 `bbox`；不看页码、标题原文、evidence_id，也不用任何业务词表。两层状态
    与五条成员资格判据（A–E）+ 一条仅邻近判据见文件头同名常量区说明。

    **只描述几何，不做采纳决定**：命中的候选由 `_decide_numbered_candidates` 按
    `hq-3` 判据处理 —— `inside_table` 的候选只有在通过**安全穿透资格**时才可采纳，
    否则 fail-closed 到 `formal_unassigned`；`adjacent_to_table` 的候选**不**因邻近
    被追加任何表格软理由（§二.3 / §三.3）。
    """
    result: dict = {}
    for page in layout.pages:
        lines = [line for line in page.lines if not line.is_furniture]
        if not lines:
            continue
        rows = _layout_rows(lines)
        multi = [row for row in rows if row["multi"]]
        if not multi:
            continue
        # 判据 A：本行自己所在的行就是多字段行（本行是其中一格）。
        for row in multi:
            for line in row["lines"]:
                result[(page.page_number, line.line_index)] = (
                    TABLE_SCOPE_INSIDE, TABLE_REGION_OWN_ROW_REASON)
        # 判据 B：邻近多字段行 ∧ 本行起点落在该行的列锚点上 ∧ 本行字号等于该行某格
        # 字号（列**成员资格**，而不是"离得近"）。
        for line in lines:
            key = (page.page_number, line.line_index)
            if key in result:
                continue
            bbox = line.bbox
            for row in multi:
                if _row_vertical_gap(bbox, row) \
                        > TABLE_REGION_COLUMN_ANCHOR_REACH_PT:
                    continue
                if not any(abs(bbox[0] - column)
                           <= TABLE_REGION_COLUMN_ANCHOR_TOL_PT
                           for column in row["columns"]):
                    continue
                if not _row_size_member(line, row):
                    continue
                result[key] = (TABLE_SCOPE_INSIDE,
                               TABLE_REGION_ANCHOR_MEMBER_REASON)
                break
        # 判据 C：固定宽度行标签列的折行片段。
        #
        # 片段必须由**两条都不是表格结构行**的续写行构成：多字段行自身的一格（判据 A
        # 的成员）不是"同一单元格的续写片段"，它与下一条独立行之间是**表格边界**。缺了
        # 这一条，"表格最后一行 + 紧随其后的真实小标题"会被当成一条被列宽截断的单元格
        # 片段（两条行都短、行首同为左列），从而把表后标题误判成表格成员（§三.3 明令
        # 禁止的漏收形态）。这里只承认**非结构行**之间的片段关系。
        structural = {key for row in multi for key in
                      ((page.page_number, line.line_index) for line in row["lines"])}
        for line in lines:
            key = (page.page_number, line.line_index)
            if key in result:
                continue
            bbox = line.bbox
            for other in lines:
                if other is line:
                    continue
                other_key = (page.page_number, other.line_index)
                if other_key in structural:
                    continue
                gap = (other.bbox[1] - bbox[3] if other.bbox[1] >= bbox[3]
                       else (bbox[1] - other.bbox[3]
                             if bbox[1] >= other.bbox[3] else -1.0))
                if not 0.0 <= gap <= TABLE_CELL_FRAGMENT_GAP_PT:
                    continue
                if abs(other.bbox[0] - bbox[0]) > TABLE_CELL_FRAGMENT_X0_TOL_PT:
                    continue
                for row in multi:
                    if _row_vertical_gap(bbox, row) > TABLE_REGION_EXTENDED_PT \
                            or _row_vertical_gap(other.bbox, row) \
                            > TABLE_REGION_EXTENDED_PT:
                        continue
                    edge = row["columns"][1] + TABLE_CELL_FRAGMENT_EDGE_TOL_PT
                    if bbox[2] <= edge and other.bbox[2] <= edge:
                        result[key] = (TABLE_SCOPE_INSIDE,
                                       TABLE_REGION_LABEL_COLUMN_REASON)
                        break
                if key in result:
                    break
        # 判据 E：两行**同族**多字段行之间的**纯标签带**（同族闭合 + 字号成员资格）。
        #
        # "同族闭合"必须真的是**同一表格对象的结构行闭合**：上下两行之间不得再夹着别的
        # 多字段行。缺了这一条，页面上相距几百点的两行同族行（其实是**两张不同的表**）
        # 会把中间的整页正文都圈成"标签带"，于是带内的真实小标题被判成表格成员 ——
        # 而表格成员要过 §三.2 的安全穿透资格，普通小标题过不了，于是一批**无歧义**
        # 的注释小标题被误拒（§三.3 明令禁止的漏收形态）。
        for upper in multi:
            for lower in multi:
                if lower["y0"] <= upper["y1"]:
                    continue
                if len(upper["columns"]) != len(lower["columns"]):
                    continue
                if abs(upper["columns"][0] - lower["columns"][0]) \
                        > TABLE_REGION_FAMILY_TOL_PT:
                    continue
                if any(abs(a - b) > TABLE_REGION_FAMILY_TOL_PT
                       for a, b in zip(upper["columns"], lower["columns"])):
                    continue
                if any(row is not upper and row is not lower
                       and row["y0"]
                       >= upper["y1"] - TABLE_REGION_ROW_OVERLAP_TOL_PT
                       and row["y1"]
                       <= lower["y0"] + TABLE_REGION_ROW_OVERLAP_TOL_PT
                       for row in multi):
                    continue
                value_edge = upper["columns"][1]
                band = [item for item in lines
                        if item.bbox[1] > upper["y1"]
                        and item.bbox[3] < lower["y0"]]
                if len(band) < TABLE_REGION_BAND_MIN_LINES:
                    continue
                if any(item.bbox[0] >= value_edge for item in band):
                    continue
                # 成员资格的字号那一半：带内行的字号必须等于该表某格的字号。缺了它，
                # "两行同族多字段行之间恰好夹着一条真实小标题"会被整段当成标签带。
                if not any(_row_size_member(item, upper) for item in band):
                    continue
                for item in band:
                    result.setdefault((page.page_number, item.line_index),
                                      (TABLE_SCOPE_INSIDE,
                                       TABLE_REGION_LABEL_BAND_REASON))
        # 判据 D：上下两侧都被多字段行夹住（同一表格对象的结构行闭合），且字号等于
        # 夹住它的那些行的某格字号。
        for line in lines:
            key = (page.page_number, line.line_index)
            if key in result:
                continue
            bbox = line.bbox
            ups = [row for row in multi
                   if row["y1"] <= bbox[1] + TABLE_REGION_ROW_OVERLAP_TOL_PT]
            downs = [row for row in multi
                     if row["y0"] >= bbox[3] - TABLE_REGION_ROW_OVERLAP_TOL_PT]
            above = min((_row_vertical_gap(bbox, row) for row in ups),
                        default=float("inf"))
            below = min((_row_vertical_gap(bbox, row) for row in downs),
                        default=float("inf"))
            if above <= TABLE_REGION_PROXIMITY_PT \
                    and below <= TABLE_REGION_PROXIMITY_PT \
                    and any(_row_size_member(line, row) for row in ups + downs):
                result[key] = (TABLE_SCOPE_INSIDE, TABLE_REGION_ROW_BAND_REASON)
        # `adjacent_to_table`：仅邻近（≤ `TABLE_REGION_PROXIMITY_PT`），五条成员资格
        # 判据一条都不成立。**距离本身不是成员资格**，因此这里只登记状态，不产生任何
        # 采纳 / 拒绝后果。
        for line in lines:
            key = (page.page_number, line.line_index)
            if key in result:
                continue
            if any(_row_vertical_gap(line.bbox, row)
                   <= TABLE_REGION_PROXIMITY_PT for row in multi):
                result[key] = (TABLE_SCOPE_ADJACENT,
                               TABLE_REGION_ADJACENT_REASON)
    return result


def table_like_region_lines(layout: PageLayout) -> dict:
    """**只取 `inside_table` 成员**的便利视图：`{(页, 行): 判据码}`（`trg-3`）。

    保留这个入口是为了让"落在表格 / 网格区域内"这一既有问法继续有单一答案；两层
    状态的完整信息在 `table_region_scopes`。
    """
    return {key: reason
            for key, (scope, reason) in table_region_scopes(layout).items()
            if scope == TABLE_SCOPE_INSIDE}


def _is_centered(page, line: LayoutLine) -> bool:
    center = (line.bbox[0] + line.bbox[2]) / 2.0
    return abs(center - page.width / 2.0) <= CENTER_TOLERANCE_PT


def _mode(values: list) -> float | None:
    """确定性众数：出现次数最多者优先，平局取**更小**的值。"""
    counts: dict = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def document_column_right(layout: PageLayout) -> float | None:
    """正文栏右边界估计 = 足够长的非家具行 `bbox[2]` 的**文档级众数**。

    折行正文的后继行、以及长正文行都会排到栏右边缘，因此众数给出真实栏宽；短行
    （居中标题、表格单元格、缩进小标题）不参与估计。没有任何足够长的行时返回
    `None`（此时折行守卫**不可判定**，不参与否决）。
    """
    values = []
    for page in layout.pages:
        for line in page.lines:
            if line.is_furniture:
                continue
            if len(tight(line.text)) < COLUMN_RIGHT_MIN_TIGHT_LEN:
                continue
            values.append(round(line.bbox[2], 1))
    return _mode(values)


def body_column_left(layout: PageLayout, base_size: float) -> float | None:
    """正文栏左边界估计 = 正文号、非编号层级、足够长的行的 `bbox[0]` 众数。"""
    values = []
    for page in layout.pages:
        for line in page.lines:
            if line.is_furniture:
                continue
            if leading_heading_level(line.text.strip()) is not None:
                continue
            if base_size > 0 and _line_size(line) > base_size + SIZE_ABOVE_BODY_EPS_PT:
                continue
            if len(tight(line.text)) < BODY_LEFT_MIN_TIGHT_LEN:
                continue
            values.append(round(line.bbox[0], 1))
    return _mode(values)


def table_row_line_indices(layout: PageLayout) -> set:
    """同基线行（表格行）的 `(页, 行)` 集合。

    本版式引擎把**每个表格单元格输出为独立单 span 行**，因此 `_is_multicolumn`
    在这里恒为假。通用的表格信号是**同一基线上还有另一条起点不同的行**：真实表格
    必然如此，而正文段落每行独占一条基线。只用真实 `bbox`，不依赖 TS5。
    """
    result: set = set()
    for page in layout.pages:
        lines = [line for line in page.lines if not line.is_furniture]
        for index, line in enumerate(lines):
            for other in lines[index + 1:]:
                if abs(other.bbox[1] - line.bbox[1]) > TABLE_ROW_BASELINE_TOL_PT:
                    continue
                if abs(other.bbox[0] - line.bbox[0]) > TABLE_ROW_MIN_X_GAP_PT:
                    result.add((page.page_number, line.line_index))
                    result.add((page.page_number, other.line_index))
    return result


def _wrapped_in_column(line: LayoutLine, column_right: float | None,
                       size: float) -> bool:
    """行右端是否已经排到正文栏右边缘（⇒ 折行正文的续写行）。"""
    if column_right is None:
        return False
    tolerance = max(WRAP_EDGE_MIN_PT, WRAP_EDGE_SIZE_RATIO * size)
    return column_right - line.bbox[2] <= tolerance


def numbering_core_text(text: str) -> str:
    """去掉行首编号记号与全部标点 / 空白后剩下的**实义字符**。

    只用于判定"这一行除了编号什么都没有"（如 `（3）=`、`103、`、`（一）综`），
    不参与层级或标题文本判定。编号记号由 `harness.heading_structure` 的
    `strip_leading_numbering` 切出 —— 本模块不另写一套编号规则。
    """
    title = strip_leading_numbering(text)
    return "".join(ch for ch in tight(title) if ch not in NUMBERING_ONLY_STRIP_CHARS)


# ---------------------------------------------------------------------------
# 假阳性守卫（年份 / 金额 / 比例 / 表格行 / 页码 / 普通短句 / 纯字号）
# ---------------------------------------------------------------------------

def numeric_row_ratio(flat: str) -> float:
    if flat == "":
        return 0.0
    return sum(1 for ch in flat
               if ch in NUMERIC_LIKE_CHARS or ch in _EXTRA_NUMERIC_CHARS) / len(flat)


def false_positive_reasons(text: str, *, numbered: bool) -> tuple:
    """一条标题形态的**假阳性原因码**（空元组 = 未命中任何守卫）。

    这些守卫是 TS3 §五 强制要求的反例面：年份（`2025年`）、金额 / 比例、表格数据
    行、页眉页脚与页码、普通短句，都不得被判成标题。守卫只看**本行**形态，不做
    语义 / 关键词判断。
    """
    flat = tight(text)
    reasons = []
    if flat == "":
        return ("empty_line",)
    if len(flat) < MIN_TITLE_LEN:
        reasons.append("too_short")
    if len(text) > MAX_TITLE_LEN:
        reasons.append("title_too_long")
    if _RE_YEAR_LIKE.match(flat):
        reasons.append("year_like_line")
    if _RE_AMOUNT_ONLY.match(flat):
        reasons.append("amount_or_ratio_line")
    if _RE_PAGE_LABEL_LIKE.match(flat):
        reasons.append("page_label_like_line")
    if numeric_row_ratio(flat) >= NUMERIC_ROW_RATIO:
        reasons.append("numeric_row")
    if any(ch in text for ch in SENTENCE_FINAL_CHARS):
        reasons.append("sentence_final")
    if len(flat) > MAX_UNNUMBERED_TITLE_LEN and not numbered:
        reasons.append("long_unnumbered_line")
    return tuple(reasons)


# ---------------------------------------------------------------------------
# S3：正文标题候选
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HeadingCandidate:
    """一条**正文**标题候选行及其全部可复核信号。"""

    page_number: int
    line_index: int
    line_text: str
    title: str
    title_normalized: str
    numbered: bool
    declared_level: int | None
    font_size: float
    bold: bool
    centered: bool
    style_path: bool
    body_recurrence: int
    toc_matches: tuple
    bookmark_matches: tuple
    rejected: tuple
    x0: float = 0.0
    previous_line_key: tuple | None = None
    primary_evidence: tuple = ()
    supporting_evidence: tuple = ()
    soft_reasons: tuple = ()
    #: §四 P1-B：该行**自身**已排到本页正文栏最右观测边缘（折行正文的续写行）。
    #: 这是单行版面事实，不依赖任何其它候选，因此可以用来切断候选之间的循环自证。
    wrap_flagged: bool = False
    #: §二.3（`trg-3`）：该行**自身**相对通用表格 / 网格的两层状态之一：
    #: `inside_table`（成员资格已被几何证明）/ `adjacent_to_table`（仅邻近）/
    #: `none`（与表格无关）。单行版面事实，取自 `table_region_scopes`。
    table_scope: str = TABLE_SCOPE_NONE
    #: `inside_table` 时给**成员资格判据码**（`TABLE_REGION_INSIDE_REASONS` 之一）；
    #: `adjacent_to_table` 时恒为 `TABLE_REGION_ADJACENT_REASON`（**只说明"邻近"这一
    #: 状态，不是成员资格证明**，也不产生任何采纳 / 拒绝后果）；`none` 时为 `None`。
    #: 采纳判据只按 `table_scope` 分支，绝不按本字段是否存在分支 —— 否则会把仅仅邻近
    #: 表格的真实标题一并拒绝（§三.3）。
    table_region_reason: str | None = None
    #: §二.2：紧邻下一行是本行的**同列折行续写**（⇒ 本行是被列宽截断的标题上半行，
    #: 不是完整标题行）。单行版面事实，只读本行与紧随其后的那一行原始 `LayoutLine`。
    wrap_continuation: bool = False
    #: §三.4（`hq-4`）：本行作为**已验证目录项的 landing** 时，该目录项**声明的层级
    #: 路径深度**（`TocEntry.declared_depth`，0 基）。它只做一件事：给这条正文标题行
    #: 提供层级来源。无编号的 landing 行若只按字号秩定级，会把不同目录深度的同级标题
    #: 串成父子；有了真实目录路径深度，层级就来自**已验证的目录路径**本身。
    #: 只在 `TOC_BODY_LANDING_EVIDENCE` 成立时非 `None`；其它候选一律 `None`，
    #: 层级照旧由编号 / 字号决定（**不**改变任何既有路径的行为）。
    navigation_level: int | None = None

    @property
    def structural_evidence_ok(self) -> bool:
        """**编号候选**的采纳条件：主证据 ≥1 且 主证据 + 辅证据 ≥2。

        编号格式本身**不在**证据集合里：只含编号的候选无论编号多"标准"都不够。
        """
        return (len(self.primary_evidence) >= 1
                and len(self.primary_evidence) + len(self.supporting_evidence)
                >= MIN_HEADING_EVIDENCE)

    @property
    def intrinsic_primary_evidence(self) -> tuple:
        """主证据里属于**候选自身**可复核事实的那些（`hq-3` 的封闭集合）。

        §二.1/§二.2：标题资格**必须**由这些证据之一支撑；`level_layout` 之类的
        候选间相似性一律不算候选自身证据，因此永远不会出现在这个元组里。
        """
        return tuple(code for code in self.primary_evidence
                     if code in HEADING_PRIMARY_EVIDENCE)

    @property
    def strong_primary_evidence(self) -> tuple:
        """主证据里的**强**版式 / 来源证据（不含弱主证据 `standalone_line`）。"""
        return tuple(code for code in self.primary_evidence
                     if code in HEADING_STRONG_PRIMARY_EVIDENCE)

    @property
    def accepted_by_numbering(self) -> bool:
        return (self.numbered and not self.rejected
                and not self.soft_reasons and self.structural_evidence_ok)

    @property
    def accepted_by_style(self) -> bool:
        return (not self.numbered and self.style_path and not self.rejected)

    @property
    def accepted_by_toc_landing(self) -> bool:
        """§三.3（`hq-4`）：本行是**已验证目录项的正文 landing**。

        这条路径**与编号 / 版式无关**：一条正文一级标题可能既不居中、字号也不高于
        正文、更没有编号（真实年报的"致股东的信"就是这样）。它的标题资格来自**来源
        对象链**：目录项 → 声明的页标签 → 唯一物理页 → 该页上文本一致的真实
        `LayoutLine`，四段全部能在真实 `PageLayout` 上重算。

        `TOC_BODY_LANDING_EVIDENCE` 只在**硬拒绝为空**时才会被授予（见
        `scan_heading_candidates`），因此"页眉页脚 / 普通表格内容 / 数字行 / 噪声 /
        目录页行本身"都已经在授予之前被排除，这里无需重复判定。
        """
        return (not self.rejected
                and TOC_BODY_LANDING_EVIDENCE in self.primary_evidence)

    @property
    def ambiguous(self) -> bool:
        """编号候选但证据不足 / 处于同层编号列表上下文：**显式**未归属，不静默丢弃。"""
        return (self.numbered and not self.rejected
                and not self.accepted_by_numbering
                and not self.accepted_by_toc_landing)

    @property
    def ambiguous_reason(self) -> str | None:
        """候选级歧义码（**可执行**的细分原因，不再一律压成同一个笼统值）。"""
        if not self.ambiguous:
            return None
        for code in self.soft_reasons:
            if code in UNASSIGNED_REASON_BY_SOFT_CODE:
                return code
        if self.soft_reasons:
            return AMBIGUOUS_LIST_RUN_REASON
        return AMBIGUOUS_INSUFFICIENT_REASON

    @property
    def formal_unassigned_reason(self) -> str:
        """正式 `OutlineSpan.unassigned_reason`（公共封闭词表内的 5 类之一）。

        `hierarchy_conflict` 不由候选自身判定：它取决于该候选在文档顺序上的**局部结构
        域**（跳级），因此在树装配那一遍由调用方按 `unassigned_reason_for(...)` 传入。
        """
        return unassigned_reason_for(self)

    @property
    def accepted(self) -> bool:
        return (self.accepted_by_numbering or self.accepted_by_style
                or self.accepted_by_toc_landing)


def toc_landing_target_is_body_line(table_scope: str,
                                    table_region_reason: "str | None") -> bool:
    """§四 P1-B(3)（`hq-4`）：目录项 landing 的**目标行**是否有资格作正文行。

    这是"目录来源"与"正文落地"分工里的最后一道**单行事实**判据：只有**不是普通表格
    内容**的行才可能成为 `OutlineNode`。判据只看该行自身在 `trg-3` 里的两层状态：

    - `table_scope == inside_table` 且判据码是 `table_row_own`（本行自己就是一条多字段
      表格行的一格）⇒ **不是**正文行。目录项指向它只说明"目录里有这个字符串"，不说明
      "这一行是标题"；
    - 其余全部为真：`inside_table` 的其它判据（列锚点成员 / 标签列片段 / 结构行闭合 /
      纯标签带）、`adjacent_to_table`、`none`。

    **为什么只排除判据 A**：判据 A 是"本行**自己**是这条多字段行的一格"——它就是普通
    表格内容本身。判据 B–E 说明的是本行落在表格结构附近，而真实正文标题紧贴表格、甚至
    正好排在列锚点上正是常态（§三.3 禁止因邻近表格漏收标题）。把 B–E 一并排除会把
    §三.4 的"出借方"这类**真实**标题一起挡在树外 —— 那是另一种漏收。

    本函数**不**依赖任何业务词、页码或文档身份；`table_scope` / 判据码由调用方从真实
    `PageLayout` 的 `table_region_scopes()` 取得，两侧结论因此可逐条比对上。
    """
    return not (table_scope == TABLE_SCOPE_INSIDE
                and table_region_reason == TOC_LANDING_TABLE_CELL_REASON)


def verified_toc_landings(*, layout: PageLayout, page_number: int,
                          line_index: int, landing_title: str,
                          toc_matches: tuple) -> tuple:
    """候选行上经过**对象级验证**的目录项 landing 条目（`hq-4`）。

    这是 `inside_table` 候选穿透表格成员资格的**唯一**依据，也是正文 landing 取得
    标题资格的来源，因此这里的每一条都必须是**可被独立重算**的对象事实，而不是
    "证据码里写着 `toc_body_landing`"：

    1. **页标签映射**：目录行里真实出现的页标签，必须由真实版式的页码 furniture 唯一
       映射到**本候选所在物理页**（`_resolve_page_label`，不做"最近页 / 固定偏移"猜测）；
    2. **目标行**：本候选所在的那一行真实存在，且其归一化文本等于目录项的归一化标题
       （`normalize_toc_title`）—— 目录项指向的是**这一行**，不只是"某处标题同名"；
    3. **来源对象身份**：`TocSource.create(...)` 必须能在**真实版式**上重建该目录项的
       身份并通过 `verify_against_layout`（行内容摘要、字符区间逐字符一致）。位置或
       文本一变，`toc_source_id` 随之改变；
    4. **声明的页标签真实存在**：标签由 `TocSource.__post_init__` 要求逐字符出现在目录
       行文本里，自报一个原文没有的标签不是来源证据。

    这四段只证明"**这条目录项指向这一行**"，**不**证明"这一行是正文标题"。因此调用方
    还必须先过 §四 P1-B(3) 的单行判据 `toc_landing_target_is_body_line(...)`：目标行本身
    是普通表格内容（判据 A：本行自己就是多字段表格行的一格）时，**不装配** landing 链。

    缺任何一条即丢弃该候选目录项（该条目录项随后按 `toc_unmatched` 显式未归属）。
    返回规范化字典的元组，按 `candidate.toc_matches` 的出现顺序 —— 确定性、可直接
    序列化进候选审计记录。
    """
    entries: list = []
    if layout is None:
        return ()
    for entry in toc_matches:
        label = entry.declared_page_label
        if not label:
            continue
        if _resolve_page_label(layout, label) != page_number:
            continue
        # 目录项的**标题部分**（不是整条目录行 —— 整行还带着点线填充与页标签）必须
        # 等于候选行的归一化标题：目录项指向的是**这一行**。目录行本身另由下面的
        # `TocSource.create` / `verify_against_layout` 逐字符核验。
        if entry.title_normalized != landing_title:
            continue
        try:
            source = TocSource.create(
                layout=layout, page_number=entry.page_number,
                line_index=entry.line_index, char_start=entry.char_start,
                char_end=entry.char_end, declared_page_label=label)
            source.verify_against_layout(layout)
        except Exception:
            continue
        entries.append({
            "kind": "toc",
            "toc_source_id": source.toc_source_id,
            "toc_source_locator": source.toc_source_locator,
            "toc_builder_version": source.toc_builder_version,
            "source_page": source.page_number,
            "source_line": source.line_index,
            "char_start": source.char_start,
            "char_end": source.char_end,
            "entry_text": source.entry_text,
            "title_normalized": landing_title,
            "declared_page_label": label,
            # §三.4：目录项**声明的层级路径深度**（0 基）。它由目录行自身的编号形态
            # 得出，是这条 landing 的层级来源；复核侧可用自己重算的 `TocEntry` 比对。
            "declared_depth": entry.declared_depth,
            "physical_page": page_number,
            "landing_page": page_number,
            "landing_line": line_index,
        })
    return tuple(entries)


def verified_source_landings(candidate: HeadingCandidate,
                             layout: PageLayout) -> tuple:
    """候选**自身**行上的全部来源 landing 条目（`hq-4`：目录项 **+** 书签导航）。

    返回顺序确定：先目录项（`kind="toc"`，已对象级验证）后书签（`kind="bookmark"`）。

    **书签的信任边界（§二.2，`hq-4` 的关键收窄）**：书签必须同时满足两条才被列入 ——
    书签声明的物理页等于**本候选所在物理页**，且其归一化标题等于该行归一化文本。但
    即便两条都满足，书签**也只被登记为 `bookmark_navigation_only`**：书签来自 PDF
    大纲，**不在** `PageLayout` 里，复核方能够重算的只有"声明页码 + 标题 ↔ 目标行"这两
    条，书签序号（`ordinal`）与来源对象身份无从重建。因此书签条目：

    - **不**使 `safe_table_override()` 为真（只有 `kind="toc"` 参与穿透，见该函数）；
    - **不**使复核侧 `_independent_source_landing_facts(...)["available"]` 为真；
    - 只作导航候选 / 审计信息，并带上确定性原因码 `bookmark_navigation_only`。
    """
    if layout is None:
        return ()
    page_number = candidate.page_number
    line = layout.line_at(page_number, candidate.line_index)
    if line is None:
        return ()
    landing_title = normalize_toc_title(line.text)
    entries: list = list(verified_toc_landings(
        layout=layout, page_number=page_number, line_index=candidate.line_index,
        landing_title=landing_title, toc_matches=candidate.toc_matches))
    for bookmark in candidate.bookmark_matches:
        if bookmark.page_number != page_number:
            continue
        if normalize_toc_title(bookmark.title) != landing_title:
            continue
        entries.append({
            "kind": "bookmark",
            "ordinal": bookmark.ordinal,
            "title": bookmark.title,
            "title_normalized": landing_title,
            "declared_level": bookmark.declared_level,
            "bookmark_page": bookmark.page_number,
            "landing_page": page_number,
            "landing_line": candidate.line_index,
            # §二.2：书签**只**是导航候选 / 审计信息 —— 两条可复核条件都成立也不改变
            # 它不可重建身份这一事实。原因码是确定性的、逐条落盘，不藏在总括里。
            "navigation_only": True,
            "navigation_reason": BOOKMARK_NAVIGATION_ONLY_REASON,
        })
    return tuple(entries)


def landing_navigation_level(landings: tuple) -> int | None:
    """从已验证的**目录项** landing 里取该行的导航层级（§三.4；无则 `None`）。

    只读 `kind="toc"` 的条目 —— 书签是导航候选，**不**提供层级（它同样不可重建）。
    多条目录项指向同一行时取**最小**深度：最深的那条声明说明这一行至少处在那个层级，
    而节点实际深度由树装配的局部结构域决定（同一条 landing 只提供"声明的路径深度"，
    不直接决定最终 `effective_depth`）。
    """
    depths = [entry["declared_depth"] for entry in landings
              if entry.get("kind") == "toc"
              and isinstance(entry.get("declared_depth"), int)]
    return min(depths) if depths else None


def _source_landing_json(candidate: HeadingCandidate, layout: PageLayout) -> str:
    """`source_landing=` 的持久化载荷（规范 JSON；无来源 landing 时是空串）。"""
    entries = verified_source_landings(candidate, layout)
    if not entries:
        return ""
    return json.dumps(list(entries), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def safe_table_override(candidate: HeadingCandidate,
                        *, layout: PageLayout | None = None) -> bool:
    """表内候选的**安全穿透资格**（`hq-4`）：唯一依据是对象级验证过的**目录项** landing。

    已被几何证明属于表格的候选（`inside_table`）**不**凭版式样式穿透：一条居中 / 放大
    / 加粗的行仍然可以是表头或强调单元格。因此 `size_above_body`、`centered`、
    `bold_majority` 无论出现一项、两项还是三项，都**不能**单独或组合穿透；普通
    supporting 证据（`column_indent` / `paragraph_boundary` / `heading_context` /
    `level_layout`）更不参与计数。

    唯一许可的穿透依据是 `verified_toc_landings`：候选自身行是某个**目录项**的真实
    landing（页标签唯一映射到本页、目标行文本一致、`TocSource` 身份可在真实版式上
    重建并通过校验）。

    **书签不算数（§二.2）**：本函数**只**统计 `kind="toc"` 的条目。书签命中即便两条
    可复核条件都满足，也只在审计载荷里以 `bookmark_navigation_only` 登记 —— 它的来源
    身份无法由 `PageLayout` 重建，因此不能让一条已证明属于表格的行脱身。没有目录项
    landing 的表内候选一律进入 `formal_unassigned` —— 取消其 `OutlineNode` 资格，
    但**不删除内容、不静默丢失**。

    `layout` 缺失（或为 `None`）时一律返回 `False`：无法完成对象级验证时 fail-closed，
    不得凭证据码列表放行。
    """
    if TABLE_OVERRIDE_SOURCE_LANDING not in candidate.strong_primary_evidence:
        return False
    if layout is None:
        return False
    return any(entry.get("kind") == "toc"
               for entry in verified_source_landings(candidate, layout))


def _matches_for(title_normalized: str, entries: tuple, bookmarks: tuple) -> tuple:
    toc = tuple(e for e in entries if e.title_normalized == title_normalized)
    marks = tuple(b for b in bookmarks
                  if normalize_toc_title(b.title) == title_normalized)
    return toc, marks


def unassigned_reason_for(candidate: HeadingCandidate,
                          jump: bool = False) -> str:
    """一条未归属候选的**正式**细分原因（§五 P1-C 的 5 类之一；确定性映射）。

    `jump=True` 表示该候选在文档顺序上的局部结构域里发生跳级（调用方在装配那一遍按
    当前层级栈算出），此时层级归属无法在不猜父节点的前提下确定 —— 归为
    `hierarchy_conflict`，而不是含糊的"边界不明"。
    """
    if jump:
        return UNASSIGNED_HIERARCHY_CONFLICT
    code = candidate.ambiguous_reason or AMBIGUOUS_INSUFFICIENT_REASON
    if code in UNASSIGNED_REASON_BY_SOFT_CODE:
        return UNASSIGNED_REASON_BY_SOFT_CODE[code]
    return UNASSIGNED_INSUFFICIENT_EVIDENCE


def page_right_edges(layout: PageLayout) -> dict:
    """每页正文栏**最右观测边缘** = 该页非家具、长度足够（≥ `COLUMN_RIGHT_MIN_TIGHT_LEN`）
    的行的最大右端 x。

    用**最大**值而不是众数：两栏页面里，众数会选中另一栏（那一栏行数更多）的右边界，
    从而把本文栏的折行行算成"没到边缘"。取最大值只描述"这一页上文字排到过哪里"，
    是单页版面事实，不含任何候选之间的比较。
    """
    edges: dict = {}
    for page in layout.pages:
        best = None
        for line in page.lines:
            if line.is_furniture:
                continue
            if len(tight(line.text)) < COLUMN_RIGHT_MIN_TIGHT_LEN:
                continue
            right = float(line.bbox[2])
            if best is None or right > best:
                best = right
        if best is not None:
            edges[page.page_number] = best
    return edges


def _wrap_flagged(line, size: float, edge: float | None) -> bool:
    """该行是否已排到本页正文栏最右边缘（§四 P1-B 的折行判据）。"""
    if edge is None:
        return False
    slack = float(edge) - float(line.bbox[2])
    return (-WRAP_EDGE_TOL_PT
            <= slack <= max(WRAP_EDGE_MIN_PT, WRAP_EDGE_SIZE_RATIO * size))


def _wrap_continuation_flagged(line, following, size: float) -> bool:
    """本行是否是**同列折行续写**的上半行（§二.2 的 `standalone_line` 判据之一）。

    成立当且仅当紧随其后的那一非家具行：垂直间距 ≤ `WRAP_CONTINUATION_GAP_PT`、
    行首 x0 与之相差 ≤ `WRAP_CONTINUATION_X0_TOL_PT`、且**自身不是编号行**。
    只读本行与下一行的真实 `bbox` / 文本形态，不看任何其它候选、业务词或页码。

    被列宽截断的表内单元格正是这个形态（`一、账面原值` 之后紧跟 `1.期初余额`
    之类的下一行同一列文本）；而完整标题行之后通常是正文段落或另一条编号标题。
    """
    if following is None:
        return False
    gap = float(following.bbox[1]) - float(line.bbox[3])
    if not 0.0 <= gap <= WRAP_CONTINUATION_GAP_PT:
        return False
    if abs(float(following.bbox[0]) - float(line.bbox[0])) \
            > WRAP_CONTINUATION_X0_TOL_PT:
        return False
    return leading_heading_level(following.text.strip()) is None


def _paragraph_boundary(previous, current) -> bool:
    """`paragraph_boundary` 的**真实**判据（§四 P1-B(3)）。

    成立当且仅当：本行是页/版式序列的第一行；或上一非家具行的**真实行尾**是句末标点；
    或上一行与本行之间存在真实垂直间距（≥ `PARAGRAPH_GAP_MIN_PT`）。

    **不是**"上一行文本里任意位置出现句号" —— 那会让"上一行中间含句号的表格行 / 履历
    行"凭空提供段落边界，等于用一个与版面无关的字符出现位置冒充版面信号。
    """
    if previous is None:
        return True
    if previous.text.rstrip()[-1:] in SENTENCE_FINAL_CHARS:
        return True
    gap = float(current.bbox[1]) - float(previous.bbox[3])
    return gap >= PARAGRAPH_GAP_MIN_PT


def scan_heading_candidates(context: OutlineSourceContext) -> tuple:
    """扫描**全部页面、全部非家具行**，产出标题候选（含被拒候选与原因码）。

    无编号标题有两条接受路径：
    - `style_heading`：bold + 字号高于正文基准 + 短行 + 无句末标点 + 不在正文里复现；
    - `corroborated_heading`：归一标题与某条目录项 / 书签一致（S1/S2 佐证）。
    **只因字号较大、既无编号也无 S1/S2 佐证**的正文行因此被拒
    （`style_only_no_structure`）。
    """
    layout = context.layout
    toc_pages, toc_entries = collect_toc_entries(layout)
    table_scopes = table_region_scopes(layout)
    base_size = body_font_size(layout)
    column_right = document_column_right(layout)
    body_left = body_column_left(layout, base_size)
    table_rows = table_row_line_indices(layout)
    right_edges = page_right_edges(layout)

    occurrence_pages: dict = {}
    for page in layout.pages:
        if page.page_number in toc_pages:
            continue
        for line in page.lines:
            if line.is_furniture:
                continue
            occurrence_pages.setdefault(tight(line.text), set()).add(
                page.page_number)

    candidates: list = []
    for page in layout.pages:
        naked = [line for line in page.lines if not line.is_furniture]
        for position, line in enumerate(naked):
            title = line.text.strip()
            flat = tight(title)
            numbered = leading_heading_level(title) is not None
            rejected = list(false_positive_reasons(title, numbered=numbered))
            soft: list = []
            # §二.3（`trg-3`）：表格 / 网格**不再是**硬拒绝，也不再是单一"区域"状态。
            # 候选自身带上两层状态之一：`inside_table`（成员资格已被几何证明）或
            # `adjacent_to_table`（仅邻近）。只有前者会进入 `_decide_numbered_candidates`
            # 的安全穿透判定；后者不追加任何表格软理由。
            table_scope, table_region_reason = table_scopes.get(
                (page.page_number, line.line_index),
                (TABLE_SCOPE_NONE, None))
            if page.page_number in toc_pages:
                rejected.append("toc_page_line")
            size = _line_size(line)
            bold = _line_is_bold(line)
            style_path = bool(bold and base_size > 0
                              and size >= base_size + TITLE_SIZE_DELTA_PT)
            if numbered:
                spans = iter_heading_spans(title)
                if not spans or spans[0].offset != 0:
                    # 编号不在行首：这是**正文里的**编号，不是标题行的编号。
                    rejected.append("numbering_not_at_line_start")
                # 通用反例守卫（只看行形态，不看任何业务词）：
                if len(numbering_core_text(title)) < NUMBERING_ONLY_MIN_CHARS:
                    # 去掉编号与标点后没有实义字符：纯编号 / 纯符号行。
                    rejected.append(NUMBERING_ONLY_REASON)
                elif _wrapped_in_column(line, column_right, size):
                    # 右端已排到正文栏边缘：折行正文的续写行。
                    rejected.append(WRAPPED_PARAGRAPH_REASON)
                elif (page.page_number, line.line_index) in table_rows:
                    # 同一基线上还有别的起点不同的行：表格单元格行。
                    rejected.append(TABLE_CELL_REASON)
            else:
                if len(flat) > MAX_UNNUMBERED_TITLE_LEN:
                    rejected.append("unnumbered_too_long")
            toc_matches, bookmark_matches = _matches_for(
                flat, toc_entries, context.bookmarks)
            # §三.3（`hq-4`）：候选是否是一条**已验证目录项的正文 landing**。这一步
            # 在**证据装配之前**、对编号与无编号候选**一视同仁**地做 —— 目录项指向的
            # 正文一级标题经常既无编号、字号也不高于正文（"致股东的信"这类独立首页），
            # 旧实现只在 `numbered` 分支里装配证据，于是这类行永远拿不到来源证据、
            # 只能按 `style_only_no_structure` 被拒。来源对象链是**行的**属性，不是
            # "它有没有编号"的属性。
            #
            # 只在存在同名目录项时才做（普通正文行的常见路径不付出额外重算代价）。
            # §四 P1-B(3)（`hq-4`）：**目标行**是普通表格内容（本行自己就是一条多字段
            # 表格行的一格）时，目录项 landing 链**不**装配 —— 目录项指向它只证明"目录里
            # 有这个字符串"，不证明"这一行是正文标题"。这不是放宽容差，而是补上 P1-B 的
            # 第三段判据：来源对象链只能把行**送进**候选，不能替它背书是标题。
            toc_landings = (verified_toc_landings(
                layout=layout, page_number=page.page_number,
                line_index=line.line_index, landing_title=flat,
                toc_matches=toc_matches)
                if toc_matches and toc_landing_target_is_body_line(
                    table_scope, table_region_reason) else ())
            toc_landing_ok = bool(toc_landings)
            if not numbered and not toc_matches and not bookmark_matches \
                    and not style_path:
                rejected.append("style_only_no_structure")
            recurrence = len(occurrence_pages.get(flat, set()) - {page.page_number})
            if not numbered and not toc_matches and not bookmark_matches \
                    and recurrence + 1 >= REPEATED_NOISE_MIN_PAGES:
                rejected.append("repeated_noise")

            level_from_numbering = leading_heading_level(title)
            declared_level = None if level_from_numbering is None \
                else level_from_numbering - 1
            previous = naked[position - 1] if position > 0 else None
            following = naked[position + 1] if position + 1 < len(naked) else None
            lifted = base_size > 0 and size > base_size + SIZE_ABOVE_BODY_EPS_PT
            if not rejected and numbered and not lifted \
                    and _same_level_plain_adjacent(
                        level_from_numbering, base_size, previous, following):
                # 紧邻同层编号行且**双方都没有**字号抬升：更像有序编号列表，而不是标题。
                # 只降级为 ambiguous，不硬拒（真实小标题常成串）。
                #
                # §五 P1-C(1)：本行**自身**相对正文基准有字号抬升时，不适用该降级。
                # 有序编号列表的每一项都排在正文尺寸上；一行若自己就比正文大，它已经
                # 有一个**单行版面事实**把它与旁边的编号正文区分开，此时"旁边有编号
                # 正文"不足以把它降级 —— 否则真实的编号小标题会因为上一行恰是编号
                # 说明正文而被漏收（实测形态：同级编号说明正文之后紧接一条被抬升的
                # 编号小标题）。判据只比较字号与正文基准，不看任何词、页码或编号值。
                soft.append(AMBIGUOUS_LIST_RUN_REASON)

            wrap_flagged = _wrap_flagged(line, size, right_edges.get(page.page_number))
            wrap_continuation = (
                numbered and not rejected
                and _wrap_continuation_flagged(line, following, size))
            primary: list = []
            supporting: list = []
            if numbered and not rejected:
                if base_size > 0 and size > base_size + SIZE_ABOVE_BODY_EPS_PT:
                    primary.append("size_above_body")
                if _is_centered(page, line):
                    primary.append("centered")
                if bold:
                    # 整行加粗：单行自身的版面事实。实测三份真实年报里**没有**加粗的
                    # 编号行（0/0/0），因此本证据在真实材料上是惰性的；保留它是因为
                    # §二.2 把"居中 / 加粗等独立版面证据"列为候选自身的合法证据，而
                    # 一条词表里挂着、生产侧永远不产出的证据码会让独立验收方的
                    # `intrinsic` 集合出现不可达分支。
                    primary.append("bold_majority")
                if toc_landing_ok:
                    # §二/§三（`hq-4`）：**只有**对象级验证过的目录项 landing 才是来源
                    # 证据。`hq-3` 在这里写的是 `if toc_matches or bookmark_matches`，即
                    # 「同名目录项 / 同名书签」这一**召回**事实就等于来源事实；书签的那
                    # 一半在本轮被取消（身份不可重建，见 `verified_source_landings`）。
                    primary.append(TOC_BODY_LANDING_EVIDENCE)
                if table_scope != TABLE_SCOPE_INSIDE and not wrap_flagged \
                        and not wrap_continuation:
                    # 弱主证据（§二.2）：本行**自身**是完整、未折行、未截断、且不属于
                    # 表格成员的独立标题行形态。它不含任何版式提升，单独不足以把表内
                    # 标签升为标题 —— 因此只有 `inside_table` 的候选拿不到它。
                    #
                    # §三.3：`adjacent_to_table`（仅仅靠近表格）**照常**拿到它。标题
                    # 后面紧跟着一张新表，或标题紧跟在上一张表的末行之后，都不改变
                    # "这是一条完整独立标题行"这一单行事实；仅仅因为贴着表格就抽掉它
                    # 会漏收真实标题。
                    primary.append("standalone_line")
                if body_left is not None \
                        and abs(line.bbox[0] - body_left) >= COLUMN_INDENT_MIN_PT:
                    supporting.append("column_indent")
                if _paragraph_boundary(previous, line):
                    supporting.append("paragraph_boundary")
            elif not rejected and toc_landing_ok:
                # §三.3（`hq-4`）：**无编号**的正文 landing。它走的是与编号候选完全
                # 相同的资格语义 —— 证据码同样来自**本行自身**的对象级来源事实，而不是
                # "它有没有编号"。样式辅证据照常补上（能补就补，补不上也不影响：这条
                # 路径的资格由来源对象链本身承担）。
                primary.append(TOC_BODY_LANDING_EVIDENCE)
                if body_left is not None \
                        and abs(line.bbox[0] - body_left) >= COLUMN_INDENT_MIN_PT:
                    supporting.append("column_indent")
                if _paragraph_boundary(previous, line):
                    supporting.append("paragraph_boundary")

            candidates.append(HeadingCandidate(
                page_number=page.page_number, line_index=line.line_index,
                line_text=line.text, title=title, title_normalized=flat,
                numbered=numbered, declared_level=declared_level,
                font_size=size, bold=bold, centered=_is_centered(page, line),
                style_path=style_path, body_recurrence=recurrence,
                toc_matches=toc_matches, bookmark_matches=bookmark_matches,
                rejected=tuple(sorted(set(rejected))), x0=line.bbox[0],
                previous_line_key=(None if previous is None
                                   else (page.page_number, previous.line_index)),
                primary_evidence=tuple(primary),
                supporting_evidence=tuple(supporting),
                soft_reasons=tuple(soft), wrap_flagged=wrap_flagged,
                table_scope=table_scope,
                table_region_reason=table_region_reason,
                wrap_continuation=wrap_continuation,
                # §三.4：层级来源。只有已验证目录项 landing 才带导航层级；其余候选
                # 保持 `None`，层级照旧由编号 / 字号决定（既有路径零改变）。
                navigation_level=(
                    landing_navigation_level(toc_landings)
                    if TOC_BODY_LANDING_EVIDENCE in primary else None)))
    return _decide_numbered_candidates(tuple(candidates), layout=layout)


def _same_level_plain_adjacent(level: int | None, base_size: float,
                               previous, following) -> bool:
    """紧邻行是否为**同编号层级且无字号抬升**的普通编号行（有序列表上下文）。"""
    if level is None:
        return False
    for other in (previous, following):
        if other is None:
            continue
        if leading_heading_level(other.text.strip()) != level:
            continue
        if base_size > 0 \
                and _line_size(other) > base_size + SIZE_ABOVE_BODY_EPS_PT:
            continue
        return True
    return False


def _decide_numbered_candidates(candidates: tuple,
                                *, layout: PageLayout | None = None) -> tuple:
    """确定性自顶向下的编号候选判定（`hq-3` 资格 profile）。

    **取消全文件候选互证（§二.1）**：采纳判据是"候选**自身**的主证据 ≥1 且 主+辅 ≥
    `MIN_HEADING_EVIDENCE`"。`level_layout`（同级候选共享版式类）已降格为辅证据，
    且只在**同页或相邻页**这一辅助邻近窗口内出借 —— 出借方必须是一条**自证成立**的
    候选（自己带强主证据：字号抬升 / 居中 / 加粗 / 目录书签佐证），受借方必须与它同层、
    同版式类、且落在 `NEARBY_PAGE_WINDOW_PAGES` 页内。"整份文档里另有某条候选人长得像"
    因此永远无法单独给一条候选提供标题资格，也不能与一条普通辅证据拼成资格。

    实测（TS3 标题资格定点返修轮，三份真实 PDF）：本版在 2024 / 2025 / KCZ 三件上把
    已采纳标题从 681 / 674 / 334 收紧到 **600 / 588 / 320**（其中 2024 与 2025 的最后
    3 / 4 条由判据 E 的纯标签带挡下），同时 §三 点名的全部表格污染节点（`1、国家持`、
    `2、国有法`、`一、有限售`、`（一）综合`、`三、本期增`、`一、账面原值`、
    `二、累计折旧` 等）全部退出正式标题树。

    三条**单行版面事实**驱动的降级（都只读候选自身，因此不是循环自证）：

    - `wrapped_numbered_prose`：本行已排到本页正文栏最右观测边缘（折行正文的续写行），
      且**没有**任何强主证据 → 不得采纳。
    - `table_region_candidate`：本行是 `inside_table`（成员资格已被几何证明：多字段行
      的格子 / 列锚点成员 / 标签列片段 / 标签带 / 结构行闭合），且**没有通过**
      `safe_table_override` 的安全穿透资格 → 无法与表内行标签 / 被列宽截断的单元格
      区分，fail-closed。**仅仅邻近表格（`adjacent_to_table`）不触发这一条。**
    - `numbered_prose_shape`：本行**自身**的文字形态已是普通编号正文（短子句链，或
      "定义冒号 + 定义谓词"），且没有任何强主证据 → 不得采纳。判定只看本行文本，
      不看其它候选、不看业务词、不看页码与编号值。

    前两条同时决定了 `standalone_line`（弱主证据）是否成立：折行行与 `inside_table`
    的一行都不是"完整、未被截断、且不属于表格成员的独立标题行"；`adjacent_to_table`
    的一行**仍是**。

    **已知残留（如实登记，不靠继续放宽容差换取）**：真实的短标签带（带内行数不足
    `TABLE_REGION_BAND_MIN_LINES`）或上下两行多字段行**不同族**时，判据 E 不成立；
    某格内容跨越多行、而本行离任何多字段行又都超过 `TABLE_REGION_COLUMN_ANCHOR_REACH_PT`
    时，判据 A–E 也一条都不成立（例如表内一格的长承诺正文排在离最近结构行 80pt 以上
    的位置）。这类行与"正好排在自成一表的表格上方的真实小标题"在**局部几何**上同形，
    因此保留为已知残留：它们进入正式树仍是可能的，独立验收门会把它们逐条列出，
    交给 §五 TableObject 处理。
    """
    # 第一遍：**只看候选自身**的强主证据，定出"自证成立"的候选集合。它只用于给局部
    # 结构域内的相邻候选出借 `level_layout` 辅证据；不参与任何候选自身的采纳判定。
    self_evident: tuple = tuple(
        candidate for candidate in candidates
        if not candidate.rejected and candidate.numbered
        and candidate.strong_primary_evidence and not candidate.soft_reasons)
    accepted_keys: set = set()
    result: list = []
    for candidate in candidates:
        if candidate.rejected or not candidate.numbered:
            result.append(candidate)
            if candidate.accepted:
                accepted_keys.add((candidate.page_number, candidate.line_index))
            continue
        primary = list(candidate.primary_evidence)
        supporting = list(candidate.supporting_evidence)
        if candidate.previous_line_key is not None \
                and candidate.previous_line_key in accepted_keys:
            supporting.append("heading_context")
        for other in self_evident:
            if other is candidate or other.declared_level != candidate.declared_level:
                continue
            if abs(other.page_number - candidate.page_number) \
                    > NEARBY_PAGE_WINDOW_PAGES:
                continue
            if abs(other.font_size - candidate.font_size) \
                    <= LAYOUT_CLASS_SIZE_TOL_PT \
                    and abs(other.x0 - candidate.x0) <= LAYOUT_CLASS_X0_TOL_PT:
                supporting.append("level_layout")
                break
        merged = replace(candidate,
                         primary_evidence=tuple(dict.fromkeys(primary)),
                         supporting_evidence=tuple(dict.fromkeys(supporting)))
        soft = list(candidate.soft_reasons)
        strong = bool(merged.strong_primary_evidence)
        if merged.wrap_flagged and not strong:
            soft.append(AMBIGUOUS_WRAPPED_PROSE_REASON)
        # §三.2（`hq-3`）：表内候选**不**凭版式样式穿透：唯一许可的依据是**对象级
        # 验证过的来源 landing**（`verified_source_landings`：页标签映射到本页、目标行
        # 文本一致、`TocSource` 身份可在真实版式上重建）；版式强证据无论几项都不放行。
        # 未通过者 fail-closed 进入正式未归属（不删除内容、不静默丢失）。
        # §三.3：`adjacent_to_table` 的候选**不**进入这一条 —— 仅仅垂直邻近表格不构成
        # 成员资格，它照常按普通标题资格判断（判据取 `table_scope`，不是取"有没有理由码"：
        # 邻近候选自己也带一条 `table_row_adjacent` 理由码，用理由码判断会把它一并拒绝）。
        if merged.table_scope == TABLE_SCOPE_INSIDE \
                and not safe_table_override(merged, layout=layout):
            soft.append(AMBIGUOUS_TABLE_REGION_REASON)
        would_be_accepted = (len(merged.primary_evidence) >= 1
                             and len(merged.primary_evidence)
                             + len(merged.supporting_evidence)
                             >= MIN_HEADING_EVIDENCE)
        if would_be_accepted and not strong \
                and prose_shaped_title(merged.title):
            soft.append(AMBIGUOUS_PROSE_SHAPE_REASON)
        decided = replace(merged, soft_reasons=tuple(dict.fromkeys(soft)))
        result.append(decided)
        if decided.accepted:
            accepted_keys.add((decided.page_number, decided.line_index))
    return tuple(result)


def _style_levels(candidates: tuple) -> dict:
    """无编号标题的层级 = 其字号在**已接受的无编号标题**中的秩（降序）。

    字号秩只作辅助证据：**只**在没有任何编号时才用来定层级，且不超过
    `MAX_STYLE_LEVEL`。平局由字号值本身排序，结果确定。
    """
    sizes = sorted({c.font_size for c in candidates if c.accepted_by_style},
                   reverse=True)
    return {size: min(index, MAX_STYLE_LEVEL)
            for index, size in enumerate(sizes)}


# ---------------------------------------------------------------------------
# 层级与树装配
# ---------------------------------------------------------------------------

def _level_conflict(candidate: HeadingCandidate, candidates: tuple) -> bool:
    """编号与字号冲突：深编号（≥3 级）却用最大档字号排版。

    冲突**不影响**层级判定（编号深度是主要依据），只作为原因码留痕。
    """
    if candidate.declared_level is None or candidate.declared_level < 2:
        return False
    sizes = [c.font_size for c in candidates if c.font_size > 0]
    if not sizes:
        return False
    return candidate.font_size >= max(sizes) - 0.5


def _line_bbox(layout: PageLayout, candidate: HeadingCandidate) -> tuple:
    line = layout.line_at(candidate.page_number, candidate.line_index)
    if line is None:
        raise OutlineBuildError(
            f"标题行在真实版式上不存在：页 {candidate.page_number} / 行 "
            f"{candidate.line_index}；不得用候选里的自报坐标代替真实版式行")
    return tuple(line.bbox)


def acceptance_basis(candidate: HeadingCandidate) -> str:
    """该候选**凭什么**取得标题资格（`hq-4`；`-` 表示未取得）。

    它被**持久化**进候选审计记录（`accepted_by=`），使独立验收方能机械回答一个
    否则无法从产物回答的问题：**这条已采纳节点是不是靠来源 landing 放行的**。
    没有这个字段时，"生产侧按书签放行、复核侧无法重建"只能靠猜；有它之后就是一条
    确定的比较（见 `structural_review_findings` 的 `toc_landing_unverified_accepted`）。

    取值封闭且互斥优先：`toc_landing`（已验证目录项 landing）/ `numbering`（编号 +
    证据算术）/ `style`（版式）/ `-`。判定只读候选自身，不看文档、公司或页码。
    """
    if not candidate.accepted:
        return "-"
    if candidate.accepted_by_toc_landing:
        return "toc_landing"
    if candidate.accepted_by_numbering:
        return "numbering"
    return "style"


def _candidate_evidence(candidate: HeadingCandidate,
                        layout: PageLayout | None = None) -> tuple:
    return (
        f"page={candidate.page_number}",
        f"line={candidate.line_index}",
        f"numbered={candidate.numbered}",
        f"declared_level={candidate.declared_level}",
        # §三.4：该行的**导航层级**（来自已验证目录项的声明深度；无来源时为 `None`）。
        # 它是树装配时层级的第一来源，因此必须与 `declared_level` 一起持久化。
        f"navigation_level={candidate.navigation_level}",
        f"font_size={candidate.font_size}",
        f"bold={candidate.bold}",
        f"centered={candidate.centered}",
        f"style_path={candidate.style_path}",
        f"body_recurrence={candidate.body_recurrence}",
        f"toc_matches={len(candidate.toc_matches)}",
        f"bookmark_matches={len(candidate.bookmark_matches)}",
        f"primary_evidence={','.join(candidate.primary_evidence) or '-'}",
        f"supporting_evidence={','.join(candidate.supporting_evidence) or '-'}",
        f"soft_reasons={','.join(candidate.soft_reasons) or '-'}",
        # §二（`hq-4`）：**这条候选凭哪条路径被采纳**。必须持久化，否则"按来源 landing
        # 放行"与"按版式放行"在产物里无法区分，复核方也就无法回答"生产侧是不是靠一条
        # 它自己重建不出来的依据放行的"。
        f"accepted_by={acceptance_basis(candidate)}",
        # §三.1：`inside_table` / `adjacent_to_table` / `none` 三层状态**持久化**到候选
        # 审计记录里，独立验收门据此判断"生产侧把这一行当成表格成员还是仅仅邻近"。
        f"table_scope={candidate.table_scope}",
        f"x0={candidate.x0}",
        # §二（`hq-4`）：**对象级验证过**的来源 landing 载荷（规范 JSON；无来源则空串）。
        # 独立验收门不采信上面的 `toc_body_landing` 证据码，只从这个载荷里的来源对象
        # 上下文（`TocSource` 身份、目标行、页标签映射）自己重算 —— 因此"自报有目录
        # 佐证"与"真的指向本行"在持久化层就分开了。书签条目在载荷里显式标注
        # `bookmark_navigation_only`，复核侧据此既不采信它、也不把它丢掉。
        f"{SOURCE_LANDING_FIELD}="
        f"{'' if layout is None else _source_landing_json(candidate, layout)}",
    )


def _first_toc_label(candidate: HeadingCandidate):
    return candidate.toc_matches[0].declared_page_label \
        if candidate.toc_matches else None


def _first_bookmark_page(candidate: HeadingCandidate):
    return candidate.bookmark_matches[0].page_number \
        if candidate.bookmark_matches else None


def _toc_physical_page(layout: PageLayout, candidate: HeadingCandidate):
    if not candidate.toc_matches:
        return None
    return _resolve_page_label(layout, candidate.toc_matches[0].declared_page_label)


def _page_conflict(layout: PageLayout, candidate: HeadingCandidate) -> bool:
    """目录 / 书签声明的页码与真实正文锚点页码是否冲突（冲突即留痕）。"""
    physical = _toc_physical_page(layout, candidate)
    if physical is not None and physical != candidate.page_number:
        return True
    page = _first_bookmark_page(candidate)
    return page is not None and page != candidate.page_number


def _toc_marker(entry: TocEntry) -> str:
    spans = iter_heading_spans(entry.title_part)
    if spans:
        return spans[0].heading.strip() or entry.title_part[:MIN_TITLE_LEN]
    return entry.title_part[:max(MIN_TITLE_LEN, min(4, len(entry.title_part)))]


def _toc_occurrence(source: TocSource, entry: TocEntry) -> ReferenceOccurrence:
    """目录项 occurrence：位置**必须**与真实 `TocSource` 完全一致。"""
    return ReferenceOccurrence(
        source_ref=edge_ref("toc", source.toc_source_id),
        page_number=source.page_number, line_index=source.line_index,
        char_start=source.char_start, char_end=source.char_end,
        occurrence_index=0, reference_marker=_toc_marker(entry),
        reference_kind="toc_to_body", declared_target=entry.title_part,
        normalized_text=source.entry_text)


def _reference_markers(text: str) -> tuple:
    found = []
    for marker in REFERENCE_MARKERS:
        if marker in text:
            found.append(marker)
            if len(found) >= REFERENCE_MARKER_MAX_PER_LINE:
                break
    return tuple(found)


def _used_sources(candidates: tuple, toc_entries: tuple, bookmarks: tuple) -> tuple:
    """实际用到的 `candidate_sources`（必须属于公共类型的封闭词表）。

    "用到"= **该来源真的参与了某条候选的判定**，不是"文件里恰好存在这种来源"：
    S2 只有在某一行的归一标题与某条书签一致时才计入。否则"同一份 PDF 换一个
    书签清单"会改变 outline 身份，而树其实一模一样 —— 身份必须只反映真实依据。
    """
    used = []
    if toc_entries:
        used.append("toc_page")
    if any(c.bookmark_matches for c in candidates):
        used.append("pdf_bookmarks")
    if any(c.numbered and c.accepted for c in candidates):
        used.append("body_numbering")
    if any(not c.numbered and c.accepted for c in candidates):
        used.append("layout_style")
    if any("repeated_noise" in c.rejected for c in candidates):
        used.append("repeated_noise_exclusion")
    return tuple(name for name in CANDIDATE_SOURCES if name in used)


def _unassigned_span(layout: PageLayout, context: OutlineSourceContext,
                     locator_value: str, page_number: int, line_index: int,
                     title: str, unassigned_reason: str,
                     evidence_count: int = 0) -> OutlineSpan:
    """把一条**能定位到真实版式行**的未归属候选包成正式 `OutlineSpan`。

    单行 span：起止锚点都是这条真实 `LayoutLine`。`confidence` = 已满足的结构证据
    占采纳门槛的比例（未送达门槛必然 < 1），含义是"证据充分度"，不是"是标题的概率"。
    没有真实行的候选**不可能**走到这里（`layout.line_at` 为 None 时 fail-closed）。

    `unassigned_reason` 必须是 §五 的**可执行细分原因**（来源区分，不是笼统的
    "边界不明"）：正文编号候选 / 目录项 / 书签各自的未归属原因**不得互相冒充**。
    """
    if unassigned_reason not in UNASSIGNED_FORMAL_REASONS:
        raise OutlineBuildError(
            f"正式未归属原因必须是 {UNASSIGNED_FORMAL_REASONS} 之一，"
            f"得到 {unassigned_reason!r}；不得把所有未归属压成同一个笼统原因")
    page_number, line_index = int(page_number), int(line_index)
    line = layout.line_at(page_number, line_index)
    if line is None:
        raise OutlineBuildError(
            f"未归属候选在真实版式上不存在：页 {page_number} / 行 {line_index}；"
            f"正式 unassigned 必须能回溯到来源行")
    normalized = tight(title)
    if normalized == "":
        raise OutlineBuildError(
            f"未归属候选的归一文本为空：页 {page_number} / 行 {line_index}；"
            f"OutlineSpan 不允许空文本")
    anchor = (page_number, line_index, tuple(line.bbox))
    return OutlineSpan.create(
        document_outline_locator=locator_value, node_id=None,
        document_id=context.document_id, document_version=context.document_version,
        evidence_set_version=V.OUTLINE_UNASSIGNED_EVIDENCE_SET_VERSION,
        role="unassigned", start_anchor=anchor, end_anchor=anchor,
        normalized_text=normalized,
        layout_line_refs=((page_number, line_index),),
        unassigned_reason=unassigned_reason,
        confidence=min(0.99, evidence_count / (MIN_HEADING_EVIDENCE + 1)),
        # TS4 §18.11.2 / §18.12.2：**显式**传入 TS3 unassigned 的写路径版本，
        # 不再依赖"恰好还没变的默认值"—— TS4 新增 `sb-2` 之后，默认值一旦漂移，
        # 冻结树就无法 canonical 重建（232 个对象逐个失配）。
        span_builder_version=V.OUTLINE_UNASSIGNED_SPAN_BUILDER_VERSION)


def canonical_unassigned_order(spans) -> tuple:
    """§五：正式 unassigned 的**规范顺序**，与候选 / 书签的**到达顺序**无关。

    未归属 span 由两个彼此独立的来源产生（目录项循环、书签循环）。若直接按到达顺序
    落盘，反转书签输入就会交换 span 次序 —— 成员集合完全一样，`content_fingerprint`
    与 `outline_id` 却变了，等于让 outline 身份依赖"输入怎么排列"（同一份文档可以有
    多个身份）。因此落盘前按**版式位置**排序：先页、再行，然后按来源细分原因、
    span_id、内容指纹兜底，构成全序。这样"成员集合不变 ⇒ 身份不变"。
    """
    return tuple(sorted(
        spans, key=lambda s: (s.start_anchor[0], s.start_anchor[1],
                              s.unassigned_reason, s.span_id,
                              s.content_fingerprint)))


def build_document_outline(context: OutlineSourceContext) -> OutlineBuildResult:
    """由来源上下文构建正式 `DocumentOutline` + 类型化构建审计。

    **纯函数**：不打开文件、不查库、不联网；相同输入连续两次构建得到逐字节一致的
    规范 JSON 与相同 id。
    """
    if not isinstance(context, OutlineSourceContext):
        raise OutlineBuildError(
            f"build_document_outline 需要 OutlineSourceContext，得到 "
            f"{type(context).__name__}；正文标题必须由显式来源上下文佐证")
    layout = context.layout
    toc_pages, toc_entries = collect_toc_entries(layout)
    candidates = scan_heading_candidates(context)
    style_levels = _style_levels(candidates)

    locator_value = derive_document_outline_locator(
        page_layout_id=layout.page_layout_id, document_id=context.document_id,
        algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
        schema_version=V.OUTLINE_SCHEMA_VERSION)

    # --- 逐行记账：先给每个非家具行一个默认桶（守恒由构造保证，仍显式断言） ---
    assignments: dict = {}
    originals: list = []
    for page in layout.pages:
        for line in page.lines:
            if line.is_furniture:
                continue
            key = (page.page_number, line.line_index)
            originals.append(key)
            assignments[key] = LineAssignment(
                page_number=page.page_number, line_index=line.line_index,
                assignment="ordinary_content", node_id=None,
                reason="待 TS4 切分 OutlineSpan")

    # --- 标题候选：分类（纯判定，无副作用） -----------------------------------
    #
    # 分类只决定"这条候选属于哪一类"，**不**创建节点 / span：正式未归属的原因里有
    # `hierarchy_conflict`，它取决于该候选在文档顺序上的**局部结构域**（是否跳级），
    # 只有在树装配那一遍按当前层级栈才算得出来。因此把"分类"与"落地"分开。
    prepared: list = []      # [(candidate, declared, reason_codes, state)]
    accepted: list = []
    for candidate in candidates:
        declared = candidate.declared_level
        # §三.4（`hq-4`）：层级来源的优先级是**确定性的三级**，只看候选自身：
        #
        # 1. 正文行**自己声明**的编号层级（`declared_level`）—— 本地正文结构；
        # 2. 已验证**目录路径**的声明深度（`navigation_level`）—— 仅在正文行自己没有
        #    编号声明时启用。无编号的正文 landing（"致股东的信"这类独立一级标题）没有
        #    任何本地编号可依，旧实现只能退到字号秩（`style_levels`），于是**同字号但
        #    不同目录深度**的两条标题会被串成父子。目录路径深度是一条可复核的来源事实：
        #    它来自目录行自身的编号形态，由 `TocEntry` 在真实版式上重算；
        # 3. 字号秩（`style_levels`）—— 两条来源都没有时的既有回退，**行为不变**。
        #
        # 层级仍然只是**声明**：最终深度由树装配的局部结构域（`declared` vs 当前栈）
        # 决定，缺级跳转照旧只留痕、不补猜父节点。
        if declared is None and candidate.navigation_level is not None:
            declared = candidate.navigation_level
        elif declared is None and candidate.accepted_by_style:
            declared = style_levels.get(candidate.font_size, 0)
        reason_codes = list(candidate.rejected)
        if candidate.numbered and _level_conflict(candidate, candidates):
            reason_codes.append("numbering_style_conflict")
        if candidate.accepted_by_style and not candidate.toc_matches \
                and not candidate.bookmark_matches:
            reason_codes.append("style_only_accepted")
        if candidate.accepted:
            state = "accepted"
        elif candidate.ambiguous:
            state = "ambiguous"
        else:
            state = "rejected"
        entry = (candidate, declared, tuple(sorted(set(reason_codes))), state)
        prepared.append(entry)
        if state == "accepted":
            accepted.append(entry)

    audit: list = []
    pending: list = []
    unassigned_spans: list = []

    # --- S1/S2 候选：**不能**创建节点 ---------------------------------------
    matched_toc_keys = set()
    matched_bookmarks = set()
    for candidate, _declared, _reasons, _state in accepted:
        for entry in candidate.toc_matches:
            matched_toc_keys.add((entry.page_number, entry.line_index))
        for bookmark in candidate.bookmark_matches:
            matched_bookmarks.add(bookmark.ordinal)
    for entry in toc_entries:
        key = (entry.page_number, entry.line_index)
        corroborated = key in matched_toc_keys
        assignments[key] = LineAssignment(
            page_number=entry.page_number, line_index=entry.line_index,
            assignment="toc_candidate", node_id=None,
            reason=("目录项与真实正文标题对应" if corroborated
                    else "toc_only_candidate：目录项未找到真实正文锚点"))
        if not corroborated:
            # §五 P1-C(3)：目录项**有真实 landing line**，因此不得只写 sidecar audit /
            # pending 后从正式 Outline 消失 —— 它必须作为 typed formal unassigned 的
            # 一条单行 span 留在 `DocumentOutline.unassigned` 里，原因 `toc_unmatched`。
            if tight(entry.title_part) != "":
                unassigned_spans.append(_unassigned_span(
                    layout, context, locator_value, entry.page_number,
                    entry.line_index, entry.title_part, UNASSIGNED_TOC_UNMATCHED,
                    evidence_count=(2 if entry.declared_page_label else 1)))
            audit.append(CandidateAuditRecord(
                kind="toc_entry", page_number=entry.page_number,
                line_index=entry.line_index, ordinal=len(audit),
                text=entry.title_part, accepted=False,
                evidence=(f"declared_page_label={entry.declared_page_label}",
                          f"declared_depth={entry.declared_depth}"),
                reason_codes=("toc_only_candidate",)))
    for bookmark in context.bookmarks:
        if bookmark.ordinal in matched_bookmarks:
            continue
        # 书签本身没有 LayoutLine。若某条真实正文行与它的归一标题一致，就把那行记
        # 为 `bookmark_candidate`；否则**不**给它编造锚点，只登记为待审候选。
        landing = None
        for key in originals:
            if assignments[key].assignment != "ordinary_content":
                continue
            line = layout.line_at(key[0], key[1])
            if line is not None \
                    and tight(line.text) == normalize_toc_title(bookmark.title):
                landing = key
                break
        if landing is not None:
            assignments[landing] = LineAssignment(
                page_number=landing[0], line_index=landing[1],
                assignment="bookmark_candidate", node_id=None,
                reason="书签标题命中的真实正文行（S2 不得单独创建节点）")
            # §五 P1-C(3)：已有真实 landing line ⇒ 必须进入正式 unassigned，
            # 原因 `bookmark_unmatched`（不得只留在 pending 里）。
            unassigned_spans.append(_unassigned_span(
                layout, context, locator_value, landing[0], landing[1],
                bookmark.title, UNASSIGNED_BOOKMARK_UNMATCHED, evidence_count=2))
        audit.append(CandidateAuditRecord(
            kind="bookmark", page_number=bookmark.page_number, line_index=0,
            ordinal=len(audit), text=bookmark.title, accepted=False,
            evidence=(f"declared_level={bookmark.declared_level}",
                      f"declared_page={bookmark.page_number}",
                      f"landing_line={landing}"),
            reason_codes=("bookmark_only_candidate",),
            declared_level=bookmark.declared_level))
        pending.append(PendingItem(
            kind="bookmark_only_candidate", stage="manual_review",
            page_number=bookmark.page_number, line_index=0,
            detail=(f"书签 {bookmark.title!r} 在版式上没有真实正文标题锚点；"
                    f"S2 不得直接创建节点，禁止采用最近页面 / 最相似标题")))
    toc_page_set = set(toc_pages)
    for key in originals:
        if key[0] not in toc_page_set:
            continue
        if assignments[key].assignment != "ordinary_content":
            continue
        assignments[key] = LineAssignment(
            page_number=key[0], line_index=key[1], assignment="toc_candidate",
            node_id=None, reason="目录页内不可解析为目录项的行（页面未被丢弃）")

    # --- 装配树（§三 P1-A：`declared_level → effective_depth` 的**局部结构域**映射） --
    #
    # 层级由**声明层级**驱动。`candidate.declared_level` 是 `harness.heading_structure`
    # 编号层级（1..5）的 **0 基深度**表示：`第一节`=0、`一、`=1、`（一）`=2、`1、`=3、
    # `（1）`=4；无编号标题记 0。
    #
    #     while stack and stack[-1].declared >= D:  stack.pop()
    #     depth  = len(stack)          # 0 表示当前局部结构域的顶层
    #     parent = stack[-1]           # 空栈 ⇒ 顶层（不猜父节点）
    #     stack.append((D, node_id))
    #
    # 为什么不是"逐级夹紧"（旧 `oa-1` 行为）：旧写法把 `level = min(declared, len(stack))`
    # 再取 `stack[-1]` 作父节点，于是**同级的 1、2、3、4 会被串成父子链**
    # （第一条 1、被压到深度 0，第二条 2、被夹到深度 1 挂到它下面），"同一编号体系里的
    # 同级标题"因此在树里变成了嵌套 —— 这是 §三 明确要修的缺陷。
    #
    # 局部结构域：`declared_level → effective_depth` 的映射只由**当前栈**决定。一次
    # 压缩（缺级跳转）之后，**同一 declared_level 必然继续得到同一有效深度**，因为
    # 决定深度的是"栈里还有几个声明层级更小的祖先"，而不是该层级第几次出现。
    # 章节重置 / 编号体系切换（例如 `1、` 改为 `一、`，或新章节重新从 `1、` 开始）会
    # 把栈弹空，`effective_depth` 归零，从而**重建**局部映射 —— 只依据声明层级本身，
    # 不依据公司名、页码、表号、标题原文或案例 ID。
    nodes: list = []
    children: dict = {}
    path_of: dict = {}
    sibling_index: dict = {}
    stack: list = []            # [(declared_level, node_id)]，按文档顺序
    for candidate, declared_level, reason_codes, state in prepared:
        declared = int(declared_level or 0)
        if declared < 0:
            declared = 0
        if state == "rejected":
            audit.append(CandidateAuditRecord(
                kind="heading", page_number=candidate.page_number,
                line_index=candidate.line_index, ordinal=len(audit),
                text=candidate.title, accepted=False,
                evidence=_candidate_evidence(candidate, layout),
                reason_codes=tuple(sorted(set(reason_codes))),
                declared_level=candidate.declared_level))
            continue
        if state == "ambiguous":
            # 证据不足 / 同层编号列表 / 折行正文 / 跳级：**正式**未归属，不是静默丢弃。
            # `declared` 已是**0 基深度**（`第一节`=0、`一、`=1、`（一）`=2、`1、`=3、
            # `（1）`=4）。它大于当前栈深 ⇒ 与局部结构域里的现有祖先之间缺级（跳级），
            # 层级归属无法在不猜父节点的前提下确定。
            jump = declared > len(stack)
            reason = unassigned_reason_for(candidate, jump=jump)
            span = _unassigned_span(
                layout, context, locator_value, candidate.page_number,
                candidate.line_index, candidate.title, reason,
                evidence_count=(len(candidate.primary_evidence)
                                + len(candidate.supporting_evidence)))
            unassigned_spans.append(span)
            assignments[(candidate.page_number, candidate.line_index)] = \
                LineAssignment(
                    page_number=candidate.page_number,
                    line_index=candidate.line_index,
                    assignment="unassigned_ambiguous", node_id=None,
                    reason=(f"编号候选未归属（{reason}）：已进入正式 "
                            f"DocumentOutline.unassigned，span_id={span.span_id}"))
            audit.append(CandidateAuditRecord(
                kind="heading", page_number=candidate.page_number,
                line_index=candidate.line_index, ordinal=len(audit),
                text=candidate.title, accepted=False,
                evidence=_candidate_evidence(candidate, layout),
                reason_codes=tuple(sorted(set(reason_codes)
                                          | {candidate.ambiguous_reason
                                             or AMBIGUOUS_INSUFFICIENT_REASON})),
                declared_level=candidate.declared_level))
            continue
        while stack and stack[-1][0] >= declared:
            stack.pop()
        level = len(stack)
        parent_id = stack[-1][1] if stack else None
        if declared > level:
            # 缺级跳转：`declared` 是该标题按编号本应有的**0 基深度**，比实际深度更深
            # 意味着上层标题不存在，层级因此被**确定性压缩**到当前局部结构域的下一层。
            # 只留痕，不改判；这里**不**回填"最近的前序标题"当父节点（不补猜漂亮树）。
            reason_codes = tuple(sorted(set(reason_codes) | {"level_jump_clamped"}))
        structural_path = (tuple(path_of[parent_id]) + (candidate.title_normalized,)
                           if parent_id is not None
                           else (candidate.title_normalized,))
        ordinal = sibling_index.get(parent_id, 0)
        sibling_index[parent_id] = ordinal + 1
        node = OutlineNode.create(
            document_outline_locator=locator_value, parent_id=parent_id,
            title=candidate.title, title_normalized=candidate.title_normalized,
            structural_path=structural_path,
            source_anchor=(candidate.page_number, candidate.line_index,
                           _line_bbox(layout, candidate)),
            child_ids=(), ordinal=ordinal)
        nodes.append(node)
        path_of[node.node_id] = structural_path
        children.setdefault(parent_id, []).append(node.node_id)
        stack.append((declared, node.node_id))
        assignments[(candidate.page_number, candidate.line_index)] = LineAssignment(
            page_number=candidate.page_number, line_index=candidate.line_index,
            assignment="accepted_heading", node_id=node.node_id,
            reason="真实正文标题行")
        audit.append(CandidateAuditRecord(
            kind="heading", page_number=candidate.page_number,
            line_index=candidate.line_index, ordinal=len(audit),
            text=candidate.title, accepted=True, node_id=node.node_id,
            evidence=_candidate_evidence(candidate, layout),
            reason_codes=reason_codes,
            declared_level=candidate.declared_level, applied_level=level,
            toc_declared_page_label=_first_toc_label(candidate),
            toc_declared_page_physical=_toc_physical_page(layout, candidate),
            bookmark_declared_page=_first_bookmark_page(candidate),
            page_conflict=_page_conflict(layout, candidate)))

    nodes = [OutlineNode.create(
        document_outline_locator=locator_value, parent_id=node.parent_id,
        title=node.title, title_normalized=node.title_normalized,
        structural_path=node.structural_path, source_anchor=node.source_anchor,
        child_ids=tuple(children.get(node.node_id, ())), ordinal=node.ordinal)
        for node in nodes]

    # --- 引用边：parent_child（全部）+ toc_to_body（对象级可核验才 resolved） -
    toc_sources: list = []
    edges: list = []
    resolved_pairs: list = []
    unresolved_pairs: list = []
    for node in nodes:
        if node.parent_id is None:
            continue
        edges.append(ReferenceEdge.create(
            document_outline_locator=locator_value,
            from_ref=edge_ref("node", node.parent_id),
            to_ref=edge_ref("node", node.node_id), edge_kind="parent_child",
            resolution_evidence=(
                f"derived:structural_path_prefix|level={node.level - 1}->"
                f"{node.level}|child_ids_symmetric")))
    for entry in toc_entries:
        source = TocSource.create(
            layout=layout, page_number=entry.page_number,
            line_index=entry.line_index, char_start=entry.char_start,
            char_end=entry.char_end, declared_page_label=entry.declared_page_label)
        toc_sources.append(source)
        occurrence = _toc_occurrence(source, entry)
        targets = [n for n in nodes
                   if normalize_toc_title(n.title) == entry.title_normalized]
        unique_target = targets[0] if len(targets) == 1 else None
        physical = _resolve_page_label(layout, entry.declared_page_label)
        if unique_target is not None and physical is not None \
                and physical == unique_target.source_anchor[0]:
            edges.append(ReferenceEdge.create(
                document_outline_locator=locator_value,
                from_ref=edge_ref("toc", source.toc_source_id),
                to_ref=edge_ref("node", unique_target.node_id),
                edge_kind="toc_to_body",
                resolution_evidence=(
                    f"toc_title_match:{V.TOC_TITLE_MATCH_VERSION}"
                    f"|toc_page_label_match:{V.TOC_PAGE_LABEL_MATCH_VERSION}"
                    f"|label={entry.declared_page_label}->page{physical}"
                    f"|node_anchor_verified"),
                occurrence=occurrence))
            resolved_pairs.append((entry, unique_target))
        else:
            if len(targets) > 1:
                reason = "ambiguous_candidates"
            elif physical is None:
                reason = "toc_page_label_mapping_unproven"
            else:
                reason = "target_not_in_scope"
            edges.append(ReferenceEdge.create(
                document_outline_locator=locator_value,
                from_ref=edge_ref("toc", source.toc_source_id), to_ref=None,
                edge_kind="toc_to_body", resolution_evidence=None,
                reason_code=reason, occurrence=occurrence))
            unresolved_pairs.append((entry, reason))

    # --- TS4 待办：正文引用标记（只登记，不伪造来源锚点） -------------------
    cross_reference_markers = 0
    for page in layout.pages:
        if page.page_number in toc_page_set:
            continue
        for line in page.lines:
            if line.is_furniture:
                continue
            if assignments[(page.page_number, line.line_index)].assignment \
                    == "accepted_heading":
                continue
            for marker in _reference_markers(line.text):
                cross_reference_markers += 1
                pending.append(PendingItem(
                    kind="cross_reference", stage="pending_ts4",
                    page_number=page.page_number, line_index=line.line_index,
                    detail=(f"正文引用标记 {marker!r}：其来源载体只能是 TS4 的 "
                            f"OutlineSpan（TS3 无 span），本轮不生成边，不得猜测目标、"
                            f"不得用字符串相同判 resolved")))

    reference_context = ReferenceValidationContext(
        toc_sources=tuple(toc_sources), layout=layout)
    # §五：目录项与书签两个来源的 span 在这里汇合，落盘前先排成规范顺序，避免
    # "成员集合相同、输入顺序不同"导致 outline 身份分叉。
    unassigned_spans = list(canonical_unassigned_order(unassigned_spans))
    outline = DocumentOutline.create(
        document_id=context.document_id, document_version=context.document_version,
        page_layout_id=layout.page_layout_id, nodes=tuple(nodes),
        edges=tuple(edges), unassigned=tuple(unassigned_spans),
        candidate_sources=_used_sources(candidates, toc_entries, context.bookmarks),
        reference_context=reference_context)

    assignment_counts: dict = {}
    for item in assignments.values():
        assignment_counts[item.assignment] = \
            assignment_counts.get(item.assignment, 0) + 1

    # --- 验收必须分别报告的口径（不是"总节点数"） ----------------------------
    accepted_candidates = [c for c, _d, _r, _s in accepted]
    ambiguous_candidates = [c for c in candidates if c.ambiguous]
    rejected_candidates = [c for c in candidates
                           if not c.accepted and not c.ambiguous]
    evidence_distribution: dict = {}
    for candidate in accepted_candidates:
        key = "+".join(sorted(candidate.primary_evidence
                              + candidate.supporting_evidence)) or "-"
        evidence_distribution[key] = evidence_distribution.get(key, 0) + 1
    false_positive_distribution: dict = {}
    for candidate in rejected_candidates:
        for code in (candidate.rejected or ("unknown",)):
            false_positive_distribution[code] = \
                false_positive_distribution.get(code, 0) + 1
    ambiguous_distribution: dict = {}
    for candidate in ambiguous_candidates:
        key = candidate.ambiguous_reason or AMBIGUOUS_INSUFFICIENT_REASON
        ambiguous_distribution[key] = ambiguous_distribution.get(key, 0) + 1
    # §二（`hq-4`）：诊断口径里的"强信号"跟着证据码改名 —— 来源侧只剩**对象级验证过
    # 的目录项 landing**，书签命中不再计入（它只是导航候选）。
    strong_signals = {"size_above_body", "centered", TOC_BODY_LANDING_EVIDENCE}
    subheading_without_size_signal = [
        c for c in accepted_candidates
        if not (set(c.primary_evidence) & strong_signals) and not c.bold]
    numbering_only_accepted = [
        c for c in accepted_candidates
        if len(c.primary_evidence) + len(c.supporting_evidence)
        < MIN_HEADING_EVIDENCE]

    result = OutlineBuildResult(
        outline=outline, candidates=tuple(audit),
        line_assignments=tuple(assignments[key] for key in originals),
        pending=tuple(pending), toc_sources=tuple(toc_sources),
        resolved_edges=tuple(resolved_pairs),
        unresolved_edges=tuple(unresolved_pairs),
        layout=layout,
        stats={
            "outline_builder_version": OUTLINE_BUILDER_VERSION,
            "algorithm_version": V.OUTLINE_ALGORITHM_VERSION,
            "document_id": context.document_id,
            "page_layout_id": layout.page_layout_id,
            "document_version": context.document_version,
            "total_pages": len(layout.pages),
            "toc_pages": list(toc_pages),
            "toc_entries": len(toc_entries),
            "bookmarks": len(context.bookmarks),
            "heading_candidates": len(candidates),
            "heading_candidates_accepted": len(accepted_candidates),
            "heading_candidates_ambiguous": len(ambiguous_candidates),
            "heading_candidates_rejected": len(rejected_candidates),
            "accepted_evidence_distribution": dict(sorted(
                evidence_distribution.items())),
            "accepted_subheading_without_size_signal":
                len(subheading_without_size_signal),
            "accepted_subheading_without_size_signal_samples": [
                f"p{c.page_number}:{c.line_index} {c.title[:40]}"
                for c in subheading_without_size_signal[:20]],
            "accepted_numbering_only": len(numbering_only_accepted),
            "false_positive_reason_distribution": dict(sorted(
                false_positive_distribution.items())),
            "ambiguous_reason_distribution": dict(sorted(
                ambiguous_distribution.items())),
            "unassigned_spans": len(unassigned_spans),
            "unassigned_samples": [
                f"p{s.start_anchor[0]}:{s.start_anchor[1]} {s.normalized_text[:40]}"
                for s in unassigned_spans[:20]],
            "nodes": len(nodes),
            "levels": _level_histogram(nodes),
            "edges_total": len(edges),
            "edges_parent_child": sum(1 for e in edges
                                      if e.edge_kind == "parent_child"),
            "edges_toc_to_body": sum(1 for e in edges
                                     if e.edge_kind == "toc_to_body"),
            "edges_toc_to_body_resolved": len(resolved_pairs),
            "edges_toc_to_body_unresolved": len(unresolved_pairs),
            "cross_reference_edges": 0,
            "cross_reference_pending_ts4": cross_reference_markers,
            "body_font_size": body_font_size(layout),
            "line_assignment_counts": dict(sorted(assignment_counts.items())),
            "non_furniture_lines": len(originals),
            "pending_ts4_span_builder": True,
            "manual_review_required": True,
        })
    result.assert_conserved(layout)
    return result


def _level_histogram(nodes: list) -> dict:
    counts: dict = {}
    for node in nodes:
        key = str(node.level)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# 人工复核视图
# ---------------------------------------------------------------------------

def outline_markdown(result: OutlineBuildResult, title: str) -> str:
    """逐节点展示 level / 完整路径 / 原始标题 / anchor / S1S2S3 证据 / 原因码。"""
    outline = result.outline
    audit_by_node = {item.node_id: item for item in result.candidates
                     if item.node_id is not None}
    lines = [f"# {title}", "",
             f"- document_id: `{outline.document_id}`",
             f"- page_layout_id: `{outline.page_layout_id}`",
             f"- outline_id: `{outline.outline_id}`",
             f"- algorithm_version: `{outline.algorithm_version}`",
             f"- nodes: {len(outline.nodes)}",
             f"- candidate_sources: {list(outline.candidate_sources)}",
             "- **状态：manual_review_required**（构建完成不等于标题树通过）", "",
             "## 标题树", ""]
    for node in outline.nodes:
        item = audit_by_node.get(node.node_id)
        pad = "  " * node.level
        lines.append(f"{pad}- **L{node.level}** {node.title!r}")
        lines.append(f"{pad}  - path: `{' / '.join(node.structural_path)}`")
        lines.append(f"{pad}  - anchor: page={node.source_anchor[0]} "
                     f"line={node.source_anchor[1]} "
                     f"bbox={list(node.source_anchor[2])}")
        if item is not None:
            sources = ["S3 正文编号" if item.declared_level is not None
                       else "S3 正文排版（bold/字号）"]
            if item.toc_declared_page_label is not None:
                sources.append(f"S1 目录声明页={item.toc_declared_page_label}"
                               f"(→物理页 {item.toc_declared_page_physical})")
            if item.bookmark_declared_page is not None:
                sources.append(f"S2 书签声明页={item.bookmark_declared_page}")
            lines.append(f"{pad}  - sources: {', '.join(sources)}")
            for tag in ("primary_evidence", "supporting_evidence"):
                value = next((entry.split("=", 1)[1] for entry in item.evidence
                              if entry.startswith(tag + "=")), "-")
                lines.append(f"{pad}  - {tag}: {value}")
            lines.append(f"{pad}  - reason_codes: {list(item.reason_codes)}")
            lines.append(f"{pad}  - 目录声明页与正文锚点冲突: "
                         f"{'是（已留痕）' if item.page_conflict else '否'}")
        lines.append("")

    lines.append("## 正式未归属（DocumentOutline.unassigned）")
    lines.append("")
    lines.append(f"- span 数：{len(outline.unassigned)}；这些 span 参与 "
                 f"content_fingerprint / outline_id / 读回校验")
    for span in outline.unassigned[:30]:
        lines.append(f"- `{span.span_id}` p{span.start_anchor[0]} "
                     f"l{span.start_anchor[1]} reason={span.unassigned_reason} "
                     f"confidence={span.confidence} {span.normalized_text[:40]!r}")
    if len(outline.unassigned) > 30:
        lines.append(f"- …（其余 {len(outline.unassigned) - 30} 项见 "
                     f"`formal_unassigned.json`）")
    lines.append("")

    lines.append("## 目录项 → 正文（toc_to_body）")
    lines.append("")
    for entry, node in result.resolved_edges:
        lines.append(f"- 已解析（对象级核验通过）：目录页 p{entry.page_number} "
                     f"{entry.entry_text.strip()!r} → `{node.node_id}`"
                     f"（声明页标签 {entry.declared_page_label}）")
    for entry, reason in result.unresolved_edges:
        lines.append(f"- 未解析（{reason}）：目录页 p{entry.page_number} "
                     f"{entry.entry_text.strip()!r}")
    if not result.resolved_edges and not result.unresolved_edges:
        lines.append("- 无目录项（S1 为空）")

    by_reason: dict = {}
    for item in result.candidates:
        if item.accepted:
            continue
        for code in item.reason_codes:
            by_reason.setdefault(code, []).append(item)
    lines.append("")
    lines.append("## 未采纳候选（逐项可追溯）")
    lines.append("")
    if not by_reason:
        lines.append("- 无未采纳候选")
    for code in sorted(by_reason):
        items = by_reason[code]
        lines.append(f"### {code}（{len(items)}）")
        lines.append("")
        for item in items[:20]:
            lines.append(f"- [{item.kind}] p{item.page_number} "
                         f"l{item.line_index} {item.text[:50]!r}")
        if len(items) > 20:
            lines.append(f"- …（其余 {len(items) - 20} 项见 "
                         f"`outline_candidate_audit.json`）")
        lines.append("")

    lines.append("## 行归属统计（每个非家具行恰好一个桶）")
    lines.append("")
    for name, count in result.line_assignment_counts().items():
        lines.append(f"- {name}: {count}")
    lines.append(f"- 合计: {len(result.line_assignments)}"
                 f"（= 非家具行 {result.stats['non_furniture_lines']}）")
    lines.append("")
    lines.append("## 各层级节点数量")
    lines.append("")
    for level, count in result.stats["levels"].items():
        lines.append(f"- L{level}: {count}")
    lines.append(f"- 合计: {len(outline.nodes)}")
    lines.append("")

    lines.append("## 页中小标题样例（非页首行的已采纳节点）")
    lines.append("")
    mid_page = [n for n in outline.nodes if n.source_anchor[1] > 0]
    if not mid_page:
        lines.append("- 无：本份文档所有已采纳标题都落在页首行")
    for node in mid_page[:20]:
        lines.append(f"- **L{node.level}** {node.title!r} p{node.source_anchor[0]} "
                     f"l{node.source_anchor[1]}")
    if len(mid_page) > 20:
        lines.append(f"- …（其余 {len(mid_page) - 20} 项见 `document_outline.json`）")
    lines.append("")

    lines.append("## 目录专项（S1）")
    lines.append("")
    toc_only = [c for c in result.candidates
                if "toc_only_candidate" in c.reason_codes]
    conflicts = [c for c in result.candidates if c.page_conflict]
    lines.append(f"- 目录项：{result.stats['toc_entries']}"
                 f"（目录页 {result.stats['toc_pages']}）")
    lines.append(f"- 在真实正文锚点上对应（已生成 `toc_to_body`）："
                 f"{len(result.resolved_edges)}")
    lines.append(f"- 未解析（`toc_to_body` 保持 unresolved）："
                 f"{len(result.unresolved_edges)}")
    lines.append(f"- **toc_only_candidate**（目录项找不到真实正文锚点，未建节点）："
                 f"{len(toc_only)}")
    for item in toc_only[:20]:
        lines.append(f"  - p{item.page_number} l{item.line_index} "
                     f"{item.text[:50]!r}")
    lines.append(f"- **声明页与正文锚点冲突**（已留痕，不猜最近页）：{len(conflicts)}")
    for item in conflicts[:20]:
        lines.append(f"  - p{item.page_number} l{item.line_index} {item.text[:40]!r} "
                     f"目录声明页={item.toc_declared_page_label}"
                     f"(→物理页 {item.toc_declared_page_physical})")
    lines.append("")

    lines.append("## 书签专项（S2）")
    lines.append("")
    bookmark_only = [c for c in result.candidates
                     if "bookmark_only_candidate" in c.reason_codes]
    lines.append(f"- 书签数：{result.stats['bookmarks']}")
    lines.append(f"- **bookmark_only_candidate**（无真实正文锚点，未建节点）："
                 f"{len(bookmark_only)}")
    for item in bookmark_only[:20]:
        lines.append(f"  - 声明页={item.bookmark_declared_page} "
                     f"{item.text[:50]!r}")
    lines.append("- S2 只能佐证 S3，**任何**书签都不单独创建节点")
    lines.append("")

    lines.append("## 未解析引用（reference）")
    lines.append("")
    cross = [p for p in result.pending if p.kind == "cross_reference"]
    lines.append(f"- `toc_to_body` 未解析：{len(result.unresolved_edges)}")
    for entry, reason in result.unresolved_edges[:20]:
        lines.append(f"  - p{entry.page_number} {entry.entry_text.strip()[:40]!r} "
                     f"reason={reason}")
    lines.append(f"- `cross_reference` 登记待 TS4（本轮**不产出**该类边）："
                 f"{len(cross)}")
    lines.append("")

    lines.append("## 低置信 / 歧义候选（未强升为节点）")
    lines.append("")
    low_confidence = [
        c for c in result.candidates
        if not c.accepted
        and set(c.reason_codes) & set(LOW_CONFIDENCE_REASON_CODES)]
    lines.append(f"- 计数：{len(low_confidence)}")
    for item in low_confidence[:20]:
        lines.append(f"  - p{item.page_number} l{item.line_index} "
                     f"{item.text[:40]!r} reason={list(item.reason_codes)}")
    if len(low_confidence) > 20:
        lines.append(f"  - …（其余 {len(low_confidence) - 20} 项见 "
                     f"`outline_candidate_audit.json`）")
    ambiguous_lines = [a for a in result.line_assignments
                       if a.assignment == "unassigned_ambiguous"]
    lines.append(f"- 显式未归属行：{len(ambiguous_lines)}")
    lines.append("")

    lines.append("## TS4 待办（本轮明确不做）")
    lines.append("")
    pending_counts: dict = {}
    for item in result.pending:
        pending_counts[item.kind] = pending_counts.get(item.kind, 0) + 1
    for kind in sorted(pending_counts):
        lines.append(f"- {kind}: {pending_counts[kind]}")
    formal_reasons = formal_unassigned_reason_counts(result)
    lines.append(f"- 正式未归属 span（`DocumentOutline.unassigned`）："
                 f"{len(result.outline.unassigned)} 条**单行** span；"
                 f"细分原因见下（正文材料边界仍属 TS4（`pending_ts4`），"
                 f"本轮不构造任何正文材料 span）")
    for reason in UNASSIGNED_FORMAL_REASONS:
        lines.append(f"  - `{reason}`：{formal_reasons.get(reason, 0)}")
    return "\n".join(lines) + "\n"


def line_structure_diagnostic(result: OutlineBuildResult,
                              layout: PageLayout) -> dict:
    """每条非家具 `LayoutLine` 的**正式四态结构归属**（§六）。

    四态是封闭集合 `LINE_STRUCTURE_STATES`，每个非家具行**恰好**落入其中一个：

    - `heading_node`：该行是已采纳标题，挂着正式节点（`node_id` 非空）；
    - `formal_unassigned`：该行挂着正式 `DocumentOutline.unassigned` 里的 span
      （`unassigned_span_id` 非空）—— 来源可以是 body heading / TOC / bookmark，
      因此"可定位但未成为正式节点"的行不会从正式 Outline 里消失；
    - `non_content`：目录页行 / 目录项命中行 / 书签命中行（不是正文材料）；
    - `body_under_node`：其余普通正文行。

    **不静默继承**（§六(2)）：`formal_unassigned` 是一条真实边界，它之后的正文行
    **不得**继续挂在它之前那个已采纳标题下面。因此遇到 formal-unassigned 行即清空
    当前标题，其后的正文行 `owning_node_id` 为 `None`，并在 `body_attachment` 里如实
    标注原因（`after_formal_unassigned_boundary`），与文档开头的
    `before_first_heading` 区分开 —— 两种"没有归属"不是同一件事。

    正文材料边界（`body_under_node` 最终切到哪里）仍属 TS4；本函数只给出"按文档
    顺序最近且未被边界打断的已采纳标题"这一可复核从属提示。正式未归属状态**只**以
    `DocumentOutline.unassigned` 为准，本视图不得替代它。
    """
    if set(LINE_STRUCTURE_STATES) != {"heading_node", "formal_unassigned",
                                      "body_under_node", "non_content"}:
        raise OutlineBuildError(
            f"LINE_STRUCTURE_STATES 必须恰为四态，得到 {LINE_STRUCTURE_STATES!r}")
    node_by_line = {n.source_anchor[:2]: n.node_id for n in result.outline.nodes}
    span_by_line = {s.layout_line_refs[0]: s.span_id
                    for s in result.outline.unassigned}
    assignment_by_line = {(a.page_number, a.line_index): a.assignment
                          for a in result.line_assignments}
    current_node = None
    boundary_seen = False
    records: list = []
    for page in layout.pages:
        for line in page.lines:
            if line.is_furniture:
                continue
            key = (page.page_number, line.line_index)
            assignment = assignment_by_line.get(key)
            if assignment is None:
                raise OutlineBuildError(
                    f"诊断发现未被记账的非家具行：页 {key[0]} / 行 {key[1]}；"
                    f"行归属必须守恒")
            owning = attachment = None
            if key in node_by_line:
                current_node = node_by_line[key]
                boundary_seen = False
                state = "heading_node"
                owning = current_node
            elif key in span_by_line:
                # 正式未归属是一条**边界**：其后正文不得继承更早的标题。
                current_node = None
                boundary_seen = True
                state = "formal_unassigned"
            elif assignment in ("toc_candidate", "bookmark_candidate"):
                state = "non_content"
            else:
                state = "body_under_node"
                owning = current_node
                if current_node is not None:
                    attachment = "preceding_heading"
                elif boundary_seen:
                    attachment = "after_formal_unassigned_boundary"
                else:
                    attachment = "before_first_heading"
            records.append({
                "page_number": key[0], "line_index": key[1],
                "structure_state": state,
                "line_assignment": assignment,
                "node_id": owning,
                "unassigned_span_id": span_by_line.get(key),
                "owning_node_id": (owning if state == "body_under_node" else None),
                "body_attachment": attachment,
                # 兼容旧字段名：`heading_node` 行的所属节点即它自己。
                "preceding_heading_node_id": (
                    owning if state == "body_under_node" else None),
            })
    counts: dict = {state: 0 for state in LINE_STRUCTURE_STATES}
    for record in records:
        counts[record["structure_state"]] += 1
    non_furniture = int(result.stats["non_furniture_lines"])
    orphan_ids = [r for r in records
                  if r["structure_state"] == "heading_node" and not r["node_id"]]
    span_missing = [r for r in records
                    if r["structure_state"] == "formal_unassigned"
                    and not r["unassigned_span_id"]]
    return {
        "note": ("正式四态逐行归属：每个非家具行恰好落入 heading_node / "
                 "formal_unassigned / body_under_node / non_content 之一；"
                 "formal_unassigned 之后的正文不继承更早的标题。"
                 "正式未归属状态只以 DocumentOutline.unassigned 为准；"
                 "body_under_node 的正文切分属 TS4"),
        "states": list(LINE_STRUCTURE_STATES),
        "counts": dict(sorted(counts.items())),
        "total_lines": len(records),
        "non_furniture_lines": non_furniture,
        # 四态计数守恒：四态之和 == 逐行记录数 == 非家具行数。
        "four_state_sum": sum(counts.values()),
        "conserved": (sum(counts.values()) == len(records)
                      == non_furniture),
        "heading_node_without_node_id": len(orphan_ids),
        "formal_unassigned_without_span_id": len(span_missing),
        "lines": records,
    }


#: 结构复核门的**形态判据**（结构/标点形态，不是业务关键词、不是公司名或表格号）。
#: 标题是"名称"而不是"句子"：这些标记只产出**复核项**，不改变构建结果 —— 采纳与否
#: 仍只由 §四 的证据门槛决定。
PROSE_CLAUSE_MARKS: tuple = ("，", "；", "。", ",", ";", ".")
#: `人员履历` 的形态不是"含逗号"，而是**逗号分隔的短子句链**：`7、某先生，47岁，现任
#: 本公司董事…` 里夹在标点之间的子句都很短（姓名 / 年龄 / 职务）。真实年报模板标题
#: 也常含一个逗号（"七、与上年度财务报告相比，合并报表范围发生变化的情况说明"），
#: 因此判据要求**≥2 个子句标点**且**中间存在短子句**，不能只看"是否含逗号"。
PROSE_SHORT_CLAUSE_MAX_LEN: int = 8
#: 定义句的形态：定义冒号之后紧接**定义谓词**（`是指` / `系指` / `定义为` / `即为`）。
#: 只看冒号会把"2、特别现金分红：为积极落实…"这类真实模板标题误报为定义正文。
DEFINITION_MARKS: tuple = ("：", ":")
DEFINITION_PREDICATE_MARKS: tuple = ("是指", "系指", "定义为", "即为")


def prose_clause_chain(title: str) -> tuple:
    """`title` 里**夹在子句标点之间的短子句**（空元组 = 不构成短子句链）。

    只看标点位置与子句长度，不看任何词；首个子句（含编号与标题主体）与末个子句不参与
    判定，因为标题本身也可能以标点收尾。
    """
    flat = tight(title)
    parts: list = [flat]
    for mark in PROSE_CLAUSE_MARKS:
        parts = [piece for part in parts for piece in part.split(mark)]
    short: list = []
    for piece in parts[1:-1]:
        if 0 < len(piece) <= PROSE_SHORT_CLAUSE_MAX_LEN:
            short.append(piece)
    if len(parts) < 3:
        return ()
    return tuple(short)


def definition_shaped_title(title: str) -> bool:
    """`title` 是否是**定义正文形态**：定义冒号之后紧接定义谓词。

    只判断"冒号 + 紧跟的定义谓词"这一标点—词形事实，不看公司名、编号值、页码或任何
    业务关键词。单看冒号会把 `2、特别现金分红：为积极落实…` 这类真实模板标题误判为
    定义正文，因此谓词是必要条件。
    """
    flat = tight(title)
    for mark in DEFINITION_MARKS:
        if mark in flat:
            tail = flat.partition(mark)[2]
            return any(tail.startswith(p) for p in DEFINITION_PREDICATE_MARKS)
    return False


def prose_shaped_title(title: str) -> bool:
    """`title` 是否是**句子形态**（短子句链或定义句），而不是"名称"形态。

    与 `structural_review_findings` 的正文复核项共用同一判据：复核门看到什么形态，
    决策门就用什么形态。二者只做形态判断，不参与"是否采纳"的证据门槛本身 ——
    该门槛仍只由 §四 的主/支持证据计数决定。
    """
    return bool(prose_clause_chain(title)) or definition_shaped_title(title)


def _independent_region_facts(layout: PageLayout, page_number: int,
                              line_index: int) -> dict:
    """**独立验收方**自己从持久化 `PageLayout` 重算的区域事实（§二.4）。

    与生产侧 `table_region_scopes` 是**两条独立代码路径**：这里只共享纯几何原语
    `_layout_rows`（按真实 `bbox` 分行、按 x0 间距分簇）与 `trg-3` 的版式容差，判断
    本身在这里重新做一遍。因此即使生产侧的合并结论写错，本函数也不会跟着错 —— 这正是
    独立验收的意义。

    返回的 `scope` 是**独立重算**出来的两层状态，供验收门与生产侧自报的 `table_scope`
    逐条比对（矛盾即登记 `table_scope_mismatch` 缺口并 fail-closed）。
    """
    page = next((p for p in layout.pages if p.page_number == page_number), None)
    if page is None:
        return {"available": False}
    line = next((l for l in page.lines if l.line_index == line_index), None)
    if line is None:
        return {"available": False}
    naked = [l for l in page.lines if not l.is_furniture]
    rows = _layout_rows(naked)
    own = next((row for row in rows
                if any(l.line_index == line_index for l in row["lines"])), None)
    own_row_multi = bool(own is not None and own["multi"])
    # 同一基线上还有**起点不同**的另一条非家具行：最朴素的表格单元格事实。
    baseline_row = any(
        other.line_index != line_index
        and abs(other.bbox[1] - line.bbox[1]) <= TABLE_ROW_BASELINE_TOL_PT
        and abs(other.bbox[0] - line.bbox[0]) > TABLE_ROW_MIN_X_GAP_PT
        for other in naked)
    multi_rows = [row for row in rows if row["multi"]]
    # 上 / 下两侧是否被多字段行夹住（同一表格对象的结构行闭合），且字号等于夹住它的
    # 行的某格字号。
    above = below = False
    bracket_rows: list = []
    for row in multi_rows:
        if row is own:
            continue
        if _row_vertical_gap(line.bbox, row) > TABLE_REGION_PROXIMITY_PT:
            continue
        # 行带判据的上下界必须与生产侧共用同一个 `trg-3` 版式容差：差一点点重叠
        # （相邻行视觉上仍属同一行带）正是网格内部的常态。独立复核**独立重算判断**，
        # 但不得用一套比生产侧更紧的边界 —— 那会给复核门留下"生产侧判进网格、复核
        # 门看不见"的盲区，正是本函数要消除的问题。
        if row["y1"] <= line.bbox[1] + TABLE_REGION_ROW_OVERLAP_TOL_PT:
            above = True
            bracket_rows.append(row)
        elif row["y0"] >= line.bbox[3] - TABLE_REGION_ROW_OVERLAP_TOL_PT:
            below = True
            bracket_rows.append(row)
    band_bracketed = bool(
        above and below and any(_row_size_member(line, row)
                               for row in bracket_rows))
    # 邻近多字段行 ∧ 本行起点落在该行的列锚点上 ∧ 本行字号等于该行某格字号
    # （列**成员资格**，而不是"离得近"）。
    row_multi_anchor = any(
        row is not own
        and _row_vertical_gap(line.bbox, row)
        <= TABLE_REGION_COLUMN_ANCHOR_REACH_PT
        and any(abs(line.bbox[0] - column)
                <= TABLE_REGION_COLUMN_ANCHOR_TOL_PT
                for column in row["columns"])
        and _row_size_member(line, row)
        for row in multi_rows)
    # 处在两行**同族**多字段行之间的纯标签带里（判据 E 的独立重算）：夹住本行的两行
    # 多字段行列锚点逐列相近，两行之间的**每一**行都排在第二列起点之前（整段带里没有
    # 值列内容），且带内行的字号等于该表某格的字号。夹住本行的两行必须是**上下相邻**的
    # 结构行（中间不再夹别的多字段行）—— 否则相距很远的两个表格对象会把整页正文圈成
    # "标签带"，真实小标题因此被判成表格成员（与生产侧同一条闭合条件）。
    label_band = False
    for upper in multi_rows:
        if upper["y1"] >= line.bbox[1]:
            continue
        for lower in multi_rows:
            if lower["y0"] <= line.bbox[3]:
                continue
            if len(upper["columns"]) != len(lower["columns"]) \
                    or any(abs(a - b) > TABLE_REGION_FAMILY_TOL_PT
                           for a, b in zip(upper["columns"], lower["columns"])):
                continue
            if any(row is not upper and row is not lower
                   and row["y0"]
                   >= upper["y1"] - TABLE_REGION_ROW_OVERLAP_TOL_PT
                   and row["y1"]
                   <= lower["y0"] + TABLE_REGION_ROW_OVERLAP_TOL_PT
                   for row in multi_rows):
                continue
            value_edge = upper["columns"][1]
            band = [item for item in naked
                    if item.bbox[1] > upper["y1"] and item.bbox[3] < lower["y0"]]
            if len(band) < TABLE_REGION_BAND_MIN_LINES:
                continue
            if any(item.bbox[0] >= value_edge for item in band):
                continue
            if not any(_row_size_member(item, upper) for item in band):
                continue
            label_band = True
            break
        if label_band:
            break
    # 判据 C 的独立重算：与相邻**非结构行**构成被列宽截断的单元格片段。
    label_column_fragment = _independent_fragment_fact(
        layout, page_number, line_index)
    inside = bool(own_row_multi or baseline_row or band_bracketed
                  or row_multi_anchor or label_band or label_column_fragment)
    near = any(_row_vertical_gap(line.bbox, row)
               <= TABLE_REGION_PROXIMITY_PT for row in multi_rows)
    scope = (TABLE_SCOPE_INSIDE if inside
             else (TABLE_SCOPE_ADJACENT if near else TABLE_SCOPE_NONE))
    return {
        "available": True,
        "scope": scope,
        "own_row_multi": own_row_multi,
        "baseline_row": baseline_row,
        "band_bracketed": band_bracketed,
        "row_multi_anchor": row_multi_anchor,
        "label_band": label_band,
        "label_column_fragment": label_column_fragment,
    }


def _independent_fragment_fact(layout: PageLayout, page_number: int,
                               line_index: int) -> bool:
    """**独立验收方**自己重算的"被列宽截断的单元格片段"事实（§二.4）。

    与生产侧规则 C 同源（`trg-3` 版式参数），但**不读**生产侧的任何结论，只从真实
    `bbox` 重算：本行与相邻（上一行或下一行）的同列行构成一个垂直片段，**两行右端都
    止于同页邻近多字段行的第二列起点之前**（列宽截断），且两行都在该网格的行距范围内。

    只做"同列续写"是不够的 —— 一条**居中、字号抬升**的完整标题折行时同样会与下一行
    同列续写；那种行有自己的强主证据，不是表内片段。因此判据必须落在"被网格列宽
    截断"这一几何事实上。

    片段关系**只在两条非结构行之间成立**：多字段行自身的一格不是"同一单元格的续写
    片段"，它与相邻独立行之间是**表格边界**（缺了这一条，表后紧跟的真实标题会被当成
    表格成员的截断片段）。
    """
    page = next((p for p in layout.pages if p.page_number == page_number), None)
    if page is None:
        return False
    naked = [l for l in page.lines if not l.is_furniture]
    position = next((i for i, l in enumerate(naked)
                     if l.line_index == line_index), None)
    if position is None:
        return False
    line = naked[position]
    multi = [row for row in _layout_rows(naked) if row["multi"]]
    structural = {l.line_index for row in multi for l in row["lines"]}
    if line.line_index in structural:
        return False
    for other in (naked[position - 1] if position else None,
                  naked[position + 1] if position + 1 < len(naked) else None):
        if other is None or other.line_index in structural:
            continue
        gap = (other.bbox[1] - line.bbox[3] if other.bbox[1] >= line.bbox[3]
               else (line.bbox[1] - other.bbox[3]
                     if line.bbox[1] >= other.bbox[3] else -1.0))
        if not 0.0 <= gap <= TABLE_CELL_FRAGMENT_GAP_PT:
            continue
        if abs(other.bbox[0] - line.bbox[0]) > TABLE_CELL_FRAGMENT_X0_TOL_PT:
            continue
        for row in multi:
            if _row_vertical_gap(line.bbox, row) > TABLE_REGION_EXTENDED_PT \
                    or _row_vertical_gap(other.bbox, row) \
                    > TABLE_REGION_EXTENDED_PT:
                continue
            edge = row["columns"][1] + TABLE_CELL_FRAGMENT_EDGE_TOL_PT
            if line.bbox[2] <= edge and other.bbox[2] <= edge:
                return True
    return False


def _parse_table_scope(evidence: tuple) -> str | None:
    """从持久化审计记录里取回**生产侧自报**的 `table_scope`（缺失返回 `None`）。

    独立验收门**不采信**这个值：它先独立重算自己的 `scope`，再与本值逐条比对
    （矛盾 → `table_scope_mismatch`）；本值缺失 → `table_scope_unavailable`。
    """
    for item in evidence:
        if item.startswith("table_scope="):
            raw = item.partition("=")[2]
            return raw if raw in TABLE_SCOPES else None
    return None


def _parse_soft_reasons(evidence: tuple) -> tuple:
    """从持久化审计记录里取回 `soft_reasons`（候选自身登记的降级原因）。"""
    for item in evidence:
        if item.startswith("soft_reasons="):
            raw = item.partition("=")[2]
            return () if raw in ("", "-") else tuple(raw.split(","))
    return ()


def _parse_evidence_codes(evidence: tuple) -> tuple:
    """从持久化审计记录里取回 `primary_evidence` / `supporting_evidence` 两个元组。

    独立验收方**只读**候选自己的证据码，不读生产侧的采纳布尔值。
    """
    primary: tuple = ()
    supporting: tuple = ()
    for item in evidence:
        if item.startswith("primary_evidence="):
            raw = item.partition("=")[2]
            primary = () if raw in ("", "-") else tuple(raw.split(","))
        elif item.startswith("supporting_evidence="):
            raw = item.partition("=")[2]
            supporting = () if raw in ("", "-") else tuple(raw.split(","))
    return primary, supporting


def _independent_strong_primary_facts(layout: PageLayout, page_number: int,
                                      line_index: int, record_evidence: tuple,
                                      base_size: float, *,
                                      toc_landing_verified: bool = False) -> tuple:
    """独立验收方**自己重算**的强主证据（§二.4：不复用生产侧结论）。

    只认能从持久化版式 / 候选证据独立确证的那些：

    - `size_above_body`：用同一份持久化版面重算的正文基准字号，再看本行真实字号是否
      确实高于它 —— 生产侧声称的"字号抬升"在这里必须能被复算出来；
    - `centered`：重算行中心与页宽中心之差；
    - `bold_majority`：用同一份持久化版面重算该行字体的加粗事实（`_line_is_bold`）。
      实测三份真实年报里没有加粗的编号行，因此这一条在真实材料上是惰性的；它存在的
      意义是让 `HEADING_PRIMARY_EVIDENCE` 里没有不可达证据码；
    - `toc_body_landing`：**不**由本函数自己重算，而是由调用方传入
      `toc_landing_verified` —— 即 `_independent_source_landing_facts` 已在真实版式上
      重建出至少一条 `kind="toc"` 的对象级 landing。
      **`hq-4` 的关键点**：这里**不再**看 `toc_matches=` / `bookmark_matches=` 计数。
      同名计数是**召回**事实（生产侧自报），不是来源事实；尤其书签，其身份根本无法由
      `PageLayout` 重建，因此计数非零**不**构成任何一条可复核的强主证据。

    复算不出来的强主证据一律**不算** —— 这正是"不得复用生产侧布尔结论"的落点。
    """
    page = next((p for p in layout.pages if p.page_number == page_number), None)
    if page is None:
        return ()
    line = next((l for l in page.lines if l.line_index == line_index), None)
    if line is None:
        return ()
    verified: list = []
    if base_size > 0 and _line_size(line) > base_size + SIZE_ABOVE_BODY_EPS_PT:
        verified.append("size_above_body")
    center = (line.bbox[0] + line.bbox[2]) / 2.0
    if abs(center - page.width / 2.0) <= CENTER_TOLERANCE_PT:
        verified.append("centered")
    if _line_is_bold(line):
        verified.append("bold_majority")
    if toc_landing_verified:
        verified.append(TOC_BODY_LANDING_EVIDENCE)
    return tuple(dict.fromkeys(verified))


def _parse_accepted_by(evidence: tuple) -> str | None:
    """从候选审计记录里取回生产侧**自报**的采纳依据（`accepted_by=`；缺失 → `None`）。

    取值来自 `acceptance_basis` 的封闭集合（`toc_landing` / `numbering` / `style` /
    `-`）。复核侧只用它回答一个问题：**这条已采纳节点是不是按来源 landing 放行的**。
    自报值与复核侧重算结论不一致时（自报 `toc_landing` 但重建不出目录项 landing），
    按 `toc_landing_unverified_accepted` 阻断 —— 不得只留痕。
    """
    for item in evidence:
        if item.startswith("accepted_by="):
            raw = item.partition("=")[2]
            return raw or None
    return None


def _parse_source_landing(evidence: tuple) -> list | None:
    """从候选审计记录里取回生产侧**自报**的来源 landing 载荷（缺失 / 不可解析 → `None`）。

    独立验收门**不采信**载荷里的结论，只把它当作"生产侧声称有来源 landing"的入口：
    每一条都要在本函数之外、由复核侧在真实版式上重新算一遍。
    """
    for item in evidence:
        if item.startswith(f"{SOURCE_LANDING_FIELD}="):
            raw = item.partition("=")[2]
            if not raw:
                return None
            try:
                payload = json.loads(raw)
            except ValueError:
                return None
            if not isinstance(payload, list) or not payload:
                return None
            return payload
    return None


def _empty_source_landing_facts() -> dict:
    """来源 landing 复算结果的**空形态**（键集合与真实复算完全一致）。

    调用方经常要在"生产侧根本没自报来源载荷"时也走同一条代码路径，因此这个空形态必须
    带齐所有键：缺一个键就会在 `landing["available"]` 这种判读处抛 `KeyError`，把
    "没有来源载荷"变成崩溃而不是一条结论。语义上它等价于"复核侧复算不成立"。
    """
    return {"reported": False, "available": False, "entries": [],
            "navigation_only": [], "problems": [],
            "bookmark_identity_recheckable": False}


def _independent_source_landing_facts(layout: PageLayout | None, record,
                                      *, toc_pages: tuple | None = None,
                                      toc_entries: tuple | None = None) -> dict:
    """独立验收方**自己重算**的来源 landing（§三：验收器不得相信生产侧自报的字符串）。

    输入只有两样外部事实：持久化版式，以及候选审计记录里那串 `source_landing` 载荷。
    对目录项，复核侧重算的是：

    - 来源**对象身份**：`TocSource.create(...)` + `verify_against_layout` 必须能在真实
      版式上重建同一条身份（否则 `toc_source_id` 对不上就被丢弃）；
    - **来源页确实是目录页**：由复核侧自己 `collect_toc_entries` 重算的目录页集合判定，
      而不是采信生产侧"这是一条目录项"的声明；
    - **目标行**：被复核候选自己那一行的真实文本归一化后必须等于目录项标题；
    - **页面映射**：目录行里真实出现的页标签必须由真实版式唯一映射到候选所在物理页。

    **`hq-4` 的关键变化（§二.2）**：书签**不再**计入 `available`。书签只有"声明物理页 ==
    候选所在物理页"与"标题 == 目标行文本"两条可复核（书签来自 PDF 大纲，不在
    `PageLayout` 里；`ordinal` 是生产侧的身份声明，无法由版式重算）。两条都满足的书签
    只被登记进 `navigation_only`，并带确定性原因码 `bookmark_navigation_only` ——
    它是**导航候选 / 审计信息**，不是"来源已复核"。

    返回 `{"reported", "available", "entries", "navigation_only", "problems",
    "bookmark_identity_recheckable"}`：

    - `reported` = 生产侧自报了载荷；
    - `available` = **至少一条目录项**在复核侧通过对象级重算（书签永不使其为真）；
    - `navigation_only` = 复核侧确认可读、但只作导航的书签条目（逐条留痕，不丢弃）；
    - `bookmark_identity_recheckable` 是 `available` 的诚实同义名：书签身份不可重建，
      因此"来源可复核"只能由目录项成立。
    """
    payload = _parse_source_landing(record.evidence)
    out = _empty_source_landing_facts()
    out["reported"] = payload is not None
    if payload is None:
        return out
    if layout is None:
        out["problems"].append("缺持久化 `PageLayout`：来源 landing 无法独立重算")
        return out
    key = (record.page_number, record.line_index)
    landing = layout.line_at(*key)
    if landing is None:
        out["problems"].append("被复核候选的页码 / 行号在真实版式上不存在")
        return out
    for item in payload:
        if not isinstance(item, dict):
            out["problems"].append("载荷条目不是对象")
            continue
        kind = item.get("kind")
        if kind == "toc":
            if toc_pages is None:
                out["problems"].append(
                    "缺目录页上下文：无法独立确认来源页确实是目录页")
                continue
            if item.get("source_page") not in toc_pages:
                out["problems"].append("来源页不是复核侧重算出的目录页")
                continue
            if item.get("landing_page") != key[0] \
                    or item.get("landing_line") != key[1]:
                out["problems"].append("载荷自报的 landing 不是被复核候选人自己那一行")
                continue
            try:
                source = TocSource.create(
                    layout=layout, page_number=item["source_page"],
                    line_index=item["source_line"], char_start=item["char_start"],
                    char_end=item["char_end"],
                    declared_page_label=item["declared_page_label"])
                source.verify_against_layout(layout)
            except Exception as error:  # noqa: BLE001 - 逐条记录失败原因
                out["problems"].append(
                    f"目录项来源身份无法在真实版式上重算：{error}")
                continue
            if source.toc_source_id != item.get("toc_source_id"):
                out["problems"].append("目录项来源身份与复核侧重算结果不一致")
                continue
            if normalize_toc_title(landing.text) != item.get("title_normalized"):
                out["problems"].append("目标行文本与目录项标题不一致")
                continue
            physical = _resolve_page_label(layout, item["declared_page_label"])
            if physical != key[0]:
                out["problems"].append(
                    "目录项声明的页标签无法唯一映射到候选所在物理页")
                continue
            # §三.4：层级来源也由复核侧**自己重算** —— 在复核侧自己的目录项集合里找到
            # 同一条来源行，取它算出来的声明深度。载荷里自报的 `declared_depth` 只作
            # 对照，不参与任何判定。
            recomputed_depth = None
            if toc_entries is not None:
                for entry in toc_entries:
                    if (entry.page_number, entry.line_index) \
                            == (source.page_number, source.line_index):
                        recomputed_depth = entry.declared_depth
                        break
            out["entries"].append({
                "kind": "toc",
                "toc_source_id": source.toc_source_id,
                "source_page": source.page_number,
                "source_line": source.line_index,
                "declared_page_label": item["declared_page_label"],
                "physical_page": physical,
                "declared_depth": recomputed_depth,
                "reported_declared_depth": item.get("declared_depth"),
                "landing_page": key[0], "landing_line": key[1],
                "recomputed": True,
            })
        elif kind == "bookmark":
            if item.get("landing_page") != key[0] \
                    or item.get("landing_line") != key[1]:
                out["problems"].append("载荷自报的 landing 不是被复核候选人自己那一行")
                continue
            if item.get("bookmark_page") != key[0]:
                out["problems"].append("书签声明的物理页码与候选所在物理页不符")
                continue
            if normalize_toc_title(landing.text) != item.get("title_normalized"):
                out["problems"].append("目标行文本与书签标题不一致")
                continue
            # §二.2：两条可复核条件都成立也**只**是导航候选。原因码确定性、逐条落盘。
            out["navigation_only"].append({
                "kind": "bookmark",
                "bookmark_page": item.get("bookmark_page"),
                "landing_page": key[0], "landing_line": key[1],
                "navigation_only": True,
                "navigation_reason": BOOKMARK_NAVIGATION_ONLY_REASON,
                "recomputed": True,
            })
        else:
            out["problems"].append(f"未知来源类型 {kind!r}")
    # `hq-4`：`available` **只**由目录项成立。书签条目即使全部通过两条可复核条件，
    # 也只是 `navigation_only` —— 它的身份（PDF 大纲条目序号）不在版式里，复核方无权
    # 把"我读到了一模一样的标题和页码"当成"来源身份已重建"。
    out["available"] = any(entry["kind"] == "toc" for entry in out["entries"])
    out["bookmark_identity_recheckable"] = out["available"]
    return out


#: `toc_body_reconciliation` 的**全部**桶名（顺序即展示顺序）。
#: 消费方必须按本清单**显式**取桶，不得 `for name, items in recon.items()` —— 后者
#: 会在"桶 dict 加了一个非桶键"时静默把非列表值当成桶（`len("tocr-2") == 6`）。
#: 它是 `V.TOC_BODY_RECONCILIATION_VERSION == "tocr-2"` 的桶集合；`tocr-1` 的桶集合
#: 是 `("toc_body_resolved", "toc_source_only", "toc_target_unresolved",`
#: `"bookmark_navigation_only", "body_heading_unassigned")` —— 见 `versions.py` 的
#: legacy 登记，旧载荷不得静默按本清单解释。
TOC_BODY_RECONCILIATION_BUCKETS: tuple = (
    "toc_body_resolved",
    "toc_body_unassigned",
    "toc_target_unresolved",
    "bookmark_navigation_only",
    "body_heading_unassigned",
)

#: `toc_body_reconciliation` 里**阻断**关闭门的桶（`tocr-2` 起）。
#: `toc_body_unassigned` 表示"目录项已在真实版式上闭合到一条合格正文行，而该行不在树
#: 里" —— 它是「可能存在正文标题漏收」的直接证据，因此令所在文档与整轮 TS3 关闭门失败。
#: 其余四桶分别表示"已闭合 / 页目标未闭合 / 只有导航 / 广义正文候选"，都不阻断。
TOC_BODY_RECONCILIATION_BLOCKING_BUCKETS: tuple = ("toc_body_unassigned",)


def toc_body_reconciliation_declaration() -> dict:
    """对账的**版本 + 阻断桶清单**的唯一落盘来源（`toc_body_reconciliation_*` 两个键）。

    它们必须随产物落盘，而不是只在验收脚本里从常量现取：验收方要能拿**持久化载荷
    自身**核对"这份产物是按哪版语义、哪些桶算阻断产生的"。缺少任一键、或值与当前
    常量不符 ⇒ 该载荷是 legacy / incomplete，run 级生成与验收一律 fail-closed，
    绝不静默按当前语义解释。
    """
    return {
        "toc_body_reconciliation_version": V.TOC_BODY_RECONCILIATION_VERSION,
        "toc_body_reconciliation_blocking_buckets":
            list(TOC_BODY_RECONCILIATION_BLOCKING_BUCKETS),
    }


def toc_body_reconciliation(result: OutlineBuildResult,
                            layout: "PageLayout | None" = None) -> dict:
    """**目录来源**与**正文落地**的对账（§三.5 / §三.6，`hq-4`）。

    目的只有一个：把过去被混成一句"真标题漏收"的**两类完全不同的东西**分开。

    - **目录来源行**（`TocSource` / `TocEntry`）在目录页上，是**导航来源**；它**不是**
      正文行，既不得进正文树，也不是"被漏收的正文标题"。
    - **正文 landing 行**是页映射之后真实存在的正文行，只有它才可能成为 `OutlineNode`。

    五类**确定性**桶（全部由复核侧**自己**从持久化版式重算，不读生产侧结论）。
    桶名与语义的版本是 `V.TOC_BODY_RECONCILIATION_VERSION`：

    - `toc_body_resolved`：目录项 → 声明的页标签唯一映射到某物理页 → 该页上存在文本一致
      的真实正文行（且该行**不是**普通表格内容）→ 且该行已经是一个**正式节点**。
      三段全部成立才计入。
    - `toc_body_unassigned`（**阻断**，`tocr-2` 起）：目录项在真实版式上**确已闭合到一条
      合格正文行**（TocSource 身份可重建、页标签唯一映射、该页上存在文本一致、非
      furniture、非普通表格内容的真实正文行），但该行**不是**任何正式 `OutlineNode`。
      它表示"**可能存在正文标题漏收**"，因此必须进入 blocking / manual-review，令所在
      文档与整轮 TS3 关闭门**失败**，并逐条保留可复核身份（TocSource 身份、声明页标签、
      物理页、landing 行坐标与正文文本、候选拒绝原因、可关联的未归属 span 身份）。
      **不得**为清零它而自动把该正文行加入树：只有既有标题资格规则本身足以采纳时才允许
      生成节点，否则如实阻断。

      `tocr-1` 曾把同一状态记成 `toc_source_only` 并宣称"目录来源仅有导航价值、**不是**
      「真标题漏收」"。那是 **fail-open**：这条 landing 是**正文行**，不是目录页上的来源
      行，它的缺席恰恰是漏收的直接证据。该桶名与语义已废除，`tocr-1` 载荷按 legacy 识别。
    - `toc_target_unresolved`：目录项**声明的页目标在版式上无法确认**：页标签无法唯一
      映射到物理页，映射到的页上没有任何文本一致的真实正文行，或该页上只有同名的**普通
      表格内容**行（判据 A：多字段表格行的一格 —— 它不是正文标题行，`§四 P1-B(3)`）。
      **不得**为它编造节点，也不得把它记为"落地成功"。若树上**另有**同名正文标题节点，
      本条**照样**留在本桶（页映射是硬条件），但必须逐条带 `same_title_node_ids` /
      `same_title_node_paths` 把"这个正文标题其实已经进树了"写成**事实** ——
      "目录项的页目标没闭合"**不等于**"该正文标题漏收"，两者不得互相冒充。
      它表示"没有形成有效正文 landing"，与 `toc_body_unassigned` **不得**合并。
    - `bookmark_navigation_only`：书签来源全部登记在这里。书签来自 PDF 大纲，不是
      `PageLayout` 上的行；生产侧对它的命中只作导航候选 / 审计信息。它**不**获得
      available、标题资格或表格穿透能力，也**不得**进入 `toc_body_unassigned`。
    - `body_heading_unassigned`：**正文**标题候选（`kind="heading"`）里未被采纳的那些，
      逐条带候选级原因码。它是**广义正文候选**诊断，与"TOC 已闭合到具体正文行但未入树"
      的 `toc_body_unassigned` 来自**不同对象**，因此不得互相冒充。

    本函数**只做对账**，不改变任何采纳结论、不提高召回，**也不**为了让某个桶清零而
    改变节点集。
    """
    out = {
        "toc_body_resolved": [], "toc_body_unassigned": [],
        "toc_target_unresolved": [], "bookmark_navigation_only": [],
        "body_heading_unassigned": [],
    }
    if layout is None:
        return out
    _toc_pages, toc_entries = collect_toc_entries(layout)
    nodes = {n.source_anchor[:2]: n for n in result.outline.nodes}
    # §四 P1-B(2)(7)：同名**正文标题节点**的索引。它只用来**说明事实** ——
    # "这条目录项声明的页目标没有闭合"与"这个正文标题没有被收进树"是**两件事**；
    # 后者若成立，读产物的人必须在这里看得见，否则一句"目标未解析"会被读成
    # 「真标题漏收」。按标题索引**只作提示**：绝不把命中同名的目录项改判成已落地
    # （页映射是硬条件，不能由同名绕过，见 §四 P1-B(3)(4)(5)）。
    nodes_by_title: dict = {}
    for _node in result.outline.nodes:
        # 与 `toc_landing_targets` 同一套归一（`normalize_toc_title`），两侧索引口径一致。
        nodes_by_title.setdefault(normalize_toc_title(_node.title), []).append(_node)

    def _same_title_node_facts(entry) -> dict:
        """同名正文节点事实（**不是**落地判据）。"""
        same = nodes_by_title.get(entry.title_normalized) or ()
        if not same:
            return {}
        return {
            "same_title_node_ids": [n.node_id for n in same],
            "same_title_node_paths": [list(n.structural_path) for n in same],
        }

    def _same_title_clause(facts: dict) -> str:
        """把"同名正文标题**已经**是节点"这件事写成**事实陈述**，不写成落地结论。"""
        return ("。**但**：本树上**存在**同名正文标题节点 "
                f"{facts['same_title_node_ids']}（结构路径 "
                f"{facts['same_title_node_paths']}）—— 因此本条只说明**这条目录项声明的"
                "页目标**未闭合，**不得**据此宣称该正文标题漏收；反过来，同名节点也**不**"
                "因为这条目录项而获得任何落地身份（页标签 → 物理页 → 文本一致的正文行是"
                "硬条件，不能由同名绕过）")

    # §tocr-2：`toc_body_unassigned` 必须逐条保留"这条 landing 行为什么没进树"的
    # 可复核身份。两个索引都用**复核侧自己**持久化读数建立，不读生产侧结论：
    # - 该锚点上的标题候选审计记录（含拒绝原因码）：`landing_candidate_rejections`；
    # - 该锚点上的**正式未归属 span**：目录项自身在找不到正文锚点时会留下一条锚在
    #   目录页来源行的 `toc_unmatched` 单行 span，正文 landing 行被拒时也可能留下自己
    #   的未归属 span。两者都要能按锚点查出来，否则读产物的人只能看到"没进树"。
    heading_records_by_anchor: dict = {}
    for _record in result.candidates:
        if getattr(_record, "kind", None) == "heading":
            heading_records_by_anchor.setdefault(
                (_record.page_number, _record.line_index), []).append(_record)
    unassigned_by_anchor: dict = {}
    for _span in result.outline.unassigned:
        unassigned_by_anchor.setdefault(_span.start_anchor[:2], []).append(_span)

    def _unassigned_span_facts(*anchors) -> dict:
        """按锚点取正式未归属 span 身份（**不是**落地判据，只作可关联身份）。"""
        spans: list = []
        for anchor in anchors:
            for span in unassigned_by_anchor.get(anchor, ()):
                spans.append({
                    "span_id": span.span_id,
                    "start_anchor": [span.start_anchor[0], span.start_anchor[1]],
                    "page_range": list(span.page_range),
                    "unassigned_reason": span.unassigned_reason,
                })
        if not spans:
            return {}
        return {"unassigned_span_ids": [s["span_id"] for s in spans],
                "unassigned_spans": spans}

    # §四 P1-B(3)：目标行判据要用的两层表格状态。目录项为空时不必付这次重算。
    scopes = table_region_scopes(layout) if toc_entries else {}
    for entry in toc_entries:
        item = {
            "source_page": entry.page_number,
            "source_line": entry.line_index,
            "title": entry.title_part,
            "title_normalized": entry.title_normalized,
            "declared_page_label": entry.declared_page_label,
            "declared_depth": entry.declared_depth,
        }
        physical = (_resolve_page_label(layout, entry.declared_page_label)
                    if entry.declared_page_label else None)
        if physical is None:
            why = ("目录项声明的页标签无法由真实版式的页码 furniture 唯一映射到"
                   "任何物理页：**这条目录项声明的页目标**未解析，不得为它编造节点")
            facts = _same_title_node_facts(entry)
            if facts:
                why += _same_title_clause(facts)
            out["toc_target_unresolved"].append(dict(item, why=why, **facts))
            continue
        landing = None
        table_cell = None
        page = next((p for p in layout.pages if p.page_number == physical), None)
        if page is not None:
            for line in page.lines:
                if line.is_furniture:
                    continue
                if normalize_toc_title(line.text) != entry.title_normalized:
                    continue
                # §四 P1-B(3)：同名的**普通表格内容**行不是正文 landing。目标行判据与
                # `scan_heading_candidates` 用同一个函数读同一份真实版式状态，两侧结论
                # 因此可以逐条比对上（不允许"生产侧不收、对账侧仍算落地"）。
                scope, reason = scopes.get(
                    (physical, line.line_index), (TABLE_SCOPE_NONE, None))
                if not toc_landing_target_is_body_line(scope, reason):
                    if table_cell is None:
                        table_cell = line
                    continue
                landing = line
                break
        if landing is None:
            why = ("页标签唯一映射到了物理页，但该页上**没有**任何归一文本与目录项标题"
                   "一致的真实正文行：**这条目录项声明的页目标**未解析，不得为它编造节点")
            if table_cell is not None:
                why = ("页标签唯一映射到了物理页，该页上确实存在同名的**普通表格内容**行"
                       f"（多字段表格行的一格，{TOC_LANDING_TABLE_CELL_REASON}）：它不是"
                       "正文标题行，目录项指向它也不得让它成为 landing，更不得为它编造节点")
            facts = _same_title_node_facts(entry)
            if facts:
                why += _same_title_clause(facts)
            out["toc_target_unresolved"].append(dict(
                item, physical_page=physical, why=why, **facts))
            continue
        key = (physical, landing.line_index)
        node = nodes.get(key)
        if node is None:
            # §tocr-2：**这是阻断项，不是留痕**。目录项已在真实版式上闭合到一条合格
            # 正文行，而该行不在树里 —— 这就是"可能存在正文标题漏收"的直接证据。
            # 逐条带出可复核身份：来源对象身份、声明页标签、物理页、landing 行坐标与
            # 正文文本、该 landing 行的候选拒绝原因、可关联的正式未归属 span。
            try:
                source = TocSource.create(
                    layout=layout, page_number=entry.page_number,
                    line_index=entry.line_index, char_start=entry.char_start,
                    char_end=entry.char_end,
                    declared_page_label=entry.declared_page_label)
                source.verify_against_layout(layout)
                source_facts = {
                    "toc_source_id": source.toc_source_id,
                    "toc_source_locator": source.toc_source_locator,
                    "toc_builder_version": source.toc_builder_version,
                    "char_start": source.char_start,
                    "char_end": source.char_end,
                    "entry_text": source.entry_text,
                    "toc_source_rebuildable": True,
                }
            except Exception as exc:  # pragma: no cover - 目录项来自同一份版式
                # fail-closed：重建不出身份时**照样**阻断，并如实记录原因，绝不因为
                # "身份重建失败"就把这条状态降级成留痕。
                source_facts = {
                    "toc_source_rebuildable": False,
                    "toc_source_rebuild_error": f"{type(exc).__name__}: {exc}",
                }
            out["toc_body_unassigned"].append(dict(
                item, physical_page=physical, landing_line=landing.line_index,
                landing_text=landing.text,
                landing_line_index=landing.line_index,
                landing_candidate_rejections=[{
                    "accepted": bool(c.accepted),
                    "reason_codes": list(c.reason_codes),
                    "declared_level": getattr(c, "declared_level", None),
                    "node_id": getattr(c, "node_id", None),
                } for c in heading_records_by_anchor.get(key, ())],
                blocking=True,
                action_required=("人工复核该正文 landing 行为何没有成为正式节点：目录项在"
                                 "真实版式上已闭合到这条合格正文行，它不在树里即表示可能"
                                 "存在标题漏收"),
                **source_facts,
                **_unassigned_span_facts(key, (entry.page_number, entry.line_index)),
                why=("目录项在真实版式上**已闭合到一条合格正文行** —— 来源身份可重建、"
                     "声明的页标签唯一映射到该物理页、该页上存在文本一致、非家具、"
                     "非普通表格内容的真实正文行 —— 但该行**不是**任何正式 "
                     "`OutlineNode`（`toc_body_unassigned`）。这是「可能存在正文标题漏收」"
                     "的**直接证据**，因此它是**阻断项**：不得为清零它而自动把该正文行"
                     "加入树（只有既有标题资格规则本身足以采纳时才允许生成节点），"
                     "也不得把它降级成「目录来源仅有导航价值」的留痕 —— `tocr-1` 的 "
                     "fail-open 已废除")))
            continue
        out["toc_body_resolved"].append(dict(
            item, physical_page=physical, landing_line=landing.line_index,
            node_id=node.node_id, node_title=node.title,
            node_path=list(node.structural_path),
            why="目录项 → 页标签 → 物理页 → 文本一致的真实正文行 → 正式节点，四段闭合"))
    for record in result.candidates:
        kind = getattr(record, "kind", None)
        if kind == "bookmark":
            out["bookmark_navigation_only"].append({
                "declared_page": record.page_number, "title": record.text,
                "declared_level": record.declared_level,
                "reason_codes": list(record.reason_codes),
                "navigation_reason": BOOKMARK_NAVIGATION_ONLY_REASON,
                "why": ("书签来自 PDF 大纲，不在 `PageLayout` 里：它只作导航候选 / 审计"
                        "信息，不参与标题资格、不穿透 `inside_table`、不使复核侧 "
                        "`available` 为真"),
            })
        elif kind == "heading" and not record.accepted:
            out["body_heading_unassigned"].append({
                "page_number": record.page_number, "line_index": record.line_index,
                "title": record.text, "reason_codes": list(record.reason_codes),
                "why": ("正文标题候选未被采纳（`body_heading_unassigned`）：它是**广义**"
                        "正文候选诊断，与目录来源行**不同对象**，既不得与 "
                        "`toc_body_unassigned` 混为一谈，也不得与目录来源行混为一谈"),
            })
    return out


def structural_review_findings(result: OutlineBuildResult,
                               layout: "PageLayout | None" = None) -> dict:
    """真实产物的**结构复核门**（§六(5) / §二.4）：**独立**识别已知的真实问题。

    七类都必须能被机械识别，否则 `manual_review.md` 会在存在这些问题时写出
    "自动检查无失败项"这样不成立的结论：

    1. `same_declared_level_nesting`（**同级误嵌套**）：父子边的两侧声明层级
       （`HeadingCandidate.declared_level`，0 基）**相等**且都来自编号体系。同一编号
       体系里的 `1、2、3、4` 必须是 siblings；一旦挂成父子，就是 §三 禁止的串树。
       没有编号（`declared_level is None`）的行不参与本判据 —— 无编号标题的层级由
       局部结构域决定，不构成"同级"事实。
    2. `prose_like_accepted` / `definition_like_accepted`（**正文误收**）：已采纳标题
       呈"逗号分隔的**短子句链**"（人员履历 "7、某先生，47岁，现任董事……"）或
       "定义冒号 + 定义谓词"（"二、重要缺陷：是指……"）。判据只看标点位置、子句
       长度与定义谓词，不看公司名、页码、编号值或任何业务关键词 —— 真实年报模板标题
       也常含一个逗号，所以"含逗号"本身不是判据（详见 `prose_clause_chain`）。
    3. `toc_unmatched` / `bookmark_unmatched` / `numbered_list_ambiguity`
       （**待人工复核候选显化**）：正式 `DocumentOutline.unassigned` 里因为"目录项 /
       书签有真实 landing 行但没能对应到节点"或"编号列表 / 表格区域上下文"而未归属的
       span —— 未被采纳的候选**必须显化**，不得从正式 Outline 里静静消失。
       `numbered_list_ambiguity` **不是**"真标题漏收"：这些候选没有独立人工标注金标，
       既可能是真小标题也可能是表内标签，因此这里统一按**待人工复核候选**记录。
    4. `global_peer_only_accepted`（**全文件候选互证误收**）：已采纳标题**自身**没有任何
       主证据，标题资格只来自"同一编号层级上另有一条版式相近的候选"——即 §二.1 点名的
       循环自证。逐条记录该"出借方"是否落在**同页或相邻页窗口**之外
       （`peer_outside_local_domain`）。
    5. `table_region_accepted`（**表格成员误收**）：已采纳标题是表格**成员**
       （`inside_table`；`trg-3`）且**没有通过**安全穿透资格。两层状态与成员资格由本函数
       **自己**从持久化版式重算（见 `_independent_region_facts`），不复用生产侧的合并结论。
    6. `fragmented_cell_accepted`（**截断单元格误收**）：已采纳标题是被列宽截断 / 与下一行
       同列续写的单元格片段，由本函数自己重算（`_independent_fragment_fact`）。
    7. `adjacent_heading_rejected`（**邻近表格的标题被误拒**）：候选只是 `adjacent_to_table`
       （没有任何成员资格证明），却被抽掉了弱主证据 `standalone_line` —— 补回它证据算术
       即成立。这是 §三.3 禁止的"只因为贴着表格就拒绝"，正常实现下恒为 0。
    8. `accepted_without_intrinsic_primary_evidence`：已采纳标题的证据码里**没有**任何
       属于 `HEADING_PRIMARY_EVIDENCE` 的自身事实（第 4 类的超集）。

    **阻断语义（§三.4 / `hq-3`）**：`inside_table` 且被采纳的节点**一律**进入阻断复核。
    只有通过安全穿透资格的节点进入**非阻断**的 `safe_table_override_accepted` 留痕，且
    **逐条**输出证据（独立重算的来源 landing / 几何事实）；其余计入
    `unsafe_table_override_accepted`。门要求
    `inside_table_accepted == len(safe_table_override_accepted)`。

    **两侧对称（§三）**：安全穿透资格在两侧**逐条同形**，且验收侧更严格 —— 唯一依据是
    **对象级验证过的目录项 landing**（`_independent_source_landing_facts`：目录项要能在
    真实版式上重建 `TocSource` 身份、来源页要确是复核侧自己重算出的目录页、目标行文本
    要一致、页标签要唯一映射到候选所在物理页）。版式样式（`size_above_body` /
    `centered` / `bold_majority`）无论几项、普通 supporting 证据都不放行。
    **书签在 `hq-4` 也不再放行**：复核侧只把它登记成 `navigation_only`（导航候选），
    不让它使 `available` 为真 —— 它的身份（PDF 大纲条目序号）不在持久化版式里。
    两侧结论分叉时**两个方向都显式阻断**：`safe_override_mismatch`（生产侧判 safe、
    复核侧复算不 safe）与 `unsafe_override_mismatch`（生产侧判 unsafe、复核侧复算 safe）；
    已采纳表内标题缺可独立复核的目录项 landing 上下文时另记 `source_landing_unverified`
    缺口；已采纳节点自报"凭来源 landing 采纳"而复核侧重建不出来时记
    `toc_landing_unverified_accepted`（同样是**阻断**计数）。

    **fail-closed（§三.10）**：`layout`（或 `result.layout`）缺失时，第 5–8 类无法独立
    复核；候选审计缺少持久化的 `table_scope` 时无法区分 `inside_table` 与
    `adjacent_to_table`；自报 `table_scope` 与独立重算矛盾时不得采信生产侧结论 ——
    三种情形都不返回 clean，而是显式记录 `verification_gaps`。

    返回计数与逐条明细；`clean` 仅表示"未发现上述形态复核项且独立复核数据齐备"，
    **不表示**标题树通过。
    """
    effective_layout = layout if layout is not None else result.layout
    verification_gaps: list = []
    region_cache: dict = {}
    if effective_layout is None:
        verification_gaps.append({
            "kind": "page_layout_unavailable",
            "why": ("结构复核门拿不到 `PageLayout`：表格区域 / 截断单元格 / 自身主证据"
                    "三类无法独立复核。按 fail-closed 处理 —— 不得据此返回 clean。"),
            "action_required": "以 `result.layout` 或显式 `layout=` 提供持久化版式后重跑",
        })
    if not result.candidates:
        verification_gaps.append({
            "kind": "candidate_audit_unavailable",
            "why": "候选审计记录为空：无法复核任何标题资格来源。",
            "action_required": "确认构建结果确实携带了候选审计",
        })
    node_by_anchor = {n.source_anchor[:2]: n for n in result.outline.nodes}
    # 同一锚点上可能同时存在正文标题记录与目录 / 书签落地记录，因此只取**正文标题**
    # 记录，并在同锚点重复时优先取被采纳的那一条（层级事实属于真实节点那一行）。
    candidate_by_anchor: dict = {}
    for record in result.candidates:
        if getattr(record, "kind", None) != "heading":
            continue
        key = (record.page_number, record.line_index)
        current = candidate_by_anchor.get(key)
        if current is None or (record.accepted and not current.accepted):
            candidate_by_anchor[key] = record
    nodes = {n.node_id: n for n in result.outline.nodes}

    nesting: list = []
    for node in result.outline.nodes:
        if node.parent_id is None:
            continue
        parent = nodes.get(node.parent_id)
        child_cand = candidate_by_anchor.get(node.source_anchor[:2])
        parent_cand = (None if parent is None
                       else candidate_by_anchor.get(parent.source_anchor[:2]))
        if child_cand is None or parent_cand is None:
            continue
        declared = child_cand.declared_level
        if declared is None or parent_cand.declared_level != declared:
            continue
        nesting.append({
            "parent": {"node_id": parent.node_id,
                       "page_number": parent.source_anchor[0],
                       "line_index": parent.source_anchor[1],
                       "title": parent.title,
                       "declared_level": parent_cand.declared_level},
            "child": {"node_id": node.node_id,
                      "page_number": node.source_anchor[0],
                      "line_index": node.source_anchor[1],
                      "title": node.title, "declared_level": declared},
            "why": ("同一编号体系内声明层级相同却挂成父子：同级标题必须是 "
                    "siblings（§三）"),
        })

    prose_like: list = []
    definition_like: list = []
    # `result.candidates` 是**类型化审计记录**（`CandidateAuditRecord`），字段是
    # `text` / `accepted` / `node_id` / `evidence` / `reason_codes`，不是
    # `HeadingCandidate`（那是扫描期内部对象，构建结果里不保留）。
    for record in result.candidates:
        if not record.accepted:
            continue
        title = record.text
        short_clauses = prose_clause_chain(title)
        if short_clauses:
            prose_like.append({
                "page_number": record.page_number,
                "line_index": record.line_index, "title": title,
                "short_clauses": list(short_clauses), "node_id": record.node_id,
                "declared_level": record.declared_level,
                "applied_level": record.applied_level,
                "evidence": list(record.evidence),
                "why": ("已采纳标题是逗号分隔的短子句链：形态上更像正文句子"
                        "（人员履历 / 叙述列表）"),
            })
            continue
        flat_title = tight(title)
        mark = next((m for m in DEFINITION_MARKS if m in flat_title), "")
        tail = flat_title.partition(mark)[2] if mark else ""
        predicates = [m for m in DEFINITION_PREDICATE_MARKS if tail.startswith(m)]
        if definition_shaped_title(title):
            definition_like.append({
                "page_number": record.page_number,
                "line_index": record.line_index, "title": title,
                "mark": mark, "predicates": predicates, "node_id": record.node_id,
                "declared_level": record.declared_level,
                "applied_level": record.applied_level,
                "evidence": list(record.evidence),
                "why": "已采纳标题在定义冒号后紧接定义谓词：形态上更像定义正文",
            })

    missed: dict = {}
    for reason in (UNASSIGNED_TOC_UNMATCHED, UNASSIGNED_BOOKMARK_UNMATCHED,
                   UNASSIGNED_NUMBERED_LIST_AMBIGUITY):
        missed[reason] = []
    for span in result.outline.unassigned:
        reason = span.unassigned_reason
        if reason in missed:
            missed[reason].append({
                "span_id": span.span_id,
                "page_number": span.start_anchor[0],
                "line_index": span.start_anchor[1],
                "text": span.normalized_text,
                "page_range": list(span.page_range),
                "unassigned_reason": reason,
            })

    orphan_heading_candidates = [
        {"page_number": r.page_number, "line_index": r.line_index,
         "title": r.text, "reason_codes": list(r.reason_codes),
         "why": "候选被采纳却没有对应的正式节点：正文行不得静默丢失"}
        for r in result.candidates
        if r.accepted and r.node_id is None]

    # --- §二.4：独立验收 —— 自己从候选证据码与持久化版式重算，不复用生产侧布尔结论 ---
    accepted_heading_records = [
        r for r in result.candidates
        if getattr(r, "kind", None) == "heading" and r.accepted]
    local_class_index: dict = {}
    for record in accepted_heading_records:
        local_class_index.setdefault(record.declared_level, []).append(record)

    peer_only: list = []
    without_intrinsic: list = []
    table_region_accepted: list = []
    fragmented_cell_accepted: list = []
    safe_table_override_accepted: list = []
    adjacent_heading_rejected: list = []
    inside_table_accepted_count = 0
    scope_unavailable: list = []
    scope_mismatch: list = []
    safe_override_mismatch: list = []
    unsafe_override_mismatch: list = []
    source_landing_unverified: list = []
    toc_landing_unverified: list = []
    bookmark_navigation_only: list = []
    base_size = (0.0 if effective_layout is None
                 else body_font_size(effective_layout))
    #: 目录页集合 / 目录项集合由复核侧自己重算（`collect_toc_entries`），用于确认
    #: "来源页确实是目录页"并复算层级深度。只在确有候选自报来源 landing 时才计算，
    #: 避免对普通产物多做一遍全文件扫描。
    toc_page_cache: tuple | None = None
    toc_entry_cache: tuple | None = None

    def _toc_collect_for_audit() -> tuple:
        nonlocal toc_page_cache, toc_entry_cache
        if toc_page_cache is None:
            collected = ((), ()) if effective_layout is None \
                else collect_toc_entries(effective_layout)
            toc_page_cache, toc_entry_cache = tuple(collected[0]), tuple(collected[1])
        return toc_page_cache

    def _toc_pages_for_audit() -> tuple:
        return _toc_collect_for_audit()

    def _toc_entries_for_audit() -> tuple:
        _toc_collect_for_audit()
        return toc_entry_cache or ()
    for record in accepted_heading_records:
        primary, supporting = _parse_evidence_codes(record.evidence)
        intrinsic = tuple(code for code in primary
                          if code in HEADING_PRIMARY_EVIDENCE)
        entry_common = {
            "page_number": record.page_number, "line_index": record.line_index,
            "title": record.text, "node_id": record.node_id,
            "declared_level": record.declared_level,
            "applied_level": record.applied_level,
            "primary_evidence": list(primary),
            "supporting_evidence": list(supporting),
        }
        # §二/§三（`hq-4`）：来源 landing 先复算一次，供三处共用 —— 强主证据复算、
        # 表内穿透资格、以及"生产侧是否按**复核侧重建不出来**的依据放行"。只在生产侧
        # 确实自报了载荷时才做，普通已采纳节点不付这份代价。
        landing: dict = _empty_source_landing_facts()
        toc_landing_verified = False
        if effective_layout is not None \
                and _parse_source_landing(record.evidence) is not None:
            landing = _independent_source_landing_facts(
                effective_layout, record, toc_pages=_toc_pages_for_audit(),
                toc_entries=_toc_entries_for_audit())
            toc_landing_verified = any(
                entry["kind"] == "toc" for entry in landing["entries"])
            if landing.get("navigation_only"):
                bookmark_navigation_only.append(dict(
                    entry_common, navigation_only=landing["navigation_only"],
                    why=("该已采纳节点的来源载荷里有**书签**命中。书签只作导航候选 / "
                         "审计信息（`bookmark_navigation_only`）：它的身份无法由 "
                         "`PageLayout` 重建，因此不参与标题资格、不穿透 `inside_table`、"
                         "也不使复核侧 `available` 为真")))
        # §二.2（`hq-4`）：生产侧声称"凭来源 landing 采纳"（`accepted_by=toc_landing`）
        # 或证据码里挂着 `toc_body_landing`，而复核侧**重建不出来**对象级目录项 landing
        # —— 这正是"按一条复核方无权重建的依据放行"。它是**阻断**计数，不得只留痕。
        reported_basis = _parse_accepted_by(record.evidence)
        if (reported_basis == "toc_landing" or TOC_BODY_LANDING_EVIDENCE in primary) \
                and not toc_landing_verified:
            toc_landing_unverified.append(dict(
                entry_common, accepted_by=reported_basis or "-",
                landing=landing,
                why=("生产侧把这行当作「已验证目录项的正文 landing」采纳，但复核侧在真实 "
                     "版式上重建不出任何对象级目录项 landing（载荷缺失 / 不可解析 / "
                     "来源身份、目录页身份、目标行或页标签映射重算不成立）："
                     "按 fail-closed 阻断，不得返回 clean")))
        if not intrinsic:
            without_intrinsic.append(dict(
                entry_common,
                why=("已采纳标题的 primary_evidence 里没有任何 "
                     "HEADING_PRIMARY_EVIDENCE 自身事实")))
        if not intrinsic and "level_layout" in supporting:
            peers: list = []
            for other in local_class_index.get(record.declared_level, ()):
                if other is record:
                    continue
                if abs(other.page_number - record.page_number) \
                        <= NEARBY_PAGE_WINDOW_PAGES:
                    continue
                peers.append({"page_number": other.page_number,
                              "line_index": other.line_index,
                              "title": other.text})
            peer_only.append(dict(
                entry_common,
                peer_outside_local_domain=bool(peers),
                peers_outside_local_domain=peers[:8],
                why=("标题资格只来自「同一编号层级上另有一条版式相近的候选」，"
                     "候选自身没有任何主证据：§二.1 点名的全文件循环自证")))
        if effective_layout is None:
            continue
        key = (record.page_number, record.line_index)
        if key not in region_cache:
            region_cache[key] = _independent_region_facts(
                effective_layout, record.page_number, record.line_index)
        facts = region_cache[key]
        if not facts.get("available"):
            continue
        # 强主证据**由本函数自己复算**：生产侧声称的"字号抬升 / 居中"必须能被独立
        # 复现出来，否则不算；`toc_body_landing` 则由上面那次对象级重算决定 ——
        # **不再**由 `toc_matches=` / `bookmark_matches=` 的计数决定（`hq-4`）。
        strong = _independent_strong_primary_facts(
            effective_layout, record.page_number, record.line_index,
            record.evidence, base_size, toc_landing_verified=toc_landing_verified)
        # 两层状态**由本函数自己重算**（`trg-3`），不读生产侧自报结论：
        # （a）缺 `table_scope` → 无法区分 inside / adjacent，fail-closed；
        # （b）自报 scope 与独立重算矛盾 → 同样 fail-closed。
        declared_scope = _parse_table_scope(record.evidence)
        if declared_scope is None:
            scope_unavailable.append(dict(
                entry_common,
                why=("候选审计记录里没有持久化 `table_scope`（`trg-3` 的两层状态）："
                     "复核门无法区分「表格成员」与「仅仅邻近」，按 fail-closed 处理")))
        elif declared_scope != facts.get("scope"):
            scope_mismatch.append(dict(
                entry_common, declared_scope=declared_scope,
                recomputed_scope=facts.get("scope"), geometry=facts,
                why=("生产侧自报的 `table_scope` 与复核门独立重算的两层状态矛盾："
                     "不得相信生产侧自报的 inside / adjacent 结论")))
        fragment = _independent_fragment_fact(
            effective_layout, record.page_number, record.line_index)
        in_region = facts.get("scope") == TABLE_SCOPE_INSIDE
        if in_region:
            inside_table_accepted_count += 1
            # §三.2（`hq-4`）：表内采纳节点**一律**进入阻断复核。安全穿透的**唯一**
            # 依据是复核侧自己重算出来的**对象级目录项 landing**（来源对象身份、来源页
            # 确是目录页、目标行文本一致、页标签唯一映射到本页）。版式强证据（无论几项）
            # 与普通 supporting 证据都不再放行；**书签也不再放行** —— 它的身份复核侧
            # 无权重建，只能作导航候选（`navigation_only`）。这与生产侧
            # `safe_table_override` 的资格**逐条同形**（§三要求两侧对称，且验收侧更严格）。
            if landing["available"]:
                safe_table_override_accepted.append(dict(
                    entry_common, geometry=facts, verified_strong=list(strong),
                    fragment_fact=bool(fragment), landing=landing,
                    why=("表格区域内（独立重算的成员资格），且**对象级目录项 landing 成立**："
                         "来源对象身份 / 目标行 / 页标签映射由复核侧在真实版式上自己重算"
                         "通过 —— 逐条留痕，不计入误收")))
            else:
                table_region_accepted.append(dict(
                    entry_common, geometry=facts, verified_strong=list(strong),
                    fragment_fact=bool(fragment), landing=landing,
                    why=("已采纳标题是表格**成员**（独立重算的几何事实），但**没有通过**"
                         "安全穿透资格：没有可独立重算的对象级**目录项** landing"
                         "（版式样式无论几项、书签命中都不再放行）：无法与表内行标签区分")))
                if landing["reported"] or landing.get("navigation_only") \
                        or TOC_BODY_LANDING_EVIDENCE in strong:
                    # 生产侧声称有来源佐证（证据码或载荷），复核侧复算不成立 ——
                    # 这正是"生产侧判 safe、验收侧复算不 safe"的分叉，必须**显式阻断**。
                    mismatch = dict(
                        entry_common, geometry=facts, landing=landing,
                        self_reported_evidence=(
                            TOC_BODY_LANDING_EVIDENCE in strong
                            or bool(landing.get("navigation_only"))
                            or landing["reported"]),
                        why=("生产侧把一个表内候选当作有安全穿透资格采纳了，"
                             "但复核侧无法在真实版式上重算出对象级**目录项** landing"
                             "（书签命中不构成穿透依据）："
                             "两侧结论分叉，阻断"))
                    safe_override_mismatch.append(mismatch)
                    source_landing_unverified.append(dict(
                        entry_common, landing=landing,
                        why=("已采纳的表内标题缺少**可独立复核**的目录项 landing 上下文"
                             "（载荷缺失 / 不可解析 / 来源对象身份或目标行重算不成立）："
                             "按 fail-closed 处理，不得返回 clean")))
        if fragment and not strong:
            fragmented_cell_accepted.append(dict(
                entry_common, geometry=facts, verified_strong=[],
                why=("已采纳标题与紧随其后的同列行构成一个被网格列宽截断的单元格"
                     "片段，且没有任何可独立复算的强主证据：不是完整独立标题行")))
    # §三.3：`adjacent_to_table` 的候选**不得**因为邻近被自动拒绝。本计数是这条规则
    # 的**阻断式**检查：邻近状态、未被采纳、没有任何硬拒绝原因与降级原因，而它的
    # 证据算术**只要补上**那条被抽掉的弱主证据 `standalone_line` 就成立 —— 也就是
    # "仅仅因为贴着表格而被拒绝"的确切形态。正常实现下它恒为 0。
    if effective_layout is not None:
        for record in result.candidates:
            if getattr(record, "kind", None) != "heading" or record.accepted:
                continue
            if record.reason_codes:
                continue
            key = (record.page_number, record.line_index)
            if key not in region_cache:
                region_cache[key] = _independent_region_facts(
                    effective_layout, record.page_number, record.line_index)
            facts = region_cache[key]
            if not facts.get("available") \
                    or facts.get("scope") != TABLE_SCOPE_ADJACENT:
                continue
            primary, supporting = _parse_evidence_codes(record.evidence)
            if "standalone_line" in primary or _parse_soft_reasons(record.evidence):
                continue
            granted = tuple(dict.fromkeys(tuple(primary) + ("standalone_line",)))
            if len(granted) >= 1 \
                    and len(granted) + len(supporting) >= MIN_HEADING_EVIDENCE:
                adjacent_heading_rejected.append({
                    "page_number": record.page_number,
                    "line_index": record.line_index, "title": record.text,
                    "node_id": record.node_id,
                    "primary_evidence": list(primary),
                    "supporting_evidence": list(supporting),
                    "geometry": facts,
                    "why": ("候选仅仅**邻近**表格，却被抽掉了弱主证据 "
                            "`standalone_line`：补回它证据算术即成立 —— 这正是 §三.3 "
                            "禁止的「只因为贴着表格就拒绝」"),
                })
    # §三（`hq-3`）：反方向的分叉也要**显式记录**。生产侧以"表内候选"降级、未采纳一条
    # 候选，而复核侧却能在真实版式上把它的对象级来源 landing 重算成立 —— 这意味着生产
    # 侧可能漏收了一条真实标题。两侧结论不一致时必须留下逐条证据，不得静默分叉。
    if effective_layout is not None:
        for record in result.candidates:
            if getattr(record, "kind", None) != "heading" or record.accepted:
                continue
            if AMBIGUOUS_TABLE_REGION_REASON \
                    not in _parse_soft_reasons(record.evidence):
                continue
            if _parse_source_landing(record.evidence) is None:
                continue
            landing = _independent_source_landing_facts(
                effective_layout, record, toc_pages=_toc_pages_for_audit(),
                toc_entries=_toc_entries_for_audit())
            if not landing["available"]:
                continue
            unsafe_override_mismatch.append({
                "page_number": record.page_number,
                "line_index": record.line_index, "title": record.text,
                "node_id": record.node_id,
                "landing": landing,
                "why": ("生产侧以「表内候选」降级且未采纳，复核侧却重算出成立的对象级"
                        "来源 landing：两侧结论分叉（生产侧可能漏收一条真实标题），"
                        "显式记录，不得静默分叉"),
            })

    if effective_layout is None and accepted_heading_records:
        verification_gaps.append({
            "kind": "region_verification_skipped",
            "why": (f"{len(accepted_heading_records)} 条已采纳标题的区域 / 片段复核"
                    "被跳过：缺 `PageLayout`。"),
            "action_required": "提供持久化版式后重跑结构复核门",
        })
    if scope_unavailable:
        verification_gaps.append({
            "kind": "table_scope_unavailable",
            "why": (f"{len(scope_unavailable)} 条已采纳标题的审计记录没有持久化 "
                    "`table_scope`（`trg-3` 两层状态）：复核门无法区分表格成员与邻近，"
                    "按 fail-closed 处理。"),
            "action_required": "以 `trg-3` 重新构建 outline，使候选审计携带 `table_scope`",
        })
    if scope_mismatch:
        verification_gaps.append({
            "kind": "table_scope_mismatch",
            "why": (f"{len(scope_mismatch)} 条已采纳标题自报的 `table_scope` 与复核门"
                    "独立重算的两层状态矛盾（见逐条明细）。"),
            "action_required": "核对生产侧区域判据；矛盾未消除前不得返回 clean",
        })
    if source_landing_unverified:
        verification_gaps.append({
            "kind": "source_landing_unverified",
            "why": (f"{len(source_landing_unverified)} 条已采纳的表内标题缺少**可独立"
                    "复核**的来源 landing 上下文（载荷缺失 / 不可解析，或来源对象身份、"
                    "目录页身份、目标行、页标签映射在真实版式上重算不成立）。"),
            "action_required": ("以 `hq-4` 重新构建 outline，使候选审计携带 "
                                "`source_landing` 载荷；重算通过前不得返回 clean"),
        })
    # §二.2（`hq-4`）："按一条复核方无权重建的依据放行"。生产侧自报 `accepted_by=toc_landing`
    # （或证据码挂着 `toc_body_landing`），而复核侧在真实版式上重建不出对象级目录项
    # landing —— 这正是"生产侧凭书签 / 自报字符串放行"的确切形态。它**阻断**。
    if toc_landing_unverified:
        verification_gaps.append({
            "kind": "toc_landing_unverified",
            "why": (f"{len(toc_landing_unverified)} 条已采纳节点自报「凭已验证目录项 landing "
                    "采纳」，但复核侧在真实版式上重建不出任何对象级目录项 landing。"),
            "action_required": ("以 `hq-4` 重新构建 outline，使「凭来源 landing 采纳」的节点"
                                "都携带可重算的 `source_landing` 载荷；"
                                "重算通过前不得返回 clean"),
        })

    counts = {
        "same_declared_level_nesting": len(nesting),
        "prose_like_accepted": len(prose_like),
        "definition_like_accepted": len(definition_like),
        "toc_unmatched": len(missed[UNASSIGNED_TOC_UNMATCHED]),
        "bookmark_unmatched": len(missed[UNASSIGNED_BOOKMARK_UNMATCHED]),
        "numbered_list_ambiguity": len(missed[UNASSIGNED_NUMBERED_LIST_AMBIGUITY]),
        "accepted_without_node": len(orphan_heading_candidates),
        "global_peer_only_accepted": len(peer_only),
        # §三.4：`inside_table` 且被采纳、但**没有通过**安全穿透资格的节点。这是阻断
        # 计数（旧的 `table_region_strong_primary_accepted` 曾被列为非阻断 audit 项，
        # 而真实产物已证明其中存在普通承诺正文）。名字直接写明"不安全穿透"，不再用
        # 中性词"表格区域"掩盖它是一条误收。
        "unsafe_table_override_accepted": len(table_region_accepted),
        "fragmented_cell_accepted": len(fragmented_cell_accepted),
        # §三.3：仅仅邻近表格就被拒绝的候选。必须为 0。
        "adjacent_heading_rejected": len(adjacent_heading_rejected),
        "accepted_without_intrinsic_primary_evidence": len(without_intrinsic),
        "verification_gap": len(verification_gaps),
        # §三：生产侧与复核侧的结论分叉。两个方向都是阻断计数：前者是"生产侧判 safe、
        # 复核侧复算不 safe"，后者是"生产侧判 unsafe、复核侧复算 safe"（显式记录）。
        "safe_override_mismatch": len(safe_override_mismatch),
        "unsafe_override_mismatch": len(unsafe_override_mismatch),
        # §三：已采纳表内标题缺少可独立复核的来源 landing 上下文（fail-closed 缺口）。
        "source_landing_unverified": len(source_landing_unverified),
        # §二.2（`hq-4`）：已采纳节点自报"凭来源 landing 采纳"、复核侧重建不出来。
        # 它是**阻断**计数：不得把"按书签 / 自报字符串放行"降级成留痕。
        "toc_landing_unverified_accepted": len(toc_landing_unverified),
    }
    #: 阻断复核计数（**不计入** `clean` 的值由上面的 `counts` 承担）：表内采纳节点总数。
    #: 门要求 `inside_table_accepted == len(safe_table_override_accepted)` —— 每一个表内
    #: 采纳节点都必须在安全穿透清单里**逐条**出现，否则即为不安全穿透。
    review_counts = {
        "inside_table_accepted": inside_table_accepted_count,
        "unsafe_table_override_accepted": len(table_region_accepted),
        "safe_table_override_accepted": len(safe_table_override_accepted),
        "adjacent_heading_rejected": len(adjacent_heading_rejected),
        "fragmented_cell_accepted": len(fragmented_cell_accepted),
        "safe_override_mismatch": len(safe_override_mismatch),
        "unsafe_override_mismatch": len(unsafe_override_mismatch),
        "source_landing_unverified": len(source_landing_unverified),
        "toc_landing_unverified_accepted": len(toc_landing_unverified),
    }
    #: 留痕项（**不计入** `clean`）：`inside_table` 且通过安全穿透资格的已采纳节点。
    #: 它们必须**逐条**输出证据（`verified_strong` / 几何事实 / 为什么不是表格单元格），
    #: 不能只给数量 —— 这正是旧 `table_region_strong_primary_accepted` 的教训。
    audit_counts = {"safe_table_override_accepted": len(safe_table_override_accepted)}
    return {
        "scope": ("真实产物结构复核门：同级误嵌套 / 正文误收 / 待人工复核候选显化，"
                  "以及 §二.4 的独立复核项（全文件候选互证误收 / 表格成员误收 / "
                  "截断单元格误收 / 无自身主证据 / 邻近表格的标题被误拒）。"
                  "表格边界按 `trg-3` 分两层复核：`inside_table_accepted` 是**阻断**"
                  "计数，每一条都必须逐条出现在 `safe_table_override_accepted` 里；"
                  "`unsafe_table_override_accepted` / `adjacent_heading_rejected` / "
                  "`fragmented_cell_accepted` 只要非零，本门即不得宣称通过。"
                  "`numbered_list_ambiguity` **不是**「真标题漏收」：这些候选没有独立"
                  "人工标注金标，统一按**待人工复核候选**记录。"
                  "目录来源 / 正文 landing 对账（`toc_body_reconciliation`，状态机版本 "
                  "`toc_body_reconciliation_version`）里 `toc_body_unassigned` "
                  "**同样是阻断计数**：它表示目录项已在真实版式上闭合到一条合格正文行、"
                  "而该行不在树里，即可能存在正文标题漏收；没有版本字段或版本不是当前值的"
                  "历史载荷一律 legacy，不得静默按当前语义解释。"
                  "clean 只表示未发现这些"
                  "形态复核项且独立复核数据齐备，不表示标题树通过"),
        "acceptor_version": V.HEADING_QUALIFICATION_PROFILE_VERSION,
        "table_region_qualification_version": V.TABLE_REGION_QUALIFICATION_VERSION,
        "layout_available": effective_layout is not None,
        "counts": counts,
        "review_counts": review_counts,
        "audit_counts": audit_counts,
        "inside_table_accepted": inside_table_accepted_count,
        "unsafe_table_override_accepted": table_region_accepted,
        "safe_table_override_accepted": safe_table_override_accepted,
        "safe_override_mismatch": safe_override_mismatch,
        "unsafe_override_mismatch": unsafe_override_mismatch,
        "source_landing_unverified": source_landing_unverified,
        "toc_landing_unverified_accepted": toc_landing_unverified,
        "bookmark_navigation_only": bookmark_navigation_only,
        "adjacent_heading_rejected": adjacent_heading_rejected,
        "fragmented_cell_accepted": fragmented_cell_accepted,
        "clean": (all(value == 0 for value in counts.values())
                  and not verification_gaps
                  and inside_table_accepted_count
                  == len(safe_table_override_accepted)),
        "same_declared_level_nesting": nesting,
        "prose_like_accepted": prose_like,
        "definition_like_accepted": definition_like,
        "toc_unmatched": missed[UNASSIGNED_TOC_UNMATCHED],
        "bookmark_unmatched": missed[UNASSIGNED_BOOKMARK_UNMATCHED],
        "numbered_list_ambiguity": missed[UNASSIGNED_NUMBERED_LIST_AMBIGUITY],
        "accepted_without_node": orphan_heading_candidates,
        "global_peer_only_accepted": peer_only,
        "table_region_accepted": table_region_accepted,
        "accepted_without_intrinsic_primary_evidence": without_intrinsic,
        "table_scope_unavailable": scope_unavailable,
        "table_scope_mismatch": scope_mismatch,
        "verification_gaps": verification_gaps,
        # §三.5（`hq-4`）：**目录来源**与**正文落地**的对账。它把过去被写成一句
        # "真标题漏收"的两类**不同对象**分开：目录来源行（导航来源）与正文标题候选。
        #
        # 它的**状态机版本**是桶 dict 的**同级**键（不是桶内键）：桶 dict 的每一个键
        # 都是一个 bucket 列表，读法恒为 `len(items)` / `for bucket, items in ...`；
        # 把版本塞进桶 dict 会让那些读法把版本字符串当成一个桶（`len("tocr-2") == 6`）。
        # 版本与桶分开落盘，读产物的人既能逐桶计数，也能一眼看出这批桶是按哪版语义
        # 产生的 —— 没有这个字段（或值不是当前版本）的历史载荷一律 legacy，不得静默
        # 按 `tocr-2` 解释。
        # 版本与阻断桶清单**成对落盘**，且只有一个来源（见
        # `toc_body_reconciliation_declaration`）。缺任一键、或值不等于当前常量 ⇒
        # legacy/incomplete，run 级生成与验收必须 fail-closed。
        **toc_body_reconciliation_declaration(),
        "toc_body_reconciliation": toc_body_reconciliation(result, effective_layout),
    }


def formal_unassigned_reason_counts(result: OutlineBuildResult) -> dict:
    """正式未归属 span 的**细分原因**计数（§五(4)：不得全部压成同一个原因）。

    原因取自 `OutlineSpan.unassigned_reason` —— 即正式 `DocumentOutline.unassigned`
    里真实持久化的字段，而不是旁路统计。
    """
    counts: dict = {reason: 0 for reason in UNASSIGNED_FORMAL_REASONS}
    for span in result.outline.unassigned:
        reason = span.unassigned_reason
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def unassigned_markdown(result: OutlineBuildResult) -> str:
    lines = ["# unassigned / 待审候选", "",
             "本文件只列**未被采纳**的候选与显式未归属行；不得据此提高召回或删除正文。",
             ""]
    for item in result.candidates:
        if item.accepted:
            continue
        lines.append(f"- [{item.kind}] p{item.page_number} l{item.line_index} "
                     f"{item.text[:60]!r} reason={list(item.reason_codes)}")
    ambiguous = [a for a in result.line_assignments
                 if a.assignment == "unassigned_ambiguous"]
    lines.append("")
    if ambiguous:
        lines.append(f"## 显式未归属行（{len(ambiguous)}）")
        for item in ambiguous:
            lines.append(f"- p{item.page_number} l{item.line_index} {item.reason}")
    else:
        lines.append("## 显式未归属行（0）")
        lines.append("")
        lines.append("零条显式未归属行：所有非家具行都归入了 "
                     "accepted_heading / ordinary_content / toc_candidate / "
                     "bookmark_candidate 之一（TS3 不删除低置信正文，也不把低置信"
                     "正文强升为节点）。")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------

def self_check() -> dict:
    out: dict = {"module": "document_structure.outline_builder",
                 "outline_builder_version": OUTLINE_BUILDER_VERSION, "checks": []}

    def ok(cond: bool, msg: str) -> None:
        out["checks"].append(("PASS " if cond else "FAIL ") + msg)

    ok(OUTLINE_BUILDER_VERSION == V.OUTLINE_ALGORITHM_VERSION,
       "构建器版本与公共 OUTLINE_ALGORITHM_VERSION 同源（不另立版本常量）")
    ok(set(("toc_page", "pdf_bookmarks", "body_numbering", "layout_style",
            "repeated_noise_exclusion")) <= set(CANDIDATE_SOURCES),
       "candidate_sources 全部落在公共类型的封闭词表内")
    ok("parent_child" in EDGE_KINDS and "toc_to_body" in EDGE_KINDS
       and "table_continuation" in EDGE_KINDS,
       "生成的边类型都属于公共 EDGE_KINDS（table_continuation 属 TS5，不生成）")
    ok(leading_heading_level("2025年") is None,
       "年份行不构成编号层级（复用 harness.heading_structure）")
    ok("year_like_line" in false_positive_reasons("2025年", numbered=False),
       "年份整行命中 year_like_line 守卫")
    ok("amount_or_ratio_line" in false_positive_reasons("1,234.56", numbered=False),
       "金额/比例整行命中守卫")
    ok("page_label_like_line" in false_positive_reasons("- 3 -", numbered=False),
       "页码形态行命中守卫")
    ok("numeric_row" in false_positive_reasons("1,234.56 12.3% 5,678.90",
                                              numbered=False),
       "表格数据行命中 numeric_row 守卫")
    ok("sentence_final" in false_positive_reasons(
        "公司实现营业收入100万元，同比增长5%。", numbered=False),
       "普通短句（句末标点）命中守卫")
    ok("title_too_long" in false_positive_reasons("甲" * 61, numbered=True),
       "超过公共 title 上限的行不成标题")
    ok("long_unnumbered_line" in false_positive_reasons("甲" * 41, numbered=False),
       "无编号长行不成标题")
    ok(false_positive_reasons("第三节 管理层讨论与分析", numbered=True) == (),
       "真实编号标题不命中任何假阳性守卫")
    ok(false_positive_reasons("（一）主营业务分析", numbered=True) == (),
       "真实三级小标题不命中任何假阳性守卫")
    ok(false_positive_reasons("1、主要会计数据", numbered=True) == (),
       "真实四级小标题不命中任何假阳性守卫")
    ok(leading_heading_level("第三节 管理层讨论与分析") == 1
       and leading_heading_level("一、主要业务") == 2
       and leading_heading_level("（一）主营业务分析") == 3
       and leading_heading_level("1、主要会计数据") == 4
       and leading_heading_level("（1）营业收入构成") == 5,
       "编号层级序覆盖 1–5 级（含页中小标题），且复用 harness 实现")
    entry = parse_toc_entry("第三节 管理层讨论与分析 ............ 12", 3, 5, 0)
    ok(entry is not None and entry.title_part == "第三节 管理层讨论与分析"
       and entry.declared_page_label == "12"
       and entry.title_part in entry.entry_text,
       "目录项切分只取其尾部真实页标签，标题部分是原文真实子串")
    # 目录项的判据是**结构**（标题与页标签之间有点线填充），不是"以数字结尾"：
    # 以数字结尾的正文行（`公司实现营业收入100万元`）不得被切成目录项，也不得
    # 被猜出标题 —— 宁可返回 None，也不产生一条没有真实佐证的目录候选。
    ok(parse_toc_entry("公司实现营业收入100万元", 3, 5, 0) is None
       and parse_toc_entry("第三节 管理层讨论与分析 12", 3, 5, 0) is None,
       "无点线填充的行不得被当成目录项（不猜标题、不靠关键词）")
    ok(numeric_row_ratio("1,234.56") > 0.8
       and numeric_row_ratio("管理层讨论与分析") < 0.2,
       "低信息量字符占比能区分数据行与文字行")
    ok(sorted(LINE_STRUCTURE_STATES) == ["body_under_node", "formal_unassigned",
                                         "heading_node", "non_content"],
       "逐行结构归属恰为四态封闭集合（§六；旧 accepted_heading/ordinary_content "
       "桶不得冒充正式四态）")
    ok(len(set(UNASSIGNED_FORMAL_REASONS)) == 5
       and "boundary_ambiguous" not in UNASSIGNED_FORMAL_REASONS,
       "正式未归属原因恰为五个可执行细分原因（§五；不得全部压成 "
       "boundary_ambiguous）")
    # §四(2)(4) 的形态判据：只看本行标点与子句长度，不看业务词。
    ok(prose_shaped_title("7、某先生，47岁，现任本公司董事")
       and definition_shaped_title("二、重要缺陷：是指……")
       and prose_shaped_title("二、重要缺陷：是指……"),
       "正文形态判据识别人员履历短子句链与定义句（§四）")
    ok(not prose_shaped_title("七、与上年度财务报告相比，合并报表范围发生变化的情况说明")
       and not prose_shaped_title("2、特别现金分红：为积极落实股东回报规划")
       and not prose_shaped_title("四、主营业务分析")
       and not prose_shaped_title("（一）资产结构分析"),
       "只含一个逗号或只含非定义冒号的真实标题不被判成正文形态（不误伤召回）")
    ok(AMBIGUOUS_PROSE_SHAPE_REASON != AMBIGUOUS_WRAPPED_PROSE_REASON
       and UNASSIGNED_REASON_BY_SOFT_CODE.get(AMBIGUOUS_PROSE_SHAPE_REASON)
       == UNASSIGNED_NUMBERED_LIST_AMBIGUITY,
       "编号正文形态降级原因有独立码并映射到正式 numbered_list_ambiguity 细分原因")
    # §五：正式 unassigned 的落盘顺序必须**只由版式位置决定**，与来源到达顺序无关；
    # 否则同一份文档会因为"书签怎么排列"得到两个 outline_id。
    class _Probe:
        def __init__(self, anchor, reason, span_id, fingerprint):
            self.start_anchor = anchor
            self.unassigned_reason = reason
            self.span_id = span_id
            self.content_fingerprint = fingerprint

    _probe = [_Probe((3, 7), "toc_unmatched", "os-b", "f2"),
              _Probe((1, 2), "bookmark_unmatched", "os-a", "f1")]
    ok(tuple(s.span_id for s in canonical_unassigned_order(_probe)) == ("os-a", "os-b")
       and canonical_unassigned_order(_probe)
       == canonical_unassigned_order(tuple(reversed(_probe))),
       "正式 unassigned 按版式位置规范排序，输入顺序不同但身份相同（§五）")

    # --- §二.1 / §二.2 / §三.2：标题资格 profile `hq-3` 的可执行断言 -----------
    # 证据词表本身：`level_layout` 已降格为辅证据，`standalone_line` 降为**弱**主证据。
    ok("level_layout" not in HEADING_PRIMARY_EVIDENCE
       and "level_layout" not in HEADING_STRONG_PRIMARY_EVIDENCE
       and "level_layout" in HEADING_SUPPORTING_EVIDENCE,
       "level_layout 只能作辅证据（§二.1：不得单独充当主证据，也不得与一条普通辅证据"
       "拼成标题资格）")
    ok("standalone_line" in HEADING_PRIMARY_EVIDENCE
       and "standalone_line" not in HEADING_STRONG_PRIMARY_EVIDENCE
       and set(HEADING_STRONG_PRIMARY_EVIDENCE) < set(HEADING_PRIMARY_EVIDENCE),
       "standalone_line 是**弱**主证据：属于表格成员或折行时不得成立")
    ok(MIN_HEADING_EVIDENCE == 2,
       "采纳门槛未放宽：仍要求 主证据 ≥1 且 主+辅 ≥ MIN_HEADING_EVIDENCE")
    ok(NEARBY_PAGE_WINDOW_PAGES == 1,
       "level_layout 只定义在同一 / 相邻页这一**辅助邻近窗口**（域外候选一律不出借）；"
       "该窗口不是已验证的结构域，因此旧名 LOCAL_DOMAIN_PAGE_SPAN 已删除")

    def _cand(**kwargs) -> HeadingCandidate:
        base = dict(page_number=1, line_index=0, line_text="一、示例", title="一、示例",
                    title_normalized="一、示例", numbered=True, declared_level=1,
                    font_size=12.0, bold=False, centered=False, style_path=False,
                    body_recurrence=0, toc_matches=(), bookmark_matches=(), rejected=())
        base.update(kwargs)
        return HeadingCandidate(**base)

    _self_evident = _cand(page_number=1, line_index=1,
                          primary_evidence=("size_above_body",),
                          supporting_evidence=("paragraph_boundary",))
    _borrower = _cand(page_number=1, line_index=2,
                      supporting_evidence=("paragraph_boundary",))
    _near = {c.line_index: c for c in _decide_numbered_candidates(
        (_self_evident, _borrower))}
    _far = {c.line_index: c for c in _decide_numbered_candidates(
        (_cand(page_number=9, line_index=1, primary_evidence=("size_above_body",),
               supporting_evidence=("paragraph_boundary",)), _borrower))}
    ok("level_layout" in _near[2].supporting_evidence
       and "level_layout" not in _near[2].primary_evidence,
       "邻近窗口内的**自证**候选仍可出借 level_layout 辅证据（§二.1 允许的用途）")
    ok("level_layout" not in _far[2].supporting_evidence,
       "窗口外（相隔多页）的同层同版式候选**不**出借 level_layout：全文件互证已取消")
    ok(not _near[2].accepted and not _far[2].accepted
       and _near[2].intrinsic_primary_evidence == (),
       "只拿到 level_layout + 一条普通辅证据的候选不得被采纳（仍缺候选自身主证据）")
    ok(_near[2].ambiguous_reason == AMBIGUOUS_INSUFFICIENT_REASON,
       "这类候选进正式 unassigned 的「证据不足」档，不静默消失")

    # --- §二.3：通用表格 / 网格区域资格 `trg-3` 的几何断言 ----------------------
    # 只用真实 `bbox` 与真实字号（桩对象带几何与 span 字号），五条成员资格判据 +
    # 一条仅邻近判据各命中一次。
    class _StubSpan:
        def __init__(self, text, size):
            self.text = text
            self.size = size
            self.is_bold = False

    class _StubLine:
        def __init__(self, index, bbox, text, size=11.0):
            self.line_index = index
            self.bbox = bbox
            self.text = text
            self.is_furniture = False
            self.spans = (_StubSpan(text, size),)

    class _StubPage:
        def __init__(self, number, lines):
            self.page_number = number
            self.lines = tuple(lines)

    class _StubLayout:
        """最小版式桩：只提供复核侧真正读到的两样（`pages` 与按坐标取行）。"""

        def __init__(self, pages):
            self.pages = tuple(pages)

        def line_at(self, page_number, line_index):
            for page in self.pages:
                if page.page_number == page_number:
                    return next((l for l in page.lines
                                 if l.line_index == line_index), None)
            return None

    _lines = []
    for _y in (400.0, 420.0, 440.0):
        for _x in (72.0, 300.0, 480.0):
            _lines.append(_StubLine(len(_lines), (_x, _y, _x + 90.0, _y + 13.0),
                                    "单元格"))
    _lines.append(_StubLine(9, (72.0, 380.0, 180.0, 393.0), "一、行标签"))
    _lines.append(_StubLine(10, (80.0, 455.0, 250.0, 468.2), "行标签上半"))
    _lines.append(_StubLine(11, (80.0, 470.2, 250.0, 483.4), "行标签下半"))
    _lines.append(_StubLine(12, (200.0, 414.0, 280.0, 421.0), "行带内部"))
    # 判据 E 的独立 fixture：两行同族多字段行之间夹着**三段纯行标签**
    # （标签列起点悬出在值列之前，带内没有任何值列内容）。
    _band_lines = [_StubLine(_i, (62.3, _y, 118.6, _y + 13.0), "行标签")
                   for _i, _y in ((3, 130.0), (4, 150.0), (5, 170.0))]
    _band_page = _StubPage(2, [
        _StubLine(0, (72.0, 100.0, 162.0, 113.0), "项目"),
        _StubLine(1, (300.0, 100.0, 390.0, 113.0), "1,234.56"),
        _StubLine(2, (480.0, 100.0, 570.0, 113.0), "1,234.56"),
        *_band_lines,
        _StubLine(6, (72.0, 200.0, 162.0, 213.0), "项目"),
        _StubLine(7, (300.0, 200.0, 390.0, 213.0), "1,234.56"),
        _StubLine(8, (480.0, 200.0, 570.0, 213.0), "1,234.56"),
    ])
    # 判据 B / 邻近的**对照 fixture**：第 3 页与第 2 页几何**完全相同**，只有目标行的
    # 字号不同（11.0 = 单元格字号 vs 14.0 = 与单元格不同族）。第 3 页的目标行因此是
    # 表格**成员**，第 4 页的同形行只是**邻近窗口内的一条独立行**（连邻近都算不上：
    # 它的间距 27pt > `TABLE_REGION_PROXIMITY_PT`）。这一对断言就是 `trg-3` 判据 B 里
    # "字号必须等于该行某格字号"那一条的可执行形式。
    _page3 = _StubPage(3, [
        _StubLine(0, (72.0, 100.0, 162.0, 113.0), "项目"),
        _StubLine(1, (300.0, 100.0, 390.0, 113.0), "1,234.56"),
        _StubLine(2, (480.0, 100.0, 570.0, 113.0), "1,234.56"),
        _StubLine(3, (200.0, 114.0, 320.0, 127.0), "近旁独立行", size=14.0),
        _StubLine(4, (72.0, 140.0, 180.0, 153.0), "列锚点成员", size=11.0),
    ])
    _page4 = _StubPage(4, [
        _StubLine(0, (72.0, 100.0, 162.0, 113.0), "项目"),
        _StubLine(1, (300.0, 100.0, 390.0, 113.0), "1,234.56"),
        _StubLine(2, (480.0, 100.0, 570.0, 113.0), "1,234.56"),
        _StubLine(3, (72.0, 140.0, 180.0, 153.0), "字号不同族", size=14.0),
    ])
    # 判据 E 的**闭合条件**对照 fixture：第 5 页与第 6 页的两条同族多字段行都相距很远
    # （100 → 300/320），唯一差别是第 5 页在两条同族行之间**还夹着另一张表**的多字段行
    # （列锚点 72/150，整行都排在标签带的值列边界之前）。第 5 页的两行因此**不是**同一
    # 个表格对象的结构行闭合，中间那三条"行标签"不构成标签带；第 6 页才闭合。缺了这一
    # 条闭合条件，页面上相距几百点的两个表格对象会把中间的整页正文圈成标签带。
    def _band_page_with(*, interior: bool) -> "_StubPage":
        head = [_StubLine(0, (72.0, 100.0, 162.0, 113.0), "项目"),
                _StubLine(1, (300.0, 100.0, 390.0, 113.0), "1,234.56"),
                _StubLine(2, (480.0, 100.0, 570.0, 113.0), "1,234.56")]
        band = [_StubLine(3, (62.3, 130.0, 118.6, 143.0), "行标签"),
                _StubLine(4, (62.3, 150.0, 118.6, 163.0), "行标签"),
                _StubLine(5, (62.3, 170.0, 118.6, 183.0), "行标签")]
        tail_y = 320.0
        mid = ([_StubLine(6, (72.0, 200.0, 162.0, 213.0), "另一张表"),
                _StubLine(7, (150.0, 200.0, 240.0, 213.0), "1,234.56")] +
               [_StubLine(8, (62.3, 250.0, 118.6, 263.0), "行标签"),
                _StubLine(9, (62.3, 270.0, 118.6, 283.0), "行标签"),
                _StubLine(10, (62.3, 290.0, 118.6, 303.0), "行标签")]
               if interior else [])
        base = len(head) + len(band) + len(mid)
        tail = [_StubLine(base, (72.0, tail_y, 162.0, tail_y + 13.0), "项目"),
                _StubLine(base + 1, (300.0, tail_y, 390.0, tail_y + 13.0),
                          "1,234.56"),
                _StubLine(base + 2, (480.0, tail_y, 570.0, tail_y + 13.0),
                          "1,234.56")]
        return head + band + mid + tail

    _band_unclosed = _StubPage(5, _band_page_with(interior=True))
    _band_closed = _StubPage(6, _band_page_with(interior=False))
    _layout_stub = _StubLayout([_StubPage(1, _lines), _band_page, _page3, _page4,
                                _band_unclosed, _band_closed])
    _scopes = table_region_scopes(_layout_stub)

    def _scope(page, line):
        return _scopes.get((page, line), (TABLE_SCOPE_NONE, None))

    def _reason(page, line):
        return _scope(page, line)[1]

    ok(set(_reason(1, i) for i in range(9)) == {TABLE_REGION_OWN_ROW_REASON},
       "判据 A：多字段行里的每一格都是表格成员")
    ok(_reason(1, 9) == TABLE_REGION_ANCHOR_MEMBER_REASON,
       "判据 B：本行起点落在邻近多字段行的列锚点上，且字号等于该行某格字号")
    ok(_reason(1, 10) == TABLE_REGION_LABEL_COLUMN_REASON
       and _reason(1, 11) == TABLE_REGION_LABEL_COLUMN_REASON,
       "判据 C：右端止于第二列起点之前的同列折行片段属标签列")
    ok(_reason(1, 12) == TABLE_REGION_ROW_BAND_REASON,
       "判据 D：上下都被多字段行夹住（同一表格对象的结构行闭合）的行是表格成员")
    ok(set(_reason(2, i) for i in (3, 4, 5)) == {TABLE_REGION_LABEL_BAND_REASON},
       "判据 E：两行同族多字段行之间的纯标签带是表格成员（带内只有行标签）")
    ok(TABLE_REGION_LABEL_BAND_REASON not in
       {_reason(2, i) for i in (0, 1, 2, 6, 7, 8)},
       "判据 E 不把网格自身的值列行算成标签带（判据 A 优先）")
    ok(set(_reason(6, i) for i in (3, 4, 5)) == {TABLE_REGION_LABEL_BAND_REASON},
       "判据 E 的**闭合条件**：两条同族多字段行之间不再夹别的多字段行时，"
       "中间的纯行标签构成标签带")
    ok(all(_scope(5, i)[0] != TABLE_SCOPE_INSIDE for i in (3, 4, 5, 8, 9, 10))
       and TABLE_REGION_LABEL_BAND_REASON not in
       {_reason(5, i) for i in (3, 4, 5, 8, 9, 10)},
       "判据 E 的**闭合条件**：同一页上两条**相距很远**的同族多字段行之间还夹着"
       "另一张表的结构行时，它们不是同一个表格对象的闭合，中间的行标签**不**构成"
       "标签带（否则整页正文都会被圈成表格成员，真实小标题被误拒）")
    ok(_scope(3, 4) == (TABLE_SCOPE_INSIDE, TABLE_REGION_ANCHOR_MEMBER_REASON)
       and _scope(4, 3)[0] != TABLE_SCOPE_INSIDE,
       "判据 B 的**字号成员资格**是成员资格证明的一部分：几何完全相同、只有字号不同族"
       "（11.0 vs 14.0）的两行，一行是表格成员，另一行不是")
    ok(_scope(3, 3)[0] == TABLE_SCOPE_ADJACENT,
       "仅仅邻近（1pt）而不在列锚点上、字号亦不合的行只算 `adjacent_to_table`："
       "**距离本身不是成员资格证明**")
    ok(_scope(3, 4)[0] == TABLE_SCOPE_INSIDE
       and _scope(4, 3)[0] == TABLE_SCOPE_NONE
       and _scope(99, 0) == (TABLE_SCOPE_NONE, None),
       "两层状态封闭：`inside_table` / `adjacent_to_table` / `none` 恰为三种取值")
    ok(set(TABLE_REGION_INSIDE_REASONS) == {TABLE_REGION_OWN_ROW_REASON,
                                            TABLE_REGION_ANCHOR_MEMBER_REASON,
                                            TABLE_REGION_LABEL_COLUMN_REASON,
                                            TABLE_REGION_ROW_BAND_REASON,
                                            TABLE_REGION_LABEL_BAND_REASON}
       and TABLE_REGION_ADJACENT_REASON not in TABLE_REGION_INSIDE_REASONS
       and set(TABLE_REGION_REASONS) == set(TABLE_REGION_INSIDE_REASONS)
       | {TABLE_REGION_ADJACENT_REASON}
       and set(UNASSIGNED_REASON_BY_SOFT_CODE.values()) <= set(UNASSIGNED_FORMAL_REASONS),
       "五条成员资格判据码与一条仅邻近判据码封闭且互斥，"
       "且歧义码全部映射到正式未归属原因词表内")
    # §三.2：安全穿透资格的真值表（`hq-3`）。只读候选自身证据码，不受其它候选影响。
    _only_centered = _cand(primary_evidence=("centered",),
                           supporting_evidence=("column_indent",),
                           table_scope=TABLE_SCOPE_INSIDE,
                           table_region_reason=TABLE_REGION_ANCHOR_MEMBER_REASON)
    _centered_with_support = _cand(primary_evidence=("centered",),
                                   supporting_evidence=("column_indent",
                                                        "paragraph_boundary",
                                                        "level_layout"),
                                   table_scope=TABLE_SCOPE_INSIDE,
                                   table_region_reason=TABLE_REGION_ANCHOR_MEMBER_REASON)
    _size_with_level = _cand(primary_evidence=("size_above_body",),
                             supporting_evidence=("column_indent", "level_layout"),
                             table_scope=TABLE_SCOPE_INSIDE,
                             table_region_reason=TABLE_REGION_ANCHOR_MEMBER_REASON)
    _toc_backed = _cand(primary_evidence=(TOC_BODY_LANDING_EVIDENCE,),
                        table_scope=TABLE_SCOPE_INSIDE,
                        table_region_reason=TABLE_REGION_ANCHOR_MEMBER_REASON)
    _three_layout = _cand(primary_evidence=("centered", "size_above_body",
                                            "bold_majority"),
                          supporting_evidence=("column_indent",),
                          table_scope=TABLE_SCOPE_INSIDE,
                          table_region_reason=TABLE_REGION_ANCHOR_MEMBER_REASON)
    _two_layout = _cand(primary_evidence=("centered", "size_above_body"),
                        supporting_evidence=("column_indent",),
                        table_scope=TABLE_SCOPE_INSIDE,
                        table_region_reason=TABLE_REGION_ANCHOR_MEMBER_REASON)
    _many_support = _cand(primary_evidence=("centered",),
                          supporting_evidence=("column_indent", "paragraph_boundary",
                                               "heading_context", "level_layout"),
                          table_scope=TABLE_SCOPE_INSIDE,
                          table_region_reason=TABLE_REGION_ANCHOR_MEMBER_REASON)
    ok(safe_table_override(_only_centered) is False
       and safe_table_override(_centered_with_support) is False
       and safe_table_override(_size_with_level) is False,
       "§二（`hq-4`）：`centered` / `size_above_body` 单项**不得**穿透；"
       "`centered` + 普通 supporting evidence、`size_above_body` + `level_layout` 也不得")
    ok(safe_table_override(_two_layout) is False
       and safe_table_override(_three_layout) is False
       and safe_table_override(_many_support) is False,
       "§二：版式强证据**无论几项**（两项 / 三项）都不能穿透 —— 已被证明属于表格的"
       "候选仍可能是居中、放大或加粗的表头 / 强调单元格；普通 supporting 更不参与")
    ok(safe_table_override(_toc_backed) is False,
       "§二：`toc_body_landing` 证据码**本身**不是穿透依据；缺 layout（无法完成对象级"
       "验证）时一律 fail-closed")
    ok(safe_table_override(_toc_backed, layout=_layout_stub) is False,
       "§二：给了 layout 但该候选行不是任何目录项的真实 landing 时仍不放行")
    # §二.2（`hq-4`）：书签命中**只**作导航候选 —— 即便两条可复核条件都成立，
    # 它也**不**使穿透成立、**不**使复核侧 `available` 为真。
    _bookmark_only = _cand(primary_evidence=(TOC_BODY_LANDING_EVIDENCE,),
                           table_scope=TABLE_SCOPE_INSIDE,
                           table_region_reason=TABLE_REGION_ANCHOR_MEMBER_REASON)
    ok(not any(entry.get("kind") == "toc"
               for entry in verified_source_landings(_bookmark_only, _layout_stub)),
       "§二.2（`hq-4`）：候选行上没有真实目录项来源时，来源 landing 里不含任何 "
       "`kind=\"toc\"` 条目 —— 书签命中无法补上这一条")
    ok(BOOKMARK_NAVIGATION_ONLY_REASON == "bookmark_navigation_only",
       "§二.2：书签的确定性登记原因码是 `bookmark_navigation_only`")
    # §四 P1-B(3)（`hq-4`）：目录项 landing 的**目标行**不得是普通表格内容。
    ok(toc_landing_target_is_body_line(
        TABLE_SCOPE_INSIDE, TABLE_REGION_OWN_ROW_REASON) is False,
       "§四 P1-B(3)：目标行**自己**就是一条多字段表格行的一格（判据 A）时，目录项指向"
       "它也不得让该行取得标题资格、更不得穿透 `inside_table` —— 来源对象链只证明"
       "「这条目录项指向这一行」，**不**证明「这一行是正文标题」")
    ok(all(toc_landing_target_is_body_line(scope, reason) is True
           for scope, reason in (
               (TABLE_SCOPE_INSIDE, TABLE_REGION_ANCHOR_MEMBER_REASON),
               (TABLE_SCOPE_INSIDE, TABLE_REGION_LABEL_COLUMN_REASON),
               (TABLE_SCOPE_INSIDE, TABLE_REGION_ROW_BAND_REASON),
               (TABLE_SCOPE_INSIDE, TABLE_REGION_LABEL_BAND_REASON),
               (TABLE_SCOPE_ADJACENT, TABLE_REGION_ADJACENT_REASON),
               (TABLE_SCOPE_NONE, None)))
       and TOC_LANDING_TABLE_CELL_REASON in TABLE_REGION_INSIDE_REASONS,
       "§四 P1-B(3)：只排除判据 A。列锚点成员 / 标签列片段 / 结构行闭合 / 纯标签带 / "
       "仅邻近 / 与表格无关的行都**不**被排除 —— 真实正文标题紧贴表格、甚至正好排在列"
       "锚点上是常态，一律排除会把它们一起挡在树外（另一种漏收，§三.3）")

    class _BookmarkRecordStub:
        page_number = 1
        line_index = 0
        evidence = (f"{SOURCE_LANDING_FIELD}=" + json.dumps([{
            "kind": "bookmark", "bookmark_page": 1, "landing_page": 1,
            "landing_line": 0,
            "title_normalized": normalize_toc_title("单元格")}],
            ensure_ascii=False),)
    _bookmark_facts = _independent_source_landing_facts(
        _layout_stub, _BookmarkRecordStub, toc_pages=(1,))
    ok(_bookmark_facts["available"] is False
       and _bookmark_facts["bookmark_identity_recheckable"] is False
       and _bookmark_facts["navigation_only"],
       "§二.2（`hq-4`）：书签 landing 在复核侧**只**登记为 `navigation_only` —— "
       "`available` 恒为 False（身份无法由 `PageLayout` 重建）")
    # §三：复核侧**不采信**生产侧自报的载荷 —— 载荷声称有目录项 landing，但来源对象
    # 身份在真实版式上重建不出来时，复核侧必须判不成立并逐条给出原因。
    class _RecordStub:
        page_number = 1
        line_index = 1
        evidence = (f"{SOURCE_LANDING_FIELD}=" + json.dumps([{
            "kind": "toc", "source_page": 1, "source_line": 0, "char_start": 0,
            "char_end": 3, "declared_page_label": "1", "toc_source_id": "toc-0",
            "title_normalized": "一、示例", "landing_page": 1,
            "landing_line": 1}], ensure_ascii=False),)
    _audit_landing = _independent_source_landing_facts(
        _layout_stub, _RecordStub, toc_pages=(1,))
    ok(_audit_landing["reported"] is True
       and _audit_landing["available"] is False and _audit_landing["problems"],
       "§三：复核侧不复用生产侧自报的来源结论 —— 载荷声称的目录项无法在真实版式上"
       "重建 `TocSource` 身份时判不成立，并逐条记录原因（fail-closed）")
    ok(_independent_source_landing_facts(
        _layout_stub, _RecordStub, toc_pages=())["available"] is False,
       "§三：来源页不是复核侧自己重算出的目录页时一律不成立")

    # --- §二.4：独立验收方自己重算几何，并与生产侧分属两条代码路径 --------------
    _facts = _independent_region_facts(_layout_stub, 1, 1)
    ok(_facts.get("available") and _facts.get("own_row_multi")
       and _facts.get("baseline_row"),
       "独立验收方从持久化版式重算「本行即多字段行内的一格」")
    ok(_independent_region_facts(_layout_stub, 1, 9).get("row_multi_anchor"),
       "独立验收方重算「本行起点落在邻近网格的列锚点上且字号同格」")
    ok(_independent_region_facts(_layout_stub, 1, 12).get("band_bracketed"),
       "独立验收方重算「本行上下都被多字段行夹住」")
    ok(_independent_region_facts(_layout_stub, 2, 4).get("label_band") is True
       and _independent_region_facts(_layout_stub, 2, 3).get("label_band") is True
       and not _independent_region_facts(_layout_stub, 2, 0).get("label_band"),
       "独立验收方重算「处在同族多字段行之间的纯标签带里」（不复制生产侧结论）")
    ok(_independent_region_facts(_layout_stub, 1, 1).get("scope") == TABLE_SCOPE_INSIDE
       and _independent_region_facts(_layout_stub, 3, 3).get("scope")
       == TABLE_SCOPE_ADJACENT
       and _independent_region_facts(_layout_stub, 4, 3).get("scope")
       == TABLE_SCOPE_NONE,
       "独立验收方独立重算出与生产侧同形的两层状态（成员 / 仅邻近 / 都不是），"
       "供验收门逐条比对生产侧自报的 `table_scope`")
    ok(_independent_region_facts(_layout_stub, 3, 4).get("row_multi_anchor") is True
       and _independent_region_facts(_layout_stub, 4, 3).get("row_multi_anchor") is False,
       "独立验收方也把**字号成员资格**当成成员资格证明：几何完全相同、只有字号不同族"
       "的两行，一行成立、一行不成立")
    ok(_independent_fragment_fact(_layout_stub, 1, 10) is True
       and _independent_fragment_fact(_layout_stub, 1, 0) is False,
       "独立验收方重算「被列宽截断的单元格片段」，且不把网格首行误判为片段")
    ok(_independent_region_facts(_layout_stub, 99, 0) == {"available": False}
       and _independent_fragment_fact(_layout_stub, 99, 0) is False,
       "版式数据缺失时独立复核显式不可用 / 不成立（fail-closed，不返回干净结论）")
    ok(V.VERSION_CONSTANTS.get("HEADING_QUALIFICATION_PROFILE_VERSION")
       == V.HEADING_QUALIFICATION_PROFILE_VERSION
       and V.VERSION_CONSTANTS.get("TABLE_REGION_QUALIFICATION_VERSION")
       == V.TABLE_REGION_QUALIFICATION_VERSION
       and V.VERSION_CONSTANTS.get("TOC_BODY_RECONCILIATION_VERSION")
       == V.TOC_BODY_RECONCILIATION_VERSION
       and OUTLINE_BUILDER_VERSION == V.OUTLINE_ALGORITHM_VERSION,
       "标题资格 / 表格区域资格 / 对账状态机版本只读 document_structure.versions"
       "（本模块不写版本字面量）")
    # §tocr-2：桶清单必须与 `toc_body_reconciliation` 在**无版式**时返回的键集逐字符
    # 一致（`layout=None` 时不读 `result`，因此这里可以只验形状而不构造构建结果），
    # 否则"按清单显式取桶"的消费方会静默漏桶或空桶。
    _recon_keys = tuple(toc_body_reconciliation(None, None))
    ok(tuple(sorted(_recon_keys)) == tuple(sorted(TOC_BODY_RECONCILIATION_BUCKETS))
       and "toc_source_only" not in _recon_keys
       and TOC_BODY_RECONCILIATION_BLOCKING_BUCKETS == ("toc_body_unassigned",),
       "对账桶清单与实现一致：六桶、`toc_source_only` 已废除、"
       "`toc_body_unassigned` 是唯一阻断桶")
    # 阻断桶也必须**落盘**：验收方要能拿持久化载荷自身核对，而不是拿当前常量去解释
    # 一份可能来自旧版的载荷。落盘值只有一个来源（`toc_body_reconciliation_declaration`），
    # 因此这里验的是"它确实是那两个键、且值就是当前常量"，而不是另写一份期望值。
    _decl = toc_body_reconciliation_declaration()
    ok(set(_decl) == {"toc_body_reconciliation_version",
                      "toc_body_reconciliation_blocking_buckets"}
       and _decl["toc_body_reconciliation_version"]
       == V.TOC_BODY_RECONCILIATION_VERSION
       and tuple(_decl["toc_body_reconciliation_blocking_buckets"])
       == TOC_BODY_RECONCILIATION_BLOCKING_BUCKETS,
       "对账版本与阻断桶清单的**唯一落盘来源**是 "
       "`toc_body_reconciliation_declaration()`")
    out["ok"] = all(line.startswith("PASS ") for line in out["checks"])
    return out


def _main(argv=None) -> int:  # pragma: no cover - 仅供人工排查
    import json
    report = self_check()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())

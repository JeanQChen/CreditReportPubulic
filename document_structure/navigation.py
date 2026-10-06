"""M930-2 §5.5：Contract → 标题树**确定性候选导航**（company-independent）。

本模块回答一个问题：**给定冻结 Contract 的一个 aspect，标题树里哪些 node / subtree
值得去读？** 它只做候选排序与选择，不产生任何证据。

三条硬边界（计划 §5.5）：

1. 规则不得包含公司、页码、表号或答案关键词——本模块的全部输入只有 Contract 字段
   与版本化规则条目，`company_id` 不进入任何派生输入；
2. title / synopsis **只作候选导航**，永不作为 Evidence、fact 或 coverage 证明：
   本模块不产出 `ResearchMaterial`，不产出 `SupportedFact`，`produces_coverage()`
   恒为 False；
3. 低置信、结构不可用或显式跨引用时才进入既有 bounded fallback；fallback 的结果仍
   必须由上层重切成**精确 OutlineSpan**，本模块不给"整块 Evidence"留下任何入口。

导航给出的是**有界读集**（`NavigationDecision.read_node_ids`），不是"唯一充分子树"：

- 读集的**根**是落在近分带（`TIE_BAND_RULE_ID`）内、且已各自下潜到最小充分 node 的
  候选——同分或仅差一个最弱导航信号（简介命中）的候选必须**一起**进入有界考察，
  不得由文档先后静默决定唯一子树；
- 读集按"候选强度优先"排序后由 `NavigationLimits.max_subtree_nodes` 截断，**截断是
  预算事实**而不是相关性判断：被截掉的 node 如实进 `unread_node_ids`；
- 低置信（`low_confidence`）时候选**可见、可审计但不读**（`read_node_ids` 为空）：
  没有任何一个键命名到树上的东西时，读得越多越危险，因此这里 fail-closed。

版本化资产：`AspectNavigationProfile`（由 `document_structure.schema` 持有类型与身份
派生）。本模块只是它的**构建器与消费者**，不重新定义 schema。
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Iterable, Mapping, Sequence

from document_structure import versions as V
from document_structure.schema import (
    AspectNavigationEntry,
    AspectNavigationProfile,
    DocumentOutline,
    NavigationSynopsis,
    OutlineNode,
    SchemaValidationError,
)


class NavigationError(SchemaValidationError):
    """导航输入 / 规则失败（缺字段、越界、非确定性输入）。"""


# ---------------------------------------------------------------------------
# 0. 规则条目（公司无关；不得含公司、页码、表号或答案关键词）
# ---------------------------------------------------------------------------

#: 规则条目族标识。任何新增策略都必须以**新条目**出现并按 `PROFILE_RULE_VERSION`
#: 版本化，不得按 aspect / 公司写特例。
NAV_KEY_RULE_ID = "navkey-declared-fields"
NAV_FORM_RULE_ID = "navform-role-tier-kind"
#: 并列子形态规则：Contract 声明的字段名里出现并列连词时（例如"产品与方案"），
#: **整体**可能不是文档实际写出的标签，而它的**子形态**是。切分只按连词与枚举符，
#: 不做中文分词；且子形态只在整体标签**不在本树**时才参与匹配（见
#: `NavigationIndex.key_match_forms`）——文档确实写了复合标签时不许拆开成更泛的词。
NAV_SUBFORM_RULE_ID = "navsubform-declared-label-conjunction"
#: 读根规则：近分带根先上提到**最上层命中祖先**再展开有界读集（见 `_sufficient_root`）。
#: 路径轴会让命中父章节的子节点得分更高，只读叶子会把挂满正文的父章节整段丢掉。
READ_ROOT_RULE_ID = "readroot-topmost-matched-ancestor"
#: 完整标签段规则：一个声明键只有与标题的某个**完整标签段**相等时才算"命中该标题"。
#: 标签段由**原始**标题文本按枚举符 / 并列连词切出（并剥掉编号前缀），再加整体标题本身；
#: 因此"长句披露标题里夹带该词"只算**片段命中**——它仍进候选（可审计），但不能作读根。
#: 不做中文分词、不引入任何具体词：判据只有"整段相等"，与公司 / 页码 / 答案无关。
NAV_SEGMENT_RULE_ID = "navsegment-complete-label-segment"
#: 父节点回退规则：当某 aspect 在本树**没有一个候选既整段命中、又通过读根资格**时（没有
#: 独立标题，或只有同词片段命中，或命中的章节与主体不相邻），改用它在冻结 Contract 里的
#: **祖先声明**（topic 标题 / question 文本的枚举项）作键，在同一套打分 / 近分带 / 读根上提
#: 机制上重跑，读到的就是那个真实父节点的有界正文（父章节子树覆盖子项，回退不丢料）。
#: 祖先键**去掉** aspect 自己的键（祖先层不得复用本层键），且仍只来自冻结 Contract。
NAV_PARENT_RULE_ID = "navparent-contract-ancestor-label"
#: **并列连词兜底规则**（`anp-8` 起）：祖先声明被**并列连词**（`以及 / 与 / 及 / 和 / 或`）
#: 连成一条复合标签、而 `_ENUM_SPLIT`（枚举分隔符）在它上面**一个键都切不出来**时，按连词
#: 再切一层。判据与 `_LABEL_JOIN`（标题分段已在用的那条规则）同源，仍只读冻结 Contract 的
#: 声明文本，不做中文分词、不含公司名 / 页码 / 答案词。**只在枚举层零键时生效**：枚举层已经
#: 有键的 aspect 不受影响，因此这条兜底只会把"祖先层**没有**键"改成"祖先层有键"，不会平白
#: 改动已有路由。真实 Contract 里只有 `供应商当前集中度` / `集中度跨期变化` 两条落在这里，
#: 而这两条的祖先键原先为空，等于同时关掉了祖先层回退与 `anp-7` 主体补读两道门。
NAV_PARENT_JOIN_RULE_ID = "navparent-conjunction-fallback"
#: **兄弟项排除规则**（`anp-6` 起）：祖先层的每个候选键还须**逐段归属到本 aspect** —— 一个
#: 候选祖先键若恰好是**同一个 question 名下某个兄弟 aspect** 的声明字段 / 标签（由
#: `aspect_nav_keys` 同源同法派生），它就不是本 aspect 的父节点键，必须排除。理由：Contract 的
#: question 文本是它名下**多个 aspect 子项的并列枚举**（真实 Contract 里
#: `客户当前集中度、前五大合计占比、关联方、集中度跨期变化、披露范围` 就是三个 aspect 的字段
#: 拼在一起），`anp-5` 把 question 文本的每个枚举项整段交给该 question 的**每个** aspect，于是
#: `集中度变化` 凭兄弟字段 `关联方` 把 `十三、关联方及关联交易` 读成"客户集中度"的材料——
#: `anp-5` 的读根主体邻接判据只施加在**本层**读根上（祖先键按定义就是该 aspect 的祖先声明，
#: 故不重复判定），因此这条误召在 `anp-5` 下**照旧发生**（真实 r5 产物已逐条复现：该章 13 个
#: node 全进读集，产出与客户集中度无关的关联方材料）。主题标题段的归属
#: （`NAV_TOPIC_SEGMENT_RULE_ID`）已把"并列词归谁"下沉到 question 一级，本条再下沉一级到
#: **aspect 一级**。判据只有规范化后的**整键相等**：不做子串、不做中文分词、不含公司名 /
#: 页码 / 答案词；输入只有冻结 Contract 的 `required_fields` 与 `requirement_text`。
#: 排除只决定"祖先层**有哪些**键"，打分 / 近分带 / 读根上提 / 失败即退回本层一字未改。
NAV_SIBLING_ITEM_RULE_ID = "navparent-sibling-item-exclusion"
#: **topic 标题段的归属规则**（`anp-4` 起）：topic 标题是它名下**多个 question 的并列概括**
#: （真实 Contract 里 "主营业务、经营模式、产业链、收入成本毛利构成、客户与供应商集中度"
#: 就是四个 question 的标签拼在一起），因此标题的每个完整标签段必须**先归属到某一个
#: question**，再随该 question 的子项进入祖先层；**不得整段赋给该 topic 的每个 aspect**
#: （那会让"收入构成 / 毛利率"这类子项凭共享父节点读到与它无关的"经营模式"正文）。
#: 归属判据是**分层多信号**的纯字符串判据（见 `TOPIC_SEGMENT_ASSIGN_TIERS`），输入只有
#: 冻结 Contract 的 topic 标题 / question 文本 / aspect 声明字段——公司、页码、答案词、
#: 文档标题都不参与。多问争用或全无信号时**不强选**：该段被丢弃并留下可审计原因。
NAV_TOPIC_SEGMENT_RULE_ID = "navtopic-segment-question-ownership"
#: **读根贴标签规则**（`anp-5` 起）：一个候选要作读根，它的标题上必须有一个本层键的
#: **匹配形态**落在某个**完整标签段**上（判据逐字见 `_form_is_anchored`）：整段相等；
#: 或在**短标题**的某个标签段上作前 / 后缀；或（仅限 Contract **声明的整键**）**完整
#: 落在**某个标签段内部。这条把 `NAV_SEGMENT_RULE_ID` 的整段判据从祖先层推广到本层，
#: 并覆盖并列子形态：`产品与方案` 退回子形态 `产品` 时，`2、主要产品及其用途` 的标签段
#: `主要产品` 以 `产品` 为后缀 → 成立；而 `十一、衍生产品情况` / `1、金融衍生产品名称`
#: 的标签段前 / 后缀都不是 `产品` → 不成立。**放宽只对短标题生效**（标题本身就是字段名
#: 形态，如 `1、营业收入构成`）：长标题是**句子**，句子里切出来的片段（`（2）占公司营业
#: 收入或营业利润10%以上的行业、产品或地区、销售模式的情况` 切出的 `销售模式的情况`）
#: 只是同词夹带，不构成"这个标题是关于那个字段的"证据——`报告期`、`销售模式` 因此都
#: 不再把披露长标题读成自己那一栏的材料。
NAV_ROOT_ANCHOR_RULE_ID = "navroot-label-anchored"
#: **读根主体邻接规则**（`anp-5` 起）：读根除贴标签外，还必须与该 aspect 的**主体标签**
#: （`AspectNavigationEntry.subject_head`，即 `requirement_text` 括号前的头部）共享长度
#: ≥ `_TOPIC_SEGMENT_MIN_SHARED` 的最长公共子串。理由：`关联方` 确实是
#: `十三、关联方及关联交易` 的**完整短标签段**，贴标签判据单独放它进来，但那条 aspect 的
#: 主体是"客户当前集中度"——两者最长公共子串为 0，于是那一章不再能把"客户当前集中度"
#: 的材料读成关联方披露。主体标签推导不出时（空串）**不加**这一层：不得凭空造主体。
NAV_ROOT_SUBJECT_RULE_ID = "navroot-subject-adjacency"
#: **主体补读规则**（`anp-7` 起）：本层定位**有缺陷**时，按 Contract 祖先声明键做**有界补读**。
#:
#: 缺陷（判据只有一条，且与本层/祖先层的读根资格同族口径）：**读集里没有任何一节贴上
#: 祖先键**（`label_anchored_keys(index, parent_keys, ·) ` 对读集逐节点为空）。两种形态都会
#: 落入它：(a) 本层定位直接 fallback（零候选 / 低置信 / 无合格读根），读集为空；
#: (b) 本层定位 `selected` 了，但选中的是**别的栏目**的那一节——真实反例是两份年报的
#: `main_business`：本层键 `收入` 把它稳定送到 `四、主营业务分析 / 2、收入与成本`
#: （`anp-5` 的注释已把这条写成本层判据的预期形态），而「报告期内公司从事的主要业务 /
#: 1、主要业务」（441 字正文）**一个字都没进读集**。这不是"查无此节"，是"这一层键
#: 指不到它"。
#:
#: 候选判据（**比缺陷判据严格得多**，两者不是同一条，必须分别看）：一个未读节点要成为
#: **补读根**，必须同时满足
#:   ① **是某个祖先键的按序近似形态**（`is_ordered_variant`：首字符与末字符都与该键
#:      相同，按序出现的公共字符数 ≥ `max(_TOPIC_SEGMENT_MIN_SHARED, ⌈len(键)/2⌉)`，
#:      且键的内部字符至少对上 `_SUPPLEMENT_MIN_INTERIOR_CHARS` 个）。
#:      这条把"某几个常用字恰好同现"挡在外面：`主营业务` 与 `1、主要业务` 是首尾对齐、
#:      四字里按序对上三字；而 `25、合同成本` 对 `收入成本毛利构成` 连**首字**都对不上，
#:      `1、报告期内利润分配政策…` 对 `报告期与口径` 末字对不上——真实两份年报复核过：
#:      后者若不挡，`报告期` 一个键会一次拉进三十余节与主营业务无关的章节。
#:      **只对上首末两字不算近似形态**：四字词里首末同字是汉语复合词的常态，
#:      `主营业务` 与募集说明书的 `（二）主动债务管理` 首字都是 `主`、末字都是 `务`，
#:      中间四字一个都对不上——只看首末就会把一节**债务管理**披露读成主营业务的材料，
#:      正是"相邻但无关的披露冒充主营业务材料"。故键长 ≥ 3 时必须再对上一个**内部**字
#:      （两字键没有内部字，首末对齐就是它的全部判据，不加这一层）。
#:   ② **标题是字段名形态**（规范化后 ≤ `_LABEL_MAX_CHARS`，与读根贴标签同一条口径）：
#:      长标题是句子，句子里按序凑出几个字不构成"这节是关于那个键的"证据。
#:   ③ **自带实质正文**（该节点自有准入正文 ≥ `_SUPPLEMENT_MIN_BODY_CHARS`）：补读的是
#:      "一节正文"，不是空标题。这条同时把父章节本身挡在外面——`四、主营业务分析` 与
#:      `一、报告期内公司从事的主要业务` 都**没有**自有正文，正文在它们的子节上。
#:   ④ 不是显式跨引用来源。
#: 合格者按（是否整段贴上祖先键 → 按序公共字符数 → 自有正文长度 → 文档顺序）排序，取前
#: `_SUPPLEMENT_MAX_ROOTS` 个作补读根，**排在**本层读根之后展开，因此预算截断先满足本层。
#: 它**不**改打分、**不**改近分带宽度、**不**改本层与祖先层的任何选出结果——只在读集上
#: 做加法，且加进来的每一节都逐条记在候选审计里（原因里点名本条规则与它命中的那个键）。
#: 输入只有冻结 Contract 的祖先声明与树上的标题 / 自有正文量，不含公司名、页码、表号或
#: 答案词。
NAV_SUPPLEMENT_RULE_ID = "navsupp-nearest-ancestor-subject-body"
#: 补读根**自带正文**的最小字符数：低于此的节点补进来只是审计噪声，不构成"一节正文"。
_SUPPLEMENT_MIN_BODY_CHARS = 80
#: 补读根的数量上限：有界补读——不许把几十个近似标题一次全读进来。
_SUPPLEMENT_MAX_ROOTS = 3
#: **上提补读规则**（`anp-9` 起）：`NAV_SUPPLEMENT_RULE_ID` 的**父章节**分支。
#:
#: 与主体补读同门（同一个缺陷判据、同一个补读槽位、同样只在读集上做加法），但候选来自
#: **另一类节点**，判据也不同。`NAV_SUPPLEMENT_RULE_ID` 的候选判据 ③ 有意把父章节挡在外面：
#: 它要求候选**自带**准入正文 ≥ `_SUPPLEMENT_MIN_BODY_CHARS`。这在"正文挂在子节上"的章节上
#: 会整段漏读——真实反例是两份年报的 `四、主营业务分析`：它自有正文只有 8 个字符
#: （一个模板复选框），而 `（1）动力业务` / `（2）储能业务` / `（3）新兴领域` /
#: `（4）供应链及产能` 四千余字正文全在它的子树里；这些子节标题（`动力业务`…）**对不上**
#: 任何祖先键的按序近似形态（`主营业务` 对 `动力业务` 连首字都对不上），于是整棵子树
#: 一个字都进不了读集——同一份文档在 2024 年报里同样的章节也一样漏。
#:
#: 本条只补**读根上提链上的父节**，因此与"几十个近似标题"无缘：
#:   ① **落在读集某一节点的祖先链上**（`read_node_ids` 的任一节点的祖先）。这条把"同一片
#:      已经读到的子树往上再看到它所属的那一节"限定住：本层**零读集**（fallback 形态）时
#:      没有候选，横向章节一条也进不来。
#:   ② 标题**整段贴上**某个祖先键（`label_anchored_keys`，与读根贴标签同一条判据与同一条
#:      短标题口径）。`四、主营业务分析` 对祖先键 `主营业务` 是短标题前缀，故命中；
#:      长句标题（`一、报告期内公司从事的主要业务`）在短标题口径下不命中，仍被挡在外面。
#:   ③ **有界子树里有实质正文**（`subtree_body_chars` ≥ `_SUPPLEMENT_MIN_BODY_CHARS`，
#:      子树上界 `_SUPPLEMENT_SUBTREE_MAX_NODES`）：量的是这一节**含子节**的准入正文字符数，
#:      不是它自有的那一行标题。
#:   ④ 不是显式跨引用来源。
#:
#: 它是**独立的一条规则**，不是主体补读的放宽版：候选判据不同（不看按序近似形态、不看根
#: 自有正文）、排序不同（只看文档顺序）、记账不同（`lift_root_node_ids` / `lift_rule_status`
#: 与 `supplement_root_node_ids` / `supplement_rule_status` 分开），候选审计里的依据也写成
#: 本规则自己的 id。与主体补读共用的是 `_SUPPLEMENT_MAX_ROOTS` 的上限与读集预算：两条规则
#: 都只做加法，上提根**排在主体补读根之后**展开，预算先满足更靠前的。输入仍然只有冻结
#: Contract 的祖先声明与树上的标题 / 准入正文**计数**，不含公司名、页码、表号或答案词，
#: 也不做中文分词。
NAV_LIFT_RULE_ID = "navsupp-lift-ancestor-subject-section"
#: 上提补读量子树正文时的**节点上界**（只读计数用，与 `NavigationLimits.max_subtree_nodes`
#: 同一量级；它只是"这一节是不是空的"的下限判据，不参与读集预算）。
_SUPPLEMENT_SUBTREE_MAX_NODES = 24
#: 近似形态判据里**内部字符**至少要对上的个数（键长 ≥ 3 时生效）。只共享首末两字不是
#: 近似形态：`主营业务` ↔ `（二）主动债务管理` 就是这一类。
_SUPPLEMENT_MIN_INTERIOR_CHARS = 1
#: `NavigationDecision.supplement_rule_status` / `lift_rule_status` 的**同一套**封闭取值。
#: 两条规则各记各的结论，但词表只有一份（`no_defect` 只对主体补读有意义，上提补读不产它）。
#: 补读**没做成**的原因必须可区分，尤其 `body_measure_unavailable`：索引没带自有正文量时
#: **无法判定**"这一节有没有正文"，于是**不补读**（不猜），而不是悄悄按"没有正文"处理。
SUPPLEMENT_STATUSES = (
    #: 补读根找到了，且至少一个真的进了读集。
    "fired",
    #: 本层读集里已经有一节贴上祖先键 → 没有缺陷，不补读。
    "no_defect",
    #: 该 aspect 没有祖先声明键 → 这条规则无从下手（不是缺陷）。
    "no_ancestor_keys",
    #: 判了，但树上没有同时满足"短标题 + 有实质正文 + 是祖先键的按序近似形态"的节。
    "no_candidate",
    #: 合格补读根存在，但本层读集把 `max_subtree_nodes` 预算吃光了，补读根没能进读集。
    #: 这**不是**"没有候选"，也不是"补读成功"——两者必须能分清。
    "over_budget",
    #: 索引没给逐节点自有正文量，判不了 → **不补读**（不按"没有正文"处理）。
    "body_measure_unavailable",
    #: 只有默认值：不经 `navigate` 构造的读回（旧产物）带这个值。
    "not_applicable",
)

#: `content_role -> expected_forms`：主形态，键取自冻结 Contract 的 `CONTENT_ROLES`。
#: 值是可读的正文形态标签（不含任何具体公司、页码、表号或答案词）。
NAV_FORM_RULES_BY_ROLE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("paragraph", ("正文段落",)),
    ("table", ("表格",)),
    ("paragraph_and_table", ("正文段落", "表格")),
    ("risk_note", ("风险提示段落",)),
    ("search_scope_note", ("检索范围说明",)),
    ("audit_only", ("核验说明",)),
)

#: `kind -> expected_forms` 附加形态，键取自 Contract 的 `ASPECT_KINDS`。
NAV_FORM_RULES_BY_KIND: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("fact_set", ()),
    ("all_disclosed_items", ("清单",)),
    ("single_judgment", ()),
    ("event_set", ("事件描述",)),
    ("financial_metric", ("财务指标表",)),
    ("derived_summary", ("概括段落",)),
    ("synthesizer", ("综合段落",)),
    ("search_audit", ("检索审计",)),
)

#: `display_tier -> expected_forms` 附加形态，键取自 Contract 的 `DISPLAY_TIERS`。
NAV_FORM_RULES_BY_TIER: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("required_body", ()),
    ("optional_body", ()),
    ("diagnostic_only", ("诊断信息",)),
)

#: 三项规则表的**合并视图**（供外部只读清点，不参与打分）。
NAV_FORM_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    tuple(("role:" + k, v) for k, v in NAV_FORM_RULES_BY_ROLE)
    + tuple(("kind:" + k, v) for k, v in NAV_FORM_RULES_BY_KIND)
    + tuple(("tier:" + k, v) for k, v in NAV_FORM_RULES_BY_TIER)
)

#: 规则表未覆盖时的兜底形态（不猜测，只给最保守的正文形态）。
NAV_FORM_DEFAULT: tuple[str, ...] = ("正文段落",)

#: 打分权重（标题命中 > 路径命中 > 简介命中；简介是最弱的导航信号）。
SCORE_WEIGHT_TITLE = 3.0
SCORE_WEIGHT_PATH = 1.0
SCORE_WEIGHT_SYNOPSIS = 0.5

#: 近分带（tie band）规则：读集的根候选只要落在"最优得分 − 一个最弱导航信号单位"
#: 之内即可。一次简介命中（最弱信号，`SCORE_WEIGHT_SYNOPSIS`）就能拉开的分差**不构成
#: 优势**，因此同分或近分的候选必须一起进入有界考察，而不是由文档先后静默塌缩成
#: "唯一充分子树"。单位随打分分母（树内出现的声明键数）走，不是固定宽度。
TIE_BAND_RULE_ID = "tieband-one-synopsis-unit"

FALLBACK_REASONS: tuple[str, ...] = (
    "no_navigation_keys",        # Contract 没有给出可导航的声明字段（显式 gap）
    "structurally_unavailable",  # 标题树 / 简介结构不可用
    "explicit_cross_reference",  # 候选全部落在显式跨引用来源节点上
    "low_confidence",            # 有候选但都低于阈值（可见、可审计，但不读）
    # 有候选、也够分，但**没有任何一个**通过读根资格（贴标签 + 主体邻接，见
    # `NAV_ROOT_ANCHOR_RULE_ID` / `NAV_ROOT_SUBJECT_RULE_ID`）：不作读根。
    # 与 `low_confidence` 分开记，是为了让"这一栏在本树定位不到"与"分数不够"
    # 在只读产物里可区分——前者是定位缺口，后者是置信缺口。
    "no_anchored_read_root",
)

decision_statuses = ("selected", "fallback")
"""两种终态；没有第三态（"部分选中"必须显式降级为 fallback）。"""

DISCARD_REASONS: tuple[str, ...] = (
    # 候选进入排序但没进入有界读集的**封闭**原因集合（每个候选恰有一个原因或无）。
    "outside_tie_band",          # 得分低于最优的近分带
    "over_budget",               # 在近分带子树内，但被 node 预算截断
    "explicit_cross_reference",  # 显式跨引用来源节点，不作根候选
    # 该候选的标题里**没有本层键的完整标签段命中**（或只在片段意义上含该键，或只在
    # 路径/简介轴上命中，见 `NAV_SEGMENT_RULE_ID`）：候选留痕，但不作读根——这正是
    # "长句披露标题里夹带同词"不得冒充业务标题的定点。
    "no_complete_label_segment",
    # 本层键的匹配形态**没有**落在这个标题的某个完整标签段上（既非整段相等，也非短标题下
    # 的前 / 后缀、整键落在标签段内部，见 `NAV_ROOT_ANCHOR_RULE_ID`）：候选留痕，不作读根。
    "not_label_anchored",
    # 贴标签成立，但该候选与该 aspect 的**主体标签**没有 ≥2 字最长公共子串
    # （`NAV_ROOT_SUBJECT_RULE_ID`）：`关联方` 确实是 `十三、关联方及关联交易` 的完整
    # 短标签段，但那一章与"客户当前集中度"零邻接——两条分开记，是为了让
    # "命中但贴不上标签"与"贴上了但对不上栏目"在只读产物里可区分。
    "not_subject_adjacent",
)
"""候选被舍弃的原因（closed set）。`None` 表示该候选**在**有界读集内。"""


# ---------------------------------------------------------------------------
# 1. 规范化与键派生
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[\s　·、,，。．.；;：:（）()\[\]【】《》“”\"'’‘—\-–_/\\|]+")

#: Contract `requirement_text` 里的**枚举分隔符**：Contract 用它们列出可选字段名，
#: 因此这里只取枚举项，不对自由文本做分词（不做中文分词猜测）。
_ENUM_SPLIT = re.compile(r"[、，,；;／/|或]")
_BRACKETS = "（）()【】《》“”\"'"
#: 单个导航键的最长规范化长度：超过它的片段是句子而不是字段名，丢弃。
_LABEL_MAX_CHARS = 8
#: 声明标签内部的**并列连词**（"产品与方案" / "来源和检索范围" / "上市或募资事件"）。
_LABEL_JOIN = re.compile(r"以及|[与及和或]")
#: 子形态的最短规范化长度：单字子形态（"与"以外的单字）信息量太低，不作为导航键。
_LABEL_MIN_CHARS = 2


def normalize_navigation_text(text: str) -> str:
    """导航比较用规范化：折叠空白与常见标点，不做任何语义改写。"""
    if not isinstance(text, str):
        raise NavigationError(f"导航文本必须为字符串，得到 {type(text).__name__}")
    return _PUNCT.sub("", _WS.sub("", text))


def aspect_nav_keys(required_fields: Sequence[str],
                    requirement_text: str) -> tuple[str, ...]:
    """由 Contract 声明字段 + requirement_text 的**枚举项**派生导航键。

    只做两件事：(1) 取 `required_fields`（Contract 声明的字段词表）；(2) 取
    `requirement_text` 自身（当它短到就是一个字段名）与其中被枚举分隔符分开的短
    片段。含括号的片段一律丢弃——那是"（存续/注销/吊销等）"这类注释，不是字段名。
    **不做中文分词**：任何"看起来像答案"的推测都不得进入规则。

    这里产出的键是**整体标签**。并列标签（"产品与方案"）的子形态不在本函数里派生，
    而在匹配时按 `NAV_SUBFORM_RULE_ID` 处理（见 `NavigationIndex.key_match_forms`）：
    只有整体标签**不在本树**时才退回子形态，且子形态本身也必须真的出现在树上。
    """
    if not isinstance(required_fields, (tuple, list)):
        raise NavigationError("required_fields 必须为序列")
    keys: list[str] = []

    def _add(raw: Any, where: str) -> None:
        if not isinstance(raw, str) or raw == "":
            raise NavigationError(f"{where} 必须为非空字符串，得到 {raw!r}")
        #: 键是**字段名**，不是带编号的标题：`（1）产品甲` 这样的声明字段先剥编号前缀，
        #: 否则规范化会把 `（1）` 变成孤零零的 `1`，键就再也贴不上它自己的标题标签段。
        key = normalize_navigation_text(_ENUM_PREFIX.sub("", raw))
        if key == "":
            raise NavigationError(f"{where}={raw!r} 规范化后为空，不得作为导航键")
        if key not in keys:
            keys.append(key)

    for index, field in enumerate(required_fields):
        _add(field, f"required_fields[{index}]")

    if not isinstance(requirement_text, str):
        raise NavigationError("requirement_text 必须为字符串")
    whole = normalize_navigation_text(requirement_text)
    if 1 < len(whole) <= 6:
        _add(requirement_text, "requirement_text")
    for segment in _ENUM_SPLIT.split(requirement_text):
        segment = segment.strip()
        if segment == "" or any(ch in segment for ch in _BRACKETS):
            continue
        normalized = normalize_navigation_text(segment)
        if 1 < len(normalized) <= _LABEL_MAX_CHARS:
            _add(segment, "requirement_text 枚举项")
    return tuple(sorted(keys))


def aspect_subject_head(requirement_text: str) -> str:
    """aspect 的**主体标签**（`AspectNavigationEntry.subject_head`）。

    取 `requirement_text` 里**第一个左括号之前**的头部（括号里的内容是 Contract 用来
    逐项列举可选字段的注释，不是主体本身），规范化后返回。无括号时就是整段
    `requirement_text`。头部规范化为空（或没有可用头部）时返回 `""`——**如实**表示
    "这条 aspect 没有可用的主体标签"，下游据此只按贴标签判据、不得凭空造主体。

    它**不参与打分**：打分键是 `nav_keys`（`NAV_KEY_RULE_ID`）。本函数只服务读根资格的
    **主体邻接**判据（`NAV_ROOT_SUBJECT_RULE_ID`），因此"某 aspect 的一个声明字段恰是
    无关章节短标签"这件事不再足以把那一章读成它的材料。
    """
    if not isinstance(requirement_text, str):
        raise NavigationError("requirement_text 必须为字符串")
    head: list[str] = []
    for ch in requirement_text:
        if ch in "（(":
            break
        head.append(ch)
    return normalize_navigation_text("".join(head))


def subform_keys(key: str) -> tuple[str, ...]:
    """由声明标签派生**并列子形态**（`NAV_SUBFORM_RULE_ID`）。

    纯字符串函数：无树、无 Contract 以外输入、无公司 / 页码 / 答案词。只有恰好切出
    ≥2 个、每个长度在 [`_LABEL_MIN_CHARS`, `_LABEL_MAX_CHARS`] 内的子形态时才算并列
    标签；切不开（或片段过长 / 过短 / 含括号注释）一律返回空——**不猜**。
    """
    if not isinstance(key, str) or key == "":
        raise NavigationError(f"导航键必须为非空字符串，得到 {key!r}")
    parts: list[str] = []
    for segment in _LABEL_JOIN.split(key):
        segment = segment.strip()
        if segment == "" or segment == key:
            continue
        if any(ch in segment for ch in _BRACKETS):
            continue
        if not (_LABEL_MIN_CHARS <= len(segment) <= _LABEL_MAX_CHARS):
            continue
        if segment not in parts:
            parts.append(segment)
    return tuple(parts) if len(parts) >= 2 else ()


#: 标题**编号前缀**：括号编号（"（2）"）、阿拉伯数字加分隔符（"3、" / "2."）、中文数字加
#: 分隔符（"一、"）、以及数字后直接跟空白（"3 经营模式"）。只有**真正的编号形态**才剥，
#: 因此"一体化业务"这类以中文数字开头的实词不会被误剥。
_ENUM_PREFIX = re.compile(
    r"^(?:[（(]\s*[0-9０-９一二三四五六七八九十]+\s*[）)]"
    r"|[0-9０-９]+\s+"
    r"|[0-9０-９]+\s*[、.．,，:：]"
    r"|[一二三四五六七八九十]+\s*[、.．,，:：])\s*")


def title_label_segments(raw_title: str) -> tuple[str, ...]:
    """一个真实标题的**完整标签段**（`NAV_SEGMENT_RULE_ID`）。

    输入是**原始**标题文本（不是 `title_normalized`——规范化会吃掉分隔符，而分隔符正是
    "整段"的边界）。产出 = 整体标题 + 按枚举符 / 并列连词切出的各段，逐段剥编号前缀后
    规范化、去空、去重。切分只按 `_ENUM_SPLIT` / `_LABEL_JOIN` 的字符集合，**不做中文
    分词**；因此它只回答"这个标题里哪几段是完整的标签"，不回答"这段是什么意思"。
    "段落"判据是**整段相等**：长句里夹带某个词不构成该标题是"关于那个词"的证据。
    """
    if not isinstance(raw_title, str):
        raise NavigationError(f"标题必须为字符串，得到 {type(raw_title).__name__}")
    segments: list[str] = []
    for piece in (raw_title,) + tuple(_ENUM_SPLIT.split(raw_title)):
        for sub in (piece,) + tuple(_LABEL_JOIN.split(piece)):
            normalized = normalize_navigation_text(
                _ENUM_PREFIX.sub("", sub.strip()))
            if normalized != "" and normalized not in segments:
                segments.append(normalized)
    return tuple(segments)


def longest_common_substring_length(left: str, right: str) -> int:
    """两段文本的**最长公共子串**长度（`NAV_TOPIC_SEGMENT_RULE_ID` 的最弱判据）。

    纯字符串运算：不做中文分词、不带词典、不猜测语义。空串或非字符串立即返回 0。
    """
    if not isinstance(left, str) or not isinstance(right, str):
        return 0
    if left == "" or right == "":
        return 0
    best = 0
    previous = [0] * (len(right) + 1)
    for i in range(1, len(left) + 1):
        current = [0] * (len(right) + 1)
        for j in range(1, len(right) + 1):
            if left[i - 1] == right[j - 1]:
                current[j] = previous[j - 1] + 1
                if current[j] > best:
                    best = current[j]
        previous = current
    return best


#: topic 标题段 → question 归属的**判据强弱分层**（由强到弱；只取"存在的**最强**层"，
#: 不在弱层上再放宽）。四条判据全部是纯字符串判据，输入只有冻结 Contract 的声明文本。
TOPIC_SEGMENT_ASSIGN_TIERS: tuple[str, ...] = (
    #: ① 该段与 question 文本的某个**完整标签段**整段相等（"收入成本毛利构成"）。
    "exact_label_segment",
    #: ①b 该段与 question 文本的某个完整标签段**互为子串**（"主营业务" ⊆ "主营业务构成"，
    #:     或 "产业链" ⊆ "产业链位置"）。仍是"整段"关系，不是任意位置的夹带。
    "label_containment",
    #: ② 该段与 question 文本共享长度 ≥ `_TOPIC_SEGMENT_MIN_SHARED` 的最长公共子串
    #:     （"经营模式" ↔ "采购模式 / 生产模式 / 销售模式" 共享「模式」）。这是**唯一**
    #:     能连上"经营模式"与"模式类子项"的信号，因此保留；但它只是最弱一层，
    #:     强层一旦命中就不再下探。
    "shared_substring",
    #: ③ 该段与该 question 名下各 aspect 的 `required_fields` 共享同样长度的最长公共子串。
    #:     aspect 声明字段也是 Contract 的一部分，因此与 ② 同权重的更弱一层。
    "aspect_field_substring",
)

#: 归属结果的**封闭**原因集合（每个 topic 标题段恰有一个）。
TOPIC_SEGMENT_ASSIGN_REASONS: tuple[str, ...] = (
    #: 唯一一个 question 在最强层上一枝独秀 → 该段归属它。
    "topic-segment-assigned",
    #: 多个 question 在**同一最强层**上争用 → 不强选，整段丢弃（可审计）。
    "topic-segment-contended",
    #: 所有 question 在各层上都无信号 → 不强选，整段丢弃（可审计）。
    "topic-segment-no-signal",
)

#: ② / ③ 两层判据的最小公共子串长度。取 2 是因为单字公共子串（"与""的"）不构成
#: 任何归属证据；这是一个**长度门槛**，不是词表，因此仍与公司/文档无关。
_TOPIC_SEGMENT_MIN_SHARED = 2


def _question_ref(question: Any, index: int) -> str:
    """question 的审计标识：优先冻结 Contract 的 `question_id`。

    缺 `question_id` 时退回**位置**标识（`question#<序号>`）。位置标识只用于审计行，
    不参与任何归属判据——因此"没给 id"不会改变归属结果，只会让审计行少一点可读性。
    """
    value = getattr(question, "question_id", None)
    if isinstance(value, str) and value != "":
        return value
    return f"question#{index}"


def _question_field_tokens(question: Any) -> tuple[str, ...]:
    """该 question 名下各 aspect `required_fields` 的规范化词（缺声明即空，不猜）。"""
    out: list[str] = []
    aspects = getattr(question, "aspects", None)
    if not isinstance(aspects, (tuple, list)):
        return ()
    for aspect in aspects:
        fields = getattr(aspect, "required_fields", None)
        if not isinstance(fields, (tuple, list)):
            continue
        for field in fields:
            if not isinstance(field, str) or field == "":
                continue
            normalized = normalize_navigation_text(field)
            if normalized != "" and normalized not in out:
                out.append(normalized)
    return tuple(out)


def _segment_question_tier(segment: str, question_text: str,
                           field_tokens: Sequence[str]) -> str | None:
    """某个 topic 标题段对某个 question 的**最强**归属层（无信号返回 `None`）。"""
    segments = title_label_segments(question_text)
    if segment in segments:
        return "exact_label_segment"
    if any(other != segment and (segment in other or other in segment)
           for other in segments):
        return "label_containment"
    whole = normalize_navigation_text(question_text)
    if longest_common_substring_length(segment, whole) >= _TOPIC_SEGMENT_MIN_SHARED:
        return "shared_substring"
    if any(longest_common_substring_length(segment, token)
           >= _TOPIC_SEGMENT_MIN_SHARED for token in field_tokens):
        return "aspect_field_substring"
    return None


@dataclasses.dataclass(frozen=True)
class TopicSegmentAssignment:
    """一个 topic 标题**完整标签段**的归属审计行（`NAV_TOPIC_SEGMENT_RULE_ID`）。

    行是**只读审计**：`reason` 取自封闭集合，`tier` 取自封闭分层，`contenders` 在争用时
    逐条列出争用的 question；`question_id is None` 当且仅当该段**没有被采用**。
    """

    topic_id: str
    topic_title: str
    segment: str
    question_id: str | None
    tier: str | None
    reason: str
    contenders: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("topic_id", "topic_title", "segment"):
            value = getattr(self, name)
            if not isinstance(value, str) or value == "":
                raise NavigationError(
                    f"归属审计行的 {name} 必须为非空字符串，得到 {value!r}")
        if self.reason not in TOPIC_SEGMENT_ASSIGN_REASONS:
            raise NavigationError(f"未登记的归属原因 {self.reason!r}")
        if self.tier is not None and self.tier not in TOPIC_SEGMENT_ASSIGN_TIERS:
            raise NavigationError(f"未登记的归属层 {self.tier!r}")
        adopted = self.reason == "topic-segment-assigned"
        if adopted != (self.question_id is not None and self.tier is not None):
            raise NavigationError(
                "归属审计行自相矛盾：只有 topic-segment-assigned 才带 question_id 与 tier")
        if bool(self.contenders) != (self.reason == "topic-segment-contended"):
            raise NavigationError(
                "归属审计行自相矛盾：只有 topic-segment-contended 才带争用 question 列表")


def topic_segment_assignments(topic: Any) -> tuple[TopicSegmentAssignment, ...]:
    """把一个 topic 标题的**完整标签段**逐段归属到它名下的某个 question。

    只读冻结 Contract 的 `topic_id / title / questions[*].question_id / question` 与各
    aspect 的 `required_fields`。产出按「标题段在标题里的出现顺序」排列，每个标签尺寸
    （`_LABEL_MAX_CHARS` 以内）的段恰有一行；超过该长度的段**不是标签**（整体标题就是
    这种），因此不进入归属，也不会成为祖先层键——这与 `parent_nav_keys` 的长度界同源。
    """
    topic_id = getattr(topic, "topic_id", None)
    if not isinstance(topic_id, str) or topic_id == "":
        raise NavigationError("topic 必须给出非空 topic_id（归属审计需要主题身份）")
    title = getattr(topic, "title", None)
    if not isinstance(title, str) or title == "":
        raise NavigationError("topic.title 必须为非空字符串")
    questions = getattr(topic, "questions", None)
    if not isinstance(questions, (tuple, list)) or not questions:
        raise NavigationError("topic 必须给出非空 questions 序列")
    prepared: list[tuple[str, str, tuple[str, ...]]] = []
    for index, question in enumerate(questions):
        text = getattr(question, "question", None)
        if not isinstance(text, str) or text == "":
            raise NavigationError("question.question 必须为非空字符串")
        prepared.append((_question_ref(question, index), text,
                         _question_field_tokens(question)))
    rows: list[TopicSegmentAssignment] = []
    for segment in title_label_segments(title):
        if not (1 < len(segment) <= _LABEL_MAX_CHARS):
            continue
        tiers = [(ref, tier) for ref, text, tokens in prepared
                 if (tier := _segment_question_tier(segment, text, tokens)) is not None]
        if not tiers:
            rows.append(TopicSegmentAssignment(
                topic_id=topic_id, topic_title=title, segment=segment,
                question_id=None, tier=None, reason="topic-segment-no-signal"))
            continue
        best = min(TOPIC_SEGMENT_ASSIGN_TIERS.index(tier) for _ref, tier in tiers)
        winners = tuple(ref for ref, tier in tiers
                        if TOPIC_SEGMENT_ASSIGN_TIERS.index(tier) == best)
        if len(winners) > 1:
            rows.append(TopicSegmentAssignment(
                topic_id=topic_id, topic_title=title, segment=segment,
                question_id=None, tier=None, reason="topic-segment-contended",
                contenders=winners))
            continue
        rows.append(TopicSegmentAssignment(
            topic_id=topic_id, topic_title=title, segment=segment,
            question_id=winners[0], tier=TOPIC_SEGMENT_ASSIGN_TIERS[best],
            reason="topic-segment-assigned"))
    return tuple(rows)


def _iter_contract_topics(contract: Any) -> Iterable[Any]:
    """按冻结结构的父子归属逐 topic 遍历（形状不符即 fail-closed）。"""
    sections = getattr(contract, "sections", None)
    if not isinstance(sections, (tuple, list)) or not sections:
        raise NavigationError("Contract 必须给出非空 sections 才能派生祖先声明")
    for section in sections:
        topics = getattr(section, "topics", None)
        if not isinstance(topics, (tuple, list)):
            raise NavigationError("Contract section 必须给出 topics 序列")
        for topic in topics:
            yield topic


def contract_topic_segment_assignments(
        contract: Any) -> dict[str, tuple[TopicSegmentAssignment, ...]]:
    """整份冻结 Contract 的 topic 标题段归属**审计视图**（按 `topic_id` 索引）。

    它只读 Contract，不改任何导航输入；存在的意义是让"某个标题段为什么**没有**进入
    祖先层"可被复核方逐段核对（`topic-segment-contended` / `topic-segment-no-signal`）。
    """
    out: dict[str, tuple[TopicSegmentAssignment, ...]] = {}
    for topic in _iter_contract_topics(contract):
        topic_id = getattr(topic, "topic_id", None)
        if not isinstance(topic_id, str) or topic_id == "":
            raise NavigationError("Contract topic 必须给出非空 topic_id")
        if topic_id in out:
            raise NavigationError(f"Contract 含重复 topic_id：{topic_id!r}")
        out[topic_id] = topic_segment_assignments(topic)
    return out


def parent_nav_keys(own_keys: Sequence[str],
                    ancestor_texts: Sequence[str],
                    sibling_keys: Sequence[str] = ()) -> tuple[str, ...]:
    """由**祖先声明文本**派生父节点层导航键（`NAV_PARENT_RULE_ID`）。

    祖先声明文本 = 冻结 Contract 里这条 aspect 所属 **question 的文本**，加上
    `NAV_TOPIC_SEGMENT_RULE_ID` **归属到该 question** 的 topic 标题段（`anp-4` 起；
    `anp-3` 是把 topic 标题整段交给该 topic 的每个 aspect，那正是"共享父节点即有效
    材料"的入口）。派生方式与 `aspect_nav_keys` 同源同法（只取整体与枚举项、剥编号、
    不做中文分词），再**去掉两类词**：

    (a) aspect 自己的键：祖先层复用本层键会让"父节点"变成同义反复；
    (b) **同 question 内兄弟 aspect 声明的字段**（`sibling_keys`，
        `NAV_SIBLING_ITEM_RULE_ID`，`anp-6` 起）：question 文本是它名下各 aspect 字段的
        并列枚举，兄弟子项名不是本栏的父节点名。

    `NAV_PARENT_JOIN_RULE_ID`（`anp-8` 起）再加**兜底第三层**：上面的切法是按 `_ENUM_SPLIT`
    （枚举分隔符）做的，切不开**并列连词**。祖先声明恰好是一条被连词连起来的复合标签时
    （真实 Contract：`供应商当前集中度与集中度跨期变化`），上面一个键都切不出来，于是
    "祖先层没有键"这一状态被误当成"这条 aspect 没有祖先声明"——下游的祖先层回退
    （`anp-4`）与主体补读（`anp-7`）都以"有没有祖先键"为门，两者一并失效。兜底层只用
    `_LABEL_JOIN` 再切一次，且**只在枚举层零键、且整条声明里没有枚举分隔符时**生效
    （带 `、` 的声明是枚举清单写法，兜底层不碰，以免把从句尾巴切成键）。

    公司 / 页码 / 答案词都不参与——输入只有 Contract 声明的字段。
    """
    if not isinstance(ancestor_texts, (tuple, list)):
        raise NavigationError("祖先声明必须为序列")
    if not isinstance(sibling_keys, (tuple, list)):
        raise NavigationError("兄弟项排除集必须为序列")
    for index, key in enumerate(sibling_keys):
        if not isinstance(key, str) or key == "":
            raise NavigationError(
                f"兄弟项排除集[{index}] 必须为非空字符串，得到 {key!r}")
    own = set(own_keys)
    blocked = set(sibling_keys)
    keys: list[str] = []

    def _add(raw: str) -> None:
        normalized = normalize_navigation_text(raw)
        if not (1 < len(normalized) <= _LABEL_MAX_CHARS):
            return
        if normalized in own or normalized in blocked or normalized in keys:
            return
        keys.append(normalized)

    for index, text in enumerate(ancestor_texts):
        if not isinstance(text, str) or text == "":
            raise NavigationError(
                f"祖先声明[{index}] 必须为非空字符串，得到 {text!r}")
        _add(text)
        for segment in _ENUM_SPLIT.split(text):
            segment = segment.strip()
            if segment == "" or any(ch in segment for ch in _BRACKETS):
                continue
            _add(segment)

    if not keys:
        #: **并列连词兜底层**（`NAV_PARENT_JOIN_RULE_ID`）：枚举层一个键都切不出来时，
        #: 祖先声明是一条被**并列连词**连起来的复合标签（`_LABEL_JOIN`：`以及 / 与 / 及 /
        #: 和 / 或`）。枚举分隔符切不开连词，于是"祖先**没有**键"与"祖先是一个**并列复合
        #: 标签**"被混成同一件事——下游的祖先层回退与 `anp-7` 主体补读都以"有没有祖先键"
        #: 为门，两者一并失效。这一层只在枚举层**零键**时兜底地再切一次，切法、长度界、
        #: 排除集与枚举层同源同法（仍然只读冻结 Contract 的声明文本，不做中文分词）。
        #:
        #: 为什么**只在零键时**才做：枚举层已经切出键的 aspect，其祖先层路由本来就在工作，
        #: 再补连词层会平白改动它们的读根与读集（真实 Contract：18 个 `company_business*`
        #: 里只有 `供应商当前集中度` / `集中度跨期变化` 两条落到这里，其余 16 条的祖先键
        #: 一个字节不变）。
        #:
        #: 为什么再收一道"整条声明里**没有枚举分隔符**"：声明里带 `、`（或其它
        #: `_ENUM_SPLIT` 分隔符）是文档在用**枚举清单**写法，清单项由枚举层负责，末尾那种
        #: "……对利润和资产质量的影响"式**从句尾巴**不是并列标签。真实反例：`fin_asset_quality`
        #: 那三条 aspect 的祖先声明带 `、`，放开这道门就会凭空造出 `开发支出对利润` /
        #: `资产质量的影响` 这种从句片段键（它们既不是任何树节点的完整标签段，又会把
        #: `anp-7` 主体补读的门推开）。收这道门后，全 Contract 落到兜底层的只剩供应商两条。
        for text in ancestor_texts:
            if len(_ENUM_SPLIT.split(text)) > 1:
                continue
            for sub in (text,) + tuple(_LABEL_JOIN.split(text)):
                sub = sub.strip()
                if sub == "" or any(ch in sub for ch in _BRACKETS):
                    continue
                _add(sub)
    return tuple(sorted(keys))


#: 候选审计里"用了**哪一层**键"的两个固定说法（`NAV_PARENT_RULE_ID`）。它们是**只读**
#: 的层标识：本层（Contract 声明键）与祖先层（Contract 祖先声明派生的键）。运行侧与
#: 回归都靠它回答"这条候选是按哪一层的键被考察的"，因此不得散成自由字符串。
NAV_KEY_TIER_DECLARED = "声明导航键"
NAV_KEY_TIER_ANCESTOR = "祖先声明键"


def question_sibling_keys(question: Any) -> dict[str, tuple[str, ...]]:
    """一个 question 名下**每条 aspect → 它的兄弟 aspect 声明键并集**。

    兄弟键由 `aspect_nav_keys` 同源同法派生（`required_fields` + `requirement_text` 的
    枚举项，规范化、剥编号、去重、**不做中文分词**），只是取的是**同一个 question 里别的
    aspect** 的键：它回答"这一栏的哪些候选祖先键其实属于**兄弟栏目**"，供
    `parent_nav_keys` 的兄弟项排除（`NAV_SIBLING_ITEM_RULE_ID`）使用。产出按 aspect 在
    Contract 里的声明顺序排列；形状不符即 fail-closed（不得静默产出空排除集让这条规则
    悄悄失效）。
    """
    aspects = getattr(question, "aspects", None)
    if not isinstance(aspects, (tuple, list)) or not aspects:
        raise NavigationError("Contract question 必须给出非空 aspects 序列")
    declared: list[tuple[str, tuple[str, ...]]] = []
    for aspect in aspects:
        aspect_id = getattr(aspect, "aspect_id", None)
        if not isinstance(aspect_id, str) or aspect_id == "":
            raise NavigationError("Contract aspect 必须给出非空 aspect_id")
        if any(existing == aspect_id for existing, _keys in declared):
            #: 本函数产出的是**按 aspect_id 索引**的映射：重复 id 会在映射层面被静默
            #: 合并（后者的兄弟项覆盖前者），那正是"重复声明被吞掉"的入口。fail-closed。
            raise NavigationError(f"Contract 含重复 aspect_id：{aspect_id!r}")
        fields = getattr(aspect, "required_fields", None)
        if fields is None or not isinstance(fields, (tuple, list)):
            raise NavigationError("aspect 必须提供 required_fields 序列")
        requirement_text = getattr(aspect, "requirement_text", "")
        if not isinstance(requirement_text, str):
            raise NavigationError("aspect.requirement_text 必须为字符串")
        declared.append((aspect_id, aspect_nav_keys(fields, requirement_text)))
    out: dict[str, tuple[str, ...]] = {}
    for aspect_id, _own in declared:
        merged: list[str] = []
        for other_id, other_keys in declared:
            if other_id == aspect_id:
                continue
            for key in other_keys:
                if key not in merged:
                    merged.append(key)
        out[aspect_id] = tuple(merged)
    return out


def _contract_ancestor_pass(contract: Any
                            ) -> tuple[dict[str, tuple[str, ...]],
                                       dict[str, tuple[str, ...]]]:
    """一次遍历冻结 Contract，同时派生祖先声明文本与兄弟项排除集。

    只读 `contract.sections[*].topics[*].questions[*]` 的 `title` / `question` 与
    `aspects[*].aspect_id / required_fields / requirement_text`，按冻结结构的父子归属
    取值——不推断、不补全、不按公司过滤。两份产出**同源同步**：任一条 aspect 缺任一份即
    fail-closed，绝不出现"有祖先声明但没有排除集"的半成品（那会让
    `NAV_SIBLING_ITEM_RULE_ID` 静默失效）。
    """
    labels: dict[str, tuple[str, ...]] = {}
    siblings: dict[str, tuple[str, ...]] = {}
    for topic in _iter_contract_topics(contract):
        questions = getattr(topic, "questions", None)
        if not isinstance(questions, (tuple, list)) or not questions:
            raise NavigationError("Contract topic 必须给出非空 questions 序列")
        title = getattr(topic, "title", None)
        if not isinstance(title, str) or title == "":
            raise NavigationError("Contract topic.title 必须为非空字符串")
        adopted: dict[str, list[str]] = {}
        for row in topic_segment_assignments(topic):
            if row.reason == "topic-segment-assigned" and row.question_id is not None:
                adopted.setdefault(row.question_id, []).append(row.segment)
        for question_index, question in enumerate(questions):
            question_text = getattr(question, "question", None)
            if not isinstance(question_text, str) or question_text == "":
                raise NavigationError("Contract question.question 必须为非空字符串")
            texts = (question_text,
                     *adopted.get(_question_ref(question, question_index), ()))
            for aspect_id, sibling_keys in question_sibling_keys(question).items():
                if aspect_id in labels:
                    raise NavigationError(f"Contract 含重复 aspect_id：{aspect_id!r}")
                labels[aspect_id] = texts
                siblings[aspect_id] = sibling_keys
    return labels, siblings


def contract_ancestor_labels(contract: Any) -> dict[str, tuple[str, ...]]:
    """从冻结 Contract 对象派生**每条 aspect 的祖先声明文本**。

    值的形状（`anp-4` 起）：`(question 文本, *归属到该 question 的 topic 标题段)`。**topic
    标题本身不再整段入值**——它是该 topic 名下多个 question 的并列概括，整段交给每个
    aspect 会让子项凭共享父节点读到与它无关的正文（见 `NAV_TOPIC_SEGMENT_RULE_ID`）。

    这是 `contract_ancestor_inputs` 的**一半**（只读审计用）。要构造导航 profile 必须用
    `contract_ancestor_inputs` 同时取到兄弟项排除集，否则 `NAV_SIBLING_ITEM_RULE_ID`
    无从生效。
    """
    return _contract_ancestor_pass(contract)[0]


def contract_sibling_keys(contract: Any) -> dict[str, tuple[str, ...]]:
    """从冻结 Contract 对象派生**每条 aspect 的兄弟项排除集**（`NAV_SIBLING_ITEM_RULE_ID`）。

    这是"某个候选祖先键为什么**没有**成为父节点键"的只读审计视图：值 = 同一 question 内
    其余 aspect 的 `aspect_nav_keys` 并集（规范化、去重、按声明顺序）。
    """
    return _contract_ancestor_pass(contract)[1]


def contract_ancestor_inputs(contract: Any
                             ) -> tuple[dict[str, tuple[str, ...]],
                                        dict[str, tuple[str, ...]]]:
    """**一次**遍历派生两份同源的祖先层输入：`(祖先声明文本, 兄弟项排除集)`。

    真实运行必须用它并把两份**一起**交给 `build_navigation_profile`——分开取会有"取了
    一份、忘了另一份"的形态，那正是"规则写了但没生效"的入口。
    """
    return _contract_ancestor_pass(contract)


def tie_band_width(total_keys: int) -> float:
    """近分带宽度：**一个最弱导航信号单位**（一次简介命中）在打分尺度上的大小。"""
    if not isinstance(total_keys, int) or isinstance(total_keys, bool) \
            or total_keys <= 0:
        raise NavigationError(f"total_keys 必须为正整数，得到 {total_keys!r}")
    ceiling = SCORE_WEIGHT_TITLE + SCORE_WEIGHT_PATH + SCORE_WEIGHT_SYNOPSIS
    return SCORE_WEIGHT_SYNOPSIS / (total_keys * ceiling)


def _lookup(table: tuple[tuple[str, tuple[str, ...]], ...], key: str
            ) -> tuple[str, ...] | None:
    for name, forms in table:
        if name == key:
            return forms
    return None


def form_rule(content_role: str, display_tier: str, kind: str) -> tuple[str, ...]:
    """由冻结 Contract 词表推导期望形态：角色（主）+ 层级 + 类型，按固定顺序去重。

    三条子规则各由一张**封闭词表**覆盖；任一子键不在词表内即 fail-closed（不得靠
    兜底悄悄放行未登记的角色 / 层级 / 类型）。
    """
    role_forms = _lookup(NAV_FORM_RULES_BY_ROLE, content_role)
    if role_forms is None:
        raise NavigationError(
            f"未登记的 content_role {content_role!r}（不得导航到未登记角色）")
    tier_forms = _lookup(NAV_FORM_RULES_BY_TIER, display_tier)
    if tier_forms is None:
        raise NavigationError(
            f"未登记的 display_tier {display_tier!r}（不得导航到未登记层级）")
    kind_forms = _lookup(NAV_FORM_RULES_BY_KIND, kind)
    if kind_forms is None:
        raise NavigationError(f"未登记的 kind {kind!r}（不得导航到未登记类型）")
    ordered: list[str] = []
    for form in tuple(role_forms) + tuple(tier_forms) + tuple(kind_forms):
        if form not in ordered:
            ordered.append(form)
    return tuple(ordered) or NAV_FORM_DEFAULT


def _entry_for(aspect: Any, ancestor_labels: Mapping[str, Sequence[str]] | None,
               sibling_keys: Mapping[str, Sequence[str]] | None = None
               ) -> AspectNavigationEntry:
    """由一条 Contract aspect（或其 Requirement 快照）派生导航条目。

    只读 `aspect_id / question_id / topic_id / content_role / display_tier / kind /
    required_fields`，全部是冻结 Contract 的**声明字段**。`required_fields` 是
    Contract 自己声明的字段词表，因此"用哪些词导航"这件事与公司无关、与答案无关。

    祖先层键（`parent_keys`）来自 `ancestor_labels[aspect_id]`，即 Contract 里该 aspect
    所属 **question 文本** 加上 `NAV_TOPIC_SEGMENT_RULE_ID` **归属到该 question** 的
    topic 标题段；缺该 aspect 的祖先声明即 fail-closed（不得让"没给祖先声明"静默退化成
    "该 aspect 没有父节点"这一结论）。
    """
    for name in ("aspect_id", "question_id", "topic_id", "content_role",
                 "display_tier"):
        value = getattr(aspect, name, None)
        if not isinstance(value, str) or value == "":
            raise NavigationError(f"aspect 的 {name} 必须为非空字符串，得到 {value!r}")
    fields = getattr(aspect, "required_fields", None)
    if fields is None or not isinstance(fields, (tuple, list)):
        raise NavigationError("aspect 必须提供 required_fields 序列")
    requirement_text = getattr(aspect, "requirement_text", "")
    nav_keys = aspect_nav_keys(fields, requirement_text)
    subject_head = aspect_subject_head(requirement_text)
    kind = getattr(aspect, "kind", "")
    if not isinstance(kind, str) or kind == "":
        raise NavigationError(f"aspect 的 kind 必须为非空字符串，得到 {kind!r}")
    forms = form_rule(aspect.content_role, aspect.display_tier, kind)
    joined = tuple(k for k in nav_keys if subform_keys(k))
    parent_keys: tuple[str, ...] = ()
    if ancestor_labels is None:
        parent_line = (
            f"{NAV_PARENT_RULE_ID}:未提供 Contract 祖先声明（question 文本 + 归属到该 "
            "question 的 topic 标题段）→ 父节点回退**关闭**；这不是「该 aspect 没有"
            "父节点」的结论；"
            f"{NAV_PARENT_JOIN_RULE_ID}:随父节点回退一起关闭（没有祖先声明文本可切，"
            "既不切枚举层也不切连词层）")
        #: 与祖先声明**同源**：没有祖先声明就没有祖先层键，也就无从谈"排除哪些键"。
        #: 如实写成"随父节点回退一起关闭"，而不是留空或写成"没有兄弟项"。
        sibling_line = (
            f"{NAV_SIBLING_ITEM_RULE_ID}:未提供同 question 兄弟 aspect 的声明键集 → "
            "兄弟项排除**随父节点回退一起关闭**（合成机制用例的形态）；"
            "这不是「本 aspect 没有兄弟项」的结论")
    else:
        texts = ancestor_labels.get(aspect.aspect_id)
        if texts is None:
            raise NavigationError(
                f"祖先声明未覆盖 aspect {aspect.aspect_id!r}（fail-closed：不得让"
                "缺祖先声明与「没有父节点」不可区分）")
        sibling = ((sibling_keys or {}).get(aspect.aspect_id, ())
                   if sibling_keys is not None else ())
        parent_keys = parent_nav_keys(nav_keys, texts, sibling)
        parent_line = (
            f"{NAV_PARENT_RULE_ID}:Contract 祖先声明（question 文本 1 条 + 归属到该 "
            f"question 的 topic 标题段 {max(len(texts) - 1, 0)} 条）"
            f"派生 {len(parent_keys)} 个与 nav_keys 互斥的祖先层键，"
            "仅在无完整标签段命中时用于定位父节点；"
            f"{NAV_PARENT_JOIN_RULE_ID}:枚举分隔符切不出任何键、且整条声明不含枚举"
            "分隔符时按并列连词再切一层（只在零键时生效，不改动枚举层已有键的 aspect；"
            "带 `、` 的枚举清单声明不碰，避免把从句尾巴切成键）")
        if sibling_keys is None:
            sibling_line = (
                f"{NAV_SIBLING_ITEM_RULE_ID}:未提供同 question 兄弟 aspect 的声明键集 → "
                "兄弟项排除**关闭**（祖先层可能仍含兄弟栏目的字段名）；"
                "这不是「本 aspect 没有兄弟项」的结论")
        else:
            if aspect.aspect_id not in sibling_keys:
                raise NavigationError(
                    f"兄弟项排除集未覆盖 aspect {aspect.aspect_id!r}（fail-closed：不得让"
                    "缺排除集与「没有兄弟项」不可区分）")
            sibling_line = (
                f"{NAV_SIBLING_ITEM_RULE_ID}:同 question 兄弟 aspect 声明键 "
                f"{len(sibling)} 个（整键相等即排除）不得充当本栏父节点键；"
                "祖先层只在剩余键上命中")
    derivation = (
        f"{NAV_KEY_RULE_ID}:required_fields + requirement_text 枚举项"
        f"（Contract 声明，{len(nav_keys)} 键）",
        f"{NAV_SUBFORM_RULE_ID}:整键缺席时按 {_LABEL_JOIN.pattern} 取并列子形态"
        f"（{len(joined)} 个键带子形态）",
        f"{NAV_FORM_RULE_ID}:content_role={aspect.content_role}"
        f"/display_tier={aspect.display_tier}/kind={kind} -> {'|'.join(forms)}",
        parent_line,
        sibling_line,
        f"{NAV_TOPIC_SEGMENT_RULE_ID}:topic 标题按完整标签段逐段归属到"
        f"{'|'.join(TOPIC_SEGMENT_ASSIGN_TIERS)} 最强层唯一命中的 question；"
        "争用或无信号即丢弃（不整段赋给每个子项）",
        f"{TIE_BAND_RULE_ID}:读根候选需落在最优得分 − 一个简介命中单位内"
        "（分母按本树内出现的声明键数）",
        f"{READ_ROOT_RULE_ID}:近分带根上提到最上层命中祖先再展开读集",
        f"{NAV_SEGMENT_RULE_ID}:标题命中按**完整标签段**判定；片段命中只作候选审计",
        f"{NAV_ROOT_ANCHOR_RULE_ID}:读根要求本层键的匹配形态落在某个完整标签段上"
        f"（整段相等，或短标签段（≤{_LABEL_MAX_CHARS} 字）的前/后缀）",
        f"{NAV_ROOT_SUBJECT_RULE_ID}:读根还须与主体标签 {subject_head!r}"
        f"共享 ≥{_TOPIC_SEGMENT_MIN_SHARED} 字最长公共子串"
        + ("（推导不出主体标签 → 本道判据不加，不凭空造主体）"
           if subject_head == "" else ""),
    )
    return AspectNavigationEntry(
        aspect_id=aspect.aspect_id, question_id=aspect.question_id,
        topic_id=aspect.topic_id, content_role=aspect.content_role,
        display_tier=aspect.display_tier, nav_keys=nav_keys,
        parent_keys=parent_keys, subject_head=subject_head,
        expected_forms=forms, derivation=derivation)


def build_navigation_profile(aspects: Sequence[Any], *,
                             contract_version: str, contract_fingerprint: str,
                             ancestor_labels: Mapping[str, Sequence[str]] | None = None,
                             sibling_keys: Mapping[str, Sequence[str]] | None = None
                             ) -> AspectNavigationProfile:
    """由冻结 Contract 的 aspect 集合派生**版本化**导航资产。

    输入只有 Contract 侧字段；同一份 Contract 在两个公司下产出逐字节相同的 profile
    （`company_id` 根本不在这里出现）。

    `ancestor_labels` 与 `sibling_keys` 是**同源的两份**祖先层输入，由
    `contract_ancestor_inputs(contract)` 从**冻结 Contract** 一次派生，键集必须**覆盖**
    本次全部 aspect（缺一个即 fail-closed）；允许是超集——真实调用传进来的就是整份
    Contract 的标签，按 topic 切片只会多一层出错机会，而"多"在这里无害（未被本次 aspect
    用到的标签根本不参与派生）。两者**必须同时给出或同时省略**：只给其一即 fail-closed，
    否则 `NAV_SIBLING_ITEM_RULE_ID` 会在"看起来配好了"的输入上静默失效。省略只用于不持有
    Contract 的合成机制用例（此时父节点回退与兄弟项排除一起关闭，`derivation` 里逐条写明）。
    """
    if not isinstance(aspects, (tuple, list)) or not aspects:
        raise NavigationError("aspects 必须为非空序列")
    if ancestor_labels is not None and not isinstance(ancestor_labels, Mapping):
        raise NavigationError("ancestor_labels 必须为映射或 None")
    if sibling_keys is not None and not isinstance(sibling_keys, Mapping):
        raise NavigationError("sibling_keys 必须为映射或 None")
    if (ancestor_labels is None) != (sibling_keys is None):
        raise NavigationError(
            "ancestor_labels 与 sibling_keys 必须同时给出或同时省略（fail-closed："
            f"只给其一会让 {NAV_SIBLING_ITEM_RULE_ID} / 父节点回退之一静默失效）")
    entries: list[AspectNavigationEntry] = []
    seen: set[str] = set()
    for aspect in aspects:
        entry = _entry_for(aspect, ancestor_labels, sibling_keys)
        if entry.aspect_id in seen:
            raise NavigationError(f"aspect_id 重复：{entry.aspect_id!r}")
        seen.add(entry.aspect_id)
        entries.append(entry)
    return AspectNavigationProfile.create(
        contract_version=contract_version,
        contract_fingerprint=contract_fingerprint,
        entries=tuple(entries))


# ---------------------------------------------------------------------------
# 2. 树的只读导航视图
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class NavigationLimits:
    """有界导航预算（调用方给不出界就必须显式给）。"""

    max_candidates: int = 8
    max_subtree_nodes: int = 24
    min_score: float = 0.34

    def __post_init__(self) -> None:
        for name in ("max_candidates", "max_subtree_nodes"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise NavigationError(f"{name} 必须为正整数，得到 {value!r}")
        if not isinstance(self.min_score, (int, float)) or isinstance(
                self.min_score, bool) or not (0.0 <= float(self.min_score) <= 1.0):
            raise NavigationError(
                f"min_score 必须为 [0,1] 实数，得到 {self.min_score!r}")


class NavigationIndex:
    """标题树 + 简介的**只读**导航索引（不含任何 Evidence）。

    索引只持节点标题/路径、简介文本与跨引用来源节点集合；它没有 span、没有
    Evidence、没有 authority，因此导航结果不可能"顺带"变成证据。

    `body_char_counts`（可选）是**每次调用由调用方给的逐节点"自有准入正文字符数"**，
    只以**计数**形式进入索引——索引里没有正文文本，所以它仍然拿不到任何证据内容，
    只能回答"这一节自带多少字正文"。`anp-7` 的主体补读规则用得上它：判"补读的是不是
    一节正文"必须看**真读进来多少字**，而简介长度是**抽取式摘要**，两者在真实文档上
    会分叉（必须过的 `1、主要业务` 自有正文 441 字、简介只有 78 字——按简介量卡门
    会把该读的那一节挡在外面）。调用方不给这个量时 `body_measure_available` 为假，
    补读规则**不猜**、直接记 `body_measure_unavailable`。
    """

    __slots__ = ("_nodes", "_by_id", "_children", "_synopsis_text", "_cross_ref",
                 "_unavailable_reason", "_tree_text", "_doc_order", "_key_forms",
                 "_segments", "_segment_present", "_body_chars",
                 "outline_id", "span_count_by_node")

    def __init__(self, outline: DocumentOutline, synopses: Iterable[NavigationSynopsis],
                 *, span_node_ids: Iterable[str] = (),
                 body_char_counts: Mapping[str, int] | None = None) -> None:
        if not isinstance(outline, DocumentOutline):
            raise NavigationError("导航索引需要真实 DocumentOutline")
        self.outline_id = outline.outline_id
        self._nodes = tuple(outline.nodes)
        self._by_id = {n.node_id: n for n in self._nodes}
        children: dict[str, list[str]] = {n.node_id: [] for n in self._nodes}
        for node in self._nodes:
            if node.parent_id is not None:
                if node.parent_id not in children:
                    raise NavigationError(
                        f"节点 {node.node_id!r} 的父节点 {node.parent_id!r} 不在树上")
                children[node.parent_id].append(node.node_id)
        self._children = {k: tuple(v) for k, v in children.items()}

        syn_text: dict[str, tuple[str, ...]] = {}
        for synopsis in synopses:
            if not isinstance(synopsis, NavigationSynopsis):
                raise NavigationError("synopses 必须全为 NavigationSynopsis")
            if synopsis.node_id not in self._by_id:
                raise NavigationError(
                    f"简介的 node_id {synopsis.node_id!r} 不在本树上")
            if synopsis.status == "available":
                syn_text[synopsis.node_id] = tuple(
                    normalize_navigation_text(s.text) for s in synopsis.snippets)
            else:
                syn_text[synopsis.node_id] = ()
        self._synopsis_text = syn_text

        cross: set[str] = set()
        for edge in outline.edges:
            if edge.edge_kind != "cross_reference" or edge.occurrence is None:
                continue
            ref = edge.from_ref
            if ref.startswith("node:"):
                node_id = ref.split(":", 1)[1]
                if node_id in self._by_id:
                    cross.add(node_id)
        self._cross_ref = frozenset(cross)

        self.span_count_by_node = {nid: 0 for nid in self._by_id}
        for node_id in span_node_ids:
            if node_id in self.span_count_by_node:
                self.span_count_by_node[node_id] += 1

        if body_char_counts is None:
            self._body_chars: dict[str, int] | None = None
        else:
            counts: dict[str, int] = {}
            for node_id, value in body_char_counts.items():
                if node_id not in self._by_id:
                    raise NavigationError(
                        f"自有正文量里的 node_id {node_id!r} 不在本树上")
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise NavigationError(
                        f"自有正文量必须是非负整数，得到 {node_id!r}={value!r}")
                if value:
                    counts[node_id] = value
            self._body_chars = counts

        # 全树可导航文本（标题 + 祖先路径 + 简介）。它只用于**归一化分母**：
        # 一个在树里任何地方都不出现的导航键（例如 Contract 声明的字段名而文档没写）
        # 不该拉低真正命中的候选得分。
        self._tree_text = tuple(
            self.normalized_title(nid)
            + "".join(self.normalized_title(a) for a in self.ancestors_of(nid))
            + "".join(self.synopsis_text(nid))
            for nid in self._by_id)
        #: 文档顺序（`outline.nodes` 的顺序）——同分候选的确定性平局规则用**先出现的**，
        #: 而不是用 id 字典序（id 是哈希，字典序没有语义）。
        self._doc_order = {nid: i for i, nid in enumerate(self._by_id)}

        #: 键 → 匹配形态 的缓存（索引不可变，因此缓存只影响耗时、不影响结果）。
        self._key_forms: dict[str, tuple[str, ...]] = {}
        #: node → 完整标签段 的缓存（`NAV_SEGMENT_RULE_ID`；同上，只影响耗时）。
        self._segments: dict[str, tuple[str, ...]] = {}
        #: 键 → 本树里是否存在**完整标签段**命中 的缓存。
        self._segment_present: dict[str, bool] = {}

        if not self._nodes:
            self._unavailable_reason = "标题树为空"
        elif not syn_text:
            self._unavailable_reason = "没有任何节点带简介（简介结构不可用）"
        else:
            self._unavailable_reason = None

    # -- 只读访问 ---------------------------------------------------------

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(self._by_id)

    @property
    def cross_reference_node_ids(self) -> frozenset[str]:
        return self._cross_ref

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    def node(self, node_id: str) -> OutlineNode | None:
        return self._by_id.get(node_id)

    def children_of(self, node_id: str) -> tuple[str, ...]:
        return self._children.get(node_id, ())

    def document_order(self, node_id: str) -> int:
        """节点在文档中的出现次序（同分候选的确定性平局规则）。"""
        return self._doc_order.get(node_id, len(self._doc_order))

    def subtree_of(self, node_id: str) -> tuple[str, ...]:
        """按文档顺序返回 node 自身及其全部后代。"""
        out: list[str] = []
        stack = [node_id]
        while stack:
            current = stack.pop()
            out.append(current)
            stack.extend(reversed(self._children.get(current, ())))
        return tuple(out)

    def ancestors_of(self, node_id: str) -> tuple[str, ...]:
        out: list[str] = []
        node = self._by_id.get(node_id)
        while node is not None and node.parent_id is not None:
            node = self._by_id.get(node.parent_id)
            if node is None:
                break
            out.append(node.node_id)
        return tuple(out)

    def normalized_title(self, node_id: str) -> str:
        node = self._by_id.get(node_id)
        return "" if node is None else normalize_navigation_text(node.title_normalized)

    def synopsis_text(self, node_id: str) -> tuple[str, ...]:
        return self._synopsis_text.get(node_id, ())

    @property
    def body_measure_available(self) -> bool:
        """调用方是否给了逐节点自有正文量（没给时 `anp-7` 不补读、只记状态）。"""
        return self._body_chars is not None

    def body_chars(self, node_id: str) -> int:
        """本节点**自带**的准入正文字符数（不含任何后代）。

        索引没有这个量时返回 -1（**不是** 0）——"量不出来"与"量出来是零"是两件
        不同的事，调用方必须能区分，否则会把"没法判定"当成"这一节没有正文"。
        """
        if self._body_chars is None:
            return -1
        return self._body_chars.get(node_id, 0)

    def subtree_body_chars(self, node_id: str, *,
                           limit: int = _SUPPLEMENT_SUBTREE_MAX_NODES) -> int:
        """本节点**有界子树**（节点自身 + 前 `limit` 个后代）的准入正文字符数之和。

        与 `body_chars` 是**两个量**，不得互相顶替：`body_chars` 回答"这一节自己写了多少
        字"（`anp-7` 的判据 ③ 用它），本量回答"这一节连同子节一共有多少字"（`anp-9` 的
        上提补读用它）。正文挂在子节上的章节（`四、主营业务分析`）前者近零、后者数千，
        只按前者判会把它整段当成"空标题"挡在门外。索引没有正文量时同样返回 -1。
        纯计数：只读 `subtree_of` 与逐节点正文字符数，不看正文内容。
        """
        if self._body_chars is None:
            return -1
        nodes = self.subtree_of(node_id)[: max(limit, 1)]
        return sum(self._body_chars.get(n, 0) for n in nodes)

    def _text_contains(self, needle: str) -> bool:
        return any(needle in text for text in self._tree_text)

    def key_match_forms(self, key: str) -> tuple[str, ...]:
        """一个声明导航键在本树里的**匹配形态**（`NAV_SUBFORM_RULE_ID`）。

        - 整体标签在本树出现过 → `(key,)`：只看整体，**不**再拆成更泛的子形态，
          这样"文档确实写了复合标签"的场景不会被子形态带来的泛化命中干扰；
        - 整体缺席、但并列子形态在树上出现过 → 那些子形态（每个都必须真的出现）；
        - 两者都不成立 → `()`（该键对本树没有任何匹配形态，等价于缺席）。

        `company_id` 不参与：本方法只读树文本与键自身。
        """
        cached = self._key_forms.get(key)
        if cached is not None:
            return cached
        if self._text_contains(key):
            forms: tuple[str, ...] = (key,)
        else:
            forms = tuple(s for s in subform_keys(key) if self._text_contains(s))
        self._key_forms[key] = forms
        return forms

    def keys_present(self, nav_keys: Sequence[str]) -> tuple[str, ...]:
        """在**本树**里至少出现过一次的导航键（确定性；只用于归一化打分分母）。

        一个键算"出现"，当且仅当它有**匹配形态**（整体在树上，或整体缺席但并列子形态
        在树上）——分母的单位始终是 **Contract 声明的键**，不是切出来的片段，因此
        "文档只写了复合标签的一半"不会被当成两个键来稀释命中。
        """
        return tuple(k for k in nav_keys if self.key_match_forms(k))

    def produces_coverage(self) -> bool:
        """恒为 False：导航索引与导航结果都不产生 coverage / authority。"""
        return False

    # -- 完整标签段视图（`NAV_SEGMENT_RULE_ID`） -----------------------------

    def label_segments(self, node_id: str) -> tuple[str, ...]:
        """该节点**原始标题**的完整标签段（缓存；索引不可变）。"""
        cached = self._segments.get(node_id)
        if cached is None:
            node = self._by_id.get(node_id)
            cached = () if node is None else title_label_segments(node.title)
            self._segments[node_id] = cached
        return cached

    def key_segment_forms(self, key: str, node_id: str) -> tuple[str, ...]:
        """`key` 在该节点标题上**整段相等**的那些形态（可能为空）。

        只认**声明键自身**与标题的某个完整标签段逐字符相同：**不做**并列子形态展开
        （`NAV_SUBFORM_RULE_ID` 是另一条规则，只服务本层的三路打分）。这一条收紧是
        必须的——祖先层是**回退**，它的安全性全靠"标题就是那个词"这一精确判据；一旦
        展开子形态，`产品与方案` 会经 `产品` 命中无关标题的片段（真实募集说明书上已出现）。
        因此"长句披露标题里夹带该词"得到空元组——那是片段命中，不是"这个标题是关于它的"。
        """
        segments = self.label_segments(node_id)
        if not segments:
            return ()
        return (key,) if key in segments else ()

    def key_has_segment_match(self, key: str) -> bool:
        """本树里是否**存在**某个节点标题的完整标签段等于该键本身。

        它取代 `keys_present` 成为祖先层打分的归一化分母：一个在本树里只以"片段"出现过
        的祖先键（例如长句披露标题里夹带的那一个）不该稀释真正整段命中的父节点。
        """
        cached = self._segment_present.get(key)
        if cached is None:
            cached = any(self.key_segment_forms(key, nid) for nid in self._by_id)
            self._segment_present[key] = cached
        return cached


# ---------------------------------------------------------------------------
# 3. 候选打分与选择
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class NavigationCandidate:
    """单个 node 的候选打分（**只排序，不改状态**）。

    `in_read_set` / `discard_reason` 回答的是"这条候选**有没有被读**、没被读是因为
    什么"——两者互补且封闭：`in_read_set` 为真 ⟺ `discard_reason is None`，且原因
    必须取自 `DISCARD_REASONS`。候选"凭什么成为候选"在 `reasons` 里。
    """

    node_id: str
    title: str
    structural_path: tuple[str, ...]
    level: int
    score: float
    score_breakdown: tuple[tuple[str, float], ...]
    hit_keys: tuple[str, ...]
    reasons: tuple[str, ...]
    in_read_set: bool
    discard_reason: str | None

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id, "title": self.title,
            "structural_path": list(self.structural_path), "level": self.level,
            "score": self.score,
            "score_breakdown": {k: v for k, v in self.score_breakdown},
            "hit_keys": list(self.hit_keys), "reasons": list(self.reasons),
            "in_read_set": self.in_read_set,
            "discard_reason": self.discard_reason,
        }


@dataclasses.dataclass(frozen=True)
class NavigationDecision:
    """导航终态：选定**有界读集** **或**显式 fallback（没有第三态）。

    - `selected_node_id` / `subtree_node_ids`：最强候选上提后的最小充分 node 及其子树
      （只作身份与解释用；真正的读集是 `read_node_ids`）；
    - `band_node_ids`：落在近分带内的根候选（同分/近分必须一起考察）；
    - `read_root_node_ids`：近分带根上提到最上层命中祖先后的**读根**，读集由它们展开；
    - `read_node_ids`：**有界读集**，按候选强度优先排序、由 `max_subtree_nodes` 截断；
    - `unread_node_ids` / `unread_total`：被预算截断的真实范围（如实记录，不当作
      "不相关"）；
    - `supplement_root_node_ids` / `supplement_rule_status`：本层定位有缺陷时（读集里
      没有任何一节贴上祖先声明键，含"选错了栏目那一节"）按祖先键做的有界补读，见
      `NAV_SUPPLEMENT_RULE_ID`。补读只往读集里**加**节点，不改本层的选出结果。
    - `lift_root_node_ids` / `lift_rule_status`：把已读节点所属的**那一节**读进来，见
      `NAV_LIFT_RULE_ID`。**与上一条是两条不同的规则，各自记各的账**：`anp-7` 找的是自带
      正文的短节，本条找的是"正文在**有界子树**里"的父节（自己的正文可以是 0）。两者的
      候选判据、排序、状态、审计行都不得互相冒充。
    """

    aspect_id: str
    question_id: str
    topic_id: str
    status: str
    fallback_reason: str | None
    nav_keys: tuple[str, ...]
    #: 祖先层键（`NAV_PARENT_RULE_ID`；Contract 主题/问题文本派生）。它随终态一起带出来，
    #: 是为了让"这次读集是按**哪一层**的键定位的"在产物里可回答——只报 `nav_keys` 会让
    #: 祖先层生效的读集看起来像本层定位的结果。
    parent_keys: tuple[str, ...]
    expected_forms: tuple[str, ...]
    selected_node_id: str | None
    subtree_node_ids: tuple[str, ...]
    band_node_ids: tuple[str, ...]
    read_root_node_ids: tuple[str, ...]
    read_node_ids: tuple[str, ...]
    unread_node_ids: tuple[str, ...]
    unread_total: int
    ranked: tuple[NavigationCandidate, ...]
    limits: NavigationLimits
    outline_id: str
    profile_id: str
    profile_locator: str
    rule_version: str
    versions: tuple[tuple[str, str], ...]
    #: 跨源导航决策显式携带的**文档键**（`§L2.4`）。
    #:
    #: `node_id` 本身确实内容寻址到 `document_id`（`derive_node_id` 经
    #: `document_outline_locator`），因此不同文档的 node_id 不会撞名；但源集合并后，读者
    #: 无法**直接**知道某个节点属于哪份文档，而靠**反解 id 字符串**判断归属是不可接受的
    #: （id 是哈希，随时可能升版）。故在跨源决策里显式携带，而不是让人去猜。
    #:
    #: 单文档导航（`navigate`）保持 `None`——那时「哪份文档」由调用方自己的绑定回答，多带
    #: 一个字段只会让旧读回的字节变样。跨源导航（`SourceSetNavigationIndex.navigate_all`）
    #: **必须**填写。
    document_key: dict | None = None
    #: **主体补读**（`NAV_SUPPLEMENT_RULE_ID`，`anp-7` 起）真正加进读集的那几节。
    #:
    #: 这些节点**不是**本层打分选出来的，而是本层定位**有缺陷**（读集里没有任何一节
    #: 贴上祖先声明键）时按祖先键做的有界补读。它们与 `read_root_node_ids` 一起展开读集、
    #: 一起受 `max_subtree_nodes` 截断（排在后面，因此预算先满足本层），并逐条记在
    #: `ranked[].reasons` 里。空元组 = 没有补读。
    supplement_root_node_ids: tuple[str, ...] = ()
    #: 补读规则对本层的结论（`SUPPLEMENT_STATUSES` 的封闭取值）。
    #:
    #: 必须能与"没补读"区分开：`no_defect` 是"本层读集里已经有一节贴上祖先键"，
    #: `body_measure_unavailable` 是"索引没给自有正文量、判不了所以不猜"，`no_candidate`
    #: 是"判了，但树上没有合格的补读根"——三者对下游的含义完全不同。默认
    #: `not_applicable` 只出现在不经 `navigate` 构造的读回（旧产物）上。
    supplement_rule_status: str = "not_applicable"
    #: **上提补读**（`NAV_LIFT_RULE_ID`，`anp-9` 起）真正加进读集的那几节。
    #:
    #: 它们同样**不是**本层打分选出来的，来源却是**另一条判据**：已读节点的祖先里，标题整段
    #: 贴上祖先声明键、且有界子树里正文够量的那一节。与 `supplement_root_node_ids` 分开记，
    #: 是因为两者的合格条件互不相同（这里**不要求**根自带正文）；混成一个字段会让"补读根
    #: 都自带实质正文"这条 `anp-7` 的保证在 `anp-9` 下悄悄失效。展开顺序排在最后。
    lift_root_node_ids: tuple[str, ...] = ()
    #: 上提补读规则对本层的结论（`SUPPLEMENT_STATUSES` 的封闭取值），与
    #: `supplement_rule_status` 同一套词表但**各记各的**。
    lift_rule_status: str = "not_applicable"

    def produces_coverage(self) -> bool:
        return False

    def is_fallback(self) -> bool:
        return self.status == "fallback"

    def to_dict(self) -> dict:
        return {
            "aspect_id": self.aspect_id, "question_id": self.question_id,
            "topic_id": self.topic_id, "status": self.status,
            "fallback_reason": self.fallback_reason,
            "nav_keys": list(self.nav_keys),
            "parent_keys": list(self.parent_keys),
            "expected_forms": list(self.expected_forms),
            "selected_node_id": self.selected_node_id,
            "subtree_node_ids": list(self.subtree_node_ids),
            "band_node_ids": list(self.band_node_ids),
            "read_root_node_ids": list(self.read_root_node_ids),
            "read_node_ids": list(self.read_node_ids),
            "unread_node_ids": list(self.unread_node_ids),
            "unread_total": self.unread_total,
            "ranked": [c.to_dict() for c in self.ranked],
            "limits": {"max_candidates": self.limits.max_candidates,
                       "max_subtree_nodes": self.limits.max_subtree_nodes,
                       "min_score": self.limits.min_score},
            "outline_id": self.outline_id,
            "profile_id": self.profile_id,
            "profile_locator": self.profile_locator,
            "rule_version": self.rule_version,
            "versions": {k: v for k, v in self.versions},
            "document_key": dict(self.document_key) if self.document_key else None,
            "supplement_root_node_ids": list(self.supplement_root_node_ids),
            "supplement_rule_status": self.supplement_rule_status,
            "lift_root_node_ids": list(self.lift_root_node_ids),
            "lift_rule_status": self.lift_rule_status,
        }


def _axis_hits(index: NavigationIndex, nav_keys: Sequence[str],
               text: str) -> tuple[str, ...]:
    """某一路文本（标题 / 路径 / 单条简介）命中的导航键。

    命中判定用键的**匹配形态**（整体优先、缺席才用并列子形态）；命中计数仍按
    **声明键**计——同一键的多个子形态同时出现只算一次，不放大得分。
    """
    return tuple(k for k in nav_keys
                 if any(form in text for form in index.key_match_forms(k)))


def score_node(index: NavigationIndex, nav_keys: Sequence[str], node_id: str
               ) -> tuple[float, tuple[tuple[str, float], ...], tuple[str, ...]]:
    """标题 / 路径 / 简介三路命中打分（确定性、无 I/O、无随机）。

    分母是**在本树里出现过**的导航键数（`index.keys_present`），不是契约声明的键数：
    文档里根本没写的声明字段不该稀释真正命中的候选。"出现过"按键的匹配形态判定
    （整体缺席时并列子形态也算），因此"文档只写了复合标签的一半"既不会被当成两个键
    稀释命中，也不会因为整体缺席而让该 aspect 完全没有候选。一个键都没有匹配形态时
    全部候选为 0，于是必然走 `low_confidence` fallback——不猜。
    """
    present = index.keys_present(nav_keys)
    if not present:
        return 0.0, (("title", 0.0), ("path", 0.0), ("synopsis", 0.0)), ()
    nav_keys = present
    total = len(nav_keys)
    title = index.normalized_title(node_id)
    title_hits = _axis_hits(index, nav_keys, title)
    path_text = "".join(index.normalized_title(a)
                        for a in index.ancestors_of(node_id))
    path_hits = _axis_hits(index, nav_keys, path_text)
    syn_hits: tuple[str, ...] = ()
    for text in index.synopsis_text(node_id):
        syn_hits = syn_hits + _axis_hits(index, nav_keys, text)
    title_score = SCORE_WEIGHT_TITLE * len(set(title_hits)) / total
    path_score = SCORE_WEIGHT_PATH * len(set(path_hits)) / total
    syn_score = SCORE_WEIGHT_SYNOPSIS * len(set(syn_hits)) / total
    raw = title_score + path_score + syn_score
    ceiling = SCORE_WEIGHT_TITLE + SCORE_WEIGHT_PATH + SCORE_WEIGHT_SYNOPSIS
    score = round(raw / ceiling, 6)
    breakdown = (("title", round(title_score / ceiling, 6)),
                 ("path", round(path_score / ceiling, 6)),
                 ("synopsis", round(syn_score / ceiling, 6)))
    hits = tuple(sorted(set(title_hits) | set(path_hits) | set(syn_hits)))
    return score, breakdown, hits


def segment_hits(index: NavigationIndex, keys: Sequence[str], node_id: str
                 ) -> tuple[str, ...]:
    """本节点标题上**整段相等**地命中的键（`NAV_SEGMENT_RULE_ID`）。"""
    return tuple(k for k in keys if index.key_segment_forms(k, node_id))


def _anchor_text(text: str) -> str:
    """贴标签比较用的文本：剥掉**编号前缀**再规范化（与 `title_label_segments` 同口径）。

    两侧口径必须一致：`title_label_segments` 逐段剥编号（`3、经营模式` → `经营模式`），
    而 Contract 派生的键可能**带**编号（`1产品甲`，见 `aspect_nav_keys`）。不归一化的话
    "整段就是那个词"会永远对不上。纯字符串处理，无词表。
    """
    return normalize_navigation_text(_ENUM_PREFIX.sub("", text))


def _form_is_anchored(segments: Sequence[str], key: str, form: str,
                      short_title: bool) -> bool:
    """键 `key` 的匹配形态 `form` 是否**落在**某个完整标签段上（`NAV_ROOT_ANCHOR_RULE_ID`）。

    判据（全部是纯字符串关系，无词表、无公司 / 页码 / 答案词）：
    - 整段相等：任何长度、任何标题都算——标题的某一段就是这个词；
    - 前 / 后缀与整键夹带：只在 `short_title`（标题规范化后 ≤ `_LABEL_MAX_CHARS`）时成立。
      短标题是"字段名"的形态：`2、主要产品及其用途` 的 `主要产品` 以 `产品` 为后缀，
      `1、营业收入构成` 里完整出现声明字段 `收入`；长标题是句子，句子里切出的片段
      （`销售模式的情况`）只是同词夹带，不构成"这个标题是关于那个字段的"证据。
    - 整键夹带只对 `form == key`（Contract **声明的整键**）成立，不对并列子形态成立：
      `衍生产品情况` 里的 `产品` 是 `产品与方案` 的子形态（已被 `NAV_SUBFORM_RULE_ID`
      削弱过一次），它必须**整段**或**前/后缀**落在标签段上才算数。
    """
    for segment in segments:
        if segment == form:
            return True
        if not short_title:
            continue
        if segment.startswith(form) or segment.endswith(form):
            return True
        if form == key and form in segment:
            return True
    return False


def label_anchored_keys(index: NavigationIndex, nav_keys: Sequence[str],
                        node_id: str) -> tuple[str, ...]:
    """本节点标题上**贴标签**的键（`NAV_ROOT_ANCHOR_RULE_ID`）。

    只认**声明键的匹配形态**（`key_match_forms`：整体优先、整体缺席才用并列子形态）是否
    落在标题的完整标签段上（判据见 `_form_is_anchored`），两侧都先归到 `_anchor_text`
    的同一口径。放宽是否生效由**标题本身的长短**决定，不由"片段的长短"决定：一个长句
    披露标题里可能切出很短的片段（`销售模式的情况`），片段短不代表标题是字段名。
    "命中了但贴不上标签"的候选仍然留在候选审计里（舍弃原因 `not_label_anchored`），
    只是不作读根。
    """
    segments = index.label_segments(node_id)
    if not segments:
        return ()
    #: 标题长短取**原始标题**（编号前缀在规范化时就没了分隔符，之后再剥会剥不掉，
    #: `1、主营业务收入分析` 就会因为多出那个 `1` 而被当成句子），口径与
    #: `title_label_segments` 一致。
    node = index.node(node_id)
    short_title = (len(_anchor_text("" if node is None else node.title))
                   <= _LABEL_MAX_CHARS)
    out: list[str] = []
    for key in nav_keys:
        anchor_key = _anchor_text(key)
        if anchor_key == "":
            continue
        if any(_form_is_anchored(segments, anchor_key, _anchor_text(form),
                                 short_title)
               for form in index.key_match_forms(key)):
            out.append(key)
    return tuple(out)


def subject_adjacency(index: NavigationIndex, subject_head: str, node_id: str) -> int:
    """读根标题与该 aspect **主体标签**的最长公共子串长度（`NAV_ROOT_SUBJECT_RULE_ID`）。

    比较对象取该节点标题与它各祖先标题里**最接近**的那个：读根常在一个大章节之下
    （`第六章… / 2、收入与成本`），主体词可能落在任一层。纯字符串运算，无词表。
    """
    if subject_head == "":
        return 0
    best = longest_common_substring_length(subject_head, index.normalized_title(node_id))
    for ancestor in index.ancestors_of(node_id):
        value = longest_common_substring_length(
            subject_head, index.normalized_title(ancestor))
        if value > best:
            best = value
    return best


def read_root_disqualification(index: NavigationIndex, entry: AspectNavigationEntry,
                               node_id: str) -> str | None:
    """候选**没通过读根资格**的原因（通过则 `None`）；原因取自封闭集合。

    两道判据都只读树与 Contract 声明文本：先要求本层键贴标签（`NAV_ROOT_ANCHOR_RULE_ID`），
    再要求主体邻接 ≥ `_TOPIC_SEGMENT_MIN_SHARED`（`NAV_ROOT_SUBJECT_RULE_ID`）。
    `subject_head` 为空时**不加**第二道——没有主体标签的 aspect 不能凭空造一个来收紧自己。
    """
    if not label_anchored_keys(index, entry.nav_keys, node_id):
        return "not_label_anchored"
    if entry.subject_head == "":
        return None
    if (subject_adjacency(index, entry.subject_head, node_id)
            < _TOPIC_SEGMENT_MIN_SHARED):
        return "not_subject_adjacent"
    return None


def is_read_root_corroborated(index: NavigationIndex, entry: AspectNavigationEntry,
                              node_id: str) -> bool:
    """候选是否通过**读根资格**（`NAV_ROOT_ANCHOR_RULE_ID` + `NAV_ROOT_SUBJECT_RULE_ID`）。"""
    return read_root_disqualification(index, entry, node_id) is None


def _subsequence_containment(pattern: str, text: str) -> int:
    """`pattern` 里能**按序**对上 `text` 的最多字符数（贪心取最早出现位置，最优）。

    只数"按序出现了几个字"，不要求连续——因此它比公共子串**强**在"顺序对得上"、
    **弱**在"不要求挨着"。真实用法里必须再叠首末锚（见 `ordered_variant_length`）。

    对不上的字必须**跳过继续试**，不能就地停下：`入成本毛利构` 对 `入构` 的按序最长匹配是
    `入` + `构` = 2（中间四个字树上没有），一旦在第一个对不上的 `成` 处收工就会算成 1，
    `营业收入构成` 对 `收入成本毛利构成` 因此从 4 掉到 3，把该补读的那一节挡在外面。
    """
    matched = 0
    position = 0
    for char in pattern:
        found = text.find(char, position)
        if found < 0:
            continue
        matched += 1
        position = found + 1
    return matched


def ordered_variant_length(key: str, title: str) -> int:
    """`key` 作为**按序子序列**对上 `title` 的最长长度，且**首字符与末字符都必须对上**。

    首末对齐是这条判据的锚，也是它与"公共子串长度 ≥ 2"的分水岭：
    `主营业务` ↔ `1、主要业务` 是首字对首字、末字对末字、中间按序再夹带一字；而
    `25、合同成本` 对 `收入成本毛利构成` 连**首字**都对不上，`1、报告期内利润分配政策…`
    对 `报告期与口径` **末字**对不上，`（1）动力业务` 对 `主营业务` 首字对不上。
    只数"共同出现了几个字"会把这几类全部放进来——真实年报上 `报告期` 一个键会一次
    拉起三十余节与主营业务无关的章节。纯字符串运算：无分词、无词典、无公司/页码/答案词。

    返回值**只是长度**，不含"够不够"的判断：门槛（含"不能只对上首末两字"）在
    `is_ordered_variant` 里，本函数同时被读回页当作**可读量**直接展示。
    """
    if not isinstance(key, str) or not isinstance(title, str):
        return 0
    if len(key) < _LABEL_MIN_CHARS or title == "":
        return 0
    start = title.find(key[0])
    if start < 0:
        return 0
    end = title.rfind(key[-1])
    if end <= start:
        return 0
    return 2 + _subsequence_containment(key[1:-1], title[start + 1:end])


def is_ordered_variant(key: str, title: str) -> bool:
    """`title` 是不是祖先键 `key` 的一个**按序近似形态**（`NAV_SUPPLEMENT_RULE_ID` 的候选判据）。

    两道门槛，缺一不可：

    1. **长度** = `max(_TOPIC_SEGMENT_MIN_SHARED, ⌈len(key) / 2⌉)`：按序对上的字符数至少是
       该键的一半。取一半而不是"差一个字"是因为长键（`收入成本毛利构成`）差一个字就意味着
       只对上 7 / 8，那已经不是"近似形态"而是"同一个词"；而短键（`主营业务`）由首末锚
       兜底，一半 = 2 已经要求两个不同位置的字，单字凑巧不算证据。
    2. **非平凡**（键长 ≥ 3）：`key[1:-1]` 里至少对上 `_SUPPLEMENT_MIN_INTERIOR_CHARS` 个。
       只有首末两字对齐**不算**近似形态——汉语复合词首末同字是常态：`主营业务` 与募集
       说明书的 `（二）主动债务管理` 首字同为 `主`、末字同为 `务`，中间四字一个都对不上，
       只看门槛 1 会把一节**债务管理**披露当成主营业务的材料读进来。两字键没有内部字，
       门槛 2 对它空转（首末对齐即全部判据）。
    """
    if not isinstance(key, str) or key == "":
        return False
    variant = ordered_variant_length(key, title)
    if variant < max(_TOPIC_SEGMENT_MIN_SHARED, -(-len(key) // 2)):
        return False
    return len(key) <= 2 or variant - 2 >= _SUPPLEMENT_MIN_INTERIOR_CHARS


def subject_body_supplement(
        index: NavigationIndex, entry: AspectNavigationEntry, *,
        read_node_ids: Sequence[str] = (),
        expanded_node_ids: Sequence[str] = (),
        max_roots: int = _SUPPLEMENT_MAX_ROOTS,
        min_body_chars: int = _SUPPLEMENT_MIN_BODY_CHARS,
) -> tuple[tuple[str, ...], str, dict]:
    """`anp-7` 主体补读：本层定位**有缺陷**时，按祖先声明键补齐"没读到的相关主体正文"。

    只看三件事，没有一件是份数、页码、表号或答案词：

    1. **缺陷**（`read_node_ids`）：读集里有没有**任何一节贴上祖先键**
       （`label_anchored_keys`）。没有就是缺陷——包含两种形态：读集为空（本层直接
       fallback），以及"选中了，但选中的是**别的栏目**的那一节"。真实反例是两份年报的
       `main_business`：本层键稳定把它送到 `四、主营业务分析 / 2、收入与成本`，而
       「报告期内公司从事的主要业务 / 1、主要业务」一个字都没进读集。
    2. **候选**（`is_ordered_variant` + 短标题 + 自有正文 ≥ `min_body_chars` + 非跨引用），
       按（是否整段贴上祖先键 → 按序对上字数 → 自有正文长度 → 文档顺序）排序取前
       `max_roots` 个。三条同时成立才算，因此"几十个近似标题"进不来：长标题是句子，
       句子里凑出几个字不构成"这节是关于那个键的"；只共享首末两字同样不算（四字词首末
       同字是常态，`主营业务` ↔ `主动债务管理` 就是这一类）；空标题没正文，补进来只是噪声。
    3. **能不能判**（`index.body_measure_available`）：调用方没给逐节点自有正文量时
       **不补读、也不猜**，返回 `body_measure_unavailable`。简介长度**不能**代这个量：
       必须过的 `1、主要业务` 自有正文 441 字、简介只有 78 字，按简介卡门会把它挡在外面。

    返回 `(补读根, 状态, 明细)`；状态取自 `SUPPLEMENT_STATUSES`，明细里逐条记下命中
    的键与量，供候选审计与读回页直接引用。**它不做任何选择**：调用方拿到根之后照常
    展开、照常受预算截断，本层的选出结果与读根一字不改。
    """
    keys = tuple(entry.parent_keys)
    detail: dict = {"parent_keys": list(keys), "matched": [], "candidates_total": 0,
                    "cap": max_roots, "min_body_chars": min_body_chars}
    if not keys:
        return (), "no_ancestor_keys", detail
    if any(label_anchored_keys(index, keys, node_id) for node_id in read_node_ids):
        return (), "no_defect", detail
    if not index.body_measure_available:
        #: 判不了"这一节有没有正文"就不补：按"没有正文"处理会静默丢掉该读的那一节。
        return (), "body_measure_unavailable", detail

    already = set(expanded_node_ids) | set(read_node_ids)
    cross = index.cross_reference_node_ids
    candidates: list[tuple[tuple, str, str, int, int, str]] = []
    for node_id in index.node_ids:
        if node_id in already or node_id in cross:
            continue
        node = index.node(node_id)
        if node is None:  # pragma: no cover - node_ids 与 _by_id 同源
            continue
        body_chars = index.body_chars(node_id)
        if body_chars < min_body_chars:
            continue
        title = _anchor_text(node.title)
        if len(title) > _LABEL_MAX_CHARS:  # 字段名形态：长标题是句子
            continue
        hits = [(key, ordered_variant_length(key, title)) for key in keys]
        hits = [h for h in hits if is_ordered_variant(h[0], title)]
        if not hits:
            continue
        key, variant = max(hits, key=lambda h: (-h[1], h[0]))
        anchored = bool(label_anchored_keys(index, keys, node_id))
        candidates.append((
            (0, 0 if anchored else 1, -variant, -body_chars,
             index.document_order(node_id), node_id),
            node_id, key, variant, body_chars))
    detail["candidates_total"] = len(candidates)
    if not candidates:
        return (), "no_candidate", detail
    candidates.sort(key=lambda item: item[0])
    chosen = candidates[:max_roots]
    detail["matched"] = [
        {"node_id": node_id, "title": index.node(node_id).title,
         "matched_key": key, "ordered_chars": variant, "body_chars": body}
        for _, node_id, key, variant, body in chosen]
    return tuple(item[1] for item in chosen), "fired", detail


def subject_section_lift(
        index: NavigationIndex, entry: AspectNavigationEntry, *,
        read_node_ids: Sequence[str] = (),
        expanded_node_ids: Sequence[str] = (),
        max_roots: int = _SUPPLEMENT_MAX_ROOTS,
        min_body_chars: int = _SUPPLEMENT_MIN_BODY_CHARS,
        max_subtree_nodes: int = _SUPPLEMENT_SUBTREE_MAX_NODES,
) -> tuple[tuple[str, ...], str, dict]:
    """`anp-9` 上提补读（`NAV_LIFT_RULE_ID`）：把已读节点所属的**那一节**读进来。

    与 `subject_body_supplement`（`anp-7`）是**两条互不相同的判据**，各自记各的账：

    - `anp-7` 找的是"标题本身就是那个键的近似形态、且**自带**正文"的节（`1、主要业务` 这一类
      短节，正文挂在它自己身上）。
    - 本条找的是"已读节点**所属的父节**"：它自己的正文可以是 0（`四、主营业务分析` 这类
      章节标题），实质正文在**有界子树**里。真实反例：`app_scenarios` 的本层键在树上连候选
      都产生不了，靠 `anp-7` 只拿得到 `1、主要业务` 那 441 字，而「（1）动力业务」
      「（2）储能业务」「（3）新兴领域」这些**产品与应用**正文整段进不了读集。

    三个条件同时成立才是候选，没有一件是份数、页码、表号或答案词：

    1. 它是**已读节点的祖先**（`read_node_ids` 的祖先链，不含已读节点自身）——"往上再看一层"
       必须以"这一层我们已经读到过某处"为前提，读集为空时本规则**一个候选都不产生**；
    2. 它的标题**整段贴上**某个祖先声明键（`label_anchored_keys`，严格那一条）；
    3. 它有界子树里的实质正文 ≥ `min_body_chars`（`subtree_body_chars`）。

    返回 `(上提根, 状态, 明细)`；状态取自 `SUPPLEMENT_STATUSES`。**它不做任何选择**：调用方
    拿到根之后照常展开、照常受预算截断，本层的选出结果与读根一字不改——上提根一律**排在**
    已选读根与 `anp-7` 补读根之后展开，因此只做加法。
    """
    keys = tuple(entry.parent_keys)
    detail: dict = {"parent_keys": list(keys), "matched": [], "candidates_total": 0,
                    "cap": max_roots, "min_body_chars": min_body_chars,
                    "max_subtree_nodes": max_subtree_nodes}
    if not keys:
        return (), "no_ancestor_keys", detail
    if not index.body_measure_available:
        #: 判不了"这一节子树里有没有正文"就不上提：按"没有正文"处理会静默丢掉该读的那一节。
        return (), "body_measure_unavailable", detail
    targets = _lift_targets(index, read_node_ids)
    if not targets:
        return (), "no_candidate", detail
    cross = index.cross_reference_node_ids
    already = set(expanded_node_ids) | set(read_node_ids)
    candidates: list[tuple[tuple, str, str, int]] = []
    for node_id in index.node_ids:
        if node_id in already or node_id in cross or node_id not in targets:
            continue
        node = index.node(node_id)
        if node is None:  # pragma: no cover - node_ids 与 _by_id 同源
            continue
        title = _anchor_text(node.title)
        if len(title) > _LABEL_MAX_CHARS:  # 字段名形态：长标题是句子
            continue
        anchored_keys = label_anchored_keys(index, keys, node_id)
        if not anchored_keys:
            continue
        subtree_chars = index.subtree_body_chars(
            node_id, limit=max_subtree_nodes)
        if subtree_chars < min_body_chars:
            continue
        #: 排序**只看文档结构**（先文档顺序），不看得分、也不看子树大小：候选域本来就只有
        #: 已读节点的祖先链，谁先出现谁是那一节，不引入第二套强弱判断。
        candidates.append((
            (index.document_order(node_id), node_id),
            node_id, anchored_keys[0], subtree_chars))
    detail["candidates_total"] = len(candidates)
    if not candidates:
        return (), "no_candidate", detail
    candidates.sort(key=lambda item: item[0])
    chosen = candidates[:max_roots]
    detail["matched"] = [
        {"node_id": node_id, "title": index.node(node_id).title,
         "matched_key": key, "subtree_body_chars": subtree}
        for _, node_id, key, subtree in chosen]
    return tuple(item[1] for item in chosen), "fired", detail


def _lift_targets(index: NavigationIndex,
                  read_node_ids: Sequence[str]) -> frozenset[str]:
    """已读节点的**祖先集合**（不含已读节点自身）——`anp-9` 上提补读的候选域。

    纯树运算：只读 `ancestors_of`。读集为空时返回空集，因此 fallback 形态下这条规则
    一个候选都产生不了（"往上再看到所属的那一节"必须以"这一节我们已经读到过某处"为前提）。
    """
    out: set[str] = set()
    read = set(read_node_ids)
    for node_id in read_node_ids:
        out.update(index.ancestors_of(node_id))
    return frozenset(out - read)


def score_segment_node(index: NavigationIndex, keys: Sequence[str], node_id: str,
                       present: Sequence[str]
                       ) -> tuple[float, tuple[tuple[str, float], ...],
                                  tuple[str, ...]]:
    """祖先层的打分：标题与路径两轴要求**完整标签段**，简介轴仍按匹配形态。

    分母由调用方给定（本树里有完整标签段命中的祖先键），权重与 `score_node` 同一组常量，
    因此近分带宽度、`min_score` 与候选排序的两层含义一致、可比。
    """
    if not present:
        return 0.0, (("title", 0.0), ("path", 0.0), ("synopsis", 0.0)), ()
    total = len(present)
    title_hits = segment_hits(index, present, node_id)
    path_hits: tuple[str, ...] = ()
    for ancestor in index.ancestors_of(node_id):
        path_hits = path_hits + segment_hits(index, present, ancestor)
    syn_hits: tuple[str, ...] = ()
    for text in index.synopsis_text(node_id):
        syn_hits = syn_hits + _axis_hits(index, present, text)
    title_score = SCORE_WEIGHT_TITLE * len(set(title_hits)) / total
    path_score = SCORE_WEIGHT_PATH * len(set(path_hits)) / total
    syn_score = SCORE_WEIGHT_SYNOPSIS * len(set(syn_hits)) / total
    raw = title_score + path_score + syn_score
    ceiling = SCORE_WEIGHT_TITLE + SCORE_WEIGHT_PATH + SCORE_WEIGHT_SYNOPSIS
    score = round(raw / ceiling, 6)
    breakdown = (("title", round(title_score / ceiling, 6)),
                 ("path", round(path_score / ceiling, 6)),
                 ("synopsis", round(syn_score / ceiling, 6)))
    hits = tuple(sorted(set(title_hits) | set(path_hits) | set(syn_hits)))
    return score, breakdown, hits


def _sufficient_root(index: NavigationIndex, scores: Mapping[str, float],
                     start: str) -> str:
    """选择**最小充分** node：命中节点的**最上层命中祖先**。

    为什么必须上提：路径轴给"某个祖先标题命中"加分，因此一个命中节点的**子节点**会
    继承父标题的路径加分，得分天然 ≥ 父节点。若只读得分最高的那个叶子，就会把一个
    明明也命中的**父章节**整段丢掉——正文（含多段业务描述）恰恰挂在父节点上。上提的
    停止条件是"上级本身也是命中候选"（`score > 0`），因此上提范围由文档结构自己决定，
    不会一路爬到卷首：连标题都不含任何声明键的上级不在候选里，上提即止。
    """
    current = start
    while True:
        parents = [a for a in index.ancestors_of(current) if a in scores and scores[a] > 0.0]
        if not parents:
            return current
        # `ancestors_of` 由近到远；取最远的那个命中祖先。
        current = parents[-1]


def _merge(*groups) -> tuple:
    """按 node_id 去重、保序地拼接候选（同一 node 只保留先出现的那条形态）。"""
    out: list = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item[1] in seen:
                continue
            seen.add(item[1])
            out.append(item)
    return tuple(out)


def _candidates(index: NavigationIndex, entry: AspectNavigationEntry,
                items: Sequence[tuple[float, str, tuple[tuple[str, float], ...],
                                     tuple[str, ...]]],
                *, read: frozenset[str], band_roots: frozenset[str],
                unread: frozenset[str], cross: frozenset[str],
                segmented_away: frozenset[str] = frozenset(),
                present_total: int | None = None,
                key_tier: str = NAV_KEY_TIER_DECLARED,
                disqualification: Mapping[str, str] | None = None,
                supplement_roots: frozenset[str] = frozenset(),
                supplement_over_budget: frozenset[str] = frozenset(),
                lift_roots: frozenset[str] = frozenset(),
                lift_over_budget: frozenset[str] = frozenset(),
                ) -> tuple[NavigationCandidate, ...]:
    """把打分项转成带**依据**与**舍弃原因**的候选（确定性；顺序即输入顺序）。

    `segmented_away` 里的候选是"标题里没有本层键的完整标签段命中"的那些
    （`NAV_SEGMENT_RULE_ID`）：它们在祖先层定位成功时只作审计留痕，绝不进读集，因此
    舍弃原因取 `no_complete_label_segment`（跨引用来源仍然优先记它的专用原因）。

    `disqualification`（`anp-5`）是"命中够分、但**没通过读根资格**"的候选 → 原因
    （`not_label_anchored` / `not_subject_adjacent`，见 `read_root_disqualification`）：
    同样只作审计留痕，舍弃原因逐条取它自己那个原因——"贴不上标签"与"贴上了但对不上栏目"
    必须在产物里可区分。

    `supplement_roots`（`anp-7`）是**主体补读**真正加进读集的那几节（见
    `subject_body_supplement`）：它们**不是**本层打分选出来的，因此"没通过读根资格"
    "标题里没有本层键的完整标签段"这类只针对打分候选的话**不能**记在它们头上——
    那是把补读根说成了落选者。它们自己的依据写成规则 id + 命中的那个祖先键。
    `supplement_over_budget` 是"合格但预算没让它进读集"的补读候选：必须与前者分开，
    否则"补读没做成"会被读成"补读成功了"。

    `lift_roots` / `lift_over_budget`（`anp-9`）是**上提补读**同一件事的两半，规则 id 取
    `NAV_LIFT_RULE_ID`——与上面那条**不是**同一条规则（判据、要求、展开顺序都不同），
    两者各自写各自的依据，一个字不得互相冒充。
    """
    present_total = (len(index.keys_present(entry.nav_keys))
                     if present_total is None else present_total)
    out: list[NavigationCandidate] = []
    for score, node_id, breakdown, hits in items:
        node = index.node(node_id)
        if node is None:  # pragma: no cover - 索引自持集合一致
            continue
        reasons = [
            f"title/path/synopsis 命中共 {len(hits)} / {present_total} 个"
            f"树内出现的{key_tier}",
            f"期望形态：{'|'.join(entry.expected_forms)}",
        ]
        if node_id in band_roots:
            reasons.append(f"落在近分带内（{TIE_BAND_RULE_ID}）")
        if node_id in cross:
            reasons.append("该节点是显式跨引用来源，不作根候选")
        disqualified = (None if disqualification is None
                        else disqualification.get(node_id))
        # 注意用词：这里**不能**出现 `NAV_KEY_TIER_ANCESTOR` 那个字面串（"祖先声明键"）。
        # 它在本模块是"这一层是祖先层"的**标记**（`_candidates` 的 tier 行、读回侧
        # 逐候选判定"祖先层是否生效"都按它找），补读根既不是本层候选也不是祖先层候选，
        # 混进去会让"祖先层生效"被误判成真。故写"祖先声明派生的键"。
        if node_id in supplement_roots:
            reasons.append(
                f"本层定位有缺陷（读集里没有一节贴上 Contract 祖先声明派生的键），"
                f"按那些键补读进来的主体节（{NAV_SUPPLEMENT_RULE_ID}）："
                "只加进读集，不改本层选出结果")
        elif node_id in supplement_over_budget:
            reasons.append(
                f"本层定位有缺陷，按 Contract 祖先声明派生的键补读的合格候选"
                f"（{NAV_SUPPLEMENT_RULE_ID}）：但本层读集已吃光预算，没进读集")
        elif node_id in lift_roots:
            reasons.append(
                f"已读节点所属的那一节——标题整段贴上 Contract 祖先声明派生的键，"
                f"且有界子树里正文够量（{NAV_LIFT_RULE_ID}）："
                "只加进读集，不改本层选出结果")
        elif node_id in lift_over_budget:
            reasons.append(
                f"上提补读的合格候选（{NAV_LIFT_RULE_ID}）："
                "但本层读集已吃光预算，没进读集")
        else:
            if node_id in segmented_away:
                reasons.append(
                    f"标题里没有本层键的**完整标签段**命中（见 {NAV_SEGMENT_RULE_ID}）："
                    "只作候选审计，不作读根")
            if disqualified is not None:
                reasons.append(
                    f"没通过读根资格（{NAV_ROOT_ANCHOR_RULE_ID} + "
                    f"{NAV_ROOT_SUBJECT_RULE_ID}）原因 {disqualified}："
                    "只作候选审计，不作读根")
        in_read = node_id in read
        if in_read:
            discard: str | None = None
        elif node_id in supplement_over_budget:
            discard = "over_budget"
        elif node_id in unread:
            discard = "over_budget"
        elif node_id in cross:
            discard = "explicit_cross_reference"
        elif disqualified is not None:
            discard = disqualified
        elif node_id in segmented_away:
            discard = "no_complete_label_segment"
        else:
            discard = "outside_tie_band"
        out.append(NavigationCandidate(
            node_id=node_id, title=node.title,
            structural_path=tuple(node.structural_path), level=node.level,
            score=score, score_breakdown=breakdown, hit_keys=hits,
            reasons=tuple(reasons), in_read_set=in_read, discard_reason=discard))
    return tuple(out)


def navigate(index: NavigationIndex, entry: AspectNavigationEntry, *,
             profile: AspectNavigationProfile, limits: NavigationLimits | None = None
             ) -> NavigationDecision:
    """为一个 Contract aspect 选出 node/subtree，或给出显式 fallback。"""
    if not isinstance(index, NavigationIndex):
        raise NavigationError("navigate 需要 NavigationIndex")
    if not isinstance(entry, AspectNavigationEntry):
        raise NavigationError("navigate 需要 AspectNavigationEntry")
    if not isinstance(profile, AspectNavigationProfile):
        raise NavigationError("navigate 需要 AspectNavigationProfile")
    if entry.aspect_id not in {e.aspect_id for e in profile.entries}:
        raise NavigationError(
            f"aspect {entry.aspect_id!r} 不属于 profile {profile.profile_id!r}"
            f"（fail-closed：不得用别的 profile 导航）")
    limits = NavigationLimits() if limits is None else limits
    if not isinstance(limits, NavigationLimits):
        raise NavigationError("limits 必须为 NavigationLimits")

    versions = (
        ("navigation_profile_schema", profile.schema_version),
        ("navigation_profile_rule", profile.rule_version),
        ("outline_schema", V.OUTLINE_SCHEMA_VERSION),
        ("span_schema", V.SPAN_SCHEMA_VERSION),
    )

    def _decision(status: str, fallback_reason: str | None,
                  selected: str | None, subtree: tuple[str, ...],
                  ranked: tuple[NavigationCandidate, ...], *,
                  band: tuple[str, ...] = (), read_roots: tuple[str, ...] = (),
                  read: tuple[str, ...] = (),
                  unread: tuple[str, ...] = (), unread_total: int = 0,
                  supplement_roots: tuple[str, ...] = (),
                  supplement_status: str = "not_applicable",
                  lift_roots: tuple[str, ...] = (),
                  lift_status: str = ""
                  ) -> NavigationDecision:
        if status not in decision_statuses:
            raise NavigationError(f"未登记的导航终态 {status!r}")
        if not lift_status:
            #: 未经上提补读那一步的终态（`no_navigation_keys` / `structurally_unavailable`
            #: 这类早退）也要给出可判读的结论：没有祖先声明键时，这条规则**本来就无从下手**
            #: （`no_ancestor_keys`），而不是"没试过"——否则下游无法把两者区分开。
            lift_status = ("no_ancestor_keys" if not entry.parent_keys
                           else "not_applicable")
        if supplement_status not in SUPPLEMENT_STATUSES:
            raise NavigationError(f"未登记的补读规则状态 {supplement_status!r}")
        if supplement_roots and supplement_status != "fired":
            raise NavigationError(
                "只有 fired 才允许携带补读根（其余状态一律不补读）")
        if lift_status not in SUPPLEMENT_STATUSES:
            raise NavigationError(f"未登记的上提补读规则状态 {lift_status!r}")
        if lift_roots and lift_status != "fired":
            raise NavigationError(
                "只有 fired 才允许携带上提补读根（其余状态一律不补读）")
        if set(lift_roots) & set(supplement_roots):
            raise NavigationError(
                "同一条读根不得同时记成两条规则补进来的（身份不得混用）")
        if (status == "fallback") != (fallback_reason is not None):
            raise NavigationError("fallback 终态必须且只能携带 fallback_reason")
        if fallback_reason is not None and fallback_reason not in FALLBACK_REASONS:
            raise NavigationError(f"未登记的 fallback 原因 {fallback_reason!r}")
        if status == "selected" and selected is None:
            raise NavigationError("selected 终态必须给出 selected_node_id")
        # 读集 / 近分带 / 舍弃原因的自洽校验：任何一条不闭合都不得返回。
        if len(read) > limits.max_subtree_nodes:
            raise NavigationError(
                f"读集 {len(read)} 超过 max_subtree_nodes={limits.max_subtree_nodes}")
        known = set(index.node_ids)
        if any(n not in known
               for n in tuple(read) + tuple(band) + tuple(read_roots) + tuple(unread)):
            raise NavigationError("读集 / 近分带 / 读根 / 未读范围含不在本树上的 node")
        if len(set(read)) != len(read):
            raise NavigationError("读集出现重复 node")
        if len(set(read_roots)) != len(read_roots):
            raise NavigationError("读根出现重复 node")
        ranked_ids = [c.node_id for c in ranked]
        if len(set(ranked_ids)) != len(ranked_ids):
            raise NavigationError("候选列表出现重复 node")
        if any(n not in ranked_ids for n in band):
            raise NavigationError("近分带根候选必须出现在候选列表里")
        if any(n not in ranked_ids for n in read_roots):
            raise NavigationError("读根必须出现在候选列表里（否则读集无法解释）")
        read_set = set(read)
        for c in ranked:
            if c.in_read_set != (c.node_id in read_set):
                raise NavigationError(f"候选 {c.node_id!r} 的 in_read_set 与读集不一致")
            if (c.discard_reason is None) != c.in_read_set:
                raise NavigationError(f"候选 {c.node_id!r} 的舍弃原因与读集不一致")
            if c.discard_reason is not None and c.discard_reason not in DISCARD_REASONS:
                raise NavigationError(f"未登记的舍弃原因 {c.discard_reason!r}")
        if status == "selected":
            if not read:
                raise NavigationError("selected 终态必须给出非空的有界读集")
            if not read_roots:
                raise NavigationError("selected 终态必须给出非空读根集合")
            # 读根必须是某个近分带根的**祖先或自身**（上提只允许沿祖先链走），
            # 读集则必须由读根子树展开。两条合起来锁死"读集从哪来"。
            #
            # `anp-7` 的**补读根**是这条上提链之外的第三类读根：它按祖先声明键**加**进来，
            # 不对应任何打分候选，因此这里显式放行——但要与"上提出来的读根"分开记账，
            # 不能借放行把上提越界洗成合规。
            supplement_set = frozenset(supplement_roots) | frozenset(lift_roots)
            for node_id in read_roots:
                if node_id in supplement_set:
                    continue
                if not any(node_id == r or r in index.subtree_of(node_id) for r in band):
                    raise NavigationError(
                        f"读根 {node_id!r} 不是任何近分带根的祖先（上提越界）")
            if any(r not in read_set for r in supplement_roots):
                raise NavigationError("补读根必须真的落在读集里（否则不得记成补读成功）")
            if any(r not in read_set for r in lift_roots):
                raise NavigationError("上提补读根必须真的落在读集里（否则不得记成成功）")
            for node_id in read:
                if not any(node_id == r or node_id in index.subtree_of(r)
                           for r in read_roots):
                    raise NavigationError(
                        f"读集含读根子树之外的 node {node_id!r}（读集必须由读根展开）")
        elif read or read_roots:
            raise NavigationError("fallback 终态不得携带读集 / 读根（低置信不读）")
        if unread_total < len(unread):
            raise NavigationError("unread_total 不得小于列出的未读 node 数")
        return NavigationDecision(
            aspect_id=entry.aspect_id, question_id=entry.question_id,
            topic_id=entry.topic_id, status=status, fallback_reason=fallback_reason,
            nav_keys=entry.nav_keys, parent_keys=entry.parent_keys,
            expected_forms=entry.expected_forms,
            selected_node_id=selected, subtree_node_ids=subtree,
            band_node_ids=band, read_root_node_ids=read_roots,
            read_node_ids=read, unread_node_ids=unread,
            unread_total=unread_total, ranked=ranked,
            limits=limits, outline_id=index.outline_id, profile_id=profile.profile_id,
            profile_locator=profile.profile_locator,
            rule_version=profile.rule_version, versions=versions,
            supplement_root_node_ids=tuple(supplement_roots),
            supplement_rule_status=supplement_status,
            lift_root_node_ids=tuple(lift_roots),
            lift_rule_status=lift_status)

    def _expand_roots(roots: Sequence[str]) -> tuple[list[str], tuple[str, ...]]:
        """由读根展开**有界读集**（顺序 = 读根顺序 → 预算截断先满足先出现的读根）。

        返回 `(读集, 读集之外的完整范围)`：后者如实记成未读，不当作"不相关"。
        """
        considered: list[str] = []
        for root in roots:
            for node_id in index.subtree_of(root):
                if node_id not in considered:
                    considered.append(node_id)
        read = considered[: limits.max_subtree_nodes]
        read_set = frozenset(read)
        return read, tuple(n for n in considered if n not in read_set)

    def _supplement_row(node_id: str) -> tuple:
        """补读根的候选行：得分 0——它们**不是**本层打分选出来的。"""
        return (0.0, node_id, (("title", 0.0), ("path", 0.0), ("synopsis", 0.0)), ())

    def _pick(scored, present, *, segmented_away=(), parent_tier: bool = False,
              corroborated: frozenset[str] | None = None) -> NavigationDecision:
        """由已排序候选选出**有界读集**，或给出显式 fallback。

        本层与祖先层共用同一个选择机制（近分带 / 读根上提 / 预算截断 / 候选审计一字不改），
        差别只有：用哪组键打分（`present` 即其归一化分母）、以及"被整段判据刷掉的候选"
        在候选列表里怎么记。`_decision` 的全部不变量因此对两层同时成立。

        `corroborated`（`anp-5`）：本层**通过读根资格**（贴标签 + 主体邻接）的 node 集合。
        给定时，近分带各根照旧上提，但**上提后的读根**必须在这个集合里，否则整支丢弃；
        一支都不剩时给 `no_anchored_read_root` 显式 fallback。祖先层**不传**它：祖先键
        本身就是 Contract 里该 aspect 的祖先声明（主体），再要求一次主体邻接是重复判据、
        只会过度收紧。
        """
        key_tier = NAV_KEY_TIER_ANCESTOR if parent_tier else NAV_KEY_TIER_DECLARED
        cross = index.cross_reference_node_ids
        usable = [item for item in scored if item[1] not in cross]
        excluded = [item for item in scored if item[1] in cross]
        away = tuple(segmented_away)
        away_ids = frozenset(item[1] for item in away)
        present_total = len(present)
        #: 近分带成员 → 它上提后的读根没通过读根资格的原因（逐条记，供候选审计）。
        blocked: dict[str, str] = {}

        def _rank(items, read, band_roots, unread, disq=None, supplement=(),
                  supplement_over=(), lift=(), lift_over=()):
            if disq is None:
                merged = dict(disqualification or {})
                merged.update(blocked)
            else:
                merged = disq
            return _candidates(
                index, entry, tuple(items), read=read, band_roots=band_roots,
                unread=unread, cross=cross, segmented_away=away_ids,
                present_total=present_total, key_tier=key_tier,
                disqualification=(None if parent_tier else merged),
                supplement_roots=frozenset(supplement),
                supplement_over_budget=frozenset(supplement_over),
                lift_roots=frozenset(lift),
                lift_over_budget=frozenset(lift_over))

        def _supplement_try(*, read: Sequence[str] = (),
                            expanded: Sequence[str] = ()) -> tuple[tuple[str, ...], str]:
            """试一次主体补读（`anp-7`），返回 `(补读根, 规则状态)`。

            本层定位**没有缺陷**时这里什么也不做——补读是修缺陷的，不是常规扩读。
            """
            roots, status, _ = subject_body_supplement(
                index, entry, read_node_ids=tuple(read),
                expanded_node_ids=tuple(expanded))
            return roots, status

        def _lift_try(*, read: Sequence[str] = (),
                      expanded: Sequence[str] = ()) -> tuple[tuple[str, ...], str]:
            """试一次上提补读（`anp-9`）：把已读节点所属的那一节读进来。

            与 `_supplement_try` 是**两条规则**：这里只拿"已读节点的祖先"这一类，判据是
            "整段贴上祖先键 + 有界子树正文够量"，**不要求**根自带正文，因此它补进来的节
            （`四、主营业务分析` 这类）与 `anp-7` 补进来的短节（`1、主要业务`）分开记账。
            """
            roots, status, _ = subject_section_lift(
                index, entry, read_node_ids=tuple(read),
                expanded_node_ids=tuple(expanded),
                max_subtree_nodes=limits.max_subtree_nodes)
            return roots, status

        def _admit(roots: Sequence[str], within: Sequence[str]) -> tuple[
                tuple[str, ...], frozenset[str]]:
            """从一批合格根里挑出**真的落在当前读集里**的那些（其余如实记 `over_budget`）。"""
            read_now = frozenset(within)
            return (tuple(r for r in roots if r in read_now),
                    frozenset(roots) - read_now)

        def _supplement_selected(supp_roots: Sequence[str],
                                 base_items: Sequence[tuple]) -> NavigationDecision | None:
            """本层**没有读集**（回退路径）时，把合格补读根变成 selected 终态。

            一个都没进读集就返回 `None`（调用方照旧给回退终态）——**不**冒充补读成功。
            `band` 保持为空：补读根不在任何近分带里，塞进 `band_node_ids` 会把
            "按祖先键补的"说成"打分选出来的"。

            `anp-9`：回退路径上再走一次**上提补读**（`NAV_LIFT_RULE_ID`）——本层零候选的
            aspect（真实反例：两份年报的 `app_scenarios`，本层键 `应用场景` 在树上连候选都
            产生不了）在 `anp-8` 下只能靠近似形态拿到 `1、主要业务` 那一类短节，而「（1）动力
            业务」「（3）新兴领域」那些正文整段到不了读集。上提的**读基**就是 `anp-7` 补进来
            的那几节（不是它们的子树）："往上再看一层"只以"这一节我们已经读到过"为前提。
            """
            read, unread_all = _expand_roots(tuple(supp_roots))
            lift_roots, lift_status = _lift_try(
                read=tuple(supp_roots),
                expanded=tuple(read) + tuple(unread_all))
            if lift_roots:
                combined = tuple(supp_roots) + tuple(lift_roots)
                read, unread_all = _expand_roots(combined)
            read_set = frozenset(read)
            admitted, over = _admit(supp_roots, read)
            admitted_lift, lift_over = (
                _admit(lift_roots, read) if lift_roots else ((), frozenset()))
            if not admitted and not admitted_lift:
                return None
            if lift_roots and not admitted_lift:
                lift_status = "over_budget"
            extra = tuple(_supplement_row(node_id)
                          for node_id in tuple(supp_roots) + tuple(lift_roots))
            roots_all = tuple(admitted) + tuple(admitted_lift)
            ranked = _rank(_merge(tuple(base_items) + extra, away), read_set,
                           frozenset(), frozenset(unread_all),
                           supplement=admitted, supplement_over=over,
                           lift=admitted_lift, lift_over=lift_over)
            return _decision(
                "selected", None, roots_all[0],
                tuple(index.subtree_of(roots_all[0]))[: limits.max_subtree_nodes],
                ranked, read=tuple(read),
                unread=unread_all[: limits.max_subtree_nodes],
                unread_total=len(unread_all), read_roots=roots_all,
                supplement_roots=admitted, supplement_status="fired",
                lift_roots=admitted_lift, lift_status=lift_status)

        if not usable:
            # 候选全落在显式跨引用来源上（或压根没有命中）：候选仍然可见（含舍弃原因），
            # 但**不读**——没有可命名的目标时不猜。
            #
            # `anp-7`：这一支是"零候选"形态的缺陷来源。有合格补读根时**转为 selected**
            # （读集只含补读根的子树，本层候选的舍弃原因一字不改地留着），没有时照旧
            # 回退，并把补读规则**为什么没做成**记在终态上（`no_candidate` 与
            # `body_measure_unavailable` 必须能分开）。
            reason = "explicit_cross_reference" if scored else "low_confidence"
            base_items = _merge(excluded[: limits.max_candidates], away)
            supp_roots, supp_status = _supplement_try()
            if supp_roots:
                decision = _supplement_selected(supp_roots, base_items)
                if decision is not None:
                    return decision
                supp_status = "over_budget"
            ranked = _rank(base_items, frozenset(), frozenset(), frozenset())
            return _decision("fallback", reason, None, (), ranked,
                             supplement_status=supp_status)

        scores = {item[1]: item[0] for item in usable}
        best = usable[0][0]
        if best < float(limits.min_score):
            # 低置信：把阈值之下的候选如实列出（可审计），但读集为空。
            # `anp-7`：低置信同样是缺陷来源——但补读**不**放宽 `min_score`，它换的是
            # 另一条判据（祖先键的按序近似形态 + 实质正文）；两者都判不出来才回退。
            base_items = _merge(usable[: limits.max_candidates], away)
            supp_roots, supp_status = _supplement_try()
            if supp_roots:
                decision = _supplement_selected(supp_roots, base_items)
                if decision is not None:
                    return decision
                supp_status = "over_budget"
            ranked = _rank(base_items, frozenset(), frozenset(), frozenset())
            return _decision("fallback", "low_confidence", None, (), ranked,
                             supplement_status=supp_status)

        # 近分带：同分或仅差一个最弱导航信号（简介命中）的候选一起进入有界考察。
        width = tie_band_width(present_total) if present_total > 0 else 0.0
        band_items = [item for item in usable
                      if item[0] >= best - width - 1e-9][: limits.max_candidates]
        band_roots = tuple(item[1] for item in band_items)

        # 读集 = 近分带各根**上提到最上层命中祖先**后子树的并集，按候选强度优先排序，
        # 由预算截断。上提回答的是"哪一层才是要读的那一节"：挂满正文的父章节本身也命中
        # 键时，只读它的某个子节点就会整段丢掉正文（`READ_ROOT_RULE_ID`）。
        #
        # 读根资格（`NAV_ROOT_ANCHOR_RULE_ID` + `NAV_ROOT_SUBJECT_RULE_ID`）判的是**上提后的
        # 读根**，不是近分带成员：真正被读的是读根的子树，而"某个只含片段词的子节点得分最高"
        # 恰恰是上提要修的形态（它上提之后就是那个持有正文的章节）。不合格的读根**整支丢弃**
        # 并逐条记下原因；一支都不剩时给 `no_anchored_read_root` 显式 fallback（读集为空），
        # 而不是退回去读那些不合格的根。祖先层不适用本判据（`corroborated is None`）。
        read_roots: list[str] = []
        for root in band_roots:
            node_id = _sufficient_root(index, scores, root)
            why = None if corroborated is None else disqualification.get(node_id)
            if why is not None:
                blocked[root] = why
                continue
            # 已被更强的根覆盖（同根、或落在已展开读根的子树里）：同一次考察不重复展开。
            if any(node_id == r or node_id in index.subtree_of(r) for r in read_roots):
                continue
            read_roots.append(node_id)
        if not read_roots:
            if corroborated is None:  # pragma: no cover - 祖先层不传资格集合
                raise NavigationError(
                    "祖先层不判读根资格：读根不可能全部被读根资格刷掉")
            # 有候选、也够分，但上提后没有任何一支读根能在本树里定位成"关于这一栏的那一节"：
            # 显式 fallback、读集为空。**不猜**，也不拿别的章节的材料顶上。
            #
            # `anp-7`：这一支是"读根全被资格刷掉"形态的缺陷来源（`scored` 非空却一支读根
            # 都不剩）。补读用的是**祖先键**，它本来就不要求本层键贴标签，因此能在这里
            # 补出真正那一节，而不是退回去读那些不合格的根。
            base_items = _merge(usable[: limits.max_candidates], away)
            supp_roots, supp_status = _supplement_try()
            if supp_roots:
                decision = _supplement_selected(supp_roots, base_items)
                if decision is not None:
                    return decision
                supp_status = "over_budget"
            ranked = _rank(base_items, frozenset(), frozenset(), frozenset())
            return _decision("fallback", "no_anchored_read_root", None, (), ranked,
                             supplement_status=supp_status)
        read, unread_all = _expand_roots(tuple(read_roots))

        # `anp-7` 主体补读：**选中了、但选错了栏目那一节**（读集里没有一节贴上祖先声明键）
        # 时，按祖先键补进"没读到的相关主体正文"。补读根排在**本层读根之后**展开，因此
        # 预算先满足本层；本层的读出结果（`selected` / `band` / `read_roots` 的前缀）
        # 一字不改，读集只做加法。
        #: 上提补读的**读基**：本层读根展开出来的那一份读集（`anp-7` 补读**之前**）。
        #: 它是"我们已经读到的那些节点"的忠实快照，上提只以它为起点，不把另一条规则的
        #: 结论二次利用成自己的前提。
        base_read = tuple(read)
        supp_roots, supp_status = _supplement_try(
            read=read, expanded=tuple(read) + tuple(unread_all))
        admitted: tuple[str, ...] = ()
        over: frozenset[str] = frozenset()
        if supp_roots:
            combined = tuple(read_roots) + tuple(supp_roots)
            read, unread_all = _expand_roots(combined)
            read_now = frozenset(read)
            admitted = tuple(r for r in supp_roots if r in read_now)
            over = frozenset(supp_roots) - read_now
            if admitted:
                read_roots = list(read_roots) + list(admitted)
            else:
                # 合格补读根存在、但预算被本层吃光：读集**保持不变**，状态如实记
                # `over_budget`——这既不是"没有候选"，也不是"补读成功"。
                supp_status = "over_budget"
                read, unread_all = _expand_roots(tuple(read_roots))

        # `anp-9` 上提补读：把已读节点**所属的那一节**读进来（`NAV_LIFT_RULE_ID`）。
        # 读基是**本层读根所展开的读集**（不含 `anp-7` 补进来的短节——那是它自己那一趟的
        # 结论，不由本规则二次利用），因此它就是"往上再看一层已读的东西"。
        # 展开顺序排在 `anp-7` 补读根**之后**：两条规则都只做加法，预算先满足更靠前的。
        lift_roots, lift_status = _lift_try(
            read=base_read, expanded=tuple(read) + tuple(unread_all))
        admitted_lift: tuple[str, ...] = ()
        lift_over: frozenset[str] = frozenset()
        if lift_roots:
            combined = tuple(read_roots) + tuple(lift_roots)
            read, unread_all = _expand_roots(combined)
            admitted_lift, lift_over = _admit(lift_roots, read)
            if admitted_lift:
                read_roots = list(read_roots) + list(admitted_lift)
            else:
                lift_status = "over_budget"
                read, unread_all = _expand_roots(tuple(read_roots))

        read_set = frozenset(read)
        unread = unread_all[: limits.max_subtree_nodes]
        selected = read_roots[0]
        subtree = tuple(index.subtree_of(selected))[: limits.max_subtree_nodes]

        rank_items = usable[: limits.max_candidates] + excluded[: limits.max_candidates]
        # 候选列表要能显示近分带根**和**它们上提后的读根：否则"哪些候选被考察"与"实际读了
        # 哪几棵子树"对不上号。上提出来的祖先若不在前 `max_candidates` 名里，补进来。
        extra = [item for item in usable
                 if item[1] in read_roots and item[1] not in {i[1] for i in rank_items}]
        # 补读根同样必须出现在候选列表里（否则读集无法解释），且带着它自己的依据；
        # 合格但没进读集的补读候选也留下——"补读没做成"必须是可审计的。
        extra = extra + [_supplement_row(node_id)
                         for node_id in (tuple(admitted) + tuple(sorted(over))
                                         + tuple(admitted_lift)
                                         + tuple(sorted(lift_over)))]
        ranked = _rank(_merge(tuple(rank_items) + tuple(extra), away), read_set,
                       frozenset(band_roots), frozenset(unread_all),
                       supplement=admitted, supplement_over=over,
                       lift=admitted_lift, lift_over=lift_over)
        return _decision("selected", None, selected, subtree, ranked,
                         band=band_roots, read=tuple(read), unread=unread,
                         unread_total=len(unread_all), read_roots=tuple(read_roots),
                         supplement_roots=admitted, supplement_status=supp_status,
                         lift_roots=admitted_lift, lift_status=lift_status)

    if not entry.nav_keys:
        return _decision("fallback", "no_navigation_keys", None, (), ())
    if index.unavailable_reason is not None:
        return _decision("fallback", "structurally_unavailable", None, (), ())

    scored: list[tuple[float, str, tuple[tuple[str, float], ...],
                       tuple[str, ...]]] = []
    for node_id in index.node_ids:
        score, breakdown, hits = score_node(index, entry.nav_keys, node_id)
        if score <= 0.0:
            continue
        scored.append((score, node_id, breakdown, hits))
    # 候选排序：得分降序；同分按**文档顺序**（先出现的优先）。完全确定性。
    scored.sort(key=lambda item: (-item[0], index.document_order(item[1]), item[1]))

    # 本层候选的**读根资格**（`NAV_ROOT_ANCHOR_RULE_ID` + `NAV_ROOT_SUBJECT_RULE_ID`）：
    # 只对"命中够分"的候选计算，逐候选判贴标签与主体邻接。未通过者在候选审计里留
    # `not_label_anchored`，不作读根。
    disqualification = {
        item[1]: read_root_disqualification(index, entry, item[1]) for item in scored}
    corroborated = frozenset(nid for nid, why in disqualification.items()
                             if why is None)

    # 父节点回退（`NAV_PARENT_RULE_ID`）：本层候选里**没有一个既整段命中、又通过读根资格**
    # 时，说明本层定位不到"关于这个子项的、可作读根的那一节"（没有独立标题、只有同词片段，
    # 或该章节与本 aspect 主体不相邻）。此时才改用 Contract 祖先声明派生的键，让真实父节点
    # 的有界正文可达——回退到的父章节子树**覆盖**子项，因此不丢料。祖先层未能 selected 就
    # **原样退回本层**（不猜、也不丢料）。
    strict_hits = frozenset(item[1] for item in scored
                            if segment_hits(index, entry.nav_keys, item[1]))
    if entry.parent_keys and not (strict_hits & corroborated):
        parent_present = tuple(k for k in entry.parent_keys
                               if index.key_has_segment_match(k))
        if parent_present:
            parent_scored: list[tuple[float, str, tuple[tuple[str, float], ...],
                                      tuple[str, ...]]] = []
            for node_id in index.node_ids:
                if not segment_hits(index, parent_present, node_id):
                    continue
                score, breakdown, hits = score_segment_node(
                    index, parent_present, node_id, parent_present)
                if score <= 0.0:
                    continue
                parent_scored.append((score, node_id, breakdown, hits))
            parent_scored.sort(
                key=lambda item: (-item[0], index.document_order(item[1]), item[1]))
            parent_decision = _pick(parent_scored, parent_present,
                                    segmented_away=scored, parent_tier=True)
            if parent_decision.status == "selected":
                return parent_decision

    return _pick(scored, index.keys_present(entry.nav_keys),
                 corroborated=corroborated)


# ---------------------------------------------------------------------------
# 跨源导航（§L2）：N 个索引 + 带文档身份的决策
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class SourceSetNavigationDecision:
    """一条跨源导航结论：**在哪一份文档**上得出的 `NavigationDecision`。

    `document_key` 与 `decision.document_key` 必须逐字相同（构造期校验）：前者是这条结论的
    归属，后者是结论自带的身份，两者不一致就是分派错配。
    """

    document_key: dict
    decision: NavigationDecision

    def __post_init__(self) -> None:
        if not isinstance(self.document_key, dict) or not self.document_key:
            raise NavigationError("跨源导航结论必须携带非空 document_key")
        if self.decision.document_key != self.document_key:
            raise NavigationError(
                f"跨源导航结论的归属 {self.document_key!r} 与决策自带的身份 "
                f"{self.decision.document_key!r} 不一致")

    def to_dict(self) -> dict:
        return {"document_key": dict(self.document_key),
                "decision": self.decision.to_dict()}


class SourceSetNavigationIndex:
    """有序源集上的**N 个只读导航索引**（`§L2.3`）。

    每个索引仍是「一棵树 + 该树简介」的单文档粒度（`NavigationIndex` 不改）；跨源只是
    「有几个索引」变了，**导航规则一个字节都没变**——`PROFILE_RULE_VERSION` /
    `NAV_KEY_RULE_ID` / `SCORE_WEIGHT_*` 都**不升版**（`§L2.2`）。

    **关键裁决（禁止项，`§L2.5`）**：跨文档的「较新优先」**不进入导航打分**。导航只回答
    「这个 aspect 在这份文档的这棵树上是哪个节点」；选哪份文档的材料是**研究/材料层**的
    决定。把「新」混进标题相似度会让「较新」污染导航键匹配——那是 O-12 与「导航只导航、
    不作证据」两条同时禁止的。
    """

    __slots__ = ("_indexes",)

    def __init__(self, indexes) -> None:
        """入参是**有序的** `(document_key_dict, NavigationIndex)` 对（至少一对）。

        为什么不是 `dict[dict, NavigationIndex]`：文档键是 dict，而 dict 不可哈希，用 dict
        当键在 Python 里**根本构造不出来**。要求「有序对」同时也把「序位是语义」写进签名：
        逐份台账按登记顺序读，调用方不得靠 dict 的偶然迭代序。
        """
        if isinstance(indexes, Mapping):
            # 兼容以可哈希键登记的调用方：键若是文档键对象，取其 `to_dict()` 形。
            items = [((k.to_dict() if hasattr(k, "to_dict") else k), v)
                     for k, v in indexes.items()]
        elif isinstance(indexes, (list, tuple)):
            items = list(indexes)
        else:
            raise NavigationError(
                "SourceSetNavigationIndex 需要有序的 (document_key, NavigationIndex) 对序列，"
                f"得到 {type(indexes).__name__}")
        if not items:
            raise NavigationError(
                "SourceSetNavigationIndex 需要至少一份文档的导航索引（空集无从跨源）")
        ordered: dict = {}
        for item in items:
            if not isinstance(item, (list, tuple)) or len(item) != 2:
                raise NavigationError(
                    f"跨源导航索引的每一项必须是 (document_key, NavigationIndex)，得到 {item!r}")
            key, index = item
            if not isinstance(key, dict) or not key:
                raise NavigationError("跨源导航索引的键必须是非空 document_key dict")
            if not isinstance(index, NavigationIndex):
                raise NavigationError(
                    f"跨源导航索引的值必须是 NavigationIndex，得到 {type(index).__name__}")
            marker = tuple(sorted(key.items()))
            if marker in ordered:
                raise NavigationError(f"跨源导航索引含重复文档键：{key!r}")
            ordered[marker] = (key, index)
        self._indexes = ordered
        # 主体一致性：整份源集只能是同一主体的材料（fail-closed，不取多数票）。
        subjects = {str(key.get("company_id")) for key, _ in ordered.values()}
        if len(subjects) != 1:
            raise NavigationError(
                f"跨源导航索引必须同属一个主体，得到 {sorted(subjects)}")

    def __len__(self) -> int:
        return len(self._indexes)

    def document_keys(self) -> tuple[dict, ...]:
        return tuple(key for key, _ in self._indexes.values())

    def index_for(self, document_key: dict) -> NavigationIndex | None:
        """**精确查表，无回退**：源集里没有这份文档键就返回 `None`。"""
        marker = tuple(sorted(document_key.items()))
        entry = self._indexes.get(marker)
        return None if entry is None else entry[1]

    def navigate_all(self, entry: AspectNavigationEntry, *,
                     profile: AspectNavigationProfile,
                     limits: NavigationLimits | None = None
                     ) -> tuple[SourceSetNavigationDecision, ...]:
        """在**每一份**源集文档上分别导航，返回逐文档的决策（顺序 = 索引登记顺序）。

        逐份都跑，**不**在"已经有一份命中"时提前返回：提前返回会把「只在某一份上有材料」
        变成「其他份不必看」，而"哪一份有材料"正是本层**无权**回答的问题（§L2.5）。
        """
        out: list[SourceSetNavigationDecision] = []
        for document_key, index in self._indexes.values():
            decision = navigate(index, entry, profile=profile, limits=limits)
            # 跨源决策**必须**带文档身份；单文档 `navigate` 不填，故在这里补上。
            if decision.document_key != document_key:
                decision = dataclasses.replace(
                    decision, document_key=dict(document_key))
            out.append(SourceSetNavigationDecision(
                document_key=dict(document_key), decision=decision))
        return tuple(out)


__all__ = [
    "DISCARD_REASONS", "FALLBACK_REASONS", "NAV_FORM_DEFAULT",
    "NAV_FORM_RULES", "NAV_FORM_RULES_BY_KIND", "NAV_FORM_RULES_BY_ROLE",
    "NAV_FORM_RULES_BY_TIER", "NAV_FORM_RULE_ID", "NAV_KEY_RULE_ID",
    "NAV_KEY_TIER_ANCESTOR", "NAV_KEY_TIER_DECLARED",
    "NAV_PARENT_RULE_ID", "NAV_ROOT_ANCHOR_RULE_ID", "NAV_ROOT_SUBJECT_RULE_ID",
    "NAV_SEGMENT_RULE_ID", "NAV_SIBLING_ITEM_RULE_ID",
    "NAV_SUBFORM_RULE_ID", "NAV_SUPPLEMENT_RULE_ID", "NAV_LIFT_RULE_ID",
    "SUPPLEMENT_STATUSES",
    "NAV_TOPIC_SEGMENT_RULE_ID", "READ_ROOT_RULE_ID", "NavigationCandidate",
    "NavigationDecision", "NavigationError", "NavigationIndex", "NavigationLimits",
    "SCORE_WEIGHT_PATH", "SCORE_WEIGHT_SYNOPSIS", "SCORE_WEIGHT_TITLE",
    "TIE_BAND_RULE_ID", "TOPIC_SEGMENT_ASSIGN_REASONS",
    "TOPIC_SEGMENT_ASSIGN_TIERS", "TopicSegmentAssignment",
    "SourceSetNavigationDecision", "SourceSetNavigationIndex",
    "aspect_nav_keys", "build_navigation_profile",
    "contract_ancestor_inputs", "contract_ancestor_labels",
    "contract_sibling_keys", "contract_topic_segment_assignments",
    "decision_statuses", "form_rule", "longest_common_substring_length",
    "is_ordered_variant", "is_read_root_corroborated", "label_anchored_keys",
    "navigate", "normalize_navigation_text", "ordered_variant_length",
    "parent_nav_keys", "question_sibling_keys", "read_root_disqualification",
    "score_node", "subject_body_supplement", "subject_section_lift",
    "subject_adjacency",
    "score_segment_node", "segment_hits", "subform_keys", "tie_band_width",
    "title_label_segments", "topic_segment_assignments",
]

"""§0.20 第二步（前半）：逐句**底线核对**（`sc-2` / `scp-2`）——不调模型。

本模块只回答一个问题：**这句话有没有一处可以被机械证明站不住的地方？** 它**不**回答
「这句话说得对不对」——那是随后那次独立只读审阅的活。两者不能互相顶替：

* 机械核对的结论永远是「可机械证明的底线过了 / 没过」。它**没有**资格说「语义已被支持」，
  因此 :class:`SentenceCheckReport` 里有一条 `semantic_support_claimed` 硬约束（恒为假），
  把这句话变成结构上不可越过的：想宣称语义支持，只能另立 wire，不能顺手改这个布尔；
* 审阅的结论永远是「意见」，不是判决：它不改稿、不联网、不覆盖硬错误（见 `cited_review`）。

## 判什么（每条都是**可复算**的，逐条给出判据本身）

| 轴 | 判据 | 不看什么 |
|---|---|---|
| 引用真实且属于本次输入 | 引用的键必须是本次输入清单里的键（`citation_present` / `citation_identity`） | 键存不存在之外的任何事 |
| 定位可回查 | 材料的 locator 必须有归属载体；事实必须至少有 locator / material_id / payload_ref 之一（`financial_pack` 一支按该权威自己的坐标：容器 + fact_id + 引用锚点身份） | 定位是否**正确**（那要读原文，不在此层） |
| 数字 / 否定 / 时点 / 主体有合法来源 | :func:`NS.high_risk_surface_tokens` 的四类高风险表面，逐个必须在**被引用的**来源文本里逐字出现 | 语义是否相近（本层只做字面核验） |
| 金额 / 比率必须够格 | 金额与比率**不**因「在被引的普通材料原文里逐字出现」而获得授权：必须落到一条合格事实（路径 A），或所引表材料里**某一格**（行列标签、原值、格级 locator、单位口径齐备） | 不含单位的普通数量（台 / 项 / 次…）原样走上一轴 |
| 旧材料不得写成当前状态 | 支撑文档角色全落在 `CANNOT_ESTABLISH_CURRENT_STATE_ROLES` 且句子无期间限定 ⇒ 硬错误 | 句子写得漂不漂亮 |
| 勾选 / 模板文字不得冒充公司事实 | 本句解析出的引用**全是**非叙述形态的材料、且没有任何合格事实 ⇒ 硬错误 | 那些文字本身在不在原文里（在，也不作数） |
| 表数字必须逐格可核 | 表材料里的数字只能经**格**（`cells`）取得授权，且该格必须给出业务行标签、指标列标签与**格级 locator** | 压平文本里出现过这个数字（出现过不构成授权） |
| 引用必须**归属本栏目** | 本小节**声明**的 Contract 栏目，必须在所引来源**登记归属**的栏目集合里（材料看 Pack 侧 `material_dispositions.aspect_ids`，事实看它自己的 `aspect_ids`） | 引用是否在场、字面是否逐字相同（那两件事已由前两轴判过，本轴不重复判） |

**「归属本栏目」这一轴的适用性单独一条**（`manifest_has_aspect_axis`）：本节这次输入里
**没有任何来源**被登记到任何栏目时，本轴记「不适用」，**不**判硬错误。理由是这条链上
**没有**这条轴，而不是「全错栏」——`pack_writer.scan_financial` 明确不为财务 / 附注 / 外部
快照编造 aspect 级归属（那一支的覆盖由「选中事实 + 权威缺口」表达）。把「没有轴」判成
「全错栏」是一道假门：它会让一节的每一句都硬错误，却指向一个不存在的问题。反过来，
topic Pack 那一支只要**有一条**材料被认领到栏目，本轴就逐句照判，一个字不放宽。

「不适用」**不是**一种结论，因此它在记录上有一根自己的轴：`SentenceCheckRecord.applicable`
（`sc-4`）。`pass` 里因此混着两类含义相反的东西——「判过并通过」与「这次没判」；只按
`verdict` 收句的读者会在「本节根本没有栏目登记轴」的那一支上把每一句都收成「本栏目已被
覆盖」。两个空读数（未计入覆盖 / 已判过并通过）**同时为空**时，必须先看
`sentences_with_aspect_axis_not_applicable()` 才能说这一节是「都对上了」还是「没判过」。

## 三条刻意的边界（写下来是为了让它们**不可**被顺手放宽）

1. **两套授权池**：数字与否定标记只认**来源**（被引材料的原文 + 被引事实的命题文本）；
   时点与主体另认**报告框架**（小节标题、Contract 逐字要求文本）。理由：要求文本是**要求**，
   不是证据——它能授权「报告期」这个报告自己的期间概念，不能授权任何一个数字或一句否定。
2. **表材料不进「普通来源池」**：一条表的压平正文里当然出现过它的数字，若让它进池，任何表
   数字都会自证合法。表数字必须经格级证明那一支，两支**互不顶替**（§0.19：表的阅读资格与
   它的数字的格级权威是两条分别验证的事）。
3. **金额与比率另有一道更严的门，且这道门不吃「原文里出现过」**：一道数字表面「有来源」
   与它「够格写进正文」是两件事。`m*` 材料是**普通材料**，它的原文里出现过某个金额，只证明
   这句话不是凭空造的；它**不**证明那个金额的主体、指标、期间、单位与口径对得上。因此
   :data:`QUALIFIED_ONLY_NUMERIC_SUFFIXES` 那一类表面必须另外拿到**合格事实**或**表里的一格**
   才放行（`numeric_qualification` 轴）。没有单位的普通数量（「共 3 项」）仍走上一轴。
   这道门**只**在「上一轴已经通过」时才报——来源都还没有的数字由上一轴负责，不两处判。
4. **一处错误不清零整节**：核对按句产出记录，句子之间彼此独立；报告给出每句自己的结论，
   可读的句子**照旧可读**（渲染成「不可发布」预览是 `cited_report` 的事）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from harness import numeric_disclosure as ND
from sections import cited_writer as CW
from sections import narrative_schema as NS
from sections import source_role_scope as SRS

__all__ = [
    "SENTENCE_CHECK_SCHEMA_VERSION",
    "SENTENCE_CHECK_POLICY_VERSION",
    "FAMILY_SCOPED_BLOCKING_POLICY_VERSIONS",
    "CHECK_KINDS",
    "FAILURE_REASON_BY_CHECK",
    "FAILURE_REASONS",
    "CHECK_FAMILIES",
    "CHECK_FAMILY_BY_KIND",
    "CRITERIA_DIAGNOSTIC_FAMILY",
    "family_of_check_kind",
    "family_of_failure_reason",
    "VERDICTS",
    "NUMERIC_BASES",
    "QUALIFIED_NUMERIC_BASES",
    "QUALIFIED_ONLY_NUMERIC_SUFFIXES",
    "NON_NARRATIVE_CONTENT_KINDS",
    "TABLE_CELLS_KEY",
    "TABLE_CELL_FIELDS",
    "PUBLISHABILITY_NOT_PUBLISHABLE",
    "SentenceCheckError",
    "SentenceCheckRecord",
    "SentenceState",
    "SentenceCheckReport",
    "table_cells",
    "table_cell_matches",
    "table_declared_unit",
    "document_roles",
    "manifest_has_aspect_axis",
    "registered_aspects",
    "is_qualified_numeric_surface",
    "fact_numeric_writability",
    "check_sentence",
    "check_cited_prose",
]

#: 核对记录 wire 版本。
#:
#: `sc-2`：新增 `numeric_qualification` 轴。轴名是记录上的**闭集取值**，多一个取值就是
#: 记录值域的扩张，因此 wire 与 policy 一起升版（`sc-1` ⊂ `sc-2`：`sc-2` 的每条记录仍是
#: `sc-1` 的形状，只是多了这一种轴）。
#:
#: `sc-3`：新增 `aspect_attribution` 轴——本句**引的**材料 / 事实，在 Pack / 权威侧**登记归属**
#: 的栏目里，有没有本小节**声明**的那一个（`cwm-3` 的 `declared_aspect_id` 与 `aspect_ids`）。
#: 同一次升版：`sc-2` ⊂ `sc-3`，旧记录的形状一字未改。
#:
#: `sc-4`：记录新增 `applicable`，把 `pass` 里混着的两类东西**在值域上**分开——「这一轴判过
#: 并通过」与「这一轴这次不适用」。`sc-3` ⊂ `sc-4`：旧记录的形状一字未改，只是多了一根
#: 此前**无从表达**的轴。这次升版是 §10 那组读数逼出来的：`sentences_with_declared_aspect_
#: registered()` 原先按 `verdict == "pass"` 收句，于是「本节根本没有栏目登记轴」的那一支
#: 会被收成「本栏目已被覆盖」——与它在另一侧「不在这里（没有被判过）」的自述自相矛盾。
#:
#: `sc-5`：小节声明从**单数**变**集合**（`cwm-6` 的 `declared_aspect_ids`），并新增段级
#: 归属复核（同一条 `aspect_attribution` 轴内的第二个条件，不新增轴取值）。记录形状一字未改。
#:
#: `sc-7`：新增 `display_role` 轴（**取值的扩张**，因此 wire 与 policy 一起升版）。它判
#: 「正文句有没有引到**诊断槽位**的事实」：`presentation_routing` 里
#: `contract_display_tier == "diagnostic_only"` 的栏目（冻结 Contract 的
#: `fin_solvency.interest_expense_proxy` 一类）按 `DESIGN_V2.md` §0.21 只进诊断槽位，
#: **不得**出现在普通正文里。判据只读清单自己的呈现层声明，不读事实的 `aspect_ids`。
#: 同一次升版把 :func:`NS._literal_token` 的**严格排版空白等价**带进 `numeric_surface` 轴
#: （见 `scp-6`）。`sc-6` ⊂ `sc-7`：旧记录形状一字未改，只是多了一根此前无从表达的轴。
SENTENCE_CHECK_SCHEMA_VERSION = "sc-7"
#: 核对政策版本（判据集合、两套授权池的划分、表格的字段契约，任一变化都必须升版）。
#:
#: `scp-2`：金额与比率两类表面不再由「在被引普通材料原文里逐字出现」单独授权
#: （见 :data:`QUALIFIED_ONLY_NUMERIC_SUFFIXES` 与模块头边界 3）。
#:
#: `scp-3`：新增栏目归属判据——「引用存在、字面逐字相同」**不再**被读成「本栏目已被覆盖」。
#: 只**增加**一条判据，不**放宽**任何旧判据。
#: `scp-4`：新增**呈现层**栏目归属判据（`presentation_column_attribution`）。它与 `scp-3` 的
#: 登记归属轴**并列而不合并**：财务那一支的权威事实 `aspect_ids` 恒为空（`scan_financial` 不
#: 编造 aspect 状态），因此 `scp-3` 的轴在那里恒不适用；把呈现层的路由声明塞进那一条轴，等于
#: 让「权威自己认领了这一栏」与「呈现层声明它属于这一栏」在读者面长得一样。只**增加**一条轴，
#: 不**放宽**任何旧判据。
#:
#: `scp-5`：轴 7 的判据由「声明的那**一个**栏目 ∈ 登记归属」改为「声明的栏目**集合**与登记
#: 归属**有交集**」，并加一条段级复核（段声明的 `aspect_ids` 也要与所引来源的登记归属有交集）；
#: 轴 8 由「路由集合**恰好等于**那一个栏目」改为「路由集合**包含于**声明集合」。这三处**同时**
#: 放宽与收紧：放宽的是「一个写作小节可以覆盖多个 Contract 栏目」（冻结 WritingSpec 本来就把
#: 18 个 `company_business*` aspect 映到同一个 `co-h4`），收紧的是「段自己承诺服务的那几栏必须
#: 真的由所引来源撑起来」。不许串栏这一点**一处未松**：路由集合只要有一栏在声明集合外，轴 8
#: 照旧硬错误。
#:
#: `scp-6`：两处**方向相反**的改动，各自单独记账。
#:
#: * **并入一条等价**（`numeric_surface` 轴）：`NS._literal_token` 从「只折叠空白」改为
#:   「删千分位与**全部空白**」，于是「数字与其单位之间的排版空白」这一类差异（来源
#:   `24 家` / 正文 `24家`、来源 `1,700 万` / 正文 `1,700万`）落进**严格排版空白等价**，
#:   不再记 `unsourced_number_surface`。等价面**有界**：token 形状由 `NS._NUMERIC_TOKEN_RE`
#:   限死为 `<数字><至多一个空白><可选单位>`，删空白只可能消掉数字与单位之间那一个空格——
#:   数值 / 符号 / 单位 / 尾零一个都不派生（`-9.94`≠`9.94`，`1.6`≠`1.60`，
#:   `129,641,258千`≠`129,641,258千元`）。**数字权威规则一条未松**：去空白后仍找不到的数字
#:   照样硬错。
#: * **新增一条轴**（`display_role`）：正文句引到**诊断槽位**事实（呈现层声明
#:   `contract_display_tier == "diagnostic_only"` 的栏目）⇒ 硬错 `diagnostic_fact_in_body`。
#:   这是 `DESIGN_V2.md` §0.21 展示角色边界的确定性落点，也是「代理口径只进诊断槽位」这句话
#:   在机械层唯一可回查的形式。只**增加**一条轴。
#:
#: `scp-7`：两处**收紧**，都不新增轴取值，也不放宽任何旧判据。
#:
#: * **`template_text` 轴多认一条可证形状**（:func:`_is_non_narrative_by_shape`）：除了
#:   Researcher 侧分类器判出的 `content_kind`，「勾选块（`适用`/`不适用` 紧挨勾选字形）
#:   **且**通篇无句末标点」也算非叙述内容。判据只读材料自己的 `reading_view`，
#:   **不**回写 `classify_span_content`（分类器读法进材料 payload 哈希，改它等于换材料身份）。
#: * **`current_state_scope` 轴改判绝对期间**：`SRS.unqualified_current_state` 从
#:   `srsc-3` 升到 `srsc-4`，只写 ``报告期`` 不再豁免「只由较旧来源支撑」的断言
#:   （相对限定词不携带年份，配较旧文档读出来的仍是当前态）。
#:
#: `scp-8`：**收窄**一处——`subject_surface` 轴的主体名由 `NS.entity_name_tokens` 抽取，而
#: 后者的剥前缀随 `nrules-17` 改为**不动点**。判据实现（`_uncovered` 的裸子串比对）一字未改，
#: 变的只是**判定集**：像 `截至报告期末公司` 这种「时间状语粘进主体名」的伪 token 不再产出，
#: 原先由它凭空吃掉的一条 `unsourced_subject_surface` 随之消失。**不放宽任何旧判据**：
#: 来源里确实没有的公司名照旧硬错（正反例见 `evals/test_m930_3_subject_and_report_date.py`）。
#:
#: `scp-9`（同批）：`scp-8` 的收窄**漏了一格**——`报告期末本公司…` 的「本公司」被后缀匹配
#: 切开，产出伪主体名 `末本公司`（成因见 `nrules-18`）。判据实现（`_uncovered` 的裸子串比对）
#: 仍一字未改，变的还是**判定集**：这一形状不再产出伪 token，原先由它凭空吃掉的
#: `unsourced_subject_surface` 随之消失。**不放宽任何旧判据**：来源里确实没有的主体名照旧硬错。
#:
#: `scp-10`：**判据一条未改，改的是聚合口径**——把「阻断句」从「全部硬错句的并集」收窄为
#: **事实安全族**的硬错句（:data:`CHECK_FAMILIES` 的第一族）。要动的只有两处**派生读数**：
#: :attr:`SentenceCheckReport.blocked_sentence_ids` 与
#: :attr:`SentenceCheckReport.blocked_sentence_count`（以及由前者派生的
#: :attr:`SentenceCheckReport.mechanical_verdict`）。理由与本批的三条边界：
#:
#: * **为什么不改判据**：`column_coverage` 族的两条轴（`aspect_attribution` /
#:   `presentation_column_attribution`）判的从来不是「原文是假的」，而是「这一句服务的是
#:   **别的一栏**」。把两族并进同一个 `blocked_sentence_ids`，读者面就只剩一个「正文有硬错」
#:   的数——`sections/cited_report.py` 的读侧分族表正是为了把它拆开而存在，但**系统侧**
#:   （返修目标集、控制器的放行硬门、预览的阻断句读数）一直在用那个并集，因此分族只在读回里
#:   发生，系统行为没变。本版把这条合并**从系统侧也拆开**。
#: * **不放开任何旧门**：`publishability` 恒 `not_publishable` 一字未动；栏目覆盖失败的句子
#:   **照旧**不计入所声明栏目的覆盖（:meth:`~SentenceCheckReport.sentences_without_aspect_registration`
#:   与 :meth:`~SentenceCheckReport.sentences_with_presentation_column_mismatch` 两条读数
#:   一字未改），因此它**不**让 Contract 的那一栏凭空变成「已答」；它该触发的**内容缺口**
#:   仍由 `sections/cited_report.py` 的缺口机制承接，并由既有的 `required_gap_retention`
#:   硬门阻断放行。
#: * **旧产物照旧可读**：`scp-10` 之前写成的报告按**它自己声明的** `policy_version` 解码，
#:   仍走「全族并集」的旧口径（见 :data:`FAMILY_SCOPED_BLOCKING_POLICY_VERSIONS`），
#:   于是盘上历史的 `mechanical_state` / `blocking_sentence_count` 与重算值**逐字仍相等**。
#: `scp-11`（2026-10-04）：**金额／比率的「合格事实」授权从逐字改成语义配对**。
#:
#: 起因是纵链实证的假绿：`_num_ok(token, fact_pool)` 只问「被引事实的文本里逐字出现过这个
#: token」，于是同数字不同年（`2023` 的原值写成 `2025`）、同数字不同业务（储能的原值挂到
#: 动力电池上）、把收入金额当收入占比，都能凭「数字长得一样」拿到 `qualified_fact` 授权 ——
#: 这条轴自己的文案`与所写含义相符的合格事实`当时并没有判据支撑。本版把该支改成：token 的
#: **句子侧绑定**（`harness/numeric_disclosure.py::bind_numbers`）必须与**事实侧绑定**四轴
#: 一致（期间／指标字段／单位类／业务作用域），才给授权。
#:
#: 边界（**只收紧**）：
#:   * 任一侧给不出**完整**绑定（期间读不出、作用域太短、指标窗里没有本栏目字段）⇒ **回落**
#:     旧判据（逐字出现即授权），不新造硬错；
#:   * 表数字那一支（格级授权）一字未改，仍与事实支互不顶替；
#:   * 覆盖面由 `ND.ASPECT_COVERAGE` 声明（本版只有 `company_business_main.revenue_breakdown`），
#:     表外栏目的事实支语义配对恒回落 ⇒ 行为不变；
#:   * 旧产物按**自己声明的** `policy_version` 解码（见 :data:`FAMILY_SCOPED_BLOCKING_POLICY_VERSIONS`）。
#:
#: `scp-12`（2026-10-04，M930-3 `ndc-2` 批）：事实侧那一半从**文本反推**改成**以事实自己声明的
#: 逐值身份为准**（`CitedFactEntry.value_identity`，`nd-2` 起由分业务营收事实携带）。
#:
#: 起因是同一条纵链的另一处假绿：事实的 `text` 是从年份头到本值的**逐字前缀**，2025 那条事实
#: 的文本里因此带着 2023/2024 的值，`bind_numbers(fact.text)` 会把前值也读成本事实的绑定，
#: 于是 2025 的事实可以授权 2023 的句子。本版对**声明了**身份的事实只认声明：读不出完整声明、
#: 或声明的值不是这个 token、或四轴对不上 ⇒ **不授权**（且**不**回落文本反推，落 `mismatch`）；
#: 没有声明身份的事实（财务／附注／外部与旧夹具）逐条走原判据，**行为一字未变**。
#: `scp-13`（2026-10-04，M930-3 `ndc-3` 批）：**声明了身份的事实不得再走回落**，且**已声明的
#: 占比事实在分母可核之前一律不授权**。
#:
#: 起因是 `scp-12` 留下的两条相邻假绿（都用真实 run 的 `f05`/`f09` 复现过）：
#:
#:   * **回落跑在声明之前**：`_fact_numeric_authorization` 里两条「任一侧读不出完整绑定 ⇒
#:     `AUTHORIZED`」的分支写在声明循环**之前**，于是句子侧一读不出指标窗（`metric_field=''`），
#:     一条**声明了**身份的事实照样放行任何落在它文本里的金额／比率 —— 声明的价值在这一支上
#:     被整段跳过。本版把「这批被引事实里有没有一条**声明了**身份、且逐字含这个 token 的」判在
#:     两个回落之前：**只有**金额／比率（`is_qualified_numeric_surface`）在那种情形下改成不可授权
#:     （`unverifiable`），年份等期间 token 仍照旧回落（它的授权来自事实的期间文本，不由四轴配，
#:     这一支**行为一字未变**）。
#:   * **分母无轴可核**：占比事实的四轴（期间／指标／单位类／业务作用域）全等时，
#:     `binding_compatible` 就只能拿「同为百分比」当配对成功。逐值身份里**没有分母的位置**
#:     （`metric` 是封闭词表标签、`scope` 是**分子**业务），本版因此**不**用文本反推补分母，而是
#:     把**已声明的占比事实**保守地判成不可授权（`denominator_unverified`）：不是判它写错，是判
#:     **核不了**。要让「分母写对即通过」成立，须给 `ValueIdentity` / `NumberBinding` 各加一轴并
#:     改抽取（新增能力），另行裁决。
#:
#: 两个新态都落在**同一个**轴（`numeric_qualification`）与**同一个**既有原因码
#: （`numeric_basis_not_qualified`）上：不新增失败原因码、不新增记录字段、不动 wire；两者的区别
#: 只进 `detail`（人读可辨）。没有 `value_identity` 的既有事实行（财务／附注／外部与旧夹具）
#: **行为一字未变**。
#:
#: `ndc-4`（2026-10-04，M930-3 `ndc-4` 批）：**判据一个字没改，版本不动**。本批只把上一条里
#: 「已声明的占比事实一律不授权」这一处判定抽成**事实级**函数
#: :func:`fact_numeric_writability`，让**请求面**（`sections/cited_writer.py` 派生
#: `writable_fact_keys` / `numeric_authorization`）与**逐句核对**读**同一处实现**。动因是请求面
#: 与核对口径不一致：`ndc3` 离线 run 的请求面上 12 条事实（9 金额 + 3 占比）在
#: `citable_fact_keys` 里长得一模一样，营收栏目标又要求「有合格事实就写占比」，而逐句核对对
#: 占比句一律判 `denominator_unverified` 硬错——请求面承诺的东西正是核对拒绝的东西。抽成共享
#: 函数后，「哪条事实本版不可授权」在两侧不可能分家。`_fact_numeric_authorization` 的返回值
#: **逐字不变**（纯重构），历史 `sentence_checks.json` 重算逐条零差异。
SENTENCE_CHECK_POLICY_VERSION = "scp-13"

#: 采用「阻断句 = 事实安全族」这一**分族聚合口径**的政策版本（封闭集合）。
#:
#: 这张表存在的唯一理由是**让旧产物按它写时的口径解码**：`sections/cited_report.py` 与
#: `assurance/cited_controller.py` 会用一份落盘报告**自己声明的** `policy_version` 落在这里与
#: 否，决定「阻断句」取并集还是取事实安全族。**新增一版必须在此登记**（否则新写的报告会被
#: 当成旧口径读，`evals/test_m930_3_sentence_check.py` §F 里有一条钉子钉住「当前常量 ∈ 本表」）。
FAMILY_SCOPED_BLOCKING_POLICY_VERSIONS = frozenset({"scp-10", "scp-11", "scp-12", "scp-13"})

#: 「机械核对不得宣称语义已被支持」——本批正文只有这一档，没有第二档。
PUBLISHABILITY_NOT_PUBLISHABLE = "not_publishable"

#: 核对轴的封闭集合。
CHECK_KINDS = (
    #: 这句话有没有引用。
    "citation_present",
    #: 引用的键在不在本次输入清单里（解析期已拦一次；本层对落盘读回的草稿再判一次）。
    "citation_identity",
    #: 被引的每一条，读者能不能按它的定位回查回去。
    "citation_locator",
    #: 数字表面（金额/占比/年份/编号…）的来源。
    "numeric_surface",
    #: 金额 / 比率这两类表面的**格级或事实级**资格（`scp-2` 起；见模块头边界 3）。
    "numeric_qualification",
    #: 显式否定与状态标记的来源。
    "negation_surface",
    #: 时点（含糊期间与绝对年份）的来源。
    "period_surface",
    #: 主体身份（机构/项目名一类的实体名 token）的来源。
    "subject_surface",
    #: 旧来源是否被写成了当前状态。
    "current_state_scope",
    #: 支撑文档的来源角色读不读得到（读不到就是映射不完整，fail-closed，不猜）。
    "source_role",
    #: 勾选 / 模板 / 版式碎片是否被当成公司事实在写。
    "template_text",
    #: 表数字有没有格级来源（该格必须带 row/column locator）。
    "table_cell_provenance",
    #: 该格的业务行标签是否落在句子里。
    "table_row_label",
    #: 该格的指标列标签是否落在句子里。
    "table_column_header",
    #: 表的单位 / 期间 / 口径声明在不在（不在就无从核对口径）。
    "table_declaration",
    #: 本句引的来源**登记归属**的栏目，有没有本小节**声明**的那一个（`scp-3`）。
    "aspect_attribution",
    #: 本句引的事实经**呈现层路由声明**落到的栏目，是不是本小节**声明**的那一个（`scp-4`）。
    "presentation_column_attribution",
    #: 本句引的事实里有没有落进**诊断槽位**的（`display_tier=diagnostic_only` ⇒ 只进诊断，
    #: 不得进普通正文；`scp-6`）。
    "display_role",
)

#: 轴 → 该轴**唯一**的失败原因码（一对一，防止「原因码挂错轴」这种无法回查的写法）。
FAILURE_REASON_BY_CHECK: dict[str, str] = {
    "citation_present": "uncited_sentence",
    "citation_identity": "citation_not_in_input",
    "citation_locator": "citation_locator_unretrievable",
    "numeric_surface": "unsourced_number_surface",
    "numeric_qualification": "numeric_basis_not_qualified",
    "negation_surface": "unsourced_negation_surface",
    "period_surface": "unsourced_period_surface",
    "subject_surface": "unsourced_subject_surface",
    "current_state_scope": "history_material_as_current_state",
    "source_role": "source_role_unreadable",
    "template_text": "template_text_as_company_fact",
    "table_cell_provenance": "table_cell_provenance_unavailable",
    "table_row_label": "table_row_label_missing",
    "table_column_header": "table_column_header_missing",
    "table_declaration": "table_declaration_unavailable",
    "aspect_attribution": "sentence_aspect_not_registered",
    "presentation_column_attribution": "sentence_presentation_column_mismatch",
    "display_role": "diagnostic_fact_in_body",
}

#: 全部失败原因码（= 上表的值域；单独导出，便于报告侧穷举）。
FAILURE_REASONS = tuple(FAILURE_REASON_BY_CHECK[k] for k in CHECK_KINDS)

#: 轴 → **报告族**（`scp-8` 起）。这一层**不改任何判据**：它只是把已经封闭的轴词表按
#: 「这一条失败说的是哪一种问题」再分一次类，好让读者面能把三类**分开计数**而不是并成一个
#: 「正文有问题」：
#:
#:   * `fact_safety` —— **事实安全**失败：这一句的字可能完全对，但它的**来源**撑不住它
#:     （数字无资格、期间/主体/否定无来源、历史材料当当前态、模板文字当公司事实、表数字
#:     无格级来源、只进诊断的事实写进了正文…）。这一族**必须**按硬错阻断。
#:   * `column_coverage` —— **栏目覆盖**失败：字与来源都成立，错在「它服务的是**别的一栏**」
#:     （`aspect_attribution`／`presentation_column_attribution`）。这一族的处置是**补一次
#:     正确的取材 / 改栏**，**不是**「原文事实是假的」——把它和上一族并成一个数字，正是
#:     本批要拆开的那件事（它可能意味着 Contract 的那一点尚未回答，但**绝不**冒充数字编造）。
#:   * `criteria_diagnostic`（:data:`CRITERIA_DIAGNOSTIC_FAMILY`）—— **不是一族失败**，
#:     而是「这一轴这次**没判**」（`SentenceCheckRecord.applicable is False`：本句没有数字、
#:     没有引用表材料、本节没有栏目登记轴…）。它既不通也不过，读回把它单列，两个相反的
#:     读数都不许把它算成自己那一侧的证据。
#:
#: 这张表与 :data:`CHECK_KINDS` **同源**：`scp-8` 起
#: `evals/test_m930_3_sentence_check.py` 穷举断言它恰好覆盖全部轴（缺一条即在测试里红），
#: 因此新增轴时不会静默落进「无族」。
CHECK_FAMILIES = ("fact_safety", "column_coverage")

#: 「这一轴这次没判」那一档的族名。它**不在** :data:`CHECK_FAMILIES` 里：后者是**失败**的
#: 族，前者是**非结论**的档，两者混进同一个元组就会让「族计数之和 = 硬错数」不再成立。
CRITERIA_DIAGNOSTIC_FAMILY = "criteria_diagnostic"

CHECK_FAMILY_BY_KIND: dict[str, str] = {
    # —— 事实安全 ——（来源/资格/命题要素这一边）
    "citation_present": "fact_safety",
    "citation_identity": "fact_safety",
    "citation_locator": "fact_safety",
    "numeric_surface": "fact_safety",
    "numeric_qualification": "fact_safety",
    "negation_surface": "fact_safety",
    "period_surface": "fact_safety",
    "subject_surface": "fact_safety",
    "current_state_scope": "fact_safety",
    "source_role": "fact_safety",
    "template_text": "fact_safety",
    "table_cell_provenance": "fact_safety",
    "table_row_label": "fact_safety",
    "table_column_header": "fact_safety",
    "table_declaration": "fact_safety",
    "display_role": "fact_safety",
    # —— 栏目覆盖 ——（字与来源都成立，错在「服务的是哪一栏」）
    "aspect_attribution": "column_coverage",
    "presentation_column_attribution": "column_coverage",
}

_REASON_FAMILY_BY_CHECK = FAILURE_REASON_BY_CHECK


def family_of_check_kind(check_kind: str) -> str:
    """轴 → 报告族；未知轴**抛错**，不猜一个族（分叉必须当场可见）。

    这里刻意与 :func:`failure_reason_label` 的处理**不同**：那个函数面对表外的原因码原样
    返回（把码本身印给读者看），因为它印的是**文本**；本函数返回的是**计数用的族**——猜错
    一族会让「事实安全 3 条」这个数字当场变成谎话，因此宁可让调用方红。
    """
    try:
        return CHECK_FAMILY_BY_KIND[str(check_kind)]
    except KeyError as exc:
        raise SentenceCheckError(
            f"check_kind={check_kind!r} 没有报告族（CHECK_FAMILY_BY_KIND 与 CHECK_KINDS 分叉了）"
        ) from exc


def family_of_failure_reason(reason: str) -> str:
    """失败原因码 → 报告族（读者面只拿得到原因码时的入口，如 `cited_preview.md` 的行）。

    原因码与轴一对一（:data:`FAILURE_REASON_BY_CHECK`），所以这条查表是良定义的；表外的码
    按「事实安全」处理而不是抛错——它必然来自一次分叉，而**分叉时宁可多报一条事实安全失败，
    也不得把它静默归到不阻断的那一族里**。
    """
    key = str(reason or "")
    for check_kind, reason_code in _REASON_FAMILY_BY_CHECK.items():
        if reason_code == key:
            return CHECK_FAMILY_BY_KIND[check_kind]
    return "fact_safety"

#: 「只进诊断槽位」的展示档取值（与冻结 Contract 的 `DISPLAY_TIERS` 同名同义）。正文句引到
#: 这一档栏目的事实即硬错 `diagnostic_fact_in_body`：这一档按 `DESIGN_V2.md` §0.21 只进
#: 附录 / 脚注 / 诊断表，它的**数值本身**可以照常出现在诊断槽位里，但不得被读成正文结论。
#: 取值本身在 :data:`CW.DISPLAY_TIER_DIAGNOSTIC_ONLY` 定义**一次**，这里只取别名——两处各
#: 写一遍字面量，请求面与核对面迟早会分叉到两个不同的字符串上。
DIAGNOSTIC_DISPLAY_TIER = CW.DISPLAY_TIER_DIAGNOSTIC_ONLY

#: 一句的机械结论。**没有**第三档：本层不给「部分可读」这类程度判断。
VERDICTS = ("pass", "hard_error")

#: 本句数字的**来源类型**（首个命中的那一档；按 `NUMERIC_BASES` 的顺序判）。
#:
#: `table_cell_source` 这一档是**表内数字**的合法出处，它与「合格事实」**不是**同一件事：
#: 格级证明说的是「这个数字出自这张表的这一格，行列与格级位置都在」，它**不**把该数字升格成
#: 一条 `Fact`，也不给它任何权威身份。谁要把表内数字升格为合格事实，只能走路径 A 预验证
#: （触及冻结 Contract / SourcePolicy，须单独裁决），本模块不做、也不宣称做过。
NUMERIC_BASES = ("qualified_fact", "material_verbatim_text", "table_cell_source")

#: 上面的三个来源类型里，**够格**支撑金额与比率的是哪两个。
#:
#: `material_verbatim_text` 被刻意排除：它是「这句话不是凭空造的」这一层的证明，不是
#: 「这个金额说的是这件事、这个期间、这个口径」那一层的证明。业务表数字的常见缺陷形状
#: 正是「逐字出自某份普通材料的压平正文」——它在旧判据下畅通无阻。
QUALIFIED_NUMERIC_BASES = ("qualified_fact", "table_cell_source")

#: 需要**格级或合格事实**才能授权的数字表面后缀（`DESIGN_V2.md` §0.20）。
#:
#: 两类：
#:
#: * **金额**——`…元`（含 `亿元/万元/千元/百元`）。词表按**最长优先**匹配；
#: * **比率**——`个百分点` / `%` / `‰`。
#:
#: 末尾那个裸缩放词（`亿/万/千/百`）单列一档，理由见
#: :func:`_requires_qualified_numeric_basis`：`NS.scan_numeric_tokens` 会把「129,641,258
#: 千元」截成「129,641,258 千」，末尾的「元」**不在 token 里**，因此只凭 token 后缀分不出
#: 「…千元」（金额）与「…千台」（数量）。
QUALIFIED_ONLY_NUMERIC_SUFFIXES = (
    "个百分点", "亿元", "万元", "千元", "百元", "%", "‰", "元",
    "亿", "万", "千", "百",
)

#: 上面那一档里「只有确实接 `元` 才算金额」的裸缩放词。
BARE_SCALE_SUFFIXES = ("亿", "万", "千", "百")

#: **非叙述**内容形态：不是可读正文的东西。只有这些形态在场而没有任何合格事实时，
#: 「这句话是在拿模板文字冒充公司事实」才成立（反例方向：这些材料与一条叙述材料同时在场，
#: 本判据不响——那属于引用卫生，不是硬错误）。
NON_NARRATIVE_CONTENT_KINDS = ("selection_form", "isolated_heading", "layout_fragment")

#: 「适用 / 不适用」勾选块：一个勾选字形**紧挨**着那两个词之一。**只**认这个共现形状，
#: 不认单独的方框字符——一段正常正文里出现一个 `□` 是排版残迹，不构成一张表单行。
#:
#: 勾选字形取**闭集**：`□☐■√✓☑✗×` 与私用区 `U+E000–U+F8FF`（年报里 `☑` 常被排成私用区
#: 字形，`` 即其一）。私用区那一档必须按**码位区间**判，不能列字面值：同一份 PDF
#: 在不同字体下映射到不同私用码位，列字面值就只认得住这一份文档。
_SELECTION_GLYPH_CLASS = r"\u25a1\u2610\u25a0\u221a\u2713\u2611\u2717\u00d7\ue000-\uf8ff"
_SELECTION_BLOCK_RE = re.compile(
    rf"[{_SELECTION_GLYPH_CLASS}]\s*(?:适用|不适用)|(?:适用|不适用)\s*[{_SELECTION_GLYPH_CLASS}]")

#: 句末标点（与 `SRS._SENTENCE_DELIMITERS` 同一含义的**本地**副本，只用于下面这个判据）。
#: 判它不是「这句话写得完不完整」，而是「这份材料里有没有一句可读正文」——模板行与版式
#: 碎片都没有句末标点。
_SENTENCE_END_CHARS = frozenset("。！？；!?;")


def _is_non_narrative_by_shape(material: Any) -> bool:
    """这份材料**在核对面上**算不算非叙述内容（`scp-7` 的第二条路径）。

    两条路径任一成立即算：

    1. `content_qualification.kind` 落在 :data:`NON_NARRATIVE_CONTENT_KINDS`（Researcher 侧
       的分类器已经判过）；
    2. **可证的形状**：材料的 `reading_view` 里有一处「适用 / 不适用」勾选块
       （:data:`_SELECTION_BLOCK_RE`），且整份读视图**没有任何句末标点**。

    第 2 条为什么必要、为什么要写成这个样子：

    * 年报里的勾选行常常**带着尾巴**——`☑适用 □不适用 公司需遵守《…》…的披露要求 1）营业收入及
      营业成本整体情况`。Researcher 的分类器按「勾选行 + 尾串长度」判形态，尾串一长就退回
      `text`，于是这条模板行在核对面上长得和正文一样。`scp-7` 不追着改分类器，理由见下。
    * 判据只认**这一个共现形状**：有勾选块 **且** 通篇无句末标点。真正带正文的材料（例如
      `☑适用 □不适用 报告期内，公司销售境外的主要产品为电池系统…。公司境外收入…`）里面有
      句末标点，**不**落进来；只有勾选字形而通篇是正文的段（`□☑` 出现在现金流量说明里）
      没有勾选块，也不落进来。两个条件合起来在真实清单上只命中模板行与披露指引行。
    * 它**只**在核对面上生效，**不**回写 `classify_span_content`。分类器读法进材料 payload
      哈希（`harness/tree_materials.py` 的 `content_qualification`），改它等于换掉全部材料的
      身份——判据要修的是「这条模板行有没有被当事实写进正文」，不该顺手换材料身份。
    """
    if str(getattr(material, "content_kind", "") or "") in NON_NARRATIVE_CONTENT_KINDS:
        return True
    text = str(getattr(material, "reading_view", "") or "")
    if not text:
        return False
    if not _SELECTION_BLOCK_RE.search(text):
        return False
    return not any(char in _SENTENCE_END_CHARS for char in text)

#: 表材料结构化读视图里逐格列表的键，以及每一格**必须**携带的字段。
#:
#: 字段就是「逐项核对业务行、指标列、单位、口径、原值与格级来源」这句话的可执行形式：
#: `row_label` / `column_header` 是行与列，`value_text` 是原值，`cell_locator` 是格级位置
#: （`loc-1` 的 `table_cell` 变体，带 `table_ref` 与行列下标）。少任何一个 ⇒ 这一格**没有**
#: 格级来源，该数字不得按表授权。
TABLE_CELLS_KEY = "cells"

#: 每一格**自己**必须给出的原值 / 行 / 列（缺一即该格不合格）。
TABLE_CELL_FIELDS = ("row_index", "column_index", "row_label", "column_header",
                     "value_text")

#: 读视图里给出**格级定位的坐标**的两个键（`cell_owner` + `table_ref`）。
#:
#: 为什么坐标在图侧、locator 在写作侧：格级 locator 的 wire 词表（`loc-1` 的 `table_cell`
#: 变体）只存在于 :mod:`sections.narrative_schema`，而图侧（`harness.*`）不得反向 import
#: 写作侧，也不得自己抄一份词表——抄第二遍就是给同一件事准备两个名字。图侧因此只给出
#: **中立坐标**（谁拥有这一格、引用哪张表、第几行第几列），由本模块按自己的 wire 渲染；
#: 图侧若已经给出了完整的 `cell_locator`（历史 `tom-1` 形态），则按原样校验，不覆盖。
CELL_COORDINATE_KEYS = ("cell_owner", "table_ref")


class SentenceCheckError(Exception):
    """核对器自身的输入不一致（fail-closed）：草稿与清单对不上、记录字段自相矛盾。"""


# ---------------------------------------------------------------------------
# 表格
# ---------------------------------------------------------------------------

def table_cells(structured_view: Any) -> tuple[tuple[dict, ...], tuple[str, ...]]:
    """表材料的结构化读视图 → `(合格格, 缺陷名)`。

    「合格」= 该格**同时**给出业务行标签、指标列标签、非空原值与一格级 `loc-1[table_cell]`
    定位。缺项的格**不进**合格集，并在缺陷里留名：它可能是「这张表根本没到格里」（压平正文
    只有整行文本）或「某几格缺列标签」——两者都让该格的数字**没有**格级来源，但读者需要
    分清是哪一种，因此缺陷名把 `row_index` / `column_index` 一并带回。

    格级定位有两处**合法**来源，判据相同（都是一格 `loc-1[table_cell]`，都带表引用与行列
    下标），取哪一处由读视图自己的形态决定：

    * 格自己带 `cell_locator`（历史 `tom-1` 形态）⇒ 原样 :func:`NS.validate_locator`，
      **不**覆盖、不重渲染；
    * 格不带，但读视图在**视图层**给出中立坐标 `cell_owner` + `table_ref`（`gtm-1` 形态）
      ⇒ 本模块按自己的 wire 用 :func:`NS.table_cell_locator` 渲染出来。

    两种都缺 ⇒ 这一格没有格级位置，缺陷名逐字带回**缺的是哪一样**（`cell_locator` 或
    视图层坐标），因为「格自己没说位置」和「这张视图根本给不出位置」是不同的缺口。
    """
    if not isinstance(structured_view, Mapping):
        return (), ()
    raw = structured_view.get(TABLE_CELLS_KEY)
    if raw is None:
        return (), ("no_cells",)
    if not isinstance(raw, (list, tuple)):
        return (), ("cells_not_a_sequence",)
    coordinates = {name: str(structured_view.get(name) or "").strip()
                   for name in CELL_COORDINATE_KEYS}
    good: list[dict] = []
    bad: list[str] = []
    for index, cell in enumerate(raw):
        if not isinstance(cell, Mapping):
            bad.append(f"cell#{index}:not_a_mapping")
            continue
        cell_no = f"cell#{index}"
        missing = [name for name in TABLE_CELL_FIELDS
                   if name not in cell or cell.get(name) in (None, "")]
        if missing:
            bad.append(f"{cell_no}:missing={','.join(missing)}")
            continue
        for name in ("row_index", "column_index"):
            if not isinstance(cell.get(name), int) or isinstance(cell.get(name), bool) \
                    or int(cell.get(name)) < 0:
                bad.append(f"{cell_no}:{name}_not_a_nonnegative_int")
                break
        else:
            locator_ref = cell.get("cell_locator")
            if locator_ref in (None, ""):
                absent = [name for name in CELL_COORDINATE_KEYS if not coordinates[name]]
                if absent:
                    bad.append(f"{cell_no}:cell_locator_absent(view_missing="
                               f"{','.join(absent)})")
                    continue
                locator_ref = NS.table_cell_locator(
                    coordinates["cell_owner"], table_ref=coordinates["table_ref"],
                    row_index=int(cell["row_index"]),
                    column_index=int(cell["column_index"]))
            try:
                locator = NS.validate_locator(locator_ref, "table_cells")
            except NS.NarrativeSchemaError as exc:
                bad.append(f"{cell_no}:cell_locator_invalid({exc})")
                continue
            if not locator or locator.get("locator_kind") != "table_cell":
                bad.append(f"{cell_no}:cell_locator_not_table_cell")
                continue
            good.append({"row_index": int(cell["row_index"]),
                         "column_index": int(cell["column_index"]),
                         "row_label": str(cell["row_label"]),
                         "column_header": str(cell["column_header"]),
                         "value_text": str(cell["value_text"]),
                         "cell_locator": locator})
    return tuple(good), tuple(bad)


def _table_declarations(structured_view: Mapping | None) -> dict[str, str]:
    """表的单位 / 期间 / 口径声明（读不到就是空串——**不**默认成任何一侧）。"""
    view = structured_view if isinstance(structured_view, Mapping) else {}
    return {name: str(view.get(name, "") or "").strip()
            for name in ("unit", "period", "scope")}


def split_token_unit(token: str) -> tuple[str, str]:
    """把数字 token 拆成 `(原值, 单位)`。

    单位词表取自 `NS._NUMERIC_UNITS`——**同一份**词表，不另立一个：扫描器把单位算进 token
    （「单位必须一起出现」，`§十一`），而表把单位声明在表级、格值只写原值，所以「这个数字是
    哪一格的」必须先按同一份词表把单位拆下来。拆不出单位就是空串（不是猜一个单位）。
    """
    for unit in sorted(NS._NUMERIC_UNITS, key=len, reverse=True):
        if token.endswith(unit):
            core = token[: len(token) - len(unit)].rstrip()
            if core:
                return core, unit
    return token, ""


def _cell_matches(token: str, cell: Mapping, declared_unit: str) -> bool:
    """这个数字 token 是不是**这一格**的：原值对上，且单位也对上。

    两种合法写法，都要求逐字（只做千分位/空白的排版归一，不派生等价写法）：

    * 格值自带单位（`value_text` 就是「100.00 万元」）⇒ token 与格值逐字对上；
    * 表把单位声明在**表级**、格值只写原值 ⇒ token 拆出的单位必须等于该表声明的单位。

    第二种是本次真实业务表的样子（「单位：万元」写在表上、格里只有数字）。少了单位这一项，
    「原值与单位」就没被逐项核对——那正是本轴存在的原因。
    """
    if _num_ok(token, [cell["value_text"]]):
        return True
    core, unit = split_token_unit(token)
    if not core or not unit or not declared_unit:
        return False
    return (unit == declared_unit
            and _num_ok(core, [cell["value_text"]]))


#: :func:`_cell_matches` 的**公开窄口**。只读逐值台账（`sections/cited_value_trace.py`）必须与
#: 本轴**同一判据**地说清「这个数字是靠哪一格拿到授权的」。在别处复制一份判据迟早分叉：判据
#: 一改，台账解释的就不再是本轴了。行为一字未改，只是不再要求调用方越过下划线。
table_cell_matches = _cell_matches


def table_declared_unit(structured_view: Any) -> str:
    """表级单位声明（:func:`_table_declarations` 的公开窄口，读不到就是空串）。"""
    return _table_declarations(structured_view).get("unit", "")


# ---------------------------------------------------------------------------
# 记录
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SentenceCheckRecord:
    """一条核对记录：**一句 × 一个轴**。

    `verdict` 只有 `pass` / `hard_error` 两档；`failure_reason` 在 `pass` 时必须为空、在
    `hard_error` 时必须等于该轴在 :data:`FAILURE_REASON_BY_CHECK` 里登记的那一个——这条
    一对一约束使「原因码挂错轴」在构造期就不可表达。

    `applicable`（`sc-4` 起）与 `verdict` **正交**：它说的是「**这一轴这一次判了吗**」，不是
    「判出来对不对」。好几个轴在本句没有它要判的东西时记「不适用」（本句没有数字 / 没有表 /
    没有引用…），它们照旧产出一条 `pass` 记录以保持「一句 × 每轴恰好一条」这条不变量。于是
    `pass` 里混进了两类**含义完全不同**的东西：

      * **判过并通过**——这一轴看了，结论是通过；
      * **不适用**——这一轴这次没有看，因为链上**没有**它要看的那根轴。

    把两者当同一件事读，就同时犯两个方向相反的错误：一边把「没这条轴」读成「全判过」，
    另一边把「没这条轴」读成「全判错」。所以要读「通过意味着什么」的调用方**必须**先看
    `applicable`，而不是看 `verdict`。这个字段刻意不做成 `detail` 里的字样——判据落在
    **值域**上，才能被机械核对，也不会随文案改动而漂。
    """

    record_id: str
    sentence_id: str
    subsection_id: str
    paragraph_id: str
    check_kind: str
    verdict: str
    failure_reason: str = ""
    detail: str = ""
    surfaces: tuple[str, ...] = ()
    citation_keys: tuple[str, ...] = ()
    numeric_bases: tuple[str, ...] = ()
    #: 这一轴这一次**判了吗**（`sc-4` 起）。`False` 只与 `verdict="pass"` 同时出现：
    #: 不适用是「没判」，它不构成任何一种结论。
    applicable: bool = True

    def __post_init__(self) -> None:
        if self.check_kind not in CHECK_KINDS:
            raise SentenceCheckError(
                f"check_kind={self.check_kind!r} 不在封闭集合 {list(CHECK_KINDS)} 内")
        if self.verdict not in VERDICTS:
            raise SentenceCheckError(
                f"verdict={self.verdict!r} 不在 {list(VERDICTS)} 内"
                "（本层不给「部分可读」之类的第三档）")
        if not self.applicable and self.verdict != "pass":
            raise SentenceCheckError(
                "「不适用」不是「判出错了」：`applicable=False` 只能与 `verdict='pass'` 同时"
                f"出现（得到 verdict={self.verdict!r}）。一个轴要么**判过**并给出结论，"
                "要么**没判**——没有「没判但判错了」这种记录。")
        if self.verdict == "hard_error":
            expected = FAILURE_REASON_BY_CHECK[self.check_kind]
            if self.failure_reason != expected:
                raise SentenceCheckError(
                    f"轴 {self.check_kind!r} 的失败原因码必须是 {expected!r}，"
                    f"得到 {self.failure_reason!r}")
        elif self.failure_reason:
            raise SentenceCheckError(
                f"通过的记录不得带失败原因码（得到 {self.failure_reason!r}）")
        for name in ("sentence_id", "subsection_id", "paragraph_id"):
            if not str(getattr(self, name) or "").strip():
                raise SentenceCheckError(f"SentenceCheckRecord.{name} 必须非空")
        for name in ("surfaces", "citation_keys", "numeric_bases"):
            object.__setattr__(self, name, tuple(str(x) for x in (getattr(self, name) or ())))
        for basis in self.numeric_bases:
            if basis not in NUMERIC_BASES:
                raise SentenceCheckError(
                    f"numeric_bases 含未知来源类型 {basis!r}（词表 {list(NUMERIC_BASES)}）")

    @property
    def ok(self) -> bool:
        return self.verdict == "pass"

    def to_dict(self) -> dict:
        return {"record_id": self.record_id, "sentence_id": self.sentence_id,
                "subsection_id": self.subsection_id, "paragraph_id": self.paragraph_id,
                "check_kind": self.check_kind, "verdict": self.verdict,
                "failure_reason": self.failure_reason, "detail": self.detail,
                "surfaces": list(self.surfaces),
                "citation_keys": list(self.citation_keys),
                "numeric_bases": list(self.numeric_bases),
                "applicable": bool(self.applicable)}

    @classmethod
    def create(cls, *, sentence_id: str, subsection_id: str, paragraph_id: str,
               check_kind: str, verdict: str, failure_reason: str = "", detail: str = "",
               surfaces: Iterable[str] = (), citation_keys: Iterable[str] = (),
               numeric_bases: Iterable[str] = (),
               applicable: bool = True) -> "SentenceCheckRecord":
        record = cls(record_id="", sentence_id=sentence_id, subsection_id=subsection_id,
                     paragraph_id=paragraph_id, check_kind=check_kind, verdict=verdict,
                     failure_reason=failure_reason, detail=detail,
                     surfaces=tuple(surfaces or ()), citation_keys=tuple(citation_keys or ()),
                     numeric_bases=tuple(numeric_bases or ()), applicable=bool(applicable))
        return _with_id(record, NS.content_id("scr_", record.to_dict()))

    @classmethod
    def from_dict(cls, d: Any) -> "SentenceCheckRecord":
        d = NS._reject_unknown(d, set(cls.__dataclass_fields__), "SentenceCheckRecord")
        for name in ("surfaces", "citation_keys", "numeric_bases"):
            if not isinstance(d.get(name, ()), (list, tuple)):
                raise SentenceCheckError(f"SentenceCheckRecord.{name} 必须是数组")
        record = cls(**{**d, "surfaces": tuple(d.get("surfaces") or ()),
                        "citation_keys": tuple(d.get("citation_keys") or ()),
                        "numeric_bases": tuple(d.get("numeric_bases") or ())})
        expected = NS.content_id("scr_", {**record.to_dict(), "record_id": ""})
        if record.record_id != expected:
            raise SentenceCheckError(
                f"SentenceCheckRecord.record_id 与内容不符：声明 {record.record_id!r}，"
                f"应为 {expected!r}")
        return record


def _with_id(record: SentenceCheckRecord, record_id: str) -> SentenceCheckRecord:
    return SentenceCheckRecord(
        record_id=record_id, sentence_id=record.sentence_id,
        subsection_id=record.subsection_id, paragraph_id=record.paragraph_id,
        check_kind=record.check_kind, verdict=record.verdict,
        failure_reason=record.failure_reason, detail=record.detail,
        surfaces=record.surfaces, citation_keys=record.citation_keys,
        numeric_bases=record.numeric_bases, applicable=record.applicable)


@dataclass(frozen=True)
class SentenceState:
    """一句在机械层的结果汇总（供预览渲染按句标注）。"""

    sentence_id: str
    subsection_id: str
    paragraph_id: str
    verdict: str
    failure_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"sentence_id": self.sentence_id, "subsection_id": self.subsection_id,
                "paragraph_id": self.paragraph_id, "verdict": self.verdict,
                "failure_reasons": list(self.failure_reasons)}


@dataclass(frozen=True)
class SentenceCheckReport:
    """整节的机械核对报告（`sc-2`）。

    它的 `mechanical_verdict` 只说机械层的档位（有硬错误 / 只有栏目覆盖失败 / 无硬错误 /
    根本没有正文，`scp-10` 起），而 `publishability` 是**恒定的**「不可发布」：本批没有任何
    一条链能把它变成可发布——人工确认与正式阶段关闭是**另外**的事，且必须由人来做。
    `semantic_support_claimed` 恒为假，构造期强制。

    **两族的聚合口径（`scp-10`）**：`blocked_sentence_ids` 自 `scp-10` 起只收**事实安全族**
    的硬错句；栏目覆盖族的硬错句另由 :attr:`column_coverage_sentence_ids` 单独报出，
    **不**进阻断集，但**照旧**不算那一栏已被覆盖。两族相加 = 全部硬错句
    （:attr:`hard_error_sentence_ids`，与 `scp-10` 之前 `blocked_sentence_ids` 的口径逐字相同）。
    按本报告**自己声明的** `policy_version` 解码：`scp-10` 之前的报告仍取并集，因此历史产物
    的 `mechanical_verdict` / 阻断句数重算后一字不变。
    """

    report_id: str
    schema_version: str
    policy_version: str
    draft_id: str
    input_manifest_id: str
    dependency_fingerprint: str
    sentence_count: int
    records: tuple[SentenceCheckRecord, ...]
    publishability: str = PUBLISHABILITY_NOT_PUBLISHABLE
    semantic_support_claimed: bool = False

    def __post_init__(self) -> None:
        if self.schema_version != SENTENCE_CHECK_SCHEMA_VERSION:
            raise SentenceCheckError(
                f"schema_version 必须是 {SENTENCE_CHECK_SCHEMA_VERSION!r}，"
                f"得到 {self.schema_version!r}")
        if self.publishability != PUBLISHABILITY_NOT_PUBLISHABLE:
            raise SentenceCheckError(
                "机械核对的结论**不得**声称正文可发布：本批只有 "
                f"{PUBLISHABILITY_NOT_PUBLISHABLE!r} 这一档。"
                "（「可发布」是人工确认与正式阶段关闭的事，不是核对的产出）")
        if self.semantic_support_claimed is not False:
            raise SentenceCheckError(
                "机械核对**不得**宣称「语义已被支持」：它只判可机械证明的底线，"
                "语义意见属于随后那次独立只读审阅")
        for name in ("draft_id", "input_manifest_id", "dependency_fingerprint"):
            if not str(getattr(self, name) or "").strip():
                raise SentenceCheckError(f"SentenceCheckReport.{name} 必须非空")
        object.__setattr__(self, "records", tuple(self.records or ()))
        expected = _report_id(self.identity_body())
        if self.report_id != expected:
            raise SentenceCheckError(
                f"SentenceCheckReport.report_id 与内容不符：声明 {self.report_id!r}，"
                f"应为 {expected!r}")

    # -- 派生视图 ---------------------------------------------------------

    @property
    def uses_family_scoped_blocking(self) -> bool:
        """本报告是否按 `scp-10` 的**分族聚合口径**读（由它自己声明的 `policy_version` 决定）。

        旧产物（`scp-9` 及更早）落在 :data:`FAMILY_SCOPED_BLOCKING_POLICY_VERSIONS` 之外，
        于是「阻断句」仍是全族并集——**历史的阻断句数与 `mechanical_verdict` 重算后一字不变**。
        """
        return str(self.policy_version) in FAMILY_SCOPED_BLOCKING_POLICY_VERSIONS

    @property
    def hard_error_records(self) -> tuple[SentenceCheckRecord, ...]:
        return tuple(r for r in self.records if r.verdict == "hard_error")

    def hard_error_records_in_family(self, family: str) -> tuple[SentenceCheckRecord, ...]:
        """某一**报告族**的硬错记录（族由 :func:`family_of_check_kind` 判，未知轴当场抛错）。"""
        return tuple(r for r in self.hard_error_records
                     if family_of_check_kind(r.check_kind) == family)

    @property
    def hard_error_sentence_ids(self) -> tuple[str, ...]:
        """**全部**硬错句（两族并集）。它是 `scp-10` 之前 `blocked_sentence_ids` 的逐字口径，
        现在**只**作「机械层一共标了几句」这个总数用（审阅侧的 `hard_error_sentence_ids`
        旁挂、控制器的跨轴对账都按它比），**不**是放行阻断集。"""
        return tuple(dict.fromkeys(r.sentence_id for r in self.hard_error_records))

    @property
    def fact_safety_hard_error_sentence_ids(self) -> tuple[str, ...]:
        """事实安全族的硬错句（来源/资格撑不住这句话的那一族）。"""
        return tuple(dict.fromkeys(
            r.sentence_id for r in self.hard_error_records_in_family("fact_safety")))

    @property
    def column_coverage_sentence_ids(self) -> tuple[str, ...]:
        """栏目覆盖族的硬错句（字与来源都成立，错在**服务的是别的一栏**的那一族）。

        这些句子**不**进阻断集，也**不**被读成「原文事实是假的」；但它们**照旧不算**所声明
        栏目的覆盖（:meth:`sentences_without_aspect_registration` /
        :meth:`sentences_with_presentation_column_mismatch`），因此该栏若为 Contract 必需，
        缺口照旧成立、照旧由 `required_gap_retention` 硬门阻断放行。
        """
        return tuple(dict.fromkeys(
            r.sentence_id for r in self.hard_error_records_in_family("column_coverage")))

    @property
    def blocked_sentence_ids(self) -> tuple[str, ...]:
        """**阻断句**：`scp-10` 起只取事实安全族；`scp-10` 之前是两族并集（**改义，见版本注**）。

        它的读者面是「系统侧按哪几句阻断」（返修目标集、控制器硬门、预览阻断读数）。
        栏目覆盖族的句子自 `scp-10` 起落在 :attr:`column_coverage_sentence_ids` 里单独报出。
        """
        if self.uses_family_scoped_blocking:
            return self.fact_safety_hard_error_sentence_ids
        return self.hard_error_sentence_ids

    @property
    def blocked_sentence_count(self) -> int:
        return len(self.blocked_sentence_ids)

    @property
    def column_coverage_sentence_count(self) -> int:
        return len(self.column_coverage_sentence_ids)

    @property
    def mechanical_verdict(self) -> str:
        """机械层档位。`column_coverage_only` 一档是 `scp-10` **新增**的取值。

        它存在的唯一理由是**堵住一个假干净**：栏目覆盖失败如果被读成
        `no_hard_errors`，读者面就会把「这一栏还没答上」读成「这一节全清」。
        旧产物（`scp-10` 之前）**永远不会**取到这个值——它们的 `blocked_sentence_ids`
        仍含两族并集，非空即 `has_hard_errors`。
        """
        if self.sentence_count == 0:
            return "no_prose"
        if self.blocked_sentence_ids:
            return "has_hard_errors"
        if self.uses_family_scoped_blocking and self.column_coverage_sentence_ids:
            return "column_coverage_only"
        return "no_hard_errors"

    def records_for(self, sentence_id: str) -> tuple[SentenceCheckRecord, ...]:
        return tuple(r for r in self.records if r.sentence_id == sentence_id)

    def sentences_without_aspect_registration(self) -> tuple[str, ...]:
        """栏目归属轴上**硬错误**的句子（= 不算本栏目覆盖的那些）。

        它是读数，不是判决：这些句子**照旧可读**（§0.20 的「一处错误不清零整节」），它们只是
        **不被计入**所声明栏目的覆盖。把「写了 4 句」与「有 4 句算这一栏」分开，是这一条的全部
        意义。轴「不适用」而通过的句子**不**在这里——它没有被判过，不能被读成判过。
        """
        return tuple(dict.fromkeys(
            r.sentence_id for r in self.records
            if r.check_kind == "aspect_attribution" and r.verdict == "hard_error"))

    def sentences_with_declared_aspect_registered(self) -> tuple[str, ...]:
        """栏目归属轴上**真的判过并通过**的句子（= 计入本栏目覆盖的那些）。

        「判过」由 `applicable` 承担，不靠 `verdict`：`sc-3` 起本轴在不适用时也产出一条 `pass`
        记录（为守住「一句 × 每轴恰好一条」），只按 `verdict` 收句，会把「本节根本没有栏目
        登记轴」的那一支收成「本栏目已被覆盖」。这类句子的去向见
        :meth:`sentences_with_aspect_axis_not_applicable`——**不是**「通过了」。
        """
        return tuple(dict.fromkeys(
            r.sentence_id for r in self.records
            if r.check_kind == "aspect_attribution" and r.verdict == "pass"
            and r.applicable))

    def sentences_with_aspect_axis_not_applicable(self) -> tuple[str, ...]:
        """栏目归属轴**这一次没有判**的句子（= 两种空读数各自的第三种状态）。

        两个读数（:meth:`sentences_without_aspect_registration` /
        :meth:`sentences_with_declared_aspect_registered`）在「本轴不适用」的那一支上**同时
        为空**——而同时为空**不等于**「每一句都对上了」。这一支的句子既不是「错栏」，也不是
        「算本栏目覆盖」，它们只是**没被这条轴看过**。报告侧必须把这一支单独说出来：拿「未计入
        覆盖 = 空」当「栏目全对上」，就是把本函数要防的那个假门又装了回去。
        """
        return tuple(dict.fromkeys(
            r.sentence_id for r in self.records
            if r.check_kind == "aspect_attribution" and not r.applicable))

    def sentences_with_presentation_column_mismatch(self) -> tuple[str, ...]:
        """呈现层栏目归属轴上**硬错误**的句子（= 不算所声明栏目覆盖的那些）。

        与 :meth:`sentences_without_aspect_registration` 是**两条轴**上的读数，不可互推：财务
        那一支的登记归属轴恒不适用，本轴才是那一节真正判过的那一条。两处都为空的章节既不是
        「都对上了」也不是「全错栏」——那是「这一节根本没有这条轴」。
        """
        return tuple(dict.fromkeys(
            r.sentence_id for r in self.records
            if r.check_kind == "presentation_column_attribution"
            and r.verdict == "hard_error"))

    def sentences_with_declared_column_routed(self) -> tuple[str, ...]:
        """呈现层栏目归属轴上**真的判过并通过**的句子（= 所引事实确实落在本小节声明的栏目里）。

        「判过」由 `applicable` 承担：`scp-4` 起本轴在不适用时也产出一条 `pass` 记录（为守住
        「一句 × 每轴恰好一条」），只按 `verdict` 收句会把「本节根本没有这条轴」收成「都定对了」。
        """
        return tuple(dict.fromkeys(
            r.sentence_id for r in self.records
            if r.check_kind == "presentation_column_attribution" and r.verdict == "pass"
            and r.applicable))

    def sentences_with_presentation_axis_not_applicable(self) -> tuple[str, ...]:
        """呈现层栏目归属轴**这一次没有判**的句子（第三种状态，既非错栏也非通过）。"""
        return tuple(dict.fromkeys(
            r.sentence_id for r in self.records
            if r.check_kind == "presentation_column_attribution" and not r.applicable))

    def sentence_states(self) -> tuple[SentenceState, ...]:
        """按记录出现顺序逐句汇总（一句一条）。**可读的句子照旧可读**：硬错误只落在**那一句**。"""
        order: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        for record in self.records:
            if record.sentence_id in seen:
                continue
            seen.add(record.sentence_id)
            order.append((record.sentence_id, record.subsection_id, record.paragraph_id))
        states = []
        for sentence_id, subsection_id, paragraph_id in order:
            reasons = tuple(dict.fromkeys(
                r.failure_reason for r in self.records_for(sentence_id)
                if r.verdict == "hard_error"))
            states.append(SentenceState(
                sentence_id=sentence_id, subsection_id=subsection_id,
                paragraph_id=paragraph_id,
                verdict="hard_error" if reasons else "pass", failure_reasons=reasons))
        return tuple(states)

    def summary_rows(self) -> tuple[dict, ...]:
        """`(轴, 结论, 失败原因)` 的计数表（同一格子多次命中也只算一次是**逐句**计数）。"""
        counts: dict[tuple[str, str, str], int] = {}
        for record in self.records:
            key = (record.check_kind, record.verdict, record.failure_reason)
            counts[key] = counts.get(key, 0) + 1
        return tuple({"check_kind": kind, "verdict": verdict, "failure_reason": reason,
                      "sentences": counts[(kind, verdict, reason)]}
                     for kind, verdict, reason in sorted(counts))

    # -- 身份 -------------------------------------------------------------

    def identity_body(self) -> dict:
        return _report_identity_body(
            schema_version=self.schema_version, policy_version=self.policy_version,
            draft_id=self.draft_id, input_manifest_id=self.input_manifest_id,
            dependency_fingerprint=self.dependency_fingerprint,
            sentence_count=self.sentence_count, records=self.records)

    def fingerprint(self) -> str:
        return hashlib.sha256(
            NS.canonical_json(self.identity_body()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        # 派生读数（`scp-10`）**进 `to_dict` 但不进 `_report_identity_body`**：它们是读视图，
        # 改口径不该换掉一份历史报告的身份（报告自己声明的 `policy_version` 才是口径锚）。
        return {"report_id": self.report_id, "report_fingerprint": self.fingerprint(),
                "schema_version": self.schema_version, "policy_version": self.policy_version,
                "draft_id": self.draft_id, "input_manifest_id": self.input_manifest_id,
                "dependency_fingerprint": self.dependency_fingerprint,
                "sentence_count": self.sentence_count,
                "mechanical_verdict": self.mechanical_verdict,
                "blocked_sentence_ids": list(self.blocked_sentence_ids),
                "blocked_sentence_count": self.blocked_sentence_count,
                "hard_error_sentence_ids": list(self.hard_error_sentence_ids),
                "fact_safety_sentence_ids": list(self.fact_safety_hard_error_sentence_ids),
                "column_coverage_sentence_ids": list(self.column_coverage_sentence_ids),
                "column_coverage_sentence_count": self.column_coverage_sentence_count,
                "publishability": self.publishability,
                "semantic_support_claimed": self.semantic_support_claimed,
                "records": [r.to_dict() for r in self.records],
                "sentence_states": [s.to_dict() for s in self.sentence_states()]}

    @classmethod
    def create(cls, *, draft_id: str, input_manifest_id: str, dependency_fingerprint: str,
               sentence_count: int, records: Sequence[SentenceCheckRecord],
               policy_version: str = SENTENCE_CHECK_POLICY_VERSION) -> "SentenceCheckReport":
        records = tuple(records or ())
        body = _report_identity_body(
            schema_version=SENTENCE_CHECK_SCHEMA_VERSION, policy_version=policy_version,
            draft_id=draft_id, input_manifest_id=input_manifest_id,
            dependency_fingerprint=dependency_fingerprint, sentence_count=int(sentence_count),
            records=records)
        return cls(report_id=_report_id(body), schema_version=SENTENCE_CHECK_SCHEMA_VERSION,
                   policy_version=policy_version, draft_id=draft_id,
                   input_manifest_id=input_manifest_id,
                   dependency_fingerprint=dependency_fingerprint,
                   sentence_count=int(sentence_count), records=records)

    @classmethod
    def from_dict(cls, d: Any) -> "SentenceCheckReport":
        d = NS._reject_unknown(
            d, {"report_id", "report_fingerprint", "schema_version", "policy_version",
                "draft_id", "input_manifest_id", "dependency_fingerprint", "sentence_count",
                "mechanical_verdict", "publishability", "semantic_support_claimed",
                # `scp-10` 起的派生读数：解出来**一律按 `records` + 本报告声明的
                # `policy_version` 现算**，盘上那几个键只作可读性（旧产物没有它们也照解）。
                "blocked_sentence_ids", "blocked_sentence_count", "hard_error_sentence_ids",
                "fact_safety_sentence_ids", "column_coverage_sentence_ids",
                "column_coverage_sentence_count",
                "records", "sentence_states"}, "SentenceCheckReport")
        report = cls(
            report_id=str(d.get("report_id", "") or ""),
            schema_version=str(d.get("schema_version", "") or ""),
            policy_version=str(d.get("policy_version", "") or ""),
            draft_id=str(d.get("draft_id", "") or ""),
            input_manifest_id=str(d.get("input_manifest_id", "") or ""),
            dependency_fingerprint=str(d.get("dependency_fingerprint", "") or ""),
            sentence_count=int(d.get("sentence_count", 0) or 0),
            records=tuple(SentenceCheckRecord.from_dict(r) for r in (d.get("records") or ())),
            publishability=str(d.get("publishability", "") or ""),
            semantic_support_claimed=bool(d.get("semantic_support_claimed")))
        return report


def _report_identity_body(*, schema_version: str, policy_version: str, draft_id: str,
                          input_manifest_id: str, dependency_fingerprint: str,
                          sentence_count: int, records: Sequence[Any]) -> dict:
    """报告身份体（JSON-safe）：`create()` 与 `identity_body()` 的**同一**实现。"""
    return {
        "schema_version": schema_version, "policy_version": policy_version,
        "draft_id": draft_id, "input_manifest_id": input_manifest_id,
        "dependency_fingerprint": dependency_fingerprint,
        "sentence_count": int(sentence_count),
        "records": [r.to_dict() for r in records],
    }


def _report_id(body: Mapping[str, Any]) -> str:
    return NS.content_id("scg_", dict(body))


# ---------------------------------------------------------------------------
# 逐句核对
# ---------------------------------------------------------------------------

def _surfaces_by_axis(text: str) -> dict[str, tuple[str, ...]]:
    """四类高风险表面**分轴**取出（与 :func:`NS.high_risk_surface_tokens` 同一批原语）。

    同一批原语、同一顺序，只是**分了轴**：报告要能说清「是哪一类表面没有来源」，
    而不是把四类混成一个集合（`NS.unauthorized_surfaces` 给的正是那个并集，本模块与它
    **同判据**，测试里逐例对账，防止两处口径分叉）。
    """
    return {
        "numeric_surface": tuple(NS.scan_numeric_tokens(text)),
        "negation_surface": tuple(NS.marker_hits(text)),
        "period_surface": tuple(NS.vague_period_hits(text)),
        "subject_surface": tuple(NS.entity_name_tokens(text)),
    }


def _uncovered(surfaces: Sequence[str], authorized: Sequence[str]) -> tuple[str, ...]:
    """哪些表面**未**在被引来源里逐字出现（判据与 :func:`NS.unauthorized_surfaces` 逐字相同）。"""
    pool = "\n".join(str(t) for t in authorized if t)
    return tuple(token for token in surfaces if token not in pool)


def _num_ok(token: str, texts: Sequence[str]) -> bool:
    """数字 token 是否被这批**来源文本**逐字覆盖。

    两步都是仓内既有原语、缺一不可：先用 :func:`NS.authorized_numeric_tokens` 把来源**文本**
    扫成归一化 token 集合（它只接受权威侧字符串——拿正文自己当池会让任何数字自证合法），
    再用 :func:`NS.numeric_token_authorized` 逐字比对（只做千分位/空白的排版归一，不派生
    任何等价写法）。
    """
    return NS.numeric_token_authorized(token, NS.authorized_numeric_tokens(list(texts or ())))


def is_qualified_numeric_surface(token: str, text: str = "") -> bool:
    """这个数字 token 是不是**必须**拿到格级来源或合格事实才可授权的表面（金额 / 比率）。

    `token` 来自 :func:`NS.scan_numeric_tokens`（单位已被算进 token）。判据只看 token 的
    后缀，另加一处**文本级**复核：

    * 后缀命中 `个百分点` / `%` / `‰` / `亿元` / `万元` / `千元` / `百元` / `元` ⇒ 是；
    * 后缀只是裸缩放词 `亿` / `万` / `千` / `百` ⇒ **再看 `text` 里这个 token 后面紧不紧接着
      `元`**。扫描器把「129,641,258 千元」截成「129,641,258 千」（`千` 在单位词表里排在
      `元` 前面），所以「…千元」与「…千台」的 token 逐字相同。少了这一步，要么漏掉真实金额，
      要么把「10 万台」这类普通数量也一并拒掉——后者正是 §0.20 明确允许照常引用的一类。
    * `text` 传空串时按**保守**一侧判（裸缩放词算金额）：调用方拿不到句子原文时，宁可多判
      一道，不可静默放行。
    """
    body = str(token or "").rstrip()
    if not body:
        return False
    for suffix in QUALIFIED_ONLY_NUMERIC_SUFFIXES:
        if not body.endswith(suffix):
            continue
        if suffix in BARE_SCALE_SUFFIXES:
            if not text:
                return True
            #: token 自身带空白（「129,641,258 千」），允许它与后面的「元」之间也有空白。
            return re.search(re.escape(body) + r"\s?元", text) is not None
        return True
    return False


def _fact_texts(facts: Sequence[CW.CitedFactEntry]) -> list[str]:
    return [f.text for f in facts]


#: `_fact_numeric_authorization` 的五态（**封闭集合**）。
FACT_NUMERIC_ABSENT = "absent"        # 没有任何被引事实的文本含这个 token
FACT_NUMERIC_AUTHORIZED = "authorized"  # 语义配对通过，**或**（非金额/比率、或事实未声明）任一侧读不出完整绑定
FACT_NUMERIC_MISMATCH = "mismatch"    # 逐字在事实里，但那条事实的期间／指标／单位／业务对不上句意
#: `scp-13`：这批被引事实里**有**一条声明了逐值身份、且逐字含这个金额／比率 token，而句子侧
#: 给不出能与之配对的完整绑定 ⇒ **核不了**，不是「核过了」。不得回落放行。
FACT_NUMERIC_UNVERIFIABLE = "unverifiable"
#: `scp-13`：命中的是**已声明的占比事实**——四轴可以全等，但**分母**在现有身份字段里无处承载、
#: 传递与核验，「同为百分比」不能代替分母核验 ⇒ 一律不授权（保守留作缺口，等加轴后升格）。
FACT_NUMERIC_DENOMINATOR_UNVERIFIED = "denominator_unverified"


def fact_numeric_writability(fact: "CW.CitedFactEntry") -> str:
    """**事实级**读数：这一条被引事实在本版能不能作为正文数字授权（`scp-13`）。

    与 :func:`_fact_numeric_authorization`（句子级）是**同一套判据的同一处实现**：后者判的是
    「**这句话**写的这个数字有没有一条与句意相符的合格事实」，其中「这条事实本身就被本版的
    保守口径整条挡下」那一支在这里；句子的四轴配对（期间／指标／单位类／业务作用域）**不在**
    这里——那要看那句话怎么写。

    返回本模块的既有状态串，取值只有两个：

    * :data:`FACT_NUMERIC_AUTHORIZED`——没有被本版整条挡下（**没声明身份的事实**照旧走原判据，
      因此它们的既有行为一字未变；声明了身份的**金额**事实也在这里，它们仍要过句子侧四轴）；
    * :data:`FACT_NUMERIC_DENOMINATOR_UNVERIFIED`——**已声明的占比事实**：分母在现有身份字段里
      无处承载、传递与核验，「同为百分比」不能代替分母核验 ⇒ 一律不授权。

    **为什么要有这个函数**：`ndc-4` 之前，请求面（`cited_writer`）只有「Pack 侧登记到本栏的
    事实键」这一根轴，于是三条占比事实与九条金额事实在请求面上长得一模一样，而营收栏目标又
    要求「有合格事实就写占比」——请求面承诺的正是逐句核对拒绝的。把这条判定抽成共享函数之后，
    **请求面读到的就是判据自己的那份实现**，两侧不可能分家（没有第二份词表可以被悄悄改）。

    **它不授权任何东西**：返回 `AUTHORIZED` 只是说「这条事实没有被本版整条挡下」，它**不**代替
    句子侧的四轴配对，也**不**放宽第 4 条。调用方（请求面）应当只把**返回值读了它**的那些键
    摆成「本版可写」，而**不**改动任何登记轴。
    """
    declared = getattr(fact, "value_identity", None)
    if declared is None:
        return FACT_NUMERIC_AUTHORIZED
    if str(declared.get("value_kind") or "") == "ratio":
        return FACT_NUMERIC_DENOMINATOR_UNVERIFIED
    return FACT_NUMERIC_AUTHORIZED


def _fact_numeric_authorization(token: str, sentence_text: str,
                                facts: Sequence[CW.CitedFactEntry]) -> str:
    """`scp-12`：合格事实对**这个** token 的授权，要按**语义配对**判，不是逐字比对。

    `DESIGN_V2` §0.12 要求每个事实性原子由合格事实及其支撑边蕴含；`numeric_qualification`
    这一轴写的是「没有一条**与所写含义相符**的合格事实」——本函数就是那句话的判据。此前它
    只做了 `_num_ok`（逐字出现），于是同数字不同年、不同业务、金额当占比都能假绿。

    实现在 `harness/numeric_disclosure.py`（确定性、零 LLM；覆盖面由 `ND.ASPECT_COVERAGE`
    声明）。事实侧绑定有**两条腿**，先看声明：

    * 事实**声明了**逐值身份（`CitedFactEntry.value_identity`，`nd-2` 起由 §7.2c 铸的分业务
      营收事实携带）⇒ **只**用这条声明配对。这类事实的 `text` 是从年份头到本值的**逐字前缀**
      （2025 那条里因此带着 2023/2024 的值），从文本反推会把前值也读成本事实的。声明缺失／
      读不出完整绑定／四轴对不上 ⇒ 该事实**不授权**，**不得**回落文本反推；token 确实在它
      文本里却谁都不授权 ⇒ `mismatch`（`scp-11` 在这里会假绿）。
    * 事实**没有**声明身份（财务／附注／外部与旧夹具）⇒ 逐条走原来的文本反推，**任何一处读不出
      完整绑定都回落旧判据**——「读不出」不判，「读出来不一样」才判错。

    `scp-13` 收了两格（见模块头版本注记）：**已经声明了身份的事实不再享有回落**（句子侧读不出
    就是核不了 ⇒ `unverifiable`），且**已声明的占比事实一律不授权**（分母无轴可核 ⇒
    `denominator_unverified`）。两条都只对金额／比率这一类表面生效。
    """
    if not _num_ok(token, _fact_texts(facts)):
        return FACT_NUMERIC_ABSENT
    #: `scp-13`：**有**声明了身份、又逐字含这个 token 的事实 ⇒ 这一支的回落被取消。
    #: 只对金额／比率生效：年份等期间 token 也走下面两条回落，而它按设计不由四轴配对授权
    #: （授权来自事实的期间文本），`is_qualified_numeric_surface` 对它恒为假 ⇒ 行为不变。
    declaring = tuple(f for f in facts
                      if getattr(f, "value_identity", None) is not None
                      and _num_ok(token, [f.text]))
    strict = bool(declaring) and is_qualified_numeric_surface(token, sentence_text)
    sentence_bindings = tuple(b for b in ND.bind_numbers(sentence_text) if ND.complete(b))
    if not sentence_bindings:
        return FACT_NUMERIC_UNVERIFIABLE if strict else FACT_NUMERIC_AUTHORIZED
    side = tuple(b for b in sentence_bindings if ND.token_matches_binding(token, b))
    if not side:
        return FACT_NUMERIC_UNVERIFIABLE if strict else FACT_NUMERIC_AUTHORIZED
    saw_fact_binding = False
    saw_declared_fact = False
    for fact in facts:
        if not _num_ok(token, [fact.text]):
            continue
        declared = getattr(fact, "value_identity", None)
        if declared is not None:
            saw_declared_fact = True
            if fact_numeric_writability(fact) == FACT_NUMERIC_DENOMINATOR_UNVERIFIED:
                #: `scp-13`：占比事实的分母在这六个字段里没有位置。四轴全等也不授权——
                #: 「同为百分比」不是分母核验。保守留作缺口，等身份体加分母轴后才可能放行。
                #: （`ndc-4`：这一判定抽成 :func:`fact_numeric_writability`，与请求面同一实现。）
                continue
            fact_binding = ND.binding_from_declared(declared, str(getattr(fact, "period", "") or ""))
            if fact_binding is None or not ND.token_matches_binding(token, fact_binding):
                continue
            for sentence_binding in side:
                if ND.binding_compatible(sentence_binding, fact_binding):
                    return FACT_NUMERIC_AUTHORIZED
            continue
        for fact_binding in ND.bindings_for(ND.bind_numbers(fact.text), token):
            if not ND.complete(fact_binding):
                continue
            saw_fact_binding = True
            for sentence_binding in side:
                if ND.binding_compatible(sentence_binding, fact_binding):
                    return FACT_NUMERIC_AUTHORIZED
    if saw_declared_fact:
        #: 声明了身份的事实**已经正面表态**：这个 token 在它文本里，但它的身份不是这个数/
        #: 不是这一期/不是这个业务/不是这一类。「读不出」的回落不适用于它们。
        #: 占比那一支单独给一个状态：它不是「四轴不符」，而是「分母核不了」。
        if any(fact_numeric_writability(f) == FACT_NUMERIC_DENOMINATOR_UNVERIFIED
               and _num_ok(token, [f.text]) for f in facts):
            return FACT_NUMERIC_DENOMINATOR_UNVERIFIED
        return FACT_NUMERIC_MISMATCH
    if not saw_fact_binding:
        return FACT_NUMERIC_AUTHORIZED
    return FACT_NUMERIC_MISMATCH


def _non_table_views(materials: Sequence[CW.CitedMaterialEntry]) -> list[str]:
    return [m.reading_view for m in materials if m.structured_view is None]


def _table_views(materials: Sequence[CW.CitedMaterialEntry]) -> list[CW.CitedMaterialEntry]:
    return [m for m in materials if m.structured_view is not None]


def document_roles(manifest: CW.CitedWriterInputManifest) -> dict[str, str]:
    """清单里的 `{document_id: source_role}`——与 `srsc-1` 的台账**同一**读法（含同一 fail-closed）。

    同一 `document_id` 在清单里出现两个角色 ⇒ 当场抛：按哪一份都是猜。这条与
    :func:`SRS.document_roles` 的判据同源；清单本身是那次读取的产物，本模块不重新推导角色。
    """
    roles: dict[str, str] = {}
    for material in manifest.materials:
        if not material.document_id:
            continue
        role = str(material.source_role or "")
        known = roles.get(material.document_id)
        if known is not None and known != role:
            raise SentenceCheckError(
                f"同一 document_id={material.document_id!r} 在本次清单里角色不一致："
                f"{known!r} vs {role!r}（按哪一份都是猜，fail-closed）")
        roles[material.document_id] = role
    return roles


def manifest_has_aspect_axis(manifest: CW.CitedWriterInputManifest) -> bool:
    """本节这次输入里**有没有**「栏目登记」这条轴（= 至少一条来源被认领到某个栏目）。

    这一条存在的理由与 :func:`SRS.has_source_document_series` **同形**：不是每一支权威都有
    这条轴，而「这条轴上没有东西」与「有东西但都没对上」在产物上长得一模一样。混起来就会
    造出一道**假门**：

    * 财务 / 附注 / 外部快照那一支，`pack_writer.scan_financial` 明确**不编造** aspect 状态
      （财务 workflow 至今没有 aspect 级归属），因此它的事实行 `aspect_ids` 恒为空。若本轴
      照判，那一节的**每一句**都会因为「登记的栏目里没有它」而硬错误——那不是抓到了错栏，
      是把「没有这条轴」说成了「全错栏」；
    * topic Pack 那一支有这条轴：材料在 Pack 侧被认领到具体栏目（
      `ResearchMaterialDisposition.aspect_ids`）。那里「某句引的栏目没被认领」是真结论，
      必须判。

    因此判据的**适用性**单独成一条：只有在至少一条来源带非空 `aspect_ids` 时才判本轴；否则
    记「不适用」，并说明是**哪一支**没有这条轴。这是**收紧**而不是放宽：topic Pack 那一支
    照旧逐句判，一个字没变。
    """
    for material in manifest.materials:
        if material.aspect_ids:
            return True
    for fact in manifest.facts:
        if fact.aspect_ids:
            return True
    return False


def manifest_has_presentation_column_axis(manifest: CW.CitedWriterInputManifest) -> bool:
    """本节这次输入里**有没有**「呈现层路由」这条轴（= 至少一条事实被声明落到某一栏）。

    与 :func:`manifest_has_aspect_axis` 分开成两条函数，是因为它们是**两条不同的轴**：

    * 登记轴（`scp-3`）说的是「权威自己把这条来源认领到了某一栏」；
    * 呈现轴（`scp-4`）说的是「本次运行的**呈现层声明**把一个指标放到了某一栏」。

    财务那一支只有后者：`pack_writer.scan_financial` 明确不编造 aspect 状态，因此它的事实行
    `aspect_ids` 恒为空。本函数**只**读清单自己的 `presentation_routing`——它不去读事实行的
    `aspect_ids`，否则两轴又合成一条。
    """
    routing = getattr(manifest, "presentation_routing", None)
    if not isinstance(routing, Mapping):
        return False
    return bool(routing.get("fact_columns"))


def routed_columns(*, manifest: CW.CitedWriterInputManifest,
                   citation_keys: Sequence[str]) -> tuple[str, ...]:
    """这些引用键经**呈现层声明**落到的栏目（去重、保序）。

    只认事实行：呈现层路由声明说的是「指标事实出现在哪一栏」，材料行没有这条轴（材料本来
    就该按 Pack 侧登记归属，见 :func:`registered_aspects`）。查不到的引用键**不贡献任何栏目**
    ——那由 `citation_identity` 轴负责。
    """
    routing = getattr(manifest, "presentation_routing", None)
    if not isinstance(routing, Mapping):
        return ()
    by_fact_id: dict[str, str] = {}
    for row in (routing.get("fact_columns") or ()):
        if not isinstance(row, Mapping):
            continue
        fact_id = str(row.get("fact_id") or "")
        column = str(row.get("presentation_column") or "")
        if fact_id and column:
            by_fact_id[fact_id] = column
    found: list[str] = []
    for key in citation_keys:
        fact = manifest.fact_for_key(key)
        if fact is None:
            continue
        column = by_fact_id.get(str(fact.fact_id))
        if column:
            found.append(column)
    return tuple(dict.fromkeys(found))


def routed_column_tiers(*, manifest: CW.CitedWriterInputManifest,
                        citation_keys: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """这些引用键所引事实的**展示档**（`scp-6`）——返回 `(展示档集合, 查不到档的栏目)`。

    **唯一**实现是 :func:`CW.presentation_column_tiers`（两跳：`fact_columns` 定栏、
    `routes` 定档）。本函数只多做一件事：把**引用键**映成 `fact_id`。请求面（`cp-8` 给事实行
    打档位）与逐句核对（本轴判硬错）**必须**读同一份实现——两处各写一遍，接线迟早会对不上，
    而这条轴的全部价值就是「写作侧看到的档位」与「核对侧判的档位」是同一条声明。
    """
    fact_ids = []
    for key in citation_keys:
        fact = manifest.fact_for_key(key)
        if fact is not None:
            fact_ids.append(str(fact.fact_id))
    if not fact_ids:
        return (), ()
    return CW.presentation_column_tiers(
        presentation_routing=getattr(manifest, "presentation_routing", None),
        fact_ids=tuple(fact_ids))


def registered_aspects(*, manifest: CW.CitedWriterInputManifest,
                       citation_keys: Sequence[str]) -> tuple[str, ...]:
    """这些引用键在**登记侧**归属的全部栏目（去重、保序）。

    两条轴**合流在这里，只有这一处**：材料行的 `aspect_ids` 来自
    `TopicResearchPack.material_dispositions`（Pack 侧认领），事实行的 `aspect_ids` 来自权威
    事实自己的栏目字段。空引用键（在清单里查不到）**不贡献任何栏目**——它由
    `citation_identity` 轴负责，本函数不为它代言。

    返回空 tuple 是**结论**：这些引用在登记侧没有被认领到任何栏目。它不是「读不到」——
    读不到在清单构造期就 fail-closed 了（见 `build_cited_writer_input`）。
    """
    found: list[str] = []
    for key in citation_keys:
        material = manifest.material_for_key(key)
        if material is not None:
            found.extend(material.aspect_ids)
            continue
        fact = manifest.fact_for_key(key)
        if fact is not None:
            found.extend(fact.aspect_ids)
    return tuple(dict.fromkeys(found))


def check_sentence(*, sentence: CW.CitedSentence, subsection_id: str, paragraph_id: str,
                   manifest: CW.CitedWriterInputManifest,
                   roles: Mapping[str, str] | None = None,
                   declared_aspect_ids: Sequence[str] = (),
                   paragraph_aspect_ids: Sequence[str] = ()
                   ) -> tuple[SentenceCheckRecord, ...]:
    """对**一句**逐轴核对，返回该句的记录（顺序稳定）。

    `roles` 是整节共用的 `{document_id: source_role}`（由 :func:`document_roles` 一次算出、
    含同一 fail-closed）；不传就本句现算——两条路读的是**同一份清单**，没有第二个真值。

    `declared_aspect_ids` 是本小节**声明覆盖**的那些 Contract 栏目（`cwm-6`）。空集合是
    「调用方没有声明栏目」⇒ 栏目归属轴记「不适用」，**不**猜一栏出来。整节入口
    :func:`check_cited_prose` 永远从清单的 `CitedSubsectionSpec.declared_aspect_ids` 取，
    而那个字段在构造期就拒绝空值，因此「整节跑完却一栏都没判」在这条链上不可表达。

    `paragraph_aspect_ids` 是本句所在**段落**自己声明的服务栏目（`cw-4`，可为空）。它让这条轴
    在「一个小节覆盖十几栏」时仍有分辨力：小节集合太宽，段集合才是「这一段本来要写哪几栏」。
    空集合**不**放宽本条——它只是没有可判的段级声明，此时仍按小节集合判。
    """
    sid = sentence.sentence_id
    records: list[SentenceCheckRecord] = []
    if roles is None:
        roles = document_roles(manifest)

    def emit(check_kind: str, ok: bool, detail: str = "", surfaces: Iterable[str] = (),
             citation_keys: Iterable[str] = (), numeric_bases: Iterable[str] = (),
             applicable: bool = True) -> None:
        """产出一条记录。

        `applicable=False` 是**声明「这一轴这次没判」**，它只在 `ok=True` 时成立（构造期强制）：
        本句没有这一轴要判的东西时，照旧逐轴产出一条记录以维持「一句 × 每轴恰好一条」，
        但那一条说的是「不适用」，不是「通过」。读「通过意味着什么」的调用方必须先看
        `applicable`（见 :meth:`SentenceCheckReport.sentences_with_declared_aspect_registered`）。
        """
        records.append(SentenceCheckRecord.create(
            sentence_id=sid, subsection_id=subsection_id, paragraph_id=paragraph_id,
            check_kind=check_kind, verdict="pass" if ok else "hard_error",
            failure_reason="" if ok else FAILURE_REASON_BY_CHECK[check_kind],
            detail=detail, surfaces=surfaces, citation_keys=citation_keys,
            numeric_bases=numeric_bases, applicable=applicable))

    resolved_materials: list[CW.CitedMaterialEntry] = []
    resolved_facts: list[CW.CitedFactEntry] = []
    unresolved: list[str] = []
    for key in sentence.citations:
        material = manifest.material_for_key(key)
        if material is not None:
            resolved_materials.append(material)
            continue
        fact = manifest.fact_for_key(key)
        if fact is not None:
            resolved_facts.append(fact)
            continue
        unresolved.append(key)

    # ---- 轴 1：引用在场与属于本次输入 --------------------------------------
    emit("citation_present", bool(sentence.citations),
         detail=("" if sentence.citations else
                 "本句没有任何引用：读者无从回查它出自哪里"),
         citation_keys=sentence.citations)
    emit("citation_identity", not unresolved,
         detail=("" if not unresolved else
                 f"引用了本次输入清单里不存在的键：{unresolved}"),
         citation_keys=tuple(unresolved))

    # ---- 轴 2：定位可回查 --------------------------------------------------
    def _fact_locator_missing(fact: CW.CitedFactEntry) -> bool:
        """这条**权威事实**有没有可回查的定位——按它自己的 authority kind 判，不统一成一种。

        * `topic_pack` / `external_snapshot`：`loc-1` exact locator，或有 material 载体
          （带 payload 引用）。判据一字不变；
        * `evidence_note`：由它自己的 `loc-1` char_range 承担（附注的定位本来就是字符区间）；
        * `financial_pack`：结构化库**没有**字符区间，一条财务事实的定位是它自己的坐标
          `(artifact 容器身份, fact_id)` 加上该事实引用锚点的类型化身份
          （`financial_snapshot:<snapshot_id>`，由 `citation_source_identity` 从它自己的
          `CitationRef` 派生，未知 `ref_type` 时 fail-closed）。坐标与锚点身份三者齐备即
          「按该权威自己的坐标回查得到这一条事实」。

        新增的是**财务这一支**（此前这条链根本没有财务路径，因此没有任何既有句子因此改判）；
        `topic_pack`/`evidence_note`/`external_snapshot` 的判据**不得**为了凑齐一种写法而放宽——
        那会把「有正文、没有 locator」的材料放进来。
        """
        if fact.authority_kind == "financial_pack":
            return not (str(fact.container_identity or "").strip()
                        and str(fact.fact_id or "").strip()
                        and str(fact.source_identity or "").strip())
        return fact.locator_ref is None and not fact.material_id and not fact.payload_ref

    unreachable: list[str] = []
    for material in resolved_materials:
        if not str(material.locator_ref.get("owner", "") or "").strip():
            unreachable.append(material.citation_key)
    for fact in resolved_facts:
        if _fact_locator_missing(fact):
            unreachable.append(fact.citation_key)
    emit("citation_locator", not unreachable,
         detail=("" if not unreachable else
                 "这些引用没有任何可回查的定位（无载体 / 无 locator / 无 material anchor）："
                 f"{unreachable}"),
         citation_keys=tuple(unreachable) or sentence.citations)

    # ---- 轴 3：四类高风险表面的来源 ---------------------------------------
    cells_by_material: dict[str, tuple[dict, ...]] = {}
    cell_problems: dict[str, tuple[str, ...]] = {}
    declared_by_material: dict[str, dict[str, str]] = {}
    for material in _table_views(resolved_materials):
        cells, problems = table_cells(material.structured_view)
        cells_by_material[material.citation_key] = cells
        cell_problems[material.citation_key] = problems
        declared_by_material[material.citation_key] = _table_declarations(
            material.structured_view)

    fact_pool = _fact_texts(resolved_facts)
    material_pool = _non_table_views(resolved_materials)
    source_pool = fact_pool + material_pool
    framing_pool = _framing_pool(manifest, subsection_id)

    axes = _surfaces_by_axis(sentence.text)
    #: 表数字的授权**只能**经格：原值对上、单位对上（句子的 token 自带单位，表把它声明在
    #: 表级）。表材料**不**进普通来源池——否则压平正文里的数字会自证合法（模块头边界 2）。
    cell_hits: list[tuple[str, dict, str]] = []
    table_numbers: list[str] = []
    numeric_missing: list[str] = []
    #: 拿到「合格事实」或「表里的一格」之一的数字（`qualified_fact` / `table_cell_source`）。
    numeric_qualified: set[str] = set()
    #: 上一轴放过了、但**只**靠普通材料原文逐字出现拿到的金额 / 比率。这一支才是本次新增的
    #: 那一道门要拦的东西：`m*` 里出现过，不构成金额 / 比率的授权。
    numeric_unqualified: list[str] = []
    #: `scp-13`：上面那一支里属于「**分母核不了**」的那些（占比事实的既有四轴可能全等）。
    #: 它**不**另立原因码、也**不**另立轴：只是让 `detail` 说得出「为什么这条百分比不能放行」，
    #: 免得人读时把它当成「四轴不符」或「来源里没有」。
    denominator_unverified: list[str] = []
    for token in axes["numeric_surface"]:
        in_material = _num_ok(token, material_pool)
        fact_state = _fact_numeric_authorization(token, sentence.text, resolved_facts)
        if fact_state == FACT_NUMERIC_AUTHORIZED:
            #: 合格事实是**独立**的授权来源（路径 A）：它自己就够，且**不**因此被拖进
            #: 「逐格对上行列标签」那一套要求——那是表数字那一支的事，两支互不顶替。
            #: `scp-11` 起这一支要求**语义配对**（见 `_fact_numeric_authorization`）。
            numeric_qualified.add(token)
            continue
        strict = is_qualified_numeric_surface(token, sentence.text)
        if in_material and not strict:
            continue
        #: 走到这里 ⇒ 这个数字要么来源池里根本没有，要么来源池里只有「普通材料逐字出现」
        #: 而它属于金额 / 比率那一类，要么它虽然逐字在被引事实里、那条事实的期间／指标／
        #: 单位／业务却与句意对不上（`scp-11`）。三条都要试一次格：**格**是它们共有的、
        #: 唯一的补票口。
        table_numbers.append(token)
        hit = None
        for key, cells in cells_by_material.items():
            unit = declared_by_material.get(key, {}).get("unit", "")
            for cell in cells:
                if _cell_matches(token, cell, unit):
                    hit = (token, cell, key)
                    break
            if hit:
                break
        if hit:
            cell_hits.append(hit)
            numeric_qualified.add(token)
        elif fact_state in (FACT_NUMERIC_MISMATCH, FACT_NUMERIC_UNVERIFIABLE,
                            FACT_NUMERIC_DENOMINATOR_UNVERIFIED):
            #: 逐字在**合格事实**里出现过，只是没有一条事实与句意相符（`scp-13` 起还包括
            #: 「事实声明了身份而句子侧核不了」与「占比事实的分母无轴可核」这两种）⇒ 不是
            #: 「来源里没有这个数字」，而是「没有一条与所写含义相符的合格事实」。三者的处置
            #: 相同（硬错、进**同一个**轴与**同一个**原因码），但报错理由必须分开：
            #: `numeric_surface` 说「来源里没有」在这里是错的。
            numeric_unqualified.append(token)
            if fact_state == FACT_NUMERIC_DENOMINATOR_UNVERIFIED:
                denominator_unverified.append(token)
        elif not in_material:
            numeric_missing.append(token)
        else:
            numeric_unqualified.append(token)
    bases = _numeric_bases(axes["numeric_surface"], fact_pool, material_pool,
                           tuple(hit[0] for hit in cell_hits))
    for axis in ("numeric_surface", "negation_surface", "period_surface", "subject_surface"):
        pool = source_pool if axis in ("numeric_surface", "negation_surface") \
            else list(source_pool) + list(framing_pool)
        missing = tuple(numeric_missing) if axis == "numeric_surface" \
            else _uncovered(axes[axis], pool)
        emit(axis, not missing,
             detail=("" if not missing else
                     "这些表面在**被引用的**来源里没有逐字出现（表数字还必须逐格对上）："
                     + str(missing)),
             surfaces=missing, citation_keys=sentence.citations,
             numeric_bases=bases if axis == "numeric_surface" else ())

    # ---- 轴 3b：金额 / 比率的资格（`scp-2`） ------------------------------
    #: 只判「上一轴已经通过」的那些：来源都还没有的数字由数字轴负责，不两处判同一件事。
    emit("numeric_qualification", not numeric_unqualified,
         detail=("" if not numeric_unqualified else
                 "这些金额 / 比率只在**被引普通材料**的原文里逐字出现过，既没有一条与"
                 "所写含义相符的合格事实，也没有落到所引表材料的某一格（业务行标签、"
                 "指标列标签、原值、格级 locator、单位口径五者缺一即不算）：普通材料的"
                 "原文出现**不**授权金额与比率"
                 + ("" if not denominator_unverified else
                    "。其中 " + str(tuple(denominator_unverified)) + " 命中的是**已声明的占比"
                    "事实**：期间／指标／单位类／业务作用域四轴可能全等，但**分母**在当前逐值"
                    "身份里没有承载位（`metric` 是封闭词表标签、`scope` 是分子业务），"
                    "「同为百分比」不能代替分母核验 ⇒ 保守判为**核不了**（不是判它写错），"
                    "待身份体加分母轴后升格")),
         surfaces=tuple(numeric_unqualified), citation_keys=sentence.citations,
         numeric_bases=tuple(b for b in bases if b in QUALIFIED_NUMERIC_BASES))

    # ---- 轴 4：旧来源不得写成当前状态 -------------------------------------
    docs = tuple(dict.fromkeys(m.document_id for m in resolved_materials if m.document_id))
    unknown_docs = [d for d in docs if not str(roles.get(d, "") or "").strip()]
    emit("source_role", not unknown_docs,
         detail=("" if not unknown_docs else
                 f"这些支撑文档在本次清单里读不到来源角色：{unknown_docs}（不猜角色）"),
         citation_keys=sentence.citations)
    if unknown_docs:
        emit("current_state_scope", True,
             detail="角色不可读 ⇒ 本轴不判（另见 `source_role` 的硬错误），不做两处结论",
             citation_keys=sentence.citations)
    else:
        stale = SRS.unqualified_current_state(sentence.text, support_document_ids=docs,
                                              roles=roles)
        emit("current_state_scope", not stale,
             detail=("本句只由不能表达当前状态的来源支撑，且自己没有任何期间限定："
                     "它会被读成持续至今的当前状态" if stale else ""),
             citation_keys=sentence.citations)

    # ---- 轴 5：勾选 / 模板文字不得冒充公司事实 ----------------------------
    #: `scp-7`：非叙述形态有**两条**路径——Researcher 侧分类器已判过的 `content_kind`，
    #: 以及 `_is_non_narrative_by_shape` 认得住的「勾选块 + 通篇无句末标点」形状。第二条
    #: 路径专治「勾选行带一条长尾串 ⇒ 分类器退回 `text`」的那一支（真实清单里
    #: `☑适用 □不适用 公司需遵守《…》…的披露要求 1）营业收入及营业成本整体情况` 正是如此）。
    non_narrative = [m for m in resolved_materials if _is_non_narrative_by_shape(m)]
    only_non_narrative = bool(resolved_materials) and not resolved_facts and (
        len(non_narrative) == len(resolved_materials))
    emit("template_text", not only_non_narrative,
         detail=("本句的引用全是**非叙述**形态（" +
                 "、".join(sorted({m.content_kind or "shape:selection_block"
                                   for m in non_narrative})) +
                 "），且没有任何合格事实：勾选/模板/版式碎片不得充当公司经营事实"
                 if only_non_narrative else
                 ("不适用（本句没有可用引用）"
                  if not resolved_materials and not resolved_facts else "")),
         citation_keys=sentence.citations,
         applicable=bool(resolved_materials or resolved_facts))

    # ---- 轴 6：表数字的格级来源 -------------------------------------------
    unmatched_cells = [key for key, cells in cells_by_material.items() if not cells]
    if not axes["numeric_surface"]:
        emit("table_cell_provenance", True, detail="不适用（本句没有数字）",
             citation_keys=sentence.citations, applicable=False)
    elif not cells_by_material:
        emit("table_cell_provenance", True, detail="不适用（本句没有引用表材料）",
             citation_keys=sentence.citations, applicable=False)
    elif unmatched_cells and numeric_missing:
        emit("table_cell_provenance", False,
             detail=("本句引了表并写了无从查证的数字，而所引的表材料**没有**任何合格的格"
                     "（业务行标签 / 指标列标签 / 原值 / 格级 locator 四者缺一即不合格）："
                     + str({k: list(cell_problems.get(k, ())) for k in unmatched_cells})),
             citation_keys=tuple(unmatched_cells))
    else:
        emit("table_cell_provenance", True,
             detail=("本句的每个数字都能落到具体的格" if cell_hits
                     else "本句引的表材料提供了合格格（无可对照的表数字）"),
             citation_keys=sentence.citations, numeric_bases=bases)

    # 逐格核对：只用**本句真的写到**的那些数字去对格（业务行 / 指标列 / 单位口径声明）
    row_missing: list[str] = []
    header_missing: list[str] = []
    for token, cell, _key in cell_hits:
        if cell["row_label"] not in sentence.text:
            row_missing.append(f"{token}@r{cell['row_index']}:{cell['row_label']!r}")
        if cell["column_header"] not in sentence.text:
            header_missing.append(f"{token}@c{cell['column_index']}:"
                                  f"{cell['column_header']!r}")
    emit("table_row_label", not row_missing,
         detail=("" if not row_missing else
                 f"写了表里的数字却没写它所处的业务行：{row_missing}"),
         surfaces=tuple(row_missing), citation_keys=sentence.citations)
    emit("table_column_header", not header_missing,
         detail=("" if not header_missing else
                 f"写了表里的数字却没写它所处的指标列：{header_missing}"),
         surfaces=tuple(header_missing), citation_keys=sentence.citations)

    if not table_numbers or not cells_by_material:
        emit("table_declaration", True, detail="不适用（本句没有用到表里的数字）",
             citation_keys=sentence.citations, applicable=False)
    else:
        #: 本句的数字**只能**来自表 ⇒ 表的单位 / 期间 / 口径声明必须在，否则「口径」这一项
        #: 根本无从核对。这一步**不**依赖格匹配是否成功：缺声明的表恰好会让格匹配失败，
        #: 若把它挂在匹配上，缺声明就会退化成「不适用」——那正是本条要防的静默放行。
        bare = [key for key in cells_by_material
                if not all(declared_by_material.get(key, {}).get(name)
                           for name in ("unit", "period", "scope"))]
        emit("table_declaration", not bare,
             detail=("" if not bare else
                     "本句用了表里的数字，而所引的表材料缺单位 / 期间 / 口径声明，"
                     f"口径无从核对：{bare}"),
             citation_keys=tuple(bare) or sentence.citations)

    # ---- 轴 7：本句引的来源**登记归属**本栏目吗（`scp-3`） ------------------
    #: 本轴回答的是另一件事，与上面六轴**正交**、不可互推：引用在场（轴 1）说的是「这句话
    #: 有出处」，文字对了（轴 3）说的是「这些话在出处里逐字出现」，本轴说的是「这个出处
    #: **属于这一栏**」。三者可以同时为真而结论仍然错——「一致行动人协议终止」逐字出自
    #: 一份真实材料、引用键也在本次清单里，它只是**不是**供应商集中度那一栏的材料。
    declared_set = tuple(dict.fromkeys(str(a).strip() for a in declared_aspect_ids
                                       if str(a).strip()))
    paragraph_set = tuple(dict.fromkeys(str(a).strip() for a in paragraph_aspect_ids
                                        if str(a).strip()))
    if not sentence.citations:
        emit("aspect_attribution", True,
             detail="不适用（本句没有任何引用：栏目归属由「引用在场」轴负责，不两处判）",
             citation_keys=(), applicable=False)
    elif not declared_set:
        emit("aspect_attribution", True,
             detail="不适用（调用方没有为本小节声明 Contract 栏目 ⇒ 本轴不判，不猜一栏出来）",
             citation_keys=sentence.citations, applicable=False)
    elif not manifest_has_aspect_axis(manifest):
        emit("aspect_attribution", True,
             detail="不适用（本节这次输入里**没有任何来源**被登记到任何栏目 ⇒ 这条链上没有"
                    "栏目登记轴。财务 / 附注 / 外部快照那一支本就没有 aspect 级归属："
                    "把「没有这条轴」判成「全错栏」是一道假门，本轴不那样用）",
             citation_keys=sentence.citations, applicable=False)
    else:
        registered = set(registered_aspects(manifest=manifest,
                                            citation_keys=sentence.citations))
        covered = registered & set(declared_set)
        ok = bool(covered)
        detail = "" if ok else (
            f"本小节声明的栏目 {' / '.join(f'`{a}`' for a in declared_set)} **都不在**"
            f"所引来源的登记归属里（登记归属："
            f"{list(sorted(registered)) or '（一条都没有被认领到任何栏目）'}）："
            "引用虽存在、字面虽可能逐字相同，这一句**不算**本栏目的覆盖；"
            "不得据此宣称本栏目已写出来")
        # 段级复核（`scp-5`）：小节集合在「一个小节覆盖十几栏」时太宽，段自己声明的
        # `aspect_ids` 才是「这一段本来要服务哪几栏」。段没声明（空集合）就**不**加判据——
        # 那是「没有可判的段级声明」，不是「段级声明不成立」，两者不得合并。
        if ok and paragraph_set:
            paragraph_covered = registered & set(paragraph_set)
            if not paragraph_covered:
                ok = False
                detail = (
                    f"本段自己声明服务的栏目 "
                    f"{' / '.join(f'`{a}`' for a in paragraph_set)} **不在**本句所引来源的登记"
                    f"归属里（登记归属：{list(sorted(registered))}）：句级覆盖到了本小节别的"
                    "栏目，但**不是这一段声明要写的那几栏** ⇒ 这一段没有写出它自己承诺的内容")
        emit("aspect_attribution", ok, detail=detail,
             citation_keys=sentence.citations)

    # ---- 轴 8：本句引的事实**经呈现层声明**属于本栏目吗（`scp-4`） -------------
    #: 与轴 7 **并列而不合并**。财务那一支的权威事实 `aspect_ids` 恒为空（
    #: `pack_writer.scan_financial` 不编造 aspect 状态），因此轴 7 在那里恒不适用；而轴 7
    #: 不适用**不等于**「本栏没有栏目归属这回事」——本次运行的呈现层确实声明了某指标属于某栏。
    #: 两轴混用会让读者面把「权威自己认领了这一栏」与「呈现层声明它属于这一栏」读成同一句话。
    #: 本轴只**增加**一条判据，不放宽轴 7 的任何一条：topic Pack 那一支照旧按登记归属判。
    cited_fact_keys = tuple(key for key in sentence.citations
                            if manifest.fact_for_key(key) is not None)
    if not sentence.citations:
        emit("presentation_column_attribution", True,
             detail="不适用（本句没有任何引用：栏目归属由「引用在场」轴负责，不两处判）",
             citation_keys=(), applicable=False)
    elif not declared_set:
        emit("presentation_column_attribution", True,
             detail="不适用（调用方没有为本小节声明 Contract 栏目 ⇒ 本轴不判，不猜一栏出来）",
             citation_keys=sentence.citations, applicable=False)
    elif not manifest_has_presentation_column_axis(manifest):
        emit("presentation_column_attribution", True,
             detail="不适用（本节这次输入里没有呈现层路由声明 ⇒ 这条链上没有呈现层栏目轴；"
                    "把「没有这条轴」判成「全错栏」是一道假门，本轴不那样用）",
             citation_keys=sentence.citations, applicable=False)
    elif not cited_fact_keys:
        emit("presentation_column_attribution", True,
             detail="不适用（本句没有引用任何**事实行**：呈现层路由声明只覆盖事实行，"
                    "材料行的归属由登记归属轴负责）",
             citation_keys=sentence.citations, applicable=False)
    else:
        routed = routed_columns(manifest=manifest, citation_keys=cited_fact_keys)
        # `cwm-6`：判据从「恰好等于那一个栏目」放宽到「**包含于**本小节声明的栏目集合」。
        # 放宽的只是「一个小节能覆盖几栏」，**没有**放宽「不许串栏」：本句引的事实只要有
        # 一栏落在本小节声明之外，本轴照旧是硬错误。原来的单值等号在 18 栏塌成 `fin-h2`
        # 一栏之后会把「同小节内跨栏」误判成串栏，那才是假门。
        ok = bool(routed) and set(routed) <= set(declared_set)
        emit("presentation_column_attribution", ok,
             detail=("" if ok else
                     f"本小节声明的栏目是 "
                     f"{' / '.join(f'`{a}`' for a in declared_set)}，本句所引事实经**呈现层"
                     f"声明**落到 {list(routed) or '（没有任何一栏）'}：这一句**不算**本栏目的"
                     "覆盖。路由声明只说明「这个指标本来属于哪一栏」，它不证明那一栏的 "
                     "Contract 要求已被满足；用别的栏目的指标顶替，读者会把缺的那一栏读成"
                     "已经写出来了"),
             surfaces=tuple(routed), citation_keys=sentence.citations)

    # ---- 轴 9：本句引的事实有没有落进**诊断槽位**（`scp-6`） -------------------
    #: 这一轴与轴 8 **正交**，两轴都要留：轴 8 判「引的栏目是不是本小节声明的那几栏」，
    #: 本轴判「这个栏目的**展示角色**允不允许写进普通正文」。同一句可以「栏目对得上」
    #: 而展示角色错——利息保障倍数的代理口径就在 `fin_solvency` 这一节里，它的栏目是本节
    #: 自己的栏目（轴 8 通过），但冻结 Contract 把那一栏声明成 `diagnostic_only`，它只进
    #: 诊断槽位（这一轴才拦得住）。反过来「展示角色对」也不等于「栏目对」。合并成一条会让
    #: 读者面分不清「写错了栏目」与「写完栏目但用错了展示层」。
    #:
    #: 判据是**确定性**的：只读清单自己的呈现层声明，不由写作侧自述，也不问 review LLM。
    if not sentence.citations:
        emit("display_role", True,
             detail="不适用（本句没有任何引用：展示角色说的是所引事实的档位，无引用则无此轴）",
             citation_keys=(), applicable=False)
    elif not manifest_has_presentation_column_axis(manifest):
        emit("display_role", True,
             detail="不适用（本节这次输入里没有呈现层路由声明 ⇒ 这条链上没有展示档；"
                    "把「没有这条轴」判成「全写错档」是一道假门，本轴不那样用）",
             citation_keys=sentence.citations, applicable=False)
    elif not cited_fact_keys:
        emit("display_role", True,
             detail="不适用（本句没有引用任何**事实行**：展示档只声明在事实行的呈现层路由上；"
                    "材料行的角色由「来源角色」轴负责）",
             citation_keys=sentence.citations, applicable=False)
    else:
        tiers, tier_unresolved = routed_column_tiers(manifest=manifest,
                                                     citation_keys=cited_fact_keys)
        diagnostics = tuple(t for t in tiers if t == DIAGNOSTIC_DISPLAY_TIER)
        ok = not diagnostics
        detail = "" if ok else (
            f"本句所引的事实里，有经呈现层声明落在**诊断槽位**的栏目"
            f"（`contract_display_tier={DIAGNOSTIC_DISPLAY_TIER!r}`）："
            "这一档按展示角色边界**只进**附录 / 脚注 / 诊断表，写进普通正文会让读者把它读成"
            "正文结论。代理口径一类数值在诊断槽位里照常可读，但不得被这样引用")
        if ok and tier_unresolved:
            detail = ("（另有本句所引事实落在呈现层路由里**查不到展示档**的栏目 "
                      f"{list(tier_unresolved)}：本轴不为它代言，也不假装它已核过）")
        emit("display_role", ok, detail=detail, surfaces=diagnostics,
             citation_keys=sentence.citations)

    return tuple(records)


def _framing_pool(manifest: CW.CitedWriterInputManifest, subsection_id: str) -> tuple[str, ...]:
    """报告框架文本（小节标题 + Contract 逐字要求文本）：只授权时点与主体，不授权数字/否定。"""
    texts = [manifest.section_title]
    for spec in manifest.subsections:
        if not subsection_id or spec.subsection_id == subsection_id:
            texts.extend([spec.title, spec.requirement_text])
    return tuple(t for t in texts if t)


def _numeric_bases(tokens: Sequence[str], fact_pool: Sequence[str],
                   material_pool: Sequence[str], cell_pool: Sequence[str]) -> tuple[str, ...]:
    """本句数字的来源类型（按 :data:`NUMERIC_BASES` 的顺序，去重后有序）。

    同一句里可以既有事实数字又有表数字，因此结果是**多值**的。`table_cell_source` 出现在结果里
    **不**表示那些数字取得了任何权威身份：它只说明「这个数字出自这张表的这一格，格级位置在」
    （见 :data:`NUMERIC_BASES` 与模块头边界 2）。
    """
    bases: list[str] = []
    for bucket, pool in (("qualified_fact", fact_pool),
                         ("material_verbatim_text", material_pool),
                         ("table_cell_source", cell_pool)):
        if any(_num_ok(token, pool) for token in tokens):
            bases.append(bucket)
    return tuple(bases)


# ---------------------------------------------------------------------------
# 整节
# ---------------------------------------------------------------------------

def check_cited_prose(*, draft: CW.CitedProseDraft,
                      manifest: CW.CitedWriterInputManifest) -> SentenceCheckReport:
    """整节逐句核对（`sc-2`）——**唯一**入口。

    草稿与清单必须对得上（`input_manifest_id`），否则 fail-closed：核对一份来路不明的正文，
    结论没有意义。返回的是一份**可落盘、可回放**的报告；它不修改草稿，一个字都不改。
    """
    if draft.input_manifest_id != manifest.manifest_id:
        raise SentenceCheckError(
            f"草稿绑的输入清单 {draft.input_manifest_id!r} 不是本次清单 "
            f"{manifest.manifest_id!r}：核对一份来路不明的正文没有意义，fail-closed")

    roles = document_roles(manifest)
    #: 小节 → 它**声明覆盖**的 Contract 栏目集合。从清单取（`cwm-6` 起是必填字段），不从
    #: `subsection_id` 反推——「小节 id 恰好等于 aspect id」是这条链今日的巧合，不是判据。
    declared_by_subsection = {s.subsection_id: s.declared_aspect_ids
                              for s in manifest.subsections}
    records: list[SentenceCheckRecord] = []
    count = 0
    for subsection in draft.subsections:
        declared_aspect_ids = declared_by_subsection.get(subsection.subsection_id, ())
        if not declared_aspect_ids:
            raise SentenceCheckError(
                f"草稿小节 {subsection.subsection_id!r} 在本次输入清单里没有对应的 "
                "`declared_aspect_ids`：小节与清单一一眼对应（`_check_subsection_coverage`）"
                "本应保证它存在，这里读不到说明两者已经脱钩，栏目归属轴会整体静默跳过——"
                "fail-closed，不猜一栏")
        for paragraph in subsection.paragraphs:
            for sentence in paragraph.sentences:
                count += 1
                records.extend(check_sentence(
                    sentence=sentence, subsection_id=subsection.subsection_id,
                    paragraph_id=paragraph.paragraph_id, manifest=manifest, roles=roles,
                    declared_aspect_ids=declared_aspect_ids,
                    paragraph_aspect_ids=paragraph.aspect_ids))

    return SentenceCheckReport.create(
        draft_id=draft.draft_id, input_manifest_id=manifest.manifest_id,
        dependency_fingerprint=manifest.fingerprint(), sentence_count=count,
        records=tuple(records))

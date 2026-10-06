"""O-12 期间/来源角色核对：**无期间的当前态断言**（`srsc-1` 期间位次 / `srsc-2` 独立支撑结论）。

`DESIGN_V2.md` O-12 的口径是：「同类材料按『较新且可核实者优先表达当前状态』；旧同类型材料
保留索引与来源身份，用于历史分期数据、变化、补充与冲突核对，不得静默丢弃」。这条口径在
`harness/source_manifest.py` 里已经**落成了闭集角色**（`SOURCE_ROLES` + `sm-2` 的选择规则）：

  * `current_state_source`——同系列里「较新且可核实」的那一份（兼容锚）；
  * `history_and_conflict_source`——**同系列而期间较旧**，或**同系列而期间不可核实**
    （`no_verifiable_period_lower_rank`）的成员；
  * `topic_participating_source`——**其他系列**的成员，按主题与锚同等资格参与检索；
  * `not_used`——不满足 eligibility，本 run 不作为事实来源（登记身份保留）。

本模块回答**两件前后相接的事**，都只针对**路径 B**（材料派生）且**自己没写期间**的候选：

  * `srsc-1`（:func:`unqualified_current_state`）：factual 支撑边**全部**落在不能表达当前状态的
    来源角色上 ⇒ 这条断言**不能**按现在的措辞写出来（旧材料当前化）；
  * `srsc-2`（:func:`current_state_support_is_extractive`）：有边落在**当前锚**上，但**没有
    一条**这样的边被证明确实支撑这条命题 ⇒ 同样不能按现在的措辞写出来（「有锚」≠「已核实」）。

两条判据**互斥但不互补**（见边界 5）。模块本身不读材料正文、不调模型、不改任何字、不产生
任何事实：`srsc-2` 的正文与身份都由调用方解析好后作为参数传进来（两个取数入口见
:func:`document_axis_index` / :func:`declared_axes_from_locator`）。

## 为什么判据只吃角色、不吃期间

角色**本来就是**那条期间判断的落点：`select_documents` 用**内容报告期间**（`period_end`，
不是披露日期、不是入库时间）比较同系列成员，并把结果写成角色；期间不可核实的同系列成员
同样取 `history_and_conflict_source`。在这里再实现一遍期间比较，就等于给同一条业务判断造
第二个真值来源——两者一旦漂移，「旧材料当前化」会从「被挡住」变成「谁先算谁说了算」。
因此本模块**不**持有期间表、**不**比较任何日期、**不**推造披露日期，也**不**按公司名、
文件名或页码写任何特例。

## 四条边界（缺一条就会把这条判据用错地方）

1. **只管路径 B**。路径 A 候选绑的是预验证权威事实：它在写入侧只有一个容器身份，**到不了**
   具体来源文档，硬套就会把「权威事实自己的文本」读成「材料派生的候选」，并让权威事实
   无谓地整批被拒。
2. **只管「全部支撑边都是同类较旧」**。只要有一条边落在当前锚上，这条断言就**可能**由较新
   材料核实，`srsc-1` 不再适用——本条挡的是「旧材料当前化」，不是「用了旧材料」。
3. **跨系列成员不进本判据**。其他系列的成员按主题与锚**同等资格**参与检索（口径 2：
   「一份不得无条件替代另一份」说的正是**替代**关系，不是「它不能支持当前状态」）。
4. **`srsc-2`：「落在当前锚上」不等于「新材料确实核实了这条命题」**。边界 2 只回答「有没有
   一条边落在当前锚上」，**不回答**那条边是否真的支撑这条命题。把前者当成后者，就会让
   「同 ID 错版本 / 错 Evidence Set」「新材料只有同产品名」「旧+新拼接才成立」「旧材料称 A
   而新材料称『已停止 A』」这四类全部漏过去。第四条边界因此是**独立支撑结论**：只有对**单一**
   较新材料给出**严格抽取式**证明（见 :func:`current_state_support_is_extractive`）才判
   `extractive`，其余一律 `unproven`。
5. **锚边只取 `CURRENT_STATE_ANCHOR_ROLES`，不取「非历史角色」的补集**。补集口径会把
   `topic_participating_source`（其他系列）也读成「较新材料」，与边界 3 直接冲突。因此
   `srsc-2` 与 `srsc-1` **互斥但不互补**：一条只由跨系列成员（例如同一发行人的**另一类**文件）
   支撑、自己又没写期间的候选，两条判据都不适用——它由**它自己那条链**的资格判据负责，本模块
   不为它代言。

## 为什么不是「给候选加上期间限定」而是「不写这条当前断言」

本批**不**发明期间限定语。路径 B 的候选文本里任何**期间表面**（年份、绝对日期等）本来
就要过写入侧的授权面判据（`PW._path_b_high_risk_surfaces` / 门的 `cbg-2`）：未预验证的
期间表面一律打回。把「补一个期间词」写成本模块的出口，等于在授权面之外另开一条造表面的
路——那正是本批不得做的事。因此本模块的出口只有两条：**要么去掉当前的措辞**（改写成历史
断言，或不再提出这条候选），**要么**这条断言本就由较新材料支撑（判据不成立）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class SourceRoleScopeError(Exception):
    """来源角色判据自身的输入不一致（身份对不上 / 映射不完整），fail-closed。"""


#: 本判据的规则版本（判据、词表、角色集任一变化都必须升版）。
#:
#: `srsc-1`：初版。判据只吃 `source_role`（见模块头「为什么判据只吃角色」）。
#: `srsc-2`：新增第四条边界——**独立支撑结论**。边界 2 原先把「有一条边落在当前锚上」当成
#: 「较新材料已核实这条命题」；`srsc-2` 把这一步补成一条可判的证明（严格抽取式窄情形），
#: 其余记 `unproven`。`srsc-1` 的判据、词表、角色集一字未改：`srsc-2` 只**增加**要求，
#: 不**放宽**旧要求。
#: `srsc-3`：`srsc-2` 的**抽取式子串**读法有一处被钻空的口子——源句自带期间/范围限定
#: （`报告期内…` / `2024年…`），候选却把它**截掉**，于是「截出来的那半句」既是合法连续
#: 子串、又没有任何期间限定，读者只能读成**持续至今的当前状态**。`srsc-3` 把这一步补上：
#: 覆盖候选的那一句若自带期间限定而候选自己没有 ⇒ `unproven`（原因码
#: `source_period_scope_dropped`）。判据只按**候选覆盖到的那一句**判，**不**按整份材料判
#: ——一条材料里别的句子带不带期间，与这条候选无关；因此它对「本来就没有期间限定的稳定
#: 客观描述」**不**加任何要求（不逼着描述句去补日期）。同样只**增加**要求，不放宽旧要求。
#: `srsc-4`：`srsc-1` 的第 3 条把**相对**限定词（``报告期``）也算作「自己写明了期间」。这在
#: 「支撑全是较旧来源」这个前提之下是**反的**：``报告期`` 不携带年份，要读出它是哪一年，只能
#: 另配那份来源文档自己的期间——而这条判据的成立条件恰恰是那份文档**期间较旧**。于是
#: 「截至报告期末，公司已实现…」引 2024 年报时，与「完全没有期间限定」在读者面上是同一件事。
#: `srsc-4` 把第 3 条收紧为**绝对**年份（:func:`has_absolute_period_qualification`）；只写
#: ``报告期`` 不再豁免。`srsc-2` 的 `source_period_scope_dropped` **不受影响**（它判的是
#: 「候选截掉了源句自带的限定语」，那一支继续用 :func:`has_period_qualification`，与其文档
#: 新旧无关）。同样只**增加**要求，不放宽旧要求。
RULE_VERSION = "srsc-4"

#: **不能**用来表达「当前状态」的来源角色。刻意**恰好一个**，且逐值给出理由：
#:
#:   * `current_state_source` 不在内：它就是「较新且可核实者」本身，是当前状态的合法载体；
#:   * `topic_participating_source` 不在内：它是**其他系列**的成员，按主题与锚同等资格参与
#:     检索（口径 2）。把「不同系列」读成「不能支持当前状态」会把一份募集说明书对自身主题
#:     的正常支撑判成非法；
#:   * `not_used` 不在内：它能不能作为事实来源由**既有资格链**判定（材料准入 / 支撑资格），
#:     本条判据不为它另开一条口径——两处都判会让「为什么这条边不能承重」有两个答案。
CANNOT_ESTABLISH_CURRENT_STATE_ROLES = ("history_and_conflict_source",)

#: **当前锚**的来源角色：只有它能在一条**没有期间**的当前态断言上充当「较新材料已核实了这条
#: 命题」里的那份材料（`srsc-2` 的锚边集合）。
#:
#: 为什么**恰好**是它、而不是「:data:`CANNOT_ESTABLISH_CURRENT_STATE_ROLES` 的补集」——这是本表
#: 最容易被写错的一处：
#:
#:   * 补集里还有 `topic_participating_source`（**其他系列**的成员）。O-12 的「较新」是**同系列
#:     内**的期间位次；跨系列成员按主题与锚**同等资格**参与检索（口径 2 / 模块头边界 3）。把它
#:     读成「较新材料」，会让一份募集说明书对自身主题的正常支撑无端多背一条它本不该背的证明
#:     义务——那不是收紧了门，那是换了一件事判；
#:   * 补集口径还会把缺口**换个名字留住**：「有一条非历史边」并不因此变成「当前锚边」。判据的
#:     边界 2 说的是「有一条边落在**当前锚**上」，指的就是这个角色，不是「不是历史来源的都算」。
#:
#: `not_used` 同样不在内：它能不能当当前锚由**既有资格链**判定（材料准入 / 支撑资格），本条判据
#: 不为它另开一条口径。
CURRENT_STATE_ANCHOR_ROLES = ("current_state_source",)

#: 期间限定词闭集（**通用**：不含任何公司名、文件名、页码或固定年份）。
#:
#: 只收**锚在绝对期间上、不可能被读成报告框架下的当前期间**的词。这一条排除了绝大多数
#: 候选词，逐项理由（这是本表最容易被「顺手加两个」的地方）：
#:
#:   * 不收「本年度」「当年」「同期」「上年度」「上一年度」「上半年」「下半年」——它们是
#:     **相对**词：读者按**报告自己的框架**读（报告 `report_as_of` 是 2025 年末时，
#:     「本年度」读成 2025），而候选的支撑材料是 2024 年的同系列文档。用它做限定，恰好
#:     造出本条判据要消除的那种混淆，因此**不得**当作期间限定；
#:   * 不收「目前」「当前」「仍」「持续」——那些恰恰是**当前式**措辞，收进来判据就自废；
#:   * 不收「截至」「年末」「年中」「年初」——只点出期间里的一个**时点/边界**，不点出
#:     是哪一段期间（「截至本公告日」也是「截至」）；
#:   * 不收「历年」——指一段**未指明边界**的历史跨度。
#:
#: 留下两项：绝对年份 token（`_YEAR_TOKEN_RE`，认的是「四位年 + 年」这一通用形态，不认
#: 具体是哪一年）与裸根「报告期」（文档**自述**的那个期间，口径唯一）。
#:
#: **本表今天在路径 B 上打不满**：年份 token 与「报告期」本来就要过写入侧的授权面判据
#: （`NS.scan_numeric_tokens` 的 `年` 单位／`NS.VAGUE_PERIOD_PHRASES`），未预验证的期间
#: 表面一律打回。因此路径 B 的**实际出口是不写这条当前断言**；本表的语义仍然必要——
#: 它说清了「什么样的断言按 O-12 本来可以留下」，也让最终句读回那一侧有唯一判据可用。
PERIOD_QUALIFICATION_MARKERS = ("报告期",)

#: 「四位年 + 年」的通用形态（`2024年` / `2024 年`）。它认的是**年**这个粒度标记，不认
#: 具体是哪一年，因此不含任何固定年份规则。
#:
#: 两个数字边界的作用是**不从一个更长的数字串里切出年份**：`300750年` 里的 `0750年`
#: 不是年份 token，而是一段编号的后四位。少了这两条边界，「一串编号 + 年」会被读成
#: 「这条断言点明了期间」，判据给出的结论就反过来看了。
_YEAR_TOKEN_RE = re.compile(r"(?<!\d)\d{4}\s*年")


def has_period_qualification(claim_text: str) -> bool:
    """候选文本是否自己把断言锚到了某个期间（年份 token 或**相对**期间限定词）。

    只做**存在性**判断，不做语义判断：「本年度」锚到的是哪一年由材料自己的期间决定，本函数
    不解析、不推断、不回填。空白不归一：判据逐字读候选文本，与候选自己的文本同源。

    .. warning::
       相对限定词（``报告期``）只有**与文档期间同读**才说得通。本函数两个 marker 都认，因此
       **不能**单独用来回答「这句话是不是被当前化了的旧材料断言」——那是
       :func:`has_absolute_period_qualification` 的活。`srsc-4` 起
       :func:`unqualified_current_state` 已改用后者；本函数仍留给
       :func:`source_period_scope_dropped`（那一支判的是「候选截掉了源句自带的限定语」，
       源句自带 ``报告期`` 时截掉它就是口径变了，与其文档新旧无关）。
    """
    text = str(claim_text or "")
    if not text:
        return False
    if _YEAR_TOKEN_RE.search(text):
        return True
    return any(marker in text for marker in PERIOD_QUALIFICATION_MARKERS)


def has_absolute_period_qualification(claim_text: str) -> bool:
    """候选文本是否自己把断言锚到了一个**绝对**年份（`srsc-4`）。

    与 :func:`has_period_qualification` 的唯一差别：**不认**相对限定词 ``报告期``。

    为什么必须分开：``报告期`` 是文档**自述**的那个期间，它本身不携带年份——读者要读出
    「这是哪一年」，只能另配那份文档的期间。当支撑全是较旧来源时，这个前提恰好不成立：
    一句「截至报告期末，公司已实现…超1,700万辆」引自 2024 年报，读者读到的却是**持续至今**
    的当前状态（`srsc-1` 要挡的正是这件事）。旧年来源配上相对限定词，与**没有任何**期间限定
    在读者面上是同一件事，因此本函数只认年份 token。判据仍是**通用形态**（四位年 + 年），
    不含任何固定年份规则。
    """
    return bool(_YEAR_TOKEN_RE.search(str(claim_text or "")))


#: 句边界（`srsc-3` 的「那一句」按这里切）。**只有**句末标点与换行；逗号、顿号、分号都不算
#: ——把逗号当边界会把「报告期内，公司…」切成两半，限定语与被限定的那句话正好分家，
#: 判据就永远看不见它要看见的东西。
_SENTENCE_DELIMITERS = "。！？；!?;\n\r"

#: 句边界集合（与 :data:`_SENTENCE_DELIMITERS` 同源，供切片用）。
_SENTENCE_DELIMITER_SET = frozenset(_SENTENCE_DELIMITERS)


def _sentences(view: str) -> "list[str]":
    """把一段正文切句（**保留**句末标点：判据要逐字读，不做任何改写或归一）。"""
    out: list[str] = []
    current: list[str] = []
    for char in str(view or ""):
        current.append(char)
        if char in _SENTENCE_DELIMITER_SET:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


def _tight_with_origin(text: str) -> "tuple[str, list[int]]":
    """仅空白归一的紧化，同时给出**每个紧化字符来自原串的哪个位置**（判据回指用）。"""
    chars: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(str(text or "")):
        if char.isspace():
            continue
        chars.append(char)
        origin.append(index)
    return "".join(chars), origin


def covering_sentences(view: str, claim_text: str) -> "tuple[str, ...]":
    """候选正文**覆盖到**的那几句源句（`srsc-3` 的输入面）。

    定义：把 `view` 按句边界切开、逐句仅空白归一，得到一份**带来源标注**的紧化串；
    候选自己的紧化文本在这份串里出现一次，则它覆盖到的源句集合 = 出现区间两端各自落在
    哪一句。区间跨句时**两句都算覆盖**（这正是「截掉了限定语」的常见形状：候选横跨
    「限定语那半句」与「被限定的那半句」）。

    返回空 tuple 有三种来由，调用方必须区分对待（本函数不替它决定）：候选为空、
    `view` 为空、或候选压根不在 `view` 里（后者由包含关系判据另行处理）。本函数**只**
    回答「覆盖了哪几句」，**不做**任何期间/范围判定。
    """
    needle, _ = _tight_with_origin(claim_text)
    if not needle:
        return ()
    haystack_parts: list[str] = []
    sentence_marks: list[int] = []   # 每个紧化字符属于第几句
    for index, sentence in enumerate(_sentences(view)):
        tight, _ = _tight_with_origin(sentence)
        haystack_parts.append(tight)
        sentence_marks.extend([index] * len(tight))
    haystack = "".join(haystack_parts)
    if not haystack:
        return ()
    start = haystack.find(needle)
    if start < 0:
        return ()
    end = start + len(needle) - 1
    first, last = sentence_marks[start], sentence_marks[end]
    sentences = _sentences(view)
    return tuple(sentences[i] for i in range(first, last + 1))


def source_period_scope_dropped(claim_text: str, view: str) -> bool:
    """候选是否**截掉了**它覆盖到的源句自带的期间/范围限定（`srsc-3`）。

    判据只有两步，逐字可复核：

      1. 候选自己**没有**期间限定（:func:`has_period_qualification` 为假）——候选要是自己
         写着期间，它已经是历史断言，本条不为它代言；
      2. 候选覆盖到的源句里，**至少有一句**自带期间限定（同一函数）。

    第 2 步只按**覆盖到的那几句**判，**不**按整份材料判：一条材料里别的句子带不带期间，
    与这条候选无关。因此「本来就没有任何期间限定的稳定客观描述」在这条判据下**恒不成立**
    ——它不会逼着描述句去补一个源文里没有的日期。这一条是本判据唯一的边界，也是最容易被
    写反的一处（写成「材料里出现过期间」就会把所有描述句连坐）。

    **为什么必须按「覆盖到的句子」而不是「候选是不是整句」**：`srsc-2` 只要求候选是正文的
    连续子串，于是一条源句「报告期内，公司销售境外的主要产品为电池系统，较上年同期相比
    未发生明显变化」可以被截成「公司销售境外的主要产品为电池系统」——它是合法连续子串，
    却把限定语与后半句一起丢掉，读者读到的是一句**没有期间的持续现状**。源句自带限定而
    候选没有时，这条断言的**口径变了**，按现措辞证不出来。
    """
    if has_period_qualification(claim_text):
        return False
    for sentence in covering_sentences(view, claim_text):
        if has_period_qualification(sentence):
            return True
    return False


def has_source_document_series(authority: Any) -> bool:
    """本节权威**有没有**「来源文档系列」这条轴（= 它是不是带来源集的 topic Pack 权威）。

    调用方据它短路：财务 / 附注 / 外部快照权威各有自己的 payload 与资格链，**没有**来源集，
    它们的证据材料不带来源角色。此时「读不到角色」不是缺陷，而是**这条轴上没有来源角色**——
    短路掉才是对的，否则会把那些链上的候选整批按「读不到角色」误杀。

    与 :func:`document_roles` 的分工：本函数只回答「这条轴存不存在」，不做任何角色判断；
    真进了这条轴却读不到角色（`document_id` 不在台账里、同一份文档两个角色）时才由
    :func:`document_roles` / :func:`unqualified_current_state` 当场抛。
    """
    pack_set = getattr(authority, "pack_set", None)
    return bool(tuple(getattr(pack_set, "packs", ()) or ()))


def document_roles(authority: Any) -> "dict[str, str]":
    """`{document_id: source_role}`——本节权威各 Pack 来源集的并集（**唯一**取角色处）。

    角色只从 Pack 自己的 `source_set`（`DocumentSourceSet.members` 的
    `(SourceDocumentKey, source_role)`）读，不从文件名、路径、注册类型或入库时间推断。

    两个 fail-closed（都是「身份对不上」而不是「数据缺一点」）：

    * 同一 `document_id` 在本节不同 Pack 上被登记成**不同角色**——那说明本节的两份 Pack
      对同一份材料的来源关系说法不一致，此时按哪一份都是猜；
    * 权威不是 topic Pack（财务 / 附注 / 外部快照走各自 payload，没有来源集）——本判据
      只对 topic path B 有意义，调用方本就不得在那些链上调用它。
    """
    pack_set = getattr(authority, "pack_set", None)
    packs = tuple(getattr(pack_set, "packs", ()) or ())
    if not packs:
        raise SourceRoleScopeError(
            f"来源角色判据只对 topic Pack 权威有意义，实际 "
            f"{type(authority).__name__}（无 pack_set.packs）：不得在财务/附注/外部快照链上调用")
    roles: dict[str, str] = {}
    for pack in packs:
        source_set = getattr(pack, "source_set", None)
        for key, role in tuple(getattr(source_set, "members", ()) or ()):
            document_id = str(getattr(key, "document_id", "") or "")
            role = str(role or "")
            if not document_id or not role:
                raise SourceRoleScopeError(
                    f"Pack {getattr(pack, 'pack_id', '')!r} 的来源集成员缺 document_id/role："
                    f"{key!r} / {role!r}")
            known = roles.get(document_id)
            if known is not None and known != role:
                raise SourceRoleScopeError(
                    f"同一 document_id={document_id!r} 在本节不同 Pack 上角色不一致："
                    f"{known!r} vs {role!r}（按哪一份都是猜，fail-closed）")
            roles[document_id] = role
    return roles


def unqualified_current_state(
        claim_text: str, *, support_document_ids: Any,
        roles: Mapping[str, str]) -> bool:
    """这条候选是不是「只由同类较旧来源支撑、且自己没写期间限定」的当前态断言。

    成立条件三条**同时**满足：

    1. 至少有一条支撑文档（否则这条候选没有路径 B 支撑，不进本判据）；
    2. 每一条支撑文档的角色都在 :data:`CANNOT_ESTABLISH_CURRENT_STATE_ROLES` 内；
    3. 候选文本自己没有**绝对**期间限定（`has_absolute_period_qualification` 为假）。

    第 3 条在 `srsc-4` 起收紧：只写 ``报告期`` **不算**限定。相对限定词要读出年份，必须配
    支撑文档自己的期间；而本条的前提恰是「支撑全是较旧来源」——此时「报告期末」在被引文档里
    是**那一年的**期末，在读者面上却是一条没有年份的现状断言。旧年来源 + 相对限定词因此与
    「没有期间限定」同判，这与 `DESIGN_V2.md` §5 的三条日期轴一致：事实适用期、来源归属期、
    报告基准期是三件事，相对限定词只指向第三个，不能替第一个作证。

    查不到角色的支撑文档**直接抛**：读不到角色只说明调用方给的映射不完整（缺陷），不说明
    「这份材料的角色是历史来源」。默认成任何一侧都是猜——默认成「危险」会误杀权威事实，
    默认成「安全」会让旧材料当前化漏过去。
    """
    document_ids = tuple(dict.fromkeys(
        str(d) for d in (support_document_ids or ()) if str(d or "")))
    if not document_ids:
        return False
    for document_id in document_ids:
        if document_id not in roles:
            raise SourceRoleScopeError(
                f"支撑文档 {document_id!r} 不在本节来源集台账里，读不到来源角色："
                "本判据不猜角色（默认成任一侧都会把结论送错方向）")
        if roles[document_id] not in CANNOT_ESTABLISH_CURRENT_STATE_ROLES:
            return False
    return not has_absolute_period_qualification(claim_text)


# ---------------------------------------------------------------------------
# `srsc-2`：独立支撑结论（边界 ④）
# ---------------------------------------------------------------------------

#: 四轴身份（`SourceDocumentKey` 的四轴）。**比对方式分两类**，如实分开写：
#:
#:   * :data:`COMPARED_AXES`——边上有**声明侧**，逐轴比对。`document_id` /
#:     `document_version` 来自边的 `locator_ref` 容器，而容器由
#:     `TS.material_container_identity` 确定性派生为
#:     `evidence_document:{document_id}@{document_version}`，因此这两轴在声明侧真实在场。
#:   * :data:`PRESENCE_ONLY_AXES`——边上**没有**声明侧，只能取自权威台账
#:     （Pack 的 `DocumentSourceSet` 的 `SourceDocumentKey`），因此只要求**非空在场**。
#:
#: 为什么把这两类分开写，而不是统一成「四轴逐轴比对」：把没有声明侧的两轴写成「比对」，
#: 就等于要求调用方把实际值**同时**当声明值传进来——那种比对恒真，是一道**假门**。假门比
#: 没有门更坏：它会让「四轴已闭合」成为一句没有实现支撑的断言。
#:
#: 为什么不退成只比 `material_id` 一轴：同一个 `document_id` 可以有两个版本、也可以挂在两个
#: Evidence Set 上，只比 ID 会把「同 ID 错版本」读成「新材料已核实」——那正是本边界要挡的
#: 反例④。
IDENTITY_AXES = ("company_id", "document_id", "document_version", "evidence_set_version")
COMPARED_AXES = ("document_id", "document_version")
PRESENCE_ONLY_AXES = ("company_id", "evidence_set_version")

#: :func:`current_state_support_is_extractive` 的**闭集**结论。
#:
#:   * `extractive`——存在一条四轴闭合的较新材料，其正文**连续包含**候选全文（`srsc-3` 起
#:     另要求：这次包含**没有**截掉命中那句源文自带的期间/范围限定）；
#:   * `unproven`——没有一条能做到。**这不是「候选是假的」**，只是「按现行授权判据证不出来」，
#:     调用方据此不写这条当前断言（与 `srsc-1` 同一出口）。它有两种来由，由 reason code 分开：
#:     `not_extractive_in_any_current_source`（压根没有材料包含它）与 `source_period_scope_dropped`
#:     （有材料包含它，但**只有**截掉限定语的那种包含）——两者的处置相同（不写），但读回来时
#:     意思不同：前者是「材料不足」，后者是「措辞口径变了」；
#:   * `mismatch`——某条边的声明身份与材料实际身份对不上（错版本 / 错 Evidence Set /
#:     身份缺失）。**fail-closed**：它说的是提案自身的身份链断了，不是「这条边弱一点」。
CURRENT_STATE_SUPPORT_RESULTS = ("extractive", "unproven", "mismatch")

#: 闭集 reason code（判据只输出这几种，不输出自由文本）。
CURRENT_STATE_SUPPORT_REASONS = (
    "extractive_contiguous_containment",
    "source_period_scope_dropped",
    "no_reading_supplied",
    "candidate_text_empty",
    "not_extractive_in_any_current_source",
    "axis_missing",
    "axis_mismatch_document_id",
    "axis_mismatch_document_version",
    "axis_mismatch_payload_hash",
)

#: 仅空白归一的紧化。**只**删空白，**不**动标点、**不**做同义或繁简归一。
#:
#: 这一点是判据的要害：候选与材料正文之间任何非空白的差异（包括一个「已」「不」「未」、
#: 一个逗号、一个数位）都会让包含关系**断掉**，从而落 `unproven`。判据因此**不需要**、
#: 也**不得**引入否定词表——「反例靠归一化之外的字面差异自然落空」，而不是靠枚举否定词。
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class CurrentStateSupportReading:
    """一条**可能充当当前锚**的支撑边，连同该材料在 Pack/后台侧**实际**解析出的身份。

    字段分三层，逐层都有说话的来源：

      * `company_id` / `document_id` / `document_version` / `evidence_set_version`——**实际侧**，
        取自 Pack 的 `DocumentSourceSet` 的 `SourceDocumentKey`（权威台账）。这是身份的唯一
        真值来源，不由边自报。
      * `declared_document_id` / `declared_document_version` / `declared_payload_hash`——**声明侧**，
        取自边自己的 `locator_ref` 容器与 `payload_hash`。只有这三项在边上有独立表面，
        因此只有这三项参与比对（见 :data:`COMPARED_AXES` 的说明）。
      * `actual_payload_hash`——实际侧的载体摘要；`locator_ref` 是命中那条边的精确定位（只作
        结论绑定与回查，不参与判否）；`reading_view` 是该材料的**真实正文读视图**
        （`wmctx-1` 解析所得）。

    本模块**不自己**读正文、不读库、不联网：正文与身份都由调用方解析好后作为参数传进来。
    """

    company_id: str
    document_id: str
    document_version: str
    evidence_set_version: str
    declared_document_id: str
    declared_document_version: str
    declared_payload_hash: str
    actual_payload_hash: str
    locator_ref: str
    reading_view: str


@dataclass(frozen=True)
class CurrentStateSupportVerdict:
    """结论 + 闭集理由 + 命中那条边的精确载荷/定位（候选 revision 一并绑定）。"""

    result: str
    reason_code: str
    candidate_revision: str
    matched_payload_hash: "str | None" = None
    matched_locator_ref: "str | None" = None


def _axis_defect(reading: CurrentStateSupportReading) -> "str | None":
    """这条边的四轴是否闭合；返回缺陷 reason code 或 `None`。

    顺序是**先缺失、再不一致**：身份读不出来时，「它是错的」与「它没给」是两件事，前者会被
    误读成「有人核对过并否决了」，因此缺失单独成一类（`axis_missing`）。
    """
    actual = {
        "company_id": reading.company_id,
        "document_id": reading.document_id,
        "document_version": reading.document_version,
        "evidence_set_version": reading.evidence_set_version,
    }
    declared = {
        "document_id": reading.declared_document_id,
        "document_version": reading.declared_document_version,
    }
    # ① 四轴必须都在场（实际侧）。
    for axis in IDENTITY_AXES:
        if not str(actual.get(axis, "") or "").strip():
            return "axis_missing"
    # ② 有声明侧的三项必须都在场。
    for key in COMPARED_AXES:
        if not str(declared.get(key, "") or "").strip():
            return "axis_missing"
    if not str(reading.declared_payload_hash or "").strip() \
            or not str(reading.actual_payload_hash or "").strip():
        return "axis_missing"
    # ③ 有声明侧的三项逐项比对（这是「同 ID 错版本」的唯一落点）。
    for axis in COMPARED_AXES:
        if declared[axis] != actual[axis]:
            return f"axis_mismatch_{axis}"
    if str(reading.declared_payload_hash) != str(reading.actual_payload_hash):
        return "axis_mismatch_payload_hash"
    return None


# ---------------------------------------------------------------------------
# `srsc-2` 的输入面：四轴台账与边上的声明容器
# ---------------------------------------------------------------------------
#
# 判据本身是纯函数（:func:`current_state_support_is_extractive` 只吃已经解析好的
# `CurrentStateSupportReading`）。这两条是**唯一**的取数处：实际侧取自权威台账，声明侧取自
# 边自己的 locator 容器。刻意放在本模块而不是调用方，是为了让「实际身份从哪来、声明身份从哪来」
# 只有一份答案——调用方各自解析就会出现两套口径，判据的结论也就跟着有两套。

#: locator 容器前缀（`TS.material_container_identity` 对 evidence 变体派生出的字面量）。
_EVIDENCE_CONTAINER_PREFIX = "evidence_document:"

#: 容器主体的形状：`{document_id}@{document_version}`。两段都不得为空，且都不得含 `@`/`#`——
#: 含了就说明这一段是**拼出来**的（`#` 是 `wmctx-1` 的定位后缀分隔符），拼出来的身份不能当声明侧。
_CONTAINER_BODY_RE = re.compile(r"^(?P<document_id>[^@#]+)@(?P<document_version>[^@#]+)$")


def document_axis_index(authority: Any) -> "dict[str, dict[str, str]]":
    """`{document_id: 四轴}`——本节各 Pack 来源集成员的**实际**身份台账（`srsc-2` 的实际侧）。

    与 :func:`document_roles` 同一份 `DocumentSourceSet.members`、同一条纪律，只是取出的是
    `SourceDocumentKey` 的四轴而不是角色。两个 fail-closed 逐条同形：

    * 成员缺任一轴——`SourceDocumentKey.__post_init__` 本就拒绝，这里再查一次是因为台账可能
      来自**反序列化**（不是构造）路径，而那时 `__post_init__` 已经跑过了；
    * 同一 `document_id` 在本节不同 Pack 上登着**不同**的四轴——与「同一文档两个角色」同理，
      按哪一份都是猜。

    空 `pack_set` 直接抛，理由与 :func:`document_roles` 同：这条轴上没有来源集时，调用方本就
    不得调用本判据（财务 / 附注 / 外部快照各走自己的资格链）。
    """
    pack_set = getattr(authority, "pack_set", None)
    packs = tuple(getattr(pack_set, "packs", ()) or ())
    if not packs:
        raise SourceRoleScopeError(
            f"四轴台账只对 topic Pack 权威有意义，实际 {type(authority).__name__}"
            "（无 pack_set.packs）：不得在财务/附注/外部快照链上调用")
    index: dict[str, dict[str, str]] = {}
    for pack in packs:
        source_set = getattr(pack, "source_set", None)
        for key, _role in tuple(getattr(source_set, "members", ()) or ()):
            axes = {axis: str(getattr(key, axis, "") or "") for axis in IDENTITY_AXES}
            document_id = axes["document_id"]
            if not document_id or any(not value.strip() for value in axes.values()):
                raise SourceRoleScopeError(
                    f"Pack {getattr(pack, 'pack_id', '')!r} 的来源集成员四轴不完整：{axes!r}"
                    "（身份不完整时逐轴比对没有意义，不得降级成「比对剩下的轴」）")
            known = index.get(document_id)
            if known is not None and known != axes:
                raise SourceRoleScopeError(
                    f"同一 document_id={document_id!r} 在本节不同 Pack 上四轴不一致："
                    f"{known!r} vs {axes!r}（按哪一份都是猜，fail-closed）")
            index[document_id] = axes
    return index


def declared_axes_from_locator(locator_ref: Any) -> "tuple[str, str] | None":
    """边自己的 `locator_ref` 里**声明**的 `(document_id, document_version)`；读不出返回 `None`。

    声明侧只有这一个面：容器由 `TS.material_container_identity` 确定性派生为
    `evidence_document:{document_id}@{document_version}`，`wmctx-1` 的 `material_locator_ref`
    再在后面接 `#block_span` / `#whole_payload`。本函数只拆这一段，**不**猜：

      * 不是 `evidence_document:` 容器（财务快照 / 外部快照）⇒ `None`：那两条轴上的当前态资格
        由各自 payload 与资格链承担，本判据不为它们代言；
      * 是 `evidence_document:` 却拆不出两轴 ⇒ **抛**（fail-closed）。拆不出说明容器被改过或
        被拼过，此时「读不出声明」与「声明为空」是两件事——静默退化成后者，就等于把一条
        本来会判 `mismatch` 的边读成 `unproven`，方向恰好相反。

    「取不到 owner」也返回 `None` 而不是抛：那是**没有声明面**（不是「声明面坏了」），与
    `whole_payload` 之外的定位形态同一处理——由调用方按缺失轴处理。
    """
    if not isinstance(locator_ref, Mapping):
        return None
    owner = str(locator_ref.get("owner") or "")
    if not owner:
        return None
    body = owner.split("#", 1)[0]
    if not body.startswith(_EVIDENCE_CONTAINER_PREFIX):
        return None
    matched = _CONTAINER_BODY_RE.match(body[len(_EVIDENCE_CONTAINER_PREFIX):])
    if matched is None:
        raise SourceRoleScopeError(
            f"evidence 容器 {owner!r} 拆不出 document_id@document_version："
            "声明身份读不出来时不得当成「没有声明」（默认成任一侧都会把结论送错方向）")
    return matched.group("document_id"), matched.group("document_version")


def _tight(text: Any) -> str:
    return _WHITESPACE_RE.sub("", str(text or ""))


def current_state_support_is_extractive(
        claim_text: str, *, candidate_revision: str,
        readings: Sequence[CurrentStateSupportReading]) -> CurrentStateSupportVerdict:
    """这条**当前态**断言，是否被某一份较新材料**严格抽取式**地支撑（`srsc-2` + `srsc-3`）。

    「严格抽取式」是本判据**唯一**能判 `extractive` 的形态，逐字定义如下：

        候选全文经**仅空白归一**后，是**单一**一份材料的 `reading_view` 经同样归一后的
        **连续子串**；该材料四轴与提案自报身份**逐轴闭合**；**且**（`srsc-3`）这次包含
        没有把命中那句源文自带的期间/范围限定**截在候选之外**。

    最后一条只对**候选覆盖到的那一句**判（:func:`source_period_scope_dropped`）：源句自带
    期间而候选自己没有 ⇒ 这条断言从「报告期内如此」变成了「一直如此」，是按现措辞**证不
    出来**的口径变更，落 `unproven`（`source_period_scope_dropped`）。反过来，源句本来就
    没有期间限定的稳定客观描述，在此判据下**恒判 `extractive`**——本条不会逼着描述句去补
    一个源文里没有的日期。命中多条时**先收干净的**：只要有一份材料把限定语一起包住，就判
    `extractive`，不因为别处存在截断式包含而连坐。

    判据**只**做这一件事。它**不做**、也**不得**做：

      * **最长公共子串 / 相似度**——包含关系是全有全无的，相似度会把「大部分相同」读成支撑；
      * **标点镜像**——标点不归一，因此换个逗号就断；
      * **封闭否定词表**——见 :data:`_WHITESPACE_RE` 的理由：极性差异靠字面差异自然落空；
      * **联合材料蕴含**——包含只在**单份** `reading_view` 内判定，跨材料的拼接不成立；
      * **调模型 / 读库 / 联网**——本函数是纯函数。

    指令点名的四类反例，在此形态下**构造性地**落空：

      1. **旧材料称 A／新材料称「已停止 A」**：候选「…公司A」不是「…公司已停止A」的连续子串
         （中间插了「已停止」）→ `unproven`。**不需要**认识「已停止」是不是否定词。
      2. **新材料只有同产品名**：候选是一条完整断言，材料里只有一个产品名 → 整句不包含 → `unproven`。
      3. **旧+新拼接才成立**：包含只在单份读视图内判，拼接被构造性拒绝 → `unproven`。
      4. **同 ID 错版本**：边的 `locator_ref` 容器声明 `document_id@document_version`，与实际
         台账两轴不一致 → `mismatch`（fail-closed），且门后最终读回复用同一判据，两处都拒。
         **错 Evidence Set** 在边上**没有**独立声明面，本判据只要求该轴在实际台账侧非空在场；
         它由 Pack 的 `DocumentSourceSet` 闭合，本判据**不假装**比对（见 :data:`COMPARED_AXES`）。
         如实写明这一点，是因为把它写成「四轴逐轴比对」会让调用方把实际值当声明值传进来，
         那种比对恒真——那是假门，不是门。

    `mismatch` **支配** `extractive`：只要边集里有一条边的身份链断了，对整个候选判
    `mismatch`。理由是身份链断裂说的是**提案自身**不可信（与 :func:`document_roles` 对
    「同一文档两个角色」抛错的理由同形），不是「这条边弱一点、换一条就好」。
    """
    revision = str(candidate_revision or "")
    if not revision:
        raise SourceRoleScopeError(
            "独立支撑结论必须绑定候选 revision：没有 revision，结论无法与「已审的那一版」对上")

    def _verdict(result: str, reason: str, reading: CurrentStateSupportReading | None = None) \
            -> CurrentStateSupportVerdict:
        return CurrentStateSupportVerdict(
            result=result, reason_code=reason, candidate_revision=revision,
            matched_payload_hash=(reading.actual_payload_hash if reading is not None else None),
            matched_locator_ref=(reading.locator_ref if reading is not None else None),
        )

    items = tuple(readings or ())
    if not items:
        return _verdict("unproven", "no_reading_supplied")

    needle = _tight(claim_text)
    if not needle:
        return _verdict("unproven", "candidate_text_empty")

    for reading in items:
        defect = _axis_defect(reading)
        if defect is not None:
            return _verdict("mismatch", defect, reading)

    # 包含命中分两类，**先收干净的**：候选被某一句话**连期间限定一起**包住时，
    # 那正是「可核验的期间还在」的形态，无论别的材料怎么截，都必须判 `extractive`。
    # 只有**所有**命中都建立在截掉限定语之上时，才落 `srsc-3` 的新结论。
    dropped: CurrentStateSupportReading | None = None
    for reading in items:
        view = _tight(reading.reading_view)
        if needle not in view:
            continue
        if source_period_scope_dropped(claim_text, reading.reading_view):
            if dropped is None:
                dropped = reading
            continue
        return _verdict("extractive", "extractive_contiguous_containment", reading)

    if dropped is not None:
        # **口径变了**，不是「材料不够新」：命中的那句源文自带期间/范围限定，候选把限定语
        # 截掉之后，读者只能读成持续至今的当前状态。按现措辞证不出来 ⇒ 不写这一句。
        # 命中边一并绑定（同样的 payload/locator），因为这是「哪条边引出的这一判」。
        return _verdict("unproven", "source_period_scope_dropped", dropped)

    return _verdict("unproven", "not_extractive_in_any_current_source")

"""Eval: M930-3 目标一 —— 标题导航机制（`anp-4` 六件套）的聚焦反例。

用法: python -m evals.test_m930_3_navigation_mechanics

覆盖 `document_structure/navigation.py` 的**实际改动面**，只测机制、不测内容：

1. 并列子形态 `navsubform-declared-label-conjunction`：整体标签在树上时**不**拆、
   缺席时才退回子形态，切分只按连词/枚举符且子形态自身也必须真的在树上
   （`subform_keys` / `NavigationIndex.key_match_forms`）；
2. 近分带 `tieband-one-synopsis-unit`：宽度是**一个最弱导航信号单位**（随树内出现的
   声明键数走），恰好差一个简介单位者进入带内、差两个单位者留 `outside_tie_band`；
3. 读根上提 `readroot-topmost-matched-ancestor`：近分带根先上提到**最上层命中祖先**
   再展开有界读集，且**不越过未命中的祖先**（上提范围由文档结构自己决定）；
4. 完整标签段 `navsegment-complete-label-segment`：标题命中按**整段相等**判定——
   长句披露标题里夹带该词只是片段命中，只作候选审计（`no_complete_label_segment`）；
5. 父节点回退 `navparent-contract-ancestor-label`：本层没有任何完整标签段命中时，
   用 **Contract 祖先声明**（question 文本 + 归属到该 question 的 topic 标题段）
   派生的键定位真实父节点，读它的有界正文；祖先层未 selected 就**原样退回本层**
   （不猜、不丢料）；
6. 标题段归属 `navtopic-segment-question-ownership`（`anp-4` 新增）：topic 标题是它
   名下**多个 question 的并列概括**，因此每个完整标签段必须先按分层多信号判据归属到
   **唯一一个** question，再随该 question 的子项进入祖先层——**不整段赋给每个 aspect**；
   多问争用或全无信号时**不强选**（整段丢弃并留下可审计原因）。本条同时给出真实
   Contract 上的正反例与一棵**非本公司**合成树上的正反例。
7. 读根资格 `navroot-label-anchored` + `navroot-subject-adjacency`（`anp-5` 新增）：
   近分带根**上提之后**的读根必须 (a) 本层键的匹配形态**落在标题的完整标签段上**
   ——前后缀 / 整键夹带的放宽只对**短标题**（字段名形态）生效，长句披露标题里切出的
   短片段不算；(b) 与 aspect 的**主体标签**（`requirement_text` 括号前的头部）共享
   ≥2 字最长公共子串。两道都不过即**整支丢弃**并逐条记原因；一支不剩时给
   `no_anchored_read_root`（读集为空），**不退回**读那些不合格的根。本节另给正例
   对照（主体与标题相邻的 aspect 照旧读到该章），以及
   `no_anchored_read_root`（有候选但定位不到这一栏）与 `low_confidence`（树上根本
   没有这一栏）之间的**可区分性**断言。

同时守住三类误召回风险：

- **无关标题负例**：声明键在树上完全不存在 → 候选可为空、读集为空、终态 `low_confidence`；
- **子形态误召回负例**：一个只含子形态词的**无关标题**会成为候选，但必须带
  `outside_tie_band` 被舍弃、**不得进读集**；
- **同词节点负例**（`anp-3` 新增）：标题里只**片段**含父节点词、与祖先层级无关的节点
  不得进读集（完整标签段判据），也不得把父节点回退当成"读什么都行"的放松；
- **跨 question 共享父节点负例**（`anp-4` 新增）：某个 question 归属到的 topic 标题段
  不得成为**别的 question** 子项的祖先层键——即"仅因共享父节点就把同一段文字当成
  另一个子项的有效材料"不得发生（真实文档上逐 aspect 断言，不靠统计口径）；
- **同名不同事的章节负例**（`anp-5` 新增）：一个章节只因名字里含某个字段词就充当
  另一个栏目的材料（`关联方` 章 → 客户集中度、`衍生产品` / `金融衍生产品名称` →
  产品与方案、`报告期内…内部控制…` → 报告期口径、长句披露标题 → 销售模式）不得发生；
  贴得上标签与被读成该 aspect 的那一节**是两件事**；
- **兄弟栏目字段名负例**（`anp-6` 新增）：一个 question 的文本是它名下各 aspect 字段的
  并列枚举，其中某个枚举项可能是**兄弟** aspect 声明的字段名（真实 Contract 的
  「客户当前集中度、前五大合计占比、关联方、集中度跨期变化、披露范围」里「关联方」即
  如此）。这种键**不得**充当本栏的祖先层键——祖先层刻意不做读根资格（祖先键本身就该是
  该 aspect 自己的祖先声明），一旦兄弟字段名混进去，一个只叫「十三、关联方及关联交易」
  的章节就会被整章读成集中度栏目的材料（r5 现场 13 个节点）。反向对照必须同时成立：
  question 层的整体表述（topic 标题段、没被任何兄弟声明的枚举项）照旧保留；
- **非 300750 正例**：仓库内版本化夹具（`evidence_gateway.FIXTURE_ROOT_RELPATH`）与
  **原真实文档**上各跑一遍同一机制：正例必须真的落到持有正文的那个节点/子树，
  且读集包含**近分带根子树之外**的正文节点（这正是上提要修的那部分）。

第 5 节在 live 样本上按**同一份冻结 Contract** 覆盖仓库内全部真实年报，并且必须显示
反例：「销售模式」不得把收入披露标题当读根，「采购」不得把关联交易表所在节点当读根。
规范检查只回答"规则是否公司无关"；**读到父节点正文只证明材料到达**，该 aspect 是否
covered 仍由正文、引用与 Contract 规则另行判定，本 eval 不作 coverage 断言。

夹具或 live 样本缺失时如实 skip（不静默算过）。不调 LLM、不联网、不写任何库、
不改任何产物；全部断言只读导航对象。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from document_structure import navigation as NAV
from document_structure import schema as S
from document_structure import versions as V

REPO = Path(__file__).resolve().parent.parent
CONTRACT_FP = "c" * 64


# ---------------------------------------------------------------------------
# 0. 测试侧夹具：真实 `TopicAspectRequirementSnapshot`（导航只读它的声明字段）
# ---------------------------------------------------------------------------

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _aspect(nav_key: str, *, aspect_id: str = "a1", required_fields=None):
    """一条真实 `TopicAspectRequirementSnapshot`。

    `required_fields` 与 `requirement_text` 可以不同（真实 Contract 里本就不必相同）：
    声明字段是**字段词表**，`requirement_text` 还可能带括号注释——`anp-5` 的主体标签
    正是从后者的括号前头部取。两者都取自测试侧的合成 Key，不是任何答案词。
    """
    from harness import topic_schema as TS
    return TS.TopicAspectRequirementSnapshot(
        aspect_id=aspect_id, question_id="q1", topic_id="t_probe",
        requirement_text=nav_key, kind="fact_set", producer_kind="company",
        execution_path="direct",
        required_fields=(nav_key,) if required_fields is None else required_fields,
        coverage_rules=("direct_support", "minimum_sources"), complete_set_rule="",
        evidence_requirement_ids=(TS.EvidenceRequirementRef(
            requirement_id="er-" + aspect_id, contract_sha256=_sha("c"),
            requirement_fingerprint=_sha("r"), schema_version="1",
            source_classes=()),),
        source_policy_ref=TS.SourcePolicyRef(
            policy_id="sp", policy_version="v1", content_fingerprint=_sha("p")),
        time_scope="period", display_tier="required_body", content_role="paragraph",
        missing_policy="none", blocking_policy=(), applicability_policy=None,
        impact_scope=("subject",), output_destination="body", derived_from=(),
        business_review_status="none", contract_version="v1",
        contract_sha256=_sha("c"), canonical_fingerprint=_sha("canon"),
        dependency_fingerprint=_sha("dep"))


def _siblings_of(labels, aspect_ids):
    """测试侧的兄弟项排除集：这些合成用例是**单 aspect question**，没有兄弟项。

    逐 aspect 给空元组是"真的没有兄弟项"，与"忘了传排除集"不同——后者会被
    `build_navigation_profile` 直接 fail-closed 拒掉。
    """
    if labels is None:
        return None
    return {aspect_id: () for aspect_id in aspect_ids}


def _entry(nav_key: str, *, aspect_id: str = "a1", ancestor_labels=None,
           required_fields=None):
    """由测试侧 aspect 派生**真实**导航条目（走 `_entry_for`，不自造字段）。"""
    profile = NAV.build_navigation_profile(
        (_aspect(nav_key, aspect_id=aspect_id, required_fields=required_fields),),
        contract_version="v1",
        contract_fingerprint=CONTRACT_FP, ancestor_labels=ancestor_labels,
        sibling_keys=_siblings_of(ancestor_labels, (aspect_id,)))
    return profile, profile.entries[0]


def _legacy_sibling_off(entry, old_keys, *, contract_version: str,
                        contract_fingerprint: str = CONTRACT_FP):
    """按**排除集关闭**（`anp-5` 形态）重建同一条 aspect 的对照条目与单条 profile。

    只用于**对照读数**（前后比较）：它是一份**对照输入**，不是任何现行派生结果。
    `derivation` 里逐字写明这一点，且规则版本仍是现行版本——旧规则版本的 profile 已被
    schema 层 fail-closed 拒收（`_check_version`），因此本对照证明的是「兄弟项排除这一条
    输入改变了读后果」，不冒充旧版本产物。
    """
    legacy = S.AspectNavigationEntry(
        aspect_id=entry.aspect_id + "#sibling-off", question_id=entry.question_id,
        topic_id=entry.topic_id, content_role=entry.content_role,
        display_tier=entry.display_tier, nav_keys=entry.nav_keys,
        parent_keys=old_keys, subject_head=entry.subject_head,
        expected_forms=entry.expected_forms,
        derivation=entry.derivation + (
            f"{NAV.NAV_SIBLING_ITEM_RULE_ID}:本对照条目按排除集**关闭**的 `anp-5` 形态"
            "构造祖先层键；这是对照输入，不是任何现行派生结果",))
    return legacy, S.AspectNavigationProfile.create(
        contract_version=contract_version,
        contract_fingerprint=contract_fingerprint, entries=(legacy,))


# ---------------------------------------------------------------------------
# 1. 合成标题树：只测机制，不含任何公司 / 页码 / 答案词
# ---------------------------------------------------------------------------

#: 合成树以「路径标题元组 → 行号」给出；行号即文档顺序（严格递增）。
#: 该树刻意做成"命中父章节 + 更命中子节点"的形状，用来逼出读根上提。
_SYNTH_PATHS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("第一章总述",), 0),
    (("第一章总述", "主要产品及用途"), 1),
    (("第一章总述", "主要产品及用途", "主要产品明细"), 2),
    (("第二章业务概览",), 3),
    (("第二章业务概览", "主营业务情况"), 4),
    (("其他业务",), 5),
    (("业务记录表",), 6),
    (("环境自行监测方案",), 7),
)
#: 合成简介：只给两个节点简介，且文本**必须逐字符来自该节点**（抽取式）。
_SYNTH_SYNOPSIS = {
    ("第一章总述", "主要产品及用途", "主要产品明细"): "主要产品明细：甲类、乙类",
    ("其他业务",): "其他业务收入为参考资料",
}


def _synth_tree():
    """合成真实 `DocumentOutline` + 简介，返回 (index, {路径: node_id})。"""
    loc = S.derive_document_outline_locator(
        page_layout_id="pl-navmechan000001", document_id="DOC-NAV-MECHANICS",
        algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
        schema_version=V.OUTLINE_SCHEMA_VERSION)

    def _anchor(line: int):
        y = 10.0 + line * 12.0
        return (1, line, (10.0, y, 200.0, y + 10.0))

    # 节点 id 先由派生函数算出（`OutlineNode.create` 的父引用/层级必须一次给对）。
    node_id_of = {
        path: S.derive_node_id(document_outline_locator=loc, structural_path=path,
                               source_anchor=_anchor(line), title=path[-1])
        for path, line in _SYNTH_PATHS}
    nodes = []
    siblings: dict[tuple[str, ...], int] = {}
    for path, line in _SYNTH_PATHS:
        parent_key = path[:-1]
        ordinal = siblings.get(parent_key, 0)
        siblings[parent_key] = ordinal + 1
        nodes.append(S.OutlineNode.create(
            document_outline_locator=loc,
            parent_id=None if not parent_key else node_id_of[parent_key],
            title=path[-1], title_normalized=path[-1], structural_path=path,
            source_anchor=_anchor(line),
            child_ids=tuple(node_id_of[p] for p, _l in _SYNTH_PATHS
                            if p[:-1] == path),
            ordinal=ordinal))
    outline = S.DocumentOutline.create(
        document_id="DOC-NAV-MECHANICS", document_version="sha256-navmechanics0",
        page_layout_id="pl-navmechan000001", nodes=tuple(nodes))

    by_id = {n.structural_path: n.node_id for n in outline.nodes}
    synopses = []
    for path, text in _SYNTH_SYNOPSIS.items():
        node_id = by_id[path]
        synopses.append(S.NavigationSynopsis.available(node_id=node_id, snippets=(
            S.SynopsisSnippet(span_id="os-" + _sha("syn:" + node_id)[:16],
                              snippet_index=0, char_start=0, char_end=len(text),
                              text=text),)))
    # 每个节点一条"span"（索引只记数，不持有文本）：正文挂在被上提的父节点上。
    span_node_ids = [by_id[("第一章总述", "主要产品及用途")]] * 3 \
        + [by_id[("第一章总述", "主要产品及用途", "主要产品明细")]] \
        + [by_id[("第二章业务概览", "主营业务情况")]]
    index = NAV.NavigationIndex(outline, tuple(synopses),
                                span_node_ids=span_node_ids)
    return index, by_id, outline


def _read_set_under(index, read_roots) -> set[str]:
    return {n for root in read_roots for n in index.subtree_of(root)}


# ---------------------------------------------------------------------------
# 2. 合成树上的机制正例 / 反例
# ---------------------------------------------------------------------------

def _mechanics(check, expect_error, details) -> None:
    index, by_id, outline = _synth_tree()
    P = by_id[("第一章总述", "主要产品及用途")]
    C = by_id[("第一章总述", "主要产品及用途", "主要产品明细")]
    G = by_id[("第一章总述",)]
    R2 = by_id[("第二章业务概览",)]
    A = by_id[("第二章业务概览", "主营业务情况")]
    B = by_id[("其他业务",)]
    D = by_id[("业务记录表",)]
    E = by_id[("环境自行监测方案",)]
    check(index.unavailable_reason is None, "合成树上导航索引可用（有简介）")

    # ---- 2.1 并列子形态：整体在树上不拆、缺席才退回子形态 ----------------
    key_subform = "产品与方案"
    check(index.key_match_forms("主要产品及用途") == ("主要产品及用途",),
          "整体标签就在树上时**不**拆成并列子形态（只看整体）")
    check(index.key_match_forms(key_subform) == ("产品", "方案"),
          "整体标签缺席时退回并列子形态（两个子形态都真的在树上）")
    check(index.key_match_forms("董事长的私人游艇") == (),
          "树上完全没有的键没有匹配形态（等价于缺席，不猜）")
    check(NAV.subform_keys("产品") == (),
          "没有并列连词的标签不切子形态")
    check(NAV.subform_keys("上市或募资事件") == ("上市", "募资事件"),
          "并列连词「或」按声明标签切分（不做中文分词）")
    check(NAV.subform_keys("（一）产品与方案") == (),
          "含括号注释的片段不进子形态；剩余片段不足两个时整体返回空")
    check(NAV.subform_keys("这是一个很长很长的部件与方案") == (),
          "超过 `_LABEL_MAX_CHARS` 的片段被丢弃；剩余不足两个即返回空")
    check(NAV.subform_keys("甲与乙") == (),
          "单字子形态信息量太低，不作为导航键（`_LABEL_MIN_CHARS`）")
    expect_error(lambda: NAV.subform_keys(""), NAV.NavigationError,
                 "空导航键必须拒", needle="非空字符串")
    expect_error(lambda: NAV.subform_keys(None), NAV.NavigationError,
                 "非字符串导航键必须拒")
    expect_error(lambda: NAV.aspect_nav_keys("收入", ""), NAV.NavigationError,
                 "非序列 required_fields 必须拒")
    check(NAV.aspect_nav_keys((), "这是一段远超八个字的要求文本片段") == (),
          "自由文本不做中文分词：超过 `_LABEL_MAX_CHARS` 的片段不是字段名，丢弃")
    check(NAV.aspect_nav_keys(("收入占比", "收入"), "") == ("收入", "收入占比"),
          "导航键按确定性顺序去重（同一键只出现一次）")

    # ---- 2.2 读根上提：命中父章节不被丢掉 --------------------------------
    profile, entry = _entry("主要产品")
    decision = NAV.navigate(index, entry, profile=profile)
    check(decision.status == "selected"
          and decision.selected_node_id == P,
          "父子同时命中时，选中的是**最上层命中祖先**（不是更命中的子节点）")
    check(decision.read_root_node_ids == (P,),
          "读根 = 上提后的最上层命中祖先")
    check(set(decision.read_node_ids) == {P, C},
          "读集由读根子树展开（父章节与更深的命中子节点都在读集里）")
    check(G not in set(decision.read_node_ids),
          "上提**不越过**未命中的祖先（范围由文档结构自己决定，不一路爬到卷首）")
    check(decision.subtree_node_ids == index.subtree_of(P),
          "selected 的 subtree 就是该读根的子树（可解释）")
    check(index.span_count_by_node[P] == 3 and P in set(decision.read_node_ids),
          "挂在父章节自身的正文 span 随上提重新进入读集（这正是修掉的那部分）")

    # 反例：顶层祖先自己也命中 → 上提到它为止（上提方向没有别的地板）
    profile_top, entry_top = _entry("总述", aspect_id="a-top")
    top_decision = NAV.navigate(index, entry_top, profile=profile_top)
    check(top_decision.read_root_node_ids == (G,)
          and set(top_decision.read_node_ids) == set(index.subtree_of(G)),
          "顶层祖先本身命中时上提到顶层祖先，读集覆盖整棵子树")
    check(top_decision.selected_node_id == G,
          "上提后的读根就是 selected（二者不脱钩）")

    # ---- 2.3 近分带：宽度 = 一个最弱导航信号单位 -------------------------
    present = index.keys_present(("业务",))
    check(present == ("业务",), "合成树上「业务」键确有匹配形态")
    width = NAV.tie_band_width(len(present))
    check(abs(width - NAV.SCORE_WEIGHT_SYNOPSIS / (
        len(present) * (NAV.SCORE_WEIGHT_TITLE + NAV.SCORE_WEIGHT_PATH
                        + NAV.SCORE_WEIGHT_SYNOPSIS))) < 1e-12,
          "带宽恰为一个简介命中单位（分母随树内出现的声明键数走）")
    expect_error(lambda: NAV.tie_band_width(0), NAV.NavigationError,
                 "total_keys=0 必须拒")
    expect_error(lambda: NAV.tie_band_width(True), NAV.NavigationError,
                 "total_keys 为 bool 必须拒")

    profile_b, entry_b = _entry("业务", aspect_id="a-biz")
    biz = NAV.navigate(index, entry_b, profile=profile_b)
    scores = {c.node_id: c.score for c in biz.ranked}
    check(scores[A] == max(scores.values()), "「业务」键下最强候选是命中父路径的子节点")
    # 候选分数按已发布精度（6 位小数）落库，比较必须用该精度而不是浮点全精度。
    check(abs(max(scores.values()) - scores[B] - width) < 1e-6,
          "候选 B 与最优**恰好**差一个简介单位（带宽边界，按发布精度比较）")
    check({A, B} == set(biz.band_node_ids),
          "同分或仅差一个简介单位的候选一起进近分带")
    check(biz.read_root_node_ids == (R2, B),
          "近分带根各自上提后去重：命中父路径者上提到父节点，另一个保持自身")
    check(set(biz.read_node_ids) == _read_set_under(index, (R2, B)),
          "读集 = 各读根子树的并集（顺序即候选强度优先）")
    check(biz.unread_total == 0 and biz.unread_node_ids == (),
          "未截断时不得列出任何未读节点")

    d_cand = {c.node_id: c for c in biz.ranked}[D]
    check(d_cand.discard_reason == "outside_tie_band"
          and d_cand.in_read_set is False,
          "差两个简介单位的候选被明确舍弃（原因取自封闭集合）")
    check(all(c.discard_reason in NAV.DISCARD_REASONS
              for c in biz.ranked if c.discard_reason is not None),
          "所有舍弃原因都来自 `DISCARD_REASONS`")
    check(all((c.discard_reason is None) == c.in_read_set for c in biz.ranked),
          "`in_read_set` 与 `discard_reason` 互补且封闭")
    check(biz.ranked[0].reasons and all(c.reasons for c in biz.ranked),
          "每条候选都带可审计的依据（不是只有分数）")

    # ---- 2.4 预算：截断是预算事实，截掉的节点如实进未读 ------------------
    tight = NAV.NavigationLimits(max_subtree_nodes=1)
    tight_decision = NAV.navigate(index, entry_b, profile=profile_b, limits=tight)
    check(len(tight_decision.read_node_ids) == 1
          and tight_decision.unread_total == 2,
          "读集由 `max_subtree_nodes` 截断，未读数如实记录（不静默丢弃）")
    tight_by_id = {c.node_id: c for c in tight_decision.ranked}
    check(tight_by_id[A].discard_reason == "over_budget"
          and tight_by_id[B].discard_reason == "over_budget",
          "被预算截掉的候选舍弃原因是 `over_budget`（不是相关性判断）")
    expect_error(lambda: NAV.navigate(index, entry_b, profile=profile_b,
                                      limits={"max_subtree_nodes": 1}),
                 NAV.NavigationError, "非 NavigationLimits 必须拒")

    # ---- 2.5 子形态误召回：无关标题成为候选但**不得**进读集 --------------
    profile_s, entry_s = _entry(key_subform, aspect_id="a-sub")
    sub = NAV.navigate(index, entry_s, profile=profile_s)
    check(sub.status == "selected" and sub.selected_node_id == P,
          "子形态命中下选中的仍是真实命中父章节（不是只含子形态词的无关标题）")
    check(E not in set(sub.read_node_ids),
          "只含子形态词的**无关标题**（环境自行监测方案）不得进读集")
    e_cand = {c.node_id: c for c in sub.ranked}.get(E)
    check(e_cand is not None and e_cand.discard_reason == "outside_tie_band",
          "该无关标题仍如实出现在候选里，并带明确的舍弃原因（可审计，不静默）")
    check(set(sub.read_node_ids) == _read_set_under(index, (P,)),
          "读集仍只覆盖读根子树（没有因为误召回而扩大）")

    # ---- 2.6 无关标题负例：键完全不在树上 → 可见但不读 -------------------
    profile_n, entry_n = _entry("董事长的私人游艇", aspect_id="a-none")
    none_decision = NAV.navigate(index, entry_n, profile=profile_n)
    check(none_decision.status == "fallback"
          and none_decision.fallback_reason == "low_confidence",
          "声明键在树上完全不存在 → 显式 `low_confidence` fallback")
    check(none_decision.ranked == () and none_decision.selected_node_id is None,
          "没有候选时不得猜 node（`selected_node_id` 为空）")
    check(none_decision.read_node_ids == ()
          and none_decision.read_root_node_ids == ()
          and none_decision.band_node_ids == (),
          "低置信时读集 / 读根 / 近分带全为空（fail-closed，不扩大读取范围）")
    check(all(len(index.keys_present(e.nav_keys)) == 0
              for e in (entry_n,)), "该 aspect 的树内匹配键数为 0（原因是可命名的）")
    check(none_decision.fallback_reason in NAV.FALLBACK_REASONS,
          "fallback 原因取自封闭集合")

    # ---- 2.7 结构不可用：显式 fallback，且原因已登记 ----------------------
    blind = NAV.NavigationIndex(outline, ())
    check(blind.unavailable_reason is not None, "无简介的索引必须给出不可用原因")
    blind_decision = NAV.navigate(blind, entry_b, profile=profile_b)
    check(blind_decision.status == "fallback"
          and blind_decision.fallback_reason == "structurally_unavailable"
          and blind_decision.read_node_ids == (),
          "结构不可用时走显式 fallback（不读、不猜）")

    # ---- 2.8 只读边界与身份 ----------------------------------------------
    check(all(not d.produces_coverage() for d in
              (decision, biz, sub, none_decision, blind_decision)),
          "导航终态恒不产生 coverage")
    check(not hasattr(index, "materials") and not hasattr(index, "evidence_ids"),
          "导航索引不持有材料 / Evidence id")
    check(set(decision.read_node_ids) <= set(index.node_ids)
          and set(decision.read_root_node_ids) <= set(index.node_ids),
          "读集与读根都是本树上的真实 node")
    check(biz.to_dict()["read_root_node_ids"] == list(biz.read_root_node_ids),
          "`to_dict` 如实带出读根（运行侧 trace 与决策对象不脱钩）")

    expect_error(lambda: NAV.navigate(index, entry_b, profile=profile_n),
                 NAV.NavigationError, "aspect 不在 profile 内必须拒",
                 needle="不属于 profile")

    # ---- 2.9 规则版本与依赖指纹 -----------------------------------------
    check(V.PROFILE_RULE_VERSION == "anp-9",
          f"导航规则版本已升到 anp-9（得到 {V.PROFILE_RULE_VERSION!r}）")
    check(V.PROFILE_SCHEMA_VERSION == "anps-4",
          f"导航条目 schema 已升到 anps-4（得到 {V.PROFILE_SCHEMA_VERSION!r}）")
    # 旧版本必须被**显式**登记为 legacy：不是"认不出来"，而是"认得出、且要求重算"。
    check(all(V.classify_schema_version("PROFILE_RULE_VERSION", old) == "legacy"
              for old in ("anp-1", "anp-2", "anp-3", "anp-4", "anp-5", "anp-6",
                          "anp-7", "anp-8")),
          "anp-1 … anp-8 被显式识别为 legacy（旧导航语义不得冒充现行；"
          "anp-3 是「topic 标题整段赋给每个 aspect」的旧祖先层键来源，"
          "anp-4 是「只按整段判据、不看读根资格」的旧读根语义，"
          "anp-5 是「question 文本的每个枚举项整段交给该 question 每个 aspect」的"
          "旧祖先层键来源，"
          "anp-6 是「读集只由读根子树展开、没有主体补读」的旧读集语义，"
          "anp-7 是「祖先层键只按枚举分隔符切、切不开并列连词」的旧祖先层键来源——"
          "祖先声明是 `供应商当前集中度与集中度跨期变化` 这类并列复合标签时 "
          "`parent_keys` 为空，祖先层回退与主体补读两道门一起关掉，"
          "anp-8 是「没有上提补读这条规则」的旧读集语义——正文挂在子节上的父章节"
          "（`四、主营业务分析`）自有正文近零，连同子树里 `（1）动力业务`…那些整段漏读）")
    check(V.classify_schema_version("PROFILE_RULE_VERSION", "anp-9") == "current"
          and V.classify_schema_version("PROFILE_SCHEMA_VERSION", "anps-4")
          == "current",
          "anp-9 / anps-4 是现行版本（唯一在跑的语义）")
    check(V.classify_schema_version("PROFILE_SCHEMA_VERSION", "anps-2") == "legacy"
          and V.classify_schema_version("PROFILE_SCHEMA_VERSION", "anps-1")
          == "legacy",
          "anps-1 / anps-2 被显式识别为 legacy（缺 `parent_keys` 的条目 wire）")
    joined = "\n".join(entry.derivation)
    for rule in (NAV.NAV_KEY_RULE_ID, NAV.NAV_SUBFORM_RULE_ID, NAV.NAV_FORM_RULE_ID,
                 NAV.TIE_BAND_RULE_ID, NAV.READ_ROOT_RULE_ID,
                 NAV.NAV_SEGMENT_RULE_ID, NAV.NAV_PARENT_RULE_ID,
                 NAV.NAV_TOPIC_SEGMENT_RULE_ID, NAV.NAV_SIBLING_ITEM_RULE_ID,
                 NAV.NAV_PARENT_JOIN_RULE_ID):
        check(rule in joined, f"导航条目派生里登记了规则条目 {rule!r}")
    check(not hasattr(NAV, "DESCEND_RATIO") and not hasattr(NAV, "_descend"),
          "旧的「下潜」规则已删除（同一语义不留两套实现）")
    check("READ_ROOT_RULE_ID" in NAV.__all__,
          "读根规则条目已导出（外部只读清点可见）")
    check("NAV_LIFT_RULE_ID" in NAV.__all__
          and NAV.NAV_LIFT_RULE_ID != NAV.NAV_SUPPLEMENT_RULE_ID
          and "NAV_LIFT_RULE_ID" not in joined,
          "上提补读是**另一条**规则条目（id 与主体补读不同，且不进导航条目派生："
          "它不参与条目构造，只参与读集补读）")
    check("no_complete_label_segment" in NAV.DISCARD_REASONS,
          "新的舍弃原因已登记在封闭集合里（不是自由字符串）")
    # 未提供祖先声明（合成用例）时，父节点回退必须**显式关闭**，而不是静默"没有父节点"。
    check("父节点回退**关闭**" in joined and entry.parent_keys == (),
          "无祖先声明时派生里写明父节点回退关闭，且 parent_keys 为空（可区分）")
    details.append(f"INFO: 合成树 score 明细 {sorted(scores.items())}")


# ---------------------------------------------------------------------------
# 2b. `anp-3` 新机制：完整标签段 + Contract 祖先声明的父节点回退
# ---------------------------------------------------------------------------

#: 第二棵合成树：只服务"子项没有独立标题、只有同词片段"的形状。
#: 「主营业务情况」= 子项标题（aspect 键只以**片段**出现）；「主营业务情况说明」=
#: 与祖先层级**无关**的同词节点（必须被完整标签段判据挡住）。树里不含任何公司/页码词。
_PARENT_PATHS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("第二章业务概览",), 0),
    (("第二章业务概览", "主营业务情况"), 1),
    (("主营业务情况说明",), 2),
    (("第三章其他资料",), 3),
    (("第三章其他资料", "产品明细"), 4),
)
_PARENT_SYNOPSIS = {("第三章其他资料",): "第三章其他资料：本节无业务信息"}


def _parent_tree():
    loc = S.derive_document_outline_locator(
        page_layout_id="pl-navparent000001", document_id="DOC-NAV-PARENT",
        algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
        schema_version=V.OUTLINE_SCHEMA_VERSION)

    def _anchor(line: int):
        y = 10.0 + line * 12.0
        return (1, line, (10.0, y, 200.0, y + 10.0))

    node_id_of = {
        path: S.derive_node_id(document_outline_locator=loc, structural_path=path,
                               source_anchor=_anchor(line), title=path[-1])
        for path, line in _PARENT_PATHS}
    nodes = []
    siblings: dict[tuple[str, ...], int] = {}
    for path, line in _PARENT_PATHS:
        parent_key = path[:-1]
        ordinal = siblings.get(parent_key, 0)
        siblings[parent_key] = ordinal + 1
        nodes.append(S.OutlineNode.create(
            document_outline_locator=loc,
            parent_id=None if not parent_key else node_id_of[parent_key],
            title=path[-1], title_normalized=path[-1], structural_path=path,
            source_anchor=_anchor(line),
            child_ids=tuple(node_id_of[p] for p, _l in _PARENT_PATHS
                            if p[:-1] == path),
            ordinal=ordinal))
    outline = S.DocumentOutline.create(
        document_id="DOC-NAV-PARENT", document_version="sha256-navparent00001",
        page_layout_id="pl-navparent000001", nodes=tuple(nodes))
    by_id = {n.structural_path: n.node_id for n in outline.nodes}
    synopses = []
    for path, text in _PARENT_SYNOPSIS.items():
        synopses.append(S.NavigationSynopsis.available(node_id=by_id[path], snippets=(
            S.SynopsisSnippet(span_id="os-" + _sha("psyn:" + by_id[path])[:16],
                              snippet_index=0, char_start=0, char_end=len(text),
                              text=text),)))
    #: 父节点**自身**持有正文 span（这正是"读到父节点正文"的必要条件）。
    span_node_ids = [by_id[("第二章业务概览",)]] * 2 \
        + [by_id[("第二章业务概览", "主营业务情况")]] \
        + [by_id[("主营业务情况说明",)]]
    index = NAV.NavigationIndex(outline, tuple(synopses),
                                span_node_ids=span_node_ids)
    return index, by_id


def _parent_fallback(check, expect_error, details) -> None:
    index, by_id = _parent_tree()
    R2 = by_id[("第二章业务概览",)]
    A = by_id[("第二章业务概览", "主营业务情况")]
    X = by_id[("主营业务情况说明",)]
    Y = by_id[("第三章其他资料",)]
    check(index.unavailable_reason is None, "父节点树上的导航索引可用")

    # ---- 2b.1 完整标签段：整段相等才是"这个标题是关于它的" ---------------
    check(NAV.title_label_segments("3、经营模式")[0] == "经营模式",
          "编号前缀（`3、`）被剥掉后，整段才成为完整标签段")
    check(NAV.title_label_segments("（2）销售模式")[0] == "销售模式",
          "括号编号同样只剥前缀，段本身保持逐字符完整")
    check("销售模式"
          not in NAV.title_label_segments("（2）占公司营业收入或营业利润10%以上的销售模式的情况"),
          "长句披露标题里**夹带**该词不构成完整标签段（片段命中不算命中）")
    check("一" not in NAV.title_label_segments("一体化业务模式"),
          "以中文数字开头的实词不被误剥编号（`_ENUM_PREFIX` 只认真正的编号形态）")
    check(NAV.title_label_segments("采购与生产") == ("采购与生产", "采购", "生产"),
          "并列连词切出的段也是完整标签段（整段相等仍成立）")
    dup_segments = NAV.title_label_segments("采购、采购")
    check(len(dup_segments) == len(set(dup_segments)),
          "同一标签段只出现一次（确定性去重）")
    expect_error(lambda: NAV.title_label_segments(None), NAV.NavigationError,
                 "非字符串标题必须拒")
    check(index.key_segment_forms("主营业务", A) == (),
          "只以片段出现的键在该节点上没有完整标签段命中")
    check(index.key_segment_forms("主营业务情况", A) == ("主营业务情况",),
          "整段相等的键在该节点上有完整标签段命中")
    check(index.key_segment_forms("主营业务", X) == ()
          and index.key_has_segment_match("主营业务情况说明"),
          "同词节点只在**自己**的整段上命中（不靠共享片段）")
    # 关键的不对称：`anp-2` 的并列子形态只服务**本层三路打分**，祖先层的整段判据
    # **不**展开子形态——否则「产品与方案」会经子形态「产品」命中无关标题（真实募集
    # 说明书上已出现，见第 4 节）。两条规则各有其职，不是同一条的两份实现。
    detail_node = by_id[("第三章其他资料", "产品明细")]
    check(NAV.subform_keys("产品与方案") == ("产品", "方案")
          and index.key_match_forms("产品与方案") == ("产品",),
          "本层的并列子形态规则一个字未改（打在树上的子形态仍算命中形态）")
    check(index.key_segment_forms("产品与方案", detail_node) == ()
          and NAV.segment_hits(index, ("产品与方案",), detail_node) == (),
          "祖先层的整段判据**不**展开子形态：子形态命中不构成完整标签段命中")

    # ---- 2b.2 父节点回退：Contract 祖先声明派生祖先层键 -------------------
    labels = {"a-parent": ("第二章业务概览",)}
    profile, entry = _entry("主营业务", aspect_id="a-parent",
                            ancestor_labels=labels)
    check(entry.nav_keys == ("主营业务",), "本层导航键仍只来自 Contract 声明字段")
    check(entry.parent_keys == ("第二章业务概览",),
          "祖先层键由 Contract 祖先声明确定性派生（topic 标题）")
    check(not (set(entry.parent_keys) & set(entry.nav_keys)),
          "祖先层键与本层键互斥（复用本层键会让父节点变成同义反复）")
    check(NAV.parent_nav_keys(("主营业务",), ("主营业务",)) == (),
          "祖先声明等于本层键时派生为空（不制造同义反复）")
    expect_error(lambda: NAV.parent_nav_keys((), "不是序列"), NAV.NavigationError,
                 "祖先声明必须为序列")
    expect_error(lambda: NAV.parent_nav_keys((), ("",)), NAV.NavigationError,
                 "空祖先声明必须拒")
    check(NAV.NAV_PARENT_RULE_ID in "\n".join(entry.derivation)
          and NAV.NAV_SEGMENT_RULE_ID in "\n".join(entry.derivation),
          "派生里登记了祖先层与整段判据两条规则条目")

    decision = NAV.navigate(index, entry, profile=profile)
    check(decision.status == "selected" and decision.selected_node_id == R2,
          f"本层无完整标签段命中时回退到真实父节点（得到 {decision.status}"
          f"/{decision.selected_node_id}）")
    check(decision.read_root_node_ids == (R2,),
          "读根 = 父节点自身（祖先层同样走上提与近分带机制）")
    check(A in set(decision.read_node_ids),
          "读集覆盖父节点子树的正文节点（不是只读一个空标题）")
    check(index.span_count_by_node[R2] > 0
          and R2 in set(decision.read_node_ids),
          "父节点**自身**持有的有界正文进入读集（这正是要修的丢料点）")
    check(X not in set(decision.read_node_ids),
          "与祖先层级无关的**同词节点**不得进读集（完整标签段判据）")
    x_cand = {c.node_id: c for c in decision.ranked}.get(X)
    check(x_cand is not None
          and x_cand.discard_reason == "no_complete_label_segment"
          and x_cand.in_read_set is False,
          "该同词节点仍如实出现在候选里，并带登记过的舍弃原因（可审计）")
    a_cand = {c.node_id: c for c in decision.ranked}.get(A)
    check(a_cand is not None and a_cand.in_read_set is True,
          "子项节点作为父节点子树成员仍在读集内（回退不丢子项）")
    check(Y not in set(decision.read_node_ids),
          "树里的无关节点不受父节点回退影响（回退不放大读取范围）")
    check(all(c.discard_reason in NAV.DISCARD_REASONS
              for c in decision.ranked if c.discard_reason is not None),
          "祖先层的舍弃原因同样取自封闭集合")
    check(not decision.produces_coverage(),
          "父节点回退同样**不**产生任何 coverage（读到正文 ≠ 该子项已 covered）")

    # ---- 2b.3 祖先层不成立时：原样退回本层（不猜、不丢料） ---------------
    same_profile, entry_none = _entry("主营业务", aspect_id="a-parent-none")
    none_decision = NAV.navigate(index, entry_none, profile=same_profile)
    miss_profile, entry_miss = _entry(
        "主营业务", aspect_id="a-parent-miss",
        ancestor_labels={"a-parent-miss": ("业务与经营情况",)})
    check(entry_miss.parent_keys == ("业务与经营情况",),
          "祖先声明派生出键（长度合规、且不含本层键）")
    check(not index.key_has_segment_match("业务与经营情况"),
          "该祖先键在本树里没有完整标签段命中（这正是要退的场景）")
    miss_decision = NAV.navigate(index, entry_miss, profile=miss_profile)
    check((miss_decision.status, miss_decision.selected_node_id,
           miss_decision.read_root_node_ids, miss_decision.read_node_ids)
          == (none_decision.status, none_decision.selected_node_id,
              none_decision.read_root_node_ids, none_decision.read_node_ids),
          "祖先层定位不到时**逐位**退回本层结果（不猜、也不丢料）")
    check(miss_decision.selected_node_id == A,
          "退回后本层自身的定位结果照旧（本层机制一个字未改）")

    # ---- 2b.4 声明缺失必须与「没有父节点」可区分：fail-closed ------------
    expect_error(
        lambda: NAV.build_navigation_profile(
            (_aspect("主营业务", aspect_id="a-other"),), contract_version="v1",
            contract_fingerprint=CONTRACT_FP, ancestor_labels=labels,
            sibling_keys=_siblings_of(labels, ("a-other",))),
        NAV.NavigationError, "祖先声明未覆盖该 aspect 时必须拒（不得静默降级）",
        needle="祖先声明未覆盖")
    #: 只给其一即拒：否则"兄弟项排除关闭"会伪装成"输入已配好"（`anp-6` 静默失效）。
    expect_error(
        lambda: NAV.build_navigation_profile(
            (_aspect("主营业务", aspect_id="a-half"),), contract_version="v1",
            contract_fingerprint=CONTRACT_FP, ancestor_labels=labels),
        NAV.NavigationError,
        "只给祖先声明、不给兄弟项排除集必须拒（两样输入是一体的）",
        needle="同时给出或同时省略")
    expect_error(
        lambda: NAV.build_navigation_profile(
            (_aspect("主营业务", aspect_id="a-other2"),), contract_version="v1",
            contract_fingerprint=CONTRACT_FP, ancestor_labels={"a-other2": ("x",)},
            sibling_keys={}),
        NAV.NavigationError, "兄弟项排除集未覆盖该 aspect 时必须拒（不得静默降级）",
        needle="兄弟项排除集未覆盖")
    expect_error(
        lambda: NAV.build_navigation_profile(
            (_aspect("主营业务"),), contract_version="v1",
            contract_fingerprint=CONTRACT_FP, ancestor_labels=(("第二章业务概览",),)),
        NAV.NavigationError, "祖先声明必须为映射", needle="映射")
    expect_error(
        lambda: S.AspectNavigationEntry(
            aspect_id="a-dup", question_id="q1", topic_id="t", content_role="paragraph",
            display_tier="required_body", nav_keys=("采购",), parent_keys=("采购",),
            expected_forms=(), derivation=()),
        Exception, "parent_keys 与 nav_keys 重叠必须拒")

    # ---- 2b.5 `contract_ancestor_labels`：只读冻结 Contract 的父子归属 ----
    class _Q:
        def __init__(self, question, aspects, question_id="q1"):
            self.question, self.aspects = question, aspects
            self.question_id = question_id

    class _T:
        def __init__(self, title, questions, topic_id="t1"):
            self.title, self.questions = title, questions
            self.topic_id = topic_id

    class _Sec:
        def __init__(self, topics):
            self.topics = topics

    class _A:
        def __init__(self, aspect_id, required_fields=(), requirement_text=""):
            self.aspect_id = aspect_id
            self.required_fields = required_fields
            self.requirement_text = requirement_text

    class _Contract:
        def __init__(self, sections):
            self.sections = sections

    fake = _Contract((_Sec((_T("业务概览", (_Q("经营情况如何？", (_A("asp-1"), _A("asp-2"))),)),)),))
    check(NAV.contract_ancestor_labels(fake)
          == {"asp-1": ("经营情况如何？",), "asp-2": ("经营情况如何？",)},
          "祖先声明按冻结 Contract 的父子归属逐 aspect 取值（question 文本；"
          "topic 标题**只在归属成立时**才逐段进入）")
    expect_error(lambda: NAV.contract_ancestor_labels(object()),
                 NAV.NavigationError, "非 Contract 形状必须拒（不猜）",
                 needle="sections")
    expect_error(lambda: NAV.contract_ancestor_labels(_Contract(())),
                 NAV.NavigationError, "空 sections 必须拒")
    expect_error(
        lambda: NAV.contract_ancestor_labels(
            _Contract((_Sec((_T("", (_Q("q", (_A("asp-1"),)),)),)),))),
        NAV.NavigationError, "空标题必须拒（缺祖先声明不得与「无父节点」混同）")
    dup_aspects = (_A("dup"), _A("dup"))
    dup_contract = _Contract((_Sec((_T("t", (_Q("q", dup_aspects),)),)),))
    expect_error(lambda: NAV.contract_ancestor_labels(dup_contract),
                 NAV.NavigationError, "重复 aspect_id 必须拒", needle="重复")

    # ---- 2b.6 topic 标题段的 question 归属（`anp-4` 新增）-------------------
    #: 两棵**合成**（非本公司、非本 Contract）的 topic。第一棵喂能唯一归属的段；
    #: 第二棵刻意做成"两个 question 在同一最强层上争同一个段"（与真实 Contract 的
    #: 「客户与供应商集中度」同形）。
    synth_topic = _T(
        "业务概览、经营模式、供应商集中度",
        (_Q("收入与利润构成如何？", (_A("asp-a", ("业务概览",)),), question_id="q-a"),
         _Q("采购模式与生产模式", (_A("asp-b", ("采购模式",)),), question_id="q-b"),
         _Q("供应商当前集中度", (_A("asp-c", ("集中度变化",)),), question_id="q-c")),
        topic_id="t-synth")
    rows = {r.segment: r for r in NAV.topic_segment_assignments(synth_topic)}
    check(set(rows) == {"业务概览", "经营模式", "供应商集中度"},
          f"只有标签尺寸的段进入归属（得到 {sorted(rows)}）")
    check(rows["业务概览"].reason == "topic-segment-assigned"
          and rows["业务概览"].question_id == "q-a"
          and rows["业务概览"].tier == "aspect_field_substring",
          f"段「业务概览」只被 q-a 的声明字段接住（最弱层也能唯一归属），得到 "
          f"{rows['业务概览'].reason}/{rows['业务概览'].tier}"
          f"/{rows['业务概览'].question_id}")
    check(rows["经营模式"].reason == "topic-segment-assigned"
          and rows["经营模式"].question_id == "q-b"
          and rows["经营模式"].tier == "shared_substring",
          "段「经营模式」只经最长公共子串「模式」连到 q-b（q-a/q-c 的文本与它无"
          f"公共子串），得到 {rows['经营模式'].tier}/{rows['经营模式'].question_id}")
    check(rows["供应商集中度"].reason == "topic-segment-assigned"
          and rows["供应商集中度"].question_id == "q-c",
          "段「供应商集中度」只与 q-c 共享 ≥2 字公共子串")
    synth_labels = NAV.contract_ancestor_labels(
        _Contract((_Sec((synth_topic,)),)))
    check(synth_labels["asp-a"] == ("收入与利润构成如何？", "业务概览")
          and synth_labels["asp-b"] == ("采购模式与生产模式", "经营模式")
          and synth_labels["asp-c"] == ("供应商当前集中度", "供应商集中度"),
          f"归属到某个 question 的标题段只进**它自己的**子项祖先声明（得到 {synth_labels}）")
    a_keys = NAV.parent_nav_keys(("业务概览",), synth_labels["asp-a"])
    b_keys = NAV.parent_nav_keys(("采购模式",), synth_labels["asp-b"])
    check("经营模式" not in a_keys and "经营模式" in b_keys,
          f"「经营模式」只成为 q-b 子项的祖先层键（a={a_keys} b={b_keys}）")
    check("业务概览" not in b_keys and "业务概览" not in
          NAV.parent_nav_keys(("集中度变化",), synth_labels["asp-c"]),
          "被别的 question 归属到的标题段不得成为本 question 子项的祖先层键")

    #: 争用：两个 question 在同一最强层（shared_substring）上争同一个段。
    contended_topic = _T(
        "客户与供应商集中度",
        (_Q("客户当前集中度", (_A("asp-x", ("客户占比",)),), question_id="q-x"),
         _Q("供应商当前集中度", (_A("asp-y", ("供应商占比",)),), question_id="q-y")),
        topic_id="t-contended")
    contended = {r.segment: r for r in NAV.topic_segment_assignments(contended_topic)}
    check(contended["客户"].reason == "topic-segment-assigned"
          and contended["客户"].tier == "label_containment"
          and contended["客户"].question_id == "q-x",
          "互为子串（「客户」⊆「客户当前集中度」）是比公共子串更强的一层，唯一归属 q-x")
    check(contended["供应商集中度"].reason == "topic-segment-contended"
          and contended["供应商集中度"].question_id is None
          and contended["供应商集中度"].tier is None
          and set(contended["供应商集中度"].contenders) == {"q-x", "q-y"},
          "段「供应商集中度」与 q-x/q-y **同层**（各共享「集中度」/「供应商」）→ "
          "不强选、整段丢弃")
    contended_labels = NAV.contract_ancestor_labels(
        _Contract((_Sec((contended_topic,)),)))
    check(contended_labels["asp-y"] == ("供应商当前集中度",)
          and "供应商集中度" not in contended_labels["asp-y"],
          f"争用的段**不**进任何子项（得到 {contended_labels['asp-y']}）")
    check(all(r.reason in NAV.TOPIC_SEGMENT_ASSIGN_REASONS
              and (r.tier is None or r.tier in NAV.TOPIC_SEGMENT_ASSIGN_TIERS)
              and (r.question_id is None) == (r.reason != "topic-segment-assigned")
              for r in tuple(rows.values()) + tuple(contended.values())),
          "归属行的原因 / 层都取自封闭集合，且只有被采用的段才带 question_id")
    check(NAV.longest_common_substring_length("经营模式", "采购模式与生产模式") == 2
          and NAV.longest_common_substring_length("业务概览", "客户当前集中度") == 0,
          "最长公共子串是纯字符串长度（「模式」=2；无公共子串=0）")
    check(NAV.topic_segment_assignments(
              _T("与业务无关的标题段", (_Q("另一件事", (_A("asp-x"),)),),
                 topic_id="t-none"))[0].reason == "topic-segment-no-signal",
          "各层全无信号时如实记 topic-segment-no-signal（不强选）")
    check(NAV.topic_segment_assignments(
              _T("一二三四五六七八九", (_Q("一二三四五六七八九", (_A("asp-y"),)),),
                 topic_id="t-long")) == (),
          "超过标签长度的整体标题**不是标签段**，不进入归属（与 parent_nav_keys 同界）")
    expect_error(lambda: NAV.topic_segment_assignments(_T("t", (), topic_id="t-x")),
                 NAV.NavigationError, "无 question 的 topic 必须拒（不猜）")
    expect_error(lambda: NAV.topic_segment_assignments(
        _T("t", (_Q("q", (_A("a"),)),), topic_id="")),
        NAV.NavigationError, "缺 topic_id 必须拒（归属审计需要主题身份）")
    expect_error(
        lambda: NAV.contract_topic_segment_assignments(
            _Contract((_Sec((_T("t", (_Q("q", (_A("a"),)),), topic_id="dup"),
                             _T("t2", (_Q("q", (_A("b"),)),), topic_id="dup"))),))),
        NAV.NavigationError, "重复 topic_id 必须拒", needle="重复")
    expect_error(
        lambda: NAV.TopicSegmentAssignment(
            topic_id="t", topic_title="T", segment="S", question_id="q",
            tier="exact_label_segment", reason="topic-segment-no-signal"),
        NAV.NavigationError, "归属审计行必须自洽（只有 assigned 才带 question_id/tier）")
    details.append("INFO: 父节点回退树 score 明细 "
                   f"{[(c.title, c.score, c.discard_reason) for c in decision.ranked]}")

    # ---- 2b.7 兄弟项排除（`anp-6`）：兄弟栏目的字段名不得充当本栏父节点 -------
    # 真实形状：一个 question 的文本是它名下各 aspect 字段的**并列枚举**，其中
    # 「关联方」是**兄弟** aspect 声明的字段。`anp-5` 把每个枚举项整段交给该 question
    # 的每个 aspect，「关联方」于是成了集中度类子项的祖先层键；而祖先层**刻意**不做
    # 读根资格（祖先键本身就该是该 aspect 自己的祖先声明，再要求一次主体邻接是重复
    # 判据），于是一个只叫「十三、关联方及关联交易」的章节被整章读成集中度栏目的材料
    # （r5 现场：该章 13 个节点全进读集）。本节的词都取自 Contract 的声明字段形态，
    # 不含公司 / 页码 / 答案词——规则只做「整键相等即排除」这一条纯字符串判据。
    cust_q = _Q(
        "客户当前集中度、前五大合计占比、关联方、集中度跨期变化、披露范围",
        (_A("asp-current", ("客户当前集中度", "前五大合计占比", "关联方"),
            "客户当前集中度（前五名客户合计销售金额占年度销售总额比例）"),
         _A("asp-change", ("集中度跨期变化",),
            "客户集中度跨期变化（仅在有可比期间时评估）"),
         _A("asp-anon", ("客户匿名",),
            "客户匿名或未披露（不披露客户名称时的处理）")),
        question_id="q1")
    labels_c, siblings_c = NAV.contract_ancestor_inputs(
        _Contract((_Sec((_T("客户集中度", (cust_q,), topic_id="t_probe"),)),)))
    check(set(labels_c) == set(siblings_c)
          == {"asp-current", "asp-change", "asp-anon"},
          f"祖先声明与兄弟项排除集同源同键集（{sorted(labels_c)} / "
          f"{sorted(siblings_c)}）")
    check(any("关联方" in text for text in labels_c["asp-change"])
          and labels_c["asp-change"][0].startswith("客户当前集中度、前五大合计占比"),
          "question 文本的并列枚举项照旧整条进入祖先声明文本（本规则不删输入）")
    check(set(siblings_c["asp-change"])
          == set(NAV.aspect_nav_keys(("客户当前集中度", "前五大合计占比", "关联方"), ""))
          | set(NAV.aspect_nav_keys(("客户匿名",), "")),
          "排除集 = 同一 question 内**其余** aspect 的声明键并集（得到 "
          f"{siblings_c['asp-change']}）")
    check(set(siblings_c["asp-current"])
          == set(NAV.aspect_nav_keys(("集中度跨期变化",), ""))
          | set(NAV.aspect_nav_keys(("客户匿名",), "")),
          "排除集只含**别人**的键：自己声明的字段不会把自己排除掉")
    check(NAV.contract_sibling_keys(_Contract(
        (_Sec((_T("客户集中度", (cust_q,), topic_id="t_probe"),)),)))
        == siblings_c,
        "只读审计视图 `contract_sibling_keys` 与一次遍历的产出逐键相同")
    check(all(isinstance(k, str) and k != "" for keys_ in siblings_c.values()
              for k in keys_),
          "排除集里只有非空字符串（形状不符即 fail-closed，不静默降级成空集）")

    own_change = NAV.aspect_nav_keys(("集中度跨期变化",), "")
    check("关联方" in NAV.parent_nav_keys(own_change, labels_c["asp-change"]),
          "定点对照（`anp-5` 形态、排除集为空）：「关联方」确实成为本栏祖先层键")
    fixed_keys = NAV.parent_nav_keys(own_change, labels_c["asp-change"],
                                     siblings_c["asp-change"])
    check("关联方" not in fixed_keys
          and "客户当前集中度" not in fixed_keys
          and "前五大合计占比" not in fixed_keys,
          f"兄弟栏目的字段名一个都不进本栏祖先层键（得到 {fixed_keys}）")
    check("集中度跨期变化" not in fixed_keys,
          "本栏自己的声明键不进祖先层（`NAV_PARENT_RULE_ID` 的互斥判据一个字未改）")
    check("客户集中度" in fixed_keys and "披露范围" in fixed_keys,
          "question 层的整体表述（归属到该 question 的 topic 标题段 `客户集中度`、"
          f"没被任何兄弟声明的枚举项 `披露范围`）照旧保留（得到 {fixed_keys}）")

    # 读后果：同一棵树、同一条 aspect，只切换**输入形态**。
    index_q, by_id_q = _qual_tree()
    REL_Q = by_id_q[("十三、关联方及关联交易",)]
    profile_q = NAV.build_navigation_profile(
        (_aspect("客户当前集中度（前五名客户合计销售金额占年度销售总额比例）",
                 aspect_id="asp-current",
                 required_fields=("客户当前集中度", "前五大合计占比", "关联方")),
         _aspect("客户集中度跨期变化（仅在有可比期间时评估）",
                 aspect_id="asp-change", required_fields=("集中度跨期变化",)),
         _aspect("客户匿名或未披露（不披露客户名称时的处理）",
                 aspect_id="asp-anon", required_fields=("客户匿名",))),
        contract_version="v1", contract_fingerprint=CONTRACT_FP,
        ancestor_labels=labels_c, sibling_keys=siblings_c)
    entry_fixed = next(e for e in profile_q.entries if e.aspect_id == "asp-change")
    entry_current = next(e for e in profile_q.entries if e.aspect_id == "asp-current")
    check(entry_fixed.parent_keys == fixed_keys and "关联方" not in entry_fixed.parent_keys,
          f"真实条目派生出来的祖先层键同样不含兄弟字段名（得到 "
          f"{entry_fixed.parent_keys}）")
    check(any(line.startswith(NAV.NAV_SIBLING_ITEM_RULE_ID)
              and "整键相等即排除" in line for line in entry_fixed.derivation),
          "派生说明里如实登记兄弟项排除这一条规则（可审计）")

    #: 对照条目：**同一 aspect**，祖先层键按 `anp-5` 形态（排除集关掉）算出来，
    #: 读后果与现行形态并排比较（`_legacy_sibling_off` 写明这是对照输入）。
    old_keys = NAV.parent_nav_keys(own_change, labels_c["asp-change"])
    legacy_entry, legacy_profile = _legacy_sibling_off(
        entry_fixed, old_keys, contract_version="v1")
    legacy_decision = NAV.navigate(index_q, legacy_entry, profile=legacy_profile)
    fixed_decision = NAV.navigate(index_q, entry_fixed, profile=profile_q)
    current_decision = NAV.navigate(index_q, entry_current, profile=profile_q)
    check(legacy_decision.status == "selected"
          and legacy_decision.selected_node_id == REL_Q
          and REL_Q in legacy_decision.read_node_ids,
          "反例（排除集关闭的输入）：只要「关联方」进了祖先层键，"
          f"「十三、关联方及关联交易」就整章被读成该栏材料（{legacy_decision.status}"
          f"/{[index_q.normalized_title(n) for n in legacy_decision.read_root_node_ids]}）")
    check(fixed_decision.status == "fallback"
          and fixed_decision.read_node_ids == ()
          and fixed_decision.read_root_node_ids == (),
          "同树同 aspect、只开兄弟项排除：该章不再进读集（"
          f"{fixed_decision.status}/{fixed_decision.fallback_reason}）")
    check(fixed_decision.fallback_reason != "structurally_unavailable",
          "这个空读集是**真的定位不到**，不是索引不可用（两种缺口必须可区分）")
    check(entry_current.nav_keys
          == tuple(sorted(("客户当前集中度", "前五大合计占比", "关联方")))
          and "关联方" not in entry_current.parent_keys,
          "正例对照：**声明**了 `关联方` 的那一栏，本层键照旧含它，祖先层键照旧不含它")
    check(NAV.read_root_disqualification(index_q, entry_current, REL_Q)
          == "not_subject_adjacent"
          and current_decision.status == "fallback"
          and current_decision.fallback_reason == "no_anchored_read_root",
          "该栏走到该章也只是候选：读根资格（主体邻接）把它挡住"
          f"（{current_decision.status}/{current_decision.fallback_reason}）")
    check(NAV.segment_hits(index_q, ("关联方",), REL_Q) == ("关联方",),
          "该章确实带完整标签段「关联方」——所以「进了祖先层键就会读到它」这件事"
          "本来就只差兄弟项排除这一道")
    details.append(
        f"INFO: 兄弟项排除树 asp-change 祖先层键 关={old_keys} 开={fixed_keys}；"
        f"读后果 关={legacy_decision.status}"
        f"/{[index_q.normalized_title(n) for n in legacy_decision.read_root_node_ids]}"
        f" 开={fixed_decision.status}/{fixed_decision.fallback_reason}；"
        f"asp-current={current_decision.status}/{current_decision.fallback_reason}")


# ---------------------------------------------------------------------------
# 2c. `anp-5` 新机制：读根资格（贴标签 `navroot-label-anchored` +
#     主体邻接 `navroot-subject-adjacency`）
# ---------------------------------------------------------------------------

#: 第三棵合成树：只服务"**哪个标题有资格当读根**"的形状。标题字串取自真实披露里的
#: 常见形态（短字段名标题 / 长句披露标题 / 同名不同事的章节），它们是**测试侧样本事实**，
#: 不参与任何生产规则——生产规则只有 `_form_is_anchored` / `subject_adjacency`
#: 那几条纯字符串判据（无词表、无公司 / 页码 / 答案词）。
#: 树刻意保持**扁平**：本节只判资格，近分带与上提由 2./2b. 节与真实文档断言覆盖，
#: 这里不复测。
_QUAL_PATHS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("第一章经营情况",), 0),
    (("第一章经营情况", "（1）营业收入构成"), 1),
    (("第一章经营情况",
      "（2）占公司营业收入或营业利润10%以上的行业、产品或地区、销售模式的情况"), 2),
    (("（3）主要产品及用途",), 3),
    (("十一、衍生产品情况",), 4),
    (("1、金融衍生产品名称",), 5),
    (("1、报告期内的内部控制制度建设及实施情况",), 6),
    (("十三、关联方及关联交易",), 7),
)
#: 简介只给一个节点，且文本逐字符是该节点标题并续上它自己的中性尾句（合成树上没有
#: 正文，这是本树能提供的最诚实的抽取式简介）。它不含任何本节用到的导航键新词。
_QUAL_SYNOPSIS = {
    ("1、报告期内的内部控制制度建设及实施情况",):
        "报告期内的内部控制制度建设及实施情况：本报告期未发生重大变化",
}


def _qual_tree():
    """读根资格树的真实 `DocumentOutline` + 简介，返回 (index, {路径: node_id})。"""
    loc = S.derive_document_outline_locator(
        page_layout_id="pl-navqual00000001", document_id="DOC-NAV-QUAL",
        algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
        schema_version=V.OUTLINE_SCHEMA_VERSION)

    def _anchor(line: int):
        y = 10.0 + line * 12.0
        return (1, line, (10.0, y, 200.0, y + 10.0))

    node_id_of = {
        path: S.derive_node_id(document_outline_locator=loc, structural_path=path,
                               source_anchor=_anchor(line), title=path[-1])
        for path, line in _QUAL_PATHS}
    nodes = []
    siblings: dict[tuple[str, ...], int] = {}
    for path, line in _QUAL_PATHS:
        parent_key = path[:-1]
        ordinal = siblings.get(parent_key, 0)
        siblings[parent_key] = ordinal + 1
        nodes.append(S.OutlineNode.create(
            document_outline_locator=loc,
            parent_id=None if not parent_key else node_id_of[parent_key],
            title=path[-1], title_normalized=path[-1], structural_path=path,
            source_anchor=_anchor(line),
            child_ids=tuple(node_id_of[p] for p, _l in _QUAL_PATHS
                            if p[:-1] == path),
            ordinal=ordinal))
    outline = S.DocumentOutline.create(
        document_id="DOC-NAV-QUAL", document_version="sha256-navqual00000001",
        page_layout_id="pl-navqual00000001", nodes=tuple(nodes))
    by_id = {n.structural_path: n.node_id for n in outline.nodes}
    synopses = []
    for path, text in _QUAL_SYNOPSIS.items():
        synopses.append(S.NavigationSynopsis.available(node_id=by_id[path], snippets=(
            S.SynopsisSnippet(span_id="os-" + _sha("qsyn:" + by_id[path])[:16],
                              snippet_index=0, char_start=0, char_end=len(text),
                              text=text),)))
    #: 只有两个节点假定持有正文 span（本节的断言不依赖 span，只记数）。
    index = NAV.NavigationIndex(outline, tuple(synopses), span_node_ids=(
        by_id[("第一章经营情况", "（1）营业收入构成")],
        by_id[("（3）主要产品及用途",)]))
    return index, by_id


def _read_root_qualification(check, details) -> None:
    """`anp-5` 读根资格：贴标签（只对短标题放宽）+ 主体邻接，含正例对照。"""
    index, by_id = _qual_tree()
    REV = by_id[("第一章经营情况", "（1）营业收入构成")]
    LONG = by_id[("第一章经营情况",
                  "（2）占公司营业收入或营业利润10%以上的行业、产品或地区、销售模式的情况")]
    PROD = by_id[("（3）主要产品及用途",)]
    DERIV = by_id[("十一、衍生产品情况",)]
    DERIV_NAME = by_id[("1、金融衍生产品名称",)]
    IC = by_id[("1、报告期内的内部控制制度建设及实施情况",)]
    REL = by_id[("十三、关联方及关联交易",)]
    check(index.unavailable_reason is None, "读根资格树上导航索引可用（有简介）")

    # ---- 2c.1 键身份：编号前缀不进键（键是字段名，不是带编号的标题）--------------
    check(NAV.aspect_nav_keys(("（1）产品甲",), "（1）产品甲") == ("产品甲",),
          "带编号前缀的声明字段派生出的键是**字段名**（`（1）` 不进键身份）")
    check(NAV.aspect_subject_head("（1）产品甲") == ""
          and NAV.aspect_subject_head(
              "客户当前集中度（前五名客户合计销售金额占年度销售总额比例）")
          == "客户当前集中度",
          "主体标签取括号前的头部；头部为空（整条 requirement_text 就是字段名）"
          "时如实给空串")

    # ---- 2c.2 贴标签：放宽只对**短标题**（字段名形态）生效 ----------------------
    check(NAV.label_anchored_keys(index, ("收入",), REV) == ("收入",),
          "短标题 `（1）营业收入构成` 完整含声明字段 `收入` → 贴得上标签")
    check(NAV.label_anchored_keys(index, ("产品与方案",), PROD) == ("产品与方案",),
          "短标题 `（3）主要产品及用途` 的标签段 `主要产品` 以子形态 `产品` 收尾"
          " → 贴得上标签")
    check(NAV.label_anchored_keys(index, ("产品与方案",), DERIV) == ()
          and NAV.label_anchored_keys(index, ("产品与方案",), DERIV_NAME) == (),
          "`十一、衍生产品情况` / `1、金融衍生产品名称` 贴不上 `产品与方案`"
          "（`产品` 既不是整段、也不是短标签段的前 / 后缀）")
    check(NAV.label_anchored_keys(index, ("报告期",), IC) == (),
          "长披露标题 `1、报告期内的内部控制制度建设及实施情况` 贴不上 `报告期`"
          "（长标题是句子，句子里的同词夹带不算）")
    check(NAV.label_anchored_keys(index, ("销售模式",), LONG) == (),
          "长句披露标题里切出的片段 `销售模式的情况` 贴不上 `销售模式`"
          "（**片段短**不等于**标题是字段名**）")
    check(NAV.label_anchored_keys(index, ("产品与方案",), LONG) == ("产品与方案",),
          "同一长标题里**整段相等**的标签段 `产品` 仍算贴标签"
          "（整段相等是最强判据，不因标题长而作废——不额外收紧）")
    key_rev = NAV.aspect_nav_keys(("（1）营业收入构成",), "（1）营业收入构成")
    check(key_rev == ("营业收入构成",)
          and NAV.label_anchored_keys(index, key_rev, REV) == key_rev,
          "带编号前缀的声明字段在**派生时**就剥成字段名，照旧贴得上它自己标题的标签段"
          f"（得到 {key_rev}）")

    # ---- 2c.3 主体邻接：贴得上标签 ≠ 就是该 aspect 的那一节 --------------------
    profile_rel, entry_rel = _entry("关联方及关联交易", aspect_id="a-qual-rel")
    rel_ok = NAV.navigate(index, entry_rel, profile=profile_rel)
    check(entry_rel.subject_head == "关联方及关联交易"
          and NAV.read_root_disqualification(index, entry_rel, REL) is None
          and rel_ok.status == "selected" and rel_ok.read_root_node_ids == (REL,),
          f"正例对照：主体与标题相邻的 aspect 照旧读到该章"
          f"（{rel_ok.status}/{rel_ok.fallback_reason}）")
    profile_cust, entry_cust = _entry(
        "客户当前集中度（前五名客户合计销售金额占年度销售总额比例）",
        aspect_id="a-qual-cust", required_fields=("关联方",))
    cust = NAV.navigate(index, entry_cust, profile=profile_cust)
    check(entry_cust.nav_keys == ("关联方",)
          and entry_cust.subject_head == "客户当前集中度",
          f"该 aspect 的声明键是 `关联方`、主体标签是 `客户当前集中度`"
          f"（得到 {entry_cust.nav_keys}/{entry_cust.subject_head!r}）")
    check(NAV.label_anchored_keys(index, entry_cust.nav_keys, REL) == ("关联方",),
          "`关联方` 确实是该章标题的完整短标签段（**贴标签**这一道判据成立）")
    check(NAV.subject_adjacency(index, entry_cust.subject_head, REL) == 0
          and NAV.read_root_disqualification(index, entry_cust, REL)
          == "not_subject_adjacent",
          "但该章与主体 `客户当前集中度` 零邻接 → 不作读根（两道判据各自独立）")
    check(cust.status == "fallback"
          and cust.fallback_reason == "no_anchored_read_root"
          and cust.read_node_ids == () and cust.read_root_node_ids == (),
          f"关联方披露所在章节不得充当客户集中度的材料（{cust.status}"
          f"/{cust.fallback_reason}，读集 {len(cust.read_node_ids)}）")

    # ---- 2c.4 读根资格不通过 → 整支丢弃 + 显式 fallback（不退回不合格的根）------
    profile_s, entry_s = _entry("销售模式", aspect_id="a-qual-sales")
    check(NAV.read_root_disqualification(index, entry_s, LONG)
          == "not_label_anchored",
          "长句披露标题当 `销售模式` 的读根 → 原因 `not_label_anchored`")
    sales = NAV.navigate(index, entry_s, profile=profile_s)
    check(sales.status == "fallback"
          and sales.fallback_reason == "no_anchored_read_root"
          and sales.read_node_ids == () and sales.read_root_node_ids == (),
          f"`销售模式` 在本树上读集为空（{sales.status}/{sales.fallback_reason}）")
    sales_cand = {c.node_id: c for c in sales.ranked}
    check(sales_cand.get(LONG) is not None
          and sales_cand[LONG].discard_reason == "not_label_anchored",
          "被刷掉的读根仍带**可审计的舍弃原因**出现在候选列表里（不静默消失）")
    profile_none, entry_none = _entry("董事长的私人游艇", aspect_id="a-qual-none")
    none_decision = NAV.navigate(index, entry_none, profile=profile_none)
    check(none_decision.status == "fallback"
          and none_decision.fallback_reason == "low_confidence"
          and none_decision.ranked == () and none_decision.read_node_ids == (),
          "整棵树里根本没有这一栏 → `low_confidence`、连候选都没有")
    check(sales.fallback_reason != none_decision.fallback_reason,
          "「有候选、但没有一支读根定位得到这一栏」与「树上根本没有这一栏」是两种"
          f"可区分的缺口（{sales.fallback_reason} vs "
          f"{none_decision.fallback_reason}）")

    # ---- 2c.5 主体标签推导不出时**不加**邻接判据（不凭空造主体来收紧自己）------
    profile_h, entry_h = _entry("（1）营业收入构成", aspect_id="a-qual-head")
    check(entry_h.nav_keys == ("营业收入构成",) and entry_h.subject_head == "",
          f"整条 requirement_text 就是字段名 → 推导不出主体标签"
          f"（{entry_h.nav_keys}/{entry_h.subject_head!r}）")
    check(NAV.read_root_disqualification(index, entry_h, REV) is None,
          "推导不出主体标签时**不加**邻接判据（只有贴标签一道）")
    derivation_text = "\n".join(entry_h.derivation)
    check(NAV.NAV_ROOT_ANCHOR_RULE_ID in derivation_text
          and NAV.NAV_ROOT_SUBJECT_RULE_ID in derivation_text,
          "派生说明里如实写明读根资格的两道判据（可审计）")
    check(any("推导不出主体标签" in line for line in entry_h.derivation),
          "主体标签为空时派生说明如实写明该道判据不加（不是静默跳过）")

    per_node = [
        (index.normalized_title(n), list(index.label_segments(n)),
         NAV.subject_adjacency(index, "客户当前集中度", n))
        for n in (REV, LONG, PROD, DERIV, DERIV_NAME, IC, REL)]
    details.append("INFO: 读根资格树 逐节点（标题 / 完整标签段 / 与「客户当前集中度」的"
                   f"邻接）={per_node}")
    details.append(
        f"INFO: 读根资格树 终态 销售模式={sales.fallback_reason} "
        f"客户集中度={cust.fallback_reason} 关联方正例={rel_ok.status}"
        f"/{[index.normalized_title(r) for r in rel_ok.read_root_node_ids]} "
        f"树上没有的栏={none_decision.fallback_reason}")


# ---------------------------------------------------------------------------
# 2d. `anp-7` 新机制：主体补读（`navsupp-nearest-ancestor-subject-body`）
# ---------------------------------------------------------------------------

#: 第四棵合成树：形状照着"本层键把 aspect 送到**别的栏目**那一节，而真正的主体节一个字
#: 都没读"摆（真实反例是两份年报的 `main_business`）。标题是通用栏目名（Contract 字段词与
#: 常见披露小标题），它们是**测试侧样本事实**，不参与任何生产规则；生产规则只有
#: `is_ordered_variant` / 短标题形态 / 自有正文量下限那几条纯字符串判据（无词表，不含
#: 公司名、页码、表号或答案词）。正文**只给字符数**——索引仍然拿不到任何文本。
_SUPP_PATHS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("第一节经营概况",), 0),
    (("第一节经营概况", "一、报告期内从事的主要业务"), 1),
    (("第一节经营概况", "一、报告期内从事的主要业务", "1、主要业务"), 2),
    (("第一节经营概况", "一、报告期内从事的主要业务", "2、主要产品及其用途"), 3),
    (("第一节经营概况", "一、报告期内从事的主要业务", "3、经营模式"), 4),
    (("第二节财务分析",), 5),
    (("第二节财务分析", "四、主营业务分析"), 6),
    (("第二节财务分析", "四、主营业务分析", "2、收入与成本"), 7),
    (("第二节财务分析", "四、主营业务分析", "2、收入与成本", "（1）营业收入构成"), 8),
    (("第三节其他资料",), 9),
    (("第三节其他资料", "五、主动债务管理"), 10),
    (("第三节其他资料", "4、经模用式"), 11),
)

#: 逐节点**自有正文量**（字符数；索引只记数、不持有文本）。四类形状各就各位：
#: - `1、主要业务`：必须补进来的主体节（真实两份年报上分别是 441 / 348 字）；
#: - `四、主营业务分析` 与两个父章节：**没有**自有正文（正文在子节上）→ 被正文下限挡住；
#: - `五、主动债务管理`：有正文，但与祖先键**只共享首末两字**→ 被非平凡判据挡住；
#: - `4、经模用式`：有正文、是祖先键的按序近似形态，但本层键一个字都不含——专门用来
#:   证明"树上有合格候选"与"这一层会补读它"是两件事（`no_defect` 时不补）。
_SUPP_BODY: dict[tuple[str, ...], int] = {
    ("第一节经营概况", "一、报告期内从事的主要业务"): 40,
    ("第一节经营概况", "一、报告期内从事的主要业务", "1、主要业务"): 300,
    ("第一节经营概况", "一、报告期内从事的主要业务", "2、主要产品及其用途"): 44,
    ("第一节经营概况", "一、报告期内从事的主要业务", "3、经营模式"): 287,
    ("第二节财务分析", "四、主营业务分析", "2、收入与成本"): 0,
    ("第二节财务分析", "四、主营业务分析", "2、收入与成本", "（1）营业收入构成"): 114,
    ("第三节其他资料", "五、主动债务管理"): 416,
    ("第三节其他资料", "4、经模用式"): 90,
}

#: 简介只给一个节点，文本是中性说明句（与本节任何导航键都不同词），
#: 保证"是否有简介"这一条可用，同时不给任何 aspect 送分。
_SUPP_SYNOPSIS: dict[tuple[str, ...], str] = {
    ("第一节经营概况",): "本节的栏目说明文字由合成夹具给出",
}


def _supp_tree(*, with_body_measure: bool = True):
    """主体补读树：`(索引, {路径: node_id}, outline)`。

    `with_body_measure=False` 时**不给**逐节点自有正文量——那正是 `anp-6` 口径下这条规则
    的形态（判不了就不补），本节用它做**前后对照**：同一棵树上，补读只往读集里加节点。
    """
    return _outline_index(
        _SUPP_PATHS, _SUPP_SYNOPSIS, doc_id="DOC-NAV-SUPP",
        page_layout_id="pl-navsupp00000001",
        version="sha256-navsupp00000001", syn_prefix="ssyn:",
        body_chars=_SUPP_BODY if with_body_measure else None)


def _outline_index(paths, synopsis, *, doc_id: str, page_layout_id: str,
                   version: str, syn_prefix: str, body_chars=None):
    """合成 `DocumentOutline` + 简介 + **可选的**逐节点自有正文量 → 真实 `NavigationIndex`。"""
    loc = S.derive_document_outline_locator(
        page_layout_id=page_layout_id, document_id=doc_id,
        algorithm_version=V.OUTLINE_ALGORITHM_VERSION,
        schema_version=V.OUTLINE_SCHEMA_VERSION)

    def _anchor(line: int):
        y = 10.0 + line * 12.0
        return (1, line, (10.0, y, 200.0, y + 10.0))

    node_id_of = {
        path: S.derive_node_id(document_outline_locator=loc, structural_path=path,
                               source_anchor=_anchor(line), title=path[-1])
        for path, line in paths}
    nodes = []
    siblings: dict[tuple[str, ...], int] = {}
    for path, line in paths:
        parent_key = path[:-1]
        ordinal = siblings.get(parent_key, 0)
        siblings[parent_key] = ordinal + 1
        nodes.append(S.OutlineNode.create(
            document_outline_locator=loc,
            parent_id=None if not parent_key else node_id_of[parent_key],
            title=path[-1], title_normalized=path[-1], structural_path=path,
            source_anchor=_anchor(line),
            child_ids=tuple(node_id_of[p] for p, _l in paths if p[:-1] == path),
            ordinal=ordinal))
    outline = S.DocumentOutline.create(
        document_id=doc_id, document_version=version,
        page_layout_id=page_layout_id, nodes=tuple(nodes))
    by_id = {n.structural_path: n.node_id for n in outline.nodes}
    synopses = []
    for path, text in synopsis.items():
        node_id = by_id[path]
        synopses.append(S.NavigationSynopsis.available(node_id=node_id, snippets=(
            S.SynopsisSnippet(span_id="os-" + _sha(syn_prefix + node_id)[:16],
                              snippet_index=0, char_start=0, char_end=len(text),
                              text=text),)))
    counts = (None if body_chars is None
              else {by_id[path]: n for path, n in body_chars.items()})
    index = NAV.NavigationIndex(
        outline, tuple(synopses), span_node_ids=tuple(by_id[p] for p, _l in paths),
        body_char_counts=counts)
    return index, by_id, outline


def _supp_entry(nav_keys, parent_keys, *, aspect_id: str = "a-supp",
                subject_head: str = "", question_id: str = "q1"):
    """测试侧对照条目 + 单条 profile（走真实 `AspectNavigationProfile`，不自造字段）。

    `derivation` 里逐字写明这是**测试侧对照条目**，不是任何现行派生结果——真实条目一律由
    `build_navigation_profile` 从冻结 Contract 派生。
    """
    entry = S.AspectNavigationEntry(
        aspect_id=aspect_id, question_id=question_id, topic_id="t_probe",
        content_role="paragraph", display_tier="required_body",
        nav_keys=tuple(nav_keys), parent_keys=tuple(parent_keys),
        subject_head=subject_head, expected_forms=("正文",),
        derivation=(f"{NAV.NAV_SUPPLEMENT_RULE_ID}:测试侧对照条目（只为本节断言构造，"
                    "不是任何现行派生结果）",))
    return S.AspectNavigationProfile.create(
        contract_version="v1", contract_fingerprint=CONTRACT_FP,
        entries=(entry,)), entry


def _supp_roots(decision, index) -> tuple[str, ...]:
    return tuple(index.normalized_title(n) for n in decision.supplement_root_node_ids)


def _supplement(check, expect_error, details) -> None:
    """`anp-7` 主体补读：缺陷判据、候选判据（含三条负例）、有界性与可审计性。"""
    index, by_id, outline = _supp_tree()
    compat, _by_id_c, _outline_c = _supp_tree(with_body_measure=False)
    SCOPE = by_id[("第一节经营概况",)]
    PARENT = by_id[("第一节经营概况", "一、报告期内从事的主要业务")]
    MAIN = by_id[("第一节经营概况", "一、报告期内从事的主要业务", "1、主要业务")]
    MODEL = by_id[("第一节经营概况", "一、报告期内从事的主要业务", "3、经营模式")]
    PROD_USE = by_id[("第一节经营概况", "一、报告期内从事的主要业务", "2、主要产品及其用途")]
    ANALYSIS = by_id[("第二节财务分析", "四、主营业务分析")]
    REB = by_id[("第二节财务分析", "四、主营业务分析", "2、收入与成本")]
    REV = by_id[("第二节财务分析", "四、主营业务分析", "2、收入与成本",
                 "（1）营业收入构成")]
    DEBT = by_id[("第三节其他资料", "五、主动债务管理")]
    NONSENSE = by_id[("第三节其他资料", "4、经模用式")]
    check(index.unavailable_reason is None, "补读树上导航索引可用（有简介）")

    # ---- 2d.1 正文量：判不了与"没有正文"必须可区分（不猜）----------------------
    check(index.body_measure_available and not compat.body_measure_available,
          "两棵**同一形状**的树：一棵给了逐节点自有正文量、一棵没给")
    check(index.body_chars(MAIN) == 300 and compat.body_chars(MAIN) == -1,
          "没给正文量时读回**负值哨兵**（判不了），而不是 0（会被误读成"
          "「这一节没有正文」，正是 `body_measure_unavailable` 要防的那种猜测）")
    check(index.body_chars(ANALYSIS) == 0 and index.body_chars(SCOPE) == 0,
          "给了正文量、但该节点没有自有正文时读回 0（父章节本身没有正文）")
    check(index.body_chars("node-not-in-tree") == 0,
          "同一棵树里不在正文量表上的节点读回 0（不是负值：本树**判得了**）")
    expect_error(lambda: NAV.NavigationIndex(
        outline, tuple(), body_char_counts={"node-not-in-tree": 1}),
        Exception, "正文量表含不在本树上的 node 必须拒")
    expect_error(lambda: NAV.NavigationIndex(
        outline, tuple(), body_char_counts={MAIN: -1}),
        Exception, "正文量不得为负（判不了要用「不给」表达，不是用负数）")

    # ---- 2d.2 候选判据：三条负例各挡一类，正例必须进来 --------------------------
    check(NAV.ordered_variant_length("主营业务", "主要业务") == 3
          and NAV.is_ordered_variant("主营业务", "主要业务"),
          "`主营业务` ↔ `主要业务`：首末对齐、四字里按序对上三字 → 合格")
    check(NAV.ordered_variant_length("主营业务", "主动债务管理") == 2
          and not NAV.is_ordered_variant("主营业务", "主动债务管理"),
          "`主营业务` ↔ `主动债务管理`：**只**共享首末两字，内部字一个都对不上 → 不合格"
          "（四字词首末同字是汉语常态，只看首末会把一节**债务管理**披露读成主营业务材料）")
    check(NAV.is_ordered_variant("主营业务", "主营业务的构成"),
          "`主营业务` ↔ `主营业务的构成`：内部字对得上 → 合格（不许反过来收得过紧）")
    check(not NAV.is_ordered_variant("主营业务", "主要产品及其用途")
          and not NAV.is_ordered_variant("主营业务", "25、合同成本"),
          "末字对不上（`用途`）/ 首字对不上（`合同成本`）→ 不合格（首末锚仍在）")
    check(NAV._SUPPLEMENT_MIN_INTERIOR_CHARS == 1
          and NAV._SUPPLEMENT_MAX_ROOTS == 3,
          "非平凡门槛与补读根上限都是登记在案的常量（本节其余断言按它们写）")

    # ---- 2d.3 形态 (b)：**选中了、却选错栏目那一节** → 有界补读主体正文 ---------
    # 必过反例：本层键 `收入` 稳定把 aspect 送到 `2、收入与成本`（真实两份年报同形），
    # 而「报告期内公司从事的主要业务 / 1、主要业务」一个字都没读。
    profile_a, entry_a = _supp_entry(("收入",), ("主营业务",), aspect_id="a-supp-select",
                                     subject_head="收入")
    dec_a = NAV.navigate(index, entry_a, profile=profile_a)
    compat_a = NAV.navigate(compat, entry_a, profile=profile_a)
    check(dec_a.status == "selected" and dec_a.selected_node_id == REB,
          f"本层定位照旧选中「{index.normalized_title(REB)}」"
          f"（{dec_a.status}/{dec_a.selected_node_id == REB}）")
    check(dec_a.supplement_rule_status == "fired"
          and dec_a.supplement_root_node_ids == (MAIN,),
          f"缺陷（读集里没有一节贴上祖先声明派生的键）成立 → 补读**恰好**一节"
          f"（{dec_a.supplement_rule_status}/{_supp_roots(dec_a, index)}）")
    check(MAIN in set(dec_a.read_node_ids),
          "没读到的**主体正文节**补进了读集"
          f"（自有正文 {index.body_chars(MAIN)} 字）")
    check(dec_a.read_root_node_ids == (REB, MAIN, ANALYSIS),
          "两条规则的读根**按固定次序排在**本层读根之后：先 `anp-7` 的自带正文短节，"
          "再 `anp-9` 的上提父节（预算截断先满足更靠前的）")
    check(dec_a.lift_rule_status == "fired"
          and dec_a.lift_root_node_ids == (ANALYSIS,)
          and dec_a.supplement_root_node_ids == (MAIN,),
          "两个类别**分开记账**：`anp-7` 补的是自带正文的短节 `1、主要业务`，"
          "`anp-9` 补的是正文挂在子节上的父章节 `四、主营业务分析`（自有正文 "
          f"{index.body_chars(ANALYSIS)} 字，子树 {index.subtree_body_chars(ANALYSIS)} 字）")
    check(dec_a.band_node_ids == compat_a.band_node_ids
          and dec_a.selected_node_id == compat_a.selected_node_id
          and dec_a.read_root_node_ids[:1] == compat_a.read_root_node_ids,
          "本层选出结果（选中节 / 近分带 / 主读根）与**没有正文量**的 `anp-6` 口径逐位相同"
          "（两条补读规则都不参与打分、不改近分带）")
    check(compat_a.read_node_ids
          == dec_a.read_node_ids[:len(compat_a.read_node_ids)]
          and set(dec_a.read_node_ids) - set(compat_a.read_node_ids)
          == {MAIN, ANALYSIS},
          "读集只做**加法**：原有读集逐位不变，新节点只加在末尾"
          f"（{len(compat_a.read_node_ids)} → {len(dec_a.read_node_ids)}）")
    check(compat_a.supplement_rule_status == "body_measure_unavailable"
          and compat_a.supplement_root_node_ids == ()
          and compat_a.lift_root_node_ids == ()
          and compat_a.lift_rule_status == "body_measure_unavailable",
          "没给逐节点正文量时**两条规则**都是 `body_measure_unavailable`（**判不了**）"
          "而不是「没有候选」或「没有缺陷」——原因必须分得开")
    cand_a = {c.node_id: c for c in dec_a.ranked}
    check(MAIN in cand_a and cand_a[MAIN].in_read_set
          and cand_a[MAIN].discard_reason is None
          and cand_a[MAIN].score == 0.0,
          "补读根出现在候选列表里、`in_read_set=True`、**得分 0**"
          "（它不是打分选出来的）")
    check(any(NAV.NAV_SUPPLEMENT_RULE_ID in r for r in cand_a[MAIN].reasons)
          and not any(NAV.NAV_LIFT_RULE_ID in r for r in cand_a[MAIN].reasons),
          "`anp-7` 补读根的候选原因里点名的是**它自己**那条规则条目（可审计）")
    check(ANALYSIS in cand_a and any(NAV.NAV_LIFT_RULE_ID in r
                                     for r in cand_a[ANALYSIS].reasons)
          and not any(NAV.NAV_SUPPLEMENT_RULE_ID in r
                      for r in cand_a[ANALYSIS].reasons),
          "上提根同列在候选表里，但依据写的是**另一条**规则 id——两条规则不得互相冒充")
    check(not any(NAV.NAV_KEY_TIER_ANCESTOR in r
                  for c in dec_a.ranked for r in c.reasons),
          "补读根（两条规则的都算）**不得**带「祖先声明键」这个字面标记：它既不是本层候选"
          "也不是祖先层候选，混进去会让「祖先层生效」的判定被误判成真")
    check(ANALYSIS in set(dec_a.read_node_ids) and PROD_USE not in set(dec_a.read_node_ids),
          "父章节由 `anp-9` 补进来（这正是它要修的形态）；`anp-7` 那条**不**补它——"
          "短标题但正文不足的节两边都进不来（正文下限挡的）")
    #: 上提根自己就贴着祖先键：把**补完之后**的读集喂回 `anp-7`，它那条"自带正文不足"
    #: 的缺陷度量就被上提根自己的子树填满了（自我满足）。这不是缺陷，而是为什么两条规则
    #: 必须**分账**、`anp-7` 必须在**补读前**结算：喂旧读集时它仍只认 `1、主要业务`。
    _supp_after, _st_supp_after, _det_supp_after = NAV.subject_body_supplement(
        index, entry_a, read_node_ids=dec_a.read_node_ids,
        expanded_node_ids=dec_a.read_node_ids)
    check(_supp_after == () and _st_supp_after == "no_defect"
          and [r["title"] for r in _det_supp_after.get("matched")] == []
          and _det_supp_after["candidates_total"] == 0,
          f"`anp-7` 用**补完之后**的读集再算：缺陷已被上提根自己的子树填满（`no_defect`），"
          "且唯一合格的根 `1、主要业务` 此时已读、被 `already` 排除"
          f"（候选 {_det_supp_after['candidates_total']} 节）。"
          "两条规则由此必须分账、且 `anp-7` 只能结算在补读之前")
    check(NAV.subject_body_supplement(index, entry_a)[0] == (MAIN,)
          and NAV.subject_body_supplement(index, entry_a)[1] == "fired",
          "`anp-7` 用**补读前**的口径（read/expanded 皆空）判，仍然只认 `1、主要业务`")
    _read_before = set(dec_a.read_node_ids)
    _lift_again, _st_lift, _detail_lift = NAV.subject_section_lift(
        index, entry_a, read_node_ids=dec_a.read_node_ids,
        expanded_node_ids=dec_a.read_node_ids)
    check(not (set(_lift_again) & _read_before) and ANALYSIS not in _lift_again,
          "`anp-9` 幂等：已读节点（含上提根自己、含原来那 0+2 个读根）不会被"
          f"再上提一次（重算得 {_lift_again}，与已读集无交）")
    _roots_all, _st_all, detail_all = NAV.subject_body_supplement(index, entry_a)
    check(detail_all["candidates_total"] == 1
          and [r["title"] for r in detail_all["matched"]] == ["1、主要业务"],
          f"本树上 `anp-7` 合格的补读根**只有一节**（得到 {detail_all['candidates_total']} 节）"
          "——有界，不是把几十个近似标题一起读进来")
    _lift_free, _st_lift_free, detail_lift_free = NAV.subject_section_lift(
        index, entry_a)
    check(detail_lift_free["candidates_total"] == 0
          and _st_lift_free == "no_candidate",
          "**不带读集**时上提补读一个候选都产生不了：它的候选域就是已读节点的祖先链，"
          "读集为空 ⇒ 空域（横向章节因此一条也进不来）")
    check(DEBT not in set(dec_a.read_node_ids)
          and DEBT not in cand_a,
          "`五、主动债务管理`（有正文、只共享首末两字）既不进读集、也不进候选审计"
          "——它在候选判据那一层就被挡住了，不是「补了但没进读集」")
    check(set(dec_a.supplement_root_node_ids) <= set(dec_a.read_node_ids)
          and set(dec_a.lift_root_node_ids) <= set(dec_a.read_node_ids)
          and not (set(dec_a.supplement_root_node_ids)
                   & set(dec_a.lift_root_node_ids))
          and len(set(dec_a.read_node_ids)) == len(dec_a.read_node_ids),
          "不变量：两条规则的读根都必须真的落在读集里、互不重叠，且读集不重复")

    # ---- 2d.4 `no_defect`：读集里已经贴上祖先键 → 一节不补（候选存在也补）-------
    profile_c, entry_c = _supp_entry(("模式",), ("经营模式",), aspect_id="a-supp-nodefect",
                                     subject_head="模式")
    dec_c = NAV.navigate(index, entry_c, profile=profile_c)
    check(dec_c.status == "selected" and set(dec_c.read_node_ids) == {MODEL},
          f"本层键 `模式` 读到「{index.normalized_title(MODEL)}」")
    check(dec_c.supplement_rule_status == "no_defect"
          and dec_c.supplement_root_node_ids == (),
          "读集里已有一节贴上祖先键 `经营模式` → **没有缺陷**，一节不补")
    _roots_c, st_c_free, detail_c_free = NAV.subject_body_supplement(index, entry_c)
    check(st_c_free == "fired"
          and {r["title"] for r in detail_c_free["matched"]}
          == {"3、经营模式", "4、经模用式"},
          "同一棵树上、**不带读集**时这条规则确实能补出两节（含那节本层键一个字都不含的"
          "`4、经模用式`）→ 证明上面的 `no_defect` 是**判出来的**，不是候选缺失造成的"
          "（`no_defect` 与 `no_candidate` 必须分得开）")

    # ---- 2d.5 形态 (a)：**本层零候选 / 低置信** → 补读把读集从空救回真正那一节 -------
    # 本层键与祖先键在这棵树上都**没有**候选：`销售模式`一个字都不在任何标题里，
    # `主营业务`只以片段/近似形态出现、没有哪个标题的**完整标签段**就是它——因此祖先层
    # 这一支也不会替它选出读根（这正是真实募集说明书上 `main_business` 的形态之一）。
    profile_d, entry_d = _supp_entry(("销售模式",), ("主营业务",),
                                     aspect_id="a-supp-fallback",
                                     subject_head="销售模式")
    check(not index.key_has_segment_match("主营业务"),
          "前提：`主营业务`在本树上**没有**完整标签段命中 → 祖先层这一支也不出读根"
          "（否则本支不是「零候选」缺陷，而是「祖先层已经选出来了」）")
    dec_d = NAV.navigate(index, entry_d, profile=profile_d)
    compat_d = NAV.navigate(compat, entry_d, profile=profile_d)
    check(compat_d.status == "fallback"
          and compat_d.fallback_reason == "low_confidence"
          and compat_d.read_node_ids == (),
          "没有正文量时这一支照旧是 `low_confidence` 显式 fallback（读集为空）")
    check(dec_d.status == "selected"
          and dec_d.supplement_rule_status == "fired"
          and dec_d.supplement_root_node_ids == (MAIN,)
          and dec_d.read_node_ids == (MAIN,),
          f"有正文量时同一支补出真正那一节"
          f"（{dec_d.status}/{_supp_roots(dec_d, index)}）")
    check(dec_d.band_node_ids == ()
          and dec_d.read_root_node_ids == (MAIN,),
          "补读出来的读根**不塞进近分带**（`band` 为空）：按祖先键补的不能说成打分选出来的")
    check(dec_d.ranked and all(any(NAV.NAV_SUPPLEMENT_RULE_ID in r for r in c.reasons)
                               for c in dec_d.ranked if c.in_read_set),
          "补读终态里每个进读集的候选**都有一条**原因点名了补读规则（可审计）")

    # ---- 2d.6 `no_candidate` / `no_ancestor_keys`：补不成也要说清为什么 ----------
    profile_e, entry_e = _supp_entry(("用途",), ("产业链",), aspect_id="a-supp-nocand",
                                     subject_head="用途")
    dec_e = NAV.navigate(index, entry_e, profile=profile_e)
    check(dec_e.status == "selected" and dec_e.supplement_rule_status == "no_candidate"
          and dec_e.supplement_root_node_ids == ()
          and set(dec_e.read_node_ids) == {PROD_USE},
          f"有祖先键、也有缺陷，但树上没有合格的补读根 → `no_candidate`、读集不变"
          f"（{dec_e.supplement_rule_status}）")
    profile_f, entry_f = _supp_entry(("用途",), (), aspect_id="a-supp-nokeys",
                                     subject_head="用途")
    dec_f = NAV.navigate(index, entry_f, profile=profile_f)
    check(dec_f.supplement_rule_status == "no_ancestor_keys"
          and dec_f.read_node_ids == dec_e.read_node_ids,
          "没有祖先声明键 → 这条规则无从下手（`no_ancestor_keys`，不是「没有缺陷」），"
          "读集与本层定位一字不差")
    check(NAV.SUPPLEMENT_STATUSES == ("fired", "no_defect", "no_ancestor_keys",
                                      "no_candidate", "over_budget",
                                      "body_measure_unavailable", "not_applicable"),
          "补读状态是**封闭**取值（`not_applicable` 只留给不经 navigate 构造的旧读回）")

    # ---- 2d.7 `over_budget`：预算被本层吃光 → 读集**不变**、状态如实区分 ----------
    limits = NAV.NavigationLimits(max_candidates=8, max_subtree_nodes=2, min_score=0.34)
    dec_g = NAV.navigate(index, entry_a, profile=profile_a, limits=limits)
    compat_g = NAV.navigate(compat, entry_a, profile=profile_a, limits=limits)
    check(dec_g.read_node_ids == compat_g.read_node_ids
          and dec_g.read_root_node_ids == compat_g.read_root_node_ids,
          "预算是本层的：补读根排在后面展开，本层读集一字不变")
    check(dec_g.supplement_rule_status == "over_budget"
          and dec_g.supplement_root_node_ids == (),
          f"合格补读根存在但没能进读集 → `over_budget`，**不记成补读成功**"
          f"（{dec_g.supplement_rule_status}）")
    cand_g = {c.node_id: c for c in dec_g.ranked}
    check(cand_g.get(MAIN) is not None
          and cand_g[MAIN].discard_reason == "over_budget"
          and any(NAV.NAV_SUPPLEMENT_RULE_ID in r for r in cand_g[MAIN].reasons),
          "没进读集的补读候选留在候选审计里、带 `over_budget` 舍弃原因"
          "（「补读没做成」必须是可审计的）")

    # ---- 2d.8 有界性：合格候选多于上限时**只取前 N 个**（按登记顺序）--------------
    cap_index, _cap_by, _cap_outline = _supp_cap_tree()
    profile_h, entry_h = _supp_entry(("收入",), ("主营业务",), aspect_id="a-supp-cap",
                                     subject_head="收入")
    dec_h = NAV.navigate(cap_index, entry_h, profile=profile_h)
    _r_h, st_h, detail_h = NAV.subject_body_supplement(cap_index, entry_h)
    check(detail_h["candidates_total"] == 5 and detail_h["cap"] == 3,
          f"合成树上合格补读根 5 个 > 上限 3（得到 {detail_h['candidates_total']}）")
    check(len(dec_h.supplement_root_node_ids) == NAV._SUPPLEMENT_MAX_ROOTS,
          f"读进来的**恰好**是上限个（{len(dec_h.supplement_root_node_ids)}）")
    check([cap_index.normalized_title(n) for n in dec_h.supplement_root_node_ids]
          == ["2主营业务情况", "3主营业务的构成", "4主要业务情况"],
          f"取的是（按序对上字数 → 自有正文长度 → 文档顺序）排序后的前 3 个"
          f"（得到 {[cap_index.normalized_title(n) for n in dec_h.supplement_root_node_ids]}）："
          "对上 3 字的那个正文再多也排在四个字的后面，同是对上 4 字的按自有正文长度分先后——"
          "不是「前 3 个出现的」、也不是全读进来")
    check(all(n in set(dec_h.read_node_ids)
              for n in dec_h.supplement_root_node_ids),
          "被取中的补读根都真的落在读集里")

    details.append(
        "INFO: 补读树 形态(b)选中错栏 "
        f"补={_supp_roots(dec_a, index)} 读={len(dec_a.read_node_ids)}"
        f"（anp-6 口径 {len(compat_a.read_node_ids)}）；"
        f"形态(a)低置信 补={_supp_roots(dec_d, index)}；"
        f"no_defect 候选（不带读集）={[r['title'] for r in detail_c_free['matched']]}；"
        f"over_budget 候选={[detail_h['candidates_total']]}")
    #: 只盯**补读根那一行**：它既不是本层候选也不是祖先层候选，混进「祖先层生效」的标记
    #: 会让"这一支到底走的是哪一层"被误判。祖先层**正常**产生的标记不受本规则影响——
    #: `dec_c` 就是祖先层选出来的，它的标记照旧在（下面同时钉住这一条，免得检查变空转）。
    _supp_rows = [(d, c) for d in (dec_a, dec_d) for c in d.ranked
                  if c.node_id in set(d.supplement_root_node_ids)]
    check(len(_supp_rows) == 2
          and all(any(NAV.NAV_SUPPLEMENT_RULE_ID in r for r in c.reasons)
                  for _d, c in _supp_rows)
          and not any(NAV.NAV_KEY_TIER_ANCESTOR in r
                      for _d, c in _supp_rows for r in c.reasons),
          "补读根那一行的候选原因只点名补读规则自己，**不**带「祖先声明键」这个字面标记"
          "（它不冒充本层候选、也不冒充祖先层候选）")
    check(any(NAV.NAV_KEY_TIER_ANCESTOR in r for c in dec_c.ranked for r in c.reasons),
          "对照：真正由祖先层选出来的那一支照旧带「祖先声明键」标记——"
          "补读没有把祖先层的判定口径弄脏，也不是靠改这一层的标记做出来的")


#: 第五棵合成树：只服务"合格候选多于上限"这一条。五个节点都是祖先键 `主营业务` 的按序
#: 近似形态、都自带正文、都不是长句标题；差异只在"按序对上几个字"与自有正文长度上，
#: 用来验证取前 N 个的**顺序**是登记的那一条（先按对上字数、再按自有正文长度、最后文档
#: 顺序），不是"前 5 个出现的"。
#:
#: 关键前提：本树上**没有**任何标题的完整标签段就是 `主营业务`（`2、主营业务情况` 这类
#: 都不算整段相同），因此祖先层这一支不替本层选出读根——缺陷成立，补读才会真的走一遍。
_SUPP_CAP_PATHS: tuple[tuple[tuple[str, ...], int], ...] = (
    (("第一节概况",), 0),
    (("第二节分析",), 1),
    (("第二节分析", "2、收入栏目"), 2),
    (("第二节分析", "2、收入栏目", "（1）收入明细"), 3),
    (("第三节附件",), 4),
    (("第三节附件", "1、主要业务"), 5),
    (("第三节附件", "2、主营业务情况"), 6),
    (("第三节附件", "3、主营业务的构成"), 7),
    (("第三节附件", "4、主要业务情况"), 8),
    (("第三节附件", "5、主要业务的构成"), 9),
)
#: 这棵树有**自己的**根标题，简介必须按它自己的路径键给（复用上一棵树的键会直接 KeyError）。
_SUPP_CAP_SYNOPSIS: dict[tuple[str, ...], str] = {
    ("第一节概况",): "本节的栏目说明文字由合成夹具给出",
}

#: 逐节点自有正文量：对上字数相同的三个候选靠正文长度分先后（140 / 110 / 100 取前三个，
#: 90 那个出局）；`1、主要业务` 只对上 3 个字，正文再多（120）也排在四个字的后面。
_SUPP_CAP_BODY: dict[tuple[str, ...], int] = {
    ("第二节分析", "2、收入栏目"): 0,
    ("第二节分析", "2、收入栏目", "（1）收入明细"): 200,
    ("第三节附件", "1、主要业务"): 120,
    ("第三节附件", "2、主营业务情况"): 110,
    ("第三节附件", "3、主营业务的构成"): 100,
    ("第三节附件", "4、主要业务情况"): 140,
    ("第三节附件", "5、主要业务的构成"): 90,
}


def _supp_cap_tree():
    return _outline_index(_SUPP_CAP_PATHS, _SUPP_CAP_SYNOPSIS,
                          doc_id="DOC-NAV-SUPP-CAP",
                          page_layout_id="pl-navsuppcap00001",
                          version="sha256-navsuppcap00001", syn_prefix="csyn:",
                          body_chars=_SUPP_CAP_BODY)


# ---------------------------------------------------------------------------
# 3. 非 300750 正例：仓库内版本化夹具
# ---------------------------------------------------------------------------

def _fixture_index(details):
    """夹具上的真实导航索引（缺夹具 / PDF 时返回 None，调用方如实 skip）。"""
    from document_structure import evidence_gateway as EG
    from document_structure import span_builder as SB
    from document_structure import synopsis as SY
    from document_structure.schema import PageLayout
    from evals import tree_stage_env as STAGE

    if not EG.fixture_root_dir().is_dir():
        details.append(f"SKIP 版本化夹具目录不存在（{EG.FIXTURE_ROOT_RELPATH}）")
        return None
    root = EG.load_fixture_root()
    pdf_path = REPO / root["source_pdf"]["relpath"]
    if not pdf_path.is_file():
        details.append("SKIP 夹具冻结 PDF 不在仓库内（不猜测字节）")
        return None
    layout = PageLayout.from_dict(json.loads(
        (EG.fixture_root_dir() / "page_layout.json").read_text(encoding="utf-8")))
    with STAGE.simulated_a_environment():
        handoff = SB._issue_fixture_ts3_handoff(
            raw_pdf=pdf_path.read_bytes(), expected_layout=layout,
            company_id=root["company_id"], document_id=root["document_id"],
            fixture_root=root)
        snapshot = SB._build_from_pinned_handoff(handoff, stage="distribution_only")
        # 冻结 outline 的 reference occurrence 校验需要**真实对象**核验上下文
        # （`toc_sources` 并不在冻结文件里，离线 `from_dict` 会在合法产物上
        # fail-closed）。这里直接取受信签发对象持有的同一棵 outline，不自造来源。
        outline = handoff.document_outline
    synopses = SY.build_navigation_synopses(
        node_ids=sorted({n.node_id for n in outline.nodes}), spans=snapshot.spans,
        coverages=snapshot.coverages, dispositions=snapshot.dispositions,
        policy=snapshot.qualification_policy)
    index = NAV.NavigationIndex(outline, synopses, span_node_ids=(
        [s.node_id for s in snapshot.spans]))
    return index, snapshot


def _fixture_positive(check, details) -> int:
    built = _fixture_index(details)
    if built is None:
        return 1
    index, snapshot = built
    check(index.outline_id.startswith("do-"),
          f"夹具树上拿到真实 outline 身份（{index.outline_id}）")
    check(index.unavailable_reason is None, "夹具树上简介结构可用")
    if index.unavailable_reason is not None:
        details.append("SKIP 夹具树简介不可用，无法继续（不猜）")
        return 1

    body_nodes = {n: c for n, c in index.span_count_by_node.items() if c > 0}
    check(bool(body_nodes), "夹具树上确有持有 span 的节点")
    target = max(body_nodes, key=lambda n: (body_nodes[n], -index.document_order(n)))
    title = index.node(target).title
    check(bool(title), "取真实标题作为导航键（测试不写答案关键词）")

    profile, entry = _entry(title, aspect_id="a-fixture")
    # 键由真实标题经 `aspect_nav_keys` 确定性派生（规范化会去掉标点，故不比字面相等）。
    check(entry.nav_keys == NAV.aspect_nav_keys((title,), title)
          and bool(entry.nav_keys),
          "导航键由该真实标题确定性派生（`_entry_for` → `aspect_nav_keys`，不自造）")
    # 键是**字段名**：标题可能带编号前缀（`（1）产品甲`），而标签段一律剥编号，因此这里
    # 断言的是"派生的键贴得上它自己标题的完整标签段"，不是字面包含（编号不参与键身份）。
    check(NAV.label_anchored_keys(index, entry.nav_keys, target) == entry.nav_keys,
          "派生的键在它自己的标题上贴得上标签（编号前缀不参与键身份）")
    decision = NAV.navigate(index, entry, profile=profile)
    check(decision.status == "selected",
          f"夹具上以真实标题导航得到 selected（得到 {decision.status}）")
    if decision.status != "selected":
        return 0
    check(target in set(decision.read_node_ids),
          "持有正文的节点落在读集内（正例：正文真的可达）")
    roots = set(decision.read_root_node_ids)
    check(any(target == r or target in index.subtree_of(r) for r in roots),
          "读根是该节点的祖先或自身（上提只沿祖先链走）")
    check(all(index.node(r) is not None and
              NAV.score_node(index, entry.nav_keys, r)[0] > 0.0 for r in roots),
          "每个读根本身都是命中候选（不命中的祖先不进读集）")
    check(set(decision.read_node_ids)
          <= _read_set_under(index, decision.read_root_node_ids),
          "读集由读根子树展开（无外部 node 混入）")
    check(all(c.discard_reason in NAV.DISCARD_REASONS
              for c in decision.ranked if c.discard_reason is not None),
          "夹具上的舍弃原因同样取自封闭集合")
    check(not decision.produces_coverage(), "夹具上的导航同样不产生 coverage")

    # 负例：同一棵真实树上，无关键不得产生任何读集
    profile_n, entry_n = _entry("董事长的私人游艇", aspect_id="a-fixture-none")
    none_decision = NAV.navigate(index, entry_n, profile=profile_n)
    check(none_decision.status == "fallback"
          and none_decision.fallback_reason == "low_confidence"
          and none_decision.read_node_ids == (),
          "同一棵非 300750 真实树上，无关标题 → 低置信且不读（误召回负例）")
    details.append(
        f"INFO: 夹具 outline={index.outline_id} 持有 span 的 node="
        f"{len(body_nodes)} 读集={len(decision.read_node_ids)} "
        f"读根={len(decision.read_root_node_ids)} 未读={decision.unread_total}")
    return 0


# ---------------------------------------------------------------------------
# 4. 原真实文档正例：同一机制在 live 样本上必须真的扩到正文
# ---------------------------------------------------------------------------

#: 冻结 Contract 的**测试侧只读缓存**（本 eval 只读它的声明字段，不写、不改）。
_CONTRACT_CACHE: list = []


def _contract():
    """与生产调用点同源加载的冻结 Contract（`planning.demo_scope` → `loader_v2`）。"""
    if not _CONTRACT_CACHE:
        from contracts.loader_v2 import load_contract_v2
        from planning import demo_scope as SC
        profile = SC.load_demo_scope_profile(SC.DEFAULT_PROFILE_PATH)
        _CONTRACT_CACHE.append(
            load_contract_v2(str(REPO / profile.contract_asset)))
    return _CONTRACT_CACHE[0]


def _topic_question_of(contract) -> dict[str, tuple[str, str]]:
    """`aspect_id -> (topic_id, question_id)`（只读冻结 Contract 的父子归属）。"""
    out: dict[str, tuple[str, str]] = {}
    for section in contract.sections:
        for topic in section.topics:
            for question in topic.questions:
                for aspect in question.aspects:
                    out[aspect.aspect_id] = (topic.topic_id, question.question_id)
    return out


def _real_contract_assignment(check, details) -> None:
    """真实冻结 Contract 上的标题段归属**正反例**（只读 Contract，不需要文档）。

    全部断言只读 Contract 的声明字段（topic 标题 / question 文本 / aspect 声明字段），
    因此与公司、文档、页码、答案词无关。核心不变量是 C1 的目标本身：
    **一个被归属到某 question 的 topic 标题段，只进该 question 子项的祖先层键**。
    """
    contract = _contract()
    audit = NAV.contract_topic_segment_assignments(contract)
    labels = NAV.contract_ancestor_labels(contract)
    owner = _topic_question_of(contract)
    rows = [r for rs in audit.values() for r in rs]
    topics = tuple(t for s in contract.sections for t in s.topics)
    empty_topics = [t.topic_id for t in topics if not audit[t.topic_id]]
    check(len(audit) == len(topics) and set(audit) == {t.topic_id for t in topics},
          f"冻结 Contract 的每个 topic 都产出归属审计视图（{len(audit)}/{len(topics)}）")
    check(bool(rows) and len(empty_topics) < len(topics),
          f"真实 Contract 上确有 topic 的标题段进入归属（{len(topics) - len(empty_topics)}"
          " 个 topic 有归属行；标题里**没有**标签尺寸段的 topic 行数为 0，如实登记）")
    check(all(r.reason in NAV.TOPIC_SEGMENT_ASSIGN_REASONS for r in rows),
          "真实 Contract 上归属原因全部取自封闭集合")
    adopted = [r for r in rows if r.reason == "topic-segment-assigned"]
    check(bool(adopted), f"真实 Contract 上确有标题段被唯一归属（{len(adopted)} 段）")
    check(any(r.tier == "exact_label_segment" for r in adopted)
          and any(r.tier == "label_containment" for r in adopted),
          "真实 Contract 上最强两层判据都被用到（不是只靠最弱的公共子串）")
    check(any(r.reason == "topic-segment-contended" for r in rows)
          and any(r.reason == "topic-segment-no-signal" for r in rows),
          "真实 Contract 上确有段因**争用**或**无信号**被丢弃（不强选）")
    aspects_by_topic: dict[str, list[str]] = {}
    for aspect_id, (topic_id, _qid) in owner.items():
        aspects_by_topic.setdefault(topic_id, []).append(aspect_id)
    violations: list[tuple[str, str]] = []
    for topic_id, rows_t in audit.items():
        for row in rows_t:
            if row.reason != "topic-segment-assigned":
                continue
            for aspect_id in aspects_by_topic.get(topic_id, ()):
                _tid, question_id = owner[aspect_id]
                in_assigned = row.segment in labels[aspect_id][1:]
                if in_assigned != (question_id == row.question_id):
                    violations.append((aspect_id, row.segment))
    check(not violations,
          "归属不变量：被采用的标题段只进**它自己的** question 子项的祖先层键"
          f"（违例 {violations[:5]}）")
    outsider_segments = {
        row.segment for row in rows if row.reason == "topic-segment-assigned"}
    check(bool(outsider_segments),
          "真实 Contract 上确有段进入祖先层（收窄不是把祖先层整体关掉）")
    # 「按 question 收窄」的**直接**证据：同一个 topic 里的不同 question 拿到的祖先层
    # 标题段**互不相同**（`anp-3` 下它们必然完全相同——整段交给每个 aspect）。
    differentiated: list[str] = []
    for topic_id, aspect_ids in aspects_by_topic.items():
        by_question: dict[str, set] = {}
        for aspect_id in aspect_ids:
            by_question.setdefault(owner[aspect_id][1], set()).update(
                labels[aspect_id][1:])
        if len(by_question) > 1 and len(
                {frozenset(v) for v in by_question.values()}) > 1:
            differentiated.append(topic_id)
    check(bool(differentiated),
          "真实 Contract 上确有 topic 的**不同 question** 拿到不同的祖先层标题段"
          f"（收窄真的按 question 发生了：{differentiated}）")
    details.append(
        "INFO: 真实 Contract 归属审计 "
        f"topics_without_label_segments={empty_topics} "
        f"assigned={len(adopted)} "
        f"contended={sum(1 for r in rows if r.reason == 'topic-segment-contended')} "
        f"no_signal={sum(1 for r in rows if r.reason == 'topic-segment-no-signal')} "
        f"assigned_segments={sorted(outsider_segments)}")


def _enum_only_parent_keys(own, texts, sibling_keys):
    """**测试侧参考实现**：`anp-7` 的枚举层祖先键（没有并列连词兜底层）。

    它只用来断言"兜底层**改到**了哪几条 / **没改到**哪几条"，不定义任何行为；正反例的
    期望值一律由生产函数给出。切法逐行照抄 `parent_nav_keys` 的枚举层部分（`_ENUM_SPLIT`
    分段 + 逐段 `_add`），以便两条路只在"兜底层"这一处不同。
    """
    keys: list[str] = []

    def _add(raw: str) -> None:
        normalized = NAV.normalize_navigation_text(raw)
        if not (1 < len(normalized) <= NAV._LABEL_MAX_CHARS):
            return
        if normalized in own or normalized in sibling_keys or normalized in keys:
            return
        keys.append(normalized)

    for text in texts:
        _add(text)
        for segment in NAV._ENUM_SPLIT.split(text):
            segment = segment.strip()
            if segment == "" or any(ch in segment for ch in NAV._BRACKETS):
                continue
            _add(segment)
    return tuple(sorted(keys))


def _real_join_fallback(check, details) -> None:
    """`anp-8` 并列连词兜底在**真实冻结 Contract** 上的正反例（只读 Contract 声明字段）。

    这里的正例与反例都是**实测**出来的，不是造出来的：

    * 正例 —— 祖先声明是被连词连起来的复合标签（`供应商当前集中度与集中度跨期变化`），
      枚举分隔符一个键都切不出，兜底层必须把它救回；
    * 反例 —— 祖先声明带 `、`、末尾还跟一条 `……对利润和资产质量的影响` 式**从句**
      （`fin_asset_quality` 三条），枚举层同样零键，兜底层**不得**把从句切成键。

    两条都是"枚举层零键"的同一形状，区别只在**声明里有没有枚举分隔符**——这正是兜底层
    那道门的判别依据。断言全部只读 Contract 声明字段，与公司、文档、页码、答案词无关。
    """
    contract = _contract()
    labels, siblings = NAV.contract_ancestor_inputs(contract)
    own: dict[str, tuple[str, ...]] = {}
    for section in contract.sections:
        for topic in section.topics:
            for question in topic.questions:
                for aspect in question.aspects:
                    own[aspect.aspect_id] = tuple(aspect.required_fields or ())
    check(set(labels) == set(own) == set(siblings),
          "祖先声明 / 兄弟排除集 / aspect 声明字段三份输入逐 aspect 对齐"
          f"（{len(labels)}/{len(own)}/{len(siblings)}）")

    def _join_pieces(text: str) -> tuple[str, ...]:
        return tuple(p.strip() for p in NAV._LABEL_JOIN.split(text)
                     if p.strip() != "" and not any(ch in p for ch in NAV._BRACKETS))

    def _actual(aspect_id: str) -> tuple[str, ...]:
        return NAV.parent_nav_keys(own[aspect_id], labels[aspect_id],
                                   siblings[aspect_id])

    # ---- 正例：连词复合标签 + 枚举层零键 ⇒ 兜底层必须给出键 ---------------
    rescued: list[tuple[str, tuple[str, ...]]] = []
    for aspect_id, texts in labels.items():
        if not texts:
            continue
        if any(len(NAV._ENUM_SPLIT.split(t)) > 1 for t in texts):
            continue                       # 带枚举分隔符 ⇒ 兜底层明确不碰
        if not any(len(NAV._LABEL_JOIN.split(t)) > 1 for t in texts):
            continue                       # 没有连词 ⇒ 不是并列复合标签
        if _enum_only_parent_keys(own[aspect_id], texts, siblings[aspect_id]):
            continue                       # 枚举层本来就有键 ⇒ 走不到兜底层
        keys = _actual(aspect_id)
        if keys:
            rescued.append((aspect_id, keys))
    check(bool(rescued),
          "真实 Contract 上确有「连词复合标签 + 枚举层零键」被兜底层救回"
          f"（{len(rescued)} 条：{[a for a, _k in rescued]}）")
    for aspect_id, keys in rescued:
        pieces = {p for t in labels[aspect_id] for p in _join_pieces(t)}
        expect = tuple(sorted(
            p for p in pieces
            if 1 < len(p) <= NAV._LABEL_MAX_CHARS
            and p not in own[aspect_id] and p not in set(siblings[aspect_id])))
        check(keys == expect,
              f"{aspect_id} 的祖先层键逐字等于连词切片（得到 {list(keys)}，"
              f"应为 {list(expect)}）")
        check(all(NAV.title_label_segments(t) for t in labels[aspect_id]),
              f"{aspect_id} 拿到的确实是 Contract 声明的标签文本（不是拼出来的）")
        details.append(f"INFO: anp-8 兜底救回 {aspect_id} parent_keys={list(keys)}")

    # ---- 反例 1：带枚举分隔符的从句尾巴不得被切成键 -----------------------
    enumerated = [a for a, ts in labels.items()
                  if ts and any(len(NAV._ENUM_SPLIT.split(t)) > 1 for t in ts)]
    check(bool(enumerated),
          f"真实 Contract 上确有带枚举分隔符的祖先声明（{len(enumerated)} 条）")
    zero_enum = [a for a in enumerated
                 if not _enum_only_parent_keys(own[a], labels[a], siblings[a])]
    check(bool(zero_enum),
          "其中确有**枚举层零键**的（否则这道反例根本没走到兜底层的门口，属空转）："
          f"{len(zero_enum)} 条 {zero_enum}")
    leaks = [a for a in zero_enum if _actual(a)]
    check(not leaks,
          "枚举层零键但声明里带枚举分隔符的 aspect，兜底层必须**不**造键"
          f"（泄漏 {leaks}；真实反例靶点："
          f"{[t for a in zero_enum for t in labels[a] if '、' in t][:1]}）")
    details.append(
        "INFO: anp-8 因果门 枚举分隔符声明零键 "
        f"{len(zero_enum)} 条全部保持空 parent_keys={[a for a in zero_enum]}")

    # ---- 反例 2：兜底层只动连词复合标签那几条，其余逐字不变 ---------------
    changed = [a for a in sorted(labels)
               if _enum_only_parent_keys(own[a], labels[a], siblings[a])
               != _actual(a)]
    check(sorted(changed) == sorted(a for a, _k in rescued),
          "兜底层的改动面**恰好**等于连词复合标签那几条"
          f"（改动 {len(changed)} 条 / 全 Contract {len(labels)} 条，"
          f"其余 {len(labels) - len(changed)} 条祖先层键逐字不变）")


def _real_docs(details):
    """仓库内全部 live 年报切片（缺则如实 skip，不静默算 0 命中）。"""
    pdfs = sorted((REPO / "data/samples").glob("*/announcements/*.pdf"))
    evidence_db = (REPO / "data/evidence.db").resolve()
    if not pdfs or not evidence_db.exists():
        details.append("SKIP 缺少 live 样本或 data/evidence.db（不猜）")
        return ()
    return tuple(pdfs)


def _real_index(details, pdf):
    from contracts.loader_v2 import load_contract_v2
    from document_structure import live_span_source as LSS
    from document_structure import synopsis as SY
    from evidence import store as estore
    from planning import demo_scope as SC
    from planning import schema as PS

    evidence_db = (REPO / "data/evidence.db").resolve()
    company = pdf.parents[1].name
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    doc_version = "sha256-" + sha[:16]
    saved = estore._db_path
    estore._db_path = evidence_db
    try:
        set_version = estore.current_evidence_set_ro(
            evidence_db, company, pdf.stem, doc_version)
        if not isinstance(set_version, str) or set_version == "":
            details.append(f"SKIP {pdf.stem}: Evidence 库里没有 current set（不猜值）")
            return None
        # 绑定动作本身不写库；但 span 快照构建期要求绑定在场
        # （`bind_current_evidence_authority` 在未绑定时 fail-closed）。
        live = LSS.build_live_verified_span_snapshot(LSS.LiveSpanBuildRequest(
            company_id=company, document_id=pdf.stem, document_version=doc_version,
            raw_pdf_path=str(pdf), raw_pdf_sha256=sha,
            expected_current_evidence_set_version=set_version))
    finally:
        estore._db_path = saved
    profile = SC.load_demo_scope_profile(SC.DEFAULT_PROFILE_PATH)
    contract = load_contract_v2(str(REPO / profile.contract_asset))
    business = PS.ReportJobInput(
        job_id="job_nav_mechanics", company_id=company, company_name=company,
        credit_type="general", report_as_of="2026-12-31", contract_version="v2")
    source_inputs = {
        "case_input_id": "case_nav_mechanics", "document_id": pdf.stem,
        "document_version": doc_version, "raw_pdf_sha256": sha,
        "current_evidence_set_version": set_version,
        "substrate_dependency_versions": {
            k: f"{k}-v1" for k in SC.SUBSTRATE_DEPENDENCY_KEYS},
        "external_policy_snapshot_id": None, "budget_policy_id": profile.budget_policy_id,
        "budget_policy_version": profile.budget_policy_version,
        "model_policy_id": "demo_model_policy_v1", "code_fingerprint": "1" * 64,
    }
    manifest_scope = SC.build_scope_input_manifest(profile, business, source_inputs)
    projection = SC.project_contract_v2_scope(contract, profile, manifest_scope)
    requirements = {r.topic_id: r for r in projection.requirements}
    #: 祖先声明与兄弟项排除集来自**冻结 Contract**（与生产调用点同一入口、同一份派生：
    #: `contract_ancestor_inputs` 一次给出两份，只给其一会被 `build_navigation_profile`
    #: 直接 fail-closed 拒掉 —— 真实 Contract 上确有兄弟 aspect，空排除集在这里是**夹具
    #: 失真**，不是"没有兄弟项"）。
    labels, siblings = NAV.contract_ancestor_inputs(contract)

    snapshot, outline = live.snapshot, live.document_outline
    synopses = SY.build_navigation_synopses(
        node_ids=sorted({n.node_id for n in outline.nodes}), spans=snapshot.spans,
        coverages=snapshot.coverages, dispositions=snapshot.dispositions,
        policy=live.qualification_policy)
    #: 逐节点**自有**准入正文量（`anp-7` 的判据）：与两个生产调用点同源、同一份函数，
    #: 不给它就没有补读（`body_measure_unavailable`）。简介长度**不能**代这个量。
    index = NAV.NavigationIndex(
        outline, synopses, span_node_ids=[s.node_id for s in snapshot.spans],
        body_char_counts=SY.build_navigation_body_chars(snapshot.spans))
    return index, requirements, snapshot, labels, siblings


def _fragment_only_nodes(index, entry) -> tuple[str, ...]:
    """本树里"标题只以**片段**命中本层键"的节点（没有任何完整标签段命中）。

    构造性判据，不含公司/页码/答案词：先取键在本树里的匹配形态，再看该形态是否
    只是某个标题的**子串**而非某个完整标签段。
    """
    forms = tuple(f for k in entry.nav_keys for f in index.key_match_forms(k))
    if not forms:
        return ()
    out: list[str] = []
    for node_id in index.node_ids:
        if NAV.segment_hits(index, entry.nav_keys, node_id):
            continue
        title = index.normalized_title(node_id)
        if any(f in title for f in forms):
            out.append(node_id)
    return tuple(out)


def _titled_like(index, needle: str) -> tuple[str, ...]:
    """本树里标题**含该字串**的节点（反例靶点用；只读取真实标题，不改规则）。"""
    return tuple(n for n in index.node_ids if needle in index.normalized_title(n))


#: 两条补读规则各自的规则 id。`anp-7` 找的是"标题是祖先键的按序近似形态、且**自带**正文"
#: 的短节；`anp-9` 找的是"已读节点所属的父节"（自有正文可以是 0，正文在**有界子树**里）。
#: 二者的合格条件不同，读回侧必须能分开清点——合成一类会让"补读根都自带实质正文"这条
#: `anp-7` 的保证在 `anp-9` 下悄悄失效。
_SUPPLEMENT_RULE_IDS = (NAV.NAV_SUPPLEMENT_RULE_ID, NAV.NAV_LIFT_RULE_ID)


def _supplement_nodes(index, decision) -> frozenset[str]:
    """两条补读规则**引入**的读集节点（各自的补读根及其子树）。

    把"本层原有读集"和"补读加的"分开清点：补读只做加法，因此按它切出来的前缀必须与
    旧口径**逐位相同**，后缀必须整段是补读子树。两条规则各按各的字段取根，不混成一个。
    """
    return frozenset(n for r in (tuple(decision.supplement_root_node_ids)
                                 + tuple(decision.lift_root_node_ids))
                     for n in index.subtree_of(r))


def _supplement_rank_ids(decision) -> frozenset[str]:
    """候选列表里**由补读登记**的行（凭原因字串认，不凭得分认）。

    得分 0 不是补读的身份证：本层也可能有 0 分候选；补读行的依据是它自己那条规则说明。
    两条规则的行都算——它们同属"不是打分选出来的读根"这一类。
    """
    return frozenset(c.node_id for c in decision.ranked
                     if any(rid in r for rid in _SUPPLEMENT_RULE_IDS
                            for r in c.reasons))


def _lift_rank_ids(decision) -> frozenset[str]:
    """候选列表里**由 `anp-9` 上提补读**登记的行（只认这一条规则的 id）。"""
    return frozenset(c.node_id for c in decision.ranked
                     if any(NAV.NAV_LIFT_RULE_ID in r for r in c.reasons))


def _supplement_own_rows(index, decision) -> tuple[str, ...]:
    """只由 `anp-7`（自带正文那一条）补进来的根——本测试里凡断"自带实质正文"都只对它。"""
    return tuple(decision.supplement_root_node_ids)


#: 父主题一族的 aspect **前缀**：取自冻结 Contract 的 aspect id 命名空间，
#: 不是文档标题，也不是答案词。
_MODEL_ASPECT_PREFIX = "company_business_model."

#: 指定的误召回反例（M930-3 业务纵链补充门回归样本）：
#: (aspect_id, 误召回靶点的**标题字串**, 说明)。靶点字串是**样本事实**，只在测试里出现，
#: 不参与任何生产规则；靶点在本树里不存在时如实记为"未执行"。
_COUNTEREXAMPLES = (
    ("company_business_model.sales_mode", "销售模式",
     "销售模式不得把收入披露标题当读根"),
    ("company_business_model.procurement_mode", "关联交易",
     "采购不得把关联交易表所在节点当读根"),
)

#: M930-3 的**必须过反例**：`anp-7` 要修的正是它。两份年报上本层键把 `main_business`
#: 稳定送到「四、主营业务分析 / 2、收入与成本」，而「报告期内公司从事的主要业务 /
#: 1、主要业务」没进读集。`needle` 是**样本事实**，只在测试与消息标签里出现，**不参与
#: 任何生产规则**；判据全部由生产函数给出（贴标签判缺陷、候选口径判合格根、正文量判实质）。
_MUST_PASS_SUBJECT = (
    ("company_business_main.main_business", "主要业务",
     "主营业务主体正文必须进读集"),
)


def _entry_by_id(requirements, aspect_id: str, labels, siblings):
    """按 Contract aspect id 取该 aspect 的真实导航条目（含祖先层键）。"""
    for _tid, requirement in sorted(requirements.items()):
        for aspect in requirement.aspects:
            if aspect.aspect_id != aspect_id:
                continue
            profile = NAV.build_navigation_profile(
                requirement.aspects, contract_version=requirement.contract_version,
                contract_fingerprint=requirement.contract_fingerprint,
                ancestor_labels=labels, sibling_keys=siblings)
            for entry in profile.entries:
                if entry.aspect_id == aspect_id:
                    return profile, entry
    return None


def _counterexample(check, details, index, entry, profile, *, needle: str,
                    label: str) -> dict:
    """反例：一个已知的**误召回靶点**不得成为读根 / 不得进读集。

    `needle` 是靶点标题的字串（测试侧的回归样本，不参与任何生产规则）；
    靶点在这棵树里不存在时**如实记为未执行**（不是"零命中通过"）。
    """
    targets = _titled_like(index, needle)
    decision = NAV.navigate(index, entry, profile=profile)
    info = {"aspect": entry.aspect_id, "needle": needle, "targets": len(targets),
            "status": decision.status,
            "read_chars": 0, "read_titles": [index.normalized_title(n)
                                             for n in decision.read_root_node_ids]}
    for root in decision.read_root_node_ids:
        for node_id in index.subtree_of(root):
            info["read_chars"] += sum(
                len(s) for s in index.synopsis_text(node_id))
    if not targets:
        details.append(f"SKIP {label}: 本树里没有含「{needle}」的标题靶点，"
                       "该反例**未执行**（不记作通过）")
        return info
    if decision.status != "selected":
        # 该 aspect 在本树上压根没定位到任何节点：读集为空，靶点自然没被读。反例**成立**，
        # 但它不构成"规则挡住了误召回"的证据——如实说明，不当作规则通过的证据。
        check(decision.read_node_ids == () and decision.read_root_node_ids == (),
              f"{label}: 未定位时读集 / 读根必须为空（{decision.status}"
              f"/{decision.fallback_reason}）")
        details.append(f"INFO {label}: 该 aspect 走 {decision.status}"
                       f"/{decision.fallback_reason}，读集为空——"
                       "反例成立但不构成规则证据（未执行）")
        return info
    hit = [n for n in targets if n in set(decision.read_node_ids)]
    check(not hit,
          f"{label}: 含「{needle}」的标题靶点不得进读集"
          f"（命中 {[index.normalized_title(n) for n in hit]}）")
    root_hit = [n for n in targets if n in set(decision.read_root_node_ids)]
    check(not root_hit,
          f"{label}: 含「{needle}」的标题靶点不得成为读根")
    by_id = {c.node_id: c for c in decision.ranked}
    bad = [c for n in targets if (c := by_id.get(n)) is not None
           and c.discard_reason not in ("no_complete_label_segment",
                                        "outside_tie_band",
                                        "explicit_cross_reference", "over_budget",
                                        "not_label_anchored",
                                        "not_subject_adjacent")]
    check(not bad,
          f"{label}: 靶点若出现在候选列表里，必须带明确的舍弃原因（可审计）")
    info["audited"] = tuple(
        (index.normalized_title(n), by_id[n].discard_reason)
        for n in targets if n in by_id)
    details.append(f"INFO {label}: 读根标题={info['read_titles']} "
                   f"靶点舍弃={info['audited']}")
    return info


def _real_positive(check, details) -> int:
    pdfs = _real_docs(details)
    if not pdfs:
        return 1
    skipped = 0
    reached = 0
    must_pass = 0
    for pdf in pdfs:
        built = _real_index(details, pdf)
        if built is None:
            skipped += 1
            continue
        skipped_one, reached_one, must_pass_one = _real_document(
            check, details, pdf, *built)
        skipped += skipped_one
        reached += reached_one
        must_pass += must_pass_one
    check(reached > 0,
          "真实文档上跨 question 共享父节点负例**至少执行了一次**"
          f"（执行 {reached} 次；`anp-3` 下同一段会被多个 question 的子项读到）")
    check(must_pass > 0,
          "`anp-7` 主体补读的**必须过反例至少真的执行过一次**"
          f"（补读成功 {must_pass} 次）——否则这条反例退化成空转，"
          "不许用「没有可补的」当通过")
    return skipped


def _real_document(check, details, pdf, index, requirements, snapshot, labels,
                   siblings) -> tuple[int, int, int]:
    tag = pdf.stem
    check(index.unavailable_reason is None, f"{tag}: 真实文档上简介结构可用")
    check(bool(labels), f"{tag}: 从冻结 Contract 派生出祖先声明（真实调用点同源）")
    check(bool(siblings), f"{tag}: 从冻结 Contract 派生出兄弟项排除集（真实调用点同源）")

    selected = 0
    widened: dict[str, int] = {}
    widened_peak: dict[str, int] = {}
    parent_tier_used: list[str] = []
    declared_tier_only: list[str] = []
    #: `anp-7`：补读真的补进来的 aspect，以及补进来的**节标题 → 自有正文量 → 谁在读**。
    #: 后者是读回页"共同读集、逐项支持待核"那一栏的机器可读来源。
    supp_fired: list[str] = []
    supp_added: dict[tuple[str, int], list[str]] = {}
    #: `anp-9`：上提补读真的补进来的节标题 → **子树**正文量 → 谁在读。与 `supp_added`
    #: 分开记：两条规则的合格条件不同，合成的"补充材料总量"两边都说不清。
    lift_added: dict[tuple[str, int], list[str]] = {}
    owner: dict[str, object] = {}
    #: 逐 aspect 的真实终态与祖先层标记（4d 的跨 question 负例要用**同一批**决策）。
    decisions: dict[str, tuple[object, object, bool]] = {}
    question_of: dict[str, tuple[str, str]] = {}
    for topic_id, requirement in sorted(requirements.items()):
        profile = NAV.build_navigation_profile(
            requirement.aspects, contract_version=requirement.contract_version,
            contract_fingerprint=requirement.contract_fingerprint,
            ancestor_labels=labels, sibling_keys=siblings)
        #: 同一份声明、**不带**祖先声明的旧口径（`anp-3` 之前的唯一口径）。
        #: 祖先层没生效时，两者必须**逐位**相同——否则 `anp-3` 就在不该动的地方动了。
        plain = NAV.build_navigation_profile(
            requirement.aspects, contract_version=requirement.contract_version,
            contract_fingerprint=requirement.contract_fingerprint)
        plain_by_id = {e.aspect_id: e for e in plain.entries}
        for entry in profile.entries:
            owner[entry.aspect_id] = requirement
            decision = NAV.navigate(index, entry, profile=profile)
            plain_decision = NAV.navigate(index, plain_by_id[entry.aspect_id],
                                          profile=plain)
            # 读根**凭什么**成为读根：本层走得分，祖先层走**完整标签段**。两层的判据
            # 各自成立，绝不混用（否则"片段命中被当成父节点"会从这里漏过去）。
            ancestor_tier = any(
                NAV.NAV_KEY_TIER_ANCESTOR in reason
                for c in decision.ranked for reason in c.reasons)
            decisions[entry.aspect_id] = (entry, decision, ancestor_tier)
            question_of[entry.aspect_id] = (topic_id, entry.question_id)
            #: `anp-7` 的补读根是**另一条判据**（祖先键的按序近似形态）补进来的，它
            #: 既不是本层打分候选、也不贴完整标签段。因此拆成两段比：本层部分逐位比，
            #: 补读部分单独清点——**不**承诺"所有已成功读集一字不变"，只承诺"只加不减、
            #: 加在末尾、并且在补读栏里点名"。无祖先声明的旧口径 `parent_keys` 为空，
            #: 这条规则在那里根本无从下手（`no_ancestor_keys`），所以必须显式钉住它。
            supp = set(decision.supplement_root_node_ids)
            lifted = set(decision.lift_root_node_ids)
            check(not (supp & lifted),
                  f"{tag}/{entry.aspect_id}: 同一条读根不得同时记成两条规则补的"
                  "（`anp-7` 与 `anp-9` 分账，身份不得混用）")
            prim_roots = tuple(r for r in decision.read_root_node_ids
                               if r not in supp and r not in lifted)
            check(plain_decision.supplement_root_node_ids == ()
                  and plain_decision.supplement_rule_status == "no_ancestor_keys"
                  and plain_decision.lift_root_node_ids == ()
                  and plain_decision.lift_rule_status == "no_ancestor_keys",
                  f"{tag}/{entry.aspect_id}: 无祖先声明的旧口径下**两条**补读规则都不执行"
                  "（`no_ancestor_keys`，不是「补了没成功」）")
            supp_rows = _supplement_rank_ids(decision)
            supp_nodes = _supplement_nodes(index, decision)
            if not ancestor_tier:
                check(decision.band_node_ids == plain_decision.band_node_ids
                      and prim_roots == plain_decision.read_root_node_ids
                      and [(c.node_id, c.score) for c in decision.ranked
                           if c.node_id not in supp_rows]
                      == [(c.node_id, c.score) for c in plain_decision.ranked],
                      f"{tag}/{entry.aspect_id}: 祖先层未生效时**本层三件套**"
                      "（近分带 / 本层读根 / 本层候选与得分）与无祖先声明口径逐位相同"
                      "（`anp-3`/`anp-7` 不在别处动手）")
                check(decision.status == plain_decision.status
                      or (plain_decision.status == "fallback" and bool(supp | lifted)
                          and not prim_roots
                          and decision.selected_node_id in (supp | lifted)),
                      f"{tag}/{entry.aspect_id}: 状态只允许被补读**从 fallback 抬成"
                      "selected**（抬起来时选中节点必须是补读根）；其余情况状态必须相同")
                _plain_read = tuple(plain_decision.read_node_ids)
                check(_plain_read == tuple(decision.read_node_ids)[:len(_plain_read)]
                      and set(decision.read_node_ids) - set(_plain_read)
                      <= supp_nodes,
                      f"{tag}/{entry.aspect_id}: 读集只做加法：原有读集**逐位前缀不变**，"
                      "新增节点只在两条补读规则的子树里有出处"
                      f"（原有 {len(_plain_read)} → {len(decision.read_node_ids)}）。"
                      "不承诺「整段追加在末尾」：上提根可能就是原有读根的**祖先**，"
                      "那时原有节点本就在它的子树里，按子树相减会把它整段抹掉")
            if decision.status == "fallback":
                check(decision.read_node_ids == ()
                      and decision.read_root_node_ids == ()
                      and decision.supplement_root_node_ids == (),
                      f"{tag}/{entry.aspect_id}: fallback 不得携带读集 / 读根 / 补读根")
                continue
            selected += 1
            roots = set(decision.read_root_node_ids)
            band = set(decision.band_node_ids)
            check(bool(roots) and len(roots) == len(decision.read_root_node_ids),
                  f"{tag}/{entry.aspect_id}: 读根非空且不重复")
            check(len(set(prim_roots)) == len(prim_roots)
                  and set(decision.supplement_root_node_ids) <= roots
                  and set(decision.lift_root_node_ids) <= roots,
                  f"{tag}/{entry.aspect_id}: 两条规则的补读根都必须在读集里、读根不重复")
            if ancestor_tier:
                check(entry.parent_keys
                      and all(NAV.segment_hits(index, entry.parent_keys, r)
                              for r in prim_roots),
                      f"{tag}/{entry.aspect_id}: 祖先层的每个**本层读根**都有完整标签段命中")
            else:
                check(all(NAV.score_node(index, entry.nav_keys, r)[0] > 0.0
                          for r in prim_roots),
                      f"{tag}/{entry.aspect_id}: 每个本层读根本身都是命中候选")
            if decision.supplement_rule_status == "fired":
                check(decision.supplement_root_node_ids
                      and all(index.body_chars(r) >= NAV._SUPPLEMENT_MIN_BODY_CHARS
                              for r in decision.supplement_root_node_ids),
                      f"{tag}/{entry.aspect_id}: `anp-7` 补读根都自带实质正文"
                      "（自有正文量 ≥ 下限，不是空标题）")
            else:
                check(decision.supplement_root_node_ids == (),
                      f"{tag}/{entry.aspect_id}: 只有 `fired` 才带 `anp-7` 补读根"
                      f"（{decision.supplement_rule_status}）")
            # `anp-9` 上提补读：判据与上一条**不同**（不要求根自带正文），因此单独断，
            # 且断在它自己的量上——有界**子树**正文量。
            if decision.lift_rule_status == "fired":
                check(decision.lift_root_node_ids
                      and all(index.subtree_body_chars(r)
                              >= NAV._SUPPLEMENT_MIN_BODY_CHARS
                              for r in decision.lift_root_node_ids),
                      f"{tag}/{entry.aspect_id}: `anp-9` 上提根都自带实质**子树**正文"
                      "（子树正文量 ≥ 下限；自有正文可以是 0——正文挂在子节上）")
                check(all(NAV.label_anchored_keys(index, entry.parent_keys, r)
                          for r in decision.lift_root_node_ids),
                      f"{tag}/{entry.aspect_id}: 每个 `anp-9` 上提根的标题都整段贴上"
                      "某个祖先声明键（不是靠片段命中）")
            else:
                check(decision.lift_root_node_ids == (),
                      f"{tag}/{entry.aspect_id}: 只有 `fired` 才带 `anp-9` 上提根"
                      f"（{decision.lift_rule_status}）")
            # 近分带根要么落在读根子树内，要么**因为它的读根没通过读根资格**被整支丢弃
            # （`anp-5`：贴标签 + 主体邻接）。丢弃必须逐条可审计——不许静默消失。
            inside = {b for b in band
                      if any(b == r or b in index.subtree_of(r) for r in roots)}
            dropped = [b for b in band if b not in inside]
            by_cand = {c.node_id: c for c in decision.ranked}
            check(all(by_cand.get(b) is not None
                      and by_cand[b].discard_reason in ("not_label_anchored",
                                                        "not_subject_adjacent")
                      for b in dropped),
                  f"{tag}/{entry.aspect_id}: 每个近分带根都落在某个读根子树内，"
                  f"或被读根资格刷掉并留下可审计原因（被刷掉 {len(dropped)} 个）")
            check(set(decision.read_node_ids)
                  <= _read_set_under(index, roots),
                  f"{tag}/{entry.aspect_id}: 读集完全由读根子树展开")
            if decision.unread_total == 0:
                check(set(decision.read_node_ids) == _read_set_under(index, roots),
                      f"{tag}/{entry.aspect_id}: 未截断时读集恰等于读根子树并集")
            check(all((c.discard_reason is None) == c.in_read_set
                      and (c.discard_reason is None
                           or c.discard_reason in NAV.DISCARD_REASONS)
                      for c in decision.ranked),
                  f"{tag}/{entry.aspect_id}: 候选的读/舍弃原因互补且封闭")
            check(not decision.produces_coverage(),
                  f"{tag}/{entry.aspect_id}: 真实文档上的导航同样不产生 coverage")
            # `anp-7` 补读过的 aspect / 补读进来的标题（读回页要按这个分"本层定位的"与
            # "按祖先键补的"两栏，不能混成一句"材料充分"）。
            if decision.supplement_root_node_ids or decision.lift_root_node_ids:
                supp_fired.append(entry.aspect_id)
            for root in decision.supplement_root_node_ids:
                supp_added.setdefault((index.normalized_title(root),
                                       index.body_chars(root)), []).append(
                    entry.aspect_id)
            for root in decision.lift_root_node_ids:
                lift_added.setdefault((index.normalized_title(root),
                                       index.subtree_body_chars(root)),
                                      []).append(entry.aspect_id)
            # 「上提扩到的正文」= 读集里**不在任何近分带根子树内**却持有 span 的节点。
            # 补读子树另有清点（`supp_added`）：那是另一条规则补的，混进"上提扩读"会把
            # 两条规则的执行情况加在一起，两边都说不清。
            band_subtrees = {n for b in band for n in index.subtree_of(b)}
            newly = [n for n in decision.read_node_ids
                     if n not in band_subtrees and n not in supp_nodes
                     and index.span_count_by_node[n] > 0]
            widened[entry.aspect_id] = len(newly)
            widened_peak[entry.aspect_id] = max(
                [len(s.normalized_text or "") for n in newly for s in snapshot.spans
                 if s.node_id == n and s.role == "body"] or [0])
            if ancestor_tier:
                parent_tier_used.append(entry.aspect_id)
            else:
                declared_tier_only.append(entry.aspect_id)

    # ---- 4e. 兄弟项排除的真实**前后对照**（`anp-6`）-------------------------
    # 合成用例只能证明机制；真实文档上必须证明两件事：(1) 这条规则**只减不增**，且减掉的
    # 每一个键都是**同 question 兄弟 aspect 声明的字段**；(2) 它在本树上**真的改变了取材**
    # （哪些章节不再进读集）——若一处都没改，本规则在这棵树上就是**未执行**，如实记下，
    # 不能拿"规则写进文档"当已生效。
    reduced: list[str] = []
    changed: list[tuple[str, str, str]] = []
    for aspect_id in sorted(decisions):
        entry, decision, _ancestor = decisions[aspect_id]
        old_keys = NAV.parent_nav_keys(entry.nav_keys, labels[aspect_id])
        dropped = tuple(sorted(set(old_keys) - set(entry.parent_keys)))
        if not dropped:
            continue
        reduced.append(aspect_id)
        over = sorted(set(dropped) - set(siblings[aspect_id]))
        check(not over,
              f"{tag}/{aspect_id}: 兄弟项排除只减**兄弟 aspect 声明的键**"
              f"（越删：{over}）")
        check(set(old_keys) >= set(entry.parent_keys),
              f"{tag}/{aspect_id}: 排除集只减不增（排除后仍应是排除前的子集）")
        req = owner[aspect_id]
        legacy, legacy_profile = _legacy_sibling_off(
            entry, old_keys, contract_version=req.contract_version,
            contract_fingerprint=req.contract_fingerprint)
        legacy_decision = NAV.navigate(index, legacy, profile=legacy_profile)
        legacy_read = set(legacy_decision.read_node_ids)
        for key in dropped:
            if not index.key_has_segment_match(key):
                continue
            for node_id in index.node_ids:
                if not NAV.segment_hits(index, (key,), node_id):
                    continue
                if node_id in legacy_read \
                        and node_id not in set(decision.read_node_ids):
                    changed.append((aspect_id, key, index.normalized_title(node_id)))
    if reduced:
        details.append(f"INFO {tag}: 兄弟项排除改变了祖先层键的 aspect {len(reduced)} 个")
    if changed:
        details.append(f"INFO {tag}: 兄弟项排除在真实文档上改变取材 {len(changed)} 处："
                       + "；".join(f"{a} 因删「{k}」不再读《{t}》"
                                   for a, k, t in changed))
    else:
        details.append(f"INFO {tag}: 兄弟项排除在本树上**未改变任何取材**"
                       "（没有任何被删键曾把某章读进读集）——不记作通过")

    check(selected > 0, f"{tag}: 真实文档上至少一个 aspect 走 selected（{selected} 个）")
    check(all(v == 0 or v > 0 for v in widened.values()), f"{tag}: 上提扩读清点自洽")
    expanded = sorted(k for k, v in widened.items() if v > 0)
    if not expanded:
        # 该机制在这棵树上**没被执行**：近分带根上提后都还是自身。如实记录，不记作通过
        # （`anp-5` 之后读根资格先刷掉一部分根，某些树上确实一支都不上提）。
        details.append(f"INFO {tag}: 本树上没有 aspect 发生上提扩读"
                       "（近分带根上提后都是自身）——该机制在本树**未执行**")
    if expanded:
        # 取扩读正文最长者：这条断言必须落在真实长正文上，不能靠"最短的一条"过关。
        aspect_id = max(expanded, key=lambda a: widened_peak[a])
        peak = widened_peak[aspect_id]
        check(peak > 100,
              f"{tag}/{aspect_id}: 上提扩到的读集里确有长正文 span（最长 {peak} 字）")
        check(all(widened_peak[a] > 0 for a in expanded),
              f"{tag}: 每个发生上提扩读的 aspect 都真的扩到了正文（不是空节点）")

    # ---- 4b. 子项读父节点**正文**（不是只命中标题）--------------------------
    # 选哪些 aspect 只按**冻结 Contract 的 aspect id 前缀**，不按文档标题字串筛。
    model_req = next((r for tid, r in sorted(requirements.items())
                      if any(a.aspect_id.startswith(_MODEL_ASPECT_PREFIX)
                             for a in r.aspects)), None)
    if model_req is None:
        details.append(f"SKIP {tag}: Contract 里没有承载该父主题的 topic，"
                       "父节点正文回归**未执行**（不记作通过）")
    else:
        profile_m = NAV.build_navigation_profile(
            model_req.aspects, contract_version=model_req.contract_version,
            contract_fingerprint=model_req.contract_fingerprint,
            ancestor_labels=labels, sibling_keys=siblings)
        sub_entries = [e for e in profile_m.entries
                       if e.aspect_id.startswith(_MODEL_ASPECT_PREFIX)]
        check(bool(sub_entries),
              f"{tag}: 该父主题下确有子项 aspect（{len(sub_entries)} 个）")
        #: 这棵树里**到底有没有**该父主题章节：由 Contract 祖先键的整段命中判定
        chapter_keys = tuple(k for e in sub_entries for k in e.parent_keys)
        chapter_nodes = tuple(
            n for n in index.node_ids
            if any(index.key_segment_forms(k, n) for k in chapter_keys))
        if not chapter_nodes:
            details.append(
                f"INFO {tag}: 本树没有该父主题章节标题（Contract 祖先键无一整段命中）"
                "→ 父节点正文回归**未执行**（不记作通过）")
            for e in sub_entries:
                d = NAV.navigate(index, e, profile=profile_m)
                check(not any(NAV.NAV_KEY_TIER_ANCESTOR in r
                              for c in d.ranked for r in c.reasons),
                      f"{tag}/{e.aspect_id}: 树里没有该父主题章节时"
                      "**不得**凭空走祖先层（不许猜）")
        else:
            reached: list[tuple[str, tuple[str, ...], int]] = []
            for e in sub_entries:
                d = NAV.navigate(index, e, profile=profile_m)
                if d.status != "selected" or not d.read_root_node_ids:
                    check(False, f"{tag}/{e.aspect_id}: 未定位到任何节点"
                                 f"（{d.status}/{d.fallback_reason}）")
                    continue
                body = sum(
                    len(s.normalized_text or "")
                    for r in d.read_root_node_ids for n in index.subtree_of(r)
                    for s in snapshot.spans if s.node_id == n and s.role == "body")
                reached.append((e.aspect_id,
                                tuple(index.normalized_title(r)
                                      for r in d.read_root_node_ids), body))
            check(len(reached) == len(sub_entries),
                  f"{tag}: 该父主题下每个子项都定位到了读根"
                  f"（{len(reached)}/{len(sub_entries)}）")
            if reached:
                check(len({r[1] for r in reached}) == 1,
                      f"{tag}: 子项读根落在**同一个**父节点上"
                      f"（{ {r[1] for r in reached} }）")
                check(all(r[2] > 0 for r in reached),
                      f"{tag}: 每个子项的读集里都**确有正文 span**（不是空标题）")
                peak = max(r[2] for r in reached)
                check(peak > 100,
                      f"{tag}: 父节点正文到达读集（最长 {peak} 字）"
                      "——这只证明**材料到达**，不证明任何子项 covered")
            details.append(f"INFO {tag}: 子项→父节点正文 明细="
                           f"{[(a, t, c) for a, t, c in reached]}")

    # ---- 4f. `anp-7` 主体补读：真实文档上的**必须过反例** ----------------------
    # 反例（M930-3 业务纵链）：本层键把 aspect 稳定送到**别的栏目**那一节（真实两份年报上
    # 是「四、主营业务分析 / 2、收入与成本」），而「报告期内公司从事的主要业务 / 1、主要业务」
    # 一个字都没进读集。这一节在树上确实存在、自带实质正文（> 100 字），由**生产判据**
    # （`subject_body_supplement` 的候选口径，不带读集枚举）判定，不是测试自造的词表。
    #
    # 判据写成**前提驱动**的析取，两件都要钉住：
    # - 缺陷不成立（读集里已有一节整段贴上祖先键）→ 本条不适用，如实记 `no_defect`；
    # - 缺陷成立且树上确有合格补读根 → **必须** `fired` 且补进来的那一节自带实质正文。
    # 一个都不执行时如实记"未执行"，并另设下限断言，防止这条反例退化成空转。
    must_pass_fired = 0
    must_pass_defect_free = 0
    must_pass_skipped = 0
    for aspect_id, _needle, label in _MUST_PASS_SUBJECT:
        got = _entry_by_id(requirements, aspect_id, labels, siblings)
        if got is None:
            details.append(f"SKIP {tag}/{label}: Contract 里没有 {aspect_id}，"
                           "该必须过反例**未执行**（不记作通过）")
            must_pass_skipped += 1
            continue
        profile_s, entry_s = got
        decision_s = NAV.navigate(index, entry_s, profile=profile_s)
        #: 缺陷判据看的是**本层 + `anp-7`** 读进来的东西，**不含** `anp-9` 的上提根：
        #: 上提根本身就是"整段贴上祖先键的那一节"，把它算进去会让缺陷判据自我满足
        #: （补过之后再看永远"没有缺陷"），那样这条反例就退化成空转。
        lift_s = set(decision_s.lift_root_node_ids)
        anchored = any(NAV.label_anchored_keys(index, entry_s.parent_keys, n)
                       for n in decision_s.read_node_ids if n not in lift_s)
        #: **不带读集**枚举：树上到底有几个合格补读根（证明"没补"不是因为"树上没有"）。
        cands_s = NAV.subject_body_supplement(index, entry_s)[2]["candidates_total"]
        detail_s = (f"{aspect_id}: 缺陷不成立={anchored} 树上合格补读根={cands_s} "
                    f"状态={decision_s.supplement_rule_status} "
                    f"补={[index.normalized_title(r) for r in
                          decision_s.supplement_root_node_ids]} "
                    f"上提={[index.normalized_title(r) for r in
                            decision_s.lift_root_node_ids]}"
                    f"/{decision_s.lift_rule_status}")
        if anchored:
            must_pass_defect_free += 1
            check(decision_s.supplement_rule_status == "no_defect"
                  and decision_s.supplement_root_node_ids == (),
                  f"{tag}/{label}: 读集里已有一节整段贴上祖先键 → `anp-7` 缺陷不成立、"
                  f"一节不补（{detail_s}）")
            details.append(f"INFO {tag}/{label}: {detail_s}")
            continue
        if cands_s == 0:
            #: `anp-7` 这一支无从下手，但 `anp-9` 的上提补读可能仍然把那一节读进来
            #: （真实两份年报上 `main_business` 就是这个形态：`1、主要业务` 已经读到，
            #: 而「四、主营业务分析」子树整段没读）。
            if decision_s.lift_rule_status == "fired" and decision_s.lift_root_node_ids:
                must_pass_fired += 1
                check(all(index.subtree_body_chars(r) > 100
                          for r in decision_s.lift_root_node_ids),
                      f"{tag}/{label}: `anp-7` 无从下手时，`anp-9` 上提补读**必须**把"
                      f"正文挂在子节上的那一节整段读进来（{detail_s}）")
                details.append(f"INFO {tag}/{label}: {detail_s}")
                continue
            details.append(f"SKIP {tag}/{label}: 缺陷成立，但本树上没有合格补读根 → "
                           f"补读**无从下手**（未执行，不记作通过）：{detail_s}")
            must_pass_skipped += 1
            continue
        must_pass_fired += 1
        root_bodies = [index.body_chars(r)
                       for r in decision_s.supplement_root_node_ids]
        check(decision_s.supplement_rule_status == "fired"
              and bool(decision_s.supplement_root_node_ids)
              and set(decision_s.supplement_root_node_ids) <= set(decision_s.read_node_ids),
              f"{tag}/{label}: 缺陷成立且树上有合格补读根 → 补读**必须**把主体正文节"
              f"补进读集（{detail_s}）")
        check(root_bodies and max(root_bodies) > 100,
              f"{tag}/{label}: 补进来的主体节**自带实质正文**（最长 {max(root_bodies)} 字）——"
              "不是把一个空标题或一行勾选读成材料")
        details.append(f"INFO {tag}/{label}: {detail_s}")

    # ---- 4b'. 补读过的 aspect 与"共同读集、逐项支持待核"清点 ------------------
    # 读回页有义务把"本层定位到的"和"按祖先键补的"分两栏显示：同一段原文被多个 aspect
    # 共同读到，只能说明**材料到达**，不能说明每个 aspect 都被**逐项支持**。
    if supp_added:
        for (title, body), aspects in sorted(supp_added.items(),
                                             key=lambda kv: (-len(kv[1]), kv[0])):
            details.append(
                f"INFO {tag}: 补读进来的节《{title}》（自有正文 {body} 字）被 "
                f"{len(aspects)} 个 aspect 共同读入：{sorted(aspects)}"
                "——共同读集，**逐项支持待核**")
    if lift_added:
        for (title, body), aspects in sorted(lift_added.items(),
                                             key=lambda kv: (-len(kv[1]), kv[0])):
            details.append(
                f"INFO {tag}: 上提补读进来的节《{title}》（子树正文 {body} 字）被 "
                f"{len(aspects)} 个 aspect 共同读入：{sorted(aspects)}"
                "——共同读集，**逐项支持待核**")
    own_fired = {a for v in supp_added.values() for a in v}
    lift_fired = {a for v in lift_added.values() for a in v}
    details.append(f"INFO {tag}: 补读 fired {len(supp_fired)} 个 aspect"
                   f"（{sorted(supp_fired)}）；其中 `anp-7` {len(own_fired)} 个、"
                   f"`anp-9` {len(lift_fired)} 个、两条都 fired "
                   f"{len(own_fired & lift_fired)} 个")
    if must_pass_fired + must_pass_defect_free + must_pass_skipped == 0:
        details.append(f"SKIP {tag}: 必须过反例**未执行**（Contract 里没有该 aspect）")
    details.append(f"INFO {tag}: 必须过反例 执行/补读={must_pass_fired} "
                   f"执行/无缺陷={must_pass_defect_free} 未执行={must_pass_skipped}")
    details.append(f"INFO {tag}: selected={selected} 祖先层定位={len(parent_tier_used)} "
                   f"仅本层={len(declared_tier_only)} 上提扩读="
                   f"{ {k: (widened[k], widened_peak[k]) for k in expanded} }")

    # ---- 4c. 指定误召回反例（回归样本靶点；靶点缺席即如实记未执行）-------------
    for aspect_id, needle, label in _COUNTEREXAMPLES:
        entry_c = _entry_by_id(requirements, aspect_id, labels, siblings)
        if entry_c is None:
            details.append(f"SKIP {tag}/{label}: Contract 里没有 {aspect_id}，"
                           "该反例**未执行**（不记作通过）")
            continue
        profile_c, entry_c = entry_c
        _counterexample(check, details, index, entry_c, profile_c,
                        needle=needle, label=f"{tag}/{label}")

    # 通用负例：同一棵真实树上，无关标题 → 低置信、不读（误召回负例）
    profile_n, entry_n = _entry("董事长的私人游艇", aspect_id="a-real-none")
    none_decision = NAV.navigate(index, entry_n, profile=profile_n)
    check(none_decision.status == "fallback"
          and none_decision.fallback_reason == "low_confidence"
          and none_decision.read_node_ids == ()
          and none_decision.ranked == (),
          f"{tag}: 真实文档上无关标题 → 低置信、读集为空、候选可审计（误召回负例）")

    # ---- 4d. 跨 question 共享父节点负例（`anp-4` 新增）-----------------------
    # 某个 question 归属到的 topic 标题段，其父节点**只能**被该 question 的子项按祖先层
    # 读到。`anp-3` 下这一条必然失败：整段标题交给每个 aspect，于是同一个父节点会被
    # 多个 question 的子项同时读成"有效材料"。
    reached = 0
    for topic_id, rows_t in NAV.contract_topic_segment_assignments(
            _contract()).items():
        for row in rows_t:
            if row.reason != "topic-segment-assigned":
                continue
            nodes = {n for n in index.node_ids
                     if index.key_segment_forms(row.segment, n)}
            if not nodes:
                continue
            reachers = sorted(
                aid for aid, (e, d, anc) in decisions.items()
                if anc and question_of[aid][0] == topic_id
                and set(d.read_root_node_ids) & nodes)
            if not reachers:
                continue
            reached += 1
            outsiders = [a for a in reachers
                         if question_of[a][1] != row.question_id]
            check(not outsiders,
                  f"{tag}: 段「{row.segment}」的父节点只被**它自己的** question 子项"
                  f"按祖先层读到（越界读到：{outsiders}）")
            details.append(f"INFO {tag}: 段「{row.segment}」祖先层读到的子项 "
                           f"{[a.split('.')[-1] for a in reachers]}")
    if not reached:
        details.append(f"INFO {tag}: 该树没有可归属段对应的父节点标题 → "
                       "跨 question 负例**未执行**（不记作通过）")
    return 0, reached, must_pass_fired


# ---------------------------------------------------------------------------

def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    def expect_error(fn, exc, msg: str, *, needle: str = "") -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc as e:
            if needle and needle not in str(e):
                failed += 1
                details.append(f"FAIL {msg}：原因不符（{str(e)[:120]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:120]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    _mechanics(check, expect_error, details)
    _parent_fallback(check, expect_error, details)
    _read_root_qualification(check, details)
    _supplement(check, expect_error, details)
    _real_contract_assignment(check, details)
    _real_join_fallback(check, details)
    skipped += _fixture_positive(check, details)
    skipped += _real_positive(check, details)
    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

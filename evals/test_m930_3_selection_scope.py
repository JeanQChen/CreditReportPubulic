"""M930-3 定点返修②：勾选表单行的**栏目归属**与**支撑资格**（`tmr-3` / `pw-13`）。

跑法（无管道/无重定向）：`python -X utf8 -m evals.test_m930_3_selection_scope`

本模块测的是 r6 真实 run 里被点名的三件事。三条都**只**纠正「读成了什么」与「最多能证明
什么」：不删材料、不动身份、不放宽任何门。

0. **栏目归属**（`harness/tree_materials.py`，`tmr-2` → `tmr-3`）：勾选行的「所问事项」
   **只**取行内前缀；行内没写主语时这一列**留空**，**不再**回指所在节点标题。`tmr-2` 的回指
   会把**子项**的勾选状态锚到整个**栏目**上——真实反例是 NDSD_2025 第 28 页那行
   `□适用 √不适用`：它所在节点 `（8） 主要销售客户和主要供应商情况` 是一个**栏目**，原件的
   勾选框属于其下「主要客户其他情况说明」「主要供应商其他情况说明」两个**子项**；同一页紧跟
   披露的集中度表（前五名合计销售额 / 占比）在原件里真实存在、与那行勾选框无关，且当前
   **未取得数字资格**——那是系统的读取/交付缺口，**不是**「来源称集中度不适用」，也**不是**
   「用户未提供」。同批还有 NDSD_2024 p23 那一行：`不适用` 针对的是业务/产品/服务的重大变化，
   不针对随后披露的集中度表。空 `asked_item` 是 fail-closed（`_scope_confined` 对空所问事项
   一律返回 `False`），行本身仍是材料——原文、locator、指纹、选项与选中状态全部留档。
1. **形状归属**（`harness/tree_materials.py`）：真实勾选行的形状是
   ``⟨选项串⟩ ⟨这一行管着的那句话⟩``。旧读法把选项串之后那句当成最后一个选项的标签，
   于是长度一超 ``SELECTION_OPTION_MAX_CHARS`` 就整行退化成**普通正文**——r6 里挂在
   `founded_date` 下的四份股东/实控人勾选材料正是这样被读成正文的（四份都证明不了成立日期）。
   修正后它们读成 `selection_form`，选项串之后那句逐字记进 `trailing_content`（随行留档、
   **不得**用作支撑）。反向错误同样要挡住：勾选行后面接着**一整段披露正文**的混合片段
   不得被吞成表单行——那不叫干净，那叫把合法正文的资格偷走。
2. **支撑资格**（`sections/pack_writer.py`）：`asked_item_applicability_only` 的表单行只授权
   「把它自己问的那件事再说一遍」的候选（候选文本去空白后是 `asked_item` 的子串），且候选文本
   不得含该行的 `trailing_content`。材料**留着**，被纠正的是这条边**最多能证明什么**。
   新原因 `path_b_ineligible_material_scope` 有自己的 typed 逐候选原因与定向出路，与高风险面
   **共用**同一份额度（`MAX_DIRECTED_REPROPOSAL_PASSES`，值仍为 1，预算中性）。

夹具纪律：形状类的正/反例全部是 **r6 真实语料里的原文**（勾选记号按 ``\\uf052`` 写法），
逐字抄自 `evaluation/results/m930_3_acceptance_crossdoc_real_r6/material_pack.json`；
结构类夹具用**真类**（`NS.ClaimCandidate` / `NS.ProposedSupportRef` /
`NS.manifest_member_ref` / `NS.derive_draft_revision`）。不读真实库、不建真实 Pack、
不调 LLM、不联网、不写任何文件。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import run_m930_3_acceptance as ACC
from harness import topic_schema as TS
from harness import tree_materials as TM
from sections import narrative_schema as NS
from sections import pack_writer as PW

#: 现场接线对账（§9.5）读的是本仓自己的 runner 源码文本，不是一段描述。
RUNNER = Path(__file__).resolve().parent.parent / "evaluation" / "run_m930_3_acceptance.py"


class _NS:
    """只读命名空间替身（被读的只有属性，不要求真 Pack/真库对象）。"""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


#: 文档里「已勾」的记号（Unicode 私用区 U+F052，逐字来自 r6 真实语料）与空框。
MARK = ""
BOX = "□"

#: ---- r6 真实语料：勾选行原文（旧读法读成 `text` 的那几条）------------------
#: 形状 = ``⟨选项串⟩ ⟨这一行管着的那句话⟩``。它们分别挂在「控股股东报告期内变更」
#: 「实际控制人报告期内变更」「前10名普通股股东…约定购回交易」「重大诉讼仲裁事项」
#: 「处罚及整改情况」这些节点下——**没有一条**能证明成立日期或业务构成。
ROW_CONTROLLER = f"{BOX}适用 {MARK}不适用 公司报告期控股股东未发生变更。"
ROW_ACTUAL = f"{BOX}适用 {MARK}不适用 公司报告期实际控制人未发生变更。"
ROW_BUYBACK = (f"{BOX}是 {MARK}否 公司前10 名普通股股东、前10 名无限售条件普通股股东"
               "在报告期内未进行约定购回交易。")
ROW_LITIGATION = f"{BOX}适用 {MARK}不适用 公司报告期未发生重大诉讼、仲裁事项。"
ROW_PENALTY = f"{BOX}适用 {MARK}不适用 公司报告期不存在重大处罚及整改情况。"

ROW_TITLES = {
    ROW_CONTROLLER: "（2）控股股东报告期内变更",
    ROW_ACTUAL: "（3）实际控制人报告期内变更",
    ROW_BUYBACK: "（5）公司前10名普通股股东、前10名无限售条件普通股股东在报告期内是否进行约定购回交易",
    ROW_LITIGATION: "1、重大诉讼仲裁事项",
    ROW_PENALTY: "十二、处罚及整改情况",
}

#: ---- 定点业务纠正①：被点名的**真实原文**（逐字抄自 d5 清单的 `reading_view`）-----------
#: 一行是 NDSD_2025 第 28 页 `m02`（`□适用 不适用`，行内无主语），一行是 NDSD_2024 第 22 页
#: `m26`（`2）已签订的重大采购合同截至本报告期的履行情况 □适用 不适用`，主语写在行内）。
#: 两者形状相同、**只在主语在不在行内**这一点上不同——这正是 `tmr-3` 要分开的那条线。
BARE_ROW = f"{BOX}适用 {MARK}不适用"
ROW_IN_SPAN = f"2）已签订的重大采购合同截至本报告期的履行情况 {BOX}适用 {MARK}不适用"
IN_SPAN_ASKED = "2）已签订的重大采购合同截至本报告期的履行情况"

#: 无主语那一行在 d5 清单里挂过的两个**真实**节点标题。两次回指都错，但错法不同：
#:   * `（8）主要销售客户和主要供应商情况` 是一个**栏目**——原件的勾选框属于其下
#:     「主要客户其他情况说明」「主要供应商其他情况说明」两个**子项**，回指把子项状态锚到了
#:     整个栏目上；同一页紧跟披露的集中度表真实存在（前五名合计销售额 / 占比），与这行勾选框
#:     无关，且当前**未取得数字资格** ⇒ 记系统的读取/交付缺口，**不是**「来源称集中度不适用」，
#:     也**不是**「用户未提供」。
#:   * `（7）公司报告期内业务、产品或服务发生重大变化或调整有关情况` 问的是业务/产品/服务的
#:     重大变化：这一行的 `不适用` 与随后披露的集中度表不是同一件事，不得互相顶替。
COLUMN_NODE_TITLE = "（8）主要销售客户和主要供应商情况"
ITEM_NODE_TITLE = "（7）公司报告期内业务、产品或服务发生重大变化或调整有关情况"
BARE_ROW_NODE_TITLES = (COLUMN_NODE_TITLE, ITEM_NODE_TITLE)

#: ---- r6 真实语料：**混合片段**（勾选行 + 整段披露正文）必须留在 `text` -----------
#: 五条都**不是**表单行：四条是「一个句号 + 四个逗号串起的长句」，一条是无句末标点的长串。
#: 它们若被读成表单行，那段合法披露正文就会失去资格。
MIXED_OVERSEAS_A = (
    f"{MARK}适用 {BOX}不适用 报告期内，公司销售境外的主要产品为电池系统，较上年同期相比未发生"
    "明显变化。公司境外收入129,641,258 千元，占本 期营业收入30.60%。公司主要业务地区的经营"
    "环境未发生重大变化，境外客户回款情况正常。")
MIXED_OVERSEAS_B = (
    f"{MARK}适用 {BOX}不适用 报告期内，公司销售境外的主要产品为电池系统，较上年同期相比未发生"
    "明显变化。公司境外收入110,335,509 千元，占本 期营业收入30.48%。公司主要业务地区的经营"
    "环境未发生重大变化，境外客户回款情况正常。")
MIXED_DISCLOSURE_REQ = (
    f"{MARK}适用 {BOX}不适用 公司需遵守《深圳证券交易所上市公司自律监管指引第4 号——创业板"
    "行业信息披露》中的“锂离子电池产业链相关业务” 的披露要求 1）营业收入及营业成本整体情况")
MIXED_LITIGATION_1 = (
    f"{MARK}适用 {BOX}不适用 报告期内，公司发生的诉讼/仲裁案件涉案总金额为2,053,005 千元"
    "（其中公司作为原告/申请人的涉案总金额为1,617,996 千元，作为被告/被申请人的涉案总金额为"
    "435,009 千元），截至报告期末前述案件中尚未结案的涉案总金额为1,606,371 千 元，该等诉讼/"
    "仲裁事项不会对公司的财务状况和持续经营能力构成重大不利影响。")
MIXED_LITIGATION_2 = (
    f"{MARK}适用 {BOX}不适用 报告期内，公司未达到重大诉讼仲裁披露标准的其他诉讼仲裁案件涉案"
    "总金额为4,000,942 千元（其中公司作为原告/申请 人的涉案总金额为2,180,695 千元，作为被告/"
    "被申请人的涉案总金额为1,820,246 千元），截至报告期末前述案件尚未结案 的涉案总金额为"
    "3,459,700 千元，该等诉讼仲裁事项不会对公司的财务状况和持续经营能力构成重大不利影响。")

MIXED = (MIXED_OVERSEAS_A, MIXED_OVERSEAS_B, MIXED_DISCLOSURE_REQ,
         MIXED_LITIGATION_1, MIXED_LITIGATION_2)

#: ---- 结构夹具（真类）：节内候选 + 一份「只在自己所问事项上说话」的表单行 ----------
FP = "a" * 64
REVISION = NS.derive_draft_revision(
    task_id="t-1", section_id="company", company_id="c-1", report_as_of="2025-12-31",
    contract_version="cv-1", contract_fingerprint="cf-1", writer_policy_version="pw-13",
    prompt_version="pack_section_writer_proposals_v1", model_policy="stub",
    manifest_id="man-1", manifest_fingerprint=FP)
FORM_PACK_ID = "pack-1"
#: 主夹具材料：**主语写在行内**的真实行（d5 清单 `m26`）⇒ 所问事项有据可取。
FORM_MATERIAL_ID = "mat-form-inspan"
FORM_MEMBER_REF = NS.manifest_member_ref(FORM_PACK_ID, FORM_MATERIAL_ID)
#: 对照材料：**行内无主语**的真实行（d5 清单 `m02` / `m08`）⇒ 所问事项留空。
BLANK_MATERIAL_ID = "mat-form-blank"
BLANK_MEMBER_REF = NS.manifest_member_ref(FORM_PACK_ID, BLANK_MATERIAL_ID)
#: 这条表单行问的是「2）已签订的重大采购合同截至本报告期的履行情况」（逐字来自行内前缀）。
FORM_ASKED_ITEM = IN_SPAN_ASKED
#: 「超出所问事项」的**见证文本**：这一行管着的那句话（真实原文）。它既不在这条所问事项之内，
#: 也**不得**被当成"原文如此所以有资格"——`no_support_from_trailing_content` 的执行见证。
FORM_TRAILING = "公司报告期控股股东未发生变更。"


def candidate(text: str, *, fact_type: str = "descriptive") -> NS.ClaimCandidate:
    return NS.ClaimCandidate.create(
        draft_revision=REVISION, task_id="t-1", section_id="company", company_id="c-1",
        report_as_of="2025-12-31", contract_version="cv-1", contract_fingerprint="cf-1",
        claim_text=text, fact_type=fact_type)


def path_b_edge(*, subject_id: str, material_id: str = FORM_MATERIAL_ID,
                container_id: str = FORM_PACK_ID) -> NS.ProposedSupportRef:
    """一条**闭合的**路径 B 支撑边（载体 + payload + exact locator 同在一条边上）。"""
    return NS.ProposedSupportRef.create(
        binding_subject_kind="claim_candidate", binding_subject_id=subject_id,
        draft_revision=REVISION, manifest_id="man-1", manifest_fingerprint=FP,
        authority_kind="topic_pack", authority_container_id=container_id,
        source_identity="evidence:ev-1", provenance_identity="prov-1",
        support_role="primary", support_semantics="factual",
        authorization_path="path_b_material_derived", content_fingerprint=FP,
        dependency_fingerprint="dep-1", material_id=material_id,
        payload_ref={"object_type": "research_material"},
        locator_ref=NS.char_range_locator("evidence:ev-1", 3, 40))


def form_material(*, text: str, section_path: str = "", pack_id: str = FORM_PACK_ID,
                  material_id: str = FORM_MATERIAL_ID) -> _NS:
    """一份表单行材料：资格列由**生产派生函数**给出，不手写列值。

    `section_path` 只是**导航坐标**（locator 上原件记的那条），**不参与**所问事项——
    `tmr-3` 起两者彻底分开，因此这里给不给它都不影响 `asked_item`。
    """
    reading = TM._selection_reading(text)
    if reading is None or not reading["resolved"]:
        raise AssertionError("夹具自身失效：这段原文不是一张可判定的勾选行")
    columns = TM.selection_form_columns(
        selection=reading, locator={"section_path": section_path},
        source_identity="evidence:ev-1")
    return _NS(member_ref=NS.manifest_member_ref(pack_id, material_id),
               content_qualification={"kind": "selection_form", "selection": columns})


def main() -> dict:
    passed = 0
    failed = 0
    details: list[str] = []
    runner_src = RUNNER.read_text(encoding="utf-8")

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            details.append(f"FAIL {msg}")

    def expect_raises(msg: str, fn, exc, *, needle: str = "") -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc as error:
            if needle and needle not in str(error):
                failed += 1
                details.append(f"FAIL {msg}（异常文本里没有 {needle!r}：{error}）")
            else:
                passed += 1
        except Exception as error:                                   # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}（抛的是 {type(error).__name__}：{error}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}（没有抛）")

    # ==================================================================
    # 1. 版本号：形状读法与派生列变了，形状版本就必须跟着走
    # ==================================================================
    # 同一段真实文本在 `tmr-1` 下读成 `text`、在 `tmr-2` 下读成 `selection_form`：payload 字节
    # 与指纹都不同，旧读者会把表单行当正文用。只加一列（`trailing_content`）也要升包版本：
    # 旧读者看不到那一列，会把选项串之后那句读成选项标签或正文。
    # `tmr-2` → `tmr-3`（定点业务纠正①）：第一列的**取值来源**变了——所问事项只取行内前缀，
    # 行内没写主语时留空，不再回指所在节点标题。同一份原文（`□适用 不适用`）在 `tmr-2` 下
    # 产出 `asked_item=（8）主要销售客户和主要供应商情况`（一个**栏目**的名字），在 `tmr-3` 下
    # 产出空串：`asked_item` 不同 ⇒ 派生列字节不同 ⇒ payload 与 content 指纹不同，因此必须升号。
    check(TS.TREE_MATERIAL_RESOLVER_VERSION == "tmr-3",
          f"表单行读法变了，解析器版本必须升到 tmr-3，实为 {TS.TREE_MATERIAL_RESOLVER_VERSION!r}")
    check("question_not_resolvable" not in TM.TREE_MATERIAL_SELECTION_UNRESOLVED_REASONS,
          "`question_not_resolvable` 必须退出封闭子原因词表：所问事项留空**不再是**读不定，"
          "留着它会诱使下游把「没有主语」读成「这一行作废」")
    # `/3` → `/4`（acc-37，业务取材纵链修复 §二）：本模块钉的六列 / `permitted_use` /
    # `exclusions` **一字未减**；`/4` 动的是**另一条信道**——**表对象**信封按设计不带 §二 2.3 的
    # `content_qualification`，过去它在这一列一律落 `None`，于是已准入、已保留、已进 Writer 清单
    # 的表对象被印成「内容形态：读不出」。现在表对象另成 `kind=table_object` 一族、读同信封的
    # `reading_policy`，资格列因此多出这一族取值。旧读者会把表对象读成 `unavailable`。
    # `/4` → `/5`（acc-39，§0.18 W8 单通道的读取面）：`reading_policy` 多出 `envelope_kind`
    # （读回侧同时认 `gtm-1` 与 `tom-1` 两条表对象信道，读数必须带出实际观察到的是哪一条）。
    # 本模块钉的六列 / `permitted_use` / `exclusions` 仍**一字未减**。
    check(ACC.MATERIAL_PACK_SCHEMA_VERSION == "material-pack/5",
          f"资格列从五列变六列（`/3`）、表对象另成一族（`/4`）、两条表对象信道各自可辨（`/5`），"
          f"包形状版本必须升到 material-pack/5，实为 {ACC.MATERIAL_PACK_SCHEMA_VERSION!r}")
    # 返修 ④（`pw-14`）：生成器看到的输入面又变了（materials 行多了来源角色 `source_role`），
    # 支撑资格判据之外**再加**一条期间/来源角色判据。同一份模型在 `pw-13` 与 `pw-14` 下会给出
    # 不同提案集，因此版本必须继续前进（它参与 Draft 身份）。
    # `pw-15`（恢复材料驱动写作）：输入面又变了（要求**先**给自然草稿、**再**为草稿里每个事实
    # 原子单独提交候选），产物形状也变了（门前束与 `SectionDraft` 多一层草稿层）。同一份返回在
    # `pw-14` 与 `pw-15` 下会被读成不同的东西，因此版本继续前进。
    # `pw-16`（真实写作请求定点返修）：请求面又变了两处——`natural_prose_draft` 进 `output_schema`
    # 且与 `_PLAN_KEYS` 同位（**返回里没有它、或它是空的**不再是合法束，而是 typed failure
    # `natural_prose_draft_missing`），草稿单元的出处分**两条互斥的轴**（材料行 / 权威事实行，
    # 恰有一条非空）。同一份返回在 `pw-15` 与 `pw-16` 下会被读成不同的东西（`pw-15` 会静默接受
    # 「有候选、无草稿」并退回 Claim 拼文），因此版本继续前进。
    # `pw-17`（定点返修 P2）：拒绝原因 `path_b_unproven_current_state` 多带一个 typed 分级码
    # （`source_period_scope_dropped` = 有材料包含它、但只有**截掉源句自带期间/范围限定**的那种
    # 包含），定向重提案说明据此多一条出路。同一份模型返回在 `pw-16` 与 `pw-17` 下会被读成不同
    # 的东西（前者把两种来由写成同一条），因此版本继续前进。
    # `pw-18`（r8 前的最小请求自洽修正）：请求里 `output_schema.natural_prose_draft` 的**示例**
    # 改成按本请求的输入面选那条合法轴（旧示例两条轴同时填满，与同一份请求的 rules 与解析面
    # 逐字矛盾）。同一份模型返回在 `pw-17` 与 `pw-18` 下会被读成不同的东西（前者照抄示例即被
    # 自己的请求整批拒绝），因此版本继续前进。
    # `pw-19`（同类缺陷的剩余三处，一次修尽）：`pw-18` 只把 `natural_prose_draft` 的示例按输入面
    # 分档，而同一份请求的**候选首条支撑边**仍写死 `f1`（材料轴节根本没有事实行）、
    # `narrative_draft_units` 的 **context 边**仍写死 `m1`（事实轴节没有材料行）、补件示例的
    # `budget_hint` 仍是空串（检索侧对空白 fail-closed）。同一份模型返回在 `pw-18` 与 `pw-19`
    # 下会被读成不同的东西（前者照抄示例即在自己的请求上被整批拒绝，空预算补件要到执行门才炸），
    # 因此版本继续前进。
    # `pw-20`（r8 后业务纵链收口 §一）：请求面新增**逐批支撑范围** `batch_support_scope`，
    # 并让「本批零候选 ⇒ 草稿与草稿单元都为空」成为确定性判据。同一份模型返回在 `pw-19` 与
    # `pw-20` 下会被读成不同的东西：0 条候选 + 3 段「本轮未取得」的草稿单元，在 `pw-19` 下能
    # 整批通过结构校验并把缺失陈述带进最终自然段，在 `pw-20` 下整批被拒（typed
    # `batch_candidate_witness_missing`）。因此版本继续前进。
    # `pw-21`（指令 D §二·三条日期轴）：改的是**提示词那一侧**（新资产 v11 / `proposals-15`，
    # 新增「材料披露的写法」一节：归属语不由写者写、不得不成期间地写成「一直如此」、新旧实质
    # 差异不得抹平、新闻事件日与发布日分开、披露日未知就标未知、`report_as_of` 不写）。
    # 请求面的键集与键序、解析侧判据一字未动，但**写作策略**变了——提示词资产身份就是策略
    # 身份的一部分，因此版本仍必须前进。同一份模型返回在 `pw-20` 与 `pw-21` 下形状相同，
    # 差别在**模型被要求写什么**，而那是策略版本要覆盖的东西。
    # `pw-22`（M930-3 r9 后返修 B）：改的是**材料驱动写作的闭合读法**——「一条候选恰好一处
    # 表达」改成「键集合一一对上 + 逐 occurrence 核验出处」（新资产 v12 / `proposals-16`）。
    # 请求面的键集与键序、解析侧判据一字未动，但**模型被要求写什么**变了：同一份 r9 返回在
    # `pw-21` 下把「同一件事在两份材料里各写一次」判成闭合失败，在 `pw-22` 下它是合法草稿。
    check(PW.PACK_WRITER_POLICY_VERSION == "pw-22",
          f"写作策略（提示词资产 v12 / proposals-16）变了，写作策略版本必须升到 pw-22，"
          f"实为 {PW.PACK_WRITER_POLICY_VERSION!r}")
    check(PW.PACK_WRITER_POLICY_VERSION != "pw-15",
          "上一版的策略版本号不得被本批复用（同一号下两种读法会让 Draft 身份不可判）")
    check(TM.SELECTION_TRAILING_CLAUSE_MAX == 1,
          "尾随内容的句读上限是形状判据的一部分，必须是可复算的常数（值 1）")
    # 已勾记号在源文件里是一个**不可见**的私用区码位：写死了它才谈得上"逐字对齐真实语料"。
    # 把它钉在码位上，读者误删/改写这个看不见的字符时本句立刻变红。
    check(ord(MARK) == 0xF052 and MARK == "" and MARK != BOX,
          f"已勾记号必须是私用区 U+F052（空框是 U+25A1），实为 {hex(ord(MARK))}")

    # ==================================================================
    # 2. 真实勾选行：读成表单行，选项与「这一行管着的那句话」分开
    # ==================================================================
    for text in (ROW_CONTROLLER, ROW_ACTUAL, ROW_BUYBACK, ROW_LITIGATION, ROW_PENALTY):
        reading = TM._selection_reading(text)
        if reading is None:
            check(False, f"真实勾选行必须读成表单行，实为普通正文：{text[:28]!r}")
            continue
        check(reading["resolved"] and reading["question"] == ""
              and reading["question_scope"] == "",
              f"行内没有主语时，所问事项必须**留空**（不得回指所在节点标题），实为 {reading}")
        labels = [o["label"] for o in reading["options"]]
        check(labels == ["适用", "不适用"] or labels == ["是", "否"],
              f"选项标签只应是选项词，不得吞进这一行管着的那句话，实为 {labels}")
        tail = reading["trailing_content"]
        check(tail and text.endswith(tail),
              f"trailing_content 必须是选项串之后那段原文（逐字，且留在原行末尾），"
              f"实为 {tail!r}")
        check(tail not in labels
              and all(len(x) <= TM.SELECTION_OPTION_MAX_CHARS for x in labels),
              f"尾随内容不得出现在选项里，实为 labels={labels} tail={tail!r}")
        check(len(reading["selected_labels"]) == 1,
              f"这张真实表单行的选中状态可判（恰好一个已勾），实为 {reading['selected_labels']}")

    # 2.0 定点业务纠正①：同一个形状、两种主语来源，读法必须分得开。
    #     这是本批点名那两行的地方——无论节点标题是**栏目**还是**事项**，行内没写主语时
    #     这一列都是空的：节点标题的两种身份不是读法能分辨的，硬猜就是把子项锚到栏目上的那一步。
    for node_title in BARE_ROW_NODE_TITLES:
        reading = TM._selection_reading(BARE_ROW)
        check(reading is not None and reading["question"] == ""
              and reading["question_scope"] == ""
              and reading["resolved"] is True,
              f"行内没写主语 ⇒ 所问事项留空（**不得**取节点标题 {node_title!r}）：{reading}")
    # 反向对照：主语写在行内时，它**有据**可取，且出处如实记 `in_span`。
    in_span = TM._selection_reading(ROW_IN_SPAN)
    check(in_span is not None and in_span["question"] == IN_SPAN_ASKED
          and in_span["question_scope"] == "in_span",
          f"行内自带主语时必须逐字取它（这一行与上面那两行的区别只有这一点）：{in_span}")
    check(BARE_ROW in ROW_IN_SPAN and not TM._selection_reading(BARE_ROW)["question"],
          "两条夹具必须只差「主语在不在行内」，否则这一组正反对照证明不了任何事")

    # 2.1 选中状态与标签逐字对得上（不是「有框就算」）。
    reading = TM._selection_reading(ROW_CONTROLLER)
    check(reading["selected_labels"] == ["不适用"]
          and [(o["marker_state"], o["label"]) for o in reading["options"]]
          == [("hollow", "适用"), ("marked", "不适用")],
          f"选中状态必须落在被勾的那个选项上，实为 "
          f"{[(o['marker_state'], o['label']) for o in reading['options']]}")
    reading = TM._selection_reading(ROW_BUYBACK)
    check(reading["selected_labels"] == ["否"],
          f"「{BOX}是 {MARK}否」这一行选中的是那个被勾的「否」，实为 "
          f"{reading['selected_labels']}")

    # 2.2 端到端：分类函数按节点标题给出 `selection_form` 且**是材料**。
    for text in (ROW_CONTROLLER, ROW_LITIGATION, ROW_PENALTY):
        title = ROW_TITLES[text]
        qual = TM.classify_span_content(_NS(normalized_text=text), node_title=title)
        check(qual.kind == "selection_form" and qual.is_material,
              f"真实勾选行必须成为 selection_form 材料（而不是正文），实为 "
              f"{qual.kind} / {qual.reason}：{text[:24]!r}")
    # 2.3 材料不变式：选中状态读不定的行仍然**不是**材料（读不定就不产出结构化事实）。
    unmarked = f"{BOX}是 {BOX}否 {MARK}法人 {MARK}自然人 最终控制层面持股情况"
    qual = TM.classify_span_content(
        _NS(normalized_text=unmarked),
        node_title="（2）公司最终控制层面是否存在持股比例在10%以上的股东情况")
    check(qual.kind == "selection_form" and not qual.is_material
          and qual.reason == "selection_form_unresolved",
          f"选中状态读不定的表单行不得成为材料，实为 {qual.kind} / {qual.is_material} / "
          f"{qual.reason}")

    # ==================================================================
    # 3. 反例：混合片段（勾选行 + 披露正文）必须留在 `text`
    # ==================================================================
    # 这是本批次最容易犯的反向错误：把一整段合法披露正文吞成表单行，那段正文就只剩
    # 「只读适用性」的资格——那不是干净，那是把真实内容的资格偷走。
    for text in MIXED:
        title = ROW_TITLES[ROW_LITIGATION]
        check(TM._selection_reading(text) is None,
              f"勾选行加整段披露正文的混合片段不得被读成表单行，实为 {text[:28]!r}")
        qual = TM.classify_span_content(_NS(normalized_text=text), node_title=title)
        check(qual.kind == "text" and qual.is_material,
              f"混合片段必须仍是正文材料，实为 {qual.kind} / {qual.reason}：{text[:24]!r}")

    # 3.1 挡下混合片段的两条判据各测一条（否则删掉任一条都不会红）：
    #     长句尾段的**句读上限**（一个句号、四个逗号）——只数句末标点是不够的。
    tail_mixed = MIXED_LITIGATION_1.split(" ", 2)[2]
    tight = TM._tight(tail_mixed)
    check(tail_mixed.count("。") == 1 and tail_mixed.rstrip().endswith("。")
          and sum(1 for ch in tight[:-1] if ch in TM._CLAUSE_PUNCT)
          > TM.SELECTION_TRAILING_CLAUSE_MAX,
          f"这条真实尾段是「一个句号 + 多个逗号」的长句，夹具前提失效：{tail_mixed[:24]!r}")
    check(TM._single_statement_shape(tail_mixed) is False,
          "一个句号但四个逗号串起的长句**不是**这一行管着的那句话，必须判否")
    #     无句末标点的长串（那条披露要求）——长度判据。
    tail_long = MIXED_DISCLOSURE_REQ.split(" ", 2)[2]
    check("。" not in tail_long and len(TM._tight(tail_long)) > TM.SELECTION_ROW_MAX_CHARS
          and TM._single_statement_shape(tail_long) is False,
          f"无句末标点的长串不是一句话，必须判否：{tail_long[:24]!r}")

    # ==================================================================
    # 4. 形状判据的其余边界（每条都对应一个具体的误读方式）
    # ==================================================================
    # 4.1 少于两个记号 ⇒ 逐字沿用旧的整段约束：带分句标点就仍是正文。
    prose_with_box = "公司主营业务为动力电池系统的研发、生产及销售，报告期内 未发生重大变化。"
    check(TM._selection_reading(prose_with_box) is None,
          "句中夹一个框的叙述正文不得被读成表单行")
    check(TM.classify_span_content(_NS(normalized_text=prose_with_box),
                                   node_title="主营业务").kind == "text",
          "句中夹一个框的叙述正文必须仍是正文材料")
    # 4.2 记号落在正文中间（前缀含分句标点）⇒ 不是选项行。
    mid_marker = f"报告期内，公司主要产品 {BOX}产量提升，销量同步增长。"
    check(TM._selection_reading(mid_marker) is None,
          "第一个记号之前已有分句标点时，整段不是选项行")
    # 4.3 选项串本身超长 ⇒ 不是「一张选项行」（长度约束落在选项串上，不落在整段上）。
    long_run = "公司需遵守某自律监管指引中的行业信息披露的披露要求 " * 3 + f"{BOX}适用 {MARK}不适用"
    check(len(TM._tight(long_run[:long_run.index(BOX)])) > TM.SELECTION_ROW_MAX_CHARS
          and TM._selection_reading(long_run) is None,
          "选项串自身超过 SELECTION_ROW_MAX_CHARS 时不得读成表单行")
    # 4.4 只有一个记号 ⇒ 形状未成立（连尾随内容都无从谈起），且选中状态不可判。
    one_marker = f"{BOX}适用"
    single = TM._selection_reading(one_marker)
    check(single is not None and not single["resolved"]
          and single["unresolved_reason"] == "options_not_segmented"
          and single["trailing_content"] == "",
          f"单个记号不得引入尾随内容读法，实为 {single}")
    check(not TM.classify_span_content(_NS(normalized_text=one_marker),
                                       node_title="（1）某某事项").is_material,
          "单个记号读不定选中状态 ⇒ 不是材料")
    # 4.5 纯标点的尾巴不是所述内容（行尾只有一个逗号这类只是标点）。
    punct_tail = f"{BOX}适用 {MARK}不适用，"
    reading = TM._selection_reading(punct_tail)
    check(reading is not None and reading["resolved"] and reading["trailing_content"] == "",
          f"纯标点的尾巴不得记成 trailing_content，实为 {reading}")

    # ==================================================================
    # 5. 支撑资格：`asked_item_applicability_only` 只授权「把那件事再说一遍」
    # ==================================================================
    # 5.0 空的所问事项 = **授权不了任何候选**（`tmr-3` 的 fail-closed 那一侧）。
    #     材料来自真实无主语行（d5 清单 `m02` 的原文），资格表**照样收它**：收的是「它只能在
    #     自己问的事上说话」，而它没有问的事 ⇒ 能说话的范围是**零**。这不是"这一行作废"
    #     （材料、原文、来源、选中状态都还在），也不是"所问事项＝所在节点标题"。
    blank_material = form_material(text=BARE_ROW, material_id=BLANK_MATERIAL_ID,
                                   section_path=COLUMN_NODE_TITLE)
    blank_scope = PW._selection_applicability_scope(
        material_context=_NS(materials=(blank_material,)))
    check(blank_scope == {BLANK_MEMBER_REF: {"asked_item": "", "trailing_content": ""}},
          f"无主语行仍进资格表、但所问事项为空（不是被跳过、更不是取节点标题）：{blank_scope}")
    for text in (COLUMN_NODE_TITLE, ITEM_NODE_TITLE, "不适用", "适用",
                 "主要销售客户和主要供应商情况", f"{COLUMN_NODE_TITLE}不适用"):
        check(PW._scope_confined(text, asked_item="", trailing_content="") is False,
              f"空所问事项 ⇒ 任何候选都不得通过（含写成栏目名的那种）：{text!r}")
    col_cand = candidate(COLUMN_NODE_TITLE)
    check(PW._path_b_ineligible_scope(
        bundle=_NS(candidates=(col_cand,),
                   proposals=(path_b_edge(subject_id=col_cand.candidate_id,
                                          material_id=BLANK_MATERIAL_ID),),
                   units=(), follow_up_specs=()), scope=blank_scope)
        == {col_cand.candidate_id: (BLANK_MEMBER_REF,)},
        "拿**栏目名**当候选、再绑到这一行上当支撑的，必须被逐条点名——"
        "这正是本批纠正的那条误读（`tmr-2` 会给这一行安上栏目名当所问事项）")

    material = form_material(text=ROW_IN_SPAN, section_path=IN_SPAN_ASKED)
    scope = PW._selection_applicability_scope(material_context=_NS(materials=(material,)))
    columns = material.content_qualification["selection"]
    check(scope == {FORM_MEMBER_REF: {"asked_item": FORM_ASKED_ITEM, "trailing_content": ""}}
          and columns["asked_item"] == FORM_ASKED_ITEM
          and columns["permitted_use"] == TM.TREE_MATERIAL_SELECTION_PERMITTED_USE,
          f"主语写在行内的这一份：所问事项逐字取行内前缀、出处 `in_span`，实为 {scope}")
    check("no_support_from_trailing_content" in columns["exclusions"],
          f"排除项必须逐条在列里（读法变了不改排除清单），实为 {columns['exclusions']}")

    def offenders_for(text: str, *, fact_type: str = "descriptive") -> dict:
        cand = candidate(text, fact_type=fact_type)
        bundle = _NS(candidates=(cand,),
                     proposals=(path_b_edge(subject_id=cand.candidate_id),),
                     units=(), follow_up_specs=())
        return PW._path_b_ineligible_scope(bundle=bundle, scope=scope)

    # 5.1 正例：候选文本**逐字**收在该行所问事项之内 ⇒ 本判据不点名。
    inside = "已签订的重大采购合同"
    check(inside in columns["asked_item"],
          f"夹具前提失效：正例文本不在所问事项里：{inside!r}")
    check(offenders_for(inside) == {},
          f"收在所问事项之内的候选不得被点名，实为 {offenders_for(inside)}")
    check(PW._path_b_ineligible_scope(
        bundle=_NS(candidates=(candidate(columns["asked_item"]),), proposals=(), units=(),
                   follow_up_specs=()), scope=scope) == {},
        "与所问事项逐字相同的候选同样不得被点名")
    # 5.1.1 但「资格判据通过」**不等于**「可以入正文」：这条所问事项自己带序号数字
    # （`2）`），因此与本条所问事项逐字相同的那条候选同时踩上高风险面。两条防线各自
    # 独立、各自 fail-closed：本判据放行的那条文本仍要被高风险面挡住。
    check(PW._scope_confined(FORM_ASKED_ITEM, asked_item=FORM_ASKED_ITEM,
                             trailing_content="") is True
          and NS.high_risk_surface_tokens(FORM_ASKED_ITEM) != (),
          f"资格判据放行 ≠ 可以入正文：高风险面必须独立挡住它，实为 "
          f"{NS.high_risk_surface_tokens(FORM_ASKED_ITEM)}")

    # 5.2 反例：候选把这**一行管着的那句话**当正文写 ⇒ 点名，并给出是哪条边。
    #     （`FORM_TRAILING` 是那一行所述内容本身：它既不在这条所问事项之内，也**不得**因为
    #     "原文如此"就被当成有资格。所问事项为空时由 5.0 的 fail-closed 挡住，两处互不替代。）
    offenders = offenders_for(FORM_TRAILING)
    check(offenders == {candidate(FORM_TRAILING).candidate_id: (FORM_MEMBER_REF,)},
          f"写了尾随内容的候选必须被点名，且给出不能承重的那条边，实为 {offenders}")
    wrapped = columns["asked_item"] + FORM_TRAILING
    check(offenders_for(wrapped) == {candidate(wrapped).candidate_id: (FORM_MEMBER_REF,)},
          f"把尾随内容接在所问事项后面同样必须被点名，实为 {offenders_for(wrapped)}")

    # 5.3 反例：空白归一化不是逃生门——判据只去空白，不改写任何字。
    spaced = FORM_TRAILING.replace("未发生", "未 发生").replace("报告期", "报告期 ")
    check(PW._scope_tight(spaced) == PW._scope_tight(FORM_TRAILING)
          and PW._scope_confined(spaced, asked_item=FORM_ASKED_ITEM,
                                 trailing_content=FORM_TRAILING) is False,
          "插入空白后归一形式相同 ⇒ 仍含尾随内容，仍不得通过")
    check(PW._scope_confined(FORM_TRAILING, asked_item=FORM_ASKED_ITEM,
                             trailing_content=FORM_TRAILING) is False,
          "含尾随内容的候选在任何所问事项切法下都不得通过")

    # 5.4 反例：勾选状态本身不得当事实搬走（选项标签不是所问事项的子串）。
    for state in ("不适用", "适用", "否"):
        check(PW._scope_confined(state, asked_item=FORM_ASKED_ITEM,
                                 trailing_content=FORM_TRAILING) is False,
              f"选项标签 {state!r} 不得作为候选文本通过（它不在所问事项里）")
    check(NS.high_risk_surface_tokens("不适用") != (),
          "勾选/适用状态本来就在高风险面词表里（纵深防御：即使资格判据放行也不得入正文）")

    # 5.5 反例：数字 / 否定 / 法人主体 三条各自还有**高风险面**这道独立防线。
    for probe in ("公司境外收入129,641,258 千元。", FORM_TRAILING, "控股股东为某某集团有限公司。"):
        check(NS.high_risk_surface_tokens(probe) != (),
              f"这条文本必须仍被高风险面挡住（资格判据不是唯一防线）：{probe[:20]!r}")

    # 5.6 资格表**不删材料**：没被收录的材料不在表里，也照旧不受这条判据影响。
    prose = _NS(member_ref=NS.manifest_member_ref(FORM_PACK_ID, "mat-prose-1"),
                content_qualification={"kind": "text", "selection": None})
    unresolved = _NS(member_ref=NS.manifest_member_ref(FORM_PACK_ID, "mat-unresolved-1"),
                     content_qualification={"kind": "selection_form", "selection": None})
    mixed_scope = PW._selection_applicability_scope(
        material_context=_NS(materials=(material, prose, unresolved)))
    check(mixed_scope == scope,
          f"资格表只收录可判定且只读适用性的表单行：正文材料与读不定的表单行都不进表，"
          f"实为 {mixed_scope}")
    cand = candidate(FORM_TRAILING)
    prose_edge = path_b_edge(subject_id=cand.candidate_id, material_id="mat-prose-1")
    check(PW._path_b_ineligible_scope(
        bundle=_NS(candidates=(cand,), proposals=(prose_edge,), units=(), follow_up_specs=()),
        scope=PW._selection_applicability_scope(
            material_context=_NS(materials=(prose,)))) == {},
        "支撑边绑的是普通正文材料时，本判据一个字都不说")
    check(PW._path_b_ineligible_scope(
        bundle=_NS(candidates=(cand,), proposals=(prose_edge,), units=(), follow_up_specs=()),
        scope={}) == {},
        "本节没有任何表单行时，本判据必须零成本返回（不得凭空点名）")

    # ==================================================================
    # 6. 作用域：只有路径 B factual 边才进本判据
    # ==================================================================
    # 6.1 path A（预验证权威事实）与表单行的允许用途无关：同一段文本也不得被本判据点名。
    bad_cand = candidate(FORM_TRAILING)
    path_a = NS.ProposedSupportRef.create(
        binding_subject_kind="claim_candidate", binding_subject_id=bad_cand.candidate_id,
        draft_revision=REVISION, manifest_id="man-1", manifest_fingerprint=FP,
        authority_kind="topic_pack", authority_container_id=FORM_PACK_ID,
        source_identity="evidence:ev-1", provenance_identity="prov-1",
        support_role="primary", support_semantics="factual",
        authorization_path="path_a_prevalidated", content_fingerprint=FP,
        dependency_fingerprint="dep-1", fact_id="f-1", material_id=FORM_MATERIAL_ID,
        payload_ref={"object_type": "research_material"},
        locator_ref=NS.char_range_locator("evidence:ev-1", 3, 40))
    check(PW._path_b_ineligible_scope(
        bundle=_NS(candidates=(bad_cand,), proposals=(path_a,), units=(),
                   follow_up_specs=()), scope=scope) == {},
        "路径 A 候选不进本判据（它绑的是预验证权威事实）")
    # 6.2 context 边挂在叙述草稿单元上，不授权任何事实原子 ⇒ 也不进本判据。
    unit = NS.NarrativeDraftUnit.create(draft_revision=REVISION, section_id="company",
                                        index=0, unit_kind="paragraph",
                                        text="本节说明控股股东情况。")
    ctx = NS.ProposedSupportRef.create(
        binding_subject_kind="narrative_draft_unit", binding_subject_id=unit.draft_unit_id,
        draft_revision=REVISION, manifest_id="man-1", manifest_fingerprint=FP,
        authority_kind="topic_pack", authority_container_id=FORM_PACK_ID,
        source_identity="evidence:ev-1", provenance_identity="prov-1",
        support_role="corroborating", support_semantics="context",
        authorization_path="context_only", content_fingerprint=FP,
        dependency_fingerprint="dep-1", material_id=FORM_MATERIAL_ID,
        payload_ref={"object_type": "research_material"},
        locator_ref=NS.char_range_locator("evidence:ev-1", 3, 40))
    check(PW._path_b_ineligible_scope(
        bundle=_NS(candidates=(bad_cand,), proposals=(ctx,), units=(unit,),
                   follow_up_specs=()), scope=scope) == {},
        "context 边不进本判据（它只验证背景/结构/衔接，不授权事实）")

    # ==================================================================
    # 7. typed 审计：原因、成员身份、与整束被拒的完整身份同存
    # ==================================================================
    c_ok = candidate("已签订的重大采购合同")
    bundle = _NS(candidates=(c_ok, bad_cand),
                 proposals=(path_b_edge(subject_id=c_ok.candidate_id),
                            path_b_edge(subject_id=bad_cand.candidate_id)),
                 units=(), follow_up_specs=())
    ineligible = PW._path_b_ineligible_scope(bundle=bundle, scope=scope)
    audit = PW._candidate_audit(bundle=bundle, ineligible=ineligible)
    by_id = {a.candidate_id: a for a in audit}
    check(tuple(a.candidate_id for a in audit) == (c_ok.candidate_id, bad_cand.candidate_id),
          "逐候选审计的顺序必须等于该束的候选顺序（**完整**，不是过滤后的子集）")
    check(by_id[bad_cand.candidate_id].reasons == ("path_b_ineligible_material_scope",)
          and by_id[bad_cand.candidate_id].ineligible_member_refs == (FORM_MEMBER_REF,)
          and by_id[bad_cand.candidate_id].surfaces == (),
          f"被点名的候选必须带 typed 原因与成员身份，实为 "
          f"{by_id[bad_cand.candidate_id].to_dict()}")
    check(by_id[c_ok.candidate_id].reasons == ("not_individually_implicated",)
          and by_id[c_ok.candidate_id].ineligible_member_refs == (),
          f"没被点名的候选拿到的是「不是因为它」，**不是**「可以留用」："
          f"{by_id[c_ok.candidate_id].to_dict()}")
    check("path_b_ineligible_material_scope" in PW.PROPOSAL_SET_REJECTION_KINDS
          and "path_b_ineligible_material_scope" in PW.PROPOSAL_SET_REJECTION_CANDIDATE_REASONS
          and "path_b_ineligible_material_scope" in PW.STRUCTURED_PROPOSAL_SET_REJECTION_KINDS
          and "path_b_ineligible_material_scope" in PW.REPROPOSAL_TRIGGER_KINDS,
          "新原因必须在四张词表里各就各位（整束 kind / 逐候选原因 / 结构化 kind / 触发类）")
    check(PW.PROPOSAL_SET_REJECTION_KINDS.count("path_b_ineligible_material_scope") == 1
          and len(PW.REPROPOSAL_TRIGGER_KINDS) == 4,
          "新原因只出现一次，且触发类恰好四种（四条候选级判据都各有「候选自己能改」的出路："
          "高风险表面 / 材料范围 / 期间位次（`srsc-1`）/ 独立支撑结论（`srsc-2`）；"
          "不得顺手把别的 kind 也拉进来）")

    # 7.1 不变式：原因与成员身份**双向**绑定（缺一边都不得落盘）。
    def raw_audit(**over):
        kw = dict(candidate_id=bad_cand.candidate_id,
                  reasons=("path_b_ineligible_material_scope",),
                  ineligible_member_refs=(FORM_MEMBER_REF,))
        kw.update(over)
        return PW.RejectedCandidateAudit(**kw)

    ok = raw_audit()
    check(ok.to_dict()["ineligible_member_refs"] == [FORM_MEMBER_REF],
          f"成员身份必须随盘落盘，实为 {ok.to_dict()}")
    expect_raises("有原因却没给成员身份时必须抛（原因不得是一句没有落点的理由）",
                  lambda: raw_audit(ineligible_member_refs=()),
                  PW.PackWriterError, needle="ineligible_member_refs")
    expect_raises("给了成员身份却没这条原因时必须抛（身份不得凭空出现）",
                  lambda: raw_audit(reasons=("not_individually_implicated",)),
                  PW.PackWriterError, needle="ineligible_member_refs")

    # ==================================================================
    # 8. 定向重提案：出路写清、旧束原样保留、额度**共用**且不增
    # ==================================================================
    detail = PW._path_b_rejection_detail(high_risk={}, ineligible=ineligible)
    check(bad_cand.candidate_id in detail and FORM_MEMBER_REF in detail,
          f"整束明细必须逐候选点名到身份，实为 {detail}")
    check("asked_item_applicability_only" in detail
          and "no_support_from_trailing_content" in detail,
          f"明细必须把这一列读不出什么写明，实为 {detail}")
    both = PW._path_b_rejection_detail(high_risk={bad_cand.candidate_id: ("12,345",)},
                                       ineligible=ineligible)
    check("12,345" in both and FORM_MEMBER_REF in both and "；" in both,
          f"两种触发同时出现时必须合成**一句**（额度只有一份，不得记成两次被拒），实为 {both}")
    note = PW._directed_reproposal_note(bundle=bundle, detail=detail,
                                        surfaces={}, ineligible=ineligible)
    check(PW.HIGH_RISK_REPROPOSAL_NOTE_VERSION == "hrrp-5"
          and f"（{PW.HIGH_RISK_REPROPOSAL_NOTE_VERSION}）" in note,
          f"说明文本是输入面的一部分，必须带版本号（`srsc-3` 的分级出路同样改输入面），"
          f"实为 {note[:60]!r}")
    check(FORM_TRAILING in note and FORM_MEMBER_REF in note
          and bad_cand.candidate_id in note,
          "被点名的候选必须**逐字**引用，并给出是哪条边")
    check("逐字收进该行所问事项之内" in note and "改绑" in note and "撤下" in note,
          f"出路必须写清（收进所问事项 / 改绑权威事实 / 撤下并如实发补件）：{note[-500:]!r}")
    check("逐条照原样保留" in note,
          f"其余候选必须逐条照原样保留（删掉几条正是被点名的反模式）：{note[-320:]!r}")
    check("重新输出一份完整的提案集" in note,
          "要求的是**完整**提案集重新过门，不是只改被点名的那几条")
    check(PW.MAX_DIRECTED_REPROPOSAL_PASSES == 1,
          f"两种触发共用同一份额度且值不变（预算中性），实为 "
          f"{PW.MAX_DIRECTED_REPROPOSAL_PASSES}")

    # 8.1 新修订必须**重新过门**：逐候选审计里没有「通过」这一侧，条数恒等于该束候选数。
    check(all(a.reasons for a in audit),
          "每条候选都必须有一条 typed 原因（无原因 = 与「被采信」不可区分）")
    check(len(PW._candidate_audit(bundle=bundle, ineligible=ineligible)) == 2,
          "被点名的候选不进「幸存者名单」：审计条数恒等于该束候选数")
    #     方向性：定向重提案只对触发类拒绝排定，且只能排到**后面**的某一轮。
    def record(**over):
        kw = dict(attempt=1, rejection_kind="narrative_gate_blocking",
                  rejection_detail="门规则点名", candidate_ids=(bad_cand.candidate_id,),
                  candidate_audit=(raw_audit(reasons=("gate_blocking",),
                                             rule_ids=("rule-1",),
                                             ineligible_member_refs=()),))
        kw.update(over)
        return PW.ProposalSetRejectionRecord(**kw)

    ok_rec = record(rejection_kind="path_b_ineligible_material_scope",
                    candidate_audit=(ok,), answered_by_attempt=2)
    check(ok_rec.rejection_kind == "path_b_ineligible_material_scope"
          and ok_rec.answered_by_attempt == 2,
          "触发类拒绝可以排定定向重提案（这是额度**可达**的那一面）")
    expect_raises("非触发类拒绝不得排定定向重提案",
                  lambda: record(answered_by_attempt=2),
                  PW.PackWriterError, needle="不得排定定向重提案")
    expect_raises("定向重提案不得排定到被拒的同一轮",
                  lambda: record(rejection_kind="path_b_ineligible_material_scope",
                                 candidate_audit=(ok,), attempt=2, answered_by_attempt=2),
                  PW.PackWriterError, needle="排定到被拒之后")

    # ==================================================================
    # 9. 栏目归属：`material_ids` 只说「记在哪条 aspect 名下」，不说凭什么
    # ==================================================================
    # 9.0 r6 真实读数（逐字抄自 `acceptance_report.json` → `observations.
    # company_material_source_reconciliation`）：`founded_date` 三条导航事件**全是**
    # `status=fallback`、零读节点、零选中，且**连一次树调用都没发过**；四份材料全部由有界
    # Evidence 重切（`EVIDENCE_FALLBACK_RECUT`）交出。对照面 `main_business` 是真读到的：
    # 三条事件、19 个读节点、3 次选中、0 份重切材料。
    FOUNDED = "company_identity_basic.founded_date"
    MAIN = "company_business_main.main_business"
    P99 = ("mat-tm-f8de4ce44b0606782ae4c44a601156f9",
           "mat-tm-322a00015fc4cbd65a7d4f1d46781f85",
           "mat-tm-d23b8ef54dd15edf85a3b7df39c127c4",
           "mat-tm-27229c641b12ee4ba6b66a6705a8ea6c")
    MAIN_MATS = tuple("mat-main-%d" % i for i in range(1, 10))

    def _nav(aspect_id: str, *, status: str, selected, read_nodes: int) -> dict:
        return {"aspect_id": aspect_id, "status": status, "selected_node_id": selected,
                "read_node_count": read_nodes}

    NAV_FOUNDED = [_nav(FOUNDED, status="fallback", selected=None, read_nodes=0)] * 3
    NAV_MAIN = [_nav(MAIN, status="selected", selected="on-%d" % i, read_nodes=6)
                for i in range(3)]
    #: 兜底重切事件：`trigger` 逐字抄 r6 载荷里的取值。
    RECUT_FOUNDED = {"aspect_id": FOUNDED, "layer": "evidence_recut",
                     "trigger": "empty_read_set", "recut_material_ids": list(P99)}
    RECALL_INSPECT_MAIN = {"aspect_id": MAIN, "layer": "tree_inspect", "candidate_count": 7}
    MATERIAL_ASPECTS = {**{m: [FOUNDED] for m in P99},
                        **{m: [MAIN] for m in MAIN_MATS}}
    TOPIC = {FOUNDED: "t-company", MAIN: "t-company"}

    real = ACC._material_attribution(
        recall=[RECUT_FOUNDED, RECALL_INSPECT_MAIN], tree_navigation=NAV_FOUNDED + NAV_MAIN,
        material_aspects=MATERIAL_ASPECTS, aspect_topic=TOPIC)
    by_mat = real["by_material_id"]
    by_aspect = {row["aspect_id"]: row for row in real["by_aspect"]}

    # 9.1 正例①：四份 p99 材料的依据是**兜底重切**，而且逐份都能指到那次重切。
    check([by_mat[m]["basis"] for m in P99] == [ACC.ATTRIBUTION_EVIDENCE_FALLBACK] * 4,
          "founded_date 下的四份材料必须逐份记为兜底重切来的（实得 "
          f"{[by_mat[m]['basis'] for m in P99]}）")
    check(all(by_mat[m]["trigger"] == "empty_read_set" for m in P99),
          "依据必须带上触发原因（`trigger`），不是一句无出处的断言")
    check(by_mat[P99[0]]["rules"] == ["fallback_recut_material_id_membership"],
          "派生规则名必须落盘：读者可以不信结论、只信事件")
    # 9.1.1 正例②：该 aspect 被点名「材料**全部**来自兜底」——栏目覆盖仍未达成。
    check(real["aspects_only_on_fallback_recut"] == [FOUNDED],
          f"该表必须只收「材料全部来自兜底重切」的 aspect（实得 "
          f"{real['aspects_only_on_fallback_recut']}）")
    founded_row = by_aspect[FOUNDED]
    check((founded_row["nav_event_count"], founded_row["nav_read_node_total"],
           founded_row["nav_selected_events"], founded_row["nav_fallback_events"])
          == (3, 0, 0, 3),
          f"导航侧必须逐项交出、且与依据并列（实得 {founded_row}）")
    check(founded_row["by_basis"] == {ACC.ATTRIBUTION_EVIDENCE_FALLBACK: 4},
          "该 aspect 的 by_basis 必须与逐材料读数一致")
    # 9.1.2 反例方向的对照：**真的读到了**的栏目不得被涂成兜底。
    check(by_aspect[MAIN]["by_basis"] == {ACC.ATTRIBUTION_TREE_READ_SET: len(MAIN_MATS)},
          f"main_business 是真读到的，不得被记为兜底（实得 {by_aspect[MAIN]['by_basis']}）")
    check(MAIN not in real["aspects_only_on_fallback_recut"],
          "「读集交出来的」与「兜底补位的」必须分得开——两边都要能被读出来")
    check((by_aspect[MAIN]["nav_read_node_total"], by_aspect[MAIN]["nav_selected_events"])
          == (18, 3),
          f"导航侧读数必须**聚合全部**事件，不得只取最后一条（实得 {by_aspect[MAIN]}）")

    # 9.2 反例：**删掉解释得通的事件，未知必须如实登记为未知**，不得升格成「读到了」。
    #     这是本层最容易被写坏的一处：把「没证据」写成「干净」。
    unexplained = ACC._material_attribution(
        recall=[RECALL_INSPECT_MAIN], tree_navigation=NAV_FOUNDED + NAV_MAIN,
        material_aspects=MATERIAL_ASPECTS, aspect_topic=TOPIC)
    check([unexplained["by_material_id"][m]["basis"] for m in P99]
          == [ACC.ATTRIBUTION_UNATTRIBUTED] * 4,
          "没有兜底重切事件、也没发过树调用时，依据必须是「未知」，**不得**默认成读集")
    check(unexplained["aspects_only_on_fallback_recut"] == [],
          "未知不是兜底：两条互斥结论面不得互相冒充")
    check(sorted(unexplained["unattributed_refs"]) == sorted(f"{FOUNDED}/{m}" for m in P99),
          f"未知归属必须逐条点名（实得 {unexplained['unattributed_refs']}）")
    check("干净" in real["note"] and "未达成" in real["note"],
          "说明文本必须就地挡住「未知＝干净」与「兜底＝栏目已覆盖」两个误读")
    # 9.2.1 反例：**只补一条树调用不足以把兜底材料洗成读集材料**——id 成员关系优先。
    washed = ACC._material_attribution(
        recall=[RECUT_FOUNDED, {"aspect_id": FOUNDED, "layer": "tree_inspect",
                                "candidate_count": 1}],
        tree_navigation=NAV_FOUNDED, material_aspects=MATERIAL_ASPECTS, aspect_topic=TOPIC)
    check([washed["by_material_id"][m]["basis"] for m in P99]
          == [ACC.ATTRIBUTION_EVIDENCE_FALLBACK] * 4,
          "该 aspect 后来发过树调用也**不得**改写已被重切交出的那几份材料的依据")

    # 9.3 反例：同一份材料在两条 aspect 上依据不同 ⇒ 报 `mixed`，不挑一个好看的。
    mixed = ACC._material_attribution(
        recall=[RECUT_FOUNDED, RECALL_INSPECT_MAIN], tree_navigation=NAV_FOUNDED + NAV_MAIN,
        material_aspects={P99[0]: [FOUNDED, MAIN]}, aspect_topic=TOPIC)
    check(mixed["by_material_id"][P99[0]]["basis"] == "mixed"
          and sorted(mixed["by_material_id"][P99[0]]["aspect_bases"].values())
          == sorted([ACC.ATTRIBUTION_EVIDENCE_FALLBACK, ACC.ATTRIBUTION_TREE_READ_SET]),
          "同材料跨 aspect 依据不同时必须报 `mixed`，逐 aspect 的依据也各自留着")

    # 9.4 反例：**不删材料、不改归属**。归属表是派生读数，材料清单一个字都不动。
    listed = {m for row in real["by_aspect"] for m in row["material_ids"]}
    check(listed == set(MATERIAL_ASPECTS),
          "归属表必须逐条覆盖 Pack 里记着的每一份材料，一份都不能少")
    check(all(set(by_mat[m]["aspect_bases"]) == set(MATERIAL_ASPECTS[m])
              for m in MATERIAL_ASPECTS),
          "每份材料的归属必须与原 `material_ids` 逐条对齐：既不多记一条、也不少记一条")
    recut_orphan = ACC._material_attribution(
        recall=[{**RECUT_FOUNDED, "recut_material_ids": list(P99) + ["mat-tm-orphan"]}],
        tree_navigation=NAV_FOUNDED, material_aspects=MATERIAL_ASPECTS, aspect_topic=TOPIC)
    check(recut_orphan["recut_materials_not_attributed"] == [f"{FOUNDED}/mat-tm-orphan"],
          "重切交出来但没被归属的材料必须单列——它是「重切成功、归属没落上」，不是「重切失败」")

    # 9.5 版本与接线：块自带版本、来源只列那两条真事件、报告读的是同一处。
    check(real["schema_version"] == ACC.MATERIAL_ATTRIBUTION_SCHEMA_VERSION
          == "material-attribution/1"
          and real["rule_version"] == ACC.MATERIAL_ATTRIBUTION_RULE_VERSION == "mar-1",
          "归属读数面必须自带 schema 与规则版本（换规则即换号，读者据此对齐）")
    check(real["source_events"] == ["TREE_TOOL_RESULT", "EVIDENCE_FALLBACK_RECUT"],
          "依据的来源必须**恰好**列出那两条真事件，读者才能自己回到轨迹里核")
    check(ACC.ATTRIBUTION_TREE_READ_SET != ACC.ATTRIBUTION_UNATTRIBUTED
          and len({ACC.ATTRIBUTION_TREE_READ_SET, ACC.ATTRIBUTION_EVIDENCE_FALLBACK,
                   ACC.ATTRIBUTION_UNATTRIBUTED}) == 3,
          "三种依据取值必须互不相同（否则这层读不出任何差别）")
    check(json.loads(json.dumps(real, ensure_ascii=False)) == real,
          "归属块必须可 JSON 往返（落盘不许带 dataclass 实例）")
    check(runner_src.count("material_attribution = _material_attribution(") == 1,
          "归属块必须只算一次（两处产物读同一份结果，不出现第二个真值）")
    check('"material_attribution": material_attribution,' in runner_src
          and '"attribution_by_material_id": {' in runner_src,
          "顶层的 `material_attribution` 与 `pack_materials` 里的依据列必须同源交出")
    # 结构性不变式：派生必须在 `material_aspects` **填好之后**做，否则它只会安静地读到空集。
    check(runner_src.index("material_attribution = _material_attribution(")
          > runner_src.index("material_aspects.setdefault(str(material_id), []).append"),
          "归属派生必须在 Pack 材料归属装配**之后**，否则空集会被读成「没有材料需要归属」")

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    import json

    print(json.dumps(main(), ensure_ascii=False, indent=2))

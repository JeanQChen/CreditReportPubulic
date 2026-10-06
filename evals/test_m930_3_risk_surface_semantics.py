"""M930-3 共享高风险扫描语义的**机械**修正（`nrules-10` + `nrules-11`）。

跑法（无管道/无重定向）：`python -X utf8 -m evals.test_m930_3_risk_surface_semantics`

本模块测的是「同一份文本里，哪些字面成分**真的**是高风险表面」。修正都是纯机械的、可逐字
复算的，且**没有删除任何一个风险词或主体后缀**：

1. **标记词子串豁免**（`NS.MARKER_SUBSTRING_EXEMPTIONS`）：`说明` 作为**文书名**的一部分
   （`募集说明书` / `招股说明书`）出现时不算命中。判据是「该标记在文本里的每一次出现都被
   豁免形覆盖」——同一段文本里只要另有一处独立的 `说明`，标记照旧命中。
2. **主体名匹配集卫生**（`NS._entity_suffix_matches` / `NS._entity_name_run`）：内嵌后缀标签
   （`有限公司` ⊂ `股份有限公司`）、紧邻后缀标签（`证券` + `交易所`）、回扫上界截断造出的
   **不存在**主体名、以及通用前缀词（`根据` / `年度报告披露` / `发行人` / `母公司` / `下属` /
   `境内` / `经`），一律不再产出 token。
3. `nrules-11` **新增五组同类的硬事实封闭标记**（会计口径 / 法人身份角色 / 状态评价 /
   交叉引用 / 上市状态）：判据本身一字未改（「路径 B 命中即拒 / composed 句逐字在场」），
   改的是判据**所读的封闭集**。第 4 节用**换公司、换人名、换币种、换措辞**的通用反例证明
   这五组命中的是「角色」而不是某一家公司的字面句，并逐条证明三个使用点结论一致。
5. 第 5 节核查**第四个入口**：`NarrativeDraftUnit` 不在路径 B 扫描面内，因此必须证明单元
   文本根本走不到正文里去（否则「没扫到」就是真入口，而不是边界）。三道屏障逐条给出可执行
   证据：单元文本不进组织器输入面、单元在合并阶段只产 context 边、写入侧扫描面没有单元这一栏；
   再加**屏障 4**：就算它被写进组织段，五类硬事实（数字 / 主体 / 期间 / 否定 / 判断）也照旧
   被冻结门拒——衔接语不享有任何豁免（正向对照：纯衔接语接缝必须放行）。

夹具纪律：全部文本夹具是 **r7b 真实模型返回里的原文片段**（`logs/llm/…`，逐字抄自被拒批次
的 `claim_text` 与 `narrative_draft_units[*].text`），不读真实库、不建真实 Pack、不调 LLM、
不联网、不写任何文件。反例用**真类**或只读属性替身（`_NS`），与生产签名同形。

**本模块同样断言「没删词」**：生产的两份封闭词表必须逐条还在（对照本文件里独立抄写的一份）。
少了任何一个词，本模块失败——「把误伤修成不误伤」的正确做法不是把风险词删掉。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import schema as HS
from sections import claim_binding_gate as CBG
from sections import narrative_organizer as NO
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import schema as SS

REPO = Path(__file__).resolve().parent.parent

#: 屏障 4（第 5 节）用的 topic：与任何一家公司、任何一句真实原文无关的通用夹具。
RES_TOPIC = "topic-company-business"

#: 与生产**独立**抄写的一份风险标记词表（冻结口径，`nrules-9` 为止的全部条目）。
#: 本模块据此断言「修误伤 ≠ 删词」：生产那份少任何一条，这里就红。
FROZEN_MARKERS = (
    "不存在", "未披露", "未发生", "未取得", "未达到", "未包含", "未出现", "未被",
    "不符合", "不适用", "不属于", "不构成", "不确定", "不满足", "不再", "不予",
    "尚未", "并未", "并非", "没有", "无重大", "无任何", "以上均无",
    "勾选", "已选", "未选", "选中", "打勾", "适用", "不适用", "有效", "失效",
    "是/否",
    "合计", "小计", "总计", "占比", "其中", "同比", "环比", "较上年", "较上期",
    "增减", "差额", "平均值", "本表", "下表", "上表",
    "因此", "因而", "故此", "从而", "导致", "说明", "表明", "反映",
    "由此可见", "可以看出", "这意味着", "主要是因为", "究其原因", "综上",
    "上升", "下降", "改善", "恶化",
)
#: 与生产**独立**抄写的一份主体后缀表。
FROZEN_SUFFIXES = (
    "股份有限公司", "有限责任公司", "有限公司", "集团公司", "集团", "公司", "银行",
    "证券", "基金", "保险", "研究院", "研究所", "大学", "交易所", "事务所",
)

#: ---- r7b 真实返回原文（`logs/llm/20260926T035741980925__6ffe3da2…`，批 3/4）---------
R_NAME_AND_FORMER = (
    "宁德时代新能源科技股份有限公司前身为宁德时代新能源科技有限公司，"
    "于2011年12月16日在福建省宁德市注册成立")
R_FORMER_AND_CONVERT = (
    "宁德时代新能源科技有限公司以2015年10月31日为基准日，"
    "于2015年12月15日整体变更为股份有限公司")
R_SZSE = "2018年6月11日，公司A股股票在深圳证券交易所上市，股票代码300750"
R_HKEX = "经香港联合交易所有限公司批准，公司发行135,579千股H股股份于2025年5月20日在香港联交所主板挂牌并上市交易"
R_LITIGATION = "2025年度报告披露公司未发生重大诉讼/仲裁事项"
R_PBOC = ("根据中国人民银行企业信用报告的相关记录，发行人母公司及下属子公司"
          "没有被起诉信息、没有欠息信息、没有不良负债信息")
#: 只含 `募集说明书` 这一处**子串误伤**候选的原文片段：它自己的高风险表面在修正后应恰好为空。
R_PROSPECTUS_CLEAN = "截至募集说明书签署日"
#: 同一条候选的**完整**原文：它另外还带着一个**真**风险词 `未发生`，因此整句仍必须被判高风险面。
#: 两条夹具成对使用——这正是本批要区分的「子串误伤」与「真实风险词」。
R_PROSPECTUS = "截至募集说明书签署日，发行人未发生重大债务违约情况"
#: r7b 批 2/4 的会计期间候选（身份栏目那条被真数字命中、应当**继续**被拒）。
R_PERIOD = "公司会计期间采用公历年度，即每年自1月1日起至12月31日止"
R_FUNC_CURRENCY = "公司及境内子公司以人民币为记账本位币"
#: cp-14 公司节 s0001 的真实句形。**本批登记而不实施**：`公司是<名词短语>` 与既有夹具
#: `…前身为…`（`R_NAME_AND_FORMER`）里的 `为` 是同一类系词，但 `_ENTITY_RUN_STOP` 里**没有**
#: `是`，回扫因此越过系词、粘出 `公司是一家零碳新能源科技公司` 这条来源里不存在的「主体名」。
#: 修它要动主体名匹配集这条**判定集**，按 `nrules-10` 的规矩必须与三处版本前进一起落地，
#: 属于另一件有自身回归面的事——本批只把它钉成一条**已知边界**（见下面 §「已知边界」）。
R_COPULA_IS = "公司是一家零碳新能源科技公司，主要从事动力电池、储能电池的研发、生产、销售"


class _NS:
    """只读命名空间替身（被读的只有属性，不要求真 Draft / 真 Pack 对象）。"""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def _proposal(**kw):
    """路径 B 支撑边的**最小**真形：字段齐、无 fact 身份、无未来身份字段。"""
    base = dict(binding_subject_kind="claim_candidate", binding_subject_id="cand-1",
                draft_revision="rev-1", authority_kind="topic_pack",
                support_semantics="factual", support_role="primary",
                authorization_path="path_b_material_derived", material_id=None,
                payload_ref=None, locator_ref=None)
    base.update(kw)
    return _NS(**base)


def _binding_verdict(text: str) -> str | None:
    """把一段候选文本喂进**生产**绑定门（`cbg-2` 的 `_edge_reason`），取它返回的拒绝码。

    只走「路径 B 授权面」这一段：proposal 是合法的路径 B 边、不带 fact 身份、也没有可解析的
    材料，因此门在授权面之后无论返回什么都**不是** `path_b_high_risk_surface`。
    """
    revision = _NS(subject_kind="claim_candidate", subject_id="cand-1",
                   draft_revision="rev-1")
    try:
        return CBG._edge_reason(_proposal(), subject_revision=revision, fact_table={},
                                manifest=None, subject_text=text)
    except Exception:                                                # noqa: BLE001
        # 材料解析阶段的异常与「授权面判据」无关：本模块只关心那一个码。
        return "__raised__"


def main() -> dict:
    passed = 0
    failed = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            details.append(f"FAIL {msg}")

    # ---- 0. 先证「没删词」：两份封闭词表必须逐条还在 ------------------------------
    missing_markers = [m for m in FROZEN_MARKERS if m not in NS.HIGH_RISK_SURFACE_MARKERS]
    check(not missing_markers,
          f"风险标记词表不得删词；缺 {missing_markers}")
    missing_suffixes = [s for s in FROZEN_SUFFIXES if s not in NS.ENTITY_SUFFIXES]
    check(not missing_suffixes,
          f"主体后缀表不得删词；缺 {missing_suffixes}")
    check(set(NS.MARKER_SUBSTRING_EXEMPTIONS) <= set(NS.HIGH_RISK_SURFACE_MARKERS),
          "子串豁免只能挂在已登记的风险标记上（不得凭空新增一个标记）")

    # ---- 1. 标记词子串豁免：文书名 vs 结论连接词 ---------------------------------
    check("说明" not in NS.marker_hits("截至募集说明书签署日，发行人未发生重大债务违约情况"),
          "`募集说明书` 里的 `说明` 是文书名，不是结论连接词，不得算命中")
    check("说明" not in NS.marker_hits("招股说明书"),
          "`招股说明书` 同上")
    check("说明" not in NS.marker_hits("说明书"),
          "`说明书` 同上")
    check("说明" in NS.marker_hits("公司说明该事项不存在"),
          "独立的 `说明` 仍然是结论连接词，必须命中")
    check("说明" in NS.marker_hits("详见募集说明书；公司说明该事项已了结"),
          "同一段里只要**另有一处**独立的 `说明`，标记就得命中（豁免是逐次覆盖，不是整词作废）")
    check(NS.marker_hits("募集说明书") == (),
          "整段只由豁免形构成时，标记词层面应为空")

    # 真风险词不受影响：逐个反向对照（同一条豁免规则不得顺手放过它们）。
    RISK_STILL = {
        "公司未发生重大诉讼": "未发生",
        "公司不存在重大处罚": "不存在",
        "该事项不适用": "不适用",
        "前五大客户合计占比": "合计",
        "营业收入同比上升": "同比",
        "公司因此调整了产能": "因此",
        "毛利率较上年下降": "下降",
        "公司已选该方案": "已选",
        "该事项仍未达到披露标准": "未达到",
        "产品有效期至2025年": "有效",
    }
    for text, marker in RISK_STILL.items():
        check(marker in NS.marker_hits(text),
              f"{text!r} 里的 {marker!r} 必须继续命中（修误伤不等于放行真实风险词）")
    # 子串误伤对照：这些字面**不构成**风险标记。
    for text in ("集中", "表面", "表彰", "公司债券", "本公司", "市场份额"):
        check(NS.high_risk_surface_tokens(text) == (),
              f"{text!r} 不得命中任何风险标记（子串误伤）")

    # ---- 2. 主体名匹配集卫生 -----------------------------------------------------
    check(NS.entity_name_tokens("中国工商银行股份有限公司") == ("中国工商银行股份有限公司",),
          "长主体名必须**整体**成一个 token，不得被内嵌标签切碎")
    check(NS.entity_name_tokens(R_NAME_AND_FORMER)
          == ("宁德时代新能源科技股份有限公司", "宁德时代新能源科技有限公司"),
          f"两个真实主体名各出一个 token；实测 {NS.entity_name_tokens(R_NAME_AND_FORMER)}")
    check(all("前身" not in t for t in NS.entity_name_tokens(R_NAME_AND_FORMER)),
          "回扫不得越过 `为`（否则会造出 `…股份有限公司前身为…有限公司` 这种不存在的主体）")
    check(NS.entity_name_tokens(R_FORMER_AND_CONVERT) == ("宁德时代新能源科技有限公司",),
          f"`整体变更为股份有限公司` 里没有主体名；实测 {NS.entity_name_tokens(R_FORMER_AND_CONVERT)}")
    check(NS.entity_name_tokens(R_SZSE) == ("深圳证券交易所",),
          f"`证券` + `交易所` 是一个主体，不是两个；实测 {NS.entity_name_tokens(R_SZSE)}")
    check(NS.entity_name_tokens(R_HKEX) == () or all(
        t in R_HKEX for t in NS.entity_name_tokens(R_HKEX)),
          "任何产出的主体名必须是原文里的**逐字**片段")
    check(all("年度报告" not in t for t in NS.entity_name_tokens(R_LITIGATION)),
          f"`年度报告披露公司` 不是主体名；实测 {NS.entity_name_tokens(R_LITIGATION)}")
    check(NS.entity_name_tokens(R_PBOC) == ("中国人民银行",),
          f"`根据中国人民银行` 应剥掉 `根据`；实测 {NS.entity_name_tokens(R_PBOC)}")
    check(all("母公司" not in t and "子公司" not in t
              for t in NS.entity_name_tokens(R_PBOC)),
          "`发行人母公司` / `下属子公司` 是通用角色词，不是主体名")
    check(NS.entity_name_tokens(R_FUNC_CURRENCY) == (),
          f"`境内子公司` 同上；实测 {NS.entity_name_tokens(R_FUNC_CURRENCY)}")
    # 上界：run 超过旧值 10 的真实长名仍然完整（截断是**造假主体**，不只是漏检）。
    LONG = "中国长江三峡集团有限公司"
    check(NS.entity_name_tokens(LONG) == (LONG,),
          f"长名不得被回扫上界截断；实测 {NS.entity_name_tokens(LONG)}")
    check(NS._ENTITY_MAX_BACKSCAN > 10,
          "回扫上界必须大于旧值 10（旧值正是截断造假的成因）")
    # ---- 2b. 已知边界（本批**登记而不实施**）：`是` 这类系词还没有进停止集 --------------
    #: 这一格钉的是**现状**，不是期望值：`_ENTITY_RUN_STOP` 里只有 `为`，没有同类的 `是`。
    #: 于是 `公司是<名词短语>` 会把主语与系词粘进「主体名」。这正是 cp-14 公司节 s0001 报出的
    #: 那条表面的**形状**（来源里只有 `零碳新能源科技公司`）。
    #: 为什么不顺手修：这是主体名匹配集的**判定集**变化，按 `nrules-10` 的规矩必须与
    #: `NARRATIVE_RULES_VERSION` / `NARRATIVE_GATE_VERSION` / `SENTENCE_CHECK_POLICY_VERSION`
    #: 三处版本前进一起落地，并重核钉住这些版本的离线重放——那是另一件有自身回归面的事。
    #: 这条边界因此**不**让任何一格变红：它就是当前的实测读数。
    check("是" not in NS._ENTITY_RUN_STOP and "为" in NS._ENTITY_RUN_STOP,
          "已知边界如实登记：停止集里有 `为`、**没有** `是`（修它要动判定集，故不在本批）")
    check(any(t.startswith("公司是") for t in NS.entity_name_tokens(R_COPULA_IS)),
          "已知边界的**后果**也被如实钉住：`公司是<名词短语>` 会粘出一条来源里不存在的表面"
          f"（实测 {NS.entity_name_tokens(R_COPULA_IS)}）——这条读数就是待裁决事项本身，"
          "不是本批的期望值")

    # ---- 3. 三个使用点：同一份文本，同一结论 -------------------------------------
    # 3.1 写入侧（`pack_writer._path_b_high_risk_surfaces`，组装 Draft 之前）。
    def write_side(text: str) -> dict:
        bundle = PW._PreGateBundle(
            candidates=(_NS(candidate_id="cand-1", claim_text=text),),
            units=(), follow_up_specs=(),
            proposals=(_proposal(),))
        return PW._path_b_high_risk_surfaces(bundle=bundle)

    check(write_side(R_PROSPECTUS_CLEAN) == {},
          f"只含 `募集说明书` 这一处子串误伤的路径 B 候选在写入侧不得被判高风险面；"
          f"实测 {write_side(R_PROSPECTUS_CLEAN)}")
    check(write_side(R_PROSPECTUS) == {"cand-1": ("未发生",)},
          f"同一条候选的完整原文另带真风险词 `未发生`，整句仍必须判高风险面（且**只**报那一个"
          f"真表面）；实测 {write_side(R_PROSPECTUS)}")
    check(write_side("公司未发生重大诉讼") != {},
          "真风险词在写入侧必须判高风险面")
    check(write_side(R_LITIGATION) != {} and
          all(t in R_LITIGATION for t in write_side(R_LITIGATION)["cand-1"]),
          f"写入侧报出的表面必须是原文里的逐字片段；实测 {write_side(R_LITIGATION)}")

    # 3.2 门后机械门（`claim_binding_gate` 的路径 B 授权面，独立执行同一语义）。
    check(_binding_verdict("公司未发生重大诉讼") == "path_b_high_risk_surface",
          "真风险词在绑定门必须拒绝 `path_b_high_risk_surface`")
    check(_binding_verdict(R_PERIOD) == "path_b_high_risk_surface",
          "带真实期间数字的候选在绑定门必须拒绝（这条 r7b 真实候选不得被修松）")
    check(_binding_verdict(R_PROSPECTUS_CLEAN) != "path_b_high_risk_surface",
          "只含 `募集说明书` 这一处子串误伤的候选不得被绑定门拒于授权面")

    # 3.3 composed 句表面守恒（`unauthorized_surfaces`，「逐字在场」口径）。
    check(NS.unauthorized_surfaces("公司未发生重大诉讼。", ("公司未发生重大诉讼。",)) == (),
          "composed 守恒：表面逐字在场即被授权")
    check("未发生" in NS.unauthorized_surfaces("公司未发生重大诉讼。", ("公司已了结该诉讼。",)),
          "composed 守恒：表面不在授权池里即未授权")
    check(NS.unauthorized_surfaces(R_PROSPECTUS_CLEAN, (R_PROSPECTUS_CLEAN,)) == (),
          "`募集说明书` 不再是表面，片段逐字在场时不得报未授权")

    # ---- 4. `nrules-11` 五组新标记的**通用**反例 -----------------------------------
    # 这五组判的是**角色**（会计口径 / 法人身份角色 / 状态评价 / 交叉引用 / 上市状态），
    # 不是某一家公司的字面句。因此反例一律**换主体、换人名、换币种、换措辞**：如果换一家公司
    # 就不命中了，那说明判据其实匹配的是那家公司的字面；只有这样换着写，才能证明它匹配的是角色。
    GENERIC_NEW_GROUP_SAMPLES = (
        # 会计口径（期间与记账币种）
        ("会计期间", "乙公司会计期间自4月1日起至次年3月31日止"),
        ("会计年度", "丙公司会计年度与集团保持一致"),
        ("公历年度", "丁公司按公历年度编制财务报表"),
        ("记账本位币", "戊公司以欧元为记账本位币"),
        ("本位币", "己公司的本位币不是人民币"),
        # 法人身份角色（自然人身份 / 治理角色）
        ("法定代表人", "庚公司法定代表人为张三"),
        ("法人代表", "辛公司的法人代表已变更"),
        ("实际控制人", "壬公司实际控制人为李四"),
        ("控股股东", "癸公司控股股东为某投资集团"),
        ("董事长", "子公司董事长由董事会选举产生"),
        # 状态评价（无期间绑定的现状判断）
        ("正常", "公司的生产经营活动一切正常"),
        ("良好", "公司信用状况良好"),
        ("稳定", "公司客户结构稳定"),
        # 文档内 / 跨文档交叉引用
        ("详见", "具体内容详见本节下文"),
        ("参见", "有关情况参见附件"),
        # 证券上市与挂牌状态
        ("挂牌", "公司股票在全国中小企业股份转让系统挂牌"),
        ("上市", "公司股票在上海证券交易所上市"),
        ("上市交易", "公司股票已于2024年起上市交易"),
    )
    for marker, sample in GENERIC_NEW_GROUP_SAMPLES:
        check(marker in NS.HIGH_RISK_SURFACE_MARKERS,
              f"`{marker}` 必须登记在册（`nrules-11` 的五组新标记）")
        check(marker in NS.marker_hits(sample),
              f"通用反例必须命中：{sample!r} 里的 {marker!r}")
        # 三个使用点必须给出**同一结论**：文字层面命中还不够，写入侧与绑定门都得拒绝，
        # 否则「词表加了但某一处没接到」会表现为「风险词只在纸面上是风险词」。
        check(write_side(sample) != {},
              f"{sample!r} 在写入侧必须判高风险面（实测 {write_side(sample)}）")
        check(_binding_verdict(sample) == "path_b_high_risk_surface",
              f"{sample!r} 在绑定门必须拒绝 `path_b_high_risk_surface`"
              f"（实测 {_binding_verdict(sample)!r}）")

    # 派生事实的具体形态不是判据的依赖：换成人名、换成非人民币币种照旧命中（上面已覆盖），
    # 这里再钉住反向边界——`nrules-11` **故意没有**把这些通用名词当成标记，否则「加词表」
    # 就变成了对普通措辞的全面封禁（下一批若要把其中任何一个登记为标记，那是判定集变化，
    # 必须同时前进规则集与门版本，并在这里把该条移出反面清单）。
    for loose in ("人民币", "货币", "年度", "股东", "董事", "监事", "股票"):
        check(loose not in NS.HIGH_RISK_SURFACE_MARKERS,
              f"`{loose}` 是普通名词、不承担硬事实角色，不得登记为风险标记")
    check(NS.high_risk_surface_tokens("公司以人民币结算部分货款") == (),
          "该边界是可观察的：句子含 `人民币` 但没有硬事实角色，路径 B 扫描面仍为空")

    # ---- 5. 第四个入口：`NarrativeDraftUnit` 不得成为绕过 Claim 门的正文来源 --------
    # 路径 B 扫描只覆盖 `claim_candidates` / `bundle.proposals`，**不扫**草稿单元。因此必须
    # 证明单元文本走不到正文里去；三条各自独立的屏障缺一不可，下面逐条给出可执行证据。
    UNIT_HARD_FACT = "公司法定代表人为张三，会计期间采用公历年度"
    # 屏障 2：单元文本不进组织器的输入面（context 绑定行只有身份字段，**没有**文本字段）。
    organizer_payload = NO.build_organizer_messages(
        claims=(_NS(claim_id="sc-1", topic_id="topic_a", claim_type="factual",
                    text="公司采购以长期协议为主", citation_refs=()),),
        accepted_context_bindings=(
            _NS(accepted_support_binding_id="actx-1", material_id="mat-1",
                authority_container_id="pack-1", support_role="context"),),
        unresolved=(), writing_spec=object(), presentation_profile=object(),
        identity={"task_id": "t", "section_id": "s", "section_draft_id": "d",
                  "draft_revision": "rev-1"})[0]["content"]
    check(UNIT_HARD_FACT not in organizer_payload,
          "组织器的输入面不得含草稿单元文本（模型看不到它，就不可能把它写进正文）")
    for surface in ("法定代表人", "会计期间", "公历年度"):
        check(surface not in organizer_payload,
              f"组织器输入面不得出现草稿单元里的硬事实表面 {surface!r}")
    ctx_rows = json.loads(organizer_payload)["accepted_context_bindings"]
    check(ctx_rows and all("text" not in row for row in ctx_rows),
          "context 绑定行只有身份字段：没有文本字段可供硬事实经 context 进入正文")
    # 屏障 3：合并阶段的草稿单元只写 `context_support`，不产候选文本、不构造 ClaimCandidate。
    src = (REPO / "sections/pack_writer.py").read_text(encoding="utf-8")
    units_start = src.index('for spec in plan["narrative_draft_units"]:')
    next_loop = src.find("for spec in plan[", units_start + 1)
    units_block = src[units_start:next_loop if next_loop != -1 else units_start + 2000]
    check("context_support" in units_block
          and "claim_text" not in units_block and "ClaimCandidate" not in units_block,
          "草稿单元在合并阶段只产生 context 边：既不携带候选文本，也不构造 ClaimCandidate")
    # 屏障 1 的可观察面：写入侧扫描面里**没有**单元这一栏（单元不是事实来源）。
    # 这条断言把「没扫到」钉成一个**决定**而不是一个疏漏：它成立的前提是上面两条屏障。
    unit_bundle = PW._PreGateBundle(
        candidates=(), units=(_NS(draft_unit_id="u-1", text=UNIT_HARD_FACT),),
        follow_up_specs=(), proposals=())
    check(PW._path_b_high_risk_surfaces(bundle=unit_bundle) == {},
          "写入侧高风险扫描的输入面只有候选：草稿单元不在其中（单元从不进正文）")
    # 屏障 4：就算门前文本**真的**被写进了组织段（衔接语那一段），五类硬事实也照旧被冻结门拒。
    # 判据读的是「整句文本 vs 它**自己声明**的 Claim 文本」的差集：衔接语不享有任何豁免，
    # 也不存在「这是组织语，所以里面的数字/身份/期间/否定/判断可以例外」的口子。
    # 五类各一条**通用**反例（换主体、换币种、换措辞，不绑任何一家公司或任何一句原文），
    # 外加一条正向对照：纯衔接语接缝必须放行（判据不得修成「组织段一律拒」）。
    RES_A = "公司主营业务由动力电池与储能两大条线构成"
    RES_B = "公司销售以直销为主"

    def _res_claim(text: str, index: int):
        """真 `SectionClaim`：屏障 4 要喂进**生产**冻结门，形状替身不够（门会读引用）。"""
        question_ids = (f"rq-{index}",)
        refs = (HS.CitationRef(ref_type="evidence", evidence_id=f"evidence:res-{index}"),)
        claim_id = SS.derive_claim_id("fact", RES_TOPIC, question_ids, text, refs,
                                      f"rescand-{index}", "rev-1", (f"asb-res-{index}",))
        return SS.SectionClaim(
            claim_id=claim_id, schema_version=SS.CLAIM_SCHEMA_VERSION, section_id="company",
            topic_id=RES_TOPIC, question_ids=question_ids, text=text, claim_type="fact",
            citation_refs=refs, claim_candidate_id=f"rescand-{index}",
            claim_candidate_revision="rev-1", accepted_binding_ids=(f"asb-res-{index}",))

    res_claims = (_res_claim(RES_A, 1), _res_claim(RES_B, 2))
    res_texts = (RES_A, RES_B)

    def _res_gate(text: str) -> str:
        """把一段组织段文本喂进**生产**冻结门（只有这一句的 Narrative），返回错误文本。"""
        specs = [{"text": text, "sentence_kind": "composed",
                  "claim_ids": [c.claim_id for c in res_claims],
                  "citation_ids": [SS.derive_citation_id(c.claim_id, ref)
                                   for c in res_claims for ref in c.citation_refs]}]
        paragraph = NS.NarrativeParagraph.create(
            section_id="company", topic_ids=(RES_TOPIC,), index=0, sentence_specs=specs)
        narrative = NS.SectionNarrative.create(
            task_id="task-1", section_id="company", section_draft_id="draft-1",
            draft_revision="rev-1", paragraphs=(paragraph,), tables=())
        try:
            NS.verify_section_narrative(
                narrative=narrative, claims=res_claims,
                accepted_context_binding_ids=(), dispositions=())
        except NS.NarrativeSchemaError as exc:
            return str(exc)
        return ""

    RESIDUE_HARD_FACTS = {
        "数字": "，本期销量增长42%，",
        "法人主体": "，宁德新能源科技有限公司，",
        "期间": "，报告期内，",
        "显式否定": "，未披露其他渠道，",
        "判断": "，经营情况正常，",
        # 同一判据的**通用**反例：换一家合成公司、换成自然人、换成外币、换成另一家交易所、
        # 换成另一种措辞。判据读的是「整句文本 vs 声明 Claim 文本」的差集，因此它匹配的是
        # **角色**而不是某家公司的字面句——只绑那家公司的反例证明不了这一点，这里必须换着写。
        "通用·法人主体（合成名）": "，远方重工集团股份有限公司，",
        "通用·自然人身份角色": "，公司法定代表人为张三，",
        "通用·会计口径": "，公司以欧元为记账本位币，",
        "通用·上市状态": "，公司股票在上海证券交易所上市，",
        "通用·文档交叉引用": "，详细情况详见年度报告，",
        "通用·因果推断": "，因此公司盈利能力改善，",
        "通用·趋势结论": "，公司毛利率较上年上升，",
        "通用·合成数字": "，本期产能利用率提升7个百分点，",
    }
    for label, residue in RESIDUE_HARD_FACTS.items():
        text = RES_A + residue + RES_B + "。"
        hits = NS.unauthorized_surfaces_within_claims(text, list(res_texts))
        check(bool(hits),
              f"衔接段里自造的{label}必须被边界感知核验抓住（实测 {list(hits)}）")
        check(bool(_res_gate(text)),
              f"衔接段里自造的{label}必须被冻结门拒（不得因为「那是组织语」放行）")
    check(_res_gate(RES_A + "，同时，" + RES_B + "。") == "",
          "正向对照：纯衔接语的接缝必须放行（判据不得修成「组织段一律拒」）")

    # ---- 6. 接线对账：两个消费方各自不得私藏一份词表 -----------------------------
    # `sections/narrative_schema.py` 是词表的**唯一**持有者，因此不在此列。
    for name, path in (("pack_writer", "sections/pack_writer.py"),
                       ("claim_binding_gate", "sections/claim_binding_gate.py")):
        src = (REPO / path).read_text(encoding="utf-8")
        check('"未发生"' not in src and "'未发生'" not in src,
              f"{name} 不得私藏一份风险词表（唯一实现在 `NS.HIGH_RISK_SURFACE_MARKERS`）")
    for path in ("sections/pack_writer.py", "sections/claim_binding_gate.py"):
        src = (REPO / path).read_text(encoding="utf-8")
        check("NS.high_risk_surface_tokens(" in src,
              f"{path} 必须经共享实现取高风险面")
    # 本模块自身的动因（`nrules-11` 五组新标记）把规则集推到 nrules-11；之后的判据 e
    # （`nrules-12`）、指令三的判据 10/11（`nrules-13`）、返修 ④ 的第 12 条（`nrules-14`）
    # 与指令 E 第 3 项的保真判据（`nrules-15`）又各推一次。断言的是「规则集的字面量必须等于
    # 当前值」这个纪律本身，不是本模块那一次的增量——本模块钉住的是**标记集未被动过**
    # （`FROZEN_MARKERS` 全在册、上面各节的通用反例逐条成立），版本号由持有者统一登记。
    # **`nrules-15` 不动本模块钉住的那几张表**：保真判据用的是**另加的**三张封闭补语表
    # （`FINAL_SENTENCE_FIDELITY_VERSION` / `SENTENCE_FIDELITY_DEFECTS`），
    # `HIGH_RISK_SURFACE_MARKERS` 与 `ENTITY_SUFFIXES` 一字未改。
    # **`nrules-16` 同样不动它们**：定点业务闭环批 §二 2 扩的是第 12 条所用的
    # `CURRENT_STATE_FRAMING_MARKERS`（普遍化组与否定式普遍化组），
    # `HIGH_RISK_SURFACE_MARKERS` / `ENTITY_SUFFIXES` 与上面各节逐条反例仍然原样成立——
    # 这正是本模块要钉的「标记集未被动过」。
    # **`nrules-17`／`nrules-18` 同样不动它们**：主营业务质量返修批 §四改的是**主体名抽取**
    # （`_entity_name_run` 剥前缀改为不动点 + `_ENTITY_TEMPORAL_RUNS` 不产出整串即时间状语的
    # 「字号」+ 「时间状语 + 自称」那一格），`HIGH_RISK_SURFACE_MARKERS` / `ENTITY_SUFFIXES`
    # 与上面各节逐条反例仍然原样成立——这正是本模块要钉的「标记集未被动过」。
    check(NS.NARRATIVE_RULES_VERSION == "nrules-18",
          f"判定集变了就必须升版；实测 {NS.NARRATIVE_RULES_VERSION}")

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

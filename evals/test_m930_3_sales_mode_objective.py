"""Eval: 主营业务写作组织面 `co-4`/`co-5`/`co-6` 与「已送达未写出」的读时派生读数（`munr-1`）。

用法: python -m evals.test_m930_3_sales_mode_objective

本模块钉的是本批**非冻结**改动：

1. **逐栏内容目标**（`co-3` 立、`co-4` 纠偏、`co-5` 再登记两栏、`co-6` 分列金额与占比；
   `cp-22`/`cp-23`/`cp-24`/`cp-25`）。
   `sales_mode` 在真实公司节里有正文（`s0015`）、有出处（`m13`）、有登记核对，`draft.gaps` 里
   却连一条它的缺口都没有——「答得薄」与「答得全」在账上长得一样。根因是 `co-2` 的
   `operating_model` 步 `guidance` **全是禁则**，从没说过这一栏要答什么。`co-3` 给每栏补一对
   同级的文本：**这一栏要答哪几点**与**这一栏的候选键怎么用**；`co-4` 再纠 `co-3` 那两句
   **过度推断**——「拥有**独立的**销售体系」**不**等于自建／直销（本批三份材料 `m13`/`m14`/`m44`
   都不给渠道），「客户需求牵引排产」属**生产模式**、`co-3` 把它写成要答的点时与自己的禁则正面
   冲突。`co-5`（`cp-24`）再登记 `production_mode` 与 `revenue_breakdown`：前者在 `cp-23`
   真实公司节里引了**只登记到主营业务栏**的材料去写本栏（三条 `sentence_aspect_not_registered`），
   后者带着 **49 个候选键**却**一段正文都没有**（三份载有分业务收入的材料全部 `delivered_not_used`）。
   `co-6`（`cp-25`，`ndc-4`）把该栏的**金额**与**占比**的授权面**分列**：占比事实分母不可核时
   一律不写（`scp-13` 的 `denominator_unverified`），而请求面在此之前只有一根事实键轴，
   于是「按请求面写 ⇒ 必然硬错」。四版都**只**组织写作，不判资格，也不改第 7 条
   （缺口仍由模型产生）。

2. **「已送达未写出」的读时派生读数**（`munr-1`，只在回读里现算）。正文里既然没有那份材料的
   句子，「为什么没写出去」就只能拿**可见的东西**比：它的正文是不是与**已被引用**的某一份
   **逐字重复**、它有多少句是**任何已被引用材料里都没有的**。三条都是**观测**，都**不落盘**
   ——`MaterialAdoptionRecord` 是严格 wire 类型且 `record_id` 对其 `identity_body()` 取哈希，
   给它加字段就是换掉全部历史 `cadopt_*` id。**这一列绝不冒充 Writer 侧的理由码。**

模块的**边界**：夹具是替身，本模块只钉**文本与派生读数的语义**，不证明材料真的取到了、
不证明正文达到人读内容门，也不宣称 M930-3 / TS5 / 任何正式阶段关闭。不调 LLM、不联网、不写库。
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import run_m930_3_cited_chain as CHAIN                    # noqa: E402
from sections import cited_writer as CW                                # noqa: E402


#: 步骤身份与次序的**逐字快照**（`co-1` 立；`co-2`/`co-3`/`co-4` 均未动）。后三版只动**文本**，
#: 不动分步——所以这张表一个成员、一个次序都不许变。
_STEP_IDS = (
    "products_and_technology",
    "application_scenarios",
    "industry_chain_position",
    "operating_model",
    "cost_and_margin",
    "revenue_breakdown",
    "main_business_and_performance",
    "period_and_caliber",
    "customer_concentration",
    "supplier_concentration",
)

#: 各步承接的**栏位身份后缀**（同样逐字快照）。`sales_mode` 归 `operating_model` 步。
_STEP_SUFFIXES = {
    "operating_model": ("procurement_mode", "production_mode", "sales_mode"),
}

#: `CitedSubsectionSpec.identity_body()` 的键集（`cwm-6` 起）。它参与内容身份 ⇒ 一个键都不许加。
_SPEC_IDENTITY_KEYS = ("subsection_id", "title", "requirement_text",
                       "declared_aspect_ids", "allowed_source_classes")

#: `MaterialAdoptionRecord.from_dict` 的**允许键集**（严格 wire；`NS._reject_unknown`）。
#: 与数据类字段表**不是**同一张（wire 上多一个派生属性 `disposition`、少一个自算的 `record_id`），
#: 两张都钉，因为它们各自是「加字段 = 作废历史」的那条边。
_ADOPTION_WIRE_KEYS = frozenset({"record_id", "disposition", "member_ref", "pack_id",
                                 "material_id", "citation_key", "sentence_ids"})
_ADOPTION_FIELD_NAMES = ("record_id", "member_ref", "pack_id", "material_id",
                         "citation_key", "sentence_ids")


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

    def note(msg: str) -> None:
        details.append(f"NOTE {msg}")

    # ================================================= §1 逐栏内容目标（`co-3`）
    details.append("## §1 每一栏要答什么——`co-3` 把「答得薄」变成看得见的几点（`co-4` 纠两条过度推断）")
    obj, opp = CW.column_content_objective("company_business_model.sales_mode")
    check(bool(obj) and bool(opp),
          "**正例**：`sales_mode` 栏返回非空的 `(内容目标, 引用机会)`")
    check(all(tok in obj for tok in ("①", "②", "③")),
          "⇒ 内容目标**逐点**列出该栏要答的三点（产品口径／渠道形态／客户类型）")
    check("第 7 条" in obj and "第 9 条" in obj,
          "⇒ 目标写明：写不出的点按第 7 条**记缺口**；具名名录等边界按第 9 条**不写**"
          "（缺口仍由模型产生，本表不代它判）")
    check("历史披露" in opp and "第 5、10 条" in opp,
          "⇒ 引用机会写明**当期来源与历史来源分开读**：历史来源只能带年份披露，"
          "不得承诺当前状况——这正是 `m44` 那一句的纪律")

    #: **`co-4` 的两条纠偏**——`co-3` 的目标文本把两件**不由本栏材料支持**的东西写成了要答的点。
    details.append("### §1b `co-4` 纠两条过度推断：`co-3` 的目标文本自己跟自己打架")
    #: 反例 1：`co-3` 写「销售体系是**自建的还是靠经销／代理**」，而本批三份材料（`m13`/`m14`/`m44`）
    #: 只有「公司拥有**独立的**研发、采购、生产和销售体系」——「独立」讲的是自成体系、不整体外包。
    #: 照 `co-3` 读，模型会把「独立体系」直接写成渠道形态。
    check("独立的" in obj and "自建" in obj and "直销" in obj
          and ("不等于自建" in obj or "**不**等于自建"),
          "**反例 N1**：目标文本把「拥有**独立的**销售体系」与「自建／直销」**显式切开**——"
          "「独立」≠自建、更≠直销，不得据此推定渠道形态")
    #: 反例 2：`co-3` 写「销售与生产的衔接（**订单／客户需求怎么牵引排产**）」，而它出自
    #: 「综合考虑市场情况及客户需求**安排生产**」——**属生产模式**，且与同一段文本自己的
    #: 「不写」清单（「产能扩张与排产安排…那属生产模式」）正面冲突。
    _ban = obj.split("**不写**", 1)[-1]
    _asks = obj.split("**不写**", 1)[0]
    check("排产" in _ban and "生产模式" in _ban and "排产" not in _asks,
          "**反例 N2**：`客户需求牵引排产` **只在「不写」里**出现、且标明属**生产模式**——"
          "它**不再**是要答的点（`co-3` 里它与自己的禁则正面冲突）")
    #: 反例 3：结算方式同样只由文本「独立体系」推不出来 ⇒ 必须落在「不写／记缺口」一侧。
    check("结算" in _ban,
          "**反例 N3**：结算方式与账期落在「不写」一侧（本批三份材料给不出），"
          "不得由「独立体系」推定")
    #: **反例**：未登记的后缀返回空串。空串是**结论**（本栏没有额外目标），不是键缺席——
    #: 「本栏没有额外目标」与「读不到」在产物上不能长得一样。
    check(CW.column_content_objective("company_business_model.tech_route") == ("", "")
          and CW.column_content_objective("no_such_column") == ("", ""),
          "**反例**：未登记的后缀返回 `(\"\", \"\")`——不报错、不猜、不留缺口")
    #: **同一条派生轴**：目标只讲**栏位本身**该回答什么，不是哪一家公司。
    check(CW.column_content_objective("company_business_model.sales_mode")
          == CW.column_content_objective("any_other_topic.sales_mode"),
          "⇒ 只认**栏位身份后缀**（换主题前缀不换目标）")
    check(not re.search(r"\bm\d+\b", obj + opp) and "页" not in obj + opp,
          "⇒ 目标／引用机会文本里**没有引用键（`m01` 形状）、没有页码**——"
          "它是写作组织，不是第二份材料清单")
    check(set(CW.COLUMN_CONTENT_OBJECTIVES) == {"sales_mode", "revenue_breakdown",
                                                "production_mode"},
          f"⇒ `co-5` 起登记三栏（实测 {sorted(CW.COLUMN_CONTENT_OBJECTIVES)}）；"
          "其余栏位沿用该步 `guidance`，不在这里新造目标")
    #: **`co-5`（`cp-24`）新增的两栏**：文本须逐点、须写明写不出的点按第 7 条记缺口，
    #: 并且**不得**出现引用键（`m01` 形状）或页码——与 `sales_mode` 同一条纪律。
    for _suffix, _sig in (("production_mode", ("①", "②", "③", "第 7 条", "第 3d 条")),
                          ("revenue_breakdown", ("①", "②", "③", "第 7 条", "第 4 条"))):
        _o, _p = CW.column_content_objective(f"company_business_model.{_suffix}")
        check(bool(_o) and bool(_p) and all(t in _o for t in _sig),
              f"⇒ `co-5`：`{_suffix}` 栏带逐点的内容目标与引用机会，"
              "并写明写不出的点按第 7 条记缺口")
        check(not re.search(r"\bm\d+\b", _o + _p) and "页" not in _o + _p,
              f"⇒ `co-5`：`{_suffix}` 的目标／引用机会里没有引用键、没有页码")
    #: **反例（`cp-24` 新增的边界）**：`revenue_breakdown` 的目标必须**逐项**留缺口而不是整栏
    #: 留白，且必须把「金额与占比只有合格事实才写」与「不带数字的定性内容可以写」**分开**写清楚
    #: ——这两条正是 `cp-23` 真实公司节里本栏零段落的两个原因。
    _rb, _rb_opp = CW.column_content_objective("company_business_main.revenue_breakdown")
    check("逐项" in _rb and "合格金额事实" in _rb,
          "⇒ `co-5`：`revenue_breakdown` 的金额与占比**逐项**留缺口，只有合格事实才写")
    check("不带数字" in _rb,
          "⇒ `co-5`：`revenue_breakdown` **不是**整栏留白——不带数字的定性内容照常可写")
    #: **`co-6`（`cp-25`，`ndc-4`）**：本栏目标必须**分列金额与占比**的授权面。动因是请求面与
    #: 逐句核对正面冲突——12 条分业务事实全在 `citable_fact_keys` 里，`cp-24` 的目标于是要求
    #: 「有合格事实就写占比」，而其中 3 条占比事实被 `scp-13` 判 `denominator_unverified`：
    #: 模型**照请求面写就必然硬错**。不是放松判据（`scp-13` 一字未动），是让请求面说实话。
    check("writable_fact_keys" in _rb and "denominator_unverified" in _rb,
          "**反例（`co-6`）**：`revenue_breakdown` 的金额项指向**本版可写**的 "
          "`writable_fact_keys`，占比项写明分母不可核时的处置——"
          "只写「有合格事实就写」（`co-5` 的口径）正是请求面与逐句核对打架的那一处")
    check("只读 PDF 展示区" in _rb and "普通材料" in _rb,
          "**反例（`co-6`）**：占比不得从**普通材料**或**只读 PDF 展示区**抄一个百分比顶上"
          "——那两处都不是本版的数字权威")
    check("不得" in _rb and "金额" in _rb.split("占比写不了", 1)[-1],
          "**反例（`co-6`）**：目标明确「占比写不了**不等于**这一栏可以留白」——"
          "不得因为占比不可写就把金额与业务叙述一起撤掉（不新增任何使整节清零的门）")
    check("writable_fact_keys" in _rb_opp and "citable_fact_keys" in _rb_opp,
          "**反例（`co-6`）**：引用机会把两根键轴**分开**说明——"
          "`citable_material_keys` 是材料候选、`writable_fact_keys` 是本版可写的金额事实键、"
          "`citable_fact_keys` 是 Pack 侧登记；三者不是一回事")

    # ================================================= §2 不动分步 / 身份体 / wire
    details.append("## §2 `co-4`/`co-5` 只改文本——分步、身份体与 wire 一个字节都不动")
    check(tuple(st[0] for st in CW.CONTENT_OUTLINE_STEPS) == _STEP_IDS,
          "**反例 N1**：`CONTENT_OUTLINE_STEPS` 的成员与次序与 `co-2`/`co-3` **逐字相同**"
          "（只有版本串从 `co-3` 变 `co-4`）")
    for step_id, suffixes in _STEP_SUFFIXES.items():
        step = next(st for st in CW.CONTENT_OUTLINE_STEPS if st[0] == step_id)
        check(step[2] == suffixes,
              f"**反例 N1**：`{step_id}` 步承接的栏位后缀仍为 {suffixes}——"
              "改一步的成员或次序就是改这一节的写作组织，不是本批该做的事")
    check(CW.CONTENT_OUTLINE_VERSION == "co-6"
          and CW.CONTENT_OUTLINE_PROFILE == "company_business",
          "⇒ 提纲版本升到 `co-6`（`co-3` 立内容目标、`co-4` 纠两条过度推断、"
          "`co-5` 再登记 `production_mode`/`revenue_breakdown`、"
          "`co-6` 把金额与占比的授权面分列）；"
          "档案名仍是公司无关的 `company_business`")
    spec = CW.CitedSubsectionSpec(subsection_id="co-h4", title="t", requirement_text="r",
                                  declared_aspect_ids=("company_business_model.sales_mode",))
    check(tuple(spec.identity_body()) == _SPEC_IDENTITY_KEYS,
          "**反例 N2**：`CitedSubsectionSpec.identity_body()` 的键集未变——"
          "加一个键就重哈希全部小节身份，当场作废全部历史清单")
    check(CW.CITED_WRITER_DRAFT_SCHEMA_VERSION == "cw-4",
          "**反例 N3**：`CITED_WRITER_DRAFT_SCHEMA_VERSION` 仍为 `cw-4`")
    check(tuple(f.name for f in dataclasses.fields(CW.MaterialAdoptionRecord))
          == _ADOPTION_FIELD_NAMES,
          "**反例 N3**：`MaterialAdoptionRecord` 的字段集未变——"
          "`record_id` 对 `identity_body()` 取哈希，加字段 = 换掉全部历史 `cadopt_*` id")
    #: 行为级钉：`from_dict` 对**恰好**那组键**接受**，对**多一个**键**拒绝**并且**不发散地**
    #: 失败（同一个 `_reject_unknown` 的报错契约）。这样「加一个字段」在本模块里当场变红。
    #: `record_id` 与内容绑定（改一个字段就换 id）⇒ 夹具必须用 `create()` 算出来的那一份，
    #: 不能手写一个 `cadopt_*`——手写的那份会被 `from_dict` 判成「声明与内容不符」。
    _record = CW.MaterialAdoptionRecord.create(
        member_ref="mem-1", pack_id="p1", material_id="m1", citation_key="m01",
        sentence_ids=())
    _wire = _record.to_dict()
    try:
        _accepts = (CW.MaterialAdoptionRecord.from_dict(dict(_wire))
                    == _record)
    except Exception:                                       # noqa: BLE001
        _accepts = False
    _rejects = False
    try:
        CW.MaterialAdoptionRecord.from_dict({**_wire, "extra_field": 1})
    except Exception:                                       # noqa: BLE001
        _rejects = True
    check(_ADOPTION_WIRE_KEYS == frozenset(_wire) and _accepts and _rejects,
          "**反例 N3**：`from_dict` 对**恰好**这组键接受、对**多一个**键拒绝——"
          "wire 键集在行为上也未变")

    # ================================================= §3 `munr-1` 读时派生读数
    details.append("## §3 「已送达未写出」的理由只**现算**——三条观测并排，不落盘")
    #: 替身正文（中性文本，与任何真实公司／材料无关）。`m01`/`m04` 会被引用，`m02`/`m03` 不会。
    text_by_key = {
        "m01": "公司主要通过直销方式向整车厂销售动力电池产品。公司与多家储能集成商建立了"
               "合作关系。",
        "m02": "公司主要通过直销方式向整车厂销售动力电池产品。公司与多家储能集成商建立了"
               "合作关系。",
        "m03": "公司主要通过直销方式向整车厂销售动力电池产品。公司披露了结算方式的安排。",
        "m04": "公司的生产基地分布在多个省份。",
    }
    rows = [
        {"citation_key": "m01", "state": "used"},
        {"citation_key": "m02", "state": "delivered_not_used"},
        {"citation_key": "m03", "state": "delivered_not_used"},
        {"citation_key": "m04", "state": "used"},
        {"citation_key": "m05", "state": "admitted_not_delivered"},
    ]
    CHAIN._annotate_delivered_not_used(rows, text_by_key=text_by_key)
    by_key = {r["citation_key"]: r for r in rows}
    check(all(k in r for r in rows for k in CHAIN.MATERIAL_UNUSED_DERIVED_KEYS),
          "⇒ 三列**逐行**都在（键集是等号：不适用时补空值，不是键缺席）")
    check(by_key["m02"]["duplicate_of"] == "m01" and by_key["m02"]["derived_note"] != "",
          "**正例**：与已被引用材料**逐字相同**的那一份，`duplicate_of` 写出对方键并附一句说明")
    check(by_key["m02"]["sole_carrier_sentences"] == 0,
          "⇒ 逐字重复的那一份，独有句数为 0（它一句都没带来已引用材料里没有的字）")
    check(by_key["m03"]["duplicate_of"] == "" and by_key["m03"]["sole_carrier_sentences"] == 1,
          "**正例**：只**有一句**是独有句的那一份——`duplicate_of` 为空（整份不重复），"
          "而独有句数**按句**算出 1（第一句逐字落在 `m01` 里，第二句不在）")
    check(by_key["m01"]["duplicate_of"] == "" and by_key["m01"]["sole_carrier_sentences"] == 0
          and by_key["m04"]["sole_carrier_sentences"] == 0,
          "**反例**：已被引用的行与不适用的行一律补空值——"
          "「这一列对本份不适用」与「算了但为零」在产物上不能长得一样")
    #: **只认逐字**：同义换字不是重复。拿它当重复就会把「另一份独立披露」印成「冗余」。
    paraphrase = {"m01": "公司主要通过直销方式向整车厂销售动力电池产品。",
                  "m09": "公司销售动力电池产品，主要采用直销模式，客户为整车厂。"}
    p_rows = [{"citation_key": "m01", "state": "used"},
              {"citation_key": "m09", "state": "delivered_not_used"}]
    CHAIN._annotate_delivered_not_used(p_rows, text_by_key=paraphrase)
    check(p_rows[1]["duplicate_of"] == "" and p_rows[1]["sole_carrier_sentences"] == 1,
          "**反例**：同义换字的材料**不**被判成重复（只认逐字/归一空白后的逐字）——"
          "判重放宽到语义，就会把另一份独立披露印成冗余")
    #: 归一空白只为**判重**服务；它不改写任何产物文本。
    spaced = {"m01": "公司主要通过直销方式向整车厂销售动力电池产品。",
              "m07": "公司主要通过直销方式向整车厂销售动力电池产品。"}
    s_rows = [{"citation_key": "m01", "state": "used"},
              {"citation_key": "m07", "state": "delivered_not_used"}]
    CHAIN._annotate_delivered_not_used(s_rows, text_by_key=spaced)
    check(s_rows[1]["duplicate_of"] == "m01",
          "⇒ 归一空白后逐字相同即判重（`_compact_for_duplicate` 只服务于判重，不改写产物）")
    check(CHAIN._compact_for_duplicate(" a　b\nc ") == "abc",
          "⇒ 归一函数去掉**全部**空白（含全角空格与换行），只留实词字符")

    # ================================================= §4 本模块的边界
    details.append("## §4 本模块证明的与不证明的")
    note("`co-3`/`co-4` 只把「这一栏要答哪几点」摊到请求面上；它**不**保证模型答到，也**不**改第 7 条。"
         "缺口的有无仍由真实 run 的 `draft.gaps` 给出；`co-4` 的纠偏**是否被真实模型采纳**，"
         "本模块**不**证明（离线替身不读提示词，真实调用本批为零）⇒ 只能写「未验证」。")
    note("`munr-1` 三列是**读时派生**，不是 Writer 侧登记：`WriterMaterialProcessingDisposition`"
         "这一轴在本链上仍不存在（`CitedProseDraft` 无 `material_dispositions`）。"
         "三列只并排**观测**，不解释动机，也不得被读成「Contract 必需事实未取得」。")
    check(True, "边界已声明（替身夹具 + 文本/派生读数语义，不冒充成稿质量验收）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

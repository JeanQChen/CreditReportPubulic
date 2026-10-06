"""Eval: M930-3 —— 分业务营收纵链（精确材料 → 单值候选 → 资格 → 事实 → 句子配对）。

用法: python -m evals.test_m930_3_numeric_disclosure

**这条纵链此前断在「候选生成之前」。** 研究侧唯一的候选构造器 `topic_runtime._adopt_facts`
的 `statement` 恒取模型命题文本（`claim.text`）；材料明明已把分业务营收原文送进 Pack，候选数
仍是 0。本模块钉住补上的那个确定性入口（`harness/numeric_disclosure.py`，`nd-2`）与它两端的
边界：

1. **并列三年数字必须拆成单值命题**：`2023-2025 年 … 分别为 A、B、C` 里第 i 个值绑第 i 个
   年份（**位置配对**），期间**不**来自 `period_extraction`（那个对同一句只会答 `2025`，是错的
   **值级**期间）。年份个数与值个数对不上、没有枚举开标记、单位不一致、占比没有分母、指标窗里
   出现增速动词——一律 **typed 跳过**，一个都不猜；跳过**不**铸候选，因此**不**产生资格决定
   （也不冒充 gap）。
2. **冲突值两处都铸候选、都判拒**（`conflicting_value_for_same_identity`）；同键同值只留一条
   （`duplicate_same_value`）。**先出现的那条不得被丢掉**。
3. **资格与身份只有一条路**：数值披露候选与模型 claim 走**同一**个 `_qualify_and_adopt`（三个
   digest 由决定自己声明的输入集确定性复算，`pack_set._check_decision_inputs` 会核）。重构后模型
   那条路的 `fact_id` 与三个 digest **逐字未变**（身份保持）。
4. **句子—事实配对（`scp-12`）**：`numeric_qualification` 那一支的授权从**逐字**改成语义配对——
   同数字不同年、同数字不同业务、把收入金额当收入占比必须**判错**；正例照旧通过；任一侧给不出
   **完整**绑定则**回落**旧判据（只收紧，不新造硬错）。
4b. **逐值事实身份（`nd-2`）**：一条事实的 `value_identity` 携带**目标值自身**与四轴身份
   （期间 / 业务作用域 / 指标 / 单位），并在 `SupportedFact` 构造期与候选期间逐字对齐；值级期间
   对不上（文本里有 2023/2024 前值）时**整条**不铸候选，而不是只取文本里最后一个数字。同一材料
   内的去重／冲突键必须带业务作用域：异业务同年不同值**不**互判冲突，作用域读不出来时**保守
   跳过**并留 typed 理由。
5. **进 Writer `f*` 行**：这样一条事实过关后必须出现在 `scan_topic_pack` 与写作输入清单的事实
   行里（内容逐字），且期间是它**自己**那一年。

公司无关：本模块没有公司代号、文件名、页码、表号或答案数字的**生产**字面量（断言里的年份与金额
都是**输入文本**的一部分）；§6 的实盘对照**只读**既有离线 run 的清单，不存在即跳过。不调 LLM、
不联网、不写库、不建第二套 Pack/Writer。
"""

from __future__ import annotations

import dataclasses as dc
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import test_m930_3_cited_writer as E               # noqa: E402
from evals import test_m930_3_period_chain as PC              # noqa: E402
from evals.test_m930_3_value_trace import _fact_like, _manifest_with  # noqa: E402
from harness import numeric_disclosure as ND                  # noqa: E402
from harness import topic_runtime as TR                       # noqa: E402
from harness import topic_schema as TS                        # noqa: E402
from sections import cited_writer as CW                       # noqa: E402
from sections import material_context as MC                   # noqa: E402
from sections import narrative_schema as NS                   # noqa: E402
from sections import pack_set as PSet                         # noqa: E402
from sections import pack_writer as PW                        # noqa: E402
from sections import sentence_check as SC                     # noqa: E402

#: 覆盖面**声明**的那个栏目（冻结 Contract 的 aspect_id；不是任何主体专用）。
ASPECT = "company_business_main.revenue_breakdown"
REQUIRED = ("各业务收入", "收入占比")

#: 合成正文：占位业务名（`甲类业务`/`乙类业务`，4 字，过 `_SCOPE_MIN_CHARS`），不含任何主体。
_SERIES = "2023-2025 年，公司甲类业务的销售收入分别为11,000.5 万元、12,000.5 万元和13,000.5 万元"
_RATIO = "2023-2025 年，甲类业务占公司全年营业收入比重为8.1%、7.2%及6.3%"
#: 真实语料形状（离线 run `m32` 逐字）：第二条序列的指标窗里只剩承接词「分别」，读不出业务作用域。
REAL_SCOPE_UNRESOLVED = (
    "2023-2025 年，公司动力电池系统销售收入分别为28,525,291.7 万元、"
    "25,304,133.7 万元和 31,650,636.9 万元，"
    "分别占营业收入的比重为71.2%、69.9%和74.7%")


def _body(text: str) -> str:
    return text if text.endswith("。") else text + "。"


def _series_text() -> str:
    return _body(_SERIES)


def _ids(text: str, *, aspect: str = ASPECT, required: tuple[str, ...] = REQUIRED):
    return ND.extract_disclosures(_body(text), aspect_id=aspect, required_fields=required)


def _codes(skips) -> set:
    return {s.reason_code for s in skips}


def _nd_aspect():
    """期间链夹具的 aspect → **被覆盖面声明**的那个栏目（其余字段一字不动）。"""
    return dc.replace(PC._aspect(), aspect_id=ASPECT, required_fields=REQUIRED)


def _nd_material(text: str, *, mid: str = "m-nd-1") -> tuple:
    """一份真实 evidence_span 材料（复用期间链夹具的同一约定，不另立第二套）。"""
    return PC._material(_body(text), mid=mid, evidence_id="ev-nd", page=1)


def _gate_blocks(decisions, candidates, materials) -> list:
    """白盒调用 Pack 门对决定输入身份的重算（只喂它读的四个字段，不构造 Pack）。"""
    pack = SimpleNamespace(topic_id="t1", materials=list(materials),
                           fact_qualification_decisions=list(decisions), facts=[])
    out: list = []
    PSet._check_decision_inputs(pack, out, {c.candidate_id: c for c in candidates})
    return out


def main() -> dict:  # noqa: C901 - 逐条断言，长而直
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

    def reasons(records, kind: str) -> tuple:
        return tuple(r.failure_reason for r in records
                     if r.check_kind == kind and r.verdict == "hard_error")

    # =============================================================== §1 覆盖面
    details.append("## §1 覆盖面：由冻结字段标签**声明**，表外恒空")
    check(ND.NUMERIC_DISCLOSURE_VERSION == "nd-2",
          f"抽取规则版本（实测 {ND.NUMERIC_DISCLOSURE_VERSION}）")
    check(ND.covered_fields(ASPECT, REQUIRED) == (
        ("各业务收入", "amount", ("收入",)), ("收入占比", "ratio", ("占比", "比重"))),
        f"覆盖字段逐字来自冻结字段标签（实测 {ND.covered_fields(ASPECT, REQUIRED)}）")
    check(ND.covered_fields(E._ASPECT, REQUIRED) == (),
          "表外栏目恒空 —— 覆盖面是**说出来的**，不靠启发式蹭")
    check(ND.covered_fields(ASPECT, ("各业务收入",)) == (
        ("各业务收入", "amount", ("收入",)),),
        "声明里有、冻结 required_fields 里没有的标签必须剔除（宁少覆盖，不自造栏目）")
    check(ND.normalize_text("  a\r\nb\rc  ") == MC.normalize_reading_view("  a\r\nb\rc  ")
          == "a\nb\nc",
          "归一化必须与读视图**逐字同口径**（否则 statement 切片不保证在 reading_view 里）")

    # =============================================================== §2 抽取
    details.append("## §2 并列三年数字必须拆成单值命题")
    ids, conflicts, skips = _ids(_SERIES)
    check(len(ids) == 3 and not conflicts and not skips,
          f"三年并列金额 ⇒ 恰三条单值命题、零冲突零跳过（实测 {len(ids)}/{len(conflicts)}/"
          f"{sorted(_codes(skips))}）")
    check([i.period_key for i in ids] == ["2023", "2024", "2025"],
          f"年份按**位置**配对（实测 {[i.period_key for i in ids]}）")
    check([i.raw_number for i in ids] == ["11,000.5", "12,000.5", "13,000.5"],
          f"原值按位置配对（实测 {[i.raw_number for i in ids]}）")
    check([i.period_display for i in ids] == ["2023年", "2024年", "2025年"],
          "期间表达 = 值自己那一年（不是整句的期间）")
    check({i.field_label for i in ids} == {"各业务收入"} and {i.unit for i in ids} == {"万元"}
          and {i.unit_kind for i in ids} == {"amount"},
          "字段 / 单位 / 单位类逐条绑定")
    check(all(i.scope_key for i in ids) and len({i.scope_key for i in ids}) == 1,
          f"同一并列序列里的值共用同一个业务作用域（实测 {[i.scope_key for i in ids]}）")
    check([i.ordinal for i in ids] == [0, 1, 2], "序数按位置给出")

    body = ND.normalize_text(_series_text())
    for i in ids:
        check(body[i.statement_span[0]:i.statement_span[1]] == i.statement,
              f"statement 必须是归一正文里的**逐字连续切片**（span {i.statement_span}）")
        check(i.statement.startswith("2023-2025 年"),
              f"命题从**年份头**起算（年份逐字留在命题里）：{i.statement!r}")
        check(body[i.value_span[0]:i.value_span[1]] == f"{i.raw_number} {i.unit}",
              f"value_span 指的必须正好是「原值 + 单位」（实测 {i.value_span}）")
        #: 关键性质：把**命题自己**交回句子侧绑定器，必须给出同一个值级期间——否则
        #: item 3 的配对判据在自家事实上永远落不了地（`scp-12` 会一直回落旧判据）。
        back = ND.binding_for(ND.bind_numbers(i.statement), f"{i.raw_number} {i.unit}")
        check(back is not None and back.period_key == i.period_key
              and back.metric_field == i.field_label and back.unit_kind == i.unit_kind
              and back.scope_key == i.scope_key,
              f"命题自反：`bind_numbers(statement)` 必须读出同一身份（实测 "
              f"{back} vs period={i.period_key} field={i.field_label}")

    r_ids, r_conflicts, r_skips = _ids(_RATIO)
    check(len(r_ids) == 3 and not r_conflicts and not r_skips,
          f"三年并列占比 ⇒ 恰三条单值命题（实测 {len(r_ids)}/{sorted(_codes(r_skips))}）")
    check({i.field_label for i in r_ids} == {"收入占比"} and {i.unit_kind for i in r_ids} == {"ratio"}
          and {i.unit for i in r_ids} == {"%"},
          "占比的字段 / 单位类 / 单位逐条绑定")
    check(all(i.scope_key and "占" not in i.scope_key for i in r_ids),
          f"占比的作用域必须在**分母的「占」处切开**，不把分母口径算进业务名"
          f"（实测 {[i.scope_key for i in r_ids]}）")

    details.append("## §2b 挡下的值一律 typed 跳过，且都不铸候选")
    for text, codes, what in (
            ("2023-2025 年，公司甲类业务的销售收入分别为11,000.5 万元和12,000.5 万元",
             {"period_value_count_mismatch"}, "年份个数与并列值个数不一致"),
            ("公司甲类业务的销售收入分别为11,000.5 万元、12,000.5 万元和13,000.5 万元",
             {"period_range_unparsable"}, "指标窗前没有可解析的年份头"),
            ("2023-2025 年，公司甲类业务的销售收入分别为11.0%、12.0%及13.0%",
             {"unit_kind_mismatch"}, "并列值单位类与字段声明的单位类不符"),
            ("2023-2025 年，公司甲类业务营业收入比重为8.1%、7.2%及6.3%",
             {"ratio_denominator_unstated"}, "占比没有可读的分母口径")):
        got_ids, got_cf, got_skips = _ids(text)
        check(not got_ids and not got_cf, f"{what} ⇒ 零条候选（一个都不猜）")
        check(_codes(got_skips) == codes,
              f"{what} ⇒ typed 跳过 {sorted(codes)}（实测 {sorted(_codes(got_skips))}）")

    d_ids, d_cf, d_skips = _ids(
        "2023-2025 年，公司甲类业务的收入增长分别为11.0%、12.0%及13.0%")
    check(not d_ids and not d_cf, "指标窗里出现增速动词 ⇒ 零条水平值候选")
    check(d_skips and d_skips[0].reason_code == "growth_rate_or_delta_not_a_level"
          and d_skips[0].raw_number == "11.0",
          f"第一个值必须记 `growth_rate_or_delta_not_a_level`（实测 "
          f"{[(s.reason_code, s.raw_number) for s in d_skips]}）")

    y_ids, _cy, y_skips = _ids(
        _SERIES + "，同比增长9.0%")
    year_literals = {"2023", "2024", "2025"}
    check(len(y_ids) == 3, f"后接的增速值不得变成第四条水平值（实测 {len(y_ids)} 条）")
    check("growth_rate_or_delta_not_a_level" in _codes(y_skips),
          f"增速值必须记 `growth_rate_or_delta_not_a_level`（实测 {sorted(_codes(y_skips))}）")
    check(all(i.raw_number not in year_literals for i in y_ids)
          and all(s.raw_number not in year_literals for s in y_skips),
          "年份头里的数字**不得**被当成值（`2023` 后面跟的是 `-` 而不是 `年`）")

    details.append("## §2c 冲突两处都铸候选；同键同值只留一条")
    c_ids, c_cf, c_sk = _ids(
        "2023 年公司甲类业务的销售收入为11,000.5 万元，"
        "2023 年公司甲类业务的销售收入为11,000.5 万元")
    check([i.raw_number for i in c_ids] == ["11,000.5"] and not c_cf,
          f"同 (期间,字段,单位) 同原值 ⇒ 只留第一条、不进冲突（实测 "
          f"{[i.raw_number for i in c_ids]}/{len(c_cf)}）")
    check(_codes(c_sk) == {"duplicate_same_value"},
          f"重复同值必须 typed 跳过（实测 {sorted(_codes(c_sk))}）")
    #: 对照：**并列序列**里同值不同年份是**两条不同的命题**，不是重复。
    p_ids, p_cf, p_sk = _ids(
        "2023-2024 年，公司甲类业务的销售收入分别为11,000.5 万元和11,000.5 万元")
    check([i.period_key for i in p_ids] == ["2023", "2024"] and not p_cf and not p_sk,
          f"同值不同年份是两条不同的命题（实测 {[(i.period_key, i.raw_number) for i in p_ids]}）")

    x_ids, x_cf, _x = _ids(
        "2023 年公司甲类业务的销售收入为11,000.5 万元，"
        "2023 年公司甲类业务的销售收入为11,999.5 万元")
    check(not x_ids and len(x_cf) == 2,
          f"同 (期间,字段,单位) 下两个不同原值 ⇒ **两条**都是候选、都不静默丢掉"
          f"（实测 identities={len(x_ids)} conflicts={len(x_cf)}）")
    check({c.raw_number for c in x_cf} == {"11,000.5", "11,999.5"},
          f"先出现的那条必须从候选位**换到**冲突位，不是被删掉（实测 "
          f"{[c.raw_number for c in x_cf]}）")

    details.append("## §2d 表外栏目整个不发生")
    check(_ids(_SERIES, aspect=E._ASPECT) == ((), (), ()),
          "表外栏目 ⇒ 零候选、零冲突、零跳过（本模块不冒充它也有结论）")

    details.append("## §2e 码表守恒：闭集里的每个码都真的有可达输入（不可达的必须写明）")
    mna_ids, mna_cf, mna_sk = _ids(
        "2023-2025 年，公司总资产分别为11,000.5 万元、12,000.5 万元和13,000.5 万元")
    check(not mna_ids and not mna_cf
          and _codes(mna_sk) == {"metric_not_in_aspect_fields"},
          f"指标窗里没有本栏目声明的字段词素 ⇒ `metric_not_in_aspect_fields`（实测 "
          f"{sorted(_codes(mna_sk))}）")
    nem_ids, nem_cf, nem_sk = _ids("公司2025 年营业收入6,243,982.0 万元")
    check(not nem_ids and not nem_cf and _codes(nem_sk) == {"no_enumeration_marker"},
          f"不在任何并列序列里的值 ⇒ `no_enumeration_marker`，**不**另立一套猜年份的启发式"
          f"（实测 {sorted(_codes(nem_sk))}）")

    produced: set = set()
    for _t in (_SERIES, _RATIO,
               "2023-2025 年，公司甲类业务的销售收入分别为11,000.5 万元和12,000.5 万元",
               "公司甲类业务的销售收入分别为11,000.5 万元、12,000.5 万元和13,000.5 万元",
               "2023-2025 年，公司甲类业务的销售收入分别为11.0%、12.0%及13.0%",
               "2023-2025 年，公司甲类业务营业收入比重为8.1%、7.2%及6.3%",
               "2023-2025 年，公司甲类业务的收入增长分别为11.0%、12.0%及13.0%",
               "2023-2025 年，公司总资产分别为11,000.5 万元、12,000.5 万元和13,000.5 万元",
               _SERIES + "，同比增长9.0%",
               REAL_SCOPE_UNRESOLVED,
               "2023 年公司甲类业务的销售收入为11,000.5 万元，"
               "2023 年公司甲类业务的销售收入为11,000.5 万元",
               "公司2025 年营业收入6,243,982.0 万元"):
        produced |= _codes(_ids(_t)[2])
    check(produced <= set(ND.SKIP_REASONS),
          f"产出的码必须全在闭集内（越界 ⇒ {sorted(produced - set(ND.SKIP_REASONS))}）")
    unreachable = set(ND.SKIP_REASONS) - produced
    check(unreachable == {"metric_field_ambiguous"},
          f"`metric_field_ambiguous` 是 `nd-2` 词素表下**唯一**不可达的码（`收入` 与 `占比／比重`"
          f"不可能落在同一结束位），留给下一版词素表；它一旦变得可达、或别的码变得不可达，"
          f"都必须回来改这一条（实测差集 {sorted(unreachable)}）")

    details.append("## §2f `nd-2`：作用域进键 + 读不出作用域整条跳过")
    #: 异业务同年不同值：**不是**冲突（两条各自成立的命题），两条都铸候选。
    nb_ids, nb_cf, nb_sk = _ids(
        "2025 年公司甲类业务的销售收入为11,000.5 万元，"
        "2025 年公司乙类业务的销售收入为12,000.5 万元")
    check(len(nb_ids) == 2 and not nb_cf and not nb_sk,
          f"不同业务同年不同值 ⇒ 两条候选、**零冲突**（实测 {len(nb_ids)}/{len(nb_cf)}/"
          f"{sorted(_codes(nb_sk))}，作用域 {[i.scope_key for i in nb_ids]}）")
    #: 对照：同业务同年不同值仍然两条都进冲突（键里加轴**不**放宽这一条）。
    sb_ids, sb_cf, _sb_sk = _ids(
        "2025 年公司甲类业务的销售收入为11,000.5 万元，"
        "2025 年公司甲类业务的销售收入为11,999.5 万元")
    check(not sb_ids and len(sb_cf) == 2,
          f"同业务同年不同值 ⇒ 仍**两条**都进冲突（实测 identities={len(sb_ids)} "
          f"conflicts={len(sb_cf)}）")

    #: 真实语料形状（`m32`）：`…，分别占营业收入的比重为 A、B、C`——占比那一条指标窗里只剩
    #: 承接词「分别」，`nd-1` 会把它当业务名铸出作用域为空的事实。`nd-2` 整条跳过。
    rs_ids, rs_cf, rs_sk = _ids(REAL_SCOPE_UNRESOLVED)
    check(len(rs_ids) == 3 and not rs_cf
          and {i.field_label for i in rs_ids} == {"各业务收入"}
          and [i.period_key for i in rs_ids] == ["2023", "2024", "2025"],
          f"作用域读得出的那条序列照旧铸三条候选（实测 "
          f"{[(i.field_label, i.period_key) for i in rs_ids]}）")
    check(_codes(rs_sk) == {"scope_unresolved"},
          f"作用域只剩承接词「分别」的那条序列必须整条 typed 跳过（实测 "
          f"{sorted(_codes(rs_sk))}）")
    check({s.raw_number for s in rs_sk} == {"71.2", "69.9", "74.7"},
          f"跳过的必须是占比那三个值本身（实测 {sorted(s.raw_number for s in rs_sk)}）")

    # =============================================================== §3 资格与身份
    details.append("## §3 数值披露候选与模型 claim 走**同一**个资格门")
    material, payload_bytes, locator = _nd_material(_SERIES)
    resolver = PC._Resolver({material.payload_ref.content_hash: (locator, payload_bytes)})
    aspect = _nd_aspect()
    ref = TR._citation_for(material)
    identity = _ids(_SERIES)[0][0]
    cand, dec, fact = TR._qualify_and_adopt(
        aspect, statement=identity.statement, fact_type="fact",
        period=identity.period_display, period_failure=None,
        materials=(material,), refs=(ref,), source_key="nd-" + str(material.material_id))
    check(dec.verdict == "eligible" and fact is not None,
          f"权威材料 + 显式值级期间 ⇒ eligible（实测 {dec.verdict}）")
    check(fact is not None and fact.period == "2023年" == identity.period_display,
          f"事实的期间是**它自己那一年**（实测 {getattr(fact, 'period', None)!r}）")
    check(fact is not None and fact.candidate_id == cand.candidate_id
          and fact.candidate_revision == cand.candidate_revision
          and fact.qualification_decision_id == dec.decision_id,
          "qualified result 单向回指 candidate id/revision 与 eligible 决定 id")
    check(fact is not None and fact.citation_refs == (ref,)
          and fact.source_authority == material.authority_assessment,
          "事实的引用与权威身份取自它自己的材料")
    check(fact is not None and TS.citation_source_identity(fact.citation_refs[0])
          == TS.authority_source_identity(fact.source_authority),
          "引用身份与事实的权威来源身份必须在同一身份域（否则 Pack 门会挡）")

    blocks = _gate_blocks([dec], [cand], [material])
    check(not blocks,
          f"三个 digest 必须能被 Pack 门按**声明的输入集**复算出来（实测 "
          f"{[b.reason for b in blocks]}）")

    _c2, dec_x, fact_x = TR._qualify_and_adopt(
        aspect, statement=identity.statement, fact_type="fact",
        period=identity.period_display, period_failure=None, materials=(material,),
        refs=(ref,), source_key="nd-x", force_reject_reason=ND.CONFLICT_REASON)
    check(dec_x.verdict == "rejected" and fact_x is None
          and dec_x.rejection_reason == ND.CONFLICT_REASON,
          f"冲突值 ⇒ 恰一条 rejected 决定、零条 qualified result（实测 "
          f"{dec_x.verdict}/{dec_x.rejection_reason!r}）")

    #: 同一材料同一命题、同一 source_key ⇒ fact_id 必须可复现（确定性入口不得每次换身份）。
    _c3, _d3, fact3 = TR._qualify_and_adopt(
        aspect, statement=identity.statement, fact_type="fact",
        period=identity.period_display, period_failure=None, materials=(material,),
        refs=(ref,), source_key="nd-" + str(material.material_id))
    check(fact3 is not None and fact is not None and fact3.fact_id == fact.fact_id,
          "同一输入重跑必须得到同一 fact_id（身份由内容派生，不随调用次序漂移）")

    #: `nd-2`：逐值身份**不是**旁路实参，它的期间必须与候选期间逐字相同，否则构造器当场拒。
    try:
        TS.build_supported_fact(
            "fact-nd-probe", cand, dec, text=identity.statement, fact_type="fact",
            citation_refs=(ref,), source_authority=material.authority_assessment,
            value_identity=TS.ValueIdentity(
                value_kind=identity.unit_kind, metric=identity.field_label, unit=identity.unit,
                period="2024年", scope=identity.scope_text,
                amount_canonical=identity.raw_number))
    except TS.SchemaValidationError as exc:
        check("值级期间" in str(exc),
              f"值身份声明期间与候选期间不一致 ⇒ 构造器当场拒（实测 {str(exc)[:36]!r}）")
    else:
        check(False, "值身份声明期间与候选期间不一致 ⇒ 构造器必须拒（实测未拒）")
    #: 对照：期间一致 ⇒ 同一构造器放行，并把身份原样挂上（不是「有声明就一律拒」）。
    vi_ok = TS.ValueIdentity(
        value_kind=identity.unit_kind, metric=identity.field_label, unit=identity.unit,
        period=identity.period_display, scope=identity.scope_text,
        amount_canonical=identity.raw_number)
    fact_vi = TS.build_supported_fact(
        "fact-nd-probe", cand, dec, text=identity.statement, fact_type="fact",
        citation_refs=(ref,), source_authority=material.authority_assessment,
        value_identity=vi_ok)
    check(fact_vi.value_identity is vi_ok and fact_vi.period == identity.period_display,
          f"期间一致 ⇒ 放行且身份原样挂上（实测 {fact_vi.value_identity}）")

    details.append("## §3b 重构后**模型 claim 那条路**的身份逐字未变")
    pc_text = "截至2025年12月31日，公司为部分客户提供产品质量保证，计提预计负债 3 亿元。"
    pc_cands, pc_decs, pc_facts, _pc_rej = PC._adopt(pc_text)
    legacy_fact_id = "fact-tr-" + hashlib.sha256(json.dumps(
        {"aspect_id": PC._aspect().aspect_id, "source_key": "c0", "text": pc_text,
         "sources": sorted({TS.citation_source_identity(c) for c in pc_facts[0].citation_refs})},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    check(len(pc_facts) == 1 and pc_facts[0].fact_id == legacy_fact_id,
          f"`fact_id` 派生式不得因抽公共函数而改变（实测 "
          f"{pc_facts[0].fact_id if pc_facts else None} vs {legacy_fact_id}）")
    check(len(pc_cands) == 1 and len(pc_decs) == 1
          and pc_cands[0].period == pc_facts[0].period,
          "候选期间经唯一构造器逐字进入结果")
    check(pc_facts and pc_facts[0].period == "2025-12-31",
          f"模型那条路抽到的仍是**整句**显式期间（实测 {pc_facts[0].period!r}）——"
          f"nd-1 的位置配对只作用于它自己铸的候选，不改这条路")

    details.append("## §3c 值级期间与整句期间**不是**同一个问题")
    whole = TR._cited_explicit_period((material,), resolver, {}, statement=identity.statement)
    check(whole[0] == "2025年度" and whole[1] is None,
          f"`period_extraction` 对整句只答**一个**期间（实测 {whole[0]!r}）——这正是 nd-1 "
          f"**不**调用它的原因：这条句子里是 2023、2024、2025 三条值，值级期间由位置配对给出")

    # =============================================================== §4 句子配对
    details.append("## §4 句子—事实语义配对（`scp-12`）")
    check(SC.SENTENCE_CHECK_POLICY_VERSION == "scp-13",
          f"当前逐句政策版本（实测 {SC.SENTENCE_CHECK_POLICY_VERSION}）")
    check(SC.SENTENCE_CHECK_POLICY_VERSION in SC.FAMILY_SCOPED_BLOCKING_POLICY_VERSIONS
          and "scp-10" in SC.FAMILY_SCOPED_BLOCKING_POLICY_VERSIONS,
          "当前版本必须在分族聚合登记表里，旧产物（`scp-10`）仍按它写时的口径解码")

    task, authority = E._task_and_authority()
    facts = E._scan_facts(authority, task)
    manifest = E._input_manifest(authority=authority, facts=facts)
    subsection_id = manifest.subsections[0].subsection_id
    nd_row = _fact_like(manifest.facts[0], _series_text())
    man = _manifest_with(manifest, manifest.materials, [nd_row])
    fact_key = nd_row.citation_key

    def check_sentence(text: str):
        return SC.check_sentence(
            sentence=CW.CitedSentence(sentence_id="s0001", text=text,
                                      citations=(fact_key,),
                                      numeric_tokens=NS.scan_numeric_tokens(text)),
            subsection_id=subsection_id, paragraph_id="p1", manifest=man)

    p1 = check_sentence("2025 年公司甲类业务的销售收入为13,000.5 万元。")
    check(not reasons(p1, "numeric_qualification"),
          f"P1 正确配对（同业务、当年、同为金额类）必须通过"
          f"（实测 {reasons(p1, 'numeric_qualification')}）")
    num_rec = [r for r in p1 if r.check_kind == "numeric_surface"][0]
    check(num_rec.verdict == "pass" and num_rec.numeric_bases == ("qualified_fact",),
          f"数字轴来源类型仍是 qualified_fact（实测 {num_rec.numeric_bases}）")
    check(not reasons(p1, "numeric_surface"),
          f"P1 的年份 token 也必须被同一条事实授权（实测 {reasons(p1, 'numeric_surface')}）")

    for text, label in (
            ("2025 年公司甲类业务的销售收入为11,000.5 万元。",
             "N1 同数字不同年（2023 的原值写成 2025）"),
            ("2025 年公司乙类业务的销售收入为13,000.5 万元。", "N2 同数字不同业务"),
            ("2025 年公司甲类业务的收入占比为13,000.5 万元。", "N3 把收入金额当收入占比")):
        recs = check_sentence(text)
        check(reasons(recs, "numeric_qualification") == ("numeric_basis_not_qualified",),
              f"{label} 必须在 `numeric_qualification` 上判错"
              f"（实测 {reasons(recs, 'numeric_qualification')}）")
        check(not reasons(recs, "numeric_surface"),
              f"{label} 不得被误记成「来源里没有这个数字」（那是另一档）"
              f"（实测 {reasons(recs, 'numeric_surface')}）")

    p2 = check_sentence("2025 年公司收入为13,000.5 万元。")
    check(not reasons(p2, "numeric_qualification") and not reasons(p2, "numeric_surface"),
          f"P2 句子侧给不出**完整**绑定（业务作用域太短）⇒ 回落旧判据、不新造硬错"
          f"（实测 {reasons(p2, 'numeric_qualification')} + {reasons(p2, 'numeric_surface')}）")

    n4 = check_sentence("2025 年公司甲类业务的销售收入为13,000.5 亿元。")
    check(bool(reasons(n4, "numeric_surface")),
          f"N4 万元当亿元：改单位后的 token 在来源里没有逐字出现，须判错"
          f"（实测 {reasons(n4, 'numeric_surface')} + {reasons(n4, 'numeric_qualification')}）")

    details.append("## §4c 事实**自己声明**逐值身份 ⇒ 只按声明配对（`nd-2`）")
    #: `nd-2` 起，分业务营收事实的 `text` 是**从年份头到本值的逐字前缀**——2025 那条里因此
    #: 带着 2023/2024 两个值。不声明身份时，逐句核对会从这段前缀把前值也读成本事实的。
    prefix_2025 = _SERIES                       # 逐字前缀（归一后 = 全句，三个值都在）

    def _declared(base, index: int, period: str, *, field: str, kind: str, scope: str,
                  raw: str, unit: str = "万元"):
        """把一条事实行的文本换成整句**逐字前缀**，并按本值挂上声明的逐值身份。"""
        fields = {name: getattr(base, name) for name in base.__dataclass_fields__}
        fields["text"] = prefix_2025
        fields["period"] = period
        fields["citation_key"] = CW.cited_fact_key(index)
        fields["fact_id"] = f"fact-nd-{index}"
        fields["value_identity"] = {
            "value_kind": kind, "metric": field, "unit": unit,
            "period": period, "scope": scope, "amount_canonical": raw}
        return type(base)(**fields)

    base_row = manifest.facts[0]
    dec_2025 = _declared(base_row, 0, "2025年", field="各业务收入", kind="amount",
                         scope="公司甲类业务销售", raw="13,000.5")
    dec_2023 = _declared(base_row, 1, "2023年", field="各业务收入", kind="amount",
                         scope="公司甲类业务销售", raw="11,000.5")
    man_dec = _manifest_with(manifest, manifest.materials, [dec_2025, dec_2023])
    key_2025, key_2023 = dec_2025.citation_key, dec_2023.citation_key

    def check_declared(text: str, key: str):
        return SC.check_sentence(
            sentence=CW.CitedSentence(sentence_id="s0001", text=text, citations=(key,),
                                      numeric_tokens=NS.scan_numeric_tokens(text)),
            subsection_id=subsection_id, paragraph_id="p1", manifest=man_dec)

    #: N5：2025 那条事实的文本里**逐字**含 2023 的原值，但声明的身份是 13,000.5/2025。
    #: 把它写成 2023 年那个值 ⇒ 必须判错（`scp-12` 的文本反推会假绿）。
    n5 = check_declared("2025 年公司甲类业务的销售收入为11,000.5 万元。", key_2025)
    check(reasons(n5, "numeric_qualification") == ("numeric_basis_not_qualified",),
          f"N5 声明了 2025/13,000.5 的事实**不得**授权它前缀里的 2023 值"
          f"（实测 {reasons(n5, 'numeric_qualification')}）")
    check(not reasons(n5, "numeric_surface"),
          f"N5 那个 token 确实逐字在事实文本里 ⇒ 记 `numeric_basis_not_qualified`，"
          f"不记 `unsourced_number_surface`（实测 {reasons(n5, 'numeric_surface')}）")

    #: N6：同一年、不同业务——乙业务不得借甲业务事实的声明（四轴里的作用域轴）。
    n6 = check_declared("2025 年公司乙类业务的销售收入为13,000.5 万元。", key_2025)
    check(reasons(n6, "numeric_qualification") == ("numeric_basis_not_qualified",),
          f"N6 声明作用域是甲业务的事实**不得**授权乙业务"
          f"（实测 {reasons(n6, 'numeric_qualification')}）")

    #: P3：声明与句意四轴一致的正面例必须通过。
    p3 = check_declared("2025 年公司甲类业务的销售收入为13,000.5 万元。", key_2025)
    check(not reasons(p3, "numeric_qualification"),
          f"P3 声明与句意一致 ⇒ 通过（实测 {reasons(p3, 'numeric_qualification')}）")
    #: P3 对照：同一条事实**不**授权另一个值——证明声明是**逐值**的，不是「文本里最后一个数」。
    p3c = check_declared("2025 年公司甲类业务的销售收入为11,000.5 万元。", key_2025)
    check(reasons(p3c, "numeric_qualification") == ("numeric_basis_not_qualified",),
          f"P3 对照：同一条事实不得同时授权 2023 那个值（实测 "
          f"{reasons(p3c, 'numeric_qualification')}）")

    #: P4：**2023 那条**事实（同样前缀、声明 11,000.5/2023）反过来必须授权 2023 的句子。
    p4 = check_declared("2023 年公司甲类业务的销售收入为11,000.5 万元。", key_2023)
    check(not reasons(p4, "numeric_qualification"),
          f"P4 2023 那条事实必须授权 2023 的句子——声明按值给，不是「只有最后一条算」"
          f"（实测 {reasons(p4, 'numeric_qualification')}）")

    #: P5：声明缺失 ⇒ 走原文本反推这条腿，历史行为逐字不变（**不**因新键而收紧）。
    p5 = check_sentence("2025 年公司甲类业务的销售收入为13,000.5 万元。")
    check(not reasons(p5, "numeric_qualification"),
          f"P5 未声明身份的旧事实行仍按文本反推（实测 {reasons(p5, 'numeric_qualification')}）")

    #: 键是**条件性**写出的：`None` 时整条键不出现 ⇒ 历史清单身份体逐字不变。
    plain = _fact_like(base_row, base_row.text)
    check("value_identity" not in plain.to_dict(),
          "`value_identity=None` ⇒ `to_dict()` 里整条键不出现（历史 `manifest_id` 不受影响）")
    check(dec_2025.to_dict().get("value_identity") == dec_2025.value_identity,
          "声明了身份 ⇒ `to_dict()` 里按原值写出")
    check(CW._fact_from_dict(dec_2025.to_dict()).value_identity == dec_2025.value_identity,
          "写出的身份体可被逐字读回（读盘按文件自己声明的版本分派）")

    details.append("## §4d 已声明身份的事实**不得**回落放行（`scp-13`）")
    #: `scp-12` 把事实侧改成了「按事实自己声明的身份配对」，但 `_fact_numeric_authorization`
    #: 里**两条 AUTHORIZED 回落分支跑在声明循环之前**：句子侧只要读不出完整绑定（金额的指标窗
    #: 空、或没有可命的绑定），一条**声明了身份**的事实仍然会放行任何落在它文本里的金额／比率。
    #: 本段钉两个反例（都是**真实 run 的形态**，只是换成合成占位业务名）：

    #: 反例 1：声明的金额事实被写成**另一个业务**，且句子侧的指标窗读不出（`metric_field=''`
    #: ⇒ 绑定不完整）。`scp-12` 在这里从回落 A 直接放行。
    def _decl_text(base, index: int, period: str, *, field: str, kind: str, scope: str,
                   raw: str, text: str, unit: str = "万元"):
        fields = {name: getattr(base, name) for name in base.__dataclass_fields__}
        fields["text"] = text
        fields["period"] = period
        fields["citation_key"] = CW.cited_fact_key(index)
        fields["fact_id"] = f"fact-ndd-{index}"
        fields["value_identity"] = {
            "value_kind": kind, "metric": field, "unit": unit,
            "period": period, "scope": scope, "amount_canonical": raw}
        return type(base)(**fields)

    ratio_text = _body(_RATIO)
    d_amt = _decl_text(base_row, 0, "2025年", field="各业务收入", kind="amount",
                       scope="公司甲类业务销售", raw="13,000.5", text=prefix_2025)
    d_ratio = _decl_text(base_row, 1, "2025年", field="收入占比", kind="ratio",
                         scope="甲类业务", raw="6.3", text=ratio_text, unit="%")
    #: 对照：**没有**声明身份的旧事实行（财务／附注／旧夹具那一类），行为必须逐字不变。
    plain_ratio = dc.replace(base_row, text=ratio_text, period="2025年",
                             citation_key=CW.cited_fact_key(2), fact_id="fact-ndd-2")
    man_dd = _manifest_with(manifest, manifest.materials, [d_amt, d_ratio, plain_ratio])

    def _dd(text: str, key: str):
        return SC.check_sentence(
            sentence=CW.CitedSentence(sentence_id="s0001", text=text, citations=(key,),
                                      numeric_tokens=NS.scan_numeric_tokens(text)),
            subsection_id=subsection_id, paragraph_id="p1", manifest=man_dd)

    dd_a1 = _dd("2025 年公司甲类业务的销售收入为13,000.5 万元。", d_amt.citation_key)
    check(not reasons(dd_a1, "numeric_qualification") and not reasons(dd_a1, "numeric_surface"),
          f"正例（声明金额·句意一致）仍必须通过（实测 "
          f"{reasons(dd_a1, 'numeric_qualification')} + {reasons(dd_a1, 'numeric_surface')}）")

    dd_a2 = _dd("2025 年公司乙类业务为13,000.5 万元。", d_amt.citation_key)
    check(reasons(dd_a2, "numeric_qualification") == ("numeric_basis_not_qualified",),
          f"**反例 1**：声明了甲业务 2025 金额的事实，**不得**因为「句子侧读不出指标」就放行"
          f"（实测 {reasons(dd_a2, 'numeric_qualification')}）")
    check(not reasons(dd_a2, "numeric_surface"),
          f"反例 1 的 token 逐字在事实文本里 ⇒ 记 `numeric_basis_not_qualified`，"
          f"不记 `unsourced_number_surface`（实测 {reasons(dd_a2, 'numeric_surface')}）")

    dd_a3 = _dd("2025 年公司甲类业务的销售收入为11,000.5 万元。", d_amt.citation_key)
    check(reasons(dd_a3, "numeric_qualification") == ("numeric_basis_not_qualified",),
          f"对照：同一条声明事实的前值（既有 `scp-12` 反例）照旧判错（实测 "
          f"{reasons(dd_a3, 'numeric_qualification')}）")

    #: 反例 2b：占比事实的四轴（业务／期间／指标／单位）**全等**，只有**分母**不同。
    #: 逐值身份里没有分母这一轴 ⇒ 「同为百分比」曾当面被视为配对成功。
    dd_r1 = _dd("2025 年甲类业务占乙类业务收入比重为6.3%。", d_ratio.citation_key)
    check(reasons(dd_r1, "numeric_qualification") == ("numeric_basis_not_qualified",),
          f"**反例 2b**：分母不同不得因「同为百分比」而放行（实测 "
          f"{reasons(dd_r1, 'numeric_qualification')}）")
    check(not reasons(dd_r1, "numeric_surface"),
          f"反例 2b 的 token 逐字在事实文本里 ⇒ 也记 `numeric_basis_not_qualified`"
          f"（实测 {reasons(dd_r1, 'numeric_surface')}）")

    #: **保守代价（必须写明的诚实读数）**：现有六个身份字段（`value_kind`／`metric`／`unit`／
    #: `period`／`scope`／`amount_canonical`）**没有分母的位置**（`metric` 是封闭词表标签、
    #: `scope` 是分子业务），要「可靠承载、传递并验证分母」须给 `ValueIdentity` / `NumberBinding`
    #: 各加一轴并改抽取——那是新增能力，不属本批「定点安全收尾」。因此**已声明的占比事实一律
    #: 不授权**：分母写对的同形正句也一并落在这一档（不是判它写错，是判**核不了**）。
    dd_r2 = _dd("2025 年甲类业务占公司全年营业收入比重为6.3%。", d_ratio.citation_key)
    check(reasons(dd_r2, "numeric_qualification") == ("numeric_basis_not_qualified",),
          f"保守口径：已声明的占比事实在分母可核之前**一律**不授权（同形正分母也在此档；实测 "
          f"{reasons(dd_r2, 'numeric_qualification')}）")
    dq = [r for r in dd_r1 if r.check_kind == "numeric_qualification"][0]
    check("分母" in (dq.detail or ""),
          f"分母那一支必须在 `detail` 里说得出来（人读可辨，不靠猜；实测 {dq.detail!r}）")

    #: 对照：**未**声明身份的旧事实行走原文本反推这条腿 ⇒ 逐字不变（本批不得动既有一族）。
    dd_o1 = _dd("2025 年甲类业务占公司全年营业收入比重为6.3%。", plain_ratio.citation_key)
    check(not reasons(dd_o1, "numeric_qualification"),
          f"**反例**：没有 `value_identity` 的既有事实行行为必须逐字不变（实测 "
          f"{reasons(dd_o1, 'numeric_qualification')}）")

    check({SC.FACT_NUMERIC_ABSENT, SC.FACT_NUMERIC_AUTHORIZED, SC.FACT_NUMERIC_MISMATCH,
           SC.FACT_NUMERIC_UNVERIFIABLE, SC.FACT_NUMERIC_DENOMINATOR_UNVERIFIED}
          == {"absent", "authorized", "mismatch", "unverifiable", "denominator_unverified"},
          "事实侧状态仍是**封闭集合**：两个新态各有自己的取值，下游不能把「核不了」读成放行")

    # ============================================ §4e 请求面与逐句核对读**同一处**判定（`ndc-4`）
    details.append("## §4e `cp-25`/`ndc-4`：请求面把「Pack 登记」与「本版可写」分成两根轴")
    #: 动因（`ndc-3` 离线纵链的 journal `request_face`，只读）：公司 Pack 里 12 条分业务事实
    #: （9 金额 + 3 占比）**全部**落在 `revenue_breakdown` 栏的 `citable_fact_keys` 里，而
    #: `cp-24` 的栏目目标写着「有合格事实就写占比」——请求面**承诺**的正是 `scp-13` **拒绝**的
    #: （任何绑定占比事实的句子一律 `denominator_unverified` ⇒ `numeric_basis_not_qualified`）。
    #: 修法不是放松判据（`scp-13` 一字未动），是让请求面**说实话**：多一根**派生**轴，读的是
    #: 判据自己那份实现，因此两侧不可能各说各话。

    #: (a) 事实级读数：两种取值，同一处实现。
    check(SC.fact_numeric_writability(d_amt) == SC.FACT_NUMERIC_AUTHORIZED,
          "**正例**：声明了身份的**金额**事实本版可写（它仍要过句子侧四轴；这里只说没被整条挡下）")
    check(SC.fact_numeric_writability(d_ratio) == SC.FACT_NUMERIC_DENOMINATOR_UNVERIFIED,
          "**反例**：声明了身份的**占比**事实本版不可写——分母在现有身份字段里无处承载")
    check(SC.fact_numeric_writability(plain_ratio) == SC.FACT_NUMERIC_AUTHORIZED,
          "对照：**未**声明身份的旧事实行走原判据（历史行为一字不变）")

    #: (b) 撤回清单：逐条给键与原因码，键序 = 清单序。
    _withheld = CW.withheld_numeric_fact_keys(manifest=man_dd)
    check(_withheld == ((d_ratio.citation_key, SC.FACT_NUMERIC_DENOMINATOR_UNVERIFIED),),
          f"**正例**：撤回清单逐条给键与原因码（实测 {_withheld}）")

    #: (c) 逐栏两根轴：登记轴**一字未动**，可写轴是它的**子集**。
    _cols_dd = CW.citable_columns(man_dd, man_dd.subsections[0])
    _reg_cols = [c for c in _cols_dd if d_ratio.citation_key in c["citable_fact_keys"]]
    check(bool(_reg_cols),
          "夹具自检：占比事实登记到了本节的某一栏（否则下面几条读数没有意义）")
    for _c in _reg_cols:
        check(d_ratio.citation_key in _c["citable_fact_keys"]
              and d_ratio.citation_key not in _c["writable_fact_keys"],
              f"**反例**：占比键**仍在** `citable_fact_keys`（登记语义一字未动）里、"
              f"**不在** `writable_fact_keys` 里（实测可写集 {_c['writable_fact_keys']}）")
        check(d_amt.citation_key in _c["writable_fact_keys"],
              "**正例**：同栏的**金额**键在可写集里——不得因为同栏有占比就把这一栏整段撤掉")
        check(set(_c["writable_fact_keys"]) <= set(_c["citable_fact_keys"]),
              "`writable_fact_keys` 是 `citable_fact_keys` 的**子集**：请求面不新造候选")

    #: (d) 顶层授权块：`authorizable ∪ withheld = 全部事实键`，`complete` 是结论不是抽样。
    _face_dd = CW.build_cited_prose_request(manifest=man_dd)
    _na = _face_dd["numeric_authorization"]
    _face_keys = [str(f["key"]) for f in _face_dd["authority_facts"]]
    check(_na["policy_version"] == SC.SENTENCE_CHECK_POLICY_VERSION,
          f"授权读数必须写明它是按**哪一版判据**算的（实测 {_na['policy_version']}）")
    check(_na["withheld"] == [{"key": d_ratio.citation_key,
                               "state": SC.FACT_NUMERIC_DENOMINATOR_UNVERIFIED}],
          f"**正例**：请求面逐条标出本版不可写的键与原因码（实测 {_na['withheld']}）")
    check(_na["complete"] is True
          and list(_na["authorizable_fact_keys"])
          == [k for k in _face_keys if k != d_ratio.citation_key],
          f"**正例**：`authorizable ∪ withheld = 全部事实键`（实测 "
          f"{_na['authorizable_fact_keys']} / {_na['withheld']}）")
    check(d_ratio.citation_key in _face_keys,
          "**反例**：被撤回的占比事实**仍在** `authority_facts` 里——"
          "请求面照旧看见全部事实与它们的审计去向（撤下的是**可写性**，不是**存在**）")

    #: (e) **两根轴互相印证**：请求面说不可写的键，逐句核对在真句子上确实不授权；
    #: 请求面说可写的键，同形正句仍通过。同一条判据，不可能一处说能写、一处判硬错。
    check(reasons(dd_r2, "numeric_qualification") == ("numeric_basis_not_qualified",)
          and not reasons(dd_a1, "numeric_qualification"),
          "**正反例对账**：`withheld` 里的键在逐句核对上确实不授权（分母可核之前一律不写），"
          "而可写的金额键的同形正句仍通过")

    #: (f) **反例**：不得把登记轴直接换成可写集——那会作废「哪条事实登记到本栏」这条可回查的
    #: 结论（`cp-17` 立的就是它）。被撤回的键必须**仍然**出现在 `citable_fact_keys` 里。
    check(any(d_ratio.citation_key in c["citable_fact_keys"] for c in _cols_dd)
          and not any(set(c["citable_fact_keys"]) == set(c["writable_fact_keys"])
                      for c in _cols_dd if d_ratio.citation_key in c["citable_fact_keys"]),
          "**反例**：`citable_fact_keys` 仍是 Pack 侧**登记**（被撤回的键仍在其中），"
          "可写集是**另一根**轴——两者不得被合成一个字段")

    details.append("## §4b 配对原语的边界")
    def _b(raw, period, scope, field, kind):
        return ND.NumberBinding(raw, period, scope, field, kind)

    m1 = _b("1,000.5", "2023", "甲公司类业务销售", "各业务收入", "amount")
    m2 = _b("1,000.5", "2023", "甲公司类业务销售", "各业务收入", "amount")
    m3 = _b("1,000.5", "2024", "甲公司类业务销售", "各业务收入", "amount")
    m4 = _b("1,000.5", "2023", "乙公司类业务销售", "各业务收入", "amount")
    m5 = _b("1,000.5", "2023", "甲公司类业务销售", "收入占比", "amount")
    empty = _b("1,000.5", "", "", "", "")
    check(ND.complete(m1) and not ND.complete(empty),
          "`complete` 只认四个分量都读得出的那一侧")
    check(ND.binding_compatible(m1, m2) and not ND.binding_compatible(m1, m3)
          and not ND.binding_compatible(m1, m4) and not ND.binding_compatible(m1, m5),
          "四轴逐条：期间 / 指标字段 / 单位类 / 业务作用域，任一不同即不兼容")
    check(ND.scope_compatible("公司甲类业务销售", "甲类业务销售")
          and ND.scope_compatible("甲类业务销售", "公司甲类业务销售"),
          "作用域用**互相包含**，允许正常改写")
    check(ND.scope_compatible("", "甲类业务销售") and ND.scope_compatible("甲类业务销售", ""),
          "空键 ⇒ 兼容（那一侧给不出绑定，本就不该收紧）")
    check(ND.binding_for((m1, m3), "1,000.5 万元") is m1
          and ND.bindings_for((m1, m3), "1,000.5 万元") == (m1, m3),
          "同一原值多条绑定时必须**全部**给出（只取首条会把「另有正确的一条」误判成不匹配）")

    # =============================================================== §5 进 Writer
    details.append("## §5 这样一条事实必须真的进 `f*` 行")
    nd_text = _series_text()
    nd_task = E.T._task("company", (E._TOPIC,))
    nd_pack = E.T._pack_set(
        nd_task,
        packs=(E.T._Pack(topic_id=E._TOPIC,
                         materials=(E.T._Material(E._MAT_A, f"evidence:{E.T.EV_ID}", page=12),),
                         facts=(E.T._fact("f-nd-1", nd_text, (ASPECT,), page=12, period="2023年"),),
                         aspect_results=(E.T._AspectResult(ASPECT, "covered"),)),),
        requirements=(E.T._Req(E._TOPIC, (E.T._aspect(
            ASPECT, E._TOPIC, E.T._question_id(E._TOPIC),
            requirement_text=E._REQ_TEXT),)),))
    nd_authority = PW.TopicPackAuthorityInput.create(
        nd_task, nd_pack, company_id=E.T.COMPANY_ID, report_as_of=E.T.REPORT_AS_OF,
        contract_version=E.T.CONTRACT_VERSION, contract_fingerprint=E.T.CONTRACT_FINGERPRINT)
    scanned = PW.scan_topic_pack(nd_authority, nd_task).facts
    check([str(f.fact_id) for f in scanned] == ["f-nd-1"],
          f"带**值级**期间的合格事实必须过 Pack 侧期间门与其他资格门（实测 "
          f"{[str(f.fact_id) for f in scanned]}）")
    nd_manifest = E._input_manifest(authority=nd_authority, facts=scanned)
    rows = [f for f in nd_manifest.facts if f.text == nd_text]
    check(len(rows) == 1 and str(rows[0].citation_key).startswith("f"),
          f"写作输入清单里必须**恰好一条** `f*` 行，内容与命题逐字相同（实测 "
          f"{[(f.citation_key, f.text[:12]) for f in nd_manifest.facts]}）")
    check(bool(rows) and str(getattr(rows[0], "period", "")) == "2023年",
          f"该行带到写作侧的期间必须是它**自己**那一年（实测 "
          f"{getattr(rows[0], 'period', None) if rows else None!r}）")

    # =============================================================== §6 实盘对照
    details.append("## §6 实盘对照（**只读**既有离线 run 的清单；不存在即跳过）")
    manifest_path = (Path(__file__).resolve().parent.parent / "evaluation" / "results"
                     / "m930_3_cited_offline_co5_company_r1" / "cited_input_manifest.json")
    if not manifest_path.exists():
        skipped += 1
        details.append("SKIP: 离线 run 清单不在盘上，实盘对照跳过（**不**当成通过）")
    else:
        doc = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows_by_key = {m.get("citation_key"): m for m in (doc.get("materials") or [])}
        hit = 0
        for key, material in sorted(rows_by_key.items()):
            text = material.get("reading_view") or ""
            if "分别为" not in text:
                continue
            got, _cf, _sk = _ids(text)
            if not got:
                continue
            hit += 1
            norm = ND.normalize_text(text)
            check(all(i.statement in norm for i in got),
                  f"{key}: 每条命题都必须逐字存在于该材料的读视图里")
            check(len({(i.field_label, i.period_key) for i in got}) == len(got)
                  and all(i.period_key.isdigit() and len(i.period_key) == 4 for i in got),
                  f"{key}: 同字段下每条命题的年份必须值级且互不相同（实测 "
                  f"{[(i.field_label, i.period_key) for i in got]}）")
            check(all(i.unit_kind in ("amount", "ratio") for i in got),
                  f"{key}: 每条命题都必须有明确的单位类（不许裸数字进候选）")
        check(hit > 0,
              f"实盘清单里「…分别为…」的材料必须真的能抽出单值命题（实测 {hit} 份）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    result = main()
    for d in result["details"]:
        print(d)
    print(f"passed={result['passed']} failed={result['failed']} skipped={result['skipped']}")
    sys.exit(1 if result["failed"] else 0)

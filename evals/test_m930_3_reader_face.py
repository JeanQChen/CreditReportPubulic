"""Eval: M930-3 §四 读者面（`pwr-4` / `pwr-5`）—— 人读正文里的**单位 / 主体 / 代理口径 / 来源 / 缺口**。

用法: python -m evals.test_m930_3_reader_face

本模块钉住五类**只有读者看得见**的缺陷，前四类全都在真实 r7b 产物里逐字出现过
（`evaluation/results/m930_3_acceptance_crossdoc_real_r7b/manual_review.md`）：

  1. **单位**：表格那行印的是权威的量纲记号 `单位：yuan` / `单位：ratio` / `单位：percent`，
     而同一张表的单元格渲染的是 `1,251.59亿元` / `-9.94` / `69.34%`。读者读到两个互相打架的
     单位说法。修法是**只改渲染**：`unit` 元数据一字不动（它参与身份与核对），读者面按封闭表
     译成中文；未登记的记号原样呈现（渲染层不发明单位）。
  2. **主体**：`主体：300750` —— 证券代码不是公司名称。读者面必须写成**可核实名称（主体标识）**；
     权威没有名称时不发明一个，退回主体标识（旧行为逐字保留）。
  3. **代理口径**：单元格里是 `-9.94。代理口径（PROXY_FINANCE_EXPENSES）`。限定语必须逐字留在
     单元格与 Claim 里（那是权威自己的披露，一个字都不能改），但只印一个内部原因码，读者读不出
     「这个数是怎么算出来的」。因此**另加一行中文口径说明**，措辞逐字取自权威自己的公式登记表。
  4. **来源**（`pwr-5`）：读者在表格里读到 `资产负债率`、在单元格里读到 `61.94%`，却读不到
     「这个数是什么口径、哪个期间、由谁算的」——正文里一个出处都没有。修法是**只改渲染**：
     来源由 Claim 自己的 `CitationRef`（`formula_id` / `formula_version` / `period`）与权威自己的
     公式登记表现场重建，`NarrativeSentence` / `NarrativeTable` 的 wire 一字未动；印不出中文
     来源的引用（`evidence` / `external` / 登记表查不到的公式）**整条不印**，不落内部记号。
  5. **缺口**：`（question_id=fin_statements_availability，topic=fin_source_scope）`、
     `（task=dtask_83efe8209c5b4efff222aeff）`、`reason_codes=['note_extraction_not_implemented']`
     —— 字段语法是内部 plumbing。修法是**标识保留、`key=` 语法不留**：id 是读者判读「这条缺口
     说的是什么事」的唯一主语（人读缺口一行只呈现 `[状态/原因码] + detail`），但读法不得随内部
     字段改名漂移。

**底层不美化**（本模块逐条反向证明）：类型化字段（`state` / `reason_code` / `topic_id` /
`question_id`）取值一字不改，`blocked` 不得变成别的词，`SECTION_BLOCKED` 不得为好看改绿。

公司无关：没有公司代号、文件名、页码、表号或固定年份的生产字面量（r7b 形状只作为**注释**里的
出处引用，不参与断言）。不调 LLM、不联网、不写库、不建第二套链。
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import test_demo_pack_writer as T
from evals import test_m930_3_financial_face as FF
from financial_v2 import formulas as FORMULAS
from harness import schema as HS
from planning import schema as PS
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import schema as SS

#: 夹具主体名称（**不是**生产字面量：任何可核实名称都应通过同一判据）。
FACE_NAME = "样本主体"
#: 夹具主体标识。
FACE_ID = "co-face-1"
#: 权威自己的代理口径限定语（由标记词常量拼出，不手抄一份——手抄就会与权威漂移）。
PROXY_CODE = "PROXY_FINANCE_EXPENSES"
PROXY_NOTE = f"{NS.PROXY_MARKER_PHRASE}（{PROXY_CODE}）"

#: 内部字段语法的通用形状：`<小写标识符>=`。缺口文案里**一个都不许有**——不管是
#: `task=` / `topic=` / `reason_codes=` 这些已知的，还是将来新加的。
_FIELD_SYNTAX_RE = re.compile(r"\b[a-z_][a-z0-9_]*=")

#: `SectionUnresolved` 的字段集（封闭）。读者面改造只能改 `detail` 的**措辞**，
#: 不得增删字段——加一个「给读者看的字段」就等于承认有两份缺口文本。
_UNRESOLVED_FIELDS = ("unresolved_id", "section_id", "topic_id", "question_id", "state",
                      "reason_code", "detail", "impact_scope", "blocking_effects",
                      "attempted_sources")


class _NS:
    """只读命名空间替身（渲染器只按属性名读取权威容器）。"""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def _scope_line(markdown: str) -> str:
    """渲染出的「主体 / 期间 / 单位」那一行（读者读到单位说法的唯一位置）。"""
    for line in markdown.splitlines():
        if line.startswith("主体："):
            return line
    return ""


def _reader_tables(*pairs, company_id: str = FACE_ID, company_name: str = FACE_NAME):
    """按 `(unit, 口径)` 分组构造财务表（复用 `test_m930_3_financial_face` 的**已验证**夹具）。"""
    facts = tuple(f for f, _c, _b in pairs)
    claims = tuple(c for _f, c, _b in pairs)
    bindings = tuple(b for _f, _c, b in pairs)
    # artifact 身份必须与 `FF._binding` 里登记的容器一致（表格行只能指向本节自己的事实）。
    authority = _NS(producer_kind="financial_workflow", company_id=company_id,
                    company_name=company_name,
                    artifact=_NS(artifact_id=FF.ARTIFACT_ID,
                                 periods=FF.DECLARED_PERIODS, facts=facts))
    tables = NS.build_metric_period_tables(
        section_id="financial", claims=claims, authority=authority,
        acceptance=_NS(accepted_bindings=bindings))
    return tables


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            details.append(f"FAIL {msg}")

    def expect_error(fn, exc, msg: str, *, needle: str = "") -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc as e:
            if needle and needle not in str(e):
                failed += 1
                details.append(f"FAIL {msg}（异常文本须含 {needle!r}；实际 {str(e)[:160]!r}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}（异常类型应为 {exc.__name__}；实际 "
                           f"{type(e).__name__}: {str(e)[:160]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}（没有抛异常）")

    # ==================================================================
    # 1. 单位：读者面译名与单元格显示一致，元数据一个字不改
    # ==================================================================
    # 真实 run 的量纲记号就是这三个（r7b 的 `单位：yuan / ratio / percent`）。
    check(tuple(NS.READER_UNIT_TEXTS) == ("yuan", "percent", "ratio"),
          f"读者面单位表是**封闭**表（增删记号必须是一次有意识的口径变更）；实为 "
          f"{tuple(NS.READER_UNIT_TEXTS)}")
    for token, text in NS.READER_UNIT_TEXTS.items():
        check(str(text).strip() and token not in str(text),
              f"单位 {token!r} 的读者说法必须是非空中文、且**不再出现内部记号**"
              f"（实为 {text!r}）")
    check("单元格" in NS.reader_unit_text("yuan")
          and "亿元" in NS.reader_unit_text("yuan")
          and "%" in NS.reader_unit_text("percent"),
          f"读者说法必须交代**单元格是怎么显示的**（否则读者仍要自己猜量级）；实为 "
          f"yuan={NS.reader_unit_text('yuan')!r} percent={NS.reader_unit_text('percent')!r}")
    check(NS.reader_unit_text("people") == "people"
          and NS.reader_unit_text("") == "" and NS.reader_unit_text(None) == "",
          "未登记的记号必须**原样呈现**：渲染层不得替权威发明一个单位，也不得凭记号外观猜量纲")
    check(NS.reader_unit_text("  yuan  ") == NS.READER_UNIT_TEXTS["yuan"],
          "记号先 strip 再查表（同一记号的不同空白写法必须译成同一句话）")

    yuan = FF._pair("INTEREST_BEARING_DEBT", "有息负债", period_basis=FF.PB.BASIS_END,
                    status="CALCULATED_EXACT", note="", reason_code=None,
                    values=("1,251.59亿元", "1,364.02亿元"), unit="yuan")
    ratio = FF._pair("SOLV_INTEREST_COVER", "利息保障倍数", period_basis=FF.PB.BASIS_FLOW,
                     status="CALCULATED_PROXY", note=PROXY_NOTE,
                     reason_code=PROXY_CODE, values=("3.42", "2.98"), unit="ratio")
    percent = FF._pair("DEBT_RATIO", "资产负债率", period_basis=FF.PB.BASIS_END,
                       status="CALCULATED_EXACT", note="", reason_code=None,
                       values=("69.34%", "65.24%"), unit="percent")
    tables = _reader_tables(*yuan, *ratio, *percent)
    check(len(tables) == 3,
          f"夹具前置：三个不同量纲必须是三张表（实为 {len(tables)} 张）")
    markdown = NS.render_paragraphs_markdown("财务", (), tables)
    scope = _scope_line(markdown)
    check(bool(scope), f"读者面必须渲染出「主体 / 期间 / 单位」那一行（实为 {markdown[:200]!r}）")
    check("单位：yuan" not in markdown and "单位：ratio" not in markdown
          and "单位：percent" not in markdown,
          f"读者面不得再印内部量纲记号（实为 {scope!r}）")
    check(NS.READER_UNIT_TEXTS["yuan"] in markdown
          and NS.READER_UNIT_TEXTS["percent"] in markdown,
          f"读者面的单位说法必须逐字取自封闭表（实为 {scope!r}）")
    # 「单位与单元格显示一致」：带数值的那一行里，单位说法与单元格自己的量级写法必须同向。
    yuan_scope = [line for line in markdown.splitlines()
                  if line.startswith("主体：") and "亿元" in line]
    check(bool(yuan_scope)
          and any("1,251.59亿元" in line for line in markdown.splitlines()),
          f"量纲 yuan 的表：单位说法必须提到单元格实际用的量级写法（亿元），"
          f"且单元格逐字保留（实为 {yuan_scope!r}）")
    check(any("69.34%" in line for line in markdown.splitlines()),
          "单元格逐字保留权威渲染（渲染层不得重算或舍入）")
    # 元数据不动：`unit` 仍是权威自己的记号，参与身份与核对。
    for table, token in zip(sorted(tables, key=lambda t: str(t.unit)), ("percent", "ratio", "yuan")):
        check(str(table.unit) == token and table.to_dict()["unit"] == token,
              f"表格元数据的 `unit` 必须逐字保留（渲染层不得把它改写成中文）；实为 "
              f"{table.unit!r} / {table.to_dict()['unit']!r}")

    # ==================================================================
    # 2. 主体：可核实名称 + 主体标识；没有名称时不发明
    # ==================================================================
    named = _reader_tables(*yuan, company_name=FACE_NAME)
    named_md = NS.render_paragraphs_markdown("财务", (), named)
    named_scope = _scope_line(named_md)
    check(str(named[0].entity_scope) == f"{FACE_NAME}（{FACE_ID}）"
          and FACE_NAME in named_scope and FACE_ID in named_scope,
          f"主体必须写成「可核实名称（主体标识）」：名称给读者、标识可回查（实为 "
          f"entity_scope={named[0].entity_scope!r} / 行={named_scope!r}）")
    check(f"主体：{FACE_ID}" not in named_md.replace(f"{FACE_NAME}（{FACE_ID}）", ""),
          f"有名称时不得只印主体标识（实为 {named_scope!r}）")
    nameless = _reader_tables(*yuan, company_name="")
    nameless_scope = _scope_line(NS.render_paragraphs_markdown("财务", (), nameless))
    check(str(nameless[0].entity_scope) == FACE_ID and FACE_ID in nameless_scope,
          f"权威没有名称时必须**退回主体标识**，而不是发明一个名称、也不是留空"
          f"（实为 {nameless[0].entity_scope!r} / 行={nameless_scope!r}）")
    expect_error(lambda: _reader_tables(*yuan, company_id=""),
                 NS.NarrativeSchemaError,
                 "主体标识为空时 fail-closed（不得靠名称顶替主体身份）",
                 needle="company_id")

    # ==================================================================
    # 3. 代理口径：单元格逐字不动，另加一行中文口径说明
    # ==================================================================
    check(NS.proxy_caliber_code(PROXY_NOTE) == PROXY_CODE
          and NS.proxy_caliber_code("3.42") == ""
          and NS.proxy_caliber_code(None) == "",
          "代理口径原因码只从权威自己的标记词形状里抽（没有标记词就是空串，不猜）")
    notes = NS.proxy_caliber_notes([PROXY_NOTE, "3.42", PROXY_NOTE])
    check(len(notes) == 1 and PROXY_CODE in notes[0],
          f"同一码只出一行说明（去重）；说明里必须点名原因码以便回查权威（实为 {notes!r}）")
    explanation = NS.proxy_caliber_explanation(PROXY_CODE)
    check("代理口径" in explanation and PROXY_CODE in explanation
          and re.search(r"[一-鿿]", explanation) is not None,
          f"口径说明必须是**中文**且保留原因码（实为 {explanation!r}）")
    fallback = NS.proxy_caliber_explanation("PROXY_NOT_REGISTERED")
    check("PROXY_NOT_REGISTERED" in fallback and "代理口径" in fallback,
          f"登记表里查不到该码时只陈述状态本身、**原样保留原因码**（不得编一个公式名）；"
          f"实为 {fallback!r}")
    check(NS.proxy_caliber_explanation("") == "",
          "没有码时不产出说明（不得留一行空的口径说明）")

    tables = _reader_tables(*yuan, *ratio, *percent)
    markdown = NS.render_paragraphs_markdown("财务", (), tables)
    note_lines = [line for line in markdown.splitlines() if line.startswith("- 口径说明：")]
    check(len(note_lines) == 1 and PROXY_CODE in note_lines[0],
          f"代理口径的表必须恰好带一行口径说明（精确口径的表不得带）；实为 {note_lines!r}")
    proxy_table = next(t for t in tables if str(t.unit) == "ratio")
    # 单元格 = 权威自己的渲染（`显示值。限定语`），逐字保留；口径说明是**另加**的一行，
    # 不是把限定语换掉，也不是替读者重算一个更「清楚」的数值。
    check([str(cell) for cell in proxy_table.rows[0].cells]
          == [f"{fact.display}。{PROXY_NOTE}" for fact, _c, _b in ratio],
          f"单元格里的权威限定语必须**逐字**保留（口径说明是**另加**的一行，不是替换）；"
          f"实为 {proxy_table.rows[0].cells!r}")
    check(all(not str(cell).strip() for t in tables if str(t.unit) != "ratio"
              for cell in [c for row in t.rows for c in row.cells]
              if NS.PROXY_MARKER_PHRASE in str(cell)),
          "精确口径的表不得出现口径限定语（不得凭空给精确值加口径）")

    # ==================================================================
    # 4. 缺口文案：**标识保留、`key=` 语法不留**
    # ==================================================================
    fin_task = T._task("financial", (T.TOPIC_FIN_SOLVENCY,), task_id="task-fin-reader-face")
    fin_artifact = T._Artifact(artifact_id="ffpa_reader_face", task_id=fin_task.task_id)
    fin_authority = T._financial_authority(
        fin_task, fin_artifact, note_gap=T._NoteGap(task_id=fin_task.task_id),
        company_name=FACE_NAME)

    financial_gap = PW._financial_gap_unresolved(
        "financial",
        {"fact_id": "f-reader-face", "label": "短期偿债能力", "formula_id": "FORM_READER_FACE",
         "status": "blocked", "reason_code": "source_missing"},
        fin_authority)
    note_gap = PW._note_gap_unresolved("financial", fin_authority)
    topic_gap = PW._topic_fact_gap_unresolved("financial", T.TOPIC_FIN_SOLVENCY)
    period_gap = PW._period_unresolved_gap("financial", T.TOPIC_FIN_SOLVENCY, ("f-a", "f-b"))
    outside_gap = PW._out_of_scope_facts_unresolved(
        "financial", T.TOPIC_FIN_SOLVENCY, ("f-x", "f-y", "f-z"))

    question = PS.PlannedQuestion(
        question_id="q-reader-face", question="读者面问题？", priority="required",
        topic_id=T.TOPIC_FIN_SOLVENCY, required_aspects=(), impact_scope=("subject",),
        blocking_policy=("SECTION_BLOCKED",))
    question_gap = PW._question_gap_unresolved("financial", question, T.TOPIC_FIN_SOLVENCY)

    # aspect 缺口走**真实扫描**：状态由权威侧的 aspect 结果决定，不由夹具另编一个。
    aspect_task = T._task("company", (T.TOPIC_BUSINESS,), task_id="task-aspect-reader-face")
    aspect_authority = T._company_authority(
        aspect_task, facts=(),
        materials=(T._material_of_identity("m-reader-face", "evidence:co-reader-face",
                                           text="该公司主营业务由动力电池构成。"),),
        aspects=(T._AspectResult(T.ASP_BUSINESS_MAIN, "blocked"),),
        requirements=(T._Req(T.TOPIC_BUSINESS, (
            T._aspect(T.ASP_BUSINESS_MAIN, T.TOPIC_BUSINESS,
                      T._question_id(T.TOPIC_BUSINESS)),)),))
    aspect_scan = PW.scan_topic_pack(aspect_authority, aspect_task)
    aspect_gap = PW._aspect_unresolved("company", T.ASP_BUSINESS_MAIN, aspect_scan, {})

    gaps = {"财务缺口": financial_gap, "附注缺口": note_gap, "主题无事实缺口": topic_gap,
            "期间未解决缺口": period_gap, "本节之外事实缺口": outside_gap,
            "问题无覆盖缺口": question_gap, "aspect 缺口": aspect_gap}
    for label, item in gaps.items():
        leaked = _FIELD_SYNTAX_RE.findall(item.detail)
        check(not leaked,
              f"{label}：读者面文案不得夹带内部字段语法（`key=`），实为 {leaked} :: "
              f"{item.detail!r}")
        check(isinstance(item, SS.SectionUnresolved)
              and {f.name for f in dataclasses.fields(item)} == set(_UNRESOLVED_FIELDS),
              f"{label}：缺口对象必须是同一个 `SectionUnresolved` 形状（字段集封闭，"
              f"不得为读者面另造类型或另加字段）；实为 "
              f"{sorted(f.name for f in dataclasses.fields(item))}")

    # 标识必须**在**文案里：它是这条缺口的主语（人读一行只有 `[状态/原因码] + detail`）。
    check(T.TOPIC_FIN_SOLVENCY in topic_gap.detail
          and T.TOPIC_FIN_SOLVENCY in period_gap.detail
          and T.TOPIC_FIN_SOLVENCY in outside_gap.detail,
          "主题类缺口必须点名**它自己的**主题（否则读者不知道这条缺口说的是哪个主题）")
    check("q-reader-face" in question_gap.detail and T.TOPIC_FIN_SOLVENCY in question_gap.detail,
          "问题类缺口必须点名问题与所属主题（自然措辞，不是字段语法）")
    check(T.ASP_BUSINESS_MAIN in aspect_gap.detail,
          "aspect 类缺口必须点名 aspect（读者据此回查权威侧的那条检索记录）")
    check("note_extraction_not_implemented" in note_gap.detail,
          f"附注缺口的原因码必须原样保留（不得为了让行文好看而删掉）；实为 {note_gap.detail!r}")
    check("dtask" not in note_gap.detail and fin_task.task_id not in note_gap.detail,
          f"附注缺口不得把**内部任务 id** 印进正文（它对读者没有任何含义；缺口登记在哪个"
          f"主题由记录自己的 `topic_id` 承担）；实为 {note_gap.detail!r}")

    # 内部 plumbing 的判据仍是类型化字段，不在文案里（资格决定不许塞进 `detail`）。
    check(financial_gap.topic_id == T.TOPIC_FIN_SOLVENCY
          and financial_gap.reason_code == "source_missing"
          and financial_gap.state == "NOT_PROVIDED",
          f"财务缺口：类型化字段必须原样携带权威的原因码与规范化状态（实为 "
          f"topic={financial_gap.topic_id!r} reason={financial_gap.reason_code!r} "
          f"state={financial_gap.state!r}）")
    check("FORM_READER_FACE" in financial_gap.detail
          and "f-reader-face" in financial_gap.detail
          or "短期偿债能力" in financial_gap.detail,
          f"财务缺口必须让读者认出**哪个指标**（公式 id 与指标名都在权威侧有登记）；"
          f"实为 {financial_gap.detail!r}")
    check(note_gap.state == "NOT_PROVIDED" and note_gap.reason_code == "blocked",
          f"附注缺口状态一字不改（`blocked` 不得被美化成别的词）；实为 "
          f"{note_gap.state!r} / {note_gap.reason_code!r}")
    check(period_gap.reason_code == "period_unresolved"
          and topic_gap.reason_code == "unresolved"
          and outside_gap.reason_code == "unresolved",
          "各缺口的原因码必须按各自口径给出（不得统一成一个好看的词）")
    check(aspect_gap.state == "NOT_PROVIDED" and aspect_gap.reason_code == "blocked"
          and aspect_gap.detail.startswith(f"aspect「{T.ASP_BUSINESS_MAIN}」"),
          f"aspect 缺口：`blocked` 原样映射成 `NOT_PROVIDED` + 原因码 `blocked`，"
          f"且文案以 aspect 自己开头（实为 {aspect_gap.state!r} / "
          f"{aspect_gap.reason_code!r} / {aspect_gap.detail!r}）")
    # `SECTION_BLOCKED` 不得为好看而改绿。
    check(question_gap.blocking_effects == ("SECTION_BLOCKED",)
          and question_gap.state == "NOT_PROVIDED",
          f"问题声明阻断时后果字段必须如实落成 `SECTION_BLOCKED`（不得被静默降级）；实为 "
          f"{question_gap.blocking_effects!r}")

    # 人读渲染：标签逐字，正文不带字段语法。
    appendix = NS.render_unresolved_appendix(tuple(gaps.values()), {})
    check(appendix.startswith(NS.UNRESOLVED_APPENDIX_HEADING),
          f"缺口附录必须用共享标题（避免出现两份措辞）；实为 {appendix.splitlines()[0]!r}")
    check(not _FIELD_SYNTAX_RE.findall(appendix),
          f"渲染出的缺口附录不得含 `key=` 字段语法（实为 "
          f"{_FIELD_SYNTAX_RE.findall(appendix)}）")
    check(NS._gap_line("NOT_PROVIDED", "blocked", "正文", "blocked")
          == "- [blocked/NOT_PROVIDED/blocked] 正文",
          "缺口行格式= `[权威原始状态/状态/原因码] + 正文`：标签**逐字**保留权威取值")
    check(NS._gap_line("NOT_PROVIDED", "blocked", "正文", "")
          == "- [NOT_PROVIDED/blocked] 正文",
          "权威没有原始状态时如实省略（不用占位符假装有值）")
    projections = [{"state": i.state, "reason_code": i.reason_code, "detail": i.detail,
                    "authority_status": ""} for i in gaps.values()]
    check(NS._unresolved_appendix_from_projections(projections) == appendix,
          "正文侧与组装侧必须渲染出**逐字节相同**的缺口附录（两侧共用 `_gap_line`）")

    # 反向证明：这套判据真的能抓到字段语法（否则上面全是恒真）。
    check(_FIELD_SYNTAX_RE.findall("本节问题（question_id=q1，topic=t1）")
          == ["question_id=", "topic="]
          and _FIELD_SYNTAX_RE.findall("reason_codes=['a']") == ["reason_codes="]
          and _FIELD_SYNTAX_RE.findall("本节主题「t1」的原因码：blocked") == [],
          "反例前置：判据必须抓得住 `question_id=` / `topic=` / `reason_codes=`，"
          "且不误伤自然措辞")
    # 真实的 r7b 形状（逐字取自 `manual_review.md` 的旧文案）必须被判据判红。
    _r7b_old = ("本节问题（question_id=fin_statements_availability，topic=fin_source_scope）"
                "在本轮权威输入中没有产出任何可写进正文的 Claim")
    check(len(_FIELD_SYNTAX_RE.findall(_r7b_old)) == 2,
          "r7b 的旧缺口文案必须被本判据判红（否则这次返修没有可核验的对照）")

    # ==================================================================
    # 5. 跨层：缺口身份随文案变化 ⇒ 必须被如实登记（不得假装没变）
    # ==================================================================
    # `derive_unresolved_id` 把 `detail` 计入身份。读者面改写**必然**改变受影响缺口的
    # `unresolved_id`，这是设计耦合而不是缺陷；本模块把它钉成一条**显式**事实，
    # 免得有人日后以为「改写文案不影响任何身份」。
    base = PW.derive_unresolved_id("financial", T.TOPIC_FIN_SOLVENCY, "q1", "NOT_PROVIDED",
                                   "unresolved", "正文甲")
    check(base != PW.derive_unresolved_id("financial", T.TOPIC_FIN_SOLVENCY, "q1",
                                          "NOT_PROVIDED", "unresolved", "正文乙")
          and base == PW.derive_unresolved_id("financial", T.TOPIC_FIN_SOLVENCY, "q1",
                                              "NOT_PROVIDED", "unresolved", "正文甲"),
          "缺口 id 是 `(section, topic, question, state, reason_code, detail)` 的内容身份："
          "同一输入必复算、任一输入变则必变（读者面改写与身份变化是同一件事，必须一起报告）")

    # ==================================================================
    # 6. 来源（`pwr-5`）：读者必须读到「什么口径、哪个期间、谁算的」
    # ==================================================================
    # 本节的「中文名 / 中文期间」判据**全部以权威自己的公式登记表为对照**，不手抄任何中文
    # 指标名：手抄一份就会与权威漂移，而漂移的方向恰好是「渲染层自己发明一个口径名」。
    registry = FORMULAS.build_registry()
    end_id = next(fid for fid, f in sorted(registry.items())
                  if f.period_requirement == FF.PB.BASIS_END and f.name)
    flow_id = next(fid for fid, f in sorted(registry.items())
                   if f.period_requirement == FF.PB.BASIS_FLOW and f.name)

    def _structured_claim(formula_id: str, period: str, *, version: str = "1.0"):
        """一条只带结构化引用的 Claim（真实链的财务 Claim 就是这个形状）。"""
        ref = HS.CitationRef(ref_type="structured", snapshot_id="snap-face",
                             item_code="ITEM_FACE", formula_id=formula_id,
                             formula_version=version, period=period)
        # 夹具文本里**不得**出现公式 id：否则「正文里出现公式 id」这条判据会被夹具自己判红，
        # 而真正的判据对象是**来源行**（`INTEREST_BEARING_DEBT` 那次就是这么被夹具误伤的）。
        text = "（夹具）该指标在报告期末的值为 1"
        claim_id = SS.derive_claim_id("fact", T.TOPIC_FIN_SOLVENCY, ("q1",), text, (ref,),
                                      "ccand_src_face", "dr-1", ("asb_src_face",))
        claim = SS.SectionClaim(
            claim_id=claim_id, schema_version=SS.CLAIM_SCHEMA_VERSION, section_id="financial",
            topic_id=T.TOPIC_FIN_SOLVENCY, question_ids=("q1",), text=text, claim_type="fact",
            citation_refs=(ref,), claim_candidate_id="ccand_src_face",
            claim_candidate_revision="dr-1", accepted_binding_ids=("asb_src_face",))
        return claim, ref

    def _paragraph_of(claim):
        return NS.NarrativeParagraph.create(
            section_id="financial", topic_ids=(T.TOPIC_FIN_SOLVENCY,), index=0,
            sentence_specs=[{"text": claim.text, "sentence_kind": "factual",
                             "claim_ids": (claim.claim_id,),
                             "citation_ids": NS.claim_citation_ids(claim)}])

    # --- 6.1 索引的键就是正文里那批 citation_id（同一个派生实现，不另建一套） ----
    src_claim, src_ref = _structured_claim(end_id, "2025-12-31")
    index = NS.citation_source_index((src_claim,))
    check(set(index) == set(NS.claim_citation_ids(src_claim)),
          f"来源索引的键必须**恰好**是 Claim 自己派生出的 citation_id 集合"
          f"（索引从别处派生 ⇒ 覆盖检查会变成恒假/恒真）；实为 {sorted(index)} vs "
          f"{sorted(NS.claim_citation_ids(src_claim))}")

    # --- 6.2 正向：口径名取自登记表、期间按权威口径译成中文 ---------------------
    parsed = NS.citation_ref_source(src_ref)
    check(parsed is not None and parsed[0] == f"{registry[end_id].name}@1.0",
          f"结构化引用的口径名必须逐字取自权威的公式登记表（渲染层不发明名字）；实为 {parsed!r} / "
          f"登记表 = {registry[end_id].name!r}")
    check(parsed is not None
          and parsed[1] == FF.PB.period_expression("2025-12-31", FF.PB.BASIS_END),
          f"期间必须译成权威口径下的中文表达；实为 {parsed!r}")
    # 口径是**真的**参与了表达（而不是把期间记号原样回显）：同一个期间记号在时点量与时流量下
    # 必须渲染成两句话，否则「口径」二字就只是注释。
    _, flow_ref = _structured_claim(flow_id, "2025-12-31")
    flow_note = NS.citation_ref_source(flow_ref)
    check(flow_note is not None
          and flow_note[1] == FF.PB.period_expression("2025-12-31", FF.PB.BASIS_FLOW)
          and flow_note[1] != FF.PB.period_expression("2025-12-31", FF.PB.BASIS_END),
          f"同一个期间记号在「时点量」与「时流量」下必须渲染成不同的中文期间"
          f"（否则口径没有真正参与读者面）；实为 {flow_note!r} / "
          f"end={FF.PB.period_expression('2025-12-31', FF.PB.BASIS_END)!r}")

    # --- 6.3 正向：正文里真的印出这一行，且不落任何内部记号 -------------------
    paragraph = _paragraph_of(src_claim)
    src_md = NS.render_paragraphs_markdown("财务", (paragraph,), (), citation_sources=index)
    src_lines = [line for line in src_md.splitlines() if line.startswith("- 来源：")]
    check(len(src_lines) == 1,
          f"有中文来源的引用必须印出**恰好一行** `- 来源：`；实为 {src_lines!r}（全文 "
          f"{src_md!r}）")
    check(registry[end_id].name in src_lines[0]
          and FF.PB.period_expression("2025-12-31", FF.PB.BASIS_END) in src_lines[0],
          f"来源行必须同时含口径名与中文期间；实为 {src_lines[0]!r}")
    leaked = [t for t in ("cite_", "structured:", "snap-face", "ITEM_FACE", end_id,
                          "ntab_", "npar_", "nsec_") if t in src_md]
    check(not leaked,
          f"来源行不得把内部记号（引用 id / 身份串 / 公式 id / 快照 id）印给读者；"
          f"实为 {leaked} :: {src_md!r}")
    # 同一份输入两次渲染必须逐字节相同（正文指纹可复算的前提）。
    check(NS.render_paragraphs_markdown("财务", (paragraph,), (),
                                        citation_sources=index) == src_md,
          "来源行必须由同一份索引确定性渲染（同输入两次渲染逐字节相同）")
    # 索引取**更大**的 Claim 集（例如整节含表格行的 Claim 集）时，正文引用到的那些来源不变。
    # 正式链的两个入口（写作器与组装器）用的正是各自手里的 Claim 集，这条保证它们不漂移。
    other_claim, _other_ref = _structured_claim(flow_id, "2024-12-31")
    check(NS.render_paragraphs_markdown(
        "财务", (paragraph,), (),
        citation_sources=NS.citation_source_index((src_claim, other_claim))) == src_md,
          "来源索引由**更大的** Claim 集重建时，正文引用到的来源必须一字不变"
          "（否则写作器与组装器各拿一份 Claim 集就会渲染出两份正文）")

    # --- 6.4 反向：印不出中文来源的引用整条不印，但仍然**在**索引里 -----------
    ev_claim = FF._claim("本期有息负债规模较上年有所上升", binding_id="asb_ev_face")
    ev_index = NS.citation_source_index((ev_claim,))
    check(set(ev_index) == set(NS.claim_citation_ids(ev_claim))
          and all(v is None for v in ev_index.values()),
          f"`evidence` 引用不得有中文来源，但必须**进索引**（值为 None）——"
          f"「不印」与「漏给」是两件事，覆盖检查只拦后者；实为 {ev_index!r}")
    ev_md = NS.render_paragraphs_markdown("财务", (_paragraph_of(ev_claim),), (),
                                          citation_sources=ev_index)
    check(not [line for line in ev_md.splitlines() if line.startswith("- 来源：")],
          f"印不出中文来源时不得留一行空的来源（也不得印内部记号）；实为 {ev_md!r}")
    check(NS.citation_ref_source(
        HS.CitationRef(ref_type="external", source_snapshot_id="ext-1")) is None
        and NS.citation_ref_source(
            HS.CitationRef(ref_type="structured", snapshot_id="s", formula_id="",
                           period="2025-12-31")) is None,
          "`external` 引用与没有 formula_id 的结构化引用都没有中文来源（返回 None）")
    # 登记表里查不到的公式：不得编一个中文名，也不得退回公式 id 当名字。
    unknown_ref = HS.CitationRef(ref_type="structured", snapshot_id="s",
                                 formula_id="NO_SUCH_FORMULA_FACE", formula_version="9",
                                 period="2025-12-31")
    check(NS.citation_ref_source(unknown_ref) is None,
          f"权威登记表里没有的公式必须**不印**（渲染层不得自造一个口径名，也不得把 id 当名字）；"
          f"实为 {NS.citation_ref_source(unknown_ref)!r}")
    # 引用自带期间为空时不发明期间：只印口径名，不补一个期间。
    bare = NS.citation_ref_source(HS.CitationRef(
        ref_type="structured", snapshot_id="s", formula_id=end_id,
        formula_version="1.0", period=""))
    check(bare is not None and bare[0] == f"{registry[end_id].name}@1.0" and bare[1] == "",
          f"引用没带期间时只印口径名，不补期间；实为 {bare!r}")

    # --- 6.5 反向：部分覆盖 / 忘了给索引，都必须 fail-closed -------------------
    expect_error(lambda: NS.render_paragraphs_markdown("财务", (paragraph,), (),
                                                       citation_sources={}),
                 NS.NarrativeSchemaError,
                 "来源索引**部分覆盖**正文引用时必须 fail-closed（否则读者读到的来源行会"
                 "冒充「本节全部来源」）", needle="未覆盖")
    narrative = NS.SectionNarrative.create(
        task_id="task-1", section_id="financial", section_draft_id="sdraft-1",
        draft_revision="dr-1", paragraphs=(paragraph,), tables=())
    expect_error(lambda: NS.render_final_narrative_markdown(
        "财务", narrative, (), citation_sources=None),
        NS.NarrativeSchemaError,
        "正式章节正文入口**不给**来源索引时必须 fail-closed（正式链不得有「忘了给来源」"
        "的静默走法）", needle="来源索引")
    check(NS.render_final_narrative_markdown("财务", narrative, (),
                                             citation_sources=index) == src_md,
          "正式章节正文入口与渲染原语必须产出**逐字节相同**的正文（本节没有缺口投影时，"
          "两者只差缺口附录；来源行不是额外的一层）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"passed={result['passed']} failed={result['failed']} skipped={result['skipped']}")
    sys.exit(1 if result["failed"] else 0)

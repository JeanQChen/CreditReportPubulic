"""M930-3 返修 P3 反例集：**财务读者可见面**（期间口径 / 期间表达 / 代理口径披露）。

返修计划 §5 的两行必须各有正反两面，且**拒的理由必须与读者看到的缺陷同一处**：

  12 | 利息保障倍数（`CALCULATED_PROXY`）出现在表格 | 读者在 Markdown/表格可见位置读到
     「代理口径」及适用期间；
  13 | flow 指标（如利息保障倍数）呈现期间 | 表达式不得是裸期间末日。

为什么单独立一个模块：这两条都能被既有的「单元格是配对 Claim 的逐字子串」判据**放过** ——
`1,251.59亿元` 确实是 `…为1,251.59亿元。代理口径（…）。` 的子串，`2025-12-31` 也确实出现在
Claim 文本里。也就是说旧判据全绿而读者仍然会把「代理口径的倍数」读成受审的精确值、把「本年度的
利息保障倍数」读成「2025-12-31 这一时点的量」（那一刻并不存在这个量）。因此本模块逐条钉住：

  1. `financial_v2.period_basis`：口径只由权威自己的 `statement_type` / `period_requirement` 决定
     （未知类型 fail-closed，不给默认方向）；期间表达只由期间末日所在年/月与口径决定，且**绝不**
     等于裸期间末日；非日期形状的记号逐字保留（不替权威发明措辞）；
  2. `narrative_schema.financial_table_cell` / `proxy_qualifier`：代理事实的**表格单元格**必须自带
     口径限定语，且仍是权威表面的一段连续子串（于是它不可能出现在 Claim 没承担的披露里）；
  3. 门后构造器 `build_metric_period_tables`：列名写期间表达、表按 `(unit, 口径)` 成组（同一列名下
     不得同时有时点量与期间量）、日期形状的期间却没声明口径 → fail-closed；表题由该表自己的
     `(unit, 口径)` 算出（`financial_table_caption`），四张展示表因此**逐表可辨** —— 旧表题是
     一个固定文案，四张表逐字重名，读者只能靠下一行小字回推这张表是哪一种口径（§4）；
  4. **读者实际读到的**那一步：渲染出的 Markdown **表格行**里必须能读到限定语 —— 只藏在正文 Claim
     或 `claim_id` 链里不算披露。配一份手工造的「裸值表格」，证明本模块的可见性判据确实能区分
     两者，而不是永远为真；
  5. 验收侧 `run_m930_3_acceptance` 对同一批产物**独立重算**（不 import 生产侧的常数/函数）：列名与
     单元格都要被判红 —— 门与读者看到的是同一件事；
  6. **跨层一致性**（C4 落在验收侧，第 7 节）：wire 门在 C4 之后按**分量**核 Claim 文本，而验收侧
     的 A2 曾仍要求「整格文本是配对 Claim 的连续子串」—— 权威渲染用「。」拼接数值与限定语、写作
     侧又被禁止在 `claim_text` 中间写句末标点，两条要求互斥，于是 r7b 真实 run 的 4 个代理单元格
     只因一个标点差异被整体作废。本模块用 r7b 的实际形状（`…为-9.94，代理口径（…）` 对
     `-9.94。代理口径（…）`）钉住两侧**同口径**：正例必须两侧同时放行（并附「整格确非连续子串」
     的非平凡前提），五种坏法（数值 / 期间表达 / 限定语 / 裸值单元格 / 错配 Claim）必须各自判红。

跑法（无管道/无重定向）：`PYTHONIOENCODING=utf-8 python -m evals.test_m930_3_financial_face`
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import run_m930_3_acceptance as ACC
from financial_v2 import formulas as FFORMULAS
from financial_v2 import period_basis as PB
from harness import schema as HS
from sections import narrative_schema as NS
from sections import schema as SS

#: 夹具使用的 topic（「同一主题」判据要求它稳定，与验收夹具同源）。
TOPIC = "financial_metrics"
#: 夹具主体：只作为 `entity_scope` 的取值，不参与任何判定。
ENTITY = "co-1"
#: 夹具 artifact 身份。
ARTIFACT_ID = "ffpa_fixture"
#: 权威声明的期间顺序（**身份用**的期间记号，不改写；与真实现场同为 `YYYY-MM-DD`）。
DECLARED_PERIODS = ("2024-12-31", "2025-12-31")


class _NS:
    """只读命名空间替身（判据与渲染器只按属性名读取产物容器）。"""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def _citation(evidence_id: str = "ev1") -> HS.CitationRef:
    return HS.CitationRef(ref_type="evidence", evidence_id=evidence_id, page_number=None)


def _claim(text: str, *, binding_id: str) -> SS.SectionClaim:
    refs = (_citation(),)
    bindings = (binding_id,)
    claim_id = SS.derive_claim_id("fact", TOPIC, ("q1",), text, refs,
                                  f"ccand_{binding_id}", "dr-1", bindings)
    return SS.SectionClaim(claim_id=claim_id, schema_version=SS.CLAIM_SCHEMA_VERSION,
                           section_id="financial", topic_id=TOPIC, question_ids=("q1",),
                           text=text, claim_type="fact", citation_refs=refs,
                           claim_candidate_id=f"ccand_{binding_id}",
                           claim_candidate_revision="dr-1",
                           accepted_binding_ids=bindings)


def _binding(binding_id: str, fact_id: str) -> _NS:
    return _NS(accepted_support_binding_id=binding_id, authority_kind="financial_pack",
               authority_container_id=ARTIFACT_ID, material_id=None,
               support_semantics="factual", authorization_path="path_a_prevalidated",
               source_identity="src1", payload_ref=None, locator_ref=None,
               fact_id=None, financial_fact_id=fact_id, note_fact_id=None,
               external_fact_id=None, binding_decision_id="cbd-1",
               entailment_decision_id="ced-1")


def _disposition(claim_id: str) -> _NS:
    return NS.ClaimNarrativeDisposition.create(
        section_id="financial", draft_revision="dr-1", claim_id=claim_id,
        disposition="omitted", reason_code="presented_as_table_row")


def _fact(fact_id: str, *, code: str, label: str, period: str, display: str,
          period_basis: str | None, status: str = "CALCULATED_EXACT",
          note: str = "", reason_code: str | None = None, unit: str = "元") -> _NS:
    """一条权威财务事实的替身（形状对齐 `ffpa-2` 投影：事实自己携带口径与期间表达）。

    `period_basis=None` 表示**上游没有声明口径**（用于反例：日期形状的期间没有口径时必须 fail-closed）。
    `period_label` 由 `period_basis` 模块确定性派生 —— 夹具不自己编一个表达。
    """
    basis = "" if period_basis is None else period_basis
    label_text = PB.period_expression(period, basis) if basis in PB.PERIOD_BASES else period
    return _NS(fact_id=fact_id, kind="calculation", code=code, label=label, period=period,
               period_basis=basis, period_label=label_text, display=display,
               value_text=display, unit=unit, status=status, note=note,
               reason_code=reason_code, citation={"ref_type": "structured",
                                                 "snapshot_id": "snap-1", "period": period},
               text="")


def _authority(facts) -> _NS:
    return _NS(producer_kind="financial_workflow", company_id=ENTITY,
               artifact=_NS(artifact_id=ARTIFACT_ID, periods=DECLARED_PERIODS,
                            facts=tuple(facts)))


def _section(claims, bindings, tables) -> _NS:
    narrative = NS.SectionNarrative.create(
        task_id="task-1", section_id="financial", section_draft_id="sdraft-1",
        draft_revision="dr-1", paragraphs=(), tables=tuple(tables))
    return _NS(section_id="financial", claims=tuple(claims), narrative=narrative,
               acceptance=_NS(accepted_bindings=tuple(bindings)),
               claim_narrative_dispositions=tuple(_disposition(c.claim_id) for c in claims),
               aggregate_decisions=(), entailment_decisions=())


def _claim_for_fact(fact, binding_id: str) -> SS.SectionClaim:
    """按**权威表面自己的文本**造 Claim：去掉末尾句读后逐字照抄（写入侧的同一纪律）。"""
    surface = NS.authoritative_fact_surface("financial_pack", fact)
    return _claim(surface.rstrip(NS.SENTENCE_TERMINATORS).strip(), binding_id=binding_id)


def _pair(code: str, label: str, *, period_basis, status: str, note: str,
          reason_code: str | None, values: tuple[str, str], unit: str = "元"):
    """同一指标 × 两个声明期间的（事实, Claim, 边）三元组。"""
    out = []
    for period, display in zip(DECLARED_PERIODS, values):
        fact_id = f"metric_{code}_{period}"
        fact = _fact(fact_id, code=code, label=label, period=period, display=display,
                     period_basis=period_basis, status=status, note=note,
                     reason_code=reason_code, unit=unit)
        binding_id = f"asb_{code}_{period[:4]}"
        claim = _claim_for_fact(fact, binding_id)
        out.append((fact, claim, _binding(binding_id, fact_id)))
    return out


def _table_lines(markdown: str) -> list[str]:
    """Markdown 里**表格行**（渲染器的表行一律以 `|` 开头，正文句不以 `|` 开头）。"""
    return [line for line in markdown.splitlines() if line.startswith("|")]


def _table_blocks(markdown: str) -> list[list[str]]:
    """把 Markdown 切成**表块**：连续的表行是一张表（表头行 + 分隔行 + 数据行）。

    「同一张表」是读者实际比较两类信息的单位：口径限定语在数据行、适用期间在表头行，
    读者看的是同一张表。跨表块的两个信息不算「写在一起」。
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in markdown.splitlines():
        if line.startswith("|"):
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _qualifier_visible_in_table(markdown: str, qualifier: str) -> bool:
    """读者能不能**在表格里**读到该口径限定语（本模块唯一的可见性判据）。"""
    return any(qualifier in line for line in _table_lines(markdown))


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

    # ------------------------------------------------------------------
    # 1. 口径只由权威自己的字段决定（`financial_v2.period_basis`，唯一实现）
    # ------------------------------------------------------------------
    check(PB.basis_from_statement_type("balance_sheet") == PB.BASIS_END,
          "口径：资产负债表科目是时点余额（end）")
    for statement in ("income_statement", "cash_flow"):
        check(PB.basis_from_statement_type(statement) == PB.BASIS_FLOW,
              f"口径：{statement} 是期间流量（flow）")
    expect_error(lambda: PB.basis_from_statement_type("statement_of_changes"),
                 PB.UnsupportedStatementType,
                 "口径：未登记的报表类型必须 fail-closed（不给它默认一个方向）",
                 needle="未登记的报表类型")

    check(PB.basis_from_period_requirement("end") == PB.BASIS_END,
          "口径：公式定义的 `end` 是唯一的时点量")
    for requirement in ("flow", "flow/end", "flow/avg", "yoy_flow", "yoy_end"):
        check(PB.basis_from_period_requirement(requirement) == PB.BASIS_FLOW,
              f"口径：`{requirement}` 的呈现期间是**期间**（分子是流量或表达期间内变化）")
    expect_error(lambda: PB.basis_from_period_requirement(""),
                 ValueError, "口径：空的 `period_requirement` 不得被当成时点量")

    # 真实公式定义：计划 §5 第 12/13 行点名的就是这一条。
    registry = FFORMULAS.build_registry()
    check("SOLV_INTEREST_COVER" in registry,
          "夹具前提：公式定义里有 SOLV_INTEREST_COVER（利息保障倍数）")
    if "SOLV_INTEREST_COVER" in registry:
        requirement = str(registry["SOLV_INTEREST_COVER"].period_requirement)
        check(PB.basis_from_period_requirement(requirement) == PB.BASIS_FLOW,
              f"利息保障倍数的期间要求是 {requirement!r} ⇒ 它是**期间量**，"
              "不得挂在时点列名下")
        check(PB.period_expression("2025-12-31", PB.BASIS_FLOW) == "2025年度",
              "期间表达：flow 的 12 月期末写成 `2025年度`（不是 `2025-12-31`）")
    # 对照组：真正的时点量指标（下方夹具用的就是这两条定义的身份码）。
    for formula_id in ("INTEREST_BEARING_DEBT", "SOLV_DEBT_RATIO"):
        check(formula_id in registry,
              f"夹具前提：公式定义里有 {formula_id}")
        if formula_id in registry:
            check(str(registry[formula_id].period_requirement) == "end",
                  f"{formula_id} 的期间要求是 end ⇒ 它是**时点量**，表达应为 `年末`")

    # ------------------------------------------------------------------
    # 2. 期间表达：规则只由期间末日所在年/月与口径决定，且绝不等同裸期间末日
    # ------------------------------------------------------------------
    end_expected = {"2025-12-31": "2025年末", "2025-09-30": "2025年三季度末",
                    "2025-06-30": "2025年半年末", "2025-03-31": "2025年一季度末",
                    "2025-05-31": "2025年5月末"}
    flow_expected = {"2025-12-31": "2025年度", "2025-09-30": "2025年前三季度",
                     "2025-06-30": "2025年半年度", "2025-03-31": "2025年一季度",
                     "2025-05-31": "2025年1-5月"}
    for period, expected in end_expected.items():
        check(PB.period_expression(period, PB.BASIS_END) == expected,
              f"期间表达：end + {period} ⇒ {expected}")
    for period, expected in flow_expected.items():
        check(PB.period_expression(period, PB.BASIS_FLOW) == expected,
              f"期间表达：flow + {period} ⇒ {expected}")
    for period in flow_expected:
        check(PB.period_expression(period, PB.BASIS_FLOW) != period,
              f"期间表达：flow 的 {period} 不得以裸期间末日呈现（读者会读成时点值）")
        check(PB.period_expression(period, PB.BASIS_END) != period,
              f"期间表达：end 的 {period} 同样不得以裸期间末日呈现")
        check(PB.period_expression(period, PB.BASIS_FLOW)
              != PB.period_expression(period, PB.BASIS_END),
              f"期间表达：同一期间记号在两种口径下必须是两种说法（{period}）")

    # 非日期形状逐字保留：本模块不替权威发明期间措辞。
    for token in ("2024年", "2024", "2024-13-01", "不适用", ""):
        check(PB.period_expression(token, PB.BASIS_FLOW) == token,
              f"期间表达：非权威日期形状的记号 {token!r} 必须逐字保留")
        check(PB.is_authority_period_token(token) is False,
              f"期间记号：{token!r} 不是权威日期形状")
    check(PB.is_authority_period_token("2025-12-31") is True,
          "期间记号：`YYYY-MM-DD` 是权威日期形状（因此它必须带口径）")
    expect_error(lambda: PB.period_expression("2025-12-31", "quarterly"),
                 ValueError, "期间表达：未知口径不得被静默当成某个方向")

    # ------------------------------------------------------------------
    # 3. 代理口径的**读者可见**披露（单元格，不只是 Claim 文本）
    # ------------------------------------------------------------------
    proxy_note = "代理口径（PROXY_FINANCE_EXPENSES）"
    exact_fact = _fact("metric_X_1", code="TOTAL_ASSETS", label="总资产",
                       period="2025-12-31", display="1,000", period_basis=PB.BASIS_END)
    proxy_fact = _fact("metric_Y_1", code="SOLV_INTEREST_COVER", label="利息保障倍数",
                       period="2025-12-31", display="3.42", period_basis=PB.BASIS_FLOW,
                       status="CALCULATED_PROXY", note=proxy_note,
                       reason_code="PROXY_FINANCE_EXPENSES")
    check(NS.is_proxy_fact(proxy_fact) and not NS.is_proxy_fact(exact_fact),
          "夹具前提：代理与精确事实必须能被 `status` 区分（不做推断）")
    check(NS.proxy_qualifier(exact_fact) == "",
          "披露：精确口径的事实不得被加上任何口径限定语")
    check(NS.proxy_qualifier(proxy_fact) == proxy_note,
          "披露：限定语逐字取权威自己的 `note`")
    check(NS.financial_table_cell(exact_fact) == "1,000",
          "披露：精确口径的单元格就是权威数值渲染")
    check(NS.financial_table_cell(proxy_fact) == f"3.42。{proxy_note}",
          "披露：代理口径的单元格必须自己写出口径（不只是数值）")
    for fact in (exact_fact, proxy_fact):
        surface = NS.authoritative_fact_surface("financial_pack", fact)
        check(NS.financial_table_cell(fact) in surface,
              f"披露：单元格文本必须是权威表面的一段连续子串（{fact.fact_id}）")
    # 权威没有给出 note 时退到标记词本身（仍须可见）。
    bare_proxy = _fact("metric_Z_1", code="SOLV_INTEREST_COVER", label="利息保障倍数",
                       period="2025-12-31", display="3.42", period_basis=PB.BASIS_FLOW,
                       status="CALCULATED_PROXY")
    check(NS.financial_table_cell(bare_proxy) == f"3.42。{NS.PROXY_MARKER_PHRASE}",
          "披露：权威没有给出限定语文本时，单元格至少要有标记词（不得留一个裸数值）")

    # ------------------------------------------------------------------
    # 4. 门后构造器：期间表达上列名、表按 (unit, 口径) 成组
    # ------------------------------------------------------------------
    # 三个指标：一个代理口径的期间量（计划 §5 第 12/13 行点名的利息保障倍数）、一个精确口径的
    # 期间量（EBITDA）与一个精确口径的时点量（有息负债）。后两者**同量纲**（元）—— 于是「拆成
    # 两张表」的唯一理由只能是**口径不同**，不可能是量纲不同。
    cover = _pair("SOLV_INTEREST_COVER", "利息保障倍数", period_basis=PB.BASIS_FLOW,
                  status="CALCULATED_PROXY", note=proxy_note,
                  reason_code="PROXY_FINANCE_EXPENSES", values=("3.42", "2.98"), unit="倍")
    ebitda = _pair("EBITDA", "EBITDA", period_basis=PB.BASIS_FLOW,
                   status="CALCULATED_EXACT", note="", reason_code=None,
                   values=("1,000", "900"), unit="元")
    debt = _pair("INTEREST_BEARING_DEBT", "有息负债", period_basis=PB.BASIS_END,
                 status="CALCULATED_EXACT", note="", reason_code=None,
                 values=("2,000", "1,800"), unit="元")
    facts = tuple(f for f, _c, _b in (*cover, *ebitda, *debt))
    claims = tuple(c for _f, c, _b in (*cover, *ebitda, *debt))
    bindings = tuple(b for _f, _c, b in (*cover, *ebitda, *debt))
    authority = _authority(facts)

    tables = NS.build_metric_period_tables(section_id="financial", claims=claims,
                                           authority=authority,
                                           acceptance=_NS(accepted_bindings=bindings))
    flow_header = (NS.FINANCIAL_TABLE_LABEL_HEADER, "2024年度", "2025年度")
    end_header = (NS.FINANCIAL_TABLE_LABEL_HEADER, "2024年末", "2025年末")

    def _table_for(unit: str, header: tuple):
        for table in tables:
            if str(table.unit) == unit and tuple(str(h) for h in table.header) == header:
                return table
        return None

    check(len(tables) == 3,
          f"口径分组：期间量（倍/元）与时点量（元）必须分成三张表（实际 {len(tables)} 张）")
    cover_table = _table_for("倍", flow_header)
    ebitda_table = _table_for("元", flow_header)
    debt_table = _table_for("元", end_header)
    check(cover_table is not None,
          f"列名：代理口径的期间量表必须存在（实际表头 {[tuple(str(h) for h in t.header) for t in tables]}）")
    check(ebitda_table is not None,
          "列名：同量纲（元）的期间量表必须存在（列轴用期间表达）")
    check(debt_table is not None,
          "列名：同量纲（元）的时点量表必须存在（列轴用时点表达）")
    check(ebitda_table is not None and debt_table is not None and ebitda_table is not debt_table,
          "口径分组：同量纲（元）的期间量与时点量必须是**两张**表"
          " —— 拆表的理由只能是口径不同，不是量纲不同")
    for table in tables:
        for header_text in list(table.header)[1:]:
            check(header_text not in DECLARED_PERIODS,
                  f"列名：任何列名都不得是裸期间末日（实际 {header_text!r}）")
        check(str(table.period) not in DECLARED_PERIODS,
              f"表级期间：也不得是裸期间末日（实际 {table.period!r}）")
    if cover_table is not None:
        row = cover_table.rows[0]
        check(all(proxy_note in str(cell) for cell in row.cells),
              f"披露：代理口径表的每一格都要写出口径（实际 {row.cells}）")
    if ebitda_table is not None:
        row = ebitda_table.rows[0]
        check(all(NS.PROXY_MARKER_PHRASE not in str(cell) for cell in row.cells),
              f"披露：精确口径的期间量表不得出现口径限定语（实际 {row.cells}）")
    if debt_table is not None:
        row = debt_table.rows[0]
        check(all(NS.PROXY_MARKER_PHRASE not in str(cell) for cell in row.cells),
              f"披露：时点量的表不得出现口径限定语（实际 {row.cells}）")

    # 「及适用期间」：口径限定语所在的单元格，其**列名必须就是该事实的期间表达** ——
    # 读者在同一张表里读到「代理口径」与「2025年度」，而不是一个孤立的口径词。
    if cover_table is not None:
        row = cover_table.rows[0]
        headers = [str(h) for h in cover_table.header][1:]
        for index, (cell, fact) in enumerate(zip(row.cells, cover)):
            check(str(cell).strip() == "" or index >= len(headers)
                  or headers[index] == PB.period_expression(fact[0].period, fact[0].period_basis),
                  f"可见位置：带口径那一格的列名必须是该事实的期间表达"
                  f"（第 {index} 列 {headers[index] if index < len(headers) else None!r}）")

    # --- 读者实际读到的那一步：Markdown 的**表格行**里必须能读到限定语 -------------
    markdown = NS.render_paragraphs_markdown("财务", (), tables)
    check(_qualifier_visible_in_table(markdown, proxy_note),
          "可见位置：限量语必须出现在 Markdown 的**表格行**里（读者读数字的那一格）")
    check(NS.PROXY_MARKER_PHRASE in markdown,
          "可见位置：Markdown 必须含口径标记词")
    # 口径限定语与适用期间必须在**同一张表**里：表块（连续的表行）的**表头行**必须写出期间表达。
    table_blocks = [b for b in _table_blocks(markdown) if any(proxy_note in line for line in b)]
    check(bool(table_blocks), "可见位置：限定语必须落在某一张表的表行里")
    for block in table_blocks:
        header_line = block[0]
        check("2024年度" in header_line and "2025年度" in header_line,
              f"可见位置：限定语所在的那张表，表头行必须写出**适用期间**"
              f"（实际表头 {header_line!r}）")
    # 反证：口径词不能与期间表达分处两张表就算「写在一起」——构造两张表，限定语在一张、
    # 期间在另一张，判据必须为假（否则本判据只是「文本里出现过」的换皮）。
    split_two = "\n".join([
        "| 指标 | 2024年度 | 2025年度 |",
        "| --- | --- | --- |",
        "| 营业收入 | 1,000 | 1,234 |",
        "",
        f"| {NS.FINANCIAL_TABLE_LABEL_HEADER} | 2024年末 |",
        "| --- | --- |",
        f"| 总资产 | 9。{proxy_note} |",
    ])
    proof = [b for b in _table_blocks(split_two) if any(proxy_note in line for line in b)]
    check(len(proof) == 1 and "2025年度" not in proof[0][0],
          "可见位置的反证：口径与期间分处两张表时，本判据必须区分得出来（否则判据等于没写）")
    # 反例对照：手工造一张「裸值」表（值对、期间对，唯独单元格没写口径）——本模块的可见性判据
    # 必须能把它与真表分开，否则上一条断言等于恒真。
    naked = NS.NarrativeTable.create(
        section_id="financial", topic_ids=(TOPIC,), index=0,
        caption=NS.FINANCIAL_TABLE_CAPTION,
        header=flow_header, entity_scope=ENTITY, unit="倍", period="2024年度、2025年度",
        row_specs=[{"label": "利息保障倍数", "cells": ("2.98", "3.42"), "unit": "倍",
                    "period": "2024年度、2025年度",
                    "claim_ids": (cover[0][1].claim_id, cover[1][1].claim_id),
                    "citation_ids": tuple(
                        cid for _f, c, _b in cover for cid in NS.claim_citation_ids(c))}])
    naked_markdown = NS.render_paragraphs_markdown("财务", (), (naked,))
    check(not _qualifier_visible_in_table(naked_markdown, proxy_note),
          "可见位置反例前提：裸值表格的表格行里读不到限定语（判据能区分，不是恒真）")

    # ------------------------------------------------------------------
    # 4. 表题：**四张展示表的表题必须各自可辨**（读者面的同一性缺陷）
    # ------------------------------------------------------------------
    # 缺陷现场（r7b 记录的四张表）：`yuan/时点`、`ratio/时点`、`percent/时点`、`ratio/期间`。
    # 表题此前是**同一个固定文案**，四张表逐字重名；读者眼睛先落在的那一行对四张表说同一句话，
    # 只能靠下一行小字里「单位 / 期间列名」回推。注意四个分组键里有**两组连单位都相同**
    # （`ratio` 的时点组与期间组），因此只把单位写进表题**不够** —— 口径必须一起进表题。
    # 判据刻意是「表题由该表自己的 `(unit, 口径)` 算出」而不是「表题里出现了某个词」：后者在
    # 一张表上成立、在重名的那两张上照样成立。
    yuan_end = _pair("INTEREST_BEARING_DEBT", "有息负债", period_basis=PB.BASIS_END,
                     status="CALCULATED_EXACT", note="", reason_code=None,
                     values=("1,251.59亿元", "1,364.02亿元"), unit="yuan")
    ratio_end = _pair("SOLV_DEBT_RATIO_ALT", "带息债务倍数", period_basis=PB.BASIS_END,
                      status="CALCULATED_EXACT", note="", reason_code=None,
                      values=("1.20", "1.15"), unit="ratio")
    percent_end = _pair("DEBT_RATIO", "资产负债率", period_basis=PB.BASIS_END,
                        status="CALCULATED_EXACT", note="", reason_code=None,
                        values=("69.34%", "65.24%"), unit="percent")
    ratio_flow = _pair("SOLV_INTEREST_COVER", "利息保障倍数", period_basis=PB.BASIS_FLOW,
                       status="CALCULATED_EXACT", note="", reason_code=None,
                       values=("3.42", "2.98"), unit="ratio")
    caption_all = (*yuan_end, *ratio_end, *percent_end, *ratio_flow)
    caption_tables = NS.build_metric_period_tables(
        section_id="financial", claims=tuple(c for _f, c, _b in caption_all),
        authority=_authority(tuple(f for f, _c, _b in caption_all)),
        acceptance=_NS(accepted_bindings=tuple(b for _f, _c, b in caption_all)))
    check(len(caption_tables) == 4,
          f"表题夹具前置：现场形状必须分成 4 张表（实为 {len(caption_tables)} 张）")
    captions = [str(t.caption) for t in caption_tables]
    check(len(set(captions)) == len(captions),
          f"表题必须**逐表可辨**：四张表的表题不得重名（实为 {captions}）")
    expected_captions = {NS.financial_table_caption(unit=unit, basis=basis)
                         for unit, basis in (("yuan", PB.BASIS_END), ("ratio", PB.BASIS_END),
                                             ("percent", PB.BASIS_END), ("ratio", PB.BASIS_FLOW))}
    check(set(captions) == expected_captions,
          f"每张表的表题必须由它**自己的** (unit, 口径) 算出（期望 {sorted(expected_captions)}，"
          f"实为 {captions}）")
    check(all(c.startswith(NS.FINANCIAL_TABLE_CAPTION) for c in captions),
          "表题必须以**固定文案**起头（限定语只追加在尾部：读者仍认得出这是同一类表，"
          f"而固定文案也不是模型产物）—— 实为 {captions}")
    check(NS.financial_table_caption(unit="ratio", basis=PB.BASIS_END)
          != NS.financial_table_caption(unit="ratio", basis=PB.BASIS_FLOW),
          "同单位、不同口径的两张表必须靠**口径**限定语区分（单位相同，只有口径不同）")
    for caption in captions:
        check(NS.high_risk_surface_tokens(caption) == (),
              f"表题不得携带任何高风险表面（数字 / 主体名 / 含糊期间 / 结论标记）：{caption!r} "
              f"命中 {NS.high_risk_surface_tokens(caption)}"
              "（表题不是一处可以夹带事实的地方：表级文字的授权面因此恒为空集）")
    # 反证：本判据**不是恒真** —— 固定文案本身对四张表说同一句话，差异只由两个限定语造成。
    check(len({NS.FINANCIAL_TABLE_CAPTION for _t in caption_tables}) == 1
          and NS.FINANCIAL_TABLE_CAPTION not in set(captions),
          "反证前提：旧表题（固定文案）在四张表上逐字相同、且不等于新表题 —— 重名确实存在过")
    # 未登记的记号：与 `reader_unit_text` 同一条纪律（原样呈现，不发明单位、不替权威挑口径）。
    unknown_caption = NS.financial_table_caption(unit="people", basis="quarterly")
    check("people" in unknown_caption and "quarterly" in unknown_caption,
          f"未登记的单位 / 口径记号必须**原样呈现**（实为 {unknown_caption!r}）")
    check(NS.reader_unit_short_text("yuan") == "元"
          and NS.reader_unit_short_text("people") == "people"
          and NS.reader_unit_short_text("") == ""
          and NS.reader_unit_short_text(None) == "",
          "短单位说法与 `reader_unit_text` 同一句话的头部：未登记记号逐字返回，空值返回空串")
    check(all(NS.reader_unit_short_text(u) in NS.reader_unit_text(u)
              for u in NS.READER_UNIT_TEXTS),
          "短说法必须是长说法的**连续子串**（表题与紧随其后的「主体 / 期间 / 单位」那一行"
          "不可能互相矛盾 —— 它不是第二张单位表）")

    # 版本责任：表题**在表格的内容身份里**（`NarrativeTable.identity_body` → `derive_table_id`），
    # 因此换表题必然换 `table_id`，Narrative / SectionResult 的指纹随之前进。这就是本批
    # **不需要**新版本常量来承担这次文本变化的原因 —— 两版表题不可能共用一个身份。这与 `pwr-4`
    # 那次渲染口径变化恰好相反：`主体 / 期间 / 单位` 那一行**不在任何身份里**（表格元数据一字
    # 未动），所以只能由渲染器版本承担。判据是机械的，不是一句声明。
    def _captioned(caption: str) -> NS.NarrativeTable:
        return NS.NarrativeTable.create(
            section_id="financial", topic_ids=(TOPIC,), index=0, caption=caption,
            header=flow_header, entity_scope=ENTITY, unit="倍", period="2024年度、2025年度",
            row_specs=[{"label": "利息保障倍数", "cells": ("2.98", "3.42"), "unit": "倍",
                        "period": "2024年度、2025年度",
                        "claim_ids": (cover[0][1].claim_id, cover[1][1].claim_id),
                        "citation_ids": tuple(
                            cid for _f, c, _b in cover for cid in NS.claim_citation_ids(c))}])

    old_title = _captioned(NS.FINANCIAL_TABLE_CAPTION)
    new_title = _captioned(NS.financial_table_caption(unit="倍", basis=PB.BASIS_FLOW))
    check(old_title.table_id != new_title.table_id,
          "表题参与表格的内容身份：换表题必然换 `table_id`（于是 Narrative / SectionResult 的"
          "指纹必然前进）—— 这是本批不需要新版本常量的**机制**，而不是一句声明"
          f"（两版表题实得同一个 id {old_title.table_id!r}）")

    # ------------------------------------------------------------------
    # 5. fail-closed：没有口径的日期形状期间、非法口径、披露不进 Claim 文本
    # ------------------------------------------------------------------
    no_basis = _pair("SOLV_INTEREST_COVER", "利息保障倍数", period_basis=None,
                     status="CALCULATED_PROXY", note=proxy_note,
                     reason_code="PROXY_FINANCE_EXPENSES", values=("3.42", "2.98"))
    expect_error(
        lambda: NS.build_metric_period_tables(
            section_id="financial", claims=tuple(c for _f, c, _b in no_basis),
            authority=_authority(tuple(f for f, _c, _b in no_basis)),
            acceptance=_NS(accepted_bindings=tuple(b for _f, _c, b in no_basis))),
        NS.NarrativeSchemaError,
        "口径缺失：期间记号是权威日期形状却没有 `period_basis` 时必须 fail-closed"
        "（不得凭记号外观挑一个口径）",
        needle="却没有声明期间口径")

    bad_basis = _pair("SOLV_INTEREST_COVER", "利息保障倍数", period_basis="quarterly",
                      status="CALCULATED_PROXY", note=proxy_note,
                      reason_code="PROXY_FINANCE_EXPENSES", values=("3.42", "2.98"))
    expect_error(
        lambda: NS.build_metric_period_tables(
            section_id="financial", claims=tuple(c for _f, c, _b in bad_basis),
            authority=_authority(tuple(f for f, _c, _b in bad_basis)),
            acceptance=_NS(accepted_bindings=tuple(b for _f, _c, b in bad_basis))),
        NS.NarrativeSchemaError,
        "口径自造：`period_basis` 不在封闭取值内时必须 fail-closed",
        needle="期间口径")

    # 披露只写在别处：Claim 文本里没有该权威事实的**单元格文本**（限定语被省掉）→ 拒。
    hidden = []
    for fact, claim, binding in cover:
        surface = NS.authoritative_fact_surface("financial_pack", fact)
        stripped = surface.replace(proxy_note, "").rstrip(NS.SENTENCE_TERMINATORS).strip()
        hidden.append((fact, _claim(stripped, binding_id=binding.accepted_support_binding_id),
                       binding))
    expect_error(
        lambda: NS.build_metric_period_tables(
            section_id="financial", claims=tuple(c for _f, c, _b in hidden),
            authority=_authority(tuple(f for f, _c, _b in hidden)),
            acceptance=_NS(accepted_bindings=tuple(b for _f, _c, b in hidden))),
        NS.NarrativeSchemaError,
        "披露不得只在别处：Claim 文本省掉限定语时必须 fail-closed（写不出来就不许进表）",
        needle="代理口径的限定语")

    # 该限定语**确实经由引用链可取到**（权威表面里就有它），但那一格渲染进表体时读不到：
    # 「在引用链里」不构成披露，因此上面那条拒绝不是"哪里都没有它"的平凡拒绝。
    check(all(proxy_note in NS.authoritative_fact_surface("financial_pack", f) for f, _c, _b in cover),
          "前提：被省掉的限定语**在引用链指向的权威表面里是有的**（否则上面的拒绝是平凡的）")
    check(all(tuple(NS.claim_citation_ids(c)) for _f, c, _b in cover),
          "前提：这些 Claim 都有引用链（`citation_ids`）——它只进行 JSON，**不进表体**")

    # ------------------------------------------------------------------
    # 5b. C4：门的判据是**分量**（数值 / 期间表达 / 完整限定语），不是「整格的连续子串」。
    #     标点不再承担任何判据 —— 这是本次要修的那件事。
    # ------------------------------------------------------------------
    def _gate(facts_claims_bindings) -> tuple:
        return NS.build_metric_period_tables(
            section_id="financial",
            claims=tuple(c for _f, c, _b in facts_claims_bindings),
            authority=_authority(tuple(f for f, _c, _b in facts_claims_bindings)),
            acceptance=_NS(accepted_bindings=tuple(b for _f, _c, b in facts_claims_bindings)))

    def _rewrite(pairs, fn) -> list:
        """把每一对 (fact, claim, binding) 的 Claim 文本按 `fn` 改写（其余身份照抄）。"""
        return [(f, _claim(fn(str(c.text)), binding_id=b.accepted_support_binding_id), b)
                for f, c, b in pairs]

    # 5b.1 **正向（C4 要修的就是这一条）**：写作侧按提示词要求「末尾去掉句末标点、中间也不得
    # 有句末标点」之后，门必须通过并成表。旧口径要求整格 `3.42。代理口径（…）` 是 Claim 的
    # 连续子串，而提示词禁止 `claim_text` 中间出现句末标点 —— 两条要求互斥，正确事实必然被判红。
    def _flatten(text: str) -> str:
        return text.replace("。", "").strip()

    prompt_tables = _gate(_rewrite(cover, _flatten))
    check(len(prompt_tables) == 1,
          f"C4 正向：Claim 文本按提示词去掉内部句末标点后必须**通过**门并成表"
          f"（旧口径必然把它判红；实际 {len(prompt_tables)} 张表）")
    prompt_markdown = NS.render_paragraphs_markdown("财务", (), prompt_tables)
    check(_qualifier_visible_in_table(prompt_markdown, proxy_note),
          "C4 正向：成表之后限定语仍必须出现在**表格行**里（读者可见，不是只写在引用链里）")
    # 正向之二：把内部句号换成**句中逗号**（提示词明文允许的写法）同样必须通过 ——
    # 「标点形式不参与判据」这句话必须有反方向的证据，否则它只是个断言。
    comma_tables = _gate(_rewrite(
        cover, lambda t: _flatten(t).replace(NS.PROXY_MARKER_PHRASE,
                                             "，" + NS.PROXY_MARKER_PHRASE)))
    check(len(comma_tables) == 1,
          f"C4 正向：内部句号换成句中逗号后同样必须通过（标点形式不参与判据；"
          f"实际 {len(comma_tables)} 张表）")

    # 5b.2 反例：数值不对 → 拒，且点名**数值**分量（不得笼统说「单元格不存在」）。
    expect_error(
        lambda: _gate(_rewrite(cover, lambda t: _flatten(t).replace("3.42", "9.99"))),
        NS.NarrativeSchemaError,
        "C4 反例：Claim 文本里的数值与权威事实不符时必须 fail-closed",
        needle="没有该权威事实的数值")

    # 5b.3 反例：期间表达被换成别的期间 → 拒，且点名**期间表达**分量。
    # 旧口径**没有**这条判据（整格 `值。限定语` 里根本不含期间），因此它是 C4 的**新增收紧**。
    expect_error(
        lambda: _gate(_rewrite(cover, lambda t: _flatten(t).replace("2025年度", "2024年度"))),
        NS.NarrativeSchemaError,
        "C4 反例：Claim 文本里的期间表达与权威事实不符时必须 fail-closed"
        "（同一指标跨期成行，期间是行身份的一部分）",
        needle="没有该权威事实的期间表达")

    # 5b.4 反例：限定语被**截断/改写**（留下标记词但丢了权威的完整口径文本）→ 拒。
    # 「写了『代理口径』四个字」不等于承担了权威自己的那句限定语 —— 判据要的是**完整**分量。
    expect_error(
        lambda: _gate(_rewrite(cover, lambda t: _flatten(t).replace(proxy_note,
                                                                    NS.PROXY_MARKER_PHRASE))),
        NS.NarrativeSchemaError,
        "C4 反例：限定语被截断成标记词时必须 fail-closed（不得以「出现过代理口径四个字」通过）",
        needle="没有该权威事实的代理口径的限定语")

    # 5b.5 分量分解本身的守卫：门与渲染**共用**同一份分解，不得各写一遍。
    components = NS.financial_cell_components(proxy_fact)
    check((components.value, components.period_text, components.qualifier,
           components.cell) == ("3.42", "2025年度", proxy_note, f"3.42。{proxy_note}"),
          f"C4：分量化必须与渲染同源（实为 {components}）")
    check(components.is_proxy is True and components.required_texts() == (
        ("数值", "3.42"), ("期间表达", "2025年度"), ("代理口径的限定语", proxy_note)),
          "C4：要求的分量串必须恰好是「数值 / 期间表达 / 完整限定语」三项（不多不少）")
    exact_components = NS.financial_cell_components(exact_fact)
    check(exact_components.required_texts() == (("数值", "1,000"), ("期间表达", "2025年末"))
          and exact_components.is_proxy is False,
          "C4：精确口径的事实**不得**被要求任何限定语（不得给精确值加口径）")
    expect_error(lambda: NS.financial_cell_components(_fact(
        "metric_bare_1", code="TOTAL_ASSETS", label="总资产", period="2025-12-31",
        display="", period_basis=PB.BASIS_END)),
        NS.NarrativeSchemaError,
        "C4：没有任何数值渲染的单元格必须 fail-closed（不得凭空空写一格）",
        needle="没有数值分量")

    # ------------------------------------------------------------------
    # 6. 验收侧**独立重算**：裸列名与光秃秃的单元格都必须被判红
    # ------------------------------------------------------------------
    check(ACC._fin_period_expression("2025-12-31", "flow") == "2025年度"
          and ACC._fin_period_expression("2025-12-31", "end") == "2025年末",
          "验收侧独立重算：期间表达与生产侧同一规则（各自实现）")
    check(ACC._fin_period_expression("2025-12-31", "flow") in
          {str(h) for t in tables for h in t.header},
          "验收侧独立重算：生产侧产出的列名必须能被验收侧复算出来")
    check(ACC._fin_cell_text(proxy_fact) == f"3.42。{proxy_note}",
          "验收侧独立重算：代理事实的单元格应有文本含口径限定语")
    check(ACC._fin_basis_from_formula("SOLV_INTEREST_COVER") == "flow",
          "验收侧独立重算：指标口径由公式定义自己的 `period_requirement` 复算")

    ok = ACC._financial_readability(_section(claims, bindings, tables), authority)
    check(ok["problem_count"] == 0,
          f"A2 正向：带口径的期间表达 + 单元格披露必须通过可读结构门"
          f"（实际 {ok['problems'][:3]}）")

    # 逐格换成「裸值」：期间还在，唯独口径不见了 —— 判据必须点出这一格。
    # C4 之后 wire 层**会**拒「Claim 文本缺限定语」（见上方 5b.4），但它拒的是**输入侧**；
    # 表格对象本身一旦带着裸值单元格到达验收侧（单元格由门后构造器渲染，验收侧不重跑门），
    # 仍需被判红。故这里用 `object.__setattr__` 造出「裸值表格已经过界到达验收侧」的情形，
    # 复算同一件事实。
    naked_row = next((t for t in tables
                      if tuple(str(h) for h in t.header) == flow_header), None)
    check(naked_row is not None, "夹具前提：应当存在一张 flow 列轴的表供反例使用")
    if naked_row is not None:
        object.__setattr__(naked_row.rows[0], "cells", tuple(
            str(c).split("。")[0] for c in naked_row.rows[0].cells))
    broken = ACC._financial_readability(_section(claims, bindings, tables), authority)
    check(any("不等于该权威事实应有的可见文本" in p for p in broken["problems"]),
          f"验收侧：裸值单元格（口径只藏在 Claim 文本里）必须被判红"
          f"（实际 {broken['problems'][:3]}）")
    check(any("代理口径的限定语" in p for p in broken["problems"]),
          "验收侧：判红理由必须点名「代理口径的限定语必须出现在读者读数字的那一格」")

    # ------------------------------------------------------------------
    # 7. **r7b 真实形状的跨层正反例**（C4 落在验收侧）：验收侧判据必须与 wire 门同口径。
    #
    #   现场（`evaluation/results/m930_3_acceptance_crossdoc_real_r7b/`）：24 条财务 Claim 逐条
    #   形如 `2023年度的利息保障倍数为-9.94，代理口径（PROXY_FINANCE_EXPENSES）`，而权威渲染出的
    #   那一格是 `-9.94。代理口径（PROXY_FINANCE_EXPENSES）` —— **三个分量逐字一致，只差一个
    #   标点**（`,` vs `。`）。wire 门在 C4 之后按分量核对，因此它放这 24 条通过；验收侧的 A2 却
    #   仍要求「整格文本是配对 Claim 的连续子串」，于是把 4 个代理单元格全判红 —— A2 从
    #   `report-29` 起红的就是这一条**判据漂移**，不是内容缺陷。
    #
    #   下面用同一批夹具把两侧**同时**钉住：wire 门放行 + 验收侧放行（正例），再逐条给出验收侧
    #   必须**独立**判红的五种坏法（数值 / 期间表达 / 限定语 / 裸值单元格 / 错配 Claim）。
    # ------------------------------------------------------------------
    def _r7b_text(text: str) -> str:
        """r7b 的实际写法：去掉内部句末标点，并在限定语前用**句中逗号**（提示词明文允许）。"""
        return _flatten(text).replace(NS.PROXY_MARKER_PHRASE,
                                      "，" + NS.PROXY_MARKER_PHRASE)

    r7b_pairs = _rewrite(cover, _r7b_text)
    r7b_claims = tuple(c for _f, c, _b in r7b_pairs)
    r7b_bindings = tuple(b for _f, _c, b in r7b_pairs)
    # 表一律**重新构造**（第 6 节的裸值反例已就地改写过共享的 `tables`，此处不得复用）。
    r7b_tables = _gate(r7b_pairs)
    r7b_proxy_table = next(t for t in r7b_tables
                           if tuple(str(h) for h in t.header) == flow_header
                           and str(t.unit) == "倍")

    # 正例的前提（**这条不作废，正例才是非平凡的**）：整格文本**不是** Claim 的连续子串。
    # 旧判据在这一点上必然判红，因此下面那条「必须通过」真的在测新判据。
    _r7b_cell = str(r7b_proxy_table.rows[0].cells[0])
    _r7b_claim_text = str(r7b_claims[0].text)
    check(_r7b_cell not in _r7b_claim_text,
          f"跨层正例前提：r7b 形状的 Claim 文本不含整格文本作为连续子串 —— 旧判据必然判红，"
          f"正例因此非平凡（格 {_r7b_cell!r}；Claim {_r7b_claim_text!r}）")
    # 而三个分量**各自**都在（这正是 wire 门放行的那件事，也是验收侧此后要核的那件事）。
    check(all(needle in _r7b_claim_text
              for _label, needle in ACC._fin_cell_components(cover[0][0])),
          "跨层正例前提：r7b 形状的 Claim 文本逐字承担了该事实的**全部分量**"
          f"（分量 {ACC._fin_cell_components(cover[0][0])}；Claim {_r7b_claim_text!r}）")
    check(len(r7b_tables) == 1,
          f"跨层正例：wire 门按分量核对，必须放行 r7b 形状的 Claim 并成表（C4 之后的行为；"
          f"实际 {len(r7b_tables)} 张）")
    r7b_ok = ACC._financial_readability(
        _section(r7b_claims, r7b_bindings, r7b_tables), authority)
    check(r7b_ok["problem_count"] == 0,
          f"跨层正例：**验收侧**对同一份 r7b 形状的产物必须判绿（实际 {r7b_ok['problems'][:3]}）"
          "—— 两侧判据若再漂移，本模块必须红")

    # 分量分解的守卫：验收侧**独立重算**（不 import 生产侧实现），且精确事实不得被要求限定语。
    check([label for label, _t in ACC._fin_cell_components(proxy_fact)]
          == ["数值", "期间表达", "代理口径的限定语"],
          "跨层：代理事实的分量恰好是「数值 / 期间表达 / 完整限定语」三项")
    check([label for label, _t in ACC._fin_cell_components(exact_fact)]
          == ["数值", "期间表达"],
          "跨层：精确口径的事实**不得**被要求任何限定语（不得凭空给精确值加口径）")
    check(ACC._fin_period_text(proxy_fact) == "2025年度",
          "跨层：期间分量优先取权威自己的 `period_label`（不得退回裸期间末日）")
    check(ACC._fin_cell_components is not NS.financial_cell_components,
          "跨层：验收侧必须是**独立重算**，不是生产侧函数本身")
    import inspect as _inspect
    import re as _re
    check(not _re.search(r"financial_cell_components\s*\(", _inspect.getsource(ACC)),
          "跨层：验收侧不得**调用**生产侧 `financial_cell_components`"
          "（否则两侧漂移时会被生产侧自己的实现“自证”通过；只允许在说明文字里点名它）")

    def _broken_claim_case(fn) -> object:
        """**输入侧坏了、输出侧没坏**：表照正确权威文本渲染，Claim 文本按 `fn` 弄坏。

        单元格由门后构造器渲染、验收侧不重跑门（门在 Claim 侧就会拒），因此这种坏法只能由
        验收侧自己看见 —— 表重新指向坏 Claim，单元格与事实坐标一律不动。
        """
        pairs = _rewrite(cover, lambda t: fn(_r7b_text(t)))
        tables = _gate(cover)
        proxy_table = next(t for t in tables
                           if tuple(str(h) for h in t.header) == flow_header
                           and str(t.unit) == "倍")
        for row in proxy_table.rows:
            object.__setattr__(row, "claim_ids",
                               tuple(str(c.claim_id) for _f, c, _b in pairs))
        return _section(tuple(c for _f, c, _b in pairs),
                        tuple(b for _f, _c, b in pairs), (proxy_table,))

    # 7.1 数值不对（限定期数里的数被换掉）→ 验收侧必须点名**数值**分量。
    _w = ACC._financial_readability(
        _broken_claim_case(lambda t: t.replace("3.42", "9.99")), authority)
    check(any("数值" in p and "配对 Claim" in p for p in _w["problems"]),
          f"跨层反例：Claim 侧数值与权威事实不符必须判红并点名**数值**分量"
          f"（实际 {_w['problems'][:3]}）")

    # 7.2 期间表达不对（把 2025年度 写成 2024年度）→ 点名**期间表达**分量。
    _w = ACC._financial_readability(
        _broken_claim_case(lambda t: t.replace("2025年度", "2024年度")), authority)
    check(any("期间表达" in p and "配对 Claim" in p for p in _w["problems"]),
          f"跨层反例：Claim 侧期间表达与权威事实不符必须判红并点名**期间表达**分量"
          f"（实际 {_w['problems'][:3]}）")

    # 7.3 限定语被截断成标记词（丢了权威自己的完整口径文本）→ 点名**代理口径的限定语**分量。
    _w = ACC._financial_readability(
        _broken_claim_case(lambda t: t.replace(proxy_note, NS.PROXY_MARKER_PHRASE)),
        authority)
    check(any("代理口径的限定语" in p and "配对 Claim" in p for p in _w["problems"]),
          f"跨层反例：Claim 侧限定语被截断必须判红并点名**代理口径的限定语**分量"
          f"（实际 {_w['problems'][:3]}）")

    # 7.4 表格只显示裸数（Claim 侧分量齐全，输出侧的那一格丢了限定语）→ 输出侧判据判红。
    _naked_tables = _gate(r7b_pairs)
    _naked_proxy = next(t for t in _naked_tables
                        if tuple(str(h) for h in t.header) == flow_header
                        and str(t.unit) == "倍")
    object.__setattr__(_naked_proxy.rows[0], "cells", tuple(
        str(c).split("。")[0] for c in _naked_proxy.rows[0].cells))
    _w = ACC._financial_readability(_section(r7b_claims, r7b_bindings, (_naked_proxy,)),
                                   authority)
    check(any("不等于该权威事实应有的可见文本" in p for p in _w["problems"]),
          f"跨层反例：表格只显示裸数（限定语只在 Claim 文本里）必须判红"
          f"（实际 {_w['problems'][:3]}）")

    # 7.5 错配 Claim：一格配上了**另一期**的 Claim 文本（分量对不上）→ 判红。
    #     这正是「24 条里有一条绑错」的样子：单元格与事实坐标都还在，只有配对本身错了。
    _swapped = [(f, _claim(_r7b_text(str(other.text)),
                           binding_id=b.accepted_support_binding_id), b)
                for (f, _c, b), (_f2, other, _b2) in zip(cover, reversed(cover))]
    _swapped_tables = _gate(cover)
    _swapped_proxy = next(t for t in _swapped_tables
                          if tuple(str(h) for h in t.header) == flow_header
                          and str(t.unit) == "倍")
    for row in _swapped_proxy.rows:
        object.__setattr__(row, "claim_ids",
                           tuple(str(c.claim_id) for _f, c, _b in _swapped))
    _w = ACC._financial_readability(
        _section(tuple(c for _f, c, _b in _swapped),
                 tuple(b for _f, _c, b in _swapped), (_swapped_proxy,)), authority)
    check(any("数值" in p and "配对 Claim" in p for p in _w["problems"]),
          f"跨层反例：单元格配上另一期的 Claim 必须判红（分量对不上）"
          f"（实际 {_w['problems'][:3]}）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    import json

    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["failed"] == 0 else 1)

"""Eval: §0.19/§0.20 —— 新链财务节的**确定性**「指标 × 期间」表（`cmt-4` / `cmtr-4`）。

用法: python -m evals.test_m930_3_cited_financial_table

§0.20 之后财务节**不再产生 `Claim`**，旧链的 `build_metric_period_tables` 因此构造不出表。
本模块钉住「把同一套规则搬到新链输入面」这件事的边界，逐条证明：

1. **合法缺席是 typed 记录，不是静默空表**：权威不是财务 / 权威没有事实 / 没有 ≥2 期的同指标 /
   artifact 未声明期间顺序 —— 四种各有自己的原因码，且**表格与拒绝互斥**。
2. **结构性伤一律 fail-closed**（不降级成「空表 + 一句温和说明」）：同一格两条事实、跨期
   label/unit 不一致、期间不在 artifact 声明内、期间是权威日期形状却没声明口径、口径不在封闭
   取值内、缺 label、无 `company_id` —— 各自 typed。
3. **`cmtr-2`：行有没有不再由写作结果决定**（`cmtr-3` 沿用这一条）。同一份权威事实，正文一句都不引用它、或把它整段
   改写掉，表格**逐格不变**；反过来，写作面多写了一句也不进表外的行。这是本轮的核心裁决，
   因此 §3 用正反两面钉住它，而不是靠读代码相信。
4. **逐格核对是纵深防线**：`_checked_cell` 逐格核数值 / 期间对列 / 量纲对行 / 代理限定语确实
   渲染进格 / 引用键非空。在当前**唯一**渲染实现（`NS.financial_table_cell`）下，公开路径上
   这几条不可达 —— 本模块直接调 `_checked_cell` 证明它真的接线且真的会拒，**不**假装它有一条
   业务反例。
5. **覆盖读数是独立对账**：必需事实逐条落到 `displayed` / `available_not_displayed`（带闭合
   原因码）/ `missing`（带权威自己的 `reason_code`）三档，且「已展示」必须真的能翻到那一格。
6. **双期派生事实永不进表**：判据是事实自己声明的 `input_periods`，不是 `code` 也不是期间形状。
7. **表格身份内容寻址**：`table_id` / `fingerprint` 由身份体（含 `rule_version`）重算，
   `to_dict` → `from_dict` 逐字段还原，篡改一个字段必被抓住。
8. **读者面渲染**：单位与口径在表题那一行，缺值的格写成「（留空）」而不是被回填。
9. **`cmtr-3` 起，`cmtr-4` 收紧：展示层级真的参与成表**。一行里只要有一格来自代理口径，
   整行进诊断槽位（`display_tier=diagnostic_only`），**不并入正文指标表**；`display_tier`
   进身份体与 wire，旧 wire 解码为 `required_body`。§10 用「同一单位、同一口径下两档并存」
   这一面钉住它 —— 若分组键漏掉层级，代理行就会被并进正文那张表而读者分不出哪一格是代理值。
   `cmtr-4` 另加一条：代理档还要**与呈现层路由声明指向的那一栏的契约档位一致**
   （`contract_display_tier` + `authority_proxy_fact_marker` 两条判据，见
   `test_m930_3_financial_presentation` §2）。

夹具复用 `test_demo_pack_writer` 的真实 `FinancialPackArtifact` / 真实 `FinancialFactProjection`
与同一条 `CW.build_cited_writer_input` 入口；模块无公司代号、文件名、页码、表号或固定年份的
生产字面量。不调 LLM（本地 stub 只回放夹具）、不联网、不写库、不建第二套财务口径。

**本模块不宣称任何内容门通过**：它证明的是这张表「只能由本节清单内的合格权威事实构成」，
不证明正文写得切题，也不证明财务节可发布。
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import test_demo_pack_writer as T            # noqa: E402
from sections import cited_financial_table as CFT       # noqa: E402
from sections import cited_writer as CW                 # noqa: E402
from sections import financial_pack_artifact as FPA     # noqa: E402
from sections import material_context as MC             # noqa: E402
from sections import narrative_schema as NS             # noqa: E402
from sections import pack_writer as PW                  # noqa: E402

_FIN_SECTION = "financial"
_FIN_TOPIC = "fin-solvency"
_FIN_TASK = "task-fin-table"
_REQ_TEXT = "列示报告期内主要偿债能力指标"
_SUB = "sub-solvency"
_P0 = "2023-12-31"
_P1 = "2024-12-31"
_P2 = "2025-12-31"
_T0 = "2023年末"
_T1 = "2024年末"
_T2 = "2025年末"
_CODE = "SOLV_CURRENT_RATIO"
_QUICK = "SOLV_QUICK_RATIO"
_UNIT = "ratio"
_CO = "示例股份"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class _Spec:
    """`FinancialFactProjection` 的测试侧规格（比 `_FinFact` 多带派生血缘三件套）。

    为什么不能直接用 `test_demo_pack_writer._FinFact`：那条路不把 `input_periods` 传进投影，
    于是**双期派生事实**在这条夹具上根本不成立（`is_derived_two_period_fact` 恒为假），
    §6 那条规则就一次也测不到。本模块必须能造出真实的派生事实 —— 判据本来就是事实自己声明的
    字段，而不是某个 `code` 字符串。
    """

    fact_id: str
    label: str
    display: str
    period: str
    unit: str = ""
    value_text: str | None = "1.00"
    citation: dict | None = None
    kind: str = "ratio"
    code: str = ""
    status: str = "ok"
    reason_code: str | None = None
    note: str = ""
    period_basis: str = ""
    period_label: str = ""
    derived_from: tuple[str, ...] = ()
    input_periods: tuple[str, ...] = ()
    input_raw_texts: tuple[str, ...] = ()


def _artifact(*, facts, periods=(), gaps=(), company_id: str = T.COMPANY_ID,
              report_as_of: str = T.REPORT_AS_OF) -> FPA.FinancialPackArtifact:
    """真实 `FinancialPackArtifact` 的合成工厂（身份/内容指纹由真实实现计算并 `verify`）。

    `gaps` 只装**必需**指标的缺口：权威 artifact 自己把「required 缺失」与「仅供诊断的缺失」
    分成 `gaps` / `diagnostic_gaps` 两列（`financial_worker.build_fact_pack` 的同名分流），
    本夹具照这个 schema 摆，不把两列混成一列。
    """
    snapshot = T._snapshot_identity(company_id, report_as_of)
    projections = []
    for spec in facts:
        citation = dict(spec.citation or {})
        # 财务 citation 是 `CitationRef` 的形状：`ref_type` 必须由事实自己给出（缺它即
        # fail-closed，不得臆造 locator），快照身份在此补齐。
        citation.setdefault("ref_type", "structured")
        citation.setdefault("snapshot_id", snapshot.snapshot_id)
        projections.append(FPA.FinancialFactProjection(
            fact_id=spec.fact_id, kind=spec.kind, label=spec.label, code=spec.code,
            period=spec.period, value_text=spec.value_text, display=spec.display,
            unit=spec.unit, status=spec.status, reason_code=spec.reason_code,
            note=spec.note, citation=citation,
            period_basis=spec.period_basis, period_label=spec.period_label,
            derived_from=spec.derived_from, input_periods=spec.input_periods,
            input_raw_texts=spec.input_raw_texts))
    artifact = FPA.FinancialPackArtifact(
        schema_version=FPA.FINANCIAL_PACK_ARTIFACT_SCHEMA_VERSION, artifact_id="",
        task_id=_FIN_TASK, projection_id="proj-cmt-2",
        contract_version=T.CONTRACT_VERSION, contract_fingerprint=T.CONTRACT_FINGERPRINT,
        producer_kind=FPA.FINANCIAL_ARTIFACT_PRODUCER_KIND,
        fact_selection_rule_version="fsr-demo-1",
        projection_version=FPA.FINANCIAL_PACK_PROJECTION_VERSION, snapshot=snapshot,
        periods=tuple(periods), statements_available=(), period_note={},
        facts=tuple(projections),
        selected_fact_ids=tuple(p.fact_id for p in projections), excluded=(),
        gaps=tuple(dict(g) for g in gaps), diagnostic_gaps=(), projection_notes=())
    fingerprint = artifact.compute_content_fingerprint()
    artifact = dataclasses.replace(
        artifact, content_fingerprint=fingerprint,
        artifact_id=FPA._ARTIFACT_ID_PREFIX + fingerprint[:24])
    artifact.verify()
    return artifact


def _gap(*, code: str, period: str, label: str, reason_code: str = "MISSING_INPUT",
         status: str = "MISSING_INPUT") -> dict:
    """一条**必需指标缺口**的规格（形状逐字对齐 `financial_worker.build_fact_pack` 的那一列）。"""
    return {"fact_id": f"metric_{code}_{period}", "formula_id": code, "label": label,
            "kind": "calculation", "period": period, "status": status,
            "reason_code": reason_code}


def _fact(*, code: str, period: str, label: str = "流动比率", display: str = "1.20 倍",
          unit: str = _UNIT, basis: str = "end", period_label: str = "", **kw) -> _Spec:
    return _Spec(fact_id=f"{code.lower()}-{period[:4]}", label=label, display=display,
                 period=period, unit=unit, code=code, period_basis=basis,
                 period_label=period_label, **kw)


def _pair(*, code: str = _CODE, label: str = "流动比率", unit: str = _UNIT, **kw):
    """一组「同一指标 × 两期」的事实规格（本节正例的形状）。"""
    return (_fact(code=code, period=_P1, label=label, display="1.20 倍", unit=unit,
                  period_label=_T1, **kw),
            _fact(code=code, period=_P2, label=label, display="1.35 倍", unit=unit,
                  period_label=_T2, **kw))


def _fin_task():
    return T._task(_FIN_SECTION, (_FIN_TOPIC,), task_id=_FIN_TASK, title="偿债能力")


def _authority(*, facts, periods=(), gaps=(), company_name: str = _CO):
    task = _fin_task()
    artifact = _artifact(facts=facts, periods=periods, gaps=gaps)
    authority = T._financial_authority(
        task, artifact, note_gap=T._NoteGap(task_id=task.task_id), company_name=company_name)
    return task, authority


def _subsections():
    # `cwm-3`：小节必须**声明**它要写的 Contract 栏目（取自冻结合同 `review_52q.json`）。
    return (CW.CitedSubsectionSpec(subsection_id=_SUB, title="偿债能力",
                                   requirement_text=_REQ_TEXT,
                                   declared_aspect_ids=("fin_solvency.short_term_solvency",)),)


class _FactClient:
    """本地离线**事实直述**替身：逐条引用事实行，文本按 `mutate` 加工（默认逐字照抄）。

    与 `scripts.run_m930_3_cited_chain.OfflineFactAssertionCitedProseClient` 同一纪律
    （不改写、不推理、不计算）。`mutate` / `skip` 两个钩子是 §3 的**反例来源**：本轮要证明的
    恰恰是「正文怎么改都不动表格」，因此必须能把正文改坏、改空。
    """

    def __init__(self, *, mutate=None, skip=(), per_subsection: int = 8) -> None:
        self.mutate = mutate or (lambda key, text: text)
        self.skip = {str(k) for k in skip}
        self.per_subsection = int(per_subsection)

    def compose(self, *, messages, system: str, prompt_version: str,
                model_policy: str) -> CW.CitedProseResult:
        payload = json.loads(str(messages[0]["content"]))
        facts = [f for f in payload.get("authority_facts") or ()
                 if str(f.get("text") or "").strip()
                 and str(f.get("key") or "") not in self.skip]
        subsections: list[dict] = []
        gaps: list[dict] = []
        cursor = 0
        seq = 0
        for spec in payload["subsections"]:
            picked = []
            for _ in range(self.per_subsection):
                if not facts:
                    break
                picked.append(facts[cursor % len(facts)])
                cursor += 1
            sentences = []
            for fact in picked:
                seq += 1
                key = str(fact["key"])
                sentences.append({"sentence_id": f"s{seq:04d}",
                                  "text": self.mutate(key, str(fact["text"]).strip()),
                                  "citations": [key]})
            paragraphs = ([{"paragraph_id": "p1", "sentences": sentences}]
                          if sentences else [])
            if not sentences:
                gaps.append({"subsection_id": spec["subsection_id"],
                             "requirement_text": spec["requirement_text"],
                             "reason": ("no_source_in_manifest" if not facts
                                        else "manifest_partial_for_requirement"),
                             "detail": "本替身在这一小节下没有被引用的事实行。"})
            subsections.append({"subsection_id": spec["subsection_id"],
                                "title": spec["title"], "paragraphs": paragraphs})
        text = json.dumps({"subsections": subsections, "gaps": gaps,
                           "follow_up_needs": []}, ensure_ascii=False, sort_keys=True)
        return CW.CitedProseResult(text=text, call_id="offline-fact-table-fixture",
                                   model="offline-fact-assertion",
                                   prompt_version=prompt_version, status="ok")


def _manifest_and_draft(*, authority, task, mutate=None, skip=()):
    context = MC.empty_material_context_for_authority(authority)
    scan = PW.scan_financial(authority, task)
    manifest = CW.build_cited_writer_input(
        authority=authority, material_context=context, subsections=_subsections(),
        facts=scan.facts, section_title="偿债能力")
    outcome = CW.write_cited_section(
        manifest=manifest, client=_FactClient(mutate=mutate, skip=skip),
        model_policy="offline_stub")
    return manifest, outcome.draft


def _build(*, facts, periods=(), gaps=(), mutate=None, skip=(), company_name: str = _CO,
           with_manifest: bool = False):
    task, authority = _authority(facts=facts, periods=periods, gaps=gaps,
                                 company_name=company_name)
    manifest, _draft = _manifest_and_draft(
        authority=authority, task=task, mutate=mutate, skip=skip)
    outcome = CFT.build_cited_metric_tables(
        section_id=_FIN_SECTION, authority=authority, manifest=manifest)
    return (outcome, manifest) if with_manifest else outcome


def _scan_facts(facts, periods):
    """把一组规格物化成**权威事实对象**（用于把「格」与权威字段直接对照）。"""
    artifact = _artifact(facts=facts, periods=periods)
    by_id = {f.fact_id: f for f in artifact.facts}
    return [by_id[spec.fact_id] for spec in facts]


def _table_stub() -> CFT.CitedMetricTable:
    row = CFT.CitedMetricTableRow(label="流动比率", unit=_UNIT, cells=("1.20 倍", "1.35 倍"),
                                  fact_ids=("a", "b"), citation_keys=("f01", "f02"),
                                  period_texts=(_T1, _T2))
    return CFT.CitedMetricTable.create(
        section_id=_FIN_SECTION, caption="占位表题", header=("指标", _T1, _T2),
        entity_scope="示例（c-1）", unit=_UNIT, period_basis="end", rows=(row,))


def _expect_error(fn, *, reason: str | None = None) -> str:
    """必须 fail-closed。`reason` 给出时核对 typed 原因码；不给时只要求它确实抛错。"""
    try:
        fn()
    except CFT.CitedMetricTableError as exc:
        if reason is not None and exc.reason != reason:
            raise AssertionError(
                f"typed 原因码不是 {reason!r}，而是 {exc.reason!r}：{exc}") from None
        return str(exc)
    raise AssertionError(f"这一路必须 fail-closed（期望 {reason!r}），但它通过了")


# ---------------------------------------------------------------------------
# 主流程
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

    # ============================================================ §1 合法缺席
    details.append("## §1 合法缺席各有 typed 原因码，且与表格互斥")
    out = CFT.build_cited_metric_tables(
        section_id=_FIN_SECTION,
        authority=SimpleNamespace(producer_kind="topic_harness"), manifest=None)
    check(len(out.refusals) == 1 and out.refusals[0].reason == "authority_not_financial"
          and not out.tables,
          "非财务权威 ⇒ `authority_not_financial`（在读到清单之前就返回）")

    out = _build(facts=(), periods=(_P1, _P2))
    check([r.reason for r in out.refusals] == ["no_authority_facts"],
          f"财务权威无事实 ⇒ `no_authority_facts`（实测 {[r.reason for r in out.refusals]}）")

    out = _build(facts=(_fact(code=_CODE, period=_P1, period_label=_T1),
                        _fact(code=_QUICK, period=_P2, label="速动比率", display="0.90 倍",
                              period_label=_T2)),
                 periods=(_P1, _P2))
    check([r.reason for r in out.refusals] == ["no_two_period_metric"],
          "两条指标各只有一个期间 ⇒ 单期指标不成行（「写进正文」与「排进表格」是两件事）")

    out = _build(facts=_pair(), periods=())
    check([r.reason for r in out.refusals] == ["no_declared_period_order"],
          "artifact 未声明期间顺序 ⇒ `no_declared_period_order`（列轴不得自创）")

    check(not (out.tables and out.refusals),
          "表格与拒绝**互斥**：一次构造不可能既给出表又给出「本节没有表」")
    _expect_error(
        lambda: CFT.CitedMetricTableOutcome(
            section_id=_FIN_SECTION, schema_version=CFT.CITED_METRIC_TABLE_SCHEMA_VERSION,
            rule_version=CFT.CITED_METRIC_TABLE_RULE_VERSION,
            authority_kind="financial_workflow", tables=(_table_stub(),),
            refusals=(CFT.CitedMetricTableRefusal(reason="no_authority_facts", detail="x"),)),
        reason="table_and_refusal")
    _expect_error(lambda: CFT.CitedMetricTableRefusal(reason="没拿到", detail="x"))
    check(True, "未登记的原因码被 `CitedMetricTableRefusal` 当场拒绝（原因码取自封闭词表）")

    # ============================================================ §2 正例
    details.append("## §2 正例：真实形状的「同一指标 × 两期」成表")
    specs = _pair()
    facts = _scan_facts(specs, (_P1, _P2))
    out = _build(facts=specs, periods=(_P1, _P2))
    check(not out.refusals and len(out.tables) == 1,
          f"两个期间的同指标 ⇒ 恰一张表（实测 {len(out.tables)} 张、"
          f"{len(out.refusals)} 条拒绝）")
    table = out.tables[0]
    check(table.header == (NS.FINANCIAL_TABLE_LABEL_HEADER, _T1, _T2),
          f"列名写**期间表达**而不是裸的期间末日：{table.header}")
    check(table.caption == NS.financial_table_caption(unit=_UNIT, basis="end")
          and "单位：" in table.caption and "口径：" in table.caption,
          f"表题带本表自己的单位与口径：{table.caption!r}")
    check(table.entity_scope == f"{_CO}（{T.COMPANY_ID}）",
          f"主体写成可核实的名称 + 可回查标识：{table.entity_scope!r}")
    check(table.unit == _UNIT and table.period_basis == "end",
          "单位与期间口径逐字取自权威（不是渲染层挑的）")
    check(len(table.rows) == 1 and table.rows[0].label == "流动比率",
          "恰好一行，行标签取事实自己的 `label`")
    row = table.rows[0]
    check(row.cells == tuple(NS.financial_table_cell(f) for f in facts),
          f"两格逐字等于 `NS.financial_table_cell` 的结果：{row.cells}")
    check(all(cell in NS.authoritative_fact_surface("financial_pack", f)
              for cell, f in zip(row.cells, facts)),
          "每一格都是该事实**权威表面**的连续子串（表格里不可能出现权威没说过的数）")
    check(all(all(text in cell or text in table.header[i + 1]
                  for _name, text in NS.financial_cell_components(f).required_texts())
              for i, (cell, f) in enumerate(zip(row.cells, facts))),
          "逐格五分量齐备：数值 / 期间表达（对列，由表头承担）/ 代理限定语都在读者看得见的位置")
    check(row.fact_ids == tuple(s.fact_id for s in specs)
          and len(row.citation_keys) == 2 and row.period_texts == (_T1, _T2),
          "逐格记事实坐标 / 引用键 / 期间表达，且三者同长")
    check(table.table_id == NS.content_id("cmt_", table.identity_body())
          and table.rule_version == CFT.CITED_METRIC_TABLE_RULE_VERSION,
          "表身份内容寻址，`rule_version` 在身份体内（改规则即换表 id）")
    details.append(f"NOTE §2：表题 {table.caption!r}；列 {table.header}；格 {row.cells}。"
                   "空着的格留空，本模块不做任何算术。")

    # ============================================================ §3 写作结果不参与
    details.append("## §3 `cmtr-2`（`cmtr-3` 沿用）：行有没有**不再**由写作结果决定")
    no_cite = _build(facts=_pair(), periods=(_P1, _P2))
    check(len(no_cite.tables) == 1
          and no_cite.tables[0].rows[0].cells == row.cells,
          "同一份权威 ⇒ 表逐格与 §2 相同（写作侧不参与，构造是确定性的）")
    stripped = _build(facts=_pair(), periods=(_P1, _P2),
                      mutate=lambda k, t: t.replace("1.20 倍", "1.20倍")
                      .replace("1.35 倍", "1.35倍").replace(_T1, "").replace(_T2, ""))
    check(len(stripped.tables) == 1
          and stripped.tables[0].rows[0].cells == row.cells,
          "把正文句里的数值与期间表达改掉 ⇒ 表格**逐格不变**：表格不是正文句的投影")
    dropped = _build(facts=_pair(), periods=(_P1, _P2), skip=("f01", "f02"))
    check(len(dropped.tables) == 1
          and dropped.tables[0].rows[0].cells == row.cells,
          "正文一句都不引用它（写作面报 gap）⇒ 表格照样建成：写作侧的缺席不是表格的判据")
    unknown, mf = _build(facts=_pair(), periods=(_P1, _P2), with_manifest=True)
    manifest_fact_ids = {f.fact_id for f in mf.facts}
    check(set(unknown.tables[0].rows[0].fact_ids) <= manifest_fact_ids,
          "反向：表里的每一格都落在**本节清单**的事实集内（表格不会多出清单外的行）")
    details.append("NOTE §3：判据换轴是刻意的 —— `cmtr-1` 让一次写作生成结果决定读者面有没有"
                   "这一行；`cmtr-2`（`cmtr-3` 沿用）把它交还给权威事实与本节清单。为此 `draft` 参数已从"
                   "`build_cited_metric_tables` 的签名里撤销（构造函数根本看不到草稿）。")

    # ============================================================ §4 逐格核对（纵深防线）
    details.append("## §4 逐格核对：数值 / 期间 / 量纲 / 代理口径 / 引用键")
    f1 = facts[0]
    check(CFT._checked_cell(fact=f1, column_text=_T1, unit=_UNIT, citation_key="f01",
                            table_key=("t",)) == row.cells[0],
          "正例：真实形状的一格通过五项核对，结果就是 `NS.financial_table_cell`")
    _expect_error(
        lambda: CFT._checked_cell(fact=f1, column_text=_T1, unit="yuan", citation_key="f01",
                                  table_key=("t",)),
        reason="cell_component_not_carried")
    _expect_error(
        lambda: CFT._checked_cell(fact=f1, column_text=_T2, unit=_UNIT, citation_key="f01",
                                  table_key=("t",)),
        reason="cell_component_not_carried")
    _expect_error(
        lambda: CFT._checked_cell(fact=f1, column_text=_T1, unit=_UNIT, citation_key="",
                                  table_key=("t",)),
        reason="cell_component_not_carried")
    details.append("NOTE §4：三条反例是**直接调 `_checked_cell`** 造的 —— 在唯一渲染实现下，"
                   "公开路径不可能产出错列 / 错量纲 / 无引用键的格，因此这里证明的是"
                   "「这道纵深防线真的接线、真的会拒」，不是「它有一条业务反例」。"
                   "分层说实话比编一条假反例强。")

    # ============================================================ §5 覆盖读数
    details.append("## §5 覆盖读数：必需事实逐条落到三档")
    cov_specs = (_fact(code=_CODE, period=_P1, period_label=_T1),
                 _fact(code=_CODE, period=_P2, period_label=_T2),
                 # 只有一期的另一个指标：合格、在清单内，但不成行。
                 _fact(code=_QUICK, period=_P0, label="速动比率", display="0.80 倍",
                       period_label=_T0))
    cov_gap = _gap(code="SOLV_DEBT_RATIO", period=_P1, label="资产负债率")
    out_cov = _build(facts=cov_specs, periods=(_P0, _P1, _P2), gaps=(cov_gap,))
    check(len(out_cov.tables) == 1, "覆盖读数与表格并存（读数是独立一根轴）")
    shown_codes = sorted(r.code for r in out_cov.coverage if r.state == "displayed")
    check(len(out_cov.coverage) == 4 and shown_codes == [_CODE, _CODE],
          f"两期流动比率 ⇒ 两条 `displayed`（实测 {out_cov.coverage_counts()}）")
    quick_rows = [r for r in out_cov.coverage if r.code == _QUICK]
    check(len(quick_rows) == 1 and quick_rows[0].state == "available_not_displayed"
          and quick_rows[0].reason == "below_min_periods",
          f"单期速动比率 ⇒ `available_not_displayed` / `below_min_periods`："
          f"{[r.reason for r in quick_rows]}")
    miss = [r for r in out_cov.coverage if r.state == "missing"]
    check(len(miss) == 1 and miss[0].code == "SOLV_DEBT_RATIO"
          and miss[0].reason == "MISSING_INPUT" and not miss[0].table_id,
          f"权威自己记的必需缺口 ⇒ `missing`，原因逐字搬运权威的 `reason_code`："
          f"{[(r.fact_id, r.reason) for r in miss]}")
    shown = [r for r in out_cov.coverage if r.state == "displayed"]
    check(all(r.table_id == out_cov.tables[0].table_id and r.column_text for r in shown),
          "`displayed` 必须真的能翻到那一格（表 id + 列名都在）")
    _expect_error(
        lambda: CFT.CitedMetricTableOutcome(
            section_id=_FIN_SECTION, schema_version=CFT.CITED_METRIC_TABLE_SCHEMA_VERSION,
            rule_version=CFT.CITED_METRIC_TABLE_RULE_VERSION,
            authority_kind="financial_workflow", tables=(),
            coverage=(CFT.CitedMetricCoverageRow(
                state="displayed", fact_id="x", code="c", label="l", period=_P1,
                reason="displayed", table_id="cmt_不存在"),)),
        reason="coverage_displayed_table_missing")
    check(True, "自报「已展示」但本次没有那张表 ⇒ 当场拒（读数不许自报）")
    _expect_error(
        lambda: CFT.CitedMetricCoverageRow(
            state="available_not_displayed", fact_id="x", code="c", label="l", period=_P1,
            reason="表太小塞不下"))
    check(True, "未登记的不成表原因被拒（原因码取自封闭词表）")
    cov_md = CFT.render_cited_metric_coverage_markdown(out_cov)
    check(all(state in cov_md for state in CFT.CITED_METRIC_COVERAGE_STATES)
          and "MISSING_INPUT" in cov_md and out_cov.tables[0].table_id in cov_md,
          "覆盖对账渲染包含三档、权威的理由码与可回查的表 id")
    not_in = _build(facts=_pair(code="SOLV_CASH_RATIO", label="现金比率"), periods=(_P1, _P2))
    check(len(not_in.tables) == 1, "换了指标代码但形状相同的输入，表格照常构造")
    details.append("NOTE §5：`missing` 档只搬**权威自己**的 `reason_code`，本模块不替它归一化；"
                   "另两档的原因来自构造期的真实判据。计数只报数，不产生任何「覆盖通过」结论。")

    # ============================================================ §6 结构性伤
    details.append("## §6 结构性伤一律 fail-closed（不降级成空表）")
    dup = _pair() + (_Spec(fact_id="solv_current_ratio-dup", label="流动比率",
                           display="1.21 倍", period=_P1, unit=_UNIT, code=_CODE,
                           period_basis="end", period_label=_T1),)
    _expect_error(lambda: _build(facts=dup, periods=(_P1, _P2)),
                  reason="cell_owner_ambiguous")
    _expect_error(
        lambda: _build(facts=(_fact(code=_CODE, period=_P1, period_label=_T1),
                              _fact(code=_CODE, period=_P2, label="流动比",
                                    display="1.35 倍", period_label=_T2)),
                       periods=(_P1, _P2)),
        reason="metric_label_unit_inconsistent")
    _expect_error(lambda: _build(facts=_pair(), periods=(_P1,)),
                  reason="period_not_declared_by_artifact")
    # 期间记号本身就是权威日期形状（`YYYY-MM-DD`），却**没有**声明口径 ⇒ 不得凭外观替它挑一个
    # （挑错方向恰好是把期间量写成时点量）。
    _expect_error(
        lambda: _build(facts=(_fact(code=_CODE, period=_P1, basis=""),
                              _fact(code=_CODE, period=_P2, basis="")),
                       periods=(_P1, _P2)),
        reason="period_basis_undeclared")
    _expect_error(
        lambda: _build(facts=(_fact(code=_CODE, period=_P1, label="", period_label=_T1),
                              _fact(code=_CODE, period=_P2, label="",
                                    display="1.35 倍", period_label=_T2)),
                       periods=(_P1, _P2)),
        reason="missing_label_or_unit")
    details.append("NOTE 6a：同一格两条事实 / 跨期 label 不一致 / 期间未由 artifact 声明 / "
                   "口径未声明 / 缺行标签，各自 typed 抛错——五种都是数据伤，不是缺席。")

    # 口径不在封闭取值内、以及权威没有 company_id：这两条在**真实投影层**已经拦掉一半，
    # 所以这里用扁平权威直接探本链那一道（纵深防线，不是第一条）。
    flat_task, flat_authority = _authority(facts=_pair(), periods=(_P1, _P2))
    manifest, _draft = _manifest_and_draft(authority=flat_authority, task=flat_task)
    real_artifact = flat_authority.artifact
    bad_facts = tuple(
        SimpleNamespace(fact_id=f.fact_id, kind=f.kind, label=f.label, code=f.code,
                        period=f.period, display=f.display, value_text=f.value_text,
                        unit=f.unit, status=f.status, note=f.note, period_basis="avg",
                        period_label=f.period_label, input_periods=())
        for f in real_artifact.facts)
    bad_artifact = SimpleNamespace(artifact_id=real_artifact.artifact_id,
                                   periods=real_artifact.periods, facts=bad_facts,
                                   gaps=real_artifact.gaps)

    def _flat(artifact, *, company_id: str = T.COMPANY_ID, company_name: str = _CO):
        return SimpleNamespace(producer_kind="financial_workflow", artifact=artifact,
                               company_id=company_id, company_name=company_name,
                               topic_ids=flat_authority.topic_ids,
                               topic_for_fact=flat_authority.topic_for_fact)

    _expect_error(
        lambda: CFT.build_cited_metric_tables(
            section_id=_FIN_SECTION, authority=_flat(bad_artifact), manifest=manifest),
        reason="period_basis_unknown")
    _expect_error(
        lambda: CFT.build_cited_metric_tables(
            section_id=_FIN_SECTION,
            authority=_flat(real_artifact, company_id="", company_name=""),
            manifest=manifest),
        reason="company_id_missing")
    details.append("NOTE 6b：`period_basis` 不在封闭取值内 / 权威没有 `company_id` ⇒ 各自 typed。"
                   "前者在真实投影层已先被拒，本链这一道是**纵深防线**。")

    # ============================================================ §7 双期派生事实
    details.append("## §7 双期派生事实永不进期间轴表格")
    proxy = _Spec(fact_id="proxy-cover-a", label="利息保障倍数", display="3.10 倍", period=_P1,
                  unit=_UNIT, code="SOLV_INTEREST_COVER", period_basis="flow",
                  period_label=_T1, status=NS.PROXY_STATUS, note="代理口径（proxy_input）")
    proxy_b = dataclasses.replace(proxy, fact_id="proxy-cover-b", period=_P2,
                                 period_label=_T2, display="3.25 倍")
    derived = _Spec(fact_id="cover-delta", label="利息保障倍数变动", display="0.15 倍",
                    period=f"{_P2}|{_P1}", unit=_UNIT, code="SOLV_INTEREST_COVER_DELTA",
                    period_basis="flow", period_label="本期|上期",
                    derived_from=("proxy-cover-a", "proxy-cover-b"),
                    input_periods=(_P1, _P2), input_raw_texts=("3.10", "3.25"))
    out_proxy = _build(facts=(proxy, proxy_b), periods=(_P1, _P2))
    check(len(out_proxy.tables) == 1
          and out_proxy.tables[0].rows[0].cells
          == ("3.10 倍。代理口径（proxy_input）", "3.25 倍。代理口径（proxy_input）"),
          f"代理口径事实成表时，**格子自己写出口径**：{out_proxy.tables[0].rows[0].cells}")
    out_d = _build(facts=(proxy, proxy_b, derived), periods=(_P1, _P2))
    check(len(out_d.tables) == 1
          and "cover-delta" not in out_d.tables[0].rows[0].fact_ids,
          "派生事实与两期事实同时在输入里，表里**只有**两期那一格")
    d_row = [r for r in out_d.coverage if r.reason == "derived_two_period_fact"]
    check(len(d_row) == 1 and d_row[0].state == "available_not_displayed",
          f"被挡在表外的派生事实在覆盖读数里有一条去向：{[r.fact_id for r in d_row]}")
    out_d2 = _build(facts=(derived,), periods=(_P1, _P2))
    check([r.reason for r in out_d2.refusals] == ["no_two_period_metric"],
          "只有派生事实时本节**不产生表**（它的期间是复合记号，不属于任何单一列）")
    check(NS.is_derived_two_period_fact(_scan_facts((derived,), (_P1, _P2))[0]),
          "⇒ 判据来自事实自己声明的 `input_periods`（不是 `code`、也不是期间记号的形状）")

    # ============================================================ §8 身份与往返
    details.append("## §8 表格身份内容寻址 + wire 往返")
    again = _build(facts=_pair(), periods=(_P1, _P2)).tables[0]
    check(again.table_id == table.table_id and again.fingerprint() == table.fingerprint(),
          "同一份输入两次构造 ⇒ 表 id 与指纹逐字相同（确定性，无隐藏状态）")
    back = CFT.CitedMetricTable.from_dict(
        json.loads(json.dumps(table.to_dict(), ensure_ascii=False)))
    check(back == table and back.fingerprint() == table.fingerprint(),
          "`to_dict` → `from_dict` 逐字段还原（含 rows 三元组）")
    back_out = CFT.CitedMetricTableOutcome.from_dict(
        json.loads(json.dumps(out_cov.to_dict(), ensure_ascii=False)))
    check(back_out.tables[0] == out_cov.tables[0]
          and back_out.coverage == out_cov.coverage and back_out.refusals == (),
          "产出 `to_dict` → `from_dict` 还原，覆盖读数逐行还原")
    _expect_error(lambda: dataclasses.replace(table, caption="被篡改的表题"),
                  reason="table_id_mismatch")
    _expect_error(lambda: dataclasses.replace(table, header=("指标",)))
    check(True, "只有标签列（没有期间列）不成表，构造期即拒")
    _expect_error(lambda: CFT.CitedMetricTable.from_dict({**table.to_dict(), "extra": 1}))
    check(True, "往返解码拒绝未登记字段（wire 面不留自由扩展位）")
    details.append("NOTE §8：表身份体内含 `rule_version` —— 判据变化会换掉全部表 id，"
                   "因此「同一张表的数字为什么变了」不可能被版式抹平。")

    # ============================================================ §9 读者面
    details.append("## §9 读者面渲染：单位/口径在表题那一行，缺值的格写成「（留空）」")
    gap_specs = (_fact(code=_CODE, period=_P1, display="1.20 倍", period_label=_T1),
                 _fact(code=_CODE, period=_P2, display="1.35 倍", period_label=_T2),
                 _fact(code=_QUICK, period=_P0, label="速动比率", display="0.80 倍",
                       period_label=_T0),
                 _fact(code=_QUICK, period=_P1, label="速动比率", display="0.90 倍",
                       period_label=_T1),
                 _fact(code=_QUICK, period=_P2, label="速动比率", display="0.95 倍",
                       period_label=_T2))
    out_gap = _build(facts=gap_specs, periods=(_P0, _P1, _P2))
    check(len(out_gap.tables) == 1 and len(out_gap.tables[0].rows) == 2,
          "两行指标成一张表（同一单位、同一口径）")
    gap_row = [r for r in out_gap.tables[0].rows if r.label == "流动比率"][0]
    check(out_gap.tables[0].header == (NS.FINANCIAL_TABLE_LABEL_HEADER, _T0, _T1, _T2)
          and gap_row.cells[0] == CFT.CITED_METRIC_TABLE_EMPTY_CELL
          and gap_row.cells[1:3] == ("1.20 倍", "1.35 倍"),
          f"该指标在第一期没有事实 ⇒ 该格**留空**（不得推算/补齐/按公式回填）："
          f"{gap_row.cells}")
    md = CFT.render_cited_metric_table_markdown(out_gap.tables[0])
    check(out_gap.tables[0].caption in md and out_gap.tables[0].entity_scope in md
          and NS.reader_unit_text(_UNIT) in md and "（留空）" in md
          and NS.FINANCIAL_BASIS_TEXTS["end"] in md,
          "渲染包含表题、主体、单位说法、口径说法与「（留空）」")
    details.append("NOTE §9：单位与口径写在**表题那一行**，读者读到数字之前就知道"
                   "这是元还是倍、是时点量还是期间量。")

    # ============================================================ §10 展示层级
    details.append("## §10 `cmtr-3`／`cmtr-4`：代理口径的**整行**进诊断槽位，"
                   "不并入正文指标表")

    #: 一行里**只要有一格**是代理口径，整行进诊断槽位。判据是事实自己的 `status`
    #: （`NS.is_proxy_fact` 读的唯一一个字段），不是指标名、不是公司分支、不是关键词表。
    cover_a = _fact(code="SOLV_INTEREST_COVER", period=_P1, label="利息保障倍数",
                    display="-9.94 倍", unit=_UNIT, basis="end", period_label=_T1,
                    status=NS.PROXY_STATUS, note="代理口径（PROXY_FINANCE_EXPENSES）")
    cover_b = dataclasses.replace(cover_a, fact_id="solv_interest_cover-b",
                                  period=_P2, period_label=_T2, display="-4.21 倍")

    exact_only = _build(facts=_pair(), periods=(_P1, _P2))
    check(len(exact_only.tables) == 1
          and exact_only.tables[0].display_tier == "required_body",
          f"精确口径的两期事实 ⇒ `required_body`："
          f"{[t.display_tier for t in exact_only.tables]}")
    check(not any(NS.PROXY_MARKER_PHRASE in cell
                  for cell in exact_only.tables[0].rows[0].cells),
          "精确口径的行**不得**被挂上任何口径限定语（否则读者会把受审的精确值读成代理值）")

    proxy_only = _build(facts=(cover_a, cover_b), periods=(_P1, _P2))
    check(len(proxy_only.tables) == 1
          and proxy_only.tables[0].display_tier == "diagnostic_only",
          f"代理口径的两期事实 ⇒ `diagnostic_only`："
          f"{[t.display_tier for t in proxy_only.tables]}")
    check(NS.PROXY_MARKER_PHRASE in proxy_only.tables[0].rows[0].cells[0],
          f"分层**不是**把限定语藏起来：代理格照旧逐格写出权威自己的口径："
          f"{proxy_only.tables[0].rows[0].cells}")

    # 关键反例面：两档在同一 `(unit, period_basis)` 上并存 —— 若分组键漏掉 `display_tier`，
    # 代理行就会被并进正文那张表，读者分不出哪一格是代理值。
    mixed = _build(facts=_pair() + (cover_a, cover_b), periods=(_P1, _P2))
    tiers = sorted(t.display_tier for t in mixed.tables)
    check(len(mixed.tables) == 2 and tiers == ["diagnostic_only", "required_body"],
          f"同一单位、同一口径下精确行与代理行**分成两张表**：{tiers}")
    bases = {t.period_basis for t in mixed.tables}
    units = {t.unit for t in mixed.tables}
    check(bases == {"end"} and units == {_UNIT},
          f"两张表的 `unit`/`period_basis` 逐字相同（{units} / {bases}）⇒ 分开它们的**只有**"
          "展示层级这一根轴")
    body_t = [t for t in mixed.tables if t.display_tier == "required_body"][0]
    diag_t = [t for t in mixed.tables if t.display_tier == "diagnostic_only"][0]
    check(diag_t.table_id != body_t.table_id
          and diag_t.table_id not in {t.table_id for t in mixed.tables
                                      if t.display_tier == "required_body"},
          "代理行**不在**任何 `required_body` 表里（它只以诊断表 id 出现）")
    plain_caption = NS.financial_table_caption(unit=_UNIT, basis="end")
    check(body_t.caption == plain_caption
          and diag_t.caption == f"{plain_caption} · {CFT.CITED_METRIC_DIAGNOSTIC_CAPTION_SUFFIX}",
          f"两档表题不同且可区分：{body_t.caption!r} / {diag_t.caption!r}")
    check(body_t.rows[0].label == "流动比率" and diag_t.rows[0].label == "利息保障倍数",
          "两行各自落在自己那一档，没有串表")

    mixed_cov = [r for r in mixed.coverage if r.state == "displayed"]
    cover_rows = [r for r in mixed_cov if r.code == "SOLV_INTEREST_COVER"]
    ratio_rows = [r for r in mixed_cov if r.code == _CODE]
    check(len(cover_rows) == 2 and {r.table_id for r in cover_rows} == {diag_t.table_id}
          and len(ratio_rows) == 2 and {r.table_id for r in ratio_rows} == {body_t.table_id},
          f"覆盖读数的「已展示」各自指向**自己那一档**的表 id："
          f"{[(r.code, r.table_id) for r in mixed_cov]}")
    details.append("NOTE §10a：分层只改**读者面的落位**，不改事实、不改覆盖读数、不改格内文本。"
                   "「格子上带了限定语」不是入场券 —— 一条只由代理口径输入算出来的指标，"
                   "不因为自带 `PROXY_FINANCE_EXPENSES` 标记就进正文指标表。")

    # 表身份：`display_tier` 必须在身份体内，否则「同一张表的数字为什么换了一块」会被版式抹平。
    tier_row = CFT.CitedMetricTableRow(label="流动比率", unit=_UNIT, cells=("1.20 倍", "1.35 倍"),
                                       fact_ids=("a", "b"), citation_keys=("f01", "f02"),
                                       period_texts=(_T1, _T2))
    kw_row = dict(section_id=_FIN_SECTION, caption="占位表题", header=("指标", _T1, _T2),
                  entity_scope="示例（c-1）", unit=_UNIT, period_basis="end",
                  rows=(tier_row,))
    as_body = CFT.CitedMetricTable.create(**kw_row)
    as_diag = CFT.CitedMetricTable.create(**kw_row, display_tier="diagnostic_only",
                                          display_tier_basis=("contract_display_tier",))
    check(as_body.table_id != as_diag.table_id
          and "display_tier" in as_body.identity_body(),
          "`display_tier` 在表身份体内：表题逐字相同、只有层级不同 ⇒ 换掉表 id")
    check(set(CFT.CITED_METRIC_DISPLAY_TIERS) == {"required_body", "diagnostic_only"},
          f"展示层级是**封闭**取值：{list(CFT.CITED_METRIC_DISPLAY_TIERS)}")
    _expect_error(lambda: dataclasses.replace(diag_t, display_tier="diagnostic"), reason="")
    check(True, "未登记的层级（`diagnostic`）被拒：wire 面不留自由扩展位")
    _expect_error(lambda: CFT.CitedMetricTable.create(**kw_row, display_tier="body"), reason="")
    check(True, "构造期同样拒未登记的层级")

    # `cmtr-4`：**进诊断档要有判据**，逐条登记；正文档**不得**带判据。
    # 两条都是「不得凭一个标记就改档」的落地：`diagnostic_only` 没有判据 = 读者看不出
    # 这一行凭什么被挪出正文表；`required_body` 带着判据 = 反过来暗示它本来该进诊断档。
    check(set(CFT.CITED_METRIC_TIER_BASES) == {"contract_display_tier",
                                               "authority_proxy_fact_marker"},
          f"分层判据是**封闭**取值：{list(CFT.CITED_METRIC_TIER_BASES)}")
    _expect_error(lambda: CFT.CitedMetricTable.create(**kw_row, display_tier="diagnostic_only"),
                  reason="tier_basis_missing")
    check(True, "`diagnostic_only` 表没有判据 ⇒ 构造期拒（不得凭一个标记就进诊断档）")
    _expect_error(lambda: CFT.CitedMetricTable.create(
                      **kw_row, display_tier_basis=("contract_display_tier",)),
                  reason="tier_basis_unexpected")
    check(True, "`required_body` 表带判据 ⇒ 构造期拒（正文档不记「凭什么留在这里」）")
    _expect_error(lambda: CFT.CitedMetricTable.create(
                      **kw_row, display_tier="diagnostic_only",
                      display_tier_basis=("contract_display_tier", "unregistered")),
                  reason="tier_basis_unknown")
    check(True, "未登记的分层判据名被拒：wire 面不留自由扩展位")
    _expect_error(lambda: CFT.CitedMetricTable.create(
                      **kw_row, display_tier="diagnostic_only",
                      display_tier_basis=("contract_display_tier", "contract_display_tier")),
                  reason="tier_basis_duplicate")
    check(True, "判据重复被拒（判据是**集合**，不是可以重复计数的权重）")
    check("分层判据" in CFT.render_cited_metric_table_markdown(as_diag),
          "诊断档的判据写进人读版：读者能看到它凭什么被挪出正文表")
    check("分层判据" not in CFT.render_cited_metric_table_markdown(as_body),
          "正文档不写判据行（版面上就不该出现「这一档本来属于哪」）")

    wire = json.loads(json.dumps(diag_t.to_dict(), ensure_ascii=False))
    back_diag = CFT.CitedMetricTable.from_dict(wire)
    check(wire["display_tier"] == "diagnostic_only" and back_diag == diag_t
          and back_diag.display_tier == "diagnostic_only",
          "`display_tier` 进 wire 且 `to_dict` → `from_dict` 逐字段还原")
    body_wire = json.loads(json.dumps(body_t.to_dict(), ensure_ascii=False))
    check(CFT.CitedMetricTable.from_dict(body_wire) == body_t
          and body_wire["display_tier"] == "required_body",
          "`required_body` 同样进 wire 并逐字段还原（不是只在诊断档才写这个字段）")
    # `cmt-2` 的 wire 里这两个字段都没有，而 `table_id` 又是内容寻址的 —— 于是旧 wire 会在
    # **身份核对**处当场被拒。这是版本升级的硬边界，不是静默改档：
    # 一份旧 run 的表不可能被悄悄当成「正文档」重放进读者面。
    legacy = dict(wire)
    legacy.pop("display_tier")
    legacy.pop("display_tier_basis")
    _expect_error(lambda: CFT.CitedMetricTable.from_dict(legacy),
                  reason="table_id_mismatch")
    check(True, "`cmt-2` wire（两个字段都没有）在身份核对处 fail-closed，不会被静默当成正文档")
    # 另一半反例：**只**缺判据、层级还在 —— 这一份 wire 自相矛盾（诊断档说不出凭什么）。
    # 它不得落进「正文档」这个更宽的档里蒙混过去，必须是构造函数自己拒掉。
    half_wire = dict(wire)
    half_wire.pop("display_tier_basis")
    _expect_error(lambda: CFT.CitedMetricTable.from_dict(half_wire),
                  reason="tier_basis_missing")
    check(True, "诊断档 wire 缺判据 ⇒ 构造期即拒（不降格成正文档，不在身份处蒙混）")
    details.append("NOTE §10b：分层不是「渲染时过滤」——它进了表身份体与 wire。"
                   "因此同一份输入在分层前后产出的表 id 不同，"
                   "「这张表原来在正文、现在进了诊断槽位」不可能只体现在版面上。")

    details.append(
        "NOTE 本模块证明的只有「表格只能由本节清单内的合格权威事实构成，且写作结果不参与」。"
        "它不产生正式产物、不宣称财务内容门通过；升格进 `CitedReportVersion` 身份体"
        "（`crpv-2` 的 `metric_table_ids` / `metric_tables_fingerprint`）"
        "由 `evals/test_m930_3_cited_review.py` 那一侧钉。")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

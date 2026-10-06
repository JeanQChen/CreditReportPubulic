"""Eval: M930-3 逐栏目材料契合表（`evaluation/material_column_audit.py`）的聚焦正反例。

用法: python -X utf8 -m evals.test_material_column_audit

被测的是**一个只读渲染层**：它把同一次运行的三份产物（材料包读回、来源清单、验收报告的
导航读数 + 运行 manifest 的 section→topic 归属）摊成"这一栏取到了什么、能不能用、缺什么"。
因此本 eval 守的不是"跑得出文件"，而是下面这些**读数纪律**：

1. **档位与判据都是封闭集合**：任何一条材料的 `fit` / `fit_reason`、任何一栏的缺口类型都
   必须落在模块声明的闭集里，不得散成自由字符串；
2. **六种形态各自判对**：本层键命中→`相关`；只命中祖先层键→`弱相关`；两者都不命中→`误召`；
   读不出定位符→`定位不可判定`（**不是** `误召`）；Pack 侧拒绝→`被拒绝`；缺处置记录→
   `无处置记录`。后两种**优先于**结构判定（拒绝/缺记录是更强的事实）；
3. **`null` 不是 `false`**：`in_writer_manifest` 为 `null` 时只能说"不可判定"，
   **不得**写成"不在 Writer 清单里"；
4. **读不出 ≠ 原文为空**：`text_status != resolved` 的读数两侧都保留这个区分；
5. **勾选表单行只能作适用状态**：`selection_form` 的"能证明"里必须带 `permitted_use`，
   "不能证明"里必须写"不得当作本栏业务事实正文"并列出登记过的排除项；
6. **读不回来 ≠ 没有材料**：材料包整体读不出时 `status=unavailable` 且**不产出**逐栏目
   读数；某一节的读回状态不是 `readback` 时，该节各栏必须带 `pack_section_unavailable`，
   不得只说 `no_material`；
7. **未绑定 aspect 的材料不进任何栏目**，但必须列在 `unbound_materials` 里；
8. **登记 ≠ 消费**：登记了但没有材料的文档记 `registered_not_consumed`；有材料但登记侧
   没有的记 `unregistered_material_source`；
9. **跨页连续阅读视图**：只收"同文档同节、页号相差 1、且至少共享一条 aspect 绑定"的
   相邻两块；同页两块、跨 3 页两块都**不**成组；原文逐字保留——**不得**出现"两段拼起来"
   的形态（`text_a + text_b` 不得在渲染里出现）；
10. **规则版本改变判定要可见**：同一条材料在"现行规则"与"本次运行记录的规则形态"下档位
    不同时，`fit_changed_by_rule_version` 必须为真，且人读版要把两套档位都印出来；
11. **Contract 身份只能间接核对**：逐 aspect 键比对发现不一致时必须记进
    `contract_agreement` 并在 `notes` 里明说"在澄清之前读数必须打折"，**不得**沉默；
12. **确定性**：同一份输入连续构建两次，载荷必须逐字节相等（只读、无随机、无时钟）。

合成夹具的 aspect / topic 一律用**专属命名空间**（`synth_*`），不依赖真实 Contract 的
任何 aspect id；需要真实 Contract 的两条（11 与 10）从**运行时的冻结资产**里现取 aspect，
不写死任何公司、页码或答案词。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import material_column_audit as MCA
from document_structure import navigation as NAV

REPO = Path(__file__).resolve().parent.parent
CONTRACT_ASSET = REPO / MCA.DEFAULT_CONTRACT_ASSET


# --------------------------------------------------------------------------------------
# 合成夹具
# --------------------------------------------------------------------------------------

def _entry(material_id: str, *, topic_id: str = "synth_readback",
           section_id: str = "company", document_id: str = "SYN_DOC_A",
           document_version: str = "sha256-aaaa", page: int = 1, offset: int = 0,
           section_path: Any = "第一章总则 / 乙章节 / 甲栏目",
           table_title: Any = None, aspect_ids: Any = ("synth_readback.demo",),
           admission_state: str = "admitted", retention_state: str = "retained",
           text: str = "甲栏目原文。", text_status: str = "resolved",
           in_writer_manifest: Any = None,
           writer_manifest_query: str = "not_available",
           kind: str = "text", kind_in_vocabulary: bool = True,
           selection: Any = None, with_disposition: bool = True,
           identity_basis: str = "source_set_member_same_id_and_version",
           evidence_set_version: Any = "set-synth") -> dict:
    """一条材料读回载荷（字段与真实 `material_pack.json` 的条目同形）。"""
    locator: dict = {"locator_type": "evidence", "document_id": document_id,
                     "document_version": document_version, "section_path": section_path,
                     "page": page, "table_title": table_title,
                     "block_range": [0, 0], "offset": offset}
    row: dict = {
        "section_id": section_id, "topic_id": topic_id, "pack_id": "pack-synth",
        "material_id": material_id, "material_type": "evidence_span",
        "content_hash": f"hash-{material_id}",
        "document_identity": {"company_id": "SYNCO", "document_id": document_id,
                              "document_version": document_version,
                              "evidence_set_version": evidence_set_version},
        "document_identity_basis": identity_basis,
        "locator": locator,
        "aspect_ids_from_aspect_results": list(aspect_ids),
        "in_writer_manifest": in_writer_manifest,
        "writer_manifest_query": writer_manifest_query,
        "content_qualification": {
            "kind": kind, "kind_in_vocabulary": kind_in_vocabulary,
            "kind_basis": "envelope", "selection": selection,
            "selection_basis": None if kind != "selection_form" else "node_title"},
        "text": text, "text_chars": len(text), "text_status": text_status,
    }
    if with_disposition:
        row["disposition"] = {
            "disposition_id": f"disp-{material_id}", "admission_state": admission_state,
            "retention_state": retention_state, "source_validation": "validated",
            "reason_code": ("aspect_material_admitted" if admission_state == "admitted"
                            else "aspect_material_rejected"),
            "reason_proof": "夹具理由", "policy_version": "md-1",
            "aspect_ids": list(aspect_ids)}
        row["retention_destination"] = ("retained_for_writing"
                                        if retention_state == "retained"
                                        else "dropped")
    return row


def _selection_columns() -> dict:
    return {
        # `tmr-3`：所问事项只取**行内前缀**（`in_span`），或留空；`node_title` 已不再是
        # 合法出处——拿节点标题顶替会把子项状态锚到整个栏目上。这条夹具的所问事项写在行内。
        "asked_item": "（2）占营业收入或营业利润10%以上的行业、产品情况",
        "asked_item_scope": "in_span",
        "options": [{"marker": "☑", "marker_state": "marked", "label": "适用",
                     "selected": True},
                    {"marker": "□", "marker_state": "hollow", "label": "不适用",
                     "selected": False}],
        "selected_labels": ["适用"],
        "node": {"section_path": "第九节 / 戊章节 / 己小节"},
        "source": {"source_identity": "evidence:synth", "document_id": "SYN_DOC_A",
                   "document_version": "sha256-aaaa", "page": 9,
                   "block_range": [1, 1], "offset": 96},
        "permitted_use": "asked_item_applicability_only",
        "exclusions": ["no_negation_beyond_marked_state",
                       "not_a_broad_column_coverage_proof",
                       "not_narrative_prose"],
    }


def _pack() -> dict:
    company_entries = [
        # 本层键命中 → 相关
        _entry("mat-own", aspect_ids=("synth_readback.demo",), page=10,
               section_path="第一章总则 / 乙章节 / 甲栏目", text="甲栏目原文一。"),
        # 只命中祖先层键 → 弱相关
        _entry("mat-ancestor", aspect_ids=("synth_readback.demo",), page=12,
               section_path="第一章总则 / 乙章节 / 丙小节", text="乙章节正文二。"),
        # 两者都不命中 → 误召
        _entry("mat-foreign", aspect_ids=("synth_readback.demo",), page=30,
               section_path="第九节 / 丁章节", text="丁章节正文。"),
        # 读不出定位符 → 定位不可判定
        _entry("mat-nolocator", aspect_ids=("synth_readback.demo",), page=None,
               section_path=None, table_title=None, text="无定位符原文。"),
        # Pack 侧拒绝 → 被拒绝
        _entry("mat-rejected", aspect_ids=("synth_readback.demo",),
               admission_state="rejected", retention_state="dropped",
               section_path="第一章总则 / 乙章节 / 甲栏目", text="被拒绝原文。"),
        # 缺处置记录 → 无处置记录
        _entry("mat-nodisp", aspect_ids=("synth_readback.demo",), with_disposition=False,
               section_path="第一章总则 / 乙章节 / 甲栏目", text="缺处置记录原文。"),
        # in_writer_manifest 为 null（不是 false）
        _entry("mat-manifest-null", aspect_ids=("synth_readback.demo",), page=41,
               section_path="第一章总则 / 乙章节 / 甲栏目", text="清单不可判定原文。"),
        # 原文回查不到（不是「原文为空」）
        _entry("mat-notext", aspect_ids=("synth_readback.demo",), page=42,
               section_path="第一章总则 / 乙章节 / 甲栏目", text_status="unavailable",
               text="", in_writer_manifest=False, writer_manifest_query="checked"),
        # 勾选表单行（只能作适用状态）
        _entry("mat-selection", aspect_ids=("synth_readback.demo",), page=9,
               section_path="第九节 / 戊章节 / 己小节", text="☑ 适用",
               kind="selection_form", selection=_selection_columns()),
        # 未绑定 aspect（不得进任何栏目）
        _entry("mat-unbound", aspect_ids=(), page=50,
               section_path="第一章总则 / 乙章节 / 甲栏目", text="未绑定原文。"),
        # 形态读不懂
        _entry("mat-unreadable", aspect_ids=("synth_readback.demo",), page=51,
               section_path="第一章总则 / 乙章节 / 甲栏目", kind="mystery",
               kind_in_vocabulary=False, text="形态不可读原文。"),
        # 跨页连续阅读：p60 → p61（页号相差 1、共享 aspect 绑定）→ 成组
        _entry("mat-cont-a", aspect_ids=("synth_readback.demo",), page=60, offset=1123,
               section_path="第一章总则 / 乙章节 / 甲栏目", text="跨页碎片甲。"),
        _entry("mat-cont-b", aspect_ids=("synth_readback.demo",), page=61, offset=170,
               section_path="第一章总则 / 乙章节 / 甲栏目", text="跨页碎片乙。"),
        # 同页两块 → 不成组
        _entry("mat-samepage-a", aspect_ids=("synth_readback.demo",), page=70, offset=10,
               section_path="第一章总则 / 乙章节 / 甲栏目", text="同页甲。"),
        _entry("mat-samepage-b", aspect_ids=("synth_readback.demo",), page=70, offset=99,
               section_path="第一章总则 / 乙章节 / 甲栏目", text="同页乙。"),
        # 跨 3 页两块 → 不成组
        _entry("mat-far-a", aspect_ids=("synth_readback.demo",), page=80, offset=10,
               section_path="第一章总则 / 乙章节 / 甲栏目", text="跨三页甲。"),
        _entry("mat-far-b", aspect_ids=("synth_readback.demo",), page=83, offset=10,
               section_path="第一章总则 / 乙章节 / 甲栏目", text="跨三页乙。"),
        # 另一份文档（登记侧没有 → unregistered_material_source）
        _entry("mat-extra-doc", aspect_ids=("synth_readback.demo",),
               document_id="SYN_DOC_EXTRA", document_version="sha256-cccc", page=5,
               section_path="第一章总则 / 乙章节 / 甲栏目", text="未登记文档原文。"),
    ]
    industry_entries = [
        _entry("mat-industry", topic_id="synth_industry", section_id="industry",
               aspect_ids=("synth_industry.demo",), page=17,
               section_path="第一章总则 / 乙章节", text="行业片段。"),
    ]
    #: 有材料、但**本层键一条不中**的栏：用来正面验证 `no_on_topic_material` 缺口，
    #: 且与「本栏确有本层取材」的栏形成对照（后者不得凭空记缺口）。
    company_entries.append(
        _entry("mat-sibling-only", aspect_ids=("synth_readback.sibling_only",),
               page=90, section_path="第一章总则 / 乙章节", text="兄弟栏取材原文。"))
    return {
        "schema_version": "material-pack/2", "status": "readback",
        "raw_text_field": "text",
        "sections": {
            "company": {"status": "readback", "section_id": "company",
                        "entry_count": len(company_entries), "entries": company_entries},
            "financial": {"status": "unavailable", "section_id": "financial",
                          "detail": "本节没有可用 Pack 集（pack_set=无，topic 数 1）"},
            "industry": {"status": "readback", "section_id": "industry",
                         "entry_count": len(industry_entries),
                         "entries": industry_entries},
        },
        "entry_count": len(company_entries) + len(industry_entries),
        "sections_readback": ["company", "industry"],
        "sections_unavailable": ["financial"],
        "note": "夹具材料包读回。",
    }


def _report(nav_rows: list[dict], declarations: list[dict]) -> dict:
    return {
        "schema_version": "acceptance-report/1", "status": "fail",
        "observations": {
            "company_material_source_reconciliation": {
                "status": "read_only_diagnostic", "section_id": "company",
                "requirements": [{"topic_id": "synth_readback",
                                  "question_ids": ["synth_readback_q"],
                                  "aspect_count": len(declarations),
                                  "aspects": declarations}],
                "tree_navigation": nav_rows,
            },
        },
    }


def _nav_row(aspect_id: str, *, topic_id: str, nav_keys: list, parent_keys: list,
             status: str = "selected", fallback_reason: Any = None,
             titles: tuple = (), read_node_count: int = 1,
             rule_version: str = "anp-5") -> dict:
    return {
        "topic_id": topic_id, "aspect_id": aspect_id,
        "nav_keys": list(nav_keys), "parent_keys": list(parent_keys),
        "key_tier": "declared", "rule_version": rule_version, "status": status,
        "fallback_reason": fallback_reason,
        "selected_node_id": ("on-synth" if status == "selected" else None),
        "band_node_ids": [], "read_root_node_ids": [],
        "climbed": False, "read_node_count": read_node_count,
        "unread_node_ids": [], "unread_total": 0,
        "candidates": [{"node_id": f"on-{t}", "title": t, "score": 1.0,
                        "in_read_set": True, "discard_reason": None} for t in titles],
    }


def _manifest(*, contract_version: str = "v2",
              company_topics: tuple = ("synth_readback",)) -> dict:
    return {
        "milestone": "M930-3", "runner_version": "fixture",
        "contract": {"version": contract_version, "fingerprint": "f" * 64},
        "sections": {
            "company": {"title": "公司", "topic_ids": list(company_topics)},
            "financial": {"title": "财务", "topic_ids": ["synth_finance_topic"]},
            "industry": {"title": "行业", "topic_ids": ["synth_industry"]},
        },
    }


def _source_manifest() -> dict:
    return {
        "policy_version": "sm-2", "company_id": "SYNCO",
        "primary_document_id": "SYN_DOC_A", "primary_document_version": "sha256-aaaa",
        "current_state": "resolved",
        "entries": [
            {"document_id": "SYN_DOC_A", "document_version": "sha256-aaaa",
             "source_name": "A.pdf", "registered_source_type": "annual_report",
             "evidence_set_version": "set-synth", "eligibility": "eligible_current",
             "type_judgment": {"document_class": "年度报告"},
             "content_report_period": {"period": "2025-01-01..2025-12-31",
                                       "state": "verified"}},
            {"document_id": "SYN_DOC_EMPTY", "document_version": "sha256-bbbb",
             "source_name": "B.pdf", "registered_source_type": "other",
             "evidence_set_version": "set-synth", "eligibility": "eligible_current",
             "type_judgment": {"document_class": "债券募集说明书"},
             "content_report_period": {"period": None, "state": "unknown"}},
        ],
        "selection": [
            {"document_id": "SYN_DOC_A", "document_version": "sha256-aaaa",
             "selection_state": "selected_source_set", "source_role": "current_state_source",
             "reason_code": "current_state_source_anchor", "retrieval_order": 0,
             "evidence_set_version": "set-synth"},
            {"document_id": "SYN_DOC_EMPTY", "document_version": "sha256-bbbb",
             "selection_state": "selected_source_set",
             "source_role": "topic_participating_source",
             "reason_code": "different_series_topic_participating",
             "retrieval_order": 1, "evidence_set_version": "set-synth"},
        ],
        "registration_class_mismatches": [],
        "provenance_findings": ["夹具 provenance 读数。"],
        "document_keys": [],
    }


_FIXTURE = object()   #: 「没传就是写着夹具那一份」；`None` 表示**故意不写这个文件**


def _write_run(root: Path, *, pack: Any = _FIXTURE, report: Any = None,
               manifest: Any = None, source: Any = None) -> Path:
    run = root / "run"
    run.mkdir(parents=True, exist_ok=True)
    artifacts = {"material_pack.json": _pack() if pack is _FIXTURE else pack,
                 "acceptance_report.json": report, "manifest.json": manifest,
                 "source_manifest.json": source if source is not None
                 else _source_manifest()}
    for name, payload in artifacts.items():
        if payload is None:
            continue
        (run / name).write_text(json.dumps(payload, ensure_ascii=False),
                                encoding="utf-8")
    return run


def _declaration(aspect_id: str, question_id: str, requirement_text: str,
                 kind: str = "fact_set") -> dict:
    return {"aspect_id": aspect_id, "question_id": question_id,
            "kind": kind, "content_role": "paragraph", "output_destination": "body",
            "missing_policy": "write_not_found",
            "time_scope": "CURRENT_AS_OF_WITH_24M_CHANGES",
            "requirement_text": requirement_text}


def _fixture_report() -> dict:
    return _report(
        nav_rows=[
            _nav_row("synth_readback.demo", topic_id="synth_readback",
                     nav_keys=["甲栏目"], parent_keys=["乙章节"],
                     titles=("甲栏目",)),
            _nav_row("synth_readback.sibling_only", topic_id="synth_readback",
                     nav_keys=["庚栏目"], parent_keys=["乙章节"]),
            _nav_row("synth_readback.other", topic_id="synth_readback",
                     nav_keys=["癸栏目"], parent_keys=["乙章节"], status="fallback",
                     fallback_reason="low_confidence", read_node_count=0),
            _nav_row("synth_finance_topic.demo", topic_id="synth_finance_topic",
                     nav_keys=["壬栏目"], parent_keys=["辛章节"]),
        ],
        declarations=[
            _declaration("synth_readback.demo", "synth_readback_q", "合成栏目：甲栏目"),
            _declaration("synth_readback.sibling_only", "synth_readback_q",
                         "合成栏目：庚栏目"),
            _declaration("synth_readback.other", "synth_readback_q", "合成栏目：癸栏目"),
            _declaration("synth_finance_topic.demo", "synth_finance_q", "合成财务栏目：壬栏目",
                         kind="financial_metric"),
        ])


# --------------------------------------------------------------------------------------
# 用例
# --------------------------------------------------------------------------------------

def _by_id(payload: dict) -> dict:
    return {column["aspect_id"]: column for column in payload["columns"]}


def _rows(payload: dict, aspect_id: str) -> dict:
    return {row["material_id"]: row for row in _by_id(payload)[aspect_id]["materials"]}


def _closed_sets(payload: dict, check, details) -> None:
    seen_fits, seen_reasons, seen_gaps = set(), set(), set()
    for column in payload["columns"]:
        for row in column["materials"]:
            seen_fits.add(row["fit"])
            seen_reasons.add(row["fit_reason"])
            seen_fits.add(row["fit_run_rule"])
            seen_reasons.add(row["fit_run_rule_reason"])
        for kind in ((column.get("gap") or {}).get("kinds") or ()):
            seen_gaps.add(kind)
    check(seen_fits <= set(MCA.FIT_LABELS),
          f"档位全部落在闭集内（越界：{sorted(seen_fits - set(MCA.FIT_LABELS))}）")
    check(seen_reasons <= set(MCA.FIT_REASONS),
          f"判据全部落在闭集内（越界：{sorted(seen_reasons - set(MCA.FIT_REASONS))}）")
    check(seen_gaps <= set(MCA.GAP_KINDS),
          f"缺口类型全部落在闭集内（越界：{sorted(seen_gaps - set(MCA.GAP_KINDS))}）")
    check(set(payload["fit_labels"]) == set(MCA.FIT_LABELS), "载荷声明的档位闭集与模块一致")


def _kinds(check, details) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run = _write_run(Path(tmp), report=_fixture_report(),
                         manifest=_manifest())
        payload = MCA.build(run)
        _closed_sets(payload, check, details)
        check(payload["status"] == "partial",
              "financial 节读回不可用 ⇒ 载荷 status=partial（不是 readback）")
        rows = _rows(payload, "synth_readback.demo")
        #: 合成 aspect **不在冻结 Contract 里**，所以祖先层键派生不出来 ⇒ 除本层键命中外，
        #: 只能记 `祖先层不可判定`（这一组的正面 `弱相关` / `误召` 见 `_real_aspect_cases`）。
        expected = {
            "mat-own": ("相关", "own_section_key"),
            "mat-ancestor": ("祖先层不可判定", "ancestor_keys_unavailable"),
            "mat-foreign": ("祖先层不可判定", "ancestor_keys_unavailable"),
            "mat-nolocator": ("定位不可判定", "locator_unavailable"),
            "mat-rejected": ("被拒绝", "rejected_by_pack"),
            "mat-nodisp": ("无处置记录", "no_disposition"),
        }
        for material_id, (fit, reason) in expected.items():
            row = rows.get(material_id) or {}
            check(row.get("fit") == fit and row.get("fit_reason") == reason,
                  f"{material_id} 判为 {fit}/{reason}（实得 "
                  f"{row.get('fit')}/{row.get('fit_reason')}）")
        check(rows["mat-rejected"]["fit_reason"] == "rejected_by_pack"
              and rows["mat-rejected"]["own_key_hits"] != [],
              "被拒绝优先于结构判定：即使章节整段命中本层键，仍记 `被拒绝`")
        check(rows["mat-nolocator"]["locator_reason"] is not None,
              "定位不可判定的读数里保留 typed 的 locator 原因")
        check(rows["mat-ancestor"]["fit"] == "祖先层不可判定"
              and rows["mat-ancestor"]["fit_run_rule"] == "弱相关"
              and rows["mat-ancestor"]["fit_changed_by_rule_version"] is True,
              "派生不出祖先层键时**不得**用运行记录形态顶替：`fit` 记不可判定，"
              "运行记录形态的档位另存 `fit_run_rule`")
        check("无从判定" in " ".join(rows["mat-ancestor"]["cannot_prove"]),
              "该档的「不能证明」写清是「无从判定」而不是「不属于本栏」")
        check(rows["mat-foreign"]["locator_reason"] is None
              and rows["mat-foreign"]["own_key_hits"] == [],
              "仍保留定位符读数（本层键未命中）")
        demo_gap = _by_id(payload)["synth_readback.demo"]["gap"]
        check(demo_gap is not None and demo_gap["kinds"] == ["text_unavailable"],
              "本栏有本层取材 ⇒ 不记 `no_on_topic_material` / `no_material`；"
              "但有一条已接纳材料原文回查不到，仍如实记 `text_unavailable`"
              f"（实得 {(demo_gap or {}).get('kinds')}）")
        sibling_only = _by_id(payload)["synth_readback.sibling_only"]
        check(sibling_only["gap"] is not None
              and sibling_only["gap"]["kinds"] == ["no_on_topic_material"],
              "没有本层取材的栏记 `no_on_topic_material`（且只记这一条缺口）")
        check(sibling_only["counts"]["相关"] == 0,
              "该栏本层可取材为 0（有材料 ≠ 有本层取材）")
        other = _by_id(payload)["synth_readback.other"]
        check(other["materials"] == []
              and set(other["gap"]["kinds"]) == {"navigation_fallback", "no_material"},
              "无材料且导航全 fallback 的栏：缺口同时记 `navigation_fallback` 与 "
              "`no_material`（一条材料都没有 ≠ 读不回来）")


def _null_not_false(check, details) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run = _write_run(Path(tmp), report=_fixture_report(), manifest=_manifest())
        payload = MCA.build(run)
        rows = _rows(payload, "synth_readback.demo")
        null_row = rows["mat-manifest-null"]
        check(null_row["in_writer_manifest"] is None
              and null_row["writer_manifest_query"] == "not_available",
              "夹具：清单读数确为 null + not_available")
        text = " ".join(null_row["cannot_prove"])
        check("不可判定" in text and "null" in text,
              "null 只读成「不可判定」，并明说 `null` 不是 `false`")
        check("不在本节的 Writer 精确材料清单里" not in text,
              "null **不得**渲染成「不在 Writer 清单里」")
        false_row = rows["mat-notext"]
        check(false_row["in_writer_manifest"] is False
              and "不在本节的 Writer 精确材料清单里"
              in " ".join(false_row["cannot_prove"]),
              "确为 false 时照实写「不在清单里」")
        check("回查不到" in " ".join(false_row["can_prove"] + false_row["cannot_prove"])
              and "原文为空" in " ".join(false_row["can_prove"] + false_row["cannot_prove"]),
              "读不出原文的读数保留「读不出 ≠ 原文为空」的区分")
        md = MCA.render_md(payload)
        check("进 Writer 清单：`null`" in md and "进 Writer 清单：`false`" in md,
              "人读版按 JSON 原样印 `null` / `false`（不把 `null` 印成 `None`）")
        check("`0/0` 表示本轮**没有**" in md and "不是**「进了 0 条」" in md,
              "文档表给「已计入 / 已核对」逐列读法，避免把 `0/0` 读成「进了 0 条」")


def _selection_only(check, details) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run = _write_run(Path(tmp), report=_fixture_report(), manifest=_manifest())
        payload = MCA.build(run)
        row = _rows(payload, "synth_readback.demo")["mat-selection"]
        can, cannot = " ".join(row["can_prove"]), " ".join(row["cannot_prove"])
        check("适用状态" in can and "asked_item_applicability_only" in can,
              "勾选表单行的「能证明」只到适用状态，并带 permitted_use")
        check("当作本栏业务事实正文" in cannot,
              "勾选表单行的「不能证明」明说不得当业务正文")
        for exclusion in _selection_columns()["exclusions"]:
            check(exclusion in cannot, f"登记过的排除项逐条印出：{exclusion}")
        check("金额" in cannot and "占比" in cannot,
              "勾选表单行不得承载金额 / 占比这类事实")


def _unreadable_kind(check, details) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run = _write_run(Path(tmp), report=_fixture_report(), manifest=_manifest())
        payload = MCA.build(run)
        row = _rows(payload, "synth_readback.demo")["mat-unreadable"]
        check("读不懂" in " ".join(row["can_prove"]),
              "形态读不懂的材料只能说「不能证明任何事实」")
        check(row["content_kind_in_vocabulary"] is False,
              "形态读不懂的读数保留 `kind_in_vocabulary=False`")


def _four_axis_columns(check, details) -> None:
    """四列**互不推出**：结构匹配 / 正文相关性 / 事实资格 / 实际写作采用。

    这一组防的是把第①列（章节标题标签段是否整段相等）当语义资格用的错读：
    「标题不叫『采购模式』⇒ 说这一栏没有材料」，以及反向的
    「标题对得上 ⇒ 以为这句原文有用」。
    """
    check(MCA.COLUMN_AUDIT_SCHEMA_VERSION == "material-column-audit/3",
          "四列同表 ⇒ 读数结构版本进到 `material-column-audit/3`")
    check(set(MCA.BODY_RELEVANCE_VALUES) == {
        "narrative_prose", "disclosure_state_only", "other_form", "text_unreadable",
        "kind_unreadable", "no_disposition"},
        "第②列闭集就是这六档（改档位必须同时改本 eval）")
    check(set(MCA.FACT_ELIGIBILITY_VALUES) == {
        "fact_candidate_eligible", "disclosure_state_only", "other_form", "not_admitted",
        "not_retained", "text_unreadable", "kind_unreadable", "no_disposition"},
        "第③列闭集就是这八档（改档位必须同时改本 eval）")
    check(set(MCA.WRITING_ADOPTION_VALUES) == {
        "in_writer_manifest", "not_in_writer_manifest", "manifest_unreadable"},
        "第④列闭集就是这三档（`manifest_unreadable` **不是**「没进」）")
    check(MCA.BODY_RELEVANCE_SEMANTIC_FIELD == "not_judged_here",
          "第②列的语义那一半恒为 `not_judged_here`——本表不判「原文是否在讲本栏那件事」")
    check(MCA.STRUCTURAL_MATCH_BASIS == "section_title_label_segment_equality",
          "第①列的判据写明只到「章节标题完整标签段整段相等」")

    with tempfile.TemporaryDirectory() as tmp:
        run = _write_run(Path(tmp), report=_fixture_report(), manifest=_manifest())
        payload = MCA.build(run)
        rows = _rows(payload, "synth_readback.demo")
        by_id = _by_id(payload)

        check(payload["schema_version"] == MCA.COLUMN_AUDIT_SCHEMA_VERSION,
              "载荷声明的结构版本与模块一致")
        check(payload["body_relevance_semantic_field"] == "not_judged_here"
              and payload["writing_adoption_values"] == list(MCA.WRITING_ADOPTION_VALUES)
              and payload["structural_match_basis"] == MCA.STRUCTURAL_MATCH_BASIS,
              "载荷同时声明四条轴的闭集与判据（读的人不必翻源码）")

        # --- 逐行：四列都在、都落在闭集里、都带判据 ------------------------------------
        bad_value, bad_basis, bad_semantic, missing = [], [], [], []
        for column in payload["columns"]:
            for row in column["materials"]:
                for axis, vocab in (("structural_match", MCA.FIT_LABELS),
                                    ("body_relevance", MCA.BODY_RELEVANCE_VALUES),
                                    ("fact_eligibility", MCA.FACT_ELIGIBILITY_VALUES),
                                    ("writing_adoption", MCA.WRITING_ADOPTION_VALUES)):
                    cell = row.get(axis)
                    if not isinstance(cell, dict) or "value" not in cell:
                        missing.append((row.get("material_id"), axis))
                        continue
                    if cell["value"] not in set(vocab):
                        bad_value.append((row.get("material_id"), axis, cell["value"]))
                    if not str(cell.get("basis", "") or ""):
                        bad_basis.append((row.get("material_id"), axis))
                if (row.get("body_relevance") or {}).get(
                        "semantic_relevance") != "not_judged_here":
                    bad_semantic.append(row.get("material_id"))
        check(not missing, f"每条材料都有四列读数（缺：{missing[:4]}）")
        check(not bad_value, f"四列读数全部落在闭集内（越界：{bad_value[:4]}）")
        check(not bad_basis, f"四列读数每格都带 `basis`（缺：{bad_basis[:4]}）")
        check(not bad_semantic,
              f"四列的每格都标明语义那一半没判（`not_judged_here`，越界：{bad_semantic[:4]}）")

        # --- 逐材料的四读取数 ----------------------------------------------------------
        expected = {
            # 素材：admitted+retained+text+原文可回查
            "mat-own": ("相关", "narrative_prose", "fact_candidate_eligible",
                        "manifest_unreadable"),
            "mat-rejected": (None, "narrative_prose", "not_admitted", "manifest_unreadable"),
            "mat-nodisp": (None, "no_disposition", "no_disposition", "manifest_unreadable"),
            "mat-selection": (None, "disclosure_state_only", "disclosure_state_only",
                              "manifest_unreadable"),
            "mat-unreadable": (None, "kind_unreadable", "kind_unreadable",
                               "manifest_unreadable"),
            "mat-notext": (None, "text_unreadable", "text_unreadable",
                           "not_in_writer_manifest"),
        }
        for material_id, (fit, body, fact, adopt) in expected.items():
            row = rows.get(material_id) or {}
            got = (row.get("structural_match", {}).get("value"),
                   row.get("body_relevance", {}).get("value"),
                   row.get("fact_eligibility", {}).get("value"),
                   row.get("writing_adoption", {}).get("value"))
            want = (fit if fit is not None else got[0], body, fact, adopt)
            check(got == want,
                  f"{material_id} 的四列读作 {want}（实得 {got}）")
        check(rows["mat-own"]["structural_match"]["value"] == "相关"
              and rows["mat-own"]["writing_adoption"]["value"] == "manifest_unreadable",
              "第①列 `相关` **推不出**第④列「进了清单」——本轮没有可核对的清单就是不可判定")
        check(rows["mat-rejected"]["structural_match"]["value"] == "被拒绝"
              and rows["mat-rejected"]["body_relevance"]["value"] == "narrative_prose",
              "Pack 侧被拒（第①列记 `被拒绝`）**不改变**原文形态读数：第②列照实记散文")

        # --- 反坍缩：①「不是本层键」不得把 ②③ 压成否定 --------------------------------
        loose = [r for r in rows.values()
                 if r["structural_match"]["value"] != "相关"
                 and r["body_relevance"]["value"] == "narrative_prose"
                 and r["fact_eligibility"]["value"] == "fact_candidate_eligible"]
        check(len(loose) >= 2,
              "存在「第①列不是 `相关`、但第②③列仍是可用的散文 / 可提候选」的材料"
              f"（实得 {len(loose)} 条，如 {[r['material_id'] for r in loose][:3]}）")
        check(all("不是" in r["structural_match"]["note"] or "误召" in r["structural_match"]["note"]
                  for r in loose),
              "这些材料的第①列读数自带「不是『内容与本栏无关』」的读法说明")
        if loose:
            sample = loose[0]
            check("不等于" in sample["body_relevance"]["note"]
                  and "逐字读原文" in sample["body_relevance"]["note"],
                  "第②列读数明说「形态上是散文」不等于「原文在讲本栏那件事」")
            check("不判" in sample["fact_eligibility"]["note"],
                  "第③列读数明说不替绑定门 / 蕴含门下判")

        # --- `null` ≠ `false`（第④列） -------------------------------------------------
        null_row, false_row = rows["mat-manifest-null"], rows["mat-notext"]
        check(null_row["writing_adoption"]["value"] == "manifest_unreadable"
              and "不可判定" in null_row["writing_adoption"]["reason"]
              and "null" in null_row["writing_adoption"]["reason"],
              "清单读数为 null ⇒ 第④列记「不可判定」，并明说 `null` 不是 `false`")
        check("不在本节的 Writer 精确材料清单里"
              not in null_row["writing_adoption"]["reason"],
              "第④列**不得**把 null 渲染成「不在清单里」")
        check(false_row["writing_adoption"]["value"] == "not_in_writer_manifest"
              and "不在本节的 Writer 精确材料清单里"
              in false_row["writing_adoption"]["reason"],
              "确为 false 时第④列照实记「不在清单里」")

        # --- 逐栏计数：四条轴各自闭集全键在册，且与行数对得上 --------------------------
        bad_sum, bad_own_key, bad_count = [], [], []
        for column in payload["columns"]:
            counts = column.get("four_column_counts") or {}
            for axis, vocab in (("structural_match", MCA.FIT_LABELS),
                                ("body_relevance", MCA.BODY_RELEVANCE_VALUES),
                                ("fact_eligibility", MCA.FACT_ELIGIBILITY_VALUES),
                                ("writing_adoption", MCA.WRITING_ADOPTION_VALUES)):
                cell = counts.get(axis) or {}
                if set(cell) != set(vocab):
                    bad_own_key.append((column["aspect_id"], axis))
                    continue
                if sum(cell.values()) != len(column["materials"]):
                    bad_sum.append((column["aspect_id"], axis))
            if (counts.get("structural_match") or {}) != column["counts"]:
                bad_count.append(column["aspect_id"])
        check(not bad_own_key, f"逐栏四条轴都是闭集全键在册（缺键：{bad_own_key[:4]}）")
        check(not bad_sum, f"逐栏四条轴的计数之和等于该栏材料数（不符：{bad_sum[:4]}）")
        check(not bad_count,
              f"第①列的逐栏计数与原有 `counts` 逐字一致（不符：{bad_count[:4]}）")

        # --- 人读版：逐材料四行 + 四列互不推出的读法说明 --------------------------------
        md = MCA.render_md(payload)
        check("① 结构匹配：" in md and "② 正文相关性：" in md
              and "③ 事实资格：" in md and "④ 实际写作采用：" in md,
              "人读版逐材料印出四列读数（不是只印第一列）")
        check("四列互不推出" in md and "material-column-audit/3" in md,
              "人读版写明四列互不推出，并标出结构版本")
        check("标题不叫『采购模式』" in md,
              "人读版给出「把①当②用」的具体错读例子")
        check("`manifest_unreadable` 是**不可判定**" in md,
              "人读版对第④列的不可判定单独说明")
        check(md.count("语义那一半") >= 2,
              "人读版逐栏与逐材料都标出第②列的语义那一半没判")

        # --- 指纹：四列都进 diff 行 ----------------------------------------------------
        line = next((l for l in payload["material_fitness_lines"]
                     if l.startswith("synth_readback.demo|mat-own|")), "")
        check(line.endswith("|narrative_prose|fact_candidate_eligible|manifest_unreadable"),
              f"绑定指纹四列都带（实得 …{line[-64:]}）")

        # --- 确定性：同一份产物两次构建，四列逐字一致 ----------------------------------
        first = MCA.build(run)
        second = MCA.build(run)
        axes = lambda p: [  # noqa: E731
            (c["aspect_id"], r["material_id"],
             r["structural_match"], r["body_relevance"], r["fact_eligibility"],
             r["writing_adoption"])
            for c in p["columns"] for r in c["materials"]]
        check(axes(first) == axes(second), "四列读数逐字确定（两次构建完全一致）")

        # --- 读不回来的节：四条轴照样有闭集声明，不靠静默省略 --------------------------
        financial = by_id.get("synth_readback.financial_only")
        if financial is not None:
            counts = financial.get("four_column_counts") or {}
            check(set(counts.get("body_relevance") or {}) == set(MCA.BODY_RELEVANCE_VALUES),
                  "读不回来的节里，逐栏四列仍带闭集全键（读作 0，而不是键消失）")


def _unbound_and_documents(check, details) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run = _write_run(Path(tmp), report=_fixture_report(), manifest=_manifest())
        payload = MCA.build(run)
        unbound = {row["material_id"] for row in payload["unbound_materials"]}
        check("mat-unbound" in unbound, "未绑定 aspect 的材料列在 unbound_materials")
        for column in payload["columns"]:
            check(all(row["material_id"] != "mat-unbound"
                      for row in column["materials"]),
                  f"{column['aspect_id']} 不得收录未绑定 aspect 的材料")
        docs = {doc["document_id"]: doc for doc in payload["documents"]}
        check(docs["SYN_DOC_EMPTY"]["consumption_state"] == "registered_not_consumed",
              "登记了但没有材料的文档记 registered_not_consumed")
        check(docs["SYN_DOC_A"]["consumption_state"] == "consumed",
              "有可用材料的文档记 consumed")
        check(docs["SYN_DOC_EXTRA"]["consumption_state"]
              == "unregistered_material_source",
              "有材料但登记侧没有的文档记 unregistered_material_source")
        check(payload["registered_document_count"] == 2
              and payload["consumed_document_count"] == 2,
              "登记数 / 消费数分别计数（登记 2 份、有材料 2 份含未登记者）")
        check(docs["SYN_DOC_A"]["source_role"] == "current_state_source"
              and docs["SYN_DOC_A"]["primary"] is True,
              "来源角色与当前锚从来源清单照抄")


def _section_unavailable(check, details) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run = _write_run(Path(tmp), report=_fixture_report(), manifest=_manifest())
        payload = MCA.build(run)
        columns = _by_id(payload)
        finance = columns.get("synth_finance_topic.demo")
        check(finance is not None,
              "读回不可用那一节的栏目照样收栏（不靠不收栏抹掉缺口）")
        check(finance["gap"] is not None
              and "pack_section_unavailable" in finance["gap"]["kinds"],
              "该栏缺口记 pack_section_unavailable")
        facts = " ".join(finance["gap"]["facts"])
        check("读不回来" in facts and "pack_set" in facts,
              "缺口事实里保留节读回的 typed detail，并明说这是读不回来")
        check(finance["materials"] == [], "该栏没有材料")
        md = MCA.render_md(payload)
        check("材料包读回不完整的节" in md and "本节没有可用 Pack 集" in md,
              "人读版单列「读回不完整的节」并印出 detail")
        check(payload["sections"]["financial"]["status"] == "unavailable",
              "载荷保留逐节读回状态")
        check("no_material" in finance["gap"]["kinds"],
              "读不回来的节同时如实记「一条材料都没有」，两类事实并存不互相冒充")


def _continuity(check, details) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run = _write_run(Path(tmp), report=_fixture_report(), manifest=_manifest())
        payload = MCA.build(run)
        groups = payload["continuity_groups"]
        pairs = {tuple(f["material_id"] for f in group["fragments"])
                 for group in groups}
        check(("mat-cont-a", "mat-cont-b") in pairs,
              "页号相差 1 且共享 aspect 绑定的两块成组")
        check(("mat-samepage-a", "mat-samepage-b") not in pairs
              and not any("mat-samepage" in pid for pair in pairs for pid in pair),
              "同页两块不成组")
        check(not any("mat-far" in pid for pair in pairs for pid in pair),
              "跨 3 页的两块不成组")
        group = next(g for g in groups
                     if [f["material_id"] for f in g["fragments"]]
                     == ["mat-cont-a", "mat-cont-b"])
        check(group["document_id"] == "SYN_DOC_A" and group["section_id"] == "company",
              "成组读数带文档身份与节")
        check(group["shared_aspect_ids"] == ["synth_readback.demo"],
              "成组读数带共享 aspect 绑定")
        fragments = {f["material_id"]: f for f in group["fragments"]}
        check(fragments["mat-cont-a"]["text"] == "跨页碎片甲。"
              and fragments["mat-cont-b"]["text"] == "跨页碎片乙。",
              "碎片原文与输入逐字相同（未被改写）")
        check(fragments["mat-cont-a"]["page"] == 60
              and fragments["mat-cont-b"]["page"] == 61,
              "各碎片保留自己的页号/定位符")
        check(fragments["mat-cont-a"]["offset"] == 1123
              and fragments["mat-cont-b"]["offset"] == 170,
              "各碎片保留自己的偏移")
        md = MCA.render_md(payload)
        check("跨页连续阅读视图" in md and "未经改写、未经拼接" in md,
              "人读版给出受控连续阅读视图并声明不改写")
        check("跨页碎片甲。" in md and "跨页碎片乙。" in md,
              "两块原文都出现在人读版里")
        check("跨页碎片甲。跨页碎片乙。" not in md,
              "两块**不得**被拼成一段（原文里没有这种形态）")
        check("页边界" in md, "页边界处有显式标记")


def _pick_contract_aspect() -> tuple | None:
    """挑一条**冻结 Contract 里**的对照 aspect：本层键、现行祖先层键、被排除的兄弟键三者都非空，
    且互不重叠——这样才能用三个不同章节的原文，把 `相关` / `弱相关` / `误召` 三档一次判全。

    取"键总数最少、aspect_id 字典序最靠前"的那一条，保证同一份冻结 Contract 上可复现；
    不写死任何 aspect id、公司、页码或答案词。
    """
    from contracts.loader_v2 import load_contract_v2
    contract = load_contract_v2(str(CONTRACT_ASSET))
    labels, siblings = NAV.contract_ancestor_inputs(contract)
    candidates = []
    for aspect in contract.all_aspects():
        aspect_id = aspect.aspect_id
        own = NAV.aspect_nav_keys(tuple(aspect.required_fields or ()),
                                  str(aspect.requirement_text or ""))
        current = NAV.parent_nav_keys(own, labels.get(aspect_id, ()),
                                      siblings.get(aspect_id, ()))
        run_form = NAV.parent_nav_keys(own, labels.get(aspect_id, ()))
        removed = [k for k in run_form if k not in current]
        if not own or not current or not removed:
            continue
        if current[0] in own or removed[0] in own:
            continue
        candidates.append((len(own) + len(current) + len(removed), aspect_id,
                           aspect.topic_id, list(own), list(current), removed))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0]


def _real_aspect_cases(check, details) -> int:
    """真实 Contract aspect 上的三档正例 + 运行记录形态对照。

    同一栏、三块原文，分别落在「本层键那一节」「现行祖先层键那一章」「被 `anp-6` 排除掉的
    兄弟键那一章」：
      * 本层那一节        → `相关`（现行与运行记录形态同判）
      * 现行祖先层那一章  → `弱相关`
      * 被排除的兄弟键那章 → 现行规则 `误召`，运行记录形态 `弱相关` ⇒ `fit_changed` 可见
    """
    picked = _pick_contract_aspect()
    if picked is None:
        details.append("SKIP: 冻结 Contract 上找不到「三套键非空且互不重叠」的 aspect")
        return 1
    _, aspect_id, topic_id, own, current, removed = picked
    details.append(
        f"INFO: 对照 aspect `{aspect_id}`：本层键 {own[:3]}；现行祖先层键 {current[:3]}；"
        f"`anp-6` 排除掉的兄弟键 {removed[:3]}")
    with tempfile.TemporaryDirectory() as tmp:
        pack = {
            "schema_version": "material-pack/2", "status": "readback",
            "raw_text_field": "text",
            "sections": {
                "company": {"status": "readback", "section_id": "company",
                            "entry_count": 3, "entries": [
                                _entry("mat-real-own", topic_id=topic_id,
                                       aspect_ids=(aspect_id,),
                                       section_path=f"第一章 / {own[0]}", page=100,
                                       text="本层那一节的原文。"),
                                _entry("mat-real-ancestor", topic_id=topic_id,
                                       aspect_ids=(aspect_id,),
                                       section_path=f"第一章 / {current[0]}", page=102,
                                       text="现行祖先层那一章的原文。"),
                                _entry("mat-real-sibling", topic_id=topic_id,
                                       aspect_ids=(aspect_id,),
                                       section_path=f"第一章 / {removed[0]}", page=104,
                                       text="被排除的兄弟键那一章的原文。")]},
                "financial": {"status": "readback", "section_id": "financial",
                              "entry_count": 0, "entries": []},
                "industry": {"status": "readback", "section_id": "industry",
                             "entry_count": 0, "entries": []}},
        }
        report = _report(
            nav_rows=[_nav_row(aspect_id, topic_id=topic_id, nav_keys=own,
                              parent_keys=list(NAV.parent_nav_keys(
                                  tuple(own), labels_for(aspect_id))))],
            declarations=[_declaration(aspect_id, "synth_q", "对照栏目")])
        run = _write_run(Path(tmp), pack=pack, report=report,
                         manifest=_manifest(company_topics=(topic_id,)))
        payload = MCA.build(run)
        column = _by_id(payload).get(aspect_id)
        check(column is not None, "对照栏被收进逐栏目表")
        if column is None:
            return 0
        rows = {r["material_id"]: r for r in column["materials"]}
        check(rows["mat-real-own"]["fit"] == "相关"
              and rows["mat-real-own"]["fit_reason"] == "own_section_key"
              and rows["mat-real-own"]["fit_run_rule"] == "相关",
              "本层键那一节 → 相关（两套规则同判）")
        check(rows["mat-real-ancestor"]["fit"] == "弱相关"
              and rows["mat-real-ancestor"]["fit_reason"] == "ancestor_section_key",
              f"现行祖先层键 `{current[0]}` 那一章 → 弱相关")
        check(rows["mat-real-sibling"]["fit"] == "误召"
              and rows["mat-real-sibling"]["fit_reason"] == "no_structural_key",
              f"被 `anp-6` 排除的兄弟键 `{removed[0]}` 那一章 → 误召（现行规则）")
        check(rows["mat-real-sibling"]["fit_run_rule"] == "弱相关"
              and rows["mat-real-sibling"]["fit_changed_by_rule_version"] is True,
              "同一条材料在运行记录形态下是 弱相关 ⇒ 规则版本改变判定可见")
        check(column["fit_changed_count"] == 1, "逐栏的规则版本改变计数为 1")
        check(removed[0] in column["keys"]["ancestor_run_rule"]
              and removed[0] not in column["keys"]["ancestor_current_rule"]
              and column["keys"]["ancestor_current_rule_available"] is True,
              "两套祖先层键都写出来；被排除的兄弟键只出现在运行记录形态里")
        check(set(removed) <= set(column["keys"]["sibling_exclusion_keys"]),
              "被现行规则排除掉的每个兄弟键都出现在原样印出的兄弟项排除集里"
              f"（排除集 {column['keys']['sibling_exclusion_keys']}）")
        md = MCA.render_md(payload)
        check("运行记录规则下为 **弱相关**" in md,
              "人读版把两套档位都印出来（运行记录规则下的档位可见）")
        check("规则版本改变判定" in md, "人读版单列「规则版本改变判定」读数")
        check("相关" in md and "误召" in md, "人读版逐条印出档位与判据")
    return 0


def labels_for(aspect_id: str) -> tuple:
    from contracts.loader_v2 import load_contract_v2
    labels, _ = NAV.contract_ancestor_inputs(load_contract_v2(str(CONTRACT_ASSET)))
    return tuple(labels.get(aspect_id, ()))


def _run_only_aspect(check, details) -> None:
    """**不在冻结 Contract 里**的栏：祖先层键派生不出来，必须单列一档、不得顶替。"""
    with tempfile.TemporaryDirectory() as tmp:
        pack = {
            "schema_version": "material-pack/2", "status": "readback",
            "raw_text_field": "text",
            "sections": {
                "company": {"status": "readback", "section_id": "company",
                            "entry_count": 1, "entries": [
                                _entry("mat-run-only", topic_id="synth_run_only",
                                       aspect_ids=("synth_run_only.demo",),
                                       section_path="第一章 / 乙章节", page=200,
                                       text="运行独有栏的原文。")]},
                "financial": {"status": "readback", "section_id": "financial",
                              "entry_count": 0, "entries": []},
                "industry": {"status": "readback", "section_id": "industry",
                             "entry_count": 0, "entries": []}},
        }
        report = _report(
            nav_rows=[_nav_row("synth_run_only.demo", topic_id="synth_run_only",
                               nav_keys=["甲栏目"], parent_keys=["乙章节"])],
            declarations=[_declaration("synth_run_only.demo", "q", "运行独有栏")])
        run = _write_run(Path(tmp), pack=pack, report=report,
                         manifest=_manifest(company_topics=("synth_run_only",)))
        payload = MCA.build(run)
        column = _by_id(payload).get("synth_run_only.demo")
        check(column is not None, "运行独有栏被收进逐栏目表")
        row = {r["material_id"]: r for r in column["materials"]}["mat-run-only"]
        check(row["fit"] == "祖先层不可判定"
              and row["fit_reason"] == "ancestor_keys_unavailable",
              "运行独有栏的非本层取材判 `祖先层不可判定`（不是 `弱相关`、也不是 `误召`）")
        check(row["fit_run_rule"] == "弱相关"
              and row["fit_changed_by_rule_version"] is True,
              "运行记录形态下它会命中父键 ⇒ 两套读数的差别照实留着")
        check(column["keys"]["ancestor_current_rule"] == []
              and column["keys"]["ancestor_current_rule_available"] is False,
              "现行规则的祖先层键如实记为「派生不出来」，不冒充空集合")
        md = MCA.render_md(payload)
        check("派生不出来" in md and "本栏不在读入的冻结 Contract 里" in md,
              "人读版在该栏的键读数处写明「派生不出来」")


def _contract_agreement(check, details) -> None:
    from contracts.loader_v2 import load_contract_v2
    contract = load_contract_v2(str(CONTRACT_ASSET))
    labels, siblings = NAV.contract_ancestor_inputs(contract)
    aspects = list(contract.all_aspects())
    good = aspects[0]
    bad = aspects[1]
    good_id, bad_id = good.aspect_id, bad.aspect_id
    good_own = list(NAV.aspect_nav_keys(tuple(good.required_fields or ()),
                                        str(good.requirement_text or "")))
    rows = [
        _nav_row(good_id, topic_id=good.topic_id, nav_keys=good_own,
                 parent_keys=list(NAV.parent_nav_keys(
                     tuple(good_own), labels.get(good_id, ())))),
        _nav_row(bad_id, topic_id=bad.topic_id, nav_keys=["与契约不符的键"],
                 parent_keys=list(NAV.parent_nav_keys(
                     NAV.aspect_nav_keys(tuple(bad.required_fields or ()),
                                         str(bad.requirement_text or "")),
                     labels.get(bad_id, ())))),
    ]
    report = _report(nav_rows=rows, declarations=[])
    with tempfile.TemporaryDirectory() as tmp:
        run = _write_run(Path(tmp), report=report, manifest=_manifest())
        payload = MCA.build(run)
        agreement = payload["contract_agreement"]
        check(agreement["checked"] >= 2, "有运行读数的 aspect 全部参与键比对")
        check(any(row["aspect_id"] == bad_id for row in agreement["own_mismatches"]),
              "本层键不一致的 aspect 记进 own_mismatches")
        check(not any(row["aspect_id"] == good_id
                      for row in agreement["own_mismatches"]),
              "本层键一致的 aspect 不进 own_mismatches")
        check(any("键比对出现不一致" in note for note in payload["notes"]),
              "出现不一致时 notes 必须明说读数要打折")
        md = MCA.render_md(payload)
        check("Contract 身份（间接核对）" in md,
              "人读版给出 Contract 身份的间接核对读数")


def _missing_pack(check, details) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run = _write_run(Path(tmp), pack=None, report=_fixture_report(),
                         manifest=_manifest())
        payload = MCA.build(run)
        check(payload["status"] == "unavailable",
              "材料包读不出 ⇒ status=unavailable（不产出逐栏目读数）")
        check("missing_file" in str(payload.get("reason")),
              "原因带 typed 的 missing_file")
        check(any("不等于「本轮没有材料」" in note for note in payload.get("notes") or ()),
              "读不出来与查不到必须区分（不得读成「没有材料」）")
        check("columns" not in payload, "不可用时**不产出** columns 字段")
        md = MCA.render_md(payload)
        check("本表未产出" in md, "人读版如实写「未产出」而不是空表")


def _deterministic(check, details) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        run = _write_run(Path(tmp), report=_fixture_report(), manifest=_manifest())
        first = json.dumps(MCA.build(run), ensure_ascii=False, sort_keys=True)
        second = json.dumps(MCA.build(run), ensure_ascii=False, sort_keys=True)
        check(first == second, "同一份输入连续构建两次载荷逐字节相等（只读、无随机）")


def _readback_section_no_false_gap(check, details) -> None:
    """本层取材齐全的栏不得凭空记缺口（避免"到处都缺"的噪声读数）。"""
    with tempfile.TemporaryDirectory() as tmp:
        pack = {
            "schema_version": "material-pack/2", "status": "readback",
            "raw_text_field": "text", "entry_count": 1,
            "sections": {
                "company": {
                    "status": "readback", "section_id": "company", "entry_count": 1,
                    "entries": [_entry("mat-clean", aspect_ids=("synth_readback.demo",),
                                       section_path="第一章 / 乙章节 / 甲栏目",
                                       text="干净原文。")]},
                "financial": {"status": "readback", "section_id": "financial",
                              "entry_count": 0, "entries": []},
                "industry": {"status": "readback", "section_id": "industry",
                             "entry_count": 0, "entries": []}},
        }
        report = _report(
            nav_rows=[_nav_row("synth_readback.demo", topic_id="synth_readback",
                               nav_keys=["甲栏目"], parent_keys=["乙章节"])],
            declarations=[_declaration("synth_readback.demo", "synth_readback_q",
                                       "合成栏目：甲栏目")])
        run = _write_run(Path(tmp), pack=pack, report=report,
                         manifest=_manifest())
        payload = MCA.build(run)
        column = _by_id(payload)["synth_readback.demo"]
        check(column["gap"] is None,
              f"本层取材 + 导航 selected + 原文可核对的栏不记缺口（实得 "
              f"{(column.get('gap') or {}).get('kinds')}）")
        check(column["counts"]["相关"] == 1, "该栏档位为 相关")
        check(payload["status"] == "readback", "三节都读回时 status=readback")
        md = MCA.render_md(payload)
        check("缺口：无" in md, "人读版对无缺口的栏写「缺口：无」")


def _completion_report(gap_rows: list[dict]) -> dict:
    """在合成报告里挂一个带 typed 缺口行的 Pack 块（第三轴的唯一读数来源）。"""
    report = _fixture_report()
    report["observations"]["company_material_source_reconciliation"]["packs"] = [
        {"topic_id": "synth_readback", "materials": 2, "facts": 0,
         "gaps": len(gap_rows), "gap_rows": gap_rows}]
    return report


def _completion_axis(check, details) -> None:
    """第三轴（Contract 完成规则）：**只照抄** Pack 侧 typed 缺口行，且与取材轴互不推出。

    `company_business_model.tech_route` 那一类的业务风险就在这里：原文里读到了一句话
    （`fit=相关`、`can_prove` 非空）**不等于**这一栏的完成规则满足了。本组用合成夹具把
    这两件事同时摆在一栏上，要求它们**各说各话**。
    """
    demo_gap = {"aspect_ids": ["synth_readback.demo"], "reason_code": "unresolved",
                "layer_reason": "甲栏目", "detail": "coverage_gate_not_met: 甲栏目",
                "blocking": False}
    fin_gap = {"aspect_ids": ["synth_finance_topic.demo"],
               "reason_code": "unresolved", "layer_reason": "财务指标",
               "detail": "set_completeness_proof_unavailable: 财务指标",
               "blocking": True}
    shared_gap = {"aspect_ids": ["synth_readback.demo", "synth_readback.sibling_only"],
                  "reason_code": "unresolved", "layer_reason": "跨两栏",
                  "detail": "usage_scope_not_satisfied: 跨两栏", "blocking": False}

    with tempfile.TemporaryDirectory() as tmp:
        run = _write_run(Path(tmp),
                         report=_completion_report([demo_gap, fin_gap, shared_gap]),
                         manifest=_manifest())
        payload = MCA.build(run)
    columns = _by_id(payload)

    # --- ① 有本层取材（轴①）与有 typed 覆盖缺口（轴③）**同时成立**，谁也不推出谁 ---------
    demo = columns["synth_readback.demo"]
    check(demo["gap"] is not None and demo["gap"]["kinds"] == ["text_unavailable"],
          "前提：这一栏在取材侧**另有它自己的**缺口读数（轴②，实为 "
          f"{(demo['gap'] or {}).get('kinds')}）——两条轴各自成立，不是一件事")
    fits = [row["fit"] for row in demo["materials"]]
    check("相关" in fits,
          f"前提：这一栏确有结构上属于它的取材（实际 {fits}）")
    check(any(row["can_prove"] for row in demo["materials"]),
          "前提：这一栏的材料「能证明什么」非空（原文里确实读到了东西）")
    completion = demo["completion"]
    check(completion["verdict"] == "typed_gap_present",
          "**原文读到 ≠ 完成规则满足**：Pack 侧写了这一栏的 typed 覆盖缺口，"
          f"本栏就必须记 `typed_gap_present`（实为 {completion['verdict']!r}）")
    check(completion["source"] == "run_acceptance_report_pack_gaps"
          and completion["read_reason"] == "",
          "第三轴的来源必须登记为「本次运行验收报告的 Pack 缺口行」")
    check([row["detail"] for row in completion["rows"]] == [
        "coverage_gate_not_met: 甲栏目", "usage_scope_not_satisfied: 跨两栏"],
        "缺口行按 Pack 写的**逐字**照抄（不改写、不翻译、不排序）")
    check(completion["codes"] == ["coverage_gate_not_met", "usage_scope_not_satisfied"],
          "码只由 `detail` 的第一段**切分**得到，不做语义归类")
    check(completion["blocking_count"] == 0,
          "`blocking=true` 逐行数：本栏两行都非阻断 ⇒ 0")
    check("types" not in completion, "不得凭空多写字段（本表只照抄它读到的）")
    check(not (set(demo["gap"]["kinds"]) & set(completion["codes"])),
          "轴②的取材侧缺口类型与轴③的 Pack 覆盖码是**两套词表**，不得互相冒充"
          "（混合它们就等于让取材读数替完成规则说话）")

    # --- ② 一行绑两栏 ⇒ 两栏各记一次（这一行同时是那两栏的缺口读数） --------------------
    sib = columns["synth_readback.sibling_only"]
    check(sib["completion"]["verdict"] == "typed_gap_present"
          and [r["detail"] for r in sib["completion"]["rows"]] == [
              "usage_scope_not_satisfied: 跨两栏"],
          "一行绑多栏的缺口必须**逐栏各记一次**（不因它先挂在别的栏下就漏掉本栏）")

    # --- ③ 本栏没有缺口行 ⇒ `no_typed_gap_read`，**不是**「已满足」 ---------------------
    other = columns["synth_readback.other"]
    check(other["completion"]["verdict"] == "no_typed_gap_read",
          "产物里没有这一栏的 typed 缺口行 ⇒ 记「无从判定」，"
          f"不得记成满足（实为 {other['completion']['verdict']!r}）")
    check(other["completion"]["rows"] == [] and other["completion"]["codes"] == [],
          "「无从判定」这一档不得附带任何缺口行/码")

    # --- ④ 闭集里**没有**「已满足」这种档位 ---------------------------------------------
    check(set(MCA.COMPLETION_KINDS) == {"typed_gap_present", "no_typed_gap_read",
                                        "completion_unreadable"},
          "第三轴闭集就是这三档（这一轴本表不判，改档位必须同时改本 eval）")
    check(not any("满足" in kind or "satisf" in kind.lower()
                  for kind in MCA.COMPLETION_KINDS),
          "闭集里**不得**出现「已满足」这一档：本表不判完成规则，"
          "「没读到缺口」与「缺口不存在」必须留成两件事")
    seen = {column["completion"]["verdict"] for column in payload["columns"]}
    check(seen <= set(MCA.COMPLETION_KINDS),
          f"逐栏第三轴读数全部落在闭集内（越界：{sorted(seen - set(MCA.COMPLETION_KINDS))}）")
    check(payload["completion_kinds"] == list(MCA.COMPLETION_KINDS)
          and payload["completion_read_sources"] == list(MCA.COMPLETION_READ_SOURCES)
          and payload["completion_read_reason"] == "",
          "载荷声明的第三轴闭集与模块一致（本轮读得到 ⇒ 读失败原因为空）")
    typed = [c["aspect_id"] for c in payload["columns"]
             if c["completion"]["verdict"] == "typed_gap_present"]
    check(sorted(typed) == ["synth_finance_topic.demo", "synth_readback.demo",
                           "synth_readback.sibling_only"],
          f"有 typed 缺口的栏就是缺口行覆盖到的那些 aspect（实为 {sorted(typed)}）")
    fin = columns["synth_finance_topic.demo"]
    check(fin["completion"]["blocking_count"] == 1
          and fin["completion"]["codes"] == ["set_completeness_proof_unavailable"],
          "`blocking=true` 的行逐行计入（财务栏那一行是阻断行）")

    # --- ⑤ 人读版必须把两轴分开写，且把「无从判定」写成无从判定 -------------------------
    with tempfile.TemporaryDirectory() as tmp:
        run = _write_run(Path(tmp),
                         report=_completion_report([demo_gap]),
                         manifest=_manifest())
        md = MCA.render_md(MCA.build(run))
    check("Contract 完成规则（照抄 Pack 侧 typed 缺口行；本表不判）" in md,
          "人读版逐栏印出第三轴，并明写「照抄、本表不判」")
    check("取材轴与完成轴是两条轴" in md,
          "人读版在有 typed 缺口的栏上明写两轴分开，防止把「有原文」读成「已满足」")
    check("coverage_gate_not_met: 甲栏目" in md, "缺口行逐字进人读版")
    check("`no_typed_gap_read`" in md and "**不是**「已满足」" in md,
          "人读版把「没有缺口行」写成「无从判定」，并显式排除「已满足」读法")

    # --- ⑥ 缺口读数读不出来 ≠ 没有缺口行 ----------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        run = _write_run(Path(tmp), manifest=_manifest())  # 不写 acceptance_report.json
        unreadable = MCA.build(run)
    verdicts = {c["completion"]["verdict"] for c in unreadable["columns"]}
    check(verdicts == {"completion_unreadable"},
          f"报告读不回来时第三轴记「没做成」，不得记成「没有缺口行」（实为 {verdicts}）")
    check(unreadable["completion_read_reason"].startswith("completion_unavailable:"),
          "读失败必须带 typed 原因，且与「没有缺口行」不同码")
    check(str(unreadable["completion_read_reason"]) != "",
          "这一轴没做成时，顶层也必须能看出来（不得靠 `notes` 一句话承载）")
    md_unreadable = MCA.render_md(unreadable)
    check("这一轴本轮**没做成**" in md_unreadable,
          "人读版对没做成的第三轴写清楚，而不是留空")

    # --- ⑦ 确定性：同一份产物两次构建，第三轴逐字一致 ----------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        run = _write_run(Path(tmp),
                         report=_completion_report([demo_gap, fin_gap, shared_gap]),
                         manifest=_manifest())
        first = MCA.build(run)["columns"]
        second = MCA.build(run)["columns"]
    check([c["completion"] for c in first] == [c["completion"] for c in second],
          "第三轴读数逐字确定（同一产物两次构建完全一致）")


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

    check(set(MCA.FIT_LABELS) == {"相关", "弱相关", "误召", "祖先层不可判定", "被拒绝",
                                  "定位不可判定", "无处置记录"},
          "档位闭集就是这七档（改档位必须同时改本 eval）")
    check(set(MCA.GAP_KINDS) == {"pack_section_unavailable", "navigation_fallback",
                                 "no_material", "all_rejected", "no_on_topic_material",
                                 "text_unavailable"},
          "缺口类型闭集就是这六类（改缺口类型必须同时改本 eval）")
    _kinds(check, details)
    _null_not_false(check, details)
    _selection_only(check, details)
    _unreadable_kind(check, details)
    _four_axis_columns(check, details)
    _unbound_and_documents(check, details)
    _section_unavailable(check, details)
    _continuity(check, details)
    skipped += _real_aspect_cases(check, details)
    _run_only_aspect(check, details)
    _contract_agreement(check, details)
    _missing_pack(check, details)
    _deterministic(check, details)
    _readback_section_no_false_gap(check, details)
    _completion_axis(check, details)
    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps({k: v for k, v in outcome.items() if k != "details"},
                     ensure_ascii=False, indent=2))
    for line in outcome["details"]:
        if line.startswith("FAIL") or line.startswith("SKIP") or line.startswith("INFO"):
            print(line)
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

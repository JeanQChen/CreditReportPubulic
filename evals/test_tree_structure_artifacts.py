"""TS3 §六 P1-D / §十 聚焦反例：真实产物四态归属、结构复核门诚实性、产物 hash 索引。

覆盖：

**§六 P1-D —— 正式输出必须逐行四态归属，且不得用旧桶冒充**

- 真实 runner 必须把 `line_structure_diagnostic` 接进正式产物
  （`line_structure_assignment.json`）：每条可消费正文行确定性落入
  `heading_node` / `formal_unassigned` / `body_under_node` / `non_content` 之一；
- `body_under_node` 必须带真实所属 node ID；遇到 formal-unassigned 边界后，后续正文
  **不得**静默继承上一 accepted heading；
- 四态数量守恒并逐行可回查；旧桶（`accepted_heading` / `ordinary_content` / …）
  只能作为**内部记账 sidecar**，且必须显式声明"不是正式四态"；
- 真实产物 gate：同级误嵌套 / 人员履历与定义句误收 / 真标题漏收必须能被机械识别；
  存在这些问题时 `manual_review.md` **不得**写"自动检查无失败"。

**§十 —— 真实验收可复现性**

- 逐文件 artifact hash index（SHA256 / 大小 / 真实 UTC `generated_at` / `run_id`
  身份 / 版本与代码指纹 / 输入身份），以及缺文件 / 多文件 / 错 hash / 错版本 /
  错输入 / 假时间戳的反例；
- canonical 基线 fixture 受 Git 跟踪、可在干净 checkout 运行；真实 TS2 基线缺失时
  必须 fail-closed，且不得靠 glob / latest 兜底。

合成 PDF 夹具复用 `evals.test_tree_outline_hierarchy`（**只复用不复制**）。
"""

from __future__ import annotations

import ast
import contextlib
import datetime
import json
import pathlib
import shutil
import sys
import tempfile
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import evals.test_tree_alignment_partition as TP  # noqa: E402  源码级断言工具（复用）
import evals.test_tree_outline_hierarchy as H  # noqa: E402  合成 PDF 夹具（复用）

import evaluation.run_tree_outline_acceptance as RA  # noqa: E402
from document_structure import outline_builder as OB  # noqa: E402
from document_structure import versions as V  # noqa: E402

_results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

_COMPANY = "999999"
_DOC = "SYNTH_DOC_2026"

#: 正文行的三种归属来源（与 `line_structure_diagnostic` 写出的取值一致）：
#: 前两种**不得**挂节点，只有 `preceding_heading` 必须带真实 node ID。
_BODY_ATTACHMENTS = ("preceding_heading", "after_formal_unassigned_boundary",
                     "before_first_heading")


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def raises(fn, exc, substr, msg):
    try:
        fn()
    except exc as error:
        if substr in str(error):
            return check(True, msg)
        return check(False, f"{msg}（异常文本不含 {substr!r}：{error}）")
    except Exception as error:  # noqa: BLE001
        return check(False,
                     f"{msg}（异常类型 {type(error).__name__} 非 {exc.__name__}：{error}）")
    return check(False, f"{msg}（未抛出 {exc.__name__}）")


@contextlib.contextmanager
def _tmpdir(prefix="ts3_artifact_"):
    d = tempfile.mkdtemp(prefix=prefix)
    try:
        yield pathlib.Path(d)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _strip_docstrings(source: str) -> str:
    """去掉模块 / 函数 / 类的 docstring 后再看源码（避免把解释文字当成字面量）。"""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _build_fixture(run_dir: pathlib.Path, pages: list, *, toc=None,
                   document_id: str = _DOC) -> dict:
    """用**真实 runner 入口** `build_one` 造一份完整产物目录（含全部 sidecar）。"""
    pdf = H._make_pdf(run_dir / f"{document_id}.pdf", pages, toc=toc)
    return RA.build_one(run_dir, company_id=_COMPANY, document_id=document_id,
                        pdf_path=pdf, label="合成夹具")


def _load(run_dir: pathlib.Path, document_id: str, name: str):
    return json.loads((run_dir / document_id / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# A. 正式四态逐行结构归属（§六 P1-D）
# ---------------------------------------------------------------------------

def _test_four_state_artifact() -> None:
    with _tmpdir() as d:
        built = _build_fixture(d, H._pages_generic_subheading())
        states = _load(d, _DOC, "line_structure_assignment.json")
        buckets = _load(d, _DOC, "line_assignment_buckets.json")
        outline = _load(d, _DOC, "document_outline.json")
        formal = _load(d, _DOC, "formal_unassigned.json")
        manifest = _load(d, _DOC, "manifest.json")

        check((d / _DOC / "line_structure_assignment.json").is_file()
              and states.get("lines"),
              "A1 真实 runner 产出正式四态产物 `line_structure_assignment.json` 且逐行有记录")
        check(set(states["counts"]) == set(OB.LINE_STRUCTURE_STATES),
              f"A2 四态计数键恰为封闭四态（实际 {sorted(states['counts'])}）")
        total = len(states["lines"])
        check(states["conserved"] and states["four_state_sum"] == total
              and total == states["non_furniture_lines"],
              f"A3 四态守恒：四态之和 {states['four_state_sum']} = 逐行记录 {total} = "
              f"非家具行 {states['non_furniture_lines']}")
        check(sum(states["counts"].values()) == states["four_state_sum"],
              "A4 计数与四态之和一致（不得各算一套）")

        node_ids = {n["node_id"] for n in outline["nodes"]}
        span_ids = {s["span_id"] for s in formal["spans"]}
        missing_node = missing_span = bad_owning = 0
        for row in states["lines"]:
            state = row["structure_state"]
            if state == OB.LINE_STRUCTURE_HEADING_NODE:
                if not row.get("node_id") or row["node_id"] not in node_ids:
                    missing_node += 1
            elif state == OB.LINE_STRUCTURE_FORMAL_UNASSIGNED:
                if not row.get("unassigned_span_id") \
                        or row["unassigned_span_id"] not in span_ids:
                    missing_span += 1
            elif state == OB.LINE_STRUCTURE_BODY_UNDER_NODE:
                attachment = row.get("body_attachment")
                if attachment not in _BODY_ATTACHMENTS:
                    bad_owning += 1
                elif attachment == "preceding_heading":
                    # 挂在前序标题下 → 必须带真实 node ID
                    if not row.get("owning_node_id") \
                            or row["owning_node_id"] not in node_ids:
                        bad_owning += 1
                elif row.get("owning_node_id") is not None:
                    # 首个标题之前 / formal-unassigned 边界之后 → 不得挂任何节点
                    bad_owning += 1
        check(missing_node == 0,
              "A5 每个 heading_node 行都带真实 node ID（且能回查 outline）")
        check(missing_span == 0,
              "A6 每个 formal_unassigned 行都带真实 span ID（且能回查 formal_unassigned）")
        check(bad_owning == 0,
              "A7 preceding_heading 的正文行带真实所属 node ID，边界后 / 首标题前的"
              "正文行不挂任何节点")

        # 逐行回查：body 的归属必须是**最近的前序结构行**所决定的那一个；遇到
        # formal-unassigned 边界后不得继续继承更早的 accepted heading。
        violations = []
        for index, row in enumerate(states["lines"]):
            if row["structure_state"] != OB.LINE_STRUCTURE_BODY_UNDER_NODE:
                continue
            nearest = None
            for earlier in reversed(states["lines"][:index]):
                if earlier["structure_state"] in (OB.LINE_STRUCTURE_HEADING_NODE,
                                                  OB.LINE_STRUCTURE_FORMAL_UNASSIGNED):
                    nearest = earlier
                    break
            if nearest is None:
                if row.get("owning_node_id") is not None:
                    violations.append((row["page_number"], row["line_index"], "无前序"))
                continue
            if nearest["structure_state"] == OB.LINE_STRUCTURE_FORMAL_UNASSIGNED:
                attachment_ok = (row.get("owning_node_id") is None
                                 and row.get("body_attachment")
                                 == "after_formal_unassigned_boundary")
                if not attachment_ok:
                    violations.append(
                        (row["page_number"], row["line_index"], "边界后仍继承"))
            elif row.get("owning_node_id") != nearest["node_id"]:
                violations.append((row["page_number"], row["line_index"], "归属错行"))
        check(not violations,
              f"A8 逐行回查一致且遇 formal-unassigned 边界后不继承前序标题"
              f"（违规 {violations[:3]}）")
        after_boundary = [r for r in states["lines"]
                          if r.get("body_attachment")
                          == "after_formal_unassigned_boundary"]
        check(all(r.get("owning_node_id") is None for r in after_boundary),
              f"A9 边界后的正文行一律不挂节点（实际 {len(after_boundary)} 行）")

        # ---- 旧桶不得冒充正式四态 ----
        check(buckets.get("formal_four_states") is False
              and "line_structure_assignment.json" in buckets.get("scope", ""),
              "A10 旧桶 sidecar 显式声明不是正式四态，并指向正式产物")
        check(not set(buckets["counts"]) & set(OB.LINE_STRUCTURE_STATES),
              f"A11 旧桶名与正式四态名不重叠（实际 {sorted(buckets['counts'])}）")
        check(buckets["conserved"] and buckets["total_lines"]
              == buckets["non_furniture_lines"],
              "A12 旧桶自身的记账守恒仍成立（两套账各自守恒、互不顶替）")
        check(buckets["total_lines"] == total,
              "A13 两套账覆盖同一批行（正式四态与内部桶不得一个多一个少）")

        # ---- manifest 级可见性（run 级可一眼看出某文档是否有问题）----
        check(manifest["line_structure"]["artifact"]
              == "line_structure_assignment.json"
              and manifest["line_structure"]["conserved"] is True
              and manifest["line_structure"]["counts"] == states["counts"],
              "A14 manifest 里登记正式四态计数与守恒结论（与产物同一份数值）")
        check(manifest["structural_review_findings"]["artifact"]
              == "structural_review_findings.json",
              "A15 manifest 里登记结构复核门产物")


# ---------------------------------------------------------------------------
# B. 结构复核门：识别能力与真实产物上的表现（§六(5)）
# ---------------------------------------------------------------------------

def _stub_result(*, nodes, candidates, unassigned, layout=None):
    """最小 `OutlineBuildResult` 替身（只含 `structural_review_findings` 读的字段）。

    `layout` 默认 `None`：结构复核门在拿不到持久化版式时**必须 fail-closed**，因此
    这些替身天然落在"复核数据不齐备"的分支上（`clean` 必然为 False）。
    """
    outline = types.SimpleNamespace(nodes=tuple(nodes), unassigned=tuple(unassigned))
    return types.SimpleNamespace(outline=outline, candidates=tuple(candidates),
                                 layout=layout)


def _node(node_id, title, page, line, declared, parent_id=None):
    return types.SimpleNamespace(node_id=node_id, title=title, parent_id=parent_id,
                                 source_anchor=(page, line, (72.0, 100.0, 400.0, 112.0)))


def _cand(page, line, text, *, accepted=True, node_id=None, declared=0, kind="heading",
          primary=("standalone_line",), supporting=()):
    """候选审计替身；`evidence` 用与生产侧**同格式**的字符串元组（独立验收方按此解析）。"""
    evidence = (
        "toc_matches=0", "bookmark_matches=0",
        "primary_evidence=" + (",".join(primary) if primary else "-"),
        "supporting_evidence=" + (",".join(supporting) if supporting else "-"),
    )
    return types.SimpleNamespace(
        kind=kind, page_number=page, line_index=line, text=text, accepted=accepted,
        node_id=node_id, declared_level=declared, applied_level=declared,
        evidence=evidence, reason_codes=())


def _span(span_id, reason, page, line, text):
    return types.SimpleNamespace(
        span_id=span_id, unassigned_reason=reason, start_anchor=(page, line),
        normalized_text=text, page_range=(page, page))


def _test_structural_review_gate_detection() -> None:
    """三类真实问题必须能被机械识别（识别不到就等于没有门）。"""
    # 同级误嵌套：父子两侧声明层级相同
    parent = _node("n1", "1、研发投入情况", 2, 10, 3)
    child = _node("n2", "2、主要子公司情况", 2, 20, 3, parent_id="n1")
    findings = OB.structural_review_findings(_stub_result(
        nodes=[parent, child],
        candidates=[_cand(2, 10, "1、研发投入情况", node_id="n1", declared=3),
                    _cand(2, 20, "2、主要子公司情况", node_id="n2", declared=3)],
        unassigned=[]))
    check(findings["counts"]["same_declared_level_nesting"] == 1
          and findings["same_declared_level_nesting"][0]["child"]["node_id"] == "n2",
          "B1 同级误嵌套被识别（同一 declared_level 挂成父子）")
    check(findings["clean"] is False, "B2 出现复核项时 clean 必须为 False")

    # 对照组：声明层级不同 → 不是同级误嵌套
    other = OB.structural_review_findings(_stub_result(
        nodes=[parent, _node("n3", "（一）研发人员构成", 2, 20, 2, parent_id="n1")],
        candidates=[_cand(2, 10, "1、研发投入情况", node_id="n1", declared=3),
                    _cand(2, 20, "（一）研发人员构成", node_id="n3", declared=2)],
        unassigned=[]))
    check(other["counts"]["same_declared_level_nesting"] == 0,
          "B3 对照：声明层级不同的父子不是同级误嵌套")

    # 人员履历（短子句链）与定义句（定义冒号 + 谓词）误收
    prose = OB.structural_review_findings(_stub_result(
        nodes=[_node("n1", "1、某先生，47岁，现任本公司董事", 3, 5, 3, )],
        candidates=[_cand(3, 5, "1、某先生，47岁，现任本公司董事", node_id="n1",
                          declared=3)],
        unassigned=[]))
    clauses = prose["prose_like_accepted"][0]["short_clauses"] \
        if prose["prose_like_accepted"] else []
    check(prose["counts"]["prose_like_accepted"] == 1 and clauses == ["47岁"],
          f"B4 人员履历误收被识别（短子句 {clauses}）")

    definition = OB.structural_review_findings(_stub_result(
        nodes=[_node("n1", "二、重要缺陷：是指内部控制设计或运行中存在的缺陷", 2, 10, 1)],
        candidates=[_cand(2, 10, "二、重要缺陷：是指内部控制设计或运行中存在的缺陷",
                          node_id="n1", declared=1)],
        unassigned=[]))
    check(definition["counts"]["definition_like_accepted"] == 1
          and definition["definition_like_accepted"][0]["predicates"] == ["是指"],
          "B5 定义句误收被识别（定义冒号后紧接定义谓词）")

    # 对照：真实模板标题（只含一个逗号 / 只含非定义冒号）不得被误报为正文
    clean = OB.structural_review_findings(_stub_result(
        nodes=[_node("n1", "七、与上年度财务报告相比，合并报表范围发生变化的情况说明",
                     2, 10, 1),
               _node("n2", "2、特别现金分红：为积极落实股东回报规划", 2, 20, 1)],
        candidates=[_cand(2, 10, "七、与上年度财务报告相比，合并报表范围发生变化的情况说明",
                          node_id="n1", declared=1),
                    _cand(2, 20, "2、特别现金分红：为积极落实股东回报规划", node_id="n2",
                          declared=1)],
        unassigned=[]))
    check(clean["counts"]["prose_like_accepted"] == 0
          and clean["counts"]["definition_like_accepted"] == 0,
          "B6 对照：只含一个逗号 / 只含非定义冒号的真实标题不被误报为正文误收")

    # 未归属候选按来源细分：目录 / 书签是**真漏收显化**；编号列表歧义只是
    # **待人工复核候选**（无可独立证明其为标题的 gold，§二.4）。
    missed = OB.structural_review_findings(_stub_result(
        nodes=[], candidates=[],
        unassigned=[_span("s1", OB.UNASSIGNED_TOC_UNMATCHED, 1, 5, "第三节 环境与社会责任"),
                    _span("s2", OB.UNASSIGNED_BOOKMARK_UNMATCHED, 4, 7, "第五节 重要事项"),
                    _span("s3", OB.UNASSIGNED_NUMBERED_LIST_AMBIGUITY, 6, 9,
                          "7、某先生，47岁，现任本公司董事")]))
    check(missed["counts"]["toc_unmatched"] == 1
          and missed["counts"]["bookmark_unmatched"] == 1
          and missed["counts"]["numbered_list_ambiguity"] == 1,
          "B7 未归属候选按来源细分（目录 / 书签 / 编号列表歧义三类各自可数）")
    check(all(item["span_id"] and item["unassigned_reason"] for item in
              missed["toc_unmatched"] + missed["bookmark_unmatched"]),
          "B8 漏收项都能回查到正式未归属 span（不是一句叙述）")

    # 采纳丢节点：候选 accepted 却没有 node_id
    orphan = OB.structural_review_findings(_stub_result(
        nodes=[], candidates=[_cand(2, 10, "1、研发投入情况", node_id=None)], unassigned=[]))
    check(orphan["counts"]["accepted_without_node"] == 1,
          "B9 采纳却无正式节点被识别（正文行不得静默丢失）")

    # §二.4 / §三.9：四类**独立**复核项必须能被机械识别（不只查逗号子句与定义句）。
    peer_only = OB.structural_review_findings(_stub_result(
        nodes=[_node("n1", "1、研发投入情况", 2, 10, 3)],
        candidates=[_cand(2, 10, "1、研发投入情况", node_id="n1", declared=3,
                          primary=(), supporting=("level_layout",)),
                    _cand(9, 10, "1、研发投入情况", accepted=True, declared=3,
                          primary=("size_above_body",), supporting=("level_layout",))],
        unassigned=[]))
    check(peer_only["counts"]["global_peer_only_accepted"] == 1
          and peer_only["counts"]["accepted_without_intrinsic_primary_evidence"] == 1,
          "B11 只靠候选间版式相似取得资格的已采纳标题被独立识别"
          f"（peer_only={peer_only['counts']['global_peer_only_accepted']}）")
    entry = peer_only["global_peer_only_accepted"][0]
    check(entry["peer_outside_local_domain"] is True
          and entry["peers_outside_local_domain"][0]["page_number"] == 9,
          "B12 该'出借方'被定位到局部结构域之外（跨 7 页，构成全文件互证）")

    # §三.10：拿不到持久化版式时必须 fail-closed，不得返回 clean
    check(other["layout_available"] is False and other["clean"] is False
          and other["verification_gaps"]
          and other["verification_gaps"][0]["kind"] == "page_layout_unavailable",
          "B13 缺 PageLayout 时结构复核门 fail-closed（记录 verification_gaps，"
          "且 clean 不得为 True）")
    check("unsafe_table_override_accepted" in other["counts"]
          and "fragmented_cell_accepted" in other["counts"]
          and "adjacent_heading_rejected" in other["counts"]
          and "audit_counts" in other and "review_counts" in other,
          "B14 五类复核项（含邻近表格误拒）与留痕项 / 表格边界复核计数都出现在"
          "复核门输出里（不得静默省略）")


def _test_gate_on_real_artifacts() -> None:
    """真实产物上：履历 / 定义正文不再被误收，且显式进入正式未归属。"""
    with _tmpdir() as d:
        built = _build_fixture(d, H._pages_definition(), document_id="SYNTH_DEF_2026")
        findings = built["findings"]
        check(findings["counts"]["prose_like_accepted"] == 0
              and findings["counts"]["definition_like_accepted"] == 0,
              f"B10 定义正文不再被误收（复核门计数 "
              f"{findings['counts']['prose_like_accepted']} / "
              f"{findings['counts']['definition_like_accepted']}）")
        states = built["four_state"]
        unassigned_rows = [r for r in states["lines"]
                           if r["structure_state"]
                           == OB.LINE_STRUCTURE_FORMAL_UNASSIGNED]
        check(unassigned_rows,
              f"B11 定义正文行进入正式四态的 formal_unassigned（{len(unassigned_rows)} 行）")
        check(not [r for r in states["lines"]
                   if r["structure_state"] == OB.LINE_STRUCTURE_BODY_UNDER_NODE
                   and r.get("body_attachment") == "preceding_heading"
                   and r.get("owning_node_id") is None],
              "B12 preceding_heading 的正文行必须带所属节点（不得只写附件类型）")

    with _tmpdir() as d:
        built = _build_fixture(d, H._pages_resume(), document_id="SYNTH_RESUME_2026")
        findings = built["findings"]
        check(findings["counts"]["prose_like_accepted"] == 0,
              "B13 人员履历行不再被误收为标题")
        check(findings["counts"]["same_declared_level_nesting"] == 0,
              "B14 该文档没有同级误嵌套（同级标题保持 siblings）")


# ---------------------------------------------------------------------------
# C. manual_review.md 的诚实性（§六(5)）
# ---------------------------------------------------------------------------

_REAL_RESULT = None


def _real_result():
    """真实 `OutlineBuildResult`（manual_review 需要 `stats` / `pending` 等方法）。"""
    global _REAL_RESULT
    if _REAL_RESULT is None:
        _REAL_RESULT = H._build(H._pages_generic_subheading())[2]
    return _REAL_RESULT


def _manual_review_text(four_state, findings, validation=None, alignment=None):
    """用**真实**函数渲染 manual_review.md，只替换四态 / 复核门两个输入。"""
    validation = validation or {"checks": ["PASS 合成检查"],
                                "tamper_probe": {"all_rejected": True}}
    alignment = alignment or {"block_count": 0, "terminals_missing": [],
                              "terminals_emitted": 0, "alignment_summary": None,
                              "note": "合成夹具无 EvidenceBlock"}
    return RA.manual_review(_real_result(), validation, alignment, None,
                            four_state, findings)


def _clean_four_state():
    return {"counts": {state: 0 for state in OB.LINE_STRUCTURE_STATES},
            "four_state_sum": 0, "total_lines": 0, "non_furniture_lines": 0,
            "conserved": True, "heading_node_without_node_id": 0,
            "formal_unassigned_without_span_id": 0, "lines": []}


def _clean_findings():
    return {"counts": {"same_declared_level_nesting": 0, "prose_like_accepted": 0,
                       "definition_like_accepted": 0, "toc_unmatched": 0,
                       "bookmark_unmatched": 0, "numbered_list_ambiguity": 0,
                       "accepted_without_node": 0,
                       "global_peer_only_accepted": 0,
                       "unsafe_table_override_accepted": 0,
                       "fragmented_cell_accepted": 0,
                       "adjacent_heading_rejected": 0,
                       "accepted_without_intrinsic_primary_evidence": 0,
                       "verification_gap": 0,
                       # §三：两侧结论分叉 / 缺可独立复核的来源 landing 上下文。
                       "safe_override_mismatch": 0,
                       "unsafe_override_mismatch": 0,
                       "source_landing_unverified": 0,
                       # §二.2（`hq-4`）：自报凭来源 landing 采纳、复核侧重建不出。
                       "toc_landing_unverified_accepted": 0},
            "same_declared_level_nesting": [], "prose_like_accepted": [],
            "definition_like_accepted": [], "toc_unmatched": [],
            "bookmark_unmatched": [], "numbered_list_ambiguity": [],
            "accepted_without_node": [],
            "global_peer_only_accepted": [], "table_region_accepted": [],
            "unsafe_table_override_accepted": [],
            "fragmented_cell_accepted": [], "adjacent_heading_rejected": [],
            "accepted_without_intrinsic_primary_evidence": [],
            # §三.4：表内采纳复核计数与留痕清单（安全穿透必须逐条给证据）。
            "review_counts": {"inside_table_accepted": 0,
                              "unsafe_table_override_accepted": 0,
                              "safe_table_override_accepted": 0,
                              "adjacent_heading_rejected": 0,
                              "fragmented_cell_accepted": 0,
                              "safe_override_mismatch": 0,
                              "unsafe_override_mismatch": 0,
                              "source_landing_unverified": 0,
                              "toc_landing_unverified_accepted": 0},
            "inside_table_accepted": 0,
            "unsafe_table_override_accepted": [],
            "safe_table_override_accepted": [],
            "safe_override_mismatch": [], "unsafe_override_mismatch": [],
            "source_landing_unverified": [],
            "toc_landing_unverified_accepted": [],
            "bookmark_navigation_only": [],
            # §四 P1-B(7)：目录**来源行**与**正文 landing** 的六类对账桶，以及它的
            # **状态机版本 + 阻断桶清单**（同级键，不是桶内键）。生产侧
            # `structural_review_findings` 一定带这两个键（`tocr-2`），夹具必须同形 ——
            # 否则"干净"样例会因为缺键而被版本门拦下，测的就不是被测对象了。
            # 值的来源与生产侧**同一个函数**（不另抄一份期望值）。
            **OB.toc_body_reconciliation_declaration(),
            "toc_body_reconciliation": {
                "toc_body_resolved": [], "toc_body_unassigned": [],
                "toc_target_unresolved": [], "bookmark_navigation_only": [],
                "body_heading_unassigned": []},
            "table_scope_unavailable": [], "table_scope_mismatch": [],
            "audit_counts": {"safe_table_override_accepted": 0},
            "acceptor_version": V.HEADING_QUALIFICATION_PROFILE_VERSION,
            "table_region_qualification_version":
                V.TABLE_REGION_QUALIFICATION_VERSION,
            "layout_available": True, "verification_gaps": [], "clean": True}


def _test_manual_review_honesty() -> None:
    clean = _manual_review_text(_clean_four_state(), _clean_findings())
    check("自动检查未发现失败项" in clean,
          "C1 对照：复核门干净时如实写「自动检查未发现失败项」")
    check("line_structure_assignment.json" in clean and "正式四态" in clean,
          "C2 manual_review 引用的是**正式四态产物**（不是旧桶 sidecar）")

    # ---- 同级误嵌套 ----
    findings = _clean_findings()
    findings["counts"]["same_declared_level_nesting"] = 1
    findings["same_declared_level_nesting"] = [{
        "parent": {"node_id": "n1", "page_number": 2, "line_index": 10,
                   "title": "1、研发投入情况", "declared_level": 3},
        "child": {"node_id": "n2", "page_number": 2, "line_index": 20,
                  "title": "2、主要子公司情况", "declared_level": 3},
        "why": "同一编号体系内声明层级相同却挂成父子"}]
    findings["clean"] = False
    text = _manual_review_text(_clean_four_state(), findings)
    check("同级误嵌套" in text and "自动检查未发现失败项" not in text,
          "C3 存在同级误嵌套时不得写「自动检查无失败」")

    # ---- 正文误收（两类）----
    for key, label in (("prose_like_accepted", "子句标点形态"),
                       ("definition_like_accepted", "定义冒号形态")):
        findings = _clean_findings()
        findings["counts"][key] = 1
        findings[key] = [{"page_number": 3, "line_index": 5, "title": "甲",
                          "node_id": "n1", "declared_level": 3, "applied_level": 3,
                          "evidence": ["level_layout"], "marks": [label],
                          "short_clauses": [], "predicates": []}]
        findings["clean"] = False
        text = _manual_review_text(_clean_four_state(), findings)
        check("正文误收" in text and "自动检查未发现失败项" not in text,
              f"C4 存在正文误收（{label}）时不得写「自动检查无失败」")

    # ---- 正式 unassigned 的目录 / 书签来源计数（§四 P1-B(7)：**不是**「真标题漏收」）----
    findings = _clean_findings()
    findings["counts"]["toc_unmatched"] = 1
    findings["counts"]["bookmark_unmatched"] = 1
    findings["toc_unmatched"] = [{"span_id": "s1", "page_number": 1, "line_index": 5,
                                  "text": "第三节 环境与社会责任",
                                  "unassigned_reason": "toc_unmatched"}]
    findings["bookmark_unmatched"] = [{"span_id": "s2", "page_number": 4,
                                       "line_index": 7, "text": "第五节 重要事项",
                                       "unassigned_reason": "bookmark_unmatched"}]
    findings["clean"] = False
    text = _manual_review_text(_clean_four_state(), findings)
    check("toc_unmatched" in text and "bookmark_unmatched" in text
          and "自动检查未发现失败项" not in text,
          "C5 存在正式 `unassigned` 的目录 / 书签来源计数时不得写「自动检查无失败」")
    check("**不是**「真标题漏收」" in text and "真标题漏收显化" not in text,
          "C5b 目录来源行 / 书签**不是**正文标题，不得再被合并称为「真标题漏收」"
          "（§四 P1-B(7)：它们与正文 landing 是**不同对象**）")
    check("第三节 环境与社会责任" in text and "第五节 重要事项" in text,
          "C5c 未归属 span 仍逐条摊开（可人工判读），只是不再叫「真标题漏收」")

    # ---- 编号列表歧义：**待人工复核候选**，不得称「真标题漏收」（§二.4）----
    findings = _clean_findings()
    findings["counts"]["numbered_list_ambiguity"] = 6
    findings["numbered_list_ambiguity"] = [
        {"span_id": "s9", "page_number": 7, "line_index": 3,
         "text": "7、某先生，47岁，现任本公司董事",
         "unassigned_reason": "numbered_list_ambiguity"},
        {"span_id": "s10", "page_number": 8, "line_index": 5,
         "text": "（一）募集资金承诺项目情况",
         "unassigned_reason": "numbered_list_ambiguity"}]
    findings["clean"] = False
    text = _manual_review_text(_clean_four_state(), findings)
    check("待人工复核候选" in text and "编号列表歧义" in text
          and "自动检查未发现失败项" not in text,
          "C8 存在编号列表歧义候选时，本文档不得写「自动检查无失败」")
    check("真标题漏收" not in text,
          "C8b 无独立人工 gold 的编号列表歧义**不得**被称作「真标题漏收」（§二.4）")
    check("募集资金承诺项目情况" in text,
          "C9 待复核候选逐条摊开（可人工判读是否真的漏收）")

    # ---- 五种新增独立误收类（§二.4 / §三.3 / §三.4）必须逐条显形，且都计入失败 ----
    for key, label in (
        ("global_peer_only_accepted", "全文档同层级候选互证"),
        ("unsafe_table_override_accepted", "安全穿透资格"),
        ("fragmented_cell_accepted", "折行片段"),
        ("adjacent_heading_rejected", "邻近"),
        ("accepted_without_intrinsic_primary_evidence", "自身主证据"),
    ):
        findings = _clean_findings()
        findings["counts"][key] = 1
        findings[key] = [{"page_number": 4, "line_index": 9, "title": "1、甲",
                          "node_id": "n9", "declared_level": 4, "reason": label,
                          "evidence": ["level_layout"]}]
        findings["clean"] = False
        text = _manual_review_text(_clean_four_state(), findings)
        check(label in text and "自动检查未发现失败项" not in text,
              f"C10 独立误收类 {key} 在逐份人工复核中显形并计入失败")
        check(key in text, f"C11 {key} 以机械可解析的名字出现（不是一句叙述）")

    # ---- §三.4：表内采纳节点必须**逐条**给出安全穿透证据（不是只给数量）----
    # 安全穿透的**唯一**依据是复核侧自己重算出来的**对象级来源 landing**（§二/§三）。
    # 逐条明细里必须能看到这条 landing 的重算事实（来源对象身份 / 源页 / 页标签映射），
    # 而不是只看得到一个"2 项版式强证据"的旧口径。
    _safe_landing = {
        "reported": True, "available": True,
        "bookmark_identity_recheckable": True, "problems": [],
        "entries": [{"kind": "toc", "recomputed": True,
                     "toc_source_id": "toc-0123456789abcdef",
                     "source_page": 3, "source_line": 1,
                     "declared_page_label": "4", "physical_page": 5,
                     "landing_page": 5, "landing_line": 2}],
    }
    _safe_item = {
        "page_number": 5, "line_index": 2, "title": "二、发行条款提示",
        "node_id": "n12", "declared_level": 2, "applied_level": 2,
        # §四 P1-A(1)（`hq-4`）：表内穿透的唯一来源码是**已验证的目录项 landing**
        # （`toc_body_landing`）；`toc_or_bookmark` 这个把两者混在一起的旧码已不存在。
        "primary_evidence": [OB.TOC_BODY_LANDING_EVIDENCE],
        "supporting_evidence": ["column_indent"],
        "geometry": {"available": True, "scope": "inside_table",
                     "own_row_multi": False, "row_multi_anchor": True},
        "landing": _safe_landing,
        "why": "复核侧在真实版式上重算出成立的对象级来源 landing",
    }
    findings = _clean_findings()
    findings["inside_table_accepted"] = 1
    findings["review_counts"]["inside_table_accepted"] = 1
    findings["review_counts"]["safe_table_override_accepted"] = 1
    findings["audit_counts"]["safe_table_override_accepted"] = 1
    findings["safe_table_override_accepted"] = [dict(_safe_item)]
    text = _manual_review_text(_clean_four_state(), findings)
    check("审计项" in text and "二、发行条款提示" in text
          and "自动检查未发现失败项" in text,
          "C12 表内**安全穿透**采纳作为审计项列出，但不改变 clean 判定")
    check("toc-0123456789abcdef" in text and "own_row_multi" in text
          and "inside_table" in text and "n12" in text
          and "标签4→物理p5" in text,
          "C12b 安全穿透逐条输出**独立重算**的对象级来源 landing（来源对象身份 / "
          "源页行 / 页标签映射）/ inside-adjacent 分类 / 几何事实 / node_id"
          "（不是只给数量）")

    # ---- §三：两侧结论分叉必须阻断，且必须逐条摊开复核侧的重算事实 ----
    for key, label, expect in (
        ("safe_override_mismatch", "生产侧判「安全穿透」、独立复核侧复算**不成立**",
         "漏放"),
        ("unsafe_override_mismatch",
         "生产侧判「不安全」未采纳、独立复核侧复算**成立**", "漏收"),
        ("source_landing_unverified",
         "已采纳的表内标题缺少**可独立复核**的来源 landing 上下文", "fail-closed"),
    ):
        findings = _clean_findings()
        findings["counts"][key] = 1
        findings[key] = [{
            "page_number": 6, "line_index": 7, "title": "（六）某表内标题",
            "node_id": "n30",
            "landing": {"reported": True, "available": key != "safe_override_mismatch",
                        "bookmark_identity_recheckable": False,
                        "problems": ["来源页不是复核侧重算出的目录页"],
                        "entries": []},
            "why": label,
        }]
        findings["clean"] = False
        text = _manual_review_text(_clean_four_state(), findings)
        check(expect in text and label in text and key in text
              and "自动检查未发现失败项" not in text,
              f"C14 两侧分叉 {key} 阻断本门并以机械可解析的名字 + 逐条证据显形")
        check("复核侧重算 landing=" in text
              and "复核侧没有重算结果" not in text,
              f"C14b {key} 的逐条明细带**复核侧自己重算**的 landing 事实"
              f"（不是只给一个计数字段名）")
        check(_clean_findings()["counts"][key] == 0,
              f"C14c 对照：{key} 为 0 时不得阻断（基线仍 clean）")

    # ---- §二.2（`hq-4`）：自报凭来源 landing 采纳、复核侧重建不出 ⇒ 阻断 ----
    findings = _clean_findings()
    findings["counts"]["toc_landing_unverified_accepted"] = 1
    findings["toc_landing_unverified_accepted"] = [
        {"page_number": 2, "line_index": 8, "title": "（四）某事项",
         "node_id": "n41",
         "landing": {"reported": True, "available": False,
                     "bookmark_identity_recheckable": False,
                     "problems": ["来源页不是复核侧重算出的目录页"],
                     "entries": [],
                     "navigation_only": [{"bookmark_page": 2,
                                          "navigation_reason":
                                              OB.BOOKMARK_NAVIGATION_ONLY_REASON}]},
         "why": "生产侧凭书签放行，复核侧在版式上重建不出目录项 landing"}]
    findings["clean"] = False
    text = _manual_review_text(_clean_four_state(), findings)
    check("toc_landing_unverified_accepted" in text
          and "自动检查未发现失败项" not in text,
          "C15 自报凭来源 landing 采纳、复核侧重建不出时本门**阻断**"
          "（书签 / 自报字符串放行不得降级成留痕，§二.2）")
    check("书签仅导航" in text and "**不**计入 available" in text,
          "C15b 该条目的书签只作导航候选：逐条列出但**不**计入 `available`"
          "（`hq-4` 起书签不使复核侧 `available` 为真）")

    # ---- §四 P1-B(7)：目录来源行与正文 landing 必须分六类摊开 ----
    findings = _clean_findings()
    findings["toc_body_reconciliation"] = {
        "toc_body_resolved": [
            {"source_page": 2, "source_line": 3, "title": "第一节 某前言",
             "title_normalized": "某前言", "declared_page_label": "4",
             "declared_depth": 0, "physical_page": 4, "landing_line": 6,
             "node_id": "n1", "node_title": "某前言",
             "node_path": ["某前言"], "why": "四段闭合"}],
        "toc_body_unassigned": [
            {"source_page": 2, "source_line": 4, "title": "第二节 某未落地事项",
             "title_normalized": "某未落地事项", "declared_page_label": "9",
             "declared_depth": 0, "physical_page": 9, "landing_line": 2,
             "landing_text": "某未落地事项", "blocking": True,
             "toc_source_id": "toc-deadbeefdeadbeef",
             "toc_source_rebuildable": True,
             "landing_candidate_rejections": [
                 {"accepted": False, "reason_codes": ["style_only_no_structure"],
                  "declared_level": None, "node_id": None}],
             "unassigned_span_ids": ["span-7"],
             "action_required": "人工复核该正文 landing 行为何没有成为正式节点",
             "why": "该正文行未成为正式节点：可能存在正文标题漏收"}],
        "toc_target_unresolved": [
            {"source_page": 2, "source_line": 5, "title": "第三节 某虚构章节",
             "title_normalized": "某虚构章节", "declared_page_label": "77",
             "declared_depth": 0, "why": "页标签无法唯一映射到任何物理页"}],
        "bookmark_navigation_only": [
            {"declared_page": 3, "title": "某书签标题", "declared_level": 0,
             "reason_codes": [], "navigation_reason":
                 OB.BOOKMARK_NAVIGATION_ONLY_REASON, "why": "只作导航候选"}],
        "body_heading_unassigned": [
            {"page_number": 5, "line_index": 9, "title": "（二）某表格事项",
             "reason_codes": ["table_region_candidate"],
             "why": "正文候选未被采纳"}],
    }
    text = _manual_review_text(_clean_four_state(), findings)
    check("toc_body_resolved" in text and "toc_body_unassigned" in text
          and "toc_target_unresolved" in text
          and "bookmark_navigation_only" in text
          and "body_heading_unassigned" in text,
          "C16 六类目录来源 / 正文 landing 对账桶**逐个**显形（不得合并成一句"
          "「真标题漏收」）")
    check("某虚构章节" in text and "页标签无法唯一映射" in text,
          "C16b 目标无法确认的目录项逐条摊开：**不得**为它编造节点")
    check("（二）某表格事项" in text and "table_region_candidate" in text,
          "C16c **正文**候选未采纳与目录来源行分开列出（两类对象不同，互不冒充）")
    # 「真标题漏收」只允许出现在**显式禁止 / 否定**的措辞里，从不被当成结论使用。
    # `tocr-1` 正是用它来**否认**"目录项找到的正文行被漏收"（"它**不是**「真标题漏收」"），
    # 那种否定句本身就是 fail-open：所以本轮连否定句式一并废除，只剩禁止语。
    _residual = text.replace("不得合并成一句「真标题漏收」", "")
    check("真标题漏收" not in _residual and "真标题漏收显化" not in text,
          "C16d 目录来源 / 正文 landing 对账里「真标题漏收」只出现在**显式禁止**的"
          "措辞里，既不做合并结论、也不做对漏收的**否定**")
    # ---- §tocr-2：`toc_body_unassigned` 必须**阻断**，且逐条带可复核身份 ----
    check("阻断" in text and "正文 landing 未成为节点" in text
          and "自动检查未发现失败项" not in text,
          "C16f `toc_body_unassigned` 非空时本文档**不得**写「自动检查无失败」："
          "目录项已在真实版式上闭合到合格正文行而该行不在树里，是漏收的直接证据")
    check("toc-deadbeefdeadbeef" in text and "span-7" in text
          and "style_only_no_structure" in text,
          "C16g 阻断项逐条带出**可复核身份**：来源 id / 可关联未归属 span / "
          "该 landing 行的候选拒绝原因")
    check("目录来源仅有导航价值" not in text,
          "C16h `tocr-1` 的「目录来源仅有导航价值」这一 fail-open 措辞不再出现在"
          "产物里")
    check(_clean_findings()["toc_body_reconciliation"][
              "toc_body_resolved"] == []
          and "toc_body_resolved=0" not in _manual_review_text(
              _clean_four_state(), _clean_findings()),
          "C16e 对照：六桶皆空时不输出对账块（基线仍 clean）")

    # ---- §tocr-2 **版本门**：legacy / 无版本的旧五桶载荷不得静默按当前语义解释 ----
    for label, version in (("缺版本字段", None),
                           ("`tocr-1` 五桶载荷",
                            V.legacy_versions(
                                "TOC_BODY_RECONCILIATION_VERSION")[0])):
        findings = _clean_findings()
        if version is None:
            findings.pop("toc_body_reconciliation_version")
        else:
            findings["toc_body_reconciliation_version"] = version
        # 旧五桶载荷连桶名都不同：`toc_source_only` 才是它的"未落地"桶。
        findings["toc_body_reconciliation"] = {
            "toc_body_resolved": [], "toc_source_only": [],
            "toc_target_unresolved": [], "bookmark_navigation_only": [],
            "body_heading_unassigned": []}
        text = _manual_review_text(_clean_four_state(), findings)
        check("自动检查未发现失败项" not in text
              and "状态机版本" in text,
              f"C17 （{label}）旧对账载荷**必须**被识别为 legacy 并阻断，"
              f"不得被当成当前版本、六桶皆空而放行")
        check(f"{V.TOC_BODY_RECONCILIATION_VERSION!r}" in text,
              f"C17b （{label}）阻断文案点明**当前**版本，读产物的人知道该按哪版重算")

    # ---- §三.4：伪造的安全穿透必须被独立验收门抓住 ----
    findings = _clean_findings()
    findings["inside_table_accepted"] = 1        # 生产侧自报表内采纳了 1 条
    findings["review_counts"]["inside_table_accepted"] = 1
    findings["review_counts"]["safe_table_override_accepted"] = 0
    findings["audit_counts"]["safe_table_override_accepted"] = 0
    findings["safe_table_override_accepted"] = []   # 却没有任何一条安全穿透证据
    findings["clean"] = False
    text = _manual_review_text(_clean_four_state(), findings)
    check("安全穿透清单只有 0 条" in text and "自动检查未发现失败项" not in text,
          "C12c 表内采纳节点数与安全穿透清单不符时本门阻断（伪造的 safe override "
          "不得以聚合计数蒙过去）")

    # ---- §三.3：仅仅邻近表格就被拒绝，必须显形并计入失败 ----
    findings = _clean_findings()
    findings["counts"]["adjacent_heading_rejected"] = 1
    findings["adjacent_heading_rejected"] = [
        {"page_number": 8, "line_index": 4, "title": "（二）某业务小节",
         "why": "候选仅仅**邻近**表格，却被抽掉了弱主证据 `standalone_line`"}]
    findings["clean"] = False
    text = _manual_review_text(_clean_four_state(), findings)
    check("邻近" in text and "（二）某业务小节" in text
          and "自动检查未发现失败项" not in text,
          "C12d 邻近表格误拒逐条显形并计入失败（§三.3 禁止只因为贴着表格就拒绝）")

    # ---- 独立验收 fail-closed 缺口必须显形 ----
    findings = _clean_findings()
    findings["verification_gaps"] = [{"kind": "page_layout_unavailable",
                                      "detail": "未附着 PageLayout"}]
    findings["layout_available"] = False
    findings["clean"] = False
    text = _manual_review_text(_clean_four_state(), findings)
    check("fail-closed" in text and "page_layout_unavailable" in text
          and "自动检查未发现失败项" not in text,
          "C13 独立验收缺口（缺 PageLayout）时本文档 fail-closed，不得写「自动检查无失败」")

    # ---- 四态不守恒 ----
    four_state = _clean_four_state()
    four_state.update({"four_state_sum": 3, "total_lines": 4, "non_furniture_lines": 4,
                       "conserved": False})
    text = _manual_review_text(four_state, _clean_findings())
    check("四态守恒不成立" in text and "自动检查未发现失败项" not in text,
          "C6 四态不守恒时不得写「自动检查无失败」")

    # ---- 采纳丢节点 ----
    findings = _clean_findings()
    findings["counts"]["accepted_without_node"] = 2
    findings["clean"] = False
    text = _manual_review_text(_clean_four_state(), findings)
    check("没有正式节点" in text and "自动检查未发现失败项" not in text,
          "C7 采纳却丢节点时不得写「自动检查无失败」")


# ---------------------------------------------------------------------------
# C2. run 级总览的诚实性（§六(5)：逐份产物说的和总览必须一致）
# ---------------------------------------------------------------------------

def _run_document(document_id="SYNTH_DOC_2026", *, findings=None, four_state=None,
                  company_id=_COMPANY, alignment=None):
    return {
        "manifest": {
            "document_id": document_id, "company_id": company_id, "page_count": 9,
            "stats": {"nodes": 3, "levels": {"0": 2, "1": 1}},
            "formal_unassigned_count": len(findings["toc_unmatched"])
            if findings else 0,
            "structure_validation_ok": True, "failed_checks": [],
        },
        "acceptance": {"accepted_count": 3, "rejected_count": 10,
                       "numbering_only_accepted": 0, "small_heading_accepted": 1},
        "alignment": alignment or {"block_count": 5, "terminals_emitted": 5,
                                   "terminals_missing_count": 0},
        "findings": findings or _clean_findings(),
        "four_state": four_state or _clean_four_state(),
    }


def _conservation(documents, *, conserved=True, missing=0):
    return {
        "conserved": conserved, "total_db_blocks": 769,
        "total_frozen_blocks": 769, "total_formal_terminals": 769 - missing,
        "total_terminals_missing": missing, "fixture_note": "合成夹具无块",
        "documents": [{"document_id": d["manifest"]["document_id"],
                       "db_blocks": 5, "frozen_baseline_blocks": 5,
                       "formal_terminals": 5, "terminals_missing": 0,
                       "terminal_schema_versions": {"TextAlignmentRecord@als-3": 5},
                       "conserved": True} for d in documents],
    }


def _equivalence(*, identical=True):
    return {"comparable": True, "block_count": 769, "differing_block_count": 0,
            "differing_field_histogram": {}, "identical": identical,
            "three_state_matches_ts2": identical,
            "expected_three_state": {"aligned": 1, "partially_aligned": 0,
                                     "unaligned": 0},
            "verdict_counts": {"aligned": 1, "partially_aligned": 0,
                               "unaligned": 0},
            "ts3_record_consistency": {"ok": True}}


def _test_run_manual_review_honesty() -> None:
    frozen = {"identity": {"name": "ts2_frozen_real"}, "digests": {}}
    clean_doc = _run_document()
    clean = RA.run_manual_review([clean_doc], _conservation([clean_doc]),
                                 _equivalence(), frozen)
    check("自动检查未发现失败项" in clean,
          "H1 对照：总览在逐份文档全干净时如实写「自动检查未发现失败项」")

    # ---- 某份文档有未归属候选：总览不得说无失败 ----
    findings = _clean_findings()
    findings["counts"]["numbered_list_ambiguity"] = 6
    findings["counts"]["toc_unmatched"] = 2
    findings["counts"]["bookmark_unmatched"] = 2
    findings["numbered_list_ambiguity"] = [
        {"page_number": 3, "line_index": 7, "text": "7、某先生，47岁",
         "unassigned_reason": "numbered_list_ambiguity"}]
    findings["clean"] = False
    doc = _run_document(findings=findings)
    text = RA.run_manual_review([doc], _conservation([doc]), _equivalence(), frozen)
    check("自动检查未发现失败项" not in text,
          "H2 某份文档存在未归属候选 / 漏收时，总览不得写「自动检查无失败」")
    check("待人工复核候选" in text and "编号列表歧义 6 条" in text,
          "H3 总览把编号列表歧义计数摊开为「待人工复核候选」（不是把逐份产物的"
          "问题吞掉，也不无 gold 地称「真标题漏收」）")
    check("toc_unmatched" in text and "bookmark_unmatched" in text,
          "H4 正式 `unassigned` 的目录 / 书签来源计数按来源分别列出")
    check("**不是**「真标题漏收」" in text and "真标题漏收显化" not in text,
          "H4b 总览同样不得把目录来源行 / 书签合并称为「真标题漏收」"
          "（§四 P1-B(7)：与正文 landing 是不同对象）")

    # ---- §四 P1-B(7)：六桶必须在**总览**也分类显形 ----
    findings = _clean_findings()
    findings["toc_body_reconciliation"] = {
        "toc_body_resolved": [
            {"source_page": 2, "source_line": 3, "title": "第一节 某前言",
             "declared_page_label": "4", "declared_depth": 0, "physical_page": 4,
             "landing_line": 6, "node_id": "n1", "node_path": ["某前言"],
             "why": "四段闭合"}],
        "toc_body_unassigned": [
            {"source_page": 2, "source_line": 4, "title": "第二节 某未落地事项",
             "declared_page_label": "9", "declared_depth": 0, "physical_page": 9,
             "landing_line": 2, "landing_text": "某未落地事项", "blocking": True,
             "toc_source_id": "toc-deadbeefdeadbeef",
             "landing_candidate_rejections": [], "unassigned_span_ids": ["span-7"],
             "why": "该正文行未成为正式节点：可能存在正文标题漏收"}],
        "toc_target_unresolved": [
            {"source_page": 2, "source_line": 5, "title": "第三节 某虚构章节",
             "declared_page_label": "77", "declared_depth": 0,
             "why": "页标签无法唯一映射到任何物理页"}],
        "bookmark_navigation_only": [
            {"declared_page": 3, "title": "某书签标题",
             "navigation_reason": OB.BOOKMARK_NAVIGATION_ONLY_REASON,
             "why": "只作导航候选"}],
        "body_heading_unassigned": [
            {"page_number": 5, "line_index": 9, "title": "（二）某表格事项",
             "reason_codes": ["table_region_candidate"], "why": "正文候选未采纳"}],
    }
    findings["clean"] = False
    doc = _run_document(findings=findings)
    text = RA.run_manual_review([doc], _conservation([doc]), _equivalence(), frozen)
    check("toc_body_unassigned" in text and "toc_target_unresolved" in text
          and "bookmark_navigation_only" in text
          and "body_heading_unassigned" in text
          and "自动检查未发现失败项" not in text,
          "H7 六类目录来源 / 正文 landing 对账在**总览**也逐个分类显形"
          "（不再合并成一句「真标题漏收」）")
    check("正文 landing 未成为节点" in text and "阻断" in text,
          "H7a `toc_body_unassigned` 在**总览**里就是**阻断项**：总览不得把它写成"
          "「目录来源仅有导航价值」这类留痕")
    check("toc_body_resolved" in text and "非问题项" in text,
          "H7b 已闭合的目录→正文在总览里作为**正例**列出，不计入失败")
    check("某虚构章节" in text or "目录目标未解析" in text,
          "H7c 目标无法确认的目录项在总览点名（不得为它编造节点）")
    check("toc_source_only" not in text,
          "H7d 总览也不再出现 `toc_source_only`（`tocr-1` 的 fail-open 桶名已废除）")

    # ---- §tocr-2 **版本门**在总览同样生效 ----
    findings = _clean_findings()
    findings.pop("toc_body_reconciliation_version")
    findings["clean"] = False
    doc = _run_document(findings=findings)
    text = RA.run_manual_review([doc], _conservation([doc]), _equivalence(), frozen)
    check("状态机版本不是当前版本" in text
          and "自动检查未发现失败项" not in text,
          "H8 **反例⑦**：总览也从**同一份持久化 findings** 重算版本门 —— 缺版本或 legacy "
          "版本的载荷一律阻断，不得因为「六桶皆空」就放行")

    # ---- §二.4 / §三.3 / §三.4：五类独立误收与 fail-closed 缺口同样必须上总览 ----
    for key in ("global_peer_only_accepted", "unsafe_table_override_accepted",
                "fragmented_cell_accepted", "adjacent_heading_rejected",
                "accepted_without_intrinsic_primary_evidence"):
        findings = _clean_findings()
        findings["counts"][key] = 3
        findings["clean"] = False
        doc = _run_document(findings=findings)
        text = RA.run_manual_review([doc], _conservation([doc]), _equivalence(),
                                    frozen)
        check(key in text and "自动检查未发现失败项" not in text,
              f"H5 独立误收类 {key} 在 run 级总览显形并计入失败")
    findings = _clean_findings()
    findings["layout_available"] = False
    findings["verification_gaps"] = [{"kind": "page_layout_unavailable",
                                      "detail": "未附着 PageLayout"}]
    findings["clean"] = False
    doc = _run_document(findings=findings)
    text = RA.run_manual_review([doc], _conservation([doc]), _equivalence(), frozen)
    check("fail-closed" in text and "自动检查未发现失败项" not in text,
          "H6 某份文档的独立验收缺口（fail-closed）在 run 级总览显形")

    # ---- 同级误嵌套 / 正文误收 / 采纳丢节点同样必须显形 ----
    for key, needle in (("same_declared_level_nesting", "同级误嵌套"),
                        ("prose_like_accepted", "正文误收"),
                        ("definition_like_accepted", "正文误收"),
                        ("accepted_without_node", "却没有正式节点")):
        findings = _clean_findings()
        findings["counts"][key] = 1
        findings["clean"] = False
        doc = _run_document(findings=findings)
        text = RA.run_manual_review([doc], _conservation([doc]), _equivalence(),
                                    frozen)
        check(needle in text and "自动检查未发现失败项" not in text,
              f"H5 {key} 非零时总览同样不得写「自动检查无失败」")

    # ---- 四态不守恒同样必须显形 ----
    four_state = _clean_four_state()
    four_state.update({"four_state_sum": 3, "total_lines": 4,
                       "non_furniture_lines": 4, "conserved": False})
    doc = _run_document(four_state=four_state)
    text = RA.run_manual_review([doc], _conservation([doc]), _equivalence(), frozen)
    check("正式四态不守恒" in text and "自动检查未发现失败项" not in text,
          "H6 四态不守恒时总览不得写「自动检查无失败」")


# ---------------------------------------------------------------------------
# D. artifact hash index（§十）
# ---------------------------------------------------------------------------

def _index_for(run_dir: pathlib.Path, *, run_id: str, generated_at: str,
               run_id_source: str = "generated_utc") -> dict:
    return RA.artifact_hash_index(
        run_dir, run_id=run_id, generated_at=generated_at,
        versions=RA.version_manifest(), code=RA.code_fingerprint(),
        inputs={"documents": [_DOC], "evidence_set_versions": []},
        database={"evidence_db": {"sha256": "0" * 64, "size": 1}},
        run_id_source=run_id_source)


def _test_artifact_hash_index() -> None:
    with _tmpdir() as d:
        _build_fixture(d, H._pages_generic_subheading())
        stamp = _utc_now()
        run_id = f"tree_structure_ts3_outline_synth_{stamp}"
        index = _index_for(d, run_id=run_id, generated_at=stamp)
        check(index["file_count"] >= 16 and index["manual_review_required"] is True,
              f"D1 hash index 覆盖产物目录内每个文件（{index['file_count']} 个）")
        check(index["run_id_identity"]["carries_utc_semantics"]
              and index["run_id_identity"]["source"] == "generated_utc"
              and index["run_id_identity"]["semantics"].startswith("按真实 UTC"),
              "D2 由真实 UTC 生成的 run_id → 声明带时间语义")
        check(index["index_identity"]
              and all(len(e["sha256"]) == 64 for e in index["files"]),
              "D3 索引绑定逐文件 SHA256 与自身 index_identity")

        ok = RA.verify_artifact_index(d, index)
        check(ok["ok"] and not ok["problems"],
              f"D4 对照：未改动的产物目录核验通过（problems={ok['problems']}）")

        # 错 hash（篡改文件内容）
        target = d / _DOC / "document_outline.json"
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        bad = RA.verify_artifact_index(d, index)
        check(not bad["ok"] and any("SHA256 不符" in p for p in bad["problems"]),
              f"D5 篡改文件内容 → 逐文件重算发现（{bad['problems'][:1]}）")

    with _tmpdir() as d:
        _build_fixture(d, H._pages_generic_subheading())
        index = _index_for(d, run_id="opaque-run-id", generated_at=_utc_now(),
                           run_id_source="caller_supplied")
        check(not index["run_id_identity"]["carries_utc_semantics"]
              and not index["run_id_identity"]["looks_like_timestamp"]
              and index["run_id_identity"]["source"] == "caller_supplied",
              "D6 调用方给的普通 opaque run_id 明确标注为不带时间语义")
        # §十 反例：调用方**手写**一个长得像时间戳的 `--run-id`。只按后缀 Z 判断就会
        # 让产物宣称"有真实时间语义"，而那一刻的真实 UTC 完全是另一回事。
        forged = _index_for(d, run_id="rework9_20260101T000000Z",
                            generated_at=_utc_now(),
                            run_id_source="caller_supplied")
        ident = forged["run_id_identity"]
        check(ident["looks_like_timestamp"] is True
              and ident["carries_utc_semantics"] is False
              and ident["source"] == "caller_supplied",
              "D6b 手写的 Z 后缀 run_id 一律 opaque（不得据它推断生成时刻）")
        check(_index_for(d, run_id="ts3_outline_20260917T110418Z",
                         generated_at="2026-09-17T11:04:18Z"
                         )["run_id_identity"]["carries_utc_semantics"] is True,
              "D6c 对照：由真实 UTC 打出的 run_id 才带时间语义")
        (d / _DOC / "outline_tree.md").unlink()
        missing = RA.verify_artifact_index(d, index)
        check(any("文件缺失" in p for p in missing["problems"]),
              f"D7 索引登记但文件缺失 → 显式报错（{missing['problems'][:1]}）")
        (d / _DOC / "extra.json").write_text("{}", encoding="utf-8")
        extra = RA.verify_artifact_index(d, index)
        check(any("未登记" in p for p in extra["problems"]),
              f"D8 目录里多出未登记文件 → 显式报错（{extra['problems'][:1]}）")
        wrong_version = RA.verify_artifact_index(
            d, index, expected_versions={"outline_algorithm_version": "oa-1"})
        check(any("版本不符" in p for p in wrong_version["problems"]),
              "D9 版本不符 → 显式报错（旧版本号不得冒充本轮产物）")
        wrong_inputs = RA.verify_artifact_index(
            d, index, expected_inputs={"documents": ["OTHER_DOC"],
                                       "evidence_set_versions": []})
        check(any("输入身份不符" in p for p in wrong_inputs["problems"]),
              "D10 输入身份不符 → 显式报错")
        naive = RA.verify_artifact_index(
            d, _index_for(d, run_id="x", generated_at="2026-09-17 12:00:00"))
        check(any("真实 UTC" in p for p in naive["problems"]),
              "D11 generated_at 不是真实 UTC ISO 形式（缺 T/Z）→ 显式报错")
        check("datetime.timezone.utc" in pathlib.Path(RA.__file__).read_text(
            encoding="utf-8"),
              "D12 run_id / generated_at 的唯一默认来源是真实 UTC（不手写时间戳）")

        # 索引自身不进索引：核验时不得把它当成"未登记的多余文件"
        (d / "artifact_index.json").write_text(
            json.dumps(index, ensure_ascii=False), encoding="utf-8")
        (d / "artifact_index_check.json").write_text("{}", encoding="utf-8")
        after = RA.verify_artifact_index(d, index)
        check(not any("artifact_index" in p for p in after["problems"]),
              f"D13 索引自身与其核验报告不参与登记（{after['problems']}）")

    # §九：版本升级后，验收代码**不得**留下旧版本字面量。曾经这里写死 `"al-2"`，
    # 于是 `ALIGNER_VERSION` 升到 `al-3` 之后，正式记录一律被判"aligner_version
    # 不符" —— 拿旧版本号当权威，正是"新版写进 manifest、正式 terminal 却分不清
    # 旧自报输入与新权威输入"的形态。版本只能从 `versions.py` 这一个来源读。
    body = TP._code_without_docstrings(RA._ts3_record_consistency)
    check("V.ALIGNER_VERSION" in body
          and "V.ALIGN_SCHEMA_VERSION" in body
          and "V.ALIGN_REFUSAL_SCHEMA_VERSION" in body,
          "D14 记录版本核验只读 versions.py（al-3 / als-3 / alr-2 的唯一来源）")
    runner_src = pathlib.Path(RA.__file__).read_text(encoding="utf-8")
    stripped = _strip_docstrings(runner_src)
    stale = [lit for lit in ("al-1", "al-2", "als-1", "als-2", "alr-1",
                             "oa-1", "ebi-1", "os-1", "os-2", "os-3")
             if repr(lit) in stripped]
    check(not stale,
          f"D15 验收代码里没有遗留的旧版本字面量（实际 {stale}）")
    check(V.classify_schema_version("ALIGNER_VERSION", V.ALIGNER_VERSION)
          == "current"
          and V.classify_schema_version("ALIGNER_VERSION", "al-2") == "legacy",
          "D16 当前 aligner 版本判为 current，旧版本判为 legacy"
          "（旧版本必须显式拒绝，不得静默当作本轮产物）")


# ---------------------------------------------------------------------------
# E. canonical 受跟踪 fixture 与真实基线 fail-closed（§十）
# ---------------------------------------------------------------------------

def _test_canonical_and_real_baseline() -> None:
    spec = RA.canonical_baseline_spec()
    check(spec.root == RA.CANONICAL_BASELINE_ROOT
          and str(spec.root).startswith(str(RA.REPO_ROOT)),
          f"E1 canonical 基线来自仓库内**受跟踪**的 fixture 目录（{spec.root}）")
    check("evaluation" not in spec.root.parts
          or "results" not in spec.root.parts,
          "E2 canonical 基线不依赖未跟踪的 evaluation/results/** 本地目录")
    blocks_path, records, meta = RA.load_baseline_spec(spec)
    check(len(records) == spec.block_count and spec.block_count < 769,
          f"E3 canonical 基线可在干净 checkout 下加载且块数自洽"
          f"（{len(records)} = {spec.block_count}，是极小 fixture 而非整份产物）")
    check(meta["digests"] and all(v["match"] for v in meta["digests"].values()),
          "E4 canonical fixture 的关键文件哈希与清单逐项相符")

    # ---- 反例：canonical 清单/文件被改动 ----
    with _tmpdir() as d:
        fixture = d / spec.run_dir          # `load_baseline_spec(root=…)` 的定位方式
        shutil.copytree(RA.CANONICAL_BASELINE_ROOT / spec.run_dir, fixture)
        manifest = json.loads(
            RA.CANONICAL_BASELINE_MANIFEST.read_text(encoding="utf-8"))
        pristine = RA.canonical_baseline_spec()      # root=真实 fixture 目录
        # (1) 错 hash
        broken = json.loads(json.dumps(manifest))
        first = sorted(broken["files"])[0]
        broken["files"][first] = "0" * 64
        bad_hash_path = d / "manifest_bad_hash.json"
        bad_hash_path.write_text(json.dumps(broken), encoding="utf-8")
        raises(lambda: RA.load_baseline_spec(
            RA.canonical_baseline_spec(bad_hash_path), root=d),
            RA.FrozenBaselineError, "哈希不符",
            "E5 canonical 文件哈希与清单不符 → fail-closed")
        # (2) 缺文件
        (fixture / first).unlink()
        raises(lambda: RA.load_baseline_spec(
            RA.canonical_baseline_spec(RA.CANONICAL_BASELINE_MANIFEST), root=d),
            RA.FrozenBaselineError, "缺关键文件",
            "E6 canonical 基线缺关键文件 → fail-closed")
        # (3) 块数不符（清单自报块数与 fixture 记录数不一致）——用另一份干净副本，
        #     哈希全部重算，隔离出"只有块数不符"这一条不变量
        clean_root = d / "clean"
        clean_root.mkdir()
        clean = clean_root / spec.run_dir
        shutil.copytree(RA.CANONICAL_BASELINE_ROOT / spec.run_dir, clean)
        bad_count = json.loads(json.dumps(manifest))
        bad_count["block_count"] = int(manifest["block_count"]) + 1
        bad_count["files"] = {name: RA.sha256_file(clean / name)
                              for name in sorted(manifest["files"])}
        bad_count_path = d / "manifest_bad_count.json"
        bad_count_path.write_text(json.dumps(bad_count), encoding="utf-8")
        raises(lambda: RA.load_baseline_spec(
            RA.canonical_baseline_spec(bad_count_path), root=clean_root),
            RA.FrozenBaselineError, "块数不符",
            "E7 canonical 基线块数与清单不符 → fail-closed")
        # (4) 清单缺失
        raises(lambda: RA.canonical_baseline_spec(d / "nope.json"),
               RA.FrozenBaselineError, "缺失", "E8 canonical 清单缺失 → fail-closed")

    # ---- 真实 TS2 基线：绑定不可 glob，缺失即 fail-closed ----
    real = RA.ts2_baseline_spec()
    check(real.run_dir == RA.TS2_BASELINE_RUN_DIR
          and real.files == tuple(sorted(RA.TS2_BASELINE_FILES.items()))
          and real.block_count == RA.TS2_BASELINE_BLOCK_COUNT == 769,
          "E9 真实 TS2 基线由精确目录名 + 关键文件 SHA256 + 769 块数绑定")
    check(real.identity()["selection_rule"].find("不 glob") >= 0,
          "E10 基线身份显式声明不 glob / 不取 latest（不得被更晚目录顶替）")
    with _tmpdir() as d:
        raises(lambda: RA.load_baseline_spec(real, root=d),
               RA.FrozenBaselineError, "目录缺失",
               "E11 本地缺失真实 TS2 基线 → fail-closed（不得降级为「跳过对账」）")


# ---------------------------------------------------------------------------
# F. 轮级对账版本门 / 六桶汇总（P2-1 / P2-2）与产物指针（P2-3）
# ---------------------------------------------------------------------------

def _run_manifest_dict_source() -> str:
    """只取验收脚本里**写 run manifest 的那个字典表达式**（AST 定位后 unparse）。

    断言"落盘的值不来自字面量"必须针对这一处，而不是整份源码：整份源码里出现
    常量名 / 显示文案，都不能证明 manifest 里写下去的是常量而不是抄一遍的字面量。
    用 `ts3_scope` 作为该字典的唯一签名键定位（其它产物没有这个键）。
    """
    tree = ast.parse(pathlib.Path(RA.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Dict) and any(
                isinstance(k, ast.Constant) and k.value == "ts3_scope"
                for k in arg.keys):
            return ast.unparse(arg)
    raise AssertionError("未能在验收脚本里定位 run manifest 字典表达式")


def _declared_findings(*, version=..., blocking=..., **overrides):
    """真实形态的 findings 夹具：版本与阻断桶清单默认取**生产侧同一来源**。"""
    findings = _clean_findings()
    if version is not ...:
        if version is None:
            findings.pop("toc_body_reconciliation_version", None)
        else:
            findings["toc_body_reconciliation_version"] = version
    if blocking is not ...:
        if blocking is None:
            findings.pop("toc_body_reconciliation_blocking_buckets", None)
        else:
            findings["toc_body_reconciliation_blocking_buckets"] = list(blocking)
    findings.update(overrides)
    return findings


def _test_toc_reconciliation_rollup_and_manifest() -> None:
    """§tocr-2（P2-1 / P2-2）：轮级版本门与六桶汇总**单一来源**，且落盘可核对。"""
    frozen = {"identity": {"name": "ts2_frozen_real"}, "digests": {}}
    author_version = V.TOC_BODY_RECONCILIATION_VERSION
    author_blocking = list(OB.TOC_BODY_RECONCILIATION_BLOCKING_BUCKETS)
    legacy_version = V.legacy_versions("TOC_BODY_RECONCILIATION_VERSION")
    check(legacy_version, "R3a legacy 版本清单非空（`tocr-1` 已登记）")
    legacy = str(legacy_version[0])

    # ---- R1 单一来源：版本 / 阻断桶只从权威常量来，验收脚本不写字面量 ----
    clean_doc = _run_document()
    rollup = RA.toc_reconciliation_rollup([clean_doc])
    check(rollup["version"] == author_version
          and rollup["blocking_buckets"] == author_blocking,
          f"R1 轮级对账版本与阻断桶只来自权威常量"
          f"（{rollup['version']} / {rollup['blocking_buckets']}）")
    check(rollup["all_documents_current"] is True and not rollup["problems"],
          f"R1a 逐份声明与顶层一致时版本门放行（problems={rollup['problems']}）")
    check(rollup["versions_by_document"][_DOC] == author_version
          and rollup["blocking_buckets_by_document"][_DOC] == author_blocking,
          "R1b 逐份声明的版本与阻断桶被**原样带出**（供 manifest 持久化）")

    # ---- R2 六桶汇总 = 逐份之和；legacy 同义桶与"已进树"计数为附注 ----
    d1 = _run_document("DOC_A")
    f1 = _declared_findings()
    f1["toc_body_reconciliation"]["toc_body_resolved"] = [{"a": 1}, {"a": 2}]
    f1["toc_body_reconciliation"]["toc_body_unassigned"] = [{"b": 1}]
    f1["toc_body_reconciliation"]["toc_target_unresolved"] = [
        {"b": 2, "same_title_node_ids": ["n1"]}, {"b": 3}]
    f1["toc_body_reconciliation"]["bookmark_navigation_only"] = [{"c": 1}]
    f1["toc_body_reconciliation"]["body_heading_unassigned"] = [{"d": i}
                                                               for i in range(4)]
    d2 = _run_document("DOC_B")
    f2 = _declared_findings()
    f2["toc_body_reconciliation"]["toc_body_resolved"] = [{"a": 3}]
    f2["toc_body_reconciliation"]["body_heading_unassigned"] = [{"d": 9}]
    docs = [_run_document("DOC_A", findings=f1),
            _run_document("DOC_B", findings=f2)]
    rollup = RA.toc_reconciliation_rollup(docs)
    check(rollup["counts"] == {
        "toc_body_resolved": 3, "toc_body_unassigned": 1,
        "toc_target_unresolved": 2, "bookmark_navigation_only": 1,
        "body_heading_unassigned": 5,
        "toc_target_unresolved_with_existing_node": 1},
        f"R2 六桶汇总 = 逐份之和，且「目标未解析但正文标题已进树」单独计数"
        f"（{rollup['counts']}）")
    check(all(len(entry["counts"]) for entry in rollup["per_document"])
          and [e["document_id"] for e in rollup["per_document"]] == ["DOC_A", "DOC_B"],
          "R2a 逐份分项计数随汇总一起带出（读产物的人可核对汇总的来源）")

    # ---- R3 反例①：某份文档声明的版本是 legacy / 缺失 ⇒ 版本门阻断 ----
    for label, version, needle in (("R3 反例① legacy 版本",
                                    legacy, "≠ 当前"),
                                   ("R4 反例② 版本键缺失",
                                    None, "缺少")):
        doc = _run_document("DOC_A",
                            findings=_declared_findings(version=version))
        rollup = RA.toc_reconciliation_rollup([doc])
        check(not rollup["all_documents_current"]
              and any(needle in p for p in rollup["problems"]),
              f"{label}：轮级版本门 block，且点名原因"
              f"（problems={rollup['problems']}）")

    # ---- R5 反例③：版本相同但**阻断桶清单不同** ⇒ 同样阻断 ----
    doc = _run_document("DOC_A",
                        findings=_declared_findings(blocking=["some_other_bucket"]))
    rollup = RA.toc_reconciliation_rollup([doc])
    check(not rollup["all_documents_current"]
          and any("blocking_buckets" in p for p in rollup["problems"]),
          f"R5 反例③ 阻断桶清单不同 ⇒ 口径不同，不得据此放行"
          f"（problems={rollup['problems']}）")
    doc = _run_document("DOC_A", findings=_declared_findings(blocking=None))
    rollup = RA.toc_reconciliation_rollup([doc])
    check(any("blocking_buckets" in p for p in rollup["problems"]),
          "R5a 反例③ 阻断桶键缺失 ⇒ 同样阻断（不得默认取当前口径解释）")

    # ---- R6 版本门问题必须同时在**总览问题清单**里出现（不得只藏在 manifest）----
    doc = _run_document("DOC_A",
                        findings=_declared_findings(version=legacy))
    text = RA.run_manual_review([doc], _conservation([doc]), _equivalence(), frozen)
    check("自动检查未发现失败项" not in text
          and "版本门阻断" in text and "≠ 当前" in text,
          "R6 版本门不成立时总览同样不得写「自动检查无失败」，且逐条点名")

    # ---- R7 P2-1：run manifest 必须自带版本 / 阻断桶 / 六桶汇总，且取自同一份 rollup ----
    # 断言对象是**run manifest 那个字典表达式本身**（AST 定位后 unparse），不是整份源码：
    # 只有这样才能证明"落盘的值不来自字面量"，而不只是"脚本里出现过常量名"。
    runner_src = pathlib.Path(RA.__file__).read_text(encoding="utf-8")
    manifest_expr = _run_manifest_dict_source()
    for key, expr in (
            ("toc_body_reconciliation_version", "recon_rollup['version']"),
            ("toc_body_reconciliation_blocking_buckets",
             "recon_rollup['blocking_buckets']"),
            ("toc_body_reconciliation_legacy_versions",
             "recon_rollup['legacy_versions']"),
            ("toc_body_reconciliation_counts", "recon_rollup['counts']"),
            ("toc_body_reconciliation_versions_by_document",
             "recon_rollup['versions_by_document']"),
            ("toc_body_reconciliation_blocking_buckets_by_document",
             "recon_rollup['blocking_buckets_by_document']"),
            ("toc_body_reconciliation_all_documents_current",
             "recon_rollup['all_documents_current']"),
            ("toc_body_reconciliation_version_gate_problems",
             "recon_rollup['problems']")):
        check(f"'{key}'" in manifest_expr and expr in manifest_expr,
              f"R7 run manifest 落盘 `{key}`，且值取自 `{expr}`"
              f"（不另算一套、不写字面量）")
    check(f"'{author_version}'" not in manifest_expr
          and f"'{legacy}'" not in manifest_expr,
          f"R7a run manifest 里没有对账版本字面量（{author_version} / {legacy} "
          f"只能来自 versions.py）")
    check(not any(f"'{b}'" in manifest_expr for b in author_blocking),
          f"R7b run manifest 里没有**阻断桶名字面量**（{author_blocking} "
          f"只能来自 outline_builder.py）")
    # 版本门 / 阻断桶的**判定口径**也不得写死在验收脚本里：阻断桶清单只认常量。
    stripped = _strip_docstrings(runner_src)
    check(any("TOC_BODY_RECONCILIATION_BLOCKING_BUCKETS" in line
              for line in stripped.split("\n")),
          "R7c 阻断桶口径来自 `OB.TOC_BODY_RECONCILIATION_BLOCKING_BUCKETS`，"
          "不在验收脚本里另立一份")

    # ---- R8 P2-2：总览必须自带版本、阻断桶与六桶汇总表 ----
    doc = _run_document("DOC_A", findings=f1)
    text = RA.run_manual_review([doc], _conservation([doc]), _equivalence(), frozen)
    check("六桶 · 版本门" in text and f"`{author_version}`" in text,
          "R8 总览显式写出对账状态机版本")
    check("阻断桶（唯一口径）" in text
          and all(f"`{b}`" in text for b in author_blocking),
          f"R8a 总览显式写出阻断桶清单（{author_blocking}）")
    for name in OB.TOC_BODY_RECONCILIATION_BUCKETS:
        check(f"| `{name}` |" in text,
              f"R8b 总览六桶汇总表逐个列出 `{name}`（含 0 值行）")
    check("| `toc_body_resolved` | 2 |" in text
          and "| `toc_body_unassigned` | 1 |" in text
          and "| `body_heading_unassigned` | 4 |" in text,
          "R8c 总览六桶表的数字与持久化载荷逐项相符（不是只列桶名）")

    # ---- R9 P2-2：`toc_body_unassigned`=0 时保留该行并写「本轮未触发」，但**不得**
    #      写成"全部目录均已解析 / 标题树不存在缺口" ----
    doc = _run_document()
    text = RA.run_manual_review([doc], _conservation([doc]), _equivalence(), frozen)
    check("本轮未触发该阻断项" in text and "`toc_body_unassigned`" in text,
          "R9 阻断桶为 0 时显式写「本轮未触发该阻断项」，且该行**未被删除**")
    # 这两句只允许出现在**显式否定**里（否则就是把"未触发一个阻断桶"写成"全绿"）。
    residual = text.replace(
        "本行**不**表示「全部目录均已解析」，也**不**表示「标题树不存在缺口」", "")
    check(residual != text,
          "R9a 「未触发该阻断桶」必须**显式**声明它不是「全绿」结论")
    for forbidden in ("全部目录均已解析", "标题树不存在缺口", "目录已全部解析",
                      "标题树无缺口", "目录解析完整", "无任何缺口"):
        check(forbidden not in residual,
              f"R9b 总览除显式否定外不得写出「{forbidden}」")

    # ---- R10 P2-2：非零的**非阻断**桶如实登记，不作失败结论 ----
    doc = _run_document("DOC_A", findings=f1)
    text = RA.run_manual_review([doc], _conservation([doc]), _equivalence(), frozen)
    check("非阻断桶 `toc_target_unresolved` = 2" in text
          and "非阻断桶 `bookmark_navigation_only` = 1" in text
          and "非阻断桶 `body_heading_unassigned` = 4" in text,
          "R10 三类非阻断桶非零时**如实登记条数**")
    check("不作失败结论" in text,
          "R10a 非阻断桶非零**不**被改写成失败结论（也不被静默清零）")

    # ---- R11 P2-2：阻断桶非零时写「本轮触发该阻断项」并**阻断** ----
    doc = _run_document("DOC_A", findings=f1)
    text = RA.run_manual_review([doc], _conservation([doc]), _equivalence(), frozen)
    check("本轮触发该阻断项" in text and "阻断" in text
          and "本轮未触发该阻断项" not in text,
          "R11 阻断桶非零 ⇒ 总览写「本轮触发该阻断项」，且不得同时写「未触发」")

    # ---- R12 P2-3：全文件扫描，遗留 `acceptance_report.md` 指针必须为 0 ----
    stale = [i for i, line in enumerate(runner_src.split("\n"), 1)
             if "acceptance_report" in line]
    check(not stale,
          f"R12 验收脚本里没有指向不存在的 `acceptance_report.md` 的指针"
          f"（实际残留行号 {stale}）")
    check(runner_src.count("manual_review.md") >= 5,
          "R12a 指针改为真实存在的 `manual_review.md`（含小节名，不是只改文件名）")

    # ---- R13 §「导航 ≠ 资格」：Bookmark 命中不得进入**正式资格依据** ----
    _, _, result, _ = H._build(H._pages_toc_bookmark(),
                               toc=[[1, "环境与社会责任", 3]])
    stats = RA.heading_acceptance_stats(result)
    check(stats["non_formal_qualification_basis"] == {},
          f"R13 真实构建里没有非正式证据码进入资格算术"
          f"（{stats['non_formal_qualification_basis']}）")
    check(all(k in OB.HEADING_PRIMARY_EVIDENCE
              for k in stats["accepted_by_qualification_basis"])
          and not any("bookmark" in k
                      for k in stats["accepted_by_qualification_basis"]),
          f"R13a Bookmark 命中不出现在 `accepted_by_qualification_basis`"
          f"（{stats['accepted_by_qualification_basis']}）")
    # 记账层反例：把 Bookmark 冒充成资格依据的接受记录必须被**机械识别**出来，
    # 而不是靠人读。识别不到就等于这个门不存在。
    fake = types.SimpleNamespace(
        accepted=True, declared_level=0, page_number=3, line_index=2,
        text="某书签标题", reason_codes=[],
        evidence=("primary_evidence=" + OB.BOOKMARK_NAVIGATION_ONLY_REASON,
                  "bookmark_matches=1"))
    stub = types.SimpleNamespace(candidates=[fake], stats={})
    bad = RA.heading_acceptance_stats(stub)
    check(bad["non_formal_qualification_basis"]
          == {OB.BOOKMARK_NAVIGATION_ONLY_REASON: 1},
          f"R13b **反例④**：以 Bookmark 作 `primary_evidence` 的接受记录被逐条记入"
          f"`non_formal_qualification_basis`（{bad['non_formal_qualification_basis']}）")
    check(OB.BOOKMARK_NAVIGATION_ONLY_REASON not in OB.HEADING_PRIMARY_EVIDENCE
          and OB.TOC_BODY_LANDING_EVIDENCE in OB.HEADING_PRIMARY_EVIDENCE,
          "R13c 正式资格词表里只有可重建身份的目录 landing，没有书签导航码")

    # ---- R14 非空 `toc_body_unassigned` 仍阻断（不因本轮真实文档为 0 而放宽）----
    doc = _run_document("DOC_A", findings=f1)
    text = RA.run_manual_review([doc], _conservation([doc]), _equivalence(), frozen)
    check("正文 landing 未成为节点" in text and "阻断" in text,
          "R14 `toc_body_unassigned` 非空 ⇒ 仍是**阻断项**（口径未放宽）")


def main() -> dict:
    groups = (
        _test_four_state_artifact,
        _test_structural_review_gate_detection,
        _test_gate_on_real_artifacts,
        _test_manual_review_honesty,
        _test_run_manual_review_honesty,
        _test_artifact_hash_index,
        _test_canonical_and_real_baseline,
        _test_toc_reconciliation_rollup_and_manifest,
    )
    for fn in groups:
        try:
            fn()
        except Exception as error:  # noqa: BLE001
            _results["failed"] += 1
            _results["details"].append(
                f"FAIL {fn.__name__} 崩溃（该组后续反例未执行）："
                f"{type(error).__name__}: {error}")
    return _results


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=1))
    raise SystemExit(0 if _results["failed"] == 0 else 1)

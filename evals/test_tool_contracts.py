"""Eval: Tool 契约层（tools/contracts.py）—— Phase 3 Batch A。

用法: python -m evals.test_tool_contracts

断言（纯声明式，无 I/O、不调用工具）：
- 工具名/状态/错误码/重试策略/成本类别枚举完整且无重复；
- ToolResult 状态与错误码合法性；
- validate_arguments：缺必需参数、未知字段拒绝、类型错误、enum、数值上下限、
  字符串长度、数组元素数、嵌套对象未知字段、`exactlyOneOf` 互斥门；
- enforce_arguments 在非法参数时抛 ToolValidationError；
- M930-2 `inspect_outline_materials` 的公共契约补齐：名称在白名单中恰出现一次、
  注册到 registry 的 ToolSpec 版本与公共声明一致、未知字段被拒、node/evidence
  两种 selector 的互斥门有效、不引入 `verify_claim` 或其他 Phase 4/5 工具。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import contracts as C
from tools import registry as R
from routing import schema as routing_schema


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # ---- 枚举完整性 ----
    check(len(C.TOOL_STATUSES) == 5, "工具结果状态恰为 5 态")
    check(set(C.TOOL_STATUSES) == {"SUCCESS", "PARTIAL", "EMPTY",
                                   "RETRYABLE_ERROR", "FATAL_ERROR"},
          "状态集合与任务书 §6.2 一致")
    check(len(C.TOOL_ERROR_CODES) == 13, "最低错误码恰为 13 个")
    check(len(set(C.TOOL_ERROR_CODES)) == len(C.TOOL_ERROR_CODES),
          "错误码无重复")
    check("verify_claim" not in C.TOOL_NAMES, "verify_claim 不注册（Phase 4/5 边界）")
    check("search_evidence" in C.TOOL_NAMES and "lookup_company_field" in C.TOOL_NAMES,
          "首批本地工具已含 search_evidence / lookup_company_field")
    check(C.RETRY_POLICIES == ("none", "retryable_only"),
          "重试策略枚举与任务书一致")
    check(C.COST_CLASSES == ("local", "db", "external"), "成本类别枚举完整")
    check(set(C.TOOL_NAMES) & {"verify_claim"} == set(),
          "verify_claim 不在 TOOL_NAMES 白名单")

    # ---- ToolResult 语义 ----
    r = C.ToolResult(call_id="c1", tool_name="search_evidence",
                     tool_version="v1", status="EMPTY", data={})
    check(r.is_empty() and not r.is_error(), "EMPTY 是合法结果且非错误")
    r_err = C.ToolResult(call_id="c2", tool_name="x", tool_version="v1",
                         status="RETRYABLE_ERROR", data={}, retryable=True)
    check(r_err.is_error() and r_err.status in C.RETRYABLE_STATUSES,
          "RETRYABLE_ERROR 是可重试错误")

    # ---- validate_arguments ----
    spec = C.ToolSpec(
        name="search_evidence", version="v1", description="本地检索",
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["company_id", "query"],
            "properties": {
                "company_id": {"type": "string"},
                "query": {"type": "string", "minLength": 1, "maxLength": 100},
                "k": {"type": "integer", "minimum": 1, "maximum": 20},
                "filters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"period": {"type": "string"}},
                },
            },
        },
        output_schema={"type": "object"},
        allowed_routes=("STANDARD_RAG", "DEEP_RETRIEVAL", "DIRECT_EVIDENCE"),
        max_results=10, timeout_ms=5000, retry_policy="none", cost_class="local",
    )

    check(C.validate_arguments(spec, {"company_id": "300750", "query": "q"}) == [],
          "合法参数无错误")
    errs = C.validate_arguments(spec, {"company_id": "300750"})
    check(any("缺少必需参数: query" in e for e in errs), "缺必需参数被拒绝")
    errs = C.validate_arguments(spec, {"company_id": "300750", "query": "q",
                                       "evil": True})
    check(any("未知参数: evil" in e for e in errs), "未知字段被拒绝")
    errs = C.validate_arguments(spec, {"company_id": "300750", "query": ""})
    check(any("query" in e and "长度小于" in e for e in errs), "字符串长度下限生效")
    errs = C.validate_arguments(spec, {"company_id": "300750", "query": "q", "k": 99})
    check(any("k" in e and "上限" in e for e in errs), "数值上限生效")
    errs = C.validate_arguments(spec, {"company_id": "300750", "query": "q", "k": "5"})
    check(any("k" in e and "类型错误" in e for e in errs), "整数类型错误被拒绝")
    errs = C.validate_arguments(spec, {"company_id": "300750", "query": "q",
                                       "filters": {"evil": "x"}})
    check(any("filters" in e and "未知字段" in e for e in errs),
          "嵌套对象未知字段被拒绝")
    errs = C.validate_arguments(spec, {"company_id": "300750", "query": "q",
                                       "filters": {"period": "2024-12-31"}})
    check(errs == [], "嵌套对象合法字段通过")
    errs = C.validate_arguments(spec, ["not", "a", "dict"])
    check(any("object" in e for e in errs), "非 dict arguments 被拒绝")

    # ---- enforce_arguments ----
    try:
        C.enforce_arguments(spec, {"company_id": "300750", "query": "q", "k": 99})
        check(False, "非法参数 enforce 应抛异常")
    except C.ToolValidationError:
        check(True, "enforce_arguments 非法参数抛 ToolValidationError")

    # ---- exactlyOneOf（互斥参数门，公共契约扩展）----
    excl = C.ToolSpec(
        name="search_evidence", version="v1", description="互斥门样例",
        input_schema={
            "type": "object", "additionalProperties": False, "required": ["q"],
            "exactlyOneOf": ["node_ids", "evidence_ids"],
            "properties": {
                "q": {"type": "string", "minLength": 1},
                "node_ids": {"type": "array", "minItems": 1,
                             "items": {"type": "string"}},
                "evidence_ids": {"type": "array", "minItems": 1,
                                 "items": {"type": "string"}},
            },
        },
        output_schema={"type": "object"}, allowed_routes=("DIRECT_EVIDENCE",),
        max_results=5, timeout_ms=1000, retry_policy="none", cost_class="local")
    check(C.validate_arguments(excl, {"q": "x", "node_ids": ["n1"]}) == [],
          "恰提供一个 selector 时无错误")
    check(C.validate_arguments(excl, {"q": "x", "evidence_ids": ["e1"]}) == [],
          "恰提供另一个 selector 时无错误")
    check(any("恰" in e or "只能" in e
              for e in C.validate_arguments(excl, {"q": "x"})),
          "两个 selector 都不提供被拒绝")
    check(any("恰" in e or "只能" in e
              for e in C.validate_arguments(
                  excl, {"q": "x", "node_ids": ["n1"], "evidence_ids": ["e1"]})),
          "两个 selector 同时提供被拒绝")
    try:
        C.enforce_arguments(excl, {"q": "x"})
        check(False, "缺 selector 时 enforce 应抛异常")
    except C.ToolValidationError:
        check(True, "缺 selector 时 enforce_arguments 抛 ToolValidationError")

    # ---- M930-2 树材料工具公共契约补齐 ----
    from harness import tree_tools as TT

    name = TT.TREE_INSPECT_TOOL_NAME
    check(C.TOOL_NAMES.count(name) == 1,
          f"{name} 在 TOOL_NAMES 中恰出现一次")
    check(TT.TREE_INSPECT_SPEC.name == name, "ToolSpec.name 与公开常量一致")
    check(TT.TREE_INSPECT_SPEC.version == TT.TREE_INSPECT_TOOL_VERSION,
          "ToolSpec.version 与公开常量一致（wire 变更必须升版本）")
    check(TT.TREE_INSPECT_SPEC.version == "v8",
          "v8 是当前 wire 版本（M930-3 读取计划批 `tim-2`：新增**可选**入参 `span_cursor` "
          "与 `over_max_spans` 条目上的续读读数 `next_cursor` / `unread_span_positions` / "
          "`total_span_positions` / `resumed_at_span_position`。这是一条**纯增量**接口："
          "不带 cursor 的调用入参与 `v7` 逐字相同；cursor 只在 `node_ids` selector 下有意义，"
          "错节点 / 越界 / 与 `evidence_ids` 同用一律 fail-closed。`v7` 的四条边界"
          "（`release_id`＋`table_id`＋`local_proof_id` 身份键、逐格 `cells` 读视图、"
          "文档级 `population_scope` 的 `table_refusals`、`tobj-*`/`tom-1` 历史只读兼容）"
          "与 `candidates`/`gaps`/`content_dispositions` 的形状语义均不变）")
    check(TT.TREE_INSPECT_SPEC.input_schema.get("exactlyOneOf")
          == ["node_ids", "evidence_ids"],
          "ToolSpec 声明的互斥 selector 与公开常量一致")
    cursor_spec = TT.TREE_INSPECT_SPEC.input_schema["properties"].get("span_cursor")
    check(cursor_spec is not None
          and cursor_spec.get("required") == ["node_id", "span_index"]
          and cursor_spec.get("additionalProperties") is False,
          "span_cursor 是声明了必填两键、禁额外键的可选对象")
    check("span_cursor" not in set(TT.TREE_INSPECT_SPEC.input_schema.get("required", ())),
          "span_cursor 不得进必填：续读是调用方显式选择，首次调用与 v7 逐字相同")

    with tempfile.TemporaryDirectory() as td:
        reg = R.ToolRegistry(audit_dir=Path(td))
        reg.register(TT.TREE_INSPECT_SPEC, lambda args: None)
        registered = reg.get(name)
        check(registered is not None
              and registered.version == TT.TREE_INSPECT_TOOL_VERSION,
              "registry 中 ToolSpec 版本与公共声明一致")
        check(registered is TT.TREE_INSPECT_SPEC,
              "registry 登记的就是公共声明的那个 spec（不是另建一份）")
        try:
            reg.register(TT.TREE_INSPECT_SPEC, lambda args: None)
            check(False, "同名工具重复注册应被拒绝")
        except C.ToolValidationError:
            check(True, "同名工具重复注册被拒绝（名称唯一性由 registry 强制）")

    ident = {"company_id": "c1", "document_id": "d1", "document_version": "dv1",
             "evidence_set_version": "es1", "need_id": "need-1"}
    check(C.validate_arguments(TT.TREE_INSPECT_SPEC, dict(ident, node_ids=["n1"])) == [],
          "node selector 合法参数通过")
    check(C.validate_arguments(TT.TREE_INSPECT_SPEC, dict(ident, evidence_ids=["e1"])) == [],
          "evidence selector 合法参数通过")
    errs_unknown = C.validate_arguments(
        TT.TREE_INSPECT_SPEC, dict(ident, node_ids=["n1"], verified=True))
    check(any("未知参数" in e for e in errs_unknown),
          "自报 verified 等未知字段被拒绝")
    errs_none = C.validate_arguments(TT.TREE_INSPECT_SPEC, dict(ident))
    check(any("只能" in e or "恰" in e for e in errs_none),
          "两个 selector 都缺失被拒绝")
    errs_both = C.validate_arguments(
        TT.TREE_INSPECT_SPEC, dict(ident, node_ids=["n1"], evidence_ids=["e1"]))
    check(any("只能" in e or "恰" in e for e in errs_both),
          "两个 selector 同时出现被拒绝（互斥门有效）")
    errs_empty = C.validate_arguments(TT.TREE_INSPECT_SPEC, dict(ident, node_ids=[]))
    check(any("元素数" in e or "只能" in e for e in errs_empty),
          "空 selector 数组被拒绝（不得空选择器蒙混过关）")

    src = Path(TT.__file__).read_text(encoding="utf-8")
    check(src.count("registry.register(TREE_INSPECT_SPEC") == 1,
          "注册点唯一：只有一处 register(TREE_INSPECT_SPEC)")
    check("LiveVerifiedSpanSource" in src,
          "会话仍绑定 run-bound live 源（不接受路径/快照参数）")

    phase45 = {"verify_claim", "emit_claim", "review_claim", "assemble_report",
               "finalize_section"}
    check(not (set(C.TOOL_NAMES) & phase45),
          f"不引入 verify_claim 或其他 Phase 4/5 工具（命中：{sorted(set(C.TOOL_NAMES) & phase45)}）")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

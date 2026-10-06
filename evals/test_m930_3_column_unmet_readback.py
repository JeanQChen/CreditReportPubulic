"""逐条 typed「栏目未达」原因从运行现场 → 只读诊断 → 人读页的回归。

## 这条回归盯着什么

runtime 早就在 `TopicRuntimeResult.gaps` 上写下**逐条**栏目未达原因（闭集
`harness.topic_runtime.UNMET_COLUMN_REASONS`），但那一层过去**只活在运行现场**：
`failure_diagnostics.json` 逐 aspect 只报覆盖门/资格门，人读页更没有这一项。于是下面五件
彼此不可互推的事在产物里被压成同一件：

    预算阻止派发（本轮根本没查） / 已派发但未命中 / 命中但不支持本栏目 /
    表未获资格 / 检索审计不完整

压成一件之后，最顺手的读法就是「上传语料里没有」。这条回归把整条搬运链钉住：

    运行现场（`TopicRuntimeResult.gaps`）
      → `RealInputs.topic_results`（新留一份，Pack 本体不带这一层）
      → `failure_diagnostics.json` 逐 aspect `column_unmet` + 逐节 `column_unmet_summary`
      → `business_material_readback` 的单元格与 `_open_issues`

## 两条不许破的边界

1. **不可判定 ≠ 空**。本节取不到运行结果（财务节走 `FinancialFactPack`，不走 topic 研究
   相位）时，条目是 `available=false` 的**不可判定**，`entries` 不是空的——空会被读成
   「本栏目没有问题」。
2. **闭集只有一份**。五条原因码从 runtime 取，**不**在诊断侧抄一份字面量：抄一份之后
   runtime 加了第六类，诊断侧的逐条计数里它会**默默缺席**，而缺席与「本次没命中」在产物上
   长得一模一样。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evaluation import business_material_readback as BR  # noqa: E402
from evaluation import run_m930_3_acceptance as ACC  # noqa: E402
from harness import topic_runtime as TR  # noqa: E402

_RUNNER_SRC = Path(ACC.__file__).read_text(encoding="utf-8")


def _gap(*, aspect_id: str, reasons: tuple[str, ...], **extra) -> dict:
    """一条**形状真实**的 runtime `declared_gaps` 条目（键取自 `topic_runtime` 的写入点）。"""
    row = {
        "aspect_id": aspect_id, "reason": "column_unmet", "column_status": "partial",
        "unmet_column_reasons": list(reasons),
        "dispatch_counts": dict(extra.pop("dispatch_counts", {})),
        "dispatched_without_call": {},
        "material_count": 0, "table_material_count": 0,
        "table_object_material_count": 0, "fact_count": 0,
        "rule_version": TR.MANDATORY_FIRST_READ_RULE_VERSION,
        "closed_set": list(TR.UNMET_COLUMN_REASONS),
        "detail": "本栏目未达 covered 的**逐条**原因（彼此不可互推）："
                  + "、".join(reasons),
    }
    row.update(extra)
    return row


def _result(*gaps: dict) -> SimpleNamespace:
    return SimpleNamespace(gaps=tuple(gaps))


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

    # ==================================================================
    # 1. 闭集只有一份：诊断侧从 runtime 取，不抄字面量
    # ==================================================================
    check(ACC._unmet_column_reasons_closed_set() == tuple(TR.UNMET_COLUMN_REASONS),
          "闭集取自 `harness.topic_runtime.UNMET_COLUMN_REASONS`"
          "（诊断侧与 runtime 必须是同一份清单）")
    # 判据落在**代码行**上（注释只为人读，不参与产物，也不构成第二份清单）：把注释剥掉之后，
    # runner 里不得再出现任何一个原因码。抄一份之后 runtime 加第六类，诊断侧的逐条计数里
    # 它会**默默缺席**——而缺席与「本次没命中」在产物上长得一模一样。
    code_lines = "\n".join(line for line in _RUNNER_SRC.splitlines()
                           if not line.lstrip().startswith("#"))
    stray = [code for code in TR.UNMET_COLUMN_REASONS if code in code_lines]
    check(not stray,
          f"runner 的**代码行**里不得出现原因码字符串字面（实得 {stray}）："
          "闭集只有 runtime 那一份，产物只搬运不复制")

    # ==================================================================
    # 2. 逐条搬运：五条原因**分开**留在同一条记录里，不塌成一句门名
    # ==================================================================
    results = {
        "company": (
            _result(_gap(aspect_id="a-1", reasons=("budget_blocked_dispatch",)),
                    _gap(aspect_id="a-2",
                         reasons=("dispatched_no_candidate",
                                  "material_found_but_unsupported"),
                         dispatch_counts={"required_search@doc-b": "budget_blocked"})),
            _result(_gap(aspect_id="a-3", reasons=("table_material_unqualified",),
                         table_material_count=2, table_object_material_count=0)),
        ),
    }
    by_aspect = ACC._column_unmet_by_aspect(results.get("company"))
    check(sorted(by_aspect) == ["a-1", "a-2", "a-3"],
          f"逐 aspect 成键（实得 {sorted(by_aspect)}）")
    check(by_aspect["a-1"]["typed_reasons"] == ["budget_blocked_dispatch"],
          "「本轮预算没让这次检索发生」单独成条")
    check(by_aspect["a-2"]["typed_reasons"]
          == ["dispatched_no_candidate", "material_found_but_unsupported"],
          "同一栏目可以同时命中多条：已派发未命中 与 材料到了但话对不上栏目 是**两件事**，"
          "不得只留一条")
    check(by_aspect["a-3"]["typed_reasons"] == ["table_material_unqualified"],
          "「表未获资格」单独成条")
    check(by_aspect["a-2"]["entries"][0]["dispatch_counts"]
          == {"required_search@doc-b": "budget_blocked"},
          "派发去路计数逐条搬运（它回答「这几次派发各自去哪了」）")
    check(by_aspect["a-1"]["closed_set"] == list(TR.UNMET_COLUMN_REASONS),
          "每条记录都带**完整**闭集：读者才分得清「命中了两条」与「只判了两条」")
    check(ACC._column_unmet_by_aspect(None) == {},
          "没有运行结果时 `_column_unmet_by_aspect` 返回空（**不**编造原因）")

    # 门名条目（覆盖门未通过）与 `column_unmet` 是同一次判定的两个粒度：两个都要在。
    mixed = ACC._column_unmet_by_aspect(
        (_result(_gap(aspect_id="a-9", reasons=("material_found_but_unsupported",)),
                 {"aspect_id": "a-9", "reason": "coverage_gate_not_met",
                  "unmet_column_reasons": ["material_found_but_unsupported"],
                  "dispatch_counts": {}, "detail": "覆盖门未通过"}),))
    check([e["reason"] for e in mixed["a-9"]["entries"]]
          == ["column_unmet", "coverage_gate_not_met"],
          "粗粒度的门名与逐条 typed 原因**并列留档**：门名只回答「没过」，原因才回答「为什么」")
    check(mixed["a-9"]["typed_reasons"] == ["material_found_but_unsupported"],
          "同一条原因出现两次只记一次（去重的是**原因**，不是记录）")

    # 别的身份的 gap（`aspect_terminal_gap` / `tree_material_gaps` / `research_contract_gap` …）
    # 回答的是**另一类**问题，各自已有读回通道（Pack 的 `unresolved` / `contract_gaps`）。
    # 混进这个键会让字段名与内容不符，也会把 9 条同名材料 gap 淹掉唯一那一条栏目级结论。
    others = ACC._column_unmet_by_aspect((_result(
        {"aspect_id": "a-8", "reason": "tree_material_gaps", "detail": "材料级"},
        {"aspect_id": "a-8", "reason": "aspect_terminal_gap", "detail": "终态"},
        {"aspect_id": "a-8", "reason": "research_contract_gap", "detail": "Contract 未取得"},
        _gap(aspect_id="a-8", reasons=("dispatched_no_candidate",))),))
    check([e["reason"] for e in others["a-8"]["entries"]] == ["column_unmet"],
          f"只收栏目级裁决条目（实得 {[e['reason'] for e in others['a-8']['entries']]}）："
          "别的身份的 gap 已有自己的读回通道，混进来会让字段名与内容不符")

    # ==================================================================
    # 3. 逐节汇总：计数为 0 的那一条**仍然列出**
    # ==================================================================
    counts = {code: sum(1 for row in by_aspect.values() if code in row["typed_reasons"])
              for code in TR.UNMET_COLUMN_REASONS}
    check(sorted(counts) == sorted(TR.UNMET_COLUMN_REASONS),
          "汇总按**闭集全量**给计数：计数为 0 的那一条也列出（它「参与过判定、只是不成立」，"
          "与「产物根本没有这一类」不是一件事）")
    check(counts["table_material_unqualified"] == 1
          and counts["search_audit_incomplete"] == 0,
          f"计数逐条对应（实得 {counts}）")

    # ==================================================================
    # 4. 人读页：逐条陈列，且**缺键不等于没问题**
    # ==================================================================
    text = BR._open_issues({"states_observed": ["not_navigated"],
                            "chapter_scope": {},
                            "column_unmet": by_aspect["a-2"]})
    check("已派发但未命中" in text and "命中但不支持本栏目" in text,
          f"两条原因必须**逐条**出现在人读行里（实得 {text!r}）")
    check("未导航到" in text,
          "状态与原因同时在：状态说「停在哪一步」，原因说「为什么停」")
    check("语料" not in text and "上传" not in text,
          "逐条原因里**不得**出现「语料里没有 / 上传里没有」这类把「本轮没查」"
          "写成「语料里没有」的措辞")

    unseen = BR._column_unmet_render({})
    check(unseen["available"] is False and unseen["typed"] == [],
          "产物没带 `column_unmet` 键 ⇒ `available=false`（不可判定）")
    check("不可判定" in unseen["lines"][0] and "（无）" not in unseen["lines"][0],
          f"缺键时写「{BR.UNMET_COLUMN_UNAVAILABLE}」，**不**写「（无）」——"
          "后者会被读成「本栏目没有问题」")
    # 幂等：人读页的 Markdown 走的是**摘要**单元格（`_cell_summary` 已经渲染过一次）。
    # 少了这一支，第二次渲染会把已渲染的 dict 当原始诊断读，`typed_reasons` 取不到 ⇒
    # 每一栏都印成「运行侧未命中任何一条 typed 原因」——凭空多出来的一句结论，比不印更坏。
    rendered = BR._column_unmet_render({"column_unmet": by_aspect["a-1"]})
    check(BR._column_unmet_render({"column_unmet": rendered}) == rendered,
          "已渲染的形状必须**幂等**返回（摘要单元格会被再渲染一次）")
    check("本轮预算阻止派发" in BR._open_issues(
        {"states_observed": [], "chapter_scope": {}, "column_unmet": rendered}),
        "拿摘要单元格再渲染一次，逐条原因仍在（不得退化成「未命中任何一条」）")
    check(BR._column_unmet_render({"column_unmet": {
        "available": False, "entries": [], "typed_reasons": [],
        "closed_set": [], "detail": "本节没有 typed 栏目未达原因可读"}})["available"] is False,
        "runtime 明写 `available=false` 时同样是不可判定（不是「没有问题」）")

    empty = BR._column_unmet_render({"column_unmet": {
        "available": True, "entries": [], "typed_reasons": [],
        "closed_set": list(TR.UNMET_COLUMN_REASONS)}})
    check(empty["available"] is True and empty["typed"] == []
          and "未命中任何一条 typed 原因" in empty["lines"][0],
          "`available=true` 且逐条为空是 runtime 的**结论**（与「不可判定」分开写）")
    check(any("闭集" in line or "未命中的其余" in line for line in empty["lines"]),
          "结论档也要列出**未命中的其余闭集原因**：否则读者分不清"
          "「这一栏一条都不成立」与「判定只覆盖了两类」")

    # 人读页的措辞表可以有自己的键（它是**展示层**，不是第二份判据清单），但键集必须与
    # runtime 闭集**逐字相等**：少一个 ⇒ 那一类原因在页面上印成裸码；多一个 ⇒ 页面上留着
    # 一个 runtime 永远不会产生的分类。两种都是漂移，必须在这里先红。
    check(set(BR.UNMET_COLUMN_LABELS) == set(TR.UNMET_COLUMN_REASONS),
          f"人读措辞表的键集必须与 runtime 闭集相等（实得多 "
          f"{sorted(set(BR.UNMET_COLUMN_LABELS) - set(TR.UNMET_COLUMN_REASONS))} / "
          f"少 {sorted(set(TR.UNMET_COLUMN_REASONS) - set(BR.UNMET_COLUMN_LABELS))}）")
    check(BR.UNMET_COLUMN_LABELS["budget_blocked_dispatch"].startswith("本轮预算阻止派发"),
          "「预算阻止派发」的措辞点明这是**本轮没查**，不是「查过没有」")
    check("命中但不支持本栏目" in BR.UNMET_COLUMN_LABELS["material_found_but_unsupported"],
          "「取得 ≠ 支持」在措辞里写明：材料拿到了不等于栏目被支持")

    # ==================================================================
    # 5. 从诊断产物读回：`load_fact_states` 把这一层带出来
    # ==================================================================
    import tempfile
    payload = {
        "schema_version": ACC.FAILURE_DIAGNOSTICS_SCHEMA_VERSION,
        "sections": {"company": {"aspects": [
            {"aspect_id": "a-1", "authority_status": "partial",
             "writable_facts": [], "column_unmet": by_aspect["a-1"]},
            {"aspect_id": "a-2", "authority_status": "not_found",
             "writable_facts": [], "column_unmet": by_aspect["a-2"]},
        ]}},
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "failure_diagnostics.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        states = BR.load_fact_states(path)
        check(states["a-1"]["column_unmet"]["typed_reasons"] == ["budget_blocked_dispatch"],
              "`load_fact_states` 必须把逐条原因读出来（它过去只读事实资格）")
        check("column_unmet" in states["a-2"] and "column_unmet" not in states.get("a-3", {}),
              "逐 aspect 取自己那一份，不跨 aspect 混用")

        # 缺键的旧产物：留 `None`，由渲染侧写「不可判定」——**不**补一个空 dict。
        legacy = {"sections": {"company": {"aspects": [
            {"aspect_id": "a-1", "writable_facts": [], "authority_status": "covered"}]}}}
        (Path(tmp) / "legacy.json").write_text(
            json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        legacy_states = BR.load_fact_states(Path(tmp) / "legacy.json")
        check(legacy_states["a-1"]["column_unmet"] is None,
              "旧产物没有这一键时留 `None`（**不得**补成 `{}`：那会被渲染成「没有问题」）")

    check(BR.load_fact_states(None) == {},
          "没有诊断产物时返回空字典（不凭空断言任何状态）")

    # ==================================================================
    # 6. 接线：产物与人读页都真的用上了这两个渲染器
    # ==================================================================
    check('"column_unmet": (column_unmet.get(aspect_id)' in _RUNNER_SRC,
          "逐 aspect 的 `column_unmet` 真的接进了诊断产物")
    check('"column_unmet_summary"' in _RUNNER_SRC,
          "逐节的汇总也接进了产物")
    check('topic_results=topic_results' in _RUNNER_SRC
          and 'topic_results: dict = dataclasses.field(default_factory=dict)' in _RUNNER_SRC,
          "运行现场那一份逐 topic 结果真的留在 `RealInputs` 上（Pack 本体不带这一层）")
    check(ACC.FAILURE_DIAGNOSTICS_SCHEMA_VERSION == "failure-diagnostics/3",
          "诊断载荷形状变了 ⇒ 版本号前进（旧读者据此判读）")
    br_src = Path(BR.__file__).read_text(encoding="utf-8")
    check(br_src.count("_column_unmet_render(") >= 4,
          "人读页的三个出口（`_cell_summary` / `_matrix` / `_open_issues`）走**同一个**渲染器，"
          "不各写一套")

    result = {"passed": passed, "failed": failed, "skipped": 0, "details": details}
    return result


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

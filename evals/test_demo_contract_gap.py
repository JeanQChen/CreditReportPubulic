"""Eval: research 侧 ContractGap / ResearchBlock 的类型门与持久化边界（§16.10 #34，M930-3A）。

用法: python -m evals.test_demo_contract_gap

本模块的 3A 部分证明：
 1. gap / block 只在 Contract **必需**项仍未取得时形成：类型层强制 current Contract basis
    （contract_version + requirement_fingerprint）、非空 required-unmet 证明与登记理由码；
 2. `rejected_candidate_ids` 是**可选引用**，不是 gap 的成因 —— 拒绝一个候选不等于形成 gap，
    反之无 Contract 依据也不得因拒绝自动造 gap（结构上：本类型没有任何 candidate/decision
    身份字段，造 gap 只需要 required-unmet 证明）；
 3. #34（持久化边界）：gap / block 只落研究侧边界（Pack 的 `topic_contract_gap` /
    `topic_research_block` 子表），Section Store 的 v2 family 里没有 gap/block 表；
    `ContractGap` 与 `SectionUnresolved` 是两套身份，不得互相冒充。

3B 部分（rejection 无 audit、必需事实未取得却无 gap/block、`FollowUpNeed` 冒充 gap）在
M930-3B 批次补齐；本文件当前**不**断言那些行为。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_schema as TS  # noqa: E402
from harness import topic_store as TST  # noqa: E402
from sections import schema as SS  # noqa: E402
from sections import store as ST  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def expect_raises(msg: str, fn, needle: str) -> None:
    try:
        fn()
    except TS.SchemaValidationError as exc:
        if needle in str(exc):
            check(True, f"{msg}：{str(exc)[:70]}")
        else:
            check(False, f"{msg}：期望理由含 {needle!r}，实际 {str(exc)[:90]}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"{msg}：期望 SchemaValidationError，实际 {type(exc).__name__}: {exc}")
    else:
        check(False, f"{msg}：未 fail-closed")


_UNMET = ("annual_report_body",)


def _gap(**kw) -> TS.ContractGap:
    base = dict(
        gap_id=TS.derive_contract_gap_id("t1", "a1", "not_found", _UNMET),
        topic_id="t1", aspect_id="a1", question_ids=("q1",),
        contract_version="v2", contract_sha256=TS.sha256_canonical({"c": "v2"}),
        requirement_fingerprint="rfp_1", unmet_required_items=_UNMET,
        reason_code="not_found", impact_scopes=("subject",),
        required_unmet_proof="Contract 要求 annual_report_body，本轮无合格权威事实支撑")
    base.update(kw)
    return TS.ContractGap(**base)


def _block(**kw) -> TS.ResearchBlock:
    base = dict(
        block_id=TS.derive_research_block_id("t1", "a1", "source_policy", _UNMET),
        topic_id="t1", aspect_id="a1", question_ids=("q1",),
        contract_version="v2", contract_sha256=TS.sha256_canonical({"c": "v2"}),
        requirement_fingerprint="rfp_1", unmet_required_items=_UNMET,
        block_kind="source_policy", detail="SourcePolicy 不允许该来源类，研究路径被挡住",
        required_unmet_proof="Contract 要求 annual_report_body，允许的来源类均不可达")
    base.update(kw)
    return TS.ResearchBlock(**base)


# ---------------------------------------------------------------------------
# 1. ContractGap：只在 Contract 必需项未取得时形成
# ---------------------------------------------------------------------------

def _check_contract_gap_gate() -> None:
    gap = _gap()
    check(gap.gap_id.startswith("cgap_") and TS.ContractGap.from_dict(gap.to_dict()).to_dict()
          == gap.to_dict(),
          "ContractGap 往返一致、身份带 cgap_ 前缀")
    check(gap.gap_id == TS.derive_contract_gap_id("t1", "a1", "not_found", _UNMET),
          "gap 身份由 topic + aspect + reason_code + required-unmet 项确定性派生")
    check(_gap(reason_code="blocked",
               gap_id=TS.derive_contract_gap_id("t1", "a1", "blocked", _UNMET)).gap_id
          != gap.gap_id,
          "理由码不同 ⇒ gap 身份不同")
    check(_gap(unmet_required_items=("annual_report_body", "audit_opinion"),
               gap_id=TS.derive_contract_gap_id("t1", "a1", "not_found",
                                                ("annual_report_body", "audit_opinion"))).gap_id
          != gap.gap_id,
          "未满足项集合不同 ⇒ gap 身份不同")

    # 必须绑定 current Contract basis + 非空 required-unmet 证明。
    expect_raises("缺 contract_version",
                  lambda: _gap(contract_version=""), "Contract basis")
    expect_raises("缺 requirement_fingerprint",
                  lambda: _gap(requirement_fingerprint=""), "Contract basis")
    expect_raises("空 unmet_required_items",
                  lambda: _gap(unmet_required_items=()), "无 Contract-required 未满足项即不得形成 gap")
    expect_raises("空 required_unmet_proof",
                  lambda: _gap(required_unmet_proof=""), "required_unmet_proof 必须非空")
    expect_raises("空 topic_id / aspect_id",
                  lambda: _gap(aspect_id=""), "topic_id/aspect_id 必须非空")
    expect_raises("未登记 reason_code",
                  lambda: _gap(reason_code="whatever"), "reason_code")
    expect_raises("未登记 impact_scope",
                  lambda: _gap(impact_scopes=("everything",)), "impact_scopes")
    expect_raises("非 hex contract_sha256",
                  lambda: _gap(contract_sha256="nothex"), "sha256 hex")
    expect_raises("gap_id 与派生值不符",
                  lambda: _gap(gap_id="cgap_x"), "与确定性派生值")
    expect_raises("from_dict 读数未知字段",
                  lambda: TS.ContractGap.from_dict({**gap.to_dict(), "decision_id": "fqd_1"}),
                  "未知字段")

    # rejection ≠ gap：拒绝只作为**可选引用**出现，不构成 gap 的存在条件。
    check(_gap().rejected_candidate_ids == (),
          "gap 可以不含任何 rejected candidate（拒绝不是 gap 的成因）")
    with_rejection = _gap(rejected_candidate_ids=("fc_1",),
                          gap_id=TS.derive_contract_gap_id("t1", "a1", "not_found", _UNMET))
    check(with_rejection.gap_id == gap.gap_id,
          "附加 rejected_candidate_ids 不改变 gap 身份（拒绝是引用，不是成因）")
    check(not ({"candidate_id", "decision_id", "verdict", "candidate_source_kind"}
               & set(TS.ContractGap.__dataclass_fields__)),
          "ContractGap 没有任何 candidate/决定身份字段（不得由单个 rejected 自动生成）")


# ---------------------------------------------------------------------------
# 2. ResearchBlock：与 rejection 是两条独立记录
# ---------------------------------------------------------------------------

def _check_research_block_gate() -> None:
    block = _block()
    check(block.block_id.startswith("rblk_")
          and TS.ResearchBlock.from_dict(block.to_dict()).to_dict() == block.to_dict(),
          "ResearchBlock 往返一致、身份带 rblk_ 前缀")
    check(block.block_id != _block(block_kind="authority",
                                   block_id=TS.derive_research_block_id("t1", "a1", "authority",
                                                                        _UNMET)).block_id,
          "block_kind 不同 ⇒ block 身份不同")
    expect_raises("缺 contract basis",
                  lambda: _block(contract_version=""), "必须绑定 current Contract basis")
    expect_raises("空 required_unmet_proof",
                  lambda: _block(required_unmet_proof=""), "required_unmet_proof 必须非空")
    expect_raises("空 detail",
                  lambda: _block(detail=""), "detail 必须非空")
    expect_raises("未登记 block_kind",
                  lambda: _block(block_kind="misc"), "block_kind")
    expect_raises("block_id 与派生值不符",
                  lambda: _block(block_id="rblk_x"), "与确定性派生值")
    check(not ({"disposition", "verdict", "decision_id", "admission_state", "reason_code"}
               & set(TS.ResearchBlock.__dataclass_fields__)),
          "block 没有 disposition / verdict / decision 字段（资格决定不得塞进 detail 之类泛化字段）")


# ---------------------------------------------------------------------------
# 3. #34（3A）：gap / block 只落研究侧边界
# ---------------------------------------------------------------------------

def _check_persistence_boundary() -> None:
    check(TS.ContractGap.__module__ == "harness.topic_schema"
          and TS.ResearchBlock.__module__ == "harness.topic_schema",
          "gap / block 是研究侧（harness）身份")
    check({"topic_contract_gap", "topic_research_block"} <= set(TST._SUCCESSOR_TABLES)
          and {"topic_contract_gap", "topic_research_block"} <= set(TST._IMMUTABLE_TABLES),
          "gap / block 持久化在 research 侧（Pack 的 topic_contract_gap / topic_research_block，只追加）")

    check(not any("gap" in t or "block" in t for t in ST.V2_SECTION_TABLES),
          "#34：Section Store 的 v2 family 里没有 gap / block 表")
    check(not any("gap" in v or "block" in v for v in ST.V2_SECTION_VIEWS),
          "#34：Section Store 的 v2 投影视图也不承载 gap / block")
    migration_sql = "\n".join(ST._migration_5_statements())
    check("contract_gap" not in migration_sql and "research_block" not in migration_sql,
          "#34：migration 5 的 DDL 里没有任何 gap / block 表（authoritative gap 不进 Section store）")

    # ContractGap 与 SectionUnresolved 是两套身份，不得互相冒充。
    check(TS.ContractGap is not SS.SectionUnresolved
          and not issubclass(TS.ContractGap, SS.SectionUnresolved)
          and not issubclass(SS.SectionUnresolved, TS.ContractGap),
          "ContractGap 与 SectionUnresolved 是互不继承的两套身份")
    check("unresolved_id" not in TS.ContractGap.__dataclass_fields__
          and "gap_id" not in SS.SectionUnresolved.__dataclass_fields__,
          "两者的身份字段互不承认（gap_id / unresolved_id 各属一侧）")
    check(not (set(TS.ContractGap.__dataclass_fields__)
               & {"section_id", "unresolved_id", "state", "detail", "blocking_effects"}),
          "ContractGap 没有 Section 侧缺口字段（不得被 SectionUnresolved 冒充）")
    check(not ({"contract_version", "contract_sha256", "requirement_fingerprint",
                "unmet_required_items", "required_unmet_proof"}
               & set(SS.SectionUnresolved.__dataclass_fields__)),
          "SectionUnresolved 没有 Contract 依据字段（不得被 ContractGap 冒充）")

    # Section 侧 unresolved family 用 Writer 侧判别键承载，不是 gap 身份。
    check("current_section_unresolved_v2" in ST.V2_SECTION_TABLES,
          "Section 侧未解决项走 current_section_unresolved_v2（writer 侧 carrier）")
    unresolved_ddl = [s for s in ST._migration_5_statements()
                      if "current_section_unresolved_v2" in s and "PRIMARY KEY" in s]
    check(bool(unresolved_ddl)
          and "PRIMARY KEY (draft_id, unresolved_kind, unresolved_id)" in unresolved_ddl[0],
          "Section 侧未解决项的主键是 (draft_id, unresolved_kind, unresolved_id)："
          "先按 draft 归属，再按 kind 区分 unresolved / block projection")


def main() -> dict:
    _check_contract_gap_gate()
    _check_research_block_gate()
    _check_persistence_boundary()
    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)

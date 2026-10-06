"""Focused offline M930-4 Controller tests on a retained real dual-section run.

Run: python -m evals.test_m930_4_cited_controller
No model, network, research, or historical-run writes occur.
"""

from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import mock_open, patch

from assurance import schema as AS
from assurance import cited_controller as CC
from assurance.cited_controller import (
    CitedControllerError, assess_persisted_run,
)
from scripts.run_m930_4_assurance import main as run_assurance_main


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "evaluation/results/m930_3_cited_real_dual_v2_r1"


def _assess_with_mutation(relative_path: str, change):
    """Tamper with one decoded fixture in memory; source files stay untouched."""
    original = CC._read_json
    target = (SOURCE / relative_path).resolve()
    def read(path):
        row, digest = original(path)
        if Path(path).resolve() == target:
            row = deepcopy(row)
            change(row)
        return row, digest
    with patch.object(CC, "_read_json", side_effect=read):
        return CC.assess_persisted_run(SOURCE)


class CitedControllerTests(unittest.TestCase):
    def test_real_run_reused_without_new_review_or_release(self) -> None:
        before = (SOURCE / "company/cited_preview.md").read_bytes()
        result = assess_persisted_run(SOURCE)
        self.assertEqual(result["new_llm_calls"], 0)
        self.assertIsNone(result["cross_section_report_version"])
        self.assertEqual(result["cross_section_semantic_review"],
                         "input_prepared_review_not_run")
        self.assertEqual(result["formal_phase_closure"], "formal_closure_not_evaluated")
        company, financial = result["section_assessments"]
        self.assertNotEqual(company["report_version"], financial["report_version"])
        self.assertEqual(len(company["blocked_sentence_ids"]), 7)
        self.assertEqual(company["prior_supported_issue_count"], 16)
        self.assertEqual(company["prior_review_scope"], "cited_prose_only")
        self.assertEqual(financial["a2_review_state"], "review_not_run")
        self.assertEqual(len(financial["blocked_sentence_ids"]), 0)
        self.assertEqual(financial["prior_supported_issue_count"], 21)
        for section in (company, financial):
            hard = AS.HardGateResult.from_dict(section["hard_gate_result"])
            prior = AS.ReviewerRunRecord.from_dict(section["reviewer_run_record"])
            assurance = AS.AssuranceResult.from_dict(section["assurance_result"])
            self.assertTrue(hard.release_blocked)
            self.assertFalse(hard.reviewability_blocked)
            self.assertEqual(prior.outcome, "reviewed")
            self.assertEqual(assurance.system_review_state, "system_review_not_passed")
            self.assertFalse(assurance.system_release_eligible)
            self.assertEqual(assurance.human_review_state, "human_not_reviewed")
        self.assertEqual(before, (SOURCE / "company/cited_preview.md").read_bytes())

    def test_identity_and_review_tamper_fail_closed(self) -> None:
        with self.assertRaises((CitedControllerError, ValueError)):
            _assess_with_mutation("company/cited_report_version.json",
                                  lambda row: row.__setitem__("report_version", "crpv_fake"))
        with self.assertRaises((CitedControllerError, ValueError)):
            _assess_with_mutation("company/review_issues.json",
                                  lambda row: row["issues"][0].__setitem__("category", "contradicted"))
        with self.assertRaises(CitedControllerError):
            _assess_with_mutation("financial/cited_balance_structure__fin_balance_structure.json",
                                  lambda row: row["items"][0].__setitem__("statement", "tampered"))
        with self.assertRaises(CitedControllerError):
            _assess_with_mutation("company/source_table_display.json",
                                  lambda row: row.__setitem__("human_confirmed_count", 12))
        with self.assertRaises(CitedControllerError):
            _assess_with_mutation("company/source_table_display.json",
                                  lambda row: row["regions"][0].__setitem__(
                                      "confirmation_state", "human_confirmed"))

        def cite_another_source(row):
            original = AS.ReviewIssue.from_dict(row["issues"][0])
            changed = AS.ReviewIssue.create(
                report_version=original.report_version,
                unit_ref=AS.ReviewUnitRef.create(
                    unit_kind="citation", unit_id="m99"),
                category=original.category, severity=original.severity,
                blocking=original.blocking, reason=original.reason,
                evidence_refs=original.evidence_refs,
                suggested_target=original.suggested_target,
                schema_version=original.schema_version,
                sentence_id=original.sentence_id, citation_id="m99",
                semantic_category=original.semantic_category)
            row["issues"][0] = changed.to_dict()
        with self.assertRaises(CitedControllerError):
            _assess_with_mutation("company/review_issues.json", cite_another_source)

    def test_ledger_mismatch_blocks_reviewability(self) -> None:
        def change(row):
            for attempt in row["call_budget"]["attempts"]:
                if attempt["section_id"] == "company" and attempt["category"] == "cited_prose_review":
                    attempt["call_id"] = "not-the-attested-call"
        company = _assess_with_mutation("cited_call_ledger.json", change)["section_assessments"][0]
        hard = AS.HardGateResult.from_dict(company["hard_gate_result"])
        self.assertTrue(hard.reviewability_blocked)
        self.assertFalse(company["assurance_result"]["system_release_eligible"])
        self.assertEqual(company["assurance_result"]["system_review_state"],
                         "system_review_not_run")
        self.assertIsNone(company["reviewer_run_record"])

        def duplicate(row):
            attempts = row["call_budget"]["attempts"]
            matching = next(attempt for attempt in attempts
                            if attempt["section_id"] == "company"
                            and attempt["category"] == "cited_prose_review")
            attempts.append(deepcopy(matching))
        company = _assess_with_mutation("cited_call_ledger.json", duplicate)["section_assessments"][0]
        self.assertTrue(AS.HardGateResult.from_dict(
            company["hard_gate_result"]).reviewability_blocked)

    def test_m4cc_4_report_level_keys_appear_without_moving_old_ones(self) -> None:
        """`m4cc-4`：报告级三块内容进读数，除派生键外旧取值不动。

        没有给 `report_review_runs` 时，覆盖格必须是「输入已备、审阅未运行」——不是「通过」，
        也不是「这块内容不存在」。意见格与覆盖格给出**同一个键集**，读产物的人不必先判断
        「这一格有没有运行」才知道怎么读它。
        """
        from assurance import report_reviewer as RX
        from assurance import report_review as RR

        result = assess_persisted_run(SOURCE)
        states = result["report_states"]

        self.assertEqual(result["policy_version"], CC.POLICY_VERSION)
        self.assertEqual(result["policy_version"], "m4cc-4")
        self.assertEqual(result["new_llm_calls"], 0)
        self.assertEqual(result["cross_section_semantic_review"],
                         states["review_coverage"]["cross_section"])
        self.assertEqual(result["unused_material_selectivity_review"],
                         states["review_coverage"]["material_selectivity"])
        self.assertIsNone(result["cross_section_report_version"])
        self.assertEqual(result["formal_phase_closure"], "formal_closure_not_evaluated")
        self.assertFalse(result["system_release_eligible"])

        # 新键：三块内容的输入在，运行记录一条没有。
        self.assertEqual({s["scope_kind"] for s in result["report_review_scopes"]},
                         set(CC.RR.REPORT_REVIEW_SCOPE_KINDS))
        self.assertEqual(len(result["report_review_inputs"]),
                         len(CC.RR.REPORT_REVIEW_SCOPE_KINDS))
        self.assertEqual(result["report_review_runs"], [])
        self.assertEqual(states["stale_report_review_runs"], [])
        self.assertEqual(result["report_review_calls_recorded"], 0)

        scope_kinds = set(CC.RR.REPORT_REVIEW_SCOPE_KINDS)
        prose_keys = {"company_cited_prose", "financial_cited_prose"}
        self.assertEqual(set(states["review_coverage"]), prose_keys | scope_kinds)
        self.assertEqual(set(states["review_opinions"]), prose_keys | scope_kinds)
        self.assertIn("进了请求", states["unit_count_meaning"])
        for kind in sorted(scope_kinds):
            self.assertEqual(states["review_coverage"][kind],
                             "input_prepared_review_not_run")
            row = states["review_opinions"][kind]
            self.assertIsNone(row["run_id"])
            self.assertEqual(row["issue_count"], 0)
            # 三个**不同**的单元计数各自成键：放进请求 / 收到可解析回复 / 提出问题
            self.assertEqual(row["requested_unit_count"], 0)
            self.assertEqual(row["covered_unit_count"], 0)
            self.assertEqual(row["reported_unit_count"], 0)
            self.assertEqual(row["requested_batch_count"], 0)
            self.assertEqual(row["completed_batch_count"], 0)
            self.assertEqual(row["failed_batch_count"], 0)
            self.assertEqual(row["llm_call_count"], 0)
            self.assertFalse(row["trusted_as_assurance_input"])
            self.assertIn("进了请求", row["coverage_meaning"])
            declared = next(s for s in result["report_review_scopes"]
                            if s["scope_kind"] == kind)["unit_count"]
            self.assertEqual(row["declared_unit_count"], declared)
            self.assertEqual(row["excluded_unit_count"], declared)

        # **预算与页面数字一致**：页面/读回里那个「调用上限（推出）」是**真的**把输入切一遍
        # 得到的批数，与运行入口按它写死进预算政策的上限逐一相等。
        for summary in result["report_review_scopes"]:
            scope = RR.ReportReviewScope.from_dict(
                next(s for s in result["report_review_inputs"]
                     if s["scope_kind"] == summary["scope_kind"]))
            batches = RX.plan_report_review_batches(scope)
            self.assertEqual(summary["derived_batch_count"], len(batches))
            self.assertEqual(summary["derived_batch_units"], len(scope.covered_unit_ids))
            self.assertEqual(summary["derived_max_batch_units"],
                             RX.report_review_structural_bound(scope)["max_batch_units"])
            self.assertNotIn("derivation_error", summary)
        bounds = {s["scope_kind"]: RX.report_review_structural_bound(
            RR.ReportReviewScope.from_dict(
                next(i for i in result["report_review_inputs"]
                     if i["scope_kind"] == s["scope_kind"])))
            for s in result["report_review_scopes"]}
        policy = RX.report_review_call_budget_policy(
            bounds=bounds, model="deepseek-v4-pro", approved=True,
            provenance="test: 页面数字与预算一致")
        cap = policy.axis_cap_for(RX.REPORT_REVIEW_BUDGET_AXIS)
        for kind, bound in sorted(bounds.items()):
            self.assertEqual(
                cap.limit_for_scope(RX.REPORT_REVIEW_SCOPE_PREFIX + kind),
                bound["batches"])
        self.assertEqual(cap.max_attempts, sum(b["batches"] for b in bounds.values()))

        # 意见**不移动**任何一格：五个显式布尔量，不从散文里推断。
        self.assertEqual(states["review_opinion_effect"], {
            "moves_mechanical": False, "moves_preview": False,
            "moves_system_release": False, "moves_human_acceptance": False,
            "moves_formal_phase_closure": False,
            "note": states["review_opinion_effect"]["note"]})
        for section in result["section_assessments"]:
            self.assertEqual(section["assurance_result"]["system_review_state"],
                             "system_review_not_passed")

    def _stale_run(self, scope, *, report_id: str, scope_kind: str | None = None):
        """造一份**合法**的 `rrq-2` 运行：绑在另一个版本（或另一份报告）上。

        它不是"伪造的残骸"：除被点名改写的那一格（版本 / 报告身份）外，批次账、单元账、
        记录与指纹都是一份真跑得出来的运行该有的样子——否则测的就是构造失败。
        """
        from assurance import report_reviewer as RX

        version = "rrv_" + "0" * 24
        batch = "rrb_" + "0" * 24
        model_policy = "offline-stale-test"
        return RX.ReportReviewRun.create(
            report_id=report_id, scope_kind=scope_kind or scope.scope_kind,
            scope_id="rrs_" + "0" * 24, scope_version=version,
            bundle_id=scope.bundle.bundle_id, model_policy_id=model_policy,
            producer_kind="independent_llm_review", outcome="reviewed",
            requested_batch_ids=(batch,), completed_batch_ids=(batch,),
            declared_unit_ids=scope.covered_unit_ids,
            requested_unit_ids=scope.covered_unit_ids,
            covered_unit_ids=scope.covered_unit_ids, reported_unit_ids=(),
            excluded_unit_ids=(),
            records=(AS.ReviewerRunRecord.create(
                report_version=version, bundle_id=scope.bundle.bundle_id,
                prompt_version=RX.REPORT_REVIEW_PROMPT_VERSION,
                model_policy_id=model_policy, outcome="reviewed",
                covered_unit_keys=scope.covered_unit_ids,
                response_fingerprint="0" * 64),),
            issues=())

    def test_stale_report_review_run_is_shown_not_aggregated(self) -> None:
        """改过正文之后的旧读数：标为陈旧、意见不聚合，**整份读数照样出得来**。"""
        from assurance import report_review as RR

        result = assess_persisted_run(SOURCE)
        scope = RR.ReportReviewScope.from_dict(
            next(s for s in result["report_review_inputs"]
                 if s["scope_kind"] == "financial_a2"))
        stale = self._stale_run(scope, report_id=SOURCE.name)
        moved = CC.assess_persisted_run(SOURCE, report_review_runs=(stale,))
        stale_rows = moved["report_states"]["stale_report_review_runs"]
        self.assertEqual(len(stale_rows), 1)
        row = stale_rows[0]
        self.assertEqual(row["scope_kind"], "financial_a2")
        self.assertEqual(row["current_scope_version"], scope.scope_version)
        self.assertEqual(set(row["moved_identity_layers"]),
                         {"内容身份 scope_id", "内容版本"})
        self.assertEqual(moved["report_review_runs"], [])
        self.assertEqual(moved["report_states"]["review_coverage"]["financial_a2"],
                         "input_prepared_review_not_run")
        # 陈旧读数**不动**其它任何一格。
        self.assertEqual(moved["section_assessments"], result["section_assessments"])
        self.assertEqual(moved["report_states"]["mechanical"],
                         result["report_states"]["mechanical"])
        self.assertEqual(moved["new_llm_calls"], 0)

    def test_a_run_from_another_report_is_rejected_not_labelled_stale(self) -> None:
        """**反例**：不同报告的同类 scope 不是「本报告的旧版」，是**别处**的读数 ⇒ 拒。

        这一条与「版本失效」是两条不同的轴：陈旧说的是「同一份报告的另一版内容」，把
        「另一份报告」也标成陈旧，会让读者以为这份内容曾经被审过——而它从来没被审过。
        """
        from assurance import report_review as RR

        result = assess_persisted_run(SOURCE)
        scope = RR.ReportReviewScope.from_dict(
            next(s for s in result["report_review_inputs"]
                 if s["scope_kind"] == "financial_a2"))
        # 除报告身份外**逐字段相同**：版本、scope_id、bundle、记录全对得上
        foreign = self._stale_run(scope, report_id="some_other_report",
                                  scope_kind="financial_a2")
        same = self._stale_run(scope, report_id=SOURCE.name, scope_kind="financial_a2")
        self.assertNotEqual(foreign.report_id, same.report_id)
        self.assertEqual(foreign.requested_batch_ids, same.requested_batch_ids)
        self.assertEqual(foreign.covered_unit_ids, same.covered_unit_ids)
        with self.assertRaisesRegex(CitedControllerError, "另一份报告"):
            CC.assess_persisted_run(SOURCE, report_review_runs=(foreign,))

        # 不属于本报告的 **scope 种类**同样拒（两种"别处"都必须 fail-closed）
        with patch.object(CC.RR, "REPORT_REVIEW_SCOPE_KINDS",
                          (*CC.RR.REPORT_REVIEW_SCOPE_KINDS, "zzz")):
            unknown = self._stale_run(scope, report_id=SOURCE.name, scope_kind="zzz")
            with self.assertRaisesRegex(CitedControllerError, "不存在的 scope"):
                CC.assess_persisted_run(SOURCE, report_review_runs=(unknown,))

    def test_create_only_output_and_source_preview_preserved(self) -> None:
        before = (SOURCE / "company/cited_preview.md").read_bytes()
        result = assess_persisted_run(SOURCE)
        out = ROOT / "evaluation/results/m930_4_test_not_created"
        with patch("scripts.run_m930_4_assurance.assess_persisted_run", return_value=result), \
             patch.object(Path, "exists", return_value=False), \
             patch.object(Path, "mkdir") as mkdir, \
             patch.object(Path, "open", mock_open()) as opened:
            self.assertEqual(run_assurance_main(["--source-run", str(SOURCE), "--output", str(out)]), 0)
            mkdir.assert_called_once_with(parents=False, exist_ok=False)
            self.assertEqual(opened.call_count, 3)
        with patch.object(Path, "exists", return_value=True), self.assertRaises(SystemExit):
            run_assurance_main(["--source-run", str(SOURCE), "--output", str(out)])
        self.assertEqual(before, (SOURCE / "company/cited_preview.md").read_bytes())


def _summary(result) -> dict:
    """运行器要的是 `passed`/`failed`/`skipped`/`details` 四个键，**不是退出码**。

    返回 `int` 会让 `run_evals` 在汇总那一步抛 `AttributeError: 'int' object has no
    attribute 'get'`，而那一行不在它的 `except` 里——套件会静默截断、`TOTAL` 不打印，
    日志看起来仍像「基本跑完」。所以退出码只在 `__main__` 守卫里从这四个键推出来。
    """
    return {
        "passed": result.testsRun - len(result.failures) - len(result.errors)
        - len(result.skipped),
        "failed": len(result.failures) + len(result.errors),
        "skipped": len(result.skipped),
        "details": ["FAIL " + str(test) + chr(10) + str(tb)
                    for test, tb in result.failures + result.errors],
    }


def main() -> dict:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CitedControllerTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return _summary(result)


if __name__ == "__main__":
    raise SystemExit(0 if main()["failed"] == 0 else 1)

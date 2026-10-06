"""Focused M930-5 read-only persisted-run demo checks.

Run: ``python -X utf8 -m evals.test_m930_5_cited_demo_ui``.
The fixture is the already-persisted real dual-section run; no LLM, network,
database connection, report generation, or human-confirmation write is allowed.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from sections import cited_demo_loader as loader_mod
from sections import cited_report as CRP
from sections.cited_demo_loader import (
    CitedDemoLoadError,
    _DISPLAY_IDENTITY_FIELDS,
    _Snapshot,
    _canonical_sha,
    default_cited_run,
    list_cited_runs,
    load_cited_run,
)


REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "evaluation" / "results"
RUN_ID = "m930_3_cited_real_dual_v2_r1"
#: 上一版策略号。用它做「陈旧读数」的样本，是为了让「当前版」与「旧版」在测试里**不是**
#: 同一个字符串——版本号必须只代表一套语义，否则"标为陈旧"这条路径无从证伪。
STALE_POLICY_VERSION = "m4cc-3"


class PersistedCitedDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not (RESULTS / RUN_ID).is_dir():
            raise unittest.SkipTest("已存真实双节演示 run 不在本机；不伪造真实产物")

    def test_default_ignores_old_incompatible_wire(self) -> None:
        self.assertEqual(default_cited_run(RESULTS, (RUN_ID,)), RUN_ID)
        old = "m930_3_cited_real_r31_dual"
        if (RESULTS / old).is_dir():
            self.assertEqual(default_cited_run(RESULTS, (RUN_ID, old)), RUN_ID)

    @staticmethod
    def _altered_read(relative_name: str, change):
        original = _Snapshot.read

        def altered(snapshot: _Snapshot, relative: str) -> bytes:
            data = original(snapshot, relative)
            return change(data) if relative == relative_name else data

        return patch.object(_Snapshot, "read", altered)

    @staticmethod
    def _changed_json(data: bytes, key: str, value: str) -> bytes:
        body = json.loads(data.decode("utf-8"))
        body[key] = value
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    def test_real_run_has_explicit_unpublishable_axes(self) -> None:
        run = load_cited_run(RUN_ID, results_root=RESULTS)
        self.assertEqual(run.ledger["mode"], "real")
        self.assertEqual((len(run.sections["company"].hard_sentence_ids),
                          run.sections["company"].report_version["sentence_count"]), (7, 16))
        self.assertEqual((len(run.sections["financial"].hard_sentence_ids),
                          run.sections["financial"].report_version["sentence_count"]), (0, 21))
        self.assertEqual(len(run.sections["financial"].draft["gaps"]), 3)
        self.assertEqual(len(run.sections["financial"].metric_tables["tables"]), 4)
        self.assertEqual(run.sections["financial"].balance_structure["producer_kind"],
                         "deterministic_presentation")
        self.assertEqual(run.sections["financial"].balance_structure["model_calls_issued"], 0)
        self.assertEqual(len(run.source_regions), 12)
        self.assertEqual(sum(r["confirmation_state"] == "human_confirmed"
                             for r in run.source_regions), 0)
        self.assertTrue(all(s.report_version["publishability"] == "not_publishable"
                            for s in run.sections.values()))
        self.assertTrue(all({issue["category"] for issue in s.review["issues"]}
                            == {"supported"} for s in run.sections.values()))
        self.assertEqual(len(run.file_hashes), 45)
        self.assertIn(RUN_ID, list_cited_runs(RESULTS))

    def test_legacy_period_run_is_still_readable(self) -> None:
        """盘上的 run 声明的是**它自己**那一期政策时，页面照旧打得开。

        这条挡的是一个具体的回归：`cited_report_version.json` 一旦被 `from_dict`（要求版本串
        == 当前常量）读，任何一次 `crpp-*` 升版都会让**每一个**已落盘 run 当场打不开——把
        「旧产物读得出」变成「旧产物一律打不开」。读盘必须走 `from_persisted_dict`（按文件
        自己声明的发布期分派，跨轴不变式与 `record_id` 复算一条不少）。
        """
        run = load_cited_run(RUN_ID, results_root=RESULTS)
        declared = {s.report_version["policy_version"] for s in run.sections.values()}
        self.assertTrue(
            declared <= {CRP.CITED_REPORT_POLICY_VERSION}
            | {pv for _sv, pv in CRP.CITED_REPORT_LEGACY_WIRE_VERSIONS},
            f"本节 run 声明的发布期不在「当前 + 登记历史」里：{declared}")
        self.assertTrue(
            declared & {pv for _sv, pv in CRP.CITED_REPORT_LEGACY_WIRE_VERSIONS},
            f"这条钉子要的是**历史发布期**的 run；{RUN_ID} 声明 {declared}，"
            f"当前常量是 {CRP.CITED_REPORT_POLICY_VERSION}——换成一条历史 run 才钉得住")

    def test_run_id_traversal_and_unknown_run_are_rejected(self) -> None:
        for run_id in ("../outside", "company/../outside", "..", "C:escape"):
            with self.subTest(run_id=run_id), self.assertRaises(CitedDemoLoadError):
                load_cited_run(run_id, results_root=RESULTS)
        with self.assertRaises(CitedDemoLoadError):
            load_cited_run("not_a_persisted_run", results_root=RESULTS)

    def test_missing_artifact_is_rejected(self) -> None:
        def missing(_data: bytes) -> bytes:
            raise CitedDemoLoadError("artifact missing")

        with self._altered_read("financial/review_issues.json", missing):
            with self.assertRaises(CitedDemoLoadError):
                load_cited_run(RUN_ID, results_root=RESULTS)

    def test_tampered_report_identity_is_rejected(self) -> None:
        with self._altered_read(
                "company/cited_report_version.json",
                lambda data: self._changed_json(data, "report_version", "crpv_forged")):
            with self.assertRaises(CitedDemoLoadError):
                load_cited_run(RUN_ID, results_root=RESULTS)

    def test_cross_file_report_version_mismatch_is_rejected(self) -> None:
        with self._altered_read(
                "financial/source_table_display.json",
                lambda data: self._changed_json(
                    data, "paired_report_version", "crpv_wrong_section")):
            with self.assertRaises(CitedDemoLoadError):
                load_cited_run(RUN_ID, results_root=RESULTS)

    def test_modified_pdf_render_is_rejected(self) -> None:
        display = json.loads((RESULTS / RUN_ID / "company" / "source_table_display.json")
                             .read_text(encoding="utf-8"))
        rel = display["regions"][0]["render_relpath"]
        with self._altered_read(f"company/source_display/{rel}",
                                lambda data: data + b"tampered"):
            with self.assertRaises(CitedDemoLoadError):
                load_cited_run(RUN_ID, results_root=RESULTS)

    def test_cross_section_confirmation_divergence_cannot_be_deduplicated(self) -> None:
        def changed_financial_display(data: bytes) -> bytes:
            body = json.loads(data.decode("utf-8"))
            first = body["regions"][0]
            first.update({"confirmation_state": "human_confirmed",
                          "confirmer": "human-test",
                          "confirmed_at": "2026-10-03T00:00:00Z",
                          "confirmation_conclusion": "完整、清晰、忠实"})
            body["human_confirmed_count"] = 1
            fingerprint = _canonical_sha({key: body[key]
                                          for key in _DISPLAY_IDENTITY_FIELDS})
            body["fingerprint"] = fingerprint
            body["display_set_id"] = "std_" + fingerprint[:24]
            return json.dumps(body, ensure_ascii=False).encode("utf-8")

        with self._altered_read("financial/source_table_display.json",
                                changed_financial_display):
            with self.assertRaisesRegex(CitedDemoLoadError, "确认状态不同"):
                load_cited_run(RUN_ID, results_root=RESULTS)

    def test_snapshot_detects_file_change_during_load(self) -> None:
        snapshot = _Snapshot(RESULTS / RUN_ID)
        snapshot.read("cited_call_ledger.json")
        snapshot.content["cited_call_ledger.json"] += b"changed"
        with self.assertRaises(CitedDemoLoadError):
            snapshot.verify_unchanged()

    def test_loader_does_not_connect_or_write(self) -> None:
        paths = tuple(p for p in (RESULTS / RUN_ID).rglob("*") if p.is_file())
        before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in paths}
        with patch.object(socket.socket, "connect", side_effect=AssertionError("network")), \
             patch.object(sqlite3, "connect", side_effect=AssertionError("database")):
            run = load_cited_run(RUN_ID, results_root=RESULTS)
        self.assertEqual(len(run.source_regions), 12)
        self.assertEqual(before, {p: (p.stat().st_size, p.stat().st_mtime_ns)
                                  for p in paths})

    def test_page_reads_the_persisted_sidecar_and_never_recomputes(self) -> None:
        """M930-5 显示的必须是**已持久化**的 M930-4 读数，且带着它的来源身份。"""
        import scripts.cited_demo_app as app
        from assurance import cited_controller as CC
        from sections import cited_demo_loader as loader

        self.assertNotEqual(STALE_POLICY_VERSION, CC.POLICY_VERSION)
        run = load_cited_run(RUN_ID, results_root=RESULTS)
        reading = {"policy_version": STALE_POLICY_VERSION,
                   "system_release_eligible": False,
                   "source_run": str(run.run_dir)}
        stub = loader.AssuranceSidecar(
            reading=reading, directory=RESULTS, label="_stub/assurance_diagnostic.json",
            sha256="ab" * 32, source_run=str(run.run_dir),
            superseded=("a/assurance_diagnostic.json", "b/assurance_diagnostic.json"))
        with patch.object(loader, "load_assurance_sidecar", return_value=stub) as called:
            got, note = app._load_assurance(run)
        self.assertEqual(called.call_count, 1)
        self.assertIs(got, reading)
        self.assertIn("_stub/assurance_diagnostic.json", note)
        self.assertIn(stub.sha256[:16], note)
        self.assertIn("2 份更早策略的读数已被取代", note)
        # 页面**原样**显示侧车声明的版本号：既不替它升到当前版，也不把它说成通过。
        self.assertEqual(got["policy_version"], STALE_POLICY_VERSION)

        # 读不到时如实说明，而不是现场算一份出来充数。
        with patch.object(loader, "load_assurance_sidecar",
                          side_effect=CitedDemoLoadError("未加载到已持久化的 M930-4 读数")):
            declined, reason = app._load_assurance(run)
        self.assertIsNone(declined)
        self.assertIn("未加载到", reason)
        self.assertFalse(app._review_attested(declined, "company"))

        # 结构性证明：本页没有「现场重算」这条路，因此不可能声称读过侧车而其实没读。
        source = (REPO / "scripts" / "cited_demo_app.py").read_text(encoding="utf-8")
        self.assertNotIn("assess_persisted_run", source)

        # 端到端：本机**真的有**一份当作正式交付的 create-only 读数，页面读回的就是它。
        real, note = app._load_assurance(run)
        self.assertIsNotNone(real)
        self.assertEqual(real["policy_version"], CC.POLICY_VERSION)
        self.assertNotEqual(real["policy_version"], STALE_POLICY_VERSION)
        self.assertEqual(real["source_run"], str(run.run_dir))
        self.assertIs(real["system_release_eligible"], False)
        self.assertIn("读取自", note)
        self.assertEqual(real["report_review_calls_recorded"], 0)
        for kind in sorted(CC.RR.REPORT_REVIEW_SCOPE_KINDS):
            self.assertEqual(
                real["report_states"]["review_coverage"][kind],
                "input_prepared_review_not_run")

        # **预算与页面数字一致**：页面那列「调用上限（推出）」直接取读数里的
        # `derived_batch_count`，而它必须等于冻结分批规则在这份输入上真的切一遍的批数。
        # 输入面（`report_review_inputs.json`）与读数同目录、由同一次运行写出；页面**不**读它，
        # 本处读它是为了**验算**页面那个数字，而不是为了替页面重新算一遍。
        from assurance import report_review as RR
        from assurance import report_reviewer as RX
        sidecar = loader.load_assurance_sidecar(run, results_root=RESULTS)
        raw_inputs = json.loads(
            (sidecar.directory / "report_review_inputs.json").read_text(encoding="utf-8"))
        inputs = {item["scope_kind"]: item
                  for item in raw_inputs["report_review_inputs"]}
        self.assertEqual(set(inputs), set(CC.RR.REPORT_REVIEW_SCOPE_KINDS))
        caps: dict[str, int] = {}
        for summary in real["report_review_scopes"]:
            scope = RR.ReportReviewScope.from_dict(inputs[summary["scope_kind"]])
            batches = RX.plan_report_review_batches(scope)
            self.assertEqual(summary["derived_batch_count"], len(batches))
            self.assertEqual(summary["derived_batch_units"], len(scope.covered_unit_ids))
            self.assertNotIn("derivation_error", summary)
            caps[summary["scope_kind"]] = summary["derived_batch_count"]
            opinion = real["report_states"]["review_opinions"][summary["scope_kind"]]
            # 没跑过就是**零**，不是把"输入里有多少单元"显示成"覆盖了多少"。
            for key in ("requested_unit_count", "covered_unit_count",
                        "reported_unit_count", "requested_batch_count",
                        "completed_batch_count", "failed_batch_count", "llm_call_count"):
                self.assertEqual(opinion[key], 0)
            self.assertFalse(opinion["trusted_as_assurance_input"])
        # 这三个数就是运行入口在授权之前必须冻结的逐 scope 上限（合计 = 整轴上限）。
        bounds = {kind: RX.report_review_structural_bound(
            RR.ReportReviewScope.from_dict(inputs[kind])) for kind in sorted(caps)}
        policy = RX.report_review_call_budget_policy(
            bounds=bounds, model="deepseek-v4-pro", approved=True,
            provenance="test: 页面数字与预算政策一致")
        cap = policy.axis_cap_for(RX.REPORT_REVIEW_BUDGET_AXIS)
        for kind in sorted(caps):
            self.assertEqual(
                cap.limit_for_scope(RX.REPORT_REVIEW_SCOPE_PREFIX + kind), caps[kind])
        self.assertEqual(cap.max_attempts, sum(caps.values()))
        # 冻结点：这份输入切出来就是 1 / 1 / 6 批。它变了不是"测试过时"，是分批规则或
        # 输入面动了——那时**必须**重新申请授权，否则已批准的上限盖不住新的上界。
        self.assertEqual(caps, {"financial_a2": 1, "cross_section": 1,
                                "material_selectivity": 6})

    def test_sidecar_lookup_is_deterministic_and_fails_closed(self) -> None:
        """多份读数时按**产物自身**排定先后；排不出来就拒，不靠查找顺序挑一份。"""
        from assurance import cited_controller as CC
        from sections.cited_demo_loader import AssuranceSidecar, load_assurance_sidecar

        run = load_cited_run(RUN_ID, results_root=RESULTS)
        scratch = Path(tempfile.mkdtemp(prefix="_tmp_m930_5_sidecar_", dir=REPO))
        try:
            def write(name: str, **overrides) -> None:
                body = {"source_run": str(run.run_dir), "new_llm_calls": 0,
                        "system_release_eligible": False, "section_assessments": []}
                body.update(overrides)
                directory = scratch / name
                directory.mkdir()
                (directory / "assurance_diagnostic.json").write_text(
                    json.dumps(body, ensure_ascii=False), encoding="utf-8")

            # (1) 一份都没有 ⇒ 拒（页面显示「未加载到」，不显示「通过」）。
            with self.assertRaisesRegex(CitedDemoLoadError, "未加载到"):
                load_assurance_sidecar(run, results_root=scratch)

            # (2) 多份**都声明当前策略版本** ⇒ 拒：显示哪一份不能由查找顺序决定。
            write("older", policy_version=STALE_POLICY_VERSION)
            write("current_a", policy_version=CC.POLICY_VERSION)
            write("current_b", policy_version=CC.POLICY_VERSION)
            with self.assertRaisesRegex(CitedDemoLoadError, "两份当前读数"):
                load_assurance_sidecar(run, results_root=scratch)

            # (3) 没有任何一份声明当前版本 ⇒ 拒并列出候选，要求显式指定。
            (scratch / "current_b" / "assurance_diagnostic.json").unlink()
            (scratch / "current_a" / "assurance_diagnostic.json").write_text(
                json.dumps({"source_run": str(run.run_dir),
                            "policy_version": STALE_POLICY_VERSION}, ensure_ascii=False),
                encoding="utf-8")
            with self.assertRaisesRegex(CitedDemoLoadError, "没有一份声明当前策略版本"):
                load_assurance_sidecar(run, results_root=scratch)

            # (4) 恰一份当前版本 ⇒ **被选中**并进入逐条复验；伪造的内容照样被拒
            #     （证明选中之后没有跳过对账）。
            (scratch / "current_a" / "assurance_diagnostic.json").write_text(
                json.dumps({"source_run": str(run.run_dir),
                            "policy_version": CC.POLICY_VERSION,
                            "source_call_ledger_sha256": "0" * 64,
                            "new_llm_calls": 0,
                            "system_release_eligible": False,
                            "section_assessments": []}, ensure_ascii=False),
                encoding="utf-8")
            with self.assertRaisesRegex(CitedDemoLoadError, "调用账本哈希"):
                load_assurance_sidecar(run, results_root=scratch)

            # 「标为陈旧」只适用于策略号不同的读数；显式指定仍然可用（本次给的是目录），
            # 并且**照样**逐条复验——陈旧不等于免检。
            (scratch / "older" / "assurance_diagnostic.json").write_text(
                json.dumps({"source_run": str(run.run_dir),
                            "policy_version": STALE_POLICY_VERSION,
                            "source_call_ledger_sha256": "0" * 64,
                            "new_llm_calls": 0,
                            "system_release_eligible": False,
                            "section_assessments": []}, ensure_ascii=False),
                encoding="utf-8")
            with self.assertRaisesRegex(CitedDemoLoadError, "调用账本哈希"):
                load_assurance_sidecar(run, results_root=scratch,
                                       explicit_dir=scratch / "older")
            self.assertTrue(issubclass(CitedDemoLoadError, Exception))
            self.assertEqual(
                AssuranceSidecar(reading={"policy_version": STALE_POLICY_VERSION},
                                 directory=scratch, label="x", sha256="0" * 64,
                                 source_run=str(run.run_dir)).policy_version,
                STALE_POLICY_VERSION)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        self.assertFalse(scratch.exists())


class ReportReviewSidecarTests(unittest.TestCase):
    """真实报告级审阅结果接进演示页：**新产物不得破坏既有查找，也不得被漏检**。

    本类只读两样东西：一份当作运行前快照的读数（`m930_4_cited_v2_r4_reportreview`，
    自动查找当前会选中它）与**当前**那份（`postreview/m930_4_cited_v2_r6_reportreview`，
    本批口径修正后的读数；r5 是修正前的原始读数，只读保留、不作为交付对账基准）。
    它证明的三件事都是**查找行为**：显式选择能读到新产物；不设环境变量时不会因为
    新增产物冒出「两份当前读数」；身份不匹配仍旧拒绝。
    """

    R6_DIR = RESULTS / "postreview" / "m930_4_cited_v2_r6_reportreview"

    @classmethod
    def setUpClass(cls) -> None:
        if not (RESULTS / RUN_ID).is_dir():
            raise unittest.SkipTest("已存真实双节演示 run 不在本机；不伪造真实产物")
        if not (cls.R6_DIR / loader_mod.ASSURANCE_SIDECAR_NAME).is_file():
            raise unittest.SkipTest("本批的 create-only 读数不在本机；不伪造读数")

    @staticmethod
    def _run():
        return load_cited_run(RUN_ID, results_root=RESULTS)

    def test_explicit_selection_reaches_the_new_sidecar(self) -> None:
        run = self._run()
        picked = loader_mod.load_assurance_sidecar(
            run, results_root=RESULTS, explicit_dir=self.R6_DIR)
        self.assertEqual(picked.directory.resolve(), self.R6_DIR.resolve())
        self.assertIn("postreview", picked.label.replace("\\", "/"))
        # 新读数**不是**「零次调用」：它绑着已经真实发生过的调用记录。
        self.assertEqual(picked.reading["report_review_calls_recorded"], 6)
        # 显式指定**不等于**免检：来源身份仍被逐条复验过才拿到对象。
        self.assertEqual(picked.reading["source_run"], str(run.run_dir))

        # 环境变量是与 `explicit_dir` 并列的受支持入口，行为必须一致。
        via_env = loader_mod.load_assurance_sidecar(
            run, results_root=RESULTS,
            environ={loader_mod.ASSURANCE_DIR_ENV: str(self.R6_DIR)})
        self.assertEqual(via_env.reading, picked.reading)
        self.assertEqual(via_env.sha256, picked.sha256)

    def test_new_sidecar_does_not_create_a_second_current_reading(self) -> None:
        """新增产物的**位置**本身就是设计：自动查找看不见它，因此不会出现「两份当前读数」。

        自动查找扫的是 `results_root/*/assurance_diagnostic.json`（**一层**）。新产物放在
        `results_root/postreview/<dir>/`，深度为二，所以它既不进候选、也不需要竞争。
        `environ={}` 强制走纯扫描路径，免得本机真的设了那个环境变量时这条测试变成假绿。
        """
        run = self._run()
        scanned = [p for p, _ in loader_mod._scan_sidecars(RESULTS, run)]
        self.assertTrue(scanned, "纯扫描路径应当至少找到当作正式交付的那份读数")
        self.assertNotIn((self.R6_DIR / loader_mod.ASSURANCE_SIDECAR_NAME).resolve(),
                         {p.resolve() for p in scanned})

        # 纯扫描不抛错 ⇒ 当前版本恰一份 ⇒ 页面不会显示「两份当前读数」。
        auto = loader_mod.load_assurance_sidecar(run, results_root=RESULTS, environ={})
        self.assertNotIn(self.R6_DIR.resolve(), {Path(x).resolve()
                                                 for x in auto.superseded})
        from assurance import cited_controller as CC
        self.assertEqual(auto.policy_version, CC.POLICY_VERSION)
        # 选中的仍是**那一份**：新增产物没有把自动查找挤到它身上。
        self.assertEqual(auto.reading["report_review_calls_recorded"], 0)

    def test_identity_mismatch_still_rejected_for_the_new_sidecar(self) -> None:
        """换源 run、换账本哈希、换正文版本——三条身份轴逐条仍拒，且拒的是新产物那份。"""
        run = self._run()
        body = json.loads(
            (self.R6_DIR / loader_mod.ASSURANCE_SIDECAR_NAME).read_text(encoding="utf-8"))
        cases = {
            "来源 run": lambda b: b.update({"source_run": str(RESULTS / "elsewhere")}),
            "调用账本哈希": lambda b: b.update({"source_call_ledger_sha256": "0" * 64}),
            "自负调用数": lambda b: b.update({"new_llm_calls": 1}),
        }
        for label, mutate in cases.items():
            with self.subTest(axis=label):
                changed = json.loads(json.dumps(body))
                mutate(changed)
                self._write_and_expect_reject(changed, label)
        # 正文版本轴：逐节对齐，改一个字就是另一份内容。
        changed = json.loads(json.dumps(body))
        changed["section_assessments"][0]["report_version"] = "crpv_forged"
        self._write_and_expect_reject(changed, "正文版本")

    def _write_and_expect_reject(self, changed: dict, label: str) -> None:
        scratch = Path(tempfile.mkdtemp(prefix="_tmp_m930_5_r5_", dir=REPO))
        try:
            directory = scratch / "candidate"
            directory.mkdir()
            (directory / loader_mod.ASSURANCE_SIDECAR_NAME).write_text(
                json.dumps(changed, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(CitedDemoLoadError):
                loader_mod.load_assurance_sidecar(self._run(), results_root=RESULTS,
                                                  explicit_dir=directory)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def test_page_shows_numbers_and_issues_of_a_failed_scope(self) -> None:
        """整体判 `failed` 的 scope，页面必须**同时**显示它的失败、它的数和它的意见。

        以前页面按覆盖状态是否以 `reviewed_by_` 开头去门控，于是「审阅已开始但未跑完」
        这一格被读成「未运行」、已经真提过的 7 条意见被显示成「无」。
        """
        import scripts.cited_demo_app as app

        run = self._run()
        with patch.dict(os.environ, {loader_mod.ASSURANCE_DIR_ENV: str(self.R6_DIR)}):
            assurance, note = app._load_assurance(run)
        self.assertIsNotNone(assurance)
        self.assertIn("读取自", note)

        rows = {row["内容块"]: row for row in app._scope_review_rows(assurance)}
        self.assertEqual(len(rows), 3)
        sel = rows["未用材料的选择性遗漏"]
        self.assertIn("未跑完", sel["审阅覆盖"])
        self.assertNotIn("未运行", sel["审阅覆盖"])
        self.assertEqual((sel["声明单元"], sel["已发起批次"], sel["已落批记录"],
                          sel["失败批次"]), (150, 5, 4, 1))
        self.assertEqual((sel["放进请求"], sel["收到可解析回复"], sel["模型提出问题"],
                          sel["未覆盖单元"]), (125, 100, 7, 50))
        self.assertEqual(sel["意见"], "7 条")
        self.assertEqual(sel["可作审阅输入"], "否")
        # 跑完的两块**不**该被这块的失败连坐。
        self.assertEqual(rows["财务 A2 资产负债结构"]["意见"], "0 条")
        self.assertEqual(rows["两节合读"]["已发起批次"], 1)

        # 两本账分开写：预算门尝试 = 各块已发起批次数之和；已落批记录 = 产物里的记录数。
        self.assertEqual(sum(r["已发起批次"] for r in rows.values()), 7)
        self.assertEqual(assurance["report_review_calls_recorded"], 6)

        issues = app._report_review_issue_rows(assurance)
        self.assertEqual(len(issues), 7)
        # 这 7 条**全部**指向进过请求的 `citation:*` 单元。围栏里那 6 条拟议意见指向
        # `aspect_id:*` / `prose_sentence:*`，它们所在的那一批回复根本没被接受，
        # 因此**一条都不该**出现在这里。
        self.assertTrue(all(r["指向单元"].startswith("citation:") for r in issues))
        self.assertFalse(any(r["指向单元"].startswith(("aspect_id:", "prose_sentence:"))
                             for r in issues))
        highs = [r for r in issues if r["严重度"] == "high"]
        self.assertEqual(len(highs), 3)
        self.assertEqual({r["建议补到"] for r in highs},
                         {"fin_solvency.net_asset_level"})
        self.assertEqual(sum(1 for r in issues if r["阻断放行"] == "是"), 3)

        # 未获得可解析审阅的成员要**点名**，而且必须按来源**分成两拨**：
        # 真的进过失败批的（回复不可用）与压根没发出去的。合并成一句「都从未发出」
        # 会让读者以为 50 个全都没离开本机——其中 25 个是真的发出去了。
        unreviewed = app._unreviewed_units(assurance)
        self.assertEqual([kind for kind, _, _ in unreviewed], ["material_selectivity"])
        _, entered, never = unreviewed[0]
        self.assertEqual(len(entered), 25)
        self.assertEqual(entered[0], "citation:m16")
        self.assertEqual(entered[-1], "citation:m40")
        self.assertEqual(len(never), 25)
        self.assertEqual(never[0], "citation:m41")
        self.assertEqual(never[-1], "citation:m65")
        self.assertEqual(set(entered) & set(never), set())
        # 审阅意见不移动其余六格——这在产物里是显式布尔量。
        effect = assurance["report_states"]["review_opinion_effect"]
        for key in ("moves_mechanical", "moves_preview", "moves_system_release",
                    "moves_human_acceptance", "moves_formal_phase_closure"):
            self.assertIs(effect[key], False)

    def test_status_renders_without_a_sidecar(self) -> None:
        """侧车读不到时状态块必须还能画出来。

        这一分支曾经引用一个不存在的名字（`assurance_error`），于是页面恰恰在
        「读不到读数」这条最需要说清楚的路径上抛 `NameError`。这里用**没有 Streamlit 运行时**
        的方式走一遍：把 `st` 换成只记调用的替身，读侧车失败，看它是否仍然走完全程。
        """
        import scripts.cited_demo_app as app

        calls: list[tuple[str, str]] = []

        class _Recorder:
            def __getattr__(self, name: str):
                def record(*args, **kwargs):
                    text = " ".join(str(a) for a in args)
                    calls.append((name, text))
                    return None
                return record

        run = self._run()
        with patch.object(app, "st", _Recorder()):
            app._render_status(run, None, "未加载到已持久化的 M930-4 读数")
        by_name = {name: [text for n, text in calls if n == name]
                   for name in {n for n, _ in calls}}
        # 第一条就是不可发布横幅：读不到读数**不改变**这一格。
        self.assertIn("不可发布", by_name["error"][0])
        # 而且要把「读不到」的后果说出来，不能只留一句「未加载到」让人自行推断。
        self.assertTrue(any("未加载到" in text and "不得视为系统放行" in text
                            for text in by_name["warning"]))
        # 报告级那两张表**不该**在这种情况下出现：没有读数就没有可显示的逐块数字，
        # 现场凑一张空表出来会被读成「跑了、没问题」。
        self.assertFalse(any("逐块读数" in text for text in by_name.get("markdown", [])))
        self.assertFalse(any("报告级审阅意见" in text for text in by_name.get("markdown", [])))

    def test_failed_scope_red_bars_split_and_two_a2_axes_are_labelled(self) -> None:
        """有读数时红条必须**分两拨**，且节级与报告级的两个「A2」要标清范围。

        - 红条以前把 50 个未获得可解析审阅的成员写成「从未进入任何请求」，但其中 25 个
          （`m16`–`m40`）**真的发出去过**，只是那一批回复不可用。两拨的处置不同。
        - 节级表的 `A2 审阅（逐句轴）= review_not_run` 与报告级的 `financial_a2 已审阅`
          是**两条轴**，必须有一句 caption 说明，免得读者以为同一状态自相矛盾。
        """
        import scripts.cited_demo_app as app

        calls: list[tuple[str, tuple]] = []

        class _Recorder:
            def __getattr__(self, name: str):
                def record(*args, **kwargs):
                    calls.append((name, args))
                    return None
                return record

        run = self._run()
        with patch.dict(os.environ, {loader_mod.ASSURANCE_DIR_ENV: str(self.R6_DIR)}):
            assurance, note = app._load_assurance(run)
        self.assertIsNotNone(assurance)
        with patch.object(app, "st", _Recorder()):
            app._render_status(run, assurance, note)

        by_name: dict[str, list[str]] = {}
        for name, args in calls:
            by_name.setdefault(name, []).append(" ".join(str(a) for a in args))
        errors = by_name.get("error", [])
        # 两拨各自成条，且都点了名。
        self.assertTrue(any("25 个真的进了请求" in t and "citation:m16" in t
                            and "citation:m40" in t for t in errors))
        self.assertTrue(any("25 个从未发进请求" in t and "citation:m41" in t
                            and "citation:m65" in t for t in errors))
        self.assertTrue(any("未取得可采信的审阅结果" in t and "调用与失败均已留账" in t
                            for t in errors))
        self.assertFalse(any("这一批的内容没有人读到过" in t for t in errors))
        # 合并成一句「50 个都从未发出」的旧说法必须消失。
        self.assertFalse(any("50 个成员从未进入任何请求" in t for t in errors))

        # 节级表把 A2 这一列标成「逐句轴」，不让它与报告级的「财务 A2」撞名。
        self.assertTrue(any("A2 审阅（**逐句**轴）" in t
                            for t in by_name.get("dataframe", [])))
        captions = by_name.get("caption", [])
        self.assertTrue(any("两条轴各自成立" in t for t in captions))
        self.assertTrue(any("不说明核实通过" in t for t in captions))
        self.assertTrue(any("也不等于系统放行" in t for t in captions))

    def test_readable_report_keeps_three_to_one_audit_column(self) -> None:
        import scripts.cited_demo_app as app

        run = self._run()
        calls: list[tuple[str, object]] = []

        class _Recorder:
            def columns(self, spec, **kwargs):
                calls.append(("columns", spec))
                return nullcontext(), nullcontext()

            def tabs(self, names):
                calls.append(("tabs", names))
                return [nullcontext() for _ in names]

            def __getattr__(self, name):
                def record(*args, **kwargs):
                    calls.append((name, args))
                return record

        with patch.object(app, "st", _Recorder()), \
             patch.object(app, "_render_a1") as prose, \
             patch.object(app, "_render_balance"), \
             patch.object(app, "_render_metric_tables"), \
             patch.object(app, "_render_source_regions"), \
             patch.object(app, "_render_assurance_rail") as audit:
            app._render_report(run, None, "侧车未加载")
        self.assertIn(("columns", [3, 1]), calls)
        self.assertEqual(prose.call_count, 2)
        audit.assert_called_once_with(run, None, "侧车未加载")

    def test_prose_is_grouped_by_paragraph_and_hard_errors_remain_visible(self) -> None:
        import scripts.cited_demo_app as app

        section = self._run().sections["company"]
        paragraphs: list[str] = []
        errors: list[str] = []

        class _Recorder:
            def expander(self, *args, **kwargs):
                return nullcontext()

            def markdown(self, body, **kwargs):
                if 'class="report-paragraph"' in body:
                    paragraphs.append(body)

            def error(self, body, **kwargs):
                errors.append(body)

            def __getattr__(self, name):
                return lambda *args, **kwargs: None

        with patch.object(app, "st", _Recorder()), \
             patch.object(app, "_render_citation"):
            app._render_a1(section, True)
        expected = sum(len(subsection["paragraphs"])
                       for subsection in section.draft["subsections"])
        self.assertEqual(len(paragraphs), expected)
        first_two = section.draft["subsections"][0]["paragraphs"][0]["sentences"][:2]
        self.assertTrue(all(sentence["text"] in paragraphs[0] for sentence in first_two))
        self.assertTrue(any('class="report-sentence-error"' in body
                            for body in paragraphs))
        self.assertEqual(len(errors), len(section.hard_sentence_ids))

    def test_progress_does_not_impersonate_harness_checkpoint(self) -> None:
        import scripts.cited_demo_app as app

        run = self._run()
        with patch.dict(os.environ, {loader_mod.ASSURANCE_DIR_ENV: str(self.R6_DIR)}):
            assurance, _ = app._load_assurance(run)
        rows = app._progress_rows(run, assurance)
        self.assertEqual(rows[0]["环节"], "材料送达 Writer")
        self.assertEqual(rows[-1]["状态"], "部分完成")
        self.assertEqual(len(rows), 6)
        self.assertTrue(all("可回查身份" in row for row in rows))
        calls = []

        class _Recorder:
            def expander(self, *args, **kwargs):
                return nullcontext()

            def __getattr__(self, name):
                def record(*args, **kwargs):
                    calls.append((name, args))
                return record

        with patch.object(app, "st", _Recorder()):
            app._render_progress(run, assurance)
        self.assertFalse(any(name == "progress" for name, _ in calls))
        self.assertTrue(any(name == "warning" and "最近 checkpoint 无法核验" in args[0]
                            for name, args in calls))


def _summary(result) -> dict:
    """运行器要的是 `passed`/`failed`/`skipped`/`details` 四个键，**不是退出码、也不是抛异常**。

    `run_evals` 收下 `main()` 的返回值之后直接对它取 `.get(...)`；返回 `None` 或 `int`
    会在那一行抛 `AttributeError`，而那一行不在它的 `except` 里——套件静默截断、
    `TOTAL` 不打印，日志看起来仍像「基本跑完」。
    """
    return {
        "passed": result.testsRun - len(result.failures) - len(result.errors)
        - len(result.skipped),
        "failed": len(result.failures) + len(result.errors),
        "skipped": len(result.skipped),
        "details": [f"FAIL {test}\n{tb}" for test, tb in result.failures + result.errors],
    }


def main() -> dict:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PersistedCitedDemoTests)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ReportReviewSidecarTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return _summary(result)


if __name__ == "__main__":
    raise SystemExit(0 if main()["failed"] == 0 else 1)

"""Focused checks for the M930-4/5 read-only **display binding**.

Run: ``python -X utf8 -m evals.test_m930_5_display_binding``.

The fixture is the two already-persisted real artifacts the demo shows side by
side: the single-section flat run ``m930_3_cited_real_ndc5_company_r1`` and
``m930_3_cited_real_dual_v2_r1/financial``. No LLM, network, database
connection, report generation or human-confirmation write is allowed.
"""

from __future__ import annotations

import dataclasses
import json
import os
import socket
import sqlite3
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from sections import cited_demo_binding as B
from sections import cited_demo_loader as L


REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "evaluation" / "results"
COMPANY = "m930_3_cited_real_ndc5_company_r1"
DUAL = "m930_3_cited_real_dual_v2_r1"


class _Recorder:
    """只记调用、不依赖 Streamlit 运行时的 `st` 替身。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.kwargs: list[dict] = []
        self.session_state: dict = {}

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self.calls.append((name, args))
            self.kwargs.append(kwargs)
            if name == "columns":
                spec = args[0]
                count = spec if isinstance(spec, int) else len(spec)
                return [_Recorder() for _ in range(count)]
            if name == "tabs":
                return [_Recorder() for _ in range(len(args[0]))]
            if name in ("container", "expander", "empty", "form", "status", "spinner"):
                return self
            if name in ("radio", "selectbox"):
                # 真实 Streamlit 返回被选中的选项；默认选第一个。
                return (args[1] if len(args) > 1 else kwargs.get("options"))[0]
            return None
        return record

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def texts(self, name: str) -> list[str]:
        """该调用的完整文本面——**位置参数与关键字参数一起**（`st.progress` 的文案在 `text=`）。"""
        out = []
        for (call_name, args), kwargs in zip(self.calls, self.kwargs):
            if call_name != name:
                continue
            parts = [str(a) for a in args] + [f"{k}={v}" for k, v in kwargs.items()]
            out.append(" ".join(parts))
        return out


class _FakeUpload:
    """Streamlit `UploadedFile` 的最小替身：`name` + 内存字节。"""

    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


def _flatten(recorder: _Recorder) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = [(n, " ".join(str(a) for a in args))
                                  for n, args in recorder.calls]
    return out


class DisplayBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        for run in (COMPANY, DUAL):
            if not (RESULTS / run).is_dir():
                raise unittest.SkipTest("已存真实产物不在本机；不伪造真实产物")

    def test_binding_pins_both_sources_and_their_identities(self) -> None:
        binding = B.load_display_binding(RESULTS)
        self.assertEqual(binding.binding_version, "cdb-1")
        # 两个来源来自**不同 run**，绑定不得把它们合并成一个身份。
        self.assertEqual(B.COMPANY_SOURCE.run_id, COMPANY)
        self.assertEqual(B.FINANCIAL_SOURCE.run_id, DUAL)
        self.assertNotEqual(B.COMPANY_SOURCE.run_id, B.FINANCIAL_SOURCE.run_id)
        company = binding.company
        self.assertEqual(company.report_version["report_version"],
                         "crpv_d9fe3e91c58cfa6a91c852f4")
        self.assertEqual(company.sentence_count, 16)
        self.assertEqual(len(company.fact_safety_sentence_ids), 6)
        self.assertEqual(len(company.column_coverage_sentence_ids), 2)
        self.assertEqual(set(company.fact_safety_sentence_ids)
                         | set(company.column_coverage_sentence_ids),
                         set(company.hard_sentence_ids))
        self.assertEqual(tuple(company.blocking_sentence_ids),
                         company.fact_safety_sentence_ids)
        # 两个数字必须分别成立：6 是事实安全阻断，2 是栏目覆盖待修。
        self.assertEqual(int(company.report_version["blocking_sentence_count"]), 6)
        self.assertEqual(len(company.source_regions), 12)
        self.assertEqual(sum(r["confirmation_state"] == "human_confirmed"
                             for r in company.source_regions), 0)
        # 结构化缺口为 0 **不**等于「全部栏目已覆盖」——这条只验证产物读数是 0。
        self.assertEqual(company.gap_bins["axes"]["total_gap_count"], 0)
        self.assertTrue(company.review_call_attested)
        self.assertEqual(len(binding.artifact_hashes), 17)
        financial = binding.financial
        self.assertEqual(financial.balance_structure["producer_kind"],
                         "deterministic_presentation")
        self.assertEqual(financial.balance_structure["model_calls_issued"], 0)
        self.assertEqual(len(financial.items), 3)
        self.assertEqual(financial.reading_count, 117)

    def test_company_review_must_be_in_its_own_run_ledger(self) -> None:
        """逐句审阅只有在**本 run** 的成功账本里才算「既有独立审阅」。"""
        binding = B.load_display_binding(RESULTS)
        ledger = binding.company.ledger
        self.assertEqual(ledger["run_outcome"], "completed")
        attempts = ledger["call_budget"]["attempts"]
        self.assertTrue(any(a["category"] == "cited_prose_review"
                            and a["section_id"] == "company" and a["status"] == "ok"
                            for a in attempts))
        self.assertEqual(binding.company.review["call"]["call_id"],
                         next(a["call_id"] for a in attempts
                              if a["category"] == "cited_prose_review"))

    def test_a2_review_identity_is_recomputed_not_inferred_from_column_names(self) -> None:
        """A2 的审阅能否单列，靠**重算内容版本**，不靠栏目同名。"""
        binding = B.load_display_binding(RESULTS)
        identity = binding.a2_review
        self.assertIsNotNone(identity)
        self.assertTrue(identity.verified)
        self.assertEqual(identity.recomputed_scope_version,
                         identity.recorded_scope_version)
        self.assertTrue(identity.recomputed_scope_version.startswith("rrv_"))

        # 反例：A2 内容改一处（这里改一个栏目标签），重算出的版本就不再相同。
        tampered = dataclasses.replace(
            binding.financial,
            balance_structure={**binding.financial.balance_structure,
                               "items": [dict(item) for item in binding.financial.items]})
        tampered.balance_structure["items"][0]["label"] += "（被改过）"
        changed = B._a2_review_identity(tampered, results_root=RESULTS)
        self.assertIsNotNone(changed)
        self.assertFalse(changed.verified)
        self.assertNotEqual(changed.recomputed_scope_version,
                            changed.recorded_scope_version)
        self.assertIn("不得显示为已审", changed.reason)

    def test_artifact_byte_mismatch_is_refused(self) -> None:
        """任一被钉住的文件改一个字节，绑定就必须拒，而不是降级为「仍然可以看」。"""
        original = L._Snapshot.read

        def altered(snapshot: L._Snapshot, relative: str) -> bytes:
            data = original(snapshot, relative)
            if relative == "cited_prose_reworked.json":
                return data.replace(b"16", b"17", 1)
            return data

        with patch.object(L._Snapshot, "read", altered), \
             self.assertRaises(B.DisplayBindingError) as caught:
            B.load_display_binding(RESULTS)
        self.assertIn("cited_prose_reworked.json", str(caught.exception))
        self.assertIn("字节哈希不符", str(caught.exception))
        self.assertIn("拒绝展示", str(caught.exception))

    def test_missing_frozen_artifact_is_refused(self) -> None:
        """缺一个被钉住的冻结产物，同样是**拒**，且点名是哪个文件读不到。"""
        original = L._Snapshot.read

        def missing(snapshot: L._Snapshot, relative: str) -> bytes:
            if relative == "cited_call_ledger.json":
                raise L.CitedDemoLoadError("artifact missing")
            return original(snapshot, relative)

        with patch.object(L._Snapshot, "read", missing), \
             self.assertRaises(B.DisplayBindingError) as caught:
            B.load_display_binding(RESULTS)
        self.assertIn("cited_call_ledger.json", str(caught.exception))
        self.assertIn("读不到", str(caught.exception))

    def test_a2_producer_must_stay_deterministic(self) -> None:
        """字节钉是第一道；万一某版绑定漏钉了 A2，语义门仍要拒「自称发过模型调用」。"""
        original = L._Snapshot.read
        target = "financial/" + B.A2_FILENAME
        #: 模拟「这一版绑定没有把 A2 json 纳入逐字节钉子」。
        loose = dataclasses.replace(
            B.FINANCIAL_SOURCE,
            artifacts=tuple((rel, sha) for rel, sha in B.FINANCIAL_SOURCE.artifacts
                            if rel != B.A2_FILENAME))

        def altered(snapshot: L._Snapshot, relative: str) -> bytes:
            data = original(snapshot, relative)
            if relative != target:
                return data
            body = json.loads(data.decode("utf-8"))
            body["model_calls_issued"] = 3
            return json.dumps(body, ensure_ascii=False).encode("utf-8")

        with patch.object(B, "FINANCIAL_SOURCE", loose), \
             patch.object(L._Snapshot, "read", altered), \
             self.assertRaises(B.DisplayBindingError) as caught:
            B.load_display_binding(RESULTS)
        self.assertIn("A2 产物声明了模型调用", str(caught.exception))

    def test_binding_reads_do_not_touch_network_database_or_disk_writes(self) -> None:
        paths = tuple(p for run in (COMPANY, DUAL)
                      for p in (RESULTS / run).rglob("*") if p.is_file())
        before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in paths}
        with patch.object(socket.socket, "connect",
                          side_effect=AssertionError("network")), \
             patch.object(sqlite3, "connect", side_effect=AssertionError("database")):
            binding = B.load_display_binding(RESULTS)
        self.assertEqual(len(binding.artifact_hashes), 17)
        self.assertEqual(before, {p: (p.stat().st_size, p.stat().st_mtime_ns)
                                  for p in before})


class SourceDocumentTests(unittest.TestCase):
    """第一屏：只在内存里算哈希，三份全中才放行。"""

    def _uploads(self) -> list[tuple[str, bytes]]:
        out = []
        for doc in B.SOURCE_DOCUMENTS:
            path = REPO / "data" / "samples" / "300750" / "announcements" / doc.filename
            if not path.is_file():
                raise unittest.SkipTest("案例来源 PDF 不在本机；不伪造材料")
            out.append((doc.filename, path.read_bytes()))
        return out

    def test_three_matching_documents_open_the_replay(self) -> None:
        result = B.verify_source_documents(self._uploads())
        self.assertTrue(result.accepted)
        self.assertEqual(result.missing, ())
        self.assertEqual(result.message, "材料与已保存案例一致，可查看运行回放")
        self.assertEqual([v.filename for v in result.verdicts],
                         [d.filename for d in B.SOURCE_DOCUMENTS])
        self.assertTrue(all(v.matches for v in result.verdicts))
        for verdict in result.verdicts:
            self.assertEqual(verdict.declared_sha256, verdict.uploaded_sha256)

    def test_one_mismatch_refuses_entry_and_names_the_document(self) -> None:
        uploads = self._uploads()
        uploads[1] = (uploads[1][0], uploads[1][1] + b"\x00")
        result = B.verify_source_documents(uploads)
        self.assertFalse(result.accepted)
        self.assertEqual(result.missing, ("NDSD_2024_year",))
        self.assertIn("拒绝进入该案例", result.message)
        self.assertIn("NDSD_2024_year", result.message)
        verdict = next(v for v in result.verdicts if v.document_id == "NDSD_2024_year")
        self.assertFalse(verdict.matches)
        self.assertEqual(verdict.uploaded_sha256, "")

    def test_missing_document_and_extra_file_are_both_reported(self) -> None:
        uploads = self._uploads()[:2] + [("别的材料.pdf", b"not the saved case")]
        result = B.verify_source_documents(uploads)
        self.assertFalse(result.accepted)
        self.assertEqual(result.missing, ("NDSD_KCZ_2026",))
        self.assertEqual(result.unexpected, ("别的材料.pdf",))

    def test_empty_selection_is_refused(self) -> None:
        result = B.verify_source_documents([])
        self.assertFalse(result.accepted)
        self.assertEqual(result.missing,
                         tuple(d.document_id for d in B.SOURCE_DOCUMENTS))

    def test_manifest_declares_the_same_source_hashes(self) -> None:
        """产物自己写下的来源哈希前缀，必须与磁盘上那三份 PDF 的完整哈希一致。"""
        binding = B.load_display_binding(RESULTS)
        declared = B.manifest_document_hashes(binding.company)
        self.assertEqual(set(declared), {d.document_id for d in B.SOURCE_DOCUMENTS})
        for doc in B.SOURCE_DOCUMENTS:
            prefix = declared[doc.document_id]
            self.assertGreaterEqual(len(prefix), 12)
            self.assertTrue(doc.sha256.startswith(prefix.lower()),
                            f"{doc.document_id}: 产物自述 {prefix} 不是磁盘实算 "
                            f"{doc.sha256} 的前缀")


class ThreeScreenTests(unittest.TestCase):
    """三屏与右栏：真实来源标注、拒绝路径、不伪造实时与恢复能力。"""

    @classmethod
    def setUpClass(cls) -> None:
        for run in (COMPANY, DUAL):
            if not (RESULTS / run).is_dir():
                raise unittest.SkipTest("已存真实产物不在本机；不伪造真实产物")
        cls.binding = B.load_display_binding(RESULTS)

    def _app(self):
        import scripts.cited_demo_app as app
        return app

    def test_report_rail_states_the_pairing_and_never_carries_over_the_old_seven(self) -> None:
        app = self._app()
        recorder = _Recorder()
        with patch.object(app, "st", recorder):
            app._render_binding_rail(self.binding)
        texts = _flatten(recorder)
        blob = "\n".join(t for _, t in texts)
        self.assertIn("并列演示基线 · 不可发布 · 未经人工接受", blob)
        self.assertIn("本组合", blob)
        self.assertIn("未", blob)
        self.assertIn("重新进行报告级审阅", blob)
        self.assertIn("6 / 16", blob)
        self.assertIn("2", blob)
        self.assertIn("A2 独立内容身份核对通过", blob)
        # 右栏里**不得**出现旧双节 run 的公司节读数。
        self.assertNotIn("7 / 16", blob)
        self.assertNotIn("7 句硬错", blob)
        self.assertIn("不等于", blob)   # total_gap_count = 0 的读法
        self.assertIn("全部栏目已覆盖", blob)

    def test_rail_prominently_says_unreviewed_when_a2_identity_fails(self) -> None:
        """反例落到**页面**上：A2 身份对不上或无读数时，右栏必须显著显示「未审」。"""
        app = self._app()
        tampered = dataclasses.replace(
            self.binding.a2_review,
            verified=False,
            recomputed_scope_version="rrv_00000000000000000000000a",
            reason="既有读数绑的是另一份 A2 内容：重算出的内容版本与它不同。")
        cases = (
            (tampered, "A2 未审：既有读数绑的是另一份 A2 内容", tampered.reason),
            (None, "A2 未审：读不到可核验的既有审阅读数", "不得据此认定已审阅"),
        )
        for a2_review, headline, detail in cases:
            with self.subTest(a2_review=a2_review):
                recorder = _Recorder()
                with patch.object(app, "st", recorder):
                    app._render_binding_rail(dataclasses.replace(
                        self.binding, a2_review=a2_review))
                blob = "\n".join(t for _, t in _flatten(recorder))
                self.assertIn(headline, blob)
                self.assertIn(detail, blob)
                # 未审时**不得**出现「核对通过」。
                self.assertNotIn("A2 独立内容身份核对通过", blob)
                # 无论 A2 审没审，这四条恒在场。
                self.assertIn("并列演示基线 · 不可发布 · 未经人工接受", blob)
                self.assertIn("本组合", blob)
                self.assertIn("重新进行报告级审阅", blob)
                self.assertIn("0 / 12", blob)

    def test_company_tab_shows_ndc5_six_not_the_old_seven(self) -> None:
        app = self._app()
        recorder = _Recorder()
        with patch.object(app, "st", recorder), \
             patch.object(app, "_render_a1") as prose, \
             patch.object(app, "_render_balance"), \
             patch.object(app, "_render_source_regions") as regions:
            app._render_deliverable(self.binding)
        self.assertIn([3, 1], [args[0] for name, args in recorder.calls
                               if name == "columns"])
        prose.assert_called_once()
        self.assertTrue(prose.call_args[0][1])     # review_attested = 同 run 账本核过
        regions.assert_called_once_with(self.binding.company)
        blob = "\n".join(t for _, t in _flatten(recorder))
        self.assertIn(COMPANY, blob)
        self.assertIn("6 句事实安全硬错", blob)
        self.assertIn("2 句栏目覆盖待修", blob)
        self.assertIn("不是旧双节 run 的那份公司正文", blob)

    def test_process_screen_builds_saved_nodes_and_never_fakes_realtime(self) -> None:
        app = self._app()
        company_nodes = app._company_nodes(self.binding)
        financial_nodes = app._financial_nodes(self.binding)
        self.assertTrue(all("保存节点" in n for n in company_nodes))
        self.assertTrue(all(n["来源 run"] == COMPANY for n in company_nodes))
        self.assertTrue(all(n["来源 run"] == DUAL for n in financial_nodes))
        # 两个来源分别列节点，节点数不同也互不混合。
        recorder = _Recorder()
        with patch.object(app, "st", recorder):
            app._render_process(self.binding)
        blob = "\n".join(t for _, t in _flatten(recorder))
        self.assertIn("已存运行过程回放", blob)
        self.assertIn("保存节点", blob)
        # 不冒充实时：百分比/耗时/模型调用/恢复点都以**否定**出现，且没有别的读数替代。
        self.assertIn("不显示", blob)
        self.assertIn("实时百分比", blob)
        self.assertIn("耗时", blob)
        self.assertIn("正在调用模型", blob)
        self.assertIn("可从此处恢复", blob)
        self.assertIn("产物提交后的只读回放", blob)
        # 唯一一个进度条是**回放位置**，不是实时百分比。
        progress = recorder.texts("progress")
        self.assertEqual(len(progress), 1)
        self.assertTrue(all("回放位置" in text for text in progress))
        # 两个来源分别列节点，绝不画成一次联合运行的单一时间线。
        self.assertIn("按来源分别列出", blob)
        # 「播放」只翻页，不重跑。
        self.assertIn("不", blob)
        self.assertIn("代表每一步在本次会话里又跑了一遍", blob)

    def test_upload_screen_never_implies_on_the_spot_generation(self) -> None:
        app = self._app()
        recorder = _Recorder()
        with patch.object(app, "st", recorder):
            app._render_upload(self.binding)
        buttons = [args for name, args in recorder.calls if name == "button"]
        self.assertEqual(len(buttons), 2)
        labels = [str(args[0]) for args in buttons]
        self.assertEqual(labels, ["查看已存运行过程 →", "直接阅读报告与审核 →"])
        for label in labels:
            # 措辞不得暗示「当场重新生成」，也不得用「生成」二字冒充本次动作。
            self.assertNotIn("生成", label)
            self.assertNotIn("重新", label)
        blob = "\n".join(t for _, t in _flatten(recorder))
        self.assertIn("授信类型、金额、期限、增信措施在本演示里**只是表单**", blob)
        self.assertIn("**没有**用于生成", blob)
        self.assertIn("只读回放", blob)
        self.assertIn("不现场重新生成", blob)

    def test_upload_results_drive_the_entry_buttons(self) -> None:
        """核对结果必须真的门控入口：一份不符就 `disabled=True`，三份全中才放行。"""
        app = self._app()
        for accepted, expected in ((False, True), (True, False)):
            with self.subTest(accepted=accepted):
                seen: list[dict] = []
                recorder = _Recorder()

                def capture_button(*args, **kwargs):
                    seen.append(kwargs)

                if accepted:
                    docs = []
                    for doc in B.SOURCE_DOCUMENTS:
                        path = (REPO / "data" / "samples" / "300750"
                                / "announcements" / doc.filename)
                        if not path.is_file():
                            self.skipTest("案例来源 PDF 不在本机；不伪造材料")
                        docs.append(_FakeUpload(doc.filename, path.read_bytes()))
                    recorder.file_uploader = lambda *a, **k: docs
                with patch.object(app, "st", recorder), \
                     patch.object(recorder, "button", capture_button, create=True):
                    app._render_upload(self.binding)
                self.assertEqual(len(seen), 2)
                self.assertTrue(all(kw.get("disabled") is expected for kw in seen),
                                f"accepted={accepted} 时按钮的 disabled 读数不对：{seen}")
                blob = "\n".join(t for _, t in _flatten(recorder))
                if accepted:
                    self.assertIn("材料与已保存案例一致", blob)
                    self.assertIn("3 / 3 一致", blob)
                else:
                    self.assertIn("材料与已保存案例不一致，拒绝进入该案例", blob)
                    self.assertIn("0 / 3 一致", blob)
                    self.assertIn("也不提供「跳过校验继续」的入口", blob)

    def test_audit_detail_labels_the_original_run_history(self) -> None:
        app = self._app()
        recorder = _Recorder()
        with patch.object(app, "st", recorder), \
             patch.object(app, "_render_historical_audit") as history:
            app._render_audit_detail(self.binding, str(RESULTS))
        history.assert_called_once_with(str(RESULTS))
        blob = "\n".join(t for _, t in _flatten(recorder))
        self.assertIn("展示绑定", blob)
        self.assertIn("不是 `report_version`", blob)
        self.assertIn("A2 既有审阅的独立内容身份", blob)
        self.assertIn(self.binding.a2_review.recomputed_scope_version, blob)


class HistoricalAuditTests(unittest.TestCase):
    """旧侧车只能出现在**明确标注原 run** 的历史审计区。"""

    def _app(self):
        import scripts.cited_demo_app as app
        return app

    def test_historical_area_is_labelled_and_reads_the_original_run(self) -> None:
        app = self._app()
        if not (RESULTS / DUAL).is_dir():
            self.skipTest("原本 run 不在本机")
        recorder = _Recorder()
        with patch.object(app, "st", recorder):
            app._render_historical_audit(str(RESULTS))
        blob = "\n".join(t for _, t in _flatten(recorder))
        self.assertIn("原 run 历史审计", blob)
        self.assertIn(DUAL, blob)
        self.assertIn("未套用到本次并列展示", blob)
        self.assertIn("未", blob)
        # 本区展示的是原本 run 的**两节**报告，不是并列绑定的公司节。
        self.assertIn("原 run 的两节报告", blob)

    def test_ndc5_never_enters_the_dual_loader_candidates(self) -> None:
        """ndc5 是单节扁平 run：结构上就进不了双节 loader，也就无从被误绑。"""
        candidates = L.list_cited_runs(RESULTS)
        self.assertNotIn(COMPANY, candidates)
        self.assertIn(DUAL, candidates)
        with self.assertRaises(L.CitedDemoLoadError):
            L.load_cited_run(COMPANY, results_root=RESULTS)

    def test_binding_company_is_a_different_document_from_the_page_company(self) -> None:
        """两个公司节是**两份不同正文**：绑定读 ndc5，页面历史区读 dual_v2_r1。"""
        binding = B.load_display_binding(RESULTS)
        self.assertEqual(binding.company.report_version["report_version"],
                         "crpv_d9fe3e91c58cfa6a91c852f4")
        if not (RESULTS / DUAL).is_dir():
            self.skipTest("原本 run 不在本机")
        old = L.load_cited_run(DUAL, results_root=RESULTS)
        self.assertEqual(old.sections["company"].report_version["report_version"],
                         "crpv_8a6b2e150bbda5a59c307b2b")
        self.assertNotEqual(binding.company.report_version["report_version"],
                            old.sections["company"].report_version["report_version"])
        self.assertEqual(len(old.sections["company"].hard_sentence_ids), 7)


def _summary(result) -> dict:
    """运行器要 `passed`/`failed`/`skipped`/`details` 四个键，不是退出码。"""
    return {
        "passed": result.testsRun - len(result.failures) - len(result.errors)
        - len(result.skipped),
        "failed": len(result.failures) + len(result.errors),
        "skipped": len(result.skipped),
        "details": [f"FAIL {test}\n{tb}" for test, tb in result.failures + result.errors],
    }


def main() -> dict:
    suite = unittest.TestSuite()
    for case in (DisplayBindingTests, SourceDocumentTests, ThreeScreenTests,
                 HistoricalAuditTests):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(case))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return _summary(result)


if __name__ == "__main__":
    raise SystemExit(0 if main()["failed"] == 0 else 1)

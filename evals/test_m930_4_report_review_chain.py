"""Focused offline tests for the **executable but never-fired** report-level review chain.

Run: python -m evals.test_m930_4_report_review_chain

每一条都对应一个具体缺陷：分批覆盖、容量预检、**发现式输出契约**（空意见合法）、
问题定位、非法单元与伪造出处、`blocking` 强转反例、信封层、单元归属、替身端到端、
身份 round-trip、Controller 绑定、**硬错误优先**、prompt 资产对账、事前预算门、
**失败留痕**（首批失败 / 后批失败 / 截断）、运行入口离线替身、源 run 逐字节不变。

本模块**不发起任何模型调用、不联网、不写库、不改历史 run**：所有客户端都是本地替身，
所有写入都落在 `evaluation/results/_tmp_*` 并在 `finally` 里清理。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from assurance import report_review as RR
from assurance import report_reviewer as RX
from assurance import schema as AS
from assurance.cited_controller import assess_persisted_run
from llm import budget as LB

from evals.test_m930_4_report_review import ROOT, SOURCE, REPORT_ID, _facts, _scopes, _rewrite_sentence
from scripts import run_m930_4_report_review as ENTRY


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


class EchoClient:
    """离线回声：**只报发现**，因此缺省回一个空 `issues`。**不发请求。**

    `irv-2` 之后"逐单元回一条 `supported`"不再是契约要求的行为，所以缺省回声必须示范
    **合法的空数组**；需要造出具体问题的用例通过 `mutate` 注入。
    """

    def __init__(self, *, mutate=None) -> None:
        self.seen: list[dict] = []
        self.mutate = mutate

    def review(self, *, messages, system, prompt_version, model_policy):
        payload = json.loads(messages[0]["content"])
        self.seen.append(payload)
        body = {"issues": []}
        if self.mutate is not None:
            body = self.mutate(payload, body)
        return RX.ReportReviewResult(
            text=json.dumps(body, ensure_ascii=False),
            call_id=f"offline-{payload['scope_kind']}-{len(self.seen)}",
            model="offline", prompt_version=prompt_version, status="ok")


class FailingClient(EchoClient):
    """在第 `fail_at` 次调用上抛异常（解析失败用 `mutate` 造出坏 JSON）。"""

    def __init__(self, *, fail_at: int) -> None:
        super().__init__()
        self.fail_at = fail_at

    def review(self, **kwargs):
        if len(self.seen) + 1 == self.fail_at:
            self.seen.append(json.loads(kwargs["messages"][0]["content"]))
            raise RuntimeError("provider 断线")
        return super().review(**kwargs)


def _issue_payload(key: str, **over) -> dict:
    row = {"unit_kind": key.partition(":")[0], "unit_id": key.partition(":")[2],
           "category": "insufficient", "severity": "high", "blocking": False,
           "reason": "x", "evidence_refs": [], "suggested_target": None}
    row.update(over)
    return row


def _run(scope, client=None) -> RX.ReportReviewRun:
    return RX.run_report_review(scope=scope, client=client or EchoClient(),
                                model_policy="offline-chain-test")


class ReportReviewChainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scopes = _scopes()
        self.bounds = {kind: RX.report_review_structural_bound(scope)
                       for kind, scope in self.scopes.items()}

    # --- 1. 分批 -----------------------------------------------------------------
    def test_batches_cover_every_unit_exactly_once_and_are_deterministic(self) -> None:
        """1. 分批并集 == scope 单元集、两两不重叠，且两次规划逐字段相同。"""
        for kind, scope in self.scopes.items():
            first = RX.plan_report_review_batches(scope)
            second = RX.plan_report_review_batches(scope)
            self.assertEqual([b.batch_id for b in first], [b.batch_id for b in second])
            covered = [key for batch in first for key in batch.unit_keys]
            self.assertEqual(sorted(covered), sorted(scope.covered_unit_ids))
            self.assertEqual(len(set(covered)), len(covered), f"{kind}：分批重叠")
            RX.assert_within_capacity(scope, first)
            bound = self.bounds[kind]
            self.assertEqual(bound["units"], len(scope.covered_unit_ids))
            self.assertEqual(bound["batches"], len(first))
            self.assertLessEqual(bound["max_batch_units"], RX.REPORT_REVIEW_MAX_UNITS_PER_CALL)
            self.assertLessEqual(bound["max_batch_chars"],
                                 RX.REPORT_REVIEW_MAX_INPUT_CHARS_PER_CALL)
            # 批数是**推出来的**：单元数上限是主闸，字符上限只会让它更多，不会更少
            self.assertGreaterEqual(
                bound["batches"],
                -(-bound["units"] // RX.REPORT_REVIEW_MAX_UNITS_PER_CALL))

    # --- 2. 容量预检 -------------------------------------------------------------
    def test_capacity_preflight_fails_closed(self) -> None:
        """2. 单元数上限**真的**切批；单单元超字符上限与超上界都在发请求前被拒。"""
        scope = self.scopes["cross_section"]
        one_each = RX.plan_report_review_batches(scope, max_units=1)
        self.assertEqual(len(one_each), len(scope.covered_unit_ids))
        self.assertTrue(all(len(b.unit_keys) == 1 for b in one_each))
        self.assertEqual(sorted(k for b in one_each for k in b.unit_keys),
                         sorted(scope.covered_unit_ids))

        # 单单元自身就超字符上限 ⇒ 直接失败（不截断、不丢单元、不单独硬发）
        with self.assertRaises(RX.ReportReviewError) as ctx:
            RX.plan_report_review_batches(scope, max_chars=1)
        self.assertEqual(ctx.exception.reason, "unit_exceeds_batch_capacity")

        # 规划出的批次若超出**常量**上限，预检必须拒绝（上界高于已批准容量 = 不许发）
        heavy = self.scopes["material_selectivity"]
        oversize = RX.plan_report_review_batches(
            heavy, max_units=RX.REPORT_REVIEW_MAX_UNITS_PER_CALL + 5)
        self.assertTrue(any(len(b.unit_keys) > RX.REPORT_REVIEW_MAX_UNITS_PER_CALL
                            for b in oversize))
        with self.assertRaises(RX.ReportReviewError):
            RX.assert_within_capacity(heavy, oversize)

    # --- 3. 发现式契约：空意见合法，覆盖由程序推 -----------------------------------
    def test_findings_only_contract_accepts_an_empty_issue_list(self) -> None:
        """3. **空 `issues` 合法**：这次调用完成了，只是没有报出会实质改变结论的问题。

        覆盖**不由**模型回声证明：`requested_unit_keys` 取自批次本身，因此即便模型一条
        意见都没提，程序照样知道"哪些成员进了请求"。这正是 `irv-2` 与 `irv-1` 的分界。
        """
        scope = self.scopes["material_selectivity"]
        batch = RX.plan_report_review_batches(scope)[0]
        parsed = RX.parse_report_review('{"issues": []}', scope=scope, batch=batch)
        self.assertEqual(parsed.issues, ())
        self.assertEqual(list(parsed.requested_unit_keys), sorted(batch.unit_keys))
        self.assertEqual(len(parsed.response_fingerprint), 64)

        # 端到端：三块全空意见 ⇒ 三块都 outcome="reviewed"，但 reported 单元数为 0
        for kind, scope in self.scopes.items():
            run = _run(scope)
            self.assertEqual(run.outcome, "reviewed")
            self.assertEqual(run.issues, ())
            self.assertEqual(run.reported_unit_ids, ())
            self.assertEqual(len(run.covered_unit_ids), len(scope.covered_unit_ids))
            self.assertEqual(run.excluded_unit_ids, ())

    def test_real_finding_is_localised_and_duplicates_are_rejected(self) -> None:
        """4. 真问题落到具体单元；同一单元 + 同一类别 + 同一理由的第二条整批被拒。"""
        scope = self.scopes["cross_section"]
        batch = RX.plan_report_review_batches(scope)[0]
        keys = list(batch.unit_keys)
        target = keys[0]
        parsed = RX.parse_report_review(
            json.dumps({"issues": [_issue_payload(target, reason="跨节口径与财务附注不一致")]},
                       ensure_ascii=False), scope=scope, batch=batch)
        self.assertEqual(len(parsed.issues), 1)
        self.assertEqual(parsed.issues[0].unit_ref.key, target)
        self.assertEqual(parsed.issues[0].report_version, scope.scope_version)
        self.assertIn(target, parsed.requested_unit_keys,
                      "提了意见的单元当然在请求里——定位必须落在本批")

        with self.assertRaises(RX.ReportReviewError) as ctx:
            RX.parse_report_review(
                json.dumps({"issues": [_issue_payload(target, reason="同一句话"),
                                       _issue_payload(target, reason="同一句话")]},
                           ensure_ascii=False), scope=scope, batch=batch)
        self.assertEqual(ctx.exception.reason, "issue_duplicate_finding")

    def test_illegal_unit_forged_ref_and_string_blocking_are_rejected(self) -> None:
        """5. 三条安全反例：越界单元、**本次请求里不存在的出处**、`"false"` 字符串。"""
        scope = self.scopes["cross_section"]
        batch = RX.plan_report_review_batches(scope)[0]
        keys = list(batch.unit_keys)
        # 本 scope 有、本批没有的单元 —— 张冠李戴必须拒
        other_kind = next(k for k, s in self.scopes.items() if k != "cross_section")
        other_scope = self.scopes[other_kind]
        other = next(k for k in other_scope.covered_unit_ids if k not in keys)
        with self.assertRaises(RX.ReportReviewError) as ctx:
            RX.parse_report_review(
                json.dumps({"issues": [_issue_payload(other)]}, ensure_ascii=False),
                scope=scope, batch=batch)
        self.assertIn(ctx.exception.reason,
                      ("issue_unit_not_in_batch", "issue_unit_not_in_scope"))

        # 伪造出处：写一个请求面里根本没出现过的定位串
        forged = "cited:不存在的来源#段0"
        anchors = RX._anchor_strings(batch.payload) | set(batch.unit_keys)
        self.assertFalse(RX._ref_is_traceable(forged, anchors))
        with self.assertRaises(RX.ReportReviewError) as ctx:
            RX.parse_report_review(
                json.dumps({"issues": [_issue_payload(keys[0], evidence_refs=[forged])]},
                           ensure_ascii=False), scope=scope, batch=batch)
        self.assertEqual(ctx.exception.reason, "issue_evidence_ref_unknown")

        # 出处**能回查**时必须放行：从请求面里真的存在的串里取一个
        real_ref = next(s for s in sorted(anchors)
                        if len(s) >= 8 and s not in set(batch.unit_keys))
        ok = RX.parse_report_review(
            json.dumps({"issues": [_issue_payload(keys[0], evidence_refs=[real_ref])]},
                       ensure_ascii=False), scope=scope, batch=batch)
        self.assertEqual(ok.issues[0].evidence_refs, (real_ref,))

        # `blocking` 必须是 JSON 布尔：字符串 "false" 经 bool() 会变 True
        with self.assertRaises(RX.ReportReviewError) as ctx:
            RX.parse_report_review(
                json.dumps({"issues": [_issue_payload(keys[0], blocking="false")]},
                           ensure_ascii=False), scope=scope, batch=batch)
        self.assertEqual(ctx.exception.reason, "issue_blocking_not_bool")
        self.assertIn("blocking", str(ctx.exception))

    # --- 6. 信封层 ---------------------------------------------------------------
    def test_envelope_rejects_release_unknown_and_non_json(self) -> None:
        """6. 放行/改写字段、未登记字段、非 JSON、空串、非法类别：五种入口各自被拒。

        每一种都必须是 **`ReportReviewError`**（本链的错误类型）：冻结 wire 抛的是
        `AssuranceSchemaError`，若让它原样逃出解析，`run_report_review` 的失败留存
        （`ReportReviewRunAborted`）就接不住——读者看到的是陌生异常，而不是「哪几批跑了」。
        """
        scope = self.scopes["cross_section"]
        batch = RX.plan_report_review_batches(scope)[0]
        good = {"issues": [_issue_payload(k) for k in batch.unit_keys]}

        for field in ("verdict", "publishable", "rewritten_text", "released"):
            with self.assertRaises(RX.ReportReviewError) as ctx:
                RX.parse_report_review(json.dumps({**good, field: "pass"}),
                                       scope=scope, batch=batch)
            self.assertEqual(ctx.exception.reason, "reviewer_release_attempt", field)
        with self.assertRaises(RX.ReportReviewError) as ctx:
            RX.parse_report_review(json.dumps({**good, "note": "hello"}),
                                   scope=scope, batch=batch)
        self.assertEqual(ctx.exception.reason, "response_envelope_invalid")
        with self.assertRaises(RX.ReportReviewError) as ctx:
            RX.parse_report_review("{not json", scope=scope, batch=batch)
        self.assertEqual(ctx.exception.reason, "response_not_json")
        with self.assertRaises(RX.ReportReviewError) as ctx:
            RX.parse_report_review("   ", scope=scope, batch=batch)
        self.assertEqual(ctx.exception.reason, "empty_response")
        # 意见行里试图改写正文：拒的理由必须是「改写」而不是「字段不认识」
        with self.assertRaises(RX.ReportReviewError) as ctx:
            RX.parse_report_review(json.dumps({"issues": [
                {**_issue_payload(k), "rewritten_text": "改过的句子"}
                for k in batch.unit_keys]}), scope=scope, batch=batch)
        self.assertEqual(ctx.exception.reason, "issue_row_invalid")
        # 冻结词表之外的类别必须由 wire 拒，理由照实转述
        with self.assertRaises(RX.ReportReviewError) as ctx:
            RX.parse_report_review(json.dumps({"issues": [
                _issue_payload(k, category="unsupported") for k in batch.unit_keys]}),
                scope=scope, batch=batch)
        self.assertEqual(ctx.exception.reason, "issue_schema_invalid")
        self.assertTrue(ctx.exception.unit_key)

    # --- 7. 单元归属 -------------------------------------------------------------
    def test_unit_attribution_is_from_the_bundle_not_guessed(self) -> None:
        """7. 解析出的引用取自 bundle 的 `unit_inventory`，不是从模型文本里猜的。"""
        scope = self.scopes["cross_section"]
        batch = RX.plan_report_review_batches(scope)[0]
        refs = {u.key: u for u in RX.batch_unit_refs(scope, batch)}
        self.assertEqual(set(refs), set(batch.unit_keys))
        parsed = RX.parse_report_review(
            json.dumps({"issues": [_issue_payload(k) for k in batch.unit_keys]}),
            scope=scope, batch=batch)
        self.assertEqual({i.unit_ref.key for i in parsed.issues}, set(batch.unit_keys))
        for issue in parsed.issues:
            self.assertEqual(issue.unit_ref, refs[issue.unit_ref.key])
        keys = list(batch.unit_keys)
        bad = f"{keys[0].partition(':')[0]}:{keys[0].partition(':')[2]}x"
        with self.assertRaises(RX.ReportReviewError) as ctx:
            RX.parse_report_review(
                json.dumps({"issues": [_issue_payload(k) for k in keys[:-1]]
                            + [_issue_payload(bad)]}), scope=scope, batch=batch)
        self.assertIn(ctx.exception.reason,
                      ("issue_unit_not_in_batch", "issue_unit_not_in_scope"))

    # --- 8. 替身端到端 -----------------------------------------------------------
    def test_echo_client_end_to_end_keeps_the_producer_honest(self) -> None:
        """8. 批数 == 上界；每批一条记录且 `llm_call_count == 1`；产出者标为离线回声。"""
        for kind, scope in self.scopes.items():
            client = EchoClient()
            run = _run(scope, client)
            self.assertEqual(len(client.seen), self.bounds[kind]["batches"])
            self.assertEqual(len(run.records), len(run.completed_batch_ids))
            self.assertEqual(len(run.requested_batch_ids), len(run.completed_batch_ids))
            self.assertEqual(run.failed_batch_ids, ())
            self.assertTrue(all(r.llm_call_count == 1 for r in run.records))
            self.assertTrue(all(r.outcome == "reviewed" for r in run.records))
            self.assertEqual(run.outcome, "reviewed")
            self.assertEqual(run.excluded_unit_ids, ())
            self.assertEqual(list(run.covered_unit_ids), sorted(scope.covered_unit_ids))
            self.assertEqual(list(run.requested_unit_ids), sorted(scope.covered_unit_ids))
            self.assertEqual(run.issues, (), "`irv-2` 的替身缺省只报发现，空数组合法")
            self.assertEqual(run.producer_kind, "offline_diagnostic_echo")
            self.assertFalse(run.trusted_as_assurance_input,
                             "离线回声不是独立审阅，不得被当成审阅输入")
            batches = RX.plan_report_review_batches(scope)
            for payload, batch in zip(client.seen, batches):
                self.assertEqual(payload["batch_unit_keys"], sorted(batch.unit_keys))
                self.assertEqual(payload["reviewed_unit_count"],
                                 len(payload["batch_unit_keys"]))

    # --- 9. 失败留痕 -------------------------------------------------------------
    def test_first_batch_failure_leaves_a_writable_readable_failed_run(self) -> None:
        """9. **第一批就失败**：run 仍能写能读，`excluded` 覆盖全部声明单元。"""
        scope = self.scopes["material_selectivity"]
        self.assertGreater(len(RX.plan_report_review_batches(scope)), 1,
                           "这一条要求该 scope 真的有多个批次")
        with self.assertRaises(RX.ReportReviewRunAborted) as ctx:
            RX.run_report_review(scope=scope, client=FailingClient(fail_at=1),
                                 model_policy="offline-chain-test")
        aborted = ctx.exception
        self.assertEqual(aborted.reason, "batch_failed")
        self.assertEqual(aborted.producer_kind, "offline_diagnostic_echo")
        self.assertEqual(len(aborted.attempted_batch_ids), 1)
        self.assertEqual(aborted.completed_batch_ids, ())
        run = RX.aborted_report_review_run(scope=scope, aborted=aborted,
                                           model_policy="offline-chain-test")
        self.assertEqual(run.outcome, "failed")
        self.assertEqual(len(run.requested_batch_ids), 1)
        self.assertEqual(run.completed_batch_ids, ())
        self.assertEqual(len(run.failed_batch_ids), 1)
        self.assertEqual(run.records, ())
        self.assertEqual(run.issues, ())
        self.assertEqual(run.covered_unit_ids, ())
        self.assertEqual(sorted(run.excluded_unit_ids), sorted(scope.covered_unit_ids))
        # 只试过第一批 ⇒ 未覆盖的单元**包含**后面那些批的单元
        self.assertLess(len(run.covered_unit_ids), len(scope.covered_unit_ids))
        self._round_trip(run)
        # 失败批**不**伪装成一条记录：记录数恒等于已完成批数
        self.assertEqual(len(run.records), len(run.completed_batch_ids))

    def test_second_batch_failure_keeps_the_first_batch_visible(self) -> None:
        """10. **第一批成功、第二批失败**：第一批的记录与意见照样留存。"""
        scope = self.scopes["material_selectivity"]
        batches = RX.plan_report_review_batches(scope)
        self.assertGreater(len(batches), 1, "这一条要求该 scope 真的有多个批次")
        first_keys = sorted(batches[0].unit_keys)
        target = first_keys[0]

        def mutate(payload, body):
            if payload["batch_unit_keys"] == first_keys:
                return {"issues": [_issue_payload(target, reason="第一批的真问题")]}
            return body

        class SecondFails(EchoClient):
            def review(self, **kwargs):
                payload = json.loads(kwargs["messages"][0]["content"])
                if payload["batch_unit_keys"] != first_keys:
                    self.seen.append(payload)
                    raise RuntimeError("第二批断线")
                return super().review(**kwargs)

        with self.assertRaises(RX.ReportReviewRunAborted) as ctx:
            RX.run_report_review(scope=scope, client=SecondFails(mutate=mutate),
                                 model_policy="offline-chain-test")
        aborted = ctx.exception
        self.assertEqual(len(aborted.attempted_batch_ids), 2)
        self.assertEqual(aborted.completed_batch_ids, (aborted.attempted_batch_ids[0],))
        run = RX.aborted_report_review_run(scope=scope, aborted=aborted,
                                           model_policy="offline-chain-test")
        self.assertEqual(run.outcome, "failed")
        self.assertEqual(len(run.requested_batch_ids), 2)
        self.assertEqual(len(run.completed_batch_ids), 1)
        self.assertEqual(len(run.failed_batch_ids), 1)
        self.assertEqual(len(run.records), 1)
        # 第一批的意见**没有被失败吞掉**
        self.assertEqual(len(run.issues), 1)
        self.assertEqual(run.issues[0].unit_ref.key, target)
        self.assertEqual(sorted(run.covered_unit_ids), first_keys)
        self.assertEqual(run.reported_unit_ids, (target,))
        self.assertNotIn(target, run.excluded_unit_ids)
        self._round_trip(run)

    def test_truncated_call_is_ledgered_as_a_failure_not_an_opinion(self) -> None:
        """11. **截断**：客户端先记一条失败流水再抛；那条记录**没有** `text`/`response_hash`。

        半截回复不是意见。本条只钉客户端这一步（不发真实请求：`chat_with_usage` 被替换成
        抛 `LLMTruncatedResponse`），随后验证它确实在 `run_report_review` 里变成一次
        **已尝试未完成**的批，而不是一条被解析的意见。
        """
        from llm import client as llm

        scope = self.scopes["cross_section"]
        batches = RX.plan_report_review_batches(scope)[:1]
        cut = llm.LLMResponse(text='{"issues": [', input_tokens=1200, output_tokens=8193,
                              latency_ms=10, model="offline-probe", call_id="cut-1",
                              finish_reason="max_tokens")
        client = RX.LlmReportReviewClient(model="offline-probe")
        with patch.object(llm, "chat_with_usage",
                          side_effect=llm.LLMTruncatedResponse(cut)):
            with self.assertRaises(llm.LLMTruncatedResponse):
                client.review(messages=RX.build_report_review_messages(batches[0]),
                              system="s", prompt_version=RX.REPORT_REVIEW_PROMPT_VERSION,
                              model_policy="offline-chain-test")
        self.assertEqual(len(client.calls), 1)
        record = client.calls[0]
        self.assertEqual(record["status"], "error")
        self.assertNotIn("text", record)
        self.assertNotIn("response_hash", record)
        self.assertIn("LLMTruncatedResponse", record["error"])
        self.assertEqual(record["finish_reason"], "max_tokens")
        self.assertEqual(record["output_tokens"], 8193)

        class Truncating:
            def review(self, *, messages, system, prompt_version, model_policy):
                raise llm.LLMTruncatedResponse(cut)

        with self.assertRaises(RX.ReportReviewRunAborted) as ctx:
            RX.run_report_review(scope=scope, client=Truncating(),
                                 model_policy="offline-chain-test")
        run = RX.aborted_report_review_run(scope=scope, aborted=ctx.exception,
                                           model_policy="offline-chain-test")
        self.assertEqual(len(run.requested_batch_ids), 1, "截断的批仍然计入『已尝试』")
        self.assertEqual(run.completed_batch_ids, ())
        self.assertEqual(run.issues, (), "半截回复不得变成意见")
        self.assertEqual(run.records, ())

    def _round_trip(self, run: RX.ReportReviewRun) -> None:
        scratch = Path(tempfile.mkdtemp(prefix="_tmp_m930_4_rr_chain_",
                                        dir=ROOT / "evaluation/results"))
        try:
            path = RX.write_report_review_run(scratch, run)
            back = RX.load_report_review_run(path)
            self.assertEqual(back.to_dict(), run.to_dict())
            self.assertEqual(back.run_id, run.run_id)
            self.assertEqual(back.report_id, REPORT_ID)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        self.assertFalse(scratch.exists())

    # --- 12. 身份 round-trip -----------------------------------------------------
    def test_run_round_trips_and_forged_bookkeeping_is_rejected(self) -> None:
        """12. 落盘读回逐字段相同；伪造覆盖账/派生键/`failed_batch_ids` 一律被拒。"""
        scope = self.scopes["financial_a2"]
        run = _run(scope)
        scratch = Path(tempfile.mkdtemp(prefix="_tmp_m930_4_rr_chain_",
                                        dir=ROOT / "evaluation/results"))
        try:
            path = RX.write_report_review_run(scratch, run)
            back = RX.load_report_review_run(path)
            self.assertEqual(back.to_dict(), run.to_dict())
            self.assertEqual(back.identity_body(), run.identity_body())
            with self.assertRaises(FileExistsError):
                RX.write_report_review_run(scratch, run)  # create-only（`x` 模式）
            forged = scratch / "forged.json"
            for mutate in (
                lambda p: p.__setitem__("covered_unit_ids", p["declared_unit_ids"][:1]),
                lambda p: p.__setitem__("reported_unit_ids", p["covered_unit_ids"]),
                lambda p: p.__setitem__("failed_batch_ids", ["rrb_forged"]),
                lambda p: p.__setitem__("record_ids", []),  # 派生键不进 wire
                lambda p: p.__setitem__("report_id", "another_run_dir"),
            ):
                payload = json.loads(path.read_text(encoding="utf-8"))
                mutate(payload)
                forged.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                with self.assertRaises(RX.ReportReviewError):
                    RX.load_report_review_run(forged)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        self.assertFalse(scratch.exists())

    # --- 13. Controller 绑定 -----------------------------------------------------
    def test_controller_marks_a_stale_run_stale_and_does_not_aggregate_it(self) -> None:
        """13. **正文改过之后的旧读数**记为陈旧且**不**聚合，而整份读数仍然出得来。

        陈旧是「本报告的另一版内容」，不是「另一份报告」——后者由第 14 条钉死。
        """
        draft = _facts("company").draft
        first = next(s for sub in draft.subsections for par in sub.paragraphs
                     for s in par.sentences)
        moved = _scopes([_facts("company", draft=_rewrite_sentence(draft, 0, first.text + "。")),
                         _facts("financial")])
        stale = _run(moved["cross_section"])
        current = self.scopes["cross_section"]
        self.assertEqual(stale.report_id, REPORT_ID)
        self.assertNotEqual(stale.scope_version, current.scope_version)
        self.assertNotEqual(stale.scope_id, current.scope_id,
                            "内容身份体含版本，所以这一层也会动——正因如此必须按 kind 匹配")
        base = assess_persisted_run(SOURCE)
        result = assess_persisted_run(SOURCE, report_review_runs=(stale,))
        states = result["report_states"]
        self.assertEqual(states["review_coverage"]["cross_section"],
                         "input_prepared_review_not_run")
        self.assertEqual(states["review_opinions"]["cross_section"]["issue_count"], 0)
        self.assertEqual(len(states["stale_report_review_runs"]), 1)
        row = states["stale_report_review_runs"][0]
        self.assertEqual(row["run_id"], stale.run_id)
        self.assertEqual(row["issue_count"], len(stale.issues))
        self.assertEqual(row["current_scope_version"], current.scope_version)
        self.assertIn("内容身份 scope_id", row["moved_identity_layers"])
        self.assertIn("内容版本", row["moved_identity_layers"])
        self.assertEqual(states["mechanical"], base["report_states"]["mechanical"])
        self.assertEqual(states["preview"], base["report_states"]["preview"])
        self.assertEqual(states["system_release"]["eligible"], False)

    def test_a_run_from_another_report_is_rejected_not_labelled_stale(self) -> None:
        """14. **反例**：另一份报告的同类 scope 不得被叫作「本报告的旧版」。"""
        from assurance.cited_controller import CitedControllerError

        run = _run(self.scopes["cross_section"])
        foreign = RX.ReportReviewRun.create(
            report_id="some_other_run_dir", scope_kind=run.scope_kind,
            scope_id=run.scope_id, scope_version=run.scope_version,
            bundle_id=run.bundle_id, model_policy_id=run.model_policy_id,
            producer_kind="independent_llm_review", outcome=run.outcome,
            requested_batch_ids=run.requested_batch_ids,
            completed_batch_ids=run.completed_batch_ids,
            declared_unit_ids=run.declared_unit_ids,
            requested_unit_ids=run.requested_unit_ids,
            covered_unit_ids=run.covered_unit_ids,
            reported_unit_ids=run.reported_unit_ids,
            excluded_unit_ids=run.excluded_unit_ids, records=run.records,
            issues=run.issues)
        self.assertNotEqual(foreign.report_id, run.report_id)
        self.assertTrue(foreign.trusted_as_assurance_input)
        # 除报告身份外逐字段相同 ⇒ 若控制器只看 scope 版本，这条会被当成有效审阅
        with self.assertRaises(CitedControllerError) as ctx:
            assess_persisted_run(SOURCE, report_review_runs=(foreign,))
        self.assertIn("另一份报告", str(ctx.exception))

    # --- 15. 硬错误优先 -----------------------------------------------------------
    def test_report_level_opinions_never_move_mechanical_or_release(self) -> None:
        """15. **反例**：三块内容全挂 `supported` 且算作真实审阅，机械/放行两格一动不动。"""
        base = assess_persisted_run(SOURCE)

        def all_supported(payload, body):
            return {"issues": [
                _issue_payload(key, category="supported", severity="none",
                               reason=f"离线回声：{key} 无问题")
                for key in payload["batch_unit_keys"]]}

        runs = [self._as_real(_run(scope, EchoClient(mutate=all_supported)))
                for scope in self.scopes.values()]
        self.assertTrue(all(r.trusted_as_assurance_input for r in runs))
        result = assess_persisted_run(SOURCE, report_review_runs=tuple(runs))
        states = result["report_states"]
        for run in runs:
            row = states["review_opinions"][run.scope_kind]
            self.assertEqual(row["issue_count"], len(run.issues))
            self.assertTrue(row["trusted_as_assurance_input"])
            self.assertTrue(states["review_coverage"][run.scope_kind].startswith("reviewed_by_"))
            # 三套单元计数各就各位：都进了请求、都收到回复、都被提了问题
            self.assertEqual(row["requested_unit_count"], len(run.requested_unit_ids))
            self.assertEqual(row["covered_unit_count"], len(run.covered_unit_ids))
            self.assertEqual(row["reported_unit_count"], len(run.reported_unit_ids))
            self.assertIn("covered_unit_count", row)
            self.assertIn("进了请求", row["coverage_meaning"])
            self.assertIn("收到可解析回复", row["coverage_meaning"])
            self.assertIn("互不推导", row["coverage_meaning"])
            self.assertIn("不能", row["supported_count_meaning"])
            self.assertIn("核实", row["supported_count_meaning"])
        self.assertEqual(states["mechanical"], base["report_states"]["mechanical"])
        self.assertEqual(states["preview"], base["report_states"]["preview"])
        self.assertEqual(states["system_release"]["eligible"], False)
        for sid, row in states["system_release"]["per_section"].items():
            self.assertEqual(
                row["system_review_state"],
                base["report_states"]["system_release"]["per_section"][sid]
                ["system_review_state"])
        for key, value in states["review_opinion_effect"].items():
            if key != "note":
                self.assertIs(value, False, f"审阅意见不得移动 {key}")
        self.assertEqual(len(states["mechanical"]["company"]["blocked_sentence_ids"]), 7)

    def _as_real(self, run: RX.ReportReviewRun) -> RX.ReportReviewRun:
        """把替身跑出来的运行**重贴**成真实产出者身份（只改这一格，其余身份全一致）。"""
        return RX.ReportReviewRun.create(
            report_id=run.report_id, scope_kind=run.scope_kind, scope_id=run.scope_id,
            scope_version=run.scope_version, bundle_id=run.bundle_id,
            model_policy_id=run.model_policy_id,
            producer_kind="independent_llm_review", outcome=run.outcome,
            requested_batch_ids=run.requested_batch_ids,
            completed_batch_ids=run.completed_batch_ids,
            declared_unit_ids=run.declared_unit_ids,
            requested_unit_ids=run.requested_unit_ids,
            covered_unit_ids=run.covered_unit_ids,
            reported_unit_ids=run.reported_unit_ids,
            excluded_unit_ids=run.excluded_unit_ids, records=run.records,
            issues=run.issues)

    # --- 16. prompt 资产对账 -----------------------------------------------------
    def test_prompt_asset_identity_is_reconciled_at_load(self) -> None:
        """16. 资产头与冻结常量不符（哪个方向都一样）即拒，且拒在**加载**。"""
        from llm import client as llm

        real = llm.load_prompt(RX.REPORT_REVIEW_PROMPT_ASSET)
        self.assertEqual(RX.load_report_review_prompt(), real)
        self.assertEqual(RX.CW.declared_prompt_identity(real),
                         (RX.REPORT_REVIEW_PROMPT_ASSET, RX.REPORT_REVIEW_PROMPT_REVISION))
        bad = [
            real.replace("revision irv-2", "revision irv-3", 1),
            real.replace(RX.REPORT_REVIEW_PROMPT_ASSET, "other_prompt", 1),
            "没有任何头部的提示词正文\n" + real,
        ]
        for index, text in enumerate(bad):
            with patch.object(llm, "load_prompt", return_value=text):
                with self.assertRaises(RX.ReportReviewError) as ctx:
                    RX.load_report_review_prompt()
                self.assertIn(ctx.exception.reason,
                              ("prompt_asset_identity_missing",
                               "prompt_asset_identity_mismatch"), f"case {index}")

    # --- 17. 事前预算门 ----------------------------------------------------------
    def test_budget_gate_blocks_the_whole_run_before_any_call(self) -> None:
        """17. 未授权 ⇒ 装不上强制门；已授权 ⇒ 归属到独立轴，第 N+1 批事前被拒。"""
        unapproved = RX.report_review_call_budget_policy(
            bounds=self.bounds, model="deepseek-v4-pro", approved=False,
            provenance="test: 未授权")
        with self.assertRaises(ValueError):
            LB.LLMCallBudget(unapproved, enforce=True)

        policy = RX.report_review_call_budget_policy(
            bounds=self.bounds, model="deepseek-v4-pro", approved=True,
            provenance="test: 已授权")
        RX.assert_budget_covers_bounds(policy, self.bounds)
        ledger = LB.LLMCallBudget(policy, enforce=True)
        limit = self.bounds["material_selectivity"]["batches"]
        for index in range(limit):
            attempt = ledger.reserve(
                call_id=f"c{index}", prompt_version=RX.REPORT_REVIEW_PROMPT_VERSION,
                model="deepseek-v4-pro", max_tokens=RX.REPORT_REVIEW_MAX_OUTPUT_TOKENS,
                section_id=f"{RX.REPORT_REVIEW_SCOPE_PREFIX}material_selectivity")
            ledger.settle(attempt, status=LB.STATUS_OK)
        self.assertEqual(ledger.axis_counts().get(RX.REPORT_REVIEW_BUDGET_AXIS), limit)
        with self.assertRaises(LB.LLMCallBudgetExceeded) as ctx:
            ledger.reserve(
                call_id="c-over", prompt_version=RX.REPORT_REVIEW_PROMPT_VERSION,
                model="deepseek-v4-pro", max_tokens=RX.REPORT_REVIEW_MAX_OUTPUT_TOKENS,
                section_id=f"{RX.REPORT_REVIEW_SCOPE_PREFIX}material_selectivity")
        self.assertEqual(ledger.refusals[-1]["axis"], RX.REPORT_REVIEW_BUDGET_AXIS)
        self.assertEqual(ctx.exception.limit, limit)

        # 上限低于结构上界不是「更严格」，而是把注定中途撞门的运行伪装成可运行
        tight = {kind: {**bound, "batches": bound["batches"] + 1}
                 for kind, bound in self.bounds.items()}
        with self.assertRaises(RX.ReportReviewError):
            RX.assert_budget_covers_bounds(policy, tight)

    # --- 18. 运行入口 -------------------------------------------------------------
    def test_run_entry_dry_run_fires_nothing_and_writes_nothing(self) -> None:
        """18. 缺省干跑：`llm_calls == 0`、一个文件都不写，且给出逐 scope 上限。"""
        scratch = Path(tempfile.mkdtemp(prefix="_tmp_m930_4_rr_entry_",
                                        dir=ROOT / "evaluation/results"))
        try:
            inputs = scratch / "report_review_inputs.json"
            result = assess_persisted_run(SOURCE)
            inputs.write_text(json.dumps(
                {"report_review_inputs": result["report_review_inputs"]},
                ensure_ascii=False), encoding="utf-8")
            out = scratch / "would_be_output"
            code = ENTRY.main(["--inputs", str(inputs), "--dry-run"])
            self.assertEqual(code, 0)
            self.assertFalse(out.exists())
            self.assertEqual([p.name for p in scratch.iterdir()],
                             ["report_review_inputs.json"], "干跑不得留下任何产物")
            # 干跑不接受 `--output`：说好不写就不写
            with self.assertRaises(SystemExit):
                ENTRY.main(["--inputs", str(inputs), "--output", str(out)])
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def test_run_entry_offline_stand_in_never_becomes_an_independent_review(self) -> None:
        """19. 断网下用离线替身走**真实入口**：账本零真实尝试，产出者仍是回声。

        入口在**第一请求之前**装强制预算门（未授权时构造期就抛），所以这条同时证明
        「未授权跑不起来」。替身不过 `llm.budget`（`suspended` 的纪律：替身探针不占已批准
        额度），因此 `ledger_attempt_count == 0` 而 `recorded_call_count == 批数`——两个数
        在摘要里分开写，读的人不会把「没真发」读成「没记账」。
        """
        scratch = Path(tempfile.mkdtemp(prefix="_tmp_m930_4_rr_entry_",
                                        dir=ROOT / "evaluation/results"))
        try:
            inputs = scratch / "report_review_inputs.json"
            result = assess_persisted_run(SOURCE)
            inputs.write_text(json.dumps(
                {"report_review_inputs": result["report_review_inputs"]},
                ensure_ascii=False), encoding="utf-8")
            out = scratch / "entry_output"
            model = "deepseek-v4-pro"

            # ① 未授权：连输出目录都不该被创建
            with self.assertRaises(SystemExit):
                ENTRY.main(["--inputs", str(inputs), "--output", str(out),
                            "--approved", "--provenance", "  "])
            self.assertFalse(out.exists())

            # ② 已授权 + 离线替身：全程不发请求，产出者身份由 client 推出。
            #    **断网是运行期真的断**，不是靠"模块没 import httpx"这种静态字样推出来的：
            #    任何一处真去连 socket 都会在这里炸，而替身路径不会。
            with patch.object(socket.socket, "connect",
                              side_effect=AssertionError("断网：不得发起真实网络连接")), \
                 patch.object(socket, "create_connection",
                              side_effect=AssertionError("断网：不得发起真实网络连接")):
                code = ENTRY.main(["--inputs", str(inputs), "--output", str(out),
                                   "--approved", "--model", model,
                                   "--provenance", "test: 离线替身入口回归"],
                                  client=EchoClient())
            self.assertEqual(code, 0)
            summary = json.loads((out / "report_review_entry.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["mode"], "real")
            self.assertEqual(summary["producer_kind"], "offline_diagnostic_echo")
            report = summary["report"]
            self.assertEqual(report["ledger_attempt_count"], 0)
            self.assertEqual(report["recorded_call_count"],
                             sum(row["completed_batch_count"] for row in report["scopes"]))
            self.assertTrue(report["all_reviewed"])
            self.assertIn("不得读成", report["call_count_meaning"])
            self.assertEqual(set(summary["required_caps"]), set(RR.REPORT_REVIEW_SCOPE_KINDS))
            written = sorted(p.name for p in out.iterdir())
            self.assertEqual(written,
                             ["report_review_entry.json"] + sorted(
                                 f"report_review_run__{kind}.json"
                                 for kind in RR.REPORT_REVIEW_SCOPE_KINDS))

            # ③ Controller 读回：记录如实、覆盖为「离线回声」、**不是**审阅输入
            runs = tuple(RX.load_report_review_run(out / name)
                         for name in written if name.startswith("report_review_run__"))
            states = assess_persisted_run(SOURCE, report_review_runs=runs)["report_states"]
            for run in runs:
                self.assertFalse(run.trusted_as_assurance_input)
                self.assertEqual(states["review_coverage"][run.scope_kind],
                                 "reviewed_by_offline_echo")
                self.assertFalse(
                    states["review_opinions"][run.scope_kind]["trusted_as_assurance_input"])
                self.assertEqual(states["review_opinions"][run.scope_kind]["issue_count"], 0)
            self.assertEqual(states["system_release"]["eligible"], False)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def test_run_entry_writes_a_failed_run_and_returns_non_zero(self) -> None:
        """20. **后批失败**：入口照样 create-only 落盘 failed run，并返回非零。"""
        scratch = Path(tempfile.mkdtemp(prefix="_tmp_m930_4_rr_entry_",
                                        dir=ROOT / "evaluation/results"))
        try:
            inputs = scratch / "report_review_inputs.json"
            result = assess_persisted_run(SOURCE)
            inputs.write_text(json.dumps(
                {"report_review_inputs": result["report_review_inputs"]},
                ensure_ascii=False), encoding="utf-8")
            out = scratch / "entry_output"
            # 第一块 scope 的第 1 批就失败：其余 scope 照跑，整体判失败。断网同样在运行期强制。
            with patch.object(socket.socket, "connect",
                              side_effect=AssertionError("断网：不得发起真实网络连接")), \
                 patch.object(socket, "create_connection",
                              side_effect=AssertionError("断网：不得发起真实网络连接")):
                code = ENTRY.main(["--inputs", str(inputs), "--output", str(out),
                                   "--approved", "--model", "deepseek-v4-pro",
                                   "--provenance", "test: 失败留痕回归"],
                                  client=FailingClient(fail_at=1))
            self.assertEqual(code, 1)
            summary = json.loads((out / "report_review_entry.json").read_text(encoding="utf-8"))
            self.assertFalse(summary["report"]["all_reviewed"])
            failed = [row for row in summary["report"]["scopes"]
                      if row["outcome"] == "failed"]
            self.assertEqual(len(failed), 1)
            self.assertEqual(failed[0]["completed_batch_count"], 0)
            self.assertEqual(failed[0]["failed_batch_count"], 1)
            self.assertGreater(failed[0]["excluded_unit_count"], 0)
            self.assertIsNotNone(failed[0]["failure"])
            # 落盘的 failed run **能读回来**，且 `excluded` 与声明单元不交
            run = RX.load_report_review_run(out / failed[0]["path"])
            self.assertEqual(run.outcome, "failed")
            self.assertEqual(run.covered_unit_ids, ())
            self.assertEqual(sorted(run.excluded_unit_ids), sorted(run.declared_unit_ids))
            self.assertEqual(len(run.records), len(run.completed_batch_ids))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def test_run_entry_real_client_disables_thinking_and_keeps_frozen_limits(self) -> None:
        """22. 真实入口**自己造**的那个客户端：显式关推理、上限不动、截断仍不算成功。

        这一条钉的是"调用前最后一道检查"：入口在真实模式下构造的是
        `RX.LlmReportReviewClient(model=..., thinking=...)`，而此前它是 `thinking=None`
        ——等于把「要不要推理」交给 provider 默认值。项目模型是推理模型、推理计入
        `output_tokens`，同一个模型、同一个 8192 上限已经在 M930-3 上真出过
        「额度用尽但零可见正文」。因此这里**从入口**造客户端，只在 `chat_with_usage`
        这一层拦截（离线，不发请求），逐项断言真正会发出去的那三个参数。

        未授权时必须是 **0 次调用**：预算门在构造期就抛，请求根本到不了 provider。
        """
        from llm import client as llm

        scratch = Path(tempfile.mkdtemp(prefix="_tmp_m930_4_rr_entry_",
                                        dir=ROOT / "evaluation/results"))
        try:
            inputs = scratch / "report_review_inputs.json"
            result = assess_persisted_run(SOURCE)
            inputs.write_text(json.dumps(
                {"report_review_inputs": result["report_review_inputs"]},
                ensure_ascii=False), encoding="utf-8")
            out = scratch / "entry_output"
            sent: list[dict] = []

            def fake_chat_with_usage(messages, **kwargs):
                sent.append({"messages": messages, **kwargs})
                return llm.LLMResponse(text='{"issues": []}', input_tokens=10,
                                       output_tokens=5, latency_ms=1,
                                       model="deepseek-v4-pro", call_id=f"probe-{len(sent)}",
                                       finish_reason="end_turn")

            # 开关本身先钉住：只在这条入口声明，且就是 provider 认的那个形状。
            self.assertEqual(ENTRY.REPORT_REVIEW_THINKING_DISABLED, {"type": "disabled"})

            # ⓪ 未授权：一次都不许调用
            with patch.object(llm, "chat_with_usage", side_effect=fake_chat_with_usage), \
                 patch.object(socket.socket, "connect",
                              side_effect=AssertionError("断网：不得发起真实网络连接")), \
                 patch.object(socket, "create_connection",
                              side_effect=AssertionError("断网：不得发起真实网络连接")):
                with self.assertRaises(SystemExit):
                    ENTRY.main(["--inputs", str(inputs), "--output", str(out),
                                "--approved", "--provenance", "   "])
                self.assertEqual(sent, [], "未授权时不得发出任何调用")
                self.assertFalse(out.exists(), "未授权时不得创建输出目录")

                # ⓪b 预算**预检**失败（已批准上限盖不住结构上界）：同样 0 次调用、不建目录。
                #     走的是入口里真的 `assert_budget_covers_bounds`，不是替身抛错。
                real_policy = RX.report_review_call_budget_policy

                def tight_policy(*, bounds, model, approved, provenance):
                    return real_policy(
                        bounds={kind: ({**bound, "batches": 5}
                                       if kind == "material_selectivity" else bound)
                                for kind, bound in bounds.items()},
                        model=model, approved=approved, provenance=provenance)

                with patch.object(RX, "report_review_call_budget_policy",
                                  side_effect=tight_policy):
                    with self.assertRaises(SystemExit):
                        ENTRY.main(["--inputs", str(inputs), "--output", str(out),
                                    "--approved", "--model", "deepseek-v4-pro",
                                    "--provenance", "test: 上限低于上界"])
                self.assertEqual(sent, [], "预检失败时不得发出任何调用")
                self.assertFalse(out.exists(), "预检失败时不得创建输出目录")

                # ① 已授权：入口自造客户端，只拦在 `chat_with_usage`
                code = ENTRY.main(["--inputs", str(inputs), "--output", str(out),
                                   "--approved", "--model", "deepseek-v4-pro",
                                   "--provenance", "test: 只读探针（离线拦截）"])
            self.assertEqual(code, 0)

            # ② 逐项核对真正会发出去的参数：关推理 / 输出上限 8192 / 截断不算成功
            self.assertEqual(len(sent), 8, "本批输入应为 1+1+6 = 8 批")
            for call in sent:
                self.assertEqual(call["thinking"], {"type": "disabled"})
                self.assertEqual(call["max_tokens"], 8192)
                self.assertIs(call["reject_truncated"], True)
                self.assertEqual(call["model"], "deepseek-v4-pro")
                self.assertEqual(call["prompt_version"], RX.REPORT_REVIEW_PROMPT_VERSION)
                self.assertEqual(call["system"], RX.load_report_review_prompt())
            # 客户端没有把开关再改回去（`run_report_review` 会按批校正 max_tokens，开关不该被动）
            self.assertEqual(ENTRY.REPORT_REVIEW_THINKING_DISABLED, {"type": "disabled"})

            summary = json.loads((out / "report_review_entry.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["model"], "deepseek-v4-pro")
            self.assertEqual(summary["required_caps"],
                             {"cross_section": 1, "financial_a2": 1, "material_selectivity": 6})
            self.assertEqual(sum(row["requested_batch_count"]
                                 for row in summary["report"]["scopes"]), 8)
            self.assertEqual(summary["producer_kind"], "independent_llm_review")
            self.assertTrue(summary["report"]["all_reviewed"])
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    # --- 21. 真实字节 ------------------------------------------------------------
    def test_planning_and_running_touch_no_source_byte(self) -> None:
        """21. 规划 + 替身跑完三块，源 run 目录逐文件摘要不变。"""
        before = _tree_digest(SOURCE)
        for scope in self.scopes.values():
            batches = RX.plan_report_review_batches(scope)
            RX.assert_within_capacity(scope, batches)
            RX.build_report_review_messages(batches[0])
            _run(scope)
        self.assertEqual(before, _tree_digest(SOURCE))
        for relative in ("assurance/report_reviewer.py", "scripts/run_m930_4_report_review.py"):
            source_text = (ROOT / relative).read_text(encoding="utf-8")
            for token in ("httpx", "requests", "urllib", "socket", "sqlite3", "subprocess"):
                self.assertNotIn(token, source_text, f"{relative} 不得依赖 {token}")


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
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReportReviewChainTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return _summary(result)


if __name__ == "__main__":
    raise SystemExit(0 if main()["failed"] == 0 else 1)

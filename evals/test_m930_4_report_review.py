"""Focused offline M930-4 tests for the report-level review **inputs**.

Run: python -m evals.test_m930_4_report_review
No model, network, research, database or historical-run write occurs.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from copy import copy, deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from assurance import report_review as RR
from assurance import schema as AS
from assurance.cited_controller import assess_persisted_run
from sections import cited_report as CRP
from sections import cited_writer as CW
from scripts.run_m930_4_assurance import main as run_assurance_main


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "evaluation/results/m930_3_cited_real_dual_v2_r1"
REPORT_ID = SOURCE.name


def _read(relative: str):
    return json.loads((SOURCE / relative).read_text(encoding="utf-8"))


def _facts(section_id: str, *, draft=None, balance=None) -> RR.SectionReviewFacts:
    base = SOURCE / section_id
    if draft is None:
        draft = CW.CitedProseDraft.from_dict(_read(f"{section_id}/cited_prose.json")["draft"])
    if balance is None and section_id == "financial":
        balance = _read(f"{section_id}/cited_balance_structure__fin_balance_structure.json")
    return RR.SectionReviewFacts(
        section_id=section_id, manifest=CW.CitedWriterInputManifest.from_dict(
            _read(f"{section_id}/cited_input_manifest.json")),
        draft=draft,
        # 盘上这份 `cited_report_version.json` 是**历史发布期**写成的（`crpp-4`），因此走
        # `from_persisted_dict`：按记录自己声明的发布期分派，而不是拿「当前常量」去卡它。
        # 历史 run 必须继续可读——口径升版不得把每一份旧 run 判成伪造。
        version=CRP.CitedReportVersion.from_persisted_dict(
            _read(f"{section_id}/cited_report_version.json")),
        balance=balance)


def _previews() -> dict[str, str]:
    return {sid: (SOURCE / sid / "cited_preview.md").read_text(encoding="utf-8")
            for sid in ("company", "financial")}


def _scopes(sections=None) -> dict[str, RR.ReportReviewScope]:
    sections = sections or [_facts("company"), _facts("financial")]
    built = RR.build_report_review_scopes(report_id=REPORT_ID, sections=sections,
                                          previews=_previews())
    return {scope.scope_kind: scope for scope in built}


def _rebuild(draft: CW.CitedProseDraft,
             subsections) -> CW.CitedProseDraft:
    """换掉小节后把内容派生 id 一并修正，得到一个**合法**的另一版草稿。

    直接 `dataclasses.replace` 过不了 `draft_id` 校验（先构造后派生），所以先复制、
    再改写、再按同一条派生规则补 id——「正文改一字」在测试里也必须改出一个**能被
    正常解码**的草稿，否则测的就不是版本失效，而是构造失败。
    """
    rebuilt = copy(draft)
    object.__setattr__(rebuilt, "subsections", tuple(subsections))
    object.__setattr__(rebuilt, "draft_id", CW.derive_draft_id(rebuilt))
    assert CW.derive_draft_id(rebuilt) == rebuilt.draft_id
    return rebuilt


def _rewrite_sentence(draft: CW.CitedProseDraft, index: int, new_text: str) -> CW.CitedProseDraft:
    """把第 index 句的文本换成一个字不同（其余字段与内容派生 id 一并修正）。"""
    seen = 0
    subsections = []
    for subsection in draft.subsections:
        paragraphs = []
        for paragraph in subsection.paragraphs:
            sentences = []
            for sentence in paragraph.sentences:
                sentences.append(replace(sentence, text=new_text)
                                 if seen == index else sentence)
                seen += 1
            paragraphs.append(replace(paragraph, sentences=tuple(sentences)))
        subsections.append(replace(subsection, paragraphs=tuple(paragraphs)))
    return _rebuild(draft, subsections)


def _a2_with_recomputed_fingerprint(balance: dict) -> dict:
    """改一条 A2 statement 后，按产物自己的规则重算指纹。

    真实链上 A2 指纹覆盖 `items[*]`，改一个字而不重算指纹会被 Controller 直接拒；
    所以「A2 改一字」必须是**合法**的另一版产物，而不是伪造的残骸。
    """
    mutated = deepcopy(balance)
    mutated["items"][0]["statement"] = str(mutated["items"][0]["statement"]) + "。"
    body = {k: v for k, v in mutated.items() if k != "fingerprint"}
    mutated["fingerprint"] = hashlib.sha256(json.dumps(
        body, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    return mutated


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


class ReportReviewScopeTests(unittest.TestCase):
    def test_scopes_are_inputs_only_and_never_claim_a_review(self) -> None:
        """1. 三块输入都建得出来，状态恒为未运行，且不产生新的模型调用。"""
        scopes = _scopes()
        self.assertEqual(set(scopes), set(RR.REPORT_REVIEW_SCOPE_KINDS))
        for scope in scopes.values():
            self.assertEqual(scope.state, "review_not_run")
            # 输入面声明的是**分批规则**，不是一个批数：逐 scope 的调用上限由
            # `plan_report_review_batches` 从这批规则推出，不由输入面自己写死。
            self.assertEqual(scope.batching_rules, RR.REPORT_REVIEW_BATCHING_RULES)
            self.assertEqual(scope.model_policy_id, RR.REPORT_REVIEW_MODEL_POLICY_ID)
            self.assertEqual(scope.prompt_version, AS.INDEPENDENT_REVIEWER_PROMPT_VERSION)
        result = assess_persisted_run(SOURCE)
        self.assertEqual(result["new_llm_calls"], 0)
        self.assertFalse(result["system_release_eligible"])
        self.assertEqual([s["scope_kind"] for s in result["report_review_scopes"]],
                         ["financial_a2", "cross_section", "material_selectivity"])
        for summary in result["report_review_scopes"]:
            self.assertEqual(summary["state"], "review_not_run")
        # 三个 scope 的输入是**输入**：保证没有任何意见被凭空聚合出来。
        opinions = result["report_states"]["review_opinions"]
        for kind in RR.REPORT_REVIEW_SCOPE_KINDS:
            self.assertFalse(opinions[kind]["trusted_as_assurance_input"])
            self.assertEqual(opinions[kind]["issue_count"], 0)

    def test_a2_identity_is_independent_of_the_prose_versions(self) -> None:
        """2. A2 有自己的内容身份，不等于任一 crpv_*。"""
        scopes = _scopes()
        prose = {f.version.report_version for f in
                 (_facts("company"), _facts("financial"))}
        for scope in scopes.values():
            self.assertEqual(scope.bundle.report_version, scope.scope_version)
            self.assertNotIn(scope.scope_version, prose)
            self.assertNotIn(scope.scope_version, scope.parent_report_versions)
        self.assertEqual(list(scopes["financial_a2"].parent_report_versions),
                         [f"{_facts('financial').version.report_version}"])
        self.assertEqual(set(scopes["cross_section"].parent_report_versions), prose)

    def test_bundle_binds_the_exact_payload_and_declares_isolation(self) -> None:
        """3. bundle 声明的可读内容 = 实际请求面；隔离项逐项声明。"""
        for scope in _scopes().values():
            self.assertEqual(scope.bundle.allowed_content_fingerprint,
                             scope.request.fingerprint)
            self.assertEqual(set(scope.bundle.excluded_context),
                             set(AS.REQUIRED_EXCLUDED_CONTEXT))
            self.assertEqual(len(scope.bundle.excluded_context),
                             len(AS.REQUIRED_EXCLUDED_CONTEXT))
            self.assertEqual(tuple(scope.covered_unit_ids),
                             tuple(scope.bundle.unit_keys()))

    def test_round_trip_survives_strict_decoding(self) -> None:
        """4. 落盘再读回后身份不变（字段集严格、未知字段拒绝）。"""
        for scope in _scopes().values():
            back = RR.ReportReviewScope.from_dict(json.loads(json.dumps(scope.to_dict())))
            self.assertEqual(back.scope_id, scope.scope_id)
            self.assertEqual(back.scope_version, scope.scope_version)
            self.assertEqual(back.bundle.bundle_id, scope.bundle.bundle_id)
            self.assertEqual(back.request.fingerprint, scope.request.fingerprint)
            self.assertEqual(back.identity_body(), scope.identity_body())
            with self.assertRaises(RR.ReportReviewError):
                RR.ReportReviewScope.from_dict({**scope.to_dict(), "extra": 1})

    def test_forged_identity_or_payload_fails_closed(self) -> None:
        """5. 伪造 scope_id / 篡改请求面 / 篡改 bundle 指纹，一律拒绝。"""
        scope = _scopes()["financial_a2"]
        with self.assertRaises(RR.ReportReviewError):
            RR.ReportReviewScope.from_dict({**scope.to_dict(), "scope_id": "rrs_forged"})
        payload = deepcopy(scope.request.payload)
        payload["review_question"] = payload["review_question"] + "（篡改）"
        with self.assertRaises(RR.ReportReviewError):
            RR.ReportReviewRequest(payload=payload, fingerprint=scope.request.fingerprint)
        with self.assertRaises(RR.ReportReviewError):
            RR.ReportReviewScope(
                schema_version=scope.schema_version, scope_id="rrs_forged",
                scope_kind=scope.scope_kind, scope_version=scope.scope_version,
                parent_report_versions=scope.parent_report_versions,
                covered_unit_ids=scope.covered_unit_ids,
                covered_sentence_ids=scope.covered_sentence_ids,
                state=scope.state, policy_version=scope.policy_version,
                batching_rules=scope.batching_rules, prompt_version=scope.prompt_version,
                model_policy_id=scope.model_policy_id, bundle=scope.bundle,
                request=scope.request)
        other = RR.ReportReviewRequest(payload={"probe": 1},
                                       fingerprint=RR._digest({"probe": 1}))
        with self.assertRaises(RR.ReportReviewError):  # bundle 与请求面不是同一件事
            RR.ReportReviewScope.create(
                scope_kind="financial_a2", scope_version=scope.scope_version,
                parent_report_versions=scope.parent_report_versions,
                covered_unit_ids=scope.covered_unit_ids,
                covered_sentence_ids=(), bundle=scope.bundle, request=other)

    def test_a2_content_change_invalidates_a2_and_cross_only(self) -> None:
        """6. A2 改一字 ⇒ A2 与跨节版本变；两节正文版本与选择性版本不动。"""
        before = _scopes()
        balance = _a2_with_recomputed_fingerprint(_facts("financial").balance)
        after = _scopes([_facts("company"), _facts("financial", balance=balance)])
        self.assertNotEqual(after["financial_a2"].scope_version,
                            before["financial_a2"].scope_version)
        self.assertNotEqual(after["cross_section"].scope_version,
                            before["cross_section"].scope_version)
        self.assertNotEqual(after["financial_a2"].scope_id, before["financial_a2"].scope_id)
        self.assertEqual(after["financial_a2"].parent_report_versions,
                         before["financial_a2"].parent_report_versions)
        self.assertEqual(after["cross_section"].parent_report_versions,
                         before["cross_section"].parent_report_versions)
        self.assertEqual(after["material_selectivity"].scope_version,
                         before["material_selectivity"].scope_version)

    def test_prose_change_invalidates_cross_section_but_not_a2(self) -> None:
        """7. 正文改一字 ⇒ 跨节版本变，A2 版本**不变**（独立失效轴）。"""
        before = _scopes()
        draft = _facts("financial").draft
        first = next(s for sub in draft.subsections for par in sub.paragraphs
                     for s in par.sentences)
        changed = _rewrite_sentence(draft, 0, first.text + "。")
        after = _scopes([_facts("company"),
                         _facts("financial", draft=changed)])
        self.assertNotEqual(after["cross_section"].scope_version,
                            before["cross_section"].scope_version)
        self.assertEqual(after["financial_a2"].scope_version,
                         before["financial_a2"].scope_version)
        self.assertEqual(after["financial_a2"].scope_id, before["financial_a2"].scope_id)

    def test_material_use_set_change_invalidates_selectivity(self) -> None:
        """8. 使用集改一条 ⇒ 选择性版本变。"""
        before = _scopes()
        draft = _facts("company").draft
        stripped = _rebuild(draft, [
            replace(sub, paragraphs=tuple(
                replace(par, sentences=tuple(replace(s, citations=())
                                              for s in par.sentences))
                for par in sub.paragraphs))
            for sub in draft.subsections])
        after = _scopes([_facts("company", draft=stripped), _facts("financial")])
        self.assertNotEqual(after["material_selectivity"].scope_version,
                            before["material_selectivity"].scope_version)
        self.assertNotEqual(after["material_selectivity"].scope_id,
                            before["material_selectivity"].scope_id)
        self.assertEqual(after["financial_a2"].scope_version,
                         before["financial_a2"].scope_version)

    def test_selectivity_covers_every_member_exactly_once(self) -> None:
        """9. 全清单成员都在，使用/未使用两集不交且并集完整，计数与清单一致。"""
        scope = _scopes()["material_selectivity"]
        payload = scope.request.payload
        self.assertEqual(len(scope.bundle.unit_inventory), 150)
        self.assertEqual(len(scope.bundle.unit_inventory), len(scope.bundle.excerpts))
        expected = {"company": 10, "financial": 21}
        total = 0
        for row in payload["sections"]:
            used = row["used_citation_keys"]
            unused = row["unused_citation_keys"]
            keys = [m["citation_key"] for m in row["members"]]
            self.assertEqual(len(keys), len(set(keys)))
            self.assertEqual(sorted(used + unused), sorted(keys))
            self.assertFalse(set(used) & set(unused))
            self.assertEqual(len(used), expected[row["section_id"]])
            self.assertEqual({m["citation_key"]: m["used_in_prose"]
                              for m in row["members"]},
                             {k: (k in set(used)) for k in keys})
            total += len(keys)
        self.assertEqual(total, 150)
        self.assertEqual(len(scope.covered_sentence_ids), 0)

    def test_source_run_untouched_and_output_is_create_only(self) -> None:
        """10. 源 run 逐字节不变；CLI 仍 create-only 且写三个文件；模块无网络/数据库依赖。"""
        before = _tree_digest(SOURCE)
        result = assess_persisted_run(SOURCE)
        self.assertEqual(before, _tree_digest(SOURCE))

        # 真写一次到**仓库内** `evaluation/results` 下的临时目录：产物必须落在被
        # create-only 纪律与 `.gitignore` 覆盖的地方，而不是靠系统 Temp 可写——那样这条
        # 回归在不可写的 Temp 上会变成环境失败，看起来却像代码失败。
        scratch_root = ROOT / "evaluation/results"
        scratch = Path(tempfile.mkdtemp(prefix="_tmp_m930_4_rr_test_", dir=scratch_root))
        try:
            out = scratch / "created"
            self.assertEqual(
                run_assurance_main(["--source-run", str(SOURCE), "--output", str(out)]), 0)
            self.assertEqual(sorted(p.name for p in out.iterdir()),
                             ["assurance_diagnostic.json", "readback.md",
                              "report_review_inputs.json"])
            readback = (out / "readback.md").read_text(encoding="utf-8")
            self.assertIn("真实调用申请", readback)
            self.assertIn("**未创建**", readback)
            # 未授权就是未授权：读回里必须写着「未发起任何调用」，且不出现放行措辞
            self.assertIn("本命令未发起任何调用", readback)
            self.assertNotIn("系统放行：`True`", readback)
            plan = json.loads((out / "report_review_inputs.json").read_text(encoding="utf-8"))
            self.assertEqual(set(plan["report_review_batch_plans"]),
                             set(RR.REPORT_REVIEW_SCOPE_KINDS))
            with self.assertRaises(SystemExit):
                run_assurance_main(["--source-run", str(SOURCE), "--output", str(out)])
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        self.assertEqual(before, _tree_digest(SOURCE))
        self.assertFalse(scratch.exists())

        source_text = (ROOT / "assurance/report_review.py").read_text(encoding="utf-8")
        for token in ("httpx", "requests", "urllib", "socket", "sqlite3", "subprocess"):
            self.assertNotIn(token, source_text, f"报告级审阅输入不得依赖 {token}")

    def test_prose_change_with_unchanged_citation_keys_still_invalidates(self) -> None:
        """11. **反例**：改一句正文、引用键一条不动 ⇒ 选择性版本仍必须变。

        这一条盯的是最容易漏的那种失效：审阅者判「遗漏有没有实质改变已写结论」靠的是
        成稿句段本身，所以「引用键集合没动」**不能**被当成「这份输入没变」。如果内容身份
        只覆盖成员与使用集，这句话一改，一份针对旧措辞的审阅会继续显得有效。
        """
        before = _scopes()["material_selectivity"]
        draft = _facts("company").draft
        first = next(s for sub in draft.subsections for par in sub.paragraphs
                     for s in par.sentences)
        changed = _rewrite_sentence(draft, 0, first.text + "，且未见其他重大不利变化。")
        after = _scopes([_facts("company", draft=changed), _facts("financial")])
        got = after["material_selectivity"]
        # 先证明「引用键这条轴确实没动」——否则这就不是那个反例
        before_rows = {row["section_id"]: row for row in before.request.payload["sections"]}
        after_rows = {row["section_id"]: row for row in got.request.payload["sections"]}
        self.assertEqual(before_rows["company"]["used_citation_keys"],
                         after_rows["company"]["used_citation_keys"])
        self.assertEqual([m["citation_key"] for m in before_rows["company"]["members"]],
                         [m["citation_key"] for m in after_rows["company"]["members"]])
        self.assertNotEqual(got.scope_version, before.scope_version)
        self.assertNotEqual(got.scope_id, before.scope_id)
        # 与这句正文无关的轴不受牵连：财务 A2 的版本不动
        self.assertEqual(after["financial_a2"].scope_version,
                         _scopes()["financial_a2"].scope_version)

    def test_cross_section_is_given_the_evidence_it_is_asked_about(self) -> None:
        """12. 跨节审阅只能就**给它的**材料发问：两节被引原文与 A2 附注必须在请求面里。

        「一节的说法是否被另一节的材料推翻」需要另一节材料的字节；要求模型判断它没看到的
        证据是无效提问。这条同时盯住反向：问题措辞必须明说未列出的材料不得作为依据。
        """
        scope = _scopes()["cross_section"]
        payload = scope.request.payload
        rows = {row["section_id"]: row for row in payload["sections"]}
        self.assertEqual(set(rows), {"company", "financial"})
        for facts in (_facts("company"), _facts("financial")):
            cited = list(facts.cited_keys())
            self.assertTrue(cited)
            got = rows[facts.section_id]["cited_sources"]
            self.assertEqual([r["citation_id"] for r in got], cited,
                             f"{facts.section_id} 的被引原文没有按同一顺序全部进请求面")
            for row in got:
                self.assertTrue(str(row["text"]).strip())
                self.assertTrue(str(row["locator"]).strip())
        notes = payload["a2_notes"]
        self.assertTrue(notes, "跨节审阅要判「与财务 A2 是否矛盾」，A2 附注必须一并给出")
        self.assertEqual([n["note_id"] for n in notes],
                         [n.get("note_id") for n in (_facts("financial").balance["notes"])])
        for note in notes:
            self.assertIn("detail", note)
        # 逐节引用键也必须在：只有正文不足以做跨节对账
        for sid, row in rows.items():
            self.assertEqual(row["sentence_ids"],
                             list(_facts(sid).sentence_ids()))
        question = payload["review_question"]
        self.assertIn("cited_sources", question)
        self.assertIn("不得", question)
        self.assertIn("不足以判断", question)
        # 「给了」的含义是**模型能在请求里读到**：题面与原文都在同一个 payload 里，
        # 而 payload 就是发给模型的全部内容（`build_report_review_messages` 只发它）。
        # `bundle.excerpts` 是单元级的读视图，不是模型输入；此处不把它当原文载体。
        blob = json.dumps(payload, ensure_ascii=False)
        for row in rows.values():
            for source in row["cited_sources"]:
                needle = str(source["text"])[:40]
                self.assertIn(needle, blob,
                              f"{source['citation_id']} 的原文没有出现在请求面里")
        self.assertEqual(scope.bundle.allowed_content_fingerprint,
                         scope.request.fingerprint)
        self.assertEqual([r.unit_kind for r in scope.bundle.unit_inventory],
                         ["section", "section", "table"])


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
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReportReviewScopeTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return _summary(result)


if __name__ == "__main__":
    raise SystemExit(0 if main()["failed"] == 0 else 1)

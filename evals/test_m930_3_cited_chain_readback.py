"""Eval: 写作运行目录的**离线四段对账**（`ccrb-1`）——清单 → 句子引用 → 核对 → 审阅输入。

用法: python -m evals.test_m930_3_cited_chain_readback

缘起是真实 r2（call_id `5856a5b820a14b2ca50bd3e31924264d`）那一轮：运行目录里只落了两份产物，
既没有精确输入清单，也没有逐句核对与审阅输入。于是「这一句引的是哪份材料」「核对看的是不是
这份正文」「审阅拿到的句子集合是不是这 27 句」三句话**都无法从盘上的字节回答**，那一轮因此
只能叫**离线派生诊断**。

本模块把这三句话变成可执行的检查，逐条证明：

1. **四段齐备时逐段对账**：清单（`from_dict` 重算身份）／草稿（`from_dict` 重算身份）／
   核对报告（绑的草稿与清单必须与前一环**同一个**、覆盖的句子集合必须逐条相等）／审阅
   （绑的草稿与清单同上，「审阅**会**看到什么」由**现有构建口**重建一次来核，而不是信文件里
   的一句话）。
2. **逐句四段对照**：一句一行，四个对象在这一句上分别是什么；某一环漏了这句就写在这一行上。
3. **缺件就是缺件**：任何一环没落盘 ⇒ 记「本轮不可判」并列出缺的是哪个文件；**不**拿别轮产物
   顶替、**不**按请求面拼一份「像清单的东西」。r2 的目录就是这一条的正例。
4. **留存簿在场时再对一层**：把留存的可见回复**重新走一遍**真实解析口，要求得到**同一个**
   `draft_id`——这是「盘上的正文确实由留存的那次调用产出」唯一能被证明的形式。
5. **篡改必被发现**：换掉草稿、换掉清单、换掉审阅的句子集合，逐条报出问题。

夹具复用 `test_m930_3_cited_writer` 的真实 Pack / 真实材料上下文 / 真实解析与核对入口；
**不调 LLM**（写作与审阅都用替身）、不联网、不写库；写盘只写进 `TemporaryDirectory`。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import test_m930_3_cited_writer as W        # noqa: E402
from sections import cited_chain_readback as CCRB      # noqa: E402
from sections import cited_call_journal as CCJ         # noqa: E402
from sections import cited_review as CR                # noqa: E402
from sections import cited_writer as CW                # noqa: E402
from sections import sentence_check as SC              # noqa: E402


class _StubProseClient:
    """写作侧替身：回放一段真实形状的返回。"""

    model = "stub-model"
    thinking = {"type": "disabled"}

    def __init__(self, *, text: str) -> None:
        self.text = text

    def compose(self, *, messages, system, prompt_version, model_policy):  # noqa: ANN001
        return CW.CitedProseResult(
            text=self.text, call_id="call-stub", model=self.model,
            prompt_version=prompt_version, status="ok", input_tokens=11,
            output_tokens=22, latency_ms=33, finish_reason="end_turn", error="")


class _EchoReviewClient:
    """审阅侧替身：**逐句**回一条「没问题」，好让审阅的逐句覆盖等式真的被走到。

    它**不具备**任何独立语义判断——产出者身份由 `review_producer_kind_of` 如实推成
    `offline_diagnostic_echo`，不得被读成「有人独立读过这段正文」。每句的 `citation_id`
    取该句自己的第一个引用（审阅意见必须挂在该句真正引用的键上，这是链上的硬约束）。
    """

    def __init__(self, *, draft) -> None:  # noqa: ANN001
        self._draft = draft
        self.calls: list[dict] = []

    def review(self, *, messages, system, prompt_version, model_policy):  # noqa: ANN001
        self.calls.append({"prompt_version": prompt_version, "model_policy": model_policy})
        issues = [{"sentence_id": s.sentence_id, "citation_id": s.citations[0],
                   "category": "supported", "severity": "none", "blocking": False,
                   "reason": "回放替身：未对这句做独立语义判断"}
                  for s in self._draft.sentences()]
        return CR.CitedReviewResult(
            text=json.dumps({"issues": issues}, ensure_ascii=False),
            call_id="call-review-stub", model="stub-reviewer",
            prompt_version=prompt_version, status="ok")


def _write(path: Path, body) -> None:  # noqa: ANN001
    path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")


def _build_run(run_dir: Path, *, with_journal: bool = True, with_review: bool = True) -> dict:
    """把一次「模拟的」写作运行落成一个真实目录：四段（+ 留存簿）都由**真实入口**产出。"""
    manifest = W._input_manifest()
    reply = W._draft_payload(manifest=manifest, citations_a=("m01",), citations_b=("m02",))
    messages, _system = CW.build_cited_prose_messages(manifest=manifest, system="sys")

    journal = CCJ.journal_for(run_dir) if with_journal else None
    if journal is not None:
        journal.record_input(section_id=manifest.section_id, manifest=manifest,
                             prompt_version=CW.CITED_WRITER_PROMPT_VERSION,
                             model_policy="stub", request_face=messages[0]["content"])
    outcome = CW.write_cited_section(manifest=manifest, client=_StubProseClient(text=reply),
                                     journal=journal)
    draft = outcome.draft
    checks = SC.check_cited_prose(draft=draft, manifest=manifest)

    _write(run_dir / "cited_input_manifest.json", manifest.to_dict())
    _write(run_dir / "cited_prose.json", outcome.to_dict())
    _write(run_dir / "sentence_checks.json", checks.to_dict())
    built = {"manifest": manifest, "draft": draft, "checks": checks, "review": None,
             "reply": reply}
    if with_review:
        # 不传 `check_report`：回声替身不做硬错误感知，传进去只会让「supported」撞上
        # `_check_hard_errors_not_overridden`，那是夹具的限制造出的假问题，不是链的问题。
        review = CR.review_cited_prose(
            draft=draft, manifest=manifest, client=_EchoReviewClient(draft=draft),
            system="审阅系统提示（替身）",
            report_version="crpv-test", report_id="crp_test")
        _write(run_dir / "review_issues.json", review.to_dict())
        built["review"] = review
    return built


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

    # ==================================================== §1 四段齐备：逐段对账
    details.append("## §1 四段齐备：清单 → 句子引用 → 核对 → 审阅输入")
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        run_dir.mkdir()
        built = _build_run(run_dir)
        readback = CCRB.read_chain(run_dir)
        check(readback.complete and not readback.problems,
              f"**正例**：四段齐备且互相自洽（跨段问题 {list(readback.problems)}）")
        link = readback.link("input_manifest")
        check(link is not None and link.present and not link.problems
              and link.identity == built["manifest"].manifest_id,
              "⇒ 清单段：身份由 `from_dict` **重算**得到，与构造时的 `manifest_id` 相同")
        link = readback.link("sentence_citations")
        check(link is not None and not link.problems
              and link.identity == built["draft"].draft_id,
              "⇒ 句子引用段：草稿身份同样重算得到，且逐句引用键都落在本清单里")
        link = readback.link("sentence_checks")
        check(link is not None and not link.problems
              and link.identity == built["checks"].report_id,
              "⇒ 核对段：报告绑的草稿/清单与前一环**同一个**，覆盖的句子集合逐条相等")
        link = readback.link("review_input")
        check(link is not None and not link.problems
              and link.identity == built["review"].report_id,
              "⇒ 审阅段：绑的草稿与清单同上，且**重建**的审阅请求逐句（ID、文本）与草稿一致")
        check(link.detail.startswith("审阅 `offline_diagnostic_echo`"),
              "⇒ 替身产出的意见被**如实**标为回声，不得冒充独立 LLM 审阅")
        check(len(readback.sentences) == len(built["draft"].sentence_ids()),
              f"逐句四段对照覆盖全部句子（实际 {len(readback.sentences)} 行）")
        check(all(row.citations for row in readback.sentences),
              "⇒ 每一句在对照表里都带自己的引用键（引用逐句可核，不是节级声明）")
        check(all(row.check_verdicts for row in readback.sentences),
              "⇒ 每一句都带着逐句核对的结论（没有「核对漏了这句」的行）")
        check(all(len(row.review_issue_ids) == 1 for row in readback.sentences),
              "⇒ 审阅的逐句表态在对照表里**逐句**可见（回声替身对每句各回一条，"
              "「没表态」与「表态没问题」在行上分得开）")
        details.append("NOTE §1：四段自洽只说明「四个对象是同一件事」，与内容成立无关。")

        # ================================================ §2 留存簿：重放得到同一份草稿
        details.append("## §2 留存簿在场：留存的可见回复重放得到**同一个**成品")
        journal_link = readback.link("call_journal")
        check(journal_link is not None and journal_link.present and not journal_link.problems,
              "⇒ 留存簿段的清单身份/材料引用键与清单一致，且留存的可见回复**重放**得到同一 "
              "draft_id")
        check(CCJ.assert_retention_shape(
            json.loads((run_dir / CCJ.CITED_CALL_JOURNAL_FILENAME).read_text(
                encoding="utf-8"))) == (),
              "⇒ 留存簿自身形状自洽（`publishable` / `hidden_reasoning_persisted` 恒假）")
        check(journal_link.detail.startswith(f"留存 `{CCJ.CITED_CALL_JOURNAL_VERSION}`"),
              "⇒ 留存簿段自带版本与 call_id（可回查到具体那一次调用）")

        # ================================================ §3 篡改必被发现（逐段反例）
        details.append("## §3 篡改必被发现：换草稿 / 换清单 / 换句子集合")
        prose_path = run_dir / "cited_prose.json"
        intact_prose = prose_path.read_text(encoding="utf-8")
        tampered = json.loads(intact_prose)
        paras = tampered["draft"]["subsections"][0]["paragraphs"]
        paras[0]["sentences"] = paras[0]["sentences"][1:]        # 悄悄少一句，ID 不动
        _write(prose_path, tampered)
        bad = CCRB.read_chain(run_dir)
        check(not bad.complete and bad.link("sentence_citations").problems,
              "**反例 1**：草稿被改写而 `draft_id` 未动 ⇒ 该段报「读不回」"
              "（身份是内容寻址，改了内容就重算不出原来的 id）")
        check(bool(bad.problems),
              f"⇒ 跨段问题逐条落在 `problems` 里，读回侧不看结论也指得出是哪两段对不上"
              f"（{list(bad.problems)[:1]}）")

        manifest_path = run_dir / "cited_input_manifest.json"
        intact_manifest = manifest_path.read_text(encoding="utf-8")
        _task, _authority = W._task_and_authority()
        other = W._input_manifest(authority=_authority,
                                  facts=W._scan_facts(_authority, _task))
        check(other.manifest_id != built["manifest"].manifest_id,
              "⇒ 夹具前提：换一批事实会**真的**换掉清单身份（不是同一个 id）")
        _write(manifest_path, other.to_dict())
        bad = CCRB.read_chain(run_dir)
        check(not bad.complete
              and any("不是本目录的清单" in p for p in bad.problems),
              "**反例 2**：清单被换成另一次的 ⇒ 逐段报出「绑的清单不是本目录的清单」"
              "（缺件与「换了另一份」都逃不掉）")
        manifest_path.write_text(intact_manifest, encoding="utf-8")
        prose_path.write_text(intact_prose, encoding="utf-8")
        check(CCRB.read_chain(run_dir).complete,
              "⇒ 前后两次篡改都被还原，说明上面报的问题确实来自篡改而不是夹具本身")

    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        run_dir.mkdir()
        _build_run(run_dir, with_journal=False)
        review_path = run_dir / "review_issues.json"
        review_body = json.loads(review_path.read_text(encoding="utf-8"))
        review_body["sentence_ids"] = review_body["sentence_ids"][:-1]     # 审阅少看一句
        _write(review_path, review_body)
        bad = CCRB.read_chain(run_dir)
        check(not bad.complete
              and any("审阅的句子集合与草稿不等" in p for p in bad.problems),
              "**反例 3**：审阅的句子集合比草稿少一句 ⇒ 报出「审阅看的不是这些句」")
        check(all(len(row.review_issue_ids) == 1 for row in bad.sentences),
              "⇒ 集合被换掉只影响**声明**：逐句意见本身没动（读回不替审阅改口）")

    # ==================================================== §4 缺件一律「本轮不可判」
    details.append("## §4 缺件：记「本轮不可判」，**不**拿别轮产物顶替")
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "real-r2-like"
        empty.mkdir()
        (empty / "cited_call_ledger.json").write_text("{}", encoding="utf-8")
        r2like = CCRB.read_chain(empty)
        check(not r2like.complete and all(not x.present for x in r2like.links),
              "**正例**（r2 的形状）：五段全缺 ⇒ 逐段记「本轮不可判」")
        check(all(x.detail == CCRB.LINK_UNJUDGED for x in r2like.links if not x.present),
              "⇒ 每段都写明缺的是哪个文件（不靠「没写」来省略）")
        check(r2like.unjudged_fields == CCRB.HISTORICAL_UNFILLABLE,
              "⇒ 列出**本轮不可判**的历史字段清单（缺件时不得声称这些字段对得上）")
        check(not r2like.problems,
              "⇒ 「缺件」本身不是问题项：它是结论，不该被读成「对账失败」")
        check(not r2like.sentences,
              "⇒ 正文本身缺席时**不凭空造句行**（没有草稿就没有逐句对照）")

        missing_dir = CCRB.read_chain(Path(tmp) / "does-not-exist")
        check(not missing_dir.complete and missing_dir.problems,
              "**反例**：目录根本不存在 ⇒ 单独报出来（与「目录在、文件缺」不是同一件事）")
        check(not missing_dir.unjudged_fields,
              "⇒ 目录不存在时不冒充「历史字段不可判」：那一条只对**真实跑过的目录**说")

    # ==================================================== §5 渲染与边界
    details.append("## §5 人读页与边界")
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp) / "run"
        run_dir.mkdir()
        _build_run(run_dir)
        readback = CCRB.read_chain(run_dir)
        md = CCRB.render_chain_markdown(readback)
        check("清单 → 句子引用 → 核对 → 审阅输入" in md
              and "不是验收门" in md and "不判合格" in md,
              "⇒ 页首写明这是**对账**、不是验收门（四段自洽 ≠ 内容成立）")
        check(md.count("| `s0") >= len(readback.sentences),
              f"⇒ 逐句对照表把每一句都摆出来（{len(readback.sentences)} 句）")
        body = readback.to_dict()
        check(body["publishable"] is False
              and body["boundary"]["no_substitution"]
              and body["boundary"]["not_a_gate"],
              "⇒ 产物恒标**不可发布**，并写明「缺件不顶替」「本页不是门」两条边界")
        check(body["chain_readback_version"] == CCRB.CITED_CHAIN_READBACK_VERSION,
              f"⇒ 读回规则自带版本号 `{body['chain_readback_version']}`")
        check(json.dumps(body, ensure_ascii=False, sort_keys=True)
              == json.dumps(CCRB.read_chain(run_dir).to_dict(), ensure_ascii=False,
                            sort_keys=True),
              "同一份目录两次读回逐字相同（可回查、可对账）")

    details.append(
        "NOTE 本模块只证明**四段是不是同一件事**；正文是否合格、结论是否站得住，"
        "仍须独立审阅与人工接受，本模块不做、也不宣称做过。")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

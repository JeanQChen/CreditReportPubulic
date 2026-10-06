"""一次写作运行目录的**离线**四段对账（`ccrb-1`）：清单 → 句子引用 → 核对 → 审阅输入。

存在的理由是一次具体的缺口：真实 r2 那一轮在运行目录里**只有**两份产物，既没有精确输入清单，
也没有逐句核对与审阅输入，于是「这一句引的是哪份材料」「核对看的是不是这份正文」「审阅拿到的
句子集合是不是这 27 句」三句话**都无法从盘上的字节回答**。本模块把这三句话变成可执行的检查，
下一轮真实运行一落盘就能立刻回答。

它**只读盘上已有的字节**，并且逐环都要穿过**现有的读回口**，不另写一套解析：

1. `cited_input_manifest.json` → :meth:`CitedWriterInputManifest.from_dict`（校验内容身份）；
2. `cited_prose.json` → :meth:`CitedProseDraft.from_dict`（校验草稿身份），逐句引用键必须落在
   第 1 环的清单键集合里；
3. `sentence_checks.json` → :meth:`SC.SentenceCheckReport.from_dict`，它的 `draft_id` /
   `input_manifest_id` 必须与第 1、2 环**同一个**，且逐句记录覆盖的句子集合与草稿**逐条相等**；
4. `review_issues.json` → `CitedReviewOutcome` 的（反序列化视图），其 `draft_id` /
   `input_manifest_id` / `sentence_ids` 必须与前三环对得上；并**重建**一次审阅请求
   （:func:`CR.build_cited_review_request`），确认审阅**会**看到的句子与引用与草稿逐句一致。

另外，若 `cited_call_journal.json` 在场，把留存簿里那次调用**发出去的**清单身份与草稿身份也对上，
并把留存的可见回复**重新走一遍**真实解析口，要求得到**同一个** `draft_id`——这一条是「盘上的
正文确实由留存的那次调用产出」这句话在本模块里唯一能被证明的形式。

三条边界：

* **缺文件就是缺文件。** 任何一环缺失，本模块记「本轮不可判」并**明确列出缺的是哪一个文件**，
  绝不拿另一轮的产物顶替、也绝不按请求面拼一份「像清单的东西」。真实 r2 就是这种情况。
* **不判合格。** 四段对账通过只说明「四个对象是同一件事」，与「正文写得好不好」「能不能发布」
  无关；后者是独立审阅与人工接受的事。
* **不写盘、不联网、不调模型。**
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from sections import cited_review as CR
from sections import cited_writer as CW
from sections import sentence_check as SC


#: 读回规则版本（换判据或换产物形状就换号）。
CITED_CHAIN_READBACK_VERSION = "ccrb-1"

#: 四段各自由哪个文件承载（固定文件名：读回侧按名字找，不靠通配）。
CHAIN_LINK_FILES = (
    ("input_manifest", "cited_input_manifest.json"),
    ("sentence_citations", "cited_prose.json"),
    ("sentence_checks", "sentence_checks.json"),
    ("review_input", "review_issues.json"),
)

#: 留存簿文件名（可选第五环；在场才查）。
CHAIN_JOURNAL_FILE = "cited_call_journal.json"

#: 缺件时逐环要说的话。**不**因为缺件把整份读回判失败：缺件本身就是结论。
LINK_UNJUDGED = "本轮不可判（文件不在运行目录里）"

#: 「本轮不可判」的历史字段清单：r2 那一轮缺的正是这些。它们**不能**从别处补。
HISTORICAL_UNFILLABLE = (
    "manifest_id", "manifest_fingerprint", "material_manifest_id", "material_ref",
    "reading_view_fingerprint", "document_id", "structured_view", "content_qualification",
)


@dataclass(frozen=True)
class ChainLink:
    """一段的在场与身份对账结果。"""

    name: str
    filename: str
    present: bool
    identity: str = ""
    detail: str = ""
    problems: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"name": self.name, "filename": self.filename, "present": bool(self.present),
                "identity": self.identity, "detail": self.detail,
                "problems": list(self.problems)}


@dataclass(frozen=True)
class ChainSentenceRow:
    """**逐句**把四段摆在一行上：这一句在各段里分别是什么。"""

    sentence_id: str
    subsection_id: str
    paragraph_id: str
    text: str
    citations: tuple[str, ...] = ()
    check_kinds: tuple[str, ...] = ()
    check_verdicts: tuple[str, ...] = ()
    review_issue_ids: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {"sentence_id": self.sentence_id, "subsection_id": self.subsection_id,
                "paragraph_id": self.paragraph_id, "text": self.text,
                "citations": list(self.citations),
                "check_kinds": list(self.check_kinds),
                "check_verdicts": list(self.check_verdicts),
                "review_issue_ids": list(self.review_issue_ids),
                "problems": list(self.problems)}


@dataclass(frozen=True)
class ChainReadback:
    """一次运行的离线四段对账结果（可落盘、可回查）。"""

    run_dir: str
    links: tuple[ChainLink, ...] = ()
    sentences: tuple[ChainSentenceRow, ...] = ()
    problems: tuple[str, ...] = ()
    unjudged_fields: tuple[str, ...] = ()
    version: str = CITED_CHAIN_READBACK_VERSION

    @property
    def complete(self) -> bool:
        """四段**全部在场且无问题**才叫完成。缺件不算完成、也不算失败——它叫「不可判」。"""
        return all(link.present and not link.problems for link in self.links)

    def link(self, name: str) -> ChainLink | None:
        return next((x for x in self.links if x.name == name), None)

    def to_dict(self) -> dict:
        return {
            "chain_readback_version": self.version,
            "publishable": False,
            "run_dir": self.run_dir,
            "complete": self.complete,
            "boundary": {
                "what_this_is": ("盘上四份产物的**离线**对账：它们是不是同一件事。"
                                 "它不判正文好坏、不判合格、不代替独立审阅与人工接受。"),
                "no_substitution": ("缺件一律记「本轮不可判」，不拿另一轮产物顶替，"
                                    "也不按请求面拼一份清单。"),
                "not_a_gate": ("本页不构成任何验收门：四段自洽 ≠ 内容成立。"),
            },
            "links": [link.to_dict() for link in self.links],
            "sentences": [row.to_dict() for row in self.sentences],
            "problems": list(self.problems),
            "unjudged_fields": list(self.unjudged_fields),
        }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _material_keys(manifest: CW.CitedWriterInputManifest) -> tuple[str, ...]:
    return tuple(str(m.citation_key) for m in manifest.materials)


def _fact_keys(manifest: CW.CitedWriterInputManifest) -> tuple[str, ...]:
    return tuple(str(f.citation_key) for f in manifest.facts)


def _link_input_manifest(run_dir: Path, problems: list[str]) -> tuple[
        ChainLink, CW.CitedWriterInputManifest | None]:
    path = run_dir / "cited_input_manifest.json"
    if not path.exists():
        return (ChainLink(name="input_manifest", filename=path.name, present=False,
                          detail=LINK_UNJUDGED), None)
    own: list[str] = []
    try:
        manifest = CW.CitedWriterInputManifest.from_dict(_load_json(path))
    except Exception as exc:  # noqa: BLE001 - 读回侧要把「读不出来」如实记下来
        own.append(f"读不回 CitedWriterInputManifest：{type(exc).__name__}: {exc}")
        problems.extend(own)
        return (ChainLink(name="input_manifest", filename=path.name, present=True,
                          detail="在场但读不回", problems=tuple(own)), None)
    return (ChainLink(name="input_manifest", filename=path.name, present=True,
                      identity=manifest.manifest_id,
                      detail=(f"清单 `{manifest.manifest_id[:12]}…`｜材料 "
                              f"{len(manifest.materials)} 份、事实 {len(manifest.facts)} 条｜"
                              f"小节 {len(manifest.subsections)} 个")), manifest)


def _link_sentence_citations(run_dir: Path, manifest, problems: list[str]) -> tuple[
        ChainLink, CW.CitedProseDraft | None, Mapping[str, Any] | None]:
    path = run_dir / "cited_prose.json"
    if not path.exists():
        return (ChainLink(name="sentence_citations", filename=path.name, present=False,
                          detail=LINK_UNJUDGED), None, None)
    raw = _load_json(path)
    own: list[str] = []
    draft_payload = raw.get("draft") if isinstance(raw, Mapping) else None
    if draft_payload is None:
        own.append("cited_prose.json 里没有 `draft` 段：这份产物不是 CitedProseOutcome 的形状")
        problems.extend(own)
        return (ChainLink(name="sentence_citations", filename=path.name, present=True,
                          detail="在场但形状不符", problems=tuple(own)), None, raw)
    try:
        draft = CW.CitedProseDraft.from_dict(draft_payload)
    except Exception as exc:  # noqa: BLE001
        own.append(f"读不回 CitedProseDraft：{type(exc).__name__}: {exc}")
        problems.extend(own)
        return (ChainLink(name="sentence_citations", filename=path.name, present=True,
                          detail="在场但读不回", problems=tuple(own)), None, raw)
    if manifest is not None and draft.input_manifest_id != manifest.manifest_id:
        own.append(f"草稿绑的清单 `{draft.input_manifest_id}` 不是本次清单 "
                   f"`{manifest.manifest_id}`：两段说的不是同一次写作")
    sentences = [s for sub in draft.subsections for para in sub.paragraphs
                 for s in para.sentences]
    if manifest is not None:
        known = set(_material_keys(manifest)) | set(_fact_keys(manifest))
        stray = sorted({c for s in sentences for c in s.citations if c not in known})
        if stray:
            own.append(f"有句引用了不在本清单里的键 {stray}")
    problems.extend(own)
    return (ChainLink(name="sentence_citations", filename=path.name, present=True,
                      identity=draft.draft_id,
                      detail=(f"草稿 `{draft.draft_id[:12]}…`｜小节 {len(draft.subsections)} 个、"
                              f"句子 {len(sentences)} 句｜缺口 {len(draft.gaps)} 条、"
                              f"补件 {len(draft.follow_up_needs)} 条"),
                      problems=tuple(own)), draft, raw)


def _link_sentence_checks(run_dir: Path, manifest, draft, problems: list[str]) -> tuple[
        ChainLink, SC.SentenceCheckReport | None]:
    path = run_dir / "sentence_checks.json"
    if not path.exists():
        return (ChainLink(name="sentence_checks", filename=path.name, present=False,
                          detail=LINK_UNJUDGED), None)
    own: list[str] = []
    try:
        report = SC.SentenceCheckReport.from_dict(_load_json(path))
    except Exception as exc:  # noqa: BLE001
        own.append(f"读不回 SentenceCheckReport：{type(exc).__name__}: {exc}")
        problems.extend(own)
        return (ChainLink(name="sentence_checks", filename=path.name, present=True,
                          detail="在场但读不回", problems=tuple(own)), None)
    if draft is not None:
        if report.draft_id != draft.draft_id:
            own.append(f"核对报告绑的草稿 `{report.draft_id}` 不是本目录的草稿 "
                       f"`{draft.draft_id}`：核对看的是另一份正文")
        checked = {r.sentence_id for r in report.records}
        drafted = {s.sentence_id for sub in draft.subsections for para in sub.paragraphs
                   for s in para.sentences}
        if checked != drafted:
            own.append(f"核对覆盖的句子集合与草稿不等：只在核对里 {sorted(checked - drafted)}，"
                       f"只在草稿里 {sorted(drafted - checked)}")
        if report.sentence_count != len(drafted):
            own.append(f"核对声明的 sentence_count={report.sentence_count}，"
                       f"草稿实有 {len(drafted)} 句")
    if manifest is not None and report.input_manifest_id != manifest.manifest_id:
        own.append(f"核对报告绑的清单 `{report.input_manifest_id}` 不是本目录的清单 "
                   f"`{manifest.manifest_id}`")
    problems.extend(own)
    return (ChainLink(name="sentence_checks", filename=path.name, present=True,
                      identity=report.report_id,
                      detail=(f"报告 `{report.report_id[:12]}…`｜结论 "
                              f"`{report.mechanical_verdict}`、`{report.publishability}`｜"
                              f"记录 {len(report.records)} 条、硬错句 "
                              f"{report.blocked_sentence_count} 句"),
                      problems=tuple(own)), report)


def _link_review_input(run_dir: Path, manifest, draft, problems: list[str]) -> tuple[
        ChainLink, Mapping[str, Any] | None]:
    path = run_dir / "review_issues.json"
    if not path.exists():
        return (ChainLink(name="review_input", filename=path.name, present=False,
                          detail=LINK_UNJUDGED), None)
    body = _load_json(path)
    own: list[str] = []
    if not isinstance(body, Mapping):
        own.append("review_issues.json 不是对象")
        problems.extend(own)
        return (ChainLink(name="review_input", filename=path.name, present=True,
                          detail="在场但形状不符", problems=tuple(own)), None)
    if manifest is not None and body.get("input_manifest_id") != manifest.manifest_id:
        own.append(f"审阅绑的清单 `{body.get('input_manifest_id')}` 不是本目录的清单 "
                   f"`{manifest.manifest_id}`")
    if draft is not None and body.get("draft_id") != draft.draft_id:
        own.append(f"审阅绑的草稿 `{body.get('draft_id')}` 不是本目录的草稿 "
                   f"`{draft.draft_id}`：审阅看的不是这份正文")
    if draft is not None:
        reviewed = tuple(str(x) for x in (body.get("sentence_ids") or ()))
        drafted = tuple(s.sentence_id for sub in draft.subsections for para in sub.paragraphs
                        for s in para.sentences)
        if reviewed != drafted:
            own.append(f"审阅的句子集合与草稿不等（或不同序）：审阅 {list(reviewed)}，"
                       f"草稿 {list(drafted)}")
    # 「审阅**会**看到什么」由现有构建口重建一次，而不是靠文件里的一句声明。
    if manifest is not None and draft is not None:
        try:
            request = CR.build_cited_review_request(draft=draft, manifest=manifest)
        except Exception as exc:  # noqa: BLE001
            own.append(f"用本目录的清单 + 草稿重建审阅请求失败："
                       f"{type(exc).__name__}: {exc}")
        else:
            rows = request.payload.get("sentences") or ()
            want = [(s.sentence_id, s.text) for sub in draft.subsections
                    for para in sub.paragraphs for s in para.sentences]
            got = [(str(r.get("sentence_id", "") or ""), str(r.get("text", "") or ""))
                   for r in rows]
            if got != want:
                own.append("重建的审阅请求逐句（ID、文本）与草稿不一致")
            sources = {str(s.get("key", "") or "") for s in (request.payload.get("sources") or ())}
            if not sources:
                own.append("重建的审阅请求里**没有任何来源**：正文引用的键一个都没进审阅输入")
    problems.extend(own)
    return (ChainLink(name="review_input", filename=path.name, present=True,
                      identity=str(body.get("report_id", "") or ""),
                      detail=(f"审阅 `{body.get('review_producer_kind', '')}`｜结论 "
                              f"`{body.get('outcome', '')}`｜意见 "
                              f"{len(body.get('issues') or ())} 条｜句子 "
                              f"{len(body.get('sentence_ids') or ())} 句"),
                      problems=tuple(own)), body)


def _link_journal(run_dir: Path, manifest, draft, problems: list[str]) -> tuple[
        ChainLink, Mapping[str, Any] | None]:
    path = run_dir / CHAIN_JOURNAL_FILE
    if not path.exists():
        return (ChainLink(name="call_journal", filename=path.name, present=False,
                          detail=LINK_UNJUDGED), None)
    body = _load_json(path)
    own: list[str] = []
    recorded = (body.get("input") or {}) if isinstance(body, Mapping) else {}
    if manifest is not None and recorded.get("manifest_id") != manifest.manifest_id:
        own.append(f"留存簿记的清单 `{recorded.get('manifest_id')}` 不是本目录的清单 "
                   f"`{manifest.manifest_id}`")
    if manifest is not None:
        keys = list(recorded.get("material_keys") or ())
        want = list(_material_keys(manifest))
        if keys != want:
            own.append(f"留存簿记的材料引用键与清单不一致：簿 {keys}，清单 {want}")
    if draft is not None:
        parsed = (body.get("parse") or {}) if isinstance(body, Mapping) else {}
        if parsed.get("ok") and parsed.get("draft_id") != draft.draft_id:
            own.append(f"留存簿记的解析草稿 `{parsed.get('draft_id')}` 不是本目录的草稿 "
                       f"`{draft.draft_id}`")
        # 最能说明问题的一条：把留存的可见回复**重新解析一次**，要求得到同一个 draft_id。
        #
        # `writer_identity` 必须用**这次调用自己的 `call_id`**：写作链在 `write_cited_section`
        # 里正是这么传的（`parse_cited_prose_with_normalization(..., writer_identity=result.call_id)`），
        # 而 `draft_id` 是内容寻址、把这一轴也算进身份。拿提示词版本号之类的别的字符串去重放，
        # 必然得到另一个 draft_id——那是读回侧自造的假警报，不是链的问题。
        visible = ((body.get("reply") or {}) if isinstance(body, Mapping) else {}).get("text")
        if isinstance(visible, str) and visible.strip() and manifest is not None:
            reply_call_id = str(((body.get("reply") or {}) if isinstance(body, Mapping)
                                 else {}).get("call_id", "") or "")
            if not reply_call_id:
                own.append("留存簿没记 `reply.call_id`：没有它就无法用**同一次调用**的身份重放"
                           "这段回复（拿别的字符串去重放只会得到一个必然不同的 draft_id）")
            try:
                replayed = CW.parse_cited_prose(
                    visible, manifest=manifest, task_id=manifest.task_id,
                    section_id=manifest.section_id, writer_identity=reply_call_id)
            except Exception as exc:  # noqa: BLE001
                own.append(f"留存的可见回复重放解析失败：{type(exc).__name__}: {exc}")
            else:
                if replayed.draft_id != draft.draft_id:
                    own.append(f"留存的可见回复重放得到草稿 `{replayed.draft_id}`，"
                               f"与本目录的草稿 `{draft.draft_id}` 不是同一份："
                               "盘上的正文与留存的那次调用对不上（多半是提示词身份变了）")
    if body.get("publishable") is not False:
        own.append("留存簿的 publishable 不是 false")
    problems.extend(own)
    return (ChainLink(name="call_journal", filename=path.name, present=True,
                      detail=(f"留存 `{body.get('journal_version', '')}`｜call_id "
                              f"`{(body.get('reply') or {}).get('call_id', '')}`｜"
                              f"状态 `{(body.get('reply') or {}).get('status', '')}`"),
                      problems=tuple(own)), body)


def _sentence_rows(draft, report, review) -> tuple[ChainSentenceRow, ...]:
    """逐句把四段摆平：一句一行，缺哪一段就在这一行上写明。"""
    checks: dict[str, list[SC.SentenceCheckRecord]] = {}
    if report is not None:
        for record in report.records:
            checks.setdefault(record.sentence_id, []).append(record)
    issues: dict[str, list[str]] = {}
    for issue in (review or {}).get("issues") or ():
        #: `rvi-2` 的意见钉在**一句**上（`sentence_id`），不是一串；读回侧按它的真值表读。
        sid = str(issue.get("sentence_id", "") or "")
        if sid:
            issues.setdefault(sid, []).append(str(issue.get("issue_id", "") or ""))

    rows: list[ChainSentenceRow] = []
    for sub in draft.subsections:
        for para in sub.paragraphs:
            for sentence in para.sentences:
                own: list[str] = []
                records = checks.get(sentence.sentence_id)
                if report is not None and not records:
                    own.append("逐句核对里**没有**这一句的记录")
                rows.append(ChainSentenceRow(
                    sentence_id=sentence.sentence_id, subsection_id=sub.subsection_id,
                    paragraph_id=para.paragraph_id, text=sentence.text,
                    citations=tuple(sentence.citations),
                    check_kinds=tuple(r.check_kind for r in (records or ())),
                    check_verdicts=tuple(r.verdict for r in (records or ())),
                    review_issue_ids=tuple(issues.get(sentence.sentence_id, ())),
                    problems=tuple(own)))
    return tuple(rows)


def read_chain(run_dir: Path | str) -> ChainReadback:
    """运行目录 → 四段（+ 可选留存簿）对账。**唯一**入口。"""
    run_dir = Path(run_dir)
    problems: list[str] = []
    if not run_dir.exists():
        # 与「目录在、文件缺」**不是**同一件事：这里根本没有过一轮运行，所以既不能说
        # 「这几个历史字段本轮不可判」（那是对**跑过的**目录说的），也不该按缺件逐环报告。
        return ChainReadback(
            run_dir=str(run_dir),
            links=tuple(ChainLink(name=name, filename=filename, present=False,
                                  detail=f"运行目录不存在：{run_dir}")
                        for name, filename in CHAIN_LINK_FILES),
            problems=(f"运行目录不存在：{run_dir}",),
            unjudged_fields=())

    input_link, manifest = _link_input_manifest(run_dir, problems)
    prose_link, draft, _raw = _link_sentence_citations(run_dir, manifest, problems)
    check_link, report = _link_sentence_checks(run_dir, manifest, draft, problems)
    review_link, review = _link_review_input(run_dir, manifest, draft, problems)
    journal_link, _journal = _link_journal(run_dir, manifest, draft, problems)

    sentences = _sentence_rows(draft, report, review) if draft is not None else ()
    missing_any = not all(x.present for x in (input_link, prose_link, check_link, review_link))
    return ChainReadback(
        run_dir=str(run_dir),
        links=(input_link, prose_link, check_link, review_link, journal_link),
        sentences=sentences, problems=tuple(problems),
        unjudged_fields=(HISTORICAL_UNFILLABLE if missing_any else ()))


def render_chain_markdown(readback: ChainReadback) -> str:
    """人读页：先报四段在场与否，再**逐句**摆出四段。"""
    lines = ["# 写作运行目录｜离线四段对账（清单 → 句子引用 → 核对 → 审阅输入）", "",
             "> **不是验收门、不判合格**：这一页只回答「这四个对象是不是同一件事」。",
             "> 四段自洽**不等于**内容成立；能不能接受仍由独立审阅与人工接受判。", "",
             f"- 运行目录：`{readback.run_dir}`",
             f"- 四段是否齐备且自洽：**{'是' if readback.complete else '否'}**", "",
             "## 1. 四段（+ 留存簿）逐步", "",
             "| 段 | 文件 | 在场 | 身份 | 说明 | 问题 |",
             "|---|---|---|---|---|---|"]
    for link in readback.links:
        lines.append(f"| `{link.name}` | `{link.filename}` | "
                     f"{'是' if link.present else '**否**'} | `{link.identity}` | "
                     f"{link.detail} | {'；'.join(link.problems) or '—'} |")
    if readback.problems:
        lines += ["", "### 跨段问题", ""]
        lines += [f"- {p}" for p in readback.problems]
    lines += ["", "## 2. 逐句四段对照", "",
              "| 句 | 小节 | 引用（= 清单里的键） | 逐句核对（轴=结论） | 审阅意见 | 问题 |",
              "|---|---|---|---|---|---|"]
    for row in readback.sentences:
        checks = "；".join(f"`{k}`={v}" for k, v in
                           zip(row.check_kinds, row.check_verdicts)) or "—"
        citations = "、".join(f"`{c}`" for c in row.citations) or "—"
        issues = "、".join(f"`{i}`" for i in row.review_issue_ids) or "—"
        lines.append(f"| `{row.sentence_id}` | `{row.subsection_id}` | {citations} | "
                     f"{checks} | {issues} | {'；'.join(row.problems) or '—'} |")
    lines += ["", "## 3. 本页**不判**的东西", ""]
    lines += [f"- {item}" for item in readback.unjudged_fields] or ["- （无）"]
    if readback.unjudged_fields:
        lines += ["", "上面这些字段**不能**从别处的产物补出来：补出来的东西看着像身份，"
                  "实际没有任何字节与之对应。缺件一律记「本轮不可判」。"]
    lines.append("")
    return "\n".join(lines)

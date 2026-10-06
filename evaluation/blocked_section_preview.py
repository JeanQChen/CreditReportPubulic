"""受阻章节的**不可发布预览**（只读、版本化、独立出口）。

## 它解决的是哪一件事

M930-3 的正式出口是两条：整本报告组装成功 ⇒ `report_preview.md`；本节没成形 ⇒ 失败侧门前
诊断 `pre_gate_draft.*`（`pgr-1`）。真实 run r9 暴露了**第三条**现场，两条出口都不接：

  * 本节**已经**有正式 `SectionDraft`、门侧决定、`SectionResult` 与逐条引用 —— 也就是说
    门前留存那条出口**明确拒绝**覆盖它（`pre_gate_draft.json` 里这一节的状态就是 `readback`：
    「门前留存**不是**这一节的出口」）；
  * 但整本报告因为**另一节**没产出而被拒（r9：`有章节未产出，不得组装整本报告：['company']`），
    于是 `report_preview.md` **一个字都没写**。

结果：r9 的财务节是**真的过了所有门**的（`SectionResult` 25 条 Claim、评价决定
`PASS_WITH_GAPS`、25 条路径 A accepted binding、正文与四张权威指标表都在库里），而读者拿到的
**什么都没有**。这不是「内容不合格」，是**内容没有出口**——「失败不可审计」的第二种形态：
上一轮修掉的是「本节失败、本节内容丢了」，这里要修的是「别节失败、**本节内容也丢了**」。

## 它是什么、不是什么

它是**逐节**的只读读回：从**持久化对象**（`section_chain_v2.db` 的 current 链 + 运行目录里
的 `section_evaluations.json` / `proposal_set_rejections.json` / `acceptance_report.json`）取出
每一节**已经过门**的内容，连同**逐条阻断原因**与**缺口**一起列出，逐字标注
`未核验、不可发布`。

三件事必须**分栏**、不得混成一篇「已核验正文」：

  1. `accepted`：已过门前硬门与门后裁定的内容（Claim / final Narrative / Result 正文与表 /
     最终句决定 / 逐条引用与逐条支撑边）。它**不是**「已核验」——本节可能仍是
     `SECTION_BLOCKED`，且整本报告可能已被拒；本件只是把**库里确实存在的东西**读出来。
  2. `rejected_pre_gate`：被拒的门前草稿（`pgr-1` 留存）。逐字未核验，**不得**被读成已接受事实。
  3. `stub_organizer`：由**替身组织器**产出的正文。它**不是**真实模型成稿，只在调用方**显式
     声明**该节正文来自替身时出现，并逐字标 `stub`。

三栏之外另有两处**人读面**（`/2` 新增；都是**呈现**，不新增判据、不放宽任何门）：

  4. `sections[*].follow_up`：模型提出过的**补件去向**。三种记录分列（待裁决诉求 / 连 Contract
     校验都没过的原始诉求 / 已写出内容那些节里自己不成立的申请），状态一律
     `pending_adjudication`——**未裁决、未执行、不是 gap**。它与三栏的关系是「另外一件事」，
     因此**另起一块**，不塞进栏里（塞进去会让「三栏」变成四栏，`CONTENT_COLUMNS` 那句
     「三栏同时在场」就不再是读法）。
  5. 逐 Claim 与逐句的 `citations[]`：把 `cite_…` 这类**单向散列**引用译成人读行。中文来源
     复用生产渲染器的**同一个**实现（`NS.citation_source_index` / `NS.citation_source_notes`），
     本件**不另造**来源措辞；生产渲染器有意不印的内部记号，在**复核面**按内部记号列出并标注
     「复核面」——读者面与复核面因此不会各说各话。解析不出来的 `citation_id` 如实标
     `unresolved`，**不得**因为译不出来就把那句写成没有引用。

它**不**产生、也**不**冒充：正式 `report_version`、`SectionResult=PASS`、系统放行、人工接受。
没有可持久化内容时它给 typed `unavailable` + **首个缺失对象**，**不**从旧报告拼一篇顶上，
也**不**造一段空壳正文。它**不放宽**任何门：正式组装器对 `REWORK/BLOCKED/FAILED` 的拒绝判据
一字不改（`sections/report_assembler.py`），本件是判据之外的只读出口。

## 离线重放 ≠ 当前版本真实成稿

`source_kind` 是**必填的显式声明**，不是推断出来的。三档回答三个不同的问题，**不得混用**：

  * `real_run`：本次运行自己写出的持久化对象。
  * `historical_real_run`：**读回**一次历史真实 run 已落库的产物。没有重放、没有新模型调用、
    也没有在当前代码上重跑任何一步——它说的是「那一版代码当时产出了什么」。
  * `offline_replay`：拿**历史 run 留存的字节**在**当前代码**上重走同一条链得到的产物。它
    **不是**一次真实验收、**不是**一次真实模型调用；组织器与蕴含判定都可能是替身，因此这一档
    必须与 `stub_organizer_sections` 一起读。

本件**不**回写被重放/被读回的历史目录：写入目标由调用方显式给出，且 CLI 上
`--in-place`（写回 run 目录）**只**对 `real_run` 档开放，其余两档被结构性地拒绝。

用法::

    python -X utf8 -m evaluation.blocked_section_preview --run-dir DIR [--sections a,b,c]
        [--source-kind real_run|offline_replay] [--stub-sections s] [--out DIR] [--in-place]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

#: 载荷形状版本。字段增删/改读法必须改这个号——复核者的读法依赖它。
#: `/1`：首版。逐节 `outlet_status`（三态）＋三栏内容（`accepted` / `rejected_pre_gate` /
#: `stub_organizer`）＋逐条阻断原因＋缺口＋逐 Claim 支撑边。
#: `/2`：**只加字段**——三栏名、四档出口状态、三档来源、三档正文来源一字未动。新增两处
#: **人读面**（都是呈现层，不新增任何判据、不放宽任何门）：
#:   * `sections[*].follow_up`：补件去向（待裁决诉求 / 不成立的原始诉求 / 已写出节里自己不成立
#:     的申请）。**独立于三栏**——它与「本节有没有内容」是两个问题。
#:   * `accepted.content.citations` / `accepted.content.citation_index` /
#:     `accepted.content.narrative.paragraphs[*].sentences[*].citations`：把 `cite_…`
#:     译成人读行；中文来源复用生产渲染器的唯一实现，本件不另造措辞。
SCHEMA_VERSION = "blocked-section-preview/2"

#: 本件身份上的**不可发布**标注。**只有一处定义**在写手侧（留存对象自己带
#: `pack_writer.PRE_GATE_RETENTION_LABEL`），这里取同一个串用——两处各写一句措辞必然漂移。
LABEL_FALLBACK = "未核验、不可发布"

#: 逐节的出口状态（封闭取值）。四者意思不同，不得合并：
#:   * `content_readback`：本节**有**可读回的已过门内容（`SectionResult` 读回来了）。
#:     请注意它只说「库里有东西可读」，**不**说本节通过、也不说内容合格。
#:   * `unavailable`：本节**没有**可持久化的内容。`first_missing_object` 给出**第一个**缺的对象。
#:     它是**事实**（这一节确实没产出），不是「我们没去查」。
#:   * `readback_failed`：本节**有**落库的草稿行，但 current 链**读不回来**（形状/身份/版本
#:     对不上）。它**不是** `unavailable`——把「读不回来」写成「没有内容」正是本件要防的静默错。
#:   * `store_unreadable`：**库本身**打不开（缺文件/不是 SQLite/表缺）。它与 `readback_failed`
#:     的区别是「整库不可读」而非「这一节的链不可读」，两者都不等于「没有内容」。
OUTLET_STATUSES = ("content_readback", "unavailable", "readback_failed", "store_unreadable")

#: 来源档位（封闭取值）。**必填**：调用方声明，不由本件推断。三者**不得混用**，因为它们
#: 回答的是三个不同的问题：
#:   * `real_run`：本次运行**自己**写出的持久化对象（验收 runner 的现场）。
#:   * `historical_real_run`：读的是一次**历史真实 run** 留下的产物。**没有**重放、**没有**
#:     新模型调用、也**没有**在当前代码上重跑任何一步——因此它说的是「那一版代码当时产出了
#:     什么」，**不是**「当前版本会产出什么」。
#:   * `offline_replay`：用历史 run 留存的字节在**当前代码**上重走同一条链得到的产物。
#:     它照旧**不是**真实验收、**不是**真实模型调用。
SOURCE_KINDS = ("real_run", "historical_real_run", "offline_replay")

#: 逐档的定性（人读版与机器读版共用同一处措辞）。**只有一处定义**，否则两个渲染会各说各话。
SOURCE_KIND_NOTES = {
    "real_run": "**本次运行**：下面的内容读自本次运行自己写出的持久化对象。",
    "historical_real_run": (
        "**历史真实运行**：下面的内容是**读回**一次历史真实 run 已经落库的产物。本次读回"
        "**没有**重放、**没有**新模型调用、也**没有**在当前代码上重跑任何一步——因此它说的是"
        "**那一版代码当时产出了什么**，**不是**当前版本会产出什么。"),
    "offline_replay": (
        "**离线重放**：下面的内容读自一次**重放**（用历史 run 留存的字节在**当前代码**上重走"
        "同一条链）。它**不是**一次真实验收、**不是**一次真实模型调用；标为 `stub_organizer` "
        "的节，其正文由替身组织器产出，只证明链路走通。"),
}

#: 正文来源（封闭取值）：
#:   * `model`：调用方**显式声明**本节正文来自真实模型调用（本件**不**核验这一点，只回声声明）。
#:   * `stub_organizer`：调用方**显式声明**本节正文由**替身组织器**产出，**不是**真实模型成稿。
#:   * `not_applicable`：**缺省档**。本节没有正文，或调用方**没有声明**这一节的正文来源。
#:     它**不是**「已核验为模型成稿」——把「没声明」写成 `model` 正是本件要防的那一类越权断言，
#:     所以 `model` **只能由调用方显式给出**，不由本件推断。
PROSE_ORIGINS = ("model", "stub_organizer", "not_applicable")

#: 三栏内容的名字。顺序即人读顺序：先已过门的，再被拒的，最后替身的。
CONTENT_COLUMNS = ("accepted", "rejected_pre_gate", "stub_organizer")

#: 缺失对象的**判定顺序**（`first_missing_object` 取其中第一个真的缺的）。顺序即写作主链的
#: 单向次序：没有 Draft 就谈不上门的决定，没有 Result 就谈不上「本节成形」。
MISSING_OBJECT_ORDER = ("SectionDraft", "SectionResult")

#: 本件**不得**被读成的东西。逐条列在载荷上，而不是只写在文档里——产物自己要说清楚它不是什么。
FORBIDDEN_CLAIMS = (
    "本件不是正式 `report_version`，也不得被用来生成、冒充或回填任何一个正式版本号；",
    "本件不是 `SectionResult=PASS`：本节状态原样印出（`SECTION_BLOCKED` 不得为好看改绿）；",
    "本件不是系统放行：整本报告是否被拒由正式组装器的判据决定，本件不放宽任何门；",
    "本件不是人工接受：没有任何人工确认发生在本次读回里；",
    "本件不是 M930-4 的独立审查：它不做逐条 `ReviewIssue`，也不产出任何 Assurance 状态；",
    "`stub_organizer` 档的正文**不是**真实模型成稿；`offline_replay` 档**不是**真实验收。",
)

#: 三栏各自的定性（人读版与机器读版共用同一处措辞）。
COLUMN_NOTES = {
    "accepted": (
        "**已过门前硬门与门后裁定的内容**（Claim / final Narrative / Result 正文与表格 / "
        "逐条引用与逐条支撑边）。它读自持久化的 current 链，身份由读回侧重算核验。"
        "它**不是**「已核验正文」：本节可能仍是 `SECTION_BLOCKED`，整本报告可能已被拒。"),
    "rejected_pre_gate": (
        "**被拒的门前草稿**（`pgr-1` 留存）。逐字未核验、不可发布：它没有通过任何门，"
        "**不得**被读成已接受事实，也不得被引用进任何报告。"),
    "stub_organizer": (
        "**替身组织器产出**的正文。它**不是**真实模型成稿，只证明链路走通；"
        "本栏非空时，`accepted` 栏的正文来源也必须在 `prose_origin` 上标为 `stub_organizer`。"),
}

#: 「本次读回自己失败了」时输出什么。空表会被读成「本节什么都没有」，所以失败必须自成一句。
BUILD_FAILED_NOTE = (
    "**本次受阻预览本身没读成**（`status=build_failed`）：下面的空表是「读不回来」，"
    "**不是**「本轮没有任何内容」。它不影响正文，也不改任何门——但这份账这次不能用。")

#: 引用字段的**中文标签**（唯一一处定义：机器读版与人读版共用）。
#: 只做**译名**：键集与 `harness.schema.CitationRef` 的字段一一对应；这里不新增字段、
#: 也不替任何字段下结论。印不出来的（值为空）一律不印。
CITATION_FIELD_LABELS = (
    ("ref_type", "引用类型"),
    ("evidence_id", "证据块"),
    ("evidence_fact_id", "证据事实"),
    ("snapshot_id", "快照"),
    ("item_code", "科目"),
    ("formula_id", "公式"),
    ("formula_version", "公式版本"),
    ("period", "期间"),
    ("source_snapshot_id", "外部快照"),
    ("page_number", "页码"),
)

#: 支撑边字段的**中文标签**（同一条线上 `accepted` / `context` 两路边的并集）。
SUPPORT_FIELD_LABELS = (
    ("authority_kind", "权威种类"),
    ("authority_container_id", "权威容器"),
    ("support_semantics", "支撑语义"),
    ("support_role", "支撑角色"),
    ("authorization_path", "授权路径"),
    ("material_id", "材料"),
    ("fact_id", "事实"),
    ("financial_fact_id", "财务事实"),
    ("note_fact_id", "附注事实"),
    ("external_fact_id", "外部事实"),
    ("locator_ref", "定位"),
    ("payload_ref", "载荷引用"),
)

#: 逐条引用在人读版里的**去向**（封闭取值）。三种意思不同，不得合并：
#:   * `reader_named`：生产渲染器**印得出**中文来源 ⇒ 本件逐字沿用它的措辞。
#:   * `reviewer_only`：生产渲染器**有意不印**（`evidence` / `external` 没有中文来源名）。
#:     本件是**复核面**，按内部记号列出并显式标注——**不得**把它读成读者面措辞。
#:   * `unresolved`：这条 `citation_id` 在**本件读回的 Claim 集**里找不到对应引用
#:     （形如某句引用了别节的引用）。如实记录，**不猜**、**不丢**。
CITATION_READINGS = ("reader_named", "reviewer_only", "unresolved")

#: 补件去向的三种记录（封闭取值）。三者意思不同，不得合并，也不得互推：
#:   * `pending_need`：可类型化的 `FollowUpNeed`（**待裁决、未执行**）。
#:   * `untypeable_raw`：连 Contract 校验都没过的原始诉求（含不成立原因）。
#:   * `rejected_application`：**已写出内容**的节里自己不成立的申请（不阻断本节）。
FOLLOW_UP_ROW_KINDS = ("pending_need", "untypeable_raw", "rejected_application")

#: 补件去向的**唯一**定性（机器读版与人读版共用）。三句话缺一不可：不是执行结果、
#: 不是缺口、也不由本件裁决。
FOLLOW_UP_NOTE = (
    "**待裁决诉求：不是执行结果、不是缺口。** 这些是 Writer 在写作时提出的补件诉求，按产物自己的"
    " `status=pending_adjudication` 原样列出：**没有**被裁决、**没有**被执行、**没有**因此生成"
    "任何材料或事实。补件是否执行由 Harness 按 Contract、预算与 SourcePolicy 决定，不由本件决定。"
    "缺口另有 Contract 依据与检索记录——把诉求读成缺口，或把「模型没提诉求」读成「本节不缺东西」，"
    "两种读法都是错的。已由 Harness 裁决并执行过的补件运行是**另一套身份**"
    "（`FollowUpExecutionResult`），不在本块。")

#: 补件去向的产物文件名（唯一一处）。
FOLLOW_UP_SOURCE_FILE = "follow_up_needs.json"


def _ref_fields(ref: Any) -> dict:
    """一条引用 / 支撑边的可读字段（**不**改一个键，也不加派生字段）。"""
    if isinstance(ref, Mapping):
        return {str(k): v for k, v in ref.items()}
    body = getattr(ref, "__dict__", None)
    if isinstance(body, dict):
        return {str(k): v for k, v in body.items()}
    to_dict = getattr(ref, "to_dict", None)
    if callable(to_dict):
        try:
            out = to_dict()
        except Exception:  # noqa: BLE001 —— 读不出字段是结论，不是崩溃
            return {}
        return {str(k): v for k, v in out.items()} if isinstance(out, Mapping) else {}
    return {}


def _labelled_line(fields: Mapping[str, Any], labels: Sequence[tuple[str, str]]) -> str:
    """`(键, 中文标签)` 表 → 一行人读文本。空值不印（把「没有」印成「空」是另一种误读）。"""
    parts: list[str] = []
    for key, label in labels:
        value = fields.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple)):
            value = "、".join(str(v) for v in value)
        elif isinstance(value, Mapping):
            inner = "、".join(f"{k}={v}" for k, v in value.items()
                              if v not in (None, "", [], {}))
            value = "{" + inner + "}"
        parts.append(f"{label} `{value}`")
    return "；".join(parts) or "（本条引用没有任何可读字段）"


def _named_index(claims: Any) -> dict:
    """一节 Claim 集 → **中文来源**索引（生产渲染器的唯一实现，本件不另造措辞）。"""
    from sections import narrative_schema as NS

    return NS.citation_source_index(claims)


def _citation_index(named: Mapping[str, Any], claims: Any) -> dict[str, dict]:
    """本节 Claim 集 → `citation_id -> 人读引用行`（`cite_…` 是单向散列，只能现场重建）。

    两层来源，各司其职，**不互推**：

      * **中文来源**由调用方传入（`_named_index`，即生产渲染器的唯一实现）——本件不另造
        一句来源措辞，也不把「印不出中文来源」当成「没有来源」；
      * **复核面内部记号**由引用对象自己的字段拼出（同一份 `derive_citation_id` 派生键，
        因此键就是正文里那批 `cite_…`）。
    """
    from sections import schema as SS

    out: dict[str, dict] = {}
    for claim in claims or ():
        claim_id = str(getattr(claim, "claim_id", "") or "")
        if not claim_id:
            continue
        for ref in tuple(getattr(claim, "citation_refs", ()) or ()):
            citation_id = SS.derive_citation_id(claim_id, ref)
            source = named.get(citation_id)
            row = {
                "citation_id": citation_id,
                "claim_id": claim_id,
                "reader_named_source": (f"{source[0]}（期间 {source[1]}）"
                                        if source and source[1] else
                                        (source[0] if source else None)),
                "reading": "reader_named" if source else "reviewer_only",
                "ref_fields": _ref_fields(ref),
            }
            row["line"] = _labelled_line(row["ref_fields"], CITATION_FIELD_LABELS)
            if row["reading"] == "reviewer_only":
                row["line"] += "（**复核面**：生产渲染器不把内部记号印进读者面，此处按复核面列出）"
            existing = out.get(citation_id)
            if existing is not None and existing["line"] != row["line"]:
                # 同一 citation_id 两条不同明细：那是身份碰撞，如实记下**两条**，不取其一。
                out[citation_id] = {**existing, "collision": [
                    existing["line"], row["line"]],
                    "line": "同一 `citation_id` 出现两条不同明细（身份碰撞，两条并列）"}
                continue
            out.setdefault(citation_id, row)
    return out


def _citation_rows(citation_ids: Iterable[Any], index: Mapping[str, dict]) -> list[dict]:
    """一组 `citation_id` → 人读引用行（保序；解析不出来的标 `unresolved`，不丢）。"""
    rows: list[dict] = []
    for raw in citation_ids or ():
        citation_id = str(raw)
        row = index.get(citation_id)
        if row is None:
            rows.append({"citation_id": citation_id, "reading": "unresolved",
                         "claim_id": None, "reader_named_source": None,
                         "ref_fields": {},
                         "line": ("本件读回的 Claim 集里没有这条 `citation_id`："
                                  "**不代表它不存在**，只代表它不来自本节任何一条 Claim 的引用"
                                  "（如实记录，不猜）")})
            continue
        rows.append(dict(row))
    return rows


def _reader_source_notes(citation_ids: Iterable[Any], named: Mapping[str, Any]) -> list[str]:
    """一段引用的人读**中文来源**行（生产渲染器的同一个 `citation_source_notes`）。

    **不印**那些印不出中文来源的引用——这是生产渲染器已有的读者面判据（`pwr-4`），
    本件逐字沿用：复核面另有一列（`reviewer_only`）把它们按内部记号列出。
    """
    from sections import narrative_schema as NS

    return [str(n) for n in tuple(NS.citation_source_notes(citation_ids, named) or ())]


def _read_json(path: Path) -> tuple[dict | None, str]:
    """读一份 JSON 产物：`(载荷, 原因)`。缺失/解析失败**都必须与「载荷是空对象」分开报**。"""
    if not path.exists():
        return None, f"产物不存在：{path.name}"
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"产物不可解析：{path.name}（{type(exc).__name__}: {str(exc)[:160]}）"
    if not isinstance(body, dict):
        return None, f"产物顶层不是对象：{path.name}（得到 {type(body).__name__}）"
    return body, ""


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _titles_from_run(run_dir: Path) -> dict[str, str]:
    """尽量从运行产物里取节标题；取不到就留空串，**不**编一个标题。

    r9 的 `section_drafts.json` 里没有标题（那是 `SectionDraft` 的载荷）。标题对判读没有
    影响，但对人读有用，因此这里只在**确有**来源时给出：`acceptance_report.json` 的
    `gates[].evidence.sections[].title`。没有就空着——空标题在渲染上是可见的缺失，
    编一个标题则会让「这一节叫什么」变成不可核验的断言。
    """
    report, _ = _read_json(run_dir / "acceptance_report.json")
    titles: dict[str, str] = {}
    for gate in tuple((report or {}).get("gates") or ()):
        if not isinstance(gate, dict):
            continue
        for row in tuple((gate.get("evidence") or {}).get("sections") or ()):
            if isinstance(row, dict) and _text(row.get("section_id")) and _text(row.get("title")):
                titles.setdefault(str(row["section_id"]), str(row["title"]))
    return titles


def _draft_rows(db: Path) -> tuple[dict[str, str], str]:
    """从库里读出 `section_id → draft_id`（**只读**：`mode=ro` + `query_only`）。"""
    if not db.exists():
        return {}, f"章节链库不存在：{db.name}"
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return {}, f"章节链库打不开：{db.name}（{type(exc).__name__}: {str(exc)[:160]}）"
    try:
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute(
            "SELECT draft_id, section_id FROM current_section_draft_v2 "
            "ORDER BY section_id").fetchall()
    except sqlite3.Error as exc:
        # 表缺 / 不是 SQLite / 被改过形状，都在这里，**不和**「这一节没有 draft」混为一谈。
        return {}, f"章节链库不可读：{db.name}（{type(exc).__name__}: {str(exc)[:160]}）"
    finally:
        conn.close()
    return {str(row[1]): str(row[0]) for row in rows}, ""


def _load_chain(db: Path, draft_id: str):
    """用**生产读回器**重建一条 current 链（身份在读回侧重算，被改行即 fail-closed）。

    为什么必须用生产读回器、而不是自己 SELECT 几张表拼出来：本件的结论是「库里确实存在什么」，
    因此「存在」的判据必须与落库侧同源——自己拼一份读视图，就会把「这一行其实是旧的/被改过的」
    读成「这一行是合法的」。读回器会重算 `draft_revision` / 各对象的内容身份，对不上就拒。
    """
    from sections import store as ST

    saved = ST._db_path
    ST._db_path = db
    try:
        return ST.load_current_section_chain_v2(draft_id), ""
    except Exception as exc:  # noqa: BLE001 —— 读不回来是**结论**，不是崩溃
        return None, f"{type(exc).__name__}: {str(exc)[:400]}"
    finally:
        ST._db_path = saved


def _claim_row(claim, bindings: Mapping[str, Any], index: Mapping[str, dict]) -> dict:
    """一条 Claim 的读回行：正文 + 逐条引用（**人读行**）+ 逐条支撑边（材料/权威/定位）。"""
    from sections import schema as SS  # 引用 id 的**唯一**派生实现（不二次硬编码）

    support: list[dict] = []
    for binding_id in tuple(getattr(claim, "accepted_binding_ids", ()) or ()):
        binding = bindings.get(str(binding_id))
        if binding is None:
            # 引用了一条本件没读到的支撑边：如实记下来，**不**静默丢掉。
            support.append({"accepted_support_binding_id": str(binding_id),
                            "missing": "该 accepted binding 不在本次读回的束里"})
            continue
        support.append({
            "accepted_support_binding_id": binding.accepted_support_binding_id,
            "support_semantics": binding.support_semantics,
            "support_role": binding.support_role,
            "authority_kind": binding.authority_kind,
            "authority_container_id": binding.authority_container_id,
            "authorization_path": binding.authorization_path,
            "source_identity": binding.source_identity,
            "provenance_identity": binding.provenance_identity,
            "entailment_decision_id": binding.entailment_decision_id,
            "fact_id": binding.fact_id,
            "financial_fact_id": binding.financial_fact_id,
            "note_fact_id": binding.note_fact_id,
            "external_fact_id": binding.external_fact_id,
            "material_id": binding.material_id,
            "payload_ref": binding.payload_ref,
            "locator_ref": binding.locator_ref,
        })
    return {
        "claim_id": str(claim.claim_id),
        "text": str(claim.text),
        "claim_type": str(claim.claim_type),
        "topic_id": str(claim.topic_id),
        "question_ids": [str(q) for q in tuple(claim.question_ids or ())],
        "citation_refs": [dict(c.__dict__) if hasattr(c, "__dict__")
                          else dict(getattr(c, "to_dict", lambda: {})())
                          for c in tuple(claim.citation_refs or ())],
        # 同一批引用的**人读行**（`citation_id` 与中文来源/复核面记号）。与上面的裸字段是
        # 两个问题：上面是「引用对象原样长什么样」，这里是「它译成人话是哪一条」。
        "citations": _citation_rows(
            [SS.derive_citation_id(str(claim.claim_id), ref)
             for ref in tuple(claim.citation_refs or ())], index),
        "claim_candidate_id": str(claim.claim_candidate_id),
        "claim_candidate_revision": str(claim.claim_candidate_revision),
        "accepted_binding_ids": [str(b) for b in tuple(claim.accepted_binding_ids or ())],
        "support": support,
    }


def _unresolved_row(item) -> dict:
    return {
        "unresolved_id": str(item.unresolved_id),
        "state": str(item.state),
        "reason_code": str(item.reason_code),
        "topic_id": str(item.topic_id),
        "question_id": item.question_id,
        "impact_scope": [str(x) for x in tuple(item.impact_scope or ())],
        "blocking_effects": [str(x) for x in tuple(item.blocking_effects or ())],
        "detail": str(item.detail),
    }


def _accepted_column(chain) -> dict:
    """`accepted` 栏：已过门内容（Claim / final Narrative / Result 正文与缺口）。"""
    result = chain.result
    bindings = {b.accepted_support_binding_id: b for b in chain.accepted_bindings}
    claim_objs = tuple(chain.claims)
    named = _named_index(claim_objs)
    index = _citation_index(named, claim_objs)
    claims = [_claim_row(c, bindings, index) for c in claim_objs]
    narrative = chain.narrative
    return {
        "draft_id": chain.draft.draft_id,
        "draft_revision": chain.draft.draft_revision,
        "draft_schema_version": chain.draft.schema_version,
        "section_result_id": None if result is None else result.section_result_id,
        "result_status": None if result is None else str(result.status),
        "result_schema_version": None if result is None else result.schema_version,
        "markdown": "" if result is None else str(result.markdown),
        "markdown_fingerprint": "" if result is None else str(result.markdown_fingerprint),
        "claim_total": len(claims),
        "claims": claims,
        # `cite_…` → 人读引用行（本节引用全集；句子/段落/表格里的 `citation_ids` 都来查它）。
        "citation_index": {k: dict(v) for k, v in index.items()},
        "narrative": None if narrative is None else {
            "narrative_id": str(narrative.narrative_id),
            "draft_revision": str(narrative.draft_revision),
            "paragraph_total": len(tuple(narrative.paragraphs or ())),
            # 逐**句**给出正文与引用：`NarrativeSentence` 才是「每句引用」的落点（段落级的
            # `claim_ids` 只是句并集的复核字段，不是引用本身）。
            "paragraphs": [
                {"paragraph_id": str(p.paragraph_id), "index": int(p.index),
                 "topic_ids": [str(t) for t in tuple(p.topic_ids or ())],
                 "claim_ids": [str(c) for c in tuple(p.claim_ids or ())],
                 "citation_ids": [str(c) for c in tuple(p.citation_ids or ())],
                 "reader_source_notes": _reader_source_notes(
                     tuple(p.citation_ids or ()), named),
                 "sentences": [
                     {"sentence_id": str(s.sentence_id), "index": int(s.index),
                      "sentence_kind": str(s.sentence_kind), "text": str(s.text),
                      "claim_ids": [str(c) for c in tuple(s.claim_ids or ())],
                      "citation_ids": [str(c) for c in tuple(s.citation_ids or ())],
                      # 这一句的引用**逐条译成人读行**（中文来源 + 复核面记号）；译不出来的
                      # 标 `unresolved`——「这一句凭什么这么说」不能只剩一串 `cite_…`。
                      "citations": _citation_rows(tuple(s.citation_ids or ()), index),
                      "reader_source_notes": _reader_source_notes(
                          tuple(s.citation_ids or ()), named),
                      "context_binding_ids": [str(b) for b in tuple(s.context_binding_ids or ())],
                      "numeric_tokens": [str(t) for t in tuple(s.numeric_tokens or ())],
                      "connector": s.connector}
                     for s in tuple(p.sentences or ())]}
                for p in tuple(narrative.paragraphs or ())],
            "table_total": len(tuple(narrative.tables or ())),
            "tables": [
                {"table_id": str(t.table_id), "caption": str(t.caption),
                 "header": [str(h) for h in tuple(t.header or ())],
                 "row_total": len(tuple(t.rows or ())),
                 "entity_scope": str(t.entity_scope), "unit": str(t.unit),
                 "period": str(t.period),
                 "claim_ids": [str(c) for c in tuple(t.claim_ids or ())],
                 "citation_ids": [str(c) for c in tuple(t.citation_ids or ())],
                 "citations": _citation_rows(tuple(t.citation_ids or ()), index),
                 "reader_source_notes": _reader_source_notes(
                     tuple(t.citation_ids or ()), named)}
                for t in tuple(narrative.tables or ())],
            "context_binding_ids": [str(b) for b in tuple(narrative.context_binding_ids or ())],
        },
        "accepted_binding_total": len(chain.accepted_bindings),
        "unresolved_total": 0 if result is None else len(tuple(result.unresolved or ())),
        "unresolved": [] if result is None else [_unresolved_row(u)
                                                 for u in tuple(result.unresolved or ())],
        "final_sentence_decision_total": len(chain.final_sentence_decisions),
    }


def _blocking_reasons(*, evaluation: Mapping | None, accepted: Mapping | None,
                      assembly_error: str) -> list[str]:
    """逐条阻断原因（**只**从持久化对象派生，不另作判断）。

    三个来源，彼此不互推：

      * 章级评估的 blocking 级 issue（`section_evaluations.json` 的 `evaluation.issues`）；
      * 结果里带 `blocking_effects` 的未解决项（`SectionResult.unresolved`）；
      * 整本报告被拒的原因（`acceptance_report.json` 的 `assembly_error`）。
    """
    reasons: list[str] = []
    if assembly_error:
        reasons.append(f"整本报告被拒：{assembly_error}")
    for issue in tuple((evaluation or {}).get("issues") or ()):
        if not isinstance(issue, dict) or str(issue.get("severity")) != "blocking":
            continue
        reasons.append(f"章级评估 blocking 规则 {issue.get('rule_id')!r}："
                       f"{str(issue.get('detail'))[:300]}")
    decision = str((evaluation or {}).get("decision") or "")
    if decision in ("REWORK", "BLOCKED", "FAILED"):
        reasons.append(f"章级评估结论为 {decision!r}："
                       "它不是可发布状态（正式组装器据此拒绝组装本节）。")
    for item in tuple((accepted or {}).get("unresolved") or ()):
        effects = tuple(item.get("blocking_effects") or ())
        if effects:
            reasons.append(f"缺口 {item.get('reason_code')}（{item.get('unresolved_id')}）"
                           f"声明阻断效果 {list(effects)}：{str(item.get('detail'))[:300]}")
    return reasons


def _rejected_column(rejections: Mapping, section_id: str) -> dict:
    """`rejected_pre_gate` 栏：被拒的门前草稿（`pgr-1` 留存），逐字未核验。

    留存**只有一处来源**（`ProposalSetRejectionRecord.retained_pre_gate`）：本件不重新渲染、
    不改写、也不把它缝进 `accepted`。留存记为 `none` 时是「没有可留存的东西」（事实），
    不是「没去查」——两种情形在这里分开报。
    """
    from sections import pack_writer as PW

    label = str(getattr(PW, "PRE_GATE_RETENTION_LABEL", LABEL_FALLBACK))
    version = str(getattr(PW, "PRE_GATE_RETENTION_VERSION", ""))
    empty_basis = str(getattr(PW, "PRE_GATE_RETENTION_EMPTY_BASIS", "none"))
    rounds: list[dict] = []
    for row in tuple((rejections.get("sections") or {}).get(section_id) or ()):
        if not isinstance(row, dict):
            rounds.append({"unreadable": f"拒绝记录不是对象（得到 {type(row).__name__}）"})
            continue
        kept = row.get("retained_pre_gate")
        basis = kept.get("basis") if isinstance(kept, dict) else None
        batches = [dict(b) for b in ((kept or {}).get("batches") or ())] \
            if isinstance(kept, dict) else []
        rounds.append({
            "attempt": row.get("attempt"),
            "rejection_kind": row.get("rejection_kind"),
            "rejection_detail": row.get("rejection_detail"),
            "retained_basis": basis,
            "retained_prose_unit_total": (kept or {}).get("prose_unit_total")
            if isinstance(kept, dict) else None,
            "retained_batches": batches,
            # 人读要能**读到被拒的门前正文本身**，而不只是「有几个单元」。逐批摊成带坐标的
            # 行（批次 → 单元 → 逐字正文）：本栏是「未核验、不可发布」的定性来源，读不到正文
            # 这一栏就只剩一个计数，读者只能凭数字相信「当时确实写过东西」。
            "retained_prose_units": [
                {"batch_id": str(b.get("batch_id")), "label": str(b.get("label")),
                 "aspect_ids": [str(a) for a in (b.get("aspect_ids") or ())],
                 "prose_unit_id": str(u.get("prose_unit_id")), "index": u.get("index"),
                 "text": _text(u.get("text")),
                 "source_member_refs": [str(m) for m in (u.get("source_member_refs") or ())],
                 "source_fact_refs": [str(f) for f in (u.get("source_fact_refs") or ())]}
                for b in batches if isinstance(b, dict)
                for u in (b.get("prose_units") or ()) if isinstance(u, dict)],
        })
    with_content = [r for r in rounds
                    if r.get("retained_basis") and r["retained_basis"] != empty_basis]
    return {
        "present": bool(with_content),
        "note": COLUMN_NOTES["rejected_pre_gate"],
        "label": label,
        "retention_version": version,
        "round_total": len(rounds),
        "rounds_with_content": len(with_content),
        "rounds": rounds,
        "detail": ("被拒的轮次里留有门前已写出来的草稿正文（逐轮见下）。"
                   if with_content else
                   "本次没有任何一轮留下门前草稿正文：这是「没有可留存的东西」，"
                   "不是「读不回来」。"),
    }


def _follow_up_block(follow_ups: Mapping, section_id: str) -> dict:
    """`sections[*].follow_up`：本节的**补件去向**（`follow-up-needs/*`，待裁决诉求）。

    **独立于三栏**：三栏问「本节有没有内容」，这一块问「模型提过什么补件诉求、它们现在在哪」。
    两者可以同时为空，也可以一个有内容一个没诉求——合并成一栏就会让「没内容」看起来像
    「没诉求」。

    三种记录分列，**不得合并**（`FOLLOW_UP_ROW_KINDS`）：它们分别是「这一节整个没写出来」
    「诉求填错了」「本节写出来了但某条申请自己不成立」三种现场。
    """
    rows = (follow_ups.get("sections") or {}).get(section_id) or {}
    if not isinstance(rows, Mapping):
        rows = {}
    status = _text(follow_ups.get("status"))
    version = _text(follow_ups.get("schema_version"))

    def _rows(value: Any) -> list[dict]:
        return [dict(r) for r in value if isinstance(r, Mapping)] if isinstance(value, list) else []

    pending = _rows(rows.get("follow_up_needs"))
    untypeable = _rows(rows.get("follow_up_untypeable"))
    rejected = _rows((follow_ups.get("follow_up_rejected_applications") or {}).get(section_id))
    adjudicated = (follow_ups.get("adjudicated_runs") or {}).get(section_id) or 0
    present = bool(pending or untypeable or rejected)

    pending_rows = [{
        "kind": "pending_need",
        "need_id": _text(n.get("need_id")),
        "statement": _text(n.get("statement")),
        "target_requirement_id": _text(n.get("target_requirement_id")),
        "topic_id": _text(n.get("topic_id")), "question_id": _text(n.get("question_id")),
        "aspect_id": _text(n.get("aspect_id")),
        "requiredness": _text(n.get("requiredness")),
        "expected_source_class": _text(n.get("expected_source_class")),
        "budget_hint": _text(n.get("budget_hint")),
        "section_draft_revision": _text(n.get("section_draft_revision")),
        "contract_authorized_scope": [str(x) for x in (n.get("contract_authorized_scope") or ())],
    } for n in pending]

    def _rejected_rows(items: list[dict], kind: str) -> list[dict]:
        return [{"kind": kind,
                 "attempt": r.get("attempt"), "spec_index": r.get("spec_index"),
                 "statement": _text(r.get("statement")),
                 "topic_id": _text(r.get("topic_id")),
                 "question_id": _text(r.get("question_id")),
                 "aspect_id": _text(r.get("aspect_id")),
                 "code": _text(r.get("code")), "reason": _text(r.get("reason"))}
                for r in items]

    return {
        "present": present,
        "note": FOLLOW_UP_NOTE,
        "status": status,
        "schema_version": version,
        "source": follow_ups.get("source"),
        "row_kinds": list(FOLLOW_UP_ROW_KINDS),
        "pending": pending_rows,
        "pending_total": len(pending_rows),
        "untypeable": _rejected_rows(untypeable, "untypeable_raw"),
        "untypeable_total": len(untypeable),
        "rejected_applications": _rejected_rows(rejected, "rejected_application"),
        "rejected_application_total": len(rejected),
        # 已由 Harness 裁决并执行的补件运行数是**另一套身份**：这里只报计数，且明说它不在本块。
        "adjudicated_run_total": int(adjudicated or 0),
        "detail": (
            f"本节提出过补件诉求：待裁决 {len(pending_rows)} 条、不成立的原始诉求 "
            f"{len(untypeable)} 条、已写出内容里自己不成立的申请 {len(rejected)} 条。"
            "全部**未裁决、未执行**（见 `note`）。" if present else
            "本节**没有**任何补件诉求记录：这是「产物里确实没有这一节的诉求」"
            "（`follow_up_needs.json` 里没有本节），不是「本次没去查」。"),
    }


def _no_content_columns(rejections: Mapping, section_id: str, *, stub: bool,
                        reason: str) -> dict:
    """没有 `accepted` 内容时三栏的形状（**三栏都在**，各自说清为什么是空的）。

    三栏必须**同时在场**，只是 `present=False`：少一栏会被读成「这一栏不适用」，而真相是
    「这一栏本次读不出来」。`rejected_pre_gate` 仍然照读——被拒的门前草稿与本节有没有成形是
    两件事。
    """
    return {
        "accepted": {"present": False, "note": COLUMN_NOTES["accepted"], "detail": reason},
        "rejected_pre_gate": _rejected_column(rejections, section_id),
        "stub_organizer": {
            "present": False, "note": COLUMN_NOTES["stub_organizer"],
            "detail": ("本节被声明为替身组织器节，但本次没有读回它的正文。"
                       if stub else
                       "本次运行**没有**声明本节使用替身组织器，且本节没有可读回的正文："
                       "本栏不适用。"),
        },
    }


PROSE_ORIGIN_NOTES = {
    "model": "来源按**调用方声明**回声：本节正文来自真实模型调用（本件**不**核验这一点）。",
    "stub_organizer": ("本节正文由**替身组织器**产出（调用方声明）：**不是**真实模型成稿，"
                       "只证明链路走通。"),
    "not_applicable": ("本件**不掌握**本节正文的来源：调用方没有声明它来自真实模型调用还是"
                       "替身组织器；本节也可能根本没有正文。把「没声明」读成「模型成稿」"
                       "正是本件要防的越权断言。"),
}


def _section_row(*, section_id: str, title: str,
                 stub_sections: frozenset[str], model_sections: frozenset[str],
                 draft_id: str | None, draft_error: str,
                 chain, chain_error: str, evaluations: Mapping, rejections: Mapping,
                 follow_ups: Mapping, section_errors: Mapping, assembly_error: str) -> dict:
    evaluation = (evaluations.get(section_id) or {}).get("evaluation") or None
    if not isinstance(evaluation, dict):
        evaluation = None
    error = _text(section_errors.get(section_id))
    prose_origin = ("stub_organizer" if section_id in stub_sections
                    else ("model" if section_id in model_sections else "not_applicable"))
    row: dict[str, Any] = {
        "section_id": section_id,
        "title": title,
        "prose_origin": prose_origin,
        "prose_origin_note": PROSE_ORIGIN_NOTES[prose_origin],
        "section_error": error,
        "evaluation": None if evaluation is None else {
            "decision": evaluation.get("decision"),
            "evaluation_id": evaluation.get("evaluation_id"),
            "rules_version": evaluation.get("rules_version"),
            "issues": [dict(i) for i in (evaluation.get("issues") or ())],
        },
        "columns": {},
        # 补件去向**独立于三栏**，因此无内容/读不回来那几条路径上它照样读（它问的是另一个
        # 问题：本节有没有内容，与模型提过什么诉求，是两件事）。
        "follow_up": _follow_up_block(follow_ups, section_id),
        "first_missing_object": None,
        "detail": "",
    }

    if draft_error:
        # 库本身读不回来 ⇒ 整节的缺失判定**不可信**，不得退化成 unavailable。
        row["outlet_status"] = "store_unreadable"
        row["detail"] = (f"章节链库不可读（{draft_error}）：本节有没有内容**无从判定**。"
                         "这是「读不回来」，**不是**「本节没有内容」。")
        row["columns"] = _no_content_columns(rejections, section_id, stub=section_id in stub_sections,
                                            reason="章节链库不可读：本节内容能不能读出**无从判定**。")
        row["blocking_reasons"] = _blocking_reasons(
            evaluation=evaluation, accepted=None, assembly_error=assembly_error)
        return row

    if draft_id is None:
        row["outlet_status"] = "unavailable"
        row["first_missing_object"] = MISSING_OBJECT_ORDER[0]
        # 本节错误**只印一次**（渲染侧有独立一行）：判读句里再写一遍会让同一件事在同一份
        # 产物的两处出现两种口径，读者就得去猜哪一处才算数。
        row["detail"] = (
            "本节**没有**落库的 `SectionDraft`，因此也没有门侧决定、`SectionResult` 与正文："
            f"可持久化内容为**零**。第一个缺失对象是 `{MISSING_OBJECT_ORDER[0]}`。")
        row["columns"] = _no_content_columns(
            rejections, section_id, stub=section_id in stub_sections,
            reason="本节没有落库的 `SectionDraft`：既无正文也无 Claim 可读。")
        row["blocking_reasons"] = _blocking_reasons(
            evaluation=evaluation, accepted=None, assembly_error=assembly_error)
        return row

    if chain_error or chain is None:
        row["outlet_status"] = "readback_failed"
        row["first_missing_object"] = "SectionResult"
        row["detail"] = (f"本节有落库的草稿行（`{draft_id}`），但 current 链**读不回来**"
                         f"（{chain_error}）：内容**可能存在但本次读不出**，不得读成本节没有内容。")
        row["columns"] = _no_content_columns(
            rejections, section_id, stub=section_id in stub_sections,
            reason="current 链读不回来：本节内容**可能存在但本次读不出**。")
        row["blocking_reasons"] = _blocking_reasons(
            evaluation=evaluation, accepted=None, assembly_error=assembly_error)
        return row

    accepted = _accepted_column(chain)
    rejected = _rejected_column(rejections, section_id)
    has_accepted = bool(accepted["markdown"] or accepted["claim_total"])
    row["outlet_status"] = "content_readback"
    row["detail"] = (
        "本节有**读回来的**已过门内容（见 `accepted` 栏）：Claim "
        f"{accepted['claim_total']} 条、`SectionResult` 状态 "
        f"`{accepted['result_status']}`、正文 {len(accepted['markdown'])} 字符、"
        f"缺口 {accepted['unresolved_total']} 条。**它不等于本节通过**：状态照印，"
        "缺口与阻断原因照列。")
    row["columns"] = {
        "accepted": {"present": has_accepted, "note": COLUMN_NOTES["accepted"],
                     "detail": ("本栏读自持久化 current 链（身份由读回侧重算核验）。"
                                if has_accepted else
                                "本栏为空：本节有 Draft/Result 对象，但既无正文也无 Claim。"),
                     "content": accepted},
        "rejected_pre_gate": rejected,
        "stub_organizer": {
            "present": section_id in stub_sections, "note": COLUMN_NOTES["stub_organizer"],
            "detail": ("本节正文由替身组织器产出（调用方声明）——`accepted` 栏的正文因此"
                       "**不是**真实模型成稿，只证明链路走通。"
                       if section_id in stub_sections else
                       "本次运行**没有**声明本节使用替身组织器：本栏不适用。"),
        },
    }
    row["blocking_reasons"] = _blocking_reasons(
        evaluation=evaluation, accepted=accepted, assembly_error=assembly_error)
    if not has_accepted:
        row["detail"] += "（**注意**：本节读回来的 `accepted` 栏为空——本节有 Draft/Result 对象，" \
                         "但既无正文也无 Claim。）"
    return row


def build_preview(run_dir: Path, *, sections: Sequence[str], titles: Mapping[str, str] | None = None,
                  source_kind: str = "real_run", stub_sections: Iterable[str] = (),
                  model_sections: Iterable[str] = (), read_at: str | None = None) -> dict:
    """构建不可发布预览的载荷（**只读**；它自己不写盘，写盘由 `write_preview` 决定去处）。

    `sections` 由调用方给出（本件不自己发明节序：节序是运行侧的事实，不是本件的常量）。
    `source_kind` / `stub_sections` / `model_sections` 是**声明**，不是推断——见模块文档：
    没有声明的节一律落 `prose_origin="not_applicable"`，**不**默认成 `model`。
    """
    run_dir = Path(run_dir)
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"source_kind 必须是 {list(SOURCE_KINDS)} 之一，得到 {source_kind!r}")
    order = [str(s) for s in sections]
    stub = frozenset(str(s) for s in stub_sections)
    model = frozenset(str(s) for s in model_sections)
    unknown_stub = sorted(stub - set(order))
    if unknown_stub:
        raise ValueError(f"stub_sections 里有不属于本次节序的节：{unknown_stub}")
    unknown_model = sorted(model - set(order))
    if unknown_model:
        raise ValueError(f"model_sections 里有不属于本次节序的节：{unknown_model}")
    both = sorted(stub & model)
    if both:
        # 「同一节的正文既是模型成稿又是替身产出」是自相矛盾的声明：不是取其一，是拒收。
        raise ValueError(f"同一节不得同时声明为 `model` 与 `stub_organizer`：{both}")

    titles = dict(titles or _titles_from_run(run_dir))
    for section_id in order:
        titles.setdefault(section_id, "")

    evaluations, eval_reason = _read_json(run_dir / "section_evaluations.json")
    rejections, rej_reason = _read_json(run_dir / "proposal_set_rejections.json")
    report, report_reason = _read_json(run_dir / "acceptance_report.json")
    follow_ups, follow_up_reason = _read_json(run_dir / FOLLOW_UP_SOURCE_FILE)

    drafts, draft_error = _draft_rows(run_dir / "section_chain_v2.db")
    section_errors = (report or {}).get("section_errors") or {}
    if not isinstance(section_errors, dict):
        section_errors = {}
    assembly_error = _text((report or {}).get("assembly_error"))
    report_version = (report or {}).get("report_version")

    rows: list[dict] = []
    for section_id in sections:
        draft_id = drafts.get(str(section_id))
        chain = chain_error = None
        if draft_id is not None:
            chain, chain_error = _load_chain(run_dir / "section_chain_v2.db", draft_id)
        rows.append(_section_row(
            section_id=str(section_id), title=str(titles.get(str(section_id), "")),
            stub_sections=stub, model_sections=model,
            draft_id=draft_id, draft_error=draft_error, chain=chain,
            chain_error=str(chain_error or ""),
            evaluations=evaluations or {}, rejections=rejections or {},
            follow_ups=({**(follow_ups or {}), "source": (
                FOLLOW_UP_SOURCE_FILE if follow_ups is not None else follow_up_reason)}),
            section_errors=section_errors, assembly_error=assembly_error))

    accepted_totals = [r["columns"]["accepted"].get("content") or {} for r in rows
                       if r["outlet_status"] == "content_readback"]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "readback",
        "publishable": False,
        "label": _label(),
        "generated_at": _text((report or {}).get("generated_at")),
        "read_at": read_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_dir": run_dir.name,
        "source_kind": source_kind,
        "source_kind_note": SOURCE_KIND_NOTES[source_kind],
        "stub_organizer_sections": sorted(stub),
        #: 逐档**回声调用方的声明**：哪些节被声明为替身、哪些被声明为模型、以及哪些节**有内容
        #: 却没有声明来源**。第三项是这张表真正的用处——它把「没声明」显式地摆出来，而不是让它
        #: 悄悄落在缺省档上被读成「已核验为模型成稿」。
        "declared_prose_origins": {
            "model": sorted(model),
            "stub_organizer": sorted(stub),
            "undeclared_with_content": sorted(
                r["section_id"] for r in rows
                if r["prose_origin"] == "not_applicable"
                and r["outlet_status"] == "content_readback"
                and (r["columns"]["accepted"].get("content") or {}).get("claim_total")),
        },
        "refusal": {
            "report_assembled": report_version is not None,
            "assembly_error": assembly_error,
            "report_version": report_version,
            "source": "acceptance_report.json" if report is not None else report_reason,
        },
        "m930_4_boundary": {
            "independent_review_performed": False,
            "formal_report_version": report_version,
            "note": ("本件**不是** M930-4 的独立审查：它不产出任何 `ReviewIssue`，也不产出 "
                     "Assurance 状态。没有正式 `report_version` 时，M930-4 不得声称已对正式"
                     "报告完成审查。"),
        },
        "content_columns": list(CONTENT_COLUMNS),
        "column_notes": dict(COLUMN_NOTES),
        "outlet_statuses": list(OUTLET_STATUSES),
        "source_kinds": list(SOURCE_KINDS),
        "prose_origins": list(PROSE_ORIGINS),
        "forbidden_claims": list(FORBIDDEN_CLAIMS),
        "evaluations_source": ("section_evaluations.json" if evaluations is not None
                               else eval_reason),
        "rejections_source": ("proposal_set_rejections.json" if rejections is not None
                              else rej_reason),
        "follow_up_source": (FOLLOW_UP_SOURCE_FILE if follow_ups is not None
                             else follow_up_reason),
        "follow_up_note": FOLLOW_UP_NOTE,
        "follow_up_row_kinds": list(FOLLOW_UP_ROW_KINDS),
        "citation_readings": list(CITATION_READINGS),
        "citation_field_labels": dict(CITATION_FIELD_LABELS),
        "support_field_labels": dict(SUPPORT_FIELD_LABELS),
        "sections": rows,
        "totals": {
            "section_total": len(rows),
            "content_readback": sum(1 for r in rows if r["outlet_status"] == "content_readback"),
            "unavailable": sum(1 for r in rows if r["outlet_status"] == "unavailable"),
            "readback_failed": sum(1 for r in rows if r["outlet_status"] == "readback_failed"),
            "store_unreadable": sum(1 for r in rows if r["outlet_status"] == "store_unreadable"),
            "claim_total": sum(int(a.get("claim_total") or 0) for a in accepted_totals),
            "unresolved_total": sum(int(a.get("unresolved_total") or 0) for a in accepted_totals),
            "rejected_pre_gate_rounds": sum(
                int(r["columns"]["rejected_pre_gate"].get("rounds_with_content") or 0)
                for r in rows),
            # 补件去向**逐类**计数（三类不得合并成一个数：「整节没写出来」与「写出来了但某条
            # 申请自己不成立」是两种现场）。
            "follow_up_pending": sum(int(r["follow_up"]["pending_total"]) for r in rows),
            "follow_up_untypeable": sum(int(r["follow_up"]["untypeable_total"]) for r in rows),
            "follow_up_rejected_applications": sum(
                int(r["follow_up"]["rejected_application_total"]) for r in rows),
            "follow_up_adjudicated_runs": sum(
                int(r["follow_up"]["adjudicated_run_total"]) for r in rows),
        },
        "note": (
            f"**{_label()}**。本件是**受阻章节**的只读出口：整本报告因某节未产出/未过门而被拒时，"
            "把**每一节**已经过门的内容、被拒的门前草稿、以及逐条阻断原因与缺口分别列出，"
            "供人判断「这一轮到底有没有内容可读」。它**不是** `SectionResult`、**不是**正式预览、"
            "**不是**任何判定对象的替代：不进正文、不进 `report_preview.md`，没有任何门读它，"
            "它也**不放宽**任何门——整本报告仍然是被拒的。它是**只读**读回：正文与引用一字未改。"),
    }
    payload["preview_fingerprint"] = _fingerprint(payload)
    return payload


def _label() -> str:
    """不可发布标注：**只有一处定义**在写手侧（留存对象自己带），这里取同一个串。

    取不到写手侧常量时退回到本文件里那个**同字面**的兜底——两处措辞不一致会让
    「哪些话是本件的定性」在同一份产物的两个渲染里各说各话，而那正是「不得把它读成正文」
    这条纪律的落点。
    """
    return _writer_label()


def _writer_label() -> str:
    from sections import pack_writer as PW

    return str(getattr(PW, "PRE_GATE_RETENTION_LABEL", LABEL_FALLBACK))


def _fingerprint(payload: Mapping) -> str:
    """载荷自身的内容指纹（**排除**指纹字段与读回时刻，两者都不是内容）。"""
    body = {k: v for k, v in payload.items()
            if k not in ("preview_fingerprint", "read_at")}
    blob = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return "bsp_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def _citation_lines(cite: Mapping, *, indent: str) -> list[str]:
    """一条引用的**人读行**：中文来源（若能译）+ 内部明细（复核面）。

    引用在库里是 `cite_…` 单向散列，读者面看到的必须是中文来源。这里把**两件事都印出来**：
    译得出的印中文来源并且明细原样保留（便于核对是哪条事实）；译不出的明写 `reviewer_only`
    并说明生产渲染器有意不印它——**不**用「没印出来」冒充「这一条不存在」。
    """
    head = f"{indent}引用 `{cite['citation_id']}`（`{cite['reading']}`）"
    named = cite.get("reader_named_source")
    if named:
        head += f" → **{named}**"
    out = [head, f"{indent}  - 明细：{cite['line']}"]
    # 同一 `citation_id` 撞出两条明细：两条**并列**印出，不取其一（身份碰撞要看得见）。
    for extra in tuple(cite.get("collision") or ()):
        out.append(f"{indent}  - 另一条明细：{extra}")
    return out


def render_md(payload: Mapping) -> str:
    """人读版（与 `blocked_section_preview.json` 同一次读数的两个渲染）。"""
    if payload.get("status") == "build_failed":
        return "\n".join([f"# M930-3 受阻章节不可发布预览 · **{payload.get('label')}**", "",
                          f"> {payload.get('note', '')}", "",
                          "（本次预览没有构建成功，没有可读的逐节内容。）", ""])
    lines = [f"# M930-3 受阻章节不可发布预览 · **{payload['label']}**", "",
             f"> {payload['note']}", "",
             f"机器可读版在 `blocked_section_preview.json`；两者出自同一次读数"
             f"（`{payload['schema_version']}`）。载荷指纹 `{payload['preview_fingerprint']}`。", "",
             f"- 来源：`{payload['source_kind']}` —— {payload['source_kind_note']}",
             f"- 整本报告是否组装：`{payload['refusal']['report_assembled']}`"
             + (f"（拒发原因：{payload['refusal']['assembly_error']}）"
                if payload['refusal']['assembly_error'] else ""),
             f"- 正式 `report_version`：`{payload['refusal']['report_version']}`",
             f"- 逐节：可读回内容 {payload['totals']['content_readback']} 节 / "
             f"无内容 {payload['totals']['unavailable']} 节 / 读不回来 "
             f"{payload['totals']['readback_failed']} 节 / 库不可读 "
             f"{payload['totals']['store_unreadable']} 节",
             f"- 已过门 Claim 合计 {payload['totals']['claim_total']} 条；"
             f"缺口合计 {payload['totals']['unresolved_total']} 条；"
             f"被拒门前草稿有内容的轮次合计 {payload['totals']['rejected_pre_gate_rounds']}",
             f"- 补件去向（**待裁决，未执行，不是 gap**）：待裁决诉求 "
             f"{payload['totals'].get('follow_up_pending', 0)} 条 / 不成立的原始诉求 "
             f"{payload['totals'].get('follow_up_untypeable', 0)} 条 / 已写内容里自己不成立的"
             f"申请 {payload['totals'].get('follow_up_rejected_applications', 0)} 条",
             ""]
    declared = payload.get("declared_prose_origins") or {}
    lines += [f"- 正文来源声明：模型成稿 "
              f"{declared.get('model') or '（无）'}；替身组织器 "
              f"{declared.get('stub_organizer') or '（无）'}；**有内容但未声明来源** "
              f"{declared.get('undeclared_with_content') or '（无）'}", ""]
    if payload["stub_organizer_sections"]:
        lines += [f"- **替身组织器节**：{payload['stub_organizer_sections']} —— "
                  "这些节的正文**不是**真实模型成稿，只证明链路走通。", ""]
    lines += ["## 三栏是什么（不得混成一篇「已核验正文」）", ""]
    for name in payload["content_columns"]:
        lines.append(f"- `{name}`：{payload['column_notes'][name]}")
    lines += ["", "## 三栏之外，本件另答两个问题（**它们不是第四栏内容**）", "",
              f"- 引用读数档 `{payload['citation_readings']}`：`reader_named` 是生产渲染器"
              "会印给读者的中文来源；`reviewer_only` 是生产渲染器**有意不印**的内部记号"
              "（`pwr-4`），本件在**复核面**列出并标注；`unresolved` 是「本件读回的 Claim 集里"
              "没有这条 id」，**不代表它不存在**。三者不得合并。",
              f"- 补件去向（来源 `{payload['follow_up_source']}`）："
              f"{payload['follow_up_note']}",
              f"- 引用字段中文名 `{payload['citation_field_labels']}`；"
              f"支撑边字段中文名 `{payload['support_field_labels']}`。"]
    lines += ["", "## 本件**不是**什么", ""]
    lines += [f"- {item}" for item in payload["forbidden_claims"]]
    lines.append("")
    for row in payload["sections"]:
        lines += [f"## {row['section_id']} · {row['title'] or '(无标题)'}", "",
                  f"- 出口状态：`{row['outlet_status']}`"
                  + (f"；第一个缺失对象 `{row['first_missing_object']}`"
                     if row["first_missing_object"] else ""),
                  f"- 正文来源：`{row['prose_origin']}` —— {row['prose_origin_note']}",
                  f"- 判读：{row['detail']}"]
        if row.get("section_error"):
            lines.append(f"- 本节错误：`{row['section_error']}`")
        if row.get("evaluation"):
            ev = row["evaluation"]
            lines.append(f"- 章级评估：`{ev['decision']}`（`{ev['evaluation_id']}`，"
                         f"规则 `{ev['rules_version']}`）")
        lines.append("")
        reasons = row.get("blocking_reasons") or []
        if reasons:
            lines += ["### 逐条阻断原因", ""]
            lines += [f"{i}. {r}" for i, r in enumerate(reasons, 1)]
            lines.append("")
        accepted = row["columns"]["accepted"]
        lines += ["### 栏一 `accepted`（已过门内容；**未核验、不可发布**）", "",
                  f"- {accepted.get('detail', accepted['note'])}"]
        content = accepted.get("content")
        if not content:
            lines += ["- **本栏为空**：本次没有从持久化对象读回本节的已过门内容。", ""]
        else:
            lines += ["", f"- `SectionDraft` `{content['draft_id']}`（rev "
                      f"`{content['draft_revision']}`，wire `{content['draft_schema_version']}`）",
                      f"- `SectionResult` `{content['section_result_id']}`；状态 "
                      f"**`{content['result_status']}`**",
                      f"- Claim {content['claim_total']} 条；accepted binding "
                      f"{content['accepted_binding_total']} 条；缺口 {content['unresolved_total']} 条；"
                      f"最终句决定 {content['final_sentence_decision_total']} 条", ""]
            if content["markdown"]:
                lines += ["#### 本节正文（读自 `SectionResult.markdown`，一字未改）", "",
                          "````text", content["markdown"], "````", ""]
            narrative = content.get("narrative")
            if narrative and narrative["paragraph_total"]:
                lines += [f"#### final Narrative `{narrative['narrative_id']}`"
                          f"（逐句 + **每句引用译成人读行**；`claim_ids` 是句级声明，"
                          f"`citation_ids` 是句级引用）", ""]
                lines += ["`reading` 两档：`reader_named` = 生产渲染器会印给读者的中文来源；",
                          "`reviewer_only` = 生产渲染器**有意不印**（`pwr-4`），此处按复核面列出；",
                          "`unresolved` = 本件读回的 Claim 集里没有这条 id（不等于它不存在）。", ""]
                for para in narrative["paragraphs"]:
                    lines.append(f"- 段落 `{para['paragraph_id']}`（topic "
                                 f"{para['topic_ids']}）")
                    for sent in para["sentences"]:
                        lines.append(f"  - `{sent['sentence_kind']}` **{sent['text']}**")
                        lines.append(f"    - claims={sent['claim_ids']} "
                                     f"citations={sent['citation_ids']}"
                                     + (f" context={sent['context_binding_ids']}"
                                        if sent["context_binding_ids"] else ""))
                        for cite in sent.get("citations") or ():
                            lines += _citation_lines(cite, indent="      - ")
                        if sent.get("reader_source_notes"):
                            lines.append("      - **读者面引用来源**（生产渲染器"
                                         "`citation_source_notes` 的中文行）："
                                         + "；".join(sent["reader_source_notes"]))
                    if para.get("reader_source_notes"):
                        lines.append("    - 段落级读者面引用来源：" +
                                     "；".join(para["reader_source_notes"]))
                for tbl in narrative["tables"]:
                    lines.append(f"- 叙述型表格 `{tbl['table_id']}`（{tbl['caption']}；"
                                 f"表头 {tbl['header']}；{tbl['row_total']} 行；单位 "
                                 f"`{tbl['unit']}`；期间 `{tbl['period']}`；主体 "
                                 f"`{tbl['entity_scope']}`）")
                    for cite in tbl.get("citations") or ():
                        lines += _citation_lines(cite, indent="  - ")
                    if tbl.get("reader_source_notes"):
                        lines.append("  - **读者面引用来源**：" +
                                     "；".join(tbl["reader_source_notes"]))
                lines.append("")
            if content["claims"]:
                lines += ["#### 逐条 Claim 与逐条支撑边（支撑边给出材料/权威/定位）", ""]
                for claim in content["claims"]:
                    lines.append(f"- **`{claim['claim_id']}`**（`{claim['claim_type']}`，"
                                 f"主题 `{claim['topic_id']}`）：{claim['text']}")
                    for cite in claim.get("citations") or ():
                        lines += _citation_lines(cite, indent="  - ")
                    for edge in claim["support"]:
                        lines.append(
                            f"  - 支撑边 `{edge.get('accepted_support_binding_id')}`："
                            f"`{edge.get('support_semantics')}`/`{edge.get('support_role')}` "
                            f"authority=`{edge.get('authority_kind')}`"
                            f"({edge.get('authority_container_id')}) "
                            f"material=`{edge.get('material_id')}` "
                            f"fact=`{edge.get('fact_id') or edge.get('financial_fact_id') or edge.get('note_fact_id') or edge.get('external_fact_id')}` "
                            f"locator=`{edge.get('locator_ref')}`")
                lines.append("")
            index = content.get("citation_index") or {}
            if index:
                lines += ["#### 本节引用总表（`cite_…` → 人读行；句子/段落/表格里的 "
                          "`citation_ids` 都来查这里）", ""]
                for citation_id, cite in index.items():
                    lines += _citation_lines(dict(cite, citation_id=citation_id),
                                             indent="- ")
                lines.append("")
            if content["unresolved"]:
                lines += ["#### 缺口（权威状态原样保留）", ""]
                for item in content["unresolved"]:
                    effects = item["blocking_effects"]
                    lines.append(f"- `{item['reason_code']}`（`{item['state']}`"
                                 + (f"，阻断 {effects}" if effects else "") + f"）：{item['detail']}")
                lines.append("")
        rejected = row["columns"]["rejected_pre_gate"]
        lines += [f"### 栏二 `rejected_pre_gate`（被拒门前草稿 · "
                  f"**{rejected['label']}** · `{rejected['retention_version']}`）", "",
                  f"- {rejected['detail']}"]
        if not rejected["present"]:
            lines.append("- 本栏为**空**：本次没有留下任何被拒的门前草稿正文。")
        lines.append("")
        for rnd in rejected["rounds"]:
            if rnd.get("unreadable"):
                lines += [f"  - {rnd['unreadable']}"]
                continue
            lines.append(f"  - 第 {rnd['attempt']} 轮 · 拒绝原因 `{rnd['rejection_kind']}` · "
                         f"留存依据 `{rnd['retained_basis']}` · 门前正文单元 "
                         f"{rnd['retained_prose_unit_total']}")
            if rnd.get("rejection_detail"):
                lines.append(f"    - 拒绝说明：{rnd['rejection_detail']}")
            for unit in rnd.get("retained_prose_units") or ():
                lines.append(f"    - `{unit['prose_unit_id']}`（批 `{unit['batch_id']}`/"
                             f"`{unit['label']}`，第 {unit['index']} 段；"
                             f"出处：{unit['source_member_refs'] or unit['source_fact_refs']}）")
                lines.append(f"      > {unit['text']}")
        if rejected["rounds"]:
            lines.append("")
        stub = row["columns"]["stub_organizer"]
        lines += ["### 栏三 `stub_organizer`（替身组织器）", "",
                  f"- {stub['detail']}", ""]
        lines += _follow_up_lines(row["follow_up"])
    return "\n".join(lines)


def _follow_up_lines(block: Mapping) -> list[str]:
    """本节的**补件去向**（独立于三栏：不并入任何一栏，也不冒充 gap）。"""
    out = ["### 补件去向（待裁决诉求 · **未执行** · 不是 gap）", "",
           f"- {block['note']}",
           f"- 来源：`{block['source']}`"
           + (f"（`{block['schema_version']}`，状态 `{block['status']}`）"
              if block.get("schema_version") else ""),
           f"- 三类记录**分列**，不得合并：{block['row_kinds']}",
           f"- {block['detail']}", ""]
    if block["pending"]:
        out += ["#### 一、待裁决诉求（本节写不出来时模型提出的）", ""]
        for need in block["pending"]:
            out.append(f"- `{need['need_id']}`（必需性 `{need['requiredness']}`；"
                       f"期望来源档 `{need['expected_source_class']}`；预算提示 "
                       f"`{need['budget_hint']}`）：{need['statement']}")
            out.append(f"  - 目标要求 `{need['target_requirement_id']}`；topic "
                       f"`{need['topic_id']}` / question `{need['question_id']}` / aspect "
                       f"`{need['aspect_id']}`；草案修订 "
                       f"`{need['section_draft_revision']}`；Contract 授权范围 "
                       f"{need['contract_authorized_scope']}")
        out.append("")
    if block["untypeable"]:
        out += ["#### 二、不成立的原始诉求（连类型都没填对，**不是**有效诉求）", ""]
        for bad in block["untypeable"]:
            out.append(f"- 第 `{bad['attempt']}` 轮第 `{bad['spec_index']}` 条 · 码 "
                       f"`{bad['code']}`：{bad['statement']}（{bad['reason']}）")
        out.append("")
    if block["rejected_applications"]:
        out += ["#### 三、已写内容里自己不成立的申请（本节**有**内容，但该条申请被拒）", ""]
        for rej in block["rejected_applications"]:
            out.append(f"- 第 `{rej['attempt']}` 轮第 `{rej['spec_index']}` 条 · 码 "
                       f"`{rej['code']}`：{rej['statement']}（{rej['reason']}）")
        out.append("")
    if block["adjudicated_run_total"]:
        out.append(f"- **已裁决执行的补件运行另有 {block['adjudicated_run_total']} 次**："
                   "那是**另一套身份**，不在本块的「待裁决」里。")
        out.append("")
    return out


def write_preview(payload: Mapping, out_dir: Path) -> tuple[Path, Path]:
    """把两个渲染写进**调用方给出的**目录（本件不自己决定去处，也不改任何历史目录）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "blocked_section_preview.json"
    md_path = out_dir / "blocked_section_preview.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_md(payload), encoding="utf-8")
    return json_path, md_path


def failed_payload(reason: str) -> dict:
    """本件**自己没构建成**时的载荷：空表必须自带「这是读不回来」的定性，不得被读成没有内容。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "build_failed",
        "publishable": False,
        "label": LABEL_FALLBACK,
        "detail": reason,
        "sections": [],
        "totals": {},
        "forbidden_claims": list(FORBIDDEN_CLAIMS),
        "note": BUILD_FAILED_NOTE,
    }


def default_out_dir(run_dir: Path) -> Path:
    """CLI 的缺省写盘去处：**临时目录**，不是 run 目录。

    这是本批的硬纪律之一：离线重放**不得回写**被重放的历史目录。把缺省值定在临时目录，
    而不是「缺省写回 run 目录、重放时记得改」，才不会有一次忘记就污染历史现场。
    """
    import tempfile

    return Path(tempfile.gettempdir()) / "m930_blocked_preview" / Path(run_dir).name


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="受阻章节的不可发布预览（只读；不写被重放的 run 目录）")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--sections", default="company,financial,industry",
                    help="逗号分隔的节序（缺省即三节主链次序）")
    ap.add_argument("--source-kind", default="offline_replay", choices=list(SOURCE_KINDS),
                    help="来源档位（**必填语义**：缺省是最保守的一档 `offline_replay`）")
    ap.add_argument("--stub-sections", default="",
                    help="逗号分隔：正文由替身组织器产出的节（无则留空）")
    ap.add_argument("--model-sections", default="",
                    help="逗号分隔：正文来自真实模型调用的节。**必须显式给出**——留空只是"
                         "「没声明」，落到 `not_applicable`，不会被读成模型成稿")
    ap.add_argument("--out", default=None, type=Path,
                    help="写盘目录（缺省：系统临时目录，**不**写回 run 目录）")
    ap.add_argument("--in-place", action="store_true",
                    help="显式写回 run 目录（仅 `real_run` 档允许；`offline_replay` 一律拒绝）")
    args = ap.parse_args(argv)

    sections = [s.strip() for s in args.sections.split(",") if s.strip()]
    stub = {s.strip() for s in args.stub_sections.split(",") if s.strip()}
    model = {s.strip() for s in args.model_sections.split(",") if s.strip()}
    if args.in_place:
        if args.out is not None:
            raise SystemExit("`--in-place` 与 `--out` 互斥")
        if args.source_kind != "real_run":
            # 结构性拒绝：重放**不得**回写历史目录。这条不靠「记得别写」，靠这里拦住。
            raise SystemExit(
                f"拒绝写回 run 目录：`--source-kind={args.source_kind}` 是重放/非本次运行的产物，"
                "不得覆盖被重放的历史现场。请用 `--out` 指定一个独立目录。")
        out_dir = args.run_dir
    else:
        out_dir = args.out or default_out_dir(args.run_dir)

    # **声明**的对错是用法错误，不是读回失败：它必须在 `try` **之外**判，否则「同一节既声明成
    # 模型成稿又声明成替身产出」会被降级成一句 `build_failed`，读的人会以为库读不回来。
    order = set(sections)
    for name, values in (("--stub-sections", stub), ("--model-sections", model)):
        unknown = sorted(values - order)
        if unknown:
            raise SystemExit(f"{name} 里有不属于本次节序的节：{unknown}")
    if stub & model:
        raise SystemExit(f"同一节不得同时声明为 model 与 stub_organizer：{sorted(stub & model)}")

    try:
        payload = build_preview(args.run_dir, sections=sections, source_kind=args.source_kind,
                                stub_sections=stub, model_sections=model)
    except Exception as exc:  # noqa: BLE001 —— 本件自己失败也要留下可读的一句
        payload = failed_payload(f"{type(exc).__name__}: {str(exc)[:400]}")
    json_path, md_path = write_preview(payload, out_dir)
    print(json.dumps({"status": payload.get("status"),
                      "outlet_statuses": {r["section_id"]: r["outlet_status"]
                                          for r in payload.get("sections", ())},
                      "totals": payload.get("totals", {}),
                      "json": str(json_path), "md": str(md_path)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""Eval: M930-3 指令 E 第 4 项——r7b 留存原始返回的**离线重放**：四条坏候选出口逐项取证。

用法: python -X utf8 -m evals.test_m930_3_r7b_replay

## 本模块证明什么

r7b 那一轮的公司节**整节失败**，失败原因写在它自己的记录里：两束候选提案被拒
（`proposal_set_rejections.json` → `sections.company`，两条 `schema_invalid`）。本模块拿**这两束
留存原始返回**（`logs/llm/<call_id>.jsonl` 的 `completion`，逐字节等于记录里的 `response_hash`）
在当前代码上做**离线重放**，逐项测指令 E 第 4 项点名的四条出口：

  1. **`primary` 边序错误**（真实批 3/4 `6ffe3da2…`）：这束是合法 JSON，只是 `c9` 与 `c16` 的
     **第一条支撑边不是 `primary`**。整束解析因此 fail-closed；逐候选补救把**这两条**逐条记名
     拒掉、其余 19 条照原样继续，且**守恒**（19 + 2 = 21，一条不多一条不少）。记录里只引用了
     第一条错误（`c9`）——出口本身是逐候选的，两条都在案。
  2. **非法 JSON**（真实纠正返回 `c7c3dc88…`）：字符串**内部**一处裸换行（
     `Invalid control character at: line 255 column 196 (char 6820)`）让 r7b 的严格解析整束作废。
     现在的容错**只**覆盖已证明的裸换行/回车/制表符，且逐类收窄：NUL / 字符串外的裸控制字符
     照旧拒。同一份字节现在解析成功（35 条候选 / 2 个草稿单元 / 11 条补件诉求），并且仍要过
     **同一套**结构校验——容错放开的是「这一个字节怎么编码」，不是「什么内容算合法」。
  3. **高风险候选**（真实批 2/4 `7d333f3e…`）：44 条候选里 18 条命中 `HIGH_RISK_SURFACE_MARKERS`
     （批 1/4 是 0/40，批 3/4 是 21/21，纠正返回是 8/35——逐批复算，不写死一个数）。高风险面判据
     **只吃候选自己的文本**，且**排在成员解析之前**：成员指向一个不存在的材料时，高风险文本
     照样返回 `path_b_high_risk_surface`，普通描述性文本则不会拿到这个码。
  4. **重复候选**（真实批 3/4 派生一束）：把一条真实候选的 `candidate_key` 改成前一条的标签，
     逐候选补救记下**第二条**为 `candidate_key_duplicated`（带模型自己写的原文），整束解析则直接
     抛「候选标签 'c4' 重复」。集合级问题在聚合绑定门上**不形成决定**：空集 / 缺身份 / 重复 /
     非 canonical 顺序一律 fail-closed 抛出（`proposal_set_empty` / `proposal_missing` /
     `proposal_duplicate` / `proposal_order_drift`），**不**跳过聚合绑定、**不**静默重排。

## 有界、带痕、不静默丢（本模块复核的那一半）

  * **有界重问**：每批至多 `MAX_BATCH_SHAPE_CORRECTIONS_PER_BATCH`（= 1）次，且与截断缩小**共用**
    `MAX_SWEEP_SHRINK_STEPS`（= 2）那一份额外额度——上界逐项可读，不是「再试几次」。
  * **带痕**：纠正说明由**那一批自己的**身份与错误串生成（批次坐标、label、aspects、已声明行数
    全在说明里），并逐字要求「只重出**本批**这一份完整 JSON」「其余批次的返回**不受影响**」。
  * **不静默丢补件**：批内补救有一道硬边界——`narrative_draft_units` / `follow_up_needs` 必须
    **整体**解析成功，否则整批退回既有 fail-closed（不做「只留候选、丢掉诉求」的补救）。
    本模块用真实返回派生的反例逐项断言这一点。

## r7b 现场与当前代码的**对照**（本模块不重写历史）

r7b 那两次拒绝发生在**更早的代码**上：日志里的 `prompt_version` 是
`pack_section_writer_proposals_v7@proposals-9`（当前是 `proposals-11`），逐候选裁出（`cco-4`）与
批次形状纠正（`bsc-1`）都在那之后才有。两件事因此必须分开读：

  * r7b 的**第二次**调用是「通用重试」那条路：它把**整条扫描**重跑了一遍（记录里第 2 轮的
    `batches` 是 1/4 与 2/4 两次调用，第 1 轮已经拿到的合法返回没有被沿用），追加的说明**不含
    任何批次坐标**，且引用的是**批 3/4** 的错误——本模块从留存请求里逐字读出这句话（它出现在
    批 1/4 与批 2/4 两次重跑的请求尾部）。当前代码把这条路换成了**批内**纠正：只把失败那一批
    放回队首，说明带批次坐标。这是**行为差异**，不是历史被改写。
  * 本模块**没有**用一次真实 sweep 复算那条调度（那需要脚本化整轮返回或一次新调用）；它复算的
    是说明文本本身与四条出口的逐条行为。调度不变量在本模块是**代码事实 + r7b 现场行为的对照**，
    已在下面逐条标注，**不计入**任何通过读数。

## 本模块**不**主张的事

  * **不是**新的真实验收，也**不是**一次真实模型调用：零网络、零新调用，所有文本都来自 r7b 留存。
  * 链级结论（原束 → 裁出 → 新修订 → 聚合绑定 / 蕴含 / 接受边 / 定稿正文）**不在这里**：
    见 `evals/test_m930_3_prewrite_offline_replay.py`（同一批留存返回的**同链**重放）。本模块不
    另搭一套夹具，凡需要「整节链」的结论一律引用它，不在此重复。
  * 第 4 项的重复候选所用的一束是**派生夹具**（真实返回改一个标签），逐条标注，不是模型返回。
  * 不宣布 M930-3 / TS5 / 正式树门或后续阶段关闭；这两束在 r7b 里是**失败**，本模块不把它读成
    通过，也不把它读成「出口已被真实 run 行使过」——恰恰相反：这两条出口**从未**被真实 run 行使。

数据依赖：`logs/llm/` 下 5 个真实调用文件（含 r7b 第 2 轮批 1/4 的 `a88b2bbd…`）与
`evaluation/results/m930_3_acceptance_crossdoc_real_r7b/proposal_set_rejections.json`。
都不在版本控制里，缺失时本模块 **typed skip**。
"""

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sections import claim_binding_gate as CBG
from sections import narrative_schema as NS
from sections import pack_writer as PW

ROOT = Path(__file__).resolve().parent.parent
LLM_LOGS = ROOT / "logs" / "llm"
R7B = ROOT / "evaluation" / "results" / "m930_3_acceptance_crossdoc_real_r7b"
REJECTIONS = R7B / "proposal_set_rejections.json"

#: r7b 那一轮 company 节的**全部**留存调用（call_id 文件名的主键部分）。前三个是第 1 轮
#: （批 1/4、2/4、3/4），第四个是第 2 轮**纠正/重问**（批 2/4），第五个是第 2 轮批 1/4。
#: 批 4/4 从未被调用——第 1 轮在批 3/4 上失败，第 2 轮在批 2/4 上失败，两次都没走到批 4。
CALL_B1 = "d4548a48cea8498888bb806744ae4753"
CALL_B2 = "7d333f3e1b574d63a5c63f382f9f8a5e"
CALL_B3 = "6ffe3da21e55446cb4a79b6129ebbbc2"
CALL_CORRECTION = "c7c3dc88a7dc42b6a3296a0dcccd964b"
CALL_RETRY_B1 = "a88b2bbd1fde46cebcac3c84cb1ed208"

#: 记录里两条拒绝各自的 `narration_call_id`（用于与留存文件对齐，不另判身份）。
RECORDED_ATTEMPT1_CALL = CALL_B3
RECORDED_ATTEMPT2_CALL = CALL_CORRECTION

#: r7b 现场那两次拒绝**共同**的 kind（记录值）。两者都是 `schema_invalid` 但**不是同一件事**：
#: 第 1 束是候选级结构非法（首边不是 primary），第 2 束是整响应连 JSON 都不合法。
RECORDED_REJECTION_KINDS = ("schema_invalid", "schema_invalid")

#: r7b 第 1 轮批 3/4 上真正越线的候选标签（整束解析只会报第一条；逐候选补救两条都在案）。
PRIMARY_ORDER_OFFENDERS = ("c9", "c16")

#: 非法 JSON 那一束的严格解析错误原文（记录与现场逐字一致，本模块把它当**钉子**）。
RECORDED_JSON_ERROR = "Invalid control character at: line 255 column 196 (char 6820)"

#: 逐批高风险表面命中数（本模块**复算**出来的读数；写死只为让读数变化当场可见）。
HIGH_RISK_HITS = {"b1": (0, 40), "b2": (18, 44), "b3": (21, 21), "correction": (8, 35)}

#: 本模块读的是 **r7b 那一轮留存的字节**，它们属于 `proposals-10` 形态：载荷里根本没有
#: `natural_prose_draft` 这一层（回看 §1：那一束的草稿身份是从 `narrative_draft_units` 读的）。
#: 用**当前线**的「有候选 ⇒ 有草稿」去判一条本来就不欠这个键的历史记录，是把版本差异读成缺陷。
#:
#: 因此这里**显式声明**一条历史线政策，再由它派生解析口径——不是把要求关掉，而是把「按哪条线
#: 读这份字节」写成可读的常量。`proposals-10` 在 `WriterPolicy` 里只能配 `stub`（历史线不可配
#: 真实模型），这正是「离线留档重放」该有的政策面。
LEGACY_REPLAY_POLICY = PW.WriterPolicy(proposal_wire="proposals-10")

#: 历史线重放的解析口径（由政策派生，不在调用点二次写死）。
LEGACY_REPLAY_DRAFT_MODE = LEGACY_REPLAY_POLICY.requires_natural_draft()


def _anchors_present() -> tuple[bool, str]:
    missing: list[str] = []
    if not REJECTIONS.exists():
        missing.append(str(REJECTIONS))
    for prefix in (CALL_B1, CALL_B2, CALL_B3, CALL_CORRECTION, CALL_RETRY_B1):
        if not list(LLM_LOGS.glob(f"*{prefix}*.jsonl")):
            missing.append(f"logs/llm/*{prefix}*.jsonl")
    return (not missing), "、".join(missing)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _call_path(prefix: str) -> Path:
    hits = sorted(LLM_LOGS.glob(f"*{prefix}*.jsonl"))
    if not hits:
        raise AssertionError(f"现场没有 {prefix} 的调用日志")
    return hits[0]


def _read_call(prefix: str) -> dict:
    return _read_json(_call_path(prefix))


def _request_of(row: Mapping) -> dict:
    """留存请求的 JSON 主体。

    用 `raw_decode` 而不是 `json.loads`：纠正那一轮的请求在 JSON **之后**追加了一段说明
    （「上一次输出被拒：…请修正后重新输出完整 JSON。」），`json.loads` 会因 `Extra data` 直接失败。
    生产侧的离线替身走的是同一个读法（`CompanyReplayClient.narrate`）。
    """
    content = row["messages"][0]["content"]
    body, _end = json.JSONDecoder(strict=False).raw_decode(content)
    return body


def _request_tail(row: Mapping) -> str:
    content = row["messages"][0]["content"]
    _body, end = json.JSONDecoder(strict=False).raw_decode(content)
    return content[end:]


def _recorded_aliases(request: Mapping) -> PW.SupportAliasTable:
    """按**记录下来的请求**重建本次请求声明的别名表（`saref-1`）。

    只从请求自己的声明行读：`ref` / `container_id` / `material_id` / `fact_id` 逐字取自
    `materials` 与 `authority_facts` 两块的对应行。材料行的 `authority_kind` 取常量
    `topic_pack`——它不是本模块的判断：`SupportAliasTable.__post_init__` 自己就要求材料选项恒为
    `topic_pack`（exact `ResearchMaterial` 只存在于 Pack），生产侧的 `_support_aliases` 也是这么
    派生的。重建结果另有**独立核对**：逐项等于请求自己声明的 ref 清单（见 §0）。
    """
    materials = tuple(PW.SupportOption(ref=str(r["ref"]), authority_kind="topic_pack",
                                       container_identity=str(r["container_id"]),
                                       material_id=str(r["material_id"]))
                      for r in (request.get("materials") or []))
    facts = tuple(PW.SupportOption(ref=str(r["ref"]), authority_kind=str(r["authority_kind"]),
                                   container_identity=str(r["container_id"]),
                                   fact_id=str(r["fact_id"]))
                  for r in (request.get("authority_facts") or []))
    return PW.SupportAliasTable(facts=facts, materials=materials)


def _payload_of(prefix: str) -> tuple[dict, dict, str]:
    """`(调用行, 请求体, 返回文本)`——`completion` 就是模型的原始返回文本。"""
    row = _read_call(prefix)
    return row, _request_of(row), str(row.get("completion") or "")


def _lenient_payload(text: str) -> dict:
    return PW.load_json_with_proven_leniency(text, "候选提案")


def _key_error(fn, *args, **kwargs) -> str:
    """跑一个预期会 fail-closed 的调用，返回它的错误串（没抛就返回空串）。"""
    try:
        fn(*args, **kwargs)
    except PW.PackWriterError as exc:
        return str(exc)
    return ""


def _derived_text(payload: Mapping, *, candidates: list) -> str:
    """真实返回派生一份文本（只替换 `claim_candidates`，其余逐字保留）。"""
    body = dict(payload)
    body["claim_candidates"] = candidates
    return json.dumps(body, ensure_ascii=False)


# ---------------------------------------------------------------------------
# §0 现场与记录：两束被拒、五个调用、逐字节锚点
# ---------------------------------------------------------------------------

def _check_recorded_facts(check, check_eq, details) -> dict:
    rej = _read_json(REJECTIONS)
    check_eq(rej.get("schema_version"), "proposal-set-rejections/6",
             "读的是 r7b 记录的那一版拒绝表（schema 版本不靠猜）")
    check_eq(rej.get("total_rejected_bundles"), 2, "r7b 的 company 段恰好两束被拒")
    check_eq(rej.get("sections_completed"), ["financial", "industry"],
             "r7b 完成的是财务与行业两节（company **没有**任何产物——它是整节失败）")
    failed = (rej.get("sections_failed") or {}).get("company") or ""
    check("候选提案连续被拒" in failed and RECORDED_JSON_ERROR in failed,
          "r7b 记录的公司节失败原因逐字引用本模块 §2 复算的那条 JSON 错误")

    records = rej["sections"]["company"]
    check_eq(len(records), 2, "company 段两条拒绝记录（第 1 轮 / 第 2 轮）")
    first, second = records[0], records[1]
    check_eq((first["attempt"], second["attempt"]), (1, 2),
             "两条记录分别是第 1 次与第 2 次生成")
    check_eq((first["rejection_kind"], second["rejection_kind"]), RECORDED_REJECTION_KINDS,
             "两条记录的 kind 都是 `schema_invalid`（但**不是同一件事**：一条是候选级结构非法，"
             "一条是整响应非法 JSON）")
    check_eq((first["narration_call_id"], second["narration_call_id"]),
             (RECORDED_ATTEMPT1_CALL, RECORDED_ATTEMPT2_CALL),
             "两条记录指向的调用就是本模块重放的那两个留存文件")
    for rec in records:
        check(rec["whole_set_rejected"] is True and rec["reproposal"] is None
              and rec["answered_by_attempt"] is None,
              f"第 {rec['attempt']} 束是**整束**被拒，且没有走定向重提案（两条出口互斥）")

    # --- 逐字节锚点：留存的 `completion` 就是被哈希进记录的那份字节 -------------------
    anchors = {}
    for name, prefix in (("b1", CALL_B1), ("b2", CALL_B2), ("b3", CALL_B3),
                         ("correction", CALL_CORRECTION), ("retry_b1", CALL_RETRY_B1)):
        row = _read_call(prefix)
        anchors[name] = hashlib.sha256(
            str(row["completion"]).encode("utf-8")).hexdigest()
    check_eq(anchors["b3"], first["response_hash"],
             "批 3/4 的留存返回逐字节等于记录里那一束的 `response_hash`（sha256）")
    check_eq(anchors["correction"], second["response_hash"],
             "纠正返回逐字节等于记录里第 2 束的 `response_hash`")
    retry_batches = second["batches"]
    check_eq(anchors["retry_b1"], retry_batches[0]["response_hash"],
             "第 2 轮批 1/4 的留存返回逐字节等于记录里那一次的 `response_hash`")
    check_eq([b["call_id"] for b in first["batches"]],
             [CALL_B1, CALL_B2, CALL_B3],
             "第 1 轮记录的三次调用与现场三个留存文件一一对应")
    check_eq([b["label"] for b in first["batches"]], ["1/4", "2/4", "3/4"],
             "第 1 轮只走了三个批次：**批 4/4 从未被调用**（在批 3/4 上失败即停）")
    check(all(b["status"] == "ok" and b["shrink_depth"] == 0 and b["focus"] is False
              and b["parent_batch_id"] is None for b in first["batches"]),
          "第 1 轮三次调用本身都成功返回（失败发生在**返回之后**的结构校验上，不是调用失败），"
          "三次都是原始批次：没有截断缩小、没有聚焦子批")
    check_eq([b["call_id"] for b in retry_batches], [CALL_RETRY_B1, CALL_CORRECTION],
             "第 2 轮记录的两次调用与现场两个留存文件一一对应")

    # --- 第 2 轮**重跑的是整条扫描**：批 1/4 被重问，而它上一轮已经成功 -------------
    row_retry, _req_retry, text_retry = _payload_of(CALL_RETRY_B1)
    retry_payload = _lenient_payload(text_retry)
    check_eq(len(retry_payload.get("claim_candidates") or []), 15,
             "第 2 轮批 1/4 返回的是**另一份**内容（15 条候选，第 1 轮是 40 条）："
             "这一次调用不是「沿用上一轮」，而是重新问了一遍")
    tail_retry = _request_tail(row_retry)
    error_b3 = _key_error(PW.parse_writer_proposals, _payload_of(CALL_B3)[2],
                          aliases=_recorded_aliases(_payload_of(CALL_B3)[1]),
                          require_natural_draft=LEGACY_REPLAY_DRAFT_MODE)
    check(tail_retry.startswith("\n\n\n上一次输出被拒：")
          and "请修正后重新输出完整 JSON。" in tail_retry
          and "候选 c9 的第一条支撑边必须是 primary" in tail_retry,
          "第 2 轮批 1/4 的请求尾部追加的是**通用重试说明**：它逐字引用**批 3/4** 的错误")
    check("wpbatch_" not in tail_retry and "aspect_ids" not in tail_retry
          and "本批" not in tail_retry,
          "这条通用说明**不含任何批次坐标**（没有 batch_id、没有 batch 的 aspect 清单、"
          "也没有「本批」这种相对指称）：它被追加到**每一批**的请求上，与「哪一批真的错了」无关")
    details.append(
        "NOTE r7b 现场（记录 + 留存请求逐字读出，不改写）：第 1 轮走 1/4、2/4、3/4，在**批 3/4** 上"
        f"整束解析失败（{error_b3}）；第 2 轮由**通用重试**通道重跑了**整条扫描**——批 1/4 与 2/4 "
        "各被重问一次（批 3/4 反而没被重问），追加说明引用批 3/4 的错误且不含批次坐标；第 2 轮"
        "在批 2/4 上拿到非法 JSON，通用额度用尽 ⇒ 整节 fail-closed。当前代码把这条路换成"
        "**批内**形状纠正（`bsc-1`：只把失败那一批放回队首、说明带该批坐标、每批至多 1 次、与"
        "截断缩小共用额度）。本模块复核说明文本与四条出口；**调度**不变量是代码事实 + 现场对照，"
        "未在此用一次真实 sweep 复算。")

    # --- 第 1 束的逐候选身份：与留存返回逐条对齐（不凭空列） -----------------------
    payload_b3 = _lenient_payload(_payload_of(CALL_B3)[2])
    raw_keys = [str(c.get("candidate_key")) for c in payload_b3["claim_candidates"]]
    check_eq(list(first["candidate_ids"]), raw_keys,
             "第 1 束记录里的 21 条候选身份 = 留存返回里**逐条、按序**的候选标签"
             "（不是「有用的那几条」）")
    check_eq(list(first["draft_unit_ids"]),
             [str(u.get("unit_key")) for u in payload_b3["narrative_draft_units"]],
             "第 1 束记录里的草稿单元身份 = 留存返回里的单元标签")
    check_eq(first["follow_up_count"], len(payload_b3["follow_up_needs"]),
             "第 1 束记录里的补件条数 = 留存返回里的诉求条数（被拒 ≠ 没有诉求）")
    check_eq(first["candidate_audit"], [],
             "`schema_invalid` 这一束的逐候选归属**必须为空**：整响应从未成为结构化对象，"
             "按文本猜一份就是伪造审计")
    return {"first": first, "second": second, "error_b3": error_b3,
            "payload_b3": payload_b3, "raw_keys": raw_keys}


# ---------------------------------------------------------------------------
# §1 出口一：`primary` 边序错误（真实批 3/4）
# ---------------------------------------------------------------------------

def _check_primary_edge_order(check, check_eq, details, facts) -> None:
    row, request, text = _payload_of(CALL_B3)
    aliases = _recorded_aliases(request)
    check_eq(len(aliases.materials), 46,
             "批 3/4 的请求**声明**了 46 行精确材料（别名逐行完备）")
    check_eq(len(aliases.facts), 0,
             "批 3/4 的请求**没有**声明任何权威事实行（路径 A 在本批无从声明）")

    # 这一束**不是** JSON 缺陷：严格解析成功，失败发生在候选级结构校验上。
    strict_ok = True
    try:
        json.loads(text)
    except ValueError:
        strict_ok = False
    check(strict_ok, "批 3/4 的返回是**合法 JSON**——它被拒不是因为编码，而是因为候选结构")

    error = _key_error(PW.parse_writer_proposals, text, aliases=aliases,
                       require_natural_draft=LEGACY_REPLAY_DRAFT_MODE)
    check("第一条支撑边必须是 primary" in error and PRIMARY_ORDER_OFFENDERS[0] in error,
          f"整束解析 fail-closed，错误逐字点名 {PRIMARY_ORDER_OFFENDERS[0]} 的首条支撑边不是 "
          f"primary（实得 {error!r}）")
    check_eq(error, facts["error_b3"],
             "现场复算的错误串与 §0 里从记录侧引用的**同一条**")

    salvaged = PW._salvage_candidates(text, aliases=aliases)
    check(salvaged is not None, "逐候选补救在这束上适用（顶层可读、单元与诉求整体合法）")
    kept, rejected = salvaged
    check_eq([r["candidate_key"] for r in rejected], list(PRIMARY_ORDER_OFFENDERS),
             "被逐条拒掉的**恰好**是两条越线候选——记录只引用了第一条，出口本身是逐候选的")
    check_eq([r["reason"] for r in rejected], ["candidate_structure_invalid"] * 2,
             "两条的原因都是候选级结构非法（不是整束 `schema_invalid`：这两条候选的文本与错误串"
             "是可信的，因此可以逐条记名）")
    check(all("第一条支撑边必须是 primary" in r["detail"] for r in rejected),
          "每条被拒条目都带着**它自己**的逐字错误串")
    check_eq(len(kept) + len(rejected), len(facts["raw_keys"]),
             f"**守恒**：幸存 {len(kept)} + 被拒 {len(rejected)} = 原束 "
             f"{len(facts['raw_keys'])}（「候选无故消失」在结构上无从表达）")
    check_eq(len(kept), len(facts["raw_keys"]) - 2, "幸存条数 = 原束条数 − 越线条数")

    # --- 幸存者**逐字未改写**：用整束解析当 oracle，比对同一批输入 -------------------
    survivors_raw = [c for c in facts["payload_b3"]["claim_candidates"]
                     if str(c.get("candidate_key")) not in PRIMARY_ORDER_OFFENDERS]
    oracle = PW.parse_writer_proposals(
        _derived_text(facts["payload_b3"], candidates=survivors_raw), aliases=aliases,
        require_natural_draft=LEGACY_REPLAY_DRAFT_MODE)
    check_eq([c["candidate_key"] for c in kept],
             [c["candidate_key"] for c in oracle["claim_candidates"]],
             "幸存的候选身份 = 原束去掉两条越线候选后的**同一有序**身份（不重排、不换序）")
    check_eq([c["claim_text"] for c in kept],
             [c["claim_text"] for c in oracle["claim_candidates"]],
             "幸存候选的文本逐字等于原束文本（补救**不改写**候选文本）")
    check_eq([c["support"] for c in kept],
             [c["support"] for c in oracle["claim_candidates"]],
             "幸存候选的支撑边逐条等于原束展开后的边（补救**不补边、不改角色**）")

    batch_plan = PW._salvage_batch_plan(text, aliases=aliases,
                                        require_natural_draft=LEGACY_REPLAY_DRAFT_MODE)
    check(batch_plan is not None, "批内补救也适用：候选去掉两条、单元与诉求整体保留")
    plan, plan_rejected = batch_plan
    check_eq(len(plan["claim_candidates"]), len(kept), "批内补救得到的候选集与逐候选补救一致")
    check_eq(len(plan_rejected), len(rejected), "批内补救记下的被拒条数与逐候选补救一致")
    check_eq(plan["narrative_draft_units"], oracle["narrative_draft_units"],
             "草稿单元逐字保留（补救只动候选，不动单元）")
    check_eq(plan["follow_up_needs"], oracle["follow_up_needs"],
             "补件诉求逐字保留（被拒的候选不带走别的候选提过的诉求）")

    # --- 硬边界反例：单元整体不合法时**退回既有 fail-closed**，不做局部保留 -----------
    broken_units = [dict(u) for u in facts["payload_b3"]["narrative_draft_units"]]
    broken_units[0] = dict(broken_units[0], unit_kind="not_a_declared_kind")
    broken_text = _derived_text(dict(facts["payload_b3"], narrative_draft_units=broken_units),
                                candidates=facts["payload_b3"]["claim_candidates"])
    check(PW._salvage_batch_plan(broken_text, aliases=aliases,
                                 require_natural_draft=LEGACY_REPLAY_DRAFT_MODE) is None,
          "反例（补件/单元被静默丢掉）：把任意一条草稿单元的 `unit_kind` 改坏后，批内补救"
          "**整体不适用**（返回 None）——不静默丢单元，退回既有 C4/fail-closed 路径"
          "（这条口径必须与上文一致，否则 None 有可能来自「这份字节没有当前线的草稿层」"
          "而不是「单元被改坏」——那测的就不是这条边界了）")
    check(PW._salvage_candidates(broken_text, aliases=aliases) is not None,
          "同一份文本的**逐候选**补救仍然适用：候选级补救与批内补救是两条不同的边界")
    details.append(
        "NOTE 「单元/诉求整体合法」这道边界不是装饰：候选被逐条拒掉有逐条记录，而草稿单元或"
        "补件诉求被整段丢掉**没有**可逐条记名的主体。因此批内补救宁可整体不适用，也不做"
        "「只留候选」的补救——本模块的反例就在上面。")

    # --- C4 批次形状纠正：有界、带痕、批内 -----------------------------------------
    check_eq(PW.MAX_BATCH_SHAPE_CORRECTIONS_PER_BATCH, 1,
             "每批至多纠正一次（再次失败即停，不借通用通道把同一件事整轮重问一遍）")
    check_eq(PW.MAX_SWEEP_SHRINK_STEPS, 2,
             "额外调用额度（截断缩小与批次纠正**共用**）总共 2 次：上界逐项可读")
    check_eq(PW.BATCH_SHAPE_CORRECTION_NOTE_VERSION, "bsc-1",
             "纠正说明是**有版本**的输入面（改一句就是改一次输入纪律）")
    check_eq(PW.BATCH_SHAPE_CORRECTION_TRIGGER_KIND, "schema_invalid",
             "纠正的触发 kind 复用已登记的 `schema_invalid`，不新开一类")
    hint = PW._batch_shape_hint(facts["error_b3"])
    check(len(hint) >= 1 and "第一条支撑边必须是 primary" in hint[0],
          "纠正提示按**这一批自己的错误串**给出可执行的修法（不是一句「请重出」）")

    batch3 = request["batch"]
    note3 = PW._batch_shape_correction_note(
        error=facts["error_b3"], batch_label=str(batch3["label"]),
        batch_id=str(batch3["batch_id"]), aspect_ids=tuple(batch3["aspect_ids"]),
        declared_fact_refs=len(aliases.facts), declared_material_refs=len(aliases.materials))
    check(isinstance(note3, str) and batch3["batch_id"] in note3
          and str(batch3["label"]) in note3 and batch3["aspect_ids"][0] in note3,
          "纠正说明带**这一批自己的**批次坐标：batch_id、label 与 aspect 清单")
    check(facts["error_b3"] in note3 and "只重出**本批**这一份完整 JSON" in note3
          and "不受影响" in note3,
          "说明同时带该批的错误原文，并逐字要求只重出本批、其余批次不受影响")
    note2 = PW._batch_shape_correction_note(
        error=facts["error_b3"], batch_label="2/4", batch_id="wpbatch_别的批次",
        aspect_ids=("company_business_model.tech_route",), declared_fact_refs=0,
        declared_material_refs=46)
    check(note2 != note3 and "wpbatch_别的批次" in note2 and batch3["batch_id"] not in note2,
          "反例（r7b 现场形态）：说明的批次坐标**只来自调用方**——把别的批次的身份传进去，"
          "说明就会指向别的批次。因此「纠正对准失败的那一批」是**调用点**的不变量"
          "（`pack_writer.py`：`queue.insert(0, target_batch)` 与 `batch_notes[bid]`），"
          "说明文本自身只能保证「带的是我被交来的那批坐标」")


# ---------------------------------------------------------------------------
# §2 出口二：非法 JSON（真实纠正返回）
# ---------------------------------------------------------------------------

def _check_illegal_json(check, check_eq, details, facts) -> None:
    row, request, text = _payload_of(CALL_CORRECTION)
    aliases = _recorded_aliases(request)
    check_eq(len(aliases.materials), 46, "纠正请求声明的仍是同一批 46 行材料（同一张表跨批恒定）")
    check_eq(request["batch"]["batch_id"], "wpbatch_b1a1466200f760fcc9d143a8",
             "纠正请求回答的是**批 2/4**（与它引用的错误所属批次不同——这是 r7b 现场的对照点）")

    strict_error = ""
    try:
        json.loads(text)
    except ValueError as exc:
        strict_error = str(exc)
    check_eq(strict_error, RECORDED_JSON_ERROR,
             "严格解析失败的位置与记录逐字一致（line 255 column 196 char 6820）")

    controls = PW._raw_control_chars_in_strings(text)
    check_eq(controls, ["\n"],
             "字符串**内部**的裸控制字符恰好一个：裸换行（`\\r`/`\\t` 与它同类，本束里没有）")
    check(all(c in PW._RAW_CONTROL_ALLOWED for c in controls),
          "它落在**已证明**的容忍集内：现场唯一证据就是这一处裸换行")
    check_eq(PW._RAW_CONTROL_ALLOWED, ("\n", "\r", "\t"),
             "容忍集是封闭的三元组（不是「所有控制字符」）")

    payload = _lenient_payload(text)
    check_eq(payload, json.loads(text, strict=False),
             "容错解析与「只把 strict 关掉」逐字段相同：容错**没有**做任何额外改写")
    check_eq((len(payload["claim_candidates"]), len(payload["narrative_draft_units"]),
              len(payload["follow_up_needs"])), (35, 2, 11),
             "同一份字节现在解析出 35 条候选 / 2 个草稿单元 / 11 条补件诉求"
             "（r7b 当时整束作废，所以那一束的逐候选归属为空、补件条数为 0）")

    # --- 容错**不**放宽结构判定：同一份返回仍要过同一套校验器 ------------------------
    plan = PW.parse_writer_proposals(text, aliases=aliases,
                                     require_natural_draft=LEGACY_REPLAY_DRAFT_MODE)
    check_eq(len(plan["claim_candidates"]), 35,
             "经容错读出的这份返回仍然过**同一套**结构校验（容错放开的是「这一个字节怎么编码」，"
             "不是「什么内容算合法」）")
    check_eq(_key_error(PW.parse_writer_proposals, json.dumps({"claim_candidates": []}),
                        aliases=aliases,
                        require_natural_draft=LEGACY_REPLAY_DRAFT_MODE), "",
             "空候选束本身是合法的结构（它不是「读不出」，而是「没有候选」——两者是不同的事，"
             "因此这一束不会走进 §1 的逐候选补救）")

    # --- 反例：未证明的裸控制字符照旧拒（逐类收窄，不是一开全放） --------------------
    nul_text = text[:6820] + "\x00" + text[6821:]
    nul_error = _key_error(PW.load_json_with_proven_leniency, nul_text, "候选提案")
    check("U+0000" in nul_error and "只容忍字符串内的裸换行/回车/制表符" in nul_error,
          "反例（未证明的控制字符）：把现场那处裸换行换成裸 NUL 后，解析**仍然拒**，"
          "并逐字说明是哪一类字符不被放行")
    check("U+0000" not in _key_error(PW.load_json_with_proven_leniency, text, "候选提案"),
          "对照：现场那处裸换行**不**触发这条拒绝（两类字符的处理不同）")

    outside = "{\x01" + text[1:]
    outside_error = _key_error(PW.load_json_with_proven_leniency, outside, "候选提案")
    check(outside_error != "" and "U+" not in outside_error,
          "反例（字符串**外**的裸控制字符）：它本来就不是合法 JSON 的空白，"
          "容错不看它、`json.loads(strict=False)` 也救不回来 ⇒ 照旧拒")

    extra_key = dict(payload)
    extra_key["unknown_top_key"] = 1
    extra_error = _key_error(PW.parse_writer_proposals, json.dumps(extra_key), aliases=aliases,
                             require_natural_draft=LEGACY_REPLAY_DRAFT_MODE)
    check("未登记字段" in extra_error and "unknown_top_key" in extra_error,
          "反例（结构性错误·顶层键集封闭）：多出一个未登记键时，容错读得出 JSON 也照旧被结构"
          "校验拒掉（工具/检索意图不可表达）")
    undirected = [dict(c) for c in payload["claim_candidates"]]
    undirected[0] = dict(undirected[0], support=[])
    undirected_error = _key_error(PW.parse_writer_proposals,
                                  _derived_text(payload, candidates=undirected), aliases=aliases,
                                  require_natural_draft=LEGACY_REPLAY_DRAFT_MODE)
    check("必须至少有一条 factual 支撑边" in undirected_error,
          "反例（结构性错误·候选级，派生夹具）：把真实返回里第一条候选的支撑边清空后，"
          "容错读得出、这条候选仍被结构校验拒掉")
    check(_lenient_payload(json.dumps(extra_key)) == extra_key,
          "对照：上面两个反例在**容错这一层**都读得出（拒它们的是结构校验，不是容错）——"
          "「容错放宽了内容判定」在本模块里没有任何证据支持")

    check_eq(facts["second"]["candidate_ids"], [],
             "记录里第 2 束的逐候选归属为空、补件条数为 0——与「整响应当时读不出」一致；"
             "本模块不把「现在读得出」写成「当时读得出」")
    check_eq(facts["second"]["follow_up_count"], 0,
             "同上：那一束的补件条数为 0 是**当时读不出**的结果，不是「模型没提诉求」")


# ---------------------------------------------------------------------------
# §3 出口三：高风险候选
# ---------------------------------------------------------------------------

@dataclass
class _StubProposal:
    """**结构替身**：只提供机械门在「高风险面」那一分支**之前**会读到的字段。

    这不是真实 proposal，任何地方都不得当真实 proposal 读：它存在的唯一理由是证明
    `path_b_high_risk_surface` 这条判据**排在成员解析之前**（成员指向不存在的材料时，
    高风险文本照样拿到这个码）。真实 proposal 的整链证据见同链重放模块。
    """
    binding_subject_kind: str
    binding_subject_id: str
    draft_revision: str
    authority_kind: str = "topic_pack"
    support_semantics: str = "factual"
    support_role: str = "primary"
    authorization_path: str = "path_b_material_derived"
    material_id: str = "mat-不存在的成员"


def _check_high_risk(check, check_eq, details) -> None:
    per_batch = {}
    for name, prefix in (("b1", CALL_B1), ("b2", CALL_B2), ("b3", CALL_B3),
                         ("correction", CALL_CORRECTION)):
        text = _payload_of(prefix)[2]
        payload = _lenient_payload(text)
        candidates = [c for c in payload["claim_candidates"] if isinstance(c, Mapping)]
        texts = [str(c.get("claim_text") or "") for c in candidates]
        again = [NS.high_risk_surface_tokens(t) for t in texts]
        check(all(NS.high_risk_surface_tokens(t) == again[i] for i, t in enumerate(texts)),
              f"{name}：高风险表面判据是**纯函数**（同一批文本两次调用逐条相同，"
              "读数不依赖调用顺序或状态）")
        hits = [(str(c.get("candidate_key")), again[i]) for i, c in enumerate(candidates)
                if again[i]]
        check(all(tok in texts[i] for i, _c in enumerate(candidates) if again[i]
                  for tok in again[i]),
              f"{name}：每一条命中都是候选文本里的**逐字**成分（不是词形归一后的猜测）")
        per_batch[name] = (len(hits), len(candidates))
    check_eq(per_batch, HIGH_RISK_HITS,
             "逐批复算的高风险命中数（批 1/4 一条都没命中，批 2/4 命中 18/44，批 3/4 是 21/21，"
             "纠正返回 8/35）")
    markers = NS.HIGH_RISK_SURFACE_MARKERS
    check_eq(len(markers), 84, "高风险词表 84 条（判据的唯一实现处）")
    check(len(set(markers)) == 83 and [m for m in set(markers)
                                       if markers.count(m) > 1] == ["不适用"]
          and all(isinstance(m, str) and m for m in markers),
          "84 条里有 1 条**刻意重复**：`不适用` 同时属于「显式否定」与「勾选/适用性」两组"
          "（词表按语义分组，同一字面成分落在两组里是有意的），因此它不是 83 条而是 84 条")
    check_eq(NS.high_risk_surface_tokens("本项不适用"), ("不适用", "适用"),
             "重复条目**不影响**判据读数：`high_risk_surface_tokens` 逐 token 去重并保序，"
             "`不适用` 只出现一次")
    check_eq(NS.high_risk_surface_tokens("勾选：不适用"), ("不适用", "勾选", "适用"),
             "同一段文本里的多个表面按固定顺序全部给出（调用方不得各自挑一部分）")

    # --- 判据只看候选文本，且排在成员解析之前 ---------------------------------------
    payload_b3 = _payload_of(CALL_B3)[0] and _lenient_payload(_payload_of(CALL_B3)[2])
    risky_text = str(payload_b3["claim_candidates"][0]["claim_text"])
    check(bool(NS.high_risk_surface_tokens(risky_text)),
          "挑出的这条真实候选文本确实带高风险表面（不是构造出来的）")
    payload_b1 = _lenient_payload(_payload_of(CALL_B1)[2])
    plain_text = next((str(c["claim_text"]) for c in payload_b1["claim_candidates"]
                       if not NS.high_risk_surface_tokens(str(c["claim_text"]))), "")
    check(bool(plain_text), "同一批里挑出一条**不带**高风险表面的真实候选文本做对照")

    subject = CBG.BindingSubjectRevision("claim_candidate", "ccand_探针", "sdrev_探针")
    stub_risky = _StubProposal("claim_candidate", "ccand_探针", "sdrev_探针")
    reason_risky = CBG._edge_reason(stub_risky, subject_revision=subject, fact_table={},
                                    manifest=NS.WriterMaterialManifest.create(), subject_text=risky_text)
    check_eq(reason_risky, "path_b_high_risk_surface",
             "高风险文本在路径 B 上得到的**就是** `path_b_high_risk_surface`——注意这个结果是在"
             "「成员指向不存在的材料、材料上下文为空」的条件下拿到的：判据排在成员解析之前")
    stub_plain = _StubProposal("claim_candidate", "ccand_探针", "sdrev_探针")
    low = None
    try:
        low = CBG._edge_reason(stub_plain, subject_revision=subject, fact_table={},
                               manifest=NS.WriterMaterialManifest.create(), subject_text=plain_text)
    except Exception as exc:  # noqa: BLE001 —— 下游缺少真实成员时的任何 fail-closed 都接受
        low = f"{type(exc).__name__}"
    check(str(low) != "path_b_high_risk_surface",
          f"对照：同一份（缺成员的）条件下，普通描述性文本**不会**拿到高风险码（实得 {low!r}）")

    check_eq(NS.high_risk_surface_tokens(""), (),
             "空文本不命中任何表面（判据不在空串上开火）")
    check_eq(CBG.CLAIM_BINDING_GATE_VERSION, NS.CLAIM_BINDING_GATE_VERSION,
             "机械门版本只在 wire 模块定义一处，门侧 re-export（同一份判据不留第二份字面量）")
    check_eq(PW.CANDIDATE_CARVE_OUT_VERSION, "cco-6",
             "逐候选裁出的版本（高风险面是它的第一条封闭原因；`cco-6` 给裁出加了第二类被裁对象"
             "——context 草稿单元，「明确否定事实不是背景衔接」那一类越权由此逐项撤下；"
             "`cco-5` 给 `path_b_unproven_current_state` 加了 typed 分级码）")
    check("path_b_high_risk_surface" in PW.CANDIDATE_CARVE_OUT_REASONS
          and "candidate_structure_invalid" in PW.CANDIDATE_CARVE_OUT_REASONS,
          "裁出的封闭原因词表里既有高风险面、也有候选级结构非法（本模块 §1/§3 两条出口）")
    details.append(
        f"NOTE 高风险面在本批真实返回上的读数（逐批复算，不写死）：{per_batch}；"
        f"词表 {len(NS.HIGH_RISK_SURFACE_MARKERS)} 条。它**不**看该字面成分是否在材料正文里"
        "逐字存在——原文逐字存在不等于已经资格化，高风险硬事实只能走路径 A 的预验证权威。")


# ---------------------------------------------------------------------------
# §4 出口四：重复候选 + 聚合绑定的集合级出口
# ---------------------------------------------------------------------------

def _check_duplicate_and_aggregate(check, check_eq, details, facts) -> None:
    _row, request, text = _payload_of(CALL_B3)
    aliases = _recorded_aliases(request)
    payload = facts["payload_b3"]

    # --- 派生夹具（**不是**模型返回）：把 c5 的标签改成 c4 --------------------------
    candidates = [dict(c) for c in payload["claim_candidates"]]
    index_c5 = next(i for i, c in enumerate(candidates) if c.get("candidate_key") == "c5")
    candidates[index_c5] = dict(candidates[index_c5], candidate_key="c4")
    derived = _derived_text(payload, candidates=candidates)

    # 先确认这条被改名的候选**自身**是结构合法的（否则它会被「首边非 primary」先拒，
    # 测的就不是重复这条出口了）。
    check_eq(str(payload["claim_candidates"][index_c5]["support"][0]["support_role"]), "primary",
             "被改名的候选（原 c5）自身首边是 primary——因此它只会因**标签重复**被拒，"
             "不会先被结构出口拦下（否则这条反例测的是别的出口）")

    error = _key_error(PW.parse_writer_proposals, derived, aliases=aliases,
                       require_natural_draft=LEGACY_REPLAY_DRAFT_MODE)
    check("候选标签 'c4' 重复" in error,
          f"整束解析在标签上直接 fail-closed（实得 {error!r}）")

    salvaged = PW._salvage_candidates(derived, aliases=aliases)
    check(salvaged is not None, "逐候选补救在这份派生输入上适用（既有幸存、也有被拒）")
    kept, rejected = salvaged
    reasons = {r["candidate_key"]: r["reason"] for r in rejected}
    check_eq(reasons.get("c4"), "candidate_key_duplicated",
             "被拒的是**第二条** c4（原 c5）：重复标签逐条记名，不是整批作废")
    check_eq([r["candidate_key"] for r in rejected], ["c4", *PRIMARY_ORDER_OFFENDERS],
             "同一束里三类越线各自独立记名：重复标签一条 + 首边非 primary 两条（按出现顺序）")
    dup = next(r for r in rejected if r["reason"] == "candidate_key_duplicated")
    check_eq(dup["claim_text"], str(payload["claim_candidates"][index_c5]["claim_text"]),
             "被拒条目带的是**模型自己写的**原文（它从未成为结构化对象，因此没有可派生的身份）")
    check_eq(len(kept) + len(rejected), len(payload["claim_candidates"]),
             "守恒：幸存 + 被拒 = 原束条数（改名不产生、也不抹掉任何候选）")
    check_eq([c["candidate_key"] for c in kept].count("c4"), 1,
             "第一条 c4 仍在幸存集里：补救拒掉的是重复的那一条，不是「遇到重复就删两个」")

    # --- 集合级问题在聚合绑定门上**不形成决定** -------------------------------------
    empty_manifest = NS.WriterMaterialManifest.create()
    check(isinstance(empty_manifest, NS.WriterMaterialManifest),
          "空 manifest 是合法状态（本节确实没有 Pack material），因此可以拿它只做**类型形状**的入口")
    subject = CBG.BindingSubjectRevision("claim_candidate", "ccand_探针", "sdrev_探针")
    cases = {
        "proposal_set_empty": (),
        "proposal_missing": (_ProposalStandIn("p2"), _ProposalStandIn("")),
        "proposal_duplicate": (_ProposalStandIn("p1"), _ProposalStandIn("p1")),
        "proposal_order_drift": (_ProposalStandIn("p2"), _ProposalStandIn("p1")),
    }
    observed = {}
    for expected_code, proposals in cases.items():
        message = ""
        try:
            CBG.decide_bindings(subject, proposals, None, manifest=empty_manifest)
        except CBG.ClaimBindingGateError as exc:
            message = str(exc)
        except Exception as exc:  # noqa: BLE001 —— 任何别的异常都是 FAIL（见下一条断言）
            message = f"非本门异常：{type(exc).__name__}: {exc}"
        observed[expected_code] = message.split(":")[0]
    check_eq(observed, {code: code for code in cases},
             "四种集合级问题各**只**由 `ClaimBindingGateError` 抛出、错误串以封闭结构码开头："
             "「不形成决定」在结构上无从伪装成一条 pass 或 fail")
    check(all(code in CBG.STRUCTURAL_PRIORITY
              for code in ("proposal_duplicate", "proposal_order_drift")),
          "其中两条已在 `STRUCTURAL_PRIORITY` 里（判定顺序即语义：先唯一性，再完整性/未知，最后顺序）")
    check("candidate_structure_invalid" in PW.CANDIDATE_CARVE_OUT_REASONS
          and "candidate_key_duplicated" not in PW.CANDIDATE_CARVE_OUT_REASONS,
          "裁出的封闭原因词表里有 `candidate_structure_invalid`，**没有** "
          "`candidate_key_duplicated`——两者不是同一层的记账：前者是「整响应已成形、这条候选自己"
          "结构非法」（可逐条记名进裁出审计），后者是**标签在候选集内不唯一**（它的存在本身就是"
          "「这一束的标签不可指」，只在补救这一路逐条记名，不构成对外审计的一句话）")
    check(PW.CANDIDATE_CARVE_OUT_VERSION == "cco-6"
          and PW.BATCH_SHAPE_CORRECTION_NOTE_VERSION == "bsc-1",
          "本模块复核的是 `cco-6` + `bsc-1` 这一版（r7b 跑在它们之前，两者不能混读）")
    check("schema_invalid" in PW.PROPOSAL_SET_REJECTION_KINDS,
          "记录里那两条的 `schema_invalid` 属于**整束**词表：它说的是「整响应连结构化对象都没"
          "成形」——这正是 §2 那一束当时的状态，也是它的逐候选归属必须为空的原因")
    details.append(
        "NOTE 这四条结构码**没有**对应任何一条 `ClaimBindingDecision`：`narr-4`/`narr-6` 的决定"
        "要么「非空唯一 proposal_ids 且逐边全 pass ⇒ pass」，要么「任一逐边失败 ⇒ fail」，"
        "集合级问题在里面**不可表达**——因此只能 fail-closed 抛出，不得静默重排或抽掉几条再算一遍。"
        "本模块的复核条件是「`authority=None` + 空 manifest」：这些码在**读任何内容之前**就抛出。")
    details.append(
        "NOTE 本模块**不**宣称这两条出口已被真实 run 行使过：r7b 那一轮跑在 `proposals-9` 上，"
        "逐候选裁出（`cco-4`）与批次形状纠正（`bsc-1`）都在它之后。这里做的是离线重放，"
        "不是一次新的验收。")


@dataclass
class _ProposalStandIn:
    """集合级出口的**最小替身**：它们只读 `proposed_support_id`。

    这一点不是声明，而是被上面的调用条件证明的：`authority=None`、manifest 为空时四个结构码
    照样抛出 ⇒ 抛出之前没有读过任何权威/材料内容。真实 proposal 的整链证据不在这里。
    """
    proposed_support_id: str


def _run_checks(check, check_eq, details) -> None:
    facts = _check_recorded_facts(check, check_eq, details)
    _check_primary_edge_order(check, check_eq, details, facts)
    _check_illegal_json(check, check_eq, details, facts)
    _check_high_risk(check, check_eq, details)
    _check_duplicate_and_aggregate(check, check_eq, details, facts)
    details.append(
        "NOTE 链级证据不在本模块：原束 → 逐候选裁出 → 新修订 → 聚合绑定 / 蕴含 / 接受边 / 定稿"
        "正文由 `evals/test_m930_3_prewrite_offline_replay.py` 用**同一批**留存返回重放。"
        "本模块只做「四条坏候选出口」这一层，两处夹具不各自为政。")
    details.append(
        "NOTE 本模块**不**宣布任何阶段关闭，也**不**把这两束读成通过：它们在 r7b 里是整节失败，"
        "在这里是四条出口的逐项取证。M930-3 / TS5 / 正式树门状态不变。")


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            details.append(f"FAIL {msg}")

    def check_eq(actual, expected, msg: str) -> None:
        check(actual == expected, f"{msg}（实得 {actual!r}，期望 {expected!r}）")

    ok, missing = _anchors_present()
    if not ok:
        skipped += 1
        details.append(f"SKIP 缺少 r7b 原始调用日志 / 拒绝表：{missing}。"
                       "离线重放需要那一轮真实运行的字节，它们不在版本控制里")
        return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}

    _run_checks(check, check_eq, details)
    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

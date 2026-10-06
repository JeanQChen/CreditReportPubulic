"""Eval: 权威事实按**呈现路由**落栏，未路由的事实不落到任何一栏（财务离线正文错栏定点修）。

用法: python -m evals.test_m930_3_routing_fact_placement

背景（r27 现场读数）：财务节的 25 条权威事实 `aspect_ids` **全为空**，而请求面里已经有
`presentation_routing.fact_columns` 指明每条事实的 `fact_id → presentation_column`。
`OfflineFactAssertionCitedProseClient` 当时在「没有登记轴」时退回**确定性轮转**，于是 18 句
中有 17 句被 `sentence_check` 的 `presentation_column_attribution` 轴（`scp-4`）正确判为错栏。

本模块钉住的是**选材规则**与它的边界，而不是改判结果：

1. **有路由轴时按路由选事实**：一条事实只进它自己那一栏；别的栏不得拿它充数。
2. **没有路由的事实不得被轮转塞进某一栏**：它们留 typed 去向
   （`FACT_PLACEMENT_DISPOSITIONS` 闭集），四种「没写出去」的情形**分开记**。
3. **两条轴都没有时才轮转**（附注 / 外部那一支的既有回退），这条回退**没有被删掉**。
4. **错栏硬核对没有被放宽**：它仍是 `applicable=True` 的判过的轴，把事实挪到别的栏照样
   `hard_error`。把这条轴改成「不适用」来刷绿是**禁止**的，本模块专门有反例钉住这一点。
5. **零路由栏如实留缺口**：`net_asset_level` / `rigid_debt_structure` 在 r27 的路由里本来
   就没有指标（`column_gaps` 自己已声明），正文不得用别处的指标去填。

夹具是 r27 那次离线运行**自己落下的产物**（`evaluation/results/m930_3_cited_offline_r27_phase_a/
financial/`）：请求面、事实行、路由声明逐字取自它，不另造一份。运行目录不存在时本模块
typed 跳过（PASS 计数为 0），不假装跑过。模块无公司代号、证券代码、固定页码或答案关键词。
不调 LLM、不联网、不写库、不建第二套 Harness/Pack/Writer。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import run_m930_3_cited_chain as CHAIN   # noqa: E402
from sections import cited_writer as CW               # noqa: E402
from sections import sentence_check as SC             # noqa: E402

#: r27 财务节的产物目录。它是一次**离线替身**运行，不是真实模型结果。
R27_FIN = (ROOT / "evaluation" / "results" / "m930_3_cited_offline_r27_phase_a" / "financial")

#: r27 路由里明确没有可用指标的两个栏目（`column_gaps` 自己声明的）。这里写的是**栏目 id**，
#: 不是「某公司没有这些指标」——换一家主体，这两栏有没有指标由那一轮的路由自己决定。
_GAP_COLUMNS = ("fin_solvency.net_asset_level", "fin_solvency.rigid_debt_structure")


def _load(name: str):
    path = R27_FIN / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _payload(*, subsections, facts, routing=None, diagnostic=(), tier_overrides=None):
    """一个最小请求面：只放本模块要判的那几个字段，其余留空。

    权威事实行的 id 字段名**随 `authority_kind` 变**（`financial_pack` →
    `financial_fact_id`，见 `NS.FACT_FIELD_BY_AUTHORITY_KIND`），所以夹具按真实请求面的
    形状写：带上 `authority_kind` 与**该 kind 自己的** id 字段，而不是写死 `fact_id`。
    呈现路由 `fact_columns[].fact_id` 用的是**路由自己的**字段名，与事实行的 id 值域相同。

    `diagnostic` 给那些**展示档被声明为 `diagnostic_only`** 的事实 id（`cp-8` 的两个标记位
    一起写出来：逐行 `display_tier` + 顶部 `diagnostic_slot_fact_keys`，与真实请求面同形）。
    `tier_overrides` 用来把某一行改成**别的**档位（例如 `required_body`，或空串=查不到档）
    ——正反例只差这一个字段，本条要证的正是「拦不拦由档位决定，不由别的什么决定」。
    """
    overrides = dict(tier_overrides or {})
    diagnostic_set = set(diagnostic)
    body = {"subsections": [{"subsection_id": sid, "title": sid,
                             "requirement_text": f"要求 {sid}",
                             "declared_aspect_ids": [sid],
                             "allowed_source_classes": []}
                            for sid in subsections],
            "materials": [],
            "authority_facts": [
                {"key": k, "authority_kind": "financial_pack",
                 "financial_fact_id": fid, "text": f"事实 {fid}",
                 "aspect_ids": [],
                 "display_tier": overrides.get(fid, "diagnostic_only"
                                               if fid in diagnostic_set else "")}
                for k, fid in facts]}
    if routing is not None:
        body["presentation_routing"] = routing
    body["diagnostic_slot_fact_keys"] = [k for k, fid in facts if fid in diagnostic_set
                                         and fid not in overrides]
    return body


def _face_subsections(doc: dict) -> list[dict]:
    """r27 的小节行 → 现行请求面的形状。**有界只读回放**，不是兼容猜读。

    r27 的请求面写在 `cwm-5` 上，小节行落的是**单数**键 `declared_aspect_id`；`cwm-6` 换成了
    集合 `declared_aspect_ids`。历史字节不能按新面重解，也不该因为旧而丢掉——那样「错栏」这条
    轴会在整份 r27 面上静默跳过，把「没判过」印成「都对上了」。

    所以这里显式读旧键，且只读旧键：新键出现就用它，两键都不在就当场抛。声明集合按旧 wire 的
    完整含义取（那时一个小节恰好声明一栏），不写「先试新的、不行退回旧的」那种两可读法。
    """
    out: list[dict] = []
    for row in doc["subsections"]:
        row = dict(row)
        if "declared_aspect_ids" in row:
            declared = [str(a) for a in (row["declared_aspect_ids"] or ())]
        elif row.get("declared_aspect_id"):
            declared = [str(row["declared_aspect_id"])]
        else:
            raise AssertionError(f"这一行小节既没有 `declared_aspect_ids` 也没有 "
                                 f"`declared_aspect_id`：{sorted(row)!r}")
        row.pop("declared_aspect_id", None)
        row["declared_aspect_ids"] = declared
        out.append(row)
    return out


def _fixture_manifest(doc: dict) -> CW.CitedWriterInputManifest:
    """由 r27 的真实材料/事实行 + **回放后的小节行**造一份夹具清单。

    身份是**夹具身份**：`cwm-6` 的 id 由现行身份体算出，因此它**不**等于 r27 那份 `cwm-5`
    清单的 `2c7572…`。这里不假装相等——`cwm-5` → `cwm-6` 换过身份体，把旧指纹搬过来才是
    造假。§4/§5 要的是「登记归属与呈现路由怎么被读」，不是那一轮的历史身份。
    """
    return CW.CitedWriterInputManifest.create(
        task_id=str(doc["task_id"]), section_id=str(doc["section_id"]),
        section_title=str(doc["section_title"]),
        pack_set_fingerprint=str(doc["pack_set_fingerprint"]),
        material_manifest_id=str(doc["material_manifest_id"]),
        material_manifest_fingerprint=str(doc["material_manifest_fingerprint"]),
        material_context_id=str(doc["material_context_id"]),
        material_context_fingerprint=str(doc["material_context_fingerprint"]),
        subsections=tuple(CW.CitedSubsectionSpec(**s) for s in _face_subsections(doc)),
        materials=tuple(CW._material_from_dict(m) for m in doc["materials"]),
        facts=tuple(CW._fact_from_dict(f) for f in doc["facts"]),
        presentation_routing=doc["presentation_routing"])


def _compose(payload, *, facts_per_subsection: int = 3):
    client = CHAIN.OfflineFactAssertionCitedProseClient(
        facts_per_subsection=facts_per_subsection)
    result = client.compose(messages=[{"content": json.dumps(payload, ensure_ascii=False)}],
                            system="", prompt_version=CW.CITED_WRITER_PROMPT_VERSION,
                            model_policy="offline_stub")
    return client, json.loads(result.text)


def _sentences_of(body, subsection_id: str) -> list[dict]:
    for row in body["subsections"]:
        if row["subsection_id"] == subsection_id:
            return [s for p in row["paragraphs"] for s in p["sentences"]]
    raise AssertionError(f"返回面里没有小节 {subsection_id!r}")


def _gap_of(body, subsection_id: str) -> dict:
    hits = [g for g in body["gaps"] if g["subsection_id"] == subsection_id]
    assert len(hits) == 1, f"{subsection_id!r} 应恰有一条缺口，实得 {len(hits)}"
    return hits[0]


def _disposition_of(client, fact_id: str) -> str:
    rows = client.calls[0]["placement_dispositions"]
    hits = [r for r in rows if r["fact_id"] == fact_id]
    assert len(hits) == 1, f"{fact_id!r} 应恰有一条去向，实得 {len(hits)}"
    return hits[0]["disposition"]


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

    def skip(msg: str) -> None:
        nonlocal skipped
        skipped += 1
        details.append(f"SKIP: {msg}")

    # ================================================= §0 前提
    details.append("## §0 前提：r27 财务节产物在场，且它们确实是这次读数的夹具")
    manifest_doc = _load("cited_input_manifest.json")
    prose_doc = _load("cited_prose.json")
    checks_doc = _load("sentence_checks.json")
    tables_doc = _load("cited_metric_tables.json")
    if None in (manifest_doc, prose_doc, checks_doc, tables_doc):
        skip(f"没有找到 {R27_FIN} 下的 r27 产物：本模块整份跳过（不假装跑过）")
        return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}

    routing = manifest_doc["presentation_routing"]
    routed = {row["fact_id"]: row.get("presentation_column") or ""
              for row in routing["fact_columns"]}
    facts = manifest_doc["facts"]
    check(len(routed) == len(facts) and not routing["unrouted_facts"],
          f"r27 的 {len(facts)} 条事实在路由里逐条有落栏（实测路由 {len(routed)} 条，"
          f"未路由 {len(routing['unrouted_facts'])} 条）")
    check(all(not f["aspect_ids"] for f in facts),
          "而这批事实的登记归属 `aspect_ids` **一条都没有**——错栏的入口正在这里")

    # ================================================= §1 规则：按路由选事实
    details.append("## §1 规则：有路由轴就按路由选事实，一条事实只进它自己那一栏")
    payload = _payload(subsections=["col-a", "col-b"],
                       facts=[("f01", "fact-a"), ("f02", "fact-b"), ("f03", "fact-orphan")],
                       routing={"routing_version": "fpr-1",
                                "fact_columns": [
                                    {"fact_id": "fact-a", "presentation_column": "col-a"},
                                    {"fact_id": "fact-b", "presentation_column": "col-b"}]})
    client, body = _compose(payload)
    check(client.calls[0]["selection_axis"] == "presentation_routing.fact_columns",
          f"本支这次用的选材轴是呈现路由（实测 {client.calls[0]['selection_axis']!r}）")
    check([s["citations"] for s in _sentences_of(body, "col-a")] == [["f01"]],
          f"正例：col-a 只拿到路由给它的 fact-a（实测 "
          f"{[s['citations'] for s in _sentences_of(body, 'col-a')]}）")
    check([s["citations"] for s in _sentences_of(body, "col-b")] == [["f02"]],
          "正例：col-b 只拿到路由给它的 fact-b（没有顺手把 f01 也写进去）")
    check(all("f03" not in s["citations"]
              for sid in ("col-a", "col-b") for s in _sentences_of(body, sid)),
          "反例：**没有路由**的 f03 一条正文都没进——它不得被轮转塞进任何一栏")

    # 反例 1b：路由指向别栏的事实，不得出现在本栏
    payload_b = _payload(subsections=["col-a", "col-b"],
                         facts=[("f01", "fact-a"), ("f02", "fact-b")],
                         routing={"routing_version": "fpr-1",
                                  "fact_columns": [
                                      {"fact_id": "fact-a", "presentation_column": "col-a"}]})
    _c, body_b = _compose(payload_b)
    check(_sentences_of(body_b, "col-b") == []
          and _gap_of(body_b, "col-b")["reason"] == "no_source_in_manifest",
          f"反例：本栏没有路由给它的事实 ⇒ 零句 + `no_source_in_manifest`"
          f"（实测 {_gap_of(body_b, 'col-b')['reason']!r}）——不用别栏的事实凑字")

    # 反例 1c：两条轴都没有时，轮转回退**仍在**（附注 / 外部那一支的既有行为）
    payload_c = _payload(subsections=["col-a", "col-b"],
                         facts=[("f01", "fact-a"), ("f02", "fact-b")])
    client_c, body_c = _compose(payload_c, facts_per_subsection=1)
    check(client_c.calls[0]["selection_axis"] == "rotation",
          f"反例：既没有登记轴也没有路由轴 ⇒ 退回确定性轮转"
          f"（实测 {client_c.calls[0]['selection_axis']!r}）——这条回退没有被删掉")
    check(len(_sentences_of(body_c, "col-a")) == 1 and len(_sentences_of(body_c, "col-b")) == 1,
          "反例：轮转回退下两个小节各拿到一条未写过的事实")

    # ================================================= §2 typed 去向
    details.append("## §2 typed 去向：五种「没写出去」的情形分开记，不合并成一句「没用上」")
    check(CHAIN.FACT_PLACEMENT_DISPOSITIONS == (
        "placed", "fact_not_routed", "routed_column_not_in_request",
        "routed_column_cap_reached", "no_column_axis_in_request",
        "diagnostic_slot_not_prose"),
        f"去向闭集逐字钉住（实测 {CHAIN.FACT_PLACEMENT_DISPOSITIONS}）")
    check(bool(CHAIN.FACT_PLACEMENT_NOTE.strip()) and "覆盖" in CHAIN.FACT_PLACEMENT_NOTE,
          "去向文件自带一句「这是去向、不是覆盖结论」的说明（`routed_column_cap_reached` "
          "是**本轮**句数上限，读成「这一栏没有内容」就是把写作预算当覆盖）")

    payload_d = _payload(subsections=["col-a", "col-b"],
                         facts=[("f01", "fact-a"), ("f02", "fact-b"), ("f03", "fact-orphan"),
                                ("f04", "fact-elsewhere"), ("f05", "fact-a2")],
                         routing={"routing_version": "fpr-1",
                                  "fact_columns": [
                                      {"fact_id": "fact-a", "presentation_column": "col-a"},
                                      {"fact_id": "fact-a2", "presentation_column": "col-a"},
                                      {"fact_id": "fact-b", "presentation_column": "col-b"},
                                      {"fact_id": "fact-elsewhere",
                                       "presentation_column": "col-not-in-request"}]})
    client_d, _body_d = _compose(payload_d, facts_per_subsection=1)
    check(client_d.calls[0]["routing_axis_present"] is True,
          "调用账本如实记下「这一次有没有路由轴」")
    check(_disposition_of(client_d, "fact-a") == "placed",
          "进正文的事实记 `placed`（有去向的不是只有没写出去的那些）")
    check(_disposition_of(client_d, "fact-orphan") == "fact_not_routed",
          "路由里没有它 ⇒ `fact_not_routed`（要去补的是路由声明）")
    check(_disposition_of(client_d, "fact-elsewhere") == "routed_column_not_in_request",
          "路由给了栏目、本次请求面没有这一栏 ⇒ `routed_column_not_in_request`")
    check(_disposition_of(client_d, "fact-a2") == "routed_column_cap_reached",
          "同栏事实超出每栏句数上限 ⇒ `routed_column_cap_reached`（本轮写作预算，不是归属错）")
    rows = client_d.calls[0]["placement_dispositions"]
    check({r["fact_id"] for r in rows}
          == {f["financial_fact_id"] for f in payload_d["authority_facts"]},
          "**每一条**事实都恰有一条去向：没有「没记账」的落空事实")
    # 去向要能**落盘**才算真的留下来了：`fact_placement.json` 读的是替身身上的这个属性，
    # 与 `withheld_candidates.json` 读 `withheld` 是同一个形状。
    check(list(getattr(client_d, "placement_dispositions", ())) == rows,
          "同一份去向也挂在替身身上（`fact_placement.json` 逐条回查用的就是它）")
    check(all(r["disposition"] in CHAIN.FACT_PLACEMENT_DISPOSITIONS for r in rows),
          "去向取值逐条落在闭集内（不新造词）")

    # 两条轴都没有时才轮转（§1 反例 1c）：轮转没取到的那一条，去向记「本支没有栏目轴」。
    _client_e, _body_e = _compose(_payload(subsections=["col-a"],
                                           facts=[("f01", "fact-a"), ("f02", "fact-b")]),
                                  facts_per_subsection=1)
    check(_disposition_of(_client_e, "fact-b") == "no_column_axis_in_request",
          "两条轴都没有、轮转也没轮到它 ⇒ 去向记 `no_column_axis_in_request`"
          "（本支这次拿不到任何一栏归属，与「路由里没有它」是两件事）")

    # ============================================ §2b 展示档：诊断槽位事实不进普通正文
    details.append("## §2b 展示档（`cp-8`）：`diagnostic_only` 的事实**栏目对得上也不进正文**")
    _routing_two = {"routing_version": "fpr-1",
                    "fact_columns": [
                        {"fact_id": "fact-a", "presentation_column": "col-a"},
                        {"fact_id": "fact-b", "presentation_column": "col-a"}]}
    client_f, body_f = _compose(_payload(
        subsections=["col-a"],
        facts=[("f01", "fact-a"), ("f02", "fact-b")], routing=_routing_two,
        diagnostic=("fact-a",)))
    check([s["citations"] for s in _sentences_of(body_f, "col-a")] == [["f02"]],
          "正例：两条事实落**同一栏**、登记与路由都成立，其中一条是诊断槽位 ⇒ 正文只写另一条"
          f"（实测 {[s['citations'] for s in _sentences_of(body_f, 'col-a')]}）"
          "——这不是错栏，是展示角色，所以它**不**以「栏」为单位拦")
    check(_disposition_of(client_f, "fact-a") == "diagnostic_slot_not_prose",
          "被挡下的那一条留 typed 去向 `diagnostic_slot_not_prose`，与另外五种区分开"
          "（前几种说「为什么没轮上」，这一种说「按展示角色就不该写进正文」）")

    _client_g, body_g = _compose(_payload(
        subsections=["col-a"], facts=[("f01", "fact-a"), ("f02", "fact-b")],
        routing=_routing_two, tier_overrides={"fact-a": "required_body"}))
    check([s["citations"] for s in _sentences_of(body_g, "col-a")] == [["f01"], ["f02"]],
          "反例：同一份请求面，只把那条的档位换成 `required_body` ⇒ 它照常进正文"
          f"（实测 {[s['citations'] for s in _sentences_of(body_g, 'col-a')]}）"
          "——拦不拦由**档位**决定，不由别的什么决定")

    _client_h, body_h = _compose(_payload(
        subsections=["col-a"], facts=[("f01", "fact-a"), ("f02", "fact-b")],
        routing=_routing_two, tier_overrides={"fact-a": ""}))
    check(any("f01" in s["citations"] for s in _sentences_of(body_h, "col-a")),
          "**边界**：档位**查不到**（空串）⇒ **不**按诊断槽位拦。空串是「未知」，与「已声明"
          "诊断槽位」是两件事：本轴只拦有声明的那一类，不为未知造一道假门"
          "（写作侧对未知的处置是提示词第 12 条「吃不准就不要用它」，那是一条写作纪律，"
          "不是机械判据）")

    # ================================================= §3 零路由栏如实留缺口
    details.append("## §3 零路由栏：正文不得用别处的指标去填，缺口要说出路由自己的结论")
    gap_payload = _payload(subsections=["col-empty"],
                           facts=[("f01", "fact-a")],
                           routing={"routing_version": "fpr-1",
                                    "fact_columns": [
                                        {"fact_id": "fact-a", "presentation_column": "col-other"}],
                                    "column_gaps": [
                                        {"column": "col-empty",
                                         "reason": "no_registered_metric_in_selected_facts",
                                         "contract_display_tier": "required_body"}]})
    _client_g, body_g = _compose(gap_payload)
    gap = _gap_of(body_g, "col-empty")
    check(gap["reason"] == "no_source_in_manifest",
          f"零路由栏如实报 `no_source_in_manifest`（实测 {gap['reason']!r}）")
    check("presentation_routing.fact_columns" in gap["detail"],
          "缺口文案说的是**呈现路由**这条轴（不是「登记归属里没有」，那是要分别修的另一件事）")
    check("no_registered_metric_in_selected_facts" in gap["detail"],
          "路由自己登记的 typed 理由被照抄进缺口（不在这里另造一套词）")

    # ================================================= §4 r27 现场：按路由落栏
    details.append("## §4 r27 现场：重走一次替身，逐句栏目归属与两条既有缺口")
    request = {"subsections": _face_subsections(manifest_doc),
               "materials": [],
               # 与真实请求面同形：财务权威事实的 id 走 `financial_fact_id` 这个 kind 自有字段。
               "authority_facts": [{"key": f["citation_key"], "authority_kind": "financial_pack",
                                    "financial_fact_id": f["fact_id"],
                                    "text": f["text"], "aspect_ids": list(f["aspect_ids"])}
                                   for f in facts],
               "presentation_routing": routing}
    client_r, body_r = _compose(request)
    check(client_r.calls[0]["selection_axis"] == "presentation_routing.fact_columns",
          "r27 现场这次按呈现路由选材（不再轮转）")
    placed = 0
    for row in body_r["subsections"]:
        declared = row["subsection_id"]
        for sentence in [s for p in row["paragraphs"] for s in p["sentences"]]:
            for key in sentence["citations"]:
                fact_id = next(f["fact_id"] for f in facts if f["citation_key"] == key)
                if routed.get(fact_id) != declared:
                    placed += 1
    check(placed == 0,
          f"清点：**零**句引用的事实落在别栏（旧读数在这里是 17 句错栏；实测 {placed}）")
    for column in _GAP_COLUMNS:
        sentence_count = len(_sentences_of(body_r, column))
        gap_row = _gap_of(body_r, column)
        check(sentence_count == 0 and gap_row["reason"] == "no_source_in_manifest",
              f"`{column}` 在路由里本来就没有指标 ⇒ 零句 + `no_source_in_manifest` 如实保留"
              f"（实测 {sentence_count} 句 / {gap_row['reason']!r}）")
    check(all("no_registered_metric_in_selected_facts" in _gap_of(body_r, c)["detail"]
              for c in _GAP_COLUMNS),
          "这两个缺口都把路由自己登记的理由带了出来（读者能看出空栏是呈现层的结论）")

    # ================================================= §5 错栏硬核对没有被放宽
    details.append("## §5 反例：把事实挪到别的栏，`presentation_column_attribution` 照样判硬错")
    manifest = _fixture_manifest(manifest_doc)
    pairs = [(f["citation_key"], routed[f["fact_id"]]) for f in facts
             if routed.get(f["fact_id"])]
    key_right = None
    for key, column in pairs:
        other = sorted({c for _k, c in pairs if c != column})
        if other:
            key_right = (key, column, other[0])
            break
    assert key_right is not None, "夹具里应有至少两个不同栏目的事实"
    key, right_column, wrong_column = key_right
    fact_text = next(f["text"] for f in facts if f["citation_key"] == key)
    sentence = CW.CitedSentence(sentence_id="s0001", text=fact_text, citations=(key,))
    # 两次核对都显式给出**清单里真实存在**的 `declared_aspect_ids`：不传它会让这条轴退回
    # 「调用方没有声明栏目」的不适用分支，那样验的就不是「错栏被拒」而是「没判」。
    bad = [r for r in SC.check_sentence(sentence=sentence, subsection_id=wrong_column,
                                        paragraph_id="p1", manifest=manifest,
                                        declared_aspect_ids=(wrong_column,))
           if r.check_kind == "presentation_column_attribution"]
    check(len(bad) == 1 and bad[0].applicable is True,
          f"这条轴**判过了**（`applicable={bad[0].applicable}`），没有被改成「不适用」来刷绿")
    check(bad[0].verdict == "hard_error"
          and bad[0].failure_reason == "sentence_presentation_column_mismatch",
          f"引用「{right_column}」的事实却写在「{wrong_column}」⇒ `{bad[0].failure_reason}`"
          f" 硬错（实测 {bad[0].verdict}）")
    good = [r for r in SC.check_sentence(sentence=sentence, subsection_id=right_column,
                                         paragraph_id="p1", manifest=manifest,
                                         declared_aspect_ids=(right_column,))
            if r.check_kind == "presentation_column_attribution"]
    check(len(good) == 1 and good[0].verdict == "pass" and good[0].applicable is True,
          "同一句写在它自己那一栏 ⇒ 过，且同样是**判过**的一条记录"
          f"（`applicable={good[0].applicable}`）")

    # ================================================= §6 确定性财务表没被这次改动碰
    details.append("## §6 回归：四张确定性财务指标表的逐格值 / 期间 / 单位 / 覆盖计数")
    tables = tables_doc["tables"]
    check(len(tables) == 4, f"仍是四张指标表（实测 {len(tables)}）")
    check({t["unit"] for t in tables} <= {"yuan", "ratio", "percent"},
          f"单位取值落在闭集内（实测 {sorted({t['unit'] for t in tables})}）")
    check(tables_doc["coverage_counts"] == {"available_not_displayed": 1, "displayed": 24},
          f"覆盖计数不变：24 displayed + 1 available_not_displayed"
          f"（实测 {tables_doc['coverage_counts']}）")
    check(tables_doc["refusals"] == [],
          f"零拒答（实测 {len(tables_doc['refusals'])} 条）")
    cells_aligned = True
    for table in tables:
        for row in table["rows"]:
            if not (len(row["cells"]) == len(row["fact_ids"]) == len(row["period_texts"])):
                cells_aligned = False
    check(cells_aligned, "每格都同时有 `cells` / `fact_ids` / `period_texts` 三个坐标，长度对齐")
    check(all(row.get("presentation_column")
              for table in tables for row in table["rows"]),
          "每一行仍带自己的呈现栏目（表的呈现位置不由本次改动决定）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    report = main()
    for line in report["details"]:
        print(line)
    print(f"\npassed = {report['passed']}, failed = {report['failed']}, "
          f"skipped = {report['skipped']}")
    raise SystemExit(1 if report["failed"] else 0)

"""Eval: 写作调用的**失败前留存**（`ccj-1`）——输入、可见回复、解析状态各自落盘。

用法: python -m evals.test_m930_3_cited_call_journal

存在的理由是一次具体的丢失：真实 r2（call_id `5856a5b820a14b2ca50bd3e31924264d`）在 `logs/llm`
里留下了完整回复，但**运行目录里什么都没有**——没有输入清单身份、没有可见回复、没有解析状态。
于是那一轮的正规化结果只能叫**离线派生诊断**。本模块钉住「以后不会再这样丢」这件事，逐条证明：

1. **按发生顺序落盘，且每一步都留得住**：`record_input`（发请求前）→ `record_reply`（收到回复
   后、解析前）→ `record_parse`。前一步的内容不会被后一步顶掉；解析**失败**时回复仍在盘上。
2. **不留隐藏推理**：留存里存的只可能是调用方交出来的**可见文本**；`hidden_reasoning_persisted`
   在文件级与回复级都恒为 `false`，且这是一条**可断言**的声明而不是措辞。
3. **恒不可发布**：`publishable` 恒为 `false`（写失败时也一样）。
4. **留存写失败不得顶掉原异常**：`record_*` 从**不抛**。写不进去只记进 `write_errors`——证据写
   不进去是证据的问题，不能因此把「这一轮为什么失败」换成「写文件失败了」。
5. **请求面留原文而不是摘要**：长度与哈希按**原文**记；超限时如实记 `request_face_truncated`
   并把留下的那一截截在明写的上限处，而不是悄悄截断。
6. **形状自查只查自洽**：`assert_retention_shape` 逐条报出缺失/不一致（版本、两个恒定声明、
   `reply` 段自己的声明、`input` 段是否在场），不评价正文质量。

夹具是**临时目录**里的合成输入：无真实请求、无公司代号、不联网、不写库、不碰历史 run。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sections import cited_call_journal as CCJ     # noqa: E402

_VISIBLE = '{"subsections": [{"subsection_id": "sub-a"}]}'


def _manifest() -> SimpleNamespace:
    """最小的清单替身：只带留存真正会读的那几个属性。"""
    materials = (SimpleNamespace(citation_key="m01", member_ref="ref-m01"),
                 SimpleNamespace(citation_key="m02", member_ref="ref-m02"))
    facts = (SimpleNamespace(citation_key="f01"),)
    subsections = (SimpleNamespace(subsection_id="sub-a", declared_aspect_ids=("aspect-a",),
                                   requirement_text="要求 A"),)
    return SimpleNamespace(task_id="task-1", manifest_id="cwm-abc", schema_version="cwm-1",
                           materials=materials, facts=facts, subsections=subsections,
                           fingerprint=lambda: "fp-abc")


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

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "run"
        journal = CCJ.journal_for(out)
        check(journal.path.name == CCJ.CITED_CALL_JOURNAL_FILENAME,
              f"留存文件名固定为 `{CCJ.CITED_CALL_JOURNAL_FILENAME}`（读回侧按名字找，不靠通配）")
        check(not journal.path.exists(), "建簿本身不落盘（还没发生任何事就不该有文件）")

        # ========================================== §1 三个记录点按发生顺序
        details.append("## §1 三个记录点：按发生顺序落盘，前一步不被后一步顶掉")
        journal.record_input(section_id="company", manifest=_manifest(),
                             prompt_version="cp-1", model_policy="cwm-1",
                             model="some-model", thinking={"type": "disabled"},
                             request_face="这是一份请求面", system_text="系统提示")
        on_disk = journal.load()
        check(on_disk["input"]["request_face"] == "这是一份请求面",
              "发请求**之前**请求面原文已经在盘上（不是摘要、不是哈希）")
        check(on_disk["input"]["manifest_id"] == "cwm-abc"
              and on_disk["input"]["manifest_fingerprint"] == "fp-abc"
              and on_disk["input"]["manifest_schema_version"] == "cwm-1",
              "⇒ 「这次送给模型的是哪一份清单」可回查（id + 指纹 + schema 版本）")
        check(on_disk["input"]["thinking"] == {"type": "disabled"},
              "⇒ 推理档位随请求一起留：截断与否事后要能对上")
        check(on_disk["input"]["material_keys"] == ["m01", "m02"]
              and on_disk["input"]["fact_keys"] == ["f01"],
              "⇒ 逐条材料/事实引用键在场（两轴各自成列，不混）")
        check(on_disk["input"]["material_refs"] == ["ref-m01", "ref-m02"],
              "⇒ 材料行的 `member_ref` 单独成列（键与去向不是同一件事）")
        check(on_disk["input"]["subsections"] == [
            {"subsection_id": "sub-a", "declared_aspect_ids": ["aspect-a"],
             "requirement_text": "要求 A"}],
            "⇒ 逐小节声明栏目进留存（事后能回答「这一小节当时声明的是哪一栏」）")
        check(on_disk["input"]["system_sha256"]
              == CCJ._sha256("系统提示") and on_disk["input"]["system_sha256"],
              "系统提示只留哈希（它是运行时指令，不进证据正文）")
        check(on_disk["reply"] is None and on_disk["parse"] is None,
              "这一步之后 `reply`/`parse` 仍为空（顺序可读，不是一次性覆盖）")
        details.append("NOTE §1a：输入面身份与原文同时在场。")

        journal.record_reply(call_id="call-x", status="ok", error="", text=_VISIBLE,
                             finish_reason="end_turn", input_tokens=11, output_tokens=22,
                             latency_ms=33, model="some-model")
        after_reply = journal.load()
        check(after_reply["reply"]["text"] == _VISIBLE,
              "收到回复后**解析之前**可见正文逐字落盘")
        check(after_reply["reply"]["visible_chars"] == len(_VISIBLE)
              and after_reply["reply"]["visible_sha256"] == CCJ._sha256(_VISIBLE),
              "⇒ 长度与哈希当场记下（事后可证明盘上这份没被改过）")
        check(after_reply["reply"]["finish_reason"] == "end_turn"
              and after_reply["reply"]["output_tokens"] == 22
              and after_reply["reply"]["latency_ms"] == 33,
              "⇒ finish_reason / usage / 延迟一并留（「正常结束」与「被截断」可区分）")
        check(after_reply["input"] is not None and after_reply["input"]["manifest_id"] == "cwm-abc",
              "记回复**没有顶掉**输入段（一份文件里三件事各占一栏）")
        details.append("NOTE §1b：解析还没发生，正文已经留住了。")

        journal.record_parse(ok=False, error_type="NarrativeSchemaError",
                             error="CitedSubsection 含未登记字段 ['gaps']",
                             reason="reply_field_malformed")
        after_fail = journal.load()
        check(after_fail["parse"]["ok"] is False
              and after_fail["parse"]["error_type"] == "NarrativeSchemaError"
              and after_fail["parse"]["reason"] == "reply_field_malformed",
              "解析**失败**记异常类型 + typed 原因码 + 消息")
        check(after_fail["reply"]["text"] == _VISIBLE,
              "⇒ 解析失败时正文**仍在盘上**（这正是 r2 缺的那一半）")
        check(after_fail["input"] is not None,
              "⇒ 失败时输入段也仍在（三件事一起构成「这一轮到底发生了什么」）")
        details.append("NOTE §1c：失败路径不留白。")

        journal.record_parse(ok=True, draft_id="cwd-xyz", sentence_count=27,
                             subsection_count=18,
                             normalization={"normalize_version": "crn-1", "sentence_ids": []})
        ok_body = journal.load()
        check(ok_body["parse"]["draft_id"] == "cwd-xyz"
              and ok_body["parse"]["sentence_count"] == 27
              and ok_body["parse"]["subsection_count"] == 18,
              "解析成功记草稿身份与句/小节数")
        check(ok_body["parse"]["normalization"]["normalize_version"] == "crn-1",
              "⇒ 归一化台账（模型原 ID / 结构位置 / 新 ID 的映射）随留存落盘")
        check(journal.load() == journal.to_dict(),
              "盘上与内存里逐字相同（每次 `_flush` 都是整体原子替换 ⇒ 读到的一定是完整快照）")
        details.append("NOTE §1d：编号台账属留存证据，不属内容寻址。")

        # ========================================== §2 三条边界
        details.append("## §2 三条边界：不留隐藏推理、恒不可发布、写失败不抛")
        for name, body in (("input 之后", after_reply), ("解析失败之后", after_fail),
                           ("解析成功之后", ok_body)):
            check(body["publishable"] is False
                  and body["hidden_reasoning_persisted"] is False
                  and body["reply"]["hidden_reasoning_persisted"] is False,
                  f"{name}：`publishable` 与两处 `hidden_reasoning_persisted` 恒为 false")
        blob = json.dumps(ok_body, ensure_ascii=False)
        check("reasoning_content" not in blob and "thinking_content" not in blob,
              "序列化体里没有隐藏推理字段（留的只有调用方交出来的可见文本）")
        details.append("NOTE §2a：这两条恒为 false 是可断言的声明，不是措辞。")

        broken_parent = Path(tmp) / "not-a-dir"
        broken_parent.write_text("我是一个文件，不是目录", encoding="utf-8")
        doomed = CCJ.CitedCallJournal(broken_parent / "cited_call_journal.json")
        raised = ""
        try:
            doomed.record_input(section_id="s", manifest=_manifest(), prompt_version="cp-1",
                                model_policy="cwm-1", request_face="请求面")
            doomed.record_reply(call_id="c", status="failed", error="boom", text="")
            doomed.record_parse(ok=False, error_type="X", error="y", reason="z")
        except Exception as exc:  # noqa: BLE001 - 这一路**必须**什么都不抛
            raised = f"{type(exc).__name__}: {exc}"
        check(raised == "", f"留存写失败时 `record_*` 一次都没有抛（实际：{raised or '未抛'}）")
        check(len(doomed.write_errors) == 3,
              f"三处写失败各记一条 `write_errors`（实际 {len(doomed.write_errors)} 条）")
        check(doomed.to_dict()["publishable"] is False
              and doomed.to_dict()["input"] is not None,
              "⇒ 写不进去也照旧回答「这一轮发生了什么」，且仍然不可发布")
        details.append("NOTE §2b：证据写不进去是证据的问题，不得把原异常顶掉。")

        # ========================================== §3 形状自查
        details.append("## §3 形状自查：只查自洽，不评价正文")
        check(CCJ.assert_retention_shape(ok_body) == (),
              "完整留存通过形状自查（返回空序列）")
        for mutate, token in (
                ({"publishable": True}, "publishable"),
                ({"hidden_reasoning_persisted": True}, "hidden_reasoning_persisted"),
                ({"journal_version": "ccj-0"}, "journal_version"),
                ({"input": None}, "input 段"),
                ({"reply": {"hidden_reasoning_persisted": True}}, "reply.hidden_reasoning_persisted")):
            body = json.loads(json.dumps(ok_body))
            body.update(mutate)
            problems = CCJ.assert_retention_shape(body)
            check(any(token in p for p in problems),
                  f"篡改 {list(mutate)} ⇒ 被点名报出（{list(problems)}）")
        details.append("NOTE §3：自查报告的是**账目是否自洽**，不是正文质量。")

        # ========================================== §4 请求面：留原文，超限如实记
        details.append("## §4 请求面留原文；超限如实记，不悄悄截断")
        limit = CCJ.CITED_JOURNAL_REQUEST_FACE_LIMIT
        long_face = "请" * (limit + 10)
        big = CCJ.journal_for(Path(tmp) / "big")
        big.record_input(section_id="company", manifest=_manifest(), prompt_version="cp-1",
                         model_policy="cwm-1", request_face=long_face)
        big_body = big.load()["input"]
        check(big_body["request_face_truncated"] is True,
              "超限时**如实记** `request_face_truncated=true`")
        check(big_body["request_face_chars"] == limit + 10,
              "⇒ 记的是**原文**长度（不是截断后的长度）")
        check(big_body["request_face_sha256"] == CCJ._sha256(long_face),
              "⇒ 哈希按**原文**算（事后可比对「盘上这截是不是那份请求面的前缀」）")
        check(len(big_body["request_face"]) == limit,
              f"留下的那一截正好截在明写的上限 {limit} 处")
        short = CCJ.journal_for(Path(tmp) / "small")
        short.record_input(section_id="company", manifest=_manifest(), prompt_version="cp-1",
                           model_policy="cwm-1", request_face="短请求面")
        check(short.load()["input"]["request_face_truncated"] is False,
              "没超限就不带截断标记（这个字段不是装饰）")
        details.append("NOTE §4：请求面原文是「模型看到了什么」本身，摘要回答不了这个问题。")

        details.append(
            "NOTE 本模块只证明**留存的结构边界**：留得下、留得对、留不下也不吞掉原异常。"
            "留存内容是**未核对、不可发布**的证据，不是成品正文。")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

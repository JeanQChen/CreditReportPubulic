"""Eval: llm.client —— LLMResponse / chat_with_usage / 唯一日志文件名（Phase 3 Batch B commit 1）。

用法: python -m evals.test_llm_client

断言（全 mock，不触真实 API）：
- LLMResponse dataclass 字段完整（text/input_tokens/output_tokens/latency_ms/model/call_id/finish_reason）；
- chat_with_usage 返回 usage / latency / call_id / finish_reason；
- provider 未返回 usage → input/output_tokens 为 None（绝不记 0）；
- chat 向后兼容：委托 chat_with_usage 并返回其 .text（str）；
- _log_llm_call 以 call_id 唯一文件名落盘（同秒不覆盖）；
- 日志记录含 prompt_version / finish_reason / latency_ms / call_id，且不含 API Key；
- reject_truncated=True 时 `finish_reason ∈ (length, max_tokens)` 判本次调用**失败**
  （抛 LLMTruncatedResponse，不返回残缺文本；原始字节仍按 call_id 落盘）；缺省 False 不变。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm.client as LC


class _FakeBlock:
    def __init__(self, text):
        self.text = text


class _FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResp:
    def __init__(self, text, usage, stop_reason):
        self.content = [_FakeBlock(text)]
        self.usage = usage
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, resp):
        self.resp = resp

    def create(self, **kwargs):
        return self.resp


class _FakeClient:
    def __init__(self, resp):
        self.messages = _FakeMessages(resp)


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    tmp_log = Path(tempfile.mkdtemp(prefix="eval_llm_client_"))

    # ---- LLMResponse dataclass 字段 ----
    fields = set(LC.LLMResponse.__dataclass_fields__)
    expected_fields = {"text", "input_tokens", "output_tokens", "latency_ms",
                       "model", "call_id", "finish_reason"}
    check(fields == expected_fields, "LLMResponse 字段完整且无多余")

    # ---- chat_with_usage：正常 usage ----
    resp_ok = _FakeResp("hello", _FakeUsage(10, 5), "end_turn")
    with patch.object(LC, "get_client", return_value=_FakeClient(resp_ok)), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        r = LC.chat_with_usage(
            [{"role": "user", "content": "hi"}], system="sys",
            prompt_version="research_action_v1")
    check(r.text == "hello", "chat_with_usage 返回 text")
    check(r.input_tokens == 10 and r.output_tokens == 5, "usage 正常返回 token 数")
    check(r.finish_reason == "end_turn", "finish_reason 正确捕获")
    check(r.model == LC.LLM_MODEL, "model 使用默认 LLM_MODEL")
    check(r.call_id != "" and r.latency_ms >= 0, "call_id/latency_ms 已填充")

    # ---- chat_with_usage：usage 缺失 → None ----
    resp_none = _FakeResp("x", None, None)
    with patch.object(LC, "get_client", return_value=_FakeClient(resp_none)), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        r2 = LC.chat_with_usage([{"role": "user", "content": "hi"}])
    check(r2.input_tokens is None and r2.output_tokens is None,
          "provider 未返回 usage → input/output_tokens 为 None（不记 0）")
    check(r2.finish_reason is None, "finish_reason 缺失 → None")

    # ---- chat 向后兼容：返回 str ----
    with patch.object(LC, "get_client", return_value=_FakeClient(resp_ok)), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        txt = LC.chat([{"role": "user", "content": "hi"}])
    check(txt == "hello" and isinstance(txt, str), "chat 向后兼容返回 str（委托 chat_with_usage）")

    # ---- 唯一日志文件名：同秒不覆盖 ----
    with patch.object(LC, "get_client", return_value=_FakeClient(resp_ok)), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        a = LC.chat_with_usage([{"role": "user", "content": "a"}], prompt_version="pv")
        b = LC.chat_with_usage([{"role": "user", "content": "b"}], prompt_version="pv")
    log_files = list(tmp_log.glob("*.jsonl"))
    check(len(log_files) >= 2 and a.call_id != b.call_id, "两次调用各落独立文件（不覆盖）")

    # ---- 日志内容含审计字段、不含 Key ----
    # 注意：文件名时间戳只有毫秒分辨率，同一毫秒内的多条日志在按名排序时以随机
    # call_id 决定先后，因此 `written[-1]` 不能代表"最后一次调用"。这里改为按
    # call_id 定位 a/b 两条记录（断言更强，且与文件枚举顺序无关）。
    written = [json.loads(p.read_text(encoding="utf-8")) for p in log_files]
    check(all(rec["call_id"] for rec in written), "每条日志都带非空 call_id")
    by_call_id = {rec["call_id"]: rec for rec in written}
    rec_a, rec_b = by_call_id.get(a.call_id), by_call_id.get(b.call_id)
    check(rec_a is not None and rec_b is not None
          and rec_a["prompt_version"] == "pv" and rec_b["prompt_version"] == "pv",
          "日志含 call_id / prompt_version（按 call_id 定位，不依赖枚举顺序）")
    rec = rec_b if rec_b is not None else (rec_a if rec_a is not None else written[-1])
    check("finish_reason" in rec and "latency_ms" in rec,
          "日志含 finish_reason / latency_ms")
    serialized = json.dumps(written, ensure_ascii=False)
    check("DEEPSEEK_API_KEY" not in serialized and "sk-" not in serialized.lower(),
          "日志不含 API Key")

    # ---- 截断拒绝：`reject_truncated=True` 时截断算**失败**，不返回残缺文本 ----
    # 这个开关是**逐调用点**的：缺省 False（其它管线自己处置短输出，既有行为逐字节不变），
    # 本链的写作适配器显式开启。判据在咽喉处钉一次：残缺内容绝不作为返回值流出。
    truncated = _FakeResp('{"proposals": [{"topic_id": "t1"', _FakeUsage(20, 8192), "max_tokens")
    with patch.object(LC, "get_client", return_value=_FakeClient(truncated)), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        raised = None
        returned = None
        try:
            returned = LC.chat_with_usage([{"role": "user", "content": "hi"}],
                                          prompt_version="pv", max_tokens=8192,
                                          reject_truncated=True)
        except LC.LLMTruncatedResponse as exc:
            raised = exc
    check(raised is not None and returned is None,
          "reject_truncated=True：截断必须抛 LLMTruncatedResponse，不返回残缺文本")
    check(raised is not None and raised.response.finish_reason == "max_tokens"
          and raised.response.output_tokens == 8192 and raised.response.call_id,
          "异常必须带那次响应的身份（finish_reason / usage / call_id）")
    check("length" in LC.TRUNCATION_STOP_REASONS and "max_tokens" in LC.TRUNCATION_STOP_REASONS,
          "两种 provider 口径（length / max_tokens）都算截断")
    logged = [json.loads(p.read_text(encoding="utf-8")) for p in tmp_log.glob("*.jsonl")]
    hit = [rec for rec in logged
           if raised is not None and rec["call_id"] == raised.response.call_id]
    check(len(hit) == 1 and hit[0]["completion"] == '{"proposals": [{"topic_id": "t1"'
          and hit[0]["prompt_version"] == "pv",
          "被截断的原始输出仍按 call_id 落盘（证据，不是正文）")

    with patch.object(LC, "get_client", return_value=_FakeClient(truncated)), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        plain = LC.chat_with_usage([{"role": "user", "content": "hi"}])
    check(plain.text.startswith('{"proposals"') and plain.finish_reason == "max_tokens",
          "缺省 reject_truncated=False 保持既有行为（不溢出到其它调用点）")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

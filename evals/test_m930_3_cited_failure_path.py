"""Eval: 真实 cited 链的**失败路径**——请求参数、响应读取、截断留痕、调用账本。

用法: python -m evals.test_m930_3_cited_failure_path

背景（2026-10-01 的 r1 真实运行）：公司节第一次写作调用返回
`finish_reason=max_tokens`、`output_tokens=8192`、**可见正文 0 字**，链在第一句就停住。
现有日志**不足以**唯一判定根因：它同时与「推理耗掉了输出额度」和「响应有正文但适配器没读到」
相符。本模块不假装已判定根因，而是把那两类**可复核**的东西钉住：

1. **请求参数**：本链两个真实客户端**显式**关推理；`llm.client` 的共享缺省**不动**
   （逐调用点控制，不是全局改默认）。判据是最终发往 `messages.create` 的 kwargs。
2. **响应读取**：`text` = **全部**可见 `text` 块按序相接（不是第一块）；thinking /
   未知块不进 `text`；缺 `content` 不炸。
3. **截断留痕**：截断那条路径的日志带响应**形态**读数（块类型/块数/各块长度/可见正文总长/
   `stop_reason`/usage），且**只有长度、没有内容**——隐藏推理正文与完整原始响应都不落盘。
4. **失败留账**：截断的调用既**抛**（停住整轮、不重试）也**记**（客户端 `calls` 里一条失败
   流水，含 `call_id`）；脚本在节循环中抛错时仍**原子**写出 `cited_call_ledger.json`，
   带失败原因与 call ID，且**不**写出任何章节成功产物。
5. **离线失败路径重放**：装上真正的 `cited-budget-2` 门，用假响应走一遍「零可见正文」的
   失败路径，读回「有可复核的响应形态，也有一条已占额的失败账」。

本模块不发任何模型请求、不联网、不写库、不碰 `logs/llm`（日志目录被指向临时目录）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm.client as LC                            # noqa: E402
from llm import budget as LB                       # noqa: E402
from scripts import run_m930_3_cited_chain as CHAIN  # noqa: E402
from sections import cited_budget as CB            # noqa: E402
from sections import cited_review as CR            # noqa: E402
from sections import cited_writer as CW            # noqa: E402

_MODEL = "cited-failure-path-model"


# ---------------------------------------------------------------------------
# 假 provider：块形状可控，且把最终 kwargs 留下来
# ---------------------------------------------------------------------------

class _Block:
    """一个内容块。`kind` 进 `type`，`body` 进该类型自己的载体字段。"""

    _FIELD = {"text": "text", "thinking": "thinking", "redacted_thinking": "data"}

    def __init__(self, kind: str, body: str = "") -> None:
        self.type = kind
        setattr(self, self._FIELD.get(kind, "text"), body)


class _UnknownBlock:
    """既没有 `type` 也没有任何已知载体字段的块：适配器不得因此炸掉。"""

    def __init__(self) -> None:
        self.opaque = "?"


class _Usage:
    def __init__(self, i, o):
        self.input_tokens = i
        self.output_tokens = o


class _Resp:
    def __init__(self, blocks, *, stop_reason, usage=(10, 8192), content=None):
        self.content = list(blocks) if content is None else content
        self.stop_reason = stop_reason
        self.usage = None if usage is None else _Usage(*usage)


class _Messages:
    def __init__(self, resp, sink):
        self._resp = resp
        self._sink = sink

    def create(self, **kwargs):
        self._sink.append(kwargs)
        return self._resp


class _FakeProvider:
    def __init__(self, resp):
        self.sent: list[dict] = []
        self.messages = _Messages(resp, self.sent)


def _truncated_thinking_only() -> _Resp:
    """r1 那种形状：推理块吃光额度，可见 `text` 块为空。"""
    return _Resp([_Block("thinking", "推" * 777), _Block("text", "")],
                 stop_reason="max_tokens")


#: 半截 JSON 的可见正文：长度在断言里从它**算出来**，不写字面数字（写死就会随夹具漂移）。
_PARTIAL_JSON = '{"subsections": ['


def _truncated_with_text() -> _Resp:
    return _Resp([_Block("text", _PARTIAL_JSON)], stop_reason="max_tokens",
                 usage=(20, 8192))


def main() -> dict:  # noqa: C901 - 逐条 check，线性读法
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    tmp_log = Path(tempfile.mkdtemp(prefix="eval_cited_failure_"))
    tmp_run = Path(tempfile.mkdtemp(prefix="eval_cited_run_"))

    # ---------------------------------------------------------------- §1 请求参数
    details.append("## §1 请求参数：本链显式关推理，共享缺省不动")
    check(CHAIN.CITED_THINKING_DISABLED == {"type": "disabled"},
          "本链的推理开关常量就是 `{'type': 'disabled'}`（不是别的形状）")
    details.append(
        "NOTE 只钉「本链传了什么」与「共享缺省是什么」，不钉「端点是否真的照做」——"
        "后者只能在真实调用里观测，本模块不发请求。")

    prose_provider = _FakeProvider(_Resp([_Block("text", "ok")], stop_reason="end_turn"))
    with patch.object(LC, "get_client", return_value=prose_provider), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        CW.LlmCitedProseClient(model=_MODEL,
                               thinking=CHAIN.CITED_THINKING_DISABLED).compose(
            messages=[{"role": "user", "content": "x"}], system="s",
            prompt_version=CW.CITED_WRITER_PROMPT_VERSION, model_policy="test")
    check(len(prose_provider.sent) == 1
          and prose_provider.sent[0].get("thinking") == {"type": "disabled"},
          f"写作客户端最终发往 `messages.create` 的 `thinking` 是关闭推理"
          f"（实测 {prose_provider.sent[0].get('thinking')!r}）")

    review_provider = _FakeProvider(_Resp([_Block("text", "ok")], stop_reason="end_turn"))
    with patch.object(LC, "get_client", return_value=review_provider), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        CR.LlmCitedReviewClient(model=_MODEL,
                                thinking=CHAIN.CITED_THINKING_DISABLED).review(
            messages=[{"role": "user", "content": "x"}], system="s",
            prompt_version=CR.CITED_REVIEW_PROMPT_VERSION, model_policy="test")
    check(len(review_provider.sent) == 1
          and review_provider.sent[0].get("thinking") == {"type": "disabled"},
          "审阅客户端同样显式关推理（两个客户端各钉一次，不是「一个对了就算」）")

    bare_provider = _FakeProvider(_Resp([_Block("text", "ok")], stop_reason="end_turn"))
    with patch.object(LC, "get_client", return_value=bare_provider), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        LC.chat_with_usage([{"role": "user", "content": "x"}])
    check("thinking" not in bare_provider.sent[0],
          "反例：共享客户端缺省**仍然**不传 `thinking`（本批没有改全局默认值）")

    unflagged_provider = _FakeProvider(_Resp([_Block("text", "ok")], stop_reason="end_turn"))
    with patch.object(LC, "get_client", return_value=unflagged_provider), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        CW.LlmCitedProseClient(model=_MODEL).compose(
            messages=[{"role": "user", "content": "x"}], system="s",
            prompt_version=CW.CITED_WRITER_PROMPT_VERSION, model_policy="test")
    check("thinking" not in unflagged_provider.sent[0],
          "反例：**不传**开关的 cited 客户端确实不传（这个开关由调用点给，不是藏在类缺省里）")

    # ---------------------------------------------------------------- §2 响应读取
    details.append("## §2 响应读取：全部可见 text 块按序相接")
    multi = _Resp([_Block("thinking", "想"), _Block("text", "前"), _Block("text", "后")],
                  stop_reason="end_turn")
    check(LC._visible_text(multi) == "前后",
          f"多个 `text` 块按顺序**全部**相接（实测 {LC._visible_text(multi)!r}）")
    only_thinking = _Resp([_Block("thinking", "想" * 10)], stop_reason="max_tokens")
    check(LC._visible_text(only_thinking) == "",
          "只有 thinking 块 → 可见正文为空串（不是报错，也不是把推理当正文）")
    with_unknown = _Resp([_Block("text", "甲"), _UnknownBlock(), _Block("text", "乙")],
                         stop_reason="end_turn")
    check(LC._visible_text(with_unknown) == "甲乙",
          "未知块既不进正文、也不打断其余 `text` 块的读取")
    check(LC._visible_text(_Resp([], stop_reason="end_turn", content=None)) == "",
          "响应缺 `content` → 空串（不是 AttributeError）")

    single = _Resp([_Block("text", "hello")], stop_reason="end_turn")
    with patch.object(LC, "get_client", return_value=_FakeProvider(single)), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        r_single = LC.chat_with_usage([{"role": "user", "content": "x"}])
    check(r_single.text == "hello" and r_single.finish_reason == "end_turn",
          "既有单块响应的行为逐字不变（不是「为了多块而改了单块」）")

    # ---------------------------------------------------------------- §3 截断留痕
    details.append("## §3 截断日志：带响应形态，且只有长度没有内容")
    hidden = "秘" * 777
    shape_provider = _FakeProvider(
        _Resp([_Block("thinking", hidden), _Block("text", "")], stop_reason="max_tokens",
              usage=(14967, 8192)))
    trunc_exc = None
    with patch.object(LC, "get_client", return_value=shape_provider), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        try:
            LC.chat_with_usage([{"role": "user", "content": "x"}], system="s",
                               max_tokens=8192, prompt_version="pv",
                               reject_truncated=True)
        except LC.LLMTruncatedResponse as exc:
            trunc_exc = exc
    check(trunc_exc is not None, "截断仍抛 `LLMTruncatedResponse`（半截 JSON 不当成功正文）")
    log_files = list(tmp_log.glob("*.jsonl"))
    hit = [json.loads(p.read_text(encoding="utf-8")) for p in log_files
           if trunc_exc is not None
           and p.name.endswith(f"__{trunc_exc.response.call_id}.jsonl")]
    check(len(hit) == 1, "被截断的那次调用按 call_id 落**恰好一条**日志")
    shape = hit[0].get("response_shape") if hit else None
    check(shape is not None and shape["block_kinds"] == ["thinking", "text"]
          and shape["block_count"] == 2 and shape["block_chars"] == [777, 0],
          f"形态读数记下块类型与各块长度（实测 {shape and shape.get('block_kinds')} / "
          f"{shape and shape.get('block_chars')}）")
    check(shape is not None and shape["visible_text_chars"] == 0
          and shape["text_block_count"] == 0 and shape["non_text_chars"] == 777,
          "「零可见正文」这件事在日志里**可读**（可见 0 / 非可见 777），不必靠猜")
    check(shape is not None and shape["stop_reason"] == "max_tokens"
          and shape["usage"] == {"input_tokens": 14967, "output_tokens": 8192},
          "形态读数带 `stop_reason` 与 usage（与记录自身的同名字段一致，不另算一套）")
    blob = json.dumps(hit, ensure_ascii=False)
    check(hidden not in blob and "秘" not in blob,
          "反例：隐藏推理正文**不落盘**（形态里只有长度；日志里搜不到那段字）")
    check(hit and "response_shape" not in json.dumps(
        [json.loads(p.read_text(encoding="utf-8")) for p in log_files
         if p.name.endswith(f"__{r_single.call_id}.jsonl")], ensure_ascii=False),
        "反例：**未截断**调用的日志不带 `response_shape`（这条读数只为截断那条路存在）")

    text_provider = _FakeProvider(_truncated_with_text())
    text_exc = None
    with patch.object(LC, "get_client", return_value=text_provider), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        try:
            LC.chat_with_usage([{"role": "user", "content": "x"}], max_tokens=8192,
                               prompt_version="pv", reject_truncated=True)
        except LC.LLMTruncatedResponse as exc:
            text_exc = exc
    hit2 = [json.loads(p.read_text(encoding="utf-8")) for p in tmp_log.glob("*.jsonl")
            if text_exc is not None
            and p.name.endswith(f"__{text_exc.response.call_id}.jsonl")]
    shape2 = hit2[0].get("response_shape") if hit2 else None
    check(shape2 is not None and shape2["visible_text_chars"] == len(_PARTIAL_JSON)
          and shape2["non_text_chars"] == 0,
          "反例：截断但**有**可见正文时，形态读数如实报出非零可见长度（不是恒 0 的摆设）")

    # ---------------------------------------------------------------- §4 失败留账
    details.append("## §4 失败留账：既抛也记，账本原子落盘且不含成功产物")
    client = CW.LlmCitedProseClient(model=_MODEL)
    fail_provider = _FakeProvider(_truncated_thinking_only())
    raised = None
    with patch.object(LC, "get_client", return_value=fail_provider), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        try:
            client.compose(messages=[{"role": "user", "content": "x"}], system="s",
                           prompt_version=CW.CITED_WRITER_PROMPT_VERSION,
                           model_policy="test")
        except LC.LLMTruncatedResponse as exc:
            raised = exc
    check(raised is not None, "写作客户端把截断**抛出去**（不返回残缺结果）")
    check(len(client.calls) == 1 and client.calls[0]["status"] == "error"
          and client.calls[0]["call_id"] == raised.response.call_id,
          f"同一次截断在客户端 `calls` 里留下失败流水并带 call_id"
          f"（实测 {client.calls[:1]}）")
    check("text" not in client.calls[0] and "response_hash" not in client.calls[0],
          "反例：失败流水里**没有** `text` / `response_hash`（半截内容不得升格成产物身份）")
    ok_client = CW.LlmCitedProseClient(model=_MODEL)
    with patch.object(LC, "get_client",
                      return_value=_FakeProvider(_Resp([_Block("text", "{}")],
                                                       stop_reason="end_turn"))), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        ok_client.compose(messages=[{"role": "user", "content": "x"}], system="s",
                          prompt_version=CW.CITED_WRITER_PROMPT_VERSION,
                          model_policy="test")
    check(len(ok_client.calls) == 1 and ok_client.calls[0]["status"] == "ok",
          "反例：正常返回只留一条 `ok` 流水（失败流水不是「每次调用都写一条」）")

    review_client = CR.LlmCitedReviewClient(model=_MODEL)
    with patch.object(LC, "get_client",
                      return_value=_FakeProvider(_truncated_thinking_only())), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        try:
            review_client.review(messages=[{"role": "user", "content": "x"}], system="s",
                                 prompt_version=CR.CITED_REVIEW_PROMPT_VERSION,
                                 model_policy="test")
        except LC.LLMTruncatedResponse:
            pass
    check(len(review_client.calls) == 1
          and review_client.calls[0]["status"] == "error",
          "审阅客户端同样既抛也记（两个客户端各钉一次）")

    # ---------------------------------------------------------------- §5 离线重放
    details.append("## §5 离线失败路径重放：真预算门 + 假响应 → 零可见正文 + 失败账本")
    gate = CB.cited_call_budget_gate(approved_model=_MODEL)
    replay_provider = _FakeProvider(_truncated_thinking_only())
    replay_client = CW.LlmCitedProseClient(model=_MODEL,
                                           thinking=CHAIN.CITED_THINKING_DISABLED)
    replay_exc = None
    with patch.object(LC, "get_client", return_value=replay_provider), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        with CHAIN._cited_call_scope(gate, section_id="company"):
            try:
                replay_client.compose(messages=[{"role": "user", "content": "x"}],
                                      system="s",
                                      prompt_version=CW.CITED_WRITER_PROMPT_VERSION,
                                      model_policy="test")
            except LC.LLMTruncatedResponse as exc:
                replay_exc = exc
    check(replay_exc is not None and LB.installed() is None,
          "重放结束：截断抛出，且 `_cited_call_scope` 已把预算门还原（不污染相邻的节）")
    summary = gate.summary()
    check(summary["attempt_total"] == 1 and summary["attempts_by_status"].get("error") == 1,
          f"重放里那次调用在账上**已占额**（实测 {summary['attempts_by_status']}）")
    attempt = summary["attempts"][0] if summary["attempts"] else {}
    check(attempt.get("call_id") == replay_exc.response.call_id
          and "truncated" in str(attempt.get("error")),
          "账上那一条带的是**同一个** call_id，且错误写明是截断（不是一句泛化失败）")

    ledger_summary = CHAIN._write_cited_call_ledger(
        run_dir=tmp_run, cited_gate=gate, approved_model=_MODEL,
        failure=CHAIN._failure_record(replay_exc, section_id="company"))
    ledger_path = tmp_run / "cited_call_ledger.json"
    check(ledger_path.exists(), "失败路径上 `cited_call_ledger.json` **确实落盘**了")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    check(ledger.get("run_outcome") == "failed"
          and ledger["failure"]["error_type"] == "LLMTruncatedResponse"
          and ledger["failure"]["call_id"] == replay_exc.response.call_id
          and ledger["failure"]["section_id"] == "company",
          f"账本写明失败原因、节与那次调用的 call_id（实测 {ledger.get('failure')}）")
    check(ledger.get("mode") == "real"
          and ledger["call_budget"]["attempt_total"] == 1,
          "失败账本里带着**已占用的调用次数**（不是一份空账）")
    check(sorted(p.name for p in tmp_run.iterdir()) == ["cited_call_ledger.json"],
          f"失败路径**只**产出账本，没有任何章节成功产物（实测 "
          f"{sorted(p.name for p in tmp_run.iterdir())}）")
    check(ledger_summary is not None and ledger_summary["attempt_total"] == 1,
          "落账函数把可打印的账本摘要回给调用方（失败时也要能在终端读回）")

    # 反例：成功路径的账本标 completed，且失败一栏不出现
    ok_gate = CB.cited_call_budget_gate(approved_model=_MODEL)
    ok_dir = Path(tempfile.mkdtemp(prefix="eval_cited_run_ok_"))
    CHAIN._write_cited_call_ledger(run_dir=ok_dir, cited_gate=ok_gate,
                                   approved_model=_MODEL, failure=None)
    ok_ledger = json.loads((ok_dir / "cited_call_ledger.json").read_text(encoding="utf-8"))
    check(ok_ledger["run_outcome"] == "completed" and "failure" not in ok_ledger,
          "反例：成功路径的账本标 `completed`，且**没有** `failure` 一栏")
    off_dir = Path(tempfile.mkdtemp(prefix="eval_cited_run_off_"))
    CHAIN._write_cited_call_ledger(run_dir=off_dir, cited_gate=None,
                                   approved_model=None, failure=None)
    off_ledger = json.loads((off_dir / "cited_call_ledger.json").read_text(encoding="utf-8"))
    check(off_ledger["mode"] == "offline" and off_ledger["ledger"] is None
          and off_ledger["run_outcome"] == "completed",
          "反例：离线模式仍写明「没有真实调用可记」，同时把本轮结局与它分开标")

    # ------------------------------------------------ §5b 失败记录里的可回查审阅身份
    details.append("## §5b `CitedReviewIncomplete` 必须带上可回查的审阅 call_id")
    # 成因有两种，账本形状不同：调用**失败**（行 `status="error"`）与调用成功但**回复不合约**
    # （行 `status="ok"`——cp-14 的 `review_reply_unparsable` 正是后者）。两种情况下那次调用的
    # `call_id` 都在账本上，失败记录必须把它抄进来，读者才能只凭运行目录回到
    # `logs/llm/<时间戳>__<call_id>.jsonl` 读原始可见回复。
    review_gate = CB.cited_call_budget_gate(approved_model=_MODEL)
    ok_provider = _FakeProvider(_Resp([_Block("text", '{"issues": []}')],
                                      stop_reason="end_turn", usage=(10, 20)))
    review_client = CR.LlmCitedReviewClient(model=_MODEL,
                                            thinking=CHAIN.CITED_THINKING_DISABLED)
    with patch.object(LC, "get_client", return_value=ok_provider), \
         patch.object(LC, "LOGS_DIR", tmp_log):
        with CHAIN._cited_call_scope(review_gate, section_id="company"):
            reviewed = review_client.review(messages=[{"role": "user", "content": "x"}],
                                            system="s",
                                            prompt_version=CR.CITED_REVIEW_PROMPT_VERSION,
                                            model_policy="test")
    check(reviewed.status == "ok" and bool(reviewed.call_id),
          f"夹具前置：这次审阅**调用本身**成功并留下 call_id（实测 status="
          f"{reviewed.status!r}，call_id={reviewed.call_id!r}）")
    rec = CHAIN._incomplete_review_failure(
        incomplete=[("company", "review_reply_unparsable")], cited_gate=review_gate)
    check(rec["call_id"] == reviewed.call_id,
          f"正例：失败记录带的就是那次审阅调用的 call_id（实测 {rec['call_id']!r}）")
    check(rec["error_type"] == "CitedReviewIncomplete"
          and "review_reply_unparsable" in rec["error"]
          and rec["section_id"] == "company",
          f"同一条记录仍写明 typed 原因码与节（实测 {rec['error_type']!r}/"
          f"{rec['section_id']!r}）")
    # 反例 1：账本里**没有**该节的审阅尝试时留空串——不编一个 ID 冒充「可回查」。
    other = CHAIN._incomplete_review_failure(
        incomplete=[("financial", "review_reply_unparsable")], cited_gate=review_gate)
    check(other["call_id"] == "",
          f"反例：该节没有审阅尝试时不得编 call_id（实测 {other['call_id']!r}）")
    # 反例 2：离线替身没有预算门，同样留空串；没有没跑完的节时整条记录不出现。
    check(CHAIN._incomplete_review_failure(
        incomplete=[("company", "review_reply_unparsable")], cited_gate=None)["call_id"] == "",
          "反例：没有真实调用账本（离线替身）时留空串")
    check(CHAIN._incomplete_review_failure(incomplete=[], cited_gate=review_gate) is None,
          "反例：没有没跑完的节时整条失败记录不出现（不写一条空失败）")

    details.append(
        "NOTE 本模块证明的是**失败路径本身**（参数、读数、留痕、留账）；它**不**证明 r1 的"
        "根因是哪一种，也不证明「关掉推理就能写完 18 个小节」——那要等下一次获批的真实调用。")
    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=1, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

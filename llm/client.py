"""Anthropic API 封装，通过 DeepSeek 兼容接口调用。

所有 LLM 调用必须经过此模块，不得在 agent 内部直接创建 client。
每次 LLM 调用自动落盘到 logs/llm/（含 token / latency / call_id / finish_reason /
prompt_version），供 Phase 3 Batch B Harness 的可观测性要求使用。

接口契约（向后兼容）：
- `chat(...) -> str` 保持不变，内部委托 `chat_with_usage(...).text`；
- `chat_with_usage(...) -> LLMResponse` 返回带 usage / latency / call_id 的结构化结果；
- usage 缺失（provider 未返回）记 None，绝不记 0。

CLI:
  python -m llm.client --self-check
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, LLM_MODEL
from llm import budget

logger = logging.getLogger(__name__)

_client: Anthropic | None = None
LOGS_DIR = Path("logs/llm")

#: provider 表示「输出被输出长度上限截断」的 `stop_reason` 取值。Anthropic 风格是 `length`，
#: 本项目的 DeepSeek 兼容端点实测返回 `max_tokens`（`logs/llm` 历史里有 17 条）。两者都算截断：
#: 残缺的正文/JSON **不是**一次「短一点的合格输出」。
TRUNCATION_STOP_REASONS = ("length", "max_tokens")


@dataclass(frozen=True)
class LLMResponse:
    """一次 LLM 调用的结构化结果（usage 兼容 + 审计元数据）。"""

    text: str
    input_tokens: int | None       # provider 未返回 usage → None，不记 0
    output_tokens: int | None
    latency_ms: int
    model: str
    call_id: str
    finish_reason: str | None


class LLMTruncatedResponse(RuntimeError):
    """provider 明确表示输出被截断（`reject_truncated=True` 时**替代**返回值抛出）。

    这是一次**失败的调用**，不是「一次短一点的输出」：残缺正文/JSON 不得当成合格内容，因此
    本异常携带那次响应（`call_id` / `finish_reason` / usage）抛给调用方，由调用方**如实停止**
    ——不自动重跑、不换模型、不换 prompt（重试属于另一份批准），也不要试图修补残缺 JSON。
    """

    def __init__(self, response: LLMResponse) -> None:
        self.response = response
        super().__init__(
            f"provider 截断输出：finish_reason={response.finish_reason!r}"
            f"（output_tokens={response.output_tokens}，call_id={response.call_id}）："
            "残缺内容不得当成合格正文；本门不自动重跑")


def get_client() -> Anthropic:
    """返回已配置的 Anthropic client（指向 DeepSeek base_url）。

    `max_retries=0`：SDK 缺省是 2 次**内部静默重试**，而预算账本按**一次调用**记一条。留着
    它，「失败与重试也计入」就不成立——一次记账可能对应三次真实 HTTP 请求，账本会系统性
    少记。重试改由调用方在账本内显式进行：每次重试都是一次新的 `reserve_attempt` 与一条
    新记录，于是「发了几次」与「记了几条」始终相等。
    """
    global _client
    if _client is None:
        if not DEEPSEEK_API_KEY:
            raise RuntimeError("DEEPSEEK_API_KEY not configured. Check your .env file.")
        _client = Anthropic(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL,
                            max_retries=0)
    return _client


def _new_call_id() -> str:
    return uuid.uuid4().hex


def _block_chars(block: Any) -> int:
    """一个内容块的可读字符数（**只数长度，不取内容**）。

    按块类型取各自的载体字段：`text` 块是 `text`、`thinking` 块是 `thinking`、
    `redacted_thinking` 是 `data`。未知块返回 0 而不是抛错——它正是要被如实记下来的那种块。
    """
    for attr in ("text", "thinking", "data"):
        value = getattr(block, attr, None)
        if isinstance(value, str):
            return len(value)
    return 0


def _block_kind(block: Any) -> str:
    """块的类型名。优先取 provider 的 `type`；没有就退回类名（不猜、不归一化）。"""
    kind = getattr(block, "type", None)
    if isinstance(kind, str) and kind:
        return kind
    return type(block).__name__


def _visible_text(resp: Any) -> str:
    """响应里**全部**可见 `text` 块，按顺序相接。

    只取第一个（或只取最后一个）`text` 块都会丢正文，而丢掉的正文**不会让下游报错**：
    它只会让一份本该完整的输出看上去「本来就这么短」。多块响应在长 JSON 上真实出现过，
    因此这里既不挑块、也不做「哪块才是正文」的判断——非空 `text` 块依次相接即是全部。
    """
    parts: list[str] = []
    for block in getattr(resp, "content", None) or []:
        value = getattr(block, "text", None)
        if isinstance(value, str) and value:
            parts.append(value)
    return "".join(parts)


def _response_shape(resp: Any) -> dict:
    """响应的**形态**读数：块类型、块数、各块长度、可见 text 总长、`stop_reason`、usage。

    只记**长度**，不记内容：隐藏推理正文与完整原始响应都不落盘。这条读数只为一件事存在
    ——「输出被截断而可见正文为空」事后可复核。没有它，日志里只剩一个空串，读的人无法
    区分「模型没写出东西」与「写出来了但适配器没读到」。
    """
    blocks = list(getattr(resp, "content", None) or [])
    text_chars = sum(_block_chars(b) for b in blocks
                     if isinstance(getattr(b, "text", None), str))
    return {
        "block_count": len(blocks),
        "block_kinds": [_block_kind(b) for b in blocks],
        "block_chars": [_block_chars(b) for b in blocks],
        "text_block_count": len([b for b in blocks
                                 if isinstance(getattr(b, "text", None), str)
                                 and getattr(b, "text")]),
        "visible_text_chars": len(_visible_text(resp)),
        "non_text_chars": sum(_block_chars(b) for b in blocks) - text_chars,
        "stop_reason": getattr(resp, "stop_reason", None),
        "usage": {"input_tokens": getattr(getattr(resp, "usage", None),
                                         "input_tokens", None),
                  "output_tokens": getattr(getattr(resp, "usage", None),
                                           "output_tokens", None)},
    }


def chat_with_usage(
    messages: list[dict],
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
    prompt_version: str | None = None,
    thinking: dict | None = None,
    reject_truncated: bool = False,
) -> LLMResponse:
    """单轮对话，返回结构化 LLMResponse（含 usage/latency/call_id）。自动落盘日志。

    thinking: 传给 provider 的推理开关（Anthropic 风格 {"type": "disabled"} 或
    {"type": "enabled", "budget_tokens": N}）。None = 不传、由 provider 默认。
    DeepSeek-V4-Pro 为推理模型，推理内容计入 output_tokens；结构化 JSON 任务应显式
    `{"type": "disabled"}` 以免推理耗尽额度导致正文为空。

    响应读数：`text` = 响应里**全部**可见 `text` 块按序相接（不是「第一块」），
    thinking / redacted_thinking / 未知块一概不进 `text`；截断路径另落一份块**形态**
    （块类型、块数、各块长度、可见正文总长、stop_reason、usage——只有长度，没有内容）。

    reject_truncated: 缺省 False = 既有行为逐字节不变（其它管线自行处置短输出，例如研究相位
    的「截断且正文为空 → ACTION_SCHEMA_INVALID」）。置 True 时，`stop_reason` 落在
    `TRUNCATION_STOP_REASONS` 的响应**不算成功**：账本按 `error` 落账（失败照样占一次），
    那次响应仍写进 `logs/llm`（截断的原始输出是证据，不是正文），然后抛
    `LLMTruncatedResponse` —— 调用方如实停止，不自动重跑。
    """
    client = get_client()
    model_name = model or LLM_MODEL
    call_id = _new_call_id()

    # 事前预算门（`llm.budget`）：未安装时返回 None，既有行为逐字节不变。安装后，第 N+1 次
    # 尝试在**这里**抛出——请求不会到达 provider，且失败的尝试同样已经占了一次。
    ticket = budget.reserve_attempt(call_id=call_id, prompt_version=prompt_version,
                                    model=model_name, max_tokens=max_tokens)
    t0 = time.perf_counter()

    system_params = [{"type": "text", "text": system}] if system else None

    create_kwargs: dict = {
        "model": model_name,
        "max_tokens": max_tokens,
        "system": system_params,
        "messages": messages,
    }
    if thinking is not None:
        create_kwargs["thinking"] = thinking

    try:
        resp = client.messages.create(**create_kwargs)
    except BaseException as exc:  # noqa: BLE001 — 失败的尝试照样占一次，账要落
        budget.settle_attempt(ticket, status=budget.STATUS_ERROR,
                              error=f"{type(exc).__name__}: {exc}")
        raise

    elapsed_ms = int((time.perf_counter() - t0) * 1000)

    # DeepSeek 可能返回 ThinkingBlock，只取 TextBlock——**全部**的 TextBlock，按顺序相接。
    completion = _visible_text(resp)

    # usage 可能缺失（provider 未返回），保守记 None，不记 0。
    usage = getattr(resp, "usage", None)
    input_tokens = usage.input_tokens if usage is not None else None
    output_tokens = usage.output_tokens if usage is not None else None
    finish_reason = getattr(resp, "stop_reason", None)

    result = LLMResponse(
        text=completion,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=elapsed_ms,
        model=model_name,
        call_id=call_id,
        finish_reason=finish_reason,
    )

    if reject_truncated and str(finish_reason) in TRUNCATION_STOP_REASONS:
        # 截断 = 一次**失败的调用**（不是「短一点的输出」）：账本按 error 落账——它照样占一次
        # 尝试；原始输出仍然写进 `logs/llm`（截断的字节是证据，不是正文）；然后抛出，由调用方
        # 如实停止。这里不修补残缺 JSON、不重跑、不换模型/换 prompt。
        # 截断这条路径**额外**落一份响应**形态**读数（块类型/块数/块长度/可见正文总长/
        # stop_reason/usage）：它是这条路径上唯一能被事后复核的东西。成功路径不落它——
        # 「截断了没」是这条路径独有的问题，把形态塞进每一次成功调用只会稀释日志。
        budget.settle_attempt(ticket, status=budget.STATUS_ERROR,
                              error=f"truncated: finish_reason={finish_reason!r}")
        _log_llm_call(result, messages, system, prompt_version, thinking,
                      response_shape=_response_shape(resp))
        logger.warning("LLM call truncated: model=%s output=%s finish_reason=%s call_id=%s",
                       model_name, output_tokens, finish_reason, call_id)
        raise LLMTruncatedResponse(result)

    budget.settle_attempt(ticket, status=budget.STATUS_OK)
    _log_llm_call(result, messages, system, prompt_version, thinking)

    logger.info("LLM call: model=%s input=%s output=%s latency=%dms call_id=%s",
                model_name, input_tokens, output_tokens, elapsed_ms, call_id)

    return result


def chat(
    messages: list[dict],
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
) -> str:
    """单轮对话，返回 LLM 回复文本。自动落盘日志到 logs/llm/（向后兼容入口）。"""
    return chat_with_usage(messages, system=system, model=model,
                           max_tokens=max_tokens).text


def chat_stream(
    messages: list[dict],
    system: str | None = None,
    model: str | None = None,
    max_tokens: int = 4096,
    prompt_version: str | None = None,
):
    """流式对话，yield text delta。

    **不留旁路**：装了预算门时，本入口与 `chat_with_usage` 一样先归属、先记账、再发请求。
    这一点在**调用时**生效（不是等第一次 `next()`），否则「安装了门但流式请求照样出去」会是
    一条只在真正取第一个 token 时才暴露的缺口。今天全仓无调用点，因此 `prompt_version`
    缺省为 `None`：装了门时它会因「未登记归属」被拒——这正是要的（要用流式就显式登记）。
    """
    client = get_client()
    model_name = model or LLM_MODEL
    call_id = _new_call_id()
    ticket = budget.reserve_attempt(call_id=call_id, prompt_version=prompt_version,
                                    model=model_name, max_tokens=max_tokens)
    return _stream_text(client, model_name, system, messages, max_tokens, ticket)


def _stream_text(client, model_name: str, system: str | None, messages: list[dict],
                 max_tokens: int, ticket):
    """流式传输本体（生成器）。记账口径与 `chat_with_usage` 一致：异常落 `error`。"""
    system_params = [{"type": "text", "text": system}] if system else None
    try:
        with client.messages.stream(
            model=model_name,
            max_tokens=max_tokens,
            system=system_params,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text
    except GeneratorExit:
        # 消费者提前关闭：这次尝试确实发生过，但结局未知 ⇒ 保留 `reserved`，
        # 既不谎报 ok 也不谎报 error（账本里 `reserved` 就是「发了、结局未落」）。
        raise
    except BaseException as exc:  # noqa: BLE001 — 失败的尝试照样占一次，账要落
        budget.settle_attempt(ticket, status=budget.STATUS_ERROR,
                              error=f"{type(exc).__name__}: {exc}")
        raise
    else:
        budget.settle_attempt(ticket, status=budget.STATUS_OK)


def load_prompt(name: str) -> str:
    """从 llm/prompts/<name>.txt 加载 prompt 模板。"""
    prompt_path = Path(__file__).resolve().parent / "prompts" / f"{name}.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


def _log_llm_call(
    result: LLMResponse,
    messages: list[dict],
    system: str | None,
    prompt_version: str | None,
    thinking: dict | None = None,
    response_shape: dict | None = None,
) -> None:
    """落盘 LLM 调用日志到 logs/llm/（微秒时间戳 + call_id 唯一文件名，防同秒覆盖）。

    `response_shape` 缺省 `None` = 不写这一栏，因此既有调用点的日志记录**逐字段不变**；
    只有截断那条路径会带上它（见 `_response_shape`：只有长度，没有内容）。
    """
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        record = {
            "timestamp": ts,
            "call_id": result.call_id,
            "model": result.model,
            "prompt_version": prompt_version,
            "thinking": thinking,
            "finish_reason": result.finish_reason,
            "system": system,
            "messages": messages,
            "completion": result.text,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "latency_ms": result.latency_ms,
            "elapsed_s": round(result.latency_ms / 1000.0, 3),
        }
        if response_shape is not None:
            record["response_shape"] = response_shape
        filepath = LOGS_DIR / f"{ts}__{result.call_id}.jsonl"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.warning("Failed to write LLM log", exc_info=True)


def _main(argv: list[str]) -> int:
    import argparse
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        prog="python -m llm.client", description="LLM 调用层自检")
    parser.add_argument("--self-check", action="store_true",
                        help="打印模块自检摘要（不发起真实调用）")
    args = parser.parse_args(argv)

    if args.self_check:
        summary = {
            "model": LLM_MODEL,
            "api_key_configured": bool(DEEPSEEK_API_KEY),
            "LLMResponse_fields": list(LLMResponse.__dataclass_fields__),
            "log_dir": str(LOGS_DIR),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    sys.exit(_main(sys.argv[1:]))

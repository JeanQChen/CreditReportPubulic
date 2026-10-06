"""权威 payload 信封的**文本**读视图（唯一实现）。

材料/事实的原文只能来自**已验字节**（`ResolvedPayload.payload_bytes`）解出的信封
`content.text`，不得来自索引自报字段、文件名、章节标题或任何旁路。本模块只做这一件事，
供研究侧（期间抽取）与信用侧（事实抽取）共用；`harness/credit_authority._envelope_text`
委托到这里，避免出现第二套信封解析。

版本：`PAYLOAD_TEXT_VERSION` 进入任何下游派生身份（期间抽取结果等）时须逐字回指。
"""

from __future__ import annotations

import json
from typing import Any

#: 信封读视图版本。解析口径（`content.text`）变化时必须改这里。
PAYLOAD_TEXT_VERSION = "payload-text-1"


def envelope_of(payload_bytes: bytes | str | None) -> dict | None:
    """解出信封 dict；缺字节 / 非法 UTF-8 / 非 JSON / 非对象 → 一律 ``None``（fail-closed）。

    调用方**不得**把 ``None`` 当作「空文本」继续往下走：那会把「读不到原文」静默降级成
    「原文是空的」。
    """
    if payload_bytes is None:
        return None
    if isinstance(payload_bytes, str):
        payload_bytes = payload_bytes.encode("utf-8")
    if not payload_bytes:
        return None
    try:
        env = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return env if isinstance(env, dict) else None


def envelope_text(payload_bytes: bytes | str | None) -> str:
    """信封 ``content.text``（缺字节 / 非法信封 / 缺字段 → ``""``）。"""
    env = envelope_of(payload_bytes)
    if env is None:
        return ""
    content = env.get("content") or {}
    if not isinstance(content, dict):
        return ""
    return str(content.get("text", "") or "")


def resolved_payload_text(resolved: Any) -> str:
    """已解析 payload 对象 → 文本。对象没有 ``payload_bytes`` 时返回 ``""``。"""
    return envelope_text(getattr(resolved, "payload_bytes", None))

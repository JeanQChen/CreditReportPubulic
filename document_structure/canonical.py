"""`document_structure` 的**最低层**公共原语：确定性规范形 + fail-closed 异常。

本模块刻意**不 import 任何项目内模块**（只用标准库），因此 `document_structure`
位于目标链最底层，不会反向依赖 `harness`（Topic runtime）或其它上层包。

TS1 修正轮（P1-1 与架构检查 §三.2）：上一轮 `schema.py` 从 `harness.topic_schema`
借用 `SchemaValidationError` / `canonical_json` / `sha256_canonical`。那会让
**基础结构层依赖 Topic runtime 的 schema 模块**；TS7B 把树结构接进 harness 后，
`harness.topic_schema → document_structure → harness.topic_schema` 会成为真实循环导入。
因此这三个原语改为在本模块内提供**最小实现**，语义与原实现一致
（`sort_keys=True`、`ensure_ascii=False`、`separators=(",", ":")`），
并有测试证明与 `harness.topic_schema` 的对应函数逐字节等价。

除此之外本模块只提供两件与身份有关的纯函数：

- `quantize` 之外的 `identity` / `locator`：把"稳定定位身份"与"不可变 revision 身份"
  的字符串形状固定为 `loc-<kind>-<hex16>` / `<kind>-<hex16>`，避免各类型各写一套。
- `canonical_text`：空白折叠归一（仅用于**身份**计算，不改变对象本体字段）。

TS1.1 收口轮（P2）：规范形**禁止 NaN / ±Inf**。上一轮 `to_json_value` 直接把
`float` 原样返回，`canonical_json` 也未显式 `allow_nan=False`，于是
`canonical_json({"x": float("nan")})` 会产出 `{"x":NaN}` 这种非标准 JSON。
现在两道闸都在本层：`to_json_value` 在**任何嵌套层级**（dict / list / tuple /
set / frozenset / 嵌套 dataclass 的 `to_dict`）立即抛 `SchemaValidationError`；
`canonical_json` 再显式 `allow_nan=False`。上层各字段的有限性检查仍然保留，
但规范形不再**依赖**它们。
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------

class SchemaValidationError(ValueError):
    """结构层 schema 反序列化 / 构造 fail-closed。

    与 `harness.topic_schema.SchemaValidationError` **不是**同一个类（刻意如此：
    结构层不得依赖 Topic runtime 层）。两者都是 `ValueError` 子类，因此捕获
    `ValueError` 的既有调用方仍然安全。
    """


# ---------------------------------------------------------------------------
# 确定性规范形
# ---------------------------------------------------------------------------

def to_json_value(v: Any) -> Any:
    """对象图 → JSON 安全 primitive（tuple→list；嵌套 frozen 对象→其 to_dict）。

    **任何嵌套层级**出现 NaN / ±Inf 都在此立即 fail-closed：`json.dumps` 默认会把
    它们写成 `NaN` / `Infinity`，那是非标准 JSON，会让规范形不可被标准解析器读回、
    也会让身份在不同运行时上不一致。这里不做"跳过或置零"，而是直接抛
    `SchemaValidationError`——静默改写会掩盖上游的数值缺陷。
    """
    if isinstance(v, bool) or v is None or isinstance(v, (str, int)):
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            raise SchemaValidationError(
                f"规范形不得包含 NaN/±Inf（必须为有限实数）：{v!r}")
        return v
    if isinstance(v, (tuple, list)):
        return [to_json_value(x) for x in v]
    if isinstance(v, (frozenset, set)):
        return sorted((to_json_value(x) for x in v), key=str)
    if isinstance(v, dict):
        return {k: to_json_value(x) for k, x in v.items()}
    if hasattr(v, "to_dict"):
        # 必须继续递归：嵌套对象自己的 to_dict 里同样可能藏着 NaN/±Inf。
        return to_json_value(v.to_dict())
    raise TypeError(f"不可序列化类型: {type(v).__name__}")


def canonical_json(obj: Any) -> str:
    """确定性 JSON 字符串（sort_keys、ensure_ascii=False、无多余空白）。

    与字典插入顺序无关；这是"相同输入必然得到相同身份"的前提。

    `allow_nan=False` 是第二道闸：`to_json_value` 已逐层拒绝 NaN/±Inf，
    这里再显式禁止一次，保证即使将来有人绕过 `to_json_value`，
    `canonical_json` 也**绝不**产出非标准 JSON。
    """
    payload = to_json_value(obj)
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"), allow_nan=False)
    except ValueError as e:  # pragma: no cover - 防御性：正常路径已在上面拦下
        raise SchemaValidationError(
            f"规范形不得包含 NaN/±Inf（必须为有限实数）：{e}") from e


def sha256_canonical(obj: Any) -> str:
    """对对象图求稳定 sha256（64 位小写十六进制）。"""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 身份字符串形状
# ---------------------------------------------------------------------------

# 稳定定位身份前缀（`loc-<kind>-<hex16>`）与不可变 revision 身份前缀
# （`<kind>-<hex16>`）刻意不同，避免调用方把二者混用。
LOCATOR_PREFIX = "loc"
_ID_HEX_LEN = 16


def identity(kind: str, payload: dict) -> str:
    """不可变 revision / content identity：`<kind>-<sha256(canonical_json)[:16]>`。

    payload 必须覆盖：schema version、builder/algorithm/rule/normalization version、
    上游对象身份与来源版本、以及**全部会改变对象业务含义的规范化内容**。
    因此"同一 id 对应两种规范形"在构造期就会被 `__post_init__` 的重算比对拦下。
    """
    return f"{kind}-{sha256_canonical(payload)[:_ID_HEX_LEN]}"


def locator(kind: str, payload: dict) -> str:
    """稳定定位身份：`loc-<kind>-<sha256(canonical_json)[:16]>`。

    只由"对象是谁 / 在哪"决定，**不含**会随内容变化的字段，因此内容修订后
    locator 不变、`*_id` 必变；Store / 引用 / current-stale / 依赖指纹一律使用
    `*_id`，locator 只用于跨修订定位。
    """
    return f"{LOCATOR_PREFIX}-{kind}-{sha256_canonical(payload)[:_ID_HEX_LEN]}"


# ---------------------------------------------------------------------------
# 归一与数值
# ---------------------------------------------------------------------------

def canonical_text(text: str) -> str:
    """空白折叠归一（仅用于身份计算）：`" ".join(text.split())`。

    与 `evidence/ids.py:47-49 _normalize_text` 同语义。用于保证"只有空白规范化差异"
    时内容身份保持稳定；**不**用于改写对象本体字段。
    """
    return " ".join(text.split())


def is_finite(value: Any) -> bool:
    """有限实数判定：`bool` 不算数值；NaN / ±Inf 一律 False。"""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


__all__ = [
    "SchemaValidationError",
    "canonical_json",
    "canonical_text",
    "identity",
    "is_finite",
    "locator",
    "sha256_canonical",
    "to_json_value",
]

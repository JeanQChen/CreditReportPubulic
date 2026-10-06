"""`M930-3` 写作调用**失败前留存**（`ccj-1`）：一次真实调用的输入、可见回复与解析状态。

存在的理由是一次具体的丢失：真实 r2（call_id `5856a5b820a14b2ca50bd3e31924264d`）在
`logs/llm` 里留下了完整回复，但**运行目录里什么都没有**——没有输入清单身份、没有可见回复、
没有解析状态。于是那一轮的正规化结果只能叫**离线派生诊断**，不能倒写成正式通过：连「这次送给
模型的是哪一份清单」都无法证明，更谈不上拿它去顶替。

三件事按**发生顺序**落盘，每次落盘都是整体原子替换（读到的一定是某一次完整的快照）：

1. :meth:`CitedCallJournal.record_input` —— **发请求之前**：输入清单身份（`manifest_id` +
   指纹）、逐条材料/事实引用键、逐小节 `declared_aspect_id`，以及**请求面原文**。
   请求面原文是「精确输入」本身，不是它的摘要：靠摘要无法在事后回答「模型看到的是这一段吗」。
2. :meth:`CitedCallJournal.record_reply` —— 收到回复之后、**解析之前**：可见正文全文、
   `call_id`、`finish_reason`、usage、延迟、状态。**失败也留**。
3. :meth:`CitedCallJournal.record_parse` —— 解析结果：成功记草稿身份与句/小节数；失败记
   异常类型、原因码与消息。

三条边界：

* **不保存隐藏推理内容。** `reply.text` 只可能是 `llm.client` 读出的**可见文本块**；
  `hidden_reasoning_persisted` 恒为 `false`，这是一条**可断言**的声明，不是措辞。
* **不可发布。** `publishable` 恒为 `false`：这份文件里的一切都还没有经过硬核对与独立审阅。
* **留存写失败不得顶掉原异常。** 本类的 `record_*` 从不抛出：写失败记进 `write_errors`
  （能在盘上写就一并写进去）。这一点是硬的——留存是**证据**，证据写不进去是证据的问题，
  不能因此把「这一轮为什么失败」换成「写文件失败了」。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

#: 留存文件版本。
CITED_CALL_JOURNAL_VERSION = "ccj-1"

#: 留存文件名（固定：读回侧按名字找，不靠通配）。
CITED_CALL_JOURNAL_FILENAME = "cited_call_journal.json"

#: 请求面原文在文件里最多留这么多字符。真实 r2 的请求面约 5 万字符量级，上限留足余量；
#: 超限时**如实记** `request_face_truncated=true` 与两侧哈希，而不是悄悄截断。
CITED_JOURNAL_REQUEST_FACE_LIMIT = 400_000


def _sha256(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    """把任意对象压成 JSON-safe：认得出 `to_dict` 就用它，其余按基础类型落。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    return str(value)


class CitedCallJournal:
    """一次写作调用的留存簿。**不是**预算账本（那是 `sections.cited_budget`）。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.write_errors: list[str] = []
        self._state: dict = {
            "journal_version": CITED_CALL_JOURNAL_VERSION,
            "publishable": False,
            "hidden_reasoning_persisted": False,
            "note": ("真实模型调用前后的留存。**未经硬核对、独立审阅，不可发布**：这里的一切都还"
                     "不是成品正文，只是「这次调用到底发生了什么」的可回查证据。可见回复在解析"
                     "之前落盘，因此解析失败也留得下来；不保存任何隐藏推理内容。"),
            "input": None,
            "reply": None,
            "parse": None,
            "write_errors": [],
        }

    # -- 落盘 ---------------------------------------------------------------

    def _flush(self) -> None:
        """整体原子替换。写失败只记不抛（见模块头最后一条边界）。"""
        try:
            body = json.dumps(self._state, ensure_ascii=False, indent=1, sort_keys=True)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(self.path)
        except Exception as exc:  # noqa: BLE001 - 留存写失败不得顶掉原异常
            message = f"{type(exc).__name__}: {exc}"
            self.write_errors.append(message)
            self._state["write_errors"] = list(self.write_errors)

    # -- 三个记录点 ---------------------------------------------------------

    def record_input(self, *, section_id: str, manifest: Any, prompt_version: str,
                     model_policy: str, model: str = "", thinking: Any = None,
                     request_face: str = "", system_text: str = "") -> None:
        """**发请求之前**：把这次要送出去的输入钉死。"""
        materials = list(getattr(manifest, "materials", ()) or ())
        facts = list(getattr(manifest, "facts", ()) or ())
        face = str(request_face or "")
        truncated = len(face) > CITED_JOURNAL_REQUEST_FACE_LIMIT
        self._state["input"] = {
            "section_id": str(section_id or ""),
            "task_id": str(getattr(manifest, "task_id", "") or ""),
            "manifest_id": str(getattr(manifest, "manifest_id", "") or ""),
            "manifest_fingerprint": (str(manifest.fingerprint())
                                     if callable(getattr(manifest, "fingerprint", None)) else ""),
            "manifest_schema_version": str(getattr(manifest, "schema_version", "") or ""),
            "prompt_version": str(prompt_version or ""),
            "model_policy": str(model_policy or ""),
            "model": str(model or ""),
            #: `{"type": "disabled"}` 这类显式档位随请求一起留：截断与否，事后要能对上。
            "thinking": _jsonable(thinking),
            "system_sha256": _sha256(str(system_text or "")) if system_text else "",
            "subsections": [{"subsection_id": str(getattr(s, "subsection_id", "") or ""),
                             "declared_aspect_ids": [str(a) for a
                                                     in (getattr(s, "declared_aspect_ids", ())
                                                         or ())],
                             "requirement_text": str(getattr(s, "requirement_text", "") or "")}
                            for s in getattr(manifest, "subsections", ()) or ()],
            "material_keys": [str(getattr(m, "citation_key", "") or "") for m in materials],
            "material_refs": [str(getattr(m, "member_ref", "") or "") for m in materials],
            "fact_keys": [str(getattr(f, "citation_key", "") or "") for f in facts],
            "material_count": len(materials),
            "fact_count": len(facts),
            "request_face_chars": len(face),
            "request_face_sha256": _sha256(face),
            "request_face_truncated": bool(truncated),
            "request_face": (face[:CITED_JOURNAL_REQUEST_FACE_LIMIT] if truncated else face),
        }
        self._flush()

    def record_reply(self, *, call_id: str, status: str, error: str, text: Any,
                     finish_reason: str = "", input_tokens: Any = None,
                     output_tokens: Any = None, latency_ms: Any = None,
                     model: str = "") -> None:
        """收到回复之后、**解析之前**：把模型**可见**的那一份留下来。"""
        visible = str(text if text is not None else "")
        self._state["reply"] = {
            "call_id": str(call_id or ""),
            "status": str(status or ""),
            "error": str(error or ""),
            "model": str(model or ""),
            "finish_reason": str(finish_reason or ""),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
            "visible_chars": len(visible),
            "visible_sha256": _sha256(visible),
            #: 恒为 `false`：这里存的是 `llm.client` 读出的**可见文本块**，不含隐藏推理。
            "hidden_reasoning_persisted": False,
            "text": visible,
        }
        self._flush()

    def record_parse(self, *, ok: bool, draft_id: str = "", sentence_count: int = 0,
                     subsection_count: int = 0, error_type: str = "", error: str = "",
                     reason: str = "", normalization: Any = None) -> None:
        """解析结果（含归一化台账：模型原 ID/结构位置/新 ID 的映射）。"""
        self._state["parse"] = {
            "ok": bool(ok), "error_type": str(error_type or ""), "error": str(error or ""),
            "reason": str(reason or ""), "draft_id": str(draft_id or ""),
            "sentence_count": int(sentence_count or 0),
            "subsection_count": int(subsection_count or 0),
            "normalization": _jsonable(normalization),
        }
        self._flush()

    # -- 读回（只读；供测试与读回面使用） -----------------------------------

    def to_dict(self) -> dict:
        return json.loads(json.dumps(self._state, ensure_ascii=False))

    def load(self) -> dict:
        """从盘上读回本文件（读回面用；缺文件返回空态）。"""
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))


def journal_for(out_dir: Path | str) -> CitedCallJournal:
    """按固定文件名在给定目录里建一本留存簿。"""
    return CitedCallJournal(Path(out_dir) / CITED_CALL_JOURNAL_FILENAME)


def assert_retention_shape(body: Mapping[str, Any]) -> Sequence[str]:
    """读回侧的**形状自查**：返回缺失/不一致的项（空序列 = 通过）。

    它只检查「这份留存是否自洽」，不评价正文质量——那需要清单与硬核对，属于别的轴。
    """
    problems: list[str] = []
    if str(body.get("journal_version", "")) != CITED_CALL_JOURNAL_VERSION:
        problems.append(f"journal_version 不是 {CITED_CALL_JOURNAL_VERSION!r}")
    if body.get("publishable") is not False:
        problems.append("publishable 不是 false（留存内容未经核对，不得发布）")
    if body.get("hidden_reasoning_persisted") is not False:
        problems.append("hidden_reasoning_persisted 不是 false（不得保存隐藏推理内容）")
    reply = body.get("reply")
    if isinstance(reply, Mapping) and reply.get("hidden_reasoning_persisted") is not False:
        problems.append("reply.hidden_reasoning_persisted 不是 false")
    if body.get("input") is None:
        problems.append("缺少 input 段（调用前应已落输入清单身份与请求面）")
    return tuple(problems)

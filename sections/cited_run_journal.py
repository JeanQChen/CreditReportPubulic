"""`rj-1`：一次运行**自己**的阶段日志（append-only `run_progress.jsonl`）。

为什么要有它
------------

三屏演示的第 2 屏原来播的是**已有产物的保存节点**——那是「产物清单」，不是「这次跑了什么」。
它答不了「这次点击之后系统实际走到了哪一步、什么时候走的、有没有失败」。本模块让链在
**真实阶段边界**上追加事件：每行一个 JSON，带真实 UTC 时间戳与阶段状态，落盘即 flush。

三条纪律
--------

1. **只写真实发生过的。** 事件的 `at_utc` 由本模块在写入那一刻取，不接受调用方传入一个
   「好看的时间」。没有日志文件就是没有日志文件，页面必须如实显示没有，**不得**用 `sleep`
   或百分比伪装进度。
2. **没有可恢复点就写没有。** 事件里的 ``resumable`` 恒为 ``False``、``checkpoint_id`` 恒为
   ``""``：本 run 目前确实**没有**与同一运行身份绑定的、可据此继续的恢复点。Evidence 阶段的
   数据检查点不是整份报告的恢复点，不得拿来冒充。
3. **只追加，不改写。** 每条事件写完即 flush 并关闭文件句柄；进程中途倒下，已落盘的阶段仍在。
   日志**不**参与任何发布决定，也不进 `report_version`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


JOURNAL_VERSION = "rj-1"
JOURNAL_NAME = "run_progress.jsonl"

#: 本 run 会在真实边界上记的阶段。**顺序即真实先后**，页面按它正序展示。
CITED_RUN_STAGES = (
    "run_input_verified",
    "financial_input_verified",
    "environment_built",
    "table_proof_matrix",
    "financial_snapshot_rechecked",
    "section_started",
    "writer_requested",
    "writer_replied",
    "sentence_checked",
    "rework_requested",
    "rework_replied",
    "review_requested",
    "review_replied",
    "report_version_written",
    "section_emitted",
    "run_failed",
)

CITED_RUN_STATUSES = ("started", "completed", "failed")

#: 阶段的中文读数。页面直接用它，避免在渲染层再维护一份映射而两处漂移。
STAGE_LABELS = {
    "run_input_verified": "运行输入已核验（上传字节 == 本 run 实读对象）",
    "financial_input_verified": "财务输入已核验（上传 XLSX == 快照 source_versions 逐份同字节）",
    "environment_built": "研究侧环境装配完成（当前 Evidence 权威）",
    "table_proof_matrix": "表格证明矩阵已生成",
    "financial_snapshot_rechecked": "财务快照在装配期复核：未漂移",
    "section_started": "本节开始（Pack 侧材料集已解析）",
    "writer_requested": "写作请求已发出",
    "writer_replied": "写作已返回",
    "sentence_checked": "机械逐句核对完成",
    "rework_requested": "返修请求已发出",
    "rework_replied": "返修已返回",
    "review_requested": "独立审阅请求已发出",
    "review_replied": "独立审阅已返回",
    "report_version_written": "本节 report_version 已签发",
    "section_emitted": "本节产物已落盘",
    "run_failed": "运行失败（已完成产物保留，不自动转成功）",
}


class CitedRunJournalError(ValueError):
    """日志无法写入或读回。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class RunProgressEvent:
    seq: int
    stage: str
    status: str
    at_utc: str
    section_id: str
    detail: str
    #: 恒为 False：本 run 没有与同一运行身份绑定的可恢复点。
    resumable: bool = False
    checkpoint_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"journal_version": JOURNAL_VERSION, "seq": self.seq, "stage": self.stage,
                "status": self.status, "at_utc": self.at_utc,
                "section_id": self.section_id, "detail": self.detail,
                "resumable": self.resumable, "checkpoint_id": self.checkpoint_id}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RunProgressEvent":
        try:
            return cls(seq=int(raw["seq"]), stage=str(raw["stage"]), status=str(raw["status"]),
                       at_utc=str(raw["at_utc"]), section_id=str(raw.get("section_id", "")),
                       detail=str(raw.get("detail", "")),
                       resumable=bool(raw.get("resumable", False)),
                       checkpoint_id=str(raw.get("checkpoint_id", "") or ""))
        except (KeyError, TypeError, ValueError) as exc:
            raise CitedRunJournalError(f"阶段事件结构不完整：{exc}") from exc

    @property
    def label(self) -> str:
        return STAGE_LABELS.get(self.stage, self.stage)


def journal_path(run_dir: str | Path) -> Path:
    return Path(run_dir) / JOURNAL_NAME


class RunProgressJournal:
    """往 `<run_dir>/run_progress.jsonl` 追加事件。**不覆盖**已存在的同名日志。"""

    def __init__(self, run_dir: str | Path, *, run_id: str) -> None:
        self.run_dir = Path(run_dir)
        self.run_id = str(run_id)
        self.path = journal_path(self.run_dir)
        self._seq = 0
        if self.path.exists():
            self._seq = len(read_run_progress(self.run_dir))

    @property
    def seq(self) -> int:
        return self._seq

    def record(self, stage: str, status: str, *, section_id: str = "",
               detail: str = "") -> RunProgressEvent:
        if stage not in CITED_RUN_STAGES:
            raise CitedRunJournalError(
                f"未登记的阶段名 {stage!r}：本日志只记真实阶段边界，"
                "不接受临时拼出来的名字")
        if status not in CITED_RUN_STATUSES:
            raise CitedRunJournalError(f"未登记的阶段状态 {status!r}")
        self._seq += 1
        event = RunProgressEvent(seq=self._seq, stage=stage, status=status, at_utc=_utc_now(),
                                 section_id=str(section_id), detail=str(detail))
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
            fh.flush()
        return event


def read_run_progress(run_dir: str | Path) -> tuple[RunProgressEvent, ...]:
    """读回日志。**缺失即返回空**——「没有日志」是一种如实状态，不是错误。

    单行损坏会具名抛错而不是被跳过：一份读不出事件的进度日志不能拿来当「跑到这里就没了」的
    证据，页面必须显示读不到，而不是显示一个短一点的列表。
    """
    path = journal_path(run_dir)
    if not path.is_file():
        return ()
    events: list[RunProgressEvent] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CitedRunJournalError(
                f"阶段日志第 {lineno} 行不可解码：{path}") from exc
        if raw.get("journal_version") != JOURNAL_VERSION:
            raise CitedRunJournalError(
                f"阶段日志第 {lineno} 行的版本 {raw.get('journal_version')!r} 不是 "
                f"{JOURNAL_VERSION!r}：本实现不隐式兼容其它版本")
        events.append(RunProgressEvent.from_dict(raw))
    return tuple(events)


def progress_summary(run_dir: str | Path) -> dict[str, Any]:
    """给页面用的读数。**只有真实事件**，没有推算出来的百分比或剩余时间。"""
    events = read_run_progress(run_dir)
    failed = next((e for e in events if e.status == "failed"), None)
    return {
        "event_count": len(events),
        "last_stage": events[-1].stage if events else "",
        "last_at_utc": events[-1].at_utc if events else "",
        "failed": failed is not None,
        "failure_stage": failed.stage if failed else "",
        "failure_detail": failed.detail if failed else "",
        "resumable": False,  # 本 run 没有与同一运行身份绑定的可恢复点
        "has_journal": bool(events),
    }

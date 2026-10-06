# -*- coding: utf-8 -*-
"""只读的 run 读取器：把"某个已落盘 run 的字节"变成可复核的证据引用。

它做三件事，且只做这三件事：

1. **定位。** 一个 run 的产物在 `evaluation/results/<run_id>/`，本次上传的原始字节在
   `data/run_inputs/<run_id>/`。两者都是**别人的**目录，本包只读。
2. **哈希。** 每个被用来支撑读数的文件都当场重算 SHA-256，写进证据索引；换了字节，
   哈希就变，读数就不再成立。
3. **登记。** 每次取证都记下"哪个 run、哪个文件、哪个字段"，避免读back 里出现
   "某个数字来自某处"这种无法复核的说法。

缺文件不是异常，是一种**结果**：`:meth:`RunReader.json` 返回 ``None``，取证引用带
``exists=False``。评测必须能把"文件不在盘上"和"文件在盘上但值为空"分开报。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: 一个 run 的产物根目录（相对仓库根）。
RESULTS_DIRNAME = "evaluation/results"

#: 本次上传原始字节的暂存根目录（相对仓库根）。
RUN_INPUT_DIRNAME = "data/run_inputs"

#: 读数一律按这两个子目录分账；不看别的目录，以免把历史 run 或另一个 run 的目录读进来。
_RUN_INPUT_FILE = "run_input_manifest.json"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class EvidenceRef:
    """一条"读数的依据"。`field_path` 指到具体字段，不指到整个文件。"""

    run_id: str
    path: str
    field_path: str
    sha256: str | None
    exists: bool
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "path": self.path,
            "field_path": self.field_path,
            "sha256": self.sha256,
            "exists": self.exists,
            "note": self.note,
        }


@dataclass
class RunReader:
    """一个 run 的只读视图。构造函数不读盘，只算出根路径。"""

    repo_root: Path
    run_id: str
    results_dirname: str = RESULTS_DIRNAME
    run_input_dirname: str = RUN_INPUT_DIRNAME
    #: 本批开发测试批次的读数（D01/D03 用）。它**不**来自被评 run，由评测命令注入；
    #: 缺省 ``None`` 时 D01/D03 如实报「未运行」，绝不用报告 run 的数据顶替。
    dev_batch: dict[str, Any] | None = None
    refs: list[EvidenceRef] = field(default_factory=list)
    _sha_cache: dict[str, str | None] = field(default_factory=dict, repr=False)
    _json_cache: dict[str, Any] = field(default_factory=dict, repr=False)

    # ---- 路径 -------------------------------------------------------------
    @property
    def run_dir(self) -> Path:
        return self.repo_root / self.results_dirname / self.run_id

    @property
    def input_dir(self) -> Path:
        return self.repo_root / self.run_input_dirname / self.run_id

    def rel(self, relpath: str) -> str:
        """run 产物目录下相对的仓库相对路径（正斜杠）。"""
        return f"{self.results_dirname}/{self.run_id}/{relpath}"

    def input_rel(self, relpath: str) -> str:
        """本次输入暂存目录下相对的仓库相对路径（正斜杠）。"""
        return f"{self.run_input_dirname}/{self.run_id}/{relpath}"

    def path(self, relpath: str) -> Path:
        return self.run_dir / relpath

    def input_path(self, relpath: str) -> Path:
        return self.input_dir / relpath

    # ---- 存在性与哈希 -----------------------------------------------------
    def has(self, relpath: str) -> bool:
        return self.path(relpath).is_file()

    def in_has(self, relpath: str) -> bool:
        return self.input_path(relpath).is_file()

    def sha256_of(self, abspath: Path) -> str | None:
        key = str(abspath)
        if key not in self._sha_cache:
            self._sha_cache[key] = _sha256_file(abspath) if abspath.is_file() else None
        return self._sha_cache[key]

    def json(self, relpath: str) -> Any | None:
        """读一个 JSON 产物；不存在或不是合法 JSON 都返回 ``None``。

        返回 ``None`` 时调用方必须自己去问 :meth:`has`，才能把"缺件"和"空对象"分开。
        """
        key = "run::" + relpath
        if key in self._json_cache:
            return self._json_cache[key]
        path = self.path(relpath)
        value: Any = None
        if path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                value = None
        self._json_cache[key] = value
        return value

    def input_json(self, relpath: str) -> Any | None:
        key = "in::" + relpath
        if key in self._json_cache:
            return self._json_cache[key]
        path = self.input_path(relpath)
        value: Any = None
        if path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                value = None
        self._json_cache[key] = value
        return value

    # ---- 取证 -------------------------------------------------------------
    def ref(self, relpath: str, field_path: str, *, note: str = "",
            uploaded: bool = False) -> dict[str, Any]:
        """登记一条证据并返回它的字典形式（可直接塞进结果行的 `evidence_refs`）。"""
        path = self.input_path(relpath) if uploaded else self.path(relpath)
        entry = EvidenceRef(
            run_id=self.run_id,
            path=self.input_rel(relpath) if uploaded else self.rel(relpath),
            field_path=field_path,
            sha256=self.sha256_of(path),
            exists=path.is_file(),
            note=note,
        )
        self.refs.append(entry)
        return entry.to_dict()

    def evidence_index(self) -> list[dict[str, Any]]:
        """去重后的证据索引：同一个(文件,字段)只留一条。"""
        seen: dict[tuple[str, str, str], dict[str, Any]] = {}
        for entry in self.refs:
            key = (entry.path, entry.field_path, entry.sha256 or "")
            seen.setdefault(key, entry.to_dict())
        return sorted(seen.values(), key=lambda e: (e["path"], e["field_path"]))

    # ---- 常用产物 ---------------------------------------------------------
    def section_dirs(self) -> tuple[str, ...]:
        """这个 run 实际落了产物的节（按目录探测，不按声明）。"""
        found = []
        for section in ("company", "financial"):
            if (self.run_dir / section).is_dir():
                found.append(section)
        return tuple(found)

    def ledger(self) -> dict[str, Any] | None:
        return self.json("cited_call_ledger.json")


__all__ = ["RunReader", "EvidenceRef", "RESULTS_DIRNAME", "RUN_INPUT_DIRNAME"]

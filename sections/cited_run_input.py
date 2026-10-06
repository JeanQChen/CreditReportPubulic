"""`cri-1`：把一次运行**绑定到操作者真正上传的那几份字节**上。

这一份要解决的问题
------------------

演示页原来只做「内存里算一遍 SHA-256、和已保存案例比一比」，比对通过之后点进去看的是
**历史 run 的产物**。它证明不了「本次上传的字节被本次运行读过」。本模块把上传的原始字节
落到一个**新建、隔离、不可覆盖**的运行输入目录里，并给出一个只按**内容哈希**定位的解析器，
供正式链在**读 PDF 的三个点**上使用。

三条纪律
--------

1. **文件名不参与查找。** 落盘一律是 ``objects/<完整 sha256>.pdf``；解析按
   ``(document_id, sha256)`` 命中，必须**恰好一份**。零份、两份都拒绝。上传时的文件名只
   作为凭据记进清单，**永不**用来找回一份文件，也**永不**回退到 `data/samples` 或
   evidence 库里登记的那个路径。
2. **不可覆盖。** 目标目录已存在即拒。缺一份、多一份、重复、任一哈希不符，都具名拒绝，
   没有「跳过校验继续」这条路。
3. **落盘之后还要被证明过。** :func:`stage_run_input` 写完立刻 :func:`load_run_input` 重读
   逐字节复核；:class:`RunInputResolver` 每次解析也都重算哈希——运行持续二十几分钟，
   「装载那一刻是对的」不等于「被读的那一刻还是那一份」。

它**不**写 `data/` 下的任何库，**不**改冻结资产，也**不**是第二套证据来源：声明的权威始终是
**当前 Evidence 登记**（:func:`registered_documents` 只读它），本模块只是把「操作者上传的
字节」与那份登记之间的等式物化下来。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


#: 运行输入绑定的 schema 版本。产物升级不动它；「运行输入按什么规则建立、按什么规则解析」
#: 这件事本身变了才动。
RUN_INPUT_VERSION = "cri-1"
MANIFEST_NAME = "run_input_manifest.json"
#: 同一份清单在运行目录里的**副本名**：运行自己读过什么，运行目录里说得清。
BINDING_NAME = "run_input_binding.json"
OBJECTS_DIRNAME = "objects"
#: 启动本次运行的那份请求面（谁点的、哪个模式、写哪一节）。它不是产物，是运行输入的一部分。
REQUEST_NAME = "run_request.json"

#: 本绑定承载的来源角色。它**不进身份体**（`identity_body()` 逐字未变，历史指纹因此不受影响），
#: 而是由"这份清单是什么"确定性导出：`cri-1` 承载的永远是 Evidence 文档。财务 XLSX 走
#: `sections.cited_financial_input`（`cfi-1`）另一份独立绑定，不混进这里。
EVIDENCE_SOURCE_ROLE = "evidence_document"


class CitedRunInputError(ValueError):
    """运行输入无法按声明建立或读回；调用方必须停住，不得回退到别的来源。"""


# ------------------------------------------------------------------ 哈希


def sha256_bytes(data: bytes) -> str:
    """小写十六进制。Evidence 登记、`@sha256-` 前缀与页面显示都是小写，这里统一到同一种写法。"""
    return hashlib.sha256(data).hexdigest().lower()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().lower()


def canonical_digest(body: Any) -> str:
    """规范 JSON（键排序、无空白）的 SHA-256。

    运行输入的各种身份体都用它：`cri-1` 的清单指纹、`cfi-1` 的财务绑定指纹。**只有一份实现**，
    否则「同一份清单在两处算出不同指纹」这种缺陷会以"指纹不符"的形式出现在完全没有改动过
    的文件上。
    """
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest().lower()


#: 旧名保留为别名：本模块内既有调用点逐字不变。
_canonical = canonical_digest


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ 类型


@dataclass(frozen=True)
class DeclaredDocument:
    """**声明的一侧**：当前 Evidence 登记里这一家公司的一份来源材料。

    它是权威侧的读数（只读 `evidence.db` 得到），不是从上传面抄来的。
    """

    document_id: str
    document_version: str
    sha256: str
    filename: str
    size_bytes: int
    page_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"document_id": self.document_id, "document_version": self.document_version,
                "sha256": self.sha256, "filename": self.filename,
                "size_bytes": self.size_bytes, "page_count": self.page_count}


@dataclass(frozen=True)
class StagedObject:
    """**上传的一侧**：一份落盘的原始字节，以及它对应哪一个登记文档。"""

    document_id: str
    document_version: str
    declared_sha256: str
    declared_filename: str
    #: 操作者选择的那个文件名。**只作凭据**：任何查找都不看它。
    uploaded_name: str
    size_bytes: int
    object_relpath: str
    upload_order: int

    def to_dict(self) -> dict[str, Any]:
        return {"document_id": self.document_id, "document_version": self.document_version,
                "declared_sha256": self.declared_sha256,
                "declared_filename": self.declared_filename,
                "uploaded_name": self.uploaded_name, "size_bytes": self.size_bytes,
                "object_relpath": self.object_relpath, "upload_order": self.upload_order}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StagedObject":
        try:
            return cls(document_id=str(raw["document_id"]),
                       document_version=str(raw["document_version"]),
                       declared_sha256=str(raw["declared_sha256"]).lower(),
                       declared_filename=str(raw["declared_filename"]),
                       uploaded_name=str(raw["uploaded_name"]),
                       size_bytes=int(raw["size_bytes"]),
                       object_relpath=str(raw["object_relpath"]),
                       upload_order=int(raw["upload_order"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise CitedRunInputError(f"运行输入清单的成员结构不完整：{exc}") from exc


@dataclass(frozen=True)
class RunInputBinding:
    run_input_version: str
    run_id: str
    created_at_utc: str
    manifest_sha256: str
    documents: tuple[StagedObject, ...]

    def identity_body(self) -> dict[str, Any]:
        return {"run_input_version": self.run_input_version, "run_id": self.run_id,
                "created_at_utc": self.created_at_utc,
                "documents": [d.to_dict() for d in self.documents]}

    def canonical_fingerprint(self) -> str:
        return _canonical(self.identity_body())

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_body(), "manifest_sha256": self.manifest_sha256}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RunInputBinding":
        if not isinstance(raw, dict):
            raise CitedRunInputError("运行输入清单不是一个 JSON 对象")
        try:
            documents = tuple(StagedObject.from_dict(d) for d in raw["documents"])
            return cls(run_input_version=str(raw["run_input_version"]),
                       run_id=str(raw["run_id"]),
                       created_at_utc=str(raw["created_at_utc"]),
                       manifest_sha256=str(raw["manifest_sha256"]).lower(),
                       documents=documents)
        except (KeyError, TypeError) as exc:
            raise CitedRunInputError(f"运行输入清单字段不完整：{exc}") from exc

    def by_document_id(self, document_id: str) -> StagedObject | None:
        return next((d for d in self.documents if d.document_id == document_id), None)

    def declared_hashes(self) -> dict[str, str]:
        return {d.document_id: d.declared_sha256 for d in self.documents}

    @property
    def source_role(self) -> str:
        """本绑定承载的来源角色。见 :data:`EVIDENCE_SOURCE_ROLE`：不进身份体，只作读数。"""
        return EVIDENCE_SOURCE_ROLE


# ------------------------------------------------------------------ 声明侧


def current_subjects(financial_db: str | Path) -> tuple[str, ...]:
    """只读列出财务库里**有 current 快照**的主体。

    与 `scripts/run_m930_3_cited_chain.py::_current_snapshot_subjects` 同一读法与同一意图：
    能在代码里写死的只有「读哪张表」，不能写死公司名。两份实现刻意保持独立——页面侧不得
    因为「链那边能跑」就假定自己也知道主体是谁。
    """
    import sqlite3

    db = Path(financial_db)
    if not db.is_file():
        return ()
    conn = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT company_id FROM current_snapshot ORDER BY company_id").fetchall()
    finally:
        conn.close()
    return tuple(str(r[0]) for r in rows)


def registered_documents(evidence_db: str | Path, company_id: str
                         ) -> tuple[DeclaredDocument, ...]:
    """只读当前 Evidence 登记里这一家公司的**当前**来源材料。**不写库、不迁移。**

    只收「status=current 且确实有 current evidence set」的那些——没有当前证据集的行，链那边
    也进不了源集（`:func:`harness.source_manifest.build_source_manifest` 同一条口径），
    把这种行算进声明只会让上传面被迫多交一份永远用不上的文件。
    """
    from evidence import store as ESTORE

    out: list[DeclaredDocument] = []
    for row in ESTORE.list_documents_ro(evidence_db, company_id):
        if str(row.status) != "current":
            continue
        if ESTORE.current_evidence_set_ro(evidence_db, row.company_id,
                                          row.document_id, row.document_version) is None:
            continue
        out.append(DeclaredDocument(
            document_id=str(row.document_id), document_version=str(row.document_version),
            sha256=str(row.file_sha256).lower(),
            filename=Path(str(row.source_path or row.document_id)).name,
            size_bytes=int(row.file_size), page_count=row.page_count))
    return tuple(out)


# ------------------------------------------------------------------ 建立


def stage_run_input(uploads: Sequence[tuple[str, bytes]], target_dir: str | Path, *,
                    declared: Sequence[DeclaredDocument],
                    run_id: str) -> RunInputBinding:
    """把这次上传的原始字节落到一个**新建、不可覆盖**的运行输入目录里。

    :param uploads: ``[(上传时的文件名, 原始字节), …]``，取自浏览器控件的当次取值。
    :param target_dir: 本次运行的输入目录；**已存在即拒**。
    :param declared: 当前 Evidence 登记声明的那几份材料。
    :returns: 落盘并**重读复核过**的绑定。

    拒绝面（全部具名，不软失败）：声明为空、目录已存在、上传里有重复字节、声明里有重复哈希
    或重复 document_id、缺一份、多一份、任一哈希不在声明里。
    """
    declared = tuple(declared)
    if not declared:
        raise CitedRunInputError(
            "当前 Evidence 登记里没有可声明的来源材料：本 run 不建立空输入")
    target = Path(target_dir)
    if target.exists():
        raise CitedRunInputError(f"运行输入目录已存在，拒绝覆盖：{target}")

    uploaded: list[tuple[str, bytes]] = [(str(name), bytes(data)) for name, data in uploads]
    digests = [sha256_bytes(data) for _, data in uploaded]

    by_hash: dict[str, tuple[str, bytes, int]] = {}
    for order, ((name, data), digest) in enumerate(zip(uploaded, digests)):
        if digest in by_hash:
            raise CitedRunInputError(
                f"上传里有两份**内容相同**的文件（sha256 {digest[:16]}…）："
                f"{by_hash[digest][0]!r} 与 {name!r}。本 run 拒绝「重复」，"
                "也不猜哪一份算数")
        by_hash[digest] = (name, data, order)

    wanted: dict[str, DeclaredDocument] = {}
    ids: set[str] = set()
    for doc in declared:
        key = doc.sha256.lower()
        if key in wanted:
            raise CitedRunInputError(
                f"声明里有两份 sha256 相同的文档（{key[:16]}…）："
                f"{wanted[key].document_id!r} 与 {doc.document_id!r}；"
                "字节相同就无法按哈希判定哪一份是哪个文档，本 run 拒绝")
        if doc.document_id in ids:
            raise CitedRunInputError(f"声明里 document_id 重复：{doc.document_id!r}")
        wanted[key] = doc
        ids.add(doc.document_id)

    missing = tuple(d.document_id for d in declared if d.sha256.lower() not in by_hash)
    unexpected = tuple(name for (name, _), digest in zip(uploaded, digests)
                       if digest not in wanted)
    if missing or unexpected:
        parts = []
        if missing:
            parts.append("缺少声明的来源材料：" + "、".join(missing))
        if unexpected:
            parts.append("上传了登记之外的文件：" + "、".join(unexpected))
        raise CitedRunInputError(
            "运行输入与当前 Evidence 登记不符——" + "；".join(parts)
            + "。本 run 拒绝启动，也不回退到 data/samples 或登记路径")

    target.mkdir(parents=True, exist_ok=False)
    objects_dir = target / OBJECTS_DIRNAME
    objects_dir.mkdir()

    staged: list[StagedObject] = []
    for doc in declared:
        key = doc.sha256.lower()
        name, data, order = by_hash[key]
        relpath = f"{OBJECTS_DIRNAME}/{key}.pdf"
        destination = target / relpath
        with open(destination, "wb") as fh:
            fh.write(data)
        staged.append(StagedObject(
            document_id=doc.document_id, document_version=doc.document_version,
            declared_sha256=key, declared_filename=doc.filename,
            uploaded_name=name, size_bytes=len(data),
            object_relpath=relpath, upload_order=order))

    binding = RunInputBinding(
        run_input_version=RUN_INPUT_VERSION, run_id=str(run_id),
        created_at_utc=_utc_now(), manifest_sha256="", documents=tuple(staged))
    binding = replace(binding, manifest_sha256=binding.canonical_fingerprint())
    (target / MANIFEST_NAME).write_text(
        json.dumps(binding.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    #: 落盘之后立刻重读逐字节复核。写成功 != 写对了，这一步把「落盘」也变成一条被证明过的读数。
    return load_run_input(target, declared=declared)


# ------------------------------------------------------------------ 读回


def _resolve_object(root: Path, relpath: str) -> Path:
    rel = Path(relpath)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise CitedRunInputError(f"运行输入对象路径非法：{relpath!r}")
    try:
        path = (root / rel).resolve(strict=False)
        path.relative_to(root.resolve(strict=False))
    except (OSError, ValueError) as exc:
        raise CitedRunInputError(f"运行输入对象越界：{relpath!r}") from exc
    return path


def load_run_input(target_dir: str | Path, *,
                   declared: Sequence[DeclaredDocument] | None = None) -> RunInputBinding:
    """读回一份运行输入清单，并**逐字节重验**每份对象。

    `declared` 给出时，还要求清单里的 ``document_id → sha256`` 与它**逐项相等**——
    「上传的三份」必须就是「当前登记的三份」，不是其中一部分。
    """
    target = Path(target_dir)
    if not target.is_dir():
        raise CitedRunInputError(f"运行输入目录不可达：{target}")
    manifest_path = target / MANIFEST_NAME
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CitedRunInputError(f"运行输入清单读不到或不可解码：{manifest_path}") from exc
    binding = RunInputBinding.from_dict(raw)
    if binding.run_input_version != RUN_INPUT_VERSION:
        raise CitedRunInputError(
            f"运行输入清单的版本 {binding.run_input_version!r} 不是本实现支持的 "
            f"{RUN_INPUT_VERSION!r}：本实现不隐式兼容其它版本")
    if binding.manifest_sha256 != binding.canonical_fingerprint():
        raise CitedRunInputError(
            "运行输入清单的自身指纹不符：清单在落盘之后被改写过，本 run 拒绝使用它")

    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for obj in binding.documents:
        if obj.document_id in seen_ids:
            raise CitedRunInputError(f"运行输入清单里 document_id 重复：{obj.document_id!r}")
        if obj.declared_sha256 in seen_hashes:
            raise CitedRunInputError(
                f"运行输入清单里 sha256 重复：{obj.declared_sha256[:16]}…")
        seen_ids.add(obj.document_id)
        seen_hashes.add(obj.declared_sha256)
        path = _resolve_object(target, obj.object_relpath)
        if not path.is_file():
            raise CitedRunInputError(
                f"运行输入对象已丢失（{obj.document_id}）：{obj.object_relpath}")
        blob = path.read_bytes()
        if len(blob) != obj.size_bytes:
            raise CitedRunInputError(
                f"运行输入对象 {obj.document_id} 的字节数已变化："
                f"清单 {obj.size_bytes} / 实读 {len(blob)}")
        actual = sha256_bytes(blob)
        if actual != obj.declared_sha256:
            raise CitedRunInputError(
                f"运行输入对象 {obj.document_id} 的字节已变化："
                f"清单 {obj.declared_sha256} / 实读 {actual}（fail-closed）")

    if declared is not None:
        want = {d.document_id: d.sha256.lower() for d in declared}
        got = binding.declared_hashes()
        if want != got:
            raise CitedRunInputError(
                "运行输入清单与当前 Evidence 登记不是同一组材料："
                f"登记 {sorted(want)} / 清单 {sorted(got)}；"
                "哈希或成员任一处不同都拒绝（本 run 不「按名字对上就算」）")
    return binding


# ------------------------------------------------------------------ 解析


class RunInputResolver:
    """把 `(document_id, file_sha256)` 解析成本 run **自己那份上传对象**的路径。

    正式链读 PDF 的三个点都必须走它。**没有任何回退分支**：命中不了就是失败，不会去读
    evidence 库里登记的 `source_path`，更不会去 `data/samples` 找一份同名文件。
    """

    def __init__(self, root: str | Path, binding: RunInputBinding) -> None:
        self.root = Path(root)
        self.binding = binding
        index: dict[tuple[str, str], list[StagedObject]] = {}
        for obj in binding.documents:
            index.setdefault((obj.document_id, obj.declared_sha256), []).append(obj)
        self._index = index

    @classmethod
    def from_dir(cls, target_dir: str | Path, *,
                 declared: Sequence[DeclaredDocument] | None = None) -> "RunInputResolver":
        target = Path(target_dir)
        return cls(target, load_run_input(target, declared=declared))

    def resolve(self, *, document_id: str, file_sha256: str) -> Path:
        """定位并**当场重算哈希**。命中不唯一、对象丢失或字节变化，全部抛错。"""
        key = (str(document_id), str(file_sha256).lower())
        hits = self._index.get(key, [])
        if not hits:
            uploaded = [o.declared_sha256 for o in self.binding.documents
                        if o.document_id == key[0]]
            hint = (f"（本 run 上传的 {key[0]} 哈希是 "
                    + "、".join(h[:16] + "…" for h in uploaded) + "）") if uploaded else \
                   f"（本 run 的上传里没有文档 {key[0]!r}）"
            raise CitedRunInputError(
                f"本 run 的上传输入里没有 {key[0]} 的 sha256 {key[1][:16]}… 对象{hint}："
                "拒绝以登记路径或 data/samples 顶替（fail-closed）")
        if len(hits) != 1:
            raise CitedRunInputError(
                f"本 run 的上传输入里 {key[0]} 的对象不唯一（{len(hits)} 份）："
                "不「就近取一份」")
        obj = hits[0]
        path = _resolve_object(self.root, obj.object_relpath)
        if not path.is_file():
            raise CitedRunInputError(
                f"本 run 的上传对象在运行期间丢失（{obj.document_id}）：{obj.object_relpath}")
        actual = sha256_file(path)
        if actual != obj.declared_sha256:
            raise CitedRunInputError(
                f"本 run 的上传对象在运行期间字节变化（{obj.document_id}）："
                f"清单 {obj.declared_sha256} / 实读 {actual}（fail-closed）")
        return path

    def owns(self, path: str | Path) -> bool:
        """这个路径是不是落在本 run 的输入目录内。用于在解析之后再做一次范围断言。"""
        try:
            Path(path).resolve(strict=False).relative_to(self.root.resolve(strict=False))
        except (OSError, ValueError):
            return False
        return True


# ------------------------------------------------------------------ 请求面


def write_run_request(target_dir: str | Path, *, run_id: str, mode: str, section_ids,
                      model: str | None = None, profile_path: str | None = None,
                      subject: str | None = None, subject_name: str | None = None) -> dict:
    """把「谁点了开始生成、拿什么参数启动」写进运行输入目录。

    它是运行输入的一部分而不是产物：运行目录里的产物是链写出来的，这一份是**发起侧**写的。
    两者分开，页面才能把「我请求了什么」与「链实际做了什么」摆在一起看。
    """
    payload = {
        "run_id": str(run_id), "mode": str(mode),
        "section_ids": [str(s) for s in section_ids],
        "model": (None if model is None else str(model)),
        "profile_path": (None if profile_path is None else str(profile_path)),
        "subject": (None if subject is None else str(subject)),
        "subject_name": (None if subject_name is None else str(subject_name)),
        "requested_at_utc": _utc_now(),
    }
    (Path(target_dir) / REQUEST_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload

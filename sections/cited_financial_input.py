"""`cfi-1`：把一次运行**绑定到操作者真正上传的那三份财务 XLSX**上，并证明它们与当前权威快照同字节。

它解决的问题
------------

`cri-1` 只覆盖三份业务电子 PDF。财务节的权威输入是另一组材料（三份 Excel 报表及其背后的
`financial_v2.db` 快照），与那三份 PDF 之间**没有任何可互相勾稽的内容身份**。本模块给这组材料
建一份**独立、typed** 的绑定，而不是把它们塞进 `cri-1` 伪装成 Evidence PDF——两者是不同的来源
角色，混进同一份清单会让"这三份是证据文档"这句话变成一句读者无法核对的断言。

两次证明，缺一不可
------------------

1. **同字节**。本次上传的三份 XLSX 必须与该快照**完整**的 `source_versions` 逐份 SHA-256 相等。
   文件名不参与判定：落盘一律 ``financial_objects/<sha256>.xlsx``，解析按
   ``(source_version, sha256)`` 命中，**恰好一份**。缺、多、重复、任一哈希不符都具名拒绝，
   **一处都不回落**到 `data/samples/300750/financial/`。
2. **同一身份**。主体、期间、合并口径、币种、用途与快照 ID 五项必须同时对上，且该快照在只读
   权威查询里是 `exists ∧ is_current ∧ validity == "valid" ∧ 未 report_blocked ∧ 未隔离`。
   装配期还会用 :func:`verify_snapshot_unchanged` 再核一次"快照没有漂移"。

对外准确表述
------------

本批**不**写共享财务库、**不**重抽取工作簿。这件事的准确说法是
「**本次上传核对并复用同字节的已建立权威快照**」，不是"本次重新生成了财务权威"。

三条纪律
--------

1. **只读**。本模块对 `financial_v2.db` 只开 `mode=ro` + `PRAGMA query_only=ON`，**从不**调用
   `financial_v2.store.init_db()`（那会执行 DDL），也从不写任何库。
2. **单一解析入口**。快照的选取复用 `evaluation.run_m930_3_acceptance._financial_dims`——本 run 的
   财务节实际消费的就是它选出的那一条；权威五轴复用 `sections.financial_pack_artifact` 的只读
   查询。**不另写一套口径**，否则绑定住的快照与被消费的快照可能是两条。
3. **不可覆盖**。绑定文件已存在即拒，没有"重跑一遍覆盖掉"这条路。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from sections import cited_run_input as CRI

#: 绑定 schema 版本。"运行输入按什么规则建立、按什么规则解析"这件事变了才动。
FINANCIAL_INPUT_VERSION = "cfi-1"
BINDING_FILENAME = "financial_input_binding.json"
#: 同一份绑定在运行目录里的**副本名**（链在建目录之后复制一份，说明本轮读的是哪几份字节）。
BINDING_COPY_NAME = "financial_input_binding.json"
OBJECTS_DIRNAME = "financial_objects"
#: 本绑定承载的来源角色。与 `cri-1` 的 `evidence_document` 是**两个**角色，不得混用。
FINANCIAL_SOURCE_ROLE = "financial_source"
#: 快照必须逐字等于这个有效性。`None`/`stale`/`superseded`/`unknown` 一律拒。
REQUIRED_VALIDITY = "valid"


class CitedFinancialInputError(ValueError):
    """财务输入无法按声明建立或读回；调用方必须停住，不得回退到 samples 或旧快照。"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ 只读库访问


def _readonly_conn(db: str | Path) -> sqlite3.Connection:
    """只读打开财务库。**绝不创建**、**绝不迁移**、**绝不执行 DDL**。"""
    path = Path(db)
    if not path.is_file():
        raise CitedFinancialInputError(f"财务库不存在（只读打开，绝不创建）：{path}")
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _json_list(raw: Any) -> tuple[str, ...]:
    """库里几个"JSON 数组"列（`source_versions` / `report_periods`）的统一读法。"""
    if raw is None:
        return ()
    if isinstance(raw, (list, tuple)):
        return tuple(str(item) for item in raw)
    text = str(raw).strip()
    if not text:
        return ()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CitedFinancialInputError(f"库里的 JSON 列读不开：{text[:64]!r}（{exc}）") from exc
    if not isinstance(parsed, (list, tuple)):
        raise CitedFinancialInputError(f"库里的 JSON 列不是数组：{text[:64]!r}")
    return tuple(str(item) for item in parsed)


# ------------------------------------------------------------------ 类型


@dataclass(frozen=True)
class SnapshotIdentity:
    """一份财务快照的**完整身份**：口径四轴 + 主体 + 期末 + 有效性 + 全部来源版本。"""

    snapshot_id: str
    company_id: str
    #: 读者面写法的主体名称。它是**声明 + 核对**的结果（见 `_declared_company_name`），
    #: 因此和口径一样属于被绑定的身份，不能被改口换成库里的另一个名字。
    company_name: str
    as_of_date: str
    scope: str
    currency: str
    purpose: str
    validity: str
    #: `(source_version, file_sha256)`，按 source_version 排序后存。上传侧要逐份对上它。
    source_versions: tuple[tuple[str, str], ...]

    def identity_body(self) -> dict[str, Any]:
        return {"snapshot_id": self.snapshot_id, "company_id": self.company_id,
                "company_name": self.company_name, "as_of_date": self.as_of_date,
                "scope": self.scope,
                "currency": self.currency, "purpose": self.purpose,
                "validity": self.validity,
                "source_versions": [list(pair) for pair in self.source_versions]}

    def to_dict(self) -> dict[str, Any]:
        return self.identity_body()

    @classmethod
    def from_dict(cls, raw: Any) -> "SnapshotIdentity":
        if not isinstance(raw, dict):
            raise CitedFinancialInputError("快照身份不是一个 JSON 对象")
        try:
            pairs = tuple((str(p[0]), str(p[1]).lower()) for p in raw["source_versions"])
        except (KeyError, TypeError, IndexError, ValueError) as exc:
            raise CitedFinancialInputError(f"快照身份的来源版本形状不对：{exc}") from exc
        return cls(snapshot_id=str(raw["snapshot_id"]), company_id=str(raw["company_id"]),
                   company_name=str(raw["company_name"]),
                   as_of_date=str(raw["as_of_date"]), scope=str(raw["scope"]),
                   currency=str(raw["currency"]), purpose=str(raw["purpose"]),
                   validity=str(raw["validity"]),
                   source_versions=tuple(sorted(pairs)))


@dataclass(frozen=True)
class DeclaredFinancialSource:
    """**声明侧**：只读财务库得到的一条快照来源版本。**不是**从上传面抄来的。"""

    source_version: str
    source_document_id: str
    #: 库里登记的 basename。**只作凭据**：任何查找都不看它。
    source_name: str
    source_class: str
    file_sha256: str
    file_type: str
    size_bytes: int
    record_set_version: str
    report_periods: tuple[str, ...]
    statement_scope: str
    currency: str
    unit: str

    def to_dict(self) -> dict[str, Any]:
        return {"source_version": self.source_version,
                "source_document_id": self.source_document_id,
                "source_name": self.source_name, "source_class": self.source_class,
                "file_sha256": self.file_sha256, "file_type": self.file_type,
                "size_bytes": self.size_bytes,
                "record_set_version": self.record_set_version,
                "report_periods": list(self.report_periods),
                "statement_scope": self.statement_scope,
                "currency": self.currency, "unit": self.unit}


@dataclass(frozen=True)
class StagedFinancialSource:
    """**上传侧**：一份落盘的原始字节，以及它对应哪一条快照来源版本。"""

    source_version: str
    source_document_id: str
    declared_sha256: str
    declared_filename: str
    #: 操作者选择的那个文件名。**只作凭据**：任何查找都不看它。
    uploaded_name: str
    size_bytes: int
    object_relpath: str
    upload_order: int
    source_role: str

    def to_dict(self) -> dict[str, Any]:
        return {"source_version": self.source_version,
                "source_document_id": self.source_document_id,
                "declared_sha256": self.declared_sha256,
                "declared_filename": self.declared_filename,
                "uploaded_name": self.uploaded_name, "size_bytes": self.size_bytes,
                "object_relpath": self.object_relpath, "upload_order": self.upload_order,
                "source_role": self.source_role}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StagedFinancialSource":
        try:
            return cls(source_version=str(raw["source_version"]),
                       source_document_id=str(raw["source_document_id"]),
                       declared_sha256=str(raw["declared_sha256"]).lower(),
                       declared_filename=str(raw["declared_filename"]),
                       uploaded_name=str(raw["uploaded_name"]),
                       size_bytes=int(raw["size_bytes"]),
                       object_relpath=str(raw["object_relpath"]),
                       upload_order=int(raw["upload_order"]),
                       source_role=str(raw["source_role"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise CitedFinancialInputError(f"财务输入清单的成员结构不完整：{exc}") from exc


@dataclass(frozen=True)
class FinancialInputBinding:
    financial_input_version: str
    run_id: str
    created_at_utc: str
    binding_sha256: str
    snapshot: SnapshotIdentity
    sources: tuple[StagedFinancialSource, ...]

    @property
    def source_role(self) -> str:
        return FINANCIAL_SOURCE_ROLE

    def identity_body(self) -> dict[str, Any]:
        return {"financial_input_version": self.financial_input_version,
                "run_id": self.run_id, "created_at_utc": self.created_at_utc,
                "snapshot": self.snapshot.to_dict(),
                "sources": [s.to_dict() for s in self.sources]}

    def canonical_fingerprint(self) -> str:
        return CRI.canonical_digest(self.identity_body())

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_body(), "binding_sha256": self.binding_sha256}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FinancialInputBinding":
        if not isinstance(raw, dict):
            raise CitedFinancialInputError("财务输入清单不是一个 JSON 对象")
        try:
            sources = tuple(StagedFinancialSource.from_dict(s) for s in raw["sources"])
            snapshot = SnapshotIdentity.from_dict(raw["snapshot"])
            return cls(financial_input_version=str(raw["financial_input_version"]),
                       run_id=str(raw["run_id"]),
                       created_at_utc=str(raw["created_at_utc"]),
                       binding_sha256=str(raw["binding_sha256"]).lower(),
                       snapshot=snapshot, sources=sources)
        except (KeyError, TypeError) as exc:
            raise CitedFinancialInputError(f"财务输入清单字段不完整：{exc}") from exc

    def by_source_version(self, source_version: str) -> StagedFinancialSource | None:
        return next((s for s in self.sources if s.source_version == source_version), None)

    def declared_hashes(self) -> dict[str, str]:
        return {s.source_version: s.declared_sha256 for s in self.sources}


# ------------------------------------------------------------------ 声明侧（只读）


def _authority_five(fin_db: str | Path, snapshot: SnapshotIdentity) -> Any:
    """权威五轴：复用 `financial_pack_artifact` 的**只读**独立查询，不另写一套判据。"""
    from sections import financial_pack_artifact as FPA

    return FPA._readonly_snapshot_authority(fin_db, snapshot)


def registered_subject_names(fin_db: str | Path, *, subject: str) -> tuple[str, ...]:
    """某主体在财务库里**登记过**的主体名称（只读，去重后排序）。

    `subj-2` 要的是「声明 + 核对」：读者面写的公司名称必须由权威自己的登记去核，不能由页面
    或 runner 编一个。所以页面在让操作者事先声明名称之前，先要知道库里到底登记了哪些名字：

    * 恰好一个 ⇒ 可以声明它；`current_snapshot_identity` 随后会再核一遍逐字相等。
    * 零个 ⇒ 这个主体没有被权威登记过，谁也不能替它编一个名字。
    * 多个 ⇒ 来源侧自相矛盾（与 `_declared_company_name` 同一判据），页面不得任选一个。

    本函数**只回答"登记了什么"**，不替调用方决定哪种情况该拒——三种情况都如实返回元组，
    由调用方按上面三条各自具名拒。
    """
    conn = _readonly_conn(fin_db)
    try:
        rows = conn.execute(
            "SELECT DISTINCT declared_company_name FROM financial_source_document "
            "WHERE company_id = ? AND subject_match_status = 'matched' "
            "AND declared_company_name IS NOT NULL AND TRIM(declared_company_name) <> ''",
            (str(subject),)).fetchall()
    finally:
        conn.close()
    return tuple(sorted({str(r[0]).strip() for r in rows}))


def current_snapshot_identity(fin_db: str | Path, *, subject: str, subject_name: str = "",
                              scope: str | None = None, currency: str | None = None,
                              purpose: str | None = None,
                              snapshot_as_of: str | None = None,
                              report_as_of: str = "") -> SnapshotIdentity:
    """本 run 财务节**实际会消费**的那条快照，连同它的完整身份。

    选取复用 `run_m930_3_acceptance._financial_dims`——同一个入口，因此"绑定住的"与"被消费的"
    不可能是两条快照。`_financial_dims` 只看 `current_snapshot` 指针，**不**看有效性；因此这里
    再叠一次只读权威五轴，把 `validity == "valid"` 也变成绑定的一部分。
    """
    from evaluation import run_m930_3_acceptance as ACC

    if not str(subject_name or "").strip():
        raise CitedFinancialInputError(
            "必须声明主体名称：`subj-2` 的主体名称是「声明 + 核对」，本模块不从库里挑一个名字补上")
    #: `report_as_of` 是**报告参考日**，不参与快照选取（选取只看 `snapshot_as_of` 与
    #: `current_snapshot` 指针）。声明类型要求它非空，因此缺省给当天；它不会进入绑定身份体。
    clock = str(report_as_of or "").strip() or _utc_now()[:10]
    declaration = ACC.DeclaredReportInput(
        subject_id=str(subject), report_as_of=clock,
        subject_name=str(subject_name),
        scope=scope, currency=currency, purpose=purpose, snapshot_as_of=snapshot_as_of)
    dims = ACC._financial_dims(Path(fin_db), declaration)
    if not dims:
        raise CitedFinancialInputError(
            f"财务库读不到或不可达：{fin_db}（本 run 不拿旧快照或合成输入顶替）")
    if str(dims["company_id"]) != str(subject):
        raise CitedFinancialInputError(
            f"财务快照核对结果 {dims['company_id']!r} 与声明主体 {subject!r} 不一致："
            "声明主体与快照必须指向同一家公司")

    conn = _readonly_conn(fin_db)
    try:
        row = conn.execute(
            "SELECT source_versions FROM financial_snapshot WHERE snapshot_id = ?",
            (str(dims["snapshot_id"]),)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise CitedFinancialInputError(f"快照 {dims['snapshot_id']!r} 在库里不存在")
    version_ids = _json_list(row["source_versions"])
    if not version_ids:
        raise CitedFinancialInputError(
            f"快照 {dims['snapshot_id']!r} 没有任何 source_version：没有来源的权威快照"
            "无法与上传的 XLSX 逐份对上，本 run 拒绝")

    sources = declared_financial_sources(fin_db, version_ids=version_ids)
    snapshot = SnapshotIdentity(
        snapshot_id=str(dims["snapshot_id"]), company_id=str(dims["company_id"]),
        company_name=str(dims.get("company_name") or subject_name),
        as_of_date=str(dims["as_of_date"]), scope=str(dims["scope"]),
        currency=str(dims["currency"]), purpose=str(dims["purpose"]), validity="",
        source_versions=tuple(sorted((s.source_version, s.file_sha256) for s in sources)))

    authority = _authority_five(fin_db, snapshot)
    problems: list[str] = []
    if not authority.exists:
        problems.append("快照在库里不存在")
    if not authority.is_current:
        problems.append(f"快照不是 current 指针指向（is_current={authority.is_current}）")
    if str(authority.validity) != REQUIRED_VALIDITY:
        problems.append(f"有效性是 {authority.validity!r}，本 run 只接受 {REQUIRED_VALIDITY!r}")
    if authority.report_blocked:
        problems.append("快照 report_blocked")
    if authority.quarantined:
        problems.append("快照已隔离")
    if problems:
        raise CitedFinancialInputError(
            f"快照 {snapshot.snapshot_id!r} 不是可用的当前有效权威：" + "；".join(problems)
            + "。本 run 在第一个请求之前拒绝，不拿旧快照顶替")
    return replace(snapshot, validity=str(authority.validity))


def declared_financial_sources(fin_db: str | Path, *,
                               version_ids: Sequence[str]) -> tuple[DeclaredFinancialSource, ...]:
    """只读拉出这几条 source_version 的完整来源身份（哈希、期间、口径、币种、单位）。"""
    ids = [str(v) for v in version_ids]
    if not ids:
        return ()
    placeholders = ",".join("?" for _ in ids)
    conn = _readonly_conn(fin_db)
    try:
        vrows = conn.execute(
            "SELECT sv.source_version, sv.source_document_id, sv.file_sha256, sv.file_type, "
            "sv.file_size, sd.source_name, sd.source_class "
            "FROM financial_source_version sv "
            "LEFT JOIN financial_source_document sd "
            "  ON sd.source_document_id = sv.source_document_id "
            f"WHERE sv.source_version IN ({placeholders})", ids).fetchall()
        rrows = conn.execute(
            "SELECT record_set_version, source_version, report_periods, currency, unit, "
            "statement_scope FROM financial_record_set "
            f"WHERE source_version IN ({placeholders})", ids).fetchall()
    finally:
        conn.close()

    by_version: dict[str, sqlite3.Row] = {str(r["source_version"]): r for r in vrows}
    sets_by_version: dict[str, sqlite3.Row] = {str(r["source_version"]): r for r in rrows}
    missing = [v for v in ids if v not in by_version]
    if missing:
        raise CitedFinancialInputError(
            f"快照声明的来源版本在库里查不到对应行：{missing}（账实不符，本 run 拒绝）")
    no_set = [v for v in ids if v not in sets_by_version]
    if no_set:
        raise CitedFinancialInputError(
            f"来源版本没有对应的 record set（期间/口径/币种无从核对）：{no_set}")

    out: list[DeclaredFinancialSource] = []
    for version_id in ids:
        row = by_version[version_id]
        rs = sets_by_version[version_id]
        out.append(DeclaredFinancialSource(
            source_version=str(row["source_version"]),
            source_document_id=str(row["source_document_id"]),
            source_name=Path(str(row["source_name"] or row["source_version"])).name,
            source_class=str(row["source_class"] or ""),
            file_sha256=str(row["file_sha256"]).lower(),
            file_type=str(row["file_type"] or ""),
            size_bytes=int(row["file_size"]),
            record_set_version=str(rs["record_set_version"]),
            report_periods=_json_list(rs["report_periods"]),
            statement_scope=str(rs["statement_scope"] or ""),
            currency=str(rs["currency"] or ""), unit=str(rs["unit"] or "")))
    return tuple(sorted(out, key=lambda s: s.source_version))


# ------------------------------------------------------------------ 建立


def stage_financial_input(uploads: Sequence[tuple[str, bytes]], target_dir: str | Path, *,
                          declared: Sequence[DeclaredFinancialSource],
                          snapshot: SnapshotIdentity,
                          run_id: str) -> FinancialInputBinding:
    """把这次上传的三份 XLSX 落到本 run 的运行输入目录里（**与 PDF 同一个目录**）。

    :param uploads: ``[(上传时的文件名, 原始字节), …]``，取自浏览器控件的当次取值。
    :param target_dir: 本 run 的运行输入目录。它**应当已经**由 `cri-1` 的 PDF 落盘创建；
        本函数不建这个目录，也不接受"绑定文件已存在"。
    :param declared: 当前有效快照声明的三条来源版本。
    :returns: 落盘并**重读复核过**的绑定。

    拒绝面（全部具名，不软失败）：声明为空、绑定已存在、目标目录不可达、上传里有重复字节、
    声明里有重复哈希或重复 source_version、缺一份、多一份、任一哈希不在声明里。
    """
    declared = tuple(declared)
    if not declared:
        raise CitedFinancialInputError(
            "当前有效快照没有可声明的财务来源：本 run 不建立空输入")
    target = Path(target_dir)
    if not target.is_dir():
        raise CitedFinancialInputError(
            f"运行输入目录不可达：{target}（财务输入与 PDF 落在同一目录，先建目录）")
    binding_path = target / BINDING_FILENAME
    if binding_path.exists():
        raise CitedFinancialInputError(f"财务输入绑定已存在，拒绝覆盖：{binding_path}")

    uploaded: list[tuple[str, bytes]] = [(str(name), bytes(data)) for name, data in uploads]
    digests = [CRI.sha256_bytes(data) for _, data in uploaded]

    by_hash: dict[str, tuple[str, bytes, int]] = {}
    for order, ((name, data), digest) in enumerate(zip(uploaded, digests)):
        if digest in by_hash:
            raise CitedFinancialInputError(
                f"上传里有两份**内容相同**的财务文件（sha256 {digest[:16]}…）："
                f"{by_hash[digest][0]!r} 与 {name!r}。本 run 拒绝「重复」，"
                "也不猜哪一份算数")
        by_hash[digest] = (name, data, order)

    wanted: dict[str, DeclaredFinancialSource] = {}
    versions: set[str] = set()
    for source in declared:
        key = source.file_sha256.lower()
        if key in wanted:
            raise CitedFinancialInputError(
                f"快照声明里有两份 sha256 相同的来源（{key[:16]}…）："
                f"{wanted[key].source_version!r} 与 {source.source_version!r}；"
                "字节相同就无法按哈希判定哪一份是哪一条来源，本 run 拒绝")
        if source.source_version in versions:
            raise CitedFinancialInputError(
                f"快照声明里 source_version 重复：{source.source_version!r}")
        wanted[key] = source
        versions.add(source.source_version)

    missing = tuple(s.source_version for s in declared
                    if s.file_sha256.lower() not in by_hash)
    unexpected = tuple(name for (name, _), digest in zip(uploaded, digests)
                       if digest not in wanted)
    if missing or unexpected:
        parts = []
        if missing:
            parts.append("缺少快照声明的财务来源：" + "、".join(missing))
        if unexpected:
            parts.append("上传了快照来源之外的文件：" + "、".join(unexpected))
        raise CitedFinancialInputError(
            "财务输入与当前有效快照的来源集不符——" + "；".join(parts)
            + "。本 run 在第一个请求之前拒绝，也不回退到 data/samples 或旧快照")

    objects_dir = target / OBJECTS_DIRNAME
    objects_dir.mkdir(exist_ok=False)

    staged: list[StagedFinancialSource] = []
    for source in declared:
        key = source.file_sha256.lower()
        name, data, order = by_hash[key]
        relpath = f"{OBJECTS_DIRNAME}/{key}.xlsx"
        with open(target / relpath, "wb") as fh:
            fh.write(data)
        staged.append(StagedFinancialSource(
            source_version=source.source_version,
            source_document_id=source.source_document_id,
            declared_sha256=key, declared_filename=source.source_name,
            uploaded_name=name, size_bytes=len(data),
            object_relpath=relpath, upload_order=order,
            source_role=FINANCIAL_SOURCE_ROLE))

    binding = FinancialInputBinding(
        financial_input_version=FINANCIAL_INPUT_VERSION, run_id=str(run_id),
        created_at_utc=_utc_now(), binding_sha256="", snapshot=snapshot,
        sources=tuple(staged))
    binding = replace(binding, binding_sha256=binding.canonical_fingerprint())
    binding_path.write_text(
        json.dumps(binding.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    #: 落盘之后立刻重读逐字节复核。写成功 != 写对了。
    return load_financial_input(target, declared=declared, snapshot=snapshot)


# ------------------------------------------------------------------ 读回


def _resolve_object(root: Path, relpath: str) -> Path:
    rel = Path(relpath)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        raise CitedFinancialInputError(f"财务输入对象路径非法：{relpath!r}")
    try:
        path = (root / rel).resolve(strict=False)
        path.relative_to(root.resolve(strict=False))
    except (OSError, ValueError) as exc:
        raise CitedFinancialInputError(f"财务输入对象越界：{relpath!r}") from exc
    return path


def load_financial_input(target_dir: str | Path, *,
                         declared: Sequence[DeclaredFinancialSource] | None = None,
                         snapshot: SnapshotIdentity | None = None
                         ) -> FinancialInputBinding:
    """读回一份财务输入绑定，并**逐字节重验**每份对象。

    `declared`／`snapshot` 给出时，还要求清单里的 `source_version → sha256` 与快照身份与它们
    **逐项相等**——「上传的三份」必须就是「当前快照的三份」，不是其中一部分，也不是另一期的。
    """
    target = Path(target_dir)
    if not target.is_dir():
        raise CitedFinancialInputError(f"运行输入目录不可达：{target}")
    path = target / BINDING_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CitedFinancialInputError(f"财务输入绑定读不到或不可解码：{path}") from exc
    binding = FinancialInputBinding.from_dict(raw)
    if binding.financial_input_version != FINANCIAL_INPUT_VERSION:
        raise CitedFinancialInputError(
            f"财务输入绑定的版本 {binding.financial_input_version!r} 不是本实现支持的 "
            f"{FINANCIAL_INPUT_VERSION!r}：本实现不隐式兼容其它版本")
    if binding.binding_sha256 != binding.canonical_fingerprint():
        raise CitedFinancialInputError(
            "财务输入绑定的自身指纹不符：绑定在落盘之后被改写过，本 run 拒绝使用它")

    seen_versions: set[str] = set()
    seen_hashes: set[str] = set()
    for obj in binding.sources:
        if obj.source_version in seen_versions:
            raise CitedFinancialInputError(
                f"财务输入绑定里 source_version 重复：{obj.source_version!r}")
        if obj.declared_sha256 in seen_hashes:
            raise CitedFinancialInputError(
                f"财务输入绑定里 sha256 重复：{obj.declared_sha256[:16]}…")
        if obj.source_role != FINANCIAL_SOURCE_ROLE:
            raise CitedFinancialInputError(
                f"财务输入对象的来源角色是 {obj.source_role!r}，不是 {FINANCIAL_SOURCE_ROLE!r}："
                "来源角色是这份绑定的一部分，不接受被改过的值")
        seen_versions.add(obj.source_version)
        seen_hashes.add(obj.declared_sha256)
        blob_path = _resolve_object(target, obj.object_relpath)
        if not blob_path.is_file():
            raise CitedFinancialInputError(
                f"财务输入对象已丢失（{obj.source_version}）：{obj.object_relpath}")
        blob = blob_path.read_bytes()
        if len(blob) != obj.size_bytes:
            raise CitedFinancialInputError(
                f"财务输入对象 {obj.source_version} 的字节数已变化："
                f"绑定 {obj.size_bytes} / 实读 {len(blob)}")
        actual = CRI.sha256_bytes(blob)
        if actual != obj.declared_sha256:
            raise CitedFinancialInputError(
                f"财务输入对象 {obj.source_version} 的字节已变化："
                f"绑定 {obj.declared_sha256} / 实读 {actual}（fail-closed）")

    if declared is not None:
        want = {s.source_version: s.file_sha256.lower() for s in declared}
        got = binding.declared_hashes()
        if want != got:
            raise CitedFinancialInputError(
                "财务输入绑定与当前快照的来源集不是同一组材料："
                f"快照 {sorted(want)} / 绑定 {sorted(got)}；"
                "哈希或成员任一处不同都拒绝（本 run 不「按文件名对上就算」）")
    if snapshot is not None and binding.snapshot.identity_body() != snapshot.identity_body():
        raise CitedFinancialInputError(
            "财务输入绑定里的快照身份与当前有效快照不同："
            f"绑定 {binding.snapshot.snapshot_id!r} / 当前 {snapshot.snapshot_id!r}。"
            "快照在绑定之后发生过漂移，本 run 拒绝以旧快照继续")
    return binding


def verify_snapshot_unchanged(fin_db: str | Path, *, binding: FinancialInputBinding
                              ) -> SnapshotIdentity:
    """装配期**再核一次**快照没有漂移。返回当前身份供运行目录留痕。"""
    current = current_snapshot_identity(
        fin_db, subject=binding.snapshot.company_id,
        subject_name=binding.snapshot.company_name,
        scope=binding.snapshot.scope, currency=binding.snapshot.currency,
        purpose=binding.snapshot.purpose, snapshot_as_of=binding.snapshot.as_of_date)
    if current.identity_body() != binding.snapshot.identity_body():
        raise CitedFinancialInputError(
            f"财务快照在本 run 装配期间发生漂移：绑定 {binding.snapshot.snapshot_id!r}"
            f"（{binding.snapshot.as_of_date}）／现在 {current.snapshot_id!r}"
            f"（{current.as_of_date}）。本 run 拒绝以漂移前的快照继续")
    return current


# ------------------------------------------------------------------ 解析


class FinancialInputResolver:
    """把 `(source_version, sha256)` 解析成本 run **自己那份上传对象**的路径。

    **没有任何回退分支**：命中不了就是失败，不会去 `data/samples/300750/financial/` 找一份
    同名文件，也不会退到 `financial_v2.db` 里登记的那份工作簿。
    """

    def __init__(self, root: str | Path, binding: FinancialInputBinding) -> None:
        self.root = Path(root)
        self.binding = binding
        index: dict[tuple[str, str], list[StagedFinancialSource]] = {}
        for obj in binding.sources:
            index.setdefault((obj.source_version, obj.declared_sha256), []).append(obj)
        self._index = index

    @classmethod
    def from_dir(cls, target_dir: str | Path, *,
                 declared: Sequence[DeclaredFinancialSource] | None = None,
                 snapshot: SnapshotIdentity | None = None) -> "FinancialInputResolver":
        target = Path(target_dir)
        return cls(target, load_financial_input(target, declared=declared, snapshot=snapshot))

    def resolve(self, *, source_version: str, file_sha256: str) -> Path:
        """定位并**当场重算哈希**。命中不唯一、对象丢失或字节变化，全部抛错。"""
        key = (str(source_version), str(file_sha256).lower())
        hits = self._index.get(key, [])
        if not hits:
            uploaded = [o.declared_sha256 for o in self.binding.sources
                        if o.source_version == key[0]]
            hint = (f"（本 run 上传的 {key[0]} 哈希是 "
                    + "、".join(h[:16] + "…" for h in uploaded) + "）") if uploaded else \
                   f"（本 run 的上传里没有来源 {key[0]!r}）"
            raise CitedFinancialInputError(
                f"本 run 的财务上传输入里没有 {key[0]} 的 sha256 {key[1][:16]}… 对象{hint}："
                "拒绝以 data/samples 或库里登记的工作簿顶替（fail-closed）")
        if len(hits) != 1:
            raise CitedFinancialInputError(
                f"本 run 的财务上传输入里 {key[0]} 的对象不唯一（{len(hits)} 份）："
                "不「就近取一份」")
        obj = hits[0]
        path = _resolve_object(self.root, obj.object_relpath)
        if not path.is_file():
            raise CitedFinancialInputError(
                f"本 run 的财务上传对象在运行期间丢失（{obj.source_version}）："
                f"{obj.object_relpath}")
        actual = CRI.sha256_file(path)
        if actual != obj.declared_sha256:
            raise CitedFinancialInputError(
                f"本 run 的财务上传对象在运行期间字节变化（{obj.source_version}）："
                f"绑定 {obj.declared_sha256} / 实读 {actual}（fail-closed）")
        return path

    def owns(self, path: str | Path) -> bool:
        """这个路径是不是落在本 run 的输入目录内。"""
        try:
            Path(path).resolve(strict=False).relative_to(self.root.resolve(strict=False))
        except (OSError, ValueError):
            return False
        return True


# ------------------------------------------------------------------ 自检

def _self_check(argv: Sequence[str] | None = None) -> int:
    """只读自检：打印当前有效快照身份与三条来源版本。**不写任何库、不建任何目录。**"""
    import argparse

    parser = argparse.ArgumentParser(description="cfi-1 只读自检：当前有效财务快照与其来源")
    parser.add_argument("--db", default="data/financial_v2.db")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--subject-name", default="")
    parser.add_argument("--scope", default=None)
    parser.add_argument("--currency", default=None)
    parser.add_argument("--purpose", default=None)
    parser.add_argument("--snapshot-as-of", default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    names = registered_subject_names(args.db, subject=args.subject)
    if not str(args.subject_name or "").strip():
        #: 没声明名称时只回答「库里登记了什么」——这正是页面在让操作者声明之前要看的读数。
        print(f"主体       {args.subject}")
        print(f"登记名称   {list(names) if names else '（库里没有该主体的登记名称）'}")
        return 0

    snapshot = current_snapshot_identity(
        args.db, subject=args.subject, subject_name=args.subject_name,
        scope=args.scope, currency=args.currency, purpose=args.purpose,
        snapshot_as_of=args.snapshot_as_of)
    sources = declared_financial_sources(
        args.db, version_ids=[v for v, _ in snapshot.source_versions])
    print(f"快照       {snapshot.snapshot_id}")
    print(f"主体       {snapshot.company_id}／{snapshot.company_name}")
    print(f"期末/口径  {snapshot.as_of_date}／{snapshot.scope}／{snapshot.currency}"
          f"／{snapshot.purpose}／{snapshot.validity}")
    print(f"来源       {len(sources)} 份")
    for source in sources:
        print(f"  {source.source_version}  {source.file_sha256}")
        print(f"    {source.source_name}（{source.source_class}，"
              f"{source.statement_scope}，{source.currency}，{source.unit}）")
        print(f"    期间 {list(source.report_periods)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - 自检入口
    raise SystemExit(_self_check())

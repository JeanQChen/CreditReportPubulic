"""TS4 受信只读 Evidence authority（计划 §18.3.2 / §18.11.1 / §18.14.2-4）。

本模块是 TS4 正式链路**唯一**可以取得"本文档当前证据集"的地方。它解决的是一个
很具体的问题：`aligner` 的 `EvidenceSetSnapshot` 能证明"这份成员清单自洽"，但证明
不了"它就是库里 status='current' 的那一份"。任何调用方都能自己拼一份数学上自洽的
snapshot 塞进对齐入口。因此权威性不能来自对象字段，只能来自**签发**：

- `bind_current_evidence_authority()` **无参数**，只读取 `sections.service._prepare_stores`
  已经预检绑定的 `evidence.store._db_path`；它**不** init、**不** migrate、**不**改
  模块级 `_db_path`。生产 API 里根本不存在 `db_path` 形参，所以"给官方 gateway 换一个
  库"这条路在签名层面就不存在；
- 读数据**只**经 `evidence.store` 的 `?mode=ro + query_only` 只读原语
  （`current_evidence_set_ro` / `list_document_evidence_ro`），并额外自证连接确实是
  只读（`PRAGMA query_only == 1` 且探针写入必须抛错）；
- 每次取数都按**签发时登记的数据库身份**重开：resolved path / size / mtime_ns /
  sha256 四项任一变化即 fail-closed。因此"换掉库文件"或"运行中改库"都会被拒；
- 缺库 / 未绑定 / 无 current set / 空集 / 类型越出 `EVIDENCE_TYPES` 一律 fail-closed，
  不降级、不猜、不用调用方参数补齐身份。

两个签发域（§18.3.2）：`live`（生产 service 预检后的当前库）与
`pinned_acceptance`。后者有两个不导出的 issuer：

- `_issue_pinned_evidence_authority`：`source_kind=historical_run`，固定读工作区正式
  `data/evidence.db`；
- `_issue_fixture_evidence_authority`：`source_kind=versioned_fixture`，读**仓库内**
  版本化夹具的 Evidence 成员文件，逐成员核验 sha256 后才取数，**不碰任何 `data/*.db`**
  （§18.14.2-9）。它仍属 `pinned_acceptance` 域，因此夹具正向验收**不是** testing 域。

`testing` 域**不**在本模块出现——夹具正向验收必须走 `pinned_acceptance`，不得用
testing 冒充真实验收。
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from document_structure import versions as V
from document_structure.aligner import (
    EVIDENCE_TYPE_CLOSED_SET,
    EvidenceBlockInput,
    EvidenceSetMember,
    EvidenceSetSnapshot,
    snapshot_fingerprint,
)
from document_structure.canonical import (
    SchemaValidationError,
    sha256_canonical,
)
from document_structure.span_schema import _issue_capability

__all__ = [
    "DEFAULT_EVIDENCE_DB_PATH", "READONLY_PRIMITIVES", "CURRENT_SET_QUERY_CONTRACT",
    "FIXTURE_SOURCE_KIND", "EVIDENCE_AUTHORITY_SOURCE_KINDS",
    "EvidenceGatewayError", "VerifiedCurrentEvidenceAuthority",
    "bind_current_evidence_authority", "parse_structured_payload",
    "collect_document_rows", "readonly_primitive_identity",
    "current_set_query_contract_identity", "db_file_identity",
    "evidence_authority_identity", "member_identity_sha256", "self_check",
]


class EvidenceGatewayError(SchemaValidationError):
    """受信只读 Evidence 访问失败（缺库 / 未绑定 / 无 current set / 身份不符）。"""


# ---------------------------------------------------------------------------
# 1. 固定路径与固定只读原语（**不接受**调用者传入路径）
# ---------------------------------------------------------------------------

#: 工作区正式 Evidence 库的**绝对**路径。必须是绝对路径：`pinned_acceptance` 是"根锁
#: 固定路径"的签发域，若写成相对路径（`Path("data/evidence.db")`），它会绑定到**进程
#: CWD** 解析出来的那个文件——从另一个目录发起验收就会悄悄绑到另一个库（或不存在）。
#: 仓库根由本文件位置唯一确定，因此与调用者的 CWD 无关。
REPO_ROOT: Path = Path(__file__).resolve().parent.parent

DEFAULT_EVIDENCE_DB_PATH: Path = REPO_ROOT / "data" / "evidence.db"

#: 版本化正向夹具的 `source_kind`（§18.14.2-9）。它**不是**第四个签发域：夹具仍属
#: `pinned_acceptance`，只是取数来源是仓库内的版本化成员文件而不是工作区正式库。
FIXTURE_SOURCE_KIND = "versioned_fixture"

#: Evidence authority 允许的 `source_kind` 封闭集合。名单进入签发身份，因此"多一个
#: 来源"必然改变 authority fingerprint，不可能悄悄接上第二条取数路径。
EVIDENCE_AUTHORITY_SOURCE_KINDS: tuple[str, ...] = (
    "current_store", "historical_run", FIXTURE_SOURCE_KIND)

#: 正式 gateway **只**允许调用这两个 `?mode=ro + query_only` 原语。名字进入签发身份，
#: 因此"改用另一个读法"必然改变 authority fingerprint，不可能悄悄替换。
READONLY_PRIMITIVES: tuple[str, ...] = (
    "current_evidence_set_ro",
    "list_document_evidence_ro",
)

#: 读取契约的**唯一**文本：current 集合由 `evidence_sets.status='current'` 唯一确定，
#: 成员由 `evidence_blocks.evidence_set_version` 精确相等确定。它进入签发身份，同样
#: 不可悄悄放宽（例如改成"取最新一个 set"就会改指纹）。
CURRENT_SET_QUERY_CONTRACT: str = (
    "evidence_sets.status='current' AND evidence_sets.company_id=? "
    "AND evidence_sets.document_id=? AND evidence_sets.document_version=? "
    "AND evidence_blocks.evidence_set_version=evidence_sets.evidence_set_version"
)


def readonly_primitive_identity() -> str:
    """只读原语身份的规范指纹（**源文件内容 + 原语名 + 读取契约**，不写版本字面量）。"""
    return sha256_canonical({
        "primitive_names": list(READONLY_PRIMITIVES),
        "query_contract": CURRENT_SET_QUERY_CONTRACT,
        "store_source_sha256": _store_source_sha256(),
    })


def current_set_query_contract_identity() -> str:
    """读取契约身份的规范指纹（与只读原语分开登记，各自可独立复核）。"""
    return sha256_canonical({
        "query_contract": CURRENT_SET_QUERY_CONTRACT,
        "primitive_names": list(READONLY_PRIMITIVES),
    })


def _store_source_sha256() -> str:
    """`evidence/store.py` 的源文件 sha256（正式 gateway 使用的唯一读库实现）。"""
    from evidence import store as estore
    p = Path(estore.__file__).resolve()
    if not p.is_file():
        raise EvidenceGatewayError(f"只读原语实现不可定位（fail-closed）：{p}")
    return _sha256_file(p)


def _sha256_file(path: Path) -> str:
    from evidence.ids import file_sha256
    return file_sha256(str(path))


def db_file_identity(path: Path) -> dict:
    """数据库文件的**可重算身份**：解析后路径 + size + mtime_ns + sha256。"""
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise EvidenceGatewayError(
            f"Evidence 库不存在（fail-closed）：{p}；不得以空集/缺库继续，"
            f"也不得 init 一个空库冒充权威来源")
    st = p.stat()
    return {
        "resolved_path": str(p),
        "size": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
        "sha256": _sha256_file(p),
    }


def evidence_authority_identity(*, scope: str, source_kind: str,
                                resolved_db_identity: dict,
                                trust_root_file_sha256: str | None = None) -> str:
    """§18.3.5 的 Evidence authority fingerprint（canonical payload，由本模块重算）。"""
    if scope not in ("live", "pinned_acceptance"):
        raise EvidenceGatewayError(
            f"Evidence authority 的签发域必须为 live 或 pinned_acceptance，得到 {scope!r}")
    if source_kind not in EVIDENCE_AUTHORITY_SOURCE_KINDS:
        raise EvidenceGatewayError(
            f"source_kind 必须为 {EVIDENCE_AUTHORITY_SOURCE_KINDS} 之一，"
            f"得到 {source_kind!r}")
    if scope == "live" and source_kind != "current_store":
        raise EvidenceGatewayError(
            f"live 域的 source_kind 只能为 current_store，得到 {source_kind!r}；"
            f"夹具 / 历史产物不得出现在生产链上（fail-closed）")
    if source_kind == FIXTURE_SOURCE_KIND and scope != "pinned_acceptance":
        raise EvidenceGatewayError(
            f"版本化夹具只属于 pinned_acceptance 域，得到 {scope!r}")
    for name in ("resolved_path", "size", "mtime_ns", "sha256"):
        if name not in resolved_db_identity:
            raise EvidenceGatewayError(f"resolved_db_identity 缺字段 {name!r}")
    if scope == "live":
        issver = V.VERIFIED_CURRENT_EVIDENCE_AUTHORITY_VERSION
    elif source_kind == FIXTURE_SOURCE_KIND:
        issver = V.FIXTURE_EVIDENCE_AUTHORITY_VERSION
    else:
        issver = V.PINNED_EVIDENCE_AUTHORITY_VERSION
    return sha256_canonical({
        "provider_version": V.EVIDENCE_GATEWAY_PROVIDER_VERSION,
        "scope": scope,
        "source_kind": source_kind,
        "issuer_version": issver,
        "resolved_db_identity": dict(resolved_db_identity),
        "trust_root_file_sha256": trust_root_file_sha256,
        "readonly_primitive_identity": readonly_primitive_identity(),
        "current_set_query_contract_identity": current_set_query_contract_identity(),
    })


# ---------------------------------------------------------------------------
# 2. 只读访问（mode=ro + query_only，并**自证**只读）
# ---------------------------------------------------------------------------

def _open_registered_readonly(db_path: Path) -> sqlite3.Connection:
    """按已登记路径严格只读打开，并自证 `mode=ro + query_only` 成立。

    `evidence.store._open_readonly_conn` 已经用 `?mode=ro` + `PRAGMA query_only` 打开；
    这里再做一次**行为级**自证：`query_only` 必须为 1，且一次探针写入必须抛
    `sqlite3.OperationalError`。只检查 PRAGMA 返回值不足以证明写入真的会被拒。
    """
    from evidence import store as estore
    conn = estore._open_readonly_conn(db_path)
    if conn is None:
        raise EvidenceGatewayError(
            f"只读打开失败（fail-closed）：{db_path}；缺库不得继续")
    try:
        row = conn.execute("PRAGMA query_only").fetchone()
        if row is None or int(row[0]) != 1:
            raise EvidenceGatewayError(
                f"只读连接的 PRAGMA query_only 不为 1（fail-closed）：{db_path}")
        try:
            conn.execute("CREATE TABLE __ts4_write_probe__(x)")
        except sqlite3.OperationalError:
            pass
        else:
            raise EvidenceGatewayError(
                f"只读连接竟允许写入（fail-closed）：{db_path}；"
                f"『只读』不成立即不得作为权威来源")
    except Exception:
        conn.close()
        raise
    return conn


def parse_structured_payload(raw: Any) -> dict | None:
    """`structured_payload` 列 -> dict|None；**不是**对象（数组/标量）即 fail-closed。

    空串与 `None` 是"没有载荷"（现行列语义）；非对象 JSON 是拿错列或数据被篡改，
    必须拒绝。"降级成 None"等于用一个自洽的假身份替换真实身份。
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        raise EvidenceGatewayError(
            f"structured_payload 必须为 str / dict / None，得到 "
            f"{type(raw).__name__}（fail-closed）")
    import json
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise EvidenceGatewayError(f"structured_payload 不是合法 JSON：{e}") from e
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise EvidenceGatewayError(
            f"structured_payload 必须是 JSON 对象或 null，得到 "
            f"{type(payload).__name__}（fail-closed）")
    return payload


def _authoritative_identity(row: dict, name: str, expected: str,
                            what: str) -> str:
    """从**权威行本身**取身份字段；缺失 / 空 / 与查询键不符一律 fail-closed。

    `row.get(name) or caller_value` 会在权威行缺字段时静默补上调用方参数，于是
    "这次对齐的是哪份文档/哪个版本"就由调用方声明决定，而不是由证据来源决定。
    """
    value = row.get(name)
    if not isinstance(value, str) or value.strip() == "":
        raise EvidenceGatewayError(
            f"{what} 的权威 Evidence 行的 {name} 缺失或为空（得到 {value!r}，"
            f"document_id={row.get('document_id')!r} / page={row.get('page_number')!r} / "
            f"block={row.get('block_index')!r}）；身份字段不得由调用方参数补齐"
            f"（fail-closed）")
    if value != expected:
        raise EvidenceGatewayError(
            f"{what} 的权威 Evidence 行的 {name}={value!r} 与查询键 {expected!r} 不一致；"
            f"不得用另一份文档 / 版本的行冒充本次对齐输入（fail-closed）")
    return value


def _block_input_from_row(company_id: str, document_id: str, version: str,
                          row: dict, what: str) -> EvidenceBlockInput:
    """把一行 Evidence 记录变成**完整身份**的生产输入对象（失败即 fail-closed）。"""
    block = EvidenceBlockInput(
        company_id=_authoritative_identity(row, "company_id", company_id, what),
        document_id=_authoritative_identity(row, "document_id", document_id, what),
        document_version=_authoritative_identity(
            row, "document_version", version, what),
        evidence_set_version=row["evidence_set_version"],
        evidence_block_id=row["evidence_id"],
        content_hash=row["content_hash"],
        evidence_type=row["evidence_type"],
        page_number=row["page_number"],
        block_index=row["block_index"],
        text=row["text"] or "",
        structured_payload=parse_structured_payload(row.get("structured_payload")))
    return block


def _load_snapshot_and_blocks(db_path: Path, resolved_db_identity: dict, *,
                              scope: str, source_kind: str,
                              company_id: str, document_id: str,
                              document_version: str) -> tuple:
    """按**已登记数据库身份**重开只读连接，取 current 快照与全量输入块。

    重开时逐项复核 size / mtime_ns / sha256：只要库文件在签发之后被替换或改动
    （包括"官方 gateway 配替代 DB"），这里就 fail-closed，而不是照读一份新库。
    """
    from evidence import store as estore
    current = db_file_identity(db_path)
    for name in ("resolved_path", "size", "mtime_ns", "sha256"):
        if current[name] != resolved_db_identity[name]:
            raise EvidenceGatewayError(
                f"Evidence 库身份在签发之后发生变化：{name} "
                f"{resolved_db_identity[name]!r} -> {current[name]!r}；"
                f"不得对未登记的库取数（fail-closed）")
    what = f"{scope}/{source_kind}"
    set_version = estore.current_evidence_set_ro(
        db_path, company_id, document_id, document_version)
    if not set_version:
        raise EvidenceGatewayError(
            f"[{what}] {document_id} 没有 status='current' 的 evidence set；"
            f"不得用调用方提交的清单冒充权威集合（fail-closed）")
    blocks = list(estore.list_document_evidence_ro(
        db_path, company_id, document_id, document_version, set_version))
    if not blocks:
        raise EvidenceGatewayError(
            f"[{what}] {document_id} 的 current evidence set {set_version!r} 里"
            f"一个成员都没有；空集合不得被表述为对齐输入（fail-closed）")
    members = []
    inputs = []
    for b in blocks:
        if b.evidence_type not in EVIDENCE_TYPE_CLOSED_SET:
            raise EvidenceGatewayError(
                f"[{what}] evidence_type {b.evidence_type!r} 越出正式封闭集合 "
                f"{sorted(EVIDENCE_TYPE_CLOSED_SET)}（fail-closed）")
        members.append(EvidenceSetMember(
            evidence_id=b.evidence_id, content_hash=b.content_hash,
            evidence_type=b.evidence_type, page_number=b.page_number,
            block_index=b.block_index))
        inputs.append(_block_input_from_row(
            company_id, document_id, document_version, {
                "company_id": b.company_id, "document_id": b.document_id,
                "document_version": b.document_version,
                "evidence_set_version": b.evidence_set_version,
                "evidence_id": b.evidence_id, "content_hash": b.content_hash,
                "evidence_type": b.evidence_type, "page_number": b.page_number,
                "block_index": b.block_index, "text": b.text,
                "structured_payload": (None if b.structured_payload is None
                                       else b.structured_payload),
            }, what))
    ordered = tuple(sorted(members, key=lambda m: m.sort_key))
    snapshot = EvidenceSetSnapshot(
        company_id=company_id, document_id=document_id,
        document_version=document_version, evidence_set_version=set_version,
        status="current", members=ordered, block_count=len(ordered),
        fingerprint=snapshot_fingerprint(
            company_id=company_id, document_id=document_id,
            document_version=document_version,
            evidence_set_version=set_version, status="current",
            gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION, members=ordered),
        gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION)
    snapshot.assert_current()
    by_key = {(b.page_number, b.block_index): b for b in inputs}
    if len(by_key) != len(inputs):
        raise EvidenceGatewayError(
            f"[{what}] 存在重复的 (page_number, block_index)（fail-closed）")
    ordered_inputs = tuple(by_key[m.sort_key] for m in ordered)
    return snapshot, ordered_inputs


# ---------------------------------------------------------------------------
# 2b. 版本化正向夹具的只读取数（§18.14.2-9）
# ---------------------------------------------------------------------------
#
# 夹具**不写** `data/*.db`：它的 Evidence 成员是仓库内的版本化文件。取数路径因此不
# 经过 sqlite，但信任边界不打折：每个成员文件的 sha256 都由 manifest 钉死并在读取
# 时逐文件重算，成员身份由 `EvidenceBlockInput` 自己重算，集合身份再由
# `member_identity_sha256` 对回 manifest。固定目录来自本模块自身位置，**没有**任何
# 调用者路径参数。

FIXTURE_ROOT_RELPATH = "evals/fixtures/tree_structure/non_300750_ts4"
FIXTURE_MANIFEST_FILENAME = "manifest.json"
FIXTURE_MEMBER_ROLES: tuple[str, ...] = (
    "page_layout", "document_outline", "evidence_set")


def fixture_root_dir() -> Path:
    """夹具目录的**固定**位置（由本模块文件位置派生，不接受调用者路径）。"""
    return Path(__file__).resolve().parent.parent / FIXTURE_ROOT_RELPATH


def member_identity_sha256(members) -> str:
    """权威成员**规范身份清单**的 sha256（与 TS3 收口产物的算法一致）。

    `EvidenceSetMember.identity` 是五元组；按投影成 JSON 数组后不做任何美化、
    不加空格，因此同一份成员清单在任何进程里都得到同一个值。
    """
    import json
    payload = json.dumps([m.identity for m in members], ensure_ascii=False,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_fixture_root() -> dict:
    """读取固定目录的夹具 manifest，并把**它自己的文件 sha256** 绑进返回值。

    manifest 是 `CODE_FINGERPRINT_FILES` 的成员：它既是数据也是信任锚，因此它不
    自证——调用方（runner）另有仓库内的根 fixture 钉住它。
    """
    import json
    path = fixture_root_dir() / FIXTURE_MANIFEST_FILENAME
    if not path.is_file():
        raise EvidenceGatewayError(f"版本化正向夹具 manifest 不存在：{path}")
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise EvidenceGatewayError(f"夹具 manifest 不是合法 UTF-8 JSON：{path}（{e}）") from e
    if not isinstance(data, dict):
        raise EvidenceGatewayError("夹具 manifest 顶层必须为对象")
    out = dict(data)
    out["fixture_file_sha256"] = hashlib.sha256(raw).hexdigest()
    out["fixture_dir_relpath"] = FIXTURE_ROOT_RELPATH
    return out


def _fixture_file_identity(fixture_root: dict, relpath: str,
                           what: str) -> tuple[Path, dict]:
    """按 manifest 钉住的 sha256 / size 核验一个夹具成员文件，返回其文件身份。"""
    if not isinstance(relpath, str) or relpath == "":
        raise EvidenceGatewayError(f"{what} 的 relpath 必须为非空字符串")
    if "/" in relpath or "\\" in relpath or relpath in (".", ".."):
        raise EvidenceGatewayError(
            f"{what} 的 relpath 必须是夹具目录内的同目录文件名，得到 {relpath!r}")
    pinned = {m["relpath"]: m for m in fixture_root["members"]}
    entry = pinned.get(relpath)
    if entry is None:
        raise EvidenceGatewayError(
            f"{what} 的文件 {relpath!r} 未列入夹具 manifest 的 members；"
            f"未固定的字节不得进入正式链路（fail-closed）")
    path = fixture_root_dir() / relpath
    if not path.is_file():
        raise EvidenceGatewayError(f"{what} 的成员文件缺失：{path}（fail-closed）")
    actual = {"resolved_path": str(path), "size": int(path.stat().st_size),
              "sha256": _sha256_file(path)}
    for name in ("size", "sha256"):
        if actual[name] != entry[name]:
            raise EvidenceGatewayError(
                f"{what} 的成员文件 {relpath!r} 与 manifest 钉住值不一致：{name} "
                f"{entry[name]!r} -> {actual[name]!r}（fail-closed）")
    return path, actual


def fixture_evidence_identity(fixture_root: dict) -> dict:
    """夹具 Evidence 来源的**文件级身份**（作为 authority 的 `resolved_db_identity`）。

    夹具没有数据库，但授权身份仍需四项可重算字段：这里它们是 manifest 文件的
    resolved path / size / mtime_ns / sha256。换掉 manifest 即换掉 authority。
    """
    path = fixture_root_dir() / FIXTURE_MANIFEST_FILENAME
    st = path.stat()
    locked = fixture_root.get("fixture_file_sha256")
    actual = _sha256_file(path)
    if not isinstance(locked, str) or locked != actual:
        raise EvidenceGatewayError(
            f"夹具 manifest 的 fixture_file_sha256 与文件字节不符：{locked!r} != "
            f"{actual!r}（fail-closed）")
    return {"resolved_path": str(path), "size": int(st.st_size),
            "mtime_ns": int(st.st_mtime_ns), "sha256": actual}


def _load_fixture_snapshot_and_blocks(fixture_root: dict, *, company_id: str,
                                      document_id: str,
                                      document_version: str) -> tuple:
    """从版本化夹具的 Evidence 成员文件构造 `(snapshot, blocks)`（只读，无 DB）。

    逐层核验：manifest 自载身份 → PDF 文件 → Evidence 成员文件 sha256 → 逐块身份
    （`EvidenceBlockInput` 自己重算）→ 集合身份（`member_identity_sha256` 对回
    manifest）。任一层不符即 fail-closed，绝不"按文件为准"地静默采用一份偏离声明
    的夹具。
    """
    import json
    what = f"pinned_acceptance/{FIXTURE_SOURCE_KIND}"
    if fixture_root.get("fixture_kind") != "ts4_non_300750_positive_v1":
        raise EvidenceGatewayError(
            f"夹具 fixture_kind 必须为 'ts4_non_300750_positive_v1'，得到 "
            f"{fixture_root.get('fixture_kind')!r}（fail-closed）")
    roles = [m.get("role") for m in fixture_root.get("members") or []]
    if sorted(roles) != sorted(FIXTURE_MEMBER_ROLES):
        raise EvidenceGatewayError(
            f"夹具 members 的角色必须恰为 {sorted(FIXTURE_MEMBER_ROLES)}，"
            f"得到 {sorted(r for r in roles if r is not None)}（fail-closed）")
    for name, expected in (("company_id", company_id), ("document_id", document_id),
                           ("document_version", document_version)):
        declared = fixture_root.get(name)
        if declared != expected:
            raise EvidenceGatewayError(
                f"夹具 manifest 的 {name}={declared!r} 与请求的 {expected!r} 不一致；"
                f"夹具不得跨公司 / 跨文档 / 跨版本复用（fail-closed）")
    _, evidence_identity = _fixture_file_identity(
        fixture_root, fixture_root.get("evidence_relpath") or "", "夹具 Evidence 成员")
    payload = json.loads(
        (fixture_root_dir() / fixture_root["evidence_relpath"]).read_text(
            encoding="utf-8"))
    if payload.get("schema_type") != "TS4FixtureEvidenceSet":
        raise EvidenceGatewayError(
            f"夹具 Evidence 成员文件的 schema_type 必须为 'TS4FixtureEvidenceSet'，"
            f"得到 {payload.get('schema_type')!r}（fail-closed）")
    set_version = payload.get("evidence_set_version")
    if set_version != fixture_root.get("evidence_set_version"):
        raise EvidenceGatewayError(
            f"夹具 Evidence 集合版本与 manifest 声明不一致：{set_version!r} != "
            f"{fixture_root.get('evidence_set_version')!r}（fail-closed）")
    raw_members = payload.get("members")
    if not isinstance(raw_members, list) or not raw_members:
        raise EvidenceGatewayError(
            f"[{what}] 夹具 Evidence 集合为空；空集合不得被表述为对齐输入"
            f"（fail-closed）")
    inputs: list = []
    for i, raw in enumerate(raw_members):
        if not isinstance(raw, dict):
            raise EvidenceGatewayError(f"夹具 Evidence 成员 [{i}] 必须为对象")
        try:
            block = EvidenceBlockInput(
                company_id=raw["company_id"], document_id=raw["document_id"],
                document_version=raw["document_version"],
                evidence_set_version=raw["evidence_set_version"],
                evidence_block_id=raw["evidence_id"],
                content_hash=raw["content_hash"], evidence_type=raw["evidence_type"],
                page_number=raw["page_number"], block_index=raw["block_index"],
                text=raw["text"] or "",
                structured_payload=raw.get("structured_payload"))
        except (KeyError, TypeError) as e:
            raise EvidenceGatewayError(
                f"夹具 Evidence 成员 [{i}] 字段缺失或类型不符：{e!r}（fail-closed）") from e
        for name, expected in (("company_id", company_id), ("document_id", document_id),
                               ("document_version", document_version),
                               ("evidence_set_version", set_version)):
            if getattr(block, name) != expected:
                raise EvidenceGatewayError(
                    f"[{what}] 夹具 Evidence 成员 [{i}] 的 {name}="
                    f"{getattr(block, name)!r} 与集合声明 {expected!r} 不一致"
                    f"（fail-closed）")
        if block.evidence_type not in EVIDENCE_TYPE_CLOSED_SET:
            raise EvidenceGatewayError(
                f"[{what}] evidence_type {block.evidence_type!r} 越出正式封闭集合 "
                f"{sorted(EVIDENCE_TYPE_CLOSED_SET)}（fail-closed）")
        inputs.append(block)
    by_key: dict = {}
    for block in inputs:
        key = (block.page_number, block.block_index)
        if key in by_key:
            raise EvidenceGatewayError(
                f"[{what}] 夹具存在重复的 (page_number, block_index)={key}（fail-closed）")
        by_key[key] = block
    ordered_inputs = tuple(by_key[k] for k in sorted(by_key))
    ordered_members = tuple(
        EvidenceSetMember(evidence_id=b.evidence_block_id, content_hash=b.content_hash,
                          evidence_type=b.evidence_type, page_number=b.page_number,
                          block_index=b.block_index) for b in ordered_inputs)
    declared_sha = fixture_root.get("member_identity_sha256")
    actual_sha = member_identity_sha256(ordered_members)
    if declared_sha != actual_sha:
        raise EvidenceGatewayError(
            f"[{what}] 夹具成员身份清单的 sha256 与 manifest 声明不一致："
            f"{declared_sha!r} != {actual_sha!r}（fail-closed）")
    snapshot = EvidenceSetSnapshot(
        company_id=company_id, document_id=document_id,
        document_version=document_version, evidence_set_version=set_version,
        status="current", members=ordered_members, block_count=len(ordered_members),
        fingerprint=snapshot_fingerprint(
            company_id=company_id, document_id=document_id,
            document_version=document_version, evidence_set_version=set_version,
            status="current", gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION,
            members=ordered_members),
        gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION)
    snapshot.assert_current()
    _ = evidence_identity
    return snapshot, ordered_inputs


def collect_document_rows(conn: sqlite3.Connection, db_path: Path, *,
                          company_id: str, document_id: str,
                          document_version: str) -> list:
    """按 `(page_number, block_index)` 读取某文档版本的全部 Evidence 行（只读）。

    只用于**诊断投影**（例如验收 runner 的冻结对账），不构成对齐输入；正式输入一律
    经 `VerifiedCurrentEvidenceAuthority.load_snapshot_and_blocks` 构造。
    """
    columns = ("company_id", "document_id", "document_version", "evidence_set_version",
               "evidence_id", "content_hash", "evidence_type", "page_number",
               "block_index", "text", "structured_payload")
    rows = conn.execute(
        "SELECT " + ", ".join(columns) + " FROM evidence_blocks "
        "WHERE company_id=? AND document_id=? AND document_version=? "
        "ORDER BY page_number, block_index",
        (company_id, document_id, document_version)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 3. 运行时 capability
# ---------------------------------------------------------------------------

class VerifiedCurrentEvidenceAuthority:
    """**已签发**的只读 Evidence 权威（运行时能力，不可序列化）。

    资格不来自字段，而来自"本进程签发登记表里有它"（§18.4.2）。因此本类没有公开
    构造器：实例只能由 `bind_current_evidence_authority()` 或验收专用
    `_issue_pinned_evidence_authority()` 产生，并且 `copy` / `deepcopy` / `pickle`
    与 `to_dict` 全部显式拒绝——把一个能力对象序列化出去再从磁盘读回，就不再是
    "本进程签发的那一个"。
    """

    #: `__weakref__` 必须在 slots 里，否则签发登记表无法持弱引用（能力将永远活着）。
    __slots__ = ("_scope", "_source_kind", "_resolved_db_identity",
                 "_trust_root_file_sha256", "_authority_fingerprint", "__weakref__")

    def __init__(self, *, scope: str, source_kind: str, resolved_db_identity: dict,
                 trust_root_file_sha256: str | None,
                 authority_fingerprint: str) -> None:
        self._scope = scope
        self._source_kind = source_kind
        self._resolved_db_identity = dict(resolved_db_identity)
        self._trust_root_file_sha256 = trust_root_file_sha256
        self._authority_fingerprint = authority_fingerprint

    # -- 只读身份 ---------------------------------------------------------

    @property
    def issuer_scope(self) -> str:
        return self._scope

    @property
    def source_kind(self) -> str:
        return self._source_kind

    @property
    def issuer_version(self) -> str:
        if self._scope == "live":
            return V.VERIFIED_CURRENT_EVIDENCE_AUTHORITY_VERSION
        if self._source_kind == FIXTURE_SOURCE_KIND:
            return V.FIXTURE_EVIDENCE_AUTHORITY_VERSION
        return V.PINNED_EVIDENCE_AUTHORITY_VERSION

    @property
    def provider_version(self) -> str:
        return V.EVIDENCE_GATEWAY_PROVIDER_VERSION

    @property
    def resolved_db_identity(self) -> dict:
        return dict(self._resolved_db_identity)

    @property
    def trust_root_file_sha256(self) -> str | None:
        return self._trust_root_file_sha256

    @property
    def authority_fingerprint(self) -> str:
        return self._authority_fingerprint

    def identity(self) -> dict:
        """进入 handoff / input fingerprint 的身份（**不含**任何 Evidence 正文）。"""
        return {
            "provider_version": self.provider_version,
            "scope": self._scope,
            "source_kind": self._source_kind,
            "issuer_version": self.issuer_version,
            "resolved_db_identity": dict(self._resolved_db_identity),
            "trust_root_file_sha256": self._trust_root_file_sha256,
            "readonly_primitive_identity": readonly_primitive_identity(),
            "current_set_query_contract_identity":
                current_set_query_contract_identity(),
            "authority_fingerprint": self._authority_fingerprint,
        }

    # -- 取数（每次都按登记身份重开）---------------------------------------

    def load_snapshot_and_blocks(self, *, company_id: str, document_id: str,
                                 document_version: str) -> tuple:
        """重开同一已登记库身份，返回 `(EvidenceSetSnapshot, blocks)`。

        连接**自证**为 `mode=ro + query_only`（见 `_open_registered_readonly`），
        且数据只经 `evidence.store` 的两个登记原语读取；数据库路径不来自任何参数。
        """
        for name, value in (("company_id", company_id), ("document_id", document_id),
                            ("document_version", document_version)):
            if not isinstance(value, str) or value == "":
                raise EvidenceGatewayError(f"{name} 必须为非空字符串，得到 {value!r}")
        if self._source_kind == FIXTURE_SOURCE_KIND:
            # 夹具取数走版本化成员文件；manifest 的路径固定，且签发后逐项复核它的
            # 四项文件身份，因此"换掉 manifest 再签发一次"不会与本次签发相等。
            fixture_root = load_fixture_root()
            current = fixture_evidence_identity(fixture_root)
            for name in ("resolved_path", "size", "mtime_ns", "sha256"):
                if current[name] != self._resolved_db_identity[name]:
                    raise EvidenceGatewayError(
                        f"夹具 manifest 的身份在签发之后发生变化：{name} "
                        f"{self._resolved_db_identity[name]!r} -> {current[name]!r}；"
                        f"不得对未登记的夹具取数（fail-closed）")
            return _load_fixture_snapshot_and_blocks(
                fixture_root, company_id=company_id, document_id=document_id,
                document_version=document_version)
        path = Path(self._resolved_db_identity["resolved_path"])
        conn = _open_registered_readonly(path)
        conn.close()
        return _load_snapshot_and_blocks(
            path, self._resolved_db_identity, scope=self._scope,
            source_kind=self._source_kind, company_id=company_id,
            document_id=document_id, document_version=document_version)

    # -- 反自证：不可序列化 / 不可复制 --------------------------------------

    def to_dict(self) -> dict:
        raise EvidenceGatewayError(
            "VerifiedCurrentEvidenceAuthority 是运行时能力，不得序列化；"
            "从磁盘读回的对象不是本进程签发的那一个（fail-closed）")

    def __copy__(self):
        raise EvidenceGatewayError(
            "VerifiedCurrentEvidenceAuthority 不可 copy：副本未在签发登记表中")

    def __deepcopy__(self, memo):
        raise EvidenceGatewayError(
            "VerifiedCurrentEvidenceAuthority 不可 deepcopy：副本未在签发登记表中")

    def __reduce__(self):
        raise EvidenceGatewayError(
            "VerifiedCurrentEvidenceAuthority 不可 pickle：反序列化出的对象"
            "不是本进程签发的那一个（fail-closed）")

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return (f"<VerifiedCurrentEvidenceAuthority scope={self._scope!r} "
                f"source_kind={self._source_kind!r} "
                f"fingerprint={self._authority_fingerprint[:12]}…>")


def _issue_authority(*, scope: str, source_kind: str, db_path: Path,
                     trust_root_file_sha256: str | None) -> VerifiedCurrentEvidenceAuthority:
    identity = db_file_identity(db_path)
    fp = evidence_authority_identity(
        scope=scope, source_kind=source_kind, resolved_db_identity=identity,
        trust_root_file_sha256=trust_root_file_sha256)
    obj = VerifiedCurrentEvidenceAuthority(
        scope=scope, source_kind=source_kind, resolved_db_identity=identity,
        trust_root_file_sha256=trust_root_file_sha256, authority_fingerprint=fp)
    return _issue_capability(obj, "VerifiedCurrentEvidenceAuthority", scope)


def bind_current_evidence_authority() -> VerifiedCurrentEvidenceAuthority:
    """**唯一**生产入口（无参数）：读 service 已预检绑定的 current Store 并签发。

    "已预检绑定"指 `sections.service._prepare_stores` 把 `evidence.store._db_path`
    指向既有库（不 `init_db`、不迁移）。本函数只**读**该模块级路径，绝不写它。
    """
    from evidence import store as estore
    bound = getattr(estore, "_db_path", None)
    if bound is None:
        raise EvidenceGatewayError(
            "evidence.store._db_path 尚未由 service 预检绑定（None）；"
            "生产 API 不接受 db_path 参数，也不得在此 init 一个空库（fail-closed）")
    return _issue_authority(scope="live", source_kind="current_store",
                            db_path=Path(bound), trust_root_file_sha256=None)


def _issue_pinned_evidence_authority(root_lock: dict,
                                     db_path: Path | None = None) -> VerifiedCurrentEvidenceAuthority:
    """**验收专用**（不导出）：根锁固定路径的 pinned_acceptance issuer（§18.14.2-4）。

    路径固定为工作区正式 `data/evidence.db`；`root_lock` 的 sha256 进入签发身份。
    """
    lock_sha = root_lock.get("fixture_file_sha256")
    if not isinstance(lock_sha, str) or len(lock_sha) != 64:
        raise EvidenceGatewayError(
            "pinned Evidence authority 要求根锁提供 64 位 fixture_file_sha256")
    path = DEFAULT_EVIDENCE_DB_PATH if db_path is None else Path(db_path)
    return _issue_authority(scope="pinned_acceptance", source_kind="historical_run",
                            db_path=path, trust_root_file_sha256=lock_sha)


def _issue_fixture_evidence_authority(fixture_root: dict
                                      ) -> VerifiedCurrentEvidenceAuthority:
    """**验收专用**（不导出）：版本化正向夹具的 `pinned_acceptance` issuer（§18.14.2-9）。

    路径由 `fixture_root_dir()` 固定派生，**没有**任何 `db_path` 形参；签发身份绑定
    manifest 文件的四项文件身份与 `fixture_file_sha256`。它**不打开、不创建**任何
    `data/*.db`。
    """
    if not isinstance(fixture_root, dict):
        raise EvidenceGatewayError("fixture_root 必须为对象")
    lock_sha = fixture_root.get("fixture_file_sha256")
    if not isinstance(lock_sha, str) or len(lock_sha) != 64:
        raise EvidenceGatewayError(
            "夹具 Evidence authority 要求 fixture_root 提供 64 位 fixture_file_sha256")
    identity = fixture_evidence_identity(fixture_root)
    fp = evidence_authority_identity(
        scope="pinned_acceptance", source_kind=FIXTURE_SOURCE_KIND,
        resolved_db_identity=identity, trust_root_file_sha256=lock_sha)
    obj = VerifiedCurrentEvidenceAuthority(
        scope="pinned_acceptance", source_kind=FIXTURE_SOURCE_KIND,
        resolved_db_identity=identity, trust_root_file_sha256=lock_sha,
        authority_fingerprint=fp)
    return _issue_capability(obj, "VerifiedCurrentEvidenceAuthority",
                             "pinned_acceptance")


# ---------------------------------------------------------------------------
# 4. 自检
# ---------------------------------------------------------------------------

def self_check() -> dict:
    problems: list[str] = []
    if V.EVIDENCE_GATEWAY_PROVIDER_VERSION not in V.VERSION_CONSTANTS.values():
        problems.append("EVIDENCE_GATEWAY_PROVIDER_VERSION 未进入 versions.VERSION_CONSTANTS")
    for name in ("VERIFIED_CURRENT_EVIDENCE_AUTHORITY_VERSION",
                 "PINNED_EVIDENCE_AUTHORITY_VERSION",
                 "FIXTURE_EVIDENCE_AUTHORITY_VERSION",
                 "PINNED_TS3_HANDOFF_VERSION", "VERIFIED_TS3_HANDOFF_VERSION"):
        value = getattr(V, name, None)
        if not isinstance(value, str) or value == "":
            problems.append(f"versions.{name} 缺失或为空")
    if not isinstance(V.EVIDENCE_SET_GATEWAY_VERSION, str):
        problems.append("EVIDENCE_SET_GATEWAY_VERSION 缺失")
    if len(CURRENT_SET_QUERY_CONTRACT) == 0:
        problems.append("读取契约不得为空")
    return {
        "provider_version": V.EVIDENCE_GATEWAY_PROVIDER_VERSION,
        "default_db_path": str(DEFAULT_EVIDENCE_DB_PATH),
        "readonly_primitives": list(READONLY_PRIMITIVES),
        "readonly_primitive_identity": readonly_primitive_identity(),
        "current_set_query_contract_identity": current_set_query_contract_identity(),
        "source_kinds": list(EVIDENCE_AUTHORITY_SOURCE_KINDS),
        "fixture_source_kind": FIXTURE_SOURCE_KIND,
        "fixture_root_relpath": FIXTURE_ROOT_RELPATH,
        "fixture_member_roles": list(FIXTURE_MEMBER_ROLES),
        "problems": problems,
    }

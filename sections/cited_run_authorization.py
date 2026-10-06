"""一次性运行授权（`cra-2`）：真实模式在**第一个模型请求之前**必须持有一份逐字段对上的授权。

它解决的是一个具体的漏洞：`sections/cited_budget.py` 的每-run 预算是**上限**，不是**许可**。
预算门只能证明「用了不超过 N 次」，证明不了「这 N 次是被人批准过的」。页面上的模式提示、
`cur_mode` 单选框、乃至「点了开始生成」这个动作，都不是授权——它们全部由浏览器侧决定，
任何一个脚本都可以把它们照抄一遍。

因此本模块把「谁批准了这一次运行」做成**盘上的一条凭据**，并把它的内容钉死成一次具体运行的
全部身份：run-id、三份上传 PDF 的 `(document_id, sha256)`、三份上传 XLSX 的
`(source_version, sha256)`、被复用的权威快照身份与口径（含完整 `source_versions`）、v2
profile 三元组、主体、节集合、模型、prompt 版本、每类上限与整轮上限、自动重试次数。
任何一项对不上都拒，且拒在**建立结果目录之前**。

`cra-2`（本批）在 `cra-1` 的基础上加了四栏：`financial_sources`、`snapshot`、`profile`，以及
**持久化读回时 `granted_by` 为空即拒**。前三条是因为本批的真实运行同时产出公司节与财务节：
只绑三份 PDF 的话，这份凭据证明不了「财务节读的是哪一期、什么口径、哪一版 profile 范围」——
而那正是本批最容易被悄悄换掉的东西。第四条是同一个道理的反面：构造口本来就拒空批准人，
但那拦不住「盘上那一份被改成空字符串之后再读回」。

三条纪律：

* **一次性**。消费用 `O_CREAT | O_EXCL` 建一个 `.consumed.json`——同一份授权第二次到达时，
  内核保证只有一个进程能建成这个文件，其余当场拒。重复点击、另一个浏览器会话、并发进程
  都过不去；这不是「页面上按钮灰掉」那种客户端约束。
* **不解锁任何东西**。授权是**加**在既有预算门之上的前置条件，不是替代品：预算门照旧拦
  「多发一次请求」，本门拦「这次运行根本没被批准」。它也不构成用户权限系统——没有账户、
  没有角色、没有可继承的权限，只有一次运行。
* **不记忆**。本模块不缓存、不查询「最近一次授权」。缺一份就跑不起来，这是设计。

凭据只由人写：见 `scripts/authorize_cited_run.py`。链自己**只读**，从不写授权。
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

AUTHORIZATION_VERSION = "cra-2"
AUTHORIZATION_DIRNAME = "run_authorizations"
GRANT_SUFFIX = ".json"
CONSUMED_SUFFIX = ".consumed.json"

#: 授权只能给**真实**运行。离线运行不发任何请求，因此不需要授权，也不该被这道门拦住——
#: 给离线运行发一份「真实授权」是把读数的语义搅浑。
AUTHORIZED_MODE = "real"

#: 凭据的身份体键集。未知键即拒：一份被改过的授权不该「多带一个字段仍然生效」。
_KEYS = (
    "authorization_version", "authorization_id", "granted_at_utc", "granted_by",
    "run_id", "mode", "subject", "section_ids",
    "model", "prompt_versions", "documents", "financial_sources", "snapshot",
    "profile", "caps", "total_max_attempts", "automatic_retries",
)

#: 被绑定的主体是**主体代码**（本批为 `300750`）。主体全称进**快照身份**那一栏而不是单独一个
#: 字段：它是「声明 + 核对」的结果，与口径同属被绑定的快照身份。`subject` 仍是代码，
#: 因为链在做主体声明比对时用的就是代码。



class CitedAuthorizationError(RuntimeError):
    """授权缺失、对不上、或已被消费。三类都用同一种异常，但消息必须说清是哪一类。"""


def default_root(repo: Path) -> Path:
    return Path(repo) / "data" / AUTHORIZATION_DIRNAME


def grant_path(root: str | Path, run_id: str) -> Path:
    return Path(root) / f"{run_id}{GRANT_SUFFIX}"


def consumed_path(root: str | Path, run_id: str) -> Path:
    return Path(root) / f"{run_id}{CONSUMED_SUFFIX}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class RunAuthorization:
    """一次具体运行的全部被批准身份。字段之间没有主次，任一项都参与比对。"""

    authorization_version: str
    authorization_id: str
    granted_at_utc: str
    granted_by: str
    run_id: str
    mode: str
    subject: str
    section_ids: tuple[str, ...]
    model: str
    #: `(prompt_version, category)`，按 prompt_version 排序后存，比较时按集合比。
    prompt_versions: tuple[tuple[str, str], ...]
    #: `(document_id, sha256)`，按 document_id 排序后存；上传顺序**不**进身份。
    documents: tuple[tuple[str, str], ...]
    #: `(source_version, sha256)`：三份上传 XLSX 与快照来源版本的对应。与 `documents` **分开**：
    #: 两者是不同的来源角色，合并会让「这三份是证据文档」变成一句无法核对的断言。
    financial_sources: tuple[tuple[str, str], ...]
    #: 快照身份与口径（`snapshot_id`/主体代码/主体名称/期末/合并口径/币种/用途/有效性/
    #: 完整 `source_versions`）。存成排好序的 `(字段, 值)` 对，比较即逐项相等。
    snapshot: tuple[tuple[str, str], ...]
    #: v2 profile 的 `(profile_id, profile_version, profile_fingerprint)`。
    profile: tuple[tuple[str, str], ...]
    #: `(category, 每节上限, 整轮上限)`。
    caps: tuple[tuple[str, int, int], ...]
    total_max_attempts: int
    automatic_retries: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "authorization_version": self.authorization_version,
            "authorization_id": self.authorization_id,
            "granted_at_utc": self.granted_at_utc,
            "granted_by": self.granted_by,
            "run_id": self.run_id,
            "mode": self.mode,
            "subject": self.subject,
            "section_ids": list(self.section_ids),
            "model": self.model,
            "prompt_versions": [list(pair) for pair in self.prompt_versions],
            "documents": [list(pair) for pair in self.documents],
            "financial_sources": [list(pair) for pair in self.financial_sources],
            "snapshot": [list(pair) for pair in self.snapshot],
            "profile": [list(pair) for pair in self.profile],
            "caps": [list(cap) for cap in self.caps],
            "total_max_attempts": self.total_max_attempts,
            "automatic_retries": self.automatic_retries,
        }

    def describe(self) -> str:
        """一行可读身份。打印与失败留痕都用它，避免两处各写一遍格式。"""
        docs = "、".join(f"{d}@{s[:12]}" for d, s in self.documents)
        fins = "、".join(f"{v}@{s[:12]}" for v, s in self.financial_sources)
        snap = dict(self.snapshot)
        return (f"{self.authorization_id}：run_id={self.run_id}；模式={self.mode}；"
                f"主体={self.subject}；节={list(self.section_ids)}；模型={self.model}；"
                f"prompt={dict(self.prompt_versions)}；材料={docs}；财务={fins}；"
                f"快照={snap.get('snapshot_id')}（{snap.get('as_of_date')}／{snap.get('scope')}／"
                f"{snap.get('currency')}／{snap.get('purpose')}）；profile={dict(self.profile)}；"
                f"上限={self.caps}；整轮≤{self.total_max_attempts}；重试={self.automatic_retries}")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RunAuthorization":
        if not isinstance(payload, Mapping):
            raise CitedAuthorizationError(f"授权不是一份对象：{type(payload).__name__}")
        extra = sorted(set(payload) - set(_KEYS))
        if extra:
            raise CitedAuthorizationError(
                f"授权含未知字段 {extra}：身份体是封闭的，被加过字段的授权不予采用")
        missing = sorted(set(_KEYS) - set(payload))
        if missing:
            raise CitedAuthorizationError(f"授权缺必填字段 {missing}")
        version = str(payload["authorization_version"])
        if version != AUTHORIZATION_VERSION:
            raise CitedAuthorizationError(
                f"授权版本是 {version!r}，本链只认 {AUTHORIZATION_VERSION!r}")
        #: **持久化的**凭据同样要求批准人非空。构造口（`build_authorization`）已经拒过一次，
        #: 但那拦不住「盘上那一份被改成空字符串之后再读回」——身份体是逐字段比对的，缺了
        #: 「谁批准的」这一栏，这份凭据就不再是一次人的批准，只是几个对得上的数字。
        granted_by = str(payload["granted_by"]).strip()
        if not granted_by:
            raise CitedAuthorizationError(
                "授权读回时 `granted_by` 为空：没有「谁批准的」这一读数就不是一次人的批准，"
                "本链不予采用（空值不因为落在盘上就变成一个可省略的字段）")
        try:
            prompt_versions = tuple(
                (str(pair[0]), str(pair[1])) for pair in payload["prompt_versions"])
            documents = tuple(
                (str(pair[0]), str(pair[1])) for pair in payload["documents"])
            financial_sources = tuple(
                (str(pair[0]), str(pair[1])) for pair in payload["financial_sources"])
            snapshot = tuple(
                (str(pair[0]), str(pair[1])) for pair in payload["snapshot"])
            profile = tuple(
                (str(pair[0]), str(pair[1])) for pair in payload["profile"])
            caps = tuple(
                (str(cap[0]), int(cap[1]), int(cap[2])) for cap in payload["caps"])
        except (TypeError, IndexError, ValueError) as exc:
            raise CitedAuthorizationError(f"授权的列表字段形状不对：{exc}") from exc
        return cls(
            authorization_version=version,
            authorization_id=str(payload["authorization_id"]),
            granted_at_utc=str(payload["granted_at_utc"]),
            granted_by=granted_by,
            run_id=str(payload["run_id"]),
            mode=str(payload["mode"]),
            subject=str(payload["subject"]),
            section_ids=tuple(str(s) for s in payload["section_ids"]),
            model=str(payload["model"]),
            prompt_versions=tuple(sorted(prompt_versions)),
            documents=tuple(sorted(documents)),
            financial_sources=tuple(sorted(financial_sources)),
            snapshot=tuple(sorted(snapshot)),
            profile=tuple(sorted(profile)),
            caps=tuple(sorted(caps)),
            total_max_attempts=int(payload["total_max_attempts"]),
            automatic_retries=int(payload["automatic_retries"]),
        )


def registered_documents_of(binding: Any) -> tuple[tuple[str, str], ...]:
    """从 `cri-1` 的运行输入绑定里抽出被批准的材料身份。

    没有绑定（= 这次运行没有 `--run-input`）即拒：授权绑定的正是三份上传对象的哈希，
    一次不带上传播入的真实运行没有可绑定的材料身份，也就无从「逐字段对上」。本批的演示
    路径一律带上传播入；缺它的真实运行不在本批的授权口径之内。
    """
    if binding is None:
        raise CitedAuthorizationError(
            "本次真实运行没有运行输入（`--run-input`）：授权要求绑定三份上传对象的哈希，"
            "没有上传就没有可绑定的材料身份，因此本批的真实运行一律带上传播入")
    return tuple(sorted((str(obj.document_id), str(obj.declared_sha256))
                        for obj in binding.documents))


def registered_financial_sources_of(binding: Any) -> tuple[tuple[str, str], ...]:
    """从 `cfi-1` 的财务输入绑定里抽出被批准的三份 XLSX。

    与 `cri-1` 同样**没有绑定即拒**：真实运行的财务节读的就是这几份字节，绑不住它们，
    「本次上传 == 权威快照来源」这句话就没有可核对的对象。
    """
    if binding is None:
        raise CitedAuthorizationError(
            "本次真实运行没有财务输入（`--financial-input`）：授权要求绑定三份上传 XLSX 与快照"
            "来源版本的对应，没有上传就没有可绑定的财务材料身份")
    return tuple(sorted((str(obj.source_version), str(obj.declared_sha256))
                        for obj in binding.sources))


def snapshot_identity_of(binding: Any) -> tuple[tuple[str, str], ...]:
    """从 `cfi-1` 绑定里抽出被批准的快照身份与口径（含完整 `source_versions`）。"""
    if binding is None:
        raise CitedAuthorizationError(
            "本次真实运行没有财务输入：没有绑定的快照身份就没有「本次用哪一期、什么口径」"
            "这一读数，授权无从绑定它")
    snap = binding.snapshot
    versions = ",".join(f"{v}@{h}" for v, h in snap.source_versions)
    return tuple(sorted((
        ("snapshot_id", str(snap.snapshot_id)), ("company_id", str(snap.company_id)),
        ("company_name", str(snap.company_name)), ("as_of_date", str(snap.as_of_date)),
        ("scope", str(snap.scope)), ("currency", str(snap.currency)),
        ("purpose", str(snap.purpose)), ("validity", str(snap.validity)),
        ("source_versions", versions),
    )))


def profile_identity_of(profile: Any) -> tuple[tuple[str, str], ...]:
    """从 demo-scope profile 里抽出被批准的身份三元组。**不**在别处另抄一份指纹。"""
    if profile is None:
        raise CitedAuthorizationError(
            "本次真实运行没有 profile：授权绑定的是某一版 profile 的指纹，"
            "没有它就没有「哪一版范围被批过」这一读数")
    return tuple(sorted((
        ("profile_id", str(getattr(profile, "profile_id", ""))),
        ("profile_version", str(getattr(profile, "profile_version", ""))),
        ("profile_fingerprint", str(getattr(profile, "profile_fingerprint", ""))),
    )))


def caps_of(policy: Any) -> tuple[tuple[str, int, int], ...]:
    """从预算政策里抽出被批准的上限。用**同一份**政策，避免两处各写一套数字。"""
    return tuple(sorted((str(cap.category), int(cap.max_attempts_per_section),
                         int(cap.max_attempts_total)) for cap in policy.categories))


def build_authorization(*, granted_by: str, run_id: str, mode: str, subject: str,
                        section_ids: Sequence[str], model: str, policy: Any,
                        binding: Any, financial_binding: Any,
                        profile: Any) -> RunAuthorization:
    """把人这一次批准的内容装成凭据。**唯一**的构造入口，CLI 与测试都走它。

    `binding`（`cri-1`）、`financial_binding`（`cfi-1`）与 `profile` 三个都是**必填**：本批
    的真实运行同时产出公司节与财务节，缺了财务那两栏，凭据就只绑住了三份 PDF，而财务节
    读的是哪一期、什么口径、哪一版范围没有可核对的对象。缺一即在这里停住，不是留空跑。
    """
    granted_by = str(granted_by or "").strip()
    if not granted_by:
        raise CitedAuthorizationError(
            "必须声明批准人（`granted_by`）：没有「谁批准的」这一读数就不是一次人的批准")
    if str(mode) != AUTHORIZED_MODE:
        raise CitedAuthorizationError(
            f"授权只发给 {AUTHORIZED_MODE!r} 运行，收到 {mode!r}：离线运行不发请求，"
            "给它发授权会把读数语义搅浑")
    model = str(model or "").strip()
    if not model:
        raise CitedAuthorizationError("授权必须绑定模型身份：没有模型就没有「谁被批准了」")
    return RunAuthorization(
        authorization_version=AUTHORIZATION_VERSION,
        authorization_id=f"cra_{secrets.token_hex(6)}",
        granted_at_utc=_utc_now(),
        granted_by=granted_by,
        run_id=str(run_id),
        mode=str(mode),
        subject=str(subject),
        section_ids=tuple(str(s) for s in section_ids),
        model=model,
        prompt_versions=tuple(sorted((str(k), str(v))
                                     for k, v in dict(policy.prompt_versions).items())),
        documents=registered_documents_of(binding),
        financial_sources=registered_financial_sources_of(financial_binding),
        snapshot=snapshot_identity_of(financial_binding),
        profile=profile_identity_of(profile),
        caps=caps_of(policy),
        total_max_attempts=int(policy.total_max_attempts),
        automatic_retries=0,
    )


def write_authorization(root: str | Path, authorization: RunAuthorization) -> Path:
    """落一份新授权。**create-only**：同一 run_id 已有授权就拒，不覆盖、不追加。"""
    root = Path(root)
    target = grant_path(root, authorization.run_id)
    if target.exists():
        raise CitedAuthorizationError(
            f"本 run 已有一份授权，拒绝覆盖：{target.name}（要重批请先自行处置那一份）")
    root.mkdir(parents=True, exist_ok=True)
    try:
        with open(target, "x", encoding="utf-8") as handle:
            json.dump(authorization.to_dict(), handle, ensure_ascii=False, indent=2)
    except FileExistsError as exc:  # 两个批准进程撞在一起
        raise CitedAuthorizationError(f"本 run 已有一份授权，拒绝覆盖：{target.name}") from exc
    return target


def load_authorization(root: str | Path, *, run_id: str) -> RunAuthorization:
    """读回本 run 的授权。缺失即拒——不查找、不回落、不认「上一条」。"""
    target = grant_path(root, run_id)
    if not target.is_file():
        raise CitedAuthorizationError(
            f"未找到本次运行 {run_id!r} 的一次性授权：真实模式在**第一个模型请求之前**"
            f"要求一份逐字段对上的批准凭据（期望位置 {target}）。"
            "没有它，本 run 一次请求也不发；页面提示与每-run 预算都不构成授权")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CitedAuthorizationError(f"授权读不开：{target.name}（{exc}）") from exc
    return RunAuthorization.from_dict(payload)


def describe_live(*, run_id: str, mode: str, subject: str,
                  section_ids: Sequence[str], model: str, policy: Any,
                  binding: Any, financial_binding: Any,
                  profile: Any) -> dict[str, Any]:
    """当前这次运行**实际**的身份。与凭据比对的右侧就是它，页面也拿它给读者看。"""
    return {
        "run_id": str(run_id),
        "mode": str(mode),
        "subject": str(subject),
        "section_ids": tuple(str(s) for s in section_ids),
        "model": str(model),
        "prompt_versions": tuple(sorted((str(k), str(v))
                                        for k, v in dict(policy.prompt_versions).items())),
        "documents": registered_documents_of(binding),
        "financial_sources": registered_financial_sources_of(financial_binding),
        "snapshot": snapshot_identity_of(financial_binding),
        "profile": profile_identity_of(profile),
        "caps": caps_of(policy),
        "total_max_attempts": int(policy.total_max_attempts),
        "automatic_retries": 0,
    }


def _mismatch(field: str, granted: Any, live: Any) -> CitedAuthorizationError:
    return CitedAuthorizationError(
        f"授权的 {field} 与本次运行对不上：批准的是 {granted!r}，本次是 {live!r}。"
        "授权绑定的是**一次具体运行**，任何一项不同都要重新批准，不存在「差不多就算数」")


def check_authorization(authorization: RunAuthorization, *, live: Mapping[str, Any]) -> None:
    """逐字段比对。任一项不同即拒，且消息点名是哪一项。"""
    checks = (
        ("run_id", authorization.run_id, live["run_id"]),
        ("模式", authorization.mode, live["mode"]),
        ("主体", authorization.subject, live["subject"]),
        ("节集合", tuple(authorization.section_ids), tuple(live["section_ids"])),
        ("模型", authorization.model, live["model"]),
        ("prompt 版本", authorization.prompt_versions, tuple(live["prompt_versions"])),
        ("材料身份", authorization.documents, tuple(live["documents"])),
        ("财务材料身份", authorization.financial_sources, tuple(live["financial_sources"])),
        ("快照身份与口径", authorization.snapshot, tuple(live["snapshot"])),
        ("profile 身份", authorization.profile, tuple(live["profile"])),
        ("每类上限", authorization.caps, tuple(live["caps"])),
        ("整轮上限", authorization.total_max_attempts, int(live["total_max_attempts"])),
        ("自动重试", authorization.automatic_retries, int(live["automatic_retries"])),
    )
    for field, granted, actual in checks:
        if granted != actual:
            raise _mismatch(field, granted, actual)


def authorization_state(root: str | Path, *, run_id: str) -> dict[str, Any]:
    """页面用的三态读数：`present`／`consumed`／`absent`。它**不**判断字段是否对得上——
    那是 `check_authorization` 的事，页面拿 `describe_live` 自己比，免得两处判据分家。"""
    target = grant_path(root, run_id)
    consumed = consumed_path(root, run_id)
    return {
        "run_id": str(run_id),
        "root": str(root),
        "present": target.is_file(),
        "consumed": consumed.is_file(),
        "grant_path": str(target),
        "consumed_path": str(consumed),
    }


def read_consumption(root: str | Path, *, run_id: str) -> dict[str, Any] | None:
    target = consumed_path(root, run_id)
    if not target.is_file():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"unreadable": str(target)}


def consume_authorization(root: str | Path, authorization: RunAuthorization) -> Path:
    """一次性消费。**先建标记再发请求**：建不成说明这次授权已经被用掉了。

    `O_CREAT | O_EXCL` 是这里唯一的关键——它把「检查是否已消费」和「标记为已消费」压成
    一个原子动作。写成 `if exists(): raise` 再 `write_text()` 会有窗口期，并发下两次运行
    都能穿过去。
    """
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    target = consumed_path(root, authorization.run_id)
    payload = json.dumps({
        "authorization_id": authorization.authorization_id,
        "run_id": authorization.run_id,
        "consumed_at_utc": _utc_now(),
    }, ensure_ascii=False, indent=2)
    try:
        handle = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        previous = read_consumption(root, run_id=authorization.run_id) or {}
        raise CitedAuthorizationError(
            f"本次运行的授权已被消费（{authorization.authorization_id}）："
            f"授权是一次性的，重复点击或另一个会话不能再消费同一份"
            f"（消费记录 {previous}）。要再跑一次请**重新批准**一次新的运行") from exc
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(payload)
    return target


def write_refusal_trace(*, run_input: str | Path | None, run_id: str, stage: str,
                        reason: str) -> Path | None:
    """事前拒绝的可读留痕。

    落在**运行输入目录**而不是结果目录：结果目录一旦建出来，读者就会看到一个「正在跑」的
    run，而这一次根本没有启动。运行输入目录已经因为这次上传而存在，写在那里既不留半成品，
    又和它的上传对象摆在一起。
    """
    if run_input is None:
        return None
    target = Path(run_input) / "authorization_refusal.json"
    try:
        target.write_text(json.dumps({
            "run_id": str(run_id), "stage": str(stage), "reason": str(reason),
            "at_utc": _utc_now(),
            "note": "本 run 在**第一个模型请求之前**被拒；结果目录从未建立，也没有留下半成品。",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return None
    return target

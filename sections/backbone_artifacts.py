"""M930-1：Demo Backbone artifact 骨架（create-only 写入 + 严格读回）。

固定 DAG（M930-1 任务书 §九）::

    内容成员 (content members)  →  artifact_index.json  →  run_manifest.json

规则（逐条对应任务书 §九 1–14）：

1. 先写全部内容成员；
2. 索引只列内容成员，**不含**索引自身与 run manifest；
3. run manifest **最后**写；
4. manifest 记录 artifact_index.json 的 SHA256；
5. manifest 的根 ID 由规范体（排除自身身份字段）派生（见 ``backbone_schema``）；
6. 索引**不**回写 manifest 哈希；
7. 禁止自引用与哈希环（文件级 DAG 严格单向，见上面顺序）；
8. 相对路径归一并拒绝绝对路径、``..``、路径逃逸、重复路径、非规范分隔符、
   symlink / junction / reparse point 逃逸；**逐级**校验目录与文件，只承认索引成员
   路径隐含的普通父目录，多余目录（含空目录）一律 fail-closed；
9. 未知 / 缺失 / 重复 / 多余 / 截断 / 哈希不符 / 大小不符 / schema 不符一律 fail-closed；
   成员与索引条目都必须**显式**声明 path / sha256 / size / role / producer_step /
   工件类型身份 / 工件 schema 版本，并与工件类型注册表闭合校验 —— 索引声明与成员声明
   不一致即拒绝，**不得**由文件后缀猜类型或版本；
10. 目标 run 目录已存在即拒绝，绝不覆盖；
11. 临时目录建在同一父目录下，全部内容写完并关闭后才原子改名；
12. 失败时只清理本次运行显式创建、且已确认属于目标父目录的临时目录；
13. loader 完全只读，不 init / migrate / 写任何数据库；
14. self-check 只在测试临时目录生成空骨架，**不**写 ``evaluation/results/**``。

索引 wire 版本（M930-3D / 冲突 C6）：

- ``demo-artifact-index-v1`` 是 M930-1 **冻结**的索引 wire：角色 / 产出步骤 /
  工件类型 / schema 版本四张词表逐字不动，只作只读回放；
- ``demo-artifact-index-v2`` 是 current 写入版本：在上面**只追加** M930-3 新写作
  主链对象角色（draft / candidate / draft unit / proposal / 两类决定 / accepted
  binding / 定稿 Claim / final Narrative / current Result / writer unresolved /
  精确材料 manifest / 三类 disposition / `FollowUpNeed` 与裁决 / `ExternalFact`）
  以及 ``gate`` / ``evaluator`` 两个产出步骤；
- 声明校验按**索引自身版本**取词表（``ARTIFACT_INDEX_WIRES``）：v1 索引不得借
  current 词表声明 v2 才有的角色；未知版本一律拒绝，不猜测、不回落；
- ``load_run_artifacts`` 两个版本都能只读回放并如实报告 ``is_legacy_index``；
  ``load_legacy_run_artifacts_for_audit`` 只接受 legacy 版本。两者都不升级、不
  重写、不迁移：读回后目录逐字节未变。

Windows 安全策略（显式、可验证、不假装执行过 POSIX 目录 fsync）：

- 每个文件写完立刻 ``flush()`` + ``os.fsync(file_handle)`` 再关闭；
- POSIX 目录 fsync（``os.open(dir, O_DIRECTORY)``）在 Windows 上不可用，
  ``POSIX_DIRECTORY_FSYNC_AVAILABLE`` 如实暴露该事实，可用时尽力执行；
- 作为平台无关的补偿，``os.replace`` 之后**强制读回**：重新打开最终目录，
  逐一复核索引 SHA256、manifest SHA256 与每个成员的 SHA256 + 字节数，
  读回失败即视为写入失败并抛错。

CLI::

    python -m sections.backbone_artifacts --self-check
    python -m sections.backbone_artifacts --validate-only RUN_DIR
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from harness.topic_schema import sha256_canonical
from planning import demo_scope_schema as DSS
from sections.backbone_schema import (
    BACKBONE_SCHEMA_VERSION,
    DemoBackboneRunManifest,
)

ARTIFACT_INDEX_NAME = "artifact_index.json"
RUN_MANIFEST_NAME = "run_manifest.json"

#: M930-1 冻结的索引 wire 版本：**只读回放**，不升级、不重写、不原位扩表（C6）。
ARTIFACT_INDEX_SCHEMA_VERSION_V1 = "demo-artifact-index-v1"

#: M930-3 current 索引 wire 版本：**追加**新写作主链对象角色，v1 词表逐字不动。
ARTIFACT_INDEX_SCHEMA_VERSION_V2 = "demo-artifact-index-v2"

#: current 写入版本（``ArtifactIndex.build`` 只会产出这一个版本）。
ARTIFACT_INDEX_SCHEMA_VERSION = ARTIFACT_INDEX_SCHEMA_VERSION_V2

#: 只读回放的 legacy 索引版本（current reader 不得把它升级成 v2）。
LEGACY_ARTIFACT_INDEX_SCHEMA_VERSIONS = (ARTIFACT_INDEX_SCHEMA_VERSION_V1,)

#: 已知索引版本（v1 legacy + v2 current）；其它版本一律 fail-closed。
ARTIFACT_INDEX_KNOWN_SCHEMA_VERSIONS = (ARTIFACT_INDEX_SCHEMA_VERSION_V1,
                                       ARTIFACT_INDEX_SCHEMA_VERSION_V2)

#: v1 内容成员角色封闭词表（M930-1 冻结；**不得**原位增删）。
ARTIFACT_MEMBER_ROLES_V1 = (
    "plan",
    "scope_manifest",
    "design_surfaces",
    "report_body",
    "narrative_paragraph",
    "claim_table",
    "table_object",
    "pack_manifest",
    "gap_register",
    "review_issue",
    "assurance_result",
    "status",
    "other",
)

#: v2 相对 v1 **新增**的内容成员角色（只追加，不修改既有角色语义）。
#:
#: 覆盖 M930-3 新写作主链的落盘对象：draft / candidate / draft unit / proposal /
#: 两类决定 / accepted binding / 定稿 Claim / final Narrative / current Result /
#: writer 侧 unresolved / 精确材料 manifest / 三类 disposition / `FollowUpNeed`
#: 及其裁决 / formal `ExternalFact`。
ARTIFACT_MEMBER_ROLES_V2_ADDED = (
    "section_draft",
    "claim_candidate",
    "narrative_draft_unit",
    "support_proposal",
    "claim_binding_decision",
    "claim_entailment_decision",
    "accepted_support_binding",
    "section_claim",
    "section_narrative",
    "section_result",
    "section_unresolved",
    "writer_material_manifest",
    "material_disposition",
    "fact_narrative_disposition",
    "follow_up_need",
    "follow_up_decision",
    "external_fact",
)

ARTIFACT_MEMBER_ROLES_V2 = ARTIFACT_MEMBER_ROLES_V1 + ARTIFACT_MEMBER_ROLES_V2_ADDED

#: current 角色词表（= v2）。
ARTIFACT_MEMBER_ROLES = ARTIFACT_MEMBER_ROLES_V2

#: v1 产出步骤封闭词表（M930-1 冻结）。
ARTIFACT_PRODUCER_STEPS_V1 = (
    "scope",
    "plan",
    "research",
    "writer",
    "assembler",
    "review",
    "assurance",
    "status",
    "harness",
)

#: v2 新增产出步骤：确定性 Binding Gate 与 Section Evaluator 各自是独立产者。
ARTIFACT_PRODUCER_STEPS_V2_ADDED = ("gate", "evaluator")

ARTIFACT_PRODUCER_STEPS_V2 = ARTIFACT_PRODUCER_STEPS_V1 + ARTIFACT_PRODUCER_STEPS_V2_ADDED

#: current 产出步骤词表（= v2）。
ARTIFACT_PRODUCER_STEPS = ARTIFACT_PRODUCER_STEPS_V2

#: 工件类型注册表：角色 → (工件类型身份, 产出步骤, 工件 schema 版本)。
#:
#: 这是三个声明字段的**唯一**权威来源：成员与索引条目都必须显式携带这三个值，
#: 并与本表逐项闭合校验。类型身份与 schema 版本**绝不**由文件后缀推断。
#:
#: v1 表逐字冻结（只读回放用）；v2 = v1 + 新增条目，既有条目一个都不改。
ARTIFACT_TYPE_REGISTRY_V1: dict[str, tuple[str, str, str]] = {
    "plan": ("artifact.plan", "plan", "demo-artifact-plan-v1"),
    "scope_manifest": ("artifact.scope_manifest", "scope",
                       "demo-artifact-scope-manifest-v1"),
    "design_surfaces": ("artifact.design_surfaces", "harness",
                        "demo-artifact-design-surfaces-v1"),
    "report_body": ("artifact.report_body", "assembler", "demo-artifact-report-body-v1"),
    "narrative_paragraph": ("artifact.narrative_paragraph", "writer",
                            "demo-artifact-narrative-paragraph-v1"),
    "claim_table": ("artifact.claim_table", "writer", "demo-artifact-claim-table-v1"),
    "table_object": ("artifact.table_object", "assembler", "demo-artifact-table-object-v1"),
    "pack_manifest": ("artifact.pack_manifest", "research",
                      "demo-artifact-pack-manifest-v1"),
    "gap_register": ("artifact.gap_register", "harness", "demo-artifact-gap-register-v1"),
    "review_issue": ("artifact.review_issue", "review", "demo-artifact-review-issue-v1"),
    "assurance_result": ("artifact.assurance_result", "assurance",
                         "demo-artifact-assurance-result-v1"),
    "status": ("artifact.status", "status", "demo-artifact-status-v1"),
    "other": ("artifact.other", "harness", "demo-artifact-other-v1"),
}

ARTIFACT_TYPE_REGISTRY_V2_ADDED: dict[str, tuple[str, str, str]] = {
    "section_draft": ("artifact.section_draft", "writer",
                      "demo-artifact-section-draft-v1"),
    "claim_candidate": ("artifact.claim_candidate", "writer",
                        "demo-artifact-claim-candidate-v1"),
    "narrative_draft_unit": ("artifact.narrative_draft_unit", "writer",
                             "demo-artifact-narrative-draft-unit-v1"),
    "support_proposal": ("artifact.support_proposal", "writer",
                         "demo-artifact-support-proposal-v1"),
    "claim_binding_decision": ("artifact.claim_binding_decision", "gate",
                               "demo-artifact-claim-binding-decision-v1"),
    "claim_entailment_decision": ("artifact.claim_entailment_decision", "evaluator",
                                  "demo-artifact-claim-entailment-decision-v1"),
    "accepted_support_binding": ("artifact.accepted_support_binding", "gate",
                                 "demo-artifact-accepted-support-binding-v1"),
    "section_claim": ("artifact.section_claim", "writer",
                      "demo-artifact-section-claim-v2"),
    "section_narrative": ("artifact.section_narrative", "writer",
                          "demo-artifact-section-narrative-v1"),
    "section_result": ("artifact.section_result", "writer",
                       "demo-artifact-section-result-v2"),
    "section_unresolved": ("artifact.section_unresolved", "writer",
                           "demo-artifact-section-unresolved-v1"),
    "writer_material_manifest": ("artifact.writer_material_manifest", "writer",
                                 "demo-artifact-writer-material-manifest-v1"),
    "material_disposition": ("artifact.material_disposition", "writer",
                             "demo-artifact-material-disposition-v1"),
    "fact_narrative_disposition": ("artifact.fact_narrative_disposition", "writer",
                                   "demo-artifact-fact-narrative-disposition-v1"),
    "follow_up_need": ("artifact.follow_up_need", "writer",
                       "demo-artifact-follow-up-need-v1"),
    "follow_up_decision": ("artifact.follow_up_decision", "harness",
                           "demo-artifact-follow-up-decision-v1"),
    "external_fact": ("artifact.external_fact", "research",
                      "demo-artifact-external-fact-v1"),
}

ARTIFACT_TYPE_REGISTRY_V2: dict[str, tuple[str, str, str]] = {
    **ARTIFACT_TYPE_REGISTRY_V1, **ARTIFACT_TYPE_REGISTRY_V2_ADDED,
}

#: current 工件类型注册表（= v2）。
ARTIFACT_TYPE_REGISTRY = ARTIFACT_TYPE_REGISTRY_V2

#: 派生的工件类型身份 / schema 版本封闭词表（供 wire 校验与验收核对）。
ARTIFACT_TYPE_KEYS = tuple(v[0] for v in ARTIFACT_TYPE_REGISTRY.values())
ARTIFACT_SCHEMA_VERSIONS = tuple(v[2] for v in ARTIFACT_TYPE_REGISTRY.values())


@dataclass(frozen=True)
class ArtifactIndexWire:
    """一个索引 wire 版本的角色 / 步骤 / 注册表词表（版本分派的唯一真值表）。

    声明校验必须按**索引自身的版本**取表，不得一律用 current 表：否则一个
    冻结的 v1 索引会被静默扩权（例如声明只有 v2 才有的角色或产出步骤）。
    """

    schema_version: str
    member_roles: tuple[str, ...]
    producer_steps: tuple[str, ...]
    registry: dict[str, tuple[str, str, str]]

    def type_keys(self) -> tuple[str, ...]:
        return tuple(v[0] for v in self.registry.values())

    def schema_versions(self) -> tuple[str, ...]:
        return tuple(v[2] for v in self.registry.values())


#: 版本 → 词表（v1 legacy 只读回放；v2 current 写入）。
ARTIFACT_INDEX_WIRES: dict[str, ArtifactIndexWire] = {
    ARTIFACT_INDEX_SCHEMA_VERSION_V1: ArtifactIndexWire(
        schema_version=ARTIFACT_INDEX_SCHEMA_VERSION_V1,
        member_roles=ARTIFACT_MEMBER_ROLES_V1,
        producer_steps=ARTIFACT_PRODUCER_STEPS_V1,
        registry=ARTIFACT_TYPE_REGISTRY_V1),
    ARTIFACT_INDEX_SCHEMA_VERSION_V2: ArtifactIndexWire(
        schema_version=ARTIFACT_INDEX_SCHEMA_VERSION_V2,
        member_roles=ARTIFACT_MEMBER_ROLES_V2,
        producer_steps=ARTIFACT_PRODUCER_STEPS_V2,
        registry=ARTIFACT_TYPE_REGISTRY_V2),
}

#: 写入侧使用的 wire（永远是 current）。
ARTIFACT_INDEX_WIRE = ARTIFACT_INDEX_WIRES[ARTIFACT_INDEX_SCHEMA_VERSION]

#: 成员与索引条目共同携带的声明字段（封闭；两处必须完全一致）。
ARTIFACT_DECLARATION_KEYS = ("path", "sha256", "size", "role", "producer_step",
                             "artifact_type", "artifact_schema_version")

#: 索引条目额外携带的字段：同一份声明的自证（path/sha256/size 亦在成员侧）。
ARTIFACT_INDEX_ENTRY_KEYS = ARTIFACT_DECLARATION_KEYS

#: Windows 安全策略标识（写入 manifest 之外的诊断输出，便于验收核对）。
SAFETY_POLICY_ID = "flush_fsync_file_then_readback_v1"

#: POSIX 目录 fsync 是否可用（在 Windows 上为 False；不假装执行过）。
POSIX_DIRECTORY_FSYNC_AVAILABLE = hasattr(os, "O_DIRECTORY")

#: 规范化路径时必须拒绝的分隔符与控制字符。
_NON_CANONICAL_SEPARATORS = ("\\",)
_FORBIDDEN_SEGMENT_CHARS = ("\x00", "\n", "\r", "\t")

#: Windows 保留设备名（跨平台可移植性；命中即拒绝）。
_RESERVED_SEGMENT_NAMES = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})


class RunArtifactError(ValueError):
    """Demo Backbone artifact 协议失败（create-only / fail-closed）。"""


def _err(msg: str) -> None:
    raise RunArtifactError(msg)


# ---------------------------------------------------------------------------
# 路径规范化与逃逸防护（规则 8）
# ---------------------------------------------------------------------------

def canonical_member_path(raw: Any, *, what: str = "artifact member path") -> str:
    """归一化 artefact 相对路径（POSIX ``/`` 分隔），非法即 fail-closed。"""
    if not isinstance(raw, str):
        _err(f"{what} 必须为 str，得到 {type(raw).__name__}")
    if raw == "":
        _err(f"{what} 不能为空")
    for sep in _NON_CANONICAL_SEPARATORS:
        if sep in raw:
            _err(f"{what} 含非规范分隔符 {sep!r}: {raw!r}")
    if raw.startswith("/"):
        _err(f"{what} 不能为绝对路径: {raw!r}")
    if len(raw) >= 2 and raw[1] == ":" and raw[0].isalpha():
        _err(f"{what} 不能带盘符: {raw!r}")
    if raw.endswith("/"):
        _err(f"{what} 不能以 '/' 结尾: {raw!r}")
    segments = raw.split("/")
    for seg in segments:
        if seg == "":
            _err(f"{what} 含空路径段（重复或非规范分隔符）: {raw!r}")
        if seg in (".", ".."):
            _err(f"{what} 含 '.' 或 '..' 路径段: {raw!r}")
        if seg != seg.strip() or seg.endswith("."):
            _err(f"{what} 路径段以空白或 '.' 结尾（Windows 不可移植）: {raw!r}")
        for ch in _FORBIDDEN_SEGMENT_CHARS:
            if ch in seg:
                _err(f"{what} 路径段含控制字符: {raw!r}")
        if seg.split(".")[0].lower() in _RESERVED_SEGMENT_NAMES:
            _err(f"{what} 路径段命中保留设备名: {raw!r}")
    return "/".join(segments)


def _is_reparse_or_link(p: Path) -> bool:
    try:
        st = os.lstat(p)
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _fs_is_reparse(p: Path) -> bool:
    """读回遍历使用的 reparse 谓词（**可注入**）。

    测试在无 symlink 权限的 Windows 上通过替换本函数（或向 ``_scan_tree`` 传入
    ``is_reparse=``）构造确定性反例，而不是 skip 掉该分支。
    """
    return _is_reparse_or_link(p)


def _fs_realpath(p: Path) -> str:
    """读回遍历使用的 realpath（**可注入**，同上）。"""
    return os.path.realpath(p)


def _assert_within(anchor: Path, anchor_real: str, target: Path, what: str) -> None:
    """拒绝经由 symlink / reparse point 逃出 anchor 的路径。"""
    cur = anchor
    rel_parts = target.relative_to(anchor).parts
    for part in rel_parts:
        cur = cur / part
        if _is_reparse_or_link(cur):
            _err(f"{what} 途经 symlink / reparse point，拒绝访问: {cur}")
    real = os.path.realpath(target)
    if real != anchor_real and not real.startswith(anchor_real + os.sep):
        _err(f"{what} 逃逸出目标目录: {target} → {real}")


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------------------
# wire 类型：ArtifactIndex / ArtifactIndexEntry / ArtifactMember
# ---------------------------------------------------------------------------

def _assert_declaration(*, path: str, sha256: str, size: int, role: str,
                        producer_step: str, artifact_type: str,
                        artifact_schema_version: str, what: str,
                        wire: ArtifactIndexWire) -> None:
    """闭合校验一条工件声明（成员与索引条目共用；未知/空值/不匹配一律拒绝）。

    ``wire`` 是按**所属索引版本**取出的词表：v1 索引只能用 v1 的角色 / 步骤 /
    类型 / schema 版本，绝不能用 current 表回宽（C6）。
    """
    canonical = canonical_member_path(path, what=f"{what}.path")
    if canonical in (ARTIFACT_INDEX_NAME, RUN_MANIFEST_NAME):
        _err(f"{what}.path 不得占用协议文件名: {canonical!r}")
    if not isinstance(sha256, str) or len(sha256) != 64 \
            or any(c not in "0123456789abcdef" for c in sha256):
        _err(f"{what}.sha256 必须为 64 位小写 hex: {sha256!r}")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        _err(f"{what}.size 必须为 >= 0 的整数")
    if role not in wire.member_roles:
        _err(f"{what}.role 非法: {role!r}（允许 {wire.member_roles}）")
    expected_type, expected_step, expected_version = wire.registry[role]
    if producer_step not in wire.producer_steps:
        _err(f"{what}.producer_step 非法: {producer_step!r}"
             f"（允许 {wire.producer_steps}）")
    if artifact_type not in wire.type_keys():
        _err(f"{what}.artifact_type 非法: {artifact_type!r}（允许 {wire.type_keys()}）")
    if artifact_schema_version not in wire.schema_versions():
        _err(f"{what}.artifact_schema_version 非法: {artifact_schema_version!r}"
             f"（允许 {wire.schema_versions()}）")
    # 三个声明值必须与角色注册表闭合一致：不得声明一个与 role 不相称的类型/步骤/版本。
    if producer_step != expected_step:
        _err(f"{what}.producer_step 与 role 不符: role={role!r} 应产出步骤 "
             f"{expected_step!r}，得到 {producer_step!r}")
    if artifact_type != expected_type:
        _err(f"{what}.artifact_type 与 role 不符: role={role!r} 应为 {expected_type!r}，"
             f"得到 {artifact_type!r}")
    if artifact_schema_version != expected_version:
        _err(f"{what}.artifact_schema_version 与工件类型不符: {artifact_type!r} 应为 "
             f"{expected_version!r}，得到 {artifact_schema_version!r}")


@dataclass(frozen=True)
class ArtifactIndexEntry:
    """索引中的一条内容成员记录（显式声明的完整工件元数据）。"""

    path: str
    sha256: str
    size: int
    role: str
    producer_step: str
    artifact_type: str
    artifact_schema_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", canonical_member_path(self.path, what="entry.path"))
        _assert_declaration(path=self.path, sha256=self.sha256, size=self.size,
                            role=self.role, producer_step=self.producer_step,
                            artifact_type=self.artifact_type,
                            artifact_schema_version=self.artifact_schema_version,
                            what="ArtifactIndexEntry", wire=ARTIFACT_INDEX_WIRE)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ARTIFACT_INDEX_ENTRY_KEYS}

    def assert_declares(self, entry: "ArtifactIndexEntry", what: str) -> None:
        """索引声明与成员声明必须逐字段一致（不一致即拒绝，不做宽容合并）。"""
        for key in ARTIFACT_DECLARATION_KEYS:
            left, right = getattr(self, key), getattr(entry, key)
            if left != right:
                _err(f"{what} 声明不一致: {key} 索引 {left!r} ≠ 成员 {right!r}")

    @classmethod
    def from_dict(cls, d: Any) -> "ArtifactIndexEntry":
        if not isinstance(d, dict):
            _err(f"ArtifactIndexEntry 必须为 dict，得到 {type(d).__name__}")
        keys = set(ARTIFACT_INDEX_ENTRY_KEYS)
        extra = sorted(set(d) - keys)
        if extra:
            _err(f"ArtifactIndexEntry 含未知字段: {extra}")
        missing = sorted(keys - set(d))
        if missing:
            _err(f"ArtifactIndexEntry 缺必填字段: {missing}")
        return cls(**{k: d[k] for k in ARTIFACT_INDEX_ENTRY_KEYS})


@dataclass(frozen=True)
class ArtifactMember:
    """待写入的一个内容成员（内存中的字节 + **显式**工件声明）。

    ``producer_step`` / ``artifact_type`` / ``artifact_schema_version`` 是**必填**声明：
    缺失即无法构造成员（不设 ``None`` 默认、不按 role 静默补齐，否则"显式声明"就只是
    一句注释）；声明值仍必须与 ``ARTIFACT_TYPE_REGISTRY[role]`` 精确一致，不符即拒绝。
    """

    path: str
    role: str
    producer_step: str
    artifact_type: str
    artifact_schema_version: str
    payload: bytes | str = b""

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", canonical_member_path(self.path, what="member.path"))
        if self.role not in ARTIFACT_MEMBER_ROLES:
            _err(f"ArtifactMember.role 非法: {self.role!r}（允许 {ARTIFACT_MEMBER_ROLES}）")
        expected_type, expected_step, expected_version = ARTIFACT_TYPE_REGISTRY[self.role]
        # 三项声明必须显式给出且与 role 闭合一致；这里**不**补 None，也不静默改写。
        for name, value, expected in (
                ("producer_step", self.producer_step, expected_step),
                ("artifact_type", self.artifact_type, expected_type),
                ("artifact_schema_version", self.artifact_schema_version,
                 expected_version)):
            if value != expected:
                _err(f"ArtifactMember.{name} 与 role 不符: role={self.role!r} 应为 "
                     f"{expected!r}，得到 {value!r}")
        if isinstance(self.payload, str):
            object.__setattr__(self, "payload", self.payload.encode("utf-8"))
        elif not isinstance(self.payload, bytes):
            _err(f"ArtifactMember.payload 必须为 bytes 或 str，得到 "
                 f"{type(self.payload).__name__}")
        _assert_declaration(path=self.path, sha256=self.sha256, size=self.size,
                            role=self.role, producer_step=self.producer_step,
                            artifact_type=self.artifact_type,
                            artifact_schema_version=self.artifact_schema_version,
                            what="ArtifactMember", wire=ARTIFACT_INDEX_WIRE)

    @property
    def sha256(self) -> str:
        return _sha256_bytes(self.payload)

    @property
    def size(self) -> int:
        return len(self.payload)

    def entry(self) -> ArtifactIndexEntry:
        return ArtifactIndexEntry(path=self.path, sha256=self.sha256, size=self.size,
                                  role=self.role, producer_step=self.producer_step,
                                  artifact_type=self.artifact_type,
                                  artifact_schema_version=self.artifact_schema_version)


@dataclass(frozen=True)
class ArtifactIndex:
    """artifact_index.json 的 wire 类型（只列内容成员）。"""

    schema_version: str
    run_id: str
    members: tuple[ArtifactIndexEntry, ...]

    @property
    def wire(self) -> ArtifactIndexWire:
        """本索引**自身版本**对应的词表（v1 只读回放 / v2 current 写入）。"""
        return ARTIFACT_INDEX_WIRES[self.schema_version]

    @property
    def is_legacy(self) -> bool:
        """是否为只读回放的 legacy 索引版本（当前 = v1）。"""
        return self.schema_version in LEGACY_ARTIFACT_INDEX_SCHEMA_VERSIONS

    def __post_init__(self) -> None:
        if self.schema_version not in ARTIFACT_INDEX_KNOWN_SCHEMA_VERSIONS:
            _err(f"ArtifactIndex.schema_version 必须为已知版本之一 "
                 f"{ARTIFACT_INDEX_KNOWN_SCHEMA_VERSIONS}，得到 {self.schema_version!r}")
        if not isinstance(self.run_id, str) or not self.run_id:
            _err("ArtifactIndex.run_id 必须为非空字符串")
        if not self.members:
            _err("ArtifactIndex.members 不能为空")
        paths = [m.path for m in self.members]
        if len(set(paths)) != len(paths):
            _err("ArtifactIndex 含重复成员路径")
        for m in self.members:
            if not isinstance(m, ArtifactIndexEntry):
                _err("ArtifactIndex.members 必须全部为 ArtifactIndexEntry")
        for forbidden in (ARTIFACT_INDEX_NAME, RUN_MANIFEST_NAME):
            if forbidden in paths:
                _err(f"ArtifactIndex 不得记录 {forbidden!r}（规则 2 / 规则 6）")
        # 逐条目按**本索引版本**的词表复核：v1 索引不得借 current 表声明 v2 才有的
        # 角色 / 产出步骤 / 工件类型 / schema 版本（否则冻结 wire 被静默扩权）。
        wire = self.wire
        for m in self.members:
            _assert_declaration(path=m.path, sha256=m.sha256, size=m.size,
                                role=m.role, producer_step=m.producer_step,
                                artifact_type=m.artifact_type,
                                artifact_schema_version=m.artifact_schema_version,
                                what=f"索引 {self.schema_version} 条目 {m.path}",
                                wire=wire)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "members": [m.to_dict() for m in self.members],
        }

    @classmethod
    def build(cls, run_id: str, members: tuple[ArtifactMember, ...]) -> "ArtifactIndex":
        return cls(schema_version=ARTIFACT_INDEX_SCHEMA_VERSION, run_id=run_id,
                   members=tuple(m.entry() for m in members))

    @classmethod
    def from_dict(cls, d: Any) -> "ArtifactIndex":
        if not isinstance(d, dict):
            _err(f"ArtifactIndex 必须为 dict，得到 {type(d).__name__}")
        keys = {"schema_version", "run_id", "members"}
        extra = sorted(set(d) - keys)
        if extra:
            _err(f"ArtifactIndex 含未知字段: {extra}")
        missing = sorted(keys - set(d))
        if missing:
            _err(f"ArtifactIndex 缺必填字段: {missing}")
        raw_members = d["members"]
        if not isinstance(raw_members, list):
            _err(f"ArtifactIndex.members 必须为 list，得到 {type(raw_members).__name__}")
        if not isinstance(d["run_id"], str) or not d["run_id"]:
            _err("ArtifactIndex.run_id 必须为非空字符串")
        return cls(schema_version=d["schema_version"], run_id=d["run_id"],
                   members=tuple(ArtifactIndexEntry.from_dict(x) for x in raw_members))


def serialize_index(index: ArtifactIndex) -> bytes:
    """索引的规范字节（确定性；末尾单个换行）。"""
    return (json.dumps(index.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
            + "\n").encode("utf-8")


def serialize_manifest(manifest: DemoBackboneRunManifest) -> bytes:
    """run manifest 的规范字节（确定性；末尾单个换行）。"""
    return (json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)
            + "\n").encode("utf-8")


@dataclass(frozen=True)
class LoadedRunArtifacts:
    """严格读回的结果。

    ``index_schema_version`` / ``is_legacy_index`` 如实暴露本次读回的是哪个 wire
    版本：legacy 索引只读回放，**不升级、不重写**（``index_sha256`` 与磁盘逐字节
    一致即证明未改写）。
    """

    run_dir: str
    index_sha256: str
    manifest_sha256: str
    index: ArtifactIndex
    manifest: DemoBackboneRunManifest
    members: dict[str, bytes] = field(default_factory=dict)
    verified_member_paths: tuple[str, ...] = ()
    index_schema_version: str = ""
    is_legacy_index: bool = False

    def manifest_dict(self) -> dict:
        return self.manifest.to_dict()


@dataclass(frozen=True)
class WriteRunArtifactsResult:
    run_dir: str
    run_id: str
    member_paths: tuple[str, ...]
    index_sha256: str
    manifest_sha256: str
    manifest_id: str
    safety_policy_id: str
    posix_directory_fsync_available: bool


# ---------------------------------------------------------------------------
# 写入（create-only；规则 1–7、10–12）
# ---------------------------------------------------------------------------

ManifestBuilder = Callable[[str], DemoBackboneRunManifest]


def _fsync_file(handle) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _fsync_dir_best_effort(path: Path) -> bool:
    """POSIX 目录 fsync；Windows 上不可用则如实返回 False（不假装执行）。"""
    if not POSIX_DIRECTORY_FSYNC_AVAILABLE:
        return False
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def _write_file(path: Path, payload: bytes) -> None:
    with open(path, "wb") as fh:
        fh.write(payload)
        _fsync_file(fh)


def _cleanup_tmp(tmp: Path, parent: Path, run_id: str) -> None:
    """只清理本次运行显式创建、且确认属于目标父目录的临时目录（规则 12）。"""
    if tmp.name != f".{run_id}.tmp":
        return
    if os.path.dirname(os.path.abspath(str(tmp))) != os.path.abspath(str(parent)):
        return
    if tmp.exists() and tmp.is_dir() and not _is_reparse_or_link(tmp):
        shutil.rmtree(tmp, ignore_errors=True)


def write_run_artifacts(run_dir: str | Path, members: tuple[ArtifactMember, ...],
                        manifest_builder: ManifestBuilder) -> WriteRunArtifactsResult:
    """create-only 写入一个 run 目录，并强制读回复核。

    ``manifest_builder`` 接收索引文件的 SHA256（规则 4），返回类型化 manifest。
    目标目录已存在即拒绝（规则 10），绝不覆盖。
    """
    run_path = Path(run_dir).absolute()
    if not isinstance(members, tuple) or not members:
        _err("members 必须为非空 tuple[ArtifactMember]")
    for m in members:
        if not isinstance(m, ArtifactMember):
            _err("members 必须全部为 ArtifactMember")
    if not callable(manifest_builder):
        _err("manifest_builder 必须可调用")

    parent = run_path.parent
    if not parent.is_dir():
        _err(f"目标父目录不存在: {parent}")
    if run_path.exists():
        _err(f"目标 run 目录已存在，create-only 拒绝覆盖: {run_path}")

    paths = [m.path for m in members]
    if len(set(paths)) != len(paths):
        _err(f"members 含重复路径: {sorted(p for p in set(paths) if paths.count(p) > 1)}")

    index = ArtifactIndex.build(run_id=run_path.name, members=members)
    # 索引条目声明与成员声明必须逐字段一致（显式断言，而不是依赖"构造时恰好复制"）。
    for member, entry in zip(members, index.members):
        entry.assert_declares(member.entry(), f"成员 {member.path}")
    index_bytes = serialize_index(index)
    index_sha256 = _sha256_bytes(index_bytes)

    manifest = manifest_builder(index_sha256)
    if not isinstance(manifest, DemoBackboneRunManifest):
        _err("manifest_builder 必须返回 DemoBackboneRunManifest")
    if manifest.artifact_index_sha256 != index_sha256:
        _err("manifest.artifact_index_sha256 必须等于索引文件的 SHA256（规则 4）")
    if manifest.run_identity.run_id != index.run_id:
        _err("manifest.run_identity.run_id 必须等于 ArtifactIndex.run_id")
    if manifest.scope_manifest.run_id != index.run_id:
        _err("manifest.scope_manifest.run_id 必须等于 ArtifactIndex.run_id")
    if run_path.name != index.run_id:
        _err(f"run 目录名必须等于 run_id（{run_path.name!r} ≠ {index.run_id!r}）")

    manifest_bytes = serialize_manifest(manifest)
    manifest_sha256 = _sha256_bytes(manifest_bytes)

    tmp = parent / f".{index.run_id}.tmp"
    if tmp.exists():
        _err(f"临时目录已存在，拒绝覆盖: {tmp}")
    try:
        os.mkdir(tmp)
        # 规则 1：先写全部内容成员。
        for m in members:
            target = tmp / m.path
            target.parent.mkdir(parents=True, exist_ok=True)
            _assert_within(tmp, os.path.realpath(tmp), target, "内容成员写出路径")
            _write_file(target, m.payload)
        # 规则 6：索引只列内容成员，不回写 manifest 哈希。
        _write_file(tmp / ARTIFACT_INDEX_NAME, index_bytes)
        # 规则 3：manifest 最后写。
        _write_file(tmp / RUN_MANIFEST_NAME, manifest_bytes)
        _fsync_dir_best_effort(tmp)
        os.replace(str(tmp), str(run_path))
        _fsync_dir_best_effort(parent)
    except Exception:
        _cleanup_tmp(tmp, parent, index.run_id)
        raise

    # Windows 安全补偿：强制读回复核（不依赖 POSIX 目录 fsync）。
    loaded = load_run_artifacts(run_path)
    if loaded.index_sha256 != index_sha256:
        _err("读回复核失败：索引 SHA256 不一致")
    if loaded.manifest_sha256 != manifest_sha256:
        _err("读回复核失败：manifest SHA256 不一致")
    if loaded.manifest.manifest_id != manifest.manifest_id:
        _err("读回复核失败：manifest 身份不一致")

    return WriteRunArtifactsResult(
        run_dir=str(run_path),
        run_id=index.run_id,
        member_paths=tuple(paths),
        index_sha256=index_sha256,
        manifest_sha256=manifest_sha256,
        manifest_id=manifest.manifest_id,
        safety_policy_id=SAFETY_POLICY_ID,
        posix_directory_fsync_available=POSIX_DIRECTORY_FSYNC_AVAILABLE,
    )


# ---------------------------------------------------------------------------
# 读取（严格、完全只读；规则 9、13）
# ---------------------------------------------------------------------------

def _scan_tree(run_path: Path, real_root: str, *,
               is_reparse: Callable[[Path], bool] | None = None,
               realpath: Callable[[Path], str] | None = None,
               ) -> tuple[dict[str, Path], tuple[str, ...]]:
    """遍历 run 目录的**每一层**，返回 ``({相对文件路径: Path}, 相对目录路径元组)``。

    - 每个目录与每个文件在**被递归/读取之前**先做 reparse / 逃逸检查：任何嵌套
      symlink、junction 或其它 reparse point 立即拒绝；
    - 任何 realpath 逃出 run 根的条目立即拒绝（不看扩展名、不看类型）；
    - 只接受普通文件与普通目录，其它类型（FIFO / 设备 / 悬挂链接）一律拒绝。

    ``is_reparse`` / ``realpath`` 可注入：在没有 symlink 权限的 Windows 上，
    测试用它构造确定性 reparse / 逃逸反例，而不是跳过该分支。
    """
    is_reparse = _fs_is_reparse if is_reparse is None else is_reparse
    realpath = _fs_realpath if realpath is None else realpath
    files: dict[str, Path] = {}
    dirs: list[str] = []
    stack = [run_path]
    while stack:
        cur = stack.pop()
        with os.scandir(cur) as it:
            entries = sorted(it, key=lambda e: e.name)
        for entry in entries:
            full = Path(entry.path)
            rel = full.relative_to(run_path).as_posix()
            if is_reparse(full):
                _err(f"读回路径为 / 途经 symlink、junction 或 reparse point，拒绝访问: {rel}")
            real = realpath(full)
            if real != real_root and not real.startswith(real_root + os.sep):
                _err(f"读回路径逃逸出 run 目录: {rel} → {real}")
            if entry.is_dir(follow_symlinks=False):
                dirs.append(rel)
                stack.append(full)
            elif entry.is_file(follow_symlinks=False):
                files[rel] = full
            else:
                _err(f"run 目录含非常规条目（既非普通文件也非普通目录）: {rel}")
    return files, tuple(sorted(dirs))


def _implied_dirs(member_paths: tuple[str, ...]) -> set[str]:
    """索引成员路径隐含的**普通父目录**集合（唯一允许存在的目录集合）。"""
    allowed: set[str] = set()
    for path in member_paths:
        parts = canonical_member_path(path, what="index member path").split("/")[:-1]
        for i in range(1, len(parts) + 1):
            allowed.add("/".join(parts[:i]))
    return allowed


def _read_bytes_exact(path: Path, expected_size: int, expected_sha: str, what: str) -> bytes:
    data = path.read_bytes()
    if len(data) != expected_size:
        _err(f"{what} 大小不符（声明 {expected_size} ≠ 实际 {len(data)}，可能被截断）: {path}")
    actual = _sha256_bytes(data)
    if actual != expected_sha:
        _err(f"{what} SHA256 不符（声明 {expected_sha} ≠ 实际 {actual}）: {path}")
    return data


def load_run_artifacts(run_dir: str | Path) -> LoadedRunArtifacts:
    """严格读回一个 run 目录。

    完全只读：不初始化 / 迁移 / 写任何数据库，不创建任何文件，不改动任何字节。
    """
    run_path = Path(run_dir).absolute()
    if not run_path.is_dir():
        _err(f"run 目录不存在或不是目录: {run_path}")
    if _is_reparse_or_link(run_path):
        _err(f"run 目录不得为 symlink / reparse point: {run_path}")
    real_root = os.path.realpath(run_path)

    found, observed_dirs = _scan_tree(run_path, real_root)
    for required in (ARTIFACT_INDEX_NAME, RUN_MANIFEST_NAME):
        if required not in found:
            _err(f"run 目录缺协议文件: {required}")

    index_path = found[ARTIFACT_INDEX_NAME]
    manifest_path = found[RUN_MANIFEST_NAME]
    index_bytes = index_path.read_bytes()
    index_sha256 = _sha256_bytes(index_bytes)
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha256 = _sha256_bytes(manifest_bytes)

    try:
        index_doc = json.loads(index_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _err(f"artifact_index.json 非法 JSON: {exc}")
    try:
        manifest_doc = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _err(f"run_manifest.json 非法 JSON: {exc}")

    index = ArtifactIndex.from_dict(index_doc)
    manifest = DemoBackboneRunManifest.from_dict(manifest_doc)

    if not isinstance(manifest_doc, dict) or manifest_doc.get("schema_version") \
            != BACKBONE_SCHEMA_VERSION:
        _err("run_manifest.json schema_version 不符")
    if manifest.artifact_index_sha256 != index_sha256:
        _err("run_manifest.artifact_index_sha256 与索引文件不一致（规则 4）")
    if manifest.run_identity.run_id != index.run_id:
        _err("run_id 在索引与 manifest 之间不一致")
    if run_path.name != index.run_id:
        _err(f"run 目录名必须等于 run_id（{run_path.name!r} ≠ {index.run_id!r}）")

    member_paths = tuple(m.path for m in index.members)
    allowed_dirs = _implied_dirs(member_paths)
    extra_dirs = sorted(set(observed_dirs) - allowed_dirs)
    if extra_dirs:
        _err("run 目录含索引成员路径未隐含的多余目录（含空目录，fail-closed）: "
             f"{extra_dirs}")

    expected = {ARTIFACT_INDEX_NAME, RUN_MANIFEST_NAME} | set(member_paths)
    extra = sorted(set(found) - expected)
    if extra:
        _err(f"run 目录含索引未记录的多余文件（fail-closed）: {extra}")
    missing = sorted(expected - set(found))
    if missing:
        _err(f"run 目录缺索引记录的成员: {missing}")

    members: dict[str, bytes] = {}
    for entry in index.members:
        # 再校验一次路径（防止手工构造的索引绕过成员侧规范化）。
        canonical = canonical_member_path(entry.path, what="index member path")
        full = run_path / canonical
        if canonical not in found:
            _err(f"索引记录的成员不存在: {canonical}")
        data = _read_bytes_exact(full, entry.size, entry.sha256, f"成员 {canonical}")
        # 索引条目的类型/步骤/schema 版本声明已在构造期闭合校验；这里按**索引自身
        # 版本**的词表再断言一次该条目与其声明的角色注册项一致（防止某条声明被改坏、
        # 或 legacy 索引借 current 表被读宽后仍被当作可读）。
        _assert_declaration(path=entry.path, sha256=entry.sha256, size=entry.size,
                            role=entry.role, producer_step=entry.producer_step,
                            artifact_type=entry.artifact_type,
                            artifact_schema_version=entry.artifact_schema_version,
                            what=f"索引条目 {canonical}",
                            wire=index.wire)
        members[canonical] = data

    return LoadedRunArtifacts(
        run_dir=str(run_path),
        index_sha256=index_sha256,
        manifest_sha256=manifest_sha256,
        index=index,
        manifest=manifest,
        members=members,
        verified_member_paths=tuple(m.path for m in index.members),
        index_schema_version=index.schema_version,
        is_legacy_index=index.is_legacy,
    )


def load_legacy_run_artifacts_for_audit(run_dir: str | Path) -> LoadedRunArtifacts:
    """**只读**回放一个 legacy（v1）工件目录，并拒绝 current 索引。

    与 ``load_run_artifacts`` 的区别只有一条诚实边界：本入口要求索引版本必须是
    ``LEGACY_ARTIFACT_INDEX_SCHEMA_VERSIONS`` 之一。它不升级、不重写、不迁移、
    不把 v1 声明改写为 v2 —— 任何"顺手升级"的路径在这里就断掉（§八）。
    """
    loaded = load_run_artifacts(run_dir)
    if not loaded.is_legacy_index:
        _err(f"该 run 的索引版本为 {loaded.index_schema_version!r}，不是 legacy 版本 "
             f"{LEGACY_ARTIFACT_INDEX_SCHEMA_VERSIONS}；current 索引请走 "
             "load_run_artifacts（legacy reader 不得读取 current 工件）")
    return loaded


def validate_run_artifacts(run_dir: str | Path) -> dict:
    """validate-only：只读校验，返回结构化结论；不写任何字节。"""
    try:
        loaded = load_run_artifacts(run_dir)
    except RunArtifactError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "run_dir": loaded.run_dir,
        "run_id": loaded.index.run_id,
        "manifest_id": loaded.manifest.manifest_id,
        "manifest_fingerprint": loaded.manifest.manifest_fingerprint,
        "index_sha256": loaded.index_sha256,
        "manifest_sha256": loaded.manifest_sha256,
        "index_schema_version": loaded.index_schema_version,
        "is_legacy_index": loaded.is_legacy_index,
        "member_count": len(loaded.members),
        "member_paths": list(loaded.verified_member_paths),
        "report_version": (loaded.manifest.report_version_identity.report_version
                           if loaded.manifest.report_version_identity else None),
        "safety_policy_id": SAFETY_POLICY_ID,
        "posix_directory_fsync_available": POSIX_DIRECTORY_FSYNC_AVAILABLE,
    }


def index_sha256_of(run_dir: str | Path) -> str:
    """只读返回 artifact_index.json 的 SHA256（供 manifest 构造使用）。"""
    path = Path(run_dir).absolute() / ARTIFACT_INDEX_NAME
    if not path.is_file():
        _err(f"索引文件不存在: {path}")
    return _sha256_bytes(path.read_bytes())


def plan_index_sha256(run_id: str, members: tuple[ArtifactMember, ...]) -> str:
    """在**不写盘**的前提下预计算索引 SHA256（供 manifest 构造 / 测试使用）。

    与 ``write_run_artifacts`` 使用完全相同的规范化与序列化路径，因此两者一致。
    """
    return _sha256_bytes(serialize_index(ArtifactIndex.build(run_id=run_id, members=members)))


# ---------------------------------------------------------------------------
# self-check（只在测试临时目录生成空骨架；规则 14）
# ---------------------------------------------------------------------------

def self_check() -> dict:
    """确定性自检：只在系统临时目录内建/删，永不触碰 ``evaluation/results/**``。"""
    from sections import backbone_schema as BS  # 局部导入，避免循环 import 争议

    checks: list[dict] = []

    def _chk(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    def _expect_error(name: str, fn) -> None:
        # 只接受领域错误（都是 ValueError 子类）：TypeError 之类的实现缺陷必须直接崩出来，
        # 不能被 fail-closed 断言吞掉。
        try:
            fn()
        except (RunArtifactError, ValueError) as exc:
            _chk(name, True, type(exc).__name__)
            return
        _chk(name, False, "预期抛错但通过了")

    def _member(path: str, role: str, payload: bytes | str = "") -> ArtifactMember:
        """self-check 内构造成员：三项声明**逐个显式写出**（不依赖任何默认补齐）。"""
        artifact_type, producer_step, schema_version = ARTIFACT_TYPE_REGISTRY[role]
        return ArtifactMember(path=path, role=role, producer_step=producer_step,
                              artifact_type=artifact_type,
                              artifact_schema_version=schema_version, payload=payload)

    # ---- 构造真实的前序对象（复用 planning 的公开 API，不手搓 dict） ----
    # 注：business_input / source_inputs 目前只有 planning.demo_scope 的模块级构造器，
    # 这里复用它们以保证自检走的是同一条真实构造路径。
    from planning import demo_scope as DS
    from contracts.loader_v2 import load_contract_v2

    profile = DS.load_demo_scope_profile()
    contract = load_contract_v2(str(DS.REPO_ROOT / profile.contract_asset))
    business = DS._business_input("job_demo_backbone_0001")
    manifest_in = DS.build_scope_input_manifest(profile, business, DS._source_inputs())
    run_identity = DS.build_demo_run_identity(
        {"run_id": "demo_backbone_20260920T000000Z", "attempt": 1,
         "started_at": "2026-09-20T00:00:00Z"},
        manifest_in, "evaluation/results/demo_backbone_20260920T000000Z")
    projection = DS.project_contract_v2_scope(contract, profile, manifest_in)

    empty_runtime = {
        "live_page_layout_ids": [], "live_alignment_ids": [], "live_outline_ids": [],
        "live_span_snapshot_ids": [], "pack_ids": [], "external_snapshot_ids": [],
        "financial_fact_pack_artifact_id": None, "gaps": [],
    }
    scope_manifest = DS.resolve_demo_scope_manifest(run_identity, manifest_in, projection,
                                                    empty_runtime)
    closure = BS.FormalPhaseClosureSnapshot.build(
        phases=({"phase_id": "phase4", "status": "open",
                 "source_document": "V2_TODO.md", "recorded_at": "2026-09-20"},),
        source_documents=({"path": "V2_TODO.md", "sha256": "a1" * 32},),
        recorded_at="2026-09-20")
    surfaces = BS.DesignSurfaceMatrix.build((
        BS.DesignSurfaceRecord(surface_id="ds.artifact_integrity",
                               axis="design_surface_coverage",
                               title="artifact create-only 与严格读回", status="demonstrated",
                               evidence_kind="artifact",
                               evidence_refs=("sections/backbone_artifacts.py",)),
    ))

    scratch = Path(tempfile.mkdtemp(prefix="m930_1_artifact_selfcheck_"))
    run_dir = scratch / run_identity.run_id
    try:
        members = (
            _member("content/scope.json", "scope_manifest",
                    json.dumps(scope_manifest.to_dict(), ensure_ascii=False,
                               sort_keys=True, indent=2) + "\n"),
            _member("content/design_surfaces.json", "design_surfaces",
                    json.dumps(surfaces.to_dict(), ensure_ascii=False,
                               sort_keys=True, indent=2) + "\n"),
            _member("README.txt", "other", "empty skeleton\n"),
        )

        def _builder(index_sha: str) -> DemoBackboneRunManifest:
            return DemoBackboneRunManifest.build(
                run_identity=run_identity, scope_manifest=scope_manifest,
                artifact_index_sha256=index_sha, formal_closure=closure,
                design_surfaces=surfaces, created_at="")

        # 预计算与实际写入必须一致（防止两条序列化路径漂移）。
        predicted = plan_index_sha256(run_identity.run_id, members)
        result = write_run_artifacts(run_dir, members, _builder)
        _chk("write.create_only", run_dir.is_dir()
             and result.index_sha256 == predicted, result.manifest_id)

        # 正常写入 + 严格读回
        loaded = load_run_artifacts(run_dir)
        _chk("read.strict_roundtrip",
             loaded.index_sha256 == result.index_sha256
             and loaded.manifest_sha256 == result.manifest_sha256
             and loaded.manifest.to_dict() == _builder(result.index_sha256).to_dict(),
             loaded.manifest.manifest_id)

        # 规则 2 / 6：索引只列内容成员，不含索引自身与 manifest，也不回写 manifest 哈希
        index_names = {m.path for m in loaded.index.members}
        _chk("index.excludes_self_and_manifest",
             ARTIFACT_INDEX_NAME not in index_names and RUN_MANIFEST_NAME not in index_names
             and index_names == {m.path for m in members}, "")
        raw_index_text = (run_dir / ARTIFACT_INDEX_NAME).read_text(encoding="utf-8")
        _chk("index.no_manifest_hash_cycle",
             result.manifest_sha256 not in raw_index_text
             and RUN_MANIFEST_NAME not in raw_index_text, "")

        # 规则 10：目标目录已存在即拒绝
        _expect_error("write.rejects_existing_dir",
                      lambda: write_run_artifacts(run_dir, members, _builder))
        _chk("write.no_overwrite_side_effect",
             load_run_artifacts(run_dir).index_sha256 == result.index_sha256, "")

        # 规则 11：临时目录不存在残留；且没有建到别处
        _chk("write.tmp_cleaned_up",
             not (scratch / f".{run_identity.run_id}.tmp").exists(), "")
        _chk("write.only_touched_target",
             sorted(p.name for p in scratch.iterdir()) == [run_identity.run_id], "")

        # 规则 8 + 12：非法路径在构造期即拒绝，且失败不留下任何目录
        def _bad_write() -> None:
            bad_members = (
                _member("content/scope.json", "scope_manifest", "x"),
                _member("content/../escape.json", "other", "x"),
            )
            write_run_artifacts(scratch / "demo_backbone_escape", bad_members, _builder)

        _expect_error("path.rejects_dotdot", _bad_write)
        _chk("write.failure_left_no_dirs",
             not (scratch / "demo_backbone_escape").exists()
             and not (scratch / ".demo_backbone_escape.tmp").exists(), "")

        # 规则 9：放宽/篡改一律 fail-closed
        def _tamper(label: str, rel: str, mutate) -> None:
            p = run_dir / rel
            original = p.read_bytes()
            mutate(p)
            _expect_error(f"fail_closed.{label}", lambda: load_run_artifacts(run_dir))
            p.write_bytes(original)

        _tamper("member_appended", "README.txt",
                lambda p: p.write_bytes(p.read_bytes() + b"tamper"))
        _tamper("member_truncated", "README.txt", lambda p: p.write_bytes(p.read_bytes()[:3]))
        _tamper("index_role_drift", "artifact_index.json",
                lambda p: p.write_bytes(p.read_bytes().replace(b'"role": "other"',
                                                               b'"role": "plan"')))
        _tamper("manifest_unknown_field", "run_manifest.json",
                lambda p: p.write_bytes(p.read_bytes().replace(b'"manifest_id"',
                                                               b'"manifest_idX"')))

        def _add_extra():
            (run_dir / "extra.json").write_bytes(b"{}")

        original_extra = run_dir / "extra.json"
        _add_extra()
        _expect_error("fail_closed.extra_file", lambda: load_run_artifacts(run_dir))
        original_extra.unlink()

        # 规则 8：多余目录（含**空**目录）也 fail-closed
        empty_dir = run_dir / "content" / "empty_subdir"
        empty_dir.mkdir()
        _expect_error("fail_closed.extra_empty_dir", lambda: load_run_artifacts(run_dir))
        empty_dir.rmdir()
        _chk("fail_closed.extra_empty_dir_restored",
             load_run_artifacts(run_dir).index_sha256 == result.index_sha256, "")

        # 规则 8：嵌套 reparse point —— 用**注入**谓词构造确定性反例，
        # 不依赖 Windows 的 symlink 创建权限，也不允许 skip。
        real_root = os.path.realpath(run_dir)
        # (a) 根下**普通文件**被标为 reparse：必须拒绝（证明文件层被检查）
        _expect_error(
            "fail_closed.nested_reparse_file",
            lambda: _scan_tree(run_dir, real_root,
                               is_reparse=lambda p: p.name == "README.txt"))
        # (b) **嵌套目录**被标为 reparse：必须在递归前拒绝（证明目录层也被检查）
        _expect_error(
            "fail_closed.nested_reparse_dir",
            lambda: _scan_tree(run_dir, real_root,
                               is_reparse=lambda p: p.name == "content"))
        # 注入谓词匹配为空时遍历必须正常完成（证明断言来自注入，而非偶然失败）
        _files, _dirs = _scan_tree(run_dir, real_root, is_reparse=lambda p: False)
        _chk("nested_reparse.injection_is_the_only_cause",
             "README.txt" in _files and "content" in _dirs,
             f"dirs={list(_dirs)}")

        # 规则 8：目录 / 文件 realpath 逃逸出 run 根 —— 同样用注入 realpath 构造反例
        _expect_error(
            "fail_closed.dir_realpath_escape",
            lambda: _scan_tree(run_dir, real_root,
                               realpath=lambda p: str(scratch / "outside")
                               if p.name == "content" else os.path.realpath(p)))
        _expect_error(
            "fail_closed.file_realpath_escape",
            lambda: _scan_tree(run_dir, real_root,
                               realpath=lambda p: str(scratch / "outside")
                               if p.name == "scope.json" else os.path.realpath(p)))

        # 规则 9：类型 / 步骤 / schema 版本声明的闭合校验与索引↔成员一致性。
        # 直接打类型层反例（走完整 run 目录会先撞上索引哈希不符，测不到该分支）。
        _good_entry = _member("README.txt", "other", "empty skeleton\n").entry()
        _good_dict = _good_entry.to_dict()
        for label, key, bad in (
                ("artifact_type_unknown", "artifact_type", "artifact.nope"),
                ("artifact_type_role_mismatch", "artifact_type", "artifact.plan"),
                ("artifact_schema_version_unknown", "artifact_schema_version", "v9"),
                ("artifact_schema_version_role_mismatch", "artifact_schema_version",
                 "demo-artifact-plan-v1"),
                ("producer_step_unknown", "producer_step", "step9"),
                ("producer_step_role_mismatch", "producer_step", "plan"),
                ("role_unknown", "role", "widget"),
                ("role_empty", "role", ""),
                ("path_empty", "path", ""),
                ("sha256_empty", "sha256", ""),
        ):
            _expect_error(f"declaration.{label}",
                          lambda k=key, v=bad: ArtifactIndexEntry.from_dict(
                              {**_good_dict, k: v}))
        _expect_error("declaration.unknown_key",
                      lambda: ArtifactIndexEntry.from_dict({**_good_dict, "extra_key": 1}))
        _expect_error("declaration.missing_key",
                      lambda: ArtifactIndexEntry.from_dict(
                          {k: v for k, v in _good_dict.items() if k != "producer_step"}))
        # 索引声明 ↔ 成员声明不一致（哈希/大小/类型任一处不同）即拒绝
        _expect_error("declaration.index_member_size_mismatch",
                      lambda: _good_entry.assert_declares(
                          _member("README.txt", "other", "x").entry(),
                          "成员 README.txt"))
        _expect_error("declaration.index_member_type_mismatch",
                      lambda: _good_entry.assert_declares(
                          _member("README.txt", "gap_register",
                                  "empty skeleton\n").entry(),
                          "成员 README.txt"))
        _chk("declaration.index_member_match",
             _good_entry.assert_declares(
                 _member("README.txt", "other",
                         "empty skeleton\n").entry(), "x") is None, "")
        _chk("declaration.registry_roles_covered",
             set(ARTIFACT_TYPE_REGISTRY) == set(ARTIFACT_MEMBER_ROLES)
             and len(set(ARTIFACT_TYPE_KEYS)) == len(ARTIFACT_TYPE_KEYS)
             and len(set(ARTIFACT_SCHEMA_VERSIONS)) == len(ARTIFACT_SCHEMA_VERSIONS), "")

        # C6 / §16.8：索引 wire 版本分派。v1 词表逐字冻结且必须是 current 的**真子集**
        # （只追加，不改既有条目）；v1 索引不得借 current 表声明 v2 才有的角色。
        _v1_wire = ARTIFACT_INDEX_WIRES[ARTIFACT_INDEX_SCHEMA_VERSION_V1]
        _v2_wire = ARTIFACT_INDEX_WIRES[ARTIFACT_INDEX_SCHEMA_VERSION_V2]
        _chk("wire.v1_frozen_strict_subset",
             set(_v1_wire.member_roles) < set(_v2_wire.member_roles)
             and set(_v1_wire.producer_steps) < set(_v2_wire.producer_steps)
             and set(_v1_wire.registry) < set(_v2_wire.registry)
             and all(_v2_wire.registry[r] == t
                     for r, t in _v1_wire.registry.items()), "")
        _chk("wire.current_is_v2",
             ARTIFACT_INDEX_SCHEMA_VERSION == ARTIFACT_INDEX_SCHEMA_VERSION_V2
             and ARTIFACT_INDEX_WIRE is _v2_wire, "")
        _chk("wire.legacy_versions_only_v1",
             LEGACY_ARTIFACT_INDEX_SCHEMA_VERSIONS
             == (ARTIFACT_INDEX_SCHEMA_VERSION_V1,), "")
        _chk("wire.writer_always_current",
             ArtifactIndex.build(
                 "run_x", (_member("README.txt", "other", "x"),)
             ).schema_version == ARTIFACT_INDEX_SCHEMA_VERSION, "")
        # 正向：一个只用 v1 角色的 v1 索引必须能被接受并被标为 legacy（只读回放可用）。
        _chk("wire.v1_index_accepts_v1_role",
             ArtifactIndex(
                 schema_version=ARTIFACT_INDEX_SCHEMA_VERSION_V1, run_id="run_x",
                 members=(_member("README.txt", "other", "x").entry(),)
             ).is_legacy, "")
        # 反向：v1 索引声明一个只有 v2 才有的角色 → 拒绝（冻结 wire 不得静默扩权）。
        _expect_error("wire.v1_index_rejects_v2_role",
                      lambda: ArtifactIndex(
                          schema_version=ARTIFACT_INDEX_SCHEMA_VERSION_V1, run_id="run_x",
                          members=(_member("d.json", "section_draft", "x").entry(),)))
        # 反向：未知索引版本 → 拒绝（不猜测、不回落）。
        _expect_error("wire.unknown_index_version",
                      lambda: ArtifactIndex(
                          schema_version="demo-artifact-index-v9", run_id="run_x",
                          members=(_member("README.txt", "other", "x").entry(),)))
        # 反向：legacy reader 不得读取 current 索引（两个入口各守一边）。
        _expect_error("wire.legacy_reader_rejects_current_index",
                      lambda: load_legacy_run_artifacts_for_audit(run_dir))

        # 规则 9 续：三项声明是**必填**构造参数——缺少任何一个都构不出成员（不会按 role
        # 静默补齐）；显式传 None 同样在构造期被拒绝，而不是被当成"未声明"放行。
        _other_type, _other_step, _other_version = ARTIFACT_TYPE_REGISTRY["other"]
        _decl_values = {"artifact_type": _other_type, "producer_step": _other_step,
                        "artifact_schema_version": _other_version}
        for _omitted in ("artifact_type", "producer_step", "artifact_schema_version"):
            _kwargs = {"path": "README.txt", "role": "other",
                       **{k: v for k, v in _decl_values.items() if k != _omitted}}
            try:
                ArtifactMember(**_kwargs)
            except TypeError as exc:
                _chk(f"declaration.required.{_omitted}", True, f"TypeError: {exc}")
            else:
                _chk(f"declaration.required.{_omitted}", False, "缺少必填声明仍构造成功")
        for _key in ("artifact_type", "producer_step", "artifact_schema_version"):
            _expect_error(
                f"declaration.no_silent_none_fill.{_key}",
                lambda k=_key: ArtifactMember(
                    path="README.txt", role="other",
                    **{**_decl_values, k: None}))
        _chk("index.declaration_unchanged",
             load_run_artifacts(run_dir).index_sha256 == result.index_sha256, "")

        readme = run_dir / "README.txt"
        saved = readme.read_bytes()
        readme.unlink()
        _expect_error("fail_closed.missing_member", lambda: load_run_artifacts(run_dir))
        readme.write_bytes(saved)
        _chk("fail_closed.restored", load_run_artifacts(run_dir).members["README.txt"] == saved,
             "")

        # 规则 8：路径规范化（绝对路径 / .. / 分隔符 / 保留名 / 盘符）
        _expect_error("path.rejects_absolute",
                      lambda: canonical_member_path("/etc/passwd"))
        _expect_error("path.rejects_drive",
                      lambda: canonical_member_path("C:/x.json"))
        _expect_error("path.rejects_backslash",
                      lambda: canonical_member_path("a\\b.json"))
        _expect_error("path.rejects_double_slash",
                      lambda: canonical_member_path("a//b.json"))
        _expect_error("path.rejects_reserved",
                      lambda: canonical_member_path("content/CON.json"))
        _chk("path.normalizes",
             canonical_member_path("content/a/b.json") == "content/a/b.json", "")

        # validate-only 不改字节
        before = {p.relative_to(run_dir).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in sorted(run_dir.rglob("*")) if p.is_file()}
        verdict = validate_run_artifacts(run_dir)
        after = {p.relative_to(run_dir).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in sorted(run_dir.rglob("*")) if p.is_file()}
        _chk("validate_only.no_byte_change",
             verdict["ok"] is True and before == after,
             f"members={verdict.get('member_count')}")
        _chk("validate_only.no_db_no_llm",
             verdict["ok"] is True and verdict["run_id"] == run_identity.run_id
             and verdict["report_version"] is None, "")

        # loader 不产生额外文件
        _chk("read.loader_creates_nothing",
             sorted(p.name for p in scratch.iterdir()) == [run_identity.run_id], "")

        # 自检本身绝不写 evaluation/results/**
        _chk("self_check.outside_results_dir",
             "evaluation" not in scratch.parts and "results" not in scratch.parts,
             str(scratch))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    ok = all(c["ok"] for c in checks)
    return {
        "module": "sections.backbone_artifacts",
        "ok": ok,
        "passed": sum(1 for c in checks if c["ok"]),
        "failed": sum(1 for c in checks if not c["ok"]),
        "safety_policy_id": SAFETY_POLICY_ID,
        "posix_directory_fsync_available": POSIX_DIRECTORY_FSYNC_AVAILABLE,
        "checks": checks,
    }


def _main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    if args == ["--self-check"]:
        report = self_check()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1
    # 官方接口是 --validate-only（与实施计划一致）；--validate 暂时容忍但不出现在
    # 帮助文本中，避免第二套接口长期存在。
    if args and args[0] in ("--validate-only", "--validate"):
        if len(args) != 2:
            print("用法: python -m sections.backbone_artifacts --validate-only RUN_DIR")
            return 2
        print(json.dumps(validate_run_artifacts(args[1]), ensure_ascii=False, indent=2))
        return 0
    print(__doc__)
    print("用法: python -m sections.backbone_artifacts --self-check | "
          "--validate-only RUN_DIR")
    return 2 if args else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(_main(sys.argv[1:]))

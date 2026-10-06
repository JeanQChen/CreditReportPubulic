"""M930-2 生产 SourcePolicyResolver：从组合根显式传入的冻结资产独立解析。

用法：

    resolver = FrozenSourcePolicyResolver.from_asset(
        "templates/policies/source_policy_v1.yaml")
    snap = resolver.resolve(requirement.aspects[0].source_policy_ref)   # 或 None

边界（M930-2 返修裁决 §三）：

- 资产路径**只在**组合根（``from_asset``）出现一次；``resolve()`` 只接受
  ``SourcePolicyRef``，不接受文件路径，不重读磁盘，不允许需求方临时换资产；
- 内容身份一律由本模块对冻结资产正文**重新计算**（``schema_v2.content_fingerprint``），
  绝不采信调用方自报的 fingerprint / policy_id / policy_version；
- 加载与校验走正式入口 ``contracts.source_policy.load_source_policy()`` 与
  ``validate_source_policy()``，本模块不另立第二套来源政策语义；
- 只有 policy_id、policy_version、content_fingerprint 三项与 ``SourcePolicyRef``
  全部一致才返回 ``FrozenSourcePolicySnapshot``；任一不一致（含身份漂移）→ ``None``，
  由 ``TS.verify_frozen_source_policy`` 继续 fail-closed；
- 未冻结、篡改、缺文件、解析失败、正式校验不通过 → 抛具名 ``SourcePolicyAssetError``
  （组合根错误，不降级为「无政策」）；
- ``key_industry_topics`` 由资产正文派生，不硬编码；``key_conclusion_rule(_version)``
  取自 schema 受信常量。本模块不含公司、topic、页码或 Evidence ID 专用规则。
"""

from __future__ import annotations

from pathlib import Path

from contracts import schema_v2 as S
from contracts import source_policy as SP
from harness import topic_schema as TS

RESOLVER_VERSION = "fspr-1"

#: 资产级 fail-closed 原因码（封闭集合）。
ASSET_ERROR_REASONS = (
    "asset_missing",            # 路径不存在
    "asset_unreadable",         # 存在但读取/解析失败
    "asset_not_frozen",         # status != "frozen"（未冻结资产不得进入运行时）
    "asset_fingerprint_drift",  # 声明 content_sha256 缺失或与正文重算值不一致（被篡改）
    "asset_invalid",            # 正式 validator 拒绝
)

#: 身份不一致原因码（``resolve`` 返回 None 前的判定依据，供测试/审计读取）。
IDENTITY_MISMATCH_REASONS = (
    "policy_id_mismatch",
    "policy_version_mismatch",
    "content_fingerprint_mismatch",
)


class SourcePolicyAssetError(Exception):
    """冻结来源政策资产不可用（组合根错误）：具名 fail-closed，绝不降级为「无政策」。"""

    def __init__(self, reason: str, message: str) -> None:
        if reason not in ASSET_ERROR_REASONS:
            raise ValueError(f"未知的 SourcePolicy 资产原因码: {reason!r}")
        # 原因码进消息本体：日志/测试只读文本时也能拿到具名 fail-closed 依据。
        super().__init__(f"[{reason}] {message}")
        self.reason = reason
        self.message = message

    def to_dict(self) -> dict:
        return {"resolver_version": RESOLVER_VERSION, "reason": self.reason,
                "message": self.message}


class FrozenSourcePolicyResolver:
    """从单个冻结资产解析 ``SourcePolicyRef`` 的生产 resolver。

    组合根（`planning/demo_scope` 等）在装配时调用一次 ``from_asset``；runtime 只拿
    ``resolve()``。实例持有资产正文的规范内容指纹，不持有路径，故运行期无法换资产。
    """

    __slots__ = ("_snapshot", "_source_classes")

    def __init__(self, snapshot: TS.FrozenSourcePolicySnapshot,
                 *, source_classes: tuple[str, ...] = ()) -> None:
        if not isinstance(snapshot, TS.FrozenSourcePolicySnapshot):
            raise TypeError("FrozenSourcePolicyResolver 只接受 FrozenSourcePolicySnapshot")
        self._snapshot = snapshot
        self._source_classes = tuple(source_classes)

    # -- 组合根入口 ---------------------------------------------------------

    @classmethod
    def from_asset(cls, asset_path: str | Path) -> "FrozenSourcePolicyResolver":
        """从冻结资产路径构建 resolver（组合根唯一一次读盘）。"""
        path = Path(asset_path)
        if not path.exists():
            raise SourcePolicyAssetError(
                "asset_missing", f"冻结来源政策资产不存在: {path}")

        policy = cls._load(path)
        raw = policy.raw

        if policy.status != "frozen":
            raise SourcePolicyAssetError(
                "asset_not_frozen",
                f"来源政策资产未冻结（status={policy.status!r}），不得进入运行时: {path}")

        declared = raw.get("content_sha256")
        computed = S.content_fingerprint(raw)
        if declared in (None, "") or declared != computed:
            raise SourcePolicyAssetError(
                "asset_fingerprint_drift",
                f"来源政策资产内容指纹不一致（声明 {declared!r} ≠ 重算 {computed!r}）: {path}")

        result = SP.validate_source_policy(policy)
        if not result.valid:
            raise SourcePolicyAssetError(
                "asset_invalid",
                f"来源政策资产未通过正式校验（{len(result.errors)} 处）: "
                + "；".join(result.errors))

        snapshot = TS.FrozenSourcePolicySnapshot(
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            content_fingerprint=computed,
            key_industry_topics=tuple(policy.key_industry_topics),
            key_conclusion_rule=TS.KEY_CONCLUSION_RULE,
            key_conclusion_rule_version=TS.KEY_CONCLUSION_RULE_VERSION,
        )
        return cls(snapshot, source_classes=tuple(policy.source_classes))

    @staticmethod
    def _load(path: Path) -> SP.SourcePolicy:
        try:
            return SP.load_source_policy(str(path))
        except Exception as e:  # noqa: BLE001 — 解析/IO 一律 fail-closed 为具名资产错误
            raise SourcePolicyAssetError(
                "asset_unreadable",
                f"来源政策资产无法读取或解析: {path}（{type(e).__name__}: {e}）") from e

    # -- runtime 入口 -------------------------------------------------------

    def resolve(self, source_policy_ref: TS.SourcePolicyRef) -> TS.FrozenSourcePolicySnapshot | None:
        """按 ref 解析冻结投影；任一项身份不一致（dangling）→ None。

        绝不接受路径参数，绝不重读磁盘，绝不返回「最接近」的政策。
        """
        if not isinstance(source_policy_ref, TS.SourcePolicyRef):
            raise TypeError("resolve() 只接受 SourcePolicyRef（不接受文件路径或原始 dict）")
        if self.identity_mismatch_reason(source_policy_ref) is not None:
            return None
        return self._snapshot

    def identity_mismatch_reason(self, source_policy_ref: TS.SourcePolicyRef) -> str | None:
        """身份不一致的原因码；三项全等 → None（供测试与审计，不参与放行判断）。"""
        if source_policy_ref.policy_id != self._snapshot.policy_id:
            return "policy_id_mismatch"
        if source_policy_ref.policy_version != self._snapshot.policy_version:
            return "policy_version_mismatch"
        if source_policy_ref.content_fingerprint != self._snapshot.content_fingerprint:
            return "content_fingerprint_mismatch"
        return None

    @property
    def snapshot(self) -> TS.FrozenSourcePolicySnapshot:
        """已冻结的不可变投影（只读；组合根审计用，不参与 resolve 放行判断）。"""
        return self._snapshot

    def identity(self) -> dict:
        return {"resolver_version": RESOLVER_VERSION,
                "policy_id": self._snapshot.policy_id,
                "policy_version": self._snapshot.policy_version,
                "content_fingerprint": self._snapshot.content_fingerprint,
                "source_classes": list(self._source_classes)}

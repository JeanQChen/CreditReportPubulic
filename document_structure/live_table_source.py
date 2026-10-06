"""M930-3 §18.1 A5：**demo run 内的正式图侧组合根**（`lts-2`）。

`DESIGN_V2.md` §0.17.1 要求演示主题的目标表「经现有图／工具链」取得**正式资格**。
而 `TableObjectV4` 本身不是资格结论：图侧唯一的资格载体是
`final_verifier.verify_final_material_snapshot` 签发的
`VerifiedFinalMaterialStructureSnapshot`。这条链此前只有一个调用方
（`final_material_builder.build_final_material_snapshot` 被 TS5 验收 runner 与测试
调用），**demo run 根本不在图侧链上**——本模块就是那条接线。

调用顺序固定（每一步都只调用既有公开生产入口）：

```text
LiveVerifiedSpanSource.verified_snapshot          （runtime 能力，取用时复核签发域）
  → FMB.build_final_material_snapshot(verified)    （构建；**不是**资格）
  → FV.verify_final_material_snapshot(verified, snapshot)   （唯一资格入口）
```

**builder 的返回值不是资格结论**。这正是 §18.8.8-8 记录的实测教训：只调
`build_final_material_snapshot` 会得到"通过"的假结论（三份上传材料都如此），
真正的终态只由 `final_verifier` 给出。因此本模块：

- **不吞掉** `final_verifier` 的拒绝：它被翻译成一个 typed、可审计的
  `LiveTableSourceRefusal`（含拒绝种类与逐层守恒读数），并在只读报告里逐字可读；
- **不降级**：拒绝时 `LiveTableSource.verified` 与 `.tables` 都不可用
  （`tables` 直接抛错），因此"忽略一个 flag 照常消费"在本模块里不存在；
- **不**把 `VerifiedFinalMaterialStructureSnapshot` 之外的任何东西当资格
  （诊断平铺文本、`refused` / `partial` 对象一律不是，见 §0.17.3-1）。

反自证边界（与 `live_span_source` 同）：本类只**持有** `LiveVerifiedSpanSource` 与
`VerifiedFinalMaterialStructureSnapshot` 两个 runtime 能力，不复制、不序列化、不从
JSON 重建。每次取用都重新要求内层能力仍在本进程签发登记表内，因此"自造一个
`LiveTableSource`"拿不到任何资格。重新证明只能重走整条链并与冻结投影逐项比较
（`reprove_live_table_source`）。

本模块**不**写库、**不** init / migrate、**不**读 Contract、**不**调 LLM / 网络。
"""

from __future__ import annotations

import dataclasses
from typing import Any

from document_structure import final_material_builder as FMB
from document_structure import final_verifier as FV
from document_structure import live_span_source as LSS
from document_structure import table_schema as TS
from document_structure import versions as V
from document_structure.schema import SchemaValidationError


class LiveTableSourceError(SchemaValidationError):
    """图侧组合**用法 / 环境**错误（输入不是同次 live 能力 / 复核器自身不独立）。

    这一类与 `LiveTableSourceRefusal` **不是**同一种东西：拒绝说的是"这份文档没有
    取得正式资格"（文档事实），异常说的是"调用方式或代码完整性有问题"（不是文档的
    属性）。把后者报成前者会让审计把代码缺陷读成业务缺口。
    """


#: 本组合根的规则版本（`lts-2`）。
LIVE_TABLE_SOURCE_VERSION = V.LIVE_TABLE_SOURCE_VERSION

#: 图侧组合的**封闭**拒绝种类。每一个都对应一个可复核的事实，不得互相顶替。
#:
#: - `final_material_build_failed`：`build_final_material_snapshot` 本身就抛错，
#:   快照**未诞生**（原始异常类型与文本逐字保留在 `detail` 里，不被吞掉）。
#: - `final_material_blocked`：快照已诞生，但存在**阻断性**结构缺口
#:   （`upstream_table_scope_miss` / `root_identity_mismatch`），复核器拒绝签发。
#: - `final_material_conservation_ineligible`：快照已诞生，但最终材料守恒资格不成立
#:   （未解释残余 / 层内问题码非空），复核器拒绝签发。
#: - `final_verification_failed`：复核的**结构比对**本身不符（根不一致 / 重算不等 /
#:   成员身份漂移等），复核器拒绝签发。
#:
#: 四者是**并列**的四种拒绝，不是"同一个失败的四种说法"：它们分别指向构建、
#: 上游结构缺口、守恒资格与结构一致性四个不同环节。演示门的读回必须逐份材料给出
#: 究竟是哪一种，而不是笼统写"未通过"。
GRAPH_TABLE_REFUSAL_KINDS: tuple[str, ...] = (
    "final_material_build_failed",
    "final_material_blocked",
    "final_material_conservation_ineligible",
    "final_verification_failed",
)

#: 只读报告里每条拒绝附带的样例问题码条数（全量条数仍以计数给出）。
_REFUSAL_EXAMPLES = 3


# ---------------------------------------------------------------------------
# 1. typed 拒绝（值，不是异常；不是 wire 类型，只是诊断投影）
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class LiveTableSourceRefusal:
    """一条**typed** 的图侧资格拒绝（fail-closed 的诚实终态）。

    它只承载"为什么没取得资格"这一件事，因此**不**携带任何表格对象、也不携带
    任何数字读数：拒绝的表不得被当作正式材料或数字权威（§0.17.3-1）。
    """

    document_id: str
    document_version: str
    refusal_kind: str
    detail: str
    error_type: str
    blocking_kinds: dict
    layer_readings: tuple
    problem_count: int
    problem_examples: tuple
    builder_table_count: int
    builder_version: str
    verifier_version: str
    source_version: str

    def __post_init__(self) -> None:
        if self.refusal_kind not in GRAPH_TABLE_REFUSAL_KINDS:
            raise LiveTableSourceError(
                f"未登记的图侧拒绝种类 {self.refusal_kind!r}；"
                f"封闭词表为 {GRAPH_TABLE_REFUSAL_KINDS}")
        if self.source_version != LIVE_TABLE_SOURCE_VERSION:
            raise LiveTableSourceError(
                f"拒绝记录的 source_version 必须为 "
                f"{LIVE_TABLE_SOURCE_VERSION!r}，得到 {self.source_version!r}")
        if not isinstance(self.blocking_kinds, dict) \
                or not isinstance(self.layer_readings, tuple):
            raise LiveTableSourceError("拒绝记录的计数字段形状不符（dict / tuple）")

    def to_dict(self) -> dict:
        """纯 JSON 安全值（供只读报告逐份材料写出）。"""
        return {
            "document_id": self.document_id,
            "document_version": self.document_version,
            "refusal_kind": self.refusal_kind,
            "error_type": self.error_type,
            "detail": self.detail,
            "blocking_kinds": dict(self.blocking_kinds),
            "layer_readings": [dict(row) for row in self.layer_readings],
            "problem_count": self.problem_count,
            "problem_examples": list(self.problem_examples),
            "builder_table_count": self.builder_table_count,
            "builder_version": self.builder_version,
            "verifier_version": self.verifier_version,
            "source_version": self.source_version,
        }

    def summary(self) -> str:
        """一行可读结论（不替代 `detail`）。"""
        return (f"{self.document_id} 图侧资格拒绝：{self.refusal_kind}"
                f"（builder 侧 {self.builder_table_count} 张表，**不构成资格**）")


# ---------------------------------------------------------------------------
# 2. 守恒读数（拒绝时逐层给出，供审计复算）
# ---------------------------------------------------------------------------

def _layer_readings(snapshot: Any) -> tuple:
    """逐层守恒读数：`(层名, 本层问题数, 分项值, 合计, 是否成立, 样例问题)`。"""
    conservation = getattr(snapshot, "conservation", None)
    if conservation is None:
        return ()
    rows = []
    for layer in conservation.layers:
        values = TS.conservation_layer_values(layer)
        total = TS.conservation_term_value(layer.total)
        problems = tuple(layer.problems)
        rows.append({
            "layer_kind": layer.layer_kind,
            "problem_count": len(problems),
            "values": {k: int(v) for k, v in sorted(values.items())},
            "total": int(total),
            "eligible": bool(TS.conservation_layer_eligible(
                layer.layer_kind, values, total, problems)),
            "problem_examples": [str(p) for p in problems[:_REFUSAL_EXAMPLES]],
        })
    return tuple(rows)


def _refusal_from(snapshot: Any, document_id: str, document_version: str,
                  kind: str, error: BaseException) -> LiveTableSourceRefusal:
    conservation = getattr(snapshot, "conservation", None)
    problems = tuple(getattr(conservation, "problems", ()) or ())
    return LiveTableSourceRefusal(
        document_id=document_id,
        document_version=document_version,
        refusal_kind=kind,
        detail=str(error),
        error_type=type(error).__name__,
        blocking_kinds=dict(getattr(error, "blocking_kinds", {}) or {}),
        layer_readings=_layer_readings(snapshot),
        problem_count=len(problems),
        problem_examples=tuple(str(p) for p in problems[:_REFUSAL_EXAMPLES]),
        builder_table_count=int(getattr(snapshot, "table_count", 0) or 0),
        builder_version=FMB.BUILDER_VERSION,
        verifier_version=FV.VERIFIER_VERSION,
        source_version=LIVE_TABLE_SOURCE_VERSION,
    )


# ---------------------------------------------------------------------------
# 3. 输入闸门（调用方式错误 = 异常，不是拒绝）
# ---------------------------------------------------------------------------

def _check_version(name: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise LiveTableSourceError(
            f"{name} 版本漂移：构建产物为 {actual!r}，当前权威常量为 {expected!r}；"
            f"图侧组合根不得在版本不一致时继续（fail-closed）")


def _assert_live_span_source(span_source: Any):
    """输入必须是**本进程同次签发**的 live 树能力来源。

    四道检查缺一不可，且都在**任何构建之前**：①类型是组合根产出的来源对象；
    ②其 TS4 能力仍在本进程签发登记表内（`issued_capability` 会再核一次）；
    ③签发域是 `live`；④签发域版本等于当前权威常量。
    """
    if not isinstance(span_source, LSS.LiveVerifiedSpanSource):
        raise LiveTableSourceError(
            "图侧组合根只接受 LiveVerifiedSpanSource（同次 live 签发），得到 "
            f"{type(span_source).__name__}；从 JSON 重建 verified 状态的做法不存在"
            "（fail-closed）")
    try:
        # 取用即复核：内层 TS4 能力必须仍在本进程签发登记表内。自造对象在这里
        # 失败（"字段长得一样"不产生资格）。
        verified = span_source.verified_snapshot
    except Exception as e:  # noqa: BLE001 - 统一成组合根的用法错误
        raise LiveTableSourceError(
            f"输入的 live 树来源未持有**本进程签发**的 TS4 能力："
            f"{type(e).__name__}: {e}（fail-closed）") from e
    if verified.issuer_scope != LSS.LIVE_ISSUER_SCOPE:
        raise LiveTableSourceError(
            f"图侧组合根只接受 live 域的 TS4 复核结果，得到 "
            f"{verified.issuer_scope!r}")
    _check_version("verified_span_snapshot.issuer_version",
                   verified.issuer_version, V.VERIFIED_SPAN_SNAPSHOT_VERSION)
    if FV.verifier_independence_problems():
        # 复核器的独立性是**代码完整性**问题：它一旦不成立，任何"拒绝/通过"的
        # 结论都不能被采信，因此这里直接失败，不翻译成文档级拒绝。
        raise LiveTableSourceError(
            "复核器自身违反独立性约束，组合根拒绝据此给出任何资格结论")
    return verified


# ---------------------------------------------------------------------------
# 4. 运行时能力包装（只持有，不复制、不序列化）
# ---------------------------------------------------------------------------

class LiveTableSource:
    """**同次签发**的图侧正式来源（运行时对象，不可序列化）。

    两种终态恰有其一，没有第三种：

    - `is_qualified is True`：`final_verifier` 已签发
      `VerifiedFinalMaterialStructureSnapshot`（`.verified` 可取，`.tables` 可取）；
    - `is_qualified is False`：`final_verifier` 拒绝，`.refusal` 携带 typed 种类、
      原始异常类型与文本、逐层守恒读数（`.verified` / `.tables` **不可取**）。

    "builder 通过了"不在其中——它**不是**资格。
    """

    __slots__ = ("_span_source", "_request_identity", "_snapshot", "_verified",
                 "_refusal", "__weakref__")

    def __init__(self, *, span_source: LSS.LiveVerifiedSpanSource,
                 request_identity: dict, snapshot: Any = None,
                 verified: Any = None, refusal: Any = None) -> None:
        if (refusal is None) == (verified is None):
            raise LiveTableSourceError(
                "图侧来源必须恰有**一种**终态（已签发 capability 或 typed 拒绝），"
                "两种都给或都不给都是构造错误（fail-closed）")
        if verified is not None and snapshot is None:
            raise LiveTableSourceError("已签发的图侧来源必须携带 fms-1 快照")
        self._span_source = span_source
        self._request_identity = dict(request_identity)
        self._snapshot = snapshot
        self._verified = verified
        self._refusal = refusal

    # -- 资格复核 ---------------------------------------------------------

    def _assert_live(self) -> None:
        # 内层 TS4 来源每次取用都复核（`verified_snapshot` 自己会核）。
        self._span_source.verified_snapshot
        if self._verified is not None:
            FV.assert_final_material_capability(self._verified,
                                                (LSS.LIVE_ISSUER_SCOPE,))

    # -- 只读访问（每次取用都先复核资格）-----------------------------------

    @property
    def is_qualified(self) -> bool:
        self._assert_live()
        return self._verified is not None

    @property
    def verified(self):
        """已签发的 `VerifiedFinalMaterialStructureSnapshot`（未取得资格即抛错）。"""
        self._assert_live()
        if self._verified is None:
            raise LiveTableSourceError(
                f"该文档未取得图侧正式资格（{self._refusal.refusal_kind}）："
                f"能力对象从未诞生，任何消费都必须走拒绝分支（fail-closed）")
        return self._verified

    @property
    def refusal(self) -> LiveTableSourceRefusal:
        """typed 拒绝（已取得资格时抛错——拒绝不是"可选的附加信息"）。"""
        self._assert_live()
        if self._refusal is None:
            raise LiveTableSourceError("该文档已取得图侧正式资格，没有拒绝记录")
        return self._refusal

    @property
    def snapshot(self):
        """真实 `FinalMaterialStructureSnapshot`（未取得资格时为 `None`）。"""
        self._assert_live()
        return self._snapshot

    @property
    def tables(self) -> tuple:
        """本快照的 `TableObjectV4` 成员（**仅**在已取得资格时可取）。

        `TableObjectV4` 本身**不是**数字权威（`is_financial_authority()` 恒 False），
        也不是"合格数字"；它是 §0.17.1 要求的**结构对象**。
        """
        self._assert_live()
        if self._verified is None:
            raise LiveTableSourceError(
                f"该文档未取得图侧正式资格（{self._refusal.refusal_kind}）："
                f"拒绝的表不得被放行给工具或 Pack（fail-closed）")
        return tuple(self._verified.snapshot.tables)

    @property
    def snapshot_tables(self) -> tuple:
        """**已构造**的 `TableObjectV4` 成员（未取得资格时**也可读**；`lts-2`）。

        这是 `lts-1`→`lts-2` 的那次**判定轴翻转**，因此单独有一条访问器而不是放宽
        `tables`：

        - `tables`（旧轴口径）仍只在**已取得资格**时可取 —— "拒绝的表不得被放行给工具
          或 Pack"这条规则一字未改；
        - 而 §0.19 要求逐表证明读**同一份图侧来源**上**已构造**的对象。逐表证明的判据
          里包含"这张表自己账不平"这一类，它必须在**没有得到文档级资格**的状态下也读得到，
          否则逐表证明就只能读"已经过了文档门"的表 —— 那等于没做逐表证明。

        因此本访问器是**只读诊断/证明读数**，不是资格：它返回对象，但**不**产生任何
        放行、**不**让 `verified` 可取、**不**改 `is_qualified`。快照未诞生（builder 自身
        抛错）时返回空元组 —— "没有对象可证明"是事实，不是空证明。
        """
        self._assert_live()
        if self._snapshot is None:
            return ()
        return tuple(self._snapshot.tables)

    def table_scope_readings(self) -> tuple:
        """本快照成员表在**同一份图侧来源**上的 §0.19 逐表范围读数（`lts-2`）。

        读数由 `final_material_builder.build_table_scope_readings` 在**同一个** TS4 能力
        上重算（原子分区判据只有那一处）。重算结果必须与快照成员**精确等集**：不等即
        fail-closed —— 那说明这份来源上"再构建一次"得到的表与快照里的表不是同一批，
        逐表证明也就无从谈起。
        """
        self._assert_live()
        if self._snapshot is None:
            raise LiveTableSourceError(
                "快照未诞生（builder 自身失败）：没有可证明的表对象，"
                "也不存在逐表范围读数（fail-closed）")
        readings = FMB.build_table_scope_readings(self._span_source.verified_snapshot)
        by_id = {r.table_id for r in readings}
        expected = {t.table_id for t in self._snapshot.tables}
        if by_id != expected:
            raise LiveTableSourceError(
                "逐表范围读数与快照成员不等集（缺 "
                f"{sorted(expected - by_id)}；多 {sorted(by_id - expected)}）——"
                "同一份来源上重算出的表与快照不是同一批（fail-closed）")
        order = {t.table_id: i for i, t in enumerate(self._snapshot.tables)}
        return tuple(sorted(readings, key=lambda r: order[r.table_id]))

    @property
    def document_id(self) -> str:
        self._assert_live()
        return self._request_identity["document_id"]

    @property
    def document_version(self) -> str:
        self._assert_live()
        return self._request_identity["document_version"]

    @property
    def span_source(self) -> LSS.LiveVerifiedSpanSource:
        self._assert_live()
        return self._span_source

    # -- 可持久化身份 -----------------------------------------------------

    def version_identities(self) -> dict:
        """本次组合实际使用的版本（由**产物自身**读出后与权威常量复核）。"""
        self._assert_live()
        _check_version("final_material_builder.builder_version",
                       FMB.BUILDER_VERSION, V.FINAL_MATERIAL_BUILDER_VERSION)
        _check_version("final_verifier.verifier_version",
                       FV.VERIFIER_VERSION,
                       V.VERIFIED_FINAL_MATERIAL_ISSUER_VERSION)
        _check_version("live_table_source_version", LIVE_TABLE_SOURCE_VERSION,
                       V.LIVE_TABLE_SOURCE_VERSION)
        verified_versions: dict = {}
        if self._verified is not None:
            snapshot = self._verified.snapshot
            _check_version("final_material_structure.schema_version",
                           snapshot.schema_version,
                           V.FINAL_MATERIAL_STRUCTURE_SCHEMA_VERSION)
            _check_version("final_material_structure.builder_version",
                           snapshot.builder_version,
                           V.FINAL_MATERIAL_BUILDER_VERSION)
            _check_version("verified_final_material.issuer_version",
                           self._verified.issuer_version, FV.VERIFIER_VERSION)
            verified_versions = {
                "final_material_snapshot_schema_version": snapshot.schema_version,
                "final_material_snapshot_builder_version": snapshot.builder_version,
                "verified_final_material_issuer_version":
                    self._verified.issuer_version,
                "verified_final_material_fingerprint":
                    self._verified.verification_fingerprint,
            }
        return {
            "live_table_source_version": LIVE_TABLE_SOURCE_VERSION,
            "final_material_builder_version": FMB.BUILDER_VERSION,
            "final_verifier_version": FV.VERIFIER_VERSION,
            **verified_versions,
        }

    def identity_projection(self) -> dict:
        """可持久化的完整身份投影（纯 JSON 安全值，不含任何运行时能力）。

        与 `live_span_source.identity_projection` 同样只是"这次组合**是什么**"的
        记录，不是"这次组合**有效**"的证明：读回它不产生资格，重新证明只能重走
        整条链（`reprove_live_table_source`）。
        """
        self._assert_live()
        projection = {
            "request": dict(self._request_identity),
            "versions": self.version_identities(),
            "qualified": self._verified is not None,
        }
        if self._verified is not None:
            snapshot = self._verified.snapshot
            projection["final_material"] = {
                "snapshot_locator": snapshot.snapshot_locator,
                "snapshot_id": snapshot.snapshot_id,
                "schema_version": snapshot.schema_version,
                "builder_version": snapshot.builder_version,
                "document_id": snapshot.document_id,
                "document_version": snapshot.document_version,
                "evidence_set_version": snapshot.evidence_set_version,
                "page_layout_id": snapshot.page_layout_id,
                "outline_id": snapshot.outline_id,
                "verified_span_snapshot_id": snapshot.verified_span_snapshot_id,
                "upstream_dependency_fingerprint":
                    snapshot.upstream_dependency_fingerprint,
                "content_fingerprint": snapshot.content_fingerprint,
                "table_count": snapshot.table_count,
                "table_ids": [t.table_id for t in snapshot.tables],
                "table_locators": [t.table_locator for t in snapshot.tables],
                "final_span_count": snapshot.final_span_count,
                "component_count": snapshot.component_count,
                "blocking_gap_count": snapshot.blocking_gap_count,
            }
            projection["verified"] = self._verified.identity()
        else:
            projection["refusal"] = self._refusal.to_dict()
        return projection

    # -- 反自证 -----------------------------------------------------------

    def to_dict(self) -> dict:
        raise LiveTableSourceError(
            "LiveTableSource 是运行时来源，不得序列化；持久化请用"
            " identity_projection()，从 JSON 恢复资格的做法不存在（fail-closed）")

    def __copy__(self):
        raise LiveTableSourceError("LiveTableSource 不可 copy")

    def __deepcopy__(self, memo):
        raise LiveTableSourceError("LiveTableSource 不可 deepcopy")

    def __reduce__(self):
        raise LiveTableSourceError("LiveTableSource 不可 pickle")

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        state = "qualified" if self._verified is not None \
            else f"refused:{self._refusal.refusal_kind}"
        return (f"<LiveTableSource {self._request_identity.get('document_id')!r} "
                f"{self._request_identity.get('document_version')!r} {state}>")


# ---------------------------------------------------------------------------
# 5. 公开入口
# ---------------------------------------------------------------------------

def _request_identity(span_source: LSS.LiveVerifiedSpanSource) -> dict:
    projection = span_source.identity_projection()
    return dict(projection["request"])


def build_live_table_source(span_source: Any) -> LiveTableSource:
    """**M930-3 唯一**图侧组合根：由同次 live TS4 能力取得图侧正式资格。

    只调用两个既有公开生产入口（builder 与 verifier），不 init / migrate / 写库，
    不读 Contract、不调 LLM / 网络。**文档级未取得资格不是异常**：它返回一个携带
    typed 拒绝的 `LiveTableSource`，由调用方在只读报告里逐份材料写出。
    """
    verified_span = _assert_live_span_source(span_source)
    identity = _request_identity(span_source)
    document_id = identity["document_id"]
    document_version = identity["document_version"]

    try:
        snapshot = FMB.build_final_material_snapshot(verified_span)
    except Exception as e:  # noqa: BLE001 - 原始异常逐字进拒绝记录，不吞掉
        refusal = LiveTableSourceRefusal(
            document_id=document_id, document_version=document_version,
            refusal_kind="final_material_build_failed",
            detail=f"{type(e).__name__}: {e}", error_type=type(e).__name__,
            blocking_kinds={}, layer_readings=(), problem_count=0,
            problem_examples=(), builder_table_count=0,
            builder_version=FMB.BUILDER_VERSION,
            verifier_version=FV.VERIFIER_VERSION,
            source_version=LIVE_TABLE_SOURCE_VERSION)
        return LiveTableSource(span_source=span_source, request_identity=identity,
                               refusal=refusal)

    if not isinstance(snapshot, TS.FinalMaterialStructureSnapshot):
        raise LiveTableSourceError(
            f"builder 的产出必须是 fms-1 快照，得到 {type(snapshot).__name__}"
            "（fail-closed；不接受任何其它形状的「通过」）")

    try:
        verified = FV.verify_final_material_snapshot(verified_span, snapshot)
    except FV.FinalVerificationError as e:
        # 三类拒绝**不互相顶替**：阻断性缺口、守恒不合格、结构比对不符各是一种
        # 事实，各自的 `kind` 逐条可读（`except ... as e` 在块尾会解绑 `e`，
        # 因此拒绝值在块内就地构造）。
        if isinstance(e, FV.FinalMaterialBlockedError):
            kind = "final_material_blocked"
        elif isinstance(e, FV.FinalMaterialConservationError):
            kind = "final_material_conservation_ineligible"
        else:
            kind = "final_verification_failed"
        return LiveTableSource(
            span_source=span_source, request_identity=identity, snapshot=snapshot,
            refusal=_refusal_from(snapshot, document_id, document_version,
                                  kind, e))
    source = LiveTableSource(span_source=span_source, request_identity=identity,
                             snapshot=snapshot, verified=verified)
    # 版本漂移闸门：任一 builder / verifier / 快照版本与权威常量不一致都在
    # **返回给调用方之前**失败。
    source.version_identities()
    return source


def reprove_live_table_source(span_source: Any, projection: dict
                              ) -> LiveTableSource:
    """从同次 live 能力**重走**图侧链，并要求 identity 与冻结投影相等。

    这是"重新证明图侧资格"的唯一合法方式：读回 JSON 不产生资格，只有再走一次
    并逐项比较才成立。不一致（源被替换、Evidence 集合变了、版本升了）即 fail-closed。
    """
    if not isinstance(projection, dict):
        raise LiveTableSourceError("冻结投影必须为对象")
    for key in ("request", "versions", "qualified"):
        if key not in projection:
            raise LiveTableSourceError(f"冻结投影缺必要分组 {key!r}（fail-closed）")
    fresh = build_live_table_source(span_source)
    current = fresh.identity_projection()
    if current != projection:
        drift = [k for k in sorted(set(current) | set(projection))
                 if current.get(k) != projection.get(k)]
        raise LiveTableSourceError(
            f"重新组合的图侧 identity 与冻结投影不一致，漂移分组：{drift}"
            f"（fail-closed；不得用旧投影继续）")
    return fresh


__all__ = [
    "GRAPH_TABLE_REFUSAL_KINDS", "LIVE_TABLE_SOURCE_VERSION",
    "LiveTableSource", "LiveTableSourceError", "LiveTableSourceRefusal",
    "build_live_table_source", "reprove_live_table_source",
]

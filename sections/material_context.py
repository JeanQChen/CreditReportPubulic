"""§三 A：Writer / Entailment Evaluator 的**真实材料正文**解析边界（`wmctx-1`）。

本模块回答一个此前**没有被回答**的问题：「Writer 手上这份材料，正文到底是什么，凭什么
说它是当前 PackSet 里那一份？」

在 `wmctx-1` 之前，`WriterMaterialManifestEntry` 只有 `material_id` + `content_hash`：
它们能证明「有一条材料」，**不能**证明「正文在场」。于是 P6（Pack→Writer）只到接口骨架：
LLM 拿到的是 ID 列表，Evaluator 拿到的也是 ID 列表，没有任何一环看到过正文。本模块把
「解析 → 校验 → 不可变上下文」做成**唯一**入口：

1. resolver 由组合根注入（`PayloadResolver`）；**缺 resolver 一律 typed fail-closed**，
   不得跳过当前 PackSet 重核、也不得静默降级成「只有 ID」；
2. 逐份当前材料解析 `payload_ref` → 真实 payload 字节 → 重算 payload 哈希、回查
   source/provenance/material identity/locator/version；
3. 产出**不可变、可序列化**的上下文：只记录可重算的稳定引用与指纹，
   **不含** resolver / DB 连接 / 可变 store 对象（内容身份必须能被任何人重算）。

`reading_view` 是 payload 正文的**抽取式**投影（去首尾空白、CRLF→LF），`structured_view`
是表格类 payload 的结构化读视图（逐字来自信封，不重排、不改写、不翻译）。两者都不新增
任何字符，因此「模型看过的正文」与「Evaluator 复核的正文」是同一串字节。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from harness import topic_schema as TS
from sections import narrative_schema as NS
from sections import pack_set as PSet

__all__ = [
    "MATERIAL_CONTEXT_VERSION",
    "MATERIAL_PAYLOAD_ENVELOPE_VERSION",
    "MATERIAL_CONTEXT_REASONS",
    "MaterialContextError",
    "ResolvedWriterMaterial",
    "WriterMaterialContext",
    "material_sort_key",
    "material_locator_ref",
    "normalize_reading_view",
    "pack_set_fingerprint",
    "authority_material_boundary_fingerprint",
    "empty_material_context_for_authority",
    "reading_for_manifest_member",
    "readings_for_manifest_members",
    "resolve_writer_material_context",
]

#: 上下文 wire 版本。字段增删/语义变化必须改名，旧对象只经 legacy reader 只读回放。
MATERIAL_CONTEXT_VERSION = "wmctx-1"
#: 材料 payload 信封版本（与 `harness.topic_store` 的 `material_payload_version` 同一口径）。
MATERIAL_PAYLOAD_ENVELOPE_VERSION = 1

#: 上下文无法建立时的**封闭**原因码（typed；不得塞进泛化字符串）。
MATERIAL_CONTEXT_REASONS = (
    "resolver_missing",
    "pack_set_not_verified",
    "pack_identity_unverifiable",
    "material_index_unverifiable",
    "payload_ref_missing",
    "payload_ref_unverifiable",
    "payload_envelope_invalid",
    "payload_text_empty",
    "material_identity_mismatch",
    #: §三 A.7/A.6：manifest 成员 ↔ 已解析正文的交叉复检失败（「清单里有 ID」不等于「正文在场」）。
    "member_not_in_context",
    "member_payload_mismatch",
    #: 非 Pack 权威（financial/note/external）的材料边界指纹算不出来：缺 `producer_kind`
    #: 或 `input_id`。这一条**只**服务于「本权威没有 Pack 材料边界」的情形，不覆盖上面任何一条。
    "material_boundary_unverifiable",
)


class MaterialContextError(Exception):
    """材料正文上下文无法建立（fail-closed）。

    `reason` 取 `MATERIAL_CONTEXT_REASONS`；`member_ref` 是出问题的 manifest 成员（可空）。
    """

    def __init__(self, message: str, *, reason: str, member_ref: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.member_ref = member_ref


# ---------------------------------------------------------------------------
# 读视图（抽取式投影，不新增字符）
# ---------------------------------------------------------------------------

def normalize_reading_view(text: Any) -> str:
    """payload 正文 → 读视图：只做换行归一并去首尾空白，不增删任何实词。"""
    return str(text if text is not None else "").replace("\r\n", "\n").replace("\r", "\n").strip()


def material_sort_key(material: "ResolvedWriterMaterial") -> tuple[str, str, str]:
    """`wmctx-1` 的**唯一**顺序口径：`topic_id → pack_id → material_id`。

    不得依赖 DB 行序或 dict 迭代序（§6.4.1 第 4 层）：同一 PackSet 的上下文指纹必须在任何
    进程、任何插入顺序下逐字节相同。
    """
    return (material.topic_id, material.pack_id, material.material_id)


def material_locator_ref(*, material: Any) -> dict:
    """material 的 exact locator（`loc-1` tagged union）。

    两种**显式区分**的变体，不再靠 owner 串去猜类型（旧的 `(owner, start, end)` 把「半开字符
    区间」与「闭块区间」挤进同一个形状，单块材料的 `(0, 0)` 因此无法表达）：

    * `block_range` + `first`/`last`：material 的 locator 带 `block_range` 时，区间就是它自己
      声明的**闭**块区间（单块 `first == last` 合法）；
    * `whole_payload`：其余情形只指向**整个载体**，**不声称**原文里的任何位置（与
      `pack_writer._external_locator_ref` 对 snapshot 载体给出的说明同一口径）。

    旧 wire 在 `whole_payload` 分支把 `len(payload_hash)` 塞进第三个位置，使一个**digest 长度**
    看起来像字符区间结束位——正是 §四 要消掉的「靠模糊 tuple 猜类型」。因此本函数不再接收
    `payload_hash`：载体摘要由边上**另外**的 `payload_hash` 字段承担，定位就是定位。

    owner 取 `TS.material_container_identity`（按 locator 变体确定性派生），不是自报串。
    """
    container = TS.material_container_identity(material)
    locator = material.locator
    block_range = getattr(locator, "block_range", None)
    if block_range:
        first, last = int(block_range[0]), int(block_range[1])
        if first <= last:
            return NS.block_range_locator(f"{container}#block_span", first, last)
    return NS.whole_payload_locator(f"{container}#whole_payload")


def _reading_view_fingerprint(*, payload_hash: str, object_type: str, reading_view: str,
                              structured_view: Mapping[str, Any] | None) -> str:
    """读视图指纹：**可重算**（同 payload + 同投影 ⇒ 同指纹）。"""
    return TS.sha256_canonical({
        "kind": "writer_material_reading_view",
        "object_type": str(object_type),
        "payload_hash": str(payload_hash),
        "reading_view": reading_view,
        "structured_view": dict(structured_view) if structured_view else None,
    })


def _checked_continuity(continuity: Any, *, member_ref: str) -> dict | None:
    """续接读法的**形状**校验（对象构造期；与 `_continuity_from_split` 同一组判据）。

    这里只钉形状，不重算来源：核心四项（片序 / 共片数 / 全文长度 / 本地区间）逐字来自 payload
    信封，邻居引用由上下文层按已固定成员集合补。读不懂即 fail-closed，不静默丢成「普通整段」。
    """
    if continuity is None:
        return None
    if not isinstance(continuity, Mapping):
        raise NS.NarrativeSchemaError(
            f"ResolvedWriterMaterial({member_ref}).span_continuity 只能是 mapping 或 None")
    try:
        shape = _continuity_from_split(
            {k: continuity.get(k) for k in
             ("span_id", "piece_index", "piece_count", "span_char_length",
              "span_local_char_range")},
            member_ref=member_ref)
    except MaterialContextError as exc:
        raise NS.NarrativeSchemaError(str(exc)) from exc
    for name in ("continued_from", "continued_in"):
        value = continuity.get(name)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise NS.NarrativeSchemaError(
                f"ResolvedWriterMaterial({member_ref}).span_continuity.{name} "
                "只能是非空 member_ref 字符串或 None")
        shape[name] = str(value) if value is not None else None
    return shape


# ---------------------------------------------------------------------------
# 单份材料的解析结果
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResolvedWriterMaterial:
    """一份**已解析正文**的当前 Pack material（不可变、可序列化）。

    所有字段都是可重算的稳定引用或指纹：没有 resolver、没有连接、没有 store 对象。
    """

    member_ref: str
    topic_id: str
    pack_id: str
    material_id: str
    material_type: str
    research_material_disposition_id: str
    source_identity: str
    provenance_identity: str
    locator_ref: dict
    payload_ref: dict
    payload_hash: str
    content_hash: str
    material_content_fingerprint: str
    reading_view: str
    reading_view_fingerprint: str
    structured_view: dict | None = None
    #: §二 2.3 的**内容形态**读法（`kind` + 表单行的 `selection`）。它是**派生**字段：
    #: 原件在 payload 里，而 `content_qualification` 本来就进 payload 哈希（`tree_materials`
    #: 写入时如此），所以它已被 `payload_hash` 钉住，不另立一条独立身份。它**不在**
    #: `identity_body()` 里（避免让既有 `context_id` 因读法增列而漂移），但**进**序列化，
    #: 因此落盘再读回时形态判定不会丢。`reading_view` 仍是**抽取式**原文，一格不改：
    #: 「模型看过的正文」与「Evaluator 复核的正文」必须还是同一串字节。
    content_qualification: dict | None = None
    #: **跨块切分读法**（同一 `OutlineSpan` 被多个 Evidence 块切开时才有）：逐字来自 payload
    #: 信封的 `span_split`（片序 / 共片数 / 全文长度 / 本片在 span 本地区的精确区间），外加由
    #: 本次**已固定**的成员集合解析出的 `continued_from` / `continued_in`（前一片 / 后一片的
    #: member_ref；不在本清单里时为 None）。与 `content_qualification` 同一纪律：**派生**字段、
    #: 已被 `payload_hash` 与上下文成员集合钉住、**不进** `identity_body()`、进序列化。
    #: 它**不**改变任何身份：每一片仍是自己的材料、自己的 locator、自己的 `reading_view`。
    span_continuity: dict | None = None

    def __post_init__(self) -> None:
        for name in ("member_ref", "topic_id", "pack_id", "material_id", "material_type",
                     "research_material_disposition_id", "source_identity", "provenance_identity",
                     "payload_hash", "content_hash", "material_content_fingerprint",
                     "reading_view", "reading_view_fingerprint"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise NS.NarrativeSchemaError(
                    f"ResolvedWriterMaterial.{name} 必须是非空字符串")
        for name in ("payload_hash", "content_hash", "material_content_fingerprint",
                     "reading_view_fingerprint"):
            if not NS._is_sha256_hex(getattr(self, name)):
                raise NS.NarrativeSchemaError(
                    f"ResolvedWriterMaterial.{name} 必须是 64 位 sha256 hex")
        expected_ref = NS.manifest_member_ref(self.pack_id, self.material_id)
        if self.member_ref != expected_ref:
            raise NS.NarrativeSchemaError(
                f"ResolvedWriterMaterial.member_ref 与容器/材料身份不符：声明 "
                f"{self.member_ref!r}，应为 {expected_ref!r}")
        locator = NS.validate_locator(self.locator_ref, "ResolvedWriterMaterial")
        if locator is None:
            raise NS.NarrativeSchemaError(
                "ResolvedWriterMaterial.locator_ref 不得为空（已解析的材料必须有 exact locator）")
        object.__setattr__(self, "locator_ref", locator)
        if not isinstance(self.payload_ref, Mapping) or not self.payload_ref:
            raise NS.NarrativeSchemaError(
                "ResolvedWriterMaterial.payload_ref 必须是规范 `MaterialPayloadRef` dict"
                "（不得用裸 id 代替可解析引用）")
        canonical = TS.MaterialPayloadRef.from_dict(dict(self.payload_ref)).to_dict()
        if canonical != dict(self.payload_ref):
            raise NS.NarrativeSchemaError(
                "ResolvedWriterMaterial.payload_ref 不是规范序列化（字段/值必须能被正式 reader 往返）")
        if canonical["content_hash"] != self.payload_hash:
            raise NS.NarrativeSchemaError(
                "ResolvedWriterMaterial.payload_hash 与 payload_ref.content_hash 不一致")
        if self.structured_view is not None and not isinstance(self.structured_view, Mapping):
            raise NS.NarrativeSchemaError(
                "ResolvedWriterMaterial.structured_view 只能是 mapping 或 None")
        object.__setattr__(self, "structured_view",
                           dict(self.structured_view) if self.structured_view else None)
        if self.content_qualification is not None and not isinstance(
                self.content_qualification, Mapping):
            raise NS.NarrativeSchemaError(
                "ResolvedWriterMaterial.content_qualification 只能是 mapping 或 None")
        qualification = dict(self.content_qualification) if self.content_qualification else None
        if qualification is not None:
            from harness import tree_materials as TM  # 延迟导入：词表只有一份
            kind = str(qualification.get("kind", "") or "")
            if kind not in TM.TREE_MATERIAL_CONTENT_KINDS:
                raise NS.NarrativeSchemaError(
                    f"ResolvedWriterMaterial.content_qualification.kind 不在封闭词表内：{kind!r}")
            if qualification.get("is_material") is False:
                raise NS.NarrativeSchemaError(
                    "ResolvedWriterMaterial.content_qualification 声明「不是材料」，"
                    "但本对象只承载已成为材料的正文")
            if kind == "selection_form" and not isinstance(
                    qualification.get("selection"), Mapping):
                raise NS.NarrativeSchemaError(
                    "content_qualification.kind='selection_form' 必须带 selection 结构化读法"
                    "（表单行不得只留一个形态名）")
        object.__setattr__(self, "content_qualification", qualification)
        object.__setattr__(self, "span_continuity", _checked_continuity(
            self.span_continuity, member_ref=self.member_ref))
        expected_fp = _reading_view_fingerprint(
            payload_hash=self.payload_hash, object_type=self.material_type,
            reading_view=self.reading_view, structured_view=self.structured_view)
        if self.reading_view_fingerprint != expected_fp:
            raise NS.NarrativeSchemaError(
                "ResolvedWriterMaterial.reading_view_fingerprint 与读视图不符（正文被改写）")

    def identity_body(self) -> dict:
        return {
            "member_ref": self.member_ref, "topic_id": self.topic_id, "pack_id": self.pack_id,
            "material_id": self.material_id, "material_type": self.material_type,
            "research_material_disposition_id": self.research_material_disposition_id,
            "source_identity": self.source_identity, "provenance_identity": self.provenance_identity,
            "locator_ref": dict(self.locator_ref), "payload_ref": dict(self.payload_ref),
            "payload_hash": self.payload_hash, "content_hash": self.content_hash,
            "material_content_fingerprint": self.material_content_fingerprint,
            "reading_view": self.reading_view,
            "reading_view_fingerprint": self.reading_view_fingerprint,
            "structured_view": self.structured_view,
        }

    def to_dict(self) -> dict:
        # `content_qualification` 与 `span_continuity` **进序列化但不进 `identity_body()`**：
        # 它们由 payload 承载、已被 `payload_hash`（以及上下文成员集合）钉住，另立一条身份只会
        # 让既有 `context_id` 无谓漂移。
        return {**self.identity_body(),
                "content_qualification": self.content_qualification,
                "span_continuity": self.span_continuity}

    @classmethod
    def create(cls, **kwargs: Any) -> "ResolvedWriterMaterial":
        payload_hash = str(kwargs.get("payload_hash", "") or "")
        body = {
            "member_ref": str(kwargs.get("member_ref", "") or ""),
            "topic_id": str(kwargs.get("topic_id", "") or ""),
            "pack_id": str(kwargs.get("pack_id", "") or ""),
            "material_id": str(kwargs.get("material_id", "") or ""),
            "material_type": str(kwargs.get("material_type", "") or ""),
            "research_material_disposition_id": str(
                kwargs.get("research_material_disposition_id", "") or ""),
            "source_identity": str(kwargs.get("source_identity", "") or ""),
            "provenance_identity": str(kwargs.get("provenance_identity", "") or ""),
            "locator_ref": kwargs.get("locator_ref"),
            "payload_ref": dict(kwargs.get("payload_ref") or {}),
            "payload_hash": payload_hash,
            "content_hash": str(kwargs.get("content_hash", "") or ""),
            "material_content_fingerprint": str(
                kwargs.get("material_content_fingerprint", "") or ""),
            "reading_view": str(kwargs.get("reading_view", "") or ""),
            "structured_view": kwargs.get("structured_view"),
            "content_qualification": kwargs.get("content_qualification"),
            "span_continuity": kwargs.get("span_continuity"),
        }
        body["reading_view_fingerprint"] = _reading_view_fingerprint(
            payload_hash=payload_hash, object_type=body["material_type"],
            reading_view=body["reading_view"], structured_view=body["structured_view"])
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "ResolvedWriterMaterial":
        d = NS._reject_unknown(d, {
            "member_ref", "topic_id", "pack_id", "material_id", "material_type",
            "research_material_disposition_id", "source_identity", "provenance_identity",
            "locator_ref", "payload_ref", "payload_hash", "content_hash",
            "material_content_fingerprint", "reading_view", "reading_view_fingerprint",
            "structured_view", "content_qualification", "span_continuity"},
            "ResolvedWriterMaterial")
        return cls(
            member_ref=str(d.get("member_ref") or ""),
            topic_id=str(d.get("topic_id") or ""),
            pack_id=str(d.get("pack_id") or ""),
            material_id=str(d.get("material_id") or ""),
            material_type=str(d.get("material_type") or ""),
            research_material_disposition_id=str(
                d.get("research_material_disposition_id") or ""),
            source_identity=str(d.get("source_identity") or ""),
            provenance_identity=str(d.get("provenance_identity") or ""),
            locator_ref=d.get("locator_ref"),
            payload_ref=dict(d.get("payload_ref") or {}),
            payload_hash=str(d.get("payload_hash") or ""),
            content_hash=str(d.get("content_hash") or ""),
            material_content_fingerprint=str(d.get("material_content_fingerprint") or ""),
            reading_view=str(d.get("reading_view") or ""),
            reading_view_fingerprint=str(d.get("reading_view_fingerprint") or ""),
            structured_view=d.get("structured_view"),
            content_qualification=d.get("content_qualification"),
            span_continuity=d.get("span_continuity"))


# ---------------------------------------------------------------------------
# 上下文（一次写作消费的完整正文集合）
# ---------------------------------------------------------------------------

def _derive_context_id(context: "WriterMaterialContext") -> str:
    return _derive_context_id_for(
        schema_version=context.schema_version, task_id=context.task_id,
        section_id=context.section_id, pack_set_fingerprint=context.pack_set_fingerprint,
        materials=context.materials)


def _derive_context_id_for(*, schema_version: str, task_id: str, section_id: str,
                          pack_set_fingerprint: str, materials: Sequence[Any]) -> str:
    """`context_id` 的**唯一**派生（按字段而不是按实例：构造期还没有合法实例）。"""
    return NS.content_id("wmctx_", {
        "schema_version": schema_version, "task_id": task_id, "section_id": section_id,
        "pack_set_fingerprint": pack_set_fingerprint,
        "materials": [m.identity_body() for m in materials],
    })


@dataclass(frozen=True)
class WriterMaterialContext:
    """本次写作消费的**精确材料正文**上下文（available 的正文侧）。

    与 `WriterMaterialManifest`（身份侧）一一对应：manifest 的每个成员在上下文里有**恰好
    一条**正文记录，反之亦然。两者都不允许空集冒充「有材料」：空上下文只对没有 Pack 材料
    边界的 producer kind 合法（此时 manifest 也必须为空）。
    """

    context_id: str
    schema_version: str
    task_id: str
    section_id: str
    pack_set_fingerprint: str
    materials: tuple[ResolvedWriterMaterial, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != MATERIAL_CONTEXT_VERSION:
            raise NS.NarrativeSchemaError(
                f"WriterMaterialContext.schema_version 必须为 {MATERIAL_CONTEXT_VERSION!r}")
        for name in ("task_id", "section_id", "pack_set_fingerprint"):
            if not str(getattr(self, name) or "").strip():
                raise NS.NarrativeSchemaError(f"WriterMaterialContext.{name} 必须非空")
        materials = tuple(self.materials or ())
        for material in materials:
            if not isinstance(material, ResolvedWriterMaterial):
                raise NS.NarrativeSchemaError(
                    "WriterMaterialContext.materials 只能由 ResolvedWriterMaterial 构成，"
                    f"得到 {type(material).__name__}")
        object.__setattr__(self, "materials", materials)
        refs = [m.member_ref for m in materials]
        if len(set(refs)) != len(refs):
            raise NS.NarrativeSchemaError(
                "WriterMaterialContext 含重复成员（按 container+material 身份去重）")
        canonical = tuple(sorted(materials, key=material_sort_key))
        if materials != canonical:
            raise NS.NarrativeSchemaError(
                "WriterMaterialContext.materials 必须是 (topic_id, pack_id, material_id) 的"
                f"规范顺序：得到 {[material_sort_key(m) for m in materials]}，"
                f"应为 {[material_sort_key(m) for m in canonical]}")
        expected = _derive_context_id(self)
        if self.context_id != expected:
            raise NS.NarrativeSchemaError(
                f"WriterMaterialContext.context_id 与内容不符：声明 {self.context_id!r}，"
                f"应为 {expected!r}")

    def member_refs(self) -> tuple[str, ...]:
        return tuple(m.member_ref for m in self.materials)

    def material_for(self, member_ref: str) -> ResolvedWriterMaterial | None:
        for material in self.materials:
            if material.member_ref == member_ref:
                return material
        return None

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version, "task_id": self.task_id,
            "section_id": self.section_id, "pack_set_fingerprint": self.pack_set_fingerprint,
            "materials": [m.identity_body() for m in self.materials],
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(
            NS.canonical_json(self.identity_body()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {"context_id": self.context_id, "context_fingerprint": self.fingerprint(),
                **self.identity_body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "WriterMaterialContext":
        raw = kwargs.get("materials") or ()
        materials = tuple(
            ResolvedWriterMaterial.from_dict(x)
            if isinstance(x, Mapping) and "member_ref" in x and "reading_view" in x else x
            for x in raw)
        body = {
            "schema_version": MATERIAL_CONTEXT_VERSION,
            "task_id": str(kwargs.get("task_id", "") or ""),
            "section_id": str(kwargs.get("section_id", "") or ""),
            "pack_set_fingerprint": str(kwargs.get("pack_set_fingerprint", "") or ""),
            "materials": materials,
        }
        body["context_id"] = _derive_context_id_for(
            schema_version=body["schema_version"], task_id=body["task_id"],
            section_id=body["section_id"],
            pack_set_fingerprint=body["pack_set_fingerprint"], materials=materials)
        return cls(**body)

    @classmethod
    def from_dict(cls, d: Any) -> "WriterMaterialContext":
        d = NS._reject_unknown(d, {"context_id", "context_fingerprint", "schema_version",
                                   "task_id", "section_id", "pack_set_fingerprint", "materials"},
                               "WriterMaterialContext")
        return cls(
            context_id=str(d.get("context_id") or ""),
            schema_version=d.get("schema_version"),
            task_id=str(d.get("task_id") or ""),
            section_id=str(d.get("section_id") or ""),
            pack_set_fingerprint=str(d.get("pack_set_fingerprint") or ""),
            materials=tuple(ResolvedWriterMaterial.from_dict(x)
                            for x in (d.get("materials") or ())))


# ---------------------------------------------------------------------------
# 解析（组合根注入 resolver 的唯一入口）
# ---------------------------------------------------------------------------

def pack_set_fingerprint(pack_set: Any) -> str:
    """当前 PackSet 的**内容**指纹：`(pack_id, content_fingerprint)` 的规范集合。

    不含 DB 路径 / 连接 / 时间戳：任何人拿同一份 PackSet 都能重算出同一个值。
    """
    pairs = sorted(
        (str(getattr(pack, "pack_id", "") or ""), str(pack.content_fingerprint()))
        for pack in tuple(getattr(pack_set, "packs", ()) or ()))
    return TS.sha256_canonical({"kind": "verified_pack_set", "packs": [
        {"pack_id": pid, "content_fingerprint": fp} for pid, fp in pairs]})


def authority_material_boundary_fingerprint(authority: Any) -> str:
    """本节权威的**材料边界**指纹——`WriterMaterialContext` 与输入清单**共用同一个值**。

    两条分支，各说各的边界，不做「统一字段」式的伪统一：

    * **topic Pack 权威**：边界就是那一份 `VerifiedPackSet`，取值等于
      `pack_set_fingerprint(authority.pack_set)`——**逐字节不变**，历史身份不受影响；
    * **其他 producer kind**（financial / note / external）：这些权威**没有** exact
      `ResearchMaterial` 边界（财务用 artifact + 附注、外部用 snapshot/body hash），边界就是
      **空集 + 「这是哪一份权威」**。取值带自己的 `kind` 与非空 `input_id`，因此它**永远**不会
      与任何真实 PackSet 指纹撞值，同时仍可只凭权威对象本身在任意进程重算出来。

    不得为财务造一个假 `VerifiedPackSet` 来复用同一条指纹路径：那样「本节材料边界为空」这件
    事实会被一个假身份盖住，读回侧再也看不出「这一节本来就没有 Pack 材料」。
    """
    pack_set = getattr(authority, "pack_set", None)
    if pack_set is not None:
        return pack_set_fingerprint(pack_set)
    producer_kind = str(getattr(authority, "producer_kind", "") or "")
    input_id = str(getattr(authority, "input_id", "") or "")
    if not producer_kind or not input_id:
        raise MaterialContextError(
            "非 Pack 权威缺 `producer_kind`/`input_id`：材料边界指纹（空材料边界）无法重算，"
            "不得退化成「用类型名凑一个」",
            reason="material_boundary_unverifiable")
    return TS.sha256_canonical({
        "kind": "no_pack_material_boundary",
        "producer_kind": producer_kind,
        "authority_input_id": input_id,
        "task_id": str(getattr(authority, "task_id", "") or ""),
        "section_id": str(getattr(authority, "section_id", "") or ""),
    })


def empty_material_context_for_authority(authority: Any) -> "WriterMaterialContext":
    """**非 Pack 权威**的合法空材料正文上下文（财务/附注/外部这一支的唯一入口）。

    为什么必须存在这一支：`resolve_writer_material_context` 要求一个真实 `VerifiedPackSet`，
    而财务节的权威是 `FinancialAuthorityInput`（artifact + 附注事实），它**没有** Pack 材料。
    把「没有材料」表达成「没有任何上下文对象」是不行的——`cited_writer.build_cited_writer_input`
    对 `material_context=None` 是 fail-closed，那会把财务整节挡在门外；反过来伪造一份
    `VerifiedPackSet` 又是在**发明**权威。空上下文是第三种、也是正确的一种取值：
    `pack_set_fingerprint` 带上「本权威没有 Pack 材料边界」这个 kind（见上），`materials` 为空。

    空集**不是**「无材料也照样写」的许可：这一支只对 topic Pack 之外的 producer kind 合法，
    且 `pack_writer._derive_material_manifest` 对「非 Pack 权威却携带材料」是 fail-closed，
    双向都堵住。
    """
    pack_set = getattr(authority, "pack_set", None)
    if pack_set is not None:
        raise MaterialContextError(
            "topic Pack 权威不得走空材料上下文：它的材料集合由当前 PackSet 唯一决定，"
            "必须经 `resolve_writer_material_context` 逐份解析真实正文",
            reason="pack_set_not_verified")
    return WriterMaterialContext.create(
        task_id=str(getattr(authority, "task_id", "") or ""),
        section_id=str(getattr(authority, "section_id", "") or ""),
        pack_set_fingerprint=authority_material_boundary_fingerprint(authority),
        materials=())


def _decode_envelope(*, resolved: Any, material: Any,
                     member_ref: str) -> tuple[dict, dict | None, dict | None]:
    """解析 payload 信封并做**材料侧**回查（信封层完整性由 resolver 负责，见模块 docstring）。

    返回 ``(content, content_qualification, span_split)``：正文、**内容形态读法**与
    **跨块切分读法**。后两者旧版都被丢在这一层：导致「这份材料是勾选表单行」与「这份材料是
    同一段原文的第几片」都到不了 Writer 与任何门。现在它们随正文一起返回，但仍只作派生读法
    ——`reading_view` 依旧是抽取式原文，一个字符都不改、**不合并**任何两片。
    """
    payload_ref = material.payload_ref
    try:
        envelope = json.loads(bytes(resolved.payload_bytes).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise MaterialContextError(
            f"材料 {member_ref} 的 payload 信封不是合法 JSON：{exc}",
            reason="payload_envelope_invalid", member_ref=member_ref) from exc
    if not isinstance(envelope, Mapping):
        raise MaterialContextError(
            f"材料 {member_ref} 的 payload 信封不是对象",
            reason="payload_envelope_invalid", member_ref=member_ref)
    if envelope.get("material_payload_version") != MATERIAL_PAYLOAD_ENVELOPE_VERSION:
        raise MaterialContextError(
            f"材料 {member_ref} 的 material_payload_version 不是 "
            f"{MATERIAL_PAYLOAD_ENVELOPE_VERSION}（fail-closed，不猜旧格式）",
            reason="payload_envelope_invalid", member_ref=member_ref)
    if str(envelope.get("object_type") or "") != str(material.material_type):
        raise MaterialContextError(
            f"材料 {member_ref} 的信封 object_type 与 material_type 不符",
            reason="payload_envelope_invalid", member_ref=member_ref)
    if str(envelope.get("authority_identity") or "") != str(material.source_identity):
        raise MaterialContextError(
            f"材料 {member_ref} 的信封 authority_identity 与 material.source_identity 不符",
            reason="material_identity_mismatch", member_ref=member_ref)
    if not str(envelope.get("evidence_id") or ""):
        raise MaterialContextError(
            f"材料 {member_ref} 的信封缺 evidence_id（来源身份不可回查）",
            reason="payload_envelope_invalid", member_ref=member_ref)
    # 信封的 `source_content_hash` 是**来源层**身份（真实链上 = 父 Evidence 块的 content_hash，
    # 见 `harness/topic_materials.py` 与 `harness/topic_store.py`），而 `material.content_hash`
    # 与 `payload_ref.content_hash` 是**载体层**身份（= payload 字节哈希）。两者必须各自对上，
    # 不得拿一个当另一个：把它们混为一谈，等于用「存储层哈希」冒充「来源层身份」。
    source_identity_layer = str(
        getattr(getattr(material, "authority_assessment", None), "content_hash", "") or "")
    if not source_identity_layer:
        raise MaterialContextError(
            f"材料 {member_ref} 的 authority_assessment 缺 content_hash（来源层身份不可回查）",
            reason="material_identity_mismatch", member_ref=member_ref)
    if str(envelope.get("source_content_hash") or "") != source_identity_layer:
        raise MaterialContextError(
            f"材料 {member_ref} 的信封 source_content_hash 与权威评估的来源层 content_hash 不符"
            "（该 material 不是这份 payload 的来源层身份）",
            reason="material_identity_mismatch", member_ref=member_ref)
    content = envelope.get("content")
    if not isinstance(content, Mapping):
        raise MaterialContextError(
            f"材料 {member_ref} 的信封缺 content（无正文）",
            reason="payload_envelope_invalid", member_ref=member_ref)
    # §二 2.3：信封里的**内容形态**读法（勾选表单行的所问事项/选项/选中状态就在这里）。
    # 它此前被丢在这一层，于是 Writer 与所有门都看不见表单行与普通正文的分界——材料仍然
    # 是材料，但「它是什么形态、最多能证明什么」必须随正文一起到达读的人手上。
    qualification = envelope.get("content_qualification")
    if qualification is not None and not isinstance(qualification, Mapping):
        raise MaterialContextError(
            f"材料 {member_ref} 的 content_qualification 不是对象",
            reason="payload_envelope_invalid", member_ref=member_ref)
    # 跨块切分读法：同一 `OutlineSpan` 每跨一个 Evidence 块就产出一份材料，切片形状写进信封的
    # `span_split`（第几片 / 共几片 / 全文长度 / 本片在 span 本地区的精确区间）。它**逐字**来自
    # payload（因此已被 payload_hash 钉住），本层只搬运——不重排、不合并、不伪造 locator。
    split = envelope.get("span_split")
    if split is not None and not isinstance(split, Mapping):
        raise MaterialContextError(
            f"材料 {member_ref} 的 span_split 不是对象",
            reason="payload_envelope_invalid", member_ref=member_ref)
    del payload_ref
    return (dict(content),
            (dict(qualification) if qualification is not None else None),
            (dict(split) if split is not None else None))


def _continuity_from_split(split: Mapping[str, Any] | None, *, member_ref: str) -> dict | None:
    """信封的 `span_split` → **续接读法**（片序 + 本地区间；邻居引用由上下文层补）。

    片序形状先在这里钉死（读不懂即 fail-closed，不降级成「普通整段材料」）：

    * `span_id` 非空串、`piece_index ≥ 1`、`piece_count ≥ 2`、`piece_index ≤ piece_count`；
    * `span_char_length ≥ 1`；`span_local_char_range = [a, b)` 且 `0 ≤ a < b ≤ span_char_length`。

    `continued_from` / `continued_in` 由 :func:`_link_span_pieces` 按**同一份上下文**的成员集合
    确定性补上：邻居不在本次清单里时留 ``None``（结论是「下一片不在本清单」，不是「没有下一片」，
    后者由 `piece_index < piece_count` 表达）。
    """
    if split is None:
        return None
    span_id = str(split.get("span_id", "") or "")
    piece_index = split.get("piece_index")
    piece_count = split.get("piece_count")
    span_char_length = split.get("span_char_length")
    local_range = split.get("span_local_char_range")
    if not span_id:
        raise MaterialContextError(
            f"材料 {member_ref} 的 span_split 缺 span_id（切分身份不可回查）",
            reason="payload_envelope_invalid", member_ref=member_ref)
    if not isinstance(piece_index, int) or not isinstance(piece_count, int) \
            or isinstance(piece_index, bool) or isinstance(piece_count, bool):
        raise MaterialContextError(
            f"材料 {member_ref} 的 span_split 片序不是整数",
            reason="payload_envelope_invalid", member_ref=member_ref)
    if piece_count < 2 or piece_index < 1 or piece_index > piece_count:
        raise MaterialContextError(
            f"材料 {member_ref} 的 span_split 片序不成立：{piece_index}/{piece_count}"
            "（只有真正被切开的材料才带这个键，共片数必须 ≥ 2）",
            reason="payload_envelope_invalid", member_ref=member_ref)
    if not isinstance(span_char_length, int) or span_char_length < 1:
        raise MaterialContextError(
            f"材料 {member_ref} 的 span_split 缺全文长度",
            reason="payload_envelope_invalid", member_ref=member_ref)
    if not isinstance(local_range, (list, tuple)) or len(local_range) != 2:
        raise MaterialContextError(
            f"材料 {member_ref} 的 span_split 缺本地区间",
            reason="payload_envelope_invalid", member_ref=member_ref)
    start, end = local_range[0], local_range[1]
    if not isinstance(start, int) or not isinstance(end, int) \
            or not (0 <= start < end <= span_char_length):
        raise MaterialContextError(
            f"材料 {member_ref} 的 span_split 本地区间不成立：{[start, end]}"
            f"（须落在 [0, {span_char_length}] 内且非空）",
            reason="payload_envelope_invalid", member_ref=member_ref)
    return {
        "span_id": span_id,
        "piece_index": piece_index,
        "piece_count": piece_count,
        "span_char_length": span_char_length,
        "span_local_char_range": [start, end],
        "continued_from": None,
        "continued_in": None,
    }


def _link_span_pieces(materials: Sequence["ResolvedWriterMaterial"]) -> None:
    """把同一 `span_id` 的各片按 `piece_index` 串起来（**就地**改写派生字段）。

    链接只用本次上下文里**已经固定**的成员集合：每一片只指自己**前一片 / 后一片**的
    `member_ref`。这不制造新身份、不改 locator、不合并正文——`reading_view` 仍是各片自己的
    抽取式原文，每一个 locator 仍只指它自己那一段在原件上的位置。
    """
    by_span: dict[str, list[Any]] = {}
    for material in materials:
        continuity = getattr(material, "span_continuity", None)
        if continuity:
            by_span.setdefault(str(continuity["span_id"]), []).append(material)
    for pieces in by_span.values():
        ordered = sorted(pieces, key=lambda m: int(m.span_continuity["piece_index"]))
        for position, material in enumerate(ordered):
            continuity = dict(material.span_continuity)
            continuity["continued_from"] = (
                ordered[position - 1].member_ref if position > 0 else None)
            continuity["continued_in"] = (
                ordered[position + 1].member_ref if position + 1 < len(ordered) else None)
            object.__setattr__(material, "span_continuity", continuity)


def resolve_writer_material_context(*, pack_set: Any, resolver: Any, task_id: str = "",
                                    section_id: str = "") -> WriterMaterialContext:
    """当前 `VerifiedPackSet` + 注入的 `PayloadResolver` → 不可变正文上下文（**唯一**入口）。

    逐份材料 fail-closed：dangling / 哈希不符 / locator 不符 / 身份不符 / 信封损坏 / 正文为
    空，任何一条都抛出 `MaterialContextError`（typed），**不**产出「只有 ID 的上下文」。
    """
    from sections import pack_writer as PW  # 延迟导入：共享同一份材料配对实现（无环）

    if not isinstance(pack_set, PSet.VerifiedPackSet):
        raise MaterialContextError(
            "解析材料正文必须拿到真实 `VerifiedPackSet`（字段形状替身一律不得进入权威输入）",
            reason="pack_set_not_verified")
    if resolver is None:
        raise MaterialContextError(
            "缺 payload resolver：材料正文无法解析。topic authority 必须由组合根注入"
            "`PayloadResolver`，不得静默降级成「只有 material ID」的写作",
            reason="resolver_missing")
    if not str(task_id or "").strip():
        task_id = str(getattr(pack_set, "task_id", "") or "")
    if not str(section_id or "").strip():
        section_id = str(getattr(pack_set, "section_id", "") or "")

    resolved_materials: list[ResolvedWriterMaterial] = []
    packs = sorted(tuple(getattr(pack_set, "packs", ()) or ()),
                   key=lambda p: (str(getattr(p, "topic_id", "") or ""),
                                  str(getattr(p, "pack_id", "") or "")))
    for pack in packs:
        pack_id = str(getattr(pack, "pack_id", "") or "")
        topic_id = str(getattr(pack, "topic_id", "") or "")
        try:
            pack.verify_pack_id()
        except Exception as exc:  # noqa: BLE001 - 任何 pack 身份失败都 fail-closed
            raise MaterialContextError(
                f"pack {pack_id!r} 身份重算失败：{exc}",
                reason="pack_identity_unverifiable") from exc
        try:
            index = PW._pack_material_index(pack)
        except Exception as exc:  # noqa: BLE001 - 材料/RMD 不配对即 fail-closed
            raise MaterialContextError(
                f"pack {pack_id!r} 的 materials × ResearchMaterialDisposition 无法配对：{exc}",
                reason="material_index_unverifiable") from exc
        for material_id in sorted(index):
            material, rmd = index[material_id]
            member_ref = NS.manifest_member_ref(pack_id, material_id)
            resolved_materials.append(_resolve_one(
                material=material, rmd=rmd, resolver=resolver, member_ref=member_ref,
                topic_id=topic_id, pack_id=pack_id, material_id=material_id))
    # 全部成员就位后**一次**把同 span 的各片串起来：邻居引用要用到本次清单的完整成员集合，
    # 逐份解析时还看不到。串接不产生新身份、不改 locator、不合并正文（见 `_link_span_pieces`）。
    _link_span_pieces(resolved_materials)
    return WriterMaterialContext.create(
        task_id=task_id, section_id=section_id,
        pack_set_fingerprint=pack_set_fingerprint(pack_set),
        materials=tuple(resolved_materials))


def _qualification_view(qualification: Mapping[str, Any] | None, *, locator: Any,
                        source_identity: str, member_ref: str) -> dict | None:
    """信封里的内容形态 → 随正文一起交给读的人的**派生**读法（§二 2.3）。

    只有两条规则，都是**形状**规则（无公司 / 文档 / 页码依赖）：

    * `kind` 必须落在封闭词表 `TREE_MATERIAL_CONTENT_KINDS` 内，否则 fail-closed——
      形态名是从 payload 读来的，读不懂就不许往下走，而不是当成普通正文放行；
    * `selection_form` 必须带**已判定**的 selection，并由 `selection_form_columns` 摊成六列
      （所问事项 / 选项 / 选中状态 / 所在节点 / 来源 / 尾随内容 `trailing_content`）
      + 允许用途与排除项。第六列是选项串之后**这一行管着的那句话**，随行留档但
      **不得**用作支撑（`TREE_MATERIAL_SELECTION_EXCLUSIONS` 明文禁止）。本函数**不**在这里
      追加任何判定：列怎么摊由 `tree_materials` 一处决定，改在第二处就会有两个真值。
      读不定的表单行根本不是材料，走到这里还读不定即 fail-closed。

    `is_material=True` 是事实陈述（它确实经人口筛选进来了），不是许可：许可在
    `permitted_use` 与 `exclusions` 两列里逐条写明。
    """
    if qualification is None:
        return None
    from harness import tree_materials as TM

    kind = str(qualification.get("kind", "") or "")
    if kind not in TM.TREE_MATERIAL_CONTENT_KINDS:
        raise MaterialContextError(
            f"材料 {member_ref} 的 content_qualification.kind 不在封闭词表内：{kind!r}",
            reason="payload_envelope_invalid", member_ref=member_ref)
    view: dict = {"kind": kind, "is_material": True}
    selection = qualification.get("selection")
    if selection is None:
        if kind == "selection_form":
            raise MaterialContextError(
                f"材料 {member_ref} 被标为勾选表单行，但信封里没有可判定的 selection"
                "（表单行不得只留一个形态名）",
                reason="payload_envelope_invalid", member_ref=member_ref)
        return view
    if not isinstance(selection, Mapping):
        raise MaterialContextError(
            f"材料 {member_ref} 的 content_qualification.selection 不是对象",
            reason="payload_envelope_invalid", member_ref=member_ref)
    columns = TM.selection_form_columns(
        selection=selection,
        locator=(locator.to_dict() if hasattr(locator, "to_dict") else dict(locator or {})),
        source_identity=source_identity)
    if columns is None:
        raise MaterialContextError(
            f"材料 {member_ref} 的勾选表单行状态未判定（resolved=false）却进了材料人口",
            reason="payload_envelope_invalid", member_ref=member_ref)
    view["selection"] = columns
    return view


def _resolve_one(*, material: Any, rmd: Any, resolver: Any, member_ref: str, topic_id: str,
                 pack_id: str, material_id: str) -> ResolvedWriterMaterial:
    payload_ref = getattr(material, "payload_ref", None)
    if not isinstance(payload_ref, TS.MaterialPayloadRef):
        raise MaterialContextError(
            f"材料 {member_ref} 缺真实 `MaterialPayloadRef`（裸 id / 字符串引用不可解析正文）",
            reason="payload_ref_missing", member_ref=member_ref)
    if str(getattr(material, "material_type", "") or "") != str(payload_ref.object_type):
        raise MaterialContextError(
            f"材料 {member_ref} 的 material_type 与 payload_ref.object_type 不符",
            reason="material_identity_mismatch", member_ref=member_ref)
    if str(getattr(material, "source_identity", "") or "") != str(payload_ref.authority_identity):
        raise MaterialContextError(
            f"材料 {member_ref} 的 source_identity 与 payload_ref.authority_identity 不符"
            "（两者必须落在同一身份域）",
            reason="material_identity_mismatch", member_ref=member_ref)
    locator = getattr(material, "locator", None)
    if locator is None or locator.to_dict() != payload_ref.locator.to_dict():
        raise MaterialContextError(
            f"材料 {member_ref} 的 locator 与 payload_ref.locator 不符",
            reason="material_identity_mismatch", member_ref=member_ref)
    provenance = str(getattr(rmd, "provenance_identity", "") or "")
    expected_provenance = TS.material_provenance_identity(material)
    if provenance != expected_provenance:
        raise MaterialContextError(
            f"材料 {member_ref} 的 Pack 侧去向 provenance_identity 与 material 重算不符："
            f"{provenance!r} != {expected_provenance!r}",
            reason="material_identity_mismatch", member_ref=member_ref)

    try:
        resolved = TS.verify_material_payload_ref(payload_ref, resolver)
    except TS.SchemaValidationError as exc:
        raise MaterialContextError(
            f"材料 {member_ref} 的 payload_ref 无法校验（dangling / 哈希 / locator / 版本不符）："
            f"{exc}", reason="payload_ref_unverifiable", member_ref=member_ref) from exc
    payload_hash = str(payload_ref.content_hash)
    if resolved.payload_bytes is None:
        raise MaterialContextError(
            f"材料 {member_ref} 的 resolver 没有返回 payload 字节（正文不可得）",
            reason="payload_ref_unverifiable", member_ref=member_ref)
    if hashlib.sha256(bytes(resolved.payload_bytes)).hexdigest() != payload_hash:
        raise MaterialContextError(
            f"材料 {member_ref} 的 payload 字节重算哈希 ≠ payload_ref.content_hash",
            reason="payload_ref_unverifiable", member_ref=member_ref)

    content, qualification, split = _decode_envelope(resolved=resolved, material=material,
                                                     member_ref=member_ref)
    reading_view = normalize_reading_view(content.get("text"))
    if not reading_view:
        raise MaterialContextError(
            f"材料 {member_ref} 的正文为空：空正文不得成为 Writer 材料（不是「无材料」）",
            reason="payload_text_empty", member_ref=member_ref)
    structured = content.get("structured_payload")
    structured_view = dict(structured) if isinstance(structured, Mapping) and structured else None
    locator = getattr(material, "locator", None)
    return ResolvedWriterMaterial.create(
        member_ref=member_ref, topic_id=topic_id, pack_id=pack_id, material_id=material_id,
        material_type=str(material.material_type),
        research_material_disposition_id=str(getattr(rmd, "disposition_id", "") or ""),
        source_identity=str(material.source_identity), provenance_identity=provenance,
        locator_ref=material_locator_ref(material=material),
        payload_ref=payload_ref.to_dict(), payload_hash=payload_hash,
        content_hash=str(getattr(material, "content_hash", "") or ""),
        material_content_fingerprint=TS.material_content_fingerprint(material),
        reading_view=reading_view, structured_view=structured_view,
        content_qualification=_qualification_view(
            qualification, locator=locator,
            source_identity=str(material.source_identity),
            member_ref=member_ref),
        span_continuity=_continuity_from_split(split, member_ref=member_ref))


def _sequence_of_readings(context: WriterMaterialContext) -> Sequence[str]:
    """上下文里的正文序列（审计/测试用：确认「Writer 看过的」与「Evaluator 复核的」同一串）。"""
    return tuple(m.reading_view for m in context.materials)


def reading_for_manifest_member(*, material_context: Any, member: Any) -> ResolvedWriterMaterial:
    """manifest 成员 ↔ 已解析材料正文的**交叉复检**（唯一实现，两个门共用）。

    这是 §三 A 的收口点：`WriterMaterialManifestEntry`（wmm-2）携带 payload 身份、精确定位、
    payload 哈希与读视图指纹，但**不携带正文**；正文只在 `WriterMaterialContext` 里。任何要
    「按正文判断」的下游（机械门 / 语义门 / 渲染）都必须经过本函数拿正文，而不是各自
    信任一份自报字段。

    逐项**逐字**比对，任一不一致即 typed fail-closed（`MaterialContextError`）：

    * 成员必须在该 context 里存在（`member_not_in_context`）——「manifest 里有 ID」不构成
      「正文被解析过」；
    * `payload_ref` / `locator_ref` / `payload_hash` / `content_fingerprint` /
      `reading_view_fingerprint` 必须全部一致（`member_payload_mismatch`）——被打过补丁的
      成员不能借另一份材料的正文通过；
    * 正文必须非空（`payload_text_empty`）——空正文不能支撑任何断言。
    """
    if not isinstance(material_context, WriterMaterialContext):
        raise MaterialContextError(
            "缺少 wmctx-1 材料正文上下文：只有 material ID 的清单不得用于复核正文",
            reason="member_not_in_context", member_ref=str(getattr(member, "member_ref", "") or ""))
    member_ref = str(getattr(member, "member_ref", "") or "")
    resolved = material_context.material_for(member_ref)
    if resolved is None:
        raise MaterialContextError(
            f"manifest 成员 {member_ref!r} 不在本次材料正文上下文里："
            "「清单里有这个成员」不等于「它的正文被解析过」",
            reason="member_not_in_context", member_ref=member_ref)
    pairs = (
        ("source_identity", str(getattr(member, "source_identity", "") or "")),
        ("provenance_identity", str(getattr(member, "provenance_identity", "") or "")),
        ("material_content_fingerprint", str(
            getattr(member, "material_content_fingerprint", "") or "")),
        ("payload_hash", str(getattr(member, "payload_hash", "") or "")),
        ("reading_view_fingerprint", str(getattr(member, "reading_view_fingerprint", "") or "")),
    )
    for name, declared in pairs:
        if declared != str(getattr(resolved, name, "") or ""):
            raise MaterialContextError(
                f"manifest 成员 {member_ref!r} 的 {name} 与已解析正文不一致：声明 {declared!r}，"
                f"实际 {str(getattr(resolved, name, '') or '')!r}",
                reason="member_payload_mismatch", member_ref=member_ref)
    if dict(getattr(member, "payload_ref", {}) or {}) != dict(resolved.payload_ref):
        raise MaterialContextError(
            f"manifest 成员 {member_ref!r} 的 payload_ref 与已解析正文不一致",
            reason="member_payload_mismatch", member_ref=member_ref)
    if NS.locator_sort_key(getattr(member, "locator_ref", None)) \
            != NS.locator_sort_key(resolved.locator_ref):
        raise MaterialContextError(
            f"manifest 成员 {member_ref!r} 的 locator_ref 与已解析正文不一致",
            reason="member_payload_mismatch", member_ref=member_ref)
    if not resolved.reading_view.strip():
        raise MaterialContextError(
            f"manifest 成员 {member_ref!r} 的正文为空：空正文不得支撑任何断言",
            reason="payload_text_empty", member_ref=member_ref)
    return resolved


def readings_for_manifest_members(*, material_context: Any, members: Sequence[Any],
                                  ) -> tuple[ResolvedWriterMaterial, ...]:
    """一批成员的正文复检（顺序与 `members` 一致；任一失败即整批拒绝）。"""
    return tuple(reading_for_manifest_member(material_context=material_context, member=m)
                 for m in members)

# -*- coding: utf-8 -*-
"""§0.21（一）：**原 PDF 表区的只读展示**（`std-1` / `stdp-1`）。

`DESIGN_V2.md` §0.21 把演示竖链拆成两条**身份不可互换**的可读路径：

* 路径 (a) 主营业务正文：`Contract → Harness/树 → Evidence/Pack + 合格事实 → 精确 Writer
  manifest → 逐句引用正文 → 确定性硬核对 → 独立只读审阅 → 不可发布预览`；
* 路径 (b) 业务原表：`已登记的原始电子 PDF → 精确页/区域定位 → 人工确认 →
  来源原件区域只读渲染 → 预览`。

**本模块只做路径 (b)，且只做它的「取得原件像素与可回查坐标」这一段。** 它刻意**不** import
写作链的任何模块（`cited_writer` / `pack_writer` / `sentence_check` …）：两条路径的身份既然
不可互换，代码层面也不该有一条顺手把表区喂进 Pack 的近路。

## 这条路径**不**产生什么（写下来是为了让它不可被顺手升格）

* 它**不是** `TopicResearchPack` 的第二条投递通道：产出里没有 material id、没有 topic id、
  没有 `WriterMaterialManifestEntry`，也没有任何字段可以被 `material_context` 读进去。
* 它**不是** `TableObject`、不是结构化重建、**不**产生任何格级数字权威。区域里当然能看见
  数字，但「看得见」与「可以写进正文」是 §0.21 第三步要求分开的两件事。本模块里没有
  `cell` / `fact` / `claim` 这类字眼，也不做 OCR、截图识别或对平铺文本的二次抽取。
* 它**不**证明 Contract `set_complete`，**不**构成 TS5 通过，**不**是系统放行或发布资格。

## 人工确认只有人能签

`confirmation_state` 的初值恒为 `pending_human_confirmation`。本模块**没有**任何一条代码
路径会把它写成 `human_confirmed`——只有 :func:`apply_human_confirmation`，它要一个非空的
确认人、确认时间与结论，且结论只覆盖**区域是否完整、是否清晰、是否忠实于原件**这三件事
（见 :data:`HUMAN_CONFIRMATION_CONCLUSIONS`）。它**永不**覆盖「这些格里的数字已被授权」。

## fail-closed

登记来源取不到、实际文件哈希与登记不符、登记来源不是 PDF、区域越出该页媒体框、区域退化、
表题锚点不在页文字层、关键行文字不在区域文字层、续表链顺序断裂——一律记成
:data:`REGION_DISPLAY_DEFECT_KINDS` 里的一条**类型化缺陷**，该区域记 `display_state="defect"`，
**绝不**静默降级成「材料里没有这张表」：§0.21 要求「确有表格却未呈现」记**系统展示能力缺陷**，
而不是来源缺口。缺陷区域照样出现在产出里，读者要看到的是「这里有一个我们没能展示的表区」。

## 生产规则里没有公司名、证券代码与固定页码

本次选哪些页、哪些区域，全部来自一份**运行级来源展示记录**（数据文件，见
`templates/demo_scopes/m930_3_source_table_display_v1.yaml`）。本模块只认「登记来源 + 页号 +
区域 + 锚点」这套中立坐标，对任何公司、任何年份一视同仁。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "SOURCE_TABLE_DISPLAY_SCHEMA_VERSION",
    "SOURCE_TABLE_DISPLAY_POLICY_VERSION",
    "REGION_CONFIRMATION_STATES",
    "HUMAN_CONFIRMATION_CONCLUSIONS",
    "CONFIRMATION_QUESTIONS",
    "REGION_DISPLAY_DEFECT_KINDS",
    "REGION_DISPLAY_STATES",
    "SOURCE_DISPLAY_REGISTRATION_DECISIONS",
    "DEFAULT_RENDER_SCALE",
    "SourceTableDisplayError",
    "SourceDisplayRegion",
    "SourceDisplayDefect",
    "SourceDisplayRegionRecord",
    "SourceDisplayRegistration",
    "HumanConfirmation",
    "SourceTableDisplaySet",
    "load_source_display_specs",
    "load_source_display_registrations",
    "build_source_table_display_set",
    "apply_human_confirmation",
    "render_source_table_display_markdown",
]

#: 展示记录的 wire 版本。字段增删即升版。
SOURCE_TABLE_DISPLAY_SCHEMA_VERSION = "std-1"
#: 展示政策版本（缺陷分类、锚点判据、确认态与结论词表、渲染口径，任一变化即升版）。
SOURCE_TABLE_DISPLAY_POLICY_VERSION = "stdp-1"

#: 区域的人工确认态。**初值恒为** `pending_human_confirmation`，本模块只读不签署。
REGION_CONFIRMATION_STATES = (
    "pending_human_confirmation",
    "human_confirmed",
    "human_rejected",
)

#: 人工确认**只能**说的两句话。它们覆盖的是「区域完整性 / 清晰度 / 与原件的忠实性」，
#: 与「区域里的数字是否取得格级或事实级资格」**无关**——后者不在这条路径上，也不由人一句
#: 确认产生。
HUMAN_CONFIRMATION_CONCLUSIONS = (
    "complete_clear_faithful",
    "incomplete_or_unclear_or_unfaithful",
)

#: 读者面上「请人核对」的三个问题。逐条对应 :data:`HUMAN_CONFIRMATION_CONCLUSIONS` 里那句话
#: 覆盖的范围，**只是展示文案**：把它摆到人眼前不等于人已经看过，更不等于有人签过。
CONFIRMATION_QUESTIONS = (
    "区域**完整**：表题、表头、全部数据行、续表都在框内，没有被裁掉或漏掉",
    "区域**清晰**：原件像素可读，没有模糊、倾斜、遮挡到读不出字",
    "区域**忠实**：就是登记文档该页原件上的那一块，没有被替换、拼接或改写",
)

#: 展示缺陷的封闭集合。任一条在场 ⇒ 该区域 `display_state="defect"`。
REGION_DISPLAY_DEFECT_KINDS = (
    #: 登记路径上取不到这个文件（来源清单过期或被移动）。
    "source_file_missing",
    #: 实际文件哈希与登记哈希不符 ⇒ fail-closed，不展示（否则展示的是另一个版本的原件）。
    "source_file_hash_mismatch",
    #: 登记来源不是 PDF：没有可渲染的原件区域。
    "source_not_pdf",
    #: 页号越出该文档页数。
    "page_out_of_range",
    #: 区域越出该页媒体框（坐标写错了，或页尺寸变了）。
    "region_out_of_page",
    #: 区域退化（宽或高非正）。
    "region_degenerate",
    #: 表题锚点不在该页文字层里 ⇒ 页号很可能指向了别的内容。
    "title_anchor_not_found",
    #: 关键行文字不在该区域的文字层里 ⇒ 区域可能切到了别的表，或者只切了半张。
    "row_anchor_not_found",
    #: 续表链断裂：前驱不在本清单里、不早于本区域，或前驱本身已是缺陷区域。
    "continuation_out_of_order",
    #: 渲染后端不可用（缺依赖）。
    "render_backend_unavailable",
    #: 渲染本身抛错。
    "render_failed",
)

#: 区域的展示态。**没有**第三档：这条路径要么能给出原件区域，要么带类型化缺陷。
REGION_DISPLAY_STATES = ("displayable", "defect")

#: 候选表区的登记决定。每个**被考察过**的表区都要有一句采用／不采用的理由——这条记录存在的
#: 意义正是「不显示也要说明为什么不显示」，避免「没展示」被读成「材料里没有」。
SOURCE_DISPLAY_REGISTRATION_DECISIONS = ("adopted", "not_adopted")

#: 渲染缩放（PDF 点 → 像素）。2.0 约合 144 DPI，足够人眼核对表格字样。
DEFAULT_RENDER_SCALE = 2.0


class SourceTableDisplayError(Exception):
    """展示记录的输入不一致（fail-closed）：记录缺字段、续表前驱指向自己、确认签名不全…"""


# ---------------------------------------------------------------------------
# 运行级来源展示记录（数据）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceDisplayRegion:
    """一个**待展示的原件区域**：登记来源 + 页号 + 区域 + 锚点 + 可读标注。

    区域坐标是 PDF 点、原点在**左上角**（`(x0, top, x1, bottom)`，与 pdfplumber 同口径）。
    锚点是这条记录**可复算**的部分：表题锚点在页文字层里、关键行文字在区域文字层里，
    两者都在才说明「这一版 PDF 的这一页这一块，确实是这张表」。
    """

    region_key: str
    rail: str
    """展示族（运行级记录给的组名，例如「收入构成」「营业成本」「毛利率」）。"""
    document_id: str
    document_version: str
    page_number: int
    region_points: tuple[float, float, float, float]
    title: str
    period_label: str
    unit_label: str
    title_anchor: str
    row_anchors: tuple[str, ...] = ()
    continuation_of: str = ""
    note: str = ""
    source_name: str = ""
    """登记来源的文件名。缺省留空——**登记清单**在运行时会用真实文件名覆盖它，
    避免同一件事（这份来源叫什么）在记录里再抄一份。"""
    file_sha256: str = ""
    """登记哈希。缺省留空，理由同上：以本次已登记的来源清单为准。"""

    def __post_init__(self) -> None:
        for name in ("region_key", "rail", "document_id", "document_version",
                     "title", "title_anchor"):
            if not str(getattr(self, name) or "").strip():
                raise SourceTableDisplayError(f"区域记录缺 {name}：{self.region_key!r}")
        if int(self.page_number) < 1:
            raise SourceTableDisplayError("页号必须从 1 起")
        if len(self.region_points) != 4:
            raise SourceTableDisplayError("区域必须是 (x0, top, x1, bottom) 四个数")
        if self.continuation_of and self.continuation_of == self.region_key:
            raise SourceTableDisplayError("续表不能把自己当成前驱")

    @property
    def region_px(self) -> tuple[float, float, float, float]:
        return tuple(float(v) for v in self.region_points)  # type: ignore[return-value]


@dataclass(frozen=True)
class SourceDisplayDefect:
    """一条类型化展示缺陷——**不是**来源缺口，是系统展示能力缺陷。"""

    kind: str
    detail: str

    def __post_init__(self) -> None:
        if self.kind not in REGION_DISPLAY_DEFECT_KINDS:
            raise SourceTableDisplayError(f"未登记的展示缺陷类型：{self.kind!r}")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "detail": self.detail}


@dataclass(frozen=True)
class HumanConfirmation:
    """一次**人**给出的确认。本模块不产生它，只接受它。"""

    confirmer: str
    confirmed_at: str
    conclusion: str
    note: str = ""

    def __post_init__(self) -> None:
        if not str(self.confirmer).strip():
            raise SourceTableDisplayError("人工确认缺确认人")
        if not str(self.confirmed_at).strip():
            raise SourceTableDisplayError("人工确认缺确认时间")
        if self.conclusion not in HUMAN_CONFIRMATION_CONCLUSIONS:
            raise SourceTableDisplayError(
                f"人工确认结论不在词表里：{self.conclusion!r}")


@dataclass(frozen=True)
class SourceDisplayRegistration:
    """一条**候选表区登记**：这份材料里被考察过的表区，以及采用／不采用的**理由**。

    「采用」的登记必须由至少一个区域实现（:attr:`region_keys` 非空），「不采用」的登记
    必须写明理由——这条约束让「没有展示」总是带着一句可回查的话，而不是一个空白。
    """

    document_id: str
    area_label: str
    decision: str
    reason: str
    region_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.decision not in SOURCE_DISPLAY_REGISTRATION_DECISIONS:
            raise SourceTableDisplayError(f"未登记的采用决定：{self.decision!r}")
        if not str(self.document_id).strip() or not str(self.area_label).strip():
            raise SourceTableDisplayError("候选表区登记缺 document_id 或 area_label")
        if not str(self.reason).strip():
            raise SourceTableDisplayError(
                f"{self.area_label}：登记必须写明采用／不采用理由")
        if self.decision == "adopted" and not self.region_keys:
            raise SourceTableDisplayError(
                f"{self.area_label}：采用却没有对应的展示区域")
        if self.decision == "not_adopted" and self.region_keys:
            raise SourceTableDisplayError(
                f"{self.area_label}：不采用却挂了展示区域")

    def to_dict(self) -> dict:
        return {"document_id": self.document_id, "area_label": self.area_label,
                "decision": self.decision, "reason": self.reason,
                "region_keys": list(self.region_keys)}


@dataclass(frozen=True)
class SourceDisplayRegionRecord:
    """一个区域的**展示读数**：原件身份、页/区域、渲染产物、锚点复算、人工确认态。"""

    region: SourceDisplayRegion
    display_state: str
    defects: tuple[SourceDisplayDefect, ...]
    file_sha256_actual: str = ""
    file_hash_verified: bool = False
    page_media_box: tuple[float, float, float, float] | None = None
    render_relpath: str = ""
    render_sha256: str = ""
    render_pixel_size: tuple[int, int] | None = None
    region_text_digest: str = ""
    region_char_count: int = 0
    confirmation_state: str = "pending_human_confirmation"
    confirmer: str = ""
    confirmed_at: str = ""
    confirmation_conclusion: str = ""
    confirmation_note: str = ""

    def __post_init__(self) -> None:
        if self.display_state not in REGION_DISPLAY_STATES:
            raise SourceTableDisplayError(f"未登记的展示态：{self.display_state!r}")
        if self.confirmation_state not in REGION_CONFIRMATION_STATES:
            raise SourceTableDisplayError(f"未登记的确认态：{self.confirmation_state!r}")
        if self.display_state == "displayable" and self.defects:
            raise SourceTableDisplayError(
                f"{self.region.region_key}：无缺陷区域不得带缺陷记录")
        if self.confirmation_state == "human_confirmed" and not (
                self.confirmer and self.confirmed_at and self.confirmation_conclusion):
            raise SourceTableDisplayError(
                f"{self.region.region_key}：已确认的区域必须有确认人、时间与结论")

    @property
    def displayable(self) -> bool:
        return self.display_state == "displayable"

    @property
    def human_confirmed(self) -> bool:
        return self.confirmation_state == "human_confirmed"

    def to_dict(self) -> dict:
        region = self.region
        return {
            "region": {
                "region_key": region.region_key, "rail": region.rail,
                "document_id": region.document_id,
                "document_version": region.document_version,
                "source_name": region.source_name,
                "file_sha256": region.file_sha256,
                "page_number": region.page_number,
                "region_points": [float(v) for v in region.region_points],
                "title": region.title, "period_label": region.period_label,
                "unit_label": region.unit_label, "title_anchor": region.title_anchor,
                "row_anchors": list(region.row_anchors),
                "continuation_of": region.continuation_of, "note": region.note,
            },
            "display_state": self.display_state,
            "defects": [d.to_dict() for d in self.defects],
            "file_sha256_actual": self.file_sha256_actual,
            "file_hash_verified": self.file_hash_verified,
            "page_media_box": (None if self.page_media_box is None
                               else [float(v) for v in self.page_media_box]),
            "render_relpath": self.render_relpath,
            "render_sha256": self.render_sha256,
            "render_pixel_size": (None if self.render_pixel_size is None
                                  else [int(self.render_pixel_size[0]),
                                        int(self.render_pixel_size[1])]),
            "region_text_digest": self.region_text_digest,
            "region_char_count": self.region_char_count,
            "confirmation_state": self.confirmation_state,
            "confirmer": self.confirmer, "confirmed_at": self.confirmed_at,
            "confirmation_conclusion": self.confirmation_conclusion,
            "confirmation_note": self.confirmation_note,
        }


@dataclass(frozen=True)
class SourceTableDisplaySet:
    """一次运行的**全部**原表区展示记录。

    :attr:`paired_report_version` 只作展示并列（这一页是和哪一版正文一起看的），
    **不**进本集合的身份体：这条路径与写作链没有身份耦合，否则「表区一变正文版本就变」，
    两条不可互换的身份就又被缝回去了。
    """

    task_id: str
    section_id: str
    source_record_id: str
    registered_source_id: str
    regions: tuple[SourceDisplayRegionRecord, ...]
    registrations: tuple[SourceDisplayRegistration, ...] = ()
    paired_report_version: str = ""

    def __post_init__(self) -> None:
        adopted = {k for reg in self.registrations if reg.decision == "adopted"
                   for k in reg.region_keys}
        keys = [r.region.region_key for r in self.regions]
        if self.registrations:
            stray = [k for k in keys if k not in adopted]
            if stray:
                raise SourceTableDisplayError(
                    f"这些展示区域没有任何一条「采用」登记认领：{stray}")
            missing = [k for k in adopted if k not in set(keys)]
            if missing:
                raise SourceTableDisplayError(
                    f"「采用」登记指向了不存在的展示区域：{missing}")

    @property
    def defects(self) -> tuple[SourceDisplayDefect, ...]:
        return tuple(d for r in self.regions for d in r.defects)

    @property
    def displayable_regions(self) -> tuple[SourceDisplayRegionRecord, ...]:
        return tuple(r for r in self.regions if r.displayable)

    @property
    def confirmed_regions(self) -> tuple[SourceDisplayRegionRecord, ...]:
        return tuple(r for r in self.regions if r.human_confirmed)

    def identity_body(self) -> dict:
        return {
            "schema_version": SOURCE_TABLE_DISPLAY_SCHEMA_VERSION,
            "policy_version": SOURCE_TABLE_DISPLAY_POLICY_VERSION,
            "task_id": self.task_id, "section_id": self.section_id,
            "source_record_id": self.source_record_id,
            "registered_source_id": self.registered_source_id,
            "registrations": [g.to_dict() for g in self.registrations],
            "regions": [r.to_dict() for r in self.regions],
        }

    def fingerprint(self) -> str:
        payload = json.dumps(self.identity_body(), ensure_ascii=False,
                             sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def display_set_id(self) -> str:
        return "std_" + self.fingerprint()[:24]

    def to_dict(self) -> dict:
        return {"schema_version": SOURCE_TABLE_DISPLAY_SCHEMA_VERSION,
                "policy_version": SOURCE_TABLE_DISPLAY_POLICY_VERSION,
                "display_set_id": self.display_set_id(),
                "fingerprint": self.fingerprint(),
                "task_id": self.task_id, "section_id": self.section_id,
                "source_record_id": self.source_record_id,
                "registered_source_id": self.registered_source_id,
                "paired_report_version": self.paired_report_version,
                "registrations": [g.to_dict() for g in self.registrations],
                "regions": [r.to_dict() for r in self.regions],
                "defect_count": len(self.defects),
                "displayable_count": len(self.displayable_regions),
                "human_confirmed_count": len(self.confirmed_regions)}


# ---------------------------------------------------------------------------
# 装载运行级记录
# ---------------------------------------------------------------------------

_REGION_REQUIRED = ("region_key", "rail", "document_id", "document_version",
                    "page_number", "region_points", "title")


def load_source_display_specs(path: str | Path) -> tuple[SourceDisplayRegion, ...]:
    """读运行级来源展示记录（YAML/JSON）→ 有序的区域记录。

    顺序即展示顺序，也是续表链的判定顺序（`continuation_of` 必须指向**更早**的一项）。
    记录里出现的公司名/年份只是**这批数据**的内容，本模块不据此分支。
    """
    import yaml  # 仓内既有依赖

    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, Mapping):
        raise SourceTableDisplayError("来源展示记录必须是对象")
    raw = doc.get("regions")
    if not isinstance(raw, (list, tuple)) or not raw:
        raise SourceTableDisplayError("来源展示记录至少要有一个区域")
    out: list[SourceDisplayRegion] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            raise SourceTableDisplayError(f"区域记录必须是对象：{item!r}")
        missing = [k for k in _REGION_REQUIRED if item.get(k) in (None, "")]
        if missing:
            raise SourceTableDisplayError(f"区域记录缺字段 {missing}：{item!r}")
        points = tuple(float(v) for v in item["region_points"])
        region = SourceDisplayRegion(
            region_key=str(item["region_key"]), rail=str(item["rail"]),
            document_id=str(item["document_id"]),
            document_version=str(item["document_version"]),
            source_name=str(item.get("source_name") or ""),
            file_sha256=str(item.get("file_sha256") or ""),
            page_number=int(item["page_number"]), region_points=points,
            title=str(item["title"]),
            period_label=str(item.get("period_label") or ""),
            unit_label=str(item.get("unit_label") or ""),
            title_anchor=str(item.get("title_anchor") or item["title"]),
            row_anchors=tuple(str(a) for a in (item.get("row_anchors") or ())),
            continuation_of=str(item.get("continuation_of") or ""),
            note=str(item.get("note") or ""))
        if region.region_key in seen:
            raise SourceTableDisplayError(f"区域键重复：{region.region_key!r}")
        seen.add(region.region_key)
        out.append(region)

    prior: dict[str, SourceDisplayRegion] = {}
    for region in out:
        if region.continuation_of and region.continuation_of not in prior:
            raise SourceTableDisplayError(
                f"{region.region_key}：续表前驱 {region.continuation_of!r} "
                "不在它之前的记录里（续表链必须按展示顺序给出）")
        prior[region.region_key] = region
    return tuple(out)


def load_source_display_registrations(path: str | Path
                                      ) -> tuple[SourceDisplayRegistration, ...]:
    """读同一份记录里的 `registrations` 段：候选表区 + 采用／不采用理由。

    这一段可以缺省（老记录没有它），但一旦给出，它就是**这份材料被怎么看过**的台账。
    """
    import yaml

    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, Mapping):
        raise SourceTableDisplayError("来源展示记录必须是对象")
    raw = doc.get("registrations")
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise SourceTableDisplayError("registrations 必须是列表")
    out: list[SourceDisplayRegistration] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise SourceTableDisplayError(f"候选表区登记必须是对象：{item!r}")
        out.append(SourceDisplayRegistration(
            document_id=str(item.get("document_id") or ""),
            area_label=str(item.get("area_label") or ""),
            decision=str(item.get("decision") or ""),
            reason=str(item.get("reason") or ""),
            region_keys=tuple(str(k) for k in (item.get("region_keys") or ()))))
    return tuple(out)


# ---------------------------------------------------------------------------
# 复算：哈希、区域、锚点
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tight(text: str) -> str:
    """空白归一——锚点与文字层摘要**只**按这个口径比，避免排版换行造成假阴性。"""
    return "".join(str(text).split())


def _region_text(page: Any, points: Sequence[float]) -> str:
    """剪出区域内的文字层（pdfplumber 口径），不做 OCR、不做截图识别。"""
    x0, top, x1, bottom = (float(v) for v in points)
    crop = page.crop((x0, top, x1, bottom))
    return crop.extract_text() or ""


def _page_media_box(page: Any) -> tuple[float, float, float, float]:
    return (0.0, 0.0, float(page.width), float(page.height))


def _render_region_to_png(pdf_path: Path, page_index: int,
                          points: Sequence[float], scale: float) -> bytes:
    """把该页的该区域渲染成 PNG 字节（原件像素，不经任何文本抽取）。"""
    import io

    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        page = doc[page_index]
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil().convert("RGB")
    finally:
        try:
            doc.close()
        except Exception:  # pragma: no cover - 关闭失败不影响读数
            pass
    x0, top, x1, bottom = (float(v) for v in points)
    box = (max(0, int(round(x0 * scale))), max(0, int(round(top * scale))),
           max(0, int(round(x1 * scale))), max(0, int(round(bottom * scale))))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise SourceTableDisplayError("区域换算到像素后退化了")
    if box[2] > image.width or box[3] > image.height:
        raise SourceTableDisplayError(
            f"区域换算到像素后越出渲染图 {image.size}：{box}")
    buffer = io.BytesIO()
    image.crop(box).save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 装配
# ---------------------------------------------------------------------------

def build_source_table_display_set(
    *,
    regions: Sequence[SourceDisplayRegion],
    task_id: str,
    section_id: str,
    source_record_id: str,
    registered_source_id: str,
    output_dir: str | Path,
    resolve_source_path: Mapping[str, str] | Any,
    registered_sha256: Mapping[str, str] | None = None,
    registered_source_name: Mapping[str, str] | None = None,
    render_scale: float = DEFAULT_RENDER_SCALE,
    registrations: Sequence[SourceDisplayRegistration] = (),
    paired_report_version: str = "",
) -> SourceTableDisplaySet:
    """把运行级区域记录变成**可人查**的展示读数（每个区域渲染一张 PNG + 类型化缺陷）。

    `resolve_source_path` 给出 `{document_id: 实际路径}`（由调用方从**已登记的来源清单**取，
    因此「登记了哪一份」与「渲染了哪一份」是同一件事）；`registered_sha256` 给出
    `{document_id: 登记哈希}`，缺省时回落到区域记录自己的 `file_sha256`。

    任何一步失败都**不**抛：区域记 `display_state="defect"` 并带上类型化缺陷，整批照常产出。
    这样做是因为 §0.21 要求「确有表格而未呈现」必须留下**系统展示能力缺陷**的记录，而不是
    让整条链在这里崩掉、或者被读成「材料里没有这张表」。
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved: dict[str, str] = dict(resolve_source_path or {})
    declared: dict[str, str] = dict(registered_sha256 or {})
    names: dict[str, str] = dict(registered_source_name or {})

    #: 登记来源的名字以**登记清单**为准：记录里不再抄一份可能过期的文件名。
    if names:
        regions = tuple(
            (replace(region, source_name=names[region.document_id])
             if names.get(region.document_id) and
             names[region.document_id] != region.source_name else region)
            for region in regions)

    #: 每个 document_id 只做一次「取文件 + 核哈希 + 开页」的复算，区域之间共享。
    file_cache: dict[str, tuple[Path | None, str, list[SourceDisplayDefect]]] = {}
    page_cache: dict[tuple[str, int], Any] = {}
    readers: list[Any] = []

    try:
        records: list[SourceDisplayRegionRecord] = []
        by_key: dict[str, SourceDisplayRegionRecord] = {}
        for region in regions:
            defects: list[SourceDisplayDefect] = []
            actual_sha = ""
            path: Path | None = None
            cached = file_cache.get(region.document_id)
            if cached is None:
                raw_path = str(resolved.get(region.document_id) or "")
                candidate = Path(raw_path) if raw_path else None
                if candidate is None or not candidate.is_file():
                    defects.append(SourceDisplayDefect(
                        "source_file_missing",
                        f"登记的来源 {region.document_id!r} 在路径上取不到文件：{raw_path!r}"))
                elif candidate.suffix.lower() != ".pdf":
                    defects.append(SourceDisplayDefect(
                        "source_not_pdf",
                        f"登记的来源 {region.document_id!r} 不是 PDF：{candidate.name!r}"))
                else:
                    actual_sha = _sha256_file(candidate)
                    expected = str(declared.get(region.document_id)
                                   or region.file_sha256 or "")
                    if expected and actual_sha != expected:
                        defects.append(SourceDisplayDefect(
                            "source_file_hash_mismatch",
                            f"实际文件哈希 {actual_sha} 与登记 {expected} 不符"
                            "（展示的会是另一个版本的原件，fail-closed）"))
                    path = candidate
                file_cache[region.document_id] = (path, actual_sha, defects)
            else:
                path, actual_sha, base_defects = cached
                defects.extend(base_defects)

            record = _build_one_region(
                region=region, path=path, actual_sha=actual_sha, defects=defects,
                out_dir=out_dir, render_scale=render_scale, page_cache=page_cache,
                readers=readers, by_key=by_key)
            records.append(record)
            by_key[region.region_key] = record

        return SourceTableDisplaySet(
            task_id=task_id, section_id=section_id,
            source_record_id=source_record_id,
            registered_source_id=registered_source_id,
            regions=tuple(records), registrations=tuple(registrations),
            paired_report_version=paired_report_version)
    finally:
        for reader in readers:
            try:
                reader.close()
            except Exception:  # pragma: no cover
                pass


def _build_one_region(*, region: SourceDisplayRegion, path: Path | None,
                      actual_sha: str, defects: list[SourceDisplayDefect],
                      out_dir: Path, render_scale: float,
                      page_cache: dict, readers: list,
                      by_key: dict[str, SourceDisplayRegionRecord]
                      ) -> SourceDisplayRegionRecord:
    """一个区域一轮：续表明细 → 页 → 区域 → 锚点 → 渲染 → 记录。"""
    defects = list(defects)
    media_box: tuple[float, float, float, float] | None = None
    region_text = ""
    render_relpath = ""
    render_sha = ""
    pixel_size: tuple[int, int] | None = None
    page = None

    if region.continuation_of:
        prior = by_key.get(region.continuation_of)
        if prior is None:
            defects.append(SourceDisplayDefect(
                "continuation_out_of_order",
                f"续表前驱 {region.continuation_of!r} 不在本清单里"))
        elif not prior.displayable:
            defects.append(SourceDisplayDefect(
                "continuation_out_of_order",
                f"续表前驱 {region.continuation_of!r} 本身是缺陷区域，"
                "续表的完整展示无从成立"))
        elif prior.region.document_id != region.document_id:
            defects.append(SourceDisplayDefect(
                "continuation_out_of_order",
                f"续表前驱 {region.continuation_of!r} 属于另一份文档"))
        elif prior.region.page_number > region.page_number:
            defects.append(SourceDisplayDefect(
                "continuation_out_of_order",
                f"续表前驱在第 {prior.region.page_number} 页，落到了本区域第 "
                f"{region.page_number} 页之后"))

    if path is not None and not defects:
        import pdfplumber

        key = (region.document_id, region.page_number)
        page = page_cache.get(key)
        if page is None:
            reader = pdfplumber.open(str(path))
            readers.append(reader)
            if region.page_number > len(reader.pages):
                defects.append(SourceDisplayDefect(
                    "page_out_of_range",
                    f"第 {region.page_number} 页越出该文档 {len(reader.pages)} 页"))
            else:
                page = reader.pages[region.page_number - 1]
                page_cache[key] = page

        if page is not None:
            media_box = _page_media_box(page)
            x0, top, x1, bottom = region.region_px
            if x1 <= x0 or bottom <= top:
                defects.append(SourceDisplayDefect(
                    "region_degenerate",
                    f"区域退化：{(x0, top, x1, bottom)}"))
            elif (x0 < media_box[0] - 0.5 or top < media_box[1] - 0.5
                  or x1 > media_box[2] + 0.5 or bottom > media_box[3] + 0.5):
                defects.append(SourceDisplayDefect(
                    "region_out_of_page",
                    f"区域 {(x0, top, x1, bottom)} 越出页媒体框 {media_box}"))
            else:
                page_text = _tight(page.extract_text() or "")
                if _tight(region.title_anchor) not in page_text:
                    defects.append(SourceDisplayDefect(
                        "title_anchor_not_found",
                        f"表题锚点 {region.title_anchor!r} 不在第 "
                        f"{region.page_number} 页文字层里（页号可能指向别处）"))
                region_text = _region_text(page, region.region_px)
                tight_region = _tight(region_text)
                missing_rows = [a for a in region.row_anchors
                                if _tight(a) not in tight_region]
                if missing_rows:
                    defects.append(SourceDisplayDefect(
                        "row_anchor_not_found",
                        f"这些关键行文字不在本区域文字层里（可能切到了别的表或只切了半张）："
                        f"{missing_rows}"))

    if path is not None and not defects:
        try:
            payload = _render_region_to_png(path, region.page_number - 1,
                                            region.region_px, render_scale)
        except ImportError as exc:
            defects.append(SourceDisplayDefect(
                "render_backend_unavailable", f"渲染后端不可用：{exc}"))
        except Exception as exc:
            defects.append(SourceDisplayDefect("render_failed", f"渲染失败：{exc}"))
        else:
            render_sha = hashlib.sha256(payload).hexdigest()
            name = f"{region.document_id}__p{region.page_number:04d}__"\
                   f"{region.region_key}.png"
            target = out_dir / name
            target.write_bytes(payload)
            render_relpath = name
            try:
                from PIL import Image
                import io as _io
                with Image.open(_io.BytesIO(payload)) as image:
                    pixel_size = (int(image.width), int(image.height))
            except Exception:  # pragma: no cover - 像素尺寸只作展示
                pixel_size = None

    return SourceDisplayRegionRecord(
        region=region,
        display_state=("defect" if defects else "displayable"),
        defects=tuple(defects), file_sha256_actual=actual_sha,
        file_hash_verified=bool(actual_sha and not any(
            d.kind == "source_file_hash_mismatch" for d in defects)),
        page_media_box=media_box, render_relpath=render_relpath,
        render_sha256=render_sha, render_pixel_size=pixel_size,
        region_text_digest=hashlib.sha256(
            _tight(region_text).encode("utf-8")).hexdigest() if region_text else "",
        region_char_count=len(_tight(region_text)))


def apply_human_confirmation(record: SourceDisplayRegionRecord,
                             confirmation: HumanConfirmation
                             ) -> SourceDisplayRegionRecord:
    """把一次**人**的确认签名盖上。

    这是本模块里**唯一**能产生 `human_confirmed` / `human_rejected` 的入口，而且它要求一个
    完整的 :class:`HumanConfirmation`（确认人 + 时间 + 词表内结论）。缺陷区域**不接受**签署：
    一张没能渲染出来的表区，「区域完整清晰忠实」这句话无从成立。
    """
    if not record.displayable:
        raise SourceTableDisplayError(
            f"{record.region.region_key}：缺陷区域不能被确认为「完整清晰忠实」")
    if not isinstance(confirmation, HumanConfirmation):
        raise SourceTableDisplayError("人工确认必须是 HumanConfirmation")
    state = ("human_confirmed"
             if confirmation.conclusion == "complete_clear_faithful"
             else "human_rejected")
    return replace(record, confirmation_state=state,
                   confirmer=confirmation.confirmer,
                   confirmed_at=confirmation.confirmed_at,
                   confirmation_conclusion=confirmation.conclusion,
                   confirmation_note=confirmation.note)


# ---------------------------------------------------------------------------
# 读者面
# ---------------------------------------------------------------------------

_DISPLAY_FACE_BANNER = (
    "> **原表区只读展示（来源原件视图）。** 这一块是**已登记的原始电子 PDF** 上的精确区域，"
    "供人核对。\n"
    "> 它**不**进入 Pack/Writer 材料身份，**不**是结构化重建，**不**给区域里的数字任何\n"
    "> 格级或事实级资格，也**不**证明 Contract `set_complete` 或系统放行。\n"
    "> 「看得到原表」与「可以把数字写进正文」是两件事（`DESIGN_V2.md` §0.21）。"
)


def _confirmation_face_lines(record: SourceDisplayRegionRecord) -> list[str]:
    """读者面上「这一块还差人点头」的那几行。**只读展示**，不产生任何签署。

    缺陷区域没有这段：一张没能渲染出来的表区，「完整清晰忠实」这句话无从成立
    （:func:`apply_human_confirmation` 同样拒收），所以不摆问题、只摆缺陷。
    """
    if not record.displayable:
        return []
    if record.confirmation_state == "pending_human_confirmation":
        out = ["  - **待人工确认**——请对照上面的原表图，逐条看这三件事："]
        out += [f"    {i}. {q}" for i, q in enumerate(CONFIRMATION_QUESTIONS, start=1)]
        out.append(
            "    - 结论只能是这两种：`complete_clear_faithful`（三条都成立）或 "
            "`incomplete_or_unclear_or_unfaithful`（任一条不成立）。")
        out.append(
            "    - **由人判断、由人签署**（`apply_human_confirmation`，要求确认人 + 时间 + "
            "词表内结论）：本页只把原表图和登记身份摆到人眼前，**程序不会代签**，也不会因为"
            "没人看过就写成「已确认」。")
        return out
    if record.confirmation_state == "human_confirmed":
        return ["  - **已由人确认**：区域完整、清晰、忠实于原件。"
                "**这一句只覆盖区域本身**，不给区域里的任何数字格级或事实级资格。"]
    return ["  - **人已判为不合格**（不完整 / 不清晰 / 不忠实）：这一块原表图**未**取得人工确认，"
            "不得据此认为该区域已被核过。"]


def render_source_table_display_markdown(
        display: SourceTableDisplaySet,
        *, registered_sha256: Mapping[str, str] | None = None,
        image_base: str | None = None) -> str:
    """展示集合 → Markdown：**每个区域**给出身份、页/区域、渲染哈希与确认态。

    `registered_sha256`（可选）= `{document_id: 登记哈希}`，即**登记清单里实际拿去比对**的
    那一个哈希。缺省时回落到区域记录自己的 `file_sha256`。

    为什么要显式传它：区域记录自己的 `file_sha256` 是**建区时抄的**字段，登记清单在世时它
    往往是空的；只印它会让读者看到「原件哈希：（空）……一致」——仿佛比对没有发生过。这里
    印的是**真正参与比对**的那个值，并且在两侧都没有可比哈希时如实写「**未比对**」：
    `file_hash_verified` 只说明「没有发现不符」，它**单独不足以**证明比对发生过。

    `image_base`（可选）= 渲染 PNG 相对**本 Markdown 文件所在目录**的前缀。给了它，每个
    可展示区域就地嵌入原件像素（`![…](…)`）并另给一条可直接打开的链接；缺省时保持旧行为
    （只印文件名），以免改了路径却没给基准时链接指向空气。**嵌入图片不改变任何身份**：
    区域仍是只读展示，不进 Pack/Writer，也不给区域里的数字任何资格——这一步只解决
    「读者点得开、看得见」，不解决「数字能不能用」。
    """
    lines: list[str] = ["## 业务原表区（只读展示，未进入 Pack/Writer）", "",
                        _DISPLAY_FACE_BANNER, ""]
    lines.append(f"- 展示集：`{display.display_set_id()}`／政策 `{SOURCE_TABLE_DISPLAY_POLICY_VERSION}`"
                 f"／登记来源清单 `{display.registered_source_id}`")
    lines.append(f"- 区域 {len(display.regions)} 个：可展示 "
                 f"{len(display.displayable_regions)}，带缺陷 {len(display.regions) - len(display.displayable_regions)}，"
                 f"已人工确认 {len(display.confirmed_regions)}")
    if display.paired_report_version:
        lines.append(f"- 与本页正文版本并列展示：`{display.paired_report_version}`"
                     "（**只作并列**，不进本展示集身份）")
    lines.append("")

    for record in display.regions:
        region = record.region
        state = "可展示" if record.displayable else "**展示缺陷**"
        lines.append(f"### {region.rail}／{region.title}")
        lines.append("")
        lines.append(f"- 文档：`{region.document_id}`（版本 `{region.document_version}`，"
                     f"`{region.source_name}`）")
        lines.append(f"- 页／区域：第 **{region.page_number}** 页，"
                     f"PDF 点 `({region.region_points[0]:.1f}, {region.region_points[1]:.1f}, "
                     f"{region.region_points[2]:.1f}, {region.region_points[3]:.1f})`"
                     + (f"（续表，前驱 `{region.continuation_of}`）"
                        if region.continuation_of else "（本表首块）"))
        lines.append(f"- 表题：{region.title}　可确认期间：{region.period_label or '（未标注）'}"
                     f"　单位：{region.unit_label or '（未标注）'}")
        expected = str((registered_sha256 or {}).get(region.document_id)
                       or region.file_sha256 or "")
        if not expected:
            hash_verdict = "**未比对**（登记清单与区域记录都没有可比的登记哈希）"
        elif record.file_hash_verified:
            hash_verdict = "一致"
        else:
            hash_verdict = "**不一致**"
        lines.append(f"- 原件哈希：登记 `{expected or '（登记未带哈希）'}`"
                     f"　实测 `{record.file_sha256_actual or '（未取到）'}`"
                     f"　{hash_verdict}")
        lines.append(f"- 渲染文件：`{record.render_relpath or '（未生成）'}`"
                     f"　渲染哈希：`{record.render_sha256 or '（未生成）'}`"
                     + (f"　像素：{record.render_pixel_size[0]}×{record.render_pixel_size[1]}"
                        if record.render_pixel_size else ""))
        if record.render_relpath:
            if image_base:
                target = f"{image_base.rstrip('/')}/{record.render_relpath}"
                #: alt 文本只用**结构事实**（编号／页／区域），不拼标题原文字——
                #: 标题里的 `[`/`]`/`|` 会把 Markdown 结构撑坏。
                lines.append(
                    f"- 原表图（原件像素，直接可见）："
                    f"![{region.rail} 第{region.page_number}页 区域]"
                    f"({target})")
                lines.append(f"  - 打不开内嵌图时直接点："
                             f"[{record.render_relpath}]({target})")
            else:
                lines.append("- 原表图：**未嵌入**（渲染器没给图片基准目录；"
                             f"文件在 `{record.render_relpath}`）")
        lines.append(f"- 区域文字层摘要：`{record.region_text_digest or '（空）'}`"
                     f"（{record.region_char_count} 字，仅作可复算凭证，不作数字权威）")
        lines.append(f"- 展示态：{state}"
                     + (f"　人工确认：`{record.confirmation_state}`"
                        if record.displayable else ""))
        lines.extend(_confirmation_face_lines(record))
        for defect in record.defects:
            lines.append(f"  - 系统展示能力缺陷 `{defect.kind}`：{defect.detail}")
        if region.note:
            lines.append(f"  - 备注：{region.note}")
        lines.append("")

    if display.registrations:
        lines.append("### 候选表区登记（采用／不采用，逐条给理由）")
        lines.append("")
        lines.append("| 文档 | 表区 | 决定 | 理由 | 展示区域 |")
        lines.append("|---|---|---|---|---|")
        for reg in display.registrations:
            decision = "采用" if reg.decision == "adopted" else "不采用"
            keys = "、".join(f"`{k}`" for k in reg.region_keys) or "—"
            lines.append(f"| `{reg.document_id}` | {reg.area_label} | {decision} | "
                         f"{reg.reason} | {keys} |")
        lines.append("")

    if display.confirmed_regions:
        lines.append("**已人工确认的区域**（确认范围：区域完整、清晰、忠实于原件；"
                     "**不**含任何数字资格）")
        lines.append("")
        for record in display.confirmed_regions:
            lines.append(f"- `{record.region.region_key}`：{record.confirmer} 于 "
                         f"{record.confirmed_at} 记为 `{record.confirmation_conclusion}`"
                         + (f"——{record.confirmation_note}" if record.confirmation_note else ""))
        lines.append("")
    else:
        lines.append("**没有任何区域取得人工确认**——上面每一块都还是「待人工确认」。"
                     "本模块不能代替人签署。")
        lines.append("")
        lines.append("要请人核对的，是每一块区域**是否完整、是否清晰、是否忠实于原件**这三件事"
                     "（逐块的三个问题就印在那一块的原表图下面）。人看完只能给两种结论："
                     "`complete_clear_faithful`（三条都成立）或 "
                     "`incomplete_or_unclear_or_unfaithful`（任一条不成立）。")
        lines.append("")
        lines.append("这份展示**只解决「读者能不能看见原表」**：区域不是 `TableObject`、不进 "
                     "Pack/Writer 材料、不给区域里的数字任何格级或事实级资格，也不证明 Contract "
                     "`set_complete`。即便人确认了「区域完整清晰忠实」，那句话覆盖的也只是**区域"
                     "本身**，数字能不能写进正文仍由各自的资格决定。")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def registered_sha256_from_source_entries(entries: Iterable[Any]) -> dict[str, str]:
    """从登记来源清单条目取 `{document_id: 登记哈希}`（只读，不改登记）。"""
    out: dict[str, str] = {}
    for entry in entries:
        document_id = str(getattr(entry, "document_id", "") or "")
        if document_id:
            out[document_id] = str(getattr(entry, "file_sha256", "") or "")
    return out

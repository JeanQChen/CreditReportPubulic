"""来源归属轴（`AGT §5` 三条日期轴的 (b) 条，定点澄清 2026-09-27）。

**本模块只做一件事**：把**已登记**的来源身份折成一条读者可读、可复核的**归属语**
（这份正文说的是**哪一份材料**、**哪个版本**、**哪一页**、**可核实的披露日是哪天**）。

三条日期轴里，本模块承载的是 (b) **来源归属**，它与另外两条**绝不互相顶替**：

- (a) **事实适用期**（「2025 年度」「截至 2025-12-31」）：高风险、必须逐字来自合格
  Claim / 权威事实或材料原文；**本模块不生成、不推断、不补写**它。
- (b) **来源归属**（本模块）：材料身份 + 版本 + 页码 + 可核实披露日。它回答的是
  「这是该材料披露的情况」，**不是**「截至报告生成日仍然如此」。
- (c) **`report_as_of`**（报告生成日）：唯一真值来源是 `harness/report_clock.py`；
  本模块**不读、不写、不代替**它。

因此本模块的两条硬纪律：

1. **确定性**：归属语由系统从登记身份渲染，**模型一个字也不能写**（写者既不能
   自造材料名，也不能自造日期）。它与行文里的散文**分开**：散文里不许出现日期，
   日期只出现在这条由系统附加的归属语里。
2. **不可核实就明说不可核实**：披露日来自 `HS.DisclosureDateState`。只有
   `state == "verified"` 才渲染出日期；否则一律渲染为「披露日未知」，并且
   **不得**用入库时间、PDF 元数据、上传时间或财务期末冒充（这几个名字在
   `FORBIDDEN_DISCLOSURE_SUBSTITUTES` 里被逐个点名，`assert_no_forbidden_substitute`
   逐条查它们有没有混进日期位）。

与 `harness/source_manifest.py` 的分工：那边判「这份材料的披露日到底可不可核实」，
本模块只**消费**那个判断结果并渲染，不重新判一次（两处各判一份就制造了两个真值）。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from harness import source_manifest as SM

# ---------------------------------------------------------------------------
# 版本与封闭词表
# ---------------------------------------------------------------------------

#: 归属语的口径版本。它进 `CitationDisplay` 的渲染身份：口径变了，渲染出的引用就变，
#: 读者与复核者据此能区分「同一句话按旧口径渲染」与「按新口径渲染」。
SOURCE_ATTRIBUTION_VERSION = "srattr-1"

#: 披露日的可核实状态（与 `HS.DisclosureDateState.state` **同域**，不另立一套）。
ATTRIBUTION_DISCLOSURE_STATES = ("verified", "unknown")

#: **不得**被用来顶替披露日的东西（逐条点名，便于审计）。
#: 前三条来自 `AGENTS.md §5`「披露日未知就标未知，不能用上传日、PDF 元数据或财务期末代替」，
#: 第四条是入库时间（`HS._DISCLOSURE_UNKNOWN_BASIS_REQUIRED` 已经点过名的那一个）。
FORBIDDEN_DISCLOSURE_SUBSTITUTES = (
    "ingestion_time",          # 入库时间
    "pdf_metadata",            # PDF 元数据（创建/修改时间）
    "upload_time",             # 上传时间
    "content_report_period_end",  # 财务期末（那是事实适用期的端点，不是披露日）
)

#: 归属语渲染里「披露日不可核实」时使用的**固定**表述（不得换成暗示有日期的写法）。
DISCLOSURE_UNKNOWN_LABEL = "披露日未知"

_ISO_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class SourceAttributionError(Exception):
    """归属语构造/渲染的 fail-closed 错误。

    宁可不渲染这条归属语，也不渲染一条把日期说错的归属语：错日期比没日期更坏，
    因为读者会把它当成可核实的披露日引用出去。
    """


# ---------------------------------------------------------------------------
# 值对象
# ---------------------------------------------------------------------------

def _canonical_json(body: Any) -> str:
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_nonempty(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceAttributionError(f"{what} 必须是非空字符串，实际 {value!r}")
    return value


@dataclass(frozen=True)
class SourceAttribution:
    """一条读者可读的来源归属（材料身份 + 页码 + 可核实披露日）。

    字段纪律：

    - `document_id` / `document_version` 是**登记身份**（`SourceDocumentKey` 的轴），
      不含路径、不含 run id、不含 `created_at`；
    - `document_label` 是给读者看的材料名（登记表的 `source_name` + 类型标签），
      **不是**模型写的、也不是从正文里猜的；
    - `page_number` 是精确页码（1 起）；没有页码就不构造本对象（宁缺毋滥）；
    - `disclosure_state` 是 `ATTRIBUTION_DISCLOSURE_STATES` 的封闭值；
    - `disclosure_date` 只在 `verified` 时非空，且必须是 `YYYY-MM-DD`；
    - `disclosure_basis` 是「为什么可核实 / 为什么不可核实」的原文依据，不得为空。
    """

    attribution_id: str
    attribution_version: str
    document_id: str
    document_version: str
    document_label: str
    page_number: int
    disclosure_date: str
    disclosure_state: str
    disclosure_basis: str

    def __post_init__(self) -> None:
        _require_nonempty(self.attribution_version, "SourceAttribution.attribution_version")
        if self.attribution_version != SOURCE_ATTRIBUTION_VERSION:
            raise SourceAttributionError(
                f"SourceAttribution.attribution_version={self.attribution_version!r} "
                f"不是当前口径 {SOURCE_ATTRIBUTION_VERSION!r}")
        _require_nonempty(self.document_id, "SourceAttribution.document_id")
        _require_nonempty(self.document_version, "SourceAttribution.document_version")
        _require_nonempty(self.document_label, "SourceAttribution.document_label")
        _require_nonempty(self.disclosure_basis, "SourceAttribution.disclosure_basis")
        if "/" in self.document_id or "\\" in self.document_id \
                or self.document_id.lower().endswith(".pdf"):
            raise SourceAttributionError(
                f"SourceAttribution.document_id 不得是路径或文件名（内容寻址），"
                f"实际 {self.document_id!r}")
        if not isinstance(self.page_number, int) or isinstance(self.page_number, bool) \
                or self.page_number < 1:
            raise SourceAttributionError(
                f"SourceAttribution.page_number 必须是 >=1 的整数，实际 {self.page_number!r}"
                "（没有精确页码就不构造归属语，不用「约第几页」顶）")
        if self.disclosure_state not in ATTRIBUTION_DISCLOSURE_STATES:
            raise SourceAttributionError(
                f"SourceAttribution.disclosure_state={self.disclosure_state!r} "
                f"不在 {list(ATTRIBUTION_DISCLOSURE_STATES)} 内")
        if self.disclosure_state == "verified":
            if not self.disclosure_date:
                raise SourceAttributionError(
                    "disclosure_state='verified' 但 disclosure_date 为空")
            if not _ISO_DAY_RE.match(self.disclosure_date):
                raise SourceAttributionError(
                    f"披露日必须是日粒度的 YYYY-MM-DD，实际 {self.disclosure_date!r}"
                    "（月粒度线索不得升格成披露日）")
        else:
            if self.disclosure_date:
                raise SourceAttributionError(
                    f"disclosure_state='unknown' 但 disclosure_date={self.disclosure_date!r}："
                    "不可核实的披露日不得渲染出一个日期")
        expected = derive_attribution_id(self)
        if self.attribution_id != expected:
            raise SourceAttributionError(
                f"SourceAttribution.attribution_id 与内容不符：声明 {self.attribution_id!r}，"
                f"应为 {expected!r}")

    def identity_body(self) -> dict:
        return {
            "kind": "source_attribution",
            "attribution_version": self.attribution_version,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "document_label": self.document_label,
            "page_number": self.page_number,
            "disclosure_date": self.disclosure_date,
            "disclosure_state": self.disclosure_state,
            "disclosure_basis": self.disclosure_basis,
        }

    def to_dict(self) -> dict:
        body = self.identity_body()
        body["attribution_id"] = self.attribution_id
        return body

    @classmethod
    def from_dict(cls, d: Any) -> "SourceAttribution":
        if not isinstance(d, dict):
            raise SourceAttributionError("SourceAttribution 必须是 mapping")
        expected = {"attribution_id", "kind", "attribution_version", "document_id",
                    "document_version", "document_label", "page_number",
                    "disclosure_date", "disclosure_state", "disclosure_basis"}
        unknown = sorted(set(d) - expected)
        if unknown:
            raise SourceAttributionError(f"SourceAttribution 含未登记字段 {unknown}")
        return cls(
            attribution_id=_require_nonempty(d.get("attribution_id"),
                                             "SourceAttribution.attribution_id"),
            attribution_version=_require_nonempty(
                d.get("attribution_version"), "SourceAttribution.attribution_version"),
            document_id=_require_nonempty(d.get("document_id"), "SourceAttribution.document_id"),
            document_version=_require_nonempty(d.get("document_version"),
                                               "SourceAttribution.document_version"),
            document_label=_require_nonempty(d.get("document_label"),
                                             "SourceAttribution.document_label"),
            page_number=d.get("page_number"),
            disclosure_date=str(d.get("disclosure_date") or ""),
            disclosure_state=_require_nonempty(
                d.get("disclosure_state"), "SourceAttribution.disclosure_state"),
            disclosure_basis=_require_nonempty(d.get("disclosure_basis"),
                                               "SourceAttribution.disclosure_basis"),
        )


def _derive_id_from_body(body: dict) -> str:
    digest = hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()
    return "srattr_" + digest[:24]


def derive_attribution_id(attr: Any) -> str:
    body = attr.identity_body() if isinstance(attr, SourceAttribution) else attr.body
    return _derive_id_from_body(body)


# ---------------------------------------------------------------------------
# 构造（唯一口径）
# ---------------------------------------------------------------------------

def assert_no_forbidden_substitute(*, disclosure_date: str, disclosure_state: str,
                                   document_id: str, label: str) -> None:
    """确认渲染出的日期**不是**被禁的替代物。

    对 `verified` 的日期，检查它没有同时出现在别的地方被当成「日期」用：
    最直接的一条是它不得等于入库时间，也不得是财务期末那种「期间端点」的写法。
    这条检查是**逐条点名**的（`FORBIDDEN_DISCLOSURE_SUBSTITUTES`），不是泛化 validator。
    """
    if disclosure_state != "verified":
        return
    if len(disclosure_date) != 10 or not _ISO_DAY_RE.match(disclosure_date):
        raise SourceAttributionError(
            f"{document_id!r} 的披露日 {disclosure_date!r} 不是日粒度日期"
            f"（替代物 {list(FORBIDDEN_DISCLOSURE_SUBSTITUTES)} 一律不得混进日期位）")
    del label


def build_source_attribution(
    *,
    document_id: str,
    document_version: str,
    document_label: str,
    page_number: int,
    disclosure: SM.DisclosureDateState,
) -> SourceAttribution:
    """从**已登记**的来源身份与披露状态确定性构造归属语（唯一构造实现）。

    `disclosure` 必须来自 `harness/source_manifest.py` 的判断结果：本函数不重新判一次
    「这份材料的披露日可不可核实」——两处各判一份就制造了两个真值，而这两个真值一旦
    不一致，读者看到的那一个未必是核验过的那一个。

    不可核实的情形**照实带出来**（`state="unknown"` + 空日期），而不是省略这条归属语：
    省略会让读者分不清「这份材料没有披露日」与「系统没去查」。
    """
    if not isinstance(disclosure, SM.DisclosureDateState):
        raise SourceAttributionError(
            f"disclosure 必须是 DisclosureDateState，实际 {type(disclosure).__name__}"
            "（不得用裸字符串或别的字段顶替披露日）")
    # 判断结果自身的纪律（verified 必须带得出日；unknown 必须写明为什么不可核实、
    # 且不得带日期）由唯一实现处复核，这里不另写一套判据。
    SM.assert_disclosure_state(disclosure)

    state = disclosure.state
    date = disclosure.date or ""
    if state == "verified":
        if date == disclosure.ingestion_time:
            raise SourceAttributionError(
                f"{document_id!r} 的披露日与入库时间相同（{date!r}）：入库时间不得冒充披露日")
        assert_no_forbidden_substitute(disclosure_date=date, disclosure_state=state,
                                       document_id=document_id, label=document_label)

    body = {
        "kind": "source_attribution",
        "attribution_version": SOURCE_ATTRIBUTION_VERSION,
        "document_id": document_id,
        "document_version": document_version,
        "document_label": document_label,
        "page_number": page_number,
        "disclosure_date": date,
        "disclosure_state": state,
        "disclosure_basis": disclosure.basis or "",
    }
    return SourceAttribution(
        attribution_id=_derive_id_from_body(body),
        attribution_version=SOURCE_ATTRIBUTION_VERSION,
        document_id=document_id,
        document_version=document_version,
        document_label=document_label,
        page_number=page_number,
        disclosure_date=date,
        disclosure_state=state,
        disclosure_basis=disclosure.basis or "",
    )


# ---------------------------------------------------------------------------
# 渲染（确定性；模型不参与）
# ---------------------------------------------------------------------------

def render_source_attribution(attr: SourceAttribution) -> str:
    """渲染归属语：`据 <label>（<document_id>@<version>），披露日 <date|未知>，第 N 页`。

    披露日不可核实时就写 `披露日未知`（`DISCLOSURE_UNKNOWN_LABEL`），**不留空格、
    不写「近期」「年内」「报告期末」**——任何含糊的替代说法都会把「不可核实」读成
    「有日期只是没写全」。
    """
    date_part = (f"披露日 {attr.disclosure_date}"
                 if attr.disclosure_state == "verified" else DISCLOSURE_UNKNOWN_LABEL)
    return (f"据 {attr.document_label}"
            f"（{attr.document_id}@{attr.document_version}），"
            f"{date_part}，第 {attr.page_number} 页")


def attributions_by_key(rows: Sequence[Any], *,
                        entries_by_document: Mapping[Any, Any]
                        ) -> dict[str, SourceAttribution]:
    """把 `(key, document_id, document_version, source_name, page_number)` 行折成
    `{key: SourceAttribution}`。

    `key` 是**调用方的身份轴**：legacy 发布态按 `evidence_id` 键（见
    `sections/publishable_report.py`），M930-3 读者面的逐句回指按 `material_id` 键。
    同一个渲染器，两种键——**不是**两套归属语口径。

    `entries_by_document` 的值只需要有 `.source_name` 与 `.disclosure`
    （`SM.SourceManifestEntry`，或本模块的 `RegisteredSourceIdentity`）：只要那两件，
    是因为归属语只消费那两件——多要一个字段就等于给「用别的字段凑一个披露日」留了口子。

    查不到登记条目、查不到页码、或登记条目的披露状态本身不合法时，**这一条跳过**
    （该引用退回调用方的既有中性渲染），而不是造一条缺字段的归属语。跳过是**逐条**的：
    一条查不到不影响其它条渲染出来。
    """
    out: dict[str, SourceAttribution] = {}
    for row in rows or ():
        try:
            key, document_id, document_version, source_name, page_number = row
        except (TypeError, ValueError):
            continue
        entry = entries_by_document.get((str(document_id), str(document_version)))
        if entry is None:
            continue
        if not isinstance(page_number, int) or isinstance(page_number, bool) \
                or page_number < 1:
            continue
        label = str(source_name or "").strip() or str(getattr(entry, "source_name", "") or "")
        try:
            out[str(key)] = build_source_attribution(
                document_id=str(document_id),
                document_version=str(document_version),
                document_label=label,
                page_number=page_number,
                disclosure=entry.disclosure,
            )
        except (SourceAttributionError, ValueError):
            # 登记条目的披露状态自身不合规：**这一条**不渲染归属语。
            # 不在这里把它洗成 unknown——那会把「登记数据有问题」伪装成「披露日确实未知」。
            # `ValueError` 是 `harness/source_manifest.assert_disclosure_state` 的既有抛法
            # （那条纪律的唯一实现）；只读路径（登记数据从 JSON 回读）同样可能踩到它，
            # 因此这里一并按「这一条跳过」处理。try 块里**只有** `build_source_attribution`
            # 一个调用，所以这个 except 面不会顺手吞掉别处的 ValueError。
            continue
    return out


# ---------------------------------------------------------------------------
# 只读登记入口（消费已落盘的 `source_manifest.json`）
# ---------------------------------------------------------------------------

#: `RegisteredSourceIdentity.from_dict` 接受的**恰好**这些字段。多一个就拒——
#: 少一个字段的「宽松读入」会让后人以为某份材料有披露日而其实没有。
REGISTERED_IDENTITY_FIELDS = ("document_id", "document_version", "source_name", "disclosure")


@dataclass(frozen=True)
class RegisteredSourceIdentity:
    """**只读**的登记身份：归属语渲染所需的**最小**字段集。

    它不是 `SM.SourceManifestEntry` 的替代品，也不是第二份清单：它只在**读回已落盘的
    `source_manifest.json`**（离线重放、历史 run 只读复核）时用——那条路径上没有活着的
    `SourceManifest` 对象，硬造一个完整的 `SourceManifestEntry` 只会把「类型判断依据」
    「内容报告期间」「政策后果」这些**与归属语无关**的字段从 JSON 里随手补出来。

    纪律与 `SourceAttribution` 一致：披露状态仍然只能来自 `SM.DisclosureDateState`，
    仍然过 `SM.assert_disclosure_state`（在 `build_source_attribution` 里），因此「不可核实
    就明说不可核实」与「替代物不得冒充」不会因为走了只读路径而少一道。
    """

    document_id: str
    document_version: str
    source_name: str
    disclosure: SM.DisclosureDateState

    @classmethod
    def from_entry_dict(cls, d: Any) -> "RegisteredSourceIdentity":
        if not isinstance(d, Mapping):
            raise SourceAttributionError("RegisteredSourceIdentity 必须是 mapping")
        missing = [f for f in REGISTERED_IDENTITY_FIELDS if f not in d]
        if missing:
            raise SourceAttributionError(
                f"登记条目缺字段 {missing}：只读入口只接受 {list(REGISTERED_IDENTITY_FIELDS)}"
                "（缺字段就地拒，不猜一个披露日出来）")
        raw = d["disclosure"]
        if not isinstance(raw, Mapping):
            raise SourceAttributionError("登记条目的 disclosure 必须是 mapping")
        return cls(
            document_id=_require_nonempty(d["document_id"], "entry.document_id"),
            document_version=_require_nonempty(d["document_version"], "entry.document_version"),
            source_name=str(d.get("source_name") or ""),
            disclosure=SM.DisclosureDateState(
                date=raw.get("date"),
                state=str(raw.get("state") or ""),
                period_hint=raw.get("period_hint"),
                period_hint_precision=raw.get("period_hint_precision"),
                basis=str(raw.get("basis") or ""),
                ingestion_time=str(raw.get("ingestion_time") or ""),
            ),
        )


def source_registry_from_manifest_document(document: Any) -> dict[tuple[str, str],
                                                                 RegisteredSourceIdentity]:
    """已落盘的 `source_manifest.json` 内容 → `{(document_id, document_version): 登记身份}`。

    逐条 `from_entry_dict`：**一条不合规只丢它自己**，其余照常进表。整份丢掉的写法会让
    「有一份材料的登记数据坏了」变成「这一轮没有任何登记材料」，把局部问题读成全局缺失。
    """
    if not isinstance(document, Mapping):
        raise SourceAttributionError("source_manifest 文档必须是 mapping")
    entries = document.get("entries")
    if not isinstance(entries, (list, tuple)):
        raise SourceAttributionError("source_manifest 文档的 entries 必须是序列")
    out: dict[tuple[str, str], RegisteredSourceIdentity] = {}
    for raw in entries:
        try:
            identity = RegisteredSourceIdentity.from_entry_dict(raw)
        except SourceAttributionError:
            continue
        out[(identity.document_id, identity.document_version)] = identity
    return out


def attributions_by_evidence(rows: Sequence[Any], *,
                             entries_by_document: Mapping[Any, Any]
                             ) -> dict[str, SourceAttribution]:
    """`attributions_by_key` 的 `evidence_id` 键别名（legacy 发布态路径）。"""
    return attributions_by_key(rows, entries_by_document=entries_by_document)

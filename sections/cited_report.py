"""§0.20 第二步（收口）：新的**报告版本身份**与「逐句标注的不可发布预览」（`crpv-3`）。

本模块把一条链的四样产出收在**一个新的版本接口**下，而不是塞进旧
`ReportVersion`/`SectionDraft`/`SectionClaim` 那套 wire：

```
输入清单 fingerprint ─┐
正文草稿 id/fingerprint ─┼─→  report_version（版本锚，`crpv_*`）
机械核对报告 id/fingerprint ─┤
                        └─→  CitedReportVersion（完整记录，`cpr_*`）
审阅 bundle + 意见
```

**两个 id 是刻意的。** `report_version` 只由**写作侧输入**派生（清单、草稿、依赖指纹），
因此它可以被审阅意见引用而不产生环——`ReviewIssue.report_version` 要指向"我在审阅哪一版
正文"，若版本锚里含意见 id，算锚就要先有意见、算意见又要先有锚。
`record_id` 才是把核对与审阅**都**收进来的完整身份。

## 四个状态**分列**，任何一个都不得压成"成功"

§0.20 明文要求状态至少分开记录，本模块逐项照做：

| 轴 | 取值 | 谁决定 |
|---|---|---|
| ① 正文预览是否可读 | `preview_unavailable` / `draft_previewable` | 有没有可读的正文行 |
| ② 逐句确定性硬核对 | `no_prose` / `has_hard_errors` / `no_hard_errors` | `sentence_check` 的机械结论 |
| ③ 独立审阅 | `system_review_not_run` / `not_passed` / `passed_awaiting_human` | 审阅是否跑完、有无 blocking，**且不得与②矛盾** |
| ④ 人工接受 | `human_not_reviewed` / `human_reviewed` | **由人带过**，本模块不产生 |

**③ 不得越过 ②**（`crpv-3`）：只要 ② 是 `has_hard_errors`，③ 就**不可能**取
`passed_awaiting_human`。审阅自己一条 blocking 都没提、机械层却有硬错误时，③ 记
`system_review_not_passed`——理由是"系统放行"是一个**跨轴**的结论，而硬错误是可机械证明的
底线失败，它不需要审阅同意就成立。审阅在这几句上的独立意见照样逐句显示（含
`hard_error_override_sentence_ids`：它说 supported、机械层说硬错误，两条并列摊开给读者看）。

**审阅没跑完 ≠ 没有正文**（`crpv-3`）：审阅一轮解析失败时，正文、逐句硬核对、缺口与补件需求
全部照旧落盘并**可见**，另加一条 typed `review_failure`，`process_state` 记
`flow_incomplete`。禁止的是**伪造**一份"已完成的正式报告"，不是禁止把已有的草稿给人看。

本模块**没有**"可发布"这个产出：`publishability` 是恒定常量 `not_publishable`，构造期强制
（与 `sentence_check.SentenceCheckReport` 同一手法）。它给出的不是"通过/不通过"，而是一份
**`release_blockers()`**——"如果要发布，现在还差哪几项"。把"没发现问题"直接写成"可以发布"，
正是 §0.20 要求分开记录的那件事。

## 「人工修改」只有展示入口

`CITED_HUMAN_EDIT_MODES` 只有 `display_entry_only` 一档：本批不实施编辑、不回写、不续跑。
预览页上那一行是**状态展示**，不是一个可点的功能；把它写成"已支持人工修改"是错的。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from assurance import schema as AS
from sections import cited_financial_table as CFT
from sections import cited_review as CR
from sections import cited_writer as CW
from sections import narrative_schema as NS
from sections import sentence_check as SC
from sections.sentence_check import SentenceCheckReport

__all__ = [
    "CITED_REPORT_VERSION_SCHEMA_VERSION",
    "CITED_REPORT_POLICY_VERSION",
    "CITED_PREVIEW_PUBLISHABILITY",
    "CITED_HUMAN_EDIT_MODES",
    "CITED_RELEASE_BLOCKER_KINDS",
    "CITED_REVIEW_FAILURE_KINDS",
    "CITED_ROW_REVIEW_STATES",
    "CitedReportError",
    "failure_reason_label",
    "CitedReportVersion",
    "CitedSentencePreviewRow",
    "CitedSectionPreview",
    "derive_report_version",
    "derive_report_record_id",
    "build_cited_report_version",
    "build_cited_section_preview",
    "render_cited_preview_markdown",
    "CITED_GAP_BINS",
    "CITED_GAP_BLOCKING_BINS",
    "CITED_GAP_NOT_APPLICABLE_POLICIES",
    "CITED_GAP_RELEASE_BLOCKING_POLICIES",
    "CITED_GAP_NON_BLOCKING_BINS",
    "CitedGapBin",
    "classify_draft_gaps",
    "required_gap_count_from_bins",
    "gap_axes_from_bins",
    "classify_check_report",
    "FROZEN_CONTRACT_ASSET",
    "frozen_contract_identity",
]

# ---------------------------------------------------------------------------
# 草稿缺口的**分桶**（`crpp-5`）
# ---------------------------------------------------------------------------
# 现场：`m930_3_cited_real_company_cp22_r1` 的 8 条草稿缺口**全部**进了
# `CitedReportVersion.required_gap_count`，于是 `release_blockers()` 无条件吐
# `required_gaps_present`。这不成立：其中两条是 `display_tier: optional_body` 且适用性政策逐字
# 写着「仅在有可比期间时评估」的**跨期变化**栏（`company_customer_concentration
# .customer_concentration_change` / `company_supplier_concentration.supplier_concentration_change`），
# 没有可比期间时 Contract 自己判 `NOT_APPLICABLE`；另有一条
# （`company_customer_concentration.customer_anonymity`）的 `applicability_policy` 与
# `missing_policy` 都逐字写着「不阻断」。
#
# 「可选栏目也要全算成必需阻断」与「有缺口就当没缺口」是**两个**错误，本模块只做前者到后者的
# 那条正中间的事：**把分桶如实算出来，并且把每一格是按哪一条 Contract 政策分进去的一起留下**。
# 判据是纯查表，不做任何语义判断，也不读正文。

#: 草稿缺口的分桶（**封闭五档**，`crpp-5`）。逐条给出一档，不聚成一个布尔。
#:
#: * `required`：Contract 要求写、且没有任何可豁免声明 —— 它是 `required_gap_count` 的**唯一**
#:   正常来源。
#: * `optional`：`display_tier: optional_body` —— 写它更好，缺它不是「必需未覆盖」。
#: * `not_applicable`：`applicability_policy` 落在 :data:`CITED_GAP_NOT_APPLICABLE_POLICIES`
#:   里 —— 冻结 Contract 的 `missing_policies` 对这两条政策逐字写着「记为合法不适用
#:   （NOT_APPLICABLE）、**不阻断**」。
#: * `diagnostic`：`display_tier: diagnostic_only` —— 诊断栏，本就不进正文门。
#: * `unresolved`：**在冻结 Contract 里找不到这一栏**。fail-closed：它计入
#:   `required_gap_count`，但**另立一档**报出来 —— 「查不到」与「查到了、确实必需」对下游是
#:   两条不同的指令，不得合并成一条。
CITED_GAP_BINS = ("required", "optional", "not_applicable", "diagnostic", "unresolved")

#: 计数进 `required_gap_count` 的两档。`unresolved` 在列：**查不到政策时按必需处理**，
#: 这是 fail-closed，不是「宽容」。
CITED_GAP_BLOCKING_BINS = ("required", "unresolved")

#: 冻结 Contract 逐字声明「合法不适用、不阻断」的两条适用性政策
#: （`templates/contracts/standard_v3.yaml` 的 `missing_policies`）。**封闭集合**：
#: 加一条就是改判定集，必须与 `CITED_REPORT_POLICY_VERSION` 一起升版。
CITED_GAP_NOT_APPLICABLE_POLICIES = (
    "not_applicable_no_comparable_period",
    "not_applicable_no_plan",
)

#: 缺一档时 `display_tier` 的落点。它**不是**兜底值：`required_body` 是「必须写」那一档，
#: 字段缺失时按它处理是 fail-closed 的方向，`unresolved` 那一档另有条件（查不到栏目）。
_REQUIRED_BODY = "required_body"


@dataclass(frozen=True)
class CitedGapBin:
    """一条草稿缺口的**桶**：分到哪一档，以及**凭什么**分到那一档。

    `policy_basis` 是给人复核用的一句话，逐字带上决定分桶的那一条 Contract 声明。没有它，
    「这一条为什么不算阻断」只剩下一个桶名，读的人无法当场反驳——那正是本批要避免的那种读数。
    """

    gap_id: str
    requirement_text: str
    gap_bin: str
    #: 分桶依据（封闭几个取值之一：`contract_not_applicable_policy` /
    #: `contract_display_tier_optional` / `contract_display_tier_diagnostic` /
    #: `contract_required_body` / `contract_aspect_not_found`）。
    policy_basis: str
    #: 命中/未命中的 Contract 栏目 id。`unresolved` 时为空串（**结论**：查不到，不是没记）。
    contract_aspect_id: str = ""
    display_tier: str = ""
    applicability_policy: str = ""
    blocking_policy: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.gap_bin not in CITED_GAP_BINS:
            raise CitedReportError(
                f"CitedGapBin.gap_bin={self.gap_bin!r} 不在封闭词表 {list(CITED_GAP_BINS)} 内")
        object.__setattr__(self, "blocking_policy",
                           tuple(str(b) for b in (self.blocking_policy or ())))

    def to_dict(self) -> dict:
        return {"gap_id": self.gap_id, "requirement_text": self.requirement_text,
                "gap_bin": self.gap_bin, "policy_basis": self.policy_basis,
                "contract_aspect_id": self.contract_aspect_id,
                "display_tier": self.display_tier,
                "applicability_policy": self.applicability_policy,
                "blocking_policy": list(self.blocking_policy)}


def _contract_aspect_index(aspects: Any) -> dict[str, Any]:
    """`requirement_text` → Contract 栏目。同一要求文本对到**不同**栏目时**整条拒绝**。

    键只能是 `requirement_text`：那是缺口对象**唯一**带过来的 Contract 面（草稿的
    `requirement_text` 逐字取自冻结 Contract，见 `CitedSubsectionSpec`）。要求文本在本次范围内
    一对多时，本函数**不猜**：猜错的一侧会把一条必需缺口静默地记成可选，而那种错在产物上
    看不出来。宁可整段 fail-closed。
    """
    index: dict[str, Any] = {}
    ambiguous: set[str] = set()
    for aspect in aspects or ():
        text = str(getattr(aspect, "requirement_text", "") or "").strip()
        if not text:
            continue
        if text in index and str(getattr(index[text], "aspect_id", "")) != str(
                getattr(aspect, "aspect_id", "")):
            ambiguous.add(text)
            continue
        index.setdefault(text, aspect)
    for text in ambiguous:
        index.pop(text, None)
    return index


#: 冻结 Contract 的载体路径（与 R2 材料验收、本仓其余部分**同一份**资产）。
FROZEN_CONTRACT_ASSET = "templates/contracts/standard_v3.yaml"


@lru_cache(maxsize=1)
def frozen_contract_identity() -> tuple[str, str]:
    """当前盘上冻结 Contract 的 `(contract_version, 内容指纹)`（`crpp-6`）。

    缺口分桶的**每一条**判定依据（`missing_policies` / `display_tier` / `blocking_policy`）
    都来自这份资产，因此分桶结论只有在**它**被指名时才可复核。写侧把这两个值记进
    `cited_gap_bins.json`（`contract_version` / `contract_fingerprint`），读侧
    （`assurance/cited_controller.py`）用它比对**同一份**资产——两侧共用本函数，不各写一遍。

    经 `contracts.loader_v2` 加载 + `contracts.validator_v2` 校验（与 R2 同一套），指纹算法
    用 `contracts.schema_v2.content_fingerprint`（排除冻结元数据键）。离线、确定性、只读：
    不联网、不调模型、不写盘、不碰数据库。加载或校验失败一律抛出——那时没有可比的 Contract
    身份，宁可拒绝也不拿占位值去比。
    """
    from contracts import loader_v2 as LV2
    from contracts import schema_v2 as SV2
    from contracts import validator_v2 as VV2
    path = Path(__file__).resolve().parents[1] / FROZEN_CONTRACT_ASSET
    contract = LV2.load_contract_v2(str(path))
    result = VV2.validate_contract_v2(contract)
    if not result.valid:
        raise CitedReportError(
            f"冻结 Contract 校验失败（{len(result.errors)} 处）: {result.errors[:3]}")
    return str(contract.contract_version), SV2.content_fingerprint(contract.raw)


def classify_draft_gaps(*, gaps: Any, aspects: Any) -> tuple[CitedGapBin, ...]:
    """把草稿缺口逐条分桶（`crpp-5`）。**纯查表**，输入顺序即输出顺序。

    `aspects` 是本节范围内冻结 Contract 的栏目对象（只需带 `aspect_id` / `requirement_text` /
    `display_tier` / `applicability_policy` / `blocking_policy` 五个属性）。它由调用方从既有的
    Contract 读视图投影进来，**本函数不读盘、不猜政策**。

    判定顺序**必须**是「先适用性、后展示档」：`customer_anonymity` 是 `required_body` +
    `not_applicable_no_plan`，先看展示档会把它记成必需——而 Contract 的 `missing_policies`
    对那条政策逐字写着「不阻断」。
    """
    index = _contract_aspect_index(aspects)
    out: list[CitedGapBin] = []
    for gap in gaps or ():
        text = str(getattr(gap, "requirement_text", "") or "").strip()
        aspect = index.get(text)
        if aspect is None:
            out.append(CitedGapBin(
                gap_id=str(getattr(gap, "gap_id", "") or ""), requirement_text=text,
                gap_bin="unresolved", policy_basis="contract_aspect_not_found"))
            continue
        policy = str(getattr(aspect, "applicability_policy", "") or "")
        tier = str(getattr(aspect, "display_tier", "") or "")
        if policy in CITED_GAP_NOT_APPLICABLE_POLICIES:
            gap_bin, basis = "not_applicable", "contract_not_applicable_policy"
        elif tier == "optional_body":
            gap_bin, basis = "optional", "contract_display_tier_optional"
        elif tier == "diagnostic_only":
            gap_bin, basis = "diagnostic", "contract_display_tier_diagnostic"
        else:
            gap_bin, basis = "required", "contract_required_body"
        out.append(CitedGapBin(
            gap_id=str(getattr(gap, "gap_id", "") or ""), requirement_text=text,
            gap_bin=gap_bin, policy_basis=basis,
            contract_aspect_id=str(getattr(aspect, "aspect_id", "") or ""),
            display_tier=tier, applicability_policy=policy,
            blocking_policy=tuple(str(b) for b in (getattr(aspect, "blocking_policy", ()) or ()))))
    return tuple(out)


def required_gap_count_from_bins(bins: Sequence[CitedGapBin]) -> int:
    """`required_gap_count` 的**唯一**算法：只数 :data:`CITED_GAP_BLOCKING_BINS` 里的那两档。

    它**不是** `len(draft.gaps)`：那个数把可选/不适用/诊断栏一起算成必需覆盖未达，
    `release_blockers()` 于是无条件吐 `required_gaps_present`。
    """
    return sum(1 for b in bins if b.gap_bin in CITED_GAP_BLOCKING_BINS)


#: 冻结 Contract `blocking_policy` 里**表示阻断**的封闭取值（`templates/contracts/standard_v3.yaml`
#: 只出现这四个：`JOB_BLOCKED` / `REPORT_BLOCKED` / `SECTION_BLOCKED` / `NONE`）。
#: `NONE` **不**在表里——它的语义逐字就是「不阻断」。加一条就是改判定集，必须与
#: `CITED_REPORT_POLICY_VERSION` 一起升版。
CITED_GAP_RELEASE_BLOCKING_POLICIES = ("JOB_BLOCKED", "REPORT_BLOCKED", "SECTION_BLOCKED")

#: 缺口分桶里**永不**成为放行阻断的那两档（冻结 Contract 对它们逐字写着「不阻断」或
#: 「不进正文门」）：`not_applicable`（`missing_policies` 明文 NOT_APPLICABLE）与
#: `diagnostic`（诊断栏，本就不进正文门）。
CITED_GAP_NON_BLOCKING_BINS = ("not_applicable", "diagnostic")


def _gap_is_release_blocking(bin_row: CitedGapBin) -> bool:
    """这一条缺口是否**按冻结 Contract 的 `blocking_policy`** 阻断放行。

    三条判定，**按序**：
      1. 分桶落在 :data:`CITED_GAP_NON_BLOCKING_BINS` ⇒ **不**阻断（Contract 自己说的）。
      2. `unresolved`（在冻结 Contract 里查不到这一栏）⇒ **阻断**。fail-closed：查不到政策
         不等于没有政策，把「查不到」当「不阻断」正是本模块要防的那个假门。
      3. 其余看该栏目的 `blocking_policy` 与 :data:`CITED_GAP_RELEASE_BLOCKING_POLICIES`
         有没有交集。**空集也算阻断**（同一条 fail-closed 理由：声明缺了，不猜成 NONE）。
    """
    if bin_row.gap_bin in CITED_GAP_NON_BLOCKING_BINS:
        return False
    if bin_row.gap_bin == "unresolved":
        return True
    declared = {str(tok) for tok in (bin_row.blocking_policy or ())}
    if not declared:
        return True
    return bool(declared & set(CITED_GAP_RELEASE_BLOCKING_POLICIES))


def gap_axes_from_bins(bins: Sequence[CitedGapBin]) -> dict[str, Any]:
    """缺口的两条**正交轴**（`crpp-6` 起，读侧现算；**不进**任何 wire 的字段集）。

    一个「缺口数」同时被两件不同的事借用过，而它们对下游是两条不同的指令：

    * **正文应写未写**（`required_body_gap_count`）：Contract 判这一栏该进正文
      （`display_tier: required_body` 且不适用性豁免），而本轮没有材料撑起它。它说的是
      **内容**缺了。
    * **放行阻断**（`release_blocking_gap_count`）：按该栏目的 `blocking_policy`
      （`SECTION_BLOCKED` / `REPORT_BLOCKED` / `JOB_BLOCKED`），这一条在**系统放行**上算不算
      拦路的。它说的是**门**。

    两条轴**不重合**，而且 cp22 的真实现场正是它们分开的例子：客户当前 / 供应商当前两条
    是 `required_body` 但 `blocking_policy: [NONE]`——内容该补，**单独**不构成系统放行阻断；
    收入占比 / 成本与毛利 / 期间·单位·口径三条则同时 `SECTION_BLOCKED`。

    `required_gap_count`（:func:`required_gap_count_from_bins`，`crpp-5`）是**第三个数**，
    与上面两条都不同：它是「必需且尚未解决」的条数（`required` + `unresolved`）。三个数
    一起给，**不合并**。
    """
    rows = tuple(bins or ())
    required_body = sum(1 for b in rows if b.gap_bin == "required")
    release_blocking = sum(1 for b in rows if _gap_is_release_blocking(b))
    by_bin: dict[str, int] = {name: 0 for name in CITED_GAP_BINS}
    for b in rows:
        by_bin[b.gap_bin] = by_bin.get(b.gap_bin, 0) + 1
    return {
        "policy_version": CITED_REPORT_POLICY_VERSION,
        "total_gap_count": len(rows),
        "required_body_gap_count": required_body,
        "release_blocking_gap_count": release_blocking,
        "required_gap_count": required_gap_count_from_bins(rows),
        "by_bin": by_bin,
        "release_blocking_gap_ids": sorted(
            b.gap_id for b in rows if _gap_is_release_blocking(b)),
    }

# ---------------------------------------------------------------------------
# 逐句机械结论的**分族计数**（`crpp-5`，读者面读数，**不改 wire**）
# ---------------------------------------------------------------------------


def classify_check_report(check_report: SentenceCheckReport) -> dict[str, Any]:
    """把逐句机械结论按**族**分开计数（`crpp-5`）。**现算**，不写进任何 wire。

    为什么要分：`sc-7` 的硬错总数把**性质完全不同**的失败加在一个数字里——
    「数字没有资格」「历史材料当前化」和「引用未登记本栏」的处置分别是撤数/改时态/**补一次
    正确的取材**，三者的返修动作不重叠，也不该互相顶替。一个「9 句硬错」既说不出「有没有
    编数字」，也说不出「Contract 的那一栏答没答」，因此本函数给出三个**分开的**读数：

    * :data:`sections.sentence_check.CHECK_FAMILIES` 里的每一族各一条 `hard` 计数
      （记录数 **与** 句数分别给：一句可以在多轴、多族上失败，两个数不同不是错）；
    * `diagnostic`：`applicable is False` 的记录——**这一轴这次没判**。它既不是通过也不是
      失败，单列出来，两个相反方向都不许拿它当自己那一侧的证据；
    * 逐句的族归属（`sentences`），让「哪几句是栏目覆盖失败、哪几句是事实安全失败」当场
      可查，而不必让读者自己按原因码反推。

    **不落盘、不进身份体**：这个读数完全由 `sentence_checks.json` 与
    :data:`sections.sentence_check.CHECK_FAMILY_BY_KIND` 决定，因此对每一份历史产物都
    **现算现得相同**（不需要 `schema_version` 升版，也不会作废任何历史 artifact）。
    把它做成字段才是错的：那会让每一份已冻结的 `sentence_checks.json` 都解不出来。
    """
    records = tuple(getattr(check_report, "records", ()) or ())
    hard: dict[str, dict[str, Any]] = {
        family: {"records": 0, "sentences": set(), "reasons": {}}
        for family in SC.CHECK_FAMILIES}
    diagnostic_kinds: dict[str, int] = {}
    diagnostic_records = 0
    sentence_families: dict[str, dict[str, Any]] = {}
    for record in records:
        kind = str(getattr(record, "check_kind", "") or "")
        sentence_id = str(getattr(record, "sentence_id", "") or "")
        if getattr(record, "verdict", "") != "hard_error":
            # `sc-4` 起 `pass` 里混着「判过并通过」与「这一轴这次没判」两类：后者**不是结论**，
            # 单立一档，否则它与「通过」在产物上长得一模一样（同为 `pass`）。
            if not bool(getattr(record, "applicable", True)):
                diagnostic_records += 1
                diagnostic_kinds[kind] = diagnostic_kinds.get(kind, 0) + 1
            continue
        reason = str(getattr(record, "failure_reason", "") or "")
        family = SC.family_of_check_kind(kind)
        hard[family]["records"] += 1
        hard[family]["sentences"].add(sentence_id)
        hard[family]["reasons"][reason] = hard[family]["reasons"].get(reason, 0) + 1
        entry = sentence_families.setdefault(
            sentence_id, {"sentence_id": sentence_id, "families": [], "reasons": []})
        if family not in entry["families"]:
            entry["families"].append(family)
        if reason not in entry["reasons"]:
            entry["reasons"].append(reason)
    hard_sentences = set(sentence_families)
    return {
        "schema_version": SC.SENTENCE_CHECK_SCHEMA_VERSION,
        "policy_version": SC.SENTENCE_CHECK_POLICY_VERSION,
        "families": list(SC.CHECK_FAMILIES),
        "diagnostic_family": SC.CRITERIA_DIAGNOSTIC_FAMILY,
        "hard": {
            family: {
                "records": data["records"],
                "sentences": len(data["sentences"]),
                "reasons": {k: data["reasons"][k] for k in sorted(data["reasons"])},
            }
            for family, data in hard.items()},
        "hard_record_total": sum(d["records"] for d in hard.values()),
        "hard_sentence_total": len(hard_sentences),
        # 三个句子集合**分开**给（`crpp-6`）：并集是「机械层一共标了哪几句」，
        # `blocked_sentence_ids` 是「系统按哪几句阻断」（`scp-10` 起只取事实安全族），
        # `column_coverage_sentence_ids` 是「哪几句服务错了栏目」。三者**不可互推**：
        # 并集 ≠ 阻断集，阻断集为空也**不**等于栏目都答上了。
        "hard_error_sentence_ids": sorted(str(s) for s in hard_sentences),
        "blocked_sentence_ids": sorted(
            str(s) for s in (getattr(check_report, "blocked_sentence_ids", ()) or ())),
        "fact_safety_sentence_ids": sorted(
            str(s) for s in (getattr(check_report, "fact_safety_hard_error_sentence_ids", ()) or ())),
        "column_coverage_sentence_ids": sorted(
            str(s) for s in (getattr(check_report, "column_coverage_sentence_ids", ()) or ())),
        "diagnostic": {
            "records": diagnostic_records,
            "by_kind": {k: diagnostic_kinds[k] for k in sorted(diagnostic_kinds)},
        },
        "sentences": [sentence_families[k] for k in sorted(sentence_families)],
    }

#: 报告版本接口的 wire 版本。字段增删即升版；旧版走只读解码。
#:
#: `crpv-2`：身份体增加 `review_producer_kind`（这份意见是独立审阅还是离线回声）与
#: `metric_table_ids` / `metric_tables_fingerprint`（财务节确定性指标表）。前者让「系统审阅已通过、
#: 等人确认」这句话不可能由一次机械回声写出来；后者让**表格内容一变，记录身份就变** ——
#: 指标表因此不是一份旁挂产物，而是这一版预览身份的一部分。
CITED_REPORT_VERSION_SCHEMA_VERSION = "crpv-3"
#: 预览政策版本（四个状态轴的划分、逐句标注口径、"不可发布"标注、离线回声的读者面标注，
#: 任一变化即升版）。
#:
#: `crpp-3`：③ 轴不得越过 ②（有机械硬错误就不得 `passed_awaiting_human`）；预览行增加第三档
#: `not_in_review_scope`；审阅失败时正文照旧可见、另记 typed `review_failure`。
#:
#: `crpp-4`：预览的**逐句行**把失败原因码渲染成**中文人读理由**（逐码一句，见
#: :data:`_FAILURE_REASON_LABEL`），并保留原因码本身供机器对账。它**不**改任何判据、**不**改
#: 任何状态轴的取值、**不**把某一类错误写成「原文事实错误」这种统称——`scp-5` 的
#: 「引用未登记本栏」与 `srsc-1` 的「历史材料不能证明当前状态」是两个不同性质的错，
#: 处置也不同，读的人必须能当场分开。
#:
#: **本常量在 `report_version` 身份体里**（:func:`_report_version_body` 逐字带它，见那里的
#: 返回字典），所以「任一变化即升版」对**正文版本锚同样成立**：同一份 `task/draft/manifest`
#: 输入在 `crpp-3` 与 `crpp-4` 下算出的 `crpv_*` **不相等**（实测
#: `crpv_05f4f0f7…` ≠ `crpv_0fe6d114…`）。这不是副作用，正是这条锚要表达的东西——
#: 预览政策换了，同一份草稿的「是哪一版报告」就换了。
#:
#: `crpp-5`：**放行口径**。`required_gap_count` 的取值口径换成
#: :func:`required_gap_count_from_bins`（只数 Contract 必需缺口）。它**不改任何判据**、不改任何
#: 逐句行的标注、不改四个状态轴的划分：变的是「哪些缺口算作『若要发布还差这一项』」。`cp22`
#: 的 8 条缺口里，两条跨期变化（`optional_body` + `not_applicable_no_comparable_period`）、一条
#: 依法未披露标注（`not_applicable_no_plan`）不再计入 —— 这三条**仍然如实出现在**逐条分桶
#: （`cited_gap_bins.json`）与读回里，只是不再冒充「必需未覆盖」。
#:
#: `crpp-5` 同时收进**逐句机械结论的分族读数**（:func:`classify_check_report`）：把
#: `fact_safety`（来源/资格撑不住本句）与 `column_coverage`（字与来源都成立、错在服务哪一栏）
#: 分开计数，并把「这一轴这次没判」（`applicable=False`）单列成
#: `criteria_diagnostic` 一档。它是**现算**的读数、**不进**任何 wire、**不进**身份体，
#: 因此对每一份历史产物现算即得同一结果（不升 `schema_version`、不作废任何 artifact）。
#: 它与上面那条口径修正共用同一个版本号：两者都是「怎么读这份产物」，不是「判据怎么判」。
#:
#: 由此有两条必须如实说的后果，**不得**写成「本版不改变任何 `crpv_*`」：
#:
#: * 本批之后新产的每一份 `crpv_*` 都与 `crpp-3` 时代的不同；
#: * 历史上写着 `crpp-3` 的 `cited_report_version.json` 经
#:   :meth:`CitedReportVersion.from_dict` **写侧入口**不再解码（`__post_init__` 要求等于
#:   **当前**常量）。**只读**入口另有一条：:meth:`CitedReportVersion.from_legacy_dict`
#:   覆盖 :data:`CITED_REPORT_LEGACY_WIRE_VERSIONS` 里登记的历史发布期。旧记录留在原地作
#:   对照，**不**回填、**不**改写、**不**升格成当前版。
#: `crpp-6`：**两条轴分开报 + 缺口标题去混同**，不改任何判据、不改任何状态轴取值、
#: 不新增 wire 字段（`CitedReportVersion` 的字段集合一字未动，身份体因此不变公式：
#: `policy_version` 本来就在身份体里，所以同一份草稿的 `crpv_*` 照旧随它换）。
#:
#: * 缺口加两条**读侧现算**的轴（:func:`gap_axes_from_bins`）：`required_body_gap_count`
#:   （正文应写未写）与 `release_blocking_gap_count`（按冻结 Contract `blocking_policy`
#:   阻断放行）。它们与既有的 `required_gap_count` 三个数**并排给、不合并**。两条轴不重合
#:   正是 `cp22` 的真实情形：客户当前/供应商当前是 `required_body` 但 `blocking_policy:[NONE]`
#:   ——内容该补，单独不构成放行阻断。
#: * 预览的缺口标题不再写「本合同要求的，本次没有可用来源」（把「材料里有表但数字未授权」
#:   「可选未写」「合法不适用」「确无来源」四件事合成一句）；逐条按 :data:`_GAP_BIN_LABEL`
#:   给出**各自的**处置。
#: * 逐句机械结论的分族读数（:func:`classify_check_report`）增列三个**分开的**句子集合
#:   （并集 / 阻断集 / 栏目覆盖集），与 `scp-10` 的阻断集改写配套。
CITED_REPORT_POLICY_VERSION = "crpp-6"

#: **只读**解码可覆盖的历史发布期 `(schema_version, policy_version)`。**封闭表**，写侧永不用它。
#:
#: 进表条件只有一条，且**必须逐条满足**：该期的 `identity_body()` **字段集合与当前完全一致**，
#: 只有版本串不同。只有满足它，:meth:`CitedReportVersion.from_legacy_dict` 复算出的
#: `record_id` / `record_fingerprint` 才可能与文件里写的一致——否则「解码成功」只是个假象。
#:
#: `crpv-1` / `crpv-2` **不在**表里，因为它们的字段集合本身变过（`review_producer_kind` 是
#: `crpv-2` 加的，`metric_table_ids`/`metric_tables_fingerprint` 是 `crpv-2` 加的，`review_failure`
#: 是 `crpv-3` 加的）：拿今天的形状去复算那时的身份必然对不上，硬塞进来只会把「解不开」
#: 包装成「解开了」。那两类记录**按原始 JSON 读**，本类不解码。
#:
#: `crpp-5` 把 `(crpv-3, crpp-4)` 收进来：从 `crpp-5` 起 `required_gap_count` 的**取值口径**
#: 变了（只数 Contract 必需缺口，不再把可选/不适用/诊断栏一起数进去，见
#: :func:`required_gap_count_from_bins`），而**字段集合一字未动**，所以 `crpv-3`/`crpp-4` 那批
#: 记录（含全部 `cp22` 前后的历史 run）仍满足进表条件，必须继续可 typed 读回——它们记的是
#: 旧口径下的数，**不重算、不回填**。
CITED_REPORT_LEGACY_WIRE_VERSIONS = (("crpv-3", "crpp-3"), ("crpv-3", "crpp-4"),
                                     ("crpv-3", "crpp-5"))

#: 预览的发布资格——**恒定**。正式发布要另走系统放行与人工接受，不是本模块的产出。
CITED_PREVIEW_PUBLISHABILITY = "not_publishable"

#: 「人工修改」的可用档位。只有展示入口这一档。
CITED_HUMAN_EDIT_MODES = ("display_entry_only",)

#: 一份预览"若要发布，现在还差什么"的封闭原因集（供读者面逐条显示，不聚成一个布尔）。
CITED_RELEASE_BLOCKER_KINDS = (
    #: 有正文句没过机械底线（这一处不清零整节，但发布前必须处理）。
    "hard_errors_present",
    #: 独立审阅没有跑完（未跑 ≠ 通过）。
    "review_not_completed",
    #: 审阅提出了 blocking 意见且尚未解决。
    "blocking_review_issues",
    #: 写作面登记的缺口里，有 Contract 要求没有得到覆盖。
    "required_gaps_present",
    #: 根本没有可读正文。
    "preview_unavailable",
    #: 人工尚未接受（这一项**只能**由人来消掉）。
    "human_not_reviewed",
)

#: 独立审阅**没跑完**的封闭原因集（`crpv-3`）。空串表示"审阅这一步没有失败"——它**不**表示
#: "审阅通过了"：通过与否看 ③ 轴，不看这里。
#:
#: 这一档存在的理由很具体：真实 r28 里审阅回复有 22/23 行 `reason` 为空串，`ReviewIssue` 的
#: 非空约束当场抛错，于是**整轮 23 句草稿在人读出口一节不剩**——一个格式错误吃掉了正文。
#: 审阅失败是**一条读数**，它要被记下来，而不是把别的产物一起带走。
CITED_REVIEW_FAILURE_KINDS = (
    "",
    #: 审阅回复不是可解析的 JSON。
    "review_reply_not_json",
    #: 审阅回复结构/字段不合约（含逐句覆盖等式、引用挂载、`rvi-*` 构造失败）。
    "review_reply_unparsable",
    #: 审阅调用本身失败（provider 错误、截断）。
    "review_call_failed",
    #: 没有可审的句子（草稿一句带引用的正文都没有）。
    "review_no_reviewable_sentence",
)

#: 预览里**每一行**的审阅档位（封闭三档，`crpv-3`）。三档并排、不得压成两档：
#:
#: * `reviewed`：这句话在本次审阅对象内，审阅对它表过态（覆盖等式保证"一行一条"）。
#: * `not_in_review_scope`：这句话**没有引用**，给不出合法的审阅单元（`rvi-2` 的
#:   `citation_id` 必填），因此它从来不在审阅对象里。它必然同时是 `uncited_sentence` 硬错误。
#: * `review_not_completed`：本轮独立审阅没跑完（`review_failure` 非空），这一行**没有**被
#:   独立看过。它与"看了、没问题"必须分得开——那正是这条链唯一能提供的东西。
CITED_ROW_REVIEW_STATES = ("reviewed", "not_in_review_scope", "review_not_completed")


class CitedReportError(Exception):
    """报告版本 / 预览装配的 fail-closed。"""

    def __init__(self, message: str, *, reason: str = "") -> None:
        super().__init__(message)
        self.reason = reason


# ---------------------------------------------------------------------------
# 版本锚与记录身份
# ---------------------------------------------------------------------------

def _report_version_body(*, task_id: str, section_id: str, draft_id: str,
                         draft_fingerprint: str, input_manifest_id: str,
                         dependency_fingerprint: str,
                         policy_version: str | None = None) -> dict:
    """`report_version` 的身份体：**只**含写作侧输入，因此可以掉进审阅意见里而不成环。"""
    return {"schema_version": CITED_REPORT_VERSION_SCHEMA_VERSION,
            "policy_version": policy_version or CITED_REPORT_POLICY_VERSION,
            "task_id": task_id, "section_id": section_id, "draft_id": draft_id,
            "draft_fingerprint": draft_fingerprint,
            "input_manifest_id": input_manifest_id,
            "dependency_fingerprint": dependency_fingerprint}


def derive_report_version(*, draft: CW.CitedProseDraft,
                          manifest: CW.CitedWriterInputManifest,
                          policy_version: str | None = None) -> str:
    """正文版本锚 `crpv_*`：同一份输入清单 + 同一份草稿 ⇒ 同一个版本号。

    `policy_version=None`（**默认、写侧与全部新产物**）用的是当前常量。
    显式传一个**已登记**的历史发布期只在**只读评估历史产物**时用：那时要回答的是
    「这份文件里的锚，按**它自己声明的那一期政策**算得对不对」，而不是「它像不像今天的
    ——后者答的是「它是不是**陈旧**」，是另一个问题，**不能**拿同一个断言一锅端
    （一次政策升版会把盘上所有旧 run 都判成「伪造」，而它们一个字节都没被改过）。
    未登记的政策串一律拒绝：给一个凭空的政策算锚没有意义，只会把「算得出」包装成「验过了」。
    """
    if draft.input_manifest_id != manifest.manifest_id:
        raise CitedReportError(
            f"草稿绑的输入清单 {draft.input_manifest_id!r} 不是本次清单 "
            f"{manifest.manifest_id!r}", reason="draft_manifest_mismatch")
    if policy_version is not None:
        known = {CITED_REPORT_POLICY_VERSION} | {
            pv for _sv, pv in CITED_REPORT_LEGACY_WIRE_VERSIONS}
        if str(policy_version) not in known:
            raise CitedReportError(
                f"policy_version={policy_version!r} 既不是当前发布期 "
                f"{CITED_REPORT_POLICY_VERSION!r}，也不在登记过的历史发布期 "
                f"{sorted(known - {CITED_REPORT_POLICY_VERSION})} 里："
                "不替一个未登记的政策算版本锚（算得出不等于验过了）",
                reason="report_policy_version_unknown")
    body = _report_version_body(
        task_id=draft.task_id, section_id=draft.section_id, draft_id=draft.draft_id,
        draft_fingerprint=draft.fingerprint(), input_manifest_id=manifest.manifest_id,
        dependency_fingerprint=manifest.fingerprint(), policy_version=policy_version)
    return NS.content_id("crpv_", body)


# ---------------------------------------------------------------------------
# CitedReportVersion：四个状态 + 完整身份
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitedReportVersion:
    """一条新写作链的**报告版本记录**（`crpv-1`）。

    四个状态轴彼此独立（见模块 docstring 的表），`human_review_state` 由调用方**带过**。
    `publishability` 恒定、`semantic_support_claimed` 式的自我加冕在这里体现为"没有这个字段"。
    """

    schema_version: str
    record_id: str
    report_version: str
    report_id: str
    task_id: str
    section_id: str
    input_manifest_id: str
    dependency_fingerprint: str
    draft_id: str
    draft_fingerprint: str
    check_report_id: str
    check_report_fingerprint: str
    review_bundle_id: str
    review_outcome_recorded: bool
    review_producer_kind: str
    metric_table_ids: tuple[str, ...]
    metric_tables_fingerprint: str
    process_state: str
    preview_state: str
    mechanical_state: str
    system_review_state: str
    human_review_state: str
    sentence_count: int
    blocking_sentence_count: int
    blocking_issue_count: int
    required_gap_count: int
    review_failure: str = ""
    """独立审阅没跑完的 typed 原因（`:data:`CITED_REVIEW_FAILURE_KINDS``）；空串 = 没失败。

    它**不**是"审阅通过"的同义词：通过与否是 ③ 轴的事。它只回答"审阅这一步有没有被一个
    格式/调用错误挡住"，以及挡住时 `process_state` 为什么是 `flow_incomplete`。
    """
    policy_version: str = CITED_REPORT_POLICY_VERSION
    publishability: str = CITED_PREVIEW_PUBLISHABILITY
    human_edit_mode: str = CITED_HUMAN_EDIT_MODES[0]

    def __post_init__(self) -> None:
        if self.schema_version != CITED_REPORT_VERSION_SCHEMA_VERSION:
            raise CitedReportError(
                f"CitedReportVersion.schema_version 必须为 "
                f"{CITED_REPORT_VERSION_SCHEMA_VERSION!r}，得到 {self.schema_version!r}")
        if self.policy_version != CITED_REPORT_POLICY_VERSION:
            raise CitedReportError(
                f"CitedReportVersion.policy_version 必须为 {CITED_REPORT_POLICY_VERSION!r}")
        self._validate_body()

    def _validate_body(self) -> None:
        """版本常量**之外**的全部不变式；**写侧与只读历史解码共用同一份**。

        :meth:`from_legacy_dict` 跳过的是上面那两道版本闸，**不是**这里任何一条：
        形状归一、跨轴自洽、`record_id` 与 `record_fingerprint` 复算，一个都不少。
        两条路径共用这一个方法，是为了让「历史记录也被同样严格地验过」是一句可核查的话，
        而不是两处各写一遍、日后悄悄分叉。
        """
        if self.publishability != CITED_PREVIEW_PUBLISHABILITY:
            raise CitedReportError(
                "本条链的产出**只有**预览，`publishability` 不得取 "
                f"{CITED_PREVIEW_PUBLISHABILITY!r} 之外的值（得到 {self.publishability!r}）："
                "正式发布要另走系统放行与人工接受，不能由预览自行宣布")
        if self.human_edit_mode not in CITED_HUMAN_EDIT_MODES:
            raise CitedReportError(
                f"CitedReportVersion.human_edit_mode 必须属于 {CITED_HUMAN_EDIT_MODES}："
                "本批只预留展示入口，不实施编辑、回写或续跑")
        for name in ("report_version", "report_id", "task_id", "section_id",
                     "input_manifest_id", "dependency_fingerprint", "draft_id",
                     "draft_fingerprint"):
            if not str(getattr(self, name) or "").strip():
                raise CitedReportError(f"CitedReportVersion.{name} 必须非空")
        for name in ("process_state", "preview_state", "mechanical_state",
                     "system_review_state", "human_review_state"):
            value = str(getattr(self, name) or "")
            allowed = {"process_state": AS.PROCESS_STATES,
                       "preview_state": AS.PREVIEW_STATES,
                       "system_review_state": AS.SYSTEM_REVIEW_STATES,
                       "human_review_state": AS.HUMAN_REVIEW_STATES}.get(name)
            if allowed is not None and value not in allowed:
                raise CitedReportError(
                    f"CitedReportVersion.{name} 必须属于 {allowed}，得到 {value!r}")
        if self.mechanical_state not in ("no_prose", "has_hard_errors", "no_hard_errors"):
            raise CitedReportError(
                f"CitedReportVersion.mechanical_state 必须属于机械核对的三档，"
                f"得到 {self.mechanical_state!r}")
        # 两个形状字段先归一，后面的不变式才不会被「传了个 list」这类形状问题带偏。
        object.__setattr__(self, "review_producer_kind", str(self.review_producer_kind or ""))
        object.__setattr__(self, "review_failure", str(self.review_failure or ""))
        object.__setattr__(self, "metric_table_ids",
                           tuple(str(x) for x in (self.metric_table_ids or ())))
        object.__setattr__(self, "metric_tables_fingerprint",
                           str(self.metric_tables_fingerprint or ""))

        # 状态之间**必须**自洽：单独看每个字段都对、合起来矛盾的记录是伪造。
        if self.review_producer_kind not in ("",) + CR.CITED_REVIEW_PRODUCER_KINDS:
            raise CitedReportError(
                f"CitedReportVersion.review_producer_kind 必须属于 "
                f"{CR.CITED_REVIEW_PRODUCER_KINDS} 或空串（未审阅），"
                f"得到 {self.review_producer_kind!r}")
        if self.preview_state == "preview_unavailable" and self.sentence_count != 0:
            raise CitedReportError(
                "CitedReportVersion：preview_unavailable 时不得有正文句")
        if self.sentence_count == 0 and self.mechanical_state != "no_prose":
            raise CitedReportError(
                "CitedReportVersion：没有正文句时 mechanical_state 必须是 'no_prose'")
        if (self.system_review_state != "system_review_not_run"
                and not self.review_outcome_recorded):
            raise CitedReportError(
                "CitedReportVersion：审阅状态不为 not_run 时必须记下审阅产出实例"
                "（不能只写一个状态就当成审过了）")
        if self.review_failure not in CITED_REVIEW_FAILURE_KINDS:
            raise CitedReportError(
                f"CitedReportVersion.review_failure 必须属于 "
                f"{CITED_REVIEW_FAILURE_KINDS}，得到 {self.review_failure!r}")
        if self.review_failure and self.review_outcome_recorded:
            raise CitedReportError(
                "CitedReportVersion：审阅失败与审阅产出实例不能并存——"
                f"记下了 {self.review_failure!r} 却又有 outcome，两者必有一处是伪造")
        if self.review_failure and self.system_review_state != "system_review_not_run":
            raise CitedReportError(
                "CitedReportVersion：审阅失败时 ③ 轴只能是 system_review_not_run，"
                f"得到 {self.system_review_state!r}")
        if self.system_review_state == "system_review_passed_awaiting_human":
            if self.blocking_issue_count != 0:
                raise CitedReportError(
                    "CitedReportVersion：审阅状态为 passed 时不得存在 blocking 意见")
            if self.review_bundle_id == "":
                raise CitedReportError(
                    "CitedReportVersion：审阅状态为 passed 时必须绑定审阅输入 bundle")
            if self.review_producer_kind != "independent_llm_review":
                raise CitedReportError(
                    "CitedReportVersion：只有**独立**审阅才有资格把状态写成 passed；"
                    f"本记录的生产者是 {self.review_producer_kind!r}，"
                    "离线替身意见不得进入读者预览的「已通过」档")
            # ③ 不得越过 ②：机械层已证实的底线失败，不需要审阅同意就成立。审阅一条 blocking
            # 都没提、而正文里明明有硬错误时把它记成「系统审阅通过」，正是 §0.20 要求分开记录
            # 的那件事被偷偷合并——读者会拿一个跨轴结论当成两轴皆净。
            if self.blocking_sentence_count > 0:
                raise CitedReportError(
                    "CitedReportVersion：本节有 "
                    f"{self.blocking_sentence_count} 句机械硬错误，③ 轴不得写 "
                    "system_review_passed_awaiting_human（审阅没提 blocking ≠ 系统通过："
                    "硬错误是可机械证明的底线失败，它优先）")
        # 表格身份：有表就有指纹，没表就两样都空。半有半无意味着「表进来了但没进身份」，
        # 那样表格一变预览版本不变，逐格回查就无从对版本。
        has_tables = bool(self.metric_table_ids)
        if has_tables != bool(self.metric_tables_fingerprint):
            raise CitedReportError(
                "CitedReportVersion：metric_table_ids 与 metric_tables_fingerprint "
                "必须同时存在或同时为空（表内容一变，记录身份就得变）")
        if len(set(self.metric_table_ids)) != len(self.metric_table_ids):
            raise CitedReportError("CitedReportVersion.metric_table_ids 不得有重复")
        if self.blocking_sentence_count > self.sentence_count:
            raise CitedReportError(
                "CitedReportVersion：blocking_sentence_count 不得超过 sentence_count")

        expected = derive_report_record_id(self)
        if self.record_id != expected:
            raise CitedReportError(
                f"CitedReportVersion.record_id 与内容不符：声明 {self.record_id!r}，"
                f"应为 {expected!r}")

    # -- 派生视图（**不**产生放行结论） -----------------------------------

    def release_blockers(self) -> tuple[str, ...]:
        """"若要发布，现在还差什么"——逐条列出，不聚成一个布尔。顺序固定，便于对账。"""
        out: list[str] = []
        if self.preview_state == "preview_unavailable":
            out.append("preview_unavailable")
        if self.blocking_sentence_count > 0:
            out.append("hard_errors_present")
        if self.system_review_state == "system_review_not_run":
            out.append("review_not_completed")
        if self.blocking_issue_count > 0:
            out.append("blocking_review_issues")
        if self.required_gap_count > 0:
            out.append("required_gaps_present")
        if self.human_review_state == "human_not_reviewed":
            out.append("human_not_reviewed")
        return tuple(sorted(set(out), key=CITED_RELEASE_BLOCKER_KINDS.index))

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version, "policy_version": self.policy_version,
            "report_version": self.report_version, "report_id": self.report_id,
            "task_id": self.task_id, "section_id": self.section_id,
            "input_manifest_id": self.input_manifest_id,
            "dependency_fingerprint": self.dependency_fingerprint,
            "draft_id": self.draft_id, "draft_fingerprint": self.draft_fingerprint,
            "check_report_id": self.check_report_id,
            "check_report_fingerprint": self.check_report_fingerprint,
            "review_bundle_id": self.review_bundle_id,
            "review_outcome_recorded": self.review_outcome_recorded,
            "review_producer_kind": self.review_producer_kind,
            "review_failure": self.review_failure,
            "metric_table_ids": list(self.metric_table_ids),
            "metric_tables_fingerprint": self.metric_tables_fingerprint,
            "process_state": self.process_state, "preview_state": self.preview_state,
            "mechanical_state": self.mechanical_state,
            "system_review_state": self.system_review_state,
            "human_review_state": self.human_review_state,
            "sentence_count": self.sentence_count,
            "blocking_sentence_count": self.blocking_sentence_count,
            "blocking_issue_count": self.blocking_issue_count,
            "required_gap_count": self.required_gap_count,
            "publishability": self.publishability,
            "human_edit_mode": self.human_edit_mode,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(
            NS.canonical_json(self.identity_body()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {"record_id": self.record_id, "record_fingerprint": self.fingerprint(),
                **self.identity_body()}

    @classmethod
    def from_dict(cls, d: Any) -> "CitedReportVersion":
        """只读解码（`crpv-1`）。`record_id` 与 `record_fingerprint` 都当场复算，改一个字节即拒。"""
        fields = set(cls.__dataclass_fields__)
        d = NS._reject_unknown(d, fields | {"record_fingerprint"}, "CitedReportVersion")
        record = cls(**{name: d.get(name) for name in fields if name != "record_id"},
                     record_id=str(d.get("record_id") or ""))
        declared = d.get("record_fingerprint")
        if declared is not None and str(declared) != record.fingerprint():
            raise CitedReportError(
                f"CitedReportVersion.record_fingerprint 与内容不符：声明 {declared!r}，"
                f"实际 {record.fingerprint()!r}", reason="record_fingerprint_mismatch")
        return record

    @classmethod
    def from_persisted_dict(cls, d: Any) -> "CitedReportVersion":
        """按记录**自己声明的**发布期分派解码（一次口径升版不得让旧 run 变得读不出来）。

        三个方向，逐条写明：

        * 两个版本串**都是当前常量** ⇒ :meth:`from_dict`（写侧同一道闸）；
        * 落在 :data:`CITED_REPORT_LEGACY_WIRE_VERSIONS` ⇒ :meth:`from_legacy_dict`
          （同一份 :meth:`_validate_body`，只免掉「版本串等于当前常量」这一条）；
        * 其余 ⇒ 仍走 :meth:`from_dict`，让它抛**带 typed 原因**的错（不静默降级、不猜一期）。

        它**不放宽**任何跨轴不变式，也**不改历史身份**：解出来的对象其 `policy_version` 仍是
        文件里写的那个，`fingerprint()` 因而在**旧政策下**复算。凡是从盘上读
        `cited_report_version.json` 的地方都应当走这个入口——直接 `from_dict` 只适用于
        「刚刚由 :func:`build_cited_report_version` 造出来、必然是当前版」的对象。
        """
        pair = (str(d.get("schema_version") or ""), str(d.get("policy_version") or ""))
        if pair == (CITED_REPORT_VERSION_SCHEMA_VERSION, CITED_REPORT_POLICY_VERSION):
            return cls.from_dict(d)
        if pair in CITED_REPORT_LEGACY_WIRE_VERSIONS:
            return cls.from_legacy_dict(d)
        return cls.from_dict(d)

    @classmethod
    def from_legacy_dict(cls, d: Any) -> "CitedReportVersion":
        """**只读**解码历史发布期记录（见 :data:`CITED_REPORT_LEGACY_WIRE_VERSIONS`）。

        与 :meth:`from_dict` 的唯一差别是**不**再要求两个版本串等于当前常量，而要求那一对
        落在封闭表里。**其余一律照旧**：未知键拒收、字段形状归一、跨轴不变式、
        `record_id` 与 `record_fingerprint` 当场复算——全都走同一个
        :meth:`_validate_body`，不是删掉校验的捷径。

        **不变更任何历史身份**：解出来的对象其 `schema_version` / `policy_version` 仍是文件里
        写的那个，`fingerprint()` 因而在**旧政策下**复算。这正是不重算旧身份要的效果；
        `to_dict()` 原样吐回旧版本串，可直接覆盖回原文件而不改一个字节。

        表外的记录（`crpv-1`/`crpv-2`，或一对当前常量）**拒绝并说明原因**，不静默降级：
        返回一个身份复算必然对不上的对象，比直接说不支持更糟。
        """
        fields = set(cls.__dataclass_fields__)
        d = NS._reject_unknown(d, fields | {"record_fingerprint"}, "CitedReportVersion")
        pair = (str(d.get("schema_version") or ""), str(d.get("policy_version") or ""))
        if pair not in CITED_REPORT_LEGACY_WIRE_VERSIONS:
            raise CitedReportError(
                f"CitedReportVersion.from_legacy_dict 只解码登记过的历史发布期 "
                f"{list(CITED_REPORT_LEGACY_WIRE_VERSIONS)}，得到 {pair!r}："
                "不在表里说明那一期的身份体字段集合与当前不同，复算 identity 必然对不上。"
                "这类记录按原始 JSON 读，不要解码成对象",
                reason="legacy_wire_version_unknown")
        # 绕开的两道闸**只是**版本常量相等；`_validate_body` 一条不少地照跑。
        record = object.__new__(cls)
        for name in fields:
            object.__setattr__(record, name, d.get(name))
        record._validate_body()
        declared = d.get("record_fingerprint")
        if declared is not None and str(declared) != record.fingerprint():
            raise CitedReportError(
                f"CitedReportVersion.record_fingerprint 与内容不符：声明 {declared!r}，"
                f"实际 {record.fingerprint()!r}", reason="record_fingerprint_mismatch")
        return record


def derive_report_record_id(record: CitedReportVersion) -> str:
    """完整记录身份 `cpr_*`：核对与审阅**都**收进来（`record_id` 自身不进身份体，无环）。"""
    return NS.content_id("cpr_", record.identity_body())


def _metric_table_identity(metric_tables: Sequence[CFT.CitedMetricTable], *,
                           manifest: CW.CitedWriterInputManifest,
                           section_id: str) -> tuple[tuple[str, ...], str]:
    """指标表 → （表 id 集合，聚合指纹）。**空表返回两个空值**，不返回空指纹。

    两处**当场**核对，缺一不可：
    - 表必须属于本节（跨节把别的节的表挂进来，预览就会展示一份本节清单解释不了的格子）；
    - 每一格的引用键必须落在**本节**输入清单里（与 `build_cited_metric_tables` 用
      `manifest.facts` 过滤同一条边界；这里再核一次，是为了挡住调用方绕过该函数自己拼表）。
    """
    tables = tuple(metric_tables or ())
    if not tables:
        return (), ""
    allowed_keys = set(manifest.all_keys())
    for table in tables:
        if str(getattr(table, "section_id", "") or "") != section_id:
            raise CitedReportError(
                f"指标表 {getattr(table, 'table_id', '')!r} 属于节 "
                f"{getattr(table, 'section_id', '')!r}，不是本节 {section_id!r}",
                reason="metric_table_wrong_section")
        for row in table.rows:
            for key in row.citation_keys:
                if key not in allowed_keys:
                    raise CitedReportError(
                        f"指标表 {table.table_id!r} 的格引用键 {key!r} 不在本节输入清单 "
                        f"{manifest.manifest_id!r} 内",
                        reason="metric_table_citation_outside_manifest")
    # 排序而非按传入顺序：身份只认「展示了哪几张表」，不认调用方拼表的先后。
    ids = tuple(sorted(table.table_id for table in tables))
    fingerprint = NS.content_id(
        "cmts_", {"tables": sorted(table.fingerprint() for table in tables)})
    return ids, fingerprint


def build_cited_report_version(*, draft: CW.CitedProseDraft,
                               manifest: CW.CitedWriterInputManifest,
                               check_report: SentenceCheckReport,
                               review_outcome: CR.CitedReviewOutcome | None,
                               report_id: str,
                               required_gap_count: int = 0,
                               human_review_state: str = "human_not_reviewed",
                               metric_tables: Sequence[CFT.CitedMetricTable] = (),
                               review_failure: str = ""
                               ) -> CitedReportVersion:
    """四个状态轴的**唯一**装配处：每一项都从真实产物读出来，不靠调用方报数。

    `metric_tables` 是**确定性**指标表的成品（`sections.cited_financial_table` 造）。它不参与
    任何状态判定——表格有没有，与正文有没有、审阅跑没跑是不同的事——只进身份体：表一变，
    `record_id` / `record_fingerprint` 就变，预览版本因此与它展示的格子**同版**。

    `review_failure` 是**审阅没跑完**的 typed 原因（`:data:`CITED_REVIEW_FAILURE_KINDS``）：
    它让"审阅失败"这条读数进得来，而**不**要求调用方拿一个假的 outcome 来占位——伪造审阅
    产出实例正是本模块要挡住的事。它非空时 `process_state` 记 `flow_incomplete`。
    """
    report_version = derive_report_version(draft=draft, manifest=manifest)
    if review_outcome is not None and review_outcome.report_version != report_version:
        raise CitedReportError(
            f"审阅绑的 report_version={review_outcome.report_version!r} 不是本次 "
            f"{report_version!r}", reason="review_report_version_mismatch")
    if check_report.draft_id != draft.draft_id:
        raise CitedReportError(
            f"机械核对报告绑的草稿 {check_report.draft_id!r} 不是本次 {draft.draft_id!r}",
            reason="check_report_draft_mismatch")
    failure = str(review_failure or "")
    if failure and review_outcome is not None:
        raise CitedReportError(
            f"review_failure={failure!r} 与已记下的审阅产出实例不能并存："
            "要么审阅跑完了（有 outcome），要么它失败了（有原因码）",
            reason="review_failure_with_outcome")

    sentence_count = len(draft.sentence_ids())
    blocking_sentences = len(check_report.blocked_sentence_ids)
    if review_outcome is None:
        producer_kind = ""
        system_review_state = "system_review_not_run"
        blocking_issues = 0
        review_bundle_id = ""
    else:
        producer_kind = str(getattr(review_outcome, "review_producer_kind", "") or "")
        blocking_issues = len(review_outcome.blocking_issue_ids)
        review_bundle_id = review_outcome.bundle_id
        if producer_kind == "independent_llm_review":
            # ③ 不得越过 ②：审阅一条 blocking 都没提，而正文里还有机械硬错误时，状态停在
            # `system_review_not_passed`。审阅在这几句上的独立意见不被抹掉——它逐句显示，
            # 分歧另有 `hard_error_override_sentence_ids` 留档——但"系统放行"这个**跨轴**结论
            # 不给出去：硬错误是可机械证明的底线失败，不需要审阅同意就成立。
            clean = (blocking_issues == 0 and blocking_sentences == 0)
            system_review_state = ("system_review_passed_awaiting_human" if clean
                                   else "system_review_not_passed")
        else:
            # 离线回声**不是**独立审阅：它的意见照记（读者要看到回声说了什么），但状态必须
            # 老老实实停在「没跑过」——否则读者预览会把一段替身回声读成「系统审阅已通过」。
            system_review_state = "system_review_not_run"

    metric_table_ids, metric_fingerprint = _metric_table_identity(metric_tables,
                                                                  manifest=manifest,
                                                                  section_id=draft.section_id)

    parts: dict[str, Any] = {
        "schema_version": CITED_REPORT_VERSION_SCHEMA_VERSION,
        "policy_version": CITED_REPORT_POLICY_VERSION,
        "report_version": report_version, "report_id": report_id,
        "task_id": draft.task_id, "section_id": draft.section_id,
        "input_manifest_id": manifest.manifest_id,
        "dependency_fingerprint": manifest.fingerprint(), "draft_id": draft.draft_id,
        "draft_fingerprint": draft.fingerprint(), "check_report_id": check_report.report_id,
        "check_report_fingerprint": check_report.fingerprint(),
        "review_bundle_id": review_bundle_id,
        "review_outcome_recorded": review_outcome is not None,
        "review_producer_kind": producer_kind,
        "review_failure": failure,
        "metric_table_ids": metric_table_ids,
        "metric_tables_fingerprint": metric_fingerprint,
        # 审阅被一个格式/调用错误挡住时，这一轮的流程**没有**走完：正文写出来了、硬核对跑了、
        # 缺口登记了，但独立审阅这一环停在半路。记 `flow_incomplete` 是让读者看得见这件事，
        # 而**不是**把已写出的正文藏起来——"流程跑完"与"草稿可读"是两件事。
        "process_state": ("flow_incomplete" if failure else "flow_complete"),
        "preview_state": "draft_previewable" if sentence_count else "preview_unavailable",
        "mechanical_state": check_report.mechanical_verdict,
        "system_review_state": system_review_state,
        "human_review_state": human_review_state, "sentence_count": sentence_count,
        "blocking_sentence_count": blocking_sentences, "blocking_issue_count": blocking_issues,
        "required_gap_count": int(required_gap_count),
        "publishability": CITED_PREVIEW_PUBLISHABILITY,
        "human_edit_mode": CITED_HUMAN_EDIT_MODES[0],
    }
    # 身份与 `identity_body()` 必须由**同一**份字段算：两处各写一遍，字段增删后就会分叉成
    # 「有时可构造、有时不可」。
    record_id = NS.content_id("cpr_", {k: v for k, v in parts.items() if k != "record_id"})
    return CitedReportVersion(record_id=record_id, **parts)


# ---------------------------------------------------------------------------
# 逐句预览
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CitedSentencePreviewRow:
    """一句正文在**读者面**上的完整一行：文字、引用、机械结论、审阅意见。

    四个轴**并列显示**，不合并：读者要能同时看到"这句话引了什么""机械层判它什么""审阅说它
    什么"。把它们压成一个"好/坏"，一处硬错误就会连带遮掉审阅对它说了什么。
    """

    sentence_id: str
    subsection_id: str
    paragraph_id: str
    text: str
    citations: tuple[str, ...]
    mechanical_verdict: str
    failure_reasons: tuple[str, ...]
    review_semantic_category: str
    review_severity: str
    review_blocking: bool
    review_reason: str
    review_state: str = "review_not_completed"
    """这一行的审阅档位，取值属于 :data:`CITED_ROW_REVIEW_STATES`。

    默认值取得最保守的那一档（"没审阅完"）：构造一行时**忘了**传它，读者看到的是"没被独立
    看过"，而不是一个凭空的"已审阅"。反过来默认成 `reviewed` 会让一次漏传伪装成一次通过。
    """

    def __post_init__(self) -> None:
        if self.review_state not in CITED_ROW_REVIEW_STATES:
            raise CitedReportError(
                f"CitedSentencePreviewRow.review_state 必须属于 "
                f"{CITED_ROW_REVIEW_STATES}，得到 {self.review_state!r}："
                "「审阅看了、没问题」与「这一句没被独立看过」不得共用一个取值")
        if self.review_state != "reviewed" and (self.review_blocking
                                               or self.review_semantic_category):
            raise CitedReportError(
                f"句子 {self.sentence_id!r} 的审阅档位是 {self.review_state!r}，"
                "却带着审阅意见：没有意见对象就写不出意见，这两件事不能同时为真")

    @property
    def mechanical_families(self) -> tuple[str, ...]:
        """本句的硬错落在哪几族（`scp-10`；现算，**不落盘、不进 wire**）。

        空元组 = 没有硬错。一句可以同时落在两族（既引错栏、又用了无资格数字），此时两族
        都列出来——读者据此决定下一步动作：`fact_safety` 要撤数或补资格（这一句的**事实**
        撑不住），`column_coverage` 要补一次正确的取材/改栏（字与来源都成立，只是服务错了
        栏目）。把这两件事印成同一个「硬错误」，读者就分不出「原文是假的」和「挂错地方了」。
        """
        if not self.failure_reasons:
            return ()
        seen = {SC.family_of_failure_reason(r) for r in self.failure_reasons}
        return tuple(f for f in SC.CHECK_FAMILIES if f in seen)

    @property
    def ok(self) -> bool:
        """两轴都干净才为真。任一处有问题，这一句就带上标记——但**其余句子照旧可读**。

        `review_state != "reviewed"` 也算"不干净"：没被独立看过的句子，与"看了、没问题"
        不是同一件事，不能因为它恰好没有意见就当它干净了。
        """
        return (self.mechanical_verdict == "pass" and not self.review_blocking
                and self.review_semantic_category in ("", "supported")
                and self.review_state == "reviewed")

    def to_dict(self) -> dict:
        return {"sentence_id": self.sentence_id, "subsection_id": self.subsection_id,
                "paragraph_id": self.paragraph_id, "text": self.text,
                "citations": list(self.citations),
                "mechanical_verdict": self.mechanical_verdict,
                "failure_reasons": list(self.failure_reasons),
                "review_state": self.review_state,
                "review_semantic_category": self.review_semantic_category,
                "review_severity": self.review_severity,
                "review_blocking": self.review_blocking, "review_reason": self.review_reason,
                "ok": self.ok}

    @classmethod
    def from_dict(cls, d: Any) -> "CitedSentencePreviewRow":
        """只读解码。`ok` 是**派生**值，回读时一律重算，不接受落盘的那一个。"""
        d = NS._reject_unknown(
            d, {"sentence_id", "subsection_id", "paragraph_id", "text", "citations",
                "mechanical_verdict", "failure_reasons", "review_state",
                "review_semantic_category",
                "review_severity", "review_blocking", "review_reason", "ok"},
            "CitedSentencePreviewRow")
        return cls(
            sentence_id=str(d.get("sentence_id") or ""),
            subsection_id=str(d.get("subsection_id") or ""),
            paragraph_id=str(d.get("paragraph_id") or ""), text=str(d.get("text") or ""),
            citations=tuple(str(x) for x in (d.get("citations") or ())),
            mechanical_verdict=str(d.get("mechanical_verdict") or ""),
            failure_reasons=tuple(str(x) for x in (d.get("failure_reasons") or ())),
            review_semantic_category=str(d.get("review_semantic_category") or ""),
            review_severity=str(d.get("review_severity") or ""),
            review_blocking=bool(d.get("review_blocking")),
            review_reason=str(d.get("review_reason") or ""),
            review_state=str(d.get("review_state") or ""))


@dataclass(frozen=True)
class CitedSectionPreview:
    """一节的**只读**预览：版本记录 + 逐句行 + 逐句中文缺口 + 补件需求 + 指标表产出。

    `metric_table_outcome` 与其表 id **当场**对齐版本记录里的 `metric_table_ids`：预览页上
    的表格格子与它声称的那一版正文因此是同一条记录，不是两份各说各话的产物。
    """

    section_title: str
    version: CitedReportVersion
    rows: tuple[CitedSentencePreviewRow, ...]
    gaps: tuple[CW.CitedProseGap, ...]
    follow_up_needs: tuple[CW.CitedFollowUpNeed, ...]
    subsections: tuple[tuple[str, str], ...] = ()
    """`(subsection_id, title)`，按请求面顺序；用于把逐句行分节呈现。"""
    metric_table_outcome: CFT.CitedMetricTableOutcome | None = None
    """`None` = 本节不是财务节（没有指标表这回事），与「财务节但一张表都没成」不同：后者是
    一个带 typed 拒绝／覆盖读数的空产出。"""

    def __post_init__(self) -> None:
        outcome = self.metric_table_outcome
        declared = tuple(sorted(t.table_id for t in (outcome.tables if outcome else ())))
        if declared != tuple(self.version.metric_table_ids):
            raise CitedReportError(
                f"预览里的指标表 {declared!r} 与版本记录声明的 "
                f"{self.version.metric_table_ids!r} 不一致：表格与正文必须同版",
                reason="preview_metric_table_version_mismatch")

    def to_dict(self) -> dict:
        return {"section_title": self.section_title, "version": self.version.to_dict(),
                "rows": [r.to_dict() for r in self.rows],
                "gaps": [g.to_dict() for g in self.gaps],
                "follow_up_needs": [n.to_dict() for n in self.follow_up_needs],
                "subsections": [{"subsection_id": s, "title": t}
                                for s, t in self.subsections],
                "metric_table_outcome": (self.metric_table_outcome.to_dict()
                                         if self.metric_table_outcome else None)}

    @classmethod
    def from_dict(cls, d: Any) -> "CitedSectionPreview":
        d = NS._reject_unknown(
            d, {"section_title", "version", "rows", "gaps", "follow_up_needs",
                "subsections", "metric_table_outcome"}, "CitedSectionPreview")
        subs = d.get("subsections") or ()
        raw_outcome = d.get("metric_table_outcome")
        return cls(
            section_title=str(d.get("section_title") or ""),
            version=CitedReportVersion.from_dict(d.get("version")),
            rows=tuple(CitedSentencePreviewRow.from_dict(r) for r in (d.get("rows") or ())),
            gaps=tuple(CW.CitedProseGap.from_dict(g) for g in (d.get("gaps") or ())),
            follow_up_needs=tuple(CW.CitedFollowUpNeed.from_dict(n)
                                  for n in (d.get("follow_up_needs") or ())),
            subsections=tuple((str(s.get("subsection_id") or ""), str(s.get("title") or ""))
                              for s in subs),
            metric_table_outcome=(CFT.CitedMetricTableOutcome.from_dict(raw_outcome)
                                  if raw_outcome else None))


def build_cited_section_preview(*, draft: CW.CitedProseDraft,
                                manifest: CW.CitedWriterInputManifest,
                                check_report: SentenceCheckReport,
                                review_outcome: CR.CitedReviewOutcome | None,
                                version: CitedReportVersion,
                                metric_table_outcome: CFT.CitedMetricTableOutcome | None = None
                                ) -> CitedSectionPreview:
    """草稿 + 机械核对 + 审阅 → 逐句预览。

    一句话都没有时照样产出一份**空**预览（`preview_state=preview_unavailable`），而不是
    抛错：读者要知道的是"这一节没有可读正文"，不是"程序崩了"。
    """
    if version.draft_id != draft.draft_id:
        raise CitedReportError(
            f"版本记录绑的草稿 {version.draft_id!r} 不是本次 {draft.draft_id!r}",
            reason="version_draft_mismatch")

    review_by_sentence: dict[str, list[AS.ReviewIssue]] = {}
    for issue in (review_outcome.issues if review_outcome else ()):
        review_by_sentence.setdefault(issue.sentence_id, []).append(issue)
    excluded = set(getattr(review_outcome, "excluded_uncited_sentence_ids", ()) or ())
    rank = {name: index for index, name in enumerate(AS.REVIEW_SEVERITIES)}

    def review_state_of(sentence_id: str, citations: Sequence[str]) -> str:
        """一行的审阅档位。三档互斥且穷尽，**不**允许回落到一个默认的"没问题"。

        圈外句（没引用）不因"审阅没意见"而变成"审阅看了"；审阅整轮没跑完时，全部有引用的
        句子记 `review_not_completed`——那是一条**读数**，不是"审阅漏看了几句"。
        """
        if not citations:
            return "not_in_review_scope"
        if review_outcome is None:
            return "review_not_completed"
        if sentence_id in excluded:  # pragma: no cover - 与上面那条 `not citations` 同因
            return "not_in_review_scope"
        return "reviewed"

    rows: list[CitedSentencePreviewRow] = []
    for subsection in draft.subsections:
        for paragraph in subsection.paragraphs:
            for sentence in paragraph.sentences:
                records = check_report.records_for(sentence.sentence_id)
                failed = [r for r in records if r.verdict == "hard_error"]
                state = review_state_of(sentence.sentence_id, sentence.citations)
                issues = (sorted(review_by_sentence.get(sentence.sentence_id, ()),
                                 key=lambda i: (-rank.get(i.severity, 0), i.semantic_category))
                          if state == "reviewed" else [])
                head = issues[0] if issues else None
                rows.append(CitedSentencePreviewRow(
                    sentence_id=sentence.sentence_id,
                    subsection_id=subsection.subsection_id,
                    paragraph_id=paragraph.paragraph_id, text=sentence.text,
                    citations=tuple(sentence.citations),
                    mechanical_verdict=("hard_error" if failed else "pass"),
                    failure_reasons=tuple(dict.fromkeys(r.failure_reason for r in failed)),
                    review_semantic_category=(head.semantic_category if head else ""),
                    review_severity=(head.severity if head else ""),
                    review_blocking=any(i.blocking for i in issues),
                    review_reason=(head.reason if head else ""),
                    review_state=state))

    return CitedSectionPreview(
        section_title=manifest.section_title, version=version, rows=tuple(rows),
        gaps=draft.gaps, follow_up_needs=draft.follow_up_needs,
        subsections=tuple((s.subsection_id, s.title) for s in manifest.subsections),
        metric_table_outcome=metric_table_outcome)


# ---------------------------------------------------------------------------
# 渲染：明确标注「不可发布、未经人工接受」
# ---------------------------------------------------------------------------

_NOT_PUBLISHABLE_BANNER = (
    "> **不可发布、未经人工接受。** 本文本是一条离线/演示链的**预览**："
    "它只用于检查写作者是否按 Pack 原文写作、以及每句话各自引了什么。\n"
    "> 机械核对只证明**可机械证明的底线**，它**没有**资格宣称「语义已被支持」；"
    "审阅给出的是**意见**，不是放行。\n"
    "> 人工修改：**仅预留展示入口**（本版不提供编辑、回写或续跑）。"
)

_MECHANICAL_LABEL = {
    "pass": "机械底线：通过",
    "hard_error": "机械底线：**硬错误**",
}

#: 只落 `column_coverage` 族的那一句在读者面上的说法（`scp-10`）。它**不是**「原文是假的」，
#: 因此不能只说「硬错误」；但它也不是「没问题」——这一句服务错了栏目，仍不计入所声明栏目的
#: 覆盖，仍要返修。两个字都要说到，读者才不会往任一极端读。
_MECHANICAL_LABEL_COLUMN_ONLY = (
    "机械底线：**栏目覆盖待修**（字与来源都成立，错在它服务的是**别的一栏**；"
    "这一句**不**构成事实安全阻断，但它**不**计入所声明栏目的覆盖）"
)


def _mechanical_label(row: CitedSentencePreviewRow) -> str:
    """逐句机械结论的措辞。分族是**现算**的（:attr:`CitedSentencePreviewRow.mechanical_families`）。"""
    families = row.mechanical_families
    if row.mechanical_verdict != "pass" and families == ("column_coverage",):
        return _MECHANICAL_LABEL_COLUMN_ONLY
    return _MECHANICAL_LABEL.get(row.mechanical_verdict, row.mechanical_verdict)


def _mechanical_mark(row: CitedSentencePreviewRow) -> str:
    """逐句标记（正文行末尾那对方括号里的那一件）。"""
    families = row.mechanical_families
    if "fact_safety" in families:
        return "**机械硬错误（事实安全）**"
    if families == ("column_coverage",):
        return "**栏目覆盖待修**"
    return "**机械硬错误**"

#: 失败原因码 → **人读中文理由**（`crpp-4`）。逐句行此前只印原因码本身
#: （`sentence_aspect_not_registered` 这样的机器串），读者要判「这一句到底哪里不对」只能去
#: 翻判据表；而把这些码压成一句「原文事实错误」更糟——它们**不是**一件事：
#:
#:   * `sentence_aspect_not_registered` 说的是**引用未登记本栏**：这句话的**字**可能是对的，
#:     错在它引的来源在 Pack 里只登记到别的栏目（`scp-3`／`scp-5` 的归属轴）；
#:   * `history_material_as_current_state` 说的是**历史材料不能证明当前状态**：来源本身合格、
#:     登记也没问题，问题是它是**上年／历史来源**，句子却按持续至今的当前状态写（`srsc-1`）。
#:
#: 这两句各自的处置完全不同（前者补一次正确的取材，后者要么改写成明确「上年」、要么撤句留
#: 缺口），压成一句会让人去做错的返修。因此本表**逐码给一句中文**，并刻意让每一句都点明
#: 「错在哪一条轴上」，而不是给一个「有问题」的统称。
#:
#: 取值域**必须**覆盖 `sentence_check.FAILURE_REASONS`（唯一的失败原因词表）；本表不新造码，
#: 只给出中文。词表增删时本表由 `evals/test_m930_3_cited_review.py` §15 的穷举断言一起核对
#: ——缺一条就在那里红，不在这里静默退化成原因码原文。
_FAILURE_REASON_LABEL = {
    "uncited_sentence": "这一句没有任何引用（正文句必须逐句引用本次输入面里的键）",
    "citation_not_in_input": "这一句引的键不在本次输入面里（引用真实性）",
    "citation_locator_unretrievable": "所引来源的定位取不到（引不出可回查的位置）",
    "unsourced_number_surface": "句中的数字/比率没有对应的合格来源表面",
    "numeric_basis_not_qualified": "句中的金额/比率没有格级或事实级资格（普通材料引用不授权）",
    "unsourced_negation_surface": "句中出现了未获支持的高风险表述（如否定、趋势、并列判断）",
    "unsourced_period_surface": "句中的期间表述没有来源支持（含把多个期间罩在「报告期内」一句里）",
    "unsourced_subject_surface": "句中的主体表述没有来源支持",
    "history_material_as_current_state": (
        "**历史材料不能证明当前状态**：所引来源是上年/历史来源，本句却按持续至今的当前状态写"),
    "source_role_unreadable": "所引来源的来源角色读不出来（角色轴无从核对）",
    "template_text_as_company_fact": "把勾选/模板/版式碎片当成了公司事实来写",
    "table_cell_provenance_unavailable": "句中引用的表数字没有格级来源（该格必须带行列定位）",
    "table_row_label_missing": "所引单元格的业务行标签没有落在句子里",
    "table_column_header_missing": "所引单元格的指标列标签没有落在句子里",
    "table_declaration_unavailable": "表的单位/期间/口径声明取不到（口径无从核对）",
    "sentence_aspect_not_registered": (
        "**引用未登记本栏**：所引来源在本节 Pack 里只登记到其它栏目，未登记本句声明的这一栏"),
    "sentence_presentation_column_mismatch": "所引事实经呈现层路由落到的栏目与本句声明的栏目不符",
    "diagnostic_fact_in_body": "把只进诊断槽位的事实写成了普通正文结论",
}


#: 族 → 人读中文（逐句行前缀，`crpp-5`）。**不合并成一档**：`fact_safety` 是「来源撑不住这
#: 句话」，`column_coverage` 是「来源撑得住，但这句话服务的是别的一栏」，两者返修动作不同。
_FAMILY_LABEL = {
    "fact_safety": "事实安全",
    "column_coverage": "栏目覆盖",
}


def failure_reason_label(reason: str) -> str:
    """一个失败原因码的中文人读理由；不认识的原因码**原样返回**，不编一个说法。

    不认识时原样返回而不是填一句「未知问题」：原因码来自 `sentence_check` 的封闭词表，
    出现表外的码说明是**词表与渲染面分叉了**，那时读者最需要看到的恰恰是那个码本身
    ——把它藏进「未知问题」会让分叉变成看不见的事。
    """
    return _FAILURE_REASON_LABEL.get(str(reason or ""), str(reason or ""))

#: ③ 轴按**意见的生产者**分行，不按状态码分行。状态码只有「跑没跑完、有没有 blocking」，
#: 它答不出「这条意见是谁给的」——而离线替身的回声与真实独立审阅在同一个状态码下毫无区别。
_REVIEW_AXIS_LABEL = {
    "independent_llm_review": "",
    "offline_diagnostic_echo": (
        "③ 独立审阅：**未进行真实独立语义审阅**（本页文字与意见来自**离线诊断替身**"
        "`offline_diagnostic_echo`）"
    ),
}


def _review_axis_text(version: CitedReportVersion) -> str:
    """③ 轴的那一段：**先认失败，再认生产者，最后报状态**。

    三个顺序都不能换：

    * 审阅没跑完时（`review_failure` 非空）先说这件事——否则读者会把"这一节没有审阅意见"
      读成"审阅提不出意见"。
    * 离线回声一律走 `_REVIEW_AXIS_LABEL`，因此预览里**永不**出现
      `system_review_passed_awaiting_human` —— 那个字符串是留给真实独立审阅的。
    * 真实独立审阅 `not_passed` 而它一条 blocking 都没提时，把"卡在哪"写出来：那是机械层的
      硬错误，不是审阅的意见。不写这一句，"not_passed" 会被读成"审阅发现了问题"。
    """
    human = f"④ 人工接受：`{version.human_review_state}`"
    if version.review_failure:
        return (f"③ 独立审阅：**未完成**（`{version.review_failure}`）"
                f"——正文与逐句机械核对照旧可读，但**没有任何独立审阅意见**　{human}")
    fixed = _REVIEW_AXIS_LABEL.get(version.review_producer_kind)
    if fixed:
        return (f"{fixed}（机器对照意见 {version.blocking_issue_count} 条 blocking）　{human}")
    tail = ""
    if (version.system_review_state == "system_review_not_passed"
            and version.blocking_issue_count == 0 and version.blocking_sentence_count > 0):
        tail = (f"——审阅本身没提 blocking，拦住的是"
                f"{version.blocking_sentence_count} 句**机械硬错误**（可机械证明的底线失败"
                f"优先，不看审阅是否同意）")
    return (f"③ 独立审阅：`{version.system_review_state}`"
            f"（blocking {version.blocking_issue_count} 条）{tail}　{human}")


#: 缺口分桶 → 读者面的一句话（`crpp-6`）。它**必须**把「材料里有没有这张表」与
#: 「本轮算不算必需」分开说——旧标题「本合同要求的，本次没有可用来源」把四种完全不同的处境
#: 合成了一句：确有材料但数字未授权、可选栏未写、Contract 判合法不适用、来源里确实没有。
#: 四种对下游是四条不同的指令，读者不能从一句话里反推。
_GAP_BIN_LABEL = {
    "required": "**Contract 要求写进正文，本轮未取得**（内容缺）",
    "optional": "Contract 判为**可选**栏，未写**不**算必需未覆盖",
    "not_applicable": "Contract 判**合法不适用**（`missing_policies` 明文 NOT_APPLICABLE），**不**阻断",
    "diagnostic": "**诊断栏**，本就不进正文门",
    "unresolved": "**在冻结 Contract 里查不到这一栏**（fail-closed：按必需处理）",
}


def render_cited_preview_markdown(preview: CitedSectionPreview,
                                  *, gap_bins: Sequence[CitedGapBin] = ()) -> str:
    """预览 → Markdown：**逐句**给出引用、机械结论与审阅意见，一处错误不清零整节。

    `gap_bins`（`crpp-6` 起可选）是同一份草稿的缺口分桶（`cited_gap_bins.json` 的那批行）。
    给了就把缺口**逐条按档**渲染，并把两条轴（正文应写未写 / 放行阻断）分开报；不给就退回
    不带分档的渲染，但**标题也不再冒充**「来源里没有」。
    """
    version = preview.version
    lines: list[str] = []
    lines.append(f"# {preview.section_title}")
    lines.append("")
    lines.append(_NOT_PUBLISHABLE_BANNER)
    lines.append("")
    if version.review_failure:
        lines.append(
            f"> **独立审阅未完成**（`{version.review_failure}`）。这一份是**降级诊断预览**："
            "正文、逐句机械核对、缺口与补件需求都是本轮的真实产出，照旧逐句可读；"
            "**缺的是独立审阅这一环**——下面每一行的「审阅」都标着「未完成」，"
            "那不是「审阅看过、没问题」。本页不伪造审阅意见，也不宣布任何正式报告版本。")
        lines.append("")
    lines.append(f"- report_version：`{version.report_version}`")
    lines.append(f"- 输入清单：`{version.input_manifest_id}` ／ 依赖指纹 "
                 f"`{version.dependency_fingerprint[:16]}…`")
    lines.append(f"- ① 预览：`{version.preview_state}`　"
                 f"② 机械核对：`{version.mechanical_state}`"
                 f"（{version.blocking_sentence_count}/{version.sentence_count} 句带硬错误）　"
                 + _review_axis_text(version))
    blockers = version.release_blockers()
    lines.append(f"- 若要发布，还差：{('、'.join(blockers) if blockers else '（无）')}")
    lines.append(f"- 发布资格：`{version.publishability}`")
    lines.append("")
    # 这一行是「同版」这句话**可核对**的凭证：正文、指标表、记录指纹都指向同一份记录。
    lines.append(f"- 记录指纹：`{version.fingerprint()}`"
                 f"　指标表指纹："
                 f"{('`' + version.metric_tables_fingerprint + '`') if version.metric_tables_fingerprint else '**（本节无指标表）**'}")
    lines.append("")

    title_by_subsection = dict(preview.subsections)
    current = None
    for row in preview.rows:
        if row.subsection_id != current:
            current = row.subsection_id
            lines.append("")
            lines.append(f"## {title_by_subsection.get(current, current)}")
            lines.append("")
        marks: list[str] = []
        if row.mechanical_verdict != "pass":
            marks.append(_mechanical_mark(row))
        if row.review_blocking:
            marks.append("**审阅 blocking**")
        elif row.review_semantic_category:
            marks.append("审阅意见")
        if row.review_state == "not_in_review_scope":
            marks.append("**不在审阅对象内**")
        elif row.review_state == "review_not_completed":
            marks.append("**未经独立审阅**")
        flag = f"　［{'／'.join(marks)}］" if marks else ""
        lines.append(f"- {row.text}{flag}")
        citations = "、".join(f"`{c}`" for c in row.citations) or "（无引用）"
        lines.append(f"  - 引用：{citations}")
        # 逐句机械结论**按族分列**（`crpp-5`）：同一句可以在两族上同时失败，两族都印出来，
        # 因为「这句在哪一边错了」决定了下一步是改取材还是补资格。族由持久化的原因码现算
        # （`family_of_failure_reason`），**不加字段**。
        family_text = ""
        if row.failure_reasons:
            seen = [SC.family_of_failure_reason(r) for r in row.failure_reasons]
            families = [f for f in SC.CHECK_FAMILIES if f in seen]
            family_text = "族：" + "／".join(
                _FAMILY_LABEL.get(f, f) for f in families) + "；"
        lines.append(f"  - {_mechanical_label(row)}"
                     + (f"（{family_text}{'；'.join(f'{failure_reason_label(r)}（`{r}`）'
                                                 for r in row.failure_reasons)}）"
                        if row.failure_reasons else ""))
        if row.review_semantic_category:
            lines.append(f"  - 审阅：`{row.review_semantic_category}`"
                         f"／严重度 `{row.review_severity}`"
                         f"{'／blocking' if row.review_blocking else ''}"
                         + (f"——{row.review_reason}" if row.review_reason else ""))
        elif row.review_state == "not_in_review_scope":
            lines.append("  - 审阅：**不在独立审阅对象内**（本句没有任何引用；逐句审阅的最小"
                         "单元是「一句话 + 它引的来源」，没有引用的句子给不出这个单元）")
        elif row.review_state == "review_not_completed":
            lines.append("  - 审阅：**本轮独立审阅未完成**——这一句没有被任何独立意见覆盖，"
                         "**不等于**「审阅看过、没问题」")

    if preview.metric_table_outcome is not None:
        outcome = preview.metric_table_outcome
        body_tables = tuple(t for t in outcome.tables
                            if t.display_tier != "diagnostic_only")
        diagnostic_tables = tuple(t for t in outcome.tables
                                 if t.display_tier == "diagnostic_only")
        lines.append("")
        lines.append("## 财务指标表（由合格权威事实确定性生成，逐格可回查）")
        lines.append("")
        if body_tables:
            for table in body_tables:
                lines.append(CFT.render_cited_metric_table_markdown(table))
                lines.append("")
        elif not diagnostic_tables:
            lines.append("本节**没有**生成任何指标表。")
            lines.append("")
        #: 诊断槽位必须与正文表**分开成块**：代理口径的指标一旦与精确口径的行同表，读者读到的
        #: 就是「这一节用受审口径给出了这个数」。档位由 `cmtr-4` 按事实自己的代理标记**与**呈现层
        #: 路由所指向那一栏的 Contract 展示层级共同判定（两条判据必须一致），且
        #: 代理限定语照旧渲染在每一格里，因此这里只是**换块**，不是隐藏。
        if diagnostic_tables:
            lines.append("### 诊断槽位（`display_tier=diagnostic_only`，"
                         "**不并入正文指标表**；代理口径限定语照旧逐格呈现）")
            lines.append("")
            #: 读者面**只读**的限制说明：把这一块是什么、不是什么写清楚。
            #: 它不是一条更强的口径结论，也不是可以和正文精确口径行并排比较的数；
            #: 代理口径的极端值（例如分母为负、或分母极小）会让这一列在数值上失去
            #: 通常「利息保障倍数」读法所期待的含义，因此只作诊断参考。
            lines.append("> **这一块是诊断参考，不是结论。** 下面这一行的数由**代理口径**"
                         "（`PROXY_FINANCE_EXPENSES`）算得，`FORMULA_REVIEW.md` §1 写明代理"
                         "结果「不参与『精确口径』比较」；它**不**与上面的正文指标表逐格可比，"
                         "也**不**在本报告里承担「利息保障倍数」这一口径的正式结论——"
                         "该结论需要精确口径的利息费用，本次未取得时不作正式结论。"
                         "逐格读法只看**它自己那一行的走势**，不要把它当成可与其他指标"
                         "换算或加权的一格。")
            lines.append("")
            for table in diagnostic_tables:
                lines.append(CFT.render_cited_metric_table_markdown(table))
                lines.append("")
        for refusal in outcome.refusals:
            lines.append(f"- 本节指标表被拒：`{refusal.reason}`——{refusal.detail}")
        if outcome.refusals:
            lines.append("")
        lines.append(CFT.render_cited_metric_coverage_markdown(outcome))
        lines.append("")

    if preview.gaps:
        lines.append("")
        lines.append("## 缺口（逐条分档；**不等于**「来源里没有」）")
        lines.append("")
        bins_by_id = {b.gap_id: b for b in (gap_bins or ())}
        if bins_by_id:
            axes = gap_axes_from_bins(gap_bins)
            lines.append(
                f"- 共 **{axes['total_gap_count']}** 条缺口；其中 Contract 要求写进正文而"
                f"本轮未取得的 **{axes['required_body_gap_count']}** 条，"
                f"按冻结 Contract 的 `blocking_policy` **阻断放行**的 "
                f"**{axes['release_blocking_gap_count']}** 条"
                f"（`required_gap_count` **{axes['required_gap_count']}** 条 = 必需 + 查不到）。"
                "**两条轴不是一回事**：内容该补的缺口未必单独阻断放行，反之亦然。")
            lines.append("")
        for gap in preview.gaps:
            bin_row = bins_by_id.get(str(gap.gap_id))
            label = _GAP_BIN_LABEL.get(bin_row.gap_bin, "（**未分档**：本页未拿到分桶旁挂）") \
                if bin_row else None
            prefix = f"- （{label}）" if label else "- "
            lines.append(f"{prefix}`{gap.reason}`：{gap.requirement_text}"
                         + (f"——{gap.detail}" if gap.detail else ""))

    if preview.follow_up_needs:
        lines.append("")
        lines.append("## 补件需求（Writer 提出，本条链不执行）")
        lines.append("")
        for need in preview.follow_up_needs:
            lines.append(f"- `{need.requiredness}`：{need.statement}")
    return "\n".join(lines).rstrip() + "\n"

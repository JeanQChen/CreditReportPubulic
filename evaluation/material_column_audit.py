"""M930-3 **逐栏目材料契合同表**（人读 / 机器读）——`material_fitness.md` / `.json`。

它回答的是业务侧那一问，而不是研究侧那一问：

  「这一栏到底取到了什么原文、出自哪一份文档的哪一版、在第几页 / 哪个定位符、它**能**证明
   什么、**不能**证明什么，以及它**属于这一栏还是属于别人**。」

与既有产物的分工（四者是**同一次读回**的不同渲染，不各算一遍）：

  * `material_pack.md` / `.json`（T1）：逐**材料**摊开原文与去向（Pack 本体读数）；
  * `source_manifest.md` / `.json`：逐**来源**给出登记身份与来源角色；
  * `cross_document_evidence.md`：逐**来源**回答「哪一份查了没有」；
  * 本文件：逐**栏目**回答「对题不对题、能不能用、缺什么」。因此它是**只读视图**：输入的
    每一个字段都来自前者的产物，本模块**不重读 Pack、不调 LLM、不联网、不写库**。

**四列互不推出（`material-column-audit/3`）**：`相关／弱相关／误召` 只是第①列**结构匹配**
（章节标题的完整标签段是否整段等于本栏本层 / 祖先层声明键）。它与另外三列**是四件不同的事**，
任何一列都不得顶替另一列：

  ① **结构匹配** `structural_match`：**只**按章节标题标签段判定（`NAV_SEGMENT_RULE_ID`，与导航
     同一条判据、同一个实现）。它回答「这一块原文在不在本栏那一节里」；
  ② **正文相关性** `body_relevance`：**只**读内容形态与原文可回查性（`narrative_prose` /
     `disclosure_state_only` / …）。「形态上是散文」**不等于**「原文确实在讲本栏那一件事」；
     后者要逐字读原文，**本表一个字都不判**（`semantic_relevance: "not_judged_here"`）；
  ③ **事实资格** `fact_eligibility`：照抄 Pack 侧处置（admission / retention）与形态读数的合取，
     只说「能不能承载事实候选的取材」；它**不判**绑定与蕴含——那是 Writer / Evaluator 的门
     （`ClaimBindingDecision` / `ClaimEntailmentDecision`）；
  ④ **实际写作采用** `writing_adoption`：材料**有没有真的进**本节的 Writer 精确材料清单；
     `null` 是**不可判定**，不是「没进」。

因此本表里的档位是**取材契合度**，不是内容质量分：

  * `相关`：章节标题命中本栏**本层**声明键（材料就在本栏自己那一节里）；
  * `弱相关`：只命中**祖先层**键（材料取自本栏的父章节整章正文，用前需在本节内再定位）；
  * `误召`：两者都不命中（章节不属于这一栏——这正是「共享父节点即有效材料」要挡的形状）；
  * `被拒绝`：Pack 侧处置为 `rejected`（带 typed 理由码）；
  * `定位不可判定`：材料定位符读不出章节路径（**不是**「不属于这一栏」）；
  * `无处置记录`：材料侧义务是「每份恰一条处置」，缺记录本身就是要修的事实。

**导航读集与材料绑定是两条不同的轴**：材料之所以记在某栏下，来自该栏的 aspect 结果
（`aspect_ids_from_aspect_results` / `disposition.aspect_ids`），导航只决定「先读哪些章节」。
所以本表**不**用「导航有没有读到」去判材料对题与否，只用章节标题的结构契合——两条轴各自
记录、互不冒充。

**档位按现行规则（`PROFILE_RULE_VERSION`）判定**，并同时给出按**本次运行记录的规则版本**判定的
档位（`fit_run_rule`）与两者是否不同（`fit_changed_by_rule_version`）。这样 `anp-6` 的兄弟项排除
在真实文档上"改变了哪几栏的取材判定"是**表里可见**的，而不是靠人记得。

用法（离线诊断；不写库、不调 LLM）::

    python -X utf8 -m evaluation.material_column_audit <run_dir> [--out <dir>] [--repo <dir>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from document_structure import navigation as NAV

#: `material-column-audit/3`：每份材料另给**四列**互相独立的读数（结构匹配 / 正文相关性 /
#: 事实资格 / 实际写作采用），见下。`/2` 的读数里只有三条轴（取材契合 + 本栏缺口 + Contract
#: 完成规则），**没有**把「章节标题对得上」「原文形态能进正文」「能不能承载事实」「有没有真的
#: 被写作采用」分成四件不同的事——于是 `相关／弱相关／误召` 很容易被当成内容语义资格用。
#: 四条轴互不推出，故版本前进。
COLUMN_AUDIT_SCHEMA_VERSION = "material-column-audit/3"

#: 第①列 结构匹配的判据名（**封闭**）：只有这一条。
STRUCTURAL_MATCH_BASIS = "section_title_label_segment_equality"

#: 第②列 正文相关性的取值（**封闭**集合）。它**只**读内容形态与原文可回查性，
#: **不判语义**：`narrative_prose` 说的是「这块原文形态上是散文」，**不是**「它在讲本栏那一件事」。
BODY_RELEVANCE_VALUES: tuple[str, ...] = (
    "narrative_prose",        # `kind=text` 且原文可逐字回查 ⇒ **形态上**可作正文素材
    "disclosure_state_only",  # `kind=selection_form` ⇒ 只承载披露事项的适用状态
    "other_form",             # 其它形态（表格等）：形态上不是散文，正文相关性另行判定
    "text_unreadable",        # `kind=text` 但原文回查不到（读不出 ≠ 原文为空）
    "kind_unreadable",        # 形态读不懂
    "no_disposition",         # 连处置记录都没有——取材义务未履行
)

#: 第②列的语义那一半**不由本表判**（封闭值；没有第二个取值）。
BODY_RELEVANCE_SEMANTIC_FIELD = "not_judged_here"

#: 第③列 事实资格的取值（**封闭**集合）。它是「Pack 侧处置 × 内容形态」的**合取**，
#: 只说能不能承载事实候选的取材；**不判**蕴含、不判绑定、不判 authority。
FACT_ELIGIBILITY_VALUES: tuple[str, ...] = (
    "fact_candidate_eligible",  # admitted + retained + `kind=text` + 原文可回查
    "disclosure_state_only",    # 只能在**所问事项的适用状态**范围内说话
    "other_form",               # 形态上不是散文：不得当业务事实正文
    "not_admitted",             # Pack 侧处置不是 `admitted`
    "not_retained",             # 已接纳但未保留（`retention_state != retained`）
    "text_unreadable",          # 原文回查不到
    "kind_unreadable",          # 形态读不懂
    "no_disposition",           # 缺处置记录
)

#: 第④列 实际写作采用的取值（**封闭**集合）。`manifest_unreadable` 是「不可判定」，
#: **不是**「没进」（`material-pack/2` 的原话：`null` 不是 `false`）。
WRITING_ADOPTION_VALUES: tuple[str, ...] = (
    "in_writer_manifest",
    "not_in_writer_manifest",
    "manifest_unreadable",
)
DEFAULT_CONTRACT_ASSET = "templates/contracts/standard_v3.yaml"
SECTION_ORDER: tuple[str, ...] = ("company", "financial", "industry")

#: 取材契合档位（**封闭**集合；人读与机器读共用同一份，不得散成自由字符串）。
FIT_LABELS: tuple[str, ...] = (
    "相关",
    "弱相关",
    "误召",
    "祖先层不可判定",
    "被拒绝",
    "定位不可判定",
    "无处置记录",
)

#: 档位背后的**判据名**（封闭集合）：每一条都指向一个可复核的结构性事实。
FIT_REASONS: tuple[str, ...] = (
    "own_section_key",           # 命中本栏本层声明键
    "ancestor_section_key",      # 只命中本栏祖先层键
    "no_structural_key",         # 两者都不命中
    "ancestor_keys_unavailable",  # 祖先层键**无从派生**（本栏不在冻结 Contract 里）
    "locator_unavailable",       # 定位符读不出章节路径
    "rejected_by_pack",          # Pack 侧拒绝
    "no_disposition",            # 缺处置记录
)

#: 缺口类型（封闭集合；只写登记过的事实，不给业务结论）。
GAP_KINDS: tuple[str, ...] = (
    "pack_section_unavailable",  # 本节的材料包读回本身没读成（**不是**「没有材料」）
    "navigation_fallback",       # 导航终态是 fallback（带 typed 原因）
    "no_material",               # 这一栏一条材料都没有
    "all_rejected",              # 取了材料但全被 Pack 拒绝
    "no_on_topic_material",      # 有材料，但没有一条结构上属于本栏
    "text_unavailable",          # 有可用材料，但原文回查不到
)

#: 材料键的来源（封闭集合）：键是从**本次运行的报告读数**取的，还是由**冻结 Contract 派生**的。
KEY_SOURCES: tuple[str, ...] = (
    "run_tree_navigation",          # 本次运行的逐节导航读数（最忠实）
    "contract_derivation_current",  # 由 Contract 派生（现行规则）
    "unavailable",
)

#: 第三轴（Contract 完成规则）的读数档位（**封闭**集合）。
#:
#: 这一轴**不是**本表判的：本表只把 Pack 侧**已有的 typed 覆盖读数**逐字照抄到栏目下。
#: 三档刻意**不含**「已满足」——「没读到缺口」与「缺口不存在」是两件事，
#: 「找到原文」更推不出「完成规则满足」（采购/生产有原文 ≠ 该栏完整；一句
#: 「以自主研发为主、外部合作为辅的研发模式」不等于 `tech_route` 已满足）。
COMPLETION_KINDS: tuple[str, ...] = (
    "typed_gap_present",   # 本栏在 Pack 侧有 typed 缺口行（逐字连同 `detail` / `blocking` 照抄）
    "no_typed_gap_read",   # 本栏在本轮产物里**没有**任何 typed 覆盖缺口读数 ⇒ **无从判定**，不是「已满足」
    "completion_unreadable",  # 产物读不回来 / 没有可读的 Pack 缺口读数 ⇒ 这一轴根本没做
)

#: 第三轴读数的来源（封闭集合）：来自**本次运行产物**，还是读不出来。
COMPLETION_READ_SOURCES: tuple[str, ...] = (
    "run_acceptance_report_pack_gaps",  # 本次运行验收报告里的 Pack 逐 aspect 缺口行
    "unavailable",
)


def _load_json(path: Path) -> tuple[Any, str]:
    """读一个 JSON 产物；读不出时返回 `(None, typed 原因)`，**不抛**。

    读不回来与"里面没有东西"是两件事：本模块的每一处都保留这个区分。
    """
    if not path.is_file():
        return None, f"missing_file:{path.name}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except Exception as exc:  # noqa: BLE001 —— 坏产物不得把整张表带走
        return None, f"unreadable_file:{path.name}:{type(exc).__name__}"


def _walk(obj: Any, key: str):
    """递归产出所有名为 `key` 的值（报告里同名块按节 / 按文档多次出现）。"""
    if isinstance(obj, Mapping):
        for name, value in obj.items():
            if name == key:
                yield value
            yield from _walk(value, key)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item, key)


# --------------------------------------------------------------------------------------
# 输入读数（逐份产物）
# --------------------------------------------------------------------------------------

def _section_topics(manifest: Any) -> tuple[dict[str, str], dict]:
    """本次运行的 `section → topic_ids` 与它记录的 Contract 身份。

    `topic → section` 的归属**只**取自本次运行的 manifest，不在本模块里另立一套映射：
    谁是"财务栏"必须由这一轮自己说，不由本表猜。
    """
    out: dict[str, str] = {}
    recorded: dict = {}
    if not isinstance(manifest, Mapping):
        return out, recorded
    contract = manifest.get("contract")
    if isinstance(contract, Mapping):
        recorded = {"version": contract.get("version"),
                    "fingerprint": contract.get("fingerprint")}
    for section_id, block in (manifest.get("sections") or {}).items():
        if not isinstance(block, Mapping):
            continue
        for topic_id in block.get("topic_ids") or ():
            out.setdefault(str(topic_id), str(section_id))
    return out, recorded


def _navigation_rows(report: Any) -> tuple[dict[str, dict], str]:
    """从验收报告取逐 `(topic, aspect)` 的导航读数（合并同一 aspect 的重复行）。

    报告里的 `tree_navigation` 行**不带文档身份**，同一 aspect 按「逐节 × 逐文档」重复出现。
    本函数按 `aspect_id` 合并：相同读数去重，不同读数全部保留在 `outcomes`，并记出现次数
    `observation_count`。**出现次数不是文档数**——这一句写在 `notes` 里，免得被读成"查了几份"。
    """
    if report is None:
        return {}, "navigation_unavailable:acceptance_report_not_readable"
    rows: dict[str, dict] = {}
    for block in _walk(report, "tree_navigation"):
        if not isinstance(block, list):
            continue
        for row in block:
            if not isinstance(row, Mapping):
                continue
            aspect_id = str(row.get("aspect_id", "") or "")
            if aspect_id == "":
                continue
            entry = rows.setdefault(aspect_id, {
                "aspect_id": aspect_id, "topic_id": str(row.get("topic_id", "") or ""),
                "nav_keys": [], "parent_keys": [], "outcomes": [],
                "observation_count": 0, "rule_versions": [],
                "recorded_tier": [], "recorded_fallback_reasons": []})
            entry["observation_count"] += 1
            rule_version = str(row.get("rule_version", "") or "")
            if rule_version and rule_version not in entry["rule_versions"]:
                entry["rule_versions"].append(rule_version)
            for name in ("nav_keys", "parent_keys"):
                values = row.get(name)
                if isinstance(values, list):
                    for value in values:
                        text = str(value)
                        if text not in entry[name]:
                            entry[name].append(text)
            tier = str(row.get("key_tier", "") or "")
            if tier and tier not in entry["recorded_tier"]:
                entry["recorded_tier"].append(tier)
            reason = row.get("fallback_reason")
            if reason and reason not in entry["recorded_fallback_reasons"]:
                entry["recorded_fallback_reasons"].append(reason)
            outcome = {
                "key_tier": tier,
                "status": str(row.get("status", "") or ""),
                "fallback_reason": reason,
                "selected_node_id": row.get("selected_node_id"),
                "read_root_titles": [str(c.get("title", "") or "") for c in
                                     (row.get("candidates") or [])
                                     if isinstance(c, Mapping)
                                     and c.get("in_read_set") is True],
                "read_node_count": int(row.get("read_node_count") or 0),
            }
            if outcome not in entry["outcomes"]:
                entry["outcomes"].append(outcome)
    return rows, ("" if rows else "navigation_unavailable:no_tree_navigation_rows")


def _report_declarations(report: Any) -> dict[str, dict]:
    """从验收报告取逐 aspect 的**声明**读数（逐字段照抄；缺失时留空不猜）。"""
    out: dict[str, dict] = {}
    if report is None:
        return out
    for container in _walk(report, "aspects"):
        if not isinstance(container, list):
            continue
        for row in container:
            if not isinstance(row, Mapping):
                continue
            aspect_id = str(row.get("aspect_id", "") or "")
            if aspect_id == "" or aspect_id in out:
                continue
            out[aspect_id] = {
                "question_id": str(row.get("question_id", "") or ""),
                "requirement_text": str(row.get("requirement_text", "") or ""),
                "kind": str(row.get("kind", "") or ""),
                "content_role": str(row.get("content_role", "") or ""),
                "output_destination": str(row.get("output_destination", "") or ""),
                "missing_policy": str(row.get("missing_policy", "") or ""),
                "time_scope": str(row.get("time_scope", "") or ""),
            }
    return out


def _pack_completion_rows(report: Any) -> tuple[dict[str, list[dict]], str]:
    """从验收报告取**Pack 侧逐 aspect 的 typed 缺口行**（照抄，不解释）。

    这是第三轴（Contract 完成规则）的唯一读数来源：Pack 自己写下的 typed 缺口行
    （`reason_code` / `layer_reason` / `detail` / `blocking`）按 `aspect_ids` 落到本栏。
    一行绑多个 aspect 时**逐栏各记一次**（这一行同时是那几栏的缺口读数）。

    **不判定**这一行是否等于「完成规则未满足」——那要由 Pack 侧规则与人工裁决说；
    本函数只保证「Pack 写了什么，这里逐字是什么」。读不出来时返回 typed 原因，
    **不抛**：读不回来与「没有缺口」是两件事。
    """
    out: dict[str, list[dict]] = {}
    if report is None:
        return out, "completion_unavailable:acceptance_report_not_readable"
    for rows in _walk(report, "gap_rows"):
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            detail = str(row.get("detail", "") or "")
            entry = {
                "reason_code": str(row.get("reason_code", "") or ""),
                "layer_reason": str(row.get("layer_reason", "") or ""),
                "detail": detail,
                #: `detail` 的第一段（`coverage_gate_not_met: 技术路线` 里的前半）——
                #: 只做**切分**，不做语义归类，也不改写原串。
                "code": (detail.split(":", 1)[0] if detail else ""),
                "blocking": row.get("blocking"),
            }
            for aspect_id in (row.get("aspect_ids") or ()):
                key = str(aspect_id)
                if key:
                    out.setdefault(key, []).append(dict(entry))
    if not out:
        return {}, "completion_unavailable:no_pack_gap_rows"
    return out, ""


def _completion_for(aspect_id: str, gaps: Mapping[str, list[dict]],
                    read_reason: str) -> dict:
    """本栏的第三轴读数（**照抄** Pack 侧缺口行；本表不判完成规则）。

    `verdict` 三档里**没有**「已满足」：
    - 有 typed 缺口行 ⇒ `typed_gap_present`（原文里找没找到是另一条轴上的事）；
    - 一份缺口行都没有 ⇒ `no_typed_gap_read`（**无从判定**，不是「过了」）；
    - 缺口读数读不出来 ⇒ `completion_unreadable`（这一轴根本没做）。
    """
    rows = list(gaps.get(aspect_id) or ())
    if read_reason:
        return {"source": "unavailable", "read_reason": read_reason,
                "verdict": "completion_unreadable", "rows": [],
                "codes": [], "blocking_count": 0}
    if rows:
        codes: list[str] = []
        for row in rows:
            code = str(row.get("code") or "")
            if code and code not in codes:
                codes.append(code)
        return {"source": "run_acceptance_report_pack_gaps", "read_reason": "",
                "verdict": "typed_gap_present", "rows": rows,
                "codes": codes,
                "blocking_count": sum(1 for row in rows if row.get("blocking") is True)}
    return {"source": "run_acceptance_report_pack_gaps", "read_reason": "",
            "verdict": "no_typed_gap_read", "rows": [], "codes": [],
            "blocking_count": 0}


def _source_manifest(path: Path) -> tuple[dict, str]:
    """登记来源清单的可读切片：逐份身份、来源角色、主锚、登记类不符。"""
    payload, reason = _load_json(path)
    if not isinstance(payload, Mapping):
        return {}, reason or "source_manifest_not_a_mapping"
    selection: dict[tuple[str, str], dict] = {}
    for row in payload.get("selection") or ():
        if isinstance(row, Mapping):
            selection[(str(row.get("document_id") or ""),
                       str(row.get("document_version") or ""))] = dict(row)
    documents: list[dict] = []
    for entry in payload.get("entries") or ():
        if not isinstance(entry, Mapping):
            continue
        key = (str(entry.get("document_id") or ""),
               str(entry.get("document_version") or ""))
        picked = selection.get(key, {})
        period = entry.get("content_report_period") or {}
        judgment = entry.get("type_judgment") or {}
        documents.append({
            "document_id": key[0], "document_version": key[1],
            "source_name": entry.get("source_name"),
            "registered_source_type": entry.get("registered_source_type"),
            "identified_document_class": judgment.get("document_class"),
            "evidence_set_version": entry.get("evidence_set_version"),
            "eligibility": entry.get("eligibility"),
            "content_report_period": period.get("period"),
            "content_report_period_state": period.get("state"),
            "source_role": picked.get("source_role"),
            "retrieval_order": picked.get("retrieval_order"),
            "selection_state": picked.get("selection_state"),
            "selection_reason_code": picked.get("reason_code"),
        })
    return {
        "policy_version": payload.get("policy_version"),
        "company_id": payload.get("company_id"),
        "primary_document_id": payload.get("primary_document_id"),
        "primary_document_version": payload.get("primary_document_version"),
        "current_state": payload.get("current_state"),
        "documents": documents,
        "registration_class_mismatches": list(
            payload.get("registration_class_mismatches") or ()),
        "provenance_findings": list(payload.get("provenance_findings") or ()),
    }, ""


def _contract_context(contract_asset: Path) -> dict:
    """由**冻结 Contract** 取声明字段与两套祖先层键（现行规则 / 本次运行记录的规则）。

    本函数只调用 `document_structure.navigation` 的生产函数，**不另写一套**匹配或派生：
    `aspect_nav_keys` / `contract_ancestor_labels` / `contract_sibling_keys` /
    `parent_nav_keys`。祖先层两套键的差别只有一处——`anp-6` 的兄弟项排除集给不给，因此
    "本次运行记录的规则"用的是 `parent_nav_keys(own, labels)`（`anp-5` 形态，默认空排除集）。
    """
    context: dict = {
        "asset": str(contract_asset), "loaded": False, "error": "",
        "contract_version": None, "frozen_at": None, "aspect_count": 0,
        "aspects": {}, "rule_version_current": None, "rule_version_run_recorded": None,
    }
    try:
        from contracts.loader_v2 import load_contract_v2
    except Exception as exc:  # noqa: BLE001
        context["error"] = f"loader_unavailable:{type(exc).__name__}"
        return context
    if not contract_asset.is_file():
        context["error"] = f"contract_asset_missing:{contract_asset}"
        return context
    try:
        contract = load_contract_v2(str(contract_asset))
    except Exception as exc:  # noqa: BLE001
        context["error"] = f"contract_load_failed:{type(exc).__name__}:{str(exc)[:160]}"
        return context
    try:
        labels, siblings = NAV.contract_ancestor_inputs(contract)
    except Exception as exc:  # noqa: BLE001
        context["error"] = f"ancestor_inputs_failed:{type(exc).__name__}:{str(exc)[:160]}"
        return context
    from document_structure import versions as VER
    context["rule_version_current"] = VER.PROFILE_RULE_VERSION
    aspects: dict[str, dict] = {}
    for aspect in contract.all_aspects():
        aspect_id = str(getattr(aspect, "aspect_id", "") or "")
        if aspect_id == "":
            continue
        fields = tuple(getattr(aspect, "required_fields", ()) or ())
        requirement_text = str(getattr(aspect, "requirement_text", "") or "")
        own = NAV.aspect_nav_keys(fields, requirement_text)
        ancestor_texts = tuple(labels.get(aspect_id, ()))
        ancestor_current = NAV.parent_nav_keys(own, ancestor_texts,
                                               siblings.get(aspect_id, ()))
        ancestor_run = NAV.parent_nav_keys(own, ancestor_texts)
        aspects[aspect_id] = {
            "aspect_id": aspect_id,
            "topic_id": str(getattr(aspect, "topic_id", "") or ""),
            "question_id": str(getattr(aspect, "question_id", "") or ""),
            "requirement_text": requirement_text,
            "required_fields": list(fields),
            "kind": str(getattr(aspect, "kind", "") or ""),
            "content_role": str(getattr(aspect, "content_role", "") or ""),
            "output_destination": str(getattr(aspect, "output_destination", "") or ""),
            "missing_policy": str(getattr(aspect, "missing_policy", "") or ""),
            "time_scope": str(getattr(aspect, "time_scope", "") or ""),
            "own_keys": list(own),
            "ancestor_texts": list(ancestor_texts),
            "ancestor_keys_current_rule": list(ancestor_current),
            "ancestor_keys_run_rule": list(ancestor_run),
            "sibling_exclusion_keys": list(siblings.get(aspect_id, ())),
        }
    context.update({
        "loaded": True, "aspect_count": len(aspects), "aspects": aspects,
        "contract_version": getattr(contract, "contract_version", None),
        "frozen_at": getattr(contract, "frozen_at", None),
    })
    return context


# --------------------------------------------------------------------------------------
# 契合判定（纯函数：给定两套键与一条材料，给出档位 + 判据）
# --------------------------------------------------------------------------------------

def _section_title_samples(locator: Mapping) -> tuple[tuple[str, ...], str]:
    """材料定位符里的**标题样本**（逐段标题 + 表题），以及取不到时的 typed 原因。"""
    titles: list[str] = []
    section_path = locator.get("section_path")
    if isinstance(section_path, str) and section_path.strip() != "":
        for piece in section_path.split("/"):
            piece = piece.strip()
            if piece != "" and piece not in titles:
                titles.append(piece)
    table_title = locator.get("table_title")
    if isinstance(table_title, str) and table_title.strip() != "":
        if table_title.strip() not in titles:
            titles.append(table_title.strip())
    if not titles:
        return (), "locator_unavailable:no_section_path_and_no_table_title"
    return tuple(titles), ""


def _path_key_hits(titles: tuple[str, ...], keys: tuple[str, ...]) -> tuple[str, ...]:
    """章节路径里**整段等于**某个键的键（`NAV_SEGMENT_RULE_ID` 的唯一判据）。

    用导航自己的 `title_label_segments`（剥编号前缀 + 按枚举符 / 并列连词切段 + 规范化），
    与生产规则**同一个实现**——本模块不另写一套"看起来差不多"的匹配。
    """
    if not titles or not keys:
        return ()
    wanted = set(keys)
    hits: list[str] = []
    for title in titles:
        for segment in NAV.title_label_segments(title):
            if segment in wanted and segment not in hits:
                hits.append(segment)
    return tuple(hits)


def _classify(row: Mapping, titles: tuple[str, ...], locator_reason: str,
              own_keys: tuple[str, ...], ancestor_keys: tuple[str, ...],
              ancestor_keys_available: bool = True) -> dict:
    """给定键集，判定一条材料的取材契合（只做结构判定，不做语义判断）。

    `ancestor_keys_available=False` 表示**祖先层键根本派生不出来**（本栏不在冻结
    Contract 里，也没有别的派生来源）。此时**不得**拿「本次运行记录的规则形态」那套键
    顶上——那样会把「无从判定」冒充成「按现行规则判过了」，于是 `anp-6` 的排除效果正好
    在最需要看见的地方消失。这种情况单列一档，不并入 `误召`（两者都是下界读数，
    但原因不同，修法也不同）。

    判据顺序（前面的压过后面的）：缺处置记录 > Pack 拒绝 > 本层键命中 > **定位符读不出** >
    祖先层键派生不出 > 祖先层键命中 > 都不命中。`定位不可判定` 排在祖先层不可判定之前：
    连"这一段在哪一节"都读不出来时，那条读数最该先被看见。
    """
    own_hits = _path_key_hits(titles, own_keys)
    ancestor_hits = _path_key_hits(titles, ancestor_keys)
    disposition = row.get("disposition")
    if not isinstance(disposition, Mapping):
        reason, fit = "no_disposition", "无处置记录"
    elif str(disposition.get("admission_state") or "") == "rejected":
        reason, fit = "rejected_by_pack", "被拒绝"
    elif own_hits:
        reason, fit = "own_section_key", "相关"
    elif locator_reason:
        reason, fit = "locator_unavailable", "定位不可判定"
    elif not ancestor_keys_available:
        reason, fit = "ancestor_keys_unavailable", "祖先层不可判定"
    elif ancestor_hits:
        reason, fit = "ancestor_section_key", "弱相关"
    else:
        reason, fit = "no_structural_key", "误召"
    return {"fit": fit, "fit_reason": reason,
            "own_key_hits": list(own_hits), "ancestor_key_hits": list(ancestor_hits)}


def _can_prove(row: Mapping, fit_reason: str) -> tuple[str, ...]:
    """这一份**能**证明什么（只按登记过的 typed 字段说）。"""
    qualification = row.get("content_qualification") or {}
    kind = str(qualification.get("kind", "") or "")
    disposition = row.get("disposition") or {}
    if disposition.get("admission_state") != "admitted":
        return (f"不能证明任何事实：Pack 侧处置不是 `admitted`"
                f"（`{disposition.get('admission_state')}`，理由码 "
                f"`{disposition.get('reason_code')}`）",)
    if not qualification.get("kind_in_vocabulary"):
        return (f"不能证明任何事实：内容形态读不懂（`{kind}`，"
                f"`{qualification.get('kind_basis')}`）",)
    lines: list[str] = []
    if kind == "selection_form":
        selection = qualification.get("selection") or {}
        labels = "、".join(str(x) for x in (selection.get("selected_labels") or ()))
        lines.append("只能在**勾选表单行所问事项**的范围内说话：披露事项的**适用状态**"
                     + (f"（本行选中 `{labels}`）" if labels else "")
                     + f"；允许用途 `{selection.get('permitted_use')}`")
    elif fit_reason == "own_section_key":
        lines.append("可在本条原文范围内承载本栏（本层那一节）的事实候选"
                     "（仍需 Claim 绑定与蕴含门，本表不判蕴含）")
    elif fit_reason == "ancestor_section_key":
        lines.append("可作为本栏**父章节**整章正文的取材（本章覆盖本栏，但本栏那一节"
                     "尚未被单独定位——用前需在本节内再定位）")
    elif fit_reason == "ancestor_keys_unavailable":
        lines.append("原文可达，但本表**派生不出本栏的祖先层键**（本栏不在读入的冻结 "
                     "Contract 里），因此无法按现行规则判定它是否属于本栏的父章节"
                     "——不得据此声称本栏事实")
    else:
        lines.append("原文可达，但所在章节与本栏声明键无整段相等关系"
                     "（结构上不属于本栏，不得据此声称本栏事实）")
    if row.get("text_status") == "resolved":
        lines.append(f"原文可逐字核对（{row.get('text_chars')} 字）")
    else:
        lines.append(f"原文**回查不到**（`{row.get('text_status')}`）——"
                     "这不等于「原文为空」，也不等于「没有这条材料」")
    return tuple(lines)


def _cannot_prove(row: Mapping, fit_reason: str) -> tuple[str, ...]:
    """这一份**不能**证明什么（同上：只写登记过的字段与 typed 读数）。"""
    lines: list[str] = []
    qualification = row.get("content_qualification") or {}
    kind = str(qualification.get("kind", "") or "")
    if kind == "selection_form":
        selection = qualification.get("selection") or {}
        node = selection.get("node") or {}
        lines.append("**不得**当作本栏业务事实正文：它是披露事项的适用状态行，"
                     "不承载金额 / 占比 / 客户名称这类事实")
        if selection.get("asked_item"):
            lines.append(f"它只对应所问事项 `{selection.get('asked_item')}`"
                         f"（所在节点 `{node.get('section_path')}`）")
        exclusions = selection.get("exclusions") or ()
        if exclusions:
            lines.append("登记过的排除项：" + "、".join(f"`{x}`" for x in exclusions))
    elif not qualification.get("kind_in_vocabulary"):
        lines.append("形态读不懂 → 不得当业务正文（也不按 `text` 读）")
    disposition = row.get("disposition") or {}
    if disposition.get("admission_state") == "admitted" \
            and disposition.get("retention_state") != "retained":
        lines.append(f"Pack 侧**未保留**（`{disposition.get('retention_state')}`）")
    if row.get("in_writer_manifest") is False:
        lines.append("不在本节的 Writer 精确材料清单里（取到 ≠ 被 Writer 消费）")
    if row.get("in_writer_manifest") is None:
        lines.append(f"是否进 Writer 清单**不可判定**"
                     f"（`{row.get('writer_manifest_query')}`）——`null` 不是 `false`")
    if row.get("document_identity_basis") != "source_set_member_same_id_and_version":
        lines.append("文档身份的第四轴（证据集版本）不可判定"
                     f"（`{row.get('document_identity_basis')}`）——"
                     "此时 `evidence_set_version` 是 `null` 而不是猜测值")
    identity = row.get("document_identity") or {}
    if not identity.get("company_id"):
        lines.append("身份的 `company_id` 取不到（如实留空，不猜）")
    if fit_reason == "no_structural_key":
        lines.append("它所在章节的完整标签段**既不等于**本栏本层键、**也不等于**本栏祖先层键"
                     "——这是「共享父节点 / 兄弟栏目字段名被当成父节点」的误召形状，"
                     "不得据此声称本栏事实")
    if fit_reason == "locator_unavailable":
        lines.append("定位符读不出章节路径 → **不足以**判定它属于哪一栏"
                     "（不是「不属于本栏」的结论）")
    if fit_reason == "ancestor_keys_unavailable":
        lines.append("祖先层键派生不出来 → **不足以**判定（这是「无从判定」，"
                     "**不是**「不属于本栏」；也**不得**用本轮运行记录的规则形态那套键顶替，"
                     "那会把「没判过」写成「按现行规则判过了」）")
    return tuple(lines)


def _four_columns(row: Mapping, *, fit: str, fit_reason: str) -> dict:
    """一份材料在**四条互不推出**的轴上的读数（照抄 + 形态，**不判语义**）。

    这四列刻意分开：`相关／弱相关／误召`（第①列）只是**章节标题的标签段**对不对得上，
    与「原文形态能不能进正文」（第②列）、「能不能承载事实候选」（第③列）、
    「有没有真的被写作采用」（第④列）是四件不同的事。把它们并成一列，
    就会出现「标题不叫『采购模式』⇒ 说这一栏没有材料」这种把结构读数当语义资格的错读；
    反过来也会出现「标题对得上 ⇒ 以为这句话有用」的错读。

    **本函数不判蕴含**：第③列的 `fact_candidate_eligible` 只说「取材上可以提候选」，
    过不过绑定门 / 蕴含门是 Writer / Evaluator 的事，这里一个字都不替它判。
    第②列的语义那一半恒为 `not_judged_here`——要判「原文是否在讲本栏那一件事」，
    只能逐字读原文，不是这张表能给的。
    """
    qualification = row.get("content_qualification") or {}
    kind = str(qualification.get("kind", "") or "")
    kind_readable = bool(qualification.get("kind_in_vocabulary"))
    text_resolved = row.get("text_status") == "resolved"
    disposition = row.get("disposition")
    has_disposition = isinstance(disposition, Mapping)
    admission = str((disposition or {}).get("admission_state") or "") \
        if has_disposition else ""
    retention = str((disposition or {}).get("retention_state") or "") \
        if has_disposition else ""
    admitted = admission == "admitted"
    retained = retention == "retained"

    # ---- ② 正文相关性（只读形态与原文可回查性） ------------------------------------
    if not has_disposition:
        body_value, body_reason = "no_disposition", "缺处置记录：取材义务未履行，形态读数不单独成立"
    elif not kind_readable:
        body_value, body_reason = "kind_unreadable", f"内容形态读不懂（`{kind}`）"
    elif kind == "selection_form":
        body_value, body_reason = ("disclosure_state_only",
                                   "内容形态是勾选表单行：只承载披露事项的适用状态")
    elif kind != "text":
        body_value, body_reason = ("other_form",
                                   f"内容形态是 `{kind}`：形态上不是散文")
    elif not text_resolved:
        body_value, body_reason = ("text_unreadable",
                                   f"原文**回查不到**（`{row.get('text_status')}`）——"
                                   "这不等于「原文为空」")
    else:
        body_value, body_reason = ("narrative_prose",
                                   f"内容形态是 `text` 且原文可逐字回查"
                                   f"（{row.get('text_chars')} 字）")

    # ---- ③ 事实资格（Pack 侧处置 × 形态的合取） ------------------------------------
    if not has_disposition:
        fact_value, fact_reason = "no_disposition", "缺处置记录：不得据此声称任何事实"
    elif not admitted:
        fact_value, fact_reason = ("not_admitted",
                                   f"Pack 侧处置不是 `admitted`（`{admission}`"
                                   f"，理由码 `{(disposition or {}).get('reason_code')}`）")
    elif not kind_readable:
        fact_value, fact_reason = ("kind_unreadable",
                                   f"内容形态读不懂（`{kind}`）→ 不得当业务正文")
    elif kind == "selection_form":
        fact_value, fact_reason = ("disclosure_state_only",
                                   "只能在**勾选表单行所问事项**的范围内说话："
                                   "披露事项的**适用状态**")
    elif kind != "text":
        fact_value, fact_reason = ("other_form",
                                   f"内容形态是 `{kind}`：不是散文，不得当业务事实正文")
    elif not retained:
        fact_value, fact_reason = ("not_retained",
                                   f"Pack 侧**未保留**（`{retention}`）")
    elif not text_resolved:
        fact_value, fact_reason = ("text_unreadable",
                                   f"原文回查不到（`{row.get('text_status')}`）")
    else:
        fact_value, fact_reason = ("fact_candidate_eligible",
                                   "已接纳 + 已保留 + 形态可读 + 原文可回查 ⇒ "
                                   "**可作事实候选的取材**（仍须过绑定门与蕴含门，"
                                   "本表不判）")

    # ---- ④ 实际写作采用（照抄；`null` 不是 `false`） --------------------------------
    in_manifest = row.get("in_writer_manifest")
    if in_manifest is True:
        adopt_value = "in_writer_manifest"
        adopt_reason = "确在本节的 Writer 精确材料清单里"
    elif in_manifest is False:
        adopt_value = "not_in_writer_manifest"
        adopt_reason = "确不在本节的 Writer 精确材料清单里（取到 ≠ 被 Writer 消费）"
    else:
        adopt_value = "manifest_unreadable"
        adopt_reason = (f"**不可判定**（`{row.get('writer_manifest_query')}`）"
                        "——`null` 不是 `false`：本轮可能根本没有可核对的清单")

    return {
        "structural_match": {
            "value": fit, "reason": fit_reason, "basis": STRUCTURAL_MATCH_BASIS,
            "note": "只判「所在章节标题的完整标签段是否整段等于本栏本层 / 祖先层声明键」；"
                    "判 `误召` 时**不是**「内容与本栏无关」。"},
        "body_relevance": {
            "value": body_value, "reason": body_reason, "basis": "content_form_only",
            "semantic_relevance": BODY_RELEVANCE_SEMANTIC_FIELD,
            "note": "「形态上是散文」**不等于**「原文在讲本栏那一件事」；"
                    "后者只能逐字读原文得出，本表不判。"},
        "fact_eligibility": {
            "value": fact_value, "reason": fact_reason,
            "basis": "pack_disposition_x_content_form",
            "note": "只说能不能承载**事实候选的取材**；绑定与蕴含由 "
                    "`ClaimBindingDecision` / `ClaimEntailmentDecision` 判，本表不判。"},
        "writing_adoption": {
            "value": adopt_value, "reason": adopt_reason,
            "basis": "writer_manifest_membership",
            "note": "`manifest_unreadable` 是**不可判定**，不是「没进」。"},
    }


def _material_row(row: Mapping, *, own_keys: tuple[str, ...],
                  ancestor_keys_current: tuple[str, ...],
                  ancestor_keys_run: tuple[str, ...], section_id: str,
                  ancestor_current_available: bool = True) -> dict:
    """一条材料在本栏目下的读数（**照抄**读回载荷，只加结构契合判定）。"""
    locator = row.get("locator") or {}
    titles, locator_reason = _section_title_samples(locator)
    current = _classify(row, titles, locator_reason, own_keys, ancestor_keys_current,
                        ancestor_current_available)
    run_rule = _classify(row, titles, locator_reason, own_keys, ancestor_keys_run)
    four = _four_columns(row, fit=current["fit"], fit_reason=current["fit_reason"])
    identity = row.get("document_identity") or {}
    disposition = row.get("disposition") or {}
    qualification = row.get("content_qualification") or {}
    return {
        "section_id": section_id,
        "material_id": row.get("material_id"),
        "material_type": row.get("material_type"),
        "content_hash": row.get("content_hash"),
        "bound_aspect_ids": list(row.get("aspect_ids_from_aspect_results") or ()),
        "document_id": identity.get("document_id"),
        "document_version": identity.get("document_version"),
        "evidence_set_version": identity.get("evidence_set_version"),
        "document_identity_basis": row.get("document_identity_basis"),
        "locator": locator,
        "page": locator.get("page"),
        "block_range": locator.get("block_range"),
        "offset": locator.get("offset"),
        "section_path": locator.get("section_path"),
        "table_title": locator.get("table_title"),
        "title_samples": list(titles),
        "locator_reason": locator_reason or None,
        "admission_state": disposition.get("admission_state"),
        "retention_state": disposition.get("retention_state"),
        "source_validation": disposition.get("source_validation"),
        "reason_code": disposition.get("reason_code"),
        "reason_proof": disposition.get("reason_proof"),
        "retention_destination": row.get("retention_destination"),
        "in_writer_manifest": row.get("in_writer_manifest"),
        "writer_manifest_query": row.get("writer_manifest_query"),
        "content_kind": qualification.get("kind"),
        "content_kind_in_vocabulary": bool(qualification.get("kind_in_vocabulary")),
        "text_status": row.get("text_status"),
        "text_chars": row.get("text_chars"),
        "text": row.get("text"),
        "fit": current["fit"], "fit_reason": current["fit_reason"],
        "own_key_hits": current["own_key_hits"],
        "ancestor_key_hits": current["ancestor_key_hits"],
        "fit_run_rule": run_rule["fit"],
        "fit_run_rule_reason": run_rule["fit_reason"],
        "fit_changed_by_rule_version": current["fit"] != run_rule["fit"],
        #: 四条**互不推出**的轴：结构匹配 / 正文相关性 / 事实资格 / 实际写作采用。
        "structural_match": four["structural_match"],
        "body_relevance": four["body_relevance"],
        "fact_eligibility": four["fact_eligibility"],
        "writing_adoption": four["writing_adoption"],
        "can_prove": list(_can_prove(row, current["fit_reason"])),
        "cannot_prove": list(_cannot_prove(row, current["fit_reason"])),
    }


def _column_gap(rows: list[dict], navigation: Mapping | None,
                section_state: str, section_detail: str,
                declaration: Mapping) -> dict | None:
    """本栏的**缺口读数**（没有缺口就是 `None`；只写登记过的事实，不给业务结论）。"""
    kinds: list[str] = []
    facts: list[str] = []
    if section_state != "readback":
        kinds.append("pack_section_unavailable")
        facts.append(f"本节的材料包读回状态是 `{section_state}`"
                     + (f"（`{section_detail}`）" if section_detail else "")
                     + "——这是**读不回来**，不是「本节没有材料」，"
                       "本栏没有任何可以据以判定的读数")
    outcomes = (navigation or {}).get("outcomes") or ()
    fallback = [o for o in outcomes if o.get("status") == "fallback"]
    if outcomes and len(fallback) == len(outcomes):
        kinds.append("navigation_fallback")
        reasons: list[str] = []
        for outcome in fallback:
            reason = str(outcome.get("fallback_reason") or "")
            if reason and reason not in reasons:
                reasons.append(reason)
        facts.append("导航终态全部是 fallback：" + "、".join(f"`{r}`" for r in reasons))
    admitted = [r for r in rows if r["admission_state"] == "admitted"]
    on_topic = [r for r in rows if r["fit"] == "相关"]
    if not rows:
        kinds.append("no_material")
        facts.append("本栏一条材料都没有")
    elif not admitted:
        kinds.append("all_rejected")
        facts.append(f"取了 {len(rows)} 条材料，但**全部**被 Pack 侧拒绝")
    else:
        if not on_topic:
            kinds.append("no_on_topic_material")
            facts.append(f"{len(admitted)} 条已接纳材料中**没有一条**的所在章节"
                         "整段等于本栏本层声明键（"
                         + "、".join(f"`{k}`" for k in (declaration.get("own_keys") or ()))
                         + "）——本栏没有本层取材")
        unresolved = [r for r in admitted if r["text_status"] != "resolved"]
        if unresolved:
            kinds.append("text_unavailable")
            facts.append(f"{len(unresolved)} 条已接纳材料的原文回查不到"
                         "（读不出 ≠ 原文为空）")
    if not kinds:
        return None
    if declaration.get("missing_policy"):
        facts.append(f"Contract `missing_policy={declaration['missing_policy']}`"
                     + (f"；`content_role={declaration['content_role']}`"
                        if declaration.get("content_role") else ""))
    return {"kinds": kinds, "facts": facts}


# --------------------------------------------------------------------------------------
# 构建
# --------------------------------------------------------------------------------------

def build(run_dir: Path, *, repo: Path | None = None,
          contract_asset: Path | None = None) -> dict:
    """由一次运行的产物构建逐栏目契合表（**只读**：不重读 Pack、不调 LLM、不写库）。"""
    run_dir = Path(run_dir)
    repo = Path(repo) if repo is not None else Path(__file__).resolve().parent.parent
    asset = Path(contract_asset) if contract_asset is not None \
        else repo / DEFAULT_CONTRACT_ASSET

    pack, pack_reason = _load_json(run_dir / "material_pack.json")
    report, report_reason = _load_json(run_dir / "acceptance_report.json")
    manifest, manifest_reason = _load_json(run_dir / "manifest.json")
    source_manifest, source_reason = _source_manifest(run_dir / "source_manifest.json")

    notes = [
        "本表是**只读视图**：字段全部来自同一次材料包读回、来源清单与验收报告的导航读数；"
        "本模块不重读 Pack、不调 LLM、不联网、不写库。",
        "档位是**取材契合度**（章节标题的完整标签段是否整段等于本栏本层 / 祖先层声明键），"
        "**不是**内容质量分，也不替 Writer / Evaluator 的绑定与蕴含门说话。"
        "四个结构性档位：`相关`=本栏本层可取材；`弱相关`=仅父章节整章（用前需在本节内再定位）；"
        "`误召`=章节读得出、但结构上不足以声称属于本栏；`祖先层不可判定`=本栏不在冻结 "
        "Contract 里、祖先层键无从派生（详见下面那条）。另有三个「非结构性」读数，"
        "表示结构判定**根本没做**：`被拒绝` / `无处置记录` / `定位不可判定`；"
        "缺口另见每栏 `gap` 块。",
        "**四列互不推出**（`material-column-audit/3`）：每份材料另给 `structural_match`（①结构"
        "匹配）/ `body_relevance`（②正文相关性）/ `fact_eligibility`（③事实资格）/ "
        "`writing_adoption`（④实际写作采用）四条轴的读数，逐栏另有 `four_column_counts`。"
        "**四条轴必须分开读**：①只说章节标题的标签段对不对得上——判 `误召` **不是**「内容与"
        "本栏无关」，判 `相关` 也**不是**「这句话有用」；②**只读形态**，其语义那一半恒为 "
        "`not_judged_here`——「形态上是散文」推不出「原文在讲本栏那一件事」；③是 Pack 处置 × "
        "形态的合取，只说「取材上可不可以提候选」，**不判**绑定与蕴含；④照抄 Writer 精确材料"
        "清单的成员关系，`manifest_unreadable` 是**不可判定**（`null` 不是 `false`）。"
        "把①当②用，就会出现「标题不叫『采购模式』⇒ 说这一栏没有材料」这种错读。",
        "**这个判据是不对称的，读数必须按不对称读**：判 `相关`/`弱相关` 时，"
        "章节标题整段等于本栏声明键——那是一条**足够**的结构依据；判 `误召` 时只说明"
        "「标题与声明键没有整段相等关系」，**不是**「内容与本栏无关」——披露章节常按主题"
        "命名（如「行业发展状况及发展趋势」「经营模式」），而不是按所问项命名，"
        "所以 `误召` 是**下界**读数：它说「不得据此声称本栏事实」，不说「这份材料没用」。",
        "`定位不可判定` 与 `误召` 不同：前者是**读不出**定位符，后者是**读得出、且不属于本栏**。",
        "`祖先层不可判定` 是第三类下界读数：本栏**不在读入的冻结 Contract 里**，"
        "祖先层键无从派生，因此「是否属于本栏父章节」根本没判过。它**不得**与 `误召` 合并，"
        "更**不得**用「本次运行记录的规则形态」那套键顶替——那会把「没判过」写成"
        "「按现行规则判过了」（`anp-6` 的兄弟项排除会在最需要看见的地方消失）。"
        "出现这一档即说明本次运行的 aspect 集合与冻结 Contract 不一致，须先澄清 Contract 身份。",
        "结构契合同一条判据：`键 ∈ title_label_segments(路径段)`，与导航的 "
        "`NavigationIndex.key_segment_forms` 完全一致；差别只在输入——本表用的是材料定位符里的"
        "**章节路径段**，导航用的是标题树的**节点标题**。因此本表不声称任何导航读集，"
        "也没有第二套匹配实现。",
        "报告里的 `tree_navigation` 行**不带文档身份**，同一 aspect 按「逐节 × 逐文档」重复出现；"
        "本表按 `aspect_id` 合并，`observation_count` 是出现次数，**不是**文档数。",
        "**导航读集与材料绑定是两条轴**：材料记在某栏下来自该栏的 aspect 结果，"
        "不是来自导航读集；因此本表不用「导航读到没有」判材料对题与否。",
        "注册了 N 份文档 ≠ 消费了 N 份：`documents` 块逐份给出材料数与接纳数，"
        "某一份若 `admitted=0`，那是**登记了但没取到可用材料**。",
        "`no_material`（一条都没有）与 `pack_section_unavailable`（本节读回没读成）是两件事，"
        "不得互相冒充。",
        "**三条轴，互不推出**（`material-column-audit/2` 起）：①取材契合（`materials[].fit`，"
        "结构上能不能从这一栏取材）；②本栏缺口（`gap`，取材侧的下界读数）；"
        "③**Contract 完成规则**（`completion`，**照抄** Pack 侧逐 aspect 的 typed 缺口行）。"
        "①有原文**推不出**③已满足：采购/生产能读到原文 ≠ 该栏完整；一句「以自主研发为主、"
        "外部合作为辅的研发模式」不等于 `tech_route` 已满足。③的三个档位里**没有**「已满足」："
        "`typed_gap_present`＝Pack 侧确有 typed 缺口行；`no_typed_gap_read`＝本轮产物里没有"
        "可读的缺口行 ⇒ **无从判定**（不是「过了」）；`completion_unreadable`＝这一轴没做成。"
        "本表**不判**完成规则，只保证「Pack 写了什么，这里逐字是什么」。",
    ]
    if pack_reason:
        notes.append(f"材料包读回：`{pack_reason}`")
    if report_reason:
        notes.append(f"验收报告：`{report_reason}`（导航侧读数缺失）")
    if manifest_reason:
        notes.append(f"运行 manifest：`{manifest_reason}`（section→topic 归属缺失）")
    if source_reason:
        notes.append(f"来源清单：`{source_reason}`（登记侧读数缺失）")

    if not isinstance(pack, Mapping):
        return {
            "schema_version": COLUMN_AUDIT_SCHEMA_VERSION,
            "status": "unavailable", "run_dir": str(run_dir),
            "reason": pack_reason or "material_pack_not_a_mapping",
            "notes": notes + [
                "材料包读回载荷读不出来 → 本表**不产出**逐栏目读数。"
                "这不等于「本轮没有材料」：读不回来与查不到是两件事。"],
            "fit_labels": list(FIT_LABELS), "fit_reasons": list(FIT_REASONS),
            "body_relevance_values": list(BODY_RELEVANCE_VALUES),
            "body_relevance_semantic_field": BODY_RELEVANCE_SEMANTIC_FIELD,
            "fact_eligibility_values": list(FACT_ELIGIBILITY_VALUES),
            "writing_adoption_values": list(WRITING_ADOPTION_VALUES),
            "structural_match_basis": STRUCTURAL_MATCH_BASIS,
            "gap_kinds": list(GAP_KINDS), "key_sources": list(KEY_SOURCES),
            "completion_kinds": list(COMPLETION_KINDS),
            "completion_read_sources": list(COMPLETION_READ_SOURCES),
            "completion_read_reason": "completion_unavailable:material_pack_not_readable",
        }

    contract = _contract_context(asset)
    topics, recorded_contract = _section_topics(manifest)
    navigation, nav_reason = _navigation_rows(report)
    report_declarations = _report_declarations(report)
    pack_completion, completion_reason = _pack_completion_rows(report)
    if completion_reason:
        notes.append(f"第三轴（Contract 完成规则）：`{completion_reason}`"
                     "——这一轴本轮**没做成**，不得读成「本栏完成规则已满足」。")
    run_rule_versions = sorted({version for row in navigation.values()
                                for version in row["rule_versions"]})
    if nav_reason:
        notes.append(f"导航侧：`{nav_reason}`")
    if run_rule_versions:
        notes.append(
            "本次运行的逐节导航读数记录的规则版本："
            + "、".join(f"`{v}`" for v in run_rule_versions)
            + f"；本表的 `fit` 档位按现行规则 `{contract['rule_version_current']}` 判，"
              "`fit_run_rule` 按「本次运行记录的规则形态」判（两套祖先层键都逐栏写出）。")
    if not contract["loaded"]:
        notes.append(f"Contract 未能读入（`{contract['error']}`）→ 声明标签与键只能来自"
                     "验收报告里已有的读数；缺的部分如实留空。")
    if contract["loaded"] and recorded_contract:
        recorded_version = recorded_contract.get("version")
        if recorded_version and str(recorded_version) != str(contract["contract_version"]):
            notes.append(
                f"**Contract 版本不一致**：本次运行记录 `{recorded_version}`，"
                f"本表读入 `{contract['contract_version']}`（`{asset}`）"
                "——下面的声明标签与键都按后者，读数须按此打折。")
        else:
            notes.append(
                f"本次运行记录的 Contract 身份：version `{recorded_version}` / "
                f"fingerprint `{recorded_contract.get('fingerprint')}`；本表读入 "
                f"`{asset}`（version `{contract['contract_version']}`）。"
                "指纹未独立重算，因此身份一致性由下面 `contract_agreement` 的"
                "**逐 aspect 键比对**间接给出，而不是由本表声称。")

    # ---- 逐份文档：登记侧（来源清单）与消费侧（材料包）对账 ----------------------------
    consumed: dict[tuple[str, str], dict] = {}
    per_aspect: dict[str, list[dict]] = {}
    unbound: list[dict] = []
    sections: dict[str, dict] = {}
    for section_id in SECTION_ORDER:
        block = (pack.get("sections") or {}).get(section_id)
        if not isinstance(block, Mapping):
            sections[section_id] = {"status": "absent", "detail": "材料包里没有这一节的块",
                                    "entry_count": 0}
            continue
        sections[section_id] = {
            "status": str(block.get("status") or "unavailable"),
            "detail": str(block.get("detail") or ""),
            "entry_count": int(block.get("entry_count") or 0)}
        if sections[section_id]["status"] != "readback":
            continue
        for row in block.get("entries") or ():
            if not isinstance(row, Mapping):
                continue
            identity = row.get("document_identity") or {}
            key = (str(identity.get("document_id") or ""),
                   str(identity.get("document_version") or ""))
            stat = consumed.setdefault(key, {
                "document_id": key[0], "document_version": key[1],
                "material_count": 0, "admitted": 0, "rejected": 0, "retained": 0,
                "in_writer_manifest_true": 0, "writer_manifest_checked": 0,
                "pages": [], "sections": [], "content_kinds": {},
                "identity_four_axes_known": 0, "evidence_set_versions": []})
            stat["material_count"] += 1
            disposition = row.get("disposition") or {}
            if disposition.get("admission_state") == "admitted":
                stat["admitted"] += 1
            if disposition.get("admission_state") == "rejected":
                stat["rejected"] += 1
            if disposition.get("retention_state") == "retained":
                stat["retained"] += 1
            if row.get("in_writer_manifest") is True:
                stat["in_writer_manifest_true"] += 1
            if row.get("writer_manifest_query") == "checked":
                stat["writer_manifest_checked"] += 1
            page = (row.get("locator") or {}).get("page")
            if isinstance(page, int) and page not in stat["pages"]:
                stat["pages"].append(page)
            if section_id not in stat["sections"]:
                stat["sections"].append(section_id)
            kind = str((row.get("content_qualification") or {}).get("kind", "")
                       or "unavailable")
            stat["content_kinds"][kind] = stat["content_kinds"].get(kind, 0) + 1
            if row.get("document_identity_basis") == \
                    "source_set_member_same_id_and_version":
                stat["identity_four_axes_known"] += 1
            set_version = identity.get("evidence_set_version")
            if set_version is not None and set_version not in stat["evidence_set_versions"]:
                stat["evidence_set_versions"].append(set_version)
            aspects: list[str] = []
            for aspect_id in (row.get("aspect_ids_from_aspect_results") or ()):
                if str(aspect_id) not in aspects:
                    aspects.append(str(aspect_id))
            for aspect_id in (disposition.get("aspect_ids") or ()):
                if str(aspect_id) not in aspects:
                    aspects.append(str(aspect_id))
            if not aspects:
                unbound.append({"material_id": row.get("material_id"),
                                "section_id": section_id,
                                "topic_id": row.get("topic_id"),
                                "page": page,
                                "reason": "material_without_aspect_binding"})
                continue
            for aspect_id in aspects:
                per_aspect.setdefault(aspect_id, []).append(
                    {"section_id": section_id, "entry": row})

    # ---- 逐份文档的登记 / 消费对账 ---------------------------------------------------
    document_rows: list[dict] = []
    registered_keys: set[tuple[str, str]] = set()
    for doc in source_manifest.get("documents") or ():
        key = (doc["document_id"], doc["document_version"])
        registered_keys.add(key)
        stat = consumed.get(key, {})
        material_count = int(stat.get("material_count") or 0)
        admitted = int(stat.get("admitted") or 0)
        if material_count == 0:
            state = "registered_not_consumed"
        elif admitted == 0:
            state = "consumed_all_rejected"
        else:
            state = "consumed"
        pages = sorted(stat.get("pages") or [])
        document_rows.append({
            **doc,
            "primary": (doc["document_id"] == source_manifest.get("primary_document_id")
                        and doc["document_version"]
                        == source_manifest.get("primary_document_version")),
            "material_count": material_count,
            "admitted": admitted,
            "rejected": int(stat.get("rejected") or 0),
            "retained": int(stat.get("retained") or 0),
            "in_writer_manifest_true": int(stat.get("in_writer_manifest_true") or 0),
            "writer_manifest_checked": int(stat.get("writer_manifest_checked") or 0),
            "identity_four_axes_known": int(stat.get("identity_four_axes_known") or 0),
            "pages": pages,
            "page_span": (f"{pages[0]}–{pages[-1]}" if pages else None),
            "sections": sorted(stat.get("sections") or []),
            "content_kinds": dict(sorted((stat.get("content_kinds") or {}).items())),
            "consumption_state": state,
            "evidence_set_version_consumed": stat.get("evidence_set_versions") or [],
        })
    for key, stat in sorted(consumed.items()):
        if key in registered_keys:
            continue
        document_rows.append({
            "document_id": key[0], "document_version": key[1], "primary": False,
            "source_name": None, "registered_source_type": None,
            "identified_document_class": None,
            "evidence_set_version": None, "eligibility": None,
            "content_report_period": None, "content_report_period_state": None,
            "source_role": None, "retrieval_order": None, "selection_state": None,
            "selection_reason_code": None,
            "material_count": stat["material_count"], "admitted": stat["admitted"],
            "rejected": stat["rejected"], "retained": stat["retained"],
            "in_writer_manifest_true": stat["in_writer_manifest_true"],
            "writer_manifest_checked": stat["writer_manifest_checked"],
            "identity_four_axes_known": stat["identity_four_axes_known"],
            "pages": sorted(stat["pages"]), "page_span": None,
            "sections": sorted(stat["sections"]),
            "content_kinds": dict(sorted(stat["content_kinds"].items())),
            "consumption_state": "unregistered_material_source",
            "evidence_set_version_consumed": stat["evidence_set_versions"],
        })
    consumed_document_count = sum(
        1 for d in document_rows if d["material_count"] > 0)

    # ---- 逐栏目 ---------------------------------------------------------------------
    def _declaration_for(aspect_id: str) -> dict:
        declared = dict(contract["aspects"].get(aspect_id) or {})
        fallback = report_declarations.get(aspect_id) or {}
        source = ("contract_and_run_report" if declared and fallback
                  else "contract" if declared
                  else "run_acceptance_report" if fallback else "none")
        for name in ("topic_id", "question_id", "requirement_text", "kind",
                     "content_role", "output_destination", "missing_policy",
                     "time_scope"):
            if not declared.get(name) and fallback.get(name):
                declared[name] = fallback[name]
        declared["aspect_id"] = aspect_id
        declared["declaration_source"] = source
        return declared

    def _keys_for(aspect_id: str, nav: Mapping | None, declared: Mapping,
                  contract_declared: bool) -> dict:
        """本栏的两套键：本层键与两套祖先层键（现行规则 / 本次运行记录的规则）。

        本层键优先取**本次运行的读数**（`nav_keys`），缺失时按现行规则从 Contract 派生；
        祖先层键的"本次运行记录"一套优先取运行读数（`parent_keys`），缺失时按 `anp-5` 形态
        派生。**两套键都写出来**，判据用了哪一套由 `verdict_rule_version` 说明。

        现行规则的祖先层键**只**由冻结 Contract 派生。契约里没有这一栏时它是 `None`
        （`ancestor_current_rule_available=False`），而不是空集合——空集合是「这一栏确实
        没有祖先层键」（一条可信的否定读数），`None` 是「派生不出来」。两者混同会让
        `anp-6` 的兄弟项排除效果在最需要看见的地方悄悄退回运行记录形态。
        """
        if nav and nav.get("nav_keys"):
            own = tuple(str(x) for x in nav["nav_keys"])
            own_source = "run_tree_navigation"
        elif declared.get("own_keys"):
            own = tuple(str(x) for x in declared["own_keys"])
            own_source = "contract_derivation_current"
        else:
            own, own_source = (), "unavailable"
        if contract_declared:
            current = tuple(str(x) for x in
                            (declared.get("ancestor_keys_current_rule") or ()))
            current_available = True
        else:
            current, current_available = (), False
        run_form = tuple(str(x) for x in (declared.get("ancestor_keys_run_rule") or ()))
        if nav and nav.get("parent_keys"):
            ancestor_run = tuple(str(x) for x in nav["parent_keys"])
            ancestor_run_source = "run_tree_navigation"
        else:
            ancestor_run = run_form
            ancestor_run_source = ("contract_derivation_run_rule_form"
                                   if run_form else "unavailable")
        return {"own": list(own), "own_source": own_source,
                "ancestor_current_rule": list(current),
                "ancestor_current_rule_available": current_available,
                "ancestor_run_rule": list(ancestor_run),
                "ancestor_run_rule_source": ancestor_run_source,
                "ancestor_texts": list(declared.get("ancestor_texts") or ()),
                "sibling_exclusion_keys": list(
                    declared.get("sibling_exclusion_keys") or ())}

    candidate_ids = sorted(set(per_aspect) | set(navigation) | set(contract["aspects"]))
    columns: list[dict] = []
    excluded: list[str] = []
    for aspect_id in candidate_ids:
        declared = _declaration_for(aspect_id)
        topic_id = str(declared.get("topic_id") or "")
        nav = navigation.get(aspect_id)
        if nav and not topic_id:
            topic_id = str(nav.get("topic_id") or "")
        section_id = topics.get(topic_id)
        section_state = (sections.get(section_id, {}).get("status")
                         if section_id else "unknown")
        has_material = aspect_id in per_aspect
        #: 收栏条件（任一成立即收），三条都指向"本轮**计划要回答**这一栏"：
        #:   (a) 这一栏有材料；或
        #:   (b) 这一栏有导航读数（本轮真的为它跑过导航）；或
        #:   (c) 它的 topic 属于本轮 manifest 声明的 topic（哪怕这一栏一条材料都没有——
        #:       「取到 0 条」正是最该被逐栏看到的缺口，不能靠不收栏来抹掉）。
        #: `section_state` 不是 `readback` 的节同样按 (c) 收：那一节"没有材料"是读不回来。
        if not (has_material or nav or topic_id in topics):
            excluded.append(aspect_id)
            continue
        keys = _keys_for(aspect_id, nav, declared,
                         aspect_id in contract["aspects"])
        station = sections.get(section_id) if section_id else None
        rows = [
            _material_row(item["entry"], own_keys=tuple(keys["own"]),
                          ancestor_keys_current=tuple(keys["ancestor_current_rule"]),
                          ancestor_keys_run=tuple(keys["ancestor_run_rule"]),
                          section_id=item["section_id"],
                          ancestor_current_available=bool(
                              keys["ancestor_current_rule_available"]))
            for item in per_aspect.get(aspect_id, [])]
        counts = {label: 0 for label in FIT_LABELS}
        for row in rows:
            counts[row["fit"]] += 1
        run_rule_counts = {label: 0 for label in FIT_LABELS}
        for row in rows:
            run_rule_counts[row["fit_run_rule"]] += 1
        #: 四列各自的逐栏计数（闭集全键在册，缺的在页面上读作 0）。
        four_counts = {
            "structural_match": {label: 0 for label in FIT_LABELS},
            "body_relevance": {value: 0 for value in BODY_RELEVANCE_VALUES},
            "fact_eligibility": {value: 0 for value in FACT_ELIGIBILITY_VALUES},
            "writing_adoption": {value: 0 for value in WRITING_ADOPTION_VALUES},
        }
        for row in rows:
            four_counts["structural_match"][row["structural_match"]["value"]] += 1
            four_counts["body_relevance"][row["body_relevance"]["value"]] += 1
            four_counts["fact_eligibility"][row["fact_eligibility"]["value"]] += 1
            four_counts["writing_adoption"][row["writing_adoption"]["value"]] += 1
        gap_declaration = dict(declared)
        gap_declaration.setdefault("own_keys", keys["own"])
        columns.append({
            "aspect_id": aspect_id, "topic_id": topic_id, "section_id": section_id,
            "section_readback_state": section_state,
            "section_readback_detail": (station or {}).get("detail", ""),
            "requirement_text": declared.get("requirement_text", ""),
            "question_id": declared.get("question_id", ""),
            "required_fields": list(declared.get("required_fields") or ()),
            "kind": declared.get("kind", ""),
            "content_role": declared.get("content_role", ""),
            "missing_policy": declared.get("missing_policy", ""),
            "time_scope": declared.get("time_scope", ""),
            "output_destination": declared.get("output_destination", ""),
            "declaration_source": declared.get("declaration_source"),
            "keys": keys,
            "navigation": None if nav is None else {
                "recorded_nav_keys": nav["nav_keys"],
                "recorded_parent_keys": nav["parent_keys"],
                "recorded_tier": nav["recorded_tier"],
                "outcomes": nav["outcomes"],
                "observation_count": nav["observation_count"],
                "rule_versions": nav["rule_versions"],
            },
            "verdict_rule_version": contract["rule_version_current"],
            "materials": rows,
            "counts": counts,
            "counts_run_rule": run_rule_counts,
            "four_column_counts": four_counts,
            "fit_changed_count": sum(1 for r in rows
                                     if r["fit_changed_by_rule_version"]),
            "gap": _column_gap(rows, nav, section_state,
                              (station or {}).get("detail", ""), gap_declaration),
            "completion": _completion_for(aspect_id, pack_completion,
                                          completion_reason),
        })

    # ---- Contract 身份：由逐 aspect 键比对间接给出 ------------------------------------
    agreement = {"checked": 0, "own_matched": 0, "ancestor_run_form_matched": 0,
                 "own_mismatches": [], "ancestor_run_form_mismatches": []}
    for aspect_id, nav in sorted(navigation.items()):
        declared = contract["aspects"].get(aspect_id)
        if declared is None:
            continue
        agreement["checked"] += 1
        if list(nav["nav_keys"]) == list(declared["own_keys"]):
            agreement["own_matched"] += 1
        else:
            agreement["own_mismatches"].append({
                "aspect_id": aspect_id,
                "run_recorded": nav["nav_keys"],
                "derived_from_asset": declared["own_keys"]})
        if set(nav["parent_keys"]) == set(declared["ancestor_keys_run_rule"]):
            agreement["ancestor_run_form_matched"] += 1
        else:
            agreement["ancestor_run_form_mismatches"].append({
                "aspect_id": aspect_id,
                "run_recorded": nav["parent_keys"],
                "derived_from_asset": declared["ancestor_keys_run_rule"]})
    if agreement["checked"]:
        if not agreement["own_mismatches"] and not agreement["ancestor_run_form_mismatches"]:
            notes.append(
                f"Contract 身份（间接）：逐 aspect 键比对 {agreement['checked']}/"
                f"{agreement['checked']} 全等——本层键与「本次运行记录规则形态」的祖先层键"
                "都与本表读入的 Contract 派生的完全一致。"
                "这不是指纹校验，但足以支撑「声明字段用的是同一份 Contract」。")
        else:
            notes.append(
                f"**Contract 键比对出现不一致**：本层键不一致 "
                f"{len(agreement['own_mismatches'])} 处，祖先层键（运行记录形态）不一致 "
                f"{len(agreement['ancestor_run_form_mismatches'])} 处——"
                "明细见 `contract_agreement`。在澄清之前，本表的键与标签读数必须按此打折。")

    # ---- 受控连续阅读视图（跨页碎片，**不重写**原文） --------------------------------
    continuity: list[dict] = []
    #: 一块材料是**一个碎片**，不管它被几栏绑定：先按 `material_id` 去重，再把各栏的绑定并起来。
    #: 否则同一块材料会因绑定多条 aspect 在同一位置出现多次，"相邻页"就永远对不上。
    grouped: dict[tuple[str, str, str], dict[str, dict]] = {}
    for column in columns:
        for row in column["materials"]:
            if row["admission_state"] != "admitted" or row["retention_state"] != "retained":
                continue
            if row["content_kind"] != "text" or row["text_status"] != "resolved":
                continue
            if not isinstance(row["page"], int):
                continue
            key = (str(row["document_id"]), str(row["document_version"]),
                   str(row["section_id"]))
            bucket = grouped.setdefault(key, {})
            fragment = bucket.setdefault(str(row["material_id"]), {
                "material_id": row["material_id"], "page": row["page"],
                "offset": row["offset"], "block_range": row["block_range"],
                "section_path": row["section_path"], "text": row["text"],
                "text_chars": row["text_chars"],
                "column_aspect_ids": set(), "bound_aspect_ids": list(
                    row.get("bound_aspect_ids") or ())})
            fragment["column_aspect_ids"].add(column["aspect_id"])
    for key in sorted(grouped):
        ordered = sorted(grouped[key].values(),
                         key=lambda f: (f["page"], f["offset"] or 0))
        index = 0
        while index + 1 < len(ordered):
            left, right = ordered[index], ordered[index + 1]
            pages_adjacent = right["page"] == left["page"] + 1
            shared = sorted(left["column_aspect_ids"] & right["column_aspect_ids"])
            if pages_adjacent and shared:
                continuity.append({
                    "document_id": key[0], "document_version": key[1],
                    "section_id": key[2], "shared_aspect_ids": shared,
                    "fragments": [
                        {"material_id": f["material_id"], "page": f["page"],
                         "offset": f["offset"], "block_range": f["block_range"],
                         "section_path": f["section_path"], "text": f["text"],
                         "text_chars": f["text_chars"],
                         "column_aspect_ids": sorted(f["column_aspect_ids"]),
                         "aspect_ids_from_aspect_results": f["bound_aspect_ids"]}
                        for f in (left, right)],
                    "rule": ("同文档同节，两块的**页号相差 1** 且至少共享一条 aspect 绑定 ⇒ "
                             "记为一段跨页连续阅读视图。原文逐字保留、**不重写**；"
                             "页边界处只加标记，不拼接、不改写、不补字。"),
                })
                index += 2
            else:
                index += 1

    return {
        "schema_version": COLUMN_AUDIT_SCHEMA_VERSION,
        "status": "readback" if all(
            s["status"] == "readback" for s in sections.values()) else "partial",
        "run_dir": str(run_dir),
        "material_pack_schema_version": pack.get("schema_version"),
        "entry_count": pack.get("entry_count"),
        "contract": {
            "asset": contract["asset"], "loaded": contract["loaded"],
            "error": contract["error"],
            "contract_version": contract["contract_version"],
            "frozen_at": contract["frozen_at"],
            "aspect_count": contract["aspect_count"],
            "rule_version_current": contract["rule_version_current"],
            "run_recorded": recorded_contract,
        },
        "run_recorded_rule_versions": run_rule_versions,
        "contract_agreement": agreement,
        "sections": sections,
        "documents": document_rows,
        "registered_document_count": len(source_manifest.get("documents") or ()),
        "consumed_document_count": consumed_document_count,
        "document_material_total": sum(d["material_count"] for d in document_rows),
        "source_manifest": {
            "policy_version": source_manifest.get("policy_version"),
            "primary_document_id": source_manifest.get("primary_document_id"),
            "primary_document_version": source_manifest.get("primary_document_version"),
            "current_state": source_manifest.get("current_state"),
            "registration_class_mismatches":
                source_manifest.get("registration_class_mismatches") or [],
            "provenance_findings": source_manifest.get("provenance_findings") or [],
        },
        "columns": columns,
        "column_count": len(columns),
        "columns_excluded": excluded,
        "unbound_materials": unbound,
        "continuity_groups": continuity,
        "material_fitness_lines": sorted(_bindings_digest(columns)),
        "fit_labels": list(FIT_LABELS), "fit_reasons": list(FIT_REASONS),
        "body_relevance_values": list(BODY_RELEVANCE_VALUES),
        "body_relevance_semantic_field": BODY_RELEVANCE_SEMANTIC_FIELD,
        "fact_eligibility_values": list(FACT_ELIGIBILITY_VALUES),
        "writing_adoption_values": list(WRITING_ADOPTION_VALUES),
        "structural_match_basis": STRUCTURAL_MATCH_BASIS,
        "gap_kinds": list(GAP_KINDS), "key_sources": list(KEY_SOURCES),
        "completion_kinds": list(COMPLETION_KINDS),
        "completion_read_sources": list(COMPLETION_READ_SOURCES),
        "completion_read_reason": completion_reason,
        "notes": notes,
    }


def _bindings_digest(columns: list[dict]) -> list[str]:
    """一行一条 `材料 × 栏目` 绑定（人读 / diff 用的紧凑指纹）。

    指纹里**同时带四列**：`结构匹配 | 正文相关性 | 事实资格 | 实际写作采用`。
    只带第一列会让 diff 看不出「同一块原文的形态 / 资格 / 采用读数变了」。
    """
    lines: list[str] = []
    for column in columns:
        for row in column["materials"]:
            lines.append(
                f"{column['aspect_id']}|{row['material_id']}|{row['fit']}"
                f"|{row['document_id']}@{row['document_version']}"
                f"|p{row['page']}|{row['content_kind']}|{row['admission_state']}"
                f"|{(row.get('body_relevance') or {}).get('value')}"
                f"|{(row.get('fact_eligibility') or {}).get('value')}"
                f"|{(row.get('writing_adoption') or {}).get('value')}")
    return lines


# --------------------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------------------

def _render_completion(completion: Mapping) -> list[str]:
    """第三轴（Contract 完成规则）的人读块：**照抄** Pack 侧缺口行，不给业务结论。"""
    verdict = str(completion.get("verdict", "") or "")
    head = "- **Contract 完成规则（照抄 Pack 侧 typed 缺口行；本表不判）**："
    if verdict == "typed_gap_present":
        out = [head + f"`typed_gap_present`（{len(completion.get('rows') or ())} 行；"
                      f"码 " + "、".join(f"`{c}`" for c in completion.get("codes") or ())
                      + f"；`blocking=true` {completion.get('blocking_count')} 行）",
               "  - **这一栏的取材轴与完成轴是两条轴**：上面那几行 `fit` / 能证明什么"
               "只说「原文里有没有、能不能取材」，**推不出**这一栏已满足。"]
        for row in completion.get("rows") or ():
            out.append(f"  - `{row.get('detail')}`（`reason_code={row.get('reason_code')}`、"
                       f"`layer_reason={row.get('layer_reason')}`、"
                       f"`blocking={json.dumps(row.get('blocking'), ensure_ascii=False)}`）")
        return out
    if verdict == "no_typed_gap_read":
        return [head + "`no_typed_gap_read`——本轮产物里**没有**这一栏的 typed 覆盖缺口行"
                       "⇒ **无从判定是否满足**（**不是**「已满足」）。"]
    return [head + f"`completion_unreadable`（`{completion.get('read_reason')}`）"
                   "——这一轴本轮**没做成**，不得读成「已满足」。"]


def _render_four_columns(counts: Mapping) -> list[str]:
    """四列逐栏计数的渲染块（**照抄**，不给结论）。

    四列刻意分开印：`相关／弱相关／误召` 只是第①列；`narrative_prose`（第②列）只说
    「形态上是散文」；`fact_candidate_eligible`（第③列）只说「取材上可以提候选」；
    第④列的 `manifest_unreadable` 是**不可判定**，不是「没进」。
    """
    if not counts:
        return []
    spec = (
        ("① 结构匹配", "structural_match", FIT_LABELS,
         "章节标题标签段；判 `误召` **不是**「内容无关」"),
        ("② 正文相关性", "body_relevance", BODY_RELEVANCE_VALUES,
         "**只读形态**；语义相关性 `not_judged_here`——「形态是散文」≠「在讲本栏那一件事」"),
        ("③ 事实资格", "fact_eligibility", FACT_ELIGIBILITY_VALUES,
         "Pack 处置 × 形态的合取；**不判**绑定与蕴含"),
        ("④ 实际写作采用", "writing_adoption", WRITING_ADOPTION_VALUES,
         "`manifest_unreadable` 是**不可判定**，**不是**「没进」"),
    )
    out = ["- 四列（**互不推出**；任何一列都不得顶替另一列）："]
    for label, key, vocabulary, note in spec:
        block = counts.get(key) or {}
        rendered = "、".join(f"`{v}`×{block.get(v, 0)}" for v in vocabulary
                             if block.get(v))
        out.append(f"  - {label}：{rendered or '（本栏无材料）'}；{note}")
    return out


def _page_text(row: Mapping) -> str:
    page = row.get("page")
    block_range = row.get("block_range")
    offset = row.get("offset")
    if page is None:
        return "**页/块读不出**"
    if isinstance(block_range, list) and block_range:
        return f"p{page}（块 {block_range[0]}–{block_range[-1]}，偏移 {offset}）"
    return f"p{page}（偏移 {offset}）"


def _excerpt(text: Any, limit: int = 400) -> str:
    if not isinstance(text, str) or text == "":
        return ""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "……"


def render_md(payload: Mapping) -> str:
    """逐栏目契合表的人读版（**不重新计算**，只换渲染）。"""
    lines = ["# M930-3 逐栏目材料契合表（人工阅读用）", "",
             "机器可读版在 `material_fitness.json`；两者的读数出自同一次读回。", ""]
    for note in payload.get("notes") or ():
        lines.append(f"- {note}")
    lines.append("")
    if payload.get("status") == "unavailable":
        lines += [f"**本表未产出**：`{payload.get('reason')}`", ""]
        return "\n".join(lines) + "\n"

    source = payload.get("source_manifest") or {}
    lines += ["## 一、登记了几份、消费了几份", "",
              f"- 登记来源：**{payload.get('registered_document_count')} 份**；"
              f"实际有材料的：**{payload.get('consumed_document_count')} 份**"
              f"（材料合计 {payload.get('document_material_total')} 条，"
              f"材料包 `entry_count={payload.get('entry_count')}`）。",
              f"- 当前锚（`current_state_source`）：`{source.get('primary_document_id')}"
              f"@{source.get('primary_document_version')}`；"
              f"清单状态 `{source.get('current_state')}`。",
              "", "**登记 ≠ 消费**：`消费状态` 为 `registered_not_consumed` 的那一份是"
              "登记了但一份可用材料都没取到；这不等于「查过了没有」。", "",
              "| 文档 | 版本 | 名称 | 来源角色 | 检索序 | 期间 | 材料 | 接纳 | 拒绝 | "
              "保留 | 进 Writer 清单 | 四轴可判定 | 页范围 | 消费状态 |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | "
              "--- | --- | --- |"]
    for doc in payload.get("documents") or ():
        lines.append(
            f"| `{doc['document_id']}`{'（锚）' if doc.get('primary') else ''} | "
            f"`{doc['document_version']}` | {doc.get('source_name') or '—'} | "
            f"`{doc.get('source_role')}` | {doc.get('retrieval_order')} | "
            f"{doc.get('content_report_period') or '（未知）'} | {doc['material_count']} | "
            f"{doc['admitted']} | {doc['rejected']} | {doc['retained']} | "
            f"{doc['in_writer_manifest_true']}/{doc['writer_manifest_checked']} | "
            f"{doc['identity_four_axes_known']}/{doc['material_count']} | "
            f"{doc.get('page_span') or '（无页号）'} | `{doc['consumption_state']}` |")
    lines += ["",
              "两列读法：**`进 Writer 清单` 是「已计入 / 已核对」**——`0/0` 表示本轮**没有**"
              "Writer 精确材料清单可核对（本轮没有 `SectionDraft`），**不是**「进了 0 条」；"
              "`四轴可判定` 是「身份四轴齐备 / 材料数」。两列都只看分子会读反。", ""]
    if source.get("registration_class_mismatches"):
        lines += ["### 登记类与内容判定类不符（登记侧读数，不是本表的结论）", ""]
        for row in source["registration_class_mismatches"]:
            key = row.get("document_key") or {}
            lines.append(f"- `{key.get('document_id')}`：登记 `{row.get('registered_class')}`"
                         f"、内容判定 `{row.get('content_identified_class')}`；依据 "
                         f"`{row.get('identification_basis')}`")
        lines.append("")
    if source.get("provenance_findings"):
        lines += ["### 来源侧 provenance 读数", ""]
        for text in source["provenance_findings"]:
            lines.append(f"- {text}")
        lines.append("")

    sections = payload.get("sections") or {}
    if any(s.get("status") != "readback" for s in sections.values()):
        lines += ["### 材料包读回不完整的节", "",
                  "| 节 | 读回状态 | 读数 |", "| --- | --- | --- |"]
        for section_id in SECTION_ORDER:
            stat = sections.get(section_id) or {}
            if stat.get("status") == "readback":
                continue
            lines.append(f"| `{section_id}` | `{stat.get('status')}` | "
                         f"{stat.get('detail') or '（无 detail）'} |")
        lines += ["", "这些节的 `entries` 缺失是**读不回来**，不是「没有材料」；"
                  "它们下面的栏目一律带 `pack_section_unavailable` 缺口。", ""]

    unbound = payload.get("unbound_materials") or []
    if unbound:
        lines += [f"### 未绑定 aspect 的材料：{len(unbound)} 条", "",
                  "材料侧义务是逐材料绑定 aspect；这些材料没有记在任何一栏下，",
                  "因此**不进**下面的逐栏目表。列在这里以便复核：", ""]
        for row in unbound:
            lines.append(f"- `{row.get('material_id')}`（节 `{row.get('section_id')}` / "
                         f"topic `{row.get('topic_id')}` / p{row.get('page')}）"
                         f"：`{row.get('reason')}`")
        lines.append("")

    agreement = payload.get("contract_agreement") or {}
    if agreement.get("checked"):
        lines += ["### Contract 身份（间接核对）", "",
                  f"- 逐 aspect 键比对：{agreement['checked']} 条有运行读数；本层键全等 "
                  f"{agreement['own_matched']}/{agreement['checked']}；祖先层键"
                  f"（运行记录形态）全等 "
                  f"{agreement['ancestor_run_form_matched']}/{agreement['checked']}。",
                  "- 这是**派生一致性**，不是指纹校验；不一致明细见 "
                  "`contract_agreement`。", ""]

    lines += ["## 二、逐栏目", ""]
    for column in payload.get("columns") or []:
        title = column.get("requirement_text") or "（Contract 未给 requirement_text）"
        lines += [f"### 栏目 `{column['aspect_id']}`：{title}", ""]
        lines.append(
            f"- 归属：节 `{column.get('section_id') or '（未判定）'}`"
            f" / topic `{column.get('topic_id') or '—'}`"
            f" / question `{column.get('question_id') or '—'}`；声明来源 "
            f"`{column.get('declaration_source')}`")
        if column.get("required_fields"):
            lines.append("- Contract `required_fields`："
                         + "、".join(f"`{x}`" for x in column["required_fields"]))
        if column.get("missing_policy"):
            lines.append(f"- Contract `missing_policy={column['missing_policy']}`；"
                         f"`kind={column.get('kind')}`；"
                         f"`content_role={column.get('content_role')}`；"
                         f"`output_destination={column.get('output_destination')}`；"
                         f"`time_scope={column.get('time_scope')}`")
        keys = column.get("keys") or {}
        run_rule_label = {
            "run_tree_navigation": "本次运行记录",
            "contract_derivation_run_rule_form":
                "按本次运行记录的规则形态派生（`anp-5` 形态：兄弟项排除关闭）",
            "unavailable": "**不可得**",
        }.get(str(keys.get("ancestor_run_rule_source")), "（来源未登记）")
        lines.append(f"- 本层声明键（来源 `{keys.get('own_source')}`）："
                     + ("、".join(f"`{k}`" for k in keys.get("own") or ()) or "（无）"))
        lines.append(f"- 祖先层键 · {run_rule_label}"
                     f"（来源 `{keys.get('ancestor_run_rule_source')}`）："
                     + ("、".join(f"`{k}`" for k in keys.get("ancestor_run_rule") or ())
                        or "（无）"))
        lines.append(f"- 祖先层键 · 现行规则 `{payload['contract'].get('rule_version_current')}`："
                     + ("、".join(f"`{k}`" for k in keys.get("ancestor_current_rule") or ())
                        or "（无）")
                     + ("" if keys.get("ancestor_current_rule_available")
                        else " ⚠ **派生不出来**（本栏不在读入的冻结 Contract 里）；"
                             "此时本栏的 `fit` 记 `祖先层不可判定`，"
                             "**不得**拿上面那套运行记录形态的键顶替"))
        if keys.get("sibling_exclusion_keys"):
            lines.append("- 现行规则的兄弟项排除集（`NAV_SIBLING_ITEM_RULE_ID`）："
                         + "、".join(f"`{k}`" for k in keys["sibling_exclusion_keys"]))
        navigation = column.get("navigation")
        if navigation is None:
            lines.append("- 导航读数：**本轮没有这一栏的行**"
                         "（本层键因此按现行规则从 Contract 派生；祖先层键见上）")
        else:
            for outcome in navigation["outcomes"]:
                lines.append(
                    f"- 导航终态：`{outcome['status']}`"
                    + (f"/`{outcome['fallback_reason']}`"
                       if outcome.get("fallback_reason") else "")
                    + f"；命中层级 `{outcome['key_tier']}`；读根 "
                    + ("、".join(f"《{t}》" for t in outcome["read_root_titles"])
                       or "（无）")
                    + f"；读集 {outcome['read_node_count']} 个节点"
                    + f"（记录规则 `{'、'.join(navigation['rule_versions'])}`）")
        counts = column.get("counts") or {}
        lines.append("- 取材档位（现行规则）："
                     + ("、".join(f"`{label}`×{counts[label]}"
                                  for label in FIT_LABELS if counts.get(label))
                        or "（本栏无材料）"))
        if column.get("fit_changed_count"):
            run_counts = column.get("counts_run_rule") or {}
            lines.append(f"- **规则版本改变判定** {column['fit_changed_count']} 条：按本次运行记录的"
                         "规则形态会是 "
                         + ("、".join(f"`{label}`×{run_counts[label]}"
                                      for label in FIT_LABELS
                                      if run_counts.get(label)) or "（无）"))
        lines += _render_four_columns(column.get("four_column_counts") or {})
        gap = column.get("gap")
        if gap:
            lines.append("- **缺口**：" + "、".join(f"`{k}`" for k in gap["kinds"]))
            for fact in gap["facts"]:
                lines.append(f"  - {fact}")
        else:
            lines.append("- 缺口：无（本栏至少有一条本层取材，且原文可核对）")
        lines += _render_completion(column.get("completion") or {})
        lines.append("")
        if not column.get("materials"):
            lines.append("（本栏没有任何材料——见上面的缺口读数；"
                         "这不等于「该栏不适用」，也不等于「不用给结论」。）")
            lines.append("")
            continue
        for row in column["materials"]:
            lines.append(f"#### `{row['material_id']}` · **{row['fit']}**"
                         f"（`{row['fit_reason']}`）"
                         + (f" · 运行记录规则下为 **{row['fit_run_rule']}**"
                            if row["fit_changed_by_rule_version"] else ""))
            lines.append("")
            lines.append(
                f"- 原文出处：文档 `{row['document_id']}` / 版本 `{row['document_version']}`"
                f" / 证据集 "
                + (f"`{row['evidence_set_version']}`"
                   if row.get("evidence_set_version") is not None
                   else "**取不到（不是猜的值）**")
                + f"；{_page_text(row)}")
            lines.append(f"- 定位符：`{json.dumps(row['locator'], ensure_ascii=False)}`")
            lines.append(f"- 章节路径：`{row.get('section_path')}`"
                         + (f"；表题 `{row.get('table_title')}`"
                            if row.get("table_title") else ""))
            lines.append("- 结构契合：本层键命中 "
                         + ("、".join(f"`{k}`" for k in row["own_key_hits"]) or "（无）")
                         + "；祖先层键命中（现行规则）"
                         + ("、".join(f"`{k}`" for k in row["ancestor_key_hits"]) or "（无）"))
            if row.get("locator_reason"):
                lines.append(f"- 定位符读数：`{row['locator_reason']}`")
            lines.append(
                f"- Pack 处置：admission=`{row['admission_state']}` / "
                f"retention=`{row['retention_state']}` / "
                f"source_validation=`{row['source_validation']}`；"
                f"理由码 `{row['reason_code']}`；理由 `{row['reason_proof']}`")
            lines.append(f"- 去向：`{row['retention_destination']}`；进 Writer 清单："
                         f"`{json.dumps(row['in_writer_manifest'], ensure_ascii=False)}`"
                         f"（`{row['writer_manifest_query']}`）")
            lines.append(f"- 内容形态：`{row['content_kind']}`"
                         + ("（可读）" if row.get("content_kind_in_vocabulary")
                            else "（**读不懂**）"))
            for label, key in (("① 结构匹配", "structural_match"),
                               ("② 正文相关性", "body_relevance"),
                               ("③ 事实资格", "fact_eligibility"),
                               ("④ 实际写作采用", "writing_adoption")):
                block = row.get(key) or {}
                lines.append(f"- {label}：`{block.get('value')}`——{block.get('reason')}"
                             f"（判据 `{block.get('basis')}`"
                             + (f"；语义那一半 `{block['semantic_relevance']}`"
                                if block.get("semantic_relevance") else "")
                             + "）")
            lines.append("- 能证明什么：")
            for item in row["can_prove"]:
                lines.append(f"  - {item}")
            lines.append("- 不能证明什么：")
            for item in row["cannot_prove"]:
                lines.append(f"  - {item}")
            excerpt = _excerpt(row.get("text"))
            if excerpt:
                lines += ["", "- 原文（摘录；全文见 `material_pack.md`）：", "",
                          "```text", excerpt, "```", ""]
            else:
                lines += ["", f"- 原文：**读不出**（`{row['text_status']}`）——"
                              "这不等于「原文为空」。", ""]

    groups = payload.get("continuity_groups") or []
    lines += ["## 三、跨页连续阅读视图（受控；**原文逐字保留，不重写**）", ""]
    if not groups:
        lines += ["本轮的已接纳 `text` 材料里，没有**同文档同节、页号相差 1、且共享 aspect "
                  "绑定**的相邻碎片 → 本视图为空。", ""]
    for index, group in enumerate(groups, start=1):
        lines += [f"### 视图 {index}：`{group['document_id']}@"
                  f"{group['document_version']}` / 节 `{group['section_id']}`", "",
                  f"- 共享 aspect 绑定：" + "、".join(
                      f"`{a}`" for a in group["shared_aspect_ids"]),
                  f"- 判据：{group['rule']}", ""]
        for order, fragment in enumerate(group["fragments"], start=1):
            lines += [f"**碎片 {order}** · `{fragment['material_id']}`",
                      "",
                      f"- 定位符：文档 `{group['document_id']}` / 版本 "
                      f"`{group['document_version']}` / p{fragment['page']}"
                      f"（块 {fragment['block_range']}，偏移 {fragment['offset']}）",
                      f"- 章节路径：`{fragment['section_path']}`",
                      f"- 字数：{fragment['text_chars']}（**原文如下，未经改写、未经拼接**）",
                      "", "```text", fragment["text"] or "", "```", ""]
            if order < len(group["fragments"]):
                lines += ["---（页边界：以下碎片来自下一页，各自保留自己的来源与定位符）"
                          "---", ""]
        lines.append("")

    excluded = payload.get("columns_excluded") or []
    if excluded:
        lines += [f"## 四、未列入的 Contract 栏目：{len(excluded)} 条", "",
                  "这些方面本轮**没有导航读数、没有材料**，也不属于读回缺口的节，",
                  "因此不逐条展开（避免把它们读成「已判为无关」）。", "",
                  "`" + "`、`".join(excluded) + "`", ""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="由一次运行的产物生成逐栏目材料契合表（只读，不调 LLM）")
    parser.add_argument("run_dir", help="运行结果目录（须含 material_pack.json）")
    parser.add_argument("--out", default=None,
                        help="输出目录（默认与 run_dir 相同）")
    parser.add_argument("--repo", default=None, help="仓库根（默认按本文件位置推断）")
    parser.add_argument("--contract", default=None,
                        help=f"冻结 Contract 资产（默认 {DEFAULT_CONTRACT_ASSET}）")
    parser.add_argument("--quiet", action="store_true", help="不打印汇总")
    args = parser.parse_args(argv)
    payload = build(Path(args.run_dir),
                    repo=Path(args.repo) if args.repo else None,
                    contract_asset=Path(args.contract) if args.contract else None)
    out_dir = Path(args.out) if args.out else Path(args.run_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "material_fitness.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "material_fitness.md").write_text(
        render_md(payload), encoding="utf-8")
    if not args.quiet:
        print(json.dumps({
            "status": payload.get("status"),
            "columns": payload.get("column_count"),
            "columns_excluded": len(payload.get("columns_excluded") or ()),
            "documents": [(d["document_id"], d["material_count"], d["admitted"],
                           d["consumption_state"])
                          for d in payload.get("documents") or ()],
            "unbound_materials": len(payload.get("unbound_materials") or ()),
            "continuity_groups": len(payload.get("continuity_groups") or ()),
            "gap_columns": sum(1 for c in payload.get("columns") or () if c.get("gap")),
            "out_dir": str(out_dir),
        }, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") in ("readback", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""跨文档联合检索 §L1.5 / §0.2.1：**逐 aspect × 逐来源责任模型**（T11 + T21）。

跑法（无管道/无重定向）：`PYTHONIOENCODING=utf-8 python -m evals.test_aspect_source_responsibility`

本模块只测**检索前**的责任派生，不测任何检索结果、材料或终态（那些在 §L4.4 的另一个对象里）。

1. **真实契约口径**（正面）：本仓 187 个 aspect 中，**184 个**全成员 `proof_required != "true"`
   （不含 `set_complete` 的普通事实栏目）；恰 **3 个**（`set_complete` 栏目）出现 `undetermined` 边。
2. **X-1 反例**（T21）：普通事实栏目**永不**承担集合枚举证明责任。反例面 = 给同一个 aspect
   的 `coverage_rules` 加上 `set_complete`，断言它**当场**长出未决边——证明第一约束真的在起作用，
   而不是"恰好都没判到"。
3. **X-2 反例**（T21）：`set_complete` 栏目下，**只有**「在必查范围内且非 `supplemental_only`」
   的非当前状态来源取 `undetermined`；必查范围外者取 `"false"`（`not_in_required_scope`），
   `supplemental_only` 者取 `"false"`（`supplemental_only_never_proves_completeness`）。
   **不得**由"源集有多份"直接推出未决。
4. **X-3 反例**（T21）：派生函数的签名里**没有**任何检索结果/材料/facts/trace/stop_reason 参数；
   本模块也**不引用**任何不存在的 SourcePolicy 排除字段名。
5. **规则 2b**：逐份尝试的来由是 `batch_scheduling_rule`（本批调度规则），**不是** Contract 义务。
6. **单文档退化**（回归保护）：源集 1 份当前来源 ⇒ 恰一行、取 `"true"`、无任何 `undetermined` 边。
7. **禁止倒填**：责任指纹只覆盖输入轴；改一个 `proof_required` 即改指纹；遍历顺序不影响指纹。
8. **记录不得含结果字段**：`to_dict()` 的键集封闭；`from_dict` 拒收 `material_found` 等结果字段。
9. **来源类桥**：上传文档的 Contract 来源类 = `evidence` 权威类 `company_industry`；
   **不得**用登记侧 `material_group` 当成员来源类（那会把必须查的年报判成"无需检索"）。

公司无关：一律用合成公司/合成文档键，不引入 300750 / 宁德时代 / 固定页码分支。
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.test_topic_pack_contract_reachability import (  # noqa: E402  真实冻结契约投影（复用）
    _load_frozen_assets, _snapshot_from_aspect)
from harness import source_manifest as SM  # noqa: E402
from harness import topic_runtime as TR  # noqa: E402
from harness import topic_schema as TS  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

_COMPANY = "ACME"


def _keys(*ids: str) -> tuple[SM.SourceDocumentKey, ...]:
    return tuple(SM.SourceDocumentKey(
        company_id=_COMPANY, document_id=doc_id,
        document_version="sha256-" + f"{i + 1:x}" * 16,
        evidence_set_version="set-" + f"{i + 1:x}" * 12)
        for i, doc_id in enumerate(ids))


def _source_set(keys, roles) -> TR.DocumentSourceSet:
    return TR.DocumentSourceSet(members=tuple(zip(keys, roles)))


def _three_doc_set() -> TR.DocumentSourceSet:
    """三文档源集：本年报（当前锚）+ 同系列旧年报 + 募集说明书（其他系列）。"""
    return _source_set(_keys("DOC_2025", "DOC_2024", "DOC_BOND"),
                       ("current_state_source", "history_and_conflict_source",
                        "topic_participating_source"))


def _all_aspects():
    contract, sp, csha, spfp, ers, _ts = _load_frozen_assets()
    out = []
    for sec in contract.sections:
        for topic in sec.topics:
            for question in topic.questions:
                for aspect in question.aspects:
                    out.append(_snapshot_from_aspect(aspect, csha, sp, spfp, ers))
    return out


def _with_required_classes(snap, classes: tuple[str, ...]):
    """把某个 aspect 的冻结 EvidenceRequirementRef 的 authority 换成给定来源类（合成变体）。"""
    er = snap.evidence_requirement_ids[0]
    authority = TS.EvidenceAuthorityPolicy(
        required_any_of=(TS.SourceClassGroup(source_classes=classes),),
        supplemental_only=())
    return dataclasses.replace(
        snap, evidence_requirement_ids=(dataclasses.replace(er, authority=authority),))


def _with_supplemental_only(snap, required: tuple[str, ...], supplemental: tuple[str, ...]):
    er = snap.evidence_requirement_ids[0]
    authority = TS.EvidenceAuthorityPolicy(
        required_any_of=(TS.SourceClassGroup(source_classes=required),),
        supplemental_only=supplemental)
    return dataclasses.replace(
        snap, evidence_requirement_ids=(dataclasses.replace(er, authority=authority),))


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            details.append(f"FAIL {msg}")

    sources = _three_doc_set()

    # ------------------------------------------------------------------
    # 1. 真实契约口径（正面）
    # ------------------------------------------------------------------
    check(TR.ASSUME_RULE_VERSION == "asr-1",
          f"责任派生规则版本必须是 asr-1（实际 {TR.ASSUME_RULE_VERSION!r}）")
    check(TR.PROOF_REQUIRED_VALUES == ("true", "false", "undetermined"),
          f"proof_required 必须是三值域（实际 {TR.PROOF_REQUIRED_VALUES}）")
    check("undetermined" in TR.PROOF_REQUIRED_VALUES,
          "「无法确定」必须是一等结论，不得退化成 false")

    aspects = _all_aspects()
    check(len(aspects) == 187, f"冻结契约应有 187 个 aspect（实际 {len(aspects)}）")

    no_true = 0
    undetermined_aspects: list[str] = []
    set_complete_ids: list[str] = []
    all_records: list[TR.AspectSourceResponsibility] = []
    for snap in aspects:
        records = TR.derive_aspect_source_responsibility(aspect=snap, sources=sources)
        all_records.extend(records)
        check(len(records) == 3,
              f"{snap.aspect_id} 应逐份产生 3 条责任记录（实际 {len(records)}）")
        if all(r.proof_required != "true" for r in records):
            no_true += 1
        if any(r.proof_required == "undetermined" for r in records):
            undetermined_aspects.append(snap.aspect_id)
        if "set_complete" in set(snap.coverage_rules or ()):
            set_complete_ids.append(snap.aspect_id)

    check(no_true == 184, f"应有 184 个 aspect 全成员非 true（实际 {no_true}）")
    check(sorted(undetermined_aspects) == sorted(set_complete_ids) and len(set_complete_ids) == 3,
          f"恰 3 个 set_complete 栏目出现未决边（实际 undetermined={sorted(undetermined_aspects)}，"
          f"set_complete={sorted(set_complete_ids)}）")
    check(all(r.rule_version == TR.ASSUME_RULE_VERSION for r in all_records),
          "每条责任记录都必须带规则版本（可复算）")
    check(all(r.in_source_set for r in all_records),
          "源集成员的 in_source_set 恒真（源集之外的键不产生本记录）")

    # ------------------------------------------------------------------
    # 2. X-1 反例：普通事实栏目永不承担集合枚举证明责任
    # ------------------------------------------------------------------
    plain = next(s for s in aspects
                 if "set_complete" not in set(s.coverage_rules or ())
                 and "company_industry" in set(
                     TS.derive_support_eligibility(s).required_source_classes))
    plain_records = TR.derive_aspect_source_responsibility(aspect=plain, sources=sources)
    check(all(r.proof_required == "false" for r in plain_records),
          f"{plain.aspect_id} 不含 set_complete ⇒ 全成员 proof_required=false")
    check(all(("aspect_not_set_complete_no_enumeration_proof" in {row[0] for row in r.basis})
              for r in plain_records),
          "普通事实栏目的证明责任必须由第一约束 rule 明确表态")
    check(not any("undetermined" == r.proof_required for r in plain_records),
          "普通事实栏目不得出现未决边（第一约束在最前，到此为止）")

    # 反例面：给同一个 aspect 加上 set_complete，它必须**当场**长出未决边。
    promoted = dataclasses.replace(
        plain, coverage_rules=tuple(plain.coverage_rules) + ("set_complete",))
    promoted_records = TR.derive_aspect_source_responsibility(aspect=promoted, sources=sources)
    check([r.proof_required for r in promoted_records] == ["true", "undetermined", "undetermined"],
          f"加上 set_complete 后必须出现未决边（实际 "
          f"{[r.proof_required for r in promoted_records]}）")
    check("aspect_not_set_complete_no_enumeration_proof"
          not in {row[0] for r in promoted_records for row in r.basis},
          "set_complete 栏目不得再引用第一约束的 rule_id")

    # 反例面：冻结证据需求既无 authority 也无 source_classes ⇒ 必查范围为空 ⇒ 全成员 false
    # （`TS.derive_support_eligibility`：两者皆空才空；只有 authority 缺失时仍读 `source_classes`）。
    no_scope = dataclasses.replace(
        promoted, evidence_requirement_ids=tuple(
            dataclasses.replace(er, authority=None, source_classes=())
            for er in promoted.evidence_requirement_ids))
    check(not TS.derive_support_eligibility(no_scope).required_source_classes,
          "夹具前提：该变体的必查来源类为空")
    na_records = TR.derive_aspect_source_responsibility(aspect=no_scope, sources=sources)
    check(all(r.retrieval_required is False for r in na_records),
          "无冻结来源类时不得凭空要求检索")
    check(all(r.proof_required == "false" for r in na_records),
          "无冻结来源类时证明域为空（fail-closed，不取 true）")

    # ------------------------------------------------------------------
    # 3. X-2 反例：只有「必查范围内 ∧ 非 supplemental_only」的非当前来源取 undetermined
    # ------------------------------------------------------------------
    set_complete_snap = next(s for s in aspects
                             if "set_complete" in set(s.coverage_rules or ()))
    sc_records = TR.derive_aspect_source_responsibility(
        aspect=set_complete_snap, sources=sources)
    by_id = {r.source_document_key.document_id: r for r in sc_records}
    check(by_id["DOC_2025"].proof_required == "true"
          and "current_state_source_proves_completeness"
          in {row[0] for row in by_id["DOC_2025"].basis},
          "当前状态锚取 true，且理由点名 current_state_source_proves_completeness")
    for doc_id in ("DOC_2024", "DOC_BOND"):
        rec = by_id[doc_id]
        check(rec.retrieval_required is True,
              f"{doc_id} 在必查范围内必须要求检索")
        check(rec.proof_required == "undetermined",
              f"{doc_id} 必须原样表达 undetermined（不得压成 false/true）")
        check("cross_period_responsibility_not_expressible_in_frozen_contract"
              in {row[0] for row in rec.basis},
              f"{doc_id} 的未决理由必须点名「冻结契约表达不出跨期责任」")

    # 对照面 A：必查范围外 ⇒ false（不是 undetermined；不得由"多份来源"推断未决）
    out_of_scope = _with_required_classes(set_complete_snap, ("structured_db",))
    oos_records = TR.derive_aspect_source_responsibility(
        aspect=out_of_scope, sources=sources)
    check(all(r.retrieval_required is False for r in oos_records),
          "来源类不在必查范围内 ⇒ retrieval_required=false")
    check(all(r.proof_required == "false" for r in oos_records),
          "必查范围外的成员取 false，**不得**因源集有多份而误判 undetermined")
    check(all({row[0] for row in r.basis} == {"not_in_required_scope"} for r in oos_records),
          "retrieval_required=false 的依据只能是 not_in_required_scope")

    # 对照面 B：supplemental_only ⇒ false（可检索、不承担集合完整性证明）
    supp = _with_supplemental_only(set_complete_snap, ("company_industry",), ("company_industry",))
    supp_records = TR.derive_aspect_source_responsibility(aspect=supp, sources=sources)
    check(all(r.retrieval_required is True for r in supp_records),
          "supplemental_only 的来源仍可要求检索（retrieval_required 可为真）")
    check(all(r.proof_required == "false" for r in supp_records),
          "supplemental_only 恒 false（永不承担集合完整性证明）")
    check(all("supplemental_only_never_proves_completeness" in {row[0] for row in r.basis}
              for r in supp_records),
          "supplemental_only 的 proof 依据必须点名其永不证明完整性")
    check(all({row[0] for row in r.basis} >= {"batch_scheduling_rule"}
              for r in supp_records),
          "supplemental_only 且要求检索者属于臂 A/B（basis 含批次调度规则）")

    # 对照面 C：期间不可核实 ⇒ 全成员 undetermined（含非 set_complete 的第一约束对照）
    ambiguous = TR.derive_aspect_source_responsibility(
        aspect=set_complete_snap, sources=sources,
        current_state=SM.CURRENT_STATE_AMBIGUOUS)
    check(all(r.proof_required == "undetermined" for r in ambiguous),
          "ambiguous_current_state ⇒ 全成员 undetermined")
    check(all("ambiguous_current_state_no_proof_domain" in {row[0] for row in r.basis}
              for r in ambiguous),
          "期间不可核实的未决理由必须点名无证明域")
    amb_plain = TR.derive_aspect_source_responsibility(
        aspect=plain, sources=sources, current_state=SM.CURRENT_STATE_AMBIGUOUS)
    check(all(r.proof_required == "false" for r in amb_plain),
          "第一约束在最前：非 set_complete 栏目在未决源集下仍取 false")

    # 单文档退化（回归保护）
    single = _source_set(_keys("DOC_2025"), ("current_state_source",))
    single_records = TR.derive_aspect_source_responsibility(
        aspect=set_complete_snap, sources=single)
    check(len(single_records) == 1 and single_records[0].proof_required == "true",
          "单文档源集 ⇒ 恰一行、取 true（逐字退化为今天的行为）")
    check(all(r.proof_required != "undetermined" for r in single_records),
          "单文档退化不得产生未决边")

    # ------------------------------------------------------------------
    # 4. X-3 反例：责任只能由输入轴派生（签名里不得有任何结果轴）
    # ------------------------------------------------------------------
    params = set(inspect.signature(
        TR.derive_aspect_source_responsibility).parameters)
    check(params == {"aspect", "sources", "member_source_classes", "current_state"},
          f"派生函数签名必须只含输入轴（实际 {sorted(params)}）")
    forbidden = ("material", "fact", "trace", "stop_reason", "result", "outcome", "attempt",
                 "search", "hit", "found", "pack", "gap")
    bad = sorted(p for p in params for f in forbidden if f in p)
    check(not bad, f"责任派生函数不得接受任何结果轴参数（实际命中 {bad}）")

    # SourcePolicy 不构成 retrieval_required=false 的理由：本模块不引用任何排除字段名，
    # 且派生输入里根本没有 SourcePolicy。
    src = (REPO / "harness" / "topic_runtime.py").read_text(encoding="utf-8")
    check("excluded_source" not in src and "source_policy_excluded" not in src,
          "不得引用不存在的 SourcePolicy 排除字段名")

    # ------------------------------------------------------------------
    # 5. 规则 2b：逐份尝试的来由是本批调度规则，不是 Contract 义务
    # ------------------------------------------------------------------
    for rec in sc_records:
        batch_rows = [row for row in rec.basis if row[0] == "batch_scheduling_rule"]
        check(len(batch_rows) == 1,
              f"{rec.source_document_key.document_id} 的批次调度来由必须逐条写明")
        check("company_industry" in batch_rows[0][1],
              "批次调度规则必须点名来源类是哪一个")
        check("不是" in batch_rows[0][1] and "Contract" in batch_rows[0][1],
              "批次调度规则必须显式声明它不是 Contract 的逐份义务")
    check(not any("Contract 要求逐份" in row[1] for r in sc_records for row in r.basis),
          "不得把本批调度规则写成 Contract 的逐份义务")
    check(TR.NOT_REQUIRED_BASIS_RULE_IDS == frozenset(
        {"not_in_required_scope", "batch_scheduling_rule"}),
          "臂 C1 可引用的 rule_id 恰好是这一对（c 轮收紧）")
    check("supplemental_only_never_proves_completeness"
          not in TR.NOT_REQUIRED_BASIS_RULE_IDS,
          "supplemental_only 的 proof 依据**永不**是「无需检索」的依据")

    # ------------------------------------------------------------------
    # 6. 禁止倒填：指纹只覆盖输入轴，顺序无关、内容敏感
    # ------------------------------------------------------------------
    fp1 = TR.aspect_source_responsibility_fingerprint(tuple(all_records))
    fp2 = TR.aspect_source_responsibility_fingerprint(tuple(reversed(all_records)))
    check(fp1 == fp2, "同一责任台账的指纹不得随遍历顺序变化（可复算）")
    tampered = list(all_records)
    victim = next(i for i, r in enumerate(tampered) if r.proof_required == "undetermined")
    tampered[victim] = dataclasses.replace(tampered[victim], proof_required="false")
    check(TR.aspect_source_responsibility_fingerprint(tuple(tampered)) != fp1,
          "看见结果后倒填责任（把 undetermined 压成 false）必须改变指纹")
    check(fp1 == TR.aspect_source_responsibility_fingerprint(
        tuple(TR.AspectSourceResponsibility.from_dict(r.to_dict()) for r in all_records)),
        "责任记录往返后指纹不变（字段封闭、无隐式字段）")

    # ------------------------------------------------------------------
    # 7. 记录不得含任何结果字段
    # ------------------------------------------------------------------
    expected_keys = {"aspect_id", "source_document_key", "in_source_set", "retrieval_required",
                     "proof_required", "basis", "rule_version"}
    rec_dict = sc_records[0].to_dict()
    check(set(rec_dict) == expected_keys,
          f"责任记录键集必须封闭（实际 {sorted(rec_dict)}）")
    for forbidden_key in ("material_found", "attempted", "hit", "call_ids", "stop_reason"):
        check(forbidden_key not in rec_dict,
              f"责任记录不得含结果字段 {forbidden_key}")
        poisoned = dict(rec_dict)
        poisoned[forbidden_key] = []
        try:
            TR.AspectSourceResponsibility.from_dict(poisoned)
            check(False, f"from_dict 必须拒收结果字段 {forbidden_key}")
        except (TR.ResponsibilityDerivationError, TS.SchemaValidationError):
            check(True, f"from_dict 拒收结果字段 {forbidden_key}")
    try:
        TR.AspectSourceResponsibility.from_dict(
            dict(rec_dict, proof_required='maybe'))
        check(False, "proof_required 取三值域外的值必须失败")
    except (TR.ResponsibilityDerivationError, TS.SchemaValidationError):
        check(True, "三值域之外 fail-closed")
    try:
        TR.AspectSourceResponsibility.from_dict(dict(rec_dict, basis=[["free_text", "理由"]]))
        check(False, "未登记的 basis rule_id 必须失败")
    except (TR.ResponsibilityDerivationError, TS.SchemaValidationError):
        check(True, "basis rule_id 封闭集之外 fail-closed")

    # ------------------------------------------------------------------
    # 8. 来源类桥：上传文档 = evidence 权威 = company_industry
    # ------------------------------------------------------------------
    check(TR.UPLOADED_DOCUMENT_SOURCE_CLASS == "company_industry",
          "上传文档的 Contract 来源类必须是 company_industry")
    check(TR.UPLOADED_DOCUMENT_SOURCE_CLASS
          == TS.AUTHORITY_SOURCE_CLASS_BY_TYPE["evidence"],
          "该桥必须复用既有 AUTHORITY_SOURCE_CLASS_BY_TYPE，不新增词汇")
    check(TR.UPLOADED_DOCUMENT_SOURCE_CLASS not in ("financial", "project"),
          "**不得**用登记侧 material_group 当成员来源类（那会把必须查的年报判成无需检索）")
    check("material_group" not in params,
          "派生函数不得读登记侧 material_group 决定 retrieval_required")
    default_records = TR.derive_aspect_source_responsibility(
        aspect=set_complete_snap, sources=sources)
    check(all(r.retrieval_required is True for r in default_records),
          "缺省来源类下，要求 company_industry 的栏目必须逐份要求检索（不得静默跳过）")

    # ------------------------------------------------------------------
    # 9. DocumentSourceSet：有序、同主体、至多一个当前锚、不兜底
    # ------------------------------------------------------------------
    check(sources.roles() == ("current_state_source", "history_and_conflict_source",
                             "topic_participating_source"),
          "源集角色必须按序位原样保留（有序即语义）")
    check(sources.keys()[0].document_id == "DOC_2025",
          "序位不得被重排")
    check(sources.current_state_key().document_id == "DOC_2025",
          "唯一当前锚必须可点名")
    check(sources.fingerprint() == TR.DocumentSourceSet.from_dict(
        sources.to_dict()).fingerprint(),
        "源集指纹往返不变")
    check(sources.fingerprint() != _source_set(
        _keys("DOC_2025", "DOC_BOND", "DOC_2024"),
        ("current_state_source", "topic_participating_source",
         "history_and_conflict_source")).fingerprint(),
        "源集指纹必须对序位与角色敏感")

    for label, members, exc in (
        ("空源集", (), TR.ResponsibilityDerivationError),
        ("重复文档", ((_keys("D1")[0], "current_state_source"),
                   (_keys("D1")[0], "topic_participating_source")),
         TR.ResponsibilityDerivationError),
        ("非法角色", ((_keys("D1")[0], "primary"),), TR.ResponsibilityDerivationError),
        ("两个当前锚", ((_keys("D1")[0], "current_state_source"),
                    (_keys("D2")[0], "current_state_source")),
         TR.ResponsibilityDerivationError),
        ("主体不一致", tuple(zip(
            (SM.SourceDocumentKey("ACME", "D1", "v1", "e1"),
             SM.SourceDocumentKey("OTHER", "D2", "v2", "e2")),
            ("current_state_source", "topic_participating_source"))),
         TR.ResponsibilityDerivationError),
    ):
        try:
            TR.DocumentSourceSet(members=members)
            check(False, f"{label} 的源集必须被拒（fail-closed）")
        except exc:
            check(True, f"{label} fail-closed")

    # ------------------------------------------------------------------
    # 9b. T4：成员关联一律按**四轴**核对（正例 + 两类反例）
    # ------------------------------------------------------------------
    # 正例：源集里真实存在的那把键（四轴全同）必须取得到角色。
    member_key = _keys("DOC_2025")[0]
    check(sources.role_of(member_key) == "current_state_source",
          "正例：源集成员的完整四轴键必须取回它自己的角色")
    check(sources.has_member(member_key) is True,
          "正例：`has_member` 对源集成员为真")
    check(sources.member_key(member_key) == member_key,
          "正例：`member_key` 回取到源集里的那一把键")
    # 反例一：同 `document_id`、错 `document_version`——内容寻址下那是**另一份文档**，
    # 不是「同一份的另一个版本」。就近匹配会在这里静默配上一个角色。
    wrong_version = SM.SourceDocumentKey(_COMPANY, "DOC_2025", "sha256-other", "set-other")
    try:
        sources.role_of(wrong_version)
        check(False, "反例：同 id 错 document_version 必须 fail-closed（不得按 id 就近匹配）")
    except TR.ResponsibilityDerivationError as exc:
        check("同 document_id" in str(exc),
              f"同 id 错版本必须报出「同 document_id、不同文档身份」这一档（实际 {exc}）")
    check(sources.has_member(wrong_version) is False,
          "反例：同 id 错版本不是成员（`has_member` 为假，且不抛）")
    # 反例二：同 id 同版本、错 `evidence_set_version`——同样的两份文档，同样的 fail-closed。
    wrong_set = SM.SourceDocumentKey(
        member_key.company_id, member_key.document_id,
        member_key.document_version, "set-other")
    try:
        sources.role_of(wrong_set)
        check(False, "反例：同 id 同版本、错 evidence_set_version 必须 fail-closed")
    except TR.ResponsibilityDerivationError as exc:
        check("同 document_id" in str(exc),
              f"错第四轴同样报「同 document_id、不同文档身份」（实际 {exc}）")
    check(sources.has_member(wrong_set) is False,
          "反例：错第四轴不是成员（`has_member` 为假，且不抛）")
    # 反例三：根本不在集合内 ⇒ 另一档错误（漏登记，而不是身份写错）。
    try:
        sources.role_of(SM.SourceDocumentKey(_COMPANY, "DOC_MISSING", "v", "e"))
        check(False, "role_of 对源集之外的键必须失败（不兜底、不默认）")
    except (TR.ResponsibilityDerivationError, TS.SchemaValidationError) as exc:
        check("不在本来源集内" in str(exc),
              f"集合外的键要报「不在本来源集内」，与「同 id 错身份」分档（实际 {exc}）")
    # `member_source_classes`：**键必须是完整四轴**。
    # 正例：四轴的 `(aspect, 来源)` 键必须真的把来源类带上（否则这张表就是摆设）。
    try:
        TR.derive_aspect_source_responsibility(
            aspect=set_complete_snap, sources=sources,
            member_source_classes={(_COMPANY, member_key.document_id,
                                    member_key.document_version,
                                    member_key.evidence_set_version): "company_industry"})
        check(True, "正例：四轴键的来源类表被接受")
    except (TR.ResponsibilityDerivationError, TS.SchemaValidationError) as exc:
        check(False, f"正例：四轴键的来源类表**不得**被拒（实际 {exc}）")
    # 反例一：裸 `document_id` 作键（旧写法）——即使 id 是对的也必须拒。
    try:
        TR.derive_aspect_source_responsibility(
            aspect=set_complete_snap, sources=sources,
            member_source_classes={member_key.document_id: "company_industry"})
        check(False, "反例：裸 document_id 作键必须被拒（四轴才是比较单位）")
    except (TR.ResponsibilityDerivationError, TS.SchemaValidationError) as exc:
        check("SourceDocumentKey" in str(exc) or "四轴" in str(exc),
              f"裸 id 键要明确报出「需要四轴键」（实际 {exc}）")
    # 反例二：四轴里 id 对、版本错 ⇒ 同 id 错身份档。
    try:
        TR.derive_aspect_source_responsibility(
            aspect=set_complete_snap, sources=sources,
            member_source_classes={(_COMPANY, member_key.document_id, "sha256-other",
                                    member_key.evidence_set_version): "company_industry"})
        check(False, "反例：同 id 错 document_version 的来源类表必须 fail-closed")
    except (TR.ResponsibilityDerivationError, TS.SchemaValidationError) as exc:
        check("同 document_id" in str(exc),
              f"错版本要报「同 document_id、不同文档身份」（实际 {exc}）")
    # 反例三：四轴里其余全对、第四轴错 ⇒ 同样 fail-closed。
    try:
        TR.derive_aspect_source_responsibility(
            aspect=set_complete_snap, sources=sources,
            member_source_classes={(_COMPANY, member_key.document_id,
                                    member_key.document_version, "set-other"):
                                   "company_industry"})
        check(False, "反例：错第四轴的来源类表必须 fail-closed")
    except (TR.ResponsibilityDerivationError, TS.SchemaValidationError) as exc:
        check("同 document_id" in str(exc),
              f"错第四轴同样报「同 document_id、不同文档身份」（实际 {exc}）")
    # 反例四：完全在集合外 ⇒ 「不在本来源集内」档，与上面两档分开。
    try:
        TR.derive_aspect_source_responsibility(
            aspect=set_complete_snap, sources=sources,
            member_source_classes={(_COMPANY, "DOC_MISSING", "v", "e"): "company_industry"})
        check(False, "member_source_classes 含源集之外的键必须失败")
    except (TR.ResponsibilityDerivationError, TS.SchemaValidationError) as exc:
        check("不在本来源集内" in str(exc),
              f"集合外的键要报「不在本来源集内」（实际 {exc}）")

    # ------------------------------------------------------------------
    # 10. 归属理由可读：不得出现「未披露 / 没有 / 不存在」
    # ------------------------------------------------------------------
    blob = json.dumps([r.to_dict() for r in all_records], ensure_ascii=False)
    for word in ("未披露", "不存在"):
        check(word not in blob,
              f"责任记录不得使用「{word}」这类客观否定（那时是结果的语义，不是责任）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

"""Eval: formal `ExternalFact` 构造门与 qualified-result 基数（§16.10 #9 / #37，M930-3A）。

用法: python -m evals.test_demo_external_fact

证明：
 1. `ExternalSnapshot` **只作来源载体**：carrier 由 `ExternalSnapshotAuthorityAssessment`
    + `MaterialPayloadRef(object_type="external_snapshot")` 表达，唯一的授权对象是
    `ExternalFact`；snapshot 本身不能冒充事实（无该类型、无该 Pack 字段）。
 2. 构造门缺一即拒：资格决定、snapshot/body hash 三方闭环、SourcePolicy 版本、日期、
    命题/aspect、external locator、authority verdict=authoritative。
 3. #37：`external_source` 候选的 eligible 决定**恰一条** `ExternalFact`；rejected 决定
    零条；internal/external result kind 不得交叉或双产；每条 result 反解唯一 candidate +
    eligible 决定。
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_schema as TS  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

_SNAP = "ext1"
_URL = "https://x.example/1"
_DOMAIN = "x.example"
_BODY_HASH = TS.sha256_canonical({"body": _SNAP})
_POLICY_VERSION = "source-policy-1"


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def expect_raises(msg: str, fn, needle: str) -> None:
    try:
        fn()
    except TS.SchemaValidationError as exc:
        if needle in str(exc):
            check(True, f"{msg}：{str(exc)[:70]}")
        else:
            check(False, f"{msg}：期望理由含 {needle!r}，实际 {str(exc)[:90]}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"{msg}：期望 SchemaValidationError，实际 {type(exc).__name__}: {exc}")
    else:
        check(False, f"{msg}：未 fail-closed")


def _digest(tag: str) -> str:
    return TS.sha256_canonical({"fixture": tag})


# ---------------------------------------------------------------------------
# 夹具：全部走生产唯一 factory（不另写第二套身份口径）
# ---------------------------------------------------------------------------

def _locator(*, snapshot_id: str = _SNAP, url: str = _URL) -> TS.ExternalLocator:
    return TS.ExternalLocator(source_snapshot_id=snapshot_id, canonical_url=url, domain=_DOMAIN)


def _authority(*, snapshot_id: str = _SNAP, verdict: str = "authoritative",
               content_hash: str = _BODY_HASH) -> TS.ExternalSnapshotAuthorityAssessment:
    return TS.ExternalSnapshotAuthorityAssessment(
        source_snapshot_id=snapshot_id, canonical_url=_URL, domain=_DOMAIN,
        fetched_nonempty=True, content_hash=content_hash, published_at="2025-01-01",
        time_qualified=True, source_grade="B", min_grade_met=True,
        independence_domain="independent.example", verdict=verdict, reason="",
        validator_version="vv1")


def _payload_ref(*, locator: TS.ExternalLocator | None = None, object_type: str = "external_snapshot",
                 authority_identity: str | None = None,
                 content_hash: str = _BODY_HASH) -> TS.MaterialPayloadRef:
    loc = locator or _locator()
    return TS.MaterialPayloadRef(
        object_type=object_type,
        authority_identity=(authority_identity
                            if authority_identity is not None
                            else f"external_snapshot:{loc.source_snapshot_id}"),
        version="v1", content_hash=content_hash, locator=loc,
        created_dependency_fingerprint=_digest("cdep"))


def _ext_chain(statement: str, *, snapshot_id: str = _SNAP, verdict: str = "eligible"
               ) -> tuple[TS.FactCandidate, TS.FactQualificationDecision]:
    """external_source 候选 + 资格决定（唯一 factory）。"""
    cand = TS.build_fact_candidate(
        candidate_source_kind="external_source", statement=statement, fact_type="fact",
        aspect_ids=("a1",), question_ids=("q1",), source_snapshot_id=snapshot_id)
    if verdict == "eligible":
        dec = TS.build_qualification_decision(
            cand, verdict="eligible", input_identity_digest=_digest("id"),
            input_source_identity=f"external_snapshot:{snapshot_id}",
            input_locator_digest=_digest("loc"), input_payload_digest=_digest("pay"),
            input_snapshot_id=snapshot_id)
    else:
        dec = TS.build_qualification_decision(
            cand, verdict="rejected", input_identity_digest=_digest("id"),
            input_source_identity=f"external_snapshot:{snapshot_id}",
            input_locator_digest=_digest("loc"), input_payload_digest=_digest("pay"),
            input_snapshot_id=snapshot_id, rejection_reason="来源等级不足")
    return cand, dec


def _topic_chain(statement: str = "内部材料事实"
                 ) -> tuple[TS.FactCandidate, TS.FactQualificationDecision, TS.SupportedFact]:
    """topic_material 候选 + eligible 决定 + `SupportedFact`（唯一 factory）。"""
    cand = TS.build_fact_candidate(
        candidate_source_kind="topic_material", statement=statement, fact_type="fact",
        aspect_ids=("a1",), question_ids=("q1",), material_ids=("m1",))
    dec = TS.build_qualification_decision(
        cand, verdict="eligible", input_identity_digest=_digest("id"),
        input_source_identity="evidence:ev1", input_locator_digest=_digest("loc"),
        input_payload_digest=_digest("pay"))
    fact = TS.build_supported_fact(
        "sf_topic_1", cand, dec, text=statement, fact_type="fact",
        citation_refs=(TS.CitationRef(ref_type="evidence", evidence_id="ev1"),),
        source_authority=TS.EvidenceAuthorityAssessment(evidence_id="ev1",
                                                        verdict="authoritative"))
    return cand, dec, fact


def _external_fact(statement: str = "外部来源命题", *, snapshot_id: str = _SNAP,
                   candidate: TS.FactCandidate | None = None,
                   decision: TS.FactQualificationDecision | None = None,
                   ) -> TS.ExternalFact:
    cand, dec = _ext_chain(statement, snapshot_id=snapshot_id)
    return TS.build_external_fact(
        candidate or cand, decision or dec, statement=statement, canonical_url=_URL,
        body_hash=_BODY_HASH, source_policy_version=_POLICY_VERSION, as_of_date="2025-12-31",
        locator=_locator(snapshot_id=snapshot_id), payload_ref=_payload_ref(),
        content_hash=_BODY_HASH, source_authority=_authority(snapshot_id=snapshot_id))


def _fact_with(**overrides) -> TS.ExternalFact:
    """在良构 ExternalFact 上做**单点**变异（每次只制造一个缺陷）。"""
    return dataclasses.replace(_external_fact(), **overrides)


# ---------------------------------------------------------------------------
# 1. ExternalSnapshot 只作来源载体（#9 的身份侧）
# ---------------------------------------------------------------------------

def _check_snapshot_is_carrier_only() -> None:
    check(not hasattr(TS, "ExternalSnapshot"),
          "不存在独立 `ExternalSnapshot` 授权类型（无该类可冒充事实）")
    check("external_snapshot" in TS.MATERIAL_TYPES
          and "external_snapshot" in TS.AUTHORITY_TYPES,
          "external_snapshot 只以 material 载体 / authority discriminator 形式登记")
    check(not any("external_snapshot" in f and f != "external_facts"
                  for f in TS.TopicResearchPack.__dataclass_fields__),
          "Pack 侧只有 external_facts 一个 external 授权族（无 snapshot 授权族字段）")
    check(TS.authority_source_identity(_authority()) == f"external_snapshot:{_SNAP}"
          and isinstance(_authority(), TS.ExternalSnapshotAuthorityAssessment),
          "carrier 的来源身份由 ExternalSnapshotAuthorityAssessment 表达")

    # snapshot 载体本身**不是**事实：它没有命题、没有 candidate 回指、没有资格决定。
    auth = _authority()
    for forbidden in ("statement", "aspect_ids", "candidate_id", "candidate_revision",
                      "qualification_decision_id", "external_fact_id"):
        check(not hasattr(auth, forbidden),
              f"carrier（authority assessment）没有事实字段 {forbidden}（snapshot 不得冒充事实）")


# ---------------------------------------------------------------------------
# 2. ExternalFact 构造门（#9：snapshot 冒充 / 哈希 / SourcePolicy 不符）
# ---------------------------------------------------------------------------

def _check_construction_gate() -> None:
    fact = _external_fact()
    check(isinstance(fact, TS.ExternalFact)
          and fact.source_policy_version == _POLICY_VERSION
          and fact.as_of_date == "2025-12-31"
          and fact.aspect_ids == ("a1",)
          and fact.external_fact_id.startswith("ef-"),
          "良构 external 事实（资格决定 + SourcePolicy + 日期 + 命题 + locator + 哈希）构造通过")

    doc = fact.to_dict()
    check(TS.ExternalFact.from_dict(doc).to_dict() == doc,
          "ExternalFact 往返一致")
    expect_raises("from_dict 读数非对象",
                  lambda: TS.ExternalFact.from_dict(json.dumps(doc)), "需要 dict")
    expect_raises("from_dict 读未知字段",
                  lambda: TS.ExternalFact.from_dict({**doc, "fact_id": "sf_x"}), "未知字段")

    # -- 资格决定 / 单向回指 --
    expect_raises("缺资格决定 id",
                  lambda: _fact_with(qualification_decision_id=""), "必须绑定资格决定 id")
    expect_raises("缺 candidate id",
                  lambda: _fact_with(candidate_id=""), "单向回指 candidate id + revision")

    # -- snapshot 身份四方闭环（每次只制造一个缺陷：locator 与 payload_ref 同步改，
    #    否则先命中的是「locator 与 payload_ref.locator 不一致」这条前置检查）--
    bad_snap_loc = _locator(snapshot_id="ext_other")
    expect_raises("locator 的 snapshot 与 source_snapshot_id 不符",
                  lambda: _fact_with(locator=bad_snap_loc,
                                     payload_ref=_payload_ref(locator=bad_snap_loc)),
                  "locator.source_snapshot_id")
    expect_raises("authority 的 snapshot 与 source_snapshot_id 不符",
                  lambda: _fact_with(source_authority=_authority(snapshot_id="ext_other")),
                  "source_authority.source_snapshot_id")
    expect_raises("payload_ref.authority_identity 与 snapshot 来源身份不符",
                  lambda: _fact_with(payload_ref=_payload_ref(authority_identity="external_snapshot:ext_other")),
                  "authority_identity 与 snapshot 来源身份不一致")
    bad_url_loc = _locator(url="https://y.example/2")
    expect_raises("locator 的 canonical_url 与 canonical_url 不符",
                  lambda: _fact_with(locator=bad_url_loc,
                                     payload_ref=_payload_ref(locator=bad_url_loc)),
                  "locator.canonical_url")

    # -- external locator：证据/财务 locator 不得冒充 --
    ev_loc = _payload_ref().locator
    expect_raises("evidence locator 冒充 external locator",
                  lambda: _fact_with(locator=TS.EvidenceLocator(document_id="doc1", page=3)),
                  "必须为 ExternalLocator")
    check(isinstance(ev_loc, TS.ExternalLocator)
          and ev_loc.to_dict()["locator_type"] == "external_snapshot"
          and not isinstance(ev_loc, TS.EvidenceLocator),
          "external 身份只走 ExternalLocator（与 evidence/financial locator 不同型）")

    # -- source_authority 必须是 external carrier 的评估 --
    expect_raises("evidence authority 冒充 external authority",
                  lambda: _fact_with(source_authority=TS.EvidenceAuthorityAssessment(
                      evidence_id="ev1", verdict="authoritative")),
                  "必须为 ExternalSnapshotAuthorityAssessment")

    # -- payload_ref 载体类型 --
    expect_raises("payload_ref.object_type 非 external_snapshot",
                  lambda: _fact_with(payload_ref=_payload_ref(object_type="evidence_span")),
                  "object_type 必须为 'external_snapshot'")

    # -- 三方 body/content hash 闭环 --
    expect_raises("body_hash 与 content_hash 不符",
                  lambda: _fact_with(content_hash=_digest("other")), "与 content_hash")
    expect_raises("body_hash 与 authority.content_hash 不符",
                  lambda: _fact_with(source_authority=_authority(content_hash=_digest("other"))),
                  "source_authority.content_hash")
    expect_raises("body_hash 与 payload_ref.content_hash 不符",
                  lambda: _fact_with(payload_ref=_payload_ref(content_hash=_digest("other"))),
                  "payload_ref.content_hash")
    expect_raises("body_hash 非 64 位 hex",
                  lambda: _fact_with(body_hash="deadbeef"), "sha256 hex")

    # -- 来源权威必须已判 authoritative（D 级 / 未过时间资格不得成为 formal 事实）--
    for verdict in ("rejected", "supplemental_only"):
        expect_raises(f"authority verdict={verdict} 不得形成 formal ExternalFact",
                      lambda v=verdict: _fact_with(source_authority=_authority(verdict=v)),
                      "不得形成 formal ExternalFact")

    # -- SourcePolicy 版本 / 日期 / 命题 / aspect --
    expect_raises("缺 SourcePolicy 版本",
                  lambda: _fact_with(source_policy_version=""), "source_policy_version 必须非空")
    expect_raises("缺 as_of_date",
                  lambda: _fact_with(as_of_date=""), "as_of_date 必须非空")
    expect_raises("空命题",
                  lambda: _fact_with(statement=""), "statement 必须非空")
    expect_raises("空 aspect_ids",
                  lambda: _fact_with(aspect_ids=()), "aspect_ids 必须非空")
    expect_raises("缺 canonical_url",
                  lambda: _fact_with(canonical_url=""), "canonical_url 必须非空")

    # -- 事实身份的确定性派生 --
    same = TS.external_fact_id_for(_SNAP, "外部来源命题", _locator())
    check(same == fact.external_fact_id,
          "同一 snapshot + 命题 + locator ⇒ 同一 ExternalFact 身份")
    check(TS.external_fact_id_for(_SNAP, "另一条命题", _locator()) != fact.external_fact_id,
          "命题不同 ⇒ ExternalFact 身份不同")

    # -- factory 只接 eligible 决定；internal 候选不得产 external 事实 --
    _, rejected = _ext_chain("外部来源命题", verdict="rejected")
    cand, _ = _ext_chain("外部来源命题")
    expect_raises("build_external_fact 吃 rejected 决定",
                  lambda: TS.build_external_fact(
                      cand, rejected, statement="s", canonical_url=_URL, body_hash=_BODY_HASH,
                      source_policy_version=_POLICY_VERSION, as_of_date="2025-12-31",
                      locator=_locator(), payload_ref=_payload_ref(), content_hash=_BODY_HASH,
                      source_authority=_authority()),
                  "只接受 eligible 决定")
    t_cand, t_dec, _ = _topic_chain()
    expect_raises("topic_material 候选不得产 ExternalFact",
                  lambda: TS.build_external_fact(
                      t_cand, t_dec, statement="s", canonical_url=_URL, body_hash=_BODY_HASH,
                      source_policy_version=_POLICY_VERSION, as_of_date="2025-12-31",
                      locator=_locator(), payload_ref=_payload_ref(), content_hash=_BODY_HASH,
                      source_authority=_authority()),
                  "source_snapshot_id 必须非空")


# ---------------------------------------------------------------------------
# 3. 资格决定不得引用尚未形成的 result
# ---------------------------------------------------------------------------

def _check_decision_does_not_reference_result() -> None:
    _, dec = _ext_chain("外部来源命题")
    doc = dec.to_dict()
    check(not ({"fact_id", "external_fact_id", "text", "candidate_text"} & set(doc)),
          "FactQualificationDecision 的 wire 里没有任何 qualified-result 字段")
    # 决定身份**结构上**无法承载 qualified result：`DECISION_FORBIDDEN_KEYS` 与 wire 字段集
    # 不相交，因此这些键既进不了白名单、也无处可放 —— 双重防线，注入一律 fail-closed。
    check(set(TS.DECISION_FORBIDDEN_KEYS) & set(doc) == set(),
          "DECISION_FORBIDDEN_KEYS 与决定 wire 字段集不相交（结构上无法承载 result 身份）")
    for key in TS.DECISION_FORBIDDEN_KEYS:
        expect_raises(f"决定注入 result 身份字段 {key}",
                      lambda k=key: TS.FactQualificationDecision.from_dict({**doc, k: "x"}),
                      "未知字段")

    check(dec.decision_id == TS.derive_fact_qualification_decision_id(
        dec.candidate_id, dec.candidate_revision, dec.candidate_source_kind, dec.verdict,
        dec.rules_version),
        "决定身份由 candidate revision + verdict + 规则版本确定性派生")
    expect_raises("决定 id 与派生值不符",
                  lambda: TS.FactQualificationDecision.from_dict({**doc, "decision_id": "fqd_x"}),
                  "与确定性派生值")

    # 输入形状按来源类别封闭：两条路径不得互相携带对方的输入。
    expect_raises("external 决定携带 input_material_ids",
                  lambda: TS.FactQualificationDecision.from_dict(
                      {**doc, "input_material_ids": ["m1"]}),
                  "不得绑定 input_material_ids")
    expect_raises("external 决定缺 input_snapshot_id",
                  lambda: TS.FactQualificationDecision.from_dict(
                      {**doc, "input_snapshot_id": None}),
                  "必须绑定 input_snapshot_id")

    t_cand, t_dec, _ = _topic_chain()
    t_doc = t_dec.to_dict()
    expect_raises("topic 决定携带 input_snapshot_id",
                  lambda: TS.FactQualificationDecision.from_dict(
                      {**t_doc, "input_snapshot_id": _SNAP}),
                  "不得绑定 input_snapshot_id")
    expect_raises("topic 决定缺 input_material_ids",
                  lambda: TS.FactQualificationDecision.from_dict(
                      {**t_doc, "input_material_ids": []}),
                  "必须绑定输入 material 集合")
    check(t_dec.input_material_ids == tuple(t_cand.material_ids),
          "topic 决定的输入 material 集合取自候选（唯一口径）")

    # rejected 决定 = 该候选唯一的 typed rejection audit（必有理由）。
    expect_raises("rejected 决定缺 rejection_reason",
                  lambda: TS.FactQualificationDecision.from_dict(
                      {**doc, "verdict": "rejected", "decision_id":
                       TS.derive_fact_qualification_decision_id(
                           dec.candidate_id, dec.candidate_revision,
                           dec.candidate_source_kind, "rejected", dec.rules_version)}),
                  "必须携带非空 rejection_reason")
    expect_raises("eligible 决定携带 rejection_reason",
                  lambda: TS.FactQualificationDecision.from_dict(
                      {**doc, "rejection_reason": "x"}),
                  "不得携带 rejection_reason")


# ---------------------------------------------------------------------------
# 4. #37：eligible 决定 ↔ qualified result 的基数与 kind 闭合
# ---------------------------------------------------------------------------

def _check_qualified_result_cardinality() -> None:
    cand, dec = _ext_chain("外部来源命题")
    fact = _external_fact()

    TS.verify_candidate_decision_exact_set((cand,), (dec,))
    TS.verify_qualified_result_exact_set((cand,), (dec,), (), (fact,))
    check(True, "#37：1 candidate + 1 eligible 决定 + 1 ExternalFact 的精确集通过")

    # -- eligible external 决定必须恰一条 ExternalFact --
    expect_raises("eligible external 决定零条 result",
                  lambda: TS.verify_qualified_result_exact_set((cand,), (dec,), (), ()),
                  "必须恰有一条 ExternalFact")
    # 同 candidate 的第二条 result（改命题 ⇒ 新 external_fact_id，但仍是同一候选）。
    second = dataclasses.replace(fact, external_fact_id="ef_second", statement="另一条外部命题")
    expect_raises("eligible external 决定两条 result",
                  lambda: TS.verify_qualified_result_exact_set(
                      (cand,), (dec,), (), (fact, second)),
                  "必须恰有一条 ExternalFact")
    expect_raises("重复 external_fact_id",
                  lambda: TS.verify_qualified_result_exact_set(
                      (cand,), (dec,), (), (fact, _external_fact())),
                  "ExternalFact.external_fact_id")

    # -- internal / external result kind 不得交叉或双产 --
    t_cand, t_dec, t_fact = _topic_chain()
    fact_as_topic_cand = dataclasses.replace(
        t_fact, candidate_id=cand.candidate_id, candidate_revision=cand.candidate_revision)
    expect_raises("external 候选产 SupportedFact（跨 kind）",
                  lambda: TS.verify_qualified_result_exact_set(
                      (cand,), (dec,), (fact_as_topic_cand,), (fact,)),
                  "不得由 external_source 候选产生")
    ext_for_topic = dataclasses.replace(
        fact, candidate_id=t_cand.candidate_id, candidate_revision=t_cand.candidate_revision)
    expect_raises("topic 候选产 ExternalFact（跨 kind）",
                  lambda: TS.verify_qualified_result_exact_set(
                      (t_cand,), (t_dec,), (), (ext_for_topic,)),
                  "不得由 topic_material 候选产生")
    expect_raises("topic eligible 决定双产（SupportedFact + ExternalFact）",
                  lambda: TS.verify_qualified_result_exact_set(
                      (t_cand,), (t_dec,), (t_fact,), (ext_for_topic,)),
                  "不得由 topic_material 候选产生")
    expect_raises("topic eligible 决定零条 SupportedFact",
                  lambda: TS.verify_qualified_result_exact_set((t_cand,), (t_dec,), (), ()),
                  "必须恰有一条 SupportedFact")

    # -- rejected 决定零条 qualified result（rejection ≠ gap，也不产结果）--
    r_cand, r_dec = _ext_chain("被拒的外部命题", verdict="rejected")
    fact_for_rejected = dataclasses.replace(
        fact, candidate_id=r_cand.candidate_id, candidate_revision=r_cand.candidate_revision)
    expect_raises("rejected 决定仍产 result",
                  lambda: TS.verify_qualified_result_exact_set(
                      (r_cand,), (r_dec,), (), (fact_for_rejected,)),
                  "不得产生任何 qualified result")
    TS.verify_qualified_result_exact_set((r_cand,), (r_dec,), (), ())
    check(True, "rejected 决定零条 result 时通过（rejection 本身就是唯一 audit）")

    # -- 反解：result 必须回指同一 candidate 的 eligible 决定 --
    dangling = dataclasses.replace(fact, qualification_decision_id="fqd_missing")
    expect_raises("result 的资格决定不存在",
                  lambda: TS.verify_qualified_result_exact_set(
                      (cand,), (), (), (dangling,)),
                  "的资格决定不存在")
    a_cand, a_dec = _ext_chain("A 命题")
    b_cand, b_dec = _ext_chain("B 命题")
    a_fact = _external_fact("A 命题")
    b_fact = _external_fact("B 命题")
    swapped_a = dataclasses.replace(a_fact, qualification_decision_id=b_dec.decision_id)
    swapped_b = dataclasses.replace(b_fact, qualification_decision_id=a_dec.decision_id)
    expect_raises("result 交叉回指另一 candidate 的决定",
                  lambda: TS.verify_qualified_result_exact_set(
                      (a_cand, b_cand), (a_dec, b_dec), (), (swapped_a, swapped_b)),
                  "未回指其 eligible 决定")

    # -- candidate ↔ decision 的精确集也逐条闭合（revision / source_kind）--
    expect_raises("candidate 无资格决定",
                  lambda: TS.verify_candidate_decision_exact_set((cand,), ()),
                  "必须恰有一条资格决定")
    # 同一候选的第二条**合法**决定：verdict 不同 ⇒ 派生 id 不同（不能靠 replace 改 id）。
    dup_dec = TS.build_qualification_decision(
        cand, verdict="rejected", input_identity_digest=_digest("id"),
        input_source_identity=f"external_snapshot:{_SNAP}", input_locator_digest=_digest("loc"),
        input_payload_digest=_digest("pay"), input_snapshot_id=_SNAP,
        rejection_reason="同一候选的第二条决定")
    expect_raises("candidate 两条资格决定",
                  lambda: TS.verify_candidate_decision_exact_set((cand,), (dec, dup_dec)),
                  "必须恰有一条资格决定")
    expect_raises("决定引用不存在的 candidate",
                  lambda: TS.verify_candidate_decision_exact_set((), (dec,)),
                  "引用了不存在的 candidate")
    # revision 不一致：同 candidate_id、不同 revision 的第二条候选（问题集不同 ⇒ revision 变）。
    cand_other_rev = TS.build_fact_candidate(
        candidate_source_kind="external_source", statement="外部来源命题", fact_type="fact",
        aspect_ids=("a1",), question_ids=("q2",), source_snapshot_id=_SNAP)
    check(cand_other_rev.candidate_id == cand.candidate_id
          and cand_other_rev.candidate_revision != cand.candidate_revision,
          "同候选 id、不同 revision 的对照候选（仅问题集不同）")
    rev_dec = TS.build_qualification_decision(
        cand_other_rev, verdict="eligible", input_identity_digest=_digest("id"),
        input_source_identity=f"external_snapshot:{_SNAP}", input_locator_digest=_digest("loc"),
        input_payload_digest=_digest("pay"), input_snapshot_id=_SNAP)
    expect_raises("决定 revision 与候选不一致",
                  lambda: TS.verify_candidate_decision_exact_set((cand,), (rev_dec,)),
                  "revision")
    # source_kind 不一致：决定自报 topic_material 却挂在 external 候选的 id/revision 上。
    # candidate_id 派生式含 kind，故这条只能经 wire 构造（决定自身不校验 candidate 存在性）。
    wrong_kind_dec = TS.FactQualificationDecision.from_dict({
        "decision_id": TS.derive_fact_qualification_decision_id(
            cand.candidate_id, cand.candidate_revision, "topic_material", "eligible",
            TS.FACT_QUALIFICATION_VERSION),
        "candidate_id": cand.candidate_id, "candidate_revision": cand.candidate_revision,
        "candidate_source_kind": "topic_material", "verdict": "eligible",
        "rules_version": TS.FACT_QUALIFICATION_VERSION,
        "input_identity_digest": _digest("id"), "input_material_ids": ["m1"],
        "input_snapshot_id": None, "input_source_identity": "evidence:ev1",
        "input_locator_digest": _digest("loc"), "input_payload_digest": _digest("pay"),
        "rejection_reason": None})
    expect_raises("决定 source_kind 与候选不一致",
                  lambda: TS.verify_candidate_decision_exact_set((cand,), (wrong_kind_dec,)),
                  "candidate_source_kind")


def main() -> dict:
    _check_snapshot_is_carrier_only()
    _check_construction_gate()
    _check_decision_does_not_reference_result()
    _check_qualified_result_cardinality()
    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)

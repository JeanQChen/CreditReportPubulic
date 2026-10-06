"""Eval: FND successor（fnd-2）四类 authority union 与集合键/引用集合（§16.10 #26 / #31 / #39）。

用法: python -m evals.test_demo_fact_narrative_disposition

`FactNarrativeDisposition` successor 记录「一条**被选中的预验证权威事实**在正文里的去向」。
本文件只测**纯 schema 层**：四类 authority 的封闭 tagged union、集合键
`(authority_kind, container_identity, authority_specific_fact_id)` 的精确相等、三态语义，
以及 `claimed` / `supporting_only` 的「完整有序精确引用集合」。不调 LLM、不联网、不写文件、
不碰 Pack/Store/Writer 运行时。

如实登记的边界（**部分实现**，见 §16.7 C11）：

1. 3B 交付的是 **wire + 两个纯重算校验器**（`verify_fnd_key_set` /
   `verify_fnd_reference_sets`）。它们把「选中的权威事实键集」「Claim→binding 映射」
   「binding→role 映射」作为**参数**接收，自己**不**从 authority input 派生任何东西。
   门后 coordinator 的确定性派生（P15）与 verifier 侧的逐条重算（P14）属于 3D：
   本文件不声称它们已实现，也没有一条断言会把「尚未实现」说成「已实现」。
2. `supporting_only` 的「所有 usage 全为 corroborating」在本层只能按**调用方给出的**
   `binding_roles` 重算；真实链上 role 来自 accepted binding（3C）。因此这里测的是
   「这条规则真的在重算里执行」，不是「真实链上的 role 已被核对」。
3. 本文件不覆盖 RMD / WMPD 的字段面（那在 `test_demo_material_dispositions` #12），
   只断言 FND 这一侧**没有**它们独有的轴，也**不是** gap / rejection audit。
"""

from __future__ import annotations

import inspect
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sections import narrative_schema as NS  # noqa: E402

#: 四类 authority 的合格种子（各自带 authority 专属 fact 字段与 qualification/validation ref）。
_SEEDS = {
    "topic_pack": dict(authority_kind="topic_pack", authority_container_id="pack1",
                       pack_id="pack1", fact_id="f1",
                       fact_qualification_decision_id="fqd1"),
    "financial_pack": dict(authority_kind="financial_pack", authority_container_id="fp1",
                           financial_fact_id="ff1",
                           financial_selection_rule_version="fsr1"),
    "evidence_note": dict(authority_kind="evidence_note", authority_container_id="note1",
                          note_fact_id="nf1", note_validation_rule_version="nvr1",
                          locator_ref=NS.char_range_locator("ev1", 10, 20)),
    # 容器身份用**真实的**派生函数（容器 ≠ 事实身份），不手写前缀。
    "external_snapshot": dict(
        authority_kind="external_snapshot",
        authority_container_id=NS.external_authority_container_id(
            types.SimpleNamespace(source_snapshot_id="snap1")),
        pack_id="pack1", external_fact_id="ef1",
        fact_qualification_decision_id="fqd2", locator_ref=NS.char_range_locator("extdoc1", 0, 12),
        payload_ref={"snapshot_id": "snap1", "canonical_url": "https://example.test/a",
                     "body_hash": "b" * 64, "source_policy_version": "sp-1",
                     "as_of_date": "2026-01-31"}),
}
_FACT_FIELD = dict(NS.FACT_FIELD_BY_AUTHORITY_KIND)


def _base(**kw) -> dict:
    """合格基线：claimed + 一条 accepted binding + 一条 SectionClaim。"""
    body = dict(disposition="claimed", required=True, source_identity="evidence:ev1",
                provenance_identity="prov1", content_fingerprint="c" * 64,
                accepted_binding_ids=("ab1",), section_claim_ids=("cl1",))
    body.update(kw)
    return body


def _fnd(*, drop: tuple[str, ...] = (), **kw) -> NS.FactNarrativeDisposition:
    """按 kind 取完整种子，叠加 `kw` 覆盖，再 `drop` 掉指定字段（缺字段反例专用）。"""
    kind = str(kw.get("authority_kind") or "topic_pack")
    seed = dict(_SEEDS.get(kind, _SEEDS["topic_pack"]))
    body = _base()
    body.update(kw)
    seed.update(body)
    for name in drop:
        seed.pop(name, None)
    return NS.FactNarrativeDisposition.create(**seed)


def _without(seed: dict, *fields: str) -> dict:
    return {k: v for k, v in seed.items() if k not in fields}


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

    def expect_error(fn, exc, msg: str, *, needle: str = "") -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc as e:
            if needle and needle not in str(e):
                failed += 1
                details.append(f"FAIL {msg}：原因不符（{str(e)[:160]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:160]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    # ============================================================ §1 四类 authority union
    check(NS.FND_SCHEMA_VERSION == "fnd-2",
          f"successor 的 wire 版本必须是 fnd-2（实际 {NS.FND_SCHEMA_VERSION!r}）")
    # FND 与当前 narrative wire **同族**（门后束的成员载荷都挂在同一节写作链上）。`narr-7` 给
    # `SectionDraft` 加了门前自然草稿层、`narr-8` 又给草稿的出处拆出第二条轴（`source_fact_refs`），
    # 两版**都没有**改动 `FactNarrativeDisposition` 的键集，所以 `fnd-2` 不动；但 current 叙述版本
    # 必须与五个 legacy 版本都不重叠。
    check(NS.NARRATIVE_SCHEMA_VERSION == "narr-8"
          and NS.NARRATIVE_SCHEMA_VERSION not in NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS
          and NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS
          == ("narr-3", "narr-4", "narr-5", "narr-6", "narr-7"),
          f"门后束所在的叙述 wire 必须是 narr-8（实际 {NS.NARRATIVE_SCHEMA_VERSION!r}），"
          f"narr-3 / narr-4 / narr-5 / narr-6 / narr-7 已转 legacy（实际 "
          f"{NS.LEGACY_NARRATIVE_SCHEMA_VERSIONS!r}）")

    wire_key_sets: dict[str, set[str]] = {}
    for kind, seed in _SEEDS.items():
        disp = _fnd(**seed)
        wire_key_sets[kind] = set(disp.to_dict())
        check(disp.authority_specific_fact_id == seed[_FACT_FIELD[kind]],
              f"{kind}：authority 专属 fact 身份必须由 {_FACT_FIELD[kind]} 唯一映射")
        check(disp.container_identity == seed["authority_container_id"],
              f"{kind}：容器身份就是 authority_container_id")
        check(disp.disposition_key == (kind, seed["authority_container_id"],
                                       seed[_FACT_FIELD[kind]]),
              f"{kind}：集合键 = (authority_kind, container_identity, authority 专属 fact)")
        back = NS.FactNarrativeDisposition.from_dict(disp.to_dict())
        check(back.disposition_id == disp.disposition_id
              and back.disposition_key == disp.disposition_key,
              f"{kind}：to_dict → from_dict 必须回到同一条去向")
        check(disp.identity_body()["schema_version"] == NS.FND_SCHEMA_VERSION,
              f"{kind}：身份体必须显式携带 successor 版本号")
    check(len({frozenset(v) for v in wire_key_sets.values()}) == 1,
          "四类 authority 的 wire 键集必须完全相同（同一个 tagged union，不是四份载荷）："
          f"{ {k: sorted(v) for k, v in wire_key_sets.items()} }")
    check(not ({"container_identity", "authority_specific_fact_id", "disposition_key"}
               & wire_key_sets["topic_pack"]),
          "集合键访问器是派生的，不落进 wire（避免第二份真值）")

    # -- 缺字段：authority 专属 fact 身份 / qualification-validation authority ref --
    for kind in _SEEDS:
        fact_field = _FACT_FIELD[kind]
        expect_error(lambda k=kind, f=fact_field: _fnd(authority_kind=k, drop=(f,)),
                     NS.NarrativeSchemaError, f"{kind}：缺 {fact_field} 必须被拒",
                     needle=fact_field)
        ref_field = NS.FND_AUTHORITY_REF_BY_KIND[kind]
        expect_error(lambda k=kind, f=ref_field: _fnd(authority_kind=k, drop=(f,)),
                     NS.NarrativeSchemaError, f"{kind}：缺 {ref_field} 必须被拒",
                     needle=ref_field)
    check(set(NS.FND_AUTHORITY_REF_BY_KIND) == set(NS.AUTHORITY_KINDS),
          "四类 authority 各自恰有一个 qualification/validation authority ref 槽位")

    # -- 混装：跨 kind 的 fact 字段 / 跨 kind 的 authority ref / 未登记 kind --
    expect_error(lambda: _fnd(**{**_SEEDS["topic_pack"], "external_fact_id": "ef9"}),
                 NS.NarrativeSchemaError, "topic_pack 携带 external_fact_id 必须被拒",
                 needle="authority kind 混装")
    expect_error(lambda: _fnd(**{**_SEEDS["financial_pack"], "note_fact_id": "nf9"}),
                 NS.NarrativeSchemaError, "financial_pack 携带 note_fact_id 必须被拒",
                 needle="authority kind 混装")
    expect_error(lambda: _fnd(**{**_SEEDS["financial_pack"],
                                 "fact_qualification_decision_id": "fqd9"}),
                 NS.NarrativeSchemaError, "financial_pack 携带资格决定必须被拒",
                 needle="authority kind 混装")
    expect_error(lambda: _fnd(**{**_SEEDS["evidence_note"],
                                 "financial_selection_rule_version": "fsr9"}),
                 NS.NarrativeSchemaError, "evidence_note 携带选材规则版本必须被拒",
                 needle="authority kind 混装")
    expect_error(lambda: _fnd(**{**_SEEDS["external_snapshot"],
                                 "note_validation_rule_version": "nvr9"}),
                 NS.NarrativeSchemaError, "external_snapshot 携带附注核验版本必须被拒",
                 needle="authority kind 混装")
    expect_error(lambda: _fnd(authority_kind="internal_memo"),
                 NS.NarrativeSchemaError, "未登记的 authority kind 必须被拒",
                 needle="authority_kind")
    # 封闭 union ≠ 泛化逃逸字段：没有 `qualification_ref` / `authority_ref` 这类通用槽位。
    check(not ({"qualification_ref", "authority_ref", "fact_ref", "authority_identity",
                "authority_ref_kind"} & set(NS.FactNarrativeDisposition.__dataclass_fields__)),
          "FND 不得有泛化的 authority/qualification 逃逸字段（封闭 tagged union）")
    expect_error(lambda: NS.FactNarrativeDisposition.from_dict(
        {**_fnd().to_dict(), "qualification_ref": "q1"}),
        NS.NarrativeSchemaError, "读回泛化 qualification_ref 必须被拒", needle="未登记字段")

    # -- topic_pack / external_snapshot 的容器身份与事实身份必须分开表达 --
    expect_error(lambda: _fnd(**{**_SEEDS["topic_pack"], "pack_id": "pack2"}),
                 NS.NarrativeSchemaError, "topic_pack 容器与 pack_id 不一致必须被拒",
                 needle="topic_pack 的容器就是 Pack 自己")
    expect_error(lambda: _fnd(**{**_SEEDS["external_snapshot"],
                                 "authority_container_id": "pack1"}),
                 NS.NarrativeSchemaError, "external 容器退化成 Pack 必须被拒",
                 needle="current Pack 分开表达")
    expect_error(lambda: _fnd(**{**_SEEDS["topic_pack"], "locator_ref": NS.char_range_locator("doc1", 0, 5)}),
                 NS.NarrativeSchemaError, "topic_pack 携带 locator 必须被拒",
                 needle="不得携带 locator_ref")
    expect_error(lambda: _fnd(**{**_SEEDS["topic_pack"], "payload_ref": {"page": 1}}),
                 NS.NarrativeSchemaError, "topic_pack 无 material 却有 payload 必须被拒",
                 needle="payload_ref 只在绑定 material_id 时允许")
    _fnd(**{**_SEEDS["topic_pack"], "material_id": "m1"})
    check(True, "topic_pack 带 material 但不带 payload 是合法的（上游无 payload 不是漏绑）")
    # -- note / snapshot 的定位与载体 --
    expect_error(lambda: _fnd(authority_kind="evidence_note", drop=("locator_ref",)),
                 NS.NarrativeSchemaError, "evidence_note 缺 exact locator 必须被拒",
                 needle="必须绑定 exact locator")
    expect_error(lambda: _fnd(authority_kind="external_snapshot", drop=("payload_ref",)),
                 NS.NarrativeSchemaError, "external_snapshot 缺 snapshot 载体必须被拒",
                 needle="必须绑定 snapshot payload_ref")
    broken_ext = dict(_SEEDS["external_snapshot"])
    broken_ext["payload_ref"] = _without(broken_ext["payload_ref"], "body_hash")
    expect_error(lambda: _fnd(**broken_ext), NS.NarrativeSchemaError,
                 "snapshot 载体缺 body_hash 必须被拒", needle="snapshot ref 缺字段")

    # ============================================================ §2 集合键精确相等
    one, two = _fnd(**_SEEDS["topic_pack"]), _fnd(**{**_SEEDS["topic_pack"], "fact_id": "f2"})
    other_container = _fnd(**{**_SEEDS["topic_pack"], "authority_container_id": "pack2",
                              "pack_id": "pack2"})
    check(other_container.disposition_key != one.disposition_key
          and other_container.authority_specific_fact_id == one.authority_specific_fact_id,
          "跨容器同名 fact 是**两条**事实（裸 fact id 不足以区分解重）")
    keys = NS.verify_fnd_key_set([one.disposition_key, two.disposition_key], [one, two])
    check(tuple(keys) == (one.disposition_key, two.disposition_key),
          "键集相等时返回声明的键序（升序）")
    expect_error(lambda: NS.verify_fnd_key_set([one.disposition_key], [one, two]),
                 NS.NarrativeSchemaError, "选中键集缺一条必须被拒", needle="不相等")
    expect_error(lambda: NS.verify_fnd_key_set(
        [one.disposition_key, two.disposition_key, other_container.disposition_key],
        [one, two]), NS.NarrativeSchemaError, "选中键集多一条必须被拒", needle="不相等")
    expect_error(lambda: NS.verify_fnd_key_set(["f1", "f2"], [one, two]),
                 NS.NarrativeSchemaError, "裸 fact ID 作集合键必须被拒",
                 needle="不得使用裸 fact ID")
    expect_error(lambda: NS.verify_fnd_key_set([("topic_pack", "pack1")], [one]),
                 NS.NarrativeSchemaError, "两格键必须被拒", needle="必须是三格非空三元组")
    expect_error(lambda: NS.verify_fnd_key_set(
        [one.disposition_key, one.disposition_key], [one]),
        NS.NarrativeSchemaError, "选中侧重复键必须被拒", needle="集合键重复")
    expect_error(lambda: NS.verify_fnd_key_set([one.disposition_key], [one, one]),
                 NS.NarrativeSchemaError, "FND 侧重复必须被拒", needle="集合键重复")
    # 直接测「裸 id 去重」这条风险的后果：若调用方按裸 fact id 建集合，跨容器的两条同名
    # 事实会被折叠成一条，于是 FND 侧多出的那条会被误报为「多」而不是「缺」。
    expect_error(lambda: NS.verify_fnd_key_set(
        [one.disposition_key], [one, other_container]),
        NS.NarrativeSchemaError, "跨容器同名 fact 不得被折叠", needle="不相等")

    # ============================================================ §3 三态语义与引用集合
    expect_error(lambda: _fnd(**{**_SEEDS["topic_pack"], "accepted_binding_ids": ()}),
                 NS.NarrativeSchemaError, "claimed 无 binding 必须被拒",
                 needle="至少一条 factual accepted binding")
    expect_error(lambda: _fnd(**{**_SEEDS["topic_pack"], "section_claim_ids": ()}),
                 NS.NarrativeSchemaError, "claimed 无 Claim 必须被拒",
                 needle="至少一条 SectionClaim")
    expect_error(lambda: _fnd(**{**_SEEDS["topic_pack"], "reason_code": "covered_by_other_fact"}),
                 NS.NarrativeSchemaError, "claimed 带 reason_code 必须被拒",
                 needle="不得携带 reason_code")
    supporting = _fnd(**{**_SEEDS["topic_pack"], "disposition": "supporting_only",
                         "section_claim_ids": (),
                         "reason_code": "covered_by_other_fact", "required": False})
    check(supporting.disposition == "supporting_only"
          and supporting.accepted_binding_ids == ("ab1",) and not supporting.section_claim_ids,
          "supporting_only：有 accepted binding、无 Claim")
    expect_error(lambda: _fnd(**{**_SEEDS["topic_pack"], "disposition": "supporting_only",
                                 "required": False, "reason_code": "covered_by_other_fact"}),
                 NS.NarrativeSchemaError, "supporting_only 带 Claim 必须被拒",
                 needle="不得绑定 SectionClaim")
    expect_error(lambda: _fnd(**{**_SEEDS["topic_pack"], "disposition": "supporting_only",
                                 "required": False, "section_claim_ids": ()}),
                 NS.NarrativeSchemaError, "supporting_only 缺 reason_code 必须被拒",
                 needle="必须给出登记过的 reason_code")
    not_presented = _fnd(**{**_SEEDS["topic_pack"], "disposition": "not_presented_with_reason",
                            "required": False, "accepted_binding_ids": (),
                            "section_claim_ids": (), "reason_code": "outside_narrative_scope"})
    check(not not_presented.accepted_binding_ids and not not_presented.section_claim_ids,
          "not_presented_with_reason：不引用任何 binding / Claim")
    expect_error(lambda: _fnd(**{**_SEEDS["topic_pack"],
                                 "disposition": "not_presented_with_reason",
                                 "required": False, "reason_code": "outside_narrative_scope",
                                 "section_claim_ids": ()}),
                 NS.NarrativeSchemaError, "not_presented 仍引用 binding 必须被拒",
                 needle="不得引用任何 accepted binding")
    expect_error(lambda: _fnd(**{**_SEEDS["topic_pack"],
                                 "disposition": "not_presented_with_reason",
                                 "required": False, "accepted_binding_ids": (),
                                 "section_claim_ids": ()}),
                 NS.NarrativeSchemaError, "not_presented 缺 reason_code 必须被拒",
                 needle="必须给出登记过的 reason_code")

    # -- claimed 的引用集合必须**完整有序精确** --
    disp = _fnd(**{**_SEEDS["topic_pack"], "accepted_binding_ids": ("ab1", "ab2"),
                   "section_claim_ids": ("cl1",)})
    NS.verify_fnd_reference_sets(disp, accepted_binding_ids=("ab1", "ab2"),
                                 section_claim_ids=("cl1",),
                                 claim_binding_ids={"cl1": ("ab1", "ab2")},
                                 binding_roles={"ab1": "primary", "ab2": "corroborating"})
    passed += 1
    expect_error(lambda: NS.verify_fnd_reference_sets(
        disp, accepted_binding_ids=("ab1",), section_claim_ids=("cl1",),
        claim_binding_ids={"cl1": ("ab1", "ab2")}),
        NS.NarrativeSchemaError, "漏声明一条 binding 必须被拒", needle="完整有序精确集合")
    expect_error(lambda: NS.verify_fnd_reference_sets(
        disp, accepted_binding_ids=("ab1", "ab2", "ab3"), section_claim_ids=("cl1",),
        claim_binding_ids={"cl1": ("ab1", "ab2")}),
        NS.NarrativeSchemaError, "多声明一条 binding 必须被拒", needle="完整有序精确集合")
    expect_error(lambda: NS.verify_fnd_reference_sets(
        disp, accepted_binding_ids=("ab1", "ab2"), section_claim_ids=(),
        claim_binding_ids={"cl1": ("ab1", "ab2")}),
        NS.NarrativeSchemaError, "漏声明 Claim 必须被拒", needle="完整有序精确集合")
    expect_error(lambda: NS.verify_fnd_reference_sets(
        disp, accepted_binding_ids=("ab1", "ab2"), section_claim_ids=("cl1",),
        claim_binding_ids={}),
        NS.NarrativeSchemaError, "引用不存在的 Claim 必须被拒", needle="不存在的 SectionClaim")
    expect_error(lambda: NS.verify_fnd_reference_sets(
        disp, accepted_binding_ids=("ab1", "ab2"), section_claim_ids=("cl1",),
        claim_binding_ids={"cl1": ("ab1", "ab2", "ab3")}),
        NS.NarrativeSchemaError, "Claim 参与的 binding 未声明（反向遗漏）必须被拒",
        needle="漏声明了 Claim")
    expect_error(lambda: NS.verify_fnd_reference_sets(
        disp, accepted_binding_ids=("ab1", "ab2"), section_claim_ids=("cl1",),
        claim_binding_ids={"cl1": ("ab1",)}),
        NS.NarrativeSchemaError, "声明的 binding 无法解析到所列 Claim 必须被拒",
        needle="无法解析到所列 Claim")
    # supporting_only：不得作为任何 Claim 的 primary authority；全为 corroborating 才通过。
    supporting2 = _fnd(**{**_SEEDS["topic_pack"], "disposition": "supporting_only",
                          "section_claim_ids": (),
                          "reason_code": "covered_by_other_fact", "required": False,
                          "accepted_binding_ids": ("ab1", "ab2")})
    NS.verify_fnd_reference_sets(supporting2, accepted_binding_ids=("ab1", "ab2"),
                                 section_claim_ids=(),
                                 binding_roles={"ab1": "corroborating",
                                                "ab2": "corroborating"})
    passed += 1
    expect_error(lambda: NS.verify_fnd_reference_sets(
        supporting2, accepted_binding_ids=("ab1", "ab2"), section_claim_ids=(),
        binding_roles={"ab1": "primary", "ab2": "corroborating"}),
        NS.NarrativeSchemaError, "supporting_only 含 primary usage 必须被拒",
        needle="必须全为 corroborating")
    expect_error(lambda: NS.verify_fnd_reference_sets(
        supporting2, accepted_binding_ids=("ab1", "ab2"), section_claim_ids=("cl1",)),
        NS.NarrativeSchemaError, "supporting_only 被核出 Claim 引用必须被拒",
        needle="完整有序精确集合")
    expect_error(lambda: NS.verify_fnd_reference_sets(
        {"disposition": "claimed"}, accepted_binding_ids=(), section_claim_ids=()),
        NS.NarrativeSchemaError, "校验器只接受 FND 对象", needle="只接受 FactNarrativeDisposition")

    # ============================================================ §4 FND 不是别的身份
    fnd_fields = set(NS.FactNarrativeDisposition.__dataclass_fields__)
    check(not ({"admission_state", "retention_state", "source_validation", "reason_proof"}
               & fnd_fields),
          "FND 没有研究侧材料准入/保留/来源校验轴（不是 RMD）")
    check(not ({"used", "not_used", "processing_state", "member_ref", "writer_draft_id",
                "manifest_member_ref"} & fnd_fields),
          "FND 没有 Writer 侧材料处理轴（不是 WMPD）")
    check(not ({"gap_id", "contract_gap_id", "detail", "unmet_required_items", "block_id",
                "rejection_audit_id"} & fnd_fields),
          "FND 不是 gap / rejection audit（「未呈现」是去向，不是缺口：FND 不得替代它们）")
    check(not ({"claim_text", "paragraph_id", "sentence_text", "citation_refs"} & fnd_fields),
          "FND 不是正文/引用本身：它只登记去向，不携带 Claim 文本或 citation")
    # 纯重算校验器：不接收 authority input、不自行派生（派生在 3D 的门后 coordinator）。
    params = set(inspect.signature(NS.verify_fnd_key_set).parameters)
    check(params == {"selected_keys", "dispositions", "what"},
          f"键集校验器只接收两个集合 + 说明串（实际 {sorted(params)}）")
    params = set(inspect.signature(NS.verify_fnd_reference_sets).parameters)
    check(params == {"disp", "accepted_binding_ids", "section_claim_ids",
                     "claim_binding_ids", "binding_roles"},
          f"引用集校验器只重算调用方给出的集合（实际 {sorted(params)}）")

    # 身份随内容变化：改一条引用集必得新 disposition_id；输入顺序不影响身份。
    a = _fnd(**{**_SEEDS["topic_pack"], "accepted_binding_ids": ("ab1",)})
    b = _fnd(**{**_SEEDS["topic_pack"], "accepted_binding_ids": ("ab2",)})
    check(a.disposition_id != b.disposition_id,
          "引用集合不同必得不同 disposition_id（去向身份绑定引用集合）")
    check(_fnd(**{**_SEEDS["topic_pack"], "accepted_binding_ids": ("ab2", "ab1")}).disposition_id
          == _fnd(**{**_SEEDS["topic_pack"], "accepted_binding_ids": ("ab1", "ab2")}).disposition_id,
          "create 会把引用集合规范排序：同一集合的不同输入顺序得到同一身份")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

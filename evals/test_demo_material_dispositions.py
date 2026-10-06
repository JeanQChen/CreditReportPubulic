"""Eval: 材料处置三套身份与 successor 基数（§16.10 #12 / #28 / #37，M930-3A）。

用法: python -m evals.test_demo_material_dispositions

证明：
 1. RMD（研究侧）只承载「准入 / 保留 / 来源校验」三轴，**不含** used / not_used；
    它的 container / source / provenance / content 身份由 material 确定性重算，不靠自报。
 2. #12：RMD / WMPD / FND 是三套身份 —— 类型不同、准入/保留/校验轴与权威/引用轴互不
    出现在对方 wire、身份域不重叠、持久化home不同（`current_section_wmpd_v2` /
    `current_section_fnd_v2` 各自基数键）；任一方向互相读回都 fail-closed。
    FND successor（fnd-2）之后两者**共用** source/provenance/content 三个词的词汇，
    因此该性质改由「容器身份的存储/派生之别 + 取值不同域 + 字段缺失面」证明，
    不再靠字段名清单（见 `_RESEARCH_ONLY_AXES` 处的说明）。
 3. #28：`DEPENDENCY_VERSION_KEYS` 精确 18 键（新增 material_disposition /
    fact_qualification / external_fact），缺键 / 多余键 / 自动补全一律拒。
 4. #37：material→RMD、candidate→决定、eligible 决定→qualified result 三类基数由唯一
    四门入口 `verify_pack_successor_sets` 逐条重算；六族 successor 字段必须显式传入。
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_schema as TS  # noqa: E402
from sections import narrative_schema as NS  # noqa: E402
from sections import store as ST  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

# Writer 侧独有的轴（`used` / `not_used` 属于 WMPD，不得出现在 RMD 上）。
_WRITER_AXIS_FIELDS = ("used", "not_used", "processing_state", "member_ref", "writer_draft_id",
                       "manifest_member_ref")
# 研究报告侧独有的轴（不得出现在 FND 上）：这三轴说的是**材料能否准入 / 是否保留 /
# 来源校验结论**，FND 说的是「一条**预验证权威事实**在正文里的去向」——后者没有准入语义。
#
# 注意 successor（fnd-2）之后，`source_identity` / `provenance_identity` /
# `content_fingerprint` / `material_id` 是**两套身份共用的词汇**（P1-8 明确要求 FND 自带
# source/provenance identity），因此不能再靠「字段名不相交」证明两者不互相冒充；
# 改由**值域与可达性**证明：RMD 的 `container_identity` 是**存储字段**（材料容器），
# FND 的 `container_identity` 是**派生属性**（权威容器 = `authority_container_id`），
# 两者在类型层就不是同一个东西，且任一方向读回都 fail-closed。
_RESEARCH_ONLY_AXES = ("admission_state", "retention_state", "source_validation",
                       "reason_proof", "policy_version")


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def expect_raises(msg: str, fn, needle: str, exc_type: type[Exception] = TS.SchemaValidationError
                  ) -> None:
    try:
        fn()
    except exc_type as exc:
        if needle in str(exc):
            check(True, f"{msg}：{str(exc)[:70]}")
        else:
            check(False, f"{msg}：期望理由含 {needle!r}，实际 {str(exc)[:90]}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"{msg}：期望 {exc_type.__name__}，实际 {type(exc).__name__}: {exc}")
    else:
        check(False, f"{msg}：未 fail-closed")


def _sha(tag: str) -> str:
    return TS.sha256_canonical({"fixture": tag})


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def _material(material_id: str = "m1") -> TS.ResearchMaterial:
    loc = TS.EvidenceLocator(document_id="doc1", page=3)
    auth = TS.EvidenceAuthorityAssessment(
        evidence_id="ev1", document_id="doc1", page=3, content_hash=_sha("ev1"),
        verdict="authoritative")
    return TS.ResearchMaterial(
        material_id=material_id, material_type="evidence_span", source_identity="evidence:ev1",
        locator=loc,
        payload_ref=TS.MaterialPayloadRef(
            object_type="evidence_span", authority_identity="evidence:ev1", version="v1",
            content_hash=_sha("payload"), locator=loc,
            created_dependency_fingerprint=_sha("cdep")),
        content_hash=_sha("payload"), authority_assessment=auth)


def _rmd(material: TS.ResearchMaterial | None = None, *, aspect_ids: tuple[str, ...] = ("a1",),
         admission_state: str = "admitted", retention_state: str = "retained",
         source_validation: str = "validated", reason_code: str = "aspect_material_admitted",
         reason_proof: str = "aspect a1 需要该材料", policy_version: str = "md-1"
         ) -> TS.ResearchMaterialDisposition:
    return TS.build_material_disposition(
        material or _material(), aspect_ids=aspect_ids, admission_state=admission_state,
        retention_state=retention_state, source_validation=source_validation,
        reason_code=reason_code, reason_proof=reason_proof, policy_version=policy_version)


def _fnd(*, disposition: str = "claimed", section_claim_ids: tuple[str, ...] = ("c1",),
         accepted_binding_ids: tuple[str, ...] = ("ab1",), reason_code: str | None = None,
         required: bool = False, fact_id: str = "sf_1",
         qualification_decision_id: str = "fqd1") -> NS.FactNarrativeDisposition:
    """FND successor（fnd-2）夹具：topic_pack 分支 + 一个合格的三态去向。"""
    return NS.FactNarrativeDisposition.create(
        authority_kind="topic_pack", authority_container_id="pack1", pack_id="pack1",
        fact_id=fact_id, fact_qualification_decision_id=qualification_decision_id,
        disposition=disposition, required=required, section_claim_ids=section_claim_ids,
        accepted_binding_ids=accepted_binding_ids, reason_code=reason_code,
        source_identity="evidence:ev1", provenance_identity=_sha("prov"),
        content_fingerprint=_sha("content"))


def _ext_chain(statement: str = "外部命题") -> tuple[TS.FactCandidate, TS.FactQualificationDecision]:
    cand = TS.build_fact_candidate(
        candidate_source_kind="external_source", statement=statement, fact_type="fact",
        aspect_ids=("a1",), question_ids=("q1",), source_snapshot_id="ext1")
    dec = TS.build_qualification_decision(
        cand, verdict="eligible", input_identity_digest=_sha("id"),
        input_source_identity="external_snapshot:ext1", input_locator_digest=_sha("loc"),
        input_payload_digest=_sha("pay"), input_snapshot_id="ext1")
    return cand, dec


class _PackView:
    """四门入口只需要六个属性；用最小 stand-in 直测入口本身（不构造整包）。"""

    def __init__(self, **kw) -> None:
        self.materials = kw.get("materials", ())
        self.material_dispositions = kw.get("material_dispositions", ())
        self.fact_candidates = kw.get("fact_candidates", ())
        self.fact_qualification_decisions = kw.get("fact_qualification_decisions", ())
        self.facts = kw.get("facts", ())
        self.external_facts = kw.get("external_facts", ())


# ---------------------------------------------------------------------------
# 1. RMD：研究侧三轴 + 确定性身份
# ---------------------------------------------------------------------------

def _check_rmd_identity() -> None:
    mat = _material()
    d = _rmd(mat)
    check(isinstance(d, TS.ResearchMaterialDisposition)
          and d.material_id == mat.material_id
          and d.admission_state == "admitted" and d.retention_state == "retained"
          and d.source_validation == "validated",
          "RMD 只声明 material 的三轴结论（admission / retention / source_validation）")
    check(d.container_identity == TS.material_container_identity(mat)
          and d.provenance_identity == TS.material_provenance_identity(mat)
          and d.content_fingerprint == TS.material_content_fingerprint(mat)
          and d.source_identity == mat.source_identity,
          "RMD 的 container / provenance / content / source 身份由 material 确定性重算")
    check(TS.ResearchMaterialDisposition.from_dict(d.to_dict()).to_dict() == d.to_dict(),
          "RMD 往返一致")

    check(d.disposition_id == _rmd(mat).disposition_id,
          "同一 material + 三轴 + 理由码 + 规则版本 ⇒ 同一 RMD 身份")
    other_reason = _rmd(mat, admission_state="rejected", retention_state="dropped",
                        source_validation="rejected", reason_code="authority_rejected",
                        reason_proof="来源权威 rejected")
    check(other_reason.disposition_id != d.disposition_id,
          "结论/理由码不同 ⇒ RMD 身份不同（身份含判定结论，不是材料身份的别名）")
    expect_raises("RMD disposition_id 与派生值不符",
                  lambda: TS.ResearchMaterialDisposition.from_dict(
                      {**d.to_dict(), "disposition_id": "rmd_x"}),
                  "与确定性派生值")

    # -- 研究侧轴**不含** used / not_used --
    doc = d.to_dict()
    check(not (set(_WRITER_AXIS_FIELDS) & set(doc)),
          "RMD wire 不含 Writer 侧 used / not_used / processing 轴")
    check(not ({"used", "not_used"} & (set(TS.MATERIAL_ADMISSION_STATES)
                                       | set(TS.MATERIAL_RETENTION_STATES)
                                       | set(TS.MATERIAL_SOURCE_VALIDATIONS)
                                       | set(TS.MATERIAL_DISPOSITION_REASON_CODES))),
          "used / not_used 不是研究侧任何一轴的取值（不得冒充研究结论）")
    # rejected 的 RMD 仍是「恰一条处置」，不是 rejection audit、也不自动造 gap。
    check(not isinstance(other_reason, TS.ContractGap)
          and not hasattr(other_reason, "gap_id")
          and not hasattr(other_reason, "block_id"),
          "RMD 不是 gap / block（rejected 只是材料侧结论，不自动产生 ContractGap/ResearchBlock）")
    check(other_reason.admission_state == "rejected" and other_reason.retention_state == "dropped",
          "rejected 材料仍以普通 RMD 表达（不另造第五种权威对象）")

    # -- 枚举 / 必填 fail-closed --
    expect_raises("未知 admission_state",
                  lambda: _rmd(admission_state="maybe"), "admission_state")
    expect_raises("未知 retention_state",
                  lambda: _rmd(retention_state="kept"), "retention_state")
    expect_raises("未知 source_validation",
                  lambda: _rmd(source_validation="unknown"), "source_validation")
    expect_raises("未知 reason_code",
                  lambda: _rmd(reason_code="not_used"), "reason_code")
    expect_raises("空 reason_proof",
                  lambda: _rmd(reason_proof=""), "reason_proof 必须非空")
    expect_raises("空 policy_version",
                  lambda: _rmd(policy_version=""), "policy_version 必须非空")
    expect_raises("非 hex content_fingerprint",
                  lambda: TS.ResearchMaterialDisposition.from_dict(
                      {**doc, "content_fingerprint": "nothex"}),
                  "sha256 hex")


# ---------------------------------------------------------------------------
# 2. #12：RMD / WMPD / FND 不得互相冒充
# ---------------------------------------------------------------------------

def _check_three_identities() -> None:
    rmd = _rmd()
    fnd = _fnd()

    # -- 类型与身份域 --
    check(TS.ResearchMaterialDisposition is not NS.FactNarrativeDisposition
          and not issubclass(TS.ResearchMaterialDisposition, NS.FactNarrativeDisposition)
          and not issubclass(NS.FactNarrativeDisposition, TS.ResearchMaterialDisposition),
          "RMD 与 FND 是两套互不继承的身份类型")
    check(TS.ResearchMaterialDisposition.__module__.startswith("harness")
          and NS.FactNarrativeDisposition.__module__.startswith("sections"),
          "RMD 属研究侧边界（harness），FND 属 Writer 侧边界（sections）")
    check(fnd.disposition_id.startswith("fnd_") and not rmd.disposition_id.startswith("fnd_"),
          "两者的 disposition_id 命名域不重叠（fnd_ 前缀只属 FND）")
    check(len({rmd.disposition_id, fnd.disposition_id}) == 2,
          "RMD / FND 的身份不碰撞")

    # -- wire 字段不相交，任一方向读回都拒 --
    r_doc, f_doc = rmd.to_dict(), fnd.to_dict()
    check(not (set(_WRITER_AXIS_FIELDS) & set(r_doc)),
          "研究侧 wire 没有 Writer 侧轴（WMPD 不得被 RMD 冒充）")
    check(not (set(_RESEARCH_ONLY_AXES) & set(f_doc)),
          "Writer 侧 wire 没有研究侧准入/保留/来源校验轴（RMD 不得被 FND 冒充）")
    expect_raises("FND reader 读 RMD 载荷",
                  lambda: NS.FactNarrativeDisposition.from_dict(r_doc),
                  "未登记字段", NS.NarrativeSchemaError)
    expect_raises("RMD reader 读 FND 载荷",
                  lambda: TS.ResearchMaterialDisposition.from_dict(f_doc),
                  "未知字段")

    # -- 容器/来源身份口径不同：不能拿一个的 identity 当另一个的 --
    # successor 之后两者**共用** source/provenance/content 三个词的词汇，所以不再靠字段名
    # 区分，而是靠「是不是同一个东西」：RMD 的容器身份是**存储字段**（材料容器），
    # FND 的容器身份是**派生属性**（权威容器，等于 `authority_container_id`）。
    check("container_identity" in TS.ResearchMaterialDisposition.__dataclass_fields__
          and "container_identity" not in NS.FactNarrativeDisposition.__dataclass_fields__,
          "容器身份在 RMD 是存储字段、在 FND 是派生属性（两种容器不是同一个东西）")
    check(fnd.container_identity == fnd.authority_container_id
          and fnd.container_identity != rmd.container_identity,
          "两者的容器身份取值不同域（权威容器 ≠ 材料容器）")
    check(not ({"container_identity", "authority_specific_fact_id", "disposition_key"}
               & set(f_doc)),
          "FND 的三个集合键访问器是**派生**的，不落进 wire（避免第二份真值）")
    check(hasattr(fnd, "authority_kind") and hasattr(fnd, "authority_container_id")
          and not hasattr(rmd, "authority_kind"),
          "权威容器身份只属 FND（四类 authority tagged union）")
    check(hasattr(fnd, "accepted_binding_ids") and hasattr(fnd, "section_claim_ids")
          and not ({"accepted_binding_ids", "section_claim_ids"} & set(r_doc)),
          "正文引用集合只属 FND：RMD 没有 Claim/binding 引用轴")
    check(not ({"unresolved_id", "required", "authority_kind", "authority_container_id"}
               & set(r_doc)),
          "RMD 不带 FND 的缺口绑定/authority 字段（去向不是材料处置）")
    # 两套身份都有一个 `disposition_id` 与一个 `reason_code`，所以字段名本身不足以区分；
    # 区分它们的证据是 id 命名域不碰撞（上面）与字段缺失面（上面），不是字段名清单。

    # -- 持久化 home 不同（P17 v2 family；两个 family 的基数键互不相同）--
    check("current_section_wmpd_v2" in ST.V2_SECTION_TABLES
          and "current_section_fnd_v2" in ST.V2_SECTION_TABLES
          and not any("material_disposition" in t or "_rmd" in t for t in ST.V2_SECTION_TABLES),
          "WMPD / FND 各有独立 v2 family；RMD 没有 Section Store 表（研究侧不落 Section 边界）")
    wmpd_ddl = "\n".join(ST._migration_5_statements())
    check("UNIQUE (draft_id, member_ref)" in wmpd_ddl,
          "WMPD 的基数键 = 每个 manifest 成员恰一条")
    check("UNIQUE (draft_id, authority_kind, container_identity, authority_specific_fact_id)"
          in wmpd_ddl,
          "FND 的基数键 = authority_kind + 容器 + 权威专属 fact 恰一条")
    check("member_ref" not in "\n".join(
        s for s in ST._migration_5_statements() if "current_section_fnd_v2" in s),
        "FND family 不使用 WMPD 的 member_ref 键（两套基数语义不互相兼容）")


# ---------------------------------------------------------------------------
# 3. #28：依赖版本键 15 → 18
# ---------------------------------------------------------------------------

def _check_dependency_keys() -> None:
    keys = TS.DEPENDENCY_VERSION_KEYS
    check(len(keys) == 18, f"DEPENDENCY_VERSION_KEYS 精确 18 键（实际 {len(keys)}）")
    check(len(set(keys)) == 18, "18 键无重复")
    new_keys = {"material_disposition": TS.MATERIAL_DISPOSITION_VERSION,
                "fact_qualification": TS.FACT_QUALIFICATION_VERSION,
                "external_fact": TS.EXTERNAL_FACT_VERSION}
    check(set(new_keys) <= set(keys),
          "M930-3 三个新轴（material_disposition / fact_qualification / external_fact）已登记")

    dv = TS.build_current_dependency_versions(contract_version="c-1",
                                              source_policy_version="sp-1")
    check(set(dv) == set(keys), "唯一 factory 返回精确 18 键")
    check(dv["material_disposition"] == new_keys["material_disposition"]
          and dv["fact_qualification"] == new_keys["fact_qualification"]
          and dv["external_fact"] == new_keys["external_fact"],
          "三个新轴的唯一值源就是三个具名常量")
    check(TS.validate_dependency_versions(dv) == dict(sorted(dv.items())),
          "validate 只做排序规范形（不新增/删除/替换键）")

    for key, value in new_keys.items():
        expect_raises(f"缺键 {key}",
                      lambda k=key: TS.validate_dependency_versions(
                          {a: b for a, b in dv.items() if a != k}),
                      "不得自动补全")
    expect_raises("多余键",
                  lambda: TS.validate_dependency_versions({**dv, "m930_extra": "x"}),
                  "未知键")
    expect_raises("空 dict",
                  lambda: TS.validate_dependency_versions({}),
                  "不得为空")
    expect_raises("键值为空串",
                  lambda: TS.validate_dependency_versions({**dv, "external_fact": ""}),
                  "必须为非空字符串")

    check(len(TS.LEGACY_V5_DEPENDENCY_VERSION_KEYS) == 15
          and set(TS.LEGACY_V5_DEPENDENCY_VERSION_KEYS) < set(keys),
          "v5 legacy 键集 15 键、是 current 的真子集（只供只读回看，不得升级）")
    check(len(TS.LEGACY_V4_DEPENDENCY_VERSION_KEYS) == 6
          and set(TS.LEGACY_V4_DEPENDENCY_VERSION_KEYS) < set(TS.LEGACY_V5_DEPENDENCY_VERSION_KEYS),
          "v4 legacy 键集 6 键、是 v5 的真子集")
    check(not (set(TS.LEGACY_V5_DEPENDENCY_VERSION_KEYS) & set(new_keys)),
          "legacy 键集不含任何 M930-3 新轴")


# ---------------------------------------------------------------------------
# 4. #37：successor 基数由唯一四门入口逐条重算
# ---------------------------------------------------------------------------

def _check_successor_cardinality() -> None:
    m1, m2 = _material("m1"), _material("m2")

    # -- 门 1：每个 material 恰一条 RMD --
    TS.verify_material_disposition_exact_set((m1,), (_rmd(m1),))
    check(True, "门 1：1 material + 1 RMD 通过")
    expect_raises("material 无 RMD",
                  lambda: TS.verify_material_disposition_exact_set((m1,), ()),
                  "必须恰有一条 ResearchMaterialDisposition")
    expect_raises("material 两条 RMD",
                  lambda: TS.verify_material_disposition_exact_set(
                      (m1,), (_rmd(m1), _rmd(m1, admission_state="rejected",
                                             retention_state="dropped",
                                             source_validation="rejected",
                                             reason_code="authority_rejected"))),
                  "必须恰有一条 ResearchMaterialDisposition")
    expect_raises("RMD 引用不存在的 material",
                  lambda: TS.verify_material_disposition_exact_set((m1,), (_rmd(m2),)),
                  "引用了不存在的 material")
    expect_raises("重复 material_id",
                  lambda: TS.verify_material_disposition_exact_set((m1, m1), (_rmd(m1),)),
                  "material_id")

    # -- 唯一四门入口：三类新族各自缺失都必须被拦 --
    cand, dec = _ext_chain()
    good = _PackView(materials=(m1,), material_dispositions=(_rmd(m1),),
                     fact_candidates=(cand,), fact_qualification_decisions=(dec,),
                     external_facts=())
    expect_raises("四门入口：eligible external 决定缺 qualified result",
                  lambda: TS.verify_pack_successor_sets(good),
                  "必须恰有一条 ExternalFact")
    TS.verify_pack_successor_sets(_PackView(
        materials=(m1,), material_dispositions=(_rmd(m1),), fact_candidates=(),
        fact_qualification_decisions=(), external_facts=()))
    check(True, "四门入口：material / candidate / 决定 / qualified result 四族一致时通过")
    expect_raises("四门入口：缺 RMD 族",
                  lambda: TS.verify_pack_successor_sets(_PackView(materials=(m1,))),
                  "必须恰有一条 ResearchMaterialDisposition")
    expect_raises("四门入口：缺 candidate→决定 族",
                  lambda: TS.verify_pack_successor_sets(_PackView(
                      materials=(m1,), material_dispositions=(_rmd(m1),),
                      fact_candidates=(cand,))),
                  "必须恰有一条资格决定")

    # -- 六族 successor 字段必须显式传入（不得靠默认值绕门）--
    fields = TS.TopicResearchPack.__dataclass_fields__
    for name in ("material_dispositions", "fact_candidates", "fact_qualification_decisions",
                 "external_facts", "contract_gaps", "research_blocks"):
        f = fields[name]
        check(f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING,
              f"TopicResearchPack.{name} 无默认值（必须显式传入六族）")
    check(TS.TOPIC_PACK_SCHEMA_VERSION == "7",
          "current Pack wire 版本为 7")


def main() -> dict:
    _check_rmd_identity()
    _check_three_identities()
    _check_dependency_keys()
    _check_successor_cardinality()
    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)

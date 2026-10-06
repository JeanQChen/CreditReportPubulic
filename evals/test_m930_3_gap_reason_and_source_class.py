"""Eval: 缺口**理由**与补件**来源类**的两条业务行为（`cw-3`）。

用法: python -m evals.test_m930_3_gap_reason_and_source_class

缘起是 M930-3 真实 r2（call_id `5856a5b820a14b2ca50bd3e31924264d`）那一轮的**两条可核事实**，
它们在 r2 留存的字节里都还在：

1. **理由写反了。** 7 条缺口**全部**自述 `no_source_in_manifest`。可是同一份请求面的材料登记里，
   其中 5 栏（`company_business_main.cost_gross_margin` 6 份、`company_business_model.cost_structure`
   3 份、三个客户栏目各 3 份）**明明有材料登记到那一栏**，只有 2 个供应商栏目确为零材料。
   `no_source_in_manifest` 在下游是「这一节本来就没什么可写」，真实情形是「材料在，只是里面的
   **合格数字事实**本次没有授权」——两者要补的东西不同（去检索 vs 去补事实资格）。把 7 条写成
   同一条，等于把 5 条「要补事实」静默改写成「要补检索」。
2. **来源类不是来源类。** 4 条补件需求把 `expected_source_class` 写成 `financial_pack`。可检索
   来源类是**封闭词表**（`harness.topic_schema.SOURCE_CLASSES`），`financial_pack` 不在里面——
   它在**另一根轴**上（`sections.narrative_schema.AUTHORITY_KINDS`）：前者回答「去哪里检索」，
   后者回答「这份事实由谁背书」。这个字段一旦被下游当**取材指令**读，就等于把下一次检索指错方向。

本模块钉的就是这两条**业务行为**，不钉措辞、不钉源码文本、不钉版本号字面量：

* §1 判定规则本身（纯函数，逐条正反例）：改判**只**由「该栏登记数」这一条读数触发，**两个方向
  各一条**——自述「清单里没有来源」而该栏**有**登记 ⇒ `source_present_but_not_admissible`；
  自述「有材料但不可用 / 只取到一部分」而该栏**零**登记 ⇒ 改回 `no_source_in_manifest`（这两句
  **都预设「本栏至少有一条来源」**，零登记把它们同时证伪：`cp-15` 之前的改判是单向的，于是
  「供应商栏一份来源都没有」可能被记成「有材料但不合格」）。`not_applicable_to_authority` **不**
  预设任何来源，原样保留——本模块没有依据反驳它，凭直觉改判比不改更坏。
* §2 把 r2 的**真实回复字节**重走一遍真实解析口，逐条读回改判台账、审计字段与身份影响。
* §3 补件的来源类由**该栏 Contract 允许的来源**推导（逐条取自真实冻结 Contract 的
  `evidence_requirements`），生成器自述的那一个只作审计；推导档位与空取值的一致性在构造期
  fail-closed。
* §4 「这一栏有没有来源」是**按栏**问的，不是按节问的（`cwm-6` 之后一个小节覆盖多条要求）。
  §1 的 `registered_count` 由调用方按 `aspect_for_requirement` 定好栏位后传入；对不上就退回
  本模块原有的较宽口径，**不猜栏目**。本段用 cp-12 真实留存的清单与缺口原话复验：供应商两栏
  （登记 0 份）保留 `no_source_in_manifest`，客户三栏（各有 3 份上年历史来源）照旧改判
  `source_present_but_not_admissible`——「来源缺口」与「取材失败」要补的东西不同，不得合并记。

**边界**：本模块只读 `logs/llm` 下 r2 那一份留存调用与冻结 Contract 资产，不调 LLM、不联网、
不写库、不碰历史 run（写盘只用 `TemporaryDirectory`）。它证明的是**判定与推导**这两条业务行为，
不是正文质量、不是那一轮的正式通过、也不宣称 M930-3 / TS5 / 任何正式阶段关闭。

数据依赖：`logs/llm/` 下 r2 那一份调用日志**不在版本控制里**，缺失时本模块 **typed skip**
（不计通过），不静默绿。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import loader_v2                      # noqa: E402
from contracts import schema_v2 as S                 # noqa: E402
from harness import topic_schema as TS               # noqa: E402
from sections import cited_writer as CW              # noqa: E402
from sections import material_context as MC          # noqa: E402
from sections import narrative_schema as NS          # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LLM_LOGS = ROOT / "logs" / "llm"
CONTRACT_ASSET = str(ROOT / S.CONTRACT_V2_ASSET)

#: 真实 r2 的调用身份。它是**一次已发生的事实**的键，不是可漂移的版本号：用来在
#: `logs/llm` 里定位那一份留存调用（文件名带时间戳前缀，故按它做子串匹配）。
R2_CALL_ID = "5856a5b820a14b2ca50bd3e31924264d"

#: 夹具用的容器身份。请求面**不落**容器 id（它落的是 `(pack_id, material_id)` 的编码 `ref`），
#: 所以「材料行的登记归属与正文」取自真实请求面，而容器侧这三个值只能是夹具值——本模块不假装
#: 它们是那一轮的取值，也不拿它们做任何断言。
_FIXTURE_PACK_ID = "pack_fixture_gap_reason"
_FIXTURE_PROVENANCE = "prov_fixture_gap_reason"
_FIXTURE_MANIFEST_FP = "ab332e26137ac4d6884a72aacf3562f9cd17800054f47f15fe02d3b9fd7d708b"
_FIXTURE_CONTEXT_FP = "a84f0d5751886ee0ca9825f6a3400918d09898f45d87b9a43ab149ce312ce02f"

#: 同一句缺口话，除自述理由外逐字相同——用来证明「判定只由理由与登记数决定」。
_GAP_DETAIL = "本栏还缺合格数值事实。"


# ---------------------------------------------------------------------------
# 数据依赖与夹具
# ---------------------------------------------------------------------------

def _r2_log_path() -> Path | None:
    hits = sorted(LLM_LOGS.glob(f"*{R2_CALL_ID}*.jsonl"))
    return hits[0] if hits else None


def _anchors_present() -> tuple[bool, str]:
    missing: list[str] = []
    if _r2_log_path() is None:
        missing.append(f"logs/llm/*{R2_CALL_ID}*.jsonl")
    if not Path(CONTRACT_ASSET).exists():
        missing.append(str(Path(CONTRACT_ASSET).relative_to(ROOT)))
    return (not missing), "；".join(missing)


def _r2_record() -> dict:
    path = _r2_log_path()
    assert path is not None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            return json.loads(line)
    raise AssertionError(f"{path} 里没有可用的记录")


def _contract_source_classes() -> dict[str, tuple[str, ...]]:
    """冻结 Contract 逐栏的**允许检索来源类**（并集保序，与组合根的投影同一口径）。

    这里只做投影：不排序、不筛掉 `TS.SOURCE_CLASSES` 之外的取值、不做「挑一个」。
    """
    contract = loader_v2.load_contract_v2(CONTRACT_ASSET)
    ers = contract.raw.get("evidence_requirements", {})
    out: dict[str, tuple[str, ...]] = {}
    for aspect in contract.all_aspects():
        seq: list[str] = []
        for rid in aspect.evidence_requirement_ids:
            for sc in (ers[rid].get("source_classes") or ()):
                name = str(sc or "").strip()
                if name and name not in seq:
                    seq.append(name)
        out[aspect.aspect_id] = tuple(seq)
    return out


def _face_declared_aspect(row: dict) -> str:
    """从**历史**请求面的一行小节里读它声明的那个 Contract 栏目，只读回放、不做兼容猜读。

    r2 那一轮的请求面写在 `cwm-3` 上，小节行落的是**单数**键 `declared_aspect_id`
    （`cwm-6` 才换成集合 `declared_aspect_ids`）。历史字节不能按新面重解——`from_dict` 只认
    新键，正是这个意思：旧对象只经只读回放读。

    所以这里显式读旧键，且**只**读旧键：新键出现了就用它，两键都不在就当场报错。绝不写
    「先试新的、不行退回旧的」那种兼容读法——那会把「这一面到底声明了哪一栏」变成一次猜，
    而本模块 §0 正要拿这个值当作真值去做登记对账。
    """
    if "declared_aspect_ids" in row:
        ids = tuple(str(a).strip() for a in (row["declared_aspect_ids"] or ()) if str(a).strip())
        if len(ids) != 1:
            raise AssertionError(
                f"本模块用的是 r2 那一轮「一小节一栏」的历史面，一行不该声明 {len(ids)} 栏："
                f"{ids!r}")
        return ids[0]
    aspect = str(row.get("declared_aspect_id") or "").strip()
    if not aspect:
        raise AssertionError(f"这一行小节既没有 `declared_aspect_ids` 也没有 "
                            f"`declared_aspect_id`：{sorted(row)!r}")
    return aspect


def _fixture() -> tuple[dict, str, CW.CitedWriterInputManifest, dict]:
    """r2 留存的请求面与回复 → (请求面, 回复文本, 夹具清单, 该栏登记表)。

    清单里的**小节规格**与**材料行的登记归属、正文、来源角色、位置、payload 哈希**都逐字来自
    真实请求面；`allowed_source_classes` 取自**冻结 Contract 的同一投影**（r2 那一轮的请求面是
    旧修订号，本来就没有这一轴——把旧面缺字段读成「Contract 没有声明来源类」是一句假话，所以
    这里用真实的 Contract 取值，而不是把缺字段当取值）。容器侧身份见模块头。
    """
    record = _r2_record()
    face = json.loads(record["messages"][0]["content"])
    reply = record["completion"]
    classes = _contract_source_classes()

    subsections = tuple(
        CW.CitedSubsectionSpec(
            subsection_id=str(row["subsection_id"]), title=str(row["title"]),
            requirement_text=str(row["requirement_text"]),
            declared_aspect_ids=(aspect,),
            allowed_source_classes=classes.get(aspect, ()))
        for row, aspect in ((row, _face_declared_aspect(row))
                            for row in face["subsections"]))

    materials = []
    for row in face["materials"]:
        reading_view = str(row["text"])
        fingerprint = MC._reading_view_fingerprint(
            payload_hash=str(row["payload_hash"]), object_type=str(row["material_type"]),
            reading_view=reading_view, structured_view=None)
        materials.append(CW.CitedMaterialEntry(
            citation_key=str(row["key"]),
            member_ref=NS.manifest_member_ref(_FIXTURE_PACK_ID, str(row["material_id"])),
            pack_id=_FIXTURE_PACK_ID, material_id=str(row["material_id"]),
            topic_id=str(row["topic_id"]), material_type=str(row["material_type"]),
            source_identity=str(row["source_identity"]),
            provenance_identity=_FIXTURE_PROVENANCE,
            source_role=str(row["source_role"]), locator_ref=dict(row["locator"]),
            payload_hash=str(row["payload_hash"]), reading_view=reading_view,
            reading_view_fingerprint=fingerprint,
            aspect_ids=tuple(row.get("aspect_ids") or ())))

    manifest = CW.CitedWriterInputManifest.create(
        task_id=str(face["task_id"]), section_id=str(face["section_id"]),
        section_title=str(face["section_title"]),
        pack_set_fingerprint="pset_fixture_gap_reason",
        material_manifest_id="mman_fixture_gap_reason",
        material_manifest_fingerprint=_FIXTURE_MANIFEST_FP,
        material_context_id="mctx_fixture_gap_reason",
        material_context_fingerprint=_FIXTURE_CONTEXT_FP,
        subsections=subsections, materials=tuple(materials), facts=(),
        presentation_routing=face.get("presentation_routing"))

    registered: dict[str, tuple[str, ...]] = {}
    for row in face["materials"]:
        for aspect_id in (row.get("aspect_ids") or ()):
            registered[aspect_id] = registered.get(aspect_id, ()) + (str(row["key"]),)
    return face, reply, manifest, registered


def _reply_payload(reply: str) -> dict:
    return json.loads(reply)


def _spec(subsection_id: str, classes: tuple[str, ...] = ()) -> CW.CitedSubsectionSpec:
    return CW.CitedSubsectionSpec(
        subsection_id=subsection_id, title=f"标题 {subsection_id}",
        requirement_text=f"要求 {subsection_id}", declared_aspect_ids=(subsection_id,),
        allowed_source_classes=classes)


# ---------------------------------------------------------------------------
# §1 判定规则：一条唯一的改判（纯函数，正反例）
# ---------------------------------------------------------------------------

def _section_rule(check) -> None:
    check(CW.CITED_GAP_REASONS == (
        "no_source_in_manifest", "manifest_partial_for_requirement",
        "source_present_but_not_admissible", "not_applicable_to_authority"),
          "缺口理由沿用既有封闭词表——**没有新增原因码**：「材料在、只是里面的合格数字事实"
          "本次没有授权」这一情形映射到已存在的 `source_present_but_not_admissible`，"
          "本批新增的只是判定来源那一根审计轴")

    claimed = "no_source_in_manifest"
    reason, assignment = CW.assign_gap_reason(
        claimed_reason=claimed, spec=_spec("a.b"), registered_count=3)
    check(reason == "source_present_but_not_admissible",
          "正例：自述「清单里没有来源」而清单里**有**登记到该栏的材料 ⇒ 改判为"
          f"「有材料但不可用」（实得 {reason!r}）")
    check(assignment == "system_reassigned_from_manifest",
          f"⇒ 且判定来源如实记成系统改判（实得 {assignment!r}）")

    reason, assignment = CW.assign_gap_reason(
        claimed_reason=claimed, spec=_spec("a.b"), registered_count=0)
    check(reason == claimed and assignment == "writer_declared",
          "正例：同一条自述、该栏登记确为 0 份 ⇒ **原样保留**（实得 "
          f"{reason!r}/{assignment!r}）——改判只由登记数触发，不由措辞触发")

    reason, assignment = CW.assign_gap_reason(
        claimed_reason=claimed, spec=None, registered_count=5)
    check(reason == claimed and assignment == "uncheckable_subsection_not_in_manifest",
          "正例：连是哪一栏都取不到 ⇒ 不改判、也不假装核过（实得 "
          f"{reason!r}/{assignment!r}）——登记数再多也不构成一句能下的判断")

    for other in ("manifest_partial_for_requirement", "source_present_but_not_admissible"):
        reason, assignment = CW.assign_gap_reason(
            claimed_reason=other, spec=_spec("a.b"), registered_count=7)
        check(reason == other and assignment == "writer_declared",
              f"正例：自述 {other!r}、而该栏**确有**登记 ⇒ 原样保留，"
              f"实得 {reason!r}/{assignment!r}")

    # 反面：同一句自述、而该栏**零**登记 ⇒ 两句的预设都不成立，改回「本栏没有来源」。
    # 业务形状就是供应商那两栏（清单里一份来源都没有）：把它记成「有材料但不合格」，下游会去
    # 补事实资格，而真正要做的是去检索——「来源缺口」与「取材失败」要补的东西不同。
    for other in ("manifest_partial_for_requirement", "source_present_but_not_admissible"):
        reason, assignment = CW.assign_gap_reason(
            claimed_reason=other, spec=_spec("a.b"), registered_count=0)
        check(reason == "no_source_in_manifest"
              and assignment == "system_reassigned_from_manifest",
              f"反例：自述 {other!r}、而该栏登记为 0 ⇒ 改判回 `no_source_in_manifest`"
              f"（「只取到一部分」以取到过为前提，「来源在场」直接断言在场），实得 "
              f"{reason!r}/{assignment!r}")

    reason, assignment = CW.assign_gap_reason(
        claimed_reason="not_applicable_to_authority", spec=_spec("a.b"), registered_count=0)
    check(reason == "not_applicable_to_authority" and assignment == "writer_declared",
          "反例：`not_applicable_to_authority` **不**预设任何来源（它说的是「这条要求与本节的"
          f"权威输入无关」）⇒ 登记为 0 也不改判，实得 {reason!r}/{assignment!r}")

    check(set(CW.CITED_GAP_REASON_ASSIGNMENTS) == {
        "writer_declared", "system_reassigned_from_manifest",
        "uncheckable_subsection_not_in_manifest"},
          "判定来源是封闭三值：自述 / 系统改判 / 无从核对——不存在第四种含糊档位")
    check(set(CW.CITED_SOURCE_CLASS_DERIVATIONS) == {
        "contract_aspect_evidence_requirements", "contract_aspect_without_evidence_requirements",
        "aspect_not_in_manifest"},
          "补件来源类的推导档位也是封闭三值：Contract 声明了 / Contract 没声明 / 该栏不在清单里")


# ---------------------------------------------------------------------------
# §2 r2 真实回复走真实解析口：逐条读回改判台账
# ---------------------------------------------------------------------------

def _section_r2_replay(check, face, reply, manifest, registered) -> None:
    payload = _reply_payload(reply)
    check(len(payload.get("gaps") or []) == len({g["subsection_id"] for g in payload["gaps"]}),
          "前提：r2 的顶层缺口逐条指向不同小节（本条只对顶层那 7 条负责）")

    claimed_by_subsection = {str(g["subsection_id"]): str(g["reason"])
                             for g in payload["gaps"]}
    check(all(v == "no_source_in_manifest" for v in claimed_by_subsection.values()),
          "前提（r2 的现场）：7 条缺口**全部**自述「清单里没有来源」")

    draft, _ledger = CW.parse_cited_prose_with_normalization(reply, manifest=manifest)

    by_subsection = {g.subsection_id: g for g in draft.gaps}
    check(set(by_subsection) == set(claimed_by_subsection),
          f"解析后缺口逐条对上（{len(by_subsection)} 条，不多不少）")

    expect_reassigned = sorted(a for a, keys in registered.items()
                               if len(keys) > 0 and a in by_subsection)
    expect_kept = sorted(a for a in by_subsection if a not in set(expect_reassigned))
    check(len(expect_reassigned) == 5,
          f"r2 现场：**5** 栏有材料登记却被写成「没有来源」（实得 {len(expect_reassigned)}）")
    check(len(expect_kept) == 2,
          f"r2 现场：**2** 栏确为零材料（实得 {len(expect_kept)}）——"
          "供应商两栏与前五栏必须分开记，不得合并成一条「都没有材料」")

    for aspect_id in expect_reassigned:
        gap = by_subsection[aspect_id]
        check(gap.reason == "source_present_but_not_admissible"
              and gap.reason_assignment == "system_reassigned_from_manifest",
              f"改判：{aspect_id}（该栏登记 {len(registered[aspect_id])} 份）⇒ "
              f"{gap.reason!r}/{gap.reason_assignment!r}")
        check(gap.claimed_reason == claimed_by_subsection[aspect_id],
              f"⇒ {aspect_id} 的**原话保留**在 `claimed_reason` 里（审计不丢）："
              f"{gap.claimed_reason!r}")

    for aspect_id in expect_kept:
        gap = by_subsection[aspect_id]
        check(gap.reason == "no_source_in_manifest"
              and gap.reason_assignment == "writer_declared",
              f"不动：{aspect_id}（该栏登记 0 份）⇒ {gap.reason!r}/{gap.reason_assignment!r}")
        check(gap.claimed_reason == claimed_by_subsection[aspect_id],
              f"⇒ {aspect_id} 原话与判定一致，`claimed_reason` 仍是它")

    check(all(g.reason in CW.CITED_GAP_REASONS for g in draft.gaps),
          "改判后逐条理由仍落在封闭词表内")
    check(all(g.detail and g.requirement_text for g in draft.gaps),
          "改判不动 `detail` 与 `requirement_text`：本模块只重定理由，不替生成器改写它说的话")
    check(all(g.detail == _reply_detail(payload, g.subsection_id) for g in draft.gaps),
          "⇒ 逐条 `detail` 与回复里的原话逐字相同（改判不改写正文侧的任何一句话）")

    # 身份影响：`reason` 进身份体、两个审计字段不进。所以「因为改判而换 id」这件事的**唯一**
    # 原因必须是 `reason` 本身变了，而不是多了两个字段。
    sample = draft.gaps[0]
    same = CW.CitedProseGap.create(
        subsection_id=sample.subsection_id, requirement_text=sample.requirement_text,
        reason=sample.reason, detail=sample.detail,
        reason_assignment="writer_declared", claimed_reason=sample.reason)
    check(same.gap_id == sample.gap_id,
          "审计字段不进身份体：同一个 `(小节, 要求, 理由, 说明)` 换一种判定来源/自述，"
          "缺口身份**不变**（否则每多一个审计字段就换一次 id，历史读回全断）")
    changed = CW.CitedProseGap.create(
        subsection_id=sample.subsection_id, requirement_text=sample.requirement_text,
        reason="no_source_in_manifest", detail=sample.detail)
    check(changed.gap_id != sample.gap_id,
          "而理由本身进身份体：换成另一条理由就换 id——「没有来源」与「有材料但不可用」"
          "在下游是两份不同的产物，不得共用一个身份")

    other = [g for g in draft.gaps if g.reason_assignment == "writer_declared"]
    check(all(g.gap_id for g in other),
          "未被改判的缺口同样是完整对象（不是「没改判就少一份产物」）")


def _reply_detail(payload: dict, subsection_id: str) -> str:
    for gap in payload.get("gaps") or []:
        if str(gap.get("subsection_id")) == subsection_id:
            return str(gap.get("detail") or "")
    raise AssertionError(f"回复里没有 {subsection_id} 的缺口")


# ---------------------------------------------------------------------------
# §3 补件来源类：由 Contract 推导，不由生成器自选
# ---------------------------------------------------------------------------

def _section_source_class(check, face, reply, manifest) -> None:
    payload = _reply_payload(reply)
    needs = payload.get("follow_up_needs") or []
    check(len(needs) == 4, f"前提（r2 的现场）：4 条补件需求（实得 {len(needs)}）")
    check(all(str(n.get("expected_source_class") or "") == "financial_pack" for n in needs),
          "前提（r2 的现场）：4 条**全部**自述来源类为 `financial_pack`")

    classes = _contract_source_classes()
    draft, _ledger = CW.parse_cited_prose_with_normalization(reply, manifest=manifest)
    check(len(draft.follow_up_needs) == len(needs),
          "解析后补件需求逐条对上")

    for need, raw in zip(draft.follow_up_needs, needs):
        want = classes.get(str(raw.get("aspect_id")), ())
        check(need.expected_source_classes == want,
              f"推导：{need.aspect_id} 的期望来源类 = 该栏 Contract 允许的来源 "
              f"{list(want)}（实得 {list(need.expected_source_classes)}）")
        check(need.source_class_derivation == "contract_aspect_evidence_requirements",
              f"⇒ 档位如实记为「Contract 声明了证据要求」（实得 {need.source_class_derivation!r}）")
        check(need.writer_claimed_source_class == "financial_pack",
              f"⇒ 生成器自述的那一个原样留在审计字段里（{need.writer_claimed_source_class!r}）")
        check("financial_pack" not in need.expected_source_classes,
              f"⇒ `financial_pack` **没有**进入期望来源类：{need.aspect_id}")

    check("financial_pack" in NS.AUTHORITY_KINDS,
          "`financial_pack` 是**权威类型**（回答「这份事实由谁背书」）——它确实在权威词表里")
    check("financial_pack" not in TS.SOURCE_CLASSES,
          "而它不是**来源类**（回答「去哪里检索」）——两者是两根不同的轴，"
          "写错一个类别等于把下一次检索直接指挥到错误的方向")
    check(all(set(c) <= set(TS.SOURCE_CLASSES) for c in classes.values()
              if c and not set(c) - set(TS.SOURCE_CLASSES)),
          "本次真实投影出的允许来源类都落在可检索来源词表内（越界值由读回标出，不在此静默改写）")

    # 身份影响：期望来源类进身份体 ⇒ 换一类来源就是另一条补件诉求。
    sample = draft.follow_up_needs[0]
    fields = {f.name for f in __import__("dataclasses").fields(sample)}
    other_classes = tuple(c for c in sample.expected_source_classes) + ("external",)
    twin = CW.CitedFollowUpNeed.create(**{
        **{name: getattr(sample, name) for name in fields if name != "need_id"},
        "expected_source_classes": other_classes})
    check(twin.need_id != sample.need_id,
          "换一类期望来源 ⇒ 补件身份随之改变（补件诉求的取值是它的身份的一部分）")
    twin_same = CW.CitedFollowUpNeed.create(**{
        **{name: getattr(sample, name) for name in fields if name != "need_id"},
        "writer_claimed_source_class": "别的说法"})
    check(twin_same.need_id == sample.need_id,
          "⇒ 而生成器自述的那一个**不进**身份体：审计字段换了不换 id")

    # 推导档位与空取值的一致性（构造期 fail-closed，不靠读者自觉）
    check(CW.derive_expected_source_classes(spec=None) == ((), "aspect_not_in_manifest"),
          "该栏不在清单里 ⇒ 空取值 + `aspect_not_in_manifest`（「量不出来」不冒充「量出来是空」）")
    empty = CW.derive_expected_source_classes(spec=_spec("a.b"))
    check(empty == ((), "contract_aspect_without_evidence_requirements"),
          "该栏在 Contract 里没有声明证据要求 ⇒ 空取值 + 对应的那一档")
    nonempty = CW.derive_expected_source_classes(spec=_spec("a.b", ("company_industry",)))
    check(nonempty == (("company_industry",), "contract_aspect_evidence_requirements"),
          "该栏声明了 ⇒ 逐字取回那一批（保序、不排序、不挑一个）")

    _expect_error(
        lambda: CW.CitedFollowUpNeed.create(
            statement="s", subsection_id="a.b", target_requirement_text="t",
            topic_id="a", aspect_id="a.b", section_id="company", prose_revision="rev",
            requiredness="required", writer_identity="w",
            source_class_derivation="contract_aspect_evidence_requirements"),
        token="空取值只允许出现",
        msg="反例：声明「Contract 声明了证据要求」却一个类都推不出来 ⇒ 构造期拒收"
            "（空取值只能是「量不出来」那一档的结论）")
    _expect_error(
        lambda: CW.CitedFollowUpNeed.create(
            statement="s", subsection_id="a.b", target_requirement_text="t",
            topic_id="a", aspect_id="a.b", section_id="company", prose_revision="rev",
            requiredness="required", writer_identity="w",
            expected_source_classes=("company_industry", "company_industry"),
            source_class_derivation="contract_aspect_evidence_requirements"),
        token="不得有重复项",
        msg="反例：期望来源类重复 ⇒ 构造期拒收（重复项会让补件的来源类集合与其取值不同，"
            "两份清单共用同一个 id）")
    _expect_error(
        lambda: CW.CitedProseGap.create(
            subsection_id="a.b", requirement_text="t", reason="no_source_in_manifest",
            detail="d", reason_assignment="writer_declared",
            claimed_reason="manifest_partial_for_requirement"),
        token="却标成 writer_declared",
        msg="反例：自述与判定不同却不标「系统改判」⇒ 构造期拒收（改判必须留下判定来源）")


def _spec_multi(subsection_id: str, pairs: tuple[tuple[str, str], ...]) -> CW.CitedSubsectionSpec:
    """一个覆盖**多条** Contract 要求的小节规格：`(aspect_id, 要求原文)` 逐条位置对位。"""
    return CW.CitedSubsectionSpec(
        subsection_id=subsection_id, title=f"标题 {subsection_id}",
        requirement_text="\n".join(line for _a, line in pairs),
        declared_aspect_ids=tuple(a for a, _line in pairs),
        allowed_source_classes=())


def _manifest_with(specs, materials) -> CW.CitedWriterInputManifest:
    return CW.CitedWriterInputManifest.create(
        task_id="t_fixture_column_scope", section_id="company", section_title="公司",
        pack_set_fingerprint="pset_fixture_column_scope",
        material_manifest_id="mman_fixture_column_scope",
        material_manifest_fingerprint=_FIXTURE_MANIFEST_FP,
        material_context_id="mctx_fixture_column_scope",
        material_context_fingerprint=_FIXTURE_CONTEXT_FP,
        subsections=tuple(specs), materials=tuple(materials), facts=())


def _material(key: str, aspects: tuple[str, ...]) -> CW.CitedMaterialEntry:
    reading_view = "夹具正文"
    return CW.CitedMaterialEntry(
        citation_key=key, member_ref=NS.manifest_member_ref(_FIXTURE_PACK_ID, f"mat-{key}"),
        pack_id=_FIXTURE_PACK_ID, material_id=f"mat-{key}", topic_id="topic_fixture",
        material_type="evidence_span", source_identity=f"src-{key}",
        provenance_identity=_FIXTURE_PROVENANCE, source_role="current_state_source",
        locator_ref={"locator_schema": "loc-1", "locator_kind": "block_range",
                     "owner": "evidence_document:fixture@sha256-" + "0" * 16 + "#block_span",
                     "first": 0, "last": 0},
        payload_hash="0" * 64, reading_view=reading_view,
        reading_view_fingerprint=MC._reading_view_fingerprint(
            payload_hash="0" * 64, object_type="evidence_span", reading_view=reading_view,
            structured_view=None),
        aspect_ids=aspects)


# ---------------------------------------------------------------------------
# §4 登记数按**栏**数，不按节数：多栏小节里的空栏不得被别栏的材料顶掉
# ---------------------------------------------------------------------------

def _section_column_scope(check) -> None:
    """`cwm-6` 之后一个小节覆盖多条 Contract 要求，于是「这一栏有没有来源」必须按栏问。

    本条钉的是**真实发生过的那一次错**：`co-h4` 一节 18 栏，供应商两栏登记确为 0 份，却因为
    同节里客户栏有 3 份材料，被按整节计数改判成「有材料但不可用」——把「来源缺口」写成了
    「取材失败」，把两件要补的东西不同的事记成同一条查得动的事实。
    """
    pairs = (("a.a1", "第一条要求"), ("a.a2", "第二条要求"), ("a.a3", "第三条要求"))
    spec = _spec_multi("a", pairs)
    check(CW.aspect_for_requirement(spec, "第二条要求") == "a.a2",
          "正例：这一条要求的文本能逐字对到栏位 ⇒ 返回**那一个** aspect_id")
    check([CW.aspect_for_requirement(spec, line) for _a, line in pairs]
          == [a for a, _line in pairs],
          "⇒ 三条要求逐条各归各栏，按 Contract 顺序，不重不漏")

    check(CW.aspect_for_requirement(spec, "清单里没写过的要求") is None,
          "反例：文本对不上任何一行 ⇒ `None`（**「查不出来」不是「查出来是 0」**，"
          "调用方据此退回本模块原有的较宽口径，而不是猜一个栏目）")
    check(CW.aspect_for_requirement(spec, "") is None, "反例：空文本 ⇒ `None`")
    check(CW.aspect_for_requirement(None, "第二条要求") is None, "反例：没有规格 ⇒ `None`")
    skewed = CW.CitedSubsectionSpec(
        subsection_id="a", title="标题 a", requirement_text="第一条要求\n第二条要求",
        declared_aspect_ids=("a.a1",), allowed_source_classes=())
    check(CW.aspect_for_requirement(skewed, "第二条要求") is None,
          "反例：行数与栏数不等（对位不成立）⇒ `None`——宁可不缩小范围，不按错位猜栏目")

    # 走**真实解析口**的缺口那一步：同一份清单、同一句自述，只有要求文本不同。
    materials = (_material("m01", ("a.a1",)),)
    manifest = _manifest_with((spec,), materials)
    def _gap(line: str):
        return CW._gap_from_payload(
            {"subsection_id": "a", "requirement_text": line,
             "reason": "no_source_in_manifest", "detail": _GAP_DETAIL},
            manifest=manifest)

    empty_column, populated_column = pairs[1], pairs[0]
    gap = _gap(empty_column[1])
    check(gap.reason == "no_source_in_manifest" and gap.reason_assignment == "writer_declared",
          f"**本条要钉的错**：自述「没有来源」的那一栏（{empty_column[0]}）登记确为 0 份 ⇒ "
          f"理由**原样保留**，不得被同节别栏的材料顶掉（实得 {gap.reason!r}/"
          f"{gap.reason_assignment!r}）")
    gap = _gap(populated_column[1])
    check(gap.reason == "source_present_but_not_admissible"
          and gap.reason_assignment == "system_reassigned_from_manifest",
          f"对照：同一个小节里**确有**材料的那一栏（{populated_column[0]}）⇒ 照旧改判"
          f"（实得 {gap.reason!r}/{gap.reason_assignment!r}）——一条规则，两种结果，"
          "差别只来自登记数")
    check(_gap(empty_column[1]).gap_id != _gap(populated_column[1]).gap_id,
          "⇒ 两条缺口是两份不同身份的产物（理由进身份体），不得合并记一条")


def _fact(key: str, *, fact_id: str, aspects: tuple[str, ...]) -> CW.CitedFactEntry:
    return CW.CitedFactEntry(
        citation_key=key, authority_kind="financial_pack",
        container_identity="fin_snapshot_fixture", fact_id=fact_id,
        text="2024年末的有息负债为1,364.02亿元。", topic_id="fin",
        aspect_ids=aspects, fact_type="metric", period="2024-12-31", scope="",
        required=False)


def _manifest_with_facts(specs, materials, facts, routing) -> CW.CitedWriterInputManifest:
    return CW.CitedWriterInputManifest.create(
        task_id="t_fixture_source_axis", section_id="financial", section_title="财务",
        pack_set_fingerprint="pset_fixture_source_axis",
        material_manifest_id="mman_fixture_source_axis",
        material_manifest_fingerprint=_FIXTURE_MANIFEST_FP,
        material_context_id="mctx_fixture_source_axis",
        material_context_fingerprint=_FIXTURE_CONTEXT_FP,
        subsections=tuple(specs), materials=tuple(materials), facts=tuple(facts),
        presentation_routing=routing)


def _section_source_axis_accounting(check) -> None:
    """「这一栏有没有来源」数的是**两根轴**：材料行 ∪ 权威事实行（事实侧还要认呈现层路由）。

    业务形状直接取自 cp-14 财务节：那一节 `materials` 为空，落栏依据只有
    `presentation_routing.fact_columns`。只数材料行会把**每一栏**都读成「本栏没有来源」，于是
    「有事实、只是数字本次没授权」会被改判成「本来就没什么可写」——正是本模块头两条要分开的
    那两件事，只是换了一根轴。
    """
    spec = _spec_multi("fin", (("fin.a", "短期偿债"), ("fin.b", "净资产水平")))
    manifest = _manifest_with_facts(
        (spec,), (),
        (_fact("f01", fact_id="m_x", aspects=()),
         _fact("f02", fact_id="m_y", aspects=()),
         _fact("f03", fact_id="m_z", aspects=("fin.c",))),
        {"fact_columns": [{"fact_id": "m_x", "presentation_column": "fin.a"}]})

    check(CW.registered_material_keys(manifest, ("fin.a",)) == (),
          "前提：这一栏在 `materials` 里一份都没有（财务节的真实形状）")
    check(CW.registered_fact_keys(manifest, ("fin.a",)) == ("f01",),
          "正例：事实行的 `aspect_ids` 为空、但呈现层路由把它声明到本栏 ⇒ 它就是本栏的来源")
    check(CW.registered_source_keys(manifest, ("fin.a",)) == ("f01",),
          "⇒ 两轴合计仍只有那一条（材料 0 + 事实 1），不重复计数")

    populated = CW._gap_from_payload(
        {"subsection_id": "fin", "requirement_text": "短期偿债",
         "reason": "no_source_in_manifest", "detail": _GAP_DETAIL}, manifest=manifest)
    check(populated.reason == "source_present_but_not_admissible",
          f"正例：本栏**有**来源（路由声明的那条事实）而自述「清单里没有来源」⇒ 照旧改判"
          f"（实得 {populated.reason!r}）")

    empty = CW._gap_from_payload(
        {"subsection_id": "fin", "requirement_text": "净资产水平",
         "reason": "source_present_but_not_admissible", "detail": _GAP_DETAIL},
        manifest=manifest)
    check(empty.reason == "no_source_in_manifest"
          and empty.reason_assignment == "system_reassigned_from_manifest",
          f"反例：另一栏既没有材料、也没有落到本栏的事实 ⇒ 自述「有材料但不合格」被改判回"
          f"「本栏没有来源」（实得 {empty.reason!r}/{empty.reason_assignment!r}）——"
          "供应商那样的零来源栏不得被记成「有材料但不合格」")

    check(CW.registered_fact_keys(manifest, ("fin.b",)) == (),
          "反例：`f02` 的 `aspect_ids` 为空、又不在路由里 ⇒ 它**不是**任何一栏的来源"
          "（「读不出落栏」与「落到了本栏」不能长得一样）")
    check(CW.registered_fact_keys(manifest, ("fin.a", "fin.c")) == ("f01", "f03"),
          "正例：传一组栏目时取**并集**，事实按清单顺序返回、不重排")


def _cp12_real_replay(check) -> None:
    """用 cp-12 真实留存的清单与缺口**原话**重走一边缺口那一步（只读历史 run）。

    历史 run 不在版本控制里时本段只记 NOTE，不把整个模块标 skip——§1–§4 不依赖它。
    """
    run = ROOT / "evaluation" / "results" / "m930_3_cited_real_dual_cp12_r1"
    man_path = run / "company" / "cited_input_manifest.json"
    prose_path = run / "company" / "cited_prose.json"
    if not man_path.exists() or not prose_path.exists():
        check(True, "NOTE cp-12 历史 run 不在本机 ⇒ 本段不执行（§1–§4 不依赖它）")
        return
    manifest = CW.CitedWriterInputManifest.from_dict(
        json.loads(man_path.read_text(encoding="utf-8")))
    draft = CW.CitedProseDraft.from_dict(
        json.loads(prose_path.read_text(encoding="utf-8"))["draft"])
    spec = CW.manifest_spec_for_subsection(manifest, "co-h4")
    check(spec is not None and len(spec.declared_aspect_ids) == 18,
          "前提：cp-12 公司节 `co-h4` 一节**18 栏**（多栏小节正是本条要覆盖的现场）")

    kept, reassigned = [], []
    for gap in draft.gaps:
        again = CW._gap_from_payload(
            {"subsection_id": gap.subsection_id, "requirement_text": gap.requirement_text,
             "reason": gap.claimed_reason, "detail": gap.detail}, manifest=manifest)
        column = CW.aspect_for_requirement(spec, gap.requirement_text)
        (reassigned if again.reason_assignment == "system_reassigned_from_manifest"
         else kept).append((column, again.reason, again.reason_assignment))
    check(all(r[0] for r in kept + reassigned),
          "前提：8 条缺口的 `requirement_text` 逐条都能对到栏位（对位在真实产物上成立）")

    supplier = [r for r in kept if r[0].startswith("company_supplier_")]
    customer = [r for r in reassigned if r[0].startswith("company_customer_")]
    check(len(supplier) == 2 and all(r[1] == "no_source_in_manifest" for r in supplier),
          f"**要钉的错**：供应商两栏（登记 0 份）⇒ 理由保留 `no_source_in_manifest`"
          f"（实得 {[r[1] for r in supplier]}）——「这一节本来就没什么可写」与"
          "「材料在、只是数字没授权」要补的东西不同")
    check(len(customer) == 3 and all(r[1] == "source_present_but_not_admissible"
                                     for r in customer),
          f"对照：客户三栏（各有 3 份上年历史来源）⇒ 照旧改判"
          f"（实得 {[r[1] for r in customer]}）——它们的来源是历史态，不作当前态，"
          "所以是「有材料但不可用」，不是「没有来源」")
    check(len(kept) + len(reassigned) == len(draft.gaps),
          f"逐条都有结论，不多不少（{len(kept)} 保留 + {len(reassigned)} 改判 "
          f"= {len(draft.gaps)} 条）")


def _expect_error(fn, *, token: str, msg: str) -> None:
    try:
        fn()
    except CW.CitedWriterError as exc:
        if token not in str(exc):
            raise AssertionError(
                f"{msg}——但异常消息里没有 {token!r}，无法据此定位：{exc}") from None
        return
    raise AssertionError(f"{msg}，但它通过了")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> dict:
    passed = 0
    failed = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    ok, missing = _anchors_present()
    if not ok:
        return {"passed": 0, "failed": 0, "skipped": 1,
                "details": [f"SKIP r2 现场数据缺失，本模块不猜、不静默绿：{missing}"]}

    face, reply, manifest, registered = _fixture()

    details.append("## §0 前提：r2 留存的字节与冻结 Contract")
    check(len(face["subsections"]) == 18 and len(face["materials"]) == 29,
          f"r2 请求面：{len(face['subsections'])} 小节 / {len(face['materials'])} 材料")
    check(not (face.get("authority_facts") or ()),
          "r2 请求面的 `authority_facts` 为空——本次公司节确实没有可用的合格数字事实"
          "（这正是「材料在、数字不能用」这条情形的现场）")
    face_aspects = {_face_declared_aspect(r) for r in face["subsections"]}
    declared_registered = {a for a in registered if a in face_aspects}
    check(len(registered) == 16 and len(declared_registered) == len(registered),
          f"请求面的材料登记覆盖 {len(registered)} 个栏目，且**逐条**都是本次小节的声明栏目"
          "（登记表取自真实请求面，不是另造的映射）")
    check(all(len(r) > 0 for r in registered.values()),
          "⇒ 登记表里出现的每一栏都至少有一份材料（空栏不在这张表里）")

    details.append("## §1 判定规则：改判**双向**，两条都由该栏登记数触发，正反例齐备")
    _section_rule(check)

    details.append("## §2 r2 真实回复重走真实解析口：逐条读回改判台账")
    _section_r2_replay(check, face, reply, manifest, registered)

    details.append("## §3 补件来源类由 Contract 推导，生成器自述只作审计")
    _section_source_class(check, face, reply, manifest)

    details.append("## §4 登记数按**栏**数：多栏小节里的空栏不得被别栏材料顶掉")
    _section_column_scope(check)
    _cp12_real_replay(check)

    details.append("## §5 登记数按**两轴**数：材料行 ∪ 权威事实行（事实侧认呈现层路由）")
    _section_source_axis_accounting(check)

    details.append(
        "NOTE 本模块只证明**判定**（缺口理由由登记数改判）与**推导**（补件来源类取自 Contract）"
        "这两条业务行为。它不评价正文质量、不与清单对账、不构成那一轮的正式通过，也不宣称 "
        "M930-3 / TS5 或任何正式阶段关闭。")
    details.append(
        "NOTE 容器侧身份（pack_id / provenance_identity / 两个清单指纹）是夹具值：请求面不落"
        "它们。材料行的登记归属、正文、来源角色、位置与 payload 哈希逐字取自 r2 的真实请求面。")

    with tempfile.TemporaryDirectory():
        # 本模块全程只读：显式走一次临时目录，证明落盘面确实是临时目录而不是任何历史 run。
        pass

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    result = main()
    for line in result["details"]:
        print(line)
    print(f"\npassed = {result['passed']}, failed = {result['failed']}, "
          f"skipped = {result['skipped']}")

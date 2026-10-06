"""Eval: 本批读回新增的**两份材料去向账**——材料粒度的 `wmdl-1` 与「栏目 × 来源」粒度的 `sarm-1`。

用法: python -m evals.test_m930_3_cited_destination_ledger

本模块钉的是派遣书写回（`scripts/run_m930_3_cited_chain.py` §4.3 / §4.3.2）里的四件事：

1. **材料粒度的去向是四态互斥且穷尽**，每态各有其判据顺序：`read_not_admitted`（读到内容、
   研究侧未获准入）→ `admitted_not_delivered`（获准进 Pack、没进本节写作清单）→
   `delivered_not_used`（进了清单、Writer 侧判本次未采用）→ `used`（正文里有句子引用）。
   另有两态是**缺陷**（账与账对不上），不得读成「一种正常去向」。
2. **两个粒度不得互相顶替**。材料粒度回答的是「这份材料去了哪一栏」；「这一栏在这份来源上
   有没有被读到」的主语是**格子**。把后者压进前者，就会把「这一栏这次没查到」印成「这份来源里
   没有该内容」。本模块用**取值域不相交**这一条把两种词表分开：材料去向态里不得出现任何
   投影终态（`NOT_FOUND_AFTER_SEARCH` 等）的名字。
3. **「被采用」与「写得对」是两列**，不合并。同一句既引用了材料、又在 §6 被判硬错时，
   `cited_sentences` 与 `cited_hard_errors` 必须同时非零；一句引多条材料时，硬错记到它引的
   每条上。
4. **静默降级**（本模块存在的主要理由）。两个账都靠 `getattr(obj, 名字, 默认值)` 从产物上
   读数——**字段名写错不会报错，只会印出一片「—」**。§6 因此逐个断言：账真正读的每个字段名，
   都在对应真实类型的字段表里。这条断言当场抓到了一个真缺陷：出处身份被从 `ResearchMaterial`
   上取（它没有这个字段），于是「逐条可回查」那一列在真 run 里恒为空。
5. **一列的表头必须说得出这一列的主语**。§3b 钉的是同类缺陷的第三种：栏目 × 材料矩阵里
   「算出数／不算数／本轴没判」三列的**主语是小节**（`aspect_attribution` 的判据是
   「所引来源登记在本小节声明的**某一栏**里」，它判不出「这一句服务的是哪一栏」），
   而表头一度把它们写成「（本栏目）」。同一个小节里十几行拿到同一个数，读者会把
   「小节级读数」读成「本栏被 N 句覆盖」。§3b 把粒度差钉成断言：小节级三列相同、
   栏目级两列不同、非本轴的记录不得串入。

夹具是**替身**（不是真实产物）：本模块只钉判定与词表，不证明材料真的取到了、正文达到人读
内容门，也不宣称 M930-3 / TS5 或任何正式阶段关闭。不调 LLM、不联网、不写库。
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_schema as TS                                 # noqa: E402
from scripts import run_m930_3_cited_chain as CHAIN                    # noqa: E402
from sections import cited_writer as CW                                # noqa: E402
from sections import narrative_schema as NS                            # noqa: E402

_SECTION = "company"
_PACK_ID = "pack-company-a"
_TOPIC_ID = "company_business"


# ---------------------------------------------------------------------------
# 替身：只带账**真正会读**的那些字段。字段名的真伪由 §5 对着真实类型逐个核对。
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class _FakeLocator:
    document_id: str = ""

    def to_dict(self) -> dict:
        return {"document_id": self.document_id} if self.document_id else {}


@dataclasses.dataclass(frozen=True)
class _FakeMaterial:
    material_id: str
    material_type: str = "evidence_span"
    source_identity: str = "src-doc-1"
    locator: Any = None


@dataclasses.dataclass(frozen=True)
class _FakeRmd:
    material_id: str
    admission_state: str = "admitted"
    retention_state: str = "retained"
    source_validation: str = "validated"
    container_identity: str = "container-1"
    provenance_identity: str = "provenance-1"
    reason_code: str = "material_admitted"
    reason_proof: str = "proof"
    aspect_ids: tuple = ()
    content_fingerprint: str = "a" * 64


@dataclasses.dataclass(frozen=True)
class _FakePack:
    pack_id: str = _PACK_ID
    topic_id: str = _TOPIC_ID
    materials: tuple = ()
    material_dispositions: tuple = ()
    source_aspect_outcomes: tuple = ()


@dataclasses.dataclass(frozen=True)
class _FakePackSet:
    packs: tuple = ()


@dataclasses.dataclass(frozen=True)
class _FakeAuthority:
    pack_set: Any = None


@dataclasses.dataclass(frozen=True)
class _FakeInputs:
    authorities: dict


@dataclasses.dataclass(frozen=True)
class _FakeMember:
    citation_key: str
    pack_id: str = _PACK_ID
    material_id: str = ""
    topic_id: str = _TOPIC_ID
    document_id: str = "doc-1"
    source_identity: str = "src-doc-1"
    provenance_identity: str = "provenance-1"
    material_type: str = "evidence_span"
    locator_ref: dict = dataclasses.field(default_factory=dict)
    aspect_ids: tuple = ()
    #: §6d 那条数字账要读的原文视图（真类型 `CitedMaterialEntry.reading_view`）。
    reading_view: str = ""


@dataclasses.dataclass(frozen=True)
class _FakeManifest:
    materials: tuple = ()
    facts: tuple = ()
    subsections: tuple = ()


@dataclasses.dataclass(frozen=True)
class _FakeWmpd:
    pack_id: str
    material_id: str
    usage: str
    reason_code: str | None = None
    reason_proof: Any = None
    support_usages: tuple = ()


@dataclasses.dataclass(frozen=True)
class _FakeSentence:
    sentence_id: str
    citations: tuple = ()
    #: §6d 那条数字账要读的句子原文（真类型 `CitedSentence.text`）。
    text: str = ""


@dataclasses.dataclass(frozen=True)
class _FakeParagraph:
    sentences: tuple = ()
    aspect_ids: tuple = ()


@dataclasses.dataclass(frozen=True)
class _FakeDraftSubsection:
    subsection_id: str
    paragraphs: tuple = ()


@dataclasses.dataclass(frozen=True)
class _FakeDraft:
    subsections: tuple = ()
    material_dispositions: tuple = ()
    writer_identity: str = "offline_stub"


@dataclasses.dataclass(frozen=True)
class _FakeDraftNoWriterAxis:
    """**没有** Writer 侧处理去向这一轴的草稿。

    今天的 `CitedProseDraft` 就是这一类（类型上根本没有这个字段）。它与「这一轴在、只是这一节
    一份都没登记」是两回事：把两者读成同一件事，就会把「这条链上没有这一轴」印成
    「二十九份材料各自缺了一条去向」——读回自己造出来的假缺陷。
    """

    subsections: tuple = ()
    writer_identity: str = "offline_stub"


@dataclasses.dataclass(frozen=True)
class _FakeCheckRecord:
    sentence_id: str
    verdict: str
    check_kind: str = "aspect_attribution"
    subsection_id: str = "co-h4"
    applicable: bool = True
    #: §6b 那条数字账要读的 typed 原因码与表面串（真类型
    #: `SentenceCheckRecord.failure_reason` / `.surfaces`）。
    failure_reason: str = ""
    surfaces: tuple = ()


@dataclasses.dataclass(frozen=True)
class _FakeSourceEntry:
    document_id: str
    document_version: str = "sha256-0000"


@dataclasses.dataclass(frozen=True)
class _FakeSourceManifest:
    entries: tuple = ()

    def source_set_entries(self) -> tuple:
        return self.entries


@dataclasses.dataclass(frozen=True)
class _FakeFact:
    """写作清单里的一条**合格事实**行：只声明它属于哪几栏（不声明对应哪一件「事」）。"""

    fact_id: str = "f-1"
    aspect_ids: tuple = ()


@dataclasses.dataclass(frozen=True)
class _FakeContractAspect:
    """冻结 Contract 的一个栏目：逐字给出 `required_fields`（要求）与展示档。"""

    aspect_id: str
    required_fields: tuple = ()
    display_tier: str = ""


@dataclasses.dataclass(frozen=True)
class _FakeRequirement:
    aspects: tuple = ()


@dataclasses.dataclass(frozen=True)
class _FakeMatrixInputs:
    """`_aspect_material_matrix` 真正会读的三处：权威表、有序源集、冻结 Contract 要求。"""

    authorities: dict
    source_manifest: Any = dataclasses.field(default_factory=_FakeSourceManifest)
    requirements: Any = None


@dataclasses.dataclass(frozen=True)
class _FakeSubsectionSpec:
    subsection_id: str
    declared_aspect_ids: tuple = ()
    requirement_text: str = ""


@dataclasses.dataclass(frozen=True)
class _FakeGap:
    subsection_id: str
    reason: str


@dataclasses.dataclass(frozen=True)
class _FakeMatrixDraft:
    subsections: tuple = ()
    gaps: tuple = ()


@dataclasses.dataclass(frozen=True)
class _FakeCheckReport:
    records: tuple = ()


@dataclasses.dataclass(frozen=True)
class _FakeSourceKey:
    document_id: str


@dataclasses.dataclass(frozen=True)
class _FakeSearchRecord:
    synthesized_stop_reason: str = "NOT_FOUND_AFTER_SEARCH"
    projected_terminal: str = ""
    unfulfilled_reason: str = ""
    qualified: bool = False


@dataclasses.dataclass(frozen=True)
class _FakeOutcome:
    aspect_id: str
    document_id: str
    arm: str
    material_ids: tuple = ()
    search_record: Any = None
    not_required_basis: tuple = ()

    @property
    def source_document_key(self) -> _FakeSourceKey:
        return _FakeSourceKey(document_id=self.document_id)


def _inputs(pack: _FakePack) -> _FakeInputs:
    return _FakeInputs(authorities={_SECTION: _FakeAuthority(pack_set=_FakePackSet((pack,)))})


def _ledger(pack: _FakePack, *, manifest, draft, check_report):
    return CHAIN._material_destination_ledger(
        inputs=_inputs(pack), section_id=_SECTION, manifest=manifest, draft=draft,
        check_report=check_report)


def _row_state(*, admission="admitted", in_manifest=True, wmpd="used", cited=False, **kwargs):
    """跑一份单材料 Pack，取那一行的 `(state, defect)`——判定次序在这一条上直接可读。

    `cited=True` 时草稿里真的有一句引用这条材料：`used` 那一态靠的是**这条观测**，
    不是 `wmpd` 自报的 `usage`（因此本夹具把两者分开给）。
    """
    material = _FakeMaterial(material_id="mat-1")
    pack = _FakePack(materials=(material,),
                     material_dispositions=(_FakeRmd(material_id="mat-1",
                                                     admission_state=admission),))
    members = (_FakeMember(citation_key="mat:mat-1", material_id="mat-1"),) if in_manifest else ()
    dispositions = ()
    if wmpd is not None:
        dispositions = (_FakeWmpd(pack_id=_PACK_ID, material_id="mat-1", usage=wmpd,
                                  reason_code=("duplicate"
                                               if wmpd == "not_used" else None)),)
    subsections = ()
    if cited:
        subsections = (_FakeDraftSubsection(subsection_id="co-h4", paragraphs=(
            _FakeParagraph(sentences=(
                _FakeSentence(sentence_id="s-1", citations=("mat:mat-1",)),)),)),)
    ledger = _ledger(pack, manifest=_FakeManifest(materials=members),
                     draft=_FakeDraft(subsections=subsections,
                                      material_dispositions=dispositions),
                     check_report=_FakeCheckReport())
    row = next(r for r in ledger["rows"] if r["material_id"] == "mat-1")
    return ledger, row


# ---------------------------------------------------------------------------

def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    def note(msg: str) -> None:
        details.append(f"NOTE {msg}")

    def skip(msg: str) -> None:
        nonlocal skipped
        skipped += 1
        details.append(f"SKIP: {msg}")

    # ================================== §0 两套词表的取值域（本模块其余断言的基准）
    details.append("## §0 两套词表的取值域逐字取自产物侧，不在读回里另立一套")
    check(tuple(CHAIN.MATERIAL_DESTINATION_STATES) == (
        "read_not_admitted", "admitted_not_delivered", "delivered_not_used", "used"),
        f"材料去向态恰四态且次序固定（实测 {CHAIN.MATERIAL_DESTINATION_STATES}）")
    check(len(set(CHAIN.MATERIAL_DESTINATION_STATES)) == 4,
        "四态互斥（无重复取值）")
    check(set(CHAIN.MATERIAL_DESTINATION_DEFECT_STATES) == {
        "delivered_without_writer_disposition", "in_manifest_without_pack_material"},
        f"缺陷态恰两种，与四态分开（实测 {CHAIN.MATERIAL_DESTINATION_DEFECT_STATES}）")
    check(not (set(CHAIN.MATERIAL_DESTINATION_STATES)
               & set(CHAIN.MATERIAL_DESTINATION_DEFECT_STATES)),
        "缺陷态与去向态**不相交**：对不上账的行不得伪装成一种正常去向")
    drift = set(CHAIN.MATERIAL_DESTINATION_STATES) & set(TS.PROJECTED_TERMINALS)
    check(not drift,
        f"材料去向态与投影终态**取值域不相交**（实测交集 {drift or '空'}）："
        "「源中未定位／已定位未读」在材料粒度上不可表达")
    check(len(TS.SOURCE_ASPECT_OUTCOME_ARMS) == 4
          and set(TS.SOURCE_ASPECT_OUTCOME_ARMS) == {"A", "B", "C1", "C2"},
        f"四臂词表逐字取自 `harness.topic_schema`（实测 {TS.SOURCE_ASPECT_OUTCOME_ARMS}）")
    check(set(NS.MATERIAL_USAGE_STATES) == {"used", "not_used"},
        f"Writer 侧去向码只有两个（实测 {NS.MATERIAL_USAGE_STATES}）："
        "研究侧准入与 Writer 侧采用是两套身份，不得混填")

    # ================================== §1 四态判定：判据次序在行里直接可读
    details.append("## §1 材料去向四态：逐态跑一份单材料 Pack，看它落在哪一态")
    ledger, row = _row_state(admission="rejected", in_manifest=False, wmpd=None)
    check(row["state"] == "read_not_admitted" and not row["defect"],
        "研究侧未获准入 ⇒ `read_not_admitted`（**不是**缺陷，也**不是** gap）："
        f"实测 {row['state']!r}")
    check(row["rmd_reason_code"] == "material_admitted",
        "该态如实带出研究侧那一套理由码（照抄，不在读回里翻译）")

    # 已获准入、但清单里没有：即便 Writer 侧也没有处置，也不该报「缺 Writer 处置」——
    # 那是**没送达**，不是**送达了没人处理**。判据次序在这里必须挡住第二种读法。
    _ledger2, row = _row_state(admission="admitted", in_manifest=False, wmpd="not_used")
    check(row["state"] == "admitted_not_delivered" and not row["defect"],
        "获准进 Pack、未进本节清单 ⇒ `admitted_not_delivered`（**不是**缺陷）："
        f"实测 {row['state']!r}")

    _ledger3, row = _row_state(admission="admitted", in_manifest=True, wmpd=None)
    check(row["state"] == "delivered_without_writer_disposition" and row["defect"],
        "进了清单却**没有** Writer 侧处理去向 ⇒ 缺陷态（台账对不上，不得静默）："
        f"实测 {row['state']!r}")

    _ledger4, row = _row_state(admission="admitted", in_manifest=True, wmpd="not_used")
    check(row["state"] == "delivered_not_used" and not row["defect"],
        f"进了清单、Writer 判本次未采用 ⇒ `delivered_not_used`：实测 {row['state']!r}")
    check(row["writer_reason_code"] == "duplicate"
          and row["writer_reason_code"] in NS.MATERIAL_NOT_USED_REASONS,
        "未采用的理由码逐条照抄 **Writer 侧**那一套封闭码（不是研究侧的理由码），"
        "且该码确实落在封闭表内——读回不在这里翻译成自由文本")

    _ledger5, row = _row_state(admission="admitted", in_manifest=True, wmpd="used",
                               cited=True)
    check(row["state"] == "used" and not row["defect"],
        f"正文里有句子引用它 ⇒ `used`：实测 {row['state']!r}")
    # 反例：自报「用了」但正文里找不到任何引用它的句子 —— 四态**不**采信这条自报。
    _ledger5b, row = _row_state(admission="admitted", in_manifest=True, wmpd="used")
    check(row["state"] == "delivered_not_used",
        "反例：`usage=\"used\"` 而零引用 ⇒ 仍落 `delivered_not_used`——"
        f"`used` 是观测不是自报（实测 {row['state']!r}）")

    observed = {next(r for r in lg["rows"] if r["material_id"] == "mat-1")["state"]
                for lg in (ledger, _ledger2, _ledger3, _ledger4, _ledger5)}
    check(observed == set(CHAIN.MATERIAL_DESTINATION_STATES)
          | {"delivered_without_writer_disposition"},
        "五份夹具跑遍了四态 + 一种缺陷态：词表**没有用不到的取值**（不是摆设）")
    check(all(s in (set(CHAIN.MATERIAL_DESTINATION_STATES)
                    | set(CHAIN.MATERIAL_DESTINATION_DEFECT_STATES)) for s in observed),
        "所有产出态都落在声明的取值域内（不凭空造态）")

    # 反例：草稿**整条轴都不在**时，「进了清单却没有去向」不得逐份报成缺陷——
    # 缺的不是这一份的去向，是这一条链的这一轴。今天 `CitedProseDraft` 正是如此。
    pack = _FakePack(materials=(_FakeMaterial(material_id="mat-1"),),
                     material_dispositions=(_FakeRmd(material_id="mat-1"),))
    manifest = _FakeManifest(materials=(_FakeMember(citation_key="mat:mat-1",
                                                    material_id="mat-1"),))
    ledger_noaxis = _ledger(pack, manifest=manifest, draft=_FakeDraftNoWriterAxis(),
                            check_report=_FakeCheckReport())
    check(ledger_noaxis["writer_axis_present"] is False,
        "草稿上没有这一轴时 `writer_axis_present=False`：读回先问「轴在不在」，再去取它的值")
    check(not ledger_noaxis["defects"],
        f"**不逐份报假缺陷**（实测缺陷表 {ledger_noaxis['defects'] or '空'}）："
        "29 份材料各自被印成「缺去向」，掩盖的恰恰是唯一的真事实——这条链上没有这一轴")
    row_noaxis = next(r for r in ledger_noaxis["rows"] if r["material_id"] == "mat-1")
    check(row_noaxis["state"] == "delivered_not_used" and row_noaxis["cited_sentences"] == 0,
        f"没被引用 ⇒ `delivered_not_used`（只说「没写出去」）：实测 {row_noaxis['state']!r}")
    check(row_noaxis["writer_usage"] == "" and row_noaxis["writer_reason_code"] is None,
        "理由码本链取不到就如实为空，不拿研究侧的理由码顶上")

    # 反例的另一半：同一份草稿若真带着这一轴而这一份没有登记，那才是缺陷。
    ledger_axis = _ledger(pack, manifest=manifest, draft=_FakeDraft(),
                          check_report=_FakeCheckReport())
    check(ledger_axis["writer_axis_present"] is True
          and ledger_axis["defects"].get("delivered_without_writer_disposition") == 1,
        "轴**在**而这一份没登记 ⇒ 缺陷（两件事分得开，不是一句话两种读法）")

    # `used` 是**观测**：正文里确有句子引用它，不需要谁自报「我用了」。
    cited_draft = _FakeDraftNoWriterAxis(subsections=(_FakeDraftSubsection(
        subsection_id="co-h4", paragraphs=(_FakeParagraph(sentences=(
            _FakeSentence(sentence_id="s-1", citations=("mat:mat-1",)),)),)),))
    row_cited = next(r for r in _ledger(pack, manifest=manifest, draft=cited_draft,
                                        check_report=_FakeCheckReport())["rows"]
                     if r["material_id"] == "mat-1")
    check(row_cited["state"] == "used" and row_cited["cited_sentences"] == 1,
        "观测优先于自报：一句引用了它 ⇒ `used`，即便这条链上根本没有作者侧自报这一轴")

    # ================================== §2 清单里有、Pack 里没有 = 缺陷态，如实列出
    details.append("## §2 「清单里有、本节 Pack 里没有」：对不上账时如实列出，不静默吞掉")
    pack = _FakePack(materials=(), material_dispositions=())
    ghost = _FakeMember(citation_key="mat:mat-ghost", material_id="mat-ghost")
    ledger = _ledger(pack, manifest=_FakeManifest(materials=(ghost,)),
                     draft=_FakeDraft(), check_report=_FakeCheckReport())
    ghosts = [r for r in ledger["rows"] if r["state"] == "in_manifest_without_pack_material"]
    check(len(ghosts) == 1 and ghosts[0]["defect"] is True,
        f"该行被列出且标缺陷（实测 {len(ghosts)} 行）")
    check(ledger["defects"].get("in_manifest_without_pack_material") == 1,
        "缺陷计数按**缺陷表**统计，不混入去向计数")
    check(ledger["counts"] == {},
        "四态计数里**没有**这一行（缺陷态不是一种去向）")
    check(ledger["writer_material_count"] == 1 and ledger["pack_material_count"] == 0,
        "两个计数口径分别保留（清单 1 份 / Pack 0 份）：两个数不相等本身就是读数")

    # ================================== §3 「被采用」与「写得对」是两列
    details.append("## §3 「引用它的句数」与「其中带硬错」并排给出：被采用 ≠ 写得对")
    pack = _FakePack(
        materials=(_FakeMaterial(material_id="mat-1"), _FakeMaterial(material_id="mat-2")),
        material_dispositions=(_FakeRmd(material_id="mat-1"), _FakeRmd(material_id="mat-2")))
    key, key2 = "mat:mat-1", "mat:mat-2"
    manifest = _FakeManifest(materials=(
        _FakeMember(citation_key=key, material_id="mat-1"),
        _FakeMember(citation_key=key2, material_id="mat-2")))
    draft = _FakeDraft(
        material_dispositions=(
            _FakeWmpd(pack_id=_PACK_ID, material_id="mat-1", usage="used",
                      support_usages=("p-1",)),
            _FakeWmpd(pack_id=_PACK_ID, material_id="mat-2", usage="used",
                      support_usages=("p-2",))),
        subsections=(_FakeDraftSubsection(subsection_id="co-h4", paragraphs=(
            _FakeParagraph(sentences=(
                _FakeSentence(sentence_id="s-1", citations=(key,)),
                _FakeSentence(sentence_id="s-2", citations=(key,)),
            )),
            # 一句同时引**两条不同材料**：它的硬错要记到它引的**每条**材料上。
            _FakeParagraph(sentences=(
                _FakeSentence(sentence_id="s-3", citations=(key, key2)),
            )),
        )),),
    )
    check_report = _FakeCheckReport(records=(
        _FakeCheckRecord(sentence_id="s-1", verdict="ok"),
        _FakeCheckRecord(sentence_id="s-2", verdict="hard_error"),
        _FakeCheckRecord(sentence_id="s-3", verdict="hard_error"),
    ))
    ledger = _ledger(pack, manifest=manifest, draft=draft, check_report=check_report)
    row = next(r for r in ledger["rows"] if r["material_id"] == "mat-1")
    row2 = next(r for r in ledger["rows"] if r["material_id"] == "mat-2")
    check(row["state"] == "used" and row2["state"] == "used",
        f"两份都被引用了 ⇒ 都落 `used`：实测 {row['state']!r} / {row2['state']!r}")
    check(row["cited_sentences"] == 3,
        f"引用它的句数逐句累加：实测 {row['cited_sentences']}")
    check(row["cited_hard_errors"] == 2,
        f"其中带硬错的句数另计（实测 {row['cited_hard_errors']}）：两列非零**同时**出现，"
        "读者能直接看到「用上了但用错了」")
    check(row2["cited_sentences"] == 1 and row2["cited_hard_errors"] == 1,
        "一句同时引两条材料时，这次引用与它的硬错记到它引的**每条**材料上"
        f"（实测 mat-2：{row2['cited_sentences']} 句 / {row2['cited_hard_errors']} 硬错）")
    check(row["support_usages"] == ("p-1",),
        "Support usage 逐条照抄 Writer 侧（不在读回里重算）")

    # 反例：同一批句子全部判 ok 时，硬错列必须归零——否则上一断言的 2 只是个常数。
    calm = _FakeCheckReport(records=tuple(
        _FakeCheckRecord(sentence_id=s, verdict="ok") for s in ("s-1", "s-2", "s-3")))
    row_ok = next(r for r in _ledger(pack, manifest=manifest, draft=draft,
                                     check_report=calm)["rows"]
                  if r["material_id"] == "mat-1")
    check(row_ok["cited_hard_errors"] == 0 and row_ok["cited_sentences"] == 3,
        "反例：全部判 ok ⇒ 硬错列归零而引用列不变——两列确实各走各的")

    # ================================== §3b 栏目 × 材料矩阵里三列的**主语**是小节，不是本栏
    details.append("## §3b 栏目 × 材料矩阵：把「小节级读数」摆在同一行里，读者须看得出它的主语")
    # `aspect_attribution` 的判据是「本句所引来源登记在本小节声明的**某一栏**里」
    # （`sections/sentence_check.py` 轴 7：`covered = registered & set(declared_set)`）。
    # 它**判不出**「这一句服务的是哪一栏」，因此三个桶只能按 `subsection_id` 归档。
    # 这一节里十几行并排时，这三列必然是同一个数——读的人若把 3 读成「本栏被 3 句覆盖」，
    # 就把小节级的读数冒充成了栏目级的结论。本段把这个粒度差钉成可执行断言。
    matrix_pack = _FakePack(
        materials=(_FakeMaterial(material_id="mat-1", locator=_FakeLocator("doc-1")),
                   _FakeMaterial(material_id="mat-2", locator=_FakeLocator("doc-2"))),
        material_dispositions=(_FakeRmd(material_id="mat-1",
                                        aspect_ids=("a-1", "a-2")),
                               _FakeRmd(material_id="mat-2", aspect_ids=("a-9",))))
    matrix_inputs = _FakeMatrixInputs(
        authorities={_SECTION: _FakeAuthority(pack_set=_FakePackSet((matrix_pack,)))},
        source_manifest=_FakeSourceManifest(entries=(_FakeSourceEntry("doc-1"),
                                                     _FakeSourceEntry("doc-2"))))
    matrix_manifest = _FakeManifest(
        materials=(_FakeMember(citation_key="mat:mat-1", material_id="mat-1"),
                   _FakeMember(citation_key="mat:mat-2", material_id="mat-2")),
        subsections=(_FakeSubsectionSpec(subsection_id="co-h4",
                                         declared_aspect_ids=("a-1", "a-2"),
                                         requirement_text="主营业务构成"),
                     _FakeSubsectionSpec(subsection_id="co-h5",
                                         declared_aspect_ids=("a-9",),
                                         requirement_text="经营模式")))
    matrix_draft = _FakeMatrixDraft(
        subsections=(_FakeDraftSubsection(subsection_id="co-h4", paragraphs=(
            _FakeParagraph(aspect_ids=("a-1",), sentences=(
                _FakeSentence(sentence_id="s-1", citations=("mat:mat-1",)),
                _FakeSentence(sentence_id="s-2", citations=("mat:mat-1",)))),
            _FakeParagraph(aspect_ids=("a-2",), sentences=(
                _FakeSentence(sentence_id="s-3", citations=("mat:mat-1",)),)),
        )),),
        gaps=(_FakeGap(subsection_id="co-h4", reason="no_source_in_manifest"),))
    matrix_report = _FakeCheckReport(records=(
        _FakeCheckRecord(sentence_id="s-1", verdict="pass"),
        _FakeCheckRecord(sentence_id="s-2", verdict="hard_error"),
        _FakeCheckRecord(sentence_id="s-3", verdict="pass", applicable=False),
        # 另一条轴上的记录（同一小节）：不得被算进这三个桶。
        _FakeCheckRecord(sentence_id="s-1", verdict="hard_error",
                         check_kind="numeric_surface"),
        # 同一批句子的**另一个小节**：三个桶因此各归各小节，不串。
        _FakeCheckRecord(sentence_id="s-4", verdict="pass", subsection_id="co-h5"),
    ))
    matrix = CHAIN._aspect_material_matrix(
        inputs=matrix_inputs, section_id=_SECTION, manifest=matrix_manifest,
        draft=matrix_draft, check_report=matrix_report)
    rows_by_aspect = {r["aspect_id"]: r for r in matrix["rows"]}
    a1, a2, a9 = rows_by_aspect["a-1"], rows_by_aspect["a-2"], rows_by_aspect["a-9"]
    check((a1["sentences_in_column"], a1["sentences_out_of_column"],
           a1["sentences_axis_not_applicable"]) == (1, 1, 1),
        f"本小节三桶逐桶计数（实测 {(a1['sentences_in_column'], a1['sentences_out_of_column'], a1['sentences_axis_not_applicable'])}）："
        "只按 `verdict` 分两桶会把「本轴不适用」的 `pass` 记成「算数」")
    check((a1["sentences_in_column"], a1["sentences_out_of_column"],
           a1["sentences_axis_not_applicable"])
          == (a2["sentences_in_column"], a2["sentences_out_of_column"],
              a2["sentences_axis_not_applicable"]),
        "同属一个小节的两栏拿到**完全相同**的三桶值：这三列的**主语是小节**——"
        "它们不是逐栏判出来的，读回据此把表头写成「（**本小节**，非本栏）」")
    check(a9["sentences_in_column"] == 1 and a9["sentences_out_of_column"] == 0,
        f"换一个小节，三桶随之各归各（实测 a-9：{a9['sentences_in_column']}）："
        "归档键是 `subsection_id`，不是全局累计")
    check((a1["paragraphs_declaring"], a1["sentences_declared"]) == (1, 2)
          and (a2["paragraphs_declaring"], a2["sentences_declared"]) == (1, 1),
        "**栏目级**的分辨力只在左边两列（`cw-4` 段级声明轴）：a-1 有 1 段 2 句、a-2 有 1 段 1 句，"
        "两者不同——右边三列做不到这件事。栏目级的读数不许由右边三列补上")
    check(a1["sentences"] == 3 and a2["sentences"] == 3,
        "「本小节写出句数」同样是小节级（两栏都是 3）：它说的是小节写过多少，不是本栏写过多少")
    check(a1["in_pack"] == {"doc-1": 1} and a9["in_pack"] == {"doc-2": 1},
        f"「进 Pack」逐栏各算（实测 a-1 {a1['in_pack']} / a-9 {a9['in_pack']}）："
        "与小节级的栏目归属轴不是同一条读数")
    check(a1["gaps"] == ["no_source_in_manifest"] and a9["gaps"] == [],
        "缺口挂在小节上（本节的草稿只有一条 gap）：逐栏缺口不由右边三列推")
    # 缺口列的**主语是小节**：同一个小节下的十几行印的是**同一条**。读的人若把它读成
    # 「这一栏各自缺了一条」，就把小节级的读数冒充成了栏目级的证明——渲染侧因此把表头写成
    # 「缺口（**本小节**，非本栏）」。这一条把「同小节各行同值」钉成可执行断言。
    two_gaps = CHAIN._aspect_material_matrix(
        inputs=matrix_inputs, section_id=_SECTION, manifest=matrix_manifest,
        draft=dataclasses.replace(matrix_draft, gaps=(
            _FakeGap(subsection_id="co-h4", reason="no_source_in_manifest"),
            _FakeGap(subsection_id="co-h4", reason="source_present_but_not_admissible"))),
        check_report=matrix_report)
    two_rows = {r["aspect_id"]: r for r in two_gaps["rows"]}
    check(two_rows["a-1"]["gaps"] == two_rows["a-2"]["gaps"]
          == ["no_source_in_manifest", "source_present_but_not_admissible"]
          and two_rows["a-9"]["gaps"] == [],
        f"同小节两栏拿到**逐字相同**的缺口清单（实测 a-1 {two_rows['a-1']['gaps']}）："
        "缺口列的主语是小节，不是本栏——渲染侧据此刻意把它标成「（**本小节**，非本栏）」")
    # 反例：把非 `aspect_attribution` 的记录算进来，三桶就会变——上一条断言的 1/1/1 才不是常数。
    noisy = _FakeCheckReport(records=tuple(
        r for r in matrix_report.records if r.check_kind == "aspect_attribution"
        or r.subsection_id == "co-h5"))
    noisy = noisy.records + (_FakeCheckRecord(sentence_id="s-2", verdict="hard_error",
                                              check_kind="numeric_surface"),)
    matrix_noisy = CHAIN._aspect_material_matrix(
        inputs=matrix_inputs, section_id=_SECTION, manifest=matrix_manifest,
        draft=matrix_draft, check_report=_FakeCheckReport(records=noisy))
    noisy_rows = {r["aspect_id"]: r for r in matrix_noisy["rows"]}
    check(noisy_rows["a-1"]["sentences_in_column"] == 1
          and noisy_rows["a-1"]["sentences_out_of_column"] == 1,
        "反例：再加一条 `numeric_surface` 的硬错记录，三桶**一个都不动**——"
        "这条轴只认 `aspect_attribution`，别的轴的结论不得串进来")

    # ================================== §3c 逐栏「要了字段、取得多少」并排（`cfgap-1`）
    details.append("## §3c 栏目要了哪些字段 × 本栏名下有几条合格事实：**要求**与**取得**并排")
    # 这一节要钉住的是「本栏写了话」与「本栏要的字段拿到了」是两件事：前者看句子，后者看
    # 冻结 Contract 的 `required_fields` 与本栏名下的合格事实条数。判据必须是两者的**交集**：
    # 「要了字段」而「零条事实」才报缺口；「没要字段」不报（那不是缺口），
    # 「要了字段且有事实」也不报（那是栏目级已有取得，虽然字段级支撑仍判不出来）。
    field_pack = _FakePack(
        materials=(_FakeMaterial(material_id="mat-1", locator=_FakeLocator("doc-1")),),
        material_dispositions=(_FakeRmd(material_id="mat-1", aspect_ids=("a-1", "a-2")),))
    field_inputs = _FakeMatrixInputs(
        authorities={_SECTION: _FakeAuthority(pack_set=_FakePackSet((field_pack,)))},
        source_manifest=_FakeSourceManifest(entries=(_FakeSourceEntry("doc-1"),)),
        requirements={"req-biz": _FakeRequirement(aspects=(
            _FakeContractAspect(
                aspect_id="a-1", display_tier="required_body",
                required_fields=("前五大客户合计销售占比", "是否含关联方", "披露范围口径")),
            _FakeContractAspect(aspect_id="a-2", display_tier="required_body",
                                required_fields=("报告期客户名单",)),
            # 有事实、有档，但 Contract 一个字段都没要：这不是缺口。
            _FakeContractAspect(aspect_id="a-9", display_tier="required_body",
                                required_fields=()),
        ))})
    field_manifest = _FakeManifest(
        materials=(_FakeMember(citation_key="mat:mat-1", material_id="mat-1"),),
        # 事实只挂在 a-2 名下：a-1 要了三件事，本栏名下一条事实都没有。
        facts=(_FakeFact(fact_id="f-1", aspect_ids=("a-2",)),),
        subsections=(_FakeSubsectionSpec(subsection_id="co-h4",
                                         declared_aspect_ids=("a-1", "a-2", "a-9"),
                                         requirement_text="销售与客户"),))
    field_matrix = CHAIN._aspect_material_matrix(
        inputs=field_inputs, section_id=_SECTION, manifest=field_manifest,
        draft=_FakeMatrixDraft(), check_report=_FakeCheckReport())
    field_rows = {r["aspect_id"]: r for r in field_matrix["rows"]}
    gap_code = CHAIN.ASPECT_FIELD_GAP_REQUIRED_FIELDS_WITHOUT_FACT
    check(field_rows["a-1"]["required_fields"]
          == ("前五大客户合计销售占比", "是否含关联方", "披露范围口径")
          and field_rows["a-1"]["required_field_count"] == 3,
        f"逐栏字段**逐字**照抄冻结 Contract（实测 {field_rows['a-1']['required_fields']}）："
        "读回不替它归纳，也不把要求翻译成一句自由文本")
    check(field_rows["a-1"]["contract_display_tier"] == "required_body"
          and field_rows["a-1"]["qualified_facts"] == 0,
        f"展示档同样是契约字面值（实测 {field_rows['a-1']['contract_display_tier']!r}），"
        "与本栏名下事实条数**各列各的**，不互相推算")
    check(field_rows["a-1"]["field_gaps"] == (gap_code,),
        f"要了字段而本栏零条合格事实 ⇒ 逐栏报出缺口（实测 {field_rows['a-1']['field_gaps']}）："
        "这是**读取 / 资格 / 交付**缺口，不是「来源称不适用」，也不是「用户缺件」")
    check(field_rows["a-2"]["required_field_count"] == 1
          and field_rows["a-2"]["qualified_facts"] == 1
          and field_rows["a-2"]["field_gaps"] == (),
        "同一栏既有要求也有一条事实 ⇒ **不**报缺口：这张表不把「取得了一条」升格成"
        "「三件事各有几条」——字段级支撑它判不出来，因此也无从报字段级缺口")
    check(field_rows["a-9"]["required_field_count"] == 0
          and field_rows["a-9"]["qualified_facts"] == 0
          and field_rows["a-9"]["field_gaps"] == (),
        "Contract 对本栏一个字段都没要时**不**报缺口：缺口是「要了没拿到」的交集，"
        "不是「这里什么都没有」的同义词")
    # 反例：把 Contract 要求整批撤掉，缺口必须**全部**消失——否则上一条的 a-1 只是个常数。
    no_req = CHAIN._aspect_material_matrix(
        inputs=_FakeMatrixInputs(
            authorities=field_inputs.authorities,
            source_manifest=field_inputs.source_manifest, requirements=None),
        section_id=_SECTION, manifest=field_manifest,
        draft=_FakeMatrixDraft(), check_report=_FakeCheckReport())
    check(all(r["field_gaps"] == () for r in no_req["rows"]),
        "反例：本节没有 Contract 要求这一轴时，逐栏缺口一律为空——"
        "读回不靠「事实条数为零」自己编出一条缺口来")
    # 反例：同一条事实挪到 a-1 名下，a-1 的缺口随之消失、a-2 的随之出现——交集的另一侧也动。
    swapped = CHAIN._aspect_material_matrix(
        inputs=field_inputs, section_id=_SECTION,
        manifest=dataclasses.replace(
            field_manifest, facts=(_FakeFact(fact_id="f-1", aspect_ids=("a-1",)),)),
        draft=_FakeMatrixDraft(), check_report=_FakeCheckReport())
    swapped_rows = {r["aspect_id"]: r for r in swapped["rows"]}
    check(swapped_rows["a-1"]["field_gaps"] == ()
          and swapped_rows["a-2"]["field_gaps"] == (gap_code,),
        "反例：把唯一一条事实从 a-2 挪到 a-1，缺口跟着**换栏**——判据在「要求 ∩ 取得」，"
        "不是某一栏的常数")

    # ================================== §4 没有 Pack 材料边界的一支：不适用，不是全零
    details.append("## §4 财务 artifact 那一支没有 Pack 材料边界：这张账**不适用**，不是全零")
    no_pack = _FakeInputs(authorities={_SECTION: _FakeAuthority(pack_set=None)})
    check(CHAIN._material_destination_ledger(
        inputs=no_pack, section_id=_SECTION, manifest=_FakeManifest(),
        draft=_FakeDraft(), check_report=_FakeCheckReport()) is None,
        "材料去向账返回 `None`（不适用）——而不是一张全为「未送达」的表")
    check(CHAIN._source_arm_ledger(inputs=no_pack, section_id=_SECTION) is None,
        "四臂台账同样返回 `None`：两支同一约定")
    check(CHAIN._material_destination_ledger(
        inputs=_FakeInputs(authorities={}), section_id="industry",
        manifest=_FakeManifest(), draft=_FakeDraft(),
        check_report=_FakeCheckReport()) is None,
        "本节根本不在权威表里时也返回 `None`（不凭空造一张空表）")

    # ================================== §5 四臂台账：逐 (栏目 × 来源) 一格一条
    details.append("## §5 四臂台账：主语是「格子」，与材料粒度分开成两张表")
    outcomes = (
        _FakeOutcome(aspect_id="a-1", document_id="doc-1", arm="A", material_ids=("mat-1",)),
        _FakeOutcome(aspect_id="a-1", document_id="doc-2", arm="B",
                     search_record=_FakeSearchRecord(projected_terminal="NOT_FOUND_AFTER_SEARCH")),
        _FakeOutcome(aspect_id="a-2", document_id="doc-1", arm="B",
                     search_record=_FakeSearchRecord(
                         projected_terminal="UNQUALIFIED_SEARCH_OBSERVATION", qualified=False)),
        _FakeOutcome(aspect_id="a-2", document_id="doc-2", arm="C1",
                     not_required_basis=(("rule", "source_not_applicable"),)),
        _FakeOutcome(aspect_id="a-3", document_id="doc-1", arm="C2",
                     search_record=_FakeSearchRecord(
                         projected_terminal="UNFULFILLED_REQUIRED_SEARCH",
                         unfulfilled_reason="budget_exhausted")),
    )
    arms = CHAIN._source_arm_ledger(
        inputs=_inputs(_FakePack(source_aspect_outcomes=outcomes)), section_id=_SECTION)
    check(arms["record_count"] == 5,
        f"逐 `(栏目, 来源)` 一格一条（实测 {arms['record_count']}）")
    check(arms["arm_counts"] == {"A": 1, "B": 2, "C1": 1, "C2": 1},
        f"四臂分别计数（实测 {arms['arm_counts']}）")
    check(set(arms["arm_counts"]) <= set(TS.SOURCE_ASPECT_OUTCOME_ARMS),
        "臂的取值全部落在产物侧词表内（读回不另立一套臂名）")
    check(arms["documents"] == ("doc-1", "doc-2"),
        f"来源文档去重且保序（实测 {arms['documents']}）")
    check(arms["aspect_ids"] == ("a-1", "a-2", "a-3"),
        f"栏目去重且保序（实测 {arms['aspect_ids']}）")
    terminals = {r["projected_terminal"] for r in arms["rows"] if r["projected_terminal"]}
    check(terminals <= set(TS.PROJECTED_TERMINALS),
        f"投影终态逐字取自产物侧（实测 {sorted(terminals)}）")
    c2 = next(r for r in arms["rows"] if r["arm"] == "C2")
    check(c2["unfulfilled_reason"] == "budget_exhausted" and c2["arm"] == "C2",
        "`C2` 单独带「要求检索但本轮未查成」的原因：这一格的读数**不能**与 `B` 混读")
    b_rows = {(r["aspect_id"], r["document_id"]): r for r in arms["rows"] if r["arm"] == "B"}
    check(b_rows[("a-1", "doc-2")]["projected_terminal"] == "NOT_FOUND_AFTER_SEARCH"
          and b_rows[("a-1", "doc-2")]["qualified"] is False,
        "`B` 臂带 `NOT_FOUND_AFTER_SEARCH` 才说得上「源中未定位」")
    check(b_rows[("a-2", "doc-1")]["projected_terminal"] == "UNQUALIFIED_SEARCH_OBSERVATION",
        "`B` 臂不合格也逐条在册：「搜过但观察条件不齐」与「没搜到」是两条**不同**的读数，"
        "同一臂内不得只印一个终态")
    check(all("material_ids" in r and "qualified" in r for r in arms["rows"]),
        "A 臂的材料归属与非 A 臂的合格与否在同一张表的行里各自有位（不漏字段）")

    # ================================== §5b 同一格并排登记轴：`C2` 不是「没有材料」
    details.append("## §5b `C2` 只说「没有检索痕迹」：旁边必须摆上「这一格在 Pack 登记里有没有材料」")
    # 臂的判据是 `if traces and materials`：有材料、只是没留下检索痕迹的那些格也会落到
    # `not_dispatched`。只看臂，读者会把「已有材料、只是没有检索痕迹」读成「这一栏在这份
    # 来源上什么都没有」。登记轴因此逐格并排，把 `C2` 细分成两个**不同**的读数。
    reg_pack = _FakePack(
        materials=(_FakeMaterial(material_id="mat-1", locator=_FakeLocator("doc-1")),),
        material_dispositions=(_FakeRmd(material_id="mat-1", aspect_ids=("a-1",)),))
    reg_outcomes = (
        # (a-1, doc-1)：有材料、只是没检索痕迹 ⇒ `C2` 但登记不为零。
        _FakeOutcome(aspect_id="a-1", document_id="doc-1", arm="C2",
                     search_record=_FakeSearchRecord(
                         projected_terminal="UNFULFILLED_REQUIRED_SEARCH",
                         unfulfilled_reason="budget_exhausted")),
        # (a-1, doc-2)：既无材料也无痕迹。
        _FakeOutcome(aspect_id="a-1", document_id="doc-2", arm="C2",
                     search_record=_FakeSearchRecord(
                         projected_terminal="UNFULFILLED_REQUIRED_SEARCH",
                         unfulfilled_reason="budget_exhausted")),
    )
    reg_arms = CHAIN._source_arm_ledger(
        inputs=_inputs(dataclasses.replace(reg_pack, source_aspect_outcomes=reg_outcomes)),
        section_id=_SECTION)
    reg_rows = {(r["aspect_id"], r["document_id"]): r for r in reg_arms["rows"]}
    check(reg_rows[("a-1", "doc-1")]["registered_materials"] == 1
          and reg_rows[("a-1", "doc-2")]["registered_materials"] == 0,
        f"同一臂的两格，登记轴读数**不同**（实测 "
        f"{reg_rows[('a-1', 'doc-1')]['registered_materials']} / "
        f"{reg_rows[('a-1', 'doc-2')]['registered_materials']}）："
        "「已有材料、只是没有检索痕迹」与「既无材料也无痕迹」由此可分")
    check(reg_rows[("a-1", "doc-1")]["arm"] == reg_rows[("a-1", "doc-2")]["arm"] == "C2",
        "两格臂值**相同**：读回不借登记轴去改臂（臂的判定属于 `harness/topic_runtime`），"
        "只把两条轴并排")
    check("registered_materials" not in arms["rows"][0]
          or arms["rows"][0]["registered_materials"] == 0,
        "旧的 §5 台账（Pack 里没有材料行）逐格登记数为零——"
        "不因为这一批新增了登记轴，就把没登记的格印成有材料")
    # 反例：登记沿材料自己的 `locator.document_id` 归位，不按落在哪个 outcome 上就近分配。
    reg_pack2 = dataclasses.replace(
        reg_pack, materials=(
            _FakeMaterial(material_id="mat-1", locator=_FakeLocator("doc-2")),),
        material_dispositions=(_FakeRmd(material_id="mat-1", aspect_ids=("a-1",)),))
    reg_arms2 = CHAIN._source_arm_ledger(
        inputs=_inputs(dataclasses.replace(reg_pack2, source_aspect_outcomes=reg_outcomes)),
        section_id=_SECTION)
    reg2 = {(r["aspect_id"], r["document_id"]): r for r in reg_arms2["rows"]}
    check(reg2[("a-1", "doc-1")]["registered_materials"] == 0
          and reg2[("a-1", "doc-2")]["registered_materials"] == 1,
        "反例：把材料挪到 doc-2，登记数**跟着挪格**——归位键是 `(栏目, 材料自己的 document_id)`，"
        "不是「这一栏下随便哪一格」")

    # ================================== §6 静默降级：账读的每个字段名都要真的存在于真实类型上
    details.append("## §6 静默降级检查：`getattr(x, 名字, 默认值)` 写错只会印成「—」，不会报错")
    rmd_fields = {f.name for f in dataclasses.fields(TS.ResearchMaterialDisposition)}
    material_fields = {f.name for f in dataclasses.fields(TS.ResearchMaterial)}
    member_fields = {f.name for f in dataclasses.fields(CW.CitedMaterialEntry)}
    wmpd_fields = {f.name for f in dataclasses.fields(NS.WriterMaterialProcessingDisposition)}
    outcome_fields = {f.name for f in dataclasses.fields(TS.SourceAspectOutcome)}
    record_fields = {f.name for f in dataclasses.fields(TS.SourceSearchOutcomeRecord)}

    read_from_rmd = {"material_id", "container_identity", "provenance_identity", "admission_state",
                     "retention_state", "source_validation", "reason_code", "reason_proof",
                     "aspect_ids", "content_fingerprint"}
    read_from_material = {"material_id", "source_identity", "material_type", "locator"}
    read_from_member = {"pack_id", "material_id", "citation_key", "topic_id", "document_id",
                        "source_identity", "provenance_identity", "material_type", "locator_ref",
                        "aspect_ids"}
    read_from_wmpd = {"pack_id", "material_id", "usage", "reason_code", "reason_proof",
                      "support_usages"}
    read_from_outcome = {"aspect_id", "arm", "material_ids", "search_record", "not_required_basis",
                         "source_document_key"}
    read_from_record = {"projected_terminal", "synthesized_stop_reason", "unfulfilled_reason",
                        "qualified"}

    for label, names, real in (("研究侧登记 RMD", read_from_rmd, rmd_fields),
                               ("材料", read_from_material, material_fields),
                               ("清单成员", read_from_member, member_fields),
                               ("Writer 侧去向 WMPD", read_from_wmpd, wmpd_fields),
                               ("四臂结果", read_from_outcome, outcome_fields),
                               ("检索记录", read_from_record, record_fields)):
        missing = sorted(names - real)
        check(not missing,
              f"{label}：账读的 {len(names)} 个字段名都在真实类型上"
              f"（缺失 {missing or '无'}）——缺一个就会静默读成空串")

    # 反例：这一条断言本身要能抓到「字段名不存在」。用一个真不存在的名字试一次。
    check("provenance_identity" not in material_fields,
        "反例（本批抓到过的真缺陷）：`ResearchMaterial` **没有** `provenance_identity` 字段——"
        "出处身份只能从研究侧登记上取；从材料上取会恒为空，把「逐条可回查」印成一片「—」")
    check("provenance_identity" in rmd_fields,
        "而它在 RMD 上确实存在：修法是把这一列改从登记侧读，不是把材料侧补一个字段")

    # 真实产物的口径也要对上：CitedMaterialEntry 的 `document_id` 允许为空（结论，不是缺失）
    check("document_id" in member_fields and "provenance_identity" in member_fields,
        "清单成员自带文档身份与出处身份两轴：非文档系列材料上为空串是**结论**，读回据此印"
        "「（无文档系列）」而不是「缺」")

    # 「这一轴在不在」这件事本身也要钉住：`CitedProseDraft` 今天**没有** Writer 侧处置轴。
    # 它不是本批要修的东西（本批不新建第三套身份），但读回必须**知道**它没有——否则就会
    # 按 §1 那条反例把「链上没有这一轴」印成 29 个假缺陷。
    cited_draft_fields = {f.name for f in dataclasses.fields(CW.CitedProseDraft)}
    check("material_dispositions" not in cited_draft_fields,
        "现状如实登记：`CitedProseDraft`（本链的草稿类型）**没有** Writer 侧处置轴——"
        "读回据 `writer_axis_present` 说一次，不逐份报假缺陷")
    check("material_dispositions" in {f.name for f in dataclasses.fields(NS.SectionDraft)},
        "而**正式**写作侧 `SectionDraft` 有这一轴（`pack_writer` 由 manifest × proposals 确定性"
        "派生，不由模型自报）：两处不许混读，本模块因此不把前者说成「本批已实现」")

    # ============== §6b 逐**数字表面**的最早丢失点台账（主语是数字，不是材料）
    details.append("## §6b 数字表面台账：三件事分开列，观察不到的四段不给档")

    def _num_ledger(*, material_text: str = "", draft_sentences: tuple = (),
                    records: tuple = (), admission: str = "admitted",
                    reason_code: str = "aspect_material_admitted"):
        pack = _FakePack(
            materials=(_FakeMaterial(material_id="mat-1"),),
            material_dispositions=(_FakeRmd(material_id="mat-1",
                                            admission_state=admission,
                                            reason_code=reason_code),))
        manifest = _FakeManifest(materials=(
            _FakeMember(citation_key="m01", material_id="mat-1",
                        reading_view=material_text),))
        draft = _FakeDraft(subsections=(
            _FakeDraftSubsection(subsection_id="co-h4", paragraphs=(
                _FakeParagraph(sentences=draft_sentences),)),))
        return CHAIN._numeric_surface_ledger(
            inputs=_inputs(pack), section_id=_SECTION, manifest=manifest, draft=draft,
            check_report=_FakeCheckReport(records=records))

    check(CHAIN._numeric_surface_ledger(
              inputs=_FakeInputs(authorities={_SECTION: _FakeAuthority(pack_set=None)}),
              section_id=_SECTION, manifest=_FakeManifest(), draft=_FakeDraft(),
              check_report=_FakeCheckReport()) is None,
          "没有 Pack 材料边界（财务那一支）⇒ 本条账**不适用**（`None`），不是全零")

    _text = "2025年公司实现销量541万，同比增长12.5%，毛利率为18 亿元。"
    _led = _num_ledger(
        material_text=_text,
        draft_sentences=(_FakeSentence(sentence_id="s-1", citations=("m01",),
                                       text="公司出货量为541万，比率为12.5%。"),),
        records=(_FakeCheckRecord(sentence_id="s-1", verdict="hard_error",
                                  check_kind="numeric_qualification",
                                  failure_reason="unsourced_qualified_number",
                                  surfaces=("12.5%",)),))
    _by_surface = {r["surface"]: r for r in _led["rows"]}
    check(_by_surface["541万"]["state"] == "written_numeric_axis_clean",
          f"写进正文、数字轴没拦它 ⇒ `written_numeric_axis_clean`（实测 "
          f"{_by_surface['541万']['state']}）")
    check(_by_surface["12.5%"]["state"] == "written_numeric_axis_hard_error"
          and _by_surface["12.5%"]["numeric_failure_reasons"] == ["unsourced_qualified_number"]
          and _by_surface["12.5%"]["numeric_failure_scope"] == "surface",
          "写进正文且数字轴判硬错 ⇒ 原因码**逐字**取自判据，不在这里重判")
    check(_by_surface["541万"]["numeric_failure_scope"] == ""
          and _by_surface["541万"]["numeric_failure_reasons"] == [],
          "**同句另一个数字不得被连坐**：判据点名的是 `12.5%`，`541万` 就不该跟着记硬错"
          "——读回自己造出来的假错，正是本批要分开的东西")
    _sentence_scoped = _num_ledger(
        material_text=_text,
        draft_sentences=(_FakeSentence(sentence_id="s-1", citations=("m01",),
                                       text="公司出货量为541万，比率为12.5%。"),),
        records=(_FakeCheckRecord(sentence_id="s-1", verdict="hard_error",
                                  check_kind="numeric_qualification",
                                  failure_reason="unsourced_qualified_number"),))
    _sc = {r["surface"]: r for r in _sentence_scoped["rows"]}
    check(_sc["541万"]["numeric_failure_scope"] == "sentence"
          and _sc["12.5%"]["numeric_failure_scope"] == "sentence",
          "判据**没给表面串**时按整句归属，并在行里标成 `sentence`："
          "「这句里有数字没来源」与「就是这个数字没来源」是两句不同的话")
    check(_by_surface["18 亿元"]["state"] == "in_pack_material_not_written"
          and _by_surface["18 亿元"]["in_pack"]
          and _by_surface["18 亿元"]["material_reasons"] == ["admitted／aspect_material_admitted"],
          "Pack 原文里有、正文没写 ⇒「已送达未写出」，并附**材料级**理由（不是数字级理由）")
    check(_by_surface["18 亿元"]["sentences"] == []
          and _by_surface["541万"]["pack_keys"] == ["m01"],
          "两个方向各自可查：没写出去的没有句子行；写出去的能回到它出现在哪份材料里")
    check("2025年" not in _by_surface,
          "普通数量（年份）没写进正文时不逐条列出——它们进合计，免得把要看的那几行淹掉")
    check(_led["aggregate_only"] == ["2025年"],
          f"合计里如实数得出来（实测 {_led['aggregate_only']}）")

    _draft_only = _num_ledger(
        material_text="本段落没有任何数字。",
        draft_sentences=(_FakeSentence(sentence_id="s-1", citations=("m01",),
                                       text="本期装机规模达到7 倍。"),))
    _row = next(r for r in _draft_only["rows"] if r["surface"] == "7 倍")
    check(_row["state"] == "written_numeric_axis_clean" and not _row["in_pack"],
          "正文写了、判据没拦、可 Pack 原文里查不到 ⇒ 状态与「在不在 Pack 原文里」"
          "**两根轴分开记**（`written_numeric_axis_clean` + `在 Pack 原文里 = 否`）——"
          "这一格才是「本链可见范围内没有它的出处」")
    check(_draft_only["written_not_in_pack"] == ["7 倍"],
          "最值得看的那一格**单独算出来**列在产物里，不让读者自己在两列之间对")
    check(_draft_only["counts"].get("in_pack_material_not_written", 0) == 0,
          "`在 Pack 原文里 = 否` 不得被记成「已送达未写出」——那是同一个词反着用")

    _denied = _num_ledger(material_text="毛利率为18 亿元。", admission="not_admitted",
                          reason_code="aspect_material_not_admitted")
    _row = next(r for r in _denied["rows"] if r["surface"] == "18 亿元")
    check(_row["state"] == "in_pack_material_not_written"
          and _row["material_reasons"] == ["not_admitted／aspect_material_not_admitted"],
          "材料未获准入时，里面的数字跟着记**同一档材料级理由**，并在读回里写明"
          "「这是材料的理由，不是这个数字各自的理由」")

    check(set(_led["counts"]) <= set(CHAIN.NUMERIC_SURFACE_STATES),
          "逐档计数只取封闭取值域里的档，不自造第五档")
    check([s[0] for s in _led["unobserved_segments"]]
          == [s[0] for s in CHAIN.NUMERIC_CHAIN_UNOBSERVED_SEGMENTS]
          and len(_led["unobserved_segments"]) == 4,
          "观察不到的四段**逐段**在产物里（原 PDF 格值 / 导航与工具结果 / 事实候选生成 / "
          "逐条资格拒绝原因）——不给档，也不假装看过")
    check(not any("资格被拒" in str(r) for r in _led["rows"])
          and not any("扫描排除" in str(r) for r in _led["rows"]),
          "台账**不**为任何一个数字断言「资格被拒 / 扫描排除」——本链产物里没有那两段的"
          "typed 记录，说了就是把观察不到写成被拒")
    _md = "\n".join(CHAIN._numeric_surface_ledger_readback(ledger=_led))
    check("被拒了" in _md and "不等于" in _md and "整句归属" in _md,
          "读回把四句读法写死：已送达未写出 ≠ 被拒；查不到 ≠ 来源里没有；数字轴未判硬错 "
          "≠ 已获格级数字权威；整句归属 ≠ 点名到那个数字")
    check("不适用" in "\n".join(CHAIN._numeric_surface_ledger_readback(ledger=None)),
          "账为 `None` 时读回印「不适用」，不印一张空表（空表会被读成「一个数字都没有」）")

    # ================================== §7 本模块的边界
    details.append("## §7 本模块证明的与不证明的")
    note("本模块只钉**判定与词表**：四态如何判、缺陷态如何认、两个粒度如何分开、字段名是否真的"
         "存在。它**不**证明：材料真的取到了、18 个栏目都有人写、正文达到人读内容门，"
         "或 M930-3 / TS5 / 任何正式阶段关闭——那些要真实 run 之后由人对着正文读。")
    note("夹具是替身，不是真实产物：`_FakePack` / `_FakeManifest` / `_FakeDraft` 只具备账真正"
         "会读的字段。真实链上的读数由离线双节 run 的读回给出。")
    check(True, "边界已声明（替身夹具 + 判定级结论，不冒充成稿质量验收）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

"""§三 E：写作相位的 current 链持久化（恰一次提交 → 读回重算 → 失败不留半事务）。

用法: python -m evals.test_demo_section_chain_persistence

本模块只断言**落库**这一件事，不重复门外语义（那是 `test_demo_writer_formal_chain` /
`test_demo_report_assembler` 的活），也不重复 v2 reader 的逐行篡改检测（那是
`test_demo_section_store_readback` 的活）。全部离线、确定性，且**只写临时库**：本文件把
`sections.store` 的库路径指向 `TemporaryDirectory` 内的文件，结束时还原，因此不碰
`data/sections.db`。

覆盖：

 1. 真链真库：`run_backbone_writer_phase(section_store=...)` 之后，临时库里能读到**完整**门后束
    （manifest / WMPD / subject / proposal → 三类决定 → FND / 定稿 Claim / final Narrative /
    Result），行数与**产出的对象**逐项相等（不是写死的期望值）；
 2. 恰一次提交：本次运行对 `commit_section_chain_v2` 的调用计数恰为 1 —— 提交与读回同属
    `commit_and_verify_section_chain_v2` 一个入口，调用方拿不到第二次提交的机会；
 3. 读回可复现：独立连接读回后重建出同一 Draft、同一 Claim 集、同一 final Narrative、同一
    Result 正文与指纹；把读回结果**再提交**走复用路径且行集不变；
 4. 未注入 store：产物如实记 `not_injected`（不得读成「已落库且无异常」）；
 5. 能力不符：注入的对象没有落库能力时 typed fail-closed，不得泄漏裸 `AttributeError`；
 6. 失败不留半事务：链在第 4 行插入时失败 ⇒ 各 family 0 行（事务整体回滚）；已存链被改坏后的
    复用路径拒绝 ⇒ 库里行数一条不增；
 7. §九 **正式入口**：`run_formal_m930_writer_phase` 缺 store / 读回未验证 / 产物自报
    `not_injected` 三种情形一律拒；真库下「恰一次提交 + 独立读回」同时成立。非正式入口
    （`run_backbone_writer_phase`）保留 `not_injected` 的无 store 模式，那是给纯单元测试用的。
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals import test_demo_backbone_writer_phase as WP  # noqa: E402
from evals import test_demo_report_assembler as FIXT  # noqa: E402
from sections import company_worker as CW  # noqa: E402
from sections import pack_writer as PW  # noqa: E402
from sections import presentation_profile as PP  # noqa: E402
from sections import store as ST  # noqa: E402
from sections import writing_spec as WS  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def expect_raises(msg: str, fn, exc_type) -> str:
    try:
        fn()
    except exc_type as exc:  # noqa: BLE001
        check(True, f"{msg}：{type(exc).__name__}")
        return str(exc)
    except Exception as exc:  # noqa: BLE001
        check(False, f"{msg}：期望 {exc_type.__name__}，实际 {type(exc).__name__}: {exc}")
        return ""
    check(False, f"{msg}：未 fail-closed")
    return ""


# ---------------------------------------------------------------------------
# 只读探针：直接查真库（不经 store 的读入口，因此「读回」与「查库」是两条独立证据）
# ---------------------------------------------------------------------------

_V2_TABLES = (*ST.V2_WRITER_SIDE_FAMILIES, *ST.V2_DECISION_FAMILIES,
              *ST.V2_POST_GATE_FAMILIES)


def _query(db: Path, sql: str, params: tuple = ()) -> list:
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def _count(db: Path, table: str, where: str = "", params: tuple = ()) -> int:
    clause = f" WHERE {where}" if where else ""
    return int(_query(db, f"SELECT COUNT(*) AS n FROM {table}{clause}", params)[0]["n"])


def _row_counts(db: Path) -> dict[str, int]:
    return {t: _count(db, t) for t in _V2_TABLES}


def _spec_and_profile():
    return (WS.load_writing_spec(FIXT.SPEC_PATH),
            PP.load_presentation_profile(FIXT.PROFILE_PATH))


def _section_input(*, task, authority, spec, profile):
    projection = PW.ContractProjection.create(
        spec, section_id="company", contract_version=FIXT.CONTRACT_VERSION,
        contract_fingerprint=FIXT.CONTRACT_FINGERPRINT)
    return CW.BackboneWriterSectionInput(
        task=task, authority=authority, projection=projection,
        writing_spec=spec, presentation_profile=profile,
        dependency_fingerprint=FIXT.DEPENDENCY_FINGERPRINT)


def _drive(*, task, authority, spec, profile, section_store) -> CW.BackboneWriterPhase:
    """唯一写作主链的一次调用（真门 + stub LLM；落库经注入的 section store）。"""
    return CW.run_backbone_writer_phase(
        (_section_input(task=task, authority=authority, spec=spec, profile=profile),),
        llm_client=FIXT._StubLlm(WP._presented_plan(
            PW.scan_topic_pack(authority, task), authority)),
        entailment_llm_client=FIXT._StubEntailmentClient(FIXT._ENTAILED),
        final_sentence_llm_client=FIXT._stub_final_sentence_client(),
        material_resolver=FIXT._payload_resolver(),
        section_store=section_store)


def _drive_formal(*, task, authority, spec, profile, section_store) -> CW.BackboneWriterPhase:
    """**正式**入口（§九）：同一个输入，但落库是强制的、且要求读回验证。"""
    return CW.run_formal_m930_writer_phase(
        (_section_input(task=task, authority=authority, spec=spec, profile=profile),),
        section_store=section_store,
        llm_client=FIXT._StubLlm(WP._presented_plan(
            PW.scan_topic_pack(authority, task), authority)),
        entailment_llm_client=FIXT._StubEntailmentClient(FIXT._ENTAILED),
        final_sentence_llm_client=FIXT._stub_final_sentence_client(),
        material_resolver=FIXT._payload_resolver())


# ---------------------------------------------------------------------------
# 1–3：真链 → 恰一次提交 → 读回可复现
# ---------------------------------------------------------------------------

def _check_real_chain_persistence(db: Path, spec, profile) -> None:
    task, authority, _scan = WP._company_fixture()
    real_commit = ST.commit_section_chain_v2
    commits: list[str] = []

    def _counting_commit(chain):
        commits.append(chain.draft.draft_id)
        return real_commit(chain)

    ST.commit_section_chain_v2 = _counting_commit
    try:
        phase = _drive(task=task, authority=authority, spec=spec, profile=profile,
                       section_store=ST)
    finally:
        ST.commit_section_chain_v2 = real_commit

    section = phase.sections[0]
    persistence = dict(section.persistence)
    check(persistence.get("state") == "v2_chain_committed_and_verified"
          and persistence.get("roundtrip") == "verified",
          f"落库状态必须是「已提交且读回重算通过」（得到 {persistence.get('state')!r}）")
    check(len(commits) == 1 and commits[0] == section.draft.draft_id,
          f"本节**恰好**提交一次（实际提交 {len(commits)} 次：{commits}）")
    check(_count(db, "current_section_draft_v2", "draft_id = ?", (section.draft.draft_id,)) == 1,
          "临时库里该 draft 的 v2 root 恰 1 行")

    # 行数与**产出对象**逐项相等（不写死期望值，也不把「有几行」当独立结论）。
    expected = {
        "current_section_material_manifest_v2": 1,
        "current_section_wmpd_v2": len(section.draft.material_disposition_ids),
        "current_section_subject_v2": len(section.draft.claim_candidate_ids)
        + len(section.draft.narrative_draft_unit_ids),
        "current_section_proposal_v2": len(section.draft.proposed_support_ids),
        "current_section_binding_decision_v2": len(section.aggregate_decisions),
        "current_section_entailment_decision_v2": len(section.entailment_decisions),
        "current_section_accepted_binding_v2": len(section.acceptance.accepted_bindings),
        "current_section_fnd_v2": len(section.dispositions),
        "current_section_claim_narrative_disposition_v2": len(
            section.claim_narrative_dispositions),
        "current_section_claim_v2": len(section.claims),
        "current_section_narrative_v2": 1,
        "current_section_unresolved_v2": len(section.draft.unresolved_ids),
        "current_section_result_v2": 1,
    }
    for family, want in sorted(expected.items()):
        got = _count(db, family, "draft_id = ?", (section.draft.draft_id,))
        check(got == want,
              f"{family} 行数必须等于产出对象计数 {want}（实际 {got}）")
    # 人读内容：本节真写出了 Claim 与段落（否则上面的「行数与对象相等」可能是 0 == 0）。
    check(expected["current_section_claim_v2"] > 0
          and expected["current_section_fnd_v2"] > 0
          and len(section.narrative.paragraphs) > 0,
          "本节确实定稿了 Claim / FND 去向 / 自然段落（不是空壳落库）")

    # 读回可复现：独立连接重建出同一 Draft / Claim / Narrative / Result。
    loaded = ST.load_current_section_chain_v2(section.draft.draft_id)
    check(loaded is not None, "读回不为空")
    check(loaded.draft.identity_body() == section.draft.identity_body(),
          "读回重建的 Draft 与写入侧逐字段相同（含 exact material manifest）")
    check(tuple(c.claim_id for c in loaded.claims)
          == tuple(c.claim_id for c in section.claims),
          "读回的 Claim 集与写入侧逐个相同")
    # `writer_attempt` / `natural_prose_draft` **不进 `identity_body`**（所以上面那条逐字段相等
    # 看不见它们），但它们进 `draft_revision`。少了它们，读回会算出一个「首轮 / 没有草稿层」
    # 的修订，与成员行声明的修订不符 —— 因此必须单独核对，且先钉死前提：本节**确实**带草稿层
    # （否则这一条是「0 == 0」的空断言，绿得毫无信息）。
    check(len(section.draft.natural_prose_draft) > 0,
          "前提：本节确实带门前草稿层（否则下面的草稿层核对是空断言）")
    check(loaded.draft.draft_revision == section.draft.draft_revision
          and loaded.draft.writer_attempt == section.draft.writer_attempt,
          "读回重算的 draft_revision / writer_attempt 与写入侧相同")
    check(tuple(u.prose_unit_id for u in loaded.draft.natural_prose_draft)
          == tuple(u.prose_unit_id for u in section.draft.natural_prose_draft),
          "读回的草稿层与写入侧逐单元相同（草稿层不进 identity_body，但进修订）")
    check(loaded.narrative is not None
          and loaded.narrative.identity_body() == section.narrative.identity_body(),
          "读回的 final Narrative 与写入侧逐字段相同（段落/表格都在 root 载荷里）")
    check(loaded.result is not None and loaded.result.markdown == section.result.markdown
          and loaded.result.markdown_fingerprint == section.result.markdown_fingerprint
          and loaded.result.section_version == section.result.section_version,
          "读回的 SectionResult 正文/指纹/版本与写入侧相同（组装器复算得同一份产物）")
    check(loaded.to_chain().member_rows() == _chain_of(section).member_rows(),
          "读回结果再提交得到的行集与首次提交逐行相同（往返稳定）")
    again = ST.commit_and_verify_section_chain_v2(_chain_of(section))
    check(again["commit"]["reused"] is True and again["roundtrip"] == "verified",
          "同一链再提交走复用路径（reused=True）且读回仍然通过")
    check(_count(db, "current_section_draft_v2", "draft_id = ?",
                 (section.draft.draft_id,)) == 1,
          "复用不追加第二份 root（append-only 下也不重复写）")

    # 半条链：门后对象单独在场而没有 Result 封口，必须拒（否则「写一半」与完整链在库里无从
    # 区分）。反例用**真实产出**的对象，因此拒的不是「手搓对象不合法」。
    expect_raises("缺 Result 却有 Claim/Narrative/FND：整体缺席才合法，半条链必须拒",
                  lambda: dataclasses.replace(_chain_of(section), result=None),
                  ST.SectionStorageConflictError)
    expect_raises("缺 Result 但只带 final Narrative：半条链同样必须拒",
                  lambda: dataclasses.replace(_chain_of(section), result=None, claims=()),
                  ST.SectionStorageConflictError)


def _chain_of(section) -> ST.SectionChainV2:
    """产出对象 → 链容器（与 `_persist_section_chain` 同一构造；不新增第二套口径）。

    **门后 family 逐类都要搬**：漏一类（本节曾漏 `final_sentence_decisions`）会让「往返稳定」
    变成假红——被漏掉的那一行只在提交侧存在，而读回侧根本没有它可比。
    """
    return ST.SectionChainV2(
        draft=section.draft, aggregate_decisions=tuple(section.aggregate_decisions),
        entailment_decisions=tuple(section.entailment_decisions),
        accepted_bindings=tuple(section.acceptance.accepted_bindings),
        fact_narrative_dispositions=tuple(section.dispositions),
        claim_narrative_dispositions=tuple(section.claim_narrative_dispositions),
        claims=tuple(section.claims), narrative=section.narrative,
        final_sentence_decisions=tuple(section.final_sentence_decisions),
        result=section.result)


# ---------------------------------------------------------------------------
# 4–5：未注入 / 能力不符
# ---------------------------------------------------------------------------

def _check_injection_states(spec, profile) -> None:
    task, authority, _scan = WP._company_fixture()
    not_injected = _drive(task=task, authority=authority, spec=spec, profile=profile,
                          section_store=None).sections[0]
    check(dict(not_injected.persistence).get("state") == "not_injected",
          "未注入 section store 时产物必须如实记 not_injected（不得记成成功）")
    check(not hasattr(not_injected, "persisted_at") and "draft_id" not in not_injected.persistence,
          "not_injected 状态不得夹带任何落库身份（它不是「落库成功但没有细节」）")

    text = expect_raises("注入的对象没有落库能力时必须 typed fail-closed",
                         lambda: _drive(task=task, authority=authority, spec=spec,
                                        profile=profile, section_store=object()),
                         CW.BackboneWriterPhaseError)
    check("section store" in text and "能力" in text,
          f"拒绝理由必须点名缺失的能力（得到 {text[:100]!r}）")


# ---------------------------------------------------------------------------
# 6：失败不留半事务
# ---------------------------------------------------------------------------

def _check_atomic_rollback(db: Path, spec, profile) -> None:
    """在第 4 行插入时注入失败：各 family 必须 0 行（事务整体回滚，而不是留下前 3 行）。"""
    task, authority, _scan = WP._company_fixture()
    real_insert = ST._v2_insert
    calls: list[str] = []

    def _explode(conn, family, **kwargs):
        calls.append(family)
        if len(calls) == 4:
            raise RuntimeError("injected: 第 4 行插入失败")
        return real_insert(conn, family, **kwargs)

    ST._v2_insert = _explode
    try:
        expect_raises("提交中途失败必须抛出（不得吞掉）",
                      lambda: _drive(task=task, authority=authority, spec=spec,
                                     profile=profile, section_store=ST),
                      RuntimeError)
    finally:
        ST._v2_insert = real_insert
    check(len(calls) >= 4, f"注入点确实在事务内被触发（实际插入尝试 {len(calls)} 次）")
    counts = _row_counts(db)
    check(all(n == 0 for n in counts.values()),
          f"中途失败后各 family 必须 0 行（实际 {counts}）：任一步失败整事务 rollback")
    check(_count(db, "current_section_draft_v2") == 0,
          "中途失败后连 root 行也不得留下")


def _check_corrupt_reuse_leaves_no_rows(db: Path, spec, profile) -> None:
    """已存链被改坏后的复用路径：必须在写任何一行之前拒绝，且库内行数一条不增。"""
    task, authority, _scan = WP._company_fixture()
    section = _drive(task=task, authority=authority, spec=spec, profile=profile,
                     section_store=ST).sections[0]
    draft_id = section.draft.draft_id
    before = _row_counts(db)
    check(before["current_section_claim_v2"] > 0 and before["current_section_result_v2"] == 1,
          "前提：这份临时库里确实有一条完整的已存链")

    # 改一个**仍然合法**的字段（候选正文）并连带重算指纹：content-addressing 会先拦住，
    # 但复用路径必须**写任何一行之前**就拒绝，所以「库里行数不变」是可观测判据。
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TRIGGER trg_current_section_subject_v2_no_update")
        conn.commit()
    finally:
        conn.close()
    row = _query(db, "SELECT payload_json FROM current_section_subject_v2 "
                     "WHERE draft_id = ? AND subject_kind = ? LIMIT 1",
                 (draft_id, "claim_candidate"))[0]
    payload = json.loads(row["payload_json"])
    payload["claim_text"] = "公司2025年营业收入为9999.99亿元。"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("UPDATE current_section_subject_v2 SET payload_json = ?, "
                     "content_fingerprint = ? WHERE draft_id = ? AND subject_kind = ?",
                     (json.dumps(payload, ensure_ascii=False, sort_keys=True),
                      ST._v2_fingerprint(payload), draft_id, "claim_candidate"))
        conn.commit()
    finally:
        conn.close()

    text = expect_raises("已存链被改坏：复用路径必须 fail-closed",
                         lambda: ST.commit_and_verify_section_chain_v2(_chain_of(section)),
                         ST.SectionStorageCorruptionError)
    check(bool(text), "拒绝理由非空（不得用空诊断冒充实检）")
    check(_row_counts(db) == before,
          "拒绝发生在写入之前：库里行数一条不增（不是「先写后回滚」也算数）")


def _check_prose_layer_loss_rejected(db: Path, spec, profile) -> None:
    """载荷少了**草稿层**必须被拒，而不是被读成「本节没有草稿层」。

    `natural_prose_draft` / `writer_attempt` 不进 `identity_body`（所以「载荷少了一项」不会
    改变 `draft_id`，内容寻址这一层看不见它），但它们**进 `draft_revision`**。读回必须从载荷
    里拿到它们才能复算出同一个修订；少一项就会算出一个「首轮 / 无草稿层」的修订。反例把指纹
    一并重算，因此被拒的理由不可能是「指纹对不上」这条廉价判据。
    """
    task, authority, _scan = WP._company_fixture()
    section = _drive(task=task, authority=authority, spec=spec, profile=profile,
                     section_store=ST).sections[0]
    draft_id = section.draft.draft_id
    check(len(section.draft.natural_prose_draft) > 0,
          "前提：被改的这份载荷确实带草稿层")
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("DROP TRIGGER trg_current_section_draft_v2_no_update")
        stored = conn.execute("SELECT payload_json FROM current_section_draft_v2 "
                              "WHERE draft_id = ?", (draft_id,)).fetchone()[0]
        payload = json.loads(stored)
        check("natural_prose_draft" in payload,
              "前提：草稿层确实随 root 载荷留存（不留存就没有可丢的东西）")
        payload.pop("natural_prose_draft", None)
        conn.execute("UPDATE current_section_draft_v2 SET payload_json = ?, "
                     "content_fingerprint = ? WHERE draft_id = ?",
                     (json.dumps(payload, ensure_ascii=False, sort_keys=True),
                      ST._v2_fingerprint(payload), draft_id))
        conn.commit()
    finally:
        conn.close()

    text = expect_raises("载荷丢了草稿层：读回必须 fail-closed（不得读成「本节没有草稿层」）",
                         lambda: ST.load_current_section_chain_v2(draft_id),
                         ST.SectionStorageCorruptionError)
    check("draft_revision" in text,
          f"拒绝理由必须指名修订不符（得到 {text[:140]!r}）")


# ---------------------------------------------------------------------------
# 7：§九 正式写作入口（强制落库、读回验证）—— 与「非正式入口允许 not_injected」分工
# ---------------------------------------------------------------------------

def _check_formal_entry(db: Path, spec, profile) -> None:
    """正式入口把「未落库」「读回未验证」变成不可能，而不是变成一条可选提示。"""
    task, authority, _scan = WP._company_fixture()

    text = expect_raises(
        "正式入口缺 Section Store 必须拒（not_injected 不是正式成功）",
        lambda: _drive_formal(task=task, authority=authority, spec=spec, profile=profile,
                              section_store=None),
        CW.BackboneWriterPhaseError)
    check("Section Store" in text and "not_injected" in text,
          f"拒绝理由必须点名 Section Store 与 not_injected（得到 {text[:120]!r}）")

    class _FakeSectionStore:
        """只在**形状**上合格的替身：用来单独检验正式入口对落库状态的复核（不写任何库）。"""

        SectionChainV2 = ST.SectionChainV2

        def __init__(self, roundtrip: str) -> None:
            self.roundtrip = roundtrip
            self.calls: list[str] = []

        def commit_and_verify_section_chain_v2(self, chain):
            self.calls.append(str(chain.draft.draft_id))
            return {"commit": {"reused": False}, "roundtrip": self.roundtrip,
                    "family_rows": {}}

    unverified = _FakeSectionStore("unverified")
    text = expect_raises(
        "落库返回「读回未验证」时正式入口必须拒（不得把提交回报读成成功）",
        lambda: _drive_formal(task=task, authority=authority, spec=spec, profile=profile,
                              section_store=unverified),
        CW.BackboneWriterPhaseError)
    check("读回" in text and "unverified" in text,
          f"拒绝理由必须点名读回结果（得到 {text[:120]!r}）")

    verified = _FakeSectionStore("verified")
    phase = _drive_formal(task=task, authority=authority, spec=spec, profile=profile,
                          section_store=verified)
    check(len(verified.calls) == 1
          and dict(phase.sections[0].persistence).get("roundtrip") == "verified",
          f"读回 verified 时正式入口走通，且提交恰一次（实际 {len(verified.calls)} 次）")

    real_persist = CW._persist_section_chain
    CW._persist_section_chain = lambda **kwargs: {"state": "not_injected"}
    try:
        text = expect_raises(
            "产物自报 not_injected 时正式入口必须拒（正式入口下未落库不是成功态）",
            lambda: _drive_formal(task=task, authority=authority, spec=spec, profile=profile,
                                  section_store=verified),
            CW.BackboneWriterPhaseError)
    finally:
        CW._persist_section_chain = real_persist
    check("not_injected" in text, f"拒绝理由必须点名 not_injected（得到 {text[:120]!r}）")

    # 真库：正式入口下「恰一次提交 + 独立读回」同时成立，且库里确实有这条链。
    real_commit = ST.commit_section_chain_v2
    commits: list[str] = []

    def _counting_commit(chain):
        commits.append(str(chain.draft.draft_id))
        return real_commit(chain)

    ST.commit_section_chain_v2 = _counting_commit
    try:
        phase = _drive_formal(task=task, authority=authority, spec=spec, profile=profile,
                              section_store=ST)
    finally:
        ST.commit_section_chain_v2 = real_commit
    formal_section = phase.sections[0]
    check(len(commits) == 1 and commits[0] == str(formal_section.draft.draft_id),
          f"正式入口下本节**恰好**提交一次（实际 {len(commits)} 次）")
    check(dict(formal_section.persistence).get("state") == "v2_chain_committed_and_verified",
          "正式入口的产物必须记「已提交且读回验证」")
    check(_count(db, "current_section_draft_v2", "draft_id = ?",
                 (formal_section.draft.draft_id,)) == 1,
          "正式入口提交的链在临时库里恰 1 行 root（读回是独立连接做的）")


def main() -> dict:
    spec, profile = _spec_and_profile()
    saved = ST._db_path
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "sections_chain_persistence.db"
        ST.init_db(db)
        try:
            _check_real_chain_persistence(db, spec, profile)
            _check_injection_states(spec, profile)
        finally:
            ST._db_path = saved
        # 正式入口的真库用例必须跑在**空库**上：同一条 fixture 在已存链的库上会走复用路径，
        # 而两次运行的门后行内容（created_at 等）本就不同 —— 那是复用路径的判据，不是本节的。
        db_formal = Path(td) / "sections_chain_formal.db"
        ST.init_db(db_formal)
        try:
            _check_formal_entry(db_formal, spec, profile)
        finally:
            ST._db_path = saved
        # 原子性反例必须跑在**空库**上（「0 行」才有意义）。
        db2 = Path(td) / "sections_chain_rollback.db"
        ST.init_db(db2)
        try:
            _check_atomic_rollback(db2, spec, profile)
        finally:
            ST._db_path = saved
        db3 = Path(td) / "sections_chain_corrupt.db"
        ST.init_db(db3)
        try:
            _check_corrupt_reuse_leaves_no_rows(db3, spec, profile)
        finally:
            ST._db_path = saved
        # 草稿层丢失的反例同样必须跑在**空库**上：判据是「这条链被拒」，而不是「库里没有别的链」。
        db4 = Path(td) / "sections_chain_prose_loss.db"
        ST.init_db(db4)
        try:
            _check_prose_layer_loss_rejected(db4, spec, profile)
        finally:
            ST._db_path = saved
    return _results


if __name__ == "__main__":
    import json
    print(json.dumps(main(), ensure_ascii=False, indent=2))
    sys.exit(1 if _results["failed"] else 0)

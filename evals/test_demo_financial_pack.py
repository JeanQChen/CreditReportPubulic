"""Eval: M930-2 财务权威投影（`sections.financial_pack_artifact`）与财务 backbone phase。

用法: python -m evals.test_demo_financial_pack

覆盖：
- 投影是 `FinancialFactPack` 的**确定性函数**：同输入同 `artifact_id`，改选材规则版本即改身份；
- 金额是 Decimal **规范字符串**且精确往返（Float 化 / 科学计数法一律拒），缺失值保持 None
  （不用 0 冒充），citation 原样保留且 `citation.snapshot_id` 必须等于本快照身份；
- 选中与排除**同时**登记：`selected_fact_ids` 与 `facts` 逐位一致、excluded 只登记 FactPack
  自身的 gaps/diagnostic_gaps 并带 required 标志、同一 fact 不得既选中又排除；
- 权威不得自报：非 `SnapshotAuthority` → 拒；快照非权威必须**如实记录**（notes 写明 + 重算
  `authoritative()==False`），不静默当权威；
- replay 两模式语义：无源 → `offline_replay` 且明说**未**复核权威；有源 → `live` 且必须真复核；
  快照自构建以来漂移 → `passed()==False` 并逐字段记录漂移；两种模式的非法组合构造即拒；
- 篡改（值 / artifact_id / 选中清单脱节 / 指纹）一律拒；
- 本模块**不得**引用文档结构层 / TableObject（AST 负向断言，§5.10）；
- 财务 backbone phase（§十 真正只读）：phase **直接对真实** `data/financial_v2.db` 调用（测试
  不自造副本替它挡枪）；会建表/迁移的旧构建逻辑只在 phase 内部临时副本上跑，真实库调用前后
  sha256/size/mtime_ns/WAL/SHM 逐项不变，且 phase 自记的前后观测与测试独立观测一致。产物
  身份与直接构建逐字段一致（副本路径/临时目录名绝不进内容身份）；section/policy/空
  projection 在碰库前 fail-closed；被观测库中途被改动（在测试副本上复现）→ 只读前提破裂即
  `FinancialReadOnlyViolation`；复核时快照漂移 → `FinancialPhaseBlocked` **带着** artifact
  与附注缺口抛出（缺口不被吞），此时只读观测仍逐项不变。
- 只读保证在**连接层**成立（P1-A）：权威复核用 `mode=ro` URI + `PRAGMA query_only=ON` 独立
  打开真实库，**不**调用 `fstore._get_conn()`、**不**改 `fstore._db_path`、**不**创建库/父
  目录/WAL/SHM（把 `_get_conn` 换成"调用即报错"后仍复核成功，且重算结果与既有只读口径逐
  字段一致）；`readonly_financial_copy` 在成功/抛错/复核失败三条路径都恢复进入前的进程级
  绑定；stale / report_blocked / quarantined / 非 current 四类健康态在副本上仍各自 fail-closed。
- 财务附注输入状态（§十一）**独立**于财务主权威：本批未实现抽取 → task-bound
  `EvidenceNoteGap`（`note_extraction_not_implemented`，`searched_need_ids` 为空——不谎称查过），
  绝不并入 artifact、绝不用空集或普通财务缺口冒充；两种附注状态**恰有其一**，构造期即拒
  违规组合，附注事实还必须落在**已准入** span 上（同一 Evidence 的别的 span 也不放行）。

公司/期间无关：公司与期间由**库里 current 快照自身**推出，不写证券代码或固定页码特判。
库缺失时如实 skip；不调 LLM、不联网、不写任何真实数据库（也不向真实库路径写临时副本）。
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import structured_provenance as SP
from planning import schema as PS
from sections import financial_pack_artifact as FPA
from sections import financial_worker as FW

FIN_DB = Path("data/financial_v2.db")

GOOD = SP.SnapshotAuthority(exists=True, is_current=True, validity="valid",
                            report_blocked=False, quarantined=False)
STALE = SP.SnapshotAuthority(exists=True, is_current=False, validity="stale",
                             report_blocked=True, quarantined=False)


class _FakeSource:
    """独立来源替身；构建阶段与复核阶段可返回**不同**权威事实（复现「构建后漂移」）。"""

    def __init__(self, query_authority: SP.SnapshotAuthority,
                 build_authority: SP.SnapshotAuthority | None = None) -> None:
        self._query = query_authority
        self._build = build_authority or query_authority

    def authority_for(self, target):
        return self._build

    def query(self, artifact):
        return self._query


class _MutatingSource(_FakeSource):
    """复核阶段**改动被观测库**的来源替身：用来证明只读保证真的会 fail-closed。

    只在测试自己造的副本上使用：真实 `data/financial_v2.db` 永不被写。
    """

    def __init__(self, query_authority: SP.SnapshotAuthority, *, db: Path) -> None:
        super().__init__(query_authority)
        self._db = Path(db)

    def query(self, artifact):
        with self._db.open("ab") as fh:
            fh.write(b"-- mutated by eval\n")
        return super().query(artifact)


def _current_dims(db: Path) -> dict | None:
    """只读地从**库里自己的 current_snapshot 指针**读出一组维度（不猜公司/期间）。"""
    uri = f"file:{db.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        row = conn.execute(
            "SELECT company_id, scope, currency, as_of_date, purpose, snapshot_id "
            "FROM current_snapshot ORDER BY company_id, as_of_date, scope LIMIT 1").fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if row is None:
        return None
    return {"company_id": row[0], "scope": row[1], "currency": row[2],
            "as_of_date": row[3], "purpose": row[4], "snapshot_id": row[5]}


def _fin_task(snapshot_id: str) -> PS.SectionTask:
    topics = tuple(sorted(set(FW._FORMULA_TO_TOPIC.values())))
    return PS.SectionTask(
        task_id="task-fin-eval", plan_id="plan-eval", section_id="financial",
        title="财务分析", purpose="eval", research_policy="workflow",
        topic_ids=topics, questions=(), output_requirements=(),
        evaluation_rule_ids=(), allowed_capabilities=("financial",),
        blocking_rules=(), dependency_versions={"financial_snapshot_id": snapshot_id})


def _build(pack, task, *, projection_id: str = "proj-eval",
           rule_version: str = FW.FINANCIAL_FACT_SELECTION_RULE_VERSION,
           authority: SP.SnapshotAuthority = GOOD) -> FPA.FinancialPackArtifact:
    return FPA.build_financial_pack_artifact(
        pack, task_id=task.task_id, projection_id=projection_id,
        contract_version="v2", contract_fingerprint="f" * 64,
        fact_selection_rule_version=rule_version, authority=authority)


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
                details.append(f"FAIL {msg}：原因不符（{str(e)[:140]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:140]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    if not FIN_DB.exists():
        skipped += 1
        details.append(f"SKIP 缺少 {FIN_DB}（财务投影需要既有权威库）")
        return {"passed": passed, "failed": failed, "skipped": skipped,
                "details": details}
    dims = _current_dims(FIN_DB)
    if dims is None:
        skipped += 1
        details.append("SKIP 财务库里没有 current_snapshot 指针（不猜公司/期间）")
        return {"passed": passed, "failed": failed, "skipped": skipped,
                "details": details}

    from financial_v2 import snapshots as fsnapshots
    from financial_v2 import store as fstore

    # 只读绑定：不 init、不迁移、不写（真实库只允许 SELECT）。
    fstore._db_path = FIN_DB.resolve()
    current = fsnapshots.current_snapshot(
        dims["company_id"], dims["scope"], dims["currency"],
        dims["as_of_date"], dims["purpose"])
    if current is None or current.snapshot_id != dims["snapshot_id"]:
        skipped += 1
        details.append("SKIP current_snapshot 指针与库内容不一致（不猜）")
        return {"passed": passed, "failed": failed, "skipped": skipped,
                "details": details}

    with tempfile.TemporaryDirectory() as td:
        copy = Path(td) / "fin_copy.db"
        # 真实库的**副本**：`build_fact_pack_for_task` 会执行 DDL（init_db），因此只能在副本上跑。
        shutil.copy2(FIN_DB, copy)
        task = _fin_task(dims["snapshot_id"])
        pack = FW.build_fact_pack_for_task(
            task, company_id=dims["company_id"],
            snapshot_id=dims["snapshot_id"], fin_db=str(copy),
            scope=dims["scope"], currency=dims["currency"],
            purpose=dims["purpose"], as_of_date=dims["as_of_date"])

        authority = SP.query_snapshot_authority(dims["snapshot_id"],
                                                current.snapshot_id)
        check(authority.authoritative(),
              "前置条件：库里 current 快照确实是权威快照（非权威会另走如实记录分支）")
        artifact = _build(pack, task, authority=authority)

        # ============ 1. 身份 / 确定性 ====================================
        check(artifact.schema_version == FPA.FINANCIAL_PACK_ARTIFACT_SCHEMA_VERSION
              and artifact.projection_version == FPA.FINANCIAL_PACK_PROJECTION_VERSION
              and artifact.producer_kind == FPA.FINANCIAL_ARTIFACT_PRODUCER_KIND,
              "artifact 版本与生产者身份来自本模块具名常量（不用 Harness Pack 冒充）")
        rebuilt = _build(pack, task, authority=authority)
        check(rebuilt.artifact_id == artifact.artifact_id
              and rebuilt.content_fingerprint == artifact.content_fingerprint,
              "同 pack + 同投影输入 → 同一 artifact 身份（确定性）")
        other_rule = _build(pack, task, rule_version="other-rule-v9", authority=authority)
        check(other_rule.artifact_id != artifact.artifact_id,
              "改选材规则版本即改 artifact 身份（版本进入内容规范形）")
        other_proj = _build(pack, task, projection_id="proj-other", authority=authority)
        check(other_proj.artifact_id != artifact.artifact_id,
              "改 projection_id 即改 artifact 身份")
        check(artifact.snapshot.snapshot_id == pack.snapshot_id
              and (artifact.snapshot.company_id, artifact.snapshot.as_of_date,
                   artifact.snapshot.scope, artifact.snapshot.currency,
                   artifact.snapshot.purpose)
              == (dims["company_id"], dims["as_of_date"], dims["scope"],
                  dims["currency"], dims["purpose"]),
              "快照身份与库里 current 指针逐字段一致（不由调用方另填）")
        check(artifact.snapshot.authoritative() is True
              and artifact.snapshot.validity == "valid",
              "权威事实按字段重算（不采信自报）")

        # ============ 2. 金额：Decimal 规范字符串 =========================
        mismatched: list[str] = []
        floatish: list[str] = []
        for proj, raw in zip(artifact.facts, pack.facts):
            raw_value = getattr(raw, "value", None)
            if (proj.value_text is None) != (raw_value is None):
                mismatched.append(proj.fact_id)
                continue
            if raw_value is None:
                continue
            if proj.value_text != str(raw_value):
                mismatched.append(proj.fact_id)
            if proj.value_decimal() != raw_value or not isinstance(raw_value, Decimal):
                floatish.append(proj.fact_id)
            if proj.value_text and ("e" in proj.value_text.lower()
                                    or proj.value_text.strip() != proj.value_text):
                floatish.append(proj.fact_id)
        check(not mismatched,
              f"每条金额都是源 Decimal 的规范字符串（None 保持 None，{mismatched[:3]}）")
        check(not floatish,
              f"金额不走 float / 科学计数法，且可精确往返为 Decimal（{floatish[:3]}）")
        bad_cite = [f.fact_id for f in artifact.facts if not (
            f.citation.get("ref_type") == "structured"
            and f.citation.get("snapshot_id") == artifact.snapshot.snapshot_id)]
        check(artifact.facts and not bad_cite,
              f"每条事实保留结构化 citation 且指向本快照（{len(bad_cite)} 条不符："
              f"{bad_cite[:3]}）")

        # ============ 3. 选中 / 排除 同时登记 =============================
        check(tuple(f.fact_id for f in artifact.facts) == tuple(artifact.selected_fact_ids),
              "selected_fact_ids 与 facts 逐位一致（声明不得与内容脱节）")
        expected_excluded = (len(pack.gaps), len(pack.diagnostic_gaps))
        actual_excluded = (sum(1 for e in artifact.excluded if e.required),
                           sum(1 for e in artifact.excluded if not e.required))
        check(actual_excluded == expected_excluded,
              f"excluded 分别登记 FactPack 的 gaps/diagnostic_gaps（{actual_excluded}）")
        check(set(f.fact_id for f in artifact.facts)
              .isdisjoint(e.fact_id for e in artifact.excluded),
              "同一条 fact 不得既选中又排除")
        check(artifact.gaps == tuple(pack.gaps)
              and artifact.diagnostic_gaps == tuple(pack.diagnostic_gaps),
              "FactPack 的 required/diagnostic 缺口原样带入，不做二次筛选")
        by_id = {e.fact_id: e for e in artifact.excluded}
        check(all(by_id[g["fact_id"]].reason_code == g.get("reason_code")
                  and by_id[g["fact_id"]].kind == g.get("kind", "")
                  and by_id[g["fact_id"]].period == g.get("period", "")
                  for g in tuple(pack.gaps) + tuple(pack.diagnostic_gaps)),
              "excluded 登记保留原缺口的原因码/类别/期间（排除也有据可查）")
        check(artifact.periods == tuple(pack.periods)
              and artifact.statements_available == tuple(pack.statements_available)
              and artifact.period_note == dict(pack.period_note),
              "期间、可用报表与期间说明原样投影（不重新推断期间）")
        check(any("excluded 仅登记" in n for n in artifact.projection_notes),
              "上游更早过滤未留 per-fact 记录这一点被显式声明（不编造原因）")

        # ============ 4. replay：两种模式 =================================
        offline = FPA.verify_financial_pack_artifact(artifact)
        check(offline.mode == "offline_replay"
              and offline.snapshot_authority_reverified is False
              and offline.snapshot_authoritative is None,
              "无独立来源 → offline_replay，且不声称已复核快照权威")
        check(any("未" in n and "复核" in n for n in offline.notes),
              "offline 报告明文声明未复核权威（不得据此声称财务权威仍有效）")
        check(offline.content_fingerprint_ok and offline.facts_unchanged
              and offline.identity_consistent,
              "offline 仍校验冻结内容自洽（指纹/选中清单/身份）")

        live = FPA.verify_financial_pack_artifact(
            artifact, authority_source=FPA.FinancialDbAuthoritySource(str(FIN_DB)))
        check(live.mode == "live" and live.snapshot_authority_reverified is True,
              "给了独立来源 → live 且真的重新查询过快照权威")
        check(live.passed() and live.snapshot_authoritative is True
              and live.identity_consistent and not live.notes,
              "live 复核通过：快照仍权威、身份无漂移")

        drifted = FPA.verify_financial_pack_artifact(
            artifact, authority_source=_FakeSource(STALE))
        check(drifted.mode == "live" and drifted.passed() is False
              and drifted.snapshot_authoritative is False
              and drifted.identity_consistent is False,
              "构建后快照漂移 → live 复核不通过（不得放行）")
        check(any("漂移" in n for n in drifted.notes),
              "漂移必须逐字段写明（不是只给一个 False）")
        expect_error(lambda: FPA.verify_financial_pack_artifact(
            artifact, authority_source=_FakeSource({"authoritative": True})),
            FPA.FinancialArtifactError, "来源返回自报字典必须拒",
            needle="SnapshotAuthority")

        expect_error(lambda: FPA.ArtifactReplayReport(
            mode="offline_replay", artifact_id="x", content_fingerprint_ok=True,
            snapshot_authority_reverified=True, snapshot_authoritative=True,
            identity_consistent=True, facts_unchanged=True),
            FPA.FinancialArtifactError, "offline 不得声称已复核权威")
        expect_error(lambda: FPA.ArtifactReplayReport(
            mode="live", artifact_id="x", content_fingerprint_ok=True,
            snapshot_authority_reverified=False, snapshot_authoritative=None,
            identity_consistent=True, facts_unchanged=True),
            FPA.FinancialArtifactError, "live 必须真的复核过权威")
        expect_error(lambda: FPA.ArtifactReplayReport(
            mode="guessed", artifact_id="x", content_fingerprint_ok=True,
            snapshot_authority_reverified=False, snapshot_authoritative=None,
            identity_consistent=True, facts_unchanged=True),
            FPA.FinancialArtifactError, "未知 replay 模式必须拒")

        # ============ 5. 非权威快照必须如实记录 ===========================
        soft = _build(pack, task, authority=STALE)
        check(soft.snapshot.authoritative() is False
              and any("非权威" in n for n in soft.projection_notes),
              "非权威快照被如实记录并写明（不静默当权威）")
        check(FPA.verify_financial_pack_artifact(soft).passed() is True
              and FPA.verify_financial_pack_artifact(
                  soft, authority_source=_FakeSource(STALE)).passed() is False,
              "非权威快照：offline 只证内容自洽，live 必须不通过")
        expect_error(lambda: _build(pack, task,
                                    authority={"authoritative": True}),
                     FPA.FinancialArtifactError, "自报权威（字典）必须拒",
                     needle="SnapshotAuthority")
        expect_error(lambda: _build(pack, task, rule_version=""),
                     FPA.FinancialArtifactError, "空选材规则版本必须拒")
        expect_error(lambda: _build(pack, task, projection_id=""),
                     FPA.FinancialArtifactError, "空 projection_id 必须拒")

        # ============ 6. 篡改一律拒 ======================================
        # 谁来拒都算（构造期不变量或 `verify()` 重算），但必须**有人**拒，且不得静默通过。
        check(bool(artifact.facts), "前置条件：真实库投影出的事实非空（否则本节无意义）")
        if artifact.facts:
            alt_text = "1" + (artifact.facts[0].value_text or "0")
            tampered = dataclasses.replace(
                artifact,
                facts=(dataclasses.replace(artifact.facts[0], value_text=alt_text),)
                + artifact.facts[1:])
            expect_error(tampered.verify, FPA.FinancialArtifactError,
                         "改金额必须被指纹发现")
            check(dataclasses.replace(
                tampered, content_fingerprint="", artifact_id=""
            ).compute_content_fingerprint() != artifact.content_fingerprint,
                "金额进入内容指纹（不重算指纹也改不动身份）")
            bad_citation = dataclasses.replace(
                artifact.facts[0],
                citation=dict(artifact.facts[0].citation, snapshot_id="snap-other"))
            expect_error(lambda: dataclasses.replace(
                artifact, facts=(bad_citation,) + artifact.facts[1:]
            ).__post_init__(), FPA.FinancialArtifactError,
                "citation 指向别的快照必须拒")
        expect_error(lambda: dataclasses.replace(
            artifact, selected_fact_ids=()).__post_init__(),
            FPA.FinancialArtifactError, "选中清单与内容脱节必须拒")
        expect_error(lambda: dataclasses.replace(
            artifact, fact_selection_rule_version="").__post_init__(),
            FPA.FinancialArtifactError, "空选材规则版本必须拒")
        expect_error(lambda: dataclasses.replace(
            artifact, artifact_id="ffpa_" + "0" * 24).verify(),
            FPA.FinancialArtifactError, "改 artifact_id 必须被发现")
        expect_error(lambda: dataclasses.replace(
            artifact, content_fingerprint="0" * 64).verify(),
            FPA.FinancialArtifactError, "改内容指纹必须被发现")
        expect_error(lambda: dataclasses.replace(
            artifact, producer_kind="topic_harness").__post_init__(),
            FPA.FinancialArtifactError, "别的生产者身份必须拒")

        # 往返：from_dict 后身份逐字段一致
        back = FPA.FinancialPackArtifact.from_dict(artifact.to_dict())
        check(back.artifact_id == artifact.artifact_id
              and back.content_fingerprint == artifact.content_fingerprint
              and back.to_dict() == artifact.to_dict(),
              "to_dict/from_dict 往返后内容与身份逐字段一致")

        # ============ 7. 负向：不得从 TableObject 造财务事实 ===============
        FPA.assert_no_table_conversion()
        passed += 1  # AST 断言通过即计一次

        # ============ 8. 财务 backbone phase（§十 真正只读）===============
        # 关键：phase 直接对**真实**库调用——测试不替它造副本，只读保证必须由 phase 自己成立。
        def _phase(**over):
            phase_task = over.pop("task", task)
            kwargs = dict(company_id=dims["company_id"],
                          projection_id="proj-eval", contract_version="v2",
                          contract_fingerprint="f" * 64, fin_db=str(FIN_DB),
                          scope=dims["scope"], currency=dims["currency"],
                          purpose=dims["purpose"], as_of_date=dims["as_of_date"],
                          snapshot_id=dims["snapshot_id"])
            kwargs.update(over)
            return FW.run_backbone_financial_phase(phase_task, **kwargs)

        real_before = FW.financial_db_state(FIN_DB)
        phase = _phase()
        real_after = FW.financial_db_state(FIN_DB)
        check(FW.financial_db_drift(real_before, real_after) == (),
              "phase 全程对真实财务库只读：sha256/size/mtime_ns/WAL/SHM 逐项不变")
        check(phase.phase_version == FW.BACKBONE_FINANCIAL_PHASE_VERSION,
              "phase 结果带具名 phase 版本")
        check(phase.artifact.artifact_id == artifact.artifact_id
              and phase.artifact.content_fingerprint == artifact.content_fingerprint,
              "phase 产物与直接构建同一身份（副本路径/临时目录名不进内容身份）")
        check(phase.artifact.fact_selection_rule_version
              == FW.FINANCIAL_FACT_SELECTION_RULE_VERSION,
              "phase 使用财务 Worker 自己具名的选材规则版本")
        check(phase.artifact.snapshot.snapshot_id == dims["snapshot_id"],
              "phase 绑定库里 current 快照")
        check(phase.db_state_before == real_before and phase.db_state_after == real_after
              and FW.financial_db_drift(phase.db_state_before, phase.db_state_after) == (),
              "phase 自记的只读观测与测试独立观测一致，且自身零漂移")
        check(phase.readonly_copy_policy_version == FW.FINANCIAL_READONLY_COPY_POLICY_VERSION,
              "只读副本策略版本具名（可审计，不靠调用方口头保证）")
        check(phase.replay.mode == "live" and phase.replay.passed()
              and phase.replay.snapshot_authoritative is True,
              "phase 内建独立 live 复核通过，不返回『大概没问题』的 artifact")

        # ---- §十一：附注输入状态**独立**、**恰有其一**、且不并入 artifact ----
        gap = phase.evidence_note_gap
        check(phase.evidence_note_facts is None
              and isinstance(gap, FPA.EvidenceNoteGap)
              and not isinstance(gap, FPA.ValidatedEvidenceNoteFactSet),
              "本批取不到附注 → EvidenceNoteGap（不是空附注集，也不是普通财务缺口）")
        check(gap.schema_version == FPA.EVIDENCE_NOTE_SCHEMA_VERSION
              and gap.task_id == task.task_id and gap.company_id == dims["company_id"]
              and gap.contract_version == "v2" and gap.contract_fingerprint == "f" * 64,
              "附注缺口 task-bound：schema/任务/公司/契约逐字段绑定")
        check(gap.reason_codes == ("note_extraction_not_implemented",)
              and gap.searched_need_ids == ()
              and gap.searched_scope == tuple(task.allowed_capabilities),
              "缺口只声明『本轮未实现抽取』，不谎称查过任何 need")
        check(bool(gap.detail) and not any(
            w in gap.detail for w in FPA.EVIDENCE_NOTE_GAP_FORBIDDEN_WORDING),
              "缺口措辞不得替公司声明『未披露』（只说本轮已纳入范围内未取得）")
        check("evidence_note_gap" not in phase.artifact.to_dict()
              and "evidence_note_facts" not in phase.artifact.to_dict()
              and isinstance(phase.artifact, FPA.FinancialPackArtifact),
              "附注状态绝不并入 FinancialPackArtifact（来源权威不同不得混）")
        check(phase.to_dict()["evidence_note_gap"]["reason_codes"]
              == ["note_extraction_not_implemented"]
              and phase.to_dict()["evidence_note_facts"] is None
              and phase.to_dict()["db_readonly"]["drift"] == [],
              "序列化后附注缺口独立可审，且如实声明零漂移")

        # 附注状态的不变量：构造期 fail-closed，不靠调用方自觉
        expect_error(lambda: dataclasses.replace(gap, reason_codes=()),
                     FPA.FinancialArtifactError, "空原因码的附注缺口必须拒")
        expect_error(lambda: dataclasses.replace(
            gap, reason_codes=("company_did_not_disclose",)),
            FPA.FinancialArtifactError, "未登记原因码必须拒")
        expect_error(lambda: dataclasses.replace(
            gap, reason_codes=("note_span_not_evidence_backed",)),
            FPA.FinancialArtifactError,
            "非『未实现』原因必须绑定真实 searched_need_ids（不得无据宣称『未取得』）")
        expect_error(lambda: dataclasses.replace(
            gap, reason_codes=("note_span_not_evidence_backed",),
            searched_need_ids=("need-note-1",), searched_scope=()),
            FPA.FinancialArtifactError, "非『未实现』原因缺 searched_scope 必须拒")
        expect_error(lambda: dataclasses.replace(gap, detail="公司未披露财务附注"),
                     FPA.FinancialArtifactError, "替公司声明『未披露』的措辞必须拒")
        expect_error(lambda: dataclasses.replace(gap, schema_version="evn-2"),
                     FPA.FinancialArtifactError, "附注缺口 schema 版本必须具名")
        expect_error(lambda: dataclasses.replace(gap, task_id=""),
                     FPA.FinancialArtifactError, "附注缺口必须 task-bound（task_id 非空）")

        # `ValidatedEvidenceNoteFactSet`：另一支附注状态，形状与 span 定位同样 fail-closed
        note_fact = FPA.EvidenceNoteFact(
            fact_id="note-1", label="附注", value_text="1", display="1",
            evidence_id="ev-note", evidence_char_range=(2, 5), span_id="span-note")
        note_set = FPA.ValidatedEvidenceNoteFactSet(
            schema_version=FPA.EVIDENCE_NOTE_SCHEMA_VERSION, task_id=task.task_id,
            company_id=dims["company_id"], report_as_of=pack.as_of_date,
            contract_version="v2", contract_fingerprint="f" * 64, facts=(note_fact,),
            searched_scope=tuple(task.allowed_capabilities), searched_need_ids=("need-note-1",),
            validation_rule_version=FW.EVIDENCE_NOTE_VALIDATION_RULE_VERSION)
        check(FPA.validate_evidence_note_fact_set(note_set, (note_fact.span_ref(),)) == ()
              and FPA.validate_evidence_note_fact_set(
                  note_set, (("ev-note", 2, 6),)) != ()
              and FPA.validate_evidence_note_fact_set(
                  note_set, (("ev-other", 2, 5),)) != (),
              "附注事实必须落在已准入 span 上：同一 Evidence 的别的区间、别的 Evidence 都不放行")
        expect_error(lambda: dataclasses.replace(note_set, facts=()),
                     FPA.FinancialArtifactError,
                     "空附注集必须拒（空集会被读成『查过且确无附注』）")
        expect_error(lambda: dataclasses.replace(note_set, contract_fingerprint=""),
                     FPA.FinancialArtifactError, "附注集必须契约绑定")
        expect_error(lambda: dataclasses.replace(
            note_set, facts=(note_fact, note_fact)),
            FPA.FinancialArtifactError, "附注集含重复 fact_id 必须拒")
        expect_error(lambda: FPA.ValidatedEvidenceNoteFactSet(
            schema_version="evn-old", task_id=task.task_id, company_id=dims["company_id"],
            report_as_of=pack.as_of_date, contract_version="v2", contract_fingerprint="f" * 64,
            facts=(note_fact,), searched_scope=(), searched_need_ids=("need-note-1",),
            validation_rule_version=FW.EVIDENCE_NOTE_VALIDATION_RULE_VERSION),
            FPA.FinancialArtifactError, "附注集 schema 版本必须具名")
        expect_error(lambda: FPA.EvidenceNoteFact(
            fact_id="note-2", label="", value_text="1", display="1", evidence_id="ev-note",
            evidence_char_range=(5, 5), span_id="span-note"),
            FPA.FinancialArtifactError, "退化（start==end）的附注 span 区间必须拒")
        expect_error(lambda: FPA.EvidenceNoteFact(
            fact_id="note-3", label="", value_text="约 1 亿元", display="约 1 亿元",
            evidence_id="ev-note", evidence_char_range=(2, 5), span_id="span-note"),
            FPA.FinancialArtifactError, "非 Decimal 规范字符串的附注金额必须拒")

        # 结果级不变量：恰有其一 + 构造期自检真实库零漂移
        expect_error(lambda: dataclasses.replace(phase, evidence_note_gap=None),
                     FW.FinancialWorkerError,
                     "两种附注状态同时缺失必须拒（不得既不查也不记）")
        expect_error(lambda: dataclasses.replace(phase, evidence_note_facts=note_set),
                     FW.FinancialWorkerError,
                     "两种附注状态同时存在必须拒（附注与财务主权威不得混）")
        drifted_state = dict(phase.db_state_after)
        drifted_main = dict(drifted_state["main"])
        drifted_main["sha256"] = "0" * 64
        drifted_state["main"] = drifted_main
        expect_error(lambda: dataclasses.replace(phase, db_state_after=drifted_state),
                     FW.FinancialReadOnlyViolation,
                     "结果构造期自检真实库漂移即拒（不允许返回『看起来没问题』的结果）")

        # section / policy / 空 projection 必须在碰库前 fail-closed
        for label, over, needle in (
            ("phase_wrong_section",
             {"task": dataclasses.replace(task, section_id="company")}, "'financial'"),
            ("phase_wrong_policy",
             {"task": dataclasses.replace(task, research_policy="harness")},
             "'workflow'"),
            ("phase_empty_projection", {"projection_id": ""}, "projection_id"),
        ):
            expect_error(lambda over=over: _phase(**over), FW.FinancialWorkerError,
                         f"{label} 必须在碰库前 fail-closed", needle=needle)
        check(FW.financial_db_drift(FW.financial_db_state(FIN_DB), real_before) == (),
              "正向 phase 与全部 fail-closed 分支都没有改动真实库")

        # ---- P1-A：真实库只读保证必须在**连接层**成立 ------------------------
        # 不是"事后比对 hash 没变"，也不是"把模块级 _db_path 指过去再普通 connect"：
        # 复核路径不得依赖 `fstore._get_conn()`，也不得改动进程级 `fstore._db_path`。
        p1a_before = FW.financial_db_state(FIN_DB)
        saved_get_conn = fstore._get_conn
        saved_binding = fstore._db_path

        def _boom(*_a, **_k):
            raise AssertionError(
                "FinancialDbAuthoritySource 不得调用/依赖 fstore._get_conn()")

        ro_source = FPA.FinancialDbAuthoritySource(str(FIN_DB))
        try:
            fstore._get_conn = _boom
            via_sql = ro_source.authority_for(pack)
            reverified = FPA.verify_financial_pack_artifact(
                artifact, authority_source=ro_source)
        finally:
            fstore._get_conn = saved_get_conn
        check(via_sql.authoritative() and reverified.mode == "live"
              and reverified.passed() is True,
              "禁用 fstore._get_conn 后仍能从真实库独立只读复核成功（连接级只读，不靠全局绑定）")
        check(via_sql == authority,
              "只读 SQL 独立重算的权威五项与既有只读口径逐字段一致")
        check(fstore._db_path == saved_binding,
              "只读权威查询不改动进程级 fstore._db_path")

        # 不存在的库路径：只读打开必须 fail-closed，且不创建文件或父目录
        missing_dir = Path(td) / "no_such_dir"
        missing_db = missing_dir / "financial_v2.db"
        expect_error(lambda: FPA.FinancialDbAuthoritySource(
            str(missing_db)).authority_for(pack),
            FPA.FinancialArtifactError, "不存在的库必须 fail-closed（不建库）")
        check(not missing_db.exists() and not missing_dir.exists(),
              "只读打开不存在的库既不创建库文件也不创建父目录")

        # 三条路径都必须恢复进入前的进程级绑定（builder 成功 / builder 抛错 / 复核失败）
        binding_ok = True
        binding_notes: list[str] = []
        for label, over in (
            ("builder 成功", {}),
            ("builder 抛错", {"snapshot_id": "snap-does-not-exist"}),
            ("复核失败", {"authority_source": _FakeSource(STALE, build_authority=authority)}),
        ):
            expected_binding = FIN_DB.resolve()
            fstore._db_path = expected_binding
            try:
                _phase(**over)
            except FW.FinancialWorkerError:
                pass
            if fstore._db_path != expected_binding:
                binding_ok = False
                binding_notes.append(f"{label}→{fstore._db_path}")
        fstore._db_path = saved_binding
        check(binding_ok,
              "readonly_financial_copy 在成功/抛错/复核失败三条路径都恢复进程级绑定"
              f"（异常项：{binding_notes}）")

        # 真实库 main/WAL/SHM 的存在性与 sha256/size/mtime_ns 前后逐项不变
        p1a_after = FW.financial_db_state(FIN_DB)
        existence_ok = all(
            (p1a_before.get(key) is None) == (not Path(str(FIN_DB) + suffix).exists())
            for suffix, key in (("", "main"), ("-wal", "wal"), ("-shm", "shm")))
        check(existence_ok and FW.financial_db_drift(p1a_before, p1a_after) == (),
              "真实库 main/WAL/SHM 的存在性与 sha256/size/mtime_ns 逐项前后不变")

        # stale / report_blocked / quarantined / 不再是 current 各自仍 fail-closed（在副本上复现）
        def _copy_db(name: str) -> Path:
            dest = Path(td) / name
            shutil.copy2(FIN_DB, dest)
            for suffix in ("-wal", "-shm"):
                side = Path(str(FIN_DB) + suffix)
                if side.exists():
                    shutil.copy2(side, Path(str(dest) + suffix))
            return dest

        def _mutate(db: Path, sql: str, params: tuple) -> None:
            """在**副本**上构造健康态反例。

            副本先解除本库自身的历史事实不可变触发器（`trg_<table>_no_update` 等）：真实库
            从不这样改，这里只是要造出「库里确实是这样」的健康态，用来证明只读权威查询会
            如实重算并 fail-closed。
            """
            conn = sqlite3.connect(str(db))
            try:
                for (name,) in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
                    conn.execute(f'DROP TRIGGER "{name}"')
                conn.execute(sql, params)
                conn.commit()
            finally:
                conn.close()

        stale_db = _copy_db("fin_stale.db")
        _mutate(stale_db,
                "INSERT INTO snapshot_validity (event_id, snapshot_id, status, "
                "invalidated_by, invalidated_reason, event_at) VALUES (?,?,?,?,?,?)",
                ("ev-eval-stale", dims["snapshot_id"], "stale", "eval", "eval",
                 "2099-01-01T00:00:00Z"))
        blocked_db = _copy_db("fin_blocked.db")
        _mutate(blocked_db,
                "UPDATE financial_snapshot SET report_blocked=1 WHERE snapshot_id=?",
                (dims["snapshot_id"],))
        quarantined_db = _copy_db("fin_quarantined.db")
        _mutate(quarantined_db,
                "INSERT INTO quarantine (quarantine_id, object_type, object_id, reason, "
                "quarantined_at) VALUES (?,?,?,?,?)",
                ("q-eval", "financial_snapshot", dims["snapshot_id"], "eval",
                 "2026-01-01T00:00:00Z"))
        notcurrent_db = _copy_db("fin_notcurrent.db")
        _mutate(notcurrent_db,
                "DELETE FROM current_snapshot WHERE company_id=? AND scope=? AND currency=? "
                "AND as_of_date=? AND purpose=?",
                (dims["company_id"], dims["scope"], dims["currency"],
                 dims["as_of_date"], dims["purpose"]))

        health = {
            "stale": (stale_db, lambda a: a.validity == "stale" and a.is_current is False),
            "report_blocked": (blocked_db, lambda a: a.report_blocked is True),
            "quarantined": (quarantined_db, lambda a: a.quarantined is True),
            "not_current": (notcurrent_db, lambda a: a.is_current is False),
        }
        for label, (db, predicate) in health.items():
            got = FPA.FinancialDbAuthoritySource(str(db)).authority_for(pack)
            check(predicate(got) and got.authoritative() is False,
                  f"{label}：只读 SQL 独立重算后仍 fail-closed（exists={got.exists}, "
                  f"is_current={got.is_current}, validity={got.validity!r}, "
                  f"report_blocked={got.report_blocked}, quarantined={got.quarantined}）")
        check(FW.financial_db_drift(FW.financial_db_state(FIN_DB), p1a_before) == (),
              "副本上的健康态反例没有触碰真实库")

        # 只读保证的 **fail-closed** 证明：被观测库中途被改动 ⇒ 必须拒（在测试副本上复现）
        expect_error(lambda: _phase(fin_db=str(copy), authority_source=_MutatingSource(GOOD, db=copy)),
                     FW.FinancialReadOnlyViolation,
                     "真实库中途被改动 → 只读前提破裂必须 fail-closed", needle="漂移")

        # 复核时漂移 → 必须带 artifact 与附注缺口一起抛出（缺口不被吞）
        try:
            _phase(authority_source=_FakeSource(STALE, build_authority=authority))
        except FW.FinancialPhaseBlocked as e:
            passed += 1
            check(e.artifact.artifact_id == phase.artifact.artifact_id
                  and e.report.mode == "live" and e.report.passed() is False,
                  "phase 漂移时抛 FinancialPhaseBlocked 且带着 artifact（缺口可见）")
            check(isinstance(e.note_gap, FPA.EvidenceNoteGap)
                  and FW.financial_db_drift(e.db_state_before, e.db_state_after) == ()
                  and e.to_dict()["db_readonly"]["drift"] == [],
                  "block 同时带出附注缺口与只读观测（不因 block 吞缺口或放宽只读）")
        else:
            failed += 1
            details.append("FAIL 复核漂移时 phase 未 block")
        check(FW.financial_db_drift(FW.financial_db_state(FIN_DB), real_before) == (),
              "block 分支同样对真实库只读（无漂移）")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

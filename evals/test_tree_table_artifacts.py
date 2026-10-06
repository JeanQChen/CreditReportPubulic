# -*- coding: utf-8 -*-
"""TS5 §19.12.7-6 / -7：验收产物与人工 review 的**机器可判**部分。

全部离线、**不写仓库内任何文件**：所有产物级反例都在 `tempfile` 临时目录里合成，
跑完即删。唯一真实读数来自 `evals/fixtures/tree_structure/ts5_trust_roots_v2.json`
（`ttr-2` 信任锚，只读；`ttr-1` 的旧文件保留为只读历史，走显式历史入口）与
`evaluation/` 下的 schema / runner 模块。

覆盖：

- **§19.12.7-6 产物与索引**：`tar-1` / `ttr-2` / `trm-2` / `tai-1` / `trv-1` /
  `tra-1` 六条轴的字面量与 `EVALUATION_VERSION_AXES` 一致；`tai-1` 索引在**真实成员
  集合**上逐文件重建、核验通过；缺文件 / 多文件 / 改字节 / 乱序 / 重复 / 版本漂移
  / `ok` 自报 全部被拒；`build_machine_index` 在磁盘集合与成员集合不一致时 fail-closed；
  每个可索引成员都有登记 role，且索引内部成员、人工模板与 seal-only 产物**不**进索引。
- **§19.14 `trm-2` 拒发契约**（A8）：两种拒发原因各自成篇（结构缺口 / 纯守恒不合格）、
  `refusal_blocking_kinds` 非空当且仅当原因含 `blocking_structure_gap`、`document_blocked`
  是 capability 轴而与 `blocking_gap_count` 那条结构轴互不冒充、合法拒发同样落盘全部
  验收产物，以及 `trm-1` 载荷走 current reader fail-closed / 走显式历史入口可读回。
- **§19.12.7-7 人工 review**：`trv-1` 的 10 项固定 check ID、两个固定角色、`(role ×
  check_id)` 完全笛卡尔积与固定顺序；`decision` **只能派生**（机器门 false → 需修改、
  任一项 fail → 拒绝、任一项 not_reviewed → 需修改、全 pass → 批准），自报与派生不一致
  即拒；`manual_review.md` 只是人读投影，同一 review JSON 投影逐字节确定，且显式声明
  "不构成授权"；`tra-1` 身份只绑定已封存字节与版本（不含时间戳），任一 sha256 / 版本 /
  结论位被改即拒；重复 seal 与封存后字节变化都被拒。

`self_check()` 是本模块对 **evaluation schema ↔ 生产 `table_schema`** 的独立对照：
evaluation 侧刻意**不** import 生产包，因此两处的封闭词表必须由本模块断言一致。
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import fields as _fields
from dataclasses import replace as _replace
from pathlib import Path
from types import SimpleNamespace

from document_structure import table_schema as TS
from document_structure import versions as V
from document_structure.canonical import SchemaValidationError, canonical_json
from evidence import store as _estore
from evaluation import run_tree_table_acceptance as RR
from evaluation import tree_table_acceptance_schema as SCH
from evals.test_tree_structure_schema import _make_table_v4  # noqa: E402  合成夹具（复用）

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def raises(fn, exc, substr, msg):
    try:
        fn()
    except exc as e:
        if substr in str(e):
            _results["passed"] += 1
            _results["details"].append("PASS " + msg)
            return True
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 异常信息不含 {substr!r}：{str(e)!r}")
        return False
    except Exception as e:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 抛出 {type(e).__name__} 而非 {exc.__name__}：{e}")
        return False
    _results["failed"] += 1
    _results["details"].append(f"FAIL {msg} —— 未抛出 {exc.__name__}")
    return False


# ---------------------------------------------------------------------------
# 0. 测试自带真值表
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[1]
_TRUST_ROOT_FILE = (_REPO / "evals" / "fixtures" / "tree_structure"
                    / "ts5_trust_roots_v2.json")

#: §19.14.1 / §19.14.2 的六条版本轴：名字 → 期望字面量。**测试自带**，不从被测对象反读。
_EXPECTED_AXES = {
    "acceptance_runner": "tar-1",
    "trust_root_schema": "ttr-2",
    "run_manifest_schema": "trm-2",
    "artifact_index_schema": "tai-1",
    "review_schema": "trv-1",
    "review_attestation_schema": "tra-1",
}

#: §19.14.2 的 10 项人工复核 check ID（固定顺序），测试自带副本。
_EXPECTED_CHECK_IDS = (
    "no_false_positive_table",
    "table_parts_and_boundaries_correct",
    "merged_multiline_cells_traceable",
    "surrounding_text_relations_correct",
    "continuation_identity_correct",
    "internal_cell_headings_retained",
    "nonfinancial_tables_use_generic_path",
    "financial_authority_separated",
    "explicit_gaps_are_honest",
    "final_conservation_zero",
)
_EXPECTED_ROLES = ("user", "codex")

#: `tra-1` 的结论位（全部为 bool，全 True 才可能 approve）。
_ATTESTATION_CONCLUSIONS = (
    "trust_root_rechecked", "review_decision_schema_ok",
    "artifact_index_recomputed_ok", "final_verifier_ok",
    "database_immutability_ok", "machine_gates_passed",
)

_SHA_A = "a" * 64
_SHA_B = "b" * 64


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_EVIDENCE_DB = _REPO / "data" / "evidence.db"
_FIN_DB = _REPO / "data" / "financial_v2.db"


def _file_identity(path: Path) -> dict | None:
    if not path.exists():
        return None
    st = path.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _store_identity(path: Path) -> dict:
    out = {"db": _file_identity(path)}
    for suffix in ("-wal", "-shm"):
        out[suffix] = _file_identity(Path(str(path) + suffix))
    return out


def _identity_bundle() -> dict:
    return {"evidence": _store_identity(_EVIDENCE_DB),
            "financial": _store_identity(_FIN_DB)}


def _order() -> tuple:
    """真实信任锚的固定文档序（只读信任锚，不触发任何绑定 / 构建）。"""
    return tuple(RR.load_trust_root()["document_order"])


def _seed_members(root: Path, order) -> list:
    """在临时目录里合成 `tai-1` 的**可索引成员全集**（内容自成一体，只求可核验）。"""
    members = list(SCH.indexable_members(order))
    for rel in members:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{rel}\n", encoding="utf-8")
    return members


def _build_index(root: Path, order) -> dict:
    return RR.build_machine_index(
        run_id="tree_table_ts5_synthetic", run_dir_relpath="tmp/synthetic",
        document_order=order, results_dir=root)


# ---------------------------------------------------------------------------
# A1. 版本轴、自检与两处封闭词表
# ---------------------------------------------------------------------------

def self_check() -> dict:
    """evaluation schema 与**生产** `table_schema` 的独立对照（docstring 承诺）。"""
    problems: list = []
    if dict(SCH.EVALUATION_VERSION_AXES) != dict(_EXPECTED_AXES):
        problems.append(f"六条版本轴与测试自带字面量不一致："
                        f"{dict(SCH.EVALUATION_VERSION_AXES)}")
    if tuple(SCH.REQUIRED_REVIEW_CHECK_IDS) != _EXPECTED_CHECK_IDS:
        problems.append("REQUIRED_REVIEW_CHECK_IDS 与固定的 10 项不一致")
    if tuple(SCH.REVIEWER_ROLES) != _EXPECTED_ROLES:
        problems.append("REVIEWER_ROLES 必须恰为 ('user', 'codex')")
    if tuple(SCH.TABLE_GAP_KINDS) != tuple(TS.TABLE_GAP_KINDS):
        problems.append("evaluation 侧的 TABLE_GAP_KINDS 与生产 `table_schema` 漂移："
                        f"{tuple(SCH.TABLE_GAP_KINDS)} vs {tuple(TS.TABLE_GAP_KINDS)}")
    if tuple(SCH.BLOCKING_GAP_KINDS) != tuple(TS.GAP_BLOCKING_KINDS):
        problems.append("evaluation 侧的 BLOCKING_GAP_KINDS 与生产漂移："
                        f"{tuple(SCH.BLOCKING_GAP_KINDS)} vs "
                        f"{tuple(TS.GAP_BLOCKING_KINDS)}")
    if tuple(SCH.VERIFIER_VERDICTS) != ("issued", "refused"):
        problems.append("VERIFIER_VERDICTS 必须恰为 ('issued', 'refused')")
    return {"problems": problems}


def _a1_axes_and_self_check() -> None:
    check(not self_check()["problems"],
          f"evaluation ↔ 生产 词表 / 版本轴对照零问题（{self_check()['problems']}）")
    check(SCH.RUN_DIR_PREFIX == "tree_table_ts5_",
          f"run 目录前缀为 create-only 的 {SCH.RUN_DIR_PREFIX!r}")
    check(not SCH.self_check()["problems"],
          f"evaluation schema 自检零问题（{SCH.self_check()['problems'][:2]}）")
    runner_check = RR.self_check()
    check(not runner_check["problems"],
          f"runner 自检零问题（{runner_check['problems'][:2]}）")
    check(runner_check["mode"] == "pinned_threshold_enabled",
          "runner 自检声明的模式是 pinned + threshold_enabled")
    code = RR.code_fingerprint()
    check(not code["missing"] and code["file_count"] == len(RR.CODE_FINGERPRINT_FILES),
          f"代码指纹的 {len(RR.CODE_FINGERPRINT_FILES)} 个文件全部存在、"
          f"逐文件可读（缺 {code['missing']}）")
    check(tuple(RR.EXCLUDED_FROM_MACHINE_INDEX) == tuple(SCH.NOT_SCANNED_BY_INDEX),
          "runner 不扫描的集合恰为 `tai-1` 的 not_scanned_by_index")
    check(len(RR.REVIEW_QUESTIONS) == len(_EXPECTED_CHECK_IDS),
          f"runner 的 10 个复核问题与 10 项 check ID 等长"
          f"（{len(RR.REVIEW_QUESTIONS)} vs {len(_EXPECTED_CHECK_IDS)}）")
    check(_sha256_file(_TRUST_ROOT_FILE) == RR.TRUST_ROOT_FILE_SHA256,
          "信任锚文件的磁盘 sha256 等于 runner 内的字面量（根锚未被改写）")
    check(all(getattr(SCH, name) == value for name, value in (
        ("TS5_ACCEPTANCE_RUNNER_VERSION", _EXPECTED_AXES["acceptance_runner"]),
        ("TS5_TRUST_ROOT_SCHEMA_VERSION", _EXPECTED_AXES["trust_root_schema"]),
        ("TS5_RUN_MANIFEST_SCHEMA_VERSION", _EXPECTED_AXES["run_manifest_schema"]),
        ("TS5_ARTIFACT_INDEX_SCHEMA_VERSION",
         _EXPECTED_AXES["artifact_index_schema"]),
        ("TS5_REVIEW_SCHEMA_VERSION", _EXPECTED_AXES["review_schema"]),
        ("TS5_REVIEW_ATTESTATION_SCHEMA_VERSION",
         _EXPECTED_AXES["review_attestation_schema"]))),
          "六条版本轴的**常量名 ↔ 字面量**逐条相符（轴表不是自报的）")
    check(RR.STAGE == "TS4-B" and RR.STAGE_TOKEN == "threshold_enabled",
          f"runner 声明的阶段与 token 为 {RR.STAGE!r} / {RR.STAGE_TOKEN!r}")


# ---------------------------------------------------------------------------
# A2. `tai-1`：索引重建、核验与全部反例
# ---------------------------------------------------------------------------

def _a2_machine_index() -> None:
    order = _order()
    with tempfile.TemporaryDirectory(prefix="ts5_artifacts_") as tmp:
        root = Path(tmp)
        members = _seed_members(root, order)
        expected_members = list(SCH.indexable_members(order))
        machine_members = list(SCH.expected_machine_members(order))
        check(members == expected_members and len(members) > 0 and
              set(expected_members) < set(machine_members),
              f"临时目录按 `tai-1` **可索引**成员集合铺满（{len(members)} 个文件；"
              f"机器成员全集 {len(machine_members)}）")

        # 每个可索引成员都必须有登记 role，否则 runner 自己就 fail-closed。
        unregistered: list = []
        for rel in members:
            try:
                RR._member_role(rel)
            except RR.StepFailure as e:
                unregistered.append((rel, e.field))
        check(not unregistered,
              f"每个可索引成员都有登记 role（未登记 {unregistered[:3]}）")

        index = _build_index(root, order)
        SCH.validate_artifact_index(index, document_order=order)
        check(len(index["files"]) == len(expected_members),
              f"索引逐文件登记全部 {len(expected_members)} 个可索引成员")
        check({row["path"] for row in index["files"]} == set(expected_members) and
              list(index["members_expected"]) == expected_members,
              "索引登记集合与可索引成员集合逐项相等（索引内部成员不在其中）")
        check([row["path"] for row in index["files"]] ==
              sorted(row["path"] for row in index["files"]),
              "索引行按 path 升序固定排列（顺序也是契约）")
        check(all(row["role"] for row in index["files"]),
              "每一行的 role 都非空（role 承载 schema / 算法版本，不能靠文件名猜）")
        verify = RR.verify_machine_index(root, index, order)
        check(verify["ok"] is True and not verify["missing"] and
              not verify["extra"] and not verify["hash_mismatch"],
              f"独立重算核验通过（scan={verify['scan_file_count']} / "
              f"index={verify['index_file_count']}）")
        SCH.validate_artifact_index_check(verify, document_order=order)
        check(canonical_json(RR.verify_machine_index(root, index, order)) ==
              canonical_json(verify),
              "核验结果可重复（同一磁盘状态重算逐字节相同）")

        # —— 索引自身的反例 ---------------------------------------------
        rows = list(index["files"])
        raises(lambda: SCH.validate_artifact_index(
            {**index, "files": rows[1:]}, document_order=order),
            SCH.AcceptanceSchemaError, "成员集合",
            "索引少登记一个成员即被拒（缺文件不得靠自报掩盖）")
        raises(lambda: SCH.validate_artifact_index(
            {**index, "files": rows + [{"path": "zzz_extra.json", "sha256": _SHA_A,
                                        "size": 1, "role": "x"}]},
            document_order=order),
            SCH.AcceptanceSchemaError, "成员集合",
            "索引多登记一个成员即被拒（多文件同样是漂移）")
        raises(lambda: SCH.validate_artifact_index(
            {**index, "files": list(reversed(rows))}, document_order=order),
            SCH.AcceptanceSchemaError, "升序",
            f"索引 files 乱序即被拒（{len(rows)} 行必须按 path 升序）")
        raises(lambda: SCH.validate_artifact_index(
            {**index, "files": rows[:1] + [rows[0]] + rows[1:]}, document_order=order),
            SCH.AcceptanceSchemaError, "重复",
            "索引重复登记同一 path 即被拒（重复行仍保持升序，因此只能被重复判定拦下）")
        raises(lambda: SCH.validate_artifact_index(
            {**index, "files": [{**rows[0], "sha256": "short"}] + rows[1:]},
            document_order=order),
            SCH.AcceptanceSchemaError, "sha256",
            "索引里的 sha256 形状非法即被拒")
        raises(lambda: SCH.validate_artifact_index(
            {**index, "artifact_index_schema_version": "tai-2"},
            document_order=order),
            SCH.AcceptanceSchemaError, "tai-1",
            "索引版本漂移到 `tai-2` 即被拒（版本是封闭集合）")
        raises(lambda: SCH.validate_artifact_index(
            {**index, "schema_type": "TS4ArtifactIndex"}, document_order=order),
            SCH.AcceptanceSchemaError, "schema_type",
            "索引 schema_type 不符即被拒")
        raises(lambda: SCH.validate_artifact_index(
            {**index, "index_internal_members": []}, document_order=order),
            SCH.AcceptanceSchemaError, "index_internal_members",
            "索引内部成员表被改即被拒（索引不可能含自身 sha256）")
        raises(lambda: SCH.validate_artifact_index(
            {**index, "members_expected": expected_members[:-1]},
            document_order=order),
            SCH.AcceptanceSchemaError, "members_expected",
            "members_expected 与实际成员集合不一致即被拒")

        # —— 磁盘 tamper：核验器必须逐类报出 ---------------------------
        victim_rel = sorted(row["path"] for row in index["files"])[0]
        victim = root / victim_rel
        original = victim.read_text(encoding="utf-8")
        try:
            victim.write_text(original + "tampered\n", encoding="utf-8")
            tampered = RR.verify_machine_index(root, index, order)
            check(tampered["ok"] is False and len(tampered["hash_mismatch"]) == 1 and
                  tampered["hash_mismatch"][0]["path"] == victim_rel and
                  tampered["hash_mismatch"][0]["actual_sha256"] ==
                  _sha256_file(victim) and
                  not tampered["missing"] and not tampered["extra"],
                  f"改一个字节后核验失败并逐文件报出 hash_mismatch（{victim_rel}）")
            check(_build_index(root, order)["files"] != index["files"],
                  "只改内容时索引仍可重建（路径集合未变），但重建出的哈希已不同——"
                  "因此内容 tamper 只能由**独立核验**发现，不能靠重建自证")
        finally:
            victim.write_text(original, encoding="utf-8")
            check(RR.verify_machine_index(root, index, order)["ok"] is True,
                  "还原字节后核验重新通过（上一条的失败确实来自被改的那一个文件）")

        (root / "zzz_extra.json").write_text("x\n", encoding="utf-8")
        extra = RR.verify_machine_index(root, index, order)
        check(extra["ok"] is False and
              [row["path"] for row in extra["extra"]] == ["zzz_extra.json"],
              "多一个未登记文件即被报为 extra 并判失败")
        raises(lambda: _build_index(root, order),
                RR.StepFailure, "没有登记 role",
                "多一个**无 role 登记**的文件时重建索引即 fail-closed"
                "（在成员集合判定之前就按 role 拦下）")
        (root / "zzz_extra.json").unlink()

        # 有 role 登记、但不在成员集合里的文件必须由**成员集合**判定拦下。
        stray_dir = root / RR.MATRIX_SNAPSHOT_DIRNAME
        stray = stray_dir / "EXTRA.json"
        stray.write_text("x\n", encoding="utf-8")
        check(RR._member_role(stray.relative_to(root).as_posix()) != "",
              "`EXTRA.json` 落在逐文档快照目录里，因此**有** role 登记"
              "（反例载体有效：它只能被成员集合判定拦下）")
        raises(lambda: _build_index(root, order),
                RR.StepFailure, "machine_artifact_index.members",
                "有 role 但不在成员集合里的文件，重建索引时被成员集合判定 fail-closed")
        stray.unlink()

        victim.unlink()
        missing = RR.verify_machine_index(root, index, order)
        check(missing["ok"] is False and
              [row["path"] for row in missing["missing"]] == [victim_rel],
              "删掉一个已登记文件即被报为 missing 并判失败")
        raises(lambda: _build_index(root, order),
                RR.StepFailure, "machine_artifact_index.members",
                "缺一个文件时重建索引即 fail-closed（缺文件不得靠自报补齐）")
        victim.write_text(original, encoding="utf-8")
        check(RR.verify_machine_index(root, index, order)["ok"] is True and
              _build_index(root, order) == index,
              "全部还原后核验通过且重建索引与原件逐字段相等"
              "（上两条失败确实来自磁盘差异，不是索引本身的问题）")

        # —— 核验产物自身的反例 -----------------------------------------
        raises(lambda: SCH.validate_artifact_index_check(
            {**verify, "ok": True, "missing": [{"path": "a"}]},
            document_order=order),
            SCH.AcceptanceSchemaError, "派生",
            "索引核验的 ok 必须由 missing/extra/hash_mismatch 派生，自报即拒")
        raises(lambda: SCH.validate_artifact_index_check(
            {**verify, "index_file_count": verify["index_file_count"] + 1},
            document_order=order),
            SCH.AcceptanceSchemaError, "scan_file_count",
            "磁盘扫描数与索引登记数不等即被拒（不得虚报等集）")
        raises(lambda: SCH.validate_artifact_index_check(
            {**verify, "not_scanned_by_index": []}, document_order=order),
            SCH.AcceptanceSchemaError, "not_scanned_by_index",
            "not_scanned_by_index 被改即被拒")
        raises(lambda: SCH.validate_artifact_index_check(
            {**verify, "members_recomputed": expected_members[:-1]},
            document_order=order),
            SCH.AcceptanceSchemaError, "members_recomputed",
            "members_recomputed 与 current 成员集合不一致即被拒")

        # —— 成员集合的封闭性（人工模板 / seal-only 不得进索引） --------
        idx_internal = set(SCH.INDEX_INTERNAL_MEMBERS)
        human = set(SCH.HUMAN_TEMPLATE_MEMBERS)
        seal_only = set(SCH.SEAL_ONLY_MEMBERS)
        check(human and seal_only and
              not (human | seal_only) & set(machine_members),
              f"人工模板 {sorted(human)} 与 seal-only {sorted(seal_only)} "
              f"都不在机器成员集合里")
        check(idx_internal <= set(machine_members) and
              not idx_internal & set(members),
              f"索引内部成员 {sorted(idx_internal)} 属于机器成员全集，"
              f"但不由索引登记自己的哈希")
        check(set(RR.MEMBER_ROLES) <= set(machine_members),
              "MEMBER_ROLES 里的每一项都属于本次 run 的机器成员集合")
        check(set(machine_members) - set(expected_members) == idx_internal,
              "可索引成员 = 机器成员 − 索引内部成员（无第三种差集）")
        check(set(machine_members) - set(RR.MEMBER_ROLES) == {
            f"{RR.MATRIX_SNAPSHOT_DIRNAME}/{key}.json" for key in order},
              "机器成员全集里除 `MEMBER_ROLES` 逐条登记的 run 级成员外，"
              "只剩逐文档 final 快照（没有第三种来源）")


# ---------------------------------------------------------------------------
# A3. `trv-1`：人工复核 JSON
# ---------------------------------------------------------------------------

def _decision_with(verdicts: dict, *, machine_gates: bool) -> dict:
    d = SCH.empty_review_decision(run_id="tree_table_ts5_synthetic",
                                  final_snapshot_ids=["fms-1", "fms-2"])
    d["machine_gates_passed"] = machine_gates
    for row in d["reviews"]:
        row["verdict"] = verdicts.get(row["check_id"], row["verdict"])
    d["decision"] = SCH.derive_review_decision(d)
    return d


def _a3_review_schema() -> None:
    run_id, snaps = "tree_table_ts5_synthetic", ["fms-1", "fms-2"]
    empty = SCH.empty_review_decision(run_id=run_id, final_snapshot_ids=snaps)
    check(tuple(empty["check_ids"]) == _EXPECTED_CHECK_IDS,
          "空模板的 check_ids 恰为固定的 10 项且顺序固定")
    check(tuple(empty["reviewer_roles"]) == _EXPECTED_ROLES,
          "空模板的 reviewer_roles 恰为 ('user', 'codex')")
    check(len(empty["reviews"]) == len(_EXPECTED_CHECK_IDS) * len(_EXPECTED_ROLES),
          f"空模板 {len(empty['reviews'])} 行 = 10 × 2 的完全笛卡尔积")
    check(all(r["verdict"] == "not_reviewed" for r in empty["reviews"]),
          "实施方只能生成全 not_reviewed 的空模板（不得代填）")
    check(empty["machine_gates_passed"] is False and
          empty["decision"] == "needs_changes" and
          SCH.derive_review_decision(empty) == "needs_changes",
          "空模板的 machine_gates_passed=False，派生 decision 为 needs_changes")
    SCH.validate_review_decision(empty, run_id=run_id, final_snapshot_ids=snaps)
    check(True, "空模板自身通过 `trv-1`（反例基线成立）")

    # —— decision 只能派生 -------------------------------------------
    all_pass = _decision_with({cid: "pass" for cid in _EXPECTED_CHECK_IDS},
                              machine_gates=True)
    check(SCH.derive_review_decision(all_pass) == "approve",
          "机器门通过 + 20 项全 pass ⇒ 派生 approve")
    SCH.validate_review_decision(all_pass, run_id=run_id, final_snapshot_ids=snaps)
    check(True, "全 pass 的自洽 review 通过 `trv-1`")
    one_fail = _decision_with({_EXPECTED_CHECK_IDS[3]: "fail"},
                              machine_gates=True)
    check(SCH.derive_review_decision(one_fail) == "reject",
          "任一项 fail ⇒ 派生 reject")
    one_pending = _decision_with({_EXPECTED_CHECK_IDS[7]: "not_reviewed"},
                                 machine_gates=True)
    check(SCH.derive_review_decision(one_pending) == "needs_changes",
          "任一项 not_reviewed ⇒ 派生 needs_changes")
    gate_failed = _decision_with({cid: "pass" for cid in _EXPECTED_CHECK_IDS},
                                 machine_gates=False)
    check(SCH.derive_review_decision(gate_failed) == "needs_changes",
          "机器门未过时即使 20 项全 pass 也只得 needs_changes（人工不得越过机器门）")

    raises(lambda: SCH.validate_review_decision(
        {**all_pass, "decision": "reject"}, run_id=run_id,
        final_snapshot_ids=snaps),
        SCH.AcceptanceSchemaError, "不一致",
        "自报 decision 与派生值不一致即被拒（decision 不得自报）")
    raises(lambda: SCH.validate_review_decision(
        {**all_pass, "reviews": all_pass["reviews"][:-1]}, run_id=run_id,
        final_snapshot_ids=snaps),
        SCH.AcceptanceSchemaError, "笛卡尔积",
        "少一行复核即被拒（不得只审一半）")
    raises(lambda: SCH.validate_review_decision(
        {**all_pass, "reviews": list(reversed(all_pass["reviews"]))},
        run_id=run_id, final_snapshot_ids=snaps),
        SCH.AcceptanceSchemaError, "笛卡尔积",
        "复核行乱序即被拒（顺序也是契约）")
    raises(lambda: SCH.validate_review_decision(
        {**all_pass, "check_ids": list(_EXPECTED_CHECK_IDS)[:-1]},
        run_id=run_id, final_snapshot_ids=snaps),
        SCH.AcceptanceSchemaError, "check_ids",
        "check ID 集合被改即被拒（10 项是固定的）")
    raises(lambda: SCH.validate_review_decision(
        {**all_pass, "reviewer_roles": ["user"]}, run_id=run_id,
        final_snapshot_ids=snaps),
        SCH.AcceptanceSchemaError, "reviewer_roles",
        "复核角色被改即被拒（恰为 user + codex）")
    raises(lambda: SCH.validate_review_decision(
        {**all_pass, "run_id": "someone_else"}, run_id=run_id,
        final_snapshot_ids=snaps),
        SCH.AcceptanceSchemaError, "run_id",
        "review 绑定到别的 run 即被拒")
    raises(lambda: SCH.validate_review_decision(
        {**all_pass, "final_snapshot_ids": ["fms-1"]}, run_id=run_id,
        final_snapshot_ids=snaps),
        SCH.AcceptanceSchemaError, "final_snapshot_ids",
        "final snapshot 序列被改即被拒（少收一份文档不得自证一致）")
    raises(lambda: SCH.validate_review_decision(
        {**all_pass, "review_schema_version": "trv-2"}, run_id=run_id,
        final_snapshot_ids=snaps),
        SCH.AcceptanceSchemaError, "trv-1",
        "复核 schema 版本漂移即被拒")

    # —— 人读投影 -----------------------------------------------------
    proj = SCH.manual_review_projection(all_pass)
    check(SCH.manual_review_projection(all_pass) == proj,
          "同一 review JSON 的人读投影逐字节确定")
    check("不构成授权" in proj and "review_decision.json" in proj,
          "投影显式声明自己不是授权文件（唯一授权是同目录 review JSON）")
    check(SCH.manual_review_projection(one_fail) != proj,
          "投影随 verdict 变化（不是空转的常量文本）")
    check(all(cid in proj for cid in _EXPECTED_CHECK_IDS),
          "投影逐项列出 10 个 check ID")
    check(proj.count("\n") >= len(_EXPECTED_CHECK_IDS),
          "投影至少每项一行（人读表不得压缩掉条目）")


# ---------------------------------------------------------------------------
# A4. `tra-1`：封存身份
# ---------------------------------------------------------------------------

def _attestation(*, run_id: str = "tree_table_ts5_synthetic",
                 run_dir_relpath: str = "tmp/synthetic") -> dict:
    ident = SCH.review_attestation_identity(
        trust_root_file_sha256=_SHA_A, trust_root_content_fingerprint=_SHA_B,
        run_manifest_sha256=_SHA_A, artifact_index_sha256=_SHA_B,
        artifact_index_check_sha256=_SHA_A, review_decision_sha256=_SHA_B,
        runner_version=SCH.TS5_ACCEPTANCE_RUNNER_VERSION,
        issuer_version="ffv-1", final_fingerprints={"fms-b": _SHA_B,
                                                    "fms-a": _SHA_A})
    return {
        "schema_type": "TS5ReviewAttestation",
        "review_attestation_schema_version":
            SCH.TS5_REVIEW_ATTESTATION_SCHEMA_VERSION,
        "run_id": run_id,
        "run_dir_relpath": run_dir_relpath,
        "sealed_at_utc": "2026-09-19T00:00:00Z",
        "identity": ident,
        "conclusions": {key: True for key in _ATTESTATION_CONCLUSIONS},
    }


def _a4_attestation() -> None:
    att = _attestation()
    SCH.validate_review_attestation(
        att, run_id="tree_table_ts5_synthetic", run_dir_relpath="tmp/synthetic")
    check(True, "自洽的封存身份通过 `tra-1`")
    check(list(att["identity"]["final_snapshot_fingerprints"]) == ["fms-a", "fms-b"],
          "final snapshot 指纹按 key 升序固定排列（与传入顺序无关）")
    check("sealed_at_utc" not in att["identity"],
          "身份载荷**不含**时间戳（同一批字节重签得到同一身份）")
    check(_attestation()["identity"] == att["identity"],
          "同一批字节两次构造的身份逐字节相同（可复算，不是随机值）")

    raises(lambda: SCH.validate_review_attestation(
        {**att, "identity": {**att["identity"], "run_manifest_sha256": "short"}},
        run_id="tree_table_ts5_synthetic", run_dir_relpath="tmp/synthetic"),
        SCH.AcceptanceSchemaError, "sha256",
        "封存身份里的任一 sha256 形状非法即被拒")
    raises(lambda: SCH.validate_review_attestation(
        {**att, "identity": {**att["identity"],
                             "acceptance_runner_version": "tar-2"}},
        run_id="tree_table_ts5_synthetic", run_dir_relpath="tmp/synthetic"),
        SCH.AcceptanceSchemaError, "tar-1",
        "封存身份绑定的 runner 版本漂移即被拒（旧版本结论不可比）")
    raises(lambda: SCH.validate_review_attestation(
        {**att, "identity": {k: v for k, v in att["identity"].items()
                             if k != "artifact_index_sha256"}},
        run_id="tree_table_ts5_synthetic", run_dir_relpath="tmp/synthetic"),
        SCH.AcceptanceSchemaError, "artifact_index_sha256",
        "身份少绑一个已封存字节即被拒（绑定必须是封闭字段表）")
    raises(lambda: SCH.validate_review_attestation(
        {**att, "identity": {**att["identity"], "extra": 1}},
        run_id="tree_table_ts5_synthetic", run_dir_relpath="tmp/synthetic"),
        SCH.AcceptanceSchemaError, "extra",
        "身份多带一个字段即被拒（不得夹带无用信息）")
    raises(lambda: SCH.validate_review_attestation(
        {**att, "conclusions": {k: v for k, v in att["conclusions"].items()
                                if k != "final_verifier_ok"}},
        run_id="tree_table_ts5_synthetic", run_dir_relpath="tmp/synthetic"),
        SCH.AcceptanceSchemaError, "final_verifier_ok",
        "结论位缺一项即被拒（六项结论必须齐备）")
    raises(lambda: SCH.validate_review_attestation(
        {**att, "run_id": "other_run"},
        run_id="tree_table_ts5_synthetic", run_dir_relpath="tmp/synthetic"),
        SCH.AcceptanceSchemaError, "run_id",
        "封存身份绑定到别的 run 即被拒")
    raises(lambda: SCH.validate_review_attestation(
        {**att, "run_dir_relpath": "elsewhere"},
        run_id="tree_table_ts5_synthetic", run_dir_relpath="tmp/synthetic"),
        SCH.AcceptanceSchemaError, "run_dir_relpath",
        "封存身份绑定到别的目录即被拒")
    raises(lambda: SCH.validate_review_attestation(
        {**att, "identity": {**att["identity"],
                             "final_snapshot_fingerprints": {"fms-b": _SHA_B,
                                                             "fms-a": _SHA_A}}},
        run_id="tree_table_ts5_synthetic", run_dir_relpath="tmp/synthetic"),
        SCH.AcceptanceSchemaError, "升序",
        "final snapshot 指纹乱序即被拒")
    raises(lambda: SCH.validate_review_attestation(
        {**att, "review_attestation_schema_version": "tra-2"},
        run_id="tree_table_ts5_synthetic", run_dir_relpath="tmp/synthetic"),
        SCH.AcceptanceSchemaError, "tra-1",
        "封存 schema 版本漂移即被拒")


# ---------------------------------------------------------------------------
# A5. runner 级 fail-closed（全部在 `bind_inputs()` 之前就失败）
# ---------------------------------------------------------------------------

def _a5_runner_fail_closed() -> None:
    with tempfile.TemporaryDirectory(prefix="ts5_artifacts_") as tmp:
        root = Path(tmp)
        missing_dir = root / "does_not_exist"
        raises(lambda: RR.validate_only(missing_dir),
                RR.StepFailure, "run_dir",
                "`--validate-only` 对不存在的目录 fail-closed")
        raises(lambda: RR.seal_review(missing_dir),
                RR.StepFailure, "run_dir",
                "`--seal-review` 对不存在的目录 fail-closed")

        empty = root / "empty_run"
        empty.mkdir()
        raises(lambda: RR.seal_review(empty),
                RR.StepFailure, "run_manifest.json",
                "缺 run_manifest.json 的目录 seal 即 fail-closed（不给半成品封存）")

        sealed = root / "already_sealed"
        sealed.mkdir()
        (sealed / "review_attestation.json").write_text("{}", encoding="utf-8")
        raises(lambda: RR.seal_review(sealed),
                RR.StepFailure, "review_attestation.json",
                "已存在 attestation 的 run 重复 seal 被拒（不得覆盖后重签）")

        raises(lambda: RR.execute_run(run_id="bad id!", results_root=root,
                                      generated_at="2026-09-19T00:00:00Z"),
                RR.StepFailure, "run_id",
                "run_id 形状非法时执行即 fail-closed")
        raises(lambda: RR.execute_run(run_id="tree_span_ts4_wrong_prefix",
                                      results_root=root,
                                      generated_at="2026-09-19T00:00:00Z"),
                RR.StepFailure, "tree_table_ts5_",
                "run_id 不以 TS5 前缀开头即 fail-closed（不得混用历史 run 命名）")
        existing = root / f"{SCH.RUN_DIR_PREFIX}already_here"
        existing.mkdir()
        raises(lambda: RR.execute_run(
            run_id=f"{SCH.RUN_DIR_PREFIX}already_here", results_root=root,
            generated_at="2026-09-19T00:00:00Z"),
            RR.StepFailure, "create-only",
            "结果目录已存在时执行被拒（create-only，绝不覆盖历史目录）")
        check(not (missing_dir / "review_attestation.json").exists() and
              not (empty / "review_attestation.json").exists(),
              "上述 fail-closed 路径没有留下任何封存产物（失败不留半成品）")
        check(sorted(p.name for p in root.iterdir()) ==
              sorted([existing.name, "already_sealed", "empty_run"]),
              "临时目录里只有本测试自己建的目录，runner 一个文件都没写"
              "（含 execute_run 的三条 fail-closed 路径）")

    # 信任锚文件被改即 fail-closed：把 workdir 指向一个临时副本再改一个字节。
    saved_root, saved_relpath = RR.REPO_ROOT, RR.TRUST_ROOT_RELPATH
    try:
        with tempfile.TemporaryDirectory(prefix="ts5_artifacts_") as tmp:
            fake_root = Path(tmp)
            rel = Path("fixtures") / "ts5_trust_roots_v2.json"
            (fake_root / rel).parent.mkdir(parents=True, exist_ok=True)
            (fake_root / rel).write_bytes(_TRUST_ROOT_FILE.read_bytes())
            RR.REPO_ROOT, RR.TRUST_ROOT_RELPATH = fake_root, rel.as_posix()
            dr = RR.load_trust_root()
            check(dr["trust_root_file_sha256"] == RR.TRUST_ROOT_FILE_SHA256,
                  "未改动的临时副本哈希等于 runner 字面量（反例基线成立）")
            (fake_root / rel).write_bytes(_TRUST_ROOT_FILE.read_bytes() + b"\n")
            raises(lambda: RR.load_trust_root(),
                    RR.StepFailure, "trust_root_file_sha256",
                    "信任锚文件多一个字节即 fail-closed（根锚变化后历史结论不可比）")
            (fake_root / rel).write_text("{}", encoding="utf-8")
            raises(lambda: RR.load_trust_root(),
                    RR.StepFailure, "trust_root_file_sha256",
                    "信任锚被替换为空对象同样先被文件哈希拦下")
    finally:
        RR.REPO_ROOT, RR.TRUST_ROOT_RELPATH = saved_root, saved_relpath
    check(RR.REPO_ROOT == saved_root and RR.TRUST_ROOT_RELPATH == saved_relpath and
          _sha256_file(_TRUST_ROOT_FILE) == RR.TRUST_ROOT_FILE_SHA256,
          "反例只在内存里改模块常量：真实信任锚文件与其哈希均未被触碰")


# A6. 机器产物投影在**真实样本**上可执行
#
# A2/A3 都在合成种子/合成目录上测索引、review 与 attestation，产物**构造器**本身
# 直到真实验收首跑才第一次被调用（首跑即在 `build_relation_rows` 里 fail）。这一组
# 用一份真实文档把三个聚合投影函数跑到底，把该空白补上：成员集合、版本自报、两条
# 版本轴互不代填、关系端点 kind。只读，不落任何 `evaluation/results/**` 目录。

_A6_DOCUMENT = "FIXTURE_BOND_2026__non_300750_ts4"

#: 由 `execute_run` 而非三个聚合投影函数产出的 run 级成员（A2 已单独覆盖）。
_A6_RUN_LEVEL_MEMBERS = frozenset({
    "run_manifest.json", "database_immutability.json",
    "machine_artifact_index.json", "machine_artifact_index_check.json",
})


def _derivation_stub_samples() -> list:
    """`derive_required_sample_verdicts` 需要的**最小**样本读视图（每个声明文档一份）。

    固定样本裁决必须覆盖**全部**声明文档（缺一个即 fail-closed），因此任何"只喂一个
    文档"的调用方都必须先补齐。补齐件只提供派生器真正读到的字段，并且**故意**带上
    `passed=True` / `status="pass"` 这类自报值：真实调用路径上也就此证明派生器看不见
    它们。它不主张任何真实文档内容，真实结论只来自真实验收。
    """
    table = _make_table_v4()
    samples = []
    for decl in RR.REQUIRED_SAMPLE_DECLARATIONS:
        key = decl["document_key"]
        if key in {s["document_key"] for s in samples}:
            continue
        samples.append({
            "document_key": key,
            "wrapper": None,
            "final": SimpleNamespace(
                document_id=f"stub-{key}", document_version=table.document_version,
                tables=(table,), relations=(), table_count=1,
                final_span_count=0, blocking_gap_count=0,
                conservation=SimpleNamespace(problems=(), layers=()),
                gaps=SimpleNamespace(entries=(), blocking_entry_count=0)),
            "passed": True,
            "status": "pass",
            "self_reported_verdict_accepted": True,
            "refusal_reason_codes": (),
            "refusal_reason": None,
            "refusal_blocking_kinds": (),
        })
    return samples


def _a6_artifact_projections() -> None:
    inputs = RR.bind_inputs()
    samples = RR._decorate(
        [RR.build_document(inputs=inputs, document_key=_A6_DOCUMENT)])
    with tempfile.TemporaryDirectory(prefix="ts5_artifact_projections_") as tmp:
        tmp_dir = Path(tmp)
        # 复刻 `execute_run` 的**顺序前提**：final 快照必须先落盘，快照索引才能
        # 取到真实 `file_sha256`。写反了索引会记 `null`，而只读回读在文件已存在
        # 时重算得真实哈希，于是逐字节比较必然判成"内容 tamper"——这个缺陷只在
        # 真实验收 + validate-only 里才现形，所以在这里把前提和取值一起钉住。
        snapshot_paths = {}
        for sample in samples:
            relpath = (f"{RR.MATRIX_SNAPSHOT_DIRNAME}/"
                       f"{sample['document_key']}.json")
            path = tmp_dir / relpath
            path.parent.mkdir(parents=True, exist_ok=True)
            RR._write_json(path, RR._snapshot_of(sample))
            snapshot_paths[relpath] = path
        # 本组只构建一个真实文档，而 `required_sample_matrix` 必须覆盖全部声明文档
        # （缺一个即 fail-closed）。这里只补齐**派生器**的输入；矩阵其余部分仍由那个
        # 真实文档产出。补齐件不带任何"真实结论"的含意，且状态只作投影形状之用。
        stub_rows = RR.derive_required_sample_verdicts(_derivation_stub_samples())
        json_members = RR._json_members(
            samples, run_id="tree_table_ts5_projected", results_dir=tmp_dir,
            machine_gates={
                "machine_gates_version": "tgm-1",
                "passed": False,
                "failed_gate_count": sum(1 for r in stub_rows
                                         if r["status"] != "pass"),
                "failed_gate_ids": [f"required_sample.{r['capability']}"
                                    for r in stub_rows if r["status"] != "pass"],
                "required_sample_matrix": stub_rows,
                "note": ("本组只构建一个真实文档；固定样本裁决由派生器对补齐读视图"
                         "重算，仅用于投影形状，真实结论见真实验收产物。"),
            })
        md_members = RR._md_members(
            samples, matrix=json_members["table_acceptance_matrix.json"])
        jsonl_members = RR._jsonl_members(samples)
        index_entries = json_members["final_snapshot_index.json"]["snapshots"]
        hash_offenders = [
            (entry["relpath"], entry["file_sha256"],
             RR.sha256_file(snapshot_paths[entry["relpath"]]))
            for entry in index_entries
            if entry["file_sha256"]
            != RR.sha256_file(snapshot_paths[entry["relpath"]])]
        check(len(index_entries) == len(samples) and not hash_offenders,
              f"快照索引 {len(index_entries)} 条的 `file_sha256` 都取到文件且等于磁盘"
              f"上那份快照的哈希（不符：{hash_offenders}）")

    projected = set(json_members) | set(md_members) | set(jsonl_members)
    check(projected == set(RR.MEMBER_ROLES) - set(_A6_RUN_LEVEL_MEMBERS),
          f"真实文档上三个聚合投影产出 {len(projected)} 个成员，与 `MEMBER_ROLES` "
          f"去掉 run 级成员后逐项相等（差集 "
          f"{sorted(projected.symmetric_difference(
              set(RR.MEMBER_ROLES) - set(_A6_RUN_LEVEL_MEMBERS))) }）")

    syn = json_members["final_navigation_synopsis.json"]
    check(syn["schema_version"] == V.FINAL_SYNOPSIS_SCHEMA_VERSION
          and syn["synopsis_version"] == V.FINAL_SYNOPSIS_VERSION
          and V.FINAL_SYNOPSIS_SCHEMA_VERSION != V.SYNOPSIS_SCHEMA_VERSION,
          f"final_navigation_synopsis.json 自报 TS5 轴 "
          f"{syn['schema_version']} / {syn['synopsis_version']}")
    check(syn["ts4_schema_version"] == V.SYNOPSIS_SCHEMA_VERSION
          and syn["ts4_synopsis_version"] == V.SYNOPSIS_VERSION,
          f"同一成员内的 TS4 轴另记为 {syn['ts4_schema_version']} / "
          f"{syn['ts4_synopsis_version']}（两条轴不互相代填）")
    role = RR.MEMBER_ROLES["final_navigation_synopsis.json"]
    check(f"@{V.FINAL_SYNOPSIS_SCHEMA_VERSION}/{V.FINAL_SYNOPSIS_VERSION}" in role
          and V.SYNOPSIS_SCHEMA_VERSION not in role
          and V.SYNOPSIS_VERSION not in role,
          f"该成员的 role 记录 final 轴且不拿 TS4 轴冒充：{role!r}")
    entries = [s for d in syn["documents"] for s in d["synopses"]]
    check(entries and all(s["schema_version"] == V.FINAL_SYNOPSIS_SCHEMA_VERSION
                          and s["synopsis_version"] == V.FINAL_SYNOPSIS_VERSION
                          for s in entries),
          f"{len(entries)} 条 final synopsis 条目的 wire/算法版本全为 "
          f"{V.FINAL_SYNOPSIS_SCHEMA_VERSION} / {V.FINAL_SYNOPSIS_VERSION}")

    relations = json_members["table_relations.json"]
    rows = [r for d in relations["documents"] for r in d["relations"]]
    kinds = {t.endpoint_kind for t in TS.ENDPOINT_REF_TYPES}
    check(rows and all(r["source"]["endpoint_kind"] in kinds
                       and r["target"]["endpoint_kind"] in kinds for r in rows),
          f"关系的两端在产物里都带合法 `endpoint_kind`（{len(rows)} 条；"
          f"实得 {sorted({(r['source']['endpoint_kind'], r['target']['endpoint_kind']) for r in rows})}；"
          f"合法 kinds={sorted(kinds)}）")
    check(all(d["relation_count"] == len(d["relations"]) for d in relations["documents"]),
          "每个文档的 relation_count 与其 relations 长度一致（投影未截断）")

    # 落盘契约：每个成员必须能按 `run_tree_span_acceptance._write_json` /
    # `_write_json_lines` 的原样方式序列化。typed 对象漏进产物（例如把
    # `ConservationTerm` 而不是它的 count 放进投影）**只会在真正写盘时**才炸，
    # 因此在内存里"构造成功"不等于产物可用。
    unserializable = []
    # run 级与逐文档产物同样要过落盘契约（manifest / 人工模板 / 快照不是三个聚合
    # 投影函数产出的，但同样由 `_write_json` 直接 `json.dumps`）。
    written_like = {"run_manifest.json": RR.build_run_manifest(
        inputs=inputs, samples=samples, run_id="tree_table_ts5_projected",
        run_id_source="probe", run_dir_relpath="probe", generated_at="probe"),
        "review_decision.json": RR.build_review_template(
            run_id="tree_table_ts5_projected",
            final_snapshot_ids=[s["final"].snapshot_id for s in samples]),
        "matrix_snapshot/<fixture>.json": RR._snapshot_of(samples[0])}
    for name, payload in sorted(dict(json_members, **written_like).items()):
        try:
            json.dumps(payload, ensure_ascii=False, indent=1)
        except TypeError as e:
            unserializable.append((name, str(e)))
    check(not unserializable,
          f"{len(json_members) + len(written_like)} 个 json 产物都能按落盘契约序列化"
          f"（不可序列化：{unserializable}）")
    bad_rows = []
    for name, rows in sorted(jsonl_members.items()):
        for row in rows:
            try:
                canonical_json(row)
            except TypeError as e:
                bad_rows.append((name, str(e)))
                break
    check(not bad_rows,
          f"{len(jsonl_members)} 个 jsonl 成员逐行可规范序列化（不可序列化：{bad_rows}）")

    # 守恒投影的语义：`total` 必须是**本层单位**的计数（与 `terms` 同口径），且
    # 资格位必须由**共用判据**重算而不是照抄记录位。这一条把"只投影 count 而不是
    # typed 对象"和"资格不得自报"两件事一起钉成语义，而不只是"能序列化"。
    matrix = json_members["table_acceptance_matrix.json"]
    conservation_rows = [row for row in matrix["rows"]
                         if row["check_id"] == "final_conservation_zero"]
    layer_rows = [(f"{key}:{kind}", layer)
                  for row in conservation_rows
                  for key, summary in row["machine_evidence"].items()
                  for kind, layer in summary["layers"].items()]
    check(conservation_rows and layer_rows
          and all(layer["recomputed_eligible"] == (
                      not layer["problems"]
                      and layer["sum_of_values"] == layer["total"]
                      and all(int(layer["terms"].get(name, 0)) == 0
                              for name in TS.CONSERVATION_ZERO_REQUIRED_TERMS
                              if name in layer["terms"]))
                  and layer["total"] == sum(layer["terms"].values())
                  for _, layer in layer_rows),
          f"{len(layer_rows)} 层守恒在矩阵里的 `total` 都是本层单位的计数、等于分项"
          f"之和，且资格位等于共用判据的重算值（{sorted(name for name, _ in layer_rows)}）")
    check({name.split(":", 1)[1] for name, _ in layer_rows}
          == set(TS.CONSERVATION_LAYER_KINDS),
          f"矩阵投影出的守恒层恰为登记的 {sorted(TS.CONSERVATION_LAYER_KINDS)}"
          f"（不多不少，跨层不得相加后冒名）")


def _a7_required_sample_derivation() -> None:
    """P1-B：§19.13 固定样本的裁决必须**从真实产物派生**，执行者没有自报入口。

    最小反例只覆盖"派生"这件事本身：声明里写了要求而真实产物不满足时必须 fail；
    产物满足时 pass；缺席只有在**对象自己登记 + 台账有痕**时才可辩护；以及往输入里
    塞 `passed=True` / `status="pass"` 绝不能改变任何一条裁决。
    """
    # 1) 静态自报矩阵必须已被声明式矩阵取代，且声明本身落在封闭登记表里。
    check(not hasattr(RR, "REQUIRED_SAMPLE_MATRIX"),
          "静态自报的 `REQUIRED_SAMPLE_MATRIX` 必须已被声明式 "
          "`REQUIRED_SAMPLE_DECLARATIONS` 取代（不得两条路并存）")
    declared = RR.REQUIRED_SAMPLE_DECLARATIONS
    check(len(declared) == 10,
          f"§19.13 的固定样本必须恰为 10 条（得到 {len(declared)}）")
    declared_keys = [d["document_key"] for d in declared]
    problems = RR._declaration_problems(declared_keys)
    check(problems == [],
          f"声明的要求元素/关系/缺口/不变量必须全部登记在册"
          f"（{problems[:3]}）")
    check({d["requirement_kind"] for d in declared} <= set(RR._REQUIREMENT_KINDS),
          f"声明的 requirement_kind 必须属于 {RR._REQUIREMENT_KINDS}")
    for name in ("REQUIRED_ELEMENT_VOCABULARY", "REQUIRED_DOCUMENT_FLAGS"):
        vocab = getattr(RR, name)
        check(bool(vocab) and len(set(vocab)) == len(vocab),
              f"{name} 必须是非空去重词表（{vocab}）")
    # 声明里写的要求元素必须每一个都有真实判据，否则"声明了但没人实现"会被静默放过。
    for element in {e for d in declared for e in d.get("required_elements", ())}:
        try:
            RR._table_element_observed(_make_table_v4(), element)
        except RR.StepFailure as e:
            check(False, f"要求元素 {element!r} 没有真实判据：{e}")

    # 2) 真实 `TableObjectV4` 夹具（2×2 有表头网格，title/unit/note 皆空）。
    table = _make_table_v4()
    check(table.page_number == 1 and "header" in {r.role for r in table.rows}
          and not table.title_blocks and not table.note_blocks,
          "夹具必须是 page_number=1、有表头而无表题/备注的对象")

    def _rebuild(table, **over):
        kw = {f.name: getattr(table, f.name) for f in _fields(table)}
        kw.update(over)
        return TS.TableObjectV4.create(**kw)

    def _field_short(table, **over):
        """把某字段登记为**不确定**：对象自己承认 + 状态随之 partial（口径自洽）。"""
        over.setdefault("structure_state", "partial")
        over.setdefault("structure_state_reason", "synthetic_field_not_proven")
        return _rebuild(table, **over)

    def _final(tables=(), gaps=(), document_id=None):
        return SimpleNamespace(
            document_id=document_id or table.document_id,
            document_version=table.document_version,
            tables=tuple(tables), relations=(), table_count=len(tables),
            final_span_count=0, blocking_gap_count=0,
            # 文档级守恒与不变量由 A5/A6 单独覆盖，这里只需一个"合格"的读视图。
            conservation=SimpleNamespace(problems=(), layers=()),
            gaps=SimpleNamespace(entries=tuple(gaps), blocking_entry_count=0))

    def _gap(kind, *, table_id, page=1):
        return TS.TableGapEntry(
            gap_kind=kind, detail_code="synthetic_detail", page_number=page,
            table_id=table_id, component_id=None, disposition_id=None,
            blocks_document_capability=False)

    def _sample(final, document_key="synthetic", **extra):
        # 故意把"自报通过"的字段塞进输入：派生器必须完全看不见它们。
        return dict({"final": final, "wrapper": None,
                     "document_key": document_key, "verdict": "issued",
                     "refusal_reason_codes": (), "refusal_reason": None,
                     "refusal_blocking_kinds": (),
                     "passed": True, "status": "pass",
                     "self_reported_verdict_accepted": True}, **extra)

    def _decl(**over):
        base = dict(capability="合成能力", sample="合成样本",
                    must_confirm="合成确认项", document_key="synthetic",
                    requirement_kind="table_object",
                    locator={"page_numbers": (1,)}, expected_object_count=1,
                    required_elements=("body",))
        base.update(over)
        return base

    # 3) 对象在场/缺席：自报字段不得改变结论。
    row = RR._derive_table_object_verdict(_decl(), _sample(_final((table,))))
    check(row["status"] == "pass" and row["observed_object_count"] == 1,
          f"对象在场且要求元素在场时必须 pass（{row['failure_reasons']}）")
    row = RR._derive_table_object_verdict(_decl(), _sample(_final()))
    check(row["status"] == "fail"
          and row["failure_reasons"] == ["required_object_count:1->0"]
          and row["document_table_inventory"] == [],
          f"要求对象缺席时必须 fail 并附上真实的整份文档对象清单（{row}）")

    # 4) 要求元素缺席：只有"对象登记 + 台账有痕"才可辩护。
    row = RR._derive_table_object_verdict(
        _decl(required_elements=("body", "title")), _sample(_final((table,))))
    check(row["status"] == "fail" and row["failure_reasons"] == [
        f"required_element_absent_without_reviewable_gap:title:{table.table_id}"],
        f"要求元素缺席且无可复核 typed 声明时必须 fail（{row['failure_reasons']}）")
    titleless = _field_short(table, missing_or_uncertain_fields=("title",))
    check(titleless.table_id != table.table_id,
          "反例成立的前提：登记缺口会改变对象身份（否则缺口绑定测不出东西）")
    row = RR._derive_table_object_verdict(
        _decl(required_elements=("body", "title")),
        _sample(_final((titleless,), gaps=(_gap("provenance_incomplete",
                                                table_id=titleless.table_id),))))
    check(row["status"] == "pass"
          and row["observed_objects"][0]["required_elements"]["title"]["deferred"]
          is not None,
          f"对象自认缺口码 + 台账里有绑定它的 typed gap 时才算可辩护缺席"
          f"（{row['failure_reasons']}）")
    row = RR._derive_table_object_verdict(
        _decl(required_elements=("body", "title")),
        _sample(_final((titleless,), gaps=(_gap("provenance_incomplete",
                                                table_id=None),))))
    check(row["status"] == "fail" and row["failure_reasons"] == [
        f"required_element_absent_without_reviewable_gap:title:"
        f"{titleless.table_id}"],
        f"typed gap 未绑定到该对象时不得充当缺席理由（{row['failure_reasons']}）")
    # `note` 不在对象自己的 `MISSING_FIELD_CODES` 里，因此它**没有**可辩护缺席形态：
    # 一个连"备注缺失"都无法登记的对象不可能自证备注可缺席。
    row = RR._derive_table_object_verdict(
        _decl(required_elements=("body", "note")), _sample(_final((table,))))
    check(row["status"] == "fail" and row["failure_reasons"] == [
        f"required_element_absent_and_has_no_gap_code:note:{table.table_id}"],
        f"无缺口码的元素缺席必须 fail，且理由如实区分于「可登记但未辩护」"
        f"（{row['failure_reasons']}）")
    # header 只有在给出**登记过的**缺席原因时才可辩护。这一条的守卫有两层：构造期
    # 就拒绝未登记的原因（因此这种对象根本造不出来），派生器再镜像判一次（读回纵深）。
    body_only_rows = tuple(_replace(row, role="body", is_repeated_header=False)
                           for row in table.rows)
    headerless_kw = dict(
        rows=body_only_rows, structure_kind="headerless_grid",
        structure_state="partial", structure_state_reason="header_not_proven",
        header_absence_reason="no_header_evidence",
        missing_or_uncertain_fields=("header",))
    headerless = _rebuild(table, **headerless_kw)
    row = RR._derive_table_object_verdict(
        _decl(required_elements=("header",)),
        _sample(_final((headerless,), gaps=(_gap("provenance_incomplete",
                                                 table_id=headerless.table_id),))))
    check(row["status"] == "pass",
          f"header 缺席但给出了登记过的 header_absence_reason 时可辩护"
          f"（{row['failure_reasons']}）")
    unregistered = None
    try:
        _rebuild(table, **dict(headerless_kw,
                               header_absence_reason="synthetic_unregistered"))
    except SchemaValidationError as e:
        unregistered = str(e)
    check(unregistered is not None and "header_absence_reason" in unregistered,
          f"未登记的 header_absence_reason 必须在构造期就被拒"
          f"（{unregistered}）")

    # 5) 要求续表关系：`continued_by` 缺席同样只有"登记 + 有痕"才可辩护。
    row = RR._derive_table_object_verdict(
        _decl(required_relations=("continued_by",)), _sample(_final((table,))))
    check(row["status"] == "fail" and row["failure_reasons"] == [
        f"required_relation_absent:continued_by:{table.table_id}"],
        f"要求续表关系而真实产物里没有时必须 fail（{row['failure_reasons']}）")
    unclosed = _field_short(table, missing_or_uncertain_fields=("continuation",))
    row = RR._derive_table_object_verdict(
        _decl(required_relations=("continued_by",)),
        _sample(_final((unclosed,),
                       gaps=(_gap("ambiguous_continuation",
                                  table_id=unclosed.table_id),))))
    check(row["status"] == "pass",
          f"续表关系缺席但对象自认 `continuation` 不确定且台账有 "
          f"`ambiguous_continuation` 时才可辩护（{row['failure_reasons']}）")

    # 6) 视觉对象负例：该页不得有 TableObject，且必须有确切的 typed gap。
    intruder = RR._derive_typed_gap_verdict(
        _decl(requirement_kind="typed_gap",
              required_gap_kinds=("visual_object_not_table",),
              forbid_table_object=True), _sample(_final((table,))))
    check(intruder["status"] == "fail"
          and any(r.startswith("unexpected_table_object:")
                  for r in intruder["failure_reasons"]),
          f"自称视觉对象的那一页出现 TableObject 时必须 fail"
          f"（{intruder['failure_reasons']}）")
    missing_gap = RR._derive_typed_gap_verdict(
        _decl(requirement_kind="typed_gap", locator={"page_numbers": (2,)},
              required_gap_kinds=("visual_object_not_table",),
              forbid_table_object=True), _sample(_final()))
    check(missing_gap["status"] == "fail"
          and missing_gap["failure_reasons"] ==
          ["required_typed_gap_absent:visual_object_not_table"]
          and missing_gap["document_gap_kind_histogram"] == {},
          f"要求 typed gap 而台账里没有时必须 fail 并附上真实的缺口分布"
          f"（{missing_gap}）")
    visual = RR._derive_typed_gap_verdict(
        _decl(requirement_kind="typed_gap", locator={"page_numbers": (2,)},
              required_gap_kinds=("visual_object_not_table",),
              forbid_table_object=True),
        _sample(_final(gaps=(_gap("visual_object_not_table", table_id=None,
                                  page=2),))))
    check(visual["status"] == "pass" and visual["observed_gap_count"] == 1,
          f"确切位置的 typed visual gap 在场时必须 pass（{visual}）")

    # 7) 文档级不变量同样只能从真实产物重算，未登记的不变量 fail-closed。
    flags = RR._derive_document_invariants_verdict(
        _decl(requirement_kind="document_invariants",
              locator={"scope": "document"},
              required_flags=("has_table_object",)), _sample(_final((table,))))
    check(flags["status"] == "pass"
          and flags["observed_flags"] == {"has_table_object": True},
          f"文档级不变量按真实 table_count 重算（{flags}）")
    flags = RR._derive_document_invariants_verdict(
        _decl(requirement_kind="document_invariants",
              locator={"scope": "document"},
              required_flags=("has_table_object",)), _sample(_final()))
    check(flags["status"] == "fail"
          and flags["failure_reasons"] ==
          ["document_invariant_failed:has_table_object"],
          f"不变量不成立时必须 fail（{flags['failure_reasons']}）")
    unregistered = None
    try:
        RR._derive_document_invariants_verdict(
            _decl(requirement_kind="document_invariants",
                  locator={"scope": "document"},
                  required_flags=("not_registered_flag",)), _sample(_final()))
    except RR.StepFailure as e:
        unregistered = str(e)
    check(unregistered is not None and "not_registered_flag" in unregistered,
          f"未登记的文档级不变量必须 fail-closed（{unregistered}）")

    # 8) 生产模块里不得出现公司名或固定页码/表号字面量（泛化正例声明的真实判据）。
    hits = RR._page_or_company_literals_in_production()
    check(hits == [],
          f"生产模块里检出公司名或固定页码/表号字面量：必须由真实产物而不是字面量"
          f"分支（{hits[:3]}）")
    # 唯一豁免必须**恰好**是"负向表自身的元素行"：豁免清单就是这张表的四行，不得
    # 多出任何一行（多出来的那一行就是被静默放过的真实字面量）。
    exemptions = RR._literal_scan_exemptions()
    check(exemptions
          and all(row["declaration"].startswith("_FORBIDDEN_")
                  for row in exemptions),
          f"字面量扫描的豁免必须逐条落在 `_FORBIDDEN_*` 表上（{exemptions[:3]}）")
    check({row["file"] for row in exemptions}
          == {"document_structure/table_classification.py"},
          f"豁免只应出现在声明负向表的那个模块（{[r['file'] for r in exemptions]}）")

    # 9) 全量派生入口：输入只有"声明 + 真实样本"，且钉死来源、拒绝自报。
    samples = _derivation_stub_samples()
    check({s["document_key"] for s in samples} == set(declared_keys),
          "补齐读视图必须逐个覆盖声明里的 document_key（缺一个即 fail-closed）")
    rows = RR.derive_required_sample_verdicts(samples)
    check(len(rows) == len(declared)
          and all(r["verdict_source"] == "derived_from_formal_artifacts"
                  and r["self_reported_verdict_accepted"] is False
                  for r in rows),
          "每条样本裁决都必须标注派生来源且声明不接受自报")
    # 合成样本里只有一张 page 1 的表：不依赖页面的文档级不变量那条应当成立，其余
    # 九条（都按冻结页码/结构定位）必须如实 fail——这就是"声明要求 vs 真实产物"。
    fixture_key = "FIXTURE_BOND_2026__non_300750_ts4"
    by_key = {(r["document_key"], r["requirement_kind"]): r for r in rows}
    fixture_row = by_key.get((fixture_key, "document_invariants"))
    check(fixture_row is not None and fixture_row["status"] == "pass",
          f"文档级不变量在合成样本上必须成立"
          f"（{None if fixture_row is None else fixture_row['failure_reasons']}）")
    page_bound = [r for r in rows if r["requirement_kind"] !=
                  "document_invariants"]
    check(len(page_bound) == 9 and all(r["status"] == "fail"
                                      for r in page_bound),
          f"按冻结页码定位的九条在合成样本上必须全部 fail，不得被凑成通过"
          f"（{[r['status'] for r in page_bound]}）")
    # 自报字段被改成"全绿"也不得改变任何一条裁决（派生器读不到它们）。
    louder = []
    for sample in samples:
        loud = dict(sample)
        loud.update({"passed": True, "status": "pass", "verdict": "pass",
                     "verdict_source": "self_reported",
                     "self_reported_verdict_accepted": True})
        louder.append(loud)
    echo = RR.derive_required_sample_verdicts(louder)
    check([(r["status"], r["failure_reasons"]) for r in echo]
          == [(r["status"], r["failure_reasons"]) for r in rows],
          "输入里自报 `status=pass` 不得改变任何一条派生裁决")

    # 10) §八(7)：被截断的关键行 / 单元格 / 注释不得作为必需样本通过对象。
    #     闸门是**状态原因**：三条表示"文本域未被完整证成"的原因各自屏蔽一批要求元素，
    #     未登记的原因（合成测试用的 `synthetic_field_not_proven` 等）不受影响——因此
    #     上面第 4/5 条的"可辩护缺席"判据没有被这条闸门顺手改掉。
    vocab = set(RR.REQUIRED_ELEMENT_VOCABULARY)
    for reason, blocked in sorted(RR._TRUNCATION_UNPROVEN_ELEMENTS.items()):
        check(bool(blocked) and blocked <= vocab,
              f"截断原因 {reason!r} 屏蔽的元素必须全部登记在要求元素词表内"
              f"（越界 {sorted(blocked - vocab)}）")
    check(set(RR.CONSERVATION_TRUNCATION_REASONS) == set(
              RR._TRUNCATION_UNPROVEN_ELEMENTS),
          "被闸门认作「截断」的状态原因必须恰为登记表里的那一组"
          f"（实得 {sorted(RR._TRUNCATION_UNPROVEN_ELEMENTS)}）")
    check(all(RR._table_element_observed(table, element)
              for element in ("body", "header")),
          "对照：完整对象上这些元素确实观测为真（否则下面的拒绝是空转）")
    for reason, element in (("row_boundary_truncated", "body"),
                            ("row_boundary_truncated", "header"),
                            ("cell_provenance_incomplete", "note"),
                            ("cell_provenance_incomplete", "body")):
        truncated = _rebuild(table, structure_state="partial",
                             structure_state_reason=reason)
        check(not RR._table_element_observed(truncated, element),
              f"状态原因 {reason!r} 下不得把 {element!r} 判成已观测"
              f"（被截断的行 / 单元格 / 注释不得冒充已证明存在）")
    # 闸门按**原因**开合，不是"只要 partial 就全否"：未登记的原因不改变任何元素判据，
    # 否则第 4/5 条的"对象自认 + 台账有痕 ⇒ 可辩护缺席"会被这条闸门顺手改掉。
    unrelated = _rebuild(table, structure_state="partial",
                         structure_state_reason="synthetic_field_not_proven")
    check(RR._table_element_observed(unrelated, "body"),
          "未登记的 partial 原因不得屏蔽任何元素（闸门键在原因上）")
    row = RR._derive_table_object_verdict(
        _decl(required_elements=("body",)), _sample(_final((truncated,))))
    check(row["status"] == "fail" and row["failure_reasons"] == [
        f"required_element_absent_and_has_no_gap_code:body:"
        f"{truncated.table_id}"],
        f"被截断的对象不得作为必需样本通过（{row['failure_reasons']}）")


# ---------------------------------------------------------------------------
# A8. `trm-2`：拒发契约的两种原因、双轴分离、拒发仍产全产物与 `trm-1` 历史读回
# ---------------------------------------------------------------------------

#: 三份真实 PDF 中最小、且**守恒资格不成立**（实测未解释残余）的那一份。它是最省的
#: 真实"纯守恒拒发"样本，因此 A8 用它当唯一真实读数，不用合成替身。
_A8_REFUSED_DOCUMENT = "NDSD_KCZ_2026"
_A8_RUN_ID = "tree_table_ts5_a8_probe"
#: 探针不自造时间戳：固定字面量让同一份载荷可逐字节复算（`trm-2` 只要求非空字符串）。
_A8_GENERATED_AT = "2026-09-19T00:00:00Z"


def _a8_refusal_contract() -> None:
    """§19.14 `trm-2` 的**必要**测试（逐条对应裁决给的契约边界）。

    1. 纯守恒拒发：原因落在封闭词表里，且**不带**任何阻断性缺口种类；
    2. 结构缺口拒发：`refusal_blocking_kinds` 非空**当且仅当**原因含
       `blocking_structure_gap`——两个方向都试（合成载荷，不建第二份真实文档）；
    3. `document_blocked` 是 capability 轴：拒发 ⇒ True，与结构缺口轴
       （`blocking_gap_count` / 上游根的 refusal 字段）互不冒充；
    4. 合法拒发**仍然产全**全部机器产物，落盘后索引可独立核验；
    5. `trm-1` 载荷：current reader 一律 fail-closed，显式历史入口可读回。
    """
    inputs = RR.bind_inputs()
    sample = RR._decorate([RR.build_document(
        inputs=inputs, document_key=_A8_REFUSED_DOCUMENT)])[0]
    key = sample["document_key"]
    order = [key]

    # --- 1. 纯守恒拒发 ------------------------------------------------
    check(sample["verdict"] == "refused" and sample["wrapper"] is None,
          f"{key} 是守恒拒发的真实样本（verdict={sample['verdict']!r}）")
    check(sample["refusal_reason_codes"]
          == [RR.REFUSAL_FINAL_CONSERVATION_INELIGIBLE]
          and sample["refusal_blocking_kinds"] == []
          and sample["refusal_reason"]
          == RR.REFUSAL_FINAL_CONSERVATION_INELIGIBLE,
          f"纯守恒拒发的原因落在封闭词表里、且**不带**阻断性缺口种类"
          f"（reasons={sample['refusal_reason_codes']}，"
          f"kinds={sample['refusal_blocking_kinds']}）")
    check((RR.REFUSAL_BLOCKING_STRUCTURE_GAP,
           RR.REFUSAL_FINAL_CONSERVATION_INELIGIBLE)
          == tuple(SCH.REFUSAL_REASON_CODES),
          f"runner 侧的拒发词表常量与 schema 的 {SCH.REFUSAL_REASON_CODES} "
          "逐项一致（不得各写一份）")
    check(tuple(SCH.VERIFIER_VERDICTS) == ("issued", "refused"),
          "verdict 仍恰为两支——合法拒发没有被改成第三种 verdict，"
          "也没有被改成运行级异常")
    mismatch = {(m["field"], m["declared"], m["observed"])
                for m in sample["pin_mismatches"]}
    check(("document_blocked", False, True) in mismatch,
          f"信任锚里那份 `expected_document_blocked=False` 与实测 True 的差异被"
          f"**如实记为失败的机器门**（不是让 run 消失、也不是改写 pin）：{sorted(mismatch)}")

    # `run_id_source` 与 `run_dir_relpath` 都按**生产口径**给：run 目录必须是
    # create-only 前缀 + 以本 run_id 结尾的规范相对路径，这两条是 `trm-2` 的形状门，
    # 探针不得靠放宽它们来通过。
    manifest = RR.build_run_manifest(
        inputs=inputs, samples=[sample], run_id=_A8_RUN_ID,
        run_id_source="explicit",
        run_dir_relpath=f"evaluation/results/{SCH.RUN_DIR_PREFIX}{_A8_RUN_ID}",
        generated_at=_A8_GENERATED_AT)
    root = manifest["upstream_roots"][0]
    doc = manifest["documents"][0]
    accepted, err = _manifest_accepted(manifest, order)
    check(accepted,
          f"拒发样本的 run_manifest 在 `trm-2` 下仍然合法：{err}")

    # --- 3. 双轴分离 --------------------------------------------------
    check(doc["capability_issued"] is False and doc["document_blocked"] is True
          and root["refusal_reason_codes"]
          == [RR.REFUSAL_FINAL_CONSERVATION_INELIGIBLE]
          and root["refusal_blocking_kinds"] == []
          and doc["blocking_gap_count"]
          == sample["final"].blocking_gap_count,
          f"capability 轴（拒发 ⇒ `document_blocked=True`）与结构缺口轴"
          f"（无阻断种类、`blocking_gap_count={doc['blocking_gap_count']}` "
          f"如实照记）分列两处，互不冒充")
    check(manifest["expected_blocking_summary"]
          == {"documents_blocked": 1, "documents_capability_issued": 0,
              "total_blocking_gaps": doc["blocking_gap_count"]},
          f"拒发摘要与逐文档记录逐项一致（拒发不计成签发）："
          f"{manifest['expected_blocking_summary']}")

    # --- 2. 两种拒发原因的**充要**关系（合成载荷，逐条反例） ----------
    blocking_kind = SCH.BLOCKING_GAP_KINDS[0]

    def _root(**over):
        return {**root, **over}

    def _accepted(payload_root) -> tuple:
        return _manifest_accepted(
            {**manifest, "upstream_roots": [payload_root]}, order)

    ok, err = _accepted(_root(
        refusal_reason_codes=[RR.REFUSAL_BLOCKING_STRUCTURE_GAP],
        refusal_blocking_kinds=[blocking_kind],
        capability_issued=False))
    check(ok, f"结构缺口拒发（原因 + 阻断种类齐备）必须被接受：{err}")
    bad = [
        (_root(refusal_reason_codes=[RR.REFUSAL_BLOCKING_STRUCTURE_GAP],
               refusal_blocking_kinds=[], capability_issued=False),
         "非空当且仅当",
         "原因含 `blocking_structure_gap` 却不给阻断种类即被拒"),
        (_root(refusal_reason_codes=[RR.REFUSAL_FINAL_CONSERVATION_INELIGIBLE],
               refusal_blocking_kinds=[blocking_kind], capability_issued=False),
         "非空当且仅当",
         "纯守恒拒发却带上阻断种类即被拒（两轴不得互相冒充）"),
        (_root(refusal_reason_codes=[], refusal_blocking_kinds=[],
               capability_issued=False),
         "必须给出至少一个 refusal_reason_codes",
         "拒发却不给原因即被拒（不得退化成无声拒发）"),
        (_root(refusal_reason_codes=["not_a_registered_reason"],
               refusal_blocking_kinds=[], capability_issued=False),
         "必须属于",
         "拒发原因越出封闭词表即被拒"),
        (_root(refusal_reason_codes=[RR.REFUSAL_FINAL_CONSERVATION_INELIGIBLE],
               refusal_blocking_kinds=[], capability_issued=False,
               verifier_verification_fingerprint=_SHA_A),
         "不得给出 verifier_verification_fingerprint",
         "拒发不得同时自报签发指纹即被拒"),
    ]
    for payload_root, substr, msg in bad:
        accepted, err = _accepted(payload_root)
        check(not accepted and substr in (err or ""), f"{msg}（实得 {err!r}）")
    # 签发侧：`issued` 不得带任何拒发原因 / 阻断种类（词表扩了，签发口径不变）。
    issued_root = _root(verifier_verdict="issued", capability_issued=True,
                        refusal_reason_codes=[], refusal_blocking_kinds=[],
                        verifier_verification_fingerprint=_SHA_A)
    ok, err = _accepted(issued_root)
    check(ok, f"签发根（原因与阻断种类皆空）必须被接受：{err}")
    accepted, err = _accepted({**issued_root, "refusal_reason_codes": [
        RR.REFUSAL_FINAL_CONSERVATION_INELIGIBLE]})
    check(not accepted and "必须为空" in (err or ""),
          f"签发却带拒发原因即被拒（{err!r}）")

    # --- 3b. `document_blocked` 的两条反例（文档级） -------------------
    for over, substr, msg in (
            ({"document_blocked": False}, "与 document_blocked=",
             "拒发文档记 `document_blocked=False` 即被拒（capability 轴不得自报）"),
            ({"capability_issued": True}, "与 capability_issued=",
             "拒发文档记 `capability_issued=True` 即被拒")):
        payload = {**manifest, "documents": [{**doc, **over}]}
        accepted, err = _manifest_accepted(payload, order)
        check(not accepted and substr in (err or ""), f"{msg}（实得 {err!r}）")

    # --- 4. 合法拒发仍然产全产物 --------------------------------------
    with tempfile.TemporaryDirectory(prefix="ts5_a8_refused_") as tmp:
        tmp_dir = Path(tmp)
        relpath = f"{RR.MATRIX_SNAPSHOT_DIRNAME}/{key}.json"
        path = tmp_dir / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        RR._write_json(path, RR._snapshot_of(sample))
        gates = {"machine_gates_version": "tgm-1", "passed": False,
                 "failed_gate_count": 1,
                 "failed_gate_ids": [f"pinned_expectation.{key}"],
                 "required_sample_matrix": RR.derive_required_sample_verdicts(
                     _derivation_stub_samples()),
                 "note": "A8：只构建一份**守恒拒发**的真实文档，用于证明拒发同样"
                         "产全产物；固定样本裁决由派生器对补齐读视图重算。"}
        json_members = RR._json_members([sample], run_id=_A8_RUN_ID,
                                       results_dir=tmp_dir, machine_gates=gates)
        md_members = RR._md_members(
            [sample], matrix=json_members["table_acceptance_matrix.json"])
        jsonl_members = RR._jsonl_members([sample])
        for rel, payload in sorted(json_members.items()):
            RR._write_json(tmp_dir / rel, payload)
        for rel, text in sorted(md_members.items()):
            (tmp_dir / rel).write_text(text, encoding="utf-8")
        for rel, rows in sorted(jsonl_members.items()):
            RR._write_json_lines(tmp_dir / rel, rows)
        RR._write_json(tmp_dir / "run_manifest.json", manifest)
        RR._write_json(tmp_dir / "database_immutability.json",
                       {"database_immutability_version": "tdi-1",
                        "unchanged": True, "changed": [],
                        "watched_missing": []})
        (tmp_dir / "manual_review.md").write_text("probe\n", encoding="utf-8")
        RR._write_json(tmp_dir / "review_decision.json",
                       RR.build_review_template(
                           run_id=_A8_RUN_ID,
                           final_snapshot_ids=[sample["final"].snapshot_id],
                           machine_gates_passed=False))
        index = RR.build_machine_index(
            run_id=_A8_RUN_ID, run_dir_relpath="tmp/a8_probe", document_order=order,
            results_dir=tmp_dir)
        verify = RR.verify_machine_index(tmp_dir, index, order)
        check(verify["ok"] is True and not verify["missing"]
              and not verify["extra"] and not verify["hash_mismatch"],
              f"守恒拒发的 run 照样落盘**全部**机器产物、索引可独立核验"
              f"（missing={verify['missing'][:3]} extra={verify['extra'][:3]} "
              f"hash_mismatch={verify['hash_mismatch'][:3]}）")
        # 拒发不缩成员表：磁盘上的可索引文件恰为 `tai-1` 全集减去两个索引内部
        # 成员（索引不可能登记自己的 sha256），既没少也没多。
        check(len(index["files"]) == len(SCH.indexable_members(order))
              and set(index["members_expected"])
              == set(SCH.indexable_members(order))
              and set(SCH.expected_machine_members(order))
              - set(index["members_expected"])
              == set(SCH.INDEX_INTERNAL_MEMBERS),
              "拒发 run 的索引成员集合与 `tai-1` 全集一致（拒发不缩成员表）："
              f"files={len(index['files'])} "
              f"indexable={len(SCH.indexable_members(order))} "
              f"全集={len(SCH.expected_machine_members(order))}")
        decision = RR._read_json(tmp_dir / "review_decision.json",
                                 "A8 的 review 模板")
        check(SCH.derive_review_decision(decision) == "needs_changes",
              f"机器门未通过时 review_decision 必须保持 needs_changes"
              f"（实得 {SCH.derive_review_decision(decision)!r}）")
        check(not (tmp_dir / "review_attestation.json").exists(),
              "拒发 run 里**没有** seal 产物（实施方不得自行封存）")

    # --- 5. `trm-1` 历史读回 -------------------------------------------
    #
    # 历史载荷只用**旧契约能表达的那种**拒发（结构缺口 ⇒ 阻断种类非空）：`trm-1`
    # 根本无法表达"纯守恒拒发"，这也正是必须换轴的理由。声称"旧载荷里有纯守恒拒发"
    # 是伪造历史，因此这里不那样造。
    legacy = {**manifest,
              "run_manifest_schema_version":
                  SCH.LEGACY_RUN_MANIFEST_SCHEMA_VERSION,
              "trust_root_schema_version": SCH.LEGACY_TRUST_ROOT_SCHEMA_VERSION,
              "versions": dict(SCH.LEGACY_EVALUATION_VERSION_AXES),
              "upstream_roots": [
                  {k: v for k, v in {**root,
                                     "refusal_blocking_kinds": [blocking_kind]}
                   .items() if k != "refusal_reason_codes"}]}
    accepted, err = _manifest_accepted(legacy, order)
    check(not accepted and "版本漂移" in (err or ""),
          f"`trm-1` 载荷走 current reader 一律 fail-closed，且理由是**版本**而不是"
          f"形状（{err!r}）")
    hist = SCH.read_legacy_run_manifest(legacy, document_order=order)
    check(hist["status"] == "historical_only"
          and hist["schema_version"] == SCH.LEGACY_RUN_MANIFEST_SCHEMA_VERSION
          and hist["manifest"]["run_id"] == _A8_RUN_ID
          and "refusal_reason_codes"
          not in hist["manifest"]["upstream_roots"][0],
          "同一份 `trm-1` 载荷走**显式历史入口**可以读回，并标成 "
          "`historical_only`（按**当时的**字段表读，不补 current 字段）")
    check("守恒资格不成立" in hist["note"] and "不得" in hist["note"],
          f"历史读回的说明明确指出旧口径无法表达/区分纯守恒拒发、"
          f"旧结论不得当 current 结论（{hist['note'][:80]!r}…）")
    raises(lambda: SCH.read_legacy_run_manifest(manifest, document_order=order),
           SCH.AcceptanceSchemaError, "版本漂移",
           "历史入口反过来拒绝 current 载荷（两个方向都不静默）")


def _manifest_accepted(payload, order) -> tuple:
    """调用 current reader，返回 `(是否通过, 错误文本)`。"""
    try:
        SCH.validate_run_manifest(payload, document_order=order)
    except SCH.AcceptanceSchemaError as e:
        return False, str(e)
    return True, None


_GROUPS = (
    ("A1-version-axes", _a1_axes_and_self_check),
    ("A2-machine-index", _a2_machine_index),
    ("A3-review-schema", _a3_review_schema),
    ("A4-attestation", _a4_attestation),
    ("A5-runner-fail-closed", _a5_runner_fail_closed),
    ("A6-artifact-projections", _a6_artifact_projections),
    ("A7-required-sample-derivation", _a7_required_sample_derivation),
    ("A8-trm2-refusal-contract", _a8_refusal_contract),
)


def _run_group(name, fn) -> None:
    import time
    started = time.time()
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        import traceback
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {name} 分组异常：{type(e).__name__}: {e}\n"
            f"{traceback.format_exc()[-1200:]}")
    _results["details"].append(f"__{name}__ {time.time() - started:.1f}s")


def main() -> dict:
    import time
    started = time.time()
    previous_db_path = getattr(_estore, "_db_path", None)
    before = _identity_bundle()
    try:
        _estore._db_path = _EVIDENCE_DB
        for name, fn in _GROUPS:
            _run_group(name, fn)
    finally:
        _estore._db_path = previous_db_path
        after = _identity_bundle()
        check(before == after,
              "只读前置：`data/evidence.db` 与 `data/financial_v2.db`（含 -wal / -shm）"
              "在本模块前后逐项不变（size/mtime_ns/sha256）——包括 `execute_run` 的"
              "三条 fail-closed 路径与信任锚反例")
    _results["seconds"] = round(time.time() - started, 1)
    return _results


if __name__ == "__main__":
    _res = main()
    print(json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

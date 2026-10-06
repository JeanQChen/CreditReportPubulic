# -*- coding: utf-8 -*-
"""T33 runner 拒绝覆盖 + 机器产物索引核验（计划 §18.14 / §18.14.1）。

覆盖（§18.13 T33 行 + §18.14）：

- 普通运行目标目录已存在 → `raise SystemExit`，且**不创建任何文件**（不是"覆盖"、
  不是"另起编号"、不是"先建目录再报错"）；
- 唯一例外是 `--seal-review <既有 TS4-A 目录>`：不运行构建器、不改任何既有文件，
  只在完整复核后 create-once 写 `review_attestation.json`；目标不存在 / 已封存 /
  任一绑定不符（manifest 阶段、代码指纹、机器索引、根绑定）一律拒绝且**不留半成品**；
- `machine_artifact_index.json` 整份篡改（改一条 / 删一条 / 加一条 / 改文件本体 /
  改索引元数据）必须被逐文件重算发现，且 `machine_index_identity` 不可复现；
- `CODE_FINGERPRINT_FILES` 与计划 §18.14.1 **逐文件**对齐：无目录通配、无"全部测试"、
  TS4-A 只列 A 侧策略资产。

**不执行完整验收**：本文件只做参数 / 目录 / 拒绝路径与索引核验的单元级检查。所有
临时目录一律建在 `tempfile` 下，**绝不触碰 `evaluation/results/**`**。

运行器 `evaluation/run_tree_span_acceptance.py` 由并行轨道实现：若它**不存在**，所有
依赖它的断言组会记录一条**显式 SKIP（含原因）**而不是让本文件导入失败；不依赖运行器
的断言（计划侧 `CODE_FINGERPRINT_FILES` 口径、`evaluation/results` 只读自证）照常执行。
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import io
import json
import pathlib
import re
import shutil
import sys
import tempfile
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from document_structure import versions as V  # noqa: E402
from evals import tree_stage_env as STAGE  # noqa: E402

RUNNER_RELPATH = "evaluation/run_tree_span_acceptance.py"
PLAN_RELPATH = "TREE_STRUCTURE_IMPLEMENTATION_PLAN.md"
RESULTS_ROOT = REPO / "evaluation" / "results"
FIXTURE_DIR_RELPATH = "evals/fixtures/tree_structure/non_300750_ts4"
TRUST_ROOT_RELPATH = "evals/fixtures/tree_structure/ts4_trust_roots.json"
EVIDENCE_DB_RELPATH = "data/evidence.db"

#: §18.14.1 明确**只属于 TS4-B** 的固定策略资产：A 批不得预先绑定。
TS4_B_ONLY_POLICIES = (
    "document_structure/policies/span_qualification_approval_v1.json",
    "document_structure/policies/span_qualification_frozen_v1.json",
)
#: §18.14.1 对 TS4-A 的必列项（逐条断言，缺一即 FAIL）。
MANDATED_TS4_A_FILES = (
    "document_structure/__init__.py",
    "document_structure/versions.py",
    "document_structure/canonical.py",
    "document_structure/schema.py",
    "document_structure/outline_builder.py",
    "document_structure/layout_builder.py",
    "document_structure/aligner.py",
    "document_structure/evidence_gateway.py",
    "document_structure/span_schema.py",
    "document_structure/span_policy.py",
    "document_structure/span_builder.py",
    "document_structure/span_verifier.py",
    "document_structure/synopsis.py",
    "evidence/store.py",
    "sections/service.py",
    RUNNER_RELPATH,
    "evals/run_evals.py",
    "evals/test_tree_span_schema.py",
    "evals/test_tree_span_input.py",
    "evals/test_tree_span_coords.py",
    "evals/test_tree_span_builder.py",
    "evals/test_tree_span_verifier.py",
    "evals/test_tree_synopsis.py",
    "evals/test_tree_span_conservation.py",
    "evals/test_tree_span_artifacts.py",
    "evals/test_tree_span_fixture.py",
    "evals/test_tree_span_completion.py",
    "evals/test_tree_span_formal_chain.py",
    "evals/test_tree_page_layout.py",
    "evals/test_tree_structure_schema.py",
    "evals/test_tree_structure_adversarial.py",
    TRUST_ROOT_RELPATH,
    FIXTURE_DIR_RELPATH + "/manifest.json",
    "document_structure/policies/registry_v1.json",
    "document_structure/policies/span_qualification_distribution_v1.json",
)

_results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

# 运行器由并行轨道实现：缺失只记 SKIP，绝不因此让本文件导入失败。
RUNNER = None
_RUNNER_IMPORT: tuple[str, str] | None = None
try:
    from evaluation import run_tree_span_acceptance as RUNNER  # noqa: PLC0415
except ModuleNotFoundError as error:  # 运行器尚未实现
    _RUNNER_IMPORT = ("absent", f"{type(error).__name__}: {error}")
except Exception as error:  # noqa: BLE001  运行器存在但坏了 —— 这是真缺陷
    _RUNNER_IMPORT = ("broken", f"{type(error).__name__}: {error}")


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def skip(msg):
    _results["skipped"] += 1
    _results["details"].append("SKIP " + msg)


def raises(fn, exc, substr, msg):
    try:
        fn()
    except exc as error:
        text = str(error)
        if substr == "" or substr in text:
            return check(True, msg)
        return check(False, f"{msg} —— 异常信息不含 {substr!r}：{text!r}")
    except Exception as error:  # noqa: BLE001
        return check(False, f"{msg} —— 抛出 {type(error).__name__} 而非 "
                            f"{exc.__name__}：{error}")
    return check(False, f"{msg} —— 未抛出 {exc.__name__}")


@contextlib.contextmanager
def _tmpdir(prefix="ts4_artifacts_"):
    directory = tempfile.mkdtemp(prefix=prefix)
    try:
        yield pathlib.Path(directory)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _tree_snapshot(root: pathlib.Path) -> dict:
    """目录树快照：相对路径 → {size, mtime_ns, sha256} / 目录标记。"""
    out: dict = {}
    root = pathlib.Path(root)
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_file():
            stat = path.stat()
            out[rel] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                        "sha256": _sha256(path)}
        elif path.is_dir():
            out[rel + "/"] = {"dir": True}
    return out


def _watched_snapshot() -> dict:
    """本模块**不得改动**的既有路径（结果根 + 证据库 + 冻结 run 根）。"""
    watched = {"evaluation/results": _tree_snapshot(RESULTS_ROOT)}
    for rel in (EVIDENCE_DB_RELPATH, "data/financial_v2.db", TRUST_ROOT_RELPATH):
        path = REPO / rel
        if not path.exists():
            watched[rel] = {"missing": True}
            continue
        stat = path.stat()
        watched[rel] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                        "sha256": _sha256(path)}
    return watched


def _diff(before: dict, after: dict) -> list:
    changed = [key for key in sorted(set(before) | set(after))
               if before.get(key) != after.get(key)]
    return changed


def _runner_gate(group: str) -> bool:
    """运行器可用性闸：缺失 → SKIP（含原因）；存在但坏 → FAIL。"""
    if RUNNER is not None:
        return True
    kind, error = _RUNNER_IMPORT or ("absent", "未知导入失败")
    if kind == "absent":
        skip(f"{group} —— 跳过：{RUNNER_RELPATH} 尚未实现（并行轨道），本组全部"
             f"断言未执行；原因={error}")
    else:
        check(False, f"{group} —— {RUNNER_RELPATH} 存在但无法导入：{error}")
    return False


def _capture(fn, *args, **kwargs) -> tuple:
    """执行并捕获 stdout：返回 `(返回值, 输出文本)`。"""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        value = fn(*args, **kwargs)
    return value, buffer.getvalue()


def _main_outcome(argv: list) -> tuple:
    """调用 `_main`：返回 `("exit", 文本)` / `("return", 返回值)` / `("raise", 文本)`。"""
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            value = RUNNER._main(argv)
    except SystemExit as error:
        return "exit", (str(error.code) if error.code else "") + buffer.getvalue()
    except BaseException as error:  # noqa: BLE001
        return "raise", f"{type(error).__name__}: {error}{buffer.getvalue()}"
    return "return", value


def _fabricate_run_dir(target: pathlib.Path, *, stage: str,
                       code_fingerprint=None) -> None:
    """在**临时目录**里伪造一份"形状完整"的 A 产物目录（只用于拒绝路径）。"""
    target.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_type": "TS4RunManifest", "stage": stage,
                "run_id": target.name, "generated_at_utc": "2026-09-18T00:00:00Z"}
    if code_fingerprint is not None:
        manifest["code_fingerprint"] = code_fingerprint
    RUNNER._write_json(target / "run_manifest.json", manifest)
    for name in _seal_required_files():
        path = target / name
        if path.exists():
            continue
        if name.endswith(".md"):
            path.write_text("# 人工验收清单（临时副本）\n", encoding="utf-8")
        else:
            RUNNER._write_json(path, {"placeholder": name})


#: 若无法从源码解析出必产物清单，退回到本清单（只用于造"形状完整"的临时拒绝夹具，
#: 不参与任何生产断言；若 runner 的真清单变长，伪造目录会被判"缺件"，测试会以
#: 断言失败而不是静默通过的方式暴露差异）。
_SEAL_REQUIRED_FALLBACK = (
    "run_manifest.json", "machine_artifact_index.json",
    "confidence_distribution.json", "span_snapshot_aggregate.json",
    "span_snapshot_index.json", "policy_decision.json", "manual_review.md",
)


def _seal_required_files() -> tuple:
    """从 `seal_review` 源码里取「必须存在」的产物名（不硬编码，避免抄错）。"""
    try:
        source = inspect_source(RUNNER.seal_review)
        match = re.search(r"for name in \((.*?)\):", source, re.S)
        if match is not None:
            parsed = tuple(re.findall(r'"([^"]+)"', match.group(1)))
            if len(parsed) >= 6:
                return parsed
    except Exception:  # noqa: BLE001  源码解析不是被测对象，失败即退回
        pass
    return _SEAL_REQUIRED_FALLBACK


def inspect_source(obj) -> str:
    import inspect  # noqa: PLC0415  仅此处需要

    return inspect.getsource(obj)


# ---------------------------------------------------------------------------
# T33-A 普通运行：目标目录已存在必须拒绝覆盖，且不创建任何文件
# ---------------------------------------------------------------------------

def _test_t33a_run_dir_refusal() -> None:
    if not _runner_gate("T33-A 结果目录拒绝覆盖"):
        return
    for label, create_dir in (("非空既有目录", True), ("空既有目录", False)):
        with _tmpdir() as tmp:
            root = tmp / "results"
            root.mkdir()
            target = root / "ts4a_dup_run"
            target.mkdir()
            if create_dir:
                (target / "run_manifest.json").write_text(
                    '{"stage": "TS4-A"}\n', encoding="utf-8")
            before = _tree_snapshot(root)
            built: list = []

            def _forbid(*_args, **_kwargs):
                built.append("execute_run")
                raise AssertionError("拒绝覆盖失效：竟然进入了构建阶段")

            with mock.patch.object(RUNNER, "execute_run", _forbid):
                kind, payload = _main_outcome(
                    ["--results-root", str(root), "--run-id", target.name])

            check(kind == "exit" and "拒绝覆盖历史结果" in str(payload)
                  and target.name in str(payload),
                  f"T33-A {label}：`_main` 必须 `raise SystemExit('…拒绝覆盖历史结果…')`，"
                  f"得到 {kind}={payload!r}")
            check(built == [],
                  f"T33-A {label}：拒绝必须发生在任何构建之前（execute_run 未被调用）")
            after = _tree_snapshot(root)
            check(before == after,
                  f"T33-A {label}：拒绝时不得创建 / 删除 / 改写任何文件，差异="
                  f"{_diff(before, after)[:5]}")
            check(sorted(after) == ["ts4a_dup_run/"]
                  + (["ts4a_dup_run/run_manifest.json"] if create_dir else []),
                  f"T33-A {label}：结果根下不得多出任何占位目录或临时文件，实际="
                  f"{sorted(after)}")


def _test_t33a_run_dir_default_name() -> None:
    """默认 run_id 形如 `tree_span_ts4_<stage>_<UTC>`，且同样受"已存在即拒绝"约束。"""
    if not _runner_gate("T33-A 默认 run_id 形态与拒绝"):
        return
    with _tmpdir() as tmp:
        root = tmp / "results"
        root.mkdir()
        now = datetime.datetime.now(datetime.timezone.utc)
        # 覆盖 ±3 秒窗口：无论 runner 在秒边界的哪一侧取时刻，默认 run_id 都会命中。
        candidates: set = set()
        for token in ("ts4a", "ts4b"):
            for offset in range(-3, 4):
                moment = now + datetime.timedelta(seconds=offset)
                candidates.add(moment.strftime(
                    f"tree_span_ts4_{token}_%Y%m%dT%H%M%SZ"))
        for name in candidates:
            (root / name).mkdir()
        check("tree_span_ts4_ts4a_" in "".join(sorted(candidates)),
              "T33-A 默认 run_id 形态为 tree_span_ts4_<stage>_<UTC>（已据此造出碰撞"
              "目录用于拒绝测试）")
        before = _tree_snapshot(root)
        # 哨兵返回固定值：万一真的走到构建也**不会**执行任何验收（并可在返回值里
        # 被认出来）。
        with mock.patch.object(RUNNER, "execute_run",
                               lambda *_a, **_k: _SENTINEL_RC):
            kind, payload = _main_outcome(["--results-root", str(root)])
        if kind == "exit":
            check("拒绝覆盖历史结果" in str(payload),
                  f"T33-A 默认 run_id 命中既有目录时必须拒绝覆盖，得到 {payload!r}")
        else:
            check(kind == "return" and payload == _SENTINEL_RC,
                  f"T33-A 默认 run_id 未命中既有目录时不得改写既有目录，且必须被哨兵"
                  f"拦住（期望 {kind}='return'/payload={_SENTINEL_RC}），得到 "
                  f"{kind}={payload!r}")
        check(before == _tree_snapshot(root),
              "T33-A 默认 run_id 路径不得改写既有结果目录")


_SENTINEL_RC = 424242


# ---------------------------------------------------------------------------
# T33-B --seal-review：拒绝路径不留半成品，且不改任何既有文件
# ---------------------------------------------------------------------------

def _test_t33b_seal_review_nonexistent_and_incomplete() -> None:
    if not _runner_gate("T33-B seal-review 目标不存在 / 产物不全"):
        return
    with _tmpdir() as tmp:
        parent = tmp / "results"
        parent.mkdir()
        missing_target = parent / "no_such_run"
        before = _tree_snapshot(parent)
        rc, text = _capture(RUNNER._main, ["--seal-review", str(missing_target)])
        check(rc == 2 and "目标目录不存在" in text,
              f"T33-B 目标不存在必须拒绝（返回 2 且说明原因），得到 {rc}/{text!r}")
        check(not missing_target.exists() and _tree_snapshot(parent) == before,
              "T33-B 目标不存在时不得顺手创建目录或任何文件")
        check(not (missing_target / "review_attestation.json").exists(),
              "T33-B 目标不存在时不得留下 attestation")

        incomplete = parent / "incomplete_run"
        incomplete.mkdir()
        RUNNER._write_json(incomplete / "run_manifest.json",
                           {"stage": "TS4-A", "run_id": "incomplete_run"})
        before = _tree_snapshot(incomplete)
        rc, text = _capture(RUNNER._main, ["--seal-review", str(incomplete)])
        check(rc == 2 and ("缺" in text or "不是完整 TS4-A 产物" in text),
              f"T33-B 产物不全必须拒绝（返回 2 且指出缺件），得到 {rc}/{text!r}")
        check(_tree_snapshot(incomplete) == before,
              "T33-B 产物不全时不得改写 / 新增任何文件")
        check(not (incomplete / "review_attestation.json").exists(),
              "T33-B 产物不全时不得写 attestation（无半成品）")


def _test_t33b_seal_review_binding_mismatches() -> None:
    if not _runner_gate("T33-B seal-review 绑定不符"):
        return
    required = _seal_required_files()
    check(len(required) >= 6,
          f"T33-B 拒绝夹具所需的必产物清单可用（{len(required)} 项：{required}）")
    with _tmpdir() as tmp:
        # (c) 阶段不符：manifest.stage 不是 TS4-A。
        stage_dir = tmp / "stage_b_run"
        _fabricate_run_dir(stage_dir, stage="TS4-B")
        before = _tree_snapshot(stage_dir)
        rc, text = _capture(RUNNER._main, ["--seal-review", str(stage_dir)])
        check(rc == 2 and "stage 不是 TS4-A" in text,
              f"T33-B 非 TS4-A 目录不得被当 A 封存，得到 {rc}/{text!r}")
        check(_tree_snapshot(stage_dir) == before
              and not (stage_dir / "review_attestation.json").exists(),
              "T33-B 阶段不符时不得改写任何文件、不得留 attestation")

        # (d) 代码指纹不符：当前代码与 A run 不一致 → 拒绝，且**不得**进入绑定。
        code_dir = tmp / "code_mismatch_run"
        _fabricate_run_dir(code_dir, stage="TS4-A",
                           code_fingerprint={"fingerprint": "0" * 64,
                                             "files": {}, "missing": []})
        before = _tree_snapshot(code_dir)
        built: list = []

        def _spy_bind_all(*_args, **_kwargs):
            built.append("bind_all")
            raise AssertionError("哨兵：拒绝路径不得执行绑定 / 构建")

        with mock.patch.object(RUNNER, "bind_all", _spy_bind_all):
            rc, text = _capture(RUNNER._main, ["--seal-review", str(code_dir)])
        check(rc == 2 and "代码指纹" in text,
              f"T33-B 代码指纹与 A run 不符必须拒绝（不得用另一版代码复核旧产物），"
              f"得到 {rc}/{text!r}")
        check(built == [] and _tree_snapshot(code_dir) == before
              and not (code_dir / "review_attestation.json").exists(),
              "T33-B 代码指纹不符时必须在此之前拒绝：不绑定、不改文件、不留 attestation")

        # (e) 索引不符：代码指纹对得上，但机器索引被改动 → 仍然拒绝。
        index_dir = tmp / "index_mismatch_run"
        _fabricate_run_dir(index_dir, stage="TS4-A",
                           code_fingerprint=RUNNER.code_fingerprint())
        index = RUNNER.build_machine_index(
            results_dir=index_dir, run_id=index_dir.name,
            run_id_source="caller_supplied", generated_at="2026-09-18T00:00:00Z",
            stage="TS4-A", code=RUNNER.code_fingerprint(), versions={"v": 1},
            inputs={"i": 1}, root_identity={"r": 1}, policy_identity={"p": 1})
        tampered = json.loads(json.dumps(index))
        tampered["files"][0]["sha256"] = "0" * 64
        RUNNER._write_json(index_dir / "machine_artifact_index.json", tampered)
        before = _tree_snapshot(index_dir)
        built = []
        with mock.patch.object(RUNNER, "bind_all", _spy_bind_all):
            rc, text = _capture(RUNNER._main, ["--seal-review", str(index_dir)])
        check(rc == 2 and "机器索引核验失败" in text,
              f"T33-B 机器索引被改动必须拒绝封存，得到 {rc}/{text!r}")
        check(built == [] and _tree_snapshot(index_dir) == before
              and not (index_dir / "review_attestation.json").exists(),
              "T33-B 索引不符时必须在此之前拒绝：不绑定、不改文件、不留 attestation")


def _test_t33b_seal_review_already_sealed() -> None:
    if not _runner_gate("T33-B seal-review 已封存不得重复"):
        return
    with _tmpdir() as tmp:
        sealed = tmp / "sealed_run"
        _fabricate_run_dir(sealed, stage="TS4-A")
        attestation = sealed / "review_attestation.json"
        RUNNER._write_json(attestation, {"schema_type": "ReviewAttestation",
                                         "run_id": sealed.name,
                                         "sealed_at_utc": "2026-09-18T00:00:00Z"})
        before = _tree_snapshot(sealed)
        rc, text = _capture(RUNNER._main, ["--seal-review", str(sealed)])
        check(rc == 2 and "已封存" in text,
              f"T33-B 已封存目录必须拒绝重复 seal / 覆盖旧文件，得到 {rc}/{text!r}")
        check(_tree_snapshot(sealed) == before,
              f"T33-B 重复 seal 不得改写既有 attestation 与任何其他文件，差异="
              f"{_diff(before, _tree_snapshot(sealed))[:5]}")


def _test_t33b_seal_review_never_builds() -> None:
    """`--seal-review` 的全部拒绝路径都不得进入构建（`execute_run` 与 `bind_all`）。"""
    if not _runner_gate("T33-B seal-review 不运行构建器"):
        return
    calls: list = []

    def _spy(name):
        def _inner(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"哨兵：--seal-review 不得调用 {name}")
        return _inner

    with _tmpdir() as tmp:
        target = tmp / "whatever_run"
        with mock.patch.object(RUNNER, "execute_run", _spy("execute_run")), \
                mock.patch.object(RUNNER, "bind_all", _spy("bind_all")):
            rc, text = _capture(RUNNER._main, ["--seal-review", str(target)])
    check(calls == [] and rc == 2,
          f"T33-B 目标不存在时不得调用 execute_run / bind_all（得到 {calls}），"
          f"并且必须拒绝（得到 {rc}/{text!r}）")


# ---------------------------------------------------------------------------
# T33-C 机器产物索引：整份篡改必须被发现
# ---------------------------------------------------------------------------

def _build_temp_index(root: pathlib.Path) -> tuple:
    root.mkdir(parents=True, exist_ok=True)
    (root / "a.json").write_text('{"a": 1}\n', encoding="utf-8")
    (root / "b.txt").write_text("b\n", encoding="utf-8")
    (root / "nested").mkdir(exist_ok=True)
    (root / "nested" / "c.json").write_text('{"c": 3}\n', encoding="utf-8")
    index = RUNNER.build_machine_index(
        results_dir=root, run_id="tmp_run", run_id_source="caller_supplied",
        generated_at="2026-09-18T00:00:00Z", stage="TS4-A",
        code=RUNNER.code_fingerprint(), versions={"schema": 1},
        inputs={"frozen": "x"}, root_identity={"pdf": "y"},
        policy_identity={"policy": "z"})
    return index


def _test_t33c_machine_index_tamper() -> None:
    if not _runner_gate("T33-C 机器索引整份篡改"):
        return
    with _tmpdir() as tmp:
        root = tmp / "run"
        index = _build_temp_index(root)
        listed = {entry["path"] for entry in index["files"]}
        check(listed == {"a.json", "b.txt", "nested/c.json"},
              f"T33-C 索引逐文件登记（含子目录），实际 {sorted(listed)}")
        check("run_manifest.json" not in listed,
              "T33-C 未创建的 run_manifest.json 不得凭空登记")

        baseline = RUNNER.verify_machine_index(root, index)
        check(baseline["ok"] is True and baseline["problems"] == [],
              f"T33-C 未篡改的索引必须核验通过（作为反例的基座），得到 {baseline}")

        # 改一条：某文件的 sha256 被改写。
        tampered = json.loads(json.dumps(index))
        tampered["files"][0]["sha256"] = "0" * 64
        report = RUNNER.verify_machine_index(root, tampered)
        check(report["ok"] is False
              and any("SHA256 不符" in p for p in report["problems"]),
              f"T33-C 改一条 sha256 必须被发现，得到 {report['problems'][:3]}")

        # 删一条：登记项被删掉（文件仍在盘上）。
        tampered = json.loads(json.dumps(index))
        dropped = tampered["files"].pop()["path"]
        report = RUNNER.verify_machine_index(root, tampered)
        check(report["ok"] is False
              and any("未登记的文件" in p for p in report["problems"]),
              f"T33-C 删一条登记（{dropped}）必须被发现，得到 {report['problems'][:3]}")

        # 加一条：登记一个盘上不存在的文件。
        tampered = json.loads(json.dumps(index))
        tampered["files"].append({"path": "ghost.json", "size": 1,
                                 "sha256": "0" * 64})
        report = RUNNER.verify_machine_index(root, tampered)
        check(report["ok"] is False
              and any("文件缺失" in p for p in report["problems"]),
              f"T33-C 加一条幽灵登记必须被发现，得到 {report['problems'][:3]}")

        # 改文件本体（索引不动）：内容变化必须被发现。
        (root / "a.json").write_text('{"a": 2}\n', encoding="utf-8")
        report = RUNNER.verify_machine_index(root, index)
        check(report["ok"] is False
              and any("a.json" in p for p in report["problems"]),
              f"T33-C 只改产物文件本体也必须被发现，得到 {report['problems'][:3]}")

        # 改索引元数据但不动 files：`machine_index_identity` 不可复现。
        tampered = json.loads(json.dumps(index))
        tampered["stage"] = "TS4-B"
        tampered["generated_at_utc"] = "2026-09-19T00:00:00Z"
        report = RUNNER.verify_machine_index(root, tampered)
        check(report["ok"] is False
              and any("machine_index_identity 不可复现" in p
                      for p in report["problems"]),
              f"T33-C 索引元数据被改动（files 未动）必须因身份不可复现而被发现，得到 "
              f"{report['problems'][:3]}")

        # 复原被改动过的产物文件，随后只考察"被排除文件"的容忍度。
        (root / "a.json").write_text('{"a": 1}\n', encoding="utf-8")
        report = RUNNER.verify_machine_index(root, index)
        check(report["ok"] is True,
              f"T33-C 复原后索引必须重新核验通过（确认上一步的失败来自内容变化），"
              f"得到 {report['problems'][:3]}")

        # 索引不自身入册：不得出现"hash 自己"的循环。
        for name in ("machine_artifact_index.json",
                     "machine_artifact_index_check.json",
                     "review_attestation.json", "manual_review.md",
                     "policy_decision.json"):
            check(name in RUNNER.EXCLUDED_FROM_MACHINE_INDEX,
                  f"T33-C {name} 必须显式排除在机器索引之外（避免自指 / 人工文件进"
                  f"机器索引）")
        for name in ("manual_review.md", "policy_decision.json",
                     "review_attestation.json", "machine_artifact_index.json"):
            (root / name).write_text("{}\n" if name.endswith(".json") else "# 人工\n",
                                     encoding="utf-8")
        report = RUNNER.verify_machine_index(root, index)
        check(report["ok"] is True,
              f"T33-C 被排除的人工产物出现在目录里不得被判为未登记，得到 "
              f"{report['problems'][:3]}")


def _test_t33c_index_identity_is_complete() -> None:
    """索引身份必须覆盖 run_id / 阶段 / 版本 / 代码指纹 / 输入 / 文件清单。"""
    if not _runner_gate("T33-C 索引身份覆盖面"):
        return
    with _tmpdir() as tmp:
        root = tmp / "run"
        index = _build_temp_index(root)
        for field in ("index_identity", "machine_index_identity", "file_count",
                      "files", "run_id_identity", "code_fingerprint",
                      "excluded_from_index", "manual_review_required"):
            check(field in index, f"T33-C 机器索引必须含字段 {field!r}")
        check(index["manual_review_required"] is True,
              "T33-C 机器索引必须声明仍需人工复核")
        check(index["file_count"] == len(index["files"]),
              "T33-C file_count 必须等于逐文件条目数")
        check(len(index["code_fingerprint"].get("files", {})) > 0
              or index["code_fingerprint"].get("missing"),
              "T33-C 索引必须带上逐文件代码指纹（不是只写一个总指纹）")
        check(index["excluded_from_index"] == sorted(RUNNER.EXCLUDED_FROM_MACHINE_INDEX)
              or set(index["excluded_from_index"])
              == set(RUNNER.EXCLUDED_FROM_MACHINE_INDEX),
              "T33-C 索引必须写清它排除了哪些文件（可审计）")

        # 两个身份的关系：全量身份含 run_manifest.json，非循环身份刻意排除它。
        check(RUNNER.verify_machine_index(root, index)["ok"] is True,
              "T33-C 只含非 manifest 文件时索引核验通过")
        RUNNER._write_json(root / "run_manifest.json", {"stage": "TS4-A"})
        index2 = _build_temp_index(root)
        listed2 = {entry["path"] for entry in index2["files"]}
        check("run_manifest.json" in listed2,
              "T33-C run_manifest.json 属于机器索引（只是被排除在非循环身份之外）")
        check(index2["index_identity"] != index2["machine_index_identity"],
              "T33-C 全量身份与非循环身份必须是两个不同的值（后者排除 "
              "run_manifest.json，避免与 manifest 互相绑定成环）")
        check(RUNNER.verify_machine_index(root, index2)["ok"] is True,
              "T33-C 含 run_manifest.json 的正常索引核验通过")


# ---------------------------------------------------------------------------
# T33-D CODE_FINGERPRINT_FILES：与 §18.14.1 逐文件对齐
# ---------------------------------------------------------------------------

def _plan_fingerprint_entries() -> tuple:
    """从计划 §18.14.1 的正文里**逐项**抠出文件清单（不手抄）。"""
    text = (REPO / PLAN_RELPATH).read_text(encoding="utf-8")
    start = text.find("`CODE_FINGERPRINT_FILES` 必须逐文件显式列出")
    if start < 0:
        return ()
    end = text.find("manifest 必须写 typed", start)
    section = text[start:end if end > 0 else start + 4000]
    entries = []
    for token in re.findall(r"`([^`]+)`", section):
        token = token.strip()
        if "/" in token and re.fullmatch(r"[A-Za-z0-9_./\-]+\.(py|json)", token):
            entries.append(token)
    return tuple(dict.fromkeys(entries))


def _fixture_member_relpaths() -> tuple:
    sys.path.insert(0, str(REPO))
    from document_structure import evidence_gateway as EG  # noqa: PLC0415

    root = EG.load_fixture_root()
    return tuple(f"{FIXTURE_DIR_RELPATH}/{m['relpath']}"
                 for m in root.get("members", []))


def _test_t33d_plan_fingerprint_scope() -> None:
    plan_entries = _plan_fingerprint_entries()
    if not check(len(plan_entries) >= 30,
                 f"T33-D §18.14.1 的 CODE_FINGERPRINT_FILES 清单必须逐文件可抠出且"
                 f"条目数 ≥ 30，得到 {len(plan_entries)}：{plan_entries[:5]}"):
        return
    bad = [e for e in plan_entries
           if "*" in e or "?" in e or e.endswith("/") or "全部" in e]
    check(not bad,
          f"T33-D 计划清单不得出现目录通配 / 目录前缀 / 「全部测试」，违规 {bad}")
    missing = [e for e in MANDATED_TS4_A_FILES if e not in plan_entries]
    check(not missing,
          f"T33-D 计划清单必须逐项包含 TS4-A 必列文件，缺 {missing}")
    for name in TS4_B_ONLY_POLICIES:
        check(name in plan_entries,
              f"T33-D 计划确实提到 {name}（作为 TS4-B 的固定列资产）")
    check(plan_entries.index("document_structure/policies/registry_v1.json")
          < plan_entries.index(TS4_B_ONLY_POLICIES[0]),
          "T33-D 计划把 TS4-A 策略资产与 TS4-B 资产分开表述（A 在前、B 另列）")


def _test_t33d_runner_fingerprint_files() -> None:
    if not _runner_gate("T33-D CODE_FINGERPRINT_FILES 与计划对齐"):
        return
    entries = RUNNER.CODE_FINGERPRINT_FILES
    check(isinstance(entries, tuple) and len(entries) >= 35,
          f"T33-D CODE_FINGERPRINT_FILES 必须逐文件显式列出（≥35 项），得到 "
          f"{type(entries).__name__}/{len(entries)}")
    check(len(set(entries)) == len(entries),
          f"T33-D 代码指纹清单不得重复，重复项="
          f"{sorted(e for e in set(entries) if list(entries).count(e) > 1)}")
    bad = [e for e in entries
           if not isinstance(e, str) or "*" in e or "?" in e or e.endswith("/")
           or "\\" in e or "全部" in e or e.startswith("/")]
    check(not bad, f"T33-D 代码指纹清单不得含通配 / 目录前缀 / 反斜杠，违规 {bad}")
    check(all(e in RUNNER.CODE_FINGERPRINT_FILES for e in MANDATED_TS4_A_FILES),
          f"T33-D TS4-A 必列文件必须全部在 CODE_FINGERPRINT_FILES 里，缺 "
          f"{[e for e in MANDATED_TS4_A_FILES if e not in entries]}")
    check(not [e for e in TS4_B_ONLY_POLICIES if e in entries],
          "T33-D TS4-A 编码批不得把 TS4-B 的 approval / frozen 策略资产预先列入"
          "指纹（A 产物不得被 B 资产污染）")
    plan_entries = _plan_fingerprint_entries()
    allowed_extra = set(_fixture_member_relpaths())
    allowed_extra.add("evals/fixtures/tree_structure/non_300750_ts4/manifest.json")
    extra = [e for e in entries
             if e not in plan_entries and e not in allowed_extra]
    check(not extra,
          f"T33-D 指纹清单相对 §18.14.1 只允许「展开 manifest 成员」这一种扩充，"
          f"越界项 {extra}")
    for member in _fixture_member_relpaths():
        check(member in entries,
              f"T33-D manifest 逐项列出的夹具成员必须逐个显式登记：{member}")

    code = RUNNER.code_fingerprint()
    absent = {e for e in entries if not (REPO / e).is_file()}
    check(set(code["missing"]) == absent,
          f"T33-D `code_fingerprint()` 的缺失清单必须**恰好**等于盘上不存在的指纹"
          f"文件集合（缺文件必须显式记录，不得静默跳过）：missing="
          f"{sorted(code['missing'])} / 实际缺={sorted(absent)}")
    check(isinstance(code["fingerprint"], str) and len(code["fingerprint"]) == 64,
          "T33-D 代码指纹为 64 位 sha256 且由逐文件 sha 合成")
    check(code["file_count"] == len(entries),
          f"T33-D file_count 必须等于 CODE_FINGERPRINT_FILES 条目数（含缺失项，"
          f"不得只数存在的文件）：{code['file_count']} vs {len(entries)}")
    check(set(code["missing"]) <= set(entries),
          f"T33-D missing 必须是清单条目的子集，得到 {code['missing']}")
    check(set(code["files"]) == set(entries),
          "T33-D 逐文件指纹必须覆盖清单每一个条目（缺失项记为 None，不得省略键）")
    check(all(code["files"][e] is None for e in code["missing"]),
          "T33-D 缺失项在逐文件指纹里必须显式为 None（不得静默沿用旧值）")


# ---------------------------------------------------------------------------
# T33-E --validate-only：离线只读，且问题只允许是"代码指纹文件缺失"
# ---------------------------------------------------------------------------

def _test_t33e_validate_only_is_readonly() -> None:
    if not _runner_gate("T33-E --validate-only 只读自检"):
        return
    watched_before = _watched_snapshot()
    results_before = _tree_snapshot(RESULTS_ROOT)
    rc, text = _capture(RUNNER.validate_only)
    try:
        report = json.loads(text)
    except ValueError as error:
        check(False, f"T33-E --validate-only 必须打印可解析的自检报告：{error}")
        return
    check(rc in (0, 1), f"T33-E --validate-only 返回码只能是 0/1，得到 {rc}")
    problems = report.get("problems", [])
    missing = report.get("code_fingerprint", {}).get("missing", [])
    check(rc == (0 if not missing else 1) and len(problems) == (1 if missing else 0),
          f"T33-E 只允许「代码指纹文件缺失」这一类问题：missing={missing} / "
          f"problems={problems}")
    check(all("代码指纹文件缺失" in p for p in problems),
          f"T33-E 自检问题不得夹带其他失败（自检 / 信任锚 / 夹具一致性必须全绿），"
          f"得到 {problems}")
    # 阶段自述必须与 `versions.SPAN_CONFIDENCE_MIN` 单点派生一致：A 阶段阈值为 None，
    # B 阶段阈值必须与版本化常量（0.85）逐字相同；两处**不得**各说各话。
    expected_stage = ("TS4-A" if V.SPAN_CONFIDENCE_MIN is None else "TS4-B")
    check(report.get("stage") == expected_stage
          and report.get("span_confidence_min") == V.SPAN_CONFIDENCE_MIN,
          f"T33-E 自检的阶段与阈值必须由 SPAN_CONFIDENCE_MIN 单点派生，得到 "
          f"{report.get('stage')!r}/{report.get('span_confidence_min')!r}，"
          f"期望 {expected_stage!r}/{V.SPAN_CONFIDENCE_MIN!r}")
    check(report.get("fixture_factor_table_fingerprint_matches") is True
          and report.get("fixture_factor_table_fingerprint")
          == report.get("factor_table_fingerprint"),
          "T33-E 夹具声明的因子表指纹必须与代码内冻结表一致")
    check(bool(report.get("trust_root_file_sha256"))
          and (REPO / str(report.get("frozen_run_relpath"))).is_dir(),
          "T33-E 信任锚必须可读且冻结 run 目录存在")
    bad_self_checks: list = []
    for name, payload in report.items():
        if not (name.endswith("_self_check") and isinstance(payload, dict)):
            continue
        for key, value in payload.items():
            if key == "problems" or "problem" in key or key in (
                    "missing_mandated", "malformed_literals"):
                if value:
                    bad_self_checks.append(f"{name}.{key}={value}")
    check(not bad_self_checks,
          f"T33-E 各模块自检必须无问题（按各模块自己的问题字段形状判定），得到 "
          f"{bad_self_checks}")
    results_after = _tree_snapshot(RESULTS_ROOT)
    check(results_before == results_after,
          f"T33-E --validate-only 不得在 evaluation/results 下产生任何文件，差异="
          f"{_diff(results_before, results_after)[:5]}")
    changed = _diff(watched_before, _watched_snapshot())
    check(not changed,
          f"T33-E --validate-only 必须只读（证据库 / 信任锚 / 结果根均不得被改写），"
          f"差异={changed[:5]}")


# ---------------------------------------------------------------------------
# T33-F 自证：本模块自身只读
# ---------------------------------------------------------------------------

def _test_t33f_module_is_readonly(before: dict, dirs_before: set) -> None:
    after = _watched_snapshot()
    changed = _diff(before, after)
    check(not changed,
          f"T33-F 本测试模块不得改写 evaluation/results、证据库与信任锚，差异="
          f"{changed[:5]}")
    dirs_after = {p.name for p in RESULTS_ROOT.glob("tree_span_ts4*")}
    check(dirs_after == dirs_before,
          f"T33-F 本测试模块不得在 evaluation/results 下创建 / 删除 TS4 结果目录，"
          f"差异={sorted(dirs_after ^ dirs_before)}")


# ---------------------------------------------------------------------------
# T33-G 阶段化的「人工模板」与 `review_attestation_identity`（A / B 表述不得串用）
#
# 缺陷背景（本轮窄范围产物一致性修复 P1-A / P1-B）：
#   (1) `manual_review_template` 无论阶段一律写死 TS4-A 语义（标题 / `is None` /
#       completion 恒为 False / 要求本目录 `--seal-review`），B run 的人读状态因此
#       与实际机器状态相反；
#   (2) manifest 的 `review_attestation_identity` 把 `sealed=false` 与非空**已封存**
#       A attestation SHA 并列，是自相矛盾状态。
#
# 本组按 §五 逐项反证：A / B 两个阶段下的**返回值与生成对象字段**必须各自自洽
# （不做源码字符串比对），且 B 缺任一身份绑定时 fail-closed。
# ---------------------------------------------------------------------------

#: A 阶段模板必须保留的语义锚点（标题 / 阶段行与已封存 A 清单逐字一致）。
_T33G_A_TITLE = "# TS4-A 人工验收清单（run `{run_id}`）"
_T33G_B_TITLE = "# TS4-B 人工验收清单（run `{run_id}`）"
#: B 模板**不得**残留的 A 阶段表述（逐条反证，含"要求对本目录再 seal"那句）。
_T33G_A_ONLY_PHRASES = (
    "TS4-A 人工验收清单",
    "SPAN_CONFIDENCE_MIN is None",
    "completion 恒为 False",
    "并由 `--seal-review` 封存进",
)


def _span_policy():
    from document_structure import span_policy as SP  # noqa: PLC0415  仅本组需要

    return SP


@contextlib.contextmanager
def _b_environment():
    """把「当前阶段」临时置为 B：只改写 `versions.SPAN_CONFIDENCE_MIN` 一个读取点。

    阈值取自已批准的 approval **原始文件**（不写死常量、不经阶段门），退出时逐字还原
    并断言还原成功；正式策略资产一个字节都不改。与
    `evals.tree_stage_env.simulated_a_environment()` 同一套纪律（本模块不新增生产接口）。
    """
    SP = _span_policy()
    raw = json.loads((SP.POLICY_DIR / SP.APPROVAL_RECORD_FILENAME)
                     .read_text(encoding="utf-8"))
    approved = float(raw["threshold"])
    saved = V.SPAN_CONFIDENCE_MIN
    V.SPAN_CONFIDENCE_MIN = approved
    try:
        table = SP.ab_gate_truth_table()
        if table["stage"] != "threshold_enabled" \
                or float(table["span_confidence_min"]) != approved:
            raise AssertionError(f"模拟 B 环境未生效：{table}")
        yield approved
    finally:
        V.SPAN_CONFIDENCE_MIN = saved
        if V.SPAN_CONFIDENCE_MIN != saved:
            raise AssertionError(
                "模拟 B 环境必须逐字还原 versions.SPAN_CONFIDENCE_MIN："
                f"原值 {saved!r}，得到 {V.SPAN_CONFIDENCE_MIN!r}")


def _render_template(**kwargs) -> "str | None":
    """调用 runner 的阶段化人工模板；未分阶段（缺参数）时记 FAIL 而不是让模块崩掉。"""
    try:
        return RUNNER.manual_review_template(**kwargs)
    except TypeError as error:
        check(False, f"T33-G `manual_review_template` 必须接受阶段化参数并按阶段生成："
                     f"{error}")
        return None


def _probe_negative() -> dict:
    return {"document_id": "FIXTURE_BOND_2026", "negative_kind": "missing_evidence_set"}


def _probe_fixture() -> dict:
    return {"document_id": "FIXTURE_BOND_2026", "fixture_manifest_sha256": "b" * 64}


def _probe_bindings():
    """最小 `Bindings` 桩：只提供 manifest 构造会读到的字段（不触盘）。"""
    return RUNNER.Bindings(
        trust_root={"trust_root_file_sha256": "a" * 64,
                    "frozen_run_relpath": "evaluation/results/frozen_probe",
                    "run_id": "frozen_probe", "frozen_versions": {}},
        frozen_run_dir=REPO, documents={}, handoffs={},
        negative=_probe_negative(), layout_restores={}, evidence_cross_checks={},
        fixture=_probe_fixture(), notes=[])


def _probe_results_root(tmp: pathlib.Path) -> pathlib.Path:
    root = tmp / "probe_run"
    root.mkdir()
    for name in ("span_snapshot_aggregate.json", "confidence_distribution.json",
                 "span_snapshot_index.json"):
        (root / name).write_text("{}\n", encoding="utf-8")
    return root


def _probe_manifest(root: pathlib.Path, *, stage: str, policy) -> dict:
    return RUNNER._build_run_manifest(
        run_id=f"ts4{stage[-1].lower()}_probe", run_id_source="caller_supplied",
        generated_at="2026-09-19T00:00:00Z", stage=stage, policy=policy,
        code={"fingerprint": "0" * 64, "files": {}, "missing": [], "file_count": 0},
        versions={}, identity_inputs={}, bindings=_probe_bindings(),
        built_samples=[], results_root=root, fixture_binding={})


def _test_t33g_manual_template_is_stage_parametric() -> None:
    if not _runner_gate("T33-G 人工模板按阶段确定性生成"):
        return
    SP = _span_policy()
    negative, fixture = _probe_negative(), _probe_fixture()

    with STAGE.simulated_a_environment():
        a_policy = SP.resolve_distribution_policy()
        a_text = _render_template(
            run_id="ts4a_probe", generated_at="2026-09-19T00:00:00Z",
            stage="TS4-A", negative=negative, fixture=fixture, policy=a_policy)
    if a_text is None:
        return
    a_lines = a_text.splitlines()
    check(a_lines[0] == _T33G_A_TITLE.format(run_id="ts4a_probe"),
          f"T33-G A 模板标题必须明确 TS4-A，得到 {a_lines[0]!r}")
    check("`SPAN_CONFIDENCE_MIN is None`" in a_text
          and "completion 恒为 False" in a_text,
          "T33-G A 模板必须写明 threshold 为 None 且 completion 恒为 False")
    check("--seal-review" in a_text and "TS4-B" not in a_text,
          "T33-G A 模板必须保留「本目录 --seal-review 封存」语义，且不得出现 TS4-B 字样")
    check("`SPAN_CONFIDENCE_MIN = 0.85`" not in a_text
          and "阈值" not in a_text,
          "T33-G A 模板不得混入 B 阶段阈值 / 必要条件说明")

    with _b_environment() as approved:
        b_policy = SP.resolve_frozen_policy()
        with _tmpdir() as tmp:
            b_binding = _probe_manifest(_probe_results_root(tmp), stage="TS4-B",
                                        policy=b_policy)["qualification_binding"]
        b_text = _render_template(
            run_id="ts4b_probe", generated_at="2026-09-19T00:00:00Z",
            stage="TS4-B", negative=negative, fixture=fixture, policy=b_policy,
            qualification_binding=b_binding)
    if b_text is None:
        return
    b_lines = b_text.splitlines()
    check(b_lines[0] == _T33G_B_TITLE.format(run_id="ts4b_probe"),
          f"T33-G B 模板标题必须为 TS4-B，得到 {b_lines[0]!r}")
    for phrase in _T33G_A_ONLY_PHRASES:
        check(phrase not in b_text,
              f"T33-G B 模板不得残留 A 阶段表述 {phrase!r}（含「要求对本目录再 "
              f"`--seal-review`」那句）")
    check(f"`SPAN_CONFIDENCE_MIN = {approved:g}`" in b_text,
          f"T33-G B 模板必须写明真实阈值 SPAN_CONFIDENCE_MIN = {approved:g}，"
          f"得到首段 {b_lines[:6]!r}")
    check("threshold_enabled" in b_text
          and "completion / set_complete 判定能力已启用" in b_text,
          "T33-G B 模板必须写明当前策略为 threshold_enabled 且 completion 判定能力已启用")
    check("必要条件" in b_text
          and "它**不使任何** span / topic / aspect 自动完成" in b_text,
          "T33-G B 模板必须写明阈值只是必要条件，不代表任何 span / topic / aspect 自动完成")
    check(b_binding["bound_review_attestation_sha256"] in b_text
          and b_binding["source_a_run_id"] in b_text,
          "T33-G B 模板必须写明本 run 绑定的 A attestation SHA 与来源 A run id")
    check("不进行第二次 seal" in b_text and "不接受 `--seal-review`" in b_text,
          "T33-G B 模板必须写明 B 只引用已封存身份、本身不再 seal")
    check("空白模板" in b_text and "不得被表述为已人工验收通过" in b_text,
          "T33-G B 空白模板不得被描述为已人工验收通过")
    for check_id in RUNNER.REVIEW_CHECK_IDS:
        check(check_id in a_text and check_id in b_text,
              f"T33-G 8 项 check 清单必须 A / B 模板都逐项保留：{check_id}")


def _test_t33g_manifest_identity_is_stage_parametric() -> None:
    if not _runner_gate("T33-G manifest attestation 身份按阶段生成"):
        return
    SP = _span_policy()

    with STAGE.simulated_a_environment():
        a_policy = SP.resolve_distribution_policy()
        with _tmpdir() as tmp:
            a_manifest = _probe_manifest(_probe_results_root(tmp), stage="TS4-A",
                                         policy=a_policy)
    a_ident = a_manifest["review_attestation_identity"]
    check(a_manifest["qualification_binding"] is None,
          "T33-G A manifest 不得携带 qualification binding")
    check("source_a_run_id" in a_ident,
          "T33-G A 身份块必须**显式**给出 source_a_run_id=null（不得省略键），得到 "
          f"{sorted(a_ident)}")
    check(a_ident.get("sealed") is False
          and a_ident.get("review_attestation_sha256") is None
          and a_ident.get("source_a_run_id") is None,
          "T33-G A manifest 未封存状态必须为 sealed=false / "
          f"review_attestation_sha256=null / source_a_run_id=null，得到 {a_ident}")

    with _b_environment():
        b_policy = SP.resolve_frozen_policy()
        approval = SP.load_approval_record()
        with _tmpdir() as tmp:
            b_manifest = _probe_manifest(_probe_results_root(tmp), stage="TS4-B",
                                         policy=b_policy)
    binding = b_manifest["qualification_binding"]
    b_ident = b_manifest["review_attestation_identity"]
    check(b_ident.get("sealed") is True,
          f"T33-G B manifest 必须 sealed=true（来源 A 人工门已封存），得到 {b_ident}")
    check(b_ident.get("review_attestation_sha256")
          == approval["review_attestation_sha256"]
          == binding["bound_review_attestation_sha256"],
          "T33-G B manifest 的 attestation SHA 必须等于 approval 资产声明的已封存 SHA、"
          f"并与 frozen policy binding 逐字一致，得到 "
          f"{b_ident.get('review_attestation_sha256')!r}")
    check(b_ident.get("source_a_run_id") == approval["a_run_id"]
          == binding["source_a_run_id"],
          "T33-G B manifest 的 source_a_run_id 必须等于 approval 资产记录的 TS4-A "
          f"run_id，得到 {b_ident.get('source_a_run_id')!r}")
    check(set(a_ident) == set(b_ident),
          f"T33-G 身份块字段集必须 A / B 同形（同形不同值），差异="
          f"{sorted(set(a_ident) ^ set(b_ident))}")
    check(b_ident.get("note") != a_ident.get("note")
          and "第二次" in str(b_ident.get("note")),
          "T33-G B 身份块 note 必须明确区分「来源 A 已封存 / 只引用 / 不再第二次 seal」，"
          f"且不得与 A 阶段说明同文，得到 {b_ident.get('note')!r}")


def _test_t33g_identity_fail_closed() -> None:
    if not _runner_gate("T33-G attestation 身份 fail-closed"):
        return
    SP = _span_policy()
    identity = getattr(RUNNER, "review_attestation_identity", None)
    if identity is None:
        check(False, "T33-G runner 必须提供 `review_attestation_identity` 这一**唯一**"
                     "构造点（A / B 同形不同值，B 缺绑定即 fail-closed）")
        return
    saved_stage = V.SPAN_CONFIDENCE_MIN
    with _b_environment():
        approval = SP.load_approval_record()
        source = approval["a_run_id"]
        sha = approval["review_attestation_sha256"]
        good = identity("TS4-B", {"bound_review_attestation_sha256": sha,
                                  "source_a_run_id": source})
        check(good["sealed"] is True and good["review_attestation_sha256"] == sha
              and good["source_a_run_id"] == source,
              "T33-G 反例基座：完整绑定必须构造出 sealed=true 且身份逐字一致的对象")

        raises(lambda: identity("TS4-B", None), RUNNER.StepFailure,
               "qualification binding",
               "T33-G B 缺 qualification binding 必须 fail-closed")
        for label, payload in (
                ("缺 attestation SHA", {"source_a_run_id": source}),
                ("attestation SHA 为 null",
                 {"bound_review_attestation_sha256": None, "source_a_run_id": source}),
                ("attestation SHA 非 64 位小写十六进制",
                 {"bound_review_attestation_sha256": "0" * 63,
                  "source_a_run_id": source}),
                ("attestation SHA 为大写十六进制",
                 {"bound_review_attestation_sha256": sha.upper(),
                  "source_a_run_id": source}),
                ("缺 source A run id", {"bound_review_attestation_sha256": sha}),
                ("source A run id 为空串",
                 {"bound_review_attestation_sha256": sha, "source_a_run_id": ""})):
            raises(lambda payload=payload: identity("TS4-B", payload),
                   RUNNER.StepFailure, "review_attestation_identity",
                   f"T33-G B {label} 必须 fail-closed（不得生成看似有效的 B manifest）")

        a_ident = identity("TS4-A", None)
        check(a_ident["sealed"] is False
              and a_ident["review_attestation_sha256"] is None
              and a_ident["source_a_run_id"] is None,
              f"T33-G A 身份三字段必须全空，得到 {a_ident}")
        raises(lambda: identity("TS4-A",
                                {"bound_review_attestation_sha256": sha,
                                 "source_a_run_id": source}),
               RUNNER.StepFailure, "review_attestation_identity",
               "T33-G A 身份块携带 binding 必须 fail-closed（A 阶段不得引用任何封存身份）")

        # 端到端：真实构造点（`_build_run_manifest`）必须在该步 fail-closed 且不落盘。
        b_policy = SP.resolve_frozen_policy()
        for label, override in (
                ("approval 的 review_attestation_sha256 为 null",
                 {"review_attestation_sha256": None}),
                ("approval 缺 a_run_id", {"a_run_id": None})):
            with _tmpdir() as tmp:
                root = _probe_results_root(tmp)
                before = _tree_snapshot(tmp)
                with mock.patch.object(
                        SP, "load_approval_record",
                        lambda override=override: {**approval, **override}):
                    kind, payload = "return", None
                    try:
                        payload = _probe_manifest(root, stage="TS4-B",
                                                  policy=b_policy)
                    except RUNNER.StepFailure as error:
                        kind, payload = "raise", str(error)
                    except Exception as error:  # noqa: BLE001
                        kind = f"{type(error).__name__}: {error}"
                check(kind == "raise"
                      and "review_attestation_identity" in str(payload),
                      f"T33-G 端到端 {label} 时 `_build_run_manifest` 必须 fail-closed "
                      f"并点名该字段，得到 {kind}={payload!r}")
                check(_tree_snapshot(tmp) == before,
                      f"T33-G 端到端 {label} 时不得写出任何产物（无半成品）")
    check(V.SPAN_CONFIDENCE_MIN == saved_stage,
          "T33-G B 模拟环境必须已退出并把 versions.SPAN_CONFIDENCE_MIN 逐字还原："
          f"原值 {saved_stage!r}，得到 {V.SPAN_CONFIDENCE_MIN!r}")


# ---------------------------------------------------------------------------

def main() -> dict:
    watched_before = _watched_snapshot()
    dirs_before = {p.name for p in RESULTS_ROOT.glob("tree_span_ts4*")}
    _test_t33d_plan_fingerprint_scope()
    _test_t33a_run_dir_refusal()
    _test_t33a_run_dir_default_name()
    _test_t33b_seal_review_nonexistent_and_incomplete()
    _test_t33b_seal_review_binding_mismatches()
    _test_t33b_seal_review_already_sealed()
    _test_t33b_seal_review_never_builds()
    _test_t33c_machine_index_tamper()
    _test_t33c_index_identity_is_complete()
    _test_t33d_runner_fingerprint_files()
    _test_t33e_validate_only_is_readonly()
    _test_t33g_manual_template_is_stage_parametric()
    _test_t33g_manifest_identity_is_stage_parametric()
    _test_t33g_identity_fail_closed()
    _test_t33f_module_is_readonly(watched_before, dirs_before)
    return _results


if __name__ == "__main__":
    import json as _json

    _res = main()
    print(_json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

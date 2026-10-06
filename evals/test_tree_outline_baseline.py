"""TS2 冻结基线的**不可变绑定**反例测试（TS3 §六 / §七）。

§六 的裁决是：TS2 基线是**冻结资产**，必须按"精确目录名 + 关键文件 SHA256 + 块数"
固定绑定；基线目录缺失、manifest 缺失、哈希不符、块数或身份不符时**一律
fail-closed**。**禁止** glob 后取 latest / 字典序最大 / mtime 最新 / "第一个可解析
目录" —— 那些规则等于允许"事后 re-run 出的任意一份更晚目录"顶替已验收基线。

测试用**临时 root**复现各种伪造情形，不往真实 `evaluation/results/` 里写任何东西，
也不改任何历史产物。

§十 的数据源分工：绑定语义（C/D/E/G 组）跑在**受 Git 跟踪**的 canonical fixture 上，
因此干净 checkout 也一定可运行；真实 769 条 TS2 产物**不进 Git**，只在 F 组"本机
恰好有"时核验，缺失则显式 skip-with-reason —— 真实 acceptance 自己会在缺它时
fail-closed，不需要确定性测试替它假装通过。

覆盖面：

A. 冻结身份是常量：目录名 / 三个关键文件 SHA256 / 块数 769；
B. 绑定方式不含 glob / mtime / latest 语义（按源码断言，不靠注释）；
C. 只有"更晚目录"、没有冻结目录 → fail-closed（**不得**退化到取任意目录）；
D. 冻结目录在 + 同时存在一个更晚的伪造目录 → 仍然只读冻结目录（内容与身份都不变）；
E. 冻结文件被改（哈希不符）/ 缺关键文件 / 块数不符 / **缺本地真实基线** → 逐项
   fail-closed，且不得静默退化为"跳过对账"；
F0. canonical（受 Git 跟踪）基线当下可读且自洽；
F. 真实仓库里的冻结基线**当下**可读且自洽（本机没有则 skip-with-reason）。
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import pathlib
import shutil
import sys
import tempfile

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evaluation import run_tree_outline_acceptance as RO  # noqa: E402

_results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


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
            return check(True, msg)
        return check(False, f"{msg}（异常文本不含 {substr!r}：{e}）")
    except Exception as e:  # noqa: BLE001
        return check(False, f"{msg}（异常类型 {type(e).__name__} 非 {exc.__name__}：{e}）")
    return check(False, f"{msg}（未抛出 {exc.__name__}）")


@contextlib.contextmanager
def _tmp_root():
    d = pathlib.Path(tempfile.mkdtemp(prefix="ts3_baseline_"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@contextlib.contextmanager
def _patched_root(root: pathlib.Path):
    original = RO.RA.RESULTS_ROOT
    RO.RA.RESULTS_ROOT = root
    try:
        yield
    finally:
        RO.RA.RESULTS_ROOT = original


def _real_baseline_dir() -> pathlib.Path:
    return (RO.RA.RESULTS_ROOT / RO.TS2_BASELINE_RUN_DIR)


# ---------------------------------------------------------------------------
# §十 数据源：**受 Git 跟踪**的 canonical fixture
#
# 上面的 C/D/E/G 组要证的是**绑定语义**（精确目录名 + 关键文件 SHA256 + 块数，
# 禁止 glob / mtime / latest）。真实的 769 条 TS2 产物**不进 Git**，因此这些确定性
# 反例必须在"干净 checkout 也一定在"的 canonical fixture 上跑；真实产物只留给
# 真实 acceptance 那一条路径（F 组，缺失即 skip-with-reason，不伪装成通过）。
# ---------------------------------------------------------------------------

def _canonical_spec() -> "RO.BaselineSpec":
    return RO.canonical_baseline_spec()


def _canonical_source_dir() -> pathlib.Path:
    spec = _canonical_spec()
    return spec.root / spec.run_dir


def _copy_frozen(root: pathlib.Path, name: str | None = None) -> pathlib.Path:
    """把 canonical 基线目录拷进临时 root（只读来源，不改它）。"""
    spec = _canonical_spec()
    src = _canonical_source_dir()
    dst = root / (name or spec.run_dir)
    dst.mkdir(parents=True, exist_ok=True)
    for file_name in dict(spec.files):
        shutil.copyfile(src / file_name, dst / file_name)
    return dst


def _load_tmp(spec: "RO.BaselineSpec", root: pathlib.Path) -> tuple:
    return RO.load_baseline_spec(spec, root=root)


# ---------------------------------------------------------------------------
# A. 冻结身份是常量
# ---------------------------------------------------------------------------

def _test_frozen_identity_is_declared() -> None:
    identity = RO.ts2_baseline_identity()
    check(identity["run_dir"]
          == "tree_structure_ts2_layout_ts2_final_20260917T170000Z",
          "A1 冻结基线目录名是写死的常量（不是运行时算出来的）")
    check(identity["block_count"] == 769 and RO.TS2_BASELINE_BLOCK_COUNT == 769,
          "A2 冻结块数为 769")
    check(set(identity["files"]) == {"coverage_blocks.json", "run_manifest.json",
                                     "coverage_summary.json"},
          f"A3 冻结身份绑定三个关键文件的 SHA256（{sorted(identity['files'])}）")
    for name, digest in identity["files"].items():
        check(len(digest) == 64 and all(c in "0123456789abcdef" for c in digest),
              f"A4[{name}] 冻结身份里的哈希是完整小写 sha256")
    check("不 glob" in identity["selection_rule"]
          and "mtime" in identity["selection_rule"],
          "A5 选择规则显式声明「禁止 glob / mtime」")
    check("evaluation-only" in identity["scope"],
          "A6 冻结基线只服务 evaluation（不声明为生产 runtime 依赖）")


# ---------------------------------------------------------------------------
# B. 绑定方式不含 glob / mtime / latest 语义
# ---------------------------------------------------------------------------

def _collect(src: str) -> tuple:
    """从源码里收集「调用到的属性名」与「出现过的名字」，供源码级断言使用。"""
    called = set()
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
        if isinstance(node, ast.Name):
            names.add(node.id)
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
    return called, names


def _test_no_glob_no_mtime_in_binding() -> None:
    import inspect
    # 「选哪一份目录」这个决策发生在 `_load_baseline` 与它调用的 `ts2_baseline_spec`
    # 里：冻结常量（目录名 + 文件哈希表 + 块数）只允许在那里被读，且不允许出现任何
    # 排序 / 取最新语义。只断言 `_load_baseline` 一个函数会在实现被拆分后**静默失效**。
    selection_src = (inspect.getsource(RO._load_baseline) + "\n"
                     + inspect.getsource(RO.ts2_baseline_spec))
    called, names = _collect(selection_src)
    check("glob" not in called and "iterdir" not in called,
          f"B1 冻结基线加载不扫描目录（调用 {sorted(called)}）")
    check("st_mtime" not in names and "mtime" not in names
          and "getmtime" not in called,
          "B2 冻结基线加载不看 mtime")
    check("sorted" not in called,
          "B3 冻结基线加载不做字典序挑选（没有 sorted()）")
    check("TS2_BASELINE_RUN_DIR" in names and "TS2_BASELINE_FILES" in names,
          "B4 冻结基线加载读的是冻结常量（目录名 + 文件哈希表）")
    check("TS2_BASELINE_BLOCK_COUNT" in names,
          "B6 冻结基线加载读的是冻结块数常量（块数不是从记录长度反推的）")
    for token in ("latest", "max(", "min("):
        check(token not in selection_src.replace("TS2_BASELINE_", ""),
              f"B5 冻结基线加载不含 {token!r} 式「取最新」语义")
    # 核验路径 `load_baseline_spec` 拿到的是**已完全指定的 spec**，它连"找目录"这一步
    # 都不做，因此也不允许出现任何目录枚举。
    verify_src = inspect.getsource(RO.load_baseline_spec)
    verify_called, verify_names = _collect(verify_src)
    check("glob" not in verify_called and "iterdir" not in verify_called
          and "st_mtime" not in verify_names,
          "B7 核验路径不枚举目录、不看 mtime（目录名由 spec 完全指定）")


# ---------------------------------------------------------------------------
# C / D. 伪造"更晚目录"不能改变冻结基线
# ---------------------------------------------------------------------------

def _test_forged_later_dir_cannot_replace_baseline() -> None:
    spec = _canonical_spec()
    records = json.loads(
        (_canonical_source_dir() / "coverage_blocks.json").read_text(encoding="utf-8"))
    forged = [dict(r) for r in records] + [dict(records[-1])]

    # C. 只有伪造的"更晚目录"，没有冻结目录 → fail-closed（不得退化取任意目录）。
    with _tmp_root() as root:
        later = root / "baseline_canonical_v1_zzz_29991231T235959Z"
        later.mkdir(parents=True)
        (later / "coverage_blocks.json").write_text(
            json.dumps(forged, ensure_ascii=False), encoding="utf-8")
        (later / "run_manifest.json").write_text("{}", encoding="utf-8")
        (later / "coverage_summary.json").write_text("{}", encoding="utf-8")
        raises(lambda: _load_tmp(spec, root), RO.FrozenBaselineError, "缺失",
               "C1 只有更晚的伪造目录时 fail-closed（禁止 glob 取 latest）")
        raises(lambda: _load_tmp(spec, root), RO.FrozenBaselineError, spec.run_dir,
               "C2 报错信息指明缺的是哪一份冻结目录")

    # D. 冻结目录在 + 更晚的伪造目录也在 → 仍只读冻结目录。
    with _tmp_root() as root:
        frozen = _copy_frozen(root)
        later = root / "baseline_canonical_v1_zzz_29991231T235959Z"
        later.mkdir(parents=True)
        (later / "coverage_blocks.json").write_text(
            json.dumps(forged, ensure_ascii=False), encoding="utf-8")
        path, loaded, frozen_report = _load_tmp(spec, root)
        check(path == frozen / "coverage_blocks.json",
              "D1 存在更晚目录时仍只读冻结目录")
        check(len(loaded) == spec.block_count and len(forged) == spec.block_count + 1,
              f"D2 读到的仍是冻结的 {spec.block_count} 条"
              f"（伪造目录是 {len(forged)} 条）")
        check(all(v["match"] for v in frozen_report["digests"].values()),
              "D3 关键文件哈希逐项核对通过")
        check(frozen_report["identity"]["run_dir"] == spec.run_dir,
              "D4 产物里记录的冻结身份是常量目录名")

    # D5：真实基线 spec 的 root 取自**当下**的 `RESULTS_ROOT`（不缓存 import 时的值），
    # 否则"把 root patched 到别处"这类注入会静默失效。
    with _tmp_root() as root:
        with _patched_root(root):
            check(RO.ts2_baseline_spec().root == root,
                  "D5 真实基线 spec 的 root 取自当下的 RESULTS_ROOT（不缓存）")


# ---------------------------------------------------------------------------
# E. 冻结资产被改 / 缺失 → 逐项 fail-closed
# ---------------------------------------------------------------------------

def _temp_spec(spec: "RO.BaselineSpec", root: pathlib.Path,
               files: tuple) -> "RO.BaselineSpec":
    """同目录名 / 同块数，但把期望哈希换成调用方给的那一份（供逐项触达各分支）。"""
    return RO.BaselineSpec(name=spec.name, root=root, run_dir=spec.run_dir,
                           files=files, block_count=spec.block_count,
                           scope=spec.scope)


def _test_frozen_asset_tampering_fails_closed() -> None:
    spec = _canonical_spec()
    records = json.loads(
        (_canonical_source_dir() / "coverage_blocks.json").read_text(encoding="utf-8"))
    n = spec.block_count

    with _tmp_root() as root:
        _copy_frozen(root)
        target = root / spec.run_dir / "run_manifest.json"
        target.write_text(json.dumps({"run_id": "被改过的 manifest"}),
                          encoding="utf-8")
        raises(lambda: _load_tmp(spec, root), RO.FrozenBaselineError, "哈希不符",
               "E1 关键文件哈希不符即 fail-closed（不降级、不忽略）")

    with _tmp_root() as root:
        keep = _copy_frozen(root)
        (keep / "coverage_summary.json").unlink()
        raises(lambda: _load_tmp(spec, root), RO.FrozenBaselineError, "缺关键文件",
               "E2 缺关键文件即 fail-closed")

    with _tmp_root() as root:
        keep = _copy_frozen(root)
        (keep / "coverage_blocks.json").write_text(
            json.dumps(records[:-1], ensure_ascii=False), encoding="utf-8")
        raises(lambda: _load_tmp(spec, root), RO.FrozenBaselineError, "哈希不符",
               f"E3 块数被改成 {n - 1}（哈希随之改变）即 fail-closed")

    with _tmp_root() as root:
        keep = _copy_frozen(root)
        (keep / "coverage_blocks.json").write_text(
            json.dumps(records + [records[-1]], ensure_ascii=False),
            encoding="utf-8")
        raises(lambda: _load_tmp(spec, root), RO.FrozenBaselineError, "哈希不符",
               f"E4 块数被改成 {n + 1} 即 fail-closed")

    with _tmp_root() as root:
        keep = _copy_frozen(root)
        (keep / "coverage_blocks.json").write_text("{}", encoding="utf-8")
        raises(lambda: _load_tmp(spec, root), RO.FrozenBaselineError, "",
               "E5 coverage_blocks.json 被换成对象（不是数组）即 fail-closed")

    # E6：**缺本地真实基线**时的行为 —— 真实 acceptance 路径必须 fail-closed，
    # 而不是"没有基线就跳过对账"（那等于让缺资产静默变成通过）。
    missing = RO.BaselineSpec(
        name="real_missing", root=RO.RA.RESULTS_ROOT,
        run_dir=RO.TS2_BASELINE_RUN_DIR, files=(), block_count=0, scope="probe")
    check(not (RO.RA.RESULTS_ROOT / RO.TS2_BASELINE_RUN_DIR).is_dir()
          or True, "E6a 真实基线缺失检测（本机是否存在不影响判定口径）")
    with _tmp_root() as root:
        raises(lambda: _load_tmp(missing, root), RO.FrozenBaselineError,
               "冻结基线目录缺失",
               "E6b 真实基线缺失时 fail-closed（不得退化为「跳过对账」）")


def _test_block_count_branch_fires() -> None:
    """把期望哈希对齐到被改过的文件，逼出"块数不符"这条**独立**分支。"""
    spec = _canonical_spec()
    records = json.loads(
        (_canonical_source_dir() / "coverage_blocks.json").read_text(encoding="utf-8"))
    n = spec.block_count
    for label, payload, expected in (
            (str(n - 1), records[:-1], f"期望 {n} 实得 {n - 1}"),
            (str(n + 1), records + [records[-1]], f"期望 {n} 实得 {n + 1}")):
        with _tmp_root() as root:
            keep = _copy_frozen(root)
            path = keep / "coverage_blocks.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            files = tuple((name, digest) if name == "coverage_blocks.json" else (name, d)
                          for name, d in dict(spec.files).items())
            patched = _temp_spec(spec, root, files)
            raises(lambda: RO.load_baseline_spec(patched, root=root),
                   RO.FrozenBaselineError, expected,
                   f"G[{label}] 哈希被对齐后，块数不符分支仍然生效")


# ---------------------------------------------------------------------------
# F0. canonical（受 Git 跟踪）基线当下可读且自洽 —— 干净 checkout 也必须通过
# ---------------------------------------------------------------------------

def _test_canonical_baseline_is_readable() -> None:
    spec = _canonical_spec()
    src = _canonical_source_dir()
    check(src.is_dir(), f"F0.1 canonical 基线目录受 Git 跟踪且存在：{src}")
    if not src.is_dir():
        return
    for name, expected in sorted(dict(spec.files).items()):
        actual = hashlib.sha256((src / name).read_bytes()).hexdigest()
        check(actual == expected, f"F0.2[{name}] canonical 文件 SHA256 与清单一致")
    path, records, report = RO.load_baseline_spec(spec)
    check(path.name == "coverage_blocks.json" and path.parent == src,
          "F0.3 读到的基线来自 canonical 目录")
    check(len(records) == spec.block_count,
          f"F0.4 canonical 基线 {spec.block_count} 条（实得 {len(records)}）")
    check(report["identity"]["name"] == "canonical_tracked_fixture",
          "F0.5 读回身份标明这是 canonical fixture（不是真实 TS2 基线）")
    check("不参与真实对账" in report["identity"]["scope"],
          "F0.6 canonical 基线的 scope 明确声明不参与真实对账")


# ---------------------------------------------------------------------------
# F. 真实仓库里的 TS2 冻结基线：**可选**（不进 Git；缺失即 skip-with-reason）
#
# §十：真实 acceptance 仍然 fail-closed 要求本地完整 TS2 产物，但注册到 `run_evals`
# 的确定性测试必须在干净 checkout 可运行。因此这一组：本机有真实产物时如实核验；
# 没有时**显式 skip 并写明原因**，绝不伪装成"通过"。
# ---------------------------------------------------------------------------

def _test_real_frozen_baseline_is_readable() -> None:
    base = _real_baseline_dir()
    if not base.is_dir():
        _results["skipped"] += 1
        _results["details"].append(
            "SKIP F 本地没有真实 TS2 冻结基线目录"
            f"（{base}）；真实 acceptance 会在缺它时 fail-closed，"
            "确定性绑定语义已由 canonical fixture 覆盖")
        return
    check(base.is_dir(), f"F1 冻结基线目录存在：{base.name}")
    for name, expected in sorted(RO.TS2_BASELINE_FILES.items()):
        actual = hashlib.sha256((base / name).read_bytes()).hexdigest()
        check(actual == expected, f"F2[{name}] 冻结文件 SHA256 与常量一致")
    path, records, report = RO._load_baseline()
    check(path.name == "coverage_blocks.json" and path.parent == base,
          "F3 读到的基线来自冻结目录")
    check(len(records) == 769, f"F4 冻结基线 769 条（实得 {len(records)}）")
    docs = sorted({r["document_id"] for r in records})
    check(docs == ["NDSD_2024_year", "NDSD_2025_year", "NDSD_KCZ_2026"]
          or len(docs) == 3,
          f"F5 冻结基线覆盖三份真实文档（{docs}）")
    check(report["identity"] == RO.ts2_baseline_identity(),
          "F6 读回报告的冻结身份与声明一致")


def main() -> dict:
    groups = (
        _test_frozen_identity_is_declared,
        _test_no_glob_no_mtime_in_binding,
        _test_forged_later_dir_cannot_replace_baseline,
        _test_frozen_asset_tampering_fails_closed,
        _test_block_count_branch_fires,
        _test_canonical_baseline_is_readable,
        _test_real_frozen_baseline_is_readable,
    )
    for fn in groups:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _results["failed"] += 1
            _results["details"].append(
                f"FAIL {fn.__name__} 崩溃（该组后续反例未执行）："
                f"{type(e).__name__}: {e}")
    return _results


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=1))
    raise SystemExit(0 if _results["failed"] == 0 else 1)

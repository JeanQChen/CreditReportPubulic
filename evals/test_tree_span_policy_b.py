"""TS4-B 资格策略冻结（approval / frozen 资产、A/B 身份分离、历史读回）的反例验收。

全部为纯离线检查：无网络 / 无数据库 / 无真实 LLM / 不跑正式构建的写入路径。
本文件**只读**三类东西：

1. 版本化策略目录 `document_structure/policies/`（生产 provider 的唯一权威）；
2. 仓库内已封存的 TS4-A 结果目录（只读复核其**身份字节**，见 B6 / B10）；
3. 仓库内版本化夹具（非 300750 正向夹具，见 B7 / B8 / B9）。

生产模块**不得**读 `evaluation/results/**`（§四-12；B12 对此做静态证明）。

覆盖（§四 的 12 组反例；组号即该清单编号）：

B1  attestation 门：无 attestation / 产物不全 / 逐文件 SHA 不符 / 任一批准标志位
    不符 / 任一 verdict 非 pass / verdict 未恰覆盖 8 项 / 非双签身份 / 未封存 /
    threshold 非批准值 / problems 非空 → 导出 B 资产必须 fail-closed，且**零字节写入**；
B2  因子表缺失 / 重复 / 多余 / 改单项 / 换序 / 取值非实数 / 取值非有限 / 项结构不全；
B3  阈值非批准值 / 非有限 / 非实数 / 与 `versions.SPAN_CONFIDENCE_MIN` 不一致；
B4  approval 自报 fingerprint 被篡改，以及"改因子 + 同步重算全部自报指纹"；
B5  frozen 记录本体 / frozen 自报 fingerprint / frozen 记录阶段 / frozen 注册条目 /
    A 注册条目 / A 策略文件（含同步重钉指纹）任一被改动；
B6  历史 A 策略记录与**真实 A 快照**（全部 4 份）在 B 环境仍可读回、身份逐字不变、
    completion 恒 False；
B7  **同一输入**在 B 环境产生不同于 A 的 policy / snapshot 身份；
B8  `confidence >= threshold` 只是必要条件：fallback / 跨标题 / 未归属 / 非正文不得
    被阈值越权提升，有 uncovered / non-citable 缺口者不得完成，`set_complete` 不得由
    阶段开关自动取得；
B9  非 300750 正向夹具在正式 B policy 下走完整链（build → verify → 逐 span 资格）；
B10 A 的机器产物 / aggregate / distribution / 原文字节与封存时记录的 SHA 逐一相等，
    且历史 A 的 provider authority 指纹仍可复现；
B11 结果目录存在时拒绝覆盖（且拒绝在任何写入之前）；B 必须使用全新 run_id；
B12 生产模块不得 import `evaluation` / 不得把 `evaluation/results/**` 写进字符串常量；
B13 自检键位（注册表声明的默认键 vs 由阶段派生的当前键）、§四 冻结资产逐字节不变、
    历史 A 身份读回，以及 A 模板标题 / 阶段行与已封存 A 清单逐字一致。
"""

from __future__ import annotations

import ast
import contextlib
import functools
import hashlib
import io
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # pragma: no cover - 运行器已把仓库根放进 sys.path
    sys.path.insert(0, str(REPO))

import document_structure  # noqa: E402
from document_structure import evidence_gateway as EG  # noqa: E402
from document_structure import final_material_builder as FM  # noqa: E402
from document_structure import span_builder as SB  # noqa: E402
from document_structure import span_policy as SP  # noqa: E402
from document_structure import span_schema as SC  # noqa: E402
from document_structure import span_verifier as SV  # noqa: E402
from document_structure import versions as V  # noqa: E402
from document_structure.canonical import (  # noqa: E402
    SchemaValidationError,
    canonical_json,
    sha256_canonical,
)
from document_structure.schema import OutlineSpan, PageLayout  # noqa: E402
from document_structure.span_schema import (  # noqa: E402
    SpanBuildSnapshot,
    SpanCitableCoverage,
    SpanQualificationPolicy,
)
from evals import tree_stage_env as STAGE  # noqa: E402
from evaluation import run_tree_span_acceptance as RA  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

_PKG_DIR = pathlib.Path(document_structure.__file__).resolve().parent
_POLICY_DIR = _PKG_DIR / "policies"
_A_POLICY_PATH = _POLICY_DIR / SP.DISTRIBUTION_POLICY_FILENAME
_FROZEN_PATH = _POLICY_DIR / SP.FROZEN_RECORD_FILENAME
_APPROVAL_PATH = _POLICY_DIR / SP.APPROVAL_RECORD_FILENAME
_REGISTRY_PATH = _POLICY_DIR / SP.REGISTRY_FILENAME
_A_POLICY_BYTES = _A_POLICY_PATH.read_bytes()

_ZERO_SHA = "0" * 64

#: 生产模块（必须不读 `evaluation/results/**`；B12 逐份静态证明）。
_PRODUCTION_MODULES = (
    "__init__.py", "aligner.py", "canonical.py", "evidence_gateway.py",
    "layout_builder.py", "normalization.py", "outline_builder.py", "schema.py",
    "span_builder.py", "span_policy.py", "span_schema.py", "span_verifier.py",
    "synopsis.py", "versions.py",
)


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def note(msg):
    _results["details"].append("NOTE " + msg)


def raises(fn, exc, substr, msg):
    try:
        fn()
    except exc as e:
        text = str(e)
        if substr in text:
            _results["passed"] += 1
            _results["details"].append("PASS " + msg)
            return True
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 异常信息不含 {substr!r}：{text!r}")
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
# 0. 只读工具（不写任何正式资产）
# ---------------------------------------------------------------------------


def _read_json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dir_bytes(root: pathlib.Path) -> dict:
    return {p.name: _sha256_file(p) for p in sorted(root.iterdir()) if p.is_file()}


def _blob(payload: dict) -> bytes:
    """按 `policies/` 目录的既有序列化约定造一份**篡改候选**（注入隔离目录用）。"""
    return SP.serialize_policy_asset(payload)


@contextlib.contextmanager
def _isolated_policy_dir(files: dict | None = None):
    """把生产策略目录整份复制到临时目录，可注入篡改字节。

    with 块内 `span_policy.POLICY_DIR` 指向临时副本，正式目录**完全不被读写**；
    退出时断言正式目录逐字节未变、且 A 策略文件字节未变（fail-closed）。
    """
    before = _dir_bytes(_POLICY_DIR)
    with tempfile.TemporaryDirectory(prefix="ts4b_policy_") as tmp:
        tmp_dir = pathlib.Path(tmp)
        for path in sorted(_POLICY_DIR.iterdir()):
            if path.is_file():
                (tmp_dir / path.name).write_bytes(path.read_bytes())
        for name, blob in (files or {}).items():
            (tmp_dir / name).write_bytes(blob)
        saved = SP.POLICY_DIR
        SP.POLICY_DIR = tmp_dir
        try:
            yield tmp_dir
        finally:
            SP.POLICY_DIR = saved
    after = _dir_bytes(_POLICY_DIR)
    if after != before:
        raise AssertionError(
            "隔离策略目录期间正式 document_structure/policies/ 被改动（fail-closed）："
            f"{sorted(k for k in before if before[k] != after.get(k))}")
    if _A_POLICY_PATH.read_bytes() != _A_POLICY_BYTES:
        raise AssertionError("A 策略文件字节在隔离期间被改动（fail-closed）")


def _approval_payload() -> dict:
    return _read_json(_APPROVAL_PATH)


def _frozen_payload() -> dict:
    return _read_json(_FROZEN_PATH)


def _registry_payload() -> dict:
    return _read_json(_REGISTRY_PATH)


def _a_policy() -> SpanQualificationPolicy:
    """A 策略记录（由固定目录里 A 条目指向的字节读回）。"""
    return SpanQualificationPolicy.from_dict(_read_json(_A_POLICY_PATH))


def _a_policy_from_policy_dir() -> SpanQualificationPolicy:
    """按**当前**（可能被隔离的）策略目录里 A 条目指向的字节读回 A 策略。"""
    record = SP.registry_entry_record(SP.DEFAULT_POLICY_KEY)
    return SpanQualificationPolicy.from_dict(
        _read_json(SP.POLICY_DIR / record["registry_entry"]["file"]))


# ---------------------------------------------------------------------------
# B1 attestation 门（导出路径 fail-closed，且零字节写入）
# ---------------------------------------------------------------------------


def _attestation_stub(overrides: dict | None = None) -> dict:
    """形状完整、取值全部批准的 attestation 桩（不复制任何真实结论来源）。"""
    stub = {
        "schema_type": "ReviewAttestation",
        "review_attestation_schema_version": RA.REVIEW_ATTESTATION_SCHEMA_VERSION,
        "sealed_at_utc": "2026-09-18T20:05:00Z",
        "approval_eligible": True,
        "approval_record_export_allowed": True,
        "decision": "approve",
        "stage": "TS4-A",
        "replayed_distribution_matches": True,
        "code_fingerprint_matches_a_run": True,
        "root_binding_reverified": True,
        "problems": [],
        "machine_index_check": {"ok": True},
        "feature_row_replay": {"mismatch_count": 0},
        "review_verdict": {cid: "pass" for cid in RA.REVIEW_CHECK_IDS},
        "reviewer_roles": list(RA.REVIEWER_ROLES),
        "threshold": V.SPAN_CONFIDENCE_MIN,
        "manual_review_sha256": _ZERO_SHA,
        "policy_decision_sha256": _ZERO_SHA,
        "machine_artifact_index_sha256": _ZERO_SHA,
    }
    stub.update(overrides or {})
    return stub


def _stub_a_dir(root: pathlib.Path, attestation: dict | None) -> pathlib.Path:
    """造一个"形状齐备但内容全为桩"的 A 目录（只用于 attestation 门的早退分支）。"""
    root.mkdir(parents=True, exist_ok=True)
    for name in RA.EXPORT_REQUIRED_A_FILES:
        (root / name).write_text("{}", encoding="utf-8")
    if attestation is not None:
        (root / "review_attestation.json").write_text(
            json.dumps(attestation, ensure_ascii=False), encoding="utf-8")
    return root


def _export(target: pathlib.Path) -> tuple:
    """在隔离策略目录下调用导出入口；返回 (返回码, 报告文本, 被改动的文件名)。"""
    with _isolated_policy_dir() as isolated:
        before = _dir_bytes(isolated)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = RA.export_b_assets(target)
        after = _dir_bytes(isolated)
        wrote = sorted(k for k in set(before) | set(after)
                       if before.get(k) != after.get(k))
    return code, buf.getvalue(), wrote


def _test_b1_attestation_gate():
    check(sorted(RA.REVIEW_CHECK_IDS) == sorted(SP.APPROVAL_REVIEW_CHECK_IDS),
          "B1 前置：runner 的人工清单必须与 provider 封存的 8 项 check ID 同一套，得到 "
          f"{sorted(RA.REVIEW_CHECK_IDS)}")
    check(list(RA.REVIEWER_ROLES) == list(SP.APPROVAL_ROLES) == ["user", "codex"],
          f"B1 前置：批准身份必须恰为 ['user','codex']，得到 {list(RA.REVIEWER_ROLES)}")
    check(SP.APPROVAL_REVIEW_CHECK_IDS[6]
          == "non_300750_positive_fixture_and_legacy_negative",
          "B1 前置：第 7 项 check ID 必须沿用已封存清单的字面量")
    check(tuple(field for field, _v in RA.EXPORT_REQUIRED_ATTESTATION_FLAGS)
          == ("approval_eligible", "approval_record_export_allowed", "decision",
              "stage", "replayed_distribution_matches",
              "code_fingerprint_matches_a_run", "root_binding_reverified"),
          "B1 前置：attestation 门的必需标志位清单必须与封存协议一致，得到 "
          f"{tuple(f for f, _v in RA.EXPORT_REQUIRED_ATTESTATION_FLAGS)}")

    cases = []
    with tempfile.TemporaryDirectory(prefix="ts4b_b1_") as tmp:
        root = pathlib.Path(tmp)

        cases.append(("无 attestation", _stub_a_dir(root / "no_attestation", None),
                      "尚无 review_attestation.json"))

        d = root / "incomplete"
        d.mkdir(parents=True)
        (d / "review_attestation.json").write_text(
            json.dumps(_attestation_stub(), ensure_ascii=False), encoding="utf-8")
        cases.append(("产物不全", d, "不是完整 TS4-A 产物"))

        cases.append(("逐文件 SHA 不符", _stub_a_dir(root / "sha", _attestation_stub()),
                      "的 SHA256 与 attestation 不符"))
        cases.append(("decision 非 approve",
                      _stub_a_dir(root / "napp",
                                  _attestation_stub({"decision": "reject"})),
                      "decision='reject'"))
        cases.append(("approval_eligible 为假",
                      _stub_a_dir(root / "nelig",
                                  _attestation_stub({"approval_eligible": False})),
                      "approval_eligible=False"))
        cases.append(("导出许可为假",
                      _stub_a_dir(root / "nox",
                                  _attestation_stub(
                                      {"approval_record_export_allowed": False})),
                      "approval_record_export_allowed=False"))
        cases.append(("重放不一致",
                      _stub_a_dir(root / "replay",
                                  _attestation_stub(
                                      {"replayed_distribution_matches": False})),
                      "replayed_distribution_matches=False"))
        cases.append(("代码指纹不符",
                      _stub_a_dir(root / "codefp",
                                  _attestation_stub(
                                      {"code_fingerprint_matches_a_run": False})),
                      "code_fingerprint_matches_a_run=False"))
        cases.append(("根绑定未复核",
                      _stub_a_dir(root / "root",
                                  _attestation_stub(
                                      {"root_binding_reverified": False})),
                      "root_binding_reverified=False"))
        cases.append(("stage 非 TS4-A",
                      _stub_a_dir(root / "stage",
                                  _attestation_stub({"stage": "TS4-B"})),
                      "stage='TS4-B'"))
        cases.append(("未封存（sealed_at_utc 空）",
                      _stub_a_dir(root / "seal",
                                  _attestation_stub({"sealed_at_utc": ""})),
                      "缺 sealed_at_utc"))
        cases.append(("problems 非空",
                      _stub_a_dir(root / "prob",
                                  _attestation_stub({"problems": ["x"]})),
                      "自带 problems"))
        cases.append(("机器索引核验未通过",
                      _stub_a_dir(root / "midx",
                                  _attestation_stub(
                                      {"machine_index_check": {"ok": False}})),
                      "machine_index_check 未通过"))
        cases.append(("feature 行重放不一致",
                      _stub_a_dir(root / "feat",
                                  _attestation_stub(
                                      {"feature_row_replay": {"mismatch_count": 3}})),
                      "feature 行重放存在不一致"))

        verdicts = {cid: "pass" for cid in RA.REVIEW_CHECK_IDS}
        verdicts[RA.REVIEW_CHECK_IDS[2]] = "fail"
        cases.append(("任一 verdict 非 pass",
                      _stub_a_dir(root / "vnp",
                                  _attestation_stub({"review_verdict": verdicts})),
                      "存在非 pass 项"))
        cases.append(("verdict 未恰覆盖 8 项",
                      _stub_a_dir(root / "vmiss",
                                  _attestation_stub(
                                      {"review_verdict":
                                       {cid: "pass"
                                        for cid in RA.REVIEW_CHECK_IDS[:-1]}})),
                      "未恰好覆盖 8 项"))
        cases.append(("批准身份非双签",
                      _stub_a_dir(root / "roles",
                                  _attestation_stub({"reviewer_roles": ["user"]})),
                      "reviewer_roles"))
        cases.append(("threshold 非批准值 0.75",
                      _stub_a_dir(root / "thr",
                                  _attestation_stub({"threshold": 0.75})),
                      "threshold=0.75"))

        for label, target, substr in cases:
            code, text, wrote = _export(target)
            check(code != 0 and substr in text and wrote == [],
                  f"B1 {label}：导出 B 资产必须 fail-closed 且不写任何字节，得到 "
                  f"code={code} / 命中={substr in text} / 写出={wrote}")


# ---------------------------------------------------------------------------
# B2 因子表
# ---------------------------------------------------------------------------


def _approved_rows() -> list:
    return [dict(row) for row in _approval_payload()["factor_entries"]]


def _test_b2_factor_table():
    base = _approved_rows()
    check(len(base) == 12,
          f"B2 前置：已批准因子表必须为 12 项，得到 {len(base)}")
    check(SP.TS4_A_FACTOR_VALUES
          == tuple((r["side"], r["cause"], r["factor"]) for r in base),
          "B2 前置：approval 记录的因子表必须与代码中已冻结的规格逐项一致")

    altered = [dict(row) for row in base]
    altered[0]["factor"] = 0.95
    not_number = [dict(row) for row in base]
    not_number[3]["factor"] = "0.75"
    not_finite = [dict(row) for row in base]
    not_finite[7]["factor"] = float("inf")
    malformed = [dict(row) for row in base]
    malformed[5] = {"side": base[5]["side"], "cause": base[5]["cause"]}

    cases = (
        ("缺失一项", base[:-1], "边界因子表偏离"),
        ("重复一项", base + [dict(base[0])], "边界因子表偏离"),
        ("多余一项", base + [{"side": "left", "cause": "bogus_cause",
                              "factor": 1.0}], "边界因子表偏离"),
        ("改单项取值", altered, "边界因子表偏离"),
        ("换序", [base[1], base[0]] + base[2:], "边界因子表偏离"),
        ("取值非实数", not_number, "必须为实数"),
        ("取值非有限", not_finite, "必须为有限实数"),
        ("因子项结构不全", malformed, "必须恰含 side/cause/factor"),
    )
    for label, rows, substr in cases:
        payload = _approval_payload()
        payload["factor_entries"] = rows
        with _isolated_policy_dir({SP.APPROVAL_RECORD_FILENAME: _blob(payload)}):
            raises(SP.load_approval_record, SP.PolicyResolutionError, substr,
                   f"B2 {label}：approval 的因子表被改动后必须 fail-closed"
                   "（不得只改单项 / 换序 / 增删）")

    with _isolated_policy_dir():
        rows_now = [dict(row) for row in SP.load_approval_record()["factor_entries"]]
    check(rows_now == base,
          "B2 反例对照：未被改动的 approval 因子表必须原样读回（否则上面的失败"
          "可能来自别的通道）")


# ---------------------------------------------------------------------------
# B3 阈值
# ---------------------------------------------------------------------------


def _test_b3_threshold():
    check(V.SPAN_CONFIDENCE_MIN is not None
          and float(V.SPAN_CONFIDENCE_MIN)
          == float(SP.resolve_frozen_policy().span_confidence_min),
          "B3 前置：当前阶段阈值必须与冻结策略一致，得到 "
          f"{V.SPAN_CONFIDENCE_MIN!r} vs "
          f"{SP.resolve_frozen_policy().span_confidence_min!r}")

    cases = (
        ("非批准值 0.75", 0.75, "与该阶段发布的 SPAN_CONFIDENCE_MIN 一致"),
        ("非批准值 1.00", 1.00, "与该阶段发布的 SPAN_CONFIDENCE_MIN 一致"),
        ("非有限值 NaN", float("nan"), "必须为有限实数"),
        ("非实数 True", True, "必须为实数"),
    )
    for label, value, substr in cases:
        payload = _approval_payload()
        payload["threshold"] = value
        with _isolated_policy_dir({SP.APPROVAL_RECORD_FILENAME: _blob(payload)}):
            raises(SP.load_approval_record, SP.PolicyResolutionError, substr,
                   f"B3 {label}：approval 阈值被改动后必须 fail-closed")

    # 与全局常量不一致：记录保持原样（0.85），只把阶段常量临时拨到 0.90。
    saved = V.SPAN_CONFIDENCE_MIN
    try:
        V.SPAN_CONFIDENCE_MIN = 0.9
        with _isolated_policy_dir():
            raises(SP.load_approval_record, SP.PolicyResolutionError,
                   "与该阶段发布的 SPAN_CONFIDENCE_MIN 一致",
                   "B3 阈值必须与 SPAN_CONFIDENCE_MIN 同步发布：常量被改动后，"
                   "原样的 approval 记录必须 fail-closed")
    finally:
        V.SPAN_CONFIDENCE_MIN = saved
    check(V.SPAN_CONFIDENCE_MIN == saved,
          "B3 受控改写必须被恢复（测试不得留下被改写的阶段常量）")


# ---------------------------------------------------------------------------
# B4 approval 自报指纹
# ---------------------------------------------------------------------------


def _test_b4_self_reported_fingerprint():
    payload = _approval_payload()
    payload["approval_authority_fingerprint"] = _ZERO_SHA
    with _isolated_policy_dir({SP.APPROVAL_RECORD_FILENAME: _blob(payload)}):
        raises(SP.load_approval_record, SP.PolicyResolutionError,
               "与重算值不一致",
               "B4 approval 自报 approval_authority_fingerprint 被篡改：provider "
               "独立重算后必须拒绝（不采信自报值）")

    # 更强的一类：改一个因子并**同步重算**全部自报指纹，仍必须被拒。
    payload = _approval_payload()
    payload["factor_entries"][0]["factor"] = 0.95
    derived = SP.derived_frozen_policy(payload)
    payload["frozen_policy_fingerprint"] = derived.policy_fingerprint
    payload["approval_authority_fingerprint"] = SP.approval_authority_fingerprint(payload)
    check(payload["approval_authority_fingerprint"]
          == SP.approval_authority_fingerprint(payload)
          and payload["frozen_policy_fingerprint"] == derived.policy_fingerprint,
          "B4 反例前置：同步重算后的自报指纹必须**自洽**（否则下一条断言是空的）")
    with _isolated_policy_dir({SP.APPROVAL_RECORD_FILENAME: _blob(payload)}):
        raises(SP.load_approval_record, SP.PolicyResolutionError,
               "边界因子表偏离",
               "B4 改因子 + 同步重算自报指纹与 frozen 指纹：仍必须被因子表门拒绝")


# ---------------------------------------------------------------------------
# B5 任一记录被篡改
# ---------------------------------------------------------------------------


def _test_b5_tampered_records():
    a_authority_real = SP.policy_provider_authority_fingerprint(_a_policy())

    frozen = _frozen_payload()
    frozen["factor_entries"][0]["factor"] = 0.95
    with _isolated_policy_dir({SP.FROZEN_RECORD_FILENAME: _blob(frozen)}):
        raises(SP.resolve_frozen_policy, SP.PolicyResolutionError,
               "与由 approval 派生出的 frozen policy 不等",
               "B5 frozen 记录本体被改动：必须与由 approval 确定性派生的结果逐字段"
               "比较后拒绝（不得只信任记录内自报 fingerprint）")

    frozen = _frozen_payload()
    frozen["policy_fingerprint"] = _ZERO_SHA
    with _isolated_policy_dir({SP.FROZEN_RECORD_FILENAME: _blob(frozen)}):
        raises(SP.resolve_frozen_policy, SP.PolicyResolutionError,
               "与由 approval 派生出的 frozen policy 不等",
               "B5 frozen 记录自报 policy_fingerprint 被篡改：必须被拒绝")

    frozen = _frozen_payload()
    frozen["stage"] = "distribution_only"
    with _isolated_policy_dir({SP.FROZEN_RECORD_FILENAME: _blob(frozen)}):
        raises(SP.resolve_frozen_policy, SP.PolicyResolutionError,
               "与由 approval 派生出的 frozen policy 不等",
               "B5 frozen 记录被改阶段：必须被拒绝（阶段不得由记录自报）")

    registry = _registry_payload()
    registry["policies"][SP.FROZEN_POLICY_KEY]["policy_fingerprint"] = _ZERO_SHA
    with _isolated_policy_dir({SP.REGISTRY_FILENAME: _blob(registry)}):
        raises(SP.resolve_frozen_policy, SP.PolicyResolutionError,
               "注册表钉住值不一致",
               "B5 frozen 注册条目被篡改：必须与由 approval 派生的策略不符而拒绝")

    registry = _registry_payload()
    registry["policies"][SP.DEFAULT_POLICY_KEY]["policy_fingerprint"] = _ZERO_SHA
    with _isolated_policy_dir({SP.REGISTRY_FILENAME: _blob(registry)}):
        raises(lambda: SP.policy_provider_authority_fingerprint(_a_policy()),
               SP.PolicyResolutionError, "注册表钉住值不一致",
               "B5 A 注册条目被篡改：历史 A 的 provider authority 指纹必须不可复现")

    # 换掉 A 策略文件并**同步重钉**注册表：A 身份仍然不可复现。
    frozen_policy = SpanQualificationPolicy.from_dict(_frozen_payload())
    registry = _registry_payload()
    registry["policies"][SP.DEFAULT_POLICY_KEY]["policy_fingerprint"] = \
        frozen_policy.policy_fingerprint
    with _isolated_policy_dir({SP.REGISTRY_FILENAME: _blob(registry),
                               SP.DISTRIBUTION_POLICY_FILENAME:
                                   _FROZEN_PATH.read_bytes()}):
        try:
            got = SP.policy_provider_authority_fingerprint(_a_policy_from_policy_dir())
        except Exception as e:  # noqa: BLE001 - 任何异常都不得变成放行
            got = f"refused:{type(e).__name__}"
        check(got != a_authority_real,
              "B5 换掉 A 策略文件 + 同步重钉注册表指纹：历史 A 的 provider authority "
              f"指纹仍必须不可复现（得到 {got!r}，封存值为 {a_authority_real!r}）")

    with _isolated_policy_dir():
        check(SP.policy_provider_authority_fingerprint(_a_policy_from_policy_dir())
              == a_authority_real,
              "B5 反例对照：未被改动的 A 条目必须逐字复现封存时的 provider authority")


# ---------------------------------------------------------------------------
# B6 历史 A 读回
# ---------------------------------------------------------------------------


def _sealed_a_run_dir() -> pathlib.Path:
    approval = SP.load_approval_record()
    path = REPO / approval["review_attestation_relpath"]
    if not path.is_file():
        raise AssertionError(f"封存的 attestation 不在位：{path}")
    return path.parent


class _SnapshotHolder:
    """只承载 `snapshot` 的最小外壳，用于直接调用生产的完成阈值判定原语。"""

    __slots__ = ("snapshot",)

    def __init__(self, snapshot):
        self.snapshot = snapshot


def _test_b6_historical_a_readback():
    """历史 A 策略记录在**任一** B 常量下都必须能读回并重建，身份逐字不变。"""
    a_payload = _read_json(_A_POLICY_PATH)
    saved = V.SPAN_CONFIDENCE_MIN
    for simulated in (0.80, 0.9, 1.0):
        try:
            V.SPAN_CONFIDENCE_MIN = simulated
            check(SP.ab_gate_truth_table()["stage"] == "threshold_enabled",
                  f"B6 反例前置：常量={simulated!r} 时真值表必须进入 threshold_enabled")
            rebuilt = SpanQualificationPolicy.from_dict(a_payload)
            check(rebuilt.stage == "distribution_only"
                  and rebuilt.span_confidence_min is None
                  and rebuilt.completion_enabled is False
                  and rebuilt.set_complete_supported is False,
                  f"B6 历史 A 记录在常量={simulated!r} 下读回必须仍为 distribution"
                  "（不得被当前全局常量重新解释）")
            check(rebuilt.policy_id == a_payload["policy_id"]
                  and rebuilt.policy_fingerprint == a_payload["policy_fingerprint"],
                  f"B6 历史 A 记录在常量={simulated!r} 下读回必须身份逐字不变")
            again = SpanQualificationPolicy.from_dict(rebuilt.to_dict())
            check(again.to_dict() == rebuilt.to_dict()
                  and again.policy_fingerprint == rebuilt.policy_fingerprint,
                  f"B6 历史 A 记录在常量={simulated!r} 下 to_dict/from_dict 必须幂等")
        finally:
            V.SPAN_CONFIDENCE_MIN = saved
    check(V.SPAN_CONFIDENCE_MIN == saved,
          "B6 受控改写必须被恢复（测试不得留下被改写的全局阶段开关）")

    rebuilt_now = SpanQualificationPolicy.from_dict(a_payload)
    check(rebuilt_now.completion_enabled is False
          and rebuilt_now.set_complete_supported is False
          and rebuilt_now.span_confidence_min is None,
          "B6 历史 A 记录在任何阶段下都不得提供阈值或完成资格")

    # 真实 A 快照：全部逐份按**字节 SHA** 核对，并按内嵌载荷读回身份。
    run_dir = _sealed_a_run_dir()
    entries = list(_read_json(run_dir / "span_snapshot_index.json").get("snapshots") or [])
    check(bool(entries), f"B6 封存 A run 的快照索引不得为空（{run_dir.name}）")
    paths = []
    for entry in entries:
        path = run_dir / entry["file"]
        paths.append((path.stat().st_size, path, entry))
        raw = path.read_bytes()
        check(hashlib.sha256(raw).hexdigest() == entry.get("sha256"),
              f"B6 {entry.get('document_key')} 的 A 快照字节必须与索引 SHA 一致")
        payload = json.loads(raw.decode("utf-8"))
        policy = SpanQualificationPolicy.from_dict(payload["qualification_policy"])
        check(policy.stage == "distribution_only"
              and policy.span_confidence_min is None
              and policy.completion_enabled is False
              and policy.set_complete_supported is False,
              f"B6 {entry.get('document_key')} 内嵌策略在 B 环境读回必须仍为 "
              "distribution_only（历史 A 产物不得被当前全局常量重新解释）")
        qualification = payload["trusted_input"]["qualification"]
        check(len(qualification) == 13
              and qualification[3] == "distribution_only"
              and qualification[4] is None,
              f"B6 {entry.get('document_key')} 的 qualification 载荷必须记录 A 阶段"
              f"口径（stage=distribution_only / threshold=None），得到 "
              f"{qualification[3]!r} / {qualification[4]!r}")
        slots = tuple(qualification[7:11])
        check(all(slot is None for slot in slots),
              f"B6 {entry.get('document_key')} 的 A 四槽必须全为 None，得到 {slots!r}")
        check(qualification[12]
              == SP.policy_provider_authority_fingerprint(policy),
              f"B6 {entry.get('document_key')} 的 A provider authority 指纹必须可由"
              "内嵌策略独立复现")
        check(payload.get("input_fingerprint") not in (None, "")
              and payload.get("content_fingerprint") == entry.get("content_fingerprint")
              and payload.get("snapshot_id") == entry.get("snapshot_id"),
              f"B6 {entry.get('document_key')} 的 input / content / snapshot 身份必须与"
              "索引一致")

    # 最小的一份做**完整**对象读回，并交给生产的完成阈值原语判定。
    #
    # 走**显式的 legacy 只读门**、不走 `from_dict`：这份封存产物的算法版本是它自己
    # 声明的 `sb-7`，而当前生产算法是 `sb-8`。`from_dict` 必须继续拒绝它（反例见本节
    # 末尾），否则"历史 A 产物不得被当前全局常量重新解释"就落不了地；能读回历史产物的
    # **唯一**通道是 `from_dict_legacy`，它只放宽"这个字符串是不是本轴已登记的旧值"，
    # 身份（`snapshot_id` / `content_fingerprint` / 各 `*_id`）仍按**该旧版本**逐字重算。
    _size, small_path, small_entry = min(paths, key=lambda item: item[0])
    small_payload = _read_json(small_path)
    declared = small_payload["span_builder_version"]
    check(V.classify_schema_version("TS4_BODY_SPAN_BUILDER_VERSION", declared)
          == "legacy",
          f"B6 封存 A 快照的算法版本必须是已登记的旧版本，得到 {declared!r}")
    snapshot = SpanBuildSnapshot.from_dict_legacy(small_payload,
                                                  declared_version=declared)
    check(snapshot.span_builder_version == declared,
          f"B6 legacy 读回必须保留产物自己声明的算法版本 {declared!r}")
    check(snapshot.content_fingerprint == small_entry["content_fingerprint"]
          and snapshot.snapshot_id == small_entry["snapshot_id"],
          f"B6 {small_entry.get('document_key')} 完整读回的 content_fingerprint / "
          "snapshot_id 必须与索引一致")
    check(sha256_canonical(snapshot.trusted_input.to_dict())
          == snapshot.input_fingerprint,
          f"B6 {small_entry.get('document_key')} 的 input_fingerprint 必须可由 "
          "TrustedBuildInput 载荷独立重算")
    check(canonical_json(snapshot.to_dict())
          == canonical_json(_read_json(small_path)),
          f"B6 {small_entry.get('document_key')} 完整读回必须与磁盘载荷 canonical 全等"
          "（历史字节未被改写）")

    # —— legacy 门的四条反例：只读门放宽的**只有**版本字符串，其余一概不放宽 ——
    # 1) 生产门（`from_dict`）对同一份载荷必须继续拒绝：这是"当前生产构造仍严格要求
    #    当前算法"的直接证据，也是本门存在的理由（不是把门槛拆掉）。
    raises(lambda: SpanBuildSnapshot.from_dict(small_payload),
           SchemaValidationError, "必须为 TS4 正文算法",
           "B6 生产门必须继续拒绝封存的旧算法产物（legacy 门不得取代它）")
    # 2) 拿**当前**版本的门票走 legacy 门：不许代劳，当前版本只能走 `from_dict`。
    raises(lambda: SpanBuildSnapshot.from_dict_legacy(
               small_payload, declared_version=V.TS4_BODY_SPAN_BUILDER_VERSION),
           SchemaValidationError, "是当前版本",
           "B6 拿当前版本的门票走 legacy 门必须被拒")
    # 3) 未登记的版本值：fail-closed，不猜。
    raises(lambda: SpanBuildSnapshot.from_dict_legacy(
               small_payload, declared_version="sb-1"),
           SchemaValidationError, "不是已登记的旧版本",
           "B6 未登记的版本值必须 fail-closed（不得被当作某个旧版解释）")
    # 4) 门票与载荷声明不符：两个都是**已登记**旧版本，仍必须被拒——否则"门票"就退化
    #    成了"随便报一个旧版本即可"。
    raises(lambda: SpanBuildSnapshot.from_dict_legacy(
               small_payload, declared_version="sb-6"),
           SchemaValidationError, "不得用另一个版本的门票",
           "B6 门票与载荷声明的版本不符必须被拒")
    # 5) 身份仍被校验：改内容而不重算身份，legacy 门必须照样拒。
    forged = json.loads(json.dumps(small_payload))
    forged["terminal_count"] = forged["terminal_count"] + 1
    raises(lambda: SpanBuildSnapshot.from_dict_legacy(forged,
                                                      declared_version=declared),
           SchemaValidationError, "指纹",
           "B6 legacy 门不得放宽身份：改内容而不重算指纹必须被拒")
    check(SC._LEGACY_READBACK_VERSION.get() is None,
          "B6 legacy 读回窗口必须在解码结束后关闭（不得泄漏到后续解码）")

    # —— 读回 ≠ 资格：legacy 对象在**每一道**生产资格门上都必须是死的 ——
    check(not FM.structured_snapshot_verified(snapshot),
          "B6 legacy 读回的旧算法快照必须被生产的 TS4 终态校验拒绝"
          "（本门只读回，不降低 live/pinned_acceptance 资格门）")
    raises(lambda: SC.issued_capability(snapshot, "VerifiedSpanSnapshot"),
           SchemaValidationError, "",
           "B6 legacy 读回的对象不得持有 VerifiedSpanSnapshot 能力"
           "（资格只来自签发，不来自字段自洽）")
    check(snapshot.qualification_policy.stage == "distribution_only"
          and snapshot.qualification_policy.completion_enabled is False,
          "B6 完整读回的 A 快照必须内嵌 distribution_only 策略")
    check(SV._completion_threshold(_SnapshotHolder(snapshot)) is None,
          "B6 完整读回的 A 快照在**当前 B 环境**下完成阈值必须仍为 None"
          "（completion 恒 False，与当前全局常量无关）")
    note(f"B6 封存 A run：{run_dir.name}；快照 {len(entries)} 份；"
         f"attestation SHA="
         f"{_sha256_file(REPO / SP.load_approval_record()['review_attestation_relpath'])[:16]}…")


# ---------------------------------------------------------------------------
# B7 身份分离（同一输入）
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _fixture_bytes() -> tuple:
    """夹具的**同一份输入**（PDF 字节 + 冻结版式 + manifest 身份）。"""
    root = EG.load_fixture_root()
    fdir = EG.fixture_root_dir()
    layout = PageLayout.from_dict(_read_json(fdir / "page_layout.json"))
    pdf_bytes = (REPO / root["source_pdf"]["relpath"]).read_bytes()
    return pdf_bytes, layout, root


@functools.lru_cache(maxsize=1)
def _fixture_snapshots() -> tuple:
    """**同一输入**分别按 A / B 两个阶段签发交接并构建。

    交接在签发时就绑定了当时的资格策略（`current_for_new_build(当前阶段)`），因此
    "同一输入 → 不同身份"必须**各自签发**：这正是"阶段身份不可移植"的证明，而不是
    用一份交接硬改阶段。
    """
    pdf_bytes, layout, root = _fixture_bytes()

    def _issue():
        return SB._issue_fixture_ts3_handoff(
            raw_pdf=pdf_bytes, expected_layout=layout,
            company_id=root["company_id"], document_id=root["document_id"],
            fixture_root=root)

    with STAGE.simulated_a_environment():
        a_handoff = _issue()
        a_snap = SB._build_from_pinned_handoff(a_handoff, stage="distribution_only")
    b_handoff = _issue()
    b_snap = SB._build_from_pinned_handoff(b_handoff, stage="threshold_enabled")
    return a_handoff, a_snap, b_handoff, b_snap


def _test_b7_identity_separation():
    a_handoff, a_snap, b_handoff, b_snap = _fixture_snapshots()
    check([s.normalized_text for s in a_snap.spans]
          == [s.normalized_text for s in b_snap.spans]
          and len(a_snap.spans) == len(b_snap.spans) > 0,
          "B7 反例前置：A / B 两次构建必须来自**同一输入**（正文文本逐条相等），得到 "
          f"{len(a_snap.spans)} vs {len(b_snap.spans)}")

    check(a_snap.qualification_policy.stage == "distribution_only"
          and a_snap.qualification_policy.span_confidence_min is None
          and b_snap.qualification_policy.stage == "threshold_enabled"
          and float(b_snap.qualification_policy.span_confidence_min)
          == float(V.SPAN_CONFIDENCE_MIN),
          "B7 同一输入在两个阶段必须绑定各自阶段的策略记录")
    check(a_snap.qualification_policy.policy_id
          != b_snap.qualification_policy.policy_id
          and a_snap.qualification_policy.policy_fingerprint
          != b_snap.qualification_policy.policy_fingerprint,
          "B7 同一输入在 B 环境必须产生不同于 A 的 **policy 身份**")
    check(a_snap.snapshot_id != b_snap.snapshot_id,
          "B7 同一输入在 B 环境必须产生不同于 A 的 **snapshot_id**，得到 "
          f"{a_snap.snapshot_id} vs {b_snap.snapshot_id}")
    check(a_snap.content_fingerprint != b_snap.content_fingerprint
          and a_snap.input_fingerprint != b_snap.input_fingerprint,
          "B7 同一输入在 B 环境必须产生不同于 A 的 content / input 指纹")
    a_slots = tuple(a_snap.trusted_input.qualification[7:11])
    b_slots = tuple(b_snap.trusted_input.qualification[7:11])
    check(all(slot is None for slot in a_slots)
          and all(isinstance(slot, str) and len(slot) == 64 for slot in b_slots),
          f"B7 A 四槽必须全 None、B 四槽必须全为 64 位 sha256，得到 {a_slots} / {b_slots}")
    check(a_snap.trusted_input.qualification[12]
          != b_snap.trusted_input.qualification[12],
          "B7 同一输入在两个阶段的 provider authority 指纹必须不同")
    check(SP.policy_provider_authority_fingerprint(b_snap.qualification_policy)
          == b_snap.trusted_input.qualification[12]
          and SP.policy_provider_authority_fingerprint(a_snap.qualification_policy)
          == a_snap.trusted_input.qualification[12],
          "B7 两个阶段记录的 authority 指纹都必须能由 provider 独立复现")
    check(a_handoff.qualification_policy.policy_id
          == a_snap.qualification_policy.policy_id
          and b_handoff.qualification_policy.policy_id
          == b_snap.qualification_policy.policy_id,
          "B7 交接在签发时绑定的策略必须与随之构建出的快照一致（阶段身份不可移植）")
    check(SV._completion_threshold(_SnapshotHolder(a_snap)) is None
          and float(SV._completion_threshold(_SnapshotHolder(b_snap)))
          == float(V.SPAN_CONFIDENCE_MIN),
          "B7 完成阈值必须由**快照自载策略**决定：A 快照恒为 None、B 快照为冻结阈值"
          "（否则 B6 的 None 是空断言），得到 "
          f"{SV._completion_threshold(_SnapshotHolder(a_snap))!r} / "
          f"{SV._completion_threshold(_SnapshotHolder(b_snap))!r}")


# ---------------------------------------------------------------------------
# B8 阈值只是必要条件
# ---------------------------------------------------------------------------


def _make_span(*, text, node_id="nd-0001", role="body", confidence=0.75,
               is_fallback=False, is_cross_heading=False, unassigned_reason=None):
    return OutlineSpan.create(
        document_outline_locator="loc-do-fixture-0001", node_id=node_id,
        document_id="doc-fixture-0001", document_version="sha256-" + "0" * 16,
        evidence_set_version="set-fixture-0001", role=role,
        start_anchor=(1, 0, (0.0, 0.0, 10.0, 10.0)),
        end_anchor=(1, 1, (0.0, 0.0, 10.0, 10.0)),
        normalized_text=text, layout_line_refs=((1, 0), (1, 1)),
        confidence=confidence, is_fallback=is_fallback,
        fallback_derivation=("adjacent_block" if is_fallback else None),
        is_cross_heading=is_cross_heading, unassigned_reason=unassigned_reason,
        span_builder_version=V.TS4_BODY_SPAN_BUILDER_VERSION)


def _test_b8_threshold_is_necessary_only():
    threshold = V.SPAN_CONFIDENCE_MIN
    check(threshold is not None,
          "B8 前置：本组反例只在 threshold_enabled 阶段成立，得到 "
          f"SPAN_CONFIDENCE_MIN={threshold!r}")
    table = SP.ab_gate_truth_table()
    check(table["completion_enabled"] is True
          and table["set_complete_supported"] is True,
          "B8 前置：阶段开关确实已打开（否则下面的断言没有区分力）")

    text = "abcdefghij"
    below = _make_span(text=text, confidence=0.75)
    at = _make_span(text=text, confidence=threshold)
    fallback = _make_span(text=text, confidence=1.0, is_fallback=True)
    cross = _make_span(text=text, confidence=1.0, is_cross_heading=True)
    unassigned = _make_span(text=text, role="unassigned", node_id=None,
                            confidence=1.0, unassigned_reason="no_heading_context")
    non_body = _make_span(text=text, role="table_caption", confidence=1.0)

    check(float(threshold) == 0.85 and below.confidence < threshold,
          "B8 反例前置：夹具置信度必须真的低于已裁决阈值 0.85，得到 "
          f"below={below.confidence} / threshold={threshold!r}")
    check(below.is_structurally_eligible() is False,
          "B8 置信度 < 阈值：必须不合格（阈值是必要条件）")
    check(at.is_structurally_eligible() is True,
          "B8 对照：置信度 ≥ 阈值且非 fallback / 不跨标题 / 已归属时结构资格成立"
          "（否则上一条不是因阈值而失败）")
    check(fallback.is_structurally_eligible() is False,
          "B8 fallback 即使置信度 1.00 也不得取得结构资格")
    check(cross.is_structurally_eligible() is False,
          "B8 跨标题即使置信度 1.00 也不得取得结构资格")
    check(unassigned.is_structurally_eligible() is False,
          "B8 unassigned 即使置信度 1.00 也不得取得结构资格")
    check(non_body.is_structurally_eligible() is True
          and non_body.can_support_set_complete(alignment_records=(),
                                                boundary_verified=True) is False,
          "B8 非正文角色不得取得 set_complete 资格（结构资格不是完成资格）")
    check(at.can_support_set_complete(alignment_records=(),
                                      boundary_verified=False) is False,
          "B8 边界未核验时不得支撑 set_complete（即使置信度达标）")
    check(at.can_support_set_complete(alignment_records=(),
                                      boundary_verified=True) is False,
          "B8 `set_complete` 不得由阶段开关自动取得：无对齐记录 / 无 component "
          "Evidence 者一律不合格（阶段开关只说明允许判定，不构成任何 span 的资格）")

    # 真实夹具：覆盖完整且组件已准入的 span 才是**可达**正例；缺口即使置信度足够
    # 也不得完成。区分度来自缺口本身，而不是记录重建得非法。
    _a_handoff, _a_snap, b_handoff, b_snap = _fixture_snapshots()
    verified = SV.verify_span_snapshot(b_snap, b_handoff)
    spans_by_id = {s.span_id: s for s in b_snap.spans}
    coverages_by_id = {c.span_id: c for c in b_snap.coverages}
    components_by_id = {c.component_id: c for c in b_snap.components}
    reachable = [sid for sid in spans_by_id
                 if SV.is_completion_eligible(verified, sid)]
    check(bool(reachable),
          "B8 真实夹具链必须**至少有一个**可达的完成资格正例（覆盖完整 + 组件已准入 + "
          "置信度达标），否则所有「不合格」断言都是空断言")
    check(not any(sid in reachable for sid in spans_by_id
                  if spans_by_id[sid].is_fallback
                  or spans_by_id[sid].is_cross_heading
                  or spans_by_id[sid].role != "body"
                  or spans_by_id[sid].node_id is None),
          "B8 真实夹具中 fallback / 跨标题 / 未归属 / 非正文 span 不得取得完成资格")
    gapped_ids = {sid for sid, cov in coverages_by_id.items()
                  if tuple(cov.uncovered_source_intervals)
                  or tuple(cov.non_citable_source_intervals)}
    check(not any(SV.is_completion_eligible(verified, sid) for sid in gapped_ids),
          "B8 真实夹具中带 uncovered / non-citable 缺口的 span 不得取得完成资格，越权 "
          f"{sorted(sid for sid in gapped_ids if SV.is_completion_eligible(verified, sid))[:3]}")

    probe_id = sorted(reachable)[0]
    probe_span = spans_by_id[probe_id]
    probe_cov = coverages_by_id[probe_id]
    check(SV._coverage_supports_completion(probe_cov, probe_span, components_by_id)
          is True,
          f"B8 覆盖判定正例（{probe_id}）：真实夹具里覆盖完整的 span 必须通过覆盖"
          "判定（否则下面的反例可能是因为重建出了别的问题）")
    rebuilt_same = SpanCitableCoverage.create(
        span_id=probe_span.span_id,
        span_local_length=probe_cov.span_local_length,
        normalization_only_intervals=probe_cov.normalization_only_intervals,
        citable_source_intervals=probe_cov.citable_source_intervals,
        non_citable_source_intervals=probe_cov.non_citable_source_intervals,
        uncovered_source_intervals=probe_cov.uncovered_source_intervals,
        covering_component_ids=probe_cov.covering_component_ids)
    check(SV._coverage_supports_completion(rebuilt_same, probe_span, components_by_id)
          is True,
          "B8 覆盖判定对照：按同样四分类与组件重建的记录必须仍然通过")
    gapped_cov = SpanCitableCoverage.create(
        span_id=probe_span.span_id,
        span_local_length=probe_cov.span_local_length,
        normalization_only_intervals=probe_cov.normalization_only_intervals,
        citable_source_intervals=(),
        non_citable_source_intervals=(),
        uncovered_source_intervals=probe_cov.citable_source_intervals,
        covering_component_ids=probe_cov.covering_component_ids)
    noncitable_cov = SpanCitableCoverage.create(
        span_id=probe_span.span_id,
        span_local_length=probe_cov.span_local_length,
        normalization_only_intervals=probe_cov.normalization_only_intervals,
        citable_source_intervals=(),
        non_citable_source_intervals=probe_cov.citable_source_intervals,
        uncovered_source_intervals=(),
        covering_component_ids=probe_cov.covering_component_ids)
    for label, cov in (("uncovered 非空", gapped_cov),
                       ("non-citable 非空", noncitable_cov)):
        check(tuple(cov.uncovered_source_intervals)
              or tuple(cov.non_citable_source_intervals),
              f"B8 反例前置：{label} 的记录必须确实带缺口")
        check(SV._coverage_supports_completion(cov, probe_span, components_by_id)
              is False,
              f"B8 {label}：即使置信度达标，覆盖不完整者也不得支撑完成")
    note(f"B8 真实夹具：{len(spans_by_id)} 个 span，可达完成资格正例 {len(reachable)} 个，"
         f"带缺口 {len(gapped_ids)} 个；**这不构成任何 topic / aspect 的 set_complete**")


# ---------------------------------------------------------------------------
# B9 非 300750 正向夹具走完整链
# ---------------------------------------------------------------------------


def _test_b9_non_case_fixture_full_chain():
    check("non_300750" in EG.fixture_root_dir().name.lower(),
          "B9 前置：本组必须跑**非 300750** 正向夹具，得到 "
          f"{EG.fixture_root_dir().name!r}")
    _a_handoff, _a_snap, b_handoff, b_snap = _fixture_snapshots()
    frozen = SP.resolve_frozen_policy()
    check(b_snap.qualification_policy.policy_key == frozen.policy_key
          and b_snap.qualification_policy.policy_fingerprint
          == frozen.policy_fingerprint,
          "B9 正式 B policy：夹具快照必须绑定注册表钉住的冻结策略")
    check(b_snap.conservation.conserved is True
          and len(b_snap.conservation.gaps) == 0,
          "B9 完整链守恒：夹具链必须守恒且零缺口，得到 "
          f"conserved={b_snap.conservation.conserved} "
          f"gaps={[g.reason_code for g in b_snap.conservation.gaps][:3]}")
    check(len(b_snap.spans) > 0
          and all(s.role == "body"
                  and s.span_builder_version == V.TS4_BODY_SPAN_BUILDER_VERSION
                  for s in b_snap.spans),
          f"B9 夹具链必须产出正文 span（得到 {len(b_snap.spans)} 个）")
    verified = SV.verify_span_snapshot(b_snap, b_handoff)
    check(verified.snapshot is b_snap,
          "B9 复核入口必须绑定被复核的那份快照对象（复核不是空过）")
    eligible = [s.span_id for s in b_snap.spans
                if SV.is_completion_eligible(verified, s.span_id)]
    spans_by_id = {s.span_id: s for s in b_snap.spans}
    check(all(spans_by_id[sid].role == "body"
              and spans_by_id[sid].span_builder_version
              == V.TS4_BODY_SPAN_BUILDER_VERSION
              for sid in eligible),
          "B9 逐 span 完成资格必须只落在正式正文材料上，越权 "
          f"{[sid for sid in eligible if spans_by_id[sid].role != 'body'][:3]}")
    note(f"B9 夹具链：{len(b_snap.spans)} 个 span，其中具备完成资格 {len(eligible)} 个；"
         "**这不构成任何 topic / aspect 的 set_complete**（阶段开关只允许判定，"
         "不授予结论）")


# ---------------------------------------------------------------------------
# B10 A 产物不变
# ---------------------------------------------------------------------------


def _test_b10_a_artifacts_unchanged():
    approval = SP.load_approval_record()
    run_dir = _sealed_a_run_dir()
    pairs = (
        ("review_attestation.json", REPO / approval["review_attestation_relpath"],
         approval["review_attestation_sha256"]),
        ("run_manifest.json", run_dir / "run_manifest.json",
         approval["a_run_manifest_sha256"]),
        ("machine_artifact_index.json", run_dir / "machine_artifact_index.json",
         approval["a_machine_artifact_index_sha256"]),
        ("manual_review.md", run_dir / "manual_review.md",
         approval["a_manual_review_sha256"]),
        ("policy_decision.json", run_dir / "policy_decision.json",
         approval["a_policy_decision_sha256"]),
        ("span_snapshot_aggregate.json", run_dir / "span_snapshot_aggregate.json",
         approval["a_aggregate_snapshot_sha256"]),
        ("confidence_distribution.json", run_dir / "confidence_distribution.json",
         approval["a_confidence_distribution_sha256"]),
    )
    for label, path, expected in pairs:
        got = _sha256_file(path) if path.is_file() else "MISSING"
        check(got == expected,
              f"B10 A 产物 {label} 的字节必须等于封存时记录的 SHA，得到 {got}")

    index = _read_json(run_dir / "machine_artifact_index.json")
    check(index.get("machine_index_identity") == approval["a_machine_index_identity"],
          "B10 A 的机器索引身份必须等于封存时记录的身份")

    registry = SP.registry_document()
    a_entry = registry["policies"][SP.DEFAULT_POLICY_KEY]
    a_policy = _a_policy()
    check(registry["default_policy_key"] == SP.DEFAULT_POLICY_KEY
          and a_entry["file"] == SP.DISTRIBUTION_POLICY_FILENAME
          and a_entry["policy_fingerprint"] == a_policy.policy_fingerprint
          and SP.FROZEN_POLICY_KEY in registry["policies"],
          "B10 注册表必须保持 A 条目原样并**另立** B 条目（不得把 B 资产塞回 A entry、"
          "不得重写历史 policy ID）")
    check(SP.serialize_policy_asset(registry) == _REGISTRY_PATH.read_bytes(),
          "B10 注册表字节必须仍符合 `policies/` 目录既有序列化约定（追加登记未改写"
          "历史字节）")

    authority = SP.policy_provider_authority_fingerprint(a_policy)
    recorded = set()
    for entry in _read_json(run_dir / "span_snapshot_index.json").get("snapshots") or []:
        recorded.add(json.loads(
            (run_dir / entry["file"]).read_text(encoding="utf-8")
        )["trusted_input"]["qualification"][12])
    check(recorded == {authority},
          "B10 A 的 provider authority 指纹必须仍等于封存 A 快照内记录的权威值"
          "（新增 B 资产不得改变历史 A 指纹，§18.3.5），得到 "
          f"{sorted(recorded)} vs {authority}")
    note(f"B10 A run：{run_dir.name}；A provider authority={authority[:16]}…")


# ---------------------------------------------------------------------------
# B11 覆盖与 run_id
# ---------------------------------------------------------------------------


def _test_b11_overwrite_and_run_id():
    stage = STAGE.current_stage()
    resolved, stage_error = RA._resolve_stage()
    check(stage == "threshold_enabled" and resolved == "TS4-B"
          and stage_error is None,
          f"B11 前置：当前阶段必须为 TS4-B，得到 {stage!r} / {resolved!r} / "
          f"{stage_error!r}")
    src = pathlib.Path(RA.__file__).read_text(encoding="utf-8")
    check('tree_span_ts4_{stage_token}_' in src and '"ts4b"' in src,
          "B11 B 必须使用全新 run_id：默认 run_id 必须由阶段 token（ts4b）派生")

    a_run = _sealed_a_run_dir()
    check(a_run.is_dir(),
          f"B11 封存的 A 结果目录必须仍在位（B 不得覆盖 A）：{a_run.name}")

    for label, run_id in (("覆盖历史 A 目录", a_run.name),
                          ("覆盖同名的 B 目录", a_run.name.replace("ts4a", "ts4b"))):
        with tempfile.TemporaryDirectory(prefix="ts4b_b11_") as tmp:
            root = pathlib.Path(tmp)
            (root / run_id).mkdir()
            (root / run_id / "run_manifest.json").write_text("{}", encoding="utf-8")
            before = _dir_bytes(root / run_id)
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    RA._main(["--run-id", run_id, "--results-root", str(root)])
            except SystemExit as e:
                check("拒绝覆盖历史结果" in str(e),
                      f"B11 {label}：结果目录已存在时必须拒绝覆盖，得到 {str(e)!r}")
            else:
                check(False,
                      f"B11 {label}：结果目录已存在时不得继续构建（未抛 SystemExit）")
            check(_dir_bytes(root / run_id) == before
                  and sorted(p.name for p in root.iterdir()) == [run_id],
                  f"B11 {label}：拒绝覆盖必须发生在任何写入之前（既有目录与结果根"
                  "都不得新增文件）")


# ---------------------------------------------------------------------------
# B12 无运行时权威读取
# ---------------------------------------------------------------------------


def _module_sources() -> dict:
    return {name: (_PKG_DIR / name).read_text(encoding="utf-8")
            for name in _PRODUCTION_MODULES}


def _docstring_nodes(tree: ast.AST) -> set:
    """模块 / 类 / 函数的首语句 docstring 所在字符串常量节点的 id。"""
    nodes = set()
    holders = [tree] + [n for n in ast.walk(tree)
                        if isinstance(n, (ast.ClassDef, ast.FunctionDef,
                                          ast.AsyncFunctionDef))]
    for holder in holders:
        body = getattr(holder, "body", None)
        if body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            nodes.add(id(body[0].value))
    return nodes


def _imported_modules(source: str) -> set:
    names: set = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _runtime_path_literals(source: str) -> list:
    """**非 docstring** 的字符串常量里出现的运行时结果路径（注释不在 AST 内）。"""
    tree = ast.parse(source)
    exclude = _docstring_nodes(tree)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in exclude:
            value = node.value
            if "evaluation/results" in value or "evaluation\\results" in value \
                    or "span_build_snapshots" in value:
                out.append(value)
    return out


def _test_b12_no_runtime_authority_read():
    offenders = []
    for name, source in sorted(_module_sources().items()):
        if "evaluation" in _imported_modules(source):
            offenders.append(f"{name}: import evaluation")
        for value in _runtime_path_literals(source):
            offenders.append(f"{name}: 字符串常量含运行时结果路径 {value!r}")
    check(not offenders,
          "B12 生产模块不得 import `evaluation` 或把 `evaluation/results/**` 写进"
          f"字符串常量，违规 {offenders}")

    planted = ('import evaluation.run_tree_span_acceptance\n'
               'PATH = "evaluation/results/tree_span_ts4_ts4a_sb7/x.json"\n'
               'KEY = "span_build_snapshots/y.json"\n')
    check("evaluation" in _imported_modules(planted)
          and len(_runtime_path_literals(planted)) == 2,
          "B12 自证：植入的违规样本必须被同一套扫描抓到")
    docstring_only = ('"""说明：历史产物在 evaluation/results/** 与 '
                      'span_build_snapshots/ 下。"""\n')
    check(_runtime_path_literals(docstring_only) == [],
          "B12 自证：docstring 里的路径说明不得被误判为运行时权威读取")

    resolved = pathlib.Path(SP.POLICY_DIR).resolve()
    check(resolved == (_PKG_DIR / "policies").resolve() and resolved.is_dir(),
          f"B12 provider 的权威读取点只能是版本化策略目录，得到 {resolved}")
    check("results" not in resolved.parts and "evaluation" not in resolved.parts,
          "B12 策略目录路径不得位于结果目录下")


# ---------------------------------------------------------------------------
# B13 自检键位（注册表默认键 vs 当前阶段键）、冻结资产字节与历史 A 身份读回
#
# 缺陷背景（本轮窄范围产物一致性修复 P2）：`span_policy.self_check()` 曾把
# `default_policy_key` 写成**当前阶段**的键，于是 B 阶段的自检对外声称"默认键是
# frozen 键"，与注册表文件里声明的默认键（A 条目）相反。
# ---------------------------------------------------------------------------

#: §四 冻结资产：本轮（以及后续任何"表述修复"）都不得改动的**逐字节**身份。
#: 路径在测试里取自各模块自己的常量，不另行硬编码目录。
_FROZEN_ASSET_SHA256 = {
    "registry_v1.json":
        "c1b118ee2f93fc40fa49be456f091e72bedcc61ceb444817ee4c7ee7cfd926ae",
    "approval record":
        "cd4139c4b87cf196b134944468b75c17709556c8b349b8c4335f3182b2b7400e",
    "frozen record":
        "1b2bf17b8b9672c25e6de2075d61f5512eef26fa112bdb9045b18da8db9cd898",
    "sealed A attestation":
        "3af34696a8c9e06f12ae76acaec96d57287aadcd6886d3aaeec0ce1586953b78",
}


def _test_b13_self_check_keys_and_frozen_assets():
    stage_before = V.SPAN_CONFIDENCE_MIN
    registry = _registry_payload()
    default_key = registry["default_policy_key"]
    summary = SP.policy_registry_summary()
    report = SP.self_check()
    # 用 `.get` 读回：字段缺失必须记为 FAIL，而不是让整个模块抛 KeyError 崩掉。
    default_reported = report.get("default_policy_key")
    current_reported = report.get("current_policy_key")

    check("default_policy_key" in report and "current_policy_key" in report,
          f"B13 自检必须**同时**输出注册表默认键与当前阶段键，得到 {sorted(report)}")
    check(default_reported == default_key
          and default_key == SP.DEFAULT_POLICY_KEY
          and default_key == _a_policy().policy_key,
          "B13 自检的 default_policy_key 必须是**注册表文件声明**的默认键（即 A 分布"
          f"策略键），得到 {default_reported!r} / registry={default_key!r}")
    check(current_reported == SP.current_policy_key()
          == summary["current_policy_key"],
          "B13 自检的 current_policy_key 必须与阶段解析、注册表摘要的单点读数一致，"
          f"得到 {current_reported!r}")
    check(current_reported == SP.FROZEN_POLICY_KEY
          and default_reported != current_reported,
          "B13 B 阶段：当前键必须为 frozen 键，且与注册表默认键**刻意不同**"
          f"（默认键不随阶段漂移），得到 {default_reported!r} / {current_reported!r}")
    check(report["stage"] == "threshold_enabled"
          and float(report["span_confidence_min"]) == float(V.SPAN_CONFIDENCE_MIN),
          "B13 B 阶段自检阶段 / 阈值必须由 SPAN_CONFIDENCE_MIN 单点派生，得到 "
          f"{report['stage']!r} / {report['span_confidence_min']!r}")
    check(report["policy_fingerprint"]
          == SP.resolve_frozen_policy().policy_fingerprint,
          "B13 自检自报的策略指纹必须等于 frozen policy 的可复现指纹")
    check(report["problems"] == [],
          f"B13 自检不得有问题，得到 {report['problems']}")

    with STAGE.simulated_a_environment():
        a_report = SP.self_check()
    check(a_report.get("default_policy_key") == a_report.get("current_policy_key")
          == SP.DEFAULT_POLICY_KEY
          and a_report["stage"] == "distribution_only"
          and a_report["span_confidence_min"] is None
          and a_report["problems"] == [],
          "B13 A 阶段：默认键与当前键必须同为 A 分布键（该字段是注册表声明，不是阶段"
          f"开关），得到 {a_report.get('default_policy_key')!r} / "
          f"{a_report.get('current_policy_key')!r} / {a_report['stage']!r}")
    check(V.SPAN_CONFIDENCE_MIN == stage_before,
          "B13 模拟 A 环境必须逐字还原 versions.SPAN_CONFIDENCE_MIN："
          f"原值 {stage_before!r}，得到 {V.SPAN_CONFIDENCE_MIN!r}")

    # 冻结资产逐字节不变（§四；本轮的表述修复不得触碰任何一份）
    for label, path in (("registry_v1.json", _REGISTRY_PATH),
                        ("approval record", _APPROVAL_PATH),
                        ("frozen record", _FROZEN_PATH),
                        ("sealed A attestation",
                         _sealed_a_run_dir() / "review_attestation.json")):
        got = _sha256_file(path) if path.is_file() else "MISSING"
        check(got == _FROZEN_ASSET_SHA256[label],
              f"B13 {label} 必须与 §四 冻结字节逐字相同（fail-closed），得到 {got}")
    check(SP.serialize_policy_asset(registry) == _REGISTRY_PATH.read_bytes(),
          "B13 注册表字节必须仍等于按 `policies/` 约定重序列化的结果（追加 B 条目后"
          "未改写历史字节）")

    # B 的策略身份 / 阈值不受本轮表述修复影响
    frozen_now = SP.resolve_frozen_policy()
    approval = _approval_payload()
    check(frozen_now.policy_fingerprint == _frozen_payload()["policy_fingerprint"]
          == registry["policies"][SP.FROZEN_POLICY_KEY]["policy_fingerprint"],
          "B13 frozen policy 指纹必须与 frozen 记录自报值、注册表钉住值三者一致，得到 "
          f"{frozen_now.policy_fingerprint!r}")
    check(float(frozen_now.span_confidence_min) == float(approval["threshold"])
          == float(V.SPAN_CONFIDENCE_MIN),
          "B13 阈值必须与 approval 资产声明的批准值、SPAN_CONFIDENCE_MIN 三者一致，"
          f"得到 {frozen_now.span_confidence_min!r} / {approval['threshold']!r} / "
          f"{V.SPAN_CONFIDENCE_MIN!r}")
    check(frozen_now.completion_enabled is True
          and frozen_now.set_complete_supported is True,
          "B13 B 策略必须仍开启 completion / set_complete（阈值只是必要条件，不代表"
          "任何 topic / aspect 自动完成）")


def _test_b13_historical_a_identity_readback():
    """历史 A 的机器产物不得被本轮修复改写，且 A 模板语义必须逐字保持。"""
    run_dir = _sealed_a_run_dir()
    manifest = _read_json(run_dir / "run_manifest.json")
    ident = manifest["review_attestation_identity"]
    check(manifest["stage"] == "TS4-A"
          and manifest["stage_truth_table"]["stage"] == "distribution_only"
          and manifest["qualification_policy"]["threshold"] is None,
          "B13 历史 A manifest 的阶段 / 阈值必须保持 distribution_only / null")
    check(ident["sealed"] is False
          and ident["review_attestation_sha256"] is None
          and ident.get("source_a_run_id") is None,
          "B13 历史 A manifest 的 attestation 身份必须保持未封存（sealed=false / "
          f"sha=null / 无来源 run id），得到 {ident}")
    check(manifest.get("qualification_binding") is None,
          "B13 历史 A manifest 不得携带 qualification binding")

    lines = (run_dir / "manual_review.md").read_text(
        encoding="utf-8").splitlines()
    with STAGE.simulated_a_environment():
        regenerated = RA.manual_review_template(
            run_id=manifest["run_id"], generated_at=manifest["generated_at_utc"],
            stage="TS4-A", negative={}, fixture={}).splitlines()
    check(len(lines) >= 4 and regenerated[0] == lines[0]
          and regenerated[3] == lines[3],
          "B13 当前代码生成的 A 模板标题 / 阶段行必须与已封存 A 清单逐字一致（A 语义"
          f"不得被阶段化改动波及），得到 {regenerated[0]!r} / {regenerated[3]!r} vs "
          f"{lines[0]!r} / {lines[3]!r}")


# ---------------------------------------------------------------------------


def main() -> dict:
    _test_b1_attestation_gate()
    _test_b2_factor_table()
    _test_b3_threshold()
    _test_b4_self_reported_fingerprint()
    _test_b5_tampered_records()
    _test_b6_historical_a_readback()
    _test_b7_identity_separation()
    _test_b8_threshold_is_necessary_only()
    _test_b9_non_case_fixture_full_chain()
    _test_b10_a_artifacts_unchanged()
    _test_b11_overwrite_and_run_id()
    _test_b12_no_runtime_authority_read()
    _test_b13_self_check_keys_and_frozen_assets()
    _test_b13_historical_a_identity_readback()
    return _results


if __name__ == "__main__":
    _res = main()
    print(json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

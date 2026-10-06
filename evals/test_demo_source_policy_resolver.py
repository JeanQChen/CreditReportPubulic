"""Eval: M930-2 生产 SourcePolicyResolver（`harness.source_policy_resolver`）。

用法: python -m evals.test_demo_source_policy_resolver

覆盖：
- 真实冻结资产 `templates/policies/source_policy_v1.yaml` 正例：组合根装配一次，
  `resolve()` 返回 `FrozenSourcePolicySnapshot`，且经 `verify_frozen_source_policy`
  身份闭合；快照的 id/version/fingerprint 与资产正文**重算**值一致（不采信自报）；
- 资产级 fail-closed 反例：内容篡改（改业务字段但保留声明指纹）、未冻结资产、
  错误 policy_id（指纹已对齐，只留正式 validator 拦）、缺文件、不可解析 YAML，
  全部抛具名 `SourcePolicyAssetError` 且原因码正确；
- ref 级 dangling 反例：错误 policy_id / 错误 policy_version / 错误 fingerprint
  → `resolve()` 返回 None，且 `verify_frozen_source_policy` 继续拒绝（不静默放行）；
- `resolve()` 不接受路径或 dict（只接受 `SourcePolicyRef`），不在运行期重读磁盘：
  构造后篡改磁盘正文，已装配 resolver 的解析结果不变（不允许临时换资产）；
- 组合根错误码是封闭集合；模块源码不含公司 / 证券代码 / 固定页码专用规则。

不调 LLM、不联网、不写任何库；临时资产只写 `tempfile` 目录。
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import schema_v2 as S
from harness import source_policy_resolver as FSPR
from harness import topic_schema as TS

ASSET = Path("templates/policies/source_policy_v1.yaml")


def _dump(doc: dict) -> str:
    return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)


def _with_refingerprinted(doc: dict) -> str:
    """保留声明指纹与正文一致（用于隔离出「只有 validator 能拦」的反例）。"""
    doc = dict(doc)
    doc["content_sha256"] = S.content_fingerprint(doc)
    return _dump(doc)


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
                details.append(f"FAIL {msg}：错误类型正确但原因不符（{str(e)[:120]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:120]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    if not ASSET.exists():
        skipped += 1
        details.append(f"SKIP 冻结来源政策资产缺失（{ASSET}）")
        return {"passed": passed, "failed": failed, "skipped": skipped,
                "details": details}

    text = ASSET.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)

    # --- 正例：真实冻结资产 ------------------------------------------------
    resolver = FSPR.FrozenSourcePolicyResolver.from_asset(ASSET)
    snap = resolver.snapshot

    computed = S.content_fingerprint(doc)
    check(snap.content_fingerprint == computed,
          "快照指纹等于对资产正文重算的规范内容指纹")
    check(snap.content_fingerprint == doc.get("content_sha256"),
          "冻结资产声明指纹与重算指纹一致（资产本身自洽）")
    check(snap.policy_id == doc["policy_id"] and snap.policy_version == doc["policy_version"],
          "快照 policy_id/policy_version 取自资产正文，不由调用方传入")
    check(snap.key_industry_topics == tuple(doc["key_industry_topics"]),
          "key_industry_topics 由资产正文派生（非硬编码）")
    check(snap.key_industry_topics, "key_industry_topics 非空（不通过空名单关闭 sufficiency）")
    check(snap.key_conclusion_rule == TS.KEY_CONCLUSION_RULE
          and snap.key_conclusion_rule_version == TS.KEY_CONCLUSION_RULE_VERSION,
          "key_conclusion_rule(_version) 取自 schema 受信常量")

    ref = TS.SourcePolicyRef(policy_id=doc["policy_id"], policy_version=doc["policy_version"],
                             content_fingerprint=computed)
    resolved = resolver.resolve(ref)
    check(resolved is not None, "身份三项全等时 resolve 返回冻结投影")
    check(TS.verify_frozen_source_policy(ref, resolved) is resolved,
          "解析结果通过 verify_frozen_source_policy 身份闭合")
    check(resolver.identity_mismatch_reason(ref) is None,
          "身份一致时无 mismatch 原因码")
    check(resolver.identity()["content_fingerprint"] == computed
          and resolver.identity()["resolver_version"] == FSPR.RESOLVER_VERSION,
          "identity() 投影记录 resolver 版本与内容指纹")

    # --- ref 级 dangling：三项身份各自不一致 → None（不得返回最接近政策）----
    bad_refs = {
        "policy_id_mismatch": TS.SourcePolicyRef(
            policy_id="source_policy_v2", policy_version=doc["policy_version"],
            content_fingerprint=computed),
        "policy_version_mismatch": TS.SourcePolicyRef(
            policy_id=doc["policy_id"], policy_version="v2", content_fingerprint=computed),
        "content_fingerprint_mismatch": TS.SourcePolicyRef(
            policy_id=doc["policy_id"], policy_version=doc["policy_version"],
            content_fingerprint="0" * 64),
    }
    for expected_reason, bad_ref in bad_refs.items():
        check(resolver.resolve(bad_ref) is None,
              f"{expected_reason}：resolve 返回 None（不返回近似政策）")
        check(resolver.identity_mismatch_reason(bad_ref) == expected_reason,
              f"{expected_reason}：原因码正确")
        expect_error(lambda r=bad_ref: TS.verify_frozen_source_policy(r, resolver.resolve(r)),
                     TS.SchemaValidationError,
                     f"{expected_reason}：verify_frozen_source_policy 继续 fail-closed",
                     needle="无法解析")

    # --- resolve() 只接受 SourcePolicyRef ---------------------------------
    expect_error(lambda: resolver.resolve(str(ASSET)), TypeError,
                 "resolve() 拒绝文件路径", needle="SourcePolicyRef")
    expect_error(lambda: resolver.resolve(doc), TypeError,
                 "resolve() 拒绝原始 dict", needle="SourcePolicyRef")

    # --- 不允许运行期换资产：构造后改磁盘，已装配 resolver 不变 -------------
    with tempfile.TemporaryDirectory() as tmp:
        live = Path(tmp) / "source_policy_v1.yaml"
        live.write_text(text, encoding="utf-8")
        live_resolver = FSPR.FrozenSourcePolicyResolver.from_asset(live)
        tampered = dict(doc)
        tampered["transmission_note"] = "篡改后的说明"
        tampered["content_sha256"] = S.content_fingerprint(tampered)
        live.write_text(_dump(tampered), encoding="utf-8")
        again = live_resolver.resolve(ref)
        check(again is not None and again.content_fingerprint == computed,
              "构造后篡改磁盘不影响已装配 resolver（运行期不重读资产）")
        check(FSPR.FrozenSourcePolicyResolver.from_asset(live)
              .resolve(ref) is None,
              "用被篡改正文重新装配的 resolver 拒绝旧 ref（指纹已漂移）")

        # --- 资产级 fail-closed 反例 --------------------------------------
        cases: list[tuple[str, str, str]] = []

        drift = dict(doc)
        drift["freshness_windows_days"] = dict(drift["freshness_windows_days"])
        drift["freshness_windows_days"]["near_3m"] = 91  # 改业务字段，保留声明指纹
        cases.append(("asset_fingerprint_drift", _dump(drift), "内容篡改（声明指纹未更新）"))

        no_fp = dict(doc)
        no_fp.pop("content_sha256", None)
        cases.append(("asset_fingerprint_drift", _dump(no_fp), "frozen 资产缺 content_sha256"))

        unfrozen = dict(doc)
        unfrozen["status"] = "candidate"
        cases.append(("asset_not_frozen", _dump(unfrozen), "未冻结资产"))

        wrong_id = dict(doc)
        wrong_id["policy_id"] = "source_policy_v2"
        cases.append(("asset_invalid", _with_refingerprinted(wrong_id),
                      "错误 policy_id（指纹已对齐，只留正式 validator 拦）"))

        wrong_ver = dict(doc)
        wrong_ver["policy_version"] = "v9"
        wrong_ver["applies_to"] = dict(wrong_ver["applies_to"])
        cases.append(("asset_invalid", _with_refingerprinted(wrong_ver),
                      "错误 policy_version（指纹已对齐）"))

        no_topics = dict(doc)
        no_topics["key_industry_topics"] = []
        cases.append(("asset_invalid", _with_refingerprinted(no_topics),
                      "空 key_industry_topics（不得关闭 sufficiency）"))

        for reason, body, label in cases:
            p = Path(tmp) / "case.yaml"
            p.write_text(body, encoding="utf-8")
            expect_error(lambda p=p: FSPR.FrozenSourcePolicyResolver.from_asset(p),
                         FSPR.SourcePolicyAssetError, f"资产反例拒绝：{label}",
                         needle=reason)

        broken = Path(tmp) / "broken.yaml"
        broken.write_text("policy_id: [未闭合\n", encoding="utf-8")
        expect_error(lambda: FSPR.FrozenSourcePolicyResolver.from_asset(broken),
                     FSPR.SourcePolicyAssetError, "不可解析 YAML",
                     needle="asset_unreadable")

        expect_error(lambda: FSPR.FrozenSourcePolicyResolver.from_asset(
            Path(tmp) / "absent.yaml"),
            FSPR.SourcePolicyAssetError, "缺文件", needle="asset_missing")

    # --- 具名错误与硬编码纪律 ---------------------------------------------
    err = FSPR.SourcePolicyAssetError("asset_missing", "x")
    check(err.to_dict()["reason"] == "asset_missing"
          and err.to_dict()["resolver_version"] == FSPR.RESOLVER_VERSION,
          "SourcePolicyAssetError.to_dict 记录原因码与 resolver 版本")
    expect_error(lambda: FSPR.SourcePolicyAssetError("not_a_reason", "x"), ValueError,
                 "未知原因码被拒绝（错误码封闭集合）", needle="未知")

    check(set(FSPR.FrozenSourcePolicyResolver.__slots__) == {"_snapshot", "_source_classes"},
          "resolver 只持有冻结快照与来源类（不持有资产路径，运行期无法换资产）")

    src = Path(FSPR.__file__).read_text(encoding="utf-8")
    forbidden = ("300750", "CATL", "宁德", "宁德时代", "page_number =", "evidence_id =")
    check(not [w for w in forbidden if w in src],
          "生产 resolver 源码不含公司 / 证券代码 / 固定页码 / Evidence ID 专用规则")
    check(re.search(r'^ASSET_ERROR_REASONS = \(', src, re.M) is not None,
          "资产错误原因码以封闭元组声明（便于审计）")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["failed"] == 0 else 1)

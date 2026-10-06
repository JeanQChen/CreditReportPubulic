"""Eval: `claim-1` legacy 只读回放（§16.10 #25，M930-3A）。

用法: python -m evals.test_demo_claim_legacy_readback

证明：
 1. `claim-1` 载荷只经 `load_legacy_claim_claim1_for_audit` 还原为 `LegacySectionClaimV1`；
 2. `claim-2` current reader（`claim_from_dict`）**拒绝** `claim-1` / 缺 marker 的载荷；
 3. legacy reader **拒绝** `claim-2` 载荷（单向分流，不得 current 冒充 legacy）；
 4. 缺 `accepted_binding_ids` 的载荷**不得**被静默读成 current Claim；
 5. current `claim_to_dict` 不得宽松序列化 legacy 对象（legacy 只用自己的 `to_dict()`）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.schema import CitationRef  # noqa: E402
from sections import schema as SS  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


def expect_raises(msg: str, fn, needle: str) -> None:
    try:
        fn()
    except SS.SectionSchemaError as exc:
        if needle in str(exc):
            check(True, f"{msg}：{str(exc)[:70]}")
        else:
            check(False, f"{msg}：期望理由含 {needle!r}，实际 {str(exc)[:90]}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"{msg}：期望 SectionSchemaError，实际 {type(exc).__name__}: {exc}")
    else:
        check(False, f"{msg}：未 fail-closed")


def _ref() -> CitationRef:
    return CitationRef(ref_type="evidence", evidence_id="ev_legacy", page_number=7)


def _legacy_claim() -> SS.LegacySectionClaimV1:
    return SS.LegacySectionClaimV1(
        claim_id="c_legacy_1", section_id="company", topic_id="company_identity",
        question_ids=("company_subject_match",), text="主体一致",
        claim_type="fact", citation_refs=(_ref(),))


def _current_claim() -> SS.SectionClaim:
    ref = _ref()
    bindings = ("ab_1",)
    return SS.SectionClaim(
        claim_id=SS.derive_claim_id("fact", "company_identity", ("company_subject_match",),
                                    "主体一致", (ref,), "cc_1", "rev-1", bindings),
        schema_version=SS.CLAIM_SCHEMA_VERSION, section_id="company",
        topic_id="company_identity", question_ids=("company_subject_match",),
        text="主体一致", claim_type="fact", citation_refs=(ref,),
        claim_candidate_id="cc_1", claim_candidate_revision="rev-1",
        accepted_binding_ids=bindings)


def main() -> dict:
    # ------------------------------------------------------------------
    # 1. 登记的版本集：claim-1 是 legacy，claim-2 是 current。
    # ------------------------------------------------------------------
    check(SS.LEGACY_CLAIM_SCHEMA_VERSIONS == ("claim-1",)
          and SS.CLAIM_SCHEMA_VERSION == "claim-2"
          and SS.CLAIM_SCHEMA_VERSION not in SS.LEGACY_CLAIM_SCHEMA_VERSIONS
          and SS.LegacySectionClaimV1.LEGACY_SCHEMA_VERSION == "claim-1",
          "claim-1 为登记 legacy 版本、claim-2 为 current（不重叠）")

    # ------------------------------------------------------------------
    # 2. claim-1 载荷只读还原 + 往返。
    # ------------------------------------------------------------------
    legacy = _legacy_claim()
    legacy_doc = legacy.to_dict()
    check(legacy_doc["schema_version"] == "claim-1",
          "legacy claim 的 to_dict 自带 claim-1 marker")
    back = SS.load_legacy_claim_claim1_for_audit(legacy_doc)
    check(isinstance(back, SS.LegacySectionClaimV1)
          and not isinstance(back, SS.SectionClaim)
          and back.to_dict() == legacy_doc,
          "claim-1 载荷只经 legacy reader 还原且往返一致")
    check(not hasattr(back, "schema_version"),
          "legacy claim 视图没有 claim-2 的 schema_version 字段")

    # 缺 marker 的历史载荷同样按登记的 legacy 值只读回放（不猜版本）。
    no_marker = {k: v for k, v in legacy_doc.items() if k != "schema_version"}
    check(SS.load_legacy_claim_claim1_for_audit(no_marker).claim_id == legacy.claim_id,
          "缺 marker 的历史 claim 载荷仍按登记 legacy 值只读回放")

    # ------------------------------------------------------------------
    # 3. claim-2 current reader 拒绝 claim-1（一严一松反例）。
    # ------------------------------------------------------------------
    expect_raises("current claim reader 读 claim-1 载荷",
                  lambda: SS.claim_from_dict(legacy_doc), "claim-1")
    expect_raises("current claim reader 读缺 marker 载荷",
                  lambda: SS.claim_from_dict(no_marker), "claim-1")
    expect_raises("current claim reader 读缺 accepted_binding_ids 的 claim-2 形状载荷",
                  lambda: SS.claim_from_dict(
                      {**SS.claim_to_dict(_current_claim()),
                       "accepted_binding_ids": []}),
                  "accepted_binding_id")
    expect_raises("current claim reader 读缺 candidate revision 的 claim-2 形状载荷",
                  lambda: SS.claim_from_dict(
                      {k: v for k, v in SS.claim_to_dict(_current_claim()).items()
                       if k != "claim_candidate_revision"}),
                  "claim-2")
    expect_raises("current claim reader 读非对象载荷",
                  lambda: SS.claim_from_dict(json.dumps(legacy_doc)), "必须是对象")

    # ------------------------------------------------------------------
    # 4. legacy reader 拒绝 current claim-2 载荷（单向分流）。
    # ------------------------------------------------------------------
    current_doc = SS.claim_to_dict(_current_claim())
    expect_raises("legacy claim reader 读 claim-2 载荷",
                  lambda: SS.load_legacy_claim_claim1_for_audit(current_doc),
                  "不得以 legacy reader")
    expect_raises("legacy claim reader 读非对象载荷",
                  lambda: SS.load_legacy_claim_claim1_for_audit(json.dumps(current_doc)),
                  "必须是对象")

    # ------------------------------------------------------------------
    # 5. current serializer 不宽松序列化 legacy 对象。
    # ------------------------------------------------------------------
    expect_raises("current claim_to_dict 吃 legacy 对象",
                  lambda: SS.claim_to_dict(legacy), "legacy")

    return _results


if __name__ == "__main__":
    r = main()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["failed"] == 0 else 1)

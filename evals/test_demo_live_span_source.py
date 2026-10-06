"""Eval: M930-2 live 树能力组合根（`document_structure.live_span_source`）。

用法: python -m evals.test_demo_live_span_source

覆盖：
- 由**声明的** raw PDF 重建同次 live span 快照；identity 投影结构完整且**纯 JSON 值**
  （可持久化），运行时候选对象一律不可序列化 / 不可 copy / 不可 pickle（拒绝自证）；
- 声明身份闸门：磁盘哈希与声明不符（读盘即拒，不进链）、document_version 漂移、
  current Evidence set 漂移，全部 fail-closed；
- 身份投影与内容一致：每个 span_id 都在投影里，且投影里不含能力对象；
- `reprove_live_span_source` 只承认重走链且与冻结投影**逐分组相等**的重新证明，
  被改动的投影必须拒；
- `validate_identity_projection` 只做结构校验，缺组 / 非 live 域必须拒。

公司无关：样本路径取自 `data/samples/<company>/...` 的既有样例目录，不写证券代码特判；
样本缺失时**如实 skip**，不静默通过。不调 LLM、不联网、不写任何库。
"""

from __future__ import annotations

import copy
import json
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from document_structure import live_span_source as LSS
from document_structure import versions as V

SAMPLES = Path("data/samples")
PDF_GLOB = "*/announcements/*.pdf"


def _sample_pdf() -> Path | None:
    for path in sorted(SAMPLES.glob(PDF_GLOB)):
        return path
    return None


def _sha256_of(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


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

    def expect_rejected(fn, msg: str, *, needle: str) -> None:
        """只要**某一层**以该原因拒绝即算通过（更早的层拒绝比更晚的更好，不算缺陷）。"""
        nonlocal passed, failed
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            if needle in str(e):
                passed += 1
            else:
                failed += 1
                details.append(f"FAIL {msg}：被 {type(e).__name__} 拒绝但原因不符"
                               f"（{str(e)[:120]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    pdf = _sample_pdf()
    if pdf is None:
        skipped += 1
        details.append(f"SKIP 无 live 样本（{SAMPLES}/{PDF_GLOB}）")
        return {"passed": passed, "failed": failed, "skipped": skipped,
                "details": details}

    company = pdf.parents[1].name
    document_id = pdf.stem
    sha = _sha256_of(pdf)

    # 生产组合根要求 Evidence 库路径**由上层预检绑定**（等价于 service._prepare_stores：
    # 只读绑定，不 init、不迁移）。测试在这里扮演该角色，而不是改生产 API。
    evidence_db = Path("data/evidence.db")
    if not evidence_db.exists():
        skipped += 1
        details.append("SKIP 无 data/evidence.db（live 链需要既有 Evidence 库）")
        return {"passed": passed, "failed": failed, "skipped": skipped,
                "details": details}
    from evidence import store as estore
    estore._db_path = evidence_db.resolve()

    # --- 反例 1：声明哈希与磁盘不符 → 读盘即拒（不进链）-------------------
    def bad_sha():
        LSS.build_live_verified_span_snapshot(LSS.LiveSpanBuildRequest(
            company_id=company, document_id=document_id,
            document_version="sha256-" + sha[:16], raw_pdf_path=str(pdf),
            raw_pdf_sha256="0" * 64,
            expected_current_evidence_set_version="set-000000000000"))

    expect_error(bad_sha, LSS.LiveSpanSourceError, "声明哈希与磁盘不符必须拒",
                 needle="哈希与声明不符")

    # --- 反例 2：文件不存在 → fail-closed（不吞 OSError 当成"没材料"）-------
    def missing_pdf():
        LSS.build_live_verified_span_snapshot(LSS.LiveSpanBuildRequest(
            company_id=company, document_id=document_id,
            document_version="sha256-" + sha[:16],
            raw_pdf_path=str(pdf) + ".not-exist", raw_pdf_sha256=sha,
            expected_current_evidence_set_version="set-000000000000"))

    expect_error(missing_pdf, LSS.LiveSpanSourceError, "样本缺失必须 fail-closed",
                 needle="无法读取")

    # --- 反例 3：非请求对象 → 组合根不猜 --------------------------------
    expect_error(lambda: LSS.build_live_verified_span_snapshot({"company_id": company}),
                 LSS.LiveSpanSourceError, "非 LiveSpanBuildRequest 必须拒")

    # --- 正例：真实声明身份重走一次 live 链 ------------------------------
    base = LSS.LiveSpanBuildRequest(
        company_id=company, document_id=document_id,
        document_version="sha256-" + sha[:16], raw_pdf_path=str(pdf),
        raw_pdf_sha256=sha,
        expected_current_evidence_set_version=_live_evidence_set_version(company, pdf, sha))
    if base.expected_current_evidence_set_version == "":
        skipped += 1
        details.append("SKIP 无法从 Evidence DB 读出 current set 版本（不猜值）")
        return {"passed": passed, "failed": failed, "skipped": skipped,
                "details": details}

    source = LSS.build_live_verified_span_snapshot(base)
    projection = source.identity_projection()

    check(LSS.validate_identity_projection(projection)["ok"] is True,
          "真实签发得到的身份投影结构完整")
    check(json.dumps(projection, ensure_ascii=False, sort_keys=True) != "",
          "身份投影是纯 JSON 值（可持久化）")
    span_ids = list(projection["snapshot"]["span_ids"])
    check(span_ids and all(s.span_id in span_ids for s in source.spans()),
          "投影里的 span_id 覆盖全部真实 span")
    check(source.snapshot.snapshot_id == projection["snapshot"]["snapshot_id"],
          "投影的 snapshot_id 来自对象本身")
    check(projection["handoff"]["issuer_scope"] == LSS.LIVE_ISSUER_SCOPE,
          "投影声明 live 域")
    one_span = source.spans()[0]
    check(source.span_by_id(one_span.span_id).span_id == one_span.span_id,
          "span_by_id 回读同一 span")
    check(source.span_by_id("span-not-exist") is None,
          "未知 span_id 返回 None（不编造）")
    check(source.coverage_by_span_id("span-not-exist") is None,
          "未知 span_id 的覆盖查询返回 None（不编造）")
    check(source.snapshot.content_fingerprint
          == projection["snapshot"]["content_fingerprint"],
          "投影的内容指纹来自同一对象（不是另算一份）")

    # --- 反例 4：运行时候选对象不得自证 / 不得序列化 ----------------------
    for label, fn in (("to_dict", lambda: source.to_dict()),
                      ("copy", lambda: copy.copy(source)),
                      ("deepcopy", lambda: copy.deepcopy(source)),
                      ("pickle", lambda: pickle.dumps(source))):
        expect_error(fn, LSS.LiveSpanSourceError, f"LiveVerifiedSpanSource.{label} 必须拒")

    # --- 反例 5：投影结构校验只认完整 live 记录 ---------------------------
    expect_error(lambda: LSS.validate_identity_projection("not-a-dict"),
                 LSS.LiveSpanSourceError, "非对象投影必须拒")
    incomplete = {k: v for k, v in projection.items() if k != "handoff"}
    expect_error(lambda: LSS.validate_identity_projection(incomplete),
                 LSS.LiveSpanSourceError, "缺分组的投影必须拒", needle="缺字段")
    non_live = dict(projection)
    non_live["handoff"] = dict(projection["handoff"], issuer_scope="acceptance")
    expect_error(lambda: LSS.validate_identity_projection(non_live),
                 LSS.LiveSpanSourceError, "非 live 域投影必须拒", needle="不是 live 域")

    # --- 反例 6：被改动的冻结投影 → 重新证明必须拒（drift）----------------
    tampered = copy.deepcopy(projection)
    tampered["snapshot"]["snapshot_id"] = "snap-tampered"
    expect_error(lambda: LSS.reprove_live_span_source(base, tampered),
                 LSS.LiveSpanSourceError, "被改动的冻结投影必须拒",
                 needle="不一致")
    expect_error(lambda: LSS.reprove_live_span_source(base, {"request": {}}),
                 LSS.LiveSpanSourceError, "不完整的冻结投影必须拒", needle="缺必要分组")

    # --- 反例 7：声明身份漂移（都要重走链才能发现）------------------------
    drifted_version = LSS.LiveSpanBuildRequest(
        company_id=company, document_id=document_id,
        document_version="sha256-" + "0" * 16, raw_pdf_path=str(pdf),
        raw_pdf_sha256=sha,
        expected_current_evidence_set_version=base.expected_current_evidence_set_version)
    expect_rejected(lambda: LSS.build_live_verified_span_snapshot(drifted_version),
                    "document_version 漂移必须拒", needle="document_version")

    drifted_set = LSS.LiveSpanBuildRequest(
        company_id=company, document_id=document_id,
        document_version=base.document_version, raw_pdf_path=str(pdf),
        raw_pdf_sha256=sha, expected_current_evidence_set_version="set-000000000000")
    expect_error(lambda: LSS.build_live_verified_span_snapshot(drifted_set),
                 LSS.LiveSpanSourceError, "current Evidence set 漂移必须拒",
                 needle="Evidence set 漂移")

    identities = source.version_identities()
    check(identities.get("layout_schema_version") == V.LAYOUT_SCHEMA_VERSION
          and identities.get("span_snapshot_schema_version")
          == V.SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION,
          "版本身份由对象自身读出且等于权威常量")
    check(projection["versions"] == identities,
          "投影记录的版本与对象自身读出的一致")

    return {"passed": passed, "failed": failed, "skipped": skipped,
            "details": details}


def _live_evidence_set_version(company: str, pdf: Path, sha: str) -> str:
    """从既有 Evidence 库**只读**读出该文档的 current set 版本（不猜、不 init、不迁移）。"""
    from evidence import store as estore
    db = Path("data/evidence.db")
    if not db.exists():
        return ""
    try:
        version = estore.current_evidence_set_ro(
            db.resolve(), company, pdf.stem, "sha256-" + sha[:16])
    except Exception:  # noqa: BLE001  环境不具备时如实 skip，不伪造
        return ""
    return version if isinstance(version, str) else ""


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["failed"] == 0 else 1)

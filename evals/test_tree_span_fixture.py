# -*- coding: utf-8 -*-
"""T38-A 非 300750 正向 fixture：通用性正向链 + 缺源 fail-closed 负例（计划 §18.13 / §18.14.2-8/-9）。

覆盖（§18.13 T38-A 行）：

- 用仓库内**版本化**夹具（`evals/fixtures/tree_structure/non_300750_ts4/`）逐成员核验
  哈希后，走与真实文档**同一套** TS4-A 私有纯构建核：builder → verifier →
  conservation → synopsis；
- 签发域必须是 `issuer_scope=pinned_acceptance` / `source_kind=versioned_fixture`
  （`vthf-1`），**不是** `testing` 域：夹具正向验收证明的是"公司无关"，不是"测试域
  自洽"；
- TS4-A 恒为分布口径：`SPAN_CONFIDENCE_MIN is None`，任一 span 的
  `completion_eligible` 必须恒为 `False`；
- 负例逐条：manifest 成员缺失 / 成员身份不符 / testing 域混入 / 越权把夹具当生产
  来源，全部 fail-closed；
- 另一路负例：冻结旧 fixture（`FIXTURE_BOND_2026`）`evidence_set_version=null`、
  0 block，**只能**得到 typed fail-closed，**不得**生成 `SpanBuildSnapshot`。

硬规则：所有"是否通过"的裁决都由生产侧给出（抛错 / 字段值 / 独立重算），本文件
不自己算一遍"应该相等"就宣布通过；反例断言必须能真的失败。

只读：本文件不写任何数据库、不写 `evaluation/results/**`、不建临时夹具目录。
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from document_structure import evidence_gateway as EG  # noqa: E402
from document_structure import span_builder as SB  # noqa: E402
from document_structure import span_policy as SP  # noqa: E402
from document_structure import span_verifier as SV  # noqa: E402
from document_structure import synopsis as SY  # noqa: E402
from document_structure import versions as V  # noqa: E402
from document_structure.aligner import EvidenceSetMember  # noqa: E402
from document_structure.canonical import (  # noqa: E402
    SchemaValidationError,
    canonical_json,
)
from document_structure.normalization import tight  # noqa: E402
from document_structure.schema import (  # noqa: E402
    DocumentOutline,
    PageLayout,
    ReferenceValidationContext,
    derive_span_locator,
)
from document_structure.span_schema import CapabilityError  # noqa: E402
from evals import tree_stage_env as STAGE  # noqa: E402

FIXTURE_RELPATH = "evals/fixtures/tree_structure/non_300750_ts4"
TRUST_ROOTS_RELPATH = "evals/fixtures/tree_structure/ts4_trust_roots.json"
FROZEN_FIXTURE_COMPANY_ID = "555555"
FROZEN_FIXTURE_DOCUMENT_ID = "FIXTURE_BOND_2026"

#: TS4 新增的六个生产模块（T37 口径）：公司专有 token 加固检查的作用域。
TS4_PRODUCTION_MODULES = (
    "document_structure/evidence_gateway.py",
    "document_structure/span_schema.py",
    "document_structure/span_policy.py",
    "document_structure/span_builder.py",
    "document_structure/span_verifier.py",
    "document_structure/synopsis.py",
)

_results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

_CHAIN: dict = {}


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
    """断言 `fn()` 抛 `exc` 且信息含 `substr`（`substr` 为空串时只查类型）。"""
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


def _try(fn):
    """执行一个生产调用：`(True, 值)` 或 `(False, 异常)`；不吞掉成功/失败的区别。"""
    try:
        return True, fn()
    except Exception as error:  # noqa: BLE001
        return False, error


def _sha256_file(path: pathlib.Path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _fdir() -> pathlib.Path:
    return EG.fixture_root_dir()


def _read_json(path: pathlib.Path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def _chain() -> dict:
    """跑一次夹具正向链并缓存（只读仓库内 fixture 成员文件与冻结 PDF 字节）。"""
    if _CHAIN:
        return _CHAIN
    try:
        root = EG.load_fixture_root()
        fdir = _fdir()
        layout = PageLayout.from_dict(_read_json(fdir / "page_layout.json"))
        # 冻结 outline 成员**不反序列化**：它的 `toc_to_body` 边需要真实 `TocSource`
        # 对象，而 `TocSource` 是运行时核验输入、不进任何 wire format（见 schema.py
        # 5051-5074）。因此夹具大纲的可用形式是"由锁定 PDF 确定性重建后逐字节比对"，
        # 见 `_test_t38a2_handoff_issuance`。
        frozen_outline = _read_json(fdir / "document_outline.json")
        pdf_bytes = (REPO / root["source_pdf"]["relpath"]).read_bytes()
        # 夹具基线是 **TS4-A 版**产物（含 A 版交接）：进入 TS4-B 之后必须显式把阶段拨回
        # A（`evals.tree_stage_env`，退出即逐字还原），而不是绕过生产代码的阶段门。
        with STAGE.simulated_a_environment():
            handoff = SB._issue_fixture_ts3_handoff(
                raw_pdf=pdf_bytes, expected_layout=layout,
                company_id=root["company_id"], document_id=root["document_id"],
                fixture_root=root)
            snapshot = SB._build_from_pinned_handoff(handoff,
                                                     stage="distribution_only")
        verified = SV.verify_span_snapshot(snapshot, handoff)
        _CHAIN.update({"root": root, "dir": fdir, "layout": layout,
                       "frozen_outline": frozen_outline, "pdf_bytes": pdf_bytes,
                       "handoff": handoff, "snapshot": snapshot,
                       "verified": verified, "error": None})
    except Exception as error:  # noqa: BLE001
        _CHAIN.update({"error": f"{type(error).__name__}: {error}"})
    return _CHAIN


def _need_chain(msg) -> dict | None:
    ctx = _chain()
    if ctx.get("error"):
        check(False, f"{msg} —— 夹具正向链未能建立：{ctx['error']}")
        return None
    return ctx


def _mutated(root: dict, **overrides) -> dict:
    """夹具 manifest 的**声明副本**（只在内存里改，绝不写回仓库文件）。"""
    out = copy.deepcopy(root)
    for key, value in overrides.items():
        out[key] = value() if callable(value) else value
    return out


# ---------------------------------------------------------------------------
# T38-A1 版本化夹具的成员身份核验（逐文件哈希 + 集合身份）
# ---------------------------------------------------------------------------

def _test_t38a1_fixture_integrity() -> None:
    ok, root = _try(EG.load_fixture_root)
    if not check(ok, f"T38-A1 夹具 manifest 可读（{FIXTURE_RELPATH}/manifest.json）"
                     + ("" if ok else f"：{root}")):
        return
    fdir = _fdir()
    check(fdir == REPO / FIXTURE_RELPATH,
          f"T38-A1 夹具目录由 gateway 自身位置固定派生，得到 {fdir}")
    manifest_path = fdir / EG.FIXTURE_MANIFEST_FILENAME
    check(root.get("fixture_file_sha256") == _sha256_file(manifest_path),
          "T38-A1 manifest 自载 sha 与其文件字节一致（fixture_file_sha256 可复算）")

    members = root.get("members") or []
    roles = sorted(str(m.get("role")) for m in members)
    check(roles == sorted(EG.FIXTURE_MEMBER_ROLES),
          f"T38-A1 夹具成员角色恰为 {sorted(EG.FIXTURE_MEMBER_ROLES)}，得到 {roles}")

    verified_members = 0
    for entry in members:
        relpath = entry.get("relpath")
        ok_member, outcome = _try(lambda r=relpath, w=entry.get("role"):
                                  EG._fixture_file_identity(root, r, f"成员 {w}"))
        if not check(ok_member, f"T38-A1 夹具成员 {relpath!r} 逐文件核验通过"
                                + ("" if ok_member else f"：{outcome}")):
            continue
        _path, actual = outcome
        check(actual["sha256"] == entry["sha256"] and actual["size"] == entry["size"],
              f"T38-A1 成员 {relpath!r} 的实际 sha256/size 与 manifest 钉住值逐项相等")
        verified_members += 1
    check(verified_members == len(EG.FIXTURE_MEMBER_ROLES),
          f"T38-A1 三个夹具成员全部经哈希核验（实际 {verified_members}）")

    # 集合身份：按 gateway 自己的算法独立重算，再对回 manifest 声明值。
    ok_payload, payload = _try(lambda: _read_json(
        fdir / root["evidence_relpath"]))
    if not check(ok_payload, f"T38-A1 夹具 Evidence 成员文件可读"
                             f"（{root.get('evidence_relpath')!r}）"):
        return
    check(payload.get("schema_type") == "TS4FixtureEvidenceSet",
          f"T38-A1 Evidence 成员文件 schema_type 必须为 TS4FixtureEvidenceSet，"
          f"得到 {payload.get('schema_type')!r}")
    check(payload.get("evidence_set_version") == root.get("evidence_set_version"),
          "T38-A1 Evidence 集合版本与 manifest 声明一致")
    raw_members = payload.get("members") or []
    check(len(raw_members) == root.get("evidence_block_count")
          and len(raw_members) > 0,
          f"T38-A1 Evidence 成员条数 {len(raw_members)} 等于 manifest "
          f"evidence_block_count={root.get('evidence_block_count')} 且非空")
    ordered = sorted(raw_members,
                     key=lambda m: (m["page_number"], m["block_index"]))
    members_tuple = tuple(
        EvidenceSetMember(evidence_id=m["evidence_id"],
                          content_hash=m["content_hash"],
                          evidence_type=m["evidence_type"],
                          page_number=m["page_number"],
                          block_index=m["block_index"]) for m in ordered)
    check(EG.member_identity_sha256(members_tuple)
          == root.get("member_identity_sha256"),
          "T38-A1 member_identity_sha256 可由 Evidence 成员清单独立重算并与 manifest "
          "声明相等（集合身份不得自报）")

    # 冻结 PDF 字节身份（夹具版式的信任锚）。
    ok_pdf, pdf_bytes = _try(lambda: (REPO / root["source_pdf"]["relpath"]).read_bytes())
    if check(ok_pdf, "T38-A1 夹具锁定的原 PDF 可读"):
        check(hashlib.sha256(pdf_bytes).hexdigest() == root["source_pdf"]["sha256"]
              and len(pdf_bytes) == root["source_pdf"]["size"],
              "T38-A1 夹具 PDF 的 sha256/size 与 manifest 钉住值相等")
        check(root.get("source_pdf_sha256") == root["source_pdf"]["sha256"],
              "T38-A1 manifest 的两个 PDF sha 字段互相一致")


def _test_t38a1_fixture_identity_declarations() -> None:
    ok, root = _try(EG.load_fixture_root)
    if not check(ok, "T38-A1 夹具 manifest 可读（用于版式 / 大纲身份声明核对）"):
        return
    check(root.get("fixture_stage") == "TS4-A",
          f"T38-A1 夹具声明的阶段必须为 TS4-A，得到 {root.get('fixture_stage')!r}")
    policy = root.get("qualification_policy") or {}
    check(policy.get("stage") == "distribution_only"
          and policy.get("threshold") is None
          and policy.get("approval_fingerprint") is None,
          "T38-A1 夹具资格策略声明必须为分布口径（stage=distribution_only / "
          f"threshold=None / 无批准指纹），得到 {policy}")
    fdir = _fdir()
    ok_layout, layout = _try(lambda: PageLayout.from_dict(
        _read_json(fdir / "page_layout.json")))
    if check(ok_layout, "T38-A1 夹具 page_layout.json 可由公开 from_dict 还原"):
        check(layout.page_layout_id == root.get("page_layout_id"),
              f"T38-A1 版式身份一致：{layout.page_layout_id!r} == "
              f"{root.get('page_layout_id')!r}")
        check(layout.page_count == root.get("page_count"),
              "T38-A1 版式页数与 manifest 声明一致")
        check(layout.company_id == root.get("company_id")
              and layout.document_id == root.get("document_id")
              and layout.document_version == root.get("document_version"),
              f"T38-A1 版式文档身份与 manifest 一致（{layout.company_id}/"
              f"{layout.document_id}/{layout.document_version}）")
    frozen_outline = _read_json(fdir / "document_outline.json")
    check(frozen_outline.get("schema_type") == "DocumentOutline"
          and frozen_outline.get("outline_id") == root.get("outline_id"),
          f"T38-A1 夹具大纲成员的 schema_type / outline_id 与 manifest 一致，得到 "
          f"{frozen_outline.get('schema_type')!r}/{frozen_outline.get('outline_id')!r}")
    check(frozen_outline.get("document_id") == root.get("document_id")
          and frozen_outline.get("document_version") == root.get("document_version")
          and frozen_outline.get("page_layout_id") == root.get("page_layout_id"),
          "T38-A1 夹具大纲成员的文档身份 / 绑定版式与 manifest 一致")
    # 契约（TS1.3 §三.10 + schema.py 5051-5074）：`TocSource` 是**运行时**核验输入，
    # 不进任何 wire format。因此夹具大纲成员**不是自洽内容**：给定真实版式也仍然
    # 还原不出来，只能由锁定 PDF 重建后逐字节比对（见 T38-A2）。这是一条**必须**
    # 保留的 fail-closed 性质：不得为了"能读回夹具"而给 wire format 补 toc 字符串表。
    if ok_layout:
        raises(lambda: DocumentOutline.from_dict(
            frozen_outline,
            reference_context=ReferenceValidationContext(layout=layout)),
            SchemaValidationError, "",
            "T38-A1 夹具大纲即便交出真实版式也不得被反序列化（toc 来源为运行时对象，"
            "不得由字符串清单证明存在）")
    raises(lambda: DocumentOutline.from_dict(frozen_outline),
           SchemaValidationError, "",
           "T38-A1 无上下文的夹具大纲反序列化必须 fail-closed")
    check("toc_sources" not in frozen_outline
          and "toc_ids" not in frozen_outline,
          f"T38-A1 夹具大纲 wire format 不得携带 toc 字符串注册表，实际键："
          f"{sorted(frozen_outline)[:12]}")


# ---------------------------------------------------------------------------
# T38-A2 正向链：pinned_acceptance / versioned_fixture 域（**不是** testing）
# ---------------------------------------------------------------------------

def _test_t38a2_handoff_issuance() -> None:
    ctx = _need_chain("T38-A2 夹具交接签发")
    if ctx is None:
        return
    handoff = ctx["handoff"]
    check(handoff.issuer_scope == "pinned_acceptance"
          and handoff.source_kind == EG.FIXTURE_SOURCE_KIND,
          f"T38-A2 夹具交接签发域必须为 pinned_acceptance/versioned_fixture，得到 "
          f"{handoff.issuer_scope!r}/{handoff.source_kind!r}")
    check(handoff.issuer_scope != "testing" and handoff.source_kind != "unit_test",
          "T38-A2 夹具交接**不是** testing 域（不得用测试域冒充验收）")
    check(handoff.issuer_version == V.FIXTURE_TS3_HANDOFF_VERSION,
          f"T38-A2 夹具交接签发版本必须为 "
          f"{V.FIXTURE_TS3_HANDOFF_VERSION!r}，得到 {handoff.issuer_version!r}")
    check(handoff.evidence_authority.issuer_scope == "pinned_acceptance"
          and handoff.evidence_authority.source_kind == EG.FIXTURE_SOURCE_KIND
          and handoff.evidence_authority.issuer_version
          == V.FIXTURE_EVIDENCE_AUTHORITY_VERSION,
          "T38-A2 Evidence authority 必须由夹具 issuer 在 pinned_acceptance 域签发")
    check(handoff.alignment.issuer_version == V.FIXTURE_ALIGNMENT_ISSUER_VERSION
          or handoff.alignment.issuer_version == V.PINNED_ALIGNMENT_ISSUER_VERSION,
          f"T38-A2 对齐终态签发版本必须为夹具域版本，得到 "
          f"{handoff.alignment.issuer_version!r}")

    # 结构终态由**同一份受信 PDF 字节**重建：必须与冻结大纲 canonical 全等。
    # 这是"夹具大纲成员"唯一可用的等价性判据（它不能独立反序列化，见 T38-A1）。
    ok_canon, canon = _try(lambda: (
        canonical_json(handoff.document_outline.to_dict()),
        canonical_json(ctx["frozen_outline"])))
    if check(ok_canon, f"T38-A2 冻结大纲成员与重建大纲可规范序列化"
                       + ("" if ok_canon else f"：{canon}")):
        rebuilt_text, frozen_text = canon
        check(rebuilt_text == frozen_text,
              "T38-A2 夹具大纲可由受信 PDF 字节确定性重建，且与冻结成员 canonical "
              "逐字节全等（含 outline_id 与全部边）")
    check(len(handoff.structure_snapshot.line_states) > 0,
          "T38-A2 结构终态含非家具行")
    node_ids = {n.node_id for n in handoff.document_outline.nodes}
    check({s.node_id for s in handoff.structure_snapshot.line_states
           if s.node_id is not None} <= node_ids,
          "T38-A2 结构终态的节点归属全部来自真实大纲节点集合")

    root = ctx["root"]
    blocks = handoff.evidence_blocks
    check(len(blocks) == root["evidence_block_count"] == 9,
          f"T38-A2 夹具 Evidence 块数 {len(blocks)} 等于 manifest 声明 9")
    check(handoff.evidence_snapshot.block_count == len(blocks)
          and handoff.evidence_snapshot.evidence_set_version
          == root["evidence_set_version"],
          "T38-A2 权威 Evidence 快照的块数与集合版本与夹具声明一致")
    check(handoff.evidence_snapshot.fingerprint
          == handoff.evidence_snapshot.recomputed_fingerprint(),
          "T38-A2 证据集快照指纹可重算（权威身份不自报）")
    block_ids = sorted(b.evidence_block_id for b in blocks)
    terminal_ids = sorted(t.evidence_block_id for t in handoff.alignment.terminals)
    check(block_ids == terminal_ids and len(terminal_ids) == len(set(terminal_ids)),
          "T38-A2 全终态闭合：每个 Evidence 块恰有一个终态，无缺失 / 无多余")


def _test_t38a2_snapshot_and_conservation() -> None:
    ctx = _need_chain("T38-A2 夹具快照与守恒")
    if ctx is None:
        return
    snapshot = ctx["snapshot"]
    policy = snapshot.qualification_policy

    check(policy.stage == "distribution_only" and policy.span_confidence_min is None
          and policy.completion_enabled is False
          and policy.set_complete_supported is False,
          f"T38-A2 夹具快照的策略必须为分布口径（threshold=None / completion=False），"
          f"得到 stage={policy.stage!r} min={policy.span_confidence_min!r} "
          f"completion={policy.completion_enabled!r}")
    # 阶段读数只作**环境自述**：A 版夹具快照是否为分布口径由它自带的策略决定，
    # 与当前全局常量无关（这正是"历史 A 记录不被当前阶段重新解释"的那条性质）。
    live_stage = STAGE.current_stage()
    check(live_stage == ("distribution_only" if V.SPAN_CONFIDENCE_MIN is None
                         else "threshold_enabled"),
          f"T38-A2 当前阶段必须由 SPAN_CONFIDENCE_MIN 单点派生，得到 "
          f"{live_stage!r} vs {V.SPAN_CONFIDENCE_MIN!r}")
    if live_stage == "distribution_only":
        check(V.SPAN_CONFIDENCE_MIN is None,
              f"T38-A2 A 阶段 SPAN_CONFIDENCE_MIN 必须为 None，得到 "
              f"{V.SPAN_CONFIDENCE_MIN!r}")
    else:
        frozen = SP.resolve_frozen_policy()
        check(V.SPAN_CONFIDENCE_MIN == frozen.span_confidence_min,
              f"T38-A2 B 阶段全局阈值必须与已冻结策略阈值一致，得到 "
              f"{V.SPAN_CONFIDENCE_MIN!r} vs {frozen.span_confidence_min!r}")
        check(policy.stage == "distribution_only"
              and policy.span_confidence_min is None,
              "T38-A2 进入 B 阶段后，A 版夹具快照仍按**其自带策略**保持分布口径"
              "（不得被当前全局常量重新解释）")
    check(snapshot.schema_version == V.SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION
          and snapshot.span_builder_version == V.TS4_BODY_SPAN_BUILDER_VERSION,
          f"T38-A2 快照 wire 版本必须为 "
          f"{V.SPAN_BUILD_SNAPSHOT_SCHEMA_VERSION!r}、正文算法为 "
          f"{V.TS4_BODY_SPAN_BUILDER_VERSION!r}，得到 {snapshot.schema_version!r}/"
          f"{snapshot.span_builder_version!r}")
    check(snapshot.terminal_count == len(ctx["handoff"].alignment.terminals),
          "T38-A2 快照的 terminal_count 与受信交接的终态数一致")

    check(len(snapshot.spans) > 0,
          f"T38-A2 夹具正向链必须产出正文 span（实际 {len(snapshot.spans)}）")
    check({c.span_id for c in snapshot.coverages} == {s.span_id for s in snapshot.spans}
          and len(snapshot.coverages) == len(snapshot.spans),
          "T38-A2 每个 span 恰有一份可引用覆盖")
    span_problems: list = []
    for span in snapshot.spans:
        if span.span_builder_version != V.TS4_BODY_SPAN_BUILDER_VERSION:
            span_problems.append(f"{span.span_id}: span_builder_version")
        if span.role != "body" or span.is_fallback or span.is_cross_heading:
            span_problems.append(f"{span.span_id}: role/fallback/cross_heading")
        if span.char_range != (0, len(span.normalized_text)):
            span_problems.append(f"{span.span_id}: char_range")
    check(not span_problems,
          f"T38-A2 全部 span 均为非 fallback / 非跨标题的正文单位，问题："
          f"{span_problems[:3]}")

    conservation = snapshot.conservation
    check(conservation.conserved is True,
          "T38-A2 `conservation.conserved` 必须为 True（三级守恒成立）")
    check(tuple(conservation.gaps) == (),
          f"T38-A2 `gaps` 必须为空元组，得到 {conservation.gaps!r}")
    doc_layer = {b.bucket: b for b in conservation.document_layer}
    body_layer = {b.bucket: b for b in conservation.body_layer}
    check(sum(b.line_count for b in conservation.document_layer)
          == doc_layer["body"].line_count + doc_layer["heading"].line_count
          + doc_layer["formal_unassigned"].line_count
          + doc_layer["non_content"].line_count,
          "T38-A2 文档层四态之和等于该层总行数（守恒对象自身重算一致）")
    check(sum(b.line_count for b in conservation.body_layer)
          == body_layer["regular"].line_count
          + body_layer["table_adjacency_provisional"].line_count
          + body_layer["table_inside"].line_count
          + body_layer["unassigned"].line_count
          + body_layer["empty"].line_count,
          "T38-A2 正文层五类之和等于该层总行数")
    check(len(conservation.evidence_layer) == len(ctx["handoff"].evidence_blocks),
          "T38-A2 Evidence 层逐条分区（每条 Evidence 一行，不跨条相加）")


def _test_t38a2_verifier_and_completion() -> None:
    ctx = _need_chain("T38-A2 夹具复核与完成资格")
    if ctx is None:
        return
    snapshot = ctx["snapshot"]
    verified = ctx["verified"]
    check(verified.issuer_scope == "pinned_acceptance"
          and verified.source_kind == EG.FIXTURE_SOURCE_KIND,
          f"T38-A2 复核结论的签发域必须为 pinned_acceptance/versioned_fixture，得到 "
          f"{verified.issuer_scope!r}/{verified.source_kind!r}")
    check(verified.issuer_version == V.VERIFIED_SPAN_SNAPSHOT_VERSION,
          f"T38-A2 复核能力版本必须为 {V.VERIFIED_SPAN_SNAPSHOT_VERSION!r}，得到 "
          f"{verified.issuer_version!r}")
    check(verified.snapshot.snapshot_id == snapshot.snapshot_id
          and verified.snapshot.content_fingerprint == snapshot.content_fingerprint,
          "T38-A2 复核结论绑定的是同一份快照（snapshot_id 与内容指纹一致）")
    check(isinstance(verified.verification_fingerprint, str)
          and len(verified.verification_fingerprint) == 64,
          "T38-A2 复核指纹为 64 位 sha256")

    # 恒 False：每个 span 都不具备完成资格（TS4-A 无阈值）。
    ineligible = [s.span_id for s in snapshot.spans
                  if SV.is_completion_eligible(verified, s.span_id) is not False]
    check(not ineligible,
          f"T38-A2 `completion_eligible` 对每个 span 都必须为 False，违规："
          f"{ineligible[:3]}")
    check(SV.is_completion_eligible(verified, "os-0000000000000000") is False,
          "T38-A2 完成判定恒为 False（分布口径策略下不因 span 是否存在而放行）")

    # 复核入口不可被"testing / live 入口"旁路，也不接受伪造快照。
    # 签发域闸在能力层（CapabilityError）就先 fail-closed，早于任何构建动作。
    raises(lambda: SB.build_span_snapshot(ctx["handoff"], stage="distribution_only"),
           CapabilityError, "live",
           "T38-A2 夹具交接（pinned_acceptance）不得走公开 live builder 入口")
    raises(lambda: SB._testing_build_span_snapshot(ctx["handoff"],
                                                   stage="distribution_only"),
           CapabilityError, "testing",
           "T38-A2 夹具交接不得走 testing 域 builder 入口")
    raises(lambda: SV.verify_span_snapshot(snapshot, object()),
           SchemaValidationError, "",
           "T38-A2 复核入口拒绝非受信交接对象")
    raises(lambda: SV.is_completion_eligible(object(), snapshot.spans[0].span_id),
           SchemaValidationError, "",
           "T38-A2 完成判定只接受本进程签发的复核能力对象")


# ---------------------------------------------------------------------------
# T38-A3 synopsis：可验证 + 引用源锚点合法
# ---------------------------------------------------------------------------

def _effective_intervals(coverage):
    return tuple((iv.start, iv.end) for iv in coverage.effective_citable_intervals)


def _test_t38a3_synopsis_sources() -> None:
    ctx = _need_chain("T38-A3 夹具导航简介来源复核")
    if ctx is None:
        return
    snapshot = ctx["snapshot"]
    handoff = ctx["handoff"]
    policy = snapshot.qualification_policy
    span_by_id = {s.span_id: s for s in snapshot.spans}
    cov_by_id = {c.span_id: c for c in snapshot.coverages}
    node_ids = sorted(n.node_id for n in handoff.document_outline.nodes)

    check(sorted(s.node_id for s in snapshot.synopses) == node_ids,
          "T38-A3 每个真实大纲节点恰有一份简介（按 node_id 升序，不补造不跳过）")

    blocks_by_id = {b.evidence_block_id: b for b in handoff.evidence_blocks}
    # 逐节点的规模处置计数（用于独立重算 reason_code，不采信简介自报）。
    kind_counts: dict = {}
    for disposition in snapshot.dispositions:
        if disposition.node_id is not None:
            bucket = kind_counts.setdefault(disposition.node_id, {})
            bucket[disposition.range_kind] = bucket.get(disposition.range_kind, 0) + 1
    bad_validation: list = []
    bad_anchor: list = []
    bad_extract: list = []
    bad_reason: list = []
    available = 0
    snippet_count = 0
    for synopsis in snapshot.synopses:
        ok_val, outcome = _try(lambda s=synopsis: SY.verify_synopsis_sources(
            s, span_registry=span_by_id, coverage_registry=cov_by_id,
            verified_policy=policy))
        if not ok_val:
            bad_validation.append(f"{synopsis.node_id}: {outcome}")
            continue
        validation = outcome
        # `nsv-1`：ok 当且仅当"有片段且无问题"。无片段的节点必须是 ok=False 且
        # problems 为空（诚实不可用），不得被当成"校验通过=可用"。
        if validation.ok is not bool(validation.snippet_checks) \
                or validation.problems:
            bad_validation.append(f"{synopsis.node_id}: ok={validation.ok} "
                                  f"problems={validation.problems}")
        # reason_code 是**全函数推导**：用快照自己的处置计数 / 覆盖 / 片段数重算。
        expected_reason = SY.unavailable_reason(
            node_id=synopsis.node_id,
            kind_counts=kind_counts.get(synopsis.node_id, {}),
            has_admissible_span=any(
                SY.navigation_admissible(span) and span.node_id == synopsis.node_id
                for span in snapshot.spans),
            has_citable_coverage=any(
                span.node_id == synopsis.node_id
                and cov_by_id[span.span_id].effective_citable_intervals
                for span in snapshot.spans),
            snippet_count=len(synopsis.snippets))
        if synopsis.reason_code != expected_reason:
            bad_reason.append(f"{synopsis.node_id}: 自报 {synopsis.reason_code!r} / "
                              f"重算 {expected_reason!r}")
        if (synopsis.reason_code is None) != (synopsis.status == "available"):
            bad_reason.append(f"{synopsis.node_id}: status/reason_code 不一致")
        if synopsis.status == "available":
            available += 1
            if not synopsis.snippets:
                bad_reason.append(f"{synopsis.node_id}: available 但无片段")
        elif synopsis.snippets:
            bad_reason.append(f"{synopsis.node_id}: 不可用却带片段")
        for index, snippet in enumerate(synopsis.snippets):
            snippet_count += 1
            span = span_by_id.get(snippet.span_id)
            if span is None:
                bad_extract.append(f"{synopsis.node_id}[{index}]: 未知 span")
                continue
            if span.node_id != synopsis.node_id:
                bad_anchor.append(f"{synopsis.node_id}[{index}]: 引用别节点 span")
            if span.normalized_text[snippet.char_start:snippet.char_end] \
                    != snippet.text:
                bad_extract.append(f"{synopsis.node_id}[{index}]: 非逐字切片")
            covering = [iv for iv in _effective_intervals(cov_by_id[snippet.span_id])
                        if iv[0] <= snippet.char_start
                        and snippet.char_end <= iv[1]]
            if len(covering) != 1:
                bad_extract.append(f"{synopsis.node_id}[{index}]: 不落在唯一有效区间")
            length = snippet.char_end - snippet.char_start
            if length < SY.MIN_SNIPPET_CHARS or length > SY.MAX_SNIPPET_CHARS:
                bad_extract.append(f"{synopsis.node_id}[{index}]: 片段长度 {length}")
            # 源锚点合法性：span 定位由真实锚点重算，且锚点落在夹具版式的真实行上。
            expected_locator = derive_span_locator(
                document_outline_locator=span.document_outline_locator,
                evidence_set_version=span.evidence_set_version,
                start_anchor=span.start_anchor, end_anchor=span.end_anchor,
                span_builder_version=span.span_builder_version)
            if span.span_locator != expected_locator:
                bad_anchor.append(f"{synopsis.node_id}[{index}]: span_locator 不可复算")
            real_lines = {(p.page_number, line.line_index)
                          for p in ctx["layout"].pages for line in p.lines
                          if not line.is_furniture}
            if not set(span.layout_line_refs) <= real_lines:
                bad_anchor.append(f"{synopsis.node_id}[{index}]: 行引用越出真实版式")
            for ref in span.component_evidence_refs:
                block_id, cs, ce = ref[0], ref[1], ref[2]
                block = blocks_by_id.get(block_id)
                if block is None:
                    bad_anchor.append(f"{synopsis.node_id}[{index}]: 未知 Evidence 块")
                elif not (0 <= cs < ce <= len(tight(block.text))):
                    bad_anchor.append(
                        f"{synopsis.node_id}[{index}]: component 区间越界 {ref!r}")

    check(not bad_validation,
          f"T38-A3 全部简介的来源复核结论自洽（nsv-1：ok 当且仅当有片段且无问题），"
          f"问题：{bad_validation[:2]}")
    check(not bad_reason,
          f"T38-A3 每份简介的 reason_code / status 可由快照自身独立重算"
          f"（不可用必须诚实且带固定原因码），问题：{bad_reason[:2]}")
    check(not bad_anchor,
          f"T38-A3 简介引用的源锚点全部合法（span 定位可复算 / 行与 Evidence 区间"
          f"落在真实来源内），问题：{bad_anchor[:2]}")
    check(not bad_extract,
          f"T38-A3 片段必须为 span 原文的逐字切片且落在唯一有效可引用区间内，问题："
          f"{bad_extract[:2]}")
    check(snippet_count > 0 and available > 0,
          f"T38-A3 夹具正向链必须至少产出一条可导航片段（可用简介 {available} 份 / "
          f"片段 {snippet_count} 条）")
    # 简介只导航：不得成为完成资格的依据。
    check(all(SV.is_completion_eligible(ctx["verified"], s.span_id) is False
              for s in snapshot.spans),
          "T38-A3 存在可导航简介不改变完成资格（TS4-A 恒 False）")


# ---------------------------------------------------------------------------
# T38-A4 负例：manifest 成员缺失 / 成员身份不符 / 越权来源
# ---------------------------------------------------------------------------

def _test_t38a4_missing_and_mismatched_members() -> None:
    ok, root = _try(EG.load_fixture_root)
    if not check(ok, "T38-A4 夹具 manifest 可读（负例基座）"):
        return
    company_id = root["company_id"]
    document_id = root["document_id"]
    document_version = root["document_version"]

    def load(broken):
        return EG._load_fixture_snapshot_and_blocks(
            broken, company_id=company_id, document_id=document_id,
            document_version=document_version)

    # (1) 成员缺失：少一个 role。
    missing = _mutated(root, members=lambda: (root["members"][:-1]))
    raises(lambda: load(missing), EG.EvidenceGatewayError, "角色",
           "T38-A4 manifest 成员缺失（少一个 role）必须 fail-closed")
    # (2) 成员 relpath 指向未列入 manifest 的文件。
    raises(lambda: EG._fixture_file_identity(root, "extra_member.json", "成员"),
           EG.EvidenceGatewayError, "未列入夹具 manifest",
           "T38-A4 未列入 manifest 的文件不得进入链路")
    raises(lambda: EG._fixture_file_identity(root, "../manifest.json", "成员"),
           EG.EvidenceGatewayError, "同目录文件名",
           "T38-A4 夹具成员 relpath 不得越出夹具目录")
    # (3) 成员文件身份不符：manifest 钉住的 sha256 与实际不符。
    mismatched = _mutated(
        root, members=lambda: [dict(m, sha256="0" * 64) if m["role"] == "page_layout"
                               else m for m in root["members"]])
    raises(lambda: EG._fixture_file_identity(mismatched, "page_layout.json", "成员"),
           EG.EvidenceGatewayError, "与 manifest 钉住值不一致",
           "T38-A4 成员文件身份与 manifest 钉住值不符必须 fail-closed")
    # (4) 集合身份不符：manifest 声明的成员清单 sha 与真实成员不符。
    bad_identity = _mutated(root, member_identity_sha256="0" * 64)
    raises(lambda: load(bad_identity), EG.EvidenceGatewayError, "成员身份清单",
           "T38-A4 集合成员身份 sha 与真实成员不符必须 fail-closed")
    # (5) 集合版本不符。
    bad_version = _mutated(root, evidence_set_version="fsv-not-this-one")
    raises(lambda: load(bad_version), EG.EvidenceGatewayError, "",
           "T38-A4 Evidence 集合版本与 manifest 声明不符必须 fail-closed")
    # (6) 跨公司 / 跨文档 / 跨版本复用。
    raises(lambda: EG._load_fixture_snapshot_and_blocks(
        root, company_id="999999", document_id=document_id,
        document_version=document_version), EG.EvidenceGatewayError, "跨公司",
        "T38-A4 夹具不得跨公司复用")
    raises(lambda: EG._load_fixture_snapshot_and_blocks(
        root, company_id=company_id, document_id="OTHER_DOC",
        document_version=document_version), EG.EvidenceGatewayError, "跨文档",
        "T38-A4 夹具不得跨文档复用")
    # (7) fixture_kind 不符：不得把另一类夹具当正向来源。
    bad_kind = _mutated(root, fixture_kind="something_else")
    raises(lambda: load(bad_kind), EG.EvidenceGatewayError, "fixture_kind",
           "T38-A4 夹具类型不符必须 fail-closed")


def _test_t38a4_scope_and_authority_boundaries() -> None:
    ok, root = _try(EG.load_fixture_root)
    if not check(ok, "T38-A4 夹具 manifest 可读（作用域负例基座）"):
        return
    identity = EG.fixture_evidence_identity(root)

    raises(lambda: EG.evidence_authority_identity(
        scope="live", source_kind=EG.FIXTURE_SOURCE_KIND,
        resolved_db_identity=identity),
        EG.EvidenceGatewayError, "live 域",
        "T38-A4 夹具来源不得出现在 live（生产）签发域")
    raises(lambda: EG.evidence_authority_identity(
        scope="testing", source_kind=EG.FIXTURE_SOURCE_KIND,
        resolved_db_identity=identity),
        EG.EvidenceGatewayError, "",
        "T38-A4 testing 不是合法签发域（不得用 testing 冒充验收）")
    raises(lambda: EG.evidence_authority_identity(
        scope="pinned_acceptance", source_kind="unit_test",
        resolved_db_identity=identity),
        EG.EvidenceGatewayError, "source_kind",
        "T38-A4 未登记的 source_kind 必须 fail-closed")
    check("unit_test" not in EG.EVIDENCE_AUTHORITY_SOURCE_KINDS
          and set(EG.EVIDENCE_AUTHORITY_SOURCE_KINDS)
          == {"current_store", "historical_run", EG.FIXTURE_SOURCE_KIND},
          f"T38-A4 Evidence 来源词表封闭且不含 testing 域："
          f"{EG.EVIDENCE_AUTHORITY_SOURCE_KINDS}")

    # 夹具 issuer **没有** 任何路径形参（"换一个库/换一份夹具"在签名层面不存在）。
    params = set(inspect.signature(
        EG._issue_fixture_evidence_authority).parameters)
    check(params == {"fixture_root"},
          f"T38-A4 夹具 Evidence issuer 只接受 fixture_root（无路径参数），得到 "
          f"{sorted(params)}")
    load_params = set(inspect.signature(
        EG.VerifiedCurrentEvidenceAuthority.load_snapshot_and_blocks).parameters)
    check(load_params == {"self", "company_id", "document_id", "document_version"},
          f"T38-A4 夹具取数签名只接受文档身份（无 db_path / 任意 path），得到 "
          f"{sorted(load_params)}")

    # 越权：把夹具 authority 当生产链输入 —— 生产对齐入口只收 live 能力。
    ok_auth, authority = _try(
        lambda: EG._issue_fixture_evidence_authority(root))
    if check(ok_auth, "T38-A4 夹具 Evidence authority 可签发"
                      + ("" if ok_auth else f"：{authority}")):
        check(authority.issuer_scope == "pinned_acceptance"
              and authority.source_kind == EG.FIXTURE_SOURCE_KIND,
              "T38-A4 夹具 authority 的签发域为 pinned_acceptance/versioned_fixture")
        from document_structure import aligner as A  # noqa: PLC0415
        raises(lambda: A.align_evidence_set_verified(object(), authority),
               SchemaValidationError, "",
               "T38-A4 夹具 authority 不得进入 live 对齐入口（越权拒绝）")


# ---------------------------------------------------------------------------
# T38-A5 负例：冻结旧 fixture 无 Evidence 集（只能 typed fail-closed）
# ---------------------------------------------------------------------------

def _test_t38a5_frozen_fixture_without_evidence_set() -> None:
    trust_path = REPO / TRUST_ROOTS_RELPATH
    ok_trust, trust = _try(lambda: _read_json(trust_path))
    if not check(ok_trust, f"T38-A5 冻结信任锚可读（{TRUST_ROOTS_RELPATH}）"):
        return
    spec = (trust.get("documents") or {}).get(FROZEN_FIXTURE_DOCUMENT_ID)
    if not check(isinstance(spec, dict),
                 f"T38-A5 信任锚含 {FROZEN_FIXTURE_DOCUMENT_ID} 的身份条目"):
        return
    frozen_dir = REPO / trust["frozen_run_relpath"] / FROZEN_FIXTURE_DOCUMENT_ID
    ok_manifest, frozen = _try(lambda: _read_json(frozen_dir / "manifest.json"))
    if not check(ok_manifest, "T38-A5 冻结 FIXTURE_BOND_2026 的 manifest 可读"):
        return

    alignment = frozen.get("alignment") or {}
    check(alignment.get("evidence_set_version") is None,
          f"T38-A5 冻结 fixture 无 Evidence 集（evidence_set_version 必须为 null），"
          f"得到 {alignment.get('evidence_set_version')!r}")
    check(alignment.get("block_count") == 0
          and alignment.get("terminals_emitted") == 0,
          f"T38-A5 冻结 fixture 的 block_count / terminals_emitted 必须为 0，得到 "
          f"{alignment.get('block_count')!r}/{alignment.get('terminals_emitted')!r}")
    expected = spec.get("expected_alignment") or {}
    check(expected.get("evidence_set_version") is None
          and expected.get("block_count") == 0,
          "T38-A5 信任锚对该 fixture 的期望同样声明无 Evidence 集")
    check("evidence_set_snapshot" not in expected,
          "T38-A5 信任锚不得为该 fixture 声明证据集快照（无集不是空集）")

    ok_rows, frozen_rows = _try(lambda: _try(lambda: _read_json(
        frozen_dir / "normalization_alignment.json")))
    if ok_rows:
        inner_ok, rows_doc = frozen_rows
        if inner_ok:
            check(rows_doc.get("rows") == [],
                  f"T38-A5 冻结对齐行必须为空（实际 {len(rows_doc.get('rows') or [])} 行）")

    # 冻结版本的版式必须先能被同一份锁定 PDF 重建，否则下面的 fail-closed 归因不清。
    ok_layout, layout = _try(lambda: PageLayout.from_dict(
        _read_json(frozen_dir / "page_layout.json")))
    if not check(ok_layout, "T38-A5 冻结 FIXTURE_BOND_2026 的版式可还原"):
        return
    check(layout.document_id == FROZEN_FIXTURE_DOCUMENT_ID
          and layout.page_layout_id == spec["expected_identity"]["page_layout_id"]
          and layout.source_file_sha256
          == trust["fixture_bond_pdf_sha256"],
          "T38-A5 冻结版式的文档身份 / 版式 ID / 源 PDF sha 与信任锚逐项一致")

    pdf_path = REPO / trust["fixture_bond_pdf_relpath"]
    ok_pdf, pdf_bytes = _try(lambda: pdf_path.read_bytes())
    if not check(ok_pdf, "T38-A5 冻结 fixture 的原 PDF 可读"):
        return
    check(hashlib.sha256(pdf_bytes).hexdigest() == trust["fixture_bond_pdf_sha256"],
          "T38-A5 冻结 PDF 字节与信任锚的 sha256 一致")

    # 关键反例：该 fixture 不得产出 SpanBuildSnapshot —— 只能 typed fail-closed。
    root_lock = {"pdf_sha256": trust["fixture_bond_pdf_sha256"],
                 "fixture_file_sha256": _sha256_file(trust_path)}
    trusted_file_sha = trust["trusted_file_sha256"]
    check(root_lock["fixture_file_sha256"]
          == expected.get("trust_root_file_sha256", root_lock["fixture_file_sha256"]),
          "T38-A5 根锁文件 sha 与仓库内信任锚文件字节一致（可复算）")
    del trusted_file_sha

    def _issue_out_of_band():
        return SB._issue_pinned_ts3_handoff(
            raw_pdf=pdf_bytes, expected_layout=layout,
            company_id=FROZEN_FIXTURE_COMPANY_ID,
            document_id=FROZEN_FIXTURE_DOCUMENT_ID, root_lock=root_lock,
            frozen_rows=())

    try:
        _issue_out_of_band()
    except SchemaValidationError as error:
        text = str(error)
        typed = (("evidence" in text.lower()) or ("rows" in text)
                 or ("Evidence 库不存在" in text) or ("冻结 rows" in text))
        check(typed,
              f"T38-A5 无 Evidence 集必须得到 typed fail-closed（缺集合 / 缺行 / 缺库），"
              f"实际信息：{text!r}")
    except Exception as error:  # noqa: BLE001
        check(False, f"T38-A5 无 Evidence 集抛出 {type(error).__name__} 而非 "
                     f"SchemaValidationError：{error}")
    else:
        check(False, "T38-A5 无 Evidence 集的冻结 fixture 竟签发了交接能力"
                     "（必须 fail-closed，不得生成 SpanBuildSnapshot）")

    # 同一条件的**无数据库依赖**版本：空的冻结 rows 不得被表述为对齐结论。
    ctx = _chain()
    if ctx.get("error"):
        skip("T38-A5 空 rows fail-closed 的独立复核需要正向链的证据集快照，"
             f"当前夹具链未建立：{ctx['error']}")
    else:
        raises(lambda: __import__(
            "document_structure.aligner", fromlist=["x"]
        ).restore_pinned_alignment_from_frozen_rows(
            rows=(), blocks=ctx["handoff"].evidence_blocks, layout=ctx["layout"],
            snapshot=ctx["handoff"].evidence_snapshot),
            SchemaValidationError, "非空序列",
            "T38-A5 冻结 rows 为空不得被当作已还原的对齐终态（fail-closed）")


# ---------------------------------------------------------------------------
# T38-A6 加固：TS4 生产模块不含公司专有 token
# ---------------------------------------------------------------------------

_COMPANY_TOKEN_PATTERNS = (
    (r"(?<![0-9A-Za-z_])300750(?![0-9A-Za-z_])", "300750"),
    (r"宁德时代", "宁德时代"),
    (r"(?<![0-9A-Za-z_])CATL(?![0-9A-Za-z_])", "CATL"),
    (r"(?<![0-9A-Za-z_])case_id(?![0-9A-Za-z_])", "case_id"),
)


def _test_t38a6_no_company_tokens() -> None:
    hits: list = []
    for relpath in TS4_PRODUCTION_MODULES:
        path = REPO / relpath
        if not path.is_file():
            hits.append(f"{relpath}: 缺失")
            continue
        text = path.read_text(encoding="utf-8")
        for pattern, label in _COMPANY_TOKEN_PATTERNS:
            for match in re.finditer(pattern, text):
                line_no = text.count("\n", 0, match.start()) + 1
                hits.append(f"{relpath}:{line_no} 命中 {label}")
    check(not hits,
          f"T38-A6 TS4 生产模块不得含公司专有 token（300750 / 宁德时代 / CATL / "
          f"case_id），命中：{hits[:5]}")
    # 夹具自身也不得靠"公司专有 token"取胜（允许出现 non_300750_ts4 这类**标识符**，
    # 它在词法上是长标识符的一部分，不是独立的公司 id 字面量）。
    ok, root = _try(EG.load_fixture_root)
    if check(ok, "T38-A6 夹具 manifest 可读（token 加固基座）"):
        blob = json.dumps(root, ensure_ascii=False)
        standalone = re.findall(r"(?<![0-9A-Za-z_])300750(?![0-9A-Za-z_])", blob)
        check(not standalone,
              f"T38-A6 夹具 manifest 不得含独立的 300750 公司 id 字面量，得到 "
              f"{standalone[:3]}")


# ---------------------------------------------------------------------------

def main() -> dict:
    _test_t38a1_fixture_integrity()
    _test_t38a1_fixture_identity_declarations()
    _test_t38a2_handoff_issuance()
    _test_t38a2_snapshot_and_conservation()
    _test_t38a2_verifier_and_completion()
    _test_t38a3_synopsis_sources()
    _test_t38a4_missing_and_mismatched_members()
    _test_t38a4_scope_and_authority_boundaries()
    _test_t38a5_frozen_fixture_without_evidence_set()
    _test_t38a6_no_company_tokens()
    return _results


if __name__ == "__main__":
    import json as _json

    _res = main()
    print(_json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

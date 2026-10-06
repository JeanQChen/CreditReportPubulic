# -*- coding: utf-8 -*-
"""TS4 §18.13 —— 正式**入口边界**的对抗性验收（T5 / T6 / T7 / T8 / T8b）。

覆盖：

- **T5 入口拒绝伪造派生结果与 input capability**：正式入口只接受
  `(VerifiedTS3Handoff, stage)`；签名里**没有** provider / gateway / `db_path` /
  policy path / plain `EvidenceSetAlignment` / 调用者自造 `SpanBuildInput` /
  `line_states` / `table_scopes` / `outline_result` / `snapshot+blocks` 这些位置。
  `object.__new__`、直接构造、`copy` / `deepcopy` / `pickle` / `replace`、
  "伪造 provider 指纹后再把全部身份重算一遍"都拿不到正式资格。
- **T6 Evidence 快照缺 / 多 / 异文档 / 异版本 / 异集合**：生产入口只能经只读
  gateway；即便伪造出一整套**内部自洽**（成员序、`block_count`、指纹都自证通过）
  的 snapshot + blocks，也注入不进正式链路；成员缺 / 多 / 重复、跨公司 / 跨文档 /
  跨版本 / 跨集合分别被拒。
- **T7 全终态闭合**：删一个终态 → 拒绝（缺失）；塞一个不属于本集合的终态 → 拒绝
  （多余）；同一个 block 两个终态 → 拒绝（重复）；`terminals_missing_count` 必须与
  本地自算结果逐项一致。
- **T8 终态 / Evidence / Layout 三方对齐**：除长度 / layout / set / aligner 之外，
  交换两个 block 的 `page_number` / `block_index`、伪造 terminal 的 schema /
  locator / ID 均被拒绝；structure snapshot 的 layout / outline / version 任一不符
  也被拒绝。
- **T8b handoff / provider / policy 与 input fingerprint**：四个内部受信边界的
  version 与 authority fingerprint 全部进入 §18.3.5 的封闭公式；官方 gateway +
  替代 DB、官方 terminal factory + 手造 `EvidenceSetAlignment`、"保住 PDF 哈希却篡改
  版式并把全部 ID 重算一遍"、任意 duck-typed / Protocol 对象、自报 fingerprint、
  伪造策略记录（重算全 ID）均被拒；历史 A 的 provider / input fingerprint 可本地
  复现且不随"注册表里多一个策略条目"而变。

**测试卫生**（§18.13 末尾）：全部是"构造 + 断言"，不连网络、不调 LLM、不写数据库、
不依赖执行顺序。真实 PDF 与 `data/evidence.db` 只被**读**；运行结束断言库的
`size` / `mtime_ns` / `sha256` 三项与开始时逐位相同。

断言口径：**不采信实现自报的"结论字段"**。每条通过都要求精确的失败语义（异常类型
+ 消息子串），因此删掉对应生产逻辑后该断言必然失败。
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import inspect
import json
import pathlib
import pickle
import sys
import time

_REPO = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from evidence import store as estore  # noqa: E402
from evidence.ids import content_hash as evidence_content_hash  # noqa: E402
from evidence.ids import make_evidence_id  # noqa: E402
from evidence.schema import EVIDENCE_TYPES  # noqa: E402

from document_structure import evidence_gateway as EG  # noqa: E402
from document_structure import layout_builder as LB  # noqa: E402
from document_structure import span_builder as SB  # noqa: E402
from document_structure import span_policy as SP  # noqa: E402
from document_structure import span_verifier as SV  # noqa: E402
from document_structure import versions as V  # noqa: E402
from document_structure.aligner import (  # noqa: E402
    EvidenceBlockInput,
    EvidenceSetAlignment,
    EvidenceSetMember,
    EvidenceSetSnapshot,
    align_evidence_set_verified,
    alignment_summary,
    alignment_terminal_report,
    assert_members_exact,
    snapshot_fingerprint,
)
from document_structure.canonical import (  # noqa: E402
    SchemaValidationError,
    sha256_canonical,
)
from document_structure.span_schema import (  # noqa: E402
    CapabilityError,
    SpanBuildSnapshot,
    SpanQualificationPolicy,
    TrustedBuildInput,
    capability_scope,
)
from evals import tree_stage_env as STAGE  # noqa: E402

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

_SAMPLE_PDF = _REPO / "data" / "samples" / "300750" / "announcements" / "NDSD_KCZ_2026.pdf"
_COMPANY_ID = "300750"
_DOCUMENT_ID = "NDSD_KCZ_2026"
#: 本模块走**当前阶段**的正式新建入口：A 阶段造 A 快照、B 阶段造 B 快照，
#: 不绕过生产代码的阶段门（跨阶段新建一律由 `next_stage()` 显式构造反例）。
_STAGE = STAGE.current_stage()

_SLOT_NAMES = tuple(n for n in SB.VerifiedTS3Handoff.__slots__ if n != "__weakref__")

#: 公开入口**不得**存在的参数名（T5 的逐项反例清单）。
_FORBIDDEN_ENTRY_PARAMS = (
    "provider", "gateway", "db_path", "policy_path", "policy",
    "snapshot", "blocks", "evidence_snapshot", "evidence_blocks",
    "line_states", "table_scopes", "outline_result", "document_outline",
    "page_layout", "layout", "structure_snapshot", "span_input", "input",
    "alignment", "terminals", "authority", "evidence_authority",
    "layout_capability", "scope", "allowed_scopes", "token",
)


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
# 小工具
# ---------------------------------------------------------------------------

def _hex(seed) -> str:
    """确定性的 64 位十六进制（构造反例用的身份占位符）。"""
    return sha256_canonical([seed])


def _flip(value: str) -> str:
    """改动一个字符：形状仍合法，取值不同。"""
    return ("1" if value[0] != "1" else "2") + value[1:]


def _clone(obj, **changes):
    """`object.__new__` 旁路的一比一克隆（制造"字段自洽"的伪造对象）。

    绕过 `__post_init__`，因此可以造出"下游若不重算就抓不到"的伪造件。
    """
    clone = object.__new__(type(obj))
    for name, value in vars(obj).items():
        object.__setattr__(clone, name, value)
    for name, value in changes.items():
        object.__setattr__(clone, name, value)
    return clone


def _handoff_values(handoff) -> dict:
    return {name: getattr(handoff, name) for name in _SLOT_NAMES}


def _set_handoff_slots(obj, values: dict):
    for name in _SLOT_NAMES:
        object.__setattr__(obj, name, values[name])
    return obj


# ---------------------------------------------------------------------------
# live 正式链路夹具（只建一次；只读真实 PDF 与 data/evidence.db）
# ---------------------------------------------------------------------------

_LIVE: dict = {}


def _live() -> dict:
    """由真实 PDF + 真实 current Store 走一次 live 正式链路（四个 capability）。"""
    if "error" in _LIVE:
        raise RuntimeError(_LIVE["error"])
    if _LIVE:
        return _LIVE
    started = time.time()
    try:
        estore._db_path = _REPO / "data" / "evidence.db"
        authority = EG.bind_current_evidence_authority()
        vlayout = LB.build_verified_page_layout(
            _SAMPLE_PDF, company_id=_COMPANY_ID, document_id=_DOCUMENT_ID)
        alignment = align_evidence_set_verified(vlayout, authority)
        handoff = SB.issue_live_ts3_handoff(vlayout, alignment, authority)
        snapshot = SB.build_span_snapshot(handoff, stage=_STAGE)
        verified = SV.verify_span_snapshot(snapshot, handoff)
    except Exception as e:  # noqa: BLE001
        _LIVE["error"] = f"{type(e).__name__}: {e}"
        raise
    _LIVE.update({
        "authority": authority, "vlayout": vlayout, "alignment": alignment,
        "handoff": handoff, "snapshot": snapshot, "verified": verified,
        "seconds": time.time() - started,
    })
    return _LIVE


def _closure_kwargs(handoff) -> dict:
    """`_assert_upstream_closure` 的完整受信入参（全部取自受信交接）。"""
    return {
        "layout": handoff.page_layout,
        "document_outline": handoff.document_outline,
        "structure_snapshot": handoff.structure_snapshot,
        "evidence_snapshot": handoff.evidence_snapshot,
        "evidence_blocks": handoff.evidence_blocks,
        "terminals": handoff.alignment.terminals,
    }


def _forged_snapshot(handoff, **changes) -> EvidenceSetSnapshot:
    """造一个**内部自洽**的伪造 EvidenceSetSnapshot（指纹按全成员重算）。

    它必须能通过 `EvidenceSetSnapshot.__post_init__` 的全部自证校验 —— 反例的要害
    正在此处：自洽不等于权威。
    """
    real = handoff.evidence_snapshot
    fields = {
        "company_id": real.company_id, "document_id": real.document_id,
        "document_version": real.document_version,
        "evidence_set_version": real.evidence_set_version,
        "status": real.status, "members": real.members,
        "gateway_version": real.gateway_version,
        "snapshot_version": real.snapshot_version,
    }
    fields.update(changes)
    members = tuple(fields["members"])
    fp = snapshot_fingerprint(
        company_id=fields["company_id"], document_id=fields["document_id"],
        document_version=fields["document_version"],
        evidence_set_version=fields["evidence_set_version"],
        status=fields["status"], gateway_version=fields["gateway_version"],
        members=members)
    return EvidenceSetSnapshot(
        company_id=fields["company_id"], document_id=fields["document_id"],
        document_version=fields["document_version"],
        evidence_set_version=fields["evidence_set_version"],
        status=fields["status"], members=members, block_count=len(members),
        fingerprint=fp, gateway_version=fields["gateway_version"],
        snapshot_version=fields["snapshot_version"])


def _rebuild_block(block: EvidenceBlockInput, **changes) -> EvidenceBlockInput:
    """按改动后的字段**重算** `content_hash` / `evidence_block_id` 重建一个块。

    代表"攻击者把身份也一并重算"的反例：块自身完全自洽，但已不属于权威集合。
    """
    fields = {
        "company_id": block.company_id, "document_id": block.document_id,
        "document_version": block.document_version,
        "evidence_set_version": block.evidence_set_version,
        "page_number": block.page_number, "block_index": block.block_index,
        "text": block.text, "structured_payload": block.structured_payload,
        "evidence_type": block.evidence_type,
    }
    fields.update(changes)
    content_hash = evidence_content_hash(fields["text"], fields["structured_payload"])
    return EvidenceBlockInput(
        company_id=fields["company_id"], document_id=fields["document_id"],
        document_version=fields["document_version"],
        evidence_set_version=fields["evidence_set_version"],
        evidence_block_id=make_evidence_id(
            fields["company_id"], fields["document_id"], fields["document_version"],
            fields["evidence_set_version"], fields["page_number"],
            fields["block_index"], content_hash),
        content_hash=content_hash, evidence_type=fields["evidence_type"],
        page_number=fields["page_number"], block_index=fields["block_index"],
        text=fields["text"], structured_payload=fields["structured_payload"])


def _submitted(blocks) -> tuple:
    """把块序列投影成成员身份五元组（与内部 `_member_of` 同口径）。"""
    return tuple(EvidenceSetMember(
        evidence_id=b.evidence_block_id, content_hash=b.content_hash,
        evidence_type=b.evidence_type, page_number=b.page_number,
        block_index=b.block_index) for b in blocks)


# ---------------------------------------------------------------------------
# T5 入口拒绝伪造派生结果与 input capability
# ---------------------------------------------------------------------------

def _t5_entry_rejects_forged_inputs() -> None:
    live = _live()
    handoff = live["handoff"]

    # (1) 公开签名只有规格允许的那些字段。
    sig = inspect.signature(SB.build_span_snapshot)
    check(tuple(sig.parameters) == ("handoff", "stage"),
          f"build_span_snapshot 的公开签名必须恰为 (handoff, stage)，"
          f"得到 {tuple(sig.parameters)}")
    check(sig.parameters["stage"].kind is inspect.Parameter.KEYWORD_ONLY,
          "stage 必须是仅关键字参数（不得被位置参数塞进别的东西）")
    check(not any(p.kind is inspect.Parameter.VAR_KEYWORD
                  for p in sig.parameters.values()),
          "build_span_snapshot 不得带 **kwargs（否则等于开了任意注入面）")
    sv_sig = inspect.signature(SV.verify_span_snapshot)
    check(tuple(sv_sig.parameters) == ("snapshot", "handoff"),
          f"verify_span_snapshot 的公开签名必须恰为 (snapshot, handoff)，"
          f"得到 {tuple(sv_sig.parameters)}")
    check(tuple(inspect.signature(SB.issue_live_ts3_handoff).parameters)
          == ("layout_capability", "verified_alignment", "evidence_authority"),
          "issue_live_ts3_handoff 只接受三个受信 capability")
    check(tuple(inspect.signature(EG.bind_current_evidence_authority).parameters) == (),
          "bind_current_evidence_authority 不接受任何参数（不得传入库路径）")

    # (2) 逐项反例：清单里的每个名字当关键字传进去必须是 TypeError（参数不存在），
    #     而不是"传了但被静默忽略"。
    for name in _FORBIDDEN_ENTRY_PARAMS:
        def _call(_name=name):
            kwargs = {"stage": _STAGE, _name: None}
            return SB.build_span_snapshot(handoff, **kwargs)

        raises(_call, TypeError, name,
               f"正式入口不得接受 {name!r} 参数（签名里根本没有这个注入面）")

    # (3) 四个内部受信 provider 是模块私有：不导出、也不出现在入口参数里。
    for provider_name in ("_OutlineStructureSnapshotProvider",
                          "_ReadonlyCurrentEvidenceGateway",
                          "_AlignmentTerminalProvider",
                          "_QualificationPolicyProvider"):
        check(hasattr(SB, provider_name),
              f"{provider_name} 必须存在于组合根内部（受信步骤得有唯一实现）")
        check(provider_name not in SB.__all__,
              f"{provider_name} 不得导出（不得成为可替换扩展点）")
        check(provider_name not in tuple(sv_sig.parameters),
              f"{provider_name} 不得是复核入口的参数")
    check("_build_from_pinned_handoff" not in SB.__all__
          and "_testing_build_span_snapshot" not in SB.__all__,
          "pinned / testing 构建 wrapper 不得导出（正式入口唯一）")
    check("_issue_pinned_evidence_authority" not in EG.__all__
          and "issue_live_ts3_handoff" in SB.__all__,
          "pinned issuer 不得导出，而 live 签发入口必须是导出的唯一入口")
    # `SpanBuildInput` 同属内部 capability：模块内仍要用它（内部构建路径 + 上面的
    # 反例），但**不得**列入 `__all__` —— 列出来等于承认"调用者可以自己造输入"。
    check(hasattr(SB, "SpanBuildInput")
          and "SpanBuildInput" not in SB.__all__,
          "SpanBuildInput 是内部 capability：必须存在于模块内但不得列入 __all__")

    # (4) `object.__new__` 出来的交接：字段可以一模一样，资格不是字段。
    forged_new = object.__new__(SB.VerifiedTS3Handoff)
    _set_handoff_slots(forged_new, _handoff_values(handoff))
    check(forged_new.handoff_identity == handoff.handoff_identity
          and forged_new.page_layout.page_layout_id
          == handoff.page_layout.page_layout_id,
          "反例前置：object.__new__ 出来的交接字段与真品逐位相同")
    check(capability_scope(forged_new) is None,
          "object.__new__ 出来的交接不在签发登记表中（资格不来自字段）")
    raises(lambda: SB.build_span_snapshot(forged_new, stage=_STAGE),
           CapabilityError, "不是本进程由正式签发路径产生的实例",
           "object.__new__ + 字段全等的交接不得通过正式入口")

    # (5) 直接构造同一个类：`__init__` 可调用，资格仍不来自构造。
    direct = SB.VerifiedTS3Handoff(
        layout_capability=handoff.layout_capability,
        document_outline=handoff.document_outline,
        structure_snapshot=handoff.structure_snapshot,
        evidence_authority=handoff.evidence_authority,
        evidence_snapshot=handoff.evidence_snapshot,
        evidence_blocks=handoff.evidence_blocks, alignment=handoff.alignment,
        policy=handoff.qualification_policy, scope="live",
        source_kind="current_store",
        issuer_version=V.VERIFIED_TS3_HANDOFF_VERSION,
        policy_provider_authority_fingerprint=(
            handoff.policy_provider_authority_fingerprint),
        structure_provider_authority_fingerprint=(
            handoff._structure_provider_authority_fingerprint),
        evidence_gateway_authority_fingerprint=(
            handoff._evidence_gateway_authority_fingerprint),
        terminal_provider_authority_fingerprint=(
            handoff._terminal_provider_authority_fingerprint),
        handoff_identity=handoff.handoff_identity)
    check(capability_scope(direct) is None, "直接构造的交接同样未签发")
    raises(lambda: SB.build_span_snapshot(direct, stage=_STAGE),
           CapabilityError, "不是本进程由正式签发路径产生的实例",
           "直接构造的同形交接不得通过正式入口")

    # (6) copy / deepcopy / pickle / 序列化 / replace 全部没有注入路径。
    raises(lambda: copy.copy(handoff), SchemaValidationError, "不可 copy",
           "交接不得 copy（副本未在签发登记表中）")
    raises(lambda: copy.deepcopy(handoff), SchemaValidationError, "不可 deepcopy",
           "交接不得 deepcopy")
    raises(lambda: pickle.dumps(handoff), SchemaValidationError, "不可 pickle",
           "交接不得 pickle")
    raises(lambda: handoff.to_dict(), SchemaValidationError, "不得序列化",
           "交接不得序列化（读回来的不是本进程签发的那一个）")
    check(not dataclasses.is_dataclass(SB.VerifiedTS3Handoff),
          "交接不是 dataclass，dataclasses.replace 因此不是一条注入路径")
    raises(lambda: dataclasses.replace(handoff), TypeError, "",
           "dataclasses.replace 对交接不可用（无注入路径）")

    # (7) "伪造 provider 指纹后把全部身份重算一遍"仍然拿不到资格。
    values = _handoff_values(handoff)
    for slot in ("_structure_provider_authority_fingerprint",
                 "_evidence_gateway_authority_fingerprint",
                 "_terminal_provider_authority_fingerprint",
                 "_policy_provider_authority_fingerprint"):
        values[slot] = _flip(values[slot])
    values["_handoff_identity"] = sha256_canonical([
        "forged", values["_structure_provider_authority_fingerprint"],
        values["_evidence_gateway_authority_fingerprint"],
        values["_terminal_provider_authority_fingerprint"],
        values["_policy_provider_authority_fingerprint"]])
    recomputed = _set_handoff_slots(object.__new__(SB.VerifiedTS3Handoff), values)
    check(recomputed.handoff_identity != handoff.handoff_identity
          and all(len(v) == 64
                  for v in recomputed.authority_fingerprints().values()),
          "反例前置：伪造件把四项 authority 指纹与 handoff_identity 都重算成自洽值")
    raises(lambda: SB.build_span_snapshot(recomputed, stage=_STAGE),
           CapabilityError, "不是本进程由正式签发路径产生的实例",
           "重算全部身份也拿不到资格（资格来自签发登记表，不来自字段）")

    # (8) 调用者自造的 `SpanBuildInput`：私有工厂令牌、copy、序列化全拒。
    raises(lambda: SB.SpanBuildInput._make(token=object()),
           SB.SpanBuildError, "只能由组合根内部工厂创建",
           "伪造工厂令牌的 SpanBuildInput 不得进入正式链路")
    bare = SB.SpanBuildInput()
    check(not hasattr(bare, "_factory_capability"),
          "init=False 的空壳 SpanBuildInput 连内部令牌字段都没有")
    raises(lambda: SB._build_snapshot(bare), AttributeError, "",
           "空壳 SpanBuildInput 到不了纯算法核（缺受信字段）")
    inp = SB._build_span_input(handoff, stage=_STAGE)
    check(isinstance(inp, SB.SpanBuildInput)
          and inp._factory_capability is SB._FACTORY_TOKEN,
          "受信交接派生出的 SpanBuildInput 携带组合根私有令牌")
    raises(lambda: inp.to_dict(), SB.SpanBuildError, "不得序列化",
           "SpanBuildInput 是内部 capability，不得序列化")
    raises(lambda: copy.copy(inp), SB.SpanBuildError, "不可 copy",
           "SpanBuildInput 不可 copy")
    raises(lambda: copy.deepcopy(inp), SB.SpanBuildError, "不可 deepcopy",
           "SpanBuildInput 不可 deepcopy")
    raises(lambda: pickle.dumps(inp), SB.SpanBuildError, "不可 pickle",
           "SpanBuildInput 不可 pickle")
    raises(lambda: dataclasses.replace(inp), TypeError, "",
           "SpanBuildInput init=False：dataclasses.replace 不可用")

    # (9) plain `EvidenceSetAlignment`（无 capability）不是正式对齐结果。
    plain = EvidenceSetAlignment(
        page_layout_id=handoff.page_layout.page_layout_id,
        evidence_set_version=handoff.evidence_snapshot.evidence_set_version,
        aligner_version=V.ALIGNER_VERSION,
        blocks=handoff.alignment.alignment.blocks,
        summary=alignment_summary(handoff.alignment.alignment.blocks),
        snapshot=handoff.evidence_snapshot)
    check(capability_scope(plain) is None, "plain EvidenceSetAlignment 未签发")
    raises(lambda: SB.issue_live_ts3_handoff(live["vlayout"], plain,
                                             live["authority"]),
           CapabilityError, "不是本进程由正式签发路径产生的实例",
           "手造 EvidenceSetAlignment 不得进入正式签发入口")

    # (10) 其它任意对象同样被拒（正式入口只认本进程签发的那一个）。
    for junk in (object(), None, "handoff", 0, {"handoff": True}, []):
        raises(lambda j=junk: SB.build_span_snapshot(j, stage=_STAGE),
               SchemaValidationError, "", "非交接对象不得进入正式入口")
        raises(lambda j=junk: SV.verify_span_snapshot(live["snapshot"], j),
               SchemaValidationError, "", "非交接对象不得进入正式复核入口")


# ---------------------------------------------------------------------------
# T6 Evidence 快照缺 / 多 / 异文档 / 异版本 / 异集合
# ---------------------------------------------------------------------------

def _t6_evidence_snapshot_closure() -> None:
    live = _live()
    handoff = live["handoff"]
    layout = handoff.page_layout
    blocks = handoff.evidence_blocks
    snapshot = handoff.evidence_snapshot

    # (1) 生产入口只能经只读 gateway：入口无参数、库路径固定、只读原语登记在册。
    check("bind_current_evidence_authority" in EG.__all__,
          "只有 live gateway 入口导出")
    check(EG.DEFAULT_EVIDENCE_DB_PATH == EG.REPO_ROOT / "data" / "evidence.db"
          and EG.DEFAULT_EVIDENCE_DB_PATH.is_absolute(),
          "gateway 的库路径是固定常量，不是 CLI 参数；且必须是**绝对**路径（相对路径会随"
          "进程 CWD 绑定到不同的库）")
    db_identity = handoff.evidence_authority.resolved_db_identity
    check(sorted(db_identity) == ["mtime_ns", "resolved_path", "sha256", "size"]
          and str(db_identity["resolved_path"]).replace("\\", "/")
          .endswith("data/evidence.db"),
          "live 权威绑定 resolved path + size + mtime_ns + sha256 四项库身份")
    for primitive in EG.READONLY_PRIMITIVES:
        check(callable(getattr(estore, primitive, None)),
              f"只读原语 {primitive} 必须来自 evidence.store（不得另写 SQL）")
    check(handoff.evidence_authority.issuer_scope == "live"
          and handoff.evidence_authority.provider_version
          == V.EVIDENCE_GATEWAY_PROVIDER_VERSION,
          "live 权威的签发域与 provider 版本固定")
    check(snapshot.assert_current() is None,
          "权威快照必须是 current（非 current 不得作为正式输入）")
    check(handoff.evidence_blocks == tuple(blocks) and len(blocks) > 0,
          "快照与块只能由权威对象自己加载（调用者不得自备 snapshot+blocks）")

    # (2) 成员缺 / 多 / 重复 / 字段不符：提交侧与权威快照必须**精确相等**。
    submitted = _submitted(blocks)
    check(assert_members_exact(submitted, snapshot) is None,
          "反例前置：受信块集合与权威快照逐成员精确相等")
    raises(lambda: assert_members_exact(submitted[:-1], snapshot),
           SchemaValidationError, "提交成员数与权威快照不相等",
           "少一个成员必须拒绝")
    extra_member = EvidenceSetMember(
        evidence_id=_hex("extra-block"), content_hash=_hex("extra-hash"),
        evidence_type=EVIDENCE_TYPES[0], page_number=9999, block_index=0)
    raises(lambda: assert_members_exact(submitted + (extra_member,), snapshot),
           SchemaValidationError, "提交成员数与权威快照不相等",
           "多一个成员必须拒绝")
    raises(lambda: assert_members_exact(submitted + (submitted[0],), snapshot),
           SchemaValidationError, "提交成员数与权威快照不相等",
           "重复提交同一成员必须拒绝")
    raises(lambda: dataclasses.replace(submitted[0], block_index=-1),
           SchemaValidationError, "block_index 不得小于 0",
           "成员身份自身必须合法（负数 block_index 在构造期即被拒）")
    moved_member = dataclasses.replace(submitted[0],
                                       block_index=submitted[0].block_index + 1)
    raises(lambda: assert_members_exact(submitted[:-1] + (moved_member,), snapshot),
           SchemaValidationError, "提交成员与权威 EvidenceSet 快照不一致",
           "成员身份（page/block）不符必须逐字段拒绝，不得被总数掩盖")

    # (3) 手工伪造的"内部自洽"快照仍不能注入正式入口（成员少一个的版本）。
    fewer = _forged_snapshot(handoff, members=snapshot.members[:-1])
    check(fewer.block_count == len(snapshot.members) - 1
          and fewer.fingerprint == fewer.recomputed_fingerprint(),
          "反例前置：伪造快照自身完全自洽（成员序、block_count、指纹都自证通过）")
    object.__setattr__(handoff, "_evidence_snapshot", fewer)
    try:
        raises(lambda: SB.build_span_snapshot(handoff, stage=_STAGE),
               SchemaValidationError, "canonical 不相等",
               "自洽的伪造快照注入已签发交接后，正式入口必须拒绝")
    finally:
        object.__setattr__(handoff, "_evidence_snapshot", snapshot)

    # (4) 多一个成员的同款伪造件。
    extra = EvidenceSetMember(
        evidence_id=_hex("injected-block"), content_hash=_hex("injected-hash"),
        evidence_type=EVIDENCE_TYPES[0], page_number=9999, block_index=0)
    more_members = tuple(sorted(snapshot.members + (extra,),
                                key=lambda m: m.sort_key))
    more = _forged_snapshot(handoff, members=more_members)
    check(more.block_count == len(snapshot.members) + 1
          and more.fingerprint == more.recomputed_fingerprint(),
          "反例前置：多成员伪造快照已构造且自洽")
    object.__setattr__(handoff, "_evidence_snapshot", more)
    try:
        raises(lambda: SB.build_span_snapshot(handoff, stage=_STAGE),
               SchemaValidationError, "canonical 不相等",
               "多塞一个成员的伪造快照必须被正式入口拒绝")
    finally:
        object.__setattr__(handoff, "_evidence_snapshot", snapshot)

    # (5) 伪造的块列表（成员集合被换掉）同样注入不进去：组合根会用权威读取重取。
    object.__setattr__(handoff, "_evidence_blocks", blocks[:-1])
    try:
        raises(lambda: SB.build_span_snapshot(handoff, stage=_STAGE),
               SchemaValidationError, "重新取得的 Evidence 成员集合与交接绑定的不一致",
               "自洽的块子集注入已签发交接后，正式入口必须拒绝")
    finally:
        object.__setattr__(handoff, "_evidence_blocks", blocks)

    # (6) 跨公司 / 跨文档 / 跨版本：快照与块都必须与真实版式严格相等。
    for field, bad_value, keyword in (
            ("company_id", "000001", "company_id"),
            ("document_id", "NDSD_OTHER_YEAR", "document_id"),
            ("document_version", "sha256-" + "0" * 16, "document_version")):
        forged = _forged_snapshot(handoff, **{field: bad_value})
        check(forged.fingerprint == forged.recomputed_fingerprint(),
              f"反例前置：跨 {field} 的伪造快照自身自洽")
        raises(lambda f=forged: f.assert_bound_to(layout),
               SchemaValidationError, f"{field} 与 PageLayout 不一致",
               f"权威快照跨 {field} 必须拒绝")
        raises(lambda kw=keyword, b=bad_value: _rebuild_block(blocks[0], **{kw: b})
               .assert_bound_to(layout),
               SchemaValidationError, f"{field} 与 PageLayout 不一致",
               f"Evidence 块跨 {field}（即使重算全部身份）必须拒绝")
        object.__setattr__(handoff, "_evidence_snapshot", forged)
        try:
            raises(lambda: SB.build_span_snapshot(handoff, stage=_STAGE),
                   SchemaValidationError, "",
                   f"跨 {field} 的伪造快照注入后正式入口必须拒绝")
        finally:
            object.__setattr__(handoff, "_evidence_snapshot", snapshot)

    # (7) 块的自报身份不得覆盖重算身份：只改公司号 / 正文而不重算 ID，构造期即被拒。
    raises(lambda: dataclasses.replace(blocks[0], company_id="000001"),
           SchemaValidationError, "evidence_id 与按",
           "只改公司号而不重算身份，块在构造期即被拒（身份由 evidence.ids 重算）")
    raises(lambda: dataclasses.replace(blocks[0], text=blocks[0].text + "篡改"),
           SchemaValidationError, "content_hash 与按",
           "只改正文而不重算内容身份，块在构造期即被拒")

    # (8) 跨集合：块的 evidence_set_version 与权威快照不一致 → 闭合门拒绝。
    other_set = _rebuild_block(blocks[0], evidence_set_version="evset-injected")
    check(other_set.evidence_set_version == "evset-injected"
          and other_set.evidence_block_id != blocks[0].evidence_block_id
          and other_set.assert_identity() is None,
          "反例前置：跨集合块已把 evidence_id 一并重算成自洽值")
    raises(lambda: assert_members_exact(_submitted((other_set,) + blocks[1:]),
                                        snapshot),
           SchemaValidationError, "提交成员与权威 EvidenceSet 快照不一致",
           "跨集合的块（重算全部身份后）在成员精确比对处被拒")
    kwargs = _closure_kwargs(handoff)
    kwargs["evidence_blocks"] = (other_set,) + blocks[1:]
    raises(lambda kw=kwargs: SB._assert_upstream_closure(**kw),
           SchemaValidationError, "提交成员与权威 EvidenceSet 快照不一致",
           "把某个块换成异集合版本后，闭合门必须拒绝")
    forged_set = _forged_snapshot(handoff, evidence_set_version="evset-injected")
    kwargs = _closure_kwargs(handoff)
    kwargs["evidence_snapshot"] = forged_set
    raises(lambda kw=kwargs: SB._assert_upstream_closure(**kw),
           SchemaValidationError, "的 evidence_set_version 与权威快照",
           "权威快照换成异集合版本（自身自洽）后，闭合门必须拒绝")

    # (9) 非 current 快照不得作为正式输入。
    retired = _forged_snapshot(handoff, status="retired")
    raises(lambda: retired.assert_current(), SchemaValidationError,
           "只有 current 证据集可以作为正式对齐输入",
           "非 current 快照不得作为正式对齐输入")
    object.__setattr__(handoff, "_evidence_snapshot", retired)
    try:
        raises(lambda: SB.build_span_snapshot(handoff, stage=_STAGE),
               SchemaValidationError, "",
               "非 current 的伪造快照注入后正式入口必须拒绝")
    finally:
        object.__setattr__(handoff, "_evidence_snapshot", snapshot)

    # (10) 还原后链路仍可用（前面的篡改没有留下副作用）。
    check(SB.build_span_snapshot(handoff, stage=_STAGE).input_fingerprint
          == live["snapshot"].input_fingerprint,
          "全部反例还原后，受信交接必须回到与首次一致的 input fingerprint")


# ---------------------------------------------------------------------------
# T7 全终态闭合
# ---------------------------------------------------------------------------

def _t7_terminal_closure() -> None:
    handoff = _live()["handoff"]
    blocks = handoff.evidence_blocks
    terminals = handoff.alignment.terminals
    block_ids = sorted(b.evidence_block_id for b in blocks)

    # (1) 正向：每个块恰有一个正式终态。
    synced = SB._assert_upstream_closure(**_closure_kwargs(handoff))
    check(len(synced) == len(blocks) == len(terminals),
          "每个 Evidence 块恰有一个正式终态（正向闭合）")

    # (2) `terminals_missing_count` 必须与本地自算结果一致。
    report = alignment_terminal_report(handoff.alignment.alignment.blocks)
    summary = alignment_summary(handoff.alignment.alignment.blocks)
    own_missing = len(block_ids) - len({t.evidence_block_id for t in terminals}
                                      & set(block_ids))
    check(own_missing == 0,
          f"本地自算的 terminals_missing_count 必须为 0，得到 {own_missing}")
    check(report["terminals_missing"] == own_missing
          and report["terminals_emitted"] == report["total_blocks"]
          and report["total_blocks"] == len(blocks),
          "终态报告的三项（emitted / missing / total_blocks）必须与自算结果对账")
    check(summary["terminals_missing"] == []
          and summary["terminals_emitted"] == len(blocks),
          "汇总必须以显式清单暴露缺终态的块，而不是只给一个总数")
    check(summary["records_emitted"] + summary["records_refused"] == len(blocks),
          "记录 + 拒绝记录必须恰好覆盖全部块（769 = 769 的同一条守恒律）")

    # (3) 删一个终态 → 拒绝（缺失），且自算的缺失数恰为 1。
    dropped = terminals[:-1]
    own_missing = len(block_ids) - len({t.evidence_block_id for t in dropped}
                                      & set(block_ids))
    check(own_missing == 1,
          f"删一个终态后自算的缺失数必须为 1，得到 {own_missing}")
    kwargs = _closure_kwargs(handoff)
    kwargs["terminals"] = dropped
    raises(lambda kw=kwargs: SB._assert_upstream_closure(**kw),
           SchemaValidationError, "终态集合与 Evidence 块集合不完全相等",
           "删一个终态必须被闭合门拒绝（缺失）")

    # (4) 塞一个不属于本集合的终态 → 拒绝（多余）。
    foreign = dataclasses.replace(
        terminals[-1], page_number=9999, block_index=0,
        evidence_block_id=_hex("foreign-block"),
        terminal_id=_hex("foreign-terminal"))
    kwargs = _closure_kwargs(handoff)
    kwargs["terminals"] = terminals + (foreign,)
    raises(lambda kw=kwargs: SB._assert_upstream_closure(**kw),
           SchemaValidationError, "终态集合与 Evidence 块集合不完全相等",
           "塞一个不属于本集合的终态必须被拒绝（多余）")

    # (5) 同一个 block 两个终态 → 拒绝（重复）。
    #     注：`block_ids != sorted(terminal_ids)` 先于"重复"分支命中，因此这里命中的
    #     仍是集合不相等那条；两条都属"数量守恒但身份不闭合"，一律 fail-closed。
    duplicated = terminals[:-1] + (terminals[0],)
    own_missing = len(block_ids) - len({t.evidence_block_id for t in duplicated}
                                      & set(block_ids))
    check(own_missing == 1,
          "重复终态的集合在自算口径下同样属于缺一（不得被总数掩盖）")
    ids = [t.evidence_block_id for t in duplicated]
    check(len(set(ids)) != len(ids),
          "反例前置：终态 id 序列确实出现了重复")
    kwargs = _closure_kwargs(handoff)
    kwargs["terminals"] = duplicated
    raises(lambda kw=kwargs: SB._assert_upstream_closure(**kw),
           SchemaValidationError, "终态集合与 Evidence 块集合不完全相等",
           "同一个 block 出现两个终态必须被拒绝")

    # (6) 自算清单必须与被删掉的那一个终态逐 id 相同（不是只差一个数字）。
    missing_ids = set(block_ids) - {t.evidence_block_id for t in dropped}
    check(missing_ids == {terminals[-1].evidence_block_id},
          "自算缺失清单必须精确等于被删掉的那一个终态")


# ---------------------------------------------------------------------------
# T8 终态 / Evidence / Layout 三方对齐
# ---------------------------------------------------------------------------

def _mutate_terminals(handoff, mutate) -> SpanBuildSnapshot:
    """在已签发交接上临时改写终态视图，构建一次快照后立刻还原。

    这是"攻击者能改动本进程内对象"的最强假设：资格仍在（登记表按 id 认人），因此
    只有**独立重建 + canonical 全等**才可能发现篡改。
    """
    real = handoff.alignment._terminals
    object.__setattr__(handoff.alignment, "_terminals", mutate(real))
    try:
        return SB.build_span_snapshot(handoff, stage=_STAGE)
    finally:
        object.__setattr__(handoff.alignment, "_terminals", real)


def _t8_three_way_alignment() -> None:
    live = _live()
    handoff = live["handoff"]
    snapshot = live["snapshot"]
    blocks = handoff.evidence_blocks
    terminals = handoff.alignment.terminals
    order = sorted(range(len(blocks)),
                   key=lambda i: (blocks[i].page_number, blocks[i].block_index))
    a, b = order[0], order[-1]
    first, last = blocks[a], blocks[b]

    # (1) 交换两个 block 的 page_number / block_index。
    raises(lambda: dataclasses.replace(
        first, page_number=last.page_number, block_index=last.block_index),
        SchemaValidationError, "evidence_id 与按",
        "交换 page/block 而不重算身份，块在构造期即被拒")
    swapped_block = _rebuild_block(
        first, page_number=last.page_number, block_index=last.block_index)
    check(swapped_block.evidence_block_id != first.evidence_block_id
          and swapped_block.assert_identity() is None,
          "反例前置：交换坐标后的块已把身份重算成自洽值")
    kwargs = _closure_kwargs(handoff)
    kwargs["evidence_blocks"] = tuple(
        swapped_block if i == a else blocks[i] for i in range(len(blocks)))
    raises(lambda kw=kwargs: SB._assert_upstream_closure(**kw),
           SchemaValidationError, "",
           "交换两个 block 的 page/block（并重算全部身份）必须被闭合门拒绝")

    # (2) 终态侧：交换两个终态的 page/block（保留 terminal_id）后，独立复核对不上。
    def _swap_places(ts):
        ts = list(ts)
        i = next(k for k, t in enumerate(ts)
                 if t.evidence_block_id == first.evidence_block_id)
        j = next(k for k, t in enumerate(ts)
                 if t.evidence_block_id == last.evidence_block_id)
        ts[i], ts[j] = (
            dataclasses.replace(ts[i], page_number=ts[j].page_number,
                                block_index=ts[j].block_index),
            dataclasses.replace(ts[j], page_number=ts[i].page_number,
                                block_index=ts[i].block_index))
        return tuple(ts)

    swapped_snap = _mutate_terminals(handoff, _swap_places)
    check(isinstance(swapped_snap, SpanBuildSnapshot),
          "交换终态 page/block 后构建本身不报错（因此必须靠独立重建发现）")
    check(swapped_snap.input_fingerprint != snapshot.input_fingerprint,
          "交换终态 page/block 必须改变 input fingerprint（坐标进入封闭公式）")
    raises(lambda: SV.verify_span_snapshot(swapped_snap, handoff),
           SchemaValidationError, "独立重建结果与待复核快照不等",
           "交换终态 page/block 的快照在独立复核处必须被拒")

    # (3) 伪造终态的 schema 版本：闭合门的逐终态版本校验直接拒绝。
    def _forge_schema(ts):
        return tuple(
            dataclasses.replace(t, terminal_schema_version="als-2")
            if t.terminal_kind == "alignment"
            and t.terminal_schema_version == V.ALIGN_SCHEMA_VERSION else t
            for t in ts)

    raises(lambda: _mutate_terminals(handoff, _forge_schema),
           SchemaValidationError, "终态 schema_version 必须为",
           "把终态 schema 版本改成已退役的 als-2 必须被拒绝（版本进闭合门）")

    # (4) 伪造终态版本 / 自报长度 / 定位器 / ID。
    kwargs = _closure_kwargs(handoff)
    kwargs["terminals"] = tuple(
        dataclasses.replace(t, aligner_version="al-2") for t in terminals)
    raises(lambda kw=kwargs: SB._assert_upstream_closure(**kw),
           SchemaValidationError, "终态 aligner_version 必须为 al-3",
           "伪造终态 aligner_version 必须被闭合门拒绝")
    kwargs = _closure_kwargs(handoff)
    kwargs["terminals"] = tuple(
        dataclasses.replace(t, block_char_length=t.block_char_length + 1)
        for t in terminals)
    raises(lambda kw=kwargs: SB._assert_upstream_closure(**kw),
           SchemaValidationError, "自报长度不得覆盖真实文本",
           "伪造终态自报长度必须被闭合门拒绝（长度按真实文本重算）")

    for field in ("terminal_locator", "terminal_id",
                  "partition_validator_version"):
        forged = tuple(dataclasses.replace(t, **{field: _flip(getattr(t, field))})
                       for t in terminals)
        mutated_snap = _mutate_terminals(handoff, lambda _ts, f=forged: f)
        check(mutated_snap.input_fingerprint != snapshot.input_fingerprint,
              f"伪造终态 {field} 必须改变 input fingerprint（该字段进入封闭公式）")
        raises(lambda s=mutated_snap: SV.verify_span_snapshot(s, handoff),
               SchemaValidationError, "独立重建结果与待复核快照不等",
               f"伪造终态 {field} 的快照必须被独立复核拒绝")

    # (5) structure snapshot 的 layout / outline / version 任一不符都被拒绝。
    real_structure = handoff.structure_snapshot
    for field, bad in (
            ("page_layout_id", _hex("other-layout")),
            ("outline_id", _hex("other-outline")),
            ("outline_locator", "ol-" + _hex("other-outline-loc")[:16]),
            ("outline_algorithm_version", "oa-2"),
            ("heading_profile_version", "hq-3"),
            ("table_region_version", "trg-2"),
            ("toc_reconciliation_version", "tbr-1"),
            ("document_version", "sha256-" + "0" * 16),
            ("content_fingerprint", _hex("other-content"))):
        forged_structure = _clone(real_structure, **{field: bad})
        object.__setattr__(handoff, "_structure_snapshot", forged_structure)
        try:
            report = SV.verify_outline_structure_snapshot(handoff)
            check(report["ok"] is False and report["problem_count"] >= 1,
                  f"结构终态 {field} 被篡改后，结构复核必须报 ok=False")
            raises(lambda: SB.build_span_snapshot(handoff, stage=_STAGE),
                   SchemaValidationError, "canonical 不相等",
                   f"结构终态 {field} 被篡改后，正式入口必须拒绝")
        finally:
            object.__setattr__(handoff, "_structure_snapshot", real_structure)

    # (6) 复核结论只包在 wrapper 里，不改写 raw 快照，也不得被序列化。
    check(live["verified"].snapshot is snapshot,
          "复核结论只包在 VerifiedSpanSnapshot 里，raw 快照不被改写")
    raises(lambda: live["verified"].to_dict(), SchemaValidationError, "不得序列化",
           "VerifiedSpanSnapshot 是运行时能力，不得序列化")
    check(SB.build_span_snapshot(handoff, stage=_STAGE).input_fingerprint
          == snapshot.input_fingerprint,
          "全部反例还原后，三方对齐回到首次一致的状态")


# ---------------------------------------------------------------------------
# T8b handoff / provider / policy 与 input fingerprint
# ---------------------------------------------------------------------------

def _t8b_handoff_provider_policy() -> None:
    live = _live()
    handoff = live["handoff"]
    snapshot = live["snapshot"]
    policy = handoff.qualification_policy
    trusted = SB._compose_trusted_input(SB._build_span_input(handoff, stage=_STAGE))
    policy_attr = "_policy_provider_authority_fingerprint"

    # (1) §18.3.5 的字段与顺序固定，四个 provider 的 version / fingerprint 全在公式里。
    check(tuple(f.name for f in dataclasses.fields(TrustedBuildInput)) == (
        "page_layout", "outline", "structure", "evidence", "terminals",
        "qualification", "rules", "handoff"),
        "封闭输入载荷的字段与顺序由规格钉死（不得增删换序）")
    check(tuple(n for n, _ in SB.SS.TRUSTED_INPUT_GROUPS) == (
        "page_layout", "outline", "structure", "evidence", "qualification",
        "rules", "handoff"),
        "固定长度分组表与规格一致（terminals 逐终态变长，不在此表）")
    check(len(trusted.page_layout) == 6 and len(trusted.outline) == 8
          and len(trusted.structure) == 5 and len(trusted.evidence) == 7
          and len(trusted.qualification) == 13 and len(trusted.rules) == 5
          and len(trusted.handoff) == 10
          and len(trusted.terminals) == len(handoff.alignment.terminals),
          "封闭输入载荷各分组长度与规格一致")
    check(trusted.structure[3] == V.OUTLINE_STRUCTURE_PROVIDER_VERSION
          and trusted.structure[4]
          == handoff._structure_provider_authority_fingerprint,
          "structure provider 的 version 与 authority fingerprint 进入公式")
    check(trusted.evidence[3] == V.EVIDENCE_GATEWAY_PROVIDER_VERSION
          and trusted.evidence[4]
          == handoff._evidence_gateway_authority_fingerprint,
          "evidence gateway 的 version 与 authority fingerprint 进入公式")
    check(trusted.qualification[11] == V.QUALIFICATION_POLICY_PROVIDER_VERSION
          and trusted.qualification[12]
          == handoff.policy_provider_authority_fingerprint,
          "qualification policy provider 的 version 与 authority fingerprint 进入公式")
    check(trusted.terminals[0][6] == V.ALIGNMENT_TERMINAL_PROVIDER_VERSION
          and trusted.terminals[0][7]
          == handoff._terminal_provider_authority_fingerprint,
          "terminal provider 的 version 与 authority fingerprint 进入公式")
    check(len(handoff.authority_fingerprints()) == 4
          and all(len(v) == 64 for v in handoff.authority_fingerprints().values())
          and len(set(handoff.authority_fingerprints().values())) == 4,
          "四项 provider authority 指纹必须存在、形状合法且互不相同")
    check(trusted.handoff[0] == handoff.issuer_scope == "live"
          and trusted.handoff[1] == handoff.source_kind == "current_store"
          and trusted.handoff[2] == handoff.handoff_identity
          and trusted.handoff[3] == V.VERIFIED_TS3_HANDOFF_VERSION,
          "handoff 自身的 scope / source_kind / identity / issuer version 进入公式")
    check(trusted.handoff[4] == handoff.layout_capability.authority_fingerprint
          and trusted.handoff[5] == handoff.layout_capability.issuer_version
          and trusted.handoff[6] == handoff.alignment.authority_fingerprint
          and trusted.handoff[7] == handoff.alignment.issuer_version
          and trusted.handoff[8] == handoff.evidence_authority.authority_fingerprint
          and trusted.handoff[9] == handoff.evidence_authority.issuer_version,
          "三级上游能力的 authority fingerprint 与 issuer version 全部进入公式")

    # (2) 四项 provider authority 指纹都必须能被**独立重算**，并且被改动后**在构建边界**
    #     就被拒绝。仅靠"复核时用同一份被改过的交接重建"是不够的：那会让载荷与交接自洽，
    #     改动反而能同时通过构建与复核；因此这里的期望是构建期 fail-closed。
    check(trusted.structure[4] == SB._structure_provider_authority_fingerprint(
        scope=handoff.issuer_scope, source_kind=handoff.source_kind,
        issuer_version=handoff.issuer_version, layout=handoff.page_layout,
        outline=handoff.document_outline),
        "structure provider authority 指纹必须能被独立重算并与载荷一致")
    check(trusted.evidence[4] == SB._evidence_gateway_authority_fingerprint(
        scope=handoff.issuer_scope, source_kind=handoff.source_kind,
        issuer_version=handoff.issuer_version,
        resolved_db_identity=handoff.evidence_authority.resolved_db_identity,
        snapshot=handoff.evidence_snapshot,
        member_identity_sha256=SB.sha256_canonical(
            [list(m) for m in handoff.evidence_snapshot.member_identities()])),
        "evidence gateway authority 指纹必须能被独立重算并与载荷一致")
    check(trusted.terminals[0][7]
          == SB._terminal_provider_authority_fingerprint(
              scope=handoff.issuer_scope, source_kind=handoff.source_kind,
              issuer_version=handoff.issuer_version, alignment=handoff.alignment),
          "terminal provider authority 指纹必须能被独立重算并与载荷一致")
    for attr in ("_structure_provider_authority_fingerprint",
                 "_evidence_gateway_authority_fingerprint",
                 "_terminal_provider_authority_fingerprint",
                 policy_attr):
        real = getattr(handoff, attr)
        object.__setattr__(handoff, attr, _flip(real))
        try:
            if attr == policy_attr:
                raises(lambda: SB.build_span_snapshot(handoff, stage=_STAGE),
                       SchemaValidationError, "策略 provider authority 指纹与期望值不一致",
                       f"篡改 {attr} 后正式入口必须拒绝（策略指纹有期望值可比对）")
                continue
            raises(lambda: SB.build_span_snapshot(handoff, stage=_STAGE),
                   SchemaValidationError, "provider authority 指纹不可独立重算",
                   f"篡改 {attr} 后构建边界必须立即拒绝，"
                   f"不得产出一份'载荷与交接自洽'的快照")
        finally:
            object.__setattr__(handoff, attr, real)
    check(SB.build_span_snapshot(handoff, stage=_STAGE).input_fingerprint
          == snapshot.input_fingerprint,
          "四项指纹全部还原后，构建必须重新给出与首次一致的 input fingerprint")

    # 上游 capability 的 authority fingerprint 同样进入 handoff 分组。
    real_lf = handoff.layout_capability._authority_fingerprint
    object.__setattr__(handoff.layout_capability, "_authority_fingerprint",
                       _flip(real_lf))
    try:
        mutated = SB.build_span_snapshot(handoff, stage=_STAGE)
    finally:
        object.__setattr__(handoff.layout_capability, "_authority_fingerprint",
                           real_lf)
    check(mutated.input_fingerprint != snapshot.input_fingerprint,
          "受信版式的 authority fingerprint 必须进入 input fingerprint")
    check(SB.build_span_snapshot(handoff, stage=_STAGE).input_fingerprint
          == snapshot.input_fingerprint,
          "还原后 input fingerprint 必须回到首次一致的值")

    # (3) 官方 gateway + 替代 DB：签发域隔离，替代库拿不到 live 入口。
    substitute = EG._issue_pinned_evidence_authority(
        {"fixture_file_sha256": "0" * 64},
        db_path=_REPO / "data" / "sections.db")
    check(substitute.issuer_scope == "pinned_acceptance"
          and substitute.source_kind == "historical_run"
          and substitute.trust_root_file_sha256 == "0" * 64,
          "反例前置：pinned 签发域 / 来源类别 / 信任锚都与 live 不同")
    check(substitute.authority_fingerprint
          != handoff.evidence_authority.authority_fingerprint,
          "反例前置：pinned 权威的 authority fingerprint 与 live 不同")
    raises(lambda: align_evidence_set_verified(live["vlayout"], substitute),
           CapabilityError, "不在允许集合",
           "官方 gateway + 替代 DB 的权威不得进入 live 对齐入口")
    raises(lambda: SB.issue_live_ts3_handoff(live["vlayout"], live["alignment"],
                                             substitute),
           CapabilityError, "不在允许集合",
           "官方 gateway + 替代 DB 的权威不得进入 live 交接签发入口")
    # 即便 pinned 签发指向**同一个真实库文件**，签发域不符仍然拒绝：隔离不靠路径。
    same_db = EG._issue_pinned_evidence_authority({"fixture_file_sha256": "0" * 64})
    check(same_db.resolved_db_identity["sha256"]
          == handoff.evidence_authority.resolved_db_identity["sha256"],
          "反例前置：同一库文件签出的 pinned 权威指向同一份真实库字节")
    raises(lambda: align_evidence_set_verified(live["vlayout"], same_db),
           CapabilityError, "不在允许集合",
           "同一库文件但签发域为 pinned 的权威仍不得进入 live 入口")
    raises(lambda: EG._issue_pinned_evidence_authority({"fixture_file_sha256": "x"}),
           SchemaValidationError, "64 位 fixture_file_sha256",
           "pinned 签发要求根锁提供合法哈希（不得省略信任锚）")

    # (4) 官方 terminal factory + 手造 `EvidenceSetAlignment`。
    plain_alignment = EvidenceSetAlignment(
        page_layout_id=handoff.page_layout.page_layout_id,
        evidence_set_version=handoff.evidence_snapshot.evidence_set_version,
        aligner_version=V.ALIGNER_VERSION,
        blocks=handoff.alignment.alignment.blocks,
        summary=alignment_summary(handoff.alignment.alignment.blocks),
        snapshot=handoff.evidence_snapshot)
    raises(lambda: SB.issue_live_ts3_handoff(live["vlayout"], plain_alignment,
                                             live["authority"]),
           CapabilityError, "不是本进程由正式签发路径产生的实例",
           "手造 EvidenceSetAlignment 不得冒充官方 terminal factory 的产物")
    # 官方 terminal 元组也不是能力：交给闭合门的只有交接绑定的那一份。
    kwargs = _closure_kwargs(handoff)
    kwargs["terminals"] = tuple()
    raises(lambda kw=kwargs: SB._assert_upstream_closure(**kw),
           SchemaValidationError, "终态集合与 Evidence 块集合不完全相等",
           "官方 terminal 元组只有经交接绑定才有效；空元组必须被拒")

    # (5) 保住 PDF 哈希却篡改版式并把全部身份重算一遍：对照签发必须拒绝。
    layout = handoff.page_layout
    tampered = type(layout).create(
        document_id=layout.document_id, document_version=layout.document_version,
        company_id=layout.company_id, source_file_sha256=layout.source_file_sha256,
        pages=layout.pages[:-1])
    check(tampered.source_file_sha256 == layout.source_file_sha256
          and tampered.page_layout_id != layout.page_layout_id
          and tampered.page_count != layout.page_count,
          "反例前置：篡改版式保住了 PDF 哈希，但已把版式身份重算成自洽值")
    raises(lambda: LB._issue_cross_checked_layout(
        _SAMPLE_PDF, company_id=_COMPANY_ID, document_id=_DOCUMENT_ID,
        expected_layout=tampered, scope="pinned_acceptance",
        source_kind="historical_run",
        issuer_version=V.PINNED_PAGE_LAYOUT_ISSUER_VERSION,
        root_identity={"locked_pdf_sha256": layout.source_file_sha256}),
        SchemaValidationError, "篡改版式",
        "『保住 PDF 哈希却篡改版式』必须被对照签发的 canonical 比对拒绝")
    raises(lambda: LB._issue_cross_checked_layout(
        _SAMPLE_PDF, company_id=_COMPANY_ID, document_id=_DOCUMENT_ID,
        expected_layout=layout, scope="live", source_kind="current_store",
        issuer_version=V.VERIFIED_PAGE_LAYOUT_ISSUER_VERSION, root_identity={}),
        SchemaValidationError, "对照签发只用于 pinned_acceptance",
        "对照签发路径不得被 live 域使用（否则等于绕过重建的入口）")

    # (6) 任意 duck-typed / Protocol 对象、自报 fingerprint 一律不取得资格。
    class _FakeHandoff:
        """连 authority fingerprint 都能自报成真值的仿冒件。"""

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

    fake = _FakeHandoff(handoff)
    check(fake.handoff_identity == handoff.handoff_identity
          and fake.authority_fingerprints() == handoff.authority_fingerprints(),
          "反例前置：Protocol 仿冒件能自报出与真品相同的全部指纹")
    check(capability_scope(fake) is None, "仿冒件不在签发登记表中")
    raises(lambda: SB.build_span_snapshot(fake, stage=_STAGE),
           CapabilityError, "不是本进程由正式签发路径产生的实例",
           "任意 duck-typed / Protocol 对象不得进入正式入口")

    class _FakeAuthority:
        """自报 authority fingerprint 的仿冒证据权威。"""

        authority_fingerprint = "f" * 64
        issuer_scope = "live"
        issuer_version = V.VERIFIED_CURRENT_EVIDENCE_AUTHORITY_VERSION
        provider_version = V.EVIDENCE_GATEWAY_PROVIDER_VERSION
        resolved_db_identity = dict(
            handoff.evidence_authority.resolved_db_identity)

        def load_snapshot_and_blocks(self, **_kw):  # pragma: no cover
            raise AssertionError("仿冒权威不应被调用")

    raises(lambda: align_evidence_set_verified(live["vlayout"], _FakeAuthority()),
           CapabilityError, "不是本进程由正式签发路径产生的实例",
           "自报 authority fingerprint 的仿冒权威不得进入正式对齐入口")
    raises(lambda: SB.issue_live_ts3_handoff(
        live["vlayout"], live["alignment"], _FakeAuthority()),
        CapabilityError, "不是本进程由正式签发路径产生的实例",
        "自报 authority fingerprint 的仿冒权威不得进入交接签发入口")

    # (7) 伪造 policy / 审批记录后重算全 ID 均拒绝；历史 A 指纹可复现且不变。
    b_assets = ((SP.POLICY_DIR / SP.APPROVAL_RECORD_FILENAME).is_file(),
                (SP.POLICY_DIR / SP.FROZEN_RECORD_FILENAME).is_file())
    check(all(b_assets) if _STAGE == "threshold_enabled" else not any(b_assets),
          f"固定策略目录里 B 阶段审批 / 冻结记录的存在性必须与当前阶段一致"
          f"（当前 {_STAGE!r}，实际 approval/frozen = {b_assets}）")
    # A 条目**永远**只绑定 registry entry 与 distribution 记录：这里显式按 A 键查询，
    # 因此进入 B 之后（注册表里多了 B 条目）这条历史事实仍然逐字成立。
    record = SP.registry_entry_record(SP.DEFAULT_POLICY_KEY)
    check(sorted(record) == ["approval_record_sha256", "distribution_record_sha256",
                             "frozen_record_sha256", "policy_key",
                             "registry_entry", "registry_entry_sha256"]
          and record["approval_record_sha256"] is None
          and record["frozen_record_sha256"] is None,
          "A 记录只绑定 registry entry 与 distribution 记录，B 两槽恒为 None"
          "（不因 B 条目存在而改变）")
    check(record["registry_entry_sha256"] == sha256_canonical(record["registry_entry"])
          and "policies" not in record["registry_entry"],
          "A 身份的 entry 哈希只覆盖**该条目自身**：注册表里多一个策略条目并不会"
          "改变历史 A 的 provider fingerprint")
    # A 策略记录必须仍能**自描述地读回**（不依赖当前全局常量），历史 A 指纹可复现。
    a_policy = SpanQualificationPolicy.from_dict(json.loads(
        (SP.POLICY_DIR / record["registry_entry"]["file"])
        .read_text(encoding="utf-8")))
    a_policy_fp = sha256_canonical((
        V.QUALIFICATION_POLICY_PROVIDER_VERSION, record["registry_entry_sha256"],
        a_policy.policy_id, record["distribution_record_sha256"], None, None))
    check(a_policy.stage == "distribution_only"
          and a_policy.span_confidence_min is None
          and a_policy_fp == SP.policy_provider_authority_fingerprint(a_policy),
          "历史 A 的 policy provider authority 指纹必须可由 A 记录自身本地复现"
          "（进 B 之后仍然如此）")
    check(SP.policy_provider_authority_fingerprint(a_policy)
          == SP.policy_provider_authority_fingerprint(a_policy),
          "policy provider authority 指纹必须幂等（同一记录两次解析结果相同）")
    # 本轮 live 交接绑定的则是**当前阶段**的策略身份。
    check(SP.policy_provider_authority_fingerprint(policy)
          == handoff.policy_provider_authority_fingerprint,
          f"当前阶段（{_STAGE!r}）的 policy provider authority 指纹必须与交接绑定值一致")
    check(SP.registry_entry_record(SP.DEFAULT_POLICY_KEY)["distribution_record_sha256"]
          == record["distribution_record_sha256"]
          and SP.registry_document()["policies"][SP.DEFAULT_POLICY_KEY]
          ["policy_fingerprint"] == a_policy.policy_fingerprint,
          "固定目录里的 A 记录字节必须可复现，且注册表钉住的 A 指纹不变")
    check(snapshot.input_fingerprint
          == sha256_canonical(snapshot.trusted_input.to_dict()),
          "input fingerprint 必须可由封闭载荷本地复现")

    # 伪造一个"B 阶段口径"的策略记录（同键、全部字段自洽、指纹自洽）。
    # 注：`create()` 自身就要求 `span_confidence_min` 恒等于全局 A/B 开关，所以"在 A
    # 轮伪造一份 B 记录"这件事本身不成立；下面先把开关临时拨到 B 才造得出来它，
    # 随后证明：即便它完全自洽，两条正式入口都拒绝它。伪造阈值刻意取与冻结策略
    # **不同**的值，因此"策略必须由正式审批/冻结资产派生"这条要求被单独证伪。
    saved_gate = V.SPAN_CONFIDENCE_MIN
    try:
        V.SPAN_CONFIDENCE_MIN = 0.9
        fake_b = SpanQualificationPolicy.create(
            policy_key=policy.policy_key, stage="threshold_enabled",
            span_confidence_min=0.9,
            factor_entries=policy.factor_entries, completion_enabled=True,
            set_complete_supported=True, max_snippets_per_node=3,
            max_snippet_chars=120, min_snippet_chars=20,
            max_total_snippet_chars=300,
            sentence_terminators=policy.sentence_terminators,
            closing_quotes=policy.closing_quotes)
        check(fake_b.policy_fingerprint != policy.policy_fingerprint
              and fake_b.stage == "threshold_enabled"
              and fake_b.span_confidence_min == 0.9,
              "反例前置：伪造的 B 口径策略自身完全自洽（含指纹）")
        raises(lambda: SP.policy_provider_authority_fingerprint(fake_b),
               SchemaValidationError, "指纹与注册表钉住值不一致",
               "伪造 B 口径策略记录（重算全 ID 后）必须被固定注册表拒绝")
        raises(lambda: SB._QualificationPolicyProvider().for_verification(fake_b),
               SchemaValidationError, "指纹与注册表钉住值不一致",
               "即使在 B 口径下，伪造策略也不得经复核方向进入正式链路")
    finally:
        V.SPAN_CONFIDENCE_MIN = saved_gate
    check(V.SPAN_CONFIDENCE_MIN == saved_gate,
          f"拨回后全局 A/B 开关必须逐字还原为 {saved_gate!r}")
    # 回到本轮唯一授权的口径后，伪造的 B 记录依然被固定注册表拒绝
    # （`__post_init__` 只在**构造**时对 A/B 开关负责，事后留下的记录必须靠字节
    # 指纹比对才能识破，这正是 provider 每次重算而不采信自报值的原因）。
    raises(lambda: SB._QualificationPolicyProvider().for_verification(fake_b),
           SchemaValidationError, "指纹与注册表钉住值不一致",
           "回到本轮口径后，伪造的 B 记录仍不得经复核方向进入正式链路")
    raises(lambda: SB._QualificationPolicyProvider()
           .current_for_new_build(STAGE.next_stage()),
           SchemaValidationError, "只允许新建",
           f"当前为 {_STAGE!r} 时不得按 {STAGE.next_stage()!r} 阶段新建快照"
           "（不得静默跨阶段）")
    raises(lambda: SP.policy_provider_authority_fingerprint(fake_b),
           SchemaValidationError, "指纹与注册表钉住值不一致",
           "本轮口径下伪造策略记录同样被固定注册表拒绝")
    raises(lambda: SB._build_span_input(
        handoff, policy=fake_b, expected_policy_authority_fingerprint="a" * 64),
        SchemaValidationError, "",
        "伪造策略不得作为复核策略进入正式入口")

    # 改动一个边界因子（重新冻结、指纹自洽）仍然拒绝。
    factors = list(policy.factor_entries)
    factors[0] = dataclasses.replace(factors[0], factor=0.5)
    altered = SpanQualificationPolicy.create(
        policy_key=policy.policy_key, stage=policy.stage,
        span_confidence_min=policy.span_confidence_min,
        factor_entries=tuple(factors),
        completion_enabled=policy.completion_enabled,
        set_complete_supported=policy.set_complete_supported,
        max_snippets_per_node=policy.max_snippets_per_node,
        max_snippet_chars=policy.max_snippet_chars,
        min_snippet_chars=policy.min_snippet_chars,
        max_total_snippet_chars=policy.max_total_snippet_chars,
        sentence_terminators=policy.sentence_terminators,
        closing_quotes=policy.closing_quotes)
    check(altered.policy_fingerprint != policy.policy_fingerprint,
          "反例前置：改动一个边界因子即改变了策略指纹")
    raises(lambda: SP.policy_provider_authority_fingerprint(altered),
           SchemaValidationError, "指纹与注册表钉住值不一致",
           "替换任一 policy record（含只改一个因子）必须被拒绝")
    raises(lambda: dataclasses.replace(policy, max_snippets_per_node=9),
           SchemaValidationError, "指纹与载荷重算不一致",
           "替换 policy 记录的简介限额（不重算指纹）同样必须被拒绝")
    raises(lambda: SP.resolve_qualification_policy("no-such-policy-key"),
           SchemaValidationError, "策略注册表未登记策略",
           "未登记的策略键必须拒绝（不得退回默认策略）")
    summary = SP.policy_registry_summary()
    if _STAGE == "distribution_only":
        check(summary["stage"] == _STAGE
              and summary["span_confidence_min"] is None
              and not summary["completion_enabled"]
              and not summary["set_complete_supported"]
              and SP.ab_gate_truth_table()["stage"] == _STAGE,
              "TS4-A 真值表：分布口径、无阈值、不得完成、不得 set_complete")
    else:
        check(summary["stage"] == _STAGE
              and summary["span_confidence_min"] == V.SPAN_CONFIDENCE_MIN
              and summary["completion_enabled"]
              and summary["set_complete_supported"]
              and SP.ab_gate_truth_table()["stage"] == _STAGE,
              "TS4-B 真值表：阈值与全局常量同步、**可**判完成（可判定 ≠ 已达成）")
        check(summary["default_policy_key"] == SP.DEFAULT_POLICY_KEY
              and summary["default_policy_key"] != summary["current_policy_key"],
              "注册表声明的默认键（A 条目）不得随阶段漂移；当前阶段用独立的 B 键")
    # 跨到**另一个**阶段：本轮口径的正式新建入口必须先拒绝（不得静默跨阶段）。
    other_stage = STAGE.next_stage()
    saved = V.SPAN_CONFIDENCE_MIN
    try:
        V.SPAN_CONFIDENCE_MIN = (None if other_stage == "distribution_only"
                                 else 0.9)
        check(SP.ab_gate_truth_table()["stage"] == other_stage,
              f"A/B 真值表确实以 versions.SPAN_CONFIDENCE_MIN 为唯一输入"
              f"（期望 {other_stage!r}）")
        if other_stage == "threshold_enabled":
            raises(lambda: SP.resolve_distribution_policy(),
                   SchemaValidationError, "",
                   "进入 B 阶段后，distribution-only 的正式解析必须拒绝")
        raises(lambda: SB.build_span_snapshot(handoff, stage=_STAGE),
               SchemaValidationError, "只允许新建",
               f"跨到 {other_stage!r} 后按 {_STAGE!r} 阶段新建必须拒绝"
               "（不得静默跨阶段）")
    finally:
        V.SPAN_CONFIDENCE_MIN = saved
    check(V.SPAN_CONFIDENCE_MIN == saved,
          f"反例后必须把全局阈值逐字还原为 {saved!r}（不得污染后续用例）")
    check(SP.ab_gate_truth_table()["stage"] == _STAGE,
          f"还原后 A/B 真值表必须回到 {_STAGE!r}")
    check(SB.build_span_snapshot(handoff, stage=_STAGE).input_fingerprint
          == snapshot.input_fingerprint,
          "全部反例还原后，input fingerprint 必须与首次逐位相同")


# ---------------------------------------------------------------------------
# 数据库只读证明
# ---------------------------------------------------------------------------

def _db_identity(path: pathlib.Path) -> dict:
    st = path.stat()
    return {"size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main() -> dict:
    db_path = _REPO / "data" / "evidence.db"
    before = _db_identity(db_path)
    started = time.time()
    for fn in (_t5_entry_rejects_forged_inputs,
               _t6_evidence_snapshot_closure,
               _t7_terminal_closure,
               _t8_three_way_alignment,
               _t8b_handoff_provider_policy):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            check(False, f"{fn.__name__} 无法完成：{type(e).__name__}: {e}")
    after = _db_identity(db_path)
    check(before == after,
          f"data/evidence.db 在运行前后必须逐字节不变：{before} != {after}")
    _results["elapsed_seconds"] = round(time.time() - started, 2)
    if _LIVE and "seconds" in _LIVE:
        _results["live_chain_seconds"] = round(_LIVE["seconds"], 2)
    _results["live_chain_document_id"] = _DOCUMENT_ID
    return _results


if __name__ == "__main__":
    import json as _json

    _res = main()
    print(_json.dumps(_res, ensure_ascii=False, indent=2))
    raise SystemExit(0 if _res["failed"] == 0 else 1)

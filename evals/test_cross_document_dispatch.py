"""跨文档联合检索 §L2/§L3/§L4：**逐份派发 + 逐份四臂 + 后继来源轴归属**（T21/T22）。

跑法（无管道/无重定向）：`PYTHONIOENCODING=utf-8 python -m evals.test_cross_document_dispatch`

本模块只测**派发域、归属、读回键名**这三件最容易静默出错的事，不重跑整条研究链（那在
`test_demo_topic_runtime` / `test_demo_pack_set` 里）。「静默出错」在这里指：
**读回时不报错，但读出来的东西是假的**——「三份都注册了」看起来像「三份都读了」，
「A 份的材料」记到 B 份名下，后继把「本轮故意没跑」判成「两套来源打架」。每条都有反例。

1. **派发域**（`_dispatch_source_keys`）：锚无条件派发（§L1.5 规则 6 的单文档退化保护）；
   其余成员只在「责任说必须检索」且导航提供方确有跨源能力时派发；无锚 fail-closed
   （**不得**退化成「谁都不派发」而让整轮静默成功）。
2. **单文档提供方不越权**（`_navigate_one_source`）：锚可以走它，非锚必须 fail-closed
   ——拿锚的读集顶替另一份正是 §L3.3 明令禁止的回退。
3. **材料归属**（`derive_source_aspect_outcomes`）：材料必须落在**产出它的那一次调用寻址的
   那一份**上。X 反例：只给 DOC_2024 材料、DOC_2025 也有真实调用痕迹时，DOC_2025 **不得**
   凭这些材料成立臂 A（那会让「这是谁的材料」在读回时不可知）。三条铁律各自钉一次：
   臂 A 只在有材料那一份；臂 B 必须引用**aspect 级**审计 id（逐份粒度在记录本体里）；
   没痕迹的成员必须留下**明写的**未检索记录，不得沉默、不得改写成「无需检索」。
4. **后继来源轴归属**（`merge_source_axis`）：被重研 aspect 取本轮（`new`），其余 aspect 取
   继承（`base`）。X 反例：非重研 aspect 在 `new` 里落臂 A（本轮根本没导航它）必须
   fail-closed；另一侧，继承 aspect 在 `new` 里落 C2 `not_dispatched`（本轮故意没跑）
   **不得**被读成「两套来源打架」——那会让每一次真实后继都必然失败。
5. **读回键名**（`DocumentSourceSet.to_dict`）：成员键是 `source_document_key`。写错键名不
   报错，只会让逐份台账的文档列**整列渲染成空**——那正是「注册了三份」冒充「读了三份」的
   假象，所以这里钉死键名、非空 document_id 与清单序位。
6. **组合 payload 解析**（`CombinedPayloadResolver`）：0 命中 = dangling（交上层 fail-closed），
   ≥2 命中 = 歧义即拒。跨源之后三份的 payload 都要能解析，但**不得**让两份同时认领同一
   payload 而不留痕。
7. **臂 C2 的原因分档**（`dispatched_without_call`，`ssor-1`→`ssor-2`）：「已派发但导航没有
   产出候选」与「从未派发」改前同码 `not_dispatched`，读回时签名完全同形。X 反例：派发记录
   **不得**改判有材料（臂 A）或有完成调用（臂 B）的成员；原因码必须在闭集内，不得用自由文本
   冒充；该档不得为没发生的调用造 attempt / call_id / 证书。

公司无关：一律用合成公司/合成文档键，不引入 300750 / 宁德时代 / 固定页码分支。
"""

from __future__ import annotations

import dataclasses
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals.test_topic_pack_contract_reachability import (  # noqa: E402  真实冻结契约投影（复用）
    _load_frozen_assets, _snapshot_from_aspect)
from harness import source_manifest as SM  # noqa: E402
from harness import topic_runtime as TR  # noqa: E402
from harness import topic_schema as TS  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

_COMPANY = "ACME"
_CURRENT = "DOC_2025"
_OLDER = "DOC_2024"
_BOND = "DOC_BOND"
_ROLES = ("current_state_source", "history_and_conflict_source",
          "topic_participating_source")


def _keys(*ids: str) -> tuple[SM.SourceDocumentKey, ...]:
    return tuple(SM.SourceDocumentKey(
        company_id=_COMPANY, document_id=doc_id,
        document_version="sha256-" + f"{i + 1:x}" * 16,
        evidence_set_version="set-" + f"{i + 1:x}" * 12)
        for i, doc_id in enumerate(ids))


def _three_doc_set() -> TR.DocumentSourceSet:
    return TR.DocumentSourceSet(members=tuple(zip(_keys(_CURRENT, _OLDER, _BOND), _ROLES)))


def _all_aspects():
    contract, sp, csha, spfp, ers, _ts = _load_frozen_assets()
    out = []
    for sec in contract.sections:
        for topic in sec.topics:
            for question in topic.questions:
                for aspect in question.aspects:
                    out.append(_snapshot_from_aspect(aspect, csha, sp, spfp, ers))
    return out


def _plain_aspects():
    """两个**普通事实栏目**（不含 `set_complete`）：责任里 proof 恒为 false。"""
    plain = [s for s in _all_aspects() if "set_complete" not in set(s.coverage_rules or ())]
    if len(plain) < 2:
        raise AssertionError("冻结契约里竟然不足两个非 set_complete 栏目")
    return plain[0], plain[1]


def _pack_axis_stub(*, source_set):
    """`merge_source_axis` 只读来源轴三件；用一个最小 typed 容器承载它们。

    不构造完整 Pack：本模块测的是归属规则，不是 Pack 构造。三个属性用的都是
    `TopicResearchPack` 的真实字段名与真实类型（`DocumentSourceSet` 等），因此规则一旦读到
    别的字段名就会当场 `AttributeError`，不会静默通过。
    """
    stub = types.SimpleNamespace(
        source_set=source_set, aspect_source_responsibility=(),
        source_aspect_outcomes=(), source_comparison_audit=None)
    return stub


def _outcome(aspect_id, key, arm, *, fingerprint, material_ids=(), search_record=None,
             not_required_basis=()):
    return TS.SourceAspectOutcome(
        aspect_id=aspect_id, source_document_key=key, arm=arm,
        responsibility_fingerprint=fingerprint, material_ids=material_ids,
        search_record=search_record, not_required_basis=not_required_basis)


class _NavSingleDoc:
    """只有单文档能力的导航提供方替身。"""

    rule_version = "anr-1"

    def __init__(self) -> None:
        self.calls = 0

    def candidates(self, aspect, *, requirement):
        self.calls += 1
        return _FakeDecision(aspect.aspect_id)

    def node_ids_for(self, decision):
        return decision.bound_node_ids


class _NavMultiDoc(_NavSingleDoc):
    """有跨源能力的替身：逐份精确派发，源集外的键即拒（无回退）。"""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[str] = []

    def candidates_for(self, aspect, *, requirement, source_key):
        document_id = str(source_key.get("document_id", ""))
        if document_id not in (_CURRENT, _OLDER, _BOND):
            raise TR.TopicRuntimeError(f"源集导航索引里没有文档 {document_id!r}")
        self.seen.append(document_id)
        return _FakeDecision(aspect.aspect_id)


@dataclasses.dataclass(frozen=True)
class _FakeDecision:
    aspect_id: str
    bound_node_ids: tuple[str, ...] = ("node-x",)


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

    def check_raises(fn, exc, msg: str) -> None:
        try:
            fn()
        except exc:
            check(True, msg)
        except Exception as other:  # noqa: BLE001
            check(False, f"{msg}（抛出 {type(other).__name__}: {other}）")
        else:
            check(False, f"{msg}（未抛出）")

    sources = _three_doc_set()
    aspect, other_aspect = _plain_aspects()
    requirement_like = types.SimpleNamespace(
        topic_id="topic-x", aspects=(aspect, other_aspect))

    # ------------------------------------------------------------------
    # 1. 派发域
    # ------------------------------------------------------------------
    resp = TR.derive_aspect_source_responsibility(aspect=aspect, sources=sources)
    by_doc = {r.source_document_key.document_id: r for r in resp}
    check(set(by_doc) == {_CURRENT, _OLDER, _BOND},
          "责任记录必须覆盖源集**全部**成员（不是只覆盖锚）")

    anchor_key = sources.current_state_key()
    check(anchor_key is not None and anchor_key.document_id == _CURRENT,
          "三文档源集的当前锚必须是 current_state_source 那一份")
    older_key = next(k for k in sources.keys() if k.document_id == _OLDER)
    bond_key = next(k for k in sources.keys() if k.document_id == _BOND)

    required_docs = {d for d, r in by_doc.items() if r.retrieval_required}
    check(required_docs, "本轮真实契约里上传文档的来源类落在 required 内（否则没有派发可言）")
    dispatch_multi = TR._dispatch_source_keys(
        aspect.aspect_id, resp, anchor=anchor_key, multi_source_navigation=True)
    dispatch_single = TR._dispatch_source_keys(
        aspect.aspect_id, resp, anchor=anchor_key, multi_source_navigation=False)

    check(_CURRENT in {k.document_id for k in dispatch_multi},
          "当前锚**无条件**派发（§L1.5 规则 6 的单文档退化保护）")
    check({k.document_id for k in dispatch_multi} == {_CURRENT} | required_docs,
          f"多源派发域 = 锚 ∪ 责任必须检索的成员（得到 "
          f"{sorted(k.document_id for k in dispatch_multi)}，应为 "
          f"{sorted({_CURRENT} | required_docs)}）")
    check([k.document_id for k in dispatch_single] == [_CURRENT],
          "单文档能力的导航提供方只派发锚（其余成员在四臂里落 C2，不得凭空消失）")
    check(len(dispatch_multi) == len({k.document_id for k in dispatch_multi}),
          "派发域不得出现重复成员（同一份不得在一次派发里出现两次）")
    check([k.document_id for k in dispatch_multi]
          == [k.document_id for k in sources.keys()
              if k == anchor_key or by_doc[k.document_id].retrieval_required],
          "派发域序位取自责任记录本身的来源集序位（不重排）")

    # X 反例：没有当前锚时必须 fail-closed，不得退化成「谁都不派发」而让整轮静默成功。
    no_anchor = TR.DocumentSourceSet(members=tuple(
        (k, "history_and_conflict_source") for k in _keys(_OLDER, _BOND)))
    check(no_anchor.current_state_key() is None, "X 反例前置：该源集确实没有当前锚")
    check_raises(
        lambda: TR._dispatch_source_keys(
            aspect.aspect_id, TR.derive_aspect_source_responsibility(
                aspect=aspect, sources=no_anchor),
            anchor=None, multi_source_navigation=True),
        TR.TopicRuntimeError,
        "X 反例：无当前锚时派发域必须 fail-closed（不得静默谁都不派发）")

    check(TR._dispatch_source_keys(
        other_aspect.aspect_id, resp, anchor=anchor_key,
        multi_source_navigation=True) == (),
        "责任台账里没有该 aspect 的行时派发域为空（不猜、不兜底）")

    # ------------------------------------------------------------------
    # 2. 单文档提供方不越权
    # ------------------------------------------------------------------
    single = _NavSingleDoc()
    decision, node_ids = TR._navigate_one_source(
        single, aspect, requirement=requirement_like,
        source_key=anchor_key, anchor=anchor_key)
    check(single.calls == 1 and node_ids == ("node-x",),
          "单文档提供方服务锚时走它与改前**逐字相同**的 candidates(...)")
    check(getattr(decision, "aspect_id", None) == aspect.aspect_id,
          "锚的导航决策身份必须与 aspect 闭合")

    check_raises(
        lambda: TR._navigate_one_source(
            _NavSingleDoc(), aspect, requirement=requirement_like,
            source_key=older_key, anchor=anchor_key),
        TR.TopicRuntimeError,
        "X 反例：单文档提供方被要求对**非锚**导航必须 fail-closed（不得用锚的读集顶替）")

    multi = _NavMultiDoc()
    for key in (anchor_key, older_key, bond_key):
        TR._navigate_one_source(multi, aspect, requirement=requirement_like,
                                source_key=key, anchor=anchor_key)
    check(multi.seen == [_CURRENT, _OLDER, _BOND],
          f"跨源提供方按**给定那一份**逐次派发（得到 {multi.seen}）")
    check_raises(
        lambda: TR._navigate_one_source(
            _NavMultiDoc(), aspect, requirement=requirement_like,
            source_key=SM.SourceDocumentKey(_COMPANY, "DOC_UNKNOWN", "v", "e"),
            anchor=anchor_key),
        TR.TopicRuntimeError,
        "X 反例：源集外的键必须 fail-closed（不回退到其中任何一份）")

    # ------------------------------------------------------------------
    # 3. 逐份四臂与材料归属
    # ------------------------------------------------------------------
    # 3a. 只有 DOC_2024 真实产出了材料。DOC_2025 与 DOC_BOND 完全没有调用痕迹。
    older_trace = {"call_ordinal": 1, "call_id": "call-2024-1",
                   "source_key": older_key, "status": "SUCCESS"}
    outcomes = TR.derive_source_aspect_outcomes(
        sources=sources, responsibilities=resp,
        material_ids_by_aspect_source={(aspect.aspect_id, older_key): ("material-2024",)},
        search_traces={aspect.aspect_id: [older_trace]},
        not_found_audits_by_aspect={}, unfulfilled_reason_by_aspect={})
    arm_by_doc = {o.source_document_key.document_id: o.arm for o in outcomes}
    check(arm_by_doc.get(_OLDER) == "A", "产出材料的那一份落臂 A")
    check(arm_by_doc.get(_CURRENT) != "A",
          f"X 反例：另一份**不得**凭别份的材料成立臂 A（得到 {arm_by_doc.get(_CURRENT)!r}）")
    check(arm_by_doc.get(_BOND) != "A",
          f"X 反例：第三份同样不得沾别份的材料（得到 {arm_by_doc.get(_BOND)!r}）")
    older_row = next(o for o in outcomes if o.source_document_key.document_id == _OLDER)
    check(older_row.material_ids == ("material-2024",),
          "臂 A 的材料 id 必须恰是**那一份自己**产出的")
    check(all(o.material_ids == () for o in outcomes
              if o.source_document_key.document_id != _OLDER),
          "非臂 A 的成员一律不得带材料归属（那是臂 A 的记录）")
    check(set(arm_by_doc) == {_CURRENT, _OLDER, _BOND},
          "逐来源台账必须覆盖源集全部成员（三份都注册了，三份都要有下场）")

    # 3b. 臂 B：该份完成了实际检查但没有材料 ⇒ 必须引用**aspect 级**审计 id。
    #     逐份粒度由记录本体（source_document_key + attempts）承载，不靠给审计加键。
    audit = types.SimpleNamespace(
        audit_id="nfap-audit-1", searched_need_ids=("need-1", "need-2"),
        time_window="2026-01-01..2026-01-02", qualified=True,
        qualification_reasons=())
    only_current_trace = dict(older_trace, source_key=anchor_key,
                              call_id="call-2025-1")
    b_outcomes = TR.derive_source_aspect_outcomes(
        sources=sources, responsibilities=resp,
        material_ids_by_aspect_source={},
        search_traces={aspect.aspect_id: [only_current_trace]},
        not_found_audits_by_aspect={aspect.aspect_id: audit},
        unfulfilled_reason_by_aspect={})
    b_by_doc = {o.source_document_key.document_id: o for o in b_outcomes}
    check(b_by_doc[_CURRENT].arm == "B", "有完成调用、无材料 ⇒ 臂 B")
    record = b_by_doc[_CURRENT].search_record
    check(record is not None and record.audit_id == audit.audit_id,
          "臂 B 必须引用其 aspect 级 NotFoundAudit 的 id（身份回声不是未命中的证据）")
    check(record is not None
          and record.source_document_key.document_id == _CURRENT
          and record.aspect_id == aspect.aspect_id,
          "臂 B 的逐份粒度写在记录本体里：该记录只属于**这一份**")
    check(record is not None and record.projected_terminal == "NOT_FOUND_AFTER_SEARCH"
          and record.qualified is True,
          "合格未命中 ⇒ 投影终态 NOT_FOUND_AFTER_SEARCH")
    check(b_by_doc[_OLDER].arm == "C2",
          "同一 aspect 在**另一份**上没有任何调用 ⇒ 不得蹭那份的臂 B")
    check(b_by_doc[_OLDER].search_record is not None
          and b_by_doc[_OLDER].search_record.unfulfilled_reason == "not_dispatched",
          "未派发必须带 typed 原因 not_dispatched")
    check(b_by_doc[_OLDER].search_record.attempts == (),
          "未派发不得为没发生过的调用造痕迹（无 call_id、无 attempt）")

    # 3c. 「注册了三份 ≠ 读了三份」：完全无痕迹时每份都要有明写的下场。
    no_trace = TR.derive_source_aspect_outcomes(
        sources=sources, responsibilities=resp, material_ids_by_aspect_source={},
        search_traces={}, not_found_audits_by_aspect={}, unfulfilled_reason_by_aspect={})
    for row in no_trace:
        doc = row.source_document_key.document_id
        expected = "C1" if not by_doc[doc].retrieval_required else "C2"
        check(row.arm == expected,
              f"无痕迹的 {doc} 必须落臂 {expected}（不能沉默，也不能改写成「无需检索」）")
        if row.arm == "C1":
            check(bool(row.not_required_basis)
                  and all(b[0] in TS.NOT_REQUIRED_BASIS_RULE_IDS
                          for b in row.not_required_basis),
                  "臂 C1 必须引用 §L1.5 的 typed 依据（说不出理由不算「无需检索」）")

    # 3d. 取不到证书**不是**罕见边角，而是本来源集的常规形态之一：aspect 在甲份取到材料、
    # 在乙份查完却没有材料时，aspect 级未命中审计**按构造不存在**（它只在整条 aspect 一份
    # 材料都没有时才派生）。此时三种做错的方式都要挡住：
    #   ① 就地补一张证书 → 看见结果后倒填资格；
    #   ② 改判臂 C2 → 把「查过」写成「没查成」（C2 的三个理由码都在说该查没查成）；
    #   ③ 静默略过 → §7 禁止项 11。
    # 正确形态：仍是臂 B，`qualified=false`、`audit_id=None`、逐条 typed 理由。
    unqualified = TR.derive_source_aspect_outcomes(
        sources=sources, responsibilities=resp, material_ids_by_aspect_source={},
        search_traces={aspect.aspect_id: [only_current_trace]},
        not_found_audits_by_aspect={}, unfulfilled_reason_by_aspect={})
    unc = {o.source_document_key.document_id: o for o in unqualified}[_CURRENT]
    check(unc.arm == "B",
          "无 aspect 级证书时仍落臂 B（查过就是查过，不得改判成「该查没查成」的 C2）")
    check(unc.search_record is not None
          and unc.search_record.qualified is False
          and unc.search_record.audit_id is None,
          "无证书时不得签发合格性，也不得凭空造一个 audit_id")
    check(unc.search_record is not None
          and unc.search_record.projected_terminal == "UNQUALIFIED_SEARCH_OBSERVATION",
          "无证书 ⇒ 投影终态必须是 UNQUALIFIED_SEARCH_OBSERVATION")
    check(unc.search_record is not None
          and unc.search_record.qualification_reasons == (TR.NO_ASPECT_CERTIFICATE_REASON,),
          "无证书必须逐条写明 typed 理由（不得只给一个布尔）")
    check(unc.search_record is not None
          and unc.search_record.synthesized_stop_reason == "NOT_FOUND_AFTER_SEARCH",
          "该份自己的调用无一失败/截断 ⇒ 折叠出的停止结局是 NOT_FOUND_AFTER_SEARCH"
          "（它是结局名，不是合格性判定）")

    # X 反例一：本份自己有失败调用时不得落合格未命中，即便 aspect 级证书是合格的（Y-14）。
    mixed_trace = dict(only_current_trace)
    mixed_trace.update({"call_id": only_current_trace["call_id"] + "--x",
                        "call_ordinal": only_current_trace["call_ordinal"] + 1,
                        "status": "FATAL_ERROR", "error_code": "TOOL_FATAL"})
    mixed = TR.derive_source_aspect_outcomes(
        sources=sources, responsibilities=resp, material_ids_by_aspect_source={},
        search_traces={aspect.aspect_id: [only_current_trace, mixed_trace]},
        not_found_audits_by_aspect={aspect.aspect_id: audit},
        unfulfilled_reason_by_aspect={})
    mixed_cur = {o.source_document_key.document_id: o for o in mixed}[_CURRENT]
    check(mixed_cur.arm == "B" and mixed_cur.search_record is not None
          and mixed_cur.search_record.qualified is False
          and TR.DOC_INCOMPLETE_CALLS_REASON
          in mixed_cur.search_record.qualification_reasons,
          "X 反例：本份自己还有失败调用时不得借 aspect 级证书判合格未命中")

    # X 反例二：合格未命中却没有任何证书 ⇒ 类型层直接拒绝（不得只凭身份回声判未命中）。
    check_raises(
        lambda: TS.SourceSearchOutcomeRecord(
            record_version=TS.SOURCE_SEARCH_OUTCOME_RECORD_VERSION,
            aspect_id=aspect.aspect_id, source_document_key=anchor_key, arm="B",
            audit_id=None, attempts=(), synthesized_stop_reason="NOT_FOUND_AFTER_SEARCH",
            searched_need_ids=(), valid_attempt_count=1, time_window="",
            qualified=True, qualification_reasons=(),
            projected_terminal="NOT_FOUND_AFTER_SEARCH", unfulfilled_reason=None,
            responsibility_fingerprint=resp[0].fingerprint(), impact=None),
        TS.SchemaValidationError,
        "X 反例：qualified=true 而无 audit_id 必须被类型层拒绝")

    # X 反例三：不合格却给不出理由 ⇒ 同样拒绝。
    check_raises(
        lambda: TS.SourceSearchOutcomeRecord(
            record_version=TS.SOURCE_SEARCH_OUTCOME_RECORD_VERSION,
            aspect_id=aspect.aspect_id, source_document_key=anchor_key, arm="B",
            audit_id=None, attempts=(), synthesized_stop_reason="NOT_FOUND_AFTER_SEARCH",
            searched_need_ids=(), valid_attempt_count=1, time_window="",
            qualified=False, qualification_reasons=(),
            projected_terminal="UNQUALIFIED_SEARCH_OBSERVATION", unfulfilled_reason=None,
            responsibility_fingerprint=resp[0].fingerprint(), impact=None),
        TS.SchemaValidationError,
        "X 反例：qualified=false 却不写理由必须被类型层拒绝")

    # ------------------------------------------------------------------
    # 3e. 定点返修 C2：**已派发但导航没有产出候选** ≠ **从未派发**
    #     （`ssor-1` → `ssor-2`：臂 C2 的原因闭集新增 `dispatched_no_candidate`）
    #     改前两件事同码 `not_dispatched`，r3 的 73 条 C2 因此签名完全同形，被读成
    #     「从未派发」进而归因到写作/整束失败——一个与研究相位无关的结论。
    # ------------------------------------------------------------------
    check(TS.SOURCE_SEARCH_OUTCOME_RECORD_VERSION == "ssor-2",
          "臂 C2 的原因闭集变了 ⇒ 记录版本必须升版（旧读者见到新码会当成未知值，"
          "新读者见到 `ssor-1` 则无法确定那一档当年怎么记的）")
    check("dispatched_no_candidate" in TS.UNFULFILLED_REASONS,
          "「已派发但导航没有产出候选」必须是闭集里的一等取值，不得塞进自由文本")
    check("ssor-1" in TS.LEGACY_SOURCE_SEARCH_OUTCOME_RECORD_VERSIONS,
          "`ssor-1` 必须被登记为**已识别**的历史版本（不得让它悄悄消失，也不得静默重解释）")

    # X 反例：旧版本的记录必须被 current reader 拒绝，且报错同时点名旧版本与现行版本——
    # 只报「版本不对」会让读回的人不知道那条记录属于哪一档取值域。
    legacy_msg = ""
    try:
        TS.SourceSearchOutcomeRecord(
            record_version="ssor-1", aspect_id=aspect.aspect_id,
            source_document_key=anchor_key, arm="C2", audit_id=None, attempts=(),
            synthesized_stop_reason=None, searched_need_ids=(), valid_attempt_count=0,
            time_window="", qualified=False, qualification_reasons=(),
            projected_terminal="UNFULFILLED_REQUIRED_SEARCH",
            unfulfilled_reason="not_dispatched",
            responsibility_fingerprint=resp[0].fingerprint(), impact=None)
    except TS.SchemaValidationError as exc:
        legacy_msg = str(exc)
    check("ssor-1" in legacy_msg and "ssor-2" in legacy_msg,
          f"X 反例：`ssor-1` 记录必须被拒且报错点名两版（实际 {legacy_msg[:80]!r}）")

    # 参照组：**不给**任何派发记录时，要求检索而无痕迹的成员一律是 `not_dispatched`
    # （=从未派发）。这一组同时用来找出「本来源集里到底哪几份是必须检索的」。
    plain = TR.derive_source_aspect_outcomes(
        sources=sources, responsibilities=resp, material_ids_by_aspect_source={},
        search_traces={}, not_found_audits_by_aspect={}, unfulfilled_reason_by_aspect={})
    plain_by_doc = {o.source_document_key.document_id: o for o in plain}
    required_docs = tuple(sorted(d for d, o in plain_by_doc.items() if o.arm == "C2"))
    check(bool(required_docs),
          "对照面：无派发记录时确有成员落臂 C2（否则下面几条判据恒真，等于没测）")
    check(all(plain_by_doc[d].search_record.unfulfilled_reason == "not_dispatched"
              for d in required_docs),
          "对照面：没有派发记录时 `not_dispatched` 仍是缺省档（原义不得被新码吞掉）")

    # 正向：**已派发**的成员带自己的结论进四臂派生，与未派发的成员在同一次调用里分档。
    # 「两档无调用结局」必须至少各有成员可挂，否则「同形」这个缺陷在合成面上无处可证。
    other_required = tuple(d for d in required_docs if d != _OLDER)
    check(bool(other_required),
          f"对照面：本来源集里必须至少有**两份**必须检索的来源（当前 {required_docs!r}），"
          f"否则「同一批里分档」无法证明")
    budget_doc = other_required[0]
    # T4：派发记录的键是**四轴身份**，不是 `document_id`（上面几条只用 id 作**显示键**，
    # 这里要进派生，必须换成源集里那一把完整键）。
    budget_key = next(k for k in sources.keys() if k.document_id == budget_doc)
    c2 = TR.derive_source_aspect_outcomes(
        sources=sources, responsibilities=resp, material_ids_by_aspect_source={},
        search_traces={}, not_found_audits_by_aspect={}, unfulfilled_reason_by_aspect={},
        dispatched_without_call={(aspect.aspect_id, older_key): "dispatched_no_candidate",
                                 (aspect.aspect_id, budget_key): "budget_exhausted"})
    c2_by_doc = {o.source_document_key.document_id: o for o in c2}
    older_rec = c2_by_doc[_OLDER].search_record
    check(c2_by_doc[_OLDER].arm == "C2" and older_rec is not None
          and older_rec.unfulfilled_reason == "dispatched_no_candidate",
          "已派发但导航无候选 ⇒ 臂 C2 且原因是 `dispatched_no_candidate`")
    check(older_rec is not None and older_rec.attempts == ()
          and older_rec.searched_need_ids == () and older_rec.audit_id is None,
          "该档不得为没发生的调用造痕迹（无 attempt、无 call_id、无证书）")
    check(older_rec is not None
          and older_rec.projected_terminal == "UNFULFILLED_REQUIRED_SEARCH"
          and older_rec.impact is not None,
          "该档仍是**未履行**：投影终态 UNFULFILLED_REQUIRED_SEARCH 且带影响码"
          "（不得因为「派发过了」就当成查过了）")
    budget_rec = c2_by_doc[budget_doc].search_record
    check(budget_rec is not None
          and budget_rec.unfulfilled_reason == "budget_exhausted",
          f"派发处预算不足走已有的 `budget_exhausted`（{budget_doc}），不冒充「导航无候选」"
          f"（两档都是无调用，但成因不同，读回必须能分开）")
    remaining = tuple(d for d in other_required if d != budget_doc)
    check(all(c2_by_doc[d].search_record.unfulfilled_reason == "not_dispatched"
              for d in remaining),
          f"同一批里**不在**派发记录内的必需成员仍是 `not_dispatched`"
          f"（对照成员 {remaining!r}）")
    check(budget_rec is not None
          and budget_rec.unfulfilled_reason
          != c2_by_doc[_OLDER].search_record.unfulfilled_reason,
          "两档在**同一次**调用里必须给出两个不同的原因——「同形」正是这次要修掉的缺陷")

    # X 反例一：派发记录**只服务无调用痕迹那一档**。有材料的那一份不得被它改判。
    guarded = TR.derive_source_aspect_outcomes(
        sources=sources, responsibilities=resp,
        material_ids_by_aspect_source={(aspect.aspect_id, older_key): ("material-2024",)},
        search_traces={aspect.aspect_id: [older_trace]},
        not_found_audits_by_aspect={}, unfulfilled_reason_by_aspect={},
        dispatched_without_call={(aspect.aspect_id, older_key): "dispatched_no_candidate"})
    check({o.source_document_key.document_id: o.arm for o in guarded}[_OLDER] == "A",
          "X 反例：派发记录不得把**有材料**的成员从臂 A 改判")

    # X 反例二：有**完成调用**、无材料的那一份仍是臂 B——「查过」不得因派发记录消失。
    guarded_b = TR.derive_source_aspect_outcomes(
        sources=sources, responsibilities=resp, material_ids_by_aspect_source={},
        search_traces={aspect.aspect_id: [only_current_trace]},
        not_found_audits_by_aspect={aspect.aspect_id: audit},
        unfulfilled_reason_by_aspect={},
        dispatched_without_call={(aspect.aspect_id, anchor_key): "dispatched_no_candidate"})
    check({o.source_document_key.document_id: o for o in guarded_b}[_CURRENT].arm == "B",
          "X 反例：查完的成员不得因派发记录落回 C2（那是把「查过」写成「没查成」）")

    # X 反例三：派发记录的原因码**不是自由文本**，未登记的码必须在类型层被拒。
    check_raises(
        lambda: TR.derive_source_aspect_outcomes(
            sources=sources, responsibilities=resp, material_ids_by_aspect_source={},
            search_traces={}, not_found_audits_by_aspect={},
            unfulfilled_reason_by_aspect={},
            dispatched_without_call={(aspect.aspect_id, older_key): "导航没给候选"}),
        TS.SchemaValidationError,
        "X 反例：派发记录里的原因码必须在闭集内（自由文本会绕过全部读回判据）")

    # X 反例四（T4）：逐 `(aspect, 来源)` 的两张表都必须按**四轴**核对。
    # 正例：**四元组**形式的四轴键与 `SourceDocumentKey` 等价（两种写法都规范到同一把键），
    # 且只作用于它自己那一份——输入形状与上面 `guarded` 完全相同，只有键的写法不同。
    older_axes = SM.source_key_axes(older_key)
    tuple_keyed = TR.derive_source_aspect_outcomes(
        sources=sources, responsibilities=resp,
        material_ids_by_aspect_source={(aspect.aspect_id, older_axes): ("material-2024",)},
        search_traces={aspect.aspect_id: [older_trace]},
        not_found_audits_by_aspect={}, unfulfilled_reason_by_aspect={},
        dispatched_without_call={(aspect.aspect_id, older_axes): "dispatched_no_candidate"})
    check({o.source_document_key.document_id: o.arm for o in tuple_keyed}[_OLDER] == "A"
          and {o.source_document_key.document_id: o.material_ids for o in tuple_keyed}[_OLDER]
          == ("material-2024",),
          f"正例：四元组形式的四轴键与 SourceDocumentKey 等价（得到 "
          f"{ {o.source_document_key.document_id: o.arm for o in tuple_keyed} }）")
    # 裸 `document_id` 作键 ⇒ 拒（那是「按 id 就近匹配」的入口）。
    check_raises(
        lambda: TR.derive_source_aspect_outcomes(
            sources=sources, responsibilities=resp,
            material_ids_by_aspect_source={(aspect.aspect_id, _OLDER): ("m-1",)},
            search_traces={}, not_found_audits_by_aspect={}, unfulfilled_reason_by_aspect={}),
        TR.ResponsibilityDerivationError,
        "X 反例：`material_ids_by_aspect_source` 的裸 document_id 键必须被拒（四轴才是比较单位）")
    # 同 id、错 `document_version` ⇒ 「同 id 错身份」档（不是「漏登记」档）。
    wrong_ver = SM.SourceDocumentKey(_COMPANY, older_key.document_id, "sha256-other",
                                     older_key.evidence_set_version)
    try:
        TR.derive_source_aspect_outcomes(
            sources=sources, responsibilities=resp,
            material_ids_by_aspect_source={(aspect.aspect_id, wrong_ver): ("m-1",)},
            search_traces={}, not_found_audits_by_aspect={}, unfulfilled_reason_by_aspect={})
        check(False, "X 反例：同 id 错 document_version 的键必须 fail-closed")
    except TR.ResponsibilityDerivationError as exc:
        check("同 document_id" in str(exc),
              f"同 id 错版本要报「同 document_id、不同文档身份」（实际 {exc}）")
    # 同 id 同版本、错 `evidence_set_version` ⇒ 同样 fail-closed（第四轴也是身份）。
    wrong_set = SM.SourceDocumentKey(
        older_key.company_id, older_key.document_id, older_key.document_version, "set-other")
    try:
        TR.derive_source_aspect_outcomes(
            sources=sources, responsibilities=resp,
            material_ids_by_aspect_source={(aspect.aspect_id, wrong_set): ("m-1",)},
            search_traces={}, not_found_audits_by_aspect={}, unfulfilled_reason_by_aspect={})
        check(False, "X 反例：错 evidence_set_version 的键必须 fail-closed")
    except TR.ResponsibilityDerivationError as exc:
        check("同 document_id" in str(exc),
              f"错第四轴同样报「同 document_id、不同文档身份」（实际 {exc}）")
    # 完全在集合外 ⇒ 另一档（漏登记，不是身份写错）。
    try:
        TR.derive_source_aspect_outcomes(
            sources=sources, responsibilities=resp,
            material_ids_by_aspect_source={
                (aspect.aspect_id, SM.SourceDocumentKey(_COMPANY, "DOC_MISSING", "v", "e")):
                    ("m-1",)},
            search_traces={}, not_found_audits_by_aspect={}, unfulfilled_reason_by_aspect={})
        check(False, "X 反例：源集之外的键必须 fail-closed")
    except TR.ResponsibilityDerivationError as exc:
        check("不在本来源集内" in str(exc),
              f"集合外的键要报「不在本来源集内」，与「同 id 错身份」分档（实际 {exc}）")
    # `dispatched_without_call` 同一套纪律（两张表不得一张严一张松）。
    check_raises(
        lambda: TR.derive_source_aspect_outcomes(
            sources=sources, responsibilities=resp, material_ids_by_aspect_source={},
            search_traces={}, not_found_audits_by_aspect={}, unfulfilled_reason_by_aspect={},
            dispatched_without_call={(aspect.aspect_id, _OLDER): "dispatched_no_candidate"}),
        TR.ResponsibilityDerivationError,
        "X 反例：`dispatched_without_call` 的裸 document_id 键必须被拒（与材料表同一套纪律）")

    # 生产侧接线（这一条防的是「记了但不传」与「只记一档」：两者都会让新码永不出现）。
    _RT_SRC = (REPO / "harness" / "topic_runtime.py").read_text(encoding="utf-8")
    check(_RT_SRC.count("dispatched_without_call[(aspect.aspect_id,") == 2,
          "生产侧必须对**两档**无调用结局都留记录（导航无候选 / 派发处预算不足）")
    check("dispatched_without_call=dispatched_without_call" in _RT_SRC,
          "派发记录必须真正传进四臂派生（只在研究侧记下、不传给派生，等于没记）")

    # ------------------------------------------------------------------
    # 4. 后继来源轴归属
    # ------------------------------------------------------------------
    # base：**两条** aspect 都有真实证据（继承侧）。非重研 aspect 的臂 A 是预期要继承的东西
    # ——「继承侧有臂 A」本身不是缺陷，把它当成缺陷会让每一次真实后继都失败。
    other_resp = TR.derive_aspect_source_responsibility(
        aspect=other_aspect, sources=sources)
    base_outcomes = tuple(
        _outcome(r.aspect_id, r.source_document_key, "A",
                 fingerprint=r.fingerprint(),
                 material_ids=(f"base-{r.source_document_key.document_id}",))
        for r in (*resp, *other_resp))
    inherited_pack = _pack_axis_stub(source_set=sources)
    inherited_pack.source_aspect_responsibility = resp
    inherited_pack.source_aspect_outcomes = base_outcomes

    # 本轮**只**重研 `aspect` 一条；`other_aspect` 在 new 里如实落成「没派发」的 C2，
    # 这正是改前会把每一次真实后继都判成「两套来源打架」的那一组输入。
    new_outcomes = []
    for r in resp:
        rec = TS.SourceSearchOutcomeRecord(
            record_version=TS.SOURCE_SEARCH_OUTCOME_RECORD_VERSION,
            aspect_id=other_aspect.aspect_id, source_document_key=r.source_document_key,
            arm="C2", audit_id=None, attempts=(), synthesized_stop_reason=None,
            searched_need_ids=(), valid_attempt_count=0, time_window="",
            qualified=False, qualification_reasons=(),
            projected_terminal="UNFULFILLED_REQUIRED_SEARCH",
            unfulfilled_reason="not_dispatched",
            responsibility_fingerprint=r.fingerprint(),
            impact=TR._unfulfilled_impact(r))
        new_outcomes.append(_outcome(
            other_aspect.aspect_id, r.source_document_key, "C2",
            fingerprint=r.fingerprint(), search_record=rec))
    # 被重研的那一条：本轮真的产出了材料。
    new_resp = TR.derive_aspect_source_responsibility(aspect=aspect, sources=sources)
    for r in new_resp:
        new_outcomes.append(_outcome(
            r.aspect_id, r.source_document_key, "A", fingerprint=r.fingerprint(),
            material_ids=(f"new-{r.source_document_key.document_id}",)))
    new_pack = _pack_axis_stub(source_set=sources)
    new_pack.source_aspect_responsibility = resp
    new_pack.source_aspect_outcomes = tuple(new_outcomes)

    merged_sources, merged_resp, merged_out = TR.merge_source_axis(
        base=inherited_pack, new=new_pack, aspects=(aspect, other_aspect),
        researched_aspect_ids=(aspect.aspect_id,))
    check([k.document_id for k in merged_sources.keys()] == [_CURRENT, _OLDER, _BOND],
          "合并后的来源集保持清单序位（base 与 new 同集 ⇒ 逐位相同）")
    by_pair = {(o.aspect_id, o.source_document_key.document_id): o for o in merged_out}
    check(set(by_pair) == {(a.aspect_id, d)
                           for a in (aspect, other_aspect)
                           for d in (_CURRENT, _OLDER, _BOND)},
          "合并后的逐来源结果必须覆盖 全部 aspect × 全部成员（不留白）")
    check(all(o.material_ids and o.material_ids[0].startswith("new-")
              for (aid, _d), o in by_pair.items() if aid == aspect.aspect_id),
          "被重研 aspect 的结果取自本轮（new）")
    check(all(o.arm == "A" and o.material_ids and o.material_ids[0].startswith("base-")
              for (aid, _d), o in by_pair.items() if aid == other_aspect.aspect_id),
          "非重研 aspect 的结果取自继承（base）——本轮那些 not_dispatched **不得**顶掉它")
    check(all(o.responsibility_fingerprint
              == next(r.fingerprint() for r in merged_resp
                      if r.aspect_id == aid and r.source_document_key == o.source_document_key)
              for (aid, _d), o in by_pair.items()),
          "每条结果的责任指纹必须等于合并后**重派生**的同一条责任指纹")

    # X 反例一：非重研 aspect 在 new 里落臂 A（本轮根本没导航它）→ fail-closed。
    fabricated = _pack_axis_stub(source_set=sources)
    fabricated.source_aspect_responsibility = resp
    fabricated.source_aspect_outcomes = (
        _outcome(other_aspect.aspect_id, anchor_key, "A",
                 fingerprint="1" * 64, material_ids=("ghost",)),)
    check_raises(
        lambda: TR.merge_source_axis(
            base=inherited_pack, new=fabricated, aspects=(aspect, other_aspect),
            researched_aspect_ids=(aspect.aspect_id,)),
        TR.TopicRuntimeError,
        "X 反例：非重研 aspect 在本轮结果里落臂 A 必须 fail-closed（自造材料归属）")

    # X 反例二：不指出任何被重研 aspect ⇒ 归属无从判定，fail-closed。
    check_raises(
        lambda: TR.merge_source_axis(
            base=inherited_pack, new=new_pack, aspects=(aspect, other_aspect),
            researched_aspect_ids=()),
        TR.TopicRuntimeError,
        "X 反例：不指出任何被重研 aspect 时来源轴合并必须 fail-closed")

    # X 反例三：**继承侧**结果的责任指纹与重派生责任对不上 ⇒ 责任轴在结果成文之后被动过。
    # 这条只对继承行有意义：重研行由本轮重建，本来就是被替换掉的东西（丢弃不是掩盖）。
    stale = _pack_axis_stub(source_set=sources)
    stale.source_aspect_responsibility = resp
    stale.source_aspect_outcomes = (
        _outcome(other_aspect.aspect_id, anchor_key, "A",
                 fingerprint="0" * 64, material_ids=("stale",)),)
    check_raises(
        lambda: TR.merge_source_axis(
            base=stale, new=new_pack, aspects=(aspect, other_aspect),
            researched_aspect_ids=(aspect.aspect_id,)),
        TR.TopicRuntimeError,
        "X 反例：继承结果的责任指纹与重派生责任不一致必须 fail-closed")

    # ------------------------------------------------------------------
    # 5. 读回键名（写错 = 整列渲染成空，且不会报错）
    # ------------------------------------------------------------------
    as_dict = sources.to_dict()
    member_keys = [set(m) for m in as_dict["members"]]
    check(all(m == {"source_document_key", "source_role"} for m in member_keys),
          f"源集成员序列化键集必须是 {{source_document_key, source_role}}，得到 {member_keys}")
    check(all(m["source_document_key"].get("document_id") for m in as_dict["members"]),
          "每个成员都要能读出非空 document_id（否则逐份台账会整列渲染成空）")
    check([m["source_document_key"]["document_id"] for m in as_dict["members"]]
          == [_CURRENT, _OLDER, _BOND],
          "源集序列化必须保持清单序位（序位是语义，不得重排）")
    check([m["source_role"] for m in as_dict["members"]] == list(_ROLES),
          "每个成员的角色必须与清单一致（角色是责任派生的输入轴）")
    check(TR.DocumentSourceSet.from_dict(as_dict).to_dict() == as_dict,
          "源集 to_dict ↔ from_dict 往返相等（读回台账与运行身份一致）")

    # ------------------------------------------------------------------
    # 6. 组合 payload 解析：0 命中 = dangling、≥2 命中 = 歧义即拒
    # ------------------------------------------------------------------
    class _One:
        def __init__(self, value): self._value = value

        def resolve(self, payload_ref): return self._value

    ref = TS.MaterialPayloadRef(
        object_type="evidence_span", authority_identity="eb-1", version="v1",
        content_hash="a" * 64, created_dependency_fingerprint="b" * 64,
        locator=TS.EvidenceLocator(
            document_id=_CURRENT, document_version="v",
            section_path="1/2", page=7))
    check(TR.CombinedPayloadResolver((_One(None), _One(None))).resolve(ref) is None,
          "0 命中必须返回 None（交上层 fail-closed，不伪造 payload）")
    check(TR.CombinedPayloadResolver((_One("only"), _One(None))).resolve(ref) == "only",
          "单一命中返回该结果（跨源之后三份的 payload 都要能解析）")
    check_raises(lambda: TR.CombinedPayloadResolver(()),
                 (TR.TopicRuntimeError, TypeError),
                 "X 反例：空 resolver 列表必须被拒（组合器不得退化成空实现）")
    check_raises(
        lambda: TR.CombinedPayloadResolver((_One("a"), _One("b"))).resolve(ref),
        TR.TopicRuntimeError,
        "X 反例：≥2 个来源同时认领同一 payload 必须即拒（身份边界不可信）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

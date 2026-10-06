"""Eval: §0.20 写作链里**没有 Pack 材料边界**的那一支（财务 artifact + 已验附注事实）。

用法: python -m evals.test_m930_3_cited_financial_branch

背景：新写作链（`cwm-2` / `cwp-1` / `sc-1`）原先把「材料正文上下文」当成**唯一**入口，
于是 `build_cited_writer_input` 直接读 `authority.pack_set`——财务权威没有这个字段，链路在
取属性那一刻就 `AttributeError`（不是 typed 拒收）。本模块钉住修复后的那一支的**边界**：

1. **材料边界指纹分两支，共用同一个函数**：topic Pack 权威 → 当前 `VerifiedPackSet` 的指纹
   （逐字节与 `pack_set_fingerprint` 相同，因此历史身份**不动**）；无 Pack 材料边界的权威 →
   空材料边界的领域分离指纹。缺 `producer_kind`/`input_id` 时**不得**用类型名凑一个。
2. **空材料上下文是合法状态，不是「无材料也照样写」的许可**：它只能由
   `empty_material_context_for_authority` 产出，且**只**接受非 Pack 权威；反过来，非 Pack
   权威携带任何 Pack 材料正文一律 fail-closed。
3. **`sc-1` 轴 2（定位可回查）按各自权威的坐标判**：财务事实的坐标是
   `容器 + fact_id + 引用锚点身份`（它的 `locator_ref` / `material_id` / `payload_ref` 本来就
   都是 `None`，不能被当成「没有定位」）；**默认那一支没有被放宽**——topic 事实缺全部锚点
   仍然硬错误。
4. **离线事实替身不越权**：它只逐字复述权威表面文本、逐句挂事实引用；没有事实时如实产出
   `no_source_in_manifest` 缺口，而不是编一句出来。
5. **本节 topic 逐字取自本节任务，入口不许缩小它**：任务带两个 topic 时两个都返回，
   `--topic` 少给一项就是静默缩小已选范围（`fin_source_scope` 曾经就是这样消失的），
   多给一项就是写没选过的内容；两种都 fail-closed，不猜、不挑、不合并。**分拨**也要逐条
   有去处：`fin_source_scope` 归确定性呈现、`fin_solvency` 归写作支，两个都不是「被丢掉」。

夹具复用 `test_demo_pack_writer` 的**真实** `FinancialPackArtifact` / `FinancialAuthorityInput`
与同一条 `resolve_writer_material_context` 入口；模块无公司代号、页码、表号或固定答案关键词。
不调 LLM（替身只做确定性复述）、不联网、不写库、不建第二套 Harness/Pack/Writer。
"""

from __future__ import annotations

import dataclasses as dc
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import test_demo_pack_writer as T          # noqa: E402
from scripts import run_m930_3_cited_chain as CHAIN   # noqa: E402
from sections import cited_writer as CW               # noqa: E402
from sections import material_context as MC           # noqa: E402
from sections import pack_writer as PW                # noqa: E402
from sections import sentence_check as SC             # noqa: E402

_FIN_SECTION = "financial"
_FIN_TASK = "task-fin-solvency"
_SUBSECTION = "sub-solvency"
_REQ_TEXT = "列示报告期内主要偿债能力指标"

#: 一条财务事实的**权威表面**：期间、单位与数值都来自该事实自己的字段，不由本模块拼装。
_FACT_ID = "ff-current-ratio"
_FACT_LABEL = "流动比率"
_FACT_DISPLAY = "1.20 倍"
_FACT_PERIOD = T.REPORT_AS_OF


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def _fin_task(topic_ids=None):
    return T._task(_FIN_SECTION, topic_ids or (T.TOPIC_FIN_SOLVENCY,), task_id=_FIN_TASK,
                   title="偿债能力")


def _fin_artifact(*, task, with_fact: bool = True):
    facts = ()
    if with_fact:
        facts = (T._FinFact(fact_id=_FACT_ID, label=_FACT_LABEL, display=_FACT_DISPLAY,
                            period=_FACT_PERIOD, unit="倍",
                            citation={"ref_type": "structured",
                                      "snapshot_id": T.SNAPSHOT_ID}),)
    return T._Artifact(task_id=task.task_id, facts=facts)


def _fin_authority(*, with_fact: bool = True, topic_ids=None):
    task = _fin_task(topic_ids)
    artifact = _fin_artifact(task=task, with_fact=with_fact)
    authority = T._financial_authority(
        task, artifact, note_gap=T._NoteGap(task_id=task.task_id))
    return task, authority


def _topic_authority():
    """一个**有真实 Pack 材料边界**的权威（对照组：证明本批没有改动 Pack 那一支）。"""
    task = T._task("company", (T.TOPIC_BUSINESS,), task_id="task-co-1")
    authority = T._company_authority(
        task, facts=(), materials=(T._research_material("m-1", f"evidence:{T.EV_ID}", page=12),))
    return task, authority


def _subsections():
    # `cwm-3`：小节必须**声明**它要写的 Contract 栏目（`fin_solvency` 的栏目名取自冻结
    # 合同 `contracts/review/review_52q.json`）。留空会让逐句栏目核对整体跳过，
    # 而「跳过了」与「都对上了」在产物上不可区分，因此构造期就拒绝空值。
    return (CW.CitedSubsectionSpec(subsection_id=_SUBSECTION, title="偿债能力",
                                   requirement_text=_REQ_TEXT,
                                   declared_aspect_ids=("fin_solvency.short_term_solvency",)),)


def _fin_manifest(*, authority, facts=None):
    """财务权威 + 空材料边界上下文 → 输入清单（正文那一支的唯一入口）。"""
    context = MC.empty_material_context_for_authority(authority)
    if facts is None:
        facts = PW.scan_financial(authority, _fin_task()).facts
    return CW.build_cited_writer_input(
        authority=authority, material_context=context, subsections=_subsections(),
        facts=facts, section_title="偿债能力")


def _with_facts(manifest, facts):
    """换掉事实行、**经 `create` 重算身份**（`replace` 会带旧 `manifest_id` 触发身份校验）。"""
    fields = {f.name for f in dc.fields(manifest)}
    return CW.CitedWriterInputManifest.create(**{
        **{name: getattr(manifest, name) for name in fields if name != "manifest_id"},
        "facts": tuple(facts)})


def _check_record(sentence_text, *, manifest, key):
    """跑一次逐句核对，取回 `citation_locator` 那一条记录。"""
    sentence = CW.CitedSentence(sentence_id="s0001", text=sentence_text, citations=(key,))
    records = SC.check_sentence(sentence=sentence, subsection_id=_SUBSECTION,
                                paragraph_id="p1", manifest=manifest)
    hits = [r for r in records if r.check_kind == "citation_locator"]
    assert len(hits) == 1, f"`citation_locator` 应恰有一条记录，实得 {len(hits)}"
    return hits[0]


def _expect(fn, exc_type, *, token: str, reason: str = "") -> str:
    try:
        fn()
    except exc_type as exc:
        message = str(exc)
        if token and token not in message:
            raise AssertionError(
                f"异常消息里没有 {token!r}（无法据此定位）：{message}") from None
        if reason and getattr(exc, "reason", "") != reason:
            raise AssertionError(
                f"typed 原因码不是 {reason!r}，而是 {getattr(exc, 'reason', '')!r}：{message}"
            ) from None
        return message
    except Exception as exc:  # noqa: BLE001 —— 类型不对本身就是失败，不得被吞成「通过了」
        raise AssertionError(
            f"期望 {exc_type.__name__}，实际抛出 {type(exc).__name__}: {exc}") from None
    raise AssertionError(f"这一路必须 fail-closed，但它通过了（期望含 {token!r}）")


def _client_payload(manifest, *, facts_per_subsection: int = 3):
    client = CHAIN.OfflineFactAssertionCitedProseClient(
        facts_per_subsection=facts_per_subsection)
    payload = CW.build_cited_prose_request(manifest=manifest)
    result = client.compose(messages=[{"content": json.dumps(payload, ensure_ascii=False)}],
                            system="", prompt_version=CW.CITED_WRITER_PROMPT_VERSION,
                            model_policy="offline_stub")
    return client, result, json.loads(result.text)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    # ================================================= §1 材料边界指纹分两支
    details.append("## §1 材料边界指纹：Pack 支逐字节不动，无 Pack 支用领域分离指纹")
    co_task, co_authority = _topic_authority()
    check(MC.authority_material_boundary_fingerprint(co_authority)
          == MC.pack_set_fingerprint(co_authority.pack_set),
          "topic Pack 权威的边界指纹**逐字节等于** `pack_set_fingerprint`（历史身份不动）")
    check(co_authority.pack_set is not None,
          "对照组权威确实带 Pack 材料边界（下面用它比对两支**不共用取值域**）")

    fin_task, fin_authority = _fin_authority()
    fin_boundary = MC.authority_material_boundary_fingerprint(fin_authority)
    check(bool(fin_boundary) and fin_boundary != MC.pack_set_fingerprint(co_authority.pack_set),
          "财务权威的边界指纹非空，且与任何 Pack 指纹**不共用取值域**")
    check(fin_boundary == MC.authority_material_boundary_fingerprint(fin_authority),
          "同一个权威重算两次得到同一指纹（确定性）")
    check(str(getattr(fin_authority, "producer_kind", "")) == "financial_workflow",
          f"该权威的 producer kind 实测为 {getattr(fin_authority, 'producer_kind', '')!r}")

    # 反例 1a：非 Pack 权威**不得**走空材料上下文之外的路（空材料上下文只对它开放）
    _expect(lambda: MC.empty_material_context_for_authority(co_authority),
            MC.MaterialContextError, token="topic Pack 权威不得走空材料上下文",
            reason="pack_set_not_verified")
    details.append("NOTE 反例 1a：topic Pack 权威调 `empty_material_context_for_authority` ⇒ "
                   "`pack_set_not_verified`（空集只能是非 Pack 权威的状态）。")

    # 反例 1b：缺 `producer_kind`/`input_id` 时**不得**用类型名凑一个指纹出来
    class _BoundarylessAuthority:
        """一个既没有 `pack_set`、也没有 `producer_kind`/`input_id` 的输入。"""

    _expect(lambda: MC.authority_material_boundary_fingerprint(_BoundarylessAuthority()),
            MC.MaterialContextError, token="材料边界指纹", reason="material_boundary_unverifiable")
    details.append("NOTE 反例 1b：非 Pack 权威缺 `producer_kind`/`input_id` ⇒ "
                   "`material_boundary_unverifiable`（不得退化成「用类型名凑一个」）。")

    # ================================================= §2 财务分支的输入面
    details.append("## §2 无 Pack 材料边界那一支：空材料是合法状态，夹带材料一律拒")
    fin_manifest = _fin_manifest(authority=fin_authority)
    check(fin_manifest.materials == (),
          f"财务分支的清单材料行为**空集**（实测 {len(fin_manifest.materials)} 行）")
    check(fin_manifest.material_keys() == (),
          "空集不是「键有空洞」：材料轴一个键都不产出")
    check(len(fin_manifest.facts) == 1,
          f"财务分支的事实行来自本权威自己的读视图（实测 {len(fin_manifest.facts)} 行）")
    check(fin_manifest.pack_set_fingerprint == fin_boundary,
          "清单里的边界指纹**重算得出**同一个值（不是约定、不是空串）")
    check(fin_manifest.pack_set_fingerprint
          == MC.empty_material_context_for_authority(fin_authority).pack_set_fingerprint,
          "上下文与清单共用**同一个**边界指纹（「这份上下文属于哪一份权威」可复核）")
    check(all(k == CW.cited_fact_key(i) for i, k in enumerate(fin_manifest.fact_keys())),
          f"事实键按清单序连续编号：{fin_manifest.fact_keys()}")
    check(fin_manifest.all_keys() == fin_manifest.fact_keys(),
          "材料轴为空时，本次输入的全部引用键都落在事实轴")

    # 反例 2a：财务权威夹带 Pack 材料正文 ⇒ fail-closed（空集是合法状态，不是「什么都能塞」）
    _expect(lambda: CW.build_cited_writer_input(
        authority=fin_authority,
        material_context=T._writer_material_context(co_authority.pack_set, task_id=_FIN_TASK,
                                                    section_id=_FIN_SECTION),
        subsections=_subsections()),
        PW.PackWriterError, token="不得携带 Pack 材料正文上下文")
    details.append("NOTE 反例 2a：非 Pack 权威携带 Pack 材料正文上下文 ⇒ `PackWriterError`"
                   "（不得把别的 PackSet 的正文当成本权威的材料）。")

    # 反例 2b：财务权威不得走「只给 material ID」的降级路
    _expect(lambda: CW.build_cited_writer_input(
        authority=fin_authority, material_context=None, subsections=_subsections()),
        CW.CitedWriterError, token="材料正文上下文", reason="material_context_missing")
    details.append("NOTE 反例 2b：缺上下文 ⇒ `material_context_missing`（两个分支都不得降级）。")

    # ================================================= §3 `sc-1` 轴 2 分权威坐标判
    details.append("## §3 轴 2（定位可回查）按各权威自己的坐标判，默认那一支未被放宽")
    fact = fin_manifest.facts[0]
    check(fact.authority_kind == "financial_pack",
          f"事实行落在财务轴：{fact.authority_kind!r}")
    check(fact.locator_ref is None and fact.material_id is None and fact.payload_ref is None,
          "财务事实的 `locator_ref` / `material_id` / `payload_ref` **本来就都是 None**"
          "（这不是「没有定位」，因此轴 2 不得按这三个字段判它）")
    check(bool(fact.source_identity.startswith("financial_snapshot:")),
          f"它的引用锚点身份落在结构化快照域：{fact.source_identity!r}")

    good = _check_record(fact.text, manifest=fin_manifest, key=fact.citation_key)
    check(good.verdict == "pass",
          f"正例：财务事实三坐标齐全 ⇒ 轴 2 过（实测 {good.verdict}"
          f"{'：' + good.detail if good.detail else ''}）")

    # 反例 3a：引用锚点身份缺失 ⇒ 该事实就**没有**可回查的定位
    naked = dc.replace(fact, source_identity="")
    naked_manifest = _with_facts(fin_manifest, (naked,))
    bad = _check_record(naked.text, manifest=naked_manifest, key=naked.citation_key)
    check(bad.verdict == "hard_error" and bad.failure_reason == "citation_locator_unretrievable",
          f"反例 3a：财务事实缺引用锚点身份 ⇒ `{bad.failure_reason}`"
          f"（实测 {bad.verdict}）")
    details.append("NOTE 反例 3a：轴 2 对财务那一支**不是恒真**——三坐标缺一个就硬错误。")

    # 反例 3b（回归）：默认那一支**没有**被放宽——topic 事实缺全部锚点仍然硬错误
    topic_fact = dc.replace(fact, authority_kind="topic_pack", container_identity="p-1",
                            source_identity="evidence:ev-x", locator_ref=None,
                            material_id=None, payload_ref=None)
    topic_manifest = _with_facts(fin_manifest, (topic_fact,))
    still_bad = _check_record(topic_fact.text, manifest=topic_manifest,
                              key=topic_fact.citation_key)
    check(still_bad.verdict == "hard_error"
          and still_bad.failure_reason == "citation_locator_unretrievable",
          f"反例 3b（回归）：topic 事实缺 locator/material/payload ⇒ 仍然 "
          f"`{still_bad.failure_reason}`（默认判据未被本批放宽）")

    # ================================================= §4 离线事实替身
    details.append("## §4 离线事实替身：只逐字复述权威表面并逐句挂引用，没事实就如实报缺")
    client, result, body = _client_payload(fin_manifest)
    check(len(client.calls) == 1 and client.calls[0]["call_id"] == CHAIN.OFFLINE_FACT_WRITER_IDENTITY,
          "替身以**自己的**身份记账（与抽取式替身不共用 call_id）")
    check(client.calls[0]["call_id"] != CHAIN.OFFLINE_WRITER_IDENTITY,
          "两支替身的身份**不同**：事实直述 ≠ 材料摘录")
    subs = body["subsections"]
    check(len(subs) == 1 and subs[0]["subsection_id"] == _SUBSECTION,
          "返回面小节与请求面小节一一对应")
    sentences = [s for p in subs[0]["paragraphs"] for s in p["sentences"]]
    # 本清单**只有 1 条**权威事实（§2 已实测），因此这一栏最多只能写 1 句。
    # 旧替身在这里按 `facts_per_subsection=3` 把同一条事实复述 3 遍，读数看起来「有内容」，
    # 但那正是本批要修掉的重复句；替身一份事实在整轮里只写一次。
    check(len(sentences) == len(fin_manifest.facts) == 1,
          f"该小节按事实条数拿到句、不重复（实测 {len(sentences)} 句 / "
          f"事实 {len(fin_manifest.facts)} 条）")
    check(len({s["text"] for s in sentences}) == len(sentences),
          "同一句正文不重复出现（重复句不算有效正文）")
    check(all(s["citations"] == [fact.citation_key] for s in sentences),
          f"每句都挂本清单的事实键 {fact.citation_key!r}（不挂材料键、不挂不存在的键）")
    check(all(s["text"] == fact.text for s in sentences),
          "每句都是权威表面文本的**逐字复制**（替身不改写、不润色、不推理）")
    parsed = CW.parse_cited_prose(result.text, manifest=fin_manifest)
    check(len(parsed.sentences()) == len(sentences),
          f"这份返回能被新链的解析器读回（实测 {len(parsed.sentences())} 句）")

    # 反例 4：本权威一条事实都没有 ⇒ 如实报缺，不得编一句出来
    empty_manifest = _fin_manifest(
        authority=T._financial_authority(fin_task, _fin_artifact(task=fin_task, with_fact=False),
                                         note_gap=T._NoteGap(task_id=fin_task.task_id)))
    check(empty_manifest.facts == (), "无事实的财务权威 ⇒ 清单事实行为空集")
    _client2, _result2, body2 = _client_payload(empty_manifest)
    check(all(not p["paragraphs"] for p in body2["subsections"]),
          "反例 4：没有事实时**不产出任何段落**（不得凭空生成正文）")
    check([g["reason"] for g in body2["gaps"]] == ["no_source_in_manifest"],
          f"反例 4：如实产出 typed 缺口（实测 {[g['reason'] for g in body2['gaps']]}）")

    # ================================================= §5 本节 topic 只能来自本节任务
    details.append("## §5 本节 topic 逐字取自本节任务；入口不许缩小它（`fin_source_scope` 不得被跳过）")
    check(CHAIN._resolve_topic_ids(inputs=_FakeInputs(fin_authority, _fin_task()),
                                   section_id=_FIN_SECTION) == (T.TOPIC_FIN_SOLVENCY,),
          "正例：单 topic 任务 ⇒ 返回那一个（按本节任务的次序）")
    two_task = _fin_task((T.TOPIC_FIN_SCOPE, T.TOPIC_FIN_SOLVENCY))
    resolved = CHAIN._resolve_topic_ids(inputs=_FakeInputs(fin_authority, two_task),
                                        section_id=_FIN_SECTION)
    check(resolved == (T.TOPIC_FIN_SCOPE, T.TOPIC_FIN_SOLVENCY),
          f"正例（本批要修的那一条）：任务带**两个** topic 时两个都返回，"
          f"`{T.TOPIC_FIN_SCOPE}` **不**因为入口曾经自带 `fin_solvency` 缺省而被静默跳过"
          f"（实测 {list(resolved)}）")
    check(CHAIN._resolve_topic_ids(
              inputs=_FakeInputs(fin_authority, two_task), section_id=_FIN_SECTION,
              topic_ids=(T.TOPIC_FIN_SOLVENCY, T.TOPIC_FIN_SCOPE))
          == (T.TOPIC_FIN_SCOPE, T.TOPIC_FIN_SOLVENCY),
          "显式给出的集合**逐项相等**（次序不同也算相等）⇒ 仍按本节任务的次序返回")
    _expect(lambda: CHAIN._resolve_topic_ids(
                inputs=_FakeInputs(fin_authority, two_task), section_id=_FIN_SECTION,
                topic_ids=(T.TOPIC_FIN_SOLVENCY,)),
            SystemExit, token="静默跳过已选 topic")
    details.append(f"NOTE 反例 5a：显式 `--topic` 只给 `{T.TOPIC_FIN_SOLVENCY}` 而任务选中"
                   f"两个 ⇒ `SystemExit`（少一项就是**静默缩小已选范围**——本批的缺陷正是"
                   f"`{T.TOPIC_FIN_SCOPE}` 在没有读回行的情况下消失）。")
    _expect(lambda: CHAIN._resolve_topic_ids(
                inputs=_FakeInputs(fin_authority, _fin_task()), section_id=_FIN_SECTION,
                topic_ids=(T.TOPIC_FIN_SOLVENCY, "not-selected")),
            SystemExit, token="静默跳过已选 topic")
    details.append("NOTE 反例 5b：显式多给一个任务没选的 topic ⇒ `SystemExit`"
                   "（写一节没选过的内容同样是改范围，要改得改 profile）。")
    _expect(lambda: CHAIN._resolve_topic_ids(
                inputs=_FakeInputs(
                    fin_authority,
                    T._task(_FIN_SECTION, (), task_id=_FIN_TASK, title="偿债能力")),
                section_id=_FIN_SECTION),
            SystemExit, token="没有声明任何 topic")
    details.append("NOTE 反例 5c：本节任务一个 topic 都没声明 ⇒ `SystemExit`"
                   "（空范围不落成一份「跑过了但没有内容」的产物）。")

    # ---- 写作支与确定性呈现支的分拨 ---------------------------------------
    check(CHAIN._split_cited_and_deterministic(
              section_id=_FIN_SECTION, topic_ids=(T.TOPIC_FIN_SCOPE, T.TOPIC_FIN_SOLVENCY))
          == ((T.TOPIC_FIN_SOLVENCY,), (T.TOPIC_FIN_SCOPE,)),
          f"`{T.TOPIC_FIN_SCOPE}` 归**确定性呈现**、`{T.TOPIC_FIN_SOLVENCY}` 归**写作支**："
          "两个 topic 各自有去处，没有哪一个被丢掉")
    check(set(CHAIN.DETERMINISTIC_TOPIC_PRESENTERS)
          == {T.TOPIC_FIN_SCOPE, CHAIN.BALANCE_STRUCTURE_TOPIC},
          f"确定性呈现的 topic 是**登记**出来的（实测 "
          f"{sorted(CHAIN.DETERMINISTIC_TOPIC_PRESENTERS)}），不是「清单为空就退化」；"
          f"本批新登记的 `{CHAIN.BALANCE_STRUCTURE_TOPIC}` 走确定性呈现支，"
          "因此**不**占用每节那一个写作 topic 名额")
    _expect(lambda: CHAIN._split_cited_and_deterministic(
                section_id=_FIN_SECTION,
                topic_ids=(T.TOPIC_FIN_SOLVENCY, T.TOPIC_BUSINESS)),
            SystemExit, token="至多写")
    details.append("NOTE 反例 5d：一节选中两个**需要写正文**的 topic ⇒ `SystemExit`"
                   "（不挑一个写、也不把两栏拼成一份清单；拼了就没有各自的身份）。")
    check(CHAIN._split_cited_and_deterministic(
              section_id=_FIN_SECTION, topic_ids=(T.TOPIC_FIN_SOLVENCY,))
          == ((T.TOPIC_FIN_SOLVENCY,), ()),
          "只选一个写作 topic 时，确定性呈现支为空集（合法，不是缺口）")

    # ---- 入口的 `--topic` 解析：值是元组，不是单值 -------------------------
    check(CHAIN._parse_topic_map("financial=a+b,company=c", section_ids=("company", "financial"))
          == {"financial": ("a", "b"), "company": ("c",)},
          "`--topic` 解析出**每节一个元组**（一节多 topic 用 `+`；`,` 分隔的是节）")
    check(CHAIN._parse_topic_map(None, section_ids=("company",)) == {},
          "不给 `--topic` ⇒ 空映射，逐节 topic 由本节任务决定")
    _expect(lambda: CHAIN._parse_topic_map("financial=a+a", section_ids=("financial",)),
            SystemExit, token="出现了两次")
    details.append("NOTE 反例 5e：`--topic` 里同一个 topic 写两遍 ⇒ `SystemExit`"
                   "（重复值会让「选中集合」与「读取次序」两件事都说不清）。")

    details.append(
        "NOTE 本模块只证明**分支接口与判据**；财务节是否达到人读内容门（真实权威数字、期间、"
        "单位、代理口径与可读分析）由离线分节读回与人工验收判定，本模块不产生任何正式产物，"
        "也不宣称 M930-3 关闭。")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


class _FakeInputs:
    """`_resolve_topic_ids` 只读 `tasks[section_id].topic_ids`；分拨只读 topic 名是否登记。

    因此这里的「权威」只作占位：本模块§5 证明的是**入口的范围纪律**，不是权威装配。
    """

    def __init__(self, authority, task) -> None:
        self.authorities = {str(task.section_id): authority}
        self.tasks = {str(task.section_id): task}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

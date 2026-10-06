"""Eval: §0.20 第三步 —— **有界局部返修**（`cwr-2` / `cwrp-3` / prompt `@crw-2`），不调模型。

用法: python -m evals.test_m930_3_cited_rework

本模块钉住**返修接口**的边界，不钉它的措辞、不评价它的产出质量。逐条证明：

1. **只收点名句**：没被点名的句子正文与引用必须逐字原样（`(文本, 引用)` 多重集包含）。
   改了任何一句 —— 哪怕只把 `。` 换成 `.` —— 整次返修作废、**新稿不生效**，原稿与它自己的
   硬错读数一个字不改地留着。这正是「一处失败不清零整节」在返修面上的那一半。
2. **失败路径保留原稿与账本**：`outcome="failed"` 时 `draft is None`，而 `base_draft_id` /
   `base_check_report_id` / `failure_reason` / `call` 四样都在，**逐条可回查**。
3. **不许后台静默换引用**：新稿里出现任何基准稿没用过的引用键，都必须在 `citation_changes`
   里逐条声明。未声明 ⇒ `undeclared_citation_change`；声明了但那一句在稿里的真实引用与它
   不符 ⇒ `citation_change_target_mismatch`；声明指向不存在的句子 ⇒ `citation_change_sentence_unknown`。
   三条各自给反例。第四条路 —— 引用键根本不在本次清单里 —— 由写作侧**同一套** `validate_citations`
   先关掉（⇒ `rework_draft_invalid`），`citation_change_unknown_key` 因此到不了；这一点也钉住，
   免得日后有人以为返修面上还开着一道更松的引用校验。
4. **声明是唯一入口，且必须与实情一致**：声明合法时返修**通过**，且 `citation_changes` 里的
   句子 id 是**归一化之后的**最终 id（模型临时编号经 `crn-1` 台账解析）——不是原样收下的。
5. **最多一次**：`rounds != 1` 当场抛 `rework_rounds_out_of_range`。没有循环、没有重试入口。
6. **不必返修时一个调用都不发**：没有点名句 ⇒ `outcome="not_needed"` 且 `client.calls == []`。
   为一份没有问题的草稿花掉一次调用，是拿预算换一个恒真的动作。
7. **门是事前等式**：草稿与清单、报告与草稿两条对不上时**在装配请求面时**就拒
   （`base_report_manifest_mismatch` / `base_report_draft_mismatch`），不会拖到发出之后。
8. **真实基线上的替身重放**（只读 `evaluation/results/m930_3_cited_real_company_cp21_r1/`）：
   离线替身把 13 条机械硬错**全部**消掉（**6 句保留、7 句撤下**），而**没有**引入任何一条
   原稿没有的硬错；被点名的 13 句之外，其余句子逐字在场。这一步证明的是**接口与判据接得上**，
   **不是**「真实写作会写成这样」——替身一个字都没有重写，它只是把句子放到引用本来就登记在
   的那一栏，写不出来的整句撤下。
9. **撤下不是清零**：真实重放后本节仍有 >0 句正文，且每一句撤下都在 `gaps` 里留下一条带
   原因码的记录。撤下 ≠ 来源里没有。
10. **去向台账按编号台账算**（`cwr-2` 的更正，§5）：点名集合的 id 来自**初稿**，新稿的 id 已被
   `crn-1` **按遍历顺序重编**，两个编号空间不同源。旧口径 `set(点名) & set(新稿 id)` 得到的
   既不是「改过的」也不是「留下的」。§5 用**同一个真实基线**并排打出旧口径（8 / 5）与新口径
   （6 / 7），并逐条证明新口径与替身自报的去向一致；再对「同文换号」「拆句重号」「合句吞并」
   三种不能唯一对应的情形各给一个反例，证明它们落进 `ambiguous` 而**不是**被塞进两个计数里。
11. **返修请求面与初稿请求面读同一份数字授权读数**（`cwrp-3`，§7）：返修面此前是手工构造的
   输入，只投了 `citable_fact_keys`（Pack 侧**登记**），于是返修者手里的「可写数字键」与逐句
   核对执行的那份不同源。§7 钉三件事——两根轴都到（合成夹具 12 条事实 → 登记 12 / 可写 9 /
   撤回 3）、与初稿请求面**逐栏逐字相同**（同一份 `citable_columns` / `numeric_authorization`，
   不各算一遍）、以及「金额保留、占比撤下」在判据层成立（撤的是**可写性**，不是登记，也不是
   整段营收分析）。真实离线面（只读 `m930_3_cited_offline_ndc4_company_r1` 的 journal
   `request_face`）上复核同一个等式；目录不在场即跳过。

夹具分三层：§1–§3 与 §7 走 `test_m930_3_cited_writer` 的最小真实夹具（无公司代号、无页码、
无固定年份的生产字面量；§7 另加 12 条合成事实）；§4–§5 与 §7.3 是**只读**重放，读的是既存 run
目录里的原稿与清单，不写任何文件。
不调 LLM、不联网、不写库、不建第二套 Harness/Pack/Writer。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import test_m930_3_cited_writer as E        # noqa: E402
from evals import test_m930_3_sentence_check as S      # noqa: E402
from scripts import run_m930_3_cited_chain as RUN      # noqa: E402
from sections import cited_rework as CRW               # noqa: E402
from sections import cited_writer as CW                # noqa: E402
from sections import narrative_schema as NS            # noqa: E402
from sections import sentence_check as SC              # noqa: E402

_BODY_A = E._BODY_A
_BODY_B = E._BODY_B
_REQ_TEXT = E._REQ_TEXT

#: 真实基线：只读。它是**既存**产物，本模块**不**改写它、也不因它不存在而失败（跳过即可）。
REAL_BASELINE = (Path(__file__).resolve().parent.parent
                 / "evaluation" / "results" / "m930_3_cited_real_company_cp21_r1")


# ---------------------------------------------------------------------------
# 夹具：一个小节覆盖**两栏**，两条材料各登记到其中一栏
# ---------------------------------------------------------------------------


def _two_column_fixture():
    """一个小节声明两栏、两条材料各登记一栏 —— 这才构造得出「段落声明的栏 ≠ 引用登记的栏」。

    单栏夹具构造不出这条硬错：`aspect_attribution` 的段级复核判的是
    「本段声明的栏 ∩ 本句引用的登记归属」，两栏都声明了才有「引了 A 栏的材料、却写在 B 栏的
    段落里」这种形态。这正是真实公司节那 6 句的形状。
    """
    aspect_x = E._ASPECT                                  # 主营业务
    aspect_y = E.T.ASP_BUSINESS_SALES                      # 销售模式（同 topic 下的另一栏）
    task = E.T._task("company", (E._TOPIC,))
    materials = (E.T._Material(E._MAT_A, f"evidence:{E.T.EV_ID}", page=12),
                 E.T._Material(E._MAT_B, "evidence:ev-b", page=13))
    pack_set = E.T._pack_set(
        task,
        packs=(E.T._Pack(
            topic_id=E._TOPIC, materials=materials,
            aspect_results=(E.T._AspectResult(aspect_x, "covered"),
                            E.T._AspectResult(aspect_y, "covered")),),),
        requirements=(E.T._Req(E._TOPIC, (
            E.T._aspect(aspect_x, E._TOPIC, E.T._question_id(E._TOPIC),
                        requirement_text=_REQ_TEXT),
            E.T._aspect(aspect_y, E._TOPIC, E.T._question_id(E._TOPIC),
                        requirement_text="列示报告期内的销售模式"),)),))
    from sections import pack_writer as PW
    authority = PW.TopicPackAuthorityInput.create(
        task, pack_set, company_id=E.T.COMPANY_ID, report_as_of=E.T.REPORT_AS_OF,
        contract_version=E.T.CONTRACT_VERSION,
        contract_fingerprint=E.T.CONTRACT_FINGERPRINT)
    context = E.T._writer_material_context(authority.pack_set, task_id=str(task.task_id),
                                           section_id="company")
    specs = (CW.CitedSubsectionSpec(subsection_id="sub-products", title="产品或服务",
                                    requirement_text=_REQ_TEXT,
                                    declared_aspect_ids=(aspect_x, aspect_y)),)
    manifest = CW.build_cited_writer_input(
        authority=authority, material_context=context, subsections=specs,
        facts=(), section_title="主营业务")
    #: 默认入口把**两条**材料都登记到**两栏**（`material_dispositions` 由 aspect_results 反查），
    #: 那样构造不出「引了 A 栏的材料、写在 B 栏的段落里」。这里把登记**逐条**收窄成各占一栏
    #: ——走的仍是 `create` 重算身份那条路（`S._entry` + `S._manifest`），不手改 id。
    mats = (S._entry(manifest.materials[0], aspect_ids=(aspect_y,)),
            S._entry(manifest.materials[1], aspect_ids=(aspect_x,)))
    manifest = S._manifest(manifest, materials=mats)
    return manifest, aspect_x, aspect_y


#: §7 夹具的两句正文。金额那句与占比那句分别是 `scp-12`/`scp-13` **已经钉过的**正例与反例形状
#: （`evals/test_m930_3_numeric_disclosure.py` §4c 的 P3 与 §4d 的反例 2b），因此「哪一句该被
#: 点名」在本仓另有独立证据，本模块不靠自己的判据自证。占比那句写的是**正分母**（「全年营业
#: 收入」）也照样不可写——撤的是**可写性**，不是「它写错了」。
_NFX_AMOUNT_SENTENCE = "2025 年公司甲类业务的销售收入为13,000.5 万元。"
_NFX_RATIO_SENTENCE = "2025 年甲类业务占公司全年营业收入比重为6.3%。"
_NFX_RATIO_NUMBER = "6.3%"

#: 12 条事实的逐值：9 金额（首条与上面那句金额句逐字同值）+ 3 占比（末条与占比句同值）。
_NFX_AMOUNT_RAW = ("13,000.5", "10,000.5", "11,000.5", "12,000.5", "14,000.5",
                   "15,000.5", "16,000.5", "17,000.5", "18,000.5")
_NFX_RATIO_RAW = ("8.1", "7.2", "6.3")


def _numeric_facts(aspect: str, *, carrier_material_id: str):
    """12 条**带逐值身份**的事实（9 金额 + 3 占比），**全部**登记到同一栏（`ndc-4` 夹具）。

    复刻 `ndc-3`/`ndc-4` 公司 Pack 的形状：离线 run 的请求面上 9 条金额与 3 条占比在
    `citable_fact_keys` 里长得一模一样，而逐句核对只授权金额那 9 条。这里不另造判定：逐值
    身份里只有 `value_kind` 参与本段的读数（`sentence_check.fact_numeric_writability` 就读它），
    其余五轴照 `SupportedFact.value_identity` 的形状如实填，免得夹具比真实产物少一根轴。

    `carrier_material_id` 是这条事实的**载体材料锚点**：`topic_pack` 那一支的
    `citation_locator` 判据要「`loc-1` locator / material 载体 / payload 引用」三者有其一，
    缺了就判 `citation_locator_unretrievable`（夹具自己的引线，不是本段要测的东西）。
    """
    out = []
    for i, raw in enumerate(_NFX_AMOUNT_RAW):
        out.append(CW.CitedFactEntry(
            citation_key=CW.cited_fact_key(i), authority_kind="topic_pack",
            container_identity=E.T.EV_ID, fact_id=f"fact-nfx-amt-{i}",
            text=f"2025 年公司甲类业务的销售收入为{raw} 万元",
            topic_id=E._TOPIC, aspect_ids=(aspect,), fact_type="metric",
            period="2025年", scope="公司甲类业务销售", required=True,
            material_id=carrier_material_id,
            value_identity={"value_kind": "amount", "metric": "各业务收入",
                            "unit": "万元", "period": "2025年",
                            "scope": "公司甲类业务销售", "amount_canonical": raw}))
    for j, raw in enumerate(_NFX_RATIO_RAW):
        out.append(CW.CitedFactEntry(
            citation_key=CW.cited_fact_key(len(_NFX_AMOUNT_RAW) + j),
            authority_kind="topic_pack", container_identity=E.T.EV_ID,
            fact_id=f"fact-nfx-ratio-{j}",
            text="2023-2025 年，甲类业务占公司全年营业收入比重为8.1%、7.2%及6.3%",
            topic_id=E._TOPIC, aspect_ids=(aspect,), fact_type="metric",
            period="2025年", scope="甲类业务", required=False,
            material_id=carrier_material_id,
            value_identity={"value_kind": "ratio", "metric": "收入占比",
                            "unit": "%", "period": "2025年",
                            "scope": "甲类业务", "amount_canonical": raw}))
    return out


def _draft_from(manifest, *, aspects_a, citations_a, text_a=_BODY_A,
                aspects_b=(), citations_b=(), text_b=_BODY_B, sentences_b=True):
    """一份构造好的草稿：第 1 段（声明 `aspects_a`）放 `text_a`，第 2 段放 `text_b`。

    **两段各带自己的声明**：一段一个声明才能分别构造出「这一段声明对了」与「这一段声明错了」
    两种形态。单段夹具做不到这件事。
    """
    paragraphs = [{"paragraph_id": "p1", "aspect_ids": list(aspects_a),
                   "sentences": [{"sentence_id": "s1", "text": text_a,
                                  "citations": list(citations_a)}]}]
    if sentences_b:
        paragraphs.append({"paragraph_id": "p2", "aspect_ids": list(aspects_b),
                           "sentences": [{"sentence_id": "s2", "text": text_b,
                                          "citations": list(citations_b)}]})
    payload = json.dumps({
        "subsections": [{"subsection_id": manifest.subsections[0].subsection_id,
                         "title": manifest.subsections[0].title,
                         "paragraphs": paragraphs}],
        "gaps": [], "follow_up_needs": []}, ensure_ascii=False)
    return CW.parse_cited_prose(payload, manifest=manifest)


def _fail(fn, *, reason: str) -> str:
    try:
        fn()
    except CRW.CitedReworkError as exc:
        if exc.reason != reason:
            raise AssertionError(
                f"typed 原因码不是 {reason!r}，而是 {exc.reason!r}：{exc}") from None
        return str(exc)
    raise AssertionError(f"这一路必须拒绝（期望原因码 {reason!r}），但它通过了")


def _rework(draft, report, manifest, *, reply=None, semantic=(), rounds=1,
            client=None):
    """跑一次返修。`reply` 为 `None` 时用离线替身；否则用回放客户端。"""
    if client is None:
        client = (RUN.OfflineReworkCitedProseClient() if reply is None
                  else _ReplayReworkClient(reply))
    return CRW.run_bounded_cited_rework(
        draft=draft, check_report=report, manifest=manifest, client=client,
        semantic_sentence_ids=semantic, model_policy="eval", rounds=rounds)


def _rejected(draft, report, manifest, *, reply, reason: str) -> str:
    """「这一次返修不收」的读法：**编排层**把 `CitedReworkError` 收成 `failed` 读数。

    返修发生在调用之后，因此这里的拒绝形态是 `outcome="failed"` + typed
    `failure_reason`（外加新稿为 `None`、原稿与原硬错读数身份仍在）——不是往调用方抛异常。
    抛异常的只有**调用之前**那几条事前等式（轮次、草稿/清单身份），它们走 `_fail`。
    """
    outcome = _rework(draft, report, manifest, reply=reply)
    if outcome.outcome != "failed" or outcome.failure_reason != reason:
        raise AssertionError(
            f"这一路必须拒（期望 `failed`/{reason}），实测 {outcome.outcome!r}/"
            f"{outcome.failure_reason!r}")
    if outcome.draft is not None:
        raise AssertionError("被拒的返修不得留下新稿")
    return outcome.failure_detail


class _ReplayReworkClient:
    """回放客户端：把给定的**回复原文**当成一次调用的结果。不判断、不改写。"""

    def __init__(self, reply) -> None:
        self._reply = reply if isinstance(reply, str) else json.dumps(
            reply, ensure_ascii=False)
        self.calls: list[dict] = []

    def compose(self, *, messages, system, prompt_version, model_policy):
        self.calls.append({"call_id": "replay-1", "prompt_version": prompt_version})
        return CW.CitedProseResult(text=self._reply, call_id="replay-1",
                                   model="replay", prompt_version=prompt_version,
                                   status="ok")


def _reply_with(*, draft_payload, changes=()):
    return {**draft_payload, "citation_changes": list(changes)}


def _reply_body_from(draft):
    """把一份草稿搬回**回复形状**（逐字：正文、引用、段声明一字不差）。

    用来构造「模型照抄原稿」这一类回复——它合法（点名句允许不改），因此能把
    「返修通过」与「问题被解决」这两件事在测试里分开。
    """
    return {
        "subsections": [
            {"subsection_id": sub.subsection_id, "title": sub.title,
             "paragraphs": [
                 {"paragraph_id": para.paragraph_id,
                  "aspect_ids": list(para.aspect_ids),
                  "sentences": [{"sentence_id": s.sentence_id, "text": s.text,
                                 "citations": list(s.citations)}
                                for s in para.sentences]}
                 for para in sub.paragraphs]}
            for sub in draft.subsections],
        "gaps": [], "follow_up_needs": []}


# ===========================================================================
# §5 去向台账：判据是**编号台账**，不是两个编号空间求交集（`cwr-2` 的更正）
# ===========================================================================


def _assign(model_id: str, final_id: str):
    """一条 `crn-1` 归一化记录（只带本判据要用的两个字段）。"""
    return SimpleNamespace(model_id=model_id, final_id=final_id)


def _dispositions(*, base, reply, assignments, problem_ids):
    """薄封装：本模块只关心「哪一句去了哪」，不关心正文怎么来的。"""
    return CRW.resolve_sentence_dispositions(
        base_pairs=base, reply_pairs=reply, assignments=assignments,
        problem_ids=problem_ids)


def _eval_disposition_ledger(real, real_draft, real_client, check, details) -> None:
    """§5：`cwr-2` 的去向台账。**并排**打出旧口径与新口径，再逐条给正反例。"""
    details.append("## §5 去向台账（`cwr-2`）：按编号台账算，不按两个 id 空间求交集")

    # ---- 5.1 同一个真实基线上的旧口径 vs 新口径 ----------------------------
    named = list(real.problem_sentence_ids) + list(real.semantic_sentence_ids)
    new_ids = {s.sentence_id for sub in real.draft.subsections
               for p in sub.paragraphs for s in p.sentences}
    old_changed = tuple(sorted(set(named) & new_ids))
    old_withdrawn = tuple(sorted(set(named) - new_ids))
    kept = set(real.changed_sentence_ids)
    withdrawn = set(real.withdrawn_sentence_ids)
    ambiguous = set(real.ambiguous_sentence_ids)
    details.append(
        f"NOTE §5.1：同一份真实基线（`cp21_r1`）上，旧口径 `set(点名) & set(新稿 id)` 报 "
        f"**改栏 {len(old_changed)} / 撤下 {len(old_withdrawn)}**；`cwr-2` 按归一化编号台账报 "
        f"**保留 {len(kept)} / 撤下 {len(withdrawn)} / 歧义 {len(ambiguous)}**。")
    check(set(old_changed) != kept,
          f"旧口径与新口径**确实不同**（旧 {sorted(old_changed)}）——本条钉的是「修的是判据、"
          "不是读数文案」：若两者碰巧相等，下面每一条断言都证明不了任何事")
    check(len(kept) == 6 and len(withdrawn) == 7 and not ambiguous,
          f"新口径实测 **6 保留 / 7 撤下 / 0 歧义**（实测 {len(kept)}/{len(withdrawn)}/"
          f"{len(ambiguous)}）")

    # ---- 5.2 三桶互斥且穷尽（台账自己的不变量） ----------------------------
    check(kept | withdrawn | ambiguous == set(named),
          f"三桶并集 == 点名集合（点名 {len(named)}）")
    check(not (kept & withdrawn) and not (kept & ambiguous) and not (withdrawn & ambiguous),
          "三桶两两不相交：没有哪一句被算进两个计数里")
    check(real.disposition_counts == {"kept": len(kept), "withdrawn": len(withdrawn),
                                      "ambiguous": len(ambiguous)},
          f"`disposition_counts` 是从逐句台账现算的派生读数（实测 "
          f"{real.disposition_counts}）")
    check(len(real.sentence_dispositions) == len(named),
          f"逐句台账行数 == 点名句数（{len(real.sentence_dispositions)}）")

    # ---- 5.3 台账与替身**自报**的去向必须对上（两套独立读数） --------------
    self_reported = {d["sentence_id"]: d["disposition"] for d in real_client.dispositions}
    mismatch = [row.base_sentence_id for row in real.sentence_dispositions
                if (row.disposition == "kept") != (self_reported.get(row.base_sentence_id)
                                                   != "withdrawn")]
    check(not mismatch,
          f"`cwr-2` 台账与离线替身**自报**的去向逐句一致（不一致 {mismatch or '无'}）"
          "——两套独立读数对上，才不是自己证明自己")

    # ---- 5.4 落盘 wire 带上了新字段 ----------------------------------------
    wire = real.to_dict()
    check(wire["schema_version"] == "cwr-2" and wire["policy_version"] == "cwrp-3",
          f"结果 wire 是 `cwr-2`（**未**变，`ndc-4` 不动结果字段）／政策 `cwrp-3`"
          f"（实测 {wire['schema_version']} / {wire['policy_version']}）")
    check(wire["ambiguous_sentence_ids"] == list(real.ambiguous_sentence_ids)
          and len(wire["sentence_dispositions"]) == len(named)
          and set(wire["sentence_dispositions"][0]) == {"base_sentence_id", "disposition",
                                                        "final_sentence_ids",
                                                        "ambiguity_reason"},
          "落盘 wire 里有 `ambiguous_sentence_ids` 与逐句 `sentence_dispositions`"
          "（读回页因此**不必**绕开这两个字段）")

    # ---- 5.5 反例：不能唯一对应的四种情形各一个 ----------------------------
    # 反例 1：**换号**。初稿 s0002 的内容在新稿里由编号 s0001 承载（`crn-1` 重编）。
    #         旧口径会把 s0002 读成「撤下」（交集里没有它），真实的它是「保留」。
    b = {"s0001": ("甲句正文足够长", ("m01",)), "s0002": ("乙句的正文写得足够长", ("m02",))}
    r = {"s0001": ("乙句的正文写得足够长", ("m02",))}
    rows = _dispositions(base=b, reply=r,
                         assignments=[_assign("s0002", "s0001")], problem_ids=["s0002"])
    check(rows[0].disposition == "kept" and rows[0].final_sentence_ids == ("s0001",),
          f"换号 ⇒ `kept` 且指向**新**编号（实测 {rows[0].disposition}/"
          f"{list(rows[0].final_sentence_ids)}）——旧口径在这里报的是「撤下」")

    # 反例 2：**重号（拆句）**。模型给两句写了同一个临时编号，`crn-1` 展开成两个最终编号。
    #         不能猜哪一个是「原句」，因此记歧义。
    r2 = {"s0001": ("甲句正文足够长", ("m01",)), "s0002": ("甲句的另一半", ("m01",))}
    rows = _dispositions(base=b, reply=r2,
                         assignments=[_assign("s0001", "s0001"),
                                      _assign("s0001", "s0002")], problem_ids=["s0001"])
    check(rows[0].disposition == "ambiguous"
          and rows[0].ambiguity_reason == "id_reused_in_reply",
          f"拆句重号 ⇒ `ambiguous`/`id_reused_in_reply`（实测 {rows[0].disposition}/"
          f"{rows[0].ambiguity_reason!r}），**不**算进保留也不算进撤下")

    # 反例 3：**同文换号**。内容逐字在，但挂在初稿没出现过的编号上。
    r3 = {"s0009": ("乙句的正文写得足够长", ("m02",))}
    rows = _dispositions(base=b, reply=r3,
                         assignments=[_assign("s0099", "s0009")], problem_ids=["s0002"])
    check(rows[0].disposition == "ambiguous"
          and rows[0].ambiguity_reason == "id_absent_text_present",
          f"同文挂在未知编号上 ⇒ `ambiguous`/`id_absent_text_present`（实测 "
          f"{rows[0].disposition}/{rows[0].ambiguity_reason!r}）")

    # 反例 4：**合句**。本句正文被另一句逐字吞进去（真包含），编号也没了。
    r4 = {"s0001": ("另一句的正文" + "乙句的正文写得足够长" + "接在后面", ("m02",))}
    rows = _dispositions(base=b, reply=r4,
                         assignments=[_assign("s0001", "s0001")], problem_ids=["s0002"])
    check(rows[0].disposition == "ambiguous"
          and rows[0].ambiguity_reason == "id_absent_text_absorbed",
          f"正文被吞（合句）⇒ `ambiguous`/`id_absent_text_absorbed`（实测 "
          f"{rows[0].disposition}/{rows[0].ambiguity_reason!r}）")

    # 反例 5：**同文重复**且重复的那一份由**受保护句**保住 ⇒ 本句记 `撤下`，**不是**歧义。
    #         正文在稿里还在，但它属于另一句点名之外的句子；把它记成歧义会让「同文重复」
    #         把每一个重复句都变成不可判，读数就废了。
    dup = {"s0003": ("重复句正文足够长", ("m03",)), "s0004": ("重复句正文足够长", ("m03",))}
    rows = _dispositions(base=dup, reply={"s0004": ("重复句正文足够长", ("m03",))},
                         assignments=[_assign("s0004", "s0004")], problem_ids=["s0003"])
    check(rows[0].disposition == "withdrawn",
          f"同文重复、存活的那一份是**另一句** ⇒ `withdrawn`（实测 {rows[0].disposition}）")

    # 反例 6：**短句不参与吞并判定**。`_ABSORB_MIN_CHARS` 的存在是刻意的：两三字的残句在
    #         旁边任何一句里都可能碰巧出现，那不是证据。
    short = {"s0005": ("短。", ("m04",))}
    rows = _dispositions(base=short, reply={"s0001": ("前面短。后面", ("m04",))},
                         assignments=[_assign("s0001", "s0001")], problem_ids=["s0005"])
    check(rows[0].disposition == "withdrawn",
          f"短于 {CRW._ABSORB_MIN_CHARS} 字的正文**不**触发合句判定，照旧 `withdrawn`"
          f"（实测 {rows[0].disposition}）")

    # 反例 7：平凡反例——真的撤下就该是撤下（既不换号也不被吞）。
    rows = _dispositions(base=b, reply={"s0001": ("甲句正文足够长", ("m01",))},
                         assignments=[_assign("s0001", "s0001")], problem_ids=["s0002"])
    check(rows[0].disposition == "withdrawn" and not rows[0].final_sentence_ids,
          f"真的撤下 ⇒ `withdrawn` 且没有最终编号（实测 {rows[0].disposition}/"
          f"{list(rows[0].final_sentence_ids)}）")

    # 反例 8：两套词表**故意**不同名。替身自报的是**意图**，台账记的是**结果**。
    check(set(RUN.REWORK_DISPOSITIONS) != set(CRW.REWORK_SENTENCE_DISPOSITIONS),
          "替身自报的词表与台账的词表**不是同一套**（"
          f"{sorted(RUN.REWORK_DISPOSITIONS)} vs "
          f"{sorted(CRW.REWORK_SENTENCE_DISPOSITIONS)}）：前者是意图、后者是结果，"
          "合并会让「它说它改了」充当「它确实改成了」")


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

    manifest, aspect_x, aspect_y = _two_column_fixture()

    # ======================================================== §1 只收点名句
    details.append("## §1 没被点名的句子必须逐字原样（一处改动即整次作废）")
    #: 第 1 段声明 X，引用的却是登记在 Y 的材料 ⇒ 段级归属硬错（本批要修的那条）。
    draft = _draft_from(manifest, aspects_a=(aspect_x,),
                        citations_a=("m01",), aspects_b=(aspect_x,), citations_b=("m02",))
    report = SC.check_cited_prose(draft=draft, manifest=manifest)
    repair_kinds = {r.check_kind for r in report.hard_error_records}
    check("aspect_attribution" in repair_kinds,
          f"夹具确实构造出段级栏目归属硬错（实测硬错轴 {sorted(repair_kinds)}）")

    outcome = _rework(draft, report, manifest)
    check(outcome.outcome == "reworked",
          f"离线替身把这一句放到登记一致的栏目上 ⇒ 返修通过（实测 {outcome.outcome!r}）")
    check(outcome.protected_sentence_count == 1,
          f"未被点名的那一句是**受保护句**（实测保护 {outcome.protected_sentence_count} 句）")
    check(len(outcome.hard_error_sentence_ids) == 0,
          f"返修稿没有硬错（实测 {list(outcome.hard_error_sentence_ids)}）")
    new_texts = {s.sentence_id: s.text for p in outcome.draft.subsections
                 for s in p.sentences()}
    check(_BODY_B in new_texts.values(),
          "受保护句的正文在返修稿里逐字在场")
    check(outcome.reworked_draft_id != outcome.base_draft_id,
          "新稿有**自己的**身份（不是原稿的 id）")

    details.append("## §1b 受保护句被改动 ⇒ 整次返修作废、原稿是有效草稿")
    tampered = _reply_with(draft_payload={
        "subsections": [{"subsection_id": "sub-products", "title": "产品或服务",
                         "paragraphs": [{"paragraph_id": "p1", "aspect_ids": [aspect_y],
                                         "sentences": [
                                             {"sentence_id": "s1", "text": _BODY_A,
                                              "citations": ["m01"]},
                                             {"sentence_id": "s2",
                                              "text": _BODY_B + "。",
                                              "citations": ["m02"]}]}]}],
        "gaps": [], "follow_up_needs": []})
    bad = _rework(draft, report, manifest, reply=tampered)
    check(bad.outcome == "failed" and bad.failure_reason == "protected_sentence_altered",
          f"改一个字即拒（实测 {bad.outcome!r} / {bad.failure_reason!r}）")
    check(bad.draft is None,
          "失败时**不留新稿**：`draft is None`，调用方手上的原稿因此仍是有效草稿")
    check(bad.base_draft_id == draft.draft_id
          and bad.base_check_report_id == report.report_id,
          "失败读数仍带着原稿与原硬错读数的身份（可逐条回查）")

    # ======================================================== §2 不许静默换引用
    details.append("## §2 换引用必须逐条声明（反例 + 正例）")
    #: 这一组用**另一份**基准稿：第 1 段声明 X 而引 m01（登记 Y）⇒ 那句是硬错句；第 2 段声明 Y
    #: 引 m01 ⇒ 通过，因此它是**受保护句**。`m02` 在基准稿里**一次都没被引用**，于是
    #: 「往稿里加 m02」就是一次货真价实的**新引用键**——不声明就该被拦。
    change_base = _draft_from(manifest, aspects_a=(aspect_x,), citations_a=("m01",),
                              aspects_b=(aspect_y,), citations_b=("m01",))
    change_report = SC.check_cited_prose(draft=change_base, manifest=manifest)
    #: 这里要的是「**有硬错**的句子」，不是「**放行门挡住**的句子」：`scp-10` 起
    #: `blocked_sentence_ids` 只收事实安全族，而本夹具构造的是栏目归属族（`aspect_attribution`）。
    #: 拿 `blocked_sentence_ids` 当夹具选取器，会在族的边界一动时静默选空——那正是这条断言
    #: 一度红掉的原因。返修点名的也是两族并集（`cited_rework.build_cited_rework_request`），
    #: 因此夹具与受测入口在这里取同一个集合。
    check(change_report.hard_error_sentence_ids == ("s0001",)
          and "m02" not in {key for s in change_base.sentences() for key in s.citations},
          "§2 夹具：只有 s0001 是硬错句，且 m02 在基准稿里一次都没出现")

    def _reply(s1_citations, *, s1_id="t1"):
        return _reply_with(
            draft_payload={
                "subsections": [{"subsection_id": "sub-products", "title": "产品或服务",
                                 "paragraphs": [
                                     {"paragraph_id": "p1", "aspect_ids": [aspect_y],
                                      "sentences": [{"sentence_id": s1_id, "text": _BODY_A,
                                                     "citations": list(s1_citations)}]},
                                     {"paragraph_id": "p2", "aspect_ids": [aspect_y],
                                      "sentences": [{"sentence_id": "t2", "text": _BODY_B,
                                                     "citations": ["m01"]}]}]}],
                "gaps": [], "follow_up_needs": []})

    undeclared = _reply(["m01", "m02"])
    check(_rejected(change_base, change_report, manifest, reply=undeclared,
                    reason="undeclared_citation_change").find("m02") >= 0,
          "未声明的引用键被逐字报出来（不是一句泛化的「引用有问题」）")

    _rejected(change_base, change_report, manifest,
              reply=_reply_with(draft_payload=undeclared,
                                changes=[{"sentence_id": "t1", "from": ["m01"],
                                          "to": ["m01"]}]),
              reason="citation_change_target_mismatch")
    check(True, "声明的新引用与该句在稿里的真实引用不符 ⇒ 拒（声明不是事后的白名单）")

    _rejected(change_base, change_report, manifest,
              reply=_reply_with(draft_payload=undeclared,
                                changes=[{"sentence_id": "不存在的句", "from": ["m01"],
                                          "to": ["m01", "m02"]}]),
              reason="citation_change_sentence_unknown")
    check(True, "声明指向稿里不存在的句子 ⇒ 拒")

    #: 「引用键不在本清单里」这条路由**写作侧**的 `validate_citations` 先关掉：返修稿走的是同一
    #: 套解析，因此这个错**不可能**活到 `citation_change_unknown_key` 那一刻。这里如实钉住
    #: 真正生效的那道门，而不是钉一个到不了的分支。
    _rejected(change_base, change_report, manifest, reply=_reply(["m01", "m99"]),
              reason="rework_draft_invalid")
    check(True, "稿里出现清单外的引用键 ⇒ 由写作侧同一套 `validate_citations` 拒"
                "（返修不另开一条更松的引用校验）")

    legit = _reply_with(
        draft_payload=undeclared,
        changes=[{"sentence_id": "t1", "from": ["m01"], "to": ["m01", "m02"]}])
    ok = _rework(change_base, change_report, manifest, reply=legit)
    check(ok.outcome == "reworked",
          f"声明合法且与实情一致 ⇒ 返修通过（实测 {ok.outcome!r} / {ok.failure_reason!r}）")
    check(len(ok.declared_citation_changes) == 1
          and ok.declared_citation_changes[0]["sentence_id"] != "t1",
          "声明里的句子 id 已由 `crn-1` 台账解析成**最终** id（不是把模型临时编号原样收下）")
    check(list(ok.declared_citation_changes[0]["to"]) == ["m01", "m02"],
          "声明的 `to` 与那一句在稿里的真实引用逐字相符（排序后比较）")

    # ======================================================== §3 刻意的边界
    details.append("## §3 轮次上限 / 不必返修 / 事前等式")
    _fail(lambda: _rework(draft, report, manifest, rounds=2),
          reason="rework_rounds_out_of_range")
    check(True, "`rounds=2` 当场抛：**没有**第二次返修的入口")
    _fail(lambda: _rework(draft, report, manifest, rounds=0),
          reason="rework_rounds_out_of_range")
    check(True, "`rounds=0` 同样抛：本接口只有「一次」这一个轮次")

    clean = _draft_from(manifest, aspects_a=(aspect_y,),
                        citations_a=("m01",), aspects_b=(aspect_x,), citations_b=("m02",))
    clean_report = SC.check_cited_prose(draft=clean, manifest=manifest)
    check(not clean_report.blocked_sentence_ids,
          f"构造出一份没有硬错的草稿（实测硬错 {list(clean_report.blocked_sentence_ids)}）")
    quiet = RUN.OfflineReworkCitedProseClient()
    none_needed = _rework(clean, clean_report, manifest, client=quiet)
    check(none_needed.outcome == "not_needed" and quiet.calls == [],
          "没有点名句 ⇒ `not_needed` 且**一个调用都不发**")

    other = _draft_from(manifest, aspects_a=(aspect_x,),
                        citations_a=("m02",), aspects_b=(aspect_x,), citations_b=("m02",))
    _fail(lambda: CRW.build_cited_rework_request(
        draft=other, check_report=report, manifest=manifest),
        reason="base_report_draft_mismatch")
    check(True, "核对报告不是这份草稿的 ⇒ 装配请求面时就拒（不拖到发出之后）")

    # ======================================================== §4 真实基线只读重放
    details.append("## §4 真实基线（cp21_r1）的替身重放：**只读**")
    if not (REAL_BASELINE / "cited_prose.json").is_file():
        skipped += 1
        details.append(f"SKIP: 真实基线目录不在场（{REAL_BASELINE}），§4–§5 跳过；"
                       "本模块不因既存产物缺失而失败，也不去别处找替代。")
    else:
        man = CW.CitedWriterInputManifest.from_dict(json.loads(
            (REAL_BASELINE / "cited_input_manifest.json").read_text("utf-8")))
        real_draft = CW.CitedProseDraft.from_dict(json.loads(
            (REAL_BASELINE / "cited_prose.json").read_text("utf-8"))["draft"])
        before = CW.CitedWriterInputManifest.from_dict(json.loads(
            (REAL_BASELINE / "cited_input_manifest.json").read_text("utf-8")))
        real_report = SC.check_cited_prose(draft=real_draft, manifest=man)
        check(len(man.materials) == len(before.materials),
              f"换一份清单读的是**同一份**材料集（{len(man.materials)} 份，未增未减）")

        real_client = RUN.OfflineReworkCitedProseClient()
        real = _rework(real_draft, real_report, man, client=real_client)
        #: 基线硬错取**两族并集**（13 句），与 `cited_rework` 实际点名的集合同源；
        #: `blocked_sentence_ids` 自 `scp-10` 起只收事实安全族（本基线 7 句），拿它当
        #: 「本条基线有多少条硬错」会把栏目覆盖族那 6 句算成「受保护句」——它们**没**被
        #: 点名，但那不是「它们没问题」。族分层本身的断言在
        #: `evals/test_m930_3_sentence_check.py`，本模块只保证**点名的集合**与它同源。
        base_hard = set(real_report.hard_error_sentence_ids)
        check(real.outcome == "reworked",
              f"真实基线返修通过（实测 {real.outcome!r} / {real.failure_reason!r}）")
        check(len(real.hard_error_sentence_ids) == 0,
              f"返修稿零硬错（原稿 {len(base_hard)} 句，实测剩 "
              f"{list(real.hard_error_sentence_ids)}）")
        check(real.protected_sentence_count == len(real_draft.sentence_ids()) - len(base_hard),
              f"受保护句数 = 全句数 − 点名句数（实测 {real.protected_sentence_count}）")
        check(len(real_draft.sentence_ids()) - len(real.hard_error_sentence_ids) > 0,
              f"**没有清零整节**：返修后仍有 "
              f"{len(real_draft.sentence_ids()) - len(real.withdrawn_sentence_ids)} 句正文")

        # 材料未被改动：清单身份逐字不变（本模块从不写清单，读回来核对）
        again = CW.CitedWriterInputManifest.from_dict(json.loads(
            (REAL_BASELINE / "cited_input_manifest.json").read_text("utf-8")))
        check(again.manifest_id == man.manifest_id,
              "重放前后 `manifest_id` 逐字不变（65 份材料一份没丢、一份没改）")

        # 无资格金额 / 比率没有被放行：新稿里不得再出现任何 `numeric_qualification` 硬错
        numeric_after = [r for r in real.check_report.records
                         if r.check_kind == "numeric_qualification"
                         and r.verdict == "hard_error"]
        numeric_before = [r for r in real_report.records
                          if r.check_kind == "numeric_qualification"
                          and r.verdict == "hard_error"]
        check(not numeric_after,
              f"返修稿里 `numeric_qualification` 硬错为 0（原稿 {len(numeric_before)} 条）："
              "替身撤下而不是放行")
        check(len(numeric_after) <= len(numeric_before),
              "撤下只可能减少无资格数字，不会凭空多出一条")

        # 逐句去向必须覆盖**每一句**点名句
        named = set(real.problem_sentence_ids) | set(real.semantic_sentence_ids)
        covered = (set(real.changed_sentence_ids) | set(real.withdrawn_sentence_ids)
                   | set(real.ambiguous_sentence_ids))
        check(covered == named,
              f"每一句点名句都有去向（点名 {len(named)}，有去向 {len(covered)}）")
        check(real.protected_sentence_count + len(named) == len(real_draft.sentence_ids()),
              "受保护句 + 点名句 = 原稿全句数（一句不漏、一句不多）")

        details.append(
            f"NOTE §4：真实基线 {len(base_hard)} 条硬错 → 0；"
            f"保留 {len(real.changed_sentence_ids)} 句、"
            f"撤下 {len(real.withdrawn_sentence_ids)} 句、"
            f"歧义 {len(real.ambiguous_sentence_ids)} 句。"
            "这是**接口与判据接得上**的证据，**不是**写作质量的证据：离线替身一个字都没有"
            "重写，写不出来的句子它整句撤下。真实返修该不该重写而不是撤下，只有真实 run 能答。")

        _eval_disposition_ledger(real, real_draft, real_client, check, details)

    # ============================== §6 返修失败 / 返修后仍有硬错 / 三个终态的留存
    details.append("## §6 返修失败、返修后仍有硬错、三个终态各自留了什么")
    #: 同 §2：夹具要的是「有硬错的句子」，取两族并集。`blocked_sentence_ids` 自 `scp-10` 起
    #: 只收事实安全族，本夹具的硬错在栏目归属族，拿它当选取器会选空。
    named = tuple(report.hard_error_sentence_ids)
    check("s0001" in named,
          f"§6 夹具：s0001 是硬错句（实测 {list(named)}）")

    # ---- 6.1 调用本身失败（抛异常）----------------------------------------
    class _RaisingClient:
        def __init__(self, exc):
            self._exc = exc
            self.calls: list[dict] = []

        def compose(self, *, messages, system, prompt_version, model_policy):
            self.calls.append({"call_id": "boom-1"})
            raise self._exc

    boom = _rework(draft, report, manifest, client=_RaisingClient(RuntimeError("连接被拒")))
    check(boom.outcome == "failed" and boom.failure_reason == "rework_call_failed"
          and boom.draft is None,
          f"反例：调用抛异常 ⇒ `failed`/`rework_call_failed`、**不留新稿**（实测 "
          f"{boom.outcome}/{boom.failure_reason!r}）")
    check(boom.base_draft_id == draft.draft_id
          and boom.base_check_report_id == report.report_id and boom.request_id,
          "失败读数仍带着**原稿**与**原硬错读数**的身份，以及这次的请求面 id（逐条可回查）")

    # ---- 6.2 调用返回了，但 `status != ok` --------------------------------
    class _ErrorStatusClient:
        def __init__(self):
            self.calls: list[dict] = []

        def compose(self, *, messages, system, prompt_version, model_policy):
            self.calls.append({"call_id": "bad-1"})
            return CW.CitedProseResult(
                text="", call_id="bad-1", model="eval", prompt_version=prompt_version,
                status="error", error="provider 500")

    err = _rework(draft, report, manifest, client=_ErrorStatusClient())
    check(err.outcome == "failed" and err.failure_reason == "rework_call_failed"
          and err.call and err.call.get("call_id") == "bad-1" and err.draft is None,
          f"反例：`status='error'` 也走 `rework_call_failed`，且**调用身份进读数**"
          f"（实测 {err.outcome}/{err.failure_reason!r}/"
          f"{(err.call or {}).get('call_id')!r}）")
    check(err.base_draft_id == draft.draft_id,
          "这一路同样保留原稿身份：一次失败的返修不等于「这一节没有了」")

    # ---- 6.3 返修**通过了**但硬错一条没少 ---------------------------------
    # 这是本模块唯一一条钉「返修 ≠ 改好了」的判据：回复逐字照抄原稿（合法——点名句允许不改，
    # 受保护句照旧在场），因此 `reworked` 成立而硬错一条没少。读数必须**如实**说没少，
    # 而不是把「返修通过」渲染成「问题解决」。
    echo = _rework(draft, report, manifest,
                   reply=_reply_with(draft_payload=_reply_body_from(draft)))
    check(echo.outcome == "reworked",
          f"反例 6.3：照抄原稿也能通过接口校验（实测 {echo.outcome!r}/"
          f"{echo.failure_reason!r}）——「通过」只表示没破坏原稿")
    check(set(echo.hard_error_sentence_ids) == set(named) and named,
          f"**返修后硬错一条没少**，读数如实列出（原 {len(named)} 条，返修稿 "
          f"{len(echo.hard_error_sentence_ids)} 条）")
    check(echo.base_draft_id == draft.draft_id
          and echo.reworked_draft_id and echo.reworked_draft_id != draft.draft_id,
          "成功时**两稿身份都在**：新稿有自己的 id，原稿的 id 也留着")
    check(echo.protected_sentence_count == len(draft.sentence_ids()) - len(named),
          f"受保护句数与点名集合互补（实测 {echo.protected_sentence_count}）")

    # ---- 6.4 三个终态各自留了什么（读回页那一段文案的来源）----------------
    note_reworked = RUN._rework_retention_note(echo, gate="", has_base=True)
    note_failed = RUN._rework_retention_note(boom, gate="", has_base=True)
    note_skipped = RUN._rework_retention_note(None, gate="no_prose_to_rework",
                                              has_base=True)
    check("cited_prose.json" in note_reworked
          and "cited_prose_reworked.json" in note_reworked
          and "sentence_checks_initial.json" in note_reworked,
          "终态 `reworked` 的留存说明逐一点名两稿与两份核对（不是一句「返修成功」）")
    check("失败" in note_failed and "有效草稿" in note_failed
          and "不可发布" in note_failed and "rework_call_failed" in note_failed,
          "终态 `failed` 的留存说明写明：初稿仍是有效草稿、预览不可发布、失败原因带原因码")
    check("没有发起返修调用" in note_skipped,
          "终态 `not_needed` 的留存说明写明**没有发调用**（不是「返修失败」）")

    # ---- 6.4b 留档说明**只能提真正落盘的文件** ---------------------------
    # `sentence_checks_initial.json` 只在「生效读数 ≠ 初稿读数」时才写。返修失败时生效读数
    # **就是**初稿，那一份**不存在**；此时若说明里还点名它，读回的人会去找一份没有的文件，
    # 并把「本来就不需要另存」误读成「证据没留下来」。这条是反方向的钉子：`has_base=False`
    # 必须改变文案，而不只是被忽略的参数。
    note_reworked_nb = RUN._rework_retention_note(echo, gate="", has_base=False)
    note_failed_nb = RUN._rework_retention_note(boom, gate="", has_base=False)
    check("sentence_checks_initial.json" not in note_reworked_nb
          and "sentence_checks_initial.json" not in note_failed_nb,
          "反例 6.4b：留存说明点名的文件**必须真的存在**——没有另存初稿读数时，"
          "文案里就不出现 `sentence_checks_initial.json`")
    check("sentence_checks.json" in note_reworked_nb
          and "sentence_checks.json" in note_failed_nb,
          "反例 6.4b 的另一半：撤下 `_initial` 之后**生效读数仍被点名**（不是整段消失）")
    check(note_reworked != note_reworked_nb and note_failed != note_failed_nb,
          "`has_base` 是**真参数**：两种取值必须产出不同文案（否则它只是装饰）")

    # ---- 6.5 三态同一个口径：受保护 + 点名 == 初稿句数 --------------------
    # `protected_sentence_count` 在三种终态里都必须是「初稿句数 − 点名句数」。三态各写各的，
    # 这条可核等式就只在一半终态上成立；读回页会把终态 ① 印成「受保护句 0 句」，
    # 而那一稿其实一句都没被点名、**整稿都是受保护的**。
    clean = _draft_from(manifest, aspects_a=(aspect_y,), citations_a=("m01",),
                        aspects_b=(aspect_x,), citations_b=("m02",))
    clean_report = SC.check_cited_prose(draft=clean, manifest=manifest)
    clean_client = _ReplayReworkClient({"subsections": [], "gaps": [],
                                        "follow_up_needs": []})
    nothing = _rework(clean, clean_report, manifest, client=clean_client)
    check(nothing.outcome == "not_needed" and clean_client.calls == [],
          f"夹具：这份稿零硬错 ⇒ `not_needed` 且**一个调用都不发**（实测 "
          f"{nothing.outcome!r}/{len(clean_client.calls)} 次调用）")

    for label, outcome, total, named_n in (
            ("reworked", echo, len(draft.sentence_ids()), len(set(named))),
            ("failed", boom, len(draft.sentence_ids()), len(set(named))),
            ("not_needed", nothing, len(clean.sentence_ids()), 0)):
        check(outcome.protected_sentence_count + named_n == total,
              f"终态 `{label}` 满足「受保护 + 点名 == 初稿句数」"
              f"（{outcome.protected_sentence_count} + {named_n} == {total}）")
    check(nothing.protected_sentence_count == len(clean.sentence_ids()),
          f"终态 `not_needed` 里**整稿都受保护**（没有一句被点名），不是 0 句"
          f"（实测 {nothing.protected_sentence_count} / 初稿 {len(clean.sentence_ids())}）")
    check(boom.protected_sentence_count == echo.protected_sentence_count,
          f"失败态与成功态的受保护读数**同口径**（实测 {boom.protected_sentence_count} "
          f"vs {echo.protected_sentence_count}）——返修失败没有改动任何一句")

    # ============================== §7 返修请求面的数字授权投影（`cwrp-3`）
    details.append("## §7 返修请求面把「Pack 登记」与「本版可写」两根轴一并投过去（`cwrp-3`）")
    #: 动因（`ndc-4` 收尾）：初稿请求面（`cp-25`）已经带 `writable_fact_keys` 与顶层
    #: `numeric_authorization`，而返修请求面是 `build_cited_rework_request` **手工构造**的输入，
    #: 只投了 `citable_fact_keys`（Pack 侧**登记**）。于是返修者手里的「可写数字键」与逐句核对
    #: 执行的那份读数不同源——`ndc-3` 暴露的那处自相矛盾在返修面上被复刻了一遍。
    #:
    #: 一条**必须先说清**的设计事实：返修面**不投事实原文**（`authority_facts` 不在这里，返修
    #: 者拿不到 12 条事实各自的 `text` / `value_identity`）。所以「哪几条本版写不了」这件事在
    #: 返修面上**只能**靠这次投影说出来；少了它，返修面就只有登记轴可读。
    nfx_manifest, nfx_aspect, _nfx_side = _two_column_fixture()
    nfx_manifest = S._manifest(
        nfx_manifest,
        facts=_numeric_facts(nfx_aspect,
                             carrier_material_id=nfx_manifest.materials[1].material_id))
    nfx_amount_key = nfx_manifest.facts[0].citation_key      # `f01`：金额，本版可写
    nfx_ratio_key = nfx_manifest.facts[-1].citation_key      # `f12`：占比，本版不可写
    nfx_draft = _draft_from(
        nfx_manifest, aspects_a=(nfx_aspect,), citations_a=(nfx_amount_key,),
        text_a=_NFX_AMOUNT_SENTENCE,
        aspects_b=(nfx_aspect,), citations_b=(nfx_ratio_key,),
        text_b=_NFX_RATIO_SENTENCE)
    nfx_report = SC.check_cited_prose(draft=nfx_draft, manifest=nfx_manifest)

    # ---- 7.0 夹具自检：金额句干净、占比句是硬错 ---------------------------
    #: 这两句不是随便挑的：金额句与占比句分别是 `scp-12`/`scp-13` 已经钉过的正例与反例形状
    #: （见 `evals/test_m930_3_numeric_disclosure.py` §4c 的 P3 与 §4d 的反例 2b），因此
    #: 「哪一句该被点名」在本仓另有独立证据，本段不靠自己的判据自证。
    check("s0002" in nfx_report.hard_error_sentence_ids
          and "s0001" not in nfx_report.hard_error_sentence_ids,
          f"夹具自检：占比句（`s0002`）被点名、金额句（`s0001`）干净（实测硬错 "
          f"{list(nfx_report.hard_error_sentence_ids)}）")
    _nfx_ratio_rec = [r for r in nfx_report.records
                      if r.sentence_id == "s0002" and r.verdict == "hard_error"]
    check(any(r.check_kind == "numeric_qualification" for r in _nfx_ratio_rec),
          f"占比句的硬错落在 `numeric_qualification` 这一轴（实测 "
          f"{[(r.check_kind, r.failure_reason) for r in _nfx_ratio_rec]}）")

    nfx_request = CRW.build_cited_rework_request(
        draft=nfx_draft, check_report=nfx_report, manifest=nfx_manifest)
    nfx_face = nfx_request.payload
    nfx_writer_face = CW.build_cited_prose_request(manifest=nfx_manifest)

    # ---- 7.1 12 / 9 / 3：两个请求面上的两根轴逐字相同 ---------------------
    nfx_withheld = CW.withheld_numeric_fact_keys(manifest=nfx_manifest)
    check(len(nfx_manifest.facts) == 12 and len(nfx_withheld) == 3,
          f"夹具自检：12 条事实（9 金额 + 3 占比），其中 3 条本版不可写（实测 "
          f"{len(nfx_manifest.facts)} / {len(nfx_withheld)}）")
    check(dict(nfx_face["numeric_authorization"]) == nfx_writer_face["numeric_authorization"],
          "**正例**：返修请求面的 `numeric_authorization` 与初稿请求面的**逐字相同**"
          "（两者都是 `CW.numeric_authorization(manifest)` 这一次调用，不各算一遍）")
    check(set(nfx_face) & {"authority_facts", "diagnostic_slot_fact_keys"} == set(),
          "返修面**不投事实原文**：没了 `authority_facts`，授权块是它唯一能看见"
          "「哪几条本版写不了」的地方（这正是这次投影非要不可的理由）")

    nfx_wf_cols = [c for sub in nfx_writer_face["subsections"]
                   for c in sub["citable_columns"]]
    nfx_rw_cols = [c for sub in nfx_face["subsections"] for c in sub["columns"]]
    check(len(nfx_wf_cols) == len(nfx_rw_cols) == 2
          and all(a["aspect_id"] == b["aspect_id"]
                  for a, b in zip(nfx_wf_cols, nfx_rw_cols)),
          f"两个请求面列出**同一批栏**、同一顺序（实测 {[c['aspect_id'] for c in nfx_rw_cols]}）")
    check(all(list(a["citable_fact_keys"]) == list(b["citable_fact_keys"])
              and list(a["writable_fact_keys"]) == list(b["writable_fact_keys"])
              for a, b in zip(nfx_wf_cols, nfx_rw_cols)),
          "**正例**：逐栏的 `citable_fact_keys` 与 `writable_fact_keys` 在两个请求面上**逐字相同**"
          "（同一份 `citable_columns` 的投影，返修面不另算一份）")
    nfx_reg = [c for c in nfx_rw_cols if len(c["citable_fact_keys"]) == 12]
    check(len(nfx_reg) == 1 and len(nfx_reg[0]["writable_fact_keys"]) == 9,
          f"**12 / 9 / 3**：12 条全部登记到本栏、其中 9 条本版可写、3 条被撤回（实测 "
          f"{[len(c['citable_fact_keys']) for c in nfx_rw_cols]} / "
          f"{[len(c['writable_fact_keys']) for c in nfx_rw_cols]}）")
    check(nfx_ratio_key in nfx_reg[0]["citable_fact_keys"]
          and nfx_ratio_key not in nfx_reg[0]["writable_fact_keys"]
          and nfx_amount_key in nfx_reg[0]["writable_fact_keys"],
          "**反例**：占比键**仍在** `citable_fact_keys`（登记语义一字未动）里、**不在**可写集里；"
          "同栏的金额键在可写集里——两根轴不得被合成一根")
    check(all(set(c["writable_fact_keys"]) <= set(c["citable_fact_keys"])
              for c in nfx_rw_cols),
          "`writable_fact_keys` 是 `citable_fact_keys` 的**子集**：返修面不新造候选")
    check([w["key"] for w in nfx_face["numeric_authorization"]["withheld"]]
          == [k for k, _ in nfx_withheld]
          and all(w["state"] == SC.FACT_NUMERIC_DENOMINATOR_UNVERIFIED
                  for w in nfx_face["numeric_authorization"]["withheld"]),
          f"撤回项**逐条**带着键与原因码（不是一句「不可写」；实测 "
          f"{nfx_face['numeric_authorization']['withheld']}）")
    check(nfx_request.request_id and nfx_face["task_id"] == nfx_manifest.task_id,
          "请求面身份仍由内容寻址：加了两个键就换一个 `request_id`（旧 run 的面按它自己那一版读）")

    # ---- 7.2 「金额保留、占比撤下」在判据层成立 ---------------------------
    #: 这一路是**合法**返修：被点名的只有占比句；金额句是受保护句，按第 1 条必须逐字保留。
    #: 因此它同时钉住两件事——撤一个占比**不**连带撤掉同栏的金额叙述，也**不**等于撤掉整节。
    _nfx_sub = nfx_manifest.subsections[0]
    _nfx_gap = {"subsection_id": _nfx_sub.subsection_id, "requirement_text": _REQ_TEXT,
                "reason": "no_source_in_manifest",
                "detail": "占比事实本版不可写：分母在本版字段里无处承载与核验，撤下该数字。"}
    nfx_keep = _rework(nfx_draft, nfx_report, nfx_manifest, reply={
        "subsections": [{"subsection_id": _nfx_sub.subsection_id, "title": _nfx_sub.title,
                         "paragraphs": [
                             {"paragraph_id": "p1", "aspect_ids": [nfx_aspect],
                              "sentences": [{"sentence_id": "t1",
                                             "text": _NFX_AMOUNT_SENTENCE,
                                             "citations": [nfx_amount_key]}]}]}],
        "gaps": [dict(_nfx_gap)], "follow_up_needs": []})
    check(nfx_keep.outcome == "reworked",
          f"「金额保留、占比撤下」这一路**通过**（实测 {nfx_keep.outcome!r}/"
          f"{nfx_keep.failure_reason!r}）")
    _nfx_new = [s.text for sub in nfx_keep.draft.subsections
                for p in sub.paragraphs for s in p.sentences]
    check(_NFX_AMOUNT_SENTENCE in _nfx_new,
          f"**金额保留**：被点名的营收金额句在新稿里逐字在场（实测 {_nfx_new}）")
    check(all(_NFX_RATIO_NUMBER not in t for t in _nfx_new),
          "**占比撤下**：那个占比数字不在新稿里，也**没有**换一个别处的百分比顶上")
    check(bool(nfx_keep.draft.gaps)
          and any("占比" in str(g.detail) for g in nfx_keep.draft.gaps),
          f"撤下的那一条留下**结构化缺口**（实测 "
          f"{[g.detail for g in nfx_keep.draft.gaps]}）")
    check(len(nfx_keep.draft.sentence_ids()) > 0,
          "撤一个占比**不等于**撤掉整段营收分析：本节仍有正文")
    check(nfx_keep.withdrawn_sentence_ids and not nfx_keep.ambiguous_sentence_ids,
          f"占比句进「撤下」桶且**没有**歧义（实测撤下 {list(nfx_keep.withdrawn_sentence_ids)}"
          f" / 歧义 {list(nfx_keep.ambiguous_sentence_ids)}）")

    #: **反例**：把占比句原样留着 ⇒ 接口照样「通过」（它没破坏原稿），但返修稿仍带着那条
    #: 硬错。读数必须如实报出来——「返修通过」与「这条占比现在能写了」是两件事。
    nfx_kept_both = _rework(nfx_draft, nfx_report, nfx_manifest, reply={
        "subsections": [{"subsection_id": _nfx_sub.subsection_id, "title": _nfx_sub.title,
                         "paragraphs": [
                             {"paragraph_id": "p1", "aspect_ids": [nfx_aspect],
                              "sentences": [
                                  {"sentence_id": "t1", "text": _NFX_AMOUNT_SENTENCE,
                                   "citations": [nfx_amount_key]},
                                  {"sentence_id": "t2", "text": _NFX_RATIO_SENTENCE,
                                   "citations": [nfx_ratio_key]}]}]}],
        "gaps": [], "follow_up_needs": []})
    _nfx_left = [r.failure_reason for r in nfx_kept_both.check_report.records
                 if r.check_kind == "numeric_qualification" and r.verdict == "hard_error"]
    check(nfx_kept_both.outcome == "reworked" and bool(_nfx_left),
          "**反例**：占比原样留着 ⇒ 接口通过、读数却仍报那条 `numeric_qualification` 硬错"
          f"（实测 {_nfx_left}）")
    check(_NFX_RATIO_NUMBER in "".join(
        s.text for sub in nfx_kept_both.draft.subsections
        for p in sub.paragraphs for s in p.sentences),
          "反例的另一半：那一次返修**确实**留下了占比数字（不是读数在说另一件事）")

    # ---- 7.3 真实离线面（`ndc4`）上复核同一个等式：只读，缺目录则跳过 -----
    ndc4 = (Path(__file__).resolve().parent.parent / "evaluation" / "results"
            / "m930_3_cited_offline_ndc4_company_r1")
    if not (ndc4 / "cited_call_journal.json").is_file():
        skipped += 1
        details.append(f"SKIP §7.3：离线 run 目录不在场（{ndc4}）；本模块不因既存产物缺失而"
                       "失败，也不去别处找替代。")
    else:
        nfx_journal = json.loads((ndc4 / "cited_call_journal.json").read_text("utf-8"))
        nfx_wface = json.loads(nfx_journal["input"]["request_face"])
        nfx_rman = CW.CitedWriterInputManifest.from_dict(json.loads(
            (ndc4 / "cited_input_manifest.json").read_text("utf-8")))
        nfx_rdraft = CW.CitedProseDraft.from_dict(json.loads(
            (ndc4 / "cited_prose.json").read_text("utf-8"))["draft"])
        nfx_rreport = SC.check_cited_prose(draft=nfx_rdraft, manifest=nfx_rman)
        nfx_rface = CRW.build_cited_rework_request(
            draft=nfx_rdraft, check_report=nfx_rreport, manifest=nfx_rman).payload
        check(dict(nfx_rface["numeric_authorization"]) == nfx_wface["numeric_authorization"],
              "真实离线面上：返修请求面的 `numeric_authorization` 与**当初发给初稿**"
              "（`cited_call_journal.json` 里的 `request_face`）逐字相同")
        _wf = [c for sub in nfx_wface["subsections"] for c in sub["citable_columns"]]
        _rw = [c for sub in nfx_rface["subsections"] for c in sub["columns"]]
        check(len(_wf) == len(_rw)
              and all(a["aspect_id"] == b["aspect_id"]
                      and list(a["citable_fact_keys"]) == list(b["citable_fact_keys"])
                      and list(a["writable_fact_keys"]) == list(b["writable_fact_keys"])
                      for a, b in zip(_wf, _rw)),
              f"真实离线面上逐栏两根轴逐字相同（{len(_rw)} 栏）")
        _reg_rw = [c for c in _rw if len(c["citable_fact_keys"]) == 12]
        check(len(_reg_rw) == 1 and len(_reg_rw[0]["writable_fact_keys"]) == 9
              and len(nfx_wface["numeric_authorization"]["withheld"]) == 3,
              f"真实离线面的 **12 / 9 / 3** 与初稿面一致（实测登记 "
              f"{[len(c['citable_fact_keys']) for c in _rw]} / 可写 "
              f"{[len(c['writable_fact_keys']) for c in _rw]} / 撤回 "
              f"{len(nfx_wface['numeric_authorization']['withheld'])}）")
        check(len(nfx_rman.materials) == 65 and len(nfx_rman.facts) == 12,
              f"这一步没有动 Pack：{len(nfx_rman.materials)} 份材料 / "
              f"{len(nfx_rman.facts)} 条事实，与原登记一致")
        check(all(str(w["state"]) == SC.FACT_NUMERIC_DENOMINATOR_UNVERIFIED
                  for w in nfx_rface["numeric_authorization"]["withheld"]),
              "真实离线面上被撤回的 3 条原因码都是 `denominator_unverified`")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

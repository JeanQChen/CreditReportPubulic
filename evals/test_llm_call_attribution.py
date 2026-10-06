"""T26 · 调用归属与**事前准入**（`CROSS_DOCUMENT_JOINT_RETRIEVAL_PLAN.md` §0.3.1 / T26）。

本文件只回答一个问题：**一次真实 provider 尝试，在发出之前**是否已经被正确识别（归属到哪条
轴、哪个作用域）、正确计数（失败也占一次）、并且在超出已批准上限时被拒。

为什么必须单独成一支：研究阶段与写作阶段**共用同一份传输**（`llm.client`），但走**两条不同
的预算轴**。旧口径把「已批准的写作预算」当成全链总预算，于是①门装晚了（研究构建发生在门安装
之前），②研究 prompt 没登记归属（一旦门装早了就会在**第一次**研究调用处被拒），③SDK 的
`max_retries` 让一次记账可能对应三次真实 HTTP 请求。三条都是**结构**问题，只在现场数得出来：

  * **Y-1**：装了门 + 三个研究 prompt **未登记** + 政策已批准 ⇒ **第一次**研究调用即拒
    （证明「只改 `approved` 不够」）；
  * **Y-2**：装了门 + 已登记 + 研究轴**未批准** ⇒ **整轮拒绝、零请求**（不是跑到中途）；
  * **Y-3**：`client.max_retries == 0`，且一次 `chat_with_usage` 对应**恰一条**账本记录与
    **恰一次** `logs/llm` 落盘；
  * **Y-4**：写作轴的 62/144、40/100、0/0、2/6 与批准状态**逐值未变**（整轮 250 = 144 + 100
    + 0 + 6；**既有四项一字未动**，抬高的那 6 次恰好是最终句语义门 B 的结构上界），且研究
    尝试**不消耗**写作轴的这一整轮上限；
  * **Y-5**：`section_financial_v3` **不在**本轮链上——不进登记表、过门即拒、真实调用点只在
    遗留 `run_task` 路径。

跑法：`python -m evals.test_llm_call_attribution`
"""

from __future__ import annotations

import dataclasses
import json
import sys
from contextlib import contextmanager
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evaluation import run_m930_3_acceptance as ACC  # noqa: E402
from evals.test_llm_call_budget import _Provider  # noqa: E402
from harness import runtime as RT  # noqa: E402
from llm import budget as LB  # noqa: E402
from llm import client as LLC  # noqa: E402
from sections import claim_entailment_evaluator as CEE  # noqa: E402
from sections import pack_writer as PW  # noqa: E402

MODEL = ACC.APPROVED_BUDGET_MODEL
RESEARCH_PROMPTS = (RT.RESEARCH_ACTION_PROMPT_VERSION,
                    RT.RESEARCH_ANSWER_PROMPT_VERSION,
                    RT.RESEARCH_ENTAILMENT_PROMPT_VERSION)


def _research_call(*, prompt_version: str, topic_id: str = "t1",
                   max_tokens: int = 256) -> str:
    """在给定 topic 的作用域里发一次真实的 `chat_with_usage`（provider 是假体）。"""
    with LB.research_scope(topic_id):
        return LLC.chat_with_usage([{"role": "user", "content": "q"}],
                                   prompt_version=prompt_version, model=MODEL,
                                   max_tokens=max_tokens).text


def _writing_call(*, prompt_version: str, section_id: str = "company") -> str:
    with LB.section_scope(section_id):
        return LLC.chat_with_usage([{"role": "user", "content": "q"}],
                                   prompt_version=prompt_version, model=MODEL,
                                   max_tokens=256).text


def _policy_without_research_registration():
    """Y-1 的夹具：**只**登记四个写作调用点（研究 prompt 未登记），但研究轴已批准。

    这正是「只把 `approved` 改成 true」的形态：政策看起来已批准，但研究调用**归属不明**。
    """
    import dataclasses

    runner_policy = ACC._call_budget_policy()
    writing = {k: v for k, v in runner_policy.prompt_versions.items()
               if v not in LB.RESEARCH_CATEGORIES}
    return dataclasses.replace(runner_policy, prompt_versions=writing)


def _policy_with_unapproved_axis():
    """Y-2 的夹具：登记齐全、写作三类全批准，**只有研究轴**未批准。"""
    import dataclasses

    runner_policy = ACC._call_budget_policy()
    axis = dataclasses.replace(runner_policy.axes[0], approved=False,
                               basis="夹具：故意未批准的研究轴")
    return dataclasses.replace(runner_policy, axes=(axis,))


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

    runner_policy = ACC._call_budget_policy()

    # ------------------------------------------------------------------
    # 1. 正面：登记齐全时，归属 / 上限 / 记账三处都能到研究轴
    # ------------------------------------------------------------------
    check([runner_policy.category_of(v) for v in RESEARCH_PROMPTS]
          == list(LB.RESEARCH_CATEGORIES),
          "三个研究 prompt 必须各自登记到一个研究类别（`category_of` 可读回）")
    check(all(runner_policy.axis_of(c) == LB.AXIS_RESEARCH for c in LB.RESEARCH_CATEGORIES),
          "三个研究类别必须映射到**同一条**研究轴")
    caps = [runner_policy.cap_for(c) for c in LB.RESEARCH_CATEGORIES]
    check(len({id(c) for c in caps}) == 1 and isinstance(caps[0], LB.AxisCap),
          "三个研究类别必须共用**同一个** `AxisCap` 对象：各持一份 `CategoryCap` 等于把研究侧"
          "上限悄悄乘 3")
    # 逐 topic 上限的**期望值现场算出**（读冻结 Contract 投影），不在这里抄一份数字：
    # 「每 aspect 上界 × 该 topic 的 aspect 数」，整轴 = 各 topic 之和。
    _aspects = ACC._research_topic_aspects()
    _caps_by_topic = ACC._research_topic_caps(_aspects)
    _axis_total = sum(_caps_by_topic.values())
    _smallest_cap = min(_caps_by_topic.values())
    check(caps[0].max_attempts_per_scope == _smallest_cap
          and caps[0].max_attempts == _axis_total
          and caps[0].per_scope_max_attempts == _caps_by_topic,
          f"研究轴上限必须**逐 topic**：{_caps_by_topic}，整轮 {_axis_total}，"
          f"单值缺省取最紧的 {_smallest_cap}（实际 {caps[0].to_dict()}）")
    check(len(set(_caps_by_topic.values())) > 1,
          f"各 topic 的上限**不得全都相同**（实际 {_caps_by_topic}）："
          "一个共享值就是旧口径——它给 aspect 少的 topic 更多次/aspect，"
          "那不是上限而是按 topic 大小分配的配额")

    # ------------------------------------------------------------------
    # 2. 三研究类别**合计**吃一个 topic 的上限，不各得一份（Y-4 的一半）
    #
    # 这里用一个**没有登记**的 topic（`t1`）：它按单值缺省判，而缺省恰是最紧的那个 topic 上限。
    # 「三类别共用一轴」这条判据因此不受逐 topic 化影响——若有人把上限写成「每类各一份」，
    # 第 N+1 次就不会被拒。
    # ------------------------------------------------------------------
    ledger = LB.LLMCallBudget(runner_policy, enforce=True)
    with _Provider(), _install(ledger):
        sent = 0
        exceeded: LB.LLMCallBudgetExceeded | None = None
        for index in range(_smallest_cap + 1):
            prompt_version = RESEARCH_PROMPTS[index % len(RESEARCH_PROMPTS)]
            try:
                _research_call(prompt_version=prompt_version, topic_id="t1")
                sent += 1
            except LB.LLMCallBudgetExceeded as exc:
                exceeded = exc
                break
        check(sent == _smallest_cap,
              f"三个研究类别在同一个 topic 上合计只能发 {_smallest_cap} 次（未登记 scope 退回"
              f"单值缺省）（实际发出去 {sent} 次）")
        check(exceeded is not None and exceeded.limit_kind == "本 topic 上限"
              and exceeded.limit == _smallest_cap,
              f"第 {_smallest_cap + 1} 次必须被**事前**拒绝，且拒因逐字说明是本 topic 上限"
              f"（实际 {exceeded!r}）")
        check(ledger.count_axis(axis=LB.AXIS_RESEARCH) == _smallest_cap,
              "被拒的那一次不得占额度：账上恰是上限次")
        check(ledger.count_axis(axis=LB.AXIS_WRITING) == 0,
              "研究调用**不消耗**写作轴的账：研究跑满之后写作轴仍是 0")
        check(ledger.scope_kind_counts() == {LB.SCOPE_KIND_TOPIC: _smallest_cap},
              "研究尝试的作用域种类必须全部是 topic（一条都不能落成 section）")
        # 写作调用照常能发：两条轴的上限互不消耗。
        _writing_call(prompt_version=PW.NARRATION_PROMPT_VERSION)
        check(ledger.count_axis(axis=LB.AXIS_WRITING) == 1
              and ledger.count(category=LB.CATEGORY_NARRATION, section_id="company") == 1,
              "研究轴跑满不影响写作轴：同一本账上写作调用照常发出并逐节归集")

    # ------------------------------------------------------------------
    # 2b. **逐 topic 自派生**的正反例：小 topic 在自己的上限处被拒，而大 topic 不受影响
    #     （旧的共享值下这一条不成立：两者要么同生要么同死）
    # ------------------------------------------------------------------
    _small_topic = min(_caps_by_topic, key=lambda s: (_caps_by_topic[s], s))[len("topic:"):]
    _large_topic = max(_caps_by_topic, key=lambda s: (_caps_by_topic[s], s))[len("topic:"):]
    _small_n, _large_n = _caps_by_topic[f"topic:{_small_topic}"], \
        _caps_by_topic[f"topic:{_large_topic}"]
    per_topic_ledger = LB.LLMCallBudget(runner_policy, enforce=True)
    with _Provider(), _install(per_topic_ledger):
        refused: LB.LLMCallBudgetExceeded | None = None
        for index in range(_small_n + 1):
            try:
                _research_call(prompt_version=RESEARCH_PROMPTS[index % 3],
                               topic_id=_small_topic)
            except LB.LLMCallBudgetExceeded as exc:
                refused = exc
                break
        check(refused is not None and refused.limit == _small_n,
              f"最紧的 topic（{_small_topic}）必须在**它自己**的上限 {_small_n} 处被拒"
              f"（实际 {refused!r}）")
        # 同一个账本上，另一个 topic 继续发到自己的上限——它**不**受前一个 topic 的拒绝影响。
        sent_large = 0
        for index in range(_large_n):
            _research_call(prompt_version=RESEARCH_PROMPTS[index % 3], topic_id=_large_topic)
            sent_large += 1
        check(sent_large == _large_n
              and per_topic_ledger.count_axis(axis=LB.AXIS_RESEARCH,
                                              scope=f"topic:{_small_topic}") == _small_n
              and per_topic_ledger.count_axis(axis=LB.AXIS_RESEARCH,
                                              scope=f"topic:{_large_topic}") == _large_n,
              f"逐 topic 的上限必须**各自独立**：{_small_topic} 停在 {_small_n} 不影响 "
              f"{_large_topic} 发满 {_large_n}（实际 {per_topic_ledger.axis_counts()}）")

    # **整轴**上界。真实数字下 6 × 36 恰好等于 216，逐 topic 上限总是先命中——因此整轴那一条
    # 分支在真实数字上**永远不会被行使**。判据不能靠「反正没触发」成立：这里用一件**只有整轴
    # 上限会更小**的夹具政策，把那条分支单独跑出来。夹具只改研究轴的整轮上限，不动任何写作
    # 数字，也不动已批准语义。
    tight_axis = __import__("dataclasses").replace(
        runner_policy.axes[0], max_attempts=5, basis="夹具：整轮上限压到 5，专测整轴分支")
    tight_policy = __import__("dataclasses").replace(runner_policy, axes=(tight_axis,))
    axis_ledger = LB.LLMCallBudget(tight_policy, enforce=True)
    with _Provider(), _install(axis_ledger):
        axis_refusal: LB.LLMCallBudgetExceeded | None = None
        # 每个 topic 各发 2 次（远低于逐 topic 上限 36），5 次之后必然撞在**整轴**上限上。
        plan = [("t1", 2), ("t2", 2), ("t3", 2)]
        sent_axis = 0
        for topic_id, count in plan:
            for index in range(count):
                try:
                    _research_call(prompt_version=RESEARCH_PROMPTS[index % 3],
                                   topic_id=topic_id)
                    sent_axis += 1
                except LB.LLMCallBudgetExceeded as exc:
                    axis_refusal = axis_refusal or exc
        check(sent_axis == 5 and axis_ledger.count_axis(axis=LB.AXIS_RESEARCH) == 5,
              f"整轴上限 5 时，跨 topic 合计只能发 5 次（实际发出 {sent_axis} 次）")
        check(axis_refusal is not None and axis_refusal.limit_kind == "研究轴总上限"
              and axis_refusal.limit == 5,
              f"跨 topic 满额之后必须报**研究轴总上限**（而不是逐 topic 上限）"
              f"（实际 {axis_refusal!r}）")
        check(axis_ledger.refusals
              and all(r["axis"] == LB.AXIS_RESEARCH for r in axis_ledger.refusals),
              "每一次事前拒绝都必须在账上留下**归属到研究轴**的拒绝记录")

    # 真实数字下：每个 topic 各跑满**它自己**的上限 ⇒ 整轮恰是各上限之和（这就是整轴上限的
    # 来处，不是拍出来的）。
    full_ledger = LB.LLMCallBudget(runner_policy, enforce=False)
    with _Provider(), _install(full_ledger):
        topics = [s[len("topic:"):] for s in sorted(_caps_by_topic)]
        for topic_id in topics:
            for index in range(_caps_by_topic[f"topic:{topic_id}"]):
                _research_call(prompt_version=RESEARCH_PROMPTS[index % 3], topic_id=topic_id)
        check(full_ledger.count_axis(axis=LB.AXIS_RESEARCH) == _axis_total
              and set(full_ledger.section_counts()) == {f"topic:{t}" for t in topics},
              f"{len(topics)} 个 topic 各跑满自己的上限合计 {_axis_total} 次"
              f"（逐 topic 的账必须分开读得出）")

    # ------------------------------------------------------------------
    # 3. Y-1：研究 prompt 未登记 ⇒ **第一次**研究调用即拒
    # ------------------------------------------------------------------
    unregistered = _policy_without_research_registration()
    check(unregistered.approvable == (),
          "反例前置：这份夹具政策本身是「已全部批准」的——拒因必须来自**未登记归属**，"
          "而不是来自未批准（否则证明的是另一件事）")
    y1_ledger = LB.LLMCallBudget(unregistered, enforce=True)
    with _Provider() as provider1, _install(y1_ledger):
        y1_error: LB.LLMCallAttributionError | None = None
        try:
            _research_call(prompt_version=RESEARCH_PROMPTS[0])
        except LB.LLMCallAttributionError as exc:
            y1_error = exc
        check(y1_error is not None and "未登记归属" in str(y1_error),
              "Y-1：研究 prompt 未登记时，第一次研究调用必须被**事前**拒绝"
              f"（实际 {y1_error!r}）")
        check(provider1.calls == [],
              "Y-1：被拒的请求**没有**发到 provider（拒绝发生在发请求之前）")
        check(y1_ledger.attempts == [] and y1_ledger.refusals,
              "Y-1：归属不明的尝试既不入账也不占额度，但拒绝本身必须留痕")

    # ------------------------------------------------------------------
    # 4. Y-2：研究轴未批准 ⇒ 整轮拒绝、零请求（最早那一层，不是跑到中途）
    # ------------------------------------------------------------------
    unapproved = _policy_with_unapproved_axis()
    check(LB.AXIS_RESEARCH in unapproved.approvable,
          "反例前置：拿掉研究轴的批准之后，它必须出现在「尚未获批」清单里")
    y2_blocked_at_construction = False
    try:
        LB.LLMCallBudget(unapproved, enforce=True)
    except ValueError as exc:
        y2_blocked_at_construction = "未获批准" in str(exc)
    check(y2_blocked_at_construction,
          "Y-2：带着未批准的研究轴建**强制**门必须在构造期就拒（最早那一层：一个请求都不发，"
          "也不存在「先花掉已批准的写作额度、再撞在未批准的研究轴上」这种半轮真调用）")
    import dataclasses

    saved = ACC._call_budget_policy
    ACC._call_budget_policy = lambda: unapproved
    try:
        y2_refused = False
        try:
            ACC._call_budget(mode=ACC.MODE_REAL)
        except ACC.AcceptanceRefusal as exc:
            y2_refused = "尚未全部获批" in str(exc) and LB.AXIS_RESEARCH in str(exc)
        check(y2_refused,
              "Y-2：runner 的真实模式预检必须因此整轮拒绝，并逐字点名缺哪条轴的批准")
    finally:
        ACC._call_budget_policy = saved

    # ------------------------------------------------------------------
    # 5. Y-3：SDK 无静默重试；一次调用 = 一条账 + 一次落盘
    # ------------------------------------------------------------------
    saved_key, saved_client = LLC.DEEPSEEK_API_KEY, LLC._client
    LLC.DEEPSEEK_API_KEY = "test-key-not-used"
    LLC._client = None
    try:
        client = LLC.get_client()
        check(client.max_retries == 0,
              f"Y-3：SDK 必须 `max_retries=0`（缺省 2 次静默重试会让一次记账对应三次真实 HTTP "
              f"请求；实际 {client.max_retries}）")
    finally:
        LLC.DEEPSEEK_API_KEY, LLC._client = saved_key, saved_client
    y3_ledger = LB.LLMCallBudget(runner_policy, enforce=False)
    y3_provider = _Provider()
    with y3_provider as provider3, _install(y3_ledger):
        _research_call(prompt_version=RESEARCH_PROMPTS[1], topic_id="t3")
        logs = sorted(Path(y3_provider.logs_dir).glob("*.jsonl"))
        check(len(provider3.calls) == 1 and len(y3_ledger.attempts) == 1 and len(logs) == 1,
              f"Y-3：一次 `chat_with_usage` 必须对应恰一次 provider 调用、恰一条账、恰一条 "
              f"`logs/llm` 记录（实际 {len(provider3.calls)}/{len(y3_ledger.attempts)}/"
              f"{len(logs)}）")
        record = json.loads(logs[0].read_text(encoding="utf-8").strip())
        check(record["call_id"] == y3_ledger.attempts[0].call_id
              and record["prompt_version"] == RESEARCH_PROMPTS[1],
              "Y-3：落盘记录与账本必须指向**同一个** call_id 与同一个 prompt 版本")
    # 失败尝试照样占一次，且**不进** logs/llm（日志只在成功返回之后写）——这正是账本必须
    # 独立存在的原因。
    failing_provider = _Provider(explode=True)
    with failing_provider, _install(y3_ledger):
        try:
            _research_call(prompt_version=RESEARCH_PROMPTS[2], topic_id="t3")
        except RuntimeError:
            pass
        error_attempt = [a for a in y3_ledger.attempts if a.status == LB.STATUS_ERROR]
        check(len(error_attempt) == 1 and error_attempt[0].axis == LB.AXIS_RESEARCH
              and error_attempt[0].section_id == "topic:t3",
              "Y-3：失败的尝试照样占一次，且归属（轴 + topic 作用域）在记账时就已定")
        check(sorted(Path(failing_provider.logs_dir).glob("*.jsonl")) == [],
              "Y-3：失败尝试不进 `logs/llm`（日志只在成功返回后落盘）——"
              "所以「账本记了成功、日志里没有」才是一条真判据")

    # ------------------------------------------------------------------
    # 6. 归属错配：研究调用落在节作用域 / 写作调用落在 topic 作用域 —— 一律事前拒
    # ------------------------------------------------------------------
    mismatch_ledger = LB.LLMCallBudget(runner_policy, enforce=True)
    with _Provider() as provider6, _install(mismatch_ledger):
        no_scope = False
        try:
            LLC.chat_with_usage([{"role": "user", "content": "q"}],
                                prompt_version=RESEARCH_PROMPTS[0], model=MODEL,
                                max_tokens=256)
        except LB.LLMCallAttributionError as exc:
            no_scope = "没有作用域" in str(exc)
        check(no_scope,
              "研究调用**没有**作用域时必须事前拒：「归属不清」不得被记成「某一节的研究调用」")
        as_section = False
        try:
            _writing_call(prompt_version=RESEARCH_PROMPTS[0], section_id="company")
        except LB.LLMCallAttributionError as exc:
            as_section = "没有 topic 作用域" in str(exc)
        check(as_section,
              "研究调用落在**节**作用域里必须事前拒（研究按 topic 计量；放行会得到一个"
              "读起来像写作侧的数字）")
        as_topic = False
        with LB.research_scope("t9"):
            try:
                LLC.chat_with_usage([{"role": "user", "content": "q"}],
                                    prompt_version=PW.NARRATION_PROMPT_VERSION, model=MODEL,
                                    max_tokens=256)
            except LB.LLMCallAttributionError as exc:
                as_topic = "topic 作用域" in str(exc)
        check(as_topic,
              "写作调用落在 **topic** 作用域里必须事前拒（写作按节计量；放行会让「这一节花了"
              "多少次」少算）")
        check(provider6.calls == [] and mismatch_ledger.attempts == [],
              "三次错配都没有发到 provider、也没有入账：错配的账比没有账更糟")

    # ------------------------------------------------------------------
    # 7. `chat_stream` 没有旁路：装了门就必须归属，否则在**调用时**即拒
    # ------------------------------------------------------------------
    with _Provider() as provider7, _install(LB.LLMCallBudget(runner_policy, enforce=True)):
        stream_refused = False
        try:
            LLC.chat_stream([{"role": "user", "content": "q"}], model=MODEL, max_tokens=64)
        except LB.LLMCallAttributionError as exc:
            stream_refused = "未登记归属" in str(exc)
        check(stream_refused and provider7.calls == [],
              "流式入口不得成为预算门的旁路：缺 `prompt_version` 时必须在**调用时**就拒"
              "（而不是等第一次 `next()` 才暴露）")

    # ------------------------------------------------------------------
    # 8. Y-4：写作轴的数字与批准状态逐值未变；整轮上限只是写作轴的上限
    # ------------------------------------------------------------------
    # **既有四项一字未动**：62/144、40/100、0/0 是上一批的读数，本批只**新增**最终句语义门 B
    # 的类别（2/6），整轮上限因此 244 → 250。这不是「放宽」：抬高的 6 次恰好是新调用点的结构
    # 上界（`_narration_structural_bound()` 逐项推出，预检再比对一次），任何既有类别的上限
    # 都没有变大，缺一项也照样为红。
    writing_caps = {c.category: c for c in runner_policy.categories}
    check(writing_caps[LB.CATEGORY_NARRATION].max_attempts_per_section == 62
          and writing_caps[LB.CATEGORY_NARRATION].max_attempts_total == 144
          and writing_caps[LB.CATEGORY_CLAIM_ENTAILMENT].max_attempts_per_section == 40
          and writing_caps[LB.CATEGORY_CLAIM_ENTAILMENT].max_attempts_total == 100
          and writing_caps[LB.CATEGORY_SECTION_LLM_EVALUATOR].max_attempts_per_section == 0
          and writing_caps[LB.CATEGORY_SECTION_LLM_EVALUATOR].max_attempts_total == 0
          and runner_policy.total_max_attempts == 250,
          "Y-4：写作轴的 62/144、40/100、0/0 与整轮 250（= 144 + 100 + 0 + 6）必须逐值未变"
          f"（实际 {[c.to_dict() for c in runner_policy.categories]} / "
          f"{runner_policy.total_max_attempts}）")
    check(runner_policy.axes[0].max_attempts == _axis_total
          and _axis_total != runner_policy.total_max_attempts,
          f"写作轴的整轮上限只是**写作轴**的上限：研究轴的 {_axis_total} 独立成条，"
          "不并进这个数")
    # 报告侧：研究上限必须单独成行，且不得出现「写作轴整轮上限是全部 provider 调用总上限」的表述。
    runner_src = (REPO / "evaluation" / "run_m930_3_acceptance.py").read_text(encoding="utf-8")
    check("research_axis" in runner_src
          and "_research_axis_report_block" in runner_src
          and runner_src.count("_research_axis_report_block(") >= 3,
          "研究侧上限必须由 `_research_axis_report_block` **单独成行**（一个实现、多处引用），"
          "而不是与写作轴挤在同一个数字里")
    check("**不是全部 provider 调用的总上限**" in runner_src
          or "不是全部 provider 调用总上限" in runner_src,
          "报告必须逐字写明写作轴的整轮上限**不是**全部 provider 调用的总上限")

    # ------------------------------------------------------------------
    # 9. Y-5：`section_financial_v3` 不在本轮链上
    # ------------------------------------------------------------------
    check("section_financial_v3" not in runner_policy.prompt_versions,
          "Y-5：`section_financial_v3` 不得进本轮登记表（它属遗留 `run_task` 路径，"
          "本链的财务相位是确定性的、无 `chat_with_usage`）")
    with _Provider() as provider9, _install(LB.LLMCallBudget(runner_policy, enforce=True)):
        financial_refused = False
        try:
            _writing_call(prompt_version="section_financial_v3", section_id="financial")
        except LB.LLMCallAttributionError:
            financial_refused = True
        check(financial_refused and provider9.calls == [],
              "Y-5：即便有人拿 `section_financial_v3` 去发请求，门也必须事前拒"
              "（未登记归属 ⇒ 不会悄悄绕过预算）")
    worker_src = (REPO / "sections" / "financial_worker.py").read_text(encoding="utf-8")
    check(worker_src.count("chat_with_usage") == 1,
          f"Y-5（源代码实读）：`sections/financial_worker.py` 里 `chat_with_usage` 必须只有 1 处"
          f"（实际 {worker_src.count('chat_with_usage')} 处）——多出来的那处即「本链偷偷调 LLM」")
    backbone_body = _function_body(worker_src, "def run_backbone_financial_phase")
    check(backbone_body is not None and "chat_with_usage" not in backbone_body,
          "Y-5（源代码实读）：`run_backbone_financial_phase` 本体里必须**没有** "
          "`chat_with_usage`（本链的财务相位是确定性的）——"
          "判定按**函数本体**（到下一个顶层 `def`/`class` 为止），不是按固定字数截窗")

    # ------------------------------------------------------------------
    # 10. 门的安装时机：先 install 再 `with RealEnvironment(...)`（结构判据）
    # ------------------------------------------------------------------
    execute_body = _function_body(runner_src, "def execute_run(")
    check(execute_body is not None,
          "结构判据的前提：`execute_run` 必须可定位（定位不到就判红，不静默通过）")
    execute_body = execute_body or ""
    env_at = execute_body.find("with RealEnvironment(")
    install_at = execute_body.find("LB.install(")
    uninstall_at = execute_body.find("LB.uninstall()")
    check(0 <= install_at < env_at,
          f"门必须在 `with RealEnvironment(...)` **之前**安装（研究构建就发生在环境构造里；"
          f"装晚了研究阶段完全不受门约束）：install@{install_at} 必须早于 env@{env_at}")
    check(uninstall_at > env_at,
          "门必须在 `finally` 里卸载（进入环境失败也要卸载，并保留已经记下的账）")
    uninstall_note = execute_body[max(0, uninstall_at - 500):uninstall_at + 200]
    check("研究期间已经发生的尝试账本留在" in uninstall_note
          and "随门一起消失" in uninstall_note,
          "卸载门的地方必须逐字写明：研究期间已发生的尝试**留在账本上**，"
          "而不是随门一起消失（否则被拒的那一轮在报告里看不出「研究已经发出过几次」）")
    check("_assert_research_axis_mirrors_research_policy" in runner_src
          and runner_src.count("_research_axis_mirror(") >= 2,
          "研究轴与现场研究预算的**镜像**必须能被复算（同一个函数既做断言又做报告读数），"
          "不得靠人记住两处数字一致")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


def _function_body(source: str, header: str) -> str | None:
    """取 `header` 这个顶层 def 的**函数本体**（到下一个顶层 `def `/`class ` 为止）。

    按固定字数截窗会把**下一个**函数一起圈进来——「本函数里没有 X」这种判据就变成假的。
    这里按行首的顶层定义边界切，边界找不到时返回 None（返回 None 的判据由调用方判红）。
    """
    start = source.find(header)
    if start == -1:
        return None
    lines = source[start:].split("\n")
    body = [lines[0]]
    for line in lines[1:]:
        if (line.startswith("def ") or line.startswith("class ")
                or line.startswith("async def ")):
            break
        body.append(line)
    return "\n".join(body)


def _install(ledger: LB.LLMCallBudget):
    """装门并把「离开作用域时还原」包成上下文管理器（不遮蔽已有的门）。"""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        previous = LB.install(ledger)
        try:
            yield ledger
        finally:
            LB.uninstall()
            if previous is not None:
                LB.install(previous)

    return _ctx()


if __name__ == "__main__":
    outcome = main()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(outcome, ensure_ascii=False, indent=2))

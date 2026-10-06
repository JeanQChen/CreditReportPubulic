"""M930-3 任务二反例集：**所有真实请求共用的**事前调用预算门。

本模块只测两样东西：`llm.budget` 这道门本身，以及 `evaluation.run_m930_3_acceptance` 里的调用
计量（`_reconcile_calls` / `_gate_a7_call_budget` / `_write_refusal`）。它**不**发任何真实请求：

* provider 是本地假体（计次、可注入传输失败），日志目录指向临时目录；
* 「请求有没有发到 provider」由假体的调用次数回答——这正是「事前拦截」与「事后审计」的区别。

判据里最容易假绿的五条各有一组反例：

1. **超限请求未触达 provider**：第 N+1 次在进入 provider 之前就被拒，而不是「发了再记超限」；
2. **失败尝试照样占一次**：传输失败也占额度，`status_counts` 必须如实区分「发起过 → 失败」与
   「成功返回」；
3. **归属是 (类别, 节) 双轴**：未登记的 `prompt_version`、缺节作用域、模型不符、单次输出超
   上限，一律在发请求之前拒；
4. **共用客户端不重复计数**：蕴含门复用叙述客户端时，一次蕴含只有一条记录（真实模式下
   `LlmEntailmentClient` 自己没有 `calls`，蕴含那一次已经记在叙述客户端上）；
5. **三个计数视图逐项守恒**：账本 / 客户端记录 / 各节计量必须相等——旧口径的各节计量漏掉门后
   组织那一次，差额是可证的（等于走模型组织的节数），不是「大概少了几次」。
6. **证据产物本身也要能落盘**：门判绿不等于产物可写——内部句柄（`Path`）混进 evidence 会让
   `acceptance_report.json` 在 `dumps` 时炸掉整个 run（实测踩到过）；同理，账本产物里同一批
   尝试只能有一处，否则「逐项对账」的产物自己给出两个数。
7. **事前拒绝 ≠ 本节失败**：逐节驱动按节吞异常（别的节继续），但预算拒绝必须穿出去整轮停止；
   若被当成节内失败记一笔再继续，就正好会一路撞同一个上限、并在真实模式下持续烧已批准的额度。

跑法（无管道/无重定向）：`PYTHONIOENCODING=utf-8 python -m evals.test_llm_call_budget`
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import run_m930_3_acceptance as ACC
from harness import runtime as RT
from harness import source_manifest as SM
from llm import budget as LB
from llm import client as LLC
from sections import claim_entailment_evaluator as CEE
from sections import final_sentence_fidelity as FSF
from sections import narrative_organizer as NO
from sections import pack_writer as PW
from sections import rules_evaluator as RE

#: 夹具用的已批准模型名：门只做「声明模型 == 批准模型」的字符串比较，不联网。
MODEL = "fixture-model"
#: 一次调用的最小合法入参（假体不看内容，只记次数）。
MESSAGES = [{"role": "user", "content": "x"}]


class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Usage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _Response:
    def __init__(self, text: str = "ok", stop_reason: str = "stop") -> None:
        self.content = [_Block(text)]
        self.usage = _Usage(11, 5)
        self.stop_reason = stop_reason


class _FakeProvider:
    """假 provider：每次 `messages.create` 记一次入参；可选在调用点抛传输失败/回报截断。

    它回答的问题只有一个——**是否有请求真的发到了 provider**。`calls` 的长度就是「客户端实际
    发起的请求尝试」，与「成功返回」「归属到哪一节」是三个不同的量。

    `stop_reason`：让假 provider 如实回报 `length` / `max_tokens`（截断）——这是**一次失败的
    调用**，不是一次短一点的合格输出。
    """

    class _Messages:
        def __init__(self, owner: "_FakeProvider") -> None:
            self._owner = owner

        def create(self, **kwargs):
            self._owner.calls.append(kwargs)
            if self._owner.explode:
                raise RuntimeError("injected transport failure")
            return _Response(self._owner.text, self._owner.stop_reason)

    def __init__(self, *, explode: bool = False, stop_reason: str = "stop",
                 text: str = "ok") -> None:
        self.calls: list[dict] = []
        self.explode = explode
        self.stop_reason = stop_reason
        self.text = text
        self.messages = _FakeProvider._Messages(self)


class _Provider:
    """把 `llm.client` 的 provider 与日志目录换成临时的（离开作用域逐项还原）。"""

    def __init__(self, *, explode: bool = False, stop_reason: str = "stop",
                 text: str = "ok") -> None:
        self.provider = _FakeProvider(explode=explode, stop_reason=stop_reason, text=text)
        self._saved = None
        self._tmp = None

    def __enter__(self) -> _FakeProvider:
        self._tmp = tempfile.TemporaryDirectory(prefix="m930-3-llm-logs-")
        self._saved = (LLC.get_client, LLC.LOGS_DIR, LLC._client)
        LLC.get_client = lambda: self.provider
        LLC.LOGS_DIR = Path(self._tmp.name)
        return self.provider

    def __exit__(self, *exc) -> None:
        LLC.get_client, LLC.LOGS_DIR, LLC._client = self._saved
        self._tmp.cleanup()

    @property
    def logs_dir(self) -> Path:
        return Path(self._tmp.name)


def _policy(*, narration=(2, 6), entailment=(5, 10), evaluator=(5, 10),
            final_sentence=(2, 6), total=12, approved_model=MODEL) -> LB.CallBudgetPolicy:
    """夹具政策：**全部类别都已批准**（`enforce=True` 的前提，见 `LLMCallBudget`）。

    「本类本轮不许发」用上限 0 表达（`evaluator=(0, 0)`），而不是「不批准」：0 是一个已批准的
    决定，有出处；不批准则整轮不得以强制模式运行（构造期就拒）。

    `max_output_tokens` 一律 None：预算政策里**没有**费用型 token 限额（夹具与 runner 同口径）。
    """

    def cap(category: str, spec, basis: str) -> LB.CategoryCap:
        if spec is None:
            return LB.CategoryCap(category=category, max_attempts_per_section=None,
                                  max_attempts_total=None, max_output_tokens=None,
                                  approved=False, basis=f"{basis}（未获批准）")
        return LB.CategoryCap(category=category, max_attempts_per_section=spec[0],
                              max_attempts_total=spec[1], max_output_tokens=None,
                              approved=True, basis=basis)

    return LB.CallBudgetPolicy(
        policy_version="fixture-budget-1", approved_model=approved_model,
        categories=(
            cap(LB.CATEGORY_NARRATION, narration, "夹具：已批准叙述类"),
            cap(LB.CATEGORY_CLAIM_ENTAILMENT, entailment, "夹具：逐候选蕴含"),
            cap(LB.CATEGORY_SECTION_LLM_EVALUATOR, evaluator, "夹具：可选章级评估"),
            cap(LB.CATEGORY_FINAL_SENTENCE_FIDELITY, final_sentence,
                "夹具：最终句语义门 B"),
        ),
        prompt_versions=_PROMPT_VERSIONS,
        total_max_attempts=total, total_approved=True)


#: 夹具的归属表：与 runner 的登记表同形（四个类别、五个真实 prompt 版本——叙述与组织同属
#: 叙述类，最终句语义门 B 是**自己的一类**）。夹具缺哪一项，用到那一项的那一组就会以
#: 「未登记归属类别」整组失败——那正是归属表存在的意义，因此这里必须与 runner 同步演进。
_PROMPT_VERSIONS = {
    PW.NARRATION_PROMPT_VERSION: LB.CATEGORY_NARRATION,
    NO.NARRATIVE_ORGANIZER_PROMPT_VERSION: LB.CATEGORY_NARRATION,
    CEE.CLAIM_ENTAILMENT_PROMPT_VERSION: LB.CATEGORY_CLAIM_ENTAILMENT,
    RE.NARRATIVE_EVALUATOR_PROMPT: LB.CATEGORY_SECTION_LLM_EVALUATOR,
    FSF.FINAL_SENTENCE_FIDELITY_PROMPT_VERSION: LB.CATEGORY_FINAL_SENTENCE_FIDELITY,
}


def _partly_unapproved_policy() -> LB.CallBudgetPolicy:
    """**若有人把「未获批准」当成不设限**：只批准叙述类，蕴含与可选评估未获批。

    这种政策在真实模式下必须**整轮被拒**（构造期就拒，一个请求都不发）——注意本轮 runner 的
    真实政策不是这个形状：它把四类都写成已批准，其中可选章级评估用上限 0 表达
    「本轮一次都不发」（0 是一个已批准的决定，有出处；未批准则整轮不得以强制模式运行）。
    本夹具只用来钉住 fail-closed 的最早那一层；在离线记账模式下它照记（`enforce=False`）。
    """
    return LB.CallBudgetPolicy(
        policy_version="fixture-partly-unapproved-1", approved_model=MODEL,
        categories=(
            LB.CategoryCap(category=LB.CATEGORY_NARRATION, max_attempts_per_section=2,
                           max_attempts_total=6, max_output_tokens=None, approved=True,
                           basis="夹具：已批准叙述类"),
            LB.CategoryCap(category=LB.CATEGORY_CLAIM_ENTAILMENT, max_attempts_per_section=None,
                           max_attempts_total=None, max_output_tokens=None, approved=False,
                           basis="夹具：未获批准"),
            LB.CategoryCap(category=LB.CATEGORY_SECTION_LLM_EVALUATOR,
                           max_attempts_per_section=None, max_attempts_total=None,
                           max_output_tokens=None, approved=False, basis="夹具：未获批准"),
        ),
        prompt_versions=_PROMPT_VERSIONS, total_max_attempts=6, total_approved=True)


class _NS:
    """只读命名空间替身（对账只按属性名读取运行现场）。"""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


def _call(*, prompt_version: str, section_id: str, max_tokens: int = 100,
          model: str = MODEL) -> str:
    """在给定节的归属作用域里发一次真实 `chat_with_usage`（provider 是假体）。"""
    with LB.section_scope(section_id):
        return LLC.chat_with_usage(MESSAGES, prompt_version=prompt_version,
                                   model=model, max_tokens=max_tokens).text


@contextmanager
def _scoped_ledger(ledger: LB.LLMCallBudget):
    """安装账本，离开作用域还原（`install` 返回的是被顶掉的那一个）。"""
    previous = LB.install(ledger)
    try:
        yield ledger
    finally:
        if previous is None:
            LB.uninstall()
        else:
            LB.install(previous)


def _count_in(counts, key: str) -> int:
    """账本的分组计数：值可能是整数，也可能是再按一层分组的映射——两种都如实求和。"""
    value = (counts or {}).get(key)
    if isinstance(value, Mapping):
        return sum(int(v) for v in value.values())
    return int(value or 0)


def _narrate(client, prompt_version: str,
             model_policy: str = PW.MODEL_POLICY_PROVIDER_DEFAULT):
    """发一次叙述调用（走生产适配器 `PW.LlmNarrationClient.narrate`）。"""
    return client.narrate(messages=MESSAGES, system="s", prompt_version=prompt_version,
                          model_policy=model_policy)


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

    def check_problem(rows, needle: str, msg: str) -> None:
        joined = "｜".join(str(r) for r in rows)
        check(needle in joined, f"{msg}（判据须命中 {needle!r}；实际 {joined[:220]!r}）")

    # ------------------------------------------------------------------
    # 1. 事前拦截：超限请求未触达 provider
    # ------------------------------------------------------------------
    # 本节上限 1、本类总上限 3：本节那一层用来测「第 N+1 次未触达 provider」，本类那一层留出
    # 余量给本组末尾的**反向反例**（很大的 max_tokens 不是「超预算」，必须照常发出）。
    policy = _policy(narration=(1, 3))
    ledger = LB.LLMCallBudget(policy, enforce=True)
    with _Provider() as provider:
        assert LB.install(ledger) is None
        try:
            text = _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="company")
            check(text == "ok" and len(provider.calls) == 1,
                  "预算门正向：未超限的第 1 次必须照常发出并返回")
            refused = None
            try:
                _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="company")
            except LB.LLMCallBudgetExceeded as exc:
                refused = exc
            check(refused is not None, "第 2 次（本节上限 1）必须被拒")
            check(len(provider.calls) == 1,
                  f"超限请求**不得触达 provider**（实际 provider 收到 {len(provider.calls)} 次）")
            check(refused is not None and "请求在发出之前被拒绝" in str(refused),
                  f"拒绝理由必须写明「请求未发出」（实际 {refused!r}）")
            check(refused is not None and refused.limit_kind == "本节上限",
                  f"拒绝必须点名是哪一层上限（实际 {getattr(refused, 'limit_kind', None)!r}）")
            check(len(ledger.attempts) == 1 and len(ledger.refusals) == 1,
                  "被拒的那一次只进 refusals，**不得**计入 attempts（否则调用数会虚高）")
            check(ledger.refusals[0]["attempt_ordinal_if_sent"] == 2,
                  "拒绝记录必须写明「若发出会是第几次」，供复核者复原现场")

            # 「本类本轮不许发」用**上限 0** 表达（已批准的决定），而不是不批准整轮：
            # 后者在构造期就被拒，连门都建不起来（见本组末尾）。
            provider.calls.clear()
            zero = LB.LLMCallBudget(_policy(evaluator=(0, 0)), enforce=True)
            with _scoped_ledger(zero):
                zero_refused = None
                try:
                    _call(prompt_version=RE.NARRATIVE_EVALUATOR_PROMPT, section_id="company")
                except LB.LLMCallBudgetExceeded as exc:
                    zero_refused = exc
                check(zero_refused is not None and zero_refused.limit_kind == "本节上限",
                      f"上限 0 的类别必须一次都发不出去（实际 {zero_refused!r}）")
                check(zero.category_counts() == {} and zero.count() == 0,
                      "被拒的尝试不占额度：上限 0 的类别账上仍是 0 次尝试")

            # 未登记的 prompt_version / 缺节作用域 / 模型不符：归属不清一律在事前拒。
            provider.calls.clear()
            for label, exc_type, call in (
                ("未登记的 prompt_version",
                 LB.LLMCallAttributionError,
                 lambda: _call(prompt_version="unknown_prompt@rev", section_id="company")),
                ("缺 section 作用域", LB.LLMCallAttributionError,
                 lambda: LLC.chat_with_usage(MESSAGES, prompt_version=PW.NARRATION_PROMPT_VERSION,
                                             model=MODEL, max_tokens=100)),
                ("模型不在批准范围", LB.LLMCallBudgetExceeded,
                 lambda: _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="financial",
                               model="other-model")),
            ):
                caught = None
                try:
                    call()
                except LB.LLMCallBudgetError as exc:  # noqa: PERF203
                    caught = exc
                check(isinstance(caught, exc_type),
                      f"{label}：必须在事前被拒（实际 {caught!r}）")
            check(not provider.calls,
                  f"归属不清的三种请求都不得触达 provider（实际 {len(provider.calls)} 次）")
            check(len(ledger.refusals) == 4,
                  f"四次事前拒绝都必须留在账上（1 次本节上限 + 3 次归属不清；"
                  f"实际 {len(ledger.refusals)}）")

            # 反向反例：预算政策里**没有**费用型 output token 限额。一个很大的 max_tokens
            # **不是**「超预算」，必须照常发出——容量是技术参数，按阶段给足（见 runner 的
            # `output_capacity`）。若这里被拒，说明有人把成本上限偷偷塞回了调用门。
            huge = _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="financial",
                         max_tokens=999_999)
            check(huge == "ok" and len(provider.calls) == 1,
                  "费用型 token 限额不得存在：很大的 max_tokens 必须照常发出")
            check(ledger.attempts[-1].max_tokens == 999_999,
                  f"实际发出的 max_tokens 必须如实落账（实际 "
                  f"{ledger.attempts[-1].max_tokens!r}）")
            check(len(ledger.refusals) == 4,
                  "被接受的很大 max_tokens 不得产生任何 refusal")
            check(ledger.count() == 2,
                  "拒绝不占额度：attempts 只有最初那一次与被接受的这一次")
        finally:
            LB.uninstall()

    # 未批准的量不得以强制模式执行：这是**构造期**的硬约束，比请求期更早。若留到请求期，
    # 只批准了叙述类的政策会先把已批准的额度花光，再卡在蕴含那一次上（半轮真调用）。
    try:
        LB.LLMCallBudget(_partly_unapproved_policy(), enforce=True)
        check(False, "有未批准类别时以 enforce=True 建门必须直接 ValueError")
    except ValueError as exc:
        check("未获批准" in str(exc), f"构造期拒绝必须点明未批准的量（实际 {exc}）")
    # 同一份政策在记账模式（离线替身）下必须能建起来：离线不花真实额度，账要照记。
    check(LB.LLMCallBudget(_partly_unapproved_policy(), enforce=False).enforce is False,
          "同一份未全批准的政策在 enforce=False 下必须可用（离线账本照记）")

    # ------------------------------------------------------------------
    # 2. 失败尝试照样占一次（「失败不计数」在结构上不可表达）
    # ------------------------------------------------------------------
    ledger = LB.LLMCallBudget(_policy(narration=(2, 2)), enforce=True)
    with _Provider(explode=True) as provider:
        assert LB.install(ledger) is None
        try:
            for _ in range(2):
                try:
                    _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="company")
                except RuntimeError:
                    pass
            check(len(provider.calls) == 2,
                  "两次传输失败确实到达了 provider（因此它们占额度）")
            check(ledger.count() == 2 and ledger.status_counts() == {"error": 2},
                  f"失败的尝试必须已记账并如实标 error（实际 {ledger.status_counts()}）")
            third = None
            try:
                _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="company")
            except LB.LLMCallBudgetExceeded as exc:
                third = exc
            check(third is not None and third.limit_kind == "本节上限",
                  f"失败占额度后第 3 次必须被拒（实际 {third!r}）")
            check(len(provider.calls) == 2, "被拒的第 3 次不得触达 provider")
            check(ledger.status_counts() == {"error": 2},
                  "被拒不计入 attempts：状态分布仍是两次失败")
        finally:
            LB.uninstall()

    # ------------------------------------------------------------------
    # 3. 类别 / 节的归属双轴（enforce=False 只记账，用来核对归属）
    # ------------------------------------------------------------------
    ledger = LB.LLMCallBudget(_policy(), enforce=False)
    with _Provider() as provider:
        assert LB.install(ledger) is None
        try:
            _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="company")
            _call(prompt_version=NO.NARRATIVE_ORGANIZER_PROMPT_VERSION, section_id="company")
            _call(prompt_version=CEE.CLAIM_ENTAILMENT_PROMPT_VERSION, section_id="financial")
            check(ledger.section_counts() == {"company": {"narration": 2},
                                              "financial": {"claim_entailment": 1}},
                  f"归属必须按 (类别, 节) 双轴记录（实际 {ledger.section_counts()}）")
            check(ledger.category_counts() == {"narration": 2, "claim_entailment": 1},
                  f"类别合计必须与逐节记录一致（实际 {ledger.category_counts()}）")
            check(len(provider.calls) == 3, "三次未超限的请求都要真的发出去")
        finally:
            LB.uninstall()

    ledger = LB.LLMCallBudget(_policy(narration=(5, 5), total=1), enforce=True)
    with _Provider() as provider:
        assert LB.install(ledger) is None
        try:
            _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="company")
            run_refused = None
            try:
                _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="financial")
            except LB.LLMCallBudgetExceeded as exc:
                run_refused = exc
            check(run_refused is not None and run_refused.limit_kind == "整轮总上限",
                  f"换一节也绕不过整轮总上限（实际 {run_refused!r}）")
            check(len(provider.calls) == 1, "整轮超限的那一次不得触达 provider")
        finally:
            LB.uninstall()

    # ------------------------------------------------------------------
    # 4. 有界重写 / 可选章级评估同样受门约束
    # ------------------------------------------------------------------
    # 有界重写（`MAX_FOLLOW_UP_ROUNDS=1`）= **同一节**里再发一次门前提案：它不另开一类，
    # 因此按已批准口径「每节 2 次」时，那一节第 3 次在事前被拒并如实上报。
    ledger = LB.LLMCallBudget(_policy(narration=(2, 6)), enforce=True)
    with _Provider() as provider:
        assert LB.install(ledger) is None
        try:
            for _ in range(2):
                _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="company")
            rewrite = None
            try:
                _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="company")
            except LB.LLMCallBudgetExceeded as exc:
                rewrite = exc
            check(rewrite is not None and rewrite.limit_kind == "本节上限",
                  f"有界重写的第 3 次请求必须受门约束（实际 {rewrite!r}）")
            check(len(provider.calls) == 2, "被拒的重写轮次不得触达 provider")
            # 别的节不受牵连：本类总上限（6）还没到，financial 仍可发。
            _call(prompt_version=NO.NARRATIVE_ORGANIZER_PROMPT_VERSION, section_id="financial")
            check(len(provider.calls) == 3,
                  "本节到顶不牵连别的节（逐节与逐类是两层上限）")
        finally:
            LB.uninstall()

    # 可选章级评估（`allow_llm_evaluator`）是**另一类**：它有自己的一本账，不得搭叙述类的便车。
    # 本轮 runner 未批准它 ⇒ 真实模式整轮拒绝（第 9 组）；一旦批准，它仍受自己的逐节/逐类上限约束。
    ledger = LB.LLMCallBudget(_policy(narration=(2, 6), evaluator=(1, 1)), enforce=True)
    with _Provider() as provider:
        assert LB.install(ledger) is None
        try:
            _call(prompt_version=RE.NARRATIVE_EVALUATOR_PROMPT, section_id="company")
            second = None
            try:
                _call(prompt_version=RE.NARRATIVE_EVALUATOR_PROMPT, section_id="company")
            except LB.LLMCallBudgetExceeded as exc:
                second = exc
            check(second is not None and second.limit_kind == "本节上限",
                  f"获批后的可选评估也受逐节上限（1）约束（实际 {second!r}）")
            check(len(provider.calls) == 1, "超限的评估调用不得触达 provider")
            # 逐类上限是**各自**的：评估类到顶不得掐掉叙述类在同一节的额度。
            _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="company")
            check(len(provider.calls) == 2,
                  "评估类到顶不得影响叙述类在同一节的额度（两类各有一本账）")
        finally:
            LB.uninstall()

    # ------------------------------------------------------------------
    # 5. 共用客户端不重复计数（真实模式下蕴含门与最终句门都复用叙述客户端）
    # ------------------------------------------------------------------
    narrator = PW.LlmNarrationClient(model=MODEL, max_tokens=100,
                                     thinking={"type": "disabled"})
    entailment = CEE.LlmEntailmentClient(narration_client=narrator)
    sentence = FSF.LlmSentenceFidelityClient(narration_client=narrator)
    check(not getattr(entailment, "calls", ()),
          "夹具前提：真实模式的蕴含客户端自己没有 calls（蕴含那一次记在叙述客户端上）")
    check(not getattr(sentence, "calls", ()),
          "夹具前提：真实模式的最终句核验适配器同样没有 calls（那一次也记在叙述客户端上）")
    ledger = LB.LLMCallBudget(_policy(), enforce=False)
    patch = _Provider()
    with patch as provider:
        assert LB.install(ledger) is None
        try:
            with LB.section_scope("company"):
                narrator.narrate(messages=MESSAGES, system="s",
                                 prompt_version=PW.NARRATION_PROMPT_VERSION,
                                 model_policy=PW.MODEL_POLICY_PROVIDER_DEFAULT)
                narrator.narrate(messages=MESSAGES, system="s",
                                 prompt_version=PW.NARRATION_PROMPT_VERSION,
                                 model_policy=PW.MODEL_POLICY_PROVIDER_DEFAULT)
                narrator.narrate(messages=MESSAGES, system="s",
                                 prompt_version=NO.NARRATIVE_ORGANIZER_PROMPT_VERSION,
                                 model_policy=NO.NARRATIVE_ORGANIZER_MODEL_POLICY)
                entailment.evaluate(messages=MESSAGES, system="s",
                                    prompt_version=CEE.CLAIM_ENTAILMENT_PROMPT_VERSION,
                                    model_policy=CEE.CLAIM_ENTAILMENT_MODEL_POLICY)
                sentence.evaluate(messages=MESSAGES, system="s",
                                  prompt_version=FSF.FINAL_SENTENCE_FIDELITY_PROMPT_VERSION,
                                  model_policy=FSF.FINAL_SENTENCE_FIDELITY_MODEL_POLICY)
            check(len(provider.calls) == 5 and ledger.count() == 5,
                  f"五次真实请求必须五次记账（provider={len(provider.calls)}, "
                  f"账本={ledger.count()}）")
            check(len(narrator.calls) == 5,
                  f"共用的叙述客户端必须记下全部五次（实际 {len(narrator.calls)}）")
            client_counts = ACC._client_category_counts(narrator, entailment, sentence)
            check(client_counts == {"narration": 3, "claim_entailment": 1,
                                    "final_sentence_fidelity": 1},
                  f"客户端侧按类别归并：蕴含与最终句核验都不得被记两次（实际 {client_counts}）")
            check(client_counts == ledger.category_counts(),
                  "客户端侧记录与账本必须逐类相等（漏计与重复计都会在这里现形）")
            check(len(narrator.calls) == sum(client_counts.values()),
                  "客户端侧各类之和必须等于客户端的调用记录数（不重不漏）")

            # ------------------------------------------------------------------
            # 6. 真实模式下与 logs/llm 按 call_id 对账
            # ------------------------------------------------------------------
            log_files = sorted(patch.logs_dir.glob("*.jsonl"))
            check(len(log_files) == 5,
                  f"假 provider 只负责返回，落盘仍走生产代码：应有 5 条日志（实际 {len(log_files)}）")
            state = _NS(budget=ledger, sections={"company": _NS(llm_calls=5)},
                        section_errors={}, rollback=None, mode=ACC.MODE_REAL)
            gate = ACC._gate_a7_call_budget(state, narrator=narrator, entailment=entailment,
                                            sentence=sentence, mode=ACC.MODE_REAL,
                                            run_dir=Path("."))
            check(gate.status == "pass",
                  f"A7 正向：真实模式下四个视图逐项守恒必须判绿（实际 {gate.detail[:300]!r}）")
            # 反例：账本里「成功」的那次在日志里找不到 ⇒ 账本记的不是真实请求。
            log_files[0].unlink()
            gate = ACC._gate_a7_call_budget(state, narrator=narrator, entailment=entailment,
                                            sentence=sentence, mode=ACC.MODE_REAL,
                                            run_dir=Path("."))
            check(gate.status == "fail" and "没有对应记录" in gate.detail,
                  f"A7：账本记了成功、日志里没有 ⇒ 必须为红（实际 {gate.detail[:300]!r}）")
        finally:
            LB.uninstall()

    # ------------------------------------------------------------------
    # 7. 三个计数视图逐项守恒（含旧口径漏记门后组织的反例）
    # ------------------------------------------------------------------
    ledger = LB.LLMCallBudget(_policy(), enforce=False)
    narrator7 = PW.LlmNarrationClient(model=MODEL, max_tokens=100)
    entailment7 = CEE.LlmEntailmentClient(narration_client=narrator7)
    with _Provider(), _scoped_ledger(ledger):
        # 三个视图都由**生产代码**产生：账本来自共享入口的钩子，客户端记录来自适配器，
        # 各节计量由夹具按「本节真的发过几次」写死（真实运行里它是 `llm_calls`）。
        # company：门前提案 2 次（含一轮有界重写）+ 门后组织 1 次 + 逐候选蕴含 1 次 = 4
        with LB.section_scope("company"):
            _narrate(narrator7, PW.NARRATION_PROMPT_VERSION)
            _narrate(narrator7, PW.NARRATION_PROMPT_VERSION)
            _narrate(narrator7, NO.NARRATIVE_ORGANIZER_PROMPT_VERSION,
                     NO.NARRATIVE_ORGANIZER_MODEL_POLICY)
            entailment7.evaluate(messages=MESSAGES, system="s",
                                 prompt_version=CEE.CLAIM_ENTAILMENT_PROMPT_VERSION,
                                 model_policy=CEE.CLAIM_ENTAILMENT_MODEL_POLICY)
        # financial：门前提案 1 次 + 门后组织 1 次 = 2；industry：0（整节缺口，不调用）
        with LB.section_scope("financial"):
            _narrate(narrator7, PW.NARRATION_PROMPT_VERSION)
            _narrate(narrator7, NO.NARRATIVE_ORGANIZER_PROMPT_VERSION,
                     NO.NARRATIVE_ORGANIZER_MODEL_POLICY)
        meter = {"company": 4, "financial": 2, "industry": 0}

        def _state(ledger_, meter_=None):
            return _NS(budget=ledger_,
                       sections={s: _NS(llm_calls=n)
                                 for s, n in (meter if meter_ is None else meter_).items()},
                       section_errors={}, rollback=None, mode=ACC.MODE_OFFLINE)

        reconciliation = ACC._reconcile_calls(_state(ledger), narrator=narrator7,
                                              entailment=entailment7, sentence=_NS(calls=[]))
        check(reconciliation["problems"] == [],
              "逐项守恒正向：账本 / 客户端记录 / 各节计量三者相等时必须零问题"
              f"（实际 {reconciliation['problems']}）")
        check(reconciliation["ledger_total"] == 6 and reconciliation["meter_sum"] == 6,
              f"两视图合计必须相等（ledger={reconciliation['ledger_total']}, "
              f"meter={reconciliation['meter_sum']}）")
        check(reconciliation["organizer_attempts"] == 2,
              "门后组织的尝试数必须被单独数出来（它是旧口径漏掉的那一项）")
        check(reconciliation["meter_omitted_before_fix"] == 2,
              "旧口径的差额必须等于组织次数——差额是可证的，不是猜的")
        check(reconciliation["ledger_by_section"] == {"company": {"narration": 3,
                                                                  "claim_entailment": 1},
                                                     "financial": {"narration": 2}},
              f"逐节逐类的账必须如实（实际 {reconciliation['ledger_by_section']}）")

        # 反例（旧口径本身）：各节计量漏掉门后组织 ⇒ 必须为红并点名是哪一节。
        problems = ACC._reconcile_calls(
            _state(ledger, {"company": 3, "financial": 2, "industry": 0}),
            narrator=narrator7, entailment=entailment7, sentence=_NS(calls=[]))["problems"]
        check_problem(problems, "company", "各节计量漏掉门后组织时必须点名该节")
        check_problem(problems, "只有一个数", "对账问题必须说清「同一件事只能有一个数」")

        # 反例：计量虚高（自称发过、账本里一次都没有）⇒ 另一类虚记，必须为红。
        problems = ACC._reconcile_calls(
            _state(ledger, {"company": 4, "financial": 2, "industry": 3}),
            narrator=narrator7, entailment=entailment7, sentence=_NS(calls=[]))["problems"]
        check_problem(problems, "industry", "计量不得记下账本上没有的调用")

        # 反例：客户端的类别记录与账本不等（漏记一次蕴含）。
        short = _NS(calls=[c for c in narrator7.calls
                           if c["prompt_version"] != CEE.CLAIM_ENTAILMENT_PROMPT_VERSION])
        problems = ACC._reconcile_calls(_state(ledger), narrator=short,
                                        entailment=_NS(calls=[]), sentence=_NS(calls=[]))["problems"]
        check_problem(problems, "客户端侧记录",
                      "客户端侧与账本不等（重复计数/漏计）必须为红")

        # 反例：有尝试不属于任何一节（例如把门外的探针记进账本）。
        stray_ledger = LB.LLMCallBudget(_policy(), enforce=False)
        with _scoped_ledger(stray_ledger):
            _call(prompt_version=PW.NARRATION_PROMPT_VERSION,
                  section_id=ACC.ROLLBACK_PROBE_SCOPE)
        check(ACC._reconcile_calls(_state(ledger), narrator=narrator7,
                                   entailment=entailment7, sentence=_NS(calls=[]))["problems"] == [],
              "夹具前提：探针的账记在**另一份**只读账本上，不影响本组对账")
        stray = ACC._reconcile_calls(
            _NS(budget=stray_ledger, sections={}, section_errors={}, rollback=None,
                mode=ACC.MODE_OFFLINE),
            narrator=_NS(calls=[]), entailment=_NS(calls=[]), sentence=_NS(calls=[]))["problems"]
        check_problem(stray, "不归属于任何一节",
                      "账本里出现不属于任何一节的尝试必须为红")

        # ------------------------------------------------------------------
        # 7b. 失败节的调用也必须进对账（旧口径在逐节循环里 `continue` 掉，于是那一节的账
        #     既不计数也不对账；报告顶部那句「三视图逐项相等」在失败节上是**假的**）
        # ------------------------------------------------------------------
        # 夹具：industry 节真的发了 2 次请求，但这节**抛错**了 ⇒ 没有
        # `BackboneSectionWriterOutput`，第三个视图**不存在**。
        failed_ledger = LB.LLMCallBudget(_policy(), enforce=False)
        narrator_f = PW.LlmNarrationClient(model=MODEL, max_tokens=100)
        with _Provider(), _scoped_ledger(failed_ledger):
            with LB.section_scope("company"):
                _narrate(narrator_f, PW.NARRATION_PROMPT_VERSION)
            with LB.section_scope("industry"):
                _narrate(narrator_f, PW.NARRATION_PROMPT_VERSION)
                _narrate(narrator_f, PW.NARRATION_PROMPT_VERSION)
        failed_state = _NS(
            budget=failed_ledger, sections={"company": _NS(llm_calls=1)},
            section_errors={"industry": "PackWriterError: 本节没有可授权的材料"},
            rollback=None, mode=ACC.MODE_OFFLINE)
        recon = ACC._reconcile_calls(failed_state, narrator=narrator_f,
                                     entailment=_NS(calls=[]), sentence=_NS(calls=[]))
        check(recon["problems"] == [],
              "失败节正向：它有账、有失败记录、第三个视图本就不存在——这**不是**问题，"
              f"只要它进了显式桶（实际 {recon['problems']}）")
        check(sorted(recon["meter_absent_sections"]) == ["industry"],
              f"失败节必须逐节进桶（实际 {sorted(recon['meter_absent_sections'])}）")
        bucket = recon["meter_absent_sections"]["industry"]
        check(bucket["dispatched"] == 2 and bucket["returned_ok"] == 2
              and bucket["returned_error"] == 0 and bucket["unsettled"] == 0,
              f"失败节的三视图读数必须逐项给出（实际 {bucket}）")
        check(bucket["by_category"] == {"narration": 2},
              f"失败节也必须按**归属类别**给出（实际 {bucket['by_category']}）")
        check(bucket["meter"] is None and "不存在" in bucket["meter_reason"],
              "失败节必须写明「第三个视图不存在」及其原因，不得留一个空数让人自己猜")
        check(bucket["error"].startswith("PackWriterError"),
              f"失败节必须带出本节的失败原因（实际 {bucket['error']!r}）")
        check(recon["attempts_unmetered"] == 2 and recon["attempts_unattributed"] == 0,
              f"无产出节的尝试必须计入显式桶（实际 unmetered={recon['attempts_unmetered']}, "
              f"unattributed={recon['attempts_unattributed']}）")
        cons = recon["conservation"]
        check(cons["holds"] is True and cons["residual"] == 0
              and cons["writing_axis_total"] == 3 and cons["meter_sum"] == 1,
              f"守恒式必须成立且逐项可读（实际 {cons}）")
        check(cons["equation"].startswith("writing_axis_total ==")
              and "attempts_unmetered" in cons["equation"],
              "守恒式必须把无产出节的桶**写进等式**，而不是留在正文里当一句话")
        # 本例不含研究调用 ⇒ 两轴恒等式必须**显式**给出「研究轴 0」，而不是省略那一项：
        # 省略会让「本 run 没跑研究」与「研究调用的账没记进来」读起来一样。
        axis_identity = recon["axis_identity"]
        check(axis_identity["holds"] is True and axis_identity["residual"] == 0
              and axis_identity["ledger_total"] == 3
              and axis_identity["writing_axis_total"] == 3
              and axis_identity["research_axis_total"] == 0,
              f"两轴恒等式必须显式成立（实际 {axis_identity}）")
        check(recon["research"]["axis_total"] == 0
              and recon["research"]["topic_totals"] == {}
              and recon["research"]["conservation"]["holds"] is True,
              "研究轴视图必须在「本轮没有研究调用」时也如实给出（0 是读数，不是缺省）")
        check(recon["ledger_by_status"] == {"ok": 3},
              f"「已发 / 成功返回」必须按状态分列（实际 {recon['ledger_by_status']}）")
        check(recon["client_status_applicable"] is True
              and recon["client_ok_by_category"] == {"narration": 3},
              "真实适配器的记录带 status ⇒ 单向的『成功返回』判据在本例**适用**")
        # 反例：客户端说某次调用成功了，账本里却只有 1 次成功（另 2 次没结算）。
        # 每个反例用**新的**客户端：记录会跨次累积，沿用同一个客户端会让计数里混进上一例的记录。
        reserved_ledger = LB.LLMCallBudget(_policy(), enforce=False)
        narrator_r = PW.LlmNarrationClient(model=MODEL, max_tokens=100)
        with _Provider(), _scoped_ledger(reserved_ledger):
            with LB.section_scope("company"):
                _narrate(narrator_r, PW.NARRATION_PROMPT_VERSION)
        reserved_ledger.attempts[-1].status = LB.STATUS_RESERVED
        problems = ACC._reconcile_calls(
            _NS(budget=reserved_ledger, sections={"company": _NS(llm_calls=1)},
                section_errors={}, rollback=None, mode=ACC.MODE_OFFLINE),
            narrator=narrator_r, entailment=_NS(calls=[]), sentence=_NS(calls=[]))["problems"]
        check_problem(problems, "成功返回",
                      "客户端记录说成功、账本没记成成功时必须为红（反方向不作判据）")
        # 反例：账本里出现未登记的状态 ⇒ 不得被归并进「已发」或「成功返回」。
        weird = LB.LLMCallBudget(_policy(), enforce=False)
        narrator_w = PW.LlmNarrationClient(model=MODEL, max_tokens=100)
        with _Provider(), _scoped_ledger(weird):
            with LB.section_scope("company"):
                _narrate(narrator_w, PW.NARRATION_PROMPT_VERSION)
        weird.attempts[0].status = "half_done"
        problems = ACC._reconcile_calls(
            _NS(budget=weird, sections={"company": _NS(llm_calls=1)},
                section_errors={}, rollback=None, mode=ACC.MODE_OFFLINE),
            narrator=narrator_w, entailment=_NS(calls=[]), sentence=_NS(calls=[]))["problems"]
        check_problem(problems, "未登记的状态",
                      "账本状态词表必须封闭：未登记状态不得被静默归并")
        # 反例：无产出、**也没有**失败记录 ⇒ 这笔账没有归属（与「失败节」是两回事）。
        problems = ACC._reconcile_calls(
            _NS(budget=failed_ledger, sections={"company": _NS(llm_calls=1)},
                section_errors={}, rollback=None, mode=ACC.MODE_OFFLINE),
            narrator=narrator_f, entailment=_NS(calls=[]), sentence=_NS(calls=[]))["problems"]
        check_problem(problems, "没有归属",
                      "既无产出又无失败记录的节不得被当成「失败节」进桶")
        # 门级：失败节**不是**红门（第三个视图本就不存在），但它的读数必须出现在 A7 证据里。
        gate = ACC._gate_a7_call_budget(failed_state, narrator=narrator_f,
                                        entailment=_NS(calls=[]),
                                        mode=ACC.MODE_OFFLINE, run_dir=Path("."))
        check(gate.status == "pass",
              f"A7：本节抛错不等于账目有错——有失败记录的节进显式桶后必须仍判绿"
              f"（实际 {gate.detail[:300]!r}）")
        check(sorted(gate.evidence["reconciliation"]["meter_absent_sections"]) == ["industry"]
              and "meter_absent" in gate.evidence["counting_views"],
              "A7 证据必须同时给出无产出节的读数与这条口径本身")

        # ------------------------------------------------------------------
        # 8. A7 的门级结论（离线：只判三个视图；账本缺失必须为红）
        # ------------------------------------------------------------------
        offline_state = _NS(budget=ledger,
                            sections={s: _NS(llm_calls=n) for s, n in meter.items()},
                            section_errors={}, rollback=None, mode=ACC.MODE_OFFLINE)
        gate = ACC._gate_a7_call_budget(offline_state, narrator=narrator7,
                                        entailment=entailment7,
                                        mode=ACC.MODE_OFFLINE, run_dir=Path("."))
        check(gate.status == "pass",
              f"A7 离线正向必须判绿（实际 {gate.detail[:300]!r}）")
        check(gate.evidence["llm_log_reconciliation"]["applicable"] is False,
              "离线模式必须如实写明「与 logs/llm 对账不适用」，不得静默跳过")
        check("不在门内" in gate.evidence["rollback_probe"]["note"],
              "半事务探针必须在 A7 证据里逐字声明它不在门内（不是被漏掉的调用）")
        check("ledger" in gate.evidence["counting_views"],
              "A7 必须把三个计数视图的口径写进证据")

        # 反例：账本里出现拒绝（跑完的运行不得有拒绝）。
        refused_ledger = LB.LLMCallBudget(_policy(narration=(1, 1)), enforce=True)
        with _scoped_ledger(refused_ledger):
            _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="company")
            try:
                _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="company")
            except LB.LLMCallBudgetExceeded:
                pass
        gate = ACC._gate_a7_call_budget(
            _NS(budget=refused_ledger, sections={"company": _NS(llm_calls=1)},
                section_errors={}, rollback=None, mode=ACC.MODE_OFFLINE),
            narrator=_NS(calls=[{"prompt_version": PW.NARRATION_PROMPT_VERSION}]),
            entailment=_NS(calls=[]), mode=ACC.MODE_OFFLINE, run_dir=Path("."))
        check(gate.status == "fail" and "事前拒绝" in gate.detail,
              f"A7：跑完的运行里出现事前拒绝必须为红（实际 {gate.detail[:300]!r}）")

        # 反例：没有账本（门没装）⇒ 调用计量无从复核，必须为红。
        gate = ACC._gate_a7_call_budget(
            _NS(budget=None, sections={}, section_errors={}, rollback=None,
                mode=ACC.MODE_OFFLINE),
            narrator=_NS(calls=[]), entailment=_NS(calls=[]),
            mode=ACC.MODE_OFFLINE, run_dir=Path("."))
        check(gate.status == "fail" and "没有安装调用预算门" in gate.detail,
              f"A7：没有账本时必须为红（实际 {gate.detail[:300]!r}）")

        # ------------------------------------------------------------------
        # 9. runner 的已批准政策 + 拒绝产物
        # ------------------------------------------------------------------
        runner_policy = ACC._call_budget_policy()
        caps = {c.category: c for c in runner_policy.categories}
        check(set(caps) == {LB.CATEGORY_NARRATION, LB.CATEGORY_CLAIM_ENTAILMENT,
                            LB.CATEGORY_SECTION_LLM_EVALUATOR,
                            LB.CATEGORY_FINAL_SENTENCE_FIDELITY},
              f"runner 的类别词表必须是封闭四值（实际 {sorted(caps)}）——"
              "最终句语义门 B 是**自己的一类**，不得并进叙述类或蕴含类")
        # §二 分批之后，「一轮写作 = 一次请求」这个结构依据没有了：一轮 = 一次**分批扫描**，
        # 因此旧口径 8/24 低于新的结构上界，继续沿用就等于把一次注定失败的运行伪装成可运行。
        # 本批重估为 62/144，并且**批准状态来自用户 2026-09-24 的正式提示词**（该提示词授权
        # 这一次真实运行）——`approved=True` 不是代码自己改回来的：依据逐字写在 `basis` 里，
        # 读报告的人不必回看聊天记录就知道「这个数字是谁批的」。数字本身一字未动。
        check(caps[LB.CATEGORY_NARRATION].approved
              and caps[LB.CATEGORY_NARRATION].max_attempts_per_section == 62
              and caps[LB.CATEGORY_NARRATION].max_attempts_total == 144,
              "分批之后叙述类必须写成「每节 ≤62、整轮 ≤144」且 approved=True（含批准依据）"
              f"（实际 {caps[LB.CATEGORY_NARRATION].max_attempts_per_section}/"
              f"{caps[LB.CATEGORY_NARRATION].max_attempts_total}, "
              f"approved={caps[LB.CATEGORY_NARRATION].approved}）")
        check(ACC.APPROVAL_PROVENANCE[:12] in caps[LB.CATEGORY_NARRATION].basis
              and ACC.APPROVAL_PROVENANCE[:12] in runner_policy.axes[0].basis,
              "写作类与研究轴都必须把自己的批准依据（用户正式提示词原文）带在 basis 里："
              "「已批准」必须可追溯到一句话，而不是一个布尔值")
        check(caps[LB.CATEGORY_CLAIM_ENTAILMENT].approved
              and caps[LB.CATEGORY_CLAIM_ENTAILMENT].max_attempts_per_section == 40
              and caps[LB.CATEGORY_CLAIM_ENTAILMENT].max_attempts_total == 100,
              "本次授权的逐候选蕴含口径必须是「每节 ≤40、整轮 ≤100」")
        # 「本类本轮不许发」用**上限 0** 表达（已批准的决定），不是「不批准」——后者在构造期
        # 就拒掉整轮，与「其余两类照发」的授权不符。
        check(caps[LB.CATEGORY_SECTION_LLM_EVALUATOR].approved
              and caps[LB.CATEGORY_SECTION_LLM_EVALUATOR].max_attempts_per_section == 0
              and caps[LB.CATEGORY_SECTION_LLM_EVALUATOR].max_attempts_total == 0,
              "可选章级 LLM 评估本次必须写成「已批准但上限 0」")
        check(all(c.max_output_tokens is None for c in runner_policy.categories),
              "预算政策里不得存在费用型 output token 限额（容量按阶段给，见 output_capacity）")
        # §12.4.4 第 4 步：最终句语义门 B 是**第四个调用点**，它的上限由
        # `_narration_structural_bound()` 自己推出来（每节 ≤2、三节共 ≤6），
        # 因此整轮 244 → **250**（= 144 + 100 + 0 + 6）——抬高的部分恰好是新增调用点的结构上界。
        check(caps[LB.CATEGORY_FINAL_SENTENCE_FIDELITY].approved
              and caps[LB.CATEGORY_FINAL_SENTENCE_FIDELITY].max_attempts_per_section == 2
              and caps[LB.CATEGORY_FINAL_SENTENCE_FIDELITY].max_attempts_total == 6,
              "最终句语义门 B 必须写成「已批准、每节 ≤2、三节共 ≤6」"
              f"（实际 {caps[LB.CATEGORY_FINAL_SENTENCE_FIDELITY].max_attempts_per_section}/"
              f"{caps[LB.CATEGORY_FINAL_SENTENCE_FIDELITY].max_attempts_total}, "
              f"approved={caps[LB.CATEGORY_FINAL_SENTENCE_FIDELITY].approved}）")
        check(runner_policy.total_max_attempts == 250 and runner_policy.total_approved,
              "整轮尝试总数必须随最终句语义门一起重估为 250（= 144 + 100 + 0 + 6）且已批准"
              "（旧的 124 = 24 + 100 是按「一轮一次请求」推的，结构依据已变）")
        # 整轮上限**逐值等于各类上限之和**：文档把 250 写成「144 + 100 + 0 + 6」，那么代码里
        # 那个字面量就必须真的是这个和。这条把「改了一类上限、忘了改整轮」变成红——否则报告
        # 会印出一个不存在的上限（报告文案从政策里读这个数，不再自己写死）。
        _cap_sum = sum(int(c.max_attempts_total) for c in runner_policy.categories)
        check(runner_policy.total_max_attempts == _cap_sum,
              f"整轮上限必须等于各类上限之和 {_cap_sum}"
              f"（实际 {runner_policy.total_max_attempts}）——"
              "改任一类上限都要同时重估整轮，不得只改一处")
        # **250 只是写作轴的整轮上限**：研究调用走另一条轴，不并进这个数。把两者混成一个数字
        # 会让「研究跑了多少次」在报告里无从读回，也会让研究调用悄悄吃掉已批准的写作额度。
        # 研究轴的上限**逐 topic 自派生**（acc-20）：每个 topic 一个值，整轴 = 各 topic 之和。
        _aspects = ACC._research_topic_aspects()
        _expected_caps = ACC._research_topic_caps(_aspects)
        _expected_total = sum(_expected_caps.values())
        check([a.axis for a in runner_policy.axes] == [LB.AXIS_RESEARCH]
              and runner_policy.axes[0].approved
              and runner_policy.axes[0].per_scope_max_attempts == _expected_caps
              and runner_policy.axes[0].max_attempts == _expected_total
              and runner_policy.axes[0].max_attempts_per_scope == min(_expected_caps.values())
              and len(set(_expected_caps.values())) > 1,
              f"研究轴必须单列一条**逐 topic** 的上限（每 aspect 上界 × 该 topic 的 aspect 数），"
              f"且各 topic 的值不得全都相同（实际 {[a.to_dict() for a in runner_policy.axes]}）")
        check(runner_policy.axes[0].max_attempts != runner_policy.total_max_attempts
              and _expected_total not in (0, None),
              "研究轴整轮上限不得与写作轴的 244 合并成一个数：两条轴各有独立上限与独立批准状态")

        # 上限不是拍出来的数字：叙述类的每节上限必须能从「一轮最多写几次」推出来。写死数字
        # 而不钉住推导链，下次改重试策略就会留下一个与技术依据脱节的上限。这里用 runner
        # **自己**的推导函数（同一处取值），因此它验的是「预检用的那套推导」而不是测试里
        # 另抄的一份。
        _bound = ACC._narration_structural_bound()
        check(caps[LB.CATEGORY_NARRATION].max_attempts_per_section >= _bound["per_section"]
              and caps[LB.CATEGORY_NARRATION].max_attempts_total >= _bound["total"],
              f"叙述类上限必须容得下结构性最坏情形（每节 {_bound['per_section']}、"
              f"整轮 {_bound['total']}），否则预检通过之后才整轮失败")
        # 推导链逐项对账：全部来自可读常量——写作轮数取 `MAX_FOLLOW_UP_ROUNDS + 1`、
        # 栏目定向取 `MAX_SUBSECTION_FOCUS_PASSES`、重试取已批准常量、**每轮批数取
        # `aspect_batch_count`（与 `plan_aspect_batches` 同一处算式）、每轮缩小取
        # `MAX_SWEEP_SHRINK_STEPS`**。缺了分批这一项，「一轮一次请求」的旧算式就会重新
        # 看起来成立——那正是本批要修的形状。
        from sections import company_worker as _CW
        _rounds = ACC.APPROVED_WRITER_MAX_LLM_RETRIES + 1 + PW.MAX_SUBSECTION_FOCUS_PASSES
        _by_sec = {sid: _bound["writer_passes_per_section"] * (
            _rounds * (PW.aspect_batch_count(n) + PW.MAX_SWEEP_SHRINK_STEPS) + 1)
            for sid, n in _bound["aspects_by_section"].items()}
        check(_bound["writer_passes_per_section"] == _CW.MAX_FOLLOW_UP_ROUNDS + 1
              and _bound["rounds_per_pass"] == _rounds
              and _bound["calls_per_round_by_section"] == {
                  sid: PW.aspect_batch_count(n) + PW.MAX_SWEEP_SHRINK_STEPS
                  for sid, n in _bound["aspects_by_section"].items()}
              and _bound["per_section_by_section"] == _by_sec
              and _bound["per_section"] == max(_by_sec.values())
              and _bound["total"] == sum(_by_sec.values()),
              f"叙述类结构性上界必须逐项来自代码常量（实际 {_bound}，期望 {_by_sec}）")
        # 零 aspect 的那一节也**必须**算 1 次请求：`aspect_batch_count(0) == 1`（与
        # `plan_aspect_batches` 的零 aspect 分支逐值一致）。若按 0 算，财务节的每轮调用数会
        # 少一次，于是「上线覆盖了上界」而真实调用恰好越过它——预算门最不该有的方向。
        check(PW.aspect_batch_count(0) == 1
              and _bound["calls_per_round_by_section"] == {
                  sid: _bound["batches_by_section"][sid] + PW.MAX_SWEEP_SHRINK_STEPS
                  for sid in ACC.SECTION_ORDER},
              "零 aspect 的节也必须每轮算 1 次请求（否则上界比实际少一次）")
        # 预检必须**真的**会拒：把上限压到上界以下，`_call_budget` 要在建门之前就拒掉整轮
        # （而不是等真实请求发出去、花掉额度之后才撞上限）。
        _under = LB.CallBudgetPolicy(
            policy_version="test-under-cap", approved_model=runner_policy.approved_model,
            categories=tuple(
                LB.CategoryCap(**{**c.to_dict(), "max_attempts_per_section": 1,
                                  "max_attempts_total": 1, "approved": True})
                for c in runner_policy.categories if c.category == LB.CATEGORY_NARRATION)
            + tuple(c for c in runner_policy.categories
                    if c.category != LB.CATEGORY_NARRATION),
            prompt_versions=runner_policy.prompt_versions,
            total_max_attempts=runner_policy.total_max_attempts, total_approved=True)
        check(_under.approvable == (),
              "反例前置：压低上限的政策本身仍须是「已全部获批」，否则拒因不是上界不足")
        try:
            ACC._assert_budget_covers_structural_bound(_under)
            check(False, "上限低于结构上界时必须在发请求之前整轮拒绝")
        except ACC.AcceptanceRefusal as exc:
            check("结构上界" in str(exc) and "每节上限 1" in str(exc),
                  f"预检的拒因必须逐项报出两个推导（实际 {str(exc)[:200]!r}）")
        # 正例：当前政策必须**不**被拒（否则真实模式根本跑不起来）。
        ACC._assert_budget_covers_structural_bound(runner_policy)
        check(True, "当前已批准政策必须通过上界预检")
        check(set(runner_policy.prompt_versions) == {
            PW.NARRATION_PROMPT_VERSION, NO.NARRATIVE_ORGANIZER_PROMPT_VERSION,
            CEE.CLAIM_ENTAILMENT_PROMPT_VERSION, RE.NARRATIVE_EVALUATOR_PROMPT,
            FSF.FINAL_SENTENCE_FIDELITY_PROMPT_VERSION,
            RT.RESEARCH_ACTION_PROMPT_VERSION, RT.RESEARCH_ANSWER_PROMPT_VERSION,
            RT.RESEARCH_ENTAILMENT_PROMPT_VERSION},
              "归属表必须覆盖正式链的五个**写作**调用点与三个**研究**调用点（少一个即漏计："
              "漏掉的研究版本会在第一次研究调用处被门拒，整轮停在研究相位；漏掉最终句核验版本"
              "会在定稿门第一次发请求时被拒）")
        check([runner_policy.category_of(v) for v in (
            RT.RESEARCH_ACTION_PROMPT_VERSION, RT.RESEARCH_ANSWER_PROMPT_VERSION,
            RT.RESEARCH_ENTAILMENT_PROMPT_VERSION)] == list(LB.RESEARCH_CATEGORIES)
              and all(runner_policy.axis_of(c) == LB.AXIS_RESEARCH
                      for c in LB.RESEARCH_CATEGORIES)
              and all(runner_policy.axis_of(c) == LB.AXIS_WRITING for c in
                      (LB.CATEGORY_NARRATION, LB.CATEGORY_CLAIM_ENTAILMENT,
                       LB.CATEGORY_SECTION_LLM_EVALUATOR,
                       LB.CATEGORY_FINAL_SENTENCE_FIDELITY)),
              "三个研究 prompt 必须各自归到研究轴的三个类别，写作四类必须留在写作轴："
              "把研究类别挂到写作轴（或反之）会让「哪条轴花了多少次」读错")
        # 研究类别**不得**出现在 `categories` 里：那份列表恒为四个写作 `CategoryCap`，且被
        # 「和 == 250」的断言钉死。研究上限只经 `axes` 表达（见 `CROSS_DOCUMENT_JOINT_RETRIEVAL_PLAN.md`
        # §0.3.1 硬约束）。
        check(not (set(c.category for c in runner_policy.categories)
                   & set(LB.RESEARCH_CATEGORIES)),
              "研究类别不得混进 `categories`：那会同时改掉「和 == 250」这个已批准的口径")

        # 反向反例：若把「上限 0」误写成「不批准」，整轮都在构造期被拒（授权其实允许）。
        offline_budget = ACC._call_budget(mode=ACC.MODE_OFFLINE)
        check(offline_budget.enforce is False,
              "离线模式只记账不拦截（替身不发真实请求）")
        # 真实模式：批准依据已经写在政策里（用户 2026-09-24 正式提示词），因此门**必须**建得起来
        # ——这是「本次授权的那一次真实运行在结构上可行」的可执行证据。数字与批准状态同时被
        # 上面两条钉住，所以这里断言「能建门」不构成把「未获批也能跑」写成通过。
        real_from_policy = ACC._call_budget(mode=ACC.MODE_REAL)
        check(real_from_policy.enforce is True
              and real_from_policy.policy.approvable == (),
              "已批准的政策必须在真实模式下建起强制门（否则授权的那一次真实运行无从发起）")
        # **拒绝机制本身**仍须成立——它现在由**未批准政策的夹具**证明，而不是靠「runner 的政策
        # 恰好未获批」。这一条比旧口径更强：旧口径只证明了「这一份政策会被拒」，证明不了
        # 「换一份未批准的政策也会被拒」。用 ppolicy 的最小改法造出未批准：把研究轴的批准拿掉。
        import dataclasses as _dc
        unapproved_axis = _dc.replace(
            runner_policy.axes[0], approved=False, basis="测试夹具：故意未批准的研究轴")
        unapproved_policy = _dc.replace(runner_policy, axes=(unapproved_axis,))
        check(LB.AXIS_RESEARCH in unapproved_policy.approvable,
              "反例前置：拿掉研究轴的批准之后，它必须出现在「尚未获批」清单里")
        saved_policy = ACC._call_budget_policy
        ACC._call_budget_policy = lambda: unapproved_policy
        try:
            try:
                ACC._call_budget(mode=ACC.MODE_REAL)
                check(False, "有未批准的预算轴时，真实模式必须在预检处整轮拒绝")
            except ACC.AcceptanceRefusal as exc:
                check("尚未全部获批" in str(exc) and LB.AXIS_RESEARCH in str(exc),
                      f"拒因必须逐字列出还缺哪一条轴的批准（实际 {str(exc)[:200]!r}）")
            # 离线模式与真实模式的差别**只在 enforce**：离线替身不发真实请求，因此它照记
            # （`enforce=False`），未获批的量在那里不构成拒绝理由。这一条是**如实**的边界声明，
            # 不是「离线可以绕过批准」——它发不出任何真实请求。上界比对仍然两条模式共用。
            offline_unapproved = ACC._call_budget(mode=ACC.MODE_OFFLINE)
            check(offline_unapproved.enforce is False
                  and LB.AXIS_RESEARCH in offline_unapproved.policy.approvable,
                  "离线模式只放开 enforce（替身不发真实请求）：账本照记，但政策里的未批准量"
                  "仍必须如实留在 approvable 清单里（不得因为离线就被抹平）")
        finally:
            ACC._call_budget_policy = saved_policy
        # 「已批准但上限 0」这个写法的语义仍须成立（否则「本类本轮不许发」就没有表达方式）。
        # 这里用**测试本地的**全批准副本证明它，不再拿 runner 的政策冒充——runner 的政策现在是
        # 未获批的，用它建强制门等于把「未获批可跑」写成通过。
        approved_clone = LB.CallBudgetPolicy(
            policy_version="test-all-approved", approved_model=runner_policy.approved_model,
            categories=tuple(LB.CategoryCap(**{**c.to_dict(), "approved": True})
                             for c in runner_policy.categories),
            prompt_versions=runner_policy.prompt_versions,
            total_max_attempts=runner_policy.total_max_attempts, total_approved=True)
        real_budget = LB.LLMCallBudget(approved_clone, enforce=True)
        check(real_budget.enforce is True,
              "全部已批准 + 上限 0 的可选类 ⇒ 门必须能建起来（本次授权就是这一类）")
        zero_cap_ledger = None
        with _scoped_ledger(real_budget):
            try:
                _call(prompt_version=RE.NARRATIVE_EVALUATOR_PROMPT, section_id="company",
                      model=ACC.APPROVED_BUDGET_MODEL)
            except LB.LLMCallBudgetExceeded as exc:
                zero_cap_ledger = exc
        check(zero_cap_ledger is not None
              and zero_cap_ledger.limit_kind == "本节上限"
              and zero_cap_ledger.limit == 0,
              f"已批准但上限 0 的类别必须一次都发不出去（实际 {zero_cap_ledger!r}）")
        check(real_budget.count() == 0,
              "被拒的评估请求不占额度：账上仍是 0 次尝试")

        # `_write_refusal`：拒绝产物必须自带账本，且把「有没有发出过请求」写清楚。
        # 本组只核**拒绝产物本身**的形状，因此把三处「按节展开」的产物写手换成常数桩：
        # 它们读的是各节真对象（draft / result / narrative），在真实链路里由离线 run 覆盖。
        saved_hashes, saved_print = ACC._trust_root_hashes, ACC._print
        saved_writers = (ACC._artifacts_payload, ACC._before_after_md, ACC._manual_review_md)
        ACC._trust_root_hashes = lambda profile: {"stub": "0" * 64}
        ACC._print = lambda message: None
        ACC._artifacts_payload = lambda state: {
            "drafts": {}, "claims": [], "narratives": {}, "results": {},
            "evaluations": {}, "unresolved": []}
        ACC._before_after_md = lambda state: "# probe\n"
        ACC._manual_review_md = lambda state, gates: "# probe\n"
        try:
            with tempfile.TemporaryDirectory(prefix="m930-3-refusal-") as tmp:
                run_dir = Path(tmp) / "m930-3-acc-refusal-probe"
                run_dir.mkdir()
                code = ACC._write_refusal(
                    run_dir, mode=ACC.MODE_REAL, generated_at="2026-01-01T00:00:00Z",
                    profile=None, before={"stub": "0" * 64},
                    title="调用预算门（事前拦截）", detail="LLMCallBudgetExceeded: probe",
                    state=_NS(budget=refused_ledger, sections={"company": _NS(llm_calls=1)},
                              section_errors={}, assembly_error="", mode=ACC.MODE_REAL,
                              # 拒绝产物也要落来源清单（清单是输入身份的现场）。真实
                              # `RunState` 恒有 `inputs`；这里的替身照此补齐，不靠 getattr 兜底。
                              # `tasks` / `requirements` / `authorities` / `run_contexts` /
                              # `trace_sink` 同样是 `RealInputs` 的**必有**字段（P5 的逐 aspect
                              # 诊断要读它们），替身一并给出，缺一个就会让诊断写手崩在替身上。
                              # `company_id` / `evidence_set_version` / `dims` 也一样：
                              # `subject_declaration`（`subj-2`）在**拒绝报告**里就读它们——
                              # 被拒的一轮同样要说清「这一轮本来是为谁跑的」，也要给出
                              # **核对到**的读者面名称（`company_name`）。
                              inputs=_NS(company_id="c",
                                         evidence_set_version="ev-probe",
                                         # 报告截止日（时钟派生）与下面的快照日**不同**：
                                         # 拒绝报告要把两条日期轴各自直接摆出来（acc-16）。
                                         report_as_of="2026-09-24",
                                         dims={"subject_declared_by": "cli",
                                               "snapshot_id": "snap-probe",
                                               "as_of_date": "2026-01-01",
                                               "company_name": "样本主体"},
                                         source_manifest=SM.SourceManifest(
                                  policy_version=SM.MANIFEST_POLICY_VERSION,
                                  company_id="c", generated_at="2026-01-01T00:00:00Z",
                                  report_as_of="2026-01-01",
                                  report_timezone="Asia/Shanghai",
                                  financial_data_cutoff=None, entries=(),
                                  provenance_findings=(), selection=(),
                                  primary_document_id=None,
                                  primary_document_version=None),
                                  tasks={}, requirements={}, authorities={},
                                  run_contexts={}, resolver=None, pack_store=None,
                                  trace_sink=None, research_llm=None)),
                    budget_gate=refused_ledger)
                check(code == 1, "拒绝产物必须是非零退出码")
                report = json.loads((run_dir / "acceptance_report.json")
                                    .read_text(encoding="utf-8"))
                check(report["status"] == "refused" and report.get("refused") is True,
                      f"拒绝报告的状态必须是 refused（实际 {report['status']!r}）")
                check(report["gates"][0]["gate_id"] == "A0"
                      and report["gates"][0]["status"] == "refused",
                      "拒绝必须写成 A0 的 typed 结论（不是崩掉、也不是 fail）")
                check(any("已经真实发出 1 次请求" in line for line in report["honesty"]),
                      f"拒绝报告必须写明已发出的次数（实际 {report['honesty']}）")
                ledger_file = run_dir / "llm_call_ledger.json"
                check(ledger_file.exists(), "拒绝产物必须落账本（llm_call_ledger.json）")
                payload = json.loads(ledger_file.read_text(encoding="utf-8"))
                check(payload["ledger"]["refusal_total"] == 1
                      and payload["ledger"]["attempt_total"] == 1,
                      "账本必须同时留下「发出过几次」与「被拒的那一次」")
                check(payload["ledger"]["refusals"][0]["call_id"],
                      "被拒的尝试必须带 call_id（预算状态与调用日志按 call_id 对账）")
                index = json.loads((run_dir / "artifact_index.json")
                                   .read_text(encoding="utf-8"))
                names = [row["artifact"] for row in index["artifacts"]]
                check("llm_call_ledger.json" in names,
                      f"账本必须进产物索引（实际 {names}）")
                manifest = json.loads((run_dir / "manifest.json")
                                      .read_text(encoding="utf-8"))
                check(manifest["status"] == "refused" and manifest.get("refusal"),
                      "拒绝 manifest 必须自报 refused 并留下拒绝理由")
                # 没有任何请求发出过的情形：0 必须是**真的 0**，不是「没统计」。
                code = ACC._write_refusal(
                    run_dir, mode=ACC.MODE_REAL, generated_at="2026-01-01T00:00:00Z",
                    profile=None, before={}, title="能否构成一次真实验收",
                    detail="AcceptanceRefusal: probe", state=None, budget_gate=None)
                check(code == 1, "建门之前的拒绝同样是非零退出码")
                report = json.loads((run_dir / "acceptance_report.json")
                                    .read_text(encoding="utf-8"))
                check(any("真的 0" in line or "一个请求都没有发出" in line
                          for line in report["honesty"]),
                      f"没有账本时的 0 必须写明是真的 0（实际 {report['honesty']}）")
        finally:
            ACC._trust_root_hashes, ACC._print = saved_hashes, saved_print
            (ACC._artifacts_payload, ACC._before_after_md,
             ACC._manual_review_md) = saved_writers

    # ------------------------------------------------------------------
    # 10. 证据产物本身：内部句柄不得混进报告；同一批尝试不得写成两份
    # ------------------------------------------------------------------
    # 10a. 半事务探针的返回里 `db` 是**内部句柄**（`Path`）。A7 一开始把它原样塞进 evidence，
    #      于是 `acceptance_report.json` 在 `json.dumps` 时炸掉整个 run——这是离线 run 实测踩到
    #      的（记账全对、A7 判绿，报告却写不出去）。「门判绿」不等于「产物可落盘」，两件事都得测。
    probe = {"db": Path("rollback_probe.db"), "raised": True, "insert_attempts": 4}
    projected = ACC._probe_result_for_report(probe)
    check(projected["db"] == "rollback_probe.db" and isinstance(projected["db"], str),
          f"探针结果里的内部句柄必须投影成文件名（实际 {projected['db']!r}）")
    check(projected["raised"] is True and projected["insert_attempts"] == 4,
          "投影不得顺手改掉探针的非路径字段")
    check(ACC._probe_result_for_report(None) == {}, "没有探针时投影必须是空字典（不是 None）")

    ledger = LB.LLMCallBudget(_policy(), enforce=False)
    with _Provider():
        assert LB.install(ledger) is None
        try:
            _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="company")
            _call(prompt_version=NO.NARRATIVE_ORGANIZER_PROMPT_VERSION, section_id="company")
            _call(prompt_version=CEE.CLAIM_ENTAILMENT_PROMPT_VERSION, section_id="financial")
        finally:
            LB.uninstall()
    narrator = _NS(calls=[{"prompt_version": PW.NARRATION_PROMPT_VERSION},
                          {"prompt_version": NO.NARRATIVE_ORGANIZER_PROMPT_VERSION}])
    entailment = _NS(calls=[{"prompt_version": CEE.CLAIM_ENTAILMENT_PROMPT_VERSION}])
    state = _NS(budget=ledger,
                sections={"company": _NS(llm_calls=2), "financial": _NS(llm_calls=1)},
                section_errors={}, rollback=probe, mode=ACC.MODE_OFFLINE)
    gate = ACC._gate_a7_call_budget(state, narrator=narrator, entailment=entailment,
                                    mode=ACC.MODE_OFFLINE, run_dir=Path("."))
    check(gate.status == "pass",
          f"A7 前提：这一组的三视图是守恒的（实际 {gate.detail[:300]!r}）")
    try:
        json.dumps(gate.to_dict(), ensure_ascii=False)
        serializable = True
    except TypeError:
        serializable = False
    check(serializable, "A7 的门结论必须可落盘：探针带 Path 时也不例外")
    check(gate.evidence["rollback_probe"]["probe_result"]["db"] == "rollback_probe.db",
          "A7 证据里的探针结果必须是投影后的形态")

    # 10b. 账本产物（`llm_call_ledger.json`）里同一批尝试只能有**一处**：这份产物的用途就是
    #      逐项对账，两个同样的 49 只会让人猜哪个才算数。
    accounting = ACC._call_accounting(state, narrator=narrator, entailment=entailment,
                                     sentence=_NS(calls=[]))
    payload = ACC._ledger_payload(state, mode=ACC.MODE_OFFLINE,
                                  generated_at="2026-01-01T00:00:00Z",
                                  run_dir=Path("m930-3-acc-probe"), status="pass",
                                  accounting=accounting)
    check(payload["ledger"]["attempt_total"] == 3,
          f"账本产物必须带完整账本（实际 {payload['ledger']['attempt_total']}）")
    check("ledger" not in (payload["accounting"] or {}),
          f"账本产物里 attempts 只能有一处（实际多出的键 {sorted(payload['accounting'])}）")
    check({"narrator_calls", "entailment_calls", "reconciliation"} <= set(payload["accounting"]),
          "去掉重复的账本副本不得顺手砍掉读视图（按节/按类的对账要留下）")
    try:
        json.dumps(payload, ensure_ascii=False)
        payload_serializable = True
    except TypeError:
        payload_serializable = False
    check(payload_serializable, "账本产物必须可落盘")

    # ------------------------------------------------------------------
    # 11. 预算门的事前拒绝 ≠ 「本节失败」：整轮停下，不得被逐节异常处理吞掉
    # ------------------------------------------------------------------
    # 逐节驱动按节吞异常（「本节失败、别的节继续」），但预算拒绝**必须**穿出去：当作节内失败
    # 记一笔然后继续，就正好会一路撞同一个上限——真实模式下就是继续烧已批准的额度。这两种
    # 「非正常结束」在报告里必须能分开，所以这里把两者摆在一起看差异。
    from sections import company_worker as CW

    # 逐节驱动的调用点会在**调用之前**读这几个字段（相位本身被替换成假体，不看它们的内容）。
    _FAKE_INPUTS = _NS(pack_store=None, resolver=None, dependencies_of=None)
    saved_section_input = ACC._section_input
    saved_phase = CW.run_formal_m930_writer_phase
    try:
        ACC._section_input = lambda inputs, section_id: _NS(section_id=section_id)
        seen: list[str] = []

        def _refuse(rows, **kwargs):
            seen.append("call")
            raise LB.LLMCallBudgetExceeded(category=LB.CATEGORY_CLAIM_ENTAILMENT,
                                           section_id="company", limit_kind="本节上限",
                                           limit=1, observed=1,
                                           prompt_version=CEE.CLAIM_ENTAILMENT_PROMPT_VERSION)

        CW.run_formal_m930_writer_phase = _refuse
        errors: dict = {}
        sections: dict = {}
        raised = None
        try:
            ACC._drive_into(_FAKE_INPUTS, sections=sections, errors=errors, commits={},
                            policy=None, generated_at="2026-01-01T00:00:00Z",
                            llm_client=None, entailment_llm_client=None,
                            # 最终句语义门 B 的客户端在这条用例里**故意**也不注入：本节在跑到它
                            # 之前就已经被替换掉的相位拒掉/掐断（本用例验的是预算门与逐节记账），
                            # 缺注入因此照旧不会被读成「无需核验」——它在 `_finalize_section`
                            # 的 fail-closed 前置上另有独立用例把关。
                            final_sentence_llm_client=None,
                            section_store=None)
        except LB.LLMCallBudgetError as exc:
            raised = exc
        check(raised is not None, "预算门的事前拒绝必须穿出逐节驱动（不得被吞掉）")
        check(errors == {},
              f"被拒的那一节不得被记成「本节失败」（实际 {errors}）")
        check(sections == {}, "被拒的被节不得留下半份产出")
        check(len(seen) == 1,
              f"预算拒绝后必须整轮停止：不得继续驱动后面的节（实际驱动 {len(seen)} 次）")

        # 对照：一条**普通**异常就是「本节失败、别的节继续」——两者必须能分开。
        def _explode(rows, **kwargs):
            seen.append("call")
            raise RuntimeError("fixture: 本节写作失败")

        CW.run_formal_m930_writer_phase = _explode
        seen.clear()
        errors = {}
        try:
            ACC._drive_into(_FAKE_INPUTS, sections={}, errors=errors, commits={},
                            policy=None, generated_at="2026-01-01T00:00:00Z",
                            llm_client=None, entailment_llm_client=None,
                            # 最终句语义门 B 的客户端在这条用例里**故意**也不注入：本节在跑到它
                            # 之前就已经被替换掉的相位拒掉/掐断（本用例验的是预算门与逐节记账），
                            # 缺注入因此照旧不会被读成「无需核验」——它在 `_finalize_section`
                            # 的 fail-closed 前置上另有独立用例把关。
                            final_sentence_llm_client=None,
                            section_store=None)
        except LB.LLMCallBudgetError as exc:  # pragma: no cover - 对照必须不抛预算错
            check(False, f"普通异常不得升级成预算拒绝（实际 {exc!r}）")
        check(len(seen) == len(ACC.SECTION_ORDER) and len(errors) == len(ACC.SECTION_ORDER),
              f"普通失败按节记录并继续（实际驱动 {len(seen)} 次、记账 {len(errors)} 条）")
    finally:
        ACC._section_input = saved_section_input
        CW.run_formal_m930_writer_phase = saved_phase

    # ------------------------------------------------------------------
    # 12. `--validate-only` 真的只读，且读到的是存在的 key
    # ------------------------------------------------------------------
    # 旧写法先在别人的 run 库里 `store.init_db`（建表 + 迁移 + WAL），再读一个 A4 证据里**不存在**
    # 的 key：复核历史 run 既改了它、又恒定报 3 条假问题。
    check(ACC._produced_sections({"gates": [{"gate_id": "A4", "evidence": {
        "commit_counts": {"financial": 1, "company": 1}}}]}) == ["company", "financial"],
        "产出过的节必须按名排序取自 A4 的 commit_counts")
    check(ACC._produced_sections({"gates": []}) == [],
          "报告里没有 A4 时必须返回空（不得猜一个节去数）")

    with tempfile.TemporaryDirectory(prefix="m930-3-validate-") as tmp:
        db = Path(tmp) / "section_chain_v2.db"
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("CREATE TABLE current_section_draft_v2 (section_id TEXT)")
            conn.executemany("INSERT INTO current_section_draft_v2 VALUES (?)",
                             [("company",), ("financial",), ("financial",)])
            conn.commit()
        finally:
            conn.close()
        before = (db.read_bytes(), sorted(p.name for p in Path(tmp).iterdir()))
        counts = ACC._draft_root_counts_readonly(db, ["company", "financial", "industry"])
        check(counts == {"company": 1, "financial": 2, "industry": 0},
              f"只读计数必须按节如实数行（实际 {counts}）")
        after = (db.read_bytes(), sorted(p.name for p in Path(tmp).iterdir()))
        check(before == after,
              f"只读复核不得留下任何字节（含 -wal/-shm）：改动 = "
              f"{sorted(set(after[1]) - set(before[1]))}，库内容变化 = {before[0] != after[0]}")
        missing = None
        try:
            ACC._draft_root_counts_readonly(db.parent / "absent.db", ["company"])
        except sqlite3.Error as exc:
            missing = exc
        check(missing is not None,
              "库不存在时必须如实报错（只读连接不得顺手把库建出来）")

    # ------------------------------------------------------------------
    # 13. 调用点不得把门吃掉：受限编辑器「失败即回退」不包括预算拒绝
    # ------------------------------------------------------------------
    # 这一条是第 11 组的另一种形态：`sections/publishable_report.llm_editor` 的语义是「调用没成
    # 就回退确定性编辑器」，而它的 `prompt_version`（`publication_editor-1`）**未登记**在预算
    # 归属表里。若把拒绝也当普通失败回退，门就在这个调用点上被吃掉了：请求确实没发出，但「整轮
    # 停止」没了，产物里只会留下一条 `fallback_reason`。
    from sections import publishable_report as PB

    fake_claim = _NS(topic_id="t1", text="示例句。", claim_id="c1", citation_refs=())
    editor_topics = [("t1", "示例小节")]
    check(PB.LLM_EDITOR_PROMPT_VERSION not in _PROMPT_VERSIONS,
          "夹具前提：编辑器 prompt 版本**不在**归属表里（未登记 = 必须被拒）")

    ledger = LB.LLMCallBudget(_policy(), enforce=False)
    with _Provider() as provider, _scoped_ledger(ledger):
        refused = None
        try:
            PB.llm_editor((fake_claim,), editor_topics,
                          render_fn=lambda ref: "cite", company_id="fixture")
        except LB.LLMCallAttributionError as exc:
            refused = exc
        check(refused is not None,
              "未登记的调用点必须让预算拒绝穿出去（不得被『失败即回退』吃掉）")
        check(len(provider.calls) == 0 and ledger.count() == 0,
              "被拒的请求不得触达 provider，也不得占一次尝试")
        check(ledger.refusals and ledger.refusals[-1]["prompt_version"]
              == PB.LLM_EDITOR_PROMPT_VERSION,
              f"被拒的编辑器调用必须留在拒绝记录里（实际 {ledger.refusals}）")

    # 对照：**传输失败**仍然按原语义回退确定性编辑器——收紧的只有预算拒绝那一条。
    with _Provider(explode=True):
        body, audit = PB.llm_editor((fake_claim,), editor_topics,
                                    render_fn=lambda ref: "cite", company_id="fixture")
        check("c1" in body and audit["used"] is False
              and str(audit["fallback_reason"]).startswith("llm_call_failed:"),
              f"传输失败必须照旧回退确定性编辑器（实际 fallback_reason="
              f"{audit['fallback_reason']!r}）")

    # ------------------------------------------------------------------
    # 14. 本次授权的上限与它们的边界：第 N+1 次必须被拒（N 从政策读出，不写死）
    # ------------------------------------------------------------------
    # 「上限只是用来阻止失控循环、不是使用目标」这句话的**可测形态**是：每个上限都在第 N+1 次
    # 精确生效，而第 N 次照常发出。N 从政策本身读出（本批：叙述 每节 62 / 共 144，蕴含 每节 40 /
    # 共 100，可选评估 0/0，最终句核验 每节 2 / 共 6，整轮 250）。
    def _refused(**kwargs):
        try:
            _call(**kwargs)
        except LB.LLMCallBudgetExceeded as exc:
            return exc
        return None

    authorized_policy = ACC._call_budget_policy()
    # 本组验的是「第 N+1 次被拒」这条边界语义，需要一道**强制**门；而 runner 的政策现在把叙述类
    # 标为**尚未获批**（真实模式必须整轮拒绝，见上文用例），强制门会因为未获批而在构造期就拒掉。
    # 因此边界组按**测试本地**的全批准副本建门：上限数字**全部照抄 runner**（边界仍是 runner 的
    # 数字），只把批准位翻开——「未获批」这件事因此不会被本组悄悄抹掉。后续各组的强制门同此。
    enforced_policy = LB.CallBudgetPolicy(
        policy_version="test-boundary-approved",
        approved_model=authorized_policy.approved_model,
        categories=tuple(LB.CategoryCap(**{**c.to_dict(), "approved": True})
                         for c in authorized_policy.categories),
        prompt_versions=authorized_policy.prompt_versions,
        total_max_attempts=authorized_policy.total_max_attempts, total_approved=True)
    # 边界数字**从政策本身读**，不再写死 6/18/118：本组要验的性质是「第 N+1 次被拒、第 N 次
    # 照发」，与 N 具体是多少无关。写死数字会让每次重估上限都留下一组与技术依据脱节的断言
    # （而且失败信息会看起来像「预算错了」而不是「测试没跟上」）。
    _caps = {c.category: c for c in authorized_policy.categories}
    _nar_sec = int(_caps[LB.CATEGORY_NARRATION].max_attempts_per_section)
    _nar_tot = int(_caps[LB.CATEGORY_NARRATION].max_attempts_total)
    _ent_sec = int(_caps[LB.CATEGORY_CLAIM_ENTAILMENT].max_attempts_per_section)
    _ent_tot = int(_caps[LB.CATEGORY_CLAIM_ENTAILMENT].max_attempts_total)
    _sum_tot = sum(int(c.max_attempts_total) for c in authorized_policy.categories)
    check(_sum_tot == authorized_policy.total_max_attempts,
          f"整轮总上限必须恰好等于三类上限之和（{_nar_tot}+{_ent_tot}+0="
          f"{authorized_policy.total_max_attempts}）：它因此不会是**先**撞上的那一层，"
          "真正逐条生效的是各类自己的上限")
    # 同一组数字还必须真容得下结构上界——否则本组验的边界只是一个「注定中途失败」的边界。
    check(_nar_sec >= ACC._narration_structural_bound()["per_section"],
          "本组使用的叙述类本节上限必须 ≥ 结构上界（否则边界断言与实际链路不是同一个世界）")
    with _Provider() as provider:
        ledger14 = LB.LLMCallBudget(enforced_policy, enforce=True)
        with _scoped_ledger(ledger14):
            for _ in range(_nar_sec):
                _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="company",
                      model=ACC.APPROVED_BUDGET_MODEL)
            over_sec = _refused(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id="company",
                                model=ACC.APPROVED_BUDGET_MODEL)
            check(over_sec is not None and over_sec.limit_kind == "本节上限"
                  and over_sec.limit == _nar_sec,
                  f"叙述类本节第 {_nar_sec + 1} 次必须被「本节上限 {_nar_sec}」拒"
                  f"（实际 {over_sec!r}）")
            check(len(provider.calls) == _nar_sec,
                  f"被拒的第 {_nar_sec + 1} 次不得触达 provider")
            # 走满叙述类的**整轮**额度：三节之和（144）**小于** 3×每节上限（186）——各节的
            # 结构上界并不相等（aspect 数不同：62/44/38），所以「把每节都压满」不是拿到整轮额度
            # 的方式。这里按「政策读出来的整轮余量」发，边界仍然逐次精确（第 144 次照发、
            # 第 145 次被拒）。
            _remaining = _nar_tot - _nar_sec
            for section_id in ("financial", "industry"):
                _take = min(_remaining, _nar_sec)
                for _ in range(_take):
                    _call(prompt_version=PW.NARRATION_PROMPT_VERSION, section_id=section_id,
                          model=ACC.APPROVED_BUDGET_MODEL)
                _remaining -= _take
            check(_remaining == 0 and ledger14.count() == _nar_tot,
                  f"叙述类走满三节应是 {_nar_tot} 次（实际 {ledger14.count()}）")
            over_tot = _refused(prompt_version=PW.NARRATION_PROMPT_VERSION,
                                section_id="fourth", model=ACC.APPROVED_BUDGET_MODEL)
            check(over_tot is not None and over_tot.limit_kind == "本类总上限"
                  and over_tot.limit == _nar_tot,
                  f"换一个节也绕不过叙述类的整轮上限 {_nar_tot}（实际 {over_tot!r}）")
            check(len(provider.calls) == _nar_tot,
                  f"被拒的第 {_nar_tot + 1} 次不得触达 provider")

            for section_id in ("financial", "industry"):
                for _ in range(_ent_sec):
                    _call(prompt_version=CEE.CLAIM_ENTAILMENT_PROMPT_VERSION,
                          section_id=section_id, model=ACC.APPROVED_BUDGET_MODEL)
            over_ent_sec = _refused(prompt_version=CEE.CLAIM_ENTAILMENT_PROMPT_VERSION,
                                    section_id="financial", model=ACC.APPROVED_BUDGET_MODEL)
            check(over_ent_sec is not None and over_ent_sec.limit_kind == "本节上限"
                  and over_ent_sec.limit == _ent_sec,
                  f"蕴含类本节第 {_ent_sec + 1} 条必须被「本节上限 {_ent_sec}」拒"
                  f"（实际 {over_ent_sec!r}）")
            for _ in range(_ent_tot - 2 * _ent_sec):
                _call(prompt_version=CEE.CLAIM_ENTAILMENT_PROMPT_VERSION,
                      section_id="fourth", model=ACC.APPROVED_BUDGET_MODEL)
            _spent = _nar_tot + _ent_tot
            check(ledger14.count() == _spent,
                  f"走满授权额度应是 {_nar_tot} + {_ent_tot} = {_spent} 次"
                  f"（实际 {ledger14.count()}）")
            over = _refused(prompt_version=CEE.CLAIM_ENTAILMENT_PROMPT_VERSION,
                            section_id="fifth", model=ACC.APPROVED_BUDGET_MODEL)
            check(over is not None
                  and over.limit_kind == "本类总上限",
                  f"授信用尽后的下一次必须被拒（{_spent} ⇒ 先撞上的是该类自己的上限 "
                  f"{_ent_tot}；实际 {over!r}）")
            check(len(provider.calls) == _spent and len(ledger14.refusals) == 4,
                  f"超额的四次都不得触达 provider（实际 provider={len(provider.calls)}、"
                  f"refusals={len(ledger14.refusals)}）")
            check(ledger14.status_counts() == {"ok": _spent},
                  f"授权范围内的每一次都必须成功落账（实际 {ledger14.status_counts()}）")

    # ------------------------------------------------------------------
    # 15. 截断 = 一次**失败**的调用：不返回残缺内容、不重跑、原始字节仍留证据
    # ------------------------------------------------------------------
    # 本组的判据不是「相信没人截断」，而是：截断与「一次短一点的合格输出」在**结构上**必须
    # 不同。因此逐项钉：抛 typed 异常、不返回 NarrationResult、账本按 error 落账（照样占一次）、
    # 客户端记录带 provider 侧 call_id、原始残缺输出仍写进 logs/llm、provider 只被调用一次。
    for reason in LLC.TRUNCATION_STOP_REASONS:
        truncated_body = '{"proposals": [{"topic_id": "t1"'
        patch15 = _Provider(stop_reason=reason, text=truncated_body)
        with patch15 as provider15:
            ledger = LB.LLMCallBudget(_policy(), enforce=False)
            narrator = PW.LlmNarrationClient(
                model=MODEL, max_tokens=4096, thinking={"type": "disabled"},
                max_tokens_by_prompt_version={PW.NARRATION_PROMPT_VERSION: 8192})
            with _scoped_ledger(ledger), LB.section_scope("company"):
                raised = None
                result = None
                try:
                    result = _narrate(narrator, PW.NARRATION_PROMPT_VERSION)
                except LLC.LLMTruncatedResponse as exc:
                    raised = exc
                check(raised is not None and result is None,
                      f"finish_reason={reason}：必须抛 LLMTruncatedResponse，"
                      f"**不得**返回残缺结果（实际 result={result!r}）")
                check(raised is not None and raised.response.finish_reason == reason
                      and truncated_body in raised.response.text,
                      "异常必须带那次响应的身份（finish_reason）与其残缺正文")
                check(len(provider15.calls) == 1,
                      f"截断后不得自动重跑（provider 实际被调用 {len(provider15.calls)} 次）")
                check(len(ledger.attempts) == 1 and ledger.attempts[0].status == "error"
                      and "truncated" in ledger.attempts[0].error,
                      f"截断必须记成一次**失败**的尝试且照样占一次（实际 "
                      f"{[(a.status, a.error) for a in ledger.attempts]}）")
                check(ledger.count() == 1,
                      "截断的那一次必须计入额度（否则「失败不计数」的结构漏洞又回来了）")
                check(len(narrator.calls) == 1 and narrator.calls[0].get("status") == "error",
                      f"客户端记录必须有一条 error（实际 {narrator.calls}）")
                row = narrator.calls[0]
                check(row.get("call_id") == raised.response.call_id
                      and row.get("finish_reason") == reason
                      and row.get("output_tokens") is not None,
                      f"客户端记录必须带 provider 侧 call_id 与截断状态（实际 {row}）")
                check(row.get("max_tokens") == 8192,
                      f"客户端记录必须写明本次实际下发的容量（实际 {row.get('max_tokens')!r}）")
                logs = sorted(patch15.logs_dir.glob("*.jsonl"))
                check(len(logs) == 1, f"截断的原始输出仍须落盘为证据（实际 {len(logs)} 条日志）")
                body = json.loads(logs[0].read_text(encoding="utf-8").splitlines()[0])
                check(body["call_id"] == raised.response.call_id
                      and body["finish_reason"] == reason
                      and body["prompt_version"] == PW.NARRATION_PROMPT_VERSION
                      and truncated_body in body["completion"],
                      "logs/llm 里必须能按 call_id 找到这次截断（证据，不是正文）")

    # 对照：缺省 `reject_truncated=False` **逐字节**保持既有行为（其它管线自己处置短输出）。
    # 若这条也抛，说明「收紧」被顺手施加到了整条仓库上——那不是本次授权的范围。
    with _Provider(stop_reason="max_tokens"):
        plain = LLC.chat_with_usage(MESSAGES, model=MODEL, max_tokens=100)
        check(plain.text == "ok" and plain.finish_reason == "max_tokens",
              "不启用截断拒绝时，既有调用点必须原样拿到响应（缺省行为不得被收紧）")

    # ------------------------------------------------------------------
    # 16. 截断必须**穿过**逐节驱动、让整轮停下（不得被当成「本节失败、别的节继续」）
    # ------------------------------------------------------------------
    # 与第 11 组同形：逐节驱动按节吞异常，但截断不是节内失败——把残缺提案当成「本节没内容」
    # 记一笔再继续，正好会把残缺内容送进后面的每一节。
    from sections import company_worker as CW2

    saved_section_input2 = ACC._section_input
    saved_phase2 = CW2.run_formal_m930_writer_phase
    try:
        ACC._section_input = lambda inputs, section_id: _NS(section_id=section_id)
        seen2: list[str] = []
        truncated_response = LLC.LLMResponse(
            text='{"proposals": [', input_tokens=11, output_tokens=8192, latency_ms=7,
            model=MODEL, call_id="truncated-probe-call", finish_reason="max_tokens")

        def _truncate(rows, **kwargs):
            seen2.append("call")
            raise LLC.LLMTruncatedResponse(truncated_response)

        CW2.run_formal_m930_writer_phase = _truncate
        errors2: dict = {}
        sections2: dict = {}
        raised2 = None
        try:
            ACC._drive_into(_NS(pack_store=None, resolver=None, dependencies_of=None),
                            sections=sections2, errors=errors2, commits={},
                            policy=None, generated_at="2026-01-01T00:00:00Z",
                            llm_client=None, entailment_llm_client=None,
                            # 最终句语义门 B 的客户端在这条用例里**故意**也不注入：本节在跑到它
                            # 之前就已经被替换掉的相位拒掉/掐断（本用例验的是预算门与逐节记账），
                            # 缺注入因此照旧不会被读成「无需核验」——它在 `_finalize_section`
                            # 的 fail-closed 前置上另有独立用例把关。
                            final_sentence_llm_client=None,
                            section_store=None)
        except LLC.LLMTruncatedResponse as exc:
            raised2 = exc
        check(raised2 is not None and raised2.response is truncated_response,
              "截断必须穿出逐节驱动（同一份响应对象，不包装成别的失败）")
        check(errors2 == {}, f"截断不得被记成「本节失败」（实际 {errors2}）")
        check(sections2 == {}, "截断的那一节不得留下半份产出")
        check(len(seen2) == 1, f"截断后必须整轮停止（实际驱动 {len(seen2)} 次）")
    finally:
        ACC._section_input = saved_section_input2
        CW2.run_formal_m930_writer_phase = saved_phase2

    # 同一件事在 `execute_run` 里必须变成一份**typed 拒绝产物**（而不是崩掉的 run）：报告
    # status=refused、A0 结论点名截断，并且 honesty 不得沿用「被拒的那一次没有到达 provider」
    # ——那次请求真的发出去了，只是被 provider 截断（第 11 组的反面对照）。
    saved_writers2 = (ACC._artifacts_payload, ACC._before_after_md, ACC._manual_review_md)
    saved_hashes2, saved_print2 = ACC._trust_root_hashes, ACC._print
    saved_env, saved_clients = ACC.RealEnvironment, ACC._clients
    saved_profile, saved_drive, saved_rundb = ACC._call_budget, ACC._drive_into, ACC._run_dir_db
    from planning import demo_scope as SC2
    saved_load = SC2.load_demo_scope_profile
    # `bind_section_order` 会把节序写进**模块全局**，因此替身必须连同它一起还原，否则本组会
    # 把「单节」泄漏给本模块后面的检查。
    saved_section_order = ACC.SECTION_ORDER
    saved_active_profile_id = ACC._ACTIVE_PROFILE_ID
    # `_call_budget_policy()` 现在要从冻结 Contract 投影里算逐 topic 研究上限，而本组夹具
    # 正是为了**不碰真实资产**才把 profile 加载器换成 `None`。因此这里把那一份投影也一并
    # 换成夹具值：本组验的是「截断即停 + 拒绝产物」，不是逐 topic 上限的推导（后者另有
    # 正反例）。不换就会在写拒绝产物时因为读不到 profile 而崩——那是夹具的洞，不是被测行为。
    saved_aspects = ACC._research_topic_aspects
    ACC._research_topic_aspects = lambda: {"fixture_topic": 2}
    # 同一原因，`_call_budget_policy()` 现在还要为**最终句语义门 B** 从"写作轮的同一套模型"
    # 里推出每节/整轮上限，而那条推导要读 `_section_aspect_counts()`（冻结 WritingSpec 的
    # Contract 投影）。本组既不碰真实资产，就把这一份投影也换成夹具值：本组验的是
    # 「截断即停 + 拒绝产物」，不是叙述类上界的推导（后者另有正反例）。B 的每节上限只由
    # 写作轮数决定（与 aspect 数无关），因此夹具值不会让本组的判据变得宽松。
    saved_narr_bound_counts = ACC._section_aspect_counts
    ACC._section_aspect_counts = lambda: {"company": 1, "financial": 1, "industry": 1}

    class _FakeEnv:
        def __init__(self, *, clock=None, mode=None, declaration=None):
            # 真实 `RealEnvironment` 现在要求传入本轮唯一时钟瞬间（O-11）、运行模式（研究侧
            # LLM 注入真 provider 还是确定性替身）与**报告输入声明**（`subj-1`：主体由输入
            # 声明，不由财务库排序决定）。替身三个都照收，且都**不使用**：本组验的是
            # 「截断即停 + 拒绝产物」，既不构造时间，也不装配真实研究链。替身的形参必须与真实
            # 构造点一起演进——少了任何一个都会在 `execute_run` 的装配处直接 TypeError。
            self._clock = clock
            self._mode = mode
            self._declaration = declaration

        def __enter__(self):
            return _NS(inputs=_NS(company_id="c", document_id="d", document_version=1,
                                  report_as_of="2026-01-01",
                                  generated_at="2026-01-01T00:00:00Z",
                                  report_timezone="Asia/Shanghai",
                                  # `RealInputs` 的必有字段：`subject_declaration`（`subj-1`）
                                  # 在拒绝报告里要读它（被拒的一轮同样要说清「为谁跑的」）。
                                  evidence_set_version="ev-probe",
                                  # `rc-rd-1` 起启动行同时打印快照选择日；它与 `report_as_of`
                                  # 是**两个**日期，替身也照这个形状给。`subj-2` 另需
                                  # `snapshot_id` / `subject_declared_by` / `company_name`：
                                  # 真实 `_financial_dims` 恒给出这三个键（已由 P4 的专项 eval
                                  # 钉住），替身照给，否则 `verified_against` 与读者面名称
                                  # 会静默地写出一片 `null`。
                                  dims={"as_of_date": "2025-12-31",
                                        "snapshot_id": "snap-probe",
                                        "subject_declared_by": "cli",
                                        "company_name": "样本主体"},
                                  # 拒绝产物同样要落来源清单，因此替身必须携带一个**形状正确**
                                  # 的清单对象（用真实 dataclass，不用随手拼的 namespace）。
                                  source_manifest=SM.SourceManifest(
                                      policy_version=SM.MANIFEST_POLICY_VERSION,
                                      company_id="c", generated_at="2026-01-01T00:00:00Z",
                                      report_as_of="2026-01-01",
                                      report_timezone="Asia/Shanghai",
                                      financial_data_cutoff=None, entries=(),
                                      provenance_findings=(), selection=(),
                                      primary_document_id=None,
                                      primary_document_version=None)))

        def __exit__(self, *exc):
            return False

    # 同样用全批准副本建门（同 §14 的理由）：本组要的是一道**强制**门，而不是「runner 政策已
    # 获批」这个前提——后者现在不成立，拿它建门会连累本组的判据。
    truncated_ledger = LB.LLMCallBudget(enforced_policy, enforce=True)
    # 让账本里**已经有一次**真实发出过的尝试（就是被截断的那一次）：它按 error 落账，且没有
    # 任何事前拒绝——这正是「停止原因不是预算门」的现场。
    with _scoped_ledger(truncated_ledger), LB.section_scope("company"):
        ticket = LB.reserve_attempt(call_id=truncated_response.call_id,
                                    model=ACC.APPROVED_BUDGET_MODEL, max_tokens=8192,
                                    prompt_version=PW.NARRATION_PROMPT_VERSION)
        LB.settle_attempt(ticket, status=LB.STATUS_ERROR,
                          error="truncated: finish_reason='max_tokens'")
    check(len(truncated_ledger.attempts) == 1 and not truncated_ledger.refusals,
          "夹具前提：账本里有一次已发出但失败的尝试，且没有任何事前拒绝")
    ACC.RealEnvironment = _FakeEnv
    ACC._trust_root_hashes = lambda profile: {"stub": "0" * 64}
    ACC._print = lambda message: None
    ACC._artifacts_payload = lambda state: {
        "drafts": {}, "claims": [], "narratives": {}, "results": {},
        "evaluations": {}, "unresolved": []}
    ACC._before_after_md = lambda state: "# probe\n"
    ACC._manual_review_md = lambda state, gates: "# probe\n"
    ACC._call_budget = lambda *, mode: truncated_ledger
    ACC._clients = lambda *, mode, model: (_NS(calls=[]), _NS(calls=[]), _NS(calls=[]))
    ACC._drive_into = _truncate
    ACC._run_dir_db = lambda run_dir, fn, **kwargs: fn(**kwargs)
    # `bind_section_order(profile)` 之后，节序**只由 profile 派生**（写死的三节常量已拆）。
    # 因此替身加载器不能再返回 `None`：它必须交出一份**最小 profile**（节序 + 身份），否则
    # `execute_run` 会在装配处 fail-closed——那是被测代码的正确行为，不是夹具的洞。本组验的是
    # 「截断即停 + 拒绝产物」，与节集无关，所以这里给单节。
    SC2.load_demo_scope_profile = lambda path: _NS(
        selected_section_ids=("company",), profile_id="fixture-profile")
    try:
        with tempfile.TemporaryDirectory(prefix="m930-3-truncation-") as tmp:
            root = Path(tmp)
            code = ACC.execute_run(run_id=None, results_root=root, mode=ACC.MODE_REAL,
                                   model=ACC.APPROVED_BUDGET_MODEL, subject="c",
                                   subject_name="样本主体")
            check(code == 1, "截断整轮必须以非零退出码收场")
            run_dirs = [p for p in root.iterdir() if p.is_dir()]
            check(len(run_dirs) == 1, f"截断必须留下一份拒绝产物（实际 {len(run_dirs)} 个目录）")
            if run_dirs:
                report = json.loads((run_dirs[0] / "acceptance_report.json")
                                    .read_text(encoding="utf-8"))
                check(report["status"] == "refused" and report.get("refused") is True,
                      f"截断产物必须是 refused（实际 {report['status']!r}）")
                check(report["gates"][0]["gate_id"] == "A0"
                      and "截断" in report["gates"][0]["title"],
                      f"A0 结论必须点名是截断停止（实际 {report['gates'][0]['title']!r}）")
                honesty = "｜".join(report["honesty"])
                check("没有到达 provider" not in honesty,
                      f"截断停止的那一次**真的发出去了**：不得沿用「没有到达 provider」（实际 {honesty}）")
                check("停止原因见下方 A0 结论" in honesty,
                      f"honesty 必须如实写明停止原因不是预算门（实际 {honesty}）")
                check("truncated" in json.dumps(report["llm_calls"]["ledger"], ensure_ascii=False),
                      "截断的那一次必须留在拒绝产物的账本里（含 error 原因）")
    finally:
        (ACC._artifacts_payload, ACC._before_after_md,
         ACC._manual_review_md) = saved_writers2
        ACC._trust_root_hashes, ACC._print = saved_hashes2, saved_print2
        ACC.RealEnvironment, ACC._clients = saved_env, saved_clients
        ACC._call_budget, ACC._drive_into, ACC._run_dir_db = saved_profile, saved_drive, saved_rundb
        SC2.load_demo_scope_profile = saved_load
        ACC.SECTION_ORDER = saved_section_order
        ACC._ACTIVE_PROFILE_ID = saved_active_profile_id
        ACC._research_topic_aspects = saved_aspects
        ACC._section_aspect_counts = saved_narr_bound_counts

    # ------------------------------------------------------------------
    # 17. 每个阶段下发的容量 = 账本记下的容量（不是全链一个统一数字）
    # ------------------------------------------------------------------
    check(ACC.output_capacity_policy()["by_prompt_version"][PW.NARRATION_PROMPT_VERSION]
          == ACC.OUTPUT_CAPACITY_NARRATION
          and ACC.output_capacity_policy()["by_prompt_version"][CEE.CLAIM_ENTAILMENT_PROMPT_VERSION]
          == ACC.OUTPUT_CAPACITY_CLAIM_ENTAILMENT,
          "各阶段的输出容量必须按 prompt 版本分别给足（提案/组织 与 逐候选蕴含 不同）")
    check(ACC.OUTPUT_CAPACITY_NARRATION != ACC.OUTPUT_CAPACITY_CLAIM_ENTAILMENT,
          "两个阶段的输出结构不同，容量不得是同一个数字")
    check(all(c.max_output_tokens is None for c in ACC._call_budget_policy().categories),
          "容量是技术参数：预算政策里不得出现费用型 token 限额")
    check(ACC.OUTPUT_CAPACITY_BASIS and "8102" in ACC.OUTPUT_CAPACITY_BASIS,
          "容量的选择依据必须写进产物（依据来自实测 logs/llm，不是猜的）")
    # 真实模式：发往 provider 的 `max_tokens` 就是该阶段的容量（按 prompt 版本，不是全链一个数）。
    with _Provider() as provider17:
        ledger = LB.LLMCallBudget(_policy(), enforce=False)
        with _scoped_ledger(ledger), LB.section_scope("financial"):
            real_narrator = ACC._clients(mode=ACC.MODE_REAL, model=MODEL)[0]
            _narrate(real_narrator, PW.NARRATION_PROMPT_VERSION)
            check(provider17.calls[-1]["max_tokens"] == ACC.OUTPUT_CAPACITY_NARRATION,
                  f"叙述阶段（提案/组织）必须按自己的容量下发（实际 "
                  f"{provider17.calls[-1]['max_tokens']}）")
            check(ledger.attempts[-1].max_tokens == ACC.OUTPUT_CAPACITY_NARRATION,
                  "账本里必须记下**本次实际下发**的容量（不只是政策里的一个数字）")
            # 蕴含走**同一个**适配器：容量按它自己的 prompt 版本给，不跟着叙述走。
            CEE.LlmEntailmentClient(narration_client=real_narrator).evaluate(
                messages=MESSAGES, system="s",
                prompt_version=CEE.CLAIM_ENTAILMENT_PROMPT_VERSION,
                model_policy=CEE.CLAIM_ENTAILMENT_MODEL_POLICY)
            check(provider17.calls[-1]["max_tokens"] == ACC.OUTPUT_CAPACITY_CLAIM_ENTAILMENT,
                  f"逐候选蕴含必须按自己的容量下发（够它完整输出结构化决定；实际 "
                  f"{provider17.calls[-1]['max_tokens']}）")
            check(len(real_narrator.calls) == 2,
                  "共用一个适配器时，两个阶段的容量必须**逐次**记在各自的调用记录里")

    # 离线替身：不发请求，因此它的账上 max_tokens 必须是 None——不得伪造一个 provider 参数。
    ledger = LB.LLMCallBudget(_policy(), enforce=False)
    with _scoped_ledger(ledger), LB.section_scope("financial"):
        offline_narrator, offline_entailment, offline_sentence = ACC._clients(
            mode=ACC.MODE_OFFLINE, model=MODEL)
        check(not hasattr(offline_narrator, "capacity_for"),
              "离线替身不得带一个它根本没有下发的容量参数（替身不发请求）")
        offline_entailment.evaluate(messages=MESSAGES, system="s",
                                    prompt_version=CEE.CLAIM_ENTAILMENT_PROMPT_VERSION,
                                    model_policy=CEE.CLAIM_ENTAILMENT_MODEL_POLICY)
        check(ledger.attempts[-1].max_tokens is None,
              "离线替身没有发请求，账上不得出现一个它没有发出去的容量")
        check("max_tokens" not in offline_entailment.calls[0],
              "离线替身的客户端记录同样不得伪造 provider 参数")
        # B 门的离线替身与另两个替身不同：它**读请求**（`nsfid-1` 的输入面形状不对就当场抛，
        # 不猜、不照单全收）。因此这里给一个形状正确的最小请求面（一句最终句、零定位原子），
        # 而不是叙事用的 `MESSAGES`。本组验的是三个替身**同一个记账口径**（不发请求、不伪造
        # 容量），不是语义门本身——后者由专项模块把关。
        sentence_messages = [{"role": "user", "content": json.dumps({
            "sentences": [{"sentence_id": "s-fixture", "sentence_kind": "natural",
                           "text": "示例句。", "claim_ids": [], "citation_ids": [],
                           "context_binding_ids": []}],
            # `nsfr-2`：定位面是**逐句槽位表**（键 = 每一句的 sentence_id），不再是扁平清单。
            "claims": [], "accepted_bindings": [], "located_atoms": {"s-fixture": []},
        }, ensure_ascii=False)}]
        offline_sentence.evaluate(messages=sentence_messages, system="s",
                                  prompt_version=FSF.FINAL_SENTENCE_FIDELITY_PROMPT_VERSION,
                                  model_policy=FSF.FINAL_SENTENCE_FIDELITY_MODEL_POLICY)
        check(ledger.attempts[-1].max_tokens is None and not hasattr(offline_sentence,
                                                                     "capacity_for"),
              "最终句核验的离线替身同样不发请求、不伪造容量（三个替身一个口径）")

    # ------------------------------------------------------------------
    # 18. 截断现场必须**数得出来**，且跑完的真实 run 里有截断一律为红
    # ------------------------------------------------------------------
    with _Provider(stop_reason="max_tokens") as patch:
        ledger = LB.LLMCallBudget(_policy(), enforce=False)
        narrator = PW.LlmNarrationClient(model=MODEL, max_tokens=8192)
        with _scoped_ledger(ledger), LB.section_scope("company"):
            try:
                _narrate(narrator, PW.NARRATION_PROMPT_VERSION)
            except LLC.LLMTruncatedResponse:
                pass
        offline_state = _NS(budget=ledger, sections={}, section_errors={}, rollback=None,
                            mode=ACC.MODE_OFFLINE)
        policy = ACC.truncation_policy(offline_state, narrator=narrator,
                                       entailment=_NS(calls=[]), sentence=_NS(calls=[]))
        check(policy["truncated_attempts"] == 1 and len(policy["truncated_calls"]) == 1,
              f"截断必须被数出来（账本 {policy['truncated_attempts']} 次、"
              f"客户端 {len(policy['truncated_calls'])} 条）")
        check(policy["truncated_calls"][0]["finish_reason"] == "max_tokens"
              and policy["truncated_calls"][0]["call_id"],
              "截断现场必须带 prompt 版本与 provider call_id，供复核者按 id 对上 logs/llm")
        check(policy["applicable"] is False and policy["policy"],
              "离线模式必须写明该统计不适用（不是「没统计」），并逐字给出处置口径")
        # 跑完的真实 run 里出现截断 ⇒ 有地方把截断当成了合格输出（或重跑过）= 红。
        real_state = _NS(budget=ledger, sections={"company": _NS(llm_calls=1)},
                         section_errors={}, rollback=None, mode=ACC.MODE_REAL)
        gate = ACC._gate_a7_call_budget(real_state, narrator=narrator,
                                        entailment=_NS(calls=[]), sentence=_NS(calls=[]),
                                        mode=ACC.MODE_REAL, run_dir=Path("."))
        check(gate.status == "fail" and "截断" in gate.detail,
              f"跑完的真实 run 里出现截断必须为红（实际 {gate.status}：{gate.detail[:300]!r}）")
        check(gate.evidence["truncation_policy"]["truncated_attempts"] == 1,
              "A7 证据里必须留下截断计数（判据可复核，不是一句结论）")
    # 对照：没有截断时，同一判据必须安静（否则「有截断 → 红」会退化成「有调用 → 红」）。
    quiet_ledger = LB.LLMCallBudget(_policy(), enforce=False)
    with _Provider(), _scoped_ledger(quiet_ledger), LB.section_scope("company"):
        quiet_narrator = PW.LlmNarrationClient(model=MODEL, max_tokens=100)
        _narrate(quiet_narrator, PW.NARRATION_PROMPT_VERSION)
    quiet_state = _NS(budget=quiet_ledger, sections={"company": _NS(llm_calls=1)},
                      section_errors={}, rollback=None, mode=ACC.MODE_REAL)
    quiet_policy = ACC.truncation_policy(quiet_state, narrator=quiet_narrator,
                                         entailment=_NS(calls=[]), sentence=_NS(calls=[]))
    check(quiet_policy["truncated_attempts"] == 0 and quiet_policy["truncated_calls"] == [],
          f"没有截断时统计必须是 0/空（实际 {quiet_policy}）")

    # ------------------------------------------------------------------
    # 18b（C5）。判**最终结局**，不判「有没有出现过截断」；结局的判据是**批次谱系**
    # ------------------------------------------------------------------
    # 判据是**身份**，不是「该节最终有没有产出 `SectionDraft`」：真实 run r4 的现场正是反例
    # ——两次截断各被两个缩批子请求答回（子树叶子全部 `ok`），该节此后因**别的**拒绝类型
    # （`proposal_identity_unresolvable` / `path_b_high_risk_surface`）失败、没有 Draft。
    # 按「有没有 Draft」判会把两次**已经恢复**的截断记成结局缺口。所以下面每一组都从
    # **账本里那次被截断调用的 `call_id`** 出发，沿拒绝审计里的 `parent_batch_id` 收子树。
    with _Provider(stop_reason="max_tokens"):
        ledger18 = LB.LLMCallBudget(_policy(), enforce=False)
        narrator18 = PW.LlmNarrationClient(model=MODEL, max_tokens=8192)
        with _scoped_ledger(ledger18), LB.section_scope("company"):
            try:
                _narrate(narrator18, PW.NARRATION_PROMPT_VERSION)
            except LLC.LLMTruncatedResponse:
                pass
    _truncated_attempts = [a for a in ledger18.attempts if a.status == "error"]
    check(len(_truncated_attempts) == 1 and _truncated_attempts[0].call_id,
          "夹具前提：账本里有**恰好一条**带 `call_id` 的截断尝试（谱系锚点必须来自账本，"
          "不得在测试里另编一个 id）")
    anchor_call = _truncated_attempts[0].call_id

    def _batch(batch_id, status, *, depth, parent, call_id=None):
        return {"batch_id": batch_id, "label": f"company/{batch_id}", "status": status,
                "shrink_depth": depth, "parent_batch_id": parent, "call_id": call_id}

    def _payload(batches, *, kind="proposal_identity_unresolvable"):
        return {"company": [{"rejection_kind": kind, "batches": batches}]}

    # 截断父批 + 两个 `ok` 的缩批子批：**谱系证明这次截断被答回来了**（r4 的真实形态）。
    answered_batches = [_batch("b-1", "truncated", depth=0, parent=None, call_id=anchor_call),
                        _batch("b-1a", "ok", depth=1, parent="b-1", call_id="c-1a"),
                        _batch("b-1b", "ok", depth=1, parent="b-1", call_id="c-1b")]

    def _state(*, sections, errors=None, rejections=None):
        return _NS(budget=ledger18, sections=sections, section_errors=errors or {},
                   proposal_set_rejections=rejections if rejections is not None else {},
                   rollback=None, mode=ACC.MODE_REAL)

    def _truncation_policy(state):
        return ACC.truncation_policy(state, narrator=narrator18, entailment=_NS(calls=[]), sentence=_NS(calls=[]))

    def _truncation_detail(state):
        return ACC._gate_a7_call_budget(state, narrator=narrator18, entailment=_NS(calls=[]),
                                        mode=ACC.MODE_REAL, run_dir=Path(".")).detail

    # ① **缩批成功、下游提案失败**（r4 现场）：该节没有 Draft，但截断**没有**进入结局。
    downstream_state = _state(
        sections={"company": _NS(llm_calls=1)},
        errors={"company": "PackWriterError: proposal_identity_unresolvable 第 0 行"},
        rejections=_payload(answered_batches, kind="proposal_identity_unresolvable"))
    downstream_policy = _truncation_policy(downstream_state)
    _down_row = (downstream_policy["recovered_downstream_failed"] or [{}])[0]
    check(len(downstream_policy["recovered_downstream_failed"]) == 1
          and downstream_policy["unrecovered"] == []
          and downstream_policy["recovered"] == [],
          f"缩批答回、该节此后因**其它原因**失败 ⇒ 进 `recovered_downstream_failed`，"
          f"**不**进 `unrecovered`（实际 recovered={len(downstream_policy['recovered'])}、"
          f"downstream={len(downstream_policy['recovered_downstream_failed'])}、"
          f"unrecovered={len(downstream_policy['unrecovered'])}）")
    check(_down_row.get("lineage_outcome") == "recovered_by_shrink"
          and _down_row.get("lineage_batch_id") == "b-1"
          and [b["batch_id"] for b in _down_row.get("lineage") or ()] == ["b-1", "b-1a", "b-1b"],
          f"判读必须逐条带上谱系（锚点批、子树成员、每个成员的状态与缩批深度），"
          f"否则「恢复」只是一句结论（实际 {_down_row!r}）")
    _down_detail = _truncation_detail(downstream_state)
    check("截断进入了最终结局" not in _down_detail,
          f"**r4 现场**：截断被缩批答回、该节此后因别的拒绝类型失败——A7 **不得**因此判红。"
          f"这正是不能用「该节有没有 Draft」当判据的理由（实际 detail={_down_detail[:200]!r}）")

    # ② 同一谱系 + 该节确实产出了 ⇒ 进 `recovered`（与 ① 只差「有没有 Draft」这一件事）。
    recovered_state = _state(
        sections={"company": _NS(draft=_NS(draft_id="d-1"), llm_calls=1)},
        rejections=_payload(answered_batches))
    recovered_policy = _truncation_policy(recovered_state)
    check(len(recovered_policy["recovered"]) == 1
          and recovered_policy["recovered_downstream_failed"] == []
          and recovered_policy["unrecovered"] == [],
          f"谱系证明被缩批答回、该节也产出了 ⇒ 进 `recovered`、不进 `unrecovered`"
          f"（实际 recovered={len(recovered_policy['recovered'])}）")
    recovered_gate = ACC._gate_a7_call_budget(recovered_state, narrator=narrator18,
                                              entailment=_NS(calls=[]), mode=ACC.MODE_REAL,
                                              run_dir=Path("."))
    check("截断进入了最终结局" not in recovered_gate.detail,
          f"救回的截断**不得**判红（判据是结局，不是「出现过截断」；"
          f"实际 detail={recovered_gate.detail[:200]!r}）")

    # ③ 反面对照：子树里**存在没有子孙的截断叶** ⇒ 缩批走到了尽头 ⇒ 未恢复（必须判红）。
    ended_batches = answered_batches + [
        _batch("b-1c", "truncated", depth=1, parent="b-1", call_id="c-1c")]
    ended_state = _state(sections={"company": _NS(draft=_NS(draft_id="d-1"), llm_calls=1)},
                         errors={"company": "PackWriterError: batch_truncated 缩批用尽"},
                         rejections=_payload(ended_batches, kind="batch_truncated"))
    ended_policy = _truncation_policy(ended_state)
    _ended_row = (ended_policy["unrecovered"] or [{}])[0]
    check(len(ended_policy["unrecovered"]) == 1
          and ended_policy["recovered"] == []
          and ended_policy["recovered_downstream_failed"] == []
          and _ended_row.get("lineage_outcome") == "ended_in_lineage"
          and "b-1c" in str(_ended_row.get("lineage_detail")),
          f"子树里有**没有子孙的截断叶** ⇒ `ended_in_lineage` ⇒ 未恢复——**即使该节产出了 "
          f"`SectionDraft`**（「有 Draft」不能覆盖一条走到了尽头的截断；实际 {_ended_row!r}）")
    _ended_detail = _truncation_detail(ended_state)
    check("截断进入了最终结局" in _ended_detail and "写作轴未被恢复" in _ended_detail,
          f"走到了尽头的截断**必须**进 A7 判据（实际 {_ended_detail[:200]!r}）")
    # 研究轴同期**没有**未恢复的截断 ⇒ 不得照印「该句柄自报 `reject_truncated=False` ⇒ 残缺
    # 正文已被返回并继续解析」。那是一句在写作轴单独判红时必然为假的反话（r4 的现场：
    # 写作轴未恢复、研究轴 0 次，红线条件成立，报告里于是出现了一句与研究侧事实相反的话）。
    check("自报 `reject_truncated=False`" not in _ended_detail
          and "研究轴**没有**未恢复的截断" in _ended_detail,
          f"研究轴 0 次未恢复时，说明必须按**实际读数**成形，不得写死那句反话"
          f"（实际 {_ended_detail[:300]!r}）")

    # ④ 谱系**不可核验**但该节产出了：只以产出为证，单列一桶，不为红。
    unverified_state = _state(sections={"company": _NS(draft=_NS(draft_id="d-1"), llm_calls=1)},
                              rejections={})
    unverified_policy = _truncation_policy(unverified_state)
    check(len(unverified_policy["recovered_unverified"]) == 1
          and unverified_policy["unrecovered"] == []
          and "谱系**不可核验**" in unverified_policy["recovered_unverified"][0]["why"]
          and "只以该节产出为证" in unverified_policy["recovered_unverified"][0]["why"],
          f"查不到谱系、但该节产出了 ⇒ 单列 `recovered_unverified` 并写明**靠的是哪一份证据**"
          f"（不得把「查不到」读成「恢复了」；实际 {unverified_policy['recovered_unverified']!r}）")
    check("截断进入了最终结局" not in _truncation_detail(unverified_state),
          "谱系不可核验本身不是结局缺口；该节产出仍在，不为红")

    # ⑤ 没产出 + 失败被 typed 记名 ⇒ 未恢复，且要写明是哪一类。
    typed_state = _state(sections={"company": _NS(llm_calls=1)},
                         errors={"company": "PackWriterError: batch_truncated 缩批用尽"})
    typed_policy = _truncation_policy(typed_state)
    check(len(typed_policy["unrecovered"]) == 1
          and "batch_truncated" in typed_policy["unrecovered"][0]["why"]
          and typed_policy["recovered_unverified"] == [],
          f"谱系不可核验且没有产出 ⇒ 按 fail-closed 进 `unrecovered`，并写明是哪一类"
          f"（实际 {typed_policy['unrecovered']!r}）")

    # ⑥ `/4` 载荷（没有 `parent_batch_id`）。**不能**一律读成「不可核验」：同一轮里还留着
    # `label`（家族 + 缩批深度）与保序的 `aspect_ids`，而对半切在这两样上留下唯一可核对的痕迹。
    # 真实 run r4 的两次截断正是这个形状——一律读成「不可核验」会把两条**已经答回**的截断记成
    # 结局缺口，那正是本批要修的错。但重建**有前提**：套不上时（⑥b/⑥c/⑥d）退回「不可核验」，
    # 不得猜一个父子关系来凑结论。
    def _legacy_batch(batch_id, status, *, depth, aspects, call_id=None, label=None):
        return {"batch_id": batch_id, "label": label or ("2/4" if depth == 0
                                                         else f"2/4·缩小{depth}"),
                "status": status, "shrink_depth": depth,
                "aspect_ids": list(aspects), "call_id": call_id}

    _parent_aspects = [f"company_business_model.a{i}" for i in range(4)]
    _r4_shaped = [
        _legacy_batch("b-1", "truncated", depth=0, aspects=_parent_aspects,
                      call_id=anchor_call),
        _legacy_batch("b-1a", "ok", depth=1, aspects=_parent_aspects[:2], call_id="c-1a"),
        _legacy_batch("b-1b", "ok", depth=1, aspects=_parent_aspects[2:], call_id="c-1b")]

    def _legacy_state(batches, *, kind="proposal_identity_unresolvable", errors=None, draft=False):
        return _state(sections={"company": _NS(llm_calls=1,
                                              **({"draft": _NS(draft_id="d-1")} if draft else {}))},
                      errors=errors or {"company": "PackWriterError: 第 0 行"},
                      rejections=_payload(batches, kind=kind))

    _legacy_policy = _truncation_policy(_legacy_state(_r4_shaped))
    _legacy_row = (_legacy_policy["recovered_downstream_failed"] or [{}])[0]
    check(_legacy_row.get("lineage_outcome") == "recovered_by_shrink"
          and _legacy_row.get("lineage_basis") == "reconstructed_by_label"
          and [b["batch_id"] for b in _legacy_row.get("lineage") or ()] == ["b-1", "b-1a", "b-1b"],
          f"**r4 形状**的 `/4` 载荷（无 `parent_batch_id`）：父子关系可由 label 家族、"
          f"`shrink_depth` 层级与 aspect 保序划分**唯一重建** ⇒ 必须判「已被缩批答回」，"
          f"并写明依据是重建而不是记录（实际 {_legacy_row!r}）")
    check(_legacy_policy["unrecovered"] == [] and _legacy_policy["recovered_unverified"] == [],
          f"重建得出的结论就是「已恢复」——不得又把它塞进 `unrecovered`，也不得降级成"
          f"「只以该节产出为证」（该节在这一组里根本没有产出；"
          f"实际 unrecovered={_legacy_policy['unrecovered']!r}）")
    check(_legacy_policy.get("lineage_reconstruction_version")
          == ACC.BATCH_LINEAGE_RECONSTRUCTION_VERSION
          and _legacy_policy.get("lineage_bases") == list(ACC.LINEAGE_BASES),
          f"重建规则的版本号必须落在读数里：规则一改，历史判读结论的可信范围随之改变"
          f"（实际 {_legacy_policy.get('lineage_reconstruction_version')!r}）")
    # 重建出的子树里若有**没有子孙的截断叶**，结论仍必须是「走到尽头」——重建不是
    # 「一律恢复」的开关（子批仍须构成父批的保序划分，只是其中一条自己也被截断且没有子孙）。
    _legacy_ended = [
        _legacy_batch("b-1", "truncated", depth=0, aspects=_parent_aspects, call_id=anchor_call),
        _legacy_batch("b-1a", "ok", depth=1, aspects=_parent_aspects[:2]),
        _legacy_batch("b-1b", "truncated", depth=1, aspects=_parent_aspects[2:])]
    check((_truncation_policy(_legacy_state(_legacy_ended))["unrecovered"] or [{}])[0]
          .get("lineage_outcome") == "ended_in_lineage",
          "重建出的子树里若有**没有子孙的截断叶**，结论仍必须是「走到尽头」")

    # ⑥b 反例：子批**各自**都是父批的保序子序列，但拼起来与父批不等（b-1b 与 b-1a 重叠）——
    # 这不是对半切留下的痕迹 ⇒ 整轮不可核验，且理由要说清是「拼不上」而不是「查不到」。
    _broken = [dict(b) for b in _r4_shaped]
    _broken[2]["aspect_ids"] = list(_parent_aspects[1:])
    _broken_row = (_truncation_policy(_legacy_state(_broken, draft=True))
                   ["recovered_unverified"] or [{}])[0]
    check(_broken_row.get("lineage_outcome") == "unverifiable"
          and _broken_row.get("lineage_basis") is None
          and "子批 aspect 拼起来与它自己不等" in str(_broken_row.get("lineage_detail")),
          f"子批 aspect 不构成父批的保序划分 ⇒ 整轮不可核验（不猜），理由须可复核"
          f"（实际 {_broken_row!r}）")

    # ⑥c 反例：同一轮里出现两条同家族同层的批 ⇒ 父批候选不唯一 ⇒ 不可核验（不得挑一个）。
    _ambiguous = [dict(b) for b in _r4_shaped]
    _ambiguous.append(_legacy_batch("b-1dup", "truncated", depth=0, aspects=_parent_aspects,
                                    call_id="c-dup"))
    _amb_row = (_truncation_policy(_legacy_state(_ambiguous, draft=True))
                ["recovered_unverified"] or [{}])[0]
    check(_amb_row.get("lineage_outcome") == "unverifiable"
          and "父批有 2 个候选" in str(_amb_row.get("lineage_detail")),
          f"父批候选不唯一 ⇒ 不得猜是哪一个，如实标「不可核验」（实际 {_amb_row!r}）")

    # ⑥d 反例：批记录缺 `label` / `shrink_depth` ⇒ 无法重建 ⇒ 不可核验。
    _no_depth = [dict(b) for b in _r4_shaped]
    del _no_depth[2]["shrink_depth"]
    _no_depth_row = (_truncation_policy(_legacy_state(_no_depth, draft=True))
                     ["recovered_unverified"] or [{}])[0]
    check(_no_depth_row.get("lineage_outcome") == "unverifiable"
          and "缺可判读的" in str(_no_depth_row.get("lineage_detail")),
          f"批记录缺可判读的 `label` / `shrink_depth` ⇒ 无法重建 ⇒ 不可核验"
          f"（实际 {_no_depth_row!r}）")

    # ⑥e 重建出的「已恢复」必须在 A7 判据里**带上依据**：红线成立时，读者要能分辨那句
    #     「已恢复」是记下来的还是重建出来的。红线由**同一节里另一条**走到尽头的截断提供
    #     （谱系能重建与谱系走到尽头是两件事，必须在同一份读数里同时可见）。
    with _Provider(stop_reason="max_tokens"):
        ledger_mixed = LB.LLMCallBudget(_policy(), enforce=False)
        mixed_narrator = PW.LlmNarrationClient(model=MODEL, max_tokens=8192)
        with _scoped_ledger(ledger_mixed), LB.section_scope("company"):
            for _ in range(2):
                try:
                    _narrate(mixed_narrator, PW.NARRATION_PROMPT_VERSION)
                except LLC.LLMTruncatedResponse:
                    pass
    _mixed_ids = [a.call_id for a in ledger_mixed.attempts if a.status == "error"]
    check(len(set(_mixed_ids)) == 2,
          f"夹具前提：两条截断尝试的 `call_id` 互不相同（实际 {_mixed_ids!r}）")
    _mixed_shaped = [dict(batch) for batch in _r4_shaped]
    _mixed_shaped[0]["call_id"] = _mixed_ids[0]
    _mixed_state = _NS(
        budget=ledger_mixed, sections={"company": _NS(llm_calls=2)},
        section_errors={"company": "PackWriterError: batch_truncated 缩批用尽"},
        proposal_set_rejections={"company": [
            {"rejection_kind": "proposal_identity_unresolvable", "batches": _mixed_shaped},
            {"rejection_kind": "batch_truncated",
             "batches": [_batch("b-2", "truncated", depth=0, parent=None,
                                call_id=_mixed_ids[1])]}]},
        rollback=None, mode=ACC.MODE_REAL)
    _mixed_policy = ACC.truncation_policy(_mixed_state, narrator=mixed_narrator,
                                          entailment=_NS(calls=[]), sentence=_NS(calls=[]))
    check(len(_mixed_policy["recovered_downstream_failed"]) == 1
          and len(_mixed_policy["unrecovered"]) == 1,
          f"同一节里「重建出的已恢复」与「走到尽头」必须**同时**出现在读数里"
          f"（实际 recovered_downstream_failed="
          f"{len(_mixed_policy['recovered_downstream_failed'])}、"
          f"unrecovered={len(_mixed_policy['unrecovered'])}）")
    _mixed_detail = ACC._gate_a7_call_budget(_mixed_state, narrator=mixed_narrator,
                                             entailment=_NS(calls=[]), mode=ACC.MODE_REAL,
                                             run_dir=Path(".")).detail
    check("reconstructed_by_label" in _mixed_detail,
          f"重建得出的「已恢复」必须在 A7 判据里写明依据（否则读者无法分辨这条结论建立在"
          f"记录还是重建上；实际 {_mixed_detail[:400]!r}）")

    # ⑦ 反例：**跨轮不得拼谱系**。同一个 `batch_id` 会在不同轮里各出现一次，跨轮连边会把两轮
    # 拼成一个假子树、把「走到了尽头」洗成「被答回」。下面两行同名 `b-1`：第 0 行是本次截断的
    # 真实那一轮（没有子批），第 1 行是另一轮。判读只能守在**同一轮**内。
    cross_round = {"company": [
        {"rejection_kind": "batch_truncated",
         "batches": [_batch("b-1", "truncated", depth=0, parent=None, call_id=anchor_call)]},
        {"rejection_kind": "proposal_identity_unresolvable",
         "batches": [_batch("b-1", "truncated", depth=0, parent=None, call_id="c-other"),
                     _batch("b-1a", "ok", depth=1, parent="b-1", call_id="c-1a")]}]}
    _cross_row = (_truncation_policy(_state(sections={"company": _NS(draft=_NS(draft_id="d-1"),
                                                        llm_calls=1)},
                                 rejections=cross_round))["unrecovered"] or [{}])[0]
    check(_cross_row.get("lineage_outcome") == "ended_in_lineage"
          and _cross_row.get("lineage_batch_id") == "b-1",
          f"跨轮不得拼谱系：本次截断那一轮里没有子批 ⇒ 走到尽头（fail-closed），"
          f"不得把另一轮的同名子批接上来洗成「已恢复」（实际 {_cross_row!r}）")
    # 研究轴（T3 收紧后的**生产**姿态）：适配器在**调用返回之前**拒收截断 ⇒ 残缺正文从未
    # 进入任何解析路径 ⇒ 不构成「截断进入了最终结局」，因此**不为红**；但它必须逐条留在
    # 同一个可见面上（`truncated_calls` / `research_fail_closed`），不得因为不为红就消失。
    research_stub = _NS(calls=[{"finish_reason": "max_tokens", "call_id": "r-1",
                                "prompt_version": "research-answer", "kind": "answer"}],
                        reject_truncated=True)
    research_policy = ACC.truncation_policy(quiet_state, narrator=quiet_narrator,
                                            entailment=_NS(calls=[]), research=research_stub)
    check(len(research_policy["research_fail_closed"]) == 1
          and research_policy["research_fail_closed"][0]["axis"] == "research"
          and research_policy["research_fail_closed"][0]["reject_truncated"] is True
          and research_policy["research_unrecovered"] == []
          and research_policy["truncated_calls"][0]["client"] == "research",
          f"研究轴拒收的截断进同一可见面：逐条标出 `axis=research` 与 `reject_truncated=True`，"
          f"并**不**进 `research_unrecovered`（实际 "
          f"fail_closed={research_policy['research_fail_closed']!r}、"
          f"unrecovered={research_policy['research_unrecovered']!r}）")
    research_gate = ACC._gate_a7_call_budget(
        _NS(budget=quiet_ledger, sections={"company": _NS(llm_calls=1)}, section_errors={},
            proposal_set_rejections={}, rollback=None, mode=ACC.MODE_REAL),
        narrator=quiet_narrator, entailment=_NS(calls=[]), research=research_stub,
        mode=ACC.MODE_REAL, run_dir=Path("."))
    check("截断进入了最终结局" not in research_gate.detail,
          f"**拒收**的截断没有进入任何内容，**不得**判红：判据是结局而不是「出现过截断」"
          f"（实际 {research_gate.detail[:200]!r}）")
    check(research_gate.evidence["truncation_policy"]["research_fail_closed"]
          == research_policy["research_fail_closed"],
          "A7 证据里的研究轴读数与策略函数同一份（判据可复核，不是一句结论）")
    # 反例（**必须留住**）：某个句柄自报不拒收 ⇒ 残缺正文会被返回并继续解析 ⇒ 必须判红。
    # 这条是 T3 收紧的真正防线：生产适配器今天落在 fail-closed 那一档，但判据不能因此变成
    # 「研究轴永不判红」——将来任何一处退回 `reject_truncated=False`，红必须回来。
    lax_stub = _NS(calls=[{"finish_reason": "max_tokens", "call_id": "r-2",
                           "prompt_version": "research-answer", "kind": "answer"}],
                   reject_truncated=False)
    lax_policy = ACC.truncation_policy(quiet_state, narrator=quiet_narrator,
                                       entailment=_NS(calls=[]), research=lax_stub)
    check(len(lax_policy["research_unrecovered"]) == 1
          and lax_policy["research_unrecovered"][0]["reject_truncated"] is False
          and lax_policy["research_fail_closed"] == [],
          f"反例：句柄自报不拒收 ⇒ 残缺正文已被返回并继续解析 ⇒ 进 `research_unrecovered`"
          f"（实际 {lax_policy['research_unrecovered']!r}）")
    lax_gate = ACC._gate_a7_call_budget(
        _NS(budget=quiet_ledger, sections={"company": _NS(llm_calls=1)}, section_errors={},
            proposal_set_rejections={}, rollback=None, mode=ACC.MODE_REAL),
        narrator=quiet_narrator, entailment=_NS(calls=[]), research=lax_stub,
        mode=ACC.MODE_REAL, run_dir=Path("."))
    check(lax_gate.status == "fail" and "截断进入了最终结局" in lax_gate.detail
          and "研究轴" in lax_gate.detail,
          f"反例：自报不拒收的研究轴截断**必须**仍进 A7 判据（实际 {lax_gate.detail[:200]!r}）")
    # 生产适配器**自己**必须落在拒收那一档：上面两条都读 `reject_truncated` 这个属性，因此
    # 属性必须与真实行为一致，否则整组判据只是自证。
    from harness import runtime as HRT
    check(HRT.RealResearchLLM.reject_truncated is True,
          "生产研究适配器必须自报 `reject_truncated=True`（属性即判据的输入，必须反映真实行为）")
    # 句柄取不到 ⇒ 如实写「没数过」，**不**写「0 次」（未执行不得记为零命中）。
    missing = ACC.truncation_policy(quiet_state, narrator=quiet_narrator,
                                    entailment=_NS(calls=[]), sentence=_NS(calls=[]))
    check(missing["research_client_available"] is False
          and "没数过" in str(missing["research_axis_note"])
          and missing["research_unrecovered"] == []
          and missing["research_fail_closed"] == [],
          "没拿到研究侧句柄时，研究轴标「没数过」而不是「0 次」")
    check("没数过" in str(quiet_policy["research_axis_note"]),
          "对照面：有句柄时那条「没数过」的说明必须消失（否则它就成了永远挂着的一句话）")

    # ------------------------------------------------------------------
    # 8. cited 链的上限（`cited-budget-3`）：公司节 + 财务节 × (1 写 + 1 审) = 整轮 4，
    #    **返修本批未获批**，重试 0
    # ------------------------------------------------------------------
    # 这组钉的是「本批获批了什么额度」这件事本身，因此它读的是**生产声明**
    # （`sections.cited_budget`），不是本模块另写的一组数：换个地方再写一遍上限，
    # 只会让两处各自漂移而没人发现。
    from config import LLM_MODEL as PROJECT_WRITER_MODEL
    from sections import cited_budget as CB
    from sections import cited_review as CR
    from sections import cited_rework as CRW
    from sections import cited_writer as CW
    approved = str(PROJECT_WRITER_MODEL or "").strip()
    cited = CB.cited_call_budget_gate(approved_model=approved)
    CB.assert_caps_expressed(cited.policy)
    check(CB.CITED_BUDGET_SECTIONS == ("company", "financial")
          and CB.CITED_RUN_MAX_ATTEMPTS == 4 and CB.CITED_AUTOMATIC_RETRIES == 0
          and CB.CITED_WRITE_MAX_PER_SECTION == 1
          and CB.CITED_REVIEW_MAX_PER_SECTION == 1
          and CB.CITED_WRITE_MAX_TOTAL == 2 and CB.CITED_REVIEW_MAX_TOTAL == 2,
          "上限与节集合都是**模块常量**：重试额度、每节额度与受管节都不在命令行上可放大")
    check(CB.cited_rework_approved() is False
          and CB.CITED_REWORK_MAX_PER_SECTION == 0 and CB.CITED_REWORK_MAX_TOTAL == 0,
          "**返修本批未获批**：说「未获批」与说上限为 0 必须是同一件事——真正让它发不出去的"
          "是它不在类别集里（`:func:`cited_rework_approved` 读的就是那个下标）")
    check(cited.policy.axes == () and dict(cited.policy.category_axis) == {},
          "**没有额外预算轴**：有轴就会多出一层不受这些上限约束的额度")
    check(set(CB.cited_prompt_versions())
          == {CW.CITED_WRITER_PROMPT_VERSION, CR.CITED_REVIEW_PROMPT_VERSION},
          "只有写作与审阅两个 prompt 版本被登记：第四个调用点接进来时会在构造期停住，"
          "而不是悄悄落到某个默认类别上（**返修**这一版根本没有位置）")
    check(CRW.CITED_REWORK_PROMPT_VERSION not in CB.cited_prompt_versions(),
          "反例：返修的 prompt 版本**不在**归属表里——本批发不出返修请求这件事，"
          "在归属这一层就是可判定的，不依赖调用点是否记得关掉它")
    #: 两个节都在获批集合里；`assert_sections_approved` 是**事前**那道门，未获批的节必须在
    #: 本批的**入口**就拒，而不是等跑到那一节。
    check(CB.assert_sections_approved(("company", "financial")) == ("company", "financial"),
          "正例：公司节与财务节都通过节门（本批两个都在获批集合里）")
    try:
        CB.assert_sections_approved(("company", "industry"))
    except CB.CitedBudgetError as exc:
        check("industry" in str(exc),
              f"反例：未获批的节在**第一个请求之前**被拒且拒得有名有姓（{str(exc)[:60]}…）")
    else:
        raise AssertionError("未获批的节必须被事前拒绝（本批没有批它的任何一次调用）")
    sent: list[str] = []
    with _scoped_ledger(cited):
        for section_id in CB.CITED_BUDGET_SECTIONS:
            for prompt_version in (CW.CITED_WRITER_PROMPT_VERSION,
                                   CR.CITED_REVIEW_PROMPT_VERSION):
                sent.append(_call(prompt_version=prompt_version, section_id=section_id,
                                  model=approved))
        full = cited.summary()
        check(len(sent) == 4 and full["attempt_total"] == 4
              and _count_in(full["attempts_by_category"],
                            CB.CATEGORY_CITED_PROSE_WRITING) == 2
              and _count_in(full["attempts_by_category"],
                            CB.CATEGORY_CITED_PROSE_REVIEW) == 2
              and _count_in(full["attempts_by_section"], "company") == 2
              and _count_in(full["attempts_by_section"], "financial") == 2,
              f"正例：两节各写一次、审一次 ⇒ 整轮**恰好 4 次**且逐节逐类可查"
              f"（实测 {full['attempt_total']} 次 / {full['attempts_by_category']} / "
              f"{full['attempts_by_section']}）")
        from llm import budget as _LB
        # 两个类别都**不在** `_LB.CATEGORIES` 这份既有写作类别登记里——这正是它曾被读丢的那个缝。
        # 「按类别」这一栏必须照样看得见它们，且与整轮总数、逐节记录三方自洽：一份 summary
        # 内部对不上，读产物的人只会看到「发了 4 次、按类别 0 次」这种自相矛盾的数字。
        check(CB.CATEGORY_CITED_PROSE_WRITING not in _LB.CATEGORIES
              and CB.CATEGORY_CITED_PROSE_REVIEW not in _LB.CATEGORIES
              and set(full["attempts_by_category"])
              == {CB.CATEGORY_CITED_PROSE_WRITING, CB.CATEGORY_CITED_PROSE_REVIEW},
              "反例：类别**不在**既有 `CATEGORIES` 登记里（本批两个 cited 类别都不在），"
              "「按类别」读视图仍须列出全部已发生的类别——按固定元组取交集会让这一栏"
              f"静默归零（实测 {full['attempts_by_category']} / 登记表 {_LB.CATEGORIES}）")
        check(sum(full["attempts_by_category"].values()) == full["attempt_total"]
              and sum(sum(row.values()) for row in full["attempts_by_section"].values())
              == full["attempt_total"],
              f"按类别 / 按节的合计都必须等于整轮 {full['attempt_total']} 次："
              "同一份 summary 的三处数字互为校验，任一处漏计都读得出来"
              f"（实测按类别 {sum(full['attempts_by_category'].values())} / "
              f"按节 {sum(sum(r.values()) for r in full['attempts_by_section'].values())}）")
        before = full["attempt_total"]
        try:
            _call(prompt_version=CW.CITED_WRITER_PROMPT_VERSION, section_id="company",
                  model=approved)
        except _LB.LLMCallBudgetExceeded as exc:
            limit_kind = exc.limit_kind
        else:
            raise AssertionError("第 5 次调用必须被拒（本批只批了 4 次、每类每节各 1）")
        after = cited.summary()
        check(after["attempt_total"] == before and after["refusal_total"] == 1,
              f"反例：第 5 次调用**没有被记账**（尝试仍为 {after['attempt_total']} 次），"
              f"且留下恰好 1 条拒绝记录——它在 `reserve` 处就被拦下，请求没有发出去")
        check(limit_kind in {"本节上限", "本类总上限", "整轮总上限"},
              f"拒绝给的是**封闭的限额名**（实测 {limit_kind!r}），不是一句泛化措辞："
              "读者要能判断撞的是哪一层额度")
        refusal = after["refusals"][0]
        check(refusal["attempt_ordinal_if_sent"] == before + 1
              and refusal["category"] == CB.CATEGORY_CITED_PROSE_WRITING
              and refusal["section_id"] == "company"
              and refusal["prompt_version"] == CW.CITED_WRITER_PROMPT_VERSION
              and refusal["detail"],
              "拒绝记录说的是「若发出去会是第几次」，并逐项写明**归属**（类别 / 节 / "
              "prompt 版本）与一句明细：没发出去的调用不得看起来像发过"
              f"（实测 {sorted(refusal)}）")
        #: 返修这一版**不在归属表里**，所以它连「撞上限」的机会都没有：请求在归属这一步就
        #: 被拒，且拒得与「额度用完」分得开——前者是「这一类本批没批」，后者是「批了但用完」。
        try:
            _call(prompt_version=CRW.CITED_REWORK_PROMPT_VERSION, section_id="company",
                  model=approved)
        except _LB.LLMCallAttributionError as exc:
            attribution_missing = str(exc)
        except _LB.LLMCallBudgetExceeded as exc:  # pragma: no cover - 反例方向
            raise AssertionError(
                f"返修应当是**归属**失败（本版归属表里没有它），不是撞上限：{exc}") from exc
        else:
            raise AssertionError("返修在本版必须发不出去")
        check(CRW.CITED_REWORK_PROMPT_VERSION in attribution_missing
              or "归属" in attribution_missing,
              f"反例：返修筑在**归属**这一步就被拒（本版类别集与归属表里都没有它），"
              f"读数是「这一类本批没批」而不是「额度用完」（实测 {attribution_missing[:80]}…）")
        #: 归属失败**也**留一条拒绝记录（`reason="归属不清"`），但它与「撞上限」在账本上
        #: **分得开**：`category` 是空串而不是返修类别。若它被记成返修类别的超额，读者会以为
        #: 本批批过返修、只是额度用完了——那是与事实相反的读数。
        attribution_refusal = cited.summary()["refusals"][-1]
        check(cited.summary()["refusal_total"] == 2
              and attribution_refusal["reason"] == "归属不清"
              and attribution_refusal["category"] == ""
              and attribution_refusal["prompt_version"] == CRW.CITED_REWORK_PROMPT_VERSION,
              "返修的归属失败留下的是一条 `reason=归属不清` 的拒绝记录，且类别为空——"
              "它**不得**被记成返修类别的超额（那会让读者以为本批批过返修）"
              f"（实测 {attribution_refusal}）")
    check(cited.summary()["attempt_total"] == 4,
          "账本在 `uninstall()` 之后仍可读（离线读回与事后对账读的是同一份账）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

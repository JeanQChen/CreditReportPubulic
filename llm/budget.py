"""共享 LLM 客户端的**事前**调用预算门（M930-3 任务二：调用预算门窄范围修复）。

位置与边界
----------
所有真实请求都经由 `llm.client.chat_with_usage`；本模块提供**在发请求之前**就拒绝第 N+1 次
尝试的那道门。它只做三件事：**归属**（这次请求属于哪一类、哪一节）、**上限**（该类 / 该节 /
整轮三个上限）、**记账**（一次尝试一条记录，含被拒的那次）。

它**不是**第二套 LLM runtime：没有传输、没有重试、没有 prompt、没有模型选择。传输仍然只有
`llm.client` 一份实现；本模块只在它的入口前后各插一个钩子。

三个必须分清的量
----------------
旧口径把「已批准的写作/组织预算」当成「全链预算」，于是三个不同的量被混成一个：

* **客户端实际发起的请求尝试**：进入 `chat_with_usage` 并发往 provider 的次数。失败也占一次
  ——本门在发请求**之前**记账，所以「失败不计数」在结构上不可表达；
* **成功返回**：`settle(..., status="ok")` 的那些；
* **归属**：每次尝试恰属于一个 `(category, section_id)`。归属不清就是拒绝，不是「记到别的
  类别里」。共用客户端（蕴含门复用叙述客户端）因此仍只有**一条**记录——记账点在共享入口，
  不在各调用点。

类别是**封闭词表**：`CATEGORIES` 之外没有第三个值；`prompt_version → category` 的映射由
组合根显式登记（不在本模块里 import `sections.*`，避免反向依赖）。未登记的 `prompt_version`
一律拒绝——「新调用点悄悄绕过预算」在结构上不可表达。

上限的三个层级
--------------
`max_attempts_per_section`（本节）/ `max_attempts_total`（本类）/ `total_max_attempts`（整轮），
外加每次请求的 `max_output_tokens` 与其类别批准模型。任一层缺批准（`approved is False`）即
拒绝**所有**真实请求：未获批准的量不得靠「没设上限」蒙混成无上限。

`enforce=True` 与「有未批准的量」在构造时就不相容（`ValueError`）——「拿着未批准的数字强制
执行」不可表达。

两条预算轴（M930-3 业务纵链补充门 / `CROSS_DOCUMENT_JOINT_RETRIEVAL_PLAN.md` §0.3.1）
------------------------------------------------------------------------------------
研究与写作**共用同一份传输**，但走**两条不同的预算轴**：

* `AXIS_WRITING`：写作类别（叙述 / 逐候选蕴含 / 可选章级评估 / 最终句语义核验），上限仍逐类/
  逐节受 `CategoryCap` 管，整轮 `total_max_attempts` **只数写作轴的尝试**；
* `AXIS_RESEARCH`：研究阶段三个 prompt 版本经 `category_axis` 映射到**同一个** `AxisCap`，
  按 topic（`research_scope`）与整个研究轴分别计量。**三个研究类别共用同一个轴上限**——
  若给每类各发一份，等于把研究侧上限悄悄乘 3。**逐 topic 的上限可以各不相同**
  （`AxisCap.per_scope_max_attempts`）：研究侧的正确形式是「每 aspect 上界 × 该 topic 的
  aspect 数」，各 topic 的 aspect 数本来就不相等，用一个共享值表达就变成了按 topic 大小
  分配配额。「三个类别共用一轴」讲的是**类别之间**不各发一份，与「逐 topic 可不同」不矛盾。

研究轴**独立**上限且**独立**批准状态。`axes == ()` 时
`axis_of()` 对写作类别缺省返回 `AXIS_WRITING`，`cap_for`/`reserve`/`total_max_attempts`
的行为**逐字保持**——因此本模块的既有自检与既有 eval 夹具在旧政策下不需要改动。

「未登记的研究 prompt」「没有 topic 作用域的研究请求」「把既有写作类别改挂到别的轴」都在
**发出请求之前**拒绝：这三件事都会让「哪一类花了多少次」读错，而读错的账本比没有账本更糟。

CLI:
  python -m llm.budget --self-check     # 确定性自检（不发任何真实请求）
"""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator, Mapping, Sequence

#: 类别封闭词表。新增一个类别必须同时给出它的批准依据（`CategoryCap.basis`），
#: 否则「哪一类花了多少钱」就只剩一个数字、没有出处。
CATEGORY_NARRATION = "narration"
CATEGORY_CLAIM_ENTAILMENT = "claim_entailment"
CATEGORY_SECTION_LLM_EVALUATOR = "section_llm_evaluator"
#: 最终句语义门 B（`nsfid-1`）是 2026-09-27 批次新增的**真实调用点**（指令 D 第四项），
#: 因此它必须有自己的类别：把它记进 `narration`（写作）或 `claim_entailment`（逐候选蕴含）
#: 都会让「哪一类花了多少次」读错——B 核的是**最终句**，不是候选、不是正文生成。
CATEGORY_FINAL_SENTENCE_FIDELITY = "final_sentence_fidelity"
#: 研究阶段三个类别，与 `harness/runtime.py::LLM_CATEGORY_ACTION/ANSWER/ENTAILMENT` 一名对一。
#: 它们**不出现在** `CallBudgetPolicy.categories`（那份列表只装写作 `CategoryCap`，且被既有
#: 断言按「和 == 整轮上限」钉死），只经 `category_axis` 落到共享的 `AXIS_RESEARCH`。
CATEGORY_RESEARCH_ACTION = "research_action"
CATEGORY_RESEARCH_ANSWER = "research_answer"
CATEGORY_RESEARCH_ENTAILMENT = "research_entailment"
#: 这份元组只是**既有写作类别的登记**，不是「可以出现哪些类别」的判据：类别由每份政策自己的
#: `prompt_versions` 归属表声明（例：`cited_prose_writing`/`cited_prose_review` 就不在这里）。
#: 因此读视图一律按已记账的尝试归类，**不得**拿本元组去过滤（见 `category_counts`）。
CATEGORIES = (CATEGORY_NARRATION, CATEGORY_CLAIM_ENTAILMENT,
              CATEGORY_SECTION_LLM_EVALUATOR, CATEGORY_FINAL_SENTENCE_FIDELITY,
              CATEGORY_RESEARCH_ACTION, CATEGORY_RESEARCH_ANSWER,
              CATEGORY_RESEARCH_ENTAILMENT)
#: 研究侧三类：词表的一部分，但**不是** `categories` 的成员（见上）。
RESEARCH_CATEGORIES = (CATEGORY_RESEARCH_ACTION, CATEGORY_RESEARCH_ANSWER,
                       CATEGORY_RESEARCH_ENTAILMENT)

#: 预算轴。写作轴沿用既有逐类/逐节上限；研究轴按 topic 与整轴计量。
AXIS_WRITING = "writing"
AXIS_RESEARCH = "research"
AXES = (AXIS_WRITING, AXIS_RESEARCH)

#: 作用域种类。`section:<id>`（写作，逐节）与 `topic:<id>`（研究，逐 topic）互不相同：
#: 研究请求缺 topic 作用域即事前拒绝，不得记到某一节名下。
SCOPE_KIND_SECTION = "section"
SCOPE_KIND_TOPIC = "topic"
TOPIC_SCOPE_PREFIX = "topic:"

#: 尝试状态。`reserved` = 已记账但请求尚未返回（失败请求停在这里或转 `error`）。
STATUS_RESERVED = "reserved"
STATUS_OK = "ok"
STATUS_ERROR = "error"


def scope_kind_of(scope: str) -> str:
    """作用域字符串 → 作用域种类。只按显式前缀判定，不猜。"""
    return (SCOPE_KIND_TOPIC if str(scope or "").startswith(TOPIC_SCOPE_PREFIX)
            else SCOPE_KIND_SECTION)


class LLMCallBudgetError(RuntimeError):
    """预算门自身的事前拒绝/拒绝理由的基类。**不是**一次调用结果。"""


class LLMCallBudgetExceeded(LLMCallBudgetError):
    """第 N+1 次尝试：请求**未发出**即被拒（未超限的调用不受影响）。"""

    def __init__(self, *, category: str, section_id: str, limit_kind: str,
                 limit: int, observed: int, prompt_version: str | None = None) -> None:
        self.category = category
        self.section_id = section_id
        self.limit_kind = limit_kind
        self.limit = limit
        self.observed = observed
        self.prompt_version = prompt_version
        super().__init__(
            f"类别 {category!r} 的{limit_kind}已达上限 {limit}（本次为第 {observed + 1} 次）："
            f"请求在发出之前被拒绝（section={section_id!r}，prompt_version={prompt_version!r}）")


class LLMCallAttributionError(LLMCallBudgetError):
    """归属不清（未登记的 prompt_version / 没有 section 作用域）：拒绝，不猜。"""


@dataclass(frozen=True)
class CategoryCap:
    """一个类别的已批准上限。`approved=False` ⇒ 该类别的任何真实请求都不得发出。"""

    category: str
    max_attempts_per_section: int | None
    max_attempts_total: int | None
    max_output_tokens: int | None
    approved: bool
    basis: str = ""

    def to_dict(self) -> dict:
        return {"category": self.category,
                "max_attempts_per_section": self.max_attempts_per_section,
                "max_attempts_total": self.max_attempts_total,
                "max_output_tokens": self.max_output_tokens,
                "approved": self.approved, "basis": self.basis}


@dataclass(frozen=True)
class AxisCap:
    """一条预算轴的已批准上限（研究轴用）。

    `max_attempts_per_scope` 是**逐 topic** 的上限（不是每个研究类别各一份），
    `max_attempts` 是**整条轴**的上限。`approved=False` ⇒ 该轴上任何真实请求都不得发出。

    `per_scope_max_attempts`（可选）把「逐 topic」从**一个值**改成**逐 topic 各一个值**：
    `topic:<id> → 上限`。有了它，`max_attempts_per_scope` 只作**缺省**——没有登记的 scope
    退回它，登记了的按自己的值判。之所以需要这一层：研究侧上限的正确形式是
    `每个 aspect 的上界 × 该 topic 的 aspect 数`，而各 topic 的 aspect 数**不相等**
    （company_business 18 / industry_scale_cycle 4）。用一个共享值表达，等于给 4 个 aspect 的
    topic 9 次/aspect、给 18 个 aspect 的 topic 2 次/aspect —— 那不是一个上限，是一次
    **按 topic 大小分配的配额**，读产物的人却只会看到一个数。

    `per_scope_max_attempts` 为 `None` 时行为与从前**逐字一致**（只按 `max_attempts_per_scope`），
    因此既有政策与既有 eval 夹具不受影响。
    """

    axis: str
    max_attempts_per_scope: int | None
    max_attempts: int | None
    approved: bool
    basis: str = ""
    #: `'topic:<id>' → 该 scope 自己的上限`。**用 `None` 而不是 `{}` 作缺省**：冻结 dataclass 的
    #: 可变缺省是共享状态。`Mapping` 而非 `dict`，因为它的内容参与「哪个上限生效」的判定。
    per_scope_max_attempts: Mapping[str, int] | None = None

    def limit_for_scope(self, scope: str) -> int | None:
        """该 scope 生效的上限：登记了就用登记的，否则退回单值缺省。"""
        if self.per_scope_max_attempts:
            own = self.per_scope_max_attempts.get(str(scope))
            if own is not None:
                return int(own)
        return None if self.max_attempts_per_scope is None \
            else int(self.max_attempts_per_scope)

    def to_dict(self) -> dict:
        return {"axis": self.axis,
                "max_attempts_per_scope": self.max_attempts_per_scope,
                "max_attempts": self.max_attempts,
                "approved": self.approved, "basis": self.basis,
                "per_scope_max_attempts": (None if self.per_scope_max_attempts is None
                                           else dict(sorted(self.per_scope_max_attempts.items())))}


@dataclass(frozen=True)
class CallBudgetPolicy:
    """一次运行的预算政策：类别上限 + 整轮上限 + 批准模型 + prompt 归属表 + 可选预算轴。"""

    policy_version: str
    approved_model: str | None
    categories: tuple[CategoryCap, ...]
    #: `prompt_version → category`。归属表由组合根登记（本模块不 import 上层模块）。
    prompt_versions: Mapping[str, str]
    total_max_attempts: int | None = None
    total_approved: bool = False
    #: 额外预算轴。**默认空**：空元组时 `axis_of` 对写作类别缺省 `AXIS_WRITING`，
    #: 因此旧政策的行为逐字不变（研究类别此时也无法归属，见 `category_of`）。
    axes: tuple[AxisCap, ...] = ()
    #: `category → axis`。用 `field(default_factory=dict)` 而非可变 `{}` 默认值：
    #: 冻结 dataclass 里的可变默认值是共享状态，一个实例的写入会污染另一个。
    category_axis: Mapping[str, str] = field(default_factory=dict)

    @property
    def writing_categories(self) -> frozenset:
        return frozenset(c.category for c in self.categories)

    def axis_cap_for(self, axis: str) -> AxisCap:
        for cap in self.axes:
            if cap.axis == axis:
                return cap
        raise LLMCallAttributionError(
            f"预算轴 {axis!r} 没有登记上限：该轴不得发出请求（fail-closed）")

    def axis_of(self, category: str) -> str:
        """类别 → 预算轴。归属不清即拒绝（不猜、不默认到某一轴）。"""
        mapping = self.category_axis
        if category in self.writing_categories:
            mapped = mapping.get(category, AXIS_WRITING)
            if mapped != AXIS_WRITING:
                raise LLMCallAttributionError(
                    f"既有写作类别 {category!r} 被登记到轴 {mapped!r}：写作类别的轴不得改变"
                    "（改轴等于悄悄改写整轮写作总额的语义，fail-closed）")
            return AXIS_WRITING
        mapped = mapping.get(category)
        if mapped is None:
            raise LLMCallAttributionError(
                f"类别 {category!r} 没有登记预算轴：未映射的类别不得发出请求（fail-closed）")
        self.axis_cap_for(mapped)
        return mapped

    def cap_for(self, category: str) -> "CategoryCap | AxisCap":
        """写作类别返回它自己的 `CategoryCap`；研究类别返回**共享的** `AxisCap`。

        研究类别**不**各自持有一份 `CategoryCap`：三个研究 prompt 共用同一个轴上限，
        逐类各发一份会把研究侧上限悄悄乘 3。
        """
        axis = self.axis_of(category)
        if axis == AXIS_WRITING:
            for cap in self.categories:
                if cap.category == category:
                    return cap
            raise LLMCallAttributionError(
                f"类别 {category!r} 没有登记上限：该类别不得发出请求（fail-closed）")
        return self.axis_cap_for(axis)

    def category_of(self, prompt_version: str | None) -> str:
        category = self.prompt_versions.get(str(prompt_version))
        if category is None:
            raise LLMCallAttributionError(
                f"prompt_version={prompt_version!r} 未登记归属类别："
                "无法归属的请求不得发出（新调用点必须显式登记，不得靠默认值蒙混）")
        if category not in self.writing_categories | frozenset(self.category_axis.keys()):
            raise LLMCallAttributionError(
                f"prompt_version={prompt_version!r} 登记到未定义类别 {category!r}")
        # 类别存在还不够：它挂的轴也必须是已定义的轴，否则「记到哪条轴」无从读回。
        self.axis_of(category)
        return category

    @property
    def approvable(self) -> tuple[str, ...]:
        """尚未获批准的量（人读清单）：非空即整轮不得以强制模式运行。"""
        missing = [c.category for c in self.categories if not c.approved]
        missing.extend(a.axis for a in self.axes if not a.approved)
        if not self.total_approved:
            missing.append("run_total")
        return tuple(missing)

    def to_dict(self) -> dict:
        return {"policy_version": self.policy_version,
                "approved_model": self.approved_model,
                "total_max_attempts": self.total_max_attempts,
                "total_approved": self.total_approved,
                "categories": [c.to_dict() for c in self.categories],
                "prompt_versions": dict(sorted(self.prompt_versions.items())),
                "axes": [a.to_dict() for a in self.axes],
                "category_axis": dict(sorted(self.category_axis.items())),
                "unapproved": list(self.approvable)}


@dataclass
class BudgetAttempt:
    """一次请求尝试的账。记账发生在**发请求之前**，因此它不代表「已成功」。"""

    ordinal: int
    category: str
    section_id: str
    prompt_version: str | None
    model: str | None
    max_tokens: int | None
    call_id: str
    status: str = STATUS_RESERVED
    error: str = ""
    #: 该尝试所属的预算轴与作用域种类。**记账时就定**，因此「这次算在谁头上」可独立读回，
    #: 不必事后按类别名反推（反推会在类别改名或换轴时静默读错）。
    axis: str = AXIS_WRITING
    scope_kind: str = SCOPE_KIND_SECTION

    def to_dict(self) -> dict:
        return {"ordinal": self.ordinal, "category": self.category,
                "section_id": self.section_id, "prompt_version": self.prompt_version,
                "model": self.model, "max_tokens": self.max_tokens,
                "call_id": self.call_id, "status": self.status, "error": self.error,
                "axis": self.axis, "scope_kind": self.scope_kind}


class LLMCallBudget:
    """一次运行的调用账本 + 事前上限。**不是**线程安全的：一次运行一条链。

    `enforce=True` 有一条第 0 层的门槛：**政策里不得有任何未获批准的量**（连整轮总数也算）。
    这是「未批准就不发请求」的最早落点——比请求期更早，因此不存在「先花掉已批准的额度，再
    卡在未批准的那一类上」这种半轮真调用。想表达「本类本轮不许发」，就用 `approved=True` +
    上限 0（而不是不批准），这样「不许发」是一个**已批准的**决定（有出处、可复核）。
    """

    def __init__(self, policy: CallBudgetPolicy, *, enforce: bool) -> None:
        if enforce:
            missing = policy.approvable
            if missing:
                raise ValueError(
                    f"以强制模式安装预算门，但有未获批准的量 {list(missing)}："
                    "未批准的上限不得靠「不设限」蒙混执行（fail-closed）")
            if not policy.approved_model:
                raise ValueError("以强制模式安装预算门必须声明已批准模型")
        self.policy = policy
        self.enforce = bool(enforce)
        self.attempts: list[BudgetAttempt] = []
        self.refusals: list[dict] = []

    # -- 事前门 --------------------------------------------------------------
    def reserve(self, *, call_id: str, prompt_version: str | None, model: str | None,
                max_tokens: int | None, section_id: str) -> BudgetAttempt:
        """归属 → 轴 → 上限 → 记账。任何一条不成立即在此抛出：请求**不会**发出。

        两条轴的上限**互不消耗**：写作分支的整轮 `total_max_attempts` 只数写作轴的尝试，
        研究分支数同一 topic 的研究尝试与整个研究轴的尝试。用 `len(self.attempts)` 一把梭
        会让研究调用吃掉已批准的写作额度——那正是本项要修的读错。
        """
        category = ""
        axis = ""
        scope = str(section_id or "")
        kind = scope_kind_of(scope)
        try:
            category = self.policy.category_of(prompt_version)
            axis = self.policy.axis_of(category)
            if not scope:
                raise LLMCallAttributionError(
                    f"类别 {category!r} 的请求没有作用域：归属不清的尝试不得计入"
                    "任何一节（fail-closed）")
            if axis == AXIS_RESEARCH and kind != SCOPE_KIND_TOPIC:
                # 研究按 topic 计量。没有 topic 作用域的研究请求若不是在这里拒绝，就会以
                # 「某节发生了研究调用」的形态记进账本——一个读起来像写作侧的数字。
                raise LLMCallAttributionError(
                    f"研究类别 {category!r} 的请求没有 topic 作用域（当前作用域={scope!r}）："
                    "研究按 topic 计量，归属不清的尝试不得计入任何一节（fail-closed）")
            if axis == AXIS_WRITING and kind != SCOPE_KIND_SECTION:
                # 反向同理：写作按**节**计量。一条落在 `topic:<id>` 作用域里的写作调用若被放行，
                # 会以「某个 topic 发生了写作调用」的形态落到写作轴上——于是「这一节花了多少次」
                # 读错，而错的方向恰好是「漏记」。两条轴的作用域互不通用（fail-closed）。
                raise LLMCallAttributionError(
                    f"写作类别 {category!r} 的请求落在 topic 作用域（当前作用域={scope!r}）："
                    "写作按节计量，记到 topic 名下会让「哪一节花了多少次」读错（fail-closed）")
            # 「未获批准」不在这里判，而是在**构造期**（见 `__init__`）：强制模式与任何未批准的
            # 量在结构上不相容。这是刻意的——若留到请求期才拒，一个只批准了叙述类的政策会先
            # 把已批准的额度花光（门前提案、组织都发了），再在蕴含那一次上撞住，于是「半轮已
            # 烧掉的真实调用」成为可能。未批准 = 整轮不发（fail-closed 的最早那一层）。
            if self.enforce:
                if self.policy.approved_model and str(model) != str(self.policy.approved_model):
                    raise LLMCallBudgetExceeded(
                        category=category, section_id=scope, limit_kind="模型不在批准范围",
                        limit=0, observed=0, prompt_version=prompt_version)
                if axis == AXIS_WRITING:
                    cap = self.policy.cap_for(category)
                    if cap.max_output_tokens is not None and max_tokens is not None \
                            and int(max_tokens) > int(cap.max_output_tokens):
                        raise LLMCallBudgetExceeded(
                            category=category, section_id=scope,
                            limit_kind="单次输出 token 上限",
                            limit=int(cap.max_output_tokens), observed=int(max_tokens),
                            prompt_version=prompt_version)
                    in_section = self.count(category=category, section_id=scope)
                    if cap.max_attempts_per_section is not None \
                            and in_section >= int(cap.max_attempts_per_section):
                        raise LLMCallBudgetExceeded(
                            category=category, section_id=scope, limit_kind="本节上限",
                            limit=int(cap.max_attempts_per_section), observed=in_section,
                            prompt_version=prompt_version)
                    in_category = self.count(category=category)
                    if cap.max_attempts_total is not None \
                            and in_category >= int(cap.max_attempts_total):
                        raise LLMCallBudgetExceeded(
                            category=category, section_id=scope, limit_kind="本类总上限",
                            limit=int(cap.max_attempts_total), observed=in_category,
                            prompt_version=prompt_version)
                    in_writing = self.count_axis(axis=AXIS_WRITING)
                    if self.policy.total_max_attempts is not None \
                            and in_writing >= int(self.policy.total_max_attempts):
                        raise LLMCallBudgetExceeded(
                            category=category, section_id=scope, limit_kind="整轮总上限",
                            limit=int(self.policy.total_max_attempts), observed=in_writing,
                            prompt_version=prompt_version)
                else:
                    axis_cap = self.policy.axis_cap_for(axis)
                    # 研究轴不设单次输出 token 上限：那是各调用点自己的技术参数，
                    # 与「这条轴最多发几次」是两个量（`AxisCap` 里根本没有该字段）。
                    in_scope = self.count_axis(axis=axis, scope=scope)
                    scope_limit = axis_cap.limit_for_scope(scope)
                    if scope_limit is not None and in_scope >= scope_limit:
                        raise LLMCallBudgetExceeded(
                            category=category, section_id=scope, limit_kind="本 topic 上限",
                            limit=scope_limit, observed=in_scope,
                            prompt_version=prompt_version)
                    in_axis = self.count_axis(axis=axis)
                    if axis_cap.max_attempts is not None \
                            and in_axis >= int(axis_cap.max_attempts):
                        raise LLMCallBudgetExceeded(
                            category=category, section_id=scope, limit_kind="研究轴总上限",
                            limit=int(axis_cap.max_attempts), observed=in_axis,
                            prompt_version=prompt_version)
        except LLMCallBudgetError as exc:
            self.refusals.append({
                "category": category, "axis": axis,
                "section_id": scope, "scope_kind": kind,
                "prompt_version": prompt_version, "call_id": call_id,
                "reason": exc.limit_kind if isinstance(exc, LLMCallBudgetExceeded)
                          else "归属不清",
                "limit": getattr(exc, "limit", None), "observed": getattr(exc, "observed", None),
                "detail": str(exc), "attempt_ordinal_if_sent": len(self.attempts) + 1})
            raise
        attempt = BudgetAttempt(
            ordinal=len(self.attempts) + 1, category=category,
            section_id=scope, prompt_version=prompt_version, model=model,
            max_tokens=max_tokens, call_id=call_id, axis=axis, scope_kind=kind)
        self.attempts.append(attempt)
        return attempt

    def settle(self, attempt: BudgetAttempt, *, status: str, error: str = "") -> None:
        """请求返回后落状态。**不**改记账时的归属与计数：失败的尝试照样占一次。"""
        if not any(a is attempt for a in self.attempts):
            raise LLMCallBudgetError(
                "settle 收到的尝试不在本账本里：不得对未记账的请求落状态")
        attempt.status = status
        attempt.error = error

    # -- 读视图 --------------------------------------------------------------
    def count(self, *, category: str | None = None, section_id: str | None = None) -> int:
        return sum(1 for a in self.attempts
                   if (category is None or a.category == category)
                   and (section_id is None or a.section_id == section_id))

    def count_axis(self, *, axis: str, scope: str | None = None) -> int:
        """按**轴**计数（可选再按作用域收窄）。两条轴的上限判定都走这里。"""
        return sum(1 for a in self.attempts
                   if a.axis == axis and (scope is None or a.section_id == scope))

    def category_counts(self) -> dict:
        """按**已记账的尝试**归类合计——与 `axis_counts`/`section_counts`/`status_counts` 同一读法。

        这里刻意**不**遍历 `CATEGORIES` 那个固定元组：那份元组是「哪一类写作调用被批准过」的
        历史登记，而类别是由**每份政策**自己的 `prompt_versions` 归属表声明的。按元组取交集，
        等于说「政策登记了、但元组里没有的类别不发生」——`cited_prose_writing`/
        `cited_prose_review` 就落在这个缝里：上限判定逐条正常，`attempts_by_section` 也照实显示，
        唯独「按类别」这一栏读成 `{}`，同一份 summary 内部自相矛盾。类别不可能写错
        （`reserve` 只接受 `policy.category_of(prompt_version)` 的返回值），所以照账本归类即可。
        """
        out: dict[str, int] = {}
        for attempt in self.attempts:
            out[attempt.category] = out.get(attempt.category, 0) + 1
        return {k: out[k] for k in sorted(out)}

    def axis_counts(self) -> dict:
        out: dict[str, int] = {}
        for attempt in self.attempts:
            out[attempt.axis] = out.get(attempt.axis, 0) + 1
        return {k: out[k] for k in sorted(out)}

    def scope_kind_counts(self) -> dict:
        out: dict[str, int] = {}
        for attempt in self.attempts:
            out[attempt.scope_kind] = out.get(attempt.scope_kind, 0) + 1
        return {k: out[k] for k in sorted(out)}

    def section_counts(self) -> dict:
        out: dict[str, dict] = {}
        for attempt in self.attempts:
            out.setdefault(attempt.section_id, {})
            row = out[attempt.section_id]
            row[attempt.category] = row.get(attempt.category, 0) + 1
        return {k: out[k] for k in sorted(out)}

    def status_counts(self) -> dict:
        out: dict[str, int] = {}
        for attempt in self.attempts:
            out[attempt.status] = out.get(attempt.status, 0) + 1
        return out

    def summary(self) -> dict:
        """账本的可复核读视图（报告与对账都用它，不用裸计数）。"""
        return {"policy": self.policy.to_dict(), "enforce": self.enforce,
                "attempt_total": len(self.attempts),
                "attempts_by_category": self.category_counts(),
                "attempts_by_axis": self.axis_counts(),
                "attempts_by_scope_kind": self.scope_kind_counts(),
                "attempts_by_section": self.section_counts(),
                "attempts_by_status": self.status_counts(),
                "refusal_total": len(self.refusals), "refusals": list(self.refusals),
                "attempts": [a.to_dict() for a in self.attempts],
                "note": ("每次尝试在**发请求之前**记账：失败尝试同样占一次；"
                         "共用一个客户端的两个类别仍只有各自的一条记录；"
                         "写作轴的整轮上限只数写作轴尝试，研究调用不消耗它")}


# ---------------------------------------------------------------------------
# 进程内安装点（默认不安装：未安装时既有行为逐字节不变）
# ---------------------------------------------------------------------------

_INSTALLED: LLMCallBudget | None = None
_SECTION: ContextVar[str] = ContextVar("llm_budget_section", default="")


def install(budget: LLMCallBudget) -> LLMCallBudget | None:
    """安装预算门，返回被顶掉的那一个（调用方负责在 finally 里还原）。"""
    global _INSTALLED
    previous = _INSTALLED
    _INSTALLED = budget
    return previous


def uninstall() -> None:
    global _INSTALLED
    _INSTALLED = None


def installed() -> LLMCallBudget | None:
    return _INSTALLED


@contextmanager
def suspended() -> Iterator[None]:
    """临时取下预算门（离开作用域还原）。

    只有一个合法用途：**确定性替身探针**——它不发真实请求，因此既不该占用已批准的额度
    （真实模式下会平白掐掉一次真运行），也不该混进按节归集的账本（会把每节的计数翻倍）。
    谁用了它，谁就必须在自己的产物里写明「这一段不在门内」。
    """
    global _INSTALLED
    previous = _INSTALLED
    _INSTALLED = None
    try:
        yield
    finally:
        _INSTALLED = previous


@contextmanager
def section_scope(section_id: str) -> Iterator[None]:
    """把接下来的请求归属到本节。离开作用域即还原（不污染相邻的节）。"""
    token = _SECTION.set(str(section_id or ""))
    try:
        yield
    finally:
        _SECTION.reset(token)


@contextmanager
def research_scope(topic_id: str) -> Iterator[None]:
    """把接下来的**研究**请求归属到本 topic。离开作用域即还原。

    与 `section_scope` 复用**同一个** ContextVar，写成 `"topic:<topic_id>"`：两条轴的作用域
    在同一个变量里，因此「这次调用属于谁」只有一处真值，不会出现两套并行的作用域栈。
    空 topic_id 直接拒绝——一个空作用域会让研究调用看上去没有归属。
    """
    ident = str(topic_id or "").strip()
    if not ident:
        raise LLMCallBudgetError("research_scope 必须给出非空 topic_id：空作用域无法归属")
    token = _SECTION.set(TOPIC_SCOPE_PREFIX + ident)
    try:
        yield
    finally:
        _SECTION.reset(token)


def current_section_id() -> str:
    return _SECTION.get()


def current_scope_kind() -> str:
    return scope_kind_of(_SECTION.get())


def reserve_attempt(*, call_id: str, prompt_version: str | None,
                    model: str | None, max_tokens: int | None) -> BudgetAttempt | None:
    """共享入口的第一个钩子：未安装预算门时返回 None（无门 = 既有行为）。"""
    budget = _INSTALLED
    if budget is None:
        return None
    return budget.reserve(call_id=call_id, prompt_version=prompt_version, model=model,
                          max_tokens=max_tokens, section_id=current_section_id())


def settle_attempt(attempt: BudgetAttempt | None, *, status: str, error: str = "") -> None:
    """共享入口的第二个钩子：把这次尝试的结局落到账上（失败也要落）。"""
    if attempt is None:
        return
    budget = _INSTALLED
    if budget is None:
        return
    budget.settle(attempt, status=status, error=error)


def _main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        prog="python -m llm.budget", description="共享 LLM 客户端的事前调用预算门自检")
    parser.add_argument("--self-check", action="store_true",
                        help="确定性自检：不发任何真实请求，演示「第 N+1 次被拒」")
    parser.parse_args(argv)

    policy = CallBudgetPolicy(
        policy_version="self-check", approved_model="self-check-model",
        # 上限只按**次数**给（每节 1 / 整轮 1）；`max_output_tokens=None` = 政策里没有费用型
        # token 限额，容量是各调用点自己的技术参数（见 `evaluation.run_m930_3_acceptance`）。
        categories=(CategoryCap(CATEGORY_NARRATION, 1, 1, None, True, "self-check"),),
        prompt_versions={"self-check-prompt@rev": CATEGORY_NARRATION},
        total_max_attempts=1, total_approved=True)
    budget = LLMCallBudget(policy, enforce=True)
    install(budget)
    try:
        sent: list[str] = []
        for call_id in ("c1", "c2"):
            try:
                with section_scope("self-check-section"):
                    ticket = reserve_attempt(call_id=call_id, prompt_version="self-check-prompt@rev",
                                             model="self-check-model", max_tokens=100)
                settle_attempt(ticket, status=STATUS_OK)
                sent.append(call_id)
            except LLMCallBudgetError:
                pass
        print(json.dumps({"sent": sent, "refusals": len(budget.refusals),
                          "attempts": len(budget.attempts),
                          "summary": budget.summary()["attempts_by_status"]},
                         ensure_ascii=False, indent=2))
    finally:
        uninstall()
    return 0


if __name__ == "__main__":  # pragma: no cover - 入口
    sys.exit(_main(sys.argv[1:]))

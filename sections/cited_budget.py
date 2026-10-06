"""§0.20 新 cited 链的**独立**小预算声明（`cited-budget-3`）。

这条链（`cited_writer` 写作 + `cited_rework` 返修 + `cited_review` 审阅）**不**复用旧 Claim
链的蕴含额度，也不把自己的次数挂到 `m930-3-budget-*` 的任何一条类别上：旧政策的每一类都是为
「提案 → 聚合绑定 → 逐候选蕴含 → 接受绑定」那条链批的，把新链的调用记进那里，会让「旧链花了
多少次」这个读数凭空变高，也会让新链借旧链的批准运行——两者都是没发生过的授权。

本模块只做一件事：把已获批的上限写成一个**可事前核对**的 `CallBudgetPolicy`，并在安装之前
**自证**它确实表达了那几条上限（见 :func:`assert_caps_expressed`）。自证不是装饰：「上限写进
文档」与「上限真的生效」是两件事，而两者的差别在产物上只表现为「多发了几个请求」，事后读不
回来——所以它必须在上限生效之前就被判一次。

## 本版（`cited-budget-3`）获批的上限（逐字来自本批指令）

* **公司节与财务节各**：至多 1 次真实写作 + 1 次真实逐句独立审阅；
* 整轮合计至多 4 次；
* 自动重试为 0；
* **返修本次不自动发**：`CATEGORY_CITED_PROSE_REWORK` **不在**本版的类别集里，它的 prompt
  版本也不在归属表里，因此返修请求连归属这一步都过不去（结构性不可发出），而不是靠一个
  「本轮跳过」的开关。见 :func:`cited_rework_approved`。

自动重试为 0 不是靠一个 `retries=0` 参数表达的（那只是本模块的一组常量），而是靠**每节每类
上限为 1** 结构性成立：第二次尝试在类别内就已经超限，请求在发出之前被拒。因此这条链上不存在
「重试」这种可观测事件——它不可表达。

## 为什么返修要从类别集里**移出**，而不是设成 0

把上限设成 0 会让类别仍出现在账本上（`approved=True` 配 `0` 次），读者会读到一条自相矛盾的
记录；而它真正的语义是「本批没有批这一类」。移出类别集之后，「本批会不会发返修」这件事在
归属表上就是**可判定**的：`category_of(CITED_REWORK_PROMPT_VERSION)` 当场抛
`LLMCallAttributionError`。链侧另有一道更早的守卫（`cited_rework_approved()`），使这一轮
连请求面都不会构造。

## 历史账本 `cited-budget-1` / `cited-budget-2` 不改

`cp21_r1` 等已落盘运行的 `cited_call_ledger.json` **自带**当时的 `policy`（上限、basis、
prompt 归属表、整轮上限），是自描述的，读它不需要本模块。本模块升到 `cited-budget-3` 只改
**新**运行的读数。把历史账本按新常量重算一遍，才是改动历史——本模块不提供那条路。

`fin_source_scope` / `fin_balance_structure` 的确定性呈现**不**在本政策的任何类别里：它们不发
模型请求，因此没有可记账的尝试。给它们批一个额度，等于先假定它们会调用模型，再把那个假定
当成证据。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from llm import budget as LB
from sections import cited_review as CR
from sections import cited_rework as CRW
from sections import cited_writer as CW

__all__ = [
    "CITED_BUDGET_POLICY_VERSION",
    "CITED_BUDGET_BASIS",
    "CATEGORY_CITED_PROSE_WRITING",
    "CATEGORY_CITED_PROSE_REWORK",
    "CATEGORY_CITED_PROSE_REVIEW",
    "CITED_AUTOMATIC_RETRIES",
    "CITED_WRITE_MAX_PER_SECTION",
    "CITED_REWORK_MAX_PER_SECTION",
    "CITED_REVIEW_MAX_PER_SECTION",
    "CITED_WRITE_MAX_TOTAL",
    "CITED_REWORK_MAX_TOTAL",
    "CITED_REVIEW_MAX_TOTAL",
    "CITED_RUN_MAX_ATTEMPTS",
    "CITED_BUDGET_SECTIONS",
    "CitedBudgetError",
    "cited_prompt_versions",
    "cited_rework_approved",
    "cited_call_budget_policy",
    "cited_call_budget_gate",
    "assert_sections_approved",
    "assert_caps_expressed",
]

#: 政策版本。上限、类别名、节集合、prompt 归属表任一变化都必须升版——它进运行产物的预算
#: 读数。历史运行自带当时的 `policy`（见模块 docstring），因此升版不改历史。
CITED_BUDGET_POLICY_VERSION = "cited-budget-3"

#: 批准出处。这一行会被原样印进报告，读者据此判断「这几次是谁批的」。它**不**是通关文牒：
#: 是否真的可以发请求，仍由 `LLMCallBudget(enforce=True)` 在构造期按 `approvable` 判。
CITED_BUDGET_BASIS = (
    "M930-5 双节上传纵链批：公司节与财务节各 ≤1 写 + ≤1 逐句独立审阅，整轮 ≤4，自动重试 0，"
    "返修本次不自动发（财务节以外的节、外部检索与额外模型调用未获批；"
    "历史 `cited-budget-1`/`cited-budget-2` 账本不改）")

#: 类别名。**新名**，不复用 `CATEGORY_*` 里任何既有值：复用会让两条链在账本上无法区分。
CATEGORY_CITED_PROSE_WRITING = "cited_prose_writing"
CATEGORY_CITED_PROSE_REWORK = "cited_prose_rework"
CATEGORY_CITED_PROSE_REVIEW = "cited_prose_review"

#: 自动重试次数。恒为 0——本批指令如此授权；本链的实现也没有任何重试入口。
CITED_AUTOMATIC_RETRIES = 0

CITED_WRITE_MAX_PER_SECTION = 1
CITED_REVIEW_MAX_PER_SECTION = 1
#: 返修本版**未获批**。常量保留为 0 只为了「读者面印出来的那一行」与类别集说的是同一件事；
#: 真正让它不可发出的是**它不在** `_EXPECTED_CAPS` 与 `cited_prompt_versions()` 里。
CITED_REWORK_MAX_PER_SECTION = 0
CITED_WRITE_MAX_TOTAL = 2
CITED_REVIEW_MAX_TOTAL = 2
CITED_REWORK_MAX_TOTAL = 0
CITED_RUN_MAX_ATTEMPTS = 4

#: 本政策实际覆盖的节。**显式列出**而不是从「反正每节上限都是 1」推：整轮上限 4 只在
#: 恰好 2 节 × 2 类 × 1 次时才等于「双节各自至多 2 次、整轮至多 4 次」。节集合变了而上限
#: 没变，那句话就不再成立，届时必须重新裁决，而不是让一个旧数字继续看着像上限。
CITED_BUDGET_SECTIONS: tuple[str, ...] = ("company", "financial")


class CitedBudgetError(RuntimeError):
    """预算声明自身的事前拒绝。**不是**一次调用结果。"""


#: 类别 → 获批的 `(每节上限, 整轮上限)`。**唯一**的真值来源：政策构造与自证都读它，
#: 因此不可能出现「政策按常量建构了、自证按另一份常量核对」这种自证自的空转。
#:
#: `CATEGORY_CITED_PROSE_REWORK` **不**在这里：本批没有批返修。它的下标就是
#: :func:`cited_rework_approved` 的判据，所以"这一轮会不会发返修"只有一个来源。
_EXPECTED_CAPS: dict[str, tuple[int, int]] = {
    CATEGORY_CITED_PROSE_WRITING: (CITED_WRITE_MAX_PER_SECTION, CITED_WRITE_MAX_TOTAL),
    CATEGORY_CITED_PROSE_REVIEW: (CITED_REVIEW_MAX_PER_SECTION, CITED_REVIEW_MAX_TOTAL),
}

#: 类别名 → 中文标签。只用于 `basis` 这一行可读文字，不参与任何判据。
_CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_CITED_PROSE_WRITING: "写作",
    CATEGORY_CITED_PROSE_REWORK: "有界局部返修",
    CATEGORY_CITED_PROSE_REVIEW: "审阅",
}


def cited_rework_approved() -> bool:
    """本版**有没有**批返修这一类。链在构造返修请求**之前**问它。

    它读的是 `_EXPECTED_CAPS` 的下标（= 本版类别集），不是某个独立的开关：只有一个来源，
    因此"返修被移出类别集"与"链不再构造返修请求"不可能各自漂移。
    """
    return CATEGORY_CITED_PROSE_REWORK in _EXPECTED_CAPS


def cited_prompt_versions() -> dict[str, str]:
    """本链允许发出请求的 prompt 版本 → 类别。**只有本版类别集里那几条**。

    归属表是 fail-closed 的入口：任何别的 prompt version 走到这里都归不了类，
    `CallBudgetPolicy.category_of` 当场抛 `LLMCallAttributionError`，请求不发出。
    返修不在本版类别集里时，它的 prompt 版本**也不**在本表里——两件事同一来源，
    因此「返修发不出去」不依赖调用点是否记得关掉它。
    """
    return {
        CW.CITED_WRITER_PROMPT_VERSION: CATEGORY_CITED_PROSE_WRITING,
        CR.CITED_REVIEW_PROMPT_VERSION: CATEGORY_CITED_PROSE_REVIEW,
    }


def assert_sections_approved(section_ids: Sequence[str]) -> tuple[str, ...]:
    """**在第一个请求之前**确认本节集合就是获批的那个。

    节集合是**上限的一部分**而不是一条附注：本批批的是「公司节合计至多 3 次」。跑一节没被
    批过的节，会以「上限没超」的形态花掉一次没发生过的授权——账本事后只能证明「用了 N 次」，
    证明不了「这 N 次本来被批给了哪一节」。
    """
    wanted = tuple(str(s) for s in section_ids)
    unapproved = sorted({s for s in wanted if s not in CITED_BUDGET_SECTIONS})
    if unapproved:
        raise CitedBudgetError(
            f"节 {unapproved} 不在本版获批的节集合 {list(CITED_BUDGET_SECTIONS)} 里："
            f"本批只批了 {list(CITED_BUDGET_SECTIONS)} 的真实调用，其余节在**第一个请求之前**"
            "拒绝（不是「跑到那一节再拒绝」）")
    return wanted


def cited_call_budget_policy(*, approved_model: str) -> LB.CallBudgetPolicy:
    """本链的预算政策。**调用方不得覆盖任何上限**：上限是本批获批的量，没有参数入口。"""
    if not str(approved_model or "").strip():
        raise CitedBudgetError(
            "本链的预算政策必须声明已批准模型：没有模型身份就没有「谁被批准了」这一读数")
    return LB.CallBudgetPolicy(
        policy_version=CITED_BUDGET_POLICY_VERSION,
        approved_model=str(approved_model),
        categories=tuple(
            LB.CategoryCap(category=category,
                           max_attempts_per_section=per_section,
                           max_attempts_total=total,
                           max_output_tokens=None, approved=True,
                           basis=f"{CITED_BUDGET_BASIS}；{_CATEGORY_LABELS[category]}类别")
            for category, (per_section, total) in _EXPECTED_CAPS.items()),
        prompt_versions=cited_prompt_versions(),
        total_max_attempts=CITED_RUN_MAX_ATTEMPTS,
        total_approved=True,
    )


def cited_call_budget_gate(*, approved_model: str) -> LB.LLMCallBudget:
    """已安装即生效的预算门（`enforce=True`）。构造期就要求**没有任何未获批准的量**。"""
    policy = cited_call_budget_policy(approved_model=approved_model)
    assert_caps_expressed(policy)
    try:
        return LB.LLMCallBudget(policy, enforce=True)
    except ValueError as exc:
        raise CitedBudgetError(
            f"本链的预算政策无法以强制模式安装：{exc}") from exc


@dataclass(frozen=True)
class _CapProbe:
    """一次「这个上限到底判不判得住」的探针读数。"""

    category: str
    per_section: int | None
    total: int | None


def assert_caps_expressed(policy: LB.CallBudgetPolicy) -> None:
    """**在上限生效之前**自证政策真的表达了获批的那几条上限。

    判据都直接读政策对象自己的字段，不读本模块的常量——读常量只能证明「常量没被改过」，
    证明不了「政策按常量建构了」。任一条不成立即抛：宁可在第一个请求发出之前停住，也不要
    跑完一轮之后才发现上限是空的。
    """
    probes = [_CapProbe(category=cap.category,
                        per_section=cap.max_attempts_per_section,
                        total=cap.max_attempts_total)
              for cap in policy.categories]
    # 类别集合必须**恰好**相等：少一类 = 有一条该批的额度没生效；多一类 = 多一条可发请求的路。
    if len(probes) != len(_EXPECTED_CAPS) or {p.category for p in probes} != set(_EXPECTED_CAPS):
        raise CitedBudgetError(
            f"政策的类别集合是 {sorted(p.category for p in probes)}，不是获批的 "
            f"{sorted(_EXPECTED_CAPS)}：两类都不是可以静默接受的偏差")
    for probe in probes:
        want = _EXPECTED_CAPS[probe.category]
        if (probe.per_section, probe.total) != want:
            raise CitedBudgetError(
                f"类别 {probe.category!r} 的上限是 (每节 {probe.per_section}, 总计 {probe.total})，"
                f"不是获批的 {want}：共享预算实现没有表达出获批的上限，本链在此停止")
    if policy.total_max_attempts != CITED_RUN_MAX_ATTEMPTS:
        raise CitedBudgetError(
            f"整轮上限是 {policy.total_max_attempts}，不是获批的 {CITED_RUN_MAX_ATTEMPTS}："
            f"节 {list(CITED_BUDGET_SECTIONS)} 合计至多 {CITED_RUN_MAX_ATTEMPTS} 次这句话"
            "在政策里不成立")
    registered = dict(policy.prompt_versions)
    if registered != cited_prompt_versions():
        raise CitedBudgetError(
            f"prompt 归属表与获批的不一致：{sorted(registered)} != "
            f"{sorted(cited_prompt_versions())}（多一条就是用别的 prompt 也能花额度）")
    # 归属表的**值**也必须全是获批类别：一条指到别处的归属 = 账本上归错类。
    stray = sorted({c for c in registered.values() if c not in _EXPECTED_CAPS})
    if stray:
        raise CitedBudgetError(
            f"prompt 归属表把版本指到了未登记的类别 {stray}：本链只允许 "
            f"{sorted(_EXPECTED_CAPS)}")
    if policy.approvable:
        raise CitedBudgetError(
            f"政策里仍有未获批准的量 {list(policy.approvable)}：未批准的量不得靠「不设限」执行")
    if CITED_AUTOMATIC_RETRIES != 0:
        raise CitedBudgetError(
            f"自动重试次数被改成 {CITED_AUTOMATIC_RETRIES}：本批只批了 0，改它必须重新裁决")
    #: 返修的「批没批」与「归属表登没登」必须说同一件事。两处各自漂移的后果是：链以为不发、
    #: 归属表却仍能给它记账，或者反过来——链发得出请求、账本却归不了类。两者都是不可观测的
    #: 偏差，所以在这里一次性判掉。
    rework_registered = CRW.CITED_REWORK_PROMPT_VERSION in registered
    if rework_registered != cited_rework_approved():
        raise CitedBudgetError(
            f"返修的类别集与 prompt 归属表不一致：本版"
            f"{'批了' if cited_rework_approved() else '没批'}返修，"
            f"归属表{'登了' if rework_registered else '没登'} "
            f"{CRW.CITED_REWORK_PROMPT_VERSION!r}。两处必须同时成立或同时不成立")

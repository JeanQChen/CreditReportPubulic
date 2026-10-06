"""把**冻结** topic aspect 构造成 `InformationNeed` 的**生产实现**（M930-3 定点返修 · 步骤 ③）。

为什么需要它
------------
`harness/topic_runtime.py::InformationNeedBuilder` 是一个 Protocol；在此之前仓内**只有测试替身**
实现它（`evals.test_demo_topic_runtime._NeedBuilder` 等）。替身按冻结 aspect 造 need，却把
`required_evidence_types` / `required_source_types` 都留空，于是 `routing/router.py`
`requires_external_source()`（外部路由的**唯一**判据）在任何 aspect 上都为假——**即使**开关打开
也一样。冻结 Contract 里"某 aspect 的来源类只能是 `external`"这条要求因此从未离开过 runner。
本模块把那一跳接上：**用权威派生器**把冻结声明投进正式 `InformationNeed`。

唯一的真值来源
--------------
来源类**只**来自 `TS.derive_support_eligibility(snap).required_source_classes`——与
`external_retrieval.requirement_side` 的读法**同一个函数**。本模块**不自建第二套判据**、
不硬编码任何 topic / aspect / 行业名、不做 `source_classes → evidence_kind` 的手写映射。

刻意**不**做的事（都是具名限制，不得靠猜补齐）
----------------------------------------------
* `required_evidence_types` 保持**空**。冻结 ref (`EvidenceRequirementRef`) **不带**
  `evidence_kind`；把 `external` 手写成 `web` 是一张手写映射表，它会成为第二处真值，因此不做。
  外部要求由 `required_source_types` 里的 `external` 表达（`requires_external_source` 两条判据
  之一即可成立）。限制码：`evidence_kind_not_carried_by_ref`。
* `time_scope` 保持 **None**。冻结 aspect 的 `time_scope` 是**窗口枚举 token**
  （`THREE_YEARS_PLUS_LATEST` / `CURRENT_AS_OF_WITH_24M_CHANGES` / …），不是可比日期；而
  `InformationNeed.time_scope` 会被 `routing.router._time_scope_late` 与本地材料截止**按日期比较**。
  把枚举 token 塞进去是静默类型混淆（会被读成"无法解析"），因此宁可如实留空。
  限制码：`frozen_time_scope_is_window_token_not_date`。
* `depends_on` 保持空：冻结 aspect 不承载 need 间依赖，凭空连边会制造不存在的父子关系。

空 ref ⇒ 空来源类（**不构造伪 Need**），与 `sections/research_common.build_need` 的既定口径
"无 evidence_requirements 的问题保持空"一致。
"""

from __future__ import annotations

from harness import topic_schema as TS
from routing import schema as RS

#: 本实现与它的映射规则的版本。映射规则变了必须前进（读者据此判读产物里 need 的来源类从何而来）。
ASPECT_NEED_BUILDER_VERSION = "anb-1"

#: 具名限制码（封闭集）：每一条都对应"冻结里确实有、但本实现**刻意不投**的东西"。
ASPECT_NEED_LIMITATION_CODES = (
    "evidence_kind_not_carried_by_ref",
    "frozen_time_scope_is_window_token_not_date",
)


class FrozenAspectInformationNeedBuilder:
    """冻结 aspect → `InformationNeed` 的生产构造器（`anb-1`）。

    唯一职责：把该 aspect 的**冻结**来源类声明搬进正式 need。它**不**判资格、**不**检索、
    **不**联网、**不**决定路由——路由仍由既有 `routing.router.route()` 判定。
    """

    version = ASPECT_NEED_BUILDER_VERSION

    def build(self, aspect: TS.TopicAspectRequirementSnapshot, *,
              need_id: str, company_id: str, section_id: str,
              report_as_of: str | None) -> RS.InformationNeed:
        eligibility = TS.derive_support_eligibility(aspect)
        # 保序去重：派生器已排序，这里只做"同一来源类不重复投两次"，不改判据。
        source_types = list(dict.fromkeys(eligibility.required_source_classes))
        return RS.InformationNeed(
            need_id=need_id,
            section_id=section_id,
            question=aspect.requirement_text,
            # 见模块 docstring：ref 不带 evidence_kind，禁止手写映射。
            required_evidence_types=[],
            required_source_types=source_types,
            # 见模块 docstring：冻结 time_scope 是窗口 token，不是日期。
            time_scope=None,
            priority="primary",
            depends_on=[],
            metadata={
                "aspect_id": aspect.aspect_id,
                "topic_id": aspect.topic_id,
                "company_id": company_id,
                "report_as_of": report_as_of,
                # 回查面：这条 need 的来源类从哪来、由哪个派生器给出、本实现刻意没投什么。
                "required_source_classes": list(source_types),
                "source": "frozen_evidence_requirement_ref",
                "deriver": "TS.derive_support_eligibility",
                "builder_version": ASPECT_NEED_BUILDER_VERSION,
                "limitations": list(ASPECT_NEED_LIMITATION_CODES),
            })


__all__ = [
    "ASPECT_NEED_BUILDER_VERSION",
    "ASPECT_NEED_LIMITATION_CODES",
    "FrozenAspectInformationNeedBuilder",
]

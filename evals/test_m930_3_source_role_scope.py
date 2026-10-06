"""M930-3 定点返修③：期间/来源角色核对（`srsc-1`）——旧同类材料不得写成无期间当前断言。

跑法（无管道/无重定向）：`python -X utf8 -m evals.test_m930_3_source_role_scope`

判据来源是 `DESIGN_V2.md` O-12：同类材料「较新且可核实者优先表达当前状态」，旧同类型材料
保留索引与来源身份用于历史/变化/冲突核对。这条判断在 L0 已经落成闭集角色
（`harness/source_manifest.py::SOURCE_ROLES`），本判据**只吃角色**，不比较任何日期、不推造
任何披露日期、不按公司名/文件名/页码写特例。

必测的**四类正反例**（逐类都给正例与反例）：

  1. **同类新旧材料**：支撑里含当前锚 ⇒ 不能由较新材料核实的指控不成立（反例）；
  2. **跨类募集说明书**：其他系列成员按主题同等资格参与检索 ⇒ 不适用本判据（反例）；
  3. **仅旧材料**：全部支撑边落在同类较旧来源 ⇒ 成立（正例）；同一材料 + 期间限定 ⇒ 不成立；
  4. **期间未知**：同系列而期间不可核实的成员同样取「历史/冲突」角色 ⇒ 成立（正例）。

夹具纪律：结构用**真类**（`SM.SourceDocumentKey` / `TS.DocumentSourceSet`），文档身份逐字取自
r7b 冻结来源清单（`evaluation/results/m930_3_acceptance_crossdoc_real_r7b/source_manifest.json`
的角色与 reason_code）。不读真实库、不建真实 Pack、不调 LLM、不联网、不写任何文件。
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import source_manifest as SM
from harness import topic_schema as TS
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import source_role_scope as SRS

#: ---- r7b 冻结来源清单里的三份材料（逐字取自 `source_manifest.json`）---------------
#: 角色与 reason_code 都是**记录值**，不是本模块的新判断：
#:   * NDSD_2025_year → current_state_source / current_state_source_anchor
#:   * NDSD_2024_year → history_and_conflict_source / same_series_older_period
#:   * NDSD_KCZ_2026 → topic_participating_source / different_series_topic_participating
DOC_ANCHOR = "NDSD_2025_year"
DOC_OLDER = "NDSD_2024_year"
DOC_OTHER_SERIES = "NDSD_KCZ_2026"
COMPANY = "300750"
EVIDENCE_SET = "es-1"

ROLE_RECORDED = {
    DOC_ANCHOR: "current_state_source",
    DOC_OLDER: "history_and_conflict_source",
    DOC_OTHER_SERIES: "topic_participating_source",
}


class _NS:
    """只读命名空间替身（被读的只有属性，不要求真 Pack 对象）。

    `prose_units` 默认空元组：`pw-15` 起生产侧门前束**恒有**草稿层字段，空元组 = 本节没有
    草稿层。替身漏掉这个字段名，裁出对账读到的就是「属性不存在」（AttributeError）而不是
    「没有草稿层」——两者在产物里是不同的事，替身不得把它们混为一谈。

    `units` 同理，自 `cco-6` 起：门前束的 **context 单元**是 context 那一轴的唯一合法 target，
    `_carvable_context_units` 逐条读 `bundle.units` 的 `draft_unit_id`。空元组 = 本节没有
    context 衔接单元（则一条 blocking 问题都不可能点名单元 ⇒ 不可裁）；替身漏掉这个名字，
    读到的就是 AttributeError，而不是「没有单元可撤」。
    """

    def __init__(self, prose_units: tuple = (), units: tuple = (), **kw) -> None:
        self.__dict__.update(kw)
        self.prose_units = tuple(prose_units)
        self.units = tuple(units)


def _key(document_id: str) -> SM.SourceDocumentKey:
    return SM.SourceDocumentKey(company_id=COMPANY, document_id=document_id,
                                document_version="v1", evidence_set_version=EVIDENCE_SET)


def _source_set(*pairs: tuple[str, str]) -> TS.DocumentSourceSet:
    """真 `DocumentSourceSet`（非空、主体一致、至多一个当前锚，全部校验照走）。"""
    return TS.DocumentSourceSet(members=tuple((_key(d), role) for d, role in pairs))


def _authority(*source_sets: TS.DocumentSourceSet) -> _NS:
    packs = tuple(_NS(pack_id=f"pack-{i}", source_set=s) for i, s in enumerate(source_sets))
    return _NS(pack_set=_NS(packs=packs))


#: r7b 三份材料同属一个来源集（角色逐字取自冻结清单）。
R7B_SOURCE_SET = _source_set((DOC_ANCHOR, "current_state_source"),
                             (DOC_OLDER, "history_and_conflict_source"),
                             (DOC_OTHER_SERIES, "topic_participating_source"))
R7B_ROLES = dict(ROLE_RECORDED)

#: 「期间未知」的正例：**同系列**而内容报告期间不可核实的成员，按 `select_documents`
#: 取 `history_and_conflict_source`（reason_code=`no_verifiable_period_lower_rank`）。
ROLES_WITH_UNKNOWN_PERIOD = dict(ROLE_RECORDED)
ROLES_WITH_UNKNOWN_PERIOD["NDSD_2023_year"] = "history_and_conflict_source"

#: 一条**无年份当前式**的候选文本（r7b 真实正文里的句式，逐字取自
#: `_M930_3_READER_FACING_PREVIEW.md` 的公司节：产品清单 + 「持续推出」）。
TEXT_CURRENT = "公司持续推出创新解决方案，包括滑板底盘、针对乘用车领域的巧克力换电等。"
#: 同一件事的**历史断言**写法：把期间**绝对**点出来（r7b 里这条没有，是本批要求的改法）。
#: `srsc-4` 起「点明期间」必须是**年份**：相对限定词配较旧来源读不出年份，见下一条。
TEXT_QUALIFIED = "2024年，公司推出滑板底盘、针对乘用车领域的巧克力换电等创新解决方案。"
#: 只写**相对**期间限定词的同一句话（`srsc-4` 点名的那一支，真实 cp-21 的 `s0028`
#: 「截至报告期末，公司已实现动力电池累计装车超1,700万辆」引 2024 年报即此形状）。
TEXT_RELATIVE_ONLY = "报告期内，公司推出滑板底盘、针对乘用车领域的巧克力换电等创新解决方案。"


def main() -> dict:
    passed = 0
    failed = 0
    details: list[str] = []

    def check(cond: bool, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            details.append(f"FAIL {msg}")

    def expect_raises(msg: str, fn, exc, *, needle: str = "") -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc as error:
            if needle and needle not in str(error):
                failed += 1
                details.append(f"FAIL {msg}（异常文本里没有 {needle!r}：{error}）")
            else:
                passed += 1
        except Exception as error:                                    # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}（抛的是 {type(error).__name__}：{error}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}（没有抛）")

    # ==================================================================
    # 1. 版本与词表：判据只吃角色，词表里不得混进当前式措辞或具体年份
    # ==================================================================
    check(SRS.RULE_VERSION == "srsc-4",
          f"判据版本必须是 srsc-4（§8 第四条边界「独立支撑结论」，`srsc-3` 补抽取式子串口子，"
          f"`srsc-4` 把 `srsc-1` 的期间限定收紧为**绝对**年份），实为 {SRS.RULE_VERSION!r}")
    # `srsc-4` 只**收紧**：`srsc-1` 的角色闭集与词表一字未改，新增的是一条**更严**的谓词。
    check(SRS.has_absolute_period_qualification("2024年公司推出滑板底盘。")
          and not SRS.has_absolute_period_qualification("报告期内公司推出滑板底盘。"),
          "`has_absolute_period_qualification` 只认年份 token，**不**认相对限定词"
          "（两条谓词的分工：宽松那条留给 `srsc-2` 的 `source_period_scope_dropped`）")
    check(SRS.CANNOT_ESTABLISH_CURRENT_STATE_ROLES == ("history_and_conflict_source",),
          "不能表达当前状态的来源角色**恰好一个**：本次不得顺手把跨系列成员也拉进来"
          f"（实为 {SRS.CANNOT_ESTABLISH_CURRENT_STATE_ROLES}）")
    check(set(SRS.CANNOT_ESTABLISH_CURRENT_STATE_ROLES) <= set(SM.SOURCE_ROLES),
          "本判据的角色必须取自 L0 的闭集 SOURCE_ROLES，不得自造角色名")
    _forbidden = ("目前", "当前", "仍", "持续", "未来", "近年")
    check(not [m for m in _forbidden if m in SRS.PERIOD_QUALIFICATION_MARKERS],
          f"期间限定词表不得含当前式措辞（收了就等于判据自废）：{_forbidden}")
    # 相对词是**最像期间限定、却不是**的一类：它们按**报告自己的框架**读（报告 as-of 是
    # 2025 年末时「本年度」读成 2025），而候选的支撑材料是 2024 年的同系列文档——收进来
    # 恰好造出本判据要消除的那种混淆。
    _relative = ("本年度", "本报告期", "当年", "同期", "上年度", "上一年度",
                 "上半年", "下半年", "截至", "年末", "历年")
    check(not [m for m in _relative if m in SRS.PERIOD_QUALIFICATION_MARKERS],
          f"期间限定词表不得收相对词（读者按报告框架读，正是要消除的混淆）："
          f"{[m for m in _relative if m in SRS.PERIOD_QUALIFICATION_MARKERS]}")
    check(not [m for m in SRS.PERIOD_QUALIFICATION_MARKERS if any(c.isdigit() for c in m)],
          "期间限定词表不得含任何具体年份（那会是按答案写的特例）")
    check(len(set(SRS.PERIOD_QUALIFICATION_MARKERS)) == len(SRS.PERIOD_QUALIFICATION_MARKERS),
          "期间限定词表不得有重复项")

    # ==================================================================
    # 2. 期间限定：只认**绝对期间**（年 token / 文档自述的「报告期」）
    # ==================================================================
    for text in ("2024年公司推出滑板底盘。", "2024 年公司推出滑板底盘。",
                 "2024年度公司推出滑板底盘。", "报告期内公司推出滑板底盘。"):
        check(SRS.has_period_qualification(text), f"这条必须算带期间限定：{text!r}")
    for text in (TEXT_CURRENT, "目前公司产能利用率较高。", "公司目前有三处生产基地。",
                 "公司仍以自建生产基地为主。", "未来公司将继续扩产。", "", "   ",
                 "本年度公司推出滑板底盘。", "上半年公司销量增长。",
                 "上年同期公司销量增长。", "同期公司销量增长。"):
        check(not SRS.has_period_qualification(text), f"这条不得算带期间限定：{text!r}")
    check(not SRS.has_period_qualification("公司成立于300750年"),
          "年 token 只认「四位年 + 年」，不得从别的数字串里切出年份")

    # 2.1 本批的实际出口：年份 token 与「报告期」在**路径 B** 上本来就是高风险表面，
    #     因此「标明期间」这一支在路径 B 上打不满（结论如实记在模块头，不由本测试主张）。
    check("2024年" in NS.scan_numeric_tokens("2024年公司推出滑板底盘。"),
          "年份 token 必须是路径 B 的高风险表面（否则「不写」不是唯一出口）")
    check("报告期" in NS.vague_period_hits("报告期内公司推出滑板底盘。"),
          "「报告期」必须是路径 B 的模糊期间表面（`VAGUE_PERIOD_PHRASES`）")

    # ==================================================================
    # 3. 角色台账：从 Pack 自己的来源集取，跨 Pack 必须一致
    # ==================================================================
    roles = SRS.document_roles(_authority(R7B_SOURCE_SET))
    check(roles == ROLE_RECORDED,
          f"角色必须逐份来自 Pack 的来源集（实为 {roles}）")
    roles_two_packs = SRS.document_roles(_authority(
        _source_set((DOC_ANCHOR, "current_state_source")),
        _source_set((DOC_OLDER, "history_and_conflict_source"))))
    check(roles_two_packs == {DOC_ANCHOR: "current_state_source",
                              DOC_OLDER: "history_and_conflict_source"},
          f"跨 Pack 的角色必须并起来（实为 {roles_two_packs}）")
    expect_raises(
        "同一 document_id 在本节不同 Pack 上角色不一致时必须抛（按哪一份都是猜）",
        lambda: SRS.document_roles(_authority(
            _source_set((DOC_OLDER, "history_and_conflict_source")),
            _source_set((DOC_OLDER, "current_state_source")))),
        SRS.SourceRoleScopeError, needle="角色不一致")
    expect_raises(
        "非 topic 权威（没有 pack_set）必须抛：本判据只对 topic path B 有意义",
        lambda: SRS.document_roles(_NS(pack_set=_NS(packs=()))),
        SRS.SourceRoleScopeError, needle="topic Pack")

    # ==================================================================
    # 4. 四类正反例（本批的必测面）
    # ==================================================================
    def unsupported(text: str, docs: tuple[str, ...], table: dict) -> bool:
        return SRS.unqualified_current_state(text, support_document_ids=docs, roles=table)

    # 4.1 同类新旧材料：支撑里**含当前锚** ⇒ 能由较新材料核实，判据不成立（反例）。
    check(not unsupported(TEXT_CURRENT, (DOC_ANCHOR,), R7B_ROLES),
          "同类新旧材料·反例：支撑含当前锚时，无期间当前断言**不**成立")
    check(not unsupported(TEXT_CURRENT, (DOC_ANCHOR, DOC_OLDER), R7B_ROLES),
          "同类新旧材料·反例：新旧同时支撑同一条断言时同样不成立"
          "（本条挡的是「旧材料当前化」，不是「用了旧材料」）")

    # 4.2 跨类募集说明书：其他系列按主题**同等资格**参与检索 ⇒ 不适用本判据（反例）。
    check(not unsupported(TEXT_CURRENT, (DOC_OTHER_SERIES,), R7B_ROLES),
          "跨类募集说明书·反例：不同系列成员按主题同等资格参与检索，"
          "「不得无条件替代」说的是替代关系，不是「不能支持当前状态」")
    check(not unsupported(TEXT_CURRENT, (DOC_OLDER, DOC_OTHER_SERIES), R7B_ROLES),
          "跨类募集说明书·反例：只要有一条边不是同类较旧来源，本判据即不成立")

    # 4.3 仅旧材料：全部支撑边落在同类较旧来源 ⇒ 成立（正例）；带期间限定 ⇒ 不成立。
    check(unsupported(TEXT_CURRENT, (DOC_OLDER,), R7B_ROLES),
          "仅旧材料·正例：只由同类较旧来源支撑的无期间当前断言必须成立")
    check(unsupported(TEXT_CURRENT, (DOC_OLDER, DOC_OLDER), R7B_ROLES),
          "仅旧材料·正例：重复列举同一条边不改变结论（去重后仍是仅旧材料）")
    check(not unsupported(TEXT_QUALIFIED, (DOC_OLDER,), R7B_ROLES),
          "仅旧材料·反例：同一份旧材料 + 候选自己点明**绝对年份** ⇒ 判据不成立"
          "（O-12 允许旧材料支持历史断言）")
    # `srsc-4`（`scp-7`）收紧：**相对**限定词不豁免——它不携带年份，读者要读出「这是哪一年」
    # 只能另配那份**较旧**文档自己的期间，前提恰好不成立。
    check(unsupported(TEXT_RELATIVE_ONLY, (DOC_OLDER,), R7B_ROLES),
          "仅旧材料·正例（`srsc-4` 收紧）：「报告期内」这类**相对**限定词配较旧来源**不再**豁免"
          "——旧年来源 + 相对期间在被引文档里是那一年的期末，在读者面上仍是无年份的当前态"
          "（真实 cp-21 的 `s0028` 正是这一支）")

    # 4.4 期间未知：同系列而期间不可核实的成员同样取历史/冲突角色 ⇒ 成立（正例）。
    check(ROLES_WITH_UNKNOWN_PERIOD["NDSD_2023_year"] == "history_and_conflict_source",
          "夹具自身失效：期间不可核实的同系列成员必须取 history_and_conflict_source")
    check(unsupported(TEXT_CURRENT, ("NDSD_2023_year",), ROLES_WITH_UNKNOWN_PERIOD),
          "期间未知·正例：只由「同系列、期间不可核实」的成员支撑时必须成立"
          "（这类成员同样不能用于核实当前状态）")
    check(not unsupported(TEXT_QUALIFIED, ("NDSD_2023_year",), ROLES_WITH_UNKNOWN_PERIOD),
          "期间未知·反例：带**绝对年份**限定时不成立")
    check(unsupported(TEXT_RELATIVE_ONLY, ("NDSD_2023_year",), ROLES_WITH_UNKNOWN_PERIOD),
          "期间未知·正例（`srsc-4`）：相对限定词不豁免「期间不可核实的同系列成员」"
          "——那正是它自己的失效来由")

    # 4.5 边界：没有支撑文档的候选不进本判据（路径 A / context 由各自的判据负责）。
    check(not unsupported(TEXT_CURRENT, (), R7B_ROLES),
          "零支撑文档时本判据必须**不**成立：它不是「没有支撑」的判据")
    expect_raises(
        "支撑文档读不到角色时必须抛（默认成任一侧都会把结论送错方向）",
        lambda: unsupported(TEXT_CURRENT, ("NDSD_UNKNOWN_doc",), R7B_ROLES),
        SRS.SourceRoleScopeError, needle="来源角色")
    check("猜" in SRS.unqualified_current_state.__doc__
          and "旧材料当前化" in SRS.__doc__,
          "判据自己的文档必须说清「读不到角色就抛」与「挡的是旧材料当前化」两件事")

    # ==================================================================
    # 5. 写入侧接线（`pack_writer`）：判据挂在哪条轴上、被点名后走哪条出口
    #
    # 这里用**最轻**的替身喂**真实**的接线函数（`_support_documents` /
    # `_path_b_history_only_current_state` / `_candidate_audit` / `_path_b_rejection_detail` /
    # `_directed_reproposal_note` / `carve_out_candidate_subset`）：它们只按属性读候选与支撑边，
    # 而**真实类**下的整链行为由离线重放模块（`test_m930_3_prewrite_offline_replay`）覆盖。
    # 替身省掉的只有构造开销，没有省掉任何判据。
    # ==================================================================
    def _member(pack_id: str, material_id: str, document_id: str = "",
                *, locator: dict | None = None) -> _NS:
        # 成员键**逐字取自产品代码自己的派生函数**：这样测出来的不只是「本判据认不认这个键」，
        # 还有「判据算出的成员键与材料清单自己的键是不是同一个」——两者一旦分叉，判据会静默
        # 恒不成立（每条边都读成「不在这条轴上」），那正是这条接线最隐蔽的坏法。
        member_ref = NS.manifest_member_ref(pack_id, material_id) if material_id else ""
        if locator is None:
            locator = {"document_id": document_id, "page_number": 1} if document_id else {}
        return _NS(member_ref=member_ref, payload_ref={"locator": locator})

    orphan_doc = "NDSD_ORPHAN_year"
    # 替身用的**字段名**必须与真实类一致（`entries`，不是 wire 键 `members`）：替身写错字段名，
    # 就是替身在替产品代码隐瞒「读错属性名 → 静默空表 → 整条核对变死代码」这类缺陷。
    manifest = _NS(entries=(
        _member("pack-0", "m-old", DOC_OLDER),
        _member("pack-0", "m-anchor", DOC_ANCHOR),
        _member("pack-0", "m-other", DOC_OTHER_SERIES),
        # 结构化材料：locator 里没有文档系列（`structured` / `external_snapshot` 的形状）。
        _member("pack-0", "m-struct", locator={"table_ref": "t-1"}),
        # 本节材料清单里确实有这一份，但它的文档不在本节来源集里（缺陷形状，见 5.2）。
        _member("pack-0", "m-orphan", orphan_doc),
        _member("pack-0", "", DOC_OLDER),
    ))
    documents = PW._support_documents(manifest=manifest)
    check(documents == {NS.manifest_member_ref("pack-0", "m-old"): DOC_OLDER,
                        NS.manifest_member_ref("pack-0", "m-anchor"): DOC_ANCHOR,
                        NS.manifest_member_ref("pack-0", "m-other"): DOC_OTHER_SERIES,
                        NS.manifest_member_ref("pack-0", "m-orphan"): orphan_doc},
          f"材料清单里的**文档系列**成员必须逐条落到来源文档上（实为 {documents}）")
    check(NS.manifest_member_ref("pack-0", "m-struct") not in documents,
          "没有文档系列定位的材料不进本表（它是「不在这条轴上」，不是「读不到角色」）")

    def _cand(cid: str, text: str) -> _NS:
        return _NS(candidate_id=cid, claim_text=text)

    def _edge(cid: str, pack_id: str, material_id: str,
              *, path: str = "path_b_material_derived",
              semantics: str = "factual") -> _NS:
        return _NS(binding_subject_kind="claim_candidate", binding_subject_id=cid,
                   authorization_path=path, support_semantics=semantics,
                   authority_container_id=pack_id, material_id=material_id,
                   proposed_support_id=f"ps_{cid}_{material_id}")

    OLD_EDGE = NS.manifest_member_ref("pack-0", "m-old")
    ANCHOR_EDGE = NS.manifest_member_ref("pack-0", "m-anchor")
    OTHER_EDGE = NS.manifest_member_ref("pack-0", "m-other")
    STRUCT_EDGE = NS.manifest_member_ref("pack-0", "m-struct")

    def history_of(candidates, proposals) -> dict:
        return PW._path_b_history_only_current_state(
            bundle=_NS(candidates=tuple(candidates), proposals=tuple(proposals)),
            documents=documents, roles=R7B_ROLES)

    # 5.1 四类正反例在**接线**这一层的读数（判据本身的正反例见 §4）。
    check(history_of([_cand("c1", TEXT_CURRENT)], [_edge("c1", "pack-0", "m-old")])
          == {"c1": (OLD_EDGE,)},
          "仅旧材料·正例：接线后必须点名那条边绑的**成员**（读的人要能自己复算）")
    check(history_of([_cand("c2", TEXT_CURRENT)],
                     [_edge("c2", "pack-0", "m-old"),
                      _edge("c2", "pack-0", "m-anchor")]) == {},
          "同类新旧·反例：有一条边落在当前锚上就不成立（本条挡的是旧材料当前化）")
    check(history_of([_cand("c3", TEXT_CURRENT)], [_edge("c3", "pack-0", "m-other")]) == {},
          "跨类募集说明书·反例：其他系列成员不进本判据")
    check(history_of([_cand("c4", TEXT_QUALIFIED)], [_edge("c4", "pack-0", "m-old")]) == {},
          "带期间限定的同一条断言不成立（O-12 允许旧材料支撑历史断言）")

    # 5.2 不在本轴上的三种边：结构化材料 / 路径 A / 仅 context。
    check(history_of([_cand("c5", TEXT_CURRENT)], [_edge("c5", "pack-0", "m-struct")]) == {},
          "支撑边里有一条不是文档系列材料 ⇒ 这条候选整体不进本判据"
          "（结构化/外部权威各有自己的资格链，不由本判据代言）")
    check(history_of([_cand("c6", TEXT_CURRENT)],
                     [_edge("c6", "pack-0", "m-old", path="path_a_prevalidated")]) == {},
          "路径 A 候选不进本判据（它在写入侧到不了来源文档）")
    # context 边**只**绑叙述草稿单元（不绑候选），因此「只有 context 支撑的候选」在真实结构里
    # 就是「没有任何 factual 路径 B 边」——本判据不得替 context 判它的资格。
    check(history_of([_cand("c7", TEXT_CURRENT)],
                     [_NS(binding_subject_kind="narrative_draft_unit",
                          binding_subject_id="du-1", support_semantics="context",
                          authorization_path="path_b_material_derived",
                          authority_container_id="pack-0", material_id="m-old",
                          proposed_support_id="ps_du-1")]) == {},
          "只有 context 边的候选不进本判据（context 不授权任何事实原子）")
    check(history_of([_cand("c8", TEXT_CURRENT)],
                     [_edge("c8", "pack-0", "m-old"),
                      _edge("c8", "pack-0", "m-struct")]) == {},
          "同一条候选里只要有一条边不在这条轴上，整条候选就不进判据"
          "（不得只按「剩下的旧边」下结论）")
    check(history_of([_cand("c10", TEXT_CURRENT)],
                     [_edge("c10", "pack-0", "m-absent")]) == {},
          "成员根本不在本节材料清单里 ⇒ 这条边不属于文档系列轴（清单本身由别的判据负责）")
    expect_raises(
        "材料清单把成员落到一份**不在本节来源集里**的文档上时必须当场抛："
        "读不到角色只说明上游映射不完整，不说明「这份材料是历史来源」",
        lambda: history_of([_cand("c9", TEXT_CURRENT)],
                           [_edge("c9", "pack-0", "m-orphan")]),
        SRS.SourceRoleScopeError, needle="来源角色")

    # 5.3 权威前置条件：非 topic 权威（财务/附注/外部快照）没有来源集这条轴。
    check(SRS.has_source_document_series(_authority(R7B_SOURCE_SET)),
          "topic Pack 权威必须有来源文档系列这条轴")
    check(not SRS.has_source_document_series(_NS(producer_kind="financial_workflow")),
          "财务权威没有来源集 ⇒ 调用方短路（不得把它读成「读不到角色」而误杀整批候选）")
    check(not SRS.has_source_document_series(_NS(pack_set=_NS(packs=()))),
          "空 pack_set 同样不算有这条轴")

    # 5.4 逐候选审计与整束明细：新原因必须带自己的明细字段，且能与别的轴并列。
    bundle = _NS(candidates=(_cand("c1", TEXT_CURRENT), _cand("c2", TEXT_CURRENT),
                             _cand("c3", TEXT_CURRENT)),
                 proposals=(_edge("c1", "pack-0", "m-old"),
                            _edge("c2", "pack-0", "m-old"),
                            _edge("c2", "pack-0", "m-struct"),
                            _edge("c3", "pack-0", "m-anchor")))
    audit = PW._candidate_audit(bundle=bundle, history_only={"c1": (OLD_EDGE,)})
    check([a.reasons for a in audit] == [("path_b_history_only_current_state",),
                                         ("not_individually_implicated",),
                                         ("not_individually_implicated",)],
          f"逐候选审计必须逐条给出原因（实为 {[a.reasons for a in audit]}）")
    check(audit[0].history_only_member_refs == (OLD_EDGE,)
          and audit[0].to_dict()["history_only_member_refs"] == [OLD_EDGE],
          "新原因的明细字段必须真的带上那条边（有原因没明细 = 无法复核）")
    both = PW._candidate_audit(bundle=bundle, history_only={"c2": (OLD_EDGE,)},
                               ineligible={"c2": (STRUCT_EDGE,)})
    check(both[1].reasons == ("path_b_ineligible_material_scope",
                              "path_b_history_only_current_state"),
          f"两条正交的轴同时点名同一条候选时必须并列列出（实为 {both[1].reasons}）")
    expect_raises(
        "有明细没原因（或反之）必须当场抛",
        lambda: PW.RejectedCandidateAudit(
            candidate_id="c1", reasons=("not_individually_implicated",),
            history_only_member_refs=(OLD_EDGE,)),
        PW.PackWriterError, needle="不一致")

    detail = PW._path_b_rejection_detail(high_risk={}, ineligible={},
                                         history_only={"c1": (OLD_EDGE,)})
    check(OLD_EDGE in detail and "O-12" in detail and "不能表达当前状态" in detail,
          f"整束明细必须把新一组逐候选点名与口径写进去（实为 {detail}）")
    three = PW._path_b_rejection_detail(high_risk={"c9": ("12,345",)},
                                        ineligible={"c8": (STRUCT_EDGE,)},
                                        history_only={"c1": (OLD_EDGE,)})
    check(three.count("路径 B 候选") == 3 and "\n" not in three
          and all(t in three for t in ("c9", "c8", "c1")),
          f"三组同时出现时必须合成**一句**（额度只有一份，不得记成三次被拒）：{three}")

    note = PW._directed_reproposal_note(bundle=bundle, detail=detail, surfaces={},
                                        ineligible={}, history_only={"c1": (OLD_EDGE,)})
    check(f"（{PW.HIGH_RISK_REPROPOSAL_NOTE_VERSION}）" in note and "hrrp-5" in note,
          "说明文本是输入面的一部分，必须带版本号（多一组点名、或多一条分级出路，即升版）")
    check(TEXT_CURRENT in note and OLD_EDGE in note,
          "被点名的候选与那条边必须**逐字**引用")
    check("改绑事实目录里的权威事实行" in note and "撤下这条断言" in note,
          f"出路必须写清（改绑权威事实 / 撤下）：{note[-600:]!r}")
    check("**不要**给它补一个年份或期间词" in note and "未授权表面" in note,
          "必须显式**否掉**「补一个期间词」这条出路（补词解决不了这条边能证明到哪个期间）")
    check("逐条照原样保留" in note and "重新输出一份完整的提案集" in note,
          "其余候选逐条照原样保留、整份提案集重新过门这两条不因新增一组点名而松动")

    # 5.5 裁出：被点名者逐条排除（带原因 + 明细），幸存者逐字照原样；未知身份当场抛。
    plan = {"claim_candidates": [{"candidate_key": "c1"}, {"candidate_key": "c2"},
                                 {"candidate_key": "c3"}]}
    carved = PW.carve_out_candidate_subset(
        plan=plan, bundle=bundle, high_risk={}, ineligible={},
        history_only={"c1": (OLD_EDGE,)}, from_attempt=1, to_attempt=2,
        revision_for_plan=lambda plan: "rev-2")
    check(carved is not None, "有一条被点名、其余幸存时必须真的裁出")
    carved_plan, decision = carved
    check([s["candidate_key"] for s in carved_plan["claim_candidates"]] == ["c2", "c3"],
          "裁出只做「从有序列表里去掉被点名的那几项」，幸存者逐字照原样")
    check(decision.version == PW.CANDIDATE_CARVE_OUT_VERSION
          and PW.CANDIDATE_CARVE_OUT_VERSION == "cco-6",
          "裁出裁决必须带自己的规则版本（产物形状变了就升版：`cco-3` = 多认 "
          "`path_b_unproven_current_state`；`cco-4` = 裁出连带改草稿层、`to_revision` 由裁出后"
          "的提案集重算；`cco-5` = 那条原因多带一个 typed 分级码；`cco-6` = 多认一类被裁对象，"
          "context 单元那一轴与候选轴正交）")
    check([e.reasons for e in decision.excluded] == [("path_b_history_only_current_state",)]
          and decision.excluded[0].history_only_member_refs == (OLD_EDGE,)
          and decision.excluded[0].to_dict()["history_only_member_refs"] == [OLD_EDGE],
          f"被排除者必须带 typed 原因与逐字明细：{decision.excluded[0].to_dict()}")
    expect_raises(
        "判据点名了本束不存在的候选身份必须当场抛（不得顺手多删一条）",
        lambda: PW.carve_out_candidate_subset(
            plan=plan, bundle=bundle, high_risk={}, ineligible={},
            history_only={"c9": (OLD_EDGE,)}, from_attempt=1, to_attempt=2,
            revision_for_plan=lambda plan: "rev-2"),
        PW.PackWriterError, needle="不存在的候选身份")
    check(PW.REPROPOSAL_TRIGGER_KINDS == ("path_b_high_risk_surface",
                                          "path_b_ineligible_material_scope",
                                          "path_b_history_only_current_state",
                                          "path_b_unproven_current_state")
          and all(
              kind in getattr(PW, table)
              for table in ("PROPOSAL_SET_REJECTION_KINDS",
                            "PROPOSAL_SET_REJECTION_CANDIDATE_REASONS",
                            "STRUCTURED_PROPOSAL_SET_REJECTION_KINDS",
                            "CANDIDATE_CARVE_OUT_REASONS")
              for kind in ("path_b_history_only_current_state",
                           "path_b_unproven_current_state")),
          "两条期间轴的原因必须登记进**全部**闭集（少一处就是一条没有审计的拒绝）")
    check(PW.MAX_DIRECTED_REPROPOSAL_PASSES == 1,
          "四种触发仍**共用**同一份额度（按原因各记一份就是加预算换产出）")

    # 5.6 静态接线：判据在门前跑、三条出口各是字面量、两道前置条件短路。
    src = (Path(PW.__file__).read_text(encoding="utf-8"))
    for literal in (
            "history_only = _path_b_history_only_current_state(",
            "unproven_current_state = _path_b_unproven_current_state(",
            "if documents and SRS.has_source_document_series(authority):",
            "if high_risk or ineligible or history_only or unproven_current_state:",
            "_reject(\"path_b_history_only_current_state\", detail, source=bundle",
            "_reject(\"path_b_unproven_current_state\", detail, source=bundle",
            "_reject(\"path_b_history_only_current_state\", detail, **reject_kwargs)",
            "_reject(\"path_b_unproven_current_state\", detail, **reject_kwargs)",
            "ineligible=ineligible, history_only=history_only,",
            "unproven_current_state=unproven_current_state, gate_issues=gate_issues)",
            "unproven_current_state=unproven_current_state))",
            "axes=SRS.document_axis_index(authority), manifest=manifest,"):
        check(literal in src, f"写入侧接线必须逐字存在：{literal}")
    check(src.index("high_risk = _path_b_high_risk_surfaces(bundle=bundle)")
          < src.index("history_only = _path_b_history_only_current_state(")
          < src.index("unproven_current_state = _path_b_unproven_current_state("),
          "期间/来源角色与独立支撑结论两条判据都必须在**组装 Draft 之前**跑"
          "（不合格的候选连一份 Draft 都形不成）")
    check("roles = SRS.document_roles(authority)" in src
          and "axes=SRS.document_axis_index(authority)" in src,
          "角色与四轴台账都必须取自权威自己的来源集（不得从文件名/路径/入库时间推断）")

    # ==================================================================
    # 6. 请求面：来源角色随材料行投出（`pw-14` / `srsc-1`）
    # ==================================================================
    # 这一节回答的是「生成器那一侧的规则可不可执行」：只在门里判、却不让模型看见角色，
    # 模型就只能靠文档名猜「哪一份是旧年报」——那正是「按文件名写特例」。
    members = (
        # 文档系列材料：locator 带 document_id → 有角色
        _NS(member_ref="wmmref_a", payload_ref={"locator": {"document_id": DOC_OLDER}}),
        _NS(member_ref="wmmref_b", payload_ref={"locator": {"document_id": DOC_ANCHOR}}),
        # 结构化材料：locator 没有「文档系列」这条轴 → 不带角色（不是「读不到角色」）
        _NS(member_ref="wmmref_s", payload_ref={"locator": {"table_id": "t-1"}}),
    )
    fake_manifest = _NS(entries=members)
    roles = PW._material_source_roles(authority=_authority(R7B_SOURCE_SET),
                                      manifest=fake_manifest)
    check(roles == {"wmmref_a": "history_and_conflict_source",
                    "wmmref_b": "current_state_source"},
          f"文档系列材料必须逐份带上来源角色，结构化材料不带：{roles}")
    check(PW._material_source_roles(authority=_NS(pack_set=None),
                                    manifest=fake_manifest) == {},
          "没有来源集这条轴的权威（财务 / 附注 / 外部快照）必须短路成空表，不得抛")
    check(PW._material_source_roles(authority=_authority(R7B_SOURCE_SET),
                                    manifest=_NS(entries=())) == {},
          "本节没有文档系列材料时不得凭空造角色")

    # 5.x / 6.x 的替身都在喂**字段名**。字段名是这条接线唯一的静默失效点：读成 wire 键
    # `members` 时 `getattr(..., ())` 不抛错，只给出空元组，于是来源角色恒空、请求面不带
    # `source_role`、门与句读回双双放行——整条 O-12 核对变成死代码而回归全绿。下面三条把
    # 「真实类的字段名」「真实类的实例」「字段名写错时当场抛」钉死，让这条缺陷不可能再静默。
    real_fields = {f.name for f in dataclasses.fields(NS.WriterMaterialManifest)}
    check("entries" in real_fields and "members" not in real_fields,
          f"WriterMaterialManifest 的字段名是 `entries`（`members` 只是 wire 键）：{real_fields}")
    real_empty = NS.WriterMaterialManifest.create(members=[])
    check(PW._support_documents(manifest=real_empty) == {},
          "空清单是合法状态：读出来是空表，不是抛")
    try:
        PW._support_documents(manifest=_NS(members=(_member("pack-0", "m-old", DOC_OLDER),)))
    except PW.PackWriterError as exc:
        check("entries" in str(exc),
              f"字段名写错必须当场停，而不是静默空表：{exc}")
    else:
        check(False, "清单只带 wire 键 `members` 时必须抛出，不得静默返回空表"
                     "（静默空表会让期间/来源角色核对整条变死代码）")
    src_request = Path(PW.__file__).read_text(encoding="utf-8")
    for literal in ('"source_role": material_roles.get(member.member_ref),',
                    "material_roles = _material_source_roles(authority=authority, "
                    "manifest=manifest)",
                    "def _material_source_roles(*, authority: Any, manifest: Any)"):
        check(literal in src_request,
              f"请求面必须逐字把来源角色写进 materials 行：{literal}")
    check("if not documents or not SRS.has_source_document_series(authority):"
          in src_request,
          "请求面与门必须用**同一条**前置条件短路（两处口径不一致会让契约两侧各说一套）")

    # ==================================================================
    # 7. 组织侧：当前式措辞必须逐字来自声明的 Claim（`ng-13` / `nrules-14`）
    # ==================================================================
    # 正反例。这是 O-12 期间纪律在**组织**那一侧的落点：不加字面事实，只换时点。
    # 第 12 条的**判据实现一字未改**，但它所读的封闭标记集在其后两批各动过一次：指令 E 第 3 项
    # （`nrules-15`/`ng-14`）与定点业务闭环批 §二 2（`nrules-16`/`ng-15`：新增普遍化组
    # `一直`/`始终`/`历来`/`向来`/`一向`/`一贯`/`素来`/`从来` 与否定式普遍化组 `从未`/`未曾`）。
    # 判据实现不变但**判定集变了**——`A此外公司一直<B>` 在 `ng-14` 下通过、`ng-15` 下被拒，
    # 因此版本不得共用，下面第 7 节的反例照旧按**当前**标记集成立。
    # `ndc-2` 批再推一次：实现未动，动的是支撑边的**材料判别集**（`mbind-1` 收窄）⇒ 只门到 `ng-18`。
    check(NS.NARRATIVE_RULES_VERSION == "nrules-18"
          and NS.NARRATIVE_GATE_VERSION == "ng-18",
          "新增第 12 条必须随判定集升版（同一份正文在 ng-12 与 ng-13 下可以一通过一被拒）；"
          "指令 E 第 3 项、定点批 §二 2（扩标记集）、主营业务质量返修批 §四"
          "（改主体名抽取判据，判定集两度收窄）与 `ndc-2` 批（收窄材料判别集）各再推一次")
    check(NS.CURRENT_STATE_FRAMING_MARKERS
          and all(m not in SRS.PERIOD_QUALIFICATION_MARKERS
                  for m in NS.CURRENT_STATE_FRAMING_MARKERS),
          "两张表必须**互斥**：当前式措辞与期间限定词是相反用途，混在一张表里两边都会判反")
    check(not SRS.has_period_qualification("公司目前主要产品包括动力电池"),
          "正例前置：当前式措辞**不构成**期间限定（`srsc-1` 的口径，与第 12 条互补）")
    check(not SRS.has_period_qualification("公司仍主要产品包括动力电池"),
          "正例前置：裸「仍」同样不是期间限定")
    # 夹具：两条**原子子句**（组织器能逐字照转的 Claim 文本形态）。
    plain = ("推出创新解决方案", "产品应用于多个领域")
    # 反例一（**会被拒**）：组织语给一条断言加了材料没写的「目前」。
    framed = NS.unqualified_current_state_framing(
        "公司目前推出创新解决方案，此外，公司产品应用于多个领域。", plain)
    check(framed and framed[0][0] == "目前",
          f"组织语把断言挪到当前时点必须被定位出来：{framed}")
    # 反例二（**会被拒**）：同一个越权写在**后一条接缝**里，一样要被抓到。
    check(NS.unqualified_current_state_framing(
        "公司推出创新解决方案，此外，公司持续将产品应用于多个领域。", plain)[0][0] == "持续",
        "接缝不分前后：组织语在后面那条接缝里加「持续」同样必须被定位出来")
    # 反例三（**会被拒**）：Claim 段之后再续一个当前式措辞，仍是组织语写的。
    check(NS.unqualified_current_state_framing(
        "公司推出创新解决方案目前，此外，公司产品应用于多个领域。", plain)[0][0] == "目前",
        "写在接缝里的当前式措辞必须被定位出来")
    # 正例一（**放行**）：材料自己就写着这些词，正文逐字照转。
    with_word = ("持续推出创新解决方案", "产品应用于多个领域")
    ok = NS.unqualified_current_state_framing(
        "公司持续推出创新解决方案，此外，公司产品应用于多个领域。", with_word)
    check(ok == () and NS.current_state_framing_hits(with_word[0]) == ("持续",),
          f"Claim 文本自己写着「持续」时，组织语照转必须放行：{ok}")
    # 正例二（**放行**）：组织段里根本没有当前式措辞（中性并列）。
    check(NS.unqualified_current_state_framing(
        "公司推出创新解决方案，此外，公司产品应用于多个领域。", plain) == (),
        "只用中性并列衔接的句子不得被判违规（判据只扫组织段，不扫 Claim 段）")
    # 边界：把当前断言**降格**成历史（「此前」「曾经」）不在本条的闭集里——本条的闭集只收
    # 「把断言抬到当前」的词；降格走「不得改变强度」那条既有判据。写清这条边界，是为了让
    # 后来者**扩表时知道该扩哪一张**，而不是把两张表混成一张。
    check(NS.unqualified_current_state_framing(
        "公司此前推出创新解决方案，此外，公司产品应用于多个领域。", plain) == ()
          and "此前" not in NS.CURRENT_STATE_FRAMING_MARKERS
          and "曾经" not in NS.CURRENT_STATE_FRAMING_MARKERS,
          "降格写法不得被假装成本条覆盖（本条只管把断言抬到当前时点）")
    check(NS.unqualified_current_state_framing(
        "公司推出创新解决方案", ("推出创新解决方案",)) == (),
        "定位不成立（声明与正文不一致）时返回空，由判据 a 去拒——本条不越权替它判")
    # 门里的接线：第 12 条必须真的跑在核验链上（不是只加了一个没人调用的函数）。
    # 「模型被告知要遵守什么」那一半在 `evals/test_demo_narrative_organizer` §5 里钉住——
    # 判据进代码不等于纪律进 prompt，两处都要断言，本模块只管前者。
    ns_src = Path(NS.__file__).read_text(encoding="utf-8")
    for literal in ("framing = unqualified_current_state_framing(sentence.text, authorized)",
                    "组织语把断言挪到了当前时点"):
        check(literal in ns_src, f"门里必须逐字接线第 12 条：{literal}")
    check(ns_src.index("framing = unqualified_current_state_framing(")
          > ns_src.index("unattributed = unattributed_self_description("),
          "第 12 条必须挂在既有的逐句核验链上（第 11 条之后），不得另起一条没人调用的路径")

    # ==================================================================
    # 8. 独立支撑结论（`srsc-2`）：四条边界之外的第五件事——「落在当前锚上」不等于
    #    「新材料确实核实了这条命题」。以下逐条是**指令点名**的四类反例。
    # ==================================================================
    check(SRS.RULE_VERSION == "srsc-4",
          f"第四条边界与后续两次收紧都必须升版（srsc-2 / srsc-3 / srsc-4 下同一条候选"
          f"可以一放一拒）：{SRS.RULE_VERSION}")
    # `srsc-2` / `srsc-3` / `srsc-4` 都只增加要求，不放宽旧要求：角色集与期间词表一字未改。
    check(SRS.CANNOT_ESTABLISH_CURRENT_STATE_ROLES == ("history_and_conflict_source",)
          and SRS.PERIOD_QUALIFICATION_MARKERS == ("报告期",),
          "srsc-2 / srsc-3 / srsc-4 不得顺手改动 srsc-1 的角色集或期间词表"
          "（放宽旧要求＝把旧材料当前化放回来）")
    check(set(SRS.CURRENT_STATE_SUPPORT_RESULTS) == {"extractive", "unproven", "mismatch"}
          and len(SRS.CURRENT_STATE_SUPPORT_REASONS) == len(set(SRS.CURRENT_STATE_SUPPORT_REASONS)),
          "结论与理由码都必须是闭集（自由文本理由会让「为什么这条不能承重」有两个答案）")

    # 四轴的分工必须**在类型层**就对：有声明面的两轴参与比对，另两轴只要求在场。
    # 这条断言是防「假门」的——若哪天有人把 company/evidence_set 也写成比对，就说明
    # 调用方开始把实际值当声明值传（恒真），本条会先红。
    check(set(SRS.COMPARED_AXES) | set(SRS.PRESENCE_ONLY_AXES) == set(SRS.IDENTITY_AXES)
          and set(SRS.COMPARED_AXES) & set(SRS.PRESENCE_ONLY_AXES) == set(),
          "四轴必须被「比对轴 / 在场轴」二分且不重叠："
          f"{SRS.COMPARED_AXES} / {SRS.PRESENCE_ONLY_AXES}")
    check(SRS.COMPARED_AXES == ("document_id", "document_version"),
          "只有 document_id / document_version 在边上有声明面（`locator_ref` 容器 "
          "`evidence_document:{id}@{version}`）；把它当四轴全比对就是假门")

    def _reading(text: str, *, document_id: str = DOC_ANCHOR,
                 document_version: str = "v1", evidence_set: str = EVIDENCE_SET,
                 company: str = COMPANY,
                 declared_document_id: str | None = None,
                 declared_document_version: str | None = None,
                 declared_payload_hash: str = "ph-1",
                 actual_payload_hash: str = "ph-1",
                 locator_ref: str = "loc-1"
                 ) -> SRS.CurrentStateSupportReading:
        """一条「可能充当当前锚」的边：声明侧默认与实际一致，反例再逐轴掰开。"""
        return SRS.CurrentStateSupportReading(
            company_id=company, document_id=document_id,
            document_version=document_version, evidence_set_version=evidence_set,
            declared_document_id=(document_id if declared_document_id is None
                                  else declared_document_id),
            declared_document_version=(document_version if declared_document_version is None
                                       else declared_document_version),
            declared_payload_hash=declared_payload_hash,
            actual_payload_hash=actual_payload_hash,
            locator_ref=locator_ref, reading_view=text,
        )

    NEW_TEXT = ("公司主要从事动力电池、储能电池的研发、生产、销售，"
                "产品可应用于乘用车、商用车、表前储能、表后储能等领域。")

    def _judge(claim: str, readings) -> SRS.CurrentStateSupportVerdict:
        return SRS.current_state_support_is_extractive(
            claim, candidate_revision="sdrev-1", readings=readings)

    # ---- 正例：严格抽取式（连续包含 + 四轴闭合）⇒ extractive ------------------
    ok = _judge("公司主要从事动力电池、储能电池的研发、生产、销售",
                (_reading(NEW_TEXT),))
    check(ok.result == "extractive"
          and ok.reason_code == "extractive_contiguous_containment",
          f"候选全文是新材料读视图的连续子串时必须判 extractive：{ok}")
    check(ok.candidate_revision == "sdrev-1" and ok.matched_payload_hash == "ph-1"
          and ok.matched_locator_ref == "loc-1",
          "结论必须绑定候选 revision 与命中那条边的精确载荷/定位（否则结论对不上被审的那一版）")
    # 空白不构成差异（仅空白归一），这是「同一条正文被拆行/加空格」的正常情形。
    check(_judge("公司主要从事动力电池、储能电池的研发、生产、销售",
                 (_reading(NEW_TEXT.replace("、储能电池", "、储能电池\n")),)).result
          == "extractive",
          "仅空白差异不得让包含关系断掉（材料的真实读视图本来就可能跨行）")

    # ---- 反例①（指令点名）：旧材料称 A／新材料称「已停止 A」⇒ 不得判 extractive ----
    neg = _judge("公司从事动力电池业务",
                 (_reading("公司已停止从事动力电池业务。"),))
    check(neg.result == "unproven",
          f"新材料称「已停止 A」而候选称「A」时必须记 unproven，不得当成已核实：{neg}")
    check(neg.result != "extractive" and "未" not in neg.reason_code,
          "这条结论必须靠**字面连续性**自然落空，不得靠否定词表——"
          "判据一旦认识「已停止」是不是否定词，就等于把否定判据塞进了词表")

    # ---- 反例②（指令点名）：新材料只有同产品名 ⇒ 不得判 extractive --------------
    check(_judge("公司产品广泛应用于储能领域并具备成本优势",
                 (_reading("公司储能电池产品包括电芯、模组或电箱及电池包。"),)).result
          == "unproven",
          "整句断言不得因为材料里出现了同一个产品名就被判已核实")

    # ---- 反例③（指令点名）：旧+新拼接才成立 ⇒ 不得判 extractive ------------------
    stitched = _judge("公司通过自建生产基地为主并通过合资建厂扩充产能",
                      (_reading("公司通过自建生产基地为主。"),
                       _reading("并通过合资建厂、技术授权等方式扩充产能。")))
    check(stitched.result == "unproven",
          f"包含只在**单份**读视图内判定，跨材料拼接必须构造性落空：{stitched}")

    # ---- 反例④（指令点名）：同 ID 错版本 ⇒ mismatch，fail-closed ------------------
    # 这是编号级攻击的正面落点：`document_id` 一模一样，只有 `document_version` 不同。
    wrong_ver = _judge("公司主要从事动力电池、储能电池的研发、生产、销售",
                       (_reading(NEW_TEXT, document_version="v2",
                                 declared_document_version="v1"),))
    check(wrong_ver.result == "mismatch"
          and wrong_ver.reason_code == "axis_mismatch_document_version",
          f"同 document_id 而版本对不上必须 fail-closed：{wrong_ver}")
    check(_judge("公司主要从事动力电池、储能电池的研发、生产、销售",
                 (_reading(NEW_TEXT, declared_document_id="NDSD_OTHER"),)).reason_code
          == "axis_mismatch_document_id",
          "声明侧文档 ID 与实际台账不一致同样是身份链断裂")
    check(_judge("公司主要从事动力电池、储能电池的研发、生产、销售",
                 (_reading(NEW_TEXT, actual_payload_hash="ph-2"),)).reason_code
          == "axis_mismatch_payload_hash",
          "载体摘要对不上说明边指的是另一份载荷，必须 fail-closed")
    # 在场轴（没有声明面）只要求非空：缺失单成一类，不得与「核对过并否决」混为一谈。
    check(_judge("公司主要从事动力电池、储能电池的研发、生产、销售",
                 (_reading(NEW_TEXT, company=""),)).reason_code == "axis_missing",
          "主体轴在实际台账侧缺失时必须 axis_missing（该轴无声明面，不假装比对）")
    check(_judge("公司主要从事动力电池、储能电池的研发、生产、销售",
                 (_reading(NEW_TEXT, evidence_set=""),)).reason_code == "axis_missing",
          "Evidence Set 轴在实际台账侧缺失时必须 axis_missing——"
          "该轴由 Pack 的 DocumentSourceSet 闭合，本判据只要求它在场，不假装比对")
    check(_judge("公司主要从事动力电池、储能电池的研发、生产、销售",
                 (_reading(NEW_TEXT, declared_document_id=""),)).reason_code == "axis_missing",
          "声明侧身份读不出来必须 axis_missing，不得当成「不匹配」")

    # ---- mismatch 支配 extractive：边集里有一条身份链断了，整个候选 fail-closed ----
    mixed = _judge("公司主要从事动力电池、储能电池的研发、生产、销售",
                   (_reading(NEW_TEXT),
                    _reading(NEW_TEXT, document_version="v9", declared_document_version="v1")))
    check(mixed.result == "mismatch",
          f"一条边四轴闭合且包含成立、另一条边版本错位时，整体必须 mismatch（fail-closed）：{mixed}")

    # ---- 缺输入 / 空候选 -------------------------------------------------------
    check(_judge("公司主要从事动力电池、储能电池的研发、生产、销售", ()).reason_code
          == "no_reading_supplied",
          "没有边时记 unproven/no_reading_supplied，不得默认放行")
    check(_judge("   ", (_reading(NEW_TEXT),)).reason_code == "candidate_text_empty",
          "空候选（仅空白）不得因「空串是任何串的子串」被判 extractive")
    try:
        SRS.current_state_support_is_extractive("公司A", candidate_revision="", readings=())
    except SRS.SourceRoleScopeError as exc:
        check("revision" in str(exc), f"缺候选 revision 必须当场停：{exc}")
    else:
        check(False, "缺候选 revision 时不得静默给出结论（结论必须能对上被审的那一版）")

    # ---- 标点不归一（**不得**标点镜像）：换一个逗号就断，这正是要的严格性 ---------
    check(_judge("公司从事动力电池业务，产品包括电芯",
                 (_reading("公司从事动力电池业务、产品包括电芯。"),)).result
          == "unproven",
          "标点差异必须让包含关系断掉（判据不得做标点镜像，否则换个标点就成了「已核实」）")

    # ==================================================================
    # 8b. `srsc-3`：抽掉源句自带的期间/范围限定（**指令点名**的反例）
    #
    # `srsc-2` 只要求「候选是正文的连续子串」。于是一条源句可以被截成**合法连续子串**
    # 却把限定语与后半句一起丢掉：源文「报告期内，公司销售境外……较上年同期相比未发生
    # 明显变化。」截成「公司销售境外的主要产品为电池系统。」——读者读到的是一句
    # **没有期间的持续现状**。这是**口径变了**，不是「材料不够新」，因此必须单独成一条
    # 结论，否则写侧只能靠「模型自觉」不这么截。
    # ==================================================================
    check("source_period_scope_dropped" in SRS.CURRENT_STATE_SUPPORT_REASONS,
          "`srsc-3` 的新结论必须是**闭集里的具名 reason code**，不得塞进自由文本"
          "（否则「为什么这条不能承重」会有两个答案）")
    check(SRS.CURRENT_STATE_SUPPORT_REASONS.index("source_period_scope_dropped") > 0,
          "新码不得挤掉原有码的位置：旧码是历史 run 的回读面，只增不改")

    # ---- 反例（指令原文那一句）：截掉「报告期内」与后半句 ⇒ unproven ---------------
    OUTSEAS_SENTENCE = ("报告期内，公司销售境外的主要产品为电池系统，"
                        "较上年同期相比未发生明显变化。")
    cut = _judge("公司销售境外的主要产品为电池系统",
                 (_reading(OUTSEAS_SENTENCE),))
    check(cut.result == "unproven"
          and cut.reason_code == "source_period_scope_dropped",
          f"源句自带「报告期内」而候选把它截掉时必须记 unproven/"
          f"source_period_scope_dropped（按候选措辞证不出来，不是「材料不足」）：{cut}")
    check(cut.matched_payload_hash == "ph-1" and cut.matched_locator_ref == "loc-1",
          "这条结论同样必须绑定命中那条边的载荷/定位——否则读回来不知道是哪一句引出的")
    check(cut.reason_code != "not_extractive_in_any_current_source",
          "「口径变了」与「压根没材料包含它」必须是两个码：处置相同（都不写），"
          "但读回来意思不同，混成一个码就把口径问题读成了材料问题")

    # ---- 正例：连同限定语一起包住 ⇒ extractive（可核验的期间还在）-----------------
    kept = _judge("报告期内，公司销售境外的主要产品为电池系统",
                  (_reading(OUTSEAS_SENTENCE),))
    check(kept.result == "extractive",
          f"候选自己带着「报告期内」时，期间仍可核验，必须判 extractive：{kept}")
    # 年份形态同样算期间限定（不只是「报告期」这一个词）。
    check(_judge("公司实现营业收入100亿元",
                 (_reading("2024年公司实现营业收入100亿元。"),)).reason_code
          == "source_period_scope_dropped",
          "源句的期间限定写成具体年份时同样是限定语，截掉一样落 unproven"
          "（判据不得只认「报告期」一个词）")
    # 跨句：候选横跨「带限定的那半句」与「被限定的那半句」⇒ 两句都算覆盖 ⇒ 落空。
    spanned = _judge("公司实现营业收入100亿元。公司销售境外的主要产品为电池系统",
                     (_reading("报告期内，公司实现营业收入100亿元。"
                               "公司销售境外的主要产品为电池系统。"),))
    check(spanned.result == "unproven"
          and spanned.reason_code == "source_period_scope_dropped",
          f"候选跨句时**覆盖到的每一句**都参与判定（截掉前一分的限定语同样落空）：{spanned}")

    # ---- 不许反过来（指令原文：不要把所有稳定的客观描述都强行加日期）--------------
    stable = _reading("公司主要从事动力电池、储能电池的研发、生产、销售，"
                      "产品可应用于乘用车、商用车、表前储能、表后储能等领域。")
    check(_judge("公司主要从事动力电池、储能电池的研发、生产、销售", (stable,)).result
          == "extractive",
          "源句**本来就没有**期间限定的稳定客观描述，必须照旧判 extractive"
          "——本条不得逼着描述句去补一个源文里没有的日期")
    check(not SRS.source_period_scope_dropped("公司主要从事动力电池的研发", stable.reading_view),
          "判据是纯函数：没有限定可截时恒为 False，不因材料**别处**出现过期间而连坐")
    # **别处的限定语不得连坐**：同一份材料里另一句带期间，不影响本句的稳定描述。
    other_sentence = _reading("报告期内，公司营业收入同比增长。"
                              "公司主要从事动力电池、储能电池的研发、生产、销售。",
                              document_id=DOC_ANCHOR)
    check(_judge("公司主要从事动力电池、储能电池的研发、生产、销售",
                 (other_sentence,)).result == "extractive",
          "同材料**别的句子**带不带期间，与这条候选无关：判据只按候选覆盖到的那几句判，"
          "写成「材料里出现过期间」就会把所有描述句连坐")

    # ---- 干净命中优先：一处截断不得让另一处的完整包含失效 -------------------------
    prefer_clean = _judge("公司销售境外的主要产品为电池系统",
                          (_reading(OUTSEAS_SENTENCE, document_id=DOC_ANCHOR),
                           _reading("公司销售境外的主要产品为电池系统。",
                                    document_id=DOC_OTHER_SERIES,
                                    evidence_set=EVIDENCE_SET,
                                    locator_ref="loc-2")))
    check(prefer_clean.result == "extractive"
          and prefer_clean.matched_locator_ref == "loc-2",
          f"多份材料命中时只要有一份**所在句不带限定语**（不必截），就必须判 extractive"
          f"——且命中的是那一条干净边：{prefer_clean}")
    # 但身份链断裂仍支配一切（`mismatch` 优先于本条）。
    check(_judge("公司销售境外的主要产品为电池系统",
                 (_reading(OUTSEAS_SENTENCE, document_version="v2",
                           declared_document_version="v1"),)).result == "mismatch",
          "`srsc-3` 不得把 fail-closed 的身份判据顶掉：错版本仍然是 mismatch，不是 unproven")

    # ---- 判据的取句面逐字读回来 --------------------------------------------------
    check(SRS.covering_sentences(OUTSEAS_SENTENCE, "公司销售境外的主要产品为电池系统")
          == (OUTSEAS_SENTENCE,),
          "覆盖到的源句必须**连限定语整句**返回（返回被截的片段就等于判据自己也在截）")
    check(len(SRS.covering_sentences("甲句。乙句。", "乙句")) == 1
          and SRS.covering_sentences("甲句。乙句。", "乙句")[0].startswith("乙"),
          "句边界按句末标点切：候选落在第二句时就只覆盖第二句")
    check(SRS.covering_sentences(OUTSEAS_SENTENCE, "")
          == SRS.covering_sentences("", "公司") == (),
          "空候选 / 空正文都返回空 tuple（本函数只回答「覆盖了哪几句」，不替调用方决定处置）")

    # ==================================================================
    # 9. `srsc-2` 的**取数面与接线**：判据是纯函数（§8），但「实际身份从哪来、声明身份从哪来、
    #    正文从哪来、哪条候选进得来」四件事都在接线层，四件都必须在场才算「这条核对不是
    #    一个没人调用的函数」。真实类下的整链读数由离线重放模块覆盖；这里用真类喂取数入口、
    #    用最轻替身喂接线函数。
    # ==================================================================

    # 9.1 四轴台账（**实际侧**）：`{document_id: 四轴}`，出自 Pack 自己的来源集。
    axis_table = SRS.document_axis_index(_authority(R7B_SOURCE_SET))
    check(axis_table.get(DOC_ANCHOR) == {"company_id": COMPANY, "document_id": DOC_ANCHOR,
                                         "document_version": "v1",
                                         "evidence_set_version": EVIDENCE_SET},
          f"台账必须逐份给出**完整四轴**（实为 {axis_table.get(DOC_ANCHOR)}）")
    check(set(axis_table) == {DOC_ANCHOR, DOC_OLDER, DOC_OTHER_SERIES},
          "台账按 document_id 收敛，本节三份材料各一条")
    check(set(axis_table[DOC_ANCHOR]) == set(SRS.IDENTITY_AXES),
          "台账给出的轴必须与 :data:`SRS.IDENTITY_AXES` 逐轴同名（少一轴就少一次比对）")
    # 同一 document_id 在两个 Pack 上登着不同版本 ⇒ 按哪一份都是猜（与「同一文档两个角色」同形）。
    _key_v2 = SM.SourceDocumentKey(company_id=COMPANY, document_id=DOC_OLDER,
                                   document_version="v2", evidence_set_version=EVIDENCE_SET)
    expect_raises(
        "同一 document_id 在本节不同 Pack 上四轴不一致时必须抛（按哪一份都是猜）",
        lambda: SRS.document_axis_index(_authority(
            _source_set((DOC_OLDER, "history_and_conflict_source")),
            TS.DocumentSourceSet(members=((_key_v2, "history_and_conflict_source"),)))),
        SRS.SourceRoleScopeError, needle="四轴不一致")
    expect_raises(
        "空 pack_set 必须抛：四轴台账只对 topic Pack 权威有意义",
        lambda: SRS.document_axis_index(_NS(pack_set=_NS(packs=()))),
        SRS.SourceRoleScopeError, needle="topic Pack")
    # 四轴不完整那一支是给**反序列化**路径准备的（`SourceDocumentKey.__post_init__` 会先拒掉
    # 构造期的不完整键），因此这里只能拿只读替身喂进去——它测的正是「台账来自反序列化时
    # 会不会静默降级成比对剩下的轴」。
    _incomplete = _NS(pack_id="pack-x", source_set=_NS(members=(
        (_NS(company_id=COMPANY, document_id="", document_version="v1",
             evidence_set_version=EVIDENCE_SET), "current_state_source"),)))
    expect_raises(
        "来源集成员四轴不完整时必须当场抛（不得降级成「比对剩下的轴」）",
        lambda: SRS.document_axis_index(_NS(pack_set=_NS(packs=(_incomplete,)))),
        SRS.SourceRoleScopeError, needle="四轴不完整")

    # 9.2 声明容器（**声明侧**）：只有 `evidence_document:{id}@{ver}` 有声明面。
    _CONTAINER = "evidence_document:" + DOC_ANCHOR + "@sha256-c15272977147dee7"
    check(SRS.declared_axes_from_locator(
        {"owner": _CONTAINER + "#block_span"}) == (DOC_ANCHOR, "sha256-c15272977147dee7"),
          "`evidence_document:{id}@{ver}#…` 必须拆成两轴（真实容器形状，逐字取自离线重放正文）")
    check(SRS.declared_axes_from_locator(
        {"owner": _CONTAINER + "#whole_payload"}) == (DOC_ANCHOR, "sha256-c15272977147dee7"),
          "`#whole_payload` 与 `#block_span` 的声明侧完全相同（wgctx 的两种定位后缀）")
    check(SRS.declared_axes_from_locator({"owner": _CONTAINER})
          == (DOC_ANCHOR, "sha256-c15272977147dee7"),
          "没有 `#` 后缀时同样成立（拆的是第一段，不是「必须有后缀」）")
    for _locator in ({}, {"owner": ""}, {"owner": "financial_snapshot:ffpa_1"},
                     {"owner": "external_snapshot:es_1"}, {"page": 3}, None, "owner", 7):
        check(SRS.declared_axes_from_locator(_locator) is None,
              f"没有 evidence 声明面的定位必须返回 `None`（不是抛、也不是空串）：{_locator!r}")
    expect_raises(
        "是 evidence 容器却拆不出两轴时必须抛：静默退化成「没有声明」会把 mismatch 读成 unproven",
        lambda: SRS.declared_axes_from_locator({"owner": "evidence_document:" + DOC_ANCHOR}),
        SRS.SourceRoleScopeError, needle="拆不出")
    expect_raises(
        "拼出来的身份（多一个 `@`）同样必须抛",
        lambda: SRS.declared_axes_from_locator(
            {"owner": "evidence_document:a@b@c#block_span"}),
        SRS.SourceRoleScopeError, needle="拆不出")

    # 9.3 `srsc-2` 的**适用边界**（在真实输入面上逐条读回来）。它们的共同判据是「这条候选进
    #     不进得来」——进不来时函数**在拿到正文之前**就返回，因此这里可以显式传
    #     `material_context=None` 来把边界与「缺正文就抛」两件事分开测。
    def unproven_of(candidates, proposals, *, context=None) -> dict:
        return PW._path_b_unproven_current_state(
            bundle=_NS(candidates=tuple(candidates), proposals=tuple(proposals)),
            documents=documents, roles=R7B_ROLES, axes=axis_table,
            manifest=manifest, material_context=context)

    # 边界一：只由同类较旧材料支撑 ⇒ 那是 `srsc-1` 的事，本判据让路（**互斥的那一侧**）。
    check(unproven_of([_cand("u1", TEXT_CURRENT)],
                      [_edge("u1", "pack-0", "m-old")]) == {},
          "只由同类较旧材料支撑的候选不进本判据（`srsc-1` 负责，两条判据互斥）")
    # 边界二：跨系列成员不设锚 ⇒ 两条判据都不进（模块头边界 5）。
    check(unproven_of([_cand("u2", TEXT_CURRENT)],
                      [_edge("u2", "pack-0", "m-other")]) == {},
          "只由跨系列成员支撑的候选两条判据都不进——它由自己那条链的资格判据负责")
    # 边界三：候选**自己写明期间** ⇒ 它是历史断言，不是当前态断言。
    # 这一条是**必须显式判**的：`unqualified_current_state` 在「支撑边落在当前锚上」时提前
    # 返回 False，覆盖不到它，于是「有锚边 + 写了期间」会带着期间限定落进抽取式判定。
    check(unproven_of([_cand("u3", TEXT_QUALIFIED)],
                      [_edge("u3", "pack-0", "m-anchor")]) == {},
          "候选自己写明了期间的（有锚边也一样）不进本判据——它不是「当前态断言」")
    # 边界四／五：路径 A 与非文档系列边。
    check(unproven_of([_cand("u4", TEXT_CURRENT)],
                      [_edge("u4", "pack-0", "m-anchor",
                             path="path_a_prevalidated")]) == {},
          "路径 A 候选不进本判据（写入侧到不了具体来源文档）")
    check(unproven_of([_cand("u5", TEXT_CURRENT)],
                      [_edge("u5", "pack-0", "m-anchor"),
                       _edge("u5", "pack-0", "m-struct")]) == {},
          "支撑边里有一条不在这条轴上 ⇒ 整条候选不适用（不得只按剩下的锚边下结论）")
    check(unproven_of([_cand("u6", TEXT_CURRENT)], [_edge("u6", "pack-0", "m-absent")]) == {},
          "成员不在本节材料清单里 ⇒ 这条边不属于文档系列轴")
    # 缺 `wmctx-1` 正文上下文：**抛**，不静默返回空表（空表会让这条判据在真实链上变死代码）。
    expect_raises(
        "缺 wmctx-1 材料正文上下文时必须抛：只有 manifest 身份时任何结论都是猜",
        lambda: unproven_of([_cand("u7", TEXT_CURRENT)],
                            [_edge("u7", "pack-0", "m-anchor")]),
        PW.PackWriterError, needle="wmctx-1")

    # 9.4 取数面的**来源**：实际侧取自台账、声明侧取自成员自己的容器、正文经 `wmctx-1` 复检。
    #     真实类下的整链读数由离线重放覆盖，这里钉的是「这四样各自只有一个出处」。
    src_axes = Path(PW.__file__).read_text(encoding="utf-8")
    for literal in (
            "axis = dict(axes.get(str(documents.get(ref, \"\") or \"\"), {}) or {})",
            "entry = manifest.entry_for(ref)",
            "declared = SRS.declared_axes_from_locator(",
            "getattr(entry, \"locator_ref\", None)) if entry is not None else None",
            "resolved = MC.reading_for_manifest_member(",
            "material_context=material_context, member=entry)",
            "declared_payload_hash=(str(getattr(entry, \"payload_hash\", \"\") or \"\")",
            "if SRS.has_period_qualification(str(candidate.claim_text)):",
            "if SRS.has_period_qualification(claim_text):"):
        check(literal in src_axes, f"`srsc-2` 的取数面必须逐字存在：{literal}")
    check("axes=SRS.document_axis_index(authority), manifest=manifest,"
          in src_axes,
          "台账必须由**权威自己的来源集**派生（不得从文件名/路径/入库时间推断）")
    check("overlap = sorted(set(history_only) & set(unproven_current_state))" in src_axes,
          "两条判据的**互斥**必须在接线处当场断言，不靠调用顺序保证"
          "（同时命中说明其中一条的取数面出了问题）")

    # 9.5 逐候选审计 / 整束明细 / 定向说明 / 裁出：第四组点名要带自己的明细字段。
    bundle_u = _NS(candidates=(_cand("u1", TEXT_CURRENT), _cand("u2", TEXT_CURRENT),
                               _cand("u3", TEXT_CURRENT)),
                   proposals=(_edge("u1", "pack-0", "m-anchor"),
                              _edge("u2", "pack-0", "m-anchor"),
                              _edge("u3", "pack-0", "m-anchor")))
    def _finding(cause: str = "not_extractive_in_any_current_source",
                 member: str = ANCHOR_EDGE) -> "PW.UnprovenCurrentStateFinding":
        """`srsc-2`/`srsc-3` 的结论记录：成员身份 + 原因码**同进同出**。"""
        return PW.UnprovenCurrentStateFinding(member_refs=(member,), cause_code=cause)

    finding = _finding()
    audit_u = PW._candidate_audit(bundle=bundle_u, unproven_current_state={"u1": finding})
    check([a.reasons for a in audit_u] == [("path_b_unproven_current_state",),
                                           ("not_individually_implicated",),
                                           ("not_individually_implicated",)],
          f"逐候选审计必须逐条给出原因（实为 {[a.reasons for a in audit_u]}）")
    check(audit_u[0].unproven_current_state_member_refs == (ANCHOR_EDGE,)
          and audit_u[0].to_dict()["unproven_current_state_member_refs"] == [ANCHOR_EDGE],
          "新原因的明细字段必须真的带上那几条**锚边**（有原因没明细 = 无法复核）")
    check(audit_u[0].unproven_current_state_cause_code
          == "not_extractive_in_any_current_source"
          and audit_u[0].to_dict()["unproven_current_state_cause_code"]
          == "not_extractive_in_any_current_source",
          "原因码必须与成员身份一起落进产物（`srsc-3`：同一原因下的分级必须可读回）")
    check(audit_u[0].history_only_member_refs == (),
          "两条期间轴的明细字段**互斥**：被 `srsc-2` 点名的那条不得同时带 `srsc-1` 的明细")
    expect_raises(
        "有锚边明细却没写 `path_b_unproven_current_state` 原因必须当场抛",
        lambda: PW.RejectedCandidateAudit(
            candidate_id="u1", reasons=("not_individually_implicated",),
            unproven_current_state_member_refs=(ANCHOR_EDGE,)),
        PW.PackWriterError, needle="不一致")
    expect_raises(
        "写了原因却没带锚边明细同样必须当场抛",
        lambda: PW.RejectedCandidateAudit(
            candidate_id="u1", reasons=("path_b_unproven_current_state",)),
        PW.PackWriterError, needle="不一致")

    detail_u = PW._path_b_rejection_detail(high_risk={}, ineligible={},
                                           unproven_current_state={"u1": finding})
    check(ANCHOR_EDGE in detail_u and "srsc-2" in detail_u
          and "严格抽取式" in detail_u and "连续子串" in detail_u,
          f"整束明细必须把第四组逐候选点名与口径写进去（实为 {detail_u}）")
    check("**不等于**" in detail_u and "恰好含一份较新的材料" in detail_u,
          "口径必须是「有锚边 ≠ 已核实」（一句话说反，模型就会照反的改）")
    check(detail_u.count("路径 B 候选") == 1 and "\n" not in detail_u,
          "只有这一组时同样合成**一句**（额度只有一份，不得记成多次被拒）")
    four = PW._path_b_rejection_detail(high_risk={"c9": ("12,345",)},
                                       ineligible={"c8": (STRUCT_EDGE,)},
                                       history_only={"c1": (OLD_EDGE,)},
                                       unproven_current_state={"u1": finding})
    check(four.count("路径 B 候选") == 4 and "\n" not in four
          and all(t in four for t in ("c9", "c8", "c1", "u1")),
          f"四组同时出现时必须仍是**一句**：{four}")
    for _rejected in ("联合材料蕴含", "最长公共子串", "标点镜像", "否定词表"):
        check(_rejected in detail_u,
              f"四种「证明」必须逐字排除，否则模型会拿它们当出路：{_rejected}")
    check("pbface-4" in PW._path_b_rejection_detail.__doc__,
          "整束明细的版本号必须随第四组前进（`pbface-3` 只有三组）")

    note_u = PW._directed_reproposal_note(bundle=bundle_u, detail=detail_u, surfaces={},
                                          unproven_current_state={"u1": finding})
    check(f"（{PW.HIGH_RISK_REPROPOSAL_NOTE_VERSION}）" in note_u and "hrrp-5" in note_u,
          "说明文本是输入面的一部分，必须带版本号（多一组点名、或多一条分级出路，即升版）")
    check(TEXT_CURRENT in note_u and ANCHOR_EDGE in note_u,
          "被点名的候选与那几条锚边必须**逐字**引用")
    check("4b." in note_u and "收回到" in note_u and "连续子串" in note_u,
          "第四组必须有自己的编号出路（把措辞收回到该份材料的原文）")
    check("**不要**靠拼接两份材料" in note_u and "不要**顺手把它写成否定" in note_u,
          "新出路必须同时否掉「拼接 / 换标点 / 大部分相同 / 写成否定」四种偷懒修法")
    check("逐条照原样保留" in note_u and "重新输出一份完整的提案集" in note_u,
          "其余候选逐条照原样保留、整份提案集重新过门这两条不因新增一组点名而松动")
    check("hrrp-5" in PW._directed_reproposal_note.__doc__,
          "说明函数自己的文档必须记下第四组点名与 `srsc-3` 的分级（否则后来者只看到三组）")
    # `srsc-3`：分级必须真的改变**发出去的说明**——两种来由各写各的，模型才不用二猜一。
    period_note = PW._directed_reproposal_note(
        bundle=bundle_u, detail=detail_u, surfaces={},
        unproven_current_state={"u1": _finding("source_period_scope_dropped")})
    check("source_period_scope_dropped" in period_note
          and "**自己带着期间/范围限定**" in period_note,
          f"截图式包含必须有自己的说法（含原因码），实为末尾：{period_note[-700:]!r}")
    check("不必" in period_note and "删掉" in period_note,
          "必须显式**否掉**「按 4b 删掉它」这条偷懒修法：这条句子本来写得出、也核实得了")
    check("逐字来自原文" in period_note,
          "同时不得让模型现编一个期间词——限定语只能逐字来自原文")
    check("**确实**包含这条断言" not in note_u
          and "**确实**包含这条断言" in period_note,
          "**逐候选点名**那一侧必须互斥：材料不足的候选不得被说成「包含它、只是截了限定语」"
          "（两条出路共用一份出口清单是允许的，出口清单按原因码分流）")
    check(period_note.count("source_period_scope_dropped")
          > note_u.count("source_period_scope_dropped"),
          "原因码在**出口清单**里出现一次就够（它说的是「标了该码的那几条」），"
          "截断式那一侧另在逐候选点名里再点名一次")
    check("source_period_scope_dropped"
          in PW._directed_reproposal_note.__doc__,
          "说明函数自己的文档必须记下 `srsc-3` 的分级与各自出路")
    check("source_period_scope_dropped" in PW._path_b_rejection_detail.__doc__
          or "source_period_scope_dropped" in detail_u,
          "整束明细同样要能读出这一分级（否则被拒记录只记了「证不出来」）")

    carved_u = PW.carve_out_candidate_subset(
        plan={"claim_candidates": [{"candidate_key": "u1"}, {"candidate_key": "u2"},
                                   {"candidate_key": "u3"}]},
        bundle=bundle_u, high_risk={}, ineligible={},
        unproven_current_state={"u1": finding}, from_attempt=1, to_attempt=2,
        revision_for_plan=lambda plan: "rev-3")
    check(carved_u is not None, "有一条被点名、其余幸存时必须真的裁出")
    _carved_plan, _carved_decision = carved_u
    check([s["candidate_key"] for s in _carved_plan["claim_candidates"]] == ["u2", "u3"],
          "裁出只做「从有序列表里去掉被点名的那几项」，幸存者逐字照原样")
    check(_carved_decision.excluded[0].reasons == ("path_b_unproven_current_state",)
          and _carved_decision.excluded[0].unproven_current_state_member_refs == (ANCHOR_EDGE,)
          and _carved_decision.excluded[0].to_dict()["unproven_current_state_member_refs"]
          == [ANCHOR_EDGE],
          f"被排除者必须带 typed 原因与逐字锚边明细：{_carved_decision.excluded[0].to_dict()}")
    check(PW.CANDIDATE_CARVE_OUT_REASONS.index("path_b_unproven_current_state")
          == PW.CANDIDATE_CARVE_OUT_REASONS.index("path_b_history_only_current_state") + 1,
          f"两条期间轴的原因必须相邻地登记进裁出原因闭集"
          f"（实为 {PW.CANDIDATE_CARVE_OUT_REASONS}）")

    print(f"source_role_scope: passed={passed} failed={failed}")
    for line in details:
        print(line)
    # 汇总形状必须与 `evals/run_evals.py` 的契约一致（`passed` / `failed` / `skipped` /
    # `details`）：本模块没有 skip 语义，但**键必须在**——少一个键会让套件在汇总行抛
    # `KeyError`，从而静默跳过它之后的全部模块（本轮真实发生过一次）。
    return {"passed": passed, "failed": failed, "skipped": 0, "details": tuple(details)}


if __name__ == "__main__":
    result = main()
    sys.exit(0 if result["failed"] == 0 else 1)

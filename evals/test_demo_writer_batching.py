"""M930-3 定点返修（§二）：Writer **分批请求面**的聚焦反例集（`wbatch-1`，`pw-10` 起）。

规则版本是 `wbatch-1`（当前值）；`pw-10` 只是**这条规则引入时**的写作策略版本——策略版本此后
又前进过（`pw-11`、`pw-12`），因此这里不把它写成「当前策略」。

对应缺陷：一节的要求超过单次输出容量时，整节一次请求的 JSON 会被 provider 截断
（`m930_3_acceptance_20260924T005116Z` 的首次 Writer 调用）。本轮的修法**不是**调大
`max_tokens`、也**不是**截掉 `follow_up_needs`/候选/材料，而是：沿**现有**正式 Writer 链，
按确定性的 Contract aspect 范围分批请求，每批仍可访问**完整、精确**的材料集合；全部批次
成功后合并成**一份**完整有序提案集，才送 Binding Gate。

本文件钉住的是这条链上**可证伪**的那几件事（不跑真实文档、不调真实 LLM、不联网、不写库）：

 A. **划分**：`plan_aspect_batches` 的结果是一份**划分**（两两不交、并集 = 本节 aspect 全集、
    顺序 = 冻结投影顺序），批数逐值等于 `aspect_batch_count`（含零 aspect：两者都必须说
    「恰好一批」）；`WriterPlanBatch` / `BatchCallRecord` 的不变量各自有反例。
 B. **零 aspect 退化**：财务/附注权威不携带 aspect 状态。此时仍然是**恰好一批**（请求形状与
    其它节一致），但消息体**不发** `batch` 块——空的 `aspect_ids` 会被读成「本批什么都不用
    回答」。这不是第二套运行时：入账、合并、拒绝审计全部同一条链。
 C. **批次只切请求面**：`batch` 只收窄 `projection.aspects`；`authority_facts` / `materials` /
    `must_use_facts` / `requestable_aspects` / `gaps` 在每一批里都是**完整**的那几份。
 C2. **义务按批结算**：「每批只回答本批栏目」与「每批重复承担全节必用事实」的矛盾只在
    `batch.must_use_fact_refs` 这一个**声明的子集**上解开——目录不裁，义务按
    `batch.topic_ids` 确定性派生（正例：逐批等于按 topic 过滤的结果；负例：批次 topic 无必须
    事实 ⇒ 义务面为空而目录不变），并集必须覆盖目录里每一项（不得有无人认领的必须事实）。
    少了负例，「子集恒等于整表」的实现在本用例的单 topic 形状上也能全绿。
 D. **截断后不采纳半成品**：provider 报截断时该批结果被拒（半截 JSON 永不解析），随后
    **确定性对半**缩小重问（有界）；缩至单 aspect 仍截断、或缩小额度用尽，一律 typed
    fail-closed。半截文本在任何一条路径上都不得变成候选。
 E. **合并身份**：候选按（`claim_text`, 事实类型）判同一性、支撑边取并集、标签重排为规范序；
    草稿单元与补件诉求同理（补件只丢逐字段完全相同的重复项）。
 F. **批次缺失/重复/多余**：全部批次"成功"也不得当作完整覆盖——三者各自 fail-closed。
 G. **失败回滚**：一轮 = 一次分批扫描；整轮失败不留下部分 Draft/Claim/Pack，且逐轮调用数
    之和必须等于实际调用数（「调用与重试逐次入账」在分批链上的落点）。
 G3. **高风险面的两条出口，与它们的优先级**（§一.2 逐候选裁出 `cco-1` / §二 3 定向重提案
    `hrrp-2`；`hrrp-1` 是后者的历史版本）：路径 B 候选携带高风险表面时，**先**试**逐候选
    裁出**——确定性、**零调用**、不改一个字的候选文本，≥1 条候选存活就把不合格的逐条拒掉、
    其余带**新身份**继续走链（原束身份与逐候选原因原样留档，下一修订产出后回填 `carve_out`）。
    裁出**不适用**时（一条都没被点名 / 一条都没幸存 / 本轮自己就是裁出轮 / 已有待回答的定向轮）
    才落到有界**定向重提案**：把「哪几条候选、逐字携带了什么表面、两条合法出路」写成定向说明
    追加在完整原请求之后，由下一轮**重出完整提案集**；下一轮每条原候选的跨修订去向（逐字
    `claim_text` 连接）逐条写回那一条拒绝记录。两条出口**互斥**，且一束一轮**恰好一条**拒绝
    记录——同一次拒绝留下两条记录会让拒绝束数与逐候选审计条目成倍虚增。反例：一条都没幸存 ⇒
    定向那条出口仍然活着（trace 证明「模型没改」，额度用尽即整节 fail-closed）；重试额度为 0
    但**有**幸存者 ⇒ 裁出照样救回本节且**不新增调用**；额度 0 且一条都没幸存 ⇒ 如实写「未排定」。

复用 `test_demo_pack_writer` 的替身夹具：两套替身各自漂移会让「测试里过的链」与「真实链」
不是同一条。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import client as LLM
from sections import narrative_schema as NS
from sections import pack_writer as PW
from evals import test_demo_pack_writer as FIXT

#: 本文件取用 company 投影里 aspect 最多的那个栏目（19 个 aspect > `MAX_ASPECTS_PER_BATCH`），
#: 因此它是**天然**的多批用例，而不是靠参数凑出来的批数。
_COMPANY_SUBSECTION = "co-h8"


class _ScriptedLlm:
    """按脚本逐次返回的替身：字符串 = 合格输出；`_truncated(...)` = provider 报截断。

    与 `FIXT._StubLlm` 只差**截断**这一种读数：真实链上截断不是「短一点的输出」，而是
    `llm.LLMTruncatedResponse`（携带 provider 侧 `call_id`/usage 的**失败调用**）。用同一
    种异常，测试里过的路径才与真实跑的路径是同一条。
    """

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.calls: list[dict] = []
        #: 逐次请求的**输入面读数**：(batch 标签, 本批 aspect 数, projection.aspects 数,
        #: authority_facts 数, materials 数)。分批的语义全在这里：只有第 3 项应当变化。
        self.seen: list[tuple] = []

    def narrate(self, *, messages, system, prompt_version, model_policy) -> PW.NarrationResult:
        # 重试轮的请求是「完整原请求 + 逐字追加的拒绝对说明」，所以只取**第一个** JSON 对象：
        # 追加说明本身不是请求面的一部分（不裁剪输入面、不换 prompt 才是这条链的纪律）。
        payload, _end = json.JSONDecoder().raw_decode(messages[0]["content"])
        batch = payload.get("batch", {})
        self.seen.append((batch.get("label"), len(batch.get("aspect_ids") or []),
                          len(payload["projection"]["aspects"]),
                          len(payload["authority_facts"]), len(payload["materials"])))
        self.calls.append({"messages": messages, "system": system,
                           "prompt_version": prompt_version, "model_policy": model_policy})
        if not self.script:
            raise AssertionError("替身被超额调用（应当 fail-closed，而不是继续重问）")
        item = self.script.pop(0)
        index = len(self.calls)
        if isinstance(item, str):
            return PW.NarrationResult(
                text=item, call_id=f"call-{index}", model=PW.MODEL_POLICY_STUB,
                prompt_version=prompt_version, status="ok", input_tokens=11,
                output_tokens=22, latency_ms=1, finish_reason="stop")
        response = LLM.LLMResponse(
            text=item["text"], input_tokens=33, output_tokens=item.get("output_tokens", 8192),
            latency_ms=7, model="provider-default", call_id=f"trunc-{index}",
            finish_reason=item.get("finish_reason", "length"))
        raise LLM.LLMTruncatedResponse(response)


def _truncated(text: str, *, finish_reason: str = "length", output_tokens: int = 8192) -> dict:
    return {"text": text, "finish_reason": finish_reason, "output_tokens": output_tokens}


#: 半截 JSON：它**不得**成为任何候选、任何单元、任何补件——只在证据侧留痕。
_HALF_JSON = ('{"claim_candidates": [{"candidate_key": "c1", '
              '"claim_text": "截断残留的候选文本"')
#: 半截文本里的可辨认成分：用来证明它没有以任何形式混进产物。
_HALF_MARK = "截断残留的候选文本"


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

    def expect_error(fn, exc, msg: str, *, needle: str = "") -> None:
        nonlocal passed, failed
        try:
            fn()
        except exc as e:
            if needle and needle not in str(e):
                failed += 1
                details.append(f"FAIL {msg}：原因不符（{str(e)[:160]}）")
            else:
                passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            details.append(f"FAIL {msg}：抛出 {type(e).__name__}（{str(e)[:160]}）")
        else:
            failed += 1
            details.append(f"FAIL {msg}：未拒绝")

    spec = FIXT.WS.load_writing_spec(FIXT.SPEC_PATH)
    profile = FIXT.PP.load_presentation_profile(FIXT.PROFILE_PATH)
    projection = PW.ContractProjection.create(
        spec, section_id="company", contract_version=FIXT.CONTRACT_VERSION,
        contract_fingerprint=FIXT.CONTRACT_FINGERPRINT)
    sub_aspects = tuple(a.aspect_id for a in projection.aspects
                        if a.subsection_id == _COMPANY_SUBSECTION)
    check(len(sub_aspects) > PW.MAX_ASPECTS_PER_BATCH,
          f"前置条件：{_COMPANY_SUBSECTION} 的 aspect 数必须超过单批上限"
          f"（实测 {len(sub_aspects)} ≤ {PW.MAX_ASPECTS_PER_BATCH}）")

    _cache: dict = {}

    def company_case(aspect_count: int, task_id: str, *, blocking: tuple[str, ...] = ()) -> tuple:
        """一个 company 节的权威输入：`aspect_count` 个**同一栏目**的 aspect + 一条覆盖它们的
        权威事实。同栏目 ⇒ 该栏目被这条事实覆盖 ⇒ 不触发栏目定向的额外轮次，用例只测分批。

        `blocking`（C2）：`must_use_facts` 只收 **required** 事实，而 required 的判据是「支撑它的
        aspect 里有 `blocking_policy` 非空的那些」（`NS.required_fact_ids` 的 topic_pack 分支）。
        不给 `blocking` 时本用例的 `must_use_facts` **恒为空**——C2 的三条断言会全部恒真。因此
        C2 单独用一份 `blocking` 非空的同形输入，并把这件事在下面显式断言为前置条件。
        """
        key = (aspect_count, task_id, blocking)
        if key in _cache:
            return _cache[key]
        ids = sub_aspects[:aspect_count]
        task = FIXT._task("company", (FIXT.TOPIC_BUSINESS,), task_id=task_id)
        facts = (FIXT._fact("f-1", "公司涉及若干诉讼与仲裁事项，均已如实披露。",
                            tuple(ids)),)
        pack_set = FIXT._pack_set(
            task, facts=facts,
            aspect_results=tuple(FIXT._AspectResult(a, "covered") for a in ids),
            requirements=(FIXT._Req(
                FIXT.TOPIC_BUSINESS,
                tuple(FIXT._aspect(a, FIXT.TOPIC_BUSINESS, "q-company_business",
                                   blocking=blocking)
                      for a in ids)),))
        authority = PW.TopicPackAuthorityInput.create(
            task, pack_set, company_id=FIXT.COMPANY_ID, report_as_of=FIXT.REPORT_AS_OF,
            contract_version=FIXT.CONTRACT_VERSION,
            contract_fingerprint=FIXT.CONTRACT_FINGERPRINT)
        scan = PW.scan_topic_pack(authority, task)
        material_context = FIXT._writer_material_context(
            pack_set, task_id=str(task.task_id), section_id="company")
        case = {"task": task, "pack_set": pack_set, "authority": authority, "scan": scan,
                "ids": ids, "material_context": material_context,
                "entry": {e.fact_id: e for e in scan.facts}["f-1"]}
        materials = FIXT._material_ids_of(scan)
        container = next(iter(materials))
        case["container_id"] = container
        case["material_id"] = next(iter(materials[container]))
        case["requirement_id"] = scan.requirement_ids[FIXT.TOPIC_BUSINESS]
        _cache[key] = case
        return case

    def run(case: dict, llm, *, policy=None) -> PW.PackWriteOutcome:
        return PW.write_section(
            case["task"], case["authority"], projection=projection, writing_spec=spec,
            presentation_profile=profile, llm_client=llm, policy=policy,
            dependency_fingerprint=FIXT.DEPENDENCY_FINGERPRINT,
            material_context=case["material_context"])

    def plan_of(case: dict, *, follow_ups=(), candidates=(), units=()) -> str:
        """该案的合格输出（候选绑定那条权威事实；文本逐字取自权威事实）。"""
        if not candidates:
            candidates = (FIXT._cand("c1", case["entry"].text,
                                     FIXT._fact_edge(case["scan"], "f-1")),)
        return json.dumps(FIXT._plan(candidates=candidates, units=units,
                                     follow_ups=follow_ups), ensure_ascii=False)

    def follow_up_of(case: dict, statement: str, aspect_index: int = 0) -> dict:
        return FIXT._follow_up(statement, target_requirement_id=case["requirement_id"],
                               topic_id=FIXT.TOPIC_BUSINESS, question_id="q-company_business",
                               aspect_id=case["ids"][aspect_index])

    case19 = company_case(len(sub_aspects), "task-batch-19")

    # ==================================================================
    # A. 划分：两两不交、并集 = 全集、顺序 = 冻结投影顺序
    # ==================================================================
    for count in (0, 1, PW.MAX_ASPECTS_PER_BATCH, PW.MAX_ASPECTS_PER_BATCH + 1):
        ids = sub_aspects[:count]
        batches = PW.plan_aspect_batches(projection, ids, case19["scan"])
        flat = [a for b in batches for a in b.aspect_ids]
        check(flat == list(ids),
              f"批次的 aspect 顺序必须等于冻结投影顺序且逐个覆盖"
              f"（n={count}，实测 {len(flat)} 项）")
        check(len(set(flat)) == len(flat),
              f"划分必须两两不交（n={count}）：同一 aspect 不得落在两批里")
        check(len(batches) == PW.aspect_batch_count(count),
              f"批数必须逐值等于 `aspect_batch_count`（n={count}："
              f"{len(batches)} != {PW.aspect_batch_count(count)}）——预算上界与本函数必须同源")
        check(all(b.total == len(batches) and b.index == i + 1
                  for i, b in enumerate(batches)),
              f"批次的 index/total 必须自洽（n={count}）")
        check(all(len(b.aspect_ids) <= PW.MAX_ASPECTS_PER_BATCH for b in batches),
              f"任一批都不得超过单批上限（n={count}）")
    batch19 = PW.plan_aspect_batches(projection, company_case(len(sub_aspects),
                                                             "task-batch-19")["ids"],
                                     case19["scan"])
    check([len(b.aspect_ids) for b in batch19] == [PW.MAX_ASPECTS_PER_BATCH,
                                                   len(sub_aspects)
                                                   - PW.MAX_ASPECTS_PER_BATCH],
          f"{len(sub_aspects)} 个 aspect 必须切成本策略下的连续等长块"
          f"（实测 {[len(b.aspect_ids) for b in batch19]}）")
    check(len({b.batch_id for b in batch19}) == len(batch19),
          "批身份必须互不相同（同一批被复用会让「谁回答了什么」无从复核）")

    # A2. 零 aspect：仍然是**恰好一批**（请求形状不变成特例），但批是空批。
    zero_batches = PW.plan_aspect_batches(projection, (), case19["scan"])
    check(len(zero_batches) == 1 and zero_batches[0].aspect_ids == ()
          and zero_batches[0].label() == "1/1",
          "零 aspect 必须返回恰好一批（label=1/1，aspect 列表为空）："
          "批次序列非空，下游的「一轮 = 一次分批扫描」不因本节没有 aspect 而变成特例")

    # A3. `WriterPlanBatch` 的不变量：划分出来的批不得是空批。
    expect_error(lambda: PW.WriterPlanBatch(batch_id="x", index=1, total=2, aspect_ids=()),
                 PW.PackWriterError, "多批序列里的空 aspect 批必须被拒",
                 needle="不得是空批")
    expect_error(lambda: PW.WriterPlanBatch(batch_id="x", index=2, total=1,
                                            aspect_ids=("a",)),
                 PW.PackWriterError, "index > total 必须被拒", needle="序号非法")
    expect_error(lambda: PW.WriterPlanBatch(batch_id="x", index=0, total=1, aspect_ids=("a",)),
                 PW.PackWriterError, "index=0 必须被拒", needle="序号非法")
    expect_error(lambda: PW.WriterPlanBatch(batch_id="", index=1, total=1, aspect_ids=("a",)),
                 PW.PackWriterError, "空 batch_id 必须被拒", needle="batch_id 不得为空")
    expect_error(lambda: PW.WriterPlanBatch(batch_id="x", index=1, total=1,
                                            aspect_ids=("a", "a")),
                 PW.PackWriterError, "批内 aspect 重复必须被拒", needle="有重复")
    expect_error(lambda: PW.WriterPlanBatch(batch_id="x", index=1, total=1, aspect_ids=("a",),
                                            shrink_depth=1, parent_batch_id=None),
                 PW.PackWriterError, "缩小的批次必须有父批", needle="必须一致")
    expect_error(lambda: PW.WriterPlanBatch(batch_id="x", index=1, total=1, aspect_ids=("a",),
                                            shrink_depth=0, parent_batch_id="p"),
                 PW.PackWriterError, "直接划分的批次不得带父批", needle="必须一致")

    # A4. 缩小：**对半**（切分点由 aspect 顺序唯一决定），单 aspect 不再可拆。
    left, right = PW.split_batch_for_shrink(batch19[0], case19["scan"], projection)
    check(list(left.aspect_ids) + list(right.aspect_ids) == list(batch19[0].aspect_ids),
          "缩小必须是对半拆分且并集等于父批（不得丢 aspect、不得重排）")
    check(left.index == right.index == batch19[0].index
          and left.total == right.total == batch19[0].total,
          "子批沿用父批的 index/total（它们回答的仍是同一个「第几批」的位置）")
    check(left.shrink_depth == right.shrink_depth == 1
          and left.parent_batch_id == right.parent_batch_id == batch19[0].batch_id,
          "子批必须标出缩小层级与父批身份（缩小的方向因此可回查）")
    check(len(left.aspect_ids) == PW.MAX_ASPECTS_PER_BATCH // 2
          and len(right.aspect_ids) == PW.MAX_ASPECTS_PER_BATCH
          - PW.MAX_ASPECTS_PER_BATCH // 2,
          f"对半拆分：{PW.MAX_ASPECTS_PER_BATCH} 个 aspect 必须切成 "
          f"{PW.MAX_ASPECTS_PER_BATCH // 2} + "
          f"{PW.MAX_ASPECTS_PER_BATCH - PW.MAX_ASPECTS_PER_BATCH // 2}")
    check(left.label() == f"{batch19[0].index}/{batch19[0].total}·缩小1",
          f"缩小的批次标签必须标出层级（实测 {left.label()!r}）")
    single = PW.WriterPlanBatch(batch_id="s", index=1, total=1, aspect_ids=("a",))
    expect_error(lambda: PW.split_batch_for_shrink(single, case19["scan"], projection),
                 PW.PackWriterError, "单 aspect 批次不得再缩小",
                 needle="不得再缩小")
    expect_error(lambda: PW.split_batch_for_shrink(zero_batches[0], case19["scan"],
                                                   projection),
                 PW.PackWriterError, "零 aspect 批次不得再缩小（没有更小的确定性切分）",
                 needle="不得再缩小")

    # A5. `BatchCallRecord`：空 aspect 是**合法读数**，但只可能是那一批。
    check(PW.BatchCallRecord(batch_id="z", label="1/1", aspect_ids=(), status="ok")
          .to_dict()["aspect_ids"] == [],
          "零 aspect 的那一批的调用记录可以（且只能）记空 aspect 列表")
    expect_error(lambda: PW.BatchCallRecord(batch_id="z", label="2/3", aspect_ids=(),
                                            status="ok"),
                 PW.PackWriterError, "空 aspect 却带着多批标签的调用记录必须被拒",
                 needle="只允许")
    for bad in ("partial", "", "OK"):
        expect_error(lambda b=bad: PW.BatchCallRecord(batch_id="z", label="1/1",
                                                      aspect_ids=("a",), status=b),
                     PW.PackWriterError, f"status={bad!r} 不在词表内必须被拒",
                     needle="不在词表内")
    record_fields = set(PW.BatchCallRecord.__dataclass_fields__)
    check({"batch_id", "label", "aspect_ids", "status", "call_id", "response_hash",
           "output_tokens", "finish_reason", "shrink_depth", "focus"} <= record_fields,
          f"一次分批请求的读数必须逐次留得下（call_id 指向 logs/llm：{sorted(record_fields)}）")

    # ==================================================================
    # B. 批次只切**请求面**：只收窄 projection.aspects
    # ==================================================================
    manifest = PW._derive_material_manifest(case19["authority"],
                                            material_context=case19["material_context"])

    def payload_of(batch) -> dict:
        messages, _system = PW.build_narration_messages(
            task=case19["task"], authority=case19["authority"], scan=case19["scan"],
            projection=projection, projection_aspects=batch.aspect_ids, unresolved=(),
            presentation_profile=profile, manifest=manifest,
            material_context=case19["material_context"], batch=batch)
        return json.loads(messages[0]["content"])

    full_payload = payload_of(PW.WriterPlanBatch(batch_id="whole", index=1, total=1,
                                                aspect_ids=batch19[0].aspect_ids))
    whole_keys = ("authority_facts", "materials", "must_use_facts", "gaps",
                  "requestable_aspects", "requirement_ids")
    for batch in batch19:
        payload = payload_of(batch)
        check("batch" in payload,
              f"分批请求必须带 `batch` 块（批 {batch.label()}）：范围声明不得隐含在别处")
        check(payload["batch"]["aspect_ids"] == list(batch.aspect_ids),
              f"`batch.aspect_ids` 必须逐字等于本批的 aspect（批 {batch.label()}）")
        check([row["aspect_id"] for row in payload["projection"]["aspects"]]
              == list(batch.aspect_ids),
              f"`projection.aspects` 必须收窄成**本批**要回答的那些（批 {batch.label()}）")
        check(payload["batch"]["policy_version"] == PW.ASPECT_BATCH_POLICY_VERSION
              and payload["batch"]["shrink_depth"] == batch.shrink_depth,
              f"批次身份必须随请求一起给出（批 {batch.label()}）")
        for key in whole_keys:
            check(payload[key] == full_payload[key],
                  f"`{key}` 不得按批裁剪（批 {batch.label()}）：分批是请求面的切分，"
                  "不是材料面的裁剪")
        for token in ("完整", "多写", "少写"):
            check(token in payload["batch"]["scope_rule"],
                  f"本批的范围声明必须写明「其它面仍是完整的、多写少写都会出错」：缺 {token!r}")
        check(any("本请求是**分批**的" in rule for rule in payload["rules"]),
              f"分批请求的 rules 必须显式声明分批语义（批 {batch.label()}）")

    # B1. 「每批只回答本批栏目」与「每批重复承担全节必用事实」的矛盾在一个**声明的子集**上解开：
    #     `must_use_facts` 仍是完整目录（上面已钉），本批的义务面由 `batch.must_use_fact_refs`
    #     按 `batch.topic_ids` 结算。两件事必须分开钉：①目录不裁（否则本批引用不到别的栏目的
    #     事实）②义务按批结算（否则同一件事在每一批都被重写一遍，占掉本批的输出容量——那正是
    #     r5 被截断的形状）。子集必须由**声明出来的 topic** 确定性派生，不能让模型自己按 topic 推。
    #
    #     这里换用一份 `blocking` 非空的同形输入：`must_use_facts` 只收 required 事实，而 required
    #     的判据是「支撑它的 aspect 可 block」。不换夹具时整张表恒为空，下面每一条都恒真。
    must_use_case = company_case(len(sub_aspects), "task-batch-mustuse",
                                 blocking=("blocking-1",))
    must_use_batches = PW.plan_aspect_batches(projection, must_use_case["ids"],
                                              must_use_case["scan"])

    def must_use_payload(batch) -> dict:
        messages, _system = PW.build_narration_messages(
            task=must_use_case["task"], authority=must_use_case["authority"],
            scan=must_use_case["scan"], projection=projection,
            projection_aspects=batch.aspect_ids, unresolved=(),
            presentation_profile=profile,
            manifest=PW._derive_material_manifest(
                must_use_case["authority"],
                material_context=must_use_case["material_context"]),
            material_context=must_use_case["material_context"], batch=batch)
        return json.loads(messages[0]["content"])

    must_use_ref = must_use_payload(must_use_batches[0])
    whole_must_use = [row["ref"] for row in must_use_ref["must_use_facts"]]
    check(bool(whole_must_use),
          "前置条件：C2 夹具的 must_use_facts 必须非空（aspect 带 blocking_policy 才产生 required"
          "事实；空集合会让下面每一条恒真，证明不了任何事）")
    declared_refs: set[str] = set()
    for batch in must_use_batches:
        payload = must_use_payload(batch)
        by_topic = [row["ref"] for row in must_use_ref["must_use_facts"]
                    if str(row["topic_id"]) in set(batch.topic_ids)]
        check([row["ref"] for row in payload["must_use_facts"]] == whole_must_use,
              f"`must_use_facts` 的**行序**不得按批重排（批 {batch.label()}）："
              "同一份目录在不同批里必须是同一份读数")
        check(payload["batch"]["must_use_fact_refs"] == by_topic,
              f"`batch.must_use_fact_refs` 必须逐字等于「本批 topic 的那些必须事实」"
              f"（批 {batch.label()}：实得 {payload['batch']['must_use_fact_refs']}，"
              f"按 topic 过滤应为 {by_topic}）")
        check(set(payload["batch"]["must_use_fact_refs"]) <= set(whole_must_use),
              f"`batch.must_use_fact_refs` 必须是完整目录的**子集**（批 {batch.label()}）："
              "声明出来的义务面不得出现目录里没有的 ref")
        declared_refs |= set(payload["batch"]["must_use_fact_refs"])
    check(declared_refs == set(whole_must_use),
          f"必须事实不得有**无人认领**的项：逐批声明的义务并集必须等于完整目录"
          f"（未认领 {sorted(set(whole_must_use) - declared_refs)}）")
    # 负对照：把批次的 topic 换成一个**没有必须事实**的 topic，义务面必须清空——而目录不变。
    # 少了这一条，「子集恒等于整表」的实现也能全绿（本用例恰好单 topic，正例无法区分两者）。
    absent_topic = PW.WriterPlanBatch(batch_id="b-absent", index=1, total=1,
                                      aspect_ids=must_use_batches[0].aspect_ids,
                                      topic_ids=("topic-absent-0001",))
    absent_payload = must_use_payload(absent_topic)
    check(absent_payload["batch"]["must_use_fact_refs"] == []
          and absent_payload["must_use_facts"] == must_use_ref["must_use_facts"],
          "批次 topic 不含任何必须事实时：义务面必须为空，而**目录仍完整**"
          f"（实得 {absent_payload['batch']['must_use_fact_refs']} / "
          f"{len(absent_payload['must_use_facts'])} 行）")

    # B2. 零 aspect 的那一批**不发** `batch` 块（空 aspect_ids 会被读成「什么都不用回答」）。
    zero_payload = payload_of(zero_batches[0])
    check("batch" not in zero_payload,
          "零 aspect 的那一批不得发 `batch` 块（空的 aspect_ids 会被读成「本批什么都不用"
          "回答」，而那正好是当时最不能给模型的暗示）")
    check(zero_payload["projection"]["aspects"] == [],
          "零 aspect 的那一批 `projection.aspects` 为空")
    check(not any("本请求是**分批**的" in rule for rule in zero_payload["rules"]),
          "零 aspect 的那一批不得追加分批规则（请求形状回到「整节一次」）")
    for key in whole_keys:
        check(key in zero_payload,
              f"零 aspect 的那一批仍必须是**完整**请求面：缺 `{key}`")

    # ==================================================================
    # C. 端到端：19 aspect → 两批 → 合并成一份完整提案集
    # ==================================================================
    common_candidate = FIXT._cand("c1", case19["entry"].text,
                                 FIXT._fact_edge(case19["scan"], "f-1"))
    common_unit = FIXT._unit("u1", "本节说明公司涉及的诉讼与仲裁情况。",
                             context=[FIXT._context_edge(case19["container_id"],
                                                         case19["material_id"])])
    shared_follow_up = follow_up_of(case19, "需要补充诉讼材料的原始记录。")
    shared = json.dumps(FIXT._plan(candidates=[common_candidate], units=[common_unit],
                                   follow_ups=[shared_follow_up]), ensure_ascii=False)
    llm = _ScriptedLlm(shared, shared)
    outcome = run(case19, llm)
    check(llm.seen == [("1/2", PW.MAX_ASPECTS_PER_BATCH, PW.MAX_ASPECTS_PER_BATCH, 1, 1),
                       ("2/2", len(sub_aspects) - PW.MAX_ASPECTS_PER_BATCH,
                        len(sub_aspects) - PW.MAX_ASPECTS_PER_BATCH, 1, 1)],
          f"两批各自的请求面读数必须只在本批 aspect 数上不同"
          f"（实测 {llm.seen}）：authority_facts / materials 每批都是完整的")
    check(outcome.llm_calls == 2,
          f"19 个 aspect（> {PW.MAX_ASPECTS_PER_BATCH}）必须拆成两次调用（实测 "
          f"{outcome.llm_calls}）")
    check(outcome.draft.writer_attempt == 1,
          "两批属于**同一轮**：不得因为分批而把轮次计成两次")
    check(len(outcome.draft.claim_candidates) == 1,
          "同一断言在两批里各出现一次，必须合并成**一条**候选")
    check(len(outcome.draft.narrative_draft_units) == 1,
          "同一草稿单元在两批里各出现一次，必须合并成**一条**")
    check(len(outcome.follow_up_needs) == 1,
          "逐字段完全相同的补件诉求只留一条")
    check([c.claim_text for c in outcome.draft.claim_candidates] == [case19["entry"].text],
          "合并后的候选文本必须逐字来自权威事实（不得被改写、不得被截断）")
    check([str(u.text) for u in outcome.draft.narrative_draft_units]
          == ["本节说明公司涉及的诉讼与仲裁情况。"],
          "合并后的草稿单元文本必须逐字保留")
    check([f.statement for f in outcome.follow_up_needs]
          == ["需要补充诉讼材料的原始记录。"],
          "合并后的补件诉求原文必须保留")
    # 跨批的局部标签必然互相冲突（每批都从 c1 数起），所以**节级身份不得是批内标签**：
    # 它是内容派生的（`content_id("ccand_", …)`，并在 `__post_init__` 里逐字段回核）。
    check(outcome.draft.claim_candidates[0].candidate_id.startswith("ccand_"),
          f"合并后的节级候选身份必须是内容派生的，而不是某一批的局部标签（实测 "
          f"{outcome.draft.claim_candidates[0].candidate_id!r}）")
    check(outcome.batch_audit["rounds"][0]["candidate_sources"][0][0]["candidate_key"] == "c1",
          "合并**内部**的规范序标签才是 c1..（它只在本轮审计里用，不是节级身份）")

    rounds = outcome.batch_audit["rounds"]
    check(len(rounds) == 1, "一轮 = 一次分批扫描：本轮恰好一条分批审计")
    round0 = rounds[0]
    check(round0["merged"] is True and round0["calls"] == 2
          and round0["shrink_steps_used"] == 0,
          f"本轮审计必须记下「全部批次成功、合并成一份、没有缩小」（实测 "
          f"{ {k: round0[k] for k in ('merged', 'calls', 'shrink_steps_used')} }）")
    check(round0["batch_total"] == 2
          and [b["label"] for b in round0["batches"]] == ["1/2", "2/2"],
          "合并审计必须留下本轮的批次划分（谁回答了什么可复核）")
    check(sorted(round0["aspects_requested"]) == sorted(case19["ids"])
          and len(set(round0["aspects_requested"])) == len(case19["ids"]),
          "合并审计的 `aspects_requested` 必须是本节 aspect 全集的一份**划分**")
    check(round0["candidate_merged_from_duplicates"] == 1
          and round0["candidate_support_edges_dropped_as_duplicate"] == 1,
          f"被合并掉的重复候选与重复边必须逐条入账（实测 "
          f"{round0['candidate_merged_from_duplicates']} / "
          f"{round0['candidate_support_edges_dropped_as_duplicate']}）")
    check(round0["unit_merged_from_duplicates"] == 1
          and round0["follow_up_dropped_as_duplicate"] == 1,
          "被合并掉的重复草稿单元与重复补件诉求必须逐条入账")
    check([(s["batch_label"], s["candidate_key"]) for s in round0["candidate_sources"][0]]
          == [("1/2", "c1"), ("2/2", "c1")],
          f"合并后的每一条候选都必须能追回它是哪几批的哪一条（实测 "
          f"{round0['candidate_sources'][0]}）")
    check(round0["fact_type_unresolved_labels"] == [],
          "身份闭得上的候选不得被记成哨兵分组")
    check(tuple(round0["batch_calls"][0]) >= ("batch_id", "label", "aspect_ids", "status"),
          "逐批调用读数必须随本轮审计一起留存")
    check(outcome.batch_audit["policy_version"] == PW.ASPECT_BATCH_POLICY_VERSION
          and outcome.batch_audit["max_aspects_per_batch"] == PW.MAX_ASPECTS_PER_BATCH
          and outcome.batch_audit["max_shrink_steps"] == PW.MAX_SWEEP_SHRINK_STEPS,
          "分批审计必须带上策略版本与两个上界（否则「缩了几次」无从判定）")
    check(set(outcome.sidecar()) >= {"writer_batch_audit"},
          "分批审计必须随门前产物一起交回组合根（它不是门后身份，也不是私账）")

    # C2. 同一断言在两批里用了**不同**局部标签 ⇒ 仍必须合并成一条，且节级身份不变。
    #     这一条是「合并按内容判同一性、不按批内标签」的可证伪形式：若身份取自标签，
    #     这里就会凭空多出一条候选（同一句话在一节里出现两次），并且候选总数随标签而变。
    llm = _ScriptedLlm(
        json.dumps(FIXT._plan(candidates=[FIXT._cand("c1", case19["entry"].text,
                                                      FIXT._fact_edge(case19["scan"], "f-1"))]),
                   ensure_ascii=False),
        json.dumps(FIXT._plan(candidates=[FIXT._cand("c7", case19["entry"].text,
                                                      FIXT._fact_edge(case19["scan"], "f-1"))]),
                   ensure_ascii=False))
    relabelled = run(case19, llm)
    check(len(relabelled.draft.claim_candidates) == 1,
          f"同一断言换了局部标签仍是**同一条**候选（实测 "
          f"{len(relabelled.draft.claim_candidates)} 条）：判同一性不得看批内标签")
    check(relabelled.draft.claim_candidates[0].candidate_id
          == outcome.draft.claim_candidates[0].candidate_id,
          "节级候选身份必须只由内容决定（换了标签、换了批序，身份不变）")
    check(relabelled.batch_audit["rounds"][0]["candidate_merged_from_duplicates"] == 1,
          "跨标签的这次合并必须照样入账：`1/2` 的 c1 与 `2/2` 的 c7 被认成同一条")

    # ==================================================================
    # D. 截断后不采纳半成品
    # ==================================================================
    # D1. 单 aspect 仍被截断 ⇒ typed fail-closed（不得静默放弃该 aspect）。
    case13 = company_case(PW.MAX_ASPECTS_PER_BATCH + 1, "task-batch-13")
    llm = _ScriptedLlm(plan_of(case13), _truncated(_HALF_JSON, finish_reason="max_tokens"))
    try:
        run(case13, llm)
    except PW.ProposalSetRejectedError as exc:
        record = exc.rejections[0]
        check(record.rejection_kind == "batch_truncated",
              f"缩至单 aspect 仍截断必须是 typed `batch_truncated`（实测 "
              f"{record.rejection_kind!r}）")
        check("缩至**单个 aspect** 仍被截断" in record.rejection_detail,
              "拒绝详情必须写明是「单 aspect 仍截断」这一条路径")
        check(record.candidate_ids == () and record.proposal_ids == (),
              "半截 JSON 从未成为结构化对象：不得为它编出候选身份")
        check([(b.label, b.status, b.call_id, b.finish_reason) for b in record.batches]
              == [("1/2", "ok", "call-1", "stop"),
                  ("2/2", "truncated", "trunc-2", "max_tokens")],
              f"截断照样逐次入账（含 provider 侧 call_id 与 finish_reason），实测 "
              f"{[(b.label, b.status) for b in record.batches]}")
        check(_HALF_MARK not in json.dumps(
            {"proposal_ids": list(record.proposal_ids),
             "candidate_ids": list(record.candidate_ids),
             "follow_ups": [f.statement for f in exc.follow_up_needs]},
            ensure_ascii=False),
            "半截文本不得出现在任何身份字段里")
    except Exception as exc:  # noqa: BLE001
        failed += 1
        details.append(f"FAIL 单 aspect 截断必须 typed fail-closed（抛出 "
                       f"{type(exc).__name__}: {str(exc)[:120]}）")
    else:
        failed += 1
        details.append("FAIL 缩至单 aspect 仍被截断时不得产出章节")

    # D2. 缩小额度用尽 ⇒ typed fail-closed（不得无限次重问同一批）。
    llm = _ScriptedLlm(_truncated(_HALF_JSON), _truncated(_HALF_JSON),
                       _truncated(_HALF_JSON))
    try:
        run(case19, llm)
    except PW.ProposalSetRejectedError as exc:
        record = exc.rejections[0]
        check(record.rejection_kind == "batch_truncated"
              and "缩小" in record.rejection_detail and "共用" in record.rejection_detail
              and "额度" in record.rejection_detail and "已用尽" in record.rejection_detail,
              f"缩小额度用尽必须是 typed fail-closed 并写明是额度这一条路径、以及它与"
              f"形状纠正**共用**同一份额度（实测 {record.rejection_detail[:80]!r}）")
        check(len(record.batches) == PW.MAX_SWEEP_SHRINK_STEPS + 1,
              f"每一次截断都要入账：本路径恰好 1 次原始 + "
              f"{PW.MAX_SWEEP_SHRINK_STEPS} 次缩小（实测 {len(record.batches)}）")
        check(all(b.status == "truncated" for b in record.batches),
              "三次读数都必须是截断状态")
        check([b.shrink_depth for b in record.batches] == [0, 1, 2],
              f"缩小层级必须逐次递增（实测 {[b.shrink_depth for b in record.batches]}）")
        check(len(llm.calls) == PW.MAX_SWEEP_SHRINK_STEPS + 1,
              f"调用次数必须有界：{1 + PW.MAX_SWEEP_SHRINK_STEPS} 次之后停止，"
              f"不得无限重问（实测 {len(llm.calls)}）")
    except Exception as exc:  # noqa: BLE001
        failed += 1
        details.append(f"FAIL 缩小额度用尽必须 typed fail-closed（抛出 "
                       f"{type(exc).__name__}: {str(exc)[:120]}）")
    else:
        failed += 1
        details.append("FAIL 缩小额度用尽时不得产出章节")

    # D3. 缩小后成功：半截文本不得以任何形式进入产物。
    llm = _ScriptedLlm(_truncated(_HALF_JSON), plan_of(case19), plan_of(case19),
                       plan_of(case19))
    outcome = run(case19, llm)
    check(llm.seen[0][:3] == ("1/2", PW.MAX_ASPECTS_PER_BATCH, PW.MAX_ASPECTS_PER_BATCH)
          and llm.seen[1][:3] == ("1/2·缩小1", PW.MAX_ASPECTS_PER_BATCH // 2,
                                  PW.MAX_ASPECTS_PER_BATCH // 2),
          f"截断后的重问必须是**对半**缩小（实测 {[s[:3] for s in llm.seen]}）")
    check(all(s[4] == 1 for s in llm.seen),
          "缩小后的子批仍然看得见**完整**的材料面（缩小的是请求面，不是材料）")
    body = json.dumps({"candidates": [c.claim_text for c in outcome.draft.claim_candidates],
                       "units": [u.text for u in outcome.draft.narrative_draft_units],
                       "follow_ups": [f.statement for f in outcome.follow_up_needs]},
                      ensure_ascii=False)
    check(_HALF_MARK not in body,
          "被截断的那一批的**半截文本不得以任何形式进入产物**（不解析、不采纳、不降级）")
    round0 = outcome.batch_audit["rounds"][0]
    check(round0["shrink_steps_used"] == 1 and round0["calls"] == 4
          and round0["merged"] is True,
          f"本轮必须记下「缩小 1 次、4 次调用、最终合并成功」（实测 "
          f"{ {k: round0[k] for k in ('shrink_steps_used', 'calls', 'merged')} }）")
    check([(c["label"], c["status"]) for c in round0["batch_calls"]]
          == [("1/2", "truncated"), ("1/2·缩小1", "ok"), ("1/2·缩小1", "ok"), ("2/2", "ok")],
          f"逐批调用读数必须有序且含中间状态（实测 "
          f"{[(c['label'], c['status']) for c in round0['batch_calls']]}）")
    check(sorted(round0["aspects_requested"]) == sorted(case19["ids"]),
          "缩小后的批次并集仍必须**恰好等于**本节 aspect 全集（缩小不得丢 aspect）")

    # D4. 零 aspect 的那一批被截断 ⇒ 另一条 typed 路径（没有更小的确定性切分）。
    fin = _financial_case()
    llm = _ScriptedLlm(_truncated(_HALF_JSON))
    try:
        PW.write_section(fin["task"], fin["authority"], projection=fin["projection"],
                         writing_spec=spec, presentation_profile=profile, llm_client=llm,
                         dependency_fingerprint=FIXT.DEPENDENCY_FINGERPRINT)
    except PW.ProposalSetRejectedError as exc:
        record = exc.rejections[0]
        check(record.rejection_kind == "batch_truncated"
              and "零 aspect 的批次" in record.rejection_detail,
              f"零 aspect 批次的截断必须走它自己那条 typed 详情（实测 "
              f"{record.rejection_detail[:90]!r}）")
        check(len(llm.calls) == 1,
              f"零 aspect 的批次不可再缩小：不得重问第二次（实测 {len(llm.calls)}）")
        check([(b.label, b.aspect_ids, b.status) for b in record.batches]
              == [("1/1", (), "truncated")],
              "零 aspect 批次的截断读数必须是空 aspect 列表 + label 1/1")
    except Exception as exc:  # noqa: BLE001
        failed += 1
        details.append(f"FAIL 零 aspect 批次截断必须 typed fail-closed（抛出 "
                       f"{type(exc).__name__}: {str(exc)[:120]}）")
    else:
        failed += 1
        details.append("FAIL 零 aspect 批次被截断时不得产出章节")

    # ==================================================================
    # E. 合并身份：哨兵不得换来过门（身份判定唯一权威在权威回查）
    # ==================================================================
    table = PW._authority_fact_table(case19["scan"].facts)
    forged_edge = FIXT._fact_edge(case19["scan"], "f-1")
    forged_edge["fact_id"] = "f-not-in-authority"
    forged = FIXT._plan(candidates=[FIXT._cand("c9", "一条身份闭不上的候选", forged_edge)])
    _merged, audit = PW._merge_batch_plans(
        batch_plans=[(batch19[0], forged), (batch19[1], forged)],
        expected_aspects=case19["ids"], table=table)
    check(audit["candidate_total"] == 1 and audit["candidate_merged_from_duplicates"] == 1,
          "身份闭不上的候选**照样参与合并**（否则「被拒的是一束什么」会从审计里蒸发）")
    check(audit["fact_type_unresolved_labels"] == ["c9", "c9"],
          f"哨兵分组的原标签必须逐条入账（实测 "
          f"{audit['fact_type_unresolved_labels']}）")
    check(PW._UNRESOLVED_FACT_TYPE not in {str(e.get("fact_id"))
                                           for c in _merged["claim_candidates"]
                                           for e in c["support"]},
          "哨兵不是事实类型、不得写进任何支撑边坐标")
    llm = _ScriptedLlm(json.dumps(forged, ensure_ascii=False),
                       json.dumps(forged, ensure_ascii=False))
    try:
        run(case19, llm)
    except PW.ProposalSetRejectedError as exc:
        check(exc.rejections[0].rejection_kind == "proposal_identity_unresolvable",
              f"整束身份闭不上必须是 typed 拒绝（实测 "
              f"{exc.rejections[0].rejection_kind!r}）")
        check(_HALF_MARK not in exc.rejections[0].rejection_detail,
              "拒绝对详情不得混入与本轮无关的文本")
    except Exception as exc:  # noqa: BLE001
        failed += 1
        details.append(f"FAIL 身份闭不上必须 typed fail-closed（抛出 "
                       f"{type(exc).__name__}: {str(exc)[:120]}）")
    else:
        failed += 1
        details.append("FAIL 候选身份在权威侧闭不上时不得产出章节")

    # ==================================================================
    # F. 批次缺失 / 重复 / 多余：全部批次"成功"也不得当作完整覆盖
    # ==================================================================
    plan_ok = json.loads(plan_of(case19))
    expect_error(
        lambda: PW._merge_batch_plans(batch_plans=[(batch19[0], plan_ok)],
                                      expected_aspects=case19["ids"], table=table),
        PW.PackWriterError, "缺一批（aspect 覆盖不全）必须被拒", needle="不一致")
    expect_error(
        lambda: PW._merge_batch_plans(batch_plans=[(batch19[0], plan_ok),
                                                   (batch19[0], plan_ok)],
                                      expected_aspects=case19["ids"], table=table),
        PW.PackWriterError, "同一 aspect 落在两批里必须被拒", needle="不是划分")
    expect_error(
        lambda: PW._merge_batch_plans(batch_plans=[(batch19[0], plan_ok),
                                                   (batch19[1], plan_ok)],
                                      expected_aspects=case19["ids"][:2], table=table),
        PW.PackWriterError, "多出一批（回答了本批之外的 aspect）必须被拒", needle="多")

    # ==================================================================
    # G. 补件完整保留：不同批次提出的诉求都留下，只有逐字段相同的重复被丢
    # ==================================================================
    first = follow_up_of(case19, "需要补充第一项材料。", 0)
    second = follow_up_of(case19, "需要补充第二项材料。", 1)
    plan_a = json.dumps(FIXT._plan(follow_ups=[first, second]), ensure_ascii=False)
    plan_b = json.dumps(FIXT._plan(follow_ups=[second]), ensure_ascii=False)
    merged, audit = PW._merge_batch_plans(
        batch_plans=[(batch19[0], json.loads(plan_a)),
                     (batch19[1], json.loads(plan_b))],
        expected_aspects=case19["ids"], table=table)
    check([f["statement"] for f in merged["follow_up_needs"]]
          == ["需要补充第一项材料。", "需要补充第二项材料。"],
          f"各批提出的不同诉求必须**全部保留**且按批序（实测 "
          f"{[f['statement'] for f in merged['follow_up_needs']]}）")
    check(audit["follow_up_total"] == 2 and audit["follow_up_dropped_as_duplicate"] == 1,
          f"只有逐字段完全相同的重复项被丢，且必须逐条入账（实测 total="
          f"{audit['follow_up_total']}, dropped={audit['follow_up_dropped_as_duplicate']}）")

    # G2. 合并成功但整束仍被拒（高风险 B 面）：诉求**先成形再抛**，不得随异常蒸发。
    high_risk_plan = json.dumps(FIXT._plan(
        candidates=[FIXT._cand("c1", "公司共涉及 3 起诉讼",
                               FIXT._material_edge(case19["container_id"],
                                                   case19["material_id"]))],
        follow_ups=[shared_follow_up]), ensure_ascii=False)
    llm = _ScriptedLlm(high_risk_plan, high_risk_plan)
    try:
        run(case19, llm)
    except PW.ProposalSetRejectedError as exc:
        check([r.rejection_kind for r in exc.rejections] == ["path_b_high_risk_surface"],
              f"合并之后被拒的仍必须是 typed 拒绝（实测 "
              f"{[r.rejection_kind for r in exc.rejections]}）")
        check(len(exc.follow_up_needs) == 1
              and exc.follow_up_needs[0].statement == "需要补充诉讼材料的原始记录。",
              f"被拒各轮提出的诉求必须先成形再抛（实测 {len(exc.follow_up_needs)} 条）")
        check(exc.follow_up_untypeable == (),
              "可类型化的诉求不得被记成「不成立」")
        check(exc.rejections[0].follow_up_count == 1,
              "拒绝审计里的诉求计数必须来自**那一束**自己的输出")
    except Exception as exc:  # noqa: BLE001
        failed += 1
        details.append(f"FAIL 合并后被拒必须 typed fail-closed（抛出 "
                       f"{type(exc).__name__}: {str(exc)[:120]}）")
    else:
        failed += 1
        details.append("FAIL 高风险 B 面整束必须被拒")

    # ==================================================================
    # G3. §一.2 逐候选裁出**优先**；§二 3 定向重提案是它的**回落**（`cco-1` / `hrrp-2`）
    # ==================================================================
    # 缺陷形态（C3）：路径 B 候选携带高风险表面时，整束被拒 ⇒ 整节零产出（「整束连坐」）。
    # 现行有**两条**出口，优先级不同（`pack_writer.py` 的 `if high_risk or ineligible:` 出口
    # 逐行写明）：
    #   * **先**试**逐候选裁出**（`cco-1`，§一.2 补充裁决）：确定性、**零调用**、不改一个字的
    #     候选文本。≥1 条候选存活即排定**零调用裁出轮**，幸存者带**新身份**继续走链；
    #   * 裁出**不适用**时（一条都没被点名 / 一条都没幸存 / 本轮自己就是裁出轮 / 已有待回答的
    #     定向轮）才落到既有 C3 **有界定向重提案**（`hrrp-2`）：原束审计原样留档，把「哪几条
    #     候选、逐字携带了什么表面、有哪两条合法出路」写成一段**定向说明**追加在完整原请求
    #     之后，由**下一轮**（新修订、自己完整的候选集与支撑边边集、重新过全部门）重出完整
    #     提案集；下一轮每条原候选的**跨修订去向**逐条写回那一条拒绝记录。整节 fail-closed
    #     仍是它额度用尽时的终态。
    # 因此本组必须**同时**证明两件事：裁出确实优先（且此时**不发**定向说明），以及定向那条
    # 出口**仍然活着**——在它真正该出场的形状里（本次模型的输出中没有任何候选幸存）。
    _pb_edge = FIXT._material_edge(case19["container_id"], case19["material_id"])
    # 被点名的那条：路径 B 边 + 含数字的文本。未被点名的那条走**路径 A**（文本逐字取自权威
    # 事实）——裁出不该牵连到没被点名的候选；而用路径 A 的权威事实文本还有一个用途：它覆盖
    # 本栏目全部 aspect，因此这一节不会额外触发栏目定向轮（下面的轮数断言才是干净的两轮形状）。
    clean_text = case19["entry"].text
    risky_text = "公司共涉及 3 起诉讼"
    check(NS.high_risk_surface_tokens(risky_text) != (),
          "G3 夹具本身必须真的携带高风险表面（否则「这两条出口救回了什么」无从证明）")
    clean_cand = FIXT._cand("c1", clean_text, FIXT._fact_edge(case19["scan"], "f-1"))
    risky_cand = FIXT._cand("c2", risky_text, _pb_edge)

    # D1. 正例（**优先级**）：束里只有**部分**候选踩线 ⇒ 走**逐候选裁出**，不是定向重提案。
    #     裁出轮**零调用**，所以脚本只该被取 2 次——多要一次就会因脚本用尽而抛。
    round1 = json.dumps(FIXT._plan(candidates=[clean_cand, risky_cand]),
                        ensure_ascii=False)
    llm = _ScriptedLlm(round1, round1)
    outcome = run(case19, llm, policy=PW.WriterPolicy(max_llm_retries=1))
    rounds = [(r["round"], r["focus"], bool(r["carve_out"]), r["directed_reproposal"],
               r["merged"], r["calls"]) for r in outcome.batch_audit["rounds"]]
    check(rounds == [(1, False, False, False, True, 2), (2, False, True, False, True, 0)],
          f"第 2 轮必须是**零调用的裁出轮**（`carve_out` 非空且 `calls: 0`），且**不是**定向轮"
          f"（实测 {rounds}）")
    check(outcome.llm_calls == 2 and len(llm.calls) == 2,
          f"逐候选裁出**不新增任何模型调用**：全程只有第 1 轮那两次分批调用"
          f"（实测 {outcome.llm_calls} / {len(llm.calls)}）")
    check(all(PW.HIGH_RISK_REPROPOSAL_NOTE_VERSION not in c["messages"][0]["content"]
              for c in llm.calls),
          "裁出优先时**不得**再发定向说明：两条出口互斥，同时出现会让同一次拒绝有两种解释")
    check(outcome.draft.writer_attempt == 2 and len(outcome.rejections) == 1,
          f"裁出之后是**新修订**，原束仍留一条整束拒绝记录（实测第 "
          f"{outcome.draft.writer_attempt} 轮、{len(outcome.rejections)} 条）")
    rec = outcome.rejections[0]
    check(rec.rejection_kind == "path_b_high_risk_surface" and rec.attempt == 1,
          "被拒的原束必须留下原样的 typed 记录（整束身份与逐候选原因一字不改）")
    check(rec.answered_by_attempt is None and rec.reproposal is None,
          "裁出回答的那条记录**不得**同时挂 `answered_by_attempt` / `reproposal`："
          "那条跨修订对账由 `carve_out` 承担（两条出口互斥）")
    carve = rec.carve_out
    check(carve is not None and carve.model_calls_added == 0
          and carve.decision.version == PW.CANDIDATE_CARVE_OUT_VERSION
          and carve.decision.from_attempt == 1 and carve.decision.to_attempt == 2,
          f"裁出裁决必须是当前版本 `{PW.CANDIDATE_CARVE_OUT_VERSION}` 且指向**下一**修订、"
          f"零新增调用（实测 {carve.decision.to_dict() if carve else None}）")
    by_text = {d.claim_text: d for d in (carve.destinations if carve else ())}
    check(len(by_text) == len(rec.candidate_ids),
          f"裁出去向表的键集必须**恰好**等于原束完整有序身份（实测 {len(by_text)} vs "
          f"{len(rec.candidate_ids)}）")
    # 去向表里的原因是逐候选、**各轴独立判定**的并集，不是「一束里只留一条最重的」。这条
    # 高风险候选同时还够不上 `srsc-2` 的独立支撑结论（它的文本不是本节材料正文的严格抽取式
    # 子串），因此带两个原因——这是如实报数，不是原因串味：`surfaces` 只有它自己那一个、
    # 新身份为空、干净那条仍然一个字都没多。
    check(by_text.get(risky_text) is not None
          and by_text[risky_text].reasons == ("path_b_high_risk_surface",
                                             "path_b_unproven_current_state")
          and bool(by_text[risky_text].surfaces)
          and by_text[risky_text].next_candidate_id == "",
          f"被点名那条的去向必须是「带 typed 原因与逐字表面、**无**新身份」（实测 "
          f"{by_text.get(risky_text)!r}）")
    check(by_text.get(clean_text) is not None
          and by_text[clean_text].reasons == ()
          and by_text[clean_text].surfaces == ()
          and bool(by_text[clean_text].next_candidate_id),
          f"未被点名那条必须带**新修订身份**继续走链（实测 {by_text.get(clean_text)!r}）")
    check(carve is not None
          and carve.next_candidate_ids == tuple(
              str(c.candidate_id) for c in outcome.draft.claim_candidates),
          "裁出 trace 的另一端必须是新修订的**完整有序**候选身份（与形成的草稿逐条对应）")
    check([c.claim_text for c in outcome.draft.claim_candidates] == [clean_text]
          and not outcome.gate_result.blocking,
          "裁出必须真的救回本节：被采信的草稿里只剩那个合法原子，且硬门不 blocking")

    # D2. 反例：**一条都没幸存** ⇒ 裁出**不适用**，退回定向重提案；定向轮照**原样**重出同一条
    #     高风险原子 ⇒ 额度用尽即整节 fail-closed，且 trace 必须如实记下「它逐字回来了」
    #     （失败原因可读回，不是一句「重试失败」）。
    #     束里**只有**那条高风险候选是必须的：只要还剩一条没被点名，裁出就会先接住（D1），
    #     定向那条出口根本不会出场——而「它仍然活着」正是本组要证的第二件事。
    only_risky = json.dumps(FIXT._plan(candidates=[risky_cand]), ensure_ascii=False)
    llm = _ScriptedLlm(only_risky, only_risky, only_risky, only_risky)
    try:
        run(case19, llm, policy=PW.WriterPolicy(max_llm_retries=1))
    except PW.ProposalSetRejectedError as exc:
        check([r.attempt for r in exc.rejections] == [1, 2]
              and all(r.rejection_kind == "path_b_high_risk_surface" for r in exc.rejections),
              f"两轮各留一条 typed 拒绝（实测 {[(r.attempt, r.rejection_kind) for r in exc.rejections]}）")
        check(exc.rejections[0].carve_out is None
              and exc.rejections[0].answered_by_attempt == 2,
              "一条都没幸存 ⇒ 裁出必须**不适用**（不得留下一条空裁决冒充满意），"
              "由定向重提案接住这次拒绝")
        check(exc.rejections[0].reproposal is not None
              and {d.destination for d in exc.rejections[0].reproposal.destinations}
              == {"carried_verbatim"},
              "第二轮逐字重出了同一条候选 ⇒ trace 必须记成 `carried_verbatim`（模型没改，不是没答）")
        check(exc.rejections[1].answered_by_attempt is None
              and exc.rejections[1].reproposal is None
              and exc.rejections[1].carve_out is None,
              "定向额度用尽之后不得再排定一次，也不得凭上一轮的比对补一条去向")
        check(len(exc.rejections) == 2 and len(llm.calls) == 4,
              f"定向重提案是**有界**的：至多一次，且不新增调用额度（实测 "
              f"{len(exc.rejections)} 束、{len(llm.calls)} 次调用）")
        # 「一束一轮**恰好**一条记录」：同一次拒绝留下两条只差一个字段的记录，会让报告里的
        # 拒绝束数与逐候选审计条目成倍虚增，也让「这条记录上的 `carve_out` / `reproposal`
        # 指向谁」变得不可知。判据取（轮次, 原因码）这一对该束唯一的身份。
        pairs = [(r.attempt, r.rejection_kind) for r in exc.rejections]
        check(len(set(pairs)) == len(pairs),
              f"同一束在同一轮不得留下两条拒绝记录（实测 {pairs}）")
        check(all(len(r.candidate_audit) == len(r.candidate_ids) for r in exc.rejections),
              "逐候选审计必须与原束**逐个**对应，不得因走了两条出口而翻倍（实测 "
              f"{[(len(r.candidate_audit), len(r.candidate_ids)) for r in exc.rejections]}）")
        # 两笔额度**各自**如实报出：这一情形下两笔都用尽（第 2 轮之后没有第 3 轮，定向额度
        # 也已用掉）。只报一笔会让读的人以为另一笔还有余量。
        check("重试额度已用尽" in str(exc) and "定向重提案额度" in str(exc)
              and "fail-closed" in str(exc),
              f"终止原因必须把两笔额度各自说清（实测 {str(exc)[-300:]!r}）")
    except Exception as exc:  # noqa: BLE001
        failed += 1
        details.append(f"FAIL 定向轮未改时必须整节 typed fail-closed（抛出 "
                       f"{type(exc).__name__}: {str(exc)[:120]}）")
    else:
        failed += 1
        details.append("FAIL 定向轮照原样重出高风险原子时不得产出章节")

    # D3. 正例（预算中性）：重试额度为 0，但**有幸存者** ⇒ 裁出照样救回本节。裁出轮吃的是
    #     「一轮」，不是「一次调用」，因此它不受 `max_llm_retries` 约束；若它被额度挡住或
    #     偷偷多要一次调用，这里就会落成零产出或多一次调用。
    llm = _ScriptedLlm(round1, round1)
    try:
        outcome = run(case19, llm, policy=PW.WriterPolicy(max_llm_retries=0))
    except Exception as exc:  # noqa: BLE001
        failed += 1
        details.append(f"FAIL 重试额度为 0 时裁出仍须救回本节（抛出 "
                       f"{type(exc).__name__}: {str(exc)[:120]}）")
    else:
        zero_rounds = [(r["round"], bool(r["carve_out"]), r["calls"])
                       for r in outcome.batch_audit["rounds"]]
        check(zero_rounds == [(1, False, 2), (2, True, 0)],
              f"额度 0 时的第 2 轮仍然是**零调用裁出轮**（实测 {zero_rounds}）")
        check(outcome.llm_calls == 2 and len(llm.calls) == 2
              and [c.claim_text for c in outcome.draft.claim_candidates] == [clean_text],
              f"裁出**不新增调用**且救回本节：全程 2 次调用、草稿只剩合法原子"
              f"（实测 {outcome.llm_calls} / {len(llm.calls)} 次、"
              f"{[c.claim_text for c in outcome.draft.claim_candidates]}）")

    # D4. 反例（预算中性）：额度 0 **且一条都没幸存** ⇒ 没有定向重提案可排定，也**不得**凭空
    #     多要一次调用；终止原因只能说「没有剩余轮次」，不得说成「定向额度用尽」（两笔额度
    #     不得混说）。
    llm = _ScriptedLlm(only_risky, only_risky)
    try:
        run(case19, llm, policy=PW.WriterPolicy(max_llm_retries=0))
    except PW.ProposalSetRejectedError as exc:
        check(len(exc.rejections) == 1 and exc.rejections[0].answered_by_attempt is None
              and exc.rejections[0].reproposal is None
              and exc.rejections[0].carve_out is None,
              f"没有剩余轮次可排定时，记录必须如实写「未排定」，不得留一条跨修订 trace，"
              f"也不得留一条空裁出；且这一束这一轮**恰好一条**记录（实测 "
              f"{[(r.attempt, r.rejection_kind, r.answered_by_attempt) for r in exc.rejections]}）")
        check(len(exc.rejections[0].candidate_audit)
              == len(exc.rejections[0].candidate_ids),
              "逐候选审计条目数必须与原束候选数**相等**（两条出口各留一次档会让它翻倍）")
        check(len(llm.calls) == 2,
              f"定向重提案**不新增调用额度**：额度 0 时就只有那一轮（实测 {len(llm.calls)} 次）")
        check("重试额度已用尽" in str(exc) and "定向重提案额度" not in str(exc),
              f"终止原因必须指向「没有剩余轮次」而不是「定向额度用尽」——两笔额度不得混说"
              f"（实测 {str(exc)[-300:]!r}）")
    except Exception as exc:  # noqa: BLE001
        failed += 1
        details.append(f"FAIL 无剩余轮次时必须直接 fail-closed（抛出 "
                       f"{type(exc).__name__}: {str(exc)[:120]}）")
    else:
        failed += 1
        details.append("FAIL 无剩余轮次时不得产出章节")

    # ==================================================================
    # H. 失败回滚与逐次入账
    # ==================================================================
    # H1. 一批的返回形状不合法（`bsc-1`）⇒ **只把那一批**反馈它自己的准确错误后重问一次，
    #     其余批次的请求与返回一个字都不改，本轮照样成形。不得整轮重跑：整轮重跑会丢掉其余
    #     批次已经拿到的合法返回，而它们与这次失败无关。
    case_retry = company_case(len(sub_aspects), "task-batch-retry")
    llm = _ScriptedLlm('{"claim_candidates": [{"candidate_key": "c1"', plan_of(case_retry),
                       plan_of(case_retry))
    outcome = run(case_retry, llm, policy=PW.WriterPolicy(max_llm_retries=1))
    check([(r["round"], r["merged"], r["calls"]) for r in outcome.batch_audit["rounds"]]
          == [(1, True, 3)],
          f"批内纠正后本轮**照常成形**（1 轮 3 次调用：失败批 + 纠正 + 其余批），实测 "
          f"{[(r['round'], r['merged'], r['calls']) for r in outcome.batch_audit['rounds']]}")
    check(outcome.rejections == () and outcome.draft.writer_attempt == 1,
          f"整束从未被拒（失败的是**那一批的返回**，已就地纠正）：不得为它编一条整束拒绝"
          f"（实测 {[(r.attempt, r.rejection_kind) for r in outcome.rejections]}，"
          f"writer_attempt={outcome.draft.writer_attempt}）")
    round0 = outcome.batch_audit["rounds"][0]
    corrections = round0["shape_corrections"]
    check(len(corrections) == 1
          and corrections[0]["label"] == "1/2"
          and corrections[0]["rejection_kind"] == PW.BATCH_SHAPE_CORRECTION_TRIGGER_KIND
          and corrections[0]["note_version"] == PW.BATCH_SHAPE_CORRECTION_NOTE_VERSION
          and corrections[0]["correction"]["outcome"] == "accepted"
          and corrections[0]["rejected_call_id"]
          and corrections[0]["rejected_response_hash"],
          f"被拒那一批的**第一次返回**必须逐条留痕（call_id/hash/错误原文），并记下纠正那一次的"
          f"去向（实测 {corrections}）")
    check(round0["slack_steps_used"] == 1 <= PW.MAX_SWEEP_SHRINK_STEPS
          and round0["shrink_steps_used"] == 0,
          f"纠正与截断缩小**共用**同一份额外调用额度，用量可复核（实测 "
          f"{round0['slack_steps_used']} / 上界 {PW.MAX_SWEEP_SHRINK_STEPS}）")
    check(llm.seen[2][0] == "2/2",
          f"其余批次仍是**它自己**那一批（不被失败批次牵连、不重问，实测 {llm.seen[2][0]!r}）")
    check(PW.BATCH_SHAPE_CORRECTION_NOTE_VERSION not in llm.calls[2]["messages"][0]["content"],
          "批内纠正说明**只**发给失败的那一批：其余批次的请求不得出现它"
          "（跨批广播会让别的批次去修一件它没有做过的事）")
    check(llm.calls[1]["system"] == llm.calls[0]["system"]
          and llm.calls[2]["system"] == llm.calls[0]["system"],
          "重问不得换 prompt 资产（换 system 会让账本上同一 prompt 版本对应两种纪律）")
    check("上一次输出被拒" in llm.calls[1]["messages"][0]["content"]
          and llm.calls[1]["messages"][0]["content"].startswith("{"),
          "纠正说明必须**逐字追加在完整原请求之后**（不裁剪输入面、不换 prompt）")
    check(llm.calls[1]["messages"][0]["content"].index(PW.BATCH_SHAPE_CORRECTION_NOTE_VERSION)
          > llm.calls[1]["messages"][0]["content"].index('"batch"'),
          "纠正说明必须挂在**该批自己的**请求之后（它引用的 `c1..` 只是该批的编号）")
    check(sum(int(r["calls"]) for r in outcome.batch_audit["rounds"]) == outcome.llm_calls,
          "逐轮入账的调用数之和必须等于实际调用数（每一次分批请求都要入账）")
    check(len(outcome.llm_trace) == outcome.llm_calls,
          "调用 trace 与调用计数必须一致（分批不得让某一批悄悄不入账）")
    check(len(outcome.batch_audit["rounds"]) == outcome.draft.writer_attempt,
          "每一轮恰好一条分批审计（含中途失败的那一轮）")

    # H2. 同一批纠正后**仍然**不合法 ⇒ 再次失败即停（不纠正第二次、不整轮重跑、不留部分
    #     Draft），并把「该批覆盖的 aspect 因此没有候选」如实写进 typed 拒绝。
    llm = _ScriptedLlm('{"claim_candidates": [{"candidate_key": "c1"',
                       '{"claim_candidates": [{"candidate_key": "c1"')
    try:
        run(case_retry, llm, policy=PW.WriterPolicy(max_llm_retries=1))
    except PW.ProposalSetRejectedError as exc:
        check(len(llm.calls) == 2,
              f"同一批**不再**纠正第二次（实测 {len(llm.calls)} 次调用：原始 + 恰好一次纠正）")
        check(len(exc.rejections) == 1
              and exc.rejections[0].rejection_kind == "schema_invalid",
              f"被拒的**那一轮**必须留下恰好一条 typed 拒绝审计（实测 "
              f"{[(r.attempt, r.rejection_kind) for r in exc.rejections]}）")
        check("再次失败即停" in exc.rejections[0].rejection_detail
              and "没有任何候选进入合并结果" in exc.rejections[0].rejection_detail,
              f"拒绝详情必须写明「再次失败即停」与该批覆盖的 aspect 的**实际后果**（实测 "
              f"{exc.rejections[0].rejection_detail[-180:]!r}）")
        check(len(exc.rejections[0].batches) == 2,
              f"被拒那一次的**逐批读数**必须两次调用都在（原始 + 纠正），实测 "
              f"{len(exc.rejections[0].batches)}")
        check(all(r.proposal_ids == () and r.candidate_ids == () for r in exc.rejections),
              "被拒各束不得为半截输出编出任何候选身份")
    except Exception as exc:  # noqa: BLE001
        failed += 1
        details.append(f"FAIL 连续被拒必须 typed fail-closed（抛出 "
                       f"{type(exc).__name__}: {str(exc)[:120]}）")
    else:
        failed += 1
        details.append("FAIL 连续被拒时不得产出章节")

    # H2b. **整束级**失败（身份在权威侧闭不上）仍走通用重试通道：那一束里每一批的候选要彼此
    #      一致，因此「整轮重跑」是它的正确形态；通用说明不含批次坐标，也**不得**冒充批内纠正。
    bad_identity = plan_of(case_retry, candidates=(
        FIXT._cand("c1", case_retry["entry"].text,
                   FIXT._fact_edge(case_retry["scan"], "f-1",
                                   container_id="not-this-sections-container")),))
    llm = _ScriptedLlm(bad_identity, bad_identity, plan_of(case_retry), plan_of(case_retry))
    outcome = run(case_retry, llm, policy=PW.WriterPolicy(max_llm_retries=1))
    check([(r.attempt, r.rejection_kind) for r in outcome.rejections]
          == [(1, "proposal_identity_unresolvable")],
          f"整束级失败必须留下它自己那条 typed 原因（实测 "
          f"{[(r.attempt, r.rejection_kind) for r in outcome.rejections]}）")
    check(outcome.draft.writer_attempt == 2 and outcome.llm_calls == 4,
          f"整束级失败走**整轮重跑**（两批各重问一次，共 4 次调用），实测 "
          f"writer_attempt={outcome.draft.writer_attempt} / llm_calls={outcome.llm_calls}")
    check("上一次输出被拒" in llm.calls[2]["messages"][0]["content"]
          and llm.calls[2]["messages"][0]["content"].startswith("{")
          and PW.BATCH_SHAPE_CORRECTION_NOTE_VERSION not in llm.calls[2]["messages"][0]["content"],
          "整束级失败的说明是**通用**说明（逐字追加在完整原请求之后）：它不是某一批的形状"
          "错误，不得冒充批内纠正，也不得换 prompt 资产")

    # H3. 无事实、无材料但有显式缺口的那一节：**不调生成能力**，也没有批次审计。
    empty_task = FIXT._task("company", (FIXT.TOPIC_BUSINESS,), task_id="task-batch-empty")
    empty_set = FIXT._pack_set(empty_task, facts=(), aspect_results=())
    empty_authority = PW.TopicPackAuthorityInput.create(
        empty_task, empty_set, company_id=FIXT.COMPANY_ID, report_as_of=FIXT.REPORT_AS_OF,
        contract_version=FIXT.CONTRACT_VERSION,
        contract_fingerprint=FIXT.CONTRACT_FINGERPRINT)
    llm = _ScriptedLlm()
    try:
        empty_outcome = PW.write_section(
            empty_task, empty_authority, projection=projection, writing_spec=spec,
            presentation_profile=profile, llm_client=llm,
            dependency_fingerprint=FIXT.DEPENDENCY_FINGERPRINT,
            material_context=FIXT._writer_material_context(
                empty_set, task_id=str(empty_task.task_id), section_id="company"))
    except PW.PackWriterError as exc:
        skipped += 1
        details.append(f"SKIP 无事实那一节在本夹具下走不到 LLM 路径（{str(exc)[:80]}）")
    else:
        check(empty_outcome.llm_calls == 0 and llm.calls == [],
              "没有可选对象的那一节不得调用生成能力")
        check(empty_outcome.batch_audit == {},
              "零调用就没有批次：不得编一份空审计出来（`{}` 才是「没有请求」）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


def _financial_case() -> dict:
    """财务节：**权威不携带 aspect 状态** ⇒ `scan.aspect_status` 为空。

    这是零 aspect 批次在真实链上的来源（不是为测试造的形状）：见 `plan_aspect_batches`
    的零 aspect 分支与 `BatchCallRecord` 的空 aspect 允准条件。
    """
    spec = FIXT.WS.load_writing_spec(FIXT.SPEC_PATH)
    projection = PW.ContractProjection.create(
        spec, section_id="financial", contract_version=FIXT.CONTRACT_VERSION,
        contract_fingerprint=FIXT.CONTRACT_FINGERPRINT)
    task = FIXT._task("financial", (FIXT.TOPIC_FIN_SOLVENCY,),
                      task_id="task-fin-batching", title="偿债能力")
    artifact = FIXT._Artifact(
        artifact_id="ffpa_batch", task_id=task.task_id,
        facts=(FIXT._FinFact(fact_id="ff-short", label="短期偿债能力",
                             display="流动比率为 1.20 倍。", period=FIXT.REPORT_AS_OF,
                             unit="倍",
                             citation={"ref_type": "evidence", "evidence_id": FIXT.EV_ID,
                                       "page_number": 71}),))
    authority = FIXT._financial_authority(task, artifact, note_gap=FIXT._NoteGap(
        task_id=task.task_id))
    return {"task": task, "authority": authority, "projection": projection}


if __name__ == "__main__":
    try:  # GBK 控制台：中文详情必须可读，读不出来就等于没有失败原因
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

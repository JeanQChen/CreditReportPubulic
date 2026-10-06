"""离线替身读**请求面**的回归：领头的完整 JSON 值 + 其后逐字追加的说明。

## 这条回归盯着什么

写入侧把返修说明 / 栏目定向说明 / 批内形状纠正说明**逐字追加在完整原请求之后**
（`PW._append_note`，唯一追加点 `PW._messages_for`），所以真实模型看到的请求是
「一个完整 JSON 值 + 换行 + 一段中文说明」。替身 `OfflineNarrationClient` 从**同一份请求**
里读它要回答的那批输入，用 `json.loads` 读整段就会在这种请求上抛 `Extra data`。

现场就是这么坏的：M930-3 离线验收里 company / financial 两节**各自有一批**上一次返回形状不合法，
链按设计只把那一批放回队首重问并附上批内纠正说明——而那一次的请求正是「JSON + 说明」。
替身当场 `JSONDecodeError: Extra data: line 4 column 1`，于是**这一节整节没有产出**
（等价于「一次格式抖动 = 这一节写不出来」）。不是链的问题，也不是模型的问题：链按设计正常工作，
是替身读不出自己那份请求。

## 为什么容忍的边界必须这么窄

`Extra data` 有**两种**来路，只有一种该被容忍：

  * 「一个完整值 + 说明」——写入侧的请求纪律，是**正常**请求；
  * 「两个 JSON 值首尾相接」——请求**坏了**。

两者在 `json.loads` 眼里一模一样。因此判别只能落在**分隔符**上：`PW._append_note` 保证追加前
原请求以换行结尾，说明因此总是以换行开头。要求「剩余部分以换行开头」，就把上面两种来路分开了。
放宽成「剩余部分非空」会把坏请求也读成正常请求（`{"a":1}{"b":2}` 会被静默读成 `{"a":1}`），
那是替身替链把一个坏请求圆过去——本模块有一条反例专门钉住它不许发生。

## 与 `PW.load_json_with_proven_leniency` 的关系

那份容错管的是**模型返回**（字符串内裸控制字符那一类已证明的序列化缺陷），本模块管的是**请求面**。
两件事不得互相借道：请求面领头的值是本进程自己 `json.dumps` 出来的，任何容错都不该被用到，
所以这里连 `strict=False` 都不开。本模块有一条断言钉住这条边界仍然成立。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evaluation import run_m930_3_acceptance as ACC  # noqa: E402
from sections import pack_writer as PW  # noqa: E402

#: 一份**真的**批内形状纠正说明（走生产的同一个构造器，不手写一段像说明的文本）。
NOTE = PW._batch_shape_correction_note(
    error="候选 c3 的支撑边引用了本批没有声明过的行",
    batch_label="批 2/3", batch_id="b-2", aspect_ids=("co-h3", "co-h4"),
    declared_fact_refs=2, declared_material_refs=5)


def _payload() -> dict:
    """一份**形状真实**的门前提案请求体（键取自替身真正读的那几个）。

    事实行带着它**自己的引用**（`material_id` / `locator_ref`）：路径 A 的 material 锚点由那一行
    派生、不由模型选（`_factual_proposal`），因此写侧请求面必须逐行给出它——本模块第 7 组正是按
    这一格判「这条事实的草稿该落在哪条轴上」。
    """
    return {
        "task": {"section_id": "company", "topic_ids": ["company_business_main"]},
        "batch": {"label": "批 2/3", "batch_id": "b-2", "aspect_ids": ["co-h3", "co-h4"]},
        "authority_facts": [{"ref": "f1", "material_id": "mat-1",
                             "text": "公司主营业务为动力电池系统的研发、生产与销售。"}],
        "materials": [{"ref": "m1", "material_id": "mat-1", "topic_id": "company_business_main",
                       "text": "公司采用直销模式，客户覆盖境内外主要整车企业。"}],
        "must_use_facts": [{"ref": "f1"}],
    }


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

    body = json.dumps(_payload(), ensure_ascii=False)

    # ==================================================================
    # 1. 生产的追加形状：`_append_note` 的产出确实是「完整值 + 换行 + 说明」
    # ==================================================================
    check(PW._append_note(body, NOTE) == body + "\n" + NOTE,
          "`_append_note` 必须原样保留完整原请求、只在其后补一个换行再逐字追加说明"
          "（本模块的判别规则就钉在它这个形状上；哪天它改了，这里先红）")
    check(NOTE.startswith("\n"),
          f"批内纠正说明自身以换行开头（实为 {NOTE[:12]!r}），"
          "因此「剩余部分以换行开头」这条判据对**两层说明**（整轮说明 → 本批说明）同样成立")

    # ==================================================================
    # 2. 纯 JSON：读法与 `json.loads` 逐字节等价（不是「另一套宽松读法」）
    # ==================================================================
    check(ACC.read_request_payload(body, what="用例") == json.loads(body),
          "整段就是合法 JSON 时，替身读到的必须与 `json.loads` 完全相同")

    # ==================================================================
    # 3. 请求 + 说明：这是**正常**请求，必须读得出来，且读到的是原来那个 payload
    # ==================================================================
    with_note = PW._append_note(body, NOTE)
    check(ACC.read_request_payload(with_note, what="用例") == _payload(),
          "「完整原请求 + 逐字追加的说明」必须读出原来那份 payload")
    check(with_note != body and len(with_note) > len(body),
          "夹具必须真的带上了说明（否则第 3 组测的还是纯 JSON，红绿都说明不了事）")

    # 两层说明（整轮说明 → 本批批内说明）同样是「值 + 换行 + 说明」：逐字增长不改变读法。
    twice = PW._append_note(PW._append_note(body, "【返修说明】上一轮有一处不合格。"), NOTE)
    check(ACC.read_request_payload(twice, what="用例") == _payload(),
          "整轮说明与本批说明叠加之后仍必须读出同一份 payload"
          "（`_add_note` 与 `batch_notes` 是同一条追加通道，读法只有一种）")

    # ==================================================================
    # 4. 反例：坏请求一律不许被圆过去
    # ==================================================================
    def _raises(text: str) -> bool:
        try:
            ACC.read_request_payload(text, what="反例")
            return False
        except ValueError:
            return True

    check(_raises(body + '{"second": true}'),
          "两个 JSON 值首尾相接必须抛回（形如 `{\"a\":1}{\"b\":2}`）——它同样报 `Extra data`，"
          "但它是**坏请求**，不是「请求 + 说明」；容忍它等于替链把一次请求毁损读成一次正常请求")
    check(_raises(body + ' {"second": true}'),
          "空格分隔的第二个 JSON 值同样必须抛回（分隔符不是换行 ⇒ 那不是说明）")
    check(_raises('{"a": 1'),
          "领头的值本身不完整必须抛回（不得靠 `raw_decode` 读出一个前缀就当整段合法）")
    check(_raises(body[:len(body) // 2]),
          "被截断的原请求必须抛回（这正是「provider 截断」那类现场，读成合法就是把残缺当完整）")
    check(_raises(""),
          "空正文必须抛回（不得读成空 payload：那会在下游退化成「本节没有输入」）")

    # 边界：说明以回车开头（CRLF 现场）同样是「以换行开头」，不得因为平台差异被当成坏请求。
    check(ACC.read_request_payload(body + "\r\n说明", what="用例") == _payload(),
          "分隔符是回车换行时同样必须读得出来（不得把平台差异误判成坏请求）")

    # ==================================================================
    # 5. 边界不许向模型返回的容错借道
    # ==================================================================
    # 字符串内裸换行那一类缺陷（`PW._RAW_CONTROL_ALLOWED`）只为**模型返回**证明过；
    # 请求面领头的值是本进程自己 `json.dumps` 出来的，不该出现它，更不该被容忍。
    check(_raises('{"text": "第一行\n第二行"}\n说明'),
          "请求面**不**开 `strict=False`：字符串内裸控制字符不得因为「返回侧容忍它」而被放行"
          "（两条容错口径不得互相借道）")

    # ==================================================================
    # 6. 接线：替身真的走这条读法，且没有第二条裸 `json.loads` 留在请求面上
    # ==================================================================
    source = Path(ACC.__file__).read_text(encoding="utf-8")
    check('json.loads(messages[0]["content"])' not in source,
          "门前提案那一处不得再用裸 `json.loads` 读整段请求"
          "（它会在「本批被形状纠正后重问」的那一次抛 `Extra data`）")
    check('read_request_payload(messages[0]["content"]' in source,
          "门前提案必须改走 `read_request_payload`")

    # 真类驱动：把「请求 + 说明」喂给**替身自己**，它必须照常产出提案束。
    client = ACC.OfflineNarrationClient()
    result = client._proposals([{"role": "user", "content": with_note}],
                               PW.NARRATION_PROMPT_VERSION, PW.MODEL_POLICY_STUB,
                               "offline-probe")
    plan = json.loads(result.text)
    check(bool(plan.get("claim_candidates")),
          f"带说明的请求必须照样产出提案束（实得 {sorted(plan)} / "
          f"{len(plan.get('claim_candidates') or ())} 条候选）")
    keys = {str(c["candidate_key"]) for c in plan["claim_candidates"]}
    check(any(k.startswith("b") for k in keys),
          f"路径 B 那条（材料切片）必须在场（实得 {sorted(keys)}）："
          "带说明的这一次与不带说明的那一次必须是同一份回答")

    plain = ACC.OfflineNarrationClient()._proposals(
        [{"role": "user", "content": body}], PW.NARRATION_PROMPT_VERSION,
        PW.MODEL_POLICY_STUB, "offline-probe-plain")
    check(json.loads(plain.text) == plan,
          "同一份 payload 上，「带说明」与「不带说明」两次问答必须逐字一致"
          "（说明只影响模型要不要纠正，不改变替身对同一份输入面的确定性回答）")

    # ==================================================================
    # 7. 草稿层：替身的回答必须真的过**当前线**的门（不是形状像而已）
    # ==================================================================
    # 当前线（`PW.PROPOSAL_WIRE_CURRENT`）把 `natural_prose_draft` 摆成**第一个**顶层键且
    # **必备**：「有候选、无草稿」是 typed failure（`natural_prose_draft_missing`）。替身若还停在
    # 旧线（只答候选），整批会被拒 —— 这正是本模块标题那句「替身读不出自己那份请求」之后的
    # 第二件事：读得出请求，还要答得出**这条线要求的形状**。
    check(list(plan)[0] == "natural_prose_draft",
          f"草稿必须是第一个顶层键（实得 {list(plan)[:1]}）："
          "当前线的解析只认这个顺序里那个必备的草稿键")
    check(isinstance(plan.get("natural_prose_draft"), list) and bool(plan["natural_prose_draft"]),
          "有候选就必须有草稿（rules：草稿先写、候选是为草稿里每个事实原子补的账）")

    # 权威侧：逐行一条声明选项（`saref-1` 要求编号连续、材料选项恒为 topic_pack）。
    def _aliases(*, facts: int = 0, materials: int = 0) -> PW.SupportAliasTable:
        return PW.SupportAliasTable(
            facts=tuple(PW.SupportOption(
                ref=f"f{i}", authority_kind="topic_pack",
                container_identity=f"pack-m{i}", fact_id=f"fact-{i}")
                for i in range(1, facts + 1)),
            materials=tuple(PW.SupportOption(
                ref=f"m{i}", authority_kind="topic_pack",
                container_identity=f"pack-m{i}", material_id=f"mat-{i}")
                for i in range(1, materials + 1)))

    # 本节**有材料行** ⇒ 草稿出处只准材料轴（`pprov-1`：有材料可写时走事实轴，读者面回溯不到
    # 实际写出的那段文字）。替身必须按请求**自己的**输入面选轴，而不是随手选一条。
    parsed = PW.parse_writer_proposals(
        result.text, aliases=_aliases(facts=1, materials=1))
    prose = parsed["natural_prose_draft"]
    check(bool(prose) and all(u["source_member_refs"] and not u["source_fact_refs"]
                              for u in prose),
          "本节有材料行时，每段草稿的出处必须恰是材料轴（`source_member_refs` 非空、"
          "`source_fact_refs` 为空）")
    candidate_keys = {c["candidate_key"] for c in parsed["claim_candidates"]}
    atom_keys: set[str] = set()
    for unit in prose:
        atom_keys |= set(unit["atom_candidate_keys"])
    check(atom_keys == candidate_keys,
          f"草稿声明的原子键并集必须**恰好等于**候选键集（实得草稿 {sorted(atom_keys)} / "
          f"候选 {sorted(candidate_keys)}）：多一条是伪造映射，少一条是候选绕过草稿塞入")
    for unit in prose:
        for candidate in parsed["claim_candidates"]:
            if candidate["candidate_key"] in unit["atom_candidate_keys"]:
                check(candidate["claim_text"] in unit["text"],
                      f"候选 {candidate['candidate_key']} 的文本必须**逐字**长在它所在的草稿段里"
                      "（草稿是候选的来源，不是另写的一段）")

    # 本节**只有**权威事实行（财务节的常态，精确材料清单合法为空）⇒ 只准走事实轴。
    # 文本里不得出现未定义报告期措辞（`VAGUE_PERIOD_PHRASES`）：那是替身**自己**的提案纪律
    # （含糊期间的原子一律不提），夹具若用了它，这一组会退化成「替身没提候选」而不是「轴选对了」。
    facts_only = {
        "task": {"section_id": "financial", "topic_ids": ["financial_revenue"]},
        "authority_facts": [{"ref": "f1",
                             "text": "公司主营业务为动力电池系统的研发、生产与销售。"}],
        "must_use_facts": [{"ref": "f1"}],
    }
    fact_plan = json.loads(ACC.OfflineNarrationClient()._proposals(
        [{"role": "user", "content": json.dumps(facts_only, ensure_ascii=False)}],
        PW.NARRATION_PROMPT_VERSION, PW.MODEL_POLICY_STUB, "offline-probe-facts").text)
    fact_parsed = PW.parse_writer_proposals(
        json.dumps(fact_plan, ensure_ascii=False), aliases=_aliases(facts=1))
    check(bool(fact_parsed["natural_prose_draft"])
          and all(u["source_fact_refs"] and not u["source_member_refs"]
                  for u in fact_parsed["natural_prose_draft"]),
          "本节只有权威事实行时，每段草稿的出处必须恰是事实轴（材料面合法为空的那一档）")

    # 两张表都空 ⇒ 草稿是空数组（`rules`：这一档草稿与候选都留空，诉求写进 `follow_up_needs`）。
    empty_plan = json.loads(ACC.OfflineNarrationClient()._proposals(
        [{"role": "user", "content": json.dumps({"task": {"section_id": "company"}},
                                                ensure_ascii=False)}],
        PW.NARRATION_PROMPT_VERSION, PW.MODEL_POLICY_STUB, "offline-probe-empty").text)
    check(empty_plan.get("natural_prose_draft") == [] and not empty_plan.get("claim_candidates"),
          "两张来源表都空时草稿与候选都必须留空（空是**结论**，不是忘了答）")

    result = {"passed": passed, "failed": failed, "skipped": 0, "details": details}
    return result


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

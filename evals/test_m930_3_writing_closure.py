"""M930-3 返修 **P2 · 写作收口** 的聚焦反例集（计划 §5 第 9/10/11 行）。

用法（无管道/无重定向）：`PYTHONIOENCODING=utf-8 python -m evals.test_m930_3_writing_closure`

P2 要修的三处缺陷，各自的**反例**与**防修过头**的**正向对照**都在这里：

* §1 **边界感知的实体表面判定**（计划 §5 第 9 行）。组合句的高风险表面核验不得跨
  「Claim 段 | 组织段」的接缝**合成** token：`…年` + `同时` + `公司…` 曾凭空产出一个两条
  Claim 文本里都不存在的「…同时公司」，让**同一份合规正文**时过时不过（取决于接缝恰好落在
  哪）。修的是「量错了对象」，**不是**放宽门：
  * 1.1 接缝假主体的反例：旧的整句扫描非空、边界感知为空、冻结门放行；
  * 1.1b 判据 e（`nrules-12`）之后，**没有分隔标点**的接缝本身已被拒（`unseparated_seam`）：
    假表面是「整句扫描量错对象」的证据，不是拒它的理由——两个理由不得混成一个；
  * 1.2 **防修过头**：组织器在衔接区自己写一个**完整主体名**，边界感知必须照旧拒（法人主体门
    一个字都没删，也没有给任何词做白名单）；
  * 1.3 **防修过头**：衔接区自己写一个**数字**（`42%`）同样必须被拒（数字门一字不减）；
  * 1.4 **防修过头**：衔接区自己写一个**含糊期间**（「报告期」）同样必须被拒；
  * 1.5 定位不成立（某条 Claim 文本没按序出现）时退化为整句扫描：不替该句挑一个更松的判据。
* §2 **「一句就是一个句子」**（计划 §5 第 10/11 行）。`Claim1。同时Claim2。` 是**两个句子**
  首尾相接，不得被算成「多 Claim 自然句」；反过来，**两条不带句末标点的原子子句 + 安全衔接语**
  必须算一条。判据同时落在两侧，且必须**各自实现、逐例一致**：
  * 2.1 冻结门 `NS.verify_section_narrative`：容器拒、原子子句放行；
  * 2.2 验收 runner 的**独立复算** `ACC._natural_organization_audit`：同一批夹具上
    `natural` 的真假与冻结门是否抛错**逐例一致**（前者不得回放后者的结论）；
  * 2.3 `multi_claim_count` 仍如实统计「声明了几条 Claim」——被拒的容器**也**计入该计数，
    但它不许计入 `natural_count`（计数被灌水正是这条判据要拦的）。
* §3 版本纪律：判定集变了就必须前进，prompt 要求变了则组织器规则版本跟着动。
  **本模块写下的那几轮**都是「判定集前进、wire 不动」（当时 `nrules-13` / `ng-12`、`narr-6` 不动、
  `norg-5`）；M930-3 指令 E 第 3 项**破了这条**——门前自然草稿层进 wire（`narr-7`），第 8 条按句类
  分派（`nrules-15` / `ng-14`）。两件事在下面的 check 里逐条留痕，历史那几轮的记录不改写。
* §4 **有界定向重组织**（⑥，计划 §5 第 6 行）。第 8 条**照旧拒**拼接句，被拒的那一版由系统**确定性**
  重组织（不再发一次模型调用）：先按模型自己的句中句末标点切段并逐段重绑 Claim（**文本逐字不变**，
  模型写的衔接语跟着相邻那段留下），切不出全合法的段时改为**每条 Claim 各自一句**逐字事实句
  （只丢弃纯标点的接缝）。这里同时钉住三件容易修过头的事：
  * 4.1 切段是**逐字**的（`"".join(parts) == 原文`），衔接语不被删也不被搬走；
  * 4.2 `claims_not_present_in_order` **不修**（切不出声明里没有的 Claim）——照旧 fail-closed；
  * 4.3 补救只治**组织形态**：改完的正文仍要整份过冻结门，衔接区里自造的主体名 / 数字 / 含糊
    期间**照样被拒**（表面门一字未松）；
  * 4.4 原句声明的 context 绑定**不承接**（`factual` 句本来就不许带 context，第 4 条）。
* §5 **正文不得替系统自报检索 / 核验**（⑥，第 9 条）：材料写着的照转不误（Claim 文本逐字在场即
  合法），材料没写而正文写了「本系统已独立联网核验」即拒。含勾选符号的**原文转录**必须被放行
  ——判据只拒「系统自报」，**不做**「一律删除勾选符号」的粗清洗。

不读库、不连网、不调 LLM、不写任何文件：夹具是判据在真实链上会读到的那些形状。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import run_m930_3_acceptance as ACC
from harness import schema as HS
from llm import client as LLMC
from sections import narrative_organizer as NO
from sections import narrative_schema as NS
from sections import pack_writer as PW
from sections import schema as SS

TOPIC = "topic-company-business"
IDENTITY = {"task_id": "task-1", "section_id": "company",
            "section_draft_id": "draft-1", "draft_revision": "rev-1"}

#: 两条 Claim 的文本都是**原子子句**（不带句末标点），且第一条以「年」收尾——
#: 这正是接缝假主体（`…年` + `同时` + `公司…`）得以合成的那类现场。
CLAIM_A = "公司于2011年设立生产基地"
CLAIM_B = "公司销售以直销为主"
#: 安全衔接语：取自冻结词表 `NS.CONNECTORS`（词表里每一条都自带**尾部**逗号），本身不含任何
#: 高风险表面。接缝必须**以分隔标点起头**（`nrules-12` 判据 e）：连接语自带尾部逗号，直接粘在
#: 上一条断言后面就会读成「…设立生产基地同时，公司…」，因此合法写法是 `A，同时，B。`
CONNECTOR = "同时，"
#: 合法的组织形态（接缝以分隔标点起头）。
COMPOSED_OK = CLAIM_A + "，" + CONNECTOR + CLAIM_B + "。"
#: 反面形态：接缝**没有**分隔标点（`A同时B。`）。`ng-10` 之前它是这套夹具的「合规正文」，
#: 判据 e 之后被冻结门拒（`unseparated_seam`）。留它在现场是为了同时钉住两件事：整句扫描会
#: 在这个接缝上合成出两条 Claim 里都不存在的表面；而冻结门拒它的理由**不是**那个合成表面。
#: 这里刻意用**不带尾部逗号**的连接语：扫面合成本来就发生在「没有标点可断」的位置，
#: 带上尾部逗号会让扫描在那个逗号处切开、假表面随之消失（那正是判据 e 要的那道标点）。
GLUED_CONNECTOR = "同时"
GLUED_SEAM = CLAIM_A + GLUED_CONNECTOR + CLAIM_B + "。"


def _claim(text: str, *, index: int) -> SS.SectionClaim:
    question_ids = (f"q-{index}",)
    refs = (HS.CitationRef(ref_type="evidence", evidence_id=f"evidence:ev-{index}"),)
    claim_id = SS.derive_claim_id("fact", TOPIC, question_ids, text, refs,
                                  f"cand-{index}", "rev-1", (f"asb-{index}",))
    return SS.SectionClaim(
        claim_id=claim_id, schema_version=SS.CLAIM_SCHEMA_VERSION, section_id="company",
        topic_id=TOPIC, question_ids=question_ids, text=text, claim_type="fact",
        citation_refs=refs, claim_candidate_id=f"cand-{index}",
        claim_candidate_revision="rev-1", accepted_binding_ids=(f"asb-{index}",))


CLAIM_OBJ_A = _claim(CLAIM_A, index=1)
CLAIM_OBJ_B = _claim(CLAIM_B, index=2)
CLAIMS = (CLAIM_OBJ_A, CLAIM_OBJ_B)


class _Section:
    """`ACC._natural_organization_audit` 按属性名读取的**最小**容器（只有 claims + narrative）。

    它**不是** `SectionResult` 的替身：这里只证明判据本身会拒，不主张任何产物的合法性。
    """

    def __init__(self, claims, narrative) -> None:
        self.claims = claims
        self.narrative = narrative


def _narrative(sentence_texts) -> NS.SectionNarrative:
    """按 `sentence_texts`（句文本 → 声明哪几条 Claim）造一份 final Narrative。

    段落 topic 由所引用的 Claim 派生（与真实链同一口径），citation 由所声明 Claim 派生。
    """
    specs = []
    for text, kind, claim_objs in sentence_texts:
        citations: list[str] = []
        for c in claim_objs:
            for ref in c.citation_refs:
                cid = SS.derive_citation_id(c.claim_id, ref)
                if cid not in citations:
                    citations.append(cid)
        specs.append({"text": text, "sentence_kind": kind,
                      "claim_ids": [c.claim_id for c in claim_objs],
                      "citation_ids": citations})
    paragraph = NS.NarrativeParagraph.create(
        section_id="company", topic_ids=(TOPIC,), index=0, sentence_specs=specs)
    return NS.SectionNarrative.create(
        task_id=IDENTITY["task_id"], section_id="company",
        section_draft_id=IDENTITY["section_draft_id"],
        draft_revision=IDENTITY["draft_revision"], paragraphs=(paragraph,), tables=())


def _verify(text: str, kind: str, claim_objs) -> None:
    """冻结门对「只有这一句」的 Narrative 的判定（抛错=拒）。"""
    NS.verify_section_narrative(
        narrative=_narrative([(text, kind, claim_objs)]), claims=CLAIMS,
        accepted_context_binding_ids=(), dispositions=())


def _audit_row(text: str, kind: str, claim_objs) -> dict:
    """runner 侧**独立复算**在该句上的判定行。"""
    audit = ACC._natural_organization_audit(
        _Section(CLAIMS, _narrative([(text, kind, claim_objs)])))
    rows = audit["sentences"]
    return {"multi_claim_count": audit["multi_claim_count"],
            "natural_count": audit["natural_count"],
            "row": (rows[0] if rows else None)}


def _plan(sentence_specs, claim_objs) -> dict:
    """一份**形状与 `parse_organizer_plan` 输出一致**的句计划（本节夹具只放一段一句话）。"""
    return {"paragraphs": [{"sentences": [dict(s) for s in sentence_specs]}],
            "claim_dispositions": [{"claim_id": c.claim_id, "disposition": "selected",
                                    "reason_code": None} for c in claim_objs]}


def _reorganize(sentence_specs, claim_objs):
    """跑一次 ⑥ 的有界定向重组织：返回 `(重组织后的句规格, 补救记录)`。

    走的是**真实链**的两个函数（`NO._bounded_reorganization` → `NO._sentence_specs`），
    不是测试自带的替身：补救产物必须能直接喂给 `NarrativeParagraph.create`。
    """
    known = {c.claim_id: c for c in claim_objs}
    plan = _plan(sentence_specs, claim_objs)
    fixed, records = NO._bounded_reorganization(plan, known_claims=known)
    specs = NO._sentence_specs(fixed["paragraphs"][0], known_claims=known)
    return fixed, records, specs


def _narrative_from_specs(specs) -> NS.SectionNarrative:
    paragraph = NS.NarrativeParagraph.create(
        section_id="company", topic_ids=(TOPIC,), index=0, sentence_specs=specs)
    return NS.SectionNarrative.create(
        task_id=IDENTITY["task_id"], section_id="company",
        section_draft_id=IDENTITY["section_draft_id"],
        draft_revision=IDENTITY["draft_revision"], paragraphs=(paragraph,), tables=())


def _gate_error(narrative, claims=CLAIMS) -> str:
    """冻结门对该 Narrative 的判定（拒 → 错误文本；放行 → 空串）。"""
    try:
        NS.verify_section_narrative(
            narrative=narrative, claims=claims, accepted_context_binding_ids=(),
            dispositions=())
    except NS.NarrativeSchemaError as e:
        return str(e)
    return ""


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

    def rejects(text: str, kind: str = "composed", claims=CLAIMS) -> str:
        """冻结门是否拒该句：返回错误文本，放行则返回空串。"""
        try:
            _verify(text, kind, claims)
        except NS.NarrativeSchemaError as e:
            return str(e)
        return ""

    # ============================================================ §1 边界感知的表面核验
    texts = (CLAIM_A, CLAIM_B)
    phantom = [t for t in NS.unauthorized_surfaces(GLUED_SEAM, texts)
               if t not in CLAIM_A and t not in CLAIM_B]
    check(bool(phantom),
          f"前置条件：整句一次扫描必须在接缝上合成出两条 Claim 里都不存在的表面（实测 {phantom}）")
    check(any("同时" in t and "公司" in t for t in phantom),
          f"前置条件：合成的假表面必须是「…同时公司」那一类接缝产物（实测 {phantom}）")
    check(not NS.unauthorized_surfaces_within_claims(COMPOSED_OK, texts),
          "边界感知核验不得跨接缝合成表面：同一份合规正文必须稳定通过"
          f"（实测仍报 {NS.unauthorized_surfaces_within_claims(COMPOSED_OK, texts)}）")
    # 1.0b 判据 e（`nrules-12`）：上面那条假表面所在的**无分隔标点**接缝，本身已被冻结门拒。
    # 两个理由必须分开：整句扫描的结论是「量错了对象」（假表面），冻结门的结论是「这不是一句
    # 组织过的正文」（黏连接缝）。把后者说成前者，会让「给它补个逗号」看起来像在修 bug。
    check(NS.composed_organization_defect(text=GLUED_SEAM, authorized_texts=texts)
          == NS.COMPOSED_DEFECT_UNSEPARATED_SEAM,
          "无分隔标点的接缝必须被判为 `unseparated_seam`"
          f"（实测 {NS.composed_organization_defect(text=GLUED_SEAM, authorized_texts=texts)!r}）")
    glued_err = rejects(GLUED_SEAM)
    check(bool(glued_err) and "接缝" in glued_err and "合成" not in glued_err,
          f"冻结门必须按「接缝没有分隔标点」拒它，而不是按扫描合成的假表面（实测拒因："
          f"{glued_err[:160]}）")
    # 1.1 修的那一面：冻结门必须放行这条合规正文（修复前它在接缝恰好落在这里时被拒）。
    check(rejects(COMPOSED_OK) == "",
          f"合规的「原子子句 + 安全衔接语」组合句必须被冻结门放行（实测拒因："
          f"{rejects(COMPOSED_OK)[:120]}）")
    check(not NS.has_internal_sentence_terminator(COMPOSED_OK),
          "前置条件：该合规句的句末标点只在末尾")

    # 1.2 **防修过头**：衔接区里自造一个完整主体名，仍必须被拒（法人主体门一字未删）。
    entity_in_connector = CLAIM_A + "，" + "宁德新能源科技有限公司" + "，" + CLAIM_B + "。"
    hits = NS.unauthorized_surfaces_within_claims(entity_in_connector, texts)
    check(any("公司" in t for t in hits),
          f"衔接区里自造主体名必须被边界感知核验抓住（实测 {hits}）")
    check(bool(rejects(entity_in_connector)),
          "衔接区里自造主体名必须被冻结门拒"
          f"（实测拒因：{rejects(entity_in_connector)[:120]}）")

    # 1.3 **防修过头**：衔接区里自造一个数字，仍必须被拒（数字门一字未减）。
    number_in_connector = CLAIM_A + "，" + "同比提升42%" + "，" + CLAIM_B + "。"
    hits_num = NS.unauthorized_surfaces_within_claims(number_in_connector, texts)
    check(any("42" in t for t in hits_num),
          f"衔接区里自造数字必须被边界感知核验抓住（实测 {hits_num}）")
    check(bool(rejects(number_in_connector)),
          f"衔接区里自造数字必须被冻结门拒（实测拒因：{rejects(number_in_connector)[:120]}）")

    # 1.4 **防修过头**：衔接区里自造一个含糊期间，仍必须被拒（含糊期间门一字未减）。
    period_in_connector = CLAIM_A + "，" + "报告期内" + "，" + CLAIM_B + "。"
    check(bool(rejects(period_in_connector)),
          "衔接区里自造含糊期间必须被冻结门拒"
          f"（实测拒因：{rejects(period_in_connector)[:120]}）")

    # 1.5 定位不成立时退化为整句扫描：那种句子本来就 fail-closed，不替它挑更松的判据。
    not_ordered = CLAIM_B + "同时，" + CLAIM_A + "。"   # 顺序与声明相反
    check(NS.unauthorized_surfaces_within_claims(not_ordered, texts)
          == NS.unauthorized_surfaces(not_ordered, texts),
          "Claim 文本没有按序出现时必须退化为整句扫描（不得挑一个更松的判据）")

    # ============================================================ §2 一句就是一个句子
    container = CLAIM_A + "。同时，" + CLAIM_B + "。"
    check(NS.has_internal_sentence_terminator(container)
          and not NS.has_internal_sentence_terminator(COMPOSED_OK),
          "判据 d 必须只拒「句中句末标点」：末尾的一个句号是合法句读")

    # 2.1 冻结门：容器拒（双句容器不是自然句）、原子子句放行。
    container_err = rejects(container)
    check(bool(container_err) and "句中" in container_err,
          f"`Claim1。同时Claim2。` 必须被冻结门拒（实测拒因：{container_err[:120]}）")
    check(rejects(COMPOSED_OK) == "", "两条无句末标点的原子子句 + 安全衔接语必须放行")

    # 2.2 runner 侧**独立复算**：逐例与冻结门一致（各自实现，不回放对方结论）。
    ok_row = _audit_row(COMPOSED_OK, "composed", CLAIMS)
    bad_row = _audit_row(container, "composed", CLAIMS)
    check(ok_row["natural_count"] == 1 and ok_row["row"]["natural"] is True,
          f"原子子句组合句必须被独立复算判为「自然组织」（实测 {ok_row['row']}）")
    check(ok_row["row"]["raw_join"] is False and ok_row["row"]["internal_terminator"] is False,
          "原子子句组合句既不是机械拼接、也不含句中句末标点")
    check(bad_row["natural_count"] == 0 and bad_row["row"]["natural"] is False,
          f"双句容器不得被独立复算判为「自然组织」（实测 {bad_row['row']}）")
    check(bad_row["row"]["internal_terminator"] is True,
          "双句容器必须被记为「含句中句末标点」")
    check(bad_row["row"]["raw_join"] is False,
          "双句容器**不是**机械拼接（`A。同时B。` 有组织语，缺的是「它是一句」）——"
          "两条拒绝理由不得混成一个字段")
    check(bad_row["row"]["claims_verbatim_in_order"] is True,
          "双句容器里两条 Claim 文本确实逐字按序在场（拒它的理由只能是没有真被组织成一句）")
    # 2.2b 判据 e 的独立复算：`natural_count` 不得被「黏连接缝」的句子灌水——那种句子过不了
    # 冻结门，因此也不该在验收侧被数成一条自然句。两侧各自实现、逐例一致（见下面的循环）。
    seam_row = _audit_row(GLUED_SEAM, "composed", CLAIMS)
    check(seam_row["natural_count"] == 0 and seam_row["row"]["natural"] is False
          and seam_row["row"]["unseparated_seams"] == 1,
          f"黏连接缝的句子不得被独立复算判为「自然组织」（实测 {seam_row['row']}）")
    check(seam_row["row"]["raw_join"] is False
          and seam_row["row"]["internal_terminator"] is False
          and seam_row["row"]["claims_verbatim_in_order"] is True,
          "黏连接缝不是机械拼接、也不是双句相接：拒它的判据必须自己占一个字段"
          f"（实测 {seam_row['row']}）")
    check(ok_row["row"]["unseparated_seams"] == 0,
          "合规接缝的分隔标点计数必须为 0（判据不得把「有组织语」当成「有分隔标点」）")
    # 逐例一致性：同一批夹具上，冻结门是否拒 == 独立复算是否判非自然。
    for label, text, kind, objs in (
            ("原子子句 + 安全衔接语", COMPOSED_OK, "composed", CLAIMS),
            ("`A。同时B。` 双句容器", container, "composed", CLAIMS),
            ("黏连接缝 `A同时，B。`", GLUED_SEAM, "composed", CLAIMS),
            ("机械拼接", CLAIM_A + CLAIM_B, "composed", CLAIMS),
            ("单 Claim 事实句", CLAIM_A + "。", "factual", (CLAIM_OBJ_A,))):
        gate_rejects = bool(rejects(text, kind, objs))
        row = _audit_row(text, kind, objs)["row"]
        audited = bool(row) and not row["natural"]
        check(gate_rejects == audited,
              f"冻结门与独立复算必须逐例一致（{label}：门拒={gate_rejects} / "
              f"复算非自然={audited}）")

    # 2.3 计数：容器仍如实计入「声明了几条 Claim」，但不得计入「自然句」。
    check(bad_row["multi_claim_count"] == 1 and bad_row["natural_count"] == 0,
          "被拒的容器必须仍计入 multi_claim_count（如实统计），但不许计入 natural_count"
          f"（实测 {bad_row['multi_claim_count']} / {bad_row['natural_count']}）")
    # 单 Claim 句不进这个计数（它是 factual 句，不是「多 Claim 自然句」）。
    single = _audit_row(CLAIM_A + "。", "factual", (CLAIM_OBJ_A,))
    check(single["row"] is None and single["multi_claim_count"] == 0,
          "单 Claim 句不得进入「多 Claim 自然句」计数")

    # ============================================================ §3 版本纪律
    # M930-3 §一.1 改了共享扫描语义（主体名 token 与高风险表面词同一实现、三个使用点都经它），
    # 因此本模块开工时的 nrules-9 必须前进到 nrules-10；门判定集**未变**（ng-9 保留）。
    # §四 读者面实现之后的**实测修订**：当时预计「正文指纹一动就 ng-9 → ng-10」，实现时确认
    # 该前提不成立——§四 只改**渲染**（`render_paragraphs_markdown` 的 scope 行经
    # `reader_unit_text`、代理口径说明经 `proxy_caliber_notes`），表格元数据里的
    # `unit` / `entity_scope` **一字未动**，而两个 `ng-*` 消费点（`_authority_identity_texts`、
    # `TABLE_DISPLAY_LABELS`）**没有任何调用方**，门判定集与门看到的输入都没变。
    # 门版本说的是**判定集**（`narrative_schema` 的版本注释逐版都这么定义），不是渲染字节；
    # 因此 ng-9 如实保留，`pwr-4` 承担这次渲染口径变化。
    # M930-3 写作主链定点批 §一**修订了上面这条结论**：那次的推理（判定集没变、只有渲染变）
    # 在当时成立，前提是「门读的封闭标记集一字未动」。定点批 §一 恰好动了**这个前提**——
    # 高风险封闭标记集新增五组（`nrules-11`），而门第 2/3 条判据读的正是它，于是同一份组织段
    # 在 ng-9 与 ng-10 下可以一通过一被拒。判据实现没改，判定集改了 ⇒ 门版本必须到 ng-10。
    # 同一批 §二 又给第 8 条加了判据 e（接缝必须以分隔标点起头，`nrules-12`）：同一份组织段
    # `A同时，B。` 在 ng-10 下通过、ng-11 下被拒 ⇒ 判定集再次前进（`nrules-12` / `ng-11`）。
    # M930-3 指令三（让正文业务上能读）再给第 8 条加判据 10/11（关系性连接语必须有材料逐字
    # 支持、发行人自述必须有归属，`nrules-13`）：同一份组织段 `A，另一方面，B。`（材料没说过
    # 「另一方面」这个对照）或一条含「核心竞争力」却无归属的句子，在 ng-11 下通过、ng-12 下被拒
    # ⇒ 判定集第三次前进。**仍然只新增，不放宽**：a–e 与第 1–9 条一字未改。
    # M930-3 返修 ④（O-12 的期间纪律在组织侧落地）再加第 12 条（组织语不得把断言挪到当前
    # 时点，`nrules-14`）：同一份组织段 `A，此外，公司目前<B>`（「目前」材料没写过）在 ng-12 下
    # 通过、ng-13 下被拒 ⇒ 判定集第四次前进。**仍然只新增，不放宽**：a–e 与第 1–11 条一字未改。
    # M930-3 指令 E 第 3 项（恢复材料驱动写作 + 最终句保真核对）把第 8 条**按句类分派**：`composed`
    # 仍要求逐字组织（一字未动），`natural`（门前自然草稿句）改走 `verify_sentence_fidelity`
    # （`nrules-15`）。同一份正文 `公司2024年度主营业务收入为X亿元。` 在 ng-13 下因「逐字」被拒、
    # 在 ng-14 的 `natural` 模式下通过 ⇒ 判定集第五次前进。**仍然只新增，不放宽**：判定集只多了一个
    # 句类分支，`factual` 的整句扫描与 1–7、9–12 条一字未改；保真核对的四类缺陷是**另加**的封闭
    # 补语（`SENTENCE_FIDELITY_DEFECTS`），不复用也不改写任何既有标记表。
    # M930-3 定点业务闭环批 §二 2 第六次前进：第 12 条（当前式措辞）的**判据实现一字未改**，
    # 但它所读的封闭标记集新增普遍化组（`一直`/`始终`/`历来`/`向来`/`一向`/`一贯`/`素来`/
    # `从来`）与否定式普遍化组（`从未`/`未曾`）。同一份组织段 `A此外公司一直<B>`（材料没写过
    # 「一直」）在 ng-14 下通过、ng-15 下被拒 ⇒ 判定集第六次前进。**仍然不放宽**：第 1–11 条与
    # 第 12 条的判据本身、以及 `factual` 的整句扫描都一字未改，只换了判定集。
    # M930-3 主营业务质量返修批 §四第七、八次前进：主体名抽取的**判据实现**改了
    # （剥前缀改不动点 + 整串即时间状语的字号不产出 + 「时间状语 + 自称」那一格），
    # 判定集两度收窄 ⇒ `nrules-18` / `ng-17`。
    # **仍然不放宽**：真正错误的主体名照旧命中，`截至报告期末公司`／`报告期末本公司`
    # 这类伪 token 不再产出。
    # M930-3 `ndc-2` 批第九次前进：实现一字未改，动的是**支撑边材料判别集**——同一事实的已核验
    # 输入材料先把候选收窄（`mbind-1`）⇒ 只门前进到 `ng-18`（规则集停在 `nrules-18`）。
    check(NS.NARRATIVE_RULES_VERSION == "nrules-18" and NS.NARRATIVE_GATE_VERSION == "ng-18",
          f"判定集变了必须前进规则集与门版本（实测 {NS.NARRATIVE_RULES_VERSION!r} / "
          f"{NS.NARRATIVE_GATE_VERSION!r}）")
    # M930-3 定点批 §四（财务来源读回）再改了一次渲染字节：段落与表格各多一行 `- 来源：…`
    # （`pwr-5`，口径名与中文期间取自权威自己的公式登记表与期间口径）。判定集**没有**动
    # ——来源是渲染层的读回，不参与任何判据（`ng-12` / `nrules-13` 如实保留），因此版本责任
    # 仍然只落在渲染器上。
    check(PW.WRITER_RENDERER_VERSION == "pwr-5",
          f"§四 改了渲染字节（单位 / 代理口径说明 / 来源行 / 缺口文案），渲染器版本必须承担它"
          f"（实测 {PW.WRITER_RENDERER_VERSION!r}）")
    # 上面这几行说的是**指令三那一轮**：那轮没有新增/删除 wire 字段（判据 10/11 读的都是既有
    # 句子文本与既有 claim_ids，`redundancy_candidates` 只是输入面的线索、处置载体仍是既有
    # claim_dispositions），所以 schema 停在了 narr-6。
    # 指令 E 第 3 项**推翻了这一点**：`SectionDraft` 新增了 `natural_prose_draft`（门前自然草稿
    # 层 + 逐事实原子到候选的映射），这正是 wire 字段的增删 ⇒ 按
    # `DEMO_BACKBONE_IMPLEMENTATION_PLAN.md` §narrative wire 约定必须前进到 `narr-7`，并同时
    # 增一个**只读的单版本 legacy 读回器** `narr-6`（旧载荷不得被新读器静默重解释）。
    # 定点返修 P1-B **再推一次**：草稿单元的出处从一条材料轴拆成两条互斥轴（`source_member_refs`
    # / `source_fact_refs`），草稿层的**形状**又变了 ⇒ marker 再前进一版到 `narr-8`，并把 `narr-7`
    # 也登记为只读单版本 legacy（`narr-7` 载荷带草稿层但只有材料轴，不得被当前读器静默重解释）。
    check(NS.NARRATIVE_SCHEMA_VERSION == "narr-8",
          "草稿出处拆成两条互斥轴必须前进 schema 版本到 narr-8，并保留 narr-7 的只读单版本读回器；"
          "旧载荷不得被当前读器静默接受")
    # 判据 e 的**动因**是 prompt 侧的实测缺陷（组织器把连接语写在接缝开头，缺前置分隔标点），
    # 因此随修订一起改了 prompt 里那一节的样例与措辞：组织器的规则版本必须跟着到 `norg-4`。
    # 判据 10/11 的动因同样是 prompt 侧的实测缺陷（r7b 正文 12 处关系性连接语、6 条发行人自述
    # 零归属），因此 `norg-5` 的资产正文与输入面又各加了对应要求：规则版本必须跟着前进。
    # 返修 ④ 的动因同样是 prompt 侧的实测缺陷（正文把旧年度材料说的产品清单写成无期间当前式
    # 「持续推出」），因此 `norg-6` 的资产正文又加了「不得把断言挪到当前时点」一节：规则版本
    # 必须跟着前进。
    # 指令 E 第 3 项（门后那一半）的动因是**结构**变化而非新的实测措辞缺陷：组织器第一次拿到
    # 门前草稿作为表达基础，输入面多了一份逐原子台账，句类多出系统判定的 `natural`，并新增两条
    # **句级来源闭合**判据（声明草稿出处的句子，其 claim_ids 必须是那一段 `kept` 原子的子集；
    # 未在本轮输入里的 `prose_unit_id` 一律拒）。判定集变了 ⇒ 规则版本必须跟着前进到 `norg-7`：
    # 同一份组织器输出在 `norg-6` 下没有这两条，在 `norg-7` 下可能被拒，两版不得共用版本号。
    check(NO.NARRATIVE_ORGANIZER_RULES_VERSION == "norg-7",
          "改了 prompt 要求（中性并列 + 归属语 + 近义候选逐对裁决 + 不得把断言挪到当前时点 + "
          "以门前草稿为表达基础）就必须前进组织器规则版本（`norg-7`）"
          f"（实测 {NO.NARRATIVE_ORGANIZER_RULES_VERSION!r}）")
    # 指令「打通真实写作请求」P1-d **不动判定集**：它给组织器多投一份**只读**的材料正文依据面
    # （`orgmc-1`），输出面一个键不增、逐条判据一条不改 ⇒ 规则版本**故意**停在 `norg-7`。资产
    # **正文**仍然变了（新增一节写这份依据面能怎么用、不能怎么用），因此资产与修订号必须换：
    # 「登记版本 = 实际加载版本」靠的是新文件，不是就地改字。两面都钉住，免得下次把「输入面变了」
    # 顺手读成「判定集变了」而多推一版规则号，或者反过来就地改字。
    check(NO.NARRATIVE_ORGANIZER_PROMPT_ASSET == "section_narrative_organizer_v4"
          and NO.NARRATIVE_ORGANIZER_PROMPT_REVISION == "norg-8",
          "资产正文变了（新增材料正文依据面一节）就必须换资产与修订号"
          f"（实测 {NO.NARRATIVE_ORGANIZER_PROMPT_ASSET!r}@"
          f"{NO.NARRATIVE_ORGANIZER_PROMPT_REVISION!r}）")
    check((NO.NARRATIVE_ORGANIZER_PROMPT_ASSET, NO.NARRATIVE_ORGANIZER_PROMPT_REVISION)
          != ("section_narrative_organizer_v3", "norg-7"),
          "上一批的登记面（`_v3`@`norg-7`）不得被原地复用：改了正文还叫旧名字 = 两版正文"
          "共用一个身份，旧产物会被读成新口径")
    # 旧资产**留在盘上**且仍自报它自己那一版修订号（这才是「不得原位改写」的可执行含义）。
    for asset, revision in (("section_narrative_organizer_v3", "norg-7"),
                            ("section_narrative_organizer_v2", "norg-6")):
        superseded = LLMC.load_prompt(asset)
        check(f"revision {revision}" in superseded,
              f"旧资产 {asset} 必须原样留在盘上并自报 `revision {revision}`"
              "（被登记取代 ≠ 被改写：历史产物的身份要能被读回）")

    # ============================================================ §4 有界定向重组织（⑥）
    ctx_spec = {"text": container, "claim_ids": [CLAIM_OBJ_A.claim_id, CLAIM_OBJ_B.claim_id],
                "context_binding_ids": ["actx-1", "actx-2"]}
    fixed, records, specs = _reorganize([ctx_spec], CLAIMS)
    check(len(records) == 1 and records[0]["defect"] == NS.COMPOSED_DEFECT_INTERNAL_TERMINATOR,
          f"双句容器必须被记为「句中句末标点」缺陷（实测 {records}）")
    check(records[0]["remedy"] == NO.REORGANIZATION_SPLIT_AT_TERMINATORS
          and records[0]["sentence_count_after"] == 2,
          f"双句容器必须走第一级「按标点切段」（实测 {records}）")
    check(records[0]["context_bindings_dropped"] == 2,
          f"补救不承接 context 绑定，丢弃数必须如实记录（实测 {records[0]}）")

    # 4.1 切段是逐字的：拼回去必须与模型原文**逐字节**相同；衔接语留在它相邻的那一段。
    texts_after = [s["text"] for s in fixed["paragraphs"][0]["sentences"]]
    check("".join(texts_after) == container,
          f"切段必须逐字保留模型原文，禁止改写或补写（实测 {texts_after}）")
    check(texts_after == [CLAIM_A + "。", "同时，" + CLAIM_B + "。"],
          f"模型自己写的衔接语必须留在它原本相邻的那一段（实测 {texts_after}）")
    check([list(s["claim_ids"]) for s in fixed["paragraphs"][0]["sentences"]]
          == [[CLAIM_OBJ_A.claim_id], [CLAIM_OBJ_B.claim_id]],
          "切段后每条 Claim 必须重绑到它自己所在的那一段")
    check(all(s["sentence_kind"] == "composed" and not s["context_binding_ids"]
              for s in specs),
          "切出来的段仍是 composed 句（同一口径），且不带 context 绑定")
    check(not fixed["paragraphs"][0]["sentences"][0].get("sentence_kind"),
          "补救的句类必须写在系统内部键上，不得借模型可见的 `sentence_kind` 表达"
          "（否则模型可以自称 factual 绕过第 8 条）")
    check(_gate_error(_narrative_from_specs(specs)) == "",
          f"重组织后的正文必须能整份过冻结门（实测拒因："
          f"{_gate_error(_narrative_from_specs(specs))[:120]}）")

    # 4.1b 机械拼接（`A，B。`，没有任何句末标点可切）落第二级：各自成一句逐字事实句。
    join_spec = {"text": CLAIM_A + "，" + CLAIM_B + "。",
                 "claim_ids": [CLAIM_OBJ_A.claim_id, CLAIM_OBJ_B.claim_id],
                 "context_binding_ids": []}
    check(NS.composed_organization_defect(
        text=join_spec["text"], authorized_texts=(CLAIM_A, CLAIM_B))
        == NS.COMPOSED_DEFECT_MECHANICAL_JOIN,
        "前置条件：`A，B。` 必须是「机械拼接」缺陷")
    j_fixed, j_records, j_specs = _reorganize([join_spec], CLAIMS)
    check(len(j_records) == 1
          and j_records[0]["remedy"] == NO.REORGANIZATION_PER_CLAIM_FACTUAL,
          f"切不出合法段时必须落第二级「各自成句」（实测 {j_records}）")
    check([s["sentence_kind"] for s in j_specs] == ["factual", "factual"],
          f"第二级必须是 factual 句（实测 {[s['sentence_kind'] for s in j_specs]}）")
    check([s["text"] for s in j_specs]
          == [NS.render_factual_text([CLAIM_A]), NS.render_factual_text([CLAIM_B])],
          f"第二级的正文只能是 Claim 文本的逐字渲染（实测 {[s['text'] for s in j_specs]}）")
    check(all(len(s["claim_ids"]) == 1 for s in j_specs),
          "第二级每条事实句只承载它自己那一条 Claim（单 Claim 句不进多 Claim 计数）")
    check(_gate_error(_narrative_from_specs(j_specs)) == "",
          f"第二级补救后的正文必须能整份过冻结门（实测拒因："
          f"{_gate_error(_narrative_from_specs(j_specs))[:120]}）")

    # 4.2 `claims_not_present_in_order` **不修**：切段变不出声明里没有的 Claim。
    reversed_spec = {"text": CLAIM_B + "同时，" + CLAIM_A + "。",
                     "claim_ids": [CLAIM_OBJ_A.claim_id, CLAIM_OBJ_B.claim_id],
                     "context_binding_ids": []}
    r_fixed, r_records, r_specs = _reorganize([reversed_spec], CLAIMS)
    check(not r_records,
          "声明顺序与实际正文不符的句子不得被「补救」（补救不能凭空造出一条 Claim）")
    check([s["text"] for s in r_specs] == [reversed_spec["text"]],
          "不补救就必须**原样**留下原句（不得顺手改写）")
    check(bool(_gate_error(_narrative_from_specs(r_specs))),
          "该句必须照旧 fail-closed")

    # 4.3 补救只治组织形态：修完仍要过表面门，衔接区自造的表面照样被拒。
    for label, injected in (("完整主体名", "宁德新能源科技有限公司"),
                            ("数字", "同比提升42%"),
                            ("含糊期间", "报告期内")):
        bad_text = CLAIM_A + "。" + "同时，" + injected + "，" + CLAIM_B + "。"
        b_fixed, b_records, b_specs = _reorganize(
            [{"text": bad_text, "claim_ids": [CLAIM_OBJ_A.claim_id, CLAIM_OBJ_B.claim_id],
              "context_binding_ids": []}], CLAIMS)
        check(bool(b_records),
              f"前置条件：含「{label}」的双句容器同样要触发补救（实测 {b_records}）")
        err = _gate_error(_narrative_from_specs(b_specs))
        check(bool(err),
              f"补救**只治组织形态**：衔接区里自造的{label}必须仍被冻结门拒（实测放行）")

    # 4.4 **补救不得洗掉失败**：接缝上是模型**自造的表面**（这里是数字）时，第二级必须被禁止——
    # 丢掉那一段等于把「模型自造了一个数字」洗成一份不含该数字的合法正文；拒掉的版本没有被修好，
    # 只是被洗掉了。这一版必须原样 fail-closed。
    laundered = CLAIM_A + "，42%，" + CLAIM_B + "。"
    check(not NS.is_punctuation_only_join(laundered, (CLAIM_A, CLAIM_B)),
          "前置条件：自造数字的接缝不是纯标点接缝（判据必须看得见模型写的数字）")
    check(NS.composed_organization_defect(text=laundered, authorized_texts=(CLAIM_A, CLAIM_B))
          == NS.COMPOSED_DEFECT_MECHANICAL_JOIN,
          "前置条件：该句在判据层是「机械拼接」")
    l_fixed, l_records, l_specs = _reorganize(
        [{"text": laundered, "claim_ids": [CLAIM_OBJ_A.claim_id, CLAIM_OBJ_B.claim_id],
          "context_binding_ids": []}], CLAIMS)
    check(not l_records and [s["text"] for s in l_specs] == [laundered],
          "含自造表面的接缝不得被补救（补救不得把被拒的一版洗成合法正文）")
    check(bool(_gate_error(_narrative_from_specs(l_specs))),
          "该句必须照旧 fail-closed（补救不是「让它过」）")
    # 同一判据的另一面：模型自己写的组织语（`同时，`）接缝同样禁止第二级（见 §4.3）。

    # ============================================================ §5 不得替系统自报检索 / 核验
    # 接缝带分隔标点：这一节要试的是**第 9 条**（替系统自报），不是判据 e。
    self_claimed = CLAIM_A + "，本系统已独立联网核验，" + CLAIM_B + "。"
    provenance_err = rejects(self_claimed)
    check(bool(provenance_err) and "检索" in provenance_err and "核验" in provenance_err,
          f"正文替系统自报检索 / 核验必须被冻结门拒（实测拒因：{provenance_err[:140]}）")
    check(NS.system_provenance_hits(self_claimed) == ("联网", "核验", "本系统"),
          f"命中的短语必须如实列出（实测 {NS.system_provenance_hits(self_claimed)}）")

    # 5.1 **防修过头**：材料原文就写着这些字时，逐字转录必须放行（不得把词本身当禁词）。
    verbatim_claim = _claim("公司互联网销售渠道已通过核验", index=3)
    verbatim_text = NS.render_factual_text([verbatim_claim.text])
    check(_gate_error(_narrative([(verbatim_text, "factual", (verbatim_claim,))]),
                      claims=CLAIMS + (verbatim_claim,)) == "",
          "Claim 文本里逐字写着「互联网 / 核验」时，正文照转不误（判据拒的是**系统自报**，"
          "不是这些词本身）")

    # 5.2 **防修过头**：含勾选符号的原文转录必须放行——不做「一律删除勾选符号」的粗清洗。
    checkbox_claim = _claim(
        "2）已签订的重大采购合同截至本报告期的履行情况 □适用 √不适用", index=4)
    checkbox_text = NS.render_factual_text([checkbox_claim.text])
    check(_gate_error(_narrative([(checkbox_text, "factual", (checkbox_claim,))]),
                      claims=CLAIMS + (checkbox_claim,)) == "",
          "含勾选符号的**原文转录**必须被放行（判据拒的是「替系统自报检索」，"
          "不是「正文里出现勾选符号」）")

    return {"passed": passed, "failed": failed, "skipped": 0, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

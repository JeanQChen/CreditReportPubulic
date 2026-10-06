"""Eval: §0.20 第二步（后半）—— 独立只读审阅与新的报告版本身份
（`rvi-2` / `rib-2` / `crv-2` / `crpv-2`）。

用法: python -m evals.test_m930_3_cited_review

本模块钉住**判据本身**，不钉措辞、不钉版本字面量：

1. **旧 wire 一字不改**：§7.3 的 `REVIEW_CATEGORIES` / `REVIEW_UNIT_KINDS` 仍是原来的取值；
   `create()` 仍**默认**发射 `rvi-1` / `rib-1`，旧 payload 逐字段还原、身份 id 不变（历史
   run 与验收报告里引用的 `rvi_*` / `rib_*` 因此不会因本扩展而改变）。
2. **句级扩展是版本化的、且不得被塞进旧版**：`rvi-1` 上出现句级字段即拒；`rvi-2` 上缺
   `sentence_id` / `citation_id` 即拒。
3. **两根轴不得自相矛盾**：九类 `semantic_category` 与四值 `category` 的对应关系在构造期
   强校验；`supported` 不得带句义类，非 `supported` 必须带。
4. **审阅不放行、不改稿**：信封层与意见层的放行/改写字段各自被**指名**拒绝，且结果对象上
   根本没有「放行/通过」字段可写。
5. **逐句覆盖等式**：少一句、多一句、同一句两条，各自 fail-closed——「没表态」与「没问题」
   必须可区分。
6. **意见挂在自己的引用上**：`citation_id` 必须是**该句自己**引过的键。
7. **审阅不得覆盖机械硬错误，但它的一票也换不掉硬错误**：机械层判 `hard_error` 的句子收到
   `supported` 时**记下分歧**（`hard_error_override_sentence_ids`）而不是抛错——审阅面是故意
   盲的，看不见机械结论，"不得说 supported"是它无从遵守的规则；真正的保护在聚合层：只要还有
   机械硬错误，③ 轴就**不可能**是 `passed_awaiting_human`。给出**非** supported 的意见照旧
   允许（审阅与硬错误**分列**，谁也不顶替谁）。
7b. **分歧要读得出来，但这份表不进 wire**：`hard_error_divergence_rows()` 一处分歧一行，把
   机械侧的 `check_kind` / `failure_reason` / `surfaces` / `citation_keys` 与审阅侧的
   `citation_id` / `review_reason`（**逐字**）并排。只留一串句 id 时，读者看到"两边不一致"
   也回不到**为什么**不一致那一句上；真实失效形态正是"审阅给了理由，而理由与来源对不上"。
   它**不是** `CitedReviewOutcome` 的字段：两个输入（`issues` 与 `sentence_checks.json`）
   都已落盘，做成字段就要升 `schema_version`，而升版会让**每一份**历史
   `review_issues.json` 都解不出来——含已冻结的演示 run。故 `schema_version` 停在 `crv-3`，
   表由读回时现算，行序与 `hard_error_override_sentence_ids` 都定死；同时**不**立"全绿即
   失败"的粗暴规则——没有机械硬错误时整节全 `supported` 照常解析、零分歧行。
7c. **反向的一支也要读得出来**：`review_only_divergence_rows()` 收「审阅**提出**了意见、而
   机械层 18 条轴**一条都没拦住**」的句子，把审阅侧逐字理由与机械侧的实际判定
   （`pass` / `pass+本轴没判` / 无记录）并排。只印 7b 会让读者把「机械绿」读成「语义通过」——
   而「把发行人自述写成客观结论」「旧年材料当前化」「跨栏拼接」「重要限制遗漏」这几类事，
   确定性判据**看不见**，只能由独立语义审阅提出；机械层必须原样把它们留给审阅。它与 7b
   **不共用**收敛器（行形状不同，硬塞进同一固定键集就只能补空键，会把「本支没有机械轴」
   印成「机械轴为空」），同样不进 wire、`schema_version` 停在 `crv-3`。
8. **来源与隔离**：请求面只含被引到的来源（不是整个 Pack），`excluded_context` 逐项声明；
   bundle 指纹 == 请求面指纹、句子清单逐句对上；只有版本锚真实的请求才被受理。
   事实行的**定位按该事实自己的权威坐标**判：`financial_pack` 用「容器 + fact_id + 引用
   锚点身份」三项坐标（该支的 `locator_ref` / `material_id` / `payload_ref` 本来就都是空，
   不得据此判成「没有定位」），其余 kind 仍是三个通用锚，缺一即 fail-closed。
9. **新的报告版本身份**：版本锚只由写作侧输入派生（因此可被意见引用而不成环）、四个状态
   分列、`publishability` 恒定不可发布、「人工修改」只有展示入口。
10. **逐句标注的预览**：一处硬错误**只**标记那一句，其余句子照旧可读；预览明确写「不可发布、
    未经人工接受」「仅预留展示入口」。
11. **意见的生产者也进身份，且决定 ③ 轴的天花板**：离线诊断回声（本文本自己）无论有没有
    blocking 都停在 `system_review_not_run`，读者面上也**永不**出现
    `system_review_passed_awaiting_human`；只有声明为 `independent_llm_review` 的产出才够格
    写成 `passed_awaiting_human`。
12. **指标表与正文同版**：确定性指标表进 `record_id` 身份体（表一变记录就变，而版本锚不变），
    预览里的表格与它声称的那一版**当场**对账；格引用键越出本节清单、或表不属于本节即拒。
13. **提示词点名的句义类必须落在冻结词表里**：`review_prompt` 的「句义类 → 粗类」对照表与
    `REVIEW_SEMANTIC_CATEGORIES` / `REVIEW_CATEGORY_BY_SEMANTIC` 逐条对账，九个值不多不少；
    另配一对**行为**正反例：词表内的类名照常解析、表外的类名让**整份回复**作废。
    这条守卫来自一次真实失败：一份回复里自造一个类名，赔付的是**整节每一句**的审阅结论
    （`crr-6` → `crr-7`）。
14. **一句引多条来源时，合并证据要读得到**（`crr-8` 的前件）：判据改成「**每一个**所指都要在
    **全部所引来源的合并证据**里找得到」之后，那几条来源的原文必须**真的**进得来。夹具里各句
    只引一条，§8 因此显式造一句引两条的，逐条核到两条来源都在审阅面上、句子行也没被压成第一条。
    这条只证明**判据可执行**；模型会不会照它逐个所指核对，离线证不了。
15. **两级字段不得混填，而且提示词不许把它们并列成二选一**（`crr-11`）：`category`（四粗类）
    与 `semantic_category`（九句义类）是两条正交轴，合法配对由
    `REVIEW_CATEGORY_BY_SEMANTIC` 定死（`off_topic` ⇒ `missing_content`）。§22 钉两件事：
    (a) **行为**——合法的 `missing_content` + `off_topic` + `suggested_target` 照常解析；
    把 `off_topic` 填进 `category`（真实 `m930_3_cited_upload_20261005T161431Z` 公司节第 16 条
    的形状）继续 fail-closed 且**带句子/引用定位**；缺 `suggested_target`、两级名互换、
    空 `category` 都不许被猜成 `supported`。(b) **提示词资产关系**——粗类取值清单行里不得
    出现任何句义类名，且句义类名与粗类名不得以「或」直接并列（原文 7e 那句
    「按 `off_topic` 或 `missing_content` 提意见」正是这个形状）。两段都不钉任何一句措辞。
16. **`contract_aspect` 的 `target_ref` 是栏目身份名，不是那条要求的原文**（`crr-12`）：
    `SuggestedTarget` 的词表把 `contract_aspect` 定义成「指向应该补哪个 Contract aspect」，
    请求面也**已经**把 `aspect_id` 摆在模型眼前（`subsections[].aspect_requirements[].aspect_id`，
    与 `requirement_text` 逐行对位），读者面（`scripts/cited_demo_app.py` 的「建议补到」列）
    同样按身份名在用；但 `crr-11` 的三处占位符写的都是「**那条要求**」。§23 钉四件事：
    (a) 请求面里 `aspect_id` **在场**且逐字等于声明的栏目身份；对位不成立时该表留空
    （所以「取不到身份名」这个分支真实存在，提示词必须给它确定的退路）；
    (b) `target_ref` 填真身份名时**逐字保留**；空串与词表外的 `target_kind` 照旧 fail-closed
    且带句子/引用定位；(c) 提示词里凡出现 `contract_aspect` 的行必须同时点名 `aspect_id`。
    **它不构成一条新的门**：`SuggestedTarget.from_dict` 只校验非空，要求原文与自造名照收。

夹具复用 `test_m930_3_cited_writer` 的真实 `VerifiedPackSet` / 真实 `ResearchMaterial` / 同一
`build_cited_writer_input` 入口；无公司代号、文件名、页码、表号或固定年份的生产字面量。
不调 LLM、不联网、不写库、不建第二套 Harness/Pack/Writer/Reviewer。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assurance import schema as AS                     # noqa: E402
from evals import test_demo_pack_writer as T           # noqa: E402
from evals import test_m930_3_cited_writer as E         # noqa: E402
from sections import cited_financial_table as CFT      # noqa: E402
from sections import cited_report as CRP               # noqa: E402
from sections import cited_review as CR                # noqa: E402
from sections import cited_writer as CW                # noqa: E402
from sections import material_context as MC            # noqa: E402
from sections import narrative_schema as NS            # noqa: E402
from sections import pack_writer as PW                 # noqa: E402
from sections import sentence_check as SC              # noqa: E402

_BODY_A = E._BODY_A
_FACT_TEXT = E._FACT_TEXT
#: 本句写的数字**没有**任何被引来源含它 ⇒ 机械层必须判 `unsourced_number_surface`。
#: 与第三句（由具名权威事实授权同一量纲的数字）在同一份清单上形成正反例。
_NUMBER_TEXT = "报告期内公司主营业务收入为 999.99 万元。"

#: 版本锚由夹具**真实派生**（在 `main()` 里赋值，见 `derive_report_version`）。刻意**不**写
#: 一个常量字面量：意见必须绑在本次真实版本上，写死的锚会让「意见绑错版本」这条检查失效。
_REPORT_VERSION = ""
_REPORT_ID = "rep-cited-1"
_WRITER = "stub-writer"

_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

def _sentences() -> tuple[CW.CitedSentence, ...]:
    """三句夹具：一句干净、一句带**无来源数字**、一句由具名权威事实授权。"""
    return (
        CW.CitedSentence(sentence_id="s1", text=_BODY_A, citations=("m01",)),
        CW.CitedSentence(sentence_id="s2", text=_NUMBER_TEXT, citations=("m02",)),
        CW.CitedSentence(sentence_id="s3", text=_FACT_TEXT, citations=("f01",)),
    )


def _draft(manifest, *, sentences=None, gaps=()) -> CW.CitedProseDraft:
    spec = manifest.subsections[0]
    return CW.CitedProseDraft.create(
        task_id=manifest.task_id, section_id=manifest.section_id,
        input_manifest_id=manifest.manifest_id, writer_identity=_WRITER,
        subsections=(CW.CitedSubsection(
            subsection_id=spec.subsection_id, title=spec.title,
            paragraphs=(CW.CitedParagraph(paragraph_id="p1",
                                          sentences=tuple(sentences or _sentences())),)),),
        gaps=tuple(gaps))


def _gap(manifest) -> CW.CitedProseGap:
    spec = manifest.subsections[0]
    return CW.CitedProseGap.create(
        subsection_id=spec.subsection_id, requirement_text=spec.requirement_text,
        reason="no_source_in_manifest", detail="夹具用：本条链不执行补件")


def _issue_json(sentence_id: str, citation_id: str, *, category: str = "supported",
                semantic: str = "", severity: str = "none", blocking: bool = False,
                reason: str = "该句与其引用一致", evidence=(), target=None) -> dict:
    return {"sentence_id": sentence_id, "citation_id": citation_id, "category": category,
            "semantic_category": semantic, "severity": severity, "blocking": blocking,
            "reason": reason, "evidence_refs": list(evidence), "suggested_target": target}


def _review_json(*rows) -> str:
    return json.dumps({"issues": list(rows)}, ensure_ascii=False)


def _supported_all() -> str:
    """三句全部 `supported`（覆盖等式满足；静态夹具下三句都不含未授权表面之**外**的争议）。"""
    return _review_json(_issue_json("s1", "m01"), _issue_json("s2", "m02"),
                        _issue_json("s3", "f01"))


def _balanced() -> str:
    """真实分工的响应：s1 / s3 干净，s2 引的 m02 **不支持**那句数字。"""
    return _review_json(
        _issue_json("s1", "m01"),
        _issue_json("s2", "m02", category="insufficient", severity="high", blocking=True,
                    semantic="not_supported_by_source", evidence=("m02",),
                    reason="被引材料里没有这个数字"),
        _issue_json("s3", "f01"))


def _issue_rvi2(*, sentence_id: str, citation_id: str, category: str, semantic: str,
                severity: str = "high", blocking: bool = True, reason: str = "有问题",
                evidence=("m01",), target=None) -> AS.ReviewIssue:
    if category == "missing_content" and target is None:
        target = AS.SuggestedTarget(target_kind="contract_aspect", target_ref="req-x")
    return AS.ReviewIssue.create(
        report_version=_REPORT_VERSION,
        unit_ref=AS.ReviewUnitRef.create(unit_kind="citation",
                                         unit_id=citation_id or "?"),
        category=category, severity=severity, blocking=blocking, reason=reason,
        evidence_refs=evidence, suggested_target=target,
        schema_version=AS.REVIEW_ISSUE_SCHEMA_VERSION,
        sentence_id=sentence_id, citation_id=citation_id, semantic_category=semantic)


def _expect(fn, *, token: str, kind: type = Exception) -> str:
    try:
        fn()
    except kind as exc:
        message = str(exc)
        if token and token not in message:
            raise AssertionError(
                f"异常消息里没有 {token!r}（无法据此定位）：{message}") from None
        return message
    raise AssertionError(f"这一路必须 fail-closed，但它通过了（期望含 {token!r}）")


def _expect_as(fn, *, token: str) -> str:
    return _expect(fn, token=token, kind=AS.AssuranceSchemaError)


def _expect_cr(fn, *, token: str) -> str:
    return _expect(fn, token=token, kind=CR.CitedReviewError)


def _parse(text, *, draft, bundle, report_version, check_report=None, **kw):
    """本模块的解析入口：产出者身份在一处声明为**离线诊断回声**。

    夹具里没有 `LlmCitedReviewClient`（本模块不联网、不发真实调用），因此这份意见的身份就是
    `offline_diagnostic_echo`。集中在一处声明，而不是在十几处调用点上各写一遍 —— 少一处漏写
    就少一处误标成独立审阅的机会。

    只有 §9 那条「独立审阅才够格写成 passed」的正例显式改身份：它不是来自换个客户端，而是
    来自**声明另一条路径**，因此在这里显式写出，读者一眼能看出哪一条是替身、哪一条不是。
    """
    return CR.parse_cited_review(
        text, draft=draft, bundle=bundle, report_version=report_version,
        review_producer_kind=kw.pop("review_producer_kind", "offline_diagnostic_echo"),
        check_report=check_report, **kw)


def _expect_crp(fn, *, token: str) -> str:
    return _expect(fn, token=token, kind=CRP.CitedReportError)


def _excerpt(bundle: AS.ReviewInputBundle, citation_id: str) -> AS.ReviewExcerpt:
    for excerpt in bundle.excerpts:
        if excerpt.citation_id == citation_id:
            return excerpt
    raise AssertionError(f"bundle 里没有被引来源 {citation_id!r} 的原文片段")


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

    task, authority = E._task_and_authority()
    facts = E._scan_facts(authority, task)
    manifest = E._input_manifest(authority=authority, facts=facts)
    draft = _draft(manifest, gaps=(_gap(manifest),))
    global _REPORT_VERSION
    _REPORT_VERSION = CRP.derive_report_version(draft=draft, manifest=manifest)
    check_report = SC.check_cited_prose(draft=draft, manifest=manifest)
    request = CR.build_cited_review_request(draft=draft, manifest=manifest)
    bundle = CR.build_cited_review_bundle(
        draft=draft, manifest=manifest, request=request, report_version=_REPORT_VERSION,
        report_id=_REPORT_ID, model_policy_id="mp-stub")
    outcome = _parse(_balanced(), draft=draft, bundle=bundle,
                                    report_version=_REPORT_VERSION,
                                    check_report=check_report)

    # ============================================== §1 旧 wire 保持逐字段兼容
    details.append("## §1 旧 wire 逐字段兼容（历史 id 不变）")
    check(AS.REVIEW_CATEGORIES == ("supported", "contradicted", "insufficient",
                                   "missing_content"),
          "§7.3 的四值粗类词表原样未动（句义类是**另一根轴**，不是往这个词表里塞值）")
    check(AS.REVIEW_UNIT_KINDS == ("section", "paragraph", "table", "row", "claim",
                                   "citation", "locator"),
          "§7.3 的引用轴词表原样未动（句子身份走 `rib-2` 的 `sentence_inventory`）")
    check(AS.REVIEW_SEVERITIES == ("none", "low", "medium", "high", "critical"),
          "严重度词表原样未动")
    old = AS.ReviewIssue.create(
        report_version=_REPORT_VERSION,
        unit_ref=AS.ReviewUnitRef.create(unit_kind="claim", unit_id="c1"),
        category="contradicted", severity="high", blocking=True, reason="与引用矛盾",
        evidence_refs=("e1",))
    check(old.schema_version == "rvi-1" and old.sentence_id == ""
          and old.citation_id == "" and old.semantic_category == "",
          "`create()` 默认仍发射 `rvi-1`：旧调用点的身份不因本次扩展而改变")
    check("sentence_id" not in old.identity_body()
          and "semantic_category" not in old.identity_body(),
          "`rvi-1` 的身份体里**没有**句级字段（身份逐字节不变）")
    check(AS.ReviewIssue.from_dict(old.to_dict()).to_dict() == old.to_dict(),
          "旧 payload 读回逐字段还原")
    old_bundle = AS.ReviewInputBundle.create(
        report_version=_REPORT_VERSION, report_id=_REPORT_ID,
        unit_inventory=(AS.ReviewUnitRef.create(unit_kind="claim", unit_id="c1"),),
        model_policy_id="mp-1", allowed_content_fingerprint=AS.sha256_text("p"))
    check(old_bundle.schema_version == "rib-1" and old_bundle.sentence_inventory == (),
          "`ReviewInputBundle.create()` 默认仍发射 `rib-1`")
    check("sentence_inventory" not in old_bundle.identity_body(),
          "`rib-1` 的身份体里**没有** `sentence_inventory`")
    check(AS.ReviewInputBundle.from_dict(old_bundle.to_dict()).to_dict()
          == old_bundle.to_dict(), "旧 bundle payload 读回逐字段还原")

    # =================================== §2 句级扩展只在 rvi-2 / rib-2 上，且必填
    details.append("## §2 句级扩展只在 `rvi-2` / `rib-2` 上，且必填")
    _expect_as(lambda: AS.ReviewIssue.create(
        report_version=_REPORT_VERSION,
        unit_ref=AS.ReviewUnitRef.create(unit_kind="citation", unit_id="m01"),
        category="insufficient", severity="low", blocking=False, reason="来源不支持",
        schema_version="rvi-1", sentence_id="s1", citation_id="m01",
        semantic_category="not_supported_by_source"),
        token="不接受句级扩展字段")
    smuggled = old.to_dict()
    smuggled["sentence_id"] = "s1"
    _expect_as(lambda: AS.ReviewIssue.from_dict(smuggled), token="含未登记字段")
    _expect_as(lambda: _issue_rvi2(sentence_id="", citation_id="m01",
                                   category="contradicted", semantic="contradiction"),
               token="必须给出 sentence_id")
    _expect_as(lambda: _issue_rvi2(sentence_id="s1", citation_id="",
                                   category="contradicted", semantic="contradiction"),
               token="必须给出 citation_id")
    _expect_as(lambda: _issue_rvi2(sentence_id="s1", citation_id="m01",
                                   category="supported", semantic="off_topic",
                                   severity="none", blocking=False),
               token="semantic_category 必须为空串")
    clean_issue = _issue_rvi2(sentence_id="s1", citation_id="m01", category="supported",
                              semantic="", severity="none", blocking=False)
    check(clean_issue.schema_version == "rvi-2" and clean_issue.semantic_category == ""
          and clean_issue.issue_id.startswith("rvi_"),
          "`rvi-2` 的 supported 条目可构造（正向结果不需要句义类）")
    check(AS.ReviewIssue.from_dict(clean_issue.to_dict()).to_dict() == clean_issue.to_dict(),
          "`rvi-2` payload 读回逐字段还原")
    _expect_as(lambda: AS.ReviewInputBundle.create(
        report_version=_REPORT_VERSION, report_id=_REPORT_ID,
        unit_inventory=(AS.ReviewUnitRef.create(unit_kind="citation", unit_id="m01"),),
        model_policy_id="mp-1", allowed_content_fingerprint=AS.sha256_text("p"),
        schema_version="rib-1", sentence_inventory=("s1",)),
        token="不接受 sentence_inventory")

    # ========================================== §3 粗类 ↔ 句义类不得自相矛盾
    details.append("## §3 粗类 ↔ 句义类：对应关系写死，不接受自相矛盾")
    check(len(AS.REVIEW_SEMANTIC_CATEGORIES) == 9
          and set(AS.REVIEW_CATEGORY_BY_SEMANTIC) == set(AS.REVIEW_SEMANTIC_CATEGORIES),
          "九类句义问题各有一条粗类映射（少一条就会有类别无处安放）")
    for sem, coarse in sorted(AS.REVIEW_CATEGORY_BY_SEMANTIC.items()):
        issue = _issue_rvi2(sentence_id="s1", citation_id="m01", category=coarse,
                            semantic=sem)
        check(issue.semantic_category == sem and issue.category == coarse,
              f"句义类 {sem!r} ⇒ 粗类 {coarse!r} 可构造")
    _expect_as(lambda: _issue_rvi2(sentence_id="s1", citation_id="m01",
                                   category="contradicted",
                                   semantic="not_supported_by_source"),
               token="两根轴不得自相矛盾")
    _expect_as(lambda: _issue_rvi2(sentence_id="s1", citation_id="m01",
                                   category="contradicted", semantic="probably_bad"),
               token="必须属于")
    _expect_as(lambda: _issue_rvi2(sentence_id="s1", citation_id="m01",
                                   category="contradicted", semantic=""),
               token="必须属于")

    # ============================================ §4 不放行、不改稿
    details.append("## §4 审阅不放行、不改稿（结构上做不到）")
    for name in sorted(CR.CITED_REVIEW_FORBIDDEN_ENVELOPE_FIELDS):
        envelope = json.loads(_supported_all())
        envelope[name] = True
        _expect_cr(lambda p=envelope: _parse(
            json.dumps(p, ensure_ascii=False), draft=draft, bundle=bundle,
            report_version=_REPORT_VERSION), token="不放行、不改稿")
    envelope = json.loads(_supported_all())
    envelope["extra"] = 1
    _expect(lambda: _parse(json.dumps(envelope, ensure_ascii=False),
                                          draft=draft, bundle=bundle,
                                          report_version=_REPORT_VERSION),
            token="含未登记字段")
    for name in ("rewritten_text", "final_text", "verdict"):
        row = json.loads(_supported_all())
        row["issues"][0][name] = "改写后的句子"
        _expect(lambda p=row: _parse(json.dumps(p, ensure_ascii=False),
                                                    draft=draft, bundle=bundle,
                                                    report_version=_REPORT_VERSION),
                token="被禁止字段")
    row = json.loads(_supported_all())
    row["issues"][0]["extra"] = 1
    _expect(lambda p=row: _parse(json.dumps(p, ensure_ascii=False),
                                                draft=draft, bundle=bundle,
                                                report_version=_REPORT_VERSION),
            token="含未登记字段")
    for name in ("publishable", "passed", "released", "approved", "decision", "ready"):
        check(not hasattr(CR.CitedReviewOutcome, name),
              f"审阅结果对象上**没有** {name!r} 字段可写（放行不是它的产出）")

    # ============================================ §5 逐句覆盖等式
    details.append("## §5 逐句覆盖等式：没表态与没问题必须可区分")
    clean = _parse(_supported_all(), draft=draft, bundle=bundle,
                                  report_version=_REPORT_VERSION)
    check(clean.sentence_ids == ("s1", "s2", "s3") and len(clean.issues) == 3,
          "三句各一条意见，覆盖等式满足")
    check(clean.sentence_verdicts == (("s1", "supported"), ("s2", "supported"),
                                      ("s3", "supported")),
          "逐句结论按句给出")
    _expect_cr(lambda: _parse(
        _review_json(_issue_json("s1", "m01"), _issue_json("s2", "m02")),
        draft=draft, bundle=bundle, report_version=_REPORT_VERSION),
        token="未逐句表态")
    _expect_cr(lambda: _parse(
        _review_json(_issue_json("s1", "m01"), _issue_json("s2", "m02"),
                     _issue_json("s3", "f01"), _issue_json("s9", "m01")),
        draft=draft, bundle=bundle, report_version=_REPORT_VERSION),
        token="不存在的句子")
    _expect_cr(lambda: _parse(
        _review_json(_issue_json("s1", "m01"), _issue_json("s1", "m01"),
                     _issue_json("s2", "m02"), _issue_json("s3", "f01")),
        draft=draft, bundle=bundle, report_version=_REPORT_VERSION),
        token="逐句表态是「一句一条」")
    shuffled = _parse(
        _review_json(_issue_json("s3", "f01"), _issue_json("s1", "m01"),
                     _issue_json("s2", "m02")),
        draft=draft, bundle=bundle, report_version=_REPORT_VERSION)
    check([i.issue_id for i in shuffled.issues] == [i.issue_id for i in clean.issues],
          "意见按 id 排序 ⇒ 聚合不依赖响应里的顺序（确定性）")

    # ============================================ §6 意见挂在自己的引用上
    details.append("## §6 `citation_id` 必须是该句自己引过的键")
    _expect_cr(lambda: _parse(
        _review_json(_issue_json("s1", "m02"), _issue_json("s2", "m02"),
                     _issue_json("s3", "f01")),
        draft=draft, bundle=bundle, report_version=_REPORT_VERSION),
        token="并没有引用")
    _expect_cr(lambda: _parse(
        _review_json(_issue_json("s1", "m09"), _issue_json("s2", "m02"),
                     _issue_json("s3", "f01")),
        draft=draft, bundle=bundle, report_version=_REPORT_VERSION),
        token="并没有引用")
    check(outcome.blocking_sentence_ids == ("s2",)
          and len(outcome.blocking_issue_ids) == 1
          and outcome.blocking_issue_ids[0] in {
              i.issue_id for i in outcome.issues if i.sentence_id == "s2"},
          "只有 blocking=True 的那句进入 blocking 列表（不因有意见就 blocking）")
    check(dict(outcome.semantic_counts()) == {"not_supported_by_source": 1,
                                              "supported": 2},
          f"句义类计数逐类给出（实测 {dict(outcome.semantic_counts())}）")
    flagged = [i for i in outcome.issues if i.sentence_id == "s2"]
    check(len(flagged) == 1 and flagged[0].citation_id == "m02",
          "意见回指到该句真正引用的那条来源")

    # ======================================= §7 不覆盖机械层已证实的硬错误
    details.append("## §7 审阅不得覆盖机械层已证实的硬错误（两者分列，分歧留档）")
    check(check_report.blocked_sentence_ids == ("s2",)
          and check_report.mechanical_verdict == "has_hard_errors",
          f"机械层只把写无来源数字的那句判成硬错误（实测 "
          f"{check_report.blocked_sentence_ids}）")
    overriding = _parse(_supported_all(), draft=draft, bundle=bundle,
                        report_version=_REPORT_VERSION, check_report=check_report)
    check(overriding.hard_error_override_sentence_ids == ("s2",),
          "审阅对机械硬错误句说 supported ⇒ **记下这条分歧**，不是解析失败"
          f"（实测 {overriding.hard_error_override_sentence_ids}）")
    check(overriding.hard_error_sentence_ids == ("s2",)
          and overriding.sentence_verdicts[1] == ("s2", "supported"),
          "两条轴各自留档：机械层仍判 s2 硬错误，审阅的独立意见也照记（谁也不顶替谁）")
    check(overriding.to_dict()["hard_error_override_sentence_ids"] == ["s2"],
          "分歧进 `to_dict`：读者面能看见「审阅在这里和机械层不一致」")
    _expect_cr(lambda: _parse(_supported_all(), draft=draft, bundle=bundle,
                              report_version=_REPORT_VERSION,
                              check_report=SC.SentenceCheckReport.create(
                                  draft_id="cwd_other", input_manifest_id=manifest.manifest_id,
                                  dependency_fingerprint=manifest.fingerprint(),
                                  sentence_count=0, records=())),
               token="不是本次草稿")
    check(outcome.hard_error_sentence_ids == ("s2",),
          "硬错误句子集随结果记下（审阅与硬错误**分列**，不互相顶替）")
    check(outcome.hard_error_override_sentence_ids == (),
          "没说 supported 的意见不产生分歧记录（分歧是**具体某一句**的事）")
    check(outcome.sentence_verdicts[1] == ("s2", "not_supported_by_source"),
          "对硬错误句给出**非** supported 的意见是允许的（审阅可以同意机械层）")

    # ============================ §7b 分歧必须**可读**：机械轴 × 审阅逐字理由
    details.append("## §7b 分歧逐处展开：机械轴 × 审阅理由（只并列，不裁决）")
    hard_records = {r.sentence_id: r for r in check_report.hard_error_records}
    #: 这份表**不是** `CitedReviewOutcome` 的字段，而是读回时**现算**的派生读数：它的两个输入
    #: （`issues` 与 `sentence_checks.json`）都已落盘。做成字段就得升 `schema_version`，而升版
    #: 会让每一份历史 `review_issues.json` 都解不出来——含已冻结的演示 run。见
    #: `CR.hard_error_divergence_rows` 的 docstring，以及 §19 那条对冻结 run 的实读。
    rows = CR.hard_error_divergence_rows(issues=overriding.issues, check_report=check_report)
    check(len(rows) == 1 and rows[0]["sentence_id"] == "s2",
          f"一处分歧一行（实测 {len(rows)} 行 {[r['sentence_id'] for r in rows]}）")
    _rec = hard_records["s2"]
    check((rows[0]["check_kind"], rows[0]["failure_reason"]) ==
          (_rec.check_kind, _rec.failure_reason),
          "机械侧那一半是**逐字**抄来的轴与原因码（不是转述、不是重判）")
    check(list(rows[0]["surfaces"]) == list(_rec.surfaces),
          "机械表面串也逐字在场：没有它，读者回不到到底哪个数字／哪个词被判的")
    check(rows[0]["review_semantic_category"] == "supported"
          and rows[0]["review_severity"] == "none",
          "审阅侧那一半如实记为 supported/none —— 分歧的定义就是「它说了 supported」")
    check(rows[0]["review_reason"] == "该句与其引用一致",
          "审阅写的**理由逐字**保留：真实失效形态正是「给了一个与来源对不上的理由」，"
          f"只留句 id 就回不到那句理由上（实测 {rows[0]['review_reason']!r}）")
    check(rows[0]["citation_id"] == "m02"
          and list(rows[0]["citation_keys"]) == list(_rec.citation_keys),
          "审阅据以判断的引用键与机械记录里的引用键各自在场（两个键不是一回事时就看得出来）")
    check(tuple(dict.fromkeys(r["sentence_id"] for r in rows))
          == overriding.hard_error_override_sentence_ids,
          "id 读数是行读数的**去重投影**：两份读数不可能各说各话")
    check("hard_error_divergences" not in overriding.to_dict()
          and CR.CITED_REVIEW_SCHEMA_VERSION == "crv-3",
          "分歧行**不进 wire**：`to_dict` 里没有这个键，`schema_version` 停在 `crv-3` —— "
          "否则每一份历史 `review_issues.json` 都会因字段集不合约而解不出来")
    check(CR.hard_error_divergence_rows(issues=outcome.issues, check_report=check_report) == (),
          "给出**非** supported 意见时不产生分歧行（那是普通意见，由 blocking 轴管）")

    #: 行序不得随响应顺序漂移：同一份意见换个写法就是同一处分歧。
    reordered = _parse(_review_json(_issue_json("s3", "f01"), _issue_json("s1", "m01"),
                                    _issue_json("s2", "m02")),
                       draft=draft, bundle=bundle, report_version=_REPORT_VERSION,
                       check_report=check_report)
    check(CR.hard_error_divergence_rows(issues=reordered.issues,
                                        check_report=check_report) == rows
          and reordered.hard_error_override_sentence_ids
          == overriding.hard_error_override_sentence_ids,
          "响应换个顺序 ⇒ 同一处分歧，行序与 id 序都不动（否则读者会以为发生了两次）")

    #: **不是**「一律 supported 就失败」：没有机械硬错误时，整节全 supported 照常解析、零分歧。
    clean_draft = _draft(manifest, sentences=(
        CW.CitedSentence(sentence_id="s1", text=_BODY_A, citations=("m01",)),
        CW.CitedSentence(sentence_id="s2", text=_FACT_TEXT, citations=("f01",))))
    clean_report = SC.check_cited_prose(draft=clean_draft, manifest=manifest)
    clean = _parse(_review_json(_issue_json("s1", "m01"), _issue_json("s2", "f01")),
                   draft=clean_draft,
                   bundle=CR.build_cited_review_bundle(
                       draft=clean_draft, manifest=manifest,
                       request=CR.build_cited_review_request(draft=clean_draft,
                                                             manifest=manifest),
                       report_version=_REPORT_VERSION, report_id=_REPORT_ID,
                       model_policy_id="stub", prompt_version=CR.CITED_REVIEW_PROMPT_VERSION),
                   report_version=_REPORT_VERSION, check_report=clean_report)
    check(clean.hard_error_sentence_ids == ()
          and CR.hard_error_divergence_rows(issues=clean.issues,
                                            check_report=clean_report) == ()
          and clean.hard_error_override_sentence_ids == (),
          "没有机械硬错误时，全 supported 不产生任何分歧行 —— **不**立「全绿即失败」的粗暴规则")

    #: 行字段不合约即拒：读者面按固定键集摊开这张表，缺键／多键会让某一列悄悄消失。
    _expect_cr(lambda: CR._normalize_divergences([{**rows[0], "extra_key": 1}]),
               token="行字段不合约")
    check(True, "行里多一个键 ⇒ 拒（不猜、不丢，固定键集是这张表可读的前提）")
    _expect_cr(lambda: CR._normalize_divergences([{k: v for k, v in rows[0].items()
                                                   if k != "review_reason"}]),
               token="行字段不合约")
    check(True, "行里少一个键 ⇒ 拒（少了「审阅逐字理由」这一格，这张表就白做了）")
    _expect_cr(lambda: CR._normalize_divergences(["不是对象"]),
               token="每一行必须是对象")
    check(True, "行不是对象 ⇒ 拒（不把字符串硬塞进固定键集）")

    # ============ §7c 反向的一支：**审阅提出、机械层没拦住**的语义意见（逐处）
    details.append("## §7c 审阅提出、机械层没拦住的语义意见（机械绿 ≠ 语义通过）")
    #: 7b 只覆盖「机械说有错、审阅说 supported」。反向那一半同样必须**读得出来**：18 条轴
    #: 里没有一条判「公司自述被写成客观结论」「旧年材料当前化」「跨栏拼接」「重要限制遗漏」，
    #: 这些**只能**由独立语义审阅提出。读回若只印 7b，读者就会把「机械绿」当成「语义通过」。
    _rev_only_issues = _parse(
        _review_json(_issue_json("s1", "m01", category="contradicted", severity="high",
                                 blocking=True, semantic="stale_material_as_current",
                                 evidence=("m01",),
                                 reason="用上一期材料叙述当前状态"),
                     _issue_json("s2", "f01")),
        draft=clean_draft,
        bundle=CR.build_cited_review_bundle(
            draft=clean_draft, manifest=manifest,
            request=CR.build_cited_review_request(draft=clean_draft, manifest=manifest),
            report_version=_REPORT_VERSION, report_id=_REPORT_ID,
            model_policy_id="stub", prompt_version=CR.CITED_REVIEW_PROMPT_VERSION),
        report_version=_REPORT_VERSION, check_report=clean_report)
    check(_rev_only_issues.hard_error_sentence_ids == (),
          "前提：这份草稿在机械层**没有**任何硬错误（机械绿）")
    ro_rows = CR.review_only_divergence_rows(issues=_rev_only_issues.issues,
                                             check_report=clean_report)
    check(len(ro_rows) == 1 and ro_rows[0]["sentence_id"] == "s1",
          f"机械层没判错的句子上，审阅的语义意见**照样**进表（实测 "
          f"{[r['sentence_id'] for r in ro_rows]}）")
    check(ro_rows[0]["review_semantic_category"] == "stale_material_as_current"
          and ro_rows[0]["review_blocking"] is True,
          "语义类与阻断位逐字保留：**只有审阅能提出**的那一类问题不得被机械层吞掉")
    check(ro_rows[0]["mechanical_verdicts"] == ["pass"],
          "机械侧如实记为 pass —— 这正是本表存在的理由：机械绿**不等于**语义通过，"
          "两件事必须能同时读到")
    check("s2" not in [r["sentence_id"] for r in ro_rows],
          "`supported` 意见不进本表（那不是意见，是「没问题」）")
    check(CR.review_only_divergence_rows(
              issues=overriding.issues, check_report=check_report) == (),
          "机械层已判硬错误的句子归 7b 那一支，**不**在 7c 里重复出现"
          "（两边都印，读者会以为有两处不同的问题）")
    check(CR.review_only_divergence_rows(issues=clean.issues,
                                         check_report=clean_report) == (),
          "全 supported 时 7c 为空 —— 空表是「审阅没提这类问题」，不是「正文没问题」")
    #: 7c 的行**不是** 7b 的形状：硬塞进 `_DIVERGENCE_FIELDS` 就只能补空键，那会把
    #: 「这一支没有机械轴」印成「机械轴为空」。两张表、两个收敛器。
    _expect_cr(lambda: CR._normalize_review_only_rows([{**ro_rows[0], "extra_key": 1}]),
               token="行字段不合约")
    check(True, "7c 行多一个键 ⇒ 拒（固定键集，值与 7b 不同不共用收敛器）")
    _expect_cr(lambda: CR._normalize_review_only_rows(
                   [{k: v for k, v in ro_rows[0].items() if k != "mechanical_verdicts"}]),
               token="行字段不合约")
    check(True, "7c 行少「机械侧判定」这一格 ⇒ 拒（少了它这张表就答不出「机械层拦没拦住」）")
    check("review_only_divergences" not in _rev_only_issues.to_dict()
          and CR.CITED_REVIEW_SCHEMA_VERSION == "crv-3",
          "7c 同样是**读回时现算**的派生读数：不进 wire、`schema_version` 不动 —— "
          "做成字段会让每一份历史 `review_issues.json` 解不出来")
    _ro_reordered = _parse(
        _review_json(_issue_json("s2", "f01"),
                     _issue_json("s1", "m01", category="contradicted", severity="high",
                                 blocking=True, semantic="stale_material_as_current",
                                 evidence=("m01",),
                                 reason="用上一期材料叙述当前状态")),
        draft=clean_draft,
        bundle=CR.build_cited_review_bundle(
            draft=clean_draft, manifest=manifest,
            request=CR.build_cited_review_request(draft=clean_draft, manifest=manifest),
            report_version=_REPORT_VERSION, report_id=_REPORT_ID,
            model_policy_id="stub", prompt_version=CR.CITED_REVIEW_PROMPT_VERSION),
        report_version=_REPORT_VERSION, check_report=clean_report)
    check(CR.review_only_divergence_rows(issues=_ro_reordered.issues,
                                         check_report=clean_report) == ro_rows,
          "响应换个顺序 ⇒ 同一张 7c，行序不漂移")

    # ============================================ §8 请求面与隔离
    details.append("## §8 请求面只含被引来源、逐项声明排除、指纹对账")
    cited_keys = [row["citation_id"] for row in request.payload["sources"]]
    check(cited_keys == ["m01", "m02", "f01"],
          f"只给**被引到**的来源（实测 {cited_keys}；不是把整个材料清单倒进去）")
    #: `crr-3`：材料来源行带**来源文档身份**，逐条与清单本体相同——「判当前态先认来源文档」
    #: （第 7b 条）要可执行，审阅者就得在来源面看得见「这句话引的是哪一份文档」；取值仍取自
    #: **同一**字段来源（`cited_writer` 的 `document_id`），复核面拿到的与作者拿到的同一串身份。
    _mat_rows = [s for s in request.payload["sources"] if s["axis"] == "material"]
    check(all("document_id" in s for s in _mat_rows)
          and [s["document_id"] for s in _mat_rows]
          == [manifest.material_for_key(s["citation_id"]).document_id for s in _mat_rows],
          "材料来源行带来源文档身份，且与清单**同一字段**（逐条相同）")
    check([s["sentence_id"] for s in request.payload["sentences"]] == ["s1", "s2", "s3"]
          and request.payload["sentences"][1]["citations"] == ["m02"],
          "逐句给出正文原文与该句自己的引用")
    check(request.payload["subsections"][0]["requirement_text"]
          == manifest.subsections[0].requirement_text,
          "小节要求文本逐字取自输入（判「跑题」的依据就是它）")
    #: `crr-6`：审阅要判「答非所问」（off_topic 的栏目口径），就得看到「这一段**声称**答哪几栏」
    #: 与「那几栏**各自**要求什么」。两个字段都取自草稿 / 清单，不是审阅者的判断。
    check(all("paragraph_aspect_ids" in row for row in request.payload["sentences"])
          and request.payload["sentences"][0]["paragraph_aspect_ids"]
          == list(draft.subsections[0].paragraphs[0].aspect_ids),
          "逐句带出**所在段落自己声明**的栏目（`paragraph_aspect_ids`），取自草稿本体"
          "（本夹具的段落未声明栏目 ⇒ 是空数组，不是缺这个键）")
    _declared = CW.CitedProseDraft.create(
        task_id=manifest.task_id, section_id=manifest.section_id,
        input_manifest_id=manifest.manifest_id, writer_identity=_WRITER,
        subsections=(CW.CitedSubsection(
            subsection_id=manifest.subsections[0].subsection_id,
            title=manifest.subsections[0].title,
            paragraphs=(CW.CitedParagraph(
                paragraph_id="p1", aspect_ids=manifest.subsections[0].declared_aspect_ids,
                sentences=(CW.CitedSentence("s1", _BODY_A, ("m01",)),)),)),))
    _declared_req = CR.build_cited_review_request(draft=_declared, manifest=manifest)
    check(_declared_req.payload["sentences"][0]["paragraph_aspect_ids"]
          == list(manifest.subsections[0].declared_aspect_ids),
          "**正例**：段落声明了栏目时，审阅面逐字读得到那几栏（判「答非所问」的唯一依据）")
    _pairs = CR._aspect_requirement_pairs(manifest.subsections[0])
    check([p["aspect_id"] for p in _pairs] == list(manifest.subsections[0].declared_aspect_ids)
          and [p["requirement_text"] for p in _pairs]
          == [request.payload["subsections"][0]["requirement_text"]],
          "栏目→要求对位表按 Contract 次序逐条给出（本夹具一小节一栏一要求）")
    _two = CW.CitedSubsectionSpec(
        subsection_id="sub-two", title="两栏",
        requirement_text="甲要求\n乙要求",
        declared_aspect_ids=("a.one", "a.two"))
    check([(p["aspect_id"], p["requirement_text"])
           for p in CR._aspect_requirement_pairs(_two)]
          == [("a.one", "甲要求"), ("a.two", "乙要求")],
          "**正例**：多栏小节的两串逐条对位（次序取自 Contract，不按字典序）")
    #: `crr-8`：判据改成「**每一个**所指都要在**全部所引来源的合并证据**里找得到」之后，
    #: 前提是那几条来源的原文**真的都进得来**。夹具里各句只引一条，这里显式造一句引两条的。
    _two_src = CW.CitedProseDraft.create(
        task_id=manifest.task_id, section_id=manifest.section_id,
        input_manifest_id=manifest.manifest_id, writer_identity=_WRITER,
        subsections=(CW.CitedSubsection(
            subsection_id=manifest.subsections[0].subsection_id,
            title=manifest.subsections[0].title,
            paragraphs=(CW.CitedParagraph(
                paragraph_id="p1",
                sentences=(CW.CitedSentence("s1", _BODY_A, ("m01", "m02")),)),)),))
    _two_req = CR.build_cited_review_request(draft=_two_src, manifest=manifest)
    _two_keys = [row["citation_id"] for row in _two_req.payload["sources"]]
    check(_two_keys == ["m01", "m02"],
          f"**正例**：一句引两条来源时，两条的原文**都**进审阅面（实测 {_two_keys}）——"
          "「按全部所引来源的合并证据逐个所指核对」因此是可执行的")
    check(_two_req.payload["sentences"][0]["citations"] == ["m01", "m02"],
          "⇒ 句子行保留该句**自己的全部**引用键，不被压成第一条"
          "（压掉了，判据就只剩半张证据）")
    _short = CW.CitedSubsectionSpec(
        subsection_id="sub-short", title="对不上",
        requirement_text="只有一行要求",
        declared_aspect_ids=("a.one", "a.two"))
    check(CR._aspect_requirement_pairs(_short) == (),
          "**反例**：两串长度不等时**不猜**——对位表留空，退回小节级 `requirement_text`，"
          "而不是把某一栏硬配到某一行要求上")
    check(request.payload["uncited_sentence_ids"] == [],
          "本节句句带引用 ⇒ 排除集为空（这个键**始终存在**：空集与「没这个检查」在读者面可区分）")
    mixed = CW.CitedProseDraft.create(
        task_id=manifest.task_id, section_id=manifest.section_id,
        input_manifest_id=manifest.manifest_id, writer_identity=_WRITER,
        subsections=(CW.CitedSubsection(
            subsection_id=manifest.subsections[0].subsection_id,
            title=manifest.subsections[0].title,
            paragraphs=(CW.CitedParagraph(paragraph_id="p1", sentences=(
                CW.CitedSentence("s1", _BODY_A, ("m01",)),
                CW.CitedSentence("s9", "本次未取得合格材料。", ()))),)),))
    mixed_request = CR.build_cited_review_request(draft=mixed, manifest=manifest)
    check([s["sentence_id"] for s in mixed_request.payload["sentences"]] == ["s1"]
          and mixed_request.payload["uncited_sentence_ids"] == ["s9"],
          "无引用句被挡在审阅对象之外并**逐句具名**声明：审阅单元是「一句话 + 它引的来源」，"
          "零引用句连 `rvi-2` 要求的合法 `citation_id` 都不存在，给它出意见本身就是非法输入")
    check("本次未取得合格材料。" not in NS.canonical_json(mixed_request.payload),
          "无引用句的**正文**不送给审阅者：不给他一句他无权评价、也评价不了的话（可读内容不含它）")
    # 给无引用句出意见是**结构性**非法：`rvi-2` 要求 `citation_id` 取自该句自己的引用集，
    # 而这一集是空的 —— 审阅单元根本不存在。异常消息指向的正是这个缺失，而不是"措辞不合规"。
    _expect_cr(lambda: _parse(_review_json(_issue_json("s9", "m01")), draft=mixed,
                              bundle=CR.build_cited_review_bundle(
                                  draft=mixed, manifest=manifest, request=mixed_request,
                                  report_version=_REPORT_VERSION, report_id=_REPORT_ID,
                                  model_policy_id="mp-stub"),
                              report_version=_REPORT_VERSION),
               token="它并没有引用")
    check(tuple(request.payload["excluded_context"]) == AS.REQUIRED_EXCLUDED_CONTEXT,
          "§7.5 的五类禁止来源逐项声明（独立性靠输入，不靠口头承诺）")
    check(bundle.allowed_content_fingerprint == request.fingerprint
          and bundle.sentence_inventory == ("s1", "s2", "s3"),
          "bundle 的可读内容指纹 == 请求面指纹，句子清单逐句对上")
    check(sorted(bundle.unit_inventory, key=lambda u: u.key) == list(bundle.unit_inventory)
          and {u.unit_id for u in bundle.unit_inventory} == set(cited_keys)
          and all(u.unit_kind == "citation" for u in bundle.unit_inventory),
          "审核单元与来源单元一一对应（bundle 按单元键升序，供确定性聚合）")
    check(_excerpt(bundle, "m01").text == manifest.material_for_key("m01").reading_view,
          "审阅读到的材料字符串与写作看到的**同一串字节**（复核面不能被喂另一份东西）")
    check(_excerpt(bundle, "f01").text == manifest.fact_for_key("f01").text,
          "事实行给的是具名权威事实的**命题文本**，不是被伪装的普通材料")
    bundle_other = CR.build_cited_review_bundle(
        draft=draft, manifest=manifest, request=request, report_version=_REPORT_VERSION,
        report_id=_REPORT_ID, model_policy_id="mp-other")
    check(bundle_other.bundle_id != bundle.bundle_id,
          "model_policy 进身份：换模型策略就是另一次审阅，不是同一次")
    _expect_cr(lambda: CR.build_cited_review_bundle(
        draft=draft, manifest=manifest, request=request, report_version="",
        report_id=_REPORT_ID, model_policy_id="mp"), token="必须绑定一个**真实**")
    spec = manifest.subsections[0]
    stranded = CW.CitedProseDraft.create(
        task_id=manifest.task_id, section_id=manifest.section_id,
        input_manifest_id="cwm_other", writer_identity=_WRITER,
        subsections=(CW.CitedSubsection(
            subsection_id=spec.subsection_id, title=spec.title,
            paragraphs=(CW.CitedParagraph(paragraph_id="p1", sentences=(
                CW.CitedSentence("s1", _BODY_A, ("m01",)),)),)),))
    _expect_cr(lambda: CR.build_cited_review_request(draft=stranded, manifest=manifest),
               token="不是本次清单")
    # 反例：一条三锚全无的事实 ⇒ 审阅面 fail-closed，**不**替它编一个位置；而只带
    # `material_id` / `payload_ref` 权威锚的事实（夹具里那条就是）必须可审——两层的
    # 「能不能按位置读回」是**同一**判据（见 `_fact_location`）。
    import dataclasses as _dc
    anchorless = CW.CitedFactEntry(
        citation_key="f01", authority_kind="topic_pack", container_identity="c-x",
        fact_id="f-x", text=_FACT_TEXT, topic_id=manifest.materials[0].topic_id,
        aspect_ids=(), fact_type="numeric", period="2025年度", scope="合并",
        required=False)
    check(anchorless.locator_ref is None and anchorless.material_id is None
          and anchorless.payload_ref is None, "反例事实确实三个锚全无")
    bare_manifest = CW.CitedWriterInputManifest.create(**{
        name: getattr(manifest, name) for name in
        {f.name for f in _dc.fields(manifest)}
        if name not in ("manifest_id", "materials", "facts")},
        materials=(), facts=(anchorless,))
    bare_draft = CW.CitedProseDraft.create(
        task_id=bare_manifest.task_id, section_id=bare_manifest.section_id,
        input_manifest_id=bare_manifest.manifest_id, writer_identity=_WRITER,
        subsections=(CW.CitedSubsection(
            subsection_id=spec.subsection_id, title=spec.title,
            paragraphs=(CW.CitedParagraph(paragraph_id="p1", sentences=(
                CW.CitedSentence("s1", _FACT_TEXT, ("f01",)),)),)),))
    bare_request = CR.build_cited_review_request(draft=bare_draft,
                                                 manifest=bare_manifest)
    check(bare_request.payload["sources"][0]["locator"] == "",
          "三锚全无的事实在请求面里就是空位置（不编一个「见某处」）")
    _expect_cr(lambda: CR.build_cited_review_bundle(
        draft=bare_draft, manifest=bare_manifest, request=bare_request,
        report_version=_REPORT_VERSION, report_id=_REPORT_ID, model_policy_id="mp"),
        token="既没有定位、也没有可回查的权威锚")
    # 该事实的定位必须由**它自己携带的锚**派生（不拿 `_fact_location` 的输出与它自身比较：
    # 那种写法在对任何实现都恒真，证明不了任何事）。
    f01_entry = manifest.fact_for_key("f01")
    f01_loc = _excerpt(bundle, "f01").source_locator
    _f01_anchors = [
        str(f01_entry.material_id or ""),
        NS.canonical_json(dict(f01_entry.payload_ref)) if f01_entry.payload_ref else "",
        NS.canonical_json(dict(f01_entry.locator_ref)) if f01_entry.locator_ref else ""]
    check(bool(f01_loc) and any(a and a in f01_loc for a in _f01_anchors),
          "只带权威锚的事实照样给出可回查位置，且位置由该事实自己携带的锚派生")

    # ---- §8.1 真实形状的财务事实：按**该权威自己的坐标**回查 --------------------
    # 财务 artifact 的事实 `locator_ref` / `material_id` / `payload_ref` **本来就都是空**；
    # 它的定位是「容器身份 + fact_id + 引用锚点身份」三项坐标。只认三个通用锚，就等于把
    # 每一条合格财务事实永久判成「没有定位」——整节无法进入独立审阅。
    fin_task = T._task("financial", (T.TOPIC_FIN_SOLVENCY,), task_id="task-rev-fin",
                       title="偿债能力")
    fin_artifact = T._Artifact(task_id=fin_task.task_id, facts=(
        T._FinFact(fact_id="ff-current-ratio", label="流动比率", display="1.20 倍",
                   period=T.REPORT_AS_OF, unit="倍",
                   citation={"ref_type": "structured", "snapshot_id": T.SNAPSHOT_ID}),))
    fin_authority = T._financial_authority(
        fin_task, fin_artifact, note_gap=T._NoteGap(task_id=fin_task.task_id))
    fin_scan = PW.scan_financial(fin_authority, fin_task)
    check(len(fin_scan.facts) == 1,
          f"财务权威的扫描读视图恰一条事实（实测 {len(fin_scan.facts)} 条）")
    fin_entry = CW.CitedFactEntry.from_authority_fact(fin_scan.facts[0], citation_key="f01")
    check(fin_entry.locator_ref is None and fin_entry.material_id is None
          and fin_entry.payload_ref is None,
          "真实财务事实的三个通用锚**本来就都是空**：它不能被当成「没有定位」")
    # 期望坐标**独立**写出：容器取该 artifact 自己的身份，来源身份由该事实自己的
    # `CitationRef` 派生（不读 `_fact_location`，也不读清单里那个 `source_identity`）。
    fin_source_identity = NS.citation_source_identity(
        NS.citation_from_mapping(fin_artifact.facts[0].citation))
    expected_coord = {"anchor_kind": "financial_pack_coordinate",
                      "container_identity": fin_artifact.artifact_id,
                      "fact_id": str(fin_artifact.facts[0].fact_id),
                      "source_identity": fin_source_identity}
    check(fin_entry.container_identity == fin_artifact.artifact_id
          and fin_entry.fact_id in {str(f.fact_id) for f in fin_artifact.facts},
          "该事实行确实对应原 artifact 的那一条 fact（容器与 fact_id 都取自该 artifact）")
    fin_spec = CW.CitedSubsectionSpec(subsection_id="sub-solvency", title="偿债能力",
                                      requirement_text="列示报告期内主要偿债能力指标",
                                      declared_aspect_ids=("fin_solvency.short_term_solvency",))
    fin_manifest = CW.build_cited_writer_input(
        authority=fin_authority,
        material_context=MC.empty_material_context_for_authority(fin_authority),
        subsections=(fin_spec,), facts=fin_scan.facts, section_title="偿债能力")
    fin_draft = CW.CitedProseDraft.create(
        task_id=fin_manifest.task_id, section_id=fin_manifest.section_id,
        input_manifest_id=fin_manifest.manifest_id, writer_identity=_WRITER,
        subsections=(CW.CitedSubsection(
            subsection_id=fin_spec.subsection_id, title=fin_spec.title,
            paragraphs=(CW.CitedParagraph(paragraph_id="p1", sentences=(
                CW.CitedSentence("s1", fin_entry.text, ("f01",)),)),)),))
    fin_request = CR.build_cited_review_request(draft=fin_draft, manifest=fin_manifest)
    check(json.loads(fin_request.payload["sources"][0]["locator"]) == expected_coord,
          "正例：请求面给出**容器 + fact_id + 引用锚点身份**的权威坐标"
          "（三项齐备，不是伪造的 `loc-1`）")
    fin_bundle = CR.build_cited_review_bundle(
        draft=fin_draft, manifest=fin_manifest, request=fin_request,
        report_version=_REPORT_VERSION, report_id=_REPORT_ID, model_policy_id="mp")
    check(json.loads(_excerpt(fin_bundle, "f01").source_locator) == expected_coord,
          "同一坐标原样进入 `ReviewExcerpt`（审阅意见因此可回指到这条权威事实）")
    # 反例：缺引用锚点身份 ⇒ 三项坐标不成立，仍 fail-closed（不退回「随便给个位置」）。
    fin_lossy = CW.CitedWriterInputManifest.create(**{
        name: getattr(fin_manifest, name)
        for name in {f.name for f in _dc.fields(fin_manifest)}
        if name not in ("manifest_id", "facts")},
        facts=(_dc.replace(fin_entry, source_identity=""),))
    fin_lossy_draft = CW.CitedProseDraft.create(
        task_id=fin_lossy.task_id, section_id=fin_lossy.section_id,
        input_manifest_id=fin_lossy.manifest_id, writer_identity=_WRITER,
        subsections=(CW.CitedSubsection(
            subsection_id=fin_spec.subsection_id, title=fin_spec.title,
            paragraphs=(CW.CitedParagraph(paragraph_id="p1", sentences=(
                CW.CitedSentence("s1", fin_entry.text, ("f01",)),)),)),))
    fin_lossy_request = CR.build_cited_review_request(draft=fin_lossy_draft,
                                                      manifest=fin_lossy)
    check(fin_lossy_request.payload["sources"][0]["locator"] == "",
          "反例：缺引用锚点身份 ⇒ 坐标不成立，请求面就是空位置")
    _expect_cr(lambda: CR.build_cited_review_bundle(
        draft=fin_lossy_draft, manifest=fin_lossy, request=fin_lossy_request,
        report_version=_REPORT_VERSION, report_id=_REPORT_ID, model_policy_id="mp"),
        token="既没有定位、也没有可回查的权威锚")

    # ============================================ §9 报告版本身份
    details.append("## §9 新的报告版本身份：四个状态分列，发布资格恒定")
    version = CRP.build_cited_report_version(
        draft=draft, manifest=manifest, check_report=check_report,
        review_outcome=outcome, report_id=_REPORT_ID, required_gap_count=1)
    check(version.report_version == CRP.derive_report_version(draft=draft,
                                                              manifest=manifest),
          "版本锚由写作侧输入派生（因此可被意见引用而不成环）")
    check(all(i.report_version == version.report_version for i in outcome.issues),
          "每一条意见都绑在**同一个**真实版本锚上")
    check(version.publishability == "not_publishable",
          "发布资格恒定 `not_publishable`（正式发布另走系统放行与人工接受）")
    check(version.human_edit_mode == "display_entry_only",
          "「人工修改」只有展示入口一档")
    check((version.preview_state, version.mechanical_state, version.system_review_state,
           version.human_review_state) == ("draft_previewable", "has_hard_errors",
                                           "system_review_not_run",
                                           "human_not_reviewed"),
          f"四个状态各自如实（实测 {version.preview_state}/{version.mechanical_state}/"
          f"{version.system_review_state}/{version.human_review_state}）")
    check(version.review_producer_kind == "offline_diagnostic_echo",
          "意见的**生产者**也进身份：离线回声与真实独立审阅不是同一份产出")
    check(version.system_review_state == "system_review_not_run",
          "离线回声**不得**把状态推到「审过」：本文本的 ③ 轴只能是 not_run")
    check(version.release_blockers() == ("hard_errors_present", "review_not_completed",
                                         "blocking_review_issues", "required_gaps_present",
                                         "human_not_reviewed"),
          f"「若要发布还差什么」逐条列出（实测 {version.release_blockers()}）")
    check(version.sentence_count == 3 and version.blocking_sentence_count == 1
          and version.blocking_issue_count == 1 and version.required_gap_count == 1,
          "计数逐项由真实产物读出（不靠调用方报数）")
    check(CRP.CitedReportVersion.from_dict(version.to_dict()).to_dict()
          == version.to_dict(), "版本记录 `to_dict` → `from_dict` 逐字段还原")

    def _tweak(record, **changes) -> dict:
        kwargs = {name: getattr(record, name)
                  for name in CRP.CitedReportVersion.__dataclass_fields__}
        kwargs.update(changes)
        return kwargs

    _expect_crp(lambda: CRP.CitedReportVersion(**_tweak(version, publishability="publishable")),
                token="不得取")
    _expect_crp(lambda: CRP.CitedReportVersion(**_tweak(version, human_edit_mode="editable")),
                token="只预留展示入口")
    _expect_crp(lambda: CRP.CitedReportVersion(
        **_tweak(version, review_outcome_recorded=False,
                 system_review_state="system_review_not_passed",
                 review_producer_kind="independent_llm_review")),
        token="必须记下审阅产出实例")
    _expect_crp(lambda: CRP.CitedReportVersion(
        **_tweak(version, human_review_state="human_reviewed",
                 system_review_state="system_review_passed_awaiting_human")),
        token="不得存在 blocking 意见")
    _expect_crp(lambda: CRP.CitedReportVersion(
        **_tweak(version, preview_state="preview_unavailable")), token="不得有正文句")
    tampered = version.to_dict()
    tampered["blocking_sentence_count"] = 0
    _expect_crp(lambda: CRP.CitedReportVersion.from_dict(tampered), token="与内容不符")

    # ============================================ §9a 历史发布期：写侧仍拒，只读另开一条
    details.append("## §9a 历史发布期记录：写侧 fail-closed，只读入口另开一条")

    def _rekey(record, *, schema_version: str, policy_version: str) -> dict:
        """按**给定**发布期重算两个身份——历史文件当年就是这么算的（不是今天的形状）。"""
        names = CRP.CitedReportVersion.__dataclass_fields__
        fields = {n: getattr(record, n) for n in names}
        fields.update(schema_version=schema_version, policy_version=policy_version)
        obj = object.__new__(CRP.CitedReportVersion)
        for name in names:
            object.__setattr__(obj, name, fields[name])
        fields["record_id"] = CRP.derive_report_record_id(obj)
        obj2 = object.__new__(CRP.CitedReportVersion)
        for name in names:
            object.__setattr__(obj2, name, fields[name])
        return {"record_id": fields["record_id"],
                "record_fingerprint": obj2.fingerprint(), **obj.identity_body()}

    check(bool(CRP.CITED_REPORT_LEGACY_WIRE_VERSIONS)
          and all((sv, pv) != (CRP.CITED_REPORT_VERSION_SCHEMA_VERSION,
                               CRP.CITED_REPORT_POLICY_VERSION)
                  for sv, pv in CRP.CITED_REPORT_LEGACY_WIRE_VERSIONS),
          "只读表**不得**混进当前发布期：写侧常量与可解码的历史期是两回事"
          "（只升政策版本、字段集合没动的那些期**应当**可解，哪怕 schema 串相同）")
    for _sv, _pv in CRP.CITED_REPORT_LEGACY_WIRE_VERSIONS:
        hist = _rekey(version, schema_version=_sv, policy_version=_pv)
        _expect_crp(lambda h=hist: CRP.CitedReportVersion.from_dict(h), token="必须为")
        decoded = CRP.CitedReportVersion.from_legacy_dict(hist)
        check(decoded.schema_version == _sv and decoded.policy_version == _pv
              and decoded.to_dict() == hist,
              f"`{_sv}`/`{_pv}`：只读解出后版本串仍是文件里写的那个，且逐字段吐回原文件")
        check(decoded.fingerprint() == hist["record_fingerprint"],
              f"`{_sv}`/`{_pv}`：指纹在**旧政策下**复算（不重算旧身份）")
        _expect_crp(lambda h=dict(hist, record_fingerprint="0" * 64):
                    CRP.CitedReportVersion.from_legacy_dict(h), token="record_fingerprint")
    out_of_table = _rekey(version, schema_version="crpv-2", policy_version="crpp-2")
    try:
        CRP.CitedReportVersion.from_legacy_dict(out_of_table)
    except CRP.CitedReportError as exc:
        check(exc.reason == "legacy_wire_version_unknown",
              "表外发布期给 typed 原因 `legacy_wire_version_unknown`，不静默降级成「解开了」")
    else:
        check(False, "表外发布期（字段集合变过的那几代）居然被解成了对象")
    no_review = CRP.build_cited_report_version(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=None,
        report_id=_REPORT_ID)
    check(no_review.system_review_state == "system_review_not_run"
          and "review_not_completed" in no_review.release_blockers(),
          "未审 ≠ 审过：`system_review_not_run` 作为发布阻塞项如实列出")
    # 反例：回声**无** blocking 也不得升格成 passed。这里把同一份意见声明成独立审阅，
    # 两次结果的差别只来自生产者身份一词 —— 证明挡住升格的确实是身份，不是意见内容。
    # （这一份不能写成 `_supported_all()`：s2 有机械硬错误，声称它 supported 会先撞上
    # 「审阅不得覆盖机械底线」那条门；这里改写成低严重度、非 blocking 的意见。）
    clean = _review_json(
        _issue_json("s1", "m01"),
        _issue_json("s2", "m02", category="insufficient", severity="low", blocking=False,
                    semantic="not_supported_by_source", evidence=("m02",),
                    reason="这一句引的材料里没有这个数字，但本意见不阻塞发布"),
        _issue_json("s3", "f01"))
    independent = _parse(clean, draft=draft, bundle=bundle,
                         report_version=_REPORT_VERSION, check_report=check_report,
                         review_producer_kind="independent_llm_review")
    independent_version = CRP.build_cited_report_version(
        draft=draft, manifest=manifest, check_report=check_report,
        review_outcome=independent, report_id=_REPORT_ID, required_gap_count=1)
    clean_echo = _parse(clean, draft=draft, bundle=bundle,
                        report_version=_REPORT_VERSION, check_report=check_report)
    clean_echo_version = CRP.build_cited_report_version(
        draft=draft, manifest=manifest, check_report=check_report,
        review_outcome=clean_echo, report_id=_REPORT_ID, required_gap_count=1)
    check(clean_echo.blocking_issue_ids == (),
          "反例的前件成立：这份离线回声一条 blocking 都没有")
    check(clean_echo_version.system_review_state == "system_review_not_run",
          "**没有** blocking 的离线回声同样停在 not_run：不是「意见够干净」就能变成审过")
    check(independent_version.system_review_state == "system_review_not_passed",
          "③ 不得越过 ②：本节还有 s2 一句机械硬错误，独立审阅即使无 blocking 也**不得**"
          f"写成 `passed_awaiting_human`（实测 {independent_version.system_review_state}）")
    check(independent_version.blocking_issue_count == 0
          and independent_version.review_producer_kind == "independent_llm_review",
          "反例前件成立：这是独立审阅、且它一条 blocking 都没提——挡住升格的确实是机械硬错误")
    _expect_crp(lambda: CRP.CitedReportVersion(**_tweak(
        independent_version, system_review_state="system_review_passed_awaiting_human")),
        token="不得写 system_review_passed_awaiting_human")
    clean_report = SC.check_cited_prose(
        draft=_draft(manifest, sentences=(CW.CitedSentence("s1", _BODY_A, ("m01",)),)),
        manifest=manifest)
    clean_draft = _draft(manifest, sentences=(CW.CitedSentence("s1", _BODY_A, ("m01",)),))
    clean_request = CR.build_cited_review_request(draft=clean_draft, manifest=manifest)
    # 换了一份草稿就得换锚：版本锚由写作侧输入派生，拿主夹具的锚去绑这份小稿会在
    # `build_cited_report_version` 里被当场抓住（两稿的锚不同）。
    clean_anchor = CRP.derive_report_version(draft=clean_draft, manifest=manifest)
    check(clean_anchor != _REPORT_VERSION, "反例前件：这份干净小稿的锚与主夹具不同")
    clean_bundle = CR.build_cited_review_bundle(
        draft=clean_draft, manifest=manifest, request=clean_request,
        report_version=clean_anchor, report_id=_REPORT_ID, model_policy_id="mp-stub")
    clean_session = _parse(_review_json(_issue_json("s1", "m01")), draft=clean_draft,
                           bundle=clean_bundle, report_version=clean_anchor,
                           check_report=clean_report,
                           review_producer_kind="independent_llm_review")
    check(CRP.build_cited_report_version(
        draft=clean_draft, manifest=manifest, check_report=clean_report,
        review_outcome=clean_session, report_id=_REPORT_ID
    ).system_review_state == "system_review_passed_awaiting_human",
          "正例：机械层**无**硬错误 + 独立审阅无 blocking ⇒ 才够格写成 `passed_awaiting_human`")
    _expect_crp(lambda: CRP.CitedReportVersion(
        **_tweak(clean_echo_version, system_review_state="system_review_passed_awaiting_human")),
        token="不得进入读者预览")
    echo_md = CRP.render_cited_preview_markdown(CRP.build_cited_section_preview(
        draft=draft, manifest=manifest, check_report=check_report,
        review_outcome=clean_echo, version=clean_echo_version))
    check("未进行真实独立语义审阅" in echo_md and "offline_diagnostic_echo" in echo_md,
          "读者面上离线回声有**专有**标注，不被读成独立审阅")
    check("system_review_passed_awaiting_human" not in echo_md
          and "system_review_not_passed" not in echo_md,
          "读者面**永不**显示 `passed_awaiting_human`：那是留给真实独立审阅的字符串")

    # ============================================ §9b 指标表进同一版本身份
    details.append("## §9b 指标表与正文同版：表一变，记录身份就变")

    def _metric_table(*, section_id: str, keys: tuple[str, ...]) -> CFT.CitedMetricTable:
        return CFT.CitedMetricTable.create(
            section_id=section_id, caption="主要财务指标（夹具）",
            header=("指标", "本期", "上期"), entity_scope="演示主体（demo）",
            unit="CNY", period_basis="duration",
            rows=(CFT.CitedMetricTableRow(
                label="营业收入", unit="CNY", cells=("1.00", ""), fact_ids=("fx",),
                citation_keys=keys, period_texts=("本期",)),))

    filed = _metric_table(section_id=manifest.section_id, keys=("f01",))
    check(filed.section_id == manifest.section_id and "f01" in manifest.all_keys(),
          "夹具前提：表属于本节，格引用键在本节清单内")
    with_table = CRP.build_cited_report_version(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=outcome,
        report_id=_REPORT_ID, required_gap_count=1, metric_tables=(filed,))
    again = CRP.build_cited_report_version(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=outcome,
        report_id=_REPORT_ID, required_gap_count=1, metric_tables=(filed,))
    check(with_table.metric_table_ids == (filed.table_id,)
          and bool(with_table.metric_tables_fingerprint)
          and again.metric_tables_fingerprint == with_table.metric_tables_fingerprint,
          "指标表进身份体：表 id 与聚合指纹都记在版本记录里，且同一组表算出的指纹相同")
    check(with_table.record_id != version.record_id
          and with_table.fingerprint() != version.fingerprint(),
          "**表一变，记录身份就变**：指标表不是旁挂产物，它是这一版的一部分")
    check(with_table.report_version == version.report_version,
          "版本锚**不**随表格变：锚只含写作侧输入，表格在完整记录里（两件事分开）")
    _expect_crp(lambda: CRP.build_cited_report_version(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=outcome,
        report_id=_REPORT_ID, metric_tables=(_metric_table(
            section_id=manifest.section_id, keys=("f99",)),),
    ), token="不在本节输入清单")
    _expect_crp(lambda: CRP.build_cited_report_version(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=outcome,
        report_id=_REPORT_ID, metric_tables=(_metric_table(
            section_id=manifest.section_id + "-other", keys=("f01",)),),
    ), token="不是本节")
    _expect_crp(lambda: CRP.CitedReportVersion(
        **_tweak(with_table, metric_tables_fingerprint="")), token="必须同时存在或同时为空")
    _expect_crp(lambda: CRP.CitedReportVersion(
        **_tweak(with_table, metric_table_ids=())), token="必须同时存在或同时为空")
    # 预览：表格与它声称的那一版必须是同一条记录
    _expect_crp(lambda: CRP.build_cited_section_preview(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=outcome,
        version=version,
        metric_table_outcome=CFT.CitedMetricTableOutcome(
            section_id=manifest.section_id, schema_version=CFT.CITED_METRIC_TABLE_SCHEMA_VERSION,
            rule_version=CFT.CITED_METRIC_TABLE_RULE_VERSION, authority_kind="financial_workflow",
            tables=(filed,))),
        token="表格与正文必须同版")
    tabled_preview = CRP.build_cited_section_preview(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=outcome,
        version=with_table,
        metric_table_outcome=CFT.CitedMetricTableOutcome(
            section_id=manifest.section_id, schema_version=CFT.CITED_METRIC_TABLE_SCHEMA_VERSION,
            rule_version=CFT.CITED_METRIC_TABLE_RULE_VERSION, authority_kind="financial_workflow",
            tables=(filed,)))
    tabled_md = CRP.render_cited_preview_markdown(tabled_preview)
    check(filed.caption in tabled_md and filed.rows[0].label in tabled_md,
          "指标表在**同一份**预览里可读（不是只写去一个诊断 json）")
    check("财务指标表" in tabled_md and "留空" in tabled_md,
          "表格段落自报来源与「空格是留空、不是推算」")
    check(with_table.metric_tables_fingerprint[:12] in tabled_md,
          "预览印出指标表指纹：读者可据此核对「我读的格子属于哪一版」")
    check(CRP.CitedSectionPreview.from_dict(tabled_preview.to_dict()).to_dict()
          == tabled_preview.to_dict(), "带指标表的预览 `to_dict` → `from_dict` 逐字段还原")

    # ============================================ §10 逐句标注预览
    details.append("## §10 一处错误不清零整节，预览明确「不可发布」")
    preview = CRP.build_cited_section_preview(
        draft=draft, manifest=manifest, check_report=check_report,
        review_outcome=outcome, version=version)
    rows = {r.sentence_id: r for r in preview.rows}
    check(list(rows) == ["s1", "s2", "s3"], "三句都在预览里（一处错误不抹掉整节文字）")
    check(rows["s1"].ok and rows["s3"].ok and not rows["s2"].ok,
          "只有出问题的那一句被标记，另外两句照旧可读（`ok` 逐句独立）")
    check(rows["s2"].mechanical_verdict == "hard_error"
          and rows["s2"].failure_reasons == ("unsourced_number_surface",)
          and rows["s2"].review_blocking
          and rows["s2"].review_semantic_category == "not_supported_by_source",
          "同一句上机械结论与审阅意见**并列**显示，不合并成一个好/坏")
    check(rows["s1"].mechanical_verdict == "pass"
          and rows["s3"].mechanical_verdict == "pass",
          "另外两句的机械结论仍是 `pass`（不被邻句的硬错误连坐）")
    markdown = CRP.render_cited_preview_markdown(preview)
    check("不可发布、未经人工接受" in markdown, "预览首部明确写「不可发布、未经人工接受」")
    check("仅预留展示入口" in markdown and "不提供编辑、回写或续跑" in markdown,
          "「人工修改」如实写成只预留展示入口")
    check("语义已被支持" in markdown, "明确写出机械核对**没有**资格宣称语义已被支持")
    check("机械底线：**硬错误**" in markdown, "硬错误在读者面上有**逐句**标记")
    for text in (_BODY_A, _NUMBER_TEXT, _FACT_TEXT):
        check(text in markdown, f"正文原文逐句出现在预览里：{text[:14]}…")
    check("`crpv_" in markdown and "① 预览" in markdown and "④ 人工接受" in markdown,
          "四个状态与版本锚在读者面上分列显示")
    check("no_source_in_manifest" in markdown and "缺口" in markdown,
          "写作面登记的缺口在预览里有自己的中文段落")
    check(CRP.CitedSectionPreview.from_dict(preview.to_dict()).to_dict()
          == preview.to_dict(), "预览 `to_dict` → `from_dict` 逐字段还原")
    forged = preview.to_dict()
    forged["rows"][0]["ok"] = False
    check(CRP.CitedSectionPreview.from_dict(forged).rows[0].ok is True,
          "`ok` 是派生值：回读时重算，落盘的那一个不作数")
    single = _draft(manifest, sentences=(CW.CitedSentence("s1", _BODY_A, ("m01",)),))
    single_report = SC.check_cited_prose(draft=single, manifest=manifest)
    lonely = CRP.build_cited_section_preview(
        draft=single, manifest=manifest, check_report=single_report, review_outcome=None,
        version=CRP.build_cited_report_version(
            draft=single, manifest=manifest, check_report=single_report,
            review_outcome=None, report_id=_REPORT_ID))
    check(len(lonely.rows) == 1 and not lonely.rows[0].review_blocking
          and lonely.rows[0].review_state == "review_not_completed",
          "未审阅时预览照样出逐句行（未审 ≠ 有问题，两个轴分开记）")
    check(lonely.rows[0].ok is False,
          "但这一行**不算 ok**：机械层过了、③ 轴没跑，读者不能把它读成「两轴都干净」"
          "——`ok` 是两轴并列的合取，不是机械层的结论")

    # ============================================ §11 身份不混用
    details.append("## §11 新 wire 不与树结构版本表、旧 `ReportVersion` 混用")
    versions_src = (_ROOT / "document_structure" / "versions.py").read_text(encoding="utf-8")
    for const in (CR.CITED_REVIEW_SCHEMA_VERSION, CRP.CITED_REPORT_VERSION_SCHEMA_VERSION,
                  AS.REVIEW_ISSUE_SCHEMA_VERSION, AS.REVIEW_INPUT_BUNDLE_SCHEMA_VERSION):
        check(const not in versions_src, f"版本常量 {const!r} 不出现在树结构版本表里")
    check(not hasattr(CRP, "ReportVersion") and not hasattr(CR, "ReportVersion"),
          "新链**没有**复用旧 `ReportVersion`（旧链只读保留）")
    check(version.report_version.startswith("crpv_")
          and version.record_id.startswith("cpr_")
          and version.record_id != version.report_version,
          "版本锚与完整记录是两个身份（锚只含写作侧输入，因此可被意见引用而不成环）")
    check(CR.CITED_REVIEW_UNIT_KIND in AS.REVIEW_UNIT_KINDS,
          "审阅单元复用 §7.3 已登记的引用轴，不新增取值")

    # ============================== §12 审阅提示词资产头必须与调用账本上的版本对账
    details.append("## §12 审阅提示词的 `prompt_version` 必须对应仓内真实字节")
    real_review_prompt = CR.load_cited_review_prompt()
    declared_review = CW.declared_prompt_identity(real_review_prompt)
    check(declared_review == (CR.CITED_REVIEW_PROMPT_ASSET, CR.CITED_REVIEW_PROMPT_REVISION),
          "**正例**：仓内审阅资产第 1 行声明的 `（asset，revision）` 与本链常量**逐字相同**"
          "（写作侧早有这道加载即对账，审阅侧本次补齐同一纪律）")
    check(CW.declared_prompt_identity("你是审阅者。\n没有任何声明。") is None,
          "**反例**：资产头没有声明句式 ⇒ 取不到身份（不是「版本对得上」）")

    def _review_prompt_reason(fn) -> str:
        try:
            fn()
        except CR.CitedReviewError as exc:
            return exc.reason
        raise AssertionError("这一路必须 fail-closed，但它通过了")

    import llm.client as _llm_client
    _orig_review_load = _llm_client.load_prompt
    try:
        _llm_client.load_prompt = lambda name: (
            f"你是审阅者（{CR.CITED_REVIEW_PROMPT_ASSET}，revision crr-999）。")
        check(_review_prompt_reason(CR.load_cited_review_prompt)
              == "prompt_asset_identity_mismatch",
              "**反例**：资产自称的修订号与本链常量不一致 ⇒ 以 "
              "`prompt_asset_identity_mismatch` 拒（账本上的 `prompt_version` 将不对应任何字节）")
        _llm_client.load_prompt = lambda name: "没有声明的资产头。"
        check(_review_prompt_reason(CR.load_cited_review_prompt)
              == "prompt_asset_identity_missing",
              "**反例**：资产头取不到声明句式 ⇒ 以 `prompt_asset_identity_missing` 拒"
              "（头部格式漂移与版本对得上在这条线上不可区分）")
    finally:
        _llm_client.load_prompt = _orig_review_load
    check(CW.declared_prompt_identity(CR.load_cited_review_prompt()) == declared_review,
          "⇒ 还原后读的仍是真资产（上面两次替换只活在 try 里）")
    details.append("NOTE §12：这几条**不**断言某个字面版本号——断言的是「账本上的 "
                   "`prompt_version` 一定对应某份真实字节」这条关系。")

    # ==================== §13 诊断槽位在读者面上要写出「这是什么、不是什麼」
    details.append("## §13 代理口径的诊断槽位：读者面要有**限制说明**，不只是一个块标题")
    #: 诊断槽位的机制（分层路由、整行移块、逐格代理限定语）由金融展示侧自己的测试盯住。
    #: 这里只钉**读者面**那一条：光把代理行挪到另一个块、标题写「诊断槽位」，读者仍可能把它
    #: 当成一条可以与正文精确口径行并排比较的结论。因此断言这块**自己**写出了限制。
    diag = CFT.CitedMetricTable.create(
        section_id=manifest.section_id, caption="主要财务指标（夹具）· 诊断槽位",
        header=("指标", "本期", "上期"), entity_scope="演示主体（demo）",
        unit="CNY", period_basis="duration",
        rows=(CFT.CitedMetricTableRow(
            label="利息保障倍数", unit="CNY",
            cells=("-9.94。代理口径（PROXY_FINANCE_EXPENSES）", ""),
            fact_ids=("fx",), citation_keys=("f01",), period_texts=("本期",)),),
        display_tier="diagnostic_only",
        display_tier_basis=("contract_display_tier", "authority_proxy_fact_marker"))
    diag_version = CRP.build_cited_report_version(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=outcome,
        report_id=_REPORT_ID, required_gap_count=1, metric_tables=(diag,))
    diag_md = CRP.render_cited_preview_markdown(CRP.build_cited_section_preview(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=outcome,
        version=diag_version,
        metric_table_outcome=CFT.CitedMetricTableOutcome(
            section_id=manifest.section_id, schema_version=CFT.CITED_METRIC_TABLE_SCHEMA_VERSION,
            rule_version=CFT.CITED_METRIC_TABLE_RULE_VERSION, authority_kind="financial_workflow",
            tables=(diag,))))
    check("诊断槽位" in diag_md and "这一块是诊断参考，不是结论" in diag_md,
          "**正例**：诊断槽位块自己写明「这是诊断参考、不是结论」——不是只换个块标题就完事")
    check("不参与『精确口径』比较" in diag_md and "本次未取得时不作正式结论" in diag_md,
          "**正例**：限制说明点名代理口径「不参与『精确口径』比较」，并写明该口径的正式结论"
          "在没有精确口径分母时**不作**（与 `FORMULA_REVIEW.md` §1 一致）")
    check("代理口径（PROXY_FINANCE_EXPENSES）" in diag_md,
          "**边界**：逐格代理限定语照旧在格子里——限制说明是**加**上去的，不是替换掉格内标注")
    check("这一块是诊断参考，不是结论" not in tabled_md,
          "**反例**：只有 `required_body` 表的预览里**没有**这句限制说明"
          "（它是诊断槽位专属，不是每张表的通用套话）")
    details.append("NOTE §13：这几条断言的是**读者面的限制说明是否存在**，"
                   "不断言任何一格的值 / 期间 / 单位 / 引用；分层不改任何一格。")

    # ============ §14 提示词点名的句义类必须落在**冻结词表**里（crr-7 的纠错守卫）
    details.append("## §14 审阅提示词点名的 `semantic_category` 必须逐字落在冻结词表内")
    #: 机制：`semantic_category` 是 `assurance.schema.REVIEW_SEMANTIC_CATEGORIES` 那份**封闭**
    #: 词表，由 §0.20 由用户裁决。提示词里自造一个类名**不会只丢那一条意见**：解析面把
    #: **整次返回作废**，该节每一句的独立审阅结论一并消失。真实 cp-14 run 的公司节就是这样
    #: 折的（`review_reply_unparsable`，得到 `issuer_self_assessment_as_conclusion`）。
    #: 因此把「提示词对照表 ↔ 冻结词表」这条**关系**钉在这里——不钉任何一行文字的措辞。
    prompt_table_rows: list[tuple[str, str]] = []
    for raw_line in real_review_prompt.splitlines():
        stripped = raw_line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        head, tail = cells[0], cells[-1].strip("`").strip()
        if len(head) > 2 and head.startswith("`") and head.endswith("`") \
                and tail in AS.REVIEW_CATEGORIES:
            prompt_table_rows.append((head.strip("`"), tail))
    check(bool(prompt_table_rows),
          f"前提：提示词里有可解析的「句义类 → 粗类」对照表（解析到 {len(prompt_table_rows)} 行）")
    named = [name for name, _ in prompt_table_rows]
    check(len(set(named)) == len(named), "对照表里没有重复点名的句义类")
    for name, coarse in prompt_table_rows:
        check(name in AS.REVIEW_SEMANTIC_CATEGORIES,
              f"**正例**：提示词点名的 `{name}` 在冻结词表 `REVIEW_SEMANTIC_CATEGORIES` 里"
              "——表外的类名会让**整次返回**作废，这正是 crr-6 的实跑缺陷")
        check(AS.REVIEW_CATEGORY_BY_SEMANTIC.get(name) == coarse,
              f"**正例**：提示词给 `{name}` 配的粗类 `{coarse}` 与 "
              "`REVIEW_CATEGORY_BY_SEMANTIC` 逐字一致（构造期强校验的就是这条对应）")
    check(set(named) == set(AS.REVIEW_SEMANTIC_CATEGORIES),
          "**完整性**：冻结词表九个值在提示词里**都有**释义行"
          "（模型看不见的类别等于不可用；缺一即提示词与词表脱节）")
    check(AS.REVIEW_CATEGORIES
          == ("supported", "contradicted", "insufficient", "missing_content"),
          "反例前件：粗类仍是那四个值（提示词里也逐字列了这四个）")
    #: 上半段钉的是**提示词 → 词表**这条关系；下半段钉**行为**，不钉提示词的任何措辞：
    #: 一份回复里带了表外的类名会怎样。crr-6 的真实失败不是提示词漏写了哪句，而是模型自造了
    #: 一个类名——赔付的是**整节每一句**的审阅结论（`review_reply_unparsable`），
    #: 不是「丢掉那一条、其余照收」。
    legal_reply = _review_json(
        _issue_json("s1", "m01"),
        _issue_json("s2", "m02", category="insufficient", severity="high", blocking=True,
                    semantic="not_supported_by_source", evidence=("m02",),
                    reason="被引材料里没有这个数字"),
        _issue_json("s3", "f01"))
    try:
        legal_outcome = _parse(legal_reply, draft=draft, bundle=bundle,
                               report_version=_REPORT_VERSION)
    except Exception as exc:                                             # noqa: BLE001
        check(False, f"**正例前件**：词表内的类名本该照常解析，却抛了 {type(exc).__name__}：{exc}")
    else:
        check(len(legal_outcome.issues) == 3,
              "**正例**：`semantic_category` 取自冻结词表 ⇒ 正常解析，三条意见都在"
              "（覆盖等式满足，说明下面那条反例确实是「类名」这一处之差造成的）")

    illegal_reply = _review_json(
        _issue_json("s1", "m01"),
        _issue_json("s2", "m02", category="insufficient", severity="high", blocking=True,
                    semantic="issuer_self_assessment_as_conclusion", evidence=("m02",),
                    reason="发行人自述被写成结论"),
        _issue_json("s3", "f01"))
    try:
        _parse(illegal_reply, draft=draft, bundle=bundle,
               report_version=_REPORT_VERSION)
    except CR.CitedReviewError as exc:
        check("issuer_self_assessment_as_conclusion" in str(exc),
              "**反例**：表外的 `semantic_category` ⇒ 整份回复 fail-closed"
              "（异常当场点名那个表外类名），"
              "不是「丢掉那一条、其余照收」——cp-14 真实公司节正是这样折的")
        #: `crr-9`：这一条不合约**还带位置**——否则运行目录只剩一句无定位的原因。
        check(exc.sentence_id == "s2" and exc.citation_id == "m02"
              and exc.reason == "review_reply_unparsable",
              f"**反例**：抛出的是**带定位**的 `CitedReviewError`"
              f"（句 {exc.sentence_id!r}／引用 {exc.citation_id!r}，"
              f"原因码 {exc.reason!r}）——类型仍是 `AssuranceSchemaError` 那一族，"
              "只是现在说得清是哪一条意见出的问题")
    else:
        check(False, "表外的类名本该让整份回复作废，但它解析通过了")
    details.append("NOTE §14：上半段断言的是「提示词点名的类别 / 粗类 ↔ 冻结词表」这条**关系**，"
                   "下半段断言的是「表外类名 ⇒ 整份回复作废」这条**行为**；"
                   "两段都不断言提示词的任何句子措辞。")

    # ====================================================== §15 失败原因的中文人读理由
    details.append("## §15 预览逐句行把失败原因码翻成中文，但两个错因族必须分得开")
    #: 机制（`crpp-4`）：逐句行此前只印原因码原文（`sentence_aspect_not_registered` 这样的
    #: 机器串），读者判「这一句到底哪里不对」得去翻判据表；把这些码压成一句「原文事实错误」
    #: 更糟——它们**不是**一件事。这里钉三条**关系**，不钉任何一句中文的措辞：
    #:   (a) 标签表与 `sentence_check.FAILURE_REASONS` **互为子集**（词表增删必须同步）；
    #:   (b) 每一条都真的给了中文（不是原样退回原因码）；
    #:   (c) 两个**处置不同**的错因族彼此不同，各自点明自己错在哪条轴上。
    label_map = CRP._FAILURE_REASON_LABEL
    check(set(label_map) == set(SC.FAILURE_REASONS),
          f"**完整性**：中文理由表与 `sentence_check.FAILURE_REASONS` 逐码相等"
          f"（词表 {len(SC.FAILURE_REASONS)} 条、表 {len(label_map)} 条；"
          f"缺 {sorted(set(SC.FAILURE_REASONS) - set(label_map))}、"
          f"多 {sorted(set(label_map) - set(SC.FAILURE_REASONS))}）"
          "——缺一条就会在读者面上静默退化成原因码原文")
    for code in SC.FAILURE_REASONS:
        label = CRP.failure_reason_label(code)
        check(bool(label.strip()) and label != code,
              f"**正例**：`{code}` 有中文理由（不是原样退回代码，否则这一条等于没翻）")
    check(CRP.failure_reason_label("__no_such_reason__") == "__no_such_reason__",
          "**反例**：表外的原因码**原样返回**——词表与渲染面分叉时，读者最需要看到的"
          "恰恰是那个码本身，把它藏进「未知问题」会让分叉变成看不见的事")
    attribution = CRP.failure_reason_label("sentence_aspect_not_registered")
    staleness = CRP.failure_reason_label("history_material_as_current_state")
    check(attribution != staleness,
          "两个**处置不同**的错因族各有各的话，不压成一句统称")
    check("登记" in attribution and "栏" in attribution,
          "`sentence_aspect_not_registered` 说的是**引用未登记本栏**（归属轴：字可能对，"
          "错在这份来源在本节 Pack 里只登记到别的栏目）")
    check("历史" in staleness or "上年" in staleness,
          "`history_material_as_current_state` 说的是**历史材料不能证明当前状态**"
          "（来源角色轴：来源合格、登记也没问题，问题是它是上年／历史来源）")
    check(CRP.failure_reason_label("unsourced_number_surface") in markdown
          and "（`unsourced_number_surface`）" in markdown,
          "**行为**：预览逐句行印出的是**中文理由 + 原因码**两件都有"
          "（读的人当场看懂、机器仍能逐码对账）")
    details.append("NOTE §15：钉的是「词表 ↔ 中文表 ↔ 渲染面」这条**关系**"
                   "（完整性、不退回代码、两族可分），**不**钉任何一句中文的措辞，"
                   "也不改任何判据本身的取值。")

    # ===== §16 缺键 / 空串的 `category` 仍失败，且失败**带定位**（crr-9）
    details.append("## §16 意见缺 `category`／写空串 ⇒ 整次作废，但失败要能**定位到那一条**")
    #: 动因是一次真实运行（`m930_3_cited_real_company_cp20_r1`，审阅
    #: `call_id b3328e284ee14912b5de83fa0bfe5d88`）：调用 HTTP ok、回复完整、20 条意见齐全，
    #: 其中 `s0002` 那一条把 `citation_id` 写了**两遍**、`category` 一个都没有。
    #: `json.loads` 把重复键**静默**压成一个（后者胜），字段集校验因此看不到缺口，空串一路走到
    #: `ReviewIssue` 的构造期才炸成 `AssuranceSchemaError`——那条异常**不带**是哪一条意见出错，
    #: 整条审阅轴随之丢失。这里钉四件事：
    #:   (a) 回复里**有**合法 `category` 时照常解析（正例前件，证明下面确实是那一处之差）；
    #:   (b) 缺键 ⇒ 失败；(c) 空串 ⇒ 失败；(d) 三者都**带 `sentence_id` / `citation_id`**。
    #: 同时钉「**不猜**」：`category` 没有可推断的默认值，这里绝不把它补成 `supported`，
    #: 也绝不返回"半份意见"。
    _row = {"sentence_id": "s2", "citation_id": "m02",
            "semantic_category": "not_supported_by_source", "severity": "high",
            "blocking": True, "reason": "被引材料里没有这个数字",
            "evidence_refs": ["m02"], "suggested_target": None}
    _head = json.dumps(_issue_json("s1", "m01"), ensure_ascii=False)
    _tail = json.dumps(_issue_json("s3", "f01"), ensure_ascii=False)
    _good = json.dumps({**_row, "category": "insufficient"}, ensure_ascii=False)
    #: 真实回复的形状：同一个键写两遍、`category` 缺席。
    _dup = json.dumps(_row, ensure_ascii=False).replace(
        '"citation_id": "m02"', '"citation_id": "m02", "citation_id": "m02"', 1)
    _empty = json.dumps({**_row, "category": ""}, ensure_ascii=False)
    _no_reason = json.dumps({**_row, "category": "insufficient", "reason": ""},
                            ensure_ascii=False)

    def _reply(middle: str) -> str:
        return '{"issues": [%s, %s, %s]}' % (_head, middle, _tail)

    def _exc(fn) -> CR.CitedReviewError:
        """跑一次解析，**要求**它抛 `CitedReviewError`（返回结果本身即失败）。"""
        try:
            fn()
        except CR.CitedReviewError as exc:
            return exc
        raise AssertionError("这一路本该 fail-closed，但它返回了一份结果")

    check(json.loads(_dup) == json.loads(json.dumps(_row, ensure_ascii=False)),
          "前提：`json.loads` 把重复的 `citation_id` **静默**压成一个键"
          "——字段集校验因此看不到缺口，这正是真实那一折的入口")
    try:
        _ok = _parse(_reply(_good), draft=draft, bundle=bundle,
                     report_version=_REPORT_VERSION)
    except Exception as exc:                                             # noqa: BLE001
        check(False, f"**正例前件**：带合法 `category` 的同一行本该照常解析，却抛了"
                     f" {type(exc).__name__}：{exc}")
    else:
        check(len(_ok.issues) == 3,
              "**正例前件**：`category` 在场且合法 ⇒ 正常解析、三条意见都在"
              "（下面三条反例与它**只差那一处**）")

    for _name, _middle, _token in (
            ("缺键（重复 `citation_id`、无 `category`）", _dup, "category"),
            ("`category` 写成空串", _empty, "category"),
            ("`reason` 写成空串", _no_reason, "reason")):
        _e = _exc(lambda m=_middle: _parse(_reply(m), draft=draft, bundle=bundle,
                                           report_version=_REPORT_VERSION))
        check(_e.reason == "review_reply_unparsable"
              and _e.sentence_id == "s2" and _e.citation_id == "m02",
              f"**反例**：{_name} ⇒ 整次返回作废，原因码 `{_e.reason}`，"
              f"并**带定位**（句 `{_e.sentence_id}`／引用 `{_e.citation_id}`）"
              "——运行目录因此说得清是哪一条意见出的问题，也回得到原始回复")
        check(_token in str(_e),
              f"**反例**：{_name} 的异常消息点名 `{_token}`（不是一句无定位的 schema 报错）")
        check(not isinstance(_e, AS.AssuranceSchemaError),
              f"**反例**：{_name} 抛出的是链自己的 typed 异常，"
              "不是裸的 `AssuranceSchemaError`——裸的那个不带 `sentence_id`／`citation_id`")

    #: **不猜**：`category` 缺席时绝不能有"补一个默认值"的路径。上面三条都已在解析期抛出
    #: （`_exc` 在返回结果时直接判失败），因此这里只再加一条关于**词表本身**的断言：
    #: 空串不是 `REVIEW_CATEGORIES` 的成员，也就没有任何"合法"的默认值可补。
    check("" not in AS.REVIEW_CATEGORIES and "supported" in AS.REVIEW_CATEGORIES,
          "**不猜**：`category` 的封闭词表里没有空串，因此缺键／空串**只能**作废，"
          "没有可补的默认值——把空类别猜成 `supported`，等于把「没看过」记成「没问题」")
    details.append("NOTE §16：钉的是「缺键 / 空串 ⇒ 作废 **且**带定位」这条**行为**与"
                   "「绝不猜默认值」这条**禁令**；提示词里那句「九个键一个都不能少」是同一件事"
                   "的生成侧表述，此处不解析它的措辞。")

    # ============ §17 四族正反例：词表、期间口径、（离线证不了的那一半）
    details.append("## §17 提示词的四族正反例：类名落在冻结词表内，期间口径与机械侧同一条")
    #: `crr-10` 的 7g 四族。这里钉的是**关系**，不是措辞：
    #: (1) 每族的反例类名必须是冻结词表里的一员，且与 `REVIEW_CATEGORY_BY_SEMANTIC` 的粗类一致
    #:     —— 表外类名赔的是**整节每一句**（crr-6 的实跑教训）；
    #: (2) 正反两栏都必须写出形状，不能只写一个族名；
    #: (3) 两条边界必须在场："来源逐字有"不是免检章、机械核对是另一条轴且不构成跳过理由；
    #: (4) 7b 的期间条件必须收成**绝对年份**，与机械侧 `srsc-4` 的判据同口径。
    #: 逐行比对会被提示词的**换行**骗到（同一句话断在两行上），因此判"某句话在不在"时用
    #: 去掉全部空白的全文。判"表格某一列是什么"仍要逐行——那是结构，不是措辞。
    _flat_prompt = "".join(real_review_prompt.split())
    _FAMILY_KEYS = ("template_or_checklist_as_business_fact",
                    "document_period_as_report_period",
                    "selective_omission", "unsupported_evaluation")
    families: list[tuple[str, str, str, str]] = []
    for raw_line in real_review_prompt.splitlines():
        stripped = raw_line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) != 4:
            continue
        head = cells[0].strip("`").strip()
        if head in _FAMILY_KEYS:
            families.append((head, cells[1], cells[2], cells[3].strip("`").strip()))
    check([f[0] for f in families] == list(_FAMILY_KEYS),
          f"**完整性**：7g 的四族按固定键全部在场（实测 {[f[0] for f in families]}）")
    for key, positive, negative, category in families:
        check(category in AS.REVIEW_SEMANTIC_CATEGORIES,
              f"**正例**：`{key}` 的反例类名 `{category}` 在冻结词表内"
              "（表外类名让**整次返回**作废，不只丢那一条）")
        check(category in set(named),
              f"**正例**：`{key}` 的反例类名 `{category}` 在提示词自己的释义表里**有定义行**"
              "（§14 已把那张表与冻结词表逐条对过账；四族不得点名一个没有释义的类别）")
        check(len(positive) > 10 and len(negative) > 10 and positive != negative,
              f"**正例**：`{key}` 的**正例形状**与**反例形状**各自写出、且不是同一句"
              "（只给族名等于没给判法）")
    check("来源逐字有" in _flat_prompt and "不是免检章" in _flat_prompt,
          "**边界**：7g 明写「来源逐字有」**不**解除这四族——这正是四族的共同形状")
    check("拿它当挡箭牌" in _flat_prompt
          and "机械核对是**另一条独立**的轴" in _flat_prompt,
          "**职责边界**：7g 明写机械核对是另一条独立的轴，审阅既不替它判、"
          "**也不**因为它会拦就放过（离线替身不能证这一条被遵守，只证它被写进了判据面）")

    #: 期间口径交叉对账：提示词认「绝对年份」才叫带上了期间，机械侧 `srsc-4` 的
    #: `has_absolute_period_qualification` 认的是同一种形状。两边给出的答案必须一致——
    #: 一处判「旧料当前化」、另一处判「带够了期间」，读者面就会出现两个相反的结论。
    from sections import source_role_scope as SRS
    check(SRS.RULE_VERSION == "srsc-4",
          f"前提：机械侧期间判据版本（实测 {SRS.RULE_VERSION!r}）")
    for text, absolute in (("公司 2023 年主营业务收入为 X。", True),
                           ("截至报告期末，公司主营业务收入为 X。", False),
                           ("截至 2023 年 12 月 31 日，公司主营业务收入为 X。", True),
                           ("本报告期末，公司主营业务收入为 X。", False)):
        check(SRS.has_absolute_period_qualification(text) is absolute,
              f"**对账**：`{text}` 的绝对期间判定为 {absolute} —— 相对期间"
              "（报告期末 / 本报告期末 / 期末）**不**算带上了期间，与提示词 7b 同一条口径")
    details.append("NOTE §17：这一节钉的是「提示词 ↔ 冻结词表 ↔ 机械侧期间判据」三条线上的"
                   "**关系**。四族是**生成侧判据**，离线替身不进这一面（同样的回复重放得到同样的"
                   "意见），因此**本模块不宣称审阅质量已被改善**；真实模型会不会照四族读数，"
                   "只有真实 run 能答。")

    # ============ §18 四族不是凭空造的：真实基线里各自的机械对应轴都在
    details.append("## §18 四族的机械对应轴在真实基线上**确实存在**（只读）")
    _base = (_ROOT / "evaluation" / "results" / "m930_3_cited_real_company_cp21_r1")
    if not (_base / "cited_prose.json").exists():
        skipped += 1
        details.append("SKIP §18：真实基线目录不在场（既存产物，本模块不因它缺失而失败）")
    else:
        _man = CW.CitedWriterInputManifest.from_dict(
            json.loads((_base / "cited_input_manifest.json").read_text("utf-8")))
        _base_draft = CW.CitedProseDraft.from_dict(
            json.loads((_base / "cited_prose.json").read_text("utf-8"))["draft"])
        _rep = SC.check_cited_prose(draft=_base_draft, manifest=_man)
        _axes = {r.check_kind for r in _rep.hard_error_records}
        check("template_text" in _axes,
              "`template_or_checklist_as_business_fact` 有机械对应轴 `template_text`"
              f"（实测真实基线硬错轴 {sorted(_axes)}）")
        check("current_state_scope" in _axes,
              "`document_period_as_report_period` 有机械对应轴 `current_state_scope`"
              "——两侧对「带没带绝对期间」给的是同一个答案")
        check("aspect_attribution" in _axes,
              "`selective_omission` 之外，栏目错位那一族（`off_topic` 的语义面）也有机械对应轴"
              "`aspect_attribution`：同一根轴，两侧各自表态、并列记录")
        #: 落盘的 `sentence_checks.json` 是那次 run 当时的读数，**不是**当下的重算结果。
        _persisted = json.loads((_base / "sentence_checks.json").read_text("utf-8"))
        check((_persisted.get("policy_version"), _rep.policy_version)
              != ("", _persisted.get("policy_version")),
              "落盘的逐句读数带着它**自己那时**的政策版本（可在文件里读到，本模块不改写它）")
        details.append(
            f"NOTE §18：落盘读数政策版本 {_persisted.get('policy_version')!r}，"
            f"当下重算 {_rep.policy_version!r} —— 旧 run 的逐句读数按**原版本**读出原结论，"
            "本模块只在内存里重算用于对账，**不**回写、**不**重标。")

    details.append("## §19 冻结演示 run 的 wire 照旧解得出，分歧表在**旧**读数上也算得出来（只读）")
    _frozen = _ROOT / "evaluation" / "results" / "m930_3_cited_real_dual_v2_r1"
    if not (_frozen / "company" / "review_issues.json").is_file():
        skipped += 1
        details.append("SKIP §19：已冻结的双节演示 run 不在场（既存产物，本模块不因它缺失而失败）")
    else:
        _seen: list[str] = []
        for _sect in ("company", "financial"):
            _wire = json.loads(
                (_frozen / _sect / "review_issues.json").read_text("utf-8"))
            #: 「字段集刚好等于 dataclass 字段 + 派生视图」是 `cited_demo_loader` 的判据；本模块
            #: 用同一个等式替它先算一遍，免得那条红只出现在演示 UI 的模块里。
            check(set(_wire) == set(CR.CitedReviewOutcome.__dataclass_fields__)
                  | {"blocking_issue_ids", "semantic_counts"},
                  f"§19 {_sect}/review_issues.json 的字段集仍与 `crv-3` 的 wire 合约相等"
                  "（派生读数不进 wire，历史归档就不会因升版而解不出来）")
            check(_wire["schema_version"] == CR.CITED_REVIEW_SCHEMA_VERSION
                  == "crv-3",
                  f"§19 {_sect} 读数声明 {_wire['schema_version']!r} == 现行 "
                  f"{CR.CITED_REVIEW_SCHEMA_VERSION!r}：旧 run 按**原版本**读出原结论")
            _outcome = CR.CitedReviewOutcome(
                **{k: _wire[k] for k in CR.CitedReviewOutcome.__dataclass_fields__
                   if k != "issues"},
                issues=tuple(AS.ReviewIssue.from_dict(i) for i in _wire["issues"]))
            check(_outcome.to_dict()["hard_error_override_sentence_ids"]
                  == _wire["hard_error_override_sentence_ids"],
                  f"§19 {_sect}：id 侧读数逐字复现（`to_dict` 与落盘的同一串）")
            _chk = SC.SentenceCheckReport.from_dict(json.loads(
                (_frozen / _sect / "sentence_checks.json").read_text("utf-8")))
            #: 表**现算**：输入只有落盘的两份产物，重算结果与内存里构造的对齐一致。
            check(CR.hard_error_divergence_rows(issues=_outcome.issues, check_report=_chk)
                  == CR.hard_error_divergence_rows(issues=_outcome.issues, check_report=_chk),
                  f"§19 {_sect}：分歧表是纯函数读数（同输入 ⇒ 同表，不受迭代顺序影响）")
            _seen.append(f"{_sect}(schema={_wire['schema_version']}"
                         f"/硬错 {len(_chk.blocked_sentence_ids)} 句"
                         f"/分歧 {len(_outcome.hard_error_override_sentence_ids)} 句)")
        details.append("NOTE §19 实读：" + "、".join(_seen)
                       + " —— 本模块只在内存里读，**不**回写、**不**重标、**不**重生成。")

    details.append(
        "NOTE 本模块只证明**结构与判据**：它不声称审阅运行时已落地，也不产生任何放行结论。"
        "真实审阅调用与真实 create-only run 须另经单独授权。")

    # ============================================ §20 草稿缺口分桶（`crpp-5`）
    details.append("## §20 草稿缺口分桶：按**冻结 Contract 的政策**分，不按「凡缺口皆必需」")
    #: 机制（`crpp-5`）：`release_blockers()` 只看 `required_gap_count`，而它旧口径是
    #: `len(draft.gaps)`——于是「Contract 自己判**不适用**的一栏」与「该写没写的一栏」在产物上
    #: 长得一模一样。分桶是这条口径的**依据**，因此必须能被正反例当场推翻。
    class _Aspect:
        """最小 Contract 栏目投影（只带分桶真正读到的那五个字段）。"""

        def __init__(self, aspect_id, requirement_text, *, display_tier="required_body",
                     applicability_policy=None, blocking_policy=()):
            self.aspect_id = aspect_id
            self.requirement_text = requirement_text
            self.display_tier = display_tier
            self.applicability_policy = applicability_policy
            self.blocking_policy = tuple(blocking_policy)

    class _G:
        def __init__(self, gap_id, requirement_text):
            self.gap_id = gap_id
            self.requirement_text = requirement_text

    _aspects = (
        _Aspect("revenue", "各业务收入及收入占比"),
        _Aspect("change", "客户集中度跨期变化", display_tier="optional_body",
                applicability_policy="not_applicable_no_comparable_period"),
        _Aspect("anonymity", "依法未披露客户名称时的标注",
                applicability_policy="not_applicable_no_plan"),
        _Aspect("diagnostic", "诊断槽位指标", display_tier="diagnostic_only"),
        #: 同一段要求文本声明两次：索引**必须**把它整条丢掉（歧义 ⇒ 不许挑一个信）。
        _Aspect("dup_a", "重名要求"), _Aspect("dup_b", "重名要求"),
    )
    _bins = CRP.classify_draft_gaps(
        gaps=(_G("g1", "各业务收入及收入占比"), _G("g2", "客户集中度跨期变化"),
              _G("g3", "依法未披露客户名称时的标注"), _G("g4", "诊断槽位指标"),
              _G("g5", "契约根本没提过的要求"), _G("g6", "重名要求")),
        aspects=_aspects)
    _by_id = {b.gap_id: b for b in _bins}
    check(_by_id["g1"].gap_bin == "required"
          and _by_id["g1"].policy_basis == "contract_required_body"
          and _by_id["g1"].contract_aspect_id == "revenue",
          "**正例**：`required_body` 且无豁免声明 ⇒ `required`，并带上 Contract 栏 id 与依据")
    check(_by_id["g2"].gap_bin == "not_applicable",
          "**正例**：`optional_body` + `not_applicable_no_comparable_period` ⇒ **先**看适用性政策，"
          "判 `not_applicable`（不是 `optional`）")
    check(_by_id["g3"].gap_bin == "not_applicable",
          "**正例**：`required_body` + `not_applicable_no_plan` ⇒ 仍是不适用——"
          "展示档再高也不能压过 Contract 明文的「合法不适用、不阻断」")
    check(_by_id["g4"].gap_bin == "diagnostic",
          "**正例**：`diagnostic_only` ⇒ `diagnostic`")
    check(_by_id["g5"].gap_bin == "unresolved"
          and _by_id["g5"].policy_basis == "contract_aspect_not_found",
          "**反例**：Contract 里查不到的要求 ⇒ `unresolved`（fail-closed，按必需数）")
    check(_by_id["g6"].gap_bin == "unresolved",
          "**反例**：同一段要求文本在 Contract 里声明了两次 ⇒ 整条丢弃、判 `unresolved`"
          "（歧义时**不**挑一个信，宁可多报一条必需）")
    check(CRP.required_gap_count_from_bins(_bins) == 3,
          "必需口径 = `required` + `unresolved`：本例 6 条缺口里只有 3 条算数"
          "（`required` 1 条 + `unresolved` 2 条；`required_gap_count` **不是** "
          "`len(draft.gaps)`）")
    check(set(CRP.CITED_GAP_BLOCKING_BINS) == {"required", "unresolved"},
          "计入阻断的桶是封闭的两档，`optional` / `not_applicable` / `diagnostic` 都不在内")
    _releaseless = CRP.build_cited_report_version(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=outcome,
        report_id=_REPORT_ID, required_gap_count=CRP.required_gap_count_from_bins(_bins))
    check("required_gaps_present" in _releaseless.release_blockers(),
          "**行为**：分桶后仍有必需缺口 ⇒ `release_blockers()` 照旧吐 `required_gaps_present`"
          "（分桶的用途是**分清是谁**，不是放宽放行）")
    _excused = CRP.build_cited_report_version(
        draft=draft, manifest=manifest, check_report=check_report, review_outcome=outcome,
        report_id=_REPORT_ID,
        required_gap_count=CRP.required_gap_count_from_bins(
            tuple(b for b in _bins if b.gap_id not in ("g1", "g5", "g6"))))
    check("required_gaps_present" not in _excused.release_blockers(),
          "**对照**：只剩可选/不适用/诊断栏时这条阻断项消失——但它们**仍然逐条列在分桶里**，"
          "不是被删掉了")
    details.append("NOTE §20：分桶是**确定性查表**（政策串 + 展示档 + Contract 栏 id），"
                   "不做语义判断、不读正文；读侧产物 `cited_gap_bins.json` 逐条带依据，"
                   "控制器的 `_require_gap_count` 只核**内部一致性**，不现场重算分桶。")

    # ============================================ §21 逐句结论分族（`crpp-5`）
    details.append("## §21 逐句机械结论分族：事实安全 / 栏目覆盖 / 判据没判，三个数分开")
    _split = CRP.classify_check_report(check_report)
    _hard_records = [r for r in check_report.records if r.verdict == "hard_error"]
    check(_split["hard_record_total"] == len(_hard_records),
          f"**完整性**：分族记录数之和 == 全部硬错记录数（{_split['hard_record_total']}）"
          "——族表与轴词表分叉时这里立刻红")
    check(_split["hard_sentence_total"] == len(check_report.blocked_sentence_ids)
          and _split["blocked_sentence_ids"] == sorted(check_report.blocked_sentence_ids),
          "被阻断句数逐字等于核对报告的 `blocked_sentence_ids`（分族不新增、不减句）")
    check(_split["families"] == list(SC.CHECK_FAMILIES),
          "族清单取自 `sentence_check` 的封闭词表，不在报告层另抄一份")
    check(_split["hard"]["fact_safety"]["sentences"] >= 1
          and _split["hard"]["column_coverage"]["sentences"] == 0,
          "**正例**：夹具里那句无来源数字落在 `fact_safety`，栏目覆盖族为空——"
          "两族分开报，不是把硬错总数摊到两族")
    check(_split["hard"]["fact_safety"]["reasons"].get("unsourced_number_surface") == 1,
          "族里逐条给出原因码计数（读者可据此回到 `sentence_checks.json` 复核）")
    _fam_rows = {row["sentence_id"]: row for row in _split["sentences"]}
    check(set(_fam_rows) == set(check_report.blocked_sentence_ids)
          and _fam_rows["s2"]["families"] == ["fact_safety"],
          "逐句族归属只覆盖**被阻断**的句子，且每句的族与原因码逐条列出")
    _clean = SC.check_cited_prose(
        draft=_draft(manifest, sentences=(_sentences()[0],)), manifest=manifest)
    _clean_split = CRP.classify_check_report(_clean)
    check(_clean_split["hard_record_total"] == 0
          and all(not v["records"] for v in _clean_split["hard"].values()),
          "**反例**：没有硬错的报告 ⇒ 两族都是 0（不把「这一轴没判」算成失败）")
    check(_clean_split["diagnostic"]["records"]
          >= len([r for r in _clean.records if not getattr(r, "applicable", True)]),
          "「这一轴这次没判」是**单列**的一档：它既不进 `fact_safety` 也不进 `column_coverage`")
    check(sum(_clean_split["diagnostic"]["by_kind"].values())
          == _clean_split["diagnostic"]["records"],
          "判据没判的记录数 == 按轴计数之和（第三个数自己内部也对得上）")
    _fam_md = CRP.render_cited_preview_markdown(preview)
    check("族：事实安全" in _fam_md and "（`unsourced_number_surface`）" in _fam_md,
          "**行为**：预览逐句行印出**族 + 中文理由 + 原因码**三件，读者不必按码反推性质")
    check("族：栏目覆盖" not in _fam_md,
          "**反例**：这一稿没有栏目覆盖失败，预览里就不许出现那个族名（不印空族充数）")
    check(CRP.classify_check_report(check_report)["sentences"] == _split["sentences"],
          "分族是**纯函数读数**：同输入 ⇒ 同结果（现算，不落盘、不进身份体，"
          "因此不作废任何历史 `sentence_checks.json`）")
    details.append("NOTE §21：分族**不改任何判据**、不改任何状态轴——它只把已经封闭的轴词表"
                   "再分一次类，好让「事实安全错误 3 句」与「栏目覆盖失败 4 句」**分开说**。"
                   "控制器侧车的 `mechanical` 键仍只有 `blocked_sentence_count`（= 事实安全族），"
                   "因此**没有**形状变化、**不**需要升 `m4cc-*`，更**不需要**任何一次真实 "
                   "M930-4 运行：`scripts/run_m930_4_assurance.py` 是**离线确定性侧车生成器**，"
                   "它一次模型调用都不发，只从已落盘的 run 与报告级审阅记录重算。若将来真要把"
                   "分族计数塞进控制器形状，代价是「升版 + 在新目录里重出一份侧车并核对自动"
                   "扫描只认当前唯一版本」，仍然不含任何模型调用。")

    # ===== §22 两级字段不得混填；提示词也不得把它们并列（`crr-11`）
    details.append("## §22 `category` 与 `semantic_category` 不得混填：合法的照常解析，越界的照旧作废")
    #: 动因是一次真实双节 run（`m930_3_cited_upload_20261005T161431Z`）：公司节审阅回复第 16 条
    #: 把 `off_topic` 填进了 `category`，`AS.ReviewIssue.create` 当场拒收 ⇒
    #: `review_reply_unparsable`，**该节每一句**的独立审阅结论一并丢失、整轮 `run_outcome=failed`。
    #: **解析器拒收是正确行为**：`category`（四粗类）与 `semantic_category`（九句义类）是 §7.3 的
    #: 两条正交轴，合法配对由 `REVIEW_CATEGORY_BY_SEMANTIC` 定死。本段钉两件事：
    #:   (a) **行为**：合法配对照常解析；越界 / 互换 / 漏 `suggested_target` / 空 `category` 一律
    #:       fail-closed，且**带句子与引用定位**，绝不猜成 `supported`；
    #:   (b) **提示词资产关系**：粗类取值清单行里不得混入句义类名；两级名不得以「或」并列。
    #: 两段都**不**钉提示词的任何一句措辞——改的是措辞，钉的是关系与行为。
    check(AS.REVIEW_CATEGORY_BY_SEMANTIC.get("off_topic") == "missing_content"
          and "off_topic" not in AS.REVIEW_CATEGORIES
          and "off_topic" in AS.REVIEW_SEMANTIC_CATEGORIES,
          "前提：冻结的两条轴把 `off_topic` 定成**句义类**，其唯一合法粗类是 `missing_content`"
          "——`off_topic` 从来不是、也不该被当成 `category` 的取值")

    #: (a1) 合法三元组：粗类 `missing_content` + 句义类 `off_topic` + `suggested_target` 指回要求。
    #: 这正是 `crr-11` 让提示词写清的配对；它在构造期本来就合法，这里把「提示词写的配对」与
    #: 「schema 收的配对」钉在一起——两边对同一件事必须给出同一个答案。
    _legal_pair = _review_json(
        _issue_json("s1", "m01"),
        _issue_json("s2", "m02", category="missing_content", semantic="off_topic",
                    severity="medium", blocking=True,
                    reason="这一段声称答的是销售模式，这句话写的是收入地区构成",
                    target={"target_kind": "contract_aspect", "target_ref": "aspect-x"}),
        _issue_json("s3", "f01"))
    try:
        _pair_outcome = _parse(_legal_pair, draft=draft, bundle=bundle,
                               report_version=_REPORT_VERSION)
    except Exception as exc:                                             # noqa: BLE001
        check(False, f"**正例前件**：`missing_content` + `off_topic` + `suggested_target` "
                     f"本该照常解析，却抛了 {type(exc).__name__}：{exc}")
    else:
        _pair_issue = next(i for i in _pair_outcome.issues if i.sentence_id == "s2")
        check(_pair_issue.category == "missing_content"
              and _pair_issue.semantic_category == "off_topic"
              and _pair_issue.suggested_target is not None,
              "**正例**：合法的两级配对解析后**逐字**保留两个字段"
              "（粗类仍是 `missing_content`、句义类仍是 `off_topic`，`suggested_target` 在场）"
              "——系统不改写它的粗类，也不把句义类压成空串")

    #: (a2) 反例四种形状。前两种是**同一件事的两面**：把两级名填错槽位。
    _pair_negatives = (
        ("`category` 直接写了句义类名 `off_topic`（真实失败形状）",
         _issue_json("s2", "m02", category="off_topic", semantic="off_topic",
                     severity="medium", blocking=True, reason="这一段答非所问",
                     target={"target_kind": "contract_aspect", "target_ref": "aspect-x"}),
         "off_topic"),
        ("两级名互换：`semantic_category` 写了粗类取值 `missing_content`",
         _issue_json("s2", "m02", category="missing_content", semantic="missing_content",
                     severity="medium", blocking=True, reason="这一段答非所问",
                     target={"target_kind": "contract_aspect", "target_ref": "aspect-x"}),
         "missing_content"),
        ("两级名都对，却漏了 `missing_content` 必需的 `suggested_target`",
         _issue_json("s2", "m02", category="missing_content", semantic="off_topic",
                     severity="medium", blocking=True, reason="这一段答非所问"),
         "suggested_target"),
        ("`category` 写成空串",
         _issue_json("s2", "m02", category="", semantic="off_topic",
                     severity="medium", blocking=True, reason="这一段答非所问",
                     target={"target_kind": "contract_aspect", "target_ref": "aspect-x"}),
         "category"),
    )
    for _name, _row, _token in _pair_negatives:
        _reply = _review_json(_issue_json("s1", "m01"), _row, _issue_json("s3", "f01"))
        _e = _exc(lambda r=_reply: _parse(r, draft=draft, bundle=bundle,
                                          report_version=_REPORT_VERSION))
        check(_e.reason == "review_reply_unparsable"
              and _e.sentence_id == "s2" and _e.citation_id == "m02",
              f"**反例**：{_name} ⇒ 整次返回作废，原因码 `{_e.reason}`，"
              f"并**带定位**（句 `{_e.sentence_id}`／引用 `{_e.citation_id}`）"
              "——不返回半份意见，也绝不把这一条猜成 `supported`")
        check(_token in str(_e),
              f"**反例**：{_name} 的异常消息点名 `{_token}`（不是一句无字段诊断的 schema 报错）")
        check(not isinstance(_e, AS.AssuranceSchemaError),
              f"**反例**：{_name} 抛出的是链自己的 typed 异常，"
              "不是裸的 `AssuranceSchemaError`——裸的那个不带 `sentence_id`／`citation_id`")

    #: (b) 提示词资产关系。**只钉关系，不钉措辞**：粗类取值清单行里不得出现句义类名，
    #: 两级名也不得以「或」直接并列——原文 7e 那句「按 `off_topic` 或 `missing_content` 提意见」
    #: 正是「把两级字段读成同一层可选值」的措辞形状，而它正是 `category='off_topic'` 的来源。
    _prompt_lines = real_review_prompt.splitlines()
    _cat_enum_lines = [ln for ln in _prompt_lines if '"category"' in ln]
    check(bool(_cat_enum_lines),
          "前提：提示词里有以 JSON 键 `\"category\"` 明写的取值清单行")
    for _ln in _cat_enum_lines:
        for _sem in AS.REVIEW_SEMANTIC_CATEGORIES:
            check(_sem not in _ln,
                  f"**反例**：`\"category\"` 的取值清单行里不得出现句义类名 `{_sem}`"
                  "（那一行只能列那四个粗类；句义类名混进去，模型就会照着往 `category` 里填）："
                  f"{_ln.strip()!r}")
    _joined: list[tuple[str, str, str]] = []
    for _ln in _prompt_lines:
        for _sem in AS.REVIEW_SEMANTIC_CATEGORIES:
            for _coarse in AS.REVIEW_CATEGORIES:
                for _sep in (" 或 ", "或", " or "):
                    for _a, _b in ((_sem, _coarse), (_coarse, _sem)):
                        if f"`{_a}`{_sep}`{_b}`" in _ln:
                            _joined.append((_a, _b, _ln.strip()))
    check(not _joined,
          "**反例**：提示词里不得把句义类名与粗类名以「或」直接并列成二选一"
          "（那会把**两级**字段读成同一层可选值）：" + repr(_joined))
    details.append("NOTE §22：本段钉的是「合法配对照常解析 / 越界与互换照旧作废**且带定位**」"
                   "这条**行为**，以及「粗类清单里没有句义类名、两级名不并列」这条**资产关系**。"
                   "它证明的是合约接线与拒收行为正确，**不**保证真实模型下次一定遵守——"
                   "提示词改动离线证不了。")

    # ===== §23 `contract_aspect` 的 `target_ref` 是栏目身份名，不是要求原文（`crr-12`）
    details.append("## §23 `contract_aspect` 的 `target_ref`：请求面里真的出现的 `aspect_id`，"
                   "不是那条要求的原文")
    #: 动因是**零模型只读**就能看见的一处不一致：`SuggestedTarget` 的词表把 `contract_aspect`
    #: 定义成「指向应该补哪个 Contract aspect」，`assurance/schema.py` 的类注释写着 `target_ref`
    #: 「只能是身份引用或 aspect 名」；请求面**已经**把 `aspect_id` 摆在模型眼前
    #: （`subsections[].aspect_requirements[].aspect_id`）；读者面也按身份名在用
    #: （`scripts/cited_demo_app.py` 的「建议补到」列直接印 `target_ref`）。但 `crr-11` 的三处
    #: 占位符写的都是「**那条要求**」——照它落笔最自然的就是粘一段要求原文。本段钉三件事：
    #:   (a) **请求面**：对位表逐行给出 `aspect_id` 与 `requirement_text`，身份名**在场**；
    #:   (b) **行为**：`target_ref` 取真身份名时逐字保留；空串 / 词表外的 `target_kind` 照旧
    #:       fail-closed 且带定位；
    #:   (c) **资产关系**：提示词里凡出现 `contract_aspect` 的那一行，**必须**同时点名
    #:       `aspect_id`（否则那句话没说清身份名从哪来）。
    #: **本段证明不了**：`SuggestedTarget.from_dict` 只校验非空，**填一段要求原文或一个自造名
    #: 解析器照收**。因此这里**不**声称「`target_ref` 一定是真 aspect_id」已被系统强制——
    #: 本段钉的是提示词取向与合约边界，不是一条新的门。
    check("contract_aspect" in AS.SUGGESTED_TARGET_KINDS
          and "contract_aspect" not in AS.REVIEW_UNIT_KINDS,
          "前提：`contract_aspect` 是给 `missing_content` 用的**建议去向**，"
          "不在被审核单元词表里——它指向一栏 Contract 要求，不指向一段正文")

    _pair_spec = CW.CitedSubsectionSpec(
        subsection_id="sub-pair", title="夹具：两栏对位",
        requirement_text="第一条要求的原文。\n第二条要求的原文。",
        declared_aspect_ids=("aspect.alpha", "aspect.beta"))
    _pair_context = T._writer_material_context(authority.pack_set, task_id=str(task.task_id),
                                               section_id="company")
    try:
        _pair_manifest = CW.build_cited_writer_input(
            authority=authority, material_context=_pair_context, subsections=(_pair_spec,),
            facts=facts, section_title="夹具：两栏对位")
        _pair_draft = CW.CitedProseDraft.create(
            task_id=_pair_manifest.task_id, section_id=_pair_manifest.section_id,
            input_manifest_id=_pair_manifest.manifest_id, writer_identity=_WRITER,
            subsections=(CW.CitedSubsection(
                subsection_id="sub-pair", title="夹具：两栏对位",
                paragraphs=(CW.CitedParagraph(
                    paragraph_id="p1", aspect_ids=("aspect.alpha",),
                    sentences=(CW.CitedSentence("s1", _BODY_A, ()),)),)),))
        _pair_req = CR.build_cited_review_request(draft=_pair_draft, manifest=_pair_manifest)
    except Exception as exc:                                             # noqa: BLE001
        check(False, f"**正例前件**：两栏对位的小节本该能装配出审阅请求面，却抛了 "
                     f"{type(exc).__name__}：{exc}")
    else:
        _req_pairs = _pair_req.payload["subsections"][0]["aspect_requirements"]
        check(_req_pairs == [{"aspect_id": "aspect.alpha", "requirement_text": "第一条要求的原文。"},
                             {"aspect_id": "aspect.beta", "requirement_text": "第二条要求的原文。"}],
              "**正例**：对位表**逐行**同时给出 `aspect_id` 与那条要求的原文"
              f"（实测 {_req_pairs}）——`target_ref` 该取的**身份名**就在请求面里，"
              "不需要模型自己发明，也不需要它去粘一段中文")
        check([r["aspect_id"] for r in _req_pairs] == list(_pair_spec.declared_aspect_ids),
              "**正例**：对位表里的 `aspect_id` 逐字就是该小节声明的栏目身份"
              "（不是要求文本的摘要、不是编号、不是中文占位）")
        _mismatch_spec = CW.CitedSubsectionSpec(
            subsection_id="sub-mismatch", title="夹具：对位不成立",
            requirement_text="只有一条要求的原文。",
            declared_aspect_ids=("aspect.alpha", "aspect.beta"))
        try:
            _mismatch_manifest = CW.build_cited_writer_input(
                authority=authority, material_context=_pair_context,
                subsections=(_mismatch_spec,), facts=facts, section_title="夹具：对位不成立")
            _mismatch_draft = CW.CitedProseDraft.create(
                task_id=_mismatch_manifest.task_id, section_id=_mismatch_manifest.section_id,
                input_manifest_id=_mismatch_manifest.manifest_id, writer_identity=_WRITER,
                subsections=(CW.CitedSubsection(
                    subsection_id="sub-mismatch", title="夹具：对位不成立",
                    paragraphs=(CW.CitedParagraph(
                        paragraph_id="p1", sentences=(CW.CitedSentence("s1", _BODY_A, ()),)),)),))
            _mismatch_req = CR.build_cited_review_request(draft=_mismatch_draft,
                                                          manifest=_mismatch_manifest)
        except Exception as exc:                                         # noqa: BLE001
            check(False, f"**反例前件**：对位不成立的小节本该仍能装配请求面，却抛了 "
                         f"{type(exc).__name__}：{exc}")
        else:
            check(_mismatch_req.payload["subsections"][0]["aspect_requirements"] == [],
                  "**反例**：要求行数与声明栏数不等时对位表留空——「取不到 `aspect_id`」这个"
                  "分支真的存在，所以提示词必须给它一条确定的退路，而不是让模型猜一个栏目名")

    #: (b) 行为：真身份名逐字保留；空串与词表外的 `target_kind` 照旧 fail-closed 且带定位。
    _ident_reply = _review_json(
        _issue_json("s1", "m01"),
        _issue_json("s2", "m02", category="missing_content", semantic="off_topic",
                    severity="medium", blocking=True,
                    reason="这一段声称答的是销售模式，这句话写的是收入地区构成",
                    target={"target_kind": "contract_aspect",
                            "target_ref": "company_business_main.sales_mode"}),
        _issue_json("s3", "f01"))
    try:
        _ident_outcome = _parse(_ident_reply, draft=draft, bundle=bundle,
                                report_version=_REPORT_VERSION)
    except Exception as exc:                                             # noqa: BLE001
        check(False, f"**正例前件**：`contract_aspect` 配上真身份名本该照常解析，却抛了 "
                     f"{type(exc).__name__}：{exc}")
    else:
        _ident_issue = next(i for i in _ident_outcome.issues if i.sentence_id == "s2")
        check(_ident_issue.suggested_target is not None
              and _ident_issue.suggested_target.target_ref
              == "company_business_main.sales_mode",
              "**正例**：`target_ref` 填栏目身份名时**逐字**保留"
              "——系统不改写成要求原文、也不压成空串")
    _target_negatives = (
        ("`target_ref` 是空串",
         _issue_json("s2", "m02", category="missing_content", semantic="off_topic",
                     severity="medium", blocking=True, reason="这一段答非所问",
                     target={"target_kind": "contract_aspect", "target_ref": ""}),
         "target_ref"),
        ("`target_kind` 写成词表外的 `requirement`（把「去向」写成了「要求」）",
         _issue_json("s2", "m02", category="missing_content", semantic="off_topic",
                     severity="medium", blocking=True, reason="这一段答非所问",
                     target={"target_kind": "requirement", "target_ref": "aspect.alpha"}),
         "requirement"),
    )
    for _name, _row, _token in _target_negatives:
        _reply = _review_json(_issue_json("s1", "m01"), _row, _issue_json("s3", "f01"))
        _e = _exc(lambda r=_reply: _parse(r, draft=draft, bundle=bundle,
                                          report_version=_REPORT_VERSION))
        check(_e.reason == "review_reply_unparsable"
              and _e.sentence_id == "s2" and _e.citation_id == "m02",
              f"**反例**：{_name} ⇒ 整次返回作废，原因码 `{_e.reason}`，并**带定位**"
              f"（句 `{_e.sentence_id}`／引用 `{_e.citation_id}`）")
        check(_token in str(_e),
              f"**反例**：{_name} 的异常消息点名 `{_token}`")

    #: (c) 资产关系：凡 `contract_aspect` 出现的那一行必须同时点名 `aspect_id`。
    _ca_lines = [ln for ln in real_review_prompt.splitlines() if "contract_aspect" in ln]
    check(len(_ca_lines) >= 3,
          f"前提：提示词里有≥3 处以 `contract_aspect` 给出建议去向（实测 {len(_ca_lines)} 行）")
    for _ln in _ca_lines:
        check("aspect_id" in _ln,
              "**反例**：`contract_aspect` 出现的那一行必须同时点名 `aspect_id`"
              "（只写「那一条要求」，模型最自然的落笔就是粘一段要求原文）："
              f"{_ln.strip()!r}")
    details.append("NOTE §23：本段钉的是「请求面里身份名在场」「真身份名逐字保留」"
                   "「两处 fail-closed 带定位」与「`contract_aspect` 行必须点名 `aspect_id`」"
                   "这四条**关系与行为**，不钉任何一句措辞。**它不构成一条新的门**："
                   "`SuggestedTarget.from_dict` 只校验非空，要求原文与自造名照收——"
                   "要真强制 `target_ref` 一定是请求面里的真 aspect_id，得另立下游核对（本批不含）。")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

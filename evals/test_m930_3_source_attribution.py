"""M930-3 来源归属读者面（指令 D §二（b），`srattr-1`）。

跑法（无管道/无重定向）：`PYTHONIOENCODING=utf-8 python -m evals.test_m930_3_source_attribution`

本模块只测一件事：**读者凭什么知道这句话说的是哪一份材料、哪个版本、哪一页，那份材料的披露日
是哪天**。因此它钉住四面，每一面都是「不做就会静默错」的那一面：

1. **归属语只由系统渲染**。行源是 `material_pack` 的**同一次**读数（不重读 Pack、不从 Writer
   prompt 倒抄），披露日只来自 `source_manifest.entries` 的活对象（由
   `harness/source_manifest.py` 判过一次「可不可核实」），本模块不另判一份。
2. **逐条跳过，且有 typed 原因**。三种来由意思完全不同，必须分得开：登记表里没有这一份
   （`no_registered_entry`）/ 登记数据自身不合规（`entry_not_conforming`，**不**洗成
   `unknown`）/ locator 没有精确页码（`page_not_precise`，不写「约第几页」）。一条跳过不影响
   其它条渲染出来。
3. **不改正文、不改正文指纹**。归属语进的是独立产物（`source_attribution.json` / `.md`），
   正文仍由唯一渲染器产出并被组装器逐字节重算比对。这一条用**源码对账**钉住：runner 里没有任何
   一处把归属语拼进 `NarrativeSentence` / 正文渲染调用。
4. **「多版本无期间」被标注出来**。一句话由两个不同**文档版本**共同支撑、而 Claim 与其引用都
   不带期间时，产物里必须有那一句标注——这正是 `§二 2` 要防的读法（不能用相似文本抹平新旧材料
   的实质差异，也不能让读者读成「一直如此」）。标注**不是**判据结论，本模块也不把它当判据。

夹具全是合成对象：不读真实库、不建真实 Pack、不调 LLM。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation import run_m930_3_acceptance as ACC  # noqa: E402
from harness import source_manifest as SM  # noqa: E402
from sections import source_attribution as SRA  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


class _NS:
    """只读命名空间替身（产物函数只按属性名读取容器，不要求真 Pack/真库对象）。"""

    def __init__(self, **kw) -> None:
        self.__dict__.update(kw)


# ---------------------------------------------------------------------------
# 夹具：一份 2025 年报（披露日不可核实，封面只给月粒度）+ 一份 2026 募集说明书（无登记页码）
# ---------------------------------------------------------------------------

def _entry(document_id, document_version, source_name, disclosure):
    return _NS(document_id=document_id, document_version=document_version,
               source_name=source_name, disclosure=disclosure,
               eligibility="eligible_current")


MONTH_ONLY = SM.DisclosureDateState(
    date=None, state="unknown", period_hint="2026-03", period_hint_precision="month",
    basis="封面仅给出月粒度；入库时间 / PDF 元数据不得冒充披露日",
    ingestion_time="2026-09-26T03:46:21Z")


def _claim(claim_id, binding_ids, periods=()):
    return _NS(claim_id=claim_id, accepted_binding_ids=list(binding_ids),
               citation_refs=[_NS(period=p) for p in periods])


def _sentence(sentence_id, index, kind, text, claim_ids):
    return _NS(sentence_id=sentence_id, index=index, sentence_kind=kind, text=text,
               claim_ids=list(claim_ids))


def _bindings(material_ids):
    return [_NS(accepted_support_binding_id=f"asb_{i}", material_id=mid)
            for i, mid in enumerate(material_ids)]


def _state():
    """合成 RunState：两个节，第二节的一句由**两个不同文档版本**共同支撑且都不带期间。"""
    company_bindings = _bindings(["mat-2025-1", "mat-2026-1"])
    company = _NS(
        claims=[_claim("clm_c1", ["asb_0"]), _claim("clm_c2", ["asb_1"])],
        acceptance=_NS(accepted_bindings=company_bindings),
        narrative=_NS(paragraphs=[
            _NS(paragraph_id="npar_c1", sentences=[
                _sentence("nsen_c1", 1, "composed", "公司从事动力电池系统的研发与销售。",
                          ["clm_c1"]),
                _sentence("nsen_c2", 2, "composed", "公司生产储能电池系统。", ["clm_c2"]),
            ])]),
    )
    industry_bindings = _bindings(["mat-2025-1", "mat-2026-1"])
    industry = _NS(
        claims=[_claim("clm_i1", ["asb_0", "asb_1"])],
        acceptance=_NS(accepted_bindings=industry_bindings),
        narrative=_NS(paragraphs=[
            _NS(paragraph_id="npar_i1", sentences=[
                _sentence("nsen_i1", 1, "composed", "行业需求保持增长。", ["clm_i1"]),
            ])]),
    )
    inputs = _NS(source_manifest=_NS(entries=[
        _entry("NDSD_2025_year", "sha256-c1", "NDSD_2025_year.pdf", MONTH_ONLY),
        _entry("NDSD_KCZ_2026", "sha256-k1", "NDSD_KCZ_2026.pdf", MONTH_ONLY),
    ]))
    return _NS(inputs=inputs, sections={"company": company, "industry": industry})


def _material_pack():
    def entry(material_id, document_id, document_version, page, source_name):
        return {"material_id": material_id,
                "document_identity": {"document_id": document_id,
                                      "document_version": document_version,
                                      "evidence_set_version": "set-1"},
                "locator": {"document_id": document_id, "page": page,
                            "source_name": source_name}}
    return {"sections": {
        "company": {"entries": [
            entry("mat-2025-1", "NDSD_2025_year", "sha256-c1", 16, "NDSD_2025_year.pdf"),
            entry("mat-2026-1", "NDSD_KCZ_2026", "sha256-k1", 3, "NDSD_KCZ_2026.pdf"),
        ]},
        # 这一条 locator 没有精确页码 ⇒ 必须**逐条**跳过并记 `page_not_precise`。
        "industry": {"entries": [
            entry("mat-nopage", "NDSD_2025_year", "sha256-c1", None, "NDSD_2025_year.pdf"),
            entry("mat-2025-2", "NDSD_2025_year", "sha256-c1", 19, "NDSD_2025_year.pdf"),
        ]},
    }}


STATE = _state()
PACK = _material_pack()
PAYLOAD = ACC._source_attribution_payload(STATE, PACK)


# ============================================================ §1 版本与词表 pin

check(ACC.SOURCE_ATTRIBUTION_ARTIFACT_SCHEMA_VERSION == "m930-3-source-attribution-1",
      "产物载荷形状版本必须登记（字段增删必须改这个号）")
check(PAYLOAD["attribution_version"] == SRA.SOURCE_ATTRIBUTION_VERSION,
      "产物必须自报它用的归属语口径版本（口径变了，读法就变了）")
check(PAYLOAD["skip_reason_codes"] == list(ACC.SOURCE_ATTRIBUTION_SKIP_REASONS),
      "产物必须把逐条跳过的原因码词表一并落盘（复核者不必回头读源码）")
check(set(ACC.SOURCE_ATTRIBUTION_SKIP_REASONS)
      == {"no_registered_entry", "entry_not_conforming", "page_not_precise"},
      "三种「没渲染出归属语」的来由必须各有自己的码，不得并成一类")
check(PAYLOAD["disclosure_unknown_label"] == SRA.DISCLOSURE_UNKNOWN_LABEL,
      "不可核实时的固定表述必须由唯一实现给出，产物不另写一份")
check("ingestion_time" in PAYLOAD["forbidden_disclosure_substitutes"]
      and "content_report_period_end" in PAYLOAD["forbidden_disclosure_substitutes"],
      "被禁的披露日替代物必须逐条点名落盘（入库时间 / 财务期末都不得冒充披露日）")
check(PAYLOAD["status"] == "readback" and PAYLOAD["sections_unavailable"] == [],
      "两节定稿读数都取得到时必须自报 `readback` 且无读不回来的节")
check(PAYLOAD["sections_readback"] == ["company", "industry"],
      "读回的节必须逐个点名（不是只给一个计数）")
check(PAYLOAD["section_unavailable_reason_codes"]
      == list(ACC.SOURCE_ATTRIBUTION_SECTION_UNAVAILABLE_REASONS)
      and "section_not_readable" in ACC.SOURCE_ATTRIBUTION_SECTION_UNAVAILABLE_REASONS,
      "「这一节读不回来」的原因码必须与「这一份材料渲染不出归属语」的三个码**分开**："
      "两者不是一回事")

# ============================================================ §2 归属语渲染

rendered = {m["material_id"]: m for m in PAYLOAD["materials"]}
check(set(rendered) == {"mat-2025-1", "mat-2026-1", "mat-2025-2"},
      f"能渲染归属语的材料必须恰好是 locator 有精确页码且登记在册的那几条（实际 "
      f"{sorted(rendered)}）")
check(rendered["mat-2025-1"]["disclosure_state"] == "unknown"
      and rendered["mat-2025-1"]["disclosure_date"] == "",
      "封面只给月粒度 ⇒ 披露状态必须是 `unknown` 且**不带**日期")
check(SRA.DISCLOSURE_UNKNOWN_LABEL in rendered["mat-2025-1"]["rendered"],
      "不可核实必须渲染成「披露日未知」")
check("2026-03" not in rendered["mat-2025-1"]["rendered"],
      "月粒度线索不得出现在归属语里（它没有升格成披露日）")
check("NDSD_2025_year@sha256-c1" in rendered["mat-2025-1"]["rendered"]
      and "第 16 页" in rendered["mat-2025-1"]["rendered"],
      "归属语必须带出**登记身份@版本**与**精确页码**——读者据此才知道这是哪一版哪一页")
check(rendered["mat-2025-1"]["attribution_id"].startswith("srattr_"),
      "归属语必须有内容寻址身份（同一份材料在两次运行里渲染出同一条）")

skipped = {s["material_id"]: s["reason_code"] for s in PAYLOAD["skipped_materials"]}
check(skipped == {"mat-nopage": "page_not_precise"},
      f"没有精确页码的材料必须逐条跳过并记 typed 原因（实际 {skipped}）")
check("mat-nopage" not in rendered,
      "跳过的材料**不得**出现在归属语表里（不造半条缺页码的归属语）")

# 登记表里没有 / 登记数据不合规：两个码必须**分得开**（不合并、不洗成 unknown）
STATE_MISSING = _state()
STATE_MISSING.inputs.source_manifest.entries = [
    _entry("NDSD_2025_year", "sha256-c1", "NDSD_2025_year.pdf", MONTH_ONLY)]
PAYLOAD_MISSING = ACC._source_attribution_payload(STATE_MISSING, PACK)
codes_missing = {s["material_id"]: s["reason_code"]
                 for s in PAYLOAD_MISSING["skipped_materials"]}
check(codes_missing.get("mat-2026-1") == "no_registered_entry",
      f"不在登记表里的材料必须记 `no_registered_entry`（实际 {codes_missing}）")
check("mat-2026-1" not in {m["material_id"] for m in PAYLOAD_MISSING["materials"]},
      "登记表里没有的材料不得渲染出归属语（不猜一个披露日）")

BROKEN = SM.DisclosureDateState(
    date=None, state="unknown", period_hint=None, period_hint_precision=None,
    basis="不知道", ingestion_time="2026-09-26T03:46:21Z")  # 缺「入库时间 / 不得冒充」
STATE_BROKEN = _state()
STATE_BROKEN.inputs.source_manifest.entries = [
    _entry("NDSD_2025_year", "sha256-c1", "NDSD_2025_year.pdf", BROKEN),
    _entry("NDSD_KCZ_2026", "sha256-k1", "NDSD_KCZ_2026.pdf", MONTH_ONLY)]
PAYLOAD_BROKEN = ACC._source_attribution_payload(STATE_BROKEN, PACK)
codes_broken = {s["material_id"]: s["reason_code"]
                for s in PAYLOAD_BROKEN["skipped_materials"]}
check(codes_broken.get("mat-2025-1") == "entry_not_conforming",
      f"登记数据自身不合规必须记 `entry_not_conforming`（实际 {codes_broken}）")
check("mat-2026-1" in {m["material_id"] for m in PAYLOAD_BROKEN["materials"]},
      "一条登记数据坏了不得让其余材料一起消失（跳过是**逐条**的）")

# ============================================================ §3 逐句归属

sentences = {(s["section_id"], s["paragraph_id"], s["index"]): s
             for s in PAYLOAD["sentences"]}
check(set(sentences) == {("company", "npar_c1", 1), ("company", "npar_c1", 2),
                         ("industry", "npar_i1", 1)},
      f"逐句表必须覆盖每一句带材料支撑边的正文句（实际 {sorted(sentences)}）")
first = sentences[("company", "npar_c1", 1)]
check(first["material_ids"] == ["mat-2025-1"] and first["claim_ids"] == ["clm_c1"],
      "逐句归属必须按「句 → Claim → 采信边 → 材料」走，不得按材料反查")
check(first["attribution_texts"]
      == [rendered["mat-2025-1"]["rendered"]],
      "逐句表里的归属语必须与逐材料表**逐字相同**（同一次读数、同一个渲染器）")
check(first["multi_version_without_period"] is False,
      "单版本支撑的句子不得被标成「多版本无期间」")

shared = sentences[("industry", "npar_i1", 1)]
check(shared["multi_version_without_period"] is True,
      "一句话由两个不同**文档版本**共同支撑、而 Claim 与其引用都不带期间时，"
      "必须标注出来——这正是 `§二 2` 要防的「不能读成一直如此」")
check(shared["document_versions"] == ["NDSD_2025_year@sha256-c1", "NDSD_KCZ_2026@sha256-k1"],
      "标注必须带出到底是哪几个版本（只给一个计数读不出是哪两份）")
check(shared["declared_periods"] == [],
      "该句声明里确实没有期间——标注的前提是「不带期间」，不是「我们没去查」")

with_period = _state()
with_period.sections["industry"].claims[0].citation_refs = [_NS(period="2025-12-31")]
PAYLOAD_PERIOD = ACC._source_attribution_payload(with_period, PACK)
period_sentence = [s for s in PAYLOAD_PERIOD["sentences"]
                   if s["section_id"] == "industry"][0]
check(period_sentence["multi_version_without_period"] is False,
      "声明了期间的句子不得被标成「多版本无期间」（标错会把正常句子读成缺陷）")

# ============================================================ §4 人读渲染

MD = ACC._source_attribution_md(PAYLOAD)
check("来源归属" in MD and "披露日未知" in MD,
      "人读版必须出现归属语与「披露日未知」")
check("2026-03" not in MD,
      "人读版里不得出现月粒度线索（它没有升格成披露日）")
check("`page_not_precise`" in MD,
      "人读版必须逐条印出跳过原因码，而不是只印一句「有材料没渲染出来」")
check(f"**{len(PAYLOAD['sentences'])}**" in MD,
      "人读版必须给出正文句总数（读的人要知道这份归属账覆盖了多少句）")
check("一直如此" in MD and "不改正文" in MD,
      "人读版必须自己说清楚它**不是**「截至报告生成日仍然如此」的凭据，也不改正文")

# ============================================================ §5 接线与不变量

check("source_attribution.md" in ACC.ARTIFACTS and "source_attribution.json" in ACC.ARTIFACTS,
      "两个产物必须进 ARTIFACTS（否则不进 `artifact_index.json` 的逐文件 sha256 账）")

RUNNER_SRC = (REPO / "evaluation" / "run_m930_3_acceptance.py").read_text(encoding="utf-8")
check('_write_text(state.run_dir / "source_attribution.md"' in RUNNER_SRC
      and '_write_json(state.run_dir / "source_attribution.json"' in RUNNER_SRC,
      "`_write_run` 必须真的落盘这两个产物（写了函数却不接线＝没有这个产物）")
check('"source_attribution": {' in RUNNER_SRC,
      "报告侧必须有 `source_attribution` 指针块（拒绝路径/正常路径同一份报告形状）")
check('_source_attribution_payload(state, material_pack)' in RUNNER_SRC,
      "归属语必须与材料包读回**共用同一次读数**：单独再读一次 Pack，"
      "「哪一页」两处迟早会对不上")
check(RUNNER_SRC.count("_source_attribution_payload(") == 2,
      "`_source_attribution_payload` 必须恰好 1 处定义 + 1 处调用")

# 不改正文：runner 不得重建正文的来源索引、不得自己调正文渲染器，也不得改写句文本。
check("citation_source_index" not in RUNNER_SRC,
      "runner 不得重建正文的来源索引（那是 `company_worker` / `report_assembler` 的唯一入口；"
      "在 runner 里再建一份就会造出第二个正文来源面）")
for needle in ("render_final_narrative_markdown(", "render_paragraphs_markdown("):
    check(needle not in RUNNER_SRC,
          f"runner 不得自己调正文渲染器 {needle}（正文只有唯一生产者与唯一重算者）")
check(all(s["text"] == next(
              sent.text
              for para in STATE.sections[s["section_id"]].narrative.paragraphs
              if para.paragraph_id == s["paragraph_id"]
              for sent in para.sentences if sent.sentence_id == s["sentence_id"])
          for s in PAYLOAD["sentences"]),
      "逐句表里的 `text` 必须是原句**逐字副本**（归属语只在旁边，不得改写句文本）")
check(all(t not in s["text"] for s in PAYLOAD["sentences"]
          for t in s["attribution_texts"]),
      "归属语不得出现在句文本里（日期只允许出现在系统渲染的归属语那一列）")

# ============================================================ §6 逐节兜底（读回不许抛）

# 反面例：这一节的定稿读数有一块取不到（现场形状：夹具/读回写手的缺陷让 `.claims` 整个缺失）。
# 读回**不得**抛，也**不得**静默变成「本节一句事实句都没有」——那是把「读不回来」写成
# 「本来就没有」，是最容易被读成「本节无事实」的那种静默错。
STATE_BROKEN_SECTION = _state()
STATE_BROKEN_SECTION.sections["industry"] = _NS(llm_calls=0)
PAYLOAD_BROKEN_SECTION = ACC._source_attribution_payload(STATE_BROKEN_SECTION, PACK)
check(PAYLOAD_BROKEN_SECTION["status"] == "partial",
      "有节读不回来时整体状态必须是 `partial`，不得仍自报 `readback`")
check([u["section_id"] for u in PAYLOAD_BROKEN_SECTION["sections_unavailable"]] == ["industry"],
      "读不回来的节必须逐节点名（实际 "
      f"{PAYLOAD_BROKEN_SECTION['sections_unavailable']}）")
check(PAYLOAD_BROKEN_SECTION["sections_unavailable"][0]["reason_code"] == "section_not_readable"
      and set(PAYLOAD_BROKEN_SECTION["sections_unavailable"][0]["missing"])
      >= {"claims", "acceptance", "narrative"},
      "原因码必须是 `section_not_readable` 且**点名**到底哪几块取不到——"
      "只写「读不到」复核者无法定位")
check(PAYLOAD_BROKEN_SECTION["sections_readback"] == ["company"],
      "坏的节不得混进 `sections_readback`（两列必须互斥）")
check("本次逐句归属不完整" in PAYLOAD_BROKEN_SECTION["note"]
      and "sections_unavailable" in PAYLOAD_BROKEN_SECTION["note"],
      "载荷自述必须把「这次不完整」说出来，并指向 `sections_unavailable`——"
      "不许让空表自己冒充结论")
check([s["section_id"] for s in PAYLOAD_BROKEN_SECTION["sentences"]] == ["company", "company"],
      "一节坏掉不得让其余节的逐句归属跟着消失（兜底是**逐节**的）")
MD_BROKEN = ACC._source_attribution_md(PAYLOAD_BROKEN_SECTION)
check("读不回来" in MD_BROKEN and "`section_not_readable`" in MD_BROKEN,
      "人读版必须单列一节印出读不回来的节与原因码，不得只在下游计数里少一行")
check("读不回来**的节" in MD_BROKEN or "读不回来的节" in MD_BROKEN,
      "人读版这一节的标题必须自己说清那是「读不回来」而不是「本节没有事实句」")

# 入口本身也不许抛：整份 payload 构建若在别处炸了，也必须落一份明说「这次没读成」的载荷。
check(PAYLOAD_BROKEN_SECTION["skip_reason_codes"]
      == list(ACC.SOURCE_ATTRIBUTION_SKIP_REASONS),
      "逐节兜底不得把材料级的三个跳过原因码弄丢（两套词表并存，不是二选一）")

def main() -> dict:
    """套件入口：把本模块的检查结果交回运行器（`evals/run_evals.py`）。

    **检查体在导入期执行**（本模块是脚本式写法：夹具与 `check(...)` 都在模块顶层）。
    因此本函数**只交付结果，不得再跑一遍**——重复执行会把同一次运行数成两遍。

    **为什么必须存在这个函数**：运行器对每个模块先 `import_module` 再取 `mod.main()`。
    没有它，模块要么在导入期就 `AttributeError`（被记成 CRASH），要么——更糟——沿用一个
    **模块级**的 `sys.exit(...)`：`SystemExit` 是 `BaseException`，运行器的
    `except Exception` 接不住，套件会在**不打印 `TOTAL`** 的情况下以退出码 0 结束，
    它之后还没跑的模块静默消失，日志看起来仍像「基本跑完」。这条纪律与
    `evals/test_m930_3_r8_offline_replay.py` 钉的是同一条（那里也有一个反例）。
    """
    return _results


if __name__ == "__main__":
    print(json.dumps(_results, ensure_ascii=False, indent=2))
    sys.exit(1 if _results["failed"] else 0)

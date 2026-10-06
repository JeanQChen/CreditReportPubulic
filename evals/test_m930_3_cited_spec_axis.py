"""Eval: 写作**小节轴**必须落在冻结 WritingSpec 上（`_subsections_from_writing_spec`，`cwm-6`）。

用法: python -m evals.test_m930_3_cited_spec_axis

本模块钉的是 `R1` 那一条结构缺陷的**投影函数**，而不是它的下游产物：

那条链以前按 **Contract aspect** 造写作小节（一个 aspect 一个小节）。冻结 WritingSpec 里
`company_business*` 的栏目**全部**归属同一个 `co-h4`，`fin_solvency.*` 全部归属 `fin-h2`
（`templates/writing_specs/credit_report_v1.yaml`），于是「一个小节一条」被放大成十八个各写一句
的短栏，同一份材料被反复拿来填不同的小节。这是**小节轴造得比冻结规范更细**，不是取材不足；
把它当成「材料不够」去扩大检索，只会更歪。

本模块因此只问四件事，每一件都用**真实冻结投影 + 真实 WritingSpec 文件**回答，不手搭假表：

1. **轴来自哪一份**。小节 id / 标题 / 顺序逐条等于冻结 WritingSpec 的 TOC；标题是**从 TOC
   读出来的**（断言写成「等于 TOC 里那一条」，不抄一份字面标题进测试——抄了字面，将来资产
   改动就会被判红，而被判红的是资产更新，不是代码出错）。
2. **要求文本一个不删**。`requirement_text` 是该小节覆盖的每个 Contract 栏目要求文本的逐字
   拼接，按 Contract 侧次序，行数 = 栏目数，逐行相等。
3. **栏目责任仍在 Contract 那一侧**。`declared_aspect_ids` 与 Contract 的 aspect 有序集逐条
   相等；`allowed_source_classes` 是该组各栏目的 evidence requirement 来源类的并集，且**非空**
   ——空集在这里是**红旗**：它意味着投影读到的不是带 `source_classes` 的投影引用，而是别的
   形状（冻结 Contract 原始的 `evidence_requirement_ids` 是 `list[str]`，对字符串取
   `.source_classes` 会得到空元组而**不报错**）。这条断言就是那道静默降级的守卫。
4. **三处 fail-closed 真的会停**。指纹对不上（两处：声明值与重算值各一例）、契约栏目在
   WritingSpec 里没有归属小节、TOC 里缺该小节条目——三者都必须停住，且停的时候带说明。

不调 LLM、不联网、不写库、不建第二套 Harness/Pack/Writer；模块无公司代号、页码、表号或
固定答案关键词。断言全部**从真实资产与真实投影推导**，不钉提示词措辞。
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import sys
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts import schema_v2 as S2                                   # noqa: E402
from contracts.loader_v2 import load_contract_v2                        # noqa: E402
from harness import topic_schema as TS                                  # noqa: E402
from planning import demo_scope as DS                                   # noqa: E402
from planning import schema as PS                                       # noqa: E402
from scripts import run_m930_3_cited_chain as RUN                       # noqa: E402
from sections import writing_spec as WS                                 # noqa: E402


@dataclasses.dataclass(frozen=True)
class _FakeAspect:
    """只带投影函数**真正会读**的三个字段的栏目替身。

    不是为了省事：反例要构造「WritingSpec 里没有归属」的栏目，而真实投影里造不出这种栏目
    （真实投影正是从同一份 WritingSpec 映射出来的）。替身上只放被读的字段，是为了让「投影
    到底读了什么」这件事在夹具里一眼可见——它读 `aspect_id`、`requirement_text`、
    `evidence_requirement_ids`，不多读一个字段。
    """

    aspect_id: str
    requirement_text: str
    evidence_requirement_ids: tuple = ()


@dataclasses.dataclass(frozen=True)
class _FakeReq:
    """`_subsections_from_writing_spec` 只用 `requirement.aspects`。"""

    aspects: tuple


def _er_ref(requirement_id: str, source_classes: tuple[str, ...]) -> TS.EvidenceRequirementRef:
    """**真实**投影引用类型（不是替身）：`source_classes` 就是那条静默降级的观测点。"""
    return TS.EvidenceRequirementRef(
        requirement_id=requirement_id, contract_sha256="a" * 64,
        requirement_fingerprint="b" * 64, schema_version="1",
        source_classes=tuple(source_classes))


def _systemexit_of(fn) -> str | None:
    """跑 `fn`；停住了就返回它的说明文本，没停返回 None。

    只判「停没停 + 有没有给出说明」，**不**匹配说明的字面措辞：写法会变，规则不该变。
    """
    try:
        fn()
    except SystemExit as exc:
        return str(exc) or "<空说明>"
    return None


def _home_index(ws) -> dict[str, str]:
    """`aspect_id` → `subsection_id`，与投影函数同一读法（首见为准）。"""
    home: dict[str, str] = {}
    for row in ws.mappings:
        aspect_id = str(row.get("aspect_id") or "").strip()
        subsection_id = str(row.get("subsection_id") or "").strip()
        if aspect_id and subsection_id:
            home.setdefault(aspect_id, subsection_id)
    return home


def _toc_of(ws, section: str) -> list[dict]:
    return [e for e in (ws.toc.get(section) or ()) if isinstance(e, dict)]


def _h2_of(ws, section: str, subsection_id: str) -> str:
    for entry in _toc_of(ws, section):
        if str(entry.get("subsection_id") or "") == subsection_id:
            return str(entry.get("h2") or "")
    return ""


def _projection():
    """真实冻结投影（与运行入口同一份构造路径：profile → Contract → 阶段 A 投影）。"""
    profile = DS.load_demo_scope_profile(RUN.DEFAULT_PROFILE)
    contract = load_contract_v2(str(DS.REPO_ROOT / profile.contract_asset))
    business = PS.ReportJobInput(
        job_id="job_m930_3_spec_axis", company_id="c-demo", company_name="演示主体",
        credit_type="general", report_as_of="2026-03-31", contract_version="v2")
    source_inputs = {
        "case_input_id": "case_m930_3_spec_axis", "document_id": "doc-demo",
        "document_version": "sha256-demo", "raw_pdf_sha256": "0" * 64,
        "current_evidence_set_version": "evset-demo",
        "substrate_dependency_versions": {
            k: f"{k}-v1" for k in DS.SUBSTRATE_DEPENDENCY_KEYS},
        "external_policy_snapshot_id": None,
        "budget_policy_id": profile.budget_policy_id,
        "budget_policy_version": profile.budget_policy_version,
        "model_policy_id": "demo_model_policy_v1", "code_fingerprint": "1" * 64,
    }
    projection = DS.project_contract_v2_scope(
        contract, profile,
        DS.build_scope_input_manifest(profile, business, source_inputs))
    return profile, {r.topic_id: r for r in projection.requirements}


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

    def note(msg: str) -> None:
        details.append(f"NOTE {msg}")

    def skip(msg: str) -> None:
        nonlocal skipped
        skipped += 1
        details.append(f"SKIP: {msg}")

    # ============================================ §0 冻结 WritingSpec 与真实投影
    details.append("## §0 轴的那一份资产，与被投影的那一份 Contract")

    spec_path = DS.REPO_ROOT / RUN.WRITING_SPEC_PATH
    check(spec_path.exists(), f"冻结 WritingSpec 资产在场：{RUN.WRITING_SPEC_PATH}")
    ws = WS.load_writing_spec(str(spec_path))
    declared_fp = str((ws.raw or {}).get("content_sha256") or "")
    recomputed_fp = S2.content_fingerprint(ws.raw)
    check(declared_fp == RUN.WRITING_SPEC_CONTENT_SHA256
          and recomputed_fp == RUN.WRITING_SPEC_CONTENT_SHA256,
          f"写作小节轴落在**冻结那一份**上：声明指纹与重算指纹都等于脚本钉住的值 "
          f"（声明 {declared_fp[:12]}… / 重算 {recomputed_fp[:12]}…）——"
          "同一路径换一份内容，小节轴就会跟着换，所以这里比对的是内容不是路径")

    _, reqs = _projection()
    #: 观测面取**真实投影里公司节选中的那一个 topic**（默认 profile 的公司节只选一个），
    #: 不写死 topic 名：写死了，将来 profile 换一批 topic 就会测到空气。
    co_topics = tuple(t for t, r in reqs.items()
                      if any(str(getattr(a, "aspect_id", "")).startswith("company_")
                             for a in r.aspects))
    check(len(co_topics) == 1,
          f"默认 profile 的公司节选中恰好一个主题（实测 {list(co_topics)}）："
          "本模块的观测面就是它，不用名字挑")
    if not co_topics:
        note("§0 取不到公司节主题：后续各节没有观测面，本模块到此为止")
        return {"passed": passed, "failed": failed, "skipped": skipped,
                "details": details}
    req = reqs[co_topics[0]]

    home = _home_index(ws)
    prefixes = tuple(p for p, _ in RUN._SUBSECTION_SECTION_BY_PREFIX)
    unknown = sorted({sid for sid in home.values()
                      if not any(sid.startswith(p) for p in prefixes)})
    note(f"资产里还有一类小节 id 不落在正文章节前缀表 {list(prefixes)} 上：{unknown}——"
         "它们是 WritingSpec 的 Phase 5 输出目的地行（`synth_*` 那一批），"
         "**不是**正文小节。投影函数只在「某个被选中主题的栏目归到它名下」时才会去分节，"
         "所以它们进不进得来，取决于有没有栏目映过去——下面那条断言盯的正是这件事。")
    reachable_unknown = sorted({
        home[str(getattr(a, "aspect_id", "") or "")]
        for _t, r in reqs.items() for a in r.aspects
        if str(getattr(a, "aspect_id", "") or "") in home
        and not any(home[str(getattr(a, "aspect_id", "") or "")].startswith(p)
                    for p in prefixes)})
    check(not reachable_unknown,
          f"真实投影里**没有一个被选中的栏目**归到前缀表之外的小节（实测 {reachable_unknown}）："
          "分不出节就取不到该节的 TOC 顺序，投影函数在那种情况下会停住而不是猜一个顺序——"
          "所以这里要证的是「进不来」，不是「资产里没有这类 id」")

    # ============================================ §1 分组：栏目收成小节
    details.append("## §1 分组：写作单位是 WritingSpec 小节，责任单位仍是 Contract 栏目")

    subs = RUN._subsections_from_writing_spec(req)
    aspect_ids = tuple(str(getattr(a, "aspect_id", "") or "") for a in req.aspects)
    check(len(aspect_ids) == len(set(aspect_ids)),
          f"契约面这一 topic 的栏目 id 互不重复（{len(aspect_ids)} 条）")

    #: 期望分组与次序全部**从真实资产推导**：栏目按 Contract 次序归入各自的小节，小节按该节
    #: TOC 次序出场。
    expected_grouped: dict[str, list[str]] = {}
    for aid in aspect_ids:
        expected_grouped.setdefault(home[aid], []).append(aid)
    #: 期望的节次序 = **前缀表**声明的次序，不是「哪个栏目先出现」。这与生产函数同源，
    #: 也正是 §4 要单独证明的那条性质。
    in_play = {RUN._section_of_subsection(sid) for sid in expected_grouped}
    sections_in_play = [name for _p, name in RUN._SUBSECTION_SECTION_BY_PREFIX
                        if name in in_play]
    toc_order: list[str] = []
    for name in sections_in_play:
        toc_order.extend(str(e.get("subsection_id") or "") for e in _toc_of(ws, name))
    expected_order = [sid for sid in toc_order if sid in expected_grouped]

    check([s.subsection_id for s in subs] == expected_order,
          f"小节集合与次序等于「栏目归属 × 该节 TOC 顺序」推导出来的那一份："
          f"{[s.subsection_id for s in subs]}")
    check(len(subs) < len(aspect_ids),
          f"**写作单位比栏目少**：{len(aspect_ids)} 个 Contract 栏目投影成 {len(subs)} 个"
          "小节。这一条就是 R1 那条结构缺陷的行为证据——旧口径是「一个栏目一个小节」，"
          "那个数字只可能相等")

    by_id = {s.subsection_id: s for s in subs}
    check(set(by_id) == set(expected_grouped),
          "投影出的小节集合与推导集合逐条相等（不多造一个小节接住落单的栏目）")

    grouped_ok = True
    titles_ok = True
    for sid, group in expected_grouped.items():
        spec = by_id.get(sid)
        if spec is None:
            grouped_ok = False
            continue
        if tuple(spec.declared_aspect_ids) != tuple(group):
            grouped_ok = False
        if spec.title != _h2_of(ws, RUN._section_of_subsection(sid), sid):
            titles_ok = False
    check(grouped_ok,
          "每个小节的 `declared_aspect_ids` 与「该小节覆盖的栏目、按 Contract 次序」逐条相等："
          "责任仍在 Contract 栏目那一级，小节只是写作单位")
    check(titles_ok,
          "每个小节的标题**逐字取自该节 TOC 的 `h2`**（不是由代码另起一个名字，"
          "也不是把小节 id 当标题）")

    flattened = [a for s in subs for a in s.declared_aspect_ids]
    check(sorted(flattened) == sorted(aspect_ids) and len(flattened) == len(aspect_ids),
          f"全部 {len(aspect_ids)} 个栏目在投影里**恰好出现一次**：既不丢一栏，也不把一栏"
          "登记进两个小节（重复登记会让同一栏被写两遍而两边都「有出处」）")

    # ============================================ §2 要求文本逐字拼接
    details.append("## §2 要求文本：一个不删、一个不改、次序照 Contract")

    text_ok = True
    for sid, group in expected_grouped.items():
        spec = by_id.get(sid)
        if spec is None:
            text_ok = False
            continue
        want = [str(getattr(a, "requirement_text", "") or "")
                for a in req.aspects
                if str(getattr(a, "aspect_id", "") or "") in set(group)]
        if spec.requirement_text.split("\n") != want:
            text_ok = False
    check(text_ok,
          "每个小节的 `requirement_text` 逐行等于它覆盖的栏目的 Contract 要求文本、"
          "按 Contract 侧次序拼接，行数 = 栏目数——收的是**小节数**，不是要求内容")

    # ============================================ §3 来源类：非空，且逐条可回查
    details.append("## §3 允许来源类：投影自 evidence requirement，不是空元组")

    derived_classes: dict[str, list[str]] = {}
    for sid, group in expected_grouped.items():
        names: list[str] = []
        for aspect in req.aspects:
            if str(getattr(aspect, "aspect_id", "") or "") not in set(group):
                continue
            for ref in getattr(aspect, "evidence_requirement_ids", ()) or ():
                for source_class in getattr(ref, "source_classes", ()) or ():
                    name = str(source_class or "").strip()
                    if name and name not in names:
                        names.append(name)
        derived_classes[sid] = names

    classes_ok = True
    for sid, names in derived_classes.items():
        spec = by_id.get(sid)
        if spec is None or tuple(spec.allowed_source_classes) != tuple(names):
            classes_ok = False
    check(classes_ok,
          "每个小节的 `allowed_source_classes` 等于该组各栏目 evidence requirement 来源类的"
          "并集（首次出现序、去重）：补件该往哪一类来源找，答案来自 Contract，不是当场发明的")

    check(any(derived_classes.values()),
          "真实投影下至少有一个小节的来源类**非空**——这条是那道静默降级的守卫："
          "冻结 Contract 原始的 `evidence_requirement_ids` 是 `list[str]`，"
          "对字符串取 `.source_classes` 得到空元组而**不报错**；投影引用才带这个字段。"
          "若哪天投影面上被换成原始形状，上面那条断言会先红，而不是安静地给出一列空来源类")

    contract_ref_shape_ok = all(
        hasattr(ref, "source_classes")
        for aspect in req.aspects
        for ref in (getattr(aspect, "evidence_requirement_ids", ()) or ()))
    check(contract_ref_shape_ok,
          "投影面上每个栏目的 evidence requirement 引用都带 `source_classes` 字段"
          "（形状对得上，§3 第一条断言观测的才是来源类本身，而不是「字段压根不在」）")

    # ============================================ §4 顺序按 TOC，不按 Contract
    details.append("## §4 出场次序：按该节 TOC，不按「谁先出现」")

    #: 次序规则是**资产的性质**，不是某个主题的性质：被选中的公司主题只有一个正文小节，
    #: 拿它做不出「跨节反序」的用例。所以这一节直接从冻结映射里挑两个**不同节的真实栏目**
    #: （各取该节 TOC 第一条有栏目归属的小节），故意按**逆 TOC 次序**喂进去。
    first_aspect_of: dict[str, str] = {}
    for row in ws.mappings:
        sid = str(row.get("subsection_id") or "").strip()
        aid = str(row.get("aspect_id") or "").strip()
        if sid and aid:
            first_aspect_of.setdefault(sid, aid)
    pick: list[tuple[str, str]] = []
    for name in ("company", "financial", "industry"):
        for entry in _toc_of(ws, name):
            sid = str(entry.get("subsection_id") or "")
            if sid in first_aspect_of:
                pick.append((sid, first_aspect_of[sid]))
                break
    pick_sections = [RUN._section_of_subsection(sid) for sid, _ in pick]
    check(len(pick) >= 2 and len(set(pick_sections)) == len(pick),
          f"冻结映射里挑到 {len(pick)} 个**不同节**的栏目用于反序用例："
          f"{[sid for sid, _ in pick]}（各属 {pick_sections}）")

    if len(pick) >= 2:
        reversed_pick = list(reversed(pick))
        fake = _FakeReq(aspects=tuple(
            _FakeAspect(aspect_id=aid, requirement_text=f"{aid} 的要求文本")
            for _sid, aid in reversed_pick))
        out = RUN._subsections_from_writing_spec(fake)
        out_sids = [s.subsection_id for s in out]
        check(out_sids == [sid for sid, _ in pick],
              f"喂入次序（{[sid for sid, _ in reversed_pick]}）与输出次序（{out_sids}）相反："
              "节的次序取**前缀表声明的次序**（company → financial → industry），"
              "节内按该节 TOC——整套输出与输入次序无关，不是「谁先出现谁在前」")
        want_texts = [f"{first_aspect_of[sid]} 的要求文本" for sid, _ in pick]
        check([s.requirement_text for s in out] == want_texts,
              f"反序输入下每一小节仍然只收**自己那一栏**的要求文本（{want_texts}）："
              "分组与次序是两件事，次序变了不影响归属")
        check([tuple(s.declared_aspect_ids) for s in out]
              == [(first_aspect_of[sid],) for sid, _ in pick],
              "反序输入下每小节的栏目声明也跟着自己对，不会串到邻节去")

        forward = RUN._subsections_from_writing_spec(_FakeReq(aspects=tuple(
            _FakeAspect(aspect_id=aid, requirement_text=f"{aid} 的要求文本")
            for _sid, aid in pick)))
        check([(s.subsection_id, tuple(s.declared_aspect_ids), s.requirement_text)
               for s in forward]
              == [(s.subsection_id, tuple(s.declared_aspect_ids), s.requirement_text)
                  for s in out],
              "同一组栏目正序喂与反序喂，产出**逐字段相同**：小节轴是「这份资产 × 这组栏目」"
              "的纯函数。这条挡的是「上游把两栏对调，写作结构就换一个样子」那类隐性耦合")

    # ============================================ §5 三处 fail-closed
    details.append("## §5 fail-closed：指纹、无归属栏目、TOC 缺条目——都必须停住")

    tampered_decl = dataclasses.replace(
        ws, raw={**ws.raw, "content_sha256": "0" * 64})
    msg = _systemexit_of(lambda: _with_spec(tampered_decl, lambda: RUN._subsections_from_writing_spec(req)))
    check(msg is not None,
          "**声明的**内容指纹与钉住值不符时当场停住，不使用这份内容"
          f"（说明：{msg!r}）" if msg else
          "**声明的**内容指纹与钉住值不符时必须停住——实测没有停")

    tampered_body = dataclasses.replace(
        ws, raw={**ws.raw, "mappings": [
            {**row, "role": "secondary_reference"} for row in ws.raw.get("mappings", [])]})
    msg = _systemexit_of(lambda: _with_spec(tampered_body, lambda: RUN._subsections_from_writing_spec(req)))
    check(msg is not None,
          "**业务内容**漂移（重算指纹与钉住值不符）时也停住：声明值可以照抄，"
          f"内容抄不了（说明：{msg!r}）" if msg else
          "业务内容漂移必须停住——实测没有停")

    stray = _FakeReq(aspects=tuple(req.aspects) + (
        _FakeAspect(aspect_id="demo_contract.column_without_home",
                    requirement_text="这一栏在 WritingSpec 里没有归属小节",
                    evidence_requirement_ids=(_er_ref("er-stray", ("external",)),)),))
    msg = _systemexit_of(lambda: RUN._subsections_from_writing_spec(stray))
    check(msg is not None,
          "契约栏目在 WritingSpec 的 `mappings` 里没有归属小节时停住：不静默丢掉这一栏，"
          f"也不临时造一个小节接住（说明：{msg!r}）" if msg else
          "无归属栏目的契约必须停住——实测没有停")

    target = expected_order[0]
    trimmed = dataclasses.replace(ws, toc={
        **ws.toc,
        RUN._section_of_subsection(target): [
            e for e in _toc_of(ws, RUN._section_of_subsection(target))
            if str(e.get("subsection_id") or "") != target]})
    msg = _systemexit_of(lambda: _with_spec(trimmed, lambda: RUN._subsections_from_writing_spec(req)))
    check(msg is not None,
          f"小节 {target!r} 在 TOC 里缺条目时停住：取不到标题与顺序就不猜一个"
          f"（说明：{msg!r}）" if msg else
          f"TOC 缺 {target!r} 条目时必须停住——实测没有停")

    # ============================================ §6 反例：替身本身要能被观测
    details.append("## §6 §5 的替身是有效的（否则那四条断言是空转）")

    probe = RUN._subsections_from_writing_spec(_FakeReq(aspects=(
        _FakeAspect(aspect_id=aspect_ids[0],
                    requirement_text=str(getattr(req.aspects[0], "requirement_text", "")),
                    evidence_requirement_ids=(_er_ref("er-probe", ("external", "public")),)),)))
    check(len(probe) == 1 and probe[0].declared_aspect_ids == (aspect_ids[0],),
          "同一套替身形状喂一个**合法**栏目时能正常出一个小节：§5 那四条不是「不管喂什么都停」")
    check(tuple(probe[0].allowed_source_classes) == ("external", "public"),
          f"替身上的来源类被逐条读到（{list(probe[0].allowed_source_classes)}）："
          "来源类确实来自引用的 `source_classes`，不是常量，也不是「非空即可」")

    note("本模块只证明**小节轴这一条投影**落在冻结 WritingSpec 上、且三处 fail-closed 会停。"
         "它**不**证明：材料真的取到了、18 个栏目都有人写、正文达到人读内容门，或 M930-3 / "
         "TS5 / 任何正式阶段关闭。写作单位变粗**不代表**覆盖问题被解决——栏目级覆盖由逐段"
         "声明与覆盖账另行对账（`sections/cited_reply_readback.py`）。"
         "整个模块没有发出任何模型请求，也没有写库。")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


@contextlib.contextmanager
def _patched_spec(fake):
    """把 `load_writing_spec` 暂时换成返回 `fake`。用于**只读**地喂一份被改动的资产。

    只在本进程内、`with` 期间生效，退出即还原：反例不需要（也不允许）在磁盘上留一份
    被改动的资产。
    """
    with unittest.mock.patch.object(RUN.WS, "load_writing_spec", return_value=fake):
        yield


def _with_spec(fake, fn):
    with _patched_spec(fake):
        return fn()


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

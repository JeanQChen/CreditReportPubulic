"""Eval: 支撑边的**短别名线格式**（`saref-1`；返修 §二「让合格材料真正形成可读内容」）。

用法: python -m evals.test_demo_writer_support_alias

背景（为什么别名不是「省字数的写法」而是一条**受约束的线格式**）：r5 的失败形状是输出容量
——一条支撑边要逐字回抄 `authority_kind` + 64 位 `container_id` + 64 位 `fact_id` /
`material_id` + 三个恒定字段，约 350–400 字符，company 节第 2/4 批在 8192 输出 token 里只写完
37 条候选就被 provider 截断。对策是把**回抄**删掉：请求里逐行给出预先声明的短别名（`ref`），
模型只做选择，身份由写入侧从被引用的那一行确定性展开。

删掉回抄会引入一个新风险：模型可以自己**编**身份。本文件钉的就是「编不出来」这件事，分四面：

  §2 §3  三条不变量——预先声明 / 完备 / 不可变。缺任何一条，短别名就退化成「模型自己编身份
         的捷径」：未声明的 ref 无从展开只能靠猜（§3 的缺口/重复/错前缀反例），编号不连续
         就无法证明「请求里第 n 行」与「展开出的第 n 行」是同一行。
  §4 §5  展开是**唯一**的展开点：`authority_kind` / `container_id` / `fact_id` / `material_id`
         一律来自被引用的那一行，`support_semantics` 与 `authorization_path` 是由「引用的
         是哪一种行」决定的常量。本文件用一条 `financial_pack` 事实行证明这件事：它的
         `authority_kind` 必须原样出现在展开结果里，而 `authorization_path` 仍是
         `path_a_prevalidated`——**权威字段绝不来自路径名**（这不是「测试写不出来」，而是
         `SupportOption` 上根本没有 path 字段，见 §5 的字段表断言）。
  §6 §7  别名形式**不是第二条合法性口径**：展开后与长格式走**同一个**校验器（逐字段相等的
         解析结果、同一条拒绝信息），且别名形式里混入任何身份字段即**拒**（不是忽略——忽略
         会让「模型自报的身份」与「系统展开的身份」在产物里长得一模一样）。
  §8 §9  请求侧与展开侧必须是**同一张表**：`ref` 逐行贴在选项行上（行数不等即拒），而这张表
         只由本节**有序**选项表的位置决定（事实表 = `scan.facts` 顺序，材料表 =
         `manifest.entries` 顺序），因此「第 2 批的 m7」与「第 4 批的 m7」必是同一份材料。

如实登记的边界：
1. 本文件只覆盖**别名线格式**这一层（构造期不变量 + 解析期展开）。「展开后的边在真实链上被
   回查、绑定、蕴含门逐一核验」由 `evals/test_demo_writer_formal_chain.py` 与
   `evals/test_demo_claim_binding_gate.py` 覆盖，这里不重复也不冒充。
2. 请求面 `support_refs` 块的**文本**（策略版本、前缀、`complete: True`、rule 措辞）由
   `evals/test_demo_writer_prompt_assets.py` 对冻结资产核对；本文件只核对**声明与展开是同一
   张表**（`_declare_option_refs` 的逐行位置），不重复资产文本断言。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness import topic_schema as TS
from sections import narrative_schema as NS
from sections import pack_writer as PW

FP = "a" * 64
#: 别名表的 key 只是构造期断言，不进 wire；`pack-1` / `mat-*` 是两条表各自的容器身份占位。
PACK = "pack-1"
FIN_PACK = "finpack-1"


def _option(ref, kind, container, *, fact_id=None, material_id=None):
    return PW.SupportOption(ref=ref, authority_kind=kind, container_identity=container,
                            fact_id=fact_id, material_id=material_id)


#: 一条 `topic_pack` 事实行 + 一条 `financial_pack` 事实行。两条不同 kind 是刻意的：展开结果里的
#: `authority_kind` 必须逐字来自**行**，而不是由任何路径名或「事实行」这个类别推出来。
FACT_ROW = _option("f1", "topic_pack", PACK, fact_id="fact-1")
FIN_FACT_ROW = _option("f2", "financial_pack", FIN_PACK, fact_id="finfact-1")
MAT_ROW = _option("m1", "topic_pack", PACK, material_id="mat-1")
MAT_ROW2 = _option("m2", "topic_pack", "pack-2", material_id="mat-2")

ALIASES = PW.SupportAliasTable(facts=(FACT_ROW, FIN_FACT_ROW),
                               materials=(MAT_ROW, MAT_ROW2))

#: 长格式的两条边（与展开结果**逐字段**可比）。路径 A 的 `material_id` 必须缺省——material 锚点
#: 由写入侧从该权威事实自己的引用确定性派生，不由模型选择。
LONG_FACTUAL = {"authority_kind": "topic_pack", "container_id": PACK, "fact_id": "fact-1",
                "material_id": None, "support_role": "primary",
                "support_semantics": "factual",
                "authorization_path": "path_a_prevalidated"}
LONG_CONTEXT = {"authority_kind": "topic_pack", "container_id": PACK, "material_id": "mat-1",
                "support_role": "corroborating", "support_semantics": "context",
                "authorization_path": "context_only"}

#: 事实行（供 `_support_aliases` 的派生测试）。`AuthorityFactEntry` 本身不做校验，因此这里只给
#: `_support_aliases` 真正读到的三个字段加上最小上下文；不冒充任何真实 run 的权威目录。
MEMBER_LOCATOR = NS.char_range_locator("evidence:ev-1", 3, 40)
PAYLOAD_REF = TS.MaterialPayloadRef(
    object_type="evidence_span", authority_identity="evidence:ev-1", version="v1",
    content_hash=FP,
    locator=TS.EvidenceLocator(document_id="doc-1", document_version="dv-1",
                               section_path="s1", page=3),
    created_dependency_fingerprint="d" * 64).to_dict()


def _fact(authority_kind, container, fact_id, text):
    return PW.AuthorityFactEntry(
        authority_kind=authority_kind, container_identity=container, fact_id=fact_id,
        text=text, topic_id="t-1", aspect_ids=("a-1",), required=True, fact_type="metric",
        period="2024", scope="公司", material_id="mat-1",
        payload_ref={"object_type": "research_material"}, locator_ref=MEMBER_LOCATOR,
        source_identity="evidence:ev-1", provenance_identity="prov-1",
        content_fingerprint=FP)


def _scan(*facts):
    return PW.AuthorityScan(facts=tuple(facts), aspect_status={}, aspect_topic={},
                            aspect_impact={}, aspect_blocking={}, aspect_question={},
                            excluded_facts=(), conflicts=(), not_found=(), gaps=(),
                            coverage_counts={})


def _manifest(*members):
    entries = tuple(NS.WriterMaterialManifestEntry.create(
        pack_id=pack_id, material_id=material_id, research_material_disposition_id="rmd-1",
        source_identity="evidence:ev-1", provenance_identity="prov-1",
        material_content_fingerprint=FP, topic_id="t-1", material_type="evidence_span",
        payload_ref=PAYLOAD_REF, locator_ref=MEMBER_LOCATOR, payload_hash=FP,
        reading_view_fingerprint=FP) for pack_id, material_id in members)
    return NS.WriterMaterialManifest.create(members=entries)


def _plan(*, support=None, context_support=None, drafted: bool = True):
    """最小但合法的一束提案：只有支撑边在变，便于把解析结果逐字段比对。

    `drafted`（缺省 `True`）：当前线下「有候选、无草稿」是 typed failure，因此给出候选时**同时**
    给一段合法草稿（出处走材料轴 `m1`——`ALIASES` 里有材料行，本节草稿的出处只能是材料行；
    事实轴的用处只在材料面合法为空的节）。这段草稿**不参与**本文件任何一条断言：它只是让整束
    满足当前线的形状，被比对的仍是候选面的解析结果。
    """
    payload = {"claim_candidates": [], "narrative_draft_units": [], "follow_up_needs": []}
    if support is not None:
        payload["claim_candidates"] = [{"candidate_key": "c1", "claim_text": "公司2024年营业"
                                        "收入为1234.56亿元。", "support": list(support)}]
        if drafted:
            payload["natural_prose_draft"] = [{
                "prose_key": "p1", "text": "公司2024年营业收入为1234.56亿元。",
                "source_member_refs": ["m1"], "source_fact_refs": [],
                "atom_candidate_keys": ["c1"]}]
    if context_support is not None:
        payload["narrative_draft_units"] = [{"unit_key": "u1", "unit_kind": "paragraph",
                                            "text": "本节说明经营情况。",
                                            "context_support": list(context_support)}]
    return json.dumps(payload, ensure_ascii=False)


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

    # ============================================================ §1 版本与词表 pin
    check(PW.SUPPORT_ALIAS_POLICY_VERSION == "saref-1",
          f"别名策略版本必须是 'saref-1'（实际 {PW.SUPPORT_ALIAS_POLICY_VERSION!r}）")
    check(PW.PACK_WRITER_POLICY_VERSION == "pw-22",
          f"写作策略版本必须是 'pw-22'（实际 {PW.PACK_WRITER_POLICY_VERSION!r}）"
          "（返修 ④ 改了生成器看到的输入面：materials 行多了 source_role；`pw-15` 改了**写作"
          "顺序与产物形状**：先自然草稿、再逐原子候选，门前束/`SectionDraft` 多一层草稿层；"
          "`pw-16` 给出处加了**第二条互斥的轴**并让「有候选、无草稿」成为 typed failure；"
          "`pw-17` 给拒绝原因 `path_b_unproven_current_state` 加了 typed 分级码，并因此改了"
          "定向重提案说明；`pw-18` 让 `output_schema` 的**示例按本请求的输入面选那条合法"
          "轴**（旧示例两条轴同时填满，与同一份请求的 rules 逐字矛盾）；`pw-19` 把**同一类"
          "缺陷的剩余三处**（候选首边引 `f1`、context 边引 `m1`、补件 `budget_hint` 为空）"
          "一并按输入面分档，并给空预算补了一条写入侧入站防线——**我们收什么、请求里"
          "写什么**两处都变了；`pw-20` 再加**逐批支撑范围**：请求面多一块 "
          "`batch_support_scope`（本批各 topic 的可引用材料行、每栏目研究侧原样记录的 "
          "status、绑定它的权威事实行），并让「本批零候选 ⇒ 草稿与草稿单元都为空」成为一条"
          "确定性判据（`batch_candidate_witness_missing`）——请求面与**收**的面又一次同时变）；"
          "`pw-21`（指令 D §二·三条日期轴）新建 prompt 资产 v11 / `proposals-15`，把「一般经营"
          "描述怎么落笔」写成纪律：归属语由系统在门后按已登记来源渲染（**不由写者写**）、不得把"
          "「该材料披露的情况」升格成「一直如此」、新旧材料实质差异不得抹平、新闻事件日与发布日"
          "分开、披露日未知就标未知——提示词资产身份是策略身份的一部分，故版本继续前进）；"
          "`pw-22`（M930-3 r9 后返修 B）新建 prompt 资产 v12 / `proposals-16`，把**草稿闭合的"
          "读法**从「按键唯一」改成「按键集合相等 + 逐 occurrence 核验」（同一条候选可以出现在"
          "多个草稿单元里，但每一处都要对得上该候选自己的支撑边）——提示词资产身份是策略身份的"
          "一部分，故版本继续前进）")
    check(PW.SupportAliasTable().policy_version == PW.SUPPORT_ALIAS_POLICY_VERSION,
          "别名表的默认策略版本必须是登记值")
    check(PW.FACT_REF_PREFIX == "f" and PW.MATERIAL_REF_PREFIX == "m"
          and PW.FACT_REF_PREFIX != PW.MATERIAL_REF_PREFIX,
          "两条前缀必须是可区分的 'f' / 'm'：一个 ref 指向哪一种行必须是**语法可见**的")
    check(PW._ALIAS_FACTUAL_KEYS == ("ref", "support_role"),
          f"factual 别名形式只允许 ref + support_role（实际 {PW._ALIAS_FACTUAL_KEYS}）")
    check(PW._ALIAS_CONTEXT_KEYS == ("ref",),
          f"context 别名形式只允许 ref（实际 {PW._ALIAS_CONTEXT_KEYS}）")
    # 别名形式只比长格式**多** `ref` 这一个键，其余全部取自长格式键集：它不是另一套词汇表，
    # 而是长格式的「少写身份」写法。反过来看更准：别名形式里**剩下**的长格式字段只有模型仍须
    # 自己判断的那一个语义字段（factual 的 `support_role`），context 侧一个都不剩。
    check(set(PW._ALIAS_FACTUAL_KEYS) - {"ref"} <= set(PW._FACTUAL_SUPPORT_KEYS),
          "factual 别名键（除 ref 外）必须是 factual 长格式键的子集")
    check(set(PW._ALIAS_CONTEXT_KEYS) - {"ref"} <= set(PW._CONTEXT_SUPPORT_KEYS),
          "context 别名键（除 ref 外）必须是 context 长格式键的子集")
    check(set(PW._ALIAS_FACTUAL_KEYS) & set(PW._FACTUAL_SUPPORT_KEYS) == {"support_role"},
          "factual 别名形式里唯一保留的长格式字段必须是 support_role（其余全是回抄）")
    check(not (set(PW._ALIAS_CONTEXT_KEYS) & set(PW._CONTEXT_SUPPORT_KEYS)),
          "context 别名形式不得携带任何长格式字段（role 与语义在 context 边都是常量）")
    check(set(PW._FACTUAL_SUPPORT_KEYS) - set(PW._ALIAS_FACTUAL_KEYS)
          == {"authority_kind", "container_id", "fact_id", "material_id", "support_semantics",
              "authorization_path"},
          "factual 侧「被删掉的回抄字段」必须恰是身份与常量那六个")
    # 「context 不授权事实」在这条线上是**结构性**的：载体词表里根本没有 fact_id 这个键。
    check("fact_id" not in PW._CONTEXT_SUPPORT_KEYS,
          "context 长格式键里不得出现 fact_id（context 边不授权事实）")
    check(PW._CONTEXT_AUTHORITY_KINDS == ("topic_pack",),
          f"本批 context 边只支持 topic_pack（实际 {PW._CONTEXT_AUTHORITY_KINDS}）")

    # ============================================================ §2 选项行自己的不变量
    check(FACT_ROW.is_fact and not MAT_ROW.is_fact,
          "选项行必须能区分「引用事实行」与「引用材料行」（展开时路径由此二分）")
    expect_error(lambda: _option("f1", "topic_pack", PACK, fact_id="x", material_id="y"),
                 PW.PackWriterError, "选项行同时给 fact_id 与 material_id 即拒",
                 needle="恰有一个身份字段")
    expect_error(lambda: _option("f1", "topic_pack", PACK), PW.PackWriterError,
                 "选项行两个身份字段都不给即拒", needle="恰有一个身份字段")
    expect_error(lambda: _option("", "topic_pack", PACK, fact_id="x"), PW.PackWriterError,
                 "选项行 ref 为空即拒", needle="ref 不得为空")
    expect_error(lambda: _option("f1", "", PACK, fact_id="x"), PW.PackWriterError,
                 "选项行缺 authority_kind 即拒", needle="缺 authority_kind")
    expect_error(lambda: _option("f1", "topic_pack", "", fact_id="x"), PW.PackWriterError,
                 "选项行缺 container_identity 即拒", needle="缺 authority_kind")

    # ============================================================ §3 三条不变量
    # —— 完备：编号连续、无缺口、无重复、前缀正确。少了这一层，一个未被声明的 ref 无从展开，
    #    只能靠猜；多一个则「模型看到的选项集」大于「写入侧能展开的选项集」。
    expect_error(lambda: PW.SupportAliasTable(facts=(_option("f1", "topic_pack", PACK,
                                                             fact_id="a"),
                                                     _option("f3", "topic_pack", PACK,
                                                             fact_id="b"))),
                 PW.PackWriterError, "事实别名编号有缺口即拒", needle="一一对应")
    expect_error(lambda: PW.SupportAliasTable(facts=(_option("f1", "topic_pack", PACK,
                                                             fact_id="a"),
                                                     _option("f1", "topic_pack", PACK,
                                                             fact_id="b"))),
                 PW.PackWriterError, "事实别名重复即拒", needle="一一对应")
    expect_error(lambda: PW.SupportAliasTable(facts=(_option("f2", "topic_pack", PACK,
                                                             fact_id="a"),)),
                 PW.PackWriterError, "事实别名起始编号不是 f1 即拒", needle="一一对应")
    expect_error(lambda: PW.SupportAliasTable(facts=(_option("m1", "topic_pack", PACK,
                                                             fact_id="a"),)),
                 PW.PackWriterError, "事实表里混入材料前缀的 ref 即拒", needle="一一对应")
    expect_error(lambda: PW.SupportAliasTable(materials=(_option("m2", "topic_pack", PACK,
                                                                 material_id="a"),)),
                 PW.PackWriterError, "材料别名编号有缺口即拒", needle="一一对应")
    check(ALIASES.fact_refs() == ("f1", "f2") and ALIASES.material_refs() == ("m1", "m2")
          and ALIASES.refs() == ("f1", "f2", "m1", "m2"),
          "合法表的 ref 序列必须逐字是声明顺序（事实表在前，材料表在后）")
    # 空表合法：manifest 为空是合法状态（本节确实没有任何 Pack material），别名表随之两表皆空。
    check(PW.SupportAliasTable().refs() == (),
          "两张表皆空必须合法（空 manifest 是合法状态，不是「无材料也照样写」的许可）")
    # —— 不可变（版本）：同一份别名规则在不同版本下给出不同映射，因此策略版本是身份的组成部分。
    expect_error(lambda: PW.SupportAliasTable(policy_version="saref-0"),
                 PW.PackWriterError, "别名表策略版本不符即拒", needle="saref-1")
    # —— authority 轴：exact ResearchMaterial 只存在于 Pack，材料行不得冒充其他权威。
    expect_error(lambda: PW.SupportAliasTable(materials=(_option("m1", "financial_pack", FIN_PACK,
                                                                 material_id="mat-1"),)),
                 PW.PackWriterError, "材料行 authority_kind 非 topic_pack 即拒",
                 needle="只存在于 Pack")

    # ============================================================ §4 未声明的 ref 指不到
    expect_error(lambda: ALIASES.option("f9"), PW.PackWriterError,
                 "未声明的事实 ref 即拒", needle="不在本次请求声明的选项里")
    expect_error(lambda: PW.SupportAliasTable(facts=(FACT_ROW,)).option("m1"),
                 PW.PackWriterError, "指到另一张表（未声明的材料 ref）即拒",
                 needle="不在本次请求声明的选项里")
    expect_error(lambda: ALIASES.option(""), PW.PackWriterError,
                 "空 ref 即拒", needle="不在本次请求声明的选项里")
    check(ALIASES.option("f2") is FIN_FACT_ROW and ALIASES.option("m2") is MAT_ROW2,
          "已声明的 ref 必须解析到**那一行本身**（不是等值的另一行）")

    # ============================================================ §5 展开：逐字段来自被引用的行
    expanded = ALIASES.expand_factual("f1", support_role="primary")
    check(expanded == LONG_FACTUAL,
          f"事实行展开必须与长格式逐字段相等（实际 {expanded}）")
    fin_expanded = ALIASES.expand_factual("f2", support_role="primary")
    check(fin_expanded["authority_kind"] == "financial_pack"
          and fin_expanded["container_id"] == FIN_PACK
          and fin_expanded["fact_id"] == "finfact-1",
          "展开结果的权威三字段必须逐字来自被引用的**那一行**（不同 kind 也不例外）")
    check(fin_expanded["authorization_path"] == "path_a_prevalidated"
          and fin_expanded["support_semantics"] == "factual",
          "路径与语义是「引用事实行」这个二分决定的常量，不由行内容或路径名推断")
    mat_expanded = ALIASES.expand_factual("m1", support_role="corroborating")
    check(mat_expanded == {"authority_kind": "topic_pack", "container_id": PACK,
                           "fact_id": None, "material_id": "mat-1",
                           "support_role": "corroborating", "support_semantics": "factual",
                           "authorization_path": "path_b_material_derived"},
          f"材料行展开必须走路径 B 且不带事实身份（实际 {mat_expanded}）")
    check(ALIASES.expand_factual("f1", support_role="")["support_role"] == "",
          "support_role 是模型判断的语义字段，展开侧不得替它填默认值（空即下游拒绝）")
    # 「权威字段绝不来自 path」在这条线上是**结构性**的：选项行上压根没有 path 字段可抄。
    check("authorization_path" not in PW.SupportOption.__dataclass_fields__
          and "support_semantics" not in PW.SupportOption.__dataclass_fields__,
          "选项行不得携带 authorization_path / support_semantics 字段："
          "权威字段只能来自行身份，不能来自路径名")

    # ============================================================ §6 context 边不授权事实
    ctx = ALIASES.expand_context("m1")
    check("fact_id" not in ctx,
          "context 展开结果里不得出现 fact_id 键（「context 不授权事实」在这里是结构性的）")
    check(set(ctx) <= set(PW._CONTEXT_SUPPORT_KEYS),
          f"context 展开结果必须是 context 长格式键的子集（实际 {sorted(ctx)}）")
    check(ctx == LONG_CONTEXT, f"材料行 context 展开必须与长格式逐字段相等（实际 {ctx}）")
    expect_error(lambda: ALIASES.expand_context("f1"), PW.PackWriterError,
                 "context 边引用事实行即拒（事实行不是 context 载体）",
                 needle="不得引用事实行")

    # ============================================================ §7 别名形式不是第二条口径
    via_ref = PW.parse_writer_proposals(_plan(support=[{"ref": "f1", "support_role": "primary"}]),
                                        aliases=ALIASES)
    via_long = PW.parse_writer_proposals(_plan(support=[dict(LONG_FACTUAL)]), aliases=ALIASES)
    check(via_ref["claim_candidates"] == via_long["claim_candidates"]
          and len(via_ref["claim_candidates"]) == 1,
          "别名形式与长格式必须经**同一个**校验器得到逐字段相同的解析结果")
    check(via_ref["claim_candidates"][0]["support"][0]["fact_id"] == "fact-1",
          "解析结果里的身份必须是展开出来的那一行")
    ctx_ref = PW.parse_writer_proposals(_plan(context_support=[{"ref": "m1"}]), aliases=ALIASES)
    ctx_long = PW.parse_writer_proposals(_plan(context_support=[dict(LONG_CONTEXT)]),
                                         aliases=ALIASES)
    check(ctx_ref["narrative_draft_units"] == ctx_long["narrative_draft_units"]
          and "fact_id" not in ctx_ref["narrative_draft_units"][0]["context_support"][0],
          "context 边的别名形式与长格式必须解析成同一结果，且结果里没有 fact_id")
    # —— 混入身份字段即**拒**（不是忽略）：忽略会让「模型自报的身份」与「系统展开的身份」
    #    在产物里长得一模一样，两者从此无法区分。
    for extra in ({"container_id": PACK}, {"authority_kind": "topic_pack"},
                  {"fact_id": "fact-1"}, {"material_id": None},
                  {"support_semantics": "factual"},
                  {"authorization_path": "path_a_prevalidated"}):
        expect_error(lambda extra=extra: PW.parse_writer_proposals(
            _plan(support=[{"ref": "f1", "support_role": "primary", **extra}]),
            aliases=ALIASES),
            PW.PackWriterError,
            f"factual 别名形式混入身份/常量字段 {sorted(extra)} 即拒",
            needle="（短别名形式）")
    expect_error(lambda: PW.parse_writer_proposals(
        _plan(context_support=[{"ref": "m1", "support_role": "corroborating"}]),
        aliases=ALIASES),
        PW.PackWriterError, "context 别名形式不得自带 support_role（context 边一律同向）",
        needle="（短别名形式）")
    expect_error(lambda: PW.parse_writer_proposals(
        _plan(context_support=[{"ref": "m1", "material_id": "mat-1"}]), aliases=ALIASES),
        PW.PackWriterError, "context 别名形式混入 material_id 即拒", needle="（短别名形式）")
    # —— 没有声明就没有可展开的一行：不得凭 ref 猜身份。
    expect_error(lambda: PW.parse_writer_proposals(
        _plan(support=[{"ref": "f1", "support_role": "primary"}])),
        PW.PackWriterError, "无别名表时写 ref 形式即拒", needle="没有声明别名表")
    expect_error(lambda: PW.parse_writer_proposals(
        _plan(support=[{"ref": "f9", "support_role": "primary"}]), aliases=ALIASES),
        PW.PackWriterError, "别名形式指到未声明的 ref 即拒",
        needle="不在本次请求声明的选项里")
    expect_error(lambda: PW.parse_writer_proposals(
        _plan(context_support=[{"ref": "f1"}]), aliases=ALIASES),
        PW.PackWriterError, "别名形式把事实行当 context 载体即拒", needle="不得引用事实行")
    # 长格式在有别名表时**原样通过**（别名的引入不改写长格式这条既有路径）。
    check(via_long["claim_candidates"][0]["support"][0]["authorization_path"]
          == "path_a_prevalidated",
          "长格式在声明了别名表之后仍走原路径（别名只是**多**一种写法）")

    # ============================================================ §8 请求侧与展开侧同一张表
    rows = [{"authority_kind": "topic_pack", "container_id": PACK, "fact_id": "fact-1",
             "text": "公司2024年营业收入为1234.56亿元。"},
            {"authority_kind": "financial_pack", "container_id": FIN_PACK,
             "fact_id": "finfact-1", "text": "2024年资产负债率为45.6%。"}]
    declared = PW._declare_option_refs(rows, ALIASES.facts, "authority_facts")
    check([r["ref"] for r in declared] == list(ALIASES.fact_refs()),
          "请求侧逐行贴上的 ref 必须与展开侧的 ref 序列逐字相同（同一张表）")
    check(all(dict(rows[i]).items() <= declared[i].items() for i in range(len(rows))),
          "贴 ref 不得改写原行的任何字段")
    expect_error(lambda: PW._declare_option_refs(rows[:1], ALIASES.facts, "authority_facts"),
                 PW.PackWriterError, "请求行数少于声明别名数即拒",
                 needle="请求侧与展开侧必须是同一张表")
    expect_error(lambda: PW._declare_option_refs(rows + [dict(rows[0])], ALIASES.facts,
                                                 "authority_facts"),
                 PW.PackWriterError, "请求行数多于声明别名数即拒",
                 needle="请求侧与展开侧必须是同一张表")

    # ============================================================ §9 表只由有序位置决定
    fact_a = _fact("topic_pack", PACK, "fact-a", "公司2024年营业收入为1234.56亿元。")
    fact_b = _fact("financial_pack", FIN_PACK, "fact-b", "2024年资产负债率为45.6%。")
    manifest = _manifest((PACK, "mat-1"), ("pack-2", "mat-2"))
    scan_ab = _scan(fact_a, fact_b)
    scan_ba = _scan(fact_b, fact_a)
    aliases_ab = PW._support_aliases(scan_ab, manifest)
    aliases_ba = PW._support_aliases(scan_ba, manifest)
    check(aliases_ab.fact_refs() == ("f1", "f2") and aliases_ab.material_refs() == ("m1", "m2"),
          "派生出的 ref 必须编号连续且与选项表长度一致")
    check(aliases_ab.expand_factual("f1", support_role="primary")["fact_id"] == "fact-a"
          and aliases_ba.expand_factual("f1", support_role="primary")["fact_id"] == "fact-b",
          "ref→行 的映射只由选项表的**有序位置**决定（不是由身份或字典序决定）")
    check(aliases_ab.expand_factual("f2", support_role="primary")["authority_kind"]
          == "financial_pack"
          and aliases_ab.expand_factual("f2", support_role="primary")["container_id"] == FIN_PACK,
          "事实行的权威身份逐字取自 `scan.facts` 的那一条")
    check(aliases_ab.expand_factual("m1", support_role="primary")["container_id"] == PACK
          and aliases_ab.expand_factual("m2", support_role="primary")["container_id"] == "pack-2",
          "材料行的容器身份逐字取自 `manifest.entries` 的 `pack_id`（不是 material_id）")
    check([e["material_id"] for e in
           (aliases_ab.expand_factual("m1", support_role="primary"),
            aliases_ab.expand_factual("m2", support_role="primary"))]
          == [e.material_id for e in manifest.entries],
          "材料行的 material_id 必须按 manifest 成员顺序一一对应")
    # 同一修订内跨批恒定：同一份输入派生两次必须得到同一张表（无随机、无时间、无全局状态）。
    again = PW._support_aliases(scan_ab, manifest)
    check(again.facts == aliases_ab.facts and again.materials == aliases_ab.materials
          and again.policy_version == aliases_ab.policy_version,
          "同一份 scan/manifest 派生出的别名表必须完全相等（跨批恒定，不得每批重排）")
    # 事实表与材料表是两张独立的表：事实为空不影响材料编号，反之亦然。
    only_mat = PW._support_aliases(_scan(), manifest)
    check(only_mat.fact_refs() == () and only_mat.material_refs() == ("m1", "m2"),
          "两张表的编号各自独立：事实为空时材料仍是 m1..mM")
    check(PW._support_aliases(scan_ab, _manifest()).material_refs() == (),
          "材料为空时不得凭空造出 m1（空 manifest 是合法状态）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

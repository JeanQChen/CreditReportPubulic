"""材料包读回里**表对象信道**的形态读法（acc-37 起 / 现 `material-pack/5`）的回归。

## 这条回归盯着什么

§二 2.3 的 `content_qualification` 只覆盖 **span** 候选的形态分类（`text` /
`selection_form` / `isolated_heading` / `layout_fragment`）。**表对象**信封按设计**不带**
这一列——它的形态、允许用途与排除项写在同信封的 `reading_policy` 里（表题与表体行数在
`content.structured_payload`）。

材料包读回过去对「信封里没有 `content_qualification`」一律返回 `None`，于是：

    「这条信道不归那个分类器管」  →  在人读页印成「内容形态：**读不出**」
                                  →  在节级直方图里落进 `unavailable`

而真实现场里那 11 份表对象**已准入、已保留、原文 143 字、已进 Writer 清单**。把「读不回
形态」与「读失败」印成同一行，读者得到的结论是「表这一路根本没取到」，而实际取到了——
差额恰好是这一批业务取材要解释的东西。

## 不许破的边界

1. **两条正交声明不得合并**：`reading_material`（这张表能不能读）与 `numeric_authority`
   （表里的数字能不能以它为准）是两件事。本通道的设计是前者真、后者假；读回侧只**搬运**
   这两条，不替任何一条说话，也不因「能读」而给数字任何权威。
2. **两种「读不出」仍要分得开**：表对象是**读得出**形态的（走 `reading_policy`）；
   真正读不出的（信封里既没有 `content_qualification`，也不是表对象信封）继续记 `None`
   → `unavailable`。合并两者就是把 acc-37 修掉的错再犯一次。
3. **词表只有 span 那一份**：`table_object` 是**读回侧**的档位名，**不得**塞进
   `tree_materials.TREE_MATERIAL_CONTENT_KINDS`——那等于声称表对象由一个不管它的分类器判过。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evaluation import run_m930_3_acceptance as ACC  # noqa: E402
from harness import graph_table_materials as GTM  # noqa: E402
from harness import table_object_materials as TOM  # noqa: E402
from harness import tree_materials as TM  # noqa: E402

_RUNNER_SRC = Path(ACC.__file__).read_text(encoding="utf-8")
_GTM_SRC = Path(GTM.__file__).read_text(encoding="utf-8")

#: 一份**形状真实**的表对象信封（键取自 `table_object_materials` 的构建点，逐字不增不减）。
_TABLE_TEXT = "\n".join((
    "营业收入构成", "单位：万元", "项目 本期发生额 上期发生额", "主营业务收入 1,234 1,100",
    "合计 1,234 1,100"))
_TABLE_ENVELOPE = json.dumps({
    "material_payload_version": 1,
    "envelope_kind": TOM.TABLE_OBJECT_MATERIAL_ENVELOPE_KIND,
    "object_type": TOM.TABLE_OBJECT_MATERIAL_TYPE,
    "authority_identity": "evidence:blk-1",
    "content": {
        "text": _TABLE_TEXT,
        "structured_payload": {
            "envelope_kind": TOM.TABLE_OBJECT_MATERIAL_ENVELOPE_KIND,
            "table_title": "营业收入构成",
            "title_verifiable": True,
            "body_row_texts": ["主营业务收入 1,234 1,100", "合计 1,234 1,100"],
            "body_row_count": 2,
            "reading_material": True,
            "numeric_authority": False,
            "permitted_use": TOM.TABLE_OBJECT_PERMITTED_USE,
            "exclusions": list(TOM.TABLE_OBJECT_EXCLUSIONS),
        },
        "evidence_type": "table",
    },
    "reading_policy": {
        "reading_material": True,
        "numeric_authority": False,
        "financial_authority_claimed": False,
        "permitted_use": TOM.TABLE_OBJECT_PERMITTED_USE,
        "exclusions": list(TOM.TABLE_OBJECT_EXCLUSIONS),
    },
}, ensure_ascii=False)

#: **第二条表对象信道**：`gtm-1`（`graph-table-material-v1`）。§0.18 W8 判「同一图侧来源
#: 走单通道」之后，目标表的正式材料由 `harness/graph_table_materials.py` 产出，信封种类因此
#: 换了；`tom-1` 只留给历史 run。
#:
#: 键名**逐字取自 `graph_table_materials` 的构建点**，与上一条的差别只有两处：信封种类，以及
#: 表题键名（`title_text`，不是 `table_title`）。正文按 `gtm-1` 自己的渲染声（制表符分列、
#: 换行分行）拼，不按 `tom-1` 的空格分列拼——夹具换了信道却沿用旧渲染，会让「读得出」这件事
#: 只在夹具里成立。
_GTM_TABLE_TEXT = "\n".join((
    "营业收入构成", "单位：万元", "项目\t本期金额\t上期金额",
    "主营业务收入\t1,234\t1,100", "合计\t1,234\t1,100"))
_GTM_ENVELOPE = json.dumps({
    "material_payload_version": GTM.MATERIAL_PAYLOAD_VERSION,
    "envelope_kind": GTM.GRAPH_TABLE_MATERIAL_ENVELOPE_KIND,
    "object_type": GTM.GRAPH_TABLE_MATERIAL_TYPE,
    "authority_identity": "evidence:blk-1",
    "content": {
        "text": _GTM_TABLE_TEXT,
        "structured_payload": {
            "envelope_kind": GTM.GRAPH_TABLE_MATERIAL_ENVELOPE_KIND,
            "table_id": "gtb-1",
            "title_text": "营业收入构成",
            "body_row_count": 2,
            "structure_state": "complete",
            "reading_material": True,
            "numeric_authority": False,
            "financial_authority_claimed": False,
            "permitted_use": GTM.GRAPH_TABLE_PERMITTED_USE,
            "exclusions": list(GTM.GRAPH_TABLE_EXCLUSIONS),
        },
        "evidence_type": "table",
    },
    "reading_policy": {
        "reading_material": True,
        "numeric_authority": False,
        "financial_authority_claimed": False,
        "permitted_use": GTM.GRAPH_TABLE_PERMITTED_USE,
        "exclusions": list(GTM.GRAPH_TABLE_EXCLUSIONS),
    },
}, ensure_ascii=False)

#: 一份 span 信封（§二 2.3 的形态分类器管的就是这一条）：正常走原来的那一支。
_SPAN_ENVELOPE = json.dumps({
    "content": {"text": "# 经营模式\n公司以直营门店为主。"},
    "content_qualification": {"kind": "text", "selection": None},
}, ensure_ascii=False)

#: 一份**既不是** span 信封、**也不是**表对象信封的材料：这才是真正的「读不出形态」。
_BARE_ENVELOPE = json.dumps({"content": {"text": "一段没有形态读法的旁证。"}},
                            ensure_ascii=False)

#: 一份**词表外**的 span 信封：`kind` 读不懂，必须继续记「读不懂」，不得被升格成表对象。
_OFF_VOCAB_ENVELOPE = json.dumps({
    "content": {"text": "一段形态名不在词表里的材料。"},
    "content_qualification": {"kind": "hologram", "selection": None},
}, ensure_ascii=False)


def _locator():
    return SimpleNamespace(document_id="doc-A", document_version="v1",
                           to_dict=lambda: {"page": 10, "block_range": [3, 7]})


def _material(payload_bytes: bytes, *, material_id: str = "m-1"):
    """一份**形状真实**的（mock）Pack 材料。

    与 `evals.test_m930_3_failure_diagnostics` 的构造同一条路径：`payload_ref.content_hash`
    必须等于 payload 字节的 sha256，否则 `verify_material_payload_ref` 会 fail-closed——
    这里刻意用它，而不是绕过校验直接喂字节。
    """
    body_hash = hashlib.sha256(payload_bytes).hexdigest()
    locator = _locator()
    # 材料类型取自**信封自己**的 `object_type`（表对象信封是 `table_context`，span 信封是
    # 别的东西）：夹具不得把三份不同信道的材料都写成同一个 material_type。
    object_type = str(json.loads(payload_bytes.decode("utf-8")).get("object_type")
                      or "evidence_span")
    ref = SimpleNamespace(object_type=object_type,
                          authority_identity="evidence:blk-1", version="v1",
                          locator=locator, content_hash=body_hash,
                          to_dict=lambda: {"object_type": object_type})
    return SimpleNamespace(material_id=material_id, material_type=object_type,
                           locator=locator, content_hash=body_hash, payload_ref=ref)


def _resolver_for(*envelopes: bytes):
    """按 `content_hash` 分发的 resolver：**一份现场一个** resolver，不是一份材料一个。

    一份材料一个 resolver 的写法会让整节都解到**最后那一份**字节——那正是「材料各自的读数
    其实来自同一个信封」这种夹具失真，且它只会在断言落到第二份材料上时才暴露。
    """
    by_hash = {hashlib.sha256(e).hexdigest(): e for e in envelopes}

    def _resolve(ref):
        body = by_hash[str(ref.content_hash)]
        return SimpleNamespace(object_type=ref.object_type,
                               authority_identity=ref.authority_identity,
                               version=ref.version, locator=ref.locator,
                               content_hash=ref.content_hash, payload_bytes=body)

    return SimpleNamespace(resolve=_resolve)


def _readback_state(*envelopes: bytes):
    """把若干份信封装成一次材料包读回的**形状真实**现场（逐份一个 material_id）。"""
    resolver = _resolver_for(*envelopes)
    materials = []
    dispositions = []
    aspect_material_ids = []
    for index, envelope in enumerate(envelopes, 1):
        material = _material(envelope, material_id=f"m-{index}")
        materials.append(material)
        aspect_material_ids.append(material.material_id)
        dispositions.append(SimpleNamespace(
            disposition_id=f"rmd-{index}", material_id=material.material_id,
            admission_state="admitted", retention_state="retained",
            source_validation="validated", reason_code="aspect_material_admitted",
            reason_proof="被 0 条候选引用", policy_version="rmd-1", aspect_ids=("a-1",)))
    aspect_result = SimpleNamespace(aspect_id="a-1", material_ids=tuple(aspect_material_ids))
    pack = SimpleNamespace(
        pack_id="pack-1", materials=tuple(materials), aspect_results=(aspect_result,),
        material_dispositions=tuple(dispositions),
        source_set=SimpleNamespace(members=(), fingerprint=lambda: "fp-1"))
    pack_set = SimpleNamespace(pack_for=lambda _topic_id: pack)
    state = SimpleNamespace(
        sections={},
        inputs=SimpleNamespace(resolver=resolver,
                               tasks={"company": SimpleNamespace(
                                   section_id="company", topic_ids=["t-co"])}))
    state.authority_of = lambda _sid: SimpleNamespace(pack_set=pack_set)
    return state


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

    # ==================================================================
    # 1. 表对象信封：读得出形态，且两条正交声明各自成键
    # ==================================================================
    envelope = json.loads(_TABLE_ENVELOPE)
    qualification = ACC._table_object_qualification(envelope)
    check(isinstance(qualification, dict) and qualification["kind"] == "table_object",
          f"表对象信封必须读得出形态（实得 {qualification!r}）")
    check(qualification["kind_in_vocabulary"] is False,
          "`kind_in_vocabulary=False` 对表对象说的是「不归那条词表管」，"
          "**不是**「读不懂」——这两种结论在产物里必须分得开")
    policy = qualification["reading_policy"]
    check(policy["reading_material"] is True and policy["numeric_authority"] is False,
          f"两条正交声明各自成键、按信封原样搬运（实得 reading_material="
          f"{policy['reading_material']!r}, numeric_authority={policy['numeric_authority']!r}）")
    check(policy["permitted_use"] == TOM.TABLE_OBJECT_PERMITTED_USE,
          "允许用途逐字取自信封（不在读回侧另立第二份措辞）")
    check(list(policy["exclusions"]) == list(TOM.TABLE_OBJECT_EXCLUSIONS),
          f"排除项逐条、按序搬运（实得 {policy['exclusions']}）："
          "它是「这一份读不出什么」的账，少一条就等于替它多认一件事")
    check(policy["table_title"] == "营业收入构成" and policy["body_row_count"] == 2,
          "表题与表体行数取自 `content.structured_payload`（读的人不必解信封才知道这是哪张表）")
    check("table-object-material-v1" in qualification["kind_basis"],
          "形态依据要写明是哪一条信道读出来的，不写成一个光秃秃的档位名")

    # 读回侧**不替它改口**：信封声称什么就搬什么。这条防的是「读回顺手把数字权威抹平」——
    # 抹平之后，一个真的声称了数字权威的对象在产物里就与合格表对象长得一模一样了。
    loud = dict(envelope)
    loud["reading_policy"] = dict(envelope["reading_policy"], numeric_authority=True)
    check(ACC._table_object_qualification(loud)["reading_policy"]["numeric_authority"] is True,
          "信封声称 `numeric_authority=True` 时读回侧照搬 True（读回只搬运，不洗白）")

    # ==================================================================
    # 1b. **第二条表对象信道** `gtm-1`：同样读得出，且两条信道分得开
    #
    # 这一节防的是「修好了一条信道、换了另一条又退回去」：§0.18 W8 之后目标表的正式材料
    # 改由图侧单通道产出，信封种类随之从 `tom-1` 换成 `gtm-1`。读回侧若只认旧那一种，
    # 新信道的表材料会落回 `kind=unavailable` → 人读页又印成「内容形态：**读不出**」，
    # 而它们其实**已放行、已进 Pack、已进 Writer 清单**——与 acc-37 修掉的是同一个错。
    # ==================================================================
    gtm_envelope = json.loads(_GTM_ENVELOPE)
    gtm_qual = ACC._table_object_qualification(gtm_envelope)
    check(isinstance(gtm_qual, dict) and gtm_qual["kind"] == "table_object",
          f"`gtm-1` 信封同样必须读得出形态（实得 {gtm_qual!r}）——"
          "只认旧信道等于把新信道的表材料印成「读不出」")
    check("graph-table-material-v1" in gtm_qual["kind_basis"],
          f"形态依据要写明**实际**是哪一条信道读出来的（实得 {gtm_qual['kind_basis']!r}）")
    gtm_policy = gtm_qual["reading_policy"]
    check(gtm_policy["envelope_kind"] == GTM.GRAPH_TABLE_MATERIAL_ENVELOPE_KIND,
          "读数里带出**实际观察到的**信封种类：两条信道的读数不得被读成同一批对象的读数")
    check(gtm_policy["table_title"] == "营业收入构成" and gtm_policy["body_row_count"] == 2,
          f"表题按 `gtm-1` 自己的键名（`title_text`）读到（实得 "
          f"{gtm_policy['table_title']!r}／{gtm_policy['body_row_count']!r}）")
    check(gtm_policy["permitted_use"] == GTM.GRAPH_TABLE_PERMITTED_USE
          and list(gtm_policy["exclusions"]) == list(GTM.GRAPH_TABLE_EXCLUSIONS),
          "允许用途与逐条排除项取自**这一条信道自己**的声明，不沿用另一条的")
    check(gtm_policy["reading_material"] is True
          and gtm_policy["numeric_authority"] is False,
          "两条正交声明在这一条信道上同样各自成键（能读 ≠ 数字以它为准）")
    check(qualification["reading_policy"]["envelope_kind"]
          != gtm_policy["envelope_kind"],
          "两条信道在同一族里**仍分得开**（合一之后，历史 run 与当前 run 的表材料读数"
          "会长得一模一样，谁是谁无从查起）")

    #: 夹具保真：上面那份 `gtm-1` 信封的键名是**照生产构建点抄的**。生产侧改名而夹具不改，
    #: 这一节就会退化成「测我自己写的那份 JSON」——所以逐字核对键名仍在构建点里。
    for key in ('"title_text"', '"body_row_count"', '"reading_policy"'):
        check(key in _GTM_SRC,
              f"夹具保真：`{key}` 仍是 `graph_table_materials` 的构建键"
              "（生产侧改名后这一条会先红，而不是让夹具悄悄变成虚构）")
    check("GTM.GRAPH_TABLE_MATERIAL_ENVELOPE_KIND" in _RUNNER_SRC
          and "TOM.TABLE_OBJECT_MATERIAL_ENVELOPE_KIND" in _RUNNER_SRC,
          "接线：读回侧**同时**认两条信封种类（`gtm-1` 当前单通道 ＋ `tom-1` 历史 run）；"
          "少认一条就会把那条信道整批读成「读不出形态」")

    _t, _s, gtm_e2e = ACC._material_text_and_qualification(
        _material(_GTM_ENVELOPE.encode("utf-8")),
        _resolver_for(_GTM_ENVELOPE.encode("utf-8")))
    check(_s == "resolved" and _t == _GTM_TABLE_TEXT
          and isinstance(gtm_e2e, dict) and gtm_e2e["kind"] == "table_object",
          f"端到端：`gtm-1` 表材料读出原文与 `kind=table_object`（实得 status={_s!r}）")
    gtm_line = ACC._qualification_md_line(gtm_e2e)
    check("表对象信道" in gtm_line and "营业收入构成" in gtm_line
          and "内容形态：**读不出**" not in gtm_line,
          "人读行里 `gtm-1` 同样印成**表对象信道**，不是「读不出」")

    # ==================================================================
    # 2. 三条信道分得开：表对象 / span / 真·读不出
    # ==================================================================
    check(ACC._table_object_qualification(json.loads(_SPAN_ENVELOPE)) is None,
          "span 信封不是表对象信封 ⇒ 本函数返回 `None`（由 span 那一支去读它的 `kind`）")
    check(ACC._table_object_qualification(json.loads(_BARE_ENVELOPE)) is None,
          "既无形态读法、又不是表对象信封 ⇒ `None`（这才是真正的「读不出形态」）")
    check("table_object" not in TM.TREE_MATERIAL_CONTENT_KINDS,
          "`table_object` **不得**进 span 形态词表：它是读回侧的档位名，"
          "塞进去等于声称表对象由一个不管它的分类器判过")

    # ==================================================================
    # 3. 端到端：`_material_text_and_qualification` 三条返回各不相同
    # ==================================================================
    table_material = _material(_TABLE_ENVELOPE.encode("utf-8"))
    text, status, qual = ACC._material_text_and_qualification(
        table_material, _resolver_for(_TABLE_ENVELOPE.encode("utf-8")))
    check(status == "resolved" and text == _TABLE_TEXT,
          f"表对象的原文照旧逐字读出（实得 status={status!r}）")
    check(isinstance(qual, dict) and qual["kind"] == "table_object"
          and isinstance(qual["reading_policy"], dict),
          "端到端：表对象材料在形态列上带 `kind=table_object` 与 `reading_policy`"
          "（这才是 acc-37 修掉的那一处——它过去是 `None`）")

    _t, _s, span_qual = ACC._material_text_and_qualification(
        _material(_SPAN_ENVELOPE.encode("utf-8")),
        _resolver_for(_SPAN_ENVELOPE.encode("utf-8")))
    check(isinstance(span_qual, dict) and span_qual["kind"] == "text"
          and span_qual["reading_policy"] is None,
          "span 侧的形状逐条一致：同位置**保留** `reading_policy=None` 这一键"
          "（读者不必猜「缺这一键」是什么意思）")

    _t, _s, bare_qual = ACC._material_text_and_qualification(
        _material(_BARE_ENVELOPE.encode("utf-8")),
        _resolver_for(_BARE_ENVELOPE.encode("utf-8")))
    check(bare_qual is None,
          "既无形态读法、又不是表对象信封 ⇒ 仍是 `None`（**不得**顺手给它补一个 `text`，"
          "那等于替它声称「这是叙述材料」）")

    _t, _s, off_qual = ACC._material_text_and_qualification(
        _material(_OFF_VOCAB_ENVELOPE.encode("utf-8")),
        _resolver_for(_OFF_VOCAB_ENVELOPE.encode("utf-8")))
    check(isinstance(off_qual, dict) and off_qual["kind"] == "hologram"
          and off_qual["kind_in_vocabulary"] is False,
          "词表外的 `kind` 仍按原样带出（不降级成 `text`，也不被升格成表对象）")

    # ==================================================================
    # 4. 人读一行：表对象印出允许用途与排除项，且**不**印成「读不懂 / 读不出」
    # ==================================================================
    line = ACC._qualification_md_line(qual)
    check("table_object" in line and "表对象信道" in line,
          f"人读行的第一句点明这是**表对象信道**（实得 {line.splitlines()[0]!r}）")
    # 判据落在**结论**那一句上：行里可以出现「读不懂」三个字（为了与它对照），但**不得**
    # 以「内容形态：**读不懂 / 读不出**」这种结论式措辞开头——那才是把表对象印成读失败。
    check("不归那条词表管" in line
          and "内容形态：**读不懂**" not in line and "内容形态：**读不出**" not in line,
          "`kind_in_vocabulary=False` 在人读行里写成「不归那条词表管」，"
          "**不是**「读不懂」——前者是「换了一条信道」，后者是「形态读失败」")
    check(f"`{TOM.TABLE_OBJECT_PERMITTED_USE}`" in line
          and all(f"`{x}`" in line for x in TOM.TABLE_OBJECT_EXCLUSIONS),
          f"允许用途与**逐条**排除项都印出来（实得 {line!r}）")
    check("营业收入构成" in line and "表体行：2" in line,
          "表题与表体行数印在形态行下面（读的人一眼知道这是哪张表、有几行）")
    check("正交" in line and "`True`" in line and "`False`" in line,
          "两条声明并列印出并写明**正交**：这张表能读，不等于表里的数字能以它为准")

    check("读不出" in ACC._qualification_md_line(None),
          "`None` 仍印「读不出」（真正的读不回来）")
    check("读不懂" in ACC._qualification_md_line(off_qual),
          "词表外的 `kind` 仍印「读不懂」（与「读不出」是两条不同的结论）")
    check("**读不出**" not in line.splitlines()[0],
          "**表对象一行的第一句不得印成「读不出」**——这正是 acc-37 修掉的那一句")

    # ==================================================================
    # 5. 节级直方图：`unavailable` 不再吞掉表对象
    # ==================================================================
    state = _readback_state(_TABLE_ENVELOPE.encode("utf-8"),
                            _SPAN_ENVELOPE.encode("utf-8"),
                            _BARE_ENVELOPE.encode("utf-8"))
    block = ACC._material_pack_readback(state, "company")
    counts = block["content_kind_counts"]
    check(block["entry_count"] == 3, f"三份材料逐条读出（实得 {block['entry_count']}）")
    check(counts == {"table_object": 1, "text": 1, "unavailable": 1},
          f"直方图的键分三族（实得 {counts}）：表对象另成一族之后，"
          "`unavailable` 才真的只表示「读不出形态」——从前它**同时**吞掉了表对象")
    check(counts.get("unavailable") == 1,
          "表对象**不再**落进 `unavailable`（否则那一节的读数里，"
          "「读失败」会比实际多出表对象那一批）")
    check(block["table_object_material_ids"] == ["m-1"],
          f"节级另给表对象 id 一览（实得 {block['table_object_material_ids']}）："
          "读的人不必逐条翻 `content_qualification` 才知道哪些是表对象")
    check(sum(counts.values()) == block["entry_count"],
          "直方图各键计数之和 = 条目数（不重不漏）")
    check(block["selection_form_material_ids"] == [],
          "表对象**不得**被算进勾选表单行一览（两条信道不得互认）")
    check("table_object" in (block.get("note") or ""),
          "载荷的 `note` 里写明表对象这一族的读法（产物自己会说话，读者不必回读源码）")
    check(block["entries"][0]["content_qualification"]["reading_policy"]["numeric_authority"]
          is False,
          f"逐条里那条正交声明也在（`aspect_ids_from_aspect_results` 与它同一层）")

    # ==================================================================
    # 6. 人读版：既有「表对象信道 N 份」的清点行，也有一条条的允许用途
    # ==================================================================
    md = ACC._material_pack_md_from({"schema_version": ACC.MATERIAL_PACK_SCHEMA_VERSION,
                                     "status": "readback",
                                     "sections": {"company": block},
                                     "entry_count": block["entry_count"]})
    check("表对象信道" in md and "1 份" in md,
          "人读版给出表对象信道的清点行（数量与直方图同源，不另算一次）")
    check("允许用途" in md and "数字权威" in md,
          "逐条里印出允许用途与数字权威两条读数")
    check(all(f"`{x}`" in md for x in TOM.TABLE_OBJECT_EXCLUSIONS),
          "逐条里印出**每一条**排除项")
    check("内容形态：**读不出**（信封里既没有" in md,
          "真正读不出的那一份仍印「读不出」（表对象挪走了，这条措辞仍在用）")

    # ==================================================================
    # 7. 版本与接线
    # ==================================================================
    check(ACC.MATERIAL_PACK_SCHEMA_VERSION == "material-pack/5",
          f"载荷形状变了（`reading_policy` 多出 `envelope_kind`：两条表对象信道必须分得开）"
          f"⇒ 版本号必须前进（实得 {ACC.MATERIAL_PACK_SCHEMA_VERSION!r}）")
    check(ACC.RUNNER_VERSION == "m930-3-acc-40",
          f"载荷变化必须升 RUNNER_VERSION（实得 {ACC.RUNNER_VERSION!r}）——"
          f"acc-37 是本模块钉的表对象信道读法（`material-pack/4`），acc-38 是同批 §三 的"
          f"**替身选材与组织**（候选原子按小句边界有界切分且必须自带陈述对象；材料按来源角色"
          f"稳定重排后提案；组织侧每个接缝各取一个中性连接语），acc-39 是 §0.18 W8 单通道的"
          f"**读取面**（同时认 `gtm-1` 与 `tom-1`，并带出实际观察到的信道），acc-40 是定点业务"
          f"纠正①的**勾选行栏目归属**（读法 `tmr-2` → `tmr-3`：勾选行的所问事项只取行内前缀，"
          f"行内没写主语时留空、不再回指所在节点标题，材料包读回随之把空所问事项印成人话并把"
          f"「所在节点」标成仅导航坐标）。本模块钉的"
          f"「表对象读得出形态」这条判据在 acc-38→40 之间**一字未改**，改的是它认几条信道、"
          f"以及同一页上勾选行的所问事项从哪来")
    check(ACC.ACCEPTANCE_REPORT_SCHEMA_VERSION == "m930-3-acc-report-34",
          "报告载荷**未变**，故报告 schema 停在 34（升它会让历史报告的判读窗口凭空前移）")
    check("qualification = _table_object_qualification(envelope)" in _RUNNER_SRC
          and '"reading_policy": None,' in _RUNNER_SRC,
          "接线：`not isinstance(raw, Mapping)` 那一支真的走表对象读法，"
          "span 那一支真的补上形状一致的 `reading_policy=None`")
    check('"table_object_material_ids": table_object_ids,' in _RUNNER_SRC
          and 'if _tob:' in _RUNNER_SRC,
          "接线：节级的表对象一览既进载荷、也进人读版")
    check(_RUNNER_SRC.count('== "table_object"') >= 2,
          f"接线：人读行与节级一览都按同一个档位名分族"
          f"（实得 {_RUNNER_SRC.count(chr(61) + chr(61) + chr(32) + chr(34) + 'table_object' + chr(34))} 处；"
          "不在两处各写一套判据）")

    result = {"passed": passed, "failed": failed, "skipped": 0, "details": details}
    return result


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

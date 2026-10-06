"""TS3 生产对齐器（`document_structure/aligner.py`）反例与冻结语义测试。

覆盖 TS3 任务书 §五 中与对齐相关的全部反例，以及 `TREE_STRUCTURE_IMPLEMENTATION_PLAN.md`
§13.2 的 TS3 交付范围（A）：

A. **唯一生产实现**：`evaluation/run_tree_layout_acceptance.py` 与生产核心是**同一对象**
   （不是第二份近似算法）；生产代码不反向 import `evaluation` / `evals`。
B. **冻结三态**：`coverage <= 0` ⇒ `unaligned`；`< ALIGN_MIN` ⇒ `unaligned`；
   达到阈值但含 `unexplained` ⇒ `partially_aligned`；达到阈值且无 `unexplained` ⇒
   `aligned`。**只有 `aligned` 可引用**，且 verdict / coverage / residue_class
   **不能由调用方自报**（记录构造期重算比对）。
C. **旧 `al-1` 明确拒绝**：登记为 legacy、记录构造期拒绝、绝不产出。
D. **`width_fold` 不得恢复**：全角字符残差必须 `unexplained`；`engine_artifact` 白名单
   封闭且 `engine_artifact_subkind()` **不接受**任何页面文本参数。
E. **source occurrence 不得重复消费**：非重叠分配失败必须保守判 `unexplained`，
   并给出 occurrence 缺口诊断。
F. **不得猜测字符 offset**：每一段 `char_map` 都必须落在**真实 `LayoutSpan`** 的真实
   字符偏移上；`char_map` 与残差必须恰好互补覆盖块坐标。
G. **量化边界 fail-closed**：`exact_coverage < ALIGN_MIN <= quantize(exact_coverage)`
   时拒绝产出记录，块保持 `unaligned` / 不可引用。
H. **确定性**：同输入两次对齐逐字节一致；输入顺序不影响结论与顺序。
I. **只读边界**：不接触数据库、不 init / migrate、不联网、不写文件、不 import 上层包。
"""

from __future__ import annotations

import ast
import inspect
import json
import pathlib

import document_structure
from document_structure import aligner as A
from document_structure import schema as S
from document_structure import versions as V
from document_structure.canonical import SchemaValidationError, canonical_json
from document_structure.normalization import tight
from document_structure.schema import quantize

_results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

_PKG_DIR = pathlib.Path(document_structure.__file__).resolve().parent

_FORBIDDEN_TOKENS = ("300750", "宁德时代", "CATL", "gold", "answer_key")

_BANNED_IMPORTS = (
    "sqlite3", "requests", "httpx", "urllib", "socket", "openai", "anthropic",
    "chromadb", "bocha", "subprocess", "random", "uuid", "datetime", "time",
    "harness", "evaluation", "evals",
)

#: 合成 fixture 的固定 SHA（不读取任何真实文件）。
_SHA = "a" * 64

_EVIDENCE_SET_VERSION = "evs-ts3-synth"


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def plan(msg):
    _results["details"].append("SKIP " + msg)
    _results["skipped"] += 1


def raises(fn, exc, substr, msg):
    try:
        fn()
    except exc as e:  # noqa: PERF203
        if substr in str(e):
            return check(True, msg)
        return check(False, f"{msg}（异常文本不含 {substr!r}：{e}）")
    except Exception as e:  # noqa: BLE001
        return check(False, f"{msg}（异常类型 {type(e).__name__} 非 {exc.__name__}：{e}）")
    return check(False, f"{msg}（未抛出 {exc.__name__}）")


def _src(name: str) -> str:
    return (_PKG_DIR / name).read_text(encoding="utf-8")


def _imported_modules(source: str) -> set:
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _imported_names(source: str) -> set:
    """本模块从其它模块 `from ... import` 进来的名字（用于"不再引入 fold"检查）。"""
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
    return names


# ---------------------------------------------------------------------------
# 合成版式工厂（直接构造 TS1 公共类型；不依赖 PDF / 质量门）
# ---------------------------------------------------------------------------

def _line(index, parts, *, y=120.0, size=11.0, furniture=None, bold=False):
    if isinstance(parts, str):
        parts = [parts]
    text = " ".join(parts)
    spans = []
    cursor = 0
    for part in parts:
        spans.append(S.LayoutSpan(
            text=part, bbox=(72.0, y, 560.0, y + size), font="SimSun",
            size=size, is_bold=bold, char_start=cursor,
            char_end=cursor + len(part)))
        cursor += len(part) + 1
    return S.LayoutLine(
        line_index=index, bbox=(72.0, y, 560.0, y + size), spans=tuple(spans),
        text=text, is_furniture=furniture is not None, furniture_kind=furniture,
        reading_order=index + 1, column_index=0)


def _layout(pages, *, document_id="TS3_ALIGN_SYNTH"):
    layout_pages = tuple(
        S.LayoutPage(page_number=i, width=595.0, height=842.0, rotation=0,
                     lines=tuple(lines), has_text_layer=True)
        for i, lines in enumerate(pages, start=1))
    return S.PageLayout.create(
        document_id=document_id, document_version="sha256-" + _SHA[:16],
        company_id="999999", source_file_sha256=_SHA, pages=layout_pages)


def _block(text, *, page=1, index=0, layout=None, document_id=None,
           evidence_set_version=_EVIDENCE_SET_VERSION):
    """构造**携带完整身份**的 `EvidenceBlockInput`（TS3 §四）。

    身份由 `evidence.ids` 的正式接口在**这里**重算：合成夹具不保留任何"自报 id"
    参数——否则测试会不知不觉依赖一条生产路径上已经关闭的旁路。`document_id`
    默认取自待对齐的 `layout`，从而与 `layout` 的公司/文档/版本严格一致。
    """
    from evidence import ids as EIDS

    doc = document_id or (layout.document_id if layout is not None else "TS3_ALIGN_SYNTH")
    company = "999999"
    document_version = "sha256-" + _SHA[:16]
    content_hash = EIDS.content_hash(text, None)
    return A.EvidenceBlockInput(
        company_id=company, document_id=doc, document_version=document_version,
        evidence_set_version=evidence_set_version,
        evidence_block_id=EIDS.make_evidence_id(
            company, doc, document_version, evidence_set_version, page, index,
            content_hash),
        content_hash=content_hash, evidence_type="paragraph",
        page_number=page, block_index=index, text=text, structured_payload=None)


def _member(block):
    """块 → 权威成员身份投影（测试侧与生产侧 `_member_of` 同口径）。"""
    return A.EvidenceSetMember(
        evidence_id=block.evidence_block_id, content_hash=block.content_hash,
        evidence_type=block.evidence_type, page_number=block.page_number,
        block_index=block.block_index)


def _snapshot_for(layout, blocks, *, status="current",
                  gateway_version=None, evidence_set_version=None,
                  members=None, block_count=None, fingerprint=None,
                  company_id=None, document_id=None, document_version=None,
                  snapshot_version=None):
    """按给定块构造**权威快照**。

    测试夹具直接构造快照（生产侧必须由只读 gateway 注入）；任何一处 override 都用于
    构造"快照本身就不合法 / 与提交不符"的反例。
    """
    members = tuple(
        sorted((_member(b) for b in blocks), key=lambda m: m.sort_key)
        if members is None else members)
    set_version = evidence_set_version or _EVIDENCE_SET_VERSION
    base = dict(company_id=company_id if company_id is not None else layout.company_id,
                document_id=document_id if document_id is not None
                else layout.document_id,
                document_version=(document_version if document_version is not None
                                  else layout.document_version),
                evidence_set_version=set_version, status=status,
                gateway_version=(gateway_version if gateway_version is not None
                                 else V.EVIDENCE_SET_GATEWAY_VERSION),
                members=members)
    kwargs = dict(base,
                  block_count=(len(members) if block_count is None else block_count),
                  fingerprint=(A.snapshot_fingerprint(**base)
                               if fingerprint is None else fingerprint))
    if snapshot_version is not None:
        kwargs["snapshot_version"] = snapshot_version
    return A.EvidenceSetSnapshot(**kwargs)


class _SyncGateway:
    """最小只读 gateway 夹具（合成快照；只用于测试 `snapshot_from_gateway`）。"""

    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.gateway_version = snapshot.gateway_version

    def load_snapshot(self, *, company_id, document_id, document_version):
        return self._snapshot


_BODY = (
    "本公司报告期内经营情况稳定，主营业务收入保持增长。",
    "公司持续推进技术研发与产能建设，相关投入按计划执行。",
    "报告期内未发生对经营产生重大影响的事项，内控运行有效。",
    "公司治理结构完善，信息披露真实准确完整，无重大遗漏事项。",
)

_HEADER = "某公司2026年年度报告"

_L1 = "甲公司经营情况稳定ABC"
_L2 = "乙公司财务状况良好"
_L3 = "丙公司治理结构完善"


def _body_lines(start_index, count, y0=140.0):
    return [_line(start_index + i, _BODY[i % len(_BODY)], y=y0 + i * 22.0)
            for i in range(count)]


def _page_layout():
    """单页长正文版式（页眉 + 正文 8 行 + 页码；正文约 200 字符）。"""
    lines = [_line(0, _HEADER, y=72.0, furniture="header")]
    lines += _body_lines(1, 8)
    lines.append(_line(len(lines), "1", y=800.0, furniture="page_number"))
    return _layout([lines])


def _small_layout():
    """单页小版式：页眉 + 三条互不重叠的短内容行（含唯一 ASCII 片段 ABC）。"""
    lines = [
        _line(0, _HEADER, y=72.0, furniture="header"),
        _line(1, _L1, y=100.0),
        _line(2, _L2, y=122.0),
        _line(3, _L3, y=144.0),
    ]
    return _layout([lines], document_id="TS3_ALIGN_SMALL")


def _two_page_layout():
    lines_a = [_line(0, _HEADER, y=72.0, furniture="header")] + _body_lines(1, 4)
    lines_b = _body_lines(0, 4) + [_line(4, "2", y=800.0, furniture="page_number")]
    return _layout([lines_a, lines_b], document_id="TS3_ALIGN_TWO")


# ---------------------------------------------------------------------------
# A. 唯一生产实现与版本
# ---------------------------------------------------------------------------

def _test_unique_production_implementation() -> None:
    import evaluation.run_tree_layout_acceptance as RA
    shared = ("classify_residue", "displaced_evidence", "engine_artifact_subkind",
              "page_layout_text", "page_text_spans", "coverage_record",
              "numeric_like_ratio", "verdict_residue_class", "short_fragment")
    for name in shared:
        check(getattr(RA, name, None) is getattr(A, name, None),
              f"A1[identity] {name} 在评测侧与生产核心是同一对象")
    ra_src = (_PKG_DIR.parent / "evaluation" / "run_tree_layout_acceptance.py").read_text(
        encoding="utf-8")
    # 关键入口的三条唯一调用链：本地别名 / 直接调用 / 审计口径都指向生产核心
    check(RA._dominant is A.dominant_residue_class,
          "A1a[identity] 评测侧的汇总口径别名指向生产核心")
    check(getattr(RA, "A", None) is A,
          "A1b[identity] 评测侧模块里绑定的 aligner 就是生产核心模块")
    check("A.align_block(" in ra_src,
          "A1c[evaluation] 单块对齐经由生产核心的 A.align_block 调用")
    check("def align_block" not in ra_src,
          "A1d[evaluation] 评测侧没有自己的 align_block 定义")
    for name in ("classify_residue", "displaced_evidence", "engine_artifact_subkind",
                 "alignment_locate", "_nonoverlapping"):
        check(f"def {name}" not in ra_src,
              f"A2[evaluation] 评测侧没有第二份 {name} 定义")
    check("from document_structure import aligner" in ra_src
          or "import aligner" in ra_src,
          "A3[evaluation] 评测侧确实改为调用生产核心")
    check(V.ALIGNER_VERSION == "al-3",
          "A4 ALIGNER_VERSION 为当前权威版本 al-3（§九：对齐器语义变化必须升版）")
    check(V.ALIGN_MIN == 0.90, "A5 ALIGN_MIN 仍为冻结的 0.90")
    check(A.RECORD_REFUSAL_QUANTIZATION_BOUNDARY
          == "quantization_boundary_refused",
          "A6 记录拒绝原因码稳定")
    check(A.RESIDUE_CLASSES == S.RESIDUE_CLASS_SEVERITY,
          "A7 残差类别序复用 schema 的唯一一份定义")
    check(A.ENGINE_ARTIFACT_RULES == ("blank", "invisible_codepoint"),
          "A8 engine artifact 白名单封闭（两条）")
    # 演练：版本常量仍可分类；**旧版本不得与当前版本共用同一个版本号**
    check(V.classify_schema_version("ALIGNER_VERSION", "al-3") == "current",
          "A9 al-3 是 current")
    check(V.classify_schema_version("ALIGNER_VERSION", "al-1") == "legacy",
          "A10 al-1 被识别为 legacy")
    check(V.classify_schema_version("ALIGNER_VERSION", "al-2") == "legacy",
          "A11 al-2 被识别为 legacy（旧输入信任边界不得冒充权威集合输入）")
    check(V.legacy_versions("ALIGNER_VERSION") == ("al-1", "al-2"),
          "A11b al-1 / al-2 都已登记为历史版本")
    check(set(V.legacy_versions("ALIGNER_VERSION")) & {V.ALIGNER_VERSION} == set(),
          "A11c 当前版本不在自己的历史版本表里（不得自我降级）")


def _test_aligner_version_rejected() -> None:
    raises(lambda: S.TextAlignmentRecord.create(
        page_layout_id="pl-x", evidence_set_version=_EVIDENCE_SET_VERSION,
        page_number=1, block_index=0, evidence_block_id="eb-1",
        block_char_length=10, aligner_version="al-1"),
        SchemaValidationError, "aligner_version",
        "B1 记录构造期明确拒绝 al-1（旧算法不得复活）")
    raises(lambda: S.TextAlignmentRecord.create(
        page_layout_id="pl-x", evidence_set_version=_EVIDENCE_SET_VERSION,
        page_number=1, block_index=0, evidence_block_id="eb-1",
        block_char_length=10, aligner_version="al-2"),
        SchemaValidationError, "aligner_version",
        "B1b 记录构造期明确拒绝 al-2（旧对齐语义不得继续写新记录）")
    # 生产入口产出的每个块都必须是当前权威版本
    layout = _page_layout()
    blk = A.align_block(layout, _block(A.page_layout_text(layout, 1), layout=layout))
    check(blk.aligner_version == V.ALIGNER_VERSION,
          "B2 align_block 产出的版本必须是当前权威版本")
    check(blk.record is not None
          and blk.record.aligner_version == V.ALIGNER_VERSION,
          "B3 产出的记录版本必须是当前权威版本")


# ---------------------------------------------------------------------------
# C. 冻结三态与"不能自报"
# ---------------------------------------------------------------------------

def _test_three_states() -> None:
    layout = _page_layout()
    page_text = A.page_layout_text(layout, 1)

    # (1) 完全覆盖 → aligned 且可引用
    ok = A.align_block(layout, _block(page_text))
    check(ok.verdict == "aligned", f"C1 完全覆盖判 aligned（{ok.verdict}）")
    check(ok.is_citable is True, "C2 只有 aligned 可引用（aligned 时可引用）")
    check(ok.record is not None and ok.record.coverage == 1.0,
          "C3 aligned 块的覆盖率由重算得到 1.0")
    check(ok.residue_segments == () and ok.unmatched_chars == 0,
          "C4 完全覆盖块没有残差")
    check(ok.record.is_citable() is True, "C5 记录自身的 is_citable() 与块一致")

    # (2) 达到阈值但含 unexplained → partially_aligned 且不可引用
    partial = A.align_block(layout, _block(page_text + "犇猋麤龘", layout=layout))
    check(partial.verdict == "partially_aligned",
          f"C6 达到阈值但含 unexplained 判 partially_aligned（{partial.verdict}）")
    check(partial.is_citable is False,
          "C7 partially_aligned 不可引用（即使 coverage 很高）")
    check(partial.exact_coverage >= V.ALIGN_MIN,
          f"C8 该块 coverage 确实达到阈值（{partial.exact_coverage}）")
    check(partial.has_unexplained, "C9 该块确实含 unexplained 残差")
    check(not [s for s in partial.residue_segments
               if s.residue_class != "unexplained"],
          "C10 追加内容只产生 unexplained 残差")

    # (3) 低于阈值 → unaligned
    low = A.align_block(layout, _block("犇猋麤龘" * 5, layout=layout))
    check(low.verdict == "unaligned", f"C11 低于阈值判 unaligned（{low.verdict}）")
    check(low.is_citable is False, "C12 unaligned 不可引用")
    check(low.exact_coverage < V.ALIGN_MIN,
          f"C13 该块 coverage 确实低于阈值（{low.exact_coverage}）")

    # (4) 零长度块 → unaligned，且不得产生 aligned（记录类型同款约束）
    empty = A.align_block(layout, _block("   ", layout=layout))
    check(empty.empty_block and empty.block_char_length == 0,
          "C14 只含空白的块归一后为零长度块")
    check(empty.verdict == "unaligned" and empty.is_citable is False,
          "C15 零长度块一律 unaligned 且不可引用")
    check(S.compute_alignment_verdict(0.0, None, V.ALIGN_MIN) == "unaligned",
          "C16 coverage<=0 在公共真值表里就是 unaligned")

    # (5) 解释得当的残差（家具行）不改变可引用性 —— 冻结规则只排除 unexplained
    header_tight = tight(_HEADER)
    check(page_text.startswith(header_tight), "C17 版式首行确实是页眉家具行")
    body_only = page_text[len(header_tight):]
    combo = A.align_block(layout, _block(body_only + header_tight, layout=layout))
    check(combo.residue_class == "page_furniture",
          f"C18 尾部残差被归因为页眉家具行（{combo.residue_class}）")
    check(dict(combo.residue_subkind_counts).get("furniture_line", 0)
          == len(header_tight),
          "C19 家具行残差的子类是 furniture_line 且长度可核")
    check(combo.verdict == "aligned" and combo.is_citable is True,
          "C20 残差已解释（非 unexplained）时仍按覆盖率判 aligned")
    check(len(combo.record.char_map) >= 1,
          "C21 该块仍产出可定位的 char_map")


def _test_verdict_cannot_be_self_reported() -> None:
    layout = _page_layout()
    page_text = A.page_layout_text(layout, 1)
    ok = A.align_block(layout, _block(page_text))
    record = ok.record
    import dataclasses
    raises(lambda: dataclasses.replace(record, verdict="partially_aligned"),
           SchemaValidationError, "verdict",
           "D1 自报 verdict 被构造期重算比对拒绝")
    raises(lambda: dataclasses.replace(record, coverage=0.5),
           SchemaValidationError, "coverage",
           "D2 自报 coverage 被构造期重算比对拒绝")
    raises(lambda: dataclasses.replace(record, residue_class="unexplained"),
           SchemaValidationError, "residue_class",
           "D3 自报 residue_class 被构造期重算比对拒绝")
    # 只覆盖少数块字符的 char_map 不可能产出可引用记录（verdict 由重算决定，
    # 调用方没有"自报 aligned"的参数）。这个块**没有**任何 unexplained 残差
    # （唯一残差是已解释的 column_reorder），唯一能阻止它变成 aligned 的就是覆盖率
    # 本身。分区完整（`char_map ∪ residue` 恰好覆盖 `[0, 10)`）是构造前提：见 P1-E
    # 的公共分区验证器 —— 这里刻意不制造空洞，把变量留给覆盖率。
    tiny = S.TextAlignmentRecord.create(
        page_layout_id=layout.page_layout_id,
        evidence_set_version=_EVIDENCE_SET_VERSION, page_number=1, block_index=0,
        evidence_block_id="eb-1", block_char_length=10,
        char_map=((0, 1, 1, 0, 0, 0),), residue=((1, 10, "column_reorder"),))
    check(tiny.verdict == "unaligned" and tiny.is_citable() is False,
          "D5 覆盖率 10% 的记录重算为 unaligned 且不可引用")
    # NaN 不得穿过比较分支被伪装成"对齐不足"
    raises(lambda: S.compute_alignment_verdict(float("nan"), None, V.ALIGN_MIN),
           SchemaValidationError, "有限实数",
           "D6 NaN 覆盖率被显式拒绝（不得落进兜底档）")
    check(S.compute_alignment_verdict(0.95, None, None) == "partially_aligned"
          and S.compute_alignment_verdict(0.95, None, None) != "aligned",
          "D7 兼容档（阈值未裁决）永不产出 aligned")


# ---------------------------------------------------------------------------
# D. width_fold 不得恢复
# ---------------------------------------------------------------------------

def _test_width_fold_removed() -> None:
    src = _src("aligner.py")
    check("width_fold" not in A.ENGINE_ARTIFACT_RULES,
          "E1 engine artifact 白名单不含 width_fold")
    check("fold" not in _imported_names(src),
          "E2 生产对齐器不再引入 fold()（等价折叠不在对齐路径上）")
    check(A.engine_artifact_subkind("Ａ") is None,
          "E2b 全角字母不得被判成 engine artifact")
    check(A.engine_artifact_subkind("１２３４") is None,
          "E2c 全角数字不得被判成 engine artifact")
    params = list(inspect.signature(A.engine_artifact_subkind).parameters)
    check(len(params) == 1,
          f"E3 engine_artifact_subkind 只接受残差、不接受页面文本（{params}）")
    check(A.engine_artifact_subkind("") == "blank",
          "E4 空残差判 blank（依据 tight 归一）")
    check(A.engine_artifact_subkind(chr(0x200B) + chr(0x00AD))
          == "invisible_codepoint",
          "E5 不可见字符残差判 invisible_codepoint")

    # 端到端：全角残差必须 unexplained，且不得因此变成可引用
    layout = _small_layout()
    page_text = A.page_layout_text(layout, 1)
    cut = page_text.find("ABC")
    check(cut >= 0, "E6 合成页面确实含唯一 ASCII 片段 ABC")
    folded = page_text[:cut] + "ＡＢＣ" + page_text[cut + 3:]
    blk = A.align_block(layout, _block(folded, layout=layout))
    check(not [s for s in blk.residue_segments
               if s.residue_class == "engine_artifact"],
          "E7 全角残差不得被解释为 engine artifact（不得恢复 width_fold）")
    check(blk.has_unexplained and blk.residue_class == "unexplained",
          f"E8 全角残差保守判 unexplained（{blk.residue_class}）")
    check(blk.exact_coverage >= V.ALIGN_MIN,
          f"E9 该块覆盖率仍达到阈值（{blk.exact_coverage}）")
    check(blk.verdict == "partially_aligned" and blk.is_citable is False,
          "E10 该块只能判 partially_aligned、不可引用")
    # 旧规则的诊断参照：全角残差在页面里"折叠后能找到"，但那不是本轮允许的依据
    check(tight(folded).replace("ＡＢＣ", "ABC") in page_text,
          "E11 反证：折叠后确实能在同一页找到该文本（旧 al-1 会据此消除残差）")


def _test_residue_classification() -> None:
    layout = _small_layout()
    lines = list(layout.pages[0].lines)
    page_text = A.page_layout_text(layout, 1)
    spans = A.page_text_spans(lines)[1]

    def klass(residue):
        return A.classify_residue(layout, 1, page_text, spans, residue, lines)

    got = klass(tight(_HEADER))
    check(got[0] == "page_furniture" and got[2] == "furniture_line",
          f"F1 家具行残差判 page_furniture（{got}）")
    got = klass(tight(_L1))
    check(got[0] == "column_reorder" and got[2] == "line_segment",
          f"F2 单行连续片段判 column_reorder/line_segment（{got}）")
    got = klass(tight(_L1) + tight(_L3))
    check(got[0] == "column_reorder" and got[2] == "scattered_pieces",
          f"F3 跨行非连续但可完整绑定判 column_reorder/scattered_pieces（{got}）")
    got = klass("犇猋麤龘犇猋")
    check(got[0] == "unexplained",
          f"F4 页面里不存在的正文判 unexplained（{got}）")
    got = klass(chr(0x200B) * 3)
    check(got[0] == "engine_artifact" and got[2] == "invisible_codepoint",
          f"F5 不可见字符残差判 engine_artifact（{got}）")
    got = klass("ＡＢＣ")
    check(got[0] == "unexplained" and got[2] == "unresolved",
          f"F6 全角残差判 unexplained/unresolved（{got}）")
    check(A.classify_residue.__doc__ is not None, "F7 分类函数有文档化的证据强度序")


def _test_occurrence_not_reconsumed() -> None:
    layout = _small_layout()
    page_text = A.page_layout_text(layout, 1)
    spans = A.page_text_spans(list(layout.pages[0].lines))[1]
    residue = tight(_L2)[:5] * 3          # “乙公司财务”×3：页面里只有 1 处
    check(page_text.count(tight(_L2)[:5]) == 1,
          "G1 合成页面里该片段确实只有 1 处 occurrence")
    ev = A.displaced_evidence(residue, page_text, spans)
    check(ev["pieces"] == [],
          "G2 无法建立非重叠分配时不产出任何片段")
    check(ev["no_injective_assignment"] is True,
          "G3 明确标记 no_injective_assignment（不得降级为已解释）")
    deficit = ev.get("occurrence_deficit") or {}
    check(bool(deficit), f"G4 给出 occurrence 缺口诊断（{deficit}）")
    if deficit:
        first = sorted(deficit.values())[0]
        check(first["needed"] == 3 and first["available"] == 1,
              f"G5 缺口为“需 3 次 / 仅 1 次”（{first}）")
    got = A.classify_residue(layout, 1, page_text, spans, residue,
                             list(layout.pages[0].lines))
    check(got[0] == "unexplained" and got[2] == "residue_duplicated",
          f"G6 重复消费失败判 unexplained/residue_duplicated（{got}）")
    # 超长残差直接拒绝进入位移搜索（确定性上界）
    too_long = A.displaced_evidence("甲" * 2500, page_text, spans)
    check(too_long["reason"] == "too_long" and too_long["pieces"] == [],
          "G7 超长残差不进入位移搜索（确定性上界）")
    # 正常的完整绑定必须成功（对照组，证明上面的失败不是"总是失败"）
    ok_ev = A.displaced_evidence(tight(_L1) + tight(_L3), page_text, spans)
    check(ok_ev["leftover"] == "" and len(ok_ev["pieces"]) >= 2,
          "G8 对照：可绑定时确实绑成 ≥2 个两两不重叠的片段")


# ---------------------------------------------------------------------------
# E. char_map / 不猜偏移
# ---------------------------------------------------------------------------

def _verify_char_map(layout, block_text, alignment) -> list:
    """对象级复核：每段 char_map 必须落在真实行 / 真实 span 的真实偏移上。"""
    problems = []
    flat = tight(block_text)
    for seg in alignment.char_map:
        block_start, block_end, page_number, line_index, span_index, offset = seg
        line = layout.line_at(page_number, line_index)
        if line is None:
            problems.append(f"行不存在 {(page_number, line_index)}")
            continue
        if span_index >= len(line.spans):
            problems.append(f"span 不存在 {(page_number, line_index, span_index)}")
            continue
        span = line.spans[span_index]
        length = block_end - block_start
        if span.text[offset:offset + length] != flat[block_start:block_end]:
            problems.append(
                f"块 {flat[block_start:block_end]!r} 与 span 偏移内容 "
                f"{span.text[offset:offset + length]!r} 不一致")
    return problems


def _verify_tiling(block_text, alignment) -> bool:
    covered = [(s[0], s[1]) for s in alignment.char_map]
    covered += [(s.start, s.end) for s in alignment.residue_segments]
    covered.sort()
    cursor = 0
    for start, end in covered:
        if start != cursor:
            return False
        cursor = end
    return cursor == alignment.block_char_length == len(tight(block_text))


def _test_char_map_never_guesses() -> None:
    layout = _two_page_layout()
    p1 = A.page_layout_text(layout, 1)
    p2 = A.page_layout_text(layout, 2)
    cases = [
        ("完整对齐", p1, 1),
        ("含未解释尾巴", p1 + "犇猋麤龘", 1),
        ("第二页对齐", p2, 2),
        ("跨行重排", A.page_layout_text(layout, 2)[20:] + p2[:20], 2),
    ]
    for name, text, page in cases:
        blk = A.align_block(layout, _block(text, page=page, layout=layout))
        problems = _verify_char_map(layout, text, blk)
        check(not problems,
              f"H1[{name}] 每段 char_map 都落在真实 LayoutSpan 的真实偏移上"
              f"（{problems[:2]}）")
        check(_verify_tiling(text, blk),
              f"H2[{name}] char_map 与残差恰好互补覆盖块坐标（无空洞无重叠）")
        for seg in blk.char_map:
            check(seg[2] == page,
                  f"H3[{name}] char_map 的页码等于块所在页")
        check(len(blk.char_map) >= 1, f"H4[{name}] 该块确实有可定位映射")
    # 映射段必须逐段可核（不是整块一坨）
    longblk = A.align_block(layout, _block(p1, page=1, layout=layout))
    check(len(longblk.char_map) >= 2,
          f"H5 多行页面的 char_map 至少切成 2 段（{len(longblk.char_map)}）")
    # 页内行与 span 的边界确实被用上：段数不少于"匹配跨越的非空行数-1"
    check(all(isinstance(x[5], int) and x[5] >= 0 for x in longblk.char_map),
          "H6 span 内偏移必须为非负整数（不得为猜测值）")
    if hasattr(A, "_check_char_map_closure"):
        raises(lambda: A._check_char_map_closure(10, ((0, 5, 1, 0, 0, 0),), ()),
               A.AlignmentError, "",
               "H7 闭环检查拒绝「char_map 与残差覆盖不足」的映射")


# ---------------------------------------------------------------------------
# F. 量化边界 fail-closed
# ---------------------------------------------------------------------------

def _test_quantization_boundary_refused() -> None:
    # 构造 exact_coverage = 0.8998（< 0.90）但 quantize → 0.9（>= 0.90）的块：
    # 页 = P1(8998) + P2(1002)，块 = P2 + P1 → 残差 P2 是某条真实行的连续片段，
    # 因此它是**已解释**残差（column_reorder），旧记录语义会把它升为 aligned。
    p1 = ("甲乙丙丁戊己庚辛壬癸" * 900)[:8998]
    p2 = ("子丑寅卯辰巳午未申酉" * 101)[:1002]
    check(len(p1) == 8998 and len(p2) == 1002, "I1 合成页两段长度正确")
    layout = _layout([[ _line(0, p1, y=100.0), _line(1, p2, y=130.0) ]],
                     document_id="TS3_ALIGN_BOUNDARY")
    page_text = A.page_layout_text(layout, 1)
    check(page_text == p1 + p2, "I2 页文本为 P1+P2（顺序与行序一致）")
    blk = A.align_block(layout, _block(p2 + p1, layout=layout))
    check(blk.block_char_length == 10000, "I3 块长度为 10000")
    check(abs(blk.exact_coverage - 0.8998) < 1e-12,
          f"I4 未量化覆盖率恰为 0.8998（{blk.exact_coverage}）")
    check(quantize(blk.exact_coverage) == 0.9,
          f"I5 量化后覆盖率为 0.9（{quantize(blk.exact_coverage)}）")
    check(blk.exact_coverage < V.ALIGN_MIN <= quantize(blk.exact_coverage),
          "I6 命中量化边界区间")
    check(blk.residue_class == "column_reorder",
          f"I7 残差确实是**已解释**的 column_reorder（{blk.residue_class}）")
    check(blk.quantization_boundary_refused,
          "I8 生产对齐器标记 quantization_boundary_refused")
    check(blk.record is None and blk.record_refusal
          == A.RECORD_REFUSAL_QUANTIZATION_BOUNDARY,
          "I9 fail-closed：不产出该块的 TextAlignmentRecord")
    check(blk.verdict == "unaligned" and blk.is_citable is False,
          "I10 fail-closed：该块判 unaligned 且不可引用")
    check(blk.matched_chars == 8998, "I11 匹配字符数为 8998")
    # 张力本身（两条路给出相反结论）—— 这正是必须 fail-closed 的原因
    check(S.compute_alignment_verdict(blk.exact_coverage, "column_reorder",
                                      V.ALIGN_MIN) == "unaligned",
          "I12 按未量化 coverage 的业务规则：unaligned（不可引用）")
    check(S.compute_alignment_verdict(quantize(blk.exact_coverage),
                                      "column_reorder", V.ALIGN_MIN) == "aligned",
          "I13 按记录量化语义：本会判 aligned（可引用）—— 两者不可能同时成立")
    # 记录外的块信息仍然完整可核（下游不能"看到记录"才知道发生了什么）
    check(blk.to_dict()["record_refusal"] == "quantization_boundary_refused"
          and blk.to_dict()["alignment_id"] is None,
          "I14 拒绝事实进入可复核视图，且不冒充已有 alignment_id")
    summary = A.alignment_summary([blk])
    check(summary["records_refused"] == 1 and summary["citable_blocks"] == 0,
          "I15 汇总层把拒绝块计入 records_refused 且不计可引用")
    check(A.alignment_records([blk]) == (),
          "I16 拒绝块不出现在 alignment_records（不会被下游当已对齐材料）")

    # ---- §五：拒绝不是"记录缺失"，而是 typed、版本化、可读回的正式终态 ----
    refusal = blk.refusal_record
    check(refusal is not None
          and type(refusal).__name__ == "AlignmentRefusalRecord",
          "I17 该块的正式终态是 typed 的 AlignmentRefusalRecord")
    check(refusal.schema_version == V.ALIGN_REFUSAL_SCHEMA_VERSION,
          f"I18 拒绝终态携带自己的 wire 版本（{refusal.schema_version}）")
    check(refusal.refusal_reason == A.RECORD_REFUSAL_QUANTIZATION_BOUNDARY,
          "I19 拒绝原因码进入终态对象")
    check((refusal.matched_chars, refusal.block_char_length) == (8998, 10000),
          "I20 拒绝终态携带精确分子与分母（不是展示值）")
    check(refusal.exact_coverage == 8998 / 10000
          and refusal.coverage == quantize(refusal.exact_coverage),
          "I21 exact_coverage 是精确比值，coverage 只是展示值")
    check(refusal.is_citable() is False,
          "I22 拒绝终态恒不可引用")
    check(A.AlignmentRefusalRecord.from_dict(refusal.to_dict()).to_dict()
          == refusal.to_dict(),
          "I23 拒绝终态可序列化并逐字段读回一致")
    check(blk.terminal is refusal and blk.has_terminal,
          "I24 块恰好有一个正式终态：拒绝记录")
    check(A.alignment_terminals([blk]) == (refusal,),
          "I25 alignment_terminals 对拒绝块给出同一条终态")
    report = A.alignment_terminal_report([blk])
    check(report["total_blocks"] == 1 and report["terminals_emitted"] == 1
          and report["terminals_missing"] == 0,
          "I26 终态对账视图：1 个块 = 1 条终态，无缺失")
    check(summary["terminals_emitted"] == 1 and summary["terminals_missing"] == [],
          "I27 汇总层给出终态守恒事实")
    # 拒绝理由必须**真的成立**：不得用它掩盖一个正常块
    raises(lambda: S.AlignmentRefusalRecord.create(
        page_layout_id=layout.page_layout_id,
        evidence_set_version=_EVIDENCE_SET_VERSION, page_number=1, block_index=0,
        evidence_block_id=blk.evidence_block_id, block_char_length=10,
        matched_chars=10, residue=(), char_map=((0, 10, 1, 0, 0, 0),),
        refusal_reason=A.RECORD_REFUSAL_QUANTIZATION_BOUNDARY),
        SchemaValidationError, "拒绝理由不成立",
        "I28 完全对齐的块不得被「拒绝掉」（拒绝理由必须真的成立）")
    raises(lambda: S.AlignmentRefusalRecord.create(
        page_layout_id=layout.page_layout_id,
        evidence_set_version=_EVIDENCE_SET_VERSION, page_number=1, block_index=0,
        evidence_block_id=blk.evidence_block_id, block_char_length=10,
        matched_chars=1, residue=((1, 10, "unexplained"),),
        char_map=((0, 1, 1, 0, 0, 0),),
        refusal_reason=A.RECORD_REFUSAL_QUANTIZATION_BOUNDARY),
        SchemaValidationError, "拒绝理由不成立",
        "I29 覆盖率低但没有量化张力的块同样不得被拒绝")

    # ---- §五：当前 wire 版本携带精确分子；旧 als 载荷只读兼容、含义不变 ----
    normal = A.align_block(layout, _block(p1, page=1, index=1, layout=layout))
    check(normal.record is not None
          and normal.record.schema_version == V.ALIGN_SCHEMA_VERSION == "als-3",
          "I30 当前写路径只写 als-3 记录（§九：分区语义变化必须升版）")
    check(normal.record.schema_version
          not in V.legacy_versions("ALIGN_SCHEMA_VERSION"),
          "I30b 当前写路径不产出任何历史 wire 版本")
    check(normal.record.matched_chars == normal.matched_chars,
          "I31 记录的精确分子与块级事实一致")
    check(normal.record.to_dict()["matched_chars"] == normal.matched_chars,
          "I32 精确分子进入 wire 形态")
    legacy = dict(normal.record.to_dict())
    legacy.pop("matched_chars")
    legacy["schema_version"] = "als-1"
    legacy["alignment_id"] = S.derive_alignment_id(
        alignment_locator=legacy["alignment_locator"], schema_version="als-1",
        evidence_block_id=legacy["evidence_block_id"],
        block_char_length=legacy["block_char_length"], verdict=legacy["verdict"],
        coverage=legacy["coverage"], residue_class=legacy["residue_class"],
        residue=tuple(tuple(x) for x in legacy["residue"]),
        char_map=tuple(tuple(x) for x in legacy["char_map"]))
    read_back = S.TextAlignmentRecord.from_dict(legacy)
    check(read_back.schema_version == "als-1" and read_back.matched_chars is None,
          "I33 als-1 载荷仍可读入，且其精确分子保持缺席")
    check("matched_chars" not in read_back.to_dict(),
          "I34 als-1 读回后的 wire 形态与原文逐字段一致（不静默升级历史载荷）")
    check(read_back.verdict == S.compute_alignment_verdict(
        read_back.coverage, read_back.residue_class, V.ALIGN_MIN),
        "I35 als-1 载荷仍按历史语义（量化 coverage）重算 verdict")
    # als-1 携带 matched_chars 是**格式错误**，不是"顺手升级"
    raises(lambda: S.TextAlignmentRecord.from_dict({**legacy, "matched_chars": 1}),
           SchemaValidationError, "matched_chars",
           "I36 als-1 载荷不得携带 matched_chars（不得静默按新语义解释）")
    raises(lambda: S.TextAlignmentRecord.from_dict(
        {**legacy, "schema_version": "als-99"}),
           SchemaValidationError, "未知版本",
           "I37 未知的 align wire 版本仍 fail-closed")
    # 精确比值直接改变判定：展示 0.900 但精确低于阈值 ⇒ 拒绝，而不是 aligned
    check(S.compute_alignment_verdict_exact(8998, 10000, "column_reorder",
                                            V.ALIGN_MIN) == "unaligned",
          "I38 精确 0.8998 < 0.90 判 unaligned（不得先四舍五入）")
    check(S.compute_alignment_verdict(quantize(8998 / 10000), "column_reorder",
                                      V.ALIGN_MIN) == "aligned",
          "I39 对照：量化展示值 0.9 本会判 aligned——两者的差异即拒绝的理由")


# ---------------------------------------------------------------------------
# G. 确定性、集合级入口与只读边界
# ---------------------------------------------------------------------------

def _test_evidence_set_entry() -> None:
    layout = _two_page_layout()
    p1 = A.page_layout_text(layout, 1)
    p2 = A.page_layout_text(layout, 2)
    blocks = [
        _block(p2, page=2, index=1, layout=layout),
        _block(p1, page=1, index=0, layout=layout),
        _block(p1 + "犇猋", page=1, index=1, layout=layout),
    ]
    snap = _snapshot_for(layout, blocks)
    result = A.align_evidence_set(layout, blocks, snapshot=snap)
    check([(b.page_number, b.block_index) for b in result.blocks]
          == [(1, 0), (1, 1), (2, 1)],
          "J1 全量对齐结果按 (页, 块) 升序（与输入顺序无关）")
    check(result.aligner_version == V.ALIGNER_VERSION,
          "J2 集合级结果的版本为当前权威对齐器版本")
    check(result.summary["total_blocks"] == 3, "J3 汇总块数为 3")
    check(result.summary["aligned"] + result.summary["partially_aligned"]
          + result.summary["unaligned"] == 3,
          "J4 三态之和等于总数（无块蒸发）")
    check(result.summary["citable_blocks"] == result.summary["aligned"],
          "J5 可引用面恰等于 aligned 面")
    check(result.summary["records_emitted"] == 3,
          "J6 该批没有量化边界块，记录都应产出")
    check(len(A.alignment_records(result.blocks)) == 3,
          "J7 alignment_records 返回全部已产出记录")

    # 输入顺序改变不影响结论与顺序
    shuffled = A.align_evidence_set(layout, list(reversed(blocks)), snapshot=snap)
    check(canonical_json([b.to_dict() for b in shuffled.blocks])
          == canonical_json([b.to_dict() for b in result.blocks]),
          "J8 输入顺序改变不改变最终 JSON 与 alignment_id")
    check(shuffled.summary == result.summary, "J9 输入顺序改变不改变汇总")

    # 同一 (页, 块) 重复提交 → 拒绝（来源位置不得被重复消费）
    raises(lambda: A.align_evidence_set(layout, [blocks[0], blocks[0]],
                                        snapshot=snap),
           A.AlignmentError, "重复",
           "J10 同一 (页, 块) 重复提交被拒绝")
    # 混用 evidence_set_version → 拒绝（该块自身身份完整：set 只影响派生 id，
    # 不允许的是"同一次全量对齐里出现两个 set"）
    mixed = [blocks[0], _block(p2, page=2, index=0, layout=layout,
                               evidence_set_version="evs-other")]
    raises(lambda: A.align_evidence_set(layout, mixed, snapshot=snap),
           A.AlignmentError, "evidence_set_version",
           "J11 同一次全量对齐不得混用多个 evidence_set_version")
    # 块自报的 set 与**权威快照**不符时同样拒绝（自报值不得覆盖权威来源）
    other_set = _block(p1, page=1, index=0, layout=layout,
                       evidence_set_version="evs-other")
    raises(lambda: A.align_evidence_set(layout, [other_set], snapshot=snap),
           A.AlignmentError, "evidence_set_version",
           "J11b 块的 evidence_set_version 与权威快照不一致时拒绝")
    # 非 current 的证据集不得作为正式输入
    for status in ("building", "retired", "invalid"):
        raises(lambda s=status: A.align_evidence_set(
            layout, blocks, snapshot=_snapshot_for(layout, blocks, status=s)),
               A.AlignmentError, "current",
               f"J11c status={status} 的证据集不得作为正式对齐输入")
    # 不存在的页 → 拒绝（不得当成空页判 unaligned）
    raises(lambda: A.align_block(layout, _block(p1, page=99, layout=layout)),
           A.AlignmentError, "不存在",
           "J12 块指向不存在的页时拒绝，不得静默判空")
    # 类型错误 → 拒绝
    raises(lambda: A.align_block("layout", blocks[0]),
           A.AlignmentError, "PageLayout",
           "J13 align_block 需要真实 PageLayout 对象")
    raises(lambda: A.align_block(layout, "block"),
           A.AlignmentError, "EvidenceBlockInput",
           "J14 align_block 需要 EvidenceBlockInput")
    # EvidenceBlockInput 自身不变量（§四：身份必须重算、公司/文档/版本必须闭合）
    from evidence import ids as EIDS

    doc = layout.document_id
    version = layout.document_version
    company = layout.company_id
    text = "身份不变量"
    good_hash = EIDS.content_hash(text, None)
    good_id = EIDS.make_evidence_id(company, doc, version, _EVIDENCE_SET_VERSION,
                                    1, 0, good_hash)

    def full(**overrides):
        base = dict(company_id=company, document_id=doc, document_version=version,
                    evidence_set_version=_EVIDENCE_SET_VERSION,
                    evidence_block_id=good_id, content_hash=good_hash,
                    evidence_type="paragraph", page_number=1, block_index=0,
                    text=text, structured_payload=None)
        base.update(overrides)
        return base

    check(A.EvidenceBlockInput(**full()) is not None,
          "J15a 完整身份的输入可以构造（对照组：上面的失败不是「总是失败」）")
    for overrides, why in (
        (dict(page_number=0), "page_number 从 1 开始"),
        (dict(page_number=True), "page_number 不得为 bool"),
        (dict(block_index=-1), "block_index 非负"),
        (dict(evidence_block_id=""), "evidence_block_id 非空"),
        (dict(evidence_set_version=""), "evidence_set_version 非空"),
        (dict(company_id=""), "company_id 非空"),
        (dict(document_id=""), "document_id 非空"),
        (dict(document_version=""), "document_version 非空"),
        (dict(evidence_type=""), "evidence_type 非空"),
        (dict(text=123), "text 必须为字符串"),
        (dict(structured_payload="不是 dict"), "structured_payload 必须为 dict 或 None"),
        (dict(input_version="ebi-99"), "输入契约版本必须是当前版本"),
    ):
        raises(lambda o=overrides: A.EvidenceBlockInput(**full(**o)),
               A.AlignmentError, "", f"J15 输入不变量：{why}")

    # ---- §四 身份反例：伪造 / 篡改一律构造不出来 ----
    forged_hash = "b" * 64
    forged_id = EIDS.make_evidence_id(company, doc, version, _EVIDENCE_SET_VERSION,
                                      1, 0, forged_hash)
    raises(lambda: A.EvidenceBlockInput(**full(content_hash=forged_hash,
                                               evidence_block_id=forged_id)),
           A.AlignmentError, "content_hash",
           "J16a 自报 content_hash 与重算不一致 → 拒绝（不得采信自报内容身份）")
    raises(lambda: A.EvidenceBlockInput(**full(evidence_block_id=forged_id)),
           A.AlignmentError, "evidence_id",
           "J16b 同文同页但伪造 evidence_id → 拒绝")
    raises(lambda: A.EvidenceBlockInput(**full(text=text + "改")),
           A.AlignmentError, "content_hash",
           "J16c 文本被篡改后沿用旧 content_hash → 拒绝")
    raises(lambda: A.EvidenceBlockInput(
        **full(structured_payload={"a": 1})),
           A.AlignmentError, "content_hash",
           "J16d structured payload 被篡改后沿用旧 content_hash → 拒绝")
    # 不同公司 / 文档 / 版本：身份不同，且与 PageLayout 严格核对
    for field, other, why in (
        ("company_id", "888888", "跨公司"),
        ("document_id", "TS3_ALIGN_OTHER_DOC", "跨文档"),
        ("document_version", "sha256-" + "c" * 16, "跨版本"),
    ):
        moved = full(**{field: other})
        # 自报的 content/evidence id 必须随身份重算，否则连对象都构造不出来
        moved["content_hash"] = EIDS.content_hash(moved["text"],
                                                  moved["structured_payload"])
        moved["evidence_block_id"] = EIDS.make_evidence_id(
            moved["company_id"], moved["document_id"], moved["document_version"],
            moved["evidence_set_version"], 1, 0, moved["content_hash"])
        blk = A.EvidenceBlockInput(**moved)
        raises(lambda b=blk: A.align_block(layout, b), A.AlignmentError, field,
               f"J17{field} 身份完整但{why} → 与 PageLayout 不一致必须拒绝")
    # 文档号相同、公司不同同样必须拒绝（只核对文档号会放过这种错配）
    foreign = full(company_id="888888")
    foreign["content_hash"] = EIDS.content_hash(foreign["text"], None)
    foreign["evidence_block_id"] = EIDS.make_evidence_id(
        "888888", foreign["document_id"], foreign["document_version"],
        foreign["evidence_set_version"], 1, 0, foreign["content_hash"])
    raises(lambda: A.align_evidence_set(
        layout, [_block(p1, page=1, index=0, layout=layout),
                 A.EvidenceBlockInput(**foreign)], snapshot=snap),
        A.AlignmentError, "company_id",
        "J17e 同文档不同公司不得进入同一次全量对齐（FOREIGN_COMPANY）")
    # evidence set 是**集合级**约束：单块对齐只看公司/文档/版本，混用 set 由
    # `align_evidence_set` 拒绝（已在 J11/J11b 覆盖）；这里确认"set 不同但身份完整"
    # 的块本身不是非法输入，从而 J11 拒绝的是集合级事实而不是构造期假象。
    other_set = _block(p1, page=1, index=0, layout=layout,
                       evidence_set_version="evs-other")
    check(other_set.evidence_set_version == "evs-other"
          and A.align_block(layout, other_set).has_terminal,
          "J17f evidence set 不同的块自身身份完整、可单独对齐（混用由集合级入口拒绝）")


def _test_determinism() -> None:
    layout = _two_page_layout()
    text = A.page_layout_text(layout, 1) + "犇猋"
    a = A.align_block(layout, _block(text, layout=layout))
    b = A.align_block(layout, _block(text, layout=layout))
    check(canonical_json(a.to_dict()) == canonical_json(b.to_dict()),
          "K1 同输入两次对齐逐字节一致")
    check(a.record is not None and a.record.alignment_id == b.record.alignment_id,
          "K2 alignment_id 逐字节一致（内容寻址身份）")
    same_layout_again = _two_page_layout()
    c = A.align_block(same_layout_again, _block(text, layout=same_layout_again))
    check(a.record.alignment_id == c.record.alignment_id,
          "K3 重建同一版式后 alignment_id 仍一致（不依赖运行期状态）")
    check(a.page_layout_id == c.page_layout_id,
          "K4 重建同一版式的 page_layout_id 一致")
    # 汇总字典的键序确定
    s1, s2 = A.alignment_summary([a]), A.alignment_summary([b])
    check(canonical_json(s1) == canonical_json(s2), "K5 汇总结果确定")


def _test_readonly_and_hygiene() -> None:
    src = _src("aligner.py")
    for token in _FORBIDDEN_TOKENS:
        check(token not in src, f"L1 对齐器无公司 / 答案专用 token：{token!r}")
    imported = _imported_modules(src)
    banned = sorted(imported & set(_BANNED_IMPORTS))
    check(not banned, f"L2 对齐器无非确定 / 越界依赖（实际 {banned}）")
    for token in ("sqlite3", "open(", "requests", "INSERT INTO", "migration"):
        check(token not in src, f"L3 对齐器不含 {token!r}")
    check("datetime" not in imported and "time" not in imported,
          "L4 不影响确定性结果的时间依赖")
    # 生产核心不反向 import evaluation / evals
    for name in ("aligner.py", "outline_builder.py"):
        mods = _imported_modules(_src(name))
        check("evaluation" not in mods and "evals" not in mods,
              f"L5[{name}] 生产核心不反向 import evaluation / evals")
    # 自检必须全绿（模块自带 self_check 是 TS3 交付的一部分）
    report = A.self_check()
    check(report["ok"] is True,
          f"L6 aligner.self_check 全绿（{report['checks']}）")
    # 只读：不写任何文件
    check(not [n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id in ("open", "exec", "eval")],
          "L7 对齐器没有文件写入 / 动态执行调用")


def _test_record_closure_to_schema() -> None:
    """对齐结果与公共类型/真值表一致（不得与 TS1 冻结语义分叉）。"""
    layout = _page_layout()
    page_text = A.page_layout_text(layout, 1)
    blk = A.align_block(layout, _block(page_text + "犇猋", layout=layout))
    for name in ("verdict", "coverage", "residue_class"):
        check(getattr(blk.record, name) == blk.to_dict().get(name),
              f"M1 记录与可复核视图的 {name} 一致")
    check(tuple(tuple(x) for x in blk.to_dict()["char_map"]) == blk.record.char_map,
          "M1b 可复核视图的 char_map 与记录的 char_map 逐段一致")
    check(blk.record.block_char_length == blk.block_char_length
          and blk.record.page_number == blk.page_number
          and blk.record.block_index == blk.block_index,
          "M2 记录的定位字段与块一致")
    check(blk.verdict_residue_class
          == A.verdict_residue_class(dict(blk.residue_char_counts)),
          "M3 门槛残差类由残差字符数重算得出")
    check(not blk.has_unexplained
          or blk.verdict_residue_class == "unexplained",
          "M3b 含任何 unexplained 时门槛类必为 unexplained（不得被主类掩盖）")
    check(blk.record.verdict == S.compute_alignment_verdict(
        blk.record.coverage, blk.record.residue_class, V.ALIGN_MIN),
          "M4 记录 verdict 与公共真值表重算一致")


def main() -> dict:
    groups = (
        _test_unique_production_implementation,
        _test_aligner_version_rejected,
        _test_three_states,
        _test_verdict_cannot_be_self_reported,
        _test_width_fold_removed,
        _test_residue_classification,
        _test_occurrence_not_reconsumed,
        _test_char_map_never_guesses,
        _test_quantization_boundary_refused,
        _test_evidence_set_entry,
        _test_determinism,
        _test_record_closure_to_schema,
        _test_readonly_and_hygiene,
    )
    for fn in groups:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            _results["failed"] += 1
            _results["details"].append(
                f"FAIL {fn.__name__} 崩溃（该组后续反例未执行）："
                f"{type(e).__name__}: {e}")
    return _results


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=1))
    raise SystemExit(0 if _results["failed"] == 0 else 1)

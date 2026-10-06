"""TS3 §七 P1-E / §八 P1-F 聚焦反例：对齐终态分区不变量 + 权威证据集全集身份。

这个模块只覆盖两件事，且每条反例都是**收口前会失败**的那一类：

**§七 P1-E —— `char_map` / `residue` 必须是同一个公共校验器下的精确分区**

- `char_map` 内部升序不重叠；`residue` 内部升序不重叠；两者彼此不重叠；
- 并集**恰好**覆盖 `[0, block_char_length)`：首部 / 中部 / 尾部空洞全部拒绝；
- 重复覆盖（导致 `matched_chars` 虚高）必须被拒绝，而不是被"总数看起来对"掩盖；
- `matched_chars` 必须**精确等于** `char_map` 段长之和；
- 校验器是**唯一**权威：`TextAlignmentRecord` / `AlignmentRefusalRecord` 的构造与
  `from_dict` / `aligner.align_block` 写路径共用同一个函数 —— 只在私有写路径上校验
  等于"持久化对象反序列化本身不 fail-closed"；
- `aligned` / `partially_aligned` / `unaligned` / 拒绝终态都能正常 round-trip。

**§八 P1-F —— 正式集合入口必须绑定权威 EvidenceSet 全成员**

- 复用只读 gateway 产出的 `EvidenceSetSnapshot`（版本化 typed 对象）作为**唯一**成员
  权威；正式集合入口要求提交成员与快照**精确相等**：少一个 / 多一个 / 重复 /
  type、hash、page、block 任一不符 / 非 current / 身份错配一律 fail-closed；
- 单块入口 `align_block` 仍可作为内部算法入口，但"完成了整个 EvidenceSet"这句话只能
  由 `align_evidence_set` 给出；
- 身份缺项（空 company / document / version）不得被 adapter 用调用方参数补齐；
- 完整真实形态的集合（本模块用 292 块合成集合压测）正常通过且终态数量守恒。

合成夹具复用 `evals.test_tree_aligner` 的版式 / 块工厂（**只复用不复制**）。
"""

from __future__ import annotations

import ast
import inspect
import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import evals.test_tree_aligner as TA  # noqa: E402  合成版式 / 块工厂（复用）

from document_structure import aligner as A  # noqa: E402
from document_structure import schema as S  # noqa: E402
from document_structure import versions as V  # noqa: E402

_results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

_PAGE_LAYOUT_ID = "pl-ts3-partition-synth"
_EVIDENCE_SET_VERSION = TA._EVIDENCE_SET_VERSION


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def raises(fn, exc, substr, msg):
    """要求 `fn()` 抛出 `exc` 且异常文本含 `substr`（空串表示只要求类型）。"""
    try:
        fn()
    except exc as error:
        if substr in str(error):
            return check(True, msg)
        return check(False, f"{msg}（异常文本不含 {substr!r}：{error}）")
    except Exception as error:  # noqa: BLE001
        return check(False,
                     f"{msg}（异常类型 {type(error).__name__} 非 {exc.__name__}：{error}）")
    return check(False, f"{msg}（未抛出 {exc.__name__}）")


def _partition(**over):
    """一个**合法**分区载荷（长度 100）：`char_map` [0,60) + `residue` [60,100)。

    每个反例只改动其中一处，因此"被拒绝"只可能来自被改动的那条不变量。
    """
    base = dict(block_char_length=100,
                char_map=((0, 30, 1, 0, 0, 0), (30, 60, 1, 1, 0, 0)),
                residue=((60, 100, "unexplained"),), matched_chars=60)
    base.update(over)
    return base


def _assert_ok(payload, msg):
    try:
        recomputed = S.validate_alignment_partition(**payload)
    except Exception as error:  # noqa: BLE001
        return check(False, f"{msg}（合法载荷被拒：{type(error).__name__}: {error}）")
    return check(recomputed == sum(seg[1] - seg[0] for seg in payload["char_map"]),
                 msg)


def _assert_bad(payload, substr, msg):
    return raises(lambda: S.validate_alignment_partition(**payload),
                  S.SchemaValidationError, substr, msg)


def _code_without_docstrings(fn) -> str:
    """`fn` 的源码，**去掉 docstring** 后重新 unparse（用于源码级写法复核）。

    直接对原文做子串匹配会把"旧写法长什么样"的说明文字也算成命中，因此这里用 AST
    摘掉模块 / 函数 / 类的首条字符串语句，再序列化回代码。
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


# ---------------------------------------------------------------------------
# A. 唯一权威分区校验器
# ---------------------------------------------------------------------------

def _test_single_partition_authority() -> None:
    check(A.validate_alignment_partition is S.validate_alignment_partition,
          "A1 对齐器写路径与公共 schema 共用**同一个**分区校验函数（无第二份实现）")
    check(V.ALIGNMENT_PARTITION_VALIDATOR_VERSION == "apv-1"
          and V.classify_schema_version("ALIGNMENT_PARTITION_VALIDATOR_VERSION",
                                        "apv-1") == "current",
          "A2 分区校验器版本 apv-1 已注册且为 current（语义变化必须升版，不静默改）")
    schema_src = (Path(S.__file__)).read_text(encoding="utf-8")
    check(schema_src.count("validate_alignment_partition(") >= 3,
          "A3 两个记录类型 + 校验器本体都走同一入口"
          f"（源码出现 {schema_src.count('validate_alignment_partition(')} 次）")
    aligner_src = Path(A.__file__).read_text(encoding="utf-8")
    check("validate_alignment_partition(" in aligner_src,
          "A4 对齐器写路径调用的是公共校验器（不是自己另写一套闭合规则）")
    # 只读兼容 / 只读解析都必须经过构造路径，因此反序列化天然 fail-closed。
    for cls in (S.TextAlignmentRecord, S.AlignmentRefusalRecord):
        check("from_dict" in dir(cls) and "__post_init__" in dir(cls),
              f"A5 {cls.__name__} 的 from_dict 经构造路径（构造期校验 = 读回期校验）")


# ---------------------------------------------------------------------------
# B. 分区反例（直接打公共校验器）
# ---------------------------------------------------------------------------

def _test_partition_counterexamples() -> None:
    _assert_ok(_partition(), "B0 对照：合法分区通过并返回重算分子（60）")

    # char_map 内部重叠：30..50 与 40..60 交叉
    _assert_bad(_partition(char_map=((0, 50, 1, 0, 0, 0), (40, 60, 1, 1, 0, 0))),
                "重叠", "B1 char_map 内部重叠被拒绝")
    # residue 内部重叠
    _assert_bad(_partition(residue=((60, 80, "unexplained"), (70, 100, "unexplained"))),
                "重叠", "B2 residue 内部重叠被拒绝")
    # char_map 与 residue 交叉重叠
    _assert_bad(_partition(residue=((50, 100, "unexplained"),)),
                "重叠", "B3 char_map 与 residue 交叉重叠被拒绝")
    # 首部空洞：[0,10) 无人覆盖
    _assert_bad(_partition(char_map=((10, 30, 1, 0, 0, 0), (30, 60, 1, 1, 0, 0)),
                           matched_chars=None),
                "空洞", "B4 首部空洞被拒绝")
    # 中部空洞：[30,40) 无人覆盖（char_map 60 结束 + residue 从 60 开始）
    _assert_bad(_partition(char_map=((0, 30, 1, 0, 0, 0), (40, 60, 1, 1, 0, 0)),
                           matched_chars=None),
                "空洞", "B5 中部空洞被拒绝")
    # 尾部空洞：并集止于 90 < 100
    _assert_bad(_partition(residue=((60, 90, "unexplained"),), matched_chars=None),
                "空洞", "B6 尾部空洞被拒绝")
    # 重复覆盖：把 [0,30) 再写一遍（数学上"多覆盖"），matched_chars 随之虚高
    _assert_bad(_partition(char_map=((0, 30, 1, 0, 0, 0), (0, 30, 1, 0, 0, 0),
                                     (30, 60, 1, 1, 0, 0)),
                           matched_chars=90),
                "重叠", "B7 重复覆盖被拒绝（不得让 matched_chars 虚高）")
    # matched_chars 与 char_map 不符（虚高但不改分区）
    _assert_bad(_partition(matched_chars=90), "matched_chars",
                "B8 matched_chars 与 char_map 段长之和不符时被拒绝（不得自报分子）")
    # 越界：residue 到 120 > 100
    _assert_bad(_partition(residue=((60, 120, "unexplained"),), matched_chars=None),
                "越出块长度", "B9 区间越出块长度被拒绝")
    # 倒序 / 零长度段
    _assert_bad(_partition(char_map=((30, 30, 1, 0, 0, 0), (30, 60, 1, 1, 0, 0)),
                           matched_chars=None),
                "0 <= start < end", "B10 零长度段被拒绝")
    # 端点不是 int / 是 bool
    _assert_bad(_partition(char_map=((0, True, 1, 0, 0, 0), (True, 60, 1, 1, 0, 0)),
                           matched_chars=None),
                "int", "B11 非 int（或 bool）端点被拒绝")
    # 残差分类必须属于封闭集合
    _assert_bad(_partition(residue=((60, 100, "not_a_class"),), matched_chars=None),
                "分类", "B12 残差分类越出封闭集合被拒绝")
    # 空块：长度 0 且双空分区**必须**通过（不能把"什么都没有"误判成空洞）
    _assert_ok(_partition(block_char_length=0, char_map=(), residue=(),
                          matched_chars=0),
               "B13 长度为 0 的空块（双空分区）通过")


# ---------------------------------------------------------------------------
# C. TextAlignmentRecord：构造与反序列化都 fail-closed
# ---------------------------------------------------------------------------

def _record(**over):
    """一个**合法**的当前版本记录（coverage 1.0 → aligned）。"""
    base = dict(page_layout_id=_PAGE_LAYOUT_ID,
                evidence_set_version=_EVIDENCE_SET_VERSION,
                page_number=1, block_index=0,
                evidence_block_id="eb-" + "0" * 16,
                block_char_length=100,
                residue=(), char_map=((0, 100, 1, 0, 0, 0),))
    base.update(over)
    return S.TextAlignmentRecord.create(**base)


def _test_text_alignment_record_partition() -> None:
    record = _record()
    check(record.verdict == "aligned" and record.is_citable(),
          "C1 对照：完全匹配记录重算为 aligned 且可引用")
    check(S.TextAlignmentRecord.from_dict(record.to_dict()).to_dict()
          == record.to_dict(),
          "C2 aligned 记录 round-trip 逐字段一致（含 alignment_id）")

    partial = _record(char_map=((0, 95, 1, 0, 0, 0),),
                      residue=((95, 100, "unexplained"),))
    check(partial.verdict == "partially_aligned" and not partial.is_citable(),
          "C3 达阈值但仍有 unexplained 残差 → partially_aligned 且不可引用")
    check(S.TextAlignmentRecord.from_dict(partial.to_dict()).to_dict()
          == partial.to_dict(),
          "C4 partially_aligned 记录 round-trip 逐字段一致")

    unaligned = _record(char_map=(), residue=((0, 100, "unexplained"),))
    check(unaligned.verdict == "unaligned" and not unaligned.is_citable(),
          "C5 无匹配 → unaligned 且不可引用")
    check(S.TextAlignmentRecord.from_dict(unaligned.to_dict()).to_dict()
          == unaligned.to_dict(),
          "C6 unaligned 记录 round-trip 逐字段一致")

    # ---- from_dict 层面：损坏的分区不得被读回成合法终态 ----
    payload = record.to_dict()

    def tampered(mutate, msg, substr="分区"):
        data = json.loads(json.dumps(payload, ensure_ascii=False))
        mutate(data)
        raises(lambda: S.TextAlignmentRecord.from_dict(data),
               S.SchemaValidationError, substr, msg)

    def _hole(data):
        # 中部空洞：[40,70) 既不在 char_map 也不在 residue。其余字段（coverage /
        # matched_chars / verdict / residue_class）都按**这份**分区重算，因此被拒绝
        # 只可能来自"空洞"这一条，而不是被别的自报不一致顺手挡下。
        data["char_map"] = [[0, 40, 1, 0, 0, 0], [70, 100, 1, 1, 0, 0]]
        data["residue"] = []
        data["residue_class"] = None
        data["matched_chars"] = 70
        data["coverage"] = 0.7
        data["verdict"] = "unaligned"
        return data

    tampered(_hole, "C7 读回时 char_map 存在中部空洞 → fail-closed", substr="空洞")

    def _cross(data):
        data["char_map"] = [[0, 60, 1, 0, 0, 0]]
        data["residue"] = [[50, 100, "unexplained"]]
        data["residue_class"] = "unexplained"
        data["matched_chars"] = 60
        data["coverage"] = 0.6
        data["verdict"] = "unaligned"
        return data

    tampered(_cross, "C8 读回时 char_map 与 residue 交叉重叠 → fail-closed",
             substr="重叠")

    def _inflate(data):
        data["matched_chars"] = 200
        return data

    tampered(_inflate, "C9 读回时 matched_chars 虚高（与 char_map 不符）→ fail-closed",
             substr="matched_chars")

    def _tail_hole(data):
        data["char_map"] = [[0, 60, 1, 0, 0, 0]]
        data["residue"] = []
        data["residue_class"] = None
        data["matched_chars"] = 60
        data["coverage"] = 0.6
        data["verdict"] = "unaligned"
        return data

    tampered(_tail_hole, "C10 读回时并集止于块中间（尾部空洞）→ fail-closed",
             substr="空洞")

    def _dup(data):
        data["char_map"] = [[0, 30, 1, 0, 0, 0], [0, 30, 1, 0, 0, 0],
                            [30, 60, 1, 1, 0, 0]]
        data["residue"] = [[60, 100, "unexplained"]]
        data["residue_class"] = "unexplained"
        data["matched_chars"] = 90
        data["coverage"] = 0.9
        data["verdict"] = "partially_aligned"
        return data

    tampered(_dup, "C11 读回时重复覆盖（matched_chars 虚高）→ fail-closed",
             substr="重叠")

    # 对照：同一份载荷的分区改成**合法**后必须能读回 —— 证明上面的拒绝来自分区本身，
    # 而不是"这份载荷无论怎么改都读不回"。
    legal = json.loads(json.dumps(payload, ensure_ascii=False))
    legal["char_map"] = [[0, 40, 1, 0, 0, 0], [40, 100, 1, 1, 0, 0]]
    legal["residue"] = []
    legal["residue_class"] = None
    legal["matched_chars"] = 100
    legal["coverage"] = 1.0
    legal["alignment_id"] = S.derive_alignment_id(
        alignment_locator=legal["alignment_locator"],
        schema_version=legal["schema_version"],
        evidence_block_id=legal["evidence_block_id"], block_char_length=100,
        verdict="aligned", coverage=1.0, residue_class=None, residue=(),
        char_map=((0, 40, 1, 0, 0, 0), (40, 100, 1, 1, 0, 0)), matched_chars=100)
    try:
        check(S.TextAlignmentRecord.from_dict(legal).verdict == "aligned",
              "C12 对照：同一载荷的分区补回闭环后可读回（拒绝来自分区而不是载荷形态）")
    except S.SchemaValidationError as error:
        check(False, f"C12 对照：合法分区被拒（{error}）")


# ---------------------------------------------------------------------------
# D. AlignmentRefusalRecord：同一公共校验器
# ---------------------------------------------------------------------------

def _refusal(**over):
    """合法拒绝终态：`exact_coverage < ALIGN_MIN <= coverage`（量化边界张力）。

    `matched_chars=89996 / block_char_length=100000` → 精确 0.89996 低于 0.90，而
    量化展示值 0.90 不低于它 —— 这正是「量化边界被拒」的**唯一**合法形态。
    """
    base = dict(page_layout_id=_PAGE_LAYOUT_ID,
                evidence_set_version=_EVIDENCE_SET_VERSION,
                page_number=1, block_index=0,
                evidence_block_id="eb-" + "1" * 16,
                block_char_length=100000, matched_chars=89996,
                residue=((89996, 100000, "unexplained"),),
                char_map=((0, 89996, 1, 0, 0, 0),),
                refusal_reason="quantization_boundary_refused")
    base.update(over)
    return S.AlignmentRefusalRecord.create(**base)


def _test_refusal_record_partition() -> None:
    refusal = _refusal()
    check(refusal.exact_coverage < V.ALIGN_MIN <= refusal.coverage,
          f"D1 对照：量化边界张力成立（{refusal.exact_coverage} < {V.ALIGN_MIN} "
          f"<= {refusal.coverage}）")
    check(not refusal.is_citable(),
          "D2 拒绝终态恒不可引用（不是可引用材料的降级版本）")
    check(refusal.exact_ratio() == (89996, 100000),
          "D3 拒绝依据可被外部逐字复算（精确整数比值）")
    check(S.AlignmentRefusalRecord.from_dict(refusal.to_dict()).to_dict()
          == refusal.to_dict(),
          "D4 拒绝终态 round-trip 逐字段一致")

    payload = refusal.to_dict()

    def tampered(mutate, msg, substr="分区"):
        data = json.loads(json.dumps(payload, ensure_ascii=False))
        mutate(data)
        raises(lambda: S.AlignmentRefusalRecord.from_dict(data),
               S.SchemaValidationError, substr, msg)

    def _hole(data):
        # 尾部空洞：[89996, 100000) 无人覆盖（分区校验在"拒绝理由是否成立"之前，
        # 因此这里不必再维持量化边界张力）。
        data["char_map"] = [[0, 89996, 1, 0, 0, 0]]
        data["residue"] = []
        data["residue_class"] = None
        data["matched_chars"] = 89996
        data["exact_coverage"] = 0.89996
        data["coverage"] = 0.9
        return data

    tampered(_hole, "D5 读回时拒绝记录分区有尾部空洞 → fail-closed", substr="空洞")

    def _cross(data):
        data["char_map"] = [[0, 95000, 1, 0, 0, 0]]
        data["residue"] = [[89996, 100000, "unexplained"]]
        data["matched_chars"] = 95000
        data["exact_coverage"] = 0.95
        data["coverage"] = 0.95
        return data

    tampered(_cross, "D6 读回时拒绝记录 char_map 与 residue 交叉重叠 → fail-closed",
             substr="重叠")

    def _inflate(data):
        data["matched_chars"] = 99999
        data["exact_coverage"] = 0.99999
        return data

    tampered(_inflate,
             "D7 读回时拒绝记录 matched_chars 虚高 → fail-closed",
             substr="matched_chars")

    # 构造期（不经 from_dict）同样 fail-closed：损坏分区不能被"手工构造"出来。
    raises(lambda: S.AlignmentRefusalRecord.create(
        page_layout_id=_PAGE_LAYOUT_ID, evidence_set_version=_EVIDENCE_SET_VERSION,
        page_number=1, block_index=0, evidence_block_id="eb-" + "1" * 16,
        block_char_length=100, matched_chars=60,
        residue=((50, 100, "unexplained"),),
        char_map=((0, 60, 1, 0, 0, 0),),
        refusal_reason="quantization_boundary_refused"),
        Exception, "重叠", "D8 构造期拒绝交叉重叠的分区（不只反序列化）")


# ---------------------------------------------------------------------------
# E. §八 权威证据集快照：全成员身份
# ---------------------------------------------------------------------------

def _gateway_layout():
    return TA._layout([TA._body_lines(0, 4)], document_id="TS3_ALIGN_SNAP")


def _member_of(block):
    return A.EvidenceSetMember(
        evidence_id=block.evidence_block_id, content_hash=block.content_hash,
        evidence_type=block.evidence_type, page_number=block.page_number,
        block_index=block.block_index)


def _test_snapshot_identity() -> None:
    layout = _gateway_layout()
    text = A.page_layout_text(layout, 1)[:20]
    blocks = [TA._block(text, page=1, index=i, layout=layout) for i in range(3)]
    members = tuple(_member_of(b) for b in blocks)
    snapshot = A.EvidenceSetSnapshot(
        company_id=layout.company_id, document_id=layout.document_id,
        document_version=layout.document_version,
        evidence_set_version=_EVIDENCE_SET_VERSION, status="current",
        members=members, block_count=3,
        fingerprint=A.snapshot_fingerprint(
            company_id=layout.company_id, document_id=layout.document_id,
            document_version=layout.document_version,
            evidence_set_version=_EVIDENCE_SET_VERSION, status="current",
            gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION, members=members),
        gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION)
    check(snapshot.block_count == 3 and snapshot.member_identities()
          == tuple(m.identity for m in members),
          "E1 快照绑定全部成员的五项身份（evidence_id / hash / type / page / block）")
    check(A.snapshot_fingerprint(
        company_id=layout.company_id, document_id=layout.document_id,
        document_version=layout.document_version,
        evidence_set_version=_EVIDENCE_SET_VERSION, status="current",
        gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION,
        members=tuple(reversed(members))) == snapshot.fingerprint,
        "E2 指纹对成员**顺序**不变（规范顺序入指纹）")
    check(snapshot.fingerprint != A.snapshot_fingerprint(
        company_id=layout.company_id, document_id=layout.document_id,
        document_version=layout.document_version,
        evidence_set_version=_EVIDENCE_SET_VERSION, status="building",
        gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION, members=members),
        "E3 状态变化改变指纹（『哪一份证据集』不是调用方一句话）")
    check(snapshot.fingerprint != A.snapshot_fingerprint(
        company_id=layout.company_id, document_id=layout.document_id,
        document_version=layout.document_version,
        evidence_set_version=_EVIDENCE_SET_VERSION, status="current",
        gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION, members=members[:-1]),
        "E4 少一个成员即改变指纹（全集身份可分辨缺失）")
    check(snapshot.identity()["fingerprint"] == snapshot.fingerprint
          and snapshot.identity()["gateway_version"] == V.EVIDENCE_SET_GATEWAY_VERSION,
          "E5 快照身份进入集合级身份（含 gateway 版本与全成员指纹）")

    # ---- 快照自身的反例（构造即 fail-closed）----
    def build(**over):
        base = dict(company_id=layout.company_id, document_id=layout.document_id,
                    document_version=layout.document_version,
                    evidence_set_version=_EVIDENCE_SET_VERSION, status="current",
                    members=members, block_count=3,
                    gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION)
        base.update(over)
        # 指纹按**改后**的身份重算，因此"被拒绝"只来自被改动的那条不变量，
        # 而不是被自报指纹不一致顺手挡下。
        base.setdefault("fingerprint", A.snapshot_fingerprint(
            company_id=base["company_id"], document_id=base["document_id"],
            document_version=base["document_version"],
            evidence_set_version=base["evidence_set_version"], status=base["status"],
            gateway_version=base["gateway_version"], members=base["members"]))
        return A.EvidenceSetSnapshot(**base)

    raises(lambda: build(block_count=2), A.AlignmentError, "block_count",
           "E6 block_count 与成员数分叉 → 拒绝（身份清单与块数不得分叉）")
    raises(lambda: build(members=tuple(reversed(members)),
                         fingerprint=A.snapshot_fingerprint(
                             company_id=layout.company_id,
                             document_id=layout.document_id,
                             document_version=layout.document_version,
                             evidence_set_version=_EVIDENCE_SET_VERSION,
                             status="current",
                             gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION,
                             members=tuple(reversed(members)))),
           A.AlignmentError, "升序",
           "E7 成员未按规范顺序 → 拒绝（同一集合不得有两个指纹）")
    raises(lambda: build(members=(members[0], members[0]), block_count=2,
                         fingerprint=A.snapshot_fingerprint(
                             company_id=layout.company_id,
                             document_id=layout.document_id,
                             document_version=layout.document_version,
                             evidence_set_version=_EVIDENCE_SET_VERSION,
                             status="current",
                             gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION,
                             members=(members[0], members[0]))),
           A.AlignmentError, "", "E8 成员重复（页,块）→ 拒绝")
    raises(lambda: build(members=(), block_count=0,
                         fingerprint=A.snapshot_fingerprint(
                             company_id=layout.company_id,
                             document_id=layout.document_id,
                             document_version=layout.document_version,
                             evidence_set_version=_EVIDENCE_SET_VERSION,
                             status="current",
                             gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION,
                             members=())),
           A.AlignmentError, "空", "E9 空成员集 → 拒绝（不得把 0 条终态说成通过）")
    for field in ("company_id", "document_id", "document_version",
                  "evidence_set_version", "gateway_version"):
        raises(lambda f=field: build(**{f: ""}), A.AlignmentError, "非空字符串",
               f"E10 快照身份缺项 {field} 为空 → 拒绝（不得由调用方补齐）")
    raises(lambda: build(fingerprint="0" * 64), A.AlignmentError, "fingerprint",
           "E11 指纹自报（与全成员重算不符）→ 拒绝")
    # 非 current：快照对象如实记录状态，但**正式输入**必须由 `assert_current()` 拦下。
    building = build(status="building")
    check(building.status == "building",
          "E12a 快照如实记录 status（不把 building 静默改写成 current）")
    raises(building.assert_current, A.AlignmentError, "current",
           "E12 非 current 快照不得作为正式对齐输入")
    # gateway 版本进入身份：同一份成员、不同来源版本必须是两个可分辨的身份。
    other_gw = build(gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION + "-other")
    check(other_gw.identity()["gateway_version"] != snapshot.identity()["gateway_version"]
          and other_gw.fingerprint != snapshot.fingerprint,
          "E13 来源版本不同的两份快照指纹不同（旧自报输入与新权威输入可分辨）")
    raises(lambda: A.EvidenceSetMember(evidence_id="", content_hash="a" * 64,
                                       evidence_type="paragraph", page_number=1,
                                       block_index=0),
           A.AlignmentError, "非空", "E14 成员身份缺项（空 evidence_id）→ 拒绝")
    raises(lambda: A.EvidenceSetMember(evidence_id="e1", content_hash="a" * 64,
                                       evidence_type="not_a_type", page_number=1,
                                       block_index=0),
           A.AlignmentError, "封闭集合", "E15 成员 evidence_type 越出封闭集合 → 拒绝")

    # ---- 只读 gateway 注入：版本可核验 ----
    good = TA._SyncGateway(snapshot)
    check(A.snapshot_from_gateway(good, company_id=layout.company_id,
                                  document_id=layout.document_id,
                                  document_version=layout.document_version)
          .fingerprint == snapshot.fingerprint,
          "E16 只读 gateway 注入的快照经核验后可直接使用")

    class _BadVersion:
        gateway_version = V.EVIDENCE_SET_GATEWAY_VERSION + "-x"

        def load_snapshot(self, **kwargs):
            return snapshot

    class _NoVersion:
        def load_snapshot(self, **kwargs):
            return snapshot

    class _NotSnapshot:
        gateway_version = V.EVIDENCE_SET_GATEWAY_VERSION

        def load_snapshot(self, **kwargs):
            return {"members": []}

    raises(lambda: A.snapshot_from_gateway(_BadVersion(), company_id="1",
                                           document_id="d", document_version="v"),
           A.AlignmentError, "gateway_version",
           "E17 gateway 自报版本与快照记录不一致 → 拒绝（来源版本不可核验）")
    raises(lambda: A.snapshot_from_gateway(_NoVersion(), company_id="1",
                                           document_id="d", document_version="v"),
           A.AlignmentError, "gateway_version", "E18 gateway 不声明版本 → 拒绝")
    raises(lambda: A.snapshot_from_gateway(_NotSnapshot(), company_id="1",
                                           document_id="d", document_version="v"),
           A.AlignmentError, "EvidenceSetSnapshot",
           "E19 gateway 返回非快照对象 → 拒绝")
    raises(lambda: A.snapshot_from_gateway(good, company_id="888888",
                                           document_id=layout.document_id,
                                           document_version=layout.document_version),
           A.AlignmentError, "company_id",
           "E20 gateway 返回的快照身份与请求不符 → 拒绝")


# ---------------------------------------------------------------------------
# F. §八 正式集合入口：提交成员与权威快照精确相等（292 块压测）
# ---------------------------------------------------------------------------

#: 与真实文档同量级的合成集合规模（真实 300750 文档为 769 块；此处 292 块足以
#: 覆盖"少一个 / 多一个 / 换一个"三类反例，且不依赖任何真实产物）。
_SET_SIZE = 292
_SET_PAGES = 5


def _big_layout():
    pages = [[TA._line(0, TA._HEADER, y=72.0, furniture="header")]
             + TA._body_lines(1, 8) for _ in range(_SET_PAGES)]
    return TA._layout(pages, document_id="TS3_ALIGN_BIGSET")


def _big_blocks(layout):
    """292 个身份完整、可定位的块：`(页, 块)` 唯一、文本取自本页真实正文行。"""
    blocks = []
    for n in range(_SET_SIZE):
        page = 1 + (n % _SET_PAGES)
        index = n // _SET_PAGES
        blocks.append(TA._block(TA._BODY[n % len(TA._BODY)], page=page, index=index,
                                layout=layout))
    return blocks


def _test_evidence_set_exact_membership() -> None:
    layout = _big_layout()
    blocks = _big_blocks(layout)
    keys = [(b.page_number, b.block_index) for b in blocks]
    check(len(set(keys)) == _SET_SIZE and len(blocks) == _SET_SIZE,
          f"F0 夹具为 {_SET_SIZE} 个 (页, 块) 互不重复的块")
    members = tuple(sorted((_member_of(b) for b in blocks), key=lambda m: m.sort_key))
    snapshot = A.EvidenceSetSnapshot(
        company_id=layout.company_id, document_id=layout.document_id,
        document_version=layout.document_version,
        evidence_set_version=_EVIDENCE_SET_VERSION, status="current",
        members=members, block_count=len(members),
        fingerprint=A.snapshot_fingerprint(
            company_id=layout.company_id, document_id=layout.document_id,
            document_version=layout.document_version,
            evidence_set_version=_EVIDENCE_SET_VERSION, status="current",
            gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION, members=members),
        gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION)

    result = A.align_evidence_set(layout, blocks, snapshot=snapshot)
    report = A.alignment_terminal_report(result.blocks)
    check(report["total_blocks"] == _SET_SIZE
          and report["terminals_missing"] == 0
          and report["terminals_emitted"] == _SET_SIZE,
          f"F1 完整集合通过且终态守恒：{_SET_SIZE} = {_SET_SIZE}，无缺终态")
    check(len(A.alignment_terminals(result.blocks)) == _SET_SIZE,
          "F2 终态序列逐块一一对应（缺终态会原样暴露为 None，不会被过滤掩盖）")
    check(result.summary["records_emitted"] + result.summary["records_refused"]
          == result.summary["total_blocks"] == _SET_SIZE,
          "F3 三态汇总同样守恒（记录 + 拒绝 = 总数）")
    check(result.snapshot_identity["fingerprint"] == snapshot.fingerprint
          and result.snapshot_identity["block_count"] == _SET_SIZE,
          "F4 集合级结果携带权威快照身份（旧自报输入与新权威输入可分辨）")
    check([(b.page_number, b.block_index) for b in result.blocks] == sorted(keys),
          "F5 结果按 (页, 块) 升序，与提交顺序无关")

    # ---- 反例 1：只提交 1 块 ----
    raises(lambda: A.align_evidence_set(layout, blocks[:1], snapshot=snapshot),
           A.AlignmentError, "不相等",
           "F6 只提交 1 块（292 块集合的一部分）→ 拒绝：正式入口要求整份证据集")

    # ---- 反例 2：增加一个不在快照的"数学自洽"伪块 ----
    phantom = TA._block(TA._BODY[0], page=1, index=999, layout=layout)
    check(phantom.evidence_block_id != blocks[0].evidence_block_id,
          "F7 伪块自身身份完整（id / hash 均按正式接口重算，不是随便编的字符串）")
    raises(lambda: A.align_evidence_set(layout, blocks + [phantom], snapshot=snapshot),
           A.AlignmentError, "不相等", "F8 多提交一个快照之外的伪块 → 拒绝")

    # ---- 反例 3：用伪块**替换**中间一个成员（总数不变，只有身份不同）----
    swapped = blocks[:]
    swapped[len(swapped) // 2] = phantom
    raises(lambda: A.align_evidence_set(layout, swapped, snapshot=snapshot),
           A.AlignmentError, "", "F9 同数量替换中间成员为自洽伪块 → 逐字段比对拒绝")

    # ---- 反例 4：删除中间一个块 ----
    removed = blocks[:]
    del removed[len(removed) // 2]
    raises(lambda: A.align_evidence_set(layout, removed, snapshot=snapshot),
           A.AlignmentError, "不相等", "F10 删除中间一个块 → 拒绝（不得只对齐一部分）")

    # ---- 反例 5：篡改 evidence_type（仍在封闭集合内，身份自洽）----
    import evidence.ids as EIDS

    tampered_type = TA._block(TA._BODY[0], page=blocks[0].page_number,
                              index=blocks[0].block_index, layout=layout)
    hash_v = EIDS.content_hash(tampered_type.text, None)
    forged = A.EvidenceBlockInput(
        company_id=tampered_type.company_id, document_id=tampered_type.document_id,
        document_version=tampered_type.document_version,
        evidence_set_version=_EVIDENCE_SET_VERSION,
        evidence_block_id=EIDS.make_evidence_id(
            tampered_type.company_id, tampered_type.document_id,
            tampered_type.document_version, _EVIDENCE_SET_VERSION,
            tampered_type.page_number, tampered_type.block_index, hash_v),
        content_hash=hash_v, evidence_type="table",
        page_number=tampered_type.page_number,
        block_index=tampered_type.block_index, text=tampered_type.text)
    check(forged.evidence_type == "table" and forged.text == blocks[0].text,
          "F11 对照：被篡改类型的块自身身份完整（只有 evidence_type 与权威不同）")
    raises(lambda: A.align_evidence_set(layout, [forged] + blocks[1:],
                                        snapshot=snapshot),
           A.AlignmentError, "evidence_type",
           "F12 篡改 evidence_type（封闭集合内）→ 拒绝：以快照权威记录为准")

    # ---- 反例 6：快照 / 集合 / current / 版本错配 ----
    def with_snapshot(**over):
        base = dict(company_id=layout.company_id, document_id=layout.document_id,
                    document_version=layout.document_version,
                    evidence_set_version=_EVIDENCE_SET_VERSION, status="current",
                    members=members, block_count=len(members),
                    gateway_version=V.EVIDENCE_SET_GATEWAY_VERSION)
        base.update(over)
        # 指纹按改后的身份重算：被拒绝的原因必须是"快照与版式不是同一份"，
        # 而不是自报指纹失配。
        base.setdefault("fingerprint", A.snapshot_fingerprint(
            company_id=base["company_id"], document_id=base["document_id"],
            document_version=base["document_version"],
            evidence_set_version=base["evidence_set_version"], status=base["status"],
            gateway_version=base["gateway_version"], members=base["members"]))
        return A.EvidenceSetSnapshot(**base)

    raises(lambda: A.align_evidence_set(
        layout, blocks, snapshot=with_snapshot(status="retired")),
        A.AlignmentError, "current", "F13 非 current 快照 → 拒绝")
    raises(lambda: A.align_evidence_set(
        layout, blocks, snapshot=with_snapshot(document_id="OTHER_DOC")),
        A.AlignmentError, "document_id",
        "F14 快照与 PageLayout 不是同一份文档 → 拒绝")
    raises(lambda: A.align_evidence_set(
        layout, blocks, snapshot=with_snapshot(company_id="888888")),
        A.AlignmentError, "company_id",
        "F15 同文档不同公司 → 拒绝（只核文档号会放过这种错配）")
    other_set_blocks = [TA._block(b.text, page=b.page_number, index=b.block_index,
                                  layout=layout, evidence_set_version="evs-other")
                        for b in blocks]
    raises(lambda: A.align_evidence_set(
        layout, other_set_blocks, snapshot=snapshot),
        A.AlignmentError, "evidence_set_version",
        "F16 提交块属于另一个 evidence set（身份自洽）→ 拒绝")

    # ---- 反例 7：身份缺项不得被调用方参数补齐 ----
    import evaluation.run_tree_layout_acceptance as RA

    row = {"company_id": "", "document_id": layout.document_id,
           "document_version": layout.document_version}
    raises(lambda: RA.authoritative_identity(row, "company_id", "888888"),
           SystemExit, "不得由调用方参数补齐",
           "F17 权威行身份字段为空 → fail-closed，不得用调用方参数补齐")
    raises(lambda: RA.authoritative_identity(
        {"company_id": "999999", "document_id": layout.document_id,
         "document_version": layout.document_version},
        "company_id", "888888"),
        SystemExit, "不一致",
        "F18 权威行身份与查询键不符 → fail-closed（不得拿另一份文档的行冒充）")
    check(RA.authoritative_identity({"company_id": layout.company_id}, "company_id",
                                    layout.company_id) == layout.company_id,
          "F19 对照：权威行身份合规时原样返回（上面的失败不是「总是失败」）")
    # 源码级复核（**先去掉 docstring**，因为旧写法的写法本身写在说明里）：三个身份
    # 字段必须**只**来自 `authoritative_identity(...)`，且不存在 `row.get(x) or caller`
    # 这类补齐写法。
    body = _code_without_docstrings(RA.evidence_block_input)
    check(body.count("authoritative_identity(") == 3
          and "row.get(name) or" not in body
          and 'row.get("company_id") or' not in body
          and 'row.get("document_id") or' not in body
          and 'row.get("document_version") or' not in body,
          "F20 验收侧三个身份字段全部经 authoritative_identity，且不再有 "
          "`row.get(...) or caller_value` 补齐写法")


def _test_public_api_exports() -> None:
    """§九：已批准的公共类型 / 集合入口必须在**包根**可用（不是只有子模块里才有）。

    只在 `document_structure.aligner` 里可用等于"公共 API 未定稿"：调用方会各自
    从子模块直接取，正式入口 `align_evidence_set` 也就不会被当成唯一集合入口。
    """
    import document_structure as DS

    exported = ["TextAlignmentRecord", "AlignmentRefusalRecord",
                "EvidenceSetSnapshot", "EvidenceSetMember", "EvidenceSetGateway",
                "align_evidence_set", "assert_members_exact",
                "snapshot_fingerprint", "snapshot_from_gateway",
                "validate_alignment_partition", "compute_alignment_verdict",
                "ALIGNMENT_VERDICTS", "ALIGNMENT_REFUSAL_REASONS",
                "EVIDENCE_TYPE_CLOSED_SET", "EVIDENCE_SET_STATUSES"]
    missing = [name for name in exported if not hasattr(DS, name)]
    check(not missing,
          f"G1 公共类型 / 集合入口在包根可用（缺 {missing}）")
    not_in_all = [name for name in exported if name not in DS.__all__]
    check(not not_in_all,
          f"G2 这些名字进入 `__all__`（缺 {not_in_all}）")
    check(DS.align_evidence_set is A.align_evidence_set,
          "G3 包根导出的是**同一个**集合入口对象（不是包装 / 复制）")
    check(isinstance(DS.EVIDENCE_SET_STATUSES, (tuple, list))
          and tuple(DS.EVIDENCE_SET_STATUSES)
          == ("building", "current", "retired", "invalid")
          and set(DS.EVIDENCE_TYPE_CLOSED_SET)
          == {"heading", "paragraph", "table", "table_row"},
          f"G4 证据类型 / 状态是包根可见的封闭集合（实际 "
          f"{DS.EVIDENCE_SET_STATUSES!r} / {sorted(DS.EVIDENCE_TYPE_CLOSED_SET)}）")


def main() -> dict:
    groups = (
        _test_single_partition_authority,
        _test_partition_counterexamples,
        _test_text_alignment_record_partition,
        _test_refusal_record_partition,
        _test_snapshot_identity,
        _test_evidence_set_exact_membership,
        _test_public_api_exports,
    )
    for fn in groups:
        try:
            fn()
        except Exception as error:  # noqa: BLE001
            _results["failed"] += 1
            _results["details"].append(
                f"FAIL {fn.__name__} 崩溃（该组后续反例未执行）："
                f"{type(error).__name__}: {error}")
    return _results


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=1))
    raise SystemExit(0 if _results["failed"] == 0 else 1)

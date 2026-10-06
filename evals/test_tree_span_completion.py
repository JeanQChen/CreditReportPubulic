# -*- coding: utf-8 -*-
"""TS4-A 完成判据验收：T39（`set_complete` 的唯一完成判据 + A/B 单点真值表）。

被测对象是 §18.8.2 / §18.10 的**唯一**材料级完成入口
`span_verifier.is_completion_eligible(verified, span_id)`。本模块锁定：

1. **三要素**：完成资格只能问"已复核能力 + 冻结策略 + 该 span 的可引用覆盖"——
   - 不是 `VerifiedSpanSnapshot` 的输入直接拒绝（`CapabilityError`）；
   - 策略必须是固定目录里注册表钉住的那一份（自造策略拿不到 authority 指纹）；
   - 该 span 的 coverage 必须存在且自洽；
2. **TS4-A 恒假**：`SPAN_CONFIDENCE_MIN is None` ⇒ 任何 span 恒为 `False`；
3. **缺口不得放行**：正文有效可引用区间在**头部 / 中部**有缺口的 span，即使已有
   已准入（`admitted=True`）的 Evidence，也**不得**被判为可完成；
4. **旧入口不在链上**：`OutlineSpan.can_support_set_complete()` 是 TS3 遗留判据，
   正式链**不得**调用它（源码交叉检查 + 运行时 spy 双向证明），且策略记录里
   `set_complete_supported is False`；
5. **A/B 真值表单点**：`stage` / `completion_enabled` / `set_complete_supported`
   只由 `versions.SPAN_CONFIDENCE_MIN` 与策略记录派生——在受控 `try/finally` 里
   临时把它改成已裁决值，真值表必须**整体翻转**（证明没有被写死在别处）；
6. **历史 A 产物不因进入 B 环境而解禁**：A 快照在未来 B 环境里仍可复核，但完成
   结论仍为 `False`；把 A 产物挂上 B 策略记录则必须**拒绝放行**（返回 `False`），
   绝不"顺手升级"。
7. **`threshold_enabled`（TS4-B）分支的完整真值表**：在**受控模拟的 B 环境**里
   （临时策略目录 + 临时 `versions.SPAN_CONFIDENCE_MIN`，二者都在 `finally` 里逐字
   还原）造出一份真实的 `threshold_enabled` 快照，逐条检验 12 个条件：合法冻结策略
   + 达阈值 + 全覆盖 ⇒ `True`；未达阈值 / 头部·中部·尾部缺口 / 存在不可引用区间 /
   存在未覆盖区间 / normalization-only 不得掩盖缺口 / 自报派生值不可信 /
   fallback·跨标题·未归属 / 未知 `span_id` / coverage 重复或缺失 / 历史 A 快照 ——
   一律 `False`。该分支**只**新增"资产身份 → 判定"的读取逻辑：TS4-B 只需冻结策略
   资产并重跑，**不**新增完成判据的核心业务逻辑。

全部用例为"构造 + 断言"：不连网络、不写数据库、不依赖执行顺序。本模块**不**创建
任何正式 B 资产（`span_qualification_approval_v1.json` /
`span_qualification_frozen_v1.json` / `review_attestation.json` 一律不出现），
也不改写正式 `document_structure/policies/` 目录。
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from document_structure import span_builder as SB  # noqa: E402
from document_structure import span_policy as SP  # noqa: E402
from document_structure import span_schema as SS  # noqa: E402
from document_structure import span_verifier as SV  # noqa: E402
from document_structure import versions as V  # noqa: E402
from document_structure.canonical import SchemaValidationError  # noqa: E402
from document_structure.evidence_gateway import (  # noqa: E402
    fixture_root_dir,
    load_fixture_root,
)
from document_structure.schema import (  # noqa: E402
    OutlineSpan,
    PageLayout,
)

_results: dict = {"passed": 0, "failed": 0, "skipped": 0, "details": []}

#: A/B 真值表必须**单点**取决于它；本模块只在受控 try/finally 里临时改写。
_POLICY_STAGE_FIELDS = ("stage", "completion_enabled", "set_complete_supported")


def check(cond, msg):
    if cond:
        _results["passed"] += 1
        _results["details"].append("PASS " + msg)
    else:
        _results["failed"] += 1
        _results["details"].append("FAIL " + msg)
    return bool(cond)


def skip(msg):
    _results["skipped"] += 1
    _results["details"].append("SKIP " + msg)


def raises(fn, exc, substr, msg):
    """断言 `fn()` 抛出 `exc` 且信息含 `substr`。"""
    try:
        fn()
    except exc as error:
        text = str(error)
        if substr in text:
            _results["passed"] += 1
            _results["details"].append("PASS " + msg)
            return True
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 异常信息不含 {substr!r}：{text!r}")
        return False
    except Exception as error:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 抛出 {type(error).__name__} 而非 {exc.__name__}：{error}")
        return False
    _results["failed"] += 1
    _results["details"].append(f"FAIL {msg} —— 未抛出 {exc.__name__}")
    return False


def raises_any(fn, exc, msg):
    """断言 `fn()` 抛出 `exc`（不约束信息文案），并回传信息用于诊断。"""
    try:
        fn()
    except exc as error:
        _results["passed"] += 1
        _results["details"].append(f"PASS {msg}（{type(error).__name__}：{error}）")
        return str(error)
    except Exception as error:  # noqa: BLE001
        _results["failed"] += 1
        _results["details"].append(
            f"FAIL {msg} —— 抛出 {type(error).__name__} 而非 {exc.__name__}：{error}")
        return None
    _results["failed"] += 1
    _results["details"].append(f"FAIL {msg} —— 未抛出 {exc.__name__}")
    return None


# ---------------------------------------------------------------------------
# 0. 真实基线
# ---------------------------------------------------------------------------

def _fixture_handoff() -> SB.VerifiedTS3Handoff:
    fixture_root = load_fixture_root()
    root_dir = fixture_root_dir()
    expected_layout = PageLayout.from_dict(
        json.loads((root_dir / "page_layout.json").read_text(encoding="utf-8")))
    return SB._issue_fixture_ts3_handoff(
        raw_pdf=(REPO / fixture_root["source_pdf"]["relpath"]).read_bytes(),
        expected_layout=expected_layout,
        company_id=fixture_root["company_id"],
        document_id=fixture_root["document_id"], fixture_root=fixture_root)


def _resnapshot(snap, **overrides):
    kwargs = dict(
        qualification_policy=snap.qualification_policy,
        document_id=snap.document_id, document_version=snap.document_version,
        page_layout_id=snap.page_layout_id, outline_id=snap.outline_id,
        alignment_schema_version=snap.alignment_schema_version,
        alignment_id=snap.alignment_id,
        structure_snapshot_id=snap.structure_snapshot_id,
        trusted_input=snap.trusted_input, dispositions=snap.dispositions,
        spans=snap.spans,
        inherited_unassigned_span_ids=snap.inherited_unassigned_span_ids,
        components=snap.components, coverages=snap.coverages,
        conservation=snap.conservation, synopses=snap.synopses,
        terminal_count=snap.terminal_count)
    kwargs.update(overrides)
    return SS.SpanBuildSnapshot.create(**kwargs)


def _gap_kind(coverage) -> str | None:
    """正文有效可引用区间的缺口位置：`head` / `middle` / `tail` / `empty` / `None`。"""
    effective = coverage.effective_citable_intervals
    if not effective:
        return "empty"
    if effective[0].start > 0:
        return "head"
    if len(effective) > 1:
        return "middle"
    if effective[-1].end < coverage.span_local_length:
        return "tail"
    return None


def _admitted_component_count(snap, span_id) -> int:
    return sum(1 for c in snap.components if c.span_id == span_id and c.admitted)


def _homemade_a_policy(policy) -> SS.SpanQualificationPolicy:
    """一份**调用者自造**的 A 口径策略：除了一个简介常量外与冻结策略完全相同。"""
    return SS.SpanQualificationPolicy.create(
        policy_key=policy.policy_key, stage="distribution_only",
        span_confidence_min=None, factor_entries=policy.factor_entries,
        completion_enabled=False, set_complete_supported=False,
        max_snippets_per_node=policy.max_snippets_per_node,
        max_snippet_chars=policy.max_snippet_chars + 1,
        min_snippet_chars=policy.min_snippet_chars,
        max_total_snippet_chars=policy.max_total_snippet_chars,
        sentence_terminators=tuple(policy.sentence_terminators),
        closing_quotes=tuple(policy.closing_quotes))


def _b_policy(policy, *, threshold: float = 0.5,
              policy_key: str | None = None) -> SS.SpanQualificationPolicy:
    """模拟的 B 策略记录。

    `SpanQualificationPolicy.__post_init__` 要求 `span_confidence_min` 必须等于
    `versions.SPAN_CONFIDENCE_MIN`，因此本构造函数**只能**在受控把该常量临时改成
    已裁决值的 try/finally 块内调用——这本身就是"阶段由单点派生"的一条证据。
    """
    return SS.SpanQualificationPolicy.create(
        policy_key=(policy.policy_key if policy_key is None else policy_key),
        stage="threshold_enabled",
        span_confidence_min=threshold, factor_entries=policy.factor_entries,
        completion_enabled=True, set_complete_supported=True,
        max_snippets_per_node=policy.max_snippets_per_node,
        max_snippet_chars=policy.max_snippet_chars,
        min_snippet_chars=policy.min_snippet_chars,
        max_total_snippet_chars=policy.max_total_snippet_chars,
        sentence_terminators=tuple(policy.sentence_terminators),
        closing_quotes=tuple(policy.closing_quotes))


def _clone(obj):
    """浅拷贝一个 frozen dataclass 实例，**不**跑 `__post_init__`。

    只用于负例：这些反例要检验"判据对**敌意成员**是否 fail-closed"，而正式构造函数
    会先把这些非法成员挡在门外（那是另一道门，不构成本判据的证据）。因此这里显式
    旁路构造，绝不用于任何正向产物。
    """
    clone = object.__new__(type(obj))
    clone.__dict__.update(obj.__dict__)
    return clone


def _policy_asset_bytes(directory, name):
    """**只读**地取一份策略资产字节；不存在返回 `None`。

    只用于"跑测试前后字节是否一致"的身份比对：不创建、不写入、不补默认值，
    因此它不可能把"资产本来就不存在"伪装成"资产没变"。
    """
    path = directory / name
    return path.read_bytes() if path.is_file() else None


def _spy_legacy(calls: list):
    """把 `OutlineSpan.can_support_set_complete` 换成记录型 spy，返回还原函数。"""
    original = OutlineSpan.can_support_set_complete

    def _recording(self, **kwargs):
        calls.append(self.span_id)
        return original(self, **kwargs)

    OutlineSpan.can_support_set_complete = _recording

    def _restore():
        OutlineSpan.can_support_set_complete = original

    return _restore


class _SimulatedAEnvironment:
    """**受控模拟**的 TS4-A 环境：只把"当前阶段"临时拨回 `distribution_only`。

    它只临时改写一个模块级读取点（`versions.SPAN_CONFIDENCE_MIN`）并在 `__exit__`
    里逐字还原——正式 `policies/` 目录、A 记录与注册表**一个字节都不改**。当本轮
    本来就处于 A 阶段时，它是**空操作**，因此本模块在 A / B 两个阶段下跑同一套断言。

    需要它的原因：A 侧基线必须由**正式签发路径**产出一份真正的 A 快照（含 A 版
    handoff），而那条路径的入口门（`span_policy.assert_current_policy`）按设计只认
    **当前**阶段——进入 B 之后仍要造 A 产物，只能显式把阶段拨回去，而不是绕过门。
    """

    def __enter__(self) -> "_SimulatedAEnvironment":
        self._original = V.SPAN_CONFIDENCE_MIN
        V.SPAN_CONFIDENCE_MIN = None
        self.table = SP.ab_gate_truth_table()
        return self

    def __exit__(self, *_exc) -> bool:
        V.SPAN_CONFIDENCE_MIN = self._original
        check(V.SPAN_CONFIDENCE_MIN == self._original,
              f"T39 模拟 A 环境必须逐字还原 SPAN_CONFIDENCE_MIN，得到 "
              f"{V.SPAN_CONFIDENCE_MIN!r}")
        return False


class _SimulatedBEnvironment:
    """**受控模拟**的 TS4-B 环境：临时策略目录 + 临时阈值常量 + 与之**自洽**的测试资产。

    它只临时改写两个**模块级**读取点（`span_policy.POLICY_DIR` 与
    `versions.SPAN_CONFIDENCE_MIN`），并在 `__exit__` 里逐字还原。沙箱里放的是一整套
    **自洽**的测试 B 资产：正式注册表与分布策略的字节副本（因此历史 A 快照的 authority
    指纹在沙箱里仍逐字复现），加上"只改 threshold、其余逐字保留"的 approval 记录、
    由**正式派生函数** `span_policy.derived_frozen_policy` 从它派生出的 frozen 策略，
    以及钉住该指纹的注册表条目。

    为什么必须自洽：`qualification_binding_slots` 对 `threshold_enabled` 策略要求四个
    绑定槽非 `None` 且与 approval 记录一致，因此"随手造一份阈值策略"根本建不出快照——
    这正是 B 阶段的 fail-closed 设计。沙箱的做法是让**同一套正式派生与绑定代码**产出这
    份测试资产，而不是绕过它；正式 `policies/` 目录（含已由 runner 从 sealed attestation
    导出的 approval / frozen）**只读比对身份，一个字节都不改**，测试也从不伪造正式资产。
    """

    def __init__(self, threshold: float) -> None:
        self.threshold = float(threshold)
        self._tmp = None
        self.sandbox = None
        self.policy = None
        self.approval = None

    def __enter__(self) -> "_SimulatedBEnvironment":
        self._original_dir = SP.POLICY_DIR
        self._original_min = V.SPAN_CONFIDENCE_MIN
        check(self._original_min is not None,
              "T39-B 模拟 B 环境需要正式 TS4-B 资产作为派生基，"
              f"当前 SPAN_CONFIDENCE_MIN 为 {self._original_min!r}（尚未进入 B）")
        # 在切换目录**之前**读正式 approval 记录：它是沙箱那份的派生基。
        base_approval = SP.load_approval_record()
        self._tmp = tempfile.TemporaryDirectory(prefix="ts4b-policy-")
        self.sandbox = pathlib.Path(self._tmp.name)
        # 先改两个读取点，再派生（派生与 `__post_init__` 都要求与全局常量一致）。
        SP.POLICY_DIR = self.sandbox
        V.SPAN_CONFIDENCE_MIN = self.threshold
        for name in (SP.REGISTRY_FILENAME, SP.DISTRIBUTION_POLICY_FILENAME):
            (self.sandbox / name).write_bytes(
                (self._original_dir / name).read_bytes())

        record = dict(base_approval)
        record["threshold"] = self.threshold
        derived = SP.derived_frozen_policy(record)
        record["frozen_policy_fingerprint"] = derived.policy_fingerprint
        record["approval_authority_fingerprint"] = \
            SP.approval_authority_fingerprint(record)
        check(set(record) == set(SP.APPROVAL_RECORD_KEYS),
              "T39-B 沙箱 approval 记录的键集不得偏离正式键集")
        (self.sandbox / SP.APPROVAL_RECORD_FILENAME).write_bytes(
            SP.serialize_policy_asset(record))
        (self.sandbox / SP.FROZEN_RECORD_FILENAME).write_bytes(
            SP.serialize_policy_asset(derived.to_dict()))

        registry = json.loads(
            (self.sandbox / SP.REGISTRY_FILENAME).read_text(encoding="utf-8"))
        entry = registry["policies"].get(SP.FROZEN_POLICY_KEY)
        check(isinstance(entry, dict)
              and entry.get("file") == SP.FROZEN_RECORD_FILENAME,
              "T39-B 沙箱注册表必须原本就登记 frozen 条目"
              "（未登记说明正式 B 资产不完整，沙箱无从派生）")
        registry["policies"][SP.FROZEN_POLICY_KEY] = {
            "file": SP.FROZEN_RECORD_FILENAME,
            "policy_fingerprint": derived.policy_fingerprint}
        (self.sandbox / SP.REGISTRY_FILENAME).write_text(
            json.dumps(registry, ensure_ascii=False, sort_keys=True),
            encoding="utf-8")

        # 用**正式入口**在沙箱里解析这一份测试 frozen policy（写得出 must 读得回）。
        self.policy = SP.resolve_frozen_policy()
        self.approval = SP.load_approval_record()
        check(self.policy.policy_key == SP.FROZEN_POLICY_KEY
              and self.policy.stage == "threshold_enabled"
              and self.policy.span_confidence_min == self.threshold,
              f"T39-B 沙箱 frozen 策略必须是以模拟阈值启用的 threshold 策略，得到 "
              f"{self.policy.policy_key}/{self.policy.stage}/"
              f"{self.policy.span_confidence_min}")
        check(self.policy.policy_fingerprint
              == self.approval["frozen_policy_fingerprint"],
              "T39-B 沙箱 approval 与 frozen 必须互相绑定同一指纹")
        return self

    def __exit__(self, *_exc) -> bool:
        SP.POLICY_DIR = self._original_dir
        V.SPAN_CONFIDENCE_MIN = self._original_min
        self._tmp.cleanup()
        check(SP.POLICY_DIR == self._original_dir
              and V.SPAN_CONFIDENCE_MIN == self._original_min,
              "T39-B 模拟环境退出必须逐字还原策略目录与全局常量")
        return False


def _build_b_verified(handoff, env):
    """在**已生效**的模拟 B 环境里造出一份真实 `threshold_enabled` 快照并复核它。

    走**复核路径**（`_build_span_input(policy=…)`）而不是新建路径：新建路径的 A/B
    阶段门属于本批次的授权范围，不在这里绕过；复核路径按设计"只把快照自载的策略对回
    固定目录钉住的指纹，不看当前阶段"，正是 TS4-B 复核一份 B 产物时走的同一条代码。
    """
    authority = SP.policy_provider_authority_fingerprint(env.policy)
    inp = SB._build_span_input(handoff, policy=env.policy,
                               expected_policy_authority_fingerprint=authority)
    snapshot = SB._build_snapshot(inp)
    verified = SV.verify_span_snapshot(snapshot, handoff)
    return snapshot, verified


def _coverage_cases(target) -> dict:
    """在一条真实 coverage 的正文域上构造完成判据的**逐条覆盖反例**（含正面对照）。

    每条用例都保留原有的已准入 Evidence 引用：于是"即使有已准入 Evidence 也不放行"
    是在**同一次比较**里得出的，而不是把证据一并删掉换来的。返回
    `{用例名: (coverage, 期望的完成资格)}`。

    关键口径（§18.8.3）：**缺口**指"必需内容没被可引用区间覆盖"，
    而**不是**指"有效区间没铺到域尾"——末尾的归一化空白被透明跳过时，必需内容依然
    100% 被覆盖，那是**合法**的完整覆盖，必须放行（见 `normalization_bridges`）。
    """
    length = target.span_local_length
    head = min(3, max(1, length // 4))
    mid = length // 2
    tail = max(length - 1, mid + 1)
    common = dict(span_id=target.span_id, span_local_length=length,
                  covering_component_ids=target.covering_component_ids)

    def _make(*, norm=(), citable=(), non_citable=(), uncovered=()):
        return SS.SpanCitableCoverage.create(
            normalization_only_intervals=list(norm),
            citable_source_intervals=list(citable),
            non_citable_source_intervals=list(non_citable),
            uncovered_source_intervals=list(uncovered), **common)

    cases = {
        # 头部必需内容落在不可引用区间 ⇒ 未达覆盖
        "head_gap": (_make(citable=[(head, length)], non_citable=[(0, head)]), False),
        # 中部必需内容落在不可引用区间
        "middle_gap": (_make(citable=[(0, mid), (mid + 1, length)],
                             non_citable=[(mid, mid + 1)]), False),
        # 尾部必需内容落在不可引用区间（"有效区间没铺到域尾"本身不是缺口，
        # 这里的缺口来自 required 里那一段真的不可引用）
        "tail_gap": (_make(citable=[(0, tail)], non_citable=[(tail, length)]), False),
        # 未被覆盖 ≠ 不可引用：两条独立的负条件必须各自成立
        "uncovered_gap": (_make(citable=[(0, mid), (mid + 1, length)],
                                uncovered=[(mid, mid + 1)]), False),
        # **正面对照**：归一化空白被透明跳过，必需内容仍 100% 可引用 ⇒ 必须放行
        "normalization_bridges": (_make(norm=[(mid, mid + 1)],
                                        citable=[(0, mid), (mid + 1, length)]), True),
        # normalization-only 不得掩盖缺口：紧邻归一化区间的必需内容仍被 uncovered 挡住
        "normalization_masks_gap": (_make(
            norm=[(mid, mid + 1)], citable=[(0, mid)],
            uncovered=[(mid + 1, length)]), False),
    }
    return cases


# ---------------------------------------------------------------------------
# 1. 三要素：能力 / 冻结策略 / coverage
# ---------------------------------------------------------------------------

def _preconditions(handoff, snap, verified, pinned_a):
    """完成判据的三个前置必须都真实存在，否则后面的断言都是空的。

    `pinned_a` 是**在受控模拟 A 环境里**由正式固定目录解析出的 A 记录（注册表钉住、
    因子表对回 §18.8.5）。本函数在真实当前阶段下运行，因此 A 侧的"记录本身合法"
    一律对这份记录断言，而"当前口径是不是 A"由阶段门单独判定（见 `_ab_single_source`）。
    """
    check(bool(snap.spans), "T39 基线必须存在 span")
    check(len(snap.coverages) == len(snap.spans),
          "T39 每个 span 必须恰好有一条 coverage")
    required = [c for c in snap.coverages if c.required_content_intervals]
    check(len(required) == len(snap.coverages),
          "T39 每条 coverage 的 required_content_intervals 都必须非空")

    # 不是已复核能力：直接拒绝（"字段长得一样"不构成资格）。
    span_id = snap.spans[0].span_id
    raises(lambda: SV.is_completion_eligible(snap, span_id),
           SS.CapabilityError, "不是本进程由正式签发路径产生",
           "T39 原始快照（未复核）不得用于完成资格判定")
    raises(lambda: SV.is_completion_eligible(object.__new__(SV.VerifiedSpanSnapshot),
                                             span_id),
           SS.CapabilityError, "不是本进程由正式签发路径产生",
           "T39 仿造能力对象不得用于完成资格判定")
    raises(lambda: SV.is_completion_eligible(verified, ""),
           SV.SpanVerificationError, "span_id 必须为非空字符串",
           "T39 空 span_id 必须被拒绝（不得默认放行）")
    raises(lambda: SV.is_completion_eligible(verified, None),
           SV.SpanVerificationError, "span_id 必须为非空字符串",
           "T39 非字符串 span_id 必须被拒绝")

    # 冻结策略：注册表钉住的那一份，指纹由固定目录字节重算。
    policy = snap.qualification_policy
    pinned = pinned_a
    check(pinned.policy_id == policy.policy_id,
          "T39 快照内嵌策略必须就是固定目录里注册表钉住的那一份")
    check(SP.registry_document()["policies"][SP.DEFAULT_POLICY_KEY]
          ["policy_fingerprint"] == pinned.policy_fingerprint,
          "T39 A 策略指纹必须与注册表钉住值一致（历史 A 条目不得被改动）")
    check(SP.policy_provider_authority_fingerprint(policy)
          == handoff.policy_provider_authority_fingerprint,
          "T39 策略 provider authority 指纹必须由固定目录重算并复现")
    check(policy.policy_key == SP.DEFAULT_POLICY_KEY
          and policy.stage == "distribution_only"
          and policy.span_confidence_min is None
          and policy.completion_enabled is False
          and policy.set_complete_supported is False,
          "T39 TS4-A 策略记录必须是 distribution_only / 无阈值 / 不开完成与 set_complete")
    check(SS.SpanQualificationPolicy.from_dict(policy.to_dict()) == policy,
          "T39 A 策略记录必须能自描述地读回（历史记录的读回不依赖当前全局常量）")

    # 自造策略拿不到 authority 指纹（"换一个策略对象"不构成放行路径）。
    homemade = _homemade_a_policy(policy)
    check(homemade.policy_fingerprint != policy.policy_fingerprint,
          "T39 自造策略必须与原策略指纹不同（否则这条反例不成立）")
    check(homemade.stage == "distribution_only"
          and homemade.span_confidence_min is None,
          "T39 自造策略本身仍是合法的 A 口径记录（只差一个常量）")
    raises(lambda: SP.policy_provider_authority_fingerprint(homemade),
           SP.PolicyResolutionError, "指纹与注册表钉住值不一致",
           "T39 自造策略记录不得取得 provider authority 指纹（fail-closed）")
    check(SP.registry_document()["policies"][SP.DEFAULT_POLICY_KEY]
          ["policy_fingerprint"] == policy.policy_fingerprint,
          "T39 自造策略不得影响固定目录里被钉住的那一份")
    # 当前阶段门：A 阶段必须放行 A 记录；已进入 B 之后必须**拒绝**把历史 A 记录当作
    # 当前口径（两条分支都必须有断言，不能因为阶段不同就什么都不查）。
    if SP.ab_gate_truth_table()["stage"] == "distribution_only":
        check(SP.assert_distribution_only(policy) is None,
              "T39 A 阶段策略必须通过 distribution-only 前置门")
    else:
        raises(lambda: SP.assert_distribution_only(policy),
               SP.PolicyResolutionError, "已进入 threshold_enabled 阶段",
               "T39 进入 B 阶段后历史 A 记录不得再充当当前正式口径")


# ---------------------------------------------------------------------------
# 2. TS4-A 恒假
# ---------------------------------------------------------------------------

def _always_false(snap, verified, a_table):
    """TS4-A 记录恒假 + 当前阶段门的**两条分支都必须有断言**。

    `a_table` 是在受控模拟 A 环境里取到的真值表；`snap` 是 TS4-A 记录。关键点：
    完成资格只看**快照自载的策略记录**（`SV._completion_threshold`），因此历史 A 记录
    无论当前全局常量是 `None` 还是 `0.85`，结论都必须是 `False`——阈值启用不得把
    历史 A 记录重新解释成"已完成"，也不得事后升级其阶段身份。
    """
    check(a_table["stage"] == "distribution_only"
          and a_table["threshold_decided"] is False
          and a_table["completion_enabled"] is False
          and a_table["set_complete_supported"] is False
          and a_table["threshold_required"] is False,
          "T39 TS4-A 真值表必须整行为假（取自在受控模拟 A 环境下的正式门）")

    live = dict(SP.ab_gate_truth_table())
    _results["live_stage_truth_table"] = live
    if live["stage"] == "distribution_only":
        check(V.SPAN_CONFIDENCE_MIN is None,
              f"T39 A 阶段全局常量必须为未裁决（None），得到 {V.SPAN_CONFIDENCE_MIN!r}")
        check(live == a_table,
              "T39 A 阶段当前真值表必须与模拟 A 环境取得的那一行逐字段一致")
    else:
        check(live["stage"] == "threshold_enabled"
              and live["threshold_decided"] is True
              and live["threshold_required"] is True,
              f"T39 B 阶段真值表必须显式声明阈值已裁决且为必要门，得到 {live}")
        check(V.SPAN_CONFIDENCE_MIN == 0.85,
              f"T39 B 阶段全局常量必须是已批准的 0.85，得到 {V.SPAN_CONFIDENCE_MIN!r}")
        check(live["completion_enabled"] is True
              and live["set_complete_supported"] is True,
              "T39 B 阶段真值表必须同时记录完成与 set_complete **可判定**（不等于已达成）")
        check(live != a_table,
              "T39 B 阶段真值表必须与 A 阶段不同（否则阶段身份没有真正切换）")

    for span in snap.spans:
        if SV.is_completion_eligible(verified, span.span_id) is not False:
            check(False, f"T39 TS4-A 记录的 span {span.span_id} 完成资格必须恒为 False")
            return
    check(True, f"T39 TS4-A 记录全部 {len(snap.spans)} 个 span 的完成资格在当前阶段下恒为 False")
    check(SV.is_completion_eligible(verified, "os-does-not-exist") is False,
          "T39 未知 span_id 同样恒为 False")
    check(snap.qualification_policy.stage == "distribution_only"
          and snap.qualification_policy.span_confidence_min is None
          and snap.qualification_policy.set_complete_supported is False,
          "T39 历史 A 记录自载的阶段/阈值/set_complete 三字段不得被当前全局常量改写")


# ---------------------------------------------------------------------------
# 3. 头部 / 中部缺口不得放行
# ---------------------------------------------------------------------------

def _gaps_are_not_released(handoff, snap, verified):
    """有缺口（头部 / 中部 / 尾部 / 未覆盖）且已有已准入 Evidence 的 span 仍不得放行。

    返回 `(target_span_id, {用例名: 该用例所在的快照})`，供"模拟 B 环境"用例复用：
    只有在"完成本被允许"的环境里仍然拒绝，才证明缺口真的是一道独立的门。
    """
    span_by_id = {s.span_id: s for s in snap.spans}
    real_kinds = sorted({str(_gap_kind(c)) for c in snap.coverages})
    _results["real_gap_kinds"] = real_kinds

    # 先看真实数据里存在的那一类缺口（冻结夹具若是完整覆盖，这一支如实跳过）。
    for kind in ("head", "middle", "tail"):
        items = [c for c in snap.coverages if _gap_kind(c) == kind]
        if not items:
            skip(f"T39 真实夹具没有 {kind} 缺口样本（真实缺口种类：{real_kinds}）")
            continue
        coverage = items[0]
        span = span_by_id[coverage.span_id]
        admitted = _admitted_component_count(snap, span.span_id)
        check(admitted > 0,
              f"T39 {kind} 缺口样本 {span.span_id} 必须带有已准入 Evidence"
              f"（否则“有已准入 Evidence 也不放行”无从证明），得到 {admitted}")
        check(bool(span.component_evidence_refs),
              f"T39 {kind} 缺口样本必须带有 component Evidence")
        check(SV.is_completion_eligible(verified, span.span_id) is False,
              f"T39 {kind} 缺口 span 即使有已准入 Evidence 也不得放行")
        check(coverage.effective_citable_intervals
              != coverage.required_content_intervals,
              f"T39 {kind} 缺口样本的 effective_citable_intervals 必须真的短于"
              f" required_content_intervals（否则这条反例不成立）")

    # 受控构造：拿真实夹具里最长的 span，把它的正文域分别挖出头部缺口与中部缺口。
    # 该 span 的既有已准入 Evidence 原样保留 —— 于是"有证据也不放行"是同一次比较。
    target = max(snap.coverages, key=lambda c: c.span_local_length)
    span = span_by_id[target.span_id]
    admitted = _admitted_component_count(snap, target.span_id)
    check(admitted > 0,
          f"T39 缺口反例所用 span {target.span_id} 必须带有已准入 Evidence，"
          f"得到 {admitted}")
    check(bool(span.component_evidence_refs),
          "T39 缺口反例所用 span 必须带有 component Evidence")

    # 受控构造：在**同一条** span 上分别挖出头部 / 中部 / 尾部缺口、未覆盖缺口、
    # 归一化桥接与"归一化掩盖缺口"，逐条比较。该 span 的既有已准入 Evidence 原样保留
    # —— 于是"有证据也不放行"与"有证据且真的全覆盖才放行"是同一次比较。
    cases = _coverage_cases(target)
    case_snaps: dict = {}
    original_snapshot = verified.snapshot
    try:
        for name, (coverage, expected) in cases.items():
            check(coverage.required_content_intervals,
                  f"T39 覆盖用例 {name} 的必需内容不得为空（否则断言是空的）")
            if expected is False:
                check(coverage.effective_citable_intervals
                      != coverage.required_content_intervals,
                      f"T39 覆盖用例 {name} 必须真的构成缺口")
            case_snap = _resnapshot(
                snap, coverages=tuple(coverage if c is target else c
                                      for c in snap.coverages))
            case_snaps[name] = case_snap
            object.__setattr__(verified, "_snapshot", case_snap)
            got = SV.is_completion_eligible(verified, target.span_id)
            # A 阶段恒假：这里只能证明"没有被误放行"，不能证明覆盖口径；
            # 覆盖口径的完整真值表在受控 B 环境里跑（§7）。
            check(got is False,
                  f"T39 A 阶段覆盖用例 {name} 必须恒为 False，得到 {got!r}")
    finally:
        object.__setattr__(verified, "_snapshot", original_snapshot)
    check(verified.snapshot is snap,
          "T39 受控替换必须已还原（不得影响后续用例）")
    return target.span_id, case_snaps


# ---------------------------------------------------------------------------
# 4. 旧入口不在正式链上
# ---------------------------------------------------------------------------

def _legacy_entry_is_off_chain(handoff, snap, verified):
    # 4a 源码交叉检查：正式链模块不得出现旧判据。
    for name in ("span_verifier.py", "span_policy.py", "span_builder.py"):
        text = (REPO / "document_structure" / name).read_text(encoding="utf-8")
        check("can_support_set_complete" not in text,
              f"T39 {name} 不得引用 TS3 遗留判据 can_support_set_complete")
    schema_text = (REPO / "document_structure" / "schema.py").read_text(
        encoding="utf-8")
    check("can_support_set_complete" in schema_text,
          "T39 遗留判据必须仍存在于 base wire class 上（否则源码检查是空的）")
    check("is_completion_eligible" in SV.__all__,
          "T39 is_completion_eligible 必须是复核层导出的唯一完成判据")
    check("can_support_set_complete" not in SV.__all__,
          "T39 复核层不得导出遗留判据")

    # 4b 运行时 spy：正式链的完成判定绝不能触碰旧入口。
    calls: list = []
    original = OutlineSpan.can_support_set_complete

    def _recording_spy(self, **kwargs):
        calls.append(self.span_id)
        return original(self, **kwargs)

    OutlineSpan.can_support_set_complete = _recording_spy
    try:
        for span in snap.spans:
            SV.is_completion_eligible(verified, span.span_id)
        SV.is_completion_eligible(verified, "os-unknown")
        SV.verify_span_snapshot(snap, handoff)
        # 正式链上的阶段门必须按**当前阶段**给出结论（A 放行 / B 拒绝历史 A 记录），
        # 两种分支都要真的走一次，不能因为阶段不同就把这一行空着。
        if SP.ab_gate_truth_table()["stage"] == "distribution_only":
            SP.assert_distribution_only(snap.qualification_policy)
        else:
            raises(lambda: SP.assert_distribution_only(snap.qualification_policy),
                   SP.PolicyResolutionError, "已进入 threshold_enabled 阶段",
                   "T39 B 阶段下历史 A 记录必须被 distribution-only 前置门拒绝")
        SP.ab_gate_truth_table()
    finally:
        OutlineSpan.can_support_set_complete = original
    check(calls == [],
          f"T39 正式完成链路一次也不得调用遗留判据，实际调用 {calls}")

    # 4c 把旧入口改成"一调就炸"，整条正式链仍必须走完。
    def _explode(self, **kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError(
            "正式链路不得调用 OutlineSpan.can_support_set_complete()")

    OutlineSpan.can_support_set_complete = _explode
    try:
        for span in snap.spans:
            SV.is_completion_eligible(verified, span.span_id)
        SV.verify_span_snapshot(snap, handoff)
    finally:
        OutlineSpan.can_support_set_complete = original
    check(OutlineSpan.can_support_set_complete is original,
          "T39 spy 必须已还原（不得污染其他测试）")

    # 4d 旧入口自身的口径：它直接读**当前全局常量**，而正式判据只读**快照自载策略**。
    #    这条差异是"旧入口不是正式判据"的根据，必须在两种阶段下都成立。
    span = snap.spans[0]
    if V.SPAN_CONFIDENCE_MIN is None:
        check(span.is_structurally_eligible() is False,
              "T39 未裁决阈值下 is_structurally_eligible 必须为假（否则旧入口会误判）")
        check(span.can_support_set_complete(alignment_records=(),
                                           boundary_verified=True) is False,
              "T39 A 阶段旧判据必须为假（它只是遗留读数，不是正式判据）")
    else:
        legacy_true = sorted(s.span_id for s in snap.spans
                             if s.is_structurally_eligible() is True)
        _results["legacy_entry_readings_under_b"] = {
            "global_min": V.SPAN_CONFIDENCE_MIN,
            "document_channel": "TS4-A",
            "structurally_eligible_span_ids": legacy_true,
            "span_count": len(snap.spans),
        }
        for candidate in snap.spans:
            check(SV.is_completion_eligible(verified, candidate.span_id) is False,
                  f"T39 B 阶段历史 A 记录的 span {candidate.span_id} 完成资格必须仍为 "
                  f"False——正式判据读快照自载策略，绝不读当前全局常量")


# ---------------------------------------------------------------------------
# 5/6. A/B 单点真值表与"历史 A 产物不因进入 B 环境而解禁"
# ---------------------------------------------------------------------------

def _ab_single_source(handoff, snap, verified, gap_target_id, gap_snaps):
    span_id = snap.spans[0].span_id
    original_min = V.SPAN_CONFIDENCE_MIN
    original_policy = snap.qualification_policy
    try:
        # --- 5) 临时模拟"已裁决"：真值表必须整体翻转（单点来源）。 -------------
        V.SPAN_CONFIDENCE_MIN = 0.5
        flipped = SP.ab_gate_truth_table()
        check(flipped["span_confidence_min"] == 0.5
              and flipped["threshold_decided"] is True
              and flipped["stage"] == "threshold_enabled"
              and flipped["completion_enabled"] is True
              and flipped["set_complete_supported"] is True
              and flipped["threshold_required"] is True,
              f"T39 真值表必须以 versions.SPAN_CONFIDENCE_MIN 为唯一输入，得到 {flipped}")
        raises(lambda: SP.assert_distribution_only(original_policy),
               SP.PolicyResolutionError, "已进入 threshold_enabled 阶段",
               "T39 进入 B 阶段后 distribution-only 链路必须拒绝继续充当正式口径")

        # --- 6a) 历史 A 快照在 B 环境里仍可复核，但完成结论仍为假。 ------------
        again = SV.verify_span_snapshot(snap, handoff)
        check(again.snapshot is snap,
              "T39 A 快照在模拟 B 环境里必须仍可复核（升阶段不得作废历史产物）")
        check(again.snapshot.qualification_policy.stage == "distribution_only",
              "T39 复核不得改写快照自载的策略阶段")
        check(SV.is_completion_eligible(again, span_id) is False,
              "T39 A 产物不因环境进入 B 而取得完成资格")
        check(SV.is_completion_eligible(verified, span_id) is False,
              "T39 A 产物在 B 环境里对同一 span 仍必须为 False")

        # --- 6b) 把 A 产物挂上 B 策略记录：不得归还完成资格，绝不"顺手升级"。 ---
        b_policy = _b_policy(original_policy)
        object.__setattr__(snap, "qualification_policy", b_policy)
        check(snap.qualification_policy.stage == "threshold_enabled",
              "T39 受控替换必须确实换成了 B 策略记录（否则这条反例不成立）")
        raises(lambda: SP.policy_provider_authority_fingerprint(b_policy),
               SP.PolicyResolutionError, "指纹与注册表钉住值不一致",
               "T39 伪造的 B 策略拿不到 authority 指纹（这正是它不得放行的原因）")
        check(SV.is_completion_eligible(verified, span_id) is False,
              "T39 换上 B 策略记录后必须拒绝放行（不得归还完成资格）")
        check(SV.is_completion_eligible(verified, "os-does-not-exist") is False,
              "T39 threshold_enabled 分支对未知 span 同样必须为 False")
        message = raises_any(lambda: SV.verify_span_snapshot(snap, handoff),
                             SchemaValidationError,
                             "T39 被挂上 B 策略的 A 产物不得通过复核（也不得被升级）")
        check(bool(message),
              "T39 复核失败必须给出可读原因（不得静默通过）")

        # --- 6c) 即使把阶段临时推到 B，缺口 span 也必须拒绝放行。 --------------
        for name, case_snap in sorted(gap_snaps.items()):
            case_policy = case_snap.qualification_policy
            object.__setattr__(case_snap, "qualification_policy", b_policy)
            object.__setattr__(verified, "_snapshot", case_snap)
            try:
                check(SV.is_completion_eligible(verified, gap_target_id) is False,
                      f"T39 模拟 B 环境下覆盖用例 {name} 不得放行"
                      f"（不得因已有已准入 Evidence 而放行）")
            finally:
                object.__setattr__(verified, "_snapshot", snap)
                object.__setattr__(case_snap, "qualification_policy", case_policy)
        check(verified.snapshot is snap,
              "T39 覆盖用例的受控替换必须已还原")
    finally:
        object.__setattr__(snap, "qualification_policy", original_policy)
        V.SPAN_CONFIDENCE_MIN = original_min

    # 还原后必须回到**进入本用例时的那一个阶段**（A / B 两条分支都要断言）。
    restored = SP.ab_gate_truth_table()
    _results["ab_single_source_original_min"] = original_min
    check(V.SPAN_CONFIDENCE_MIN == original_min,
          f"T39 受控改写必须逐字还原 versions.SPAN_CONFIDENCE_MIN，"
          f"原值 {original_min!r}，得到 {V.SPAN_CONFIDENCE_MIN!r}")
    if original_min is None:
        check(restored["stage"] == "distribution_only"
              and restored["span_confidence_min"] is None
              and restored["completion_enabled"] is False
              and restored["set_complete_supported"] is False,
              f"T39 受控改写必须已还原到未裁决，得到 {restored}")
    else:
        check(restored["stage"] == "threshold_enabled"
              and restored["span_confidence_min"] == original_min
              and restored["completion_enabled"] is True
              and restored["set_complete_supported"] is True,
              f"T39 受控改写必须已还原到当前已裁决阶段，得到 {restored}")
    check(snap.qualification_policy is original_policy
          and snap.qualification_policy.stage == "distribution_only",
          "T39 受控替换的策略必须已还原")
    check(SV.is_completion_eligible(verified, span_id) is False,
          "T39 还原后完成结论必须仍为 False")
    if restored["stage"] == "distribution_only":
        check(SP.assert_distribution_only(original_policy) is None,
              "T39 还原到 A 阶段后 A 记录必须重新通过 distribution-only 前置门")
    else:
        raises(lambda: SP.assert_distribution_only(original_policy),
               SP.PolicyResolutionError, "已进入 threshold_enabled 阶段",
               "T39 还原到 B 阶段后历史 A 记录必须继续被拒绝充当正式口径")


# ---------------------------------------------------------------------------
# 7. 受控模拟 B 环境：`threshold_enabled` 分支的完整真值表
# ---------------------------------------------------------------------------

def _threshold_branch(handoff, snap_a, verified_a, case_snaps, gap_target_id):
    """§7：在**受控模拟的 B 环境**里逐条检验 `threshold_enabled` 分支的 12 个条件。

    环境只临时改写两个模块级读取点（`span_policy.POLICY_DIR`、
    `versions.SPAN_CONFIDENCE_MIN`），并在 `finally` 里逐字还原；正式 `policies/` 目录
    （含注册表与已由 runner 导出的 approval / frozen 资产）**只读比对身份、一个字节都不改**，
    本模块也**不**创建任何正式 B 资产（approval / frozen 一律由 holder 从 sealed
    attestation 导出，测试不得伪造）。

    正例与负例都走**正式读取点**（策略注册表、authority 指纹、覆盖区间代数），
    因此这里的判定不是把判据抄了一遍，而是"资产身份 → 判定"的真实读数。
    """
    original_dir = SP.POLICY_DIR
    original_min = V.SPAN_CONFIDENCE_MIN
    original_registry = (original_dir / SP.REGISTRY_FILENAME).read_bytes()
    b_asset_names = (SP.APPROVAL_RECORD_FILENAME, SP.FROZEN_RECORD_FILENAME)
    b_assets_before = {name: _policy_asset_bytes(original_dir, name)
                       for name in b_asset_names}
    confidences = sorted({s.confidence for s in snap_a.spans})
    check(len(confidences) >= 2,
          f"T39-B 夹具必须给出至少两个不同的 boundary confidence 才能劈开阈值，"
          f"得到 {confidences}")
    above = 0.5
    between = round((confidences[0] + confidences[1]) / 2.0, 3)
    check(above < confidences[0],
          f"T39-B 达阈值环境 {above} 必须低于全部真实置信度 {confidences}")
    check(confidences[0] < between <= confidences[1],
          f"T39-B 劈分阈值 {between} 必须严格落在 {confidences[0]} 与 "
          f"{confidences[1]} 之间（两侧都要有样本，否则真值表是空的）")
    # 只保留一个"未登记策略"用的测试键：其余模拟阈值都走**正式 frozen 条目**（沙箱里
    # 由正式派生函数重算指纹），因此不再需要逐阈值一个键。
    key_rogue = "span-qualification-threshold-rogue-test"
    _results["threshold_branch"] = {
        "above": above, "between": between, "confidences": confidences}

    calls: list = []
    restore_spy = _spy_legacy(calls)
    positives: dict = {}
    try:
        with _SimulatedBEnvironment(above) as env:
            check(V.SPAN_CONFIDENCE_MIN == above and SP.POLICY_DIR != original_dir,
                  "T39-B 模拟环境必须已生效（阈值常量与策略目录都临时改写）")
            snap_b, verified_b = _build_b_verified(handoff, env)
            policy_b = snap_b.qualification_policy
            span_ids = [s.span_id for s in snap_b.spans]

            # (1)(2)(3) 输入是已签发的复核快照；自载策略合法且与 authority 绑定；
            #           阈值是与该策略身份一致的有限数。
            check(verified_b.snapshot is snap_b,
                  "T39-B 受控模拟必须先造出一份能通过复核的 threshold 快照")
            check(policy_b.stage == "threshold_enabled"
                  and policy_b.completion_enabled is True
                  and policy_b.set_complete_supported is True
                  and policy_b.span_confidence_min == above,
                  f"T39-B 快照自载策略必须是启用的 threshold 策略，得到 "
                  f"{policy_b.stage}/{policy_b.span_confidence_min}")
            check(snap_b.trusted_input.qualification[12]
                  == SP.policy_provider_authority_fingerprint(policy_b),
                  "T39-B 快照记录的 authority 指纹必须与该策略逐字复现一致")
            check(bool(span_ids) and len(span_ids) == len(set(span_ids)),
                  "T39-B 快照的 span 必须非空且 id 唯一")

            # 前置事实（独立于判据）：本夹具的 span 全部是正式正文材料。
            disp_by_span = {d.span_id: d for d in snap_b.dispositions
                            if d.span_id is not None}
            check(sorted({d.range_kind for d in disp_by_span.values()}) == ["regular"],
                  "T39-B 前置事实：带 span 的处置必须全部是 regular"
                  f"，得到 {sorted({d.range_kind for d in disp_by_span.values()})}")
            tables = [d for d in snap_b.dispositions
                      if d.range_kind in ("table_inside", "table_adjacency")]
            check(bool(tables),
                  "T39-B 前置事实：夹具必须真的含表格区间（否则表格负例是空的）")
            check(all(d.span_id is None for d in tables),
                  "T39-B 表格区间不得携带 span（表格材料仍只能由 TS5 裁决）")
            others = [d for d in snap_b.dispositions
                      if d.range_kind in ("unassigned", "empty")]
            check(all(d.span_id is None for d in others),
                  "T39-B 未归属 / 空区间不得携带 span")
            check(all(s.role == "body" and not s.is_fallback
                      and not s.is_cross_heading and s.node_id is not None
                      and s.unassigned_reason is None for s in snap_b.spans),
                  "T39-B 前置事实：夹具 span 必须全部是已归属的正式正文材料")
            check(all(_admitted_component_count(snap_b, s.span_id) > 0
                      for s in snap_b.spans),
                  "T39-B 前置事实：每个 span 必须带有已准入 Evidence")

            # (4)(5)(6)(7)(8)(9)(10)(11) 正向：达阈值 + 全覆盖 + 正式正文 ⇒ 全放行。
            for span_id in span_ids:
                got = SV.is_completion_eligible(verified_b, span_id)
                positives[span_id] = got
                check(_gap_kind(next(c for c in snap_b.coverages
                                     if c.span_id == span_id)) is None,
                      f"T39-B 前置事实：span {span_id} 的覆盖必须无缺口")
                check(got is True,
                      f"T39-B 达阈值且全覆盖的正式正文 span {span_id} 必须放行，"
                      f"得到 {got!r}")
            _results["threshold_branch"]["positive_spans"] = len(positives)

            def _under(forged, span_id, label):
                """把伪造快照挂进复核能力里问一次完成资格，问完立刻还原。"""
                object.__setattr__(verified_b, "_snapshot", forged)
                try:
                    got = SV.is_completion_eligible(verified_b, span_id)
                finally:
                    object.__setattr__(verified_b, "_snapshot", snap_b)
                check(got is False, f"T39-B {label} 必须为 False，得到 {got!r}")
                return got

            # (4) 未知 span_id。
            _under(snap_b, "os-does-not-exist", "未知 span_id")

            # (4)(5) 重复 / 缺失 coverage：一律 fail-closed。
            base = max(snap_b.coverages, key=lambda c: c.span_local_length)
            dup = _clone(snap_b)
            object.__setattr__(dup, "coverages", snap_b.coverages + (base,))
            _under(dup, base.span_id, "重复 coverage")
            missing = _clone(snap_b)
            object.__setattr__(missing, "coverages",
                               tuple(c for c in snap_b.coverages if c is not base))
            _under(missing, base.span_id, "缺失 coverage")

            # (6) 未达阈值：把目标的 boundary confidence 压到**本环境阈值**之下。
            check(next(s for s in snap_b.spans
                       if s.span_id == base.span_id).confidence >= above,
                  "T39-B 未达阈值反例必须从一份**本来达阈值**的 span 出发"
                  "（否则这条反例不成立）")
            low_span = _clone(next(s for s in snap_b.spans if s.span_id == base.span_id))
            object.__setattr__(low_span, "confidence", above - 0.001)
            low = _clone(snap_b)
            object.__setattr__(low, "spans", tuple(
                low_span if s.span_id == low_span.span_id else s
                for s in snap_b.spans))
            _under(low, base.span_id, f"boundary confidence 低于阈值 {above}")

            # (7) fallback / 跨标题 / 非正文角色 / 显式未归属。
            for label, changes in (
                    ("fallback span", {"is_fallback": True}),
                    ("跨标题 span", {"is_cross_heading": True}),
                    ("非正文角色 span", {"role": "list"}),
                    ("显式未归属 span", {"role": "unassigned", "node_id": None,
                                         "unassigned_reason": "no_heading_context"}),
                    ("表格角色 span", {"role": "table_caption"})):
                forged_span = _clone(next(s for s in snap_b.spans
                                          if s.span_id == base.span_id))
                for field, value in changes.items():
                    object.__setattr__(forged_span, field, value)
                forged = _clone(snap_b)
                object.__setattr__(forged, "spans", tuple(
                    forged_span if s.span_id == forged_span.span_id else s
                    for s in snap_b.spans))
                _under(forged, base.span_id, label)

            # (10)(11) 自报派生值不可信：只把 required 与它的计数一起改小，
            #          基础区间（归一化 / 可引用 / 不可引用 / 未覆盖）一个不动。
            liar = _clone(base)
            kept = base.required_content_intervals[:-1]
            object.__setattr__(liar, "required_content_intervals", tuple(kept))
            object.__setattr__(liar, "required_content_chars",
                               sum(iv.end - iv.start for iv in kept))
            check(base.normalization_only_intervals == liar.normalization_only_intervals
                  and base.citable_source_intervals == liar.citable_source_intervals
                  and base.non_citable_source_intervals
                  == liar.non_citable_source_intervals
                  and base.uncovered_source_intervals
                  == liar.uncovered_source_intervals,
                  "T39-B 自报派生值反例只允许改 required 与其计数（否则不构成该反例）")
            check(bool(liar.problems) is False,
                  "T39-B 自报派生值反例不得靠 problems 挡下（必须由重算识别）")
            lying = _clone(snap_b)
            object.__setattr__(lying, "coverages", tuple(
                liar if c is base else c for c in snap_b.coverages))
            _under(lying, base.span_id, "自报 required 与基础区间不自洽的 coverage")

            # (2)(3) 非法策略身份 / 非法阈值：身份有效但内容非法时（克隆保留
            #        policy_id）必须由阈值有限性与阶段一致性挡下。
            for label, changes in (
                    ("阈值 NaN 的策略", {"span_confidence_min": float("nan")}),
                    ("阈值布尔值的策略", {"span_confidence_min": True}),
                    ("阶段回退的策略", {"stage": "distribution_only",
                                        "completion_enabled": False,
                                        "set_complete_supported": False})):
                forged_policy = _clone(policy_b)
                for field, value in changes.items():
                    object.__setattr__(forged_policy, field, value)
                forged = _clone(snap_b)
                object.__setattr__(forged, "qualification_policy", forged_policy)
                check(forged_policy.policy_id == policy_b.policy_id
                      and SP.policy_provider_authority_fingerprint(forged_policy)
                      == snap_b.trusted_input.qualification[12],
                      f"T39-B {label} 的**身份**必须仍然合法（否则这条反例靠的是"
                      f"身份而不是内容：它要用内容检查挡下）")
                _under(forged, base.span_id, label)

            # (2) 未注册策略（拿不到 authority 指纹）必须整个 fail-closed。
            rogue = _b_policy(policy_b, threshold=above, policy_key=key_rogue)
            raises(lambda: SP.policy_provider_authority_fingerprint(rogue),
                   SP.PolicyResolutionError, "未登记策略",
                   "T39-B 未注册策略拿不到 authority 指纹（这正是它不得放行的原因）")
            forged_rogue = _clone(snap_b)
            object.__setattr__(forged_rogue, "qualification_policy", rogue)
            _under(forged_rogue, base.span_id, "未注册策略")

            # (2) 记录指纹与自载策略不一致（把 A 快照的 trusted_input 挂到 B 快照上）。
            mismatched = _clone(snap_b)
            object.__setattr__(mismatched, "trusted_input", snap_a.trusted_input)
            check(snap_a.trusted_input.qualification[12]
                  != snap_b.trusted_input.qualification[12],
                  "T39-B 两个阶段的 authority 指纹必须不同（否则这条反例不成立）")
            _under(mismatched, base.span_id, "自载策略与记录指纹不一致")

            # (8)(9) 头部 / 中部 / 尾部缺口、不可引用、未覆盖、归一化掩盖缺口。
            cases = _coverage_cases(base)
            check(any(expected is True for _c, expected in cases.values()),
                  "T39-B 覆盖真值表必须含至少一条正面对照（否则只剩负例）")
            _results["threshold_branch"]["coverage_cases"] = {}
            for name, (coverage, expected) in sorted(cases.items()):
                check(bool(coverage.required_content_intervals),
                      f"T39-B 覆盖用例 {name} 的必需内容不得为空（否则断言是空的）")
                if expected is False:
                    check(coverage.effective_citable_intervals
                          != coverage.required_content_intervals,
                          f"T39-B 覆盖用例 {name} 必须真的构成缺口")
                forged = _clone(snap_b)
                object.__setattr__(forged, "coverages", tuple(
                    coverage if c is base else c for c in snap_b.coverages))
                object.__setattr__(verified_b, "_snapshot", forged)
                try:
                    got_case = SV.is_completion_eligible(verified_b, base.span_id)
                finally:
                    object.__setattr__(verified_b, "_snapshot", snap_b)
                _results["threshold_branch"]["coverage_cases"][name] = {
                    "expected": expected, "got": got_case}
                check(got_case is expected,
                      f"T39-B 覆盖用例 {name} 期望 {expected}，得到 {got_case!r}")

            # 6a 复核：历史 A 快照在 B 环境里仍可复核，完成结论仍为 False；
            #     A 策略（distribution_only / 阈值为 None）不得被就地解释成 B 口径。
            again_a = SV.verify_span_snapshot(snap_a, handoff)
            check(again_a.snapshot is snap_a,
                  "T39-B 历史 A 快照在模拟 B 环境里必须仍可复核")
            check(SV.is_completion_eligible(again_a, snap_a.spans[0].span_id) is False,
                  "T39-B 历史 A 快照在模拟 B 环境里完成结论仍必须为 False")
            check(SV.is_completion_eligible(verified_a, snap_a.spans[0].span_id) is False,
                  "T39-B 历史 A 复核能力在模拟 B 环境里仍必须为 False")

            # 6c 缺口快照挂上合法 B 策略后仍不得放行。
            for name, case_snap in sorted(case_snaps.items()):
                case_policy = case_snap.qualification_policy
                object.__setattr__(case_snap, "qualification_policy", policy_b)
                object.__setattr__(verified_b, "_snapshot", case_snap)
                try:
                    check(SV.is_completion_eligible(verified_b, gap_target_id) is False,
                          f"T39-B 覆盖缺口快照 {name} 挂上合法 B 策略后仍不得放行")
                finally:
                    object.__setattr__(verified_b, "_snapshot", snap_b)
                    object.__setattr__(case_snap, "qualification_policy", case_policy)
            check(verified_b.snapshot is snap_b,
                  "T39-B 受控替换必须已还原")

            # 4b 运行时 spy：B 分支同样一次也不得触碰 TS3 遗留判据。
            check(calls == [],
                  f"T39-B threshold_enabled 分支一次也不得调用遗留判据，实际调用 {calls}")
    finally:
        restore_spy()

    # 旧入口改成"一调就炸"，B 分支仍必须走完（不是靠旧入口兜底）。
    original_legacy = OutlineSpan.can_support_set_complete

    def _explode(self, **kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError(
            "threshold_enabled 分支不得调用 OutlineSpan.can_support_set_complete()")

    OutlineSpan.can_support_set_complete = _explode
    try:
        with _SimulatedBEnvironment(above) as env2:
            snap_e, verified_e = _build_b_verified(handoff, env2)
            check(all(SV.is_completion_eligible(verified_e, s.span_id) is True
                      for s in snap_e.spans),
                  "T39-B 旧入口被改成“一调就炸”后，B 分支仍必须逐 span 放行")
            SV.verify_span_snapshot(snap_e, handoff)
    finally:
        OutlineSpan.can_support_set_complete = original_legacy
    check(OutlineSpan.can_support_set_complete is original_legacy,
          "T39-B 旧入口哨兵必须已还原（不得污染其他测试）")

    # 劈分阈值环境：逐 span 的期望必须恰好是 `confidence >= threshold`。
    with _SimulatedBEnvironment(between) as env3:
        snap_c, verified_c = _build_b_verified(handoff, env3)
        check(snap_c.qualification_policy.span_confidence_min == between,
              "T39-B 劈分环境必须真的换了一份阈值策略")
        expected = {s.span_id: (s.confidence >= between) for s in snap_c.spans}
        check(len(set(expected.values())) == 2,
              f"T39-B 劈分阈值必须真的把样本劈成两类，得到 {expected}")
        got_all = {sid: SV.is_completion_eligible(verified_c, sid) for sid in expected}
        mismatch = sorted(k for k in expected if got_all[k] is not expected[k])
        check(not mismatch,
              f"T39-B 逐 span 期望（confidence >= {between}）与实际不符："
              f"{ {k: (expected[k], got_all[k]) for k in mismatch} }")
        confidence_by_id = {s.span_id: s.confidence for s in snap_c.spans}
        _results["threshold_branch"]["between_truth_table"] = {
            sid: {"confidence": confidence_by_id[sid],
                  "expected": expected[sid], "got": got_all[sid]}
            for sid in sorted(expected)}

    # 等值边界环境：阈值**恰好等于**某个 span 的置信度 ⇒ 该 span 必须通过。语义是
    # `confidence >= threshold`，**不是**严格大于；若实现写成"严格高于阈值"，这条
    # 反例会把该 span 误拒（这是 `>` / `>=` 唯一能真正被劈开的地方）。
    with _SimulatedBEnvironment(confidences[0]) as env4:
        snap_d, verified_d = _build_b_verified(handoff, env4)
        check(snap_d.qualification_policy.span_confidence_min == confidences[0],
              "T39-B 等值边界环境必须真的换了一份阈值策略")
        exact_ids = sorted(s.span_id for s in snap_d.spans
                           if s.confidence == confidences[0])
        check(bool(exact_ids),
              f"T39-B 等值边界反例必须真的存在 confidence == "
              f"{confidences[0]} 的 span（否则劈不开 > 与 >=）")
        refused = [sid for sid in exact_ids
                   if SV.is_completion_eligible(verified_d, sid) is not True]
        check(not refused,
              f"T39-B confidence == threshold（{confidences[0]}）必须通过完成判据"
              f"（语义是 >= 而非严格大于），被拒的 span：{refused}")
        _results["threshold_branch"]["exact_boundary"] = {
            "threshold": confidences[0], "span_ids": exact_ids}

    # 环境与全局常量必须逐字还原到**进入本函数时的那一个阶段**（A / B 两条分支都断言）。
    check(SP.POLICY_DIR == original_dir,
          "T39-B 模拟环境的策略目录必须已还原")
    check(V.SPAN_CONFIDENCE_MIN == original_min,
          f"T39-B 测试结束时 SPAN_CONFIDENCE_MIN 必须已还原为 {original_min!r}，"
          f"得到 {V.SPAN_CONFIDENCE_MIN!r}")
    check((original_dir / SP.REGISTRY_FILENAME).read_bytes() == original_registry,
          "T39-B 正式策略注册表不得被测试改写")
    # 正式 approval / frozen 资产：本模块只准**只读**比对，绝不准伪造、改写或补建。
    b_assets_after = {name: _policy_asset_bytes(original_dir, name)
                      for name in b_asset_names}
    check(b_assets_after == b_assets_before,
          "T39-B 正式 approval / frozen 资产的身份不得被测试改动"
          "（模拟环境只临时改写内存读取点）")
    if original_min is None:
        check(SP.ab_gate_truth_table()["stage"] == "distribution_only",
              "T39-B 未裁决阶段真值表必须仍为 distribution_only")
    else:
        check(SP.ab_gate_truth_table()["stage"] == "threshold_enabled",
              "T39-B 已裁决阶段真值表必须仍为 threshold_enabled")
        check(all(bytes_ is not None for bytes_ in b_assets_after.values()),
              "T39-B 已裁决阶段必须存在正式 approval / frozen 资产"
              "（模拟环境不得替代它们）")
        check(SP.resolve_frozen_policy().span_confidence_min == original_min,
              "T39-B 已裁决阶段的正式 frozen 策略阈值必须与全局常量一致")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> dict:
    # A 侧基线在**受控模拟 A 环境**里由正式签发路径产出（进入 B 之后仍要造 A 产物，
    # 只能显式把阶段拨回去，而不是绕过阶段门）。随后立刻离开该环境：下面全部断言都在
    # **真实当前阶段**下运行 —— 这正是"历史 A 快照在 B 环境仍可复核、且完成结论仍恒假"
    # 的证据（§18.8.5 / 计划 §18.3.2(9)）。
    with _SimulatedAEnvironment() as a_env:
        handoff = _fixture_handoff()
        snap = SB._build_from_pinned_handoff(handoff, stage="distribution_only")
        a_table = dict(a_env.table)
        pinned_a = SP.resolve_distribution_policy()
        SP.assert_distribution_only(pinned_a)
    _results["a_stage_truth_table"] = a_table
    _results["stage_at_run"] = SP.ab_gate_truth_table()["stage"]
    verified = SV.verify_span_snapshot(snap, handoff)
    check(verified.snapshot is snap,
          "T39 正向基线必须先能通过复核（否则全部断言不成立）")

    self_check = SV.self_check()
    check(self_check["problems"] == [],
          f"T39 复核层自检必须无问题：{self_check['problems']}")
    policy_check = SP.self_check()
    check(policy_check["problems"] == [],
          f"T39 策略层自检必须无问题：{policy_check['problems']}")

    _results["counts"] = {
        "spans": len(snap.spans), "coverages": len(snap.coverages),
        "components": len(snap.components),
        "admitted_components": sum(1 for c in snap.components if c.admitted),
        "outline_nodes": len(handoff.document_outline.nodes),
    }
    _preconditions(handoff, snap, verified, pinned_a)
    _always_false(snap, verified, a_table)
    gap_target_id, gap_snaps = _gaps_are_not_released(handoff, snap, verified)
    _legacy_entry_is_off_chain(handoff, snap, verified)
    _ab_single_source(handoff, snap, verified, gap_target_id, gap_snaps)
    _threshold_branch(handoff, snap, verified, gap_snaps, gap_target_id)
    return _results


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["failed"] == 0 else 1)

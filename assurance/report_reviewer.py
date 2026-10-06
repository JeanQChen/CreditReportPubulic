"""报告级只读审阅的**可执行链**（有界分批、事前预算、严格解析、可持久化）。

`assurance.report_review` 备的是**输入面**；本模块把那份输入接到一条真能跑、但**本批一次
也没有跑**的链上。它做四件事：

1. **有界分批**：`plan_report_review_batches` 把一份 scope 切成若干**确定、不重叠、合起来
   恰好覆盖全部单元**的批次。分批不是"省 token"，而是因为本端点的输出档位就是 8192
   （`logs/llm` 实测：单次最大 output 8,193 且 `finish_reason=max_tokens`）。**104,904 字符 /
   150 单元不得当成一次必能完成的调用。**
2. **事前容量预检**：`assert_within_capacity` 在任何请求之前核对每一批的单元数与字符数，
   并在**单个单元本身就超上限**时直接失败——不静默截断、不偷偷丢单元。
3. **严格解析**：`parse_report_review` 只接受 `{"issues": [...]}`，并要求每条意见都指向
   本批、本 scope 内的单元且不是重复问题。**它不要求逐单元表态**：`issues` 可以为空
   （= 本次调用没发现会实质改变结论的问题）。"哪些成员进了请求"由程序从批次推出，
   不由模型用 `supported` 逐条回声证明——那种回声恰好会把"没看"与"看过没问题"混成一样。
4. **可持久化**：`ReportReviewRun` 把逐批的 `ReviewerRunRecord` 与全部 `ReviewIssue` 绑在
   `scope_version`/`bundle_id` 上；`write_report_review_run` / `load_report_review_run`
   是唯一的落盘与读回路径。

**三本账，互不推导（`rrq-2`）。** `requested_unit_ids` = 确实放进过请求的成员；
`covered_unit_ids` = 进了**得到可解析回复**的批次的成员；`reported_unit_ids` = 模型
**实际提出问题**的成员。三者可以互不相等，产物里也**没有**任何字段可以被读成
"逐份材料都被核实过"。

**它不做什么。** 不改稿、不补研究、不重算任何数字、不覆盖机械硬错误、不产生
`AssuranceResult`、不产生任何放行状态。意见的聚合与"硬错误优先"由
`assurance.cited_controller` 确定性完成。

**意见的 schema 版本。** 这三块内容的单元不是句子（A2 的行、清单成员、两节正文与 A2 表），
`rvi-2` 强制 `sentence_id` 非空——塞进来只能靠伪造一个句 id。因此报告级意见用 **`rvi-1`**，
目标身份由 `unit_ref`（`citation:<key>` / `row:<aspect_id>` / `section:<id>` /
`table:fin_balance_structure`）承载，不新增任何 wire 词表。

**调用预算。** 本模块**不新建第二套账本**：它给出一份**独立**的
`llm.budget.CallBudgetPolicy`（新轴 `report_review`），由既有的
`llm.client.chat_with_usage` → `llm.budget.reserve_attempt` 在**发请求之前**记账并判上限。
未获授权时那份政策装不上强制门（构造期 `ValueError`），因此"没授权就发不出去"是结构性的，
不靠调用方自觉。
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from assurance import report_review as RR
from assurance import schema as AS
from llm import budget as LB
from sections import cited_writer as CW


#: 本**链**的策略版本（有界分批 + 容量选择 + 解析规则）。它**不是**输入准备策略
#: `assurance.report_review.REPORT_REVIEW_POLICY_VERSION`（`rrrp-1`）：那一份由
#: `scope_id` 间接绑定（`policy_version` 在 scope 的内容身份里），因此输入准备策略一变，
#: `scope_version` 就变，绑在旧版本上的运行记录自动成为陈旧读。
REPORT_REVIEW_CHAIN_POLICY_VERSION = "rrc-2"
REPORT_REVIEW_RUN_SCHEMA_VERSION = "rrq-2"

#: prompt 资产身份取自**冻结**常量（`assurance.schema`）：报告级审阅用的就是那一个
#: "独立审阅者" prompt 版本，不新开一条 wire 版本线。
REPORT_REVIEW_PROMPT_ASSET = AS.INDEPENDENT_REVIEWER_PROMPT_NAME
REPORT_REVIEW_PROMPT_REVISION = "irv-2"
REPORT_REVIEW_PROMPT_VERSION = AS.INDEPENDENT_REVIEWER_PROMPT_VERSION
if f"{REPORT_REVIEW_PROMPT_ASSET}@{REPORT_REVIEW_PROMPT_REVISION}" \
        != REPORT_REVIEW_PROMPT_VERSION:  # pragma: no cover - 常量对账
    raise AssertionError(
        "报告级审阅 prompt 版本与 assurance.schema 的冻结常量不一致："
        f"{REPORT_REVIEW_PROMPT_ASSET}@{REPORT_REVIEW_PROMPT_REVISION} != "
        f"{REPORT_REVIEW_PROMPT_VERSION}")

#: 意见产出者身份（与 A1 逐句审阅同一封闭两档词汇，不另立第三档）。
REPORT_REVIEW_PRODUCER_KINDS = ("independent_llm_review", "offline_diagnostic_echo")

#: 自我放行/改写类字段：出现在响应**信封层**即拒。以冻结的
#: `AS.REVIEWER_FORBIDDEN_FIELDS`（§7.7）为准并补几个同义写法——信封层的字段集是开放的
#: （模型可以多吐一个键），所以这里必须是"禁列"而不是"允许列"，否则
#: `{"issues": [...], "verdict": "pass"}` 里的 `verdict` 只会被当成一个不认识的多余键。
REPORT_REVIEW_FORBIDDEN_ENVELOPE_FIELDS = tuple(sorted(
    set(AS.REVIEWER_FORBIDDEN_FIELDS)
    | {"released", "accepted", "human_accepted", "prose", "conclusion"}))

#: 单个批次允许的**单元数**上限。这是**输出**侧的参数：审阅必须逐单元表态，一条意见
#: 的 JSON（含简短理由）实测约 150 output token 量级，8192 的输出档位对它只多不少，
#: 取 25 是给它一个 2 倍以上余量。
REPORT_REVIEW_MAX_UNITS_PER_CALL = 25
#: 单个批次允许的**输入**字符上限。60,000 字符按实测下界 1.60 字符/token ≈ 37,500 token，
#: 而本端点实测接受过 147,546 input token 的单次调用——input 侧不是约束，这道上限存在的
#: 意义是"别把整个 scope 塞进一次请求"，不是逼近 provider 的上下文边界。
REPORT_REVIEW_MAX_INPUT_CHARS_PER_CALL = 60000
#: 单次请求的输出容量（发往 provider 的 `max_tokens`）。取 8192：本端点 `logs/llm`
#: 9,513 条有 usage 的真实调用里，成功返回的最大输出是 8,193（且 `finish_reason=max_tokens`），
#: 即 8192 就是本端点的输出档位；更小的值会把"容量不够"变成"截断即失败"。
REPORT_REVIEW_MAX_OUTPUT_TOKENS = 8192
#: 字符 → token 的**保守**换算（实测下界）。取实测下界而不是中位数：低估字符数会高估
#: token 数，方向上只会更保守。
REPORT_REVIEW_CHARS_PER_TOKEN = 1.60
REPORT_REVIEW_CAPACITY_BASIS = (
    "依据本仓 `logs/llm` 9,513 条带 usage 的真实调用读视图（只读、未发请求）："
    "单次最大 input_tokens = 147,546（268,947 字符 ⇒ 1.823 字符/token）；"
    "字符/token 在 input>500 的 3,422 条里下界 1.604、中位 2.067，故换算取**下界 1.60**。"
    "单次最大 output_tokens = 8,193 且 finish_reason=max_tokens ⇒ 本端点输出档位为 8192，"
    "因此批次大小由**单元数**（输出条数）决定，输入字符数只作第二道上限。"
    "模型：本仓已批准模型。本模块不发起任何调用，以上只是容量选择依据。")

#: 分批规则与**输入面**（`assurance.report_review`）声明的规则必须逐字相同：输入面的
#: `scope_id` 里存着这批规则，链侧换一个数就会让「同一份输入」在两条规则下各有一次调用计数，
#: 而账本上只有一个数。不一致即导入期失败——与 prompt 资产对账同一纪律。
_REPORT_REVIEW_BATCHING_RULES = {
    "plan_version": "rrb-1",
    "max_units_per_call": REPORT_REVIEW_MAX_UNITS_PER_CALL,
    "max_input_chars_per_call": REPORT_REVIEW_MAX_INPUT_CHARS_PER_CALL,
    "max_output_tokens": REPORT_REVIEW_MAX_OUTPUT_TOKENS,
    "chars_per_token": REPORT_REVIEW_CHARS_PER_TOKEN,
}
if _REPORT_REVIEW_BATCHING_RULES != dict(RR.REPORT_REVIEW_BATCHING_RULES):  # pragma: no cover
    raise AssertionError(
        "报告级审阅的分批规则与本链的容量常数不一致："
        f"{_REPORT_REVIEW_BATCHING_RULES!r} != {dict(RR.REPORT_REVIEW_BATCHING_RULES)!r}——"
        "输入面的 scope_id 里存着前者，链按后者切批，两者必须是同一套规则")

#: 预算轴的记账作用域前缀：报告级审阅不属于任何"节"，但 `llm.budget` 的记账需要一个
#: 可回查的作用域串。`report:<scope_kind>` 让"哪一块内容花了多少次"可直接读回。
REPORT_REVIEW_SCOPE_PREFIX = "report:"

#: 报告级审阅的预算类别与预算轴。类别**不**登记进 `CallBudgetPolicy.categories`
#: （那是写作类别的 `CategoryCap` 表），而是经 `category_axis` 挂到本轴——于是它走
#: `llm.budget` 的"逐 scope + 整轴"分支，与写作侧的"逐类 + 逐节"计量互不消耗。
REPORT_REVIEW_BUDGET_CATEGORY = "report_review"
REPORT_REVIEW_BUDGET_AXIS = "report_review"


class ReportReviewError(ValueError):
    """报告级审阅链在装配 / 预检 / 解析任一步的 fail-closed。"""

    def __init__(self, message: str, *, reason: str = "", unit_key: str = "") -> None:
        super().__init__(message)
        self.reason = reason
        self.unit_key = unit_key


class ReportReviewRunAborted(ReportReviewError):
    """一次已经开始的批次运行中止：**已完成的批次不会被丢掉**，随异常一起交回。

    失败留存的意义就在这里：一次真实调用烧掉的是已批准的额度，把它从流水里抹掉会让
    "发了几次"与"记了几条"不相等。
    """

    def __init__(self, message: str, *, cause: BaseException, scope_kind: str,
                 producer_kind: str,
                 partial_records: Sequence[AS.ReviewerRunRecord],
                 partial_issues: Sequence[AS.ReviewIssue],
                 attempted_batch_ids: Sequence[str],
                 attempted_unit_ids: Sequence[str],
                 reason: str = "batch_failed") -> None:
        super().__init__(message, reason=reason)
        self.cause = cause
        self.scope_kind = scope_kind
        #: 产出者身份**由 client 本身推出**（`report_review_producer_kind_of`），
        #: 在 raise 现场就固定下来；失败态不得由调用方事后"声明"自己是谁。
        self.producer_kind = producer_kind
        self.partial_records = tuple(partial_records)
        self.partial_issues = tuple(partial_issues)
        self.attempted_batch_ids = tuple(attempted_batch_ids)
        #: 确实被放进过请求的单元（含失败批）——「已尝试」与「已完成」由此分开。
        self.attempted_unit_ids = tuple(attempted_unit_ids)
        #: 中止是**在最后一批上**发生的（不重试、不跳过），因此已完成的批恰是
        #: 已尝试里除最后一批以外的那些；记录数必须与它相等，否则说明有人把失败批
        #: 也记成了一条记录。
        if len(self.partial_records) != len(self.attempted_batch_ids) - 1:
            raise ReportReviewError(
                f"ReportReviewRunAborted：已完成记录 {len(self.partial_records)} 条与"
                f"已尝试 {len(self.attempted_batch_ids)} 批不相符（应为 已尝试 − 1）——"
                "失败批不得混入已完成记录")
        self.completed_batch_ids = self.attempted_batch_ids[:-1]


def _require(condition: bool, message: str, *, reason: str = "", unit_key: str = "") -> None:
    if not condition:
        raise ReportReviewError(message, reason=reason, unit_key=unit_key)


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def _sorted_unique(values: Any, what: str) -> tuple[str, ...]:
    items = tuple(str(v) for v in values)
    _require(len(set(items)) == len(items), f"{what} 含重复项：{sorted(items)}")
    _require(list(items) == sorted(items), f"{what} 必须升序（确定性聚合的前提）")
    return items


def _estimated_input_tokens(char_count: int) -> int:
    return int(math.ceil(char_count / REPORT_REVIEW_CHARS_PER_TOKEN))


# ---------------------------------------------------------------------------
# 有界分批
# ---------------------------------------------------------------------------

def _slice_payload(scope: RR.ReportReviewScope, unit_keys: Sequence[str]) -> dict:
    """把 scope 的请求面**限定到本批单元**：不在本批的内容一条不留。

    只保留贡献了单元的 section / items / notes 行；判决所依据的**判据侧**内容整段保留
    ——`material_selectivity` 的 `contract_requirements` 与 `prose_sentences`、
    `cross_section` 的 `cited_sources` 与 `a2_notes`。它们不是"被表态的单元"，
    而是读本批单元时要对照的东西，切掉它们等于让审阅者拿一份自己没见过的判据下判断。

    另加两个**请求元数据**键：`batch_unit_keys` / `reviewed_unit_count`。它们由本批单元集
    推出，不是来源内容，作用是把"这批必须逐条表态的是哪几个单元"写成机器可读的一行——
    否则一份被切过的成员表里，"25" 这个数只能靠数行数猜。
    """
    ids = _unit_ids(unit_keys)
    payload = json.loads(_canonical(scope.request.payload))
    kind = scope.scope_kind
    if kind == "material_selectivity":
        kept: list[dict[str, Any]] = []
        for section in payload["sections"]:
            members = [m for m in section["members"] if m["citation_key"] in ids]
            if not members:
                continue
            section["members"] = members
            section["used_citation_keys"] = [m["citation_key"] for m in members
                                             if m["used_in_prose"]]
            section["unused_citation_keys"] = [m["citation_key"] for m in members
                                               if not m["used_in_prose"]]
            kept.append(section)
        payload["sections"] = kept
        kept_rows = sum(len(section["members"]) for section in kept)
    elif kind == "financial_a2":
        # 单元是 `row:<aspect_id>` 与 `row:note.<note_id>`（见 `report_review._a2_scope`），
        # 不是 excerpt 的 `citation_id`（那是 `a2:…`）。按 unit_ref 的 **id 段**切。
        payload["items"] = [item for item in payload["items"]
                            if item["aspect_id"] in ids]
        payload["notes"] = [note for note in payload["notes"]
                            if f"note.{note['note_id']}" in ids]
        kept_rows = len(payload["items"]) + len(payload["notes"])
    elif kind == "cross_section":
        # 三个单元就是整节正文/整表，payload 里没有"逐单元一行"的形状，切了反而让判据面
        # 自相矛盾（`review_question` 明说拿到了"两节正文全文"）。
        kept_rows = len(set(unit_keys))
    else:  # pragma: no cover - scope_kind 由 ReportReviewScope 限定
        raise ReportReviewError(f"未知 scope_kind：{kind!r}")

    # **切完之后必须数一遍**：单元键是 `citation:<citation_key>` 而 payload 里的成员是按裸
    # `citation_key` 键的，拿整体键去比会让每一批都切出**零个**成员——payload 变小、覆盖等式
    # 照样能过（模型可以对着 `batch_unit_keys` 逐条回声），于是"审阅者看到了 25 条材料"是假的。
    # 这道断言让那种沉默失效在这里就失败。
    _require(kept_rows == len(set(unit_keys)),
             f"{kind}：切出的内容行 {kept_rows} 条 ≠ 本批单元 {len(set(unit_keys))} 个——"
             "审阅者会对着看不见的单元表态，fail-closed")
    payload["batch_unit_keys"] = sorted(str(k) for k in unit_keys)
    payload["reviewed_unit_count"] = len(unit_keys)
    return payload


def _unit_ids(unit_keys: Sequence[str]) -> set[str]:
    """单元键 `'<kind>:<id>'`（`AS.unit_ref_key` 的形状）→ id 段集合。"""
    out: set[str] = set()
    for key in unit_keys:
        kind, sep, uid = str(key).partition(":")
        _require(bool(kind) and bool(sep) and bool(uid),
                 f"单元键 {key!r} 不是 '<kind>:<id>' 形状（无法定位 payload 里的对应内容）",
                 reason="unit_key_malformed")
        out.add(uid)
    return out


@dataclass(frozen=True)
class ReportReviewBatch:
    """一次调用所对应的**请求单元**：单元集 + 完整 payload + 容量读数。

    `payload` 就是要投出去的 JSON；`request_fingerprint` 是它的稳定指纹。单元集升序、
    非空、互不重叠（分批的不变量由 :func:`plan_report_review_batches` 保证）。
    """

    schema_version: str
    batch_id: str
    scope_id: str
    scope_version: str
    scope_kind: str
    unit_keys: tuple[str, ...]
    char_count: int
    estimated_input_tokens: int
    max_output_tokens: int
    payload: dict
    request_fingerprint: str

    def __post_init__(self) -> None:
        _require(self.schema_version == REPORT_REVIEW_RUN_SCHEMA_VERSION,
                 f"ReportReviewBatch.schema_version 必须为 {REPORT_REVIEW_RUN_SCHEMA_VERSION!r}")
        _require(self.scope_kind in RR.REPORT_REVIEW_SCOPE_KINDS,
                 f"未知 scope_kind：{self.scope_kind!r}")
        units = _sorted_unique(self.unit_keys, "ReportReviewBatch.unit_keys")
        _require(bool(units), "ReportReviewBatch.unit_keys 不得为空")
        _require(self.max_output_tokens == REPORT_REVIEW_MAX_OUTPUT_TOKENS,
                 f"ReportReviewBatch.max_output_tokens 必须为 {REPORT_REVIEW_MAX_OUTPUT_TOKENS}")
        _require(isinstance(self.payload, dict), "ReportReviewBatch.payload 必须是对象")
        want = _digest(self.payload)
        _require(self.request_fingerprint == want,
                 f"ReportReviewBatch.request_fingerprint 与 payload 不符：声明 "
                 f"{self.request_fingerprint!r}，应为 {want!r}")
        _require(self.char_count == len(_canonical(self.payload)),
                 "ReportReviewBatch.char_count 与 payload 实际字符数不符")
        _require(self.estimated_input_tokens == _estimated_input_tokens(self.char_count),
                 "ReportReviewBatch.estimated_input_tokens 与字符数不符")
        expected = AS.content_id("rrb_", self.identity_body())
        _require(self.batch_id == expected,
                 f"ReportReviewBatch.batch_id 与内容不符：声明 {self.batch_id!r}，"
                 f"应为 {expected!r}")

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "scope_id": self.scope_id,
            "scope_version": self.scope_version,
            "scope_kind": self.scope_kind,
            "unit_keys": list(self.unit_keys),
            "char_count": self.char_count,
            "estimated_input_tokens": self.estimated_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "request_fingerprint": self.request_fingerprint,
        }

    def summarize(self) -> dict:
        """可落盘摘要（**不含** payload——输入全文已在 `report_review_inputs.json` 里）。"""
        return {"batch_id": self.batch_id, "unit_count": len(self.unit_keys),
                "unit_keys": list(self.unit_keys), "char_count": self.char_count,
                "estimated_input_tokens": self.estimated_input_tokens,
                "max_output_tokens": self.max_output_tokens,
                "request_fingerprint": self.request_fingerprint}


def _make_batch(scope: RR.ReportReviewScope, unit_keys: Sequence[str]) -> ReportReviewBatch:
    payload = _slice_payload(scope, unit_keys)
    char_count = len(_canonical(payload))
    body = {
        "schema_version": REPORT_REVIEW_RUN_SCHEMA_VERSION,
        "scope_id": scope.scope_id,
        "scope_version": scope.scope_version,
        "scope_kind": scope.scope_kind,
        "unit_keys": sorted(str(k) for k in unit_keys),
        "char_count": char_count,
        "estimated_input_tokens": _estimated_input_tokens(char_count),
        "max_output_tokens": REPORT_REVIEW_MAX_OUTPUT_TOKENS,
        "request_fingerprint": _digest(payload),
    }
    return ReportReviewBatch(
        schema_version=body["schema_version"],
        batch_id=AS.content_id("rrb_", body),
        scope_id=scope.scope_id, scope_version=scope.scope_version,
        scope_kind=scope.scope_kind, unit_keys=tuple(body["unit_keys"]),
        char_count=char_count, estimated_input_tokens=body["estimated_input_tokens"],
        max_output_tokens=REPORT_REVIEW_MAX_OUTPUT_TOKENS,
        payload=payload, request_fingerprint=body["request_fingerprint"])


def plan_report_review_batches(
        scope: RR.ReportReviewScope, *,
        max_units: int = REPORT_REVIEW_MAX_UNITS_PER_CALL,
        max_chars: int = REPORT_REVIEW_MAX_INPUT_CHARS_PER_CALL,
) -> tuple[ReportReviewBatch, ...]:
    """把一份 scope 切成**确定、不重叠、恰好覆盖**的批次。

    贪心按 `covered_unit_ids` 的升序装填：**每加一个单元就真的把 payload 拼出来量一次**，
    超过任一上限就把这个单元留给下一批。用实际字符数而不是估算值，是因为"估算值小于上限、
    实际值超了"正是那种只在真实调用时才暴露的失败。

    单个单元**自身**就超上限时直接失败：那说明这个 scope 的输入面本身超出了已批准容量，
    需要的是重新设计输入，而不是悄悄把它截断、或让它单独成批去撞 provider 的边界。
    """
    _require(max_units > 0 and max_chars > 0, "批次上限必须为正")
    units = list(scope.covered_unit_ids)
    _require(bool(units), "scope 没有任何单元，无法分批")

    batches: list[ReportReviewBatch] = []
    current: list[str] = []
    for key in units:
        candidate = current + [key]
        batch = _make_batch(scope, candidate)
        if batch.char_count <= max_chars and len(candidate) <= max_units:
            current = candidate
            continue
        if not current:
            raise ReportReviewError(
                f"单元 {key!r} 自身就超过批次上限（字符 {batch.char_count} > {max_chars} 或"
                f"超出单元数上限）：输入面超出已批准容量，fail-closed（不得截断或丢单元）",
                reason="unit_exceeds_batch_capacity", unit_key=key)
        batches.append(_make_batch(scope, current))
        current = [key]
        single = _make_batch(scope, current)
        if single.char_count > max_chars:
            raise ReportReviewError(
                f"单元 {key!r} 自身就超过批次上限（字符 {single.char_count} > {max_chars}）："
                "输入面超出已批准容量，fail-closed（不得截断或丢单元）",
                reason="unit_exceeds_batch_capacity", unit_key=key)
    if current:
        batches.append(_make_batch(scope, current))

    covered = sorted(key for batch in batches for key in batch.unit_keys)
    _require(covered == sorted(units),
             f"{scope.scope_kind}：分批并集与 scope 单元集不一致（缺 "
             f"{sorted(set(units) - set(covered))}，多 {sorted(set(covered) - set(units))}）")
    _require(len({key for batch in batches for key in batch.unit_keys}) == len(covered),
             f"{scope.scope_kind}：分批出现重叠单元")
    return tuple(batches)


def report_review_structural_bound(scope: RR.ReportReviewScope) -> dict:
    """一个 scope 的**结构上界**：单元数、批数、最重一批的字符数与单元数。

    这是事前上界的**唯一推导处**——已批准的每次上限必须盖得住它，否则"上限低于上界"
    就是把一次注定中途撞门的运行伪装成可运行（与
    `run_m930_3_acceptance._assert_budget_covers_structural_bound` 同一纪律）。
    """
    batches = plan_report_review_batches(scope)
    return {"scope_kind": scope.scope_kind, "units": len(scope.covered_unit_ids),
            "batches": len(batches),
            "max_batch_chars": max(batch.char_count for batch in batches),
            "max_batch_units": max(len(batch.unit_keys) for batch in batches),
            "max_output_tokens": REPORT_REVIEW_MAX_OUTPUT_TOKENS}


def assert_within_capacity(scope: RR.ReportReviewScope,
                           batches: Sequence[ReportReviewBatch]) -> None:
    """**发请求之前**核对：每一批都在两类上限之内，且批次合起来覆盖整个 scope。"""
    covered: list[str] = []
    for batch in batches:
        _require(batch.scope_id == scope.scope_id and
                 batch.scope_version == scope.scope_version,
                 f"{batch.batch_id}：批次绑的不是本 scope")
        _require(len(batch.unit_keys) <= REPORT_REVIEW_MAX_UNITS_PER_CALL,
                 f"{batch.batch_id}：单元数 {len(batch.unit_keys)} 超过上限 "
                 f"{REPORT_REVIEW_MAX_UNITS_PER_CALL}")
        _require(batch.char_count <= REPORT_REVIEW_MAX_INPUT_CHARS_PER_CALL,
                 f"{batch.batch_id}：字符数 {batch.char_count} 超过上限 "
                 f"{REPORT_REVIEW_MAX_INPUT_CHARS_PER_CALL}")
        covered.extend(batch.unit_keys)
    _require(sorted(covered) == sorted(scope.covered_unit_ids),
             f"{scope.scope_kind}：批次并集与 scope 单元集不一致")
    _require(len(set(covered)) == len(covered), f"{scope.scope_kind}：批次重叠")


# ---------------------------------------------------------------------------
# prompt / 消息 / 客户端
# ---------------------------------------------------------------------------

def load_report_review_prompt() -> str:
    """加载 `llm/prompts/independent_report_reviewer_v1.txt`，**加载即对账**。

    与 `cited_review.load_cited_review_prompt` 同一纪律：资产头声明的 `(asset, revision)`
    必须与冻结常量逐字相同。改了修订号而头部没改（或反过来）时，账本上的 `prompt_version`
    将不对应任何字节——事后没有任何证据能证明当时发出去的提示词长什么样。
    """
    from llm import client as llm
    text = llm.load_prompt(REPORT_REVIEW_PROMPT_ASSET)
    declared = CW.declared_prompt_identity(text)
    _require(declared is not None,
             f"prompt 资产 {REPORT_REVIEW_PROMPT_ASSET!r} 第 1 行没有声明的 "
             f"`（<asset>，revision <rev>）`，无法与 {REPORT_REVIEW_PROMPT_VERSION} 对账",
             reason="prompt_asset_identity_missing")
    asset, revision = declared
    _require((asset, revision) == (REPORT_REVIEW_PROMPT_ASSET,
                                   REPORT_REVIEW_PROMPT_REVISION),
             f"prompt 资产自称 {asset}@{revision}，本链声明的是 "
             f"{REPORT_REVIEW_PROMPT_VERSION}：账本上的 prompt_version 将不对应任何字节",
             reason="prompt_asset_identity_mismatch")
    return text


def build_report_review_messages(batch: ReportReviewBatch) -> list[dict]:
    """批次 → **一段** user 消息（完整 JSON，字段序稳定）。"""
    return [{"role": "user",
             "content": json.dumps(batch.payload, ensure_ascii=False, sort_keys=True,
                                   indent=1)}]


@dataclass(frozen=True)
class ReportReviewResult:
    """一次调用的结构化结果（文本 + 调用元数据）。与 `CitedReviewResult` 同纪律。"""

    text: str
    call_id: str
    model: str
    prompt_version: str
    status: str = "ok"
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int = 0
    finish_reason: str | None = None
    error: str = ""

    def __post_init__(self) -> None:
        _require(self.status in ("ok", "error"),
                 f"ReportReviewResult.status={self.status!r} 只能是 'ok' / 'error'")
        for name in ("call_id", "model", "prompt_version"):
            _require(bool(str(getattr(self, name) or "")),
                     f"ReportReviewResult.{name} 不得为空（调用元数据必须完整）")
        _require(isinstance(self.text, str), "ReportReviewResult.text 必须是字符串")
        _require(self.status != "error" or bool(self.error),
                 "ReportReviewResult.status='error' 必须带 error 说明")

    @property
    def response_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        return {"call_id": self.call_id, "model": self.model,
                "prompt_version": self.prompt_version, "status": self.status,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "latency_ms": self.latency_ms, "finish_reason": self.finish_reason,
                "error": self.error, "response_hash": self.response_hash}


class ReportReviewClient(Protocol):
    """审阅链能拿到的全部外部能力。**没有**检索入口、没有工具入口、没有写入口。"""

    def review(self, *, messages: Sequence[Mapping[str, str]], system: str,
               prompt_version: str, model_policy: str) -> ReportReviewResult: ...


class LlmReportReviewClient:
    """把 `llm.client` 适配成 `ReportReviewClient`（不带工具、不重试、不缓存）。

    截断（`reject_truncated=True`）时**先记一条失败流水再抛**：一次真实发生、已经占掉额度的
    调用不得因抛异常而从流水里消失。记录形状复用 `cited_writer.truncated_call_record`
    （没有 `text` / `response_hash`——半截内容不是意见）。
    """

    def __init__(self, *, model: str | None = None,
                 max_tokens: int = REPORT_REVIEW_MAX_OUTPUT_TOKENS,
                 thinking: dict | None = None, reject_truncated: bool = True) -> None:
        self.model = model
        self.max_tokens = int(max_tokens)
        self.thinking = thinking
        self.reject_truncated = bool(reject_truncated)
        self.calls: list[dict] = []

    def review(self, *, messages: Sequence[Mapping[str, str]], system: str,
               prompt_version: str, model_policy: str) -> ReportReviewResult:
        from llm import client as llm  # 延迟导入：离线测试不必加载 provider 依赖

        try:
            resp = llm.chat_with_usage(list(messages), system=system, model=self.model,
                                       max_tokens=self.max_tokens,
                                       prompt_version=prompt_version, thinking=self.thinking,
                                       reject_truncated=self.reject_truncated)
        except llm.LLMTruncatedResponse as exc:
            self.calls.append(CW.truncated_call_record(
                exc, model=self.model, prompt_version=prompt_version,
                model_policy=model_policy))
            raise
        except Exception as exc:  # noqa: BLE001 - 任何 provider 失败都如实记为 error 结果
            record = {"call_id": "", "model": self.model or "", "status": "error",
                      "prompt_version": prompt_version,
                      "error": f"{type(exc).__name__}: {exc}"}
            self.calls.append(record)
            return ReportReviewResult(text="", call_id=self._fallback_call_id(),
                                      model=self.model or "", prompt_version=prompt_version,
                                      status="error", error=record["error"])
        result = ReportReviewResult(
            text=str(getattr(resp, "text", "") or ""),
            call_id=str(getattr(resp, "call_id", "") or "") or self._fallback_call_id(),
            model=str(getattr(resp, "model", "") or "") or (self.model or ""),
            prompt_version=prompt_version, status="ok",
            input_tokens=getattr(resp, "input_tokens", None),
            output_tokens=getattr(resp, "output_tokens", None),
            latency_ms=int(getattr(resp, "latency_ms", 0) or 0),
            finish_reason=getattr(resp, "finish_reason", None))
        self.calls.append({**result.to_dict(), "model_policy": model_policy})
        return result

    @staticmethod
    def _fallback_call_id() -> str:
        import uuid
        return f"local-{uuid.uuid4().hex[:16]}"


def report_review_producer_kind_of(client: Any) -> str:
    """**由 client 本身**推出产出者身份（唯一实现，调用方无从谎报）。

    规则与 A1 逐句审阅同一条：只有真正能发起调用的那个客户端对象，其意见才算
    `independent_llm_review`。让调用方用参数"声明"自己是哪种，等于给了把机械回声标成
    独立审阅的入口。
    """
    return ("independent_llm_review" if isinstance(client, LlmReportReviewClient)
            else "offline_diagnostic_echo")


# ---------------------------------------------------------------------------
# 解析：模型返回 → ReviewIssue[]（唯一一处把文本变成结构化意见）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedBatchReview:
    """一个批次的解析结果：本批提出的意见 + **本批放进请求的单元** + 响应指纹。"""

    issues: tuple[AS.ReviewIssue, ...]
    requested_unit_keys: tuple[str, ...]
    response_fingerprint: str

    def __post_init__(self) -> None:
        _sorted_unique(self.requested_unit_keys, "ParsedBatchReview.requested_unit_keys")
        _require(len(self.response_fingerprint) == 64
                 and all(c in "0123456789abcdef" for c in self.response_fingerprint),
                 "ParsedBatchReview.response_fingerprint 必须是 sha256 hex")


def _anchor_strings(payload: Any) -> set[str]:
    """请求面里**真的出现过**的字符串（任意深度）∪ 本批单元键。

    这是 `evidence_refs` 可回查的判据：一个出处能回查，当且仅当它（或它在 `#` 之前的部分）
    确实出现在这次投出去的请求里。不比对"看起来像不像坐标"——那种启发式既能放过伪造，
    又会拒掉真实但形状少见的定位串。
    """
    found: set[str] = set()
    stack: list[Any] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            # 存**去空白后**的串：调用方在比对前会 `strip()` 引用，若这里留着原样的
            # 前后空白，一个「确实出现在请求里、只是边上带空格」的定位会被判成伪造。
            text = node.strip()
            if len(text) >= 3:
                found.add(text)
        elif isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, (list, tuple)):
            stack.extend(node)
    return found


def _ref_is_traceable(ref: str, anchors: set[str]) -> bool:
    ref = ref.strip()
    if not ref:
        return False
    if ref in anchors:
        return True
    base, sep, _ = ref.partition("#")
    return bool(sep) and base.strip() in anchors


def _reason_key(reason: str) -> str:
    return " ".join(reason.split())


def parse_report_review(text: Any, *, scope: RR.ReportReviewScope,
                        batch: ReportReviewBatch) -> ParsedBatchReview:
    """模型返回的 JSON → 本批提出的意见（`rvi-1`）。

    六条**解析期**硬约束，任一不满足即 `ReportReviewError`：

    1. 顶层只能是 `{"issues": [...]}`；放行/改写类字段与未登记字段当场拒绝；
    2. **不要求逐单元表态**：`issues` 可以是空数组（本批没发现实质问题），也没有条数下限。
       覆盖由程序从批次推出（`ParsedBatchReview.requested_unit_keys`），不由模型回声证明；
    3. 每条意见的 `unit_ref` 必须**就是**本批、本 scope 里的那个单元
       （`unit_kind` + `unit_id` 全等，且在 `scope.covered_unit_ids` 内）；
    4. 每条意见的 `report_version` 必须等于本次 `scope.scope_version`；
    5. `blocking` 必须是 **JSON 布尔值**——`bool("false")` 在 Python 里是 `True`，
       字符串 `"false"` 一旦被强转就会把一个非阻断意见变成阻断意见；
    6. `evidence_refs` 每一项都必须能回查本次请求（见 `_anchor_strings`）——
       凭空写一个不存在的出处不得充当反证。
    另加：**重复问题拒绝**（同一单元 + 同一类别 + 同一理由）。
    """
    raw = str(text if text is not None else "").strip()
    _require(bool(raw), "审阅返回为空（没有可解析的意见）", reason="empty_response")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReportReviewError(
            f"审阅返回不是一个完整 JSON 值：{exc}", reason="response_not_json") from exc
    _require(isinstance(payload, dict), "审阅返回必须是对象", reason="response_not_object")
    hit = sorted(set(payload) & set(REPORT_REVIEW_FORBIDDEN_ENVELOPE_FIELDS))
    _require(not hit,
             f"审阅返回含放行/改写类字段 {hit}：审阅只提意见，不放行、不改稿",
             reason="reviewer_release_attempt")
    # 冻结 wire 自己的校验抛的是 `AssuranceSchemaError`。若让它原样逃出去，
    # `run_report_review` 只接 `ReportReviewError` ⇒ 这一批不会变成 `ReportReviewRunAborted`，
    # 「失败留存」那条路整段失效，读者看到的是一个陌生异常而不是"哪几批跑了、哪几批没跑"。
    # 因此凡是**本函数自己**在做信封/意见行校验的地方，都把 schema 错误归一成本链的错误类型。
    try:
        payload = AS._reject_unknown(payload, {"issues"}, "报告级审阅返回")
    except AS.AssuranceSchemaError as exc:
        raise ReportReviewError(str(exc), reason="response_envelope_invalid") from exc

    declared = set(scope.covered_unit_ids)
    by_key = {unit.key: unit for unit in batch_unit_refs(scope, batch)}
    anchors = _anchor_strings(batch.payload) | set(batch.unit_keys)
    seen: list[tuple[str, str, str]] = []
    issues: list[AS.ReviewIssue] = []
    for row in (payload.get("issues") or ()):
        # 顺序与 `AS.ReviewIssue.from_dict` 一致：先指名拒禁列，再拒未登记字段。反过来的话
        # `{"category": ..., "rewritten_text": ...}` 会以「含未登记字段」被拒，读者看不到
        # 真正的理由（它在试图改写正文）。
        try:
            row = AS._reject_forbidden(row, AS.REVIEWER_FORBIDDEN_FIELDS, "报告级审阅意见")
            row = AS._reject_unknown(
                row, {"unit_kind", "unit_id", "category", "severity", "blocking", "reason",
                      "evidence_refs", "suggested_target"}, "报告级审阅意见")
        except AS.AssuranceSchemaError as exc:
            raise ReportReviewError(str(exc), reason="issue_row_invalid") from exc
        unit_kind = str(row.get("unit_kind") or "")
        unit_id = str(row.get("unit_id") or "")
        _require(bool(unit_kind) and bool(unit_id),
                 "报告级审阅意见必须给出 unit_kind 与 unit_id",
                 reason="issue_unit_missing")
        key = AS.unit_ref_key(unit_kind, unit_id)
        _require(key in by_key,
                 f"审阅意见指向的本批单元 {key!r} 不在本批里（本批 {sorted(by_key)}）",
                 reason="issue_unit_not_in_batch", unit_key=key)
        _require(key in declared,
                 f"审阅意见指向的单元 {key!r} 不在本 scope 声明的单元集里",
                 reason="issue_unit_not_in_scope", unit_key=key)
        target = row.get("suggested_target")
        blocking = row.get("blocking")
        _require(isinstance(blocking, bool),
                 f"审阅意见的 blocking 必须是 JSON 布尔值 true/false，得到 "
                 f"{type(blocking).__name__} {blocking!r}——"
                 "字符串 \"false\" 经 bool() 会变成 True，因此不做任何强转",
                 reason="issue_blocking_not_bool", unit_key=key)
        refs = tuple(str(x).strip() for x in (row.get("evidence_refs") or ()))
        unknown = sorted({ref for ref in refs if not _ref_is_traceable(ref, anchors)})
        _require(not unknown,
                 f"审阅意见给出了本次请求里不存在的出处 {unknown}："
                 "出处必须能回查本次投出去的请求，凭空写的定位不得充当反证",
                 reason="issue_evidence_ref_unknown", unit_key=key)
        category = str(row.get("category") or "")
        reason_text = str(row.get("reason") or "")
        try:
            issue = AS.ReviewIssue.create(
                report_version=scope.scope_version,
                unit_ref=by_key[key],
                category=category,
                severity=str(row.get("severity") or ""),
                blocking=blocking,
                reason=reason_text,
                evidence_refs=refs,
                suggested_target=(None if target is None
                                  else AS.SuggestedTarget.from_dict(target)),
                schema_version=AS.REVIEW_ISSUE_SCHEMA_VERSION_V1)
        except AS.AssuranceSchemaError as exc:
            # 非法类别/严重度/目标类型等：由冻结 wire 判，理由照实转述（含可执行替代项）
            raise ReportReviewError(str(exc), reason="issue_schema_invalid",
                                    unit_key=key) from exc
        seen.append((key, category, _reason_key(reason_text)))
        issues.append(issue)

    duplicated = sorted({sig for sig in seen if seen.count(sig) > 1})
    _require(not duplicated,
             f"同一批里出现重复问题 {[(k, c) for k, c, _ in duplicated]}："
             "同一单元 + 同一类别 + 同一理由只能提一次（重复问题拒绝）",
             reason="issue_duplicate_finding")

    issues.sort(key=lambda issue: issue.issue_id)
    return ParsedBatchReview(issues=tuple(issues),
                             requested_unit_keys=tuple(sorted(batch.unit_keys)),
                             response_fingerprint=hashlib.sha256(
                                 raw.encode("utf-8")).hexdigest())


def batch_unit_refs(scope: RR.ReportReviewScope,
                    batch: ReportReviewBatch) -> tuple[AS.ReviewUnitRef, ...]:
    """本批单元在 **bundle** 里的类型化引用（`unit_kind` 必须来自 bundle，不是猜的）。"""
    wanted = set(batch.unit_keys)
    refs = tuple(unit for unit in scope.bundle.unit_inventory if unit.key in wanted)
    _require(len(refs) == len(wanted),
             f"{scope.scope_kind}：本批单元在 bundle 的 unit_inventory 里找不全")
    return refs


# ---------------------------------------------------------------------------
# 结果对象与持久化
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReportReviewRun:
    """一块报告级内容的一次审阅运行：**三本账** + 逐批记录 + 全部意见。

    它**没有**任何"通过/放行/可发布"字段，这是刻意的：审阅完成只是"读者看到了独立意见"，
    发布还要走系统放行与人工接受两条各自独立的路。聚合与"机械硬错误优先"由
    `assurance.cited_controller` 做，本对象只如实描述"谁看了什么、说了什么"。

    **三本账互不推导**（`rrq-2`）：

    - `requested_unit_ids`：确实**放进过请求**的成员（含失败批）；
    - `covered_unit_ids`：进了**得到可解析回复**的批次的成员；
    - `reported_unit_ids`：模型**实际提出问题**的成员。

    `covered_unit_ids` **不**能读成"逐份材料被核实过"——本对象里没有任何字段表达那件事。
    `records` 只对应**已完成**的批，因此 `outcome="failed"` 时 `records` 里全是
    `outcome="reviewed"` 的记录（失败批不会伪装成一条记录混进来）。
    """

    schema_version: str
    run_id: str
    policy_version: str
    report_id: str
    scope_kind: str
    scope_id: str
    scope_version: str
    bundle_id: str
    prompt_version: str
    model_policy_id: str
    producer_kind: str
    outcome: str
    requested_batch_ids: tuple[str, ...]
    completed_batch_ids: tuple[str, ...]
    failed_batch_ids: tuple[str, ...]
    declared_unit_ids: tuple[str, ...]
    requested_unit_ids: tuple[str, ...]
    covered_unit_ids: tuple[str, ...]
    reported_unit_ids: tuple[str, ...]
    excluded_unit_ids: tuple[str, ...]
    records: tuple[AS.ReviewerRunRecord, ...]
    issues: tuple[AS.ReviewIssue, ...]

    def __post_init__(self) -> None:
        _require(self.schema_version == REPORT_REVIEW_RUN_SCHEMA_VERSION,
                 f"ReportReviewRun.schema_version 必须为 {REPORT_REVIEW_RUN_SCHEMA_VERSION!r}")
        _require(self.policy_version == REPORT_REVIEW_CHAIN_POLICY_VERSION,
                 f"ReportReviewRun.policy_version 必须为 {REPORT_REVIEW_CHAIN_POLICY_VERSION!r}")
        _require(self.scope_kind in RR.REPORT_REVIEW_SCOPE_KINDS,
                 f"未知 scope_kind：{self.scope_kind!r}")
        _require(self.producer_kind in REPORT_REVIEW_PRODUCER_KINDS,
                 f"未知 producer_kind：{self.producer_kind!r}"
                 "（这份意见是谁给的必须写清楚，否则下游分不清独立审阅与离线回声）")
        _require(self.outcome in AS.REVIEWER_OUTCOMES,
                 f"未知 outcome：{self.outcome!r}")
        for name in ("report_id", "scope_id", "scope_version", "bundle_id", "prompt_version",
                     "model_policy_id"):
            _require(bool(str(getattr(self, name) or "").strip()),
                     f"ReportReviewRun.{name} 必须非空")
        _require(self.prompt_version == REPORT_REVIEW_PROMPT_VERSION,
                 f"ReportReviewRun.prompt_version 必须为 {REPORT_REVIEW_PROMPT_VERSION!r}")

        for name in ("requested_batch_ids", "completed_batch_ids", "failed_batch_ids",
                     "declared_unit_ids", "requested_unit_ids",
                     "covered_unit_ids", "reported_unit_ids", "excluded_unit_ids"):
            values = _sorted_unique(getattr(self, name), f"ReportReviewRun.{name}")
            object.__setattr__(self, name, values)

        records = tuple(self.records or ())
        issues = tuple(self.issues or ())
        for record in records:
            _require(isinstance(record, AS.ReviewerRunRecord),
                     "ReportReviewRun.records 只能是 ReviewerRunRecord")
            _require(record.outcome == "reviewed" and record.called,
                     f"记录 {record.record_id!r} 不是一次已完成的调用："
                     "records 只装**收到可解析回复**的批，失败批不得伪装成记录")
            _require(record.report_version == self.scope_version
                     and record.bundle_id == self.bundle_id
                     and record.prompt_version == self.prompt_version
                     and record.model_policy_id == self.model_policy_id,
                     f"记录 {record.record_id!r} 绑的不是本次 scope/输入包/提示词/模型"
                     "（审阅记录必须绑在真实版本上）")
        for issue in issues:
            _require(isinstance(issue, AS.ReviewIssue),
                     "ReportReviewRun.issues 只能是 ReviewIssue")
            _require(issue.report_version == self.scope_version,
                     f"意见 {issue.issue_id!r} 绑的 report_version 不是本次 scope_version")
            _require(issue.unit_ref.key in self.declared_unit_ids,
                     f"意见 {issue.issue_id!r} 指向的单元不在本 scope 声明的单元集里")
        record_ids = [r.record_id for r in records]
        issue_ids = [i.issue_id for i in issues]
        _require(len(set(record_ids)) == len(record_ids), "ReportReviewRun 含重复记录")
        _require(len(set(issue_ids)) == len(issue_ids), "ReportReviewRun 含重复意见")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "issues", issues)

        # ---- 批次账：已尝试 / 已完成 / 失败三本各自成立，且互相可推 ----
        _require(set(self.completed_batch_ids) <= set(self.requested_batch_ids),
                 "ReportReviewRun：已完成的批必须都是已尝试过的批")
        # 两侧都取 `tuple`：`_sorted_unique` 把字段归一成元组，右式若留成 `list` 就会
        # 「恒不等」——那不是更严格，是把每一份失败 run 都判成非法。
        _require(self.failed_batch_ids == tuple(sorted(set(self.requested_batch_ids)
                                                       - set(self.completed_batch_ids))),
                 "ReportReviewRun.failed_batch_ids 必须恰好等于 已尝试 − 已完成")
        _require(len(record_ids) == len(self.completed_batch_ids),
                 f"ReportReviewRun：记录数 {len(record_ids)} 与本批已完成批数 "
                 f"{len(self.completed_batch_ids)} 不符（每个已完成批恰好一条记录）")

        # ---- 单元账：放进请求 / 收到可解析回复 / 模型提出问题 ----
        union_requested = sorted({key for record in records
                                  for key in record.covered_unit_keys})
        _require(union_requested == list(self.covered_unit_ids),
                 f"ReportReviewRun.covered_unit_ids 与已完成批的请求单元之和不符："
                 f"{list(self.covered_unit_ids)} != {union_requested}")
        _require(set(self.covered_unit_ids) <= set(self.requested_unit_ids),
                 "ReportReviewRun：进了请求不等于收到回复——covered 必须是 requested 的子集")
        _require(set(self.reported_unit_ids) <= set(self.covered_unit_ids),
                 "ReportReviewRun：模型不可能对没进过请求的单元提意见（reported ⊆ covered）")
        declared_issue_ids = _sorted_unique(
            sorted(i for record in records for i in record.issue_ids),
            "逐批记录声明的 issue_ids")
        _require(sorted(issue_ids) == list(declared_issue_ids),
                 "ReportReviewRun：意见集与逐批记录声明的意见集不符")
        _require(set(self.reported_unit_ids) == {issue.unit_ref.key for issue in issues},
                 "ReportReviewRun.reported_unit_ids 与意见指向的单元集不符")
        _require(sorted(set(self.excluded_unit_ids) | set(self.covered_unit_ids))
                 == list(self.declared_unit_ids)
                 and not (set(self.excluded_unit_ids) & set(self.covered_unit_ids)),
                 "ReportReviewRun：covered ∪ excluded 必须恰好等于 declared 且互不相交")
        if self.outcome == "reviewed":
            _require(not self.excluded_unit_ids,
                     "ReportReviewRun：outcome='reviewed' 时不得有未覆盖的单元")
            _require(not self.failed_batch_ids,
                     "ReportReviewRun：outcome='reviewed' 时不得有失败的批")
        else:
            _require(bool(self.failed_batch_ids or not self.requested_batch_ids),
                     f"ReportReviewRun：outcome={self.outcome!r} 时必须有失败的批"
                     " 或根本没有试过任何批")

        expected = AS.content_id("rrq_", self.identity_body())
        _require(self.run_id == expected,
                 f"ReportReviewRun.run_id 与内容不符：声明 {self.run_id!r}，应为 {expected!r}")

    @property
    def trusted_as_assurance_input(self) -> bool:
        """只有真正由独立模型跑出来的意见才算审阅输入；离线回声只证明接线。"""
        return self.producer_kind == "independent_llm_review" and self.outcome == "reviewed"

    def identity_body(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "report_id": self.report_id,
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
            "scope_version": self.scope_version,
            "bundle_id": self.bundle_id,
            "prompt_version": self.prompt_version,
            "model_policy_id": self.model_policy_id,
            "producer_kind": self.producer_kind,
            "outcome": self.outcome,
            "requested_batch_ids": list(self.requested_batch_ids),
            "completed_batch_ids": list(self.completed_batch_ids),
            "failed_batch_ids": list(self.failed_batch_ids),
            "declared_unit_ids": list(self.declared_unit_ids),
            "requested_unit_ids": list(self.requested_unit_ids),
            "covered_unit_ids": list(self.covered_unit_ids),
            "reported_unit_ids": list(self.reported_unit_ids),
            "excluded_unit_ids": list(self.excluded_unit_ids),
            "record_ids": [record.record_id for record in self.records],
            "issue_ids": [issue.issue_id for issue in self.issues],
        }

    def to_dict(self) -> dict:
        """线形状**就是**这些字段——刻意不用 `**identity_body()`：身份体里有
        `record_ids` / `issue_ids` 两个**派生**键，它们进 wire 就会变成「同一件事有两处说法」，
        而 `from_dict` 严格的字段集校验会（正确地）把这种 payload 拒掉。"""
        return {"run_id": self.run_id, "schema_version": self.schema_version,
                "policy_version": self.policy_version, "report_id": self.report_id,
                "scope_kind": self.scope_kind,
                "scope_id": self.scope_id, "scope_version": self.scope_version,
                "bundle_id": self.bundle_id, "prompt_version": self.prompt_version,
                "model_policy_id": self.model_policy_id, "producer_kind": self.producer_kind,
                "outcome": self.outcome,
                "requested_batch_ids": list(self.requested_batch_ids),
                "completed_batch_ids": list(self.completed_batch_ids),
                "failed_batch_ids": list(self.failed_batch_ids),
                "declared_unit_ids": list(self.declared_unit_ids),
                "requested_unit_ids": list(self.requested_unit_ids),
                "covered_unit_ids": list(self.covered_unit_ids),
                "reported_unit_ids": list(self.reported_unit_ids),
                "excluded_unit_ids": list(self.excluded_unit_ids),
                "records": [record.to_dict() for record in self.records],
                "issues": [issue.to_dict() for issue in self.issues]}

    @classmethod
    def create(cls, *, report_id: str, scope_kind: str, scope_id: str, scope_version: str,
               bundle_id: str, model_policy_id: str, producer_kind: str, outcome: str,
               requested_batch_ids: Sequence[str], completed_batch_ids: Sequence[str],
               declared_unit_ids: Sequence[str],
               requested_unit_ids: Sequence[str], covered_unit_ids: Sequence[str],
               reported_unit_ids: Sequence[str], excluded_unit_ids: Sequence[str],
               records: Sequence[AS.ReviewerRunRecord],
               issues: Sequence[AS.ReviewIssue]) -> "ReportReviewRun":
        requested = sorted(str(v) for v in requested_batch_ids)
        completed = sorted(str(v) for v in completed_batch_ids)
        body = {
            "schema_version": REPORT_REVIEW_RUN_SCHEMA_VERSION,
            "policy_version": REPORT_REVIEW_CHAIN_POLICY_VERSION,
            "report_id": report_id,
            "scope_kind": scope_kind, "scope_id": scope_id,
            "scope_version": scope_version, "bundle_id": bundle_id,
            "prompt_version": REPORT_REVIEW_PROMPT_VERSION,
            "model_policy_id": model_policy_id, "producer_kind": producer_kind,
            "outcome": outcome,
            "requested_batch_ids": requested,
            "completed_batch_ids": completed,
            "failed_batch_ids": sorted(set(requested) - set(completed)),
            "declared_unit_ids": sorted(str(v) for v in declared_unit_ids),
            "requested_unit_ids": sorted(str(v) for v in requested_unit_ids),
            "covered_unit_ids": sorted(str(v) for v in covered_unit_ids),
            "reported_unit_ids": sorted(str(v) for v in reported_unit_ids),
            "excluded_unit_ids": sorted(str(v) for v in excluded_unit_ids),
            "record_ids": [r.record_id for r in records],
            "issue_ids": [i.issue_id for i in issues],
        }
        return cls(run_id=AS.content_id("rrq_", body), schema_version=body["schema_version"],
                   policy_version=body["policy_version"], report_id=report_id,
                   scope_kind=scope_kind,
                   scope_id=scope_id, scope_version=scope_version, bundle_id=bundle_id,
                   prompt_version=body["prompt_version"],
                   model_policy_id=model_policy_id, producer_kind=producer_kind,
                   outcome=outcome,
                   requested_batch_ids=tuple(body["requested_batch_ids"]),
                   completed_batch_ids=tuple(body["completed_batch_ids"]),
                   failed_batch_ids=tuple(body["failed_batch_ids"]),
                   declared_unit_ids=tuple(body["declared_unit_ids"]),
                   requested_unit_ids=tuple(body["requested_unit_ids"]),
                   covered_unit_ids=tuple(body["covered_unit_ids"]),
                   reported_unit_ids=tuple(body["reported_unit_ids"]),
                   excluded_unit_ids=tuple(body["excluded_unit_ids"]),
                   records=tuple(records), issues=tuple(issues))

    @classmethod
    def from_dict(cls, d: Any) -> "ReportReviewRun":
        fields = set(cls.__dataclass_fields__) | {"run_id"}
        _require(isinstance(d, dict) and set(d) == fields,
                 "ReportReviewRun 字段集不合约")
        return cls(
            schema_version=str(d["schema_version"]), run_id=str(d["run_id"]),
            policy_version=str(d["policy_version"]), report_id=str(d["report_id"]),
            scope_kind=str(d["scope_kind"]),
            scope_id=str(d["scope_id"]), scope_version=str(d["scope_version"]),
            bundle_id=str(d["bundle_id"]), prompt_version=str(d["prompt_version"]),
            model_policy_id=str(d["model_policy_id"]), producer_kind=str(d["producer_kind"]),
            outcome=str(d["outcome"]),
            requested_batch_ids=tuple(d["requested_batch_ids"]),
            completed_batch_ids=tuple(d["completed_batch_ids"]),
            failed_batch_ids=tuple(d["failed_batch_ids"]),
            declared_unit_ids=tuple(d["declared_unit_ids"]),
            requested_unit_ids=tuple(d["requested_unit_ids"]),
            covered_unit_ids=tuple(d["covered_unit_ids"]),
            reported_unit_ids=tuple(d["reported_unit_ids"]),
            excluded_unit_ids=tuple(d["excluded_unit_ids"]),
            records=tuple(AS.ReviewerRunRecord.from_dict(row) for row in d["records"]),
            issues=tuple(AS.ReviewIssue.from_dict(row) for row in d["issues"]))


def write_report_review_run(out_dir: Path, run: ReportReviewRun) -> Path:
    """**create-only** 落盘（`x`）：已存在即失败，绝不覆盖已发生的调用记录。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"report_review_run__{run.scope_kind}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(run.to_dict(), handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
    return path


def load_report_review_run(path: Path) -> ReportReviewRun:
    """读回一份已持久化的运行，并**逐字段复验身份**（wire 类型在构造期自校验）。"""
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReportReviewError(f"无法读取报告级审阅记录 {path}：{exc}",
                                reason="run_record_unreadable") from exc
    try:
        return ReportReviewRun.from_dict(value)
    except (AS.AssuranceSchemaError, ReportReviewError, KeyError, TypeError) as exc:
        raise ReportReviewError(f"报告级审阅记录 {path} 身份校验失败：{exc}",
                                reason="run_record_identity_invalid") from exc


# ---------------------------------------------------------------------------
# 编排：本模块**不**自己调用模型之外的东西（client 由调用方注入）
# ---------------------------------------------------------------------------

def run_report_review(*, scope: RR.ReportReviewScope, client: ReportReviewClient,
                      model_policy: str,
                      batches: Sequence[ReportReviewBatch] | None = None,
                      system: str | None = None) -> ReportReviewRun:
    """一块 scope 的**唯一**执行入口：分批 → 预检 → 逐批调用 → 解析 → 覆盖账。

    调用方必须自己**已经**装好事前预算门（见 `report_review_call_budget_policy`）：
    本函数不装门，也不替调用方声明授权。产出者身份由 `client` 本身推出
    （`report_review_producer_kind_of`），调用方没有声明它的入口。

    任一批失败即抛出 `ReportReviewRunAborted`，**并带上已尝试的批、已完成的批记录与意见**
    ——一次真实调用占掉的额度不得因为后面的批次失败而消失。

    本函数**不修改**任何输入、**不写**任何产物、**不**产生放行状态。
    """
    planned = tuple(batches) if batches is not None else plan_report_review_batches(scope)
    assert_within_capacity(scope, planned)
    producer_kind = report_review_producer_kind_of(client)
    system_text = system if system is not None else load_report_review_prompt()

    records: list[AS.ReviewerRunRecord] = []
    issues: list[AS.ReviewIssue] = []
    attempted: list[str] = []
    attempted_units: list[str] = []

    def _abort(message: str, *, exc: BaseException, reason: str) -> ReportReviewRunAborted:
        return ReportReviewRunAborted(
            message, cause=exc, scope_kind=scope.scope_kind, producer_kind=producer_kind,
            partial_records=records, partial_issues=issues,
            attempted_batch_ids=attempted, attempted_unit_ids=attempted_units,
            reason=reason)

    for batch in planned:
        attempted.append(batch.batch_id)
        attempted_units.extend(batch.unit_keys)
        if isinstance(client, LlmReportReviewClient) \
                and client.max_tokens != batch.max_output_tokens:
            client.max_tokens = batch.max_output_tokens
        try:
            result = client.review(
                messages=build_report_review_messages(batch), system=system_text,
                prompt_version=REPORT_REVIEW_PROMPT_VERSION, model_policy=model_policy)
        except BaseException as exc:  # noqa: BLE001 - 失败也要留下已尝试/已完成的账
            raise _abort(
                f"{scope.scope_kind}：第 {len(attempted)} 批调用失败（{batch.batch_id}）："
                f"{type(exc).__name__}: {exc}", exc=exc, reason="batch_failed") from exc
        if result.status != "ok":
            raise _abort(
                f"{scope.scope_kind}：第 {len(attempted)} 批调用未成功"
                f"（{batch.batch_id}）：{result.error or result.status}",
                exc=ReportReviewError(result.error or result.status,
                                      reason="review_call_failed"),
                reason="review_call_failed")
        try:
            parsed = parse_report_review(result.text, scope=scope, batch=batch)
        except ReportReviewError as exc:
            raise _abort(
                f"{scope.scope_kind}：第 {len(attempted)} 批解析失败"
                f"（{batch.batch_id}）：{exc}", exc=exc, reason="parse_failed") from exc
        records.append(AS.ReviewerRunRecord.create(
            report_version=scope.scope_version, bundle_id=scope.bundle.bundle_id,
            prompt_version=REPORT_REVIEW_PROMPT_VERSION, model_policy_id=model_policy,
            outcome="reviewed", covered_unit_keys=parsed.requested_unit_keys,
            issue_ids=(issue.issue_id for issue in parsed.issues),
            response_fingerprint=parsed.response_fingerprint))
        issues.extend(parsed.issues)

    return _assembled_run(
        scope=scope, report_id=scope.bundle.report_id, model_policy=model_policy,
        producer_kind=producer_kind, outcome="reviewed",
        requested_batch_ids=attempted, completed_batch_ids=attempted,
        requested_unit_ids=attempted_units, records=records, issues=issues)


def _assembled_run(*, scope: RR.ReportReviewScope, report_id: str, model_policy: str,
                   producer_kind: str, outcome: str,
                   requested_batch_ids: Sequence[str],
                   completed_batch_ids: Sequence[str],
                   requested_unit_ids: Sequence[str],
                   records: Sequence[AS.ReviewerRunRecord],
                   issues: Sequence[AS.ReviewIssue]) -> ReportReviewRun:
    """批次账/单元账的**唯一**装配处：成功路径与失败路径共用，避免两处各推一遍。"""
    declared = list(scope.covered_unit_ids)
    covered = sorted({key for record in records for key in record.covered_unit_keys})
    return ReportReviewRun.create(
        report_id=report_id, scope_kind=scope.scope_kind, scope_id=scope.scope_id,
        scope_version=scope.scope_version, bundle_id=scope.bundle.bundle_id,
        model_policy_id=model_policy, producer_kind=producer_kind, outcome=outcome,
        requested_batch_ids=requested_batch_ids,
        completed_batch_ids=completed_batch_ids,
        declared_unit_ids=declared,
        requested_unit_ids=requested_unit_ids,
        covered_unit_ids=covered,
        reported_unit_ids={issue.unit_ref.key for issue in issues},
        excluded_unit_ids=sorted(set(declared) - set(covered)),
        records=records, issues=issues)


def aborted_report_review_run(*, scope: RR.ReportReviewScope, aborted: ReportReviewRunAborted,
                              model_policy: str) -> ReportReviewRun:
    """把一次中止**如实落成** `ReportReviewRun(outcome="failed")`。

    已完成的批次照样带着自己的记录与意见；已尝试但未完成的批记进 `failed_batch_ids`；
    未覆盖的单元逐条记进 `excluded_unit_ids`。这就是"失败留存"：读者能看到"哪几批跑了、
    哪几批没跑"，而不是只剩一句"失败了"。

    产出者身份取自异常本身（`ReportReviewRunAborted.producer_kind`，在 raise 现场由
    client 推出），调用方**没有**声明它的入口。
    """
    _require(isinstance(aborted, ReportReviewRunAborted),
             "aborted_report_review_run 需要 ReportReviewRunAborted")
    return _assembled_run(
        scope=scope, report_id=scope.bundle.report_id, model_policy=model_policy,
        producer_kind=aborted.producer_kind, outcome="failed",
        requested_batch_ids=aborted.attempted_batch_ids,
        # 已完成 = 已尝试 − 失败的批；失败批的 id 由异常自己记着，这里从记录数反推不够
        # ——因此异常同时带 `attempted_batch_ids` 与已完成批记录，两者相减才是失败批。
        completed_batch_ids=aborted.completed_batch_ids,
        requested_unit_ids=aborted.attempted_unit_ids,
        records=aborted.partial_records, issues=aborted.partial_issues)


# ---------------------------------------------------------------------------
# 事前调用预算：**复用** llm.budget，不新建第二套账本
# ---------------------------------------------------------------------------

def report_review_call_budget_policy(*, bounds: Mapping[str, Mapping[str, int]],
                                     model: str, approved: bool,
                                     provenance: str) -> LB.CallBudgetPolicy:
    """报告级审阅的**事前**调用预算政策（一条独立轴，不并入任何写作/研究轴）。

    `bounds` 是逐 scope 的结构上界（`report_review_structural_bound` 的产物），
    每个 scope 的允许次数**由它推出来**，不是拍一个数。

    `approved=False` 时这份政策**装不上强制门**（`LLMCallBudget(enforce=True)` 在构造期
    就 `ValueError`）——"没授权就发不出请求"因此在结构上成立，不靠调用方自觉。
    `provenance` 逐字引用批准出处，写进 `AxisCap.basis`：读产物的人不必去翻对话记录。

    **两个读产物时要知道的细节。** (1) 这条轴的记账走 `llm.budget.reserve` 里那个
    "研究式"分支（逐 scope 上限 + 整轴上限），因此被拒时 `refusal.limit_kind` 印的是
    「本 topic 上限」——那是共享分支的措辞，权威的轴名在 `refusal.axis`（= `report_review`）。
    (2) 报告级审阅用**自己的** `LLMCallBudget` 实例，绝不并进写作轮的账本：合并会让
    「这一节花了多少次写作调用」被审阅调用悄悄改写。
    """
    per_scope = {f"{REPORT_REVIEW_SCOPE_PREFIX}{kind}": max(int(bound["batches"]), 1)
                 for kind, bound in sorted(bounds.items())}
    total = sum(per_scope.values())
    return LB.CallBudgetPolicy(
        policy_version="rrcb-2",
        approved_model=model if approved else None,
        categories=(),
        prompt_versions={REPORT_REVIEW_PROMPT_VERSION: REPORT_REVIEW_BUDGET_CATEGORY},
        axes=(LB.AxisCap(axis=REPORT_REVIEW_BUDGET_AXIS, max_attempts_per_scope=None,
                         max_attempts=total, approved=bool(approved), basis=provenance,
                         per_scope_max_attempts=per_scope),),
        category_axis={REPORT_REVIEW_BUDGET_CATEGORY: REPORT_REVIEW_BUDGET_AXIS},
        total_max_attempts=None, total_approved=bool(approved))


def assert_budget_covers_bounds(policy: LB.CallBudgetPolicy,
                                bounds: Mapping[str, Mapping[str, int]]) -> None:
    """**事前**核对：已授权的逐 scope 上限必须盖得住结构上界，否则拒绝整轮。

    与 `_assert_budget_covers_structural_bound` 同一条理由：上限低于上界不是"更严格"，
    而是"把一次注定中途撞门的运行伪装成可运行"——真实请求已经发出去、已批准的额度
    已经花掉，才在某一批上撞住。
    """
    cap = policy.axis_cap_for(REPORT_REVIEW_BUDGET_AXIS)
    problems: list[str] = []
    for kind, bound in sorted(bounds.items()):
        scope = f"{REPORT_REVIEW_SCOPE_PREFIX}{kind}"
        limit = cap.limit_for_scope(scope)
        if limit is None or int(limit) < int(bound["batches"]):
            problems.append(
                f"{kind}：已授权 {limit} 次 < 结构上界 {bound['batches']} 批"
                f"（{bound['units']} 单元、最重一批 {bound['max_batch_chars']} 字符）")
    needed = sum(max(int(bound["batches"]), 1) for bound in bounds.values())
    if cap.max_attempts is None or int(cap.max_attempts) < needed:
        problems.append(f"整轴已授权 {cap.max_attempts} 次 < 结构上界 {needed} 批（各 scope 之和）")
    _require(not problems,
             "报告级审阅的已授权上限盖不住结构上界：" + "；".join(problems),
             reason="budget_below_structural_bound")

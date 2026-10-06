"""Eval: 独立审阅失败在**运行入口**上的复验（本批两处窄项修复的验收）。

用法: python -m evals.test_m930_3_cited_review_failure_entry

本批修的两处漏项（都在「真实 Writer 已写出草稿之后、独立审阅失败」这条路径上）
--------------------------------------------------------------------------------
1. **截断没在审阅边界被接住**：`LlmCitedReviewClient` 在响应被截断时记一条失败流水并重抛
   `LLMTruncatedResponse`，而 `scripts/run_m930_3_cited_chain.py` 的审阅边界只接
   `CitedReviewError` / `AssuranceSchemaError`。于是 provider 端的一次截断把整轮掀翻：草稿、
   逐句机械核对、缺口与降级诊断预览**全部消失**——真实 r28 的失败方向正是这个（失败得太彻底，
   人读出口一节不剩）。修法：**只在审阅边界**接住截断，归成既有 typed `review_call_failed`，
   随后照既有路径落盘；不重试、不提额度、不改写作侧的截断行为。
2. **解码失败的原因码根本没被映射到**：`parse_cited_review` 抛的 reason 是
   `response_not_json`，运行入口的映射键写的却是 `review_reply_not_json`，于是「回复根本不是
   一个 JSON」（下一步动作：改提示词）被记成「回复是 JSON 但不合约」（下一步动作：改协议）——
   两件不同的事，读数上长得一样。修法：把键改成解析口**实际抛出的那个字符串**。

为什么必须验到**运行入口**
--------------------------
上面两条都是**边界接线**缺陷：单测函数组合（映射表、客户端各测各的）全绿也可能接线是错的——
本批的第 2 项本身就是「两侧名字相近、各自都对、接起来错」的例子。因此本模块让**同一条
`run()`** 先真的取得 Writer 草稿，再触发审阅失败，然后逐项读**落盘产物**与**进程退出码**。

三层证据，逐层说清各自证明什么（**不**合并成一句「通过了」）
------------------------------------------------------------
* **§1 正例**（`run()`，审阅正常完成）：证明正常路径没被本批改坏，且离线正例**零 provider
  调用**（临时 `logs/llm` 无新增、生产 `_NetworkGuard` 全程未被触碰）。
* **§2 两个真实失败对象**：用**真实** `LlmCitedReviewClient` ＋ **真实** `llm.chat_with_usage`
  ＋ **真实** `cited-budget-2` 门 ＋ **真实** `review_cited_prose` ＋ **真实**草稿（取自 §1），
  分别产出一个截断异常与一个非 JSON 异常。这一段给出「这次调用**已占额**」与「同一个
  `call_id`」的读数——离线模式本身没有真实账本（`cited_call_ledger.json` 里 `ledger=null`
  是设计，不是缺陷），所以「占额」这条**只能**在这里判，不能拿离线运行的账本冒充。
* **§3 两个反例**（`run()`，审阅失败）：把 §2 里**真实产出的那两个异常对象原样**交给审阅替身
  抛出——穿进审阅边界的对象就是真实客户端／真实解析器产出的那一个，**边界 `except` 元组里的
  类型因此由真对象命中，不必在测试里重抄一份类型清单**（重抄会随源码漂移，而漂移正是第 2 项
  漏项的成因）。然后断言草稿、逐句机械核对、缺口、`cited_preview.md`、失败账本确实落盘，
  预览标明审阅未完成且不可发布，系统不放行，`run()` 返回非零。

成本（本机实测，不是估计）与它为什么仍要登记
--------------------------------------------
本模块要的输入面是**真实**的（`ACC.RealEnvironment`：真实只读库 + 真实 PDF）。本机实测：
装配 525s、`_member_lives` 101s、`build_table_proof_matrix` 391s——约 **17 分钟**，且它读的
是非 hermetic 的真实数据。它**仍然**登记进 `EVAL_MODULES`：`test_eval_suite_shape` 的登记面
要求磁盘与清单双向相等，而未登记 = 回归从不执行——本批要验的正是运行入口，而入口要的输入只有
真实装配给得出。三个 case 共用**同一次**装配与**同一次**表矩阵计算（纯计算按参数记忆化），
所以 17 分钟是**整模块一次**的成本，不是每个 case 一次。

本模块不发任何真实模型请求、不联网（socket 层拦死）、不写库、不碰 `logs/llm`（日志目录被指向
临时目录）。缺真实只读库时如实 SKIP，不拿合成输入冒充真实装配。
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm.client as LC                                 # noqa: E402
from evaluation import run_m930_3_acceptance as ACC     # noqa: E402
from harness import report_clock as RC                  # noqa: E402
from llm import budget as LB                            # noqa: E402
from scripts import run_m930_3_cited_chain as CHAIN     # noqa: E402
from sections import cited_budget as CB                 # noqa: E402
from sections import cited_report as CRP                # noqa: E402
from sections import cited_review as CR                 # noqa: E402
from sections import cited_writer as CW                 # noqa: E402

from evals.test_m930_3_real_assembly_smoke import _registered_company_name  # noqa: E402
from evals.test_m930_3_subject_and_report_date import _current_snapshot_subjects  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

#: 本节本次写正文的那一节。两处漏项都在「写作成功、审阅失败」这条路径上，因此只需要一节。
_SECTION = "company"

#: 复现用的模型身份。与 `_cited_run_gate` 取 `config.LLM_MODEL` 同一读法：**声明一个身份**，
#: 门与客户端两侧必须拿到同一个值，否则「账上记的是谁」这条读数就不成立。本模块不发真实请求，
#: 这个身份只用于复现「门按这个身份批了、账按这个身份记了」这条内部一致性。
_REPLAY_MODEL = "cited-review-failure-entry-model"

#: 审阅边界的两个失败对象各自应落的 typed 原因码
#: （`:data:`sections.cited_report.CITED_REVIEW_FAILURE_KINDS``）。
_TRUNCATION_KIND = "review_call_failed"
_NOT_JSON_KIND = "review_reply_not_json"

#: 单节运行在 `run_dir` 根上应有的产物（`_emit_section` 逐份写出；单节不分层）。
_EXPECTED_FILES = (
    "cited_input_manifest.json", "cited_prose.json", "sentence_checks.json",
    "review_issues.json", "cited_report_version.json", "cited_preview.md",
    "cited_metric_tables.json", "presentation_routing.json",
    "source_table_display.json", "source_table_display.md", "fact_placement.json",
    "withheld_candidates.json", "cited_call_journal.json", "readback.md", "demo_page.md",
    "cited_call_ledger.json",
)

#: 半截 JSON 的可见正文：它就是「被截断的审阅回复」的样子（长度在断言里从它算出来，
#: 不写字面数字——写死就会随夹具漂移）。
_TRUNCATED_REPLY = '{"issues": [{"sentence_id": "cw-s1", "citation_id": "cit-1",'
_NON_JSON_REPLY = "这不是 JSON，只是一段散文。"

#: `CHAIN.OfflineEchoCitedReviewClient` 在**本模块任何 patch 之前**的原样句柄：正例要走的正是
#: 生产替身本身，而不是它的一份复写。
_ORIGINAL_ECHO = CHAIN.OfflineEchoCitedReviewClient


# ---------------------------------------------------------------------------
# 假 provider：形状可控，且把最终 kwargs 留下来
# ---------------------------------------------------------------------------

class _Block:
    _FIELD = {"text": "text", "thinking": "thinking", "redacted_thinking": "data"}

    def __init__(self, kind: str, body: str = "") -> None:
        self.type = kind
        setattr(self, self._FIELD.get(kind, "text"), body)


class _Usage:
    def __init__(self, i: int, o: int) -> None:
        self.input_tokens = i
        self.output_tokens = o


class _Resp:
    def __init__(self, blocks, *, stop_reason: str, usage=(10, 8192)) -> None:
        self.content = list(blocks)
        self.stop_reason = stop_reason
        self.usage = None if usage is None else _Usage(*usage)


class _FakeProvider:
    """`llm.client.get_client()` 的位置上放它：`messages.create` 只回自己那颗假响应。"""

    def __init__(self, resp: _Resp) -> None:
        self._resp = resp
        self.sent: list[dict] = []
        # 直接当 `client.messages` 用：`chat_with_usage` 只碰 `.messages.create` 一处。
        self.messages = self

    def create(self, **kwargs):
        self.sent.append(kwargs)
        return self._resp


def _provider(text: str, stop_reason: str) -> _FakeProvider:
    return _FakeProvider(_Resp([_Block("text", text)], stop_reason=stop_reason))


class _SocketCut:
    """断网保护：进程内任何 socket 连接尝试当场失败。

    它拦的位置比 `llm.client._NetworkGuard` **更下面**：guard 拦的是本仓的 provider 入口
    （`chat_with_usage` / `chat` / `get_client`），它拦的是 socket 本身——即使有人绕过入口直接
    拿 SDK/httpx 发请求，也连不出去。两层一起看，而不是只信一层。
    """

    def __init__(self) -> None:
        self.trips: list[str] = []
        self._saved = ()

    def __enter__(self) -> "_SocketCut":
        def _blocked(name: str):
            def _fail(*a, **kw):
                self.trips.append(name)
                raise AssertionError(
                    f"本模块不得联网，却发生了 socket 连接（{name}）："
                    "「没有真实模型请求」这句话必须由断网本身保证，不能靠假设")
            return _fail

        self._saved = (socket.socket.connect, socket.create_connection)
        socket.socket.connect = _blocked("socket.socket.connect")
        socket.create_connection = _blocked("socket.create_connection")
        return self

    def __exit__(self, *exc) -> None:
        socket.socket.connect, socket.create_connection = self._saved


# ---------------------------------------------------------------------------
# 一次真实装配，三个 case 共用
# ---------------------------------------------------------------------------

_ENV: dict = {}


class _ReusedRealEnvironment:
    """`ACC.RealEnvironment` 的位置上放**已经装配好的那一次**。

    装配约 9 分钟，而三个 case 要的是**同一份**输入面（用例之间只换审阅替身）。这里把它装一次、
    在整个模块里复用，并数下被复用了几次：次数与 case 数不符时，说明有一条路径根本没经过真实
    输入面——那正是本模块最该当场发现的事。
    """

    def __init__(self, *, clock=None, mode=None, declaration=None,
                 source_path_resolver=None) -> None:
        _ENV["reuse"] = int(_ENV.get("reuse", 0)) + 1
        #: `source_path_resolver` 是链为「本 run 上传的原始字节」新加的入口。本模块跑的是
        #: **不带 `--run-input`** 的历史兼容路径，链那三处读 PDF 的点应当照旧走登记路径，
        #: 所以它到这里必须恒为 `None`。它一旦非空，说明有别的入口把上传对象塞进了这条
        #: 路径——那是要当场看见的事，不是可以忽略的多余参数。替身必须**逐字镜像**生产签名，
        #: 否则链一改签名，这里报的是 `TypeError`，把一个接线缺陷读成环境噪声。
        _ENV.setdefault("resolvers", []).append(source_path_resolver)

    def __enter__(self):
        return _ENV["env"]

    def __exit__(self, *exc) -> bool:
        # 真实环境的生命周期由本模块自己管（进入一次、退出一次）：这里**不**关它，
        # 否则第一个 case 结束就会把后面两个 case 的输入面一起删掉。
        return False


def _once(fn, cache: dict):
    """按参数记忆化的纯计算包装（`run()` 每个 case 都会调它一次，输入面逐字相同）。"""

    def _wrapped(*args, **kwargs):
        key = repr((args, kwargs))
        if key not in cache:
            cache[key] = fn(*args, **kwargs)
        return cache[key]

    _wrapped.__name__ = getattr(fn, "__name__", "wrapped")
    return _wrapped


# ---------------------------------------------------------------------------
# 审阅替身：把**真实产出的**失败对象原样抛出
# ---------------------------------------------------------------------------

def _review_client_class(kind: str, exc, sink: list):
    """`OfflineEchoCitedReviewClient` 的位置上要放的**类**（`run()` 是构造后调用的）。

    * `kind="echo"` → **就是**本脚本原来的那个类：正例走生产替换路径本身。
    * `kind="raise"` → 只做两件事的替身：把这次失败记进自己的 `calls`，再把 `exc` 原样抛出。

    抛出的 `exc` **不**是本模块现造的一个同类异常，而是 §2 里真实客户端（截断）与真实解析器
    （非 JSON）产出的那一个。这样穿进审阅边界的对象身份是真的，边界 `except` 元组里的类型也就
    被真对象命中——不需要在测试里重抄一份类型清单。
    """
    if kind == "echo":
        return _ORIGINAL_ECHO

    class _Raising:
        def __init__(self, *, check_report) -> None:
            self._check_report = check_report
            self.calls: list[dict] = []

        def review(self, *, messages, system: str, prompt_version: str, model_policy: str):
            if isinstance(exc, LC.LLMTruncatedResponse):
                # 与真实客户端同一条纪律：**记下来**（含 call_id）**再抛**。
                self.calls.append(CW.truncated_call_record(
                    exc, model=None, prompt_version=prompt_version,
                    model_policy=model_policy))
            else:
                self.calls.append({"call_id": "", "model": "", "status": "error",
                                   "prompt_version": prompt_version,
                                   "model_policy": model_policy,
                                   "error": f"{type(exc).__name__}: {exc}"})
            sink.append(self)
            raise exc

    return _Raising


def _run_case(*, results_root: Path, run_id: str, subject: str, subject_name: str,
              kind: str, exc, sink: list) -> int:
    """**同一条** `run()`：逐 case 只换审阅替身，其余入口参数逐字相同。"""
    with patch.object(CHAIN, "OfflineEchoCitedReviewClient",
                      _review_client_class(kind, exc, sink)):
        return CHAIN.run(results_root=results_root, run_id=run_id,
                         profile_path=CHAIN.DEFAULT_PROFILE,
                         subject=subject, subject_name=subject_name,
                         section_ids=(_SECTION,), mode=ACC.MODE_OFFLINE)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _log_rows(logs_dir: Path) -> dict:
    """临时 `logs/llm` 里的调用日志，按 `call_id` 索引（`_log_llm_call` 的命名约定）。"""
    rows: dict[str, dict] = {}
    for path in sorted(logs_dir.glob("*.jsonl")):
        record = json.loads(path.read_text(encoding="utf-8"))
        rows[str(record.get("call_id") or "")] = {"row": record, "name": path.name}
    return rows


def _exc_call_id(exc: BaseException) -> str:
    """截断异常身上带的 call_id（`LLMTruncatedResponse.response.call_id`）。

    取不到就返回空串：断言失败要读成 FAIL，不能读成 traceback 把后面的证据全埋掉。
    """
    return str(getattr(getattr(exc, "response", None), "call_id", "") or "")


def _client_call_id(client: Any) -> str:
    """客户端 `calls` 里第一条流水的 call_id；一条都没有时返回空串（同上，宁可 FAIL）。"""
    calls = list(getattr(client, "calls", None) or ())
    return str(calls[0].get("call_id") or "") if calls else ""


def _draft_body(draft: dict) -> str:
    """草稿正文的字面拼接（只用来判「草稿在人读出口里读得到」，不参与任何身份计算）。

    读法是草稿自己的形状：`subsections[].paragraphs[].sentences[].text`。
    """
    return "".join(str(s.get("text") or "")
                   for sub in (draft.get("subsections") or ())
                   for para in (sub.get("paragraphs") or ())
                   for s in (para.get("sentences") or ()))


def main() -> dict:  # noqa: C901 - 逐条 check，线性读法
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg: str) -> None:
        nonlocal passed, failed
        if cond:
            passed += 1
            details.append(f"PASS: {msg}")
        else:
            failed += 1
            details.append(f"FAIL: {msg}")

    ev_db = REPO / "data" / "evidence.db"
    fin_db = REPO / "data" / "financial_v2.db"
    if not ev_db.exists() or not fin_db.exists():
        skipped += 1
        details.append(
            f"SKIP 缺真实只读库（evidence.db={'有' if ev_db.exists() else '无'}，"
            f"financial_v2.db={'有' if fin_db.exists() else '无'}）：没有输入面就没有运行入口"
            "可验，如实跳过而不是拿合成输入冒充真实装配")
        return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}

    subjects = _current_snapshot_subjects(fin_db)
    subject_name = _registered_company_name(fin_db, subjects[0]) if subjects else None
    if not subjects or not subject_name:
        skipped += 1
        details.append(
            f"SKIP 财务库里没有可声明的真实主体名称（主体候选 {subjects}）："
            "不拿证券代码冒充名称，也不在测试里写死公司名")
        return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}
    subject = subjects[0]

    tmp_root = Path(tempfile.mkdtemp(prefix="eval_cited_review_failure_entry_"))
    results_root = tmp_root / "results"
    results_root.mkdir()
    logs_dir = tmp_root / "llm_logs"
    logs_dir.mkdir()

    # ---------------------------------------------------------------- §0 一次真实装配
    details.append("## §0 一次真实装配（整模块共用：三个 case 走同一份输入面）")
    from planning import demo_scope as SC_scope

    clock = RC.capture_report_clock()
    declaration = ACC.DeclaredReportInput(subject_id=subject, subject_name=subject_name,
                                         report_as_of=clock.report_as_of)
    ACC._DEMO_SCOPE_PROFILE_PATH = CHAIN.DEFAULT_PROFILE
    ACC.bind_section_order(SC_scope.load_demo_scope_profile(ACC.active_profile_path()))

    build_cut = _SocketCut()
    env = None
    try:
        with build_cut:
            env = ACC.RealEnvironment(clock=clock, mode=ACC.MODE_OFFLINE,
                                      declaration=declaration)
            env.__enter__()
    except BaseException as exc:  # 装配失败要看得见原因，而不是被后面断言掩盖
        details.append(f"CRASH 真实装配失败：{type(exc).__name__}: {exc}")
        return {"passed": passed, "failed": failed + 1, "skipped": skipped,
                "details": details}
    _ENV["env"] = env
    check(build_cut.trips == [], "装配期间没有发生任何 socket 连接（断网保护未被触碰）")
    details.append(f"NOTE 临时运行根目录 {tmp_root}；临时日志目录 {logs_dir}")

    matrix_cache: dict = {}
    observer: dict = {}

    def _observing(original):
        def _wrapped(**kwargs):
            observer.update(kwargs)
            return original(**kwargs)
        return _wrapped

    try:
        with patch.object(ACC, "RealEnvironment", _ReusedRealEnvironment), \
             patch.object(CHAIN, "_member_lives",
                          _once(CHAIN._member_lives, matrix_cache)), \
             patch.object(CHAIN, "build_table_proof_matrix",
                          _once(CHAIN.build_table_proof_matrix, matrix_cache)), \
             patch.object(CR, "review_cited_prose", _observing(CR.review_cited_prose)), \
             patch.object(LC, "LOGS_DIR", logs_dir), \
             _SocketCut() as net:
            # ------------------------------------------------------------ §1 正例
            details.append("## §1 正例：审阅正常完成，同一入口返回 0")
            logs_before = _log_rows(logs_dir)
            rc_ok = _run_case(results_root=results_root, run_id="review_ok",
                              subject=subject, subject_name=subject_name,
                              kind="echo", exc=None, sink=[])
            out_ok = results_root / "review_ok"
            missing = [n for n in _EXPECTED_FILES if not (out_ok / n).exists()]
            check(rc_ok == 0, f"正例：`run()` 返回 0（实测 {rc_ok}）")
            check(not missing, f"正例：单节运行的产物齐（缺 {missing}）")
            check(_log_rows(logs_dir) == logs_before,
                  "正例：离线替身全程没有写出任何 provider 调用日志"
                  "（临时 `logs/llm` 0 → 0；离线正例不发真实请求）")
            pos = _json(out_ok / "cited_report_version.json")
            check(pos["review_failure"] == "" and pos["review_outcome_recorded"] is True
                  and bool(pos["review_bundle_id"]),
                  "正例：审阅产出被记下、且**没有**失败读数"
                  f"（实测 review_failure={pos['review_failure']!r}，"
                  f"recorded={pos['review_outcome_recorded']}）")
            check(pos["process_state"] == "flow_complete"
                  and pos["review_producer_kind"] == "offline_diagnostic_echo"
                  and pos["sentence_count"] > 0,
                  f"正例：流程状态与审阅产出者如实（{pos['process_state']}／"
                  f"{pos['review_producer_kind']}／{pos['sentence_count']} 句）")
            details.append(
                f"NOTE 正例读数：机械核对 `{pos['mechanical_state']}`"
                f"（{pos['blocking_sentence_count']}/{pos['sentence_count']} 句硬错误）、"
                f"缺口 {pos['required_gap_count']} 条、发布资格 `{pos['publishability']}`、"
                f"系统审阅 `{pos['system_review_state']}`")

            # ------------------------------------------- §2 两个**真实产出**的失败对象
            details.append("## §2 真实客户端 ＋ 真实预算门 ＋ 真实解析器：两个真实失败对象")
            check(bool(observer), "§2 的前置：正例里真的调用过 `review_cited_prose`"
                                  "（否则下面的真实草稿无从取）")
            call_kwargs = dict(observer)
            check(set(call_kwargs) >= {"draft", "manifest", "report_version", "report_id",
                                       "check_report", "model_policy", "client"},
                  f"§2 取到正例的真实审阅输入（实测键 {sorted(call_kwargs)}）")
            check(isinstance(call_kwargs.get("client"), _ORIGINAL_ECHO),
                  "正例里送进 `review_cited_prose` 的确实是生产替身客户端"
                  f"（实测 {type(call_kwargs.get('client')).__name__}）")
            # `client` 留在 `call_kwargs` 里是为了上面这条身份断言；重放时要**摘掉**它，
            # 否则 `review_cited_prose(client=..., **call_kwargs)` 是重复关键字，
            # 真正的客户端一次都不会被调用（那样测的就不是截断路径，而是一个 TypeError）。
            replay_kwargs = {k: v for k, v in call_kwargs.items() if k != "client"}

            # -- 截断：真实客户端既记也抛，且这次调用在**真实预算门**上已占额 ----------
            trunc_provider = _provider(_TRUNCATED_REPLY, "max_tokens")
            trunc_client = CR.LlmCitedReviewClient(model=_REPLAY_MODEL,
                                                   thinking=CHAIN.CITED_THINKING_DISABLED)
            trunc_gate = CB.cited_call_budget_gate(approved_model=_REPLAY_MODEL)
            trunc_exc = None
            with patch.object(LC, "get_client", return_value=trunc_provider):
                with CHAIN._cited_call_scope(trunc_gate, section_id=_SECTION):
                    try:
                        CR.review_cited_prose(client=trunc_client, **replay_kwargs)
                    except BaseException as exc:  # noqa: BLE001 - 要的就是那个对象
                        trunc_exc = exc
            check(isinstance(trunc_exc, LC.LLMTruncatedResponse),
                  f"截断：真实客户端把截断**抛出去**（实测 {type(trunc_exc).__name__}: {trunc_exc}）")
            check(len(trunc_provider.sent) == 1,
                  f"截断：到 provider 的请求恰好 1 次（实测 {len(trunc_provider.sent)}）")
            check(len(trunc_client.calls) == 1
                  and trunc_client.calls[0]["status"] == "error"
                  and trunc_client.calls[0]["call_id"]
                  == getattr(getattr(trunc_exc, "response", None), "call_id", None),
                  "截断：同一次失败在客户端 `calls` 里留下带 call_id 的失败流水"
                  f"（实测 {trunc_client.calls[:1]}）")
            check(bool(trunc_client.calls)
                  and "text" not in trunc_client.calls[0]
                  and "response_hash" not in trunc_client.calls[0],
                  "截断：失败流水里没有 `text` / `response_hash`"
                  "（半截内容不得升格成可引用的产物身份）")
            tsum = trunc_gate.summary()
            #: `attempts_by_section` 的形状是 `{节: {类别: 次数}}`（`budget.section_counts()`），
            #: 不是 `{节: 次数}`——按后者断言会把一次**已经记上的**占额读成没记上。
            check(tsum["attempt_total"] == 1
                  and tsum["attempts_by_category"] == {CB.CATEGORY_CITED_PROSE_REVIEW: 1}
                  and tsum["attempts_by_section"]
                  == {_SECTION: {CB.CATEGORY_CITED_PROSE_REVIEW: 1}},
                  "截断：这次调用在 `cited-budget-2` 上**已占额**，类别与节都对得上"
                  f"（实测 {tsum['attempts_by_category']} / {tsum['attempts_by_section']}）")
            check(tsum["attempts_by_status"] == {"error": 1}
                  and tsum["attempts"][0]["call_id"] == trunc_exc.response.call_id,
                  "截断：账上那一条**带状态 error**，且是**同一个** call_id"
                  "（不是一条无声的失败）")
            check(LB.installed() is None,
                  "截断：复现结束预算门已还原（不污染相邻的节）")

            # -- 非 JSON：调用本身成功、失败发生在解析 --------------------------------
            nj_provider = _provider(_NON_JSON_REPLY, "end_turn")
            nj_client = CR.LlmCitedReviewClient(model=_REPLAY_MODEL,
                                                thinking=CHAIN.CITED_THINKING_DISABLED)
            nj_gate = CB.cited_call_budget_gate(approved_model=_REPLAY_MODEL)
            nj_exc = None
            with patch.object(LC, "get_client", return_value=nj_provider):
                with CHAIN._cited_call_scope(nj_gate, section_id=_SECTION):
                    try:
                        CR.review_cited_prose(client=nj_client, **replay_kwargs)
                    except BaseException as exc:  # noqa: BLE001
                        nj_exc = exc
            check(isinstance(nj_exc, CR.CitedReviewError),
                  f"非 JSON：真实解析器抛 `CitedReviewError`（实测 {type(nj_exc).__name__}: {nj_exc}）")
            check(getattr(nj_exc, "reason", "") == "response_not_json",
                  "非 JSON：解析口写下的 reason 逐字是 `response_not_json`"
                  f"（实测 {getattr(nj_exc, 'reason', None)!r}）")
            check(bool(nj_client.calls) and nj_client.calls[0]["status"] == "ok"
                  and nj_gate.summary()["attempts_by_status"] == {"ok": 1},
                  "非 JSON：这次**调用**本身是成功的（账上记 ok），失败发生在解析——"
                  "「调用失败」与「回复不合约」在账上是两件事，不能记成同一条")

            # -- 映射表：键必须**逐字等于**解析口实际抛出的那个字符串 ------------------
            details.append("## §2b 映射表：键 = 解析口实际抛出的 reason（本批第 2 项漏项）")
            check(nj_exc.reason in CHAIN._REVIEW_FAILURE_BY_REASON,
                  f"映射表里有 `{nj_exc.reason}` 这个键——键取自真实解析口，不是抄一个近名")
            check(CHAIN._REVIEW_FAILURE_BY_REASON.get(nj_exc.reason) == _NOT_JSON_KIND,
                  f"非 JSON → `{_NOT_JSON_KIND}`"
                  f"（实测 {CHAIN._REVIEW_FAILURE_BY_REASON.get(nj_exc.reason)!r}）")
            check("review_reply_not_json" not in CHAIN._REVIEW_FAILURE_BY_REASON,
                  "修前那个错键 `review_reply_not_json` **已不在表里**：它是本表的**值**，"
                  "不是任何一个解析口会抛出的 reason，留在键位上就还会再记错一次")
            check(CHAIN._review_failure_kind(trunc_exc) == _TRUNCATION_KIND,
                  f"真实截断异常 → `{_TRUNCATION_KIND}`（截断是**调用**失败，不是协议不合约）")
            check(CHAIN._review_failure_kind(nj_exc) == _NOT_JSON_KIND,
                  f"真实非 JSON 异常 → `{_NOT_JSON_KIND}`")
            check(CHAIN._review_failure_kind(RuntimeError("没有 reason 的意外异常"))
                  == "review_reply_unparsable",
                  "没登记的失败**不被丢掉**：落到 `review_reply_unparsable` 兜底")
            check({CHAIN._review_failure_kind(trunc_exc),
                   CHAIN._review_failure_kind(nj_exc)} <= set(CRP.CITED_REVIEW_FAILURE_KINDS),
                  "两个读数都在封闭词表 `CITED_REVIEW_FAILURE_KINDS` 内"
                  f"（{CRP.CITED_REVIEW_FAILURE_KINDS}）——没有新增第五种状态")

            # 两个真实失败对象各自的调用日志（同一 call_id 可对账）
            rows = _log_rows(logs_dir)
            trow = rows.get(_exc_call_id(trunc_exc))
            nrow = rows.get(_client_call_id(nj_client))
            check(len(rows) == 2 and trow is not None and nrow is not None,
                  f"§2 两次调用各落**恰好一条**日志，按 call_id 可对账（实测 {sorted(rows)}）")
            check(trow is not None
                  and trow["row"]["prompt_version"] == CR.CITED_REVIEW_PROMPT_VERSION
                  and trow["row"]["finish_reason"] == "max_tokens"
                  and trow["row"]["response_shape"]["stop_reason"] == "max_tokens",
                  "截断那条日志带的是**审阅**请求面与截断形态读数"
                  "（prompt 版本 = 审阅的，stop_reason = max_tokens）")
            check(nrow is not None and nrow["row"]["completion"] == _NON_JSON_REPLY,
                  "非 JSON 那条日志里留着的就是那句非 JSON 回复"
                  "（原始字节是证据，不是正文）")

            # ------------------------------------------------------------ §3 两个反例
            details.append("## §3 反例：同一条 `run()` 先写出草稿、再让审阅失败")
            logs_after_s2 = _log_rows(logs_dir)
            cases = (
                ("review_truncated", "截断", trunc_exc, _TRUNCATION_KIND,
                 LC.LLMTruncatedResponse),
                ("review_not_json", "非 JSON", nj_exc, _NOT_JSON_KIND,
                 CR.CitedReviewError),
            )
            ran_cases = 0
            for run_id, label, exc, kind, want_type in cases:
                #: §2 没拿到**真身**时，这里不能把一个来路不明的异常丢进 `run()`——
                #: 它会穿过审阅边界（只接三种）把整块证据带成 traceback。记一条 FAIL 继续。
                check(isinstance(exc, want_type),
                      f"{label}：§3 的输入是 §2 真实产出的 `{want_type.__name__}`"
                      f"（实测 {type(exc).__name__}）—— 是它就往入口里丢，不是就不丢")
                if not isinstance(exc, want_type):
                    continue
                ran_cases += 1
                sink: list = []
                rc = _run_case(results_root=results_root, run_id=run_id, subject=subject,
                               subject_name=subject_name, kind="raise", exc=exc, sink=sink)
                out = results_root / run_id
                missing = [n for n in _EXPECTED_FILES if not (out / n).exists()]
                check(rc == 1, f"{label}：`run()` 返回**非零**（实测 {rc}）——"
                               "「文件都在」不得被读成「跑完了」")
                check(not missing, f"{label}：草稿／核对／缺口／预览／账本等产物仍然齐"
                                   f"（缺 {missing}）")
                ver = _json(out / "cited_report_version.json")
                check(ver["review_failure"] == kind,
                      f"{label}：`cited_report_version.json` 的 typed 失败读数是 `{kind}`"
                      f"（实测 {ver['review_failure']!r}）")
                check(ver["process_state"] == "flow_incomplete"
                      and ver["review_outcome_recorded"] is False,
                      f"{label}：流程记 `flow_incomplete`、且**没有**伪造一份审阅产出"
                      f"（实测 {ver['process_state']}／recorded={ver['review_outcome_recorded']}）")
                check(ver["publishability"] == "not_publishable"
                      and ver["system_review_state"] == "system_review_not_run",
                      f"{label}：系统**不放行**（发布资格 `{ver['publishability']}`、"
                      f"系统审阅 `{ver['system_review_state']}`）")
                check(ver["draft_id"] == pos["draft_id"]
                      and ver["draft_fingerprint"] == pos["draft_fingerprint"]
                      and ver["sentence_count"] == pos["sentence_count"],
                      f"{label}：**原草稿**逐字还在（draft_id／fingerprint 与正例同一份，"
                      f"{ver['sentence_count']} 句）")
                check(ver["check_report_id"] == pos["check_report_id"]
                      and ver["check_report_fingerprint"] == pos["check_report_fingerprint"],
                      f"{label}：**逐句机械核对**是同一份报告"
                      "（审阅失败不重跑、不改写机械层）")
                check(ver["required_gap_count"] == pos["required_gap_count"],
                      f"{label}：缺口条数与正例一致（{ver['required_gap_count']} 条），"
                      "缺口没有被失败吞掉")

                checks = _json(out / "sentence_checks.json")
                prose = _json(out / "cited_prose.json")
                draft = prose["draft"]
                body = _draft_body(draft)
                check(checks["draft_id"] == draft["draft_id"]
                      and checks["sentence_count"] == len(checks.get("sentence_states") or ())
                      and checks["sentence_count"] == pos["sentence_count"],
                      f"{label}：`sentence_checks.json` **逐句**覆盖草稿的每一句"
                      f"（{checks['sentence_count']} 句／"
                      f"{len(checks.get('sentence_states') or ())} 条状态）")
                check(bool(draft.get("gaps") is not None),
                      f"{label}：草稿自己的缺口清单仍在 `cited_prose.json` 里"
                      f"（{len(draft.get('gaps') or ())} 条）")

                issues = _json(out / "review_issues.json")
                check(issues.get("outcome") is None
                      and issues.get("review_failure") == kind
                      and bool(issues.get("note")),
                      f"{label}：`review_issues.json` 写的是「没有意见产出」这件事本身"
                      "（`outcome: null` ＋ typed 原因 ＋ 说明），**不是**一份空的意见集"
                      "——后者会被读成「审阅跑了，一条意见都没有」")

                #: 原因码本身不足以复核（`crr-9`）：`diagnostic` 还要写清**错在哪一条意见**
                #: 与**原始可见回复在哪个 `call_id` 的日志里**。这里同时钉住「不编 ID」那一半：
                #: 离线入口**没有预算门**（`_cited_call_budget` 在 `MODE_OFFLINE` 下返回
                #: `None`，见脚本 1946 行），账本里也就没有这次的审阅尝试——此时 `call_id`
                #: 必须是空串，**不能**从替身自己的流水里抄一个看起来很像的 id 冒充。
                #: 「有门时记得上真 id」那一半由 §2 用真实客户端在真门上验过（见上文
                #: `tsum["attempts"][0]["call_id"] == trunc_exc.response.call_id`）。
                diag = issues.get("diagnostic")
                recorded = _client_call_id(sink[0]) if sink else ""
                check(isinstance(diag, dict)
                      and diag.get("error_type") == type(exc).__name__
                      and str(diag.get("message") or "") == str(exc)
                      and isinstance(diag.get("sentence_id"), str)
                      and isinstance(diag.get("citation_id"), str)
                      and diag.get("call_id") == "",
                      f"{label}：`review_issues.json` 的 `diagnostic` 是**受控诊断**"
                      "（异常类名＋原异常消息＋定位键＋调用身份），且离线入口无预算门时"
                      f"`call_id` 是空串（实测 {diag!r}）")
                if isinstance(exc, LC.LLMTruncatedResponse) and recorded:
                    check(diag.get("call_id") != recorded,
                          f"{label}：替身流水里那个 `call_id`（{recorded!r}）**没有**被抄进"
                          "诊断——账本上没有这一节的审阅尝试时，宁可留空，不编一个 ID")

                preview = (out / "cited_preview.md").read_text(encoding="utf-8")
                check("独立审阅未完成" in preview and kind in preview
                      and "不可发布" in preview,
                      f"{label}：`cited_preview.md` 标明独立审阅未完成（带原因码）且不可发布")
                first = _draft_body(draft)[:20]
                check(bool(first) and first in preview,
                      f"{label}：降级预览里读得到草稿正文"
                      f"（草稿 {len(body)} 字，首句片段已在预览里）"
                      "——失败的口径是「缺审阅这一环」，不是「什么都看不见」")

                ledger = _json(out / "cited_call_ledger.json")
                check(ledger.get("run_outcome") == "failed"
                      and ledger.get("mode") == "offline" and ledger.get("ledger") is None
                      and ledger["failure"]["error_type"] == "CitedReviewIncomplete"
                      and kind in ledger["failure"]["error"]
                      and ledger["failure"]["section_id"] == _SECTION,
                      f"{label}：失败账本确实落盘并写明节与 typed 原因"
                      f"（实测 {ledger.get('failure')}）")

                check(len(sink) == 1 and len(sink[0].calls) == 1,
                      f"{label}：这次审阅在替身自己的流水里也只剩**一条**记录"
                      "（没有重试、没有第二次调用）")
                if isinstance(exc, LC.LLMTruncatedResponse) and sink and sink[0].calls:
                    check(sink[0].calls[0].get("call_id") == str(exc.response.call_id),
                          f"{label}：替身记下的 call_id 与 §2 真实那次**同一个**"
                          f"（实测 {sink[0].calls[0].get('call_id')!r}）")

            check(_log_rows(logs_dir) == logs_after_s2,
                  "反例：两个 case 全程**没有**写出新的 provider 调用日志"
                  "（离线替身不发请求；真实调用只在 §2 的分层复现里发生）")
            check(_ENV["reuse"] == 1 + ran_cases,
                  f"每个真跑的 case 都走过了真实输入面（正例 1 ＋ 反例 {ran_cases}，"
                  f"实测复用 {_ENV['reuse']} 次）——输入面只装配一次，不在 case 之间重来")
            #: 本节跑的是不带 `--run-input` 的历史兼容路径：上传对象解析器**不得**出现在
            #: 装配面上。非空即说明有入口把上传输入塞进了这条路径。
            check(set(_ENV.get("resolvers") or ()) == {None},
                  f"历史兼容路径不得携带上传输入解析器（实测 {_ENV.get('resolvers')!r}）"
                  "——带 `--run-input` 的演示路径另有一批用例，不要混进本节")
            check(build_cut.trips == [] and net.trips == [],
                  f"全程没有发生任何 socket 连接（装配 {build_cut.trips}／运行期 {net.trips}）")
    except BaseException as exc:  # noqa: BLE001 - 本模块一轮要跑十几分钟，崩了也要留下证据
        failed += 1
        details.append(
            "FAIL 未预期崩溃（这条本身就是要修的缺陷，不是环境问题）："
            f"{type(exc).__name__}: {exc}")
        details.append(traceback.format_exc())
    finally:
        # 输入面的生命周期由本模块自己管：退出一次，临时目录随之清掉。
        try:
            env.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 - 清理失败不得顶掉上面已经读到的结论
            pass

    details.append(
        "NOTE 本模块证明的是**边界接线**：审阅失败之后草稿、逐句机械核对、缺口与降级预览仍在，"
        "typed 读数正确、系统不放行、进程非零退出；而「这次调用已占额」这条由真实预算门上的分层"
        "复现给出。它**不**证明 M930-3 或 TS5 关闭，也**不**证明正文的业务内容达到人读门。")
    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=1, default=str))
    raise SystemExit(0 if outcome["failed"] == 0 else 1)

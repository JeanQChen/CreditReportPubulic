"""Eval（**显式运行**，不进 `run_evals` 套件）：无网络的真实输入**装配** smoke。

用法: python -m evals.test_m930_3_real_assembly_smoke

为什么它不登记在 `EVAL_MODULES` 里
----------------------------------
它读的是**真实只读库与真实 PDF**（`data/evidence.db` / `data/financial_v2.db` /
`materials/`），不是临时库；单次装配要数分钟（本地权重加载 + 真实文档的结构/索引构建），
而套件里其余模块是临时库的快速回归。把数分钟的非 hermetic 装配塞进套件，会让每次
`python -m evals.run_evals` 都变慢且依赖本机数据，因此它按**显式运行**的验收 smoke 存在
（缺库时如实 SKIP，不冒充通过）。

它验的是什么
------------
P4 的「主体由**报告输入声明**、再由库核对」与「报告参考日 / 快照选择日两轴分开」这两条，
在**真实输入装配**这条路上真的走得通：

  * 声明主体 = 库里真实存在的主体（不写死公司名：声明哪一家，由
    `_current_snapshot_subjects` 只读列出候选后取用，等价于 CLI 传 `--subject`）；
  * 声明主体名称 = 库里**权威自己登记过**的唯一名称（`subj-2`：`_registered_company_name`
    只读取用，等价于 CLI 传 `--subject-name`；库里没有唯一名称时如实 SKIP，不拿代码冒充）；
  * 声明被**核对成事实**：同一主体的 current 快照、主体名称、来源清单、被绑定文档四者一致；
  * 两个日期各就各位：报告参考日来自本轮时钟，快照选择日来自库里那条 current 快照，
    各自独立携带（两者在本机真实数据上并不相等，见 NOTE）。

为什么是 `MODE_OFFLINE` 而不是 `MODE_REAL`（**已实测，不是猜**）
----------------------------------------------------------------
`RealEnvironment._build` 不只装配输入，它**接着就跑公司/行业/财务三个相位的研究**
（`worker.run_backbone_topic_phase`）。所以 `MODE_REAL` 的装配**会真的调用 provider**：
本机实测一次 `MODE_REAL` 装配在 `logs/llm/` 留下上百条 `research_*` 调用日志。
`MODE_OFFLINE` 与 `MODE_REAL` 的差别**只在研究侧 LLM 这一层**（正式 Router / ToolRegistry /
预验证资格门完全相同），装配代码是同一条；因此「无网络的真实装配 smoke」取
`MODE_OFFLINE` + 真实库/真实文档。为了不让「无网络」停留在口头假设，本模块在装配期间把
`llm.client.chat_with_usage` / `get_client` **替换成抛错版本**，并核对 `logs/llm/` 文件数
不变——真有人偷偷发出 provider 请求，这里会当场失败，而不是安静地花掉预算。

曾经的段错误（已定位并修复，留档而不是抹掉）
--------------------------------------------
本模块一度**原生段错误**（exit 139，无 Python 回溯，崩在本地权重加载期间），一度被记为
「本机偶发 native crash、重跑即可」。那个归因是**错的**，实测根因是一条真实并发缺陷：

    >>> CONSTRUCT #1 thread=ThreadPoolExecutor-1_0 from=BAAI/bge-m3
    >>> CONSTRUCT #2 thread=ThreadPoolExecutor-5_0 from=BAAI/bge-m3

真实装配会在 `ThreadPoolExecutor` 工作线程里首次取用检索器，而 `EmbeddingModel.model`
的惰性加载没有加锁，于是同一时刻有两个线程各自看到 `self._model is None`，
**并发构造两份 ~2GB 的 BGE-M3 权重**——重复抓取、双倍内存，以及并发原生加载下的段错误。
修在 `retrieval/embedding.py`（模块级加载锁 + 双重检查 + 单例本身也加锁）。
因此本模块里出现 exit 139 时**不再**当作「重跑即可」：它要么是这条缺陷回归，要么是新的
原生问题，两种都得当场查，而不是记成环境噪声、更不是记成通过。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO = Path(__file__).resolve().parent.parent

from evaluation import run_m930_3_acceptance as ACC
from harness import report_clock as RC
from llm import budget as LB
from llm import client as LLC

from evals.test_m930_3_subject_and_report_date import _current_snapshot_subjects


def _snapshot_row(db: Path, company_id: str) -> dict | None:
    """只读回一行 current 快照（smoke 用它独立算出「快照选择日」应对应的值）。"""
    conn = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM current_snapshot WHERE company_id = ?", (company_id,)).fetchall()
    finally:
        conn.close()
    return dict(rows[0]) if rows else None


def _registered_company_name(db: Path, company_id: str) -> str | None:
    """只读回该主体**权威登记过**的唯一名称（smoke 用它声明 `--subject-name`）。

    这不是生产路径选名字的逻辑（生产路径只核对声明，库里没有就拒绝）；它只是让 smoke 能拿
    库里**真实登记过**的名称去声明，从而不必在测试里写死公司名——与 `_current_snapshot_subjects`
    「挑一个库里真实存在的主体」同一约定。登记缺失或多个冲突名称时返回 `None`（由调用方如实
    SKIP，不冒充通过）。
    """
    conn = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT declared_company_name FROM financial_source_document "
            "WHERE company_id = ? AND subject_match_status = 'matched' "
            "AND declared_company_name IS NOT NULL AND TRIM(declared_company_name) <> ''",
            (company_id,)).fetchall()
    finally:
        conn.close()
    names = {str(r[0]).strip() for r in rows}
    return names.pop() if len(names) == 1 else None


def _llm_log_count() -> int:
    d = REPO / "logs" / "llm"
    return sum(1 for _ in d.iterdir()) if d.exists() else 0


class _NetworkGuard:
    """装配期间禁止任何 provider 调用：被碰到就抛错（而不是静默联网）。

    它替换的是 `llm.client` 上的入口（正式链是 `llm_client.chat_with_usage(...)` 这样按属性
    查找的，替换即生效）。若有模块在 import 时就 `from llm.client import chat_with_usage` 拿走了
    函数对象，替换拦不住——那种情况下由 `logs/llm` 文件数不变这条**兜底**判定，所以两条断言
    一起看，而不是只信 guard。
    """

    def __init__(self) -> None:
        self.attempts: list[str] = []
        self._saved: dict[str, object] = {}

    def __enter__(self) -> "_NetworkGuard":
        def _blocked(name: str):
            def _fail(*a, **kw):
                self.attempts.append(name)
                raise AssertionError(
                    f"装配 smoke 期间发生了 provider 调用（{name}）：本 smoke 必须是**无网络**的，"
                    "一旦真实装配需要联网，就该用真实模式单独跑一次并如实记账，不得在这里静默发出")
            return _fail

        for name in ("chat_with_usage", "chat", "get_client"):
            self._saved[name] = getattr(LLC, name, None)
            setattr(LLC, name, _blocked(name))
        return self

    def __exit__(self, *exc) -> None:
        for name, saved in self._saved.items():
            if saved is None:
                delattr(LLC, name)
            else:
                setattr(LLC, name, saved)


def main() -> dict:
    passed = 0
    failed = 0
    skipped = 0
    details: list[str] = []

    def check(cond, msg):
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
            f"financial_v2.db={'有' if fin_db.exists() else '无'}）：本 smoke 无输入，"
            "如实跳过而不是拿合成输入冒充真实装配")
        return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}

    subjects = _current_snapshot_subjects(fin_db)
    if not subjects:
        skipped += 1
        details.append("SKIP 财务库里没有任何 current 快照：没有可声明的真实主体")
        return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}

    subject = subjects[0]
    row = _snapshot_row(fin_db, subject)
    clock = RC.capture_report_clock()
    # `subj-2`：名称同样是**声明 + 与权威登记逐字核对**。smoke 从这里声明库里真实登记过的
    # 那个名称（不写死公司名）。库里没有唯一名称时如实 SKIP——「本项目没有可声明的名称」
    # 与「装配失败」是两回事，不能混成一条红。
    name = _registered_company_name(fin_db, subject)
    if not name:
        skipped += 1
        details.append(
            f"SKIP 主体 {subject} 在财务库里没有唯一一个 matched 的主体名称登记："
            "`subj-2` 的名称核对无从发生（不拿证券代码冒充名称）")
        return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}
    declaration = ACC.DeclaredReportInput(
        subject_id=subject, subject_name=name, report_as_of=clock.report_as_of)

    before = _llm_log_count()
    guard = _NetworkGuard()
    # 门在**进入环境之前**安装（与 runner 的真实路径同一顺序）：这样 `_build` 里的研究轴镜像
    # 断言会拿**真实** topic 集与真实 `ResearchBudgetPolicy` 复算一遍。离线装的是 `enforce=False`
    # 的账本（替身不发真实请求），但它与真实模式**共用同一份政策**，因此「镜像是怎么算出来的」
    # 在这条 smoke 里也是真实输入下的读数，而不是夹具。
    gate = LB.LLMCallBudget(ACC._call_budget_policy(), enforce=False)
    previous_gate = LB.install(gate)
    try:
        with guard:
            with ACC.RealEnvironment(clock=clock, mode=ACC.MODE_OFFLINE,
                                     declaration=declaration) as env:
                inputs = env.inputs
                research_axis_evidence = dict(env.research_axis_evidence)
        after = _llm_log_count()
    except BaseException as exc:  # 装配失败要看得见原因，而不是被后面断言掩盖
        details.append(f"CRASH 真实装配失败：{type(exc).__name__}: {exc}")
        return {"passed": passed, "failed": failed + 1, "skipped": skipped,
                "details": details}
    finally:
        LB.uninstall()
        if previous_gate is not None:
            LB.install(previous_gate)

    # -- 研究侧上限的镜像必须由真实输入复算出来（不是夹具、不是人记）-------------
    check(research_axis_evidence.get("checked") is True,
          "研究轴镜像：装了门之后必须真的核对过（`checked=true`），"
          f"不得留一个未核对就往下走（实际 {research_axis_evidence}）")
    check(not research_axis_evidence.get("problems"),
          f"研究轴镜像：真实 topic 集下逐值必须一致（实际 {research_axis_evidence.get('problems')}）")
    # 逐值核对镜像：**每个真实 topic 的上限必须等于「每 aspect 上界 × 该 topic 自己的
    # aspect 数」**——不是「一个共享值 × topic 数」，也不是夹具里写死的。
    aspects = research_axis_evidence.get("observed_aspects_by_topic") or {}
    registered = research_axis_evidence.get("axis_per_scope_max_attempts") or {}
    derived = research_axis_evidence.get("derived_axis_cap_by_topic") or {}
    expected_total = sum(ACC.RESEARCH_LLM_CALLS_PER_ASPECT * int(n) for n in aspects.values())
    check(research_axis_evidence.get("observed_topic_count") == ACC.RESEARCH_TOPIC_COUNT_BASIS
          and len(aspects) == ACC.RESEARCH_TOPIC_COUNT_BASIS
          and registered == derived
          and registered == {f"topic:{t}": ACC.RESEARCH_LLM_CALLS_PER_ASPECT * int(n)
                             for t, n in aspects.items()}
          and research_axis_evidence.get("axis_max_attempts") == expected_total
          and research_axis_evidence.get("axis_max_attempts_per_scope")
          == min(registered.values()),
          "研究轴镜像：逐 topic 上限必须等于 "
          f"每 aspect 上界 {ACC.RESEARCH_LLM_CALLS_PER_ASPECT} × 该 topic 的 aspect 数，"
          f"整轴上限等于各 topic 之和（本机实际 {expected_total}），"
          f"单值缺省等于最小值（实际 registered={registered!r} derived={derived!r} "
          f"aspects={aspects!r} axis_max={research_axis_evidence.get('axis_max_attempts')!r} "
          f"per_scope={research_axis_evidence.get('axis_max_attempts_per_scope')!r}）")

    # 两条轴在装配阶段的读数：**写作轴必须是 0**——装配不产出任何 narration/蕴含/评估调用。
    # 研究轴的读数如实记录：`MODE_OFFLINE` 换掉的只是研究侧 LLM 的**输出**，计量仍走同一道门
    # （这正是本轮要把门装到 `with RealEnvironment(...)` 之前的原因）。因此这里**不**断言它为 0，
    # 否则就是在要求「离线等于不计费」，那句恰好与本轮的预算覆盖修复相反；只要求它不越界。
    writing_axis_count = gate.count_axis(axis=LB.AXIS_WRITING)
    research_axis_count = gate.count_axis(axis=LB.AXIS_RESEARCH)
    check(writing_axis_count == 0,
          f"无网络：装配阶段写作轴必须为 0 次（实际 {writing_axis_count}）")
    check(research_axis_count <= expected_total,
          f"研究轴：装配阶段的研究尝试必须落在已批准上限 {expected_total} 之内"
          f"（实际 {research_axis_count}）")
    details.append(
        f"NOTE 装配阶段账本读数（离线替身，无 provider 调用）：研究轴 {research_axis_count} 次 / "
        f"上限 {expected_total}（逐 topic {registered}），写作轴 {writing_axis_count} 次。"
        "离线只替换研究侧 LLM 的输出，计量仍走同一道门，故研究轴非 0 是预期读数（不是联网迹象——"
        "联网由 guard 与 logs/llm 稳定两条兜底判定）")

    # -- 无网络：guard 没被碰到，且 provider 日志没有新增 ----------------------
    check(guard.attempts == [],
          "无网络：装配全程没有被触碰的 provider 入口"
          + (f"（被触碰：{guard.attempts}）" if guard.attempts else ""))
    check(after == before,
          f"无网络：装配期间 logs/llm 无新增调用日志（{before} → {after}）")

    # -- 主体：声明被核对成事实，三处身份指向同一个主体 ------------------------
    check(inputs.company_id == subject and str(inputs.dims["company_id"]) == subject,
          f"主体：声明 {subject!r} 与快照核对结果一致（不取库里排序第一条）")
    check(str(inputs.source_manifest.company_id) == subject,
          "主体：来源清单是**声明主体**的清单（声明主体与 current Evidence Set 同一公司）")
    check(str(inputs.dims.get("subject_id_declared")) == subject
          and inputs.dims.get("subject_declaration_version") == ACC.SUBJECT_DECLARATION_VERSION
          and inputs.dims.get("subject_declared_by") == "cli",
          "主体：声明（主体 + 版本 + 声明人）随装配结果一起带出，可审计")

    # -- 两个日期：各自来自自己的权威，且互不冒充 ------------------------------
    check(inputs.report_as_of == clock.report_as_of
          and inputs.dims.get("report_as_of_declared") == clock.report_as_of,
          f"日期：报告参考日来自本轮时钟（实际 {inputs.report_as_of!r}）")
    db_as_of = str(row["as_of_date"]) if row else None
    check(str(inputs.dims["as_of_date"]) == db_as_of,
          f"日期：快照选择日来自库里那条 current 快照（实际 {inputs.dims['as_of_date']!r}）")
    # 「两个日期是否真的不等」是**数据事实**，不是本 smoke 的判据：本机今天确实不等
    # （时钟 2026-09-24 vs 快照 2026-03-31），但换个日期跑就可能相等——相等时如实记录，
    # 不去断言一个由日历决定的巧合。两轴的**分离**本身由携带者断言保证（各来自自己的权威），
    # 那两条与日期是否相等无关，因此不是空断言。
    details.append(
        f"NOTE 两轴是否不等（数据事实，非判据）：报告参考日 {inputs.report_as_of!r} vs "
        f"快照选择日 {inputs.dims['as_of_date']!r} → "
        f"{'不同' if str(inputs.report_as_of) != str(inputs.dims['as_of_date']) else '相同（同日）'}")

    # -- 装配出来的材料身份是可定位的 -----------------------------------------
    check(bool(inputs.document_id) and bool(inputs.document_version)
          and bool(inputs.raw_pdf_sha256),
          f"材料：被绑定文档身份可定位（{inputs.document_id}@{inputs.document_version}）")
    entries = list(getattr(inputs.source_manifest, "entries", []) or [])
    check(len(entries) >= 1,
          f"材料：来源清单登记了 {len(entries)} 份当前材料（登记 ≠ 选用，两者都在清单里）")

    details.append(
        f"NOTE 声明主体={subject}；报告参考日={inputs.report_as_of}（时钟）；"
        f"快照选择日={inputs.dims['as_of_date']}（库）/ 独立只读回读={db_as_of}；"
        f"绑定文档={inputs.document_id}@{inputs.document_version}；"
        f"来源清单 {len(entries)} 份；研究侧 LLM=离线替身（本 smoke 无网络）")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

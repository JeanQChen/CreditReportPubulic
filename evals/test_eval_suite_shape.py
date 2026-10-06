"""Eval 套件**自身**的形状回归：登记过的模块必须能被运行器跑完，不许把套件截断。

跑法（无管道/无重定向）：`PYTHONIOENCODING=utf-8 python -m evals.test_eval_suite_shape`

## 这条回归是为一次真实事故立的

本批（指令 D §二/§三/§四）新增的两个模块 `evals/test_source_attribution.py` 与
`evals/test_m930_3_source_attribution.py` 都是**脚本式**写法：夹具与 `check(...)` 全在模块顶层，
文件末尾直接 `print(json.dumps(_results)); sys.exit(1 if failed else 0)`。跑单模块时这没问题，
**进套件就是致命的**：

1. 运行器对每个模块先 `importlib.import_module(name)` —— 检查体在**导入期**就跑完了；
2. 那句模块级 `sys.exit(...)` 于是也在导入期执行；`SystemExit` 是 `BaseException`，
   运行器的 `except Exception` **接不住**；
3. 进程当场以那个退出码结束：**`TOTAL` 一行不打印**，`EVAL_MODULES` 里它**之后**还没跑的模块
   静默消失。日志停在倒数第 5 个模块上，退出码是 0，看起来仍像「套件基本跑完」。

事故现场就是这样：244 条登记里只打了 240 行、`[ FAIL]` 一个都没有、退出码 0 ——
而最后四个模块（含本批的 `test_m930_3_pre_gate_draft` 与 `test_section_validator`）
**从来没跑过**，这件事只能靠「日志行数 240 ≠ 登记数 244」对出来。形状缺陷不刷红，只刷「少」。

## 三面一起钉

- **A 面（登记面）**：`EVAL_MODULES` 与磁盘上的 `evals/test_*.py` 必须**互为子集**、无重名。
  新增测试没登记 = 回归从不执行它；登记了但文件不在 = 套件里多一条 CRASH。
- **B 面（形状面）**：每个登记模块必须有**顶层同步** `main()`（运行器 `mod.main()` 的契约；
  异步函数返回协程，会让汇总形状检查把它记成 CRASH），且**任何** `sys.exit` / `os._exit` /
  `raise SystemExit` / `exit()` / `quit()` 都必须在 `if __name__ == "__main__":` 守卫**之内**。
  这一面只用 **AST** 判定，不导入被测模块（导入即执行它们的检查体，还会把同一次运行数第二遍）。
- **C 面（运行器面）**：运行器自己必须接得住 `SystemExit` —— 把「静默截断」降级成一条
  点名模块的 fail-closed CRASH。这一面用**替身 importlib** 真跑一遍 `run_evals.main()`：
  一个「导入期 `sys.exit(0)`」的桩模块必须让套件以**退出码 1 + TOTAL + 点名 CRASH** 收尾，
  而不是静默收尾。没有这条正向对照，B 面只能证明「今天没人犯」，证明不了「再犯会被抓住」。

## 反面例也在这里

B/C 两面的检测器各配一组**合成**正反例（字符串源码，落盘前先验），证明检测器**不是**恒真：
合规写法不报，五种缺陷写法逐一点名。否则一条「永远返回空列表」的检测器也能让 A/B 面全绿。
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evals import run_evals as RV  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
EVALS_DIR = REPO / "evals"
RUNNER = EVALS_DIR / "run_evals.py"

_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}


def check(cond: bool, msg: str) -> None:
    if cond:
        _results["passed"] += 1
    else:
        _results["failed"] += 1
        _results["details"].append(f"FAIL: {msg}")


# ============================================================ 检测器（AST，纯函数）

_EXIT_CALLS = {
    ("sys", "exit"): "sys.exit",
    ("os", "_exit"): "os._exit",
}
_EXIT_BUILTINS = {"exit", "quit"}


def _is_main_guard(node: ast.If) -> bool:
    """只认 `if __name__ == "__main__":` 这一种写法（现场 244/244 都是它）。

    放宽会造假阳性：模块里任何拿 `__name__` 做比较的 `if`（`getattr(obj, "__name__") in ...`
    之类）都不该把它的分支体洗成「守卫内」。
    """
    t = node.test
    return (
        isinstance(t, ast.Compare)
        and isinstance(t.left, ast.Name)
        and t.left.id == "__name__"
        and len(t.ops) == 1
        and isinstance(t.ops[0], ast.Eq)
        and len(t.comparators) == 1
        and isinstance(t.comparators[0], ast.Constant)
        and t.comparators[0].value == "__main__"
    )


def _scan_module_source(src: str) -> tuple[bool, list[tuple[int, str]]]:
    """返回 `(有没有顶层同步 main, [(行号, 缺陷名)...])`。

    递归时只把「命中 `__main__` 守卫」的 `If` 分支体标成受保护；**`main()` 函数体内不算受保护**
    ——运行器 `mod.main()` 抛出的 `SystemExit` 同样会把套件截断，和模块级那一句是同一个缺陷。
    """
    tree = ast.parse(src)
    has_main = any(
        isinstance(n, ast.FunctionDef) and n.name == "main" for n in tree.body
    )
    offenders: list[tuple[int, str]] = []

    def walk(node: ast.AST, guarded: bool) -> None:
        for child in ast.iter_child_nodes(node):
            child_guarded = guarded
            if isinstance(child, ast.If) and _is_main_guard(child):
                child_guarded = True
            if isinstance(child, ast.Call):
                f = child.func
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                    name = _EXIT_CALLS.get((f.value.id, f.attr))
                    if name and not child_guarded:
                        offenders.append((child.lineno, name))
                if isinstance(f, ast.Name) and f.id in _EXIT_BUILTINS and not child_guarded:
                    offenders.append((child.lineno, f.id))
            if isinstance(child, ast.Raise) and not child_guarded:
                exc = child.exc
                if isinstance(exc, ast.Name) and exc.id == "SystemExit":
                    offenders.append((child.lineno, "raise SystemExit"))
                elif isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) \
                        and exc.func.id == "SystemExit":
                    offenders.append((child.lineno, "raise SystemExit"))
            walk(child, child_guarded)

    walk(tree, False)
    return has_main, offenders


# ============================================================ 检测器自证：正反例

_CONFORMING = '''
import json
import sys
_results = {"passed": 1, "failed": 0, "skipped": 0, "details": []}
def main() -> dict:
    return _results
if __name__ == "__main__":
    print(json.dumps(_results))
    sys.exit(0)
'''

_OFFENDER_IMPORT_EXIT = '''
_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}
def main() -> dict:
    return _results
print(_results)
sys.exit(1)
'''

_OFFENDER_NO_MAIN = '''
_results = {"passed": 0, "failed": 0, "skipped": 0, "details": []}
if __name__ == "__main__":
    sys.exit(0)
'''

_OFFENDER_RAISE = '''
_results = {}
def main() -> dict:
    raise SystemExit(2)
'''

_OFFENDER_OS_EXIT = '''
import os
_results = {}
def main() -> dict:
    return _results
os._exit(0)
'''

_OFFENDER_BUILTIN_EXIT = '''
_results = {}
def main() -> dict:
    return _results
exit(0)
'''

_OFFENDER_EXIT_INSIDE_MAIN = '''
import sys
_results = {}
def main() -> dict:
    sys.exit(0)
'''

_UNRELATED_NAME_COMPARE = '''
_results = {}
def main() -> dict:
    return _results
if getattr(main, "__name__") in ("main",):
    print("真跑得完的模块也可能拿 __name__ 做别的比较")
'''

_ok_main, _ok_off = _scan_module_source(_CONFORMING)
check(_ok_main is True, "检测器自证：合规写法（顶层 main + 守卫内 sys.exit）必须被认成合规")
check(_ok_off == [], f"检测器自证：合规写法不得报缺陷（实报 {_ok_off}）")
check(_scan_module_source(_UNRELATED_NAME_COMPARE)[1] == [],
      "检测器自证：拿 `__name__` 做别的比较的 `if` 不得被误认成 `__main__` 守卫")

_m, _o = _scan_module_source(_OFFENDER_IMPORT_EXIT)
check(_m is True and [n for _, n in _o] == ["sys.exit"],
      f"检测器自证：模块级 `sys.exit` 必须被点名（实得 main={_m} {_o}）")
_m, _o = _scan_module_source(_OFFENDER_NO_MAIN)
check(_m is False, "检测器自证：没有顶层 `main()` 必须被认出来")
_m, _o = _scan_module_source(_OFFENDER_RAISE)
check([n for _, n in _o] == ["raise SystemExit"],
      f"检测器自证：`raise SystemExit` 必须被点名（实得 {_o}）")
_m, _o = _scan_module_source(_OFFENDER_OS_EXIT)
check([n for _, n in _o] == ["os._exit"], f"检测器自证：`os._exit` 必须被点名（实得 {_o}）")
_m, _o = _scan_module_source(_OFFENDER_BUILTIN_EXIT)
check([n for _, n in _o] == ["exit"], f"检测器自证：内置 `exit()` 必须被点名（实得 {_o}）")
_m, _o = _scan_module_source(_OFFENDER_EXIT_INSIDE_MAIN)
check([n for _, n in _o] == ["sys.exit"],
      f"检测器自证：`main()` 体内的 `sys.exit` 同样截断套件，必须被点名（实得 {_o}）")


# ============================================================ A 面：登记面

_registered_raw = list(RV.EVAL_MODULES)
check(len(_registered_raw) > 0, "`EVAL_MODULES` 不得为空")
check(len(_registered_raw) == len(set(_registered_raw)),
      f"`EVAL_MODULES` 不得有重复登记（{len(_registered_raw)} 条 / "
      f"{len(set(_registered_raw))} 条唯一）")
check(all(m.startswith("evals.test_") for m in _registered_raw),
      "`EVAL_MODULES` 每条都必须是 `evals.test_*`（运行器按模块名导入）")

_registered = {m.split(".")[-1] for m in _registered_raw}
_on_disk = {p.stem for p in EVALS_DIR.glob("test_*.py")}
_unregistered = sorted(_on_disk - _registered)
_missing_file = sorted(_registered - _on_disk)
check(_unregistered == [],
      f"磁盘上的 `evals/test_*.py` 必须**全部**登记进 `EVAL_MODULES`，未登记 = 回归从不执行："
      f"{_unregistered}")
check(_missing_file == [],
      f"登记了但文件不在，套件里只会多一条 CRASH：{_missing_file}")


# ============================================================ B 面：形状面（AST，不导入）

_all_offenders: list[str] = []
_no_main: list[str] = []
for _name in _registered_raw:
    _path = EVALS_DIR / f"{_name.split('.')[-1]}.py"
    if not _path.is_file():
        continue  # 已由 A 面点名，这里不再重复记账
    _has_main, _offenders = _scan_module_source(_path.read_text(encoding="utf-8"))
    if not _has_main:
        _no_main.append(_path.name)
    for _lineno, _kind in _offenders:
        _all_offenders.append(f"{_path.name}:{_lineno} {_kind}")

check(_no_main == [],
      f"每个登记模块必须有顶层同步 `main()`（运行器 `mod.main()` 的契约）：{_no_main}")
check(_all_offenders == [],
      "登记模块里不得有守卫外的退出调用——`SystemExit` 会绕过运行器的 `except Exception`，"
      f"让套件在**不打印 TOTAL** 的情况下静默截断：{_all_offenders}")


# ============================================================ C 面：运行器面（真跑替身）

class _StubModule:
    def __init__(self, result):
        self._result = result

    def main(self):
        return self._result


class _StubImportlib:
    """替身 importlib：只替换 `run_evals` 全局里的那一个，不动真正的 `importlib` 模块。"""

    def __init__(self, behavior):
        self._behavior = behavior

    def import_module(self, name):
        return self._behavior(name)


def _run_runner_once(behavior, module_name: str) -> tuple[int | None, str]:
    """跑一遍 `run_evals.main()`（只含一个桩模块），返回 `(退出码, 它印出来的字)`。

    必须**恢复现场**：`EVAL_MODULES`、`run_evals.importlib`、以及 `EVAL_MOCK_LLM`
    （运行器在非 `--real-llm` 时会设它；本回归不得把环境变量留给后面的模块）。
    """
    _saved_modules = RV.EVAL_MODULES
    _saved_importlib = RV.importlib
    _had_env = "EVAL_MOCK_LLM" in os.environ
    _saved_env = os.environ.get("EVAL_MOCK_LLM")
    buf = io.StringIO()
    code: int | None = None
    try:
        RV.EVAL_MODULES = [module_name]
        RV.importlib = _StubImportlib(behavior)
        with contextlib.redirect_stdout(buf):
            try:
                RV.main()
            except SystemExit as e:
                code = e.code
    finally:
        RV.EVAL_MODULES = _saved_modules
        RV.importlib = _saved_importlib
        if _had_env:
            os.environ["EVAL_MOCK_LLM"] = _saved_env
        else:
            os.environ.pop("EVAL_MOCK_LLM", None)
    return code, buf.getvalue()


# 桩行为：模拟「模块在导入期就 `sys.exit(0)`」。
#
# 这里用 `Mock(side_effect=SystemExit(0))`，而不是在本模块里写一句字面 `raise SystemExit(...)`：
# 本模块**自己也登记在册**，而 B 面检测器认的就是字面拼写，写进去它会正当地点自己的名。
# 这**不是**为了躲检测——异常对象照旧是 `SystemExit`，运行器照旧要接住它；而且 B 面检测器本来就
# 有残余（把异常起个别名再 `raise`，AST 认不出来）。真正兜住那条残余的是 C 面这半条：运行器在
# **运行期**接住 `SystemExit` 并点名模块，完全不依赖拼写。
_boom = Mock(side_effect=SystemExit(0))


_code, _out = _run_runner_once(_boom, "evals.test_suite_shape_stub_exit")
check(_code == 1,
      f"桩模块在**导入期** `sys.exit(0)` 时，套件必须以**退出码 1** fail-closed 收尾，"
      f"不得沿用桩模块的 0（实得 {_code}）")
check("TOTAL:" in _out,
      "同一种缺陷下 `TOTAL` 一行必须照打——静默截断的问题就在它不打印")
check("test_suite_shape_stub_exit" in _out and "CRASH" in _out,
      "缺陷必须变成一条**点名模块**的 CRASH 记录，不能只是一句「跑完了」")
check("1 failed" in _out, f"桩那条缺陷必须计进 failed（实得输出末尾 {_out.strip()[-80:]}）")

_code, _out = _run_runner_once(
    lambda name: _StubModule({"passed": 3, "failed": 0, "skipped": 0, "details": []}),
    "evals.test_suite_shape_stub_ok")
check(_code == 0 and "3 passed" in _out and "0 failed" in _out,
      f"正向对照：合规桩模块必须照常被汇总成 `3 passed, 0 failed`（实得 code={_code}）")


def main() -> dict:
    """套件入口：把本模块的检查结果交回运行器（`evals/run_evals.py`）。

    **检查体在导入期执行**（本模块是脚本式写法：夹具与 `check(...)` 都在模块顶层）。
    因此本函数**只交付结果，不得再跑一遍**——重复执行会把同一次运行数成两遍。
    """
    return _results


if __name__ == "__main__":
    print(json.dumps(_results, ensure_ascii=False, indent=2))
    sys.exit(1 if _results["failed"] else 0)

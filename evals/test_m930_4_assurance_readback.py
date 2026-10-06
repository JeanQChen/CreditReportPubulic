"""聚焦：M930-4 人读读回（`readback.md`）的两支措辞，走**真实入口**写出来。

Run: ``python -X utf8 -m evals.test_m930_4_assurance_readback``.

这一支以前是死的：无论产物里有没有绑定记录，§4 都写「本命令未发起任何调用，停在这里等
逐次授权」，结尾写「未获授权前，上面的三块内容**未受审阅**」。审阅真的跑过之后那句话就
变成**陈旧**的话——读者会把已经提出来的意见读成「还没跑」。所以这里分别用「不给任何审阅
记录」与「给三份真实记录」跑两次入口，逐字检查两支读回各自说了什么、又**没有**说什么。

只读源 run；写入的是 `mkdir(parents=False, exist_ok=False)` 的临时目录，跑完即删。
不发模型、不发网络、不连数据库。
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from scripts import run_m930_4_assurance as ENTRY


REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "evaluation" / "results"
SOURCE = RESULTS / "m930_3_cited_real_dual_v2_r1"
REVIEW_DIR = RESULTS / "m930_4_report_review_v2_r1"
REVIEW_FILES = tuple(sorted(REVIEW_DIR.glob("report_review_run__*.json")))
R5_DIR = RESULTS / "postreview" / "m930_4_cited_v2_r5_reportreview"  # 只读保留的修正前读数
R6_DIR = RESULTS / "postreview" / "m930_4_cited_v2_r6_reportreview"  # 口径修正后的当前读数


class AssuranceReadbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not SOURCE.is_dir():
            raise unittest.SkipTest("源 run 不在本机；不伪造真实产物")
        if len(REVIEW_FILES) != 3:
            raise unittest.SkipTest("三份真实报告级审阅记录不在本机；不伪造读数")

    def _generate(self, review_files) -> str:
        """跑一次真实入口，把落盘的 `readback.md` 原样读回来。"""
        parent = Path(tempfile.mkdtemp(prefix="_tmp_m930_4_readback_", dir=REPO))
        output = parent / "out"
        try:
            argv = ["--source-run", str(SOURCE), "--output", str(output)]
            for path in review_files:
                argv += ["--report-review-run", str(path)]
            self.assertEqual(ENTRY.main(argv), 0)
            return (output / "readback.md").read_text(encoding="utf-8")
        finally:
            shutil.rmtree(parent, ignore_errors=True)
            self.assertFalse(parent.exists())

    def test_no_bound_runs_keeps_the_pending_wording(self) -> None:
        text = self._generate(())
        self.assertIn("## 4. 真实调用申请（**本命令未发起任何调用，停在这里等逐次授权**）",
                      text)
        self.assertIn("未获授权前，上面的三块内容**未受审阅**，不得写成通过。", text)
        self.assertIn("## 5. 其它边界", text)
        # §2.1 是**计划上限**，没有绑定记录时只能说「尚未发出」，不得写成实绩。
        self.assertIn("### 2.1 真实调用的分批计划（**输入面推出的上限**）", text)
        self.assertIn("**尚未发出**任何调用", text)
        self.assertNotIn("计划与实绩要分账", text)
        # 没有记录时**不得**出现「已经发生」那一支的说法。
        self.assertNotIn("逐 scope 实际读数", text)
        self.assertNotIn("## 6. 其它边界", text)

    def test_bound_runs_switch_to_the_actual_reading(self) -> None:
        text = self._generate(REVIEW_FILES)
        self.assertIn("## 4. 真实调用：逐 scope 实际读数", text)
        self.assertIn("**这一节说的是已经发生的事，不是申请。**", text)
        # 那句陈旧的「等授权」必须消失：它会把已提出的意见说成尚未发生。
        self.assertNotIn("本命令未发起任何调用", text)
        self.assertNotIn("未获授权前", text)

        # 三块内容逐行给出「跑没跑 / 发了几批 / 成几批 / 放进多少 / 回来多少 / 提了几条」。
        self.assertIn("| `material_selectivity` | `failed` | 5 | 4 | 1 | 125 | 100 | 7 | 50 | 7 条 |",
                      text)
        self.assertIn("| `cross_section` | `reviewed` | 1 | 1 | 0 | 3 | 3 | 0 | 0 | 0 条 |", text)
        self.assertIn("| `financial_a2` | `reviewed` | 1 | 1 | 0 | 5 | 5 | 0 | 0 | 0 条 |", text)

        # 两本账分开写，且把差额的来路说清楚。
        self.assertIn("**预算门尝试 7**", text)
        self.assertIn("**已落批记录 6**", text)
        self.assertIn("两者相差 1 次", text)

        # §2.1 的计划与实绩要**分账**：上限 8 批、实际发起 7 批、成功落批 6 条。
        self.assertIn("**计划与实绩要分账。**", text)
        self.assertIn("上限是 **8 批**", text)
        self.assertIn("**发起 7 批**", text)
        self.assertIn("**成功落批 6 条**", text)
        self.assertNotIn("**尚未发出**任何调用", text)

        # 失败要**把 50 个未获得可解析审阅的成员分成两拨**：真进了请求的（回复不可用）
        # 与压根没发出去的。合并成一句「都从未发出」是错的——前 25 个确实发出去过，
        # 只是那一批的内容没有人读到，这两件事的处置完全不同。
        self.assertIn("**`material_selectivity` 未跑完**", text)
        self.assertIn("**50 个成员未获得可解析审阅**", text)
        self.assertIn("**25 个真的进了请求**", text)
        self.assertIn("`citation:m16`", text)
        self.assertIn("`citation:m40`", text)
        self.assertIn("**25 个从未发进请求**", text)
        self.assertIn("`citation:m41`", text)
        self.assertIn("`citation:m65`", text)
        self.assertIn("**这一批的内容没有人读到过。**", text)
        self.assertNotIn("50 个成员从未进入任何请求", text)

        # 7 条原始意见逐条照登，连同严重度、阻断与建议补到。
        for issue_id in (
                "rvi_8495ed7f680144ead3f04052", "rvi_02d12bf92fbe5aff8e4327b3",
                "rvi_9bd9520f586fc687797a07a3", "rvi_9f3fc9ea76230e4898ca46f6",
                "rvi_c0733c65a6dc9248cdbac7e7", "rvi_c1d4afae15c427a920b805a2",
                "rvi_db9554510f9ee5d1bd82ec6c"):
            self.assertIn(issue_id, text)
        self.assertEqual(text.count("| `material_selectivity` | `rvi_"), 7)

        # 读法校准：正文未采用 ≠ 来源缺失；三条 high 是同一栏目缺口的三种表达；
        # 单条材料不被采用 ≠ 该栏目只差这一个科目。
        self.assertIn("不是**整份报告缺这些事实**", text)
        self.assertIn("同一件事的三次表达", text)
        self.assertIn("不是栏目完成证明", text)

        # 权限边界与收尾两节的编号必须落在真实存在的节底下。
        self.assertIn("## 5. 审阅能做什么、不能做什么", text)
        self.assertIn("## 6. 其它边界", text)

    def test_persisted_current_sidecar_matches_the_generator(self) -> None:
        """本机那份当作交付的**当前**读数，必须**正是**现行生成器写出来的字节。

        没有这条，生成器改了、产物没重生成，页面显示的读回就会悄悄停留在旧措辞上。

        **钉的是 r6，不是 r5。** r5 是本批修正**之前**的原始读数，按本批要求只读保留、
        不得重建，所以它相对现行生成器**本来就是陈旧的**——拿它做逐字节对账会把
        「历史产物保持原样」误报成回归。r6 才是口径修正后的当前读数。
        """
        if not R6_DIR.is_dir():
            raise unittest.SkipTest("本批的 create-only 读数不在本机；不伪造读数")
        persisted = (R6_DIR / "readback.md").read_text(encoding="utf-8")
        self.assertEqual(persisted, self._generate(REVIEW_FILES))

    def test_persisted_r5_is_left_as_the_uncorrected_reading(self) -> None:
        """r5 是本批修正**之前**的原始读数：只读保留，**不得回填**成新口径。

        本批改了生成器，但**没有**去重写已经落盘的 r5——历史读数保持当时的样子。
        如果哪天有人拿新生成器把 r5 覆盖了，这条会红。
        """
        if not R5_DIR.is_dir():
            raise unittest.SkipTest("本批的只读历史读数不在本机；不伪造读数")
        persisted = (R5_DIR / "readback.md").read_text(encoding="utf-8")
        self.assertIn("**50 个成员从未进入任何请求**", persisted)
        self.assertNotEqual(persisted, self._generate(REVIEW_FILES))


def _summary(result) -> dict:
    """运行器要的是 `passed`/`failed`/`skipped`/`details` 四个键，**不是退出码**。

    返回 `int` 会让 `run_evals` 在汇总那一步抛 `AttributeError: 'int' object has no
    attribute 'get'`，而那一行不在它的 `except` 里——套件会静默截断、`TOTAL` 不打印，
    日志看起来仍像「基本跑完」。所以退出码只在 `__main__` 守卫里从这四个键推出来。
    """
    return {
        "passed": result.testsRun - len(result.failures) - len(result.errors)
        - len(result.skipped),
        "failed": len(result.failures) + len(result.errors),
        "skipped": len(result.skipped),
        "details": [f"FAIL {test}\n{tb}" for test, tb in result.failures + result.errors],
    }


def main() -> dict:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(AssuranceReadbackTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return _summary(result)


if __name__ == "__main__":
    raise SystemExit(0 if main()["failed"] == 0 else 1)

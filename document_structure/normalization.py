"""TS2 归一化与诊断原语（**版本化、确定性、公司无关**）。

本模块只提供两类东西：

1. **文本归一**：`tight` / `collapse` / `fold` / `strip_invisible`。
2. **诊断计数**：`text_stats` / `bad_char_ratio` / `chars_equivalent`。

三条硬约束（指令 §六）：

- **不改写存量 Evidence 哈希逻辑**。`evidence/ids.py:_normalize_text()` 仍是
  Evidence 内容哈希的唯一真源；本模块的 `collapse()` 只在与它**语义兼容**的前提下
  提供公开的只读等价物，不反向影响任何已存在的 `content_hash`。
- **不得删除正常正文字符来抬高覆盖率**。归一化只能做空白/宽度/不可见字符层面的
  确定性折叠：`fold()` 只做全角 → 半角映射（不改字符个数），`strip_invisible()`
  只删**不可见**字符（软连字符、零宽、BOM），不删任何可见正文。
- **不得变成模糊匹配或语义改写**。没有同义词、没有编辑距离阈值、没有
  "近似即等价"的判定；`chars_equivalent()` 是**逐字符**比较，不做模糊容忍。

`tight()` 是计划 §3.1 的对齐度量所要求的形态：**删除全部空白**，
因此"两引擎只在空白落点上不同"的残差会被正确吸收为 `engine_artifact`，
而不是被误判为缺失正文。

版本：规则语义由 `versions.NORMALIZATION_VERSION` 单一控制。任何会改变
`tight()` / `fold()` 结果的修改都必须提升该常量；本模块**不得**内联版本字面量
（`versions.py` 是本包内唯一允许出现版本字面量的文件）。
"""

from __future__ import annotations

import argparse
import json
import unicodedata

from document_structure import versions as V

#: 归一化规则版本（复用既有常量，本模块不新增版本字面量）。
NORMALIZATION_RULE_VERSION: str = V.NORMALIZATION_VERSION

#: 不可见字符：软连字符、零宽空白、BOM。只删除**不可见**字符，不删视觉可见正文。
#: 用码位构造，源码里不出现不可见字符本身（规则边界对审查者可见）。
INVISIBLE_CODEPOINTS: tuple[int, ...] = (
    0x00AD,  # SOFT HYPHEN
    0x200B,  # ZERO WIDTH SPACE
    0x200C,  # ZERO WIDTH NON-JOINER
    0x200D,  # ZERO WIDTH JOINER
    0xFEFF,  # ZERO WIDTH NO-BREAK SPACE / BOM
)
INVISIBLE_CHARS: tuple[str, ...] = tuple(chr(c) for c in INVISIBLE_CODEPOINTS)

#: 解析质量下限：坏字符（替换符 / 控制符 / 私用区 / 代理区）比例超过此值即失败。
MAX_BAD_CHAR_RATIO: float = 0.02

_ZERO_WIDTH = frozenset(INVISIBLE_CHARS)


def tight(text: str) -> str:
    """删除**全部**空白字符（计划 §3.1 的对齐度量形态）。"""
    return "".join(text.split())


def collapse(text: str) -> str:
    """把连续空白折叠为单个空格并去首尾（与 `canonical_text` 语义一致）。"""
    return " ".join(text.split())


def fold(text: str) -> str:
    """全角 → 半角映射（**等长**，不删除字符，不做 NFKC 语义合并）。

    只处理三类有明确语法含义的宽度形态：

    - ASCII 全角区 U+FF01..U+FF5E → U+0021..U+007E（减 0xFEE0）；
    - 表意空格 U+3000 → U+0020；
    - 半角片假名区 U+FF61..U+FF9F：**不折叠**（折叠会改变字形语义，宁可不折）。
    """
    out = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def strip_invisible(text: str) -> str:
    """删除软连字符 / 零宽字符 / BOM（不可见字符，不改变可见正文）。"""
    return "".join(ch for ch in text if ch not in _ZERO_WIDTH)


def is_bad_char(ch: str) -> bool:
    """替换符、控制符（除 \\t\\n\\r）、私用区、代理区、未分配 → 解析坏字符。"""
    code = ord(ch)
    if ch == "�":
        return True
    if code in (0x09, 0x0A, 0x0D):
        return False
    cat = unicodedata.category(ch)
    return cat in ("Cc", "Cf", "Co", "Cs", "Cn") and ch not in _ZERO_WIDTH


def bad_char_ratio(text: str) -> float:
    """坏字符占比（空文本为 0.0）。用于"解析质量不足"fail-fast 判定。"""
    if not text:
        return 0.0
    return sum(1 for ch in text if is_bad_char(ch)) / len(text)


def text_stats(text: str) -> dict:
    """确定性文本统计（只读诊断；不含时间、不含随机、不依赖 dict 迭代顺序）。"""
    return {
        "chars": len(text),
        "tight_chars": len(tight(text)),
        "whitespace_chars": sum(1 for ch in text if ch.isspace()),
        "invisible_chars": sum(1 for ch in text if ch in _ZERO_WIDTH),
        "fullwidth_chars": sum(1 for ch in text if 0xFF01 <= ord(ch) <= 0xFF5E),
        "bad_chars": sum(1 for ch in text if is_bad_char(ch)),
        "replacement_chars": text.count("�"),
        "cjk_chars": sum(1 for ch in text
                         if 0x4E00 <= ord(ch) <= 0x9FFF),
        "ascii_letters": sum(1 for ch in text
                             if ch.isascii() and ch.isalpha()),
        "digits": sum(1 for ch in text if ch.isdigit()),
    }


def chars_equivalent(a: str, b: str, *, drop_whitespace: bool = True) -> bool:
    """逐字符等价判定：**先**去空白（可选）、**再**全角折叠，然后**严格相等**。

    这是"等价"的唯一定义：没有编辑距离、没有模糊阈值、没有同义改写。
    软连字符等不可见字符总是先被删除（它们不承载可见信息）。
    """
    left, right = strip_invisible(a), strip_invisible(b)
    if drop_whitespace:
        left, right = tight(left), tight(right)
    return fold(left) == fold(right)


def self_check() -> dict:
    """模块自检：确定性 + 不破坏正文字符 + 与既有折叠语义兼容。"""
    issues: list[str] = []
    if tight(" a b\t\nc ") != "abc":
        issues.append("tight 未删除全部空白")
    if collapse(" a b\t\nc ") != "a b c":
        issues.append("collapse 未折叠为单空格")
    if fold("Ａ１（）") != "A1()":
        issues.append("fold 未做全角到半角映射")
    if len(fold("Ａ１（）")) != len("Ａ１（）"):
        issues.append("fold 改变了字符个数（不得删除正文字符）")
    sample = "示例公司 2024 年年度报告" + chr(0x00AD) + "全文"
    if tight(strip_invisible(sample)) != "示例公司2024年年度报告全文":
        issues.append("不可见字符处理破坏正文")
    if not chars_equivalent("第 1 页", "第　1　页"):
        issues.append("空白差异未被等价吸收")
    if chars_equivalent("第 1 页", "第 2 页"):
        issues.append("等价判定过宽（不得成为模糊匹配）")
    if bad_char_ratio("正常 abc") != 0.0:
        issues.append("坏字符比例误报")
    if NORMALIZATION_RULE_VERSION != V.NORMALIZATION_VERSION:
        issues.append("归一化版本常量漂移")
    return {
        "ok": not issues,
        "version": NORMALIZATION_RULE_VERSION,
        "issues": tuple(issues),
        "invisible_chars": INVISIBLE_CHARS,
        "max_bad_char_ratio": MAX_BAD_CHAR_RATIO,
    }


def _main(argv=None) -> int:
    """`python -m document_structure.normalization --validate-only`（无副作用）。"""
    p = argparse.ArgumentParser(description="TS2 归一化原语自检（只读，不写文件）")
    p.add_argument("--validate-only", action="store_true",
                   help="只做自检，不读取任何输入，不写任何输出")
    p.add_argument("--json", action="store_true", help="以 JSON 输出自检结果")
    args = p.parse_args(argv)
    if not args.validate_only:
        p.error("必须显式给出 --validate-only（本模块没有任何写操作）")
    report = self_check()
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())

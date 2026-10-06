"""离线替身的**组织句**回归：接缝里的连接语不得与它接的那句话自己的开头重复。

## 这条回归盯着什么

离线替身把同一主题里**不带句末标点**的连续 Claim 用中性并列连接语串成一句
（`ACC._organizer_sentence`）。连接语只从冻结的 `NS.NEUTRAL_CONNECTORS`（`此外` / `同时`）
里轮转取，且必须能通过 `NS.unauthorized_surfaces_within_claims`。

现场是这么读坏的：材料正文常自己就以 `此外，` / `同时，` 开头（那是原文的行文），替身再在
接缝里补一个同一个词，读者看到的是

    公司电池材料产品主要包括锂盐、前驱体及正极材料等，此外，此外，为进一步保障……

`此外，此外，` 不是任何一条判据会拦的东西——它既不是新增事实表面，也不是关系性连接语，
更不是双句容器。**它只是读不通**。中性并列语只负责把两条已定稿的断言接起来，重复不是它
该付出的代价，因此这一层必须由替身自己避开。

## 避不开时**照常写出正文**

`此外` 与 `同时` 都被后文自己用了开头时（材料两句话正好都这么起头），退回原样轮转。
这一档**不是**放宽任何判据：连接语仍取自同一张封闭词表、仍要过同一条判据。少写一句话
才是把内容丢掉——那是替身在替链决定「这段可以少说一句」，它没有这个立场。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from evaluation import run_m930_3_acceptance as ACC  # noqa: E402
from sections import narrative_schema as NS  # noqa: E402

#: 一句真实的材料正文式断言（不带句末标点：只有不带标点的连续 Claim 才走串句这条路）。
A = "公司电池材料产品主要包括锂盐、前驱体及正极材料等"
#: 自己就以 `此外，` 开头的下一条——这正是现场那处重复的来源。
B_AFTERWARDS = "此外，为进一步保障电池生产所需的上游关键资源及材料供应，公司参与电池矿产资源的投资"
#: 自己就以 `同时，` 开头的下一条。
B_SIMULTANEOUSLY = "同时，公司的储能电池广泛应用于表前储能和表后储能领域"


def _chunk(*texts: str) -> list[dict]:
    return [{"claim_id": f"claim_{position}", "text": text}
            for position, text in enumerate(texts)]


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
    # 1. 判据本身：只看接缝之后的那些话、只看**开头**、逐字
    # ==================================================================
    check(ACC._connector_would_repeat("此外", ["此外，x", "y"]) is False,
          "接缝只发生在第 2 条起：第 1 条在句首、前面没有接缝，判据不得把它算进来"
          "（算了就会把「整段第一句正好以此外开头」误判成重复）")
    check(ACC._connector_would_repeat("此外", ["x", "此外，y"]) is True,
          "被接的那句话自己以同一个词开头 ⇒ 判为会重复")
    check(ACC._connector_would_repeat("此外", ["x", "y 此外 z"]) is False,
          "判据是**开头**逐字，不是「含有」：中间出现的同一个词是材料自己的行文，"
          "与接缝无关（按「含有」判会把正常行文也躲开）")
    check(ACC._connector_would_repeat("同时", ["x", "此外，y"]) is False,
          "判据按**这一个**连接语判，不是「任一连接语」（这里重复的是此外，不是同时）")

    # ==================================================================
    # 2. 现场那处重复必须消失，而内容一字不少
    # ==================================================================
    sentence = ACC._organizer_sentence(NS, _chunk(A, B_AFTERWARDS), 0)
    text = sentence["text"]
    check("此外，此外" not in text,
          f"接缝里的连接语不得与被接的那句话自己的开头重复（实得 {text[:80]!r}）")
    check(all(part in text for part in (A, B_AFTERWARDS)),
          "避开重复不得以改字为代价：两条 Claim 必须仍然**逐字**在场（§七 1）")
    check(sentence["claim_ids"] == ["claim_0", "claim_1"],
          "句子声明的 Claim 与传进来的那条不差一条（少一条就是把内容丢掉）")
    check(text.endswith("。") and text.count("。") == 1,
          f"一句就是一个句子：句末标点至多出现在末尾（实得 {text[-20:]!r}）")
    # `NS.NEUTRAL_CONNECTORS` 的每一项都**自带尾部逗号**（它们是句首连接语），所以这里比的是
    # 「`<Claim>，<连接语>` 前缀」而不是「连接语去掉逗号之后的那个词」。
    seam_heads = [c for c in NS.NEUTRAL_CONNECTORS if text.startswith(A + "，" + c)]
    check(len(seam_heads) == 1,
          f"接缝里落下的必须是冻结中性并列表里的**某一项**（实得开头 {text[len(A):len(A) + 12]!r}）")

    # ==================================================================
    # 3. 两条连接语都被后文用掉开头时：照常写出正文（退回原样轮转）
    # ==================================================================
    both = ACC._organizer_sentence(NS, _chunk(A, B_AFTERWARDS, B_SIMULTANEOUSLY), 0)
    check(all(part in both["text"] for part in (A, B_AFTERWARDS, B_SIMULTANEOUSLY)),
          "两条中性连接语都避不开时，必须**照常写出正文**：少写一句是把内容丢掉，"
          "替身没有立场替链决定这段可以少说一句")
    check(len(both["claim_ids"]) == 3,
          f"三条 Claim 一条都不能少（实得 {len(both['claim_ids'])}）")

    # ==================================================================
    # 4. 不被冻结判据放宽：组装出的整句仍要过 `unauthorized_surfaces_within_claims`
    # ==================================================================
    # 替身能返回这一句，本身就说明它过了那条判据（否则会一路轮转到底并抛）。这里再独立复算
    # 一次：把「替身返回了」与「这一句真的过判据」分成两件事，将来谁改了返回条件，这里先红。
    check(not NS.unauthorized_surfaces_within_claims(
        text, [A, B_AFTERWARDS]),
          "替身组装出的整句必须仍然过冻结判据（不得为了读得通而放宽任何一条判据）")

    # 反例：连接语若被换成**关系性**措辞（断言两条断言之间存在对照/递进/归纳），冻结判据必须
    # 命中。这条钉住的是「替身不越权下关系结论」——它只看得到已定稿的 Claim 文本，
    # 材料有没有说过这个关系它无从知道。判据本身是 `NS.relation_connector_hits`（封闭表逐字
    # 命中），与中性并列表**不相交**（下面一并钉住），否则「避开重复」的候选池里会混进关系语。
    relational = A + "，另一方面，" + B_AFTERWARDS + "。"
    check(bool(NS.relation_connector_hits(relational)),
          "关系性连接语（其中/另一方面/在此基础上/综上）必须被冻结判据命中："
          "材料有没有说过这个关系，替身无从知道")
    check(not (set(NS.NEUTRAL_CONNECTORS) & set(NS.RELATION_ASSERTING_CONNECTORS)),
          "中性并列词表与关系性词表必须**不相交**：两个池子一重叠，"
          "「替身只写中性并列」这句话就不再由词表本身成立")

    result = {"passed": passed, "failed": failed, "skipped": 0, "details": details}
    return result


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

"""M930-4 独立审查与确定性 Assurance（§7）。

本包是**第三套** dependency/content identity 的宿主（`DESIGN_V2.md` §0.13 第 4.2
条、§11.2）：绑定 `report_version` 的 `ReviewIssue[]` 与聚合状态。它与 Pack
qualification、Section/Writer/Evaluator 两套身份**分开管理**：本包的任何对象
**不写入 Pack**，也**不进入 Pack content identity**。

公开面（§7.3）：

- `schema`：`ReviewIssue` / `ReviewInputBundle` / `HardGateIssue` / `HardGateResult`
  / `ReviewerRunRecord` / `AssuranceResult`，全部内容寻址、版本化、拒绝 unknown 字段。
- `gates`：确定性硬门（可审查性与放行性两个**正交**布尔）。
- `context_builder`：从权威产物独立构造隔离只读上下文。
- `reviewer`：一次结构化调用，非法输出 fail-closed。
- `controller`：按固定顺序做确定性聚合，无 Writer / research / human acceptance /
  formal closure 写接口。
- `artifacts`：按既有 `sections/backbone_artifacts.py` 的 index/manifest 约定持久化。

依赖方向：本包只依赖标准库与 `assurance.schema` 自身；**不**反向依赖 `sections`、
`harness`、`evaluation` 的私有内部（需要其对象时只读取公开身份字段）。
"""

from __future__ import annotations

__all__ = ["schema"]

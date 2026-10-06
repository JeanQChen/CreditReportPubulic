"""人批准一次真实运行：为**一个已落盘、尚未启动**的演示 run 写一份一次性授权（`cra-2`）。

这是本批里**唯一**能造出真实写作/审阅授权的入口。它故意不是页面按钮、不是配置项、不是
环境变量：授权应当是一次可追溯的人的动作，而不是某个界面上顺手点出来的一格状态。

它做什么：

1. 读 `--run-input` 指向的本 run 上传目录（`cri-1` 的 PDF 绑定 + `cfi-1` 的 XLSX 绑定 +
   页面写的 `run_request.json`）；
2. 从**代码与配置**取模型身份（`config.LLM_MODEL`）、prompt 版本与上限（`sections.cited_budget`
   的同一份政策）——这些**不由人手打**，免得批准的是纸上数字；
3. 从**只读财务库**取当前有效快照的身份与口径（含完整 `source_versions`），并核对它就是本 run
   财务节实际会消费的那一条；从请求面声明的 profile 取 v2 范围身份三元组；
4. 把上面这些 + 三份上传 PDF 的 `(document_id, sha256)` + 三份上传 XLSX 的
   `(source_version, sha256)` 一起写成一份凭据；
5. 打印这份凭据的完整身份，供人核对后再去点「启动已授权运行」。

它**不**启动运行、不发起任何请求、**不写共享库**（读财务库时只开 `mode=ro`），也**不**授权
第二次：同一 run_id 已有一份授权即拒；凭据被链消费一次之后即失效。

    python scripts/authorize_cited_run.py \\
        --run-input data/run_inputs/m930_3_cited_upload_20261005T010203Z \\
        --granted-by "张三（本轮演示）"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from sections import cited_budget as CB                            # noqa: E402
from sections import cited_financial_input as CFI                  # noqa: E402
from sections import cited_run_authorization as CRA                # noqa: E402
from sections import cited_run_input as CRI                        # noqa: E402

EVIDENCE_DB = REPO / "data" / "evidence.db"
FINANCIAL_DB = REPO / "data" / "financial_v2.db"


def _request_face(staged: Path) -> dict:
    target = staged / CRI.REQUEST_NAME
    if not target.is_file():
        raise SystemExit(
            f"{staged} 里没有 {CRI.REQUEST_NAME}：它不是由演示页「开始生成」创建的一次运行。"
            "授权只发给这样的一次运行（页面写了它请求了什么，凭据才绑得上）")
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{CRI.REQUEST_NAME} 读不开：{exc}") from exc


def _profile_path(request: dict) -> str:
    """请求面声明的 profile 路径。**必填**：授权绑的就是某一版范围的指纹。"""
    declared = str(request.get("profile_path") or "").strip()
    if not declared:
        raise SystemExit(
            f"{CRI.REQUEST_NAME} 没有记下 `profile_path`：本批的真实运行必须声明它跑的是"
            "哪一版 demo scope 范围（v2 profile 含 `fin_balance_structure`），"
            "没有这一读数就没有可绑定的范围身份")
    return declared


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-input", required=True,
                        help="本 run 的上传目录（`data/run_inputs/<run_id>/`）")
    parser.add_argument("--granted-by", required=True,
                        help="谁批准的这一次运行。空字符串即拒：没有「谁批准的」就不是人的批准")
    parser.add_argument("--authorization-root", default=None,
                        help=f"授权写到哪。缺省 = `data/{CRA.AUTHORIZATION_DIRNAME}/`")
    args = parser.parse_args(argv)

    staged = Path(args.run_input).resolve(strict=False)
    if not staged.is_dir():
        raise SystemExit(f"运行输入目录不存在：{staged}")
    request = _request_face(staged)
    run_id = str(request.get("run_id") or "")
    subject = str(request.get("subject") or "")
    subject_name = str(request.get("subject_name") or "").strip()
    mode = str(request.get("mode") or "")
    section_ids = tuple(str(s) for s in (request.get("section_ids") or ()))
    if not run_id or not subject or not section_ids:
        raise SystemExit(f"{CRI.REQUEST_NAME} 缺 run_id／subject／section_ids："
                         "没有这三样就没有可绑定的运行身份")
    if not subject_name:
        raise SystemExit(
            f"{CRI.REQUEST_NAME} 没有记下 `subject_name`：财务快照的主体名称是「声明 + 核对」，"
            "授权不从库里挑一个名字补上")
    if mode != CRA.AUTHORIZED_MODE:
        raise SystemExit(
            f"这一份请求面记的是模式 {mode!r}：一次性授权只发给 {CRA.AUTHORIZED_MODE!r} 运行。"
            "离线运行不发任何请求，给它发授权只会把读数语义搅浑")

    #: 模型、prompt 版本与上限**从代码取**：批准的是「按当前获批配置跑一次」，
    #: 不是「按这张纸上抄的数字跑一次」。
    from config import LLM_MODEL as PROJECT_WRITER_MODEL

    model = str(PROJECT_WRITER_MODEL or "").strip()
    if not model:
        raise SystemExit("配置里没有已批准的写作模型（`config.LLM_MODEL` 为空）："
                         "没有模型身份就没有可绑定的授权")
    policy = CB.cited_call_budget_policy(approved_model=model)
    CB.assert_caps_expressed(policy)
    CB.assert_sections_approved(section_ids)

    try:
        binding = CRI.load_run_input(
            staged, declared=CRI.registered_documents(EVIDENCE_DB, subject))
    except CRI.CitedRunInputError as exc:
        raise SystemExit(f"运行输入无法读回（fail-closed）：{exc}") from exc

    #: 财务输入：先取**当前有效快照**，再要求上传的三份 XLSX 与它逐份对上。反过来（先信上传、
    #: 再去库里找一条"差不多"的快照）会把「A 快照被 B 快照顶替」写成一次成功授权。
    try:
        snapshot = CFI.current_snapshot_identity(
            FINANCIAL_DB, subject=subject, subject_name=subject_name)
        declared_sources = CFI.declared_financial_sources(
            FINANCIAL_DB, version_ids=[v for v, _ in snapshot.source_versions])
        financial_binding = CFI.load_financial_input(
            staged, declared=declared_sources, snapshot=snapshot)
    except CFI.CitedFinancialInputError as exc:
        raise SystemExit(f"财务输入无法读回（fail-closed）：{exc}") from exc

    profile_path = _profile_path(request)
    try:
        from planning import demo_scope as SC_scope
        profile = SC_scope.load_demo_scope_profile(REPO / profile_path
                                                   if not Path(profile_path).is_absolute()
                                                   else profile_path)
    except Exception as exc:  # noqa: BLE001 - 任何 profile 读取失败都必须停在授权之前
        raise SystemExit(f"请求面声明的 demo scope profile 读不出来（{profile_path}）：{exc}")

    try:
        authorization = CRA.build_authorization(
            granted_by=args.granted_by, run_id=run_id, mode=mode, subject=subject,
            section_ids=section_ids, model=model, policy=policy, binding=binding,
            financial_binding=financial_binding, profile=profile)
    except CRA.CitedAuthorizationError as exc:
        raise SystemExit(f"授权无法建立：{exc}") from exc

    root = Path(args.authorization_root) if args.authorization_root else CRA.default_root(REPO)
    try:
        target = CRA.write_authorization(root, authorization)
    except CRA.CitedAuthorizationError as exc:
        raise SystemExit(f"授权无法落盘：{exc}") from exc

    print("一次性授权已落盘（链只读它，且只消费一次）：")
    print(f"  凭据        {target}")
    print(f"  身份        {authorization.describe()}")
    print(f"  批准人      {authorization.granted_by}　·　{authorization.granted_at_utc}")
    print(f"  材料指纹    {binding.manifest_sha256[:16]}…")
    print(f"  财务指纹    {financial_binding.binding_sha256[:16]}…")
    print("  提醒        本凭据只够启动**这一次** run；重复点击或另一个会话都消费不动它。"
          "它不解锁任何别的运行，也不改变既有预算门。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

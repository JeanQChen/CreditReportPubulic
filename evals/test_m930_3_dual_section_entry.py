# -*- coding: utf-8 -*-
"""M930-3 双节演示入口：节序**只由所选 demo scope profile 派生**。

本模块只测一件事：runner 跑的节集与它做会计/报告所按的节序，**都由同一份 profile 派生**，
因此「双节 profile 跑出来的 run」与「旧三节 run」在产物里可逐字区分，而不是靠目录名去猜。

正例（行为面，不打源码字面量）：

* 不传 `--demo-scope-profile` ⇒ 路径仍是 `DEFAULT_PROFILE_PATH`，节序**逐字等于**旧三节
  （向后兼容：既有 run 的读数一个字都不变）；
* 传双节 profile ⇒ 绑定后的节序 = 该 profile 的 `selected_section_ids`，且**逐字等于**
  `project_contract_v2_scope(...).report_plan.section_tasks` 的节序——这是承重不变式：
  「跑哪几节」与「按什么次序记账」不可能各说各话；
* 绑定可逆：双节之后换回三节 profile，节序回到三节（不是单向拧死）。

反例：

* `selected_section_ids` 为空 / 有重复 ⇒ `AcceptanceRefusal`，不退回缺省；
* 未知选项不被吞：`--demo-scope-profile` 必须真的被解析（用「缺 `--subject` 时的拒绝理由」
  来证，而不是断言源码里有这个字符串）；
* 信任根按**本进程所选** profile 记：双节 profile 与三节 profile 的
  `demo_scope_profile` 哈希不同，因此拿旧三节 profile 去复核一份双节 run 会如实报不符，
  而不是静默判「一致」。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation import run_m930_3_acceptance as ACC  # noqa: E402
from planning import demo_scope as SC  # noqa: E402
from planning import schema as PS  # noqa: E402

DUAL_PROFILE = "templates/demo_scopes/interview_backbone_dual_section_v1.yaml"


class _Profile:
    """只带节序的最小 profile 替身（专测派生与拒绝，不碰冻结资产）。"""

    def __init__(self, section_ids, profile_id="stub"):
        self.selected_section_ids = tuple(section_ids)
        self.profile_id = profile_id


def _projected_section_order(profile) -> list[str]:
    from contracts.loader_v2 import load_contract_v2

    contract = load_contract_v2(str(ACC.REPO / profile.contract_asset))
    business = PS.ReportJobInput(
        job_id="job_dual_section_entry_probe", company_id="__probe__",
        company_name="__probe__", credit_type="general", report_as_of="1970-01-01",
        contract_version="v2")
    source_inputs = {
        "case_input_id": "case_dual_section_entry_probe", "document_id": "__probe__",
        "document_version": "v0", "raw_pdf_sha256": "0" * 64,
        "current_evidence_set_version": "es-probe",
        "substrate_dependency_versions": {
            k: f"{k}-v1" for k in SC.SUBSTRATE_DEPENDENCY_KEYS},
        "external_policy_snapshot_id": None,
        "budget_policy_id": profile.budget_policy_id,
        "budget_policy_version": profile.budget_policy_version,
        "model_policy_id": "demo_model_policy_v1", "code_fingerprint": "1" * 64,
    }
    manifest = SC.build_scope_input_manifest(profile, business, source_inputs)
    projection = SC.project_contract_v2_scope(contract, profile, manifest)
    return [t.section_id for t in projection.report_plan.section_tasks]


def run() -> dict:
    checks: list[tuple[str, bool, str]] = []

    def check(ok: bool, label: str) -> None:
        checks.append((label, bool(ok), ""))

    def check_eq(got, want, label: str) -> None:
        checks.append((label, got == want, f"实得 {got!r} ≠ 期望 {want!r}"))

    saved_path = ACC._DEMO_SCOPE_PROFILE_PATH
    saved_order = ACC.SECTION_ORDER
    saved_id = ACC._ACTIVE_PROFILE_ID
    try:
        # ---- §1 缺省即旧三节：路径与节序都不变 ----
        ACC._DEMO_SCOPE_PROFILE_PATH = None
        check_eq(ACC.active_profile_path(), SC.DEFAULT_PROFILE_PATH,
                 "不传 --demo-scope-profile 时，实际路径就是 DEFAULT_PROFILE_PATH"
                 "（缺省不复制它的字面量）")
        check_eq(ACC.SECTION_ORDER, ("company", "financial", "industry"),
                 "模块级节序的**缺省值**仍是旧三节（本批没有改既有行为）")
        default_profile = SC.load_demo_scope_profile(ACC.active_profile_path())
        check_eq(ACC.bind_section_order(default_profile), ("company", "financial", "industry"),
                 "把缺省 profile 绑上去，节序逐字等于旧三节")

        # ---- §2 双节：节序由 profile 派生，且等于投影的节序 ----
        ACC._DEMO_SCOPE_PROFILE_PATH = DUAL_PROFILE
        check_eq(ACC.active_profile_path(), DUAL_PROFILE, "传入后实际路径就是它")
        dual = SC.load_demo_scope_profile(DUAL_PROFILE)
        check_eq(dual.profile_id, "interview_backbone_dual_section",
                 "双节 profile 的身份（不是靠文件名认的）")
        bound = ACC.bind_section_order(dual)
        check_eq(bound, ("company", "financial"),
                 "绑定后的节序 = 该 profile 的 selected_section_ids")
        check_eq(list(ACC.SECTION_ORDER), list(bound), "模块级节序被就地重绑到同一个值")
        check_eq(ACC._active_profile_id(), "interview_backbone_dual_section",
                 "报告要记的 profile 身份同步为双节")
        check("industry" not in ACC.SECTION_ORDER,
              "旧三节的 industry **不在**双节 run 的节序里（否则报告会多出一节全空的会计行）")
        check_eq(list(bound), _projected_section_order(dual),
                 "承重不变式：绑定后的节序**逐字等于**冻结 Contract 投影出的 "
                 "report_plan.section_tasks 节序——「跑哪几节」与「按什么次序记账」同源")
        check_eq(len(SC.load_demo_scope_profile(DUAL_PROFILE).selected_aspect_ids),
                 len(dual.selected_aspect_ids),
                 "同一 profile 两次加载给出一致的选面（绑定不改 profile 本身）")

        # ---- §3 可逆：换回三节 profile，节序回到三节 ----
        check_eq(ACC.bind_section_order(default_profile),
                 ("company", "financial", "industry"),
                 "绑定可逆：三节 profile 绑回来即回到三节（不是单向拧死）")
        check_eq(list(ACC.bind_section_order(dual)), list(_projected_section_order(dual)),
                 "再绑回双节，仍与投影一致")

        # ---- §4 反例：空 / 重复一律拒绝，不退回缺省 ----
        for bad, label in (((), "空集"), (("company", "company"), "重复项")):
            try:
                ACC.section_order_for(_Profile(bad))
            except ACC.AcceptanceRefusal:
                check(True, f"selected_section_ids {label} ⇒ AcceptanceRefusal（fail-closed）")
            except Exception as exc:  # noqa: BLE001
                check(False, f"selected_section_ids {label} 抛了别的异常："
                             f"{type(exc).__name__}: {exc}")
            else:
                check(False, f"selected_section_ids {label} 竟然通过了")
        check_eq(ACC.section_order_for(_Profile(("company",))), ("company",),
                 "单节 profile 也被如实派生（机制不假设节数）")

        # ---- §5 未知选项不被吞：该 flag 真的被解析到 ----
        rc = ACC._main(["--demo-scope-profile", DUAL_PROFILE])
        check_eq(rc, 0, "只给 --demo-scope-profile、不给 --subject ⇒ 走既有拒绝路径（rc=0）")
        check_eq(ACC._DEMO_SCOPE_PROFILE_PATH, DUAL_PROFILE,
                 "--demo-scope-profile 真的被解析并落到本进程的取值上"
                 "（若它是个未知选项，argparse 会直接 SystemExit(2) 而不是走到拒绝理由）")

        # ---- §6 信任根按**本进程所选** profile 记，两者不同 ----
        ACC._DEMO_SCOPE_PROFILE_PATH = None
        default_roots = dict(ACC._trust_roots(SC.load_demo_scope_profile(
            ACC.active_profile_path())))
        ACC._DEMO_SCOPE_PROFILE_PATH = DUAL_PROFILE
        dual_roots = dict(ACC._trust_roots(SC.load_demo_scope_profile(
            ACC.active_profile_path())))
        check_eq(default_roots["demo_scope_profile"],
                 (ACC.REPO / SC.DEFAULT_PROFILE_PATH),
                 "缺省时信任根里的 profile 是旧三节那一份")
        check_eq(dual_roots["demo_scope_profile"], ACC.REPO / DUAL_PROFILE,
                 "双节时信任根里的 profile 是双节那一份")
        check(ACC._sha256_file(default_roots["demo_scope_profile"])
              != ACC._sha256_file(dual_roots["demo_scope_profile"]),
              "两份 profile 的字节哈希不同 ⇒ 拿旧三节 profile 去复核双节 run 会如实报不符，"
              "而不是静默判「信任根未变」")

        # ---- §7 主题研究节的分派：只由节序派生，未注册的节一律拒绝 ----
        # 这是 acc-36 实测出来的缺陷面：`RealEnvironment._build` 曾把节集写死成
        # 「公司 + 行业」这一对，双节 profile（公司 + 财务）一跑就在 `tasks['industry']`
        # 上 KeyError —— 一个跑不起来的档位不是一个档位。
        ACC.bind_section_order(default_profile)
        check_eq(ACC.topic_section_ids(), ("company", "industry"),
                 "三节 profile 下，主题研究节 = 节序里除财务以外的节（保序）")
        check_eq([s for s, _ in ACC.topic_section_workers()], ["company", "industry"],
                 "三节 profile 下逐节派到 worker；财务**不在**这张表里（它有独立相位）")
        check_eq(list(ACC.topic_section_ids(("company", "financial"))), ["company"],
                 "双节节序下主题研究节只剩 company：industry 是**缺席**，不是被跳过")

        saved_order_for_s7 = ACC.SECTION_ORDER
        ACC.bind_section_order(dual)
        pairs = ACC.topic_section_workers()
        check_eq([s for s, _ in pairs], ["company"],
                 "双节 profile 下**只**派一节（若仍写死「公司 + 行业」，这里要么两节、"
                 "要么 KeyError，两者都不等于一节）")
        # 正例：这一节真的拿到了公司 worker **模块对象**（不是字符串、不是别的节的替身）。
        from sections import company_worker as _CW  # noqa: E402
        check(pairs and pairs[0][1] is _CW,
              "派给 company 的就是 `sections.company_worker` 模块本身（按节身份查表，不按位置）")
        # 反例：节序里出现未注册的节 ⇒ 拒绝，既不静默跳过也不拿别的 worker 顶替。
        try:
            ACC.SECTION_ORDER = ("company", "financial", "__no_such_section__")
            ACC.topic_section_workers()
        except ACC.AcceptanceRefusal:
            check(True, "节序里有未注册的节 ⇒ AcceptanceRefusal（不静默跳过、不顶替）")
        except Exception as exc:  # noqa: BLE001
            check(False, f"未注册的节抛了别的异常：{type(exc).__name__}: {exc}")
        else:
            check(False, "未注册的节竟然通过了：静默跳过会让报告少一节而看不出来")
        ACC.SECTION_ORDER = saved_order_for_s7
    finally:
        ACC._DEMO_SCOPE_PROFILE_PATH = saved_path
        ACC.SECTION_ORDER = saved_order
        ACC._ACTIVE_PROFILE_ID = saved_id

    details = [f"{'PASS' if ok else 'FAIL'}: {label}{('  ' + note) if note else ''}"
               for label, ok, note in checks]
    failed = [d for d in details if d.startswith("FAIL")]
    return {"passed": len(details) - len(failed), "failed": len(failed), "skipped": 0,
            "details": details}


def main() -> dict:
    result = run()
    for line in result["details"]:
        print(line)
    print(f"{result['passed']} passed, {result['failed']} failed, {result['skipped']} skipped")
    return result


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    outcome = main()
    raise SystemExit(1 if outcome["failed"] else 0)

"""Eval: M930-1 Demo Backbone artifact 骨架（create-only / 严格读回 / 无哈希环）。

用法: python -m evals.test_demo_backbone_artifacts

覆盖（M930-1 任务书 §十一 ``evals.test_demo_backbone_artifacts``）：
- create-only 正常写入 + 严格读回；
- 目标 run 目录已存在即拒绝（绝不覆盖）；
- manifest / index 无环：索引不列自身与 manifest，manifest 记录索引 SHA256，
  索引不回写 manifest 哈希；
- 篡改 / 截断 / 缺失 / 多余 / 重复；
- 哈希 / 大小 / schema / 版本不符；
- 成员与索引条目的工件声明（role / producer_step / 工件类型 / schema 版本）闭合校验，
  未知、空值、与 role 不相称、索引↔成员不一致一律拒绝；不得由文件后缀猜类型；
- 绝对路径、``..``、路径逃逸、多余目录（含空目录）、逐级 symlink / junction /
  reparse point 逃逸（**注入谓词**确定性覆盖，不依赖本机 symlink 权限、不 skip）；
- 索引 / manifest 自引用；
- 临时目录失败清理不超过边界（不删本次未创建的目录）；
- validate-only 不改一个字节；
- loader 不建数据库、不产生额外文件。

M930-3D 追加（C6 / §16.8 索引 wire 版本分派）：
- v1 词表逐字冻结且是 current 的**真子集**（只追加角色 / 类型 / 步骤，既有条目不动）；
- v2 current 索引可承载全部新链角色并逐字节严格读回；
- 手工落一个真实 v1 run：current reader 与 legacy 只读入口都能回放且**逐字节不改动**
  （不升级、不重写、不迁移），v1 索引的 ``schema_version`` 原样保留；
- 反向：v1 索引声明 v2 独有角色 / 未知索引版本 / legacy 入口读 current 索引一律拒绝；
- 旧对象 wire 版本串（``claim-1`` / ``section-result-1`` / ``narr-3`` / 研究侧 ``5``/``4``）
  不得当作工件 schema 版本（两条版本轴不混用）。

公司无关：不引入公司名 / 证券代码 / 固定页码 / gold 特判。
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import tokenize
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contracts.loader_v2 import load_contract_v2
from planning import demo_scope as DS
from sections import backbone_artifacts as BA
from sections import backbone_schema as BS

_RUN_ID = "demo_backbone_20260920T000000Z"


def _code_only(path: Path) -> str:
    """剥掉注释与字符串后重建可执行代码（token 原样拼接，保留 ``os.fsync`` 形态）。"""
    out: list[str] = []
    for tok in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        out.append(tok.string)
    return "".join(out)


def _fixture():
    """真实前序对象：合法 DemoRunIdentity + ResolvedDemoScopeManifest。"""
    profile = DS.load_demo_scope_profile()
    business = DS._business_input("job_demo_backbone_0001")
    manifest_in = DS.build_scope_input_manifest(profile, business, DS._source_inputs())
    run_identity = DS.build_demo_run_identity(
        {"run_id": _RUN_ID, "attempt": 1, "started_at": "2026-09-20T00:00:00Z"},
        manifest_in, f"evaluation/results/{_RUN_ID}")
    contract = load_contract_v2(str(DS.REPO_ROOT / profile.contract_asset))
    projection = DS.project_contract_v2_scope(contract, profile, manifest_in)
    scope_manifest = DS.resolve_demo_scope_manifest(run_identity, manifest_in, projection, {
        "live_page_layout_ids": [], "live_alignment_ids": [], "live_outline_ids": [],
        "live_span_snapshot_ids": [], "pack_ids": [], "external_snapshot_ids": [],
        "financial_fact_pack_artifact_id": None, "gaps": [],
    })
    closure = BS.FormalPhaseClosureSnapshot.build(
        phases=({"phase_id": "phase4", "status": "open",
                 "source_document": "V2_TODO.md", "recorded_at": "2026-09-20"},),
        source_documents=({"path": "V2_TODO.md", "sha256": "a" * 64},),
        recorded_at="2026-09-20")
    surfaces = BS.DesignSurfaceMatrix.build((
        BS.DesignSurfaceRecord(surface_id="ds.artifact_integrity",
                               axis="design_surface_coverage",
                               title="artifact create-only 与严格读回", status="demonstrated",
                               evidence_kind="artifact",
                               evidence_refs=("sections/backbone_artifacts.py",)),
    ))
    return run_identity, scope_manifest, closure, surfaces


def _members(scope_manifest, surfaces) -> tuple:
    return (
        _member("content/scope.json", "scope_manifest",
                json.dumps(scope_manifest.to_dict(), ensure_ascii=False,
                           sort_keys=True, indent=2) + "\n"),
        _member("content/design_surfaces.json", "design_surfaces",
                json.dumps(surfaces.to_dict(), ensure_ascii=False,
                           sort_keys=True, indent=2) + "\n"),
        _member("content/report_body.md", "report_body",
                "# 空骨架\n\nM930-1 不生成正文。\n"),
    )


def _entry(path: str, role: str = "other", *, sha256: str = "a" * 64, size: int = 1,
           **over) -> BA.ArtifactIndexEntry:
    """构造一条声明完整的索引条目（默认取注册表值，便于单点改坏）。"""
    artifact_type, producer_step, schema_version = BA.ARTIFACT_TYPE_REGISTRY[role]
    kwargs = dict(path=path, sha256=sha256, size=size, role=role,
                  producer_step=producer_step, artifact_type=artifact_type,
                  artifact_schema_version=schema_version)
    kwargs.update(over)
    return BA.ArtifactIndexEntry(**kwargs)


def _member(path: str, role: str = "other", payload: bytes | str = "", **over
            ) -> BA.ArtifactMember:
    """构造成员：三项声明**逐个显式写出**（测试里也不依赖任何默认补齐）。

    ``over`` 只用于显式声明反例（例如故意声明与 role 不符的 ``artifact_type``）。
    """
    artifact_type, producer_step, schema_version = BA.ARTIFACT_TYPE_REGISTRY[role]
    kwargs = dict(path=path, role=role, producer_step=producer_step,
                  artifact_type=artifact_type,
                  artifact_schema_version=schema_version, payload=payload)
    kwargs.update(over)
    return BA.ArtifactMember(**kwargs)


def _tree_fingerprint(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_dir():
            out[rel] = ("dir", 0, 0)
        else:
            st = p.stat()
            out[rel] = ("file", hashlib.sha256(p.read_bytes()).hexdigest(), st.st_mtime_ns)
    return out


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

    def expect_raises(msg: str, fn, needle: str | None = None) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            text = str(exc)
            if needle is None or needle in text:
                check(True, f"{msg} → {type(exc).__name__}: {text[:110]}")
            else:
                check(False, f"{msg} → 抛错但原因不符（期望含 {needle!r}）：{text[:150]}")
        else:
            check(False, f"{msg} → 未抛错（应 fail-closed）")

    run_identity, scope_manifest, closure, surfaces = _fixture()

    def build_manifest(index_sha: str) -> BS.DemoBackboneRunManifest:
        return BS.DemoBackboneRunManifest.build(
            run_identity=run_identity, scope_manifest=scope_manifest,
            artifact_index_sha256=index_sha, formal_closure=closure,
            design_surfaces=surfaces, created_at="2026-09-20T00:00:00Z")

    # ===================== 1. 静态检查：协议常量与 Windows 安全策略 =====================
    code = _code_only(Path(BA.__file__))
    check(BA.ARTIFACT_INDEX_NAME == "artifact_index.json"
          and BA.RUN_MANIFEST_NAME == "run_manifest.json",
          "DAG 两个固定文件名符合任务书 §九")
    check(BA.SAFETY_POLICY_ID and isinstance(BA.SAFETY_POLICY_ID, str),
          f"显式安全策略标识 = {BA.SAFETY_POLICY_ID}")
    check(BA.POSIX_DIRECTORY_FSYNC_AVAILABLE == hasattr(os, "O_DIRECTORY"),
          "POSIX 目录 fsync 可用性如实暴露（Windows 上为 False，不假装执行过）")
    check("os.fsync" in code,
          "每个文件写入后执行 flush + os.fsync(文件句柄)（安全策略的一部分）")
    check("os.replace" in code, "使用同父目录临时目录 + os.replace 原子改名")
    for forbidden in ("sqlite3", "init_db", "INSERT INTO", "connect("):
        check(forbidden not in code,
              f"artifact 模块可执行代码不含 {forbidden!r}（loader 完全只读，不碰数据库）")

    # ---- P1-5：工件声明字段闭合 + 不得由文件后缀猜类型 / 版本 ----
    check(set(BA.ARTIFACT_DECLARATION_KEYS) == {"path", "sha256", "size", "role",
                                                "producer_step", "artifact_type",
                                                "artifact_schema_version"},
          f"成员与索引条目共同声明字段固定为 7 项：{BA.ARTIFACT_DECLARATION_KEYS}")
    check(set(BA.ARTIFACT_MEMBER_ROLES) == set(BA.ARTIFACT_TYPE_REGISTRY),
          "每个 role 都在工件类型注册表中有唯一条目（无遗漏、无多余）")
    check(len(set(BA.ARTIFACT_TYPE_KEYS)) == len(BA.ARTIFACT_TYPE_KEYS)
          and len(set(BA.ARTIFACT_SCHEMA_VERSIONS)) == len(BA.ARTIFACT_SCHEMA_VERSIONS),
          "工件类型身份与 schema 版本各自唯一（不会两个类型共用一个版本号）")
    check(set(BA.ARTIFACT_PRODUCER_STEPS)
          == {v[1] for v in BA.ARTIFACT_TYPE_REGISTRY.values()},
          f"产出步骤词表与注册表完全一致：{BA.ARTIFACT_PRODUCER_STEPS}")
    check("suffix" not in code and "mimetypes" not in code and "guess_type" not in code,
          "可执行代码不使用文件后缀 / MIME 猜类型或版本（登记即唯一来源）")
    check(BA.ARTIFACT_INDEX_ENTRY_KEYS == BA.ARTIFACT_DECLARATION_KEYS,
          "索引条目对成员声明的覆盖是逐字段的全覆盖（无字段只在单侧声明）")

    scratch = Path(tempfile.mkdtemp(prefix="m930_1_artifacts_eval_"))
    try:
        run_dir = scratch / _RUN_ID
        members = _members(scope_manifest, surfaces)

        # ===================== 2. create-only 正常写入 + 严格读回 =====================
        predicted = BA.plan_index_sha256(_RUN_ID, members)
        result = BA.write_run_artifacts(run_dir, members, build_manifest)
        check(run_dir.is_dir() and result.index_sha256 == predicted,
              f"create-only 写入成功（manifest_id={result.manifest_id}）")
        check(result.posix_directory_fsync_available
              == BA.POSIX_DIRECTORY_FSYNC_AVAILABLE,
              "写入结果如实报告目录 fsync 可用性")
        loaded = BA.load_run_artifacts(run_dir)
        check(loaded.index_sha256 == result.index_sha256
              and loaded.manifest_sha256 == result.manifest_sha256,
              "严格读回：索引与 manifest 的 SHA256 与写入时一致")
        check(sorted(loaded.members) == sorted(m.path for m in members),
              f"严格读回全部内容成员（{sorted(loaded.members)}）")
        check(loaded.manifest.run_identity.run_id == _RUN_ID
              and loaded.manifest.scope_manifest.job_id == scope_manifest.job_id,
              "读回的 manifest 绑定同一运行身份与同一 job")
        check(loaded.manifest.to_dict() == build_manifest(result.index_sha256).to_dict(),
              "读回的 manifest 与重建的 manifest 逐字段一致")

        # 索引只列内容成员；manifest 最后写；索引不回写 manifest 哈希
        index_names = {m.path for m in loaded.index.members}
        check(index_names == {m.path for m in members}
              and BA.ARTIFACT_INDEX_NAME not in index_names
              and BA.RUN_MANIFEST_NAME not in index_names,
              "索引只列内容成员，不含索引自身与 run manifest（规则 2）")
        index_text = (run_dir / BA.ARTIFACT_INDEX_NAME).read_text(encoding="utf-8")
        check(result.manifest_sha256 not in index_text
              and BA.RUN_MANIFEST_NAME not in index_text,
              "索引不回写 manifest 哈希（规则 6，无哈希环）")
        manifest_doc = json.loads(
            (run_dir / BA.RUN_MANIFEST_NAME).read_text(encoding="utf-8"))
        check(manifest_doc["artifact_index_sha256"] == result.index_sha256,
              "manifest 记录索引文件的 SHA256（规则 4）")
        check(BA.index_sha256_of(run_dir) == result.index_sha256,
              "index_sha256_of 只读复核与写入记录一致")
        check(loaded.manifest.manifest_fingerprint
              == BS.sha256_canonical(loaded.manifest.manifest_fingerprint_body()),
              "manifest 根 ID 由规范体派生，且规范体排除自身身份字段")

        # ===================== 3. 目录已存在即拒绝，且无副作用 =====================
        before = _tree_fingerprint(run_dir)
        expect_raises("目标 run 目录已存在 → 拒绝覆盖",
                      lambda: BA.write_run_artifacts(run_dir, members, build_manifest),
                      "已存在")
        check(_tree_fingerprint(run_dir) == before,
              "拒绝覆盖后目标目录逐字节未变")
        check(sorted(p.name for p in scratch.iterdir()) == [_RUN_ID],
              "拒绝覆盖不留下临时目录")

        # ===================== 4. 路径规范化与逃逸防护 =====================
        for label, bad in (("绝对路径", "/etc/passwd"),
                           ("盘符路径", "C:/x.json"),
                           ("反斜杠分隔符", "content\\x.json"),
                           ("重复分隔符", "content//x.json"),
                           (".. 上跳", "content/../x.json"),
                           ("单个 ..", ".."),
                           ("'.' 路径段", "content/./x.json"),
                           ("空路径段结尾", "content/"),
                           ("保留设备名", "content/CON.json"),
                           ("保留设备名(大小写)", "content/nul.txt"),
                           ("段尾点号", "content/x./y.json"),
                           ("控制字符", "content/x\ny.json")):
            expect_raises(f"路径 {label} 被拒",
                          lambda b=bad: BA.canonical_member_path(b),
                          "RunArtifactError" if False else None)
        check(BA.canonical_member_path("content/a/b.json") == "content/a/b.json",
              "合法相对路径原样规范化")
        expect_raises("成员路径占用协议文件名（索引）被拒",
                      lambda: _member(BA.ARTIFACT_INDEX_NAME, "other", "x"),
                      "协议文件名")
        expect_raises("成员路径占用协议文件名（manifest）被拒",
                      lambda: _member(BA.RUN_MANIFEST_NAME, "other", "x"),
                      "协议文件名")
        expect_raises("索引记录自身（自引用）被拒",
                      lambda: _entry(BA.ARTIFACT_INDEX_NAME), "协议文件名")
        expect_raises("索引记录 run manifest（自引用）被拒",
                      lambda: _entry(BA.RUN_MANIFEST_NAME), "协议文件名")
        # 直接构造（不经 _member 的注册表查询），确保拦下来的是模块自身的 role 校验
        expect_raises("未知 role 被拒",
                      lambda: BA.ArtifactMember(path="a.json", role="not_a_role",
                                                producer_step="harness",
                                                artifact_type="artifact.other",
                                                artifact_schema_version="demo-artifact-other-v1",
                                                payload="x"),
                      "role 非法")
        # 显式声明但与 role 不相称 → 拒绝，不静默改写
        expect_raises("成员显式声明与 role 不符的工件类型被拒",
                      lambda: _member("a.json", "other", "x",
                                      artifact_type="artifact.plan"),
                      "artifact_type")
        expect_raises("成员显式声明与 role 不符的产出步骤被拒",
                      lambda: _member("a.json", "other", "x", producer_step="plan"),
                      "producer_step")
        expect_raises("成员显式声明与工件类型不符的 schema 版本被拒",
                      lambda: _member("a.json", "other", "x",
                                      artifact_schema_version="demo-artifact-plan-v1"),
                      "artifact_schema_version")
        # 三项声明是必填构造参数：缺少任何一个都构不出成员（不按 role 静默补齐）；
        # 显式传 None 也必须在构造期拒绝，而不是被当成"未声明"放行。
        _decls = dict(zip(("artifact_type", "producer_step", "artifact_schema_version"),
                          BA.ARTIFACT_TYPE_REGISTRY["other"]))
        for _omitted in ("artifact_type", "producer_step", "artifact_schema_version"):
            _kwargs = {"path": "a.json", "role": "other",
                       **{k: v for k, v in _decls.items() if k != _omitted}}
            try:
                BA.ArtifactMember(**_kwargs)
            except TypeError as exc:
                check(True, f"缺少必填声明构造失败（{_omitted}）：{str(exc)[:90]}")
            else:
                check(False, f"缺少必填声明构造失败（{_omitted}）：缺少声明仍构造成功")
            expect_raises(f"显式 None 不被补齐（{_omitted}）",
                          lambda k=_omitted: BA.ArtifactMember(
                              path="a.json", role="other", **{**_decls, k: None}),
                          "与 role 不符")
        expect_raises("members 重复路径被拒",
                      lambda: BA.write_run_artifacts(
                          scratch / "dup_run",
                          (_member("a.json", "other", "1"),
                           _member("a.json", "other", "2")),
                          build_manifest), "重复路径")
        check(not (scratch / "dup_run").exists() and not (scratch / ".dup_run.tmp").exists(),
              "重复路径失败后没有留下目录")

        # ===================== 5. 篡改 / 截断 / 缺失 / 多余 / 重复 =====================
        def mutate(rel: str, mutate_fn, label: str, needle: str | None = None) -> None:
            p = run_dir / rel
            original = p.read_bytes()
            mutate_fn(p)
            expect_raises(f"{label} → 读回 fail-closed",
                          lambda: BA.load_run_artifacts(run_dir), needle)
            p.write_bytes(original)

        mutate("content/report_body.md", lambda p: p.write_bytes(p.read_bytes() + b"TAMPER"),
               "成员被追加（size mismatch）", "大小不符")
        mutate("content/report_body.md", lambda p: p.write_bytes(p.read_bytes()[:7]),
               "成员被截断（size mismatch）", "大小不符")
        mutate("content/report_body.md", lambda p: p.write_bytes(b""),
               "成员被清空（size mismatch）", "大小不符")
        # 同长度不同内容 → 只有哈希能发现
        original_body = (run_dir / "content/report_body.md").read_bytes()
        mutate("content/report_body.md",
               lambda p: p.write_bytes(b"X" * len(original_body)),
               "成员被同长度替换（hash mismatch）", "SHA256 不符")

        # 成员缺失
        body = run_dir / "content/report_body.md"
        saved = body.read_bytes()
        body.unlink()
        expect_raises("成员缺失 → 读回 fail-closed",
                      lambda: BA.load_run_artifacts(run_dir), "缺索引记录的成员")
        body.write_bytes(saved)

        # 多余文件
        extra = run_dir / "content/extra.json"
        extra.write_bytes(b"{}")
        expect_raises("目录含索引未记录的多余文件 → fail-closed",
                      lambda: BA.load_run_artifacts(run_dir), "多余文件")
        extra.unlink()
        nested_extra = run_dir / "sub/deep/extra.json"
        nested_extra.parent.mkdir(parents=True)
        nested_extra.write_bytes(b"{}")
        # 索引未隐含的目录先于文件被拒（`sub` / `sub/deep` 都是多余目录）
        expect_raises("嵌套子目录中的多余文件同样 fail-closed",
                      lambda: BA.load_run_artifacts(run_dir), "多余目录")
        shutil.rmtree(nested_extra.parent)
        expect_raises("只留空的多余目录同样 fail-closed",
                      lambda: BA.load_run_artifacts(run_dir), "多余目录")
        shutil.rmtree(run_dir / "sub")
        # 索引成员路径隐含的普通父目录是合法的（content 由 content/*.json 隐含）
        check(BA.load_run_artifacts(run_dir).index_sha256 == result.index_sha256,
              "移除多余目录后健康目录恢复可读（允许的父目录不被误杀）")

        # 索引手工篡改（写回原样以保证后续检查基于健康目录）
        index_path = run_dir / BA.ARTIFACT_INDEX_NAME
        index_original = index_path.read_bytes()

        def with_index(doc, msg, needle=None):
            index_path.write_bytes((json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                               indent=2) + "\n").encode("utf-8"))
            expect_raises(msg, lambda: BA.load_run_artifacts(run_dir), needle)
            index_path.write_bytes(index_original)

        index_doc = json.loads(index_original.decode("utf-8"))
        with_index({**index_doc, "schema_version": "bogus-index-v9"},
                   "索引 schema_version 不符被拒", "schema_version")
        with_index({**index_doc, "unexpected": 1},
                   "索引含未知字段被拒", "未知字段")
        with_index({k: v for k, v in index_doc.items() if k != "members"},
                   "索引缺 members 被拒", "缺必填字段")
        with_index({**index_doc, "members": "not-a-list"},
                   "索引 members 类型不符被拒", "必须为 list")
        with_index({**index_doc,
                    "members": index_doc["members"] + [index_doc["members"][0]]},
                   "索引含重复成员路径被拒", "重复")
        with_index({**index_doc,
                    "members": index_doc["members"]
                    + [{"path": BA.RUN_MANIFEST_NAME, "sha256": "a" * 64, "size": 1,
                        "role": "other", "producer_step": "harness",
                        "artifact_type": "artifact.other",
                        "artifact_schema_version": "demo-artifact-other-v1"}]},
                   "索引自引用 manifest 被拒", "协议文件名")
        # 索引条目被改（哈希 / 大小）：索引字节随之改变 → 与 manifest 记录的索引 SHA256
        # 立刻不一致，先于成员级复核 fail-closed（这就是规则 4 的作用）。
        bad_entry = [dict(index_doc["members"][0])]
        bad_entry[0]["sha256"] = "0" * 64
        with_index({**index_doc,
                    "members": bad_entry + index_doc["members"][1:]},
                   "索引条目哈希被改 → 整份索引 SHA256 与 manifest 不符",
                   "artifact_index_sha256")
        bad_entry = [dict(index_doc["members"][0])]
        bad_entry[0]["size"] = bad_entry[0]["size"] + 5
        with_index({**index_doc,
                    "members": bad_entry + index_doc["members"][1:]},
                   "索引条目大小被改 → 整份索引 SHA256 与 manifest 不符",
                   "artifact_index_sha256")
        bad_entry = [dict(index_doc["members"][0])]
        bad_entry[0]["role"] = "not_a_role"
        with_index({**index_doc,
                    "members": bad_entry + index_doc["members"][1:]},
                   "索引记录非法 role 被拒", "role")
        bad_entry = [dict(index_doc["members"][0], path="../escape.json")]
        with_index({**index_doc,
                    "members": bad_entry + index_doc["members"][1:]},
                   "索引记录越界路径被拒", "..")
        bad_entry = [dict(index_doc["members"][0], path="/abs.json")]
        with_index({**index_doc,
                    "members": bad_entry + index_doc["members"][1:]},
                   "索引记录绝对路径被拒", "绝对路径")

        # ---- P1-5：索引条目的类型 / 步骤 / schema 版本声明闭合校验 ----
        # 注意：改坏任意一个字段都会同时改变索引字节 → 先撞上索引 SHA256 与 manifest
        # 不符。因此这里**同时**打类型层反例（走完整 run 目录测不到该分支）。
        _good = _entry("content/scope.json", "scope_manifest").to_dict()
        for label, key, bad in (("工件类型未知", "artifact_type", "artifact.nope"),
                                ("工件类型与 role 不符", "artifact_type", "artifact.plan"),
                                ("schema 版本未知", "artifact_schema_version", "v9"),
                                ("schema 版本与类型不符", "artifact_schema_version",
                                 "demo-artifact-plan-v1"),
                                ("产出步骤未知", "producer_step", "step9"),
                                ("产出步骤与 role 不符", "producer_step", "plan"),
                                ("role 未知", "role", "widget"),
                                ("path 为空", "path", ""),
                                ("sha256 为空", "sha256", ""),
                                ("size 为负", "size", -1)):
            expect_raises(f"索引条目声明{label}被拒",
                          lambda k=key, v=bad: BA.ArtifactIndexEntry.from_dict(
                              {**_good, k: v}))
        expect_raises("索引条目声明未知字段被拒",
                      lambda: BA.ArtifactIndexEntry.from_dict({**_good, "note": "x"}),
                      "未知字段")
        expect_raises("索引条目声明缺字段被拒",
                      lambda: BA.ArtifactIndexEntry.from_dict(
                          {k: v for k, v in _good.items() if k != "producer_step"}),
                      "缺必填字段")
        # 索引声明 ↔ 成员声明必须逐字段一致
        expect_raises("索引声明与成员声明大小不一致被拒",
                      lambda: _entry("content/scope.json", "scope_manifest", size=2)
                      .assert_declares(
                          _entry("content/scope.json", "scope_manifest", size=9), "x"),
                      "声明不一致")
        expect_raises("索引声明与成员声明 sha256 不一致被拒",
                      lambda: _entry("content/scope.json", "scope_manifest", sha256="a" * 64)
                      .assert_declares(
                          _entry("content/scope.json", "scope_manifest", sha256="b" * 64),
                          "x"), "声明不一致")
        check(_entry("content/scope.json", "scope_manifest")
              .assert_declares(_entry("content/scope.json", "scope_manifest"), "x") is None,
              "索引声明与成员声明完全一致时通过（不是恒失败的空断言）")

        # manifest 手工篡改
        manifest_path = run_dir / BA.RUN_MANIFEST_NAME
        manifest_original = manifest_path.read_bytes()

        def with_manifest(doc, msg, needle=None):
            manifest_path.write_bytes((json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                                   indent=2) + "\n").encode("utf-8"))
            expect_raises(msg, lambda: BA.load_run_artifacts(run_dir), needle)
            manifest_path.write_bytes(manifest_original)

        manifest_doc = json.loads(manifest_original.decode("utf-8"))
        with_manifest({**manifest_doc, "schema_version": "bogus-backbone-v9"},
                      "manifest schema_version 不符被拒", "schema_version")
        # 直接改 manifest 记录的索引 SHA256 会先破坏 manifest 自身的内容指纹（规则 5），
        # 因此这里断言的是 manifest 自证失败；「manifest 与索引不一致」由上面的索引
        # 篡改用例覆盖（规则 4）。
        with_manifest({**manifest_doc, "artifact_index_sha256": "0" * 64},
                      "manifest 记录的索引 SHA256 被改 → manifest 内容指纹自证失败",
                      "manifest_fingerprint")
        with_manifest({k: v for k, v in manifest_doc.items() if k != "manifest_id"},
                      "manifest 缺 manifest_id 被拒", "缺必填字段")
        with_manifest({**manifest_doc, "unexpected": 1},
                      "manifest 含未知字段被拒", "未知字段")
        tampered = json.loads(manifest_original.decode("utf-8"))
        tampered["design_surfaces"]["records"][0]["status"] = "demonstrated"
        tampered["design_surfaces"]["records"][0]["evidence_refs"] = []
        with_manifest(tampered, "manifest 内设计面缺证据仍称 demonstrated 被拒",
                      "evidence_refs")

        # 非 JSON / 截断的协议文件
        index_path.write_bytes(b"{not json")
        expect_raises("索引非合法 JSON 被拒", lambda: BA.load_run_artifacts(run_dir),
                      "非法 JSON")
        index_path.write_bytes(index_original)
        manifest_path.write_bytes(manifest_original[:20])
        expect_raises("manifest 被截断（非合法 JSON）被拒",
                      lambda: BA.load_run_artifacts(run_dir), "非法 JSON")
        manifest_path.write_bytes(manifest_original)

        check(BA.load_run_artifacts(run_dir).index_sha256 == result.index_sha256,
              "全部反例复原后健康目录仍可严格读回")

        # ===================== 6. symlink / junction / reparse point 逃逸 =====================
        # 用**注入的文件系统谓词**构造确定性反例：不依赖本机 symlink 创建权限，
        # 因此这里既不需要 skip，也不会出现「跳过之后仍宣称通过」。
        real_root = os.path.realpath(run_dir)
        outside = scratch / "outside_secret.txt"
        outside.write_bytes(saved)
        expect_raises("成员位置为 symlink / reparse point → 读回拒绝",
                      lambda: BA._scan_tree(
                          run_dir, real_root,
                          is_reparse=lambda p: p.name == "report_body.md"))
        expect_raises("嵌套目录为 symlink / junction → 读回拒绝",
                      lambda: BA._scan_tree(
                          run_dir, real_root,
                          is_reparse=lambda p: p.name == "content"))
        expect_raises("成员 realpath 逃逸出 run 目录 → 读回拒绝",
                      lambda: BA._scan_tree(
                          run_dir, real_root,
                          realpath=lambda p: str(outside)
                          if p.name == "report_body.md" else os.path.realpath(p)))
        expect_raises("目录 realpath 逃逸出 run 目录 → 读回拒绝",
                      lambda: BA._scan_tree(
                          run_dir, real_root,
                          realpath=lambda p: str(scratch)
                          if p.name == "content" else os.path.realpath(p)))
        # 注入谓词是**唯一**变量：同一目录在全假谓词下必须完整遍历通过
        _files, _dirs = BA._scan_tree(run_dir, real_root, is_reparse=lambda p: False)
        check(sorted(_files) == sorted([BA.ARTIFACT_INDEX_NAME, BA.RUN_MANIFEST_NAME]
                                       + [m.path for m in members])
              and list(_dirs) == ["content"],
              f"注入谓词全假时遍历完整通过（files={len(_files)}, dirs={list(_dirs)}）")
        # 模块级默认谓词必须真的被 `_scan_tree` 使用（否则注入测试会掩盖真实缺口）
        real_predicate = BA._fs_is_reparse
        try:
            BA._fs_is_reparse = lambda p: p.name == "report_body.md"
            expect_raises("替换模块级 reparse 谓词后 load 也必须拒绝",
                          lambda: BA.load_run_artifacts(run_dir), "reparse")
        finally:
            BA._fs_is_reparse = real_predicate
        check(BA.load_run_artifacts(run_dir).members["content/report_body.md"] == saved,
              "恢复真实谓词后目录仍可严格读回")
        # 本机若能真的建 symlink，则额外做一次真实端到端逃生（失败也不影响上面的结论）
        target = run_dir / "content/report_body.md"
        target.unlink()
        try:
            os.symlink(str(outside), str(target))
        except (OSError, NotImplementedError):
            target.write_bytes(saved)
            details.append("INFO: 本机无 symlink 创建权限，真实 symlink 端到端反例未执行；"
                           "reparse 分支已由上面的注入谓词确定性覆盖")
        else:
            expect_raises("真实 symlink 成员 → 读回拒绝逃逸",
                          lambda: BA.load_run_artifacts(run_dir), "reparse")
            target.unlink()
            target.write_bytes(saved)
            check(BA.load_run_artifacts(run_dir).members["content/report_body.md"] == saved,
                  "复原真实 symlink 后目录恢复可读")

        # ===================== 7. 临时目录失败清理不超过边界 =====================
        # 7.1 预先存在同名临时目录 → 拒绝且不动它
        guard_parent = scratch / "guard"
        guard_parent.mkdir()
        guard_tmp = guard_parent / f".{_RUN_ID}.tmp"
        guard_tmp.mkdir()
        sentinel = guard_tmp / "sentinel.txt"
        sentinel.write_text("do-not-delete", encoding="utf-8")
        expect_raises("预先存在的临时目录 → 拒绝写入",
                      lambda: BA.write_run_artifacts(guard_parent / _RUN_ID, members,
                                                     build_manifest), "临时目录已存在")
        check(sentinel.read_text(encoding="utf-8") == "do-not-delete"
              and not (guard_parent / _RUN_ID).exists(),
              "越界清理防护：本次未创建的临时目录及其内容原样保留")

        # 7.2 写入中途失败 → 只清理本次创建的临时目录
        fault_parent = scratch / "fault"
        fault_parent.mkdir()
        keep = fault_parent / "keep.txt"
        keep.write_text("keep", encoding="utf-8")
        real_write = BA._write_file
        calls = {"n": 0}

        def failing_write(path, payload):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("injected write failure")
            return real_write(path, payload)

        BA._write_file = failing_write
        try:
            expect_raises("成员写入中途失败 → 抛出并清理",
                          lambda: BA.write_run_artifacts(fault_parent / _RUN_ID, members,
                                                         build_manifest),
                          "injected write failure")
        finally:
            BA._write_file = real_write
        check(not (fault_parent / _RUN_ID).exists()
              and not (fault_parent / f".{_RUN_ID}.tmp").exists()
              and keep.read_text(encoding="utf-8") == "keep",
              "写入中途失败只清理本次创建的临时目录，不越过目标父目录边界")
        check(calls["n"] >= 2, f"故障注入确实发生在写入中途（write 调用 {calls['n']} 次）")

        # 7.3 清理守卫本身的边界判定
        other_parent = scratch / "other"
        other_parent.mkdir()
        wrong_name = other_parent / ".not_the_run_id.tmp"
        wrong_name.mkdir()
        BA._cleanup_tmp(wrong_name, other_parent, _RUN_ID)
        check(wrong_name.exists(),
              "_cleanup_tmp 拒绝删除名称不匹配的目录")
        BA._cleanup_tmp(fault_parent / f".{_RUN_ID}.tmp", other_parent, _RUN_ID)
        check(not (fault_parent / f".{_RUN_ID}.tmp").exists(),
              "不存在的临时目录清理为幂等无操作")
        alias = scratch / "alias"
        alias.mkdir()
        real_dir = alias / f".{_RUN_ID}.tmp"
        real_dir.mkdir()
        BA._cleanup_tmp(real_dir, other_parent, _RUN_ID)
        check(real_dir.exists(),
              "_cleanup_tmp 拒绝删除不在指定父目录下的目录（父目录不符）")

        # ===================== 8. validate-only 不改一个字节 =====================
        before_all = _tree_fingerprint(run_dir)
        before_outside = _tree_fingerprint(scratch)
        verdict = BA.validate_run_artifacts(run_dir)
        after_all = _tree_fingerprint(run_dir)
        after_outside = _tree_fingerprint(scratch)
        check(verdict["ok"] is True and before_all == after_all,
              f"validate-only 逐字节不改动 run 目录（members={verdict['member_count']}）")
        check(before_outside == after_outside,
              "validate-only 不产生任何额外文件（父目录整树未变）")
        check(verdict["run_id"] == _RUN_ID
              and verdict["manifest_id"] == result.manifest_id
              and verdict["safety_policy_id"] == BA.SAFETY_POLICY_ID
              and verdict["posix_directory_fsync_available"]
              == BA.POSIX_DIRECTORY_FSYNC_AVAILABLE,
              "validate-only 返回结构化结论且如实报告安全策略")
        bad_verdict = BA.validate_run_artifacts(scratch / "does_not_exist")
        check(bad_verdict["ok"] is False and "error" in bad_verdict,
              "validate-only 对不存在的目录返回结构化失败而非崩溃")

        # ===================== 9. loader 不建数据库、不产生额外文件 =====================
        before_load = _tree_fingerprint(scratch)
        for _ in range(3):
            BA.load_run_artifacts(run_dir)
        check(_tree_fingerprint(scratch) == before_load,
              "反复读回不产生任何额外文件（无 .db / 无日志 / 无缓存）")
        db_files = [p.as_posix() for p in scratch.rglob("*")
                    if p.is_file() and p.suffix in (".db", ".sqlite", ".sqlite3")]
        check(db_files == [], f"整个 scratch 树中不存在数据库文件（{db_files}）")
        check(not any(p.name.endswith(("-wal", "-shm", "-journal"))
                      for p in scratch.rglob("*")),
              "不存在 SQLite WAL / SHM / journal 侧写文件")
        expect_raises("load 传入不存在的目录被拒",
                      lambda: BA.load_run_artifacts(scratch / "nope"), "不存在")

        # ===================== 10. 确定性：重复写入同一内容得到同一身份 =====================
        stable = scratch / "stable"
        stable.mkdir()
        r2 = BA.write_run_artifacts(stable / _RUN_ID, members, build_manifest)
        check(r2.index_sha256 == result.index_sha256
              and r2.manifest_sha256 == result.manifest_sha256
              and r2.manifest_id == result.manifest_id,
              "同一内容重复写入 → 索引 / manifest / 身份完全一致（确定性）")
        check(r2.member_paths == result.member_paths,
              "同一内容重复写入 → 成员清单一致")

        # ===================== 11. 自检输出可观测 =====================
        selfcheck = BA.self_check()
        check(selfcheck["ok"] is True and selfcheck["failed"] == 0,
              f"模块 self-check 通过（passed={selfcheck['passed']}）")
        check(selfcheck["posix_directory_fsync_available"]
              == BA.POSIX_DIRECTORY_FSYNC_AVAILABLE,
              "self-check 如实报告目录 fsync 是否可用")
        check(len(selfcheck["checks"]) >= 20,
              f"self-check 覆盖 {len(selfcheck['checks'])} 项不变量")

        # ============ 12. index v2 新角色 + v1 只读回放（C6 / §16.8） ============
        # 12.1 版本分派词表：v1 逐字冻结且是 current 的**真子集**（只追加，不改既有条目）
        v1_roles = set(BA.ARTIFACT_MEMBER_ROLES_V1)
        v2_roles = set(BA.ARTIFACT_MEMBER_ROLES_V2)
        v1_types = {r: BA.ARTIFACT_TYPE_REGISTRY_V1[r] for r in BA.ARTIFACT_MEMBER_ROLES_V1}
        v2_types = {r: BA.ARTIFACT_TYPE_REGISTRY_V2[r] for r in BA.ARTIFACT_MEMBER_ROLES_V2}
        check(BA.ARTIFACT_MEMBER_ROLES_V1
              == ("plan", "scope_manifest", "design_surfaces", "report_body",
                  "narrative_paragraph", "claim_table", "table_object", "pack_manifest",
                  "gap_register", "review_issue", "assurance_result", "status", "other"),
              "v1 角色词表逐字冻结（13 项，一个都不改）")
        check(v1_roles < v2_roles and all(v2_types[r] == t for r, t in v1_types.items()),
              "v2 = v1 + 新增角色；既有 13 条类型/步骤/版本声明逐字未动")
        check(set(BA.ARTIFACT_MEMBER_ROLES_V2_ADDED) == {
                  "section_draft", "claim_candidate", "narrative_draft_unit",
                  "support_proposal", "claim_binding_decision",
                  "claim_entailment_decision", "accepted_support_binding",
                  "section_claim", "section_narrative", "section_result",
                  "section_unresolved", "writer_material_manifest",
                  "material_disposition", "fact_narrative_disposition",
                  "follow_up_need", "follow_up_decision", "external_fact"},
              f"v2 新增角色覆盖 draft/candidate/unit/proposal/两类决定/binding/"
              f"Claim/Narrative/Result/unresolved/manifest/三类 disposition/"
              f"follow-up/external（{len(BA.ARTIFACT_MEMBER_ROLES_V2_ADDED)} 项）")
        check(set(BA.ARTIFACT_PRODUCER_STEPS_V2_ADDED) == {"gate", "evaluator"},
              "v2 新增产出步骤：确定性 gate 与 evaluator 各自独立")
        check(BA.ARTIFACT_PRODUCER_STEPS_V1
              == ("scope", "plan", "research", "writer", "assembler", "review",
                  "assurance", "status", "harness"),
              "v1 产出步骤词表逐字冻结")
        check(BA.ARTIFACT_INDEX_SCHEMA_VERSION == BA.ARTIFACT_INDEX_SCHEMA_VERSION_V2
              and BA.LEGACY_ARTIFACT_INDEX_SCHEMA_VERSIONS
              == (BA.ARTIFACT_INDEX_SCHEMA_VERSION_V1,),
              "current 写入版本 = v2；legacy 只读版本 = (v1)")
        check(set(BA.ARTIFACT_INDEX_WIRES) == set(BA.ARTIFACT_INDEX_KNOWN_SCHEMA_VERSIONS),
              "索引 wire 真值表与已知版本集合一致（无第三种版本）")

        # 12.2 v2 索引可承载全部新角色，且严格读回逐字节一致
        new_chain_members = tuple(
            _member(f"chain/{role}.json", role, f"payload::{role}\n")
            for role in BA.ARTIFACT_MEMBER_ROLES_V2_ADDED)
        v2_dir = scratch / "v2run" / _RUN_ID
        (scratch / "v2run").mkdir()
        v2_result = BA.write_run_artifacts(v2_dir, new_chain_members, build_manifest)
        v2_loaded = BA.load_run_artifacts(v2_dir)
        check(v2_loaded.index_schema_version == BA.ARTIFACT_INDEX_SCHEMA_VERSION
              and v2_loaded.is_legacy_index is False,
              f"v2 run 由 current reader 读回并如实标为非 legacy"
              f"（index_schema_version={v2_loaded.index_schema_version}）")
        check(all(v2_loaded.members[m.path] == m.payload for m in new_chain_members),
              "v2 新角色成员逐字节严格读回（sha256/size 逐字复核）")
        check({m.role for m in v2_loaded.index.members}
              == set(BA.ARTIFACT_MEMBER_ROLES_V2_ADDED),
              "写回的新链角色集合与声明一致（无静默降级、无丢失）")
        check(v2_result.index_sha256 == BA.index_sha256_of(v2_dir),
              "v2 索引 SHA256 只读复核一致")

        # 12.3 legacy 只读入口不得读取 current(v2) run（两个入口各守一边）
        expect_raises("legacy 只读入口拒绝 current v2 索引",
                      lambda: BA.load_legacy_run_artifacts_for_audit(v2_dir),
                      "不是 legacy 版本")

        # 12.4 手工落一个**真实 v1 run**（v1 索引 + 与之一致的 manifest），
        # 验证 current reader 只读回放、legacy reader 可读，且**逐字节不被改动**。
        v1_parent = scratch / "v1run"
        v1_parent.mkdir()
        v1_dir = v1_parent / _RUN_ID
        v1_members = (_member("content/README.txt", "other", "v1 skeleton\n"),
                      _member("content/scope.json", "scope_manifest", '{"v1": 1}\n'))
        v1_index = BA.ArtifactIndex(
            schema_version=BA.ARTIFACT_INDEX_SCHEMA_VERSION_V1, run_id=_RUN_ID,
            members=tuple(m.entry() for m in v1_members))
        check(v1_index.is_legacy is True and v1_index.wire is BA.ARTIFACT_INDEX_WIRES[
                  BA.ARTIFACT_INDEX_SCHEMA_VERSION_V1],
              "v1 索引只按 v1 词表校验自身（不借 current 表扩权）")
        v1_index_bytes = BA.serialize_index(v1_index)
        v1_dir.mkdir()
        for _m in v1_members:
            _target = v1_dir / _m.path
            _target.parent.mkdir(parents=True, exist_ok=True)
            _target.write_bytes(_m.payload)
        (v1_dir / BA.ARTIFACT_INDEX_NAME).write_bytes(v1_index_bytes)
        (v1_dir / BA.RUN_MANIFEST_NAME).write_bytes(
            BA.serialize_manifest(build_manifest(hashlib.sha256(v1_index_bytes).hexdigest())))

        before_v1 = _tree_fingerprint(v1_dir)
        loaded_v1 = BA.load_run_artifacts(v1_dir)
        audit_v1 = BA.load_legacy_run_artifacts_for_audit(v1_dir)
        check(loaded_v1.index_schema_version == BA.ARTIFACT_INDEX_SCHEMA_VERSION_V1
              and loaded_v1.is_legacy_index is True,
              "v1 索引经 current reader 只读回放，并如实标为 legacy")
        check(audit_v1.index_sha256 == loaded_v1.index_sha256
              and audit_v1.members == loaded_v1.members
              and audit_v1.index.schema_version
              == BA.ARTIFACT_INDEX_SCHEMA_VERSION_V1,
              "legacy 只读入口可读 v1 run，且与 current reader 结果一致（不升级版本）")
        check(loaded_v1.index.to_dict()["schema_version"]
              == BA.ARTIFACT_INDEX_SCHEMA_VERSION_V1,
              "读回的 v1 索引 schema_version 原样保留（不被改写成 v2）")
        check(_tree_fingerprint(v1_dir) == before_v1,
              "v1 只读回放不升级、不重写、不迁移（目录逐字节未变）")
        check(BA.validate_run_artifacts(v1_dir)["is_legacy_index"] is True
              and BA.validate_run_artifacts(v2_dir)["is_legacy_index"] is False,
              "validate-only 如实报告索引版本来源（v1 legacy / v2 current）")

        # 12.5 反向：v1 索引声明一个只有 v2 才有的角色 —— 冻结 wire 不得静默扩权。
        # 索引与 manifest 都自洽，**唯一**可拒的理由就是 v1 词表里没有该角色。
        forged_entry = _entry("chain/section_draft.json", "section_draft", size=1)
        forged_doc = {"schema_version": BA.ARTIFACT_INDEX_SCHEMA_VERSION_V1,
                      "run_id": _RUN_ID, "members": [forged_entry.to_dict()]}
        forged_bytes = (json.dumps(forged_doc, ensure_ascii=False, sort_keys=True,
                                   indent=2) + "\n").encode("utf-8")
        forged_parent = scratch / "v1forged"
        forged_parent.mkdir()
        forged_dir = forged_parent / _RUN_ID
        forged_dir.mkdir()
        (forged_dir / BA.ARTIFACT_INDEX_NAME).write_bytes(forged_bytes)
        (forged_dir / BA.RUN_MANIFEST_NAME).write_bytes(
            BA.serialize_manifest(build_manifest(hashlib.sha256(forged_bytes).hexdigest())))
        expect_raises("v1 索引声明 v2 独有角色被拒（不借 current 表扩权）",
                      lambda: BA.load_run_artifacts(forged_dir), "role 非法")
        expect_raises("legacy 只读入口同样拒绝该 v1 索引（不绕过同一条校验）",
                      lambda: BA.load_legacy_run_artifacts_for_audit(forged_dir),
                      "role 非法")

        # 12.6 反向：未知索引版本一律拒绝（不猜测、不回落、不静默当 current）
        unknown_parent = scratch / "v1unknown"
        unknown_parent.mkdir()
        unknown_dir = unknown_parent / _RUN_ID
        unknown_dir.mkdir()
        unknown_bytes = (json.dumps(
            {**forged_doc, "members": [_member("content/README.txt", "other", "x").entry().to_dict()],
             "schema_version": "demo-artifact-index-v9"},
            ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        (unknown_dir / BA.ARTIFACT_INDEX_NAME).write_bytes(unknown_bytes)
        (unknown_dir / BA.RUN_MANIFEST_NAME).write_bytes(
            BA.serialize_manifest(build_manifest(hashlib.sha256(unknown_bytes).hexdigest())))
        expect_raises("未知索引版本被拒（无第三版本回落）",
                      lambda: BA.load_run_artifacts(unknown_dir), "schema_version")

        # ============ 13. legacy 生产者不得冒充 current 工件（P25–P32 方向） ============
        # 旧对象 wire 版本不是工件 schema 版本：把 `claim-1` / `section-result-1` / `narr-3`
        # 这类**旧对象版本串**填进工件声明，必须在声明期被拒（两条版本轴不混用）。
        for _legacy_wire in ("claim-1", "section-result-1", "narr-3", "5", "4"):
            expect_raises(f"旧对象 wire 版本 {_legacy_wire!r} 不得当作工件 schema 版本",
                          lambda w=_legacy_wire: _member(
                              "x.json", "other", "x", artifact_schema_version=w),
                          "artifact_schema_version")
        # 反向：v2 工件版本串也不得冒充对象 wire 版本（两个轴互不替代）
        check("claim-2" not in BA.ARTIFACT_SCHEMA_VERSIONS
              and "section-result-2" not in BA.ARTIFACT_SCHEMA_VERSIONS,
              "对象 wire 版本串不出现在工件 schema 版本词表里（两条版本轴分离）")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    # 自检 / 本测试绝不写 evaluation/results/**
    check(not any(p.name.startswith("demo_backbone_") for p in
                  (DS.REPO_ROOT / "evaluation" / "results").glob("*")),
          "本批未在 evaluation/results/** 生成任何 demo_backbone_* 正式运行目录")

    return {"passed": passed, "failed": failed, "skipped": skipped, "details": details}


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))

"""上传三份 PDF → **本次运行**：接线、反例与页面读数的聚焦回归。

Run: ``python -X utf8 -m evals.test_m930_5_upload_run``.

本模块只碰三样东西：操作者上传的**字节**、`cri-1` 运行输入目录、以及页面在这两样之上算出来
的读数。它**不**调 LLM、**不**联网、**不**写 `data/` 下的任何库、**不**改冻结产物，也**不**
把链跑起来（一次真实运行要二十多分钟，那是手工端到端验证的事）。

立它的理由：这一批把「上传 → 新 run → 新正文与审核」接了起来，而这条链最容易在四件事上
悄悄退化——(1) 找不到对象时**回退**去读登记路径或 `data/samples` 里的同名文件；(2) 按**文件名**
而不是内容哈希定位；(3) 多出来的文件只给个警告就放行；(4) 新运行的页面在拿不到本 run 产物时
回落去显示历史 run。四条在下面各有反例，要求一律是**拒**，不是降级。

本模块**不**声称正文质量：离线替身的取材与措辞由端到端跑出来的读回自述负责，不在这里。
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from planning import demo_scope as SC_scope
from sections import cited_budget as CB
from sections import cited_demo_binding as binding_mod
from sections import cited_financial_input as cfi
from sections import cited_run_authorization as CRA
from sections import cited_run_input as CRI
from sections import cited_run_journal as CRJ


REPO = Path(__file__).resolve().parent.parent
SAMPLES = REPO / "data" / "samples" / "300750" / "announcements"
FINANCIAL_SAMPLES = REPO / "data" / "samples" / "300750" / "financial"
EVIDENCE_DB = REPO / "data" / "evidence.db"
FINANCIAL_DB = REPO / "data" / "financial_v2.db"
COMPANY_SUBJECT = "300750"
#: 两个历史 run：新运行的页面**永远**不该显示它们的正文或审阅读数。
HISTORICAL_RUNS = (binding_mod.COMPANY_SOURCE.run_id, binding_mod.FINANCIAL_SOURCE.run_id)

#: 结果根与一次**真实**的 create-only 双节上传运行（只读、只作产出者读数的正例基线）。
RESULTS_ROOT = REPO / "evaluation" / "results"
REAL_UPLOAD_RUN = "m930_3_cited_upload_20261005T071143Z"


def _declared() -> tuple[CRI.DeclaredDocument, ...]:
    return CRI.registered_documents(EVIDENCE_DB, COMPANY_SUBJECT)


def _uploads() -> list[tuple[str, bytes]]:
    """三份真实上传面：文件名取登记名，字节直接读自本机案例目录。"""
    out: list[tuple[str, bytes]] = []
    for doc in _declared():
        path = SAMPLES / doc.filename
        if not path.is_file():
            raise unittest.SkipTest(f"案例来源 PDF 不在本机（{doc.filename}）；不伪造材料")
        out.append((doc.filename, path.read_bytes()))
    return out


def _subject_name() -> str:
    """主体名称由财务库的登记读出来，不在用例里写死一个名字。"""
    names = cfi.registered_subject_names(FINANCIAL_DB, subject=COMPANY_SUBJECT)
    if len(names) != 1:
        raise unittest.SkipTest(f"财务库登记的主体名称不是唯一一个（{list(names)}）")
    return names[0]


def _fin_declared() -> tuple[cfi.DeclaredFinancialSource, ...]:
    """当前有效快照声明的三条财务来源版本（含哈希）。只读，不写库。"""
    snapshot = cfi.current_snapshot_identity(
        FINANCIAL_DB, subject=COMPANY_SUBJECT, subject_name=_subject_name())
    return cfi.declared_financial_sources(
        FINANCIAL_DB, version_ids=[v for v, _ in snapshot.source_versions])


def _fin_uploads(declared: tuple[cfi.DeclaredFinancialSource, ...] | None = None
                 ) -> list[tuple[str, bytes]]:
    """三份真实财务上传面：文件名取登记名，字节读自本机案例目录。"""
    sources = declared if declared is not None else _fin_declared()
    out: list[tuple[str, bytes]] = []
    for source in sources:
        path = FINANCIAL_SAMPLES / source.source_name
        if not path.is_file():
            raise unittest.SkipTest(f"案例来源工作簿不在本机（{source.source_name}）；不伪造材料")
        out.append((source.source_name, path.read_bytes()))
    return out


class _Stub:
    """`st` 的替身：只记调用，状态放在一个共享盒子里（`calls` / `session_state` / `query`）。

    两处刻意贴近真实 Streamlit 的语义，否则页面反例测的是替身而不是页面：
    `radio`/`selectbox` 带 `key` 时**优先返回会话里的现值**（真 Streamlit 就是这么做的），
    `file_uploader` 返回盒子里摆好的上传件。
    """

    def __init__(self, box: dict) -> None:
        self._box = box

    @property
    def session_state(self) -> dict:
        return self._box["session_state"]

    @property
    def query_params(self) -> dict:
        return self._box["query"]

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self._box["calls"].append((name, args, kwargs))
            if name == "columns":
                spec = args[0]
                count = spec if isinstance(spec, int) else len(spec)
                return [_Stub(self._box) for _ in range(count)]
            if name == "tabs":
                return [_Stub(self._box) for _ in args[0]]
            if name in ("container", "expander", "empty", "form", "status", "spinner"):
                return self
            if name in ("radio", "selectbox"):
                key = kwargs.get("key")
                if key and key in self._box["session_state"]:
                    return self._box["session_state"][key]
                options = args[1] if len(args) > 1 else kwargs.get("options")
                return options[0]
            if name == "file_uploader":
                #: 第 1 屏现在有**两个**上传控件（PDF 与 XLSX），按 `key` 分开摆件。
                key = kwargs.get("key")
                return self._box.get("fin_uploads" if key == "cur_fin_uploader"
                                     else "uploads")
            if name == "button":
                return False
            if name in ("text_input", "text_area", "number_input"):
                return ""
            return None

        return record

    def __enter__(self) -> "_Stub":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _Harness:
    """一次页面调用的盒子：`st` 替身 + 会话状态 + 查询参数。"""

    def __init__(self, *, session: dict | None = None,
                 query: dict | None = None) -> None:
        self.box = {"calls": [], "session_state": dict(session or {}),
                    "query": dict(query or {}), "uploads": None}
        self.st = _Stub(self.box)

    @property
    def session_state(self) -> dict:
        return self.box["session_state"]

    def set_uploads(self, uploads) -> None:
        self.box["uploads"] = uploads

    def text(self) -> str:
        return "\n".join(
            " ".join([str(a) for a in args] + [f"{k}={v}" for k, v in kwargs.items()])
            for _name, args, kwargs in self.box["calls"])

    def by_name(self, name: str) -> list[str]:
        return [" ".join([str(a) for a in args]
                         + [f"{k}={v}" for k, v in kwargs.items()])
                for called, args, kwargs in self.box["calls"] if called == name]


class _FakeUpload:
    """Streamlit `UploadedFile` 的最小替身：`name` + 内存字节。"""

    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


class _SampleCase(unittest.TestCase):
    """需要本机三份真实 PDF 与只读登记库的用例的共同底座。"""

    @classmethod
    def setUpClass(cls) -> None:
        if not EVIDENCE_DB.is_file():
            raise unittest.SkipTest("Evidence 登记库不在本机；不伪造声明")
        cls.declared = _declared()
        if len(cls.declared) != 3:
            raise unittest.SkipTest(
                f"本机登记的当前来源不是三份（{len(cls.declared)}）；本批演示只针对三份上传")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="m930_5_upload_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def stage(self, run_id: str, uploads=None, *, financial: bool = False) -> Path:
        staged = self.tmp / "run_inputs" / run_id
        CRI.stage_run_input(uploads if uploads is not None else _uploads(), staged,
                            declared=self.declared, run_id=run_id)
        if financial:
            self.stage_financial(staged, run_id)
        return staged

    def stage_financial(self, staged: Path, run_id: str, uploads=None) -> cfi.FinancialInputBinding:
        """把三份 XLSX 落进**同一个**运行输入目录（`cfi-1` 与 `cri-1` 共存不互相覆盖）。"""
        declared = _fin_declared()
        snapshot = cfi.current_snapshot_identity(
            FINANCIAL_DB, subject=COMPANY_SUBJECT, subject_name=_subject_name())
        return cfi.stage_financial_input(
            uploads if uploads is not None else _fin_uploads(declared), staged,
            declared=declared, snapshot=snapshot, run_id=run_id)


class RunInputStagingTests(_SampleCase):
    """`cri-1` 的建立与读回：只有**内容哈希**说了算。"""

    def test_three_documents_stage_and_read_back(self) -> None:
        run_id = "m930_3_cited_upload_a"
        staged = self.stage(run_id)
        binding = CRI.load_run_input(staged, declared=self.declared)
        self.assertEqual(binding.run_id, run_id)
        self.assertEqual({d.document_id for d in binding.documents},
                         {d.document_id for d in self.declared})
        resolver = CRI.RunInputResolver.from_dir(staged, declared=self.declared)
        for doc in self.declared:
            path = resolver.resolve(document_id=doc.document_id, file_sha256=doc.sha256)
            self.assertTrue(resolver.owns(path))
            self.assertEqual(CRI.sha256_file(path), doc.sha256)
        # 上传时的文件名只作凭据：落盘一律按完整哈希取名。
        for obj in binding.documents:
            self.assertEqual(Path(obj.object_relpath).name, f"{obj.declared_sha256}.pdf")

    def test_object_moved_or_deleted_is_refused(self) -> None:
        staged = self.stage("m930_3_cited_upload_b")
        binding = CRI.load_run_input(staged)
        victim = staged / binding.documents[0].object_relpath
        victim.rename(victim.with_suffix(".bak"))
        with self.assertRaisesRegex(CRI.CitedRunInputError, "已丢失"):
            CRI.load_run_input(staged, declared=self.declared)
        with self.assertRaises(CRI.CitedRunInputError):
            CRI.RunInputResolver.from_dir(staged, declared=self.declared).resolve(
                document_id=binding.documents[0].document_id,
                file_sha256=binding.documents[0].declared_sha256)

    def test_one_byte_change_is_refused(self) -> None:
        staged = self.stage("m930_3_cited_upload_c")
        binding = CRI.load_run_input(staged)
        victim = staged / binding.documents[1].object_relpath
        victim.write_bytes(victim.read_bytes() + b"\x00")
        with self.assertRaisesRegex(CRI.CitedRunInputError, "字节"):
            CRI.load_run_input(staged, declared=self.declared)

    def test_duplicate_uploads_are_refused(self) -> None:
        uploads = _uploads()
        duplicated = uploads[:2] + [("副本.pdf", uploads[0][1])]
        with self.assertRaisesRegex(CRI.CitedRunInputError, "内容相同"):
            self.stage("m930_3_cited_upload_d", duplicated)
        self.assertFalse((self.tmp / "run_inputs" / "m930_3_cited_upload_d").exists())

    def test_extra_fourth_file_is_refused(self) -> None:
        uploads = _uploads() + [("别的材料.pdf", b"not part of the declared set")]
        with self.assertRaisesRegex(CRI.CitedRunInputError, "登记之外"):
            self.stage("m930_3_cited_upload_e", uploads)
        self.assertFalse((self.tmp / "run_inputs" / "m930_3_cited_upload_e").exists())

    def test_missing_document_is_refused_and_names_it(self) -> None:
        with self.assertRaisesRegex(CRI.CitedRunInputError, "缺少声明的来源材料"):
            self.stage("m930_3_cited_upload_f", _uploads()[:2])
        self.assertFalse((self.tmp / "run_inputs" / "m930_3_cited_upload_f").exists())

    def test_same_name_impostor_is_refused_because_lookup_is_by_hash(self) -> None:
        """同名文件冒充：名字一模一样，内容不是登记的那串字节 ⇒ 拒。"""
        uploads = _uploads()
        uploads[0] = (uploads[0][0], uploads[0][1] + b"tampered")
        with self.assertRaises(CRI.CitedRunInputError):
            self.stage("m930_3_cited_upload_g", uploads)

    def test_target_directory_is_never_overwritten(self) -> None:
        staged = self.stage("m930_3_cited_upload_h")
        before = (staged / CRI.MANIFEST_NAME).read_bytes()
        with self.assertRaisesRegex(CRI.CitedRunInputError, "已存在"):
            CRI.stage_run_input(_uploads(), staged, declared=self.declared,
                                run_id="m930_3_cited_upload_h")
        self.assertEqual((staged / CRI.MANIFEST_NAME).read_bytes(), before)

    def test_sha_not_in_the_current_registration_is_refused(self) -> None:
        """上传的三份必须就是**当前登记**的三份：清单与登记不是同一组就拒。"""
        staged = self.stage("m930_3_cited_upload_i")
        forged = tuple(dataclasses.replace(doc, sha256="0" * 64)
                       if doc.document_id == self.declared[0].document_id else doc
                       for doc in self.declared)
        with self.assertRaisesRegex(CRI.CitedRunInputError, "登记不是同一组材料"):
            CRI.load_run_input(staged, declared=forged)

    def test_resolver_miss_never_falls_back_to_a_registered_or_sample_path(self) -> None:
        staged = self.stage("m930_3_cited_upload_j")
        resolver = CRI.RunInputResolver.from_dir(staged, declared=self.declared)
        with self.assertRaisesRegex(CRI.CitedRunInputError, "data/samples"):
            resolver.resolve(document_id="NDSD_2024_year_announcement",
                             file_sha256="0" * 64)

    def test_uploaded_bytes_are_what_lands_on_disk(self) -> None:
        uploads = _uploads()
        staged = self.stage("m930_3_cited_upload_k", uploads)
        binding = CRI.load_run_input(staged)
        for (name, data), obj in zip(uploads, binding.documents):
            self.assertEqual(obj.uploaded_name, name)
            self.assertEqual((staged / obj.object_relpath).read_bytes(), data)


class FinancialInputStagingTests(_SampleCase):
    """`cfi-1` 的建立与读回：三份 XLSX 必须与**当前有效快照**的 `source_versions` 逐份同字节。

    与 `cri-1` 同一条纪律：只看**内容哈希**，文件名不参与查找；缺一、多一、重复、任一字节
    不符都**具名拒**，且绝不回退到 `data/samples` 或旧快照。这两条绑定落在**同一个**目录里，
    互不覆盖对方的文件。
    """

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        if not FINANCIAL_DB.is_file():
            raise unittest.SkipTest("财务库不在本机；不伪造财务权威")

    def _snapshot(self):
        return cfi.current_snapshot_identity(
            FINANCIAL_DB, subject=COMPANY_SUBJECT, subject_name=_subject_name())

    def test_three_workbooks_stage_and_read_back(self) -> None:
        run_id = "m930_3_cited_upload_fa"
        staged = self.stage(run_id, financial=True)
        binding = cfi.load_financial_input(staged, declared=_fin_declared(),
                                           snapshot=self._snapshot())
        self.assertEqual(binding.run_id, run_id)
        self.assertEqual(len(binding.sources), 3)
        self.assertEqual(binding.source_role, cfi.FINANCIAL_SOURCE_ROLE)
        #: 两条绑定共存于同一目录，谁也不覆盖谁。
        self.assertTrue((staged / CRI.MANIFEST_NAME).is_file())
        self.assertTrue((staged / cfi.BINDING_FILENAME).is_file())
        self.assertEqual(len(CRI.load_run_input(staged).documents), 3)

    def test_resolver_carries_the_snapshot_source_versions(self) -> None:
        staged = self.stage("m930_3_cited_upload_fb", financial=True)
        declared = _fin_declared()
        resolver = cfi.FinancialInputResolver.from_dir(staged, declared=declared,
                                                       snapshot=self._snapshot())
        for source in declared:
            path = resolver.resolve(source_version=source.source_version,
                                    file_sha256=source.file_sha256)
            self.assertTrue(resolver.owns(path))
            self.assertEqual(CRI.sha256_file(path), source.file_sha256)

    def test_missing_workbook_is_refused(self) -> None:
        staged = self.tmp / "run_inputs" / "m930_3_cited_upload_fc"
        CRI.stage_run_input(_uploads(), staged, declared=self.declared,
                            run_id="m930_3_cited_upload_fc")
        with self.assertRaisesRegex(cfi.CitedFinancialInputError, "缺少快照声明"):
            self.stage_financial(staged, "m930_3_cited_upload_fc", _fin_uploads()[:2])

    def test_extra_workbook_is_refused(self) -> None:
        staged = self.tmp / "run_inputs" / "m930_3_cited_upload_fd"
        CRI.stage_run_input(_uploads(), staged, declared=self.declared,
                            run_id="m930_3_cited_upload_fd")
        uploads = _fin_uploads() + [("renamed_extra.xlsx", b"not a real workbook")]
        with self.assertRaisesRegex(cfi.CitedFinancialInputError, "快照来源之外"):
            self.stage_financial(staged, "m930_3_cited_upload_fd", uploads)

    def test_duplicate_uploads_are_refused(self) -> None:
        staged = self.tmp / "run_inputs" / "m930_3_cited_upload_fe"
        CRI.stage_run_input(_uploads(), staged, declared=self.declared,
                            run_id="m930_3_cited_upload_fe")
        uploads = _fin_uploads()
        uploads[1] = (uploads[1][0], uploads[0][1])   # 两份内容相同
        with self.assertRaisesRegex(cfi.CitedFinancialInputError, "内容相同"):
            self.stage_financial(staged, "m930_3_cited_upload_fe", uploads)

    def test_one_byte_change_is_refused(self) -> None:
        staged = self.tmp / "run_inputs" / "m930_3_cited_upload_ff"
        CRI.stage_run_input(_uploads(), staged, declared=self.declared,
                            run_id="m930_3_cited_upload_ff")
        uploads = _fin_uploads()
        uploads[0] = (uploads[0][0], uploads[0][1] + b"x")
        with self.assertRaisesRegex(cfi.CitedFinancialInputError, "缺少快照声明"):
            self.stage_financial(staged, "m930_3_cited_upload_ff", uploads)

    def test_same_name_impostor_is_refused_because_lookup_is_by_hash(self) -> None:
        """把登记名原样抄来、内容换成别的字节：按文件名找就会放行，按哈希就拒。"""
        staged = self.tmp / "run_inputs" / "m930_3_cited_upload_fg"
        CRI.stage_run_input(_uploads(), staged, declared=self.declared,
                            run_id="m930_3_cited_upload_fg")
        uploads = _fin_uploads()
        uploads[0] = (uploads[0][0], b"PK\x03\x04 impostor bytes")
        with self.assertRaises(cfi.CitedFinancialInputError):
            self.stage_financial(staged, "m930_3_cited_upload_fg", uploads)

    def test_binding_is_create_only_in_the_shared_directory(self) -> None:
        staged = self.stage("m930_3_cited_upload_fh", financial=True)
        with self.assertRaisesRegex(cfi.CitedFinancialInputError, "拒绝覆盖"):
            self.stage_financial(staged, "m930_3_cited_upload_fh")

    def test_resolver_miss_never_falls_back_to_a_sample_path(self) -> None:
        staged = self.stage("m930_3_cited_upload_fi", financial=True)
        resolver = cfi.FinancialInputResolver.from_dir(staged, declared=_fin_declared(),
                                                       snapshot=self._snapshot())
        with self.assertRaisesRegex(cfi.CitedFinancialInputError, "data/samples"):
            resolver.resolve(source_version="sv-not-in-this-run", file_sha256="0" * 64)

    def test_stale_snapshot_identity_is_refused_on_read_back(self) -> None:
        """读回时若声明的快照与盘上那份不是同一条，就拒——不许「差不多对上」。"""
        staged = self.stage("m930_3_cited_upload_fj", financial=True)
        snapshot = self._snapshot()
        renamed = dataclasses.replace(snapshot, snapshot_id="snap-somewhere-else")
        with self.assertRaises(cfi.CitedFinancialInputError):
            cfi.load_financial_input(staged, declared=_fin_declared(), snapshot=renamed)


class ChainRefusesForeignRunInput(_SampleCase):
    """链自己的入口断言：run_id 不符、上传对象丢失、目录不是本 run 的 —
    三样都在**建立结果目录之前**停下。"""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        try:
            from scripts import run_m930_3_cited_chain as chain
        except Exception as exc:  # noqa: BLE001 - 导入不起来就跳过，不伪装跑过
            raise unittest.SkipTest(f"链模块导入不起来：{exc}") from exc
        cls.chain = chain

    def setUp(self) -> None:
        super().setUp()
        from evaluation import run_m930_3_acceptance as ACC

        saved = ACC._DEMO_SCOPE_PROFILE_PATH
        self.addCleanup(setattr, ACC, "_DEMO_SCOPE_PROFILE_PATH", saved)
        self.results_root = self.tmp / "results"

    def _run(self, run_id: str, staged: Path) -> None:
        self.chain.run(results_root=self.results_root, run_id=run_id,
                       profile_path=self.chain.BALANCE_STRUCTURE_PROFILE,
                       subject=COMPANY_SUBJECT, subject_name=_subject_name(),
                       section_ids=("company", "financial"), mode="offline",
                       run_input=staged, financial_input=staged)

    def test_another_runs_uploaded_bytes_are_refused(self) -> None:
        staged = self.stage("m930_3_cited_upload_l")
        with self.assertRaises(SystemExit) as ctx:
            self._run("m930_3_cited_upload_m", staged)
        self.assertIn("run_id", str(ctx.exception))
        self.assertFalse((self.results_root / "m930_3_cited_upload_m").exists())

    def test_missing_object_stops_before_the_run_directory_is_created(self) -> None:
        run_id = "m930_3_cited_upload_n"
        staged = self.stage(run_id)
        objects = staged / CRI.OBJECTS_DIRNAME
        next(objects.iterdir()).unlink()
        with self.assertRaises(SystemExit) as ctx:
            self._run(run_id, staged)
        message = str(ctx.exception)
        self.assertIn("fail-closed", message)
        self.assertIn("不回退到登记路径", message)
        self.assertFalse((self.results_root / run_id).exists())

    def test_a_run_input_directory_that_is_not_ours_is_refused(self) -> None:
        """给一个空目录冒充运行输入：没有清单 ⇒ 拒，且不建结果目录。"""
        empty = self.tmp / "not_a_run_input"
        empty.mkdir()
        with self.assertRaises(SystemExit) as ctx:
            self._run("m930_3_cited_upload_o", empty)
        self.assertIn("fail-closed", str(ctx.exception))
        self.assertFalse((self.results_root / "m930_3_cited_upload_o").exists())


class UploadScreensTests(_SampleCase):
    """页面四件事：点击只落盘一次、模式不写死、拿不到本 run 就不回落历史、深链要拒。"""

    def setUp(self) -> None:
        super().setUp()
        import scripts.cited_demo_app as app

        self.app = app
        self.results_root = self.tmp / "results"
        self.run_input_root = self.tmp / "run_inputs"
        self.auth_root = self.tmp / "authorizations"
        self.results_root.mkdir()
        self.run_input_root.mkdir()
        patcher = patch.dict(os.environ, {
            "CITED_DEMO_RESULTS_ROOT": str(self.results_root),
            "CITED_DEMO_RUN_INPUT_ROOT": str(self.run_input_root),
            "CITED_DEMO_AUTHORIZATION_ROOT": str(self.auth_root),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---------------------------------------------------------------- 第 1 屏

    def _grant_for(self, run_id: str) -> None:
        """按**本次上传的六份字节**给一个 run-id 批一份凭据（模拟人跑授权 CLI 那一步）。"""
        probe = self.tmp / "grant_probe"
        binding = CRI.stage_run_input(_uploads(), probe, declared=self.declared, run_id=run_id)
        declared = _fin_declared()
        snapshot = cfi.current_snapshot_identity(
            FINANCIAL_DB, subject=COMPANY_SUBJECT, subject_name=_subject_name())
        financial = cfi.stage_financial_input(_fin_uploads(declared), probe, declared=declared,
                                              snapshot=snapshot, run_id=run_id)
        model = str(__import__("config").LLM_MODEL or "").strip()
        policy = CB.cited_call_budget_policy(approved_model=model)
        profile = SC_scope.load_demo_scope_profile(REPO / self.app._DEMO_PROFILE)
        CRA.write_authorization(self.auth_root, CRA.build_authorization(
            granted_by="本用例的批准人", run_id=run_id, mode="real", subject=COMPANY_SUBJECT,
            section_ids=("company", "financial"), model=model, policy=policy, binding=binding,
            financial_binding=financial, profile=profile))

    def _click(self, *, mode: str, fin_uploads=None, uploads=None):
        """点一次「开始生成」，参数用本机真实的六份字节。返回 `(harness, launched, staged)`。"""
        harness = _Harness()
        launched: list[list[str]] = []

        class _Proc:
            def poll(self):
                return None

        def fake_popen(command, **kwargs):
            launched.append(list(command))
            return _Proc()

        payload = uploads if uploads is not None else _uploads()
        fin = fin_uploads if fin_uploads is not None else _fin_uploads()
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.app, "st", harness.st))
            stack.enter_context(patch.object(self.app.subprocess, "Popen", fake_popen))
            self.app._launch_new_run(payload, self.declared, fin, COMPANY_SUBJECT,
                                     _subject_name(), mode)
        return harness, launched

    def _run_click(self, *, mode: str, run_id: str | None = None,
                   fin_uploads=None, uploads=None):
        harness = _Harness()
        launched: list[list[str]] = []

        class _Proc:
            def poll(self):
                return None

        def fake_popen(command, **kwargs):
            launched.append(list(command))
            return _Proc()

        payload = uploads if uploads is not None else _uploads()
        fin = fin_uploads if fin_uploads is not None else _fin_uploads()
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.app, "st", harness.st))
            stack.enter_context(patch.object(self.app.subprocess, "Popen", fake_popen))
            if run_id is not None:
                stack.enter_context(
                    patch.object(self.app, "_new_run_id", lambda *a, **k: run_id))
            self.app._launch_new_run(payload, self.declared, fin, COMPANY_SUBJECT,
                                     _subject_name(), mode)
        return harness, launched

    def test_one_click_stages_once_and_launches_this_run(self) -> None:
        harness, launched = self._run_click(mode="offline")
        self.assertEqual(len(launched), 1)
        command = launched[0]
        run_id = harness.session_state["upload_run_id"]
        self.assertTrue(run_id.startswith(self.app._NEW_RUN_PREFIX))
        self.assertEqual(command[command.index("--run-id") + 1], run_id)
        staged = Path(command[command.index("--run-input") + 1])
        self.assertEqual(staged, self.run_input_root / run_id)
        #: 修正点一：模式**不写死**，逐字来自调用参数。获批之后同一个上传入口走真实路径。
        self.assertEqual(command[command.index("--mode") + 1], "offline")
        self.assertEqual(command[command.index("--section") + 1], "company,financial")
        self.assertEqual(command[command.index("--subject") + 1], COMPANY_SUBJECT)
        self.assertEqual(command[command.index("--subject-name") + 1], _subject_name())
        self.assertEqual(command[command.index("--financial-input") + 1], str(staged))
        self.assertEqual(command[command.index("--demo-scope-profile") + 1],
                         self.app._DEMO_PROFILE)
        self.assertEqual(command[command.index("--authorization-root") + 1], str(self.auth_root))
        self.assertEqual(harness.session_state["cited_demo_view"],
                         self.app._VIEW_OPTIONS[1])
        #: 落盘的正是本次上传的六份字节，两条绑定各自可读回、逐字节复核过。
        binding = CRI.load_run_input(staged, declared=self.declared)
        self.assertEqual(len(binding.documents), 3)
        fin_binding = cfi.load_financial_input(staged)
        self.assertEqual(len(fin_binding.sources), 3)
        self.assertEqual(harness.session_state["upload_launch_note"][0], "ok")
        #: 回调**不**建结果目录（那是链的事），也不会写出第二份输入。
        self.assertFalse((self.results_root / run_id).exists())
        self.assertEqual(sorted(p.name for p in self.run_input_root.iterdir()), [run_id])

    def test_real_mode_without_a_grant_launches_nothing(self) -> None:
        #: 页面提示、模式单选框、每-run 预算——三者都不是授权。没有凭据就不拉起进程。
        harness, launched = self._run_click(mode="real")
        self.assertEqual(launched, [], "无一次性授权时不得拉起任何进程")
        kind, message = harness.session_state["upload_launch_note"]
        self.assertEqual(kind, "blocked")
        self.assertIn("未发起任何请求", message)
        run_id = harness.session_state["upload_run_id"]
        #: 上传字节仍然落盘（它是这次点击的产物），但结果目录一个都没建。
        self.assertTrue((self.run_input_root / run_id).is_dir())
        self.assertFalse((self.results_root / run_id).exists())

    def test_real_mode_with_a_matching_grant_launches_once(self) -> None:
        run_id = "m930_3_cited_upload_20261005T120000Z"
        self._grant_for(run_id)
        harness, launched = self._run_click(mode="real", run_id=run_id)
        self.assertEqual(len(launched), 1)
        self.assertEqual(harness.session_state["upload_launch_note"][0], "ok")
        self.assertEqual(launched[0][launched[0].index("--mode") + 1], "real")

    def test_a_consumed_grant_is_not_treated_as_a_grant(self) -> None:
        run_id = "m930_3_cited_upload_20261005T130000Z"
        self._grant_for(run_id)
        authorization = CRA.load_authorization(self.auth_root, run_id=run_id)
        CRA.consume_authorization(self.auth_root, authorization)
        harness, launched = self._run_click(mode="real", run_id=run_id)
        self.assertEqual(launched, [], "已消费的凭据不得再启动一次运行")
        kind, message = harness.session_state["upload_launch_note"]
        self.assertEqual(kind, "blocked")
        self.assertIn("已被消费", message)

    def test_a_bad_financial_upload_rolls_the_fresh_input_dir_back(self) -> None:
        """财务那一半没建立起来 ⇒ 整个刚建出来的运行输入目录被撤掉，不留半份输入。"""
        harness, launched = self._click(mode="offline",
                                        fin_uploads=_fin_uploads()[:2])
        self.assertEqual(launched, [])
        self.assertEqual(harness.session_state["upload_launch_note"][0], "error")
        self.assertNotIn("upload_run_id", harness.session_state)
        self.assertEqual(list(self.run_input_root.iterdir()), [])

    def test_starting_an_authorized_pending_run_spawns_the_same_run_id(self) -> None:
        #: 第 2 屏那个「启动已授权的运行」按钮走的就是这条路径：**同一个** run-id，
        #: 不重新铸号，因此消费的仍然是同一份凭据。
        run_id = "m930_3_cited_upload_20261005T140000Z"
        self._grant_for(run_id)
        staged = self.run_input_root / run_id
        CRI.stage_run_input(_uploads(), staged, declared=self.declared, run_id=run_id)
        self.stage_financial(staged, run_id)
        harness = _Harness(session={"upload_subject": COMPANY_SUBJECT, "upload_mode": "real",
                                    "upload_subject_name": _subject_name()})
        launched: list[list[str]] = []

        class _Proc:
            def poll(self):
                return None

        with patch.object(self.app, "st", harness.st), \
             patch.object(self.app.subprocess, "Popen",
                          lambda command, **kw: (launched.append(list(command)), _Proc())[1]):
            self.assertTrue(self.app._spawn_new_run(staged, run_id, "real"))
        self.assertEqual(len(launched), 1)
        self.assertEqual(launched[0][launched[0].index("--run-id") + 1], run_id)

    def test_launch_is_refused_when_the_upload_does_not_match(self) -> None:
        harness = _Harness()
        uploads = _uploads()
        uploads[0] = (uploads[0][0], uploads[0][1] + b"x")
        with patch.object(self.app, "st", harness.st), \
             patch.object(self.app.subprocess, "Popen") as popen:
            self.app._launch_new_run(uploads, self.declared, _fin_uploads(),
                                     COMPANY_SUBJECT, _subject_name(), "offline")
        popen.assert_not_called()
        self.assertNotIn("upload_run_id", harness.session_state)
        self.assertEqual(harness.session_state["upload_launch_note"][0], "error")
        self.assertEqual(list(self.run_input_root.iterdir()), [])

    def test_upload_screen_enables_generation_only_on_an_exact_match(self) -> None:
        harness = _Harness()
        harness.set_uploads([_FakeUpload(name, data) for name, data in _uploads()])
        harness.box["fin_uploads"] = [_FakeUpload(name, data)
                                      for name, data in _fin_uploads()]
        with patch.object(self.app, "st", harness.st), \
             patch.object(self.app, "_subject_candidates",
                          return_value=(COMPANY_SUBJECT,)), \
             patch.object(self.app, "_declaration_for", return_value=self.declared):
            self.app._render_new_run_upload()
        blob = harness.text()
        self.assertTrue(any("3 / 3 一致" in text for text in harness.by_name("success")))
        self.assertIn("财务节 3 / 3", blob)
        self.assertEqual(harness.by_name("error"), [])
        #: 「开始生成」是**回调**，参数里必须带上两组材料；两节都在本次范围里。
        buttons = harness.by_name("button")
        self.assertTrue(any("开始生成" in b for b in buttons))

    def test_upload_screen_refuses_a_non_exact_match_without_a_skip_option(self) -> None:
        harness = _Harness()
        harness.set_uploads([_FakeUpload(name, data) for name, data in _uploads()[:2]])
        harness.box["fin_uploads"] = [_FakeUpload(name, data)
                                      for name, data in _fin_uploads()]
        with patch.object(self.app, "st", harness.st), \
             patch.object(self.app, "_subject_candidates",
                          return_value=(COMPANY_SUBJECT,)), \
             patch.object(self.app, "_declaration_for", return_value=self.declared):
            self.app._render_new_run_upload()
        blob = harness.text()
        self.assertIn("2 / 3 一致", blob)
        self.assertIn("不提供「跳过校验继续」的入口", blob)
        self.assertNotIn("公司节 3 / 3 一致", blob)

    def test_deep_link_without_this_sessions_upload_is_refused(self) -> None:
        harness = _Harness(query={"view": "3"})
        with patch.object(self.app, "st", harness.st), \
             patch.object(self.app.binding_mod, "load_display_binding",
                          side_effect=ValueError("历史绑定对不上")), \
             patch.object(self.app, "_subject_candidates", return_value=()):
            self.app.main()
        self.assertEqual(harness.session_state["cited_demo_view"],
                         self.app._VIEW_OPTIONS[0])
        self.assertIn("深链", harness.text())
        #: 被拒之后**没有**渲染历史对照屏，也没把拒绝原因留在会话里再弹一次。
        self.assertNotIn("历史展示绑定无法建立", harness.text())
        self.assertNotIn("cited_deeplink_refused", harness.session_state)

    def test_deep_link_to_the_history_screen_is_allowed(self) -> None:
        harness = _Harness(query={"view": "4"})
        with patch.object(self.app, "st", harness.st), \
             patch.object(self.app.binding_mod, "load_display_binding",
                          side_effect=ValueError("历史绑定对不上")):
            self.app.main()
        blob = harness.text()
        self.assertIn("历史对照 · 非本次生成", blob)
        self.assertIn("历史展示绑定无法建立", blob)

    # ---------------------------------------------------------------- 第 2、3 屏

    def test_new_run_screens_never_fall_back_to_a_historical_run(self) -> None:
        """历史 run id 当作本次 run 读 ⇒ 具名拒，且**不碰**任何历史读入口。"""
        for run_id in HISTORICAL_RUNS:
            #: 目录存在、但没有 `run_input_binding.json`——历史 run 的常态。
            (self.results_root / run_id).mkdir()
            for screen in (self.app._render_new_run_process,
                           self.app._render_new_run_deliverable):
                harness = _Harness(session={"upload_run_id": run_id})
                with self.subTest(run_id=run_id, screen=screen.__name__), \
                     patch.object(self.app, "st", harness.st), \
                     patch.object(self.app.upload_view, "load_upload_run",
                                  side_effect=AssertionError(
                                      "拿不到绑定就不该走到本 run 的产物读入口")):
                    screen()
                blob = harness.text()
                self.assertIn(self.app.run_input_mod.BINDING_NAME, blob)
                self.assertIn("不回落显示历史 run", blob)

    def test_new_run_screens_never_reach_for_the_historical_reader(self) -> None:
        """源码面：第 1～3 屏的渲染函数**一个**都不提历史读入口。

        两个**刻意不在**禁止之列的共用件：`binding_mod.verify_source_documents`（第 1 屏拿它
        核对「本次上传的字节 vs 当前 Evidence 登记」，声明侧与 `stage_run_input` 同一份权威，
        不是历史产物）与 `_render_a1`（按节对象鸭子类型渲染的通用呈现件，本次上传的
        `UploadRunSection` 是它的结构孪生，不是回落）。禁止的是**历史 run 的装载入口**。
        """
        blocks: dict[str, list[str]] = {}
        current: str | None = None
        for line in (REPO / "scripts" / "cited_demo_app.py").read_text(
                encoding="utf-8").splitlines():
            if line.startswith("def "):
                current = line[4:].split("(")[0]
                blocks[current] = []
            elif current is not None:
                blocks[current].append(line)
        for name in ("_render_new_run_upload", "_launch_new_run", "_require_bound_run",
                     "_render_new_run_process", "_render_new_run_deliverable",
                     "_render_incomplete_run", "_render_run_integrity_failure",
                     "_render_section_artifacts", "_render_new_run_rail"):
            self.assertIn(name, blocks)
            body = "\n".join(blocks[name])
            for forbidden in ("load_display_binding", "demo_loader.", "load_cited_run",
                              "_render_report(", "_render_audit_detail(",
                              "_render_historical(", "_render_deliverable(",
                              "_render_process(", "_render_upload("):
                self.assertNotIn(forbidden, body, f"{name} 触及历史读入口 {forbidden!r}")

    def test_process_screen_reads_only_this_runs_journal(self) -> None:
        run_id = "m930_3_cited_upload_p"
        staged = self._bound_run(run_id)
        journal = CRJ.RunProgressJournal(self.results_root / run_id, run_id=run_id)
        journal.record("run_input_verified", "completed", detail="3 份上传对象")
        journal.record("writer_requested", "started", section_id="company")
        harness = _Harness(session={"upload_run_id": run_id,
                                    "upload_staged_dir": str(staged)})
        with patch.object(self.app, "st", harness.st):
            self.app._render_new_run_process()
        blob = harness.text()
        self.assertIn("run_input_verified", blob)
        self.assertIn("writer_requested", blob)
        self.assertIn("不可恢复", blob)
        self.assertIn("已落盘事件", " ".join(harness.by_name("progress")))
        # 没有任何历史 run 的回放节点混进来。
        self.assertNotIn("已存运行过程回放", blob)

    def test_process_screen_says_so_when_the_journal_is_absent(self) -> None:
        run_id = "m930_3_cited_upload_q"
        staged = self._bound_run(run_id)
        harness = _Harness(session={"upload_run_id": run_id,
                                    "upload_staged_dir": str(staged)})
        with patch.object(self.app, "st", harness.st):
            self.app._render_new_run_process()
        blob = harness.text()
        self.assertIn("进度日志未落盘", blob)
        self.assertNotIn("已落盘事件", " ".join(harness.by_name("progress")))

    def _bound_run(self, run_id: str) -> Path:
        """造一个「本入口创建过的一次运行」：产物目录里有 `run_input_binding.json`。"""
        staged = self.stage(run_id)
        run_dir = self.results_root / run_id
        run_dir.mkdir()
        (run_dir / CRI.BINDING_NAME).write_text(
            (staged / CRI.MANIFEST_NAME).read_text(encoding="utf-8"), encoding="utf-8")
        return staged

    # ---------------------------------------------------------------- 右栏

    def test_rail_keeps_the_six_axes_apart_and_says_unreviewed(self) -> None:
        """右栏：六个状态轴分开写；没有报告级审阅就写「未审」；人工确认照实报 0 / N。"""
        section = SimpleNamespace(
            report_version={"process_state": "COMPLETED", "preview_state": "PREVIEWABLE",
                            "system_review_state": "NOT_PASSED",
                            "human_review_state": "NOT_REVIEWED",
                            "publishability": "NOT_PUBLISHABLE"},
            hard_sentence_ids=("s0001",), sentence_count=3,
            writer_producer=self.app.upload_view.PRODUCER_STANDIN,
            writer_is_model=False, review_is_model=False, review_available=False,
            review_attested=False, review_issues=())
        view = SimpleNamespace(sections={"company": section}, run_input_verified=True,
                               run_input_note="上传字节 == 本 run 实读对象",
                               financial_input_verified=True,
                               financial_input_note="上传 XLSX == 快照 source_versions",
                               source_regions=())
        harness = _Harness()
        with patch.object(self.app, "st", harness.st):
            self.app._render_new_run_rail(view)
        blob = harness.text()
        for axis in ("流程", "预览", "机械核对", "系统审核", "人工接受", "发布资格"):
            self.assertIn(axis, blob)
        self.assertIn("1 / 3 句硬错", blob)
        #: 没有报告级审阅 ⇒ 「未审」，而不是从本节意见推出一份报告级结论。
        self.assertIn("未审", blob)
        self.assertIn("离线替身产出", blob)
        #: 本次真实审阅没跑 ⇒ 明说，不写成「审阅已通过」。
        self.assertIn("本次真实审阅未运行", blob)
        self.assertIn("0 / 0", blob)
        #: 请求的财务节没有产出 ⇒ 明写缺哪一节，不把公司节的读数读成双节成功。
        self.assertIn("没有全部产出", blob)
        #: 两条输入证明各写各的：PDF 走 cri-1，XLSX 走快照来源版本。
        self.assertIn("业务 PDF：已证明", blob)
        self.assertIn("财务 XLSX：已证明", blob)

    def test_rail_shows_the_writer_producer_tri_state(self) -> None:
        """右栏对写作产出者有三档写法，且**不**把第三档折进前两档里。

        这三档是本批要修的那处误标的出口：旧写法只有「真实」与「离线」两档，读空时静默
        落进「离线」，于是把一次真实写作显示成离线替身。三档必须都能**显式**出现。
        """
        cases = (
            (self.app.upload_view.PRODUCER_MODEL, "真实模型产出", "离线替身产出"),
            (self.app.upload_view.PRODUCER_STANDIN, "离线替身产出", "真实模型产出"),
            (self.app.upload_view.PRODUCER_UNVERIFIABLE, "产出者无法核实", "真实模型产出"),
        )
        for producer, want, absent in cases:
            section = SimpleNamespace(
                report_version={"process_state": "COMPLETED", "preview_state": "PREVIEWABLE",
                                "system_review_state": "NOT_PASSED",
                                "human_review_state": "NOT_REVIEWED",
                                "publishability": "NOT_PUBLISHABLE"},
                hard_sentence_ids=(), sentence_count=0, writer_producer=producer,
                writer_is_model=producer == self.app.upload_view.PRODUCER_MODEL,
                review_is_model=False, review_available=False,
                review_attested=False, review_issues=())
            view = SimpleNamespace(sections={"company": section}, run_input_verified=True,
                                   run_input_note="x", financial_input_verified=True,
                                   financial_input_note="y", source_regions=())
            harness = _Harness()
            with patch.object(self.app, "st", harness.st):
                self.app._render_new_run_rail(view)
            blob = harness.text()
            self.assertIn(want, blob, f"producer={producer} 应显示 {want!r}")
            self.assertNotIn(absent, blob,
                             f"producer={producer} 不得同时写成 {absent!r}（三档互斥）")


class WriterProducerReadingTests(_SampleCase):
    """写作产出者的**三档**读数：真实 / 离线替身 / 无法核实。

    本批修的第一处缺陷：页面把一次**真实写作**显示成了「离线替身」。根因是旧读法读
    `cited_prose.json` 的 `call.model_policy`——那个键**只有审阅记录才有**，写作记录里根本
    不存在，于是对每一次真实写作都读出空串。修法是交叉核对三处证据（写作记录 + 调用日志 +
    本 run 账本），并把「证据缺失/矛盾」单列成第三档，不再静默落进「离线」。

    这个类的用例**不**跑链、**不**发请求：纯判据的真值表 + 一次只读的真实产物读数。
    """

    def _ledger(self, *, mode: str, attempts: list[dict]) -> dict:
        return {"mode": mode, "run_outcome": "completed",
                "call_budget": {"attempts": attempts}}

    def _writing_attempt(self, *, call_id: str, section: str, model: str,
                         category: str = "cited_prose_writing", status: str = "ok") -> dict:
        return {"call_id": call_id, "category": category, "section_id": section,
                "model": model, "status": status, "axis": "writing"}

    def _real_prose_call(self) -> dict:
        return {"call_id": "e58c5af016224c26b71a5bb25658e2e1", "model": "deepseek-v4-pro",
                "status": "ok", "error": "", "prompt_version": "cited_company_prose_v1@cp-25"}

    def _real_journal(self) -> dict:
        return {"input": {"model": "deepseek-v4-pro", "model_policy": "real:deepseek-v4-pro"},
                "reply": {"call_id": "e58c5af016224c26b71a5bb25658e2e1",
                          "model": "deepseek-v4-pro", "status": "ok"}}

    def _offline_prose_call(self) -> dict:
        return {"call_id": "offline-extractive-stitcher@cwp-1", "model": "offline-extractive",
                "status": "ok", "error": "", "prompt_version": "cited_company_prose_v1@cp-19"}

    def _offline_journal(self) -> dict:
        return {"input": {"model": "", "model_policy": "offline_stub"},
                "reply": {"call_id": "offline-extractive-stitcher@cwp-1",
                          "model": "offline-extractive", "status": "ok"}}

    def _read(self, *, prose_call, journal, ledger, section_id="company") -> str:
        import scripts.cited_demo_app as app
        return app.upload_view.current_writer_producer(
            prose_call=prose_call, journal=journal, ledger=ledger, section_id=section_id)

    def test_real_when_all_three_sources_agree(self) -> None:
        ledger = self._ledger(mode="real", attempts=[
            self._writing_attempt(call_id="e58c5af016224c26b71a5bb25658e2e1",
                                  section="company", model="deepseek-v4-pro")])
        self.assertEqual(self._read(prose_call=self._real_prose_call(),
                                    journal=self._real_journal(), ledger=ledger),
                         "model")

    def test_standin_when_all_three_sources_say_offline(self) -> None:
        ledger = self._ledger(mode="offline", attempts=[])
        self.assertEqual(self._read(prose_call=self._offline_prose_call(),
                                    journal=self._offline_journal(), ledger=ledger),
                         "standin")

    def test_unverifiable_when_the_journal_is_missing(self) -> None:
        """没有调用日志：输入策略无从核实 ⇒ 第三档，**不**回落成「离线」。"""
        ledger = self._ledger(mode="real", attempts=[
            self._writing_attempt(call_id="e58c5af016224c26b71a5bb25658e2e1",
                                  section="company", model="deepseek-v4-pro")])
        self.assertEqual(self._read(prose_call=self._real_prose_call(),
                                    journal=None, ledger=ledger), "unverifiable")

    def test_unverifiable_when_the_ledger_has_no_matching_writing_attempt(self) -> None:
        """写作记录与日志都说真实，但本 run 账本里没有这次写作 ⇒ 第三档。"""
        ledger = self._ledger(mode="real", attempts=[])
        self.assertEqual(self._read(prose_call=self._real_prose_call(),
                                    journal=self._real_journal(), ledger=ledger),
                         "unverifiable")

    def test_unverifiable_when_the_ledger_only_recorded_the_review(self) -> None:
        """账本只记了**审阅**那一类（`axis` 同为 `writing`）⇒ 不能算作写作被核验。

        账本把写作与审阅都记在 `axis="writing"` 上，判别只能靠 `category`；拿审阅记录去
        顶写作记录，正是需要被挡住的那种「近似」。
        """
        ledger = self._ledger(mode="real", attempts=[
            self._writing_attempt(call_id="e58c5af016224c26b71a5bb25658e2e1",
                                  section="company", model="deepseek-v4-pro",
                                  category="cited_prose_review")])
        self.assertEqual(self._read(prose_call=self._real_prose_call(),
                                    journal=self._real_journal(), ledger=ledger),
                         "unverifiable")

    def test_unverifiable_when_the_section_differs(self) -> None:
        ledger = self._ledger(mode="real", attempts=[
            self._writing_attempt(call_id="e58c5af016224c26b71a5bb25658e2e1",
                                  section="financial", model="deepseek-v4-pro")])
        self.assertEqual(self._read(prose_call=self._real_prose_call(),
                                    journal=self._real_journal(), ledger=ledger),
                         "unverifiable")

    def test_unverifiable_when_the_model_name_disagrees(self) -> None:
        """账本记的模型名与写作记录不同 ⇒ 不是同一次调用 ⇒ 第三档。"""
        ledger = self._ledger(mode="real", attempts=[
            self._writing_attempt(call_id="e58c5af016224c26b71a5bb25658e2e1",
                                  section="company", model="some-other-model")])
        self.assertEqual(self._read(prose_call=self._real_prose_call(),
                                    journal=self._real_journal(), ledger=ledger),
                         "unverifiable")

    def test_unverifiable_when_the_call_succeeded_but_the_ledger_status_is_not_ok(self) -> None:
        ledger = self._ledger(mode="real", attempts=[
            self._writing_attempt(call_id="e58c5af016224c26b71a5bb25658e2e1",
                                  section="company", model="deepseek-v4-pro", status="error")])
        self.assertEqual(self._read(prose_call=self._real_prose_call(),
                                    journal=self._real_journal(), ledger=ledger),
                         "unverifiable")

    def test_unverifiable_when_the_journal_reply_is_a_different_call(self) -> None:
        journal = self._real_journal()
        journal["reply"]["call_id"] = "00000000000000000000000000000000"
        ledger = self._ledger(mode="real", attempts=[
            self._writing_attempt(call_id="e58c5af016224c26b71a5bb25658e2e1",
                                  section="company", model="deepseek-v4-pro")])
        self.assertEqual(self._read(prose_call=self._real_prose_call(),
                                    journal=journal, ledger=ledger), "unverifiable")

    def test_unverifiable_when_offline_record_meets_a_real_ledger_hit(self) -> None:
        """冲突：写作记录说离线，账本却记了一次同一 `call_id` 的**成功真实**写作。"""
        ledger = self._ledger(mode="real", attempts=[
            self._writing_attempt(call_id="offline-extractive-stitcher@cwp-1",
                                  section="company", model="offline-extractive")])
        self.assertEqual(self._read(prose_call=self._offline_prose_call(),
                                    journal=self._offline_journal(), ledger=ledger),
                         "unverifiable")

    def test_unverifiable_when_offline_record_meets_a_real_journal_policy(self) -> None:
        """冲突：写作记录说离线，调用日志的输入策略却说 `real:`。"""
        journal = self._real_journal()
        journal["reply"]["call_id"] = "offline-extractive-stitcher@cwp-1"
        journal["reply"]["model"] = "offline-extractive"
        ledger = self._ledger(mode="offline", attempts=[])
        self.assertEqual(self._read(prose_call=self._offline_prose_call(),
                                    journal=journal, ledger=ledger), "unverifiable")

    def test_only_the_three_declared_values_are_ever_returned(self) -> None:
        import scripts.cited_demo_app as app
        allowed = set(app.upload_view.PRODUCER_READINGS)
        self.assertEqual(allowed, {"model", "standin", "unverifiable"})
        samples = [
            (self._real_prose_call(), self._real_journal(),
             self._ledger(mode="real", attempts=[self._writing_attempt(
                 call_id="e58c5af016224c26b71a5bb25658e2e1", section="company",
                 model="deepseek-v4-pro")])),
            (self._offline_prose_call(), self._offline_journal(),
             self._ledger(mode="offline", attempts=[])),
            (None, None, None),
            ({}, {}, {}),
        ]
        for prose_call, journal, ledger in samples:
            self.assertIn(self._read(prose_call=prose_call, journal=journal, ledger=ledger),
                          allowed)


class RealRunProducerWiringTests(unittest.TestCase):
    """只读跑一次**真实产物**：页面读出的产出者必须与产物里的证据一致。

    正例用本机那次真实双节上传 run（全部调用成功）；反例把它的公司节**原样复制**到临时目录，
    只改三处证据之一，证明读数是**从产物内容**判出来的，而不是「运行目录叫 real 就记 real」。
    """

    def setUp(self) -> None:
        src = RESULTS_ROOT / REAL_UPLOAD_RUN
        if not (src / "company" / "cited_prose.json").is_file():
            raise unittest.SkipTest(f"基线真实 run 不在本机：{REAL_UPLOAD_RUN}")
        self.src = src
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _copy(self, run_id: str) -> Path:
        dst = self.tmp / run_id
        shutil.copytree(self.src / "company", dst / "company")
        shutil.copy2(self.src / "cited_call_ledger.json", dst / "cited_call_ledger.json")
        return dst

    def _load(self, run_id: str):
        import scripts.cited_demo_app as app
        return app.upload_view.load_upload_run(
            run_id, results_root=self.tmp, run_input_root=None, section_ids=("company",))

    def test_real_run_reads_as_model_on_both_sections(self) -> None:
        import scripts.cited_demo_app as app
        view = app.upload_view.load_upload_run(
            REAL_UPLOAD_RUN, results_root=RESULTS_ROOT, run_input_root=None,
            section_ids=("company", "financial"))
        for section_id in ("company", "financial"):
            section = view.sections[section_id]
            self.assertEqual(section.writer_producer, "model",
                             f"{section_id}：真实 run 应读出「模型产出」")
            self.assertTrue(section.writer_is_model)

    def test_swapping_only_the_prose_call_flips_the_reading_to_standin(self) -> None:
        """把公司节的写作记录与调用日志换成离线形状、账本换成离线 ⇒ 读数翻成替身。

        三处身份等式（report_version／清单／草稿／核对／审阅／缺口／预览）**一字未改**，
        因此这一翻只能是「产出者判据看的是内容」造成的。
        """
        dst = self._copy("m930_3_cited_upload_synth_offline")
        (dst / "cited_call_ledger.json").write_text(
            json.dumps({"mode": "offline", "run_outcome": "completed", "ledger": None},
                       ensure_ascii=False), encoding="utf-8")
        prose = json.loads((dst / "company" / "cited_prose.json").read_text(encoding="utf-8"))
        prose["call"] = {"call_id": "offline-extractive-stitcher@cwp-1",
                         "model": "offline-extractive", "status": "ok", "error": "",
                         "prompt_version": "cited_company_prose_v1@cp-19",
                         "finish_reason": None, "latency_ms": 0, "input_tokens": None,
                         "output_tokens": None, "response_hash": "synth"}
        (dst / "company" / "cited_prose.json").write_text(
            json.dumps(prose, ensure_ascii=False), encoding="utf-8")
        journal = json.loads(
            (dst / "company" / "cited_call_journal.json").read_text(encoding="utf-8"))
        journal["input"]["model_policy"] = "offline_stub"
        journal["input"]["model"] = ""
        journal["reply"]["call_id"] = "offline-extractive-stitcher@cwp-1"
        journal["reply"]["model"] = "offline-extractive"
        (dst / "company" / "cited_call_journal.json").write_text(
            json.dumps(journal, ensure_ascii=False), encoding="utf-8")
        section = self._load(dst.name).sections["company"]
        self.assertEqual(section.writer_producer, "standin")
        self.assertFalse(section.writer_is_model)

    def test_dropping_the_ledger_writing_attempt_yields_unverifiable(self) -> None:
        """账本里只剩审阅那一次（`axis` 同为 writing）⇒ 写作产出者「无法核实」。"""
        dst = self._copy("m930_3_cited_upload_synth_ledgerless")
        (dst / "cited_call_ledger.json").write_text(json.dumps({
            "mode": "real", "run_outcome": "completed",
            "call_budget": {"attempts": [{
                "call_id": "e67bd8fe2fd549b0bb91fd3078cf3841",
                "category": "cited_prose_review", "section_id": "company",
                "model": "deepseek-v4-pro", "status": "ok", "axis": "writing"}]}},
            ensure_ascii=False), encoding="utf-8")
        section = self._load(dst.name).sections["company"]
        self.assertEqual(section.writer_producer, "unverifiable")
        self.assertFalse(section.writer_is_model)


class CompletedRunIntegrityTests(_SampleCase):
    """账本记 `completed` 却少了一节或 A2：读侧**显著**失败，不画成双节成功。

    这是本批最容易被绕过的一条。链写了 `run_outcome=completed`、目录里也确实有公司节的产物；
    页面若按「请求的节里哪些有产物就显示哪些」来读，一次只跑出公司节的运行就会被读成「财务节
    也齐了」。判据必须在读侧，而且必须是**具名拒**，不是少显示一块、更不是回落历史 run。
    """

    def setUp(self) -> None:
        super().setUp()
        import scripts.cited_demo_app as app

        self.app = app
        self.results_root = self.tmp / "results"
        self.run_input_root = self.tmp / "run_inputs"
        self.results_root.mkdir()
        self.run_input_root.mkdir()
        patcher = patch.dict(os.environ, {
            "CITED_DEMO_RESULTS_ROOT": str(self.results_root),
            "CITED_DEMO_RUN_INPUT_ROOT": str(self.run_input_root),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def _completed_run(self, run_id: str) -> Path:
        """一个「本入口创建过、账本记 completed」的运行目录；节产物由各用例自己摆。"""
        staged = self.stage(run_id)
        run_dir = self.results_root / run_id
        run_dir.mkdir()
        (run_dir / CRI.BINDING_NAME).write_text(
            (staged / CRI.MANIFEST_NAME).read_text(encoding="utf-8"), encoding="utf-8")
        (run_dir / "cited_call_ledger.json").write_text(
            json.dumps({"run_outcome": "completed"}), encoding="utf-8")
        return run_dir

    def _load(self, run_id: str):
        return self.app.upload_view.load_upload_run(
            run_id, results_root=self.results_root, run_input_root=self.run_input_root,
            section_ids=("company", "financial"))

    def test_completed_but_a_requested_section_is_absent_is_refused(self) -> None:
        run_id = "m930_3_cited_upload_absent"
        self._completed_run(run_id)
        with self.assertRaises(self.app.upload_view.CitedUploadViewError) as ctx:
            self._load(run_id)
        self.assertIn("completed", str(ctx.exception))
        self.assertIn("没有产出", str(ctx.exception))

    def test_completed_but_a_section_is_half_written_is_refused(self) -> None:
        run_id = "m930_3_cited_upload_half"
        run_dir = self._completed_run(run_id)
        (run_dir / "company").mkdir()
        (run_dir / "company" / "cited_report_version.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(self.app.upload_view.CitedUploadViewError) as ctx:
            self._load(run_id)
        self.assertIn("不完整", str(ctx.exception))

    def test_completed_but_the_financial_section_lacks_a2_is_refused(self) -> None:
        """两节都在、账本 completed，但财务节没有 A2 结构产物 ⇒ 具名拒。

        这一条与「财务节整个没产出」是两条不同的判据（前者有 A1、只是缺 A2），因此用替身把
        `_load_section` 的产物面摆成一个**缺 A2** 的财务节，直接考 `load_upload_run` 那条规则。
        """
        run_id = "m930_3_cited_upload_noa2"
        run_dir = self._completed_run(run_id)
        for section_id in ("company", "financial"):
            (run_dir / section_id).mkdir()
            (run_dir / section_id / "cited_report_version.json").write_text("{}",
                                                                           encoding="utf-8")

        def fake_section(snapshot, run_dir, section_id, prefix, ledger):
            return SimpleNamespace(section_id=section_id, balance_structure=None)

        with patch.object(self.app.upload_view, "_load_section", fake_section), \
             self.assertRaises(self.app.upload_view.CitedUploadViewError) as ctx:
            self._load(run_id)
        self.assertIn("A2", str(ctx.exception))

    def test_the_deliverable_paints_the_integrity_failure_prominently(self) -> None:
        """第 3 屏拿到这条拒时，画的是**显著失败**，不是「少显示一块」。"""
        run_id = "m930_3_cited_upload_integrity"
        self._completed_run(run_id)
        harness = _Harness(session={"upload_run_id": run_id,
                                    "upload_staged_dir": str(self.run_input_root / run_id)})
        with patch.object(self.app, "st", harness.st):
            self.app._render_new_run_deliverable()
        blob = harness.text()
        self.assertIn("不得读作双节成功", blob)
        self.assertIn("跳过缺失的那一块", blob)
        self.assertIn("没有产出", blob)


#: 一次**真实**的双节运行，但账本记 `failed`：公司节的独立审阅回复把越界类别 `off_topic`
#: 写进了 `category`，整批回复作废，`company/review_issues.json` 是**失败形状**（没有 `issues`）。
#: 财务节审阅正常。只读、只作展示判据的基线。
FAILED_REVIEW_RUN = "m930_3_cited_upload_20261005T161431Z"
#: 一次**跑完**的真实双节运行（两节审阅都有效），作对照组。
COMPLETED_REAL_RUN = "m930_3_cited_upload_20261005T151012Z"
#: 一次**离线**双节运行：审阅有意见但账本里没有真实调用（`review_attested` 恒 False）。
OFFLINE_RUN = "m930_3_cited_upload_20261005T060856Z"


def _read_run(run_id: str, sections: tuple[str, ...] = ("company", "financial")):
    import scripts.cited_demo_app as app
    return app.upload_view.load_upload_run(
        run_id, results_root=RESULTS_ROOT, run_input_root=None, section_ids=sections)


class FailedReviewReadbackTests(unittest.TestCase):
    """「真实审阅调用已发生、但回复不合约」这一合法失败产物，读侧必须如实分开两条轴。"""

    def setUp(self) -> None:
        if not (RESULTS_ROOT / FAILED_REVIEW_RUN / "company" / "review_issues.json").is_file():
            raise unittest.SkipTest(f"基线失败 run 不在本机：{FAILED_REVIEW_RUN}")

    def test_company_call_happened_but_produced_no_opinions(self) -> None:
        view = _read_run(FAILED_REVIEW_RUN)
        self.assertTrue(view.failed)
        company = view.sections["company"]
        #: 没有意见——这一条**不**等于「审阅通过」，也**不**等于「审阅没跑」。
        self.assertFalse(company.review_available)
        self.assertEqual(company.review_issues, ())
        #: 调用**确实发生了**：call_id 取自失败形状的 `diagnostic`，并在本 run 账本里四项全中。
        self.assertTrue(company.review_attested)
        self.assertTrue(company.review_ran_without_opinions)
        self.assertEqual(company.review_failure, "review_reply_unparsable")
        ledger = json.loads(
            (RESULTS_ROOT / FAILED_REVIEW_RUN / "cited_call_ledger.json").read_text("utf-8"))
        attempts = ledger["call_budget"]["attempts"]
        hit = [a for a in attempts
               if a["call_id"] == company.review_call_id
               and a["category"] == "cited_prose_review"
               and a["section_id"] == "company" and a["status"] == "ok"]
        self.assertEqual(len(hit), 1, "失败形状的 call_id 必须与账本那一次审阅调用是同一条")

    def test_the_other_section_keeps_its_real_review(self) -> None:
        """一块审阅失败**不**把另一块的真实有效审阅一起抹掉。"""
        financial = _read_run(FAILED_REVIEW_RUN).sections["financial"]
        self.assertTrue(financial.review_available)
        self.assertTrue(financial.review_attested)
        self.assertFalse(financial.review_ran_without_opinions)
        self.assertGreater(len(financial.review_issues), 0)
        self.assertEqual(financial.review_failure, "")

    def test_completed_and_offline_runs_are_not_dragged_into_the_failure_branch(self) -> None:
        for run_id, want_attested in ((COMPLETED_REAL_RUN, True), (OFFLINE_RUN, False)):
            if not (RESULTS_ROOT / run_id).is_dir():
                raise unittest.SkipTest(f"基线 run 不在本机：{run_id}")
            for section_id in ("company", "financial"):
                section = _read_run(run_id).sections[section_id]
                self.assertTrue(section.review_available, f"{run_id}/{section_id}")
                self.assertFalse(section.review_ran_without_opinions,
                                 f"{run_id}/{section_id}：有意见就不该落进「调用无意见」档")
                self.assertEqual(section.review_attested, want_attested,
                                 f"{run_id}/{section_id}")

    def test_mechanical_red_is_the_whole_set_not_the_blocking_subset(self) -> None:
        """标红数取 `sentence_checks` 的事实安全族 ∪ 栏目覆盖族，**不拿**阻断子集冒充。"""
        company = _read_run(FAILED_REVIEW_RUN).sections["company"]
        hard = set(company.hard_sentence_ids)
        self.assertEqual(hard,
                         set(company.fact_safety_sentence_ids)
                         | set(company.column_coverage_sentence_ids))
        self.assertTrue(set(company.blocking_sentence_ids) < hard,
                        "本次基线里阻断子集应真地小于标红全集，否则这条用例证不了什么")

    def test_claimed_success_without_issues_is_still_refused(self) -> None:
        """反例：声称成功（`outcome="reviewed"`）却缺 `issues` ⇒ **当场拒**，不静默降级成零意见。

        这一条是本批要守住的另一头：宽容只给**明确声明没有产出**的那一种形状；一份声称审阅
        成功的产物缺 `issues` 时，页面绝不能把它当成「零条意见」照常显示。
        """
        import scripts.cited_demo_app as app
        src = RESULTS_ROOT / COMPLETED_REAL_RUN / "company" / "review_issues.json"
        if not src.is_file():
            raise unittest.SkipTest(f"基线 run 不在本机：{COMPLETED_REAL_RUN}")
        raw = json.loads(src.read_text(encoding="utf-8"))
        self.assertEqual(str(raw.get("outcome")), "reviewed")
        raw.pop("issues")
        with self.assertRaises(app.demo_loader.CitedDemoLoadError):
            app.upload_view._decode_review(raw)

    def test_review_index_never_silently_returns_zero_for_a_broken_shape(self) -> None:
        """`_review_index` 对声明的失败形状给空表，对**坏**形状**抛**——不写 `get(…, [])`。"""
        import scripts.cited_demo_app as app
        failed = _read_run(FAILED_REVIEW_RUN).sections["company"]
        self.assertEqual(app._review_index(failed), {})
        with self.assertRaises(TypeError):
            app._review_index(SimpleNamespace(review={"outcome": "reviewed"}))


class FailedReviewScreenTests(unittest.TestCase):
    """第③屏对这次失败 run 的实际画面读数（用页面自己的渲染器，不重写一份渲染）。"""

    def setUp(self) -> None:
        if not (RESULTS_ROOT / FAILED_REVIEW_RUN / "company" / "review_issues.json").is_file():
            raise unittest.SkipTest(f"基线失败 run 不在本机：{FAILED_REVIEW_RUN}")
        import scripts.cited_demo_app as app
        self.app = app
        self._patcher = patch.dict(os.environ, {"CITED_DEMO_RESULTS_ROOT": str(RESULTS_ROOT)})
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _render(self) -> _Harness:
        harness = _Harness(session={"upload_run_id": FAILED_REVIEW_RUN,
                                    "upload_staged_dir": f"unused-{FAILED_REVIEW_RUN}"})
        with patch.object(self.app, "st", harness.st):
            self.app._render_new_run_deliverable()
        return harness

    def test_the_page_renders_without_crashing_and_without_a_traceback(self) -> None:
        """本次要修的正是这一条：失败形状的审阅**不能**让整屏抛 `KeyError`。"""
        blob = self._render().text()
        self.assertNotIn("KeyError", blob)
        self.assertNotIn("这一块渲染失败", blob)

    def test_the_company_prose_and_citations_are_on_screen(self) -> None:
        harness = self._render()
        body = "\n".join(harness.by_name("markdown"))
        self.assertIn("report-paragraph", body)
        self.assertIn("报告正文", harness.text())
        #: 引用键随段落一起渲染出来了（`[m13]` 之类）。
        self.assertIn("report-citation", body)

    def test_the_run_level_failure_is_painted_and_says_the_call_happened(self) -> None:
        blob = self._render().text()
        self.assertIn("run_outcome=failed", blob)
        self.assertIn("整体失败", blob)
        self.assertIn("真实审阅调用已发生", blob)
        self.assertIn("review_reply_unparsable", blob)
        #: 不得把公司节写成「审阅未运行」，也不得写成「审阅通过」。
        self.assertNotIn("本次真实审阅未运行", blob)

    def test_the_financial_section_still_shows_its_real_review(self) -> None:
        blob = self._render().text()
        self.assertIn("真实独立 LLM 审阅", blob)
        self.assertIn("已产出意见", blob)

    def test_a_broken_block_does_not_blank_the_other_blocks(self) -> None:
        """反例：公司节审阅换成**坏形状**（既无 `review_issues`，`review` 里也无 `issues`）。

        旧代码在这里抛 `KeyError`，而 Streamlit 的 `st.tabs` 会因此 abort 掉同一次脚本运行里
        其余 panel 的渲染——连财务节的正文一起黑掉。隔离包裹之后：那一块报**具名错误**，
        财务节的正文与引用照常渲染。
        """
        view = _read_run(FAILED_REVIEW_RUN)
        good = view.sections["financial"]
        broken = _section_shim(view.sections["company"],
                               review={"outcome": "reviewed"}, review_issues=None)
        shim_view = SimpleNamespace(
            run_id=view.run_id, failed=view.failed, failure=view.failure,
            run_outcome=view.run_outcome, sections={"company": broken, "financial": good},
            source_regions=view.source_regions, file_hashes=view.file_hashes,
            run_input_verified=view.run_input_verified, run_input_note=view.run_input_note,
            financial_input_verified=view.financial_input_verified,
            financial_input_note=view.financial_input_note)
        harness = _Harness(session={"upload_run_id": FAILED_REVIEW_RUN})
        with patch.object(self.app, "st", harness.st), \
             patch.object(self.app.upload_view, "load_upload_run", return_value=shim_view):
            self.app._render_new_run_deliverable()
        blob = harness.text()
        #: 坏的那一块**显著**报错（不是被静默跳过）。
        self.assertIn("这一块渲染失败", blob)
        self.assertIn("TypeError", blob)
        #: 其余各块照常：财务节的正文段落与引用都在画面里。
        markdown = " ".join(harness.by_name("markdown"))
        self.assertIn("report-paragraph", markdown)
        self.assertIn("report-citation", markdown)
        self.assertIn(_first_sentence(good), markdown)
        #: A2 与 PDF 回查两块也没有被连坐。
        self.assertIn("A2", blob)
        self.assertIn("原 PDF 表格区域", blob)


def _section_shim(section, **overrides):
    """按节对象**自己的读数**做一份结构孪生件，供反例摆坏形状用。

    字段与属性逐个显式抄过来（不 `**vars()`）：`UploadRunSection` 的多数读数是 `@property`，
    `vars()` 抄不到；写死一份反而会在节对象长出新属性时悄悄漏掉。
    """
    names = ("section_id", "report_version", "manifest", "draft", "checks", "review",
             "metric_tables", "source_display", "gap_bins", "preview_markdown",
             "demo_markdown", "readback_markdown", "source_regions", "source_images",
             "initial_draft", "reviewed_outcome", "draft_source", "writer_call",
             "writer_producer", "writer_is_model", "review_is_model", "review_attested",
             "balance_structure", "hard_sentence_ids", "fact_safety_sentence_ids",
             "column_coverage_sentence_ids", "blocking_sentence_ids", "sentence_count",
             "review_available", "review_issues", "review_failure", "review_diagnostic",
             "review_call_id", "review_ran_without_opinions")
    shim = SimpleNamespace(**{name: getattr(section, name) for name in names})
    shim.__dict__.update(overrides)
    return shim


def _first_sentence(section) -> str:
    for subsection in section.draft["subsections"]:
        for paragraph in subsection["paragraphs"]:
            for sentence in paragraph["sentences"]:
                return str(sentence["text"])
    raise AssertionError("这一节没有句子；本用例的判据需要一个非空正文")


class LedgerOutcomeOnProcessScreenTests(unittest.TestCase):
    """②屏：账本落盘后**明确**写整轮结果，不用阶段事件推断。"""

    def setUp(self) -> None:
        if not (RESULTS_ROOT / FAILED_REVIEW_RUN / "cited_call_ledger.json").is_file():
            raise unittest.SkipTest(f"基线失败 run 不在本机：{FAILED_REVIEW_RUN}")
        import scripts.cited_demo_app as app
        self.app = app
        self._patcher = patch.dict(os.environ, {"CITED_DEMO_RESULTS_ROOT": str(RESULTS_ROOT)})
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def _render(self, run_id: str) -> _Harness:
        harness = _Harness(session={"upload_run_id": run_id})
        with patch.object(self.app, "st", harness.st):
            self.app._render_new_run_process()
        return harness

    def test_a_failed_ledger_shows_failed_even_though_both_sections_emitted(self) -> None:
        blob = self._render(FAILED_REVIEW_RUN).text()
        self.assertIn("run_outcome=failed", blob)
        self.assertIn("CitedReviewIncomplete", blob)

    def test_a_completed_ledger_shows_completed(self) -> None:
        blob = self._render(COMPLETED_REAL_RUN).text()
        self.assertIn("run_outcome=completed", blob)
        self.assertNotIn("run_outcome=failed", blob)


class OfflineDefaultCaptionTests(_SampleCase):
    """①屏一句文案：offline 是默认，**获批凭据不会**自动把模式改成 real。"""

    def setUp(self) -> None:
        super().setUp()
        import scripts.cited_demo_app as app
        self.app = app

    def _render(self) -> _Harness:
        harness = _Harness()
        harness.set_uploads([_FakeUpload(name, data) for name, data in _uploads()])
        harness.box["fin_uploads"] = [_FakeUpload(name, data)
                                      for name, data in _fin_uploads(self.declared_fin())]
        with patch.object(self.app, "st", harness.st), \
             patch.object(self.app, "_subject_candidates",
                          return_value=(COMPANY_SUBJECT,)), \
             patch.object(self.app, "_declaration_for", return_value=self.declared):
            self.app._render_new_run_upload()
        return harness

    def declared_fin(self):
        return _fin_declared()

    def test_the_mode_caption_says_offline_is_the_default(self) -> None:
        blob = self._render().text()
        self.assertIn("默认是 `offline`", blob)
        self.assertIn("不会", blob)
        self.assertIn("自动把这里改成 `real`", blob)

    def test_the_radio_still_defaults_to_offline_and_the_options_are_unchanged(self) -> None:
        harness = self._render()
        radios = [(args, kwargs) for name, args, kwargs in harness.box["calls"]
                  if name == "radio" and args and args[0] == "运行模式"]
        self.assertEqual(len(radios), 1, "①屏应恰有一个「运行模式」单选框")
        args, kwargs = radios[0]
        self.assertEqual(list(args[1]), ["offline", "real"])
        #: 默认仍是第一项（offline）：本批不改默认值，也不改授权门。
        self.assertEqual(kwargs.get("index"), 0)
        self.assertEqual(kwargs.get("key"), "cur_mode")


class _AnsweringStub(_Stub):
    """`_Stub` 加一条：把个别控件的**返回值**摆好（表单提交、已填好的输入框）。"""

    def __init__(self, box: dict, answers: dict) -> None:
        super().__init__(box)
        self._answers = answers

    def __getattr__(self, name: str):
        if name in self._answers:
            def answer(*args, **kwargs):
                self._box["calls"].append((name, args, kwargs))
                return self._answers[name]
            return answer
        return super().__getattr__(name)


class PendingRunPanelTests(_SampleCase):
    """第 2 屏在「结果目录还没建出来」那一段的两种读法，必须分开。

    链从「进程起来」到「`run_dir.mkdir`」之间有一两秒。页面在这段窗口里重新渲染时，结果目录
    确实还不存在——但那是「**已经启动、产物尚未落盘**」，不是「尚未启动」。两者的区别不只是
    措辞：后者的屏上挂着「启动本次运行」按钮，再点一次会在**同一个 run-id** 上拉起第二个进程，
    而它会用 `"wb"` 打开同一份 `chain.log` 把第一份截断，随后自己又在 create-only 的
    `run_dir.mkdir` 上被拒——留下的正是最不该有的东西：第一份日志没了，第二份什么也没写。
    """

    def setUp(self) -> None:
        super().setUp()
        import scripts.cited_demo_app as app

        self.app = app
        self.results_root = self.tmp / "results"
        self.run_input_root = self.tmp / "run_inputs"
        self.results_root.mkdir()
        self.run_input_root.mkdir()
        patcher = patch.dict(os.environ, {
            "CITED_DEMO_RESULTS_ROOT": str(self.results_root),
            "CITED_DEMO_RUN_INPUT_ROOT": str(self.run_input_root),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def _staged(self, run_id: str) -> Path:
        return CRI.stage_run_input(_uploads(), self.run_input_root / run_id,
                                   declared=self.declared, run_id=run_id)

    @staticmethod
    def _live_proc():
        class _Proc:
            def poll(self):
                return None  # 还在跑
        return _Proc()

    def _render(self, run_id: str, staged: Path, session: dict) -> "_Harness":
        harness = _Harness(session=session)
        with patch.object(self.app, "st", harness.st):
            self.app._render_pending_run(run_id, staged, "offline")
        return harness

    def test_a_run_this_session_launched_is_not_drawn_as_never_started(self) -> None:
        run_id = "m930_3_cited_upload_session_started"
        staged = self._staged(run_id)
        harness = self._render(run_id, staged, {"upload_run_id": run_id,
                                                "upload_process": self._live_proc()})
        blob = harness.text()
        self.assertIn("已启动、产物尚未落盘", blob)
        self.assertNotIn("尚未启动", blob)
        #: 正例的反面才是这条用例的真正判据：**不许**再给一个启动入口。
        self.assertEqual([call for call in harness.by_name("button")
                          if "启动本次运行" in call], [])
        self.assertTrue([call for call in harness.by_name("button") if "刷新" in call],
                        "已启动那一屏要能刷新，否则读者只能干等")

    def test_a_run_nobody_launched_still_offers_the_start_button(self) -> None:
        """反例：会话里没有这条运行的进程句柄时，才轮到「尚未启动」那句与启动按钮。"""
        run_id = "m930_3_cited_upload_not_started"
        staged = self._staged(run_id)
        harness = self._render(run_id, staged, {"upload_run_id": run_id})
        blob = harness.text()
        self.assertIn("尚未启动", blob)
        self.assertNotIn("已启动、产物尚未落盘", blob)
        self.assertTrue([call for call in harness.by_name("button")
                         if "启动本次运行" in call])

    def test_a_dead_process_handle_is_treated_as_not_launched_by_this_session(self) -> None:
        """进程已经退出但结果目录还没出现 ⇒ 那句「已启动」就不该再说。"""
        run_id = "m930_3_cited_upload_dead_proc"

        class _Dead:
            def poll(self):
                return 1
        harness = _Harness(session={"upload_run_id": run_id, "upload_process": _Dead()})
        with patch.object(self.app, "st", harness.st):
            self.assertFalse(self.app._spawned_by_this_session(run_id))
            self.app._render_pending_run(run_id, self._staged(run_id), "offline")
        self.assertIn("尚未启动", harness.text())
        self.assertNotIn("已启动、产物尚未落盘", harness.text())


class RunIdEntryTests(_SampleCase):
    """会话里没有 run 时的那个入口：按钮必须**始终**在，且读回来仍要过绑定门。"""

    def setUp(self) -> None:
        super().setUp()
        import scripts.cited_demo_app as app

        self.app = app
        self.results_root = self.tmp / "results"
        self.run_input_root = self.tmp / "run_inputs"
        self.results_root.mkdir()
        self.run_input_root.mkdir()
        patcher = patch.dict(os.environ, {
            "CITED_DEMO_RESULTS_ROOT": str(self.results_root),
            "CITED_DEMO_RUN_INPUT_ROOT": str(self.run_input_root),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_submit_button_renders_even_with_an_empty_box(self) -> None:
        """空输入框时也要有可点的东西。

        写成 `if typed and st.button(...)` 的话，按钮在输入框为空时**根本不渲染**——页面上写着
        「按 run-id 读取一次」，却没有任何可点的东西，只有先回车一次才把它召唤出来。
        """
        harness = _Harness()
        with patch.object(self.app, "st", harness.st):
            self.assertIsNone(self.app._pick_run(key="probe"))
        self.assertIn("本会话还没有创建过运行", harness.text())
        self.assertTrue([call for call in harness.by_name("form_submit_button")
                         if "读取该 run" in call],
                        "入口按钮没有渲染：读者会看到一个叫不应的提示")
        self.assertTrue(harness.by_name("form"), "入口应当在一个表单里，回车即提交")

    def test_a_submitted_run_id_is_adopted_and_still_checked_by_the_binding(self) -> None:
        run_id = "m930_3_cited_upload_typed"
        answers = {"text_input": run_id, "form_submit_button": True}
        harness = _Harness()
        stub = _AnsweringStub(harness.box, answers)
        with patch.object(self.app, "st", stub):
            picked = self.app._pick_run(key="probe")
        self.assertEqual(picked, run_id)
        self.assertEqual(harness.session_state["upload_run_id"], run_id)
        self.assertEqual(harness.session_state["upload_staged_dir"],
                         str(self.run_input_root / run_id))
        #: 读到的是一个**没有** `run_input_binding.json` 的目录 ⇒ 第 2 屏照样具名拒。
        (self.results_root / run_id).mkdir()
        screen = _Harness(session={"upload_run_id": run_id})
        with patch.object(self.app, "st", screen.st):
            self.app._render_new_run_process()
        self.assertIn(self.app.run_input_mod.BINDING_NAME, screen.text())
        self.assertIn("不回落显示历史 run", screen.text())

    def test_an_empty_submission_says_so_instead_of_guessing(self) -> None:
        harness = _Harness()
        stub = _AnsweringStub(harness.box, {"form_submit_button": True})
        with patch.object(self.app, "st", stub):
            self.assertIsNone(self.app._pick_run(key="probe"))
        self.assertIn("本页不猜 run-id", harness.text())
        self.assertNotIn("upload_run_id", harness.session_state)


class JournalDisciplineTests(unittest.TestCase):
    """`rj-1`：没有可恢复点就写没有；阶段名与状态不是随便拼的。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="m930_5_journal_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_events_carry_no_checkpoint_and_no_fabricated_progress(self) -> None:
        run_dir = self.tmp / "run"
        journal = CRJ.RunProgressJournal(run_dir, run_id="m930_3_cited_upload_r")
        event = journal.record("section_started", "started", section_id="company")
        self.assertFalse(event.resumable)
        self.assertEqual(event.checkpoint_id, "")
        summary = CRJ.progress_summary(run_dir)
        self.assertFalse(summary["resumable"])
        self.assertEqual(summary["event_count"], 1)
        self.assertEqual(summary["last_stage"], "section_started")

    def test_unregistered_stage_or_status_is_refused(self) -> None:
        journal = CRJ.RunProgressJournal(self.tmp / "run2", run_id="x")
        with self.assertRaises(CRJ.CitedRunJournalError):
            journal.record("started_thinking", "started")
        with self.assertRaises(CRJ.CitedRunJournalError):
            journal.record("section_started", "probably_fine")

    def test_missing_journal_reads_as_empty_not_as_an_error(self) -> None:
        self.assertEqual(CRJ.read_run_progress(self.tmp / "nowhere"), ())

    def test_a_corrupt_line_is_named_not_skipped(self) -> None:
        run_dir = self.tmp / "run3"
        journal = CRJ.RunProgressJournal(run_dir, run_id="y")
        journal.record("section_started", "started")
        with open(journal.path, "a", encoding="utf-8") as fh:
            fh.write("{not json\n")
        with self.assertRaisesRegex(CRJ.CitedRunJournalError, "第 2 行"):
            CRJ.read_run_progress(run_dir)


class SubjectCandidatesTests(unittest.TestCase):
    """主体候选只读自财务库；库里没有就返回空，**不猜**。"""

    def test_missing_financial_db_yields_no_candidates(self) -> None:
        with tempfile.TemporaryDirectory(prefix="m930_5_nodb_") as tmp:
            self.assertEqual(CRI.current_subjects(Path(tmp) / "absent.db"), ())

    def test_candidates_come_from_the_real_financial_db_when_present(self) -> None:
        if not FINANCIAL_DB.is_file():
            raise unittest.SkipTest("本机没有财务库；不伪造主体候选")
        subjects = CRI.current_subjects(FINANCIAL_DB)
        self.assertIn(COMPANY_SUBJECT, subjects)
        self.assertTrue(all(isinstance(s, str) and s for s in subjects))


def _summary(result: unittest.TestResult) -> dict:
    """运行器要的是 `passed`/`failed`/`skipped`/`details` 四个键，**不是**退出码或异常。"""
    return {
        "passed": result.testsRun - len(result.failures) - len(result.errors)
        - len(result.skipped),
        "failed": len(result.failures) + len(result.errors),
        "skipped": len(result.skipped),
        "details": [f"FAIL {test}\n{tb}" for test, tb in result.failures + result.errors],
    }


def main() -> dict:
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite()
    for case in (RunInputStagingTests, FinancialInputStagingTests, ChainRefusesForeignRunInput,
                 UploadScreensTests, CompletedRunIntegrityTests,
                 PendingRunPanelTests, RunIdEntryTests,
                 JournalDisciplineTests, SubjectCandidatesTests):
        suite.addTests(loader.loadTestsFromTestCase(case))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return _summary(result)


if __name__ == "__main__":
    raise SystemExit(0 if main()["failed"] == 0 else 1)

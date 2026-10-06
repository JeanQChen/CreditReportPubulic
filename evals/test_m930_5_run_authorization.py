"""`cra-2` 一次性运行授权：真实模式凭什么在**第一个模型请求之前**停下来。

这个模块钉的是一件很容易被悄悄做错的事：把「页面上写着 real」「用户点了开始生成」「预算还剩
额度」当成授权。它们都不是——三者全部由浏览器侧决定，任何脚本都能照抄。真正的授权只能是
盘上一条逐字段对得上的凭据，且只能用一次。

模块覆盖四层：

1. **凭据本身**：怎么造、怎么读、create-only、身份体封闭（未知键／缺键／版本不符都拒），
   以及**持久化读回时 `granted_by` 为空即拒**（构造口已经拒过一次，但那拦不住盘上那一份
   被改成空字符串之后再读回）；
2. **逐字段比对**：run-id／模式／主体／节集合／模型／prompt 版本／三份材料哈希／
   **三份财务来源哈希**／**快照身份与口径**／**profile 身份**／每类上限／整轮上限／重试次数，
   **每一项**各有一条反例，证明「差不多」不算数；
3. **一次性**：并发消费只有一个能成——`O_CREAT|O_EXCL` 的原子性，不是「先查后写」；
4. **链上真行为**：真实模式无授权时 `chain.run` 在建立结果目录**之前**停住，结果目录一个都
   没建、运行输入目录里留下可读的拒绝留痕。

它**不**发任何模型请求、不联网、不写 `data/` 下的库、不改任何历史 run。第 4 层跑的是真实模式，
但停在授权门上，因此一次请求也到不了（链在授权门**之前**还会先要 `--financial-input`，因此
这一层连同财务输入也一并核过，见 `_stage`）。离线模式不走这道门（离线不发请求），那一条由离线
纵链的现行运行覆盖，本模块不重跑它——重跑一次要 20 分钟，且证明不了授权门本身。
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from evals.test_m930_5_upload_run import (  # noqa: E402
    COMPANY_SUBJECT, _declared, _fin_uploads, _subject_name, _uploads)
from planning import demo_scope as SC_scope  # noqa: E402
import scripts.run_m930_3_cited_chain as CHAIN  # noqa: E402
from evaluation import run_m930_3_acceptance as ACC  # noqa: E402
from sections import cited_budget as CB  # noqa: E402
from sections import cited_financial_input as CFI  # noqa: E402
from sections import cited_run_authorization as CRA  # noqa: E402
from sections import cited_run_input as CRI  # noqa: E402

MODE_REAL = "real"
MODE_OFFLINE = "offline"
#: 双节真实运行必须走**含 `fin_balance_structure` 的 v2** profile：v1 只绑公司节。
PROFILE = CHAIN.BALANCE_STRUCTURE_PROFILE
FINANCIAL_DB = REPO / "data" / "financial_v2.db"
SECTIONS = ("company", "financial")
#: 只给**合成替身**（`_fin_binding`）用的假主体名：那几条用例比的是「两侧同一份替身」，
#: 不必读库。真跑链的那一组用 `_subject_name()` 读真实的唯一登记名。
SUBJECT_NAME = "演示主体（本用例给出，不读库）"


def _approved_model() -> str:
    """项目当前获批的写作模型。链上取的就是它，凭据也必须是它。"""
    from config import LLM_MODEL
    return str(LLM_MODEL or "").strip()


def _policy(model: str = "test-model-1"):
    return CB.cited_call_budget_policy(approved_model=model)


class _Binding:
    """`cri-1` 绑定的最小替身：`build_authorization` 只用到 `documents`。"""

    def __init__(self, documents):
        self.documents = tuple(documents)


def _binding(documents=(("DOC_A", "a" * 64), ("DOC_B", "b" * 64), ("DOC_C", "c" * 64))):
    return _Binding([SimpleNamespace(document_id=d, declared_sha256=s) for d, s in documents])


def _fin_binding(sources=(("sv-a", "1" * 64), ("sv-b", "2" * 64), ("sv-c", "3" * 64)),
                 snapshot_id="snap-test-1", as_of="2026-03-31", scope="consolidated",
                 currency="CNY", purpose="credit_analysis"):
    """`cfi-1` 绑定的最小替身：`build_authorization` 只用到 `sources` 与 `snapshot`。"""
    return SimpleNamespace(
        sources=tuple(SimpleNamespace(source_version=v, declared_sha256=h)
                      for v, h in sources),
        snapshot=SimpleNamespace(
            snapshot_id=snapshot_id, company_id=COMPANY_SUBJECT,
            company_name=SUBJECT_NAME, as_of_date=as_of, scope=scope,
            currency=currency, purpose=purpose, validity="valid",
            source_versions=tuple(sorted(sources))))


def _profile(profile_id="phase_a_business_finance_v2", profile_version="dsp-1",
             profile_fingerprint="f" * 64):
    """demo scope profile 的最小替身：`build_authorization` 只取这三个读数。"""
    return SimpleNamespace(profile_id=profile_id, profile_version=profile_version,
                           profile_fingerprint=profile_fingerprint)


def _grant(**overrides):
    """造一份「本可以生效」的凭据；每条反例只改一个字段。"""
    kwargs = dict(
        granted_by="本用例的批准人", run_id="m930_3_cited_upload_20261005T000000Z",
        mode=MODE_REAL, subject=COMPANY_SUBJECT, section_ids=SECTIONS,
        model="test-model-1", policy=_policy(), binding=_binding(),
        financial_binding=_fin_binding(), profile=_profile())
    kwargs.update(overrides)
    return CRA.build_authorization(**kwargs)


class _PatchedPolicy:
    """把预算政策的一个轴换掉，用来证明凭据比的是**活的那份政策**，不是它自己抄的数字。"""

    def __init__(self, policy, **overrides):
        self._policy = policy
        self._overrides = overrides

    @property
    def categories(self):
        return self._overrides.get("categories", self._policy.categories)

    @property
    def prompt_versions(self):
        return self._overrides.get("prompt_versions", self._policy.prompt_versions)

    @property
    def total_max_attempts(self):
        return self._overrides.get("total_max_attempts", self._policy.total_max_attempts)


def _live(**overrides):
    base = dict(run_id="m930_3_cited_upload_20261005T000000Z", mode=MODE_REAL,
                subject=COMPANY_SUBJECT, section_ids=SECTIONS, model="test-model-1",
                policy=_policy(), binding=_binding(),
                financial_binding=_fin_binding(), profile=_profile())
    base.update(overrides)
    return CRA.describe_live(**base)


class CredentialShapeTests(unittest.TestCase):
    """凭据怎么造、怎么读。"""

    def test_build_refuses_a_grant_without_a_grantor(self) -> None:
        with self.assertRaisesRegex(CRA.CitedAuthorizationError, "批准人"):
            _grant(granted_by="   ")

    def test_build_refuses_an_offline_grant(self) -> None:
        with self.assertRaisesRegex(CRA.CitedAuthorizationError, "离线"):
            _grant(mode=MODE_OFFLINE)

    def test_build_refuses_an_empty_model(self) -> None:
        with self.assertRaisesRegex(CRA.CitedAuthorizationError, "模型"):
            _grant(model="")

    def test_build_refuses_a_real_run_without_uploads(self) -> None:
        #: 授权绑定的正是三份上传对象的哈希；没有上传就没有可绑定的材料身份。
        with self.assertRaisesRegex(CRA.CitedAuthorizationError, "上传"):
            _grant(binding=None)

    def test_build_refuses_a_real_run_without_financial_uploads(self) -> None:
        #: 财务那一栏同样必填：只绑三份 PDF 的凭据证明不了「财务节读的是哪一期、什么口径」。
        with self.assertRaisesRegex(CRA.CitedAuthorizationError, "财务"):
            _grant(financial_binding=None)

    def test_build_refuses_a_real_run_without_a_profile(self) -> None:
        with self.assertRaisesRegex(CRA.CitedAuthorizationError, "profile"):
            _grant(profile=None)

    def test_round_trip_keeps_every_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            authorization = _grant()
            CRA.write_authorization(tmp, authorization)
            back = CRA.load_authorization(tmp, run_id=authorization.run_id)
            self.assertEqual(back.to_dict(), authorization.to_dict())

    def test_write_is_create_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            CRA.write_authorization(tmp, _grant())
            with self.assertRaisesRegex(CRA.CitedAuthorizationError, "拒绝覆盖"):
                CRA.write_authorization(tmp, _grant())

    def test_unknown_field_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            authorization = _grant()
            CRA.write_authorization(tmp, authorization)
            target = CRA.grant_path(tmp, authorization.run_id)
            payload = json.loads(target.read_text("utf-8"))
            payload["extra_permission"] = "放宽一点"
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CRA.CitedAuthorizationError, "未知字段"):
                CRA.load_authorization(tmp, run_id=authorization.run_id)

    def test_missing_field_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            authorization = _grant()
            CRA.write_authorization(tmp, authorization)
            target = CRA.grant_path(tmp, authorization.run_id)
            payload = json.loads(target.read_text("utf-8"))
            del payload["caps"]
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CRA.CitedAuthorizationError, "缺必填字段"):
                CRA.load_authorization(tmp, run_id=authorization.run_id)

    def test_other_version_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            authorization = _grant()
            CRA.write_authorization(tmp, authorization)
            target = CRA.grant_path(tmp, authorization.run_id)
            payload = json.loads(target.read_text("utf-8"))
            payload["authorization_version"] = "cra-0"
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CRA.CitedAuthorizationError, "版本"):
                CRA.load_authorization(tmp, run_id=authorization.run_id)

    def test_an_emptied_grantor_on_disk_is_refused(self) -> None:
        """构造口拒空批准人拦不住「盘上那一份被改成空字符串之后再读回」。"""
        with tempfile.TemporaryDirectory() as tmp:
            authorization = _grant()
            CRA.write_authorization(tmp, authorization)
            target = CRA.grant_path(tmp, authorization.run_id)
            payload = json.loads(target.read_text("utf-8"))
            payload["granted_by"] = "   "
            target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(CRA.CitedAuthorizationError, "granted_by"):
                CRA.load_authorization(tmp, run_id=authorization.run_id)

    def test_missing_grant_is_refused_and_points_at_the_prefix_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(CRA.CitedAuthorizationError, "第一个模型请求之前"):
                CRA.load_authorization(tmp, run_id="m930_3_cited_upload_20261005T000000Z")

    def test_documents_are_order_insensitive(self) -> None:
        reversed_docs = (("DOC_C", "c" * 64), ("DOC_B", "b" * 64), ("DOC_A", "a" * 64))
        CRA.check_authorization(
            _grant(binding=_binding(reversed_docs)),
            live=_live(binding=_binding()))


class FieldByFieldTests(unittest.TestCase):
    """每一项都不是装饰：改一处就该拒，且消息点名是那一处。"""

    def assertRefused(self, field: str, *, grant=None, live=None) -> None:  # noqa: N802
        authorization = _grant(**(grant or {}))
        with self.assertRaises(CRA.CitedAuthorizationError) as ctx:
            CRA.check_authorization(authorization, live=_live(**(live or {})))
        self.assertIn(field, str(ctx.exception))

    def test_matching_identity_passes(self) -> None:
        CRA.check_authorization(_grant(), live=_live())

    def test_another_run_id_is_refused(self) -> None:
        self.assertRefused("run_id", live={"run_id": "m930_3_cited_upload_20261005T010101Z"})

    def test_offline_live_is_refused(self) -> None:
        #: 凭据只发给真实运行（造的时候就会拒），因此这里改的是**运行时**的模式。
        self.assertRefused("模式", live={"mode": MODE_OFFLINE})

    def test_another_subject_is_refused(self) -> None:
        self.assertRefused("主体", live={"subject": "300751"})

    def test_a_narrower_section_set_is_refused(self) -> None:
        #: 获批的是双节；本次只跑一节也拒——凭据绑的是**这一次具体运行**的节集合，
        #: 「少跑一节」不是更保守，而是另一件事，要重新批准。
        self.assertRefused("节集合", live={"section_ids": ("company",)})

    def test_another_model_is_refused(self) -> None:
        self.assertRefused("模型", live={"model": "another-model"})

    def test_a_bumped_prompt_version_is_refused(self) -> None:
        bumped = dict(_policy().prompt_versions)
        bumped[sorted(bumped)[0]] = "cp-999"
        self.assertRefused("prompt 版本",
                           live={"policy": _PatchedPolicy(_policy(), prompt_versions=bumped)})

    def test_a_changed_material_hash_is_refused(self) -> None:
        self.assertRefused("材料身份", live={"binding": _binding(
            (("DOC_A", "a" * 64), ("DOC_B", "b" * 64), ("DOC_C", "d" * 64)))})

    def test_a_fourth_material_is_refused(self) -> None:
        self.assertRefused("材料身份", live={"binding": _binding(
            (("DOC_A", "a" * 64), ("DOC_B", "b" * 64), ("DOC_C", "c" * 64),
             ("DOC_D", "d" * 64)))})

    def test_a_changed_financial_source_hash_is_refused(self) -> None:
        self.assertRefused("财务材料身份", live={"financial_binding": _fin_binding(
            (("sv-a", "1" * 64), ("sv-b", "2" * 64), ("sv-c", "9" * 64)))})

    def test_a_change_of_snapshot_id_is_refused(self) -> None:
        self.assertRefused("快照身份与口径",
                           live={"financial_binding": _fin_binding(snapshot_id="snap-test-2")})

    def test_a_change_of_consolidation_caliber_is_refused(self) -> None:
        self.assertRefused("快照身份与口径",
                           live={"financial_binding": _fin_binding(scope="parent_only")})

    def test_a_change_of_report_period_is_refused(self) -> None:
        self.assertRefused("快照身份与口径",
                           live={"financial_binding": _fin_binding(as_of="2025-12-31")})

    def test_a_changed_profile_fingerprint_is_refused(self) -> None:
        self.assertRefused("profile 身份",
                           live={"profile": _profile(profile_fingerprint="e" * 64)})

    def test_a_changed_profile_version_is_refused(self) -> None:
        self.assertRefused("profile 身份", live={"profile": _profile(profile_version="dsp-2")})

    def test_a_widened_per_section_cap_is_refused(self) -> None:
        widened = [type(cap)(category=cap.category,
                             max_attempts_per_section=cap.max_attempts_per_section + 1,
                             max_attempts_total=cap.max_attempts_total, max_output_tokens=None,
                             approved=True, basis=cap.basis)
                   for cap in _policy().categories]
        self.assertRefused("每类上限", live={"policy": _PatchedPolicy(_policy(),
                                                                     categories=tuple(widened))})

    def test_a_widened_total_cap_is_refused(self) -> None:
        widened = [type(cap)(category=cap.category,
                             max_attempts_per_section=cap.max_attempts_per_section,
                             max_attempts_total=cap.max_attempts_total + 1, max_output_tokens=None,
                             approved=True, basis=cap.basis)
                   for cap in _policy().categories]
        self.assertRefused("每类上限", live={"policy": _PatchedPolicy(_policy(),
                                                                     categories=tuple(widened))})

    def test_a_changed_run_cap_is_refused(self) -> None:
        self.assertRefused("整轮上限", live={"policy": _PatchedPolicy(
            _policy(), total_max_attempts=_policy().total_max_attempts + 1)})


class OneShotTests(unittest.TestCase):
    """一次性：这才是这道门与「页面上按钮灰掉」的区别。"""

    def test_first_consume_wins_and_the_second_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            authorization = _grant()
            CRA.write_authorization(tmp, authorization)
            CRA.consume_authorization(tmp, authorization)
            self.assertTrue(CRA.authorization_state(tmp, run_id=authorization.run_id)["consumed"])
            with self.assertRaisesRegex(CRA.CitedAuthorizationError, "已被消费"):
                CRA.consume_authorization(tmp, authorization)

    def test_concurrent_consumption_admits_exactly_one(self) -> None:
        #: 「先查再写」在这里会放多个线程过去；`O_CREAT|O_EXCL` 不会。
        with tempfile.TemporaryDirectory() as tmp:
            authorization = _grant()
            CRA.write_authorization(tmp, authorization)
            outcomes: list[str] = []
            lock = threading.Lock()
            start = threading.Barrier(8)

            def attempt() -> None:
                start.wait()
                try:
                    CRA.consume_authorization(tmp, authorization)
                    result = "ok"
                except CRA.CitedAuthorizationError:
                    result = "refused"
                with lock:
                    outcomes.append(result)

            threads = [threading.Thread(target=attempt) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(outcomes.count("ok"), 1, outcomes)
            self.assertEqual(outcomes.count("refused"), 7, outcomes)

    def test_consumption_is_per_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first, second = _grant(), _grant(run_id="m930_3_cited_upload_20261005T020202Z")
            CRA.write_authorization(tmp, first)
            CRA.write_authorization(tmp, second)
            CRA.consume_authorization(tmp, first)
            CRA.consume_authorization(tmp, second)  # 另一份授权不受影响

    def test_the_record_names_the_consumed_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            authorization = _grant()
            CRA.consume_authorization(tmp, authorization)
            record = CRA.read_consumption(tmp, run_id=authorization.run_id) or {}
            self.assertEqual(record.get("authorization_id"), authorization.authorization_id)


class RefusalTraceTests(unittest.TestCase):
    """事前拒绝必须留下可读的东西，且不留半成品。"""

    def test_trace_is_written_next_to_the_uploads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = CRA.write_refusal_trace(run_input=tmp, run_id="run-x", stage="s",
                                             reason="没有授权")
            self.assertIsNotNone(target)
            payload = json.loads(Path(tmp, "authorization_refusal.json").read_text("utf-8"))
            self.assertEqual(payload["reason"], "没有授权")
            self.assertIn("第一个模型请求之前", payload["note"])

    def test_trace_without_a_run_input_dir_is_not_invented(self) -> None:
        #: 不为了「留痕」去建一个目录：那本身就是半个半成品。
        self.assertIsNone(CRA.write_refusal_trace(run_input=None, run_id="run-x",
                                                  stage="s", reason="没有授权"))


class ChainRefusesBeforeAnyRequest(unittest.TestCase):
    """链上真行为：真实模式没授权，就在建立结果目录之前停。"""

    @classmethod
    def setUpClass(cls) -> None:
        if not (REPO / "data" / "evidence.db").is_file():
            raise unittest.SkipTest("缺 data/evidence.db：本组用例要一份真实的登记才谈得上绑定")
        if not FINANCIAL_DB.is_file():
            raise unittest.SkipTest("缺 data/financial_v2.db：双节真实运行要一份权威快照")
        cls.declared = _declared()
        if len(cls.declared) != 3:
            raise unittest.SkipTest(f"当前登记不是三份（{len(cls.declared)}）：本组用例按三份写")
        cls.payload = _uploads()
        cls.subject_name = _subject_name()

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="m930_5_authz_"))
        previous_profile = ACC._DEMO_SCOPE_PROFILE_PATH
        self.addCleanup(setattr, ACC, "_DEMO_SCOPE_PROFILE_PATH", previous_profile)

    def _financial(self, run_id: str):
        """当前有效快照 + 它的三条来源声明（只读，不写库）。"""
        snapshot = CFI.current_snapshot_identity(
            FINANCIAL_DB, subject=COMPANY_SUBJECT, subject_name=self.subject_name)
        declared = CFI.declared_financial_sources(
            FINANCIAL_DB, version_ids=[v for v, _ in snapshot.source_versions])
        return snapshot, declared

    def _stage(self, run_id: str) -> Path:
        staged = self.tmp / "run_inputs" / run_id
        CRI.stage_run_input(self.payload, staged, declared=self.declared, run_id=run_id)
        snapshot, declared = self._financial(run_id)
        CFI.stage_financial_input(_fin_uploads(declared), staged, declared=declared,
                                  snapshot=snapshot, run_id=run_id)
        CRI.write_run_request(staged, run_id=run_id, mode=MODE_REAL,
                              section_ids=SECTIONS, subject=COMPANY_SUBJECT,
                              subject_name=self.subject_name, profile_path=PROFILE)
        return staged

    def _grant_parts(self, staged: Path) -> dict:
        """一份凭据要绑的三样：`cri-1` 绑定、`cfi-1` 绑定、profile 三元组。"""
        return dict(binding=CRI.load_run_input(staged, declared=self.declared),
                    financial_binding=CFI.load_financial_input(staged),
                    profile=SC_scope.load_demo_scope_profile(REPO / PROFILE))

    def _run(self, *, run_id: str, staged: Path):
        return CHAIN.run(results_root=self.tmp / "results", run_id=run_id,
                         profile_path=PROFILE, subject=COMPANY_SUBJECT,
                         subject_name=self.subject_name, section_ids=SECTIONS,
                         mode=MODE_REAL, run_input=staged, financial_input=staged,
                         authorization_root=self.tmp / "authorizations")

    def _assert_refused_without_a_run_dir(self, *, run_id: str, staged: Path,
                                          expect: str) -> None:
        with self.assertRaises(SystemExit) as ctx:
            self._run(run_id=run_id, staged=staged)
        self.assertIn(expect, str(ctx.exception))
        self.assertFalse((self.tmp / "results" / run_id).exists(),
                         "事前拒绝不得留下结果目录：那会是一个看似仍在运行的半成品")
        trace = staged / "authorization_refusal.json"
        self.assertTrue(trace.is_file(), "事前拒绝要留下可读的留痕")
        self.assertIn(expect, json.loads(trace.read_text("utf-8"))["reason"])

    def test_real_run_without_any_grant_stops_before_the_first_request(self) -> None:
        run_id = "m930_3_cited_upload_20261005T030303Z"
        self._assert_refused_without_a_run_dir(run_id=run_id, staged=self._stage(run_id),
                                               expect="一次性授权")

    def test_a_grant_for_another_run_is_refused(self) -> None:
        run_id = "m930_3_cited_upload_20261005T040404Z"
        staged = self._stage(run_id)
        other = "m930_3_cited_upload_20261005T050505Z"
        grant = CRA.write_authorization(
            self.tmp / "authorizations",
            CRA.build_authorization(granted_by="本用例的批准人", run_id=other,
                                    mode=MODE_REAL, subject=COMPANY_SUBJECT,
                                    section_ids=SECTIONS, model=_approved_model(),
                                    policy=_policy(_approved_model()),
                                    **self._grant_parts(staged)))
        #: 把它挪到**本 run** 的名字下：这样链找得到一份凭据，而凭据里记的是别的一次运行。
        grant.rename(CRA.grant_path(self.tmp / "authorizations", run_id))
        self._assert_refused_without_a_run_dir(run_id=run_id, staged=staged,
                                               expect="run_id")

    def test_a_hand_edited_material_hash_is_refused(self) -> None:
        run_id = "m930_3_cited_upload_20261005T060606Z"
        staged = self._stage(run_id)
        model = _approved_model()
        authorization = CRA.build_authorization(
            granted_by="本用例的批准人", run_id=run_id, mode=MODE_REAL,
            subject=COMPANY_SUBJECT, section_ids=SECTIONS, model=model,
            policy=_policy(model), **self._grant_parts(staged))
        CRA.write_authorization(self.tmp / "authorizations", authorization)
        #: 批准之后上传被换了一字节（这里直接改凭据里的哈希，等价于「批准的不是这一份材料」）。
        target = CRA.grant_path(self.tmp / "authorizations", run_id)
        payload = json.loads(target.read_text("utf-8"))
        payload["documents"][0][1] = "0" * 64
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self._assert_refused_without_a_run_dir(run_id=run_id, staged=staged,
                                               expect="材料身份")

    def test_the_same_grant_cannot_launch_twice(self) -> None:
        run_id = "m930_3_cited_upload_20261005T070707Z"
        staged = self._stage(run_id)
        model = _approved_model()
        authorization = CRA.build_authorization(
            granted_by="本用例的批准人", run_id=run_id, mode=MODE_REAL,
            subject=COMPANY_SUBJECT, section_ids=SECTIONS, model=model,
            policy=_policy(model), **self._grant_parts(staged))
        CRA.write_authorization(self.tmp / "authorizations", authorization)
        #: 第一次消费成功（这里不真跑链——真跑要 20 分钟；消费点由 `OneShotTests` 与
        #: 链上代码共同钉住），第二次到达同一份凭据时必须被拒。
        CRA.consume_authorization(self.tmp / "authorizations", authorization)
        with self.assertRaises(SystemExit) as ctx:
            self._run(run_id=run_id, staged=staged)
        self.assertIn("已被消费", str(ctx.exception))
        self.assertFalse((self.tmp / "results" / run_id).exists())


def _summary(result: unittest.TestResult) -> dict:
    return {"passed": result.testsRun - len(result.failures) - len(result.errors),
            "failed": len(result.failures) + len(result.errors),
            "skipped": len(result.skipped),
            "details": [str(case) for case, _ in result.failures + result.errors]}


def main() -> dict:
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return _summary(result)


if __name__ == "__main__":
    raise SystemExit(0 if main()["failed"] == 0 else 1)

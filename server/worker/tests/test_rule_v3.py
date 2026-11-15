"""`recommend_thresholds` rule_v3 경로 시험(CPU). 번들 코드는 시험용 export를 그대로 쓰고 생성만 가짜다.

가짜 생성은 Rule 입력의 코드 사실로 결정을 만든다(모델 품질 시험이 아님). 실제 GPU 적재·xgrammar·생성 시간은
GPU 확인 스크립트(`rule_v3_check.py`) 몫이다.
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import fcntl  # noqa: F401
except ImportError:  # Windows CPU 시험만. 번들 호출 예산 잠금(Budget)에만 쓰인다. 운영은 Linux
    sys.modules["fcntl"] = types.SimpleNamespace(LOCK_EX=2, flock=lambda *a, **k: None)

import generation_client
import model_adapters
import research_bundle
import rule_v3_capture as capture
import rule_v3_check
import rule_v3_runtime as v3
import threshold_recommendation as recommendation
from generation_client import GenerationUnavailable
from generation_service import GenerationGate
from methods.registry import SOFT_RULE_FIELDS_BY_ID
from pg_queue import ClaimedMessage
from threshold_values import fixed_threshold
from trainer import runner
from test_recommend_thresholds import Connection, frontend_valid, job_spec, rule  # noqa: E402

REPOSITORY = Path(__file__).resolve().parents[3]
LLM_SPEC_V3 = {"evidence_version": "soft-rule-threshold-v2", "input_evidence_hash": "h", "model_version": "rule_v3",
               "prompt_version": "p", "output_schema_version": "threshold-recommendation-v1", "reuse_key": "r"}
SNAPSHOT = uuid.UUID("22222222-2222-4222-8222-222222222222")
FIELDS = ("elapsed_ms", "error_count", "error_ratio", "retry_count", "success_count", "total_count")
_ROOT: Path | None = None
_EXPORT: Path | None = None


def setUpModule():
    global _ROOT, _EXPORT
    _ROOT = Path(tempfile.mkdtemp(prefix="rv3-test-"))
    _EXPORT = _ROOT / "export"
    research_bundle.build_rule_v3(REPOSITORY, _EXPORT)


def tearDownModule():
    shutil.rmtree(_ROOT, ignore_errors=True)


def package():
    return v3.load(_EXPORT)


# ---- 가짜 생성 ----

def decide(inp):
    b, m = inp["baseline"], inp["measurement"]
    upper = inp["range_proof"].get("max")
    if b["state"] != "verified":
        return "request_evidence", {"unit_unconfirmed": ["need_verified_unit"],
                                    "insufficient": ["need_reference_minimum_n"]}[b["state"]]
    if upper is not None and upper <= b["value"]:
        return "no_applicable", []
    if m["valid_count"] == 0:
        return "deferred", ["need_valid_observations"]
    return "proposed", []


def oracle(inp, state, needed, reason):
    b = inp["baseline"]
    executable = state in ("proposed", "deferred")
    return {"v": "3", "feature": inp["feature"]["id"], "sha": inp["feature"]["sha"], "decisions": [{
        "request": "baseline", "role": "SR", "state": state,
        "basis": "reference_p95" if executable else "insufficient" if state == "request_evidence" else "none",
        "condition": {"op": b.get("comparison", "gt"), "value": b.get("value")} if executable else None,
        "evidence": [inp["measurement_id"]] + ([b["id"]] if b.get("id") else []),
        "needed": list(needed), "reason": reason,
        "purpose": "other", "warning_meaning": "Offline comparison check only.",
        "population_ref": inp["application_context"]["population_ref"],
        "precondition_refs": list(inp["application_context"]["fact_ids"]),
        "required_evaluation_basis": [inp["application_context"]["population_ref"]],
        "unavailable_action": "preserve_unassessed" if executable else "no_rule",
        "unavailable_reason": "Rows without a numeric value stay unassessed.",
        "business_scope": "No business, incident or online meaning is confirmed.",
        "baseline_use_ref": b.get("id") if executable else None}]}


class FakeLocalModels:
    """번들 LocalModels와 같은 이름·출력 키. `script[call]`로 시도별 응답을 바꾼다."""

    def __init__(self, base, rule_adapter=None, candidate_feature=None):
        self.base = base
        self.script: dict[str, str] = {}
        self.calls: list[str] = []
        self.messages: list = []  # 시도마다 받은 프롬프트(프롬프트 불변 시험)
        self.max_time_seen: list = []
        self.on_call = None
        self.adapter_sha = json.loads((_EXPORT / "model_manifest.json").read_text(encoding="utf-8"))["Rule"]["weight_sha256"]

    def generate(self, kind, messages, schema, dest, budget, call, maximum=1536, arm="Base"):
        from specmodel.io import digest, write
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        receipt = budget.reserve(kind, call)
        self.calls.append(call)
        self.messages.append(messages)
        self.max_time_seen.append(self._config().max_time)
        if self.on_call:
            self.on_call(call)
        payload = json.loads(messages[1]["content"])
        inp = payload.get("verified_input", payload)
        mode = self.script.get(call, "ok")
        state, needed = decide(inp)
        reason = {"proposed": f"참조 기간 유효 값 {inp['baseline']['reference_valid_n']}개의 p95보다 큰 현재 값을 경고합니다.",
                  "deferred": "현재 기간에 숫자 값이 없어 비교를 보류합니다.",
                  "no_applicable": "증명된 상한 때문에 조건이 성립할 수 없습니다.",
                  "request_evidence": "필요한 근거가 없어 근거를 요청합니다."}[state]
        if mode == "english":
            reason = "Current values above the reference p95 are flagged for review."
        if mode == "bad_number":
            reason = "참조 기간 유효 값 777개의 p95보다 큰 현재 값을 경고합니다."
        decision = oracle(inp, state, needed, reason)
        if mode == "bad_value":
            decision["decisions"][0]["condition"]["value"] = 123456.5
        raw = "{\"cut" if mode in ("json", "slow") else json.dumps(decision, ensure_ascii=False)
        parsed = None if mode in ("json", "slow") else json.loads(raw)
        seconds = 241.0 if mode == "slow" else 0.01
        out = {"kind": kind, "raw": raw, "parsed": parsed, "parse_error": "cut" if parsed is None else None,
               "length_limit_hit": False, "messages_sha": digest(messages), "tokens": {"input_tokens": 1000},
               "output_tokens": 200, "budget": receipt, "seconds": seconds,
               "model": {"adapter_weight_sha": self.adapter_sha}, "GPU_memory": {"peak": 0}}
        write(dest / "input.json", {"messages": messages, "schema": schema, "budget": receipt})
        write(dest / "output.json", out)
        return out


def runtime(gate=None):
    pkg = package()
    cls = v3.shared_model_class(FakeLocalModels)
    model = SimpleNamespace(generation_config=SimpleNamespace(max_time=None))
    models = cls(_ROOT, model, None, None, gate or GenerationGate(), {"fake": True})
    return v3.RuleV3Runtime(pkg, models)


# ---- 합성 snapshot 행 ----

def observation(index, *, status="S", label=None, start=datetime(2026, 10, 10, 9, 0), end=True, **counts):
    begin = start + timedelta(minutes=index)
    raw = {"status": status, "transaction_id": f"tx{index:04d}", "start_time_raw": begin.isoformat(),
           "end_time_raw": (begin + timedelta(milliseconds=100 + index * 7)).isoformat() if end else None,
           "total_count": str(1000 + index * 3), "success_count": str(990 + index * 2),
           "error_count": str(index % 4), "retry_count": "0", **counts}
    return (uuid.uuid4(), raw, "train", {"label": label} if label else None)


def rows(count=60, **options):
    return [observation(index, **options) for index in range(count)]


def plan(*fields):
    spec = job_spec(*[rule(field) for field in (fields or FIELDS)])
    return recommendation.parse_job(spec, LLM_SPEC_V3)


class Base(unittest.TestCase):
    def setUp(self):
        self.runtime = runtime()
        self.models = self.runtime.models
        generation_client.set_local_model(SimpleNamespace(rule_v3=self.runtime))
        self.addCleanup(generation_client.set_local_model, None)

    def recommend(self, data=None, *fields, **options):
        return recommendation.recommend(plan(*fields), data or rows(), SNAPSHOT, "persisted_at",
                                        source_tz=None, **options)

    def item(self, result, field):
        return next(item for item in result["items"] if item["field"] == field)


class ExportTests(unittest.TestCase):
    def test_export_is_verified_and_detects_changes(self):
        self.assertEqual(research_bundle.verify(_EXPORT), _EXPORT.resolve())
        names = {p.relative_to(_EXPORT).as_posix() for p in _EXPORT.rglob("*") if p.is_file()}
        self.assertIn("models/rule/adapter_model.safetensors", names)
        self.assertFalse(any(name.startswith(("examples/", "tests/", "models/feature/")) for name in names))
        target = _EXPORT / "contracts/rule_system.txt"
        original = target.read_bytes()
        try:
            target.write_bytes(original + b" ")
            with self.assertRaisesRegex(ValueError, "research_bundle_hash_mismatch"):
                research_bundle.verify(_EXPORT)
        finally:
            target.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "bundle_destination_exists"):
            research_bundle.build_rule_v3(REPOSITORY, _EXPORT)

    def test_cli_flag_selects_rule_v3_export(self):
        with patch.object(research_bundle, "build_rule_v3") as build, contextlib.redirect_stdout(io.StringIO()):
            research_bundle.main(["--repository", str(REPOSITORY), "--output", "/bundles/v3", "--rule-v3"])
        build.assert_called_once_with(REPOSITORY, Path("/bundles/v3"))

    def test_korean_line_is_added_once(self):
        pkg = package()
        v3.load(_EXPORT)
        text = pkg.rule_logic.rule_system()
        self.assertEqual(text.count(v3.KO_LINE), 1)


class CaptureTests(unittest.TestCase):
    def test_sample_split_and_bundle_admission(self):
        data = rows(56) + [observation(56, status="F"), observation(57, label="anomaly")]
        split = capture.split_sample(data, None)
        self.assertEqual(split["counts"]["sampleRows"], 56)
        self.assertEqual(len(split["reference"]) + len(split["current"]), 56)
        self.assertLessEqual(split["reference_period"]["end_exclusive"], split["current_period"]["start_inclusive"])
        self.assertFalse(capture.held_window_overlap(split))
        pkg = package()
        with tempfile.TemporaryDirectory() as tmp:
            for field in FIELDS:
                paths = capture.write_case(Path(tmp) / field, field, split, {"dataset_id": f"t_{field}"})
                dataset = pkg.dataset.Dataset(paths["application"])
                proposal = json.loads(paths["proposal"].read_text(encoding="utf-8"))
                compiled = pkg.calculations.compile_feature(dataset, proposal, "test")
                self.assertTrue(compiled["passed"], field)
                values = [capture.field_value(field, row) for row in split["current"]]
                self.assertEqual(compiled["compiled_feature"]["measurement"]["summary"]["valid_count"],
                                 sum(v is not None for v in values), field)

    def test_held_window_rows_are_excluded_by_kit_rule(self):
        before = [observation(i, start=datetime(2026, 9, 26, 9, 0)) for i in range(0, 30)]
        held = [observation(i, start=datetime(2026, 10, 1, 9, 0)) for i in range(30, 40)]
        after = [observation(i, start=datetime(2026, 10, 10, 9, 0)) for i in range(40, 80)]
        self.assertTrue(capture.held_window_overlap(capture.split_sample(before + held + after, None)))
        split, exclusion = capture.split_avoiding_held(before + held + after, None)
        self.assertEqual(exclusion, {"from": "2026-09-30T00:00:00+09:00", "until": "2026-10-03T00:00:00+00:00",
                                     "excludedRows": 40})  # 보류 창 10행 + 앞쪽 30행(뒤쪽만 남김)
        self.assertEqual(len(split["reference"]) + len(split["current"]), 40)
        self.assertFalse(capture.held_window_overlap(split))
        self.assertGreaterEqual(split["reference_period"]["start_inclusive"], "2026-10-03")
        # 뒤쪽이 없으면 앞쪽만 남긴다
        split, exclusion = capture.split_avoiding_held(before + held, None)
        self.assertEqual((exclusion["excludedRows"], len(split["reference"]) + len(split["current"])), (10, 30))
        self.assertLessEqual(split["current_period"]["end_exclusive"], capture.HELD_LOW)
        # 경계를 가로지르는 거래는 통째로 빠진다(행 시각은 END 먼저라 뒤쪽 창에도 걸리지 않게)
        crossing = (uuid.uuid4(), {**after[0][1], "transaction_id": "cross",
                                   "start_time_raw": "2026-10-03T08:59:00", "end_time_raw": "2026-10-03T09:01:00"},
                    "train", None)
        split, exclusion = capture.split_avoiding_held(after + [crossing], None)
        self.assertEqual(exclusion["excludedRows"], 1)
        with self.assertRaisesRegex(ValueError, "held_window_avoid_failed"):
            capture.split_avoiding_held(held, None)
        self.assertIsNone(capture.split_avoiding_held(after, None)[1])

    def test_source_timezone_rule(self):
        self.assertIsNone(capture.source_timezone(None))
        self.assertIsNone(capture.source_timezone({"status": "assumed", "timezone": "UTC"}))
        self.assertEqual(capture.source_timezone({"status": "verified", "timezone": "UTC"}), "UTC")
        self.assertEqual(capture.source_timezone({"status": "verified"}), "")


class RuntimeTests(unittest.TestCase):
    def test_direct_call_matches_cli_rule_input(self):
        pkg = package()
        cli = importlib.import_module("specmodel.cli")
        split = capture.split_sample(rows(), None)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            paths = capture.write_case(tmp / "case", "elapsed_ms", split, {"dataset_id": "t"})
            direct = runtime().run_field(paths, tmp / "direct")["result"]
            fake = FakeLocalModels(tmp)
            fake._config = lambda: SimpleNamespace(max_time=None)
            with patch.object(cli, "LocalModels", lambda *a, **k: fake), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["validate-feature", "--input-manifest", str(paths["application"]),
                                           "--request", str(paths["request"]), "--proposal", str(paths["proposal"]),
                                           "--output", str(tmp / "feature")]), 0)
                self.assertEqual(cli.main(["rule", "--input-manifest", str(paths["application"]),
                                           "--reference-manifest", str(paths["reference"]),
                                           "--feature-bundle", str(tmp / "feature/feature_bundle.json"),
                                           "--rule-plan", str(paths["rule_plan"]), "--base-path", str(tmp),
                                           "--output", str(tmp / "cli"), "--max-model-calls", "2"]), 0)
            saved = json.loads((tmp / "cli/rule_input.json").read_text(encoding="utf-8"))
            self.assertEqual(pkg.io.digest(saved["input"]), pkg.io.digest(direct["input"]))
            self.assertEqual(saved["messages"], json.loads((tmp / "direct/rule_input.json").read_text(encoding="utf-8"))["messages"])
            self.assertIn(v3.KO_LINE, saved["messages"][0]["content"])

    def test_classify(self):
        self.assertEqual(v3.classify([{"code": "JSON_or_length"}])[0], "invalid_generation_output")
        self.assertEqual(v3.classify([{"code": "baseline_grounding"}, {"code": "quoted_count_scope"}]),
                         ("expected_target_mismatch", {"schema": True, "facts": False, "expectedTarget": False}))
        self.assertEqual(v3.classify([{"code": "condition_operator_prose_conflict"}])[0], "factual_review_failed")

    def test_rationale_checks(self):
        facts = {"baseline": {"reference_valid_n": 26130, "value": 7210.5}, "measurement": {"p95": 1.0}}
        self.assertEqual(v3.rationale_checks("참조 26,130개의 p95 7210.5ms를 넘으면 경고합니다.", facts),
                         {"korean": True, "numbers": True})
        self.assertFalse(v3.rationale_checks("참조 26131개 기준입니다.", facts)["numbers"])
        self.assertTrue(v3.rationale_checks("95번째 백분위 기준입니다.", facts)["numbers"], "p95 key gives 95")
        self.assertFalse(v3.rationale_checks("Values above p95 are flagged.", facts)["korean"])
        self.assertEqual(v3.text_numbers("p95 r000001 A1 20개 0.5."), [Decimal(20), Decimal("0.5")])

    def test_id_sha_period_numbers_are_not_facts(self):
        facts = {"measurement_id": "m_4471aa00bb11cc22", "definition_sha": "4471aa00bb11cc22dd33",
                 "baseline": {"id": "baseline_9031ab12cd34ef56", "reference_valid_n": 40, "minimum_n": 20},
                 "scope": {"period": {"start_inclusive": "2026-10-10T00:00:00.000000+00:00"}},
                 "evaluation_preconditions": ["source_7781aa00bb11cc22"],
                 "table_rows": {"PROCESS": 50}}
        known = v3.fact_numbers(facts)
        self.assertEqual(known, {Decimal(40), Decimal(20), Decimal(50)})
        for text in ("4471개 기준입니다.", "2026년 10월 기간입니다.", "7781개 행입니다."):
            self.assertFalse(v3.rationale_checks(text, facts)["numbers"], text)
        self.assertTrue(v3.rationale_checks("참조 유효 값 40개, 현재 50행입니다.", facts)["numbers"])

    def test_percent_is_rejected(self):
        facts = {"baseline": {"value": 20, "reference_valid_n": 40}}
        for text in ("값의 20%를 넘으면 경고합니다.", "20퍼센트 기준입니다."):
            self.assertFalse(v3.rationale_checks(text, facts)["numbers"], text)


class AttachTests(unittest.TestCase):
    def service(self, base):
        return SimpleNamespace(manifest={"base": base}, model=None, tokenizer=None, gate=GenerationGate())

    def test_base_mismatch_or_missing_bundle_disables_only_v3(self):
        from generation_service import DesignModel
        files = json.loads((_EXPORT / "models/base_files.json").read_text(encoding="utf-8"))
        for bundle, base, code in (
                (_EXPORT, {"revision": "other", "metadata_sha256": {"config.json": "0"}}, "rule_v3_base_mismatch"),
                (_ROOT / "absent", {"revision": files["revision"]}, "FileNotFoundError")):
            service = self.service(base)
            with self.assertLogs("generation_service", "ERROR") as logged:
                DesignModel.attach_rule_v3(service, bundle, _ROOT)
            self.assertIsNone(service.rule_v3)
            self.assertEqual(service.rule_v3_error, code)
            self.assertEqual(logged.output, [f"ERROR:generation_service:rule_v3_disabled: {code}"])
        service = self.service({})
        DesignModel.attach_rule_v3(service, None, _ROOT)
        self.assertEqual((service.rule_v3, service.rule_v3_error), (None, None))

    def test_v3_base_list_matches_the_service_bundle(self):
        files = json.loads((_EXPORT / "models/base_files.json").read_text(encoding="utf-8"))
        service = json.loads((REPOSITORY / "research/models/feature_rule_design_v0_2/model_manifest.json")
                             .read_text(encoding="utf-8"))["base"]
        v3.check_base(files, service)
        with self.assertRaisesRegex(ValueError, "rule_v3_base_mismatch"):
            v3.check_base(files, {**service, "metadata_sha256": {**service["metadata_sha256"], "config.json": "0" * 64}})


class RecommendationV3Tests(Base):
    def test_result_document_and_progress(self):
        calls = []
        result = self.recommend(progress=lambda done, total: calls.append((done, total)))
        self.assertEqual(calls, [(n, 6) for n in range(7)])
        self.assertEqual((result["recommendationContractVersion"], result["provider"]),
                         ("threshold-recommendation-v1", "rule_v3"))
        self.assertEqual(result["adapterSha"], self.runtime.adapter_sha)
        self.assertEqual(result["data"]["referenceRatio"], 0.8)
        self.assertIsNone(result["heldWindowExclusion"])
        self.assertEqual({item["basis"] for item in result["items"]}, {"train_reference_80_p95"})
        self.assertEqual(result["data"]["referenceRows"] + result["data"]["currentRows"], 60)
        self.assertTrue(all(frontend_valid(item) for item in result["items"]))
        retry = self.item(result, "retry_count")
        self.assertEqual((retry["status"], retry["attempts"]), ("no_applicable", 0))
        self.assertIn("모두 0", retry["rationale"])
        split = capture.split_sample(rows(), None)
        for field in ("elapsed_ms", "error_count", "error_ratio", "success_count", "total_count"):
            item = self.item(result, field)
            p95 = capture.p95([capture.field_value(field, row) for row in split["reference"]])
            meta = SOFT_RULE_FIELDS_BY_ID[field]
            self.assertEqual((item["status"], item["operator"], item["attempts"]), ("proposed", "gt", 1), field)
            self.assertEqual(item["threshold"], fixed_threshold(p95, meta.value_type, meta.display_precision,
                                                                meta.display_precision_mode))
            self.assertEqual(item["evidence"]["n"], len(split["reference"]))
            self.assertTrue(item["evidence"]["measurementId"].startswith("m_"))
            self.assertGreaterEqual(v3.hangul_ratio(item["rationale"]), Decimal("0.5"))
        self.assertEqual(result["counts"], {"targets": 6, "proposed": 5, "requestEvidence": 0, "deferred": 0,
                                            "noApplicable": 1, "rejected": 0})
        self.assertEqual(self.models.max_time_seen, [240.0] * 5)
        self.assertIsNone(self.models.model.generation_config.max_time, "max_time is restored")

    def test_repair_then_pass(self):
        self.models.script["rule_0"] = "bad_value"
        item = self.item(self.recommend(None, "elapsed_ms"), "elapsed_ms")
        self.assertEqual((item["status"], item["attempts"]), ("proposed", 2))

    def test_failed_repair_is_classified(self):
        for mode, reason in (("bad_value", "expected_target_mismatch"), ("json", "invalid_generation_output")):
            with self.subTest(mode):
                self.models.script = {"rule_0": mode, "rule_1": mode}
                item = self.item(self.recommend(None, "elapsed_ms"), "elapsed_ms")
                self.assertEqual((item["status"], item["rejectedReason"], item["attempts"]), ("rejected", reason, 2))
                self.assertIsNone(item["rationale"])
                self.assertTrue(frontend_valid(item))

    def test_server_rationale_checks_reject(self):
        for mode in ("english", "bad_number"):
            with self.subTest(mode):
                self.models.script = {"rule_0": mode}
                item = self.item(self.recommend(None, "elapsed_ms"), "elapsed_ms")
                self.assertEqual((item["status"], item["rejectedReason"]), ("rejected", "factual_review_failed"))
                self.assertEqual(item["reviews"], {"schema": True, "facts": False, "expectedTarget": True})

    def test_current_side_without_values_is_no_applicable_before_the_bundle(self):
        # 10차: 현재 쪽 유효 값 0개는 번들·LLM 호출 전에 코드가 no_applicable로 끝낸다(이전: 번들 결정 deferred).
        data = rows(48) + [observation(index, end=False) for index in range(48, 60)]
        with patch.object(capture, "write_case", side_effect=AssertionError("bundle input written")):
            item = self.item(self.recommend(data, "elapsed_ms"), "elapsed_ms")
        self.assertEqual((item["status"], item["operator"], item["threshold"], item["attempts"]),
                         ("no_applicable", None, None, 0))
        self.assertEqual(item["rationale"], "현재 기간에 유효 값이 없어 비교할 수 없습니다.")
        self.assertEqual((item["evidence"]["currentN"], item["evidence"]["n"]), (0, 48))
        self.assertEqual(self.models.calls, [])
        self.assertTrue(frontend_valid(item))

    def test_timeout_fails_the_job_without_repair(self):
        self.models.script["rule_0"] = "slow"
        with self.assertRaisesRegex(GenerationUnavailable, "generation_timeout"):
            self.recommend(None, "elapsed_ms")
        self.assertEqual(self.models.calls, ["rule_0"])

    def test_held_window_exclusion_is_reported(self):
        held = [observation(i, start=datetime(2026, 10, 1, 9, 0)) for i in range(100, 110)]
        result = self.recommend(rows() + held, "elapsed_ms")
        self.assertEqual(result["heldWindowExclusion"]["excludedRows"], 10)
        self.assertEqual(result["data"]["referenceRows"] + result["data"]["currentRows"], 60)
        self.assertEqual(self.item(result, "elapsed_ms")["status"], "proposed")

    def test_too_few_rows_after_exclusion(self):
        # 뺀 뒤 한 필드 표본이 20 미만이면 그 필드만 no_applicable(LLM 호출 없음)
        held = [observation(i, start=datetime(2026, 10, 1, 9, 0)) for i in range(100, 160)]
        item = self.item(self.recommend(rows(20) + held, "elapsed_ms"), "elapsed_ms")
        self.assertEqual((item["status"], item["evidence"]["n"], item["attempts"]), ("no_applicable", 16, 0))
        # 전체가 모자라면(전부 보류 창 또는 분할 불가) 작업 실패
        for data in (held, rows(1)):
            with self.assertRaisesRegex(ValueError, "recommendation_input_empty"):
                self.recommend(data)
        self.assertEqual(self.models.calls, [])

    def test_missing_runtime_is_not_configured(self):
        generation_client.set_local_model(SimpleNamespace())
        with self.assertRaisesRegex(model_adapters.ModelNotConfigured, "rule_v3_not_configured"):
            model_adapters.recommend_thresholds(plan("elapsed_ms"), rows(), SNAPSHOT, "persisted_at")

    def test_llm_spec_pairs(self):
        self.assertEqual(plan("elapsed_ms").provider, "rule_v3")
        for changes in ({"evidence_version": "soft-rule-threshold-v1"}, {"model_version": "rule_v4"}):
            with self.assertRaisesRegex(ValueError, "recommendation_llm_spec_invalid"):
                recommendation.parse_job(job_spec(rule("elapsed_ms")), {**LLM_SPEC_V3, **changes})

    def test_explanation_enters_between_attempts(self):
        gate = GenerationGate()
        self.runtime = runtime(gate)
        self.models = self.runtime.models
        generation_client.set_local_model(SimpleNamespace(rule_v3=self.runtime))
        order = []

        def explain():
            with gate.slot(True, 5):
                order.append("explain")

        def on_call(call):
            order.append(call)
            self.assertTrue(gate.snapshot()["running"])
            if call == "rule_0":
                threading.Thread(target=explain, daemon=True).start()
                deadline = time.monotonic() + 5
                while gate.snapshot()["interactive"] == 0 and time.monotonic() < deadline:
                    time.sleep(0.01)
        self.models.on_call = on_call
        self.models.script["rule_0"] = "bad_value"
        self.recommend(None, "elapsed_ms")
        self.assertEqual(order, ["rule_0", "explain", "rule_1"])


# ---- 10차: 범위(scope)별 p95·후보 대상 ----

LLM_SPEC_V3_SCOPE = {**LLM_SPEC_V3, "evidence_version": "soft-rule-threshold-v3"}
SCOPE_A = {"processId": "IF_A_PR002", "adapterType": "HTTP"}
SCOPE_B = {"processId": "IF_B_PR001"}
JOB = "33333333-3333-4333-8333-333333333333"


def soft(field, scope=None, suffix="00000000", threshold=5):
    return {"kind": "soft_rule", "id": f"soft:{field}-{suffix}", "field": field, "scope": scope, "threshold": threshold}


def candidate(field, scope, number):
    return {"kind": "candidate", "id": f"{JOB}.t{number}", "field": field, "scope": scope, "threshold": None}


def scoped_plan(*targets, **scope):
    ids = [target["id"] for target in targets if target["kind"] == "candidate"]
    spec = job_spec(*targets, scope={"softRuleIds": None, "candidateIds": ids, **scope})
    return recommendation.parse_job(spec, LLM_SPEC_V3_SCOPE)


def mixed_rows(count=120):
    """짝수 행 = SCOPE_A(IF_A_PR002/HTTP), 홀수 행 = SCOPE_B(IF_B_PR001/MQ). 처리 시간은 행 번호로 늘어난다."""
    return [observation(index, process_id="IF_A_PR002" if index % 2 == 0 else "IF_B_PR001",
                        adapter_type="HTTP" if index % 2 == 0 else "MQ", process_hub_id="HUB")
            for index in range(count)]


class ScopeV3Tests(Base):
    def run_plan(self, plan, data, **options):
        return recommendation.recommend(plan, data, SNAPSHOT, "persisted_at", source_tz=None, **options)

    def test_same_field_scopes_get_their_own_reference_p95(self):
        data = mixed_rows()
        plan = scoped_plan(soft("elapsed_ms", None, "aaaaaaaa", 5000), soft("elapsed_ms", SCOPE_A, "bbbbbbbb", 7000),
                           candidate("elapsed_ms", SCOPE_A, 2), candidate("elapsed_ms", SCOPE_B, 5))
        calls = []
        result = self.run_plan(plan, data, progress=lambda done, total: calls.append((done, total)))
        self.assertEqual(calls, [(n, 3) for n in range(4)])
        self.assertEqual([(item["field"], item["scope"]) for item in result["items"]],
                         [("elapsed_ms", None), ("elapsed_ms", SCOPE_A), ("elapsed_ms", SCOPE_B)])
        self.assertEqual([(item["softRuleIds"], item["candidateIds"], item["currentThreshold"]) for item in result["items"]],
                         [(["soft:elapsed_ms-aaaaaaaa"], [], 5000),
                          (["soft:elapsed_ms-bbbbbbbb"], [f"{JOB}.t2"], 7000),
                          ([], [f"{JOB}.t5"], None)])
        split = capture.split_sample(data, None)
        meta = SOFT_RULE_FIELDS_BY_ID["elapsed_ms"]
        thresholds = set()
        for item, process in zip(result["items"], (None, "IF_A_PR002", "IF_B_PR001")):
            reference = [row for row in split["reference"] if process is None or row["process_id"] == process]
            current = [row for row in split["current"] if process is None or row["process_id"] == process]
            p95 = capture.p95([capture.field_value("elapsed_ms", row) for row in reference])
            self.assertEqual((item["status"], item["attempts"], item["basis"]), ("proposed", 1, "train_reference_80_p95"))
            self.assertEqual((item["evidence"]["n"], item["evidence"]["currentN"]), (len(reference), len(current)))
            self.assertEqual(item["threshold"], fixed_threshold(p95, meta.value_type, meta.display_precision,
                                                                meta.display_precision_mode))
            self.assertTrue(frontend_valid(item))
            thresholds.add(item["threshold"])
        self.assertEqual(len(thresholds), 3, "each scope has its own p95")
        self.assertEqual(result["data"]["referenceRows"] + result["data"]["currentRows"], 120, "split once for the job")
        self.assertEqual(result["counts"]["targets"], 3)

    def test_small_or_reference_only_scope_is_no_applicable_without_the_bundle(self):
        late = [observation(index, process_id="IF_A_PR002", adapter_type="HTTP") for index in range(100)]
        few = [observation(index, process_id="IF_C", transaction_id=f"c{index}", start=datetime(2026, 10, 10, 9, 0, 30))
               for index in range(0, 100, 10)]
        # 앞 80%(참조)에만 있는 범위: 처음 60분에만 행이 있다.
        early = [observation(index, process_id="IF_D", transaction_id=f"d{index}", start=datetime(2026, 10, 10, 9, 0, 40))
                 for index in range(60)]
        plan = scoped_plan(soft("elapsed_ms", {"processId": "IF_C"}, "cccccccc"),
                           soft("elapsed_ms", {"processId": "IF_D"}, "dddddddd"),
                           candidate("elapsed_ms", SCOPE_A, 1))
        written = []
        original = capture.write_case
        with patch.object(capture, "write_case", side_effect=lambda *a, **k: written.append(a[3]) or original(*a, **k)):
            result = self.run_plan(plan, late + few + early)
        items = {item["scope"]["processId"]: item for item in result["items"]}
        self.assertEqual((items["IF_C"]["status"], items["IF_C"]["attempts"]), ("no_applicable", 0))
        self.assertLess(items["IF_C"]["evidence"]["n"], 20)
        self.assertIn("20개에 못 미칩니다", items["IF_C"]["rationale"])
        reference_only = items["IF_D"]
        self.assertEqual((reference_only["status"], reference_only["attempts"], reference_only["evidence"]["currentN"]),
                         ("no_applicable", 0, 0))
        self.assertGreaterEqual(reference_only["evidence"]["n"], 20)
        self.assertEqual(reference_only["rationale"], "현재 기간에 이 범위의 유효 값이 없어 비교할 수 없습니다.")
        self.assertEqual(items["IF_A_PR002"]["status"], "proposed")
        self.assertEqual(len(written), 1, "only the scope with both sides reaches the bundle")
        self.assertEqual(len(self.models.calls), 1)
        self.assertFalse(recommendation.all_rejected(result))

    def test_work_folders_and_dataset_ids_are_per_group_without_scope_values(self):
        plan = scoped_plan(soft("elapsed_ms"), soft("elapsed_ms", SCOPE_A, "aaaaaaaa"),
                           soft("elapsed_ms", SCOPE_B, "bbbbbbbb"), soft("total_count", SCOPE_A, "cccccccc"))
        written = []
        original = capture.write_case
        with patch.object(capture, "write_case",
                          side_effect=lambda case, field, split, identity: written.append(
                              (case.parent.name + "/" + case.name, identity["dataset_id"])) or
                          original(case, field, split, identity)):
            self.run_plan(plan, mixed_rows())
        self.assertEqual(written, [("g0/case", f"snapshot_{SNAPSHOT}_elapsed_ms"),
                                   ("g1/case", f"snapshot_{SNAPSHOT}_elapsed_ms_g1"),
                                   ("g2/case", f"snapshot_{SNAPSHOT}_elapsed_ms_g2"),
                                   ("g3/case", f"snapshot_{SNAPSHOT}_total_count_g3")])
        self.assertFalse(any(value in text for text in sum(written, ()) for value in ("IF_A", "IF_B", "HTTP")))

    def test_unscoped_prompt_is_the_same_as_before_groups(self):
        """범위 없는 묶음의 번들 입력·프롬프트 = 묶음 도입 전 이름(`<field>/case`, dataset_id `snapshot_<id>_<field>`)."""
        data = rows()
        self.recommend(data, "elapsed_ms")
        grouped = list(self.models.messages)
        with tempfile.TemporaryDirectory() as tmp:
            before = runtime()
            split = capture.split_sample(data, None)
            paths = capture.write_case(Path(tmp) / "elapsed_ms" / "case", "elapsed_ms", split,
                                       {"dataset_id": f"snapshot_{SNAPSHOT}_elapsed_ms",
                                        "origin": "snapshot train members, source-time split", "field": "elapsed_ms"})
            before.run_field(paths, Path(tmp) / "elapsed_ms" / "rule")
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped, before.models.messages)

    def test_scope_filter_is_the_detector_rule(self):
        """Detector `in_scope` 시험(test_relation_rules SoftScope)과 같은 사례. 원천 행과 정규화 행 모두 같은 판정."""
        from detector.soft_rules import in_scope
        own = {"status": "S", "process_id": "IF_A_PR002", "adapter_type": "HTTP", "process_hub_id": "HUB",
               "transaction_id": "TX-1", "start_time_raw": "2026-10-10T09:00:00", "end_time_raw": "2026-10-10T09:00:01"}
        cases = [(SCOPE_A, own, True), (SCOPE_A, {**own, "process_id": " IF_A_PR002"}, False),
                 (SCOPE_A, {**own, "adapter_type": None}, False), (SCOPE_A, {**own, "process_id": "\\N"}, False),
                 (SCOPE_A, {**own, "adapter_type": 1}, False), (SCOPE_A, {**own, "adapter_type": "http"}, False),
                 (SCOPE_A, {key: value for key, value in own.items() if key != "adapter_type"}, False),
                 ({"processId": "IF_A_PR002"}, {**own, "adapter_type": "MQ"}, True),  # 없는 키는 대조하지 않음
                 ({"processId": "IF_A_PR002"}, {**own, "adapter_type": None}, True),
                 ({"processId": "IF_A_PR002", "processHubId": "HUB"}, own, True),
                 ({"processId": "IF_A_PR002", "processHubId": "HUB"}, {**own, "process_hub_id": "HUB2"}, False),
                 ({"processId": "IF_A_PR002", "processHubId": "HUB"}, {**own, "process_hub_id": " \\N "}, False)]
        for scope, raw, expected in cases:
            with self.subTest(scope=scope, raw=raw):
                self.assertEqual(in_scope({"scope": scope}, raw), expected)
                self.assertEqual(bool(recommendation.scoped([raw], scope, lambda row: row)), expected)
                normalized = capture.normalize_row(raw, None)
                self.assertEqual(bool(recommendation.scoped([normalized], scope, lambda row: row)), expected)
        self.assertIs(recommendation.scoped(cases, None, lambda row: row), cases)

    def test_gpu_check_plan_is_one_unscoped_group_per_field(self):
        checked = rule_v3_check.plan_for("rule_v3", ("total_count", "elapsed_ms"))
        self.assertEqual([(group.field, group.scope, len(group.rules)) for group in checked.groups],
                         [("elapsed_ms", None, 1), ("total_count", None, 1)])
        self.assertEqual(recommendation.recommend(checked, rows(), SNAPSHOT, "persisted_at")["counts"]["proposed"], 2)

    def test_old_shape_and_old_evidence_version_keep_the_result(self):
        old = recommendation.recommend(plan("elapsed_ms", "total_count"), rows(), SNAPSHOT, "persisted_at")
        new = self.run_plan(scoped_plan(soft("elapsed_ms"), soft("total_count")), rows())
        self.assertEqual(old, new)
        self.assertEqual({(json.dumps(item["scope"]), json.dumps(item["candidateIds"])) for item in old["items"]},
                         {("null", "[]")})


# ---- GPU 복제본 분배(generation_replicas 대리 객체와 같은 모양의 가짜 실행기) ----

class ScriptedExecutor:
    """복제본 대리 객체 모양(`run_field`·`adapter_sha`·`package`). 자기 가짜 모델로 실제 번들 흐름을 돌린다.

    `script[field] = (지연 초, 예외 또는 None)`로 필드별 지연·예외를 넣는다.
    """

    def __init__(self, delay=0.0, script=None):
        self.inner = runtime()
        self.package = self.inner.package
        self.adapter_sha = self.inner.adapter_sha
        self.delay = delay
        self.script = script or {}
        self.fields: list[str] = []
        self.threads: set[str] = set()

    def run_field(self, paths, out):
        field = rule_v3_check.case_field(paths)  # 작업 폴더는 묶음 번호(g<n>)라 제안 파일에서 필드를 읽는다
        self.fields.append(field)
        self.threads.add(threading.current_thread().name)
        delay, error = self.script.get(field, (self.delay, None))
        time.sleep(delay)
        if error is not None:
            raise error
        return self.inner.run_field(paths, out)


class FailingReplica(ScriptedExecutor):
    """첫 생성 묶음에서 꺼지는 복제본(대리 객체가 OOM·자식 종료를 `ReplicaUnavailable`로 올리는 모양). 꺼진 뒤 또 받으면 시험 실패."""

    def __init__(self, rerun_on_main=True, code="replica_out_of_memory"):
        super().__init__()
        self.rerun_on_main = rerun_on_main
        self.code = code

    def run_field(self, paths, out):
        from generation_replicas import ReplicaUnavailable
        self.fields.append(rule_v3_check.case_field(paths))
        if len(self.fields) > 1:
            raise AssertionError("disabled replica got another group")
        raise ReplicaUnavailable(self.code, rerun_on_main=self.rerun_on_main)


class Pool:
    def __init__(self, replicas):
        self.replicas = replicas

    def ready(self):
        return list(self.replicas)


class ParallelV3Tests(Base):
    def use(self, replicas, local=None):
        generation_client.set_local_model(SimpleNamespace(rule_v3=local or self.runtime,
                                                          rule_v3_replicas=Pool(replicas)))

    def test_no_ready_replica_keeps_serial_path(self):
        serial = self.recommend()
        self.models.calls.clear()
        self.use([])
        with patch.object(recommendation, "_run_groups", side_effect=AssertionError("parallel path")):
            self.assertEqual(self.recommend(), serial)
        self.assertEqual(len(self.models.calls), 5)

    def test_parallel_matches_serial_in_order_with_caller_progress(self):
        serial = self.recommend()
        self.models.calls.clear()
        replicas = [ScriptedExecutor(delay=0.05) for _ in range(3)]
        self.use(replicas)
        calls = []
        result = self.recommend(progress=lambda done, total: calls.append((done, total, threading.current_thread())))
        self.assertEqual(result, serial)
        self.assertEqual([item["field"] for item in result["items"]], list(FIELDS))
        self.assertEqual([(done, total) for done, total, _ in calls], [(n, 6) for n in range(7)])
        self.assertTrue(all(thread is threading.main_thread() for _, _, thread in calls))
        ran = sorted([field for replica in replicas for field in replica.fields] +
                     [call for call in self.models.calls])
        self.assertEqual(len(ran), 5, "every LLM field ran exactly once")
        self.assertGreaterEqual(sum(bool(replica.fields) for replica in replicas), 2, "fields were spread")
        self.assertTrue(all(name.startswith("rule-v3-group") for replica in replicas for name in replica.threads))

    def test_parallel_groups_match_serial_with_group_progress(self):
        targets = (soft("elapsed_ms"), soft("elapsed_ms", SCOPE_A, "aaaaaaaa"), candidate("elapsed_ms", SCOPE_B, 1),
                   soft("total_count", SCOPE_B, "bbbbbbbb"))
        serial = recommendation.recommend(scoped_plan(*targets), mixed_rows(), SNAPSHOT, "persisted_at")
        replicas = [ScriptedExecutor(delay=0.05) for _ in range(2)]
        self.use(replicas)
        calls = []
        result = recommendation.recommend(scoped_plan(*targets), mixed_rows(), SNAPSHOT, "persisted_at",
                                          progress=lambda done, total: calls.append((done, total)))
        self.assertEqual(result, serial)
        self.assertEqual([(item["field"], item["scope"]) for item in result["items"]],
                         [("elapsed_ms", None), ("elapsed_ms", SCOPE_A), ("elapsed_ms", SCOPE_B), ("total_count", SCOPE_B)])
        self.assertEqual(calls, [(n, 4) for n in range(5)])
        local = len(self.models.calls) - len(serial["items"])  # 직렬 실행 뒤 주 프로세스가 더 돈 묶음
        self.assertEqual(sum(len(replica.fields) for replica in replicas) + local, 4, "every group ran exactly once")

    def test_local_runtime_is_last(self):
        replicas = [ScriptedExecutor(), ScriptedExecutor()]
        self.use(replicas)
        item = self.item(self.recommend(None, "elapsed_ms", "total_count"), "elapsed_ms")
        self.assertEqual(item["status"], "proposed")
        self.assertEqual(self.models.calls, [], "main GPU stays free for explanations")
        self.assertEqual(sorted(replicas[0].fields + replicas[1].fields), ["elapsed_ms", "total_count"])

    def test_field_errors_map_like_serial(self):
        from rule_v3_runtime import load
        io_module = load(_EXPORT).io
        for error, kind, code in ((TimeoutError("generation_timeout"), GenerationUnavailable, "generation_timeout"),
                                  (io_module.InputFailure("x"), ValueError, "recommendation_rule_input_rejected"),
                                  (io_module.ModelFailure("x"), ValueError, "rule_v3_model_failure"),
                                  (GenerationUnavailable("generation_failed"), GenerationUnavailable,
                                   "generation_failed")):
            with self.subTest(code):
                script = {"elapsed_ms": (0, error)}
                self.use([ScriptedExecutor(script=script)], ScriptedExecutor(script=script))
                with self.assertRaises(kind) as caught:
                    self.recommend(None, "elapsed_ms", "total_count")
                self.assertEqual(str(caught.exception), code)

    def test_first_failure_in_field_order_and_rest_skipped(self):
        from rule_v3_runtime import load
        io_module = load(_EXPORT).io
        script = {"elapsed_ms": (0.3, io_module.InputFailure("late")), "error_count": (0, TimeoutError("fast"))}
        executors = [ScriptedExecutor(script=script) for _ in range(3)]  # 마지막이 주 프로세스 몫
        self.use(executors[:2], executors[2])
        calls = []
        # 시간상 먼저 난 실패는 error_count(timeout)지만 필드 순서상 앞인 elapsed_ms의 실패가 올라간다.
        with self.assertRaisesRegex(ValueError, "recommendation_rule_input_rejected"):
            self.recommend(progress=lambda done, total: calls.append(done))
        ran = [field for executor in executors for field in executor.fields]
        self.assertEqual(len(ran), len(set(ran)))
        # 실행기 3개라 동시에 시작한 앞 세 필드까지만 돌고, 나머지는 실패 뒤 시작하지 않는다.
        self.assertLessEqual(set(ran), {"elapsed_ms", "error_count", "error_ratio"})
        self.assertLessEqual({"elapsed_ms", "error_count"}, set(ran))
        self.assertEqual(calls, [0])

    def test_replica_out_of_memory_reruns_group_elsewhere(self):
        serial = self.recommend()
        failing = FailingReplica()
        others = [ScriptedExecutor(delay=0.05) for _ in range(2)]
        local = ScriptedExecutor(delay=0.05)
        self.use([failing, *others], local)
        calls = []
        result = self.recommend(progress=lambda done, total: calls.append((done, total)))
        self.assertEqual(result, serial, "same items, order and content as serial")
        self.assertEqual(calls, [(n, 6) for n in range(7)], "a re-run group is counted once")
        [lost] = failing.fields
        ran = [field for executor in (*others, local) for field in executor.fields]
        self.assertEqual(sorted(ran), sorted(set(ran)), "every other group ran once")
        self.assertEqual(len(ran), 5)
        self.assertIn(lost, ran, "the failed group ran again on another executor")

    def test_out_of_memory_everywhere_fails_like_today_with_bounded_attempts(self):
        failing = [FailingReplica(), FailingReplica()]
        local = ScriptedExecutor(script={field: (0, GenerationUnavailable("generation_failed")) for field in FIELDS})
        self.use(failing, local)
        with self.assertRaises(GenerationUnavailable) as caught:
            self.recommend(None, "elapsed_ms", "total_count")
        self.assertEqual(str(caught.exception), "generation_failed")
        self.assertEqual([len(replica.fields) for replica in failing], [1, 1], "each replica tried once, then dropped")
        self.assertLessEqual(len(local.fields), 2)
        self.assertLessEqual(sum(len(replica.fields) for replica in failing) + len(local.fields), 4)

    def test_lost_replica_reruns_only_on_other_replicas(self):
        serial = self.recommend(None, "elapsed_ms")
        lost, other, local = FailingReplica(False, "replica_exited"), ScriptedExecutor(), ScriptedExecutor()
        self.use([lost, other], local)
        self.assertEqual(self.recommend(None, "elapsed_ms"), serial)
        self.assertEqual((lost.fields, other.fields, local.fields), (["elapsed_ms"], ["elapsed_ms"], []))
        # 남은 복제본이 없으면 주 프로세스로 보내지 않고 기존처럼 generation_failed
        lost, local = FailingReplica(False, "replica_exited"), ScriptedExecutor()
        self.use([lost], local)
        with self.assertRaisesRegex(GenerationUnavailable, "generation_failed"):
            self.recommend(None, "elapsed_ms")
        self.assertEqual((lost.fields, local.fields), (["elapsed_ms"], []))

    def test_progress_failure_stops_remaining_fields(self):
        executors = [ScriptedExecutor(delay=0.05) for _ in range(2)]
        self.use(executors[:1], executors[1])

        def progress(done, total):
            if done == 1:
                raise RuntimeError("training_ownership_lost")
        with self.assertRaisesRegex(RuntimeError, "training_ownership_lost"):
            self.recommend(progress=progress)
        self.assertLess(sum(len(executor.fields) for executor in executors), 5)


class RunnerV3Tests(Base):
    def setUp(self):
        super().setUp()
        self.message = ClaimedMessage(queue="llm_recommendation", id=uuid.uuid4(), payload={}, attempts=1,
                                      lease_token=uuid.uuid4(), created_at=None)
        for target in ("lock_training_ownership", "acknowledge"):
            patcher = patch.object(runner, target, return_value=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_v3_job_reads_clock_and_writes_progress(self):
        data = rows()
        connection = Connection({"extract(epoch": (0.0,), "FROM run_job_snapshots": (SNAPSHOT,),
                                 "SELECT split::text,count": [("train", len(data))], "SELECT manifest_hash": ("m",),
                                 "data_snapshot_members AS m": data, "selection_spec->>'timeBasis'": ("persisted_at",),
                                 "FROM ops_sources": ({"status": "verified", "timezone": "Asia/Seoul"},)})
        job = {"id": uuid.uuid4(), "kind": "recommend_thresholds", "input": job_spec(rule("elapsed_ms")),
               "actor": "admin-1", "contractVersion": "v03", "llmSpec": LLM_SPEC_V3}
        result = runner._execute(connection, job, uuid.uuid4(), None, self.message, uuid.uuid4())
        self.assertEqual(result["provider"], "rule_v3")
        self.assertEqual(len(connection.find("FROM ops_sources")), 1)
        progress = [json.loads(params[0]) for sql, params in connection.statements
                    if sql.startswith("UPDATE run_jobs SET result_summary")]
        self.assertEqual(progress, [{"progress": {"processed": 0, "total": 1}}, {"progress": {"processed": 1, "total": 1}}])

    def test_new_lease_and_terminal_failures_clear_progress(self):
        import pg_queue
        clear = pg_queue.CLEAR_PROGRESS
        job_id, previous = uuid.uuid4(), uuid.uuid4()
        message = ClaimedMessage(queue="llm_recommendation", id=uuid.uuid4(),
                                 payload={"schemaVersion": 1, "jobId": str(job_id)}, attempts=1,
                                 lease_token=uuid.uuid4(), created_at=None)
        for count, statement in ((1, "SET status = 'running', attempt_count"), (3, "error_code = 'max_attempts'")):
            row = (job_id, "recommend_thresholds", "queued", {}, "admin-1", count, 3, previous, "admin", "v03",
                   LLM_SPEC_V3)
            connection = Connection({"attempt_count, max_attempts, current_attempt_id": row, "status='leased'": (1,),
                                     "SELECT status FROM run_job_attempts": ("expired",)})
            runner._claim_job(connection, message, "llm-test")
            self.assertIn(clear, connection.find(statement)[0][0], statement)
        connection = Connection({})
        pg_queue.reconcile_dead_jobs(connection, "llm_recommendation")
        self.assertIn(clear, connection.find("error_code='queue_exhausted',finished_at")[0][0])

    def test_retry_wait_clears_progress(self):
        connection = Connection({})
        job = {"id": uuid.uuid4(), "kind": "recommend_thresholds", "input": {}, "llmSpec": LLM_SPEC_V3}
        runner._finish(connection, self.message, job, uuid.uuid4(), uuid.uuid4(), None, "job_processing_failed", False)
        queued = connection.find("status = 'queued'")[0][0]
        self.assertIn("result_summary = CASE WHEN kind::text = 'recommend_thresholds' THEN NULL ELSE result_summary END", queued)


if __name__ == "__main__":
    unittest.main()

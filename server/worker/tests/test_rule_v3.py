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
        self.max_time_seen: list = []
        self.on_call = None
        self.adapter_sha = json.loads((_EXPORT / "model_manifest.json").read_text(encoding="utf-8"))["Rule"]["weight_sha256"]

    def generate(self, kind, messages, schema, dest, budget, call, maximum=1536, arm="Base"):
        from specmodel.io import digest, write
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        receipt = budget.reserve(kind, call)
        self.calls.append(call)
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

    def test_deferred_keeps_closed_fields(self):
        data = rows(48) + [observation(index, end=False) for index in range(48, 60)]
        item = self.item(self.recommend(data, "elapsed_ms"), "elapsed_ms")
        self.assertEqual((item["status"], item["operator"], item["threshold"]), ("deferred", None, None))
        self.assertEqual(item["evidence"]["currentN"], 0)
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

    def test_retry_wait_clears_progress(self):
        connection = Connection({})
        job = {"id": uuid.uuid4(), "kind": "recommend_thresholds", "input": {}, "llmSpec": LLM_SPEC_V3}
        runner._finish(connection, self.message, job, uuid.uuid4(), uuid.uuid4(), None, "job_processing_failed", False)
        queued = connection.find("status = 'queued'")[0][0]
        self.assertIn("result_summary = CASE WHEN kind::text = 'recommend_thresholds' THEN NULL ELSE result_summary END", queued)


if __name__ == "__main__":
    unittest.main()

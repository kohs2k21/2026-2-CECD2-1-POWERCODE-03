"""모델 버전 v3 구성 생성·판독과 운영 상태 판독 단위 시험(contracts/model-version.md)."""
from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
import re
import sys
import time
import unittest
from unittest.mock import patch
import uuid

from psycopg import errors

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detector import computation
from methods import registry
from trainer import model_version, runner
from trainer.operational_state import DEFAULT_POLICY, OperationalState, read_operational_state

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "model-version.md"
SOURCE = "11111111-1111-4111-8111-111111111111"
FEATURE_SET = "22222222-2222-4222-8222-222222222222"
SNAPSHOT = "55555555-5555-4555-8555-555555555555"
MODEL = "33333333-3333-4333-8333-333333333333"
FIT = "44444444-4444-4444-8444-444444444444"
CONFIG = "66666666-6666-4666-8666-666666666666"
JOB = "77777777-7777-4777-8777-777777777777"
PERIOD = {"from": "2026-09-30T15:00:00Z", "until": "2026-10-31T15:00:00Z", "timeBasis": "persisted_at"}
# 계약 내용 hash 절의 시험 벡터. 입력 바이트를 손으로 적어 구현과 독립으로 둔다.
CANONICAL = ('{"featureSetId":"22222222-2222-4222-8222-222222222222","methods":[{"artifacts":'
             '{"fitArtifactId":"44444444-4444-4444-8444-444444444444","modelArtifactId":"33333333-3333-4333-8333-333333333333"},'
             '"enabled":true,"featureSetId":"22222222-2222-4222-8222-222222222222","methodId":"ml:iforest",'
             '"parameters":{"contamination":"auto","max_samples":512,"n_estimators":128,"random_state":42,'
             '"thresholdPolicy":"train-internal-p99-v1"}}],"sourceId":"11111111-1111-4111-8111-111111111111"}')
VECTOR = "e1cd04c824db448281f4aead9f2f451444d9d61b15780b25d57cc0848b041310"


def spec(**overrides):
    method = registry.ML_METHODS_BY_ID["ml:iforest"]
    values = {"config_id": CONFIG, "job_id": JOB, "source_id": SOURCE, "feature_set_id": FEATURE_SET,
              "snapshot_id": SNAPSHOT, "method": method, "parameters": registry.iforest_parameters(),
              "parameters_requested": False, "model_artifact_id": MODEL, "fit_artifact_id": FIT,
              "selection": {"policyVersion": "train-success-only-v1", "trainingPeriod": PERIOD}, "period": PERIOD}
    return model_version.build_spec(**{**values, **overrides})


def contract_example():
    block = re.search(r"## configuration_spec schemaVersion 3.*?```json\n(.*?)\n```", CONTRACT.read_text(encoding="utf-8"), re.S)
    return json.loads(block.group(1))


class RequestRouteTests(unittest.TestCase):
    def test_v3_keys_without_policy_produce_a_model_version(self):
        for key in model_version.REQUEST_KEYS:
            self.assertTrue(model_version.is_model_version_request({key: None}), key)

    def test_requests_without_v3_keys_or_with_a_policy_stay_on_the_old_path(self):
        self.assertFalse(model_version.is_model_version_request({"kind": "train"}))
        policy = {"mode": "ml_only", "deadlineMs": 3000, "combination": "selected_path"}
        self.assertFalse(model_version.is_model_version_request({"modelKey": "iforest", "detectionPolicy": policy}))

    def test_keys_the_trainer_cannot_apply_are_not_ignored(self):
        for request in ({"excludedIntervals": []}, {"timeBasis": "esb_occurred_at"}):
            with self.assertRaisesRegex(ValueError, "^config_schema_not_supported_yet$"):
                model_version.check_training_scope(request)
        model_version.check_training_scope({"timeBasis": "persisted_at", "modelKey": "iforest"})

    def test_rules_do_not_ride_on_a_model_version_request(self):
        with self.assertRaisesRegex(ValueError, "^invalid_job_input$"):
            model_version.check_training_scope({"modelKey": "iforest", "ruleVersionIds": [SOURCE]})
        model_version.check_training_scope({"modelKey": "iforest", "ruleVersionIds": []})
        model_version.check_training_scope({"detectionPolicy": {}, "modelKey": "iforest", "ruleVersionIds": [SOURCE]})


class SpecTests(unittest.TestCase):
    def test_keys_match_the_contract_example(self):
        example, built = contract_example(), spec()
        self.assertEqual(set(built), set(example))
        self.assertEqual(set(built["methods"][0]), set(example["methods"][0]))
        self.assertEqual(set(built["methods"][0]["artifacts"]), set(example["methods"][0]["artifacts"]))
        self.assertEqual(set(built["createdFrom"]), set(example["createdFrom"]))
        self.assertEqual(built["methods"][0]["parameters"], example["methods"][0]["parameters"])
        for key in ("schemaVersion", "contractVersion", "contentHashVersion", "catalogVersion"):
            self.assertEqual(built[key], example[key], key)
        for key in ("methodId", "kind", "enabled", "methodVersion", "parameterSource"):
            self.assertEqual(built["methods"][0][key], example["methods"][0][key], key)

    def test_model_version_links_to_itself_and_its_job(self):
        built = spec()
        self.assertEqual(built["methods"][0]["artifacts"], {"candidateConfigVersionId": CONFIG, "trainJobId": JOB,
                                                            "modelArtifactId": MODEL, "fitArtifactId": FIT})
        self.assertEqual(built["createdFrom"], {"kind": "train", "trainJobId": JOB, "recommendationJobId": None})
        self.assertIsNone(built["baseVersionId"])
        for key in ("detectionPolicy", "statusRulePolicy", "ruleVersionIds"):
            self.assertNotIn(key, built)

    def test_parameter_source_says_whether_the_request_chose_values(self):
        self.assertEqual(spec()["methods"][0]["parameterSource"], "catalog_default")
        self.assertEqual(spec(parameters_requested=True)["methods"][0]["parameterSource"], "user")

    def test_display_name_uses_korean_calendar_days(self):
        self.assertEqual(spec()["displayName"], "Isolation Forest 2026-10-01~2026-11-01")

    def test_hash_matches_the_contract_vector(self):
        self.assertEqual(hashlib.sha256(CANONICAL.encode()).hexdigest(), VECTOR)
        self.assertEqual(model_version.content_hash(spec()), VECTOR)

    def test_hash_ignores_descriptive_fields_and_follows_identity_fields(self):
        changed = {**spec(), "displayName": "다른 이름", "baseVersionId": CONFIG, "createdFrom": {}, "catalogVersion": "x",
                   "trainingSnapshotId": CONFIG, "trainingSelection": {}}
        self.assertEqual(model_version.content_hash(changed), VECTOR)
        self.assertNotEqual(model_version.content_hash(spec(model_artifact_id=CONFIG)), VECTOR)
        self.assertNotEqual(model_version.content_hash(spec(parameters={**registry.iforest_parameters(), "random_state": 7})), VECTOR)

    def test_hash_keeps_non_ascii_as_utf8(self):
        text = '{"featureSetId":"f","methods":[{"artifacts":{"fitArtifactId":"b","modelArtifactId":"a"},"enabled":true,' \
               '"featureSetId":"f","methodId":"ml:x","parameters":{"이름":"값"}}],"sourceId":"s"}'
        document = {"sourceId": "s", "featureSetId": "f", "methods": [
            {"methodId": "ml:x", "enabled": True, "parameters": {"이름": "값"}, "featureSetId": "f",
             "artifacts": {"modelArtifactId": "a", "fitArtifactId": "b"}}]}
        self.assertEqual(model_version.content_hash(document), hashlib.sha256(text.encode("utf-8")).hexdigest())


class ReaderTests(unittest.TestCase):
    def test_known_schemas_are_readable(self):
        for version in (1, 2, 3):
            self.assertEqual(model_version.read_schema_version({"schemaVersion": version}), version)

    def test_unknown_schemas_fail_closed(self):
        for value in ({"schemaVersion": 4}, {"schemaVersion": 0}, {"schemaVersion": "3"}, {"schemaVersion": True},
                      {"schemaVersion": 3.0}, {}, None):
            with self.assertRaisesRegex(ValueError, "^unsupported_config_schema$"):
                model_version.read_schema_version(value)

    def test_model_key_comes_from_the_single_method(self):
        self.assertEqual(model_version.read_model_key(spec()), "iforest")

    def test_malformed_v3_specs_are_not_read(self):
        method = spec()["methods"][0]
        for broken in ({"methods": []}, {"methods": [method, method]}, {"methods": None},
                       {"methods": [{**method, "enabled": False}]}, {"methods": [{**method, "methodId": "soft:x-00000000"}]},
                       {"methods": [{**method, "kind": "soft"}]}, {"contentHashVersion": 1},
                       {"contractVersion": "detection-config-v1"}, {"detectionPolicy": {}}):
            with self.assertRaisesRegex(ValueError, "^unsupported_config_schema$"):
                model_version.read_model_key({**spec(), **broken})


class FakeConnection:
    def __init__(self, rows=None, error=None):
        self.rows, self.error = rows, error

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, _query):
        if self.error:
            raise self.error

    def fetchall(self):
        return self.rows


POLICY = {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1}, "deadlineMs": 2000}
MODELS = [{"configVersionId": CONFIG, "modelKey": "iforest", "activatedAtRevision": 5}]


class OperationalStateTests(unittest.TestCase):
    def read(self, *row):
        return read_operational_state(FakeConnection([row]))

    def rejects(self, code, *row):
        with self.assertRaisesRegex(ValueError, f"^{code}$"):
            self.read(*row)

    def test_no_saved_policy_is_revision_zero_with_the_built_in_policy(self):
        state = self.read(0, [], 0, None)
        self.assertEqual((state.active_model_set_revision, state.active_models, state.detection_policy_revision), (0, (), 0))
        self.assertEqual(state.detection_policy, DEFAULT_POLICY)

    def test_saved_values_are_copied_into_result_and_evidence(self):
        state = self.read(7, MODELS, 2, POLICY)
        self.assertEqual([(str(item.config_version_id), item.model_key) for item in state.active_models], [(CONFIG, "iforest")])
        self.assertEqual(state.deadline_ms, 2000)
        self.assertEqual(state.result_fields(), {"activeModelSetRevision": 7, "detectionPolicy": {
            "revision": 2, "policy": POLICY, "fixedAt": "attempt_start"}})
        self.assertEqual(state.evidence_fields(), {"activeModelSetRevision": 7, "detectionPolicyRevision": 2, "detectionPolicy": POLICY})

    def test_a_null_policy_with_a_revision_is_not_replaced_by_the_default(self):
        self.rejects("detection_policy_unavailable", 1, [], 3, None)
        self.rejects("detection_policy_unavailable", 1, [], 0, POLICY)

    def test_invalid_policies_stop(self):
        self.rejects("invalid_detection_policy", 1, [], 1, {"mode": "ml_only", "deadlineMs": 3000, "combination": "selected_path"})
        self.rejects("invalid_detection_policy", 1, [], 1, {**POLICY, "version": 1})
        self.rejects("invalid_detection_policy", 1, [], 1, {**POLICY, "deadlineMs": 99})
        self.rejects("invalid_detection_policy", 1, [], 1, {**POLICY, "k": {"hard": 1, "soft": 1}})
        self.rejects("unsupported_detection_policy", 1, [], 1, {**POLICY, "policyKey": "other"})

    def test_a_policy_key_of_the_wrong_type_stops_without_retry(self):
        for key in (["category-k-of-n-or-v1"], {"key": 1}, 1, None):
            self.rejects("unsupported_detection_policy", 1, [], 1, {**POLICY, "policyKey": key})

    def test_invalid_active_sets_stop(self):
        for models in (None, [{}], [{"configVersionId": "x", "modelKey": "iforest"}], [{"configVersionId": CONFIG, "modelKey": 1}], ["x"]):
            self.rejects("invalid_active_model_set", 1, models, 0, None)

    def test_a_missing_or_unreadable_view_stops_without_retry(self):
        for connection in (FakeConnection([]), FakeConnection([(0, [], 0, None)] * 2),
                           FakeConnection(error=errors.InsufficientPrivilege("permission denied")),
                           FakeConnection(error=errors.UndefinedTable("no view"))):
            with self.assertRaisesRegex(ValueError, "^operational_state_unavailable$"):
                read_operational_state(connection)

    def test_active_models_keep_stored_order_and_identity(self):
        other = str(uuid.uuid4())
        state = self.read(2, [*MODELS, {"configVersionId": other, "modelKey": "minibatch_kmeans"}], 0, None)
        self.assertEqual([item.model_key for item in state.active_models], ["iforest", "minibatch_kmeans"])


class EvaluationConnection:
    def cursor(self):
        return self

    def transaction(self):
        return nullcontext()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, args=None):
        self.last = query

    def fetchone(self):
        return (spec(),) if "configuration_spec" in self.last else (0,)

    def commit(self):
        pass


class V3EvaluationWarmupTests(unittest.TestCase):
    """첫 행의 적재가 기한을 넘기는 상황을 고정 대기로 흉내 낸다. 예열이 있으면 대기가 일어나지 않는다."""

    def evaluate(self, prepare, finish):
        loaded = []

        def score(_reference, values):
            if not loaded:
                time.sleep(.8)
                loaded.append(True)
            return float(values["count_raw"])

        def load(_reference):
            prepare()
            loaded.append(True)

        self.deadlines = []
        real_compute = computation.compute

        def recording_compute(context, raw, root, **options):
            self.deadlines.append(context["policy"].deadline_ms)
            return real_compute(context, raw, root, **options)

        field = {"name": "count_raw", "expression": {"op": "field", "field": "P.total_count"}}
        context = {"policy": None, "rules": [], "statusRulePolicy": None, "definitions": [field], "featureSetId": "fixture",
                   "models": [("fixture", "m", "hash", "f", "hash", {"featureNames": ["count_raw"]},
                               {"value": .5, "direction": "higher_is_anomaly"}, "iforest")]}
        rows = [(uuid.uuid4(), {"status": "S", "total_count": value}, "validation", None) for value in (0, 1, 0)]
        job = {"id": uuid.uuid4(), "input": {"sourceId": SOURCE, "configVersionId": CONFIG, "snapshotId": SNAPSHOT, "purpose": "validation"}}
        state = OperationalState(3, (), 1, {**POLICY, "deadlineMs": 500})
        message = type("Message", (), {"created_at": None})()
        with patch("detector.computation.load_context", return_value=context), \
             patch("detector.computation.compute", recording_compute), \
             patch("detector.computation.model_reference", return_value={}), \
             patch("detector.computation.verified_artifact_path", return_value=Path("fixture")), \
             patch("model_adapters.score_iforest", side_effect=score), patch("model_adapters.prepare_iforest", side_effect=load), \
             patch.object(runner, "_snapshot_rows", return_value=rows), patch.object(runner, "read_operational_state", return_value=state), \
             patch.object(runner, "lock_training_ownership", return_value=True), patch.object(runner, "_finish", finish):
            return runner._evaluate(EvaluationConnection(), job, Path("."), message, uuid.uuid4(), uuid.uuid4())

    def test_a_slow_first_load_does_not_leave_the_evaluation_unmeasured(self):
        result = self.evaluate(lambda: None, lambda *_args, **_kwargs: None)
        self.assertEqual((result["applicability"], result["scoredCount"], result["unavailableCount"]), ("valid", 3, 0))
        self.assertEqual(result["populationCount"], 3)

    def test_only_the_discarded_row_gets_the_long_deadline(self):
        self.evaluate(lambda: None, lambda *_args, **_kwargs: None)
        self.assertEqual(self.deadlines, [runner.WARMUP_DEADLINE_MS, 500, 500, 500])

    def test_a_failed_warmup_fails_the_job_without_publishing_an_evaluation(self):
        published = []

        def broken():
            raise ValueError("incompatible_model_runtime")

        with self.assertRaisesRegex(ValueError, "^incompatible_model_runtime$"):
            self.evaluate(broken, lambda *args, **_kwargs: published.append(args))
        self.assertEqual(published, [])


if __name__ == "__main__":
    unittest.main()

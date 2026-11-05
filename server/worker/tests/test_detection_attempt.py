"""v04 시도 실행의 DB 없는 부분: 운영 상태 해석, 방법 단위 기한 처리, 점수 형식."""
from __future__ import annotations

import ast
from concurrent.futures import Future
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch
import uuid

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from detector import jobs
from detector.evidence_v4 import PinnedRevisions, build_evidence
from detector.hard_rules import HARD_RULES, PROCESS_STATUS, read_hard_rule_snapshot, unresolved_hard_result
from detector.method_results import method_result, ml_method_result, unresolved_result
from detector.method_runner import MethodOutcome, MethodTask, execute_methods
from detector.operational_state import pin_operational_state, rule_set_content_hash
from detector.policy import _POOLS, _SLOTS, parse_policy_v2
from detector.soft_rules import soft_rule_fields, unresolved_soft_result

WORKER = Path(__file__).resolve().parents[1]
CATALOG = WORKER.parents[0] / "contracts" / "method-catalog.json"
BYCODE = {"PROCESS_STATUS_F": "critical", "PROCESS_STATUS_P": "warning", "PROCESS_STATUS_Z": "warning",
          "PROCESS_STATUS_H": "info", "PROCESS_STATUS_N": "info"}
R1_HASH = "de6a2ad8d0dcee189f36852a279263d3408f826632b95b4f14a97406b801bed0"
R0_HASH = "80d0377d69597900b7506c26761143077ae7306f464bc44d759bb62f7c83bc89"
SOFT = {"id": "soft:elapsed_ms-3f9a2c1d", "field": "elapsed_ms", "operator": "gt", "threshold": 8000,
        "severity": "warning", "enabled": True, "name": "표시 이름은 hash 입력이 아니다", "unit": "ms"}


def hard(rule_id, enabled=True, severity="critical", **extra):
    return {"id": rule_id, "enabled": enabled, "severity": severity, **extra}


def snapshot(hard_rules=None, soft_rules=()):
    return {"hardRules": hard_rules if hard_rules is not None else [
        {**rule, "enabled": True} for rule in HARD_RULES], "softRules": list(soft_rules)}


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.conn.last = "view" if "ops_operational_state" in sql else "snapshot"

    def fetchall(self):
        return self.conn.view_rows

    def fetchone(self):
        return self.conn.stored


class FakeConn:
    """뷰가 돌려줄 수 없는 행(정책 NULL인 리비전 1 등)을 시험한다. 실제 뷰 경로는 integration 시험이 본다."""

    def __init__(self, *, policy_revision=0, policy=None, content=None, models=(), rows=None, stored="same", version=2):
        content = content if content is not None else snapshot()
        digest = rule_set_content_hash(content, version)
        self.view_rows = rows if rows is not None else [
            (1, digest, version, 0, list(models), policy_revision, policy)]
        self.stored = (digest, version, content) if stored == "same" else stored

    def cursor(self):
        return FakeCursor(self)


class RuleSetHash(unittest.TestCase):
    def test_v2_reference_values(self):
        status = hard("hard:process-status", severity={"byCode": BYCODE})
        negative = hard("hard:negative-duration")
        self.assertEqual(rule_set_content_hash({"hardRules": [status, negative], "softRules": []}, 2),
                         "b6a9c84b21e7deb8df0d22bb6a11a5cd5fc80779eb4797f195505842bf605f3d")
        self.assertEqual(rule_set_content_hash({"hardRules": [status, {**negative, "enabled": False}],
                                                "softRules": [SOFT]}, 2),
                         "4cf61820400fb0de6eb41dca009cd1d20d8f29eccfcf609db8e4c6dd8098fe3d")

    def test_v1_reference_value_ignores_severity(self):
        rules = [{"id": "hard:process-status", "enabled": True, "severity": "무시"},
                 {"id": "hard:negative-duration", "enabled": False}]
        self.assertEqual(rule_set_content_hash({"hardRules": rules, "softRules": [SOFT]}, 1),
                         "5b15237724b48fcc4f18335a374ad7d5d8d7bcbaa7b5f851120f4d17b23dd8aa")

    def test_system_rows(self):
        self.assertEqual(rule_set_content_hash(snapshot(), 2), R1_HASH)
        r0 = {"hardRules": [{"id": "hard:process-status", "enabled": True},
                            {"id": "hard:negative-duration", "enabled": True}], "softRules": []}
        self.assertEqual(rule_set_content_hash(r0, 1), R0_HASH)

    def test_threshold_number_form_does_not_change_hash(self):
        values = [rule_set_content_hash(snapshot(soft_rules=[{**SOFT, "threshold": value}]), 2)
                  for value in (8000, 8000.0)]
        self.assertEqual(values[0], values[1])

    def test_unknown_version_and_malformed_snapshot_fail(self):
        with self.assertRaisesRegex(ValueError, "rule_set_hash_version_unknown"):
            rule_set_content_hash(snapshot(), 3)
        for broken in ({"hardRules": [{"id": "hard:process-status"}], "softRules": []},
                       {"hardRules": [], "softRules": [{**SOFT, "threshold": "8000"}]},
                       {"hardRules": []}):
            with self.assertRaisesRegex(ValueError, "rule_set_hash_mismatch"):
                rule_set_content_hash(broken, 2)


class Pinning(unittest.TestCase):
    def test_null_policy_only_at_revision_zero(self):
        state = pin_operational_state(FakeConn())
        self.assertEqual(state.policy.document(), {"version": 2, "policyKey": "category-k-of-n-or-v1",
                                                   "k": {"hard": 1, "soft": 1, "ml": 1}, "deadlineMs": 3000})
        self.assertEqual(state.revisions.detection_policy_revision, 0)
        with self.assertRaisesRegex(ValueError, "detection_policy_unavailable"):
            pin_operational_state(FakeConn(policy_revision=3))

    def test_stored_policy_at_revision_zero_is_not_trusted(self):
        with self.assertRaisesRegex(ValueError, "detection_policy_unavailable"):
            pin_operational_state(FakeConn(policy={"version": 2}))

    def test_zero_or_many_rows_fail(self):
        for rows in ([], [(0,), (1,)]):
            with self.assertRaisesRegex(ValueError, "operational_state_unavailable"):
                pin_operational_state(FakeConn(rows=rows))

    def test_snapshot_must_match_view(self):
        conn = FakeConn()
        digest, version, content = conn.stored
        with self.assertRaisesRegex(ValueError, "rule_set_snapshot_unavailable"):
            pin_operational_state(FakeConn(stored=None))
        for stored in (("0" * 64, version, content), (digest, 1, content),
                       (digest, version, snapshot(soft_rules=[SOFT]))):
            with self.assertRaisesRegex(ValueError, "rule_set_hash_mismatch"):
                pin_operational_state(FakeConn(stored=stored))

    def test_hash_version_follows_the_row(self):
        content = {"hardRules": [{"id": "hard:process-status", "enabled": True}], "softRules": []}
        state = pin_operational_state(FakeConn(content=content, version=1))
        self.assertEqual(state.hard_rules.enabled_ids, ("hard:process-status",))

    def test_disabled_soft_rules_are_not_methods(self):
        state = pin_operational_state(FakeConn(content=snapshot(soft_rules=[{**SOFT, "enabled": False}])))
        self.assertEqual(state.soft_rules, ())

    def test_soft_rule_slot_rejects_other_categories(self):
        for method_id in ("sr:r00", "hard:process-status"):
            content = snapshot(soft_rules=[{**SOFT, "id": method_id}])
            with self.assertRaisesRegex(ValueError, "method_kind_not_allowed"):
                pin_operational_state(FakeConn(content=content))

    def test_no_active_method_fails(self):
        content = snapshot([{**rule, "enabled": False} for rule in HARD_RULES])
        with self.assertRaisesRegex(ValueError, "detection_methods_empty"):
            pin_operational_state(FakeConn(content=content))

    def test_rule_only_runs_without_models(self):
        state = pin_operational_state(FakeConn())
        self.assertEqual(state.active_models, ())
        self.assertEqual(len(state.hard_rules.enabled_ids), 4)

    def test_active_models_keep_order_and_reject_malformed(self):
        first, second = str(uuid.uuid4()), str(uuid.uuid4())
        state = pin_operational_state(FakeConn(models=[{"configVersionId": first, "modelKey": "iforest"},
                                                       {"configVersionId": second, "modelKey": "other_model"}]))
        self.assertEqual([model.method_id for model in state.active_models], ["ml:iforest", "ml:other_model"])
        for models in ([{"modelKey": "iforest"}], [{"configVersionId": "x", "modelKey": "iforest"}],
                       [{"configVersionId": first, "modelKey": 3}], ["iforest"]):
            with self.assertRaisesRegex(ValueError, "invalid_active_model_set"):
                pin_operational_state(FakeConn(models=models))
        with self.assertRaisesRegex(ValueError, "duplicate_method_id"):
            pin_operational_state(FakeConn(models=[{"configVersionId": first, "modelKey": "iforest"},
                                                   {"configVersionId": second, "modelKey": "iforest"}]))

    def test_k_above_n_fails(self):
        policy = {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 5, "soft": 1, "ml": 1},
                  "deadlineMs": 3000}
        with self.assertRaisesRegex(ValueError, "combination_k_out_of_range:hard"):
            pin_operational_state(FakeConn(policy_revision=1, policy=policy))


def result(method_id, anomalous=False):
    return method_result(method_id, "v1", "succeeded", anomalous, code="C", severity="info" if anomalous else None)


def task(method_id, run=None, lane="rule"):
    def outcome():
        value = run() if run else result(method_id)
        return value if isinstance(value, MethodOutcome) else MethodOutcome(value)

    return MethodTask(method_id, lane, outcome,
                      lambda cause, **outcome: unresolved_result(method_id, "v1", cause, **outcome))


def run_methods(*args, **kwargs):
    return execute_methods(*args, **kwargs).methods


def wait_for_free_slots(name):
    limit = time.monotonic() + 3
    while _SLOTS[name]._value != 2:
        if time.monotonic() > limit:
            raise AssertionError(f"{name} 슬롯이 돌아오지 않았다: {_SLOTS[name]._value}")
        time.sleep(.005)


class NeverStartingPool:
    """모든 작업자가 바쁜 풀처럼 제출만 받고 시작하지 않는다."""

    def submit(self, *_args):
        return Future()


class MethodExecution(unittest.TestCase):
    def test_results_keep_task_order_and_measure_elapsed(self):
        started = time.monotonic()
        methods = run_methods([task("hard:a-rule"), task("soft:b-rule"), task("ml:model", lane="ml")], 2000,
                              started_at=started)
        self.assertEqual([method["methodId"] for method in methods], ["hard:a-rule", "soft:b-rule", "ml:model"])
        self.assertTrue(all(method["status"] == "succeeded" and method["elapsedMs"] >= 0 for method in methods))

    def test_late_result_is_discarded_with_measured_elapsed(self):
        release = threading.Event()

        def slow():
            release.wait(5)
            return result("ml:model", True)

        methods = run_methods([task("hard:a-rule"), task("ml:model", slow, "ml")], 100, started_at=time.monotonic())
        release.set()
        self.assertEqual(methods[0]["status"], "succeeded")
        late = methods[1]
        self.assertEqual((late["status"], late["cause"], late["anomalous"]), ("not_ready", "timeout", None))
        self.assertGreaterEqual(late["elapsedMs"], 100)
        self.assertLess(late["elapsedMs"], 2000)

    def test_expired_deadline_runs_nothing(self):
        called = []
        methods = run_methods([task("hard:a-rule", lambda: called.append(1) or result("hard:a-rule"))], 100,
                              started_at=time.monotonic() - 1)
        self.assertEqual(called, [])
        self.assertEqual(methods[0]["cause"], "timeout")
        self.assertGreaterEqual(methods[0]["elapsedMs"], 1000)

    def test_failure_stays_in_that_method(self):
        def broken():
            raise RuntimeError("boom")

        methods = run_methods([task("hard:a-rule", broken), task("hard:b-rule")], 2000, started_at=time.monotonic())
        self.assertEqual((methods[0]["status"], methods[0]["cause"], methods[0]["failureCode"]),
                         ("failed", "failure", "exception"))
        self.assertEqual(methods[1]["status"], "succeeded")

    def test_capacity_when_no_slot(self):
        slots = _SLOTS["ml"]
        held = [slots.acquire(blocking=False) for _ in range(2)]
        try:
            methods = run_methods([task("ml:model", lane="ml")], 500, started_at=time.monotonic())
        finally:
            for _ in held:
                slots.release()
        self.assertEqual((methods[0]["status"], methods[0]["cause"]), ("not_ready", "capacity"))
        self.assertIsNotNone(methods[0]["elapsedMs"])

    def test_many_rules_do_not_exhaust_slots(self):
        tasks = [task(f"soft:rule-{index}") for index in range(36)]
        self.assertTrue(all(method["status"] == "succeeded"
                            for method in run_methods(tasks, 3000, started_at=time.monotonic())))

    def test_slots_return_after_run(self):
        for _ in range(5):
            execute_methods([task("hard:a-rule"), task("ml:model", lane="ml")], 1000, started_at=time.monotonic())
        taken = [_SLOTS["rule"].acquire(blocking=False) for _ in range(2)]
        for _ in taken:
            _SLOTS["rule"].release()
        self.assertEqual(taken, [True, True])

    def test_progress_marks_pending_without_elapsed(self):
        gate, seen = threading.Event(), []

        def slow():
            gate.wait(5)
            return result("ml:model")

        def progress(methods):
            seen.append([(method["methodId"], method["cause"], method["elapsedMs"] is None) for method in methods])
            gate.set()

        execute_methods([task("hard:a-rule"), task("ml:model", slow, "ml")], 3000, started_at=time.monotonic(),
                        on_progress=progress)
        self.assertEqual(seen[0], [("hard:a-rule", None, False), ("ml:model", "pending", True)])
        self.assertEqual(len(seen), 1, "모든 방법이 끝나면 최종 판정이 기록을 닫는다")

    def test_progress_error_stops_and_returns_slots(self):
        def fail(_):
            raise RuntimeError("detection_lease_lost")

        gate = threading.Event()

        def slow():
            gate.wait(5)
            return result("ml:model")

        with self.assertRaisesRegex(RuntimeError, "detection_lease_lost"):
            execute_methods([task("hard:a-rule"), task("ml:model", slow, "ml")],
                            3000, started_at=time.monotonic(), on_progress=fail)
        self.assertLess(_SLOTS["ml"]._value, 2, "모델은 아직 돌고 있어 슬롯을 쥐고 있다")
        gate.set()
        wait_for_free_slots("ml")
        wait_for_free_slots("rule")

    def test_task_that_never_started_returns_its_slot(self):
        with patch.dict(_POOLS, {"ml": NeverStartingPool()}):
            methods = run_methods([task("ml:model", lane="ml")], 50, started_at=time.monotonic())
        self.assertEqual(methods[0]["cause"], "timeout")
        self.assertEqual(_SLOTS["ml"]._value, 2)

    def test_task_that_never_started_returns_its_slot_on_progress_error(self):
        def fail(_):
            raise RuntimeError("detection_lease_lost")

        with patch.dict(_POOLS, {"ml": NeverStartingPool()}):
            with self.assertRaisesRegex(RuntimeError, "detection_lease_lost"):
                execute_methods([task("hard:a-rule"), task("ml:model", lane="ml")], 3000,
                                started_at=time.monotonic(), on_progress=fail)
        self.assertEqual(_SLOTS["ml"]._value, 2)

    def _late_run(self, *, finished):
        """메인 스레드가 기한을 넘겨 멈춰 있는 동안 방법이 끝난다. 끝난 시각은 항상 기한 뒤다."""
        past_deadline = threading.Event()

        def slow():
            past_deadline.wait(5)
            finished.set()
            return MethodOutcome(result("ml:model", True), {"inferenceMs": 1.0, "features": "late"})

        def delayed_main(_methods):
            time.sleep(.15)
            past_deadline.set()
            finished.wait(5)
            time.sleep(.02)  # 방법 스레드가 도착 큐에 넣을 시간

        return run_task(slow, delayed_main)

    def test_result_after_deadline_is_discarded_even_if_it_arrives_before_main_looks(self):
        execution = self._late_run(finished=threading.Event())
        late = execution.methods[1]
        self.assertEqual((late["status"], late["cause"], late["anomalous"]), ("not_ready", "timeout", None))

    def test_values_of_discarded_result_are_not_adopted(self):
        execution = self._late_run(finished=threading.Event())
        self.assertNotIn("ml:model", execution.measured)
        self.assertIn("hard:a-rule", execution.measured)

    def test_values_of_accepted_result_are_adopted(self):
        def scored():
            return MethodOutcome(result("ml:model"), {"inferenceMs": 2.5})

        execution = execute_methods([task("ml:model", scored, "ml")], 3000, started_at=time.monotonic())
        self.assertEqual(execution.measured["ml:model"], {"inferenceMs": 2.5})


def run_task(slow, on_progress):
    return execute_methods([task("hard:a-rule"), task("ml:model", slow, "ml")], 100, started_at=time.monotonic(),
                           on_progress=on_progress)


class UnresolvedShapes(unittest.TestCase):
    def test_hard_timeout_matches_status_rule_record(self):
        hard_rules = read_hard_rule_snapshot(snapshot()["hardRules"])
        method = {**unresolved_hard_result(PROCESS_STATUS, "timeout"), "elapsedMs": 3000.5}
        others = [{**unresolved_hard_result(rule_id, "timeout"), "elapsedMs": 3000.5}
                  for rule_id in hard_rules.enabled_ids[1:]]
        evidence = build_evidence(parse_policy_v2({"version": 2, "policyKey": "category-k-of-n-or-v1",
                                                   "k": {"hard": 1, "soft": 1, "ml": 1}, "deadlineMs": 3000}),
                                  [method, *others], PinnedRevisions(1, "a" * 64, 0, 0), hard_rules,
                                  source_id="s", telemetry={}, observation={}, input_manifest_hash="h")
        self.assertEqual({key: evidence["rules"][0][key] for key in ("observedStatus", "result", "severity", "reason")},
                         {"observedStatus": None, "result": "unavailable", "severity": None, "reason": "status_unknown"})
        self.assertEqual(evidence["methodResults"][0]["elapsedMs"], 3000.5)

    def test_soft_timeout_keeps_definition(self):
        method = unresolved_soft_result(SOFT, "timeout")
        self.assertEqual(method["condition"], {"field": "elapsed_ms", "operator": "gt", "threshold": "8000", "unit": "ms"})
        self.assertIsNone(method["observedValue"])
        self.assertIsNone(unresolved_soft_result({**SOFT, "threshold": "x"}, "timeout")["condition"]["threshold"])


class ScoreTypes(unittest.TestCase):
    spec = {"direction": "higher_is_anomaly", "value": "0.61"}

    def test_numpy_scores_become_plain_floats(self):
        for score in (np.float32(.7), np.float64(.7), np.int64(1)):
            method = ml_method_result("ml:iforest", "v1", "id", score, self.spec, "p", code="IF_ANOMALY")
            self.assertEqual((method["status"], method["anomalous"]), ("succeeded", True))
            self.assertIs(type(method["score"]), float)
            self.assertIs(type(method["scoreDetail"]["raw"]), float)
            self.assertIs(type(method["scoreDetail"]["normalized"]["value"]), float)
            json.dumps(method, allow_nan=False)

    def test_float32_keeps_the_stored_value_in_evidence(self):
        method = ml_method_result("ml:iforest", "v1", "id", np.float32(.1), self.spec, "p", code="IF_ANOMALY")
        self.assertEqual(json.loads(json.dumps(method))["score"], float(np.float32(.1)))

    def test_non_numbers_stay_invalid(self):
        for score in (True, np.bool_(True), "0.7", None, np.float32("nan"), np.float64("inf")):
            method = ml_method_result("ml:iforest", "v1", "id", score, self.spec, "p", code="IF_ANOMALY")
            self.assertEqual((method["status"], method["failureCode"]), ("failed", "invalid_score"), score)


class RegistryLink(unittest.TestCase):
    def test_soft_fields_are_the_committed_catalog(self):
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(soft_rule_fields(), {item["fieldId"]: item for item in catalog["softRuleFields"]})


class HardRuleSelectionSource(unittest.TestCase):
    def test_only_snapshot_reader_constructs_selection(self):
        def constructions(tree):
            return sum(isinstance(node, ast.Call) and getattr(node.func, "id", None) == "HardRuleSelection"
                       for node in ast.walk(tree))

        found = {}
        for path in WORKER.rglob("*.py"):
            if "tests" not in path.parts:
                tree = ast.parse(path.read_text(encoding="utf-8"))
                if constructions(tree):
                    found[path.name] = tree
        self.assertEqual(list(found), ["hard_rules.py"])
        reader = next(node for node in found["hard_rules.py"].body
                      if isinstance(node, ast.FunctionDef) and node.name == "read_hard_rule_snapshot")
        self.assertEqual(constructions(reader), constructions(found["hard_rules.py"]))


class ContractSetting(unittest.TestCase):
    def test_default_is_v03(self):
        with patch.dict(os.environ):
            os.environ.pop("DETECT_JOB_CONTRACT", None)
            self.assertEqual(jobs.job_contract_setting(), "v03")

    def test_unknown_value_stops_job_creation(self):
        with patch.dict(os.environ, {"DETECT_JOB_CONTRACT": "v05"}):
            with self.assertRaisesRegex(ValueError, "invalid_detect_job_contract"):
                jobs.job_contract_setting()


if __name__ == "__main__":
    unittest.main()

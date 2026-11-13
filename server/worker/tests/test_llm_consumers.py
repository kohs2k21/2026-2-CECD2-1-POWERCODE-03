"""LLM 컨테이너의 두 소비자(주 소비자 llm·설명 전용 llm_explanation) 시험. DB·GPU 없이 큐 선택·오류 코드만 본다."""
from __future__ import annotations

import sys
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_worker
from generation_client import GenerationRejected, GenerationUnavailable
from trainer import runner


class RunnerHarness:
    """`runner.run(once=True)`을 DB 없이 한 바퀴 돌린다. claim·정리·완료 호출만 기록한다."""

    def __init__(self, messages=None, error=None, kind="explain", real_execute=False):
        self.claims, self.reconciled, self.finished = [], [], []
        self.messages = dict(messages or {})
        self.error = error
        self.kind = kind
        self.real_execute = real_execute

    def claim(self, conn, queue, *, lease_seconds=60, job_kinds=None, **_):
        self.claims.append((queue, job_kinds))
        return self.messages.pop((queue, job_kinds), None)

    def finish(self, conn, message, job, attempt_id, attempt_token, result, error_code, terminal, **_):
        self.finished.append((error_code, terminal))

    def execute(self, *_args):
        raise self.error

    def run(self, role):
        job = ({"id": uuid.uuid4(), "kind": self.kind, "input": {}, "actor": "u", "contractVersion": "v03", "llmSpec": {}},
               uuid.uuid4(), uuid.uuid4())
        execute = runner._execute if self.real_execute else self.execute
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://unused"}, clear=False), \
                patch.object(runner, "load_dotenv"), patch.object(runner.psycopg, "connect", MagicMock()), \
                patch.object(runner, "claim", self.claim), \
                patch.object(runner, "reconcile_dead_jobs", lambda conn, queue: self.reconciled.append(queue)), \
                patch.object(runner, "_claim_job", return_value=job), \
                patch.object(runner, "TrainingLeaseHeartbeat"), \
                patch.object(runner, "_execute", execute), \
                patch.object(runner, "_finish", self.finish):
            runner.run(once=True, role=role)
        return self


class ConsumerQueueTests(unittest.TestCase):
    def test_explanation_role_claims_only_explanations_and_reconciles_its_queue(self):
        harness = RunnerHarness().run("llm_explanation")
        self.assertEqual(harness.claims, [("llm_explanation", None), ("training", ("explain",))])
        self.assertEqual(harness.reconciled, ["llm_explanation"])

    def test_main_llm_role_keeps_rotation_and_reconciliation(self):
        harness = RunnerHarness().run("llm")
        self.assertEqual(harness.claims, [("llm_recommendation", None), ("llm_explanation", None),
                                          ("training", ("recommend_feature", "recommend_rule", "explain"))])
        self.assertEqual(harness.reconciled, ["training", "llm_recommendation", "llm_explanation"])

    def test_unknown_role_is_rejected(self):
        with self.assertRaises(ValueError):
            RunnerHarness().run("llm_other")


class RetryErrorCodeTests(unittest.TestCase):
    message = {("llm_explanation", None): object()}

    def test_busy_explanation_is_stored_as_generation_busy_and_retried(self):
        harness = RunnerHarness(self.message, GenerationUnavailable("generation_busy")).run("llm_explanation")
        self.assertEqual(harness.finished, [("generation_busy", False)])

    def test_other_runtime_failures_keep_job_processing_failed(self):
        for error in (GenerationUnavailable("generation_service_unavailable"), GenerationRejected(),
                      RuntimeError("generation_busy"), RuntimeError("cuda failure")):
            with self.subTest(error=repr(error)):
                harness = RunnerHarness(self.message, error).run("llm_explanation")
                self.assertEqual(harness.finished, [("job_processing_failed", False)])


class AbolishedJobKindTests(unittest.TestCase):
    """폐지 종류(recommend_feature·recommend_rule)의 남은 작업은 실행 없이 job_kind_abolished로 끝난다."""

    def test_execute_stops_before_any_db_or_model_call(self):
        for kind in ("recommend_feature", "recommend_rule"):
            with self.subTest(kind=kind):
                conn = MagicMock()
                job = {"id": uuid.uuid4(), "kind": kind, "input": {"sourceId": str(uuid.uuid4())},
                       "actor": "u", "contractVersion": "v03", "llmSpec": {}}
                with patch.object(runner, "queue_wait_ms") as queue_wait, \
                        patch.object(runner, "build_snapshot") as snapshot, \
                        patch.object(runner.model_adapters, "recommend_features") as features, \
                        patch.object(runner.model_adapters, "recommend_rules") as rules, \
                        patch.object(runner.model_adapters, "recommend_thresholds") as thresholds:
                    with self.assertRaisesRegex(ValueError, "^job_kind_abolished$"):
                        runner._execute(conn, job, uuid.uuid4(), None, MagicMock(), uuid.uuid4())
                self.assertEqual(conn.mock_calls, [])
                for called in (queue_wait, snapshot, features, rules, thresholds):
                    called.assert_not_called()

    def test_leftover_jobs_end_as_terminal_failure_on_both_queues(self):
        # 주 큐(llm_recommendation)와 옛 training 큐 배수 경로 모두 재시도 없이 종료 실패다.
        for queue, job_kinds in (("llm_recommendation", None),
                                 ("training", ("recommend_feature", "recommend_rule", "explain"))):
            for kind in ("recommend_feature", "recommend_rule"):
                with self.subTest(queue=queue, kind=kind):
                    harness = RunnerHarness({(queue, job_kinds): object()}, kind=kind, real_execute=True).run("llm")
                    self.assertEqual(harness.finished, [("job_kind_abolished", True)])

    def test_abolished_set_is_exactly_the_two_contract_kinds(self):
        self.assertEqual(runner.ABOLISHED_JOB_KINDS, {"recommend_feature", "recommend_rule"})


class LlmWorkerMainTests(unittest.TestCase):
    def test_main_starts_both_consumers_and_restarts_a_failed_explanation_consumer(self):
        roles, restarted, release = [], threading.Event(), threading.Event()

        def consume(*, once=False, poll_seconds=1.0, role="trainer"):
            roles.append(role)
            if role == "llm_explanation":
                if roles.count("llm_explanation") == 1:
                    raise RuntimeError("connection lost")
                restarted.set()
                release.wait(5)  # 다시 시작한 소비자는 main이 끝날 때까지 돈다.
                return
            self.assertEqual(role, "llm")
            self.assertTrue(restarted.wait(5), "explanation consumer was not restarted")

        model, server = object(), MagicMock()
        environment = {"DESIGN_MODEL_TOKEN": "t" * 32, "RESEARCH_BUNDLE_ROOT": "bundle", "RULE_MODEL_BASE_PATH": "base"}
        with patch.dict("os.environ", environment), patch.object(llm_worker, "load_dotenv"), \
                patch.object(llm_worker, "EXPLANATION_RESTART_SECONDS", 0.01), \
                patch("generation_service.DesignModel", return_value=model), \
                patch("generation_service.serve", return_value=server), \
                patch("generation_client.set_local_model") as set_local_model, \
                patch.object(runner, "run", consume), \
                self.assertLogs("llm_worker", "ERROR") as logs:
            llm_worker.main()
        release.set()
        for thread in threading.enumerate():
            if thread.name == "llm-explanation":
                thread.join(5)
                self.assertFalse(thread.is_alive())
        set_local_model.assert_called_once_with(model)
        self.assertIn("llm", roles)
        self.assertGreaterEqual(roles.count("llm_explanation"), 2)
        self.assertIn("explanation consumer failed", logs.output[0])
        server.shutdown.assert_called_once()
        server.server_close.assert_called_once()


if __name__ == "__main__":
    unittest.main()

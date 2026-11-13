"""GPU 생성 차례(`GenerationGate`)와 대화형 대기 초과(`generation_busy`) 시험. GPU·torch 없이 돈다."""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import generation_client
from explanation import explain
from generation_client import GenerationUnavailable, generate
from generation_service import DesignModel, GenerationBusy, GenerationGate, serve


def wait_until(predicate, seconds=2.0):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        time.sleep(0.005)


class Holder:
    """작업 등급으로 차례를 잡고 `release()`까지 놓지 않는다(실행 중인 긴 생성 흉내)."""

    def __init__(self, gate, interactive=False):
        self.gate, self.done = gate, threading.Event()
        self.thread = threading.Thread(target=self.run, args=(interactive,), daemon=True)
        self.thread.start()
        wait_until(lambda: gate.snapshot()["running"])

    def run(self, interactive):
        with self.gate.slot(interactive):
            self.done.wait(5)

    def release(self):
        self.done.set()
        self.thread.join(5)


class GenerationGateTests(unittest.TestCase):
    def test_interactive_waiter_goes_before_job_waiters_and_each_class_is_fifo(self):
        gate, order = GenerationGate(), []
        holder = Holder(gate)

        def enter(name, interactive):
            with gate.slot(interactive, None if not interactive else 5):
                order.append(name)

        threads = []
        for name, interactive, expected in (("job1", False, {"job": 1}), ("job2", False, {"job": 2}),
                                            ("explain1", True, {"interactive": 1}), ("explain2", True, {"interactive": 2})):
            thread = threading.Thread(target=enter, args=(name, interactive), daemon=True)
            thread.start()
            threads.append(thread)
            wait_until(lambda: all(gate.snapshot()[key] == value for key, value in expected.items()))
        self.assertEqual(order, [])
        holder.release()
        for thread in threads:
            thread.join(5)
        self.assertEqual(order, ["explain1", "explain2", "job1", "job2"])
        self.assertEqual(gate.snapshot(), {"running": False, "interactive": 0, "job": 0})

    def test_interactive_wait_cap_raises_busy_and_leaves_counters_consistent(self):
        gate = GenerationGate()
        holder = Holder(gate)
        admitted = []

        def waiting_job():
            with gate.slot(False):
                admitted.append("job")

        job = threading.Thread(target=waiting_job, daemon=True)
        job.start()
        wait_until(lambda: gate.snapshot()["job"] == 1)
        started = time.monotonic()
        with self.assertRaises(GenerationBusy) as caught:
            with gate.slot(True, 0.15):
                self.fail("must not run while a generation is in progress")
        elapsed = time.monotonic() - started
        self.assertEqual(str(caught.exception), "generation_busy")
        self.assertGreaterEqual(elapsed, 0.14)
        self.assertLess(elapsed, 2)
        # 실행 중 생성은 끊지 않고, 기다리던 작업은 버리지 않는다.
        self.assertEqual(gate.snapshot(), {"running": True, "interactive": 0, "job": 1})
        holder.release()
        job.join(5)
        self.assertEqual(admitted, ["job"])
        self.assertEqual(gate.snapshot(), {"running": False, "interactive": 0, "job": 0})
        with gate.slot(True, 0.15):
            self.assertTrue(gate.snapshot()["running"])

    def test_zero_cap_runs_when_free_and_fails_fast_when_held(self):
        gate = GenerationGate()
        with gate.slot(True, 0):
            pass
        holder = Holder(gate)
        with self.assertRaises(GenerationBusy):
            gate.acquire(True, 0)
        holder.release()
        self.assertEqual(gate.snapshot(), {"running": False, "interactive": 0, "job": 0})

    def test_never_two_generations_at_once(self):
        gate, lock = GenerationGate(), threading.Lock()
        state = {"active": 0, "peak": 0, "done": 0}

        def work(interactive):
            with gate.slot(interactive, 5 if interactive else None):
                with lock:
                    state["active"] += 1
                    state["peak"] = max(state["peak"], state["active"])
                time.sleep(0.01)
                with lock:
                    state["active"] -= 1
                    state["done"] += 1

        threads = [threading.Thread(target=work, args=(index % 3 == 0,), daemon=True) for index in range(9)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(state, {"active": 0, "peak": 1, "done": 9})

    def test_exception_in_generation_releases_the_slot(self):
        gate = GenerationGate()
        with self.assertRaises(RuntimeError):
            with gate.slot(False):
                raise RuntimeError("cuda failure")
        self.assertEqual(gate.snapshot(), {"running": False, "interactive": 0, "job": 0})
        with gate.slot(True, 0):
            pass


class _Tokenizer:
    eos_token_id = 0

    def __call__(self, *_args, **_kwargs):
        return {"input_ids": [1, 2]}

    def apply_chat_template(self, *_args, **_kwargs):
        return "text"

    def decode(self, *_args, **_kwargs):
        return '{"factIds":["fact_0"],"nextChecks":[]}'


class DesignModelGateTests(unittest.TestCase):
    """`DesignModel.generate`는 검증·토큰화 뒤 GPU 구간만 차례를 기다린다."""

    def model(self):
        import numpy as np
        model = DesignModel.__new__(DesignModel)
        model.manifest = {"generation": {"maximum_input_tokens": 100}, "base": {"revision": "base"}}
        model.gate = GenerationGate()
        model.tokenizer = _Tokenizer()
        model.torch = SimpleNamespace(no_grad=nullcontext, manual_seed=lambda _: None,
                                      tensor=lambda value, device: np.array(value), ones_like=np.ones_like)
        model.model = SimpleNamespace(set_adapter=lambda _: None, disable_adapter=nullcontext,
                                      generate=lambda input_ids, **_: np.array([[*input_ids[0], 5, 0]]))
        return model

    def test_explain_gives_up_after_the_wait_cap_but_validation_comes_first(self):
        model = self.model()
        holder = Holder(model.gate)
        with patch.dict("os.environ", {"LLM_EXPLANATION_WAIT_SECONDS": "0.1"}):
            with self.assertRaises(ValueError):
                model.generate("explain", "not an object")
            with self.assertRaises(GenerationBusy):
                model.generate("explain", {})
        holder.release()
        with patch.dict("os.environ", {"LLM_EXPLANATION_WAIT_SECONDS": "0.1"}):
            self.assertEqual(model.generate("explain", {})["output"], {"factIds": ["fact_0"], "nextChecks": []})
        self.assertEqual(model.gate.snapshot(), {"running": False, "interactive": 0, "job": 0})


class GenerationBusyTransportTests(unittest.TestCase):
    token = "t" * 32

    def serve(self, model):
        server = serve(model, self.token, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return {"DESIGN_MODEL_URL": f"http://127.0.0.1:{server.server_port}", "DESIGN_MODEL_TOKEN": self.token}

    def test_service_answers_busy_with_503_and_client_keeps_the_code(self):
        class Model:
            def generate(self, kind, value, report_timeout=False):
                raise GenerationBusy()
        environment = self.serve(Model())
        request = urllib.request.Request(environment["DESIGN_MODEL_URL"] + "/generate",
                                         data=json.dumps({"kind": "explain", "input": {}}).encode(),
                                         headers={"Authorization": "Bearer " + self.token})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=10)
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(json.loads(caught.exception.read()), {"code": "generation_busy"})
        with patch.dict("os.environ", environment, clear=True):
            for options in ({}, {"report_timeout": True}):
                with self.subTest(options), self.assertRaisesRegex(GenerationUnavailable, "^generation_busy$"):
                    generate("explain", {}, **options)

    def test_other_503_stays_service_unavailable(self):
        class Model:
            def generate(self, kind, value, report_timeout=False):
                raise RuntimeError("cuda failure")
        with patch.dict("os.environ", self.serve(Model()), clear=True):
            with self.assertRaisesRegex(GenerationUnavailable, "^generation_service_unavailable$"):
                generate("explain", {})

    def test_local_model_busy_maps_to_the_same_code_and_explain_stays_retryable(self):
        class Model:
            def generate(self, kind, value, report_timeout=False):
                raise GenerationBusy()
        generation_client.set_local_model(Model())
        self.addCleanup(generation_client.set_local_model, None)
        for options in ({}, {"report_timeout": True}):
            with self.subTest(options), self.assertRaisesRegex(GenerationUnavailable, "^generation_busy$"):
                generate("explain", {}, **options)
        evidence = {"decisionId": "d"}
        text = json.dumps(evidence)
        job = {"input": {"savedEvidenceJson": text, "savedEvidence": evidence, "decisionId": "d"},
               "llmSpec": {"input_evidence_hash": hashlib.sha256(text.encode()).hexdigest()}}
        # runner.run()은 ValueError를 종료, 그 밖의 RuntimeError를 재시도로 처리한다.
        with self.assertRaises(GenerationUnavailable) as caught:
            explain(job)
        self.assertNotIsInstance(caught.exception, ValueError)


if __name__ == "__main__":
    unittest.main()

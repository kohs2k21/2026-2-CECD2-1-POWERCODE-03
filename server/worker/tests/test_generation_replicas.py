"""Rule v3 GPU 복제본(`generation_replicas`) 시험. torch·GPU 없이 가짜 적재기로 확인한다.

필드 분배·결과 순서·진행률·실패 선택은 실제 번들 export가 필요해 `test_rule_v3.py` 병렬 절에 있다.
"""
from __future__ import annotations

import os
import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import generation_replicas as replicas  # noqa: E402
import replica_fakes as fakes  # noqa: E402
from generation_client import GenerationUnavailable  # noqa: E402


class FakeConnection:
    def __init__(self, replies=(), ready=True):
        self.replies = list(replies)
        self.sent = []
        self.ready = ready

    def send(self, message):
        self.sent.append(message)

    def poll(self, timeout=None):
        return self.ready

    def recv(self):
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


class FakeProcess:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        pass

    def terminate(self):
        self.alive = False

    kill = terminate


def proxy(connection, process=None):
    pool = replicas.ReplicaPool(["1"], ["a", "b", "c"], fakes.SHA, fakes.PACKAGE, start_seconds=1)
    replica = replicas.ReplicaRuntime("1", process or FakeProcess(), connection, fakes.SHA, fakes.PACKAGE, pool)
    pool._ready["1"] = replica
    return pool, replica


class ParseTests(unittest.TestCase):
    def test_gpu_list(self):
        self.assertEqual(replicas.parse_gpus(None, "0"), ([], []))
        self.assertEqual(replicas.parse_gpus(" ", "0"), ([], []))
        self.assertEqual(replicas.parse_gpus("1, 2,,02,0,x", "0"),
                         (["1", "2"], [("2", "replica_gpu_duplicate"), ("0", "replica_gpu_conflict"),
                                       ("x", "replica_gpu_invalid")]))

    def test_start_from_env_needs_rule_v3(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        with patch.dict("os.environ", {"LLM_REPLICA_GPUS": "1", "CUDA_VISIBLE_DEVICES": "0"}):
            with self.assertLogs("generation_replicas", "ERROR") as logs:
                self.assertIsNone(replicas.start_from_env(SimpleNamespace(rule_v3=None), ("a", "b", None)))
            self.assertEqual(logs.output, ["ERROR:generation_replicas:rule_v3_replica_disabled: 1 rule_v3_not_configured"])
        with patch.dict("os.environ", {"LLM_REPLICA_GPUS": ""}):
            self.assertIsNone(replicas.start_from_env(SimpleNamespace(rule_v3=fakes.FakeRuntime()), ("a", "b", "c")))


class CodeTests(unittest.TestCase):
    """자식 `_reply`의 종류 코드 → 부모 대리 객체가 같은 종류 예외로 다시 올린다."""

    def test_child_codes(self):
        runtime = fakes.FakeRuntime()
        cases = {"timeout": ("error", "timeout", "generation_timeout"), "input": ("error", "input", "bad input"),
                 "model": ("error", "model", "bad model"), "value": ("error", "value", "rule_v3_bad_value"),
                 "value_text": ("error", "value", "invalid_job_input")}
        for action, expected in cases.items():
            with self.subTest(action):
                self.assertEqual(replicas._reply(runtime, {"raise": action}, "o"), expected)
        with self.assertLogs("generation_replicas", "ERROR"):
            self.assertEqual(replicas._reply(runtime, {"raise": "other"}, "o"), ("error", "failed", "KeyError"))
        # CUDA 메모리 부족: 이름(OutOfMemoryError)·문구·번들이 감싼 원인 사슬 모두 같은 oom 코드. 감싼 ModelFailure보다 먼저 본다
        for action in ("oom", "oom_text", "oom_wrapped"):
            with self.subTest(action):
                with self.assertLogs("generation_replicas", "ERROR"):
                    self.assertEqual(replicas._reply(runtime, {"raise": action}, "o"),
                                     ("error", "oom", "replica_out_of_memory"))
        ok = replicas._reply(runtime, {}, "o")
        self.assertEqual(ok[0], "ok")

    def test_parent_raises_same_kind_as_direct_runtime(self):
        runtime = fakes.FakeRuntime()
        expected = {"timeout": (TimeoutError, "generation_timeout"), "input": (fakes.InputFailure, "bad input"),
                    "model": (fakes.ModelFailure, "bad model"), "value": (ValueError, "rule_v3_bad_value"),
                    "value_text": (ValueError, "invalid_job_input"),
                    "other": (GenerationUnavailable, "generation_failed")}
        for action, (kind, message) in expected.items():
            with self.subTest(action):
                if action != "other":  # ValueError·번들 예외는 직접 실행과 같은 종류, 그 밖만 generation_failed로 묶는다
                    with self.assertRaises(kind) as direct:
                        runtime.run_field({"raise": action}, "o")
                    self.assertIs(type(direct.exception), kind)
                with self.assertLogs("generation_replicas", "ERROR") if action == "other" else _nothing():
                    reply = replicas._reply(runtime, {"raise": action}, "o")
                pool, replica = proxy(FakeConnection([reply]))
                with self.assertRaises(kind) as caught:
                    replica.run_field({"raise": action}, Path("o"))
                self.assertIs(type(caught.exception), kind)
                self.assertEqual(str(caught.exception), message)
                self.assertFalse(replica.lost, "field error keeps the replica")
                self.assertEqual(pool.ready(), [replica])

    def test_value_error_is_terminal_like_direct_run(self):
        from trainer.runner import _terminal_error_code
        runtime = fakes.FakeRuntime()
        for action in ("value", "value_text"):
            with self.subTest(action):
                with self.assertRaises(ValueError) as direct:
                    runtime.run_field({"raise": action}, "o")
                _, replica = proxy(FakeConnection([replicas._reply(runtime, {"raise": action}, "o")]))
                with self.assertRaises(ValueError) as relayed:
                    replica.run_field({"raise": action}, Path("o"))
                self.assertEqual(_terminal_error_code(relayed.exception), _terminal_error_code(direct.exception))

    def test_adapter_mismatch_with_broken_pipe_still_logs(self):
        class BrokenConnection(FakeConnection):
            closed = False

            def send(self, message):
                raise BrokenPipeError()

            def close(self):
                self.closed = True

        class StartedProcess(FakeProcess):
            def start(self):
                pass

        conn = BrokenConnection([("ready", "b" * 64)])
        child = BrokenConnection()
        context = type("Context", (), {"Pipe": lambda self, duplex=True: (conn, child),
                                       "Process": lambda self, **kwargs: StartedProcess()})()
        pool = replicas.ReplicaPool(["1"], ["a", "b", "c"], fakes.SHA, fakes.PACKAGE, start_seconds=1, context=context)
        with self.assertLogs("generation_replicas", "ERROR") as logs:
            pool._start_one("1")
        self.assertEqual(logs.output, ["ERROR:generation_replicas:rule_v3_replica_disabled: 1 rule_v3_adapter_mismatch"])
        self.assertTrue(conn.closed)
        self.assertEqual(pool.ready(), [])

    def test_result_values_survive(self):
        runtime = fakes.FakeRuntime()
        pool, replica = proxy(FakeConnection([replicas._reply(runtime, {}, "o")]))
        result = replica.run_field({}, Path("o"))
        self.assertEqual(result["result"]["value"], Decimal("1.50"))
        self.assertEqual(str(result["result"]["value"]), "1.50")

    def test_broken_pipe_or_dead_child_disables_replica(self):
        for name, connection, process in (("eof", FakeConnection([EOFError()]), FakeProcess()),
                                          ("dead", FakeConnection(ready=False), FakeProcess(alive=False)),
                                          ("garbage", FakeConnection([("what",)]), FakeProcess())):
            with self.subTest(name):
                pool, replica = proxy(connection, process)
                with self.assertLogs("generation_replicas", "ERROR") as logs:
                    with self.assertRaisesRegex(GenerationUnavailable, "generation_failed") as caught:
                        replica.run_field({}, Path("o"))
                if name == "garbage":  # 규약 위반은 기존대로 작업 실패(다시 돌리지 않음)
                    self.assertNotIsInstance(caught.exception, replicas.ReplicaUnavailable)
                else:  # 자식 종료: 다른 복제본에서만 다시 돌릴 수 있다
                    self.assertIsInstance(caught.exception, replicas.ReplicaUnavailable)
                    self.assertFalse(caught.exception.rerun_on_main)
                self.assertTrue(replica.lost)
                self.assertFalse(process.alive)
                self.assertEqual(pool.ready(), [])
                code = "replica_protocol_error" if name == "garbage" else "replica_exited"
                self.assertEqual(pool.disabled, {"1": code})
                self.assertEqual(logs.output, [f"ERROR:generation_replicas:rule_v3_replica_disabled: 1 {code}"])
                with self.assertRaisesRegex(GenerationUnavailable, "generation_failed") as again:
                    replica.run_field({}, Path("o"))
                self.assertTrue(again.exception.rerun_on_main, "a lost replica did not run this group")

    def test_out_of_memory_disables_and_stops_replica(self):
        runtime = fakes.FakeRuntime()
        with self.assertLogs("generation_replicas", "ERROR"):
            reply = replicas._reply(runtime, {"raise": "oom"}, "o")
        connection, process = FakeConnection([reply]), FakeProcess()
        pool, replica = proxy(connection, process)
        with self.assertLogs("generation_replicas", "ERROR") as logs:
            with self.assertRaises(replicas.ReplicaUnavailable) as caught:
                replica.run_field({}, Path("o"))
        self.assertEqual(str(caught.exception), "generation_failed")  # 받을 곳이 없으면 기존 생성 장애와 같다
        self.assertTrue(caught.exception.rerun_on_main)
        self.assertEqual(logs.output, ["ERROR:generation_replicas:rule_v3_replica_disabled: 1 replica_out_of_memory"])
        self.assertEqual(pool.disabled, {"1": "replica_out_of_memory"})
        self.assertEqual(pool.ready(), [])
        self.assertEqual(connection.sent[-1], ("stop",))
        self.assertFalse(process.alive, "process ended so its GPU memory is released")


class GateTests(unittest.TestCase):
    """적재 전 여유 메모리 검사(자식 본체를 이 프로세스에서 직접 부른다)."""

    def serve(self, min_free_mb, probe):
        from unittest.mock import patch
        loaded = []

        def loader(roots):
            loaded.append(roots)
            return fakes.FakeRuntime()
        connection = FakeConnection([EOFError()])
        with patch.dict("os.environ", {"CUDA_VISIBLE_DEVICES": "0"}):
            replicas.serve_replica(connection, "4", loader, ["a"], min_free_mb, probe)
        return connection.sent, loaded

    def test_low_free_memory_skips_loading(self):
        with self.assertLogs("generation_replicas", "ERROR") as logs:
            sent, loaded = self.serve(20480, fakes.free_mb)
        self.assertEqual((sent, loaded), ([("failed", "replica_insufficient_memory")], []))
        self.assertEqual(logs.output, ["ERROR:generation_replicas:rule_v3_replica_free_memory: 4 1000 MiB < 20480 MiB"])

    def test_probe_failure_or_disabled_gate(self):
        def broken():
            raise RuntimeError("no device")
        with self.assertLogs("generation_replicas", "ERROR"):
            self.assertEqual(self.serve(20480, broken), ([("failed", "replica_memory_check_failed")], []))
        self.assertEqual(self.serve(0, broken), ([("ready", fakes.SHA)], [["a"]]))
        self.assertEqual(self.serve(1000, fakes.free_mb), ([("ready", fakes.SHA)], [["a"]]))

    def test_threshold_from_env(self):
        from unittest.mock import patch
        for value, expected in ((None, 20480), ("0", 0), ("16384", 16384), ("x", 20480), ("-1", 20480)):
            with self.subTest(value):
                env = {} if value is None else {replicas.ENV_MIN_FREE_MB: value}
                with patch.dict("os.environ", env):
                    if value is None:
                        os.environ.pop(replicas.ENV_MIN_FREE_MB, None)
                    self.assertEqual(replicas._min_free_mb(), expected)


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class SpawnTests(unittest.TestCase):
    """실제 spawn 자식 5개(가짜 적재기·가짜 여유 메모리): 준비·적재 실패·어댑터 불일치·여유 메모리 부족, 왕복,
    생성 중 메모리 부족, 자식 종료."""

    def test_pool_round_trip(self):
        pool = replicas.ReplicaPool(["1", "2", "3", "4", "5"], ["a", "b", "c"], fakes.SHA, fakes.PACKAGE,
                                    loader=fakes.load, start_seconds=120, min_free_mb=20480, probe=fakes.free_mb)
        self.addCleanup(pool.close)
        with self.assertLogs("generation_replicas", "ERROR") as logs:
            pool.start()
            pool.wait_started(180)
        self.assertEqual(sorted(logs.output), [
            "ERROR:generation_replicas:rule_v3_replica_disabled: 2 qwen_base_metadata_mismatch",
            "ERROR:generation_replicas:rule_v3_replica_disabled: 3 rule_v3_adapter_mismatch",
            "ERROR:generation_replicas:rule_v3_replica_disabled: 4 replica_insufficient_memory"])
        self.assertEqual(pool.disabled, {"2": "qwen_base_metadata_mismatch", "3": "rule_v3_adapter_mismatch",
                                         "4": "replica_insufficient_memory"})
        replica, spare = pool.ready()
        self.assertEqual((replica.gpu, replica.adapter_sha, spare.gpu), ("1", fakes.SHA, "5"))
        # 생성 중 메모리 부족: 그 복제본만 꺼지고 프로세스가 끝난다. 나머지는 계속 쓴다
        with self.assertLogs("generation_replicas", "ERROR") as logs:
            with self.assertRaises(replicas.ReplicaUnavailable) as caught:
                spare.run_field({"raise": "oom"}, Path("out"))
        self.assertTrue(caught.exception.rerun_on_main)
        self.assertEqual(logs.output, ["ERROR:generation_replicas:rule_v3_replica_disabled: 5 replica_out_of_memory"])
        self.assertFalse(spare._process.is_alive())
        self.assertEqual(pool.ready(), [replica])
        result = replica.run_field({"application": Path("x.csv")}, Path("out"))
        # 자식은 자기 GPU 하나만 본다. Decimal은 표기까지 그대로 온다.
        self.assertEqual(result["result"]["gpu"], "1")
        self.assertEqual(str(result["result"]["value"]), "1.50")
        self.assertEqual(result["attempts"], [{"adapterSha": fakes.SHA}])
        with self.assertRaisesRegex(fakes.InputFailure, "bad input"):
            replica.run_field({"raise": "input"}, Path("out"))
        self.assertEqual(pool.ready(), [replica])
        with self.assertLogs("generation_replicas", "ERROR"):
            with self.assertRaisesRegex(GenerationUnavailable, "generation_failed"):
                replica.run_field({"raise": "exit"}, Path("out"))
        self.assertEqual(pool.ready(), [])
        self.assertEqual(pool.disabled["1"], "replica_exited")


if __name__ == "__main__":
    unittest.main()

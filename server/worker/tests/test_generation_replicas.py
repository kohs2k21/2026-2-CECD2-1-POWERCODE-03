"""Rule v3 GPU 복제본(`generation_replicas`) 시험. torch·GPU 없이 가짜 적재기로 확인한다.

필드 분배·결과 순서·진행률·실패 선택은 실제 번들 export가 필요해 `test_rule_v3.py` 병렬 절에 있다.
"""
from __future__ import annotations

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
                 "model": ("error", "model", "bad model")}
        for action, expected in cases.items():
            with self.subTest(action):
                self.assertEqual(replicas._reply(runtime, {"raise": action}, "o"), expected)
        with self.assertLogs("generation_replicas", "ERROR"):
            self.assertEqual(replicas._reply(runtime, {"raise": "other"}, "o"), ("error", "failed", "KeyError"))
        ok = replicas._reply(runtime, {}, "o")
        self.assertEqual(ok[0], "ok")

    def test_parent_raises_same_kind_as_direct_runtime(self):
        runtime = fakes.FakeRuntime()
        expected = {"timeout": (TimeoutError, "generation_timeout"), "input": (fakes.InputFailure, "bad input"),
                    "model": (fakes.ModelFailure, "bad model"), "other": (GenerationUnavailable, "generation_failed")}
        for action, (kind, message) in expected.items():
            with self.subTest(action):
                if action != "other":  # 기타 예외만 직접 실행과 다르게 generation_failed로 묶는다
                    with self.assertRaises(kind):
                        runtime.run_field({"raise": action}, "o")
                with self.assertLogs("generation_replicas", "ERROR") if action == "other" else _nothing():
                    reply = replicas._reply(runtime, {"raise": action}, "o")
                pool, replica = proxy(FakeConnection([reply]))
                with self.assertRaises(kind) as caught:
                    replica.run_field({"raise": action}, Path("o"))
                self.assertIs(type(caught.exception), kind)
                self.assertEqual(str(caught.exception), message)
                self.assertFalse(replica.lost, "field error keeps the replica")
                self.assertEqual(pool.ready(), [replica])

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
                    with self.assertRaisesRegex(GenerationUnavailable, "generation_failed"):
                        replica.run_field({}, Path("o"))
                self.assertTrue(replica.lost)
                self.assertEqual(pool.ready(), [])
                code = "replica_protocol_error" if name == "garbage" else "replica_exited"
                self.assertEqual(pool.disabled, {"1": code})
                self.assertEqual(logs.output, [f"ERROR:generation_replicas:rule_v3_replica_disabled: 1 {code}"])
                with self.assertRaisesRegex(GenerationUnavailable, "generation_failed"):
                    replica.run_field({}, Path("o"))


class _nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class SpawnTests(unittest.TestCase):
    """실제 spawn 자식 3개(가짜 적재기): 준비·적재 실패·어댑터 불일치, 왕복, 자식 종료."""

    def test_pool_round_trip(self):
        pool = replicas.ReplicaPool(["1", "2", "3"], ["a", "b", "c"], fakes.SHA, fakes.PACKAGE,
                                    loader=fakes.load, start_seconds=120)
        self.addCleanup(pool.close)
        with self.assertLogs("generation_replicas", "ERROR") as logs:
            pool.start()
            pool.wait_started(180)
        self.assertEqual(sorted(logs.output), [
            "ERROR:generation_replicas:rule_v3_replica_disabled: 2 qwen_base_metadata_mismatch",
            "ERROR:generation_replicas:rule_v3_replica_disabled: 3 rule_v3_adapter_mismatch"])
        self.assertEqual(pool.disabled, {"2": "qwen_base_metadata_mismatch", "3": "rule_v3_adapter_mismatch"})
        [replica] = pool.ready()
        self.assertEqual((replica.gpu, replica.adapter_sha), ("1", fakes.SHA))
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

"""룰 세트 v3 판정 7단계(protocol-rules.md 10절): 재판정 탐색의 DB 없는 부분 — 대상 기록·기간·사전 검사(6.2)·재판정
manifest hash(6.3)·재판정 시도의 readiness.relation 확인. DB 경로(커서·1회 보장·작업 생성)는 integration_relation_recheck.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from detector import recheck_store  # noqa: E402
from detector.attempt_v04 import recheck_reference  # noqa: E402
from detector.recheck_store import _Search  # noqa: E402
from detector.decision import decide  # noqa: E402
from detector.jobs import recheck_manifest_hash  # noqa: E402
from detector.operational_state import pin_operational_state  # noqa: E402
from detector.recheck import (OVERLAP_SECONDS, RECHECK_LOCK_CLASS, SETTLE_SECONDS, WINDOW_SECONDS,  # noqa: E402
                              awaiting_transaction, ordered_unique, precheck, relation_dependent, within_window)
from detector.relation_inputs import TRANSACTION_SETTLE_SECONDS  # noqa: E402
from detector.relation_plan import plan_relations  # noqa: E402
from test_detection_attempt import BASE_HARD, FakeConn, hard, v3  # noqa: E402
from test_relation_decision import FAILED_SEVERITY, FOCUS, POLICY, RAW, inputs, process_row, run, tx_row  # noqa: E402
from test_relation_rules import OWN, REQUIRED_ID, predecessor, relation  # noqa: E402

DECIDED = datetime(2026, 11, 13, 1, 0, tzinfo=timezone.utc)
SOURCE = "00000000-0000-4000-8000-0000000000aa"
DECISION = "00000000-0000-4000-8000-0000000000dd"
TX_OBS = "00000000-0000-4000-8000-000000000090"


def state_with(*relation_rules, extra_hard=()):
    return pin_operational_state(FakeConn(content=v3(BASE_HARD + list(extra_hard), relation_rules=list(relation_rules)),
                                          version=3))


def processes(focus_end="2026-01-01 00:00:09"):
    """판정 PROCESS(focus)가 가장 늦게 끝난 거래: 선행 PR001 + 판정 PR002."""
    return [{**process_row(), "endTimeRaw": focus_end}, predecessor(endTimeRaw="2026-01-01 00:00:01")]


def first_decision(state, raw=None):
    """CSV 재생 첫 판정: 거래 행 도착 확정 전(관계 기록은 보류·deferred_to_transaction)."""
    raw = {**RAW, **OWN, **(raw or {})}
    plan = plan_relations(state, inputs(tx_row("S", "0000"), processes=processes(), confirmed=False), raw)
    methods = run(state, plan, raw)
    return methods, decide(POLICY, methods)["composite"], [dict(item) for item in plan.notes]


def arrived(state, transaction=None, rows=None, raw=None):
    """도착 확정 뒤 같은 판정 관측의 사전 검사 입력."""
    return plan_relations(state, inputs(transaction or tx_row("S", "0000"), processes=rows or processes()),
                          {**RAW, **OWN, **(raw or {})})


class Constants(unittest.TestCase):
    def test_contract_values(self):
        self.assertEqual((RECHECK_LOCK_CLASS, WINDOW_SECONDS, OVERLAP_SECONDS), (81742, 86400, 60))
        # 탐색 최소 지연과 시도의 도착 확정은 같은 상수(relation-rules.md 3.4·6.1).
        self.assertIs(SETTLE_SECONDS, TRANSACTION_SETTLE_SECONDS)


class Targets(unittest.TestCase):
    def test_awaiting_results_are_held_and_deferred(self):
        def notes(*results):
            return [{"ruleId": None, "kind": "precedes", "result": result, "reason": None, "waitingFor": None}
                    for result in results]

        self.assertTrue(awaiting_transaction(notes("held")))
        self.assertTrue(awaiting_transaction(notes("observed", "deferred_to_transaction")))
        for value in (notes("observed", "not_applicable", "not_in_pattern"), [], None, {"result": "held"}, ["held"]):
            self.assertFalse(awaiting_transaction(value), value)

    def test_window_starts_at_decision(self):
        self.assertTrue(within_window(DECIDED - timedelta(hours=1), DECIDED))  # 거래 행이 판정보다 먼저(CSV 재생)
        self.assertTrue(within_window(DECIDED + timedelta(seconds=WINDOW_SECONDS), DECIDED))
        self.assertFalse(within_window(DECIDED + timedelta(seconds=WINDOW_SECONDS, microseconds=1), DECIDED))

    def test_relation_dependent_methods(self):
        for method_id in ("hard:transaction-failed", "hard:unregistered-interface", "hard:rel-0000000a",
                          "hard:proto-0000000a"):
            self.assertTrue(relation_dependent(method_id), method_id)
        for method_id in ("hard:process-status", "soft:elapsed_ms-3f9a2c1d", "ml:iforest", "hard:rel-0000000A"):
            self.assertFalse(relation_dependent(method_id), method_id)

    def test_ordered_unique_keeps_first_order(self):
        self.assertEqual(ordered_unique(["b", "a", "b", "c", "a"]), ["b", "a", "c"])


class Prechecks(unittest.TestCase):
    def test_missing_required_step_changes_composite(self):
        state = state_with(relation(REQUIRED_ID, "required_step", **{"from": {"processId": "IF_A_PR009"}}))
        methods, composite, notes = first_decision(state)
        self.assertEqual([item["result"] for item in notes], ["deferred_to_transaction"])
        self.assertEqual(composite["grade"], "normal")
        checked = precheck(state, arrived(state), methods, composite)
        self.assertEqual((checked.target, checked.reason), (True, "composite_changed"))

    def test_present_required_step_is_not_rechecked(self):
        state = state_with(relation(REQUIRED_ID, "required_step"))  # 필수 단계 PR001이 있다 → N
        methods, composite, _ = first_decision(state)
        checked = precheck(state, arrived(state), methods, composite)
        self.assertEqual((checked.target, checked.reason), (False, "open_result_absent"))

    def test_not_latest_ended_process_has_no_open_result(self):
        """붙이는 판정(가장 늦게 끝난 PROCESS)이 아닌 판정은 required_step을 적용 안 함 → 대상 아님."""
        state = state_with(relation(REQUIRED_ID, "required_step", **{"from": {"processId": "IF_A_PR009"}}))
        methods, composite, _ = first_decision(state)
        early = arrived(state, rows=processes(focus_end="2026-01-01 00:00:00"))
        self.assertEqual(precheck(state, early, methods, composite).reason, "open_result_absent")

    def test_same_composite_is_not_rechecked(self):
        """이미 같은 근거로 이상인 판정(예: 같은 방법 T)은 다시 평가해도 종합이 같다."""
        state = state_with(relation(REQUIRED_ID, "required_step", **{"from": {"processId": "IF_A_PR009"}}))
        raw = {**RAW, **OWN}
        plan = arrived(state)
        methods = run(state, plan, raw)
        composite = decide(POLICY, methods)["composite"]
        self.assertEqual(composite["basisMethods"], [REQUIRED_ID])
        checked = precheck(state, plan, methods, composite)
        self.assertEqual((checked.target, checked.reason), (False, "composite_unchanged"))

    def test_held_precedes_becomes_violation(self):
        """선행 행 없음: 첫 판정 held → 도착 확정 뒤 T(predecessor_missing)."""
        state = state_with(relation())
        raw = {**RAW, **OWN}
        alone = [{**process_row(), "endTimeRaw": "2026-01-01 00:00:09"}]
        first = plan_relations(state, inputs(tx_row("S", "0000"), processes=alone, confirmed=False), raw)
        self.assertEqual([item["result"] for item in first.notes], ["held"])
        methods = run(state, first, raw)
        checked = precheck(state, arrived(state, rows=alone), methods, decide(POLICY, methods)["composite"])
        self.assertEqual((checked.target, checked.reason), (True, "composite_changed"))

    def test_transaction_pending_becomes_failure(self):
        state = state_with(extra_hard=[hard("hard:transaction-failed", True, FAILED_SEVERITY)])
        raw = {**RAW, **OWN}
        first = plan_relations(state, inputs(None), raw)
        self.assertEqual(first.notes[0]["reason"], "transaction_pending")
        methods = run(state, first, raw)
        composite = decide(POLICY, methods)["composite"]
        failed = precheck(state, plan_relations(state, inputs(tx_row("F", "E901")), raw), methods, composite)
        self.assertEqual((failed.target, failed.reason), (True, "composite_changed"))
        succeeded = precheck(state, plan_relations(state, inputs(tx_row("S", "0000")), raw), methods, composite)
        self.assertEqual((succeeded.target, succeeded.reason), (False, "open_result_absent"))
        # 방법 결과 보류(데이터 문제)도 N이 아니다 — hard 대분류를 판단 불가로 만들어 종합이 바뀐다.
        missing = precheck(state, plan_relations(state, inputs(tx_row(None, None)), raw), methods, composite)
        self.assertEqual((missing.target, missing.reason), (True, "composite_changed"))

    def test_unreadable_old_evidence_is_not_filtered_out(self):
        state = state_with(relation(REQUIRED_ID, "required_step", **{"from": {"processId": "IF_A_PR009"}}))
        methods, composite, _ = first_decision(state)
        for old_methods, old_composite in ((methods, None), (methods, {"grade": "normal"}), (None, composite),
                                           ([{"methodId": "hard:unknown-rule"}], composite),
                                           (methods, {**composite, "basisMethods": [{"methodId": "x"}]}),
                                           (methods, {**composite, "grade": ["normal"]}),
                                           ([{"methodId": "soft:elapsed_ms-3f9a2c1d", "category": "soft"}], composite)):
            checked = precheck(state, arrived(state), old_methods, old_composite)
            self.assertEqual((checked.target, checked.reason), (True, "precheck_unavailable"))


class StoreCandidate(unittest.TestCase):
    """판정 하나의 데이터 모양 이상은 그 판정만 precheck_failed로 넘기고, 운영 상태 고정 실패는 회차 전체를 되돌린다."""

    def search(self, state):
        found = _Search(None, SOURCE, DECIDED, WINDOW_SECONDS)
        found.state = state
        return found

    def candidate(self, found, raw):
        found.candidate(DECISION, FOCUS, 1, raw, DECIDED, [], {}, DECIDED,
                        [{"observation_id": TX_OBS, "revision_no": 1}])

    def test_bad_decision_is_skipped_not_fatal(self):
        state = state_with(relation(REQUIRED_ID, "required_step"))
        found = self.search(state)

        def broken_inputs(*_args):
            raise AttributeError("unexpected row shape")

        with patch.object(recheck_store, "recheck_manifest_exists", lambda *_: False), \
                patch.object(recheck_store, "read_relation_inputs", broken_inputs), \
                patch.object(recheck_store, "enqueue_recheck", lambda *_: self.fail("must not enqueue")):
            self.candidate(found, ["not", "an", "object"])
            self.candidate(found, None)
            self.candidate(found, {**RAW, **OWN})
        self.assertEqual(dict(found.skipped), {"precheck_failed:TypeError": 2, "precheck_failed:AttributeError": 1})
        self.assertEqual(found.outcome.enqueued, 0)

    def test_failure_code_keeps_only_code_shaped_messages(self):
        code = recheck_store.precheck_failure_code
        self.assertEqual(code(ValueError("relation_focus_unavailable")), "relation_focus_unavailable")
        self.assertEqual(code(ValueError("invalid_relation_rule:abc_1")), "invalid_relation_rule:abc_1")
        # 데이터 값이 섞일 수 있는 문구(공백·대문자·따옴표·하이픈 등)는 예외 이름만 남긴다.
        for message in ("invalid literal for int() with base 10: 'X1'", "Bad-Code", "code: 4", ""):
            self.assertEqual(code(ValueError(message)), "ValueError")
        self.assertEqual(code(KeyError("relation_focus_unavailable")), "KeyError")
        self.assertEqual(code(TypeError("unexpected")), "TypeError")
        self.assertEqual(len(code(ValueError("a" * 80))), 60)

    def test_pinning_failure_aborts_the_run(self):
        found = self.search(None)

        def unavailable(_conn):
            raise ValueError("operational_state_unavailable")

        with patch.object(recheck_store, "recheck_manifest_exists", lambda *_: False), \
                patch.object(recheck_store, "pin_operational_state", unavailable):
            with self.assertRaisesRegex(ValueError, "^operational_state_unavailable$"):
                self.candidate(found, {**RAW, **OWN})


class ManifestHash(unittest.TestCase):
    def test_hash_input_has_no_related_rows(self):
        expected = hashlib.sha256(json.dumps(["process-input-v2-recheck", SOURCE, FOCUS, 3, "recheck:1"],
                                             separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(recheck_manifest_hash(SOURCE, FOCUS, 3), expected)
        self.assertEqual(recheck_manifest_hash(uuid.UUID(SOURCE), uuid.UUID(FOCUS), 3), expected)
        self.assertNotEqual(recheck_manifest_hash(SOURCE, FOCUS, 4), expected)
        for revision in (None, "3", True, 3.0):
            with self.assertRaisesRegex(ValueError, "^detect_input_unavailable$"):
                recheck_manifest_hash(SOURCE, FOCUS, revision)


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.conn.queries.append(sql)

    def fetchone(self):
        return self.conn.decision

    def fetchall(self):
        return self.conn.rows


class _Conn:
    def __init__(self, decision=(FOCUS,), rows=None):
        self.decision = decision
        self.rows = [(uuid.UUID(TX_OBS), "T", uuid.UUID(SOURCE), 1, True, True)] if rows is None else rows
        self.queries: list[str] = []

    def cursor(self):
        return _Cursor(self)


class RecheckReference(unittest.TestCase):
    observation = SimpleNamespace(source_id=uuid.UUID(SOURCE), observation_id=uuid.UUID(FOCUS), revision_no=1)

    def fixed(self, relation=None, *, digest=None, readiness=True):
        value = {"process": "ready", "transaction": "unknown", "messages": "unknown", "body": "not_collected"}
        if relation is not None:
            value["relation"] = relation
        return ({}, None, uuid.uuid4(), digest or recheck_manifest_hash(SOURCE, FOCUS, 1), DECIDED,
                *((value,) if readiness else ()))

    @staticmethod
    def relation(**changes):
        return {"recheck": 1, "recheckedDecisionId": DECISION,
                "rows": [{"layer": "T", "observation_id": TX_OBS, "revision_no": 1}], **changes}

    def test_first_judgment_has_no_reference(self):
        conn = _Conn()
        first = "a" * 64  # process-input-v1 manifest hash 자리(재판정 hash가 아님)
        self.assertIsNone(recheck_reference(conn, self.observation, self.fixed(digest=first, readiness=False)))
        self.assertIsNone(recheck_reference(conn, self.observation, self.fixed(digest=first)))
        self.assertEqual(conn.queries, [])

    def test_recheck_hash_without_relation_fails_closed(self):
        """재판정 manifest인데 readiness.relation이 없으면(또는 readiness를 못 읽으면) 첫 판정으로 돌리지 않는다."""
        for fixed in (self.fixed(), self.fixed(readiness=False)):
            with self.assertRaisesRegex(ValueError, "^relation_recheck_reference_invalid$"):
                recheck_reference(_Conn(), self.observation, fixed)

    def test_valid_reference(self):
        conn = _Conn()
        self.assertEqual(recheck_reference(conn, self.observation, self.fixed(self.relation())),
                         {"recheckedDecisionId": DECISION, "transactionObservationId": TX_OBS})

    def test_malformed_or_mismatched_reference_fails_closed(self):
        bad_shapes = [self.relation(recheck=2), self.relation(recheck=True), self.relation(extra=1),
                      self.relation(recheckedDecisionId=DECISION.upper()), self.relation(rows=[]),
                      self.relation(rows=[{"layer": "P", "observation_id": TX_OBS, "revision_no": 1}]),
                      self.relation(rows=[{"layer": "T", "observation_id": TX_OBS, "revision_no": "1"}]),
                      self.relation(rows=[{"layer": "T", "observation_id": TX_OBS}]), "relation"]
        for relation in bad_shapes:
            with self.assertRaisesRegex(ValueError, "^relation_recheck_reference_invalid$"):
                recheck_reference(_Conn(), self.observation, self.fixed(relation))
        mismatched = {
            "not recheck hash": (_Conn(), {"digest": "f" * 64}),
            "decision of another observation": (_Conn(decision=(uuid.uuid4(),)), {}),
            "decision missing": (_Conn(decision=None), {}),
            "observation missing": (_Conn(rows=[(uuid.UUID(TX_OBS), None, None, None, None, None)]), {}),
            "other layer": (_Conn(rows=[(uuid.UUID(TX_OBS), "P", uuid.UUID(SOURCE), 1, True, True)]), {}),
            "other source": (_Conn(rows=[(uuid.UUID(TX_OBS), "T", uuid.uuid4(), 1, True, True)]), {}),
            "other revision": (_Conn(rows=[(uuid.UUID(TX_OBS), "T", uuid.UUID(SOURCE), 2, True, True)]), {}),
            "after input_as_of": (_Conn(rows=[(uuid.UUID(TX_OBS), "T", uuid.UUID(SOURCE), 1, False, True)]), {}),
            "other transaction": (_Conn(rows=[(uuid.UUID(TX_OBS), "T", uuid.UUID(SOURCE), 1, True, False)]), {}),
        }
        for name, (conn, options) in mismatched.items():
            with self.subTest(name), self.assertRaisesRegex(ValueError, "^relation_recheck_reference_invalid$"):
                recheck_reference(conn, self.observation, self.fixed(self.relation(), **options))


if __name__ == "__main__":
    unittest.main()

"""룰 세트 v3 판정 2~4단계(protocol-rules.md 10절)의 DB 없는 부분.

관계 입력(evidence relationInputs), 내장 하드룰 hard:transaction-failed, 과제 사전 분류(relationNotes), 하드룰 순서·
대조 집합. 관계 규칙 판정(5단계)은 test_relation_rules, 프로토콜 판정(6단계)은 test_protocol_rules. 재판정(7단계)은
아직 없다.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from detector import attempt_v04  # noqa: E402
from detector.decision import decide  # noqa: E402
from detector.evidence_v4 import PinnedRevisions, build_evidence  # noqa: E402
from detector.hard_rules import (COUNT_CONSERVATION, HARD_RULES, NEGATIVE_DURATION, PROCESS_STATUS,  # noqa: E402
                                 SUCCESS_WITH_ERROR, TRANSACTION_FAILED, UNREGISTERED_INTERFACE,
                                 evaluate_transaction_failed, hard_rule_runners, read_hard_rule_snapshot)
from detector.method_results import method_result, with_elapsed  # noqa: E402
from detector.method_runner import execute_methods  # noqa: E402
from detector.operational_state import pin_operational_state  # noqa: E402
from detector.policy import parse_policy_v2  # noqa: E402
from detector.relation_inputs import RelationInputs, read_relation_inputs  # noqa: E402
from detector.relation_plan import needs_relation_inputs, plan_relations  # noqa: E402
from test_detection_attempt import BASE_HARD, FakeConn, SOFT, V2_HARD_RULES, V3_VECTORS, hard, snapshot, v3  # noqa: E402

POLICY = parse_policy_v2({"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1},
                          "deadlineMs": 3000})
REVISIONS = PinnedRevisions(3, "a" * 64, 0, 0)
READ_AT = datetime(2026, 11, 13, tzinfo=timezone.utc)
FOCUS = "00000000-0000-4000-8000-000000000001"
TX_OBS = "00000000-0000-4000-8000-000000000090"
FAILED_SEVERITY = {"byCode": {"E901": "critical"}, "default": "warning"}
RAW = {"status": "S", "start_time_raw": "2026-01-01 00:00:00", "end_time_raw": "2026-01-01 00:00:01",
       "total_count": "1", "success_count": "1", "error_count": "0"}


def tx_row(status="F", code="E901", observation=TX_OBS):
    return {"observationId": observation, "revisionNo": "2", "retryCount": "0", "transactionId": "TX-1",
            "interfaceId": "IF_A", "channelId": "CH_A", "endChannelId": "CH_B", "status": status,
            "responseCode": code}


def process_row(observation=FOCUS, process_id="IF_A_PR002", depends="IF_A_PR001", focus=True):
    return {"observationId": observation, "executionId": observation.replace("-0000-4000", "-1111-4000"),
            "revisionNo": "1", "transactionId": "TX-1", "transactionObservationId": None, "processId": process_id,
            "dependsOn": depends, "status": "S", "responseCode": "0000", "totalCount": "1", "retryCount": "0",
            "channelId": "CH_B", "adapterType": "HTTP", "processHubId": None, "focus": focus}


def inputs(transaction=None, *, executions=None, ambiguous=False, focus_tx="TX-1", processes=None, messages=(),
           truncated=False, confirmed=None):
    """confirmed(거래 행 도착 확정)는 기본으로 거래 행 실행이 있으면 참(최소 지연이 지난 상태)."""
    executions = (1 if transaction else 0) if executions is None else executions
    return RelationInputs(read_at=READ_AT, source_id="00000000-0000-4000-8000-0000000000aa",
                          focus_observation_id=FOCUS, focus_transaction_id=focus_tx,
                          transaction_executions=executions, transaction=transaction, transaction_ambiguous=ambiguous,
                          transaction_confirmed=executions >= 1 if confirmed is None else confirmed,
                          processes=tuple(processes if processes is not None else [process_row()]),
                          messages=tuple(messages), processes_truncated=truncated, messages_truncated=False)


def v3_state(*extra_hard, soft_rules=(), models=()):
    """r1과 같은 내장 4개 + v3 규칙(extra_hard)."""
    return pin_operational_state(FakeConn(content=v3(BASE_HARD + list(extra_hard), soft_rules=soft_rules),
                                          version=3, models=models))


def selection(severity=None):
    return read_hard_rule_snapshot([hard(TRANSACTION_FAILED, True, severity or FAILED_SEVERITY)])


def evidence_for(state, plan, methods):
    return build_evidence(POLICY, methods, REVISIONS, state.hard_rules, source_id=uuid.uuid4(), telemetry={},
                          observation={"id": FOCUS, "revisionNo": 1, "inputAsOf": READ_AT.isoformat()},
                          input_manifest_hash="b" * 64, evaluated_hard_ids=plan.hard_ids,
                          relation=plan.evidence_fields())


def run(state, plan, raw=RAW):
    return execute_methods(attempt_v04._tasks(state, [], raw, None, plan), 3000,
                           started_at=__import__("time").monotonic()).methods


class TransactionFailedSnapshot(unittest.TestCase):
    def test_v3_row_with_transaction_failed_pins(self):
        state = v3_state(hard(TRANSACTION_FAILED, True, FAILED_SEVERITY), hard(UNREGISTERED_INTERFACE, True, "info"))
        self.assertEqual(state.hard_rules.enabled_ids, (PROCESS_STATUS, NEGATIVE_DURATION, SUCCESS_WITH_ERROR,
                                                        COUNT_CONSERVATION, TRANSACTION_FAILED,
                                                        UNREGISTERED_INTERFACE))
        self.assertEqual(state.hard_rules.transaction_failed_level_items, (("E901", "critical"),))
        self.assertEqual(state.hard_rules.unregistered_interface_level, "info")
        self.assertTrue(needs_relation_inputs(state))

    def test_builtin_default_when_absent_and_level_lookup(self):
        default = read_hard_rule_snapshot([hard(PROCESS_STATUS, True, {"byCode": {
            "PROCESS_STATUS_F": "critical", "PROCESS_STATUS_P": "warning", "PROCESS_STATUS_N": "info",
            "PROCESS_STATUS_H": "info", "PROCESS_STATUS_Z": "warning"}}, methodVersion="process-status-hr-v2")])
        self.assertEqual((default.transaction_failed_level_items, default.transaction_failed_default), ((), "warning"))
        edited = selection({"byCode": {"E901": "critical", "0100": "info"}, "default": "info"})
        self.assertEqual([edited.transaction_failed_level(code) for code in ("E901", " E901\t", "0100", "X")],
                         ["critical", "critical", "info", "info"])

    def test_malformed_severity_or_version_fails(self):
        for severity in ({"byCode": {}}, {"default": "warning"}, {"byCode": {}, "default": "high"},
                         {"byCode": {"E1": "high"}, "default": "warning"}, {"byCode": [], "default": "warning"},
                         {"byCode": {}, "default": ["warning"]}, "critical"):
            with self.subTest(severity=severity):
                with self.assertRaisesRegex(ValueError, f"^invalid_hard_rule_severity:{TRANSACTION_FAILED}$"):
                    read_hard_rule_snapshot([hard(TRANSACTION_FAILED, True, severity)])
        for severity in ({"byCode": {}, "default": "warning"}, "high", None):
            with self.subTest(severity=severity):
                with self.assertRaisesRegex(ValueError, f"^invalid_hard_rule_severity:{UNREGISTERED_INTERFACE}$"):
                    read_hard_rule_snapshot([hard(UNREGISTERED_INTERFACE, True, severity)])
        with self.assertRaisesRegex(ValueError, f"^hard_rule_version_unknown:{TRANSACTION_FAILED}$"):
            read_hard_rule_snapshot([hard(TRANSACTION_FAILED, True, FAILED_SEVERITY, methodVersion="v0")])

    def test_v2_rows_still_reject_new_builtins(self):
        for item in (hard(TRANSACTION_FAILED, False, FAILED_SEVERITY), hard(UNREGISTERED_INTERFACE, False)):
            with self.subTest(rule=item["id"]):
                with self.assertRaisesRegex(ValueError, "^relation_rules_require_v3$"):
                    pin_operational_state(FakeConn(content=snapshot([*snapshot()["hardRules"], item])))

    def test_enabled_protocol_entries_pin(self):
        # 6단계: 켜진 프로토콜 항목은 더 이상 시도 실패가 아니다(rule_set_v3_unsupported 해제).
        content = v3(BASE_HARD + [hard(UNREGISTERED_INTERFACE)], protocol_rules=V3_VECTORS["d"]["protocolRules"])
        state = pin_operational_state(FakeConn(content=content, version=3))
        self.assertEqual([rule.id for rule in state.protocol_rules], ["hard:proto-5c1e9a07"])
        self.assertEqual(state.hard_rules.protocol_rule_levels, (("hard:proto-5c1e9a07", "critical"),))


class TransactionFailedJudgment(unittest.TestCase):
    """relation-rules.md 7.2 표 행마다 1건."""

    def judge(self, relation_inputs, severity=None):
        return evaluate_transaction_failed(relation_inputs, selection(severity))

    def test_failed_transaction_is_anomaly_with_code_level(self):
        result = self.judge(inputs(tx_row("F", "E901")))
        self.assertEqual((result["status"], result["anomalous"], result["code"], result["riskLevel"]),
                         ("succeeded", True, "HARD_TRANSACTION_FAILED", "critical"))
        self.assertEqual(result["evidence"], [{"result": "hit", "transactionObservationId": TX_OBS,
                                               "responseCode": "E901"}])
        other = self.judge(inputs(tx_row("F", "E902")))
        self.assertEqual((other["anomalous"], other["riskLevel"]), (True, "warning"))
        self.assertEqual(self.judge(inputs(tx_row(" F ", "E902")))["anomalous"], True)

    def test_excluded_codes_and_other_status_are_normal(self):
        for status, code in (("F", "0000"), ("F", " S "), ("F", None), ("F", "\\N"), ("F", " "), ("S", "E901"),
                             ("f", "E901"), ("P", "E901")):
            with self.subTest(status=status, code=code):
                result = self.judge(inputs(tx_row(status, code)))
                self.assertEqual((result["status"], result["anomalous"], result["code"]), ("succeeded", False, None))
                self.assertEqual(result["evidence"], [{"result": "no_hit", "transactionObservationId": TX_OBS}])

    def test_data_problems_are_method_holds(self):
        cases = {"transaction_status_missing": (inputs(tx_row(" \\N ")), "input_missing"),
                 "transaction_row_ambiguous": (inputs(None, executions=2, ambiguous=True), "input_invalid"),
                 "transaction_id_missing": (inputs(None, focus_tx=" "), "input_missing")}
        for reason, (relation_inputs, cause) in cases.items():
            with self.subTest(reason=reason):
                result = self.judge(relation_inputs)
                self.assertEqual((result["status"], result["anomalous"], result["cause"], result["evidence"]),
                                 ("not_ready", None, cause, [{"reason": reason}]))

    def test_no_transaction_row_is_not_a_task(self):
        with self.assertRaisesRegex(ValueError, "transaction_failed_without_transaction_row"):
            self.judge(inputs(None))


class Planning(unittest.TestCase):
    def test_v2_state_reads_nothing_and_keeps_all_builtins(self):
        state = pin_operational_state(FakeConn(content=snapshot(soft_rules=[SOFT])))
        plan = plan_relations(state, None)
        self.assertFalse(needs_relation_inputs(state))
        self.assertEqual(plan.hard_ids, state.hard_rules.enabled_ids)
        self.assertEqual(plan.evidence_fields(), {})

    def test_transaction_pending_is_a_note_not_a_method(self):
        state = v3_state(hard(TRANSACTION_FAILED, True, FAILED_SEVERITY), hard(UNREGISTERED_INTERFACE))
        plan = plan_relations(state, inputs(None))
        self.assertEqual(plan.hard_ids, (PROCESS_STATUS, NEGATIVE_DURATION, SUCCESS_WITH_ERROR, COUNT_CONSERVATION))
        self.assertEqual(plan.evidence_fields()["relationNotes"], [
            {"ruleId": TRANSACTION_FAILED, "kind": "transaction", "result": "held", "reason": "transaction_pending",
             "waitingFor": {"layer": "T"}}])

    def test_transaction_present_or_unkeyed_is_evaluated(self):
        state = v3_state(hard(TRANSACTION_FAILED, True, FAILED_SEVERITY))
        for relation_inputs in (inputs(tx_row()), inputs(None, focus_tx=None), inputs(None, executions=3,
                                                                                         ambiguous=True)):
            with self.subTest(transaction=relation_inputs.transaction, keyed=relation_inputs.transaction_keyed):
                plan = plan_relations(state, relation_inputs)
                self.assertEqual(plan.hard_ids[-1], TRANSACTION_FAILED)
                self.assertEqual(plan.notes, ())

    def test_unregistered_interface_without_protocol_entries_is_silent(self):
        state = v3_state(hard(UNREGISTERED_INTERFACE))
        self.assertFalse(needs_relation_inputs(state))
        plan = plan_relations(state, None)
        self.assertNotIn(UNREGISTERED_INTERFACE, plan.hard_ids)
        self.assertEqual(plan.evidence_fields(), {})

    def test_inputs_required_when_transaction_failed_enabled(self):
        with self.assertRaisesRegex(ValueError, "relation_inputs_required"):
            plan_relations(v3_state(hard(TRANSACTION_FAILED, True, FAILED_SEVERITY)), None)

    def test_runner_without_executor_fails_closed(self):
        state = v3_state(hard(UNREGISTERED_INTERFACE))
        with self.assertRaisesRegex(ValueError, f"^rule_set_v3_unsupported:{UNREGISTERED_INTERFACE}$"):
            hard_rule_runners(state.hard_rules, RAW, state.hard_rules.enabled_ids)
        with self.assertRaisesRegex(ValueError, f"^rule_set_v3_unsupported:{TRANSACTION_FAILED}$"):
            hard_rule_runners(selection(), RAW, (TRANSACTION_FAILED,), None)


class Assembly(unittest.TestCase):
    """판정 조립: 방법 순서·n, 중간/최종 n 일치, 대조 집합, evidence 기록."""

    def test_held_transaction_failed_leaves_n_and_evidence_consistent(self):
        state = v3_state(hard(TRANSACTION_FAILED, True, FAILED_SEVERITY), soft_rules=[{**SOFT, "scope": None}])
        plan = plan_relations(state, inputs(None))
        methods = run(state, plan)
        self.assertEqual([m["methodId"] for m in methods], [*plan.hard_ids, SOFT["id"]])
        evidence = evidence_for(state, plan, methods)
        self.assertEqual(evidence["categoryResults"]["hard"]["n"], 4)
        self.assertEqual(evidence["relationNotes"][0]["reason"], "transaction_pending")
        self.assertEqual(evidence["relationInputs"], {
            "readAt": READ_AT.isoformat(), "transaction": None,
            "processes": [{"observationId": FOCUS, "revisionNo": 1}], "messages": [], "truncated": False,
            "messagesTruncated": False})
        self.assertEqual(evidence["composite"]["grade"], "normal")

    def test_failed_transaction_decides_anomaly(self):
        state = v3_state(hard(TRANSACTION_FAILED, True, FAILED_SEVERITY))
        plan = plan_relations(state, inputs(tx_row()))
        evidence = evidence_for(state, plan, run(state, plan))
        self.assertEqual([m["methodId"] for m in evidence["methodResults"]][-1], TRANSACTION_FAILED)
        self.assertEqual(evidence["categoryResults"]["hard"]["n"], 5)
        self.assertEqual((evidence["composite"]["grade"], evidence["composite"]["basisMethods"],
                          evidence["composite"]["risk"]["level"]), ("anomaly", [TRANSACTION_FAILED], "critical"))
        self.assertEqual(evidence["relationNotes"], [])
        self.assertEqual(evidence["relationInputs"]["transaction"], {
            "executions": 1, "ambiguous": False, "arrivalConfirmed": True, "observationId": TX_OBS,
            "revisionNo": 2})
        # v3 투영 rules[]에도 내장 규칙으로 남는다(관계·프로토콜·미등록 인터페이스만 뺀다).
        self.assertEqual(evidence["rules"][-1], {"ruleKey": "transaction-failed", "version": "transaction-failed-v1",
                                                 "result": "hit"})
        self.assertEqual(evidence["ruleSeverities"], ["critical"])

    def test_v2_decision_has_no_relation_keys(self):
        state = pin_operational_state(FakeConn())
        plan = plan_relations(state, None)
        evidence = evidence_for(state, plan, run(state, plan))
        self.assertFalse({"relationInputs", "relationNotes"} & set(evidence))
        self.assertEqual([m["methodId"] for m in evidence["methodResults"]], [rule["id"] for rule in V2_HARD_RULES])

    def test_comparison_set_rejects_mismatch(self):
        state = v3_state(hard(TRANSACTION_FAILED, True, FAILED_SEVERITY))
        plan = plan_relations(state, inputs(tx_row()))
        methods = run(state, plan)
        held = plan_relations(state, inputs(None))
        cases = {
            "held rule still recorded": (held.hard_ids, methods),
            "non-conditional rule dropped": (plan.hard_ids[1:], methods[1:]),
            "unknown extra": (plan.hard_ids + ("hard:rel-00000001",), methods),
            "wrong level": (plan.hard_ids, [*methods[:-1], {**methods[-1], "severity": "info", "riskLevel": "info"}]),
        }
        for name, (evaluated, results) in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, "^hard_rule_selection_mismatch$"):
                    build_evidence(POLICY, results, REVISIONS, state.hard_rules, source_id=uuid.uuid4(),
                                   telemetry={}, observation={}, input_manifest_hash="b" * 64,
                                   evaluated_hard_ids=evaluated)

    def test_rank_orders_builtins_then_relation_then_protocol_keeping_stored_order(self):
        def result(method_id, category="hard"):
            return with_elapsed(method_result(method_id, "v1", "succeeded", False), 1.0)

        ids = ["soft:elapsed_ms-00000001", "hard:proto-0000000b", "hard:rel-ffffffff", TRANSACTION_FAILED,
               "hard:proto-0000000a", "hard:rel-00000001", PROCESS_STATUS]
        decided = decide(POLICY, [result(method_id) for method_id in ids])
        self.assertEqual([m["methodId"] for m in decided["methodResults"]],
                         [PROCESS_STATUS, TRANSACTION_FAILED, "hard:rel-ffffffff", "hard:rel-00000001",
                          "hard:proto-0000000b", "hard:proto-0000000a", "soft:elapsed_ms-00000001"])
        for bad in ("hard:rel-1234567", "hard:rel-ABCDEF12", "hard:proto-0000000g", "hard:other-rule",
                    "hard:rel-00000001\n"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ValueError, f"^(hard_rule_unknown|method_kind_not_allowed):{re.escape(bad)}$"):
                    decide(POLICY, [result(bad)])

    def test_projection_excludes_relation_protocol_and_unregistered(self):
        state = pin_operational_state(FakeConn())
        state = replace(state, hard_rules=replace(state.hard_rules, relation_rule_levels=(("hard:rel-00000001", "info"),),
                                                  protocol_rule_levels=(("hard:proto-00000001", "info"),)))
        methods = [with_elapsed(method_result(method_id, "v1", "succeeded", False), 1.0)
                   for method_id in state.hard_rules.enabled_ids]
        methods += [with_elapsed(method_result(method_id, "v1", "succeeded", True, code="REL", severity="info"), 1.0)
                    for method_id in ("hard:rel-00000001", "hard:proto-00000001")]
        evidence = build_evidence(POLICY, methods, REVISIONS, state.hard_rules, source_id=uuid.uuid4(),
                                  telemetry={}, observation={}, input_manifest_hash="b" * 64,
                                  evaluated_hard_ids=(*state.hard_rules.enabled_ids, "hard:rel-00000001",
                                                      "hard:proto-00000001"))
        self.assertEqual([rule["ruleKey"] for rule in evidence["rules"]],
                         ["process-status", "negative-duration", "success-with-error", "count-conservation"])
        self.assertEqual(evidence["ruleSeverities"], [])  # T는 관계·프로토콜뿐 — 투영에 없음
        self.assertEqual(evidence["composite"]["basisMethods"], ["hard:rel-00000001", "hard:proto-00000001"])
        self.assertEqual(evidence["categoryResults"]["hard"]["n"], 6)


class RelationInputRecord(unittest.TestCase):
    def test_evidence_and_graph(self):
        predecessor = process_row("00000000-0000-4000-8000-000000000002", "IF_A_PR001", "NONE", focus=False)
        relation_inputs = inputs(None, executions=2, ambiguous=True, processes=[predecessor, process_row()])
        self.assertEqual(relation_inputs.evidence()["transaction"],
                         {"executions": 2, "ambiguous": True, "arrivalConfirmed": True, "observationId": None,
                          "revisionNo": None})
        graph = relation_inputs.graph
        self.assertEqual(graph["graph"]["id"], "TX-1")
        node = next(n for n in graph["nodes"] if n["kind"] == "transaction")
        self.assertEqual((node["id"], node["attrs"]["verification"], node["attrs"]["status"]),
                         ("transaction:TX-1", "ambiguous", None))
        edge = next(e for e in graph["edges"] if e["kind"] == "precedes")
        self.assertEqual((edge["from"], edge["to"], edge["verification"]),
                         ("process:TX-1:IF_A_PR001", "process:TX-1:IF_A_PR002", "verified"))
        self.assertIs(relation_inputs.graph, graph)  # 시도 안에서 한 번만 만든다


class FakeCursor:
    def __init__(self, results):
        self.results, self.executed = list(results), []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.results.pop(0)

    def fetchall(self):
        return self.results.pop(0)


class FakeConn2:
    def __init__(self, results):
        self.cur = FakeCursor(results)

    def cursor(self):
        return self.cur


def process_values(row):
    return (*(row[key] for key in ("observationId", "executionId", "revisionNo", "transactionId",
                                   "transactionObservationId", "processId", "dependsOn", "status", "responseCode",
                                   "totalCount", "retryCount", "channelId", "adapterType", "processHubId",
                                   "focus")), row.get("startTimeRaw"), row.get("endTimeRaw"), 1)


class Reading(unittest.TestCase):
    def test_unkeyed_focus_reads_only_itself(self):
        conn = FakeConn2([(READ_AT, " \\N "), [process_values({**process_row(), "transactionId": " \\N "})]])
        relation_inputs = read_relation_inputs(conn, uuid.uuid4(), uuid.UUID(FOCUS))
        self.assertEqual(len(conn.cur.executed), 2)
        self.assertIsNone(conn.cur.executed[1][1]["tx"])
        self.assertFalse(relation_inputs.transaction_keyed)
        self.assertEqual(relation_inputs.transaction_executions, 0)

    def test_keyed_focus_picks_largest_retry_and_marks_truncation(self):
        many = [process_values(process_row(f"00000000-0000-4000-8000-{n:012d}", f"P{n}", "NONE", focus=n == 1))
                for n in range(1, 202)]
        top = [(TX_OBS, "1", "2", "TX-1", "IF_A", "CH_A", "CH_B", "F", "E901", False, 3, 0, 0, 0, "IF_A", "IF_A"),
               ("00000000-0000-4000-8000-000000000091", "1", "1", "TX-1", "IF_A", "CH_A", "CH_B", "S", "0000", False,
                3, 0, 0, 0, "IF_A", "IF_A")]
        conn = FakeConn2([(READ_AT, "TX-1"), many, [], top])
        relation_inputs = read_relation_inputs(conn, uuid.uuid4(), uuid.UUID(FOCUS))
        self.assertEqual(relation_inputs.processes_truncated, True)
        self.assertEqual(len(relation_inputs.processes), 200)
        self.assertEqual((relation_inputs.transaction["observationId"], relation_inputs.transaction_ambiguous,
                          relation_inputs.transaction_executions), (TX_OBS, False, 3))
        self.assertEqual([params["limit"] for _, params in conn.cur.executed[1:3]], [201, 501])
        # 도착 확정은 DB가 readAt과 최소 지연으로 계산한 값(여기서는 아직 지연 안).
        self.assertFalse(relation_inputs.transaction_confirmed)
        self.assertEqual({key: conn.cur.executed[3][1][key] for key in ("read_at", "settle", "trim", "null_mark")},
                         {"read_at": READ_AT, "settle": 30, "trim": " \t\r\n", "null_mark": "\\N"})
        self.assertEqual(relation_inputs.common_interface_id, "IF_A")
        top = [(*row[:9], True, *row[10:]) for row in top]
        confirmed = read_relation_inputs(FakeConn2([(READ_AT, "TX-1"), many, [], top]), uuid.uuid4(), uuid.UUID(FOCUS))
        self.assertTrue(confirmed.transaction_confirmed)
        self.assertTrue(confirmed.evidence()["transaction"]["arrivalConfirmed"])
        # 거래 행 조회 열 수가 코드의 열 이름 목록과 다르면 위치로 짐작하지 않고 시도 실패.
        with self.assertRaisesRegex(ValueError, "^relation_transaction_columns_mismatch$"):
            read_relation_inputs(FakeConn2([(READ_AT, "TX-1"), many, [], [row[:-1] for row in top]]), uuid.uuid4(),
                                 uuid.UUID(FOCUS))

    def test_missing_focus_row_is_storage_error(self):
        conn = FakeConn2([None])
        with self.assertRaisesRegex(ValueError, "relation_focus_unavailable"):
            read_relation_inputs(conn, uuid.uuid4(), uuid.UUID(FOCUS))
        conn = FakeConn2([(READ_AT, "TX-1"), [process_values(process_row(focus=False))], [], []])
        with self.assertRaisesRegex(ValueError, "relation_focus_unavailable"):
            read_relation_inputs(conn, uuid.uuid4(), uuid.UUID(FOCUS))


class RuleIdHelpers(unittest.TestCase):
    def test_full_match_only(self):
        from detector.hard_rules import is_protocol_rule_id, is_relation_rule_id
        self.assertTrue(is_relation_rule_id("hard:rel-0a1b2c3d"))
        self.assertTrue(is_protocol_rule_id("hard:proto-0a1b2c3d"))
        for bad in ("hard:rel-0a1b2c3d\n", "hard:rel-0a1b2c3dX", "xhard:rel-0a1b2c3d", "hard:rel-0A1B2C3D", None, 1):
            with self.subTest(bad=bad):
                self.assertFalse(is_relation_rule_id(bad))
                self.assertFalse(is_protocol_rule_id(bad if not isinstance(bad, str) else
                                                     bad.replace("rel-", "proto-")))


class BuiltInList(unittest.TestCase):
    def test_new_rules_are_last_in_builtin_order(self):
        self.assertEqual([rule["id"] for rule in HARD_RULES][-2:], [TRANSACTION_FAILED, UNREGISTERED_INTERFACE])
        self.assertEqual(json.loads(json.dumps(HARD_RULES[-2]["severity"])), {"byCode": {}, "default": "warning"})


if __name__ == "__main__":
    unittest.main()

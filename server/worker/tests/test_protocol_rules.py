"""룰 세트 v3 판정 6단계(protocol-rules.md 10절): 프로토콜 규칙 경로 만들기·판정·미등록 인터페이스.

경로 만들기는 공용 벡터(contracts/protocol-path-vectors.json — Trainer 초안 집계와 같은 정의)로, 판정은 4.1 표 행마다
1건으로 본다. DB 없이 관계 입력(RelationInputs)을 직접 만든다. 재판정(7단계)은 없다.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from detector.evidence_v4 import build_evidence  # noqa: E402
from detector.hard_rules import (COUNT_CONSERVATION, NEGATIVE_DURATION, PROCESS_STATUS, SUCCESS_WITH_ERROR,  # noqa: E402
                                 UNREGISTERED_INTERFACE, unresolved_hard_result)
from detector.method_results import with_elapsed  # noqa: E402
from detector.operational_state import pin_operational_state  # noqa: E402
from detector.protocol_rules import (PATH_REASONS, REASON_CAUSES, REASON_ORDER, build_routes,  # noqa: E402
                                     read_protocol_rules)
from detector.relation_plan import needs_relation_inputs, plan_relations  # noqa: E402
from test_detection_attempt import BASE_HARD, FakeConn, hard, protocol, v3  # noqa: E402
from test_relation_decision import FOCUS, POLICY, READ_AT, REVISIONS, TX_OBS, inputs, run, tx_row  # noqa: E402

VECTORS = Path(__file__).resolve().parents[2] / "contracts" / "protocol-path-vectors.json"
PROTO, OTHER_PROTO = "hard:proto-0000000a", "hard:proto-0000000b"
BUILT_IN = (PROCESS_STATUS, NEGATIVE_DURATION, SUCCESS_WITH_ERROR, COUNT_CONSERVATION)
OBS = ["00000000-0000-4000-8000-0000000000%02d" % n for n in range(1, 10)]


def step(observation, process_id, depends, channel, *, focus=False, end="2026-01-01 00:00:01"):
    return {"observationId": observation, "executionId": observation.replace("-4000-", "-5000-"), "revisionNo": "1",
            "transactionId": "TX-1", "transactionObservationId": None, "processId": process_id, "dependsOn": depends,
            "status": "S", "responseCode": "0000", "totalCount": "1", "retryCount": "0", "channelId": channel,
            "adapterType": "HTTP", "processHubId": None, "focus": focus, "startTimeRaw": "2026-01-01 00:00:00",
            "endTimeRaw": end}


# 기본 거래: P001(CH_A) → P002(CH_B), 판정 PROCESS = P002(가장 늦게 끝남).
def linear(*, focus_last=True):
    return [step(OBS[1], "P001", "NONE", "CH_A", focus=not focus_last, end="2026-01-01 00:00:01"),
            step(FOCUS, "P002", "P001", "CH_B", focus=focus_last, end="2026-01-01 00:00:02")]


def branching():
    """P001(CH_A) → {P002(CH_B), P003(CH_C)}, 판정 = P003."""
    return [step(OBS[1], "P001", "NONE", "CH_A", end="2026-01-01 00:00:01"),
            step(OBS[2], "P002", "P001", "CH_B", end="2026-01-01 00:00:02"),
            step(FOCUS, "P003", "P001", "CH_C", focus=True, end="2026-01-01 00:00:03")]


def state_with(entries, *, unregistered=True, unregistered_level="warning", extra_hard=()):
    hard_rules = BASE_HARD + ([hard(UNREGISTERED_INTERFACE, True, unregistered_level)] if unregistered else []) + \
        list(extra_hard)
    return pin_operational_state(FakeConn(content=v3(hard_rules, protocol_rules=entries), version=3))


def entry(paths=(("CH_A", "CH_B"),), interface="IF_A", rule_id=PROTO, **extra):
    return protocol(rule_id, interface, paths, **extra)


def tx(**fields):
    return {**tx_row("S", "0000"), **fields}


def plan(state, relation_inputs):
    return plan_relations(state, relation_inputs, {})


def protocol_notes(result):
    return [item for item in result.notes if item["kind"] == "protocol"]


class Vectors(unittest.TestCase):
    """공용 벡터: Trainer protocolPathDrafts와 같은 기대값(입력 순서와 무관)."""

    def test_shared_vectors(self):
        document = json.loads(VECTORS.read_text(encoding="utf-8"))
        self.assertEqual(document["reasonOrder"], list(PATH_REASONS))
        for case in document["cases"]:
            rows = [dict(zip(document["processColumns"], values), observationId=f"o{n}")
                    for n, values in enumerate(case["processes"])]
            expected = (case["expected"]["reason"],
                        [(tuple(route["channels"]), tuple(route["steps"])) for route in case["expected"]["routes"]])
            for order in (rows, rows[::-1]):
                with self.subTest(case=case["name"], reversed=order is not rows):
                    reason, routes = build_routes(order, case["truncated"])
                    self.assertEqual((reason, [(route.channels, route.steps) for route in routes]), expected)

    def test_reason_order_and_causes(self):
        # 3.4 "사유 선택 전체 순서" 중 판정 부분(3.1a → 3.2). Trainer REASON_ORDER에서 사전 제외 셋을 뺀 것과 같다.
        self.assertEqual(REASON_ORDER, ("transaction_row_ambiguous", "interface_absent", "graph_truncated",
                                        "process_id_missing", "precedes_ambiguous", "depend_process_id_missing",
                                        "start_ambiguous", "chain_broken", "channel_absent", "channel_ambiguous",
                                        "path_too_long", "routes_too_many"))
        self.assertEqual(set(REASON_CAUSES), set(REASON_ORDER))
        self.assertEqual({REASON_CAUSES[r] for r in ("graph_truncated", "path_too_long", "routes_too_many")},
                         {"capacity"})


class SnapshotReading(unittest.TestCase):
    def test_shape_violations_fail_closed(self):
        cases = {
            "bad id": entry(rule_id="hard:proto-0000000A"),
            "absent interface": entry(interface=" \\N"),
            "bad severity": entry(severity="high"),
            "no paths": {**entry(), "paths": []},
            "empty channels": entry(paths=((),)),
            "absent channel": entry(paths=(("CH_A", " "),)),
            "non-string channel": {**entry(), "paths": [{"channels": ["CH_A", 1]}]},
            "duplicate path": entry(paths=(("CH_A",), ("CH_A",))),
            "too many channels": entry(paths=(tuple(f"C{n}" for n in range(65)),)),
            "too many paths": entry(paths=tuple((f"C{n}",) for n in range(65))),
            "enabled not bool": {**entry(), "enabled": 1},
        }
        for name, item in cases.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, r"^invalid_protocol_rule:"):
                    read_protocol_rules([item])

    def test_duplicates(self):
        with self.assertRaisesRegex(ValueError, "^duplicate_method_id$"):
            read_protocol_rules([entry(), entry(interface="IF_B")])
        with self.assertRaisesRegex(ValueError, f"^invalid_protocol_rule:{OTHER_PROTO}$"):
            read_protocol_rules([entry(), entry(rule_id=OTHER_PROTO)])

    def test_pinned_levels_and_disabled_entries(self):
        state = state_with([entry(severity="info"), entry(interface="IF_B", rule_id=OTHER_PROTO, enabled=False)])
        self.assertEqual([rule.id for rule in state.protocol_rules], [PROTO, OTHER_PROTO])
        self.assertEqual(state.hard_rules.protocol_rule_levels, ((PROTO, "info"),))


class CaseTable(unittest.TestCase):
    """protocol-rules.md 4.1 표 행마다 1건."""

    def test_feature_off_has_no_record_and_reads_nothing(self):
        for entries in ([], [entry(enabled=False)]):
            with self.subTest(entries=len(entries)):
                state = state_with(entries)
                self.assertFalse(needs_relation_inputs(state, {}))
                result = plan_relations(state, None, {})
                self.assertEqual(result.hard_ids, BUILT_IN)  # 미등록 규칙이 켜져 있어도 빠진다
                self.assertEqual(result.evidence_fields(), {})

    def test_feature_on_reads_inputs(self):
        self.assertTrue(needs_relation_inputs(state_with([entry()]), {}))

    def test_transaction_not_arrived_or_unconfirmed_is_deferred(self):
        state = state_with([entry()])
        for relation_inputs in (inputs(None, processes=linear()), inputs(tx(), processes=linear(), confirmed=False)):
            with self.subTest(executions=relation_inputs.transaction_executions):
                result = plan(state, relation_inputs)
                self.assertEqual(result.hard_ids, BUILT_IN)
                self.assertEqual(protocol_notes(result), [
                    {"ruleId": None, "kind": "protocol", "result": "deferred_to_transaction", "reason": None,
                     "waitingFor": {"layer": "T"}}])
                self.assertNotIn("protocolReview", result.evidence_fields())

    def test_transaction_id_missing_is_not_evaluated(self):
        result = plan(state_with([entry()]), inputs(None, focus_tx=" ", processes=linear()))
        self.assertEqual(result.hard_ids, BUILT_IN)
        self.assertEqual(protocol_notes(result), [{"ruleId": None, "kind": "protocol", "result": "not_applicable",
                                                   "reason": "transaction_id_missing", "waitingFor": None}])

    def test_registered_entry_matched_is_normal(self):
        result = plan(state_with([entry()]), inputs(tx(), processes=linear()))
        self.assertEqual(result.hard_ids, (*BUILT_IN, PROTO))
        method = result.relation_results[PROTO]
        self.assertEqual((method["status"], method["anomalous"], method["methodVersion"]),
                         ("succeeded", False, "protocol-path-v1"))
        self.assertEqual(method["evidence"], [{"interfaceMatched": True, "routes": [
            {"steps": ["process:TX-1:P001", "process:TX-1:P002"], "matched": True}]}])
        self.assertIsNone(result.protocol_review)
        self.assertEqual(protocol_notes(result), [])

    def test_registered_entry_unmatched_is_anomaly_with_review(self):
        result = plan(state_with([entry(paths=(("CH_A", "CH_X"),), severity="info")]),
                      inputs(tx(), processes=linear()))
        method = result.relation_results[PROTO]
        self.assertEqual((method["anomalous"], method["code"], method["riskLevel"]),
                         (True, "PROTO_PATH_UNMATCHED", "info"))
        self.assertEqual(result.protocol_review, {
            "version": 1, "kind": "path_unmatched", "interfaceId": "IF_A", "protocolRuleId": PROTO,
            "ruleSetRevision": 1, "transactionObservationId": TX_OBS,
            "routes": [{"channels": ["CH_A", "CH_B"], "pathKey": "CH_A:CH_B", "matched": False}],
            "routesReason": None,
            "endpoints": {"start": "CH_A", "end": "CH_B", "matchesRoutes": True, "ambiguous": False}})

    def test_branching_each_route_must_match(self):
        state = state_with([entry(paths=(("CH_A", "CH_B"),))])
        result = plan(state, inputs(tx(), processes=branching()))
        method = result.relation_results[PROTO]
        self.assertTrue(method["anomalous"])
        self.assertEqual([(route["channels"], route["matched"]) for route in result.protocol_review["routes"]],
                         [(["CH_A", "CH_B"], True), (["CH_A", "CH_C"], False)])
        both = plan(state_with([entry(paths=(("CH_A", "CH_C"), ("CH_A", "CH_B")))]),
                    inputs(tx(), processes=branching()))
        self.assertFalse(both.relation_results[PROTO]["anomalous"])

    def test_endpoints_are_recorded_not_judged(self):
        mismatch = tx(channelId="CH_Z", endChannelId=" ")
        normal = plan(state_with([entry()]), inputs(mismatch, processes=linear()))
        self.assertFalse(normal.relation_results[PROTO]["anomalous"])  # 끝점이 달라도 N
        hit = plan(state_with([entry(paths=(("CH_A",),))]), inputs(tx(channelId="CH_Z"), processes=linear()))
        self.assertEqual(hit.protocol_review["endpoints"],
                         {"start": "CH_Z", "end": "CH_B", "matchesRoutes": False, "ambiguous": False})
        absent = plan(state_with([entry(paths=(("CH_A",),))]), inputs(mismatch, processes=linear()))
        self.assertEqual(absent.protocol_review["endpoints"],
                         {"start": "CH_Z", "end": None, "matchesRoutes": None, "ambiguous": False})

    def test_registered_entry_without_route_is_method_hold(self):
        broken = [step(OBS[1], "P001", "NONE", None), step(FOCUS, "P002", "P001", "CH_B", focus=True,
                                                            end="2026-01-01 00:00:02")]
        result = plan(state_with([entry()]), inputs(tx(), processes=broken))
        method = result.relation_results[PROTO]
        self.assertEqual((method["status"], method["anomalous"], method["cause"], method["evidence"]),
                         ("not_ready", None, "input_missing", [{"reason": "channel_absent"}]))
        self.assertIsNone(result.protocol_review)

    def test_disabled_entry_with_feature_on_is_not_applicable(self):
        state = state_with([entry(), entry(interface="IF_B", rule_id=OTHER_PROTO, enabled=False)])
        result = plan(state, inputs(tx(interfaceId="IF_B"), processes=linear()))
        self.assertEqual(result.hard_ids, BUILT_IN)
        self.assertEqual(protocol_notes(result), [{"ruleId": OTHER_PROTO, "kind": "protocol",
                                                   "result": "not_applicable", "reason": "protocol_disabled",
                                                   "waitingFor": None}])

    def test_unregistered_interface_is_anomaly(self):
        result = plan(state_with([entry()], unregistered_level="info"), inputs(tx(interfaceId="IF_N"),
                                                                              processes=linear()))
        self.assertEqual(result.hard_ids, (*BUILT_IN, UNREGISTERED_INTERFACE))
        method = result.relation_results[UNREGISTERED_INTERFACE]
        self.assertEqual((method["anomalous"], method["code"], method["riskLevel"], method["methodVersion"]),
                         (True, "PROTO_INTERFACE_UNREGISTERED", "info", "unregistered-interface-v1"))
        self.assertEqual(method["evidence"], [{"interfaceMatched": False, "routes": [
            {"steps": ["process:TX-1:P001", "process:TX-1:P002"], "matched": False}]}])
        review = result.protocol_review
        self.assertEqual((review["kind"], review["interfaceId"], review["protocolRuleId"], review["routesReason"]),
                         ("interface_unregistered", "IF_N", None, None))
        self.assertEqual(review["routes"], [{"channels": ["CH_A", "CH_B"], "pathKey": "CH_A:CH_B", "matched": False}])

    def test_unregistered_without_routes_is_still_anomaly(self):
        broken = [step(OBS[1], "P001", "P009", "CH_A"), step(FOCUS, "P002", "P001", "CH_B", focus=True,
                                                              end="2026-01-01 00:00:02")]
        result = plan(state_with([entry()]), inputs(tx(interfaceId="IF_N"), processes=broken))
        method = result.relation_results[UNREGISTERED_INTERFACE]
        self.assertEqual((method["anomalous"], method["evidence"]),
                         (True, [{"interfaceMatched": False, "routes": []}]))
        self.assertEqual((result.protocol_review["routes"], result.protocol_review["routesReason"],
                          result.protocol_review["endpoints"]["matchesRoutes"]), ([], "start_ambiguous", None))

    def test_unregistered_rule_off_is_silent(self):
        result = plan(state_with([entry()], unregistered=False), inputs(tx(interfaceId="IF_N"), processes=linear()))
        self.assertEqual((result.hard_ids, protocol_notes(result), result.protocol_review), (BUILT_IN, [], None))

    def test_interface_absent(self):
        absent = inputs(tx(interfaceId=" \\N"), processes=linear())
        held = plan(state_with([entry()]), absent)
        method = held.relation_results[UNREGISTERED_INTERFACE]
        self.assertEqual((method["status"], method["cause"], method["evidence"]),
                         ("not_ready", "input_missing", [{"reason": "interface_absent"}]))
        silent = plan(state_with([entry()], unregistered=False), absent)
        self.assertEqual(silent.hard_ids, BUILT_IN)
        self.assertEqual(protocol_notes(silent), [{"ruleId": None, "kind": "protocol", "result": "not_applicable",
                                                   "reason": "interface_absent", "waitingFor": None}])

    def test_ambiguous_transaction_row(self):
        unknown = inputs(None, executions=2, ambiguous=True, processes=linear())
        held = plan(state_with([entry()]), unknown)
        method = held.relation_results[UNREGISTERED_INTERFACE]
        self.assertEqual((method["cause"], method["evidence"]),
                         ("input_invalid", [{"reason": "transaction_row_ambiguous"}]))
        silent = plan(state_with([entry()], unregistered=False), unknown)
        self.assertEqual([item["reason"] for item in protocol_notes(silent)], ["transaction_row_ambiguous"])
        # 후보 실행의 interface_id가 모두 같으면 그 값으로 경로 판정을 계속한다(끝점은 모호로 기록).
        common = replace(unknown, common_interface_id="IF_A")
        result = plan(state_with([entry(paths=(("CH_A",),))]), common)
        self.assertTrue(result.relation_results[PROTO]["anomalous"])
        self.assertEqual((result.protocol_review["transactionObservationId"], result.protocol_review["endpoints"]),
                         (None, {"start": None, "end": None, "matchesRoutes": None, "ambiguous": True}))

    def test_only_latest_ended_decision_judges(self):
        result = plan(state_with([entry(paths=(("CH_A",),))]), inputs(tx(), processes=linear(focus_last=False)))
        self.assertEqual((result.hard_ids, protocol_notes(result), result.protocol_review), (BUILT_IN, [], None))

    def test_truncated_input_holds_on_every_decision(self):
        # 입력 상한이면 붙이는 판정을 정할 수 없어(relation-rules.md required_step과 같음) 모든 판정에서 보류한다.
        result = plan(state_with([entry()]), inputs(tx(), processes=linear(focus_last=False), truncated=True))
        method = result.relation_results[PROTO]
        self.assertEqual((method["cause"], method["evidence"]), ("capacity", [{"reason": "graph_truncated"}]))

    def test_truncated_unregistered_holds_on_every_decision(self):
        # 입력 상한이면 미등록 인터페이스도 T가 아니라 모든 판정에서 보류(4.1, 켜진 항목·required_step과 같은 규칙).
        state = state_with([entry()])
        for focus_last in (True, False):
            with self.subTest(focus_last=focus_last):
                result = plan(state, inputs(tx(interfaceId="IF_N"), processes=linear(focus_last=focus_last),
                                            truncated=True))
                self.assertEqual(result.hard_ids, (*BUILT_IN, UNREGISTERED_INTERFACE))
                method = result.relation_results[UNREGISTERED_INTERFACE]
                self.assertEqual((method["status"], method["anomalous"], method["cause"], method["evidence"]),
                                 ("not_ready", None, "capacity", [{"reason": "graph_truncated"}]))
                self.assertIsNone(result.protocol_review)
                evidence = Assembly.evidence(None, state, result, run(state, result))
                self.assertNotIn("protocolReview", evidence)
                self.assertEqual(evidence["composite"]["grade"], "undetermined")


class Assembly(unittest.TestCase):
    """판정 조립: 방법 순서·n·대조 집합·투영·evidence 기록."""

    def evidence(self, state, result, methods):
        return build_evidence(POLICY, methods, REVISIONS, state.hard_rules, source_id=uuid.uuid4(), telemetry={},
                              observation={"id": FOCUS, "revisionNo": 1, "inputAsOf": READ_AT.isoformat()},
                              input_manifest_hash="b" * 64, evaluated_hard_ids=result.hard_ids,
                              relation=result.evidence_fields())

    def test_unmatched_route_evidence(self):
        state = state_with([entry(paths=(("CH_A",),))])
        result = plan(state, inputs(tx(), processes=linear()))
        methods = run(state, result)
        evidence = self.evidence(state, result, methods)
        self.assertEqual([m["methodId"] for m in evidence["methodResults"]], [*BUILT_IN, PROTO])
        self.assertEqual(evidence["categoryResults"]["hard"]["n"], 5)
        self.assertEqual(evidence["composite"]["grade"], "anomaly")
        self.assertEqual(evidence["composite"]["basisMethods"], [PROTO])
        self.assertEqual([rule["ruleKey"] for rule in evidence["rules"]],
                         ["process-status", "negative-duration", "success-with-error", "count-conservation"])
        self.assertEqual(evidence["ruleSeverities"], [])
        self.assertEqual(evidence["protocolReview"]["kind"], "path_unmatched")
        self.assertEqual(evidence["relationInputs"]["transaction"]["arrivalConfirmed"], True)

    def test_unregistered_order_and_projection(self):
        state = state_with([entry()])
        result = plan(state, inputs(tx(interfaceId="IF_N"), processes=linear()))
        evidence = self.evidence(state, result, run(state, result))
        self.assertEqual([m["methodId"] for m in evidence["methodResults"]], [*BUILT_IN, UNREGISTERED_INTERFACE])
        self.assertNotIn("unregistered-interface", [rule["ruleKey"] for rule in evidence["rules"]])
        self.assertEqual(evidence["protocolReview"]["kind"], "interface_unregistered")

    def test_normal_and_held_have_no_review(self):
        state = state_with([entry()])
        for relation_inputs in (inputs(tx(), processes=linear()), inputs(None, processes=linear())):
            with self.subTest(arrived=relation_inputs.transaction_executions):
                result = plan(state, relation_inputs)
                evidence = self.evidence(state, result, run(state, result))
                self.assertNotIn("protocolReview", evidence)
                self.assertEqual(evidence["composite"]["grade"], "normal")

    def test_review_only_when_final_protocol_method_is_anomalous(self):
        # 계획은 T였어도 최종 결과가 시간 초과·슬롯 부족(not_ready)이면 protocolReview를 남기지 않는다(5.3).
        for entries, interface, method_id, version in (
                ([entry(paths=(("CH_A",),))], "IF_A", PROTO, "protocol-path-v1"),
                ([entry()], "IF_N", UNREGISTERED_INTERFACE, None)):
            state = state_with(entries)
            result = plan(state, inputs(tx(interfaceId=interface), processes=linear()))
            self.assertIsNotNone(result.protocol_review)
            methods = run(state, result)
            for cause in ("timeout", "capacity"):
                with self.subTest(method=method_id, cause=cause):
                    unresolved = with_elapsed(unresolved_hard_result(method_id, cause, method_version=version), 3000.0)
                    replaced = [unresolved if m["methodId"] == method_id else m for m in methods]
                    evidence = self.evidence(state, result, replaced)
                    self.assertNotIn("protocolReview", evidence)
                    self.assertEqual(evidence["composite"]["grade"], "undetermined")
            self.assertIn("protocolReview", self.evidence(state, result, methods))

    def test_selection_mismatch_fails(self):
        state = state_with([entry(paths=(("CH_A",),))])
        result = plan(state, inputs(tx(), processes=linear()))
        methods = run(state, result)
        wrong = replace(state, hard_rules=replace(state.hard_rules, protocol_rule_levels=((PROTO, "info"),)))
        with self.assertRaisesRegex(ValueError, "^hard_rule_selection_mismatch$"):
            self.evidence(wrong, result, methods)
        unknown = replace(state, hard_rules=replace(state.hard_rules, protocol_rule_levels=()))
        with self.assertRaisesRegex(ValueError, "^hard_rule_selection_mismatch$"):
            self.evidence(unknown, result, methods)
        # 미등록 인터페이스가 평가되지 않은 판정에 들어오면 대조 실패(과제 목록과 대조 집합이 다름).
        with self.assertRaisesRegex(ValueError, "^hard_rule_selection_mismatch$"):
            build_evidence(POLICY, methods, REVISIONS, state.hard_rules, source_id=uuid.uuid4(), telemetry={},
                           observation={}, input_manifest_hash="b" * 64,
                           evaluated_hard_ids=(*BUILT_IN, UNREGISTERED_INTERFACE, PROTO))


if __name__ == "__main__":
    unittest.main()

"""룰 세트 v3 판정 5단계(protocol-rules.md 10절): 관계 규칙 판정 kind 셋·정보 표시 kind·소프트룰 scope 선택.

relation-rules.md 3.1·5절 표 행마다 1건. DB 없이 관계 입력(RelationInputs)을 직접 만든다. 재판정(7단계)은 없다.
"""
from __future__ import annotations

from pathlib import Path
import sys
import time
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from detector import attempt_v04  # noqa: E402
from detector.evidence_v4 import build_evidence, build_interim_payload  # noqa: E402
from detector.hard_rules import TRANSACTION_FAILED, unresolved_hard_result  # noqa: E402
from detector.method_runner import execute_methods  # noqa: E402
from detector.operational_state import pin_operational_state  # noqa: E402
from detector.relation_plan import needs_relation_inputs, plan_relations  # noqa: E402
from detector.relation_rules import judge_relations, latest_ended_is_focus, read_relation_rules  # noqa: E402
from detector.soft_rules import in_scope  # noqa: E402
from test_detection_attempt import BASE_HARD, SOFT, FakeConn, hard, v3  # noqa: E402
from test_relation_decision import (FAILED_SEVERITY, FOCUS, POLICY, RAW, REVISIONS, inputs, process_row,  # noqa: E402
                                    tx_row)

PRED = "00000000-0000-4000-8000-000000000002"
OTHER = "00000000-0000-4000-8000-000000000003"
PRECEDES_ID, HANDOFF_ID, REQUIRED_ID, BRANCH_ID = ("hard:rel-0000000a", "hard:rel-0000000b", "hard:rel-0000000c",
                                                   "hard:rel-0000000d")
FOCUS_NODE, PRED_NODE = "process:TX-1:IF_A_PR002", "process:TX-1:IF_A_PR001"
PRECEDES_EDGE = f"precedes:{PRED_NODE}:{FOCUS_NODE}"
OWN = {"process_id": "IF_A_PR002", "depend_process_id": "IF_A_PR001", "adapter_type": "HTTP",
       "transaction_id": "TX-1", "process_hub_id": "HUB"}


def relation(rule_id=PRECEDES_ID, kind="precedes", *, enabled=True, severity="critical", **fields):
    item = {"id": rule_id, "kind": kind, "scope": None, "from": {"processId": "IF_A_PR001"},
            "to": {"processId": "IF_A_PR002"}, "match": None, "severity": severity, "enabled": enabled}
    if kind == "message_handoff":
        item["match"] = {"from": "OUT", "to": "IN", "key": "message_id"}
    if kind == "required_step":
        item.update(scope={"interfaceId": "IF_A"}, to=None)
    return {**item, **fields}


def rules(*items):
    return read_relation_rules(list(items))


def predecessor(depends="NONE", observation=PRED, **extra):
    return {**process_row(observation, "IF_A_PR001", depends, focus=False), **extra}


def message(observation, process_id, direction, message_id="M-1"):
    return {"observationId": observation, "executionId": observation.replace("-4000-", "-5000-"), "revisionNo": "1",
            "processObservationId": None, "transactionId": "TX-1", "processId": process_id,
            "messageId": message_id, "direction": direction, "status": "S", "dataSize": "1", "messageIndex": "1"}


def judge(rule_items, relation_inputs, raw=None):
    return judge_relations(rules(*rule_items), relation_inputs, {**OWN, **(raw or {})})


def single(test, rule_items, relation_inputs, raw=None):
    methods, notes = judge(rule_items, relation_inputs, raw)
    test.assertEqual(len(methods) + len(notes), 1, (methods, notes))
    return (methods[0][1], None) if methods else (None, notes[0])


class SnapshotReading(unittest.TestCase):
    def test_unknown_kind_fails_even_when_disabled(self):
        with self.assertRaisesRegex(ValueError, f"^relation_rule_kind_unknown:{PRECEDES_ID}$"):
            rules(relation(kind="timeout", enabled=False))

    def test_shape_violations_fail_closed(self):
        cases = {
            "extra from key": relation(**{"from": {"processId": "IF_A_PR001", "channel": "X"}}),
            "absent processId": relation(**{"from": {"processId": " \\N"}}),
            "required_step with to": relation(kind="required_step", to={"processId": "X"}),
            "required_step scope": relation(kind="required_step", scope={"interfaceId": "IF_A", "adapterType": "H"}),
            "precedes without to": relation(to=None),
            "handoff wrong match": relation(kind="message_handoff", match={"from": "OUT", "to": "IN", "key": "x"}),
            "precedes with match": relation(match={"from": "OUT", "to": "IN", "key": "message_id"}),
            "empty scope": relation(scope={}),
            "unknown scope key": relation(scope={"processHubId": "H"}),
            "from equals to": relation(to={"processId": "IF_A_PR001"}),
            "handoff from equals to": relation(kind="message_handoff", to={"processId": "IF_A_PR001"}),
            "bad severity": relation(severity="high"),
            "bad id": relation(rule_id="hard:rel-0000000A"),
            "enabled not bool": relation(enabled="yes"),
        }
        for name, item in cases.items():
            with self.subTest(name):
                with self.assertRaisesRegex(ValueError, "^invalid_relation_rule:"):
                    rules(item)
        with self.assertRaisesRegex(ValueError, "^duplicate_method_id$"):
            rules(relation(), relation(kind="message_handoff"))

    def test_pinned_state_counts_only_judgment_kinds(self):
        content = v3(BASE_HARD, relation_rules=[relation(), relation(BRANCH_ID, "allowed_branch", severity="info"),
                                                relation(HANDOFF_ID, "message_handoff", enabled=False)])
        state = pin_operational_state(FakeConn(content=content, version=3))
        self.assertEqual([rule.id for rule in state.relation_rules], [PRECEDES_ID, BRANCH_ID])
        self.assertEqual(state.hard_rules.relation_rule_levels, ((PRECEDES_ID, "critical"),))
        # 정보 표시 규칙만 켜진 룰 세트(내장도 꺼짐)는 방법이 없다 — 시도 실패.
        only_info = v3([hard(rule["id"], False, rule["severity"]) for rule in BASE_HARD],
                       relation_rules=[relation(BRANCH_ID, "allowed_branch")])
        with self.assertRaisesRegex(ValueError, "^detection_methods_empty$"):
            pin_operational_state(FakeConn(content=only_info, version=3))

    def test_soft_scope_shape_fails_closed(self):
        for scope in ({"adapterType": "HTTP"}, {"processId": ""}, {"processId": "P", "other": "x"},
                      {"processId": 1}, "IF_A_PR002"):
            with self.subTest(scope=scope):
                content = v3(BASE_HARD, soft_rules=[{**SOFT, "scope": scope}])
                try:
                    pin_operational_state(FakeConn(content=content, version=3))
                except ValueError as exc:
                    # 문자열 scope는 hash 정규화에서 먼저 막힌다.
                    self.assertRegex(str(exc), f"^(invalid_soft_rule_scope:{SOFT['id']}|rule_set_hash_mismatch)$")
                else:
                    self.fail("scope shape accepted")


class Selection(unittest.TestCase):
    def test_successor_rules_follow_process_and_adapter(self):
        tx = inputs(None, processes=[predecessor(), process_row()])
        for raw in ({"process_id": "IF_A_PR003"}, {"process_id": " \\N"}, {"process_id": None},
                    {"adapter_type": "MQ"}, {"adapter_type": None}):
            with self.subTest(raw=raw):
                self.assertEqual(judge([relation(scope={"adapterType": "HTTP", "interfaceId": "IF_A"})], tx, raw),
                                 ([], []))
        methods, _ = judge([relation(scope={"adapterType": "HTTP"})], tx)
        self.assertEqual([method_id for method_id, _ in methods], [PRECEDES_ID])

    def test_inputs_are_read_only_when_a_rule_can_apply(self):
        content = v3(BASE_HARD, relation_rules=[relation()])
        state = pin_operational_state(FakeConn(content=content, version=3))
        self.assertFalse(needs_relation_inputs(state, {**RAW, "process_id": "IF_A_PR009"}))
        self.assertTrue(needs_relation_inputs(state, {**RAW, **OWN}))
        plan = plan_relations(state, None, {**RAW, "process_id": "IF_A_PR009"})
        self.assertEqual((plan.evidence_fields(), plan.hard_ids), ({}, state.hard_rules.enabled_ids))
        required = pin_operational_state(FakeConn(content=v3(BASE_HARD, relation_rules=[
            relation(REQUIRED_ID, "required_step")]), version=3))
        self.assertTrue(needs_relation_inputs(required, {**RAW, "process_id": "anything"}))


class Precedes(unittest.TestCase):
    def test_verified_edge_is_normal(self):
        result, _ = single(self, [relation()], inputs(None, processes=[predecessor(), process_row()]))
        self.assertEqual((result["status"], result["anomalous"], result["methodVersion"]),
                         ("succeeded", False, "relation-precedes-v1"))
        self.assertEqual(result["evidence"], [{"result": "no_hit", "node": FOCUS_NODE, "from": PRED_NODE,
                                               "edge": PRECEDES_EDGE}])

    def test_own_value_violations(self):
        for depends, violation in (("NONE", "start_recorded"), (" NONE\t", "start_recorded"),
                                   ("IF_A_PR000", "other_predecessor"), (" IF_A_PR001", "other_predecessor")):
            with self.subTest(depends=depends):
                result, _ = single(self, [relation(severity="warning")],
                                   inputs(None, processes=[process_row(depends=depends)]),
                                   {"depend_process_id": depends})
                self.assertEqual((result["anomalous"], result["code"], result["riskLevel"]),
                                 (True, "REL_PRECEDES", "warning"))
                self.assertEqual(result["evidence"], [{"result": "hit", "violation": violation, "node": FOCUS_NODE,
                                                       "edge": None}])

    def test_missing_predecessor_held_then_violation_after_transaction(self):
        _, held = single(self, [relation()], inputs(None))
        self.assertEqual(held, {"ruleId": PRECEDES_ID, "kind": "precedes", "result": "held",
                                "reason": "collection_incomplete", "waitingFor": {"processId": "IF_A_PR001"}})
        # [확정 11/12 사용자] 거래 행이 왔는데 선행 단계 행이 없음 = 위반(첫 판정에 거래 행이 이미 있는 경우).
        for relation_inputs in (inputs(tx_row("S", "0000")), inputs(None, executions=2, ambiguous=True)):
            with self.subTest(executions=relation_inputs.transaction_executions):
                result, _ = single(self, [relation()], relation_inputs)
                self.assertEqual((result["anomalous"], result["evidence"][0]["violation"]),
                                 (True, "predecessor_missing"))
                self.assertEqual(result["evidence"][0]["edge"], f"precedes:missing:{FOCUS_NODE}:IF_A_PR001:{FOCUS_NODE}")

    def test_method_holds(self):
        retried = process_row(OTHER, depends="IF_A_PR001", focus=False)  # 같은 재시도 번호 → 묶음 모호
        cases = {
            "depend_process_id_missing": (inputs(None, processes=[process_row(depends=None)]),
                                          {"depend_process_id": None}, "input_missing"),
            "transaction_id_missing": (inputs(None, focus_tx=" "), {"transaction_id": " "}, "input_missing"),
            "graph_truncated": (inputs(None, processes=[predecessor(), process_row()], truncated=True), {},
                                "capacity"),
            "precedes_ambiguous": (inputs(None, processes=[predecessor(), process_row(), retried]), {},
                                   "input_invalid"),
        }
        for reason, (relation_inputs, raw, cause) in cases.items():
            with self.subTest(reason=reason):
                result, _ = single(self, [relation()], relation_inputs, raw)
                self.assertEqual((result["status"], result["anomalous"], result["cause"], result["evidence"]),
                                 ("not_ready", None, cause, [{"reason": reason}]))

    def test_own_value_is_judged_before_key_checks(self):
        # 거래 ID가 없어도 자신의 값이 NONE이면 위반이다(평가 순서 1 → 2).
        result, _ = single(self, [relation()], inputs(None, focus_tx=None), {"transaction_id": None,
                                                                              "depend_process_id": "NONE"})
        self.assertEqual(result["evidence"][0]["node"], f"unkeyed:{FOCUS}")
        self.assertTrue(result["anomalous"])


class MessageHandoff(unittest.TestCase):
    def rule(self):
        return relation(HANDOFF_ID, "message_handoff", severity="warning")

    def run_case(self, messages, *, transaction=None, messages_truncated=False, focus_tx="TX-1",
                 processes=None):
        relation_inputs = inputs(transaction, focus_tx=focus_tx, messages=messages,
                                 processes=processes or [predecessor(), process_row()])
        if messages_truncated:
            relation_inputs = type(relation_inputs)(**{**relation_inputs.__dict__, "messages_truncated": True})
        return single(self, [self.rule()], relation_inputs)

    def test_match_and_mismatch(self):
        result, _ = self.run_case([message(PRED, "IF_A_PR001", "OUT"), message(FOCUS, "IF_A_PR002", "IN")])
        self.assertEqual((result["anomalous"], result["methodVersion"]), (False, "relation-message_handoff-v1"))
        out_node = "message:" + PRED.replace("-4000-", "-5000-")
        self.assertEqual(result["evidence"], [{"result": "no_hit", "node": FOCUS_NODE, "from": PRED_NODE,
                                               "edge": PRECEDES_EDGE, "handoffs": [
                                                   {"edge": f"hands_off:{PRED_NODE}:{FOCUS_NODE}:{out_node}",
                                                    "result": "match"}]}])
        result, _ = self.run_case([message(PRED, "IF_A_PR001", "OUT"), message(FOCUS, "IF_A_PR002", "IN", "M-2")])
        self.assertEqual((result["anomalous"], result["code"], result["riskLevel"]),
                         (True, "REL_MESSAGE_HANDOFF", "warning"))

    def test_holds_and_notes(self):
        out, in_ = message(PRED, "IF_A_PR001", "OUT"), message(FOCUS, "IF_A_PR002", "IN")
        second_in = message(OTHER, "IF_A_PR002", "IN", "M-3")
        cases = {
            "message_id_missing": ([{**out, "messageId": None}, in_], {}, ("not_ready", "input_missing")),
            "message_link_ambiguous": ([out, {**in_, "messageId": "M-2"}, second_in], {},
                                       ("not_ready", "input_invalid")),
            "graph_truncated": ([out, in_], {"messages_truncated": True}, ("not_ready", "capacity")),
        }
        for reason, (messages, extra, (status, cause)) in cases.items():
            with self.subTest(reason=reason):
                result, _ = self.run_case(messages, **extra)
                self.assertEqual((result["status"], result["cause"], result["evidence"]),
                                 (status, cause, [{"reason": reason}]))
        _, waiting = self.run_case([out])
        self.assertEqual((waiting["result"], waiting["reason"], waiting["waitingFor"]),
                         ("held", "collection_incomplete", {"processId": "IF_A_PR002", "direction": "IN"}))
        _, waiting = self.run_case([in_], transaction=tx_row("S", "0000"))  # 거래 행 뒤에도 메시지 없음 → 보류로 끝
        self.assertEqual(waiting["waitingFor"], {"processId": "IF_A_PR001", "direction": "OUT"})
        excluded = [predecessor(), process_row()]
        excluded = [{**row, "transactionId": "TX-07"} for row in excluded]
        _, skipped = self.run_case([{**out, "transactionId": "TX-07"}, {**in_, "transactionId": "TX-07",
                                                                         "messageId": "M-9"}],
                                   focus_tx="TX-07", processes=excluded)
        self.assertEqual((skipped["result"], skipped["reason"]), ("not_applicable", "excluded_transaction_suffix"))

    def test_predecessor_cases(self):
        _, absent = single(self, [self.rule()], inputs(None, processes=[process_row(depends="NONE")]),
                           {"depend_process_id": "NONE"})
        self.assertEqual((absent["result"], absent["reason"]), ("not_applicable", "precedes_absent"))
        _, held = single(self, [self.rule()], inputs(None))
        self.assertEqual((held["result"], held["waitingFor"]), ("held", {"processId": "IF_A_PR001"}))
        # 거래 행 도착 확정 뒤 선행 행 없음: 선행 위반은 precedes 몫, 인계는 적용 안 함(precedes_absent와 다른 사유).
        _, after = single(self, [self.rule()], inputs(tx_row("S", "0000")))
        self.assertEqual((after["result"], after["reason"]), ("not_applicable", "predecessor_missing"))
        # 도착 확정 전(CSV 재생처럼 거래 행이 먼저 커밋됨)이면 아직 보류.
        _, early = single(self, [self.rule()], inputs(tx_row("S", "0000"), confirmed=False))
        self.assertEqual((early["result"], early["reason"]), ("held", "collection_incomplete"))

    def test_several_out_messages_with_mixed_results(self):
        out_a, out_b = message(PRED, "IF_A_PR001", "OUT", "M-1"), message(OTHER, "IF_A_PR001", "OUT", "M-2")
        in_ = message(FOCUS, "IF_A_PR002", "IN", "M-2")
        retried = predecessor(OTHER, retryCount="1")  # 같은 묶음의 재시도(번호가 달라 모호 아님) — OUT을 하나씩 낸다
        processes = [predecessor(), retried, process_row()]
        # match가 하나라도 있으면 N(다른 OUT은 IN 짝이 없어도).
        result, _ = self.run_case([out_a, out_b, in_], processes=processes)
        self.assertEqual(result["anomalous"], False)
        self.assertEqual(sorted(h["result"] for h in result["evidence"][0]["handoffs"]), ["match", "undetermined"])
        # match 없음 + ID 없는 OUT 섞임 → 방법 결과 보류(데이터 문제), mismatch는 OUT 1·IN 1일 때만 생긴다.
        result, _ = self.run_case([out_a, {**out_b, "messageId": None}, {**in_, "messageId": "M-9"}],
                                  processes=processes)
        self.assertEqual((result["status"], result["cause"], result["evidence"]),
                         ("not_ready", "input_missing", [{"reason": "message_id_missing"}]))


def timed_process(observation, process_id, end, start="2026-01-01T00:00:00", focus=False):
    return {**process_row(observation, process_id, "NONE", focus=focus), "startTimeRaw": start, "endTimeRaw": end}


class RequiredStep(unittest.TestCase):
    def rule(self, rule_id=REQUIRED_ID, **fields):
        return relation(rule_id, "required_step", severity="warning", **fields)

    def transaction(self, focus_end="2026-01-01T00:00:09", other_end="2026-01-01T00:00:05", steps=("IF_A_PR001",)):
        rows = [timed_process(FOCUS, OWN["process_id"], focus_end, focus=True)]
        rows += [timed_process(f"00000000-0000-4000-8000-{n:012d}", step, other_end) for n, step in
                 enumerate(steps, start=10)]
        return rows

    def test_no_transaction_row_defers_once(self):
        methods, notes = judge([self.rule(), self.rule(rule_id="hard:rel-0000000e",
                                                       scope={"interfaceId": "IF_B"})], inputs(None))
        self.assertEqual(methods, [])
        self.assertEqual(notes, [{"ruleId": None, "kind": "required_step", "result": "deferred_to_transaction",
                                  "reason": None, "waitingFor": {"layer": "T"}}])

    def test_latest_ended_process_judges_step_set(self):
        present, _ = single(self, [self.rule()], inputs(tx_row("S", "0000"), processes=self.transaction()))
        self.assertEqual((present["anomalous"], present["methodVersion"]), (False, "relation-required_step-v1"))
        self.assertEqual(present["evidence"], [{"result": "no_hit", "node": "process:TX-1:IF_A_PR002",
                                                "transaction": "transaction:TX-1", "step": PRED_NODE,
                                                "present": True}])
        missing, _ = single(self, [self.rule()],
                            inputs(tx_row("S", "0000"), processes=self.transaction(steps=("IF_A_PR003",))))
        self.assertEqual((missing["anomalous"], missing["code"], missing["riskLevel"],
                          missing["evidence"][0]["present"]), (True, "REL_REQUIRED_STEP", "warning", False))

    def test_other_decisions_and_interfaces_do_nothing(self):
        earlier = inputs(tx_row("S", "0000"), processes=self.transaction(focus_end="2026-01-01T00:00:01"))
        self.assertEqual(judge([self.rule()], earlier), ([], []))
        other_interface = inputs(tx_row("S", "0000"), processes=self.transaction())
        self.assertEqual(judge([self.rule(scope={"interfaceId": "IF_B"})], other_interface), ([], []))

    def test_transaction_level_holds(self):
        cases = {
            "transaction_row_ambiguous": (inputs(None, executions=2, ambiguous=True, processes=self.transaction()),
                                          "input_invalid", ["IF_A", "IF_B"]),
            "graph_truncated": (inputs(tx_row("S", "0000"), processes=self.transaction(), truncated=True),
                                "capacity", ["IF_A"]),
            "transaction_id_missing": (inputs(None, focus_tx="\\N"), "input_missing", ["IF_A", "IF_B"]),
        }
        both = [self.rule(), self.rule(rule_id="hard:rel-0000000e", scope={"interfaceId": "IF_B"})]
        for reason, (relation_inputs, cause, interfaces) in cases.items():
            with self.subTest(reason=reason):
                methods, notes = judge(both, relation_inputs)
                self.assertEqual(notes, [])
                self.assertEqual(len(methods), len(interfaces))
                for _, result in methods:
                    self.assertEqual((result["status"], result["cause"], result["evidence"]),
                                     ("not_ready", cause, [{"reason": reason}]))

    def test_hold_combinations(self):
        both = [self.rule(), self.rule(rule_id="hard:rel-0000000e", scope={"interfaceId": "IF_B"})]
        # 상한 초과 + 거래 행 모호 → 인터페이스를 정할 수 없어 모든 규칙에서 상한 보류(상한이 먼저).
        methods, _ = judge(both, inputs(None, executions=2, ambiguous=True, processes=self.transaction(),
                                        truncated=True))
        self.assertEqual([(r["cause"], r["evidence"]) for _, r in methods],
                         [("capacity", [{"reason": "graph_truncated"}])] * 2)
        # 거래 행 모호인데 판정 PROCESS가 가장 늦게 끝난 것이 아님 → 기록 없음(붙이는 판정이 아님).
        earlier = self.transaction(focus_end="2026-01-01T00:00:01")
        self.assertEqual(judge(both, inputs(None, executions=2, ambiguous=True, processes=earlier)), ([], []))
        # 거래 행 interface_id 없음 → 어느 규칙도 고르지 않음(평가·기록 없음).
        no_interface = inputs({**tx_row("S", "0000"), "interfaceId": None}, processes=self.transaction())
        self.assertEqual(judge(both, no_interface), ([], []))

    def test_latest_ended_order(self):
        def focus_wins(rows):
            return latest_ended_is_focus(inputs(tx_row(), processes=rows))

        late, early = "2026-01-01T00:00:09", "2026-01-01T00:00:05"
        self.assertTrue(focus_wins([timed_process(FOCUS, "A", late, focus=True), timed_process(OTHER, "B", early)]))
        self.assertFalse(focus_wins([timed_process(FOCUS, "A", None, focus=True), timed_process(OTHER, "B", early)]))
        self.assertFalse(focus_wins([timed_process(FOCUS, "A", "x", focus=True), timed_process(OTHER, "B", early)]))
        # END 같음 → START 큰 쪽, 그것도 같으면 관측 id 큰 쪽(OTHER > FOCUS).
        self.assertTrue(focus_wins([timed_process(FOCUS, "A", late, "2026-01-01T00:00:02", True),
                                    timed_process(OTHER, "B", late, "2026-01-01T00:00:01")]))
        self.assertFalse(focus_wins([timed_process(FOCUS, "A", late, focus=True), timed_process(OTHER, "B", late)]))
        # 시간대 있는 값과 없는 값이 섞이면 END로 가르지 않는다(START·관측 id로).
        self.assertFalse(focus_wins([timed_process(FOCUS, "A", "2026-01-01T00:00:09+09:00", focus=True),
                                     timed_process(OTHER, "B", early)]))


class TransactionFirstReplay(unittest.TestCase):
    """CSV 재생은 거래 행을 PROCESS보다 먼저 커밋한다. 도착 확정(최소 지연) 전에는 "없음 = 위반"을 내지 않는다."""

    def test_unconfirmed_transaction_row_does_not_make_violations(self):
        early = inputs(tx_row("S", "0000"), confirmed=False)  # 거래 행은 있으나 뒤 PROCESS가 아직 안 옴
        _, held = single(self, [relation()], early)
        self.assertEqual((held["result"], held["reason"], held["waitingFor"]),
                         ("held", "collection_incomplete", {"processId": "IF_A_PR001"}))
        rows = [timed_process(FOCUS, OWN["process_id"], "2026-01-01T00:00:09", focus=True)]
        methods, notes = judge([relation(REQUIRED_ID, "required_step")],
                               inputs(tx_row("S", "0000"), processes=rows, confirmed=False))
        self.assertEqual((methods, notes), ([], [{"ruleId": None, "kind": "required_step",
                                                  "result": "deferred_to_transaction", "reason": None,
                                                  "waitingFor": {"layer": "T"}}]))
        # 확정 뒤에는 같은 입력이 위반이다(3.1 구조 전제).
        methods, _ = judge([relation(REQUIRED_ID, "required_step")], inputs(tx_row("S", "0000"), processes=rows))
        self.assertTrue(methods[0][1]["anomalous"])

    def test_transaction_failed_uses_row_value_regardless_of_settle(self):
        from detector.hard_rules import evaluate_transaction_failed
        from test_relation_decision import selection
        result = evaluate_transaction_failed(inputs(tx_row("F", "E901"), confirmed=False), selection())
        self.assertEqual((result["anomalous"], result["riskLevel"]), (True, "critical"))


class AllowedBranch(unittest.TestCase):
    def test_absent_predecessor_value_is_not_applicable(self):
        rule = relation(BRANCH_ID, "allowed_branch", severity="info")
        for depends in (None, " \\N", ""):
            with self.subTest(depends=depends):
                _, item = single(self, [rule], inputs(None, processes=[process_row(depends=depends)]),
                                 {"depend_process_id": depends})
                self.assertEqual((item["result"], item["reason"]), ("not_applicable", "depend_process_id_missing"))
        _, start = single(self, [rule], inputs(None, processes=[process_row(depends="NONE")]),
                          {"depend_process_id": "NONE"})
        self.assertEqual(start["result"], "not_in_pattern")

    def test_information_only(self):
        rule = relation(BRANCH_ID, "allowed_branch", severity="info")
        methods, notes = judge([rule], inputs(None, processes=[predecessor(), process_row()]))
        self.assertEqual((methods, notes), ([], [{"ruleId": BRANCH_ID, "kind": "allowed_branch",
                                                  "result": "observed", "reason": None, "waitingFor": None}]))
        _, other = single(self, [rule], inputs(None, processes=[process_row(depends="X")]),
                          {"depend_process_id": "X"})
        self.assertEqual(other["result"], "not_in_pattern")


class SoftScope(unittest.TestCase):
    def test_in_scope_exact_match(self):
        rule = {**SOFT, "scope": {"processId": "IF_A_PR002", "adapterType": "HTTP", "interfaceId": "표시용"}}
        self.assertTrue(in_scope({**SOFT, "scope": None}, {}))
        self.assertTrue(in_scope(rule, OWN))
        for raw in ({**OWN, "process_id": " IF_A_PR002"}, {**OWN, "adapter_type": None},
                    {**OWN, "process_id": "\\N"}, {**OWN, "adapter_type": 1}):
            with self.subTest(raw=raw):
                self.assertFalse(in_scope(rule, raw))
        hub = {**SOFT, "scope": {"processId": "IF_A_PR002", "processHubId": "HUB"}}
        self.assertTrue(in_scope(hub, OWN))
        self.assertFalse(in_scope(hub, {**OWN, "process_hub_id": "HUB2"}))


def assembled(content, raw, relation_inputs):
    state = pin_operational_state(FakeConn(content=content, version=3))
    plan = plan_relations(state, relation_inputs if needs_relation_inputs(state, raw) else None, raw)
    methods = execute_methods(attempt_v04._tasks(state, [], raw, None, plan), 3000,
                              started_at=time.monotonic()).methods
    evidence = build_evidence(POLICY, methods, REVISIONS, state.hard_rules, source_id=uuid.uuid4(), telemetry={},
                              observation={}, input_manifest_hash="b" * 64, evaluated_hard_ids=plan.hard_ids,
                              relation=plan.evidence_fields())
    return state, plan, methods, evidence


class Assembly(unittest.TestCase):
    RAW = {**RAW, **OWN}

    def test_violation_decides_anomaly_with_rule_severity(self):
        scoped = {**SOFT, "id": "soft:elapsed_ms-00000001", "scope": {"processId": "IF_A_PR002"}}
        elsewhere = {**SOFT, "id": "soft:elapsed_ms-00000002", "scope": {"processId": "IF_A_PR009"}}
        content = v3(BASE_HARD, soft_rules=[scoped, elsewhere],
                     relation_rules=[relation(severity="warning"), relation(BRANCH_ID, "allowed_branch")])
        _, plan, methods, evidence = assembled(content, {**self.RAW, "depend_process_id": "NONE"},
                                               inputs(None, processes=[process_row(depends="NONE")]))
        self.assertEqual([m["methodId"] for m in evidence["methodResults"]][-2:], [PRECEDES_ID, scoped["id"]])
        self.assertEqual((evidence["categoryResults"]["hard"]["n"], evidence["categoryResults"]["soft"]["n"]), (5, 1))
        composite = evidence["composite"]
        self.assertEqual((composite["grade"], composite["basisMethods"], composite["risk"]["level"]),
                         ("anomaly", [PRECEDES_ID], "warning"))
        self.assertEqual(evidence["relationNotes"], [{"ruleId": BRANCH_ID, "kind": "allowed_branch",
                                                      "result": "not_in_pattern", "reason": None,
                                                      "waitingFor": None}])
        # v3 투영 rules[]·ruleSeverities에는 관계 규칙을 넣지 않는다(근거는 methodResults).
        self.assertNotIn("rel-0000000a", [rule["ruleKey"] for rule in evidence["rules"]])
        self.assertEqual(evidence["ruleSeverities"], [])
        # 중간 기록도 같은 과제 목록이라 n이 같다.
        interim = build_interim_payload(POLICY, methods, REVISIONS, 1.0)
        self.assertEqual(interim["categoryResults"]["hard"]["n"], 5)
        self.assertEqual(plan.relation_results[PRECEDES_ID]["anomalous"], True)

    def test_held_rule_is_not_counted(self):
        content = v3(BASE_HARD + [hard(TRANSACTION_FAILED, True, FAILED_SEVERITY)],
                     relation_rules=[relation(), relation(REQUIRED_ID, "required_step")])
        _, plan, _, evidence = assembled(content, self.RAW, inputs(None))
        self.assertEqual(evidence["categoryResults"]["hard"]["n"], 4)
        self.assertEqual([note["result"] for note in evidence["relationNotes"]],
                         ["held", "held", "deferred_to_transaction"])
        self.assertEqual(evidence["composite"]["grade"], "normal")
        self.assertNotIn(PRECEDES_ID, plan.hard_ids)

    def test_relation_severity_mismatch_rejected(self):
        content = v3(BASE_HARD, relation_rules=[relation(severity="warning")])
        state, plan, methods, _ = assembled(content, {**self.RAW, "depend_process_id": "NONE"},
                                            inputs(None, processes=[process_row(depends="NONE")]))
        tampered = [{**m, "severity": "critical", "riskLevel": "critical"} if m["methodId"] == PRECEDES_ID else m
                    for m in methods]
        for evaluated, results in ((plan.hard_ids, tampered), ((*plan.hard_ids, "hard:rel-ffffffff"), methods)):
            with self.subTest(evaluated=evaluated[-1]):
                with self.assertRaisesRegex(ValueError, "^hard_rule_selection_mismatch$"):
                    build_evidence(POLICY, results, REVISIONS, state.hard_rules, source_id=uuid.uuid4(),
                                   telemetry={}, observation={}, input_manifest_hash="b" * 64,
                                   evaluated_hard_ids=evaluated)

    def test_timeout_keeps_relation_version(self):
        result = unresolved_hard_result(PRECEDES_ID, "timeout", method_version="relation-precedes-v1")
        self.assertEqual((result["methodVersion"], result["cause"], result["category"]),
                         ("relation-precedes-v1", "timeout", "hard"))


if __name__ == "__main__":
    unittest.main()

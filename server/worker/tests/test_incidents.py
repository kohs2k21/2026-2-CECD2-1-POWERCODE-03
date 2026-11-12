import hashlib
import json
import random
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from detector import incidents as inc
from detector.incidents import (DEFAULT_CRITERIA, INCIDENT_LOCK_CLASS, DecisionCandidate, OpenIncident,
                                active_execution_count, group_decisions, group_key, graph_value_absent,
                                incident_id, merge_root, occurred_time, resolve_transaction_response_code, shown,
                                transaction_node_id)
from detector.jobs import MANIFEST_LOCK_CLASS

SOURCE = "11111111-1111-1111-1111-111111111111"
KST = "Asia/Seoul"
BASE = datetime(2000, 1, 1, 0, 0, tzinfo=timezone.utc)
DECIDED = datetime(2000, 1, 1, 8, 0, tzinfo=timezone.utc)


def raw(at: datetime) -> str:
    """UTC 시각을 시간대 없는 KST 원천 문자열로 바꾼다."""
    return (at + timedelta(hours=9)).replace(tzinfo=None).isoformat(sep=" ")


def cand(n, at=None, *, rc="E001", channel="CH1", grade="anomaly", start=None, end="auto", source=SOURCE, tx=None):
    return DecisionCandidate(
        decision_id=f"d{n:03d}", event_id=n, execution_id=f"e{n:03d}", source_id=source, grade=grade,
        decided_at=DECIDED, end_time_raw=raw(at) if end == "auto" and at is not None else (None if end == "auto" else end),
        start_time_raw=start, transaction_id=tx if tx is not None else f"TX-{n}", transaction_response_code=rc,
        channel_id=channel)


def minutes(m, s=0, us=0):
    return BASE + timedelta(minutes=m, seconds=s, microseconds=us)


def run(cands, state=(), assigned=()):
    return group_decisions(SOURCE, cands, state, assigned, source_timezone=KST)


def apply(state, result):
    """결과를 반영한 다음 실행의 입력 상태(열린 사건·이미 배정된 판정)를 만든다.

    반영하면서 DB 불변식(019 UNIQUE·CHECK·PK와 Detector가 지킬 조건)을 확인한다: merged_into는 사건당 최대 1건,
    대상은 그 시점에 병합되지 않은 사건, 자기 병합 불가, continues_from은 자기 자신이 아니고 사건 사이에서 겹치지 않음.
    """
    by_id = {s.incident_id: dict(s.__dict__) for s in state}
    for new in result.incidents:
        assert new.incident_id not in by_id, ("duplicate_incident", new.incident_id)
        assert new.continues_from != new.incident_id, ("self_continuation", new.incident_id)
        if new.continues_from:
            assert new.continues_from in by_id, ("continues_from_missing", new.continues_from)
        by_id[new.incident_id] = dict(incident_id=new.incident_id, group_key=new.group_key, opened_at=new.opened_at,
                                      last_member_at=new.opened_at, continues_from=new.continues_from,
                                      has_continuation=False, merged_into=None)
        if new.continues_from:
            by_id[new.continues_from]["has_continuation"] = True
    previous = [row["continues_from"] for row in by_id.values() if row["continues_from"]]
    assert len(previous) == len(set(previous)), ("continues_from_not_unique", previous)
    for member in result.members:
        row = by_id[member.incident_id]
        row["last_member_at"] = max(row["last_member_at"], member.occurred_at)
    for merge in result.merges:
        assert merge.incident_id != merge.target_incident_id, ("self_merge", merge.incident_id)
        assert by_id[merge.incident_id]["merged_into"] is None, ("merged_twice", merge.incident_id)
        assert by_id[merge.target_incident_id]["merged_into"] is None, ("target_already_merged", merge.target_incident_id)
        by_id[merge.incident_id]["merged_into"] = merge.target_incident_id
    return [OpenIncident(**row) for row in by_id.values()]


def partition(states, members):
    """병합을 푼 뒤 판정 묶음(사건 뿌리별 decision_id 집합)."""
    merged = {s.incident_id: s.merged_into for s in states if s.merged_into}
    groups = {}
    for member in members:
        groups.setdefault(merge_root(member.incident_id, merged), set()).add(member.decision_id)
    return sorted(sorted(group) for group in groups.values())


class AbsenceAndIdTest(unittest.TestCase):
    def test_absence_matches_graph_rule(self):
        for value in (None, "", "  ", "\\N", " \\N ", "\t\\N\r\n", "\t\r\n "):
            self.assertTrue(graph_value_absent(value), repr(value))
        for value in (" A1 ", "NONE", "﻿", "\u0085", "\u001c", " ", "0"):
            self.assertFalse(graph_value_absent(value), repr(value))

    def test_transaction_node_id_escapes(self):
        self.assertEqual(transaction_node_id("TX-0001"), "transaction:TX-0001")
        self.assertEqual(transaction_node_id("a:b"), "transaction:a%3Ab")
        self.assertEqual(transaction_node_id("TX%3A1"), "transaction:TX%253A1")
        self.assertEqual(transaction_node_id(" A1 "), "transaction: A1 ")
        self.assertIsNone(transaction_node_id(" \\N "))

    def test_incident_id_is_uuid5_over_unique_columns(self):
        key = "response_code:E%3A01"
        expected_name = ":".join([SOURCE.replace(":", "%3A"), "incident-gap-v1", "response_code%3AE%253A01",
                                  "2000-01-01T00%3A00%3A00.000000+00%3A00"])
        self.assertEqual(incident_id(SOURCE, "incident-gap-v1", key, BASE),
                         str(uuid.uuid5(inc.INCIDENT_NAMESPACE, expected_name)))
        kst = BASE.astimezone(timezone(timedelta(hours=9)))
        self.assertEqual(incident_id(SOURCE, "incident-gap-v1", key, kst), incident_id(SOURCE, "incident-gap-v1", key, BASE))
        self.assertNotEqual(incident_id(SOURCE, "incident-gap-v2", key, BASE), incident_id(SOURCE, "incident-gap-v1", key, BASE))

    def test_lock_class_is_distinct(self):
        self.assertEqual(INCIDENT_LOCK_CLASS, 81741)
        self.assertNotIn(INCIDENT_LOCK_CLASS, {81736, 81739, MANIFEST_LOCK_CLASS})

    def test_criteria_document(self):
        doc = DEFAULT_CRITERIA.document()
        self.assertEqual(doc["version"], "incident-gap-v1")
        self.assertEqual((doc["gapSeconds"], doc["maxSpanSeconds"]), (300, 3600))
        self.assertEqual(doc["keys"], ["response_code", "channel_id"])
        self.assertEqual(doc["successResponseCodes"], ["0000", "S"])
        self.assertEqual(doc["timeFallback"], "decided_at")
        self.assertEqual(doc["assumedTimezone"], "Asia/Seoul")
        self.assertIn("process_hub_id", doc["excludedKeys"])
        with self.assertRaises(ValueError):
            inc.Criteria(max_span=timedelta(minutes=1))
        for name in ("Not/AZone", "", None):
            with self.assertRaisesRegex(ValueError, "invalid_incident_assumed_timezone"):
                inc.Criteria(assumed_timezone=name)


class KeyTest(unittest.TestCase):
    def test_failure_code_key_and_verbatim_value(self):
        self.assertEqual(group_key(cand(1, rc="E001")), "response_code:E001")
        self.assertEqual(group_key(cand(1, rc=" E001")), "response_code: E001")
        self.assertEqual(group_key(cand(1, rc="E:1")), "response_code:E%3A1")

    def test_success_and_absent_codes_fall_back_to_channel(self):
        for rc in ("0000", "S", " 0000 ", "\tS\r\n", None, "", "  ", "\\N", " \\N "):
            self.assertEqual(group_key(cand(1, rc=rc, channel="CH:1")), "channel_id:CH%3A1", repr(rc))

    def test_no_key_when_channel_absent(self):
        self.assertIsNone(group_key(cand(1, rc="0000", channel=" \\N ")))
        result = run([cand(1, minutes(0), rc=None, channel=None)])
        self.assertEqual((result.incidents, result.members), ((), ()))
        self.assertEqual(result.skipped[0].reason, "no_group_key")

    def test_transaction_response_code_candidates(self):
        self.assertIsNone(resolve_transaction_response_code([]))
        self.assertEqual(resolve_transaction_response_code([("t1", 1, "9999"), ("t1", 2, "E001")]), "E001")
        self.assertIsNone(resolve_transaction_response_code([("t1", 1, "E001"), ("t2", 1, "E001")]))
        # 후보가 없거나 여럿이면 None → 다음 기준(channel)
        self.assertEqual(group_key(cand(1, rc=resolve_transaction_response_code([]), channel="CH1")), "channel_id:CH1")

    def test_only_v4_anomaly_grades(self):
        cands = [cand(1, minutes(0), grade=None), cand(2, minutes(0), grade="normal"),
                 cand(3, minutes(0), grade="undetermined"), cand(4, minutes(0), grade="anomaly"),
                 cand(5, minutes(1), grade="anomaly_sign")]
        result = run(cands)
        self.assertEqual([m.decision_id for m in result.members], ["d004", "d005"])
        self.assertEqual({s.reason for s in result.skipped}, {"grade_not_eligible"})


class TimeTest(unittest.TestCase):
    def test_end_then_start_then_decided_at(self):
        self.assertEqual(occurred_time(cand(1, minutes(3), start=raw(minutes(0))), KST), (minutes(3), "process_time_raw"))
        self.assertEqual(occurred_time(cand(1, end=None, start=raw(minutes(1))), KST), (minutes(1), "process_time_raw"))
        self.assertEqual(occurred_time(cand(1, end="bad", start=raw(minutes(1))), KST), (minutes(1), "process_time_raw"))
        self.assertEqual(occurred_time(cand(1, end=" \\N ", start="bad"), KST), (DECIDED, "decided_at"))

    def test_naive_raw_with_unverified_source_assumes_seoul(self):
        # (a) 오프셋 없는 원문 + 원본 시간대 미검증(None) → Asia/Seoul 가정, 출처 process_time_raw
        self.assertEqual(occurred_time(cand(1, minutes(0)), None), (minutes(0), "process_time_raw"))
        self.assertEqual(occurred_time(cand(1, end="2000-01-01 09:00:00"), None), (minutes(0), "process_time_raw"))
        self.assertEqual(occurred_time(cand(1, end=None, start="2000-01-01T09:01:00"), None),
                         (minutes(1), "process_time_raw"))
        # 가정 시간대는 인자로 바꿀 수 있다(기준 문서 assumedTimezone)
        self.assertEqual(occurred_time(cand(1, end="2000-01-01 00:00:00"), None, "UTC"), (minutes(0), "process_time_raw"))

    def test_explicit_offset_raw_is_kept(self):
        # (b) 오프셋이 있는 원문은 원본 시간대·가정 시간대와 무관하게 그 오프셋을 쓴다
        for zone in (None, KST, "America/New_York"):
            self.assertEqual(occurred_time(cand(1, end="2000-01-01T09:00:00+09:00"), zone),
                             (minutes(0), "process_time_raw"), zone)
            self.assertEqual(occurred_time(cand(1, end="2000-01-01T00:00:00+00:00"), zone),
                             (minutes(0), "process_time_raw"), zone)
            self.assertEqual(occurred_time(cand(1, end="1999-12-31T19:00:00-05:00"), zone),
                             (minutes(0), "process_time_raw"), zone)

    def test_verified_source_zone_is_used_instead_of_assumption(self):
        # (c) 검증된 원본 시간대가 서울이 아니면 그 시간대로 붙인다
        self.assertEqual(occurred_time(cand(1, end="1999-12-31 19:00:00"), "America/New_York"),
                         (minutes(0), "process_time_raw"))
        self.assertEqual(occurred_time(cand(1, end="2000-01-01 00:00:00"), "UTC"), (minutes(0), "process_time_raw"))
        result = group_decisions(SOURCE, [cand(1, end="2000-01-01 00:00:00")], source_timezone="UTC")
        self.assertEqual((result.members[0].occurred_at, result.members[0].occurred_basis),
                         (minutes(0), "process_time_raw"))

    def test_unparseable_raw_falls_back_to_decided_at(self):
        # (d) 해석할 수 없으면 가정 시간대가 있어도 decided_at
        for zone in (None, KST):
            self.assertEqual(occurred_time(cand(1, end="bad", start="2000-13-01 00:00:00"), zone),
                             (DECIDED, "decided_at"), zone)
            self.assertEqual(occurred_time(cand(1, end=None, start=" \\N "), zone), (DECIDED, "decided_at"), zone)

    def test_fallback_member_is_counted(self):
        result = run([cand(1, minutes(0)), cand(2, end=None)])
        self.assertEqual([m.occurred_basis for m in result.members], ["process_time_raw", "decided_at"])
        self.assertEqual(result.fallback_members, 1)
        self.assertEqual({i.time_basis for i in result.incidents}, {"process_time_raw"})

    def test_unverified_source_groups_on_assumed_time(self):
        # 원본 시간대 미검증(기본값 None)이어도 오프셋 없는 원문으로 묶고 대체 구성원이 없다
        result = group_decisions(SOURCE, [cand(1, minutes(0)), cand(2, minutes(4)), cand(3, minutes(20))])
        self.assertEqual([m.occurred_at for m in result.members], [minutes(0), minutes(4), minutes(20)])
        self.assertEqual({m.occurred_basis for m in result.members}, {"process_time_raw"})
        self.assertEqual(result.fallback_members, 0)
        self.assertEqual([i.opened_at for i in result.incidents], [minutes(0), minutes(20)])
        self.assertEqual({i.time_basis for i in result.incidents}, {"process_time_raw"})
        self.assertEqual({i.criteria["assumedTimezone"] for i in result.incidents}, {"Asia/Seoul"})


class GroupingTest(unittest.TestCase):
    def test_gap_boundary(self):
        result = run([cand(1, minutes(0)), cand(2, minutes(5)), cand(3, minutes(10, us=1))])
        self.assertEqual(len(result.incidents), 2)
        first, second = result.incidents
        self.assertEqual([m.incident_id for m in result.members], [first.incident_id] * 2 + [second.incident_id])
        self.assertIsNone(second.continues_from)
        self.assertEqual(first.opened_at, minutes(0))
        self.assertEqual(first.criteria["timeFallback"], "decided_at")

    def test_keys_are_separate(self):
        result = run([cand(1, minutes(0), rc="E001"), cand(2, minutes(1), rc="0000"), cand(3, minutes(2), rc="E002")])
        self.assertEqual([i.group_key for i in result.incidents],
                         ["response_code:E001", "channel_id:CH1", "response_code:E002"])

    def test_max_span_exactly_60_joins(self):
        cands = [cand(n, minutes(5 * n)) for n in range(13)]  # 0..60분
        result = run(cands)
        self.assertEqual(len(result.incidents), 1)
        self.assertEqual(len(result.members), 13)

    def test_max_span_exceeded_opens_continuation(self):
        cands = [cand(n, minutes(5 * n)) for n in range(12)] + [cand(14, minutes(58)), cand(12, minutes(60, us=1)),
                                                                  cand(13, minutes(63))]
        result = run(cands)
        self.assertEqual(len(result.incidents), 2)
        first, second = result.incidents
        self.assertEqual(second.continues_from, first.incident_id)
        self.assertEqual(second.opened_at, minutes(60, us=1))
        self.assertEqual([m.incident_id for m in result.members[-2:]], [second.incident_id] * 2)
        self.assertEqual(result.merges, ())

    def test_continuation_is_linear(self):
        state = [OpenIncident("A", "response_code:E001", minutes(0), minutes(58), has_continuation=True)]
        # A의 구간 밖이고 A에는 이미 다음 구간이 있다 → 이어짐 없이 새 사건
        result = run([cand(1, minutes(61))], state)
        self.assertIsNone(result.incidents[0].continues_from)

    def test_bridge_merges_into_older(self):
        state = [OpenIncident("A", "response_code:E001", minutes(0), minutes(2)),
                 OpenIncident("B", "response_code:E001", minutes(9), minutes(12))]
        result = run([cand(1, minutes(6))], state)
        self.assertEqual(result.incidents, ())
        self.assertEqual(result.members[0].incident_id, "A")
        self.assertEqual([(m.incident_id, m.target_incident_id) for m in result.merges], [("B", "A")])
        # 병합된 B의 마지막 시각을 A가 이어받아 B 근처 판정도 A로 간다
        nxt = run([cand(2, minutes(16))], apply(state, result))
        self.assertEqual(nxt.members[0].incident_id, "B")  # B 자기 구간, 읽을 때 뿌리 A
        self.assertEqual(nxt.merges, ())

    def test_merged_incident_is_never_target(self):
        state = [OpenIncident("A", "response_code:E001", minutes(0), minutes(2)),
                 OpenIncident("B", "response_code:E001", minutes(1), minutes(3), merged_into="A")]
        result = run([cand(1, minutes(4))], state)
        self.assertEqual(result.members[0].incident_id, "A")
        self.assertEqual(result.merges, ())

    def test_late_decision_before_start_opens_and_absorbs(self):
        state = [OpenIncident("A", "response_code:E001", minutes(10), minutes(12))]
        result = run([cand(1, minutes(6))], state)
        new = result.incidents[0]
        self.assertEqual(new.opened_at, minutes(6))
        self.assertEqual([(m.incident_id, m.target_incident_id) for m in result.merges], [("A", new.incident_id)])

    def test_late_decision_does_not_merge_own_continuation(self):
        state = [OpenIncident("T1", "response_code:E001", minutes(0), minutes(58), has_continuation=True),
                 OpenIncident("T2", "response_code:E001", minutes(61), minutes(61), continues_from="T1")]
        result = run([cand(1, minutes(59))], state)
        self.assertEqual(result.members[0].incident_id, "T1")
        self.assertEqual(result.merges, ())

    def test_collision_with_merged_incident_joins_it(self):
        state = [OpenIncident(incident_id(SOURCE, "incident-gap-v1", "response_code:E001", minutes(200)),
                              "response_code:E001", minutes(200), minutes(201), merged_into="X")]
        result = run([cand(1, minutes(200))], state)
        self.assertEqual(result.incidents, ())
        self.assertEqual(result.members[0].incident_id, state[0].incident_id)

    def test_member_links_to_graph_transaction(self):
        result = run([cand(1, minutes(0), tx="a:b"), cand(2, minutes(1), tx=" \\N ")])
        self.assertEqual([m.transaction_node_id for m in result.members], ["transaction:a%3Ab", None])
        self.assertEqual(result.incidents[0].node_id, "incident:" + result.incidents[0].incident_id)

    def test_source_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            run([cand(1, minutes(0), source="22222222-2222-2222-2222-222222222222")])


class IdempotencyTest(unittest.TestCase):
    def cands(self):
        return [cand(n, minutes(m)) for n, m in enumerate((0, 3, 7, 30, 33, 61, 64, 90))] + \
               [cand(20, minutes(1), rc="0000"), cand(21, minutes(2), rc=None, channel=None)]

    def test_rerun_after_apply_has_no_actions(self):
        first = run(self.cands())
        again = run(self.cands(), apply([], first), [m.decision_id for m in first.members])
        self.assertEqual((again.incidents, again.members, again.merges), ((), (), ()))
        self.assertEqual({s.reason for s in again.skipped}, {"already_member", "no_group_key"})

    def test_same_input_any_order_same_result(self):
        expected = run(self.cands())
        shuffled = self.cands()
        random.Random(7).shuffle(shuffled)
        self.assertEqual(run(shuffled).incidents, expected.incidents)
        self.assertEqual(run(shuffled).members, expected.members)

    def test_duplicate_candidate_in_one_run(self):
        result = run([cand(1, minutes(0)), cand(1, minutes(0))])
        self.assertEqual(len(result.members), 1)
        self.assertEqual(result.skipped[0].reason, "already_member")

    def test_incremental_equals_batch_after_merge(self):
        batch = run([cand(1, minutes(0)), cand(2, minutes(4)), cand(3, minutes(8))])
        step1 = run([cand(1, minutes(0)), cand(3, minutes(8))])
        state = apply([], step1)
        step2 = run([cand(2, minutes(4))], state, ["d001", "d003"])
        final = apply(state, step2)
        self.assertEqual(len(step2.merges), 1)
        self.assertEqual(partition(apply([], batch), batch.members),
                         partition(final, step1.members + step2.members))


class ReadRuleTest(unittest.TestCase):
    def test_active_count_one_per_execution_and_release(self):
        members = [("d1", "e1"), ("d2", "e1"), ("d3", "e2"), ("d4", "e3")]
        latest = {"e1": "anomaly", "e2": "normal", "e3": "anomaly_sign"}
        self.assertEqual(active_execution_count(members, latest), 2)
        self.assertTrue(shown(2))
        self.assertFalse(shown(active_execution_count(members, {"e1": "anomaly"})))

    def test_merge_root(self):
        self.assertEqual(merge_root("C", {"C": "B", "B": "A"}), "A")
        with self.assertRaises(ValueError):
            merge_root("A", {"A": "B", "B": "A"})


class ReviewCaseTest(unittest.TestCase):
    def test_null_execution_rows_are_ambiguous(self):
        self.assertIsNone(resolve_transaction_response_code([(None, None, "E001")]))
        self.assertIsNone(resolve_transaction_response_code([(None, None, "E001"), (None, None, "E002")]))
        self.assertIsNone(resolve_transaction_response_code([("t1", 1, "E001"), (None, None, "E001")]))
        rc = resolve_transaction_response_code([(None, None, "E001"), ("t1", 2, "E001")])
        self.assertEqual(group_key(cand(1, rc=rc, channel="CH1")), "channel_id:CH1")

    def test_invalid_timezone_falls_back(self):
        # 검증된 원본의 시간대 이름이 잘못됐으면 가정 시간대로 덮지 않고 decided_at(기존 동작)
        for name in ("Not/AZone", "../etc", ""):
            self.assertEqual(occurred_time(cand(1, minutes(0)), name), (DECIDED, "decided_at"), name)
        result = group_decisions(SOURCE, [cand(1, minutes(0))], source_timezone="Not/AZone")
        self.assertEqual(result.members[0].occurred_basis, "decided_at")

    def test_before_boundary_exactly_gap(self):
        state = [OpenIncident("A", "response_code:E001", minutes(10), minutes(12))]
        exact = run([cand(1, minutes(5))], state)
        self.assertEqual([(m.incident_id, m.target_incident_id) for m in exact.merges],
                         [("A", exact.incidents[0].incident_id)])
        outside = run([cand(1, minutes(5, us=-1))], state)
        self.assertEqual(outside.merges, ())
        self.assertEqual(len(outside.incidents), 1)

    def test_late_decision_inside_merged_component(self):
        # A 0~48분, B 50~100분이 A로 병합. 80분 판정은 B 구간 → B(뿌리 A)에 넣고 이어짐을 만들지 않는다
        state = [OpenIncident("A", "response_code:E001", minutes(0), minutes(48)),
                 OpenIncident("B", "response_code:E001", minutes(50), minutes(100), merged_into="A")]
        result = run([cand(1, minutes(80))], state)
        self.assertEqual(result.incidents, ())
        self.assertEqual(result.members[0].incident_id, "B")
        self.assertEqual(result.merges, ())

    def test_largest_overflow_candidate_is_continued(self):
        state = [OpenIncident("P1", "response_code:E001", minutes(0), minutes(57)),
                 OpenIncident("P2", "response_code:E001", minutes(1), minutes(58))]
        result = run([cand(1, minutes(62))], state)
        self.assertEqual(result.incidents[0].continues_from, "P2")
        self.assertEqual(result.merges, ())

    def test_continuation_and_before_merge_together(self):
        state = [OpenIncident("P", "response_code:E001", minutes(0), minutes(58)),
                 OpenIncident("Q", "response_code:E001", minutes(64), minutes(66))]
        result = run([cand(1, minutes(61))], state)
        new = result.incidents[0]
        self.assertEqual(new.continues_from, "P")
        self.assertEqual([(m.incident_id, m.target_incident_id) for m in result.merges], [("Q", new.incident_id)])

    def test_stateful_input_order_does_not_matter(self):
        state = [OpenIncident("A", "response_code:E001", minutes(0), minutes(20)),
                 OpenIncident("B", "response_code:E001", minutes(30), minutes(40)),
                 OpenIncident("C", "channel_id:CH1", minutes(0), minutes(3), merged_into="D"),
                 OpenIncident("D", "channel_id:CH1", minutes(1), minutes(2))]
        cands = [cand(1, minutes(24)), cand(2, minutes(27)), cand(3, minutes(45)), cand(4, minutes(6), rc=None),
                 cand(5, minutes(70))]
        expected = run(cands, state)
        for order in ((list(reversed(cands)), list(reversed(state))), (cands[2:] + cands[:2], state[1:] + state[:1])):
            got = run(*order)
            self.assertEqual((got.incidents, got.members, got.merges), (expected.incidents, expected.members,
                                                                        expected.merges))
        self.assertEqual([(m.incident_id, m.target_incident_id) for m in expected.merges], [("B", "A")])


def continuation_cycle_free(states):
    """병합을 푼 묶음 사이 continues_from 그래프에 고리(자기 자신 포함)가 없는지."""
    merged = {s.incident_id: s.merged_into for s in states if s.merged_into}
    edges = {}
    for state in states:
        if state.continues_from:
            edges.setdefault(merge_root(state.continues_from, merged), set()).add(merge_root(state.incident_id, merged))
    done, active = set(), set()

    def visit(node):
        if node in active:
            return False
        if node in done:
            return True
        active.add(node)
        ok = all(visit(nxt) for nxt in edges.get(node, ()))
        active.discard(node)
        done.add(node)
        return ok
    return all(visit(node) for node in list(edges))


class GroupChainTest(unittest.TestCase):
    def test_no_merge_into_group_it_continues_from(self):
        # 리뷰 재현 입력: 5회 실행, 매 회 결과를 상태에 반영
        batches = [[2 + 5 * i for i in range(13)], [0], [5 * i for i in range(1, 12)] + [57], [63], [59]]
        state, assigned, n, results = [], set(), 0, []
        for batch in batches:
            cands = []
            for m in batch:
                cands.append(cand(n, minutes(m)))
                n += 1
            result = run(cands, state, assigned)
            results.append(result)
            state = apply(state, result)
            assigned |= {m.decision_id for m in result.members}
        by_open = {s.opened_at: s for s in state}
        a, b, nxt = by_open[minutes(0)], by_open[minutes(2)], by_open[minutes(63)]
        self.assertEqual(b.merged_into, a.incident_id)
        self.assertEqual(nxt.continues_from, b.incident_id)
        self.assertEqual(results[4].merges, ())
        self.assertIsNone(nxt.merged_into)
        self.assertEqual(results[4].members[0].incident_id, a.incident_id)
        self.assertTrue(continuation_cycle_free(state))

    def test_root_chain_is_compared(self):
        # target T는 R로 병합됐고 사슬이 다르다. O는 뿌리 R에서 이어진다 → 병합하지 않는다
        state = [OpenIncident("R", "response_code:E001", minutes(0), minutes(50), has_continuation=True),
                 OpenIncident("T", "response_code:E001", minutes(52), minutes(54), merged_into="R"),
                 OpenIncident("O", "response_code:E001", minutes(61), minutes(61), continues_from="R")]
        result = run([cand(1, minutes(57))], state)
        self.assertEqual(result.members[0].incident_id, "T")
        self.assertEqual(result.merges, ())
        self.assertTrue(continuation_cycle_free(apply(state, result)))

    def test_unrelated_group_merges_into_root_not_component(self):
        state = [OpenIncident("R", "response_code:E001", minutes(0), minutes(50)),
                 OpenIncident("T", "response_code:E001", minutes(52), minutes(54), merged_into="R"),
                 OpenIncident("O", "response_code:E001", minutes(61), minutes(61))]
        result = run([cand(1, minutes(57))], state)
        self.assertEqual(result.members[0].incident_id, "T")
        self.assertEqual([(m.incident_id, m.target_incident_id) for m in result.merges], [("O", "R")])

    def test_no_merge_through_third_group(self):
        # 묶음 G = {g}, g는 묶음 K의 k1에서 이어짐. 묶음 K = {k1, k2(→k1 병합)}, k2는 묶음 H의 h에서 이어짐.
        # 묶음 그래프 G → K → H. 사슬 시작 집합은 G {k1}, H {h}로 겹치지 않지만 G·H를 병합하면 GH → K → GH 고리
        state = [OpenIncident("k1", "response_code:E001", minutes(0), minutes(58), has_continuation=True),
                 OpenIncident("g", "response_code:E001", minutes(61), minutes(63), continues_from="k1"),
                 OpenIncident("h", "response_code:E001", minutes(66), minutes(124), has_continuation=True),
                 OpenIncident("k2", "response_code:E001", minutes(127), minutes(127), continues_from="h",
                              merged_into="k1")]
        result = run([cand(1, minutes(64))], state)  # g에 참여, h 시작 직전(gap 안)
        self.assertEqual(result.incidents, ())
        self.assertEqual(result.members[0].incident_id, "g")
        self.assertEqual(result.merges, ())
        self.assertTrue(continuation_cycle_free(apply(state, result)))

    def test_unknown_root_on_path_refuses_merge(self):
        # 상태 밖 사건 X로 병합된 u가 h에서 이어지고 g는 u에서 이어진다 → 경로가 모르는 묶음을 거치므로 병합하지 않는다
        state = [OpenIncident("u", "response_code:E001", minutes(0), minutes(58), has_continuation=True,
                              continues_from="h", merged_into="X"),
                 OpenIncident("g", "response_code:E001", minutes(61), minutes(63), continues_from="u"),
                 OpenIncident("h", "response_code:E001", minutes(66), minutes(70), has_continuation=True)]
        result = run([cand(1, minutes(64))], state)
        self.assertEqual(result.members[0].incident_id, "g")
        self.assertEqual(result.merges, ())

    def test_more_invalid_timezones_fall_back(self):
        for name in ("Asia", "A" * 5000, 123):
            self.assertEqual(occurred_time(cand(1, minutes(0)), name), (DECIDED, "decided_at"), repr(name)[:20])



class StoreHelperTest(unittest.TestCase):
    def test_source_timezone_only_from_verified_clock_basis(self):
        from detector.incidents_store import _source_timezone

        class Conn:
            def __init__(self, value):
                self.value = value

            def execute(self, query, params):
                return self

            def fetchone(self):
                return (self.value,)

        cases = [({"status": "unknown"}, None), ({"status": "unknown", "timezone": "UTC"}, None), ("x", None),
                 ({"status": "verified", "timezone": "UTC"}, "UTC"), ({"status": "verified"}, ""),
                 ({"status": "verified", "timezone": 9}, "")]
        for basis, expected in cases:
            self.assertEqual(_source_timezone(Conn(basis), SOURCE), expected, basis)
        # 미검증 → 가정 시간대, 검증됐지만 시간대 없음 → decided_at
        self.assertEqual(occurred_time(cand(1, minutes(0)), _source_timezone(Conn({"status": "unknown"}), SOURCE)),
                         (minutes(0), "process_time_raw"))
        self.assertEqual(occurred_time(cand(1, minutes(0)), _source_timezone(Conn({"status": "verified"}), SOURCE)),
                         (DECIDED, "decided_at"))

    def test_state_windows_per_key(self):
        cands = [cand(1, minutes(10)), cand(2, minutes(20)), cand(3, minutes(30), rc="0000"),
                 cand(4, minutes(40), grade="normal"), cand(5, minutes(50), rc=None, channel=None), cand(6, minutes(0))]
        windows = inc.state_windows(cands, ["d006"], source_timezone=KST)
        self.assertEqual(windows, {"channel_id:CH1": (minutes(30 - 65), minutes(35)),
                                   "response_code:E001": (minutes(10 - 65), minutes(25))})
        self.assertEqual(inc.state_windows([cand(1, minutes(0))], ["d001"]), {})

    def test_state_window_covers_every_rule_boundary(self):
        # 창 경계의 사건이 참여·이어짐·시작 직전 병합에 실제로 쓰인다(창이 좁으면 결과가 바뀜)
        t = minutes(200)
        windows = inc.state_windows([cand(1, t)], source_timezone=KST)
        low, high = windows["response_code:E001"]
        overflow = OpenIncident("P", "response_code:E001", low, low + timedelta(minutes=60))
        self.assertEqual(run([cand(1, t)], [overflow]).incidents[0].continues_from, "P")
        before = OpenIncident("Q", "response_code:E001", high, high)
        self.assertEqual([(m.incident_id) for m in run([cand(1, t)], [before]).merges], ["Q"])

    def test_check_result_scope(self):
        state = [OpenIncident("A", "response_code:E001", minutes(0), minutes(58))]
        result = run([cand(1, minutes(61))], state)
        scope = {"A": (SOURCE, "incident-gap-v1")}
        inc.check_result_scope(result, SOURCE, "incident-gap-v1", scope)
        with self.assertRaisesRegex(ValueError, "incident_state_scope_mismatch"):
            inc.check_result_scope(result, SOURCE, "incident-gap-v1", {"A": (SOURCE, "incident-gap-v0")})
        with self.assertRaisesRegex(ValueError, "incident_continues_from_out_of_scope"):
            inc.check_result_scope(result, SOURCE, "incident-gap-v1", {})
        with self.assertRaisesRegex(ValueError, "incident_new_scope_mismatch"):
            inc.check_result_scope(result, SOURCE, "incident-gap-v2", {})
        joined = run([cand(2, minutes(1))], state)
        with self.assertRaisesRegex(ValueError, "incident_member_out_of_scope"):
            inc.check_result_scope(joined, SOURCE, "incident-gap-v1", {})
        merged = run([cand(3, minutes(59))], state + [OpenIncident("B", "response_code:E001", minutes(62), minutes(63))])
        self.assertEqual(len(merged.merges), 1)
        with self.assertRaisesRegex(ValueError, "incident_merge_out_of_scope"):
            inc.check_result_scope(merged, SOURCE, "incident-gap-v1", {"A": (SOURCE, "incident-gap-v1")})


def utc_at(text):
    return datetime.fromisoformat(text).astimezone(timezone.utc)


class TimeBoundsTest(unittest.TestCase):
    def test_overflowing_raw_falls_through(self):
        for value in ("0001-01-01T00:00:00+09:00", "9999-12-31T23:59:59-05:00"):
            self.assertEqual(occurred_time(cand(1, end=value), None), (DECIDED, "decided_at"), value)
            self.assertEqual(occurred_time(cand(1, end=value, start=raw(minutes(1))), KST),
                             (minutes(1), "process_time_raw"), value)
        # 시간대 없는 값에 원본 시간대를 붙여 UTC로 바꿀 때 넘침
        self.assertEqual(occurred_time(cand(1, end="0001-01-01 00:00:00"), KST), (DECIDED, "decided_at"))

    def test_assumed_timezone_overflow_falls_through(self):
        # 가정 경로(source_timezone=None): Asia/Seoul을 붙인 0001-01-01 00:00:00은 UTC 변환이 넘친다
        self.assertEqual(occurred_time(cand(1, end="0001-01-01 00:00:00"), None), (DECIDED, "decided_at"))
        self.assertEqual(occurred_time(cand(1, end="0001-01-01 00:00:00", start=raw(minutes(1))), None),
                         (minutes(1), "process_time_raw"))

    def test_edge_values_inside_range_are_kept(self):
        for value in ("9999-12-31T23:58:00+00:00", "0001-01-01T00:30:00+00:00"):
            self.assertEqual(occurred_time(cand(1, end=value), None), (utc_at(value), "process_time_raw"), value)

    def test_state_windows_are_clamped(self):
        high, low = utc_at("9999-12-31T23:58:00+00:00"), utc_at("0001-01-01T00:30:00+00:00")
        windows = inc.state_windows([cand(1, end="9999-12-31T23:58:00+00:00", rc="E001"),
                                     cand(2, end="0001-01-01T00:30:00+00:00", rc="E002")])
        self.assertEqual(windows["response_code:E001"], (high - timedelta(minutes=65), inc.UTC_MAX))
        self.assertEqual(windows["response_code:E002"], (inc.UTC_MIN, low + timedelta(minutes=5)))
        self.assertEqual(inc.shift_clamped(inc.UTC_MAX, timedelta(minutes=-1)), inc.UTC_MAX - timedelta(minutes=1))

    def test_grouping_near_range_ends(self):
        late = OpenIncident("A", "response_code:E001", utc_at("9999-12-31T23:30:00+00:00"),
                            utc_at("9999-12-31T23:56:00+00:00"))
        early = OpenIncident("B", "response_code:E002", utc_at("0001-01-01T00:03:00+00:00"),
                             utc_at("0001-01-01T00:04:00+00:00"))
        state = [late, early]
        result = group_decisions(SOURCE, [cand(1, end="9999-12-31T23:58:00+00:00", rc="E001"),
                                          cand(2, end="0001-01-01T00:01:00+00:00", rc="E002")], state)
        self.assertEqual(result.members[1].incident_id, "A")  # o + max_span가 범위를 넘어도 참여
        new = result.incidents[0]
        self.assertEqual(new.opened_at, utc_at("0001-01-01T00:01:00+00:00"))
        self.assertEqual([(m.incident_id, m.target_incident_id) for m in result.merges], [("B", new.incident_id)])
        self.assertTrue(continuation_cycle_free(apply(state, result)))


class UnknownGroupTest(unittest.TestCase):
    def test_unknown_group_anywhere_in_graph_refuses_merge(self):
        # Y의 뿌리 M은 상태 밖(실제로는 R로 병합돼 R 묶음 → O 간선이 있음). R·O 사이 경로는 보이지 않지만
        # 그래프에 모르는 묶음이 있으므로 병합하지 않는다
        state = [OpenIncident("R", "response_code:E001", minutes(100), minutes(100)),
                 OpenIncident("O", "response_code:E001", minutes(103), minutes(103), has_continuation=True),
                 OpenIncident("Y", "response_code:E002", minutes(165), minutes(165), continues_from="O",
                              merged_into="M")]
        result = run([cand(1, minutes(102))], state)
        self.assertEqual(result.members[0].incident_id, "R")
        self.assertEqual(result.merges, ())
        # Y를 빼면(모르는 묶음 없음) 같은 판정으로 병합한다
        merged = run([cand(1, minutes(102))], state[:2])
        self.assertEqual([(m.incident_id, m.target_incident_id) for m in merged.merges], [("O", "R")])



class LongKeyTest(unittest.TestCase):
    def hashed(self, key, value):
        return f"{key}:sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()

    def test_boundary_512_bytes_after_escape(self):
        self.assertEqual(group_key(cand(1, rc=None, channel="A" * 512)), "channel_id:" + "A" * 512)
        self.assertEqual(group_key(cand(1, rc=None, channel="A" * 513)), self.hashed("channel_id", "A" * 513))
        # 이스케이프 뒤 길이로 잰다: ':' 171개는 원문 171바이트지만 '%3A' 513바이트
        self.assertEqual(group_key(cand(1, rc=None, channel=":" * 170)), "channel_id:" + "%3A" * 170)
        self.assertEqual(group_key(cand(1, rc=None, channel=":" * 171)), self.hashed("channel_id", ":" * 171))

    def test_multibyte_counts_utf8_bytes(self):
        kept = "가" * 170 + "AB"  # 510 + 2 = 512바이트
        self.assertEqual(group_key(cand(1, rc=None, channel=kept)), "channel_id:" + kept)
        long = "가" * 171  # 513바이트, 171자
        self.assertEqual(group_key(cand(1, rc=None, channel=long)), self.hashed("channel_id", long))
        self.assertEqual(group_key(cand(1, rc="E" * 600)), self.hashed("response_code", "E" * 600))

    def test_hashed_key_cannot_collide_with_short_value(self):
        digest = hashlib.sha256(("A" * 513).encode("utf-8")).hexdigest()
        self.assertEqual(group_key(cand(1, rc=None, channel="sha256:" + digest)), "channel_id:sha256%3A" + digest)

    def test_same_long_value_groups_and_ids_are_deterministic(self):
        value = "CH" + "0123456789abcdef" * 400
        result = run([cand(1, minutes(0), rc=None, channel=value), cand(2, minutes(2), rc=None, channel=value),
                      cand(3, minutes(1), rc=None, channel=value + "x")])
        self.assertEqual(len(result.incidents), 2)
        first = result.incidents[0]
        self.assertEqual(first.group_key, self.hashed("channel_id", value))
        self.assertLessEqual(len(first.group_key.encode("utf-8")), len("channel_id:") + inc.GROUP_KEY_MAX_BYTES)
        self.assertEqual([m.incident_id for m in result.members if m.decision_id in ("d001", "d002")],
                         [first.incident_id] * 2)
        self.assertEqual(first.incident_id, incident_id(SOURCE, "incident-gap-v1", first.group_key, minutes(0)))
        self.assertEqual(run([cand(1, minutes(0), rc=None, channel=value)]).incidents[0].incident_id, first.incident_id)
        self.assertNotIn(value, json.dumps(first.criteria))
        self.assertEqual(first.criteria["groupKeyMaxBytes"], 512)


if __name__ == "__main__":
    unittest.main()

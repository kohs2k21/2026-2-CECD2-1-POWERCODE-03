"""학습 선정 정책 v2(`train-success-excluded-v2`) 단위 시험(contracts/trainer-service.md 선정 정책 v2)."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trainer import runner, synthetic_execution
from trainer.selection import (VERSION, VERSION_EXCLUDED, exclusion_reason, intervals_hash, parse_exclusion,
                               select_training, stored_exclusion, utc_text)

PERIOD = {"from": "2026-10-01T00:00:00Z", "until": "2026-10-02T00:00:00Z"}
START = datetime(2026, 10, 1, tzinfo=timezone.utc)
V1_KEYS = ["policyVersion", "split", "populationCount", "selectedCount", "excludedCount",
           "excludedExplicitAnomalyCount", "statusCounts", "normalGroundTruth"]


def interval(start_hour, end_hour, **extra):
    return {"from": (START + timedelta(hours=start_hour)).isoformat(), "until": (START + timedelta(hours=end_hour)).isoformat(), **extra}


def rows_and_times():
    """시각 0~9시 S 10행 + F 1행 + 라벨 anomaly S 1행 + 시각 없는 S 1행."""
    rows, times = [], {}
    for hour in range(10):
        row = (uuid.uuid4(), {"status": "S", "adapter_type": "A", "total_count": hour}, "train", None)
        rows.append(row)
        times[row[0]] = START + timedelta(hours=hour, minutes=30)
    failed = (uuid.uuid4(), {"status": "F"}, "train", None)
    anomaly = (uuid.uuid4(), {"status": "S"}, "train", {"label": "anomaly"})
    unknown = (uuid.uuid4(), {"status": "S"}, "train", None)
    times[failed[0]] = times[anomaly[0]] = START + timedelta(hours=2, minutes=30)
    return rows + [failed, anomaly, unknown], times


class ParseTests(unittest.TestCase):
    def test_intervals_are_normalized_to_api_utc_strings_sorted_by_from(self):
        exclusion = parse_exclusion([interval(5, 6), {"from": "2026-10-01T10:00:00+09:00", "until": "2026-10-01T02:00:00.5+00:00",
                                                      "reasonCode": "maintenance", "note": "점검"}], PERIOD, "persisted_at")
        self.assertEqual(exclusion.settings(), [
            {"from": "2026-10-01T01:00:00.000Z", "until": "2026-10-01T02:00:00.500Z", "reasonCode": "maintenance", "note": "점검"},
            {"from": "2026-10-01T05:00:00.000Z", "until": "2026-10-01T06:00:00.000Z", "reasonCode": "other", "note": None}])

    def test_hash_is_sha256_of_compact_from_until_json(self):
        text = '[{"from":"2026-10-01T01:00:00.000Z","until":"2026-10-01T02:00:00.000Z"},' \
               '{"from":"2026-10-01T05:00:00.000Z","until":"2026-10-01T06:00:00.000Z"}]'
        exclusion = parse_exclusion([interval(5, 6, note="x"), interval(1, 2, reasonCode="known_incident")], PERIOD, "persisted_at")
        self.assertEqual(exclusion.digest, hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(parse_exclusion([], PERIOD, "persisted_at").digest, hashlib.sha256(b"[]").hexdigest())
        self.assertEqual(intervals_hash(exclusion.settings()), exclusion.digest)
        self.assertEqual(utc_text(datetime(2026, 1, 2, 3, 4, 5, 6000, tzinfo=timezone.utc)), "2026-01-02T03:04:05.006Z")

    def test_malformed_intervals_end_the_job_as_invalid_input(self):
        cases = [None, {}, [interval(1, 2)] * 33, [interval(2, 1)], [interval(1, 1)], [interval(1, 3), interval(2, 4)],
                 [{"from": "2026-09-30T23:00:00Z", "until": "2026-10-01T01:00:00Z"}], [interval(23, 25)],
                 [{"from": "2026-10-01T01:00:00", "until": "2026-10-01T02:00:00"}], [{"from": 1, "until": 2}],
                 [interval(1, 2, reasonCode="bad")], [interval(1, 2, note="x" * 201)], [interval(1, 2, extra=1)],
                 [{"from": "2026-10-01T01:00:00.000001Z", "until": "2026-10-01T02:00:00Z"}]]
        for value in cases:
            with self.subTest(value=str(value)[:80]), self.assertRaisesRegex(ValueError, "^invalid_job_input$"):
                parse_exclusion(value, PERIOD, "persisted_at")
        # 맞닿은 구간·기간 양 끝은 허용(API 검사와 같다). 기간의 밀리초 아래 자릿수는 잘라 비교한다.
        parse_exclusion([interval(0, 2), interval(2, 24)], PERIOD, "persisted_at")
        parse_exclusion([interval(0, 1)], {"from": "2026-10-01T00:00:00.000400Z", "until": PERIOD["until"]}, "persisted_at")

    def test_unsupported_interval_time_basis_is_not_ignored(self):
        with self.assertRaisesRegex(ValueError, "^config_schema_not_supported_yet$"):
            parse_exclusion([], PERIOD, "esb_occurred_at")


class SelectionTests(unittest.TestCase):
    def test_v1_output_is_unchanged_without_the_key(self):
        rows, _ = rows_and_times()
        selected, selection = select_training(rows)
        self.assertEqual(list(selection), V1_KEYS)
        self.assertEqual(selection, {"policyVersion": VERSION, "split": "train", "populationCount": 13, "selectedCount": 11,
                                     "excludedCount": 2, "excludedExplicitAnomalyCount": 1, "statusCounts": {"S": 12, "F": 1},
                                     "normalGroundTruth": False})
        self.assertEqual(len(selected), 11)

    def test_v2_excludes_rows_inside_intervals_and_unknown_times_with_disjoint_counts(self):
        rows, times = rows_and_times()
        exclusion = parse_exclusion([interval(2, 4), interval(7, 8)], PERIOD, "persisted_at")
        selected, selection = select_training(rows, exclusion, times)
        self.assertEqual(selection["policyVersion"], VERSION_EXCLUDED)
        self.assertEqual(list(selection)[:len(V1_KEYS)], V1_KEYS)
        self.assertEqual({key: selection[key] for key in ("selectedCount", "excludedCount", "excludedExplicitAnomalyCount",
                                                          "excludedByIntervalCount", "excludedTimeUnknownCount",
                                                          "excludedIntervalCount", "timeBasis", "intervalTimeBasis")},
                         {"selectedCount": 7, "excludedCount": 6, "excludedExplicitAnomalyCount": 1, "excludedByIntervalCount": 3,
                          "excludedTimeUnknownCount": 1, "excludedIntervalCount": 2, "timeBasis": "persisted_at",
                          "intervalTimeBasis": "persisted_at"})
        self.assertEqual(selection["excludedIntervalsHash"], exclusion.digest)
        self.assertEqual(sorted(row[1]["total_count"] for row in selected), [0, 1, 4, 5, 6, 8, 9])

    def test_interval_end_is_exclusive(self):
        row = (uuid.uuid4(), {"status": "S"}, "train", None)
        exclusion = parse_exclusion([interval(1, 2)], PERIOD, "persisted_at")
        self.assertEqual(exclusion_reason(row, exclusion, {row[0]: START + timedelta(hours=1)}), "excluded_interval")
        self.assertIsNone(exclusion_reason(row, exclusion, {row[0]: START + timedelta(hours=2)}))

    def test_empty_list_selects_the_same_rows_as_v1_and_needs_no_times(self):
        rows, _ = rows_and_times()
        selected, selection = select_training(rows, parse_exclusion([], PERIOD, "persisted_at"), None)
        self.assertEqual(selected, select_training(rows)[0])
        self.assertEqual((selection["excludedIntervalCount"], selection["excludedByIntervalCount"],
                          selection["excludedTimeUnknownCount"]), (0, 0, 0))

    def test_stored_settings_restore_the_same_selection_and_reject_tampering(self):
        exclusion = parse_exclusion([interval(2, 4)], PERIOD, "persisted_at")
        rows, times = rows_and_times()
        _, selection = select_training(rows, exclusion, times)
        settings = {"trainingSelection": selection, "trainingPeriod": {**PERIOD, "timeBasis": "persisted_at"},
                    "excludedIntervals": exclusion.settings()}
        self.assertEqual(stored_exclusion(settings), exclusion)
        self.assertIsNone(stored_exclusion({"trainingSelection": select_training(rows)[1]}))
        tampered = copy.deepcopy(settings)
        tampered["excludedIntervals"][0]["until"] = "2026-10-01T05:00:00.000Z"
        with self.assertRaisesRegex(ValueError, "^training_selection_invalid$"):
            stored_exclusion(tampered)


class TrainingInputTests(unittest.TestCase):
    def test_fit_operations_use_only_selected_rows_and_short_selection_stops_before_fit(self):
        definitions = [{"name": "centered", "expression": {"op": "group_centered", "groupBy": "P.adapter_type",
                                                             "value": {"op": "field", "field": "P.total_count"}}}]
        rows, times = rows_and_times()
        exclusion = parse_exclusion([interval(0, 5)], PERIOD, "persisted_at")
        with patch.object(runner, "_definitions", return_value=definitions), \
                patch.object(runner, "_snapshot_rows", return_value=rows), \
                patch.object(runner, "training_times", return_value=times) as read_times:
            prepared, _, state, _, selection = runner._training_inputs(_Connection(), uuid.uuid4(), uuid.uuid4(), exclusion, 5)
            self.assertEqual(read_times.call_args.args[2], "persisted_at")
            # 남은 행 total_count 5~9 → 중앙값 7. 구간 안 행은 fit 통계에도 들어가지 않는다.
            self.assertEqual(state["features"]["centered"]["groups"], {"A": "7"})
            self.assertEqual((len(prepared), selection["selectedCount"]), (5, 5))
            self.assertIs(state["reference"]["trainingSelection"], selection)
            with patch.object(runner, "fit_features") as fit, self.assertRaises(runner.TrainingRowsInsufficient) as raised:
                runner._training_inputs(_Connection(), uuid.uuid4(), uuid.uuid4(), exclusion, 6)
            fit.assert_not_called()
        self.assertEqual((str(raised.exception), raised.exception.min_rows), ("train_rows_insufficient_after_exclusion", 6))
        self.assertEqual(raised.exception.selection["excludedByIntervalCount"], 5)

    def test_v1_selection_skips_the_minimum_and_time_lookup(self):
        rows, _ = rows_and_times()
        definitions = [{"name": "raw", "expression": {"op": "field", "field": "P.total_count"}}]
        with patch.object(runner, "_definitions", return_value=definitions), \
                patch.object(runner, "_snapshot_rows", return_value=rows), \
                patch.object(runner, "training_times") as read_times:
            *_, selection = runner._training_inputs(_Connection(), uuid.uuid4(), uuid.uuid4(), None, 600)
        read_times.assert_not_called()
        self.assertEqual(selection["policyVersion"], VERSION)


class SyntheticBaselineTests(unittest.TestCase):
    def test_baseline_reference_uses_the_same_eligibility(self):
        rows, times = rows_and_times()
        exclusion = parse_exclusion([interval(0, 5)], PERIOD, "persisted_at")
        self.assertIs(synthetic_execution._training_reference(rows, None), rows)
        reference = synthetic_execution._training_reference(rows, (exclusion, times))
        self.assertEqual(sorted(row[1]["total_count"] for row in reference), [5, 6, 7, 8, 9])


class _Connection:
    def commit(self):
        pass


if __name__ == "__main__":
    unittest.main()

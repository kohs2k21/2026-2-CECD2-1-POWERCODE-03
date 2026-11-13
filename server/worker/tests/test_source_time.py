"""trainer.source_time: 원천 시각 해석·거래 묶음·시간순 분할의 DB 없는 단위 시험."""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from detector import incidents as inc
from detector.incidents import DecisionCandidate, occurred_time
from trainer import source_time as st
from trainer.source_time import (ASSUMED_TIMEZONE, cut_reference_current, format_utc, group_by_transaction,
                                 process_source_time, split_blocks, time_blocks)

DECIDED = datetime(2000, 1, 1, 8, 0, tzinfo=timezone.utc)
SAMPLES = [
    ("2026-08-03 10:00:00.123", "2026-08-03 10:00:01.500"),
    ("2026-08-03T10:00:00+09:00", "2026-08-03T01:00:02Z"),
    ("2026-08-03 10:00:00", None),
    (None, "2026-08-03 10:00:00"),
    ("2026-08-03 10:00:00", ""),
    ("2026-08-03 10:00:00", "\\N"),
    ("2026-08-03 10:00:00", "not-a-time"),
    ("bad", "worse"),
    (None, None),
    ("", "   "),
    ("0001-01-01T00:00:00", None),
    ("0001-01-01T05:00:00+09:00", "2026-08-03 10:00:00"),
    ("9999-12-31T23:59:59-05:00", None),
    (" 2026-08-03 10:00:00", None),
    ("2026-08-03 10:00:00.123456+05:30", "2026-08-03T10:00:00.5-03:00"),
]


def row(tx, start=None, end=None, **extra):
    return {"transaction_id": tx, "start_time_raw": start, "end_time_raw": end, **extra}


def kst(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone(timedelta(hours=9))).astimezone(timezone.utc)


class ProcessSourceTimeTests(unittest.TestCase):
    def test_assumed_timezone_matches_detector_constant(self):
        self.assertEqual(ASSUMED_TIMEZONE, inc.ASSUMED_TIMEZONE)

    def test_agrees_with_incident_occurred_time_on_samples(self):
        # 사건 묶음 occurred_time과 같은 해석: 시각을 읽으면 같은 UTC 값, 못 읽으면 occurred_time은 decided_at 대체.
        for zone in (None, "Asia/Seoul", "UTC", "America/New_York", "", "No/Such_Zone"):
            for start, end in SAMPLES:
                with self.subTest(zone=zone, start=start, end=end):
                    candidate = DecisionCandidate("d", 1, "e", "s", None, DECIDED, end_time_raw=end, start_time_raw=start)
                    expected, basis = occurred_time(candidate, zone)
                    value, tag, used = process_source_time(start, end, zone)
                    if basis == inc.TIME_FALLBACK:
                        self.assertIsNone(value)
                        self.assertIsNone(tag)
                        self.assertIsNone(used)
                    else:
                        self.assertEqual(value, expected)
                        self.assertEqual(value.tzinfo, timezone.utc)

    def test_end_first_then_start_and_basis_tags(self):
        value, basis, used = process_source_time("2026-08-03 10:00:00", "2026-08-03 10:00:05")
        self.assertEqual((value, basis, used), (kst("2026-08-03 10:00:05"), "assumed_tz", "end"))
        value, basis, used = process_source_time("2026-08-03 10:00:00", "garbage")
        self.assertEqual((value, basis, used), (kst("2026-08-03 10:00:00"), "assumed_tz", "start"))
        _, basis, _ = process_source_time(None, "2026-08-03T10:00:00+09:00")
        self.assertEqual(basis, "offset")
        value, basis, _ = process_source_time(None, "2026-08-03 10:00:00", "UTC")
        self.assertEqual((value, basis), (datetime(2026, 8, 3, 10, tzinfo=timezone.utc), "verified_tz"))

    def test_verified_without_zone_skips_naive_but_keeps_offset(self):
        self.assertEqual(process_source_time("2026-08-03 10:00:00", None, ""), (None, None, None))
        value, basis, used = process_source_time("2026-08-03T10:00:00+09:00", "2026-08-03 10:00:05", "")
        self.assertEqual((basis, used), ("offset", "start"))
        self.assertEqual(value, datetime(2026, 8, 3, 1, tzinfo=timezone.utc))

    def test_unparsable_is_none_not_substituted(self):
        self.assertEqual(process_source_time("bad", "worse"), (None, None, None))
        self.assertEqual(process_source_time(None, "0001-01-01T00:00:00"), (None, None, None))  # UTC 변환 범위 초과


class FormatTests(unittest.TestCase):
    def test_fixed_length_utc_format_and_string_order(self):
        early = format_utc(datetime(999, 1, 1, tzinfo=timezone.utc))
        late = format_utc(datetime(2026, 8, 3, 1, 2, 3, 4, tzinfo=timezone(timedelta(hours=9))))
        self.assertEqual(early, "0999-01-01T00:00:00.000000+00:00")
        self.assertEqual(late, "2026-08-02T16:02:03.000004+00:00")
        self.assertEqual(len(early), len(late))
        self.assertLess(early, late)
        with self.assertRaises(ValueError):
            format_utc(datetime(2026, 8, 3))


class GroupingTests(unittest.TestCase):
    def test_group_time_is_min_and_excluded_rows_counted(self):
        rows = [row("B", "2026-08-03 10:00:03", "2026-08-03 10:00:04"),
                row("A", "2026-08-03 10:00:05", "2026-08-03 10:00:06"),
                row("A", "2026-08-03 10:00:00", "2026-08-03 10:00:02"),
                row("A", "bad", "bad"),
                row(None, "2026-08-03 10:00:00", None),
                row("  ", "2026-08-03 10:00:00", None),
                row("C", "2026-08-03T01:00:01+00:00", None)]
        result = group_by_transaction(rows)
        self.assertEqual([g.transaction_id for g in result.groups], ["C", "A", "B"])
        self.assertEqual(result.groups[1].time, kst("2026-08-03 10:00:02"))
        self.assertEqual(len(result.groups[1].rows), 2)  # 시각 못 읽은 A 행은 빠짐
        self.assertEqual(result.counts["rows_in"], 7)
        self.assertEqual(result.counts["rows_grouped"], 4)
        self.assertEqual(result.counts["excluded_unparsed_time"], 1)
        self.assertEqual(result.counts["excluded_missing_transaction"], 2)
        self.assertEqual(result.counts["basis"], {"offset": 1, "verified_tz": 0, "assumed_tz": 3})
        self.assertEqual(result.counts["field"], {"end": 3, "start": 1})
        self.assertEqual(result.counts["groups"], 3)

    def test_all_unparsable_transaction_has_no_group(self):
        result = group_by_transaction([row("A", "x", "y"), row("B", None, "2026-08-03 10:00:00")])
        self.assertEqual([g.transaction_id for g in result.groups], ["B"])

    def test_ties_sorted_by_transaction_id_and_merged_into_one_block(self):
        rows = [row("Z", None, "2026-08-03 10:00:00"), row("A", None, "2026-08-03 10:00:00"),
                row("M", None, "2026-08-03 10:00:01")]
        groups = group_by_transaction(rows).groups
        self.assertEqual([g.transaction_id for g in groups], ["A", "Z", "M"])
        self.assertEqual([[g.transaction_id for g in b] for b in time_blocks(groups)], [["A", "Z"], ["M"]])

    def test_source_window_excludes_rows_before_grouping(self):
        rows = [row("A", None, "2026-08-03 10:00:00"), row("A", None, "2026-08-03 10:00:10"),
                row("B", None, "2026-08-03 10:00:05"), row("C", None, "2026-08-03 10:00:20"),
                row("D", "bad", "bad")]
        lower = kst("2026-08-03 10:00:05")
        upper = kst("2026-08-03 10:00:20")
        result = group_by_transaction(rows, window=(lower, upper))
        self.assertEqual([g.transaction_id for g in result.groups], ["B", "A"])
        self.assertEqual(result.groups[1].time, kst("2026-08-03 10:00:10"))  # 창 밖 A 첫 행은 빠지고 남은 행 기준
        self.assertEqual(result.counts["outside_source_window"], 2)  # A 10:00:00(< from), C 10:00:20(≥ until)
        self.assertEqual(result.counts["excluded_unparsed_time"], 1)  # 해석 실패가 창 검사보다 먼저
        self.assertEqual(group_by_transaction(rows, window=(lower, None)).counts["outside_source_window"], 1)
        self.assertEqual(group_by_transaction(rows).counts["outside_source_window"], 0)
        with self.assertRaises(ValueError):
            group_by_transaction(rows, window=(datetime(2026, 8, 3), None))
        with self.assertRaises(ValueError):
            group_by_transaction(rows, window=(upper, lower))

    def test_unsorted_groups_rejected(self):
        groups = group_by_transaction([row("A", None, "2026-08-03 10:00:00"), row("B", None, "2026-08-03 10:00:01")]).groups
        with self.assertRaises(ValueError):
            time_blocks(list(reversed(groups)))


def series(count, tie_every=0):
    rows = []
    for index in range(count):
        second = index - (index % tie_every == 1) if tie_every else index
        rows.append(row(f"T{index:04d}", None, f"2026-08-03 10:{second // 60:02d}:{second % 60:02d}"))
    return group_by_transaction(rows).groups


class SplitTests(unittest.TestCase):
    def test_cut_never_splits_equal_time_and_periods_compare_as_strings(self):
        groups = series(10, tie_every=2)  # 0·1, 2·3 … 같은 시각 → 5블록
        cut = cut_reference_current(groups, 0.8)
        self.assertEqual(cut.counts["reference_blocks"], 4)
        self.assertEqual(cut.counts["current_blocks"], 1)
        self.assertEqual(cut.counts["reference_groups"] + cut.counts["current_groups"], 10)
        self.assertLess(max(g.time for g in cut.reference), min(g.time for g in cut.current))
        self.assertLessEqual(cut.reference_period["end_exclusive"], cut.current_period["start_inclusive"])
        self.assertLess(cut.reference_period["start_inclusive"], cut.reference_period["end_exclusive"])
        self.assertLess(cut.current_period["start_inclusive"], cut.current_period["end_exclusive"])
        self.assertEqual(cut.reference_period["end_exclusive"], format_utc(cut.current[0].time))

    def test_whole_transaction_stays_on_one_side(self):
        rows = [row("A", None, "2026-08-03 10:00:00"), row("B", None, "2026-08-03 10:00:01"),
                row("A", None, "2026-08-03 10:00:09"), row("C", None, "2026-08-03 10:00:02")]
        cut = cut_reference_current(group_by_transaction(rows).groups, 0.5)
        sides = {g.transaction_id: side for side, part in (("ref", cut.reference), ("cur", cut.current)) for g in part}
        self.assertEqual(sides, {"A": "ref", "B": "cur", "C": "cur"})
        self.assertEqual(cut.counts["reference_rows"], 2)

    def test_eighty_twenty_on_distinct_blocks(self):
        cut = cut_reference_current(series(100))
        self.assertEqual((cut.counts["reference_groups"], cut.counts["current_groups"]), (80, 20))

    def test_split_blocks_default_and_nonempty(self):
        parts = split_blocks(series(20))
        self.assertEqual([len(p) for p in parts], [14, 3, 3])
        self.assertEqual([len(p) for p in split_blocks(series(3))], [1, 1, 1])
        self.assertEqual([len(p) for p in split_blocks(series(2), (0.99, 0.01))], [1, 1])
        with self.assertRaises(ValueError):
            split_blocks(series(2))
        with self.assertRaises(ValueError):
            split_blocks(series(10), (0.5, 0.4))
        with self.assertRaises(ValueError):
            cut_reference_current(series(1))

    def test_split_matches_snapshot_rule_when_enough_blocks(self):
        from trainer.snapshot import MIN_BLOCKS
        for count in range(max(MIN_BLOCKS, 4), 60):
            blocks = len(time_blocks(series(count)))
            train_end = max(1, int(blocks * .7))
            validation_end = min(blocks - 1, max(train_end + 1, int(blocks * .85)))
            with self.subTest(count=count):
                self.assertEqual([len(p) for p in split_blocks(series(count))],
                                 [train_end, validation_end - train_end, blocks - validation_end])


if __name__ == "__main__":
    unittest.main()

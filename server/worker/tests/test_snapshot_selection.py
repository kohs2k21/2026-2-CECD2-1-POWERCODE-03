"""Issue #69: snapshot 멤버 선택(execution별 최신 revision 1개)·블록 분할의 SQL 없는 단위 시험."""
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trainer.snapshot import (REVISION_ORDER, REVISION_RULE, SelectedMember, assign_splits,
                              select_latest_revisions, split_blocks)

T0 = datetime(2026, 10, 21, tzinfo=timezone.utc)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def uid(index):
    return uuid.UUID(int=index)


class SelectLatestRevisionTests(unittest.TestCase):
    def test_single_revision_rows_pass_through_unchanged(self):
        rows = [(uid(100 + i), uid(i), 1, at(i)) for i in range(5)]
        members, superseded = select_latest_revisions(rows)
        self.assertEqual(superseded, 0)
        self.assertEqual([m.observation_id for m in members], [uid(100 + i) for i in range(5)])
        self.assertEqual([m.revision_no for m in members], [1] * 5)

    def test_higher_revision_wins_even_when_persisted_earlier(self):
        execution = uid(1)
        rows = [(uid(10), execution, 2, at(0)),  # 더 새로운 revision이 먼저 저장된 (지연 재도착) 경우
                (uid(11), execution, 1, at(5))]
        members, superseded = select_latest_revisions(rows)
        self.assertEqual(superseded, 1)
        self.assertEqual(len(members), 1)
        self.assertEqual((members[0].observation_id, members[0].revision_no, members[0].persisted_at), (uid(10), 2, at(0)))

    def test_ties_follow_persisted_at_then_id_descending(self):
        execution = uid(1)
        same_revision = [(uid(20), execution, 3, at(1)), (uid(21), execution, 3, at(2))]
        members, _ = select_latest_revisions(same_revision)
        self.assertEqual(members[0].observation_id, uid(21))
        same_time = [(uid(31), execution, 3, at(1)), (uid(30), execution, 3, at(1))]
        members, _ = select_latest_revisions(same_time)
        self.assertEqual(members[0].observation_id, uid(31))
        self.assertEqual(REVISION_ORDER, ("revision_no DESC", "persisted_at DESC", "id DESC"))
        self.assertEqual(REVISION_RULE, "latest-per-execution-v1")

    def test_input_order_does_not_change_selection(self):
        execution = uid(7)
        rows = [(uid(40), execution, 1, at(0)), (uid(41), execution, 2, at(1)), (uid(42), execution, 3, at(2))]
        forward, _ = select_latest_revisions(rows)
        backward, _ = select_latest_revisions(list(reversed(rows)))
        self.assertEqual(forward, backward)
        self.assertEqual(forward[0].observation_id, uid(42))

    def test_members_are_ordered_by_persisted_at_of_selected_revision(self):
        rows = [(uid(50), uid(1), 1, at(0)), (uid(51), uid(1), 2, at(9)),
                (uid(52), uid(2), 1, at(3))]
        members, superseded = select_latest_revisions(rows)
        self.assertEqual(superseded, 1)
        self.assertEqual([m.execution_id for m in members], [uid(2), uid(1)])

    def test_missing_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "snapshot_member_identity_missing"):
            select_latest_revisions([(uid(1), None, None, at(0))])


class BlockSplitTests(unittest.TestCase):
    def members(self, count, step=1):
        return [SelectedMember(uid(100 + i), uid(i), 1, at(i * step)) for i in range(count)]

    def test_distinct_times_split_70_15_15(self):
        assigned = assign_splits(split_blocks(self.members(100)))
        counts = {}
        for _, split in assigned:
            counts[split] = counts.get(split, 0) + 1
        self.assertEqual(counts, {"train": 70, "validation": 15, "test": 15})
        self.assertEqual([split for _, split in assigned[69:71]], ["train", "validation"])

    def test_same_persisted_at_stays_in_one_block(self):
        same = [SelectedMember(uid(200 + i), uid(i), 1, at(0)) for i in range(3)]
        later = [SelectedMember(uid(300 + i), uid(10 + i), 1, at(i + 1)) for i in range(3)]
        blocks = split_blocks(same + later)
        self.assertEqual([len(block) for block in blocks], [3, 1, 1, 1])
        assigned = assign_splits(blocks)
        self.assertEqual({split for member, split in assigned if member in same}, {"train"})

    def test_fewer_than_three_blocks_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "snapshot_time_split_unavailable"):
            assign_splits(split_blocks(self.members(2)))


if __name__ == "__main__":
    unittest.main()

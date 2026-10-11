"""Notion owns task progress even while GitHub reconciliation changes links or closes issues."""
import copy
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from automation.services.sync.app import CREATE, UPDATE, Engine, State, prop, value
from automation.tests.test_sync import CFG, FakeNotion
from automation.tests.test_branch_cleanup import CleanupGitHub


class RecordingNotion(FakeNotion):
    def __init__(self):
        super().__init__()
        self.patches = []

    def patch(self, page_id, properties):
        self.patches.append(copy.deepcopy(properties))
        super().patch(page_id, properties)


class StatusOwnershipTests(unittest.TestCase):
    @contextmanager
    def task(self, status, date):
        with tempfile.TemporaryDirectory() as tmp:
            n, g = RecordingNotion(), CleanupGitHub()
            state = State(Path(tmp) / "state.db")
            engine = Engine(copy.deepcopy(CFG), n, g, state)
            n.set("상태", "select", status)
            n.set("완료일", "date", date)
            n.patches.clear()
            try:
                yield n, g, engine
            finally:
                state.db.close()

    def assert_owned(self, n, status, date):
        self.assertEqual(value(n.page, "상태"), status)
        self.assertEqual(value(n.page, "완료일"), date)
        for payload in n.patches:
            self.assertTrue({"상태", "완료일"}.isdisjoint(payload), payload.keys())

    def configure_github(self, n, g, scenario):
        branch = value(n.page, "Branch")
        g.unmerged[("head1", "dev")] = False
        if scenario.startswith("issue_closed") or scenario == "merged_cancelled":
            g.data[1].update(state="closed", state_reason="not_planned" if scenario.endswith("cancelled") else "completed")
        if scenario in ("draft", "open", "closed_pr", "merged", "merged_cancelled", "reopened"):
            g.pulls = [{"number": 2, "state": "open" if scenario in ("draft", "open") else "closed",
                "draft": scenario == "draft", "merged_at": "2026-10-08T06:00:00Z" if scenario in ("merged", "merged_cancelled", "reopened") else None,
                "head": {"ref": branch, "sha": "head1", "repo": {"full_name": CFG["repository"]}},
                "base": {"ref": "dev"}, "html_url": f"https://github.com/{CFG['repository']}/pull/2"}]
        if scenario == "reopened":
            g.events[1] = [{"event": "reopened", "created_at": "2026-10-09T06:00:00Z"}]

    def test_all_progress_values_and_dates_survive_creation_and_github_lifecycle(self):
        scenarios = ("no_pr", "draft", "open", "closed_pr", "merged", "issue_closed_completed", "issue_closed_cancelled", "merged_cancelled", "reopened")
        dates = (None, {"start": "2026-10-10", "end": "2026-10-11", "time_zone": None})
        for status in ("백로그", "개발 준비", "개발 중", "리뷰 중", "취소", "완료"):
            for date in dates:
                for scenario in scenarios:
                    with self.subTest(status=status, date=date, scenario=scenario), self.task(status, date) as (n, g, engine):
                        self.assertEqual(engine.run()[0]["result"], "synced")
                        self.assert_owned(n, status, date)
                        self.configure_github(n, g, scenario)
                        n.set(UPDATE, "checkbox", True)
                        n.patches.clear()
                        for _ in range(2):
                            self.assertNotEqual(engine.run()[0]["result"], "error")
                            self.assert_owned(n, status, date)
                        self.assertEqual(value(n.page, "Issue 번호"), 1)
                        self.assertEqual(value(n.page, "GitHub 동기화"), "완료")
                        self.assertFalse(value(n.page, CREATE))
                        self.assertFalse(value(n.page, UPDATE))
                        if scenario in ("draft", "open", "closed_pr", "merged", "merged_cancelled", "reopened"):
                            self.assertEqual(value(n.page, "Pull Request"), g.pulls[0]["html_url"])
                        self.assertEqual(g.data[1]["state"], "closed" if scenario in ("merged", "merged_cancelled", "issue_closed_completed", "issue_closed_cancelled") else "open")

    def test_user_progress_edit_during_github_io_survives_and_new_request_is_not_cleared(self):
        with self.task("개발 중", {"start": "2026-10-01"}) as (n, g, engine):
            engine.run()
            self.configure_github(n, g, "merged")
            latest_date = {"start": "2026-10-11", "end": "2026-10-12"}
            original_prs = g.prs
            def edit_during_pr_read(branch):
                for key, kind, val in (("상태", "select", "완료"), ("완료일", "date", latest_date), (UPDATE, "checkbox", True)):
                    n.page["properties"][key] = {"type": kind, **prop(kind, val)}
                n.page["last_edited_time"] = "concurrent-human-edit"
                return original_prs(branch)
            n.patches.clear()
            with patch.object(g, "prs", side_effect=edit_during_pr_read):
                self.assertEqual(engine.run()[0]["result"], "completed")
            self.assert_owned(n, "완료", latest_date)
            self.assertTrue(value(n.page, UPDATE))
            self.assertEqual(value(n.page, "Pull Request"), g.pulls[0]["html_url"])
            self.assertEqual(g.data[1]["state"], "closed")

    def test_notion_manual_completion_does_not_close_an_open_issue(self):
        with self.task("완료", {"start": "2026-10-11"}) as (n, g, engine):
            engine.run()
            g.changed = True
            engine.run()
            self.assert_owned(n, "완료", {"start": "2026-10-11"})
            self.assertEqual(g.data[1]["state"], "open")
            self.assertEqual(g.pulls, [])

    def test_error_reporting_preserves_progress_and_completion_date(self):
        with self.task("완료", {"start": "2026-10-11"}) as (n, g, engine):
            engine.run()
            n.page["properties"]["담당자"] = {"type": "people", "people": [{"id": "unknown-user"}]}
            n.patches.clear()
            self.assertEqual(engine.run()[0]["code"], "ASSIGNEE_MAPPING_REQUIRED")
            self.assert_owned(n, "완료", {"start": "2026-10-11"})
            self.assertEqual(value(n.page, "GitHub 동기화"), "오류")
            self.assertEqual(g.data[1]["state"], "open")

    def test_initial_link_does_not_add_a_missing_completion_date(self):
        n, g = RecordingNotion(), CleanupGitHub()
        self.assertNotIn("완료일", n.page["properties"])
        with tempfile.TemporaryDirectory() as tmp:
            state = State(Path(tmp) / "state.db")
            try:
                engine = Engine(copy.deepcopy(CFG), n, g, state)
                self.assertEqual(engine.run()[0]["result"], "synced")
                self.assertNotIn("완료일", n.page["properties"])
                self.assert_owned(n, "백로그", None)
            finally:
                state.db.close()

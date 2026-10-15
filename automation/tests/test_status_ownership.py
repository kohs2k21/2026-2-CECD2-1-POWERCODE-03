"""Only a confirmed non-cancelled Issue closure may complete a Notion task."""
import copy
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from automation.services.sync.app import CREATE, UPDATE, Engine, State, SyncError, prop, value
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

    def assert_owned(self, n, status, date, allow_completion=False):
        self.assertEqual(value(n.page, "상태"), status)
        self.assertEqual(value(n.page, "완료일"), date)
        for payload in n.patches:
            self.assertNotIn("완료일", payload)
            if allow_completion and "상태" in payload:
                self.assertEqual(payload["상태"], prop("select", "완료"))
            else:
                self.assertNotIn("상태", payload)

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

    def test_only_normal_issue_closure_completes_across_all_progress_values_and_dates(self):
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
                        completes = scenario in ("merged", "issue_closed_completed")
                        for _ in range(2):
                            self.assertNotEqual(engine.run()[0]["result"], "error")
                            self.assert_owned(n, "완료" if completes else status, date, allow_completion=completes)
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

    def test_closed_issue_reopened_or_cancelled_during_pr_lookup_is_not_completed(self):
        for latest in ({"state": "open", "state_reason": "reopened"}, {"state": "closed", "state_reason": "not_planned"}):
            with self.subTest(latest=latest), self.task("리뷰 중", {"start": "2026-10-16"}) as (n, g, engine):
                engine.run()
                self.configure_github(n, g, "issue_closed_completed")
                original_prs = g.prs
                def change_after_initial_issue_read(branch):
                    g.data[1].update(latest)
                    return original_prs(branch)
                n.patches.clear()
                with patch.object(g, "prs", side_effect=change_after_initial_issue_read):
                    self.assertNotEqual(engine.run()[0]["result"], "error")
                self.assert_owned(n, "리뷰 중", {"start": "2026-10-16"})

    def test_latest_issue_read_failure_reports_error_without_completion_or_request_clear(self):
        with self.task("개발 중", {"start": "2026-10-16"}) as (n, g, engine):
            engine.run()
            self.configure_github(n, g, "issue_closed_completed")
            n.set(UPDATE, "checkbox", True)
            original_issue = g.issue
            failed = False
            reads_after_prs = 0
            def fail_after_pr_lookup(branch):
                nonlocal failed
                failed = True
                return []
            def read_issue(number):
                nonlocal reads_after_prs
                if failed:
                    reads_after_prs += 1
                if reads_after_prs >= 2:
                    raise SyncError("HTTP_503")
                return original_issue(number)
            n.patches.clear()
            with patch.object(g, "prs", side_effect=fail_after_pr_lookup), patch.object(g, "issue", side_effect=read_issue):
                self.assertEqual(engine.run()[0]["code"], "HTTP_503")
            self.assert_owned(n, "개발 중", {"start": "2026-10-16"})
            self.assertTrue(value(n.page, UPDATE))
            self.assertEqual(value(n.page, "GitHub 동기화"), "오류")

    def test_merge_request_without_confirmed_normal_issue_closure_does_not_complete(self):
        for latest in ({"state": "open", "state_reason": None}, {"state": "closed", "state_reason": "not_planned"}):
            with self.subTest(latest=latest), self.task("개발 중", None) as (n, g, engine):
                engine.run()
                self.configure_github(n, g, "merged")
                original_edit = g.edit_issue
                def edit_without_effective_normal_close(number, payload):
                    original_edit(number, payload)
                    if payload.get("state") == "closed":
                        g.data[number].update(latest)
                n.patches.clear()
                with patch.object(g, "edit_issue", side_effect=edit_without_effective_normal_close):
                    self.assertNotEqual(engine.run()[0]["result"], "error")
                self.assert_owned(n, "개발 중", None)
                self.assertEqual(g.data[1]["state"], latest["state"])

    def test_closed_issue_completes_before_open_pr_cleanup_error_and_never_downgrades(self):
        with self.task("리뷰 중", {"start": "2026-10-16"}) as (n, g, engine):
            engine.run()
            self.configure_github(n, g, "open")
            first = copy.deepcopy(g.pulls[0])
            first.update(number=3, state="closed", merged_at="2026-10-08T05:00:00Z")
            g.pulls.append(first)
            g.data[1].update(state="closed", state_reason="completed")
            branch = value(n.page, "Branch")
            n.patches.clear()
            for _ in range(2):
                self.assertEqual(engine.run()[0]["code"], "OPEN_PR_PREVENTS_BRANCH_DELETE")
                self.assert_owned(n, "완료", {"start": "2026-10-16"}, allow_completion=True)
                self.assertIn(branch, g.branches)
            self.assertEqual(value(n.page, "GitHub 동기화"), "오류")

    def test_legacy_closure_completes_but_unknown_issue_state_does_not(self):
        for state, completes in (("closed", True), ("unknown", False), (None, False)):
            with self.subTest(state=state), self.task("개발 준비", None) as (n, g, engine):
                engine.run()
                g.data[1].update(state=state)
                g.data[1].pop("state_reason", None)
                g.unmerged[("head1", "dev")] = False
                n.patches.clear()
                engine.run()
                self.assert_owned(n, "완료" if completes else "개발 준비", None, allow_completion=completes)

    def test_request_added_during_final_issue_read_is_preserved_with_manual_date(self):
        with self.task("개발 중", {"start": "2026-10-16"}) as (n, g, engine):
            engine.run()
            self.configure_github(n, g, "issue_closed_completed")
            original_issue, original_prs = g.issue, g.prs
            after_prs = False
            reads_after_prs = 0
            def mark_prs_read(branch):
                nonlocal after_prs
                after_prs = True
                return original_prs(branch)
            def edit_during_final_read(number):
                nonlocal reads_after_prs
                if after_prs:
                    reads_after_prs += 1
                if reads_after_prs == 2:
                    n.page["properties"][UPDATE] = {"type": "checkbox", **prop("checkbox", True)}
                    n.page["last_edited_time"] = "new-request-during-final-issue-read"
                return original_issue(number)
            n.patches.clear()
            with patch.object(g, "prs", side_effect=mark_prs_read), patch.object(g, "issue", side_effect=edit_during_final_read):
                self.assertNotEqual(engine.run()[0]["result"], "error")
            self.assert_owned(n, "완료", {"start": "2026-10-16"}, allow_completion=True)
            self.assertTrue(value(n.page, UPDATE))

    def test_cancellation_during_pr_lookup_is_not_overwritten_by_merged_pr_auto_close(self):
        with self.task("리뷰 중", {"start": "2026-10-16"}) as (n, g, engine):
            engine.run()
            self.configure_github(n, g, "merged")
            original_prs = g.prs
            def cancel_during_pr_lookup(branch):
                g.data[1].update(state="closed", state_reason="not_planned")
                return original_prs(branch)
            n.patches.clear()
            with patch.object(g, "prs", side_effect=cancel_during_pr_lookup), patch.object(g, "edit_issue", wraps=g.edit_issue) as edit:
                self.assertEqual(engine.run()[0]["result"], "synced")
                self.assertFalse(any(call.args[1].get("state") == "closed" for call in edit.call_args_list))
            self.assertEqual(g.data[1]["state_reason"], "not_planned")
            self.assert_owned(n, "리뷰 중", {"start": "2026-10-16"})

    def test_reopening_during_pr_lookup_is_not_reclosed_by_old_merge_history(self):
        with self.task("개발 중", None) as (n, g, engine):
            engine.run()
            self.configure_github(n, g, "merged")
            g.data[1].update(state="closed", state_reason="completed")
            original_prs = g.prs
            def reopen_during_pr_lookup(branch):
                g.data[1].update(state="open", state_reason="reopened")
                g.events[1] = [{"event": "reopened", "created_at": "2026-10-16T00:00:00Z"}]
                return original_prs(branch)
            n.patches.clear()
            with patch.object(g, "prs", side_effect=reopen_during_pr_lookup), patch.object(g, "edit_issue", wraps=g.edit_issue) as edit:
                self.assertEqual(engine.run()[0]["result"], "synced")
                self.assertFalse(any(call.args[1].get("state") == "closed" for call in edit.call_args_list))
            self.assertEqual(g.data[1]["state"], "open")
            self.assertIn(value(n.page, "Branch"), g.branches)
            self.assert_owned(n, "개발 중", None)

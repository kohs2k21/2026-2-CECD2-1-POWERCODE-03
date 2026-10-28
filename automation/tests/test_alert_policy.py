"""Synthetic alert-noise cases: classification, exit codes, repeat suppression, Notion rewrites."""
import copy
import datetime as dt
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from automation.services.sync.app import (
    ALERT_KEY, SUMMARY_ROWS, Engine, State, SyncError, alert_outcome, classify_code, classify_result,
    report_alerts, run_once, value,
)
from automation.tests.test_branch_cleanup import CleanupGitHub
from automation.tests.test_sync import CFG, FakeGitHub, FakeNotion

T0 = dt.datetime(2026, 10, 28, 0, 0, tzinfo=dt.timezone.utc)
PAGE = "11111111-1111-1111-1111-111111111111"
OTHER = "22222222-2222-2222-2222-222222222222"
SECRET = "ghp_" + "A" * 36


def page_error(code, page_id=PAGE):
    return {"page_id": page_id, "result": "error", "code": code}


class FakeEngine:
    def __init__(self, results=None, error=None, crash=None):
        self.results, self.error, self.crash = results or [], error, crash

    def run(self):
        if self.crash:
            raise self.crash
        if self.error:
            raise SyncError(self.error)
        return copy.deepcopy(self.results)


class FailingCreateGitHub(FakeGitHub):
    def create_issue(self, payload):
        raise SyncError("HTTP_503")


class ClassificationTests(unittest.TestCase):
    def test_page_input_codes_are_warnings(self):
        for code in ("UNSUPPORTED_MENTION_USE_PLAIN_TEXT", "UNSUPPORTED_LINK", "UNSUPPORTED_BLOCK_image",
                     "UNSUPPORTED_BLOCK_child_page", "INVALID_TABLE", "BODY_TOO_DEEP", "BODY_TOO_LARGE",
                     "TITLE_OR_TYPE_REQUIRED", "AREA_INVALID", "MILESTONE_NOT_IN_NOTION_OPTIONS",
                     "ENGLISH_BRANCH_SLUG_REQUIRED", "TYPE_LOCKED_AFTER_CREATION"):
            with self.subTest(code=code):
                self.assertEqual(classify_code(code), "input")

    def test_cleanup_holds_are_warnings(self):
        for code in ("OPEN_PR_PREVENTS_BRANCH_DELETE", "BRANCH_HAS_UNMERGED_COMMITS_NOT_DELETED",
                     "BRANCH_CHANGED_AFTER_MERGE_NOT_DELETED", "CLEANUP_BASE_REQUIRES_REVIEW"):
            with self.subTest(code=code):
                self.assertEqual(classify_code(code), "hold")

    def test_item_level_transient_codes_are_warnings(self):
        for code in ("NETWORK_UNCERTAIN_RETRY_NEXT_RUN", "HTTP_429", "HTTP_500", "HTTP_502", "HTTP_503", "HTTP_599"):
            with self.subTest(code=code):
                self.assertEqual(classify_code(code), "transient")

    def test_security_integrity_system_and_unknown_codes_fail(self):
        for code in ("POSSIBLE_SECRET_IN_TASK", "ASSIGNEE_MAPPING_REQUIRED", "ISSUE_OWNERSHIP_MISMATCH", "DUPLICATE_MARKERS_REVIEW_REQUIRED",
                     "INVALID_ISSUE_MARKER", "INVALID_IMMUTABLE_BRANCH_METADATA", "INVALID_BRANCH_BASE",
                     "LEGACY_ACTIVE_MAIN_PR_REQUIRES_REVIEW", "LOCAL_MAPPING_MISMATCH", "REF_DELETE_NOT_ALLOWED",
                     "REF_DELETE_LEASE_FAILED_REVIEW_BRANCH", "FOREIGN_TARGET", "FOREIGN_DATA_SOURCE",
                     "FOREIGN_ISSUE_URL", "INCOMPLETE_ISSUE_LINK", "ISSUE_LINK_MISMATCH", "INTERNAL_ERROR",
                     "INVALID_BRANCH_COMPARISON", "NOTION_SCHEMA_INVALID", "GITHUB_REPOSITORY_MISMATCH",
                     "HTTP_400", "HTTP_401", "HTTP_403", "HTTP_404", "HTTP_422", "HTTP_600", "HTTP_5000",
                     "REQUEST_FAILED", "UNSUPPORTED_BLOCK", "SOMETHING_NEW", "", None):
            with self.subTest(code=code):
                self.assertEqual(classify_code(code), "failure")

    def test_run_level_error_fails_even_with_transient_code(self):
        self.assertEqual(classify_result({"result": "error", "code": "HTTP_503"}), "failure")
        self.assertEqual(classify_result({"result": "error", "code": "NETWORK_UNCERTAIN_RETRY_NEXT_RUN"}), "failure")
        self.assertEqual(classify_result(page_error("HTTP_503")), "transient")
        self.assertEqual(classify_result({"issue_number": 9, "branch": "fix/9-a", "result": "error",
                                          "code": "OPEN_PR_PREVENTS_BRANCH_DELETE"}), "hold")
        self.assertIsNone(classify_result({"page_id": PAGE, "result": "synced"}))


class SuppressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.tmp.name) / "state.db")

    def tearDown(self):
        self.state.db.close()
        self.tmp.cleanup()

    def exit_code(self, results, at):
        return alert_outcome(results, self.state, at)["exit_code"]

    def test_warnings_only_exit_zero_and_store_nothing(self):
        results = [page_error("ENGLISH_BRANCH_SLUG_REQUIRED"), page_error("UNSUPPORTED_LINK", OTHER),
                   {"issue_number": 9, "branch": "fix/9-a", "result": "error", "code": "OPEN_PR_PREVENTS_BRANCH_DELETE"},
                   page_error("HTTP_503", OTHER), {"page_id": PAGE, "result": "synced"}]
        for minutes in range(0, 60, 5):
            outcome = alert_outcome(results, self.state, T0 + dt.timedelta(minutes=minutes))
            self.assertEqual(outcome["exit_code"], 0)
            self.assertEqual(len(outcome["warnings"]), 4)
        self.assertIsNone(self.state.get_alert(ALERT_KEY))

    def test_new_failure_alerts_then_repeats_are_suppressed_until_24_hours(self):
        results = [page_error("POSSIBLE_SECRET_IN_TASK"), page_error("UNSUPPORTED_LINK", OTHER)]
        self.assertEqual(self.exit_code(results, T0), 1)
        for minutes in (5, 60, 24 * 60 - 1):
            outcome = alert_outcome(results, self.state, T0 + dt.timedelta(minutes=minutes))
            self.assertEqual(outcome["exit_code"], 0)
            self.assertEqual([e["code"] for e in outcome["suppressed"]], ["POSSIBLE_SECRET_IN_TASK"])
            self.assertEqual(outcome["failures"], [])
        later = T0 + dt.timedelta(hours=24)
        self.assertEqual(self.exit_code(results, later), 1)
        self.assertEqual(self.state.get_alert(ALERT_KEY)["alerted_at"], later.isoformat())
        self.assertEqual(self.exit_code(results, later + dt.timedelta(hours=1)), 0)

    def test_fingerprint_ignores_order_and_warning_changes(self):
        a, b = page_error("ISSUE_OWNERSHIP_MISMATCH"), page_error("INTERNAL_ERROR", OTHER)
        self.assertEqual(self.exit_code([a, b], T0), 1)
        self.assertEqual(self.exit_code([b, page_error("HTTP_503", "x"), a], T0 + dt.timedelta(minutes=5)), 0)
        self.assertEqual(self.state.get_alert(ALERT_KEY)["fingerprint"],
                         [["page:" + PAGE, "ISSUE_OWNERSHIP_MISMATCH"], ["page:" + OTHER, "INTERNAL_ERROR"]])

    def test_changed_failure_set_alerts_again(self):
        base = [page_error("ISSUE_OWNERSHIP_MISMATCH")]
        self.assertEqual(self.exit_code(base, T0), 1)
        changes = ([page_error("ISSUE_LINK_MISMATCH")],
                   [page_error("ISSUE_LINK_MISMATCH", OTHER)],
                   [page_error("ISSUE_LINK_MISMATCH", OTHER), {"result": "error", "code": "HTTP_503"}],
                   [{"issue_number": 9, "branch": "fix/9-a", "result": "error", "code": "REF_DELETE_NOT_ALLOWED"}],
                   [{"issue_number": 9, "branch": "fix/9-b", "result": "error", "code": "REF_DELETE_NOT_ALLOWED"}])
        for minute, results in enumerate(changes, start=1):
            with self.subTest(results=results):
                self.assertEqual(self.exit_code(results, T0 + dt.timedelta(minutes=minute)), 1)

    def test_resolved_failures_clear_fingerprint_and_recurrence_alerts(self):
        results = [page_error("INVALID_ISSUE_MARKER")]
        self.assertEqual(self.exit_code(results, T0), 1)
        self.assertEqual(self.exit_code([page_error("UNSUPPORTED_LINK")], T0 + dt.timedelta(minutes=5)), 0)
        self.assertIsNone(self.state.get_alert(ALERT_KEY))
        self.assertEqual(self.exit_code(results, T0 + dt.timedelta(minutes=10)), 1)

    def test_run_level_transient_error_fails_then_repeat_is_suppressed(self):
        out = io.StringIO()
        self.assertEqual(run_once(FakeEngine(error="HTTP_503"), self.state, T0, {}, out), 1)
        self.assertEqual(self.state.get_alert(ALERT_KEY)["fingerprint"], [["run", "HTTP_503"]])
        self.assertEqual(run_once(FakeEngine(error="HTTP_503"), self.state, T0 + dt.timedelta(minutes=5), {}, out), 0)
        self.assertEqual(run_once(FakeEngine(error="HTTP_502"), self.state, T0 + dt.timedelta(minutes=10), {}, out), 1)

    def test_shrinking_failure_set_alerts_again(self):
        a, b = page_error("ISSUE_OWNERSHIP_MISMATCH"), page_error("INTERNAL_ERROR", OTHER)
        self.assertEqual(self.exit_code([a, b], T0), 1)
        self.assertEqual(self.exit_code([a], T0 + dt.timedelta(minutes=5)), 1)
        self.assertEqual(self.exit_code([a], T0 + dt.timedelta(minutes=10)), 0)

    def test_future_alert_time_alerts_and_resets(self):
        results = [page_error("INTERNAL_ERROR")]
        future = T0 + dt.timedelta(hours=3)
        self.state.save_alert(ALERT_KEY, {"fingerprint": [["page:" + PAGE, "INTERNAL_ERROR"]],
                                          "alerted_at": future.isoformat()})
        self.assertEqual(self.exit_code(results, T0), 1)
        self.assertEqual(self.state.get_alert(ALERT_KEY)["alerted_at"], T0.isoformat())
        self.assertEqual(self.exit_code(results, T0 + dt.timedelta(minutes=5)), 0)

    def test_corrupt_alert_row_is_treated_as_missing(self):
        results = [page_error("INTERNAL_ERROR")]
        for raw in ("{not-json", "[1, 2]", "null", "\"text\""):
            with self.subTest(raw=raw):
                self.state.db.execute("INSERT INTO alerts VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                                      (ALERT_KEY, raw))
                self.state.db.commit()
                self.assertIsNone(self.state.get_alert(ALERT_KEY))
                self.assertEqual(self.exit_code(results, T0), 1)
                self.assertEqual(self.state.get_alert(ALERT_KEY)["alerted_at"], T0.isoformat())

    def test_unexpected_run_crash_is_run_level_internal_error(self):
        out = io.StringIO()
        crash = KeyError("token " + SECRET)
        self.assertEqual(run_once(FakeEngine(crash=crash), self.state, T0, {"GITHUB_ACTIONS": "true"}, out), 1)
        lines = out.getvalue().splitlines()
        self.assertEqual(json.loads(lines[0])["results"], [{"result": "error", "code": "INTERNAL_ERROR"}])
        self.assertEqual(lines[1:], ["::error title=notion-sync failure::INTERNAL_ERROR"])
        self.assertEqual(run_once(FakeEngine(crash=crash), self.state, T0 + dt.timedelta(minutes=5), {}, out), 0)
        for forbidden in (SECRET, "KeyError", "Traceback"):
            self.assertNotIn(forbidden, out.getvalue())

    def test_unparseable_checkpoint_alerts_instead_of_suppressing(self):
        results = [page_error("INTERNAL_ERROR")]
        fingerprint = [["page:" + PAGE, "INTERNAL_ERROR"]]
        for stored in ({"fingerprint": fingerprint}, {"fingerprint": fingerprint, "alerted_at": "bad"},
                       {"fingerprint": fingerprint, "alerted_at": "2026-10-28T00:00:00"}):
            with self.subTest(stored=stored):
                self.state.save_alert(ALERT_KEY, stored)
                self.assertEqual(self.exit_code(results, T0), 1)

    def test_existing_checkpoint_without_alert_table_keeps_mappings(self):
        path = Path(self.tmp.name) / "legacy.db"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE mappings (key TEXT PRIMARY KEY, data TEXT NOT NULL)")
        db.execute("INSERT INTO mappings VALUES (?,?)", ("repo:page", json.dumps({"number": 7})))
        db.commit()
        db.close()
        legacy = State(path)
        try:
            self.assertEqual(legacy.get("repo:page"), {"number": 7})
            self.assertEqual(alert_outcome([page_error("INTERNAL_ERROR")], legacy, T0)["exit_code"], 1)
            self.assertEqual(legacy.get("repo:page"), {"number": 7})
        finally:
            legacy.db.close()


class NotionRewriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.tmp.name) / "state.db")

    def tearDown(self):
        self.state.db.close()
        self.tmp.cleanup()

    def engine(self, github=None):
        self.n = FakeNotion()
        return Engine(copy.deepcopy(CFG), self.n, github or FakeGitHub(), self.state)

    def test_same_error_is_not_rewritten_but_changed_error_is(self):
        engine = self.engine()
        self.n.set("브랜치 요약", "rich_text", "한글 요약")
        edits = self.n.edits
        self.assertEqual(engine.run()[0]["code"], "ENGLISH_BRANCH_SLUG_REQUIRED")
        self.assertEqual(self.n.edits, edits + 1)
        stamp = self.n.page["last_edited_time"]
        for _ in range(3):
            self.assertEqual(engine.run()[0]["code"], "ENGLISH_BRANCH_SLUG_REQUIRED")
        self.assertEqual(self.n.edits, edits + 1)
        self.assertEqual(self.n.page["last_edited_time"], stamp)
        self.n.page["properties"]["영역"] = {"type": "multi_select", "multi_select": [{"name": "Unknown"}]}
        self.assertEqual(engine.run()[0]["code"], "AREA_INVALID")
        self.assertEqual(self.n.edits, edits + 2)
        self.assertEqual(value(self.n.page, "GitHub 동기화"), "오류")
        self.assertEqual(value(self.n.page, "오류 메시지"), "AREA_INVALID")

    def test_page_patched_during_failed_run_is_rewritten(self):
        engine = self.engine(FailingCreateGitHub())
        for _ in range(3):
            self.assertEqual(engine.run()[0]["code"], "HTTP_503")
            self.assertEqual(value(self.n.page, "GitHub 동기화"), "오류")
            self.assertEqual(value(self.n.page, "오류 메시지"), "HTTP_503")

    def test_repeated_cleanup_hold_writes_notion_once(self):
        n, g = FakeNotion(), CleanupGitHub()
        engine = Engine(copy.deepcopy(CFG), n, g, self.state)
        self.assertEqual(engine.run()[0]["result"], "synced")
        branch = value(n.page, "Branch")
        g.data[1].update(state="closed", state_reason="completed")
        g.unmerged[("head1", "dev")] = True
        edits = n.edits
        for run in range(4):
            result = engine.run()[0]
            self.assertEqual(result["code"], "BRANCH_HAS_UNMERGED_COMMITS_NOT_DELETED")
            self.assertEqual(n.edits, edits + 1, run)
            self.assertEqual(value(n.page, "GitHub 동기화"), "오류")
            self.assertEqual(value(n.page, "오류 메시지"), "BRANCH_HAS_UNMERGED_COMMITS_NOT_DELETED")
            self.assertEqual(value(n.page, "상태"), "완료")
            self.assertEqual(value(n.page, "Issue 번호"), 1)
            self.assertEqual(value(n.page, "Branch"), branch)
            self.assertIn(branch, g.branches)
        g.unmerged[("head1", "dev")] = False
        self.assertEqual(engine.run()[0]["result"], "synced")
        self.assertEqual(n.edits, edits + 2)
        self.assertEqual(value(n.page, "GitHub 동기화"), "완료")
        self.assertEqual(value(n.page, "오류 메시지"), "")
        self.assertNotIn(branch, g.branches)

    def test_cleanup_hold_change_rewrites_error_once(self):
        n, g = FakeNotion(), CleanupGitHub()
        engine = Engine(copy.deepcopy(CFG), n, g, self.state)
        engine.run()
        branch = value(n.page, "Branch")
        g.data[1].update(state="closed", state_reason="completed")
        engine.run()
        edits = n.edits
        g.pulls = [{"number": 2, "state": "open", "merged_at": None, "html_url": f"https://github.com/{CFG['repository']}/pull/2",
                    "head": {"ref": branch, "sha": "head1", "repo": {"full_name": CFG["repository"]}}, "base": {"ref": "dev"}}]
        for _ in range(2):
            self.assertEqual(engine.run()[0]["code"], "OPEN_PR_PREVENTS_BRANCH_DELETE")
        self.assertEqual(n.edits, edits + 1)
        self.assertEqual(value(n.page, "오류 메시지"), "OPEN_PR_PREVENTS_BRANCH_DELETE")
        self.assertEqual(value(n.page, "Pull Request"), f"https://github.com/{CFG['repository']}/pull/2")

    def test_input_error_run_exits_zero_with_warning_annotation(self):
        engine = self.engine()
        self.n.set("브랜치 요약", "rich_text", "한글 요약")
        out = io.StringIO()
        code = run_once(engine, self.state, T0, {"GITHUB_ACTIONS": "true"}, out)
        self.assertEqual(code, 0)
        lines = out.getvalue().splitlines()
        self.assertEqual(json.loads(lines[0])["results"][0]["code"], "ENGLISH_BRANCH_SLUG_REQUIRED")
        self.assertEqual(lines[1:], ["::warning title=notion-sync input::ENGLISH_BRANCH_SLUG_REQUIRED page=" + PAGE])
        self.assertNotIn("합성", out.getvalue())


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.tmp.name) / "state.db")
        self.summary = Path(self.tmp.name) / "summary.md"
        self.env = {"GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(self.summary)}

    def tearDown(self):
        self.state.db.close()
        self.tmp.cleanup()

    def results(self):
        return [
            {**page_error("POSSIBLE_SECRET_IN_TASK"), "title": "token " + SECRET, "body": "Authorization: Bearer x"},
            page_error("UNSUPPORTED_LINK\n::error::spoof " + SECRET[:4], OTHER + "\n::error::x"),
            {"issue_number": 9, "branch": "fix/9-private-branch", "result": "error", "code": "OPEN_PR_PREVENTS_BRANCH_DELETE"},
            {"result": "error", "code": "NOTION_SCHEMA_INVALID"},
        ]

    def report(self, at):
        out = io.StringIO()
        report_alerts(alert_outcome(self.results(), self.state, at), self.env, out)
        return out.getvalue()

    def test_annotations_and_summary_carry_only_codes_and_ids(self):
        text = self.report(T0)
        lines = text.splitlines()
        self.assertEqual(lines, [
            "::error title=notion-sync failure::POSSIBLE_SECRET_IN_TASK page=" + PAGE,
            "::error title=notion-sync failure::UNSUPPORTED_LINKerrorspoofghp_ page=" + OTHER,
            "::error title=notion-sync failure::NOTION_SCHEMA_INVALID",
            "::warning title=notion-sync hold::OPEN_PR_PREVENTS_BRANCH_DELETE issue=#9",
        ])
        summary = self.summary.read_text(encoding="utf-8")
        self.assertIn("실패 알림 (종료 코드 1)", summary)
        self.assertIn("`OPEN_PR_PREVENTS_BRANCH_DELETE issue=#9`", summary)
        for output in (text, summary):
            for forbidden in (SECRET, "Bearer", "token ", "private-branch", "\n::error::spoof", "::error::x"):
                self.assertNotIn(forbidden, output)

    def test_suppressed_repeat_is_reported_as_warning(self):
        self.report(T0)
        text = self.report(T0 + dt.timedelta(minutes=5))
        self.assertNotIn("::error", text)
        self.assertIn("::warning title=notion-sync repeated-failure::POSSIBLE_SECRET_IN_TASK page=" + PAGE, text)
        self.assertIn("반복 실패, 24시간 내 재알림 생략 (종료 코드 0)", self.summary.read_text(encoding="utf-8"))

    def test_summary_truncates_after_row_limit(self):
        results = [page_error("UNSUPPORTED_LINK", f"{i:08x}-0000-0000-0000-000000000000") for i in range(SUMMARY_ROWS + 5)]
        report_alerts(alert_outcome(results, self.state, T0), {"GITHUB_STEP_SUMMARY": str(self.summary)}, io.StringIO())
        summary = self.summary.read_text(encoding="utf-8")
        self.assertEqual(summary.count("| input |"), SUMMARY_ROWS)
        self.assertIn("| ... | 5건 생략 |", summary)
        self.assertIn("경고만 있음 (종료 코드 0)", summary)

    def test_no_annotations_outside_actions_and_no_summary_without_path(self):
        out = io.StringIO()
        report_alerts(alert_outcome(self.results(), self.state, T0), {}, out)
        self.assertEqual(out.getvalue(), "")
        self.assertFalse(self.summary.exists())


if __name__ == "__main__":
    unittest.main()

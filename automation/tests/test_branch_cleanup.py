"""Synthetic regression cases for issue-driven branch cleanup; no remote writes."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from automation.services.sync.app import Engine, GitHub, REPOSITORY, State, SyncError, marker, value
from automation.tests.test_sync import CFG, FakeGitHub, FakeNotion


class CleanupGitHub(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.unmerged = {}
        self.comparisons = []
        self.issue_reads = []
        self.issue_errors = {}
        self.events = {}

    def branch_names(self):
        return list(self.branches)

    def branch_head(self, name):
        return self.branches.get(name)

    def has_unmerged_commits(self, head, base):
        self.comparisons.append((head, base))
        return self.unmerged.get((head, base), True)

    def issue(self, number):
        self.issue_reads.append(number)
        if number in self.issue_errors:
            raise SyncError(self.issue_errors[number])
        if number not in self.data:
            raise SyncError("HTTP_404")
        return super().issue(number)

    def issue_events(self, number):
        return copy.deepcopy(self.events.get(number, []))


class BranchCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.n, self.g = FakeNotion(), CleanupGitHub()
        self.state = State(Path(self.tmp.name) / "state.db")
        self.engine = Engine(copy.deepcopy(CFG), self.n, self.g, self.state)
        self.branch = "fix/9-cleanup"
        self.issue = self.closed_issue(9, self.branch)

    def tearDown(self):
        self.state.db.close()
        self.tmp.cleanup()

    def closed_issue(self, number, branch, body=""):
        issue = {"number": number, "state": "closed", "state_reason": "completed",
                 "title": "Synthetic closed task", "body": body, "labels": [],
                 "html_url": f"https://github.com/{REPOSITORY}/issues/{number}"}
        self.g.data[number] = issue
        self.g.branches[branch] = "head1"
        return copy.deepcopy(issue)

    def pr(self, *, branch=None, base="dev", state="closed", merged=True,
           sha="head1", repository=REPOSITORY):
        pull = {"number": len(self.g.pulls) + 20, "state": state,
                "merged_at": "2026-10-06T12:00:00Z" if merged else None,
                "head": {"ref": branch or self.branch, "sha": sha,
                         "repo": {"full_name": repository}},
                "base": {"ref": base},
                "html_url": f"https://github.com/{REPOSITORY}/pull/20"}
        self.g.pulls.append(pull)
        return pull

    def cleanup(self, *, base="dev"):
        return self.engine.cleanup_branch(self.issue, self.branch, base)

    def assert_blocked(self, code, *, base="dev"):
        with self.assertRaisesRegex(SyncError, "^" + code + "$"):
            self.cleanup(base=base)
        self.assertIn(self.branch, self.g.branches)
        self.assertEqual(self.g.deleted, [])

    def linked_task(self):
        self.g.branches.clear()
        self.g.data.clear()
        self.assertEqual(self.engine.run()[0]["result"], "synced")
        return value(self.n.page, "Branch")

    def test_merged_closed_issue_deletes_current_merged_head(self):
        self.pr()
        self.assertEqual(self.cleanup(), "deleted")
        self.assertEqual(self.g.deleted, [self.branch])
        self.assertEqual(self.g.comparisons, [])

    def test_any_open_same_repository_head_pr_blocks_deletion(self):
        self.pr()
        for base in ("dev", "main", "release-candidate"):
            with self.subTest(base=base):
                opened = self.pr(base=base, state="open", merged=False)
                self.assert_blocked("OPEN_PR_PREVENTS_BRANCH_DELETE")
                self.g.pulls.remove(opened)

    def test_fork_and_other_head_open_prs_do_not_block_owned_branch(self):
        self.pr()
        self.pr(repository="someone/fork", state="open", merged=False)
        self.pr(branch="fix/10-other", state="open", merged=False)
        self.assertEqual(self.cleanup(), "deleted")

    def test_new_commits_after_merge_are_preserved_even_if_compare_is_empty(self):
        self.pr()
        self.g.branches[self.branch] = "new-head"
        self.g.unmerged[("new-head", "dev")] = False
        self.assert_blocked("BRANCH_CHANGED_AFTER_MERGE_NOT_DELETED")
        self.assertEqual(self.g.comparisons, [])

    def test_matching_any_merged_head_handles_branch_reuse(self):
        self.pr(sha="previous-head")
        self.pr(sha="head1")
        self.assertEqual(self.cleanup(), "deleted")

    def test_legacy_main_merged_branch_can_be_cleaned_against_main(self):
        self.pr(base="main")
        self.assertEqual(self.cleanup(base="main"), "deleted")

    def test_wrong_base_merge_is_not_proof_of_safe_deletion(self):
        self.pr(base="main")
        self.assert_blocked("CLEANUP_BASE_REQUIRES_REVIEW")
        self.assertEqual(self.g.comparisons, [])

    def test_no_merge_and_no_unique_commits_allows_cancellation_cleanup(self):
        self.g.unmerged[("head1", "dev")] = False
        self.g.data[9]["state_reason"] = "not_planned"
        self.assertEqual(self.cleanup(), "deleted")
        self.assertEqual(self.g.comparisons, [("head1", "dev")])

    def test_closed_unmerged_pr_with_unique_commits_is_preserved(self):
        self.pr(merged=False)
        self.assert_blocked("BRANCH_HAS_UNMERGED_COMMITS_NOT_DELETED")

    def test_missing_branch_is_successful_idempotent_cleanup(self):
        self.g.branches.pop(self.branch)
        self.assertEqual(self.cleanup(), "missing")
        self.assertEqual(self.g.deleted, [])
        self.assertEqual(self.g.comparisons, [])

    def test_issue_reopened_before_delete_preserves_branch(self):
        self.pr()
        self.g.data[9]["state"] = "open"
        self.assertEqual(self.cleanup(), "reopened")
        self.assertIn(self.branch, self.g.branches)
        self.assertEqual(self.g.deleted, [])
        self.assertIn(9, self.g.issue_reads)

    def test_concurrent_push_at_delete_keeps_lease_failure_visible(self):
        self.pr()
        with patch.object(self.g, "delete_branch", side_effect=SyncError("REF_DELETE_LEASE_FAILED_REVIEW_BRANCH")):
            with self.assertRaisesRegex(SyncError, "REF_DELETE_LEASE_FAILED_REVIEW_BRANCH"):
                self.cleanup()
        self.assertIn(self.branch, self.g.branches)

    def test_unknown_cleanup_base_requires_review(self):
        self.pr(base="other")
        self.assert_blocked("CLEANUP_BASE_REQUIRES_REVIEW", base="other")

    def test_sweep_cleans_closed_issue_without_notion_page_or_marker(self):
        self.pr()
        with patch.object(self.n, "pages", return_value=[]):
            self.engine.run()
        self.assertEqual(self.g.deleted, [self.branch])

    def test_sweep_preserves_open_issue_and_pull_request_number(self):
        self.g.data[9]["state"] = "open"
        pull_branch = "fix/10-pull-number"
        self.closed_issue(10, pull_branch)
        self.g.data[10]["pull_request"] = {"url": "synthetic"}
        self.g.unmerged[("head1", "dev")] = False
        self.engine.cleanup_closed_branches()
        self.assertIn(self.branch, self.g.branches)
        self.assertIn(pull_branch, self.g.branches)
        self.assertEqual(self.g.deleted, [])

    def test_sweep_only_accepts_exact_branch_naming_contract(self):
        self.g.branches.clear()
        invalid = ("main", "dev", "feat/no-id", "feat/09-leading-zero", "feat/0-zero",
                   "unknown/9-kind", "fix/9-two--dashes", "fix/9-trailing-",
                   "fix/9-under_score", "fix/9-UPPER", "fix/9-extra/path")
        self.g.branches.update({name: "head1" for name in invalid})
        self.g.unmerged[("head1", "dev")] = False
        self.engine.cleanup_closed_branches()
        self.assertEqual(set(self.g.branches), set(invalid))
        self.assertEqual(self.g.issue_reads, [])

    def test_sweep_matching_marker_retains_legacy_cleanup_base(self):
        meta = {"page_id": self.n.page["id"], "repository": REPOSITORY,
                "kind": "fix", "slug": "cleanup", "base": "main"}
        self.g.data[9]["body"] = marker(meta)
        self.pr(base="main")
        self.engine.cleanup_closed_branches()
        self.assertEqual(self.g.deleted, [self.branch])

    def test_sweep_rejects_foreign_or_inconsistent_ownership_markers(self):
        meta = {"page_id": self.n.page["id"], "repository": REPOSITORY,
                "kind": "fix", "slug": "cleanup", "base": "dev"}
        self.g.unmerged[("head1", "dev")] = False
        for changed in ({"repository": "other/repository"}, {"kind": "feat"},
                        {"slug": "different"}, {"base": "other"}):
            with self.subTest(changed=changed):
                self.g.data[9]["body"] = marker({**meta, **changed})
                self.engine.cleanup_closed_branches()
                self.assertIn(self.branch, self.g.branches)
                self.assertEqual(self.g.deleted, [])

    def test_sweep_malformed_marker_is_preserved(self):
        self.g.unmerged[("head1", "dev")] = False
        for body in ("<!-- notion-sync {not-json} -->", marker({})):
            with self.subTest(body=body):
                self.g.data[9]["body"] = body
                self.engine.cleanup_closed_branches()
                self.assertIn(self.branch, self.g.branches)
                self.assertEqual(self.g.deleted, [])

    def test_sweep_continues_after_missing_issue_and_api_failure(self):
        self.g.branches.clear()
        missing, denied, valid = "fix/8-missing", "fix/9-denied", "fix/10-valid"
        self.g.branches[missing] = "head1"
        self.g.branches[denied] = "head1"
        self.g.issue_errors[9] = "HTTP_403"
        self.closed_issue(10, valid)
        self.g.unmerged[("head1", "dev")] = False
        results = self.engine.cleanup_closed_branches()
        self.assertEqual(self.g.deleted, [valid])
        self.assertIn(missing, self.g.branches)
        self.assertIn(denied, self.g.branches)
        self.assertEqual(self.g.issue_reads[:3], [8, 9, 10])
        self.assertTrue(any(result.get("code") == "HTTP_403" for result in results))
        self.assertFalse(any(result.get("code") == "HTTP_404" for result in results))

    def test_linked_closed_issue_is_cleaned_once_and_never_recreated(self):
        self.g.branches.clear()
        self.g.data.clear()
        self.engine.run()
        branch = value(self.n.page, "Branch")
        self.g.data[1]["state"] = "closed"
        self.g.unmerged[("head1", "dev")] = False
        with patch.object(self.engine, "cleanup_branch", wraps=self.engine.cleanup_branch) as cleanup:
            self.engine.run()
            self.assertEqual(cleanup.call_count, 1)
        self.assertNotIn(branch, self.g.branches)
        self.engine.run()
        self.assertNotIn(branch, self.g.branches)
        self.assertEqual(self.g.deleted, [branch])

    def test_explicit_reopening_after_merge_survives_lost_checkpoint(self):
        self.g.branches.clear()
        self.g.data.clear()
        self.assertEqual(self.engine.run()[0]["result"], "synced")
        branch = value(self.n.page, "Branch")
        self.pr(branch=branch)
        self.g.events[1] = [{"event": "reopened", "created_at": "2026-10-07T01:00:00Z"}]
        self.state.db.execute("DELETE FROM mappings")
        self.state.db.commit()
        self.assertEqual(self.engine.run()[0]["result"], "synced")
        self.assertEqual(self.g.data[1]["state"], "open")
        self.assertIn(branch, self.g.branches)
        self.assertEqual(self.g.deleted, [])

    def test_reused_branch_with_new_open_pr_is_not_closed_by_old_merge(self):
        self.g.branches.clear()
        self.g.data.clear()
        self.assertEqual(self.engine.run()[0]["result"], "synced")
        branch = value(self.n.page, "Branch")
        self.pr(branch=branch)
        self.pr(branch=branch, base="main", state="open", merged=False, sha="new-head")
        self.g.branches[branch] = "new-head"
        self.assertEqual(self.engine.run()[0]["result"], "synced")
        self.assertEqual(self.g.data[1]["state"], "open")
        self.assertEqual(self.g.branches[branch], "new-head")
        self.assertEqual(self.g.deleted, [])

    def test_unmerged_closed_issue_preserves_notion_task_status(self):
        branch = self.linked_task()
        self.g.data[1].update({"state": "closed", "state_reason": "completed",
                               "closed_at": "2026-10-07T01:00:00Z"})
        self.g.unmerged[("head1", "dev")] = False
        self.assertEqual(self.engine.run()[0]["result"], "synced")
        self.assertEqual(value(self.n.page, "상태"), "백로그")
        self.assertIsNone(value(self.n.page, "완료일"))
        self.assertEqual(self.g.deleted, [branch])

    def test_unmerged_not_planned_issue_preserves_notion_task_status(self):
        branch = self.linked_task()
        self.g.data[1].update({"state": "closed", "state_reason": "not_planned",
                               "closed_at": "2026-10-07T01:00:00Z"})
        self.g.unmerged[("head1", "dev")] = False
        self.assertEqual(self.engine.run()[0]["result"], "synced")
        self.assertEqual(value(self.n.page, "상태"), "백로그")
        self.assertIsNone(value(self.n.page, "완료일"))
        self.assertEqual(self.g.deleted, [branch])

    def test_prior_merge_preserves_explicit_not_planned_closure(self):
        branch = self.linked_task()
        self.pr(branch=branch)
        self.g.data[1].update({"state": "closed", "state_reason": "not_planned",
                               "closed_at": "2026-10-07T01:00:00Z"})
        self.assertEqual(self.engine.run()[0]["result"], "synced")
        self.assertEqual(self.g.data[1]["state"], "closed")
        self.assertEqual(self.g.data[1]["state_reason"], "not_planned")
        self.assertEqual(value(self.n.page, "상태"), "백로그")
        self.assertIsNone(value(self.n.page, "완료일"))
        self.assertEqual(self.g.deleted, [branch])

    def test_compare_uses_commit_count_even_when_empty_commit_has_no_files(self):
        client = GitHub("synthetic-test-token", copy.deepcopy(CFG))
        for ahead, expected in ((1, True), (0, False)):
            with self.subTest(ahead=ahead), patch.object(client, "call", return_value={"ahead_by": ahead, "files": []}) as call:
                self.assertEqual(client.has_unmerged_commits("head1", "dev"), expected)
                call.assert_called_once_with("GET", "/compare/dev...head1")


if __name__ == "__main__":
    unittest.main()

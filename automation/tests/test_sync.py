import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from automation.services.sync.app import (
    Engine, State, SyncError, REPOSITORY, TARGET, DATA_SOURCE_ID, CREATE, UPDATE,
    EXPECTED_SCHEMA, Notion, check_access, validate_config, prop, value, marker,
    render_blocks, metadata, assert_safe_text, GitHub,
)


CFG = json.loads((Path(__file__).resolve().parents[1] / "config/project.json").read_text(encoding="utf-8"))


def task():
    def typed(kind, val):
        return {"type": kind, **prop(kind, val)}
    return {"id": "11111111-1111-1111-1111-111111111111", "last_edited_time": "t0",
        "parent": {"data_source_id": CFG["notion_data_source_id"]}, "properties": {
            "작업명": typed("rich_text", "합성 시험 작업"), "유형": typed("select", "✨ feat"),
            "우선순위": typed("select", "P1"), "영역": {"type": "multi_select", "multi_select": [{"name": "Automation"}]},
            "대상 저장소": typed("select", TARGET), "브랜치 요약": typed("rich_text", "sample-task"),
            "상태": typed("select", "백로그"), CREATE: typed("checkbox", True), UPDATE: typed("checkbox", False)}}


class FakeNotion:
    def __init__(self):
        self.page = task()
        self.milestones = {"M0 협업 기반 정리", "M1 중간고사", "M2 기말고사", "M3 평가·발표·고도화"}
        self.text = "## 작업\n합성 데이터만 사용\n- [ ] 통과"
        self.edits = 0
        self.fail_patch = False

    def verify_data_source(self):
        return True

    def pages(self):
        return [copy.deepcopy(self.page)]

    def get(self, _):
        return copy.deepcopy(self.page)

    def body(self, _):
        return self.text

    def patch(self, _, properties):
        if self.fail_patch:
            raise SyncError("HTTP_503")
        for key, p in properties.items():
            self.page["properties"][key] = {"type": next(iter(p)), **copy.deepcopy(p)}
        self.edits += 1
        self.page["last_edited_time"] = f"t{self.edits}"

    def set(self, key, kind, val):
        self.patch(None, {key: prop(kind, val)})


class FakeGitHub:
    def __init__(self):
        self.data, self.branches, self.pulls = {}, {}, []
        self.milestone_titles = []
        self.lost_response = False
        self.fail_branch = False
        self.deleted = []
        self.followups = 0
        self.changed = False
        self.lost_draft_response = False
        self.fail_assignment = False
        self.assignments = []

    def verify_access(self):
        return REPOSITORY

    def has_changes(self, branch, base):
        return self.changed

    def create_draft(self, branch, base, title, body, assignees):
        pr = {"number": 2, "state": "open", "draft": True, "merged_at": None,
              "title": title, "body": body,
              "head": {"ref": branch, "sha": "head1", "repo": {"full_name": REPOSITORY}},
              "base": {"ref": base}, "html_url": f"https://github.com/{REPOSITORY}/pull/2"}
        self.pulls.append(pr)
        if self.lost_draft_response:
            self.lost_draft_response = False
            raise SyncError("NETWORK_UNCERTAIN_RETRY_NEXT_RUN")
        return copy.deepcopy(pr)

    def assign_pr(self, number, people):
        if self.fail_assignment:
            self.fail_assignment = False
            raise SyncError("HTTP_503")
        self.assignments.append((number, people))

    def issues(self):
        return list(copy.deepcopy(self.data).values())

    def issue(self, number):
        return copy.deepcopy(self.data[number])

    def create_issue(self, payload):
        num = len(self.data) + 1
        self.data[num] = {**payload, "number": num, "html_url": f"https://github.com/{REPOSITORY}/issues/{num}",
            "state": "open", "labels": [{"name": x} for x in payload["labels"]]}
        if self.lost_response:
            self.lost_response = False
            raise SyncError("NETWORK_UNCERTAIN_RETRY_NEXT_RUN")
        return copy.deepcopy(self.data[num])

    def edit_issue(self, number, payload):
        p = copy.deepcopy(payload)
        if "labels" in p:
            p["labels"] = [{"name": x} for x in p["labels"]]
        self.data[number].update(p)

    def ensure_labels(self, labels):
        pass

    def milestone(self, title):
        self.milestone_titles.append(title)
        return 1 if title else None

    def ensure_branch(self, name, base):
        if self.fail_branch:
            self.fail_branch = False
            raise SyncError("HTTP_503")
        self.branches.setdefault(name, "head1")

    def prs(self, branch):
        return copy.deepcopy(self.pulls)

    def delete_branch(self, name, head):
        if name in self.branches and self.branches[name] != head:
            raise SyncError("BRANCH_CHANGED_AFTER_MERGE_NOT_DELETED")
        self.branches.pop(name, None)
        self.deleted.append(name)

    def hotfix_followup(self):
        self.followups += 1
        return "https://github.com/" + REPOSITORY + "/pull/3"


class SyncTests(unittest.TestCase):
    def test_new_task_targets_dev_while_release_stays_main(self):
        self.run_one()
        self.assertEqual(metadata(self.g.data[1]["body"])["base"], "dev")
        self.assertEqual(CFG["release_branch"], "main")
        self.g.changed = True
        self.run_one()
        self.assertEqual(self.g.pulls[0]["base"]["ref"], "dev")

    def legacy_main_link(self):
        self.run_one()
        meta = metadata(self.g.data[1]["body"])
        self.g.data[1]["body"] = self.g.data[1]["body"].replace(
            marker(meta), marker({**meta, "base": "main"}), 1)
        key = REPOSITORY + ":" + self.n.page["id"]
        saved = self.state.get(key)
        self.state.save(key, {**saved, "base": "main"})

    def test_active_legacy_without_pr_migrates_to_dev_without_resetting_branch(self):
        self.legacy_main_link()
        branch = value(self.n.page, "Branch")
        self.g.branches[branch] = "existing-work"
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(metadata(self.g.data[1]["body"])["base"], "dev")
        self.assertEqual(self.g.branches[branch], "existing-work")
        self.assertEqual(len(self.g.data), 1)
        key = REPOSITORY + ":" + self.n.page["id"]
        self.assertEqual(self.state.get(key)["base"], "dev")
        self.g.changed = True
        self.run_one()
        self.assertEqual(self.g.pulls[0]["base"]["ref"], "dev")

    def test_active_legacy_main_pr_requires_review_without_metadata_changes(self):
        self.legacy_main_link()
        self.pr(base="main")
        before = self.g.data[1]["body"]
        self.assertEqual(self.run_one()["code"], "LEGACY_ACTIVE_MAIN_PR_REQUIRES_REVIEW")
        self.assertEqual(self.g.data[1]["body"], before)
        self.assertEqual(self.g.pulls[0]["base"]["ref"], "main")
        self.assertEqual(len(self.g.pulls), 1)

    def test_active_legacy_with_existing_dev_pr_migrates_and_links_without_duplicate(self):
        self.legacy_main_link()
        branch = value(self.n.page, "Branch")
        self.g.branches[branch] = "existing-work"
        self.pr(base="dev", draft=True)
        self.g.changed = True
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(metadata(self.g.data[1]["body"])["base"], "dev")
        self.assertEqual(value(self.n.page, "Pull Request"), self.g.pulls[0]["html_url"])
        self.assertEqual(len(self.g.pulls), 1)
        self.assertEqual(self.g.branches[branch], "existing-work")

    def test_closed_legacy_main_history_remains_compatible(self):
        self.legacy_main_link()
        self.g.data[1]["state"] = "closed"
        self.n.set("상태", "select", "취소")
        self.pr(state="closed", base="main")
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(metadata(self.g.data[1]["body"])["base"], "main")
        self.assertEqual(len(self.g.pulls), 1)

    def test_merged_legacy_main_history_completes_without_dev_pr(self):
        self.legacy_main_link()
        self.pr(merged="2026-09-10T12:00:00Z", state="closed", base="main")
        self.assertEqual(self.run_one()["result"], "completed")
        self.assertEqual(metadata(self.g.data[1]["body"])["base"], "main")
        self.assertEqual(self.g.data[1]["state"], "closed")
        self.assertEqual(len(self.g.pulls), 1)

    def test_legacy_migration_retries_after_marker_write_failure(self):
        self.legacy_main_link()
        with patch.object(self.g, "edit_issue", side_effect=SyncError("HTTP_503")):
            self.assertEqual(self.run_one()["code"], "HTTP_503")
        self.assertEqual(metadata(self.g.data[1]["body"])["base"], "main")
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(metadata(self.g.data[1]["body"])["base"], "dev")
        self.assertEqual(len(self.g.data), 1)

    def test_legacy_migration_recovers_without_local_checkpoint(self):
        self.legacy_main_link()
        self.state.db.execute("DELETE FROM mappings")
        self.state.db.commit()
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(metadata(self.g.data[1]["body"])["base"], "dev")
        self.assertEqual(len(self.g.data), 1)

    def test_confirmed_teammates_map_to_assignable_github_accounts(self):
        self.n.page["properties"]["담당자"] = {"type": "people", "people": [
            {"id": "d798e1d8-b088-467a-8fff-726df0ba9011"},
            {"id": "ac8cce9d-4260-4fb9-bd8d-27b5cc9df9ef"}]}
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(self.g.data[1]["assignees"], ["juhno1023", "KRMayD"])
        self.g.changed = True
        self.run_one()
        self.assertEqual(self.g.assignments, [(2, ["juhno1023", "KRMayD"])])

    def test_unconfirmed_invitee_is_not_mapped_or_created(self):
        self.n.page["properties"]["담당자"] = {"type": "people", "people": [
            {"id": "d34bec6b-2af0-44cb-a462-ff849c3e2d25"}]}
        self.assertEqual(self.run_one()["code"], "ASSIGNEE_MAPPING_REQUIRED")
        self.assertFalse(self.g.data)
        self.assertFalse(self.g.branches)
        self.assertFalse(self.g.pulls)

    def test_assignee_allowlist_cannot_be_widened_by_config_only(self):
        cfg = copy.deepcopy(CFG)
        cfg["assignees"]["unapproved-id"] = "unapproved-login"
        with self.assertRaisesRegex(SyncError, "CONFIG_OUTSIDE_APPROVED_SCOPE"):
            validate_config(cfg)

    def test_automatic_draft_description_uses_bullets(self):
        self.run_one()
        self.g.changed = True
        self.run_one()
        lines = self.g.pulls[0]["body"].splitlines()
        self.assertTrue(all(line.startswith("- ") for line in lines[1:] if line))

    def test_issue_type_prefix_tracks_notion_title_without_duplicate_pr_prefix(self):
        self.run_one()
        self.assertEqual(self.g.data[1]["title"], "feat(automation): ✨ 합성 시험 작업")
        self.n.set("작업명", "rich_text", "[feat] 새 작업명")
        self.run_one()
        self.assertEqual(self.g.data[1]["title"], "feat(automation): ✨ 새 작업명")
        self.g.changed = True
        self.run_one()
        self.assertEqual(self.g.pulls[0]["title"], "feat(automation): ✨ 새 작업명 (#1)")
        self.assertEqual(value(self.n.page, "작업명"), "[feat] 새 작업명")

    def test_manual_github_issue_title_survives_digest_change_unless_requested(self):
        self.run_one()
        self.g.data[1]["title"] = "Human issue title"
        self.n.text += "\n- 새 본문"
        self.run_one()
        self.assertEqual(self.g.data[1]["title"], "Human issue title")
        self.n.set(UPDATE, "checkbox", True)
        self.run_one()
        self.assertEqual(self.g.data[1]["title"], "feat(automation): ✨ 합성 시험 작업")

    def test_unknown_assignee_blocks_linked_issue_without_request_checkbox(self):
        self.run_one()
        self.assertFalse(value(self.n.page, CREATE, False))
        self.assertFalse(value(self.n.page, UPDATE, False))
        self.n.page["properties"]["담당자"] = {"type": "people", "people": [{"id": "unmapped"}]}
        self.assertEqual(self.run_one()["code"], "ASSIGNEE_MAPPING_REQUIRED")

    def test_existing_legacy_plain_title_gets_prefix_without_title_checkpoint(self):
        self.legacy_main_link()
        key = REPOSITORY + ":" + self.n.page["id"]
        saved = self.state.get(key)
        saved.pop("issue_title")
        self.state.save(key, saved)
        self.g.data[1]["title"] = value(self.n.page, "작업명")
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(self.g.data[1]["title"], "feat(automation): ✨ 합성 시험 작업")

    def test_conventional_source_title_uses_first_area_without_duplicate_prefix(self):
        self.n.set("영역", "multi_select", [{"name": "Infra"}, {"name": "Backend"}])
        source_title = "[feat] feat(infra): ✨ [feat] 새 작업명"
        self.n.set("작업명", "rich_text", source_title)
        self.g.changed = True
        self.run_one()
        self.assertEqual(self.g.data[1]["title"], "feat(infra): ✨ 새 작업명")
        self.assertEqual(self.g.pulls[0]["title"], "feat(infra): ✨ 새 작업명 (#1)")
        self.assertEqual(value(self.n.page, "작업명"), source_title)
        self.assertTrue({"area:infra", "area:backend"}.issubset(
            {label["name"] for label in self.g.data[1]["labels"]}))

    def test_previous_bot_bracket_title_migrates_with_or_without_checkpoint(self):
        for checkpoint in (True, False):
            with self.subTest(checkpoint=checkpoint):
                self.run_one()
                key = REPOSITORY + ":" + self.n.page["id"]
                saved = self.state.get(key)
                previous_title = "[feat] 합성 시험 작업"
                if checkpoint:
                    saved["issue_title"] = previous_title
                else:
                    saved.pop("issue_title", None)
                saved["digest"] = "previous-format-digest"
                self.state.save(key, saved)
                self.g.data[1]["title"] = previous_title
                self.run_one()
                self.assertEqual(self.g.data[1]["title"], "feat(automation): ✨ 합성 시험 작업")

    def test_manual_conventional_title_without_checkpoint_is_preserved(self):
        self.run_one()
        key = REPOSITORY + ":" + self.n.page["id"]
        saved = self.state.get(key)
        saved.pop("issue_title")
        self.state.save(key, saved)
        human_title = "feat(infra): ✨ 직접 수정한 작업명"
        self.g.data[1]["title"] = human_title
        self.n.text += "\n- 새 본문"
        self.run_one()
        self.assertEqual(self.g.data[1]["title"], human_title)

    def test_configuration_is_fixed_to_approved_scope(self):
        validate_config(CFG)
        for key, value_to_reject in (
                ("repository", "someone/another-repo"),
                ("target", "테스트"),
                ("notion_data_source_id", "00000000-0000-0000-0000-000000000000"),
                ("base_branch", "main"),
                ("release_branch", "dev")):
            config = copy.deepcopy(CFG)
            config[key] = value_to_reject
            with self.assertRaisesRegex(SyncError, "CONFIG_OUTSIDE_APPROVED_SCOPE"):
                validate_config(config)

    def test_notion_data_source_schema_is_verified_without_writing(self):
        config = copy.deepcopy(CFG)
        properties = {}
        for name, kind in EXPECTED_SCHEMA.items():
            definition = {"type": kind}
            if kind == "select":
                definition[kind] = {"options": []}
            elif kind == "multi_select":
                definition[kind] = {"options": []}
            properties[name] = definition
        options = {
            "대상 저장소": [TARGET],
            "유형": ["✨ feat", "🐛 fix", "🚑 hotfix", "♻️ refactor", "📝 docs", "🎨 style",
                    "⚡ perf", "✅ test", "📦 build", "👷 ci", "🔧 chore"],
            "우선순위": ["P0 긴급", "P1 이번 목표 필수", "P2 일반", "P3 여유 있을 때"],
            "상태": ["백로그", "개발 준비", "개발 중", "리뷰 중", "취소", "완료"],
            "GitHub 동기화": ["생성 중", "완료", "오류"],
            "Milestone": ["M0 협업 기반 정리", "M1 중간고사", "M2 기말고사", "M3 평가·발표·고도화"],
            "영역": config["areas"],
        }
        for name, names in options.items():
            kind = EXPECTED_SCHEMA[name]
            properties[name][kind]["options"] = [{"name": item} for item in names]
        source = {"object": "data_source", "id": DATA_SOURCE_ID, "properties": properties}
        notion = Notion("synthetic-test-token", config)
        with patch.object(notion.api, "request", return_value=source) as request:
            self.assertTrue(notion.verify_data_source())
        request.assert_called_once_with("GET", "/data_sources/" + DATA_SOURCE_ID)
        self.assertEqual(notion.milestones, set(options["Milestone"]))

        # Renaming, adding, and clearing user milestones must not block the board.
        for titles in (["사용자 일정", "추가 일정"], []):
            properties["Milestone"]["select"]["options"] = [{"name": name} for name in titles]
            with patch.object(notion.api, "request", return_value=source):
                self.assertTrue(notion.verify_data_source())
            self.assertEqual(notion.milestones, set(titles))

        properties["상태"]["select"]["options"] = [{"name": "사용자 상태"}]
        with patch.object(notion.api, "request", return_value=source):
            with self.assertRaisesRegex(SyncError, "NOTION_SCHEMA_OPTIONS_INVALID"):
                notion.verify_data_source()
        self.assertEqual(notion.milestones, set())
        properties["상태"]["select"]["options"] = [{"name": name} for name in options["상태"]]

        properties["Branch"]["type"] = "url"
        with patch.object(notion.api, "request", return_value=source):
            with self.assertRaisesRegex(SyncError, "NOTION_SCHEMA_INVALID"):
                notion.verify_data_source()

    def test_github_access_check_uses_config_from_real_constructor(self):
        client = GitHub("synthetic-test-token", copy.deepcopy(CFG))
        with patch.object(client.api, "request", side_effect=[
                {"full_name": REPOSITORY},
                {"ref": "refs/heads/dev"},
                {"ref": "refs/heads/main"}]) as request:
            self.assertEqual(client.verify_access(), REPOSITORY)
        self.assertEqual(
            [call.args[:2] for call in request.call_args_list],
            [("GET", "/repos/" + REPOSITORY),
             ("GET", "/repos/" + REPOSITORY + "/git/ref/heads/dev"),
             ("GET", "/repos/" + REPOSITORY + "/git/ref/heads/main")],
        )

    def test_read_only_access_check_reports_counts_without_mutating(self):
        notion, github = FakeNotion(), FakeGitHub()
        before = notion.edits
        report = check_access(CFG, notion, github)
        self.assertEqual(report["repository"], REPOSITORY)
        self.assertEqual(report["base_branch"], "dev")
        self.assertEqual(report["release_branch"], "main")
        self.assertEqual(report["schema"], "valid")
        self.assertEqual(report["row_count"], 1)
        self.assertEqual(report["request_count"], 1)
        self.assertEqual(notion.edits, before)
        self.assertFalse(github.data)
        self.assertFalse(github.branches)
        self.assertFalse(github.pulls)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.n, self.g = FakeNotion(), FakeGitHub()
        self.state = State(Path(self.tmp.name) / "state.db")
        self.engine = Engine(copy.deepcopy(CFG), self.n, self.g, self.state)

    def tearDown(self):
        self.state.db.close()
        self.tmp.cleanup()

    def run_one(self):
        return self.engine.run()[0]

    def pr(self, merged=None, state="open", base="dev", draft=False, repo=REPOSITORY):
        branch = value(self.n.page, "Branch")
        self.g.pulls = [{"number": 2, "state": state, "draft": draft, "merged_at": merged,
            "head": {"ref": branch, "sha": "head1", "repo": {"full_name": repo}},
            "base": {"ref": base}, "html_url": f"https://github.com/{REPOSITORY}/pull/2"}]

    def test_create_and_five_repeated_requests(self):
        for _ in range(5):
            self.n.set(CREATE, "checkbox", True)
            self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(len(self.g.data), 1)
        self.assertEqual(len(self.g.branches), 1)
        self.assertEqual(value(self.n.page, "상태"), "개발 준비")

    def test_no_draft_before_file_changes(self):
        self.run_one()
        self.assertFalse(self.g.pulls)

    def test_auto_draft_assigns_notion_owner_and_reflects_status(self):
        self.n.page["properties"]["담당자"] = {"type": "people", "people": [
            {"id": next(iter(CFG["assignees"]))}]}
        self.run_one()
        self.g.changed = True
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(value(self.n.page, "상태"), "개발 중")
        self.assertTrue(self.g.pulls[0]["draft"])
        self.assertEqual(self.g.pulls[0]["base"]["ref"], "dev")
        self.assertIn("Refs #1", self.g.pulls[0]["body"])
        self.assertEqual(self.g.assignments, [(2, ["kohs2k21"])])

    def test_auto_draft_preserves_human_title_body_and_ready_state(self):
        self.run_one()
        self.g.changed = True
        self.run_one()
        self.g.pulls[0].update(title="Human title", body="Human body", draft=False)
        for _ in range(3):
            self.run_one()
        self.assertEqual(len(self.g.pulls), 1)
        self.assertEqual(self.g.pulls[0]["title"], "Human title")
        self.assertEqual(self.g.pulls[0]["body"], "Human body")
        self.assertFalse(self.g.pulls[0]["draft"])
        self.assertEqual(value(self.n.page, "상태"), "리뷰 중")

    def test_auto_draft_lost_response_recovers_one_pr(self):
        self.run_one()
        self.g.changed = self.g.lost_draft_response = True
        self.assertEqual(self.run_one()["result"], "error")
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(len(self.g.pulls), 1)
        self.assertEqual(len(self.g.assignments), 1)

    def test_auto_draft_assignment_failure_recovers(self):
        self.run_one()
        self.g.changed = self.g.fail_assignment = True
        self.assertEqual(self.run_one()["result"], "error")
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(len(self.g.pulls), 1)
        self.assertEqual(len(self.g.assignments), 1)

    def test_auto_draft_no_recreate_closed_unmerged_or_wrong_base(self):
        self.run_one()
        self.g.changed = True
        for state, base in [("closed", "dev"), ("open", "main"), ("closed", "main")]:
            self.pr(state=state, base=base)
            self.run_one()
            self.assertEqual(len(self.g.pulls), 1)

    def test_auto_draft_excludes_pre_rollout_issue(self):
        self.engine.cfg["auto_draft_pr"] = False
        self.run_one()
        self.engine.cfg["auto_draft_pr"] = self.g.changed = True
        self.run_one()
        self.assertFalse(self.g.pulls)

    def test_auto_draft_cancelled_or_closed_issue_is_not_created(self):
        self.run_one()
        self.g.changed = True
        self.n.set("상태", "select", "취소")
        self.run_one()
        self.assertFalse(self.g.pulls)
        self.n.set("상태", "select", "개발 중")
        self.g.data[1]["state"] = "closed"
        self.run_one()
        self.assertFalse(self.g.pulls)

    def test_auto_draft_hotfix_uses_dev_without_priority(self):
        self.n.set("유형", "select", "🚑 hotfix")
        self.n.set("우선순위", "select", None)
        self.run_one()
        self.g.changed = True
        self.run_one()
        self.assertEqual(self.g.pulls[0]["base"]["ref"], "dev")

    def test_compare_requires_actual_diff_not_just_empty_commit(self):
        api = GitHub("synthetic-test-token", CFG)
        for response, expected in [({"ahead_by": 0, "files": []}, False),
                ({"ahead_by": 1, "files": []}, False),
                ({"ahead_by": 1, "files": [{"filename": "example.md"}]}, True)]:
            with patch.object(api, "call", return_value=response):
                self.assertEqual(api.has_changes("feat/1-test", "dev"), expected)

    def test_uncertain_create_response_reconciles_without_duplicate(self):
        self.g.lost_response = True
        self.assertEqual(self.run_one()["result"], "error")
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(len(self.g.data), 1)

    def test_branch_failure_resumes_existing_issue(self):
        self.g.fail_branch = True
        self.run_one()
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(len(self.g.data), 1)

    def test_restart_without_local_state_recovers(self):
        self.run_one()
        self.state.db.execute("DELETE FROM mappings")
        self.state.db.commit()
        self.run_one()
        self.assertEqual(len(self.g.data), 1)

    def test_unrelated_malformed_marker_does_not_break_create_or_recovery(self):
        self.g.data[1] = {"number": 1, "body": "<!-- notion-sync {not-json} -->",
                          "state": "open", "html_url": f"https://github.com/{REPOSITORY}/issues/1",
                          "labels": []}
        self.g.lost_response = True
        first = self.run_one()
        self.assertEqual(first["result"], "error")
        self.assertEqual(first["code"], "NETWORK_UNCERTAIN_RETRY_NEXT_RUN")
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(len(self.g.data), 2)
        self.assertEqual(value(self.n.page, "Issue 번호"), 2)
        self.assertEqual(len(self.g.branches), 1)

    def test_malformed_marker_with_current_page_id_fails_closed_during_scan(self):
        page_id = self.n.page["id"]
        self.g.data[1] = {"number": 1,
                          "body": f'<!-- notion-sync {{"page_id": "{page_id}", invalid}} -->',
                          "state": "open", "html_url": f"https://github.com/{REPOSITORY}/issues/1",
                          "labels": []}
        result = self.run_one()
        self.assertEqual(result["code"], "INVALID_ISSUE_MARKER")
        self.assertEqual(len(self.g.data), 1)
        self.assertEqual(len(self.g.branches), 0)

    def test_linked_malformed_marker_fails_closed(self):
        self.n.set(CREATE, "checkbox", False)
        self.n.set("Issue 번호", "number", 1)
        self.n.set("GitHub Issue", "url", f"https://github.com/{REPOSITORY}/issues/1")
        self.g.data[1] = {"number": 1, "body": "<!-- notion-sync {not-json} -->",
                          "state": "open", "html_url": f"https://github.com/{REPOSITORY}/issues/1",
                          "labels": []}
        result = self.run_one()
        self.assertEqual(result["code"], "INVALID_ISSUE_MARKER")
        self.assertEqual(len(self.g.data), 1)
        self.assertEqual(len(self.g.branches), 0)

    def test_title_change_preserves_branch_and_body_sync(self):
        self.run_one()
        before = value(self.n.page, "Branch")
        self.n.set("작업명", "rich_text", "제목 수정")
        self.n.set("브랜치 요약", "rich_text", "different-slug")
        self.n.text = "- [x] 본문 수정"
        self.run_one()
        self.assertEqual(value(self.n.page, "Branch"), before)
        self.assertIn("[x]", self.g.data[1]["body"])

    def test_foreign_target_refused(self):
        self.n.set("대상 저장소", "select", "다른 프로젝트")
        self.assertEqual(self.run_one()["code"], "FOREIGN_TARGET")
        self.assertFalse(self.g.data)

    def test_foreign_page_parent_refused(self):
        self.n.page["parent"]["data_source_id"] = "another"
        self.assertEqual(self.run_one()["code"], "FOREIGN_DATA_SOURCE")

    def test_foreign_issue_refused(self):
        self.n.set("GitHub Issue", "url", "https://github.com/other/repo/issues/1")
        self.assertEqual(self.run_one()["code"], "FOREIGN_ISSUE_URL")

    def test_unmarked_manual_issue_is_not_imported(self):
        self.n.set(CREATE, "checkbox", False)
        self.n.set("Issue 번호", "number", 1)
        self.n.set("GitHub Issue", "url", f"https://github.com/{REPOSITORY}/issues/1")
        self.g.data[1] = {"number": 1, "body": "manual issue", "state": "open",
                          "html_url": f"https://github.com/{REPOSITORY}/issues/1",
                          "labels": []}
        self.assertEqual(self.run_one()["code"], "ISSUE_OWNERSHIP_MISMATCH")
        self.assertEqual(self.g.data[1]["body"], "manual issue")
        self.assertFalse(self.g.branches)

    def test_unrequested_page_does_nothing(self):
        self.n.set(CREATE, "checkbox", False)
        self.assertEqual(self.run_one()["result"], "skipped")
        self.assertFalse(self.g.data)

    def test_unsafe_slug_refused(self):
        self.n.set("브랜치 요약", "rich_text", "../main;bad")
        self.assertEqual(self.run_one()["code"], "ENGLISH_BRANCH_SLUG_REQUIRED")

    def test_people_require_explicit_mapping(self):
        self.n.page["properties"]["담당자"] = {"type": "people", "people": [{"id": "missing"}]}
        self.assertEqual(self.run_one()["code"], "ASSIGNEE_MAPPING_REQUIRED")

    def test_draft_and_open_pr_status(self):
        self.run_one()
        self.pr(draft=True)
        self.run_one()
        self.assertEqual(value(self.n.page, "상태"), "개발 중")
        self.pr()
        self.run_one()
        self.assertEqual(value(self.n.page, "상태"), "리뷰 중")

    def test_closed_unmerged_not_done_or_deleted(self):
        self.run_one()
        self.pr(state="closed")
        self.run_one()
        self.assertEqual(value(self.n.page, "상태"), "개발 중")
        self.assertEqual(self.g.data[1]["state"], "open")
        self.assertFalse(self.g.deleted)

    def test_wrong_base_merged_not_done(self):
        self.run_one()
        self.pr(merged="2026-09-10T12:00:00Z", state="closed", base="main")
        self.run_one()
        self.assertNotEqual(value(self.n.page, "상태"), "완료")
        self.assertFalse(self.g.deleted)

    def test_fork_pr_not_owned(self):
        self.run_one()
        self.pr(merged="2026-09-10T12:00:00Z", state="closed", repo="other/fork")
        self.run_one()
        self.assertFalse(self.g.deleted)

    def test_merge_closes_and_does_not_recreate_branch(self):
        self.run_one()
        self.pr(merged="2026-09-10T12:00:00Z", state="closed")
        self.run_one()
        self.run_one()
        self.assertEqual(value(self.n.page, "상태"), "완료")
        self.assertEqual(self.g.data[1]["state"], "closed")
        self.assertFalse(self.g.branches)

    def test_new_branch_commits_are_not_deleted(self):
        self.run_one()
        self.pr(merged="2026-09-10T12:00:00Z", state="closed")
        self.g.branches[value(self.n.page, "Branch")] = "new-unmerged-work"
        self.assertEqual(self.run_one()["code"], "BRANCH_CHANGED_AFTER_MERGE_NOT_DELETED")
        self.assertTrue(self.g.branches)

    def test_unmanaged_labels_preserved(self):
        self.run_one()
        self.g.data[1]["labels"].append({"name": "review-needed"})
        self.n.text += " changed"
        self.run_one()
        self.assertIn({"name": "review-needed"}, self.g.data[1]["labels"])

    def test_issue_marker_is_only_recognized_on_first_line(self):
        self.assertIsNone(metadata("preface\n" + marker({"page_id": self.n.page["id"]})))

    def test_issue_marker_tampering_refused(self):
        self.run_one()
        self.g.data[1]["body"] = "marker removed"
        self.assertEqual(self.run_one()["code"], "ISSUE_OWNERSHIP_MISMATCH")

    def test_type_change_requires_new_task(self):
        self.run_one()
        self.n.set("유형", "select", "🐛 fix")
        self.assertEqual(self.run_one()["code"], "TYPE_LOCKED_AFTER_CREATION")

    def test_hotfix_allows_non_p0_and_uses_dev_without_followup(self):
        self.n.set("유형", "select", "🚑 hotfix")
        self.n.set("우선순위", "select", "P3 여유 있을 때")
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(metadata(self.g.data[1]["body"])["base"], "dev")
        self.pr(merged="2026-09-10T12:00:00Z", state="closed", base="dev")
        self.run_one()
        self.assertEqual(self.g.followups, 0)

    def test_missing_priority_does_not_block_hotfix_creation(self):
        self.n.set("유형", "select", "🚑 hotfix")
        self.n.set("우선순위", "select", None)
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertFalse(any(x["name"].startswith("priority:") for x in self.g.data[1]["labels"]))

    def test_priority_rename_and_clear_preserve_other_labels(self):
        self.n.set("우선순위", "select", "P2 일반")
        self.run_one()
        self.assertIn({"name": "priority:p2"}, self.g.data[1]["labels"])
        self.g.data[1]["labels"].append({"name": "review-needed"})
        self.n.set("우선순위", "select", None)
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertNotIn({"name": "priority:p2"}, self.g.data[1]["labels"])
        self.assertIn({"name": "review-needed"}, self.g.data[1]["labels"])

    def test_select_milestone_is_linked_and_can_be_cleared(self):
        self.n.set("Milestone", "select", "M1 중간고사")
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(self.g.data[1]["milestone"], 1)
        self.assertEqual(self.g.milestone_titles, ["M1 중간고사"])
        self.n.set("Milestone", "select", None)
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertIsNone(self.g.data[1]["milestone"])

    def test_user_milestone_rename_updates_same_issue(self):
        self.n.set("Milestone", "select", "M1 중간고사")
        self.assertEqual(self.run_one()["result"], "synced")
        self.n.milestones = {"사용자 새 일정"}
        self.n.set("Milestone", "select", "사용자 새 일정")
        self.assertEqual(self.run_one()["result"], "synced")
        self.assertEqual(self.g.milestone_titles, ["M1 중간고사", "사용자 새 일정"])
        self.assertEqual(len(self.g.data), 1)

    def test_milestone_outside_current_notion_options_is_rejected_before_github_write(self):
        self.n.set("Milestone", "select", "M1 API E2E")
        self.assertEqual(self.run_one()["code"], "MILESTONE_NOT_IN_NOTION_OPTIONS")
        self.assertEqual(self.g.data, {})
        self.assertEqual(self.g.milestone_titles, [])

    def test_secret_in_milestone_is_rejected_without_echo(self):
        title = "ghp_" + "A" * 32
        self.n.milestones = {title}
        self.n.set("Milestone", "select", title)
        result = self.run_one()
        self.assertEqual(result["code"], "POSSIBLE_SECRET_IN_TASK")
        self.assertNotIn(title, json.dumps(result))
        self.assertEqual(self.g.data, {})

    def test_github_milestone_reuses_exact_title_or_creates_once(self):
        client = GitHub("synthetic-test-token", copy.deepcopy(CFG))
        with patch.object(client, "all", return_value=[{"title": "사용자 일정", "number": 8}]), \
                patch.object(client, "call") as call:
            self.assertEqual(client.milestone("사용자 일정"), 8)
            call.assert_not_called()
        with patch.object(client, "all", return_value=[{"title": "이전 일정", "number": 8}]), \
                patch.object(client, "call", return_value={"number": 9}) as call:
            self.assertEqual(client.milestone("새 일정"), 9)
            call.assert_called_once_with("POST", "/milestones", {"title": "새 일정"})

    def test_secret_is_rejected_without_value_in_error(self):
        self.n.text = "ntn_" + "A" * 32
        result = self.run_one()
        self.assertEqual(result["code"], "POSSIBLE_SECRET_IN_TASK")
        self.assertFalse("A" * 32 in json.dumps(result))
        self.assertEqual(len(self.g.data), 0)

    def test_bearer_header_is_still_blocked_without_echo(self):
        self.n.text = "Authorization: Bearer synthetic-value"
        result = self.run_one()
        self.assertEqual(result["code"], "POSSIBLE_SECRET_IN_TASK")
        self.assertFalse("synthetic-value" in json.dumps(result))
        self.assertEqual(len(self.g.data), 0)

    def test_all_github_token_prefixes_are_blocked_without_echo(self):
        synthetic_tokens = (
            "ghp_" + "A" * 32,
            "github_pat_" + "B" * 32,
            "gho_" + "C" * 32,
            "ghu_" + "D" * 32,
            "ghs_" + "E" * 32,
            "ghr_" + "F" * 32,
            "ghs_APPID_JWT",
            "ghs_",
        )
        for token in synthetic_tokens:
            self.n.text = token
            result = self.run_one()
            self.assertEqual(result["code"], "POSSIBLE_SECRET_IN_TASK")
            self.assertFalse(token in json.dumps(result))
            self.assertEqual(len(self.g.data), 0)

    def test_noop_does_not_update_notion_forever(self):
        self.run_one()
        before = self.n.edits
        self.run_one()
        self.assertEqual(self.n.edits, before)

    def test_unsupported_attachment_refuses_partial_body(self):
        with self.assertRaisesRegex(SyncError, "UNSUPPORTED_BLOCK_image"):
            render_blocks([{"type": "image", "image": {}}], lambda _: [])

    def test_todo_and_nested_rendering(self):
        block = {"id": "a", "type": "to_do", "to_do": {"checked": True,
            "rich_text": [{"type": "text", "text": {"content": "Done"}}]}, "has_children": True}
        result = render_blocks([block], lambda _: [{"type": "paragraph", "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": "Nested"}}]}}])
        self.assertIn("- [x] Done", result)
        self.assertIn("    Nested", result)

    def test_git_delete_uses_atomic_lease_without_token_in_arguments(self):
        api = GitHub("synthetic-test-token", CFG)
        with patch.object(api, "call", return_value={"object": {"sha": "old-head"}}), patch("automation.services.sync.app.subprocess.run") as run:
            run.return_value.returncode = 0
            api.delete_branch("feat/7-lease-test", "old-head")
            args = run.call_args.args[0]
            self.assertIn("--force-with-lease=refs/heads/feat/7-lease-test:old-head", args)
            self.assertNotIn("synthetic-test-token", " ".join(args))
            self.assertIn("GIT_CONFIG_VALUE_0", run.call_args.kwargs["env"])

    def test_lease_failure_is_not_silently_completed(self):
        api = GitHub("synthetic-test-token", CFG)
        with patch.object(api, "call", return_value={"object": {"sha": "old-head"}}), patch("automation.services.sync.app.subprocess.run") as run:
            run.return_value.returncode = 1
            with self.assertRaisesRegex(SyncError, "REF_DELETE_LEASE_FAILED"):
                api.delete_branch("feat/7-lease-test", "old-head")


if __name__ == "__main__":
    unittest.main()

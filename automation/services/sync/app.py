"""Single-writer Notion -> GitHub sync worker. Python standard library only."""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "automation" / "config" / "project.json"
REPOSITORY = "kohs2k21/2026-2-CECD2-1-POWERCODE-03"
TARGET = "실제 프로젝트"
DATA_SOURCE_ID = "4c329ace-6af6-4dc9-869b-6b4d86df31cf"
BASE_BRANCH = "dev"
RELEASE_BRANCH = "main"
EXPECTED_ASSIGNEES = {
    "f6f3bf35-18e1-4e00-b28b-0345b146c75d": "kohs2k21",
    "d798e1d8-b088-467a-8fff-726df0ba9011": "juhno1023",
    "ac8cce9d-4260-4fb9-bd8d-27b5cc9df9ef": "KRMayD",
}
EXPECTED_AREAS = {"Frontend", "Backend", "Collector", "Detector", "Data", "Lab", "Infra", "Automation", "Docs"}
TYPES = set("feat fix hotfix refactor docs style perf test build ci chore".split())
TYPE_EMOJIS = dict(zip("feat fix hotfix refactor docs style perf test build ci chore".split(),
                       "✨ 🐛 🚑 ♻️ 📝 🎨 ⚡ ✅ 📦 👷 🔧".split()))
CREATE = "🚀 이슈 생성 요청"
UPDATE = "🔄 본문 동기화 요청"
TYPE_OPTIONS = {
    "✨ feat", "🐛 fix", "🚑 hotfix", "♻️ refactor", "📝 docs", "🎨 style",
    "⚡ perf", "✅ test", "📦 build", "👷 ci", "🔧 chore",
}
PRIORITY_CODES = {"P0", "P1", "P2", "P3"}
STATUS_OPTIONS = {"백로그", "개발 준비", "개발 중", "리뷰 중", "취소", "완료"}
SYNC_STATUS_OPTIONS = {"생성 중", "완료", "오류"}
EXPECTED_SCHEMA = {
    "작업명": "title", "유형": "select", "우선순위": "select", "영역": "multi_select",
    "대상 저장소": "select", "브랜치 요약": "rich_text", "상태": "select",
    CREATE: "checkbox", UPDATE: "checkbox", "담당자": "people", "Milestone": "select",
    "일정": "date", "GitHub Issue": "url", "Issue 번호": "number", "Branch": "rich_text",
    "GitHub 동기화": "select", "오류 메시지": "rich_text", "Pull Request": "url",
    "완료일": "date", "마지막 동기화": "date",
}


def validate_config(config):
    """Fail closed if a checked-in config widens the approved target."""
    if (not isinstance(config, dict)
            or config.get("repository") != REPOSITORY
            or config.get("target") != TARGET
            or config.get("notion_data_source_id") != DATA_SOURCE_ID
            or config.get("base_branch") != BASE_BRANCH
            or config.get("release_branch") != RELEASE_BRANCH
            or config.get("execution") != "actions"
            or config.get("auto_draft_pr") is not True
            or config.get("assignees") != EXPECTED_ASSIGNEES
            or not isinstance(config.get("areas"), list)
            or set(config["areas"]) != EXPECTED_AREAS
            or not isinstance(config.get("poll_seconds"), int)
            or config["poll_seconds"] < 10):
        raise SyncError("CONFIG_OUTSIDE_APPROVED_SCOPE")


class SyncError(Exception):
    """Messages must be fixed, credential-free diagnostic codes."""


def load_env(path: Path) -> None:
    # Runtime-only loader. No printing values, lengths, or file content.
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
                os.environ.setdefault(key, value.strip().strip("\"'"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class API:
    def __init__(self, base, headers):
        self.base, self.headers = base, headers
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, method, path, payload=None, missing=False):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(self.base + path, data, self.headers, method=method)
        # Never blindly retry a non-idempotent create after an uncertain response.
        safe = method == "GET" or path.endswith("/query") or method == "PATCH"
        for attempt in range(3 if safe else 1):
            try:
                with self.opener.open(req, timeout=30) as response:
                    raw = response.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:
                if missing and exc.code == 404:
                    return None
                if safe and exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise SyncError(f"HTTP_{exc.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if safe and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise SyncError("NETWORK_UNCERTAIN_RETRY_NEXT_RUN") from None
        raise SyncError("REQUEST_FAILED")


def rich(items):
    parts = []
    for item in items:
        if item.get("type") != "text":
            raise SyncError("UNSUPPORTED_MENTION_USE_PLAIN_TEXT")
        text = item.get("text", {}).get("content", "")
        link = item.get("text", {}).get("link")
        if link:
            url = link.get("url", "")
            if not url.startswith("https://") or "\n" in url:
                raise SyncError("UNSUPPORTED_LINK")
            text = f"[{text}]({url})"
        ann = item.get("annotations", {})
        if ann.get("code"):
            text = "`" + text + "`"
        if ann.get("bold"):
            text = "**" + text + "**"
        if ann.get("italic"):
            text = "*" + text + "*"
        if ann.get("strikethrough"):
            text = "~~" + text + "~~"
        parts.append(text)
    return "".join(parts)


def render_blocks(blocks, children, depth=0):
    if depth > 8:
        raise SyncError("BODY_TOO_DEEP")
    output = []
    for block in blocks:
        kind = block["type"]
        data = block.get(kind, {})
        text = rich(data.get("rich_text", []))
        if kind == "paragraph":
            line = text
        elif kind in ("heading_1", "heading_2", "heading_3"):
            line = "#" * int(kind[-1]) + " " + text
        elif kind == "to_do":
            line = "- [" + ("x" if data.get("checked") else " ") + "] " + text
        elif kind == "bulleted_list_item":
            line = "- " + text
        elif kind == "numbered_list_item":
            line = "1. " + text
        elif kind == "quote":
            line = "> " + text.replace("\n", "\n> ")
        elif kind == "divider":
            line = "---"
        elif kind == "code":
            fence = "`" * max(3, max((len(x) for x in re.findall(r"`+", text)), default=0) + 1)
            lang = data.get("language", "text")
            line = f"{fence}{lang}\n{text}\n{fence}"
        else:
            # Refuse partial replacement, attachments and synced/child pages.
            raise SyncError("UNSUPPORTED_BLOCK_" + re.sub(r"[^A-Za-z0-9_]", "", kind))
        output.append(line)
        if block.get("has_children"):
            nested = render_blocks(children(block["id"]), children, depth + 1)
            output.append("\n".join("    " + x for x in nested.splitlines()))
    return "\n\n".join(output)


def value(page, name, default=None):
    p = page["properties"].get(name, {})
    kind = p.get("type")
    v = p.get(kind)
    if kind in ("title", "rich_text"):
        return "".join(x.get("plain_text", x.get("text", {}).get("content", "")) for x in v)
    if kind in ("select", "status"):
        return v["name"] if v else default
    if kind == "multi_select":
        return [x["name"] for x in v]
    if kind == "people":
        return [x["id"] for x in v]
    return v if v is not None else default


def prop(kind, val):
    if kind == "rich_text":
        return {kind: [{"text": {"content": val}}] if val else []}
    if kind == "select":
        return {kind: {"name": val} if val else None}
    return {kind: val}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def plain_task_title(title):
    kinds = "|".join(sorted(TYPES))
    scopes = "|".join(sorted(area.lower() for area in EXPECTED_AREAS))
    emojis = "|".join(re.escape(emoji) for emoji in TYPE_EMOJIS.values())
    return re.sub(r"^(?:(?:\[(?:" + kinds + r")\]\s*)|(?:(?:" + kinds
                  + r")\((?:" + scopes + r")\):\s*(?:" + emojis + r")\s*))+", "", title)


class Notion:
    def __init__(self, token, config):
        self.config = config
        self.milestones = set()
        self.api = API("https://api.notion.com/v1", {
            "Authorization": "Bearer " + token, "Notion-Version": "2025-09-03",
            "Content-Type": "application/json"})

    def verify_data_source(self):
        self.milestones = set()
        source = self.api.request("GET", "/data_sources/" + self.config["notion_data_source_id"])
        if (not isinstance(source, dict)
                or source.get("object") != "data_source"
                or source.get("id", "").replace("-", "").lower()
                != self.config["notion_data_source_id"].replace("-", "").lower()):
            raise SyncError("NOTION_DATA_SOURCE_MISMATCH")
        properties = source.get("properties", {})
        for name, expected_type in EXPECTED_SCHEMA.items():
            definition = properties.get(name, {})
            if definition.get("type") != expected_type:
                raise SyncError("NOTION_SCHEMA_INVALID")
        def option_names(name, kind):
            options = properties[name].get(kind, {}).get("options", [])
            return {option.get("name") for option in options}

        if self.config["target"] not in option_names("대상 저장소", "select"):
            raise SyncError("NOTION_TARGET_OPTION_MISSING")
        required = {
            "유형": TYPE_OPTIONS,
            "상태": STATUS_OPTIONS,
            "GitHub 동기화": SYNC_STATUS_OPTIONS,
            "영역": set(self.config["areas"]),
        }
        for name, expected in required.items():
            if expected - option_names(name, EXPECTED_SCHEMA[name]):
                raise SyncError("NOTION_SCHEMA_OPTIONS_INVALID")
        priority_codes = {name.split()[0] for name in option_names("우선순위", "select")}
        if not PRIORITY_CODES.issubset(priority_codes):
            raise SyncError("NOTION_SCHEMA_OPTIONS_INVALID")
        # Milestone titles are user-managed metadata, refreshed each run.
        self.milestones = option_names("Milestone", "select")
        return True

    def pages(self):
        body = {"page_size": 100, "filter": {
            "property": "대상 저장소", "select": {"equals": self.config["target"]}}}
        for _ in range(1000):
            result = self.api.request("POST", "/data_sources/" + self.config["notion_data_source_id"] + "/query", body)
            yield from result["results"]
            if not result.get("has_more"):
                return
            cursor = result.get("next_cursor")
            if not cursor:
                raise SyncError("NOTION_PAGINATION_CURSOR_MISSING")
            body["start_cursor"] = cursor
        raise SyncError("PAGINATION_LIMIT")

    def get(self, page_id):
        return self.api.request("GET", "/pages/" + page_id)

    def patch(self, page_id, properties):
        return self.api.request("PATCH", "/pages/" + page_id, {"properties": properties})

    def children(self, block_id):
        path = "/blocks/" + block_id + "/children?page_size=100"
        blocks = []
        while True:
            result = self.api.request("GET", path)
            blocks.extend(result["results"])
            if not result.get("has_more"):
                return blocks
            path = "/blocks/" + block_id + "/children?page_size=100&start_cursor=" + result["next_cursor"]

    def body(self, page_id):
        return render_blocks(self.children(page_id), self.children)


class GitHub:
    def __init__(self, token, config):
        self._token = token
        self.config = config
        self.repo = config["repository"]
        self.owner = self.repo.split("/")[0]
        self.prefix = "/repos/" + self.repo
        self.api = API("https://api.github.com", {"Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "powercode-notion-sync", "Content-Type": "application/json"})

    def call(self, method, path, payload=None, missing=False):
        return self.api.request(method, self.prefix + path, payload, missing)

    def verify_access(self):
        repository = self.api.request("GET", self.prefix)
        if repository.get("full_name", "").casefold() != self.repo.casefold():
            raise SyncError("GITHUB_REPOSITORY_MISMATCH")
        for name in (self.config["base_branch"], self.config["release_branch"]):
            branch = urllib.parse.quote(name, safe="")
            ref = self.call("GET", "/git/ref/heads/" + branch)
            if ref.get("ref") != "refs/heads/" + name:
                raise SyncError("GITHUB_BASE_BRANCH_MISMATCH")
        return repository["full_name"]

    def all(self, path):
        result = []
        for page in range(1, 1001):
            separator = "&" if "?" in path else "?"
            chunk = self.call("GET", f"{path}{separator}per_page=100&page={page}")
            result.extend(chunk)
            if len(chunk) < 100:
                return result
        raise SyncError("PAGINATION_LIMIT")

    def issues(self):
        return [i for i in self.all("/issues?state=all") if "pull_request" not in i]

    def issue(self, number):
        return self.call("GET", f"/issues/{number}")

    def create_issue(self, payload):
        return self.call("POST", "/issues", payload)

    def edit_issue(self, number, payload):
        return self.call("PATCH", f"/issues/{number}", payload)

    def ensure_labels(self, labels):
        existing = {i["name"] for i in self.all("/labels")}
        for label in labels:
            if label not in existing:
                self.call("POST", "/labels", {"name": label, "color": "64748b"})

    def milestone(self, title):
        if not title:
            return None
        for m in self.all("/milestones?state=all"):
            if m["title"] == title:
                return m["number"]
        return self.call("POST", "/milestones", {"title": title})["number"]

    def ensure_branch(self, name, base):
        path = "/git/ref/heads/" + urllib.parse.quote(name, safe="/")
        if self.call("GET", path, missing=True):
            return
        source = self.call("GET", "/git/ref/heads/" + base)
        self.call("POST", "/git/refs", {"ref": "refs/heads/" + name, "sha": source["object"]["sha"]})

    def prs(self, branch):
        return self.all("/pulls?state=all&head=" + urllib.parse.quote(self.owner + ":" + branch, safe=""))

    def has_changes(self, branch, base):
        diff = self.call("GET", "/compare/" + urllib.parse.quote(base + "..." + branch, safe=""))
        return diff.get("ahead_by", 0) > 0 and bool(diff.get("files"))

    def create_draft(self, branch, base, title, body, assignees):
        pr = self.call("POST", "/pulls", {"head": branch, "base": base,
            "title": title, "body": body, "draft": True})
        return pr

    def assign_pr(self, number, assignees):
        if assignees:
            self.call("POST", f"/issues/{number}/assignees", {"assignees": assignees})

    def delete_branch(self, branch, merged_sha):
        path = "/git/ref/heads/" + urllib.parse.quote(branch, safe="/")
        current = self.call("GET", path, missing=True)
        if current is None:
            return
        if current["object"]["sha"] != merged_sha:
            raise SyncError("BRANCH_CHANGED_AFTER_MERGE_NOT_DELETED")
        # Atomic expected-SHA deletion: a concurrent push after GET must not be lost.
        if not re.fullmatch(r"(?:feat|fix|hotfix|refactor|docs|style|perf|test|build|ci|chore)/[1-9][0-9]*-[a-z0-9-]+", branch):
            raise SyncError("REF_DELETE_NOT_ALLOWED")
        env = os.environ.copy()
        env.pop("NOTION_TOKEN", None)
        env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": "Authorization: Basic " + base64.b64encode(("x-access-token:" + self._token).encode()).decode()})
        result = subprocess.run(["git", "push", "--porcelain",
            f"--force-with-lease=refs/heads/{branch}:{merged_sha}",
            "https://github.com/" + self.repo + ".git", ":refs/heads/" + branch],
            cwd=ROOT, env=env, capture_output=True, timeout=60)
        if result.returncode:
            raise SyncError("REF_DELETE_LEASE_FAILED_REVIEW_BRANCH")

class State:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS mappings (key TEXT PRIMARY KEY, data TEXT NOT NULL)")

    def get(self, key):
        row = self.db.execute("SELECT data FROM mappings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, key, data):
        self.db.execute("INSERT INTO mappings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data", (key, json.dumps(data)))
        self.db.commit()


def marker(meta):
    return "<!-- notion-sync " + json.dumps(meta, sort_keys=True) + " -->"


def marker_payload(body):
    found = re.search(r"^<!--\s*notion-sync\b([^\r\n]*)", body or "")
    return found[1] if found else None


def metadata(body):
    raw = marker_payload(body)
    if raw is None:
        return None
    raw = raw.rstrip()
    if not raw.endswith("-->"):
        raise SyncError("INVALID_ISSUE_MARKER")
    try:
        parsed = json.loads(raw[:-3].strip())
    except ValueError:
        raise SyncError("INVALID_ISSUE_MARKER") from None
    if not isinstance(parsed, dict):
        raise SyncError("INVALID_ISSUE_MARKER")
    return parsed


def marker_may_belong(body, page_id):
    raw = marker_payload(body)
    if raw is None:
        return False
    raw = raw.casefold()
    return page_id.casefold() in raw or page_id.replace("-", "").casefold() in raw


def assert_safe_text(text):
    # GitHub documents these prefixes; stateless ghs_ tokens have variable JWT lengths.
    pattern = (r"(?:ntn_|secret_|ghp_|github_pat_|gho_|ghu_|ghr_)[A-Za-z0-9_.-]{16,}"
               r"|ghs_[A-Za-z0-9_.-]*|-----BEGIN .*PRIVATE KEY-----"
               r"|Authorization\s*:\s*Bearer\s+\S+")
    if re.search(pattern, text, re.I):
        raise SyncError("POSSIBLE_SECRET_IN_TASK")


class Engine:
    def __init__(self, config, notion, github, state):
        validate_config(config)
        self.cfg, self.n, self.g, self.state = config, notion, github, state

    def validate(self, page):
        if value(page, "대상 저장소") != self.cfg["target"]:
            raise SyncError("FOREIGN_TARGET")
        parent = page.get("parent", {})
        if parent.get("data_source_id", "").replace("-", "") != self.cfg["notion_data_source_id"].replace("-", ""):
            raise SyncError("FOREIGN_DATA_SOURCE")
        title = value(page, "작업명", "").strip()
        kind = value(page, "유형", "").split()
        kind = kind[-1] if kind else ""
        if not title or len(title) > 200 or kind not in TYPES:
            raise SyncError("TITLE_OR_TYPE_REQUIRED")
        # Priority is optional display metadata; retain stable GitHub label codes.
        raw_priority = value(page, "우선순위", "")
        priority = raw_priority.split()[0] if raw_priority else ""
        if priority not in ("P0", "P1", "P2", "P3"):
            priority = ""
        areas = value(page, "영역", [])
        if not areas or set(areas) - set(self.cfg["areas"]):
            raise SyncError("AREA_INVALID")
        milestone = value(page, "Milestone", "")
        if milestone and milestone not in self.n.milestones:
            raise SyncError("MILESTONE_NOT_IN_NOTION_OPTIONS")
        assert_safe_text(milestone)
        people = value(page, "담당자", [])
        if any(p not in self.cfg["assignees"] for p in people):
            raise SyncError("ASSIGNEE_MAPPING_REQUIRED")
        return title, kind, priority, areas, milestone, [self.cfg["assignees"][p] for p in people]

    def process(self, page):
        if value(page, "대상 저장소") != self.cfg["target"]:
            raise SyncError("FOREIGN_TARGET")
        lookup_key = self.cfg["repository"] + ":" + page["id"]
        if not (value(page, CREATE, False) or value(page, UPDATE, False) or value(page, "Issue 번호") or self.state.get(lookup_key)):
            return "skipped"
        title, kind, priority, areas, milestone, people = self.validate(page)
        page_id = page["id"]
        key = self.cfg["repository"] + ":" + page_id
        saved = self.state.get(key)
        existing_url = value(page, "GitHub Issue", "")
        if existing_url and not re.fullmatch(r"https://github\.com/" + re.escape(self.cfg["repository"]) + r"/issues/[1-9][0-9]*", existing_url):
            raise SyncError("FOREIGN_ISSUE_URL")
        number = value(page, "Issue 번호")
        if bool(number) != bool(existing_url):
            raise SyncError("INCOMPLETE_ISSUE_LINK")
        if number and (int(number) != number or not existing_url.endswith("/" + str(int(number)))):
            raise SyncError("ISSUE_LINK_MISMATCH")
        request_create = value(page, CREATE, False)
        if not saved and not number and not request_create:
            return "skipped"
        body_text = self.n.body(page_id)
        assert_safe_text(title + "\n" + body_text)
        if len(body_text) > 45000:
            raise SyncError("BODY_TOO_LARGE")
        issue = self.g.issue(saved["number"] if saved else int(number)) if (saved or number) else None
        if issue is None:
            # List REST results, not eventually indexed search. Reconcile lost create responses.
            matches = []
            for candidate in self.g.issues():
                try:
                    candidate_meta = metadata(candidate.get("body"))
                except SyncError as exc:
                    if (str(exc) != "INVALID_ISSUE_MARKER"
                            or marker_may_belong(candidate.get("body"), page_id)):
                        raise
                    continue
                if candidate_meta and candidate_meta.get("page_id") == page_id:
                    matches.append(candidate)
            if len(matches) > 1:
                raise SyncError("DUPLICATE_MARKERS_REVIEW_REQUIRED")
            issue = matches[0] if matches else None
        existing_prs = None
        migrated_base = False
        recovered_base = False
        if issue:
            meta = metadata(issue.get("body"))
            if not meta or meta.get("page_id") != page_id or meta.get("repository") != self.cfg["repository"]:
                raise SyncError("ISSUE_OWNERSHIP_MISMATCH")
            if meta.get("kind") not in TYPES or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", meta.get("slug", "")):
                raise SyncError("INVALID_IMMUTABLE_BRANCH_METADATA")
            # Closed/merged main history keeps its target. Active tasks without
            # a PR move metadata to dev, preserving the branch and its commits.
            if meta.get("base") not in {self.cfg["base_branch"], self.cfg["release_branch"]}:
                raise SyncError("INVALID_BRANCH_BASE")
            if kind != meta["kind"]:
                raise SyncError("TYPE_LOCKED_AFTER_CREATION")
            legacy_branch = f"{meta['kind']}/{issue['number']}-{meta['slug']}"
            stale_base = (saved and saved["branch"] == legacy_branch
                          and saved["base"] == self.cfg["release_branch"]
                          and meta["base"] == self.cfg["base_branch"])
            if (meta["base"] == self.cfg["release_branch"] or stale_base) and issue.get("state") == "open":
                existing_prs = self.g.prs(legacy_branch)
                owned_prs = [p for p in existing_prs
                    if p.get("head", {}).get("repo", {}).get("full_name") == self.cfg["repository"]
                    and p["head"]["ref"] == legacy_branch]
                if any(p["state"] == "open" and p["base"]["ref"] != self.cfg["base_branch"]
                       for p in owned_prs):
                    raise SyncError("LEGACY_ACTIVE_MAIN_PR_REQUIRES_REVIEW")
                if not owned_prs or any(p["base"]["ref"] == self.cfg["base_branch"] for p in owned_prs):
                    if stale_base:
                        # The remote marker survived a lost/read-only cache save.
                        recovered_base = True
                    else:
                        meta = {**meta, "base": self.cfg["base_branch"]}
                        migrated_base = True
        else:
            slug = value(page, "브랜치 요약", "").strip()
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or len(slug) > 60:
                raise SyncError("ENGLISH_BRANCH_SLUG_REQUIRED")
            meta = {"page_id": page_id, "repository": self.cfg["repository"], "kind": kind,
                    "slug": slug, "base": self.cfg["base_branch"]}
            if self.cfg.get("auto_draft_pr"):
                meta["auto_draft"] = True
        date = value(page, "일정")
        period = ((date.get("start", "") + " ~ " + (date.get("end") or date.get("start", ""))) if date else "미정")
        desired_body = marker(meta) + f"\n\nNotion: https://www.notion.so/{page_id.replace('-', '')}\n\n일정: {period}\n\n" + body_text
        labels = ["type:" + kind] + ["area:" + a.lower() for a in areas]
        if priority:
            labels.append("priority:" + priority.lower())
        self.g.ensure_labels(labels)
        milestone_number = self.g.milestone(milestone)
        plain_title = plain_task_title(title)
        issue_title = f"{kind}({areas[0].lower()}): {TYPE_EMOJIS[kind]} {plain_title}"
        if saved and "issue_title" in saved:
            title_owned = issue is not None and issue.get("title") == saved["issue_title"]
        else:
            title_owned = issue is None or issue.get("title") in {title, issue_title, f"[{kind}] {plain_title}"}
        sync_title = title_owned or value(page, UPDATE, False)
        payload = {"title": issue_title, "body": desired_body, "labels": labels, "assignees": people, "milestone": milestone_number}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if not issue:
            self.n.patch(page_id, {"GitHub 동기화": prop("select", "생성 중")})
            page = self.n.get(page_id)
            issue = self.g.create_issue(payload)
        number = issue["number"]
        branch = f"{meta['kind']}/{number}-{meta['slug']}"
        saved = saved or {"number": number, "branch": branch, "base": meta["base"]}
        if ((migrated_base or recovered_base) and saved["branch"] == branch
                and saved["base"] == self.cfg["release_branch"]):
            saved["base"] = meta["base"]
        if saved["branch"] != branch or saved["base"] != meta["base"]:
            raise SyncError("LOCAL_MAPPING_MISMATCH")
        # Persist immediately, before branch creation / Notion back-write.
        self.state.save(key, saved)
        if migrated_base or saved.get("digest") != digest or value(page, UPDATE, False):
            # Preserve unrelated human-added labels; replace only owned namespaces.
            unmanaged = [l["name"] for l in issue.get("labels", []) if not l["name"].startswith(("type:", "area:", "priority:"))]
            edit_payload = {**payload, "labels": sorted(set(labels + unmanaged))}
            if not sync_title:
                # A manual GitHub title is not overwritten by unrelated changes.
                edit_payload.pop("title")
            self.g.edit_issue(number, edit_payload)
            if sync_title:
                saved["issue_title"] = issue_title
            saved["digest"] = digest
            self.state.save(key, saved)
        prs = existing_prs if existing_prs is not None else self.g.prs(branch)
        owned = [p for p in prs if p.get("head", {}).get("repo", {}).get("full_name") == self.cfg["repository"] and p["head"]["ref"] == branch]
        status = value(page, "상태", "백로그")
        # Opt in only newly linked tasks. Never recreate a closed PR, or create a
        # second PR around a wrong-base PR. Empty commits do not count as work.
        if (self.cfg.get("auto_draft_pr") and meta.get("auto_draft") is True
                and not owned and not saved.get("completed")
                and status not in ("취소", "완료") and issue.get("state") == "open"):
            self.g.ensure_branch(branch, meta["base"])
            if self.g.has_changes(branch, meta["base"]):
                seed_title = f"{issue_title} (#{number})"
                seed_body = (f"<!-- notion-draft {page_id} -->\n"
                    f"- 목적·작업 범위: {title}\n"
                    f"- 상세 범위: 연결 Issue 및 Notion 작업 본문 기준\n"
                    f"- 연결: Refs #{number}\n"
                    f"- Notion: https://www.notion.so/{page_id.replace('-', '')}\n"
                    "- 검증: 미실행, 담당자의 실제 검증 결과 작성 필요\n"
                    "- 남은 작업: 구현·검증 결과와 제약 보완 후 Ready for review 전환\n"
                    "- 봇 초안: 이후 제목·본문 자동 덮어쓰기 없음\n")
                created = self.g.create_draft(branch, meta["base"], seed_title, seed_body, people)
                owned.append(created)
        correct = [p for p in owned if p["base"]["ref"] == meta["base"]]
        merged = next((p for p in correct if p.get("merged_at")), None)
        opened = next((p for p in correct if p["state"] == "open"), None)
        # Recover a lost create/assignment response without replacing edited text.
        if (opened and meta.get("auto_draft") is True
                and (opened.get("body") or "").startswith(f"<!-- notion-draft {page_id} -->")
                and not saved.get("draft_assigned")):
            self.g.assign_pr(opened["number"], people)
            saved["draft_assigned"] = True
            self.state.save(key, saved)
        properties = {"Issue 번호": prop("number", number), "GitHub Issue": prop("url", issue["html_url"]),
            "Branch": prop("rich_text", branch), "GitHub 동기화": prop("select", "완료"),
            "오류 메시지": prop("rich_text", "")}
        if merged:
            # Only the exact merged head may be deleted. Never remove configured base branches or fresh commits.
            if issue.get("state") != "closed" or issue.get("state_reason") != "completed":
                self.g.edit_issue(number, {"state": "closed", "state_reason": "completed"})
            if not saved.get("completed"):
                self.g.delete_branch(branch, merged["head"]["sha"])
            properties.update({"상태": prop("select", "완료"), "Pull Request": prop("url", merged["html_url"]),
                "완료일": prop("date", {"start": merged["merged_at"]})})
            saved["completed"] = True
        elif opened:
            properties.update({"상태": prop("select", "개발 중" if opened.get("draft") else "리뷰 중"),
                "Pull Request": prop("url", opened["html_url"]), "완료일": prop("date", None)})
        elif correct:
            # Closed without merge is explicitly not done. Preserve cancellation.
            properties.update({"상태": prop("select", "취소" if status == "취소" else "개발 중"),
                "Pull Request": prop("url", correct[0]["html_url"]), "완료일": prop("date", None)})
        else:
            if not saved.get("completed"):
                self.g.ensure_branch(branch, meta["base"])
            if status in ("백로그", "리뷰 중", "완료") and not saved.get("completed"):
                properties["상태"] = prop("select", "개발 준비")
        # Don't clear a new request or overwrite metadata when page changed during network I/O.
        fresh = self.n.get(page_id)
        if fresh.get("last_edited_time") == page.get("last_edited_time"):
            properties[CREATE] = prop("checkbox", False)
            properties[UPDATE] = prop("checkbox", False)
        changed = {k: v for k, v in properties.items() if value(fresh, k) != value({"properties": {k: {"type": next(iter(v)), **v}}}, k)}
        if changed:
            changed["마지막 동기화"] = prop("date", {"start": now()})
            self.n.patch(page_id, changed)
        self.state.save(key, saved)
        return "completed" if merged else "synced"

    def run(self):
        self.n.verify_data_source()
        results = []
        for page in self.n.pages():
            try:
                outcome = self.process(page)
                results.append({"page_id": page["id"], "result": outcome})
            except Exception as exc:
                code = str(exc) if isinstance(exc, SyncError) else "INTERNAL_ERROR"
                try:
                    self.n.patch(page["id"], {"GitHub 동기화": prop("select", "오류"),
                        "오류 메시지": prop("rich_text", code)})
                except Exception:
                    pass
                results.append({"page_id": page["id"], "result": "error", "code": code})
        return results


@contextlib.contextmanager
def single_writer(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as f:
        f.seek(0)
        if not f.read(1):
            f.write(b"0")
            f.flush()
        f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SyncError("ANOTHER_LOCAL_WORKER_RUNNING") from None
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def check_access(config, notion, github):
    validate_config(config)
    repository = github.verify_access()
    notion.verify_data_source()
    rows = requests = 0
    for page in notion.pages():
        if value(page, "대상 저장소") != config["target"]:
            raise SyncError("FOREIGN_TARGET")
        rows += 1
        if value(page, CREATE, False) or value(page, UPDATE, False):
            requests += 1
    return {
        "repository": repository,
        "base_branch": config["base_branch"],
        "release_branch": config["release_branch"],
        "notion_data_source_id": config["notion_data_source_id"],
        "schema": "valid",
        "row_count": rows,
        "request_count": requests,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--check-access", action="store_true")
    args = parser.parse_args()
    load_env(ROOT / ".env")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    validate_config(cfg)
    if (cfg["execution"] == "actions" and os.environ.get("GITHUB_ACTIONS") != "true"
            and not args.check_access):
        raise SyncError("ACTIONS_OWNS_SYNC_STOP_LOCAL_WORKER")
    notion_token = os.environ.get("NOTION_TOKEN", "")
    github_token = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", "")
    if not github_token:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True)
        if result.returncode == 0:
            github_token = result.stdout.strip()
    if not notion_token or not github_token:
        raise SyncError("CREDENTIALS_NOT_CONFIGURED")
    notion, github = Notion(notion_token, cfg), GitHub(github_token, cfg)
    if args.check_access:
        print(json.dumps(check_access(cfg, notion, github), ensure_ascii=True))
        return 0
    with single_writer(ROOT / "automation/.runtime/worker.lock"):
        engine = Engine(cfg, notion, github, State(ROOT / "automation/.runtime/state.db"))
        while True:
            try:
                results = engine.run()
            except SyncError as exc:
                results = [{"result": "error", "code": str(exc)}]
            print(json.dumps({"at": now(), "results": results}, ensure_ascii=True), flush=True)
            if not args.watch:
                return 1 if any(r["result"] == "error" for r in results) else 0
            time.sleep(max(10, cfg["poll_seconds"]))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except SyncError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True))
        raise SystemExit(1)
    except Exception:
        print(json.dumps({"error": "UNEXPECTED_FAILURE_NO_RAW_OUTPUT"}, ensure_ascii=True))
        raise SystemExit(1)

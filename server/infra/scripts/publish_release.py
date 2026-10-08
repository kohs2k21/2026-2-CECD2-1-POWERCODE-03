#!/usr/bin/env python3
"""Publish one complete, digest-pinned deployment release from main Actions CI."""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


REPOSITORY = "kohs2k21/2026-2-CECD2-1-POWERCODE-03"
IMAGE_PREFIX = "ghcr.io/kohs2k21/powercode-"
COMMIT_RE = re.compile(r"[0-9a-f]{40}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
TAG_RE = re.compile(r"deploy-([1-9][0-9]*)-([0-9a-f]{12})")


class PublishError(Exception):
    """A release failed its publication contract."""


class ApiError(PublishError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"GitHub API returned HTTP {status}")


class InvalidAsset(PublishError):
    """An existing manifest asset cannot be decoded."""


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.hostname not in {
            "api.github.com", "uploads.github.com", "github.com",
            "release-assets.githubusercontent.com", "objects.githubusercontent.com",
        }:
            raise PublishError("Unexpected GitHub asset redirect")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urllib.parse.urlparse(req.full_url).netloc != urllib.parse.urlparse(newurl).netloc:
            redirected.remove_header("Authorization")
        return redirected


class GitHub:
    def __init__(self, token):
        if not token:
            raise PublishError("GITHUB_TOKEN is required")
        self.token = token
        self.opener = urllib.request.build_opener(SafeRedirect())

    def request(self, method, path, body=None, *, raw=False, upload=False):
        origin = "https://uploads.github.com" if upload else "https://api.github.com"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/octet-stream" if raw else "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "powercode-release-publisher",
        }
        if isinstance(body, bytes):
            headers["Content-Type"] = "application/json"
        elif body is not None:
            body = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(origin + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                data = response.read(16385 if raw else 2_000_000)
        except urllib.error.HTTPError as exc:
            # API response bodies and headers may contain sensitive information.
            raise ApiError(exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise PublishError("GitHub API request failed") from None
        if raw:
            if len(data) > 16384:
                raise InvalidAsset("Existing deploy.json is oversized")
            return data
        try:
            return json.loads(data) if data else None
        except (ValueError, UnicodeDecodeError):
            raise PublishError("GitHub API returned invalid JSON") from None


def context(environ):
    if (
        environ.get("GITHUB_ACTIONS") != "true"
        or environ.get("GITHUB_EVENT_NAME") != "push"
        or environ.get("GITHUB_REF") != "refs/heads/main"
        or environ.get("GITHUB_REPOSITORY") != REPOSITORY
        or environ.get("CD_ENABLED") != "true"
    ):
        raise PublishError("Publication is restricted to enabled main push Actions runs in the allowed repository")
    commit = environ.get("GITHUB_SHA", "")
    number = environ.get("GITHUB_RUN_NUMBER", "")
    if not COMMIT_RE.fullmatch(commit) or not re.fullmatch(r"[1-9][0-9]*", number):
        raise PublishError("Invalid Actions commit or release number")
    return commit, int(number)


def manifest(commit, number, web_digest, api_digest):
    if not COMMIT_RE.fullmatch(commit) or type(number) is not int or number < 1:
        raise PublishError("Invalid manifest commit or release number")
    if not all(isinstance(digest, str) and DIGEST_RE.fullmatch(digest) for digest in (web_digest, api_digest)):
        raise PublishError("Both successful image pushes must provide SHA-256 digests")
    return {
        "schema_version": 1,
        "compose_contract": 1,
        "repository": REPOSITORY,
        "release_number": number,
        "commit": commit,
        "images": {
            "web": IMAGE_PREFIX + "web@" + web_digest,
            "api": IMAGE_PREFIX + "api@" + api_digest,
        },
    }


def check_current(api, commit):
    head = api.request("GET", f"/repos/{REPOSITORY}/git/ref/heads/main")
    if head.get("object", {}).get("sha") != commit:
        raise PublishError("The tested commit is no longer the current main commit")


def check_tag(api, tag, commit):
    try:
        resolved = api.request("GET", f"/repos/{REPOSITORY}/commits/{tag}")
    except ApiError as exc:
        if exc.status == 404:
            return
        raise
    if resolved.get("sha") != commit:
        raise PublishError("Existing deployment tag points to a different commit")


def release_assets(api, release_id):
    assets = []
    page = 1
    while True:
        batch = api.request("GET", f"/repos/{REPOSITORY}/releases/{release_id}/assets?per_page=100&page={page}")
        assets.extend(batch)
        if len(batch) < 100:
            return assets
        page += 1


def check_monotonic(api, number, commit):
    page = 1
    while True:
        batch = api.request("GET", f"/repos/{REPOSITORY}/releases?per_page=100&page={page}")
        for release in batch:
            match = TAG_RE.fullmatch(release.get("tag_name", ""))
            if match and not release.get("draft"):
                if int(match[1]) > number:
                    raise PublishError("A newer deployment release is already published")
                if int(match[1]) == number and match[2] != commit[:12]:
                    raise PublishError("Deployment release number already belongs to another commit")
        if len(batch) < 100:
            return
        page += 1


def read_asset(api, asset):
    try:
        return json.loads(api.request("GET", f"/repos/{REPOSITORY}/releases/assets/{asset['id']}", raw=True))
    except (ValueError, UnicodeDecodeError):
        raise InvalidAsset("Existing deploy.json is invalid") from None


def publish(api, deployment):
    commit = deployment["commit"]
    number = deployment["release_number"]
    tag = f"deploy-{number}-{commit[:12]}"
    base = f"/repos/{REPOSITORY}"
    check_current(api, commit)
    check_monotonic(api, number, commit)
    check_tag(api, tag, commit)
    try:
        release = api.request("GET", base + "/releases/tags/" + tag)
    except ApiError as exc:
        if exc.status != 404:
            raise
        release = api.request("POST", base + "/releases", {
            "tag_name": tag,
            "target_commitish": commit,
            "name": f"Deployment {number}",
            "body": f"- CI 검증 main 커밋: `{commit}`\n- 배포 명세: `deploy.json`",
            "draft": True,
            "prerelease": False,
        })
    if release.get("target_commitish") != commit or release.get("prerelease"):
        raise PublishError("Existing release has a different commit or is a prerelease")
    assets = release_assets(api, release["id"])
    manifests = [asset for asset in assets if asset.get("name") == "deploy.json"]
    if len(manifests) > 1:
        raise PublishError("Release has duplicate deploy.json assets")
    if not release.get("draft"):
        if len(manifests) != 1 or manifests[0].get("state") != "uploaded" or read_asset(api, manifests[0]) != deployment:
            raise PublishError("Published release is incomplete or differs; refusing to overwrite")
        return tag
    matching_asset = False
    if manifests and manifests[0].get("state") == "uploaded":
        try:
            matching_asset = read_asset(api, manifests[0]) == deployment
        except InvalidAsset:
            pass
    if manifests and not matching_asset:
        # Only incomplete/different draft assets may be replaced after interrupted runs.
        api.request("DELETE", base + f"/releases/assets/{manifests[0]['id']}")
        manifests = []
    if not manifests:
        payload = (json.dumps(deployment, sort_keys=True, indent=2) + "\n").encode("utf-8")
        asset = api.request("POST", base + f"/releases/{release['id']}/assets?name=deploy.json", payload, upload=True)
        if asset.get("name") != "deploy.json" or asset.get("state") != "uploaded":
            raise PublishError("deploy.json upload did not complete; leaving the release as a draft")
        if read_asset(api, asset) != deployment:
            raise PublishError("Uploaded deploy.json does not match; leaving the release as a draft")
    # This is the visibility boundary. Image pushes and complete asset verification
    # have succeeded before the release becomes available to the polling server.
    check_current(api, commit)
    check_monotonic(api, number, commit)
    check_tag(api, tag, commit)
    api.request("PATCH", base + f"/releases/{release['id']}", {"draft": False, "make_latest": "true"})
    return tag


def digest_from_metadata(path):
    try:
        digest = json.loads(Path(path).read_text(encoding="utf-8"))["containerimage.digest"]
    except (OSError, ValueError, KeyError, TypeError):
        raise PublishError("Missing or invalid successful image push metadata") from None
    if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
        raise PublishError("Invalid pushed image digest")
    return digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-current", action="store_true")
    parser.add_argument("--web-metadata")
    parser.add_argument("--api-metadata")
    args = parser.parse_args()
    try:
        commit, number = context(os.environ)
        api = GitHub(os.environ.get("GITHUB_TOKEN"))
        if args.check_current:
            check_current(api, commit)
            check_monotonic(api, number, commit)
            print("Current main commit and deployment sequence verified")
            return 0
        if not args.web_metadata or not args.api_metadata:
            raise PublishError("Both image push metadata files are required")
        deployment = manifest(commit, number, digest_from_metadata(args.web_metadata), digest_from_metadata(args.api_metadata))
        print(f"Published deployment release: {publish(api, deployment)}")
        return 0
    except PublishError as exc:
        print(f"Publication stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

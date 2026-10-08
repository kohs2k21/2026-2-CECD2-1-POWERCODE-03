"""Offline tests for the release visibility and replay boundaries."""

import importlib.util
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publish_release.py"
SPEC = importlib.util.spec_from_file_location("publish_release", SCRIPT)
publisher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publisher)
COMMIT = "a" * 40
DEPLOYMENT = publisher.manifest(COMMIT, 12, "sha256:" + "b" * 64, "sha256:" + "c" * 64)


class FakeGitHub:
    def __init__(self, release=None, assets=None):
        self.release = release
        self.assets = list(assets or [])
        self.releases = []
        self.main = COMMIT
        self.calls = []
        self.asset_content = {}
        self.fail_upload = False
        self.incomplete_upload = False
        self.move_main_after_upload = False
        self.corrupt_upload = False
        self.tag_commit = COMMIT

    def request(self, method, path, body=None, **kwargs):
        self.calls.append((method, path, body, kwargs))
        if path.endswith("/git/ref/heads/main"):
            return {"object": {"sha": self.main}}
        if "/commits/deploy-" in path:
            return {"sha": self.tag_commit}
        if "/releases?" in path:
            return self.releases
        if "/releases/tags/" in path:
            if self.release is None:
                raise publisher.ApiError(404)
            return dict(self.release)
        if method == "POST" and path.endswith("/releases"):
            self.release = dict(body, id=99)
            return dict(self.release)
        if method == "GET" and "/assets?" in path:
            return list(self.assets)
        if method == "GET" and "/releases/assets/" in path:
            return self.asset_content[int(path.rsplit("/", 1)[1])]
        if method == "DELETE":
            asset_id = int(path.rsplit("/", 1)[1])
            self.assets = [asset for asset in self.assets if asset["id"] != asset_id]
            return None
        if method == "POST" and "/assets?" in path:
            if self.fail_upload:
                raise publisher.ApiError(500)
            asset = {"id": 101, "name": "deploy.json", "state": "starter" if self.incomplete_upload else "uploaded"}
            self.assets.append(asset)
            self.asset_content[101] = b"{}" if self.corrupt_upload else body
            if self.move_main_after_upload:
                self.main = "d" * 40
            return asset
        if method == "PATCH":
            self.release.update(body)
            return dict(self.release)
        raise AssertionError(f"Unexpected API request: {method} {path}")


def existing_release(draft=True, commit=COMMIT):
    return {"id": 99, "tag_name": "deploy-12-" + COMMIT[:12], "target_commitish": commit, "draft": draft, "prerelease": False}


class PublisherTests(unittest.TestCase):
    def assert_not_published(self, api):
        self.assertFalse(any(call[0] == "PATCH" for call in api.calls))

    def test_manifest_exact_contract(self):
        self.assertEqual(set(DEPLOYMENT), {"schema_version", "compose_contract", "repository", "release_number", "commit", "images"})
        self.assertEqual(DEPLOYMENT["schema_version"], 1)
        self.assertEqual(DEPLOYMENT["compose_contract"], 1)
        self.assertEqual(DEPLOYMENT["repository"], publisher.REPOSITORY)
        self.assertEqual(DEPLOYMENT["images"]["web"], "ghcr.io/kohs2k21/powercode-web@sha256:" + "b" * 64)
        self.assertEqual(DEPLOYMENT["images"]["api"], "ghcr.io/kohs2k21/powercode-api@sha256:" + "c" * 64)

    def test_manifest_rejects_invalid_commit_number_and_digest(self):
        for commit, number, web, api in [
            ("main", 12, "sha256:" + "b" * 64, "sha256:" + "c" * 64),
            (COMMIT, 0, "sha256:" + "b" * 64, "sha256:" + "c" * 64),
            (COMMIT, True, "sha256:" + "b" * 64, "sha256:" + "c" * 64),
            (COMMIT, 12, "latest", "sha256:" + "c" * 64),
            (COMMIT, 12, "sha256:" + "b" * 64, None),
        ]:
            with self.subTest(commit=commit, number=number, web=web, api=api), self.assertRaises(publisher.PublishError):
                publisher.manifest(commit, number, web, api)

    def test_context_only_enabled_main_push_from_exact_repository(self):
        env = {"GITHUB_ACTIONS": "true", "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main", "GITHUB_REPOSITORY": publisher.REPOSITORY, "CD_ENABLED": "true", "GITHUB_SHA": COMMIT, "GITHUB_RUN_NUMBER": "12"}
        self.assertEqual(publisher.context(env), (COMMIT, 12))
        for key, value in {
            "GITHUB_ACTIONS": "false", "GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/heads/dev", "GITHUB_REPOSITORY": "CSID-DGU/2026-2-CECD2-1-POWERCODE-03", "CD_ENABLED": "false", "GITHUB_SHA": "main", "GITHUB_RUN_NUMBER": "0",
        }.items():
            with self.subTest(key=key), self.assertRaises(publisher.PublishError):
                publisher.context(dict(env, **{key: value}))

    def test_new_release_is_draft_until_asset_verified_then_published_last(self):
        api = FakeGitHub()
        self.assertEqual(publisher.publish(api, DEPLOYMENT), "deploy-12-" + COMMIT[:12])
        create = next(call for call in api.calls if call[0] == "POST" and call[1].endswith("/releases"))
        self.assertTrue(create[2]["draft"])
        self.assertEqual(create[2]["target_commitish"], COMMIT)
        self.assertEqual(api.calls[-1][0], "PATCH")
        self.assertEqual(api.calls[-1][2], {"draft": False, "make_latest": "true"})
        self.assertEqual(json.loads(api.asset_content[101]), DEPLOYMENT)
        self.assertTrue(any(call[3].get("raw") for call in api.calls))

    def test_stale_sha_refuses_any_mutation(self):
        api = FakeGitHub()
        api.main = "d" * 40
        with self.assertRaisesRegex(publisher.PublishError, "current main"):
            publisher.publish(api, DEPLOYMENT)
        self.assertTrue(all(call[0] == "GET" for call in api.calls))

    def test_main_advances_after_upload_keeps_release_draft(self):
        api = FakeGitHub()
        api.move_main_after_upload = True
        with self.assertRaisesRegex(publisher.PublishError, "current main"):
            publisher.publish(api, DEPLOYMENT)
        self.assertTrue(api.release["draft"])
        self.assert_not_published(api)

    def test_newer_release_blocks_older_number(self):
        api = FakeGitHub()
        api.releases = [{"tag_name": "deploy-13-" + COMMIT[:12], "draft": False}]
        with self.assertRaisesRegex(publisher.PublishError, "newer deployment"):
            publisher.publish(api, DEPLOYMENT)
        self.assertTrue(all(call[0] == "GET" for call in api.calls))

    def test_same_release_number_for_different_commit_is_rejected(self):
        api = FakeGitHub()
        api.releases = [{"tag_name": "deploy-12-" + "d" * 12, "draft": False}]
        with self.assertRaisesRegex(publisher.PublishError, "another commit"):
            publisher.publish(api, DEPLOYMENT)
        self.assertTrue(all(call[0] == "GET" for call in api.calls))

    def test_upload_failure_and_incomplete_upload_never_publish(self):
        for flag in ("fail_upload", "incomplete_upload", "corrupt_upload"):
            api = FakeGitHub()
            setattr(api, flag, True)
            with self.subTest(flag=flag), self.assertRaises(publisher.PublishError):
                publisher.publish(api, DEPLOYMENT)
            self.assert_not_published(api)
            self.assertTrue(api.release["draft"])

    def test_published_replay_returns_without_mutation(self):
        asset = {"id": 5, "name": "deploy.json", "state": "uploaded"}
        api = FakeGitHub(existing_release(draft=False), [asset])
        api.asset_content[5] = json.dumps(DEPLOYMENT).encode()
        publisher.publish(api, DEPLOYMENT)
        self.assertTrue(all(call[0] == "GET" for call in api.calls))

    def test_incomplete_or_different_published_release_never_overwritten(self):
        for assets, content in [([], None), ([{"id": 5, "name": "deploy.json", "state": "uploaded"}], b"{}"), ([{"id": 5, "name": "deploy.json", "state": "starter"}], json.dumps(DEPLOYMENT).encode())]:
            api = FakeGitHub(existing_release(draft=False), assets)
            api.asset_content[5] = content
            with self.subTest(assets=assets), self.assertRaisesRegex(publisher.PublishError, "refusing to overwrite"):
                publisher.publish(api, DEPLOYMENT)
            self.assertTrue(all(call[0] == "GET" for call in api.calls))

    def test_draft_missing_manifest_is_recovered(self):
        api = FakeGitHub(existing_release())
        publisher.publish(api, DEPLOYMENT)
        self.assertFalse(api.release["draft"])
        self.assertEqual(json.loads(api.asset_content[101]), DEPLOYMENT)

    def test_draft_wrong_or_incomplete_manifest_is_replaced(self):
        for state in ("uploaded", "starter"):
            api = FakeGitHub(existing_release(), [{"id": 5, "name": "deploy.json", "state": state}])
            api.asset_content[5] = b"{}"
            publisher.publish(api, DEPLOYMENT)
            self.assertFalse(api.release["draft"])
            self.assertTrue(any(call[0] == "DELETE" for call in api.calls))

    def test_matching_draft_asset_is_reused(self):
        api = FakeGitHub(existing_release(), [{"id": 5, "name": "deploy.json", "state": "uploaded"}])
        api.asset_content[5] = json.dumps(DEPLOYMENT).encode()
        publisher.publish(api, DEPLOYMENT)
        self.assertFalse(any(call[0] in ("POST", "DELETE") for call in api.calls))
        self.assertFalse(api.release["draft"])

    def test_draft_malformed_json_asset_is_recovered(self):
        api = FakeGitHub(existing_release(), [{"id": 5, "name": "deploy.json", "state": "uploaded"}])
        api.asset_content[5] = b"unfinished JSON"
        publisher.publish(api, DEPLOYMENT)
        self.assertFalse(api.release["draft"])
        self.assertTrue(any(call[0] == "DELETE" for call in api.calls))

    def test_wrong_existing_tag_commit_prevents_any_mutation(self):
        api = FakeGitHub()
        api.tag_commit = "d" * 40
        with self.assertRaisesRegex(publisher.PublishError, "tag points"):
            publisher.publish(api, DEPLOYMENT)
        self.assertTrue(all(call[0] == "GET" for call in api.calls))

    def test_wrong_existing_release_commit_is_rejected(self):
        api = FakeGitHub(existing_release(commit="d" * 40))
        with self.assertRaisesRegex(publisher.PublishError, "different commit"):
            publisher.publish(api, DEPLOYMENT)
        self.assert_not_published(api)

    def test_metadata_requires_successful_push_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            for value in ({}, {"containerimage.digest": "latest"}, {"containerimage.digest": None}):
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.subTest(value=value), self.assertRaises(publisher.PublishError):
                    publisher.digest_from_metadata(path)
            path.write_text(json.dumps({"containerimage.digest": "sha256:" + "b" * 64}), encoding="utf-8")
            self.assertEqual(publisher.digest_from_metadata(path), "sha256:" + "b" * 64)

    def test_redirect_never_forwards_token_to_asset_storage(self):
        req = urllib.request.Request("https://api.github.com/asset", headers={"Authorization": "Bearer secret"})
        redirected = publisher.SafeRedirect().redirect_request(req, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/asset")
        self.assertFalse(redirected.has_header("Authorization"))
        same_origin = publisher.SafeRedirect().redirect_request(req, None, 302, "Found", {}, "https://api.github.com/other")
        self.assertEqual(same_origin.get_header("Authorization"), "Bearer secret")

    def test_redirect_rejects_http_unknown_host_or_url_credentials(self):
        req = urllib.request.Request("https://api.github.com/asset", headers={"Authorization": "Bearer secret"})
        for url in ("http://api.github.com/asset", "https://example.test/asset", "https://user@api.github.com/asset"):
            with self.subTest(url=url), self.assertRaises(publisher.PublishError):
                publisher.SafeRedirect().redirect_request(req, None, 302, "Found", {}, url)


if __name__ == "__main__":
    unittest.main()

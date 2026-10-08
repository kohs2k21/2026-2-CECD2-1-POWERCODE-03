"""No Docker daemon, GitHub connection, credentials, or customer data required."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import deploy
from deploy_lib import (Compose, Deployer, DeployError, FetchError, GitHub, REPOSITORY, SafeRedirect,
                        StateStore, empty_state, tag_for, validate_manifest)


def manifest(number=1):
    return {"schema_version": 1, "compose_contract": 1, "repository": REPOSITORY,
            "release_number": number, "commit": f"{number:040x}",
            "images": {service: f"ghcr.io/kohs2k21/powercode-{service}@sha256:{number:064x}"
                       for service in ("web", "api")}}


class ValidationTests(unittest.TestCase):
    def test_allowed_manifest(self):
        self.assertEqual(validate_manifest(manifest()), manifest())

    def test_rejects_bad_schema_identity_and_injection(self):
        cases = [("schema_version", True), ("schema_version", 2), ("compose_contract", 2),
                 ("release_number", 0), ("release_number", True), ("release_number", "1"),
                 ("commit", "main"), ("commit", "a" * 39), ("repository", "other/repo"),
                 ("images", {"web": "$(touch /tmp/a)", "api": "bad"}),
                 ("images", {"web": "ghcr.io/evil/web@sha256:" + "a" * 64,
                             "api": manifest()["images"]["api"]})]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                candidate = manifest()
                candidate[key] = value
                with self.assertRaises(DeployError):
                    validate_manifest(candidate)
        candidate = manifest()
        candidate["compose_file"] = "remote.yaml"
        with self.assertRaises(DeployError):
            validate_manifest(candidate)

    def release(self):
        tag = tag_for(manifest())
        return {"draft": False, "prerelease": False, "tag_name": tag,
                "target_commitish": manifest()["commit"],
                "html_url": f"https://github.com/{REPOSITORY}/releases/tag/{tag}",
                "assets": [{"name": "deploy.json", "browser_download_url":
                            f"https://github.com/{REPOSITORY}/releases/download/{tag}/deploy.json"}]}

    def test_valid_release_and_exact_asset_url(self):
        github = GitHub()
        github.get_json = Mock(side_effect=[self.release(), manifest()])
        self.assertEqual(github.latest(), manifest())

    def test_rejects_unrelated_release_draft_prerelease_wrong_commit_and_url(self):
        cases = [("draft", True), ("prerelease", True), ("tag_name", "v1"),
                 ("target_commitish", "main"), ("html_url", "https://github.com/other/repo"),
                 ("assets", []), ("assets", [{"name": "deploy.json", "browser_download_url": "https://evil/asset"}])]
        for key, value in cases:
            with self.subTest(key=key):
                release = self.release()
                release[key] = value
                github = GitHub()
                github.get_json = Mock(side_effect=[release, manifest()])
                with self.assertRaises(DeployError):
                    github.latest()

    def test_rejects_duplicate_asset_and_mismatched_release_number(self):
        release = self.release()
        release["assets"] *= 2
        github = GitHub()
        github.get_json = Mock(side_effect=[release, manifest()])
        with self.assertRaises(DeployError):
            github.latest()
        github.get_json = Mock(side_effect=[self.release(), manifest(2)])
        with self.assertRaises(DeployError):
            github.latest()

    def test_rate_limit_records_next_attempt_without_sleep(self):
        for code in (403, 429):
            with self.subTest(code=code):
                github = GitHub(clock=lambda: 1000)
                github.opener.open = Mock(side_effect=urllib.error.HTTPError(
                    "https://api.github.com", code, "secret error", {"Retry-After": "900", "X-RateLimit-Reset": "2200"}, None))
                with self.assertRaises(FetchError) as result:
                    github.get_json("https://api.github.com")
                self.assertEqual(result.exception.next_attempt_at, 2200)
                self.assertNotIn("secret", str(result.exception))

    def test_http_timeout_and_payload_bound(self):
        github = GitHub(clock=lambda: 1000)
        github.opener.open = Mock(side_effect=TimeoutError("secret"))
        with self.assertRaises(FetchError) as result:
            github.get_json("https://api.github.com")
        self.assertEqual(result.exception.next_attempt_at, 1300)
        self.assertEqual(github.opener.open.call_args.kwargs["timeout"], 30)
        response = Mock()
        response.read.return_value = b"x" * (1024 * 1024 + 1)
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        github.opener.open = Mock(return_value=context)
        with self.assertRaises(DeployError):
            github.get_json("https://api.github.com")

    def test_asset_redirect_rejects_other_hosts_and_strips_token(self):
        import urllib.request
        handler = SafeRedirect()
        request = urllib.request.Request("https://github.com/asset", headers={"Authorization": "Bearer test"})
        for url in ("http://github.com/asset", "https://evil.example/asset", "https://user@github.com/asset"):
            with self.assertRaises(DeployError):
                handler.redirect_request(request, None, 302, "redirect", {}, url)
        redirected = handler.redirect_request(request, None, 302, "redirect", {}, "https://release-assets.githubusercontent.com/asset")
        self.assertIsNone(redirected.get_header("Authorization"))


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = StateStore(self.temporary.name)
        self.compose = Mock()
        self.deployer = Deployer(self.store, self.compose, clock=lambda: 1000)

    def fail_apply(self, candidate=manifest(2)):
        self.compose.up.side_effect = [DeployError("unhealthy"), None]
        with self.assertRaises(DeployError):
            self.deployer.deploy(candidate)

    def test_success_pull_before_up_and_atomic_current_previous(self):
        self.deployer.deploy(manifest())
        self.deployer.deploy(manifest(2))
        self.assertEqual([call[0] for call in self.compose.mock_calls], ["pull", "up", "pull", "up"])
        state = self.store.load()
        self.assertEqual(state["current"], manifest(2))
        self.assertEqual(state["previous"], manifest())
        self.assertIsNone(state["in_progress"])

    def test_duplicate_and_downgrade_do_not_run_docker(self):
        self.deployer.deploy(manifest(2))
        self.compose.reset_mock()
        self.assertEqual(self.deployer.deploy(manifest(2)), "already-current")
        self.assertEqual(self.deployer.deploy(manifest()), "older-release-ignored")
        altered = manifest(2)
        altered["commit"] = "b" * 40
        self.assertEqual(self.deployer.deploy(altered), "older-release-ignored")
        self.assertEqual(self.compose.mock_calls, [])

    def test_pull_failure_preserves_current_and_does_not_record_failed(self):
        self.deployer.deploy(manifest())
        self.compose.pull.side_effect = DeployError("pull failed")
        with self.assertRaises(DeployError):
            self.deployer.deploy(manifest(2))
        state = self.store.load()
        self.assertEqual(state["current"], manifest())
        self.assertEqual(state["failed"], [])
        self.assertEqual(state["next_attempt_at"], 1300)
        self.assertIsNone(state["in_progress"])

    def test_unhealthy_deployment_rolls_back_and_blocks_repeat_until_retry(self):
        self.deployer.deploy(manifest())
        self.fail_apply()
        self.assertEqual(self.store.load()["current"], manifest())
        self.assertEqual(self.compose.up.call_args.args, (manifest(),))
        self.compose.reset_mock(side_effect=True)
        self.assertEqual(self.deployer.deploy(manifest(2)), "failed-release-ignored")
        edited_asset = manifest(2)
        edited_asset["images"]["web"] = "ghcr.io/kohs2k21/powercode-web@sha256:" + "b" * 64
        self.assertEqual(self.deployer.deploy(edited_asset), "failed-release-ignored")
        self.assertEqual(self.compose.mock_calls, [])
        self.assertEqual(self.deployer.deploy(manifest(2), retry_failed=True), "deployed")
        self.assertEqual(self.store.load()["failed"], [])

    def test_initial_up_failure_cleans_only_services(self):
        self.compose.up.side_effect = DeployError("unhealthy")
        with self.assertRaises(DeployError):
            self.deployer.deploy(manifest())
        self.compose.cleanup.assert_called_once_with(manifest())
        self.assertIsNone(self.store.load()["current"])

    def test_interrupt_journal_recovers_on_next_invocation(self):
        self.deployer.deploy(manifest())
        self.compose.up.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.deployer.deploy(manifest(2))
        self.assertEqual(self.store.load()["in_progress"]["phase"], "applying")
        self.compose.reset_mock(side_effect=True)
        recovered = Deployer(self.store, self.compose, clock=lambda: 1000)
        recovered.recover()
        self.compose.up.assert_called_once_with(manifest())
        self.assertEqual(recovered.state["current"], manifest())
        self.assertIsNone(self.store.load()["in_progress"])

    def test_crash_during_pull_keeps_current_without_false_failed_release(self):
        self.deployer.deploy(manifest())
        self.compose.pull.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.deployer.deploy(manifest(2))
        self.compose.reset_mock(side_effect=True)
        recovered = Deployer(self.store, self.compose)
        recovered.recover()
        self.assertEqual(self.compose.mock_calls, [])
        self.assertEqual(recovered.state["failed"], [])
        self.assertEqual(recovered.state["current"], manifest())

    def test_failed_recovery_preserves_journal_for_later(self):
        self.deployer.deploy(manifest())
        self.compose.up.side_effect = DeployError("unhealthy")
        with self.assertRaises(DeployError):
            self.deployer.deploy(manifest(2))
        self.assertEqual(self.store.load()["in_progress"]["phase"], "recovering")
        self.compose.reset_mock(side_effect=True)
        Deployer(self.store, self.compose).recover()
        self.assertIsNone(self.store.load()["in_progress"])

    def test_explicit_rollback_preserves_highwater_and_blocks_latest_redeploy(self):
        self.deployer.deploy(manifest())
        self.deployer.deploy(manifest(3))
        self.compose.pull.side_effect = DeployError("registry unavailable")
        self.compose.pull.reset_mock()
        self.assertEqual(self.deployer.deploy(manifest(), rollback=True), "rolled-back")
        self.compose.pull.assert_not_called()
        self.compose.pull.side_effect = None
        self.assertEqual(self.deployer.state["high_watermark"], 3)
        self.assertEqual(self.deployer.deploy(manifest(2)), "older-release-ignored")
        self.assertEqual(self.deployer.deploy(manifest(3)), "older-release-ignored")
        self.assertEqual(self.deployer.state["failed"][-1]["reason"], "manual_rollback")
        self.assertEqual(self.deployer.deploy(manifest(3), retry_failed=True), "deployed")

    def test_recovery_uses_local_images_when_registry_is_unavailable(self):
        self.deployer.deploy(manifest())
        self.compose.up.side_effect = [DeployError("unhealthy"), None]
        self.compose.pull.side_effect = [None, DeployError("registry unavailable")]
        with self.assertRaises(DeployError):
            self.deployer.deploy(manifest(2))
        self.assertEqual(self.store.load()["current"], manifest())
        self.assertIsNone(self.store.load()["in_progress"])
        self.assertEqual(self.compose.pull.call_count, 2)  # original + candidate, never recovery

    def test_incompatible_contract_rejected_before_docker(self):
        candidate = manifest()
        candidate["compose_contract"] = 2
        with self.assertRaises(DeployError):
            self.deployer.deploy(candidate)
        self.assertEqual(self.compose.mock_calls, [])

    def test_atomic_save_failure_keeps_old_state(self):
        self.store.save(empty_state())
        state = empty_state()
        state["high_watermark"] = 100
        with patch("deploy_lib.os.replace", side_effect=OSError("failure")):
            with self.assertRaises(OSError):
                self.store.save(state)
        self.assertEqual(self.store.load(), empty_state())
        self.assertEqual(list(Path(self.temporary.name).glob(".state-*")), [])

    def test_invalid_state_fails_closed(self):
        state = empty_state()
        state["current"] = manifest(2)
        self.store.save(state)
        with self.assertRaises(DeployError):
            self.store.load()

    @unittest.skipUnless(os.name == "posix", "Linux flock contract")
    def test_single_lock_and_private_state_mode(self):
        with self.store.lock():
            with self.assertRaises(DeployError):
                with StateStore(self.temporary.name).lock():
                    pass
            self.store.save(empty_state())
        self.assertEqual(self.store.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((Path(self.temporary.name) / "deploy.lock").stat().st_mode & 0o777, 0o600)

    def test_safe_status_contains_only_release_identifiers(self):
        self.deployer.deploy(manifest())
        value = json.dumps(self.deployer.status())
        self.assertNotIn("sha256", value)
        self.assertNotIn("ghcr.io", value)


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.file = Path(self.temporary.name) / "compose.yaml"
        self.file.write_text("services: {}", encoding="utf-8")
        self.env_file = Path(self.temporary.name) / "api.env"
        self.env_file.touch()
        self.environment = patch.dict(os.environ, {"API_ENV_FILE": str(self.env_file.resolve()),
                                                   "AUTH_DATA_DIR": str(Path(self.temporary.name).resolve())})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_commands_use_argv_digest_env_bounded_timeouts_and_no_output(self):
        runner = Mock(return_value=Mock(returncode=0))
        with patch.dict(os.environ, {"COMPOSE_PROJECT_NAME": "test-owned"}):
            compose = Compose(self.file, runner)
        compose.pull(manifest())
        compose.up(manifest())
        compose.cleanup(manifest())
        for call in runner.call_args_list:
            args = call.args[0]
            self.assertEqual(args[:6], ["docker", "compose", "--project-name", "test-owned", "--file", str(self.file.resolve())])
            self.assertEqual(call.kwargs["env"]["WEB_IMAGE"], manifest()["images"]["web"])
            self.assertEqual(call.kwargs["stdout"], subprocess.DEVNULL)
            self.assertEqual(call.kwargs["stderr"], subprocess.DEVNULL)
            self.assertEqual(call.kwargs["env"]["COMPOSE_DISABLE_ENV_FILE"], "true")
            self.assertEqual(call.kwargs["env"]["COMPOSE_ENV_FILES"], "")
            self.assertLessEqual(call.kwargs["timeout"], 660)
            self.assertNotIn("shell", call.kwargs)
            self.assertNotIn("down", args)
            self.assertNotIn("--volumes", args)
        self.assertIn("--wait", runner.call_args_list[1].args[0])
        self.assertEqual(runner.call_args_list[1].args[0][8:10], ["--pull", "never"])
        self.assertEqual(runner.call_args_list[-1].args[0][-4:], ["rm", "--force", "web", "api"])

    def test_subprocess_error_details_never_escape(self):
        runner = Mock(side_effect=subprocess.TimeoutExpired("secret-token", 180, output="secret-body"))
        compose = Compose(self.file, runner)
        with self.assertRaises(DeployError) as result:
            compose.up(manifest())
        self.assertNotIn("secret", str(result.exception))

    def test_cli_persists_network_backoff_and_suppresses_details(self):
        store = StateStore(self.temporary.name)
        with patch.object(StateStore, "lock", return_value=__import__("contextlib").nullcontext()), \
             patch.object(GitHub, "latest", side_effect=FetchError(9999999999)), \
             patch("sys.stderr", new_callable=io.StringIO) as output:
            result = deploy.main(["--state-dir", self.temporary.name, "--compose-file", str(self.file)])
        self.assertEqual(result, 1)
        self.assertEqual(store.load()["next_attempt_at"], 9999999999)
        self.assertEqual(output.getvalue(), "deployment failed: release fetch unavailable; check local state with --status\n")

    def test_missing_or_relative_local_paths_fail_before_docker(self):
        runner = Mock(return_value=Mock(returncode=0))
        compose = Compose(self.file, runner)
        for key, value in (("API_ENV_FILE", "relative.env"), ("AUTH_DATA_DIR", "relative"),
                           ("API_ENV_FILE", str(self.file.parent)), ("AUTH_DATA_DIR", str(self.file))):
            with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                with self.assertRaises(DeployError):
                    compose.pull(manifest())
        runner.assert_not_called()

    def test_status_works_without_local_deployment_env(self):
        with patch.object(StateStore, "lock", return_value=__import__("contextlib").nullcontext()), \
             patch.dict(os.environ, {}, clear=True), patch("sys.stdout", new_callable=io.StringIO) as output:
            result = deploy.main(["--state-dir", self.temporary.name, "--compose-file", str(self.file), "--status"])
        self.assertEqual(result, 0)
        self.assertIsNone(json.loads(output.getvalue())["current"])

    def test_cli_cooldown_skips_network_and_compose(self):
        store = StateStore(self.temporary.name)
        state = empty_state()
        state["next_attempt_at"] = 9999999999
        store.save(state)
        with patch.object(StateStore, "lock", return_value=__import__("contextlib").nullcontext()), \
             patch.object(GitHub, "latest") as fetch, \
             patch.object(Compose, "up") as up, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            result = deploy.main(["--state-dir", self.temporary.name, "--compose-file", str(self.file)])
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), "cooldown-active\n")
        fetch.assert_not_called()
        up.assert_not_called()


if __name__ == "__main__":
    unittest.main()

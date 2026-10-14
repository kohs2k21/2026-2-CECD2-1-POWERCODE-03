"""Release validation and recoverable rootless Compose deployment (stdlib only)."""
from __future__ import annotations

import contextlib
import email.utils
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY = "kohs2k21/2026-2-CECD2-1-POWERCODE-03"
CONTRACT = 1
COOLDOWN = 300
MAX_BYTES = 1024 * 1024


class DeployError(Exception):
    """Only static, safe messages may be surfaced to the operator."""


class FetchError(DeployError):
    def __init__(self, next_attempt_at: float):
        super().__init__("release fetch unavailable")
        self.next_attempt_at = next_attempt_at


def validate_manifest(value):
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "compose_contract", "repository", "release_number", "commit", "images"
    }:
        raise DeployError("invalid manifest fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise DeployError("unsupported manifest schema")
    if type(value["compose_contract"]) is not int or value["compose_contract"] != CONTRACT:
        raise DeployError("incompatible compose contract")
    if value["repository"] != REPOSITORY:
        raise DeployError("unexpected repository")
    if type(value["release_number"]) is not int or value["release_number"] < 1:
        raise DeployError("invalid release number")
    if not isinstance(value["commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", value["commit"]):
        raise DeployError("invalid commit")
    images = value["images"]
    if not isinstance(images, dict) or set(images) != {"web", "api"}:
        raise DeployError("invalid image set")
    for service in ("web", "api"):
        if not isinstance(images[service], str) or not re.fullmatch(
            rf"ghcr\.io/kohs2k21/powercode-{service}@sha256:[0-9a-f]{{64}}", images[service]
        ):
            raise DeployError("image outside digest allowlist")
    return value


def tag_for(manifest):
    return f"deploy-{manifest['release_number']}-{manifest['commit'][:12]}"


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in {
            "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"
        } or parsed.username or parsed.password:
            raise DeployError("unexpected asset redirect")
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected.remove_header("Authorization")
        return redirected


class GitHub:
    def __init__(self, token=None, clock=time.time):
        self.token = token
        self.clock = clock
        self.opener = urllib.request.build_opener(SafeRedirect())

    def get_json(self, url, authenticated=False):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "powercode-deployer"}
        if authenticated and self.token:
            headers["Authorization"] = "Bearer " + self.token
        try:
            with self.opener.open(urllib.request.Request(url, headers=headers), timeout=30) as response:
                body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise DeployError("release response too large")
            return json.loads(body)
        except urllib.error.HTTPError as exc:
            retry_at = self.clock() + COOLDOWN
            if exc.code in (403, 429):
                try:
                    retry = exc.headers.get("Retry-After", "")
                    if retry.isdigit():
                        retry_at = max(retry_at, self.clock() + int(retry))
                    elif retry:
                        retry_at = max(retry_at, email.utils.parsedate_to_datetime(retry).timestamp())
                    retry_at = max(retry_at, float(exc.headers.get("X-RateLimit-Reset", "0")))
                except (TypeError, ValueError, OverflowError):
                    pass
            raise FetchError(retry_at) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise FetchError(self.clock() + COOLDOWN) from None
        except (ValueError, UnicodeError):
            raise DeployError("invalid release JSON") from None

    def latest(self):
        release = self.get_json(f"https://api.github.com/repos/{REPOSITORY}/releases/latest", True)
        if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
            raise DeployError("release is not a stable publication")
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not re.fullmatch(r"deploy-[1-9][0-9]*-[0-9a-f]{12}", tag):
            raise DeployError("unrelated release tag")
        if release.get("html_url") != f"https://github.com/{REPOSITORY}/releases/tag/{tag}":
            raise DeployError("unexpected release repository")
        assets = release.get("assets")
        if not isinstance(assets, list):
            raise DeployError("missing release assets")
        matches = [a for a in assets if isinstance(a, dict) and a.get("name") == "deploy.json"]
        if len(matches) != 1:
            raise DeployError("missing or duplicate deployment manifest")
        asset_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/deploy.json"
        if matches[0].get("browser_download_url") != asset_url:
            raise DeployError("unexpected manifest URL")
        manifest = validate_manifest(self.get_json(asset_url))
        if tag_for(manifest) != tag or release.get("target_commitish") != manifest["commit"]:
            raise DeployError("release identity does not match manifest")
        return manifest


def empty_state():
    return {"version": 1, "current": None, "previous": None, "failed": [],
            "in_progress": None, "next_attempt_at": 0, "high_watermark": 0}


class StateStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "state.json"

    @contextlib.contextmanager
    def lock(self):
        try:
            import fcntl
        except ImportError:
            raise DeployError("deployment requires Linux flock") from None
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.directory / "deploy.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(fd, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DeployError("another deployment is running") from None
            yield
        finally:
            os.close(fd)

    def load(self):
        if not self.path.exists():
            return empty_state()
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(state, dict) or set(state) != set(empty_state()) or state["version"] != 1:
                raise ValueError
            for key in ("current", "previous"):
                if state[key] is not None:
                    validate_manifest(state[key])
            if not isinstance(state["failed"], list):
                raise ValueError
            for item in state["failed"]:
                if not isinstance(item, dict) or set(item) != {"manifest", "reason"} or item["reason"] not in ("unhealthy", "manual_rollback"):
                    raise ValueError
                validate_manifest(item["manifest"])
            if type(state["high_watermark"]) is not int or state["high_watermark"] < 0:
                raise ValueError
            if state["current"] and state["high_watermark"] < state["current"]["release_number"]:
                raise ValueError
            if not isinstance(state["next_attempt_at"], (int, float)):
                raise ValueError
            journal = state["in_progress"]
            if journal is not None:
                if set(journal) != {"candidate", "prior", "phase"} or journal["phase"] not in ("pulling", "applying", "recovering"):
                    raise ValueError
                validate_manifest(journal["candidate"])
                if journal["prior"] is not None:
                    validate_manifest(journal["prior"])
            return state
        except (ValueError, TypeError, KeyError, DeployError):
            raise DeployError("invalid local deployment state") from None

    def save(self, state):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".state-", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.chmod(name, 0o600)
                json.dump(state, stream, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
            if os.name == "posix":
                directory_fd = os.open(self.directory, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            if os.path.exists(name):
                os.unlink(name)


class Compose:
    def __init__(self, compose_file, runner=subprocess.run):
        self.file = str(Path(compose_file).resolve(strict=True))
        self.runner = runner
        self.project = os.environ.get("COMPOSE_PROJECT_NAME", "powercode-app")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", self.project):
            raise DeployError("invalid compose project name")

    def command(self, manifest, *arguments, timeout=660):
        env = os.environ.copy()
        for key, is_file in (("API_ENV_FILE", True), ("AUTH_DATA_DIR", False)):
            local_path = Path(env.get(key, ""))
            if not local_path.is_absolute() or not (local_path.is_file() if is_file else local_path.is_dir()):
                raise DeployError("local deployment paths must be absolute and exist")
        env.update(WEB_IMAGE=manifest["images"]["web"], API_IMAGE=manifest["images"]["api"],
                   COMPOSE_DISABLE_ENV_FILE="true", COMPOSE_ENV_FILES="")
        # Compose receives local settings, but its output may include secrets: never print it.
        try:
            result = self.runner(["docker", "compose", "--project-name", self.project,
                                  "--file", self.file, *arguments], env=env, timeout=timeout,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except (subprocess.TimeoutExpired, OSError):
            raise DeployError("compose command unavailable or timed out") from None
        if result.returncode:
            raise DeployError("compose command failed")

    def pull(self, manifest):
        self.command(manifest, "pull", "web", "api")

    def up(self, manifest):
        self.command(manifest, "up", "--detach", "--pull", "never", "--wait", "--wait-timeout", "120", "web", "api", timeout=180)

    def cleanup(self, manifest):
        self.command(manifest, "stop", "--timeout", "20", "web", "api", timeout=60)
        self.command(manifest, "rm", "--force", "web", "api", timeout=60)


class Deployer:
    def __init__(self, store, compose, clock=time.time):
        self.store = store
        self.compose = compose
        self.clock = clock
        self.state = store.load()

    def save(self):
        self.store.save(self.state)

    def mark_failed(self, manifest, reason="unhealthy"):
        self.state["failed"] = [m for m in self.state["failed"]
                                if m["manifest"]["release_number"] != manifest["release_number"]]
        self.state["failed"].append({"manifest": manifest, "reason": reason})

    def recover(self):
        journal = self.state["in_progress"]
        if journal is None:
            return
        if journal["phase"] == "pulling":
            self.state["in_progress"] = None
            self.save()
            return
        self.mark_failed(journal["candidate"])
        journal["phase"] = "recovering"
        self.save()
        try:
            if journal["prior"]:
                validate_manifest(journal["prior"])
                self.compose.up(journal["prior"])
            else:
                self.compose.cleanup(journal["candidate"])
        except DeployError:
            self.state["next_attempt_at"] = self.clock() + COOLDOWN
            self.save()
            raise DeployError("recovery failed; journal retained") from None
        self.state["current"] = journal["prior"]
        self.state["in_progress"] = None
        self.state["next_attempt_at"] = self.clock() + COOLDOWN
        self.save()

    def deploy(self, candidate, retry_failed=False, rollback=False):
        candidate = validate_manifest(candidate)
        current = self.state["current"]
        if candidate == current:
            return "already-current"
        previously_failed = any(m["manifest"]["release_number"] == candidate["release_number"]
                                for m in self.state["failed"])
        if not rollback:
            if candidate["release_number"] <= self.state["high_watermark"] and not (retry_failed and previously_failed):
                return "older-release-ignored"
        if previously_failed and not retry_failed and not rollback:
            return "failed-release-ignored"
        self.state["in_progress"] = {"candidate": candidate, "prior": current, "phase": "pulling"}
        self.save()
        try:
            if not rollback:
                self.compose.pull(candidate)
        except DeployError:
            self.state["in_progress"] = None
            self.state["next_attempt_at"] = self.clock() + COOLDOWN
            self.save()
            raise DeployError("image pull failed; current deployment preserved") from None
        self.state["in_progress"]["phase"] = "applying"
        self.save()
        try:
            self.compose.up(candidate)
        except DeployError:
            self.recover()
            raise DeployError("deployment unhealthy; recovery completed") from None
        self.state["previous"] = current
        self.state["current"] = candidate
        self.state["high_watermark"] = max(self.state["high_watermark"], candidate["release_number"])
        if rollback and current:
            self.mark_failed(current, "manual_rollback")
        self.state["in_progress"] = None
        self.state["failed"] = [m for m in self.state["failed"]
                                if m["manifest"]["release_number"] != candidate["release_number"]]
        self.state["next_attempt_at"] = 0
        self.save()
        return "rolled-back" if rollback else "deployed"

    def status(self):
        return {"current": tag_for(self.state["current"]) if self.state["current"] else None,
                "previous": tag_for(self.state["previous"]) if self.state["previous"] else None,
                "failed": [{"release": tag_for(m["manifest"]), "reason": m["reason"]} for m in self.state["failed"]],
                "high_watermark": self.state["high_watermark"],
                "recovery_pending": self.state["in_progress"] is not None,
                "next_attempt_at": self.state["next_attempt_at"]}

#!/usr/bin/env python3
"""Poll a validated GitHub Release and apply it using the fixed local Compose file."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

from deploy_lib import COOLDOWN, Compose, Deployer, DeployError, FetchError, GitHub, StateStore, validate_manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--compose-file", required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--manifest", help="offline deployment manifest path")
    modes.add_argument("--rollback", action="store_true", help="deploy the previous healthy release")
    modes.add_argument("--status", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args(argv)
    store = StateStore(args.state_dir)
    try:
        with store.lock():
            deployer = Deployer(store, Compose(args.compose_file))
            if args.status:
                print(json.dumps(deployer.status(), sort_keys=True))
                return 0
            if deployer.state["next_attempt_at"] > time.time() and not (args.rollback or args.retry_failed):
                print("cooldown-active")
                return 0
            deployer.recover()
            if args.rollback:
                candidate = deployer.state["previous"]
                if candidate is None:
                    raise DeployError("no previous healthy deployment")
            elif args.manifest:
                try:
                    with Path(args.manifest).open("rb") as stream:
                        body = stream.read(1024 * 1024 + 1)
                    if len(body) > 1024 * 1024:
                        raise ValueError
                    candidate = validate_manifest(json.loads(body))
                except (ValueError, OSError):
                    raise DeployError("offline manifest could not be read") from None
            else:
                try:
                    candidate = GitHub(os.environ.get("GITHUB_TOKEN")).latest()
                except FetchError as exc:
                    deployer.state["next_attempt_at"] = exc.next_attempt_at
                    deployer.save()
                    raise
                except DeployError:
                    deployer.state["next_attempt_at"] = time.time() + COOLDOWN
                    deployer.save()
                    raise
            print(deployer.deploy(candidate, args.retry_failed, args.rollback))
            return 0
    except DeployError as exc:
        # No exception details, subprocess output, env-file content, or authorization headers.
        print(f"deployment failed: {exc}; check local state with --status", file=sys.stderr)
        return 1
    except OSError:
        print("deployment failed: local file operation unavailable; check local state with --status", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("deployment interrupted; recovery journal retained", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

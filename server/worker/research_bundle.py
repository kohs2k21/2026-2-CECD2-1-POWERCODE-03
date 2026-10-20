"""연구에서 승인한 코드·가중치만 독립 실행 번들로 내보낸다."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from functools import lru_cache
from pathlib import Path


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def build(repository: Path, destination: Path) -> None:
    repository = repository.resolve(strict=True)
    if destination.exists():
        raise ValueError("bundle_destination_exists")
    design = repository / "research/models/feature_rule_design_v0_2"
    anomaly = repository / "research/anomaly_detection"
    manifest = json.loads((design / "model_manifest.json").read_text())
    destination.mkdir(parents=True)
    try:
        for kind, item in manifest["adapters"].items():
            target = destination / "design/adapters" / kind
            target.mkdir(parents=True)
            for key, hash_key in (("weights", "sha256"), ("config", "config_sha256")):
                source = repository / item[key]
                if sha(source) != item[hash_key]:
                    raise ValueError("research_adapter_hash_mismatch")
                shutil.copy2(source, target / source.name)
        shutil.copy2(design / "model_manifest.json", destination / "design/manifest.json")
        for name in ("feature_system.txt", "rule_system.txt"):
            source = design / name
            relative = source.relative_to(repository).as_posix()
            if sha(source) != manifest["publication_assets"][relative]:
                raise ValueError("research_prompt_hash_mismatch")
            shutil.copy2(source, destination / "design" / name)
        policy = destination / "design/policy"
        policy.mkdir()
        for name in ("rule_policy.py", "v1_validator.py", "pilot_io.py"):
            shutil.copy2(design / "implementation" / name, policy / name)
        # Pickle 클래스의 canonical module 경로와 전이 의존성을 유지한다.
        # CSV, 캐시, 원본 참조와 노트북은 번들에 넣지 않는다.
        for source in (anomaly / "code").rglob("*.py"):
            target = destination / "anomaly/code" / source.relative_to(anomaly / "code")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        handoff = anomaly / "reports/model_research_v12/handoff"
        model_manifest = json.loads((handoff / "manifest.json").read_text())
        models = destination / "anomaly/models"
        models.mkdir()
        for item in model_manifest["models"]:
            source = handoff / item["file"]
            if sha(source) != item["sha256"]:
                raise ValueError("research_detector_hash_mismatch")
            shutil.copy2(source, models / item["file"])
        shutil.copy2(handoff / "manifest.json", models / "manifest.json")
        assets = {p.relative_to(destination).as_posix(): sha(p) for p in destination.rglob("*") if p.is_file()}
        (destination / "bundle.json").write_text(json.dumps(
            {"schemaVersion": 1, "assets": assets}, indent=2), encoding="utf-8")
    except Exception:
        shutil.rmtree(destination)
        raise


def verify(root: Path) -> Path:
    root = root.resolve(strict=True)
    manifest = json.loads((root / "bundle.json").read_text())
    if manifest.get("schemaVersion") != 1:
        raise ValueError("invalid_research_bundle")
    for relative, expected in manifest["assets"].items():
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or sha(path) != expected:
            raise ValueError("research_bundle_hash_mismatch")
    return root


@lru_cache(maxsize=2)
def policy(root: Path):
    root = verify(root)
    path = str(root / "design/policy")
    if path not in sys.path:
        sys.path.insert(0, path)
    import rule_policy
    return rule_policy


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.repository is None:
        args.repository = next(
            (parent for parent in Path(__file__).resolve().parents
             if (parent / "research/models/feature_rule_design_v0_2/model_manifest.json").is_file()),
            None,
        )
        if args.repository is None:
            parser.error("--repository is required when the repository cannot be found beside this script")
    build(args.repository, args.output)
    print("research runtime bundle created")


if __name__ == "__main__":
    main()

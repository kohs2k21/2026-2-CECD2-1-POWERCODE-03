"""Paths and versioned, byte-pinned integration artifacts."""
from pathlib import Path
import importlib.metadata
import json
import sys

RESEARCH = Path(__file__).resolve().parents[2]
SPEC = RESEARCH / "models/feature_rule_spec"
ML_CODE = RESEARCH / "anomaly_detection/code"
for path in (SPEC, ML_CODE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from specmodel.io import InputFailure, digest, read, sha, write

CONTRACT = "feature-rule-ml-bridge1.0"
WARNING = "warning"
NONWARNING = "nonwarning"
UNASSESSED = "unassessable"


def dependencies():
    return {name: importlib.metadata.version(name) for name in
            ("numpy", "pandas", "scikit-learn", "scipy", "joblib")}


def pin(path):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha(path)}


def new_dir(path):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=False)
    return path


def seal(folder, kind):
    folder = Path(folder)
    write(folder / "manifest.json", {
        "contract": CONTRACT, "kind": kind,
        "files": [{"file": str(p.relative_to(folder)), "sha256": sha(p)}
                  for p in sorted(folder.rglob("*")) if p.is_file()
                  and p.name != "manifest.json"],
    })


def verified(folder, kind):
    folder = Path(folder).resolve()
    manifest = read(folder / "manifest.json")
    if manifest.get("contract") != CONTRACT or manifest.get("kind") != kind:
        raise InputFailure("Incompatible integration artifact")
    files = manifest["files"]
    if not files or len({x["file"] for x in files}) != len(files):
        raise InputFailure("Empty or duplicate artifact inventory")
    for item in files:
        path = (folder / item["file"]).resolve()
        if not path.is_relative_to(folder) or sha(path) != item["sha256"]:
            raise InputFailure("Artifact changed or escaped its directory: " + item["file"])
    return folder


def load_pack(folder):
    folder = verified(folder, "feature_pack")
    if "prepared.json" not in {x["file"] for x in read(folder / "manifest.json")["files"]}:
        raise InputFailure("Feature pack is not pinned")
    return read(folder / "prepared.json")


def combine(states):
    """A positive result survives a peer failure; no silent healthy fallback."""
    states = list(states)
    if any(s not in (WARNING, NONWARNING, UNASSESSED) for s in states):
        raise InputFailure("Unknown decision state")
    if WARNING in states:
        return WARNING
    return NONWARNING if states and all(s == NONWARNING for s in states) else UNASSESSED


def json_key(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)

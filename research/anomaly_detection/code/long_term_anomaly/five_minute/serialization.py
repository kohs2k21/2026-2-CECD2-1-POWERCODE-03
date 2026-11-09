"""One-time portability repair of a CLI-created trusted joblib artifact.

Numerical model state is unchanged. The original bytes remain frozen. The
portable copy references the importable class rather than __main__, and is
the artifact to deliver to a separate backend Python process.
"""
import __main__
import argparse
import joblib
from ..common import read_json, write_json, sha256
from . import localized
from .bank import load_bank


def create_portable():
    out = localized.OUT
    destination = out / "models_portable.joblib"
    if destination.exists(): raise FileExistsError("Preserve portable artifact")
    old = out / "models.joblib"
    frozen = read_json(out / "model_manifest.json")
    if sha256(old) != frozen["models_sha256"] or localized.runtime_hash() != frozen["runtime_sha256"]:
        raise ValueError("Frozen model/runtime changed")
    sentinel = object()
    previous = getattr(__main__, "LocalizedBank", sentinel)
    try:
        __main__.LocalizedBank = localized.LocalizedBank
        bank = load_bank(old)
    finally:
        if previous is sentinel: delattr(__main__, "LocalizedBank")
        else: __main__.LocalizedBank = previous
    before = joblib.hash((bank.models, bank.settings, bank.provenance))
    joblib.dump(bank, destination, compress=3)
    loaded = load_bank(destination)
    after = joblib.hash((loaded.models, loaded.settings, loaded.provenance))
    if before != after or type(loaded).__module__ == "__main__":
        raise AssertionError("Portability conversion changed the numerical model")
    write_json(out / "portable_model_manifest.json", {
        "original_frozen_model_sha256": sha256(old), "portable_model_sha256": sha256(destination),
        "numerical_state_hash_before": before, "numerical_state_hash_after": after,
        "numeric_state_identical": True, "importable_class_module": type(loaded).__module__,
        "serialization_only_no_retraining_or_parameter_change": True,
        "runtime_sha256": localized.runtime_hash()})
    print("Portable backend artifact", destination.name, destination.stat().st_size, "bytes; numeric state identical", flush=True)


def load_portable():
    out = localized.OUT
    path = out / "models_portable.joblib"
    manifest = read_json(out / "portable_model_manifest.json")
    if sha256(path) != manifest["portable_model_sha256"]:
        raise ValueError("Portable model changed")
    return load_bank(path)


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    create_portable()

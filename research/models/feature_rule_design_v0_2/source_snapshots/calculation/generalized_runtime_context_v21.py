"""Explicit source gates around every v21 public execution boundary.

The immutable ContextVar propagates only within an explicitly entered source
scope. Frozen v19 replay validators still verify exact source bytes. Their
calculations are reused after admission; no old module is patched or mutated.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import copy

from generalized_source_policy_v21 import assert_source_admission, assert_source_paths
from generalized_content_policy_v16 import assert_profile_admission

ROOT = Path(__file__).resolve().parent
_scope = ContextVar("v21_source_scope", default=None)


@contextmanager
def source_scope(inventory, policy, *, project_root=ROOT, relationship_capture_scope=None):
    if not isinstance(policy, dict):
        raise ValueError("v21 execution requires an explicit source policy")
    frozen = copy.deepcopy(policy)
    proof = assert_source_admission(inventory, frozen, "model_input", project_root=project_root)
    assert_profile_admission(inventory, project_root=project_root)
    token = _scope.set((frozen, Path(project_root).resolve(), copy.deepcopy(relationship_capture_scope or {})))
    try:
        yield proof
    finally:
        _scope.reset(token)


def admit(inventory, *, stage="measurement", project_root=ROOT, source_policy=None):
    current = _scope.get()
    policy = source_policy if source_policy is not None else current[0] if current else None
    root = current[1] if current and source_policy is None else Path(project_root).resolve()
    if policy is None:
        raise ValueError("Source policy missing at v21 " + stage + " boundary")
    return assert_source_admission(inventory, policy, stage, project_root=root)


def relationship_scope():
    current = _scope.get()
    return copy.deepcopy(current[2]) if current else {}


def guarded(function, *args, inventory=None, stage="measurement", **kwargs):
    if inventory is None:
        raise ValueError("Guarded v21 operations require their exact inventory")
    admit(inventory, stage=stage, project_root=kwargs.get("project_root", ROOT))
    return function(*args, **kwargs)


def build_generalized_profile(schema_path, csv_paths=None, *, source_policy, project_root=ROOT, **kwargs):
    # Checks precede the frozen profiler opening CSVs or generating statistics.
    assert_source_paths([str(schema_path)] + [str(v) for v in (csv_paths or {}).values()], source_policy,
                        "profile", project_root=project_root)
    import json
    source = Path(schema_path)
    source = source if source.is_absolute() else Path(project_root) / source
    schema = json.loads(source.read_text(encoding="utf-8-sig"))
    assert_source_admission({"sources": [str(source)] + [str(v) for v in (csv_paths or {}).values()],
        "evidence": schema.get("evidence", [])}, source_policy, "profile", project_root=project_root)
    from generalized_profile_v16 import build_generalized_profile as build
    result = build(schema_path, csv_paths, project_root=project_root, **kwargs)
    admit(result, stage="profile", source_policy=source_policy, project_root=project_root)
    return result


def read_dataset(inventory, *, project_root=ROOT, **kwargs):
    admit(inventory, stage="measurement", project_root=project_root)
    from generalized_profile_v16 import read_dataset as read
    return read(inventory, project_root=project_root, **kwargs)


def measure_plan(plan, inventory, **kwargs):
    admit(inventory, stage="measurement", project_root=kwargs.get("project_root", ROOT))
    from generalized_measurement_v21 import measure_plan as measure
    return measure(plan, inventory, **kwargs)


def teacher_admission(inventory, *, source_policy, project_root=ROOT):
    return admit(inventory, stage="teacher", source_policy=source_policy, project_root=project_root)

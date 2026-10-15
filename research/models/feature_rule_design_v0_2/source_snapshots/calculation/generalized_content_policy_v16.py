"""Synchronized content classification and profile admission for v16 only.

This module never reads CSVs or content cell values. Physical-name aliases are
scope exclusion signals only; they never establish a business meaning. A seal
binds actual structured-schema bytes, all current classifications, policy and
the exact finite profile that may be given to a model or a measurement reader.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POLICY_VERSION = "content-scope2.0"
CONTENT_CLASSES = frozenset({"body", "payload", "content", "response_text", "free_text", "excluded",
                             "request_body", "response_body", "message_body", "response_message"})
SAFE_METADATA_CLASSES = frozenset({"metadata"})
CONTENT_NAME_PATTERN = r"(?:^|[_\W])(body|payload|request_body|response_body|message_body|response_message|response_text)(?:$|[_\W])"
_NAME = re.compile(CONTENT_NAME_PATTERN, re.I)
_SHA = re.compile(r"[0-9a-f]{64}\Z")
MAX_JSON_BYTES = 8 * 1024 * 1024


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def policy_definition():
    return {"version": POLICY_VERSION, "content_classes": sorted(CONTENT_CLASSES),
            "metadata_classes": sorted(SAFE_METADATA_CLASSES), "name_pattern": CONTENT_NAME_PATTERN,
            "name_normalization": "split_lowercase_to_uppercase_then_match_or_exact_compact_body_alias",
            "unknown_class": "reject_unless_explicit_schema_exclusion", "alias_override": False}


def classify_column(column):
    """Decide before values are touched; reject ambiguous explicit declarations."""
    if not isinstance(column, dict) or not isinstance(column.get("name"), str) or not column["name"]:
        raise ValueError("Content classification requires a physical column name")
    explicit = column.get("excluded", False)
    if type(explicit) is not bool:
        raise ValueError("Explicit schema exclusion must be a boolean")
    raw_class = column.get("content_class")
    if raw_class is not None and not isinstance(raw_class, str):
        raise ValueError("content_class must be a string or null")
    normalized = raw_class.strip().casefold() if raw_class is not None else ""
    classification = ("unclassified" if not normalized else "safe_metadata" if normalized in SAFE_METADATA_CLASSES
                      else "content" if normalized in CONTENT_CLASSES else "unconfirmed")
    if classification == "unconfirmed" and not explicit:
        raise ValueError("Unknown nonempty content_class requires explicit exclusion or schema review")
    delimited_name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", column["name"])
    name_alias = (_NAME.search(delimited_name) is not None
                  or column["name"].casefold() in {"requestbody", "responsebody", "messagebody"})
    return {"content_class": normalized, "classification": classification,
            "schema_explicit_excluded": explicit, "name_alias_excluded": name_alias,
            "excluded": explicit or classification == "content" or name_alias}


def classification_manifest(schema):
    return [{"table": table["id"], "column": column["name"], **classify_column(column)}
            for table in schema["tables"] for column in table["columns"]]


def profile_sha256(profile):
    """The scope seal is excluded to avoid a recursive commitment."""
    return canonical_sha({key: value for key, value in profile.items() if key != "scope_admission"})


def admission_identity(profile):
    return {"policy_version": POLICY_VERSION, "policy_sha256": canonical_sha(policy_definition()),
            "schema_source": copy.deepcopy(profile.get("schema_source")),
            "schema_version": profile.get("schema_version"), "source_version": profile.get("source_version"),
            "profile_sha256": profile_sha256(profile),
            "classification_decisions": copy.deepcopy(profile.get("content_policy", {}).get("classification_decisions"))}


def seal_profile(profile):
    """Producer seals current bytes; admission still independently checks schema."""
    identity = admission_identity(profile)
    profile["scope_admission"] = {"policy_version": POLICY_VERSION,
                                  "admission_identity": identity,
                                  "admission_identity_sha256": canonical_sha(identity)}
    return profile


def _local(value, root):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("A project-local path is required")
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    path.relative_to(root)
    return path


def _read_schema(path):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate structured schema object keys")
            value[key] = item
        return value
    def invalid(value):
        raise ValueError("Nonfinite structured schema values")
    with path.open("rb") as handle:
        raw = handle.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("Structured schema exceeds bounded read size")
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique, parse_constant=invalid), hashlib.sha256(raw).hexdigest()


def _physical(value):
    return {"tables": [{"id": table["id"], "name": table["name"], "primary_key": table["primary_key"],
                        "keys": [{key: item for key, item in declaration.items() if key != "statistics"} for declaration in table["keys"]],
                        "columns": [{key: item for key, item in column.items() if key != "statistics"} for column in table["columns"]]}
                       for table in value["tables"]],
            "relationships": [{key: relation[key] for key in ("id", "from_table", "from_columns", "to_table", "to_columns", "no_reference_values")}
                              for relation in value["relationships"]]}


def validate_profile_admission(profile, project_root=ROOT):
    result = {"policy_version": POLICY_VERSION, "passed": False, "admitted": False,
              "errors": [], "warnings": [], "checked_columns": 0, "admission_identity": None,
              "admission_identity_sha256": None, "validation_scope": "v16_current_content_policy_and_profile_identity",
              "csv_values_read": False}
    def fail(code, path, message):
        result["errors"].append({"code": code, "path": path, "message": message})
    if not isinstance(profile, dict):
        fail("profile_shape", "$", "Profile must be an object")
        return result
    try:
        root = Path(project_root).resolve(strict=True)
        pin = profile["schema_source"]
        path = _local(pin["path"], root)
        schema, sha = _read_schema(path)
        if pin.get("sha256") != sha:
            fail("schema_source_sha", "schema_source.sha256", "Actual structured schema differs from its pinned bytes")
            return result
        # Imported lazily: the profile module uses classification before statistics.
        from generalized_profile_v16 import inventory_schema
        expected = inventory_schema(schema, project_root=root)
        decisions = classification_manifest(schema)
        result["checked_columns"] = len(decisions)
        policy = profile.get("content_policy")
        expected_policy = {"version": POLICY_VERSION, "policy_sha256": canonical_sha(policy_definition()),
                           "classification_decisions": decisions}
        if policy != expected_policy:
            fail("content_policy_mismatch", "content_policy", "Profile classification must match the current policy and actual schema")
        if profile.get("version") != expected["version"]:
            fail("profile_version", "version", "Profile version is not current v16")
        if profile.get("schema_version") != expected["schema_version"] or _physical(profile) != _physical(expected):
            fail("physical_schema_mismatch", "tables", "Physical definitions and exclusions differ from current schema/policy")
        sources = profile.get("sources")
        if not isinstance(sources, list) or not sources or canonical_sha(sources) != profile.get("source_version"):
            fail("source_manifest_version", "source_version", "Source manifest must match its canonical version")
        else:
            registered = False
            for item in sources:
                local = _local(item["path"], root)
                if not isinstance(item.get("sha256"), str) or not _SHA.fullmatch(item["sha256"]):
                    raise ValueError("Invalid source SHA")
                registered |= local == path and item["sha256"] == sha
            if not registered:
                fail("schema_pin_unregistered", "sources", "Actual structured schema must be registered")
        scope = profile.get("scope", {})
        if scope != expected["scope"]:
            fail("scope_mismatch", "scope", "Scope must preserve exclusions and prohibit raw-value/anomaly export")
        for ti, table in enumerate(profile["tables"]):
            for ci, column in enumerate(table["columns"]):
                if column["excluded"] and column.get("statistics") is not None:
                    fail("excluded_statistics_present", f"tables[{ti}].columns[{ci}].statistics", "Excluded content cannot carry statistics")
        excluded = {(item["table"], item["column"]) for item in decisions if item["excluded"]}
        for ei, item in enumerate(profile.get("evidence", [])):
            target = item.get("target", {})
            if item.get("kind") == "data_profile" and (target.get("table"), target.get("column")) in excluded:
                fail("excluded_profile_evidence", f"evidence[{ei}]", "Excluded content cannot carry data-profile evidence")
        _, closing_sha = _read_schema(path)
        if closing_sha != sha:
            fail("schema_changed_during_admission", "schema_source", "Structured schema changed during checking")
        identity = admission_identity(profile)
        result["admission_identity"] = identity
        result["admission_identity_sha256"] = canonical_sha(identity)
        seal = profile.get("scope_admission")
        if (not isinstance(seal, dict) or seal.get("policy_version") != POLICY_VERSION
                or seal.get("admission_identity") != identity
                or seal.get("admission_identity_sha256") != result["admission_identity_sha256"]):
            fail("profile_seal_mismatch", "scope_admission", "Profile bytes, policy and source identity must match the producer seal")
    except (ValueError, KeyError, TypeError, IndexError, OSError, UnicodeError):
        fail("profile_source_verification", "schema_source", "Local structured schema and normalized physical profile could not be verified")
    result["passed"] = result["admitted"] = not result["errors"]
    return result


def assert_profile_admission(profile, project_root=ROOT):
    result = validate_profile_admission(profile, project_root=project_root)
    if not result["passed"]:
        raise ValueError("v16 content admission rejected: " + "; ".join(item["code"] + "@" + item["path"] for item in result["errors"]))
    return result

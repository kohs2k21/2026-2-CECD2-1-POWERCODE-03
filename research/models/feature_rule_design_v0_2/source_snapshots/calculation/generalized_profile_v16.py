"""Physical schema inventories and bounded local profiling, independent of T/P/M.

This module does not infer business meaning from names, export rows, connect to a
database, run a model, or perform anomaly detection. A structured schema is an
explicit declaration; it is not evidence that the live database matches it.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from generalized_content_policy_v16 import (
    POLICY_VERSION, classify_column, classification_manifest, policy_definition,
    seal_profile, validate_profile_admission, assert_profile_admission,
)

ROOT = Path(__file__).resolve().parent
VERSION = "2.2.0"
TYPES = {"string", "integer", "number", "datetime", "boolean", "unknown"}


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _local(path, project_root):
    root = Path(project_root).resolve()
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = root / resolved
    resolved = resolved.resolve()
    resolved.relative_to(root)
    return resolved


def _relative(path, project_root):
    return str(_local(path, project_root).relative_to(Path(project_root).resolve()))


def _pointer(document, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("A JSON evidence pointer must identify an explicit fact")
    value = document
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def _logical_type(column):
    if "logical_type" in column:
        value = column["logical_type"]
        if value not in TYPES:
            raise ValueError(f"Unsupported logical type: {value}")
        return value
    sql_type = str(column.get("sql_type") or "").strip().upper()
    if re.fullmatch(r"(?:SMALLINT|INT|INTEGER|BIGINT)", sql_type):
        return "integer"
    if re.fullmatch(r"(?:DECIMAL|NUMERIC|NUMBER)(?:\(\d+(?:\s*,\s*\d+)?\))?", sql_type):
        return "integer" if re.search(r",\s*0\)$", sql_type) else "number"
    if re.fullmatch(r"(?:REAL|FLOAT|DOUBLE(?: PRECISION)?)", sql_type):
        return "number"
    if re.fullmatch(r"(?:DATE|DATETIME|TIMESTAMP(?:\(\d+\))?(?: WITH(?: LOCAL)? TIME ZONE)?)", sql_type):
        return "datetime"
    if sql_type in {"BOOLEAN", "BOOL"}:
        return "boolean"
    if re.fullmatch(r"(?:VARCHAR2?|NVARCHAR2?|CHAR|NCHAR)(?:\(\d+(?:\s+(?:BYTE|CHAR))?\))?", sql_type) or sql_type in {"TEXT", "CLOB", "STRING"}:
        return "string"
    return "unknown"


def _is_excluded(column):
    return classify_column(column)["excluded"]


def _checked_external_evidence(items, project_root):
    if not isinstance(items, list):
        raise ValueError("Evidence must be an array")
    checked = []
    seen = set()
    for original in items:
        item = copy.deepcopy(original)
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"] or item["id"] in seen:
            raise ValueError("Evidence IDs must be unique nonempty strings")
        seen.add(item["id"])
        if item.get("kind") not in {"schema_declaration", "business_document", "unit_contract"}:
            raise ValueError("Unsupported evidence kind")
        source = item.get("source", {})
        if not isinstance(source, dict) or not isinstance(source.get("path"), str):
            raise ValueError("Evidence needs a local source path")
        path = _local(source.get("path", ""), project_root)
        if not path.is_file() or source.get("sha256") != digest(path):
            raise ValueError(f"Evidence source does not match its SHA: {item['id']}")
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
            actual = _pointer(document, source.get("pointer"))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ValueError(f"Evidence requires an exact JSON fact: {item['id']}") from exc
        if actual != item.get("value") or not isinstance(item.get("target"), dict):
            raise ValueError(f"Evidence fact/target mismatch: {item['id']}")
        target = item["target"]
        if item["kind"] in {"business_document", "unit_contract"}:
            if not isinstance(actual, dict) or actual.get("table") != target.get("table"):
                raise ValueError("Document facts must embed their physical target")
            if "column" in target and actual.get("column") != target["column"]:
                raise ValueError("Document column target mismatch")
            if "columns" in target and actual.get("columns") != target["columns"]:
                raise ValueError("Document column targets mismatch")
        elif "column" in target:
            parts = source["pointer"].split("/")
            if len(parts) != 5 or parts[1] != "tables" or parts[3] != "columns":
                raise ValueError("Schema column evidence needs its parent table pointer")
            parent = _pointer(document, "/" + "/".join(parts[1:3]))
            if parent.get("id") != target.get("table") or actual.get("name") != target["column"]:
                raise ValueError("Schema declaration parent table/column mismatch")
        item["source"]["path"] = _relative(path, project_root)
        item["verified_by"] = "code"
        item["verification_scope"] = "source_hash_and_exact_fact_only"
        checked.append(item)
    return checked


def _unit_verified(unit, table, column, evidence):
    refs = unit.get("evidence_ids", [])
    return bool(refs) and all(
        ref in evidence and evidence[ref].get("kind") in {"business_document", "unit_contract"} and
        evidence[ref].get("target", {}).get("table") == table and
        evidence[ref].get("target", {}).get("column") == column and
        evidence[ref].get("value") == {"table": table, "column": column, "unit": unit.get("name")} and
        evidence[ref].get("verified_by") == "code"
        for ref in refs)


def inventory_schema(schema, *, project_root=ROOT):
    """Normalize an explicit physical schema; no business mapping is inferred."""
    if not isinstance(schema, dict) or not isinstance(schema.get("tables"), list) or not schema["tables"]:
        raise ValueError("A structured schema needs a nonempty tables array")
    external = _checked_external_evidence(schema.get("evidence", []), project_root)
    external_by_id = {item["id"]: item for item in external}
    raw_source = {"path": "memory://structured_schema", "sha256": canonical_sha(schema)}
    evidence = list(external)
    tables = []
    ids = set()
    for ti, raw in enumerate(schema["tables"]):
        if not isinstance(raw, dict):
            raise ValueError("Physical tables must be objects")
        table_id = raw.get("id")
        if not isinstance(table_id, str) or not table_id or table_id in ids:
            raise ValueError("Physical table IDs must be unique nonempty strings")
        ids.add(table_id)
        columns = []
        names = set()
        if not isinstance(raw.get("columns"), list) or not raw["columns"]:
            raise ValueError(f"Table has no declared columns: {table_id}")
        for ci, original in enumerate(raw["columns"]):
            if not isinstance(original, dict):
                raise ValueError("Physical columns must be objects")
            name = original.get("name")
            if not isinstance(name, str) or not name or name in names:
                raise ValueError(f"Column names must be unique: {table_id}")
            names.add(name)
            nullable = original.get("nullable")
            if nullable is not None and not isinstance(nullable, bool):
                raise ValueError("nullable must be a boolean or null")
            missing = original.get("missing_tokens", [""])
            if not isinstance(missing, list) or not missing or any(not isinstance(x, str) for x in missing) or len(set(missing)) != len(missing):
                raise ValueError("missing_tokens must be unique explicit strings")
            unit = copy.deepcopy(original.get("unit") or {"name": None, "status": "unknown", "evidence_ids": []})
            unit.setdefault("name", None)
            unit.setdefault("status", "unknown")
            unit.setdefault("evidence_ids", [])
            if (unit["status"] not in {"unknown", "declared", "verified"} or
                    not isinstance(unit["evidence_ids"], list) or
                    any(not isinstance(ref, str) for ref in unit["evidence_ids"])):
                raise ValueError("Invalid unit declaration")
            if unit["status"] == "unknown" and unit["name"] is not None:
                raise ValueError("An unknown unit cannot claim a verified name")
            if unit["status"] != "unknown" and (not isinstance(unit["name"], str) or not unit["name"]):
                raise ValueError("Known units need a name")
            if unit["status"] == "verified" and not _unit_verified(unit, table_id, name, external_by_id):
                raise ValueError("Verified unit requires independently source-checked matching business evidence")
            eid = "schema_column_" + canonical_sha([table_id, name])[:20]
            if eid in external_by_id:
                raise ValueError("External evidence collides with a generated declaration ID")
            evidence.append({"id": eid, "kind": "schema_declaration",
                             "target": {"table": table_id, "column": name},
                             "value": copy.deepcopy(original),
                             "source": {**raw_source, "pointer": f"/tables/{ti}/columns/{ci}"},
                             "verified_by": "code", "verification_scope": "structured_declaration_only"})
            if unit["status"] == "declared":
                unit["evidence_ids"] = list(dict.fromkeys([eid] + unit["evidence_ids"]))
            columns.append({"name": name, "logical_type": _logical_type(original),
                            "sql_type": original.get("sql_type"), "nullable": nullable,
                            "description": str(original.get("description", "")),
                            "content_classification": classify_column(original),
                            "excluded": _is_excluded(original), "unit": unit,
                            "missing_tokens": missing, "statistics": None,
                            "evidence_ids": [eid]})
        primary = raw.get("primary_key", [])
        if not isinstance(primary, list) or any(not isinstance(name, str) for name in primary):
            raise ValueError("Invalid primary key columns")
        keys = copy.deepcopy(raw.get("keys", []))
        if not isinstance(keys, list) or any(not isinstance(key, dict) for key in keys):
            raise ValueError("Keys must be an array of objects")
        if primary:
            keys = [{"id": "primary", "kind": "primary", "columns": primary}] + [key for key in keys if key.get("columns") != primary]
        key_ids = set()
        for key in keys:
            if not isinstance(key.get("id"), str) or not key["id"] or key["id"] in key_ids or key.get("kind", "candidate") not in {"primary", "unique", "candidate"}:
                raise ValueError("Invalid or duplicate key declaration")
            key_ids.add(key["id"])
            refs = key.get("columns", [])
            if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) for ref in refs) or len(set(refs)) != len(refs) or set(refs) - names:
                raise ValueError(f"Unknown or duplicate key columns: {table_id}")
            if any(col["excluded"] and col["name"] in refs for col in columns):
                raise ValueError("Content columns cannot be analysis keys")
            key.setdefault("kind", "candidate")
            key["statistics"] = None
        if not isinstance(primary, list) or len(set(primary)) != len(primary) or set(primary) - names:
            raise ValueError("Invalid primary key columns")
        tables.append({"id": table_id, "name": str(raw.get("name", table_id)),
                       "columns": columns, "primary_key": primary,
                       "keys": keys, "row_count": None, "csv_source": None})
    table_map = {table["id"]: table for table in tables}
    relationships = []
    relation_ids = set()
    raw_relations = schema.get("relationships", [])
    if not isinstance(raw_relations, list) or any(not isinstance(item, dict) for item in raw_relations):
        raise ValueError("Relationships must be an array of objects")
    for ri, raw in enumerate(raw_relations):
        rid = raw.get("id")
        if not isinstance(rid, str) or not rid or rid in relation_ids:
            raise ValueError("Relationship IDs must be unique")
        relation_ids.add(rid)
        for side in ("from", "to"):
            tid, names = raw.get(side + "_table"), raw.get(side + "_columns")
            if (not isinstance(tid, str) or tid not in table_map or not isinstance(names, list) or
                    not names or any(not isinstance(name, str) for name in names) or len(set(names)) != len(names)):
                raise ValueError("Relationship has invalid physical references")
            column_map = {column["name"]: column for column in table_map[tid]["columns"]}
            if any(name not in column_map or column_map[name]["excluded"] for name in names):
                raise ValueError("Relationship references unknown or excluded columns")
        if len(raw["from_columns"]) != len(raw["to_columns"]):
            raise ValueError("Relationship column arities differ")
        status = raw.get("status", "declared")
        if status not in {"declared", "proposed"}:
            raise ValueError("Only code measurements may give a relation measured status")
        no_reference = raw.get("no_reference_values", [])
        if not isinstance(no_reference, list) or any(not isinstance(value, str) for value in no_reference):
            raise ValueError("No-reference sentinels must be explicit lexical strings")
        eid = "schema_relation_" + canonical_sha(rid)[:20]
        evidence.append({"id": eid, "kind": "schema_declaration", "target": {"relationship": rid},
                         "value": copy.deepcopy(raw), "source": {**raw_source, "pointer": f"/relationships/{ri}"},
                         "verified_by": "code", "verification_scope": "structured_declaration_only"})
        relationships.append({"id": rid, "from_table": raw["from_table"],
                              "from_columns": raw["from_columns"], "to_table": raw["to_table"],
                              "to_columns": raw["to_columns"], "status": status,
                              "no_reference_values": no_reference,
                              "evidence_ids": [eid], "statistics": None})
    physical = {"tables": [{k: v for k, v in table.items() if k not in {"csv_source", "row_count"}} for table in tables],
                "relationships": relationships}
    sources = [raw_source] + [copy.deepcopy(item["source"]) for item in external]
    sources = [{"path": path, "sha256": sha} for path, sha in sorted({(x["path"], x["sha256"]) for x in sources})]
    return {"version": VERSION, "schema_version": canonical_sha(physical),
            "content_policy": {"version": POLICY_VERSION, "policy_sha256": canonical_sha(policy_definition()),
                               "classification_decisions": classification_manifest(schema)},
            "source_version": canonical_sha(sources), "dataset_id": str(schema.get("dataset_id", "structured_schema")),
            "tables": tables, "relationships": relationships, "evidence": evidence,
            "sources": sources, "mapping_version": None, "source_mode": "structured_in_memory",
            "schema_source": None,
            "scope": {"excluded_content": [{"table": table["id"], "columns": [c["name"] for c in table["columns"] if c["excluded"]]} for table in tables],
                      "raw_rows_exported": False, "raw_values_exported": False,
                      "allow_anomaly_detection": False},
            "limitations": ["Structured schema is a declaration, not a live database inspection.",
                            "No business meaning, time availability or unit is inferred from physical names."]}


def _numeric(value, logical_type):
    try:
        parsed = Decimal(value)
        if not parsed.is_finite() or not math.isfinite(float(parsed)):
            return None
        if logical_type == "integer" and parsed != parsed.to_integral_value():
            return None
        return parsed
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _summary(values):
    ordered = sorted(values)
    def encoded(value):
        return int(value) if value == value.to_integral_value() else float(value)
    def quantile(p):
        at = Decimal(len(ordered) - 1) * Decimal(str(p))
        low = int(at)
        return encoded(ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (at - low))
    return {"count_valid": len(ordered), "min": encoded(ordered[0]) if ordered else None,
            "p50": quantile(.5) if ordered else None, "p95": quantile(.95) if ordered else None,
            "p99": quantile(.99) if ordered else None, "max": encoded(ordered[-1]) if ordered else None,
            "method": "decimal_linear_interpolation"}


def _csv_rows(table, path):
    declared = [column["name"] for column in table["columns"]]
    included = {column["name"] for column in table["columns"] if not column["excluded"]}
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        if not header or len(set(header)) != len(header) or set(header) != set(declared):
            raise ValueError(f"CSV header differs from declared physical schema: {table['id']}")
        # The transport and SHA reader necessarily read CSV bytes. Excluded cell
        # strings are never assembled, returned, type-parsed or summarized.
        # csv.reader is used only for the header; projected records discard all
        # excluded characters while recognizing delimiters/quotes/newlines.
        yield from _projected_records(handle, header, included, table["id"])


def _projected_records(handle, header, included, table_id):
    index, state, chars, row, record, touched, skip_lf = 0, "start", [], {}, 2, False, False
    def keep(character):
        if index < len(header) and header[index] in included:
            chars.append(character)
    def field():
        nonlocal index, chars, state
        if index < len(header) and header[index] in included:
            row[header[index]] = "".join(chars)
        index += 1
        chars = []
        state = "start"
    def finish():
        nonlocal index, row, record, touched
        field()
        if index != len(header):
            raise ValueError(f"CSV field count mismatch: {table_id}, record {record}")
        value = row
        index, row, touched = 0, {}, False
        record += 1
        return value
    while True:
        chunk = handle.read(65536)
        if not chunk:
            break
        for character in chunk:
            if skip_lf:
                skip_lf = False
                if character == "\n":
                    continue
            touched = True
            if state == "quoted":
                if character == '"':
                    state = "after_quote"
                else:
                    keep(character)
                continue
            if state == "after_quote":
                if character == '"':
                    keep(character)
                    state = "quoted"
                    continue
                if character not in {",", "\r", "\n"}:
                    raise ValueError(f"Malformed quoted CSV field: {table_id}, record {record}")
            elif state == "start" and character == '"':
                state = "quoted"
                continue
            if character == ",":
                field()
            elif character in {"\r", "\n"}:
                yield finish()
                skip_lf = character == "\r"
            else:
                state = "unquoted"
                keep(character)
    if state == "quoted":
        raise ValueError(f"Unterminated quoted CSV field: {table_id}, record {record}")
    if touched or index:
        yield finish()


def _profile_rows(table, rows):
    active = [column for column in table["columns"] if not column["excluded"]]
    for column in active:
        name, missing = column["name"], set(column["missing_tokens"])
        values = [row[name] for row in rows]
        present = [value for value in values if value not in missing]
        typ = column["logical_type"]
        invalid = 0
        numbers = []
        if typ in {"integer", "number"}:
            for value in present:
                parsed = _numeric(value, typ)
                invalid += parsed is None
                if parsed is not None:
                    numbers.append(parsed)
        elif typ == "datetime":
            for value in present:
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    invalid += 1
        elif typ == "boolean":
            invalid = sum(value.lower() not in {"true", "false", "1", "0"} for value in present)
        column["statistics"] = {"status": "measured", "row_count": len(rows),
                                "population": {"table": table["id"], "selection": None},
                                "missing_tokens": column["missing_tokens"], "missing_count": len(values) - len(present),
                                "distinct_nonmissing": len(set(present)),
                                "constant_nonmissing": len(set(present)) == 1 if present else None,
                                "invalid_type_count": invalid if typ != "unknown" else None,
                                "numeric_summary": _summary(numbers) if typ in {"integer", "number"} else None}
    for key in table["keys"]:
        counts = Counter(tuple(row[name] for name in key["columns"]) for row in rows)
        missing = {column["name"]: set(column["missing_tokens"]) for column in active}
        missing_rows = sum(count for values, count in counts.items() if any(value in missing[name] for name, value in zip(key["columns"], values)))
        duplicate_excess = sum(count - 1 for count in counts.values())
        key["statistics"] = {"row_count": len(rows), "missing_rows": missing_rows,
                             "duplicate_excess": duplicate_excess, "unique_keys": len(counts),
                             "observed_unique_nonmissing": missing_rows == 0 and duplicate_excess == 0,
                             "population": {"table": table["id"], "selection": None}}
    table["row_count"] = len(rows)


def _relation_stats(relation, table_map, datasets):
    left = datasets[relation["from_table"]]
    right = datasets[relation["to_table"]]
    left_cols = {column["name"]: column for column in table_map[relation["from_table"]]["columns"]}
    right_cols = {column["name"]: column for column in table_map[relation["to_table"]]["columns"]}
    target = Counter(tuple(row[name] for name in relation["to_columns"]) for row in right
                     if not any(row[name] in right_cols[name]["missing_tokens"] for name in relation["to_columns"]))
    missing_ref = skipped = missing_parent = ambiguous = matched = join_rows = 0
    for row in left:
        values = tuple(row[name] for name in relation["from_columns"])
        if any(value in left_cols[name]["missing_tokens"] for name, value in zip(relation["from_columns"], values)):
            missing_ref += 1
            join_rows += 1
        elif any(value in relation["no_reference_values"] for value in values):
            skipped += 1
            join_rows += 1
        else:
            n = target.get(values, 0)
            missing_parent += n == 0
            ambiguous += n > 1
            matched += n == 1
            join_rows += max(1, n)
    return {"from_rows": len(left), "to_rows": len(right), "active_reference_rows": len(left) - missing_ref - skipped,
            "missing_reference_rows": missing_ref, "no_reference_rows": skipped,
            "missing_parent_rows": missing_parent, "ambiguous_parent_rows": ambiguous,
            "matched_parent_rows": matched, "left_join_rows": join_rows,
            "comparison": "exact_lexical_composite_equality", "semantic_parent_verified": False}


def build_generalized_profile(schema_path, csv_paths=None, *, project_root=ROOT, output=None, max_rows=10000):
    """Profile supplied local CSVs and publish once; schema-only stats stay null."""
    if not isinstance(max_rows, int) or isinstance(max_rows, bool) or max_rows < 1:
        raise ValueError("max_rows must be a positive explicit bound")
    schema_path = _local(schema_path, project_root)
    schema_sha = digest(schema_path)
    schema = json.loads(schema_path.read_text(encoding="utf-8-sig"))
    profile = inventory_schema(schema, project_root=project_root)
    old_source = next(source for source in profile["sources"] if source["path"] == "memory://structured_schema")
    schema_source = {"path": _relative(schema_path, project_root), "sha256": schema_sha}
    profile["schema_source"] = copy.deepcopy(schema_source)
    profile["sources"] = [schema_source if source == old_source else source for source in profile["sources"]]
    for item in profile["evidence"]:
        if item["source"]["path"] == old_source["path"]:
            item["source"].update(schema_source)
    profile["source_version"] = canonical_sha(profile["sources"])
    # Same policy before the first CSV digest/read and before statistics.
    seal_profile(profile)
    assert_profile_admission(profile, project_root=project_root)
    csv_paths = csv_paths or {}
    table_map = {table["id"]: table for table in profile["tables"]}
    if set(csv_paths) - set(table_map):
        raise ValueError("CSV map names unknown physical tables")
    datasets = {}
    row_total = 0
    for table_id, raw_path in csv_paths.items():
        path = _local(raw_path, project_root)
        sha = digest(path)
        source = {"path": _relative(path, project_root), "sha256": sha}
        table = table_map[table_id]
        table["csv_source"] = source
        profile["sources"].append(source)
        rows = []
        for row in _csv_rows(table, path):
            row_total += 1
            if row_total > max_rows:
                raise ValueError("Bounded profile row limit exceeded; no output published")
            rows.append(row)
        _profile_rows(table, rows)
        datasets[table_id] = rows
        if digest(path) != sha:
            raise RuntimeError("CSV changed during profiling; no output published")
        for column in table["columns"]:
            if column["excluded"]:
                continue
            eid = "profile_column_" + canonical_sha([table_id, column["name"], sha])[:20]
            column["statistics"]["evidence_ids"] = [eid]
            profile["evidence"].append({"id": eid, "kind": "data_profile", "target": {"table": table_id, "column": column["name"]},
                                        "value": copy.deepcopy(column["statistics"]),
                                        "source": {**source, "pointer": None}, "verified_by": "code",
                                        "verification_scope": "local_csv_all_rows_profile"})
    for relation in profile["relationships"]:
        if relation["from_table"] in datasets and relation["to_table"] in datasets:
            relation["statistics"] = _relation_stats(relation, table_map, datasets)
            relation["status"] = "measured"
            eid = "relation_measurement_" + canonical_sha([relation["id"], profile["sources"]])[:20]
            relation["evidence_ids"].append(eid)
            profile["evidence"].append({"id": eid, "kind": "relationship_measurement", "target": {"relationship": relation["id"]},
                                        "value": copy.deepcopy(relation["statistics"]),
                                        "source": {**table_map[relation["from_table"]]["csv_source"], "pointer": None},
                                        "source_dependencies": [table_map[relation["from_table"]]["csv_source"], table_map[relation["to_table"]]["csv_source"]],
                                        "verified_by": "code", "verification_scope": "physical_lexical_relation_only"})
    profile["sources"] = sorted(profile["sources"], key=lambda source: (source["path"], source["sha256"]))
    profile["source_version"] = canonical_sha(profile["sources"])
    profile["source_mode"] = "structured_schema_csv" if csv_paths else "structured_schema_only"
    profile["profile_coverage"] = "complete" if len(csv_paths) == len(table_map) else "partial" if csv_paths else "schema_only"
    profile["execution_limits"] = {"max_rows": max_rows, "rows_read": row_total}
    for source in profile["sources"]:
        if digest(_local(source["path"], project_root)) != source["sha256"]:
            raise RuntimeError("An input changed during profiling; no output published")
    seal_profile(profile)
    assert_profile_admission(profile, project_root=project_root)
    if output is not None:
        output = _local(output, project_root)
        if output.exists():
            raise FileExistsError("Output already exists; choose a new output directory")
        output.mkdir(parents=True, exist_ok=False)
        with (output / "generalized_profile.json").open("x", encoding="utf-8") as handle:
            json.dump(profile, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
    return profile


def read_dataset(profile, *, project_root=ROOT, max_rows=10000):
    """Read bounded lexical inputs for validated statistical plans, never export."""
    if not isinstance(max_rows, int) or isinstance(max_rows, bool) or max_rows < 1:
        raise ValueError("max_rows must be a positive explicit bound")
    # Before source CSVs are hashed/opened or lexical values are assembled.
    try:
        assert_profile_admission(profile, project_root=project_root)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    datasets = {}
    count = 0
    schema_source = profile.get("schema_source")
    has_csv = any(table.get("csv_source") is not None for table in profile["tables"])
    if has_csv and schema_source is None:
        raise RuntimeError("CSV execution requires a pinned physical schema source")
    if schema_source is not None:
        schema_path = _local(schema_source["path"], project_root)
        if digest(schema_path) != schema_source["sha256"]:
            raise RuntimeError("Physical schema source SHA no longer matches")
        expected = inventory_schema(json.loads(schema_path.read_text(encoding="utf-8-sig")), project_root=project_root)
        if expected["schema_version"] != profile["schema_version"]:
            raise RuntimeError("Physical schema version no longer matches")
        def physical_view(value):
            return {"tables": [{"id": table["id"], "name": table["name"],
                                "primary_key": table["primary_key"],
                                "keys": [{key: item for key, item in declaration.items() if key != "statistics"} for declaration in table["keys"]],
                                "columns": [{key: item for key, item in column.items() if key != "statistics"} for column in table["columns"]]} for table in value["tables"]],
                    "relationships": [{key: relation[key] for key in ("id", "from_table", "from_columns", "to_table", "to_columns", "no_reference_values")} for relation in value["relationships"]]}
        if physical_view(expected) != physical_view(profile):
            raise RuntimeError("Profile physical declarations differ from their schema source")
    if canonical_sha(profile["sources"]) != profile["source_version"]:
        raise RuntimeError("Profile source manifest version no longer matches")
    for source in profile.get("sources", []):
        if source["path"].startswith("memory://"):
            continue
        if digest(_local(source["path"], project_root)) != source["sha256"]:
            raise RuntimeError("Profile source SHA no longer matches")
    for table in profile["tables"]:
        source = table.get("csv_source")
        if source is None:
            continue
        path = _local(source["path"], project_root)
        if digest(path) != source["sha256"]:
            raise RuntimeError("CSV source SHA no longer matches")
        rows = []
        for row in _csv_rows(table, path):
            count += 1
            if count > max_rows:
                raise ValueError("Bounded dataset row limit exceeded; no partial dataset returned")
            rows.append(row)
        if digest(path) != source["sha256"]:
            raise RuntimeError("CSV changed during reading")
        if table.get("row_count") != len(rows):
            raise RuntimeError("CSV row count differs from the measured profile")
        datasets[table["id"]] = rows
    return datasets


def validate_semantic_mapping(mapping, inventory, *, project_root=ROOT):
    """Check separate proposals; physical joins alone do not verify meaning."""
    errors = []
    if not isinstance(mapping, dict) or not isinstance(mapping.get("bindings"), list):
        return {"passed": False, "errors": [{"code": "mapping_shape", "field": "/bindings"}], "status": "invalid", "mapping_version": None}
    if mapping.get("schema_version") != inventory["schema_version"]:
        errors.append({"code": "mapping_schema_version", "field": "/schema_version"})
    tables = {table["id"]: table for table in inventory["tables"]}
    evidence = {item["id"]: item for item in inventory["evidence"]}
    states = []
    for index, binding in enumerate(mapping["bindings"]):
        prefix = f"/bindings/{index}"
        if not isinstance(binding, dict):
            errors.append({"code": "mapping_binding_shape", "field": prefix})
            continue
        status = binding.get("status")
        states.append(status)
        semantic = binding.get("semantic")
        table_id, names = binding.get("table"), binding.get("columns")
        if not isinstance(semantic, str) or not semantic or status not in {"proposed", "verified"}:
            errors.append({"code": "mapping_binding_shape", "field": prefix})
        columns = {column["name"]: column for column in tables.get(table_id, {}).get("columns", [])} if isinstance(table_id, str) else {}
        valid_names = isinstance(names, list) and bool(names) and all(isinstance(name, str) for name in names)
        if not valid_names or len(set(names)) != len(names) or any(name not in columns or columns[name]["excluded"] for name in (names or [])):
            errors.append({"code": "mapping_physical_reference", "field": prefix + "/columns"})
        refs = binding.get("evidence_ids", [])
        valid_refs = isinstance(refs, list) and all(isinstance(ref, str) for ref in refs)
        if not valid_refs or any(ref not in evidence for ref in refs):
            errors.append({"code": "mapping_evidence_reference", "field": prefix + "/evidence_ids"})
        if status == "verified":
            target = {"table": table_id, "columns": names}
            value = {"table": table_id, "columns": names, "semantic": semantic}
            if not valid_refs or not refs or not all(ref in evidence and evidence[ref].get("kind") == "business_document" and
                                   all(evidence[ref].get("target", {}).get(k) == v for k, v in target.items()) and
                                   evidence[ref].get("value") == value and
                                   evidence[ref].get("verified_by") == "code" for ref in refs):
                errors.append({"code": "mapping_independent_semantic_evidence", "field": prefix + "/evidence_ids"})
            else:
                try:
                    _checked_external_evidence([evidence[ref] for ref in refs], project_root)
                except (ValueError, OSError, KeyError, TypeError, IndexError):
                    errors.append({"code": "mapping_semantic_source_verification", "field": prefix + "/evidence_ids"})
    return {"passed": not errors, "errors": errors,
            "status": "invalid" if errors else "verified" if states and all(state == "verified" for state in states) else "proposed",
            "mapping_version": canonical_sha(mapping) if not errors else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--csv-map", type=Path, help="JSON object: physical table ID to local CSV path")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    csv_paths = json.loads(_local(args.csv_map, ROOT).read_text()) if args.csv_map else None
    profile = build_generalized_profile(args.schema, csv_paths, output=args.output)
    print(json.dumps({"version": VERSION, "tables": len(profile["tables"]),
                      "source_mode": profile["source_mode"], "schema_version": profile["schema_version"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

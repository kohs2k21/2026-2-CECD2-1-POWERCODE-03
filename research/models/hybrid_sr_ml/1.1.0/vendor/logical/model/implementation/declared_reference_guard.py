"""Guard dependency_order links against explicitly declared self-references.

The guard reads only ``case['public_schema']`` (or ``case['schema']`` as a
compact input form). A schema mapping entry such as
``{"pointer_col": "SameTable.identity_col"}`` declares that pointer_col
references identity_col. It reports a dependency_order link that reverses
that relationship within SameTable. It intentionally makes no natural
language inference and does not inspect other plan kinds.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


# Unicode-aware identifier start (letters or underscore), followed by word
# characters and optional '$'. Accept exactly two dotted components only.
_DOTTED_REFERENCE = re.compile(r"^\s*((?=[^\W\d])\w[\w$]*)\.((?=[^\W\d])\w[\w$]*)\s*$")


def _schema_root(case: Mapping[str, Any]) -> Any:
    """Return the supplied schema object, without consulting descriptions."""
    if "public_schema" in case:
        return case["public_schema"]
    return case.get("schema")


def _declared_references(schema: Any) -> list[tuple[str, str, str]]:
    """Collect (field, table, referenced_column) dotted schema declarations."""
    found: list[tuple[str, str, str]] = []

    def visit(value: Any, owner_table: str | None = None) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                if isinstance(key, str) and isinstance(child, str):
                    match = _DOTTED_REFERENCE.fullmatch(child)
                    if match and owner_table == match.group(1):
                        found.append((key, match.group(1), match.group(2)))
                # A mapping key that contains a nested mapping names its
                # owning table; string-valued field declarations inherit it.
                visit(child, key if isinstance(key, str) and isinstance(child, Mapping) else owner_table)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child, owner_table)

    visit(schema)
    return found


def validate_declared_reference_direction(case: Mapping[str, Any], proposal: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return candidate errors for dependency links opposite explicit schema refs.

    Expected proposal shape: ``{"candidates": [{"id": ..., "plan": {
    "kind": "dependency_order", "select": {"table": ...}, "link": [
    {"source": ..., "target": ...}]}}]}``. Candidate labels use the
    supplied generic ``id`` when present, otherwise the zero-based index.
    """
    refs = _declared_references(_schema_root(case))
    candidates = proposal.get("candidates", [])
    if not isinstance(candidates, list):
        return []

    errors: list[dict[str, Any]] = []
    for candidate_index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            continue
        plan = candidate.get("plan")
        if not isinstance(plan, Mapping) or plan.get("kind") != "dependency_order":
            continue
        select = plan.get("select")
        table = select.get("table") if isinstance(select, Mapping) else None
        links = plan.get("link", [])
        if not isinstance(links, list):
            continue
        candidate_label = candidate.get("id", candidate_index)

        for link_index, link in enumerate(links):
            if not isinstance(link, Mapping):
                continue
            source, target = link.get("source"), link.get("target")
            for pointer_column, ref_table, identity_column in refs:
                # A self-reference to the very same column is equality, so it
                # cannot establish a reversed direction.
                if pointer_column == identity_column:
                    continue
                reversed_match = (
                    table == ref_table
                    and source == identity_column
                    and target == pointer_column
                )
                # Another explicit declaration may support this exact link in
                # the forward direction. In that case the schema is ambiguous
                # about ordering and this link must not be rejected.
                forward_supported = any(
                    table == forward_table
                    and source == forward_pointer
                    and target == forward_identity
                    and forward_pointer != forward_identity
                    for forward_pointer, forward_table, forward_identity in refs
                )
                if reversed_match and not forward_supported:
                    errors.append(
                        {
                            "candidate": candidate_label,
                            "path": f"proposal.candidates[{candidate_index}].plan.link[{link_index}]",
                            "error": "dependency_order link reverses an explicitly declared self-reference",
                            "declared_reference": {pointer_column: f"{ref_table}.{identity_column}"},
                        }
                    )
    return errors


def errors(case: Mapping[str, Any], proposal: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Short public API alias for :func:`validate_declared_reference_direction`."""
    return validate_declared_reference_direction(case, proposal)


POLICY = "declared-self-reference-direction1.0"

"""Conservative static checks for impossible literal selector conjunctions."""

from decimal import Decimal, InvalidOperation
import math
LEGACY_POLICY = "literal-conjunction-consistency1.0"
POLICY = "literal-conjunction-consistency1.2-conditional-row"
SUPPORTED_POLICIES = (LEGACY_POLICY, POLICY)


def _literal_key(value):
    """Hashable typed literal; bools stay distinct from numeric 0/1."""
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        try:
            if isinstance(value, float) and not math.isfinite(value):
                return None
            return ("number", Decimal(str(value)))
        except (InvalidOperation, ValueError):
            return None
    if isinstance(value, str):
        return ("string", value)
    return None


def _numeric(value):
    key = _literal_key(value)
    return key[1] if key is not None and key[0] == "number" else None


def _selector_filters(node, path):
    if isinstance(node, dict):
        select = node.get("select")
        if isinstance(select, dict) and isinstance(select.get("filter"), list):
            yield path + ".select.filter", select["filter"]
        if node.get("kind") == "conditional_row_check" and isinstance(node.get("violation"), list):
            yield path + ".violation", node["violation"]
            if isinstance(select, dict) and isinstance(select.get("filter"), list):
                yield path + ".applicable_violation_conjunction", select["filter"] + node["violation"]
        for key, value in node.items():
            child = f"{path}.{key}"
            if isinstance(value, list):
                for i, item in enumerate(value):
                    yield from _selector_filters(item, f"{child}[{i}]")
            elif isinstance(value, dict):
                yield from _selector_filters(value, child)


def _filter_errors(atoms):
    by_column = {}
    for index, atom in enumerate(atoms):
        if not isinstance(atom, dict) or not isinstance(atom.get("column"), str):
            continue
        by_column.setdefault(atom["column"], []).append((index, atom))

    found = []
    for column, entries in by_column.items():
        eqs, ins, not_ins, bounds = [], [], [], []
        has_missing = has_present = False
        ne_keys = set()
        lower = upper = None

        for index, atom in entries:
            op, value = atom.get("op"), atom.get("value")
            if op == "is_missing" and value is None:
                has_missing = True
            elif op == "is_present" and value is None:
                has_present = True
            elif op in ("eq", "ne"):
                key = _literal_key(value)
                if key is not None:
                    if op == "eq":
                        eqs.append((index, key, value))
                    else:
                        ne_keys.add(key)
            elif op in ("in", "not_in") and isinstance(value, list):
                keys = {_literal_key(x) for x in value}
                keys.discard(None)
                (ins if op == "in" else not_ins).append((index, keys))
            elif op in ("gt", "ge", "lt", "le"):
                number = _numeric(value)
                if number is None:
                    continue
                bounds.append((index, op, number))
                if op in ("gt", "ge"):
                    inclusive = op == "ge"
                    if lower is None or number > lower[0] or (number == lower[0] and not inclusive):
                        lower = (number, inclusive, index)
                    elif number == lower[0] and not inclusive:
                        lower = (number, False, index)
                else:
                    inclusive = op == "le"
                    if upper is None or number < upper[0] or (number == upper[0] and not inclusive):
                        upper = (number, inclusive, index)
                    elif number == upper[0] and not inclusive:
                        upper = (number, False, index)

        if has_missing and has_present:
            found.append((column, "is_missing and is_present cannot both hold"))
        if has_missing and (eqs or ins):
            found.append((column, "is_missing conflicts with a required literal value"))

        eq_values = {}
        for index, key, _ in eqs:
            eq_values.setdefault(key, index)
        if len(eq_values) > 1:
            found.append((column, "distinct eq literals cannot both hold"))
        if eqs:
            eq_key = eqs[0][1]
            if eq_key in ne_keys:
                found.append((column, "eq literal is excluded by ne"))
            if any(eq_key not in allowed for _, allowed in ins):
                found.append((column, "eq literal is excluded by in"))
            if any(eq_key in excluded for _, excluded in not_ins):
                found.append((column, "eq literal is excluded by not_in"))

        if ins:
            possible = set(ins[0][1])
            for _, allowed in ins[1:]:
                possible.intersection_update(allowed)
            for _, excluded in not_ins:
                possible.difference_update(excluded)
            if not possible:
                found.append((column, "in/not_in constraints have no common literal"))

        if lower is not None and upper is not None:
            if lower[0] > upper[0] or (lower[0] == upper[0] and not (lower[1] and upper[1])):
                found.append((column, "numeric inequalities have an empty interval"))

        if eqs and bounds:
            eq_key = eqs[0][1]
            if eq_key[0] == "number":
                value = eq_key[1]
                violates = any(
                    (op == "gt" and not value > n) or (op == "ge" and not value >= n)
                    or (op == "lt" and not value < n) or (op == "le" and not value <= n)
                    for _, op, n in bounds
                )
                if violates:
                    found.append((column, "eq literal is outside the numeric interval"))

    return found


def errors(proposal, policy=POLICY):
    """Return candidate-attributed contradictions in ANDed selector filters.

    Each selector is checked independently. No schema, records, candidate ID
    meaning, or inferred business rule participates in the decision.
    """
    if policy not in SUPPORTED_POLICIES:
        raise ValueError("Unsupported predicate consistency policy")
    found = []
    if not isinstance(proposal, dict) or not isinstance(proposal.get("candidates"), list):
        return found
    for candidate in proposal["candidates"]:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("plan"), dict):
            continue
        candidate_id = candidate.get("id")
        plan = candidate["plan"]
        for path, atoms in _selector_filters(plan, "plan"):
            for column, message in _filter_errors(atoms):
                found.append({"candidate": candidate_id, "path": path, "column": column,
                              "error": message})
    if policy == POLICY:
        from plan_satisfiability import errors as unsatisfiable
        found.extend(unsatisfiable(proposal))
    return found


def apply(result, proposal):
    if not result['executable_valid']:
        return result
    bad = errors(proposal)
    if bad:
        return {**result, 'executable_valid': False, 'decision': 'invalid',
                'errors': bad, 'rules': [], 'predicate_consistency_policy': POLICY}
    return {**result, 'predicate_consistency_policy': POLICY}

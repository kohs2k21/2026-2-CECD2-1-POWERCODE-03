"""Indexed equivalent of typed_plan.matches for one immutable relation."""
from copy import deepcopy

from typed_plan import filt
from typed_plan import all3


class RelationIndex:
    """Index target rows by non-null join keys, preserving 3-valued matches.

    The target rows and relation are snapshotted at construction. Rows rejected
    by the relation's target filter are pruned; rows with unknown filter/key
    outcomes remain eligible for conservative unknown propagation.
    """

    def __init__(self, case, relation):
        self.case = case
        self.relation = deepcopy(relation)
        self.table = relation["select"]["table"]
        self.joins = tuple(deepcopy(relation["join"]))
        self.filters = deepcopy(relation["select"]["filter"])
        self.rows = tuple(deepcopy(case["records"][self.table]))
        self._by_key = {}
        self._uncertain_key_rows = []
        self._eligible_rows = []
        self._filter_values = []
        self._result_cache = {}
        self.last_candidates_examined = 0
        for index, row in enumerate(self.rows):
            filter_value = filt(row, self.filters)
            self._filter_values.append(filter_value)
            if filter_value is False:
                continue
            self._eligible_rows.append((index, row))
            key = tuple(row.get(join["target"]) for join in self.joins)
            if any(value is None for value in key):
                self._uncertain_key_rows.append((index, row))
                continue
            try:
                hash(key)
            except TypeError:
                self._uncertain_key_rows.append((index, row))
                continue
            self._by_key.setdefault(key, []).append((index, row))

    def matches(self, source_record):
        """Return exact matches and whether any target row could match unknown."""
        source_key = tuple(source_record.get(join["source"]) for join in self.joins)
        try:
            cached = self._result_cache.get(source_key)
            cacheable = True
        except TypeError:
            cached = None
            cacheable = False
        if cached is not None:
            self.last_candidates_examined = 0
            return list(cached[0]), cached[1]

        # A missing source component cannot produce a definite match. Every
        # target row must still be checked because another join component may
        # decisively rule it out, and target-filter false can suppress unknown.
        if any(value is None for value in source_key):
            candidate_rows = self._eligible_rows
        else:
            try:
                exact = self._by_key.get(source_key, ())
                # A null target component can remain a possible match.
                candidate_rows = list(exact) + self._uncertain_key_rows
            except TypeError:
                candidate_rows = self._eligible_rows

        found = []
        unknown = False
        self.last_candidates_examined = 0
        for index, row in candidate_rows:
            self.last_candidates_examined += 1
            outcomes = []
            for value, join in zip(source_key, self.joins):
                target_value = row.get(join["target"])
                outcomes.append(None if value is None or target_value is None
                                else value == target_value)
            # Keep the reference engine's conjunction semantics: a definite
            # false on any key or filter suppresses all other unknowns.
            outcome = all3(outcomes + [self._filter_values[index]])
            if outcome is True:
                found.append((self.table, index, row))
            elif outcome is None:
                unknown = True
        if cacheable:
            self._result_cache[source_key] = (tuple(found), unknown)
        return found, unknown

from collections import Counter, defaultdict
from decimal import Decimal, localcontext
from copy import deepcopy
from datetime import datetime
import math, json, hashlib
from .io import digest, read, sha
from . import primitives as native
VERSION = 'metadata-calculation-3.0.4'
OPS = {'column': 0, 'literal': 0, 'is_missing': 1, 'subtract': 2, 'divide': 2, 'eq': 2, 'lt': 2, 'lte': 2, 'gt': 2, 'gte': 2, 'and': 2, 'datetime_diff': 2, 'convert_unit': 1, 'child_count': 0, 'conditional_count': 0, 'child_sum': 0, 'parent_match_count': 0, 'group_count': 0}
NUMERIC = {'integer', 'number'}

class PlanError(ValueError):

    def __init__(self, code, path, message):
        (self.code, self.path) = (code, path)
        super().__init__(message)

class Engine:

    def missing(self, raw, table, column):
        return raw is None or raw in self.columns[table, column].get('missing_tokens', [''])

    def key(self, row, table, keys):
        values = tuple((row.get(k) for k in keys))
        return None if any((self.missing(v, table, k) for (v, k) in zip(values, keys))) else values

    def column(self, row, table, column):
        info = self.columns[table, column]
        raw = row.get(column)
        if self.missing(raw, table, column):
            return None
        typ = native._type(info)
        if typ in NUMERIC:
            value = native._number(raw)
            return None if typ == 'integer' and value is not None and (value != value.to_integral_value()) else value
        if typ == 'timestamp':
            return native._timestamp(raw)
        if typ == 'boolean':
            return raw if type(raw) is bool else {'true': True, 'false': False, '1': True, '0': False}.get(str(raw).lower())
        return str(raw)

    def infer(self, node, table, path='/expression', depth=0):
        if depth > 16:
            raise PlanError('AST_depth', path, 'Maximum depth16')
        if not isinstance(node, dict) or node.get('op') not in OPS:
            raise PlanError('unsupported_op', path, 'Operator is not supported by AST3')
        op = node['op']
        self.nodes += 1
        if self.nodes > 128:
            raise PlanError('AST_nodes', path, 'Maximum128 nodes')
        base = {'op', 'args'} if OPS[op] else {'op'}
        extra = {'column': {'table', 'column'}, 'literal': {'value'}, 'datetime_diff': {'unit'}, 'convert_unit': {'from_unit', 'to_unit'}, 'child_count': {'relationship_id'}, 'conditional_count': {'relationship_id', 'where'}, 'child_sum': {'relationship_id', 'where', 'value'}, 'parent_match_count': {'relationship_id'}, 'group_count': {'keys', 'where'}}.get(op, set())
        if set(node) != base | extra:
            raise PlanError('AST_fields', path, 'Required exact fields: ' + str(sorted(base | extra)))
        args = node.get('args', [])
        if OPS[op] and (not isinstance(args, list) or len(args) != OPS[op]):
            raise PlanError('AST_arity', path + '/args', 'Wrong argument count')
        if op == 'column':
            k = (node['table'], node['column'])
            if node['table'] != table or k not in self.columns:
                raise PlanError('column_scope', path, 'Use an admitted column from the current analysis table')
            c = self.columns[k]
            if c.get('excluded') or c.get('scope_status') == 'excluded':
                raise PlanError('body_column', path, 'Excluded content is forbidden')
            if self.inventory.get('scope_admission', {}).get('admitted') is False:
                raise PlanError('scope', path, 'Profile not admitted')
            self.sources.add(k)
            return (native._type(c), (c.get('unit') or {}).get('name') if (c.get('unit') or {}).get('status') == 'verified' else None)
        if op == 'literal':
            v = node['value']
            if type(v) not in {str, bool, int, float, type(None)} or (isinstance(v, float) and (not math.isfinite(v))):
                raise PlanError('literal_type', path, 'Finite scalar required')
            return ('null', None) if v is None else ('boolean', 'boolean') if type(v) is bool else ('number', None) if type(v) in {int, float} else ('category', None)
        if op in {'child_count', 'conditional_count', 'child_sum', 'parent_match_count'}:
            rid = node['relationship_id']
            r = self.relations.get(rid)
            if r is None:
                raise PlanError('relationship', path, 'Unknown relationship')
            expected = r['from_table'] if op == 'parent_match_count' else r['to_table']
            if table != expected:
                raise PlanError('relationship_grain', path, 'Wrong relationship analysis table')
            self.used_relations.add(rid)
            for (t, cs) in [(r['from_table'], r['from_columns']), (r['to_table'], r['to_columns'])]:
                for c in cs:
                    if (t, c) not in self.columns:
                        raise PlanError('relationship_column', path, 'Relationship key column is absent')
                    if self.columns[t, c].get('excluded'):
                        raise PlanError('body_relationship_key', path, 'Excluded content cannot be a relationship key')
                    if native._type(self.columns[t, c]) != 'category':
                        raise PlanError('unsupported_relationship_key_normalization', path, 'Relation operator is supported for category keys; numeric/Boolean/timestamp key normalization is not implemented and must be held')
                    self.sources.add((t, c))
            if op != 'parent_match_count':
                self.aggregates = True
                self.aggregate_relations.add(rid)
                proof = self.capture.get(rid)
                if isinstance(proof, dict) and 'source' in proof and ('value' in proof):
                    source = proof['source']
                    capture_path = (self.source_root / source['path']).resolve()
                    capture_path.relative_to(self.source_root)
                    if sha(capture_path) != source['sha256']:
                        raise PlanError('capture_source', path, 'Capture source SHA changed')
                    current = read(capture_path)
                    for token in source['pointer'].strip('/').split('/'):
                        token = token.replace('~1', '/').replace('~0', '~')
                        current = current[int(token)] if isinstance(current, list) else current[token]
                    if current != proof['value']:
                        raise PlanError('capture_pointer', '/expression', 'Capture facts differ from the exact source pointer')
                    proof = proof['value']
                if proof is None:
                    entries = self.capture.get('relationship_coverage', self.capture.get('relationships', []))
                    proof = next((x for x in entries if x.get('relationship_id', x.get('id')) == rid), None) if isinstance(entries, list) else entries.get(rid)
                definition = {k: r.get(k, []) if k == 'no_reference_values' else r.get(k) for k in ('id', 'from_table', 'from_columns', 'to_table', 'to_columns', 'no_reference_values')}
                if proof is None or proof.get('status') != 'complete_for_selected_parents' or proof.get('child_count_allowed') is not True or (proof.get('schema_version') != self.inventory['schema_version']) or (proof.get('relationship_definition') != definition) or (proof.get('relationship_definition_sha') != digest(definition)) or (proof.get('parent_source_sha256') != (self.tables[r['to_table']].get('csv_source') or {}).get('sha256')) or (proof.get('child_source_sha256') != (self.tables[r['from_table']].get('csv_source') or {}).get('sha256')):
                    self.capture_missing.add(rid)
                if op in {'conditional_count', 'child_sum'} and node['where'] is not None:
                    (kind, _) = self.infer(node['where'], r['from_table'], path + '/where', depth + 1)
                    if kind != 'boolean':
                        raise PlanError('filter_type', path + '/where', 'Child predicate must be Boolean')
                if op == 'child_sum':
                    (kind, unit) = self.infer(node['value'], r['from_table'], path + '/value', depth + 1)
                    if kind not in NUMERIC:
                        raise PlanError('sum_type', path + '/value', 'Only numerical child values may be summed')
                    return ('number', unit)
            return ('integer', 'count')
        if op == 'group_count':
            self.aggregates = True
            if not isinstance(node['keys'], list) or not node['keys'] or len(set(node['keys'])) != len(node['keys']):
                raise PlanError('group_keys', path + '/keys', 'Nonempty unique categorical grouping columns required')
            for k in node['keys']:
                self.infer({'op': 'column', 'table': table, 'column': k}, table, path + '/keys', depth + 1)
            if node['where'] is not None and self.infer(node['where'], table, path + '/where', depth + 1)[0] != 'boolean':
                raise PlanError('filter_type', path + '/where', 'Group predicate must be Boolean')
            return ('integer', 'count')
        typed = [self.infer(a, table, path + '/args/' + str(i), depth + 1) for (i, a) in enumerate(args)]
        if op == 'is_missing':
            return ('boolean', 'boolean')
        if op == 'and':
            if any((t != 'boolean' for (t, u) in typed)):
                raise PlanError('logical_type', path, 'Boolean operands required')
            return ('boolean', 'boolean')
        if op in {'eq', 'lt', 'lte', 'gt', 'gte'}:
            if all((u for (t, u) in typed)) and typed[0][1] != typed[1][1]:
                raise PlanError('comparison_unit_mismatch', path, 'Explicit unit conversion required before comparing verified physical units')
            if op == 'eq' and (not (typed[0][0] == typed[1][0] or all((t in NUMERIC for (t, u) in typed)))):
                raise PlanError('comparison_type', path, 'Equality operands must have compatible physical types')
            if op != 'eq' and (not (all((t in NUMERIC for (t, u) in typed)) or all((t == 'timestamp' for (t, u) in typed)))):
                raise PlanError('comparison_type', path, 'Ordered comparison needs compatible numeric/timestamp operands')
            return ('boolean', 'boolean')
        if op == 'datetime_diff':
            if any((t != 'timestamp' for (t, u) in typed)) or node['unit'] not in {'ms', 's', 'us'}:
                raise PlanError('datetime_structure', path, 'Two datetime arguments and supported output unit required')
            return ('number', node['unit'])
        if op == 'convert_unit':
            if typed[0][0] not in NUMERIC or typed[0][1] != node['from_unit'] or node['from_unit'] not in {'ms', 's', 'us'} or (node['to_unit'] not in {'ms', 's', 'us'}):
                raise PlanError('unit_conversion_evidence', path, 'Verified input unit and supported explicit time-unit conversion required')
            return ('number', node['to_unit'])
        if any((t not in NUMERIC for (t, u) in typed)):
            raise PlanError('numeric_type', path, 'Numeric operands required')
        units = [u for (t, u) in typed]
        if all(units) and units[0] != units[1]:
            raise PlanError('unit_mismatch', path, 'Explicit conversion required before arithmetic')
        if op == 'divide':
            self.division = True
            return ('number', 'ratio' if all(units) else None)
        return ('number', units[0] if all(units) else None)

    def validate(self, plan):
        self.nodes = 0
        self.sources = set()
        self.used_relations = set()
        self.capture_missing = set()
        self.aggregates = False
        self.division = False
        self.aggregate_relations = set()
        try:
            required = {'id', 'expression', 'grain', 'population', 'group_by', 'missing_policy', 'schema_version', 'source_version'}
            if not required <= set(plan):
                raise PlanError('plan_fields', '/', 'Required plan fields missing')
            if set(plan) - required - {'description', 'origin', 'plan_version'}:
                raise PlanError('plan_fields', '/', 'Type/unit/version context must be code attached, not resubmitted')
            if plan['schema_version'] != self.inventory['schema_version'] or plan['source_version'] != self.inventory['source_version']:
                raise PlanError('source_version', '/source_version', 'Stale schema/source version')

            def contains_group(node):
                return isinstance(node, dict) and (node.get('op') == 'group_count' or any((contains_group(a) for a in node.get('args', []))) or contains_group(node.get('where')) or contains_group(node.get('value')))
            if contains_group(plan['population']['filter']):
                raise PlanError('population_aggregate_cycle', '/population/filter', 'A group frequency cannot define its own counting population; use a separately validated cohort')
            table = plan['grain']['table']
            if table not in self.tables or plan['population']['table'] != table:
                raise PlanError('population_table', '/population', 'Population and grain must use the same table')
            keys = plan['grain']['keys']
            if keys != self.tables[table]['primary_key']:
                raise PlanError('grain_key', '/grain/keys', 'Complete declared output row key required')
            for c in plan['group_by']:
                if c['table'] != table or (table, c['column']) not in self.columns:
                    raise PlanError('group_column', '/group_by', 'Unknown grouping column')
                self.infer({'op': 'column', 'table': table, 'column': c['column']}, table, '/group_by')
            (typ, unit) = self.infer(plan['expression'], table)
            if plan['population']['filter'] is not None and self.infer(plan['population']['filter'], table, '/population/filter')[0] != 'boolean':
                raise PlanError('population_type', '/population/filter', 'Boolean population filter required')
            missing = plan['missing_policy']
            expected = {'input', 'empty_group', 'denominator_zero', 'child_missing', 'unknown_predicate'}
            if set(missing) != expected:
                raise PlanError('missing_policy', '/missing_policy', 'AST3 requires explicit per-operation policies')
            if missing['input'] not in {'propagate', 'count_missing'}:
                raise PlanError('input_policy', '/missing_policy/input', 'Allowed propagate/count_missing')
            if missing['empty_group'] != ('zero' if self.aggregates else None):
                raise PlanError('empty_group', '/missing_policy/empty_group', 'Only actual aggregates use zero; column reads use null')
            if missing['denominator_zero'] != ('missing' if self.division else None):
                raise PlanError('denominator_zero', '/missing_policy/denominator_zero', 'Division by zero is unavailable, never false/zero')
            if missing['child_missing'] not in {'propagate', 'skip'} or missing['unknown_predicate'] != 'defer':
                raise PlanError('aggregation_policy', '/missing_policy', 'Missing child values explicit; unknown predicates defer')
            identity = {k: deepcopy(plan[k]) for k in required - {'id'}}
            identity.update({'calculation_policy': VERSION, 'inferred_type': typ, 'unit': unit, 'source_columns': sorted(self.sources), 'relationships': sorted(self.used_relations)})
            return {'passed': True, 'definition_status': 'valid', 'definition_sha256': digest(identity), 'proposal_sha256': digest(plan), 'inferred_type': typ, 'unit': unit, 'identity': identity, 'capture_missing': sorted(self.capture_missing), 'aggregate_relationships': sorted(self.aggregate_relations), 'errors': []}
        except (PlanError, KeyError, TypeError) as error:
            return {'passed': False, 'definition_status': 'invalid', 'definition_sha256': None, 'proposal_sha256': digest(plan), 'errors': [{'code': getattr(error, 'code', 'structure'), 'path': getattr(error, 'path', '/'), 'message': str(error)}]}

    def evaluate(self, node, row, table, policy, variables=None):
        op = node['op']
        if op == 'variable':
            return (variables or {})[node['id']]
        if op == 'column':
            return self.column(row, node['table'], node['column'])
        if op == 'literal':
            return Decimal(str(node['value'])) if type(node['value']) in {int, float} else node['value']
        if op in {'child_count', 'conditional_count', 'child_sum', 'parent_match_count'}:
            r = self.relations[node['relationship_id']]
            rid = r['id']
            parent = op != 'parent_match_count'
            key = self.key(row, r['to_table'] if parent else r['from_table'], r['to_columns'] if parent else r['from_columns'])
            if key is None or (not parent and any((v in r.get('no_reference_values', []) for v in key))):
                return None
            if not parent:
                return len(self.parent_index[rid].get(key, []))
            if len(self.parent_index[rid].get(key, [])) != 1:
                return None
            children = self.child_index[rid].get(key, [])
            selected = []
            for child in children:
                selected_flag = self.evaluate(node['where'], child, r['from_table'], policy) if node.get('where') is not None else True
                if selected_flag is None:
                    return None
                if selected_flag is True:
                    selected.append(child)
            if op in {'child_count', 'conditional_count'}:
                return len(selected)
            values = [self.evaluate(node['value'], c, r['from_table'], policy) for c in selected]
            if any((v is None for v in values)) and policy['child_missing'] == 'propagate':
                return None
            if values and all((v is None for v in values)):
                return None
            return sum((v for v in values if v is not None), Decimal(0))
        if op == 'group_count':
            key = self.key(row, table, node['keys'])
            if key is None:
                return None
            population = self.current_population if table == self.current_table else None
            signature = (table, digest(node), digest(population))
            if signature not in self.group_cache:
                counts = Counter()
                unavailable = set()
                for other in self.rows[table]:
                    other_key = self.key(other, table, node['keys'])
                    if other_key is None:
                        continue
                    population_flag = self.evaluate(population, other, table, policy) if population is not None else True
                    if population_flag is False:
                        continue
                    flag = self.evaluate(node['where'], other, table, policy) if node['where'] is not None else True
                    if population_flag is None:
                        flag = None
                    if flag is None:
                        unavailable.add(other_key)
                    if flag is True:
                        counts[other_key] += 1
                self.group_cache[signature] = (counts, unavailable)
            (counts, unavailable) = self.group_cache[signature]
            return None if key in unavailable else counts[key]
        values = [self.evaluate(a, row, table, policy, variables) for a in node.get('args', [])]
        if op == 'is_missing':
            return values[0] is None
        if any((v is None for v in values)):
            return None
        if op == 'subtract':
            return values[0] - values[1]
        if op == 'divide':
            return None if values[1] == 0 else values[0] / values[1]
        if op == 'and':
            return values[0] and values[1]
        if op == 'datetime_diff':
            if (values[0].tzinfo is None) != (values[1].tzinfo is None):
                return None
            delta = values[0] - values[1]
            us = Decimal((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds)
            return us / {'us': Decimal(1), 'ms': Decimal(1000), 's': Decimal(1000000)}[node['unit']]
        if op == 'convert_unit':
            scales = {'us': Decimal(1), 'ms': Decimal(1000), 's': Decimal(1000000)}
            return values[0] * scales[node['from_unit']] / scales[node['to_unit']]
        return {'eq': lambda : values[0] == values[1], 'lt': lambda : values[0] < values[1], 'lte': lambda : values[0] <= values[1], 'gt': lambda : values[0] > values[1], 'gte': lambda : values[0] >= values[1]}[op]()
Engine.measure_full = None

def measure_full(self, plan):
    checked = self.validate(plan)
    assert checked['passed'], checked
    assert not checked['capture_missing'], checked
    for rid in checked['aggregate_relationships']:
        if not self.relationship_bounds[rid]['parent_keys_unique']:
            return ({'definition_status': 'valid', 'measurement_status': 'deferred', 'reason': 'ambiguous parents', 'definition_sha256': checked['definition_sha256']}, [])
    self.current_table = plan['grain']['table']
    self.current_population = plan['population']['filter']
    values = []
    keys = []
    unavailable = 0
    with localcontext() as context:
        context.prec = 50
        for row in self.rows[self.current_table]:
            flag = self.evaluate(self.current_population, row, self.current_table, plan['missing_policy']) if self.current_population else True
            if flag is None:
                unavailable += 1
                continue
            if flag is False:
                continue
            values.append(self.evaluate(plan['expression'], row, self.current_table, plan['missing_policy']))
            keys.append(row[plan['grain']['keys'][0]] if len(plan['grain']['keys']) == 1 else tuple((row[k] for k in plan['grain']['keys'])))
    with localcontext() as summary_context:
        summary_context.prec = 50
        summary = _summary(values)
    summary.update(population_rows_before_selection=len(self.rows[self.current_table]), selection_unavailable_count=unavailable)
    result = {'version': 'metadata-calculation-3.0.4', 'runtime': 'full-approved-scope-1.1', 'summary_decimal_precision': 50, 'definition_status': 'valid', 'measurement_status': 'measured', 'definition_sha256': checked['definition_sha256'], 'proposal_sha256': checked['proposal_sha256'], 'source_version': self.inventory['source_version'], 'summary': summary, 'validation': checked, 'anomaly_execution': 'not_run'}
    result['measurement_sha256'] = canonical({'definition': checked['definition_sha256'], 'source': self.inventory['source_version'], 'summary': summary, 'values': vector_hash(values), 'keys': canonical(keys)})
    return (result, values)
Engine.measure_full = measure_full

def canonical(o):
    return hashlib.sha256(json.dumps(o, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

def nodes(node):
    if not isinstance(node, dict):
        return
    yield node
    for x in node.get('args', []):
        yield from nodes(x)
    for k in ('where', 'value'):
        yield from nodes(node.get(k))

def narrow(ex):
    name = ex['op']
    if name == 'column':
        return ex['table'] + '.' + ex['column']
    if name == 'literal':
        return json.dumps(ex['value'], ensure_ascii=False)
    if name == 'is_missing':
        return 'unavailability(' + narrow(ex['args'][0]) + ')'
    if name == 'datetime_diff':
        return 'signed coordinates (' + narrow(ex['args'][0]) + ' minus ' + narrow(ex['args'][1]) + ') in ' + ex['unit']
    if name in ['subtract', 'divide', 'eq', 'lt', 'lte', 'gt', 'gte', 'and']:
        return '(' + narrow(ex['args'][0]) + ' ' + {'subtract': 'minus', 'divide': 'divided by', 'eq': 'equals', 'lt': '<', 'lte': '<=', 'gt': '>', 'gte': '>=', 'and': 'AND'}[name] + ' ' + narrow(ex['args'][1]) + ')'
    if name == 'parent_match_count':
        return 'physical parent match multiplicity through ' + ex['relationship_id'] + ' for each child row'
    if name in ['child_count', 'conditional_count', 'child_sum']:
        what = 'sum of ' + narrow(ex['value']) if name == 'child_sum' else 'count of captured child rows'
        return what + ' through ' + ex['relationship_id'] + ' per parent' + (' where ' + narrow(ex['where']) if ex.get('where') else '')
    if name == 'group_count':
        return 'row-associated captured frequency grouped by ' + ','.join(ex['keys']) + (' where ' + narrow(ex['where']) if ex.get('where') else '')
    if name == 'convert_unit':
        return 'explicit ' + ex['from_unit'] + ' to ' + ex['to_unit'] + ' conversion of ' + narrow(ex['args'][0])
    raise ValueError(name)

def scalar(v):
    if v is None:
        return None
    if type(v) is bool:
        return v
    if isinstance(v, (int, float, Decimal)):
        return {'numeric': str(Decimal(str(v)).normalize())}
    return str(v)

def vector_hash(values):
    return canonical([scalar(x) for x in values])
_summary = native._summary

class Independent:
    """Second implementation on raw lexical records; never calls Engine.evaluate."""

    def value(self, node, row, t, plan):
        name = node['op']
        if name == 'column':
            return self.parse(t, node['column'], row.get(node['column']))
        if name == 'literal':
            return Decimal(str(node['value'])) if type(node['value']) in [int, float] else node['value']
        if name in ['parent_match_count', 'child_count', 'conditional_count', 'child_sum']:
            (r, parents, children) = self.index[node['relationship_id']]
            p = name != 'parent_match_count'
            ks = r['to_columns'] if p else r['from_columns']
            key = tuple((row.get(c, '') for c in ks))
            if '' in key:
                return None
            if not p:
                return len(parents.get(key, []))
            if len(parents.get(key, [])) != 1:
                return None
            picked = []
            for child in children.get(key, []):
                pred = self.value(node['where'], child, r['from_table'], plan) if node.get('where') else True
                if pred is None:
                    return None
                if pred:
                    picked.append(child)
            if name != 'child_sum':
                return len(picked)
            vs = [self.value(node['value'], x, r['from_table'], plan) for x in picked]
            if any((x is None for x in vs)) and plan['missing_policy']['child_missing'] == 'propagate':
                return None
            if vs and all((x is None for x in vs)):
                return None
            return sum((v for v in vs if v is not None), Decimal(0))
        if name == 'group_count':
            signature = canonical({'node': node, 'table': t, 'filter': plan['population']['filter']})
            if signature not in self.groups:
                counter = Counter()
                bad = set()
                for x in self.rows[t]:
                    key = tuple((x[c] for c in node['keys']))
                    if '' in key:
                        continue
                    sel = self.value(plan['population']['filter'], x, t, plan) if plan['population']['filter'] else True
                    if sel is False:
                        continue
                    flag = self.value(node['where'], x, t, plan) if node['where'] else True
                    if sel is None or flag is None:
                        bad.add(key)
                    elif flag:
                        counter[key] += 1
                self.groups[signature] = (counter, bad)
            (counter, bad) = self.groups[signature]
            key = tuple((row[c] for c in node['keys']))
            return None if '' in key or key in bad else counter[key]
        vs = [self.value(x, row, t, plan) for x in node.get('args', [])]
        if name == 'is_missing':
            return vs[0] is None
        if any((x is None for x in vs)):
            return None
        if name == 'datetime_diff':
            if (vs[0].tzinfo is None) != (vs[1].tzinfo is None):
                return None
            d = vs[0] - vs[1]
            return Decimal(d.days * 86400000000 + d.seconds * 1000000 + d.microseconds) / {'us': Decimal(1), 'ms': Decimal(1000), 's': Decimal(1000000)}[node['unit']]
        if name == 'convert_unit':
            return vs[0] * {'us': 1, 'ms': 1000, 's': 1000000}[node['from_unit']] / {'us': 1, 'ms': 1000, 's': 1000000}[node['to_unit']]
        if name == 'subtract':
            return vs[0] - vs[1]
        if name == 'divide':
            return vs[0] / vs[1] if vs[1] != 0 else None
        if name == 'and':
            return vs[0] and vs[1]
        return {'eq': lambda : vs[0] == vs[1], 'lt': lambda : vs[0] < vs[1], 'lte': lambda : vs[0] <= vs[1], 'gt': lambda : vs[0] > vs[1], 'gte': lambda : vs[0] >= vs[1]}[name]()

    def verify(self, plan, expected):
        out = []
        t = plan['grain']['table']
        unknown = 0
        with localcontext() as ctx:
            ctx.prec = 50
            for row in self.rows[t]:
                flag = self.value(plan['population']['filter'], row, t, plan) if plan['population']['filter'] else True
                if flag is None:
                    unknown += 1
                    continue
                if flag:
                    out.append(self.value(plan['expression'], row, t, plan))
        assert vector_hash(out) == vector_hash(expected), ('independent full-vector mismatch', plan)
        return {'passed': True, 'method': 'separate recursive lexical evaluator; distinct relation/group indices; exact Decimal50', 'values_compared': len(out), 'selection_unavailable': unknown, 'vector_sha': vector_hash(out), 'business_truth_label': False}

def engine_init(self, inventory, rows, capture, source_root):
    from pathlib import Path
    (self.inventory, self.rows, self.capture) = (inventory, rows, capture)
    self.source_root = Path(source_root)
    self.policy = {}
    self.tables = {t['name']: t for t in inventory['tables']}
    self.columns = {(t['name'], c['name']): c for t in inventory['tables'] for c in t['columns']}
    self.relations = {r['id']: r for r in inventory['relationships']}
    self.child_index = {}
    self.parent_index = {}
    self.group_cache = {}
    self.verify_cache = {}
    self.relationship_bounds = {}
    for (rid, r) in self.relations.items():
        cs = defaultdict(list)
        ps = defaultdict(list)
        for x in rows[r['from_table']]:
            k = self.key(x, r['from_table'], r['from_columns'])
            if k is not None:
                cs[k].append(x)
        for x in rows[r['to_table']]:
            k = self.key(x, r['to_table'], r['to_columns'])
            if k is not None:
                ps[k].append(x)
        self.child_index[rid] = cs
        self.parent_index[rid] = ps
        self.relationship_bounds[rid] = {'parent_keys_unique': all((len(v) == 1 for v in ps.values())), 'maximum_parent_matches': max(map(len, ps.values()), default=0)}
Engine.__init__ = engine_init

def independent_init(self, rows, info, relations):
    (self.rows, self.info) = (rows, info)
    self.index = {}
    self.groups = {}
    for r in relations:
        ps = defaultdict(list)
        cs = defaultdict(list)
        for x in rows[r['to_table']]:
            ps[tuple((x[c] for c in r['to_columns']))].append(x)
        for x in rows[r['from_table']]:
            cs[tuple((x[c] for c in r['from_columns']))].append(x)
        self.index[r['id']] = (r, ps, cs)

def independent_parse(self, t, c, s):
    info = self.info[t][c]
    if s is None or s in info.get('missing_tokens', ['']):
        return None
    typ = info.get('logical_type')
    if typ in ['number', 'integer']:
        n = Decimal(str(s))
        if not n.is_finite():
            raise ValueError('Nonfinite declared numeric value')
        return n
    if typ in ['datetime', 'timestamp']:
        try:
            return datetime.fromisoformat(str(s).replace('Z', '+00:00'))
        except (ValueError, TypeError):
            return None
    if typ == 'boolean':
        if type(s) is bool:
            return s
        if str(s).lower() not in ['true', 'false', '1', '0']:
            raise ValueError('Malformed Boolean')
        return str(s).lower() in ['true', '1']
    return str(s)
Independent.__init__ = independent_init
Independent.parse = independent_parse

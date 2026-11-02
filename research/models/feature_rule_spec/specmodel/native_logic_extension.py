"""Generic, typed nullable Boolean operators. Never rewrites a Model AST.

The old immutable engine is extended in memory, with this source pinned in
each executable snapshot. Legacy operators and their stored SHAs stay intact.
"""
import copy, math, json
from decimal import Decimal
VERSION = 'typed-nullable-logic1.0'
NEW_OPS = {'not', 'or', 'in', 'not_in'}

def scalar_kind(value):
    if type(value) is bool:
        return 'boolean'
    if type(value) is str:
        return 'category'
    if isinstance(value, (int, float, Decimal)) and type(value) is not bool:
        if not Decimal(str(value)).is_finite():
            raise ValueError('Finite numeric set elements required')
        return 'number'
    raise ValueError('Set elements must be non-null JSON strings, numbers or Booleans')

def membership(value, values):
    if value is None:
        return None
    kind = scalar_kind(value)
    if kind != scalar_kind(values[0]):
        raise TypeError('Membership type mismatch; Boolean is not numeric')
    if kind == 'number':
        return Decimal(str(value)) in {Decimal(str(v)) for v in values}
    return value in values

def extend_schema(schema):
    out = copy.deepcopy(schema)
    out['properties']['schema'] = {'const': 'feature5.1'}
    expr = {'$ref': '#/$defs/expression'}

    def node(op, fields):
        props = {'op': {'const': op}, **fields}
        return {'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False}
    extra = [node('not', {'args': {'type': 'array', 'items': expr, 'minItems': 1, 'maxItems': 1}}), node('or', {'args': {'type': 'array', 'items': expr, 'minItems': 2, 'maxItems': 2}})]
    values = {'anyOf': [{'type': 'array', 'items': {'type': t}, 'minItems': 1, 'maxItems': 32, 'uniqueItems': True} for t in ['string', 'number', 'boolean']]}
    extra += [node(op, {'args': {'type': 'array', 'items': expr, 'minItems': 1, 'maxItems': 1}, 'values': values}) for op in ['in', 'not_in']]
    out['$defs']['expression']['anyOf'] += extra
    return out

def operator_contract():
    return {'version': VERSION, 'output_schema': 'feature5.1', 'legacy': 'feature5.0 remains unchanged and accepted only with legacy operators', 'not': {'AST': {'op': 'not', 'args': ['one Boolean expression']}, 'input': 'Boolean', 'output': 'Boolean; unavailable if input unavailable'}, 'or': {'AST': {'op': 'or', 'args': ['Boolean expression', 'Boolean expression']}, 'input': 'two same-row Booleans', 'output': 'Boolean; strict unavailable propagation if either operand unavailable'}, 'in/not_in': {'AST': {'op': 'in or not_in', 'args': ['one expression'], 'values': ['nonempty homogeneous typed scalar set']}, 'input': 'string OR finite number OR Boolean; same category as all set elements', 'output': 'Boolean; unavailable when input unavailable', 'set_size': '1..32 unique, no null or mixed Boolean/numeric types'}, 'semantics': 'not negates one known Boolean. not_in is true exactly when a known value equals none of the supplied values. No business meaning follows from a raw code. Strict nullable AND/OR preserve unavailable; is_missing is total on retained rows.', 'row_alignment': 'Nonliteral operands preserve the declared output table and row keys. No parent-value lookup, implicit join, coalesce, or output-row conversion is added.', 'no_AST_rewrite_or_intent_correction': True}

def install(fc):
    calc = fc.calc
    if getattr(calc, '_logic_extension_installed', False):
        return
    calc._logic_extension_installed = True
    module = calc.engine_module
    Engine = module.Engine
    old_infer = Engine.infer
    old_eval = Engine.evaluate
    old_validate = Engine.validate

    def infer(self, node, table, path='/expression', depth=0):
        op = node.get('op') if isinstance(node, dict) else None
        if op not in NEW_OPS:
            return old_infer(self, node, table, path, depth)

        def fail(code, field, message):
            raise module.PlanError(code, path + field, message)
        if depth > 16:
            fail('AST_depth', '', 'Maximum depth16')
        self.nodes += 1
        if self.nodes > 128:
            fail('AST_nodes', '', 'Maximum128 nodes')
        fields = {'op', 'args'} | ({'values'} if op in ['in', 'not_in'] else set())
        if set(node) != fields:
            fail('AST_fields', '', 'Required exact fields: ' + str(sorted(fields)))
        count = 2 if op == 'or' else 1
        if not isinstance(node['args'], list) or len(node['args']) != count:
            fail('AST_arity', '/args', 'Expected ' + str(count) + ' argument(s)')
        typed = [self.infer(x, table, path + '/args/' + str(n), depth + 1) for (n, x) in enumerate(node['args'])]
        if op in ['not', 'or']:
            if any((t != 'boolean' for (t, u) in typed)):
                fail('logical_type', '/args', 'Boolean operands required; numeric values are not truth values')
        else:
            values = node['values']
            if not isinstance(values, list) or not 1 <= len(values) <= 32:
                fail('membership_set', '/values', 'Nonempty set of at most32 literals required')
            try:
                kinds = {scalar_kind(v) for v in values}
            except ValueError as e:
                fail('membership_literal_type', '/values', str(e))
            if len(kinds) != 1:
                fail('membership_type', '/values', 'Homogeneous literal types required; Boolean differs from number')
            if len({(scalar_kind(v), Decimal(str(v)) if scalar_kind(v) == 'number' else v) for v in values}) != len(values):
                fail('membership_duplicate', '/values', 'Unique literal values required')
            actual = 'number' if typed[0][0] in module.NUMERIC else typed[0][0]
            if actual != next(iter(kinds)):
                fail('membership_type', '/values', 'Set type ' + str(kinds) + ' conflicts with argument ' + str(typed[0]))
        return ('boolean', 'boolean')

    def evaluate(self, node, row, table, policy, variables=None):
        op = node.get('op')
        if op not in NEW_OPS:
            return old_eval(self, node, row, table, policy, variables)
        xs = [self.evaluate(x, row, table, policy, variables) for x in node['args']]
        if any((x is None for x in xs)):
            return None
        if op == 'not':
            if type(xs[0]) is not bool:
                raise TypeError('not requires Boolean')
            return not xs[0]
        if op == 'or':
            if any((type(x) is not bool for x in xs)):
                raise TypeError('or requires two Booleans')
            return xs[0] or xs[1]
        found = membership(xs[0], node['values'])
        return found if op == 'in' else not found

    def validate(self, plan):
        result = old_validate(self, plan)
        new = any((n.get('op') in NEW_OPS for field in ['expression'] for n in calc.nodes(plan.get(field)))) or any((n.get('op') in NEW_OPS for n in calc.nodes(plan.get('population', {}).get('filter'))))
        if result.get('passed') and new:
            result['identity']['operator_extension'] = VERSION
            result['definition_sha256'] = module.digest(result['identity'])
        return result
    Engine.infer = infer
    Engine.evaluate = evaluate
    Engine.validate = validate
    old_value = calc.Independent.value

    def independent_value(self, node, row, table, plan):
        op = node.get('op')
        if op not in NEW_OPS:
            return old_value(self, node, row, table, plan)
        xs = [self.value(x, row, table, plan) for x in node['args']]
        if any((x is None for x in xs)):
            return None
        if op == 'not':
            if type(xs[0]) is not bool:
                raise TypeError('Independent non-Boolean negation')
            return False if xs[0] else True
        if op == 'or':
            if any((type(x) is not bool for x in xs)):
                raise TypeError('Independent non-Boolean OR')
            return True if True in xs else False
        value = xs[0]
        values = node['values']
        if type(value) is bool:
            found = any((type(v) is bool and value is v for v in values))
        elif isinstance(value, (int, float, Decimal)) and type(value) is not bool:
            found = any((type(v) in [int, float] and Decimal(str(v)) == Decimal(str(value)) for v in values))
        else:
            found = any((type(v) is str and type(value) is str and (value == v) for v in values))
        return found if op == 'in' else found is False
    calc.Independent.value = independent_value
    old_narrow = calc.narrow

    def narrow(ex):
        op = ex['op']
        if op == 'not':
            return 'NOT(' + narrow(ex['args'][0]) + ')'
        if op == 'or':
            return '(' + narrow(ex['args'][0]) + ' OR ' + narrow(ex['args'][1]) + ')'
        if op in ['in', 'not_in']:
            return '(' + narrow(ex['args'][0]) + ' ' + op + ' ' + json.dumps(ex['values'], ensure_ascii=False) + ')'
        return old_narrow(ex)
    calc.narrow = narrow

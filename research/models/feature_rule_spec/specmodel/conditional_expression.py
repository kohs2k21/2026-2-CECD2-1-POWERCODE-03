"""guarded-conditional1.0: typed lazy branches; strict nullable OR is unchanged."""
import copy, json
VERSION = 'guarded-conditional1.1-null-branches'

def contract():
    return {'version': VERSION, 'AST': {'op': 'if', 'args': ['Boolean condition', 'value if true', 'value if false']}, 'condition': 'Boolean; unavailable condition yields unavailable, never the false branch', 'branches': 'Both branches validated statically; same type/unit and output row, literal scalar broadcast only. At runtime evaluate only the chosen branch.', 'result': 'Chosen branch value, including unavailable; no exception-to-null conversion', 'legacy': 'All old operators and old definition hashes retain their original policy. Only plans containing if add conditional_extension to identity.', 'errors': 'Invalid source/type/unit/relationship rejected before execution, even if branch not selected. Runtime exceptions in the chosen branch propagate.', 'no_AST_rewrite': True}

def extend(s):
    s = copy.deepcopy(s)
    ex = {'$ref': '#/$defs/expression'}
    s['$defs']['expression']['anyOf'].append({'type': 'object', 'properties': {'op': {'const': 'if'}, 'args': {'type': 'array', 'items': ex, 'minItems': 3, 'maxItems': 3}}, 'required': ['op', 'args'], 'additionalProperties': False})
    return s

def install(fc):
    calc = fc.calc
    if getattr(calc, '_guarded_conditional_installed', False):
        return
    calc._guarded_conditional_installed = True
    mod = calc.engine_module
    E = mod.Engine
    oi = E.infer
    oe = E.evaluate
    ov = E.validate

    def infer(self, n, table, path='/expression', depth=0):
        if not isinstance(n, dict) or n.get('op') != 'if':
            return oi(self, n, table, path, depth)

        def fail(code, suffix, msg):
            raise mod.PlanError(code, path + suffix, msg)
        if depth > 16:
            fail('AST_depth', '', 'Maximum depth16')
        self.nodes += 1
        if self.nodes > 128:
            fail('AST_nodes', '', 'Maximum128 nodes')
        if set(n) != {'op', 'args'} or not isinstance(n['args'], list) or len(n['args']) != 3:
            fail('AST_arity', '', 'if requires exactly3 args')
        types = [self.infer(x, table, path + '/args/' + str(k), depth + 1) for (k, x) in enumerate(n['args'])]
        if types[0][0] != 'boolean':
            fail('condition_type', '/args/0', 'if condition must be Boolean')
        (a, b) = types[1:]
        # JSON null is the bottom value, not a string or a numeric zero.
        # Both branches have already been validated; null adopts only the other branch's concrete type/unit.
        a_null = n['args'][1].get('op') == 'literal' and n['args'][1].get('value') is None
        b_null = n['args'][2].get('op') == 'literal' and n['args'][2].get('value') is None
        if a_null and b_null:
            return ('null', None)
        if a_null:
            return b
        if b_null:
            return a
        an = a[0] in mod.NUMERIC
        bn = b[0] in mod.NUMERIC
        if not (a[0] == b[0] or (an and bn)):
            fail('branch_type', '/args', 'Both branches require compatible types; actual branches=' + repr(a) + ' and ' + repr(b) + '. Boolean is not numeric. Only literal JSON null can adopt the other branch type; numeric/category/Boolean mixing remains invalid.')
        if a[1] != b[1]:
            if n['args'][1].get('op') == 'literal' and an and bn:
                unit = b[1]
            elif n['args'][2].get('op') == 'literal' and an and bn:
                unit = a[1]
            else:
                fail('branch_unit', '/args', 'Both nonliteral branches require the same confirmed unit, or both unknown')
        else:
            unit = a[1]
        typ = a[0] if a[0] == b[0] else 'number'
        return (typ, unit)

    def evaluate(self, n, row, table, policy, variables=None):
        if n.get('op') != 'if':
            return oe(self, n, row, table, policy, variables)
        condition = self.evaluate(n['args'][0], row, table, policy, variables)
        if condition is None:
            return None
        if type(condition) is not bool:
            raise TypeError('if predicate is not Boolean')
        return self.evaluate(n['args'][1 if condition else 2], row, table, policy, variables)

    def validate(self, plan):
        r = ov(self, plan)
        if r.get('passed') and any((n.get('op') == 'if' for field in [plan.get('expression'), plan.get('population', {}).get('filter')] for n in calc.nodes(field))):
            r['identity']['conditional_extension'] = VERSION
            r['definition_sha256'] = mod.digest(r['identity'])
        return r
    E.infer = infer
    E.evaluate = evaluate
    E.validate = validate
    oi2 = calc.Independent.value

    def independent(self, n, row, table, plan):
        if n.get('op') != 'if':
            return oi2(self, n, row, table, plan)
        condition = self.value(n['args'][0], row, table, plan)
        if condition is None:
            return None
        if type(condition) is not bool:
            raise TypeError('Independent conditional predicate is not Boolean')
        chosen = 1 if condition is True else 2
        return self.value(n['args'][chosen], row, table, plan)
    calc.Independent.value = independent
    oldn = calc.narrow

    def narrow(n):
        return 'conditional(' + narrow(n['args'][0]) + '; ' + narrow(n['args'][1]) + '; ' + narrow(n['args'][2]) + ')' if n['op'] == 'if' else oldn(n)
    calc.narrow = narrow

def state_proof(node, known_available=frozenset(), known_missing=frozenset()):
    signature = lambda n: json.dumps(n, sort_keys=True, separators=(',', ':'))
    op = node['op']
    sig = signature(node)
    if sig in known_available:
        return {'operator': op, 'unavailable_possible': False, 'assumption': 'Available in this branch, certified by the selected availability guard', 'children': []}
    if sig in known_missing:
        return {'operator': op, 'unavailable_possible': True, 'assumption': 'Unavailable in this branch', 'children': []}
    if op == 'if':
        (cond, yes, no) = node['args']
        c = state_proof(cond, known_available, known_missing)

        def assumptions(condition, truth):
            (a, m) = branch_facts(condition, truth)
            return (known_available | a, known_missing | m)
        y = state_proof(yes, *assumptions(cond, True))
        n = state_proof(no, *assumptions(cond, False))
        return {'operator': op, 'unavailable_possible': any((x['unavailable_possible'] for x in [c, y, n])), 'proof_scope': 'Lazy branch under declared guard; not an observed-only claim', 'children': [c, y, n]}
    children = [state_proof(n, known_available, known_missing) for n in node.get('args', [])]
    if op == 'literal':
        nullable = node.get('value') is None
    elif op == 'is_missing':
        nullable = False
    elif op == 'column':
        nullable = True
    elif op in ['child_count', 'conditional_count', 'child_sum', 'parent_match_count', 'group_count', 'datetime_diff', 'divide']:
        nullable = True
    else:
        nullable = any((x['unavailable_possible'] for x in children))
    return {'operator': op, 'unavailable_possible': nullable, 'proof_scope': 'Supported operator contract on retained selected rows', 'children': children}

def branch_facts(condition, truth):
    signature = lambda n: json.dumps(n, sort_keys=True, separators=(',', ':'))
    available = {signature(condition)}
    missing = set()
    op_ = condition['op']
    if op_ == 'is_missing':
        target = signature(condition['args'][0])
        (missing if truth else available).add(target)
    elif op_ == 'not':
        (a, m) = branch_facts(condition['args'][0], not truth)
        available |= a
        missing |= m
    elif op_ in ['and', 'or']:
        assignments = [(a, b) for a in [False, True] for b in [False, True] if (a and b if op_ == 'and' else a or b) == truth]
        possible = []
        for (a, b) in assignments:
            (aa, am) = branch_facts(condition['args'][0], a)
            (ba, bm) = branch_facts(condition['args'][1], b)
            possible.append((aa | ba, am | bm))
        available |= set.intersection(*(x[0] for x in possible))
        missing |= set.intersection(*(x[1] for x in possible))
    elif op_ in ['eq', 'lt', 'lte', 'gt', 'gte', 'in', 'not_in']:
        available |= {signature(x) for x in condition.get('args', [])}
    return (available, missing)

def retained_state_proof(expression, selection):
    return state_proof(expression, *branch_facts(selection, True)) if selection is not None else state_proof(expression)

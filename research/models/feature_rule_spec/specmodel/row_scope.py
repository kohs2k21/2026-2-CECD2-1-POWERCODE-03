from .io import digest

def reset_inference(engine):
    engine.nodes = 0
    engine.sources = set()
    engine.used_relations = set()
    engine.capture_missing = set()
    engine.aggregates = False
    engine.division = False
    engine.aggregate_relations = set()

def analyze(engine, plan):
    trace = []
    errors = []
    count = 0

    def error(code, path, actual, allowed, detail, **extra):
        errors.append(dict(code=code, path=path, actual=actual, allowed=allowed, detail=detail, **extra))

    def visit(node, scope, path, depth=0):
        nonlocal count
        count += 1
        if count > 128 or depth > 16:
            error('AST_size', path, {'nodes': count, 'depth': depth}, ['nodes<=128', 'depth<=16'], 'Preserve the analysis; split its scope rather than silently truncating the AST.')
            return None
        op = node.get('op')
        out = scope
        children = []
        if op == 'literal':
            out = None
        elif op == 'column':
            out = node['table']
        elif op in ['child_count', 'conditional_count', 'child_sum', 'parent_match_count']:
            rel = engine.relations.get(node['relationship_id'])
            if not rel:
                error('relationship', path + '/relationship_id', node['relationship_id'], list(engine.relations), 'No verified relationship for this identifier.')
                return None
            out = rel['from_table'] if op == 'parent_match_count' else rel['to_table']
            for field in ['where', 'value']:
                if isinstance(node.get(field), dict):
                    ch = visit(node[field], rel['from_table'], path + '/' + field, depth + 1)
                    children.append(ch)
                    if ch and ch['output_table'] not in [None, rel['from_table']]:
                        error('aggregate_child_scope', path + '/' + field, ch, [rel['from_table']], 'Aggregate predicates/values run on child rows; only their aggregate result is on parent rows.')
        elif op == 'group_count':
            if isinstance(node.get('where'), dict):
                children.append(visit(node['where'], scope, path + '/where', depth + 1))
        else:
            for (i, ch) in enumerate(node.get('args', [])):
                children.append(visit(ch, scope, path + '/args/' + str(i), depth + 1))
            tabs = {x['output_table'] for x in children if x and x['output_table'] is not None}
            out = next(iter(tabs)) if len(tabs) == 1 else scope if not tabs else None
            if len(tabs) > 1:
                operands = [{k: x[k] for k in ['path', 'op', 'output_table', 'row_keys', 'dtype', 'unit']} for x in children if x]
                error('operand_row_grain_mismatch', path, operands, ['all nonliteral operands return the same table rows'], 'Matching numeric units do not align different rows. No supported implicit parent/child conversion exists.', operand_outputs=operands)
        if out is not None and out != scope:
            error('node_evaluation_grain', path, {'output_table': out, 'row_keys': engine.tables.get(out, {}).get('primary_key', [])}, [{'evaluation_table': scope, 'row_keys': engine.tables.get(scope, {}).get('primary_key', [])}], 'This node returns different rows from its evaluation scope. Preserve the requested grain or explicitly hold unsupported cross-row analysis.')
        entry = {'path': path, 'op': op, 'evaluation_table': scope, 'output_table': out, 'row_keys': engine.tables.get(out, {}).get('primary_key', []) if out else [], 'scalar': op == 'literal', 'dtype': None, 'unit': None}
        try:
            reset_inference(engine)
            (entry['dtype'], entry['unit']) = engine.infer(node, out or scope, path)
        except Exception as e:
            entry['type_unit_error'] = {'code': getattr(e, 'code', 'type_unit'), 'path': getattr(e, 'path', path), 'message': str(e)}
            if not any((x['path'].startswith(path) for x in errors)):
                error(entry['type_unit_error']['code'], entry['type_unit_error']['path'], node, [], str(e))
        trace.append(entry)
        return entry
    grain = plan['grain']['table']
    root = visit(plan['expression'], grain, '/plan/expression')
    if plan['population']['table'] != grain:
        error('population_grain', '/plan/population/table', plan['population']['table'], [grain], 'Population and output grain must share table rows.')
    if plan['population'].get('filter') is not None:
        visit(plan['population']['filter'], grain, '/plan/population/filter')
    return {'policy': 'row-grain1.0+immutable-AST3-types', 'passed': not errors, 'errors': errors, 'nodes': trace, 'root': root, 'proposal_unchanged_sha': digest(plan)}

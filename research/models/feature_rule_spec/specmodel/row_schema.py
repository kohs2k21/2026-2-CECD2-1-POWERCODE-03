"""Row-indexed AST grammar. It restricts row types, not the requested logical event."""
import copy

def row_indexed(schema, dataset, output_table):
    if output_table not in dataset.engine.tables:
        return schema
    s = copy.deepcopy(schema)
    original = copy.deepcopy(s['$defs']['expression']['anyOf'])
    names = {t: 'expr_' + str(n) for (n, t) in enumerate(dataset.engine.tables)}

    def typed(n, scope):
        n = copy.deepcopy(n)
        if isinstance(n, dict):
            if n.get('$ref') == '#/$defs/expression':
                return {'$ref': '#/$defs/' + names[scope]}
            return {k: typed(v, scope) for (k, v) in n.items()}
        if isinstance(n, list):
            return [typed(v, scope) for v in n]
        return n
    for table in dataset.engine.tables:
        choices = []
        for node in original:
            op = node['properties']['op']['const']
            if op in ['child_count', 'conditional_count', 'child_sum', 'parent_match_count']:
                parent = op != 'parent_match_count'
                relations = [r for r in dataset.inventory['relationships'] if r['to_table' if parent else 'from_table'] == table]
                for rel in relations:
                    q = typed(node, table)
                    q['properties']['relationship_id'] = {'const': rel['id']}
                    for field in ['where', 'value']:
                        if field in q['properties']:
                            q['properties'][field] = typed(node['properties'][field], rel['from_table'])
                    choices.append(q)
            else:
                q = typed(node, table)
                if op == 'column':
                    q['properties']['table'] = {'const': table}
                    q['properties']['column'] = {'enum': list((c['name'] for c in dataset.engine.tables[table]['columns']))}
                if op == 'group_count':
                    q['properties']['keys']['items'] = {'enum': list((c['name'] for c in dataset.engine.tables[table]['columns']))}
                choices.append(q)
        s['$defs'][names[table]] = {'anyOf': choices}
    s['$defs']['expression'] = {'$ref': '#/$defs/' + names[output_table]}
    cand = s['properties']['candidates']['items']['properties']
    plan = cand['plan']['properties']
    plan['grain']['properties']['table'] = {'const': output_table}
    plan['grain']['properties']['keys'] = {'const': dataset.engine.tables[output_table]['primary_key']}
    plan['population']['properties']['table'] = {'const': output_table}
    return s

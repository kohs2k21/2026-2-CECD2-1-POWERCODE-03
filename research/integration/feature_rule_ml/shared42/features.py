"""Execute frozen ASTs on current CSV rows; exact Decimals feed Rule decisions."""
from decimal import localcontext
import numpy as np
import pandas as pd

from ..common import InputFailure
from ..previous_shared42 import prepare_frame


def materialize(capture, p, progress=None):
    d = p['definition']
    engine = capture.engine
    outputs = {}
    for short, spec in d['schema']['tables'].items():
        rows = capture.rows[spec['name']]
        definitions = [f for f in d['features'] if f['feature']['grain']['table'] == spec['name']]
        ids = [f['id'] for f in definitions]
        values = np.full((len(rows), len(ids)), np.nan)
        populations = np.zeros(values.shape, bool)
        unknown = np.zeros(values.shape, bool)
        exact, audits = {}, []
        for j, f in enumerate(definitions):
            feature = f['feature']
            plan = {k: feature[k] for k in ('expression','grain','population','group_by','missing_policy')}
            plan.update(id=f['id'], schema_version=engine.inventory['schema_version'], source_version=engine.inventory['source_version'])
            check = engine.validate(plan)
            if not check['passed'] or check['capture_missing'] or (check['inferred_type'], check['unit']) != (feature['dtype'], feature['unit']):
                raise InputFailure('Frozen feature no longer validates: ' + f['id'] + repr(check))
            engine.current_table, engine.current_population = spec['name'], plan['population']['filter']
            v, selected = [None] * len(rows), []
            with localcontext() as ctx:
                ctx.prec = 50
                for i, row in enumerate(rows):
                    flag = engine.evaluate(plan['population']['filter'], row, spec['name'], plan['missing_policy']) if plan['population']['filter'] is not None else True
                    unknown[i,j] = flag is None
                    if flag is not True:
                        continue
                    populations[i,j] = True
                    v[i] = engine.evaluate(plan['expression'], row, spec['name'], plan['missing_policy'])
                    selected.append(v[i])
                    if v[i] is not None:
                        values[i,j] = float(v[i])
            independent = capture.independent.verify(plan, selected)
            if not independent['passed']:
                raise InputFailure('Independent raw calculation differs: ' + f['id'])
            exact[f['id']] = v
            audits.append(dict(id=f['id'], definition_sha=feature['sha'], valid=int(np.isfinite(values[:,j]).sum()), selected=int(populations[:,j].sum()), selection_unknown=int(unknown[:,j].sum()), independent=independent))
            if progress:
                progress(short, f['id'])
        metadata = []
        dates = {r['TRANSACTION_ID']: r['START_TIME'][:10] for r in capture.rows[capture.transaction_table] if r['TRANSACTION_ID']}
        for row in rows:
            adapter = row.get('ADAPTER_TYPE', '__GLOBAL__') or '__MISSING__'
            if short == 'M':
                rel = 'physical_M_to_P_retry'
                key = engine.key(row, spec['name'], ['TRANSACTION_ID','PROCESS_ID','__RETRY_LEXICAL'])
                parents = engine.parent_index[rel].get(key, []) if key is not None else []
                adapter = (parents[0].get('ADAPTER_TYPE') or '__MISSING__') if len(parents) == 1 else '__UNKNOWN_PARENT__'
            metadata.append(dict(row_id=row['__SOURCE_ROW_ID'], transaction_id=row['TRANSACTION_ID'], process_id=row.get('PROCESS_ID',''), message_id=row.get('MESSAGE_ID',''), family_date=dates.get(row['TRANSACTION_ID'], ''), adapterType=adapter))
        meta = pd.DataFrame(metadata,columns=['row_id','transaction_id','process_id','message_id','family_date','adapterType'])
        frame = prepare_frame(values, populations, ids, meta, d['ml_contracts'][short])
        outputs[short] = dict(frame=frame, metadata=meta, values=values, population=populations, selection_unknown=unknown, ids=ids, exact=exact, audits=audits)
    capture.assert_unchanged()
    return outputs

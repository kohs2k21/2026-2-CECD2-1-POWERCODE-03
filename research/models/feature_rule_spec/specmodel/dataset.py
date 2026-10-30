"""Explicit approved metadata capture admission. No sampling or family truncation."""
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime
import csv, json, copy, re
from .io import *
from .calculations import calc
from . import primitives
FORBIDDEN = re.compile('(^|_)(?:body|payload)(?:_|$)', re.I)

class Dataset:

    def __init__(self, path):
        self.path = Path(path).resolve()
        self.manifest_sha = sha(self.path)
        self.root = self.path.parent
        self.manifest = read(self.path)
        m = self.manifest
        if m.get('contract') != 'approved-metadata-capture1.0':
            raise InputFailure('Unsupported manifest contract')
        a = m.get('admission', {})
        if not all((a.get(k) is True for k in ['approved', 'Body_excluded', 'held_lineage_excluded', 'complete_family_capture'])):
            raise InputFailure('Explicit approved/Body/held-lineage/family-completeness admission required')
        if m.get('data_class') not in ['approved_real', 'controlled']:
            raise InputFailure('Only explicit approved_real or controlled metadata')
        for tag in m.get('lineage_tags', []):
            if re.search('data_1002|data_0930|data_1001|held|pending_approval', tag, re.I):
                raise InputFailure('Held or unresolved source lineage')
        period = m['period']
        if period.get('start_inclusive') and period.get('end_exclusive'):
            lo = period['start_inclusive']
            hi = period['end_exclusive']
            if not lo < hi:
                raise InputFailure('Invalid source period')
            if lo < '2026-10-03' and hi > '2026-09-30':
                raise InputFailure('Held9/30–10/2 source period overlaps capture')
        self.rows = {}
        tables = []
        self.source_pins = []
        self._source_paths = []
        for t in m['tables']:
            name = t['name']
            if name in self.rows:
                raise InputFailure('Duplicate table declaration')
            columns = t['columns']
            names = [x['name'] for x in columns]
            if len(set(names)) != len(names) or any((FORBIDDEN.search(n) for n in names)) or any((c.get('excluded') or c.get('scope_status') == 'excluded' for c in columns)):
                raise InputFailure('Duplicate or Body/payload column')
            p = (self.root / t['file']).resolve()
            if not p.is_file() or sha(p) != t['sha256']:
                raise InputFailure('Metadata source missing or SHA mismatch: ' + name)
            self._source_paths.append((p, t['sha256']))
            self.source_pins.append({'table': name, 'sha256': t['sha256'], 'bytes': p.stat().st_size})
            if t.get('format') == 'csv':
                with p.open(newline='') as h:
                    reader = csv.DictReader(h)
                    if not reader.fieldnames or set(reader.fieldnames) != set(names) or any((FORBIDDEN.search(n) for n in reader.fieldnames)):
                        raise InputFailure('CSV header differs from admitted metadata')
                    rows = list(reader)
            elif t.get('format') == 'jsonl':
                rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
            else:
                raise InputFailure('Only metadata CSV/JSONL supported')
            if len(rows) != t['row_count']:
                raise InputFailure('Declared row count mismatch')
            cols = {c['name']: c for c in columns}
            for row in rows:
                if set(row) != set(names):
                    raise InputFailure('Actual row columns differ from manifest')
                for (k, v) in row.items():
                    if v is None:
                        row[k] = ''
                    elif cols[k]['logical_type'] in ['string', 'category'] and (not isinstance(v, str)):
                        raise InputFailure('Relation/category lexical normalization is not implicit')
            keys = t['primary_key']
            if not keys or set(keys) - set(names):
                raise InputFailure('Complete actual row identifiers required')
            rowkeys = [tuple((x[k] for k in keys)) for x in rows]
            if any(('' in k for k in rowkeys)) or len(set(rowkeys)) != len(rowkeys):
                raise InputFailure('Output row identifiers must be nonmissing and unique')
            self.rows[name] = rows
            tables.append({'name': name, 'id': name, 'primary_key': keys, 'columns': columns, 'row_count': len(rows), 'csv_source': {'sha256': t['sha256']}})
        rels = m.get('relationships', [])
        byname = {t['name']: t for t in tables}
        if len({r['id'] for r in rels}) != len(rels):
            raise InputFailure('Duplicate relationship IDs')
        for r in rels:
            if r['from_table'] not in byname or r['to_table'] not in byname or len(r['from_columns']) != len(r['to_columns']) or (not r['from_columns']):
                raise InputFailure('Invalid declared relationship')
            for (tn, ks) in [(r['from_table'], r['from_columns']), (r['to_table'], r['to_columns'])]:
                if set(ks) - {c['name'] for c in byname[tn]['columns']}:
                    raise InputFailure('Relationship key absent from table')
        self.source_version = digest({'identity': m.get('source_identity', {}), 'source_pins': self.source_pins, 'period': period, 'admission': a, 'relationships': rels, 'source_policy': 'approved-metadata-capture1.0'})
        schema = digest({'tables': [{k: v for (k, v) in t.items() if k not in ['row_count', 'csv_source']} for t in tables], 'relationships': rels})
        inv = {'tables': tables, 'relationships': rels, 'source_version': self.source_version, 'schema_version': schema, 'scope_admission': {'admitted': True}, 'execution_limits': {'whole_scope': True, 'max_ast_nodes': 128, 'max_ast_depth': 16}}
        capture = {}
        for r in rels:
            capture[r['id']] = {'status': 'complete_for_selected_parents', 'child_count_allowed': True, 'schema_version': schema, 'relationship_definition': r, 'relationship_definition_sha': digest(r), 'parent_source_sha256': byname[r['to_table']]['csv_source']['sha256'], 'child_source_sha256': byname[r['from_table']]['csv_source']['sha256'], 'scope': 'Admitted complete captured families; not a mandatory-child or lifecycle business contract'}
        self.inventory = inv
        self.engine = calc.FullEngine(inv, self.rows, capture, self.root)
        self.independent = calc.Independent(self.rows, {t['name']: {c['name']: c for c in t['columns']} for t in tables}, rels)
        self._profile = None
        self.evidence = {}
        for item in m.get('evidence', []):
            if item['id'] in self.evidence:
                raise InputFailure('Duplicate evidence ID')
            if item.get('confirmation') not in ['document_confirmed', 'user_confirmed', 'controlled_contract']:
                raise InputFailure('Unconfirmed evidence cannot be certified')
            src = item['source']
            p = (self.root / src['file']).resolve()
            if sha(p) != src['sha256']:
                raise InputFailure('Authority source SHA mismatch')
            doc = read(p)
            value = doc
            for part in src.get('pointer', '').lstrip('/').split('/') if src.get('pointer') else []:
                part = part.replace('~1', '/').replace('~0', '~')
                value = value[int(part)] if isinstance(value, list) else value[part]
            if value != item['value']:
                raise InputFailure('Authority pointer/value mismatch')
            self.evidence[item['id']] = item
        for t in tables:
            for c in t['columns']:
                unit = c.get('unit') or {}
                if unit.get('status') == 'verified':
                    applicable = [self.evidence[k] for k in unit.get('evidence_ids', []) if k in self.evidence and self.evidence[k].get('kind') == 'unit' and ((self.evidence[k].get('table'), self.evidence[k].get('column')) == (t['name'], c['name']))]
                    if not any((e.get('unit_name', e['value'].get('name')) == unit['name'] for e in applicable)):
                        raise InputFailure('Verified unit lacks exact table/column/unit authority')
        self.constraints = []
        for e in self.evidence.values():
            if e.get('kind') == 'ddl_not_null':
                (t, c) = (e['table'], e['column'])
                if (t, c) not in self.engine.columns:
                    raise InputFailure('DDL evidence scope not in source')
                self.constraints.append({'id': e['id'], 'kind': 'ddl_not_null', 'table': t, 'column': c, 'confirmation': e['confirmation'], 'statement': e['value']})

    def assert_unchanged(self):
        if sha(self.path) != self.manifest_sha:
            raise InputFailure('Input manifest changed')
        for (path, expected) in self._source_paths:
            if sha(path) != expected:
                raise InputFailure('Source changed after admission; cached measurements cannot be reused')

    def profile(self):
        if self._profile is not None:
            return self._profile
        allkeys = {(r[t], k) for r in self.inventory['relationships'] for (t, ks) in [('from_table', 'from_columns'), ('to_table', 'to_columns')] for k in r[ks]}
        out = {}
        for (table, rows) in self.rows.items():
            values = []
            for c in self.engine.tables[table]['columns']:
                name = c['name']
                missing = lambda v: v is None or v in c.get('missing_tokens', [''])
                raw = [r[name] for r in rows]
                valid = [v for v in raw if not missing(v)]
                item = {'column': name, 'logical_type': c['logical_type'], 'rows': len(rows), 'missing': len(rows) - len(valid), 'distinct': len(set(map(str, valid))), 'unit': c.get('unit'), 'description': c.get('description', 'No confirmed business meaning supplied')}
                if (table, name) not in allkeys and name not in self.engine.tables[table]['primary_key']:
                    item['observed_raw_values'] = Counter(map(str, valid)).most_common(5)
                if c['logical_type'] in ['number', 'integer']:
                    nums = [primitives._number(v) for v in valid]
                    nums = [v for v in nums if v is not None]
                    item['observed_min'] = str(min(nums)) if nums else None
                    item['observed_max'] = str(max(nums)) if nums else None
                values.append(item)
            out[table] = values
        self._profile = {'period': self.manifest['period'], 'table_rows': {k: len(v) for (k, v) in self.rows.items()}, 'transaction_families': self.manifest.get('transaction_families'), 'metadata': out, 'observed_extrema_not_proven_bounds': True, 'full_source_pins': self.source_pins}
        return self._profile

"""Explicit same-schema metadata CSV admission, independent of archived paths."""
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import csv
import re

from ..common import InputFailure, digest, read, sha
from specmodel.engine import Engine, Independent

FORBIDDEN = re.compile(r'(^|_)(body|payload)(_|$)|^response_message$', re.I)


class Capture:
    def __init__(self, path, p):
        self.path = Path(path).resolve()
        self.manifest = m = read(self.path)
        self.manifest_sha = sha(self.path)
        d = p['definition']
        if m.get('contract') != 'shared42-capture1.0' or m.get('definition_sha') != p['definition_sha']:
            raise InputFailure('Explicit current capture/definition contract required')
        a = m.get('admission', {})
        if any(a.get(k) is not True for k in ('approved', 'Body_excluded', 'held_lineage_excluded', 'complete_family_capture', 'frozen_research_reference')):
            raise InputFailure('Capture approval/completeness/frozen-reference admission required')
        if m.get('data_class') not in ('approved_real', 'controlled'):
            raise InputFailure('Capture data class must be explicit')
        lo, hi = (m['period'][k] for k in ('start_inclusive', 'end_exclusive'))
        if not lo < hi or (lo < '2026-10-03' and hi > '2026-09-30'):
            raise InputFailure('Invalid period or held September30–October2 overlap')
        if any(re.search(r'data_1002|data_0930|data_1001|pending_approval|held', str(x), re.I) for x in m.get('lineage_tags', [])):
            raise InputFailure('Held source lineage')
        if m.get('schema_sha') != digest(d['schema']):
            raise InputFailure('Schema/type/unit/relationship mapping changed; redesign required')
        if set(m['tables']) != set('TPM'):
            raise InputFailure('Exactly three T/P/M tables required')
        self.rows, self.pins = {}, []
        tables = []
        for short, spec in d['schema']['tables'].items():
            entry = m['tables'][short]
            src = (self.path.parent / entry['file']).resolve()
            if sha(src) != entry['sha256']:
                raise InputFailure('Capture SHA mismatch: ' + short)
            with src.open(newline='', encoding='utf-8-sig') as stream:
                rd = csv.DictReader(stream)
                header = rd.fieldnames or []
                physical = {c['name'] for c in spec['columns'] if not c['name'].startswith('__')}
                allowed = physical | {'__SOURCE_ROW_ID', '__RETRY_LEXICAL'}
                if len(header) != len(set(header)) or set(header) - allowed or physical - set(header) or any(FORBIDDEN.search(x) for x in header):
                    raise InputFailure('CSV schema differs or contains forbidden/nonmetadata columns: ' + short)
                rows = []
                for ordinal, row in enumerate(rd):
                    if None in row or any(v is None for v in row.values()):
                        raise InputFailure('Malformed CSV row: ' + short)
                    row.setdefault('__SOURCE_ROW_ID', str(ordinal))
                    if short in 'PM':
                        retry = row['RETRY_COUNT']
                        if '__RETRY_LEXICAL' in row and row['__RETRY_LEXICAL'] != retry:
                            raise InputFailure('Retry relation must preserve exact source lexical value')
                        row['__RETRY_LEXICAL'] = retry
                    for c in spec['columns']:
                        raw = row[c['name']]
                        if raw == '':
                            continue
                        if c['logical_type'] in ('number', 'integer'):
                            try:
                                val = Decimal(raw)
                                if not val.is_finite() or (c['logical_type'] == 'integer' and val != val.to_integral_value()):
                                    raise InvalidOperation
                            except InvalidOperation as e:
                                raise InputFailure('Malformed numeric source: ' + short + '.' + c['name']) from e
                        if c['logical_type'] in ('datetime', 'timestamp'):
                            try:
                                datetime.fromisoformat(raw.replace('Z', '+00:00'))
                            except ValueError as e:
                                raise InputFailure('Malformed timestamp: ' + short + '.' + c['name']) from e
                    rows.append(row)
            if len(rows) != entry['row_count'] or len({r['__SOURCE_ROW_ID'] for r in rows}) != len(rows) or any(not r['__SOURCE_ROW_ID'] for r in rows):
                raise InputFailure('CSV row count or source row identity differs: ' + short)
            if short == 'T' and not rows:
                raise InputFailure('At least one transaction is required')
            self.rows[spec['name']] = rows
            self.pins.append(dict(table=short, path=str(src), sha256=entry['sha256']))
            tables.append(dict(name=spec['name'], id=spec['name'], primary_key=['__SOURCE_ROW_ID'], columns=spec['columns'], row_count=len(rows), csv_source=dict(sha256=entry['sha256'])))
        self.transaction_table = d['schema']['tables']['T']['name']
        ts = self.rows[self.transaction_table]
        counts = Counter(r['TRANSACTION_ID'] for r in ts if r['TRANSACTION_ID'])
        if any(n > 1 for n in counts.values()):
            raise InputFailure('Ambiguous duplicate transaction ID; partition capture periods explicitly')
        # Row-level missing/orphan HR evidence remains available, never discarded.
        self.family_ids = set(counts)
        for r in ts:
            if r['START_TIME'] and not lo <= r['START_TIME'] < hi:
                raise InputFailure('Transaction start outside declared capture period')
        inventory = dict(tables=tables, relationships=d['schema']['relationships'], schema_version=m['schema_sha'], source_version=digest(dict(pins=self.pins, manifest=self.manifest_sha)), scope_admission=dict(admitted=True))
        byname = {t['name']: t for t in tables}
        capture = {r['id']: dict(status='complete_for_selected_parents', child_count_allowed=True, schema_version=m['schema_sha'], relationship_definition=r, relationship_definition_sha=digest(r), parent_source_sha256=byname[r['to_table']]['csv_source']['sha256'], child_source_sha256=byname[r['from_table']]['csv_source']['sha256']) for r in inventory['relationships']}
        self.engine = Engine(inventory, self.rows, capture, self.path.parent)
        self.independent = Independent(self.rows, {t['name']: {c['name']: c for c in t['columns']} for t in tables}, inventory['relationships'])
        self.confirmed_evidence = set(m.get('confirmed_hr_evidence_ids', []))

    def assert_unchanged(self):
        if sha(self.path) != self.manifest_sha or any(sha(p['path']) != p['sha256'] for p in self.pins):
            raise InputFailure('Capture changed during execution')

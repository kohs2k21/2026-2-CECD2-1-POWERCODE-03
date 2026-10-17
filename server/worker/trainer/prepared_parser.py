"""Bounded numeric CSV parser with explicit metadata and declared lineage checks."""
from collections import Counter
import csv
from datetime import datetime
import hashlib
import io
import math
import re
import time

from trainer.prepared_contract import (MAX_BYTES, MAX_ROWS, MAX_COLUMNS, MAX_SECONDS, MIN_TRAIN_ROWS, canonical, digest, validate_manifest)


def training_eligible(row):
    return row['split'] == 'train' and row['status'] == 'S' and row['isSynthetic'] is False and \
           row['label'] != 'anomaly' and row['scenario'] is None


def parse_csv(content, manifest):
    began = time.monotonic()
    manifest = validate_manifest(manifest)
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        raise ValueError('prepared_file_size_exceeded')
    try: text = content.decode('utf-8-sig')
    except UnicodeDecodeError: raise ValueError('prepared_csv_must_be_utf8') from None
    if '\x00' in text:
        raise ValueError('prepared_invalid_csv_character')
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    try:
        header = next(reader)
        names = [item['name'] for item in manifest['features']]
        columns = manifest['columns']
        metadata_names = [name for name in columns.values() if name is not None]
        if len(header) > MAX_COLUMNS or len(header) != len(set(header)) or set(header) != set(names + metadata_names):
            raise ValueError('prepared_csv_header_mismatch')
        if [name for name in header if name in names] != names:
            raise ValueError('prepared_feature_order_mismatch')
        rows, ids = [], set()
        for number, values in enumerate(reader, 2):
            if time.monotonic() - began > MAX_SECONDS:
                raise ValueError('prepared_parse_time_exceeded')
            if len(rows) >= MAX_ROWS or len(values) != len(header):
                raise ValueError('prepared_csv_row_shape_mismatch')
            record = dict(zip(header, values))
            def meta(key):
                value = record.get(columns.get(key))
                if value is None or value == '': return None
                if len(value) > 1024 or value.strip() != value or any(ord(c) < 32 for c in value):
                    raise ValueError('prepared_invalid_metadata')
                return value
            identity = meta('rowId')
            if not identity or identity in ids:
                raise ValueError('prepared_duplicate_or_missing_row_id')
            ids.add(identity)
            split, label, status = meta('split'), meta('label') or 'unknown', meta('status')
            if split not in (None, 'train', 'validation', 'test') or label not in ('normal', 'anomaly', 'unknown'):
                raise ValueError('prepared_invalid_split_or_label')
            if status is not None and status not in ('S', 'F', 'P', 'N', 'unknown'):
                raise ValueError('prepared_invalid_status')
            synthetic = meta('isSynthetic')
            if synthetic not in (None, 'true', 'false'):
                raise ValueError('prepared_invalid_synthetic_flag')
            vector = []
            for name in names:
                value = record[name]
                if value == '' or value.strip() != value:
                    raise ValueError('prepared_missing_or_invalid_feature')
                try: feature = float(value)
                except (ValueError, OverflowError): raise ValueError('prepared_missing_or_invalid_feature') from None
                if not math.isfinite(feature):
                    raise ValueError('prepared_nonfinite_feature')
                vector.append(feature)
            occurred = meta('occurredAt')
            if occurred is not None:
                try:
                    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)', occurred):
                        raise ValueError()
                    parsed = datetime.fromisoformat(occurred.replace('Z', '+00:00'))
                    if parsed.tzinfo is None: raise ValueError()
                except ValueError: raise ValueError('prepared_ambiguous_event_time') from None
            rows.append({'rowId': identity, 'originalRowNumber': number, 'split': split,
                         'label': label, 'status': status, 'isSynthetic': None if synthetic is None else synthetic == 'true',
                         'scenario': meta('scenario'), 'originRowId': meta('originRowId'), 'groupId': meta('groupId'),
                         'occurredAt': occurred, 'features': vector, 'lineage': {}})
    except (csv.Error, StopIteration):
        raise ValueError('prepared_invalid_csv') from None
    if len(rows) != manifest['rowCount']:
        raise ValueError('prepared_row_count_mismatch')
    parents = list(range(len(rows)))
    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    def join(left, right):
        parents[find(left)] = find(right)
    identities = {row['rowId']: index for index,row in enumerate(rows)}
    origins, groups = {}, {}
    for index,row in enumerate(rows):
        for key,registry in (('originRowId',origins),('groupId',groups)):
            value = row[key]
            if value is not None: join(index,registry.setdefault(value,index))
        if row['originRowId'] in identities: join(index,identities[row['originRowId']])
    partitions = {}
    for index,row in enumerate(rows):
        if row['split'] is not None and partitions.setdefault(find(index),row['split']) != row['split']:
            raise ValueError('prepared_lineage_split_overlap')
    train_ids = sorted(row['rowId'] for row in rows if row['split'] == 'train')
    fit = manifest['preprocessing']
    if train_ids and fit['trainRowIdsHash'] is not None and fit['trainRowIdsHash'] != digest(train_ids):
        raise ValueError('prepared_fit_membership_mismatch')
    unknown = []
    if any(row['split'] is None for row in rows): unknown.append('split_unavailable')
    if any(row['originRowId'] is None or row['groupId'] is None for row in rows): unknown.append('origin_lineage_unverified')
    if fit['fitSplit'] != 'train' or fit['fitHash'] is None or fit['trainRowIdsHash'] is None: unknown.append('fit_lineage_unverified')
    if any(row['isSynthetic'] is None for row in rows): unknown.append('synthetic_eligibility_unverified')
    if any(row['status'] is None for row in rows): unknown.append('training_status_unavailable')
    if any(row['occurredAt'] is None for row in rows): unknown.append('event_time_unavailable')
    if not train_ids and fit['trainRowIdsHash'] is not None: unknown.append('external_fit_membership_unverified')
    if any(row['isSynthetic'] is True or row['scenario'] is not None for row in rows) and manifest.get('synthetic') is None:
        unknown.append('synthetic_generation_unverified')
    if (manifest.get('labelSource') or {}).get('kind', 'unknown') == 'unknown': unknown.append('label_source_unverified')
    if manifest['split']['timeBasis'] == 'occurred_at' and all(row['occurredAt'] is not None for row in rows):
        last_end = None
        for split in ('train', 'validation', 'test'):
            times = [datetime.fromisoformat(row['occurredAt'].replace('Z', '+00:00')) for row in rows if row['split'] == split]
            if times:
                if last_end is not None and min(times) <= last_end:
                    raise ValueError('prepared_time_split_overlap')
                last_end = max(times)
    eligible = sum(training_eligible(row) for row in rows)
    validation = {'parserVersion': 'prepared-csv-parser-v1', 'rowCount': len(rows),
                  'splitCounts': dict(Counter(row['split'] or 'unassigned' for row in rows)),
                  'labelCounts': dict(Counter(row['label'] for row in rows)), 'trainingEligibleCount': eligible,
                  'trainingPossible': eligible >= MIN_TRAIN_ROWS, 'trainingMinimumEligibleRows': MIN_TRAIN_ROWS,
                  'trainingReason': None if eligible >= MIN_TRAIN_ROWS else 'prepared_fit_calibration_population_insufficient',
                  'evaluationPossible': any(row['split'] in ('validation', 'test') for row in rows),
                  'operationalEligible': False, 'operationalReason': 'prepared_detector_input_not_reproducible',
                  'lineageStatus': 'unverified' if unknown else 'consistent_declared_not_independently_verified',
                  'reasonCodes': unknown, 'trainRowIdsHash': digest(train_ids),
                  'rowIdentity': 'prepared_numeric_feature_row', 'hrReason': 'raw_rule_inputs_unavailable',
                  'srReason': 'raw_rule_inputs_unavailable', 'esbLatencyReason': 'static_prepared_data_no_ESB_latency'}
    normalized = b''.join(canonical(row) + b'\n' for row in rows)
    if time.monotonic() - began > MAX_SECONDS:
        raise ValueError('prepared_parse_time_exceeded')
    return {'rows': rows, 'rowMetadata': [{key: value for key, value in row.items() if key != 'features'} for row in rows],
            'normalizedBytes': normalized, 'normalizedHash': hashlib.sha256(normalized).hexdigest(), 'validationResult': validation}

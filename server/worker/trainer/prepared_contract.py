"""Prepared CSV model-input contract; never derive raw PROCESS fields."""
from copy import deepcopy
import hashlib
import json
import math
import re
import uuid

VERSION = 'prepared-csv-v1'
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10000
MAX_FEATURES = 32
MAX_COLUMNS = 64
MAX_SECONDS = 20
MIN_TRAIN_ROWS = 625
HASH = re.compile(r'^[0-9a-f]{64}$')
NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')
METADATA = {'rowId', 'split', 'status', 'label', 'isSynthetic', 'scenario', 'originRowId', 'groupId', 'occurredAt'}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _text(value, maximum=256):
    return isinstance(value, str) and 0 < len(value) <= maximum and value.strip() == value and not any(ord(c) < 32 for c in value)


def validate_manifest(value):
    required = {'contractVersion', 'sourceId', 'sourceSchemaVersion', 'domain', 'inputStage', 'features',
                'preprocessing', 'split', 'columns', 'rowCount'}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {'synthetic', 'labelSource'}:
        raise ValueError('prepared_invalid_manifest')
    if value['contractVersion'] != VERSION or value['inputStage'] != 'model_input':
        raise ValueError('prepared_unsupported_input_stage')
    try: uuid.UUID(value['sourceId'])
    except (ValueError, TypeError, AttributeError): raise ValueError('prepared_invalid_source') from None
    if not _text(value['sourceSchemaVersion']) or not _text(value['domain']):
        raise ValueError('prepared_invalid_source_contract')
    if type(value['rowCount']) is not int or not 1 <= value['rowCount'] <= MAX_ROWS:
        raise ValueError('prepared_row_limit_exceeded')
    features = value['features']
    if not isinstance(features, list) or not 1 <= len(features) <= MAX_FEATURES:
        raise ValueError('prepared_invalid_feature_schema')
    names = []
    for feature in features:
        if not isinstance(feature, dict) or set(feature) != {'name', 'type', 'unit'} or \
                not isinstance(feature['name'], str) or not NAME.fullmatch(feature['name']) or feature['type'] != 'number' or \
                not _text(feature['unit']):
            raise ValueError('prepared_invalid_feature_schema')
        names.append(feature['name'])
    columns = value['columns']
    if len(names) != len(set(names)) or not isinstance(columns, dict) or 'rowId' not in columns or \
            set(columns) - METADATA or not _text(columns['rowId'], 128) or \
            any(item is not None and not _text(item, 128) for item in columns.values()):
        raise ValueError('prepared_invalid_columns')
    mapped = [item for item in columns.values() if item is not None]
    if any(not NAME.fullmatch(item) for item in mapped) or len(mapped) != len(set(mapped)) or set(mapped) & set(names) or len(mapped) + len(names) > MAX_COLUMNS:
        raise ValueError('prepared_metadata_feature_overlap')
    preprocessing = value['preprocessing']
    if not isinstance(preprocessing, dict) or set(preprocessing) != {'definitionHash', 'scaling', 'fitHash', 'fitSplit', 'trainRowIdsHash'} or \
            not isinstance(preprocessing['definitionHash'], str) or not HASH.fullmatch(preprocessing['definitionHash']) or \
            preprocessing['scaling'] not in ('none', 'applied') or preprocessing['fitSplit'] not in ('train', 'unknown') or \
            any(item is not None and (not isinstance(item, str) or not HASH.fullmatch(item))
                for item in (preprocessing['fitHash'], preprocessing['trainRowIdsHash'])):
        raise ValueError('prepared_invalid_preprocessing_contract')
    split = value['split']
    if not isinstance(split, dict) or set(split) != {'version', 'timeBasis'} or split['version'] != 'provided-v1' or split['timeBasis'] not in ('occurred_at', 'unspecified'):
        raise ValueError('prepared_invalid_split_contract')
    source = value.get('labelSource')
    if source is not None and (not isinstance(source, dict) or set(source) != {'kind', 'reference'} or
            source['kind'] not in ('manual', 'synthetic', 'unknown') or source['reference'] is not None and not _text(source['reference'])):
        raise ValueError('prepared_invalid_label_source')
    synthetic = value.get('synthetic')
    if synthetic is not None and (not isinstance(synthetic, dict) or set(synthetic) != {'generator', 'version', 'seed', 'intensity', 'baselineReference'} or
            any(not _text(synthetic[key]) for key in ('generator', 'version', 'baselineReference')) or
            type(synthetic['seed']) is not int or not 0 <= synthetic['seed'] <= 2147483647 or
            type(synthetic['intensity']) not in (int, float) or not math.isfinite(synthetic['intensity']) or not 0 <= synthetic['intensity'] <= 1000000):
        raise ValueError('prepared_invalid_synthetic_metadata')
    return deepcopy(value)

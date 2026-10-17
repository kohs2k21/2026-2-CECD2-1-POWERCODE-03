"""Pinned research profiles, explicitly bound by source metadata, never inferred."""
from copy import deepcopy
import hashlib
import json

VERSION = 'synthetic-profiles-v1'
PROFILES = {
    'esb-process-research-v1': {
        'profileId': 'esb-process-research-v1', 'profileVersion': '1', 'snapshotSchemaVersion': 'process-v1',
        'catalogVersion': VERSION,
        'unit': 'PROCESS', 'generationSpace': 'raw_copy_before_frozen_feature_transform',
        'scenarios': ['P01', 'P02', 'P03', 'P04'], 'severity': [1, 2, 3],
        'statusMeaning': {'S': 'successful_reference_not_verified_normal'},
        'failureSemantics': 'unconfirmed_P01_unsupported',
        'parentPolicy': 'strict_parent', 'timeUnit': 'ISO8601_milliseconds',
        'allowedChanges': {'P01': [], 'P02': ['end_time_raw'], 'P03': ['end_time_raw'], 'P04': ['total_count']},
        'delayMultipliers': [1.5, 2, 3], 'countIncrements': [1, 2, 3],
        'truthMeaning': 'controlled_injected_deviation_not_proven_customer_incident',
        'countContract': 'research_only_total_equals_success_plus_error',
        'requiredProcessFields': ['transaction_id', 'process_id', 'process_hub_id', 'retry_count',
                                  'adapter_type', 'channel_id', 'status', 'start_time_raw', 'end_time_raw',
                                  'total_count', 'success_count', 'error_count'],
        'requiredParentFields': ['transaction_id', 'process_hub_id', 'retry_count', 'start_time_raw', 'end_time_raw'],
        'provenance': 'research/evaluation/process_synthetic_lab/scenarios.py (Issue53/PR55)',
    },
    'adbench-numeric-v1': {
        'profileId': 'adbench-numeric-v1', 'profileVersion': '1', 'snapshotSchemaVersion': 'process-v1',
        'catalogVersion': VERSION,
        'unit': 'numeric_feature_row', 'generationSpace': 'frozen_transformed_model_input',
        'scenarios': ['local', 'cluster', 'global'], 'severity': [1],
        'truthMeaning': 'generator_distribution_membership_not_ESB_fault',
        'alpha': 5, 'percentage': .1, 'fitScope': 'snapshot_train_only_max_1000',
        'parentPolicy': 'not_applicable',
        'provenance': 'ADBench f7fed68dea6901fe9a81ed51b251b1d2456790cd; Issue53 adbench_probe.py',
    },
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def validate_request(value):
    fields = {'profileId', 'profileVersion', 'scenarios', 'seed', 'repetitions', 'targetRows'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('synthetic_invalid_request')
    profile = PROFILES.get(value['profileId']) if isinstance(value['profileId'], str) else None
    if profile is None or value['profileVersion'] != profile['profileVersion']:
        raise ValueError('synthetic_unsupported_profile')
    for name, lower, upper in [('seed', 0, 2147483647), ('repetitions', 1, 5), ('targetRows', 1, 100)]:
        if type(value[name]) is not int or not lower <= value[name] <= upper:
            raise ValueError('synthetic_invalid_bounds')
    scenarios = value['scenarios']
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 4 or len(scenarios) * value['repetitions'] > 20:
        raise ValueError('synthetic_invalid_bounds')
    seen = set()
    for item in scenarios:
        if not isinstance(item, dict) or set(item) != {'scenario', 'severity'}:
            raise ValueError('synthetic_invalid_scenario')
        scenario, severity = item['scenario'], item['severity']
        if not isinstance(scenario, str) or scenario not in profile['scenarios'] or type(severity) is not int or severity not in profile['severity']:
            raise ValueError('synthetic_unsupported_scenario')
        if (scenario, severity) in seen:
            raise ValueError('synthetic_duplicate_scenario')
        seen.add((scenario, severity))
    return deepcopy(profile)


def binding(profile, schema_version):
    return {'profileVersion': profile['profileVersion'], 'sourceSchemaVersion': schema_version,
            'staticSpecHash': digest(profile)}


def validate_binding(profile, schema_version, field_catalog, pinned=None):
    expected = binding(profile, schema_version)
    registered = field_catalog.get('syntheticProfiles', {}).get(profile['profileId']) if isinstance(field_catalog, dict) else None
    if registered != expected or pinned is not None and pinned != expected:
        raise ValueError('synthetic_source_profile_mismatch')

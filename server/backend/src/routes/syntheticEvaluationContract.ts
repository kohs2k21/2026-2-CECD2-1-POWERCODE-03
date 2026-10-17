import crypto from 'node:crypto';

export class SyntheticRequestError extends Error {
  constructor(readonly code: string, readonly status = 422) { super(code); }
}

export const SYNTHETIC_CATALOG_VERSION = 'synthetic-profiles-v1';
export const SYNTHETIC_PROFILES = {
  'esb-process-research-v1': {
    profileId: 'esb-process-research-v1', profileVersion: '1', snapshotSchemaVersion: 'process-v1',
    catalogVersion: SYNTHETIC_CATALOG_VERSION,
    unit: 'PROCESS', generationSpace: 'raw_copy_before_frozen_feature_transform',
    scenarios: ['P01', 'P02', 'P03', 'P04'], severity: [1, 2, 3],
    statusMeaning: { S: 'successful_reference_not_verified_normal' },
    failureSemantics: 'unconfirmed_P01_unsupported',
    parentPolicy: 'strict_parent', timeUnit: 'ISO8601_milliseconds',
    allowedChanges: { P01: [], P02: ['end_time_raw'], P03: ['end_time_raw'], P04: ['total_count'] },
    delayMultipliers: [1.5, 2, 3], countIncrements: [1, 2, 3],
    truthMeaning: 'controlled_injected_deviation_not_proven_customer_incident',
    countContract: 'research_only_total_equals_success_plus_error',
    requiredProcessFields: ['transaction_id', 'process_id', 'process_hub_id', 'retry_count',
      'adapter_type', 'channel_id', 'status', 'start_time_raw', 'end_time_raw', 'total_count', 'success_count', 'error_count'],
    requiredParentFields: ['transaction_id', 'process_hub_id', 'retry_count', 'start_time_raw', 'end_time_raw'],
    scenarioRequiredParentFields: { P03: ['interface_id'] },
    provenance: 'research/evaluation/process_synthetic_lab/scenarios.py (Issue53/PR55)',
  },
  'adbench-numeric-v1': {
    profileId: 'adbench-numeric-v1', profileVersion: '1', snapshotSchemaVersion: 'process-v1',
    catalogVersion: SYNTHETIC_CATALOG_VERSION,
    unit: 'numeric_feature_row', generationSpace: 'frozen_transformed_model_input',
    scenarios: ['local', 'cluster', 'global'], severity: [1],
    truthMeaning: 'generator_distribution_membership_not_ESB_fault',
    alpha: 5, percentage: .1, fitScope: 'snapshot_train_only_max_1000',
    parentPolicy: 'not_applicable',
    provenance: 'ADBench f7fed68dea6901fe9a81ed51b251b1d2456790cd; Issue53 adbench_probe.py',
  },
};

export type SyntheticEvaluation = {
  profileId: string; profileVersion: string; scenarios: { scenario: string; severity: number }[];
  seed: number; repetitions: number; targetRows: number;
};
export type SyntheticBinding = { profileVersion: string; sourceSchemaVersion: string; staticSpecHash: string };

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function exact(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
}
function profile(id: unknown, version: unknown) {
  if (typeof id !== 'string' || !Object.hasOwn(SYNTHETIC_PROFILES, id)) throw new SyntheticRequestError('synthetic_unsupported_profile');
  const found = SYNTHETIC_PROFILES[id as keyof typeof SYNTHETIC_PROFILES];
  if (version !== found.profileVersion) throw new SyntheticRequestError('synthetic_unsupported_profile');
  return found;
}
export function staticSpecHash(value: unknown): string {
  const canonical = (item: unknown): unknown => Array.isArray(item) ? item.map(canonical) : object(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, canonical(item[key])])) : item;
  return crypto.createHash('sha256').update(JSON.stringify(canonical(value))).digest('hex');
}

export function parseSyntheticEvaluation(value: unknown): SyntheticEvaluation {
  if (!object(value) || !exact(value, ['profileId', 'profileVersion', 'scenarios', 'seed', 'repetitions', 'targetRows'])) {
    throw new SyntheticRequestError('synthetic_invalid_request', 400);
  }
  const found = profile(value.profileId, value.profileVersion);
  for (const [key, min, max] of [['seed', 0, 2147483647], ['repetitions', 1, 5], ['targetRows', 1, 100]] as const) {
    if (typeof value[key] !== 'number' || !Number.isInteger(value[key]) || value[key] < min || value[key] > max) {
      throw new SyntheticRequestError('synthetic_invalid_bounds', 400);
    }
  }
  if (!Array.isArray(value.scenarios) || value.scenarios.length < 1 || value.scenarios.length > 4 ||
      value.scenarios.length * (value.repetitions as number) > 20) throw new SyntheticRequestError('synthetic_invalid_bounds', 400);
  const seen = new Set<string>();
  const scenarios = value.scenarios.map(item => {
    if (!object(item) || !exact(item, ['scenario', 'severity'])) throw new SyntheticRequestError('synthetic_invalid_scenario', 400);
    if (typeof item.scenario !== 'string' || !found.scenarios.includes(item.scenario) ||
        typeof item.severity !== 'number' || !Number.isInteger(item.severity) || !found.severity.includes(item.severity)) {
      throw new SyntheticRequestError('synthetic_unsupported_scenario');
    }
    if (item.scenario === 'P01') throw new SyntheticRequestError('synthetic_P01_semantics_unconfirmed');
    const key = `${item.scenario}:${item.severity}`;
    if (seen.has(key)) throw new SyntheticRequestError('synthetic_duplicate_scenario', 400);
    seen.add(key);
    return { scenario: item.scenario, severity: item.severity };
  });
  return { profileId: value.profileId as string, profileVersion: value.profileVersion as string, scenarios,
    seed: value.seed as number, repetitions: value.repetitions as number, targetRows: value.targetRows as number };
}

export function parseSyntheticRegistration(value: unknown) {
  if (!object(value) || !exact(value, ['profileId', 'profileVersion', 'sourceSchemaVersion']) ||
      typeof value.sourceSchemaVersion !== 'string' || !value.sourceSchemaVersion.trim() || value.sourceSchemaVersion.length > 128) {
    throw new SyntheticRequestError('synthetic_invalid_registration', 400);
  }
  const found = profile(value.profileId, value.profileVersion);
  return { profileId: found.profileId, binding: { profileVersion: found.profileVersion,
    sourceSchemaVersion: value.sourceSchemaVersion, staticSpecHash: staticSpecHash(found) } };
}

export function verifySyntheticBinding(request: SyntheticEvaluation, schemaVersion: string, fieldCatalog: unknown): SyntheticBinding {
  const found = profile(request.profileId, request.profileVersion);
  const bindings = object(fieldCatalog) ? fieldCatalog.syntheticProfiles : null;
  const binding = object(bindings) ? bindings[request.profileId] : null;
  const expected = { profileVersion: found.profileVersion, sourceSchemaVersion: schemaVersion, staticSpecHash: staticSpecHash(found) };
  if (!object(binding) || !exact(binding, Object.keys(expected)) || staticSpecHash(binding) !== staticSpecHash(expected)) {
    throw new SyntheticRequestError('synthetic_source_profile_mismatch', 409);
  }
  return expected;
}

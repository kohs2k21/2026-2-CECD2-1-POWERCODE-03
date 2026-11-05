/** Versioned Trainer discovery; capabilities are not training/evaluation results. */
import { validateFeatureExpression } from './trainerFeatureValidation.js';
import { METHOD_CATALOG, ML_METHODS, modelKeyOf } from '../ruleset/methodCatalog.js';
import { SYNTHETIC_PROFILES, SYNTHETIC_CATALOG_VERSION, staticSpecHash } from './syntheticEvaluationContract.js';
export const TRAINER_CONTRACT = 'trainer-service-v1';
export const EVALUATION_FAMILIES = ['rule_ml', 'synthetic', 'execution_cost', 'operational'] as const;
/**
 * 학습 제외 구간은 Trainer가 아직 적용하지 않는다(trainer-service.md 변경 제안, #113 하위). 받아 두면 제외되지 않은 채 학습이 성공하므로 지원 전에는 접수하지 않는다.
 * 지원되면 이 값만 바꾼다(접수·capabilities·화면이 함께 풀린다). 학습 기간 기준은 아래 `snapshot.timeBasis` 하나만 지원한다.
 */
export const TRAINING_EXCLUSION_SUPPORTED = false as boolean;
export const TRAINER_CAPABILITIES = {
  contractVersion: TRAINER_CONTRACT,
  methodCatalog: METHOD_CATALOG,
  models: ML_METHODS.map(method => ({ key: modelKeyOf(method), displayName: method.displayName, supported: method.executable.process })),
  evaluationFamilies: EVALUATION_FAMILIES,
  fittedFeatures: { operations: ['group_median', 'group_centered'], groupBy: ['P.adapter_type'],
    unknownGroup: 'missing', fitSplit: 'train' },
  snapshot: { timeBasis: 'persisted_at', splitVersion: 'execution-block-v1' },
  training: { excludedIntervals: TRAINING_EXCLUSION_SUPPORTED },
  syntheticProfiles: { catalogVersion: SYNTHETIC_CATALOG_VERSION,
    limits: { seedMax: 2147483647, targetRowsMax: 100, repetitionsMax: 5, scenariosMax: 4, attemptsMax: 20 },
    items: Object.values(SYNTHETIC_PROFILES).map(profile => ({ ...profile, staticSpecHash: staticSpecHash(profile),
      unsupportedScenarios: profile.profileId === 'esb-process-research-v1'
        ? [{ scenario: 'P01', reason: 'synthetic_P01_semantics_unconfirmed' }] : [] })) },
};

type Member = { name: string; position: number; expression: Record<string, unknown>; requiresFit: boolean };
export function featureSetCapability(members: Member[]): { supported: boolean; reason: string | null } {
  if (members.some(member => member.expression?.op === 'prepared_column')) return { supported: false, reason: 'prepared_dataset_path_required' };
  if (!members.length || members.length > 32 || new Set(members.map(x => x.name)).size !== members.length ||
      members.some((m, i) => m.position !== i || typeof m.name !== 'string' || m.name.trim() !== m.name ||
        !/^[a-z][a-z0-9_]{1,63}$/.test(m.name))) return { supported: false, reason: 'invalid_feature_set' };
  for (const member of members) {
    const expression = member.expression;
    try { validateFeatureExpression(expression); } catch { return { supported: false, reason: 'unsupported_feature_definition' }; }
    if (!expression || typeof expression !== 'object') return { supported: false, reason: 'invalid_feature_definition' };
    const fitted = ['group_median', 'group_centered'].includes(String(expression.op));
    if (member.requiresFit !== fitted || (fitted && expression.groupBy !== 'P.adapter_type')) {
      return { supported: false, reason: 'unsupported_fitted_feature' };
    }
    if (!['field', 'add', 'subtract', 'ratio', 'derived', 'group_median', 'group_centered'].includes(String(expression.op))) {
      return { supported: false, reason: 'unsupported_feature_operation' };
    }
  }
  return { supported: true, reason: null };
}

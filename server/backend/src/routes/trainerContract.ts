/** Versioned Trainer discovery; capabilities are not training/evaluation results. */
import { validateFeatureExpression } from './trainerFeatureValidation.js';
export const TRAINER_CONTRACT = 'trainer-service-v1';
export const EVALUATION_FAMILIES = ['rule_ml', 'synthetic', 'execution_cost', 'operational'] as const;
export const TRAINER_CAPABILITIES = {
  contractVersion: TRAINER_CONTRACT,
  models: [{ key: 'iforest', displayName: 'Isolation Forest', supported: true }],
  evaluationFamilies: EVALUATION_FAMILIES,
  fittedFeatures: { operations: ['group_median', 'group_centered'], groupBy: ['P.adapter_type'],
    unknownGroup: 'missing', fitSplit: 'train' },
  snapshot: { timeBasis: 'persisted_at', splitVersion: 'execution-block-v1' },
};

type Member = { name: string; position: number; expression: Record<string, unknown>; requiresFit: boolean };
export function featureSetCapability(members: Member[]): { supported: boolean; reason: string | null } {
  if (!members.length || members.length > 32 || new Set(members.map(x => x.name)).size !== members.length ||
      members.some((m, i) => m.position !== i || !/^[a-z][a-z0-9_]{1,63}$/.test(m.name))) return { supported: false, reason: 'invalid_feature_set' };
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

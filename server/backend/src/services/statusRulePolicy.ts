export function hasStatusRulePolicy(value: any, sourceId: string): boolean {
  const policy = value?.statusRulePolicy, mapping = policy?.mapping;
  return policy?.version === 'process-status-hr-v1' && policy.sourceId === sourceId &&
    Object.keys(policy).length === 3 && mapping !== null && typeof mapping === 'object' &&
    Object.keys(mapping).length === 4 && mapping.S === 'no_hit' && mapping.F === 'critical' &&
    mapping.P === 'warning' && mapping.N === 'warning';
}

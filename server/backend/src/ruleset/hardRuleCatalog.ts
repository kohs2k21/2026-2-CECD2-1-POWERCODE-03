/**
 * 이상 규칙(하드룰) 내장 목록. 정본은 detection-decision.md 4.3(Detector 소유)이고 rule-set.md `hardRules[]`는 그 투영이다.
 * Detector 코드에 목록 정본이 생기면 이 파일만 교체한다(값이 문서와 같은지는 tests/rule-set-contract.test.mjs가 확인).
 */
export type Severity = 'info' | 'warning' | 'critical';
export type HardRuleSeverity = Severity | { byCode: Record<string, Severity> };
export type HardRuleDefinition = {
  id: string; methodVersion: string; displayName: string; conditionText: string; severity: HardRuleSeverity;
};

export const HARD_RULES: readonly HardRuleDefinition[] = [
  {
    id: 'hard:process-status', methodVersion: 'process-status-hr-v1', displayName: '상태가 S가 아님', conditionText: 'STATUS ≠ S',
    severity: { byCode: { PROCESS_STATUS_F: 'critical', PROCESS_STATUS_P: 'warning', PROCESS_STATUS_N: 'warning' } },
  },
  {
    id: 'hard:negative-duration', methodVersion: 'negative-duration-v1', displayName: '시작 시각이 종료 시각보다 늦음',
    conditionText: 'START_TIME > END_TIME (종료 시각이 없으면 판단하지 않음)', severity: 'critical',
  },
];

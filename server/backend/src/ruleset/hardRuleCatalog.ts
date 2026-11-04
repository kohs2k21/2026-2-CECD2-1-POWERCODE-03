/**
 * 이상 규칙(하드룰) 내장 목록. 정본은 detection-decision.md 4.3(Detector 소유)이고 rule-set.md `hardRules[]`는 그 투영이다.
 * Detector 코드에 목록 정본이 생기면 이 파일만 교체한다(값이 문서와 같은지는 tests/rule-set-contract.test.mjs가 확인).
 * `editable`에 severity가 있는 규칙만 저장 시 코드별 위험도를 바꿀 수 있고, `severity`는 그 규칙의 내장 기본값이다(코드 목록의 기준).
 */
export type Severity = 'info' | 'warning' | 'critical';
export type HardRuleSeverity = Severity | { byCode: Record<string, Severity> };
export type HardRuleDefinition = {
  id: string; methodVersion: string; displayName: string; conditionText: string; severity: HardRuleSeverity;
  editable: readonly ('enabled' | 'severity')[];
};

export const HARD_RULES: readonly HardRuleDefinition[] = [
  {
    id: 'hard:process-status', methodVersion: 'process-status-hr-v2', displayName: '상태가 S가 아님', conditionText: 'STATUS ≠ S',
    severity: { byCode: { PROCESS_STATUS_F: 'critical', PROCESS_STATUS_P: 'warning', PROCESS_STATUS_Z: 'warning', PROCESS_STATUS_H: 'info', PROCESS_STATUS_N: 'info' } },
    editable: ['enabled', 'severity'],
  },
  {
    id: 'hard:negative-duration', methodVersion: 'negative-duration-v1', displayName: '시작 시각이 종료 시각보다 늦음',
    conditionText: 'START_TIME > END_TIME (종료 시각이 없으면 판단하지 않음)', severity: 'critical', editable: ['enabled'],
  },
  {
    id: 'hard:success-with-error', methodVersion: 'success-with-error-v1', displayName: '성공 상태이나 오류 건수가 기록됨',
    conditionText: 'STATUS = S, ERROR_COUNT > 0', severity: 'critical', editable: ['enabled'],
  },
  {
    id: 'hard:count-conservation', methodVersion: 'count-conservation-v1', displayName: '건수 합계 불일치',
    conditionText: 'TOTAL_COUNT ≠ SUCCESS_COUNT + ERROR_COUNT', severity: 'critical', editable: ['enabled'],
  },
];

/**
 * 이상 규칙(하드룰) 내장 목록. 정본은 detection-decision.md 4.3(Detector 소유)이고 rule-set.md `hardRules[]`는 그 투영이다.
 * Detector 코드에 목록 정본이 생기면 이 파일만 교체한다(값이 rule-set.md `hardRules[]` 예시와 같은지는 tests/rule-set-contract.test.mjs가 확인).
 * `editable`에 severity가 있는 규칙만 저장 시 위험도를 바꿀 수 있고, `severity`는 그 규칙의 내장 기본값이다(모양의 기준).
 * 끝 두 규칙은 룰 세트 v3(rule-set.md v3.5)에서 더해졌다. 내장 목록 순서 = Detector `HARD_RULES` 순서(끝에 추가).
 */
export type Severity = 'info' | 'warning' | 'critical';
/** 단일 단계, 내장 코드 전부를 담은 `{byCode}`, 또는 기본 단계가 있는 `{byCode, default}`(rule-set.md v3.5 — `hard:transaction-failed`). */
export type HardRuleSeverity = Severity | { byCode: Record<string, Severity>; default?: Severity };
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
  {
    id: 'hard:transaction-failed', methodVersion: 'transaction-failed-v1', displayName: '거래 실패',
    conditionText: '거래 STATUS = F, 응답 코드가 0000·S·없음이 아님', severity: { byCode: {}, default: 'warning' }, editable: ['enabled', 'severity'],
  },
  {
    id: 'hard:unregistered-interface', methodVersion: 'unregistered-interface-v1', displayName: '등록되지 않은 인터페이스',
    conditionText: '프로토콜 설정에 없는 인터페이스의 거래(켜진 프로토콜 항목이 1개 이상일 때만)', severity: 'critical', editable: ['enabled', 'severity'],
  },
];

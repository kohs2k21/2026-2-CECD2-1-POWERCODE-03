import React from 'react';
import type { ProcessDecision } from '../../services/api/pipeline.api';

const reasons: Record<string, string> = {
  deadline_exceeded: '설정한 판정 처리 기한을 초과했습니다.',
  worker_busy: '탐지 작업의 동시 실행 슬롯이 사용 중입니다.',
  component_failed: '판정 계산 중 오류가 발생했습니다.',
  rules_unavailable: '실행할 수 있는 Rule이 없습니다.',
  model_unavailable: '실행할 수 있는 모델 구성이 없습니다.',
  model_features_unavailable: '모델 입력 피처가 부족하거나 피처 구성이 일치하지 않습니다.',
  model_artifact_or_threshold_unavailable: '모델 파일·해시 또는 판정 임계값을 확인할 수 없습니다.',
};

function descriptions(evidence: unknown, rule: boolean): string[] {
  if (!Array.isArray(evidence)) return [];
  return [...new Set(evidence.flatMap((entry: unknown) => {
    if (!entry || typeof entry !== 'object') return [];
    const value = entry as { reason?: unknown; result?: unknown };
    if (typeof value.reason === 'string' && Object.hasOwn(reasons, value.reason)) {
      return [`${reasons[value.reason]} (${value.reason})`];
    }
    return rule && value.result === 'unavailable'
      ? ['Rule에 필요한 입력값 또는 파생변수가 없습니다.'] : [];
  }))];
}

export function DecisionDiagnostics({ decision }: { decision: ProcessDecision }) {
  const paths = [
    { label: 'Rule', status: decision.ruleStatus, details: descriptions(decision.evidence?.rules, true) },
    { label: 'ML', status: decision.mlStatus, details: descriptions(decision.evidence?.models, false) },
  ].filter(path => ['not_ready', 'failed'].includes(path.status));
  if (!paths.length) return null;
  return <div className="pipeline-decision-diagnostics">
    {paths.map(path => <small key={path.label}>{path.label}: {path.details.join(' · ') || '저장된 세부 사유가 없습니다.'}</small>)}
    {decision.evidence?.detectionPolicy && <small>판정 처리 기한: {decision.evidence.detectionPolicy.deadlineMs} ms</small>}
  </div>;
}

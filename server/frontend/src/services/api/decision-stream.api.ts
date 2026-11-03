import { buildApiUrl } from './client';
import { getStoredToken, expireSession } from '../auth/session';
import type { ProcessDecision } from './pipeline.api';

/** 중간 판정 기록 이벤트(`event: interim`, include=interims일 때만). 기록 식별·시도만 담고 스냅샷은 analysis interims API로 읽는다. */
export type InterimDecisionEvent = {
  eventKind: 'first_anomaly' | 'evidence_added' | 'superseded' | 'no_final';
  interimId: string;
  jobId: string;
  attemptNo: number | null;
  sourceId: string | null;
  processObservationId: string | null;
};
const interimKinds = ['first_anomaly', 'evidence_added', 'superseded', 'no_final'];

/**
 * 저장 판정 SSE(`GET /api/pipeline/events`). `onInterim`을 주면 `include=interims`로 중간 판정 기록 이벤트도 받는다
 * (주지 않으면 최종 판정 이벤트만 — 기존 형식 그대로).
 */
export async function consumeDecisionStream(signal: AbortSignal, after: string,
  receive: (decision: ProcessDecision, eventId: string) => void, onOpen?: () => void,
  onInterim?: (event: InterimDecisionEvent, eventId: string) => void): Promise<void> {
  const token = getStoredToken();
  if (!token) throw new Error('로그인이 필요합니다.');
  const response = await fetch(buildApiUrl('/api/pipeline/events', onInterim ? {include: 'interims'} : undefined), {
    headers: {Authorization: `Bearer ${token}`, Accept: 'text/event-stream', 'Last-Event-ID': after}, signal, redirect:'error',
  });
  if (response.status === 401 && token === getStoredToken()) expireSession();
  if (!response.ok || !response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new Error('저장된 판정 스트림에 연결하지 못했습니다.');
  }
  onOpen?.();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let pending = '';
  try {
    while (!signal.aborted) {
      const chunk = await reader.read();
      if (chunk.done) return;
      pending = (pending + decoder.decode(chunk.value, {stream: true})).replace(/\r\n/g, '\n');
      if (pending.length > 1_000_000) throw new Error('판정 이벤트 크기를 초과했습니다.');
      let boundary: number;
      while ((boundary = pending.indexOf('\n\n')) !== -1) {
        const frame = pending.slice(0,boundary); pending = pending.slice(boundary+2);
        const lines = frame.split('\n');
        const id = lines.find(line => line.startsWith('id: '))?.slice(4);
        const data = lines.filter(line => line.startsWith('data: ')).map(line => line.slice(6)).join('\n');
        if (!data) continue;
        if (onInterim && id && /^[1-9]\d{0,18}$/.test(id) && lines.includes('event: interim')) {
          const value = JSON.parse(data) as InterimDecisionEvent;
          if (!interimKinds.includes(value.eventKind) || typeof value.jobId !== 'string' || typeof value.interimId !== 'string') {
            throw new Error('판정 이벤트 검증에 실패했습니다.');
          }
          if (BigInt(id) > BigInt(after)) {onInterim(value,id);after=id;}
          continue;
        }
        if (!id || !/^[1-9]\d{0,18}$/.test(id) || !lines.includes('event: decision')) throw new Error('판정 이벤트 형식이 잘못되었습니다.');
        const value = JSON.parse(data) as ProcessDecision;
        if (typeof value.id !== 'string' || !['complete','partial','unavailable'].includes(value.status)) {
          throw new Error('판정 이벤트 검증에 실패했습니다.');
        }
        if (BigInt(id) > BigInt(after)) {receive(value,id);after=id;}
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

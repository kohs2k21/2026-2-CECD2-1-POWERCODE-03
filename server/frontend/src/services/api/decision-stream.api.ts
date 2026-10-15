import { buildApiUrl } from './client';
import { getStoredToken, expireSession } from '../auth/session';
import type { ProcessDecision } from './pipeline.api';

export async function consumeDecisionStream(signal: AbortSignal, after: string,
  receive: (decision: ProcessDecision, eventId: string) => void): Promise<void> {
  const token = getStoredToken();
  if (!token) throw new Error('로그인이 필요합니다.');
  const response = await fetch(buildApiUrl('/api/pipeline/events'), {
    headers: {Authorization: `Bearer ${token}`, Accept: 'text/event-stream', 'Last-Event-ID': after}, signal, redirect:'error',
  });
  if (response.status === 401 && token === getStoredToken()) expireSession();
  if (!response.ok || !response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new Error('저장된 판정 스트림에 연결하지 못했습니다.');
  }
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

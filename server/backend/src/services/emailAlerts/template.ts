import type { RiskLevel } from './config.js';
import type { MailMessage } from './transport.js';

/**
 * 메일 제목·본문(contracts/email-alerts.md "메일 내용" 절). 일반 텍스트 + 단순 HTML 둘 다.
 * 넣는 사실: 사유(새 사건·위험도 상승), 기준(종류·값), 위험도, 묶인 판정·거래 수, 시작 시각(KST, 판정 시각 기준이면 표시), 상세 링크, 발송 제한으로 생략된 수.
 * 넣지 않는 것: 원문 로그·BODY, 거래 ID, 서버 오류 원문, 비밀. 모든 값은 HTML escape, 제목은 줄바꿈 제거·길이 제한.
 * 문구는 사용자 승인 대상(새 문구) — 이 파일 한 곳에 모은다.
 */
export type Suppressed = { incident: number; decision: number };
export type IncidentPayload = {
  type: 'incident'; reason: 'opened' | 'risk_raised'; riskLevel: RiskLevel; incidentId: string;
  criteria: { kind: string | null; value: string | null; longValue: boolean };
  transactions: number | null; decisions: number | null; startAt: string | null; startBasis: 'process_time_raw' | 'decided_at' | 'mixed' | null;
  suppressed: Suppressed;
};
export type DecisionPayload = {
  type: 'decision'; reason: 'opened' | 'risk_raised'; riskLevel: RiskLevel; decisionId: string;
  grade: 'anomaly' | 'anomaly_sign' | null; processId: string | null; decidedAt: string | null; suppressed: Suppressed;
};
export type TestPayload = { type: 'test' };
export type DeliveryPayload = IncidentPayload | DecisionPayload | TestPayload;

const RISK_LABEL: Record<RiskLevel, string> = { critical: '위험', warning: '주의', info: '참고' };
const CRITERIA_LABEL: Record<string, string> = { response_code: '응답코드', channel_id: '채널' };
const LONG_VALUE_OMITTED = '값 생략(긴 값)';
const SUBJECT_PREFIX = '[ESB 알림]';
const FOOTER = 'ESB 이상 징후 탐지 대시보드 메일 알림 설정에 따라 보낸 메일입니다.';
/** 기준값은 payload에 이 길이까지만 둔다(8KB 상한 안, 메일 제목·본문에 충분). */
export const VALUE_MAX = 200;

const escapeHtml = (value: string) => value.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
const oneLine = (value: string) => value.replace(/[\u0000-\u001f\u007f]+/g, ' ').trim();
const clip = (value: string, max: number) => ([...value].length > max ? `${[...value].slice(0, max - 1).join('')}…` : value);
export const clipValue = (value: string | null) => (value === null ? null : clip(oneLine(value), VALUE_MAX));

/** 시각 → `YYYY-MM-DD HH:mm KST`(Asia/Seoul). 형식이 아니면 null. */
export function kst(iso: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(date).map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute} KST`;
}

const criteriaText = (criteria: IncidentPayload['criteria']) => {
  const kind = criteria.kind ? (CRITERIA_LABEL[criteria.kind] ?? criteria.kind) : '기준';
  return `${kind} ${criteria.value ? oneLine(criteria.value) : criteria.longValue ? LONG_VALUE_OMITTED : '—'}`;
};
const count = (value: number) => value.toLocaleString('ko-KR');
const suppressedLines = (suppressed: Suppressed) => [
  suppressed.incident > 0 ? `발송 제한으로 생략된 사건 ${count(suppressed.incident)}건이 있습니다.` : null,
  suppressed.decision > 0 ? `발송 제한으로 생략된 판정 ${count(suppressed.decision)}건이 있습니다.` : null,
].filter((line): line is string => line !== null);

type Body = { subject: string; lines: string[]; link: { label: string; url: string } | null; notes: string[] };

function incidentBody(payload: IncidentPayload, baseUrl: string): Body {
  const risk = RISK_LABEL[payload.riskLevel];
  const criteria = criteriaText(payload.criteria);
  const start = kst(payload.startAt);
  const basis = payload.startBasis === 'decided_at' ? ' (판정 시각 기준)' : payload.startBasis === 'mixed' ? ' (일부 판정 시각 기준)' : '';
  const sizes = [payload.decisions !== null ? `묶인 판정 ${count(payload.decisions)}건` : null, payload.transactions !== null ? `거래 ${count(payload.transactions)}건` : null]
    .filter(Boolean).join(' · ');
  return {
    subject: `${SUBJECT_PREFIX} ${risk} · 사건 · ${clip(criteria, 80)}${payload.reason === 'risk_raised' ? ' · 위험도 상승' : ''}`,
    lines: [payload.reason === 'risk_raised' ? '위험도 상승' : '새 사건', `기준: ${criteria}`, `위험도: ${risk}`, ...(sizes ? [sizes] : []),
      ...(start ? [`시작: ${start}${basis}`] : [])],
    link: { label: '사건 보기', url: `${baseUrl}/analysis/incidents/${encodeURIComponent(payload.incidentId)}` },
    notes: suppressedLines(payload.suppressed),
  };
}

function decisionBody(payload: DecisionPayload, baseUrl: string): Body {
  const risk = RISK_LABEL[payload.riskLevel];
  const process = payload.processId ? clip(oneLine(payload.processId), 80) : null;
  const decided = kst(payload.decidedAt);
  return {
    subject: `${SUBJECT_PREFIX} ${risk} · 판정${process ? ` · 프로세스 ${process}` : ''}${payload.reason === 'risk_raised' ? ' · 위험도 상승' : ''}`,
    lines: [payload.reason === 'risk_raised' ? '위험도 상승' : '새 판정', ...(payload.grade ? [`결과: ${payload.grade === 'anomaly_sign' ? '이상 징후' : '이상'}`] : []),
      `위험도: ${risk}`, ...(process ? [`프로세스: ${process}`] : []), ...(decided ? [`판정 시각: ${decided}`] : [])],
    link: { label: '판정 보기', url: `${baseUrl}/analysis/d/${encodeURIComponent(payload.decisionId)}` },
    notes: suppressedLines(payload.suppressed),
  };
}

const testBody = (): Body => ({ subject: `${SUBJECT_PREFIX} 시험 메일`, lines: ['메일 알림 시험 메일입니다.'], link: null, notes: [] });

/** 발송 1건 → 메일. messageId는 발송 id로 정한다(같은 발송을 다시 만들면 같은 값). */
export function renderMessage(payload: DeliveryPayload, { to, baseUrl, deliveryId, fromAddress }: { to: string; baseUrl: string | null; deliveryId: string; fromAddress: string }): MailMessage {
  const base = (baseUrl ?? '').replace(/\/+$/, '');
  const body = payload.type === 'incident' ? incidentBody(payload, base) : payload.type === 'decision' ? decisionBody(payload, base) : testBody();
  const subject = clip(oneLine(body.subject), 120);
  const text = [...body.lines, ...(body.link ? [`${body.link.label}: ${body.link.url}`] : []), ...(body.notes.length ? ['', ...body.notes] : []), '', '--', FOOTER].join('\n');
  const html = [
    '<div style="font-family:sans-serif;font-size:14px;line-height:1.6;color:#171717">',
    ...body.lines.map((line, index) => (index === 0 ? `<p style="margin:0 0 8px;font-weight:600">${escapeHtml(line)}</p>` : `<div>${escapeHtml(line)}</div>`)),
    ...(body.link ? [`<p style="margin:12px 0"><a href="${escapeHtml(body.link.url)}">${escapeHtml(body.link.label)}</a></p>`] : []),
    ...body.notes.map((note) => `<p style="margin:8px 0;color:#4d4d4d">${escapeHtml(note)}</p>`),
    `<p style="margin:16px 0 0;font-size:12px;color:#888888">${escapeHtml(FOOTER)}</p>`,
    '</div>',
  ].join('\n');
  const domain = fromAddress.slice(fromAddress.lastIndexOf('@') + 1);
  return { to, subject, text, html, messageId: `<${deliveryId}@${domain}>` };
}

import crypto from 'node:crypto';
import type { Pool } from 'pg';
import { resolveItem } from './analysisReadModel.js';

/**
 * 이상 분석 처리 상태·조사·조치 기록·사용자별 읽음(analysis-ui.md, 014_analysis_workflow.sql).
 * - 대상 키: detect 작업이 있으면 job_id(작업 항목과 그 최종 판정이 같은 대상), 작업 없는 이전 형식 판정이면 decision_id.
 * - 판정 값·evidence는 바꾸지 않는다. 조사·조치 기록은 확정 라벨·학습 입력이 아니다(다른 서비스 권한 없음).
 */
type Queryable = Pick<Pool, 'query'>;

export class AnalysisRequestError extends Error {
  constructor(readonly code: string, readonly status = 400) { super(code); }
}

export const WORKFLOW_STATES = ['detected', 'open', 'resolved'] as const;
export type WorkflowState = typeof WORKFLOW_STATES[number];
/** 와이어프레임 #93 dWorkflow 버튼: 감지됨 → 보류·완료, 보류 → 원래 상태로 복원·완료, 완료 → 원래 상태로 복원. */
export const WORKFLOW_TRANSITIONS: Record<WorkflowState, readonly WorkflowState[]> = {
  detected: ['open', 'resolved'],
  open: ['detected', 'resolved'],
  resolved: ['detected'],
};
export const FEEDBACK_KINDS = ['action', 'suspected-false-positive', 'insufficient-evidence'] as const;
export type FeedbackKind = typeof FEEDBACK_KINDS[number];
export const FEEDBACK_REASON_MAX = 2000;

const ISO = (column: string) => `to_char(${column} AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;

type Target = { column: 'job_id' | 'decision_id'; id: string };
async function targetOf(db: Queryable, id: string): Promise<{ itemId: string; target: Target }> {
  const item = await resolveItem(db, id);
  if (!item) throw new AnalysisRequestError('decision_not_found', 404);
  return { itemId: item.id, target: item.jobId ? { column: 'job_id', id: item.jobId } : { column: 'decision_id', id: item.decisionId } };
}

/**
 * PUT …/workflow: 현재 상태가 expectedState일 때만 허용 전환으로 바꾼다(변경 이력 1행 추가, 요청자 기록).
 * 현재 상태가 다르면 409 workflow_state_conflict, 허용되지 않는 전환이면 409 workflow_transition_not_allowed.
 * 동시 변경은 UNIQUE(대상, seq) 충돌로 한쪽만 성공한다(나머지 409 workflow_state_conflict).
 */
export async function changeWorkflow(pool: Pick<Pool, 'connect'>, id: string, actorId: string,
  request: { state: WorkflowState; expectedState: WorkflowState }) {
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const { itemId, target } = await targetOf(client, id);
    const { rows: [last] } = await client.query(`SELECT seq,to_state FROM run_decision_workflow_events
      WHERE ${target.column}=$1 ORDER BY seq DESC LIMIT 1`, [target.id]);
    const current: WorkflowState = last?.to_state ?? 'detected';
    if (current !== request.expectedState) throw new AnalysisRequestError('workflow_state_conflict', 409);
    if (!WORKFLOW_TRANSITIONS[current].includes(request.state)) throw new AnalysisRequestError('workflow_transition_not_allowed', 409);
    const { rows: [row] } = await client.query(`INSERT INTO run_decision_workflow_events(id,${target.column},seq,from_state,to_state,actor_id)
      VALUES($1,$2,$3,$4,$5,$6) RETURNING to_state AS state,${ISO('created_at')} AS "changedAt"`,
    [crypto.randomUUID(), target.id, (last?.seq ?? 0) + 1, current, request.state, actorId]);
    await client.query('COMMIT');
    return { id: itemId, workflow: row };
  } catch (error) {
    await client.query('ROLLBACK').catch(() => undefined);
    if ((error as { code?: string }).code === '23505') throw new AnalysisRequestError('workflow_state_conflict', 409);
    throw error;
  } finally {
    client.release();
  }
}

/** POST …/feedback: 조사·조치 기록 1건 추가(append-only, 요청자 기록). */
export async function addFeedback(db: Queryable, id: string, actorId: string, request: { kind: FeedbackKind; reason: string }) {
  const { itemId, target } = await targetOf(db, id);
  const { rows: [row] } = await db.query(`INSERT INTO run_decision_feedback(id,${target.column},kind,reason,actor_id)
    VALUES($1,$2,$3,$4,$5) RETURNING id,kind,reason,actor_id AS "actorId",${ISO('created_at')} AS "createdAt"`,
  [crypto.randomUUID(), target.id, request.kind, request.reason, actorId]);
  return { id: itemId, item: row };
}

/** GET …/feedback: 그 판정 항목의 조사·조치 기록(최신순). */
export async function listFeedback(db: Queryable, id: string) {
  const { itemId, target } = await targetOf(db, id);
  const { rows } = await db.query(`SELECT id,kind,reason,actor_id AS "actorId",${ISO('created_at')} AS "createdAt"
    FROM run_decision_feedback WHERE ${target.column}=$1 ORDER BY created_at DESC,id DESC`, [target.id]);
  return { id: itemId, items: rows };
}

/**
 * PUT …/read: 화면이 본 항목 시각(occurredAt)까지 읽음으로 기록(대상·사용자당 1행).
 * read_at = min(본 시각, 지금), 기존 값보다 뒤로 가지 않는다 → 화면을 연 뒤 생긴 중간 판정·최종 판정(더 늦은 occurredAt)은 미확인으로 남는다.
 */
export async function markRead(db: Queryable, id: string, actorId: string, seenOccurredAt: string) {
  const { itemId, target } = await targetOf(db, id);
  try {
    const { rows: [row] } = await db.query(`INSERT INTO run_decision_reads(${target.column},actor_id,read_at)
      VALUES($1,$2,least($3::timestamptz,clock_timestamp()))
      ON CONFLICT (${target.column},actor_id) DO UPDATE SET read_at=greatest(run_decision_reads.read_at,excluded.read_at)
      RETURNING ${ISO('read_at')} AS "readAt"`, [target.id, actorId, seenOccurredAt]);
    return { id: itemId, readAt: row.readAt };
  } catch (error) {
    // 형식 검사를 통과했지만 PostgreSQL이 해석하지 못한 시각(예: 2월 30일)은 요청 오류다.
    if (['22007', '22008'].includes((error as { code?: string }).code ?? '')) throw new AnalysisRequestError('invalid_read_request');
    throw error;
  }
}

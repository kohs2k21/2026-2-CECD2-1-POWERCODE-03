import crypto from 'node:crypto';
import type { Pool } from 'pg';
import { resolveItem } from './analysisReadModel.js';

/**
 * 이상 분석 처리 상태·조사·조치 기록(029 확인 결과·032 수정 포함)·사용자별 읽음(analysis-ui.md, 014_analysis_workflow.sql·029_feedback_outcome.sql·032_feedback_correction.sql).
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
/** 확인 결과(029, 기획 "확인 결과(실제 이상·정상·판단 불가)"): actual-anomaly(실제 이상)·normal(정상)·undetermined(판단 불가). null = 미입력. */
export const FEEDBACK_OUTCOMES = ['actual-anomaly', 'normal', 'undetermined'] as const;
export type FeedbackOutcome = typeof FEEDBACK_OUTCOMES[number];

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

/**
 * 조사·조치 기록 응답 항목(GET·POST·PUT 공통). 032 정정 사슬의 현재 판본 1행 = 기록 1건.
 * - id = 이 판본 행, originId = 최초 기록(정정 안 했으면 id와 같음, PUT 경로 키)
 * - createdAt = 최초 기록 시각(목록 위치), editedAt = 이 판본 시각(정정 안 했으면 null), actorId = 최초 작성자(수정은 작성자만 하므로 수정자와 같음)
 */
const FEEDBACK_ITEM = `f.id,coalesce(f.origin_id,f.id) AS "originId",f.kind,f.reason,f.outcome,coalesce(o.actor_id,f.actor_id) AS "actorId",
  ${ISO('coalesce(o.created_at,f.created_at)')} AS "createdAt",CASE WHEN f.origin_id IS NULL THEN NULL ELSE ${ISO('f.created_at')} END AS "editedAt"`;
const FEEDBACK_FROM = 'run_decision_feedback f LEFT JOIN run_decision_feedback o ON o.id=f.origin_id';
/** 현재 판본 = 그 행을 supersedes_id로 가리키는 행이 없는 행(032 부분 UNIQUE 색인). */
const FEEDBACK_CURRENT = 'NOT EXISTS (SELECT 1 FROM run_decision_feedback n WHERE n.supersedes_id=f.id)';

async function feedbackItem(db: Queryable, rowId: string) {
  const { rows: [row] } = await db.query(`SELECT ${FEEDBACK_ITEM} FROM ${FEEDBACK_FROM} WHERE f.id=$1`, [rowId]);
  return row;
}

/** POST …/feedback: 조사·조치 기록 1건 추가(append-only, 요청자 기록). 확인 결과는 선택(null = 미입력). */
export async function addFeedback(db: Queryable, id: string, actorId: string,
  request: { kind: FeedbackKind; reason: string; outcome: FeedbackOutcome | null }) {
  const { itemId, target } = await targetOf(db, id);
  const rowId = crypto.randomUUID();
  await db.query(`INSERT INTO run_decision_feedback(id,${target.column},kind,reason,actor_id,outcome) VALUES($1,$2,$3,$4,$5,$6)`,
    [rowId, target.id, request.kind, request.reason, actorId, request.outcome]);
  return { id: itemId, item: await feedbackItem(db, rowId) };
}

/**
 * PUT …/feedback/:feedbackId: 조사·조치 기록 수정(032). 기존 행은 고치지 않고 고친 값을 담은 정정 행을 추가한다(삭제 없음).
 * - feedbackId = 그 판정 항목의 최초 기록 id(응답 originId). 없거나 다른 판정 항목의 기록·정정 행 id면 404 feedback_not_found
 * - 최초 기록 작성자만(관리자 확인은 라우트) — 아니면 403 feedback_author_required
 * - expectedId = 화면이 본 현재 판본 id. 그 사이 다른 판본이 생겼으면 409 feedback_edit_conflict(동시 수정은 UNIQUE(supersedes_id) 충돌로 한쪽만 성공)
 */
export async function editFeedback(db: Queryable, id: string, feedbackId: string, actorId: string,
  request: { kind: FeedbackKind; reason: string; outcome: FeedbackOutcome | null; expectedId: string }) {
  const { itemId, target } = await targetOf(db, id);
  const { rows: [origin] } = await db.query(`SELECT id,actor_id FROM run_decision_feedback
    WHERE id=$1 AND ${target.column}=$2 AND origin_id IS NULL`, [feedbackId, target.id]);
  if (!origin) throw new AnalysisRequestError('feedback_not_found', 404);
  if (origin.actor_id !== actorId) throw new AnalysisRequestError('feedback_author_required', 403);
  const { rows: [current] } = await db.query(`SELECT f.id FROM run_decision_feedback f
    WHERE (f.id=$1 OR f.origin_id=$1) AND ${FEEDBACK_CURRENT}`, [origin.id]);
  if (!current || current.id !== request.expectedId) throw new AnalysisRequestError('feedback_edit_conflict', 409);
  const rowId = crypto.randomUUID();
  try {
    await db.query(`INSERT INTO run_decision_feedback(id,${target.column},kind,reason,actor_id,outcome,supersedes_id,origin_id)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, [rowId, target.id, request.kind, request.reason, actorId, request.outcome, current.id, origin.id]);
  } catch (error) {
    if ((error as { code?: string }).code === '23505') throw new AnalysisRequestError('feedback_edit_conflict', 409);
    throw error;
  }
  return { id: itemId, item: await feedbackItem(db, rowId) };
}

/** GET …/feedback: 그 판정 항목의 조사·조치 기록(현재 판본만, 최초 기록 시각 최신순 — 고쳐도 위치 그대로). */
export async function listFeedback(db: Queryable, id: string) {
  const { itemId, target } = await targetOf(db, id);
  const { rows } = await db.query(`SELECT ${FEEDBACK_ITEM} FROM ${FEEDBACK_FROM}
    WHERE f.${target.column}=$1 AND ${FEEDBACK_CURRENT}
    ORDER BY coalesce(o.created_at,f.created_at) DESC,coalesce(f.origin_id,f.id) DESC`, [target.id]);
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

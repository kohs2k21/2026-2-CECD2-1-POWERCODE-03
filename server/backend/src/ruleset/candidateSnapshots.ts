import type { Pool } from 'pg';
import { RuleSetError } from './ruleSetContract.js';

/**
 * GET /api/rule-set/candidates/snapshots — 후보 작업(`recommend_relations`)을 요청할 수 있는 저장 데이터셋(contracts/rule-candidates-ui.md 6.1절).
 * 조건은 작업 요청(enqueueCandidateJob)과 같다: `data_snapshots` `status='ready'`·`schema_version='process-v1'`. 최신 200개.
 * 평가 세트 기준 목록(`GET /api/evaluation-sets/snapshots`)은 모델 버전이 쓴 스냅샷만 보이므로 여기에는 맞지 않는다(학습이 실패해도 스냅샷은 남을 수 있음).
 * 스냅샷 목록 API가 계약에 없어 WebUI가 정한 형식이다. 새 표·migration 없음.
 */
type Queryable = Pick<Pool, 'query'>;

export const CANDIDATE_SNAPSHOT_LIMIT = 200;
const ISO = (column: string) => `to_char(${column} AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;

export function parseCandidateSnapshotQuery(query: Record<string, unknown>): void {
  if (Object.keys(query).length) throw new RuleSetError('unknown_candidate_filter');
}

export type CandidateSnapshot = { id: string; sourceId: string; createdAt: string; readyAt: string | null; from: string | null; until: string | null };

export async function readCandidateSnapshots(db: Queryable): Promise<{ items: CandidateSnapshot[] }> {
  const { rows } = await db.query<CandidateSnapshot>(`SELECT s.id::text AS id, s.source_id::text AS "sourceId", ${ISO('s.created_at')} AS "createdAt",
      ${ISO('s.ready_at')} AS "readyAt", s.selection_spec->>'from' AS "from", s.selection_spec->>'until' AS "until"
    FROM data_snapshots s WHERE s.status = 'ready' AND s.schema_version = 'process-v1'
    ORDER BY s.created_at DESC, s.id DESC LIMIT ${CANDIDATE_SNAPSHOT_LIMIT}`);
  return { items: rows };
}

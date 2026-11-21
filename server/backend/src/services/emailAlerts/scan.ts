import type { Pool, PoolClient } from 'pg';
import crypto from 'node:crypto';
import { parseGroupKey, readIncidentDetail } from '../incidentReadModel.js';
import { RANK_LEVEL, RISK_RANK } from './config.js';
import { NEW_ALERTS_PER_HOUR, SCAN_LOCK, loadSettingsRow, type SettingsRow } from './settingsStore.js';
import { clipValue, type DecisionPayload, type IncidentPayload, type Suppressed } from './template.js';

/**
 * 메일 알림 감지(contracts/email-alerts.md "감지" 절). API 주기 작업이 회차마다 한 트랜잭션으로 돈다(pg_try_advisory_xact_lock(81743, 0), 못 잡으면 건너뜀).
 * 1) 처리 경계(ops_email_alert_cursor) 다음의 최종 판정 이벤트(run_decision_events, id 순서 = 커밋 순서)를 최대 SCAN_BATCH건 읽는다.
 * 2) 규칙: 이상(status<>unavailable ∧ evidence.anomalous=true) ∧ 저장된 v4 composite.risk가 rated이고 하한 이상. 알림 패널의 판정 기준
 *    "최종(부분 포함)" + 위험도 미정 제외와 같은 판단(notificationReadModel stateCte). 위험도 미정은 보내지 않는다.
 * 3) 사건 소속 확정: 규칙을 통과한 판정은 그 원본의 사건 묶음 경계(run_incident_runs 최대 cursor_to, 끝 포함 — incidents.md 2.1)가 그 이벤트를
 *    지났을 때만 판단한다. 아직이면 거기서 멈추고 다음 회차에 다시 본다(묶음 작업이 같은 트랜잭션에서 구성원을 넣으므로 경계 안이면 소속이 정해져 있다).
 * 4) 사건 구성원이면 사건 흐름 알림(설정 notify_incidents), 아니면 묶이지 않은 판정 알림(notify_decisions, 키 job:<작업>·decision:<판정>).
 *    흐름 = 병합(양방향)·이어짐(양방향)으로 닿는 사건 전부, 흐름 id = notificationReadModel과 같은 머리. 흐름의 어느 사건이든 이미 알림 대상에 매여 있으면
 *    그 알림 대상을 쓴다(머리가 바뀌어도 새 사건으로 다시 보내지 않음).
 * 5) 보내는 때(사용자 11/21): 처음(opened) + 보낸 위험 단계보다 높아짐(risk_raised)만. 구성원이 늘기만 하면 보내지 않는다.
 * 6) 발송 제한: 새 알림(알림 대상·위험 단계) 시간당 NEW_ALERTS_PER_HOUR건. 넘으면 suppressed(발송 제한으로 생략)로 남기고,
 *    다음에 대기열에 들어가는 메일에 그동안 생략된 수를 적는다.
 * 대기 행 INSERT와 경계 이동이 같은 트랜잭션이라 재시작·동시 실행에도 같은 알림이 두 번 들어가지 않는다(UNIQUE (alert_id, risk_rank, recipient)도 막음).
 */
export const SCAN_BATCH = 500;
type Row = Record<string, any>;
const ISO = (column: string) => `to_char(${column} AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;
const RATED = (risk: string) => `coalesce(${risk}->>'status'='rated' AND ${risk}->>'level' IN ('critical','warning','info'),false)`;

const BATCH_SQL = `WITH top AS (SELECT coalesce(max(id),0) AS top FROM run_decision_events),
ev AS (
  SELECT e.id AS seq,d.id AS decision_id,d.job_id,d.status::text AS status,d.evidence,d.decided_at,d.process_observation_id,ex.source_id
  FROM run_decision_events e JOIN run_process_decisions d ON d.id=e.decision_id JOIN data_executions ex ON ex.id=d.execution_id
  WHERE e.id>$1::bigint AND e.event_kind='final' ORDER BY e.id LIMIT $2
), w AS (
  -- 원본별 사건 묶음 경계. 구성원 여부(m)와 같은 문장(같은 스냅숏)에서 읽어야 "경계 안인데 구성원이 아직 안 보임"이 생기지 않는다.
  SELECT s.source_id,(SELECT max(r.cursor_to) FROM run_incident_runs r WHERE r.source_id=s.source_id) AS w FROM (SELECT DISTINCT source_id FROM ev) s
), x AS (
  SELECT ev.*,f.anomalous,CASE WHEN f."schemaVersion"='4'::jsonb AND jsonb_typeof(f.composite)='object' THEN f.composite END AS composite
  FROM ev CROSS JOIN LATERAL jsonb_to_record(coalesce(
    jsonb_path_query_first(ev.evidence,'strict $ ? (@.type() == "object")','{}',true),'{}')) AS f(anomalous jsonb,"schemaVersion" jsonb,composite jsonb)
), r AS (
  SELECT x.seq,x.decision_id,x.job_id,x.source_id,${ISO('x.decided_at')} AS decided_at,
    coalesce(x.status<>'unavailable' AND x.anomalous='true'::jsonb,false) AS anomaly,
    CASE WHEN ${RATED("x.composite->'risk'")} THEN CASE x.composite->'risk'->>'level' WHEN 'critical' THEN 3 WHEN 'warning' THEN 2 ELSE 1 END ELSE 0 END AS rank,
    CASE WHEN x.composite->>'grade' IN ('anomaly','anomaly_sign') THEN x.composite->>'grade' END AS grade,
    m.incident_id,dp.process_id,w.w
  FROM x LEFT JOIN run_incident_members m ON m.decision_id=x.decision_id
  LEFT JOIN data_processes dp ON dp.observation_id=x.process_observation_id
  LEFT JOIN w ON w.source_id=x.source_id
)
SELECT (SELECT top::text FROM top) AS top,
  coalesce((SELECT jsonb_agg(jsonb_build_object('seq',r.seq::text,'decisionId',r.decision_id,'jobId',r.job_id,'sourceId',r.source_id,'decidedAt',r.decided_at,
    'anomaly',r.anomaly,'rank',r.rank,'grade',r.grade,'incidentId',r.incident_id,'processId',r.process_id,'boundary',r.w::text) ORDER BY r.seq) FROM r),'[]'::jsonb) AS rows`;

/** 사건 하나에서 닿는 흐름(notificationReadModel stateCte up·mroot·roots·head와 같은 걸음): 머리 id와 사건 id 전부. */
const FLOW_SQL = `WITH RECURSIVE up(id) AS (
  SELECT $1::uuid
  UNION
  SELECT n.id FROM up CROSS JOIN LATERAL (
    SELECT e.target_incident_id FROM run_incident_events e WHERE e.incident_id=up.id AND e.kind='merged_into'
    UNION ALL SELECT e.incident_id FROM run_incident_events e WHERE e.target_incident_id=up.id AND e.kind='merged_into'
    UNION ALL SELECT i.continues_from FROM run_incidents i WHERE i.id=up.id AND i.continues_from IS NOT NULL
    UNION ALL SELECT s.id FROM run_incidents s WHERE s.continues_from=up.id
  ) n(id)
), mroot(id,at) AS (
  SELECT id,id FROM up
  UNION
  SELECT m.id,e.target_incident_id FROM mroot m JOIN run_incident_events e ON e.incident_id=m.at AND e.kind='merged_into'
), roots AS (
  SELECT u.id,coalesce((SELECT m.at FROM mroot m WHERE m.id=u.id
    AND NOT EXISTS (SELECT 1 FROM run_incident_events e WHERE e.incident_id=m.at AND e.kind='merged_into') LIMIT 1),u.id) AS root
  FROM up u
)
SELECT (SELECT r.root FROM up JOIN run_incidents i ON i.id=up.id JOIN roots r ON r.id=up.id ORDER BY i.opened_at,r.root LIMIT 1) AS head,
  (SELECT array_agg(id::text) FROM up) AS ids`;

const LAST_HOUR_SQL = `SELECT count(DISTINCT (alert_id,risk_rank))::integer AS n FROM ops_email_deliveries
  WHERE kind<>'test' AND status<>'suppressed' AND created_at>clock_timestamp()-interval '1 hour'`;
const SUPPRESSED_SQL = `SELECT count(DISTINCT (alert_id,risk_rank)) FILTER (WHERE kind='incident')::integer AS incident,
    count(DISTINCT (alert_id,risk_rank)) FILTER (WHERE kind='decision')::integer AS decision
  FROM ops_email_deliveries WHERE status='suppressed'
    AND created_at>coalesce((SELECT max(created_at) FROM ops_email_deliveries WHERE kind<>'test' AND status<>'suppressed'),'-infinity'::timestamptz)`;

export type ScanResult = { status: 'skipped' | 'disabled' | 'armed' | 'completed'; processedThrough: string | null; queued: number; suppressed: number; waiting: boolean };
type Alert = { id: string; notified_rank: number };
type Notify = { kind: 'incident' | 'decision'; reason: 'opened' | 'risk_raised'; rank: number; seq: string };

export async function runScan(pool: Pool, { batch = SCAN_BATCH, perHour = NEW_ALERTS_PER_HOUR }: { batch?: number; perHour?: number } = {}): Promise<ScanResult> {
  const client: PoolClient = await pool.connect();
  const result: ScanResult = { status: 'completed', processedThrough: null, queued: 0, suppressed: 0, waiting: false };
  try {
    await client.query('BEGIN');
    const { rows: [lock] } = await client.query('SELECT pg_try_advisory_xact_lock($1,$2) AS ok', [...SCAN_LOCK]);
    if (!lock.ok) { await client.query('ROLLBACK'); return { ...result, status: 'skipped' }; }
    const settings = await loadSettingsRow(client);
    if (!settings?.enabled) { await client.query('COMMIT'); return { ...result, status: 'disabled' }; }
    const { rows: [cursor] } = await client.query('SELECT processed_through_seq::text AS seq FROM ops_email_alert_cursor WHERE id=1');
    if (!cursor) {
      // 켜진 설정인데 경계가 없으면(직접 고친 DB 등) 지금부터 본다(지난 판정은 보내지 않음).
      const { rows: [armed] } = await client.query(`INSERT INTO ops_email_alert_cursor(id,processed_through_seq,armed_at,last_tick_at)
        VALUES(1,(SELECT coalesce(max(id),0) FROM run_decision_events),clock_timestamp(),clock_timestamp()) RETURNING processed_through_seq::text AS seq`);
      await client.query('COMMIT');
      return { ...result, status: 'armed', processedThrough: armed.seq };
    }
    const { rows: [scan] } = await client.query(BATCH_SQL, [cursor.seq, batch]);
    const rows = scan.rows as Row[];
    const minRank = RISK_RANK[settings.min_risk_level];
    let through = BigInt(cursor.seq);
    for (const row of rows) {
      const seq = BigInt(row.seq);
      if (row.anomaly && row.rank >= minRank && (settings.notify_incidents || settings.notify_decisions)) {
        if (row.boundary === null || seq > BigInt(row.boundary)) { result.waiting = true; break; }
        const notified = row.incidentId
          ? (settings.notify_incidents ? await incidentAlert(client, row) : null)
          : (settings.notify_decisions ? await decisionAlert(client, row) : null);
        if (notified) {
          const status = await enqueue(client, settings, notified, row, perHour);
          if (status === 'queued') result.queued += 1; else result.suppressed += 1;
        }
      }
      through = seq;
    }
    if (!result.waiting && rows.length < batch && BigInt(scan.top) > through) through = BigInt(scan.top);
    await client.query('UPDATE ops_email_alert_cursor SET processed_through_seq=$1::bigint,last_tick_at=clock_timestamp() WHERE id=1', [through.toString()]);
    await client.query('COMMIT');
    return { ...result, processedThrough: through.toString() };
  } catch (error) {
    await client.query('ROLLBACK').catch(() => {});
    throw error;
  } finally {
    client.release();
  }
}

/** 알림 대상의 위험 단계 판단: 처음이면 opened, 보낸 단계보다 높으면 risk_raised, 그 밖은 null(마지막 순번만 갱신). */
async function decide(client: PoolClient, alert: Alert | null, create: () => Promise<Alert | null>, row: Row, kind: Notify['kind']) {
  const seq = row.seq as string, rank = row.rank as number;
  if (!alert) {
    const created = await create();
    if (created) return { alert: created, notify: { kind, reason: 'opened' as const, rank, seq } };
    return null;
  }
  if (rank > alert.notified_rank) {
    await client.query('UPDATE ops_email_alerts SET notified_rank=$2,last_seq=greatest(last_seq,$3::bigint),updated_at=clock_timestamp() WHERE id=$1', [alert.id, rank, seq]);
    return { alert, notify: { kind, reason: 'risk_raised' as const, rank, seq } };
  }
  await client.query('UPDATE ops_email_alerts SET last_seq=greatest(last_seq,$2::bigint),updated_at=clock_timestamp() WHERE id=$1', [alert.id, seq]);
  return null;
}

async function insertAlert(client: PoolClient, key: string, kind: Notify['kind'], row: Row): Promise<Alert | null> {
  const { rows: [created] } = await client.query(`INSERT INTO ops_email_alerts(id,alert_key,kind,first_seq,last_seq,notified_rank) VALUES($1,$2,$3,$4::bigint,$4::bigint,$5)
    ON CONFLICT (alert_key) DO NOTHING RETURNING id,notified_rank`, [crypto.randomUUID(), key, kind, row.seq, row.rank]);
  return (created as Alert | undefined) ?? null;
}

async function incidentAlert(client: PoolClient, row: Row) {
  const { rows: [flow] } = await client.query(FLOW_SQL, [row.incidentId]);
  const ids: string[] = flow.ids ?? [row.incidentId];
  const head: string = flow.head ?? row.incidentId;
  const { rows: [found] } = await client.query(`SELECT a.id,a.notified_rank FROM ops_email_alert_incidents m JOIN ops_email_alerts a ON a.id=m.alert_id
    WHERE m.incident_id=ANY($1::uuid[]) ORDER BY a.created_at,a.id LIMIT 1`, [ids]);
  let alert = (found as Alert | undefined) ?? null;
  const key = `incident:${head}`;
  if (!alert) {
    // 매핑 없이 같은 키가 이미 있으면(정리 사이 등) 그 알림 대상을 쓴다.
    const { rows: [byKey] } = await client.query('SELECT id,notified_rank FROM ops_email_alerts WHERE alert_key=$1', [key]);
    alert = (byKey as Alert | undefined) ?? null;
  }
  const decided = await decide(client, alert, () => insertAlert(client, key, 'incident', row), row, 'incident');
  const target = decided?.alert ?? alert;
  if (target) {
    await client.query('INSERT INTO ops_email_alert_incidents(incident_id,alert_id) SELECT unnest($1::uuid[]),$2 ON CONFLICT (incident_id) DO NOTHING', [ids, target.id]);
  }
  return decided ? { ...decided, head } : null;
}

async function decisionAlert(client: PoolClient, row: Row) {
  const key = row.jobId ? `job:${row.jobId}` : `decision:${row.decisionId}`;
  const { rows: [found] } = await client.query('SELECT id,notified_rank FROM ops_email_alerts WHERE alert_key=$1', [key]);
  return decide(client, (found as Alert | undefined) ?? null, () => insertAlert(client, key, 'decision', row), row, 'decision');
}

const basisOf = (basis: { processTimeRaw?: number; decidedAt?: number } | undefined): IncidentPayload['startBasis'] => {
  if (!basis) return null;
  if (!basis.decidedAt) return 'process_time_raw';
  if (!basis.processTimeRaw) return 'decided_at';
  return 'mixed';
};

/** 메일에 넣을 사실(대기열에 넣을 때 고정). 사건은 사건 상세와 같은 읽기(readIncidentDetail)로 기준값·건수·시작 시각을 얻는다. */
async function payloadOf(client: PoolClient, flowHead: string | undefined, notify: Notify, row: Row, suppressed: Suppressed) {
  const riskLevel = RANK_LEVEL[notify.rank];
  if (notify.kind === 'decision') {
    return { type: 'decision', reason: notify.reason, riskLevel, decisionId: row.decisionId, grade: row.grade ?? null,
      processId: clipValue(row.processId ?? null), decidedAt: row.decidedAt ?? null, suppressed } satisfies DecisionPayload;
  }
  const head = flowHead ?? row.incidentId;
  const detail = await readIncidentDetail(client, head);
  let criteria: IncidentPayload['criteria'];
  if (detail) criteria = { kind: detail.criteria.kind, value: clipValue(detail.criteria.value), longValue: detail.criteria.longValue };
  else {
    const { rows: [incident] } = await client.query('SELECT group_key FROM run_incidents WHERE id=$1', [head]);
    const parsed = parseGroupKey(incident?.group_key ?? '');
    criteria = { kind: parsed.kind, value: clipValue(parsed.value), longValue: parsed.valueHash !== null };
  }
  return { type: 'incident', reason: notify.reason, riskLevel, incidentId: detail?.id ?? head, criteria,
    transactions: detail?.counts.transactions ?? null, decisions: detail?.counts.decisions ?? null, startAt: detail?.startAt ?? null,
    startBasis: basisOf(detail?.occurredBasis), suppressed } satisfies IncidentPayload;
}

/** 받는 사람마다 1행. 시간당 상한 안이면 queued, 넘으면 suppressed. 반환 = 그 상태. */
async function enqueue(client: PoolClient, settings: SettingsRow, notified: { alert: Alert; notify: Notify; head?: string }, row: Row, perHour: number) {
  const { alert, notify } = notified;
  const { rows: [used] } = await client.query(LAST_HOUR_SQL);
  const status = used.n < perHour ? 'queued' : 'suppressed';
  let payload: Record<string, unknown>;
  if (status === 'queued') {
    const { rows: [skipped] } = await client.query(SUPPRESSED_SQL);
    payload = await payloadOf(client, notified.head, notify, row, { incident: skipped.incident, decision: skipped.decision });
  } else {
    payload = { type: notify.kind, reason: notify.reason, riskLevel: RANK_LEVEL[notify.rank],
      ...(notify.kind === 'incident' ? { incidentId: notified.head } : { decisionId: row.decisionId }) };
  }
  for (const recipient of settings.recipients) {
    await client.query(`INSERT INTO ops_email_deliveries(id,alert_id,kind,reason,risk_rank,trigger_seq,recipient,payload,status,next_attempt_at,finished_at)
      VALUES($1,$2,$3,$4,$5,$6::bigint,$7,$8::jsonb,$9,CASE WHEN $9='queued' THEN clock_timestamp() END,CASE WHEN $9='suppressed' THEN clock_timestamp() END)
      ON CONFLICT (alert_id,risk_rank,recipient) DO NOTHING`,
    [crypto.randomUUID(), alert.id, notify.kind, notify.reason, notify.rank, notify.seq, recipient, JSON.stringify(payload), status]);
  }
  return status;
}

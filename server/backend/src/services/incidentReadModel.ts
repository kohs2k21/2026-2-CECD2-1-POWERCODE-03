import crypto from 'node:crypto';
import type { Pool } from 'pg';
import { GRAPH_ABSENT_SQL } from './processGraph.js';

/**
 * 사건 보기(층 C, contracts/incidents-ui.md) 읽기. 019 표 4개는 INSERT만 되므로 화면 값은 모두 읽을 때 계산한다.
 * - 읽기 순서: 병합(merged_into)을 먼저 풀어 "묶음"(병합 뿌리 = 구간)을 만들고, 그다음 continues_from으로 구간을 이어 "흐름" 하나로 보인다.
 *   묶음 안 모든 사건의 continues_from을 본다(Detector incidents.md 8절).
 * - 같은 원본·같은 criteria_version·같은 group_key 일관성은 Detector 책임(DB가 강제하지 않음). 어긋난 연결은 쓰지 않고 흐름에 표시(flags)한다.
 * - 건수는 거래 기준(사용자 11/12): 구성원 판정 → 판정 PROCESS 관측 → transaction_id 문자열, 서로 다른 거래 수. 판정 수는 보조.
 * - 해제(Detector incidents.md 8절 합의): 구성원 판정의 실행(execution_id)에서 최신 판정 등급이 더는 이상·이상 징후(anomaly·anomaly_sign)가 아니면 해제.
 *   최신 판정 등급을 모르면(null: v3 evidence·형식 오류)도 이상·이상 징후가 아니므로 해제이고 unknownGrade로 따로 센다(Detector active_execution_count와 같음). 표는 바꾸지 않는다.
 * - 구성원 시각 출처(Detector 3절·8절): occurred_basis가 decided_at이면 그 시각은 원천 발생 시각이 아니라 판정 시각이다. 흐름·구간·거래 줄에 출처 수·출처를 돌려준다.
 * - 긴 기준값(Detector 4.2): group_key 값이 `sha256:<hex>`이면 원래 값은 저장돼 있지 않다. 구성원 판정의 원천 행에서 읽어 해시가 맞는 값만 보인다(없으면 value null).
 * - 끝 시각 = 구성원 occurred_at 최댓값(해제 포함). 사건 묶음은 같은 시간창·같은 기준값의 상관관계이며 인과가 아니다.
 */
type Queryable = Pick<Pool, 'query'>;

export const INCIDENT_SCAN_LIMIT = 50_000;
/** 상세·구성원: 요청 사건에서 병합·이어짐을 따라 읽는 사건 수 상한(넘으면 truncated=true, 흐름이 일부일 수 있음). */
export const INCIDENT_CHAIN_LIMIT = 10_000;
export const INCIDENT_LIST_LIMITS = { default: 20, max: 100 } as const;
export const INCIDENT_MEMBER_LIMITS = { default: 50, max: 200 } as const;
/** 목록 최소 거래 수 필터(minTransactions) 범위. 1 = 거르지 않음(기본). */
export const INCIDENT_MIN_TRANSACTIONS = { default: 1, max: 1000 } as const;
/** 거래 1건 안에 돌려주는 구성원 판정 상한(넘으면 decisionsTruncated). */
export const INCIDENT_DECISIONS_PER_TRANSACTION = 50;
/** 화면이 아는 기준 키(Detector incidents.md 4.2 우선순위). 그 밖 키 이름은 kind 그대로 돌려준다. */
export const INCIDENT_CRITERIA_KINDS = ['response_code', 'channel_id'] as const;
/**
 * 흐름 flags 고정 어휘(순서 고정, contracts 2절). 쓰지 않은 연결은 원인별로 남긴다.
 * - merge_mismatch: 병합 대상의 원본·기준 버전·group_key가 다름 / merge_cycle: 병합끼리 고리(자기 자신 포함) / merge_target_missing: 대상 행 없음
 * - continuation_mismatch·continuation_within_segment(같은 구간으로 풀림)·continuation_target_missing: 이어짐 쪽 같은 원인
 * - 읽기 상한 때문에 대상이 읽은 범위 밖이면 *_target_missing이 아니라 흐름 truncated로 표시한다(대상은 FK로 존재)
 */
export const INCIDENT_FLAGS = [
  'merge_mismatch', 'merge_cycle', 'merge_target_missing',
  'continuation_mismatch', 'continuation_within_segment', 'continuation_target_missing',
  'continuation_branched', 'continuation_cycle', 'member_source_mismatch',
] as const;
export type IncidentFlag = typeof INCIDENT_FLAGS[number];

const ISO = `'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'`;
const iso = (column: string) => `to_char(${column} AT TIME ZONE 'UTC',${ISO})`;
/** 저장된 v4 composite.grade만(analysisReadModel ITEMS_CTE와 같은 조건). v3·형식 오류 = null(등급 모름). */
const GRADE_SQL = (d: string) => `CASE WHEN ${d}.evidence->'schemaVersion'='4'::jsonb AND jsonb_typeof(${d}.evidence->'composite')='object'
  AND ${d}.evidence->'composite'->>'grade' IN ('anomaly','anomaly_sign','normal','undetermined') THEN ${d}.evidence->'composite'->>'grade' END`;
/** graph_value_absent(process-graph.md): NULL 또는 ASCII 공백·탭·CR·LF를 앞뒤에서 지운 값이 ""·"\N". */
const ABSENT_SQL = GRAPH_ABSENT_SQL;

/** 사건 골격(구성원·criteria 문서 제외 — criteria는 상세에서 흐름 머리 1건만 읽는다). */
export type IncidentSkeleton = {
  id: string;
  sourceId: string;
  criteriaVersion: string;
  groupKey: string;
  timeBasis: string;
  openedAt: string;
  continuesFrom: string | null;
  mergedInto: string | null;
  lastMemberAt: string | null;
};
export type IncidentSegment = {
  id: string;
  openedAt: string;
  endAt: string;
  incidentIds: string[];
  mergedIncidentIds: string[];
  continuesFrom: string[];
};
export type IncidentFlow = {
  id: string;
  sourceId: string;
  /** value: 원래 기준값. 긴 값이면 원천 행에서 찾은 값(못 찾으면 null), longValue = group_key에 해시만 있음. */
  criteria: { kind: string | null; value: string | null; longValue: boolean; groupKey: string; version: string; timeBasis: string };
  startAt: string;
  endAt: string;
  segments: IncidentSegment[];
  flags: IncidentFlag[];
  /** 흐름의 병합·이어짐 대상이 읽은 범위 밖(읽기 상한)이라 흐름이 일부만일 수 있음. */
  truncated: boolean;
};

/** 긴 값 group_key의 값 부분(Detector incidents.md 4.2 `GROUP_KEY_HASH_PREFIX`): 이스케이프한 짧은 값에는 ':'가 남지 않아 겹치지 않는다. */
const LONG_VALUE = /^sha256:([0-9a-f]{64})$/;
/**
 * group_key `<키 이름>:<이스케이프한 값>`(Detector incidents.md 4.2) → kind·원래 값. 쪼개는 곳은 이 API 한 곳이고 응답 소비자는 kind·value를 쓴다.
 * 긴 값(`<키 이름>:sha256:<hex>`)은 원래 값을 알 수 없으므로 value null + valueHash(원천 행에서 찾을 때 대조용, 응답에는 내보내지 않음).
 */
export const parseGroupKey = (groupKey: string): { kind: string | null; value: string | null; valueHash: string | null } => {
  const at = groupKey.indexOf(':');
  if (at <= 0) return { kind: null, value: groupKey, valueHash: null };
  const raw = groupKey.slice(at + 1);
  const hashed = LONG_VALUE.exec(raw);
  if (hashed) return { kind: groupKey.slice(0, at), value: null, valueHash: hashed[1] };
  return { kind: groupKey.slice(0, at), value: raw.replace(/%3A/g, ':').replace(/%25/g, '%'), valueHash: null };
};

const byTimeThenId = (a: { at: string; id: string }, b: { at: string; id: string }) =>
  a.at < b.at ? -1 : a.at > b.at ? 1 : a.id < b.id ? -1 : a.id > b.id ? 1 : 0;

/**
 * 순수 함수: 사건 골격 행 → 흐름 목록(시작 시각 오름차순). 시험 대상.
 * 1) 병합: 대상이 읽은 행에 있고 같은 원본·기준 버전·group_key이며 자기 자신이 아니면 유효. 유효한 병합끼리 고리를 이루면 그 고리의 병합은 모두 버린다.
 *    뿌리 = 유효한 병합을 끝까지 따라간 사건. 묶음(구간) = 같은 뿌리의 사건들.
 * 2) 이어짐: 묶음 안 모든 사건의 continues_from을 같은 조건으로 확인해 (앞 묶음 → 이 묶음) 간선을 만든다. 같은 묶음끼리는 버린다.
 * 3) 흐름 = 이어짐 간선으로 연결된 묶음들(방향 무시 연결 요소). 앞·뒤 구간이 2개 이상이면 continuation_branched, 방향 고리면 continuation_cycle.
 * partial = 읽기 상한에 걸려 행이 일부만 있음. 이때 대상이 읽은 행에 없는 연결은 *_target_missing으로 표시하지 않고(019 FK로 대상은 있음)
 * 그 연결을 가진 흐름을 truncated로 표시한다(그 연결은 쓰지 않음).
 */
export function buildIncidentFlows(rows: IncidentSkeleton[], { partial = false }: { partial?: boolean } = {}): IncidentFlow[] {
  const byId = new Map(rows.map((row) => [row.id, row]));
  const consistent = (a: IncidentSkeleton, b: IncidentSkeleton) =>
    a.sourceId === b.sourceId && a.criteriaVersion === b.criteriaVersion && a.groupKey === b.groupKey;
  const flagged = new Map<string, Set<IncidentFlag>>();
  const flag = (incidentId: string, value: IncidentFlag) => {
    if (!flagged.has(incidentId)) flagged.set(incidentId, new Set());
    flagged.get(incidentId)!.add(value);
  };
  /** 대상이 읽은 범위 밖(partial)인 연결을 가진 사건. */
  const beyond = new Set<string>();
  const missing = (incidentId: string, value: IncidentFlag) => (partial ? beyond.add(incidentId) : flag(incidentId, value));

  const target = new Map<string, string>();
  for (const row of rows) {
    if (row.mergedInto === null) continue;
    const to = byId.get(row.mergedInto);
    if (!to) missing(row.id, 'merge_target_missing');
    else if (to.id === row.id) flag(row.id, 'merge_cycle');
    else if (!consistent(row, to)) flag(row.id, 'merge_mismatch');
    else target.set(row.id, to.id);
  }
  // 병합 고리: 진출 차수 ≤ 1이므로 경로를 따라가다 현재 경로의 사건을 다시 만나면 그 뒤가 고리다.
  const done = new Set<string>();
  for (const row of rows) {
    const path: string[] = [];
    const onPath = new Map<string, number>();
    let at: string | undefined = row.id;
    while (at !== undefined && !done.has(at) && !onPath.has(at)) {
      onPath.set(at, path.length);
      path.push(at);
      at = target.get(at);
    }
    if (at !== undefined && onPath.has(at)) {
      for (const member of path.slice(onPath.get(at)!)) { target.delete(member); flag(member, 'merge_cycle'); }
    }
    for (const member of path) done.add(member);
  }
  const rootOf = new Map<string, string>();
  const root = (id: string): string => {
    const known = rootOf.get(id);
    if (known) return known;
    const next = target.get(id);
    const value = next === undefined ? id : root(next);
    rootOf.set(id, value);
    return value;
  };

  const groups = new Map<string, IncidentSkeleton[]>();
  for (const row of rows) {
    const key = root(row.id);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(row);
  }
  const preds = new Map<string, Set<string>>(), succs = new Map<string, Set<string>>();
  for (const key of groups.keys()) { preds.set(key, new Set()); succs.set(key, new Set()); }
  for (const row of rows) {
    if (row.continuesFrom === null) continue;
    const from = byId.get(row.continuesFrom);
    if (!from) { missing(row.id, 'continuation_target_missing'); continue; }
    if (!consistent(row, from)) { flag(row.id, 'continuation_mismatch'); continue; }
    if (root(from.id) === root(row.id)) { flag(row.id, 'continuation_within_segment'); continue; }
    preds.get(root(row.id))!.add(root(from.id));
    succs.get(root(from.id))!.add(root(row.id));
  }

  // 연결 요소(방향 무시).
  const component = new Map<string, string>();
  const flows: IncidentFlow[] = [];
  for (const start of groups.keys()) {
    if (component.has(start)) continue;
    const members: string[] = [];
    const stack = [start];
    component.set(start, start);
    while (stack.length) {
      const at = stack.pop()!;
      members.push(at);
      for (const next of [...preds.get(at)!, ...succs.get(at)!]) {
        if (!component.has(next)) { component.set(next, start); stack.push(next); }
      }
    }
    const segments: IncidentSegment[] = members.map((key) => {
      const incidents = groups.get(key)!;
      const openedAt = incidents.map((i) => i.openedAt).sort()[0];
      const ends = incidents.map((i) => i.lastMemberAt).filter((v): v is string => v !== null).sort();
      const endAt = ends.length && ends[ends.length - 1] > openedAt ? ends[ends.length - 1] : openedAt;
      const ids = incidents.map((i) => i.id).sort();
      return { id: key, openedAt, endAt, incidentIds: ids, mergedIncidentIds: ids.filter((id) => id !== key),
        continuesFrom: [...preds.get(key)!].sort() };
    }).sort((a, b) => byTimeThenId({ at: a.openedAt, id: a.id }, { at: b.openedAt, id: b.id }));
    const flags = new Set<IncidentFlag>();
    for (const segment of segments) for (const id of segment.incidentIds) for (const value of flagged.get(id) ?? []) flags.add(value);
    if (segments.some((s) => preds.get(s.id)!.size > 1 || succs.get(s.id)!.size > 1)) flags.add('continuation_branched');
    // 방향 고리(구간끼리 앞뒤가 돌아옴): 요소 안에서 진입 차수 0부터 지워 나가 남는 구간이 있으면 고리.
    const indegree = new Map(segments.map((s) => [s.id, preds.get(s.id)!.size]));
    const queue = segments.filter((s) => indegree.get(s.id) === 0).map((s) => s.id);
    let removed = 0;
    while (queue.length) {
      const at = queue.pop()!;
      removed += 1;
      for (const next of succs.get(at)!) {
        indegree.set(next, indegree.get(next)! - 1);
        if (indegree.get(next) === 0) queue.push(next);
      }
    }
    if (removed < segments.length) flags.add('continuation_cycle');
    const head = byId.get(segments[0].id)!;
    const { kind, value, valueHash } = parseGroupKey(head.groupKey);
    flows.push({
      id: segments[0].id,
      sourceId: head.sourceId,
      criteria: { kind, value, longValue: valueHash !== null, groupKey: head.groupKey, version: head.criteriaVersion, timeBasis: head.timeBasis },
      startAt: segments[0].openedAt,
      endAt: segments.map((s) => s.endAt).sort().at(-1)!,
      segments,
      flags: INCIDENT_FLAGS.filter((value) => flags.has(value)),
      truncated: segments.some((s) => s.incidentIds.some((id) => beyond.has(id))),
    });
  }
  return flows.sort((a, b) => byTimeThenId({ at: a.startAt, id: a.id }, { at: b.startAt, id: b.id }));
}

const SKELETON_SELECT = `SELECT i.id,i.source_id AS "sourceId",i.criteria_version AS "criteriaVersion",
      i.group_key AS "groupKey",i.time_basis AS "timeBasis",${iso('i.opened_at')} AS "openedAt",i.continues_from AS "continuesFrom",
      e.target_incident_id AS "mergedInto",${iso('mx.last')} AS "lastMemberAt"
    FROM run_incidents i
    LEFT JOIN run_incident_events e ON e.incident_id=i.id AND e.kind='merged_into'
    LEFT JOIN LATERAL (SELECT max(m.occurred_at) AS last FROM run_incident_members m WHERE m.incident_id=i.id) mx ON true`;

/**
 * 원본 1개의 사건 골격. 최근 opened_at부터 INCIDENT_SCAN_LIMIT건(인덱스 run_incidents_recent_idx (source_id, opened_at DESC, id DESC)).
 * 넘으면 truncated=true(오래된 사건은 흐름 계산에서 빠짐). criteria 문서(jsonb)는 읽지 않는다.
 */
export async function loadSkeleton(db: Queryable, sourceId: string, limit = INCIDENT_SCAN_LIMIT) {
  const { rows } = await db.query(`${SKELETON_SELECT}
    WHERE i.source_id=$1
    ORDER BY i.opened_at DESC,i.id DESC LIMIT $2`, [sourceId, limit + 1]);
  return { rows: rows.slice(0, limit) as IncidentSkeleton[], truncated: rows.length > limit };
}

/** 사건이 있는 원본 목록(인덱스 앞 열 source_id를 건너뛰며 읽음). 원본을 지정하지 않은 목록 요청만 쓴다(고객 1 = 원본 1이라 보통 1개). */
async function incidentSources(db: Queryable): Promise<string[]> {
  const { rows } = await db.query(`WITH RECURSIVE s(source_id) AS (
      (SELECT source_id FROM run_incidents ORDER BY source_id LIMIT 1)
      UNION ALL
      SELECT (SELECT i.source_id FROM run_incidents i WHERE i.source_id>s.source_id ORDER BY i.source_id LIMIT 1) FROM s WHERE s.source_id IS NOT NULL
    ) SELECT source_id::text AS id FROM s WHERE source_id IS NOT NULL`);
  return rows.map((row) => row.id as string);
}

/**
 * 구성원 기본 CTE: $1 사건 id[], $2 구간 id[], $3 흐름 id[](같은 길이). 열: flow_id, segment_id, incident_id, decision_id, occurred_at,
 * occurred_basis, execution_id, transaction_id, process_id, same_source, tx_absent. 판정 evidence(jsonb)는 읽지 않는다.
 */
const MEMBER_BASE_CTE = `map AS (
  SELECT * FROM unnest($1::uuid[],$2::uuid[],$3::uuid[]) AS x(incident_id,segment_id,flow_id)
), mem AS (
  SELECT map.flow_id,map.segment_id,m.incident_id,m.decision_id,m.occurred_at,m.occurred_basis,d.execution_id,
    p.transaction_id,p.process_id,(o.source_id=i.source_id) AS same_source,${ABSENT_SQL('p.transaction_id')} AS tx_absent
  FROM map JOIN run_incident_members m ON m.incident_id=map.incident_id
  JOIN run_incidents i ON i.id=m.incident_id
  JOIN run_process_decisions d ON d.id=m.decision_id
  JOIN data_observations o ON o.id=d.process_observation_id
  LEFT JOIN data_processes p ON p.observation_id=d.process_observation_id
)`;
/**
 * 실행별 최신 판정(rows CTE 이름의 execution_id만): PARTITION BY execution_id ORDER BY 관측 revision_no DESC NULLS LAST, decided_at DESC,
 * event_sequence DESC(Detector 8절 합의). 해제 = 최신 등급이 이상·이상 징후가 아님(normal·undetermined·등급 모름 null).
 */
const LATEST_CTE = (rows: string) => `latest AS (
  SELECT execution_id,id AS latest_id,grade AS latest_grade FROM (
    SELECT d.execution_id,d.id,${GRADE_SQL('d')} AS grade,
      row_number() OVER (PARTITION BY d.execution_id ORDER BY o.revision_no DESC NULLS LAST,d.decided_at DESC,d.event_sequence DESC) AS n
    FROM run_process_decisions d JOIN data_observations o ON o.id=d.process_observation_id
    WHERE d.execution_id IN (SELECT DISTINCT execution_id FROM ${rows})) r WHERE n=1
)`;
/** Detector incidents.md 8절·active_execution_count: 최신 등급이 ELIGIBLE_GRADES(anomaly·anomaly_sign)가 아니면 해제(null 포함). */
const RELEASED_SQL = (grade: string) => `coalesce(${grade} NOT IN ('anomaly','anomaly_sign'),true)`;

export type IncidentCounts = {
  transactions: number;
  releasedTransactions: number;
  decisions: number;
  releasedDecisions: number;
  missingTransactionId: number;
  /** 해제된 판정 중 실행의 최신 판정 등급을 모름(v3·형식 오류)이라 해제된 수(releasedDecisions에 포함, Detector 8절). */
  unknownGrade: number;
};
/** 구성원 시각 출처별 판정 수(해제 포함 — 시작·끝 시각이 해제 구성원도 포함하므로). Detector 3절 occurred_basis. */
export type IncidentOccurredBasis = { processTimeRaw: number; decidedAt: number };
const zeroCounts = (): IncidentCounts => ({ transactions: 0, releasedTransactions: 0, decisions: 0, releasedDecisions: 0, missingTransactionId: 0,
  unknownGrade: 0 });
const zeroBasis = (): IncidentOccurredBasis => ({ processTimeRaw: 0, decidedAt: 0 });

const mapping = (flows: IncidentFlow[]) => {
  const incidents: string[] = [], segments: string[] = [], owners: string[] = [];
  for (const flow of flows) for (const segment of flow.segments) for (const id of segment.incidentIds) {
    incidents.push(id); segments.push(segment.id); owners.push(flow.id);
  }
  return [incidents, segments, owners];
};

type CountEntry = { counts: IncidentCounts; basis: IncidentOccurredBasis };
/**
 * 흐름·구간별 건수. 원본이 사건과 다른 구성원 판정은 세지 않고 member_source_mismatch로 표시한다.
 * 시각 출처 수(basis)는 시작·끝 시각을 만든 구성원 전부(해제·다른 원본 포함, 골격 lastMemberAt과 같은 범위)로 센다.
 */
async function loadCounts(db: Queryable, flows: IncidentFlow[]) {
  const result = new Map<string, CountEntry & { mismatch: boolean; segments: Map<string, CountEntry> }>();
  const empty = (): CountEntry => ({ counts: zeroCounts(), basis: zeroBasis() });
  for (const flow of flows) result.set(flow.id, { ...empty(), mismatch: false, segments: new Map(flow.segments.map((s) => [s.id, empty()])) });
  if (!flows.length) return result;
  const { rows } = await db.query(`WITH ${MEMBER_BASE_CTE}, ${LATEST_CTE('mem')}, x AS (
      SELECT mem.*,NOT ${RELEASED_SQL('l.latest_grade')} AS active,(l.latest_grade IS NULL) AS unknown_grade
      FROM mem LEFT JOIN latest l USING (execution_id)
    )
    SELECT flow_id::text AS flow,segment_id::text AS segment,
      count(DISTINCT transaction_id) FILTER (WHERE same_source AND active AND NOT tx_absent)::integer AS transactions,
      count(DISTINCT transaction_id) FILTER (WHERE same_source AND NOT tx_absent)::integer AS "allTransactions",
      count(*) FILTER (WHERE same_source AND active)::integer AS decisions,
      count(*) FILTER (WHERE same_source AND NOT active)::integer AS "releasedDecisions",
      count(*) FILTER (WHERE same_source AND active AND tx_absent)::integer AS "missingTransactionId",
      count(*) FILTER (WHERE same_source AND NOT active AND unknown_grade)::integer AS "unknownGrade",
      count(*) FILTER (WHERE occurred_basis='process_time_raw')::integer AS "basisProcessTime",
      count(*) FILTER (WHERE occurred_basis='decided_at')::integer AS "basisDecidedAt",
      count(*) FILTER (WHERE NOT same_source)::integer AS mismatch
    FROM x GROUP BY GROUPING SETS ((flow_id),(flow_id,segment_id))`, mapping(flows));
  for (const row of rows) {
    const counts: IncidentCounts = { transactions: row.transactions, releasedTransactions: row.allTransactions - row.transactions,
      decisions: row.decisions, releasedDecisions: row.releasedDecisions, missingTransactionId: row.missingTransactionId,
      unknownGrade: row.unknownGrade };
    const basis: IncidentOccurredBasis = { processTimeRaw: row.basisProcessTime, decidedAt: row.basisDecidedAt };
    const entry = result.get(row.flow)!;
    if (row.segment === null) { entry.counts = counts; entry.basis = basis; entry.mismatch = row.mismatch > 0; } else entry.segments.set(row.segment, { counts, basis });
  }
  return result;
}

/**
 * 흐름별 해제되지 않은 서로 다른 거래 ID 수(거래 ID 없음·다른 원본 판정 제외). 목록 minTransactions 필터용.
 * 거래는 구성원 판정 중 하나라도 실행의 최신 판정이 이상·이상 징후면 센다(loadCounts transactions와 같은 규칙, Detector 8절 RELEASED_SQL).
 */
async function loadTransactionTotals(db: Queryable, flows: IncidentFlow[]) {
  const totals = new Map<string, number>(flows.map((flow) => [flow.id, 0]));
  if (!flows.length) return totals;
  const { rows } = await db.query(`WITH ${MEMBER_BASE_CTE}, ${LATEST_CTE('mem')}
    SELECT mem.flow_id::text AS flow,
      count(DISTINCT mem.transaction_id) FILTER (WHERE mem.same_source AND NOT mem.tx_absent AND NOT ${RELEASED_SQL('l.latest_grade')})::integer AS n
    FROM mem LEFT JOIN latest l USING (execution_id) GROUP BY mem.flow_id`, mapping(flows));
  for (const row of rows) totals.set(row.flow, row.n);
  return totals;
}

/** 긴 기준값 찾기: 흐름마다 앞 사건 LONG_VALUE_INCIDENTS개, 사건마다 첫 구성원 LONG_VALUE_MEMBERS건만 본다(싸게, 해시가 맞는 첫 값). */
export const LONG_VALUE_INCIDENTS = 20;
export const LONG_VALUE_MEMBERS = 3;
export const sha256Hex = (value: string) => crypto.createHash('sha256').update(value, 'utf8').digest('hex');

/**
 * 긴 값 흐름(group_key `<키>:sha256:<hex>`, Detector 4.2)의 원래 기준값을 구성원 판정의 원천 행에서 읽는다(Detector 4.2 "화면은 원천 행에서 읽음").
 * - channel_id: 판정 PROCESS 행 `data_processes.channel_id`
 * - response_code: 거래(TRANSACTION) 값 — 같은 원본·같은 거래 ID 문자열의 T 행을 실행마다 가장 큰 리비전 1건으로 줄여 정확히 1개일 때만,
 *   실행 식별 불가(execution_id NULL) 행이 하나라도 있으면 없음(Detector 2.3 resolve_transaction_response_code와 같은 규칙)
 * 읽은 값의 SHA-256이 group_key의 hex와 같을 때만 쓴다(그 뒤 리비전으로 값이 바뀐 행을 잘못 보이지 않게). 못 찾으면 그 흐름은 없음(화면 "값 생략").
 */
async function resolveLongValues(db: Queryable, flows: IncidentFlow[]) {
  const found = new Map<string, string>();
  const wanted = flows.flatMap((flow) => {
    const { kind, valueHash } = parseGroupKey(flow.criteria.groupKey);
    return valueHash !== null && (kind === 'channel_id' || kind === 'response_code') ? [{ flow, kind, valueHash }] : [];
  });
  if (!wanted.length) return found;
  const incidents: string[] = [], owners: string[] = [], kinds: string[] = [];
  for (const { flow, kind } of wanted) {
    for (const id of flow.segments.flatMap((s) => s.incidentIds).slice(0, LONG_VALUE_INCIDENTS)) { incidents.push(id); owners.push(flow.id); kinds.push(kind); }
  }
  const { rows } = await db.query(`WITH f AS (SELECT * FROM unnest($1::uuid[],$2::uuid[],$3::text[]) AS x(incident_id,flow_id,kind))
    SELECT f.flow_id::text AS flow,CASE WHEN f.kind='channel_id' THEN p.channel_id ELSE tx.code END AS value
    FROM f JOIN run_incidents i ON i.id=f.incident_id
    CROSS JOIN LATERAL (SELECT mm.decision_id,mm.occurred_at FROM run_incident_members mm WHERE mm.incident_id=f.incident_id
      ORDER BY mm.occurred_at,mm.decision_id LIMIT $4) m
    JOIN run_process_decisions d ON d.id=m.decision_id
    LEFT JOIN data_processes p ON p.observation_id=d.process_observation_id
    LEFT JOIN LATERAL (
      SELECT CASE WHEN count(*) FILTER (WHERE r.execution_id IS NULL)=0 AND count(*)=1 THEN min(r.code) END AS code FROM (
        SELECT DISTINCT ON (o.execution_id) o.execution_id,t.response_code AS code
        FROM data_transactions t JOIN data_observations o ON o.id=t.observation_id AND o.source_id=i.source_id AND o.layer='T'
        WHERE f.kind='response_code' AND NOT ${ABSENT_SQL('p.transaction_id')} AND t.transaction_id=p.transaction_id
        ORDER BY o.execution_id,o.revision_no DESC NULLS LAST) r
    ) tx ON true
    ORDER BY f.flow_id,m.occurred_at,m.decision_id`, [incidents, owners, kinds, LONG_VALUE_MEMBERS]);
  const hashOf = new Map(wanted.map(({ flow, valueHash }) => [flow.id, valueHash]));
  for (const row of rows) {
    if (found.has(row.flow) || typeof row.value !== 'string') continue;
    if (sha256Hex(row.value) === hashOf.get(row.flow)) found.set(row.flow, row.value);
  }
  return found;
}
const criteriaValue = (flow: IncidentFlow, resolved: Map<string, string>) => flow.criteria.value ?? resolved.get(flow.id) ?? null;

const withFlag = (flags: IncidentFlag[], add: boolean, value: IncidentFlag) =>
  add && !flags.includes(value) ? INCIDENT_FLAGS.filter((f) => f === value || flags.includes(f)) : flags;
const durationMs = (startAt: string, endAt: string) => Math.max(0, Date.parse(endAt) - Date.parse(startAt));

export type IncidentListFilter = {
  from: string | null;
  to: string | null;
  criteria: string | null;
  sourceId: string | null;
  /** 해제되지 않은 서로 다른 거래 ID 수(counts.transactions와 같은 기준)가 이 값 이상인 흐름만(1 = 거르지 않음). */
  minTransactions: number;
  limit: number;
  cursor: { startAt: string; id: string } | null;
};
export const encodeCursor = (value: unknown[]) => Buffer.from(JSON.stringify(value), 'utf8').toString('base64url');
/** (startAt, id) 내림차순 키셋. */
const newestFirst = (a: IncidentFlow, b: IncidentFlow) => -byTimeThenId({ at: a.startAt, id: a.id }, { at: b.startAt, id: b.id });

/**
 * GET /api/analysis/incidents: 흐름 목록(시작 시각 최근 순). 기간은 [startAt, endAt]이 [from, to]와 겹치는 흐름.
 * 골격은 원본마다 따로 읽는다(원본 지정 없으면 사건이 있는 원본 전부, 흐름은 원본을 넘지 않음). 쪽 넘김 = (startAt, id) 키셋.
 */
export async function readIncidentList(db: Queryable, filter: IncidentListFilter,
  { now = () => new Date(), scanLimit = INCIDENT_SCAN_LIMIT }: { now?: () => Date; scanLimit?: number } = {}) {
  const sources = filter.sourceId === null ? await incidentSources(db) : [filter.sourceId];
  let truncated = false;
  const flows: IncidentFlow[] = [];
  for (const sourceId of sources) {
    const skeleton = await loadSkeleton(db, sourceId, scanLimit);
    truncated ||= skeleton.truncated;
    flows.push(...buildIncidentFlows(skeleton.rows, { partial: skeleton.truncated }));
  }
  const from = filter.from, to = filter.to;
  let matching = flows
    .filter((flow) => (from === null || Date.parse(flow.endAt) >= Date.parse(from)) && (to === null || Date.parse(flow.startAt) <= Date.parse(to)))
    .filter((flow) => filter.criteria === null || flow.criteria.kind === filter.criteria)
    .sort(newestFirst);
  if (filter.minTransactions > 1) {
    const totals = await loadTransactionTotals(db, matching);
    matching = matching.filter((flow) => (totals.get(flow.id) ?? 0) >= filter.minTransactions);
  }
  const cursor = filter.cursor;
  const after = cursor === null ? matching : matching.filter((flow) => flow.startAt < cursor.startAt
    || (flow.startAt === cursor.startAt && flow.id < cursor.id));
  const page = after.slice(0, filter.limit);
  const counts = await loadCounts(db, page);
  const values = await resolveLongValues(db, page);
  const last = page.at(-1);
  return {
    observedAt: now().toISOString(),
    truncated,
    total: matching.length,
    nextCursor: after.length > filter.limit && last ? encodeCursor([last.startAt, last.id]) : null,
    items: page.map((flow) => {
      const entry = counts.get(flow.id)!;
      return { id: flow.id, sourceId: flow.sourceId,
        criteria: { kind: flow.criteria.kind, value: criteriaValue(flow, values), longValue: flow.criteria.longValue, version: flow.criteria.version },
        startAt: flow.startAt, endAt: flow.endAt, durationMs: durationMs(flow.startAt, flow.endAt), counts: entry.counts, occurredBasis: entry.basis,
        chain: { length: flow.segments.length, mergedIncidents: flow.segments.reduce((n, s) => n + s.mergedIncidentIds.length, 0) },
        flags: withFlag(flow.flags, entry.mismatch, 'member_source_mismatch'), truncated: flow.truncated };
    }),
  };
}

/**
 * 요청 id(어느 사건이든) → 그 사건이 속한 흐름. 사건이 없으면 null(404).
 * 목록 골격(최근 50,000건) 대신 요청 사건에서 병합(양방향)·이어짐(양방향)을 직접 따라가 사슬만 읽는다(UNION으로 고리 종료, LIMIT로 상한).
 * 상한(INCIDENT_CHAIN_LIMIT)을 넘으면 partial로 흐름을 만들고 truncated=true(흐름이 일부일 수 있음). 어긋난 연결로 닿은 다른 흐름의 사건도
 * 읽지만 결과는 요청 사건이 든 흐름 하나다(골격으로 만든 흐름과 같은 결과).
 */
async function resolveFlow(db: Queryable, id: string, limit = INCIDENT_CHAIN_LIMIT) {
  const { rows: walked } = await db.query(`WITH RECURSIVE walk(id) AS (
      SELECT id FROM run_incidents WHERE id=$1
      UNION
      SELECT n.id FROM walk w CROSS JOIN LATERAL (
        SELECT i.continues_from FROM run_incidents i WHERE i.id=w.id AND i.continues_from IS NOT NULL
        UNION ALL SELECT s.id FROM run_incidents s WHERE s.continues_from=w.id
        UNION ALL SELECT e.target_incident_id FROM run_incident_events e WHERE e.incident_id=w.id AND e.kind='merged_into'
        UNION ALL SELECT e.incident_id FROM run_incident_events e WHERE e.target_incident_id=w.id AND e.kind='merged_into'
      ) n(id)
    ) SELECT id FROM walk LIMIT $2`, [id, limit + 1]);
  if (!walked.length) return null;
  const partial = walked.length > limit;
  const { rows } = await db.query(`${SKELETON_SELECT} WHERE i.id=ANY($1::uuid[])`, [walked.slice(0, limit).map((row) => row.id)]);
  const flow = buildIncidentFlows(rows as IncidentSkeleton[], { partial }).find((f) => f.segments.some((s) => s.incidentIds.includes(id)));
  return flow ? { flow, truncated: partial || flow.truncated } : null;
}

/** GET /api/analysis/incidents/:id: 흐름 머리(기준·기간·건수) + 구간(이어짐·병합). 요청 id가 병합·뒤 구간이면 흐름 id(첫 구간)와 함께 돌려준다. */
export async function readIncidentDetail(db: Queryable, id: string, chainLimit = INCIDENT_CHAIN_LIMIT) {
  const resolved = await resolveFlow(db, id, chainLimit);
  if (!resolved) return null;
  const { flow, truncated } = resolved;
  const { rows: [head] } = await db.query('SELECT criteria FROM run_incidents WHERE id=$1', [flow.id]);
  const entry = (await loadCounts(db, [flow])).get(flow.id)!;
  const values = await resolveLongValues(db, [flow]);
  return {
    id: flow.id,
    requestedId: id,
    sourceId: flow.sourceId,
    truncated,
    criteria: { ...flow.criteria, value: criteriaValue(flow, values), document: (head?.criteria ?? {}) as Record<string, unknown> },
    startAt: flow.startAt,
    endAt: flow.endAt,
    durationMs: durationMs(flow.startAt, flow.endAt),
    counts: entry.counts,
    occurredBasis: entry.basis,
    flags: withFlag(flow.flags, entry.mismatch, 'member_source_mismatch'),
    segments: flow.segments.map((segment) => {
      const part = entry.segments.get(segment.id);
      return { ...segment, durationMs: durationMs(segment.openedAt, segment.endAt), counts: part?.counts ?? zeroCounts(), occurredBasis: part?.basis ?? zeroBasis() };
    }),
  };
}

/** 구성원 쪽 커서: [그 거래 첫 구성원 시각, 묶음 키]. */
export type MemberCursor = { firstAt: string; key: string };

/**
 * GET /api/analysis/incidents/:id/members: 흐름 구성원을 거래별로(첫 구성원 시각 → 키 순). 거래 ID가 없음인 판정은 판정마다 한 줄.
 * 거래 해제 = 그 거래의 구성원 판정이 모두 해제. 인터페이스 = 같은 원본·같은 거래 ID TRANSACTION 행의 interface_id가 하나뿐일 때만.
 * 비용: 먼저 흐름 구성원의 키·첫 시각만으로 이 쪽의 거래 키를 정하고, 그 키의 판정만 등급·실행별 최신 판정·jsonb로 묶는다.
 */
export async function readIncidentMembers(db: Queryable, id: string, limit: number, cursor: MemberCursor | null,
  chainLimit = INCIDENT_CHAIN_LIMIT) {
  const resolved = await resolveFlow(db, id, chainLimit);
  if (!resolved) return null;
  const { flow } = resolved;
  const values: unknown[] = [...mapping([flow]), flow.sourceId, cursor?.firstAt ?? null, cursor?.key ?? null, limit + 1,
    INCIDENT_DECISIONS_PER_TRANSACTION];
  const { rows } = await db.query(`WITH ${MEMBER_BASE_CTE}, keyed AS (
      SELECT mem.*,CASE WHEN tx_absent THEN 'decision:'||decision_id::text ELSE 'transaction:'||transaction_id END AS key
      FROM mem WHERE same_source
    ), keys AS (
      SELECT key,min(occurred_at) AS first_at FROM keyed GROUP BY key
    ), page AS (
      SELECT key,first_at FROM keys
      WHERE $5::timestamptz IS NULL OR (first_at,key COLLATE "C") > ($5::timestamptz,$6::text COLLATE "C")
      ORDER BY first_at,key COLLATE "C" LIMIT $7
    ), sel AS (
      SELECT keyed.* FROM keyed JOIN page USING (key)
    ), ${LATEST_CTE('sel')}, x AS (
      SELECT sel.*,${GRADE_SQL('d')} AS grade,l.latest_id,l.latest_grade,${RELEASED_SQL('l.latest_grade')} AS released,
        row_number() OVER (PARTITION BY sel.key ORDER BY sel.occurred_at,sel.decision_id) AS n
      FROM sel JOIN run_process_decisions d ON d.id=sel.decision_id LEFT JOIN latest l ON l.execution_id=sel.execution_id
    ), grouped AS (
      SELECT key,bool_and(tx_absent) AS tx_absent,min(transaction_id) AS transaction_id,min(occurred_at) AS first_at,max(occurred_at) AS last_at,
        bool_and(released) AS released,count(*)::integer AS decision_count,
        CASE WHEN bool_and(occurred_basis='process_time_raw') THEN 'process_time_raw' WHEN bool_and(occurred_basis='decided_at') THEN 'decided_at'
          ELSE 'mixed' END AS basis,
        jsonb_agg(jsonb_build_object('decisionId',decision_id,'incidentId',incident_id,'segmentId',segment_id,
          'occurredAt',${iso('occurred_at')},'occurredBasis',occurred_basis,'processId',process_id,'grade',grade,
          'released',released,'latestDecisionId',latest_id,'latestGrade',latest_grade,'gradeUnknown',latest_grade IS NULL)
          ORDER BY occurred_at,decision_id) FILTER (WHERE n<=$8) AS decisions
      FROM x GROUP BY key
    )
    SELECT (SELECT count(*)::integer FROM keys) AS total,
      (SELECT coalesce(jsonb_agg(jsonb_build_object('key',g.key,'transactionId',CASE WHEN g.tx_absent THEN NULL ELSE g.transaction_id END,
         'interfaceId',ti.interface_id,'firstOccurredAt',${iso('g.first_at')},'lastOccurredAt',${iso('g.last_at')},'occurredBasis',g.basis,'released',g.released,
         'decisionCount',g.decision_count,'decisionsTruncated',g.decision_count>$8,'decisions',g.decisions) ORDER BY g.first_at,g.key COLLATE "C"),'[]'::jsonb)
       FROM grouped g LEFT JOIN LATERAL (
         SELECT CASE WHEN count(DISTINCT t.interface_id)=1 THEN min(t.interface_id) END AS interface_id
         FROM data_transactions t JOIN data_observations o ON o.id=t.observation_id AND o.source_id=$4 AND o.layer='T'
         WHERE NOT g.tx_absent AND t.transaction_id=g.transaction_id) ti ON true) AS items`, values);
  const items = rows[0].items as Record<string, any>[];
  const page = items.slice(0, limit);
  const last = page.at(-1);
  return {
    id: flow.id,
    requestedId: id,
    total: rows[0].total as number,
    nextCursor: items.length > limit && last ? encodeCursor([last.firstOccurredAt, last.key]) : null,
    items: page,
  };
}

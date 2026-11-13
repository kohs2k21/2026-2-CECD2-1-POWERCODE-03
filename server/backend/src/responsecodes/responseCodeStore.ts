import type { Pool, PoolClient } from 'pg';
import { readCurrentHardRule, type ActorResolver } from '../ruleset/ruleSetStore.js';
import {
  ResponseCodeError, TRANSACTION_FAILED_RULE, encodeCursor, failedSeverityOf, severityFor,
  type CodeSeverity, type FailedRuleSeverity, type ListQuery, type MessageEdit,
} from './responseCodeContract.js';

/**
 * 응답 코드 사전 읽기·메시지 편집(contracts/response-codes-ui.md 3·4절, 표 ops_response_codes·ops_response_code_changes — migration 025).
 * 코드별 위험도는 현재 룰 세트 hard:transaction-failed의 {byCode, default}에서 읽는다(이 저장소는 위험도를 저장하지 않는다).
 * 룰 세트 byCode에만 있고 사전에 없는 코드도 목록·상세에 낸다(inDictionary false).
 */
type Queryable = Pick<Pool, 'query'>;
const ISO = (column: string) => `to_char(${column} AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')`;
/** 정렬·커서 비교는 코드 포인트 순(COLLATE "C", 룰 세트 정규 객체 byCode 순서와 같음). */
const BY_CODE = 'code COLLATE "C"';

export type RuleSetRef = { revision: number; ruleId: string; enabled: boolean; defaultSeverity: FailedRuleSeverity['default']; registeredCodes: number };

async function failedRule(db: Queryable) {
  const rule = await readCurrentHardRule(db, TRANSACTION_FAILED_RULE);
  const severity = failedSeverityOf(rule.severity);
  // 내장 모양({byCode, default})이 아니면 빈 위험도로 대신하지 않고 저장소 오류로 둔다.
  if (!severity) throw new Error('transaction_failed_severity_shape');
  const ref: RuleSetRef = { revision: rule.revision, ruleId: TRANSACTION_FAILED_RULE, enabled: rule.enabled, defaultSeverity: severity.default,
    registeredCodes: Object.keys(severity.byCode).length };
  return { ref, severity };
}

type ItemRow = {
  code: string; inDictionary: boolean; enumName: string | null; codeType: string | null; httpStatus: string | null; messageKey: string | null;
  message: string | null; origin: 'import' | 'edited' | null;
};
const ITEM_COLUMNS = `code,true AS "inDictionary",enum_name AS "enumName",code_type AS "codeType",http_status AS "httpStatus",message_key AS "messageKey",message,origin`;
const item = (row: ItemRow, severity: FailedRuleSeverity) => ({ ...row, severity: severityFor(row.code, severity) });

export async function readResponseCodeList(db: Queryable, filter: ListQuery) {
  const { ref, severity } = await failedRule(db);
  const extra = Object.keys(severity.byCode);
  // 사전 행 + byCode에만 있는 코드(사전에 없음). 검색은 코드·코드명·메시지(소문자 포함), 유형 필터는 사전 행만 맞는다.
  const rows = `WITH entries AS (
      SELECT ${ITEM_COLUMNS} FROM ops_response_codes
      UNION ALL
      SELECT x AS code,false,NULL,NULL,NULL,NULL,NULL,NULL FROM unnest($1::text[]) x WHERE NOT EXISTS (SELECT 1 FROM ops_response_codes r WHERE r.code=x)
    )`;
  const { rows: page } = await db.query<ItemRow>(`${rows}
    SELECT * FROM entries
    WHERE ($2::text IS NULL OR strpos(lower(code),$2)>0 OR strpos(lower(coalesce("enumName",'')),$2)>0 OR strpos(lower(coalesce(message,'')),$2)>0)
      AND ($3::text IS NULL OR "codeType"=$3)
      AND ($4::text IS NULL OR ${BY_CODE} > $4)
    ORDER BY ${BY_CODE} LIMIT $5`, [extra, filter.q, filter.type, filter.after, filter.limit + 1]);
  const { rows: [counts] } = await db.query<{ dictionary: number; edited: number; missing: number }>(`SELECT
      (SELECT count(*)::int FROM ops_response_codes) AS dictionary,
      (SELECT count(*)::int FROM ops_response_codes WHERE origin='edited') AS edited,
      (SELECT count(*)::int FROM unnest($1::text[]) x WHERE NOT EXISTS (SELECT 1 FROM ops_response_codes r WHERE r.code=x)) AS missing`, [extra]);
  const { rows: types } = await db.query<{ codeType: string; count: number }>(`SELECT code_type AS "codeType",count(*)::int AS count FROM ops_response_codes
    WHERE code_type IS NOT NULL GROUP BY code_type ORDER BY code_type COLLATE "C"`);
  const shown = page.slice(0, filter.limit);
  const hasMore = page.length > filter.limit;
  return {
    ruleSet: ref,
    counts: { dictionary: counts.dictionary, edited: counts.edited, missingFromDictionary: counts.missing },
    types,
    items: shown.map((row) => item(row, severity)),
    pagination: { limit: filter.limit, hasMore, nextCursor: hasMore && shown.length ? encodeCursor(shown.at(-1)!.code) : null },
  };
}

const HISTORY_SCAN_MAX = 500;
const HISTORY_SHOWN_MAX = 50;
type SeverityChangeRow = { revision: string; savedAt: string; actorId: string; restoredFromRevision: string | null; severity: unknown };

/**
 * 그 코드의 위험도가 바뀐 룰 세트 버전(최근 순). 근거 = 저장 리비전 changes[]의 hard_updated(hard:transaction-failed) diff.severity [이전, 이후].
 * 코드의 단계 또는 출처(코드별 ↔ 기본값 따름)가 달라진 기록만 낸다. 기본 단계만 바뀐 버전도 기본값을 따르는 코드에는 바뀜이다.
 */
async function severityHistory(db: Queryable, code: string, actor: ActorResolver) {
  const { rows } = await db.query<SeverityChangeRow>(`SELECT r.revision::text AS revision,${ISO('r.saved_at')} AS "savedAt",r.saved_by_actor_id AS "actorId",
      r.restored_from_revision::text AS "restoredFromRevision",ch->'diff'->'severity' AS severity
    FROM ops_rule_set_revisions r
    CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(r.changes)='array' THEN r.changes ELSE '[]'::jsonb END) ch
    WHERE ch->>'type'='hard_updated' AND ch->>'id'=$1
    ORDER BY r.revision DESC LIMIT $2`, [TRANSACTION_FAILED_RULE, HISTORY_SCAN_MAX]);
  const changed: { row: SeverityChangeRow; before: CodeSeverity; after: CodeSeverity }[] = [];
  for (const row of rows) {
    if (!Array.isArray(row.severity) || row.severity.length !== 2) continue;
    const [from, to] = row.severity.map(failedSeverityOf);
    if (!from || !to) continue;
    const before = severityFor(code, from);
    const after = severityFor(code, to);
    if (before.level !== after.level || before.source !== after.source) changed.push({ row, before, after });
    if (changed.length === HISTORY_SHOWN_MAX) break;
  }
  return Promise.all(changed.map(async ({ row, before, after }) => ({
    revision: Number(row.revision), savedAt: row.savedAt, savedBy: await actor(row.actorId),
    restoredFromRevision: row.restoredFromRevision === null ? null : Number(row.restoredFromRevision), before, after,
  })));
}

const MESSAGE_HISTORY_MAX = 50;
export async function readResponseCodeDetail(db: Queryable, code: string, actor: ActorResolver) {
  const { ref, severity } = await failedRule(db);
  const { rows: [row] } = await db.query<ItemRow & { revision: number; updatedAt: string; actorId: string }>(`SELECT ${ITEM_COLUMNS},revision,
      ${ISO('updated_at')} AS "updatedAt",updated_by_actor_id AS "actorId" FROM ops_response_codes WHERE code=$1`, [code]);
  const registered = Object.hasOwn(severity.byCode, code);
  if (!row && !registered) throw new ResponseCodeError('response_code_not_found', 404);
  const { rows: changes } = row ? await db.query<{ kind: string; messageBefore: string | null; messageAfter: string; actorId: string; changedAt: string }>(
    `SELECT kind,message_before AS "messageBefore",message_after AS "messageAfter",actor_id AS "actorId",${ISO('changed_at')} AS "changedAt"
     FROM ops_response_code_changes WHERE code=$1 ORDER BY id DESC LIMIT $2`, [code, MESSAGE_HISTORY_MAX]) : { rows: [] };
  const base: ItemRow = row
    ? { code: row.code, inDictionary: true, enumName: row.enumName, codeType: row.codeType, httpStatus: row.httpStatus, messageKey: row.messageKey, message: row.message, origin: row.origin }
    : { code, inDictionary: false, enumName: null, codeType: null, httpStatus: null, messageKey: null, message: null, origin: null };
  return {
    ...item(base, severity),
    revision: row?.revision ?? null,
    updatedAt: row?.updatedAt ?? null,
    updatedBy: row ? await actor(row.actorId) : null,
    ruleSet: ref,
    messageHistory: await Promise.all(changes.map(async ({ actorId, ...change }) => ({ ...change, changedBy: await actor(actorId) }))),
    severityHistory: await severityHistory(db, code, actor),
  };
}

/**
 * 메시지 편집(관리자). 사전 행이면 expectedRevision이 행 수정 번호와 같아야 한다(다르면 409 response_code_revision_conflict).
 * 사전에 없는 코드는 현재 룰 세트 byCode에 있을 때만 expectedRevision null로 새 행을 만든다(origin edited). 같은 메시지는 409 response_code_unchanged.
 */
export async function editResponseCodeMessage(pool: Pool, code: string, edit: MessageEdit, actorId: string) {
  const client: PoolClient = await pool.connect();
  try {
    await client.query('BEGIN');
    const { rows: [current] } = await client.query<{ revision: number; message: string }>(
      'SELECT revision,message FROM ops_response_codes WHERE code=$1 FOR UPDATE', [code]);
    if (!current) {
      const { severity } = await failedRule(client);
      if (!Object.hasOwn(severity.byCode, code)) throw new ResponseCodeError('response_code_not_found', 404);
      if (edit.expectedRevision !== null) throw new ResponseCodeError('response_code_revision_conflict', 409, { currentRevision: null });
      // 같은 코드를 동시에 만들면 한쪽이 PK 충돌 → 아래 catch에서 409로 바꾼다.
      await client.query(`INSERT INTO ops_response_codes(code,message,origin,updated_by_actor_id) VALUES($1,$2,'edited',$3)`, [code, edit.message, actorId]);
      await client.query(`INSERT INTO ops_response_code_changes(code,kind,message_before,message_after,actor_id) VALUES($1,'edit',NULL,$2,$3)`, [code, edit.message, actorId]);
    } else {
      if (edit.expectedRevision !== current.revision) throw new ResponseCodeError('response_code_revision_conflict', 409, { currentRevision: current.revision });
      if (current.message === edit.message) throw new ResponseCodeError('response_code_unchanged', 409);
      await client.query(`UPDATE ops_response_codes SET message=$2,origin='edited',revision=revision+1,updated_at=clock_timestamp(),updated_by_actor_id=$3
        WHERE code=$1`, [code, edit.message, actorId]);
      await client.query(`INSERT INTO ops_response_code_changes(code,kind,message_before,message_after,actor_id) VALUES($1,'edit',$2,$3,$4)`,
        [code, current.message, edit.message, actorId]);
    }
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK').catch(() => undefined);
    if ((error as { code?: string }).code === '23505') throw new ResponseCodeError('response_code_revision_conflict', 409, { currentRevision: 1 });
    throw error;
  } finally {
    client.release();
  }
}

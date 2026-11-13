import type { Pool, PoolClient } from 'pg';
import type { SheetRow } from './responseCodeFile.js';
import { RESPONSE_CODE_PATTERN, RESPONSE_CODE_LIMITS, hasControlCharacter } from './responseCodeContract.js';

/**
 * 응답 코드 사전 가져오기(contracts/response-codes-ui.md 6절). 머리글 이름으로 열을 찾고, 묶음 제목 행(값 1칸)은 건너뛰고, 검사하고,
 * 같은 코드의 행은 `A 또는 B`로 합친 뒤 upsert한다.
 * 결과는 건수와 행 번호·사유 코드만 낸다(값은 내지 않는다 — 고객 자료).
 */
export type ImportField = 'code' | 'enumName' | 'codeType' | 'httpStatus' | 'messageKey' | 'message';
/** 머리글 이름(공백 제거·소문자로 비교). 한국어 원본 머리글과 영문 별칭. */
const HEADER_NAMES: Record<ImportField, string[]> = {
  code: ['에러코드', '응답코드', '코드', 'code', 'errorcode', 'responsecode'],
  enumName: ['코드명(enum)', '코드명', 'enum', 'enumname'],
  codeType: ['유형(type)', '유형', 'type'],
  httpStatus: ['http상태', 'httpstatus', 'http'],
  messageKey: ['메시지키', 'messagekey'],
  message: ['메시지(한국어)', '메시지', 'message'],
};
const HEADER_SEARCH_ROWS = 20;
const normalizeHeader = (text: string) => text.replace(/\s+/g, '').toLowerCase();

export type ImportEntry = {
  row: number; code: string; enumName: string | null; codeType: string | null; httpStatus: string | null; messageKey: string | null; message: string;
};
export type ImportIssue = { row: number; reason: string };
export type ParsedImport = {
  headerRow: number;
  /** 머리글 아래 빈 행이 아닌 행 수(묶음 제목·잘못된 행·합친 행 포함). */
  rowsRead: number;
  groupRows: number;
  invalid: ImportIssue[];
  /** 파일에 두 번 이상 나와 한 행으로 합친 코드 수(사용자 결정 11/13 — 메시지·코드명·메시지 키를 `A 또는 B`로). */
  merged: number;
  /** 합친 코드의 둘째 이후 행 번호(값은 내지 않는다). */
  mergedRows: number[];
  /** 코드 열이 숫자 셀인 행 수(앞자리 0이 사라졌을 수 있음 — 값은 셀 글자 그대로 쓴다). */
  numericCodeCells: number;
  entries: ImportEntry[];
};

export class ResponseCodeImportError extends Error {
  constructor(readonly code: string) { super(code); }
}

function findHeader(rows: SheetRow[]) {
  for (const sheetRow of rows.slice(0, HEADER_SEARCH_ROWS)) {
    const columns: Partial<Record<ImportField, number>> = {};
    let duplicate = false;
    sheetRow.cells.forEach((cell, index) => {
      const name = normalizeHeader(cell);
      const field = (Object.keys(HEADER_NAMES) as ImportField[]).find((key) => HEADER_NAMES[key].includes(name));
      if (!field) return;
      if (columns[field] !== undefined) duplicate = true;
      columns[field] = index;
    });
    if (columns.code !== undefined && columns.message !== undefined) {
      if (duplicate) throw new ResponseCodeImportError('header_duplicate_column');
      return { row: sheetRow, columns };
    }
  }
  throw new ResponseCodeImportError('header_not_found');
}

const optional = (value: string, limit: number): string | null | false => {
  if (value === '') return null;
  if ([...value].length > limit || hasControlCharacter(value)) return false;
  return value;
};

export function parseImportRows(rows: SheetRow[]): ParsedImport {
  const header = findHeader(rows);
  const { columns } = header;
  const cell = (row: SheetRow, field: ImportField) => (columns[field] === undefined ? '' : (row.cells[columns[field]!] ?? '').trim());
  const result: ParsedImport = { headerRow: header.row.row, rowsRead: 0, groupRows: 0, invalid: [], merged: 0, mergedRows: [], numericCodeCells: 0, entries: [] };
  const byCode = new Map<string, { entry: ImportEntry; parts: MergeParts }>();
  const mergedCodes = new Set<string>();
  for (const row of rows.filter((item) => item.row > header.row.row)) {
    const filled = row.cells.filter((value) => value.trim() !== '').length;
    if (filled === 0) continue;
    result.rowsRead += 1;
    const code = cell(row, 'code');
    // 묶음 제목 행(분류 이름 한 칸, 코드 형식이 아님)만 건너뛴다. 코드 없이 값이 여러 칸이면 잘못된 행.
    if (filled === 1 && !RESPONSE_CODE_PATTERN.test(code)) { result.groupRows += 1; continue; }
    if (code === '') { result.invalid.push({ row: row.row, reason: 'code_missing' }); continue; }
    if (!RESPONSE_CODE_PATTERN.test(code)) { result.invalid.push({ row: row.row, reason: 'code_invalid' }); continue; }
    const message = cell(row, 'message');
    if (message === '') { result.invalid.push({ row: row.row, reason: 'message_missing' }); continue; }
    if ([...message].length > RESPONSE_CODE_LIMITS.message || hasControlCharacter(message)) { result.invalid.push({ row: row.row, reason: 'message_invalid' }); continue; }
    const enumName = optional(cell(row, 'enumName'), RESPONSE_CODE_LIMITS.enumName);
    const codeType = optional(cell(row, 'codeType'), RESPONSE_CODE_LIMITS.codeType);
    const httpStatus = optional(cell(row, 'httpStatus'), RESPONSE_CODE_LIMITS.httpStatus);
    const messageKey = optional(cell(row, 'messageKey'), RESPONSE_CODE_LIMITS.messageKey);
    const bad = ([['enum_name', enumName], ['code_type', codeType], ['http_status', httpStatus], ['message_key', messageKey]] as const).find(([, value]) => value === false);
    if (bad) { result.invalid.push({ row: row.row, reason: `${bad[0]}_invalid` }); continue; }
    const entry: ImportEntry = { row: row.row, code, enumName: enumName as string | null, codeType: codeType as string | null,
      httpStatus: httpStatus as string | null, messageKey: messageKey as string | null, message };
    const first = byCode.get(code);
    if (!first) {
      byCode.set(code, { entry, parts: { message: [message], enumName: enumName ? [enumName] : [], messageKey: messageKey ? [messageKey] : [] } });
      result.entries.push(entry);
      if (row.numeric[columns.code!]) result.numericCodeCells += 1;
      continue;
    }
    const reason = mergeInto(first.entry, first.parts, entry);
    if (reason) { result.invalid.push({ row: row.row, reason }); continue; }
    mergedCodes.add(code);
    result.mergedRows.push(row.row);
    if (row.numeric[columns.code!]) result.numericCodeCells += 1;
  }
  result.merged = mergedCodes.size;
  return result;
}

/** 합칠 때 잇는 말. 원본에 같은 코드가 다른 오류 두 줄에 쓰였으면 로그(코드만 찍힘)로는 구분할 수 없어 둘 다 보인다. */
export const MERGE_SEPARATOR = ' 또는 ';

type MergeParts = { message: string[]; enumName: string[]; messageKey: string[] };

/**
 * 같은 코드의 뒤 행을 첫 행에 합친다(첫 행 객체와 parts를 고친다). 반환 = 잘못된 행 사유, 합쳤으면 null.
 * - 유형·HTTP 상태: 두 행이 같아야 한다(빈 칸도 값으로 본다). 다르면 `duplicate_code_type_mismatch`·`duplicate_http_status_mismatch`
 * - 메시지·코드명·메시지 키: 서로 다르면 `<앞 값> 또는 <뒤 값>`(파일 순서). 이미 들어 있는 값(같은 값)은 다시 붙이지 않는다.
 *   빈 칸은 값이 없는 것으로 보고 다른 행 값을 쓴다. 합친 결과가 상한을 넘으면 `merged_message_too_long` 등(그 행은 잘못된 행, 첫 행은 그대로)
 */
function mergeInto(first: ImportEntry, parts: MergeParts, next: ImportEntry): string | null {
  if (first.codeType !== next.codeType) return 'duplicate_code_type_mismatch';
  if (first.httpStatus !== next.httpStatus) return 'duplicate_http_status_mismatch';
  const add = (list: string[], value: string | null) => (value === null || list.includes(value) ? list : [...list, value]);
  const message = add(parts.message, next.message);
  const enumName = add(parts.enumName, next.enumName);
  const messageKey = add(parts.messageKey, next.messageKey);
  const joined = (list: string[]) => (list.length ? list.join(MERGE_SEPARATOR) : null);
  const length = (value: string | null) => (value === null ? 0 : [...value].length);
  if (length(joined(message)) > RESPONSE_CODE_LIMITS.message) return 'merged_message_too_long';
  if (length(joined(enumName)) > RESPONSE_CODE_LIMITS.enumName) return 'merged_enum_name_too_long';
  if (length(joined(messageKey)) > RESPONSE_CODE_LIMITS.messageKey) return 'merged_message_key_too_long';
  Object.assign(parts, { message, enumName, messageKey });
  Object.assign(first, { message: joined(message)!, enumName: joined(enumName), messageKey: joined(messageKey) });
  return null;
}

export type ImportOptions = { force: boolean; dryRun: boolean; actorId: string };
export type ImportCounts = { inserted: number; updated: number; unchanged: number; keptEdited: number; overwrittenEdited: number };

type StoredRow = { code: string; enumName: string | null; codeType: string | null; httpStatus: string | null; messageKey: string | null; message: string; origin: string };
const sameValues = (a: StoredRow, b: ImportEntry) =>
  a.enumName === b.enumName && a.codeType === b.codeType && a.httpStatus === b.httpStatus && a.messageKey === b.messageKey && a.message === b.message;

/**
 * 트랜잭션 1개로 넣는다. 새 코드 INSERT(origin import), 가져온 값 그대로인 행(origin import)은 값이 다르면 UPDATE,
 * 화면에서 고친 행(origin edited)은 force일 때만 덮는다. 파일에 없는 코드는 그대로 둔다(지우지 않음). 메시지가 바뀌면 변경 기록을 남긴다.
 * dryRun이면 같은 검사·계산 뒤 ROLLBACK한다.
 */
export async function importResponseCodes(pool: Pool, entries: ImportEntry[], options: ImportOptions): Promise<ImportCounts> {
  const counts: ImportCounts = { inserted: 0, updated: 0, unchanged: 0, keptEdited: 0, overwrittenEdited: 0 };
  const client: PoolClient = await pool.connect();
  try {
    await client.query('BEGIN');
    const { rows } = await client.query<StoredRow>(`SELECT code,enum_name AS "enumName",code_type AS "codeType",http_status AS "httpStatus",
      message_key AS "messageKey",message,origin FROM ops_response_codes WHERE code=ANY($1::text[]) FOR UPDATE`, [entries.map((entry) => entry.code)]);
    const stored = new Map(rows.map((row) => [row.code, row]));
    const log = (entry: ImportEntry, before: string | null) => client.query(`INSERT INTO ops_response_code_changes(code,kind,message_before,message_after,actor_id)
      VALUES($1,'import',$2,$3,$4)`, [entry.code, before, entry.message, options.actorId]);
    for (const entry of entries) {
      const values = [entry.code, entry.enumName, entry.codeType, entry.httpStatus, entry.messageKey, entry.message, options.actorId];
      const previous = stored.get(entry.code);
      if (!previous) {
        await client.query(`INSERT INTO ops_response_codes(code,enum_name,code_type,http_status,message_key,message,origin,updated_by_actor_id)
          VALUES($1,$2,$3,$4,$5,$6,'import',$7)`, values);
        await log(entry, null);
        counts.inserted += 1;
        continue;
      }
      if (previous.origin === 'edited' && !options.force) { counts.keptEdited += 1; continue; }
      if (previous.origin === 'import' && sameValues(previous, entry)) { counts.unchanged += 1; continue; }
      await client.query(`UPDATE ops_response_codes SET enum_name=$2,code_type=$3,http_status=$4,message_key=$5,message=$6,origin='import',
        revision=revision+1,updated_at=clock_timestamp(),updated_by_actor_id=$7 WHERE code=$1`, values);
      if (previous.message !== entry.message) await log(entry, previous.message);
      if (previous.origin === 'edited') counts.overwrittenEdited += 1;
      else counts.updated += 1;
    }
    await client.query(options.dryRun ? 'ROLLBACK' : 'COMMIT');
    return counts;
  } catch (error) {
    await client.query('ROLLBACK').catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}

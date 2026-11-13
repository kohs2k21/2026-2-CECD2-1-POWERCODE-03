import { readFile } from 'node:fs/promises';
import path from 'node:path';
import pg from 'pg';
import { ResponseCodeFileError, readSheetFile } from '../responsecodes/responseCodeFile.js';
import { ResponseCodeImportError, importResponseCodes, parseImportRows } from '../responsecodes/responseCodeImport.js';

/**
 * 응답 코드 사전 가져오기 도구(contracts/response-codes-ui.md 6절, 실행 절차는 infra/database/README.md "025 응답 코드 사전").
 *   node dist/scripts/importResponseCodes.js <파일.xlsx|.csv> [--dry-run] [--force]
 * - DATABASE_URL은 환경 변수로만 받는다(.env를 읽지 않음). API 실행 역할(powercode_api) 권한으로 충분하다.
 * - 파일은 저장소 밖 경로를 준다(고객 자료). 출력은 건수·행 번호·사유 코드만(셀 값은 쓰지 않는다).
 * - --dry-run: 검사와 반영 건수 계산만 하고 ROLLBACK. --force: 화면에서 고친 메시지(origin edited)도 파일 값으로 덮는다.
 * 종료 코드: 0 성공, 1 사용법·파일·DB 오류, 2 잘못된 행이 있어 반영하지 않음(--dry-run이 아니면 아무것도 쓰지 않는다).
 */
const usage = 'usage: node dist/scripts/importResponseCodes.js <file.xlsx|file.csv> [--dry-run] [--force]';
const ACTOR = 'system:import';

async function main(argv: string[]) {
  const flags = new Set(argv.filter((arg) => arg.startsWith('--')));
  const files = argv.filter((arg) => !arg.startsWith('--'));
  if (files.length !== 1 || [...flags].some((flag) => !['--dry-run', '--force'].includes(flag))) { console.error(usage); return 1; }
  const url = process.env.DATABASE_URL?.trim();
  if (!url) { console.error('DATABASE_URL is required (environment variable)'); return 1; }
  const file = path.resolve(files[0]);
  let parsed;
  try {
    parsed = parseImportRows(readSheetFile(file, await readFile(file)));
  } catch (error) {
    if (error instanceof ResponseCodeFileError || error instanceof ResponseCodeImportError) { console.error(`file error: ${error.code}`); return 1; }
    console.error(`file error: ${(error as NodeJS.ErrnoException).code ?? 'read_failed'}`);
    return 1;
  }
  console.log(`header row: ${parsed.headerRow}`);
  console.log(`rows read: ${parsed.rowsRead}`);
  console.log(`group title rows skipped: ${parsed.groupRows}`);
  console.log(`invalid rows: ${parsed.invalid.length}${parsed.invalid.length ? ` (${parsed.invalid.map((item) => `row ${item.row} ${item.reason}`).join(', ')})` : ''}`);
  console.log(`merged codes: ${parsed.merged}${parsed.merged ? ` (rows ${parsed.mergedRows.join(', ')})` : ''}`);
  console.log(`numeric code cells: ${parsed.numericCodeCells}`);
  console.log(`codes to import: ${parsed.entries.length}`);
  if (parsed.invalid.length) { console.error('not imported: fix the invalid rows first'); return 2; }
  const pool = new pg.Pool({ connectionString: url, max: 1, connectionTimeoutMillis: 10000 });
  try {
    const counts = await importResponseCodes(pool, parsed.entries, { force: flags.has('--force'), dryRun: flags.has('--dry-run'), actorId: ACTOR });
    console.log(`${flags.has('--dry-run') ? 'dry run (rolled back)' : 'imported'}: inserted ${counts.inserted}, updated ${counts.updated}, unchanged ${counts.unchanged}, ` +
      `kept edited ${counts.keptEdited}, overwritten edited ${counts.overwrittenEdited}`);
    return 0;
  } catch (error) {
    // 연결 문자열·값이 섞일 수 있어 SQLSTATE만 쓴다.
    console.error(`database error: ${(error as { code?: string }).code ?? 'unknown'}`);
    return 1;
  } finally {
    await pool.end();
  }
}

process.exitCode = await main(process.argv.slice(2));

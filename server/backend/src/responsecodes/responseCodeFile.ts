import zlib from 'node:zlib';

/**
 * 응답 코드 사전 가져오기 파일 읽기(contracts/response-codes-ui.md 6절). 의존성 없이 .xlsx(첫 시트)·.csv(UTF-8)만 읽는다.
 * 값은 그대로 돌려주고 해석·검사는 responseCodeImport.ts가 한다. 오류 메시지에 셀 값을 넣지 않는다(고객 자료).
 */
export type SheetRow = {
  /** 1부터 세는 파일 행 번호(xlsx는 행 r 속성, csv는 레코드 순번). */
  row: number;
  /** 열 순서대로 셀 글자. 빈 셀은 ''. */
  cells: string[];
  /** 숫자 셀(xlsx t 없음·n). 코드 열이 숫자 셀이면 앞자리 0이 사라졌을 수 있어 가져오기가 따로 센다. */
  numeric: boolean[];
};

export class ResponseCodeFileError extends Error {
  constructor(readonly code: string) { super(code); }
}

/** 파일·압축 해제 상한. 사전은 수백 행이라 넉넉하다. */
const FILE_BYTES_MAX = 20 * 1024 * 1024;
const ENTRY_BYTES_MAX = 64 * 1024 * 1024;

type ZipEntry = { name: string; method: number; compressedSize: number; size: number; offset: number };

function zipEntries(buffer: Buffer): Map<string, ZipEntry> {
  // 끝 레코드(EOCD)는 마지막 22바이트 + 주석(최대 65535) 안에 있다.
  let eocd = -1;
  for (let i = buffer.length - 22; i >= Math.max(0, buffer.length - 22 - 65535); i -= 1) {
    if (buffer.readUInt32LE(i) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new ResponseCodeFileError('xlsx_not_zip');
  const count = buffer.readUInt16LE(eocd + 10);
  let at = buffer.readUInt32LE(eocd + 16);
  if (count === 0xffff || at === 0xffffffff) throw new ResponseCodeFileError('xlsx_zip64_unsupported');
  const entries = new Map<string, ZipEntry>();
  for (let n = 0; n < count; n += 1) {
    if (at + 46 > buffer.length || buffer.readUInt32LE(at) !== 0x02014b50) throw new ResponseCodeFileError('xlsx_zip_corrupt');
    const nameLength = buffer.readUInt16LE(at + 28);
    const extraLength = buffer.readUInt16LE(at + 30);
    const commentLength = buffer.readUInt16LE(at + 32);
    const name = buffer.toString('utf8', at + 46, at + 46 + nameLength);
    entries.set(name, {
      name, method: buffer.readUInt16LE(at + 10), compressedSize: buffer.readUInt32LE(at + 20), size: buffer.readUInt32LE(at + 24),
      offset: buffer.readUInt32LE(at + 42),
    });
    at += 46 + nameLength + extraLength + commentLength;
  }
  return entries;
}

function zipRead(buffer: Buffer, entries: Map<string, ZipEntry>, name: string): string | null {
  const entry = entries.get(name);
  if (!entry) return null;
  if (entry.size > ENTRY_BYTES_MAX) throw new ResponseCodeFileError('xlsx_entry_too_large');
  const at = entry.offset;
  if (at + 30 > buffer.length || buffer.readUInt32LE(at) !== 0x04034b50) throw new ResponseCodeFileError('xlsx_zip_corrupt');
  const start = at + 30 + buffer.readUInt16LE(at + 26) + buffer.readUInt16LE(at + 28);
  const data = buffer.subarray(start, start + entry.compressedSize);
  if (entry.method === 0) return data.toString('utf8');
  if (entry.method !== 8) throw new ResponseCodeFileError('xlsx_compression_unsupported');
  return zlib.inflateRawSync(data, { maxOutputLength: ENTRY_BYTES_MAX }).toString('utf8');
}

const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" };
export const decodeXml = (text: string) => text.replace(/&(#x[0-9a-fA-F]+|#\d+|[a-z]+);/g, (whole, body: string) => {
  if (body[0] === '#') {
    const point = body[1] === 'x' ? parseInt(body.slice(2), 16) : parseInt(body.slice(1), 10);
    return Number.isFinite(point) && point <= 0x10ffff ? String.fromCodePoint(point) : whole;
  }
  return ENTITIES[body] ?? whole;
});
/** 글자 조각(<t>) 이어 붙이기. 읽는 법 보조(<rPh>)는 뺀다. */
const textRuns = (xml: string) => [...xml.replace(/<rPh\b[\s\S]*?<\/rPh>/g, '').matchAll(/<t\b[^>]*>([\s\S]*?)<\/t>|<t\b[^>]*\/>/g)]
  .map((match) => decodeXml(match[1] ?? '')).join('');
const attribute = (attributes: string, name: string) => new RegExp(`(?:^|\\s)${name}="([^"]*)"`).exec(attributes)?.[1] ?? null;
const columnIndex = (letters: string) => [...letters].reduce((total, letter) => total * 26 + (letter.charCodeAt(0) - 64), 0) - 1;

/** 첫 시트(통합 문서 순서 첫 번째)의 행. 수식 셀은 저장된 결과 값을 읽는다. */
export function readXlsx(buffer: Buffer): SheetRow[] {
  if (buffer.length > FILE_BYTES_MAX) throw new ResponseCodeFileError('file_too_large');
  const entries = zipEntries(buffer);
  const workbook = zipRead(buffer, entries, 'xl/workbook.xml');
  if (workbook === null) throw new ResponseCodeFileError('xlsx_workbook_missing');
  const sheet = /<sheet\b([^>]*?)\/?>/.exec(workbook);
  const relationId = sheet ? attribute(sheet[1], 'r:id') : null;
  if (!relationId) throw new ResponseCodeFileError('xlsx_sheet_missing');
  const relations = zipRead(buffer, entries, 'xl/_rels/workbook.xml.rels') ?? '';
  const relation = [...relations.matchAll(/<Relationship\b([^>]*?)\/?>/g)].find((match) => attribute(match[1], 'Id') === relationId);
  const target = relation ? attribute(relation[1], 'Target') : null;
  if (!target) throw new ResponseCodeFileError('xlsx_sheet_missing');
  const sheetPath = target.startsWith('/') ? target.slice(1) : `xl/${target}`.replace(/\/\.\//g, '/');
  const sheetXml = zipRead(buffer, entries, sheetPath);
  if (sheetXml === null) throw new ResponseCodeFileError('xlsx_sheet_missing');
  const sharedXml = zipRead(buffer, entries, 'xl/sharedStrings.xml') ?? '';
  const shared = [...sharedXml.matchAll(/<si\b[^>]*>([\s\S]*?)<\/si>|<si\b[^>]*\/>/g)].map((match) => textRuns(match[1] ?? ''));

  const rows: SheetRow[] = [];
  let previous = 0;
  for (const match of sheetXml.matchAll(/<row\b([^>]*?)(?:\/>|>([\s\S]*?)<\/row>)/g)) {
    const rowNumber = Number(attribute(match[1], 'r') ?? previous + 1);
    previous = rowNumber;
    const cells: string[] = [];
    const numeric: boolean[] = [];
    let column = -1;
    for (const cell of (match[2] ?? '').matchAll(/<c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g)) {
      const reference = attribute(cell[1], 'r');
      column = reference ? columnIndex(/^[A-Z]+/.exec(reference)?.[0] ?? 'A') : column + 1;
      const type = attribute(cell[1], 't') ?? 'n';
      const body = cell[2] ?? '';
      const raw = /<v\b[^>]*>([\s\S]*?)<\/v>/.exec(body)?.[1];
      let value = '';
      if (type === 's') value = raw === undefined ? '' : (shared[Number(raw)] ?? '');
      else if (type === 'inlineStr') value = textRuns(/<is\b[^>]*>([\s\S]*?)<\/is>/.exec(body)?.[1] ?? '');
      else value = raw === undefined ? '' : decodeXml(raw);
      while (cells.length < column) { cells.push(''); numeric.push(false); }
      cells[column] = value;
      numeric[column] = type === 'n' && value !== '';
    }
    rows.push({ row: rowNumber, cells, numeric });
  }
  return rows;
}

/** RFC 4180 CSV(UTF-8, BOM 허용, 따옴표 안 줄바꿈·"" 이스케이프). */
export function readCsv(buffer: Buffer): SheetRow[] {
  if (buffer.length > FILE_BYTES_MAX) throw new ResponseCodeFileError('file_too_large');
  const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: false }).decode(buffer);
  const rows: SheetRow[] = [];
  let cells: string[] = [];
  let cell = '';
  let quoted = false;
  let started = false;
  const endCell = () => { cells.push(cell); cell = ''; };
  const endRow = () => {
    endCell();
    rows.push({ row: rows.length + 1, cells, numeric: cells.map(() => false) });
    cells = [];
    started = false;
  };
  for (let i = 0; i < text.length; i += 1) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') { cell += '"'; i += 1; }
      else if (char === '"') quoted = false;
      else cell += char;
      continue;
    }
    if (char === '"' && cell === '') { quoted = true; started = true; }
    else if (char === ',') { endCell(); started = true; }
    else if (char === '\r' && text[i + 1] === '\n') { endRow(); i += 1; }
    else if (char === '\n' || char === '\r') endRow();
    else { cell += char; started = true; }
  }
  if (quoted) throw new ResponseCodeFileError('csv_unterminated_quote');
  if (started || cell !== '' || cells.length) endRow();
  return rows;
}

/** 확장자로 형식을 고른다(.xlsx | .csv). */
export function readSheetFile(fileName: string, buffer: Buffer): SheetRow[] {
  const lower = fileName.toLowerCase();
  if (lower.endsWith('.xlsx')) return readXlsx(buffer);
  if (lower.endsWith('.csv')) return readCsv(buffer);
  throw new ResponseCodeFileError('unsupported_file_type');
}

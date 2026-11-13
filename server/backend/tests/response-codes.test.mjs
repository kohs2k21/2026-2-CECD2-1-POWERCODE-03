// 응답 코드 사전(contracts/response-codes-ui.md) 단위 시험: 파일 읽기(.xlsx 직접 만든 합성 zip·.csv), 머리글 이름 대응·묶음 제목 행·잘못된 행·겹치는 코드 합치기,
// 조회 조건·편집 본문 검사, 코드별 위험도(byCode → default). 값은 모두 합성(T001·E500 등) — 실제 사전 값은 쓰지 않는다. 실행 전 `npm run build`.
import assert from 'node:assert/strict';
import test from 'node:test';
import zlib from 'node:zlib';

const { readXlsx, readCsv, readSheetFile, decodeXml, ResponseCodeFileError } = await import('../dist/responsecodes/responseCodeFile.js');
const { parseImportRows, ResponseCodeImportError, MERGE_SEPARATOR } = await import('../dist/responsecodes/responseCodeImport.js');
const {
  parseListQuery, parseMessageEdit, parseCodeParam, severityFor, failedSeverityOf, encodeCursor, ResponseCodeError,
} = await import('../dist/responsecodes/responseCodeContract.js');

/** 최소 zip(로컬 머리 + 중앙 디렉터리 + 끝 레코드). stored = 압축 없이 넣을 이름. */
function zip(files, stored = new Set()) {
  const locals = [];
  const centrals = [];
  let offset = 0;
  for (const [name, text] of Object.entries(files)) {
    const raw = Buffer.from(text, 'utf8');
    const method = stored.has(name) ? 0 : 8;
    const data = method === 0 ? raw : zlib.deflateRawSync(raw);
    const nameBytes = Buffer.from(name, 'utf8');
    const crc = typeof zlib.crc32 === 'function' ? zlib.crc32(raw) : 0;
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4); local.writeUInt16LE(method, 8);
    local.writeUInt32LE(crc, 14); local.writeUInt32LE(data.length, 18); local.writeUInt32LE(raw.length, 22); local.writeUInt16LE(nameBytes.length, 26);
    const central = Buffer.alloc(46);
    central.writeUInt32LE(0x02014b50, 0); central.writeUInt16LE(20, 4); central.writeUInt16LE(20, 6); central.writeUInt16LE(method, 10);
    central.writeUInt32LE(crc, 16); central.writeUInt32LE(data.length, 20); central.writeUInt32LE(raw.length, 24); central.writeUInt16LE(nameBytes.length, 28);
    central.writeUInt32LE(offset, 42);
    locals.push(local, nameBytes, data);
    centrals.push(central, nameBytes);
    offset += local.length + nameBytes.length + data.length;
  }
  const directory = Buffer.concat(centrals);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(Object.keys(files).length, 8); end.writeUInt16LE(Object.keys(files).length, 10);
  end.writeUInt32LE(directory.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, directory, end]);
}
const header = ['코드명 (Enum)', '유형 (Type)', '에러코드', 'HTTP 상태', '메시지 키', '메시지 (한국어)'];
const shared = [...header, '공통 (SYNTHETIC)', 'SYN_FIRST', 'SYN_TYPE', 'T001', '500 SYN_ERROR', 'syn.first', '합성 & 메시지 <1>', 'SYN_SECOND', 'E500'];
const si = (text) => `<si><t xml:space="preserve">${text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')}</t></si>`;
const s = (ref, index) => `<c r="${ref}" t="s"><v>${index}</v></c>`;
const xlsx = () => zip({
  'xl/workbook.xml': '<workbook xmlns:r="r"><sheets><sheet name="코드" sheetId="1" r:id="rId7"/><sheet name="다른 시트" sheetId="2" r:id="rId8"/></sheets></workbook>',
  'xl/_rels/workbook.xml.rels': '<Relationships><Relationship Id="rId8" Target="worksheets/sheet2.xml"/><Relationship Id="rId7" Type="t" Target="worksheets/sheet1.xml"/></Relationships>',
  'xl/sharedStrings.xml': `<sst>${shared.map(si).join('')}<si><r><t>합성</t></r><r><t> 둘째</t></r><rPh><t>읽는 법</t></rPh></si></sst>`,
  'xl/worksheets/sheet1.xml': `<worksheet><sheetData>
    <row r="1">${header.map((_, i) => s(`${'ABCDEF'[i]}1`, i)).join('')}</row>
    <row r="2">${s('A2', 6)}</row>
    <row r="3">${s('A3', 7)}${s('B3', 8)}${s('C3', 9)}${s('D3', 10)}${s('E3', 11)}${s('F3', 12)}</row>
    <row r="5"><c r="A5" t="inlineStr"><is><t>SYN_INLINE</t></is></c><c r="C5"><v>10</v></c><c r="F5" t="s"><v>15</v></c></row>
    <row r="6"/>
    <row r="7">${s('A7', 13)}${s('C7', 14)}<c r="F7" t="str"><v>수식 결과 &amp; 값</v></c></row>
  </sheetData></worksheet>`,
  'xl/worksheets/sheet2.xml': '<worksheet><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>무시</t></is></c></row></sheetData></worksheet>',
}, new Set(['xl/workbook.xml']));

test('xlsx: first sheet by workbook order, shared/inline/str/number cells, sparse columns, rich text without phonetic runs', () => {
  const rows = readXlsx(xlsx());
  assert.deepEqual(rows.map((row) => row.row), [1, 2, 3, 5, 6, 7]);
  assert.deepEqual(rows[0].cells, header);
  assert.deepEqual(rows[2].cells, ['SYN_FIRST', 'SYN_TYPE', 'T001', '500 SYN_ERROR', 'syn.first', '합성 & 메시지 <1>']);
  assert.deepEqual(rows[3].cells, ['SYN_INLINE', '', '10', '', '', '합성 둘째']);
  assert.deepEqual(rows[3].numeric, [false, false, true, false, false, false], 'a cell without t is numeric');
  assert.deepEqual(rows[4].cells, []);
  assert.deepEqual(rows[5].cells, ['SYN_SECOND', '', 'E500', '', '', '수식 결과 & 값']);
  assert.equal(decodeXml('&#x41;&#66;&amp;&unknown;'), 'AB&&unknown;');
});

test('xlsx: not a zip, missing workbook, unsupported type', () => {
  assert.throws(() => readXlsx(Buffer.from('not a zip at all, just text.......')), (e) => e instanceof ResponseCodeFileError && e.code === 'xlsx_not_zip');
  assert.throws(() => readXlsx(zip({ 'a.txt': 'x' })), (e) => e.code === 'xlsx_workbook_missing');
  assert.throws(() => readSheetFile('codes.xls', Buffer.alloc(0)), (e) => e.code === 'unsupported_file_type');
});

test('csv: BOM, quotes with commas/newlines/escaped quotes, CRLF, trailing newline', () => {
  const rows = readCsv(Buffer.from('﻿에러코드,메시지\r\nT001,"합성, ""따옴표""\n둘째 줄"\r\n,\nE500,끝', 'utf8'));
  assert.deepEqual(rows.map((row) => row.cells), [['에러코드', '메시지'], ['T001', '합성, "따옴표"\n둘째 줄'], ['', ''], ['E500', '끝']]);
  assert.deepEqual(readCsv(Buffer.from('a,b\n')).map((row) => row.cells), [['a', 'b']]);
  assert.throws(() => readCsv(Buffer.from('a,"b\n')), (e) => e.code === 'csv_unterminated_quote');
  assert.throws(() => readCsv(Buffer.from([0x61, 0xff, 0x62])), TypeError, 'invalid UTF-8 is rejected');
});

test('import rows: header names, group rows skipped, invalid rows, repeated code merged, numeric code cells counted', () => {
  const parsed = parseImportRows(readXlsx(xlsx()));
  assert.equal(parsed.headerRow, 1);
  assert.deepEqual([parsed.rowsRead, parsed.groupRows, parsed.invalid.length, parsed.merged, parsed.numericCodeCells], [4, 1, 0, 0, 1]);
  assert.deepEqual(parsed.entries.map((entry) => [entry.row, entry.code, entry.enumName, entry.codeType, entry.httpStatus, entry.messageKey]),
    [[3, 'T001', 'SYN_FIRST', 'SYN_TYPE', '500 SYN_ERROR', 'syn.first'], [5, '10', 'SYN_INLINE', null, null, null], [7, 'E500', 'SYN_SECOND', null, null, null]]);

  const csv = (lines) => parseImportRows(readCsv(Buffer.from(lines.join('\n'), 'utf8')));
  const mixed = csv([
    '제목 줄,,', ' Code ,Type, Message ', 'T001,SYN,첫 값', '분류 이름,,', 'T001,SYN,다시 나온 코드', ',SYN,코드 없음', 'T 2,SYN,공백 코드', 'T003,SYN,',
    `T004,${'t'.repeat(65)},유형이 김`, `T005,SYN,${'가'.repeat(501)}`, 'T006,SYN,"제어\u0007문자"', '  T007  ,  ,  앞뒤 공백  ',
  ]);
  assert.equal(mixed.headerRow, 2, 'English aliases, case and spaces ignored; header found below a title row');
  assert.deepEqual(mixed.entries.map((entry) => [entry.row, entry.code, entry.codeType, entry.message]), [[3, 'T001', 'SYN', '첫 값 또는 다시 나온 코드'], [12, 'T007', null, '앞뒤 공백']]);
  assert.deepEqual([mixed.merged, mixed.mergedRows], [1, [5]]);
  assert.deepEqual(mixed.invalid, [
    { row: 6, reason: 'code_missing' }, { row: 7, reason: 'code_invalid' }, { row: 8, reason: 'message_missing' },
    { row: 9, reason: 'code_type_invalid' }, { row: 10, reason: 'message_invalid' }, { row: 11, reason: 'message_invalid' },
  ]);
  assert.deepEqual([mixed.rowsRead, mixed.groupRows], [10, 1]);
  assert.throws(() => csv(['코드,유형', 'T001,SYN']), (e) => e instanceof ResponseCodeImportError && e.code === 'header_not_found');
  assert.throws(() => csv(['코드,에러코드,메시지', 'T001,T001,m']), (e) => e.code === 'header_duplicate_column');
});

test('import rows: same code on several rows -> message, enum name and message key joined with " 또는 ", type and HTTP status must match', () => {
  const csv = (lines) => parseImportRows(readCsv(Buffer.from(['코드,코드명,유형,HTTP 상태,메시지 키,메시지', ...lines].join('\n'), 'utf8')));
  assert.equal(MERGE_SEPARATOR, ' 또는 ');
  const merged = csv([
    'M001,SYN_A,SYN,400,syn.a,합성 가',
    'M002,SYN_C,SYN,500,syn.c,합성 다',
    'M001,SYN_B,SYN,400,syn.b,합성 나',
    'M001,SYN_A,SYN,400,,합성 가',
    'M001,,SYN,400,syn.c,합성 라',
    'M003,,SYN,,,합성 마',
    'M003,SYN_E,SYN,,syn.e,합성 마',
  ]);
  assert.deepEqual(merged.invalid, []);
  assert.deepEqual([merged.merged, merged.mergedRows, merged.rowsRead], [2, [4, 5, 6, 8], 7]);
  assert.deepEqual(merged.entries.map((entry) => [entry.row, entry.code, entry.enumName, entry.codeType, entry.httpStatus, entry.messageKey, entry.message]), [
    [2, 'M001', 'SYN_A 또는 SYN_B', 'SYN', '400', 'syn.a 또는 syn.b 또는 syn.c', '합성 가 또는 합성 나 또는 합성 라'],
    [3, 'M002', 'SYN_C', 'SYN', '500', 'syn.c', '합성 다'],
    [7, 'M003', 'SYN_E', 'SYN', null, 'syn.e', '합성 마'],
  ], 'file order, values already present not repeated, empty cells take the other row value');
  const natural = csv(['M001,,SYN,,,가 또는 나', 'M001,,SYN,,,나']);
  assert.equal(natural.entries[0].message, '가 또는 나 또는 나', 'compares whole row values, not split pieces of a message');

  const conflict = csv([
    'M001,SYN_A,SYN,400,syn.a,합성 가', 'M001,SYN_A,OTHER,400,syn.a,합성 나', 'M001,SYN_A,SYN,500,syn.a,합성 나', 'M001,SYN_A,,400,syn.a,합성 나',
    'M001,SYN_A,SYN,,syn.a,합성 나',
    `M001,SYN_A,SYN,400,syn.a,${'나'.repeat(500 - [...'합성 가 또는 '].length + 1)}`,
    `M001,${'E'.repeat(128 - 'SYN_A 또는 '.length + 1)},SYN,400,syn.a,합성 가`,
    `M001,SYN_A,SYN,400,${'k'.repeat(256 - 'syn.a 또는 '.length + 1)},합성 가`,
    `M001,SYN_A,SYN,400,syn.a,${'나'.repeat(500 - [...'합성 가 또는 '].length)}`,
  ]);
  assert.deepEqual(conflict.invalid, [
    { row: 3, reason: 'duplicate_code_type_mismatch' }, { row: 4, reason: 'duplicate_http_status_mismatch' }, { row: 5, reason: 'duplicate_code_type_mismatch' },
    { row: 6, reason: 'duplicate_http_status_mismatch' }, { row: 7, reason: 'merged_message_too_long' }, { row: 8, reason: 'merged_enum_name_too_long' },
    { row: 9, reason: 'merged_message_key_too_long' },
  ]);
  assert.deepEqual([conflict.merged, conflict.mergedRows], [1, [10]], 'a rejected row leaves the first row unchanged; exactly 500 characters is accepted');
  assert.equal([...conflict.entries[0].message].length, 500);
  assert.deepEqual([conflict.entries[0].enumName, conflict.entries[0].messageKey], ['SYN_A', 'syn.a']);
});

test('list query, cursor, code path and message edit body', () => {
  assert.deepEqual(parseListQuery({}), { q: null, type: null, limit: 50, after: null });
  assert.deepEqual(parseListQuery({ q: '  ABC 가 ', type: 'SYN', limit: '200', cursor: encodeCursor('T001') }), { q: 'abc 가', type: 'SYN', limit: 200, after: 'T001' });
  assert.equal(parseListQuery({ q: '   ' }).q, null);
  for (const [query, code] of [[{ sort: 'x' }, 'unknown_response_code_filter'], [{ limit: '0' }, 'invalid_limit'], [{ limit: '201' }, 'invalid_limit'],
    [{ q: 'x'.repeat(101) }, 'invalid_query'], [{ q: ['a', 'b'] }, 'invalid_query'], [{ type: '' }, 'invalid_type'], [{ cursor: 'not json' }, 'invalid_cursor'],
    [{ cursor: Buffer.from('{"code":"a b"}').toString('base64url') }, 'invalid_cursor'], [{ cursor: Buffer.from('{"code":"T1","x":1}').toString('base64url') }, 'invalid_cursor']]) {
    assert.throws(() => parseListQuery(query), (e) => e instanceof ResponseCodeError && e.code === code, JSON.stringify(query));
  }
  assert.equal(parseCodeParam('~T/%01'), '~T/%01');
  for (const value of ['', 'T 1', '코드', 'x'.repeat(65), undefined]) assert.throws(() => parseCodeParam(value), (e) => e.code === 'invalid_response_code');
  assert.deepEqual(parseMessageEdit({ message: '  새 합성 메시지 ', expectedRevision: 3 }), { message: '새 합성 메시지', expectedRevision: 3 });
  assert.deepEqual(parseMessageEdit({ expectedRevision: null, message: 'm' }), { message: 'm', expectedRevision: null });
  for (const body of [null, [], {}, { message: 'm' }, { message: 'm', expectedRevision: 0 }, { message: 'm', expectedRevision: '1' }, { message: 1, expectedRevision: 1 },
    { message: 'm', expectedRevision: 1, extra: true }]) {
    assert.throws(() => parseMessageEdit(body), (e) => e.code === 'invalid_response_code_edit', JSON.stringify(body));
  }
  for (const message of ['  ', '가'.repeat(501), '줄\n바꿈', '\ud800']) {
    assert.throws(() => parseMessageEdit({ message, expectedRevision: 1 }), (e) => e.code === 'response_code_message_invalid');
  }
});

test('severity per code: byCode first, otherwise the default level; stored shapes', () => {
  const severity = { byCode: { T001: 'critical', 10: 'info' }, default: 'warning' };
  assert.deepEqual(severityFor('T001', severity), { level: 'critical', source: 'code' });
  assert.deepEqual(severityFor('10', severity), { level: 'info', source: 'code' });
  assert.deepEqual(severityFor('E500', severity), { level: 'warning', source: 'default' });
  assert.deepEqual(severityFor('constructor', severity), { level: 'warning', source: 'default' }, 'prototype keys are not codes');
  assert.deepEqual(failedSeverityOf({ byCode: {}, default: 'info' }), { byCode: {}, default: 'info' });
  for (const value of ['critical', null, [], { byCode: {} }, { byCode: [], default: 'info' }, { byCode: { T: 'high' }, default: 'info' }, { byCode: {}, default: 'high' }]) {
    assert.equal(failedSeverityOf(value), null, JSON.stringify(value));
  }
});

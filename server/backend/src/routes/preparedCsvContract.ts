import crypto from 'node:crypto';

export const PREPARED_LIMITS = { csvBytes: 5 * 1024 * 1024, manifestBytes: 65536, rows: 10000, features: 32, columns: 64, validationSeconds: 20 };
export const PREPARED_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const HASH = /^[0-9a-f]{64}$/;
const COLUMN = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/;
type ObjectValue = Record<string, any>;
export class PreparedRequestError extends Error {
  constructor(readonly code: string, readonly status = 400) { super(code); }
}
function object(value: unknown, required: string[], optional: string[] = []): ObjectValue {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      required.some(key => !Object.prototype.hasOwnProperty.call(value, key)) ||
      Object.keys(value).some(key => !required.includes(key) && !optional.includes(key))) {
    throw new PreparedRequestError('prepared_invalid_manifest');
  }
  return value as ObjectValue;
}
const text = (value: unknown): value is string => typeof value === 'string' && value.length > 0 && value.trim() === value && Array.from(value).length <= 256 && !/[\u0000-\u001f]/.test(value);
const nullableHash = (value: unknown) => value === null || typeof value === 'string' && HASH.test(value);

export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonicalJson).join(',') + ']';
  if (value !== null && typeof value === 'object') return '{' + Object.keys(value).sort().map(key =>
    JSON.stringify(key) + ':' + canonicalJson((value as ObjectValue)[key])).join(',') + '}';
  // The bounded manifest intensity uses Python JSON's small-number notation.
  if (typeof value === 'number' && value !== 0 && Math.abs(value) < 1e-4) {
    return value.toExponential().replace(/e([+-])(\d)$/, 'e$10$2');
  }
  return JSON.stringify(value);
}
export const preparedHash = (value: string | Buffer) => crypto.createHash('sha256').update(value).digest('hex');

export function parsePreparedManifest(value: unknown): ObjectValue {
  let size: number;
  try { size = Buffer.byteLength(JSON.stringify(value)); } catch { throw new PreparedRequestError('prepared_invalid_manifest'); }
  if (size > PREPARED_LIMITS.manifestBytes) throw new PreparedRequestError('prepared_manifest_too_large', 413);
  const m = object(value, ['contractVersion','sourceId','sourceSchemaVersion','domain','rowCount','inputStage','features','preprocessing','split','columns'], ['synthetic','labelSource']);
  if (m.contractVersion !== 'prepared-csv-v1' || typeof m.sourceId !== 'string' || !PREPARED_UUID.test(m.sourceId) ||
      !text(m.sourceSchemaVersion) || !text(m.domain) || m.inputStage !== 'model_input' ||
      !Number.isInteger(m.rowCount) || m.rowCount < 1 || m.rowCount > PREPARED_LIMITS.rows ||
      !Array.isArray(m.features) || !m.features.length || m.features.length > PREPARED_LIMITS.features) throw new PreparedRequestError('prepared_invalid_manifest');
  for (const feature of m.features) {
    const f = object(feature, ['name','type','unit']);
    if (typeof f.name !== 'string' || !COLUMN.test(f.name) || f.type !== 'number' || !text(f.unit)) throw new PreparedRequestError('prepared_invalid_manifest');
  }
  const p = object(m.preprocessing, ['definitionHash','scaling','fitHash','fitSplit','trainRowIdsHash']);
  if (typeof p.definitionHash !== 'string' || !HASH.test(p.definitionHash) || !['none','applied'].includes(p.scaling) ||
      !nullableHash(p.fitHash) || !['train','unknown'].includes(p.fitSplit) || !nullableHash(p.trainRowIdsHash)) throw new PreparedRequestError('prepared_invalid_manifest');
  const split = object(m.split, ['version','timeBasis']);
  if (split.version !== 'provided-v1' || !['occurred_at','unspecified'].includes(split.timeBasis)) throw new PreparedRequestError('prepared_invalid_manifest');
  const columns = object(m.columns, ['rowId'], ['split','label','status','isSynthetic','scenario','originRowId','groupId','occurredAt']);
  if (typeof columns.rowId !== 'string' || !COLUMN.test(columns.rowId) ||
      Object.values(columns).some(name => name !== null && (typeof name !== 'string' || !COLUMN.test(name)))) throw new PreparedRequestError('prepared_invalid_manifest');
  const names = [...m.features.map((f: ObjectValue) => f.name), ...Object.values(columns).filter(name => name !== null)];
  if (new Set(names).size !== names.length || names.length > PREPARED_LIMITS.columns) throw new PreparedRequestError('prepared_duplicate_columns');
  if (m.labelSource !== undefined) {
    const labels = object(m.labelSource, ['kind','reference']);
    if (!['manual','synthetic','unknown'].includes(labels.kind) || !(labels.reference === null || text(labels.reference))) throw new PreparedRequestError('prepared_invalid_manifest');
  }
  if (m.synthetic !== undefined) {
    const s = object(m.synthetic, ['generator','version','seed','intensity','baselineReference']);
    if (!text(s.generator) || !text(s.version) || !text(s.baselineReference) || !Number.isInteger(s.seed) || s.seed < 0 || s.seed > 2147483647 ||
        typeof s.intensity !== 'number' || !Number.isFinite(s.intensity) || s.intensity < 0 || s.intensity > 1000000) throw new PreparedRequestError('prepared_invalid_manifest');
  }
  // Source UUIDs have one canonical representation for DB/source binding and idempotency.
  return JSON.parse(canonicalJson({ ...m, sourceId: m.sourceId.toLowerCase() }));
}

export function parsePreparedUpload(value: unknown) {
  const body = object(value, ['csvText','manifest']);
  if (typeof body.csvText !== 'string' || !body.csvText.length || body.csvText.includes('\u0000') || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(body.csvText)) throw new PreparedRequestError('prepared_invalid_csv');
  const bytes = Buffer.from(body.csvText, 'utf8');
  if (bytes.length > PREPARED_LIMITS.csvBytes) throw new PreparedRequestError('prepared_csv_too_large', 413);
  const manifest = parsePreparedManifest(body.manifest);
  const originalHash = preparedHash(bytes);
  return { bytes, manifest, originalHash, manifestHash: preparedHash(canonicalJson(manifest)),
    requestHash: preparedHash(canonicalJson({ originalHash, manifest })) };
}

// 016 (#119): v04 detect path, NULL-config decisions, rule set hash version and system row r1, called from erd-v03.mjs inside the same disposable cluster.
// Databases: erd119_fresh (001-016 + role scripts, full flows by real LOGIN roles), erd119_upgrade (001-015 + v03/legacy rows -> 016),
// erd119_saved / erd119_empty (r1 precondition fails, nothing stays), erd119_rollback (016 fails before COMMIT), erd119_encoding (non-UTF8 session, statement by statement).
// Negative cases match the expected reason (message or constraint), not only the error class, so an unrelated rejection cannot pass.
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';

const RULE = 'ops_rule_set_revisions', VIEW = 'ops_operational_state';
const R0_HASH = '80d0377d69597900b7506c26761143077ae7306f464bc44d759bb62f7c83bc89';
const R1_HASH = 'de6a2ad8d0dcee189f36852a279263d3408f826632b95b4f14a97406b801bed0';
// rule-set.md v2 reference values (a') and (b), reproduced below before the serializer judges r1.
const REF_A = 'b6a9c84b21e7deb8df0d22bb6a11a5cd5fc80779eb4797f195505842bf605f3d';
const REF_B = '4cf61820400fb0de6eb41dca009cd1d20d8f29eccfcf609db8e4c6dd8098fe3d';
const STATUS_SEVERITY = { byCode:{ PROCESS_STATUS_F:'critical', PROCESS_STATUS_P:'warning', PROCESS_STATUS_Z:'warning', PROCESS_STATUS_H:'info', PROCESS_STATUS_N:'info' } };
// detection-decision.md 4.3 items with the rule-set.md `editable` field, written out here independently of the migration text.
const R1_HARD_RULES = [
  { id:'hard:process-status', methodVersion:'process-status-hr-v2', displayName:'상태가 S가 아님', conditionText:'STATUS ≠ S',
    severity:STATUS_SEVERITY, enabled:true, editable:['enabled','severity'] },
  { id:'hard:negative-duration', methodVersion:'negative-duration-v1', displayName:'시작 시각이 종료 시각보다 늦음',
    conditionText:'START_TIME > END_TIME (종료 시각이 없으면 판단하지 않음)', severity:'critical', enabled:true, editable:['enabled'] },
  { id:'hard:success-with-error', methodVersion:'success-with-error-v1', displayName:'성공 상태이나 오류 건수가 기록됨',
    conditionText:'STATUS = S, ERROR_COUNT > 0', severity:'critical', enabled:true, editable:['enabled'] },
  { id:'hard:count-conservation', methodVersion:'count-conservation-v1', displayName:'건수 합계 불일치',
    conditionText:'TOTAL_COUNT ≠ SUCCESS_COUNT + ERROR_COUNT', severity:'critical', enabled:true, editable:['enabled'] },
];
const R1_CHANGES = [
  { type:'hard_enabled', id:'hard:success-with-error', displayName:'성공 상태이나 오류 건수가 기록됨' },
  { type:'hard_enabled', id:'hard:count-conservation', displayName:'건수 합계 불일치' },
];
const R1_SUMMARY = { hardEnabled:2, hardDisabled:0, hardUpdated:0, softAdded:0, softUpdated:0, softRemoved:0, softEnabled:0, softDisabled:0 };
const VIEW_COLUMNS = 'rule_set_revision:int8,rule_set_content_hash:text,active_model_set_revision:int8,active_models:jsonb,detection_policy_revision:int8,detection_policy:jsonb,rule_set_content_hash_version:int2';

// json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False) for these ASCII objects.
const sorted = v => Array.isArray(v) ? v.map(sorted) : v && typeof v === 'object'
  ? Object.fromEntries(Object.keys(v).sort().map(k => [k, sorted(v[k])])) : v;
const byId = rows => [...rows].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
const canonicalV2 = ({ hardRules, softRules }) => JSON.stringify(sorted({
  hardRules: byId(hardRules).map(({ id, enabled, severity }) => ({ id, enabled, severity })),
  softRules: byId(softRules).map(({ id, field, operator, threshold, severity, enabled }) => ({ id, field, operator, threshold:String(threshold), severity, enabled })),
}));
const canonicalV1 = ({ hardRules }) => JSON.stringify(sorted({ hardRules:byId(hardRules).map(({ id, enabled }) => ({ id, enabled })), softRules:[] }));
const sha256 = text => createHash('sha256').update(text, 'utf8').digest('hex');

// Function bodies compared without comments and layout: 016 keeps code lines only.
export const code = s => s.replaceAll('\r\n', '\n').replace(/--.*$/gm, '').replace(/\s+/g, ' ').trim();
// The only intended source changes per redefined function: [before, after].
const FUNCTION_EDITS = {
  erd_v03_validate_job: [
    ["OLD.contract_version = 'v03' AND ROW(", 'OLD.contract_version IS NOT NULL AND ROW('],
    ["RAISE EXCEPTION 'v03 job input is immutable';", "RAISE EXCEPTION '% job input is immutable', OLD.contract_version;"],
    ["IF NEW.contract_version IS DISTINCT FROM 'v03' THEN RETURN NEW;", 'IF NEW.contract_version IS NULL THEN RETURN NEW;'],
    ["IF NEW.kind = 'detect' AND (SELECT source_id", "IF NEW.kind = 'detect' AND NEW.contract_version = 'v03' AND (SELECT source_id"],
  ],
  erd_v03_validate_result: [["IF j.contract_version IS DISTINCT FROM 'v03' OR j.status", "IF j.contract_version IS NULL OR j.contract_version NOT IN ('v03','v04') OR j.status"]],
  validate_decision_interim: [["IF j.contract_version IS DISTINCT FROM 'v03' OR j.kind <> 'detect' THEN RAISE EXCEPTION 'interim requires v03 detect job';",
    "IF j.contract_version IS NULL OR j.contract_version NOT IN ('v03','v04') OR j.kind <> 'detect' THEN RAISE EXCEPTION 'interim requires v03 or v04 detect job';"]],
};
export const catalog = async db => ({
  enums: (await db.query(`SELECT t.typname::text||':'||e.enumlabel::text AS v FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid ORDER BY 1`)).rows.map(r => r.v),
  columns: (await db.query(`SELECT table_schema||'.'||table_name||'.'||column_name||':'||udt_name||':'||is_nullable||':'||coalesce(column_default,'') AS v
    FROM information_schema.columns WHERE table_schema IN ('public','powercode_queue') ORDER BY 1`)).rows.map(r => r.v),
  constraints: (await db.query(`SELECT r.relname::text||'.'||c.conname::text||':'||c.convalidated::text||':'||pg_get_constraintdef(c.oid) AS v FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid ORDER BY 1`)).rows.map(r => r.v),
  triggers: (await db.query(`SELECT r.relname::text||'.'||t.tgname::text||':'||pg_get_triggerdef(t.oid) AS v FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid WHERE NOT t.tgisinternal ORDER BY 1`)).rows.map(r => r.v),
  indexes: (await db.query(`SELECT indexname||':'||indexdef AS v FROM pg_indexes WHERE schemaname IN ('public','powercode_queue') ORDER BY 1`)).rows.map(r => r.v),
  functions: (await db.query(`SELECT p.proname::text AS name, md5(p.prosrc) AS body, p.prosrc AS src, p.proconfig AS config, p.prosecdef AS definer, pg_get_userbyid(p.proowner) AS owner
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname IN ('public','powercode_queue') ORDER BY 1`)).rows,
});
export const delta = (before, after) => ({ removed:before.filter(v => !after.includes(v)), added:after.filter(v => !before.includes(v)) });
export const nameOf = v => v.split(':')[0];

// Top-level statements as psql -f sends them: skips -- comments, keeps '...' and $$...$$ whole.
export const statements = text => {
  const out = []; let cur = '', i = 0, mode = 'code';
  while (i < text.length) {
    if (mode === 'code' && text.startsWith('--', i)) { i = text.indexOf('\n', i); if (i < 0) break; continue; }
    if (mode !== 'quote' && text.startsWith('$$', i)) { mode = mode === 'code' ? 'dollar' : 'code'; cur += '$$'; i += 2; continue; }
    const ch = text[i++]; cur += ch;
    if (mode !== 'dollar' && ch === "'") mode = mode === 'code' ? 'quote' : 'code';
    if (mode === 'code' && ch === ';') { if (cur.trim()) out.push(cur.trim()); cur = ''; }
  }
  return out;
};

export async function detectV04_016({ Client, port, connection, check, failures, migrations, migrationNames, rolesSql, parity, insert }) {
  const idx = migrationNames.findIndex(n => n.startsWith('016_'));
  assert(idx > 0, '016 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  // why: regular expression over "<constraint> <message>". Statement must fail with this code for this reason.
  const no = async (label, codeWanted, why, run) => {
    try { await run(); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === codeWanted && why.test(`${e.constraint ?? ''} ${e.message}`), `${label} (${codeWanted})`, `${e.code} ${e.constraint ?? ''} ${e.message}`); }
  };
  const open = async name => { const c = connection(name); await c.connect(); return c; };
  const upTo = async (c, end) => { for (let i = 0; i < end; i++) await c.query(migrations[i]); };

  const admin = connection('postgres'); await admin.connect();
  try {
    for (const name of ['erd119_fresh','erd119_upgrade','erd119_saved','erd119_empty','erd119_rollback']) await admin.query(`CREATE DATABASE ${name}`);
    await admin.query(`CREATE DATABASE erd119_encoding ENCODING 'UTF8' LOCALE 'C' TEMPLATE template0`);
  } finally { await admin.end(); }

  // One source with its P observation, optional config and a v03-format manifest. `db` needs owner rights.
  const world = async db => {
    const source = await insert(db, 'ops_sources', { dataset_key:`qa119-${randomUUID()}` });
    const execution = await insert(db, 'data_executions', { source_id:source.id, layer:'P' });
    const hourAgo = new Date(Date.now() - 3600000);
    const observation = await insert(db, 'data_observations', { source_id:source.id, layer:'P', execution_id:execution.id, revision_no:1, persisted_at:hourAgo, api_received_at:hourAgo });
    await insert(db, 'data_processes', { observation_id:observation.id });
    const config = await insert(db, 'ml_config_versions', { source_id:source.id });
    const asOf = new Date();
    const manifest = await insert(db, 'run_input_manifests', { source_id:source.id, execution_id:execution.id, process_observation_id:observation.id,
      message_observation_refs:[], readiness:{}, input_as_of:asOf, contract_version:'v03' });
    return { source, execution, observation, config, manifest, asOf };
  };
  const detectJob = (w, version, over = {}) => ({ kind:'detect', status:'queued', contract_version:version, queue:'detection', producer_kind:'system', producer_key:'collector-119',
    idempotency_key:randomUUID(), input_manifest_id:w.manifest.id, input_config_version_id:version === 'v03' ? w.config.id : null, max_attempts:3, attempt_count:0,
    requested_by_actor_id:null, requester_role:null, ...over });
  // _claim_job: new running attempt with a 60 s lease, previous attempt expired.
  const claim = async (db, job, n = 1) => {
    await db.query(`UPDATE run_job_attempts SET status='expired',finished_at=clock_timestamp() WHERE job_id=$1 AND status='running'`, [job.id]);
    const attempt = await insert(db, 'run_job_attempts', { job_id:job.id, attempt_no:n, status:'running', lease_until:new Date(Date.now() + 60000) });
    await db.query(`UPDATE run_jobs SET status='running',current_attempt_id=$1,attempt_count=$2 WHERE id=$3`, [attempt.id, n, job.id]);
    return attempt;
  };
  const decision = (w, job, attempt, over = {}) => ({ execution_id:w.execution.id, process_observation_id:w.observation.id, config_version_id:job.input_config_version_id,
    input_manifest_id:job.input_manifest_id, input_manifest_hash:w.manifest.content_hash, input_as_of:w.asOf, job_id:job.id, result_attempt_id:attempt.id, result_lease_token:attempt.lease_token,
    status:'partial', rule_status:'succeeded', ml_status:'not_ready', latency_status:'unavailable', source_latency_ms:null, evidence:{ schemaVersion:4 }, decided_at:new Date(), ...over });
  const interim = (job, attempt, kind, over = {}) => ({ id:randomUUID(), job_id:job.id, attempt_id:attempt.id, seq:null, kind, lease_token:attempt.lease_token,
    evidence_version:'detector-evidence-v4', payload:{ schemaVersion:4 }, ...over });
  const spec = () => ({ evidence_version:'saved-decision-v1', input_evidence_hash:'a'.repeat(64), model_version:'qwen-base-6a9e13bd', prompt_version:'grounded-facts-v1',
    output_schema_version:'fact-ids-checks-v1', reuse_key:randomUUID() });
  const explainJob = (decisionRow, llmSpec, over = {}) => ({ kind:'explain', status:'queued', contract_version:'v03', queue:'llm_explanation', producer_kind:'user', producer_key:'user119',
    requested_by_actor_id:'user119', requester_role:'user', idempotency_key:randomUUID(), input_decision_id:decisionRow.id, input_config_version_id:decisionRow.config_version_id,
    llm_spec:llmSpec, max_attempts:3, attempt_count:0, ...over });
  const llmResult = (job, attempt, s, over = {}) => ({ job_id:job.id, kind:'explain', decision_id:job.input_decision_id, config_version_id:job.input_config_version_id, ...s,
    output:{ explanation:'synthetic' }, created_attempt_id:attempt.id, lease_token:attempt.lease_token, completed_at:new Date(), ...over });
  // Same statement as detector/runner.py: no RETURNING, Detector has no SELECT on this table.
  const measure = (db, w, stage, configId) => db.query(`INSERT INTO ops_pipeline_measurements(id,observation_id,stage,config_version_id,duration_ms) VALUES($1,$2,$3,$4,1)`,
    [randomUUID(), w.observation.id, stage, configId]);
  const succeed = (db, job) => db.query(`UPDATE run_jobs SET status='succeeded',finished_at=clock_timestamp() WHERE id=$1`, [job.id]);
  const kinds = async (db, job) => (await db.query(`SELECT e.event_kind FROM run_decision_events e LEFT JOIN run_decision_interims i ON i.id=e.interim_id
    LEFT JOIN run_process_decisions d ON d.id=e.decision_id WHERE i.job_id=$1 OR d.job_id=$1 ORDER BY e.id`, [job.id])).rows.map(r => r.event_kind);
  const rulesOf = async db => (await db.query(`SELECT revision,content_hash,snapshot::text,changes::text,summary::text,note,restored_from_revision,saved_by_actor_id,saved_at FROM ${RULE} ORDER BY revision`)).rows;

  // Hash rule itself: the serializer reproduces the contract's own values before it judges r1.
  const refHard = enabledNegative => [{ id:'hard:process-status', enabled:true, severity:STATUS_SEVERITY }, { id:'hard:negative-duration', enabled:enabledNegative, severity:'critical' }];
  verdict(sha256(canonicalV2({ hardRules:refHard(true), softRules:[] })) === REF_A, "hash serializer reproduces the rule-set.md v2 reference value (a')");
  verdict(sha256(canonicalV2({ hardRules:refHard(false), softRules:[{ id:'soft:elapsed_ms-3f9a2c1d', name:'ignored', field:'elapsed_ms', operator:'gt', threshold:8000, severity:'warning', enabled:true }] })) === REF_B,
    'hash serializer reproduces the rule-set.md v2 reference value (b)');

  const c = await open('erd119_fresh');
  const logins = {};
  const login = async role => { const a = new Client({ host:'127.0.0.1', port, user:logins[role].user, password:logins[role].password, database:'erd119_fresh' }); await a.connect(); return a; };
  try {
    for (const sql of migrations) await c.query(sql);
    check(`erd119_fresh: 001 through ${migrationNames.at(-1)} apply`);
    await c.query(rolesSql);
    for (const role of ['api','detector','collector','llm']) {
      logins[role] = { user:`qa119_${role}`, password:randomUUID() };
      await c.query(`CREATE ROLE ${logins[role].user} LOGIN PASSWORD '${logins[role].password}' IN ROLE powercode_${role}`);
    }
    await parity(c);
    const api = await login('api'), detector = await login('detector'), collector = await login('collector'), llm = await login('llm');
    try {
      // r0 and r1 rows.
      const rows = (await c.query(`SELECT * FROM ${RULE} ORDER BY revision`)).rows;
      verdict(rows.length === 2 && rows[0].revision === '0' && rows[1].revision === '1', `${RULE}: fresh database holds r0 and r1 only`, JSON.stringify(rows.map(r => r.revision)));
      const [r0, r1] = rows;
      verdict(r0.content_hash_version === 1 && r0.content_hash === R0_HASH && r0.snapshot.hardRules.length === 2 && sha256(canonicalV1(r0.snapshot)) === R0_HASH,
        'r0 keeps content_hash_version 1 and its v1 hash recalculated from the stored snapshot', JSON.stringify(r0));
      assert.deepEqual(r1.snapshot, { hardRules:R1_HARD_RULES, softRules:[] }, 'r1 snapshot = detection-decision.md 4.3 items, no soft rules');
      check('r1 snapshot equals the 4 hard rules (id, version, screen text, severity, editable), all enabled, no soft rules');
      assert.deepEqual(r1.changes, R1_CHANGES); assert.deepEqual(r1.summary, R1_SUMMARY);
      check('r1 changes are hard_enabled for the 2 new rules; summary counts match rule-set.md format');
      verdict(r1.content_hash_version === 2 && r1.saved_by_actor_id === 'system' && r1.note === null && r1.restored_from_revision === null && r1.saved_at instanceof Date,
        'r1 is a version 2 system row without note or restore source', JSON.stringify(r1));
      verdict(sha256(canonicalV2(r1.snapshot)) === R1_HASH && r1.content_hash === R1_HASH, `r1 content_hash recalculated from the stored snapshot = ${R1_HASH}`, r1.content_hash);

      // Column default, limits and immutability.
      const saveRow = (db, revision, extra = '') => db.query(`INSERT INTO ${RULE}(revision,content_hash,snapshot,changes,summary,saved_by_actor_id${extra ? ',content_hash_version' : ''})
        VALUES($1,$2,'{"hardRules":[],"softRules":[]}','[]','{}','admin119'${extra ? ','+extra : ''})`, [revision, 'b'.repeat(64)]);
      await api.query('BEGIN');
      try {
        await api.query('SELECT pg_advisory_xact_lock(81739,0)');
        await saveRow(api, 2);
        const saved = (await api.query(`SELECT content_hash_version FROM ${RULE} WHERE revision=2`)).rows[0];
        verdict(saved.content_hash_version === 2, 'new row that omits content_hash_version is stored as 2 (API role)', JSON.stringify(saved));
        const seen = (await api.query(`SELECT rule_set_revision,rule_set_content_hash_version FROM ${VIEW}`)).rows[0];
        verdict(seen.rule_set_revision === '2' && seen.rule_set_content_hash_version === 2, 'view follows the newest revision and its hash version', JSON.stringify(seen));
      } finally { await api.query('ROLLBACK'); }
      await no('content_hash_version outside 1,2 rejected', '23514', /ops_rule_set_revisions_content_hash_version_check/, () => saveRow(c, 2, '3'));
      await no('content_hash_version NULL rejected', '23502', /content_hash_version/, () => saveRow(c, 2, 'NULL'));
      for (const [label, sql] of [['UPDATE r0 version', `UPDATE ${RULE} SET content_hash_version=2 WHERE revision=0`], ['UPDATE r1 version', `UPDATE ${RULE} SET content_hash_version=1 WHERE revision=1`],
        ['DELETE r1', `DELETE FROM ${RULE} WHERE revision=1`], ['TRUNCATE', `TRUNCATE ${RULE}`]])
        await no(`${RULE} stays immutable: ${label}`, 'P0001', /ops_rule_set_revisions is immutable/, () => c.query(sql));

      // View: 6 agreed columns untouched, one appended; reads r1; grants and security_invoker survive CREATE OR REPLACE.
      const types = (await c.query(`SELECT string_agg(a.attname||':'||t.typname, ',' ORDER BY a.attnum) AS v FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid
        WHERE a.attrelid='${VIEW}'::regclass AND a.attnum>0 AND NOT a.attisdropped`)).rows[0].v;
      verdict(types === VIEW_COLUMNS, `${VIEW}: the 6 agreed columns keep name, order and type; rule_set_content_hash_version is appended`, types);
      const options = (await c.query(`SELECT reloptions FROM pg_class WHERE oid='${VIEW}'::regclass`)).rows[0].reloptions;
      verdict(JSON.stringify(options) === '["security_invoker=true"]', `${VIEW}: security_invoker kept`, JSON.stringify(options));
      for (const [name, db] of [['API', api], ['Detector', detector]]) {
        const s = (await db.query(`SELECT * FROM ${VIEW}`)).rows;
        verdict(s.length === 1 && s[0].rule_set_revision === '1' && s[0].rule_set_content_hash === R1_HASH && s[0].rule_set_content_hash_version === 2
          && s[0].active_model_set_revision === '0' && s[0].detection_policy_revision === '0', `${VIEW}: ${name} reads r1`, JSON.stringify(s));
      }
      await no(`${VIEW}: Collector still has no grant`, '42501', /permission denied/, () => collector.query(`SELECT * FROM ${VIEW}`));

      // v04 job shape.
      const w = await world(c), other = await world(c);
      await no('v04 job with a config rejected', '23514', /run_jobs_v03_contract/, () => insert(collector, 'run_jobs', detectJob(w, 'v04', { input_config_version_id:w.config.id })));
      await no('v04 job without a manifest rejected', '23514', /run_jobs_v03_contract/, () => insert(collector, 'run_jobs', detectJob(w, 'v04', { input_manifest_id:null })));
      await no('v04 job without an idempotency key rejected', '23514', /run_jobs_v03_contract/, () => insert(collector, 'run_jobs', detectJob(w, 'v04', { idempotency_key:null })));
      await no('v04 job with a user producer rejected', '23514', /run_jobs_v03_contract/, () => insert(collector, 'run_jobs', detectJob(w, 'v04', { producer_kind:'user', producer_key:'u', requested_by_actor_id:'u', requester_role:'admin' })));
      await no('v04 job on another queue rejected', '23514', /run_jobs_v03_contract/, () => insert(collector, 'run_jobs', detectJob(w, 'v04', { queue:'training' })));
      // Only the kind is wrong here: queue, producer and manifest are valid, so the detect-only condition itself must reject these.
      for (const kind of ['train','evaluate','preview'])
        await no(`v04 job cannot be a ${kind} job`, '23514', /run_jobs_v03_contract/, () => insert(collector, 'run_jobs', detectJob(w, 'v04', { kind })));
      await no('unknown contract version rejected', '23514', /run_jobs_v03_contract/, () => insert(collector, 'run_jobs', detectJob(w, 'v05')));
      await no('v03 detect job without a config rejected', 'P0001', /detect config\/input source mismatch/, () => insert(collector, 'run_jobs', detectJob(w, 'v03', { input_config_version_id:null })));
      await no('v03 detect job still checks config/source', 'P0001', /detect config\/input source mismatch/, () => insert(collector, 'run_jobs', detectJob(w, 'v03', { input_config_version_id:other.config.id })));
      await no('blocked v04 job requires a reason', 'P0001', /blocked reason required/, () => insert(collector, 'run_jobs', detectJob(w, 'v04', { status:'blocked' })));

      const job = await insert(collector, 'run_jobs', detectJob(w, 'v04'));
      verdict(job.input_config_version_id === null && job.contract_version === 'v04', 'Collector stores a v04 detect job with NULL config and a v03-format manifest');
      await no('second v04 job for the same manifest rejected', '23505', /run_jobs_v04_detect_idx/, () => insert(collector, 'run_jobs', detectJob(w, 'v04')));
      await no('v04 producer key duplicate rejected', '23505', /run_jobs_v04_producer_key_idx/, () => insert(collector, 'run_jobs', detectJob(other, 'v04', { idempotency_key:job.idempotency_key })));
      const v03Same = await insert(collector, 'run_jobs', detectJob(w, 'v03'));
      verdict(v03Same.contract_version === 'v03', 'a v03 and a v04 job may share a manifest (version change window)');
      await no('v04 job cannot move to v03', 'P0001', /job contract version is fixed/, () => c.query(`UPDATE run_jobs SET contract_version='v03' WHERE id=$1`, [job.id]));
      await no('v04 job cannot gain a config', 'P0001', /v04 job input is immutable/, () => c.query('UPDATE run_jobs SET input_config_version_id=$1 WHERE id=$2', [w.config.id, job.id]));
      await no('v04 job input_spec is immutable', 'P0001', /v04 job input is immutable/, () => c.query(`UPDATE run_jobs SET input_spec='{"changed":true}' WHERE id=$1`, [job.id]));
      await no('v04 job manifest is immutable', 'P0001', /v04 job input is immutable/, () => c.query('UPDATE run_jobs SET input_manifest_id=$1 WHERE id=$2', [other.manifest.id, job.id]));
      await no('v03 job input message unchanged', 'P0001', /v03 job input is immutable/, () => c.query(`UPDATE run_jobs SET input_spec='{"changed":true}' WHERE id=$1`, [v03Same.id]));

      // v04 decision: every ownership and manifest check stays, config must stay NULL.
      const queuedAttempt = await insert(detector, 'run_job_attempts', { job_id:job.id, attempt_no:1, status:'running', lease_until:new Date(Date.now() + 60000) });
      await no('v04 decision rejected while the job is not running', 'P0001', /current owner and unexpired lease/, () => insert(detector, 'run_process_decisions', decision(w, job, queuedAttempt)));
      await detector.query(`UPDATE run_jobs SET status='running',current_attempt_id=$1,attempt_count=1 WHERE id=$2`, [queuedAttempt.id, job.id]);
      const attempt = queuedAttempt;
      await no('v04 decision with a config rejected', 'P0001', /decision job kind\/input\/config mismatch/, () => insert(detector, 'run_process_decisions', decision(w, job, attempt, { config_version_id:w.config.id })));
      await no('v04 decision with a wrong lease token rejected', 'P0001', /current owner and unexpired lease/, () => insert(detector, 'run_process_decisions', decision(w, job, attempt, { result_lease_token:randomUUID() })));
      await no('v04 decision with another attempt id rejected', 'P0002', /no rows|query returned/, () => insert(detector, 'run_process_decisions', decision(w, job, attempt, { result_attempt_id:randomUUID() })));
      const manifestB = await insert(c, 'run_input_manifests', { source_id:w.source.id, execution_id:w.execution.id, process_observation_id:w.observation.id,
        message_observation_refs:[], readiness:{ other:true }, input_as_of:w.asOf, contract_version:'v03' });
      await no('v04 decision with another manifest rejected', 'P0001', /decision job kind\/input\/config mismatch/, () => insert(detector, 'run_process_decisions', decision(w, job, attempt, { input_manifest_id:manifestB.id, input_manifest_hash:manifestB.content_hash })));
      await no('v04 decision with a wrong manifest hash rejected', '23503', /decisions_manifest_hash_fk/, () => insert(detector, 'run_process_decisions', decision(w, job, attempt, { input_manifest_hash:'wrong' })));
      await no('v04 decision with another as-of rejected', 'P0001', /differs from fixed manifest/, () => insert(detector, 'run_process_decisions', decision(w, job, attempt, { input_as_of:new Date(w.asOf.getTime() - 1000) })));
      await no('v04 job success requires a decision', 'P0001', /detect completion requires decision/, () => succeed(detector, job));
      await c.query(`UPDATE run_job_attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE id=$1`, [attempt.id]);
      await no('v04 decision after lease expiry rejected', 'P0001', /current owner and unexpired lease/, () => insert(detector, 'run_process_decisions', decision(w, job, attempt)));
      const attempt2 = await claim(detector, job, 2);
      await no('v04 stale attempt rejected after reclaim', 'P0001', /current owner and unexpired lease/, () => insert(detector, 'run_process_decisions', decision(w, job, attempt)));

      // Interim records on a NULL-config job: first anomaly, evidence, supersede by a new attempt, close by the final decision.
      await no('v04 interim with a wrong lease rejected', 'P0001', /current owner and unexpired lease/, () => insert(detector, 'run_decision_interims', interim(job, attempt2, 'first_anomaly', { lease_token:randomUUID() })));
      await no('v04 evidence_added needs first_anomaly', 'P0001', /evidence_added requires first_anomaly/, () => insert(detector, 'run_decision_interims', interim(job, attempt2, 'evidence_added')));
      await insert(detector, 'run_decision_interims', interim(job, attempt2, 'first_anomaly'));
      await insert(detector, 'run_decision_interims', interim(job, attempt2, 'evidence_added'));
      const attempt3 = await claim(detector, job, 3);
      await insert(detector, 'run_decision_interims', interim(job, attempt3, 'first_anomaly'));
      const closed = (await c.query(`SELECT attempt_id,kind,superseded_by_attempt_id,seq FROM run_decision_interims WHERE job_id=$1 AND kind='superseded'`, [job.id])).rows;
      verdict(closed.length === 1 && closed[0].attempt_id === attempt2.id && closed[0].superseded_by_attempt_id === attempt3.id && closed[0].seq === 3,
        'v04 interim: new attempt first_anomaly supersedes the previous attempt (012 SECURITY DEFINER trigger)', JSON.stringify(closed));
      const final = await insert(detector, 'run_process_decisions', decision(w, job, attempt3));
      verdict(final.config_version_id === null && final.job_id === job.id, 'Detector stores a NULL-config v04 decision for the current attempt');
      await no('v04 interim after the final decision rejected', 'P0001', /interim after final decision/, () => insert(detector, 'run_decision_interims', interim(job, attempt3, 'evidence_added')));
      await no('second v04 decision for the job rejected', '23505', /run_process_decisions_job_id_key/, () => insert(detector, 'run_process_decisions', decision(w, job, attempt3)));
      await measure(detector, w, 'detector', null);
      check('Detector stores a detector-stage measurement without config');
      await succeed(detector, job);
      verdict((await c.query('SELECT status FROM run_jobs WHERE id=$1', [job.id])).rows[0].status === 'succeeded', 'v04 job succeeds after its decision');
      assert.deepEqual(await kinds(c, job), ['first_anomaly','evidence_added','first_anomaly','superseded','final']);
      check('v04 events keep the 012 order: first_anomaly, evidence_added, first_anomaly, superseded, final');
      await no('v04 decision content is immutable', 'P0001', /v03 decision content is immutable/, () => c.query(`UPDATE run_process_decisions SET evidence='{"changed":true}' WHERE id=$1`, [final.id]));
      await no('v04 decision cannot gain a config', 'P0001', /v03 decision content is immutable/, () => c.query('UPDATE run_process_decisions SET config_version_id=$1 WHERE id=$2', [w.config.id, final.id]));
      await c.query('UPDATE run_process_decisions SET published_at=clock_timestamp() WHERE id=$1', [final.id]);
      check('v04 decision publication timestamp remains updateable');

      // Interim closed by a decision without an own interim, and no_final on failure.
      const w2 = await world(c);
      const job2 = await insert(collector, 'run_jobs', detectJob(w2, 'v04'));
      const a21 = await claim(detector, job2, 1);
      await insert(detector, 'run_decision_interims', interim(job2, a21, 'first_anomaly'));
      const a22 = await claim(detector, job2, 2);
      await insert(detector, 'run_process_decisions', decision(w2, job2, a22));
      assert.deepEqual(await kinds(c, job2), ['first_anomaly','final','superseded']);
      check('v04 final decision of a newer attempt supersedes the open chain');
      const w3 = await world(c);
      const job3 = await insert(collector, 'run_jobs', detectJob(w3, 'v04'));
      const a31 = await claim(detector, job3, 1);
      await insert(detector, 'run_decision_interims', interim(job3, a31, 'first_anomaly'));
      await detector.query(`UPDATE run_jobs SET status='failed',error_code='detection_processing_failed',finished_at=clock_timestamp() WHERE id=$1`, [job3.id]);
      const noFinal = (await c.query(`SELECT reason_code FROM run_decision_interims WHERE job_id=$1 AND kind='no_final'`, [job3.id])).rows;
      verdict(noFinal.length === 1 && noFinal[0].reason_code === 'detection_processing_failed', 'failed v04 job closes its interim chain as no_final', JSON.stringify(noFinal));
      await detector.query(`UPDATE run_jobs SET status='running' WHERE id=$1`, [job3.id]);
      await no('decision after no_final rejected on a v04 job', 'P0001', /already closed its interim chain as no_final/, () => insert(detector, 'run_process_decisions', decision(w3, job3, a31)));
      await no('interim forged by a client on a v04 job rejected', 'P0001', /written only by database triggers/, () => detector.query(`INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,reason_code,payload)
        VALUES($1,$2,$3,1,'no_final','forged','{}')`, [randomUUID(), job2.id, a22.id]));
      const train = await insert(c, 'run_jobs', { kind:'train', status:'running', contract_version:'v03', queue:'training', producer_kind:'user', producer_key:'a119', requested_by_actor_id:'a119',
        requester_role:'admin', idempotency_key:randomUUID(), max_attempts:3, attempt_count:0 });
      const trainAttempt = await claim(c, train, 1);
      await no('interim still requires a detect job', 'P0001', /interim requires v03 or v04 detect job/, () => insert(c, 'run_decision_interims', interim(train, trainAttempt, 'first_anomaly')));

      // Explain on a NULL-config decision: stays a v03 job, config follows the decision.
      const s1 = spec();
      await no('v04 job cannot be an explain job', '23514', /run_jobs_v03_contract/, () => insert(api, 'run_jobs', explainJob(final, spec(), { contract_version:'v04' })));
      await no('explain with a config on a NULL-config decision rejected', 'P0001', /explanation decision\/config mismatch/, () => insert(api, 'run_jobs', explainJob(final, s1, { input_config_version_id:w.config.id })));
      const explain = await insert(api, 'run_jobs', explainJob(final, s1));
      verdict(explain.input_config_version_id === null && explain.contract_version === 'v03', 'API stores an explain job with NULL config for a NULL-config decision');
      const ea = await claim(llm, explain, 1);
      await no('LLM result with a config on a NULL-config job rejected', 'P0001', /LLM result\/job lineage mismatch/, () => insert(llm, 'run_llm_results', llmResult(explain, ea, s1, { config_version_id:w.config.id })));
      await no('explain result without decision still rejected', 'P0001', /LLM result\/job lineage mismatch/, () => insert(llm, 'run_llm_results', llmResult(explain, ea, s1, { decision_id:null })));
      await no('explain job success requires a result', 'P0001', /LLM completion requires result/, () => succeed(llm, explain));
      const saved = await insert(llm, 'run_llm_results', llmResult(explain, ea, s1));
      await succeed(llm, explain);
      verdict(saved.config_version_id === null, 'LLM role stores the result with NULL config and the job succeeds');

      // v03 regression with a config: matching stays required in both directions.
      const w4 = await world(c);
      const v03 = await insert(collector, 'run_jobs', detectJob(w4, 'v03'));
      const a41 = await claim(detector, v03, 1);
      await no('v03 decision with NULL config rejected', 'P0001', /decision job kind\/input\/config mismatch/, () => insert(detector, 'run_process_decisions', decision(w4, v03, a41, { config_version_id:null })));
      await no('v03 decision with another config rejected', 'P0001', /decision job kind\/input\/config mismatch/, () => insert(detector, 'run_process_decisions', decision(w4, v03, a41, { config_version_id:other.config.id })));
      await insert(detector, 'run_decision_interims', interim(v03, a41, 'first_anomaly'));
      const v03Decision = await insert(detector, 'run_process_decisions', decision(w4, v03, a41));
      await no('second v03 decision still hits the original 4-column unique', '23505', /run_process_decisions_execution_id_process_observation_id/, () => insert(detector, 'run_process_decisions', decision(w4, v03, a41)));
      await measure(detector, w4, 'detector', w4.config.id);
      await succeed(detector, v03);
      check('v03 job with config: interim, decision, measurement and success unchanged');
      const s2 = spec();
      await no('explain with NULL config on a config decision rejected', 'P0001', /explanation decision\/config mismatch/, () => insert(api, 'run_jobs', explainJob(v03Decision, s2, { input_config_version_id:null })));
      await no('explain with another config on a config decision rejected', 'P0001', /explanation decision\/config mismatch/, () => insert(api, 'run_jobs', explainJob(v03Decision, s2, { input_config_version_id:other.config.id })));
      const explain3 = await insert(api, 'run_jobs', explainJob(v03Decision, s2));
      const ea3 = await claim(llm, explain3, 1);
      await no('LLM result with NULL config on a config job rejected', 'P0001', /LLM result\/job lineage mismatch/, () => insert(llm, 'run_llm_results', llmResult(explain3, ea3, s2, { config_version_id:null })));
      await no('LLM result with another config rejected', 'P0001', /LLM result\/job lineage mismatch/, () => insert(llm, 'run_llm_results', llmResult(explain3, ea3, s2, { config_version_id:other.config.id })));
      await insert(llm, 'run_llm_results', llmResult(explain3, ea3, s2));
      await succeed(llm, explain3);
      check('explain with a config: job, result and success unchanged');

      // Legacy rows: NOT NULL effect kept for INSERT and for UPDATE.
      const w5 = await world(c);
      const legacy = { execution_id:w5.execution.id, process_observation_id:w5.observation.id, status:'unavailable', rule_status:'not_ready', ml_status:'not_ready', latency_status:'unavailable', source_latency_ms:null };
      await no('legacy decision without job and config rejected', '23514', /decisions_config_required/, () => insert(c, 'run_process_decisions', { ...legacy, config_version_id:null }));
      const legacyRow = await insert(c, 'run_process_decisions', { ...legacy, config_version_id:w5.config.id, input_manifest_hash:w5.manifest.content_hash });
      await no('legacy decision cannot lose its config alone', 'P0001', /run_process_decisions is immutable/, () => c.query('UPDATE run_process_decisions SET config_version_id=NULL WHERE id=$1', [legacyRow.id]));
      const job5 = await insert(collector, 'run_jobs', detectJob(w5, 'v04'));
      const a5 = await claim(detector, job5, 1);
      await no('legacy decision cannot be rewritten into a NULL-config v04 row', 'P0001', /run_process_decisions is immutable/, () => c.query(`UPDATE run_process_decisions SET config_version_id=NULL,job_id=$1,input_manifest_id=$2,result_attempt_id=$3,result_lease_token=$4 WHERE id=$5`,
        [job5.id, w5.manifest.id, a5.id, a5.lease_token, legacyRow.id]));
      // Keeps its config and attaches to a job: the INSERT-only validator and the job-row guard would both miss this.
      const attach = (j, at) => c.query('UPDATE run_process_decisions SET job_id=$1,input_manifest_id=$2,result_attempt_id=$3,result_lease_token=$4 WHERE id=$5', [j.id, w5.manifest.id, at.id, at.lease_token, legacyRow.id]);
      await no('legacy decision with a config cannot attach to a v04 job', 'P0001', /run_process_decisions is immutable/, () => attach(job5, a5));
      const job5v03 = await insert(collector, 'run_jobs', detectJob(w5, 'v03'));
      const a5v03 = await claim(detector, job5v03, 1);
      await no('legacy decision cannot attach to a v03 job', 'P0001', /run_process_decisions is immutable/, () => attach(job5v03, a5v03));
      await c.query(`UPDATE run_process_decisions SET published_at=clock_timestamp() WHERE id=$1`, [legacyRow.id]);
      check('legacy decision with a config stays updateable where it was');

      // The partial unique works on its own: the job-level index is removed inside this transaction only.
      const w6 = await world(c);
      await c.query('BEGIN');
      try {
        await c.query('DROP INDEX run_jobs_v04_detect_idx');
        const d1 = await insert(c, 'run_jobs', detectJob(w6, 'v04')), d2 = await insert(c, 'run_jobs', detectJob(w6, 'v04'));
        const b1 = await claim(c, d1, 1), b2 = await claim(c, d2, 1);
        await insert(c, 'run_process_decisions', decision(w6, d1, b1));
        await no('NULL-config duplicate decision rejected by the partial unique itself', '23505', /run_process_decisions_no_config_idx/, () => insert(c, 'run_process_decisions', decision(w6, d2, b2)));
      } finally { await c.query('ROLLBACK'); }

      // Measurements.
      await no('feature-stage measurement without config rejected', '23514', /ops_pipeline_measurements_config_check/, () => measure(detector, w, 'feature', null));
      await measure(detector, w, 'feature', w.config.id);
      check('feature-stage measurement with config unchanged');

      // Redefined functions keep their settings; the 012 SECURITY DEFINER functions keep theirs.
      const settings = Object.fromEntries((await c.query(`SELECT proname::text AS n, proconfig, prosecdef FROM pg_proc WHERE proname = ANY($1)`,
        [['erd_v03_validate_job','erd_v03_validate_result','validate_decision_interim','supersede_decision_interims','close_decision_interims_on_job_failure']])).rows.map(r => [r.n, [r.proconfig, r.prosecdef]]));
      assert.deepEqual(settings, { erd_v03_validate_job:[['search_path=public, pg_temp'], false], erd_v03_validate_result:[['search_path=public, pg_temp'], false],
        validate_decision_interim:[['search_path=pg_catalog, pg_temp'], false], supersede_decision_interims:[['search_path=pg_catalog, pg_temp'], true],
        close_decision_interims_on_job_failure:[['search_path=pg_catalog, pg_temp'], true] });
      check('016 functions keep search_path and SECURITY INVOKER; 012 SECURITY DEFINER functions untouched');
    } finally { for (const db of [api, detector, collector, llm]) await db.end(); }
  } finally { await c.end(); }

  // Existing v03 and legacy data: rows kept, only the listed catalog objects change, in-flight v03 work finishes afterwards.
  {
    const u = await open('erd119_upgrade');
    try {
      await upTo(u, idx);
      const legacyWorld = await world(u);
      const legacyRow = await insert(u, 'run_process_decisions', { execution_id:legacyWorld.execution.id, process_observation_id:legacyWorld.observation.id, config_version_id:legacyWorld.config.id,
        status:'unavailable', rule_status:'not_ready', ml_status:'not_ready', latency_status:'unavailable', source_latency_ms:null });
      const doneWorld = await world(u);
      const done = await insert(u, 'run_jobs', detectJob(doneWorld, 'v03'));
      const doneAttempt = await claim(u, done, 1);
      await insert(u, 'run_decision_interims', interim(done, doneAttempt, 'first_anomaly'));
      const doneDecision = await insert(u, 'run_process_decisions', decision(doneWorld, done, doneAttempt, { evidence:{ schemaVersion:3 } }));
      await measure(u, doneWorld, 'detector', doneWorld.config.id);
      await succeed(u, done);
      const oldSpec = spec();
      const oldExplain = await insert(u, 'run_jobs', explainJob(doneDecision, oldSpec));
      const oldExplainAttempt = await claim(u, oldExplain, 1);
      await insert(u, 'run_llm_results', llmResult(oldExplain, oldExplainAttempt, oldSpec));
      await succeed(u, oldExplain);
      const runningWorld = await world(u);
      const running = await insert(u, 'run_jobs', detectJob(runningWorld, 'v03'));
      const runningAttempt = await claim(u, running, 1);
      await insert(u, 'run_decision_interims', interim(running, runningAttempt, 'first_anomaly'));
      const queuedWorld = await world(u);
      const queued = await insert(u, 'run_jobs', detectJob(queuedWorld, 'v03'));
      const keptTables = ['run_jobs','run_job_attempts','run_process_decisions','run_llm_results','run_decision_interims','run_decision_events','ops_pipeline_measurements','run_input_manifests','ops_active_model_set_revisions'];
      const rowsOf = async () => Object.fromEntries(await Promise.all(keptTables.map(async t => [t, (await u.query(`SELECT row_to_json(x)::text AS r FROM ${t} x ORDER BY 1`)).rows.map(r => r.r)])));
      const rows = await rowsOf(), rules = await rulesOf(u), before = await catalog(u);

      await u.query(migrations[idx]);
      check(`erd119_upgrade: 001-015 + v03 and legacy rows, then ${migrationNames[idx]} applies`);
      assert.deepEqual(await rowsOf(), rows, 'rows written before 016 are kept unchanged');
      check(`erd119_upgrade: rows kept (${keptTables.join(', ')})`);
      const after = await catalog(u);
      assert.deepEqual(after.enums, before.enums, '016 changes no enum');
      const col = delta(before.columns, after.columns);
      assert.deepEqual(col.removed, ['public.ops_pipeline_measurements.config_version_id:uuid:NO:','public.run_process_decisions.config_version_id:uuid:NO:']);
      assert.deepEqual(col.added, ['public.ops_operational_state.rule_set_content_hash_version:int2:YES:','public.ops_pipeline_measurements.config_version_id:uuid:YES:',
        'public.ops_rule_set_revisions.content_hash_version:int2:NO:2','public.run_process_decisions.config_version_id:uuid:YES:']);
      const con = delta(before.constraints, after.constraints);
      assert.deepEqual(con.removed.map(nameOf), ['run_jobs.run_jobs_v03_contract','run_llm_results.run_llm_results_check']);
      assert.deepEqual(con.added.map(nameOf), ['ops_pipeline_measurements.ops_pipeline_measurements_config_check','ops_rule_set_revisions.ops_rule_set_revisions_content_hash_version_check',
        'run_jobs.run_jobs_v03_contract','run_llm_results.run_llm_results_check','run_process_decisions.decisions_config_required']);
      verdict(after.constraints.every(v => v.split(':')[1] === 'true'), 'erd119_upgrade: every constraint is validated (no NOT VALID left)');
      const trg = delta(before.triggers, after.triggers);
      assert.deepEqual([trg.removed, trg.added.map(nameOf)], [[], ['run_process_decisions.decision_config_kept']]);
      const ind = delta(before.indexes, after.indexes);
      assert.deepEqual([ind.removed, ind.added.map(nameOf)], [[], ['run_jobs_v04_detect_idx','run_jobs_v04_producer_key_idx','run_jobs_v04_queue_idx','run_process_decisions_no_config_idx']]);
      check('erd119_upgrade: columns, constraints, triggers and indexes change only as listed; enums unchanged');
      const changed = after.functions.filter(f => { const o = before.functions.find(b => b.name === f.name); return !o || o.body !== f.body; }).map(f => f.name);
      assert.deepEqual(changed, Object.keys(FUNCTION_EDITS).sort(), 'only the 3 redefined function bodies change');
      assert.equal(after.functions.length, before.functions.length, '016 adds no function');
      for (const f of after.functions) {
        const o = before.functions.find(b => b.name === f.name);
        assert.deepEqual([f.config, f.definer, f.owner], [o.config, o.definer, o.owner], `${f.name} settings kept`);
      }
      for (const [name, edits] of Object.entries(FUNCTION_EDITS)) {
        let expected = code(before.functions.find(f => f.name === name).src);
        for (const [from, to] of edits) { assert(expected.includes(from), `${name}: original has "${from}"`); expected = expected.replaceAll(from, to); }
        assert.equal(code(after.functions.find(f => f.name === name).src), expected, `${name} differs from its original only by the listed lines`);
      }
      check('erd119_upgrade: 3 function bodies differ from their originals only by the listed lines; config, SECURITY and owner kept; no function added');
      const newRules = await rulesOf(u);
      assert.deepEqual(newRules[0], rules[0], 'r0 row unchanged');
      const versions = (await u.query(`SELECT revision,content_hash_version FROM ${RULE} ORDER BY revision`)).rows;
      assert.deepEqual(versions, [{ revision:'0', content_hash_version:1 }, { revision:'1', content_hash_version:2 }]);
      check('erd119_upgrade: r0 row unchanged with content_hash_version 1; r1 added with 2');
      verdict((await u.query(`SELECT rule_set_revision FROM ${VIEW}`)).rows[0].rule_set_revision === '1', 'erd119_upgrade: view reads r1');

      // In-flight v03 work after 016.
      await insert(u, 'run_decision_interims', interim(running, runningAttempt, 'evidence_added'));
      await no('erd119_upgrade: running v03 job rejects a NULL-config decision', 'P0001', /decision job kind\/input\/config mismatch/, () => insert(u, 'run_process_decisions', decision(runningWorld, running, runningAttempt, { config_version_id:null })));
      await insert(u, 'run_process_decisions', decision(runningWorld, running, runningAttempt));
      await succeed(u, running);
      check('erd119_upgrade: job running before 016 finishes with its config');
      await no('erd119_upgrade: queued v03 job config stays fixed', 'P0001', /v03 job input is immutable/, () => u.query('UPDATE run_jobs SET input_config_version_id=NULL WHERE id=$1', [queued.id]));
      const queuedAttempt = await claim(u, queued, 1);
      await insert(u, 'run_process_decisions', decision(queuedWorld, queued, queuedAttempt));
      await succeed(u, queued);
      check('erd119_upgrade: job queued before 016 runs to success');
      const newSpec = spec();
      await no('erd119_upgrade: explain on a pre-016 decision still needs its config', 'P0001', /explanation decision\/config mismatch/, () => insert(u, 'run_jobs', explainJob(doneDecision, newSpec, { input_config_version_id:null })));
      await insert(u, 'run_jobs', explainJob(doneDecision, newSpec));
      await no('erd119_upgrade: legacy row cannot lose its config', 'P0001', /run_process_decisions is immutable/, () => u.query('UPDATE run_process_decisions SET config_version_id=NULL WHERE id=$1', [legacyRow.id]));
      const v04 = await insert(u, 'run_jobs', detectJob(doneWorld, 'v04'));
      const v04Attempt = await claim(u, v04, 1);
      const v04Final = await insert(u, 'run_process_decisions', decision(doneWorld, v04, v04Attempt));
      verdict(v04Final.config_version_id === null, 'erd119_upgrade: v04 job on an old manifest stores a NULL-config decision next to the old v03 decision');
      // schema.dbml is the final structure, so parity needs the later migrations too.
      for (let i = idx + 1; i < migrations.length; i++) await u.query(migrations[i]);
      await parity(u);
    } finally { await u.end(); }
  }

  // A saved revision blocks r1: explicit failure, and nothing of 016 stays. Message carries the reason and the action.
  {
    const s = await open('erd119_saved');
    try {
      await upTo(s, idx);
      await s.query(`INSERT INTO ${RULE}(revision,content_hash,snapshot,changes,summary,saved_by_actor_id) VALUES(1,$1,'{"hardRules":[],"softRules":[]}','[]','{}','admin119')`, ['c'.repeat(64)]);
      const before = await catalog(s), rules = await rulesOf(s);
      let error = null;
      try { await s.query(migrations[idx]); } catch (e) { error = e; }
      await s.query('ROLLBACK');
      verdict(error?.code === 'P0001' && /max revision 0 but found 1/.test(error.message) && /Reason:/.test(error.message) && /Action:/.test(error.message),
        'erd119_saved: 016 stops with the reason and the action when a revision was saved', `${error?.code} ${error?.message}`);
      assert.deepEqual(await catalog(s), before, 'failed 016 leaves the catalog unchanged');
      assert.deepEqual(await rulesOf(s), rules, 'failed 016 leaves the rule rows unchanged');
      check('erd119_saved: failed 016 leaves catalog, r0 and the saved revision unchanged (full ROLLBACK)');
    } finally { await s.end(); }
    const e = await open('erd119_empty');
    try {
      await upTo(e, idx);
      await e.query(`ALTER TABLE ${RULE} DISABLE TRIGGER rule_set_revision_immutable`);
      await e.query(`DELETE FROM ${RULE}`);
      await e.query(`ALTER TABLE ${RULE} ENABLE TRIGGER rule_set_revision_immutable`);
      const before = await catalog(e);
      let error = null;
      try { await e.query(migrations[idx]); } catch (x) { error = x; }
      await e.query('ROLLBACK');
      verdict(error?.code === 'P0001' && /found no rows/.test(error.message), 'erd119_empty: 016 also stops when r0 is missing', `${error?.code} ${error?.message}`);
      assert.deepEqual(await catalog(e), before, 'failed 016 leaves the catalog unchanged');
      check('erd119_empty: failed 016 leaves the catalog unchanged');
    } finally { await e.end(); }
  }

  // Injected failure just before COMMIT (psql -v ON_ERROR_STOP=1 -f semantics): nothing stays; then it applies cleanly.
  {
    const r = await open('erd119_rollback');
    try {
      await upTo(r, idx);
      const text = migrations[idx].replaceAll('\r\n', '\n');
      const commit = text.lastIndexOf('\nCOMMIT;');
      assert(text.search(/^BEGIN;$/m) >= 0 && commit > text.search(/^BEGIN;$/m), '016 is one BEGIN..COMMIT block');
      const before = await catalog(r), rules = await rulesOf(r);
      let error = null;
      try { await r.query(text.slice(0, commit + 1) + 'SELECT 1/0; -- injected failure after every statement\n' + text.slice(commit + 1)); } catch (e) { error = e; }
      await r.query('ROLLBACK');
      assert(error && error.code === '22012', `injected 016 failure must abort the migration (${error?.message})`);
      assert.deepEqual(await catalog(r), before, '016 structural changes rolled back');
      assert.deepEqual(await rulesOf(r), rules, 'r1 rolled back');
      check('erd119_rollback: 016 failing before COMMIT leaves catalog and rule rows unchanged');
      await r.query(migrations[idx]);
      verdict((await r.query(`SELECT rule_set_revision FROM ${VIEW}`)).rows[0].rule_set_revision === '1', 'erd119_rollback: 016 applies cleanly afterwards');
      for (let i = idx + 1; i < migrations.length; i++) await r.query(migrations[i]);
      await parity(r);
    } finally { await r.end(); }
  }

  // psql -f sends statements one by one, so SET LOCAL client_encoding reaches the server before the r1 literals. EUC_KR session on a UTF8 database.
  {
    const n = await open('erd119_encoding');
    try {
      await upTo(n, idx);
      const parts = statements(migrations[idx].replaceAll('\r\n', '\n'));
      assert(parts[0] === 'BEGIN;' && parts.at(-1) === 'COMMIT;' && parts.some(p => p.startsWith('DO $$')), '016 splits into statements');
      await n.query(`SET client_encoding = 'EUC_KR'`);
      for (const part of parts) await n.query(part);
      const restored = (await n.query('SHOW client_encoding')).rows[0].client_encoding;
      await n.query(`SET client_encoding = 'UTF8'`);
      const r1 = (await n.query(`SELECT snapshot,changes FROM ${RULE} WHERE revision=1`)).rows[0];
      assert.deepEqual(r1.snapshot.hardRules, R1_HARD_RULES); assert.deepEqual(r1.changes, R1_CHANGES);
      verdict(restored === 'EUC_KR' && sha256(canonicalV2(r1.snapshot)) === R1_HASH, 'erd119_encoding: statement-by-statement 016 in an EUC_KR session stores the r1 text intact and restores the session encoding', restored);
    } finally { await n.end(); }
  }
}

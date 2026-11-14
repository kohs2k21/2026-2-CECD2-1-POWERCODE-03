// 021: job kind recommend_relations (rule relation candidates, layer B), called from erd-v03.mjs inside the same disposable cluster.
// One database erd021: every file before 021 (one string each), 021 statement by statement (psql -f semantics), catalog differs only by the enum value and the
// run_jobs_v03_contract definition (two lists), re-run as one string gives the same catalog (prepare-runtime-db.py semantics), DBML parity, then real LOGIN roles:
// API creates the v03 training job and its queue message, Trainer claims, completes with result_summary and acknowledges (trainer/runner.py statements),
// dead-queue reconcile (pg_queue.reconcile_dead_jobs SQL read from the file) fails a crashed job. Small fixture rows only.
// result_summary values are test data; the database does not constrain their shape.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { catalog, delta, nameOf, statements } from './erd-v03-detect-v04.mjs';
import { reconcileStatements } from './erd-v03-llm-grant.mjs';

const KIND = 'recommend_relations';
const CONTRACT = /run_jobs_v03_contract/;
const LEGACY = { contract_version:null, queue:null, producer_kind:null, producer_key:null, idempotency_key:null, llm_spec:null };
// The only intended changes to the 017 definition, as pg_get_constraintdef prints them: legacy forbidden list, v03 training list.
const CONSTRAINT_EDITS = [["'compare'::text, 'recommend_methods'::text, 'recommend_thresholds'::text]", `'compare'::text, 'recommend_methods'::text, 'recommend_thresholds'::text, '${KIND}'::text]`],
  ["'compare'::text]", `'compare'::text, '${KIND}'::text]`]];
const llmSpec = () => ({ evidence_version:'e', input_evidence_hash:'a'.repeat(64), model_version:'m', prompt_version:'p', output_schema_version:'o', reuse_key:randomUUID() });

export async function recommendRelations021({ Client, port, connection, check, failures, migrations, migrationNames, rolesSql, parity, insert, infra }) {
  const idx = migrationNames.findIndex(n => n.startsWith('021_'));
  assert(idx > 0, '021 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const no = async (label, codeWanted, why, run) => {
    try { await run(); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === codeWanted && why.test(`${e.constraint ?? ''} ${e.message}`), `${label} (${codeWanted})`, `${e.code} ${e.constraint ?? ''} ${e.message}`); }
  };
  // pg_queue.reconcile_dead_jobs for the training queue, read as text (no runtime import), shared extraction from erd-v03-llm-grant.mjs.
  const reconcile = await reconcileStatements(infra);
  assert(reconcile.length === 2 && reconcile.every(s => !/[{}]/.test(s)), 'reconcile_dead_jobs has two fully substituted statements');

  const db = 'erd021';
  const admin = connection('postgres'); await admin.connect();
  try { await admin.query(`CREATE DATABASE ${db}`); } finally { await admin.end(); }
  const c = connection(db); await c.connect();
  const logins = {};
  const login = async role => { const a = new Client({ host:'127.0.0.1', port, user:logins[role].user, password:logins[role].password, database:db }); await a.connect(); return a; };
  // API request shape (test data): admin user producer, input fixed in input_spec, no config/snapshot/manifest/decision/llm_spec.
  const job = (over = {}) => ({ kind:KIND, status:'queued', contract_version:'v03', queue:'training', producer_kind:'user', producer_key:'admin903',
    requested_by_actor_id:'admin903', requester_role:'admin', idempotency_key:randomUUID(), input_spec:{ kind:KIND, sourceId:randomUUID(), ruleSetRevision:3 },
    max_attempts:3, attempt_count:0, ...over });
  const enqueue = (db, j) => db.query(`INSERT INTO powercode_queue.training_jobs(id,payload) VALUES($1,$2) RETURNING id`,
    [randomUUID(), JSON.stringify({ schemaVersion:1, jobId:j.id, enqueuedAt:new Date().toISOString() })]).then(r => r.rows[0].id);
  // pg_queue.claim (job_kinds scope) for one message, then trainer/runner.py _claim_job: row lock, queue ownership, new 60 s attempt, job running.
  const claim = async (db, j, messageId) => {
    const token = randomUUID(), attemptId = randomUUID(), attemptToken = randomUUID();
    await db.query('BEGIN');
    try {
      const leased = await db.query(`UPDATE powercode_queue.training_jobs SET status='leased',attempts=attempts+1,lease_token=$1,lease_until=clock_timestamp()+interval '60 seconds'
        WHERE id=$2 AND status='pending' AND EXISTS(SELECT 1 FROM run_jobs j WHERE j.id::text=payload->>'jobId' AND j.kind::text=ANY($3))`, [token, messageId, [KIND]]);
      assert.equal(leased.rowCount, 1, 'queue message leased by kind scope');
      await db.query(`SELECT id,kind,status,input_spec,attempt_count,max_attempts,current_attempt_id,requester_role,contract_version,llm_spec FROM run_jobs WHERE id=$1 FOR UPDATE`, [j.id]);
      await db.query(`SELECT 1 FROM powercode_queue.training_jobs WHERE id=$1 AND status='leased' AND lease_token=$2 AND lease_until>clock_timestamp() FOR UPDATE`, [messageId, token]);
      await db.query(`INSERT INTO run_job_attempts(id,job_id,attempt_no,status,worker_id,lease_token,lease_until,started_at)
        VALUES($1,$2,1,'running','trainer-021',$3,clock_timestamp()+interval '60 seconds',clock_timestamp())`, [attemptId, j.id, attemptToken]);
      await db.query(`UPDATE run_jobs SET status='running',attempt_count=attempt_count+1,current_attempt_id=$1 WHERE id=$2`, [attemptId, j.id]);
      await db.query('COMMIT');
    } catch (e) { await db.query('ROLLBACK'); throw e; }
    return { messageId, token, attemptId, attemptToken };
  };
  // trainer/runner.py _finish, success: ownership lock, attempt succeeded, job succeeded with result_summary, queue acknowledged. No run_llm_results row for this kind.
  const finish = async (db, j, m, summary) => {
    await db.query('BEGIN');
    try {
      const own = await db.query(`SELECT 1 FROM run_jobs AS j JOIN run_job_attempts AS a ON a.id=j.current_attempt_id AND a.job_id=j.id
        JOIN powercode_queue.training_jobs AS q ON q.id=$1 WHERE j.id=$2 AND j.status='running' AND a.id=$3 AND a.status='running' AND a.lease_token=$4
        AND a.lease_until>clock_timestamp() AND q.status='leased' AND q.lease_token=$5 AND q.lease_until>clock_timestamp() FOR UPDATE OF j, a, q`,
        [m.messageId, j.id, m.attemptId, m.attemptToken, m.token]);
      assert.equal(own.rowCount, 1, 'training ownership held');
      await db.query(`UPDATE run_job_attempts SET status=$1,finished_at=clock_timestamp(),error_code=$2 WHERE id=$3`, ['succeeded', null, m.attemptId]);
      await db.query(`UPDATE run_jobs SET status='succeeded',result_summary=$1::jsonb,result_config_version_id=$2,result_artifact_id=$3,
        finished_at=clock_timestamp(),error_code=NULL WHERE id=$4`, [JSON.stringify(summary), null, null, j.id]);
      const ack = await db.query(`UPDATE powercode_queue.training_jobs SET status='done',completed_at=clock_timestamp(),lease_until=NULL,lease_token=NULL
        WHERE id=$1 AND status='leased' AND lease_token=$2 AND lease_until>clock_timestamp()`, [m.messageId, m.token]);
      assert.equal(ack.rowCount, 1, 'queue message acknowledged');
      await db.query('COMMIT');
    } catch (e) { await db.query('ROLLBACK'); throw e; }
  };

  try {
    for (let i = 0; i < idx; i++) await c.query(migrations[i]);
    await no(`${db}: before 021 the kind does not exist`, '22P02', /invalid input value for enum job_kind/, () => insert(c, 'run_jobs', job()));
    const before = await catalog(c);

    // psql -f semantics: statement by statement. Korean comments sit between statements, so psql drops them.
    const parts = statements(migrations[idx].replaceAll('\r\n', '\n'));
    assert(parts[0].startsWith('ALTER TYPE job_kind ADD VALUE IF NOT EXISTS') && parts[1] === 'BEGIN;' && parts.at(-1) === 'COMMIT;', '021 splits into the enum statement and one BEGIN..COMMIT block');
    verdict(parts.every(p => /^[\x00-\x7f]*$/.test(p)), '021 sends ASCII statements only (no client_encoding dependence)');
    for (const part of parts) await c.query(part);
    check(`${db}: 001 through ${migrationNames[idx - 1]}, then ${migrationNames[idx]} applies statement by statement`);

    const after = await catalog(c);
    assert.deepEqual(delta(before.enums, after.enums), { removed:[], added:[`job_kind:${KIND}`] }, '021 adds one enum value');
    for (const key of ['columns', 'triggers', 'indexes']) assert.deepEqual(after[key], before[key], `021 leaves ${key} unchanged`);
    assert.deepEqual(after.functions.map(f => [f.name, f.body, f.config, f.definer, f.owner]), before.functions.map(f => [f.name, f.body, f.config, f.definer, f.owner]), '021 leaves functions unchanged');
    const con = delta(before.constraints, after.constraints);
    assert.deepEqual([con.removed.map(nameOf), con.added.map(nameOf)], [['run_jobs.run_jobs_v03_contract'], ['run_jobs.run_jobs_v03_contract']], 'only run_jobs_v03_contract changes');
    let expected = con.removed[0];
    for (const [from, to] of CONSTRAINT_EDITS) {
      assert.equal(expected.split(from).length - 1, 1, `017 definition has "${from}" exactly once`);
      expected = expected.replace(from, to);
    }
    assert.equal(con.added[0], expected, 'run_jobs_v03_contract differs from its 017 definition only by the two lists');
    verdict(con.added[0].split(':')[1] === 'true', `${db}: new run_jobs_v03_contract is validated (no NOT VALID)`);
    check(`${db}: enum gains ${KIND}; run_jobs_v03_contract differs from 017 only in the legacy and training lists; columns, triggers, indexes, functions unchanged`);

    await c.query(migrations[idx]);
    assert.deepEqual(await catalog(c), after, 're-run 021 gives the same catalog');
    check(`${db}: re-running 021 as one string succeeds and leaves the same catalog`);
    for (let i = idx + 1; i < migrations.length; i++) await c.query(migrations[i]);
    await parity(c);

    await c.query(rolesSql);
    for (const role of ['api', 'trainer']) {
      logins[role] = { user:`qa021_${role}`, password:randomUUID() };
      await c.query(`CREATE ROLE ${logins[role].user} LOGIN PASSWORD '${logins[role].password}' IN ROLE powercode_${role}`);
    }
    const api = await login('api'), trainer = await login('trainer');
    try {
      // API: job and queue message.
      const j1 = await insert(api, 'run_jobs', job());
      const m1 = await enqueue(api, j1);
      verdict(j1.kind === KIND && j1.queue === 'training' && j1.llm_spec === null && j1.input_manifest_id === null && j1.input_decision_id === null,
        `API role stores a v03 ${KIND} job on the training queue and its queue message`, JSON.stringify(j1));
      const queues = (await c.query(`SELECT e.enumlabel AS q FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid WHERE t.typname='queue_kind' ORDER BY e.enumsortorder`)).rows.map(r => r.q);
      assert(queues.includes('training') && queues.length > 1, 'queue_kind labels read');
      for (const queue of queues.filter(q => q !== 'training'))
        await no(`${KIND} rejected on queue ${queue}`, '23514', CONTRACT, () => insert(api, 'run_jobs', job({ queue })));
      await no(`${KIND} rejected with llm_spec`, '23514', CONTRACT, () => insert(api, 'run_jobs', job({ llm_spec:llmSpec() })));
      await no(`${KIND} rejected for an ordinary user`, '23514', CONTRACT, () => insert(api, 'run_jobs', job({ requester_role:'user' })));
      await no(`${KIND} rejected for a system producer`, '23514', CONTRACT, () => insert(api, 'run_jobs', job({ producer_kind:'system', producer_key:'system', requested_by_actor_id:null, requester_role:null })));
      await no(`${KIND} rejected when producer and requester differ`, 'P0001', /user producer mismatch/, () => insert(api, 'run_jobs', job({ producer_key:'someone-else' })));
      await no(`${KIND} rejected without an idempotency key`, '23514', CONTRACT, () => insert(api, 'run_jobs', job({ idempotency_key:null })));
      await no(`${KIND} rejected with an input manifest`, '23514', CONTRACT, () => insert(api, 'run_jobs', job({ input_manifest_id:randomUUID() })));
      await no(`${KIND} rejected with an input decision`, '23514', CONTRACT, () => insert(api, 'run_jobs', job({ input_decision_id:randomUUID() })));
      await no(`${KIND} cannot use the legacy NULL contract branch`, '23514', CONTRACT, () => insert(api, 'run_jobs', job(LEGACY)));
      await no(`${KIND} cannot be a v04 job`, '23514', CONTRACT, () => insert(api, 'run_jobs', job({ contract_version:'v04' })));
      await no(`${KIND} duplicate idempotency key rejected`, '23505', /run_jobs_requested_by_actor_id_kind_idempotency_key_key|run_jobs_v03_producer_key_idx/, () => insert(api, 'run_jobs', job({ idempotency_key:j1.idempotency_key })));
      verdict((await insert(api, 'run_jobs', job({ ...LEGACY, kind:'train' }))).contract_version === null, 'control: the same legacy row shape with kind train is accepted, so only the kind rejects the row above');

      // Trainer: claim, finish with result_summary, acknowledge.
      const summary = { candidateContractVersion:'test-only', candidates:[{ from:'A', to:'B', support:12 }], counts:{ candidates:1 } };
      const a1 = await claim(trainer, j1, m1);
      await finish(trainer, j1, a1, summary);
      const done = (await c.query(`SELECT j.status,j.error_code,j.result_summary,j.result_config_version_id,j.result_artifact_id,a.status AS attempt,q.status AS queue
        FROM run_jobs j JOIN run_job_attempts a ON a.id=j.current_attempt_id JOIN powercode_queue.training_jobs q ON q.id=$2 WHERE j.id=$1`, [j1.id, m1])).rows[0];
      assert.deepEqual(done, { status:'succeeded', error_code:null, result_summary:summary, result_config_version_id:null, result_artifact_id:null, attempt:'succeeded', queue:'done' });
      verdict((await c.query('SELECT count(*)::int AS n FROM run_llm_results WHERE job_id=$1', [j1.id])).rows[0].n === 0,
        `Trainer role claims, completes and acknowledges a ${KIND} job; result_summary stored, no run_llm_results row needed`);
      await no(`${KIND} job input stays immutable`, 'P0001', /v03 job input is immutable/, () => c.query(`UPDATE run_jobs SET input_spec='{"changed":true}' WHERE id=$1`, [j1.id]));
      // The result table stays closed to this kind: the CHECK answers with the validator trigger off in this transaction only.
      await c.query('BEGIN');
      try {
        await c.query('ALTER TABLE run_llm_results DISABLE TRIGGER llm_result_validate');
        await no(`${KIND} run_llm_results row rejected by the result CHECKs`, '23514', /run_llm_results_(kind_)?check/, () => insert(c, 'run_llm_results', { job_id:j1.id, kind:KIND,
          decision_id:null, config_version_id:null, ...llmSpec(), output:{}, created_attempt_id:a1.attemptId, lease_token:a1.attemptToken, completed_at:new Date() }));
      } finally { await c.query('ROLLBACK'); }

      // Crash: the lease runs out, completion is refused, the queue message dies and reconcile (Trainer role, one transaction) fails the job.
      const j2 = await insert(api, 'run_jobs', job());
      const m2 = await enqueue(api, j2);
      const a2 = await claim(trainer, j2, m2);
      await c.query(`UPDATE run_job_attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE id=$1`, [a2.attemptId]);
      await no(`${KIND} completion after lease expiry rejected`, 'P0001', /completion requires current unexpired attempt/, () => trainer.query(`UPDATE run_jobs SET status='succeeded' WHERE id=$1`, [j2.id]));
      await c.query(`UPDATE powercode_queue.training_jobs SET status='dead',lease_until=NULL,lease_token=NULL,last_error='lease_expired_max_attempts' WHERE id=$1`, [m2]);
      await trainer.query('BEGIN');
      try { for (const sql of reconcile) await trainer.query(sql); await trainer.query('COMMIT'); } catch (e) { await trainer.query('ROLLBACK'); throw e; }
      const dead = (await c.query(`SELECT j.status,j.error_code,j.result_summary,a.status AS attempt,a.error_code AS attempt_error FROM run_jobs j JOIN run_job_attempts a ON a.id=j.current_attempt_id WHERE j.id=$1`, [j2.id])).rows[0];
      assert.deepEqual(dead, { status:'failed', error_code:'queue_exhausted', result_summary:null, attempt:'expired', attempt_error:'queue_exhausted' });
      check(`${KIND}: expired lease cannot complete; Trainer role dead-queue reconcile expires the attempt and fails the job`);

      // Existing kinds next to the new one (their full flows run in the other databases, which also apply 021).
      verdict((await insert(api, 'run_jobs', job({ kind:'compare' }))).queue === 'training', 'compare stays accepted on the training queue');
      await no('recommend_thresholds still rejected on the training queue', '23514', CONTRACT, () => insert(api, 'run_jobs', job({ kind:'recommend_thresholds', llm_spec:llmSpec() })));
      verdict((await insert(api, 'run_jobs', job({ kind:'recommend_thresholds', queue:'llm_recommendation', llm_spec:llmSpec() }))).queue === 'llm_recommendation',
        'recommend_thresholds stays accepted on the llm_recommendation queue with llm_spec');
      for (const kind of ['import_csv', 'compare', 'recommend_thresholds'])
        await no(`${kind} still cannot use the legacy branch`, '23514', CONTRACT, () => insert(api, 'run_jobs', job({ ...LEGACY, kind })));
    } finally { for (const d of [api, trainer]) await d.end(); }
  } finally { await c.end(); }
}

// 017 (#124): job kind recommend_thresholds, called from erd-v03.mjs inside the same disposable cluster.
// Databases: erd124_fresh (001-017 in one string each + role scripts, flows by real LOGIN roles), erd124_upgrade (001-016 + existing rows -> 017 statement by statement, psql -f semantics),
// erd124_rollback (017 fails before COMMIT with the enum already committed), erd124_single (same failure as one string, prepare-runtime-db.py semantics), erd124_shadow (temp-table shadowing of the validators).
// Negative cases match the expected reason (message or constraint), not only the error class, so an unrelated rejection cannot pass.
// Stored values follow the Trainer implementation origin/feat/121-recommend-thresholds a9d215e (threshold_recommendation.py, trainer/runner.py _finish). They are test data, not database constraints.
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { catalog, code, delta, nameOf, statements } from './erd-v03-detect-v04.mjs';

const KIND = 'recommend_thresholds';
const OLD_KINDS = ['recommend_rule','recommend_feature','recommend_methods'];
const SPEC_FIELDS = ['evidence_version','input_evidence_hash','model_version','prompt_version','output_schema_version','reuse_key'];
const LEGACY = { contract_version:null, queue:null, producer_kind:null, producer_key:null, idempotency_key:null, llm_spec:null };
const spec = () => ({ evidence_version:'soft-rule-threshold-v1', input_evidence_hash:'a'.repeat(64), model_version:'rule_v2', prompt_version:'b'.repeat(64),
  output_schema_version:'threshold-recommendation-v1', reuse_key:randomUUID() });
// llm-recommendation-job.md request plus the fixed targets, as trainer/runner.py reads them: no configVersionId, no decisionId.
const inputSpec = sourceId => ({ kind:KIND, sourceId, ruleSetRevision:13, ruleSetContentHash:'c'.repeat(64), ruleSetContentHashVersion:2, scope:{ softRuleIds:null },
  data:{ snapshotId:null, from:'2026-08-01T00:00:00Z', until:'2026-11-01T00:00:00Z', timeBasis:'persisted_at' }, mode:'data_p95_with_llm_rationale',
  targets:[{ id:'soft:elapsed_ms-00000000', name:'elapsed', field:'elapsed_ms', operator:'gt', threshold:5000, severity:'warning' }] });
const output = () => ({ recommendationContractVersion:'threshold-recommendation-v1', mode:'data_p95_with_llm_rationale', items:[], counts:{ targets:0 } });
const llmJob = (sourceId, over = {}) => ({ kind:KIND, status:'queued', contract_version:'v03', queue:'llm_recommendation', producer_kind:'user', producer_key:'admin124',
  requested_by_actor_id:'admin124', requester_role:'admin', idempotency_key:randomUUID(), input_spec:inputSpec(sourceId), llm_spec:spec(), max_attempts:3, attempt_count:0, ...over });
const result = (job, attempt, over = {}) => ({ job_id:job.id, kind:job.kind, decision_id:null, config_version_id:null, ...job.llm_spec, output:output(),
  created_attempt_id:attempt.id, lease_token:attempt.lease_token, completed_at:new Date(), ...over });

// Definitions after 017, derived from the 016 definitions by the listed value only. Same text otherwise, so a redefinition from an older base cannot pass.
const METHODS = "'recommend_methods'::text";
const ADDED = `${METHODS}, 'recommend_thresholds'::text`;
const CONSTRAINT_EDITS = {
  'run_jobs.run_jobs_v03_contract': [[`'compare'::text, ${METHODS}`, `'compare'::text, ${ADDED}`],
    [`((kind)::text = ${METHODS})`, `((kind)::text = ANY (ARRAY[${ADDED}]))`]],
  'run_llm_results.run_llm_results_check': [[METHODS, ADDED]],
  'run_llm_results.run_llm_results_kind_check': [[METHODS, ADDED]],
};
const JOB_FUNCTION_EDIT = ["'explain','recommend_methods')", "'explain','recommend_methods','recommend_thresholds')"];

export async function recommendThresholds017({ Client, port, connection, check, failures, migrations, migrationNames, rolesSql, parity, insert }) {
  const idx = migrationNames.findIndex(n => n.startsWith('017_'));
  assert(idx > 0, '017 migration must be present');
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const verdict = (ok, label, detail) => ok ? check(label) : fail(label, detail);
  const no = async (label, codeWanted, why, run) => {
    try { await run(); fail(label, 'accepted'); }
    catch (e) { verdict(e.code === codeWanted && why.test(`${e.constraint ?? ''} ${e.message}`), `${label} (${codeWanted})`, `${e.code} ${e.constraint ?? ''} ${e.message}`); }
  };
  const open = async name => { const c = connection(name); await c.connect(); return c; };
  const upTo = async (c, end) => { for (let i = 0; i < end; i++) await c.query(migrations[i]); };
  const CONTRACT = /run_jobs_v03_contract/;

  const admin = connection('postgres'); await admin.connect();
  try { for (const name of ['erd124_fresh','erd124_upgrade','erd124_rollback','erd124_single','erd124_shadow']) await admin.query(`CREATE DATABASE ${name}`); }
  finally { await admin.end(); }

  // One source with its P observation, a config and a manifest. `db` needs owner rights.
  const world = async db => {
    const source = await insert(db, 'ops_sources', { dataset_key:`qa124-${randomUUID()}` });
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
  const detectJob = (w, version) => ({ kind:'detect', status:'queued', contract_version:version, queue:'detection', producer_kind:'system', producer_key:'collector-124',
    idempotency_key:randomUUID(), input_manifest_id:w.manifest.id, input_config_version_id:version === 'v03' ? w.config.id : null, max_attempts:3, attempt_count:0,
    requested_by_actor_id:null, requester_role:null });
  const decision = (w, job, attempt) => ({ execution_id:w.execution.id, process_observation_id:w.observation.id, config_version_id:job.input_config_version_id,
    input_manifest_id:job.input_manifest_id, input_manifest_hash:w.manifest.content_hash, input_as_of:w.asOf, job_id:job.id, result_attempt_id:attempt.id, result_lease_token:attempt.lease_token,
    status:'partial', rule_status:'succeeded', ml_status:'not_ready', latency_status:'unavailable', source_latency_ms:null, evidence:{ schemaVersion:4 }, decided_at:new Date() });
  const explainJob = (d, llmSpec) => ({ kind:'explain', status:'queued', contract_version:'v03', queue:'llm_explanation', producer_kind:'user', producer_key:'user124',
    requested_by_actor_id:'user124', requester_role:'user', idempotency_key:randomUUID(), input_decision_id:d.id, input_config_version_id:d.config_version_id,
    llm_spec:llmSpec, max_attempts:3, attempt_count:0 });
  const explainResult = (job, attempt) => result(job, attempt, { decision_id:job.input_decision_id, config_version_id:job.input_config_version_id });
  // _claim_job: new running attempt with a 60 s lease.
  const claim = async (db, job, n = 1) => {
    const attempt = await insert(db, 'run_job_attempts', { job_id:job.id, attempt_no:n, status:'running', lease_until:new Date(Date.now() + 60000) });
    await db.query(`UPDATE run_jobs SET status='running',current_attempt_id=$1,attempt_count=$2 WHERE id=$3`, [attempt.id, n, job.id]);
    return attempt;
  };
  const succeed = (db, job) => db.query(`UPDATE run_jobs SET status='succeeded',finished_at=clock_timestamp() WHERE id=$1`, [job.id]);
  const tx = async (db, run) => { await db.query('BEGIN'); try { await run(); await db.query('COMMIT'); } catch (e) { await db.query('ROLLBACK'); throw e; } };
  // trainer/runner.py _finish, success: same statements and order in one transaction. config and decision come from input_spec, which has neither.
  const finishSucceeded = (db, job, attempt) => tx(db, async () => {
    const s = job.llm_spec, summary = JSON.stringify(output());
    await db.query(`INSERT INTO run_llm_results
      (id,job_id,kind,decision_id,config_version_id,evidence_version,input_evidence_hash,model_version,prompt_version,
       output_schema_version,reuse_key,output,created_attempt_id,lease_token,completed_at)
      VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::jsonb,$13,$14,clock_timestamp())`,
      [randomUUID(), job.id, job.kind, job.input_spec.decisionId ?? null, job.input_spec.configVersionId ?? null, s.evidence_version, s.input_evidence_hash,
        s.model_version, s.prompt_version, s.output_schema_version, s.reuse_key, summary, attempt.id, attempt.lease_token]);
    await db.query(`UPDATE run_job_attempts SET status=$1,finished_at=clock_timestamp(),error_code=$2 WHERE id=$3`, ['succeeded', null, attempt.id]);
    await db.query(`UPDATE run_jobs SET status='succeeded',result_summary=$1::jsonb,result_config_version_id=$2,result_artifact_id=$3,
      finished_at=clock_timestamp(),error_code=NULL WHERE id=$4`, [summary, null, null, job.id]);
  });
  // trainer/runner.py _finish, terminal failure with failure_summary (recommendation_all_rejected): no run_llm_results row.
  const finishFailed = (db, job, attempt, errorCode, summary) => tx(db, async () => {
    await db.query(`UPDATE run_job_attempts SET status=$1,finished_at=clock_timestamp(),error_code=$2 WHERE id=$3`, ['failed', errorCode, attempt.id]);
    await db.query(`UPDATE run_jobs SET status='failed',error_code=$1,result_summary=$2::jsonb,finished_at=clock_timestamp() WHERE id=$3`,
      [errorCode, summary === null ? null : JSON.stringify(summary), job.id]);
  });
  const results = async (db, job) => (await db.query('SELECT count(*)::int AS n FROM run_llm_results WHERE job_id=$1', [job.id])).rows[0].n;

  const c = await open('erd124_fresh');
  const logins = {};
  const login = async role => { const a = new Client({ host:'127.0.0.1', port, user:logins[role].user, password:logins[role].password, database:'erd124_fresh' }); await a.connect(); return a; };
  try {
    for (const sql of migrations) await c.query(sql);
    check(`erd124_fresh: 001 through ${migrationNames.at(-1)} apply, each file as one string`);
    await c.query(rolesSql);
    for (const role of ['api','llm','collector','detector']) {
      logins[role] = { user:`qa124_${role}`, password:randomUUID() };
      await c.query(`CREATE ROLE ${logins[role].user} LOGIN PASSWORD '${logins[role].password}' IN ROLE powercode_${role}`);
    }
    await parity(c);
    const api = await login('api'), llm = await login('llm'), collector = await login('collector'), detector = await login('detector');
    try {
      const w = await world(c), sid = w.source.id;
      const enumQueues = (await c.query(`SELECT e.enumlabel AS q FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid WHERE t.typname='queue_kind'`)).rows.map(r => r.q);
      assert(enumQueues.includes('llm_recommendation') && enumQueues.length > 1, 'queue_kind labels read');

      // Job creation by the API role. The worker input stays in input_spec: no config, no snapshot, no decision, no manifest columns.
      const job = await insert(api, 'run_jobs', llmJob(sid));
      verdict(job.kind === KIND && job.contract_version === 'v03' && job.queue === 'llm_recommendation' && job.input_config_version_id === null && job.input_snapshot_id === null
        && job.input_decision_id === null && job.input_manifest_id === null, 'API role stores a v03 recommend_thresholds job with NULL config, snapshot, decision and manifest', JSON.stringify(job));
      for (const queue of enumQueues.filter(q => q !== 'llm_recommendation'))
        await no(`${KIND} rejected on queue ${queue}`, '23514', CONTRACT, () => insert(api, 'run_jobs', llmJob(sid, { queue })));
      await no(`${KIND} rejected without a queue`, '23514', CONTRACT, () => insert(api, 'run_jobs', llmJob(sid, { queue:null })));
      await no(`${KIND} rejected for an ordinary user`, '23514', CONTRACT, () => insert(api, 'run_jobs', llmJob(sid, { requester_role:'user' })));
      await no(`${KIND} rejected without a requester role`, '23514', CONTRACT, () => insert(api, 'run_jobs', llmJob(sid, { requester_role:null })));
      await no(`${KIND} rejected for a system producer`, '23514', CONTRACT, () => insert(api, 'run_jobs', llmJob(sid, { producer_kind:'system', producer_key:'system', requested_by_actor_id:null, requester_role:null })));
      await no(`${KIND} rejected when producer and requester differ`, 'P0001', /user producer mismatch/, () => insert(api, 'run_jobs', llmJob(sid, { producer_key:'someone-else' })));
      await no(`${KIND} rejected without an idempotency key`, '23514', CONTRACT, () => insert(api, 'run_jobs', llmJob(sid, { idempotency_key:null })));
      await no(`${KIND} rejected with an input manifest`, '23514', CONTRACT, () => insert(api, 'run_jobs', llmJob(sid, { input_manifest_id:w.manifest.id })));
      await no(`${KIND} cannot use legacy NULL contract branch`, '23514', CONTRACT, () => insert(api, 'run_jobs', llmJob(sid, LEGACY)));
      await no(`${KIND} cannot be a v04 job`, '23514', CONTRACT, () => insert(api, 'run_jobs', llmJob(sid, { contract_version:'v04' })));
      await no(`${KIND} duplicate idempotency key rejected`, '23505', /run_jobs_requested_by_actor_id_kind_idempotency_key_key|run_jobs_v03_producer_key_idx/, () => insert(api, 'run_jobs', llmJob(sid, { idempotency_key:job.idempotency_key })));
      const legacyTrain = await insert(api, 'run_jobs', llmJob(sid, { ...LEGACY, kind:'train' }));
      verdict(legacyTrain.contract_version === null, 'control: the same legacy row shape with kind train is accepted, so only the kind rejects the row above');

      // llm_spec: required before the job exists.
      await no(`${KIND} rejected without llm_spec`, 'P0001', /LLM spec object required/, () => insert(api, 'run_jobs', llmJob(sid, { llm_spec:null })));
      await no(`${KIND} llm_spec must be an object`, 'P0001', /LLM spec object required/, () => insert(api, 'run_jobs', llmJob(sid, { llm_spec:['rule_v2'] })));
      for (const field of SPEC_FIELDS) {
        const wanted = new RegExp(`LLM spec string required: ${field}`);
        await no(`${KIND} llm_spec.${field} required`, 'P0001', wanted, () => insert(api, 'run_jobs', llmJob(sid, { llm_spec:{ ...spec(), [field]:undefined } })));
        await no(`${KIND} llm_spec.${field} empty string rejected`, 'P0001', wanted, () => insert(api, 'run_jobs', llmJob(sid, { llm_spec:{ ...spec(), [field]:'' } })));
        await no(`${KIND} llm_spec.${field} number rejected`, 'P0001', wanted, () => insert(api, 'run_jobs', llmJob(sid, { llm_spec:{ ...spec(), [field]:1 } })));
      }

      // Result and completion by the LLM role.
      const attempt = await claim(llm, job, 1);
      await no(`${KIND} completion without a stored result rejected`, 'P0001', /LLM completion requires result/, () => succeed(llm, job));
      const row = over => insert(llm, 'run_llm_results', result(job, attempt, over));
      const LINEAGE = /LLM result\/job lineage mismatch/;
      await no(`${KIND} result with a decision rejected by the lineage check`, 'P0001', LINEAGE, () => row({ decision_id:randomUUID() }));
      await no(`${KIND} result with a config rejected: the job has none`, 'P0001', LINEAGE, () => row({ config_version_id:w.config.id }));
      await no(`${KIND} result of another kind rejected`, 'P0001', LINEAGE, () => row({ kind:'recommend_rule' }));
      for (const field of SPEC_FIELDS) await no(`${KIND} result must repeat llm_spec.${field}`, 'P0001', LINEAGE, () => row({ [field]:'other' }));
      await no(`${KIND} result requires the current lease token`, 'P0001', /current owner and unexpired lease/, () => row({ lease_token:randomUUID() }));
      await no(`${KIND} result output must be an object`, '23514', /run_llm_results_output_check|run_llm_results_check/, () => row({ output:['not','object'] }));
      await finishSucceeded(llm, job, attempt);
      const stored = (await c.query('SELECT kind::text,decision_id,config_version_id,evidence_version,model_version,output_schema_version FROM run_llm_results WHERE job_id=$1', [job.id])).rows;
      assert.deepEqual(stored, [{ kind:KIND, decision_id:null, config_version_id:null, evidence_version:'soft-rule-threshold-v1', model_version:'rule_v2', output_schema_version:'threshold-recommendation-v1' }]);
      const done = (await c.query('SELECT status,input_config_version_id,input_snapshot_id,result_config_version_id,error_code FROM run_jobs WHERE id=$1', [job.id])).rows[0];
      assert.deepEqual(done, { status:'succeeded', input_config_version_id:null, input_snapshot_id:null, result_config_version_id:null, error_code:null });
      check(`${KIND}: LLM role runs the _finish statements; result stored with NULL decision and config, job succeeds with NULL config`);
      await no(`${KIND} second result after success rejected`, 'P0001', /current owner and unexpired lease/, () => row({ reuse_key:randomUUID() }));
      await no(`${KIND} result is immutable`, 'P0001', /run_llm_results is immutable/, () => c.query(`UPDATE run_llm_results SET output='{"changed":true}' WHERE job_id=$1`, [job.id]));
      await no(`${KIND} job input is immutable`, 'P0001', /v03 job input is immutable/, () => c.query(`UPDATE run_jobs SET input_spec='{"changed":true}' WHERE id=$1`, [job.id]));
      const reused = await insert(api, 'run_jobs', llmJob(sid, { llm_spec:{ ...spec(), reuse_key:job.llm_spec.reuse_key } }));
      const reusedAttempt = await claim(llm, reused, 1);
      await no(`${KIND} result reuse key stays unique across jobs`, '23505', /run_llm_results_reuse_key_key/, () => insert(llm, 'run_llm_results', result(reused, reusedAttempt)));

      // The CHECK itself, behind the validator: disabled in this transaction only, so it is the constraint that answers.
      const scratch = await insert(c, 'run_jobs', llmJob(sid));
      const scratchAttempt = await claim(c, scratch, 1);
      const legacyDecisionWorld = await world(c);
      const someDecision = await insert(c, 'run_process_decisions', { execution_id:legacyDecisionWorld.execution.id, process_observation_id:legacyDecisionWorld.observation.id, config_version_id:legacyDecisionWorld.config.id,
        status:'unavailable', rule_status:'not_ready', ml_status:'not_ready', latency_status:'unavailable', source_latency_ms:null });
      for (const [label, over, wanted] of [[`${KIND} result with a decision rejected by run_llm_results_check itself`, { decision_id:someDecision.id }, /run_llm_results_check/],
        ['a kind outside the list stays rejected by the result CHECKs', { kind:'train' }, /run_llm_results_(kind_)?check/]]) {
        await c.query('BEGIN');
        try {
          await c.query('ALTER TABLE run_llm_results DISABLE TRIGGER llm_result_validate');
          await no(label, '23514', wanted, () => insert(c, 'run_llm_results', result(scratch, scratchAttempt, over)));
        } finally { await c.query('ROLLBACK'); }
      }

      // Must stay possible: a failed job keeps result_summary without any run_llm_results row.
      const rejectedJob = await insert(api, 'run_jobs', llmJob(sid));
      const rejectedAttempt = await claim(llm, rejectedJob, 1);
      const summary = { ...output(), items:[{ field:'elapsed_ms', status:'rejected', rejectedReason:'invalid_generation_output' }] };
      await finishFailed(llm, rejectedJob, rejectedAttempt, 'recommendation_all_rejected', summary);
      const failed = (await c.query('SELECT status,error_code,result_summary FROM run_jobs WHERE id=$1', [rejectedJob.id])).rows[0];
      assert.deepEqual(failed, { status:'failed', error_code:'recommendation_all_rejected', result_summary:summary });
      verdict(await results(c, rejectedJob) === 0, `${KIND}: failed job keeps result_summary and error_code without a run_llm_results row`);
      await no(`${KIND} result rejected after the job failed`, 'P0001', /current owner and unexpired lease/, () => insert(llm, 'run_llm_results', result(rejectedJob, rejectedAttempt)));
      const plainFailure = await insert(api, 'run_jobs', llmJob(sid));
      await finishFailed(llm, plainFailure, await claim(llm, plainFailure, 1), 'generation_timeout', null);
      const plain = (await c.query('SELECT status,error_code,result_summary FROM run_jobs WHERE id=$1', [plainFailure.id])).rows[0];
      assert.deepEqual(plain, { status:'failed', error_code:'generation_timeout', result_summary:null });
      check(`${KIND}: other failures keep no summary and no result`);

      // Must stay possible: a v03 job keeps input_snapshot_id NULL; the snapshot is recorded only in run_job_snapshots.
      const snapshot = await insert(c, 'data_snapshots', { source_id:sid, status:'ready' });
      const snapJob = await insert(api, 'run_jobs', llmJob(sid));
      const snapAttempt = await claim(llm, snapJob, 1);
      await llm.query('INSERT INTO run_job_snapshots(job_id,snapshot_id) VALUES($1,$2)', [snapJob.id, snapshot.id]);
      const legacyWrite = await llm.query('UPDATE run_jobs SET input_snapshot_id=$1 WHERE id=$2 AND contract_version IS NULL', [snapshot.id, snapJob.id]);
      verdict(legacyWrite.rowCount === 0, `${KIND}: the runner's legacy input_snapshot_id update touches no v03 row`);
      await no(`${KIND} v03 job cannot change input_snapshot_id after creation`, 'P0001', /v03 job input is immutable/, () => llm.query('UPDATE run_jobs SET input_snapshot_id=$1 WHERE id=$2', [snapshot.id, snapJob.id]));
      await finishSucceeded(llm, snapJob, snapAttempt);
      const fixed = (await c.query('SELECT j.status,j.input_snapshot_id,s.snapshot_id FROM run_jobs j JOIN run_job_snapshots s ON s.job_id=j.id WHERE j.id=$1', [snapJob.id])).rows;
      assert.deepEqual(fixed, [{ status:'succeeded', input_snapshot_id:null, snapshot_id:snapshot.id }]);
      check(`${KIND}: job succeeds with NULL input_snapshot_id and its snapshot in run_job_snapshots`);

      // Retry: a retryable failure returns the job to queued; only the new attempt can finish it. An expired lease completes nothing.
      const retry = await insert(api, 'run_jobs', llmJob(sid));
      const first = await claim(llm, retry, 1);
      await llm.query(`UPDATE run_job_attempts SET status='failed',finished_at=clock_timestamp(),error_code='generation_unavailable' WHERE id=$1`, [first.id]);
      await llm.query(`UPDATE run_jobs SET status='queued',error_code='generation_unavailable',next_run_at=clock_timestamp()+interval '5 seconds' WHERE id=$1`, [retry.id]);
      await no(`${KIND} result rejected while the job waits for a retry`, 'P0001', /current owner and unexpired lease/, () => insert(llm, 'run_llm_results', result(retry, first)));
      const second = await claim(llm, retry, 2);
      await no(`${KIND} result of the previous attempt rejected`, 'P0001', /current owner and unexpired lease/, () => insert(llm, 'run_llm_results', result(retry, first)));
      await c.query(`UPDATE run_job_attempts SET lease_until=clock_timestamp()-interval '1 second' WHERE id=$1`, [second.id]);
      await no(`${KIND} result after lease expiry rejected`, 'P0001', /current owner and unexpired lease/, () => insert(llm, 'run_llm_results', result(retry, second)));
      await no(`${KIND} completion after lease expiry rejected`, 'P0001', /completion requires current unexpired attempt/, () => succeed(llm, retry));
      await llm.query(`UPDATE run_job_attempts SET status='expired',finished_at=clock_timestamp() WHERE id=$1`, [second.id]);
      await finishSucceeded(llm, retry, await claim(llm, retry, 3));
      check(`${KIND}: job finishes on the attempt after a retry and an expired lease`);

      // Existing recommendation kinds keep their rules.
      for (const kind of OLD_KINDS) {
        const old = await insert(api, 'run_jobs', llmJob(sid, { kind }));
        await no(`${kind} still rejected on the training queue`, '23514', CONTRACT, () => insert(api, 'run_jobs', llmJob(sid, { kind, queue:'training' })));
        await no(`${kind} still rejected for an ordinary user`, '23514', CONTRACT, () => insert(api, 'run_jobs', llmJob(sid, { kind, requester_role:'user' })));
        await no(`${kind} still requires llm_spec`, 'P0001', /LLM spec object required/, () => insert(api, 'run_jobs', llmJob(sid, { kind, llm_spec:null })));
        const a = await claim(llm, old, 1);
        await no(`${kind} completion still requires a result`, 'P0001', /LLM completion requires result/, () => succeed(llm, old));
        await no(`${kind} result still cannot reference a decision`, 'P0001', /LLM result\/job lineage mismatch/, () => insert(llm, 'run_llm_results', result(old, a, { decision_id:randomUUID() })));
        await finishSucceeded(llm, old, a);
        check(`${kind}: job, result and success unchanged`);
      }
      for (const kind of ['recommend_feature','recommend_methods'])
        await no(`${kind} still cannot use the legacy branch`, '23514', CONTRACT, () => insert(api, 'run_jobs', llmJob(sid, { ...LEGACY, kind })));
      verdict((await insert(api, 'run_jobs', llmJob(sid, { ...LEGACY, kind:'recommend_rule' }))).contract_version === null, 'recommend_rule legacy rows stay allowed as before');

      // The 016 paths survive the redefinitions: v04 detect without config, then explain with NULL config on that decision.
      const v04 = await insert(collector, 'run_jobs', detectJob(w, 'v04'));
      await no('v04 job input stays immutable after 017', 'P0001', /v04 job input is immutable/, () => c.query(`UPDATE run_jobs SET input_spec='{"changed":true}' WHERE id=$1`, [v04.id]));
      const v04Attempt = await claim(detector, v04, 1);
      await no('v04 completion still requires a decision after 017', 'P0001', /detect completion requires decision/, () => succeed(detector, v04));
      const final = await insert(detector, 'run_process_decisions', decision(w, v04, v04Attempt));
      await succeed(detector, v04);
      const s = spec();
      const explain = await insert(api, 'run_jobs', explainJob(final, s));
      const ea = await claim(llm, explain, 1);
      await no('explain completion still requires a result after 017', 'P0001', /LLM completion requires result/, () => succeed(llm, explain));
      await no('explain result without its decision still rejected after 017', 'P0001', /LLM result\/job lineage mismatch/, () => insert(llm, 'run_llm_results', result(explain, ea)));
      const explained = await insert(llm, 'run_llm_results', explainResult(explain, ea));
      await succeed(llm, explain);
      verdict(explained.config_version_id === null && explained.decision_id === final.id, 'v04 detect and NULL-config explain still run end to end after 017');
      await no('v03 detect still requires a config after 017', 'P0001', /detect config\/input source mismatch/, () => insert(collector, 'run_jobs', { ...detectJob(w, 'v03'), input_config_version_id:null }));

      const settings = (await c.query(`SELECT proconfig,prosecdef FROM pg_proc WHERE proname='erd_v03_validate_job'`)).rows;
      assert.deepEqual(settings, [{ proconfig:['search_path=public, pg_temp'], prosecdef:false }]);
      check('erd_v03_validate_job keeps search_path=public, pg_temp and SECURITY INVOKER');
    } finally { for (const db of [api, llm, collector, detector]) await db.end(); }
  } finally { await c.end(); }

  // Existing rows of every related kind: rows kept, only the listed catalog objects change, in-flight work finishes afterwards. psql -f semantics: statement by statement.
  {
    const u = await open('erd124_upgrade');
    try {
      await upTo(u, idx);
      const w = await world(u), sid = w.source.id;
      await no('erd124_upgrade: before 017 the job cannot exist at all', '22P02', /invalid input value for enum job_kind/, () => insert(u, 'run_jobs', llmJob(sid)));
      const finished = {};
      for (const kind of OLD_KINDS) {
        const j = await insert(u, 'run_jobs', llmJob(sid, { kind }));
        await finishSucceeded(u, j, await claim(u, j, 1));
        finished[kind] = j;
      }
      const legacyRule = await insert(u, 'run_jobs', llmJob(sid, { ...LEGACY, kind:'recommend_rule' }));
      const detect = await insert(u, 'run_jobs', detectJob(w, 'v03'));
      const da = await claim(u, detect, 1);
      const old = await insert(u, 'run_process_decisions', decision(w, detect, da));
      await succeed(u, detect);
      const explain = await insert(u, 'run_jobs', explainJob(old, spec()));
      const ea = await claim(u, explain, 1);
      await insert(u, 'run_llm_results', explainResult(explain, ea));
      await succeed(u, explain);
      const running = await insert(u, 'run_jobs', llmJob(sid, { kind:'recommend_methods' }));
      const runningAttempt = await claim(u, running, 1);
      const queued = await insert(u, 'run_jobs', llmJob(sid, { kind:'recommend_rule' }));
      const failedOld = await insert(u, 'run_jobs', llmJob(sid, { kind:'recommend_feature' }));
      await finishFailed(u, failedOld, await claim(u, failedOld, 1), 'generation_failed:busy', null);
      const keptTables = ['run_jobs','run_job_attempts','run_llm_results','run_process_decisions','run_job_snapshots'];
      const rowsOf = async () => Object.fromEntries(await Promise.all(keptTables.map(async t => [t, (await u.query(`SELECT row_to_json(x)::text AS r FROM ${t} x ORDER BY 1`)).rows.map(r => r.r)])));
      const rows = await rowsOf(), before = await catalog(u);

      const parts = statements(migrations[idx].replaceAll('\r\n', '\n'));
      assert(parts[0].startsWith('ALTER TYPE job_kind ADD VALUE') && parts[1] === 'BEGIN;' && parts.at(-1) === 'COMMIT;', '017 splits into the enum statement and one BEGIN..COMMIT block');
      // Korean comments stay between statements, so psql drops them; nothing in a sent statement depends on client_encoding.
      verdict(parts.every(p => /^[\x00-\x7f]*$/.test(p)), '017 sends ASCII statements only (no client_encoding dependence)');
      for (const part of parts) await u.query(part);
      check(`erd124_upgrade: 001-016 + existing rows, then ${migrationNames[idx]} applies statement by statement`);
      assert.deepEqual(await rowsOf(), rows, 'rows written before 017 are kept unchanged');
      check(`erd124_upgrade: rows kept (${keptTables.join(', ')})`);
      const after = await catalog(u);
      assert.deepEqual(delta(before.enums, after.enums), { removed:[], added:[`job_kind:${KIND}`] }, '017 adds one enum value');
      for (const key of ['columns','triggers','indexes']) assert.deepEqual(after[key], before[key], `017 leaves ${key} unchanged`);
      const con = delta(before.constraints, after.constraints);
      assert.deepEqual([con.removed.map(nameOf), con.added.map(nameOf)], [Object.keys(CONSTRAINT_EDITS), Object.keys(CONSTRAINT_EDITS)], 'only the 3 listed constraints change');
      for (const [name, edits] of Object.entries(CONSTRAINT_EDITS)) {
        let expected = before.constraints.find(v => nameOf(v) === name);
        for (const [from, to] of edits) { assert(expected.includes(from), `${name}: 016 definition has "${from}"`); expected = expected.replaceAll(from, to); }
        assert.equal(after.constraints.find(v => nameOf(v) === name), expected, `${name} differs from its 016 definition only by the new value`);
      }
      verdict(after.constraints.every(v => v.split(':')[1] === 'true'), 'erd124_upgrade: every constraint is validated (no NOT VALID left)');
      check('erd124_upgrade: enum gains one value; 3 constraints differ from their 016 definitions only by that value; columns, triggers and indexes unchanged');
      const changed = after.functions.filter(f => before.functions.find(b => b.name === f.name)?.body !== f.body).map(f => f.name);
      assert.deepEqual(changed, ['erd_v03_validate_job'], 'only erd_v03_validate_job changes');
      assert.equal(after.functions.length, before.functions.length, '017 adds no function');
      for (const f of after.functions) {
        const o = before.functions.find(b => b.name === f.name);
        assert.deepEqual([f.config, f.definer, f.owner], [o.config, o.definer, o.owner], `${f.name} settings kept`);
      }
      const oldBody = code(before.functions.find(f => f.name === 'erd_v03_validate_job').src);
      assert.equal(oldBody.split(JOB_FUNCTION_EDIT[0]).length - 1, 2, '016 body has the two kind lists');
      assert.equal(code(after.functions.find(f => f.name === 'erd_v03_validate_job').src), oldBody.replaceAll(...JOB_FUNCTION_EDIT), 'function differs from 016 only by the two lists');
      check('erd124_upgrade: erd_v03_validate_job differs from its 016 body only by the two kind lists; config, SECURITY and owner kept; no function added');
      // Later files (018 index, 019 tables) are applied before parity because schema.dbml is the final structure.
      for (let i = idx + 1; i < migrations.length; i++) await u.query(migrations[i]);
      await parity(u);

      // Work in flight when 017 ran.
      await no('erd124_upgrade: running recommend_methods job still needs its result', 'P0001', /LLM completion requires result/, () => succeed(u, running));
      await finishSucceeded(u, running, runningAttempt);
      await finishSucceeded(u, queued, await claim(u, queued, 1));
      check('erd124_upgrade: jobs running and queued before 017 finish');
      await u.query(`UPDATE run_jobs SET status='running' WHERE id=$1`, [legacyRule.id]);
      check('erd124_upgrade: legacy recommend_rule row stays updateable');
      const fresh = await insert(u, 'run_jobs', llmJob(sid));
      await finishSucceeded(u, fresh, await claim(u, fresh, 1));
      check(`erd124_upgrade: new ${KIND} job runs next to the old rows`);
    } finally { await u.end(); }
  }

  // psql -f semantics: the enum commits first, then BEGIN..COMMIT fails. Only the enum value stays and the job is still refused.
  {
    const r = await open('erd124_rollback');
    try {
      await upTo(r, idx);
      const w = await world(r), sid = w.source.id;
      const text = migrations[idx].replaceAll('\r\n', '\n');
      const begin = text.search(/^BEGIN;$/m), commit = text.lastIndexOf('\nCOMMIT;');
      assert(begin > 0 && commit > begin, '017 has one BEGIN..COMMIT block after its enum statement');
      const before = await catalog(r);
      await r.query(text.slice(0, begin));
      let error = null;
      try { await r.query(text.slice(begin, commit + 1) + 'SELECT 1/0; -- injected failure after every statement\nCOMMIT;\n'); } catch (e) { error = e; }
      await r.query('ROLLBACK');
      assert(error && error.code === '22012', `injected 017 failure must abort the migration (${error?.message})`);
      const after = await catalog(r);
      assert.deepEqual(delta(before.enums, after.enums), { removed:[], added:[`job_kind:${KIND}`] }, 'committed enum value remains');
      assert.deepEqual({ ...after, enums:null }, { ...before, enums:null }, '017 structural changes rolled back');
      check('erd124_rollback: 017 failing before COMMIT leaves only the committed enum value');
      await no('erd124_rollback: the job stays refused until 017 finishes', '23514', CONTRACT, () => insert(r, 'run_jobs', llmJob(sid)));

      // The window the migration header warns about: the old legacy branch does not know the value. A row there makes the next run fail whole, never half applied.
      const stray = await insert(r, 'run_jobs', llmJob(sid, { ...LEGACY }));
      let stopped = null;
      try { await r.query(migrations[idx]); } catch (e) { stopped = e; }
      await r.query('ROLLBACK');
      verdict(stopped?.code === '23514' && stopped?.constraint === 'run_jobs_v03_contract', 'erd124_rollback: a legacy row written in the enum-only window stops the re-run at CHECK validation', `${stopped?.code} ${stopped?.constraint} ${stopped?.message}`);
      assert.deepEqual({ ...await catalog(r), enums:null }, { ...before, enums:null }, 'stopped 017 leaves the structure unchanged');
      await r.query('DELETE FROM run_jobs WHERE id=$1', [stray.id]);

      await r.query(migrations[idx]);
      check('erd124_rollback: 017 applies cleanly afterwards (enum ADD VALUE IF NOT EXISTS)');
      // Later files (018 index, 019 tables) are applied before parity because schema.dbml is the final structure.
      for (let i = idx + 1; i < migrations.length; i++) await r.query(migrations[i]);
      await parity(r);
      const job = await insert(r, 'run_jobs', llmJob(sid));
      await finishSucceeded(r, job, await claim(r, job, 1));
      check(`erd124_rollback: ${KIND} job runs after the re-run`);
    } finally { await r.end(); }
  }

  // One string (prepare-runtime-db.py semantics): BEGIN absorbs the enum statement, so a failure takes the enum value with it, and a success can use the value at once.
  {
    const s = await open('erd124_single');
    try {
      await upTo(s, idx);
      const text = migrations[idx].replaceAll('\r\n', '\n');
      const commit = text.lastIndexOf('\nCOMMIT;');
      const before = await catalog(s);
      let error = null;
      try { await s.query(text.slice(0, commit + 1) + 'SELECT 1/0; -- injected failure after every statement\n' + text.slice(commit + 1)); } catch (e) { error = e; }
      await s.query('ROLLBACK');
      assert(error && error.code === '22012', `injected 017 failure must abort the migration (${error?.message})`);
      assert.deepEqual(await catalog(s), before, 'one-string 017 failure rolls back the enum value too');
      check('erd124_single: one-string 017 failing before COMMIT leaves nothing, enum value included');
      await s.query(migrations[idx]);
      const w = await world(s);
      const job = await insert(s, 'run_jobs', llmJob(w.source.id));
      await finishSucceeded(s, job, await claim(s, job, 1));
      check(`erd124_single: one-string 017 applies with no unsafe enum use; the same connection runs a ${KIND} job`);
      // Later files (018 index, 019 tables) are applied before parity because schema.dbml is the final structure.
      for (let i = idx + 1; i < migrations.length; i++) await s.query(migrations[i]);
      await parity(s);
    } finally { await s.end(); }
  }

  // Temp-table shadowing. Each attack is a new connection whose first reference to the name is the attacker's table (a warmed plan cache would hide the hole).
  // database-roles.sql revokes TEMP (asserted by the 011 search_path test), so it is granted back here to judge the 017 redefinition alone.
  {
    const o = await open('erd124_shadow');
    let noResult, expired;
    try {
      for (const sql of migrations) await o.query(sql);
      await o.query(rolesSql);
      await o.query('GRANT TEMPORARY ON DATABASE erd124_shadow TO PUBLIC');
      const w = await world(o);
      noResult = await insert(o, 'run_jobs', llmJob(w.source.id));
      noResult.attempt = await claim(o, noResult, 1);
      expired = await insert(o, 'run_jobs', llmJob(w.source.id));
      expired.attempt = await claim(o, expired, 1);
      await insert(o, 'run_llm_results', result(expired, expired.attempt));
      await o.query(`UPDATE run_job_attempts SET lease_until=clock_timestamp()-interval '1 minute' WHERE id=$1`, [expired.attempt.id]);
    } finally { await o.end(); }
    const attack = async (label, wanted, shadow, job) => {
      const a = connection('erd124_shadow'); await a.connect();
      try {
        await a.query('BEGIN');
        await a.query('SET ROLE powercode_llm');
        for (const [sql, params] of shadow) await a.query(sql, params);
        await no(label, 'P0001', wanted, () => a.query(`UPDATE public.run_jobs SET status='succeeded' WHERE id=$1`, [job.id]));
      } finally { await a.query('ROLLBACK').catch(() => {}); await a.end(); }
    };
    await attack(`validate_job: LLM temp run_llm_results cannot fake the result of a ${KIND} job`, /LLM completion requires result/,
      [['CREATE TEMP TABLE run_llm_results(job_id uuid,created_attempt_id uuid)'], ['INSERT INTO pg_temp.run_llm_results VALUES($1::uuid,$2::uuid)', [noResult.id, noResult.attempt.id]]], noResult);
    await attack(`validate_job: LLM temp run_job_attempts cannot fake an unexpired lease of a ${KIND} job`, /completion requires current unexpired attempt/,
      [['CREATE TEMP TABLE run_job_attempts AS SELECT * FROM public.run_job_attempts WHERE job_id=$1', [expired.id]], [`UPDATE pg_temp.run_job_attempts SET lease_until=clock_timestamp()+interval '1 hour'`]], expired);
  }
}

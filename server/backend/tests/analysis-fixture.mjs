// 이상 분석 읽기 API 시험용 로컬 고정 데이터(합성). 로컬 QA 데이터베이스에만 넣는다.
// 고객 원본·BODY 값은 없다. 시각은 DB clock_timestamp() 기준 상대값으로 넣는다.
// v03 detect 작업·시도·중간 판정 기록은 실제 DB 트리거(004·012)를 통과하는 순서로 만든다(대체됨·최종 없음은 트리거가 기록).
import crypto from 'node:crypto';

const id = () => crypto.randomUUID();
const sha = (value) => crypto.createHash('sha256').update(value).digest('hex');

const v3 = (anomalous, extra = {}) => ({
  schemaVersion: 3,
  rules: [{ ruleKey: 'process-status', version: 'process-status-hr-v1', observedStatus: anomalous ? 'F' : 'S',
    result: anomalous ? 'hit' : 'no_hit', severity: anomalous ? 'critical' : null, reason: null }],
  models: [{ modelKey: 'iforest', anomalous: false }],
  checkers: {
    hr: { status: 'succeeded', anomalous: Boolean(anomalous), evidence: [] },
    sr: { status: 'not_ready', anomalous: null, evidence: [{ reason: 'sr_experiment_pending' }] },
    ml: { status: 'succeeded', anomalous: false, evidence: [{ modelKey: 'iforest', anomalous: false }] },
  },
  anomalous, requestedMode: 'parallel', actualPaths: ['rule', 'ml'],
  detectionPolicy: { mode: 'parallel', deadlineMs: 3000, combination: 'any_anomaly' },
  ruleSeverities: anomalous ? ['critical'] : [],
  ...extra,
});
const v4Payload = (grade, basis, elapsedMs) => ({
  schemaVersion: 4, ruleSetRevision: 12, ruleSetHash: 'a'.repeat(64),
  detectionPolicy: { version: 2, policyKey: 'category-k-of-n-or-v1', k: { hard: 1, soft: 1, ml: 1 }, deadlineMs: 3000 },
  methodResults: [
    { methodId: 'hard:process-status', category: 'hard', methodVersion: 'process-status-hr-v1', status: 'succeeded',
      anomalous: grade === 'anomaly', code: grade === 'anomaly' ? 'PROCESS_STATUS_F' : null, severity: grade === 'anomaly' ? 'critical' : null,
      riskLevel: grade === 'anomaly' ? 'critical' : null, riskLevelReason: null, cause: null, failureCode: null, elapsedMs: 9.1, evidence: [] },
    { methodId: 'soft:elapsed_ms-3f9a2c1d', category: 'soft', methodVersion: 'soft-threshold-v1', status: 'succeeded', anomalous: true,
      code: 'SOFT_ELAPSED_MS_3F9A2C1D', severity: 'warning', riskLevel: 'warning', riskLevelReason: null, cause: null, failureCode: null,
      elapsedMs: 9.3, condition: { field: 'elapsed_ms', operator: 'gt', threshold: '5000', unit: 'ms' }, observedValue: '8120', evidence: [] },
    { methodId: 'ml:iforest', category: 'ml', methodVersion: 'process-iforest-v1', status: 'not_ready', anomalous: null, code: null,
      severity: null, riskLevel: null, riskLevelReason: null, cause: 'pending', failureCode: null, elapsedMs: null, evidence: [] },
  ],
  categoryResults: {
    hard: { n: 1, k: 1, result: grade === 'anomaly' ? 'anomalous' : 'normal', status: 'succeeded', anomalous: grade === 'anomaly' },
    soft: { n: 1, k: 1, result: 'anomalous', status: 'succeeded', anomalous: true },
    ml: { n: 1, k: 1, result: 'undetermined', status: 'not_ready', anomalous: null },
  },
  composite: { policy: { version: 2, policyKey: 'category-k-of-n-or-v1', k: { hard: 1, soft: 1, ml: 1 } }, status: 'partial',
    anomalous: true, grade, basisMethods: basis, firstAnomalyElapsedMs: 9.1,
    risk: { policy: 'risk-max-v1', levelTable: 'method-defined-v2', status: 'rated', level: grade === 'anomaly' ? 'critical' : 'warning',
      determinedBy: [basis[0]], unratedMethods: [] } },
  elapsedMs,
});

/**
 * @param {import('pg').Pool} pool
 * @param {{key?: string, legacy?: number}} options legacy = 추가로 만들 이전 형식(작업 없는) 정상 판정 수
 */
export async function seedAnalysisFixture(pool, options = {}) {
  const { key = `analysis-fixture-${id()}`, legacy = 0 } = options;
  const actor = 'analysis-fixture';
  const sourceId = id(), configId = id(), featureSetId = id(), ruleId = id();
  const client = await pool.connect();
  const out = { sourceId, configId, decisions: {}, jobs: {} };
  try {
    await client.query('BEGIN');
    await client.query(`INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
      VALUES($1,$2,$3,'switchable-input','arrival-snapshot-v1','{"status":"unknown"}','{"layers":["T","P","M","B"]}',clock_timestamp())`,
    [sourceId, key, key]);
    await client.query(`INSERT INTO ml_feature_sets(id,schema_version,transform_version,output_schema,content_hash,created_by_actor_id,created_at)
      VALUES($1,'v1','v1','{}',$2,$3,clock_timestamp())`, [featureSetId, sha(featureSetId), actor]);
    await client.query(`INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,created_by_actor_id,created_at)
      VALUES($1,$2,'{"detectionPolicy":{"mode":"parallel","deadlineMs":3000,"combination":"any_anomaly"}}','{}',$3,$4,clock_timestamp()-interval '2 days')`,
    [configId, sourceId, sha(configId), actor]);
    await client.query(`INSERT INTO ml_rule_versions(id,rule_key,version,rule_spec,input_spec,evidence_spec,content_hash,created_by_actor_id,created_at)
      VALUES($1,$2,1,'{"outcome":{"decisionCode":"FIXTURE_RULE","severity":"warning"}}','{}','{}',$3,$4,clock_timestamp())`,
    [ruleId, `fixture_${ruleId.slice(0, 8)}`, sha(ruleId), actor]);
    await client.query('INSERT INTO ml_config_rules(config_version_id,rule_version_id,evaluation_order) VALUES($1,$2,0)', [configId, ruleId]);
    await client.query(`INSERT INTO ml_config_models(config_version_id,model_key,feature_set_id,adapter_spec,training_settings,threshold_spec)
      VALUES($1,'iforest',$2,'{"name":"sklearn-iforest","formatVersion":"1"}','{}','{}')`, [configId, featureSetId]);

    let serial = 0;
    /** PROCESS 관측 1건(+ 짝 TRANSACTION). 검색 대상: 거래 ID·프로세스 ID·인터페이스 ID. */
    const process = async (minutesAgo, label) => {
      serial += 1;
      const tx = `TX-${label}-${serial}`, processId = `P${String(serial % 7).padStart(2, '0')}`, iface = `IF-ORD-${(serial % 3) + 1}`;
      const execution = id(), obs = id(), txObs = id();
      await client.query(`INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at)
        VALUES($1,$2,'P',$3,'{}','v1',clock_timestamp())`, [execution, sourceId, `exec-${execution}`]);
      for (const [observation, layer, exec] of [[txObs, 'T', null], [obs, 'P', execution]]) {
        await client.query(`INSERT INTO data_observations(id,source_id,layer,execution_id,revision_no,dedup_key,content_hash,
            api_received_at,persisted_at,clock_status,parsed_times,allowed_values,quality_flags,missing_fields)
          VALUES($1,$2,$3,$4,$5,$6,$7,clock_timestamp()-($8::double precision*interval '1 minute'),
            clock_timestamp()-($8::double precision*interval '1 minute'),'unknown','{}','{}','[]','[]')`,
        [observation, sourceId, layer, exec, exec ? 1 : null, `${layer}:${observation}`, sha(observation), minutesAgo + 0.5]);
      }
      await client.query(`INSERT INTO data_transactions(observation_id,transaction_id,interface_id,status) VALUES($1,$2,$3,'S')`, [txObs, tx, iface]);
      await client.query(`INSERT INTO data_processes(observation_id,transaction_observation_id,transaction_id,process_id,channel_id,status,response_code)
        VALUES($1,$2,$3,$4,'CH-01',$5,$6)`, [obs, txObs, tx, processId, label === 'anomaly' ? 'F' : 'S', label === 'anomaly' ? 'E500' : '0000']);
      return { obs, execution, txObs, tx, processId, iface };
    };
    /** 이전 형식 판정(작업 없음). */
    const legacyDecision = async (name, minutesAgo, status, evidence, ruleStatus = 'succeeded', mlStatus = 'succeeded', code = null) => {
      const p = await process(minutesAgo, name);
      const decisionId = id();
      await client.query(`INSERT INTO run_process_decisions(id,execution_id,process_observation_id,transaction_observation_id,config_version_id,
          input_manifest_hash,status,rule_status,ml_status,decision_code,anomaly_score,evidence,decided_at,latency_status)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,0.42,$11::jsonb,clock_timestamp()-($12::double precision*interval '1 minute'),'unavailable')`,
      [decisionId, p.execution, p.obs, p.txObs, configId, sha(p.obs), status, ruleStatus, mlStatus, code, JSON.stringify(evidence), minutesAgo]);
      out.decisions[name] = { id: decisionId, ...p };
      return decisionId;
    };
    /** v03 detect 작업(시도 없음, queued). */
    const detectJob = async (name, minutesAgo) => {
      const p = await process(minutesAgo, name);
      const manifest = id(), job = id(), hash = sha(`manifest:${p.obs}`);
      await client.query(`INSERT INTO run_input_manifests(id,source_id,execution_id,process_observation_id,transaction_observation_id,
          message_observation_refs,readiness,input_as_of,contract_version,content_hash,created_at)
        VALUES($1,$2,$3,$4,$5,'[]','{}',clock_timestamp()-($6::double precision*interval '1 minute'),'v03',$7,clock_timestamp())`,
      [manifest, sourceId, p.execution, p.obs, p.txObs, minutesAgo, hash]);
      await client.query(`INSERT INTO run_jobs(id,kind,status,request_hash,input_config_version_id,input_spec,max_attempts,attempt_count,
          next_run_at,created_at,contract_version,queue,producer_kind,producer_key,idempotency_key,input_manifest_id)
        VALUES($1,'detect','queued',$2,$3,$4::jsonb,3,0,clock_timestamp(),clock_timestamp()-($5::double precision*interval '1 minute'),
          'v03','detection','system','analysis-fixture',$6,$7)`,
      [job, sha(job), configId, JSON.stringify({ sourceId, observationId: p.obs }), minutesAgo, `detect:${job}`, manifest]);
      out.jobs[name] = { id: job, manifest, hash, ...p };
      return out.jobs[name];
    };
    const attempt = async (job, n) => {
      const attemptId = id(), token = id();
      await client.query(`INSERT INTO run_job_attempts(id,job_id,attempt_no,status,worker_id,lease_token,lease_until,started_at)
        VALUES($1,$2,$3,'running','fixture-detector',$4,clock_timestamp()+interval '60 seconds',clock_timestamp())`, [attemptId, job.id, n, token]);
      await client.query(`UPDATE run_jobs SET status='running',current_attempt_id=$2,attempt_count=$3 WHERE id=$1`, [job.id, attemptId, n]);
      return { id: attemptId, token };
    };
    const interim = (job, att, kind, payload) => client.query(`INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,lease_token,evidence_version,payload)
      VALUES($1,$2,$3,NULL,$4,$5,'detector-evidence-v4',$6::jsonb)`, [id(), job.id, att.id, kind, att.token, JSON.stringify(payload)]);
    const jobDecision = async (job, att, status, evidence, code = null) => {
      const decisionId = id();
      await client.query(`INSERT INTO run_process_decisions(id,execution_id,process_observation_id,transaction_observation_id,config_version_id,
          input_manifest_id,input_manifest_hash,input_as_of,job_id,result_attempt_id,result_lease_token,status,rule_status,ml_status,
          decision_code,anomaly_score,evidence,decided_at,latency_status)
        VALUES($1,$2,$3,$4,$5,$6,$7,(SELECT input_as_of FROM run_input_manifests WHERE id=$6),$8,$9,$10,$11,'succeeded','succeeded',$12,0.61,
          $13::jsonb,clock_timestamp(),'unavailable')`,
      [decisionId, job.execution, job.obs, job.txObs, configId, job.manifest, job.hash, job.id, att.id, att.token, status, code,
        JSON.stringify(evidence)]);
      await client.query(`UPDATE run_job_attempts SET status='succeeded',finished_at=clock_timestamp() WHERE id=$1`, [att.id]);
      await client.query(`UPDATE run_jobs SET status='succeeded',finished_at=clock_timestamp() WHERE id=$1`, [job.id]);
      return decisionId;
    };
    const failJob = async (job, att) => {
      if (att) await client.query(`UPDATE run_job_attempts SET status='failed',finished_at=clock_timestamp(),error_code='detection_processing_failed' WHERE id=$1`, [att.id]);
      await client.query(`UPDATE run_jobs SET status='failed',finished_at=clock_timestamp(),error_code='detection_processing_failed' WHERE id=$1`, [job.id]);
    };

    // 이전 형식(작업 없음) 판정: 분류 매핑의 경계값.
    await legacyDecision('anomaly', 30, 'complete', v3(true), 'succeeded', 'succeeded', 'PROCESS_STATUS_F');
    await legacyDecision('anomalyPartial', 29, 'partial', v3(true), 'succeeded', 'not_ready', 'PROCESS_STATUS_F');
    await legacyDecision('normal', 28, 'complete', v3(false));
    await legacyDecision('partialFalse', 27, 'partial', v3(false, { models: [{ reason: 'deadline_exceeded' }] }), 'succeeded', 'not_ready');
    await legacyDecision('unavailable', 26, 'unavailable', v3(null, { rules: [{ reason: 'rules_unavailable' }], models: [{ reason: 'model_unavailable' }] }),
      'not_ready', 'not_ready');
    await legacyDecision('noAnomalousKey', 25, 'complete', { requestedMode: 'parallel' });
    await legacyDecision('demo', 24, 'complete', v3(false, { demo: { historicalReplay: true, runKey: 'fixture', scope: 'Process replay' } }));
    for (let index = 0; index < legacy; index += 1) await legacyDecision(`legacy${index}`, 40 + index, 'complete', v3(index % 4 === 0));

    // v03 작업: 1) 이전 시도 중간 기록이 대체된 뒤 최종 판정(대체됨) 2) 열린 중간 기록(근거 추가) 3) 대기 4) 중간 기록 뒤 실패(최종 없음)
    // 5) 중간 기록 없이 실패 6) 중간 기록 없는 최종(v4 근거).
    const replacedJob = await detectJob('replaced', 12);
    const r1 = await attempt(replacedJob, 1);
    await interim(replacedJob, r1, 'first_anomaly', v4Payload('anomaly_sign', ['soft:elapsed_ms-3f9a2c1d'], 9.4));
    await client.query(`UPDATE run_job_attempts SET status='expired' WHERE id=$1`, [r1.id]);
    const r2 = await attempt(replacedJob, 2);
    out.decisions.replaced = { ...replacedJob, id: await jobDecision(replacedJob, r2, 'complete', v3(false)), jobId: replacedJob.id };

    const interimJob = await detectJob('interim', 3);
    const i1 = await attempt(interimJob, 1);
    await interim(interimJob, i1, 'first_anomaly', v4Payload('anomaly_sign', ['soft:elapsed_ms-3f9a2c1d'], 9.4));
    await interim(interimJob, i1, 'evidence_added', v4Payload('anomaly', ['hard:process-status', 'soft:elapsed_ms-3f9a2c1d'], 11.2));

    await detectJob('pending', 1);

    const noFinalJob = await detectJob('noFinal', 8);
    const n1 = await attempt(noFinalJob, 1);
    await interim(noFinalJob, n1, 'first_anomaly', v4Payload('anomaly', ['hard:process-status'], 9.1));
    await failJob(noFinalJob, n1);

    const failedJob = await detectJob('failed', 7);
    await failJob(failedJob, await attempt(failedJob, 1));

    const v4Job = await detectJob('v4Final', 5);
    const f1 = await attempt(v4Job, 1);
    const { elapsedMs: _drop, ...finalV4 } = v4Payload('anomaly_sign', ['soft:elapsed_ms-3f9a2c1d'], 0);
    out.decisions.v4Final = { ...v4Job, id: await jobDecision(v4Job, f1, 'partial',
      { ...v3(true), ...finalV4, composite: { ...finalV4.composite, status: 'partial' } }, 'SOFT_ELAPSED_MS_3F9A2C1D'), jobId: v4Job.id };
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
  return out;
}

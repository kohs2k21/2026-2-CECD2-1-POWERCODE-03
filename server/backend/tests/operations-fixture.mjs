// 운영 현황 읽기 API 시험용 로컬 고정 데이터(합성). 로컬 QA 데이터베이스에만 넣는다.
// 고객 원본·BODY 값은 없다. 시각은 DB clock_timestamp() 기준 상대값으로 넣는다.
import crypto from 'node:crypto';
import { v04Final } from './v04-samples.mjs';

const id = () => crypto.randomUUID();
const sha = (value) => crypto.createHash('sha256').update(value).digest('hex');

/**
 * @param {import('pg').Pool} pool
 * @param {{hours?: number, transactionsPerHour?: (hourAgo: number) => number, processes?: number,
 *          recentProcesses?: number, decisions?: number, activate?: boolean, key?: string}} options
 */
export async function seedOperationsFixture(pool, options = {}) {
  const {
    hours = 24, transactionsPerHour = (h) => 3 + (h % 5), processes = 12, recentProcesses = 4,
    decisions = 8, activate = true, key = `operations-fixture-${id()}`,
  } = options;
  const actor = 'operations-fixture';
  const sourceId = id(), replayId = id(), runId = id(), oldApiRunId = id(), oldCsvRunId = id();
  const featureSetId = id(), configId = id(), ruleWarning = id(), ruleCritical = id();
  const client = await pool.connect();
  let transactions = 0;
  try {
    await client.query('BEGIN');
    await client.query(`INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
      VALUES($1,$2,$3,'switchable-input','arrival-snapshot-v1','{"status":"unknown"}',
      '{"layers":["T","P","M","B"],"bodyValuesCollected":false}',clock_timestamp())`, [sourceId, key, key]);
    await client.query(`INSERT INTO data_input_replays(id,source_id,replay_key,status,record_count,layer_counts,manifest_hash)
      VALUES($1,$2,'synthetic-s1','ready',3000,'{"T":1000,"P":1000,"M":1000}',$3)`, [replayId, sourceId, sha(replayId)]);
    await client.query(`INSERT INTO ops_input_runs(id,source_id,mode,replay_id,settings,status,next_position,accepted_count,created_at)
      VALUES($1,$2,'api',NULL,'{"pollSeconds":2}','paused',0,120,clock_timestamp()-interval '3 hours'),
            ($3,$2,'csv',$4,'{"rate":5,"repeat":1,"replayId":"${replayId}"}','completed',3000,3000,clock_timestamp()-interval '2 hours'),
            ($5,$2,'csv',$4,'{"rate":10,"repeat":2,"replayId":"${replayId}"}','running',1248,1248,clock_timestamp()-interval '30 minutes')`,
    [oldApiRunId, sourceId, oldCsvRunId, replayId, runId]);
    await client.query(`INSERT INTO ops_input_controls(source_id,mode,revision,run_id,updated_by_actor_id,worker_heartbeat_at)
      VALUES($1,'csv',7,$2,$3,clock_timestamp())`, [sourceId, runId, actor]);

    const observation = async (layer, minutesAgo, values, missing = [], flags = [], execution = null, revision = null) => {
      const obs = id();
      await client.query(`INSERT INTO data_observations(id,source_id,layer,execution_id,revision_no,dedup_key,content_hash,
          api_received_at,persisted_at,clock_status,parsed_times,allowed_values,quality_flags,missing_fields)
        VALUES($1,$2,$3,$4,$5,$6,$7,clock_timestamp()-($8::double precision*interval '1 minute'),
          clock_timestamp()-($8::double precision*interval '1 minute'),'unknown','{}',$9::jsonb,$10::jsonb,$11::jsonb)`,
      [obs, sourceId, layer, execution, revision, `${layer}:${obs}`, sha(obs), minutesAgo, JSON.stringify(values),
        JSON.stringify(flags), JSON.stringify(missing)]);
      return obs;
    };
    // T: 최근 hours시간, 시간당 transactionsPerHour(h)건(현재 시간 포함). 이전 시간은 시간 경계 안쪽에 둔다.
    for (let hourAgo = 0; hourAgo < hours; hourAgo += 1) {
      for (let index = 0, count = transactionsPerHour(hourAgo); index < count; index += 1) {
        const minutes = hourAgo === 0 ? 0.1 + index * 0.001 : hourAgo * 60 - 30 + index * 0.01;
        await observation('T', minutes, { transaction_id: `TX-${hourAgo}-${index}`, interface_id: 'IF-01', retry_count: null },
          ['response_message'], index === 0 && hourAgo === 1 ? ['invalid_numeric:retry_count'] : []);
        transactions += 1;
      }
    }
    // P: 실행 식별 + revision. 최근 1분 recentProcesses건, 나머지는 10분 이전. 한 실행은 revision 2로 재도착.
    const processObservations = [];
    for (let index = 0; index < processes; index += 1) {
      const execution = id();
      await client.query(`INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at)
        VALUES($1,$2,'P',$3,'{}','v1',clock_timestamp())`, [execution, sourceId, `exec-${execution}`]);
      const minutes = index < recentProcesses ? 0.2 : 10 + index;
      const obs = await observation('P', minutes, { process_id: `P0${index % 3}`, status: index % 4 ? 'S' : 'F', total_count: index },
        index % 3 ? [] : ['retry_count'], [], execution, 1);
      await client.query(`INSERT INTO data_processes(observation_id,transaction_id,process_id,status,total_count)
        VALUES($1,$2,$3,'S',$4)`, [obs, `TX-P-${index}`, `P0${index % 3}`, index]);
      await client.query(`INSERT INTO data_input_origins(observation_id,run_id,mode,position,namespace) VALUES($1,$2,'csv',$3,'fixture')`,
        [obs, runId, index]);
      processObservations.push({ obs, execution });
      if (index === processes - 1) {
        const revised = await observation('P', 0.3, { process_id: 'P00', status: 'S', total_count: 99 }, [], [], execution, 2);
        await client.query(`INSERT INTO data_processes(observation_id,transaction_id,process_id,status,total_count)
          VALUES($1,$2,'P00','S',99)`, [revised, `TX-P-${index}`]);
      }
    }
    for (let index = 0; index < 5; index += 1) await observation('M', 1 + index, { message_id: `M-${index}`, data_size: 10 });
    await observation('B', 5, {});

    await client.query(`INSERT INTO ml_feature_sets(id,schema_version,transform_version,output_schema,content_hash,created_by_actor_id,created_at)
      VALUES($1,'v1','v1','{}',$2,$3,clock_timestamp())`, [featureSetId, sha(featureSetId), actor]);
    await client.query(`INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,created_by_actor_id,created_at)
      VALUES($1,$2,'{"detectionPolicy":{"mode":"parallel","deadlineMs":2000,"combination":"any_anomaly"}}','{}',$3,$4,clock_timestamp())`,
    [configId, sourceId, sha(configId), actor]);
    for (const [ruleId, severity, order] of [[ruleWarning, 'warning', 0], [ruleCritical, 'critical', 1]]) {
      await client.query(`INSERT INTO ml_rule_versions(id,rule_key,version,rule_spec,input_spec,evidence_spec,content_hash,created_by_actor_id,created_at)
        VALUES($1,$2,1,$3::jsonb,'{}','{}',$4,$5,clock_timestamp())`,
      [ruleId, `fixture_${ruleId.slice(0, 8)}`, JSON.stringify({ outcome: { decisionCode: 'FIXTURE_RULE', severity } }), sha(ruleId), actor]);
      await client.query('INSERT INTO ml_config_rules(config_version_id,rule_version_id,evaluation_order) VALUES($1,$2,$3)', [configId, ruleId, order]);
    }
    await client.query(`INSERT INTO ml_config_models(config_version_id,model_key,feature_set_id,adapter_spec,training_settings,threshold_spec)
      VALUES($1,'iforest',$2,'{"name":"sklearn-iforest","formatVersion":"1"}','{}','{}')`, [configId, featureSetId]);
    if (activate) {
      await client.query(`INSERT INTO ops_system_state(id,active_config_version_id,revision,detector_state,changed_at)
        VALUES(1,$1,1,'{}',clock_timestamp()) ON CONFLICT(id) DO UPDATE SET active_config_version_id=EXCLUDED.active_config_version_id,
        revision=ops_system_state.revision+1,changed_at=EXCLUDED.changed_at`, [configId]);
    }
    // 판정: 이상(anomalous true) 2건, 정상 나머지, 1건은 unavailable(정상 여부 미확인).
    const decided = processObservations.slice(0, decisions);
    for (const [index, { obs, execution }] of decided.entries()) {
      const anomalous = index < 2;
      const status = index === decided.length - 1 ? 'unavailable' : 'complete';
      await client.query(`INSERT INTO run_process_decisions(id,execution_id,process_observation_id,config_version_id,input_manifest_hash,
          status,rule_status,ml_status,decision_code,anomaly_score,evidence,decided_at,latency_status)
        VALUES($1,$2,$3,$4,$5,$6,'succeeded','succeeded',$7,$8,$9::jsonb,clock_timestamp()-make_interval(secs=>$10::double precision),'unavailable')`,
      [id(), execution, obs, configId, sha(obs), status, anomalous ? 'FIXTURE_RULE' : null, anomalous ? 0.9 : 0.1,
        JSON.stringify({ anomalous: status === 'unavailable' ? null : anomalous, requestedMode: 'parallel', actualPaths: ['rule', 'ml'] }),
        index * 5]);
      await client.query(`INSERT INTO ops_pipeline_measurements(id,observation_id,stage,config_version_id,duration_ms,measured_at)
        VALUES($1,$2,'detector',$3,$4,clock_timestamp()-make_interval(secs=>$5::double precision))`,
      [id(), obs, configId, 20 + index * 10, index * 5]);
    }
    await client.query(`INSERT INTO powercode_queue.detection_logs(id,payload,status,created_at)
      VALUES($1,$2::jsonb,'pending',clock_timestamp()-interval '3 seconds'),($3,$2::jsonb,'pending',clock_timestamp()-interval '1 second')`,
    [id(), JSON.stringify({ schemaVersion: 1, sourceId }), id()]);
    // 이전 CSV 재생 메시지: sourceId 없이 관측 ID만 참조(원천 범위는 관측으로 확인).
    await client.query(`INSERT INTO powercode_queue.detection_logs(id,payload,status,created_at) VALUES($1,$2::jsonb,'pending',clock_timestamp())`,
      [id(), JSON.stringify({ observationId: processObservations[processObservations.length - 1].obs })]);
    for (const [index, duration] of [40, 60, 120].entries()) {
      await client.query(`INSERT INTO ops_api_poll_samples(id,source_id,endpoint,started_at,finished_at,duration_ms,db_commit_ms,
          http_status,error_code,response_bytes,item_count,poll_interval_seconds)
        VALUES($1,$2,'transactions',clock_timestamp()-($3::double precision*interval '1 minute'),
          clock_timestamp()-($3::double precision*interval '1 minute'),$4,$5,200,$6,100,1,2)`,
      [id(), sourceId, 5 + index, duration, 4 + index, index === 2 ? 'timeout' : null]);
    }
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
  return { sourceId, configId, runId, replayId, transactions, processes, decisions, featureSetId };
}

/**
 * 판정 없는 detect 작업과 시도 1건을 쓰는 함수. config가 없으면 v04, 있으면 v03(그 구성의 원천과 같은 원천).
 * observation을 주면 이미 있는 PROCESS 관측을 입력으로 쓰고, 없으면 새 관측과 입력 출처를 만든다.
 */
const detectJobWriter = (client, now) => async (source, config, waitSeconds,
  { runId, mode, scope = 'approved-active', failed = false, minutesAgo = 10, finishedMinutesAgo = 0, observation = null }) => {
  const execution = observation?.execution ?? id(), obs = observation?.obs ?? id();
  const manifest = id(), job = id(), attempt = id(), token = id(), hash = sha(`manifest:${obs}`);
  const inputOrigin = { mode, runId, configurationScope: scope };
  if (!observation) {
    await client.query(`INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at)
      VALUES($1,$2,'P',$3,'{}','v1',clock_timestamp())`, [execution, source, `exec-${execution}`]);
    await client.query(`INSERT INTO data_observations(id,source_id,layer,execution_id,revision_no,dedup_key,content_hash,
        api_received_at,persisted_at,clock_status,parsed_times,allowed_values,quality_flags,missing_fields)
      VALUES($1,$2,'P',$3,1,$4,$5,$6::timestamptz-make_interval(mins=>$7),$6::timestamptz-make_interval(mins=>$7),'unknown','{}','{}','[]','[]')`,
    [obs, source, execution, `P:${obs}`, sha(obs), now, minutesAgo]);
    await client.query(`INSERT INTO data_processes(observation_id,process_id,status) VALUES($1,'P00','S')`, [obs]);
    await client.query(`INSERT INTO data_input_origins(observation_id,run_id,mode,position,namespace) VALUES($1,$2,$3,NULL,'fixture')`, [obs, runId, mode]);
  }
  await client.query(`INSERT INTO run_input_manifests(id,source_id,execution_id,process_observation_id,message_observation_refs,readiness,
      input_as_of,contract_version,content_hash,created_at)
    VALUES($1,$2,$3,$4,'[]','{}',$5::timestamptz-make_interval(mins=>$6),'v03',$7,clock_timestamp())`,
  [manifest, source, execution, obs, now, minutesAgo, hash]);
  await client.query(`INSERT INTO run_jobs(id,kind,status,request_hash,input_config_version_id,input_spec,max_attempts,attempt_count,
      next_run_at,created_at,contract_version,queue,producer_kind,producer_key,idempotency_key,input_manifest_id)
    VALUES($1,'detect','queued',$2,$3,$4::jsonb,3,0,clock_timestamp(),$5::timestamptz-make_interval(mins=>$6),$7,'detection','system',$8,$9,$10)`,
  [job, hash, config, JSON.stringify({ schemaVersion: 1, eventId: job, jobId: job, sourceId: source, observationId: obs, inputManifestHash: hash,
    inputManifestId: manifest, ...(config ? { configVersionId: config } : {}), inputOrigin }), now, minutesAgo, config ? 'v03' : 'v04', source, job, manifest]);
  await client.query(`INSERT INTO run_job_attempts(id,job_id,attempt_no,status,worker_id,lease_token,lease_until,started_at)
    VALUES($1,$2,1,'running','fixture-detector',$3,clock_timestamp()+interval '60 seconds',$4::timestamptz-make_interval(mins=>$5)+make_interval(secs=>$6))`,
  [attempt, job, token, now, minutesAgo, waitSeconds]);
  await client.query(`UPDATE run_jobs SET status='running',current_attempt_id=$2,attempt_count=1 WHERE id=$1`, [job, attempt]);
  if (failed) {
    await client.query(`UPDATE run_job_attempts SET status='failed',finished_at=clock_timestamp(),error_code='detection_processing_failed' WHERE id=$1`, [attempt]);
    await client.query(`UPDATE run_jobs SET status='failed',finished_at=$2::timestamptz-make_interval(mins=>$3),error_code='detection_processing_failed' WHERE id=$1`,
      [job, now, finishedMinutesAgo]);
  }
  return { job, obs, execution, manifest, hash, attempt, token, inputOrigin };
};

/**
 * 원천 판별 시험용 detect 작업. 시각은 한 시점 기준 고정 간격이라 대기 값이 정확하다(대기 = 첫 시도 시작 − 작업 생성).
 * 이 원천: v03·v04 작업 각 2건(대기 2·4·6·8초, 그중 2건 최근 실패) + 2시간 전 끝난 v04 실패. 다른 원천: v03·v04 실패 각 1건(대기 3·5초).
 * @param {import('pg').Pool} pool
 * @param {{sourceId: string, configId: string, runId: string}} fixture seedOperationsFixture 결과
 */
export async function seedDetectJobs(pool, { sourceId, configId, runId }) {
  const actor = 'operations-fixture', otherSource = id(), otherConfig = id(), otherRun = id();
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const { rows: [{ now }] } = await client.query('SELECT clock_timestamp() AS now');
    await client.query(`INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
      VALUES($1,$2,$2,'switchable-input','arrival-snapshot-v1','{"status":"unknown"}','{"layers":["P"]}',clock_timestamp())`, [otherSource, `operations-other-${otherSource}`]);
    await client.query(`INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,created_by_actor_id,created_at)
      VALUES($1,$2,'{}','{}',$3,$4,clock_timestamp())`, [otherConfig, otherSource, sha(otherConfig), actor]);
    await client.query(`INSERT INTO ops_input_runs(id,source_id,mode,replay_id,settings,status,next_position,accepted_count,created_at)
      VALUES($1,$2,'api',NULL,'{"pollSeconds":2}','paused',0,0,clock_timestamp())`, [otherRun, otherSource]);
    const detectJob = detectJobWriter(client, now);
    await detectJob(sourceId, configId, 2, { runId, mode: 'csv' });
    const running = await detectJob(sourceId, null, 6, { runId, mode: 'csv' });
    await detectJob(sourceId, configId, 4, { runId, mode: 'csv', failed: true });
    await detectJob(sourceId, null, 8, { runId, mode: 'csv', failed: true });
    await detectJob(sourceId, null, 1, { runId, mode: 'csv', failed: true, minutesAgo: 180, finishedMinutesAgo: 120 });
    await detectJob(otherSource, otherConfig, 3, { runId: otherRun, mode: 'api', scope: 'input-validation', failed: true });
    await detectJob(otherSource, null, 5, { runId: otherRun, mode: 'api', failed: true });
    // 구성이 빈 Detector 측정(016은 detector 단계만 허용).
    await client.query(`INSERT INTO ops_pipeline_measurements(id,observation_id,stage,config_version_id,duration_ms,measured_at)
      VALUES($1,$2,'detector',NULL,50,clock_timestamp())`, [id(), running.obs]);
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
  return { otherSource };
}

/**
 * v04 최종 판정(구성 없음) 3건. 이 원천: v03 정상 판정이 있는 관측의 상태 규칙 이상(ML 정상) 1건 + 새 관측의 정상 판정 1건.
 * 구성이 하나도 없는 원천(룰만 운영, live): 상태 규칙 이상 1건(ML 없음).
 * @param {import('pg').Pool} pool
 * @param {{sourceId: string, configId: string, runId: string}} fixture seedOperationsFixture 결과
 */
export async function seedV04Decisions(pool, { sourceId, configId, runId }) {
  const ruleOnlySource = id(), ruleOnlyRun = id();
  const client = await pool.connect();
  try {
    await client.query('BEGIN');
    const { rows: [{ now }] } = await client.query('SELECT clock_timestamp() AS now');
    await client.query(`INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
      VALUES($1,$2,$2,'switchable-input','arrival-snapshot-v1','{"status":"unknown"}','{"layers":["P"]}',clock_timestamp())`, [ruleOnlySource, `operations-rule-only-${ruleOnlySource}`]);
    await client.query(`INSERT INTO ops_input_runs(id,source_id,mode,replay_id,settings,status,next_position,accepted_count,created_at)
      VALUES($1,$2,'api',NULL,'{"pollSeconds":2}','paused',0,0,clock_timestamp())`, [ruleOnlyRun, ruleOnlySource]);
    const detectJob = detectJobWriter(client, now);
    const decide = async (written, sample, source) => {
      const { rows: [{ asOf }] } = await client.query('SELECT input_as_of AS "asOf" FROM run_input_manifests WHERE id=$1', [written.manifest]);
      const { columns, evidence } = v04Final(sample, { sourceId: source, observationId: written.obs, inputAsOf: asOf.toISOString(), manifestHash: written.hash,
        modelVersionId: id(), input: written.inputOrigin });
      await client.query(`INSERT INTO run_process_decisions(id,execution_id,process_observation_id,config_version_id,input_manifest_id,input_manifest_hash,input_as_of,
          job_id,result_attempt_id,result_lease_token,status,rule_status,ml_status,decision_code,anomaly_score,evidence,decided_at,latency_status)
        VALUES($1,$2,$3,NULL,$4,$5,(SELECT input_as_of FROM run_input_manifests WHERE id=$4),$6,$7,$8,$9,$10,$11,$12,$13,$14::jsonb,clock_timestamp(),'unavailable')`,
      [id(), written.execution, written.obs, written.manifest, written.hash, written.job, written.attempt, written.token, columns.status, columns.rule_status,
        columns.ml_status, columns.decision_code, columns.anomaly_score, JSON.stringify(evidence)]);
      await client.query(`INSERT INTO ops_pipeline_measurements(id,observation_id,stage,config_version_id,duration_ms) VALUES($1,$2,'detector',NULL,20.5)`, [id(), written.obs]);
      await client.query(`UPDATE run_job_attempts SET status='succeeded',finished_at=clock_timestamp() WHERE id=$1`, [written.attempt]);
      await client.query(`UPDATE run_jobs SET status='succeeded',finished_at=clock_timestamp() WHERE id=$1`, [written.job]);
    };
    const { rows: [normal] } = await client.query(`SELECT process_observation_id AS obs,execution_id AS execution FROM run_process_decisions
      WHERE config_version_id=$1 AND status='complete' AND evidence->'anomalous'='false'::jsonb ORDER BY decided_at DESC,id LIMIT 1`, [configId]);
    await decide(await detectJob(sourceId, null, 1, { runId, mode: 'csv', minutesAgo: 0, observation: normal }), 'anomalyComplete', sourceId);
    await decide(await detectJob(sourceId, null, 1, { runId, mode: 'csv' }), 'normalComplete', sourceId);
    await decide(await detectJob(ruleOnlySource, null, 1, { runId: ruleOnlyRun, mode: 'api' }), 'ruleOnlyAnomaly', ruleOnlySource);
    await client.query('COMMIT');
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    client.release();
  }
  return { ruleOnlySource };
}

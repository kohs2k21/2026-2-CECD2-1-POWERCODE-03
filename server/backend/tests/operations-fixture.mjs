// 운영 현황 읽기 API 시험용 로컬 고정 데이터(합성). 로컬 QA 데이터베이스에만 넣는다.
// 고객 원본·BODY 값은 없다. 시각은 DB clock_timestamp() 기준 상대값으로 넣는다.
import crypto from 'node:crypto';

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

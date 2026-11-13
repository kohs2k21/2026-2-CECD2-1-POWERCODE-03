// 룰 세트·수식 추천 시험용 합성 데이터. 로컬 QA 데이터베이스에만 넣는다. 고객 원본·BODY 값은 없다.
import crypto from 'node:crypto';

const id = () => crypto.randomUUID();
const sha = (value) => crypto.createHash('sha256').update(value).digest('hex');

export async function seedRuleSetSource(db, key = 'rule-set-fixture') {
  const sourceId = id();
  await db.query(`INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
    VALUES($1,$2,$3,'csv','v1','{}','{}',clock_timestamp())`, [sourceId, `${key}-${sourceId.slice(0, 8)}`, key]);
  return sourceId;
}

/** PROCESS 관측 1건. occurredAt = ESB 시각(source_log_occurred_at, null 허용), persistedAt 기본 현재. revision·execution으로 재도착을 만든다. */
export async function addProcess(db, sourceId, {
  occurredAt, persistedAt = null, status = 'S', start = null, end = null, counts = {}, execution = id(), revision = 1, label = null,
}) {
  const observation = id();
  if (execution) {
    await db.query(`INSERT INTO data_executions(id,source_id,layer,execution_key,key_parts,key_contract_version,first_observed_at)
      VALUES($1,$2,'P',$3,'{}','v1',clock_timestamp()) ON CONFLICT DO NOTHING`, [execution, sourceId, `exec-${execution}`]);
  }
  await db.query(`INSERT INTO data_observations(id,source_id,layer,execution_id,revision_no,dedup_key,content_hash,source_log_occurred_at,
      api_received_at,persisted_at,clock_status,parsed_times,allowed_values,quality_flags,missing_fields)
    VALUES($1,$2,'P',$3,$4,$5,$6,$7,clock_timestamp(),coalesce($8::timestamptz,clock_timestamp()),'unknown','{}','{}','[]','[]')`,
  [observation, sourceId, execution, revision, `P:${observation}`, sha(observation), occurredAt, persistedAt]);
  await db.query(`INSERT INTO data_processes(observation_id,status,start_time_raw,end_time_raw,total_count,success_count,error_count,retry_count)
    VALUES($1,$2,$3,$4,$5,$6,$7,$8)`, [observation, status, start, end, counts.total ?? null, counts.success ?? null, counts.error ?? null, counts.retry ?? null]);
  for (const [index, value] of (Array.isArray(label) ? label : label ? [label] : []).entries()) {
    await db.query(`INSERT INTO data_label_evidence(id,observation_id,revision_no,label,reason_code,created_by_actor_id)
      VALUES($1,$2,$3,$4,'QA_LABEL','fixture')`, [id(), observation, index + 1, value]);
  }
  return { observation, execution };
}

/** v1·v2 활성 구성: 모델 1개(있으면) + 그 구성을 만든 train 작업(input_spec.from). models=0이면 모델 없는 구성. */
export async function activateConfig(db, sourceId, { trainFrom = null, models = 1 } = {}) {
  const configId = id(), featureSet = id(), hash = sha(configId);
  await db.query(`INSERT INTO ml_feature_sets(id,schema_version,transform_version,output_schema,content_hash,created_by_actor_id,created_at)
    VALUES($1,1,'t1','{}',$2,'fixture',clock_timestamp())`, [featureSet, sha(featureSet)]);
  await db.query(`INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,created_by_actor_id,created_at)
    VALUES($1,$2,'{"schemaVersion":1}','{}',$3,'fixture',clock_timestamp())`, [configId, sourceId, hash]);
  if (models) {
    await db.query(`INSERT INTO ml_config_models(config_version_id,model_key,feature_set_id,adapter_spec,training_settings,threshold_spec)
      VALUES($1,'iforest',$2,'{}','{}','{}')`, [configId, featureSet]);
  }
  if (trainFrom !== undefined) {
    const spec = trainFrom ? { from: trainFrom, until: new Date().toISOString() } : {};
    await db.query(`INSERT INTO run_jobs(id,kind,status,requested_by_actor_id,requester_role,request_hash,input_spec,max_attempts,attempt_count,next_run_at,
        result_config_version_id,created_at,finished_at)
      VALUES($1,'train','succeeded','fixture','admin',$2,$3::jsonb,1,1,clock_timestamp(),$4,clock_timestamp(),clock_timestamp())`,
    [id(), sha(configId + 'job'), JSON.stringify(spec), configId]);
  }
  await db.query(`INSERT INTO ops_system_state(id,active_config_version_id,revision,detector_state,changed_at)
    VALUES(1,$1,1,'{}',clock_timestamp()) ON CONFLICT(id) DO UPDATE SET active_config_version_id=EXCLUDED.active_config_version_id,
      revision=ops_system_state.revision+1,changed_at=EXCLUDED.changed_at`, [configId]);
  return configId;
}
export const clearActiveConfig = (db) => db.query(`UPDATE ops_system_state SET active_config_version_id=NULL,revision=revision+1,changed_at=clock_timestamp() WHERE id=1`);

/** 활성 모델 집합에 새 리비전을 넣는다(models = [{configVersionId, modelKey}]). 저장 API 대신 시험 준비용으로만 쓴다. */
export async function pushActiveModels(db, models) {
  const { rows: [latest] } = await db.query('SELECT revision::int AS revision FROM ops_active_model_set_revisions ORDER BY revision DESC LIMIT 1');
  const revision = latest.revision + 1;
  await db.query(`INSERT INTO ops_active_model_set_revisions(revision,snapshot,changes,changed_by_actor_id) VALUES($1,$2::jsonb,'[]','fixture')`,
    [revision, JSON.stringify({ models: models.map((model) => ({ displayName: null, activatedAtRevision: revision, ...model })) })]);
  return revision;
}

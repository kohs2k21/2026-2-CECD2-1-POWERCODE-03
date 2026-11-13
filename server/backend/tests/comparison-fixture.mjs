// 비교 평가 시험·로컬 미리보기용 합성 데이터(로컬 QA 데이터베이스 전용). 고객 원본·BODY 값은 없다.
// Trainer가 하는 일(세트 고정, 비교 결과 기록)은 여기서 Trainer와 같은 표·같은 트랜잭션 순서로 흉내 낸다 — 시험 전용이며 Trainer 구현이 아니다.
import crypto from 'node:crypto';

const id = () => crypto.randomUUID();
const sha = (value) => crypto.createHash('sha256').update(String(value)).digest('hex');

export async function seedSource(db, key = 'comparison-fixture') {
  const sourceId = id();
  await db.query(`INSERT INTO ops_sources(id,dataset_key,display_name,ingestion_method,schema_version,clock_basis,field_catalog,created_at)
    VALUES($1,$2,$3,'csv','v1','{}','{}',clock_timestamp())`, [sourceId, `${key}-${sourceId.slice(0, 8)}`, key]);
  return sourceId;
}

export async function seedSnapshot(db, sourceId, { schemaVersion = 'process-v1', status = 'ready', from = '2026-10-01T00:00:00Z', until = '2026-10-08T00:00:00Z' } = {}) {
  const snapshot = id();
  await db.query(`INSERT INTO data_snapshots(id,source_id,status,observation_cutoff_at,selection_spec,split_spec,schema_version,created_by_actor_id,created_at,ready_at)
    VALUES($1,$2,$3::text::snapshot_status,clock_timestamp(),$4::jsonb,'{}',$5,'fixture',clock_timestamp(),CASE WHEN $3::text='ready' THEN clock_timestamp() END)`,
  [snapshot, sourceId, status, JSON.stringify({ from, until }), schemaVersion]);
  return snapshot;
}

/** 모델 버전 1개: 학습 스냅샷 + 산출물(model·fit) + 학습 작업 + 구성 모델 행. modelKey null이면 모델 없는 구성. */
export async function seedVersion(db, sourceId, { modelKey = 'iforest', schemaVersion = 2, name = null, snapshot = null } = {}) {
  const configId = id(), featureSet = id(), job = id(), model = id(), fit = id();
  const snapshotId = snapshot ?? await seedSnapshot(db, sourceId);
  await db.query(`INSERT INTO ml_feature_sets(id,schema_version,transform_version,output_schema,content_hash,created_by_actor_id,created_at)
    VALUES($1,1,'t1','{}',$2,'fixture',clock_timestamp())`, [featureSet, sha(featureSet)]);
  await db.query(`INSERT INTO ml_config_versions(id,source_id,configuration_spec,explanation_spec,content_hash,created_by_actor_id,created_at)
    VALUES($1,$2,$3::jsonb,'{}',$4,'fixture',clock_timestamp())`, [configId, sourceId, JSON.stringify({ schemaVersion, displayName: name }), sha(configId)]);
  await db.query(`INSERT INTO run_jobs(id,kind,status,requested_by_actor_id,requester_role,request_hash,input_spec,max_attempts,attempt_count,next_run_at,result_config_version_id,created_at,finished_at)
    VALUES($1,'train','succeeded','fixture','admin',$2,'{}',1,1,clock_timestamp(),$3,clock_timestamp(),clock_timestamp())`, [job, sha(job), configId]);
  if (modelKey) {
    await db.query(`INSERT INTO ml_artifacts(id,kind,relative_path,content_hash,byte_size,snapshot_id,feature_set_id,created_job_id,format_version,runtime_version,metadata,created_at)
      VALUES($1,'fit',$2,$3,1,$4,$5,$6,'v1','t','{}',clock_timestamp())`, [fit, `fixture/${fit}`, sha(fit), snapshotId, featureSet, job]);
    await db.query(`INSERT INTO ml_artifacts(id,kind,relative_path,content_hash,byte_size,snapshot_id,feature_set_id,fit_artifact_id,created_job_id,format_version,runtime_version,metadata,created_at)
      VALUES($1,'model',$2,$3,1,$4,$5,$6,$7,'v1','t','{}',clock_timestamp())`, [model, `fixture/${model}`, sha(model), snapshotId, featureSet, fit, job]);
    await db.query(`INSERT INTO ml_config_models(config_version_id,model_key,feature_set_id,model_artifact_id,fit_artifact_id,adapter_spec,training_settings,threshold_spec)
      VALUES($1,$2,$3,$4,$5,'{}','{}','{}')`, [configId, modelKey, featureSet, model, fit]);
  }
  return { configId, snapshotId };
}

/** 활성 모델 집합 새 리비전(models = [{configVersionId, modelKey}]). */
export async function pushActiveModels(db, models) {
  const { rows: [latest] } = await db.query('SELECT revision::int AS revision FROM ops_active_model_set_revisions ORDER BY revision DESC LIMIT 1');
  const revision = latest.revision + 1;
  await db.query(`INSERT INTO ops_active_model_set_revisions(revision,snapshot,changes,changed_by_actor_id) VALUES($1,$2::jsonb,'[]','fixture')`,
    [revision, JSON.stringify({ models: models.map((model) => ({ displayName: null, activatedAtRevision: revision, ...model })) })]);
  return revision;
}

/** 트랜잭션은 연결 하나에서(Pool이면 연결을 빌린다). */
async function transaction(db, work) {
  // pg.Pool만 연결을 빌린다(Client·PoolClient는 그대로 쓴다).
  const client = 'totalCount' in db ? await db.connect() : db;
  try {
    await client.query('BEGIN');
    const value = await work(client);
    await client.query('COMMIT');
    return value;
  } catch (error) {
    await client.query('ROLLBACK');
    throw error;
  } finally {
    if (client !== db) client.release();
  }
}

/** v03 작업을 실행 중으로 만든다(Trainer claim 흉내): 시도 1개 + lease. */
async function claim(db, jobId) {
  const attempt = id();
  await db.query(`INSERT INTO run_job_attempts(id,job_id,attempt_no,status,worker_id,lease_token,lease_until,started_at)
    VALUES($1,$2,1,'running','fixture-trainer',$3,clock_timestamp()+interval '5 minutes',clock_timestamp())`, [attempt, jobId, id()]);
  await db.query(`UPDATE run_jobs SET status='running',current_attempt_id=$2,attempt_count=1 WHERE id=$1`, [jobId, attempt]);
  return attempt;
}

/** 평가 세트 고정(Trainer evaluation_set 처리 흉내): 세트 ready + 작업 성공. 합성 없는 세트라 syntheticRowCount 0. */
export async function readySet(db, setId, { rowCount = 1200, truthCount = 20 } = {}) {
  const { rows: [set] } = await db.query('SELECT job_id::text AS "jobId" FROM data_evaluation_sets WHERE id=$1', [setId]);
  return transaction(db, async (tx) => {
    const attempt = await claim(tx, set.jobId);
    const contentHash = sha(`set:${setId}`);
    await tx.query(`UPDATE data_evaluation_sets SET status='ready',generation=$2::jsonb,content_hash=$3,ready_at=clock_timestamp() WHERE id=$1`,
      [setId, JSON.stringify({ rowCount, originalRowCount: rowCount, syntheticRowCount: 0, truthCount, memberHash: sha(`members:${setId}`) }), contentHash]);
    await tx.query(`UPDATE run_job_attempts SET status='succeeded',finished_at=clock_timestamp() WHERE id=$1`, [attempt]);
    await tx.query(`UPDATE run_jobs SET status='succeeded',finished_at=clock_timestamp(),result_summary=$2::jsonb WHERE id=$1`,
      [set.jobId, JSON.stringify({ evaluationSetId: setId, evaluationSetContractVersion: 'evaluation-set-v1', status: 'ready', contentHash, synthetic: null })]);
    return contentHash;
  });
}

/** 평가 세트 실패(Trainer `evaluation_set_empty` 흉내): 작업 실패 → 011 트리거가 세트를 failed로. */
export async function failSet(db, setId, code = 'evaluation_set_empty') {
  const { rows: [set] } = await db.query('SELECT job_id::text AS "jobId" FROM data_evaluation_sets WHERE id=$1', [setId]);
  await db.query(`UPDATE run_jobs SET status='failed',error_code=$2,finished_at=clock_timestamp() WHERE id=$1`, [set.jobId, code]);
}

// Trainer `_ratio`(comparison.py)와 같은 모양: {value, numerator, denominator, reason}.
const rate = (numerator, denominator) => (denominator ? { value: numerator / denominator, numerator, denominator, reason: null }
  : { value: null, numerator: null, denominator: null, reason: 'verified_normal_labels_unavailable' });

/**
 * 비교 결과 기록(Trainer compare 처리 흉내, 011 주석의 순서): 비교 묶음 → 항목별 run_evaluations → 항목 → 작업 성공(resultSummary = summary).
 * results: { [configVersionId]: { tp, fn, fp?, tn?, error? } } — error가 있으면 그 항목은 실패.
 */
export async function completeComparison(db, jobId, results) {
  const { rows: [job] } = await db.query('SELECT input_spec AS input FROM run_jobs WHERE id=$1', [jobId]);
  const input = job.input;
  const { rows: [set] } = await db.query(`SELECT snapshot_id::text AS "snapshotId",purpose::text AS purpose,content_hash AS "contentHash",generation FROM data_evaluation_sets WHERE id=$1`, [input.evaluationSetId]);
  const keys = new Map((await db.query(`SELECT config_version_id::text AS id,model_key AS "modelKey" FROM ml_config_models WHERE config_version_id=ANY($1::uuid[])`, [input.configVersionIds])).rows.map((row) => [row.id, row.modelKey]));
  const comparisonId = id();
  const baseline = input.baselines[0]?.configVersionId ?? null;
  const items = input.configVersionIds.map((configVersionId) => {
    const result = results[configVersionId] ?? { error: 'evaluation_failed:missing_fixture' };
    const role = configVersionId === baseline ? 'active' : 'candidate';
    if (result.error) return { configVersionId, modelKey: keys.get(configVersionId), role, status: 'failed', errorCode: result.error, evaluationId: null };
    const { tp, fn, fp = null, tn = null } = result;
    const normal = fp !== null && tn !== null;
    return {
      configVersionId, modelKey: keys.get(configVersionId), role, status: 'succeeded', errorCode: null, evaluationId: id(), metricVersion: 'selected-paths-v3',
      evaluationPolicy: { source: 'configuration', detectionPolicy: { mode: 'ml_only', deadlineMs: 30000, combination: 'selected_path', legacy: false }, ruleCount: 0 },
      applicability: 'valid', reasonCodes: [], counts: { populationCount: set.generation.rowCount, scoredCount: set.generation.rowCount, unavailableCount: 0, unlabeledCount: set.generation.rowCount - tp - fn },
      categoryResults: { ml: { measurementStatus: 'measured', methodCount: 1, confusion: { tp, fp, tn, fn }, unavailableCount: 0,
        completeOnly: { recall: rate(tp, tp + fn), precision: normal ? rate(tp, tp + fp) : rate(0, 0), falsePositiveRate: normal ? rate(fp, fp + tn) : rate(0, 0) } } },
    };
  });
  const allFailed = items.every((item) => item.status === 'failed');
  const summary = {
    serviceContractVersion: 'trainer-service-v1', comparisonContractVersion: 'config-comparison-v1', comparisonId,
    evaluationSet: { id: input.evaluationSetId, contentHash: set.contentHash, purpose: set.purpose, rowCount: set.generation.rowCount,
      originalRowCount: set.generation.originalRowCount, syntheticRowCount: set.generation.syntheticRowCount, synthetic: false },
    baselines: input.baselines, activeModelSetRevision: input.activeModelSetRevision,
    ruleSet: { revision: input.ruleSetRevision, contentHash: input.ruleSetContentHash, fixedAt: 'accepted' },
    detectionPolicy: { revision: input.detectionPolicyRevision, policy: input.detectionPolicy, fixedAt: 'accepted' },
    items, itemCount: items.length, succeededCount: items.filter((item) => item.status === 'succeeded').length, failedCount: items.filter((item) => item.status === 'failed').length,
    replacementRecommendations: null, replacementRecommendationsReason: 'not_implemented_v1',
  };
  await transaction(db, async (tx) => {
    const attempt = await claim(tx, jobId);
    await tx.query(`INSERT INTO run_config_comparisons(id,job_id,evaluation_set_id,source_id,baseline_config_version_id,active_revision,status,summary,completed_at)
      VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,clock_timestamp())`,
    [comparisonId, jobId, input.evaluationSetId, input.sourceId, baseline, input.activeModelSetRevision, allFailed ? 'failed' : 'succeeded', JSON.stringify(summary)]);
    for (const item of items.filter((entry) => entry.status === 'succeeded')) {
      await tx.query(`INSERT INTO run_evaluations(id,job_id,config_version_id,snapshot_id,purpose,evaluation_spec_version,evaluation_spec_hash,evaluation_spec,applicability,
          population_count,scored_count,unavailable_count,metrics,reason_codes,completed_at,comparison_id,evaluation_set_id)
        VALUES($1,$2,$3,$4,$5,'v2',$6,'{}','valid',$7,$7,0,$8::jsonb,'[]',clock_timestamp(),$9,$10)`,
      [item.evaluationId, jobId, item.configVersionId, set.snapshotId, set.purpose, sha(`spec:${jobId}`), set.generation.rowCount,
        JSON.stringify({ ml: item.categoryResults.ml }), comparisonId, input.evaluationSetId]);
    }
    // config_not_found 항목은 FK 때문에 항목 표에 없고 summary.items에만 있다(Trainer 11/13).
    for (const item of items.filter((entry) => entry.errorCode !== 'config_not_found')) {
      await tx.query(`INSERT INTO run_config_comparison_items(comparison_id,config_version_id,role,status,error_code,evaluation_id) VALUES($1,$2,$3,$4,$5,$6)`,
        [comparisonId, item.configVersionId, item.role, item.status, item.errorCode, item.evaluationId]);
    }
    await tx.query(`UPDATE run_job_attempts SET status=$2,finished_at=clock_timestamp() WHERE id=$1`, [attempt, allFailed ? 'failed' : 'succeeded']);
    await tx.query(`UPDATE run_jobs SET status=$2,error_code=$3,finished_at=clock_timestamp(),result_summary=$4::jsonb WHERE id=$1`,
      [jobId, allFailed ? 'failed' : 'succeeded', allFailed ? 'comparison_all_items_failed' : null, JSON.stringify(summary)]);
  });
  return { comparisonId, items };
}

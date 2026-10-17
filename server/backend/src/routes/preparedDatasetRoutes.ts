import crypto from 'node:crypto';
import { mkdir, realpath, writeFile, rename, unlink, rmdir } from 'node:fs/promises';
import path from 'node:path';
import express, { Request, Response, NextFunction } from 'express';
import { getDatabasePool } from '../config/database.js';
import { authenticateToken, requireAdmin, AuthenticatedRequest } from '../middleware/auth.js';
import { parsePreparedUpload, PreparedRequestError, PREPARED_UUID } from './preparedCsvContract.js';

const router = express.Router();
router.use(authenticateToken as any, requireAdmin as any);
router.use(express.json({ limit: '32mb', strict: true }));
const route = (handler: (req: AuthenticatedRequest, res: Response) => Promise<void>) =>
  (req: Request, res: Response, next: NextFunction) => { void handler(req as AuthenticatedRequest, res).catch(next); };

router.post('/', route(async (req, res) => {
  const input = parsePreparedUpload(req.body);
  const header = req.headers['idempotency-key'];
  if (header !== undefined && (typeof header !== 'string' || !/^[a-zA-Z0-9_.:-]{1,128}$/.test(header))) throw new PreparedRequestError('invalid_idempotency_key');
  const key = header ?? crypto.randomUUID();
  const client = await getDatabasePool().connect();
  let directory: string | undefined;
  let commitAttempted = false;
  try {
    await client.query('BEGIN');
    await client.query('SELECT pg_advisory_xact_lock(81735,hashtext($1))', [JSON.stringify([req.user!.id,key])]);
    const previous = await client.query(`SELECT d.id,d.created_job_id AS "jobId",d.status,j.request_hash
      FROM run_jobs j JOIN data_prepared_datasets d ON d.created_job_id=j.id
      WHERE j.requested_by_actor_id=$1 AND j.kind::text='import_csv' AND j.idempotency_key=$2`, [req.user!.id,key]);
    if (previous.rowCount) {
      const row = previous.rows[0];
      if (row.request_hash !== input.requestHash) throw new PreparedRequestError('idempotency_payload_conflict',409);
      commitAttempted = true;
      await client.query('COMMIT');
      res.json({id:row.id,jobId:row.jobId,status:row.status});
      return;
    }
    const source = await client.query('SELECT schema_version FROM ops_sources WHERE id=$1',[input.manifest.sourceId]);
    if (!source.rowCount) throw new PreparedRequestError('source_not_found',404);
    if (source.rows[0].schema_version !== input.manifest.sourceSchemaVersion) throw new PreparedRequestError('prepared_source_schema_mismatch',409);
    if (!process.env.ARTIFACT_ROOT) throw new PreparedRequestError('prepared_storage_not_configured',503);
    const root = await realpath(process.env.ARTIFACT_ROOT);
    const base = path.join(root,'prepared');
    await mkdir(base,{recursive:true});
    if (await realpath(base) !== base) throw new PreparedRequestError('prepared_invalid_storage_path',503);
    const id = crypto.randomUUID(), jobId = crypto.randomUUID();
    directory = path.join(base,id);
    await mkdir(directory);
    await writeFile(path.join(directory,'original.tmp'),input.bytes,{flag:'wx',mode:0o600});
    await rename(path.join(directory,'original.tmp'),path.join(directory,'original.csv'));
    const spec = {kind:'import_csv',sourceId:input.manifest.sourceId,preparedDatasetId:id};
    await client.query(`INSERT INTO run_jobs(id,kind,status,requested_by_actor_id,requester_role,idempotency_key,
      request_hash,input_spec,max_attempts,attempt_count,next_run_at,created_at,contract_version,queue,producer_kind,producer_key)
      VALUES($1,'import_csv','queued',$2,'admin',$3,$4,$5::jsonb,3,0,clock_timestamp(),clock_timestamp(),'v03','training','user',$2)`,
      [jobId,req.user!.id,key,input.requestHash,JSON.stringify(spec)]);
    await client.query(`INSERT INTO data_prepared_datasets(id,source_id,manifest,manifest_hash,original_relative_path,
      original_hash,original_byte_size,created_job_id,requested_by_actor_id)
      VALUES($1,$2,$3::jsonb,$4,$5,$6,$7,$8,$9)`,
      [id,input.manifest.sourceId,JSON.stringify(input.manifest),input.manifestHash,`prepared/${id}/original.csv`,input.originalHash,input.bytes.length,jobId,req.user!.id]);
    await client.query(`INSERT INTO powercode_queue.training_jobs(id,payload) VALUES($1,$2::jsonb)`,
      [crypto.randomUUID(),JSON.stringify({schemaVersion:1,jobId,enqueuedAt:new Date().toISOString()})]);
    // An uncertain COMMIT may already have made the file reachable by a worker.
    commitAttempted = true;
    await client.query('COMMIT');
    res.status(201).json({id,jobId,status:'queued'});
  } catch (error) {
    await client.query('ROLLBACK').catch(() => {});
    if (directory && !commitAttempted) {
      await unlink(path.join(directory,'original.tmp')).catch(() => {});
      await unlink(path.join(directory,'original.csv')).catch(() => {});
      await rmdir(directory).catch(() => {});
    }
    throw error;
  } finally { client.release(); }
}));

router.get('/:id', route(async (req,res) => {
  if (!PREPARED_UUID.test(req.params.id)) throw new PreparedRequestError('prepared_invalid_dataset_id');
  const result = await getDatabasePool().query(`SELECT d.id,d.source_id AS "sourceId",d.status,d.manifest,
    d.manifest_hash AS "manifestHash",d.original_hash AS "originalHash",d.original_byte_size AS "originalByteSize",
    d.normalized_hash AS "normalizedHash",d.row_count AS "rowCount",d.validation_result AS "validationResult",
    d.snapshot_id AS "snapshotId",d.created_job_id AS "jobId",d.created_at AS "createdAt",d.ready_at AS "readyAt",
    j.status AS "jobStatus",j.error_code AS "errorCode" FROM data_prepared_datasets d
    JOIN run_jobs j ON j.id=d.created_job_id WHERE d.id=$1`, [req.params.id]);
  if (!result.rowCount) throw new PreparedRequestError('prepared_dataset_not_found',404);
  res.json(result.rows[0]);
}));
router.use((error: any,_req: Request,res: Response,_next: NextFunction) => {
  if (error instanceof PreparedRequestError) { res.status(error.status).json({code:error.code}); return; }
  if (error?.type === 'entity.too.large') { res.status(413).json({code:'prepared_request_too_large'}); return; }
  if (error?.type === 'entity.parse.failed') { res.status(400).json({code:'prepared_invalid_json'}); return; }
  res.status(503).json({code:'prepared_storage_unavailable'});
});
export default router;

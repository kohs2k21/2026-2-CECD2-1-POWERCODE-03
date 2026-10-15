import {Router,Request,Response,NextFunction} from 'express';
import crypto from 'node:crypto';
import {getDatabasePool} from '../config/database.js';
import {authenticateToken,requireAdmin,AuthenticatedRequest} from '../middleware/auth.js';
const router=Router();
router.use(authenticateToken as any,requireAdmin as any);
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const uuid=(v:unknown):v is string=>typeof v==='string' && UUID.test(v);
class InputError extends Error {constructor(readonly code:string,readonly status=400){super(code);}}
const route=(fn:(req:AuthenticatedRequest,res:Response)=>Promise<void>)=>(req:Request,res:Response,next:NextFunction)=>{void fn(req as AuthenticatedRequest,res).catch(next);};
router.get('/',route(async(req,res)=>{
 const sid=req.query.sourceId;
 if(!uuid(sid))throw new InputError('invalid_source_id');
 const pool=getDatabasePool();
 const state=await pool.query(`SELECT c.mode,c.revision::float8 AS revision,c.run_id AS "runId",
   c.config_version_id AS "configVersionId",c.updated_at AS "updatedAt",c.worker_heartbeat_at AS "workerHeartbeatAt",
   r.status,r.settings,r.next_position::float8 AS position,r.accepted_count::float8 AS accepted,
   r.duplicate_count::float8 AS duplicates,r.unidentified_count::float8 AS unidentified,r.last_error AS "lastError"
   FROM ops_input_controls c LEFT JOIN ops_input_runs r ON r.id=c.run_id WHERE c.source_id=$1`,[sid]);
 const replays=await pool.query(`SELECT id,replay_key AS "replayKey",record_count::float8 AS "recordCount",layer_counts AS "layerCounts"
   FROM data_input_replays WHERE source_id=$1 AND status='ready' ORDER BY created_at DESC LIMIT 50`,[sid]);
 const configs=await pool.query(`SELECT c.id,c.configuration_spec->'detectionPolicy'->>'mode' AS mode,c.created_at AS "createdAt"
   FROM ml_config_versions c WHERE source_id=$1 ORDER BY created_at DESC LIMIT 50`,[sid]);
 const counts=await pool.query('SELECT layer::text,count(*)::integer AS count FROM data_observations WHERE source_id=$1 GROUP BY layer',[sid]);
 const resume=await pool.query("SELECT settings,next_position::float8 AS position,status FROM ops_input_runs WHERE source_id=$1 AND mode='csv' AND status<>'failed' ORDER BY created_at DESC LIMIT 1",[sid]);
 res.json({storedCounts:Object.fromEntries(counts.rows.map(r=>[r.layer,r.count])),csvResume:resume.rows[0]??null,enabled:state.rowCount===1,sourceId:sid,control:state.rows[0]??null,replays:replays.rows,configurations:configs.rows});
}));
router.post('/:sourceId/mode',route(async(req,res)=>{
 const sid=req.params.sourceId,b=req.body;
 if(!uuid(sid)||!b||typeof b!=='object'||Array.isArray(b)||
   Object.keys(b).some(k=>!['mode','expectedRevision','replayId','rate','repeat','pollSeconds','restart','configVersionId'].includes(k))||
   !['api','csv','paused'].includes(b.mode)||!Number.isSafeInteger(b.expectedRevision)||b.expectedRevision<0||
   (b.restart!==undefined && typeof b.restart!=='boolean')||
   (b.configVersionId!==undefined && b.configVersionId!==null && !uuid(b.configVersionId)))throw new InputError('invalid_input_request');
 const settings=b.mode==='api'?{pollSeconds:b.pollSeconds??2}:b.mode==='csv'?{replayId:b.replayId,rate:b.rate??10,repeat:b.repeat??1}:{};
 if(b.mode==='api' && (typeof settings.pollSeconds!=='number'||!Number.isFinite(settings.pollSeconds)||settings.pollSeconds<.1||settings.pollSeconds>3600))throw new InputError('invalid_api_poll_seconds');
 if(b.mode==='csv' && (!uuid(settings.replayId)||typeof settings.rate!=='number'||!Number.isFinite(settings.rate)||settings.rate<.1||settings.rate>10000||!Number.isInteger(settings.repeat)||settings.repeat!<1||settings.repeat!>100))throw new InputError('invalid_csv_settings');
 const client=await getDatabasePool().connect();
 try{
  await client.query('BEGIN');
  const {rows:[state]}=await client.query('SELECT revision,run_id,config_version_id FROM ops_input_controls WHERE source_id=$1 FOR UPDATE',[sid]);
  if(!state)throw new InputError('input_source_not_initialized',404);
  if(Number(state.revision)!==b.expectedRevision)throw new InputError('input_revision_conflict',409);
  const config=b.configVersionId===undefined?state.config_version_id:b.configVersionId;
  if(config && !(await client.query('SELECT 1 FROM ml_config_versions WHERE id=$1 AND source_id=$2',[config,sid])).rowCount)throw new InputError('input_config_source_mismatch',409);
  if(b.mode==='csv' && !(await client.query("SELECT 1 FROM data_input_replays WHERE id=$1 AND source_id=$2 AND status='ready'",[settings.replayId,sid])).rowCount)throw new InputError('csv_replay_not_ready',409);
  if(state.run_id)await client.query("UPDATE ops_input_runs SET status='paused' WHERE id=$1 AND status='running'",[state.run_id]);
  let run=state.run_id;
  if(b.mode!=='paused'){
   const replay=b.mode==='csv'?settings.replayId:null;
   const old=b.restart?undefined:(await client.query(`SELECT id,(settings=$4::jsonb) AS same FROM ops_input_runs
     WHERE source_id=$1 AND mode=$2 AND replay_id IS NOT DISTINCT FROM $3 AND status<>'failed'
     ORDER BY created_at DESC LIMIT 1`,[sid,b.mode,replay,JSON.stringify(settings)])).rows[0];
   if(old){
    if(!old.same)throw new InputError('input_settings_changed_restart_required',409);
    run=old.id;
    await client.query("UPDATE ops_input_runs SET status=CASE WHEN status='completed' THEN status ELSE 'running' END,last_error=NULL WHERE id=$1",[run]);
   }else{
    run=crypto.randomUUID();
    await client.query("INSERT INTO ops_input_runs(id,source_id,mode,replay_id,settings,status) VALUES($1,$2,$3,$4,$5::jsonb,'running')",[run,sid,b.mode,replay,JSON.stringify(settings)]);
   }
  }
  await client.query(`UPDATE ops_input_controls SET mode=$1,run_id=$2,config_version_id=$3,revision=revision+1,
    updated_by_actor_id=$4,updated_at=clock_timestamp() WHERE source_id=$5`,[b.mode,run,config,req.user!.id,sid]);
  await client.query('COMMIT');
  res.json({sourceId:sid,mode:b.mode,runId:run,revision:b.expectedRevision+1,configVersionId:config});
 }catch(error){await client.query('ROLLBACK');throw error;}finally{client.release();}
}));
router.use((error:unknown,_req:Request,res:Response,next:NextFunction)=>{
 if(error instanceof InputError){res.status(error.status).json({code:error.code});return;}next(error);
});
export default router;

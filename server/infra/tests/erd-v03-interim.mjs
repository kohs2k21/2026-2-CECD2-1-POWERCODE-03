// 012 (#78 B) interim decision records, called from erd-v03.mjs inside the same disposable cluster.
// Assertions run as SQL (schema t helpers); each passed assertion raises NOTICE 'PASS …', a failed one raises 'FAIL …'.
// Attacks use real LOGIN roles on new connections (not SET ROLE); every shadowing attack creates its fake
// temp objects before any statement in that session runs a 012 function (a warmed plan cache would hide the hole).
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';

const helpersSql = `
CREATE SCHEMA t;
CREATE FUNCTION t.ins(tbl text, vals jsonb) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE c record; v jsonb := vals; r jsonb; enum_first text; cols text;
BEGIN
 FOR c IN SELECT column_name, udt_name, is_nullable, column_default, is_identity FROM information_schema.columns
          WHERE table_schema='public' AND table_name=tbl ORDER BY ordinal_position LOOP
  IF v ? c.column_name OR c.is_nullable='YES' OR c.column_default IS NOT NULL OR c.is_identity='YES' THEN CONTINUE; END IF;
  enum_first := NULL;
  SELECT e.enumlabel INTO enum_first FROM pg_enum e JOIN pg_type ty ON ty.oid=e.enumtypid WHERE ty.typname=c.udt_name ORDER BY e.enumsortorder LIMIT 1;
  v := v || jsonb_build_object(c.column_name, CASE
    WHEN enum_first IS NOT NULL THEN to_jsonb(enum_first)
    WHEN c.udt_name='uuid' THEN to_jsonb(gen_random_uuid())
    WHEN c.udt_name='jsonb' THEN '{}'::jsonb
    WHEN c.udt_name='bool' THEN 'false'::jsonb
    WHEN c.udt_name IN ('int2','int4','int8','numeric') THEN '1'::jsonb
    WHEN c.udt_name='timestamptz' THEN to_jsonb(clock_timestamp())
    ELSE to_jsonb('test-'||c.column_name||'-'||gen_random_uuid()) END);
 END LOOP;
 SELECT string_agg(quote_ident(k), ',') INTO cols FROM jsonb_object_keys(v) k;
 EXECUTE format('INSERT INTO %I (%s) SELECT %s FROM jsonb_populate_record(NULL::%I, $1) RETURNING to_jsonb(%I.*)', tbl, cols, cols, tbl, tbl)
  USING v INTO r;
 RETURN r;
END $$;
-- New v03 detect job with its own source/observation/config/manifest.
CREATE FUNCTION t.mkjob() RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE s jsonb; e jsonb; o jsonb; cfg jsonb; m jsonb; j jsonb;
BEGIN
 s := t.ins('ops_sources', '{}');
 e := t.ins('data_executions', jsonb_build_object('source_id', s->'id', 'layer', 'P'));
 o := t.ins('data_observations', jsonb_build_object('source_id', s->'id', 'layer', 'P', 'execution_id', e->'id', 'revision_no', 1,
        'persisted_at', clock_timestamp() - interval '1 hour', 'api_received_at', clock_timestamp() - interval '1 hour'));
 PERFORM t.ins('data_processes', jsonb_build_object('observation_id', o->'id'));
 cfg := t.ins('ml_config_versions', jsonb_build_object('source_id', s->'id'));
 m := t.ins('run_input_manifests', jsonb_build_object('source_id', s->'id', 'execution_id', e->'id', 'process_observation_id', o->'id',
        'message_observation_refs', '[]'::jsonb, 'readiness', '{}'::jsonb, 'input_as_of', clock_timestamp(), 'contract_version', 'v03'));
 j := t.ins('run_jobs', jsonb_build_object('kind','detect','status','queued','contract_version','v03','queue','detection',
        'producer_kind','system','producer_key','collector-test','idempotency_key', gen_random_uuid()::text,
        'input_manifest_id', m->'id', 'input_config_version_id', cfg->'id', 'max_attempts', 3, 'attempt_count', 0));
 RETURN jsonb_build_object('job', j->>'id', 'manifest', m->>'id', 'hash', m->>'content_hash', 'as_of', m->>'input_as_of',
        'obs', o->>'id', 'exec', e->>'id', 'config', cfg->>'id', 'source', s->>'id');
END $$;
-- Claim simulation (_claim_job): new running attempt n with a 60 s lease.
CREATE FUNCTION t.attempt(job uuid, n int) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE a jsonb;
BEGIN
 a := t.ins('run_job_attempts', jsonb_build_object('job_id', job, 'attempt_no', n, 'status', 'running', 'worker_id', 'detector-test',
        'lease_token', gen_random_uuid(), 'lease_until', clock_timestamp() + interval '60 seconds', 'started_at', clock_timestamp()));
 UPDATE run_jobs SET status='running', current_attempt_id=(a->>'id')::uuid, attempt_count=n WHERE id=job;
 RETURN jsonb_build_object('id', a->>'id', 'token', a->>'lease_token');
END $$;
CREATE FUNCTION t.interim(job uuid, att jsonb, kind text, token uuid DEFAULT NULL) RETURNS jsonb LANGUAGE plpgsql AS $$
BEGIN
 RETURN t.ins('run_decision_interims', jsonb_build_object('id', gen_random_uuid(), 'job_id', job, 'attempt_id', att->>'id', 'seq', NULL,
        'kind', kind, 'lease_token', COALESCE(token::text, att->>'token'), 'evidence_version', 'evidence-v4-draft',
        'payload', jsonb_build_object('schemaVersion', 4, 'composite', jsonb_build_object('anomalous', true, 'status', 'partial'))));
END $$;
CREATE FUNCTION t.decision(info jsonb, att jsonb) RETURNS jsonb LANGUAGE plpgsql AS $$
BEGIN
 RETURN t.ins('run_process_decisions', jsonb_build_object('id', gen_random_uuid(), 'execution_id', info->'exec', 'process_observation_id', info->'obs',
        'config_version_id', info->'config', 'input_manifest_id', info->'manifest', 'input_manifest_hash', info->'hash', 'input_as_of', info->'as_of',
        'job_id', info->'job', 'result_attempt_id', att->'id', 'result_lease_token', att->'token', 'status', 'partial',
        'rule_status', 'succeeded', 'ml_status', 'not_ready', 'latency_status', 'unavailable', 'evidence', '{"schemaVersion":3}'::jsonb,
        'decided_at', clock_timestamp()));
END $$;
-- _finish(result) success transition.
CREATE FUNCTION t.succeed(job uuid, att jsonb) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
 UPDATE run_job_attempts SET status='succeeded', finished_at=clock_timestamp() WHERE id=(att->>'id')::uuid;
 UPDATE run_jobs SET status='succeeded', finished_at=clock_timestamp(), error_code=NULL WHERE id=job;
END $$;
CREATE FUNCTION t.expect_error(label text, stmt text, pattern text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
 BEGIN
  EXECUTE stmt;
 EXCEPTION WHEN others THEN
  IF SQLERRM !~* pattern THEN RAISE EXCEPTION 'FAIL % : unexpected error "%" (want /%/)', label, SQLERRM, pattern; END IF;
  RAISE NOTICE 'PASS reject: % (%)', label, SQLERRM;
  RETURN;
 END;
 RAISE EXCEPTION 'FAIL % : statement was accepted', label;
END $$;
CREATE FUNCTION t.ok(label text, cond boolean) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
 IF cond IS NOT TRUE THEN RAISE EXCEPTION 'FAIL %', label; END IF;
 RAISE NOTICE 'PASS %', label;
END $$;
-- Ordered "event_kind:a<attempt>#<seq>" list for one job.
CREATE FUNCTION t.events(job uuid) RETURNS text LANGUAGE plpgsql AS $$ BEGIN RETURN (
 SELECT string_agg(e.event_kind || COALESCE(':a' || a.attempt_no || '#' || i.seq, ''), ' > ' ORDER BY e.id)
   FROM run_decision_events e
   LEFT JOIN run_decision_interims i ON i.id = e.interim_id
   LEFT JOIN run_job_attempts a ON a.id = i.attempt_id
   LEFT JOIN run_process_decisions d ON d.id = e.decision_id
  WHERE i.job_id = job OR d.job_id = job); END
$$;
-- Attempt 1 has an open chain and expired, attempt 2 is current and running.
CREATE FUNCTION t.scn() RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE i jsonb := t.mkjob(); a1 jsonb; a2 jsonb; BEGIN
 a1 := t.attempt((i->>'job')::uuid, 1); PERFORM t.interim((i->>'job')::uuid, a1, 'first_anomaly');
 UPDATE run_job_attempts SET status='expired' WHERE id=(a1->>'id')::uuid;
 a2 := t.attempt((i->>'job')::uuid, 2);
 RETURN i || jsonb_build_object('a1', a1, 'a2', a2); END $$;
-- Attempt 1 current with an open chain.
CREATE FUNCTION t.scn1() RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE i jsonb := t.mkjob(); a1 jsonb; BEGIN
 a1 := t.attempt((i->>'job')::uuid, 1); PERFORM t.interim((i->>'job')::uuid, a1, 'first_anomaly');
 RETURN i || jsonb_build_object('a1', a1); END $$;
CREATE FUNCTION t.fa_sql(s jsonb) RETURNS text LANGUAGE sql AS $$
 SELECT format($q$INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,lease_token,evidence_version,payload)
   VALUES (gen_random_uuid(),%L,%L,NULL,'first_anomaly',%L,'ev4','{}')$q$, s->>'job', s->'a2'->>'id', s->'a2'->>'token') $$;
CREATE FUNCTION t.dec_sql(s jsonb, att text) RETURNS text LANGUAGE sql AS $$
 SELECT format($q$INSERT INTO run_process_decisions(id,execution_id,process_observation_id,config_version_id,input_manifest_id,input_manifest_hash,
   input_as_of,job_id,result_attempt_id,result_lease_token,status,rule_status,ml_status,latency_status,evidence,decided_at)
   VALUES (gen_random_uuid(),%L,%L,%L,%L,%L,%L,%L,%L,%L,'partial','succeeded','not_ready','unavailable','{}',clock_timestamp()) RETURNING job_id$q$,
   s->>'exec', s->>'obs', s->>'config', s->>'manifest', s->>'hash', s->>'as_of', s->>'job', s->att->>'id', s->att->>'token') $$;
`;

// S1-S12: normal, supersede, three failure paths, ownership rejection, immutability, event extension.
const scenarios = [
['S1', `DO $$ DECLARE info jsonb := t.mkjob(); job uuid := (info->>'job')::uuid; a1 jsonb; BEGIN
 a1 := t.attempt(job, 1);
 PERFORM t.decision(info, a1); PERFORM t.succeed(job, a1);
 PERFORM t.ok('S1 no anomaly: no interim rows', NOT EXISTS (SELECT 1 FROM run_decision_interims WHERE job_id = job));
 PERFORM t.ok('S1 events = final only: ' || t.events(job), t.events(job) = 'final');
END $$`],
['S2', `DO $$ DECLARE info jsonb := t.mkjob(); job uuid := (info->>'job')::uuid; a1 jsonb; r jsonb; BEGIN
 a1 := t.attempt(job, 1);
 r := t.interim(job, a1, 'first_anomaly');
 PERFORM t.ok('S2 first_anomaly seq=1 assigned by DB', (r->>'seq')::int = 1);
 PERFORM t.interim(job, a1, 'evidence_added'); r := t.interim(job, a1, 'evidence_added');
 PERFORM t.ok('S2 evidence_added seq=3', (r->>'seq')::int = 3);
 PERFORM t.decision(info, a1);
 PERFORM t.expect_error('S2 interim after decision in final TX',
   format($q$SELECT t.interim(%L, %L::jsonb, 'evidence_added')$q$, job, a1), 'interim after final decision');
 PERFORM t.succeed(job, a1);
 PERFORM t.ok('S2 events: ' || t.events(job), t.events(job) = 'first_anomaly:a1#1 > evidence_added:a1#2 > evidence_added:a1#3 > final');
 PERFORM t.ok('S2 no superseded/no_final for finalized attempt', NOT EXISTS (SELECT 1 FROM run_decision_interims WHERE job_id = job AND kind IN ('superseded','no_final')));
 PERFORM t.expect_error('S2 interim after job succeeded',
   format($q$SELECT t.interim(%L, %L::jsonb, 'evidence_added')$q$, job, a1), 'current owner');
 PERFORM t.expect_error('S2 UPDATE interim', format('UPDATE run_decision_interims SET payload=%L WHERE job_id=%L', '{}', job), 'immutable');
 PERFORM t.expect_error('S2 DELETE interim', format('DELETE FROM run_decision_interims WHERE job_id=%L', job), 'immutable');
 PERFORM t.expect_error('S2 UPDATE interim event', format('UPDATE run_decision_events SET event_kind=%L WHERE interim_id IN (SELECT id FROM run_decision_interims WHERE job_id=%L)', 'final', job), 'immutable');
 PERFORM t.expect_error('S2 DELETE final event', format('DELETE FROM run_decision_events WHERE decision_id IN (SELECT id FROM run_process_decisions WHERE job_id=%L)', job), 'immutable');
END $$`],
['S3', `DO $$ DECLARE info jsonb := t.mkjob(); job uuid := (info->>'job')::uuid; a1 jsonb; a2 jsonb; BEGIN
 a1 := t.attempt(job, 1);
 PERFORM t.expect_error('S3 evidence_added before first_anomaly', format($q$SELECT t.interim(%L, %L::jsonb, 'evidence_added')$q$, job, a1), 'requires first_anomaly');
 PERFORM t.expect_error('S3 wrong lease token', format($q$SELECT t.interim(%L, %L::jsonb, 'first_anomaly', gen_random_uuid())$q$, job, a1), 'current owner');
 PERFORM t.expect_error('S3 first_anomaly without evidence_version',
   format($q$INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,lease_token,payload) VALUES (gen_random_uuid(),%L,%L,NULL,'first_anomaly',%L,'{}')$q$, job, a1->>'id', a1->>'token'), 'kind_shape');
 PERFORM t.expect_error('S3 payload must be object',
   format($q$INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,lease_token,evidence_version,payload) VALUES (gen_random_uuid(),%L,%L,NULL,'first_anomaly',%L,'v','[]')$q$, job, a1->>'id', a1->>'token'), 'check constraint');
 PERFORM t.expect_error('S3 unknown kind',
   format($q$INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,lease_token,evidence_version,payload) VALUES (gen_random_uuid(),%L,%L,NULL,'severity_changed',%L,'v','{}')$q$, job, a1->>'id', a1->>'token'), 'check constraint');
 PERFORM t.interim(job, a1, 'first_anomaly');
 PERFORM t.expect_error('S3 duplicate first_anomaly', format($q$SELECT t.interim(%L, %L::jsonb, 'first_anomaly')$q$, job, a1), 'must open');
 PERFORM t.expect_error('S3 direct superseded INSERT',
   format($q$INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,superseded_by_attempt_id,payload) VALUES (gen_random_uuid(),%L,%L,NULL,'superseded',%L,'{}')$q$, job, a1->>'id', a1->>'id'), 'only by database triggers');
 a2 := t.attempt(job, 2);
 PERFORM t.expect_error('S3 stale attempt (not current)', format($q$SELECT t.interim(%L, %L::jsonb, 'evidence_added')$q$, job, a1), 'current owner');
 PERFORM t.expect_error('S3 direct superseded INSERT by client',
   format($q$INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,superseded_by_attempt_id,payload) VALUES (gen_random_uuid(),%L,%L,NULL,'superseded',%L,'{}')$q$, job, a1->>'id', a2->>'id'), 'only by database triggers');
 PERFORM t.expect_error('S3 direct no_final INSERT by client',
   format($q$INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,reason_code,payload) VALUES (gen_random_uuid(),%L,%L,NULL,'no_final','x','{}')$q$, job, a1->>'id'), 'only by database triggers');
 UPDATE run_job_attempts SET lease_until = clock_timestamp() - interval '1 second' WHERE id = (a2->>'id')::uuid;
 PERFORM t.expect_error('S3 expired lease', format($q$SELECT t.interim(%L, %L::jsonb, 'first_anomaly')$q$, job, a2), 'unexpired lease');
 UPDATE run_job_attempts SET lease_until = clock_timestamp() + interval '60 seconds' WHERE id = (a2->>'id')::uuid;
 UPDATE run_job_attempts SET status='failed', finished_at=clock_timestamp(), error_code='detection_processing_failed' WHERE id=(a2->>'id')::uuid;
 UPDATE run_jobs SET status='queued', error_code='detection_processing_failed', next_run_at=clock_timestamp()+interval '5 seconds' WHERE id=job;
 PERFORM t.expect_error('S3 requeued job rejects interim', format($q$SELECT t.interim(%L, %L::jsonb, 'first_anomaly')$q$, job, a2), 'current owner');
 PERFORM t.ok('S3 requeue keeps attempt-1 chain open (no record)', NOT EXISTS (SELECT 1 FROM run_decision_interims WHERE job_id = job AND kind IN ('superseded','no_final')));
END $$`],
['S3b', `DO $$ DECLARE j jsonb; att jsonb; BEGIN
 j := t.ins('run_jobs', jsonb_build_object('kind','train','status','queued','requested_by_actor_id','admin1','requester_role','admin','max_attempts',3,'attempt_count',0,'idempotency_key',gen_random_uuid()::text));
 att := t.attempt((j->>'id')::uuid, 1);
 PERFORM t.expect_error('S3b legacy train job', format($q$SELECT t.interim(%L, %L::jsonb, 'first_anomaly')$q$, j->>'id', att), 'v03 detect');
END $$`],
['S4', `DO $$ DECLARE info jsonb := t.mkjob(); job uuid := (info->>'job')::uuid; a1 jsonb; a2 jsonb; BEGIN
 a1 := t.attempt(job, 1);
 PERFORM t.interim(job, a1, 'first_anomaly'); PERFORM t.interim(job, a1, 'evidence_added');
 UPDATE run_job_attempts SET lease_until = clock_timestamp() - interval '1 second' WHERE id=(a1->>'id')::uuid;
 UPDATE run_job_attempts SET status='expired', finished_at=clock_timestamp(), error_code='lease_expired' WHERE id=(a1->>'id')::uuid AND status='running' AND lease_until <= clock_timestamp();
 a2 := t.attempt(job, 2);
 PERFORM t.ok('S4 attempt-1 chain still open before attempt-2 record', NOT EXISTS (SELECT 1 FROM run_decision_interims WHERE job_id=job AND kind='superseded'));
 PERFORM t.interim(job, a2, 'first_anomaly');
 PERFORM t.ok('S4 superseded attempt-1 by attempt-2 (seq 3)', EXISTS (SELECT 1 FROM run_decision_interims WHERE attempt_id=(a1->>'id')::uuid AND kind='superseded' AND seq=3 AND superseded_by_attempt_id=(a2->>'id')::uuid));
 PERFORM t.interim(job, a2, 'evidence_added');
 PERFORM t.decision(info, a2); PERFORM t.succeed(job, a2);
 PERFORM t.ok('S4 trigger order: new attempt record event before superseded: ' || t.events(job), t.events(job) =
   'first_anomaly:a1#1 > evidence_added:a1#2 > first_anomaly:a2#1 > superseded:a1#3 > evidence_added:a2#2 > final');
 PERFORM t.ok('S4 single superseded', (SELECT count(*) FROM run_decision_interims WHERE job_id=job AND kind='superseded') = 1);
END $$`],
['S5', `DO $$ DECLARE info jsonb := t.mkjob(); job uuid := (info->>'job')::uuid; a1 jsonb; a2 jsonb; a3 jsonb; BEGIN
 a1 := t.attempt(job, 1);
 PERFORM t.interim(job, a1, 'first_anomaly');
 UPDATE run_job_attempts SET status='failed', finished_at=clock_timestamp() WHERE id=(a1->>'id')::uuid;
 UPDATE run_jobs SET status='queued' WHERE id=job;
 a2 := t.attempt(job, 2);
 UPDATE run_job_attempts SET status='failed', finished_at=clock_timestamp() WHERE id=(a2->>'id')::uuid;
 UPDATE run_jobs SET status='queued' WHERE id=job;
 PERFORM t.ok('S5 attempt-2 failure without record leaves attempt-1 open', NOT EXISTS (SELECT 1 FROM run_decision_interims WHERE job_id=job AND kind IN ('superseded','no_final')));
 a3 := t.attempt(job, 3);
 PERFORM t.decision(info, a3); PERFORM t.succeed(job, a3);
 PERFORM t.ok('S5 superseded by attempt-3 decision', EXISTS (SELECT 1 FROM run_decision_interims WHERE attempt_id=(a1->>'id')::uuid AND kind='superseded' AND superseded_by_attempt_id=(a3->>'id')::uuid));
 PERFORM t.ok('S5 trigger order: final event before superseded: ' || t.events(job), t.events(job) = 'first_anomaly:a1#1 > final > superseded:a1#2');
END $$`],
['S6', `DO $$ DECLARE info jsonb := t.mkjob(); job uuid := (info->>'job')::uuid; a3 jsonb; BEGIN
 PERFORM t.attempt(job, 1); UPDATE run_job_attempts SET status='failed' WHERE job_id=job; UPDATE run_jobs SET status='queued' WHERE id=job;
 PERFORM t.attempt(job, 2); UPDATE run_job_attempts SET status='failed' WHERE job_id=job AND attempt_no=2; UPDATE run_jobs SET status='queued' WHERE id=job;
 a3 := t.attempt(job, 3);
 PERFORM t.interim(job, a3, 'first_anomaly');
 UPDATE run_job_attempts SET status='failed', finished_at=clock_timestamp(), error_code='detection_processing_failed' WHERE id=(a3->>'id')::uuid;
 UPDATE run_jobs SET status='failed', error_code='detection_processing_failed', finished_at=clock_timestamp() WHERE id=job;
 PERFORM t.ok('S6 failure path 1 (_finish 3rd failure): no_final(attempt-3, detection_processing_failed)', EXISTS (SELECT 1 FROM run_decision_interims WHERE attempt_id=(a3->>'id')::uuid AND kind='no_final' AND reason_code='detection_processing_failed' AND seq=2));
 PERFORM t.ok('S6 events: ' || t.events(job), t.events(job) = 'first_anomaly:a3#1 > no_final:a3#2');
 PERFORM t.expect_error('S6 interim after failure', format($q$SELECT t.interim(%L, %L::jsonb, 'evidence_added')$q$, job, a3), 'chain of attempt is closed');
END $$`],
['S7', `DO $$ DECLARE info jsonb := t.mkjob(); job uuid := (info->>'job')::uuid; a3 jsonb; BEGIN
 PERFORM t.attempt(job, 1); UPDATE run_job_attempts SET status='failed' WHERE job_id=job; UPDATE run_jobs SET status='queued' WHERE id=job;
 PERFORM t.attempt(job, 2); UPDATE run_job_attempts SET status='failed' WHERE job_id=job AND attempt_no=2; UPDATE run_jobs SET status='queued' WHERE id=job;
 a3 := t.attempt(job, 3);
 PERFORM t.interim(job, a3, 'first_anomaly'); PERFORM t.interim(job, a3, 'evidence_added');
 UPDATE run_job_attempts SET lease_until = clock_timestamp() - interval '1 second' WHERE id=(a3->>'id')::uuid;
 UPDATE run_job_attempts SET status='expired', finished_at=clock_timestamp(), error_code='lease_expired' WHERE id=(a3->>'id')::uuid AND status='running' AND lease_until <= clock_timestamp();
 UPDATE run_jobs SET status='failed', error_code='max_attempts', finished_at=clock_timestamp() WHERE id=job;
 PERFORM t.ok('S7 failure path 2 (claim over max attempts): no_final(attempt-3, max_attempts)', EXISTS (SELECT 1 FROM run_decision_interims WHERE attempt_id=(a3->>'id')::uuid AND kind='no_final' AND reason_code='max_attempts' AND seq=3));
 PERFORM t.ok('S7 events: ' || t.events(job), t.events(job) = 'first_anomaly:a3#1 > evidence_added:a3#2 > no_final:a3#3');
END $$`],
['S8', `DO $$ DECLARE info jsonb := t.mkjob(); job uuid := (info->>'job')::uuid; a1 jsonb; BEGIN
 a1 := t.attempt(job, 1);
 PERFORM t.interim(job, a1, 'first_anomaly');
 UPDATE run_job_attempts SET lease_until = clock_timestamp() - interval '1 second' WHERE id=(a1->>'id')::uuid;
 INSERT INTO powercode_queue.detection_logs(id, payload, status, attempts) VALUES (job, jsonb_build_object('jobId', job::text), 'dead', 3);
 -- pg_queue.reconcile_dead_jobs statements for the detection queue.
 UPDATE run_job_attempts a SET status='expired',finished_at=clock_timestamp(),error_code='queue_exhausted'
   FROM run_jobs j WHERE a.id=j.current_attempt_id AND a.job_id=j.id AND a.status='running'
     AND a.lease_until<=clock_timestamp() AND EXISTS(SELECT 1 FROM powercode_queue.detection_logs q
     WHERE q.status='dead' AND q.payload->>'jobId'=j.id::text);
 UPDATE run_jobs j SET status='failed',error_code='queue_exhausted',finished_at=clock_timestamp()
   WHERE j.status IN ('queued','running') AND EXISTS(SELECT 1 FROM powercode_queue.detection_logs q
     WHERE q.status='dead' AND q.payload->>'jobId'=j.id::text)
     AND NOT EXISTS(SELECT 1 FROM run_job_attempts a WHERE a.id=j.current_attempt_id
       AND a.status='running' AND a.lease_until>clock_timestamp());
 PERFORM t.ok('S8 failure path 3 (dead queue reconcile): no_final(attempt-1, queue_exhausted)', EXISTS (SELECT 1 FROM run_decision_interims WHERE attempt_id=(a1->>'id')::uuid AND kind='no_final' AND reason_code='queue_exhausted'));
 PERFORM t.ok('S8 events: ' || t.events(job), t.events(job) = 'first_anomaly:a1#1 > no_final:a1#2');
END $$`],
['S8b', `DO $$ DECLARE info jsonb := t.mkjob(); job uuid := (info->>'job')::uuid; a1 jsonb; a2 jsonb; BEGIN
 a1 := t.attempt(job, 1);
 PERFORM t.interim(job, a1, 'first_anomaly');
 UPDATE run_job_attempts SET status='expired', lease_until=clock_timestamp()-interval '1 second' WHERE id=(a1->>'id')::uuid;
 a2 := t.attempt(job, 2);
 UPDATE run_job_attempts SET lease_until = clock_timestamp() - interval '1 second' WHERE id=(a2->>'id')::uuid;
 UPDATE run_job_attempts SET status='expired' WHERE id=(a2->>'id')::uuid;
 UPDATE run_jobs SET status='failed', error_code='queue_exhausted' WHERE id=job;
 PERFORM t.ok('S8b earlier open chain closed by no_final: ' || t.events(job), t.events(job) = 'first_anomaly:a1#1 > no_final:a1#2');
END $$`],
['S9', `DO $$ DECLARE info jsonb := t.mkjob(); job uuid := (info->>'job')::uuid; a1 jsonb; BEGIN
 a1 := t.attempt(job, 1);
 UPDATE run_job_attempts SET status='failed' WHERE id=(a1->>'id')::uuid;
 UPDATE run_jobs SET status='failed', error_code='detection_processing_failed' WHERE id=job;
 PERFORM t.ok('S9 failed job without interim: no rows/events (#78-Q3)', NOT EXISTS (SELECT 1 FROM run_decision_interims WHERE job_id=job) AND t.events(job) IS NULL);
END $$`],
['S10', `DO $$ DECLARE info jsonb := t.mkjob(); job uuid := (info->>'job')::uuid; a1 jsonb; BEGIN
 a1 := t.attempt(job, 1);
 PERFORM t.interim(job, a1, 'first_anomaly');
 PERFORM t.ok('S10 advisory lock held by interim TX', EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=81736 AND objid=0 AND pid=pg_backend_pid() AND granted));
END $$`],
['S11', `DO $$ DECLARE old_count int; final_count int; all_count int; new_count int; BEGIN
 SELECT count(*) INTO old_count FROM (SELECT e.id::text AS "eventId",d.id,d.status,
          d.config_version_id AS "configVersionId",d.rule_status AS "ruleStatus",d.ml_status AS "mlStatus",
          d.decision_code AS "decisionCode",d.anomaly_score AS "anomalyScore",d.evidence,
          o.source_id AS "sourceId",d.decided_at AS "decidedAt",d.process_observation_id AS "processObservationId"
          FROM run_decision_events e JOIN run_process_decisions d ON d.id=e.decision_id
          JOIN data_observations o ON o.id=d.process_observation_id
          WHERE e.id>0::bigint ORDER BY e.id LIMIT 100000) x;
 SELECT count(*) INTO final_count FROM run_decision_events WHERE event_kind='final';
 SELECT count(*) INTO all_count FROM run_decision_events;
 SELECT count(*) INTO new_count FROM (
  SELECT e.id, e.event_kind, COALESCE(d.job_id, i.job_id) AS job_id, a.attempt_no, i.seq, i.payload, i.reason_code,
         COALESCE(d.config_version_id, j.input_config_version_id) AS config_version_id,
         COALESCE(o.source_id, m.source_id) AS source_id, COALESCE(d.process_observation_id, m.process_observation_id) AS process_observation_id
    FROM run_decision_events e
    LEFT JOIN run_process_decisions d ON d.id = e.decision_id
    LEFT JOIN data_observations o ON o.id = d.process_observation_id
    LEFT JOIN run_decision_interims i ON i.id = e.interim_id
    LEFT JOIN run_job_attempts a ON a.id = i.attempt_id
    LEFT JOIN run_jobs j ON j.id = i.job_id
    LEFT JOIN run_input_manifests m ON m.id = j.input_manifest_id
   WHERE e.id > 0 ORDER BY e.id) y;
 PERFORM t.ok(format('S11 current API query (inner join) returns only final events (%s of %s)', old_count, all_count), old_count = final_count AND all_count > final_count);
 PERFORM t.ok(format('S11 proposed query returns all events (%s)', new_count), new_count = all_count);
 PERFORM t.ok('S11 event_kind final <=> decision_id present (all events)',
   NOT EXISTS (SELECT 1 FROM run_decision_events WHERE (event_kind='final') <> (decision_id IS NOT NULL)));
END $$`],
['S12', `DO $$ DECLARE info jsonb := t.mkjob(); job uuid := (info->>'job')::uuid; a1 jsonb; r jsonb; BEGIN
 a1 := t.attempt(job, 1); r := t.interim(job, a1, 'first_anomaly');
 PERFORM t.expect_error('S12 event kind/interim kind mismatch', format($q$INSERT INTO run_decision_events(event_kind, interim_id) VALUES ('no_final', %L)$q$, gen_random_uuid()), 'foreign key');
 PERFORM t.expect_error('S12 duplicate interim event', format($q$INSERT INTO run_decision_events(event_kind, interim_id) VALUES ('first_anomaly', %L)$q$, r->>'id'), 'duplicate key');
 PERFORM t.expect_error('S12 final without decision', $q$INSERT INTO run_decision_events(event_kind) VALUES ('final')$q$, 'target_check');
 PERFORM t.expect_error('S12 event kind required (no default)', $q$INSERT INTO run_decision_events(decision_id) VALUES (NULL)$q$, 'null value');
 PERFORM t.expect_error('S12 unknown event kind', format($q$INSERT INTO run_decision_events(event_kind, interim_id) VALUES ('severity_changed', %L)$q$, r->>'id'), 'kind_check');
END $$`],
['Q11 owner', `DO $$ DECLARE info jsonb := t.mkjob(); job uuid := (info->>'job')::uuid; a1 jsonb; BEGIN
 a1 := t.attempt(job, 1);
 PERFORM t.interim(job, a1, 'first_anomaly');
 UPDATE run_jobs SET status='failed', error_code='detection_processing_failed' WHERE id=job;
 UPDATE run_jobs SET status='running', error_code=NULL WHERE id=job;
 PERFORM t.expect_error('Q11 decision after no_final rejected', format('SELECT t.decision(%L::jsonb, %L::jsonb)', info, a1), 'already closed its interim chain as no_final');
 PERFORM t.ok('Q11 rejected decision left no row/event: ' || t.events(job), t.events(job) = 'first_anomaly:a1#1 > no_final:a1#2'
   AND NOT EXISTS (SELECT 1 FROM run_process_decisions WHERE job_id=job));
END $$`],
['Q11 normal', `DO $$ DECLARE info jsonb := t.mkjob(); job uuid := (info->>'job')::uuid; a1 jsonb; BEGIN
 -- A failed job without an interim chain has no no_final row (#78-Q3), so the guard does not fire on it either.
 a1 := t.attempt(job, 1);
 UPDATE run_jobs SET status='failed', error_code='detection_processing_failed' WHERE id=job;
 UPDATE run_jobs SET status='running', error_code=NULL WHERE id=job;
 PERFORM t.decision(info, a1); PERFORM t.succeed(job, a1);
 PERFORM t.ok('Q11 guard only reacts to no_final rows (failed job without chain): ' || t.events(job), t.events(job) = 'final');
END $$`],
['functions', `DO $$ BEGIN
 PERFORM t.ok('all 6 012 functions pin search_path=pg_catalog, pg_temp',
  (SELECT count(*) FROM pg_proc WHERE pronamespace='public'::regnamespace AND proname IN ('validate_decision_interim','supersede_decision_interims',
     'close_decision_interims_on_job_failure','publish_decision_event','publish_decision_interim_event','reject_decision_after_no_final')
     AND proconfig = ARRAY['search_path=pg_catalog, pg_temp']) = 6);
 PERFORM t.ok('only the two closing functions are SECURITY DEFINER, owned by the table owner, EXECUTE revoked from PUBLIC',
  (SELECT array_agg(p.proname::text ORDER BY p.proname) FROM pg_proc p, pg_class c
    WHERE c.oid='public.run_decision_interims'::regclass AND p.pronamespace='public'::regnamespace AND p.prosecdef
      AND p.proname IN ('validate_decision_interim','supersede_decision_interims','close_decision_interims_on_job_failure',
        'publish_decision_event','publish_decision_interim_event','reject_decision_after_no_final')
      AND p.proowner = c.relowner AND NOT has_function_privilege('public', p.oid, 'EXECUTE'))
   = ARRAY['close_decision_interims_on_job_failure','supersede_decision_interims']);
 PERFORM t.ok('012 triggers present (BEFORE/AFTER, tables)', (SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname IN
   ('interim_validate','interim_immutable','interim_supersede','decision_supersede_interims','decision_interim_job_failure',
    'interim_event_insert','decision_no_final_guard')) = 7);
END $$`],
];

const upgradeSeed = `DO $$ DECLARE info jsonb := t.mkjob(); job uuid := (info->>'job')::uuid; a1 jsonb; BEGIN
 a1 := t.attempt(job, 1);
 PERFORM t.decision(info, a1); PERFORM t.succeed(job, a1);
 PERFORM t.ins('run_process_decisions', jsonb_build_object('id', gen_random_uuid(), 'execution_id', info->'exec', 'process_observation_id', info->'obs',
   'config_version_id', info->'config', 'input_manifest_hash', 'legacy-hash', 'status', 'complete', 'rule_status', 'succeeded', 'ml_status', 'skipped',
   'latency_status', 'unavailable', 'evidence', '{}'::jsonb, 'decided_at', clock_timestamp()));
 PERFORM t.attempt((t.mkjob()->>'job')::uuid, 1);
END $$;
CREATE TABLE t.events_before AS SELECT * FROM run_decision_events;`;

const upgradeChecks = [
['U1-U4', `DO $$ BEGIN
 PERFORM t.ok('U0 pre-012 seed has final events', (SELECT count(*) FROM t.events_before) = 2);
 PERFORM t.ok('U1 existing events unchanged (id, decision_id, created_at) and typed final',
   NOT EXISTS (SELECT 1 FROM t.events_before b FULL JOIN run_decision_events e ON e.id = b.id
               WHERE b.id IS NULL OR e.id IS NULL OR e.decision_id IS DISTINCT FROM b.decision_id
                  OR e.created_at IS DISTINCT FROM b.created_at OR e.event_kind <> 'final' OR e.interim_id IS NOT NULL));
 PERFORM t.ok('U2 existing final event per decision still 1', NOT EXISTS (SELECT decision_id FROM run_decision_events WHERE decision_id IS NOT NULL GROUP BY 1 HAVING count(*) > 1));
 PERFORM t.expect_error('U3 existing event still immutable', 'UPDATE run_decision_events SET created_at = clock_timestamp()', 'immutable');
 PERFORM t.ok('U4 event_kind has no default after upgrade', (SELECT column_default IS NULL AND is_nullable='NO' FROM information_schema.columns
   WHERE table_name='run_decision_events' AND column_name='event_kind'));
END $$`],
['U5', `DO $$ DECLARE j uuid; a jsonb; info jsonb; maxold bigint := (SELECT max(id) FROM t.events_before); BEGIN
 SELECT r.id INTO STRICT j FROM run_jobs r WHERE r.status='running' AND r.kind='detect';
 SELECT jsonb_build_object('id', x.id, 'token', x.lease_token) INTO a FROM run_job_attempts x WHERE x.job_id=j;
 SELECT jsonb_build_object('job', r.id, 'manifest', m.id, 'hash', m.content_hash, 'as_of', m.input_as_of, 'obs', m.process_observation_id,
        'exec', m.execution_id, 'config', r.input_config_version_id) INTO info
   FROM run_jobs r JOIN run_input_manifests m ON m.id=r.input_manifest_id WHERE r.id=j;
 PERFORM t.interim(j, a, 'first_anomaly');
 PERFORM t.decision(info, a); PERFORM t.succeed(j, a);
 PERFORM t.ok('U5 job in flight during upgrade finalizes with interim: ' || t.events(j), t.events(j) = 'first_anomaly:a1#1 > final'
   AND (SELECT min(id) FROM run_decision_events WHERE id > maxold) > maxold);
END $$`],
];

// Attack fixtures (owner session, after database-roles.sql).
const attackSetup = `
CREATE TABLE t.adv(name text PRIMARY KEY, s jsonb);
INSERT INTO t.adv SELECT n, t.scn() FROM unnest(ARRAY['cxa','cxc','x1','x1c','x2','x2c','x3','x4','x5','x6']) n;
INSERT INTO t.adv SELECT n, t.scn1() FROM unnest(ARRAY['cxb','r5']) n;
INSERT INTO t.adv SELECT 'g', i || jsonb_build_object('a1', t.attempt((i->>'job')::uuid, 1)) FROM t.mkjob() i;
UPDATE run_job_attempts SET status='failed' WHERE id=(SELECT (s->'a1'->>'id')::uuid FROM t.adv WHERE name='g');
UPDATE run_jobs SET status='failed', error_code='detection_processing_failed' WHERE id=(SELECT (s->>'job')::uuid FROM t.adv WHERE name='g');
UPDATE run_job_attempts SET lease_until = clock_timestamp() - interval '1 second' WHERE id = (SELECT (s->'a2'->>'id')::uuid FROM t.adv WHERE name='x5');
UPDATE run_job_attempts SET status='running', lease_until = clock_timestamp() + interval '60 seconds' WHERE id = (SELECT (s->'a1'->>'id')::uuid FROM t.adv WHERE name='x6');
CREATE TABLE t.y(name text PRIMARY KEY, s jsonb);
INSERT INTO t.y SELECT n, t.scn() FROM unnest(ARRAY['y1','y2']) n;
INSERT INTO t.y SELECT n, t.scn1() FROM unnest(ARRAY['y5','y6']) n;
INSERT INTO t.y SELECT 'y7', i || jsonb_build_object('a1', t.attempt((i->>'job')::uuid, 1)) FROM t.mkjob() i;
UPDATE run_job_attempts SET lease_until = clock_timestamp() - interval '1 minute'
 WHERE id IN (SELECT (s->'a1'->>'id')::uuid FROM t.y WHERE name IN ('y6','y7'));
CREATE TABLE t.y_before AS SELECT name, t.events((s->>'job')::uuid) AS events FROM t.y;
CREATE TABLE t.y7(name text PRIMARY KEY, s jsonb);
INSERT INTO t.y7 SELECT 'y7a2', t.scn();
INSERT INTO t.y7 SELECT 'yp', t.scn1();
SELECT t.interim((s->>'job')::uuid, s->'a2', 'first_anomaly') FROM t.y7 WHERE name='y7a2';
CREATE TABLE t.y7_before AS SELECT name, t.events((s->>'job')::uuid) AS events FROM t.y7;
CREATE TABLE t.b(name text PRIMARY KEY, s jsonb);
INSERT INTO t.b SELECT 'own', t.scn1();
GRANT USAGE ON SCHEMA t TO powercode_detector, powercode_api;
GRANT SELECT ON ALL TABLES IN SCHEMA t TO powercode_detector, powercode_api;`;

// Runtime login session prelude: temp trigger that runs SQL text at trigger depth 1 (nested INSERTs are depth 2).
const viaTrigger = `
CREATE TEMP TABLE tt(x int);
CREATE FUNCTION pg_temp.run_sql() RETURNS trigger LANGUAGE plpgsql AS $f$ BEGIN EXECUTE TG_ARGV[0]; RETURN NULL; END $f$;
CREATE FUNCTION pg_temp.via_trigger(sql text) RETURNS void LANGUAGE plpgsql AS $f$ BEGIN
 EXECUTE 'DROP TRIGGER IF EXISTS tt_t ON tt';
 EXECUTE format('CREATE TRIGGER tt_t AFTER INSERT ON tt FOR EACH ROW EXECUTE FUNCTION pg_temp.run_sql(%L)', sql);
 INSERT INTO tt VALUES (1); END $f$;`;
const fakePgClass = `
CREATE TEMP TABLE pg_class(oid oid, relowner oid);
INSERT INTO pg_temp.pg_class VALUES ('public.run_decision_interims'::regclass, (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = current_user));`;

// CX-A..C, X1..X7, G1, G2 by the Detector login.
const detectorForgery = `DO $d$ DECLARE s jsonb; BEGIN
 s := (SELECT adv.s FROM t.adv WHERE name='cxa');
 PERFORM t.expect_error('CX-A multi-row first_anomaly + forged superseded', format('SELECT pg_temp.via_trigger(%L)', format(
  $q$INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,lease_token,evidence_version,superseded_by_attempt_id,payload) VALUES
   (gen_random_uuid(),%L,%L,1,'first_anomaly',%L,'ev4',NULL,'{"legit":true}'),
   ('00000000-0000-4000-8000-0000000000aa',%L,%L,7,'superseded',NULL,NULL,%L,'{"forged":"x"}')$q$,
   s->>'job', s->'a2'->>'id', s->'a2'->>'token', s->>'job', s->'a1'->>'id', s->'a2'->>'id')), 'owner-defined triggers');
 s := (SELECT adv.s FROM t.adv WHERE name='cxb');
 PERFORM t.expect_error('CX-B CTE job failed + forged no_final', format('SELECT pg_temp.via_trigger(%L)', format(
  $q$WITH u AS (UPDATE run_jobs SET status='failed', error_code='detection_processing_failed' WHERE id=%L RETURNING id)
   INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,reason_code,payload)
   SELECT '00000000-0000-4000-8000-0000000000bb', u.id, %L, 9, 'no_final', 'forged_reason', '{"forged":1}' FROM u$q$,
   s->>'job', s->'a1'->>'id')), 'owner-defined triggers');
 s := (SELECT adv.s FROM t.adv WHERE name='cxc');
 PERFORM t.expect_error('CX-C CTE decision + forged superseded', format('SELECT pg_temp.via_trigger(%L)', format(
  $q$WITH d AS (%s) INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,superseded_by_attempt_id,payload)
   SELECT '00000000-0000-4000-8000-0000000000cc', d.job_id, %L, 5, 'superseded', %L, '{"forged":"c"}' FROM d$q$,
   t.dec_sql(s,'a2'), s->'a1'->>'id', s->'a2'->>'id')), 'owner-defined triggers');
 s := (SELECT adv.s FROM t.adv WHERE name='x1');
 PERFORM t.expect_error('X1 forged superseded before first_anomaly AFTER trigger', format('SELECT pg_temp.via_trigger(%L)', format(
  $q$WITH f AS (%s RETURNING job_id) INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,superseded_by_attempt_id,payload)
   SELECT gen_random_uuid(), f.job_id, %L, NULL, 'superseded', %L, '{}' FROM f$q$, t.fa_sql(s), s->'a1'->>'id', s->'a2'->>'id')), 'owner-defined triggers');
 s := (SELECT adv.s FROM t.adv WHERE name='x1c');
 PERFORM pg_temp.via_trigger(t.fa_sql(s));
 s := (SELECT adv.s FROM t.adv WHERE name='x2');
 PERFORM t.expect_error('X2 kind swap (forged no_final instead of superseded)', format('SELECT pg_temp.via_trigger(%L)', format(
  $q$WITH f AS (%s RETURNING job_id), u AS (UPDATE run_jobs SET status='failed', error_code='detection_processing_failed' WHERE id IN (SELECT job_id FROM f) RETURNING id)
   INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,reason_code,payload) SELECT gen_random_uuid(), u.id, %L, NULL, 'no_final', 'x', '{}' FROM u$q$,
   t.fa_sql(s), s->'a1'->>'id')), 'owner-defined triggers');
 s := (SELECT adv.s FROM t.adv WHERE name='x2c');
 PERFORM pg_temp.via_trigger(format($q$WITH f AS (%s RETURNING job_id) UPDATE run_jobs SET status='failed', error_code='detection_processing_failed' WHERE id IN (SELECT job_id FROM f)$q$, t.fa_sql(s)));
 s := (SELECT adv.s FROM t.adv WHERE name='x3');
 PERFORM t.expect_error('X3a direct event for same-statement interim', format($q$WITH f AS (%s RETURNING id, kind) INSERT INTO run_decision_events(event_kind, interim_id) SELECT kind, id FROM f$q$, t.fa_sql(s)), 'duplicate key|unique');
 PERFORM t.expect_error('X3b direct event OVERRIDING SYSTEM VALUE', format($q$INSERT INTO run_decision_events(id,event_kind,interim_id) OVERRIDING SYSTEM VALUE SELECT 999999, kind, id FROM run_decision_interims WHERE attempt_id=%L$q$, s->'a1'->>'id'), 'duplicate key|unique|permission');
 PERFORM t.expect_error('X3c direct event kind mismatch', format($q$INSERT INTO run_decision_events(event_kind,interim_id) SELECT 'superseded', id FROM run_decision_interims WHERE attempt_id=%L$q$, s->'a1'->>'id'), 'duplicate key|unique|foreign key');
 s := (SELECT adv.s FROM t.adv WHERE name='x4');
 PERFORM t.interim((s->>'job')::uuid, s->'a2', 'first_anomaly');
 PERFORM t.expect_error('X4 decision then evidence_added same statement', format($q$WITH d AS (%s) INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,lease_token,evidence_version,payload)
   SELECT gen_random_uuid(), d.job_id, %L, NULL, 'evidence_added', %L, 'ev4', '{}' FROM d$q$, t.dec_sql(s,'a2'), s->'a2'->>'id', s->'a2'->>'token'), 'after final decision');
 s := (SELECT adv.s FROM t.adv WHERE name='x5');
 PERFORM t.expect_error('X5a first_anomaly with expired current lease', format($q$SELECT t.interim(%L::uuid, %L::jsonb, 'first_anomaly')$q$, s->>'job', s->'a2'), 'current owner and unexpired lease');
 s := (SELECT adv.s FROM t.adv WHERE name='x6');
 PERFORM t.expect_error('X5b evidence_added by non-current attempt with valid lease', format($q$SELECT t.interim(%L::uuid, %L::jsonb, 'evidence_added')$q$, s->>'job', s->'a1'), 'current owner and unexpired lease');
 PERFORM t.expect_error('X6a superseded by newer attempt without its record', format('SELECT pg_temp.via_trigger(%L)', format(
  $q$INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,superseded_by_attempt_id,payload) VALUES (gen_random_uuid(),%L,%L,NULL,'superseded',%L,'{}')$q$,
  s->>'job', s->'a1'->>'id', s->'a2'->>'id')), 'owner-defined triggers|requires first_anomaly or decision');
 PERFORM t.expect_error('X6b superseded by own attempt', format('SELECT pg_temp.via_trigger(%L)', format(
  $q$INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,superseded_by_attempt_id,payload) VALUES (gen_random_uuid(),%L,%L,NULL,'superseded',%L,'{}')$q$,
  s->>'job', s->'a1'->>'id', s->'a1'->>'id')), 'owner-defined triggers|kind_shape|newer current attempt');
 PERFORM t.expect_error('X7a temp trigger using supersede_decision_interims()',
   'CREATE TRIGGER tt_x7 AFTER INSERT ON tt FOR EACH ROW EXECUTE FUNCTION public.supersede_decision_interims()', 'permission denied');
 PERFORM t.expect_error('X7b temp trigger using close_decision_interims_on_job_failure()',
   'CREATE TRIGGER tt_x7 AFTER UPDATE ON tt FOR EACH ROW EXECUTE FUNCTION public.close_decision_interims_on_job_failure()', 'permission denied');
 s := (SELECT adv.s FROM t.adv WHERE name='g');
 PERFORM t.expect_error('G1 temp-trigger no_final without open chain', format('SELECT pg_temp.via_trigger(%L)', format(
  $q$INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,reason_code,payload) VALUES (gen_random_uuid(),%L,%L,NULL,'no_final','x','{}')$q$,
  s->>'job', s->'a1'->>'id')), 'owner-defined triggers|open interim chain');
 PERFORM t.expect_error('G2 client direct no_final', format(
  $q$INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,reason_code,payload) VALUES (gen_random_uuid(),%L,%L,NULL,'no_final','x','{}')$q$,
  s->>'job', s->'a1'->>'id'), 'only by database triggers');
END $d$`;

const forgeryChecks = `DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT name, (s->>'job')::uuid AS job FROM t.adv WHERE name IN ('cxa','cxb','cxc','x1','x2') LOOP
  PERFORM t.ok(format('%s rejected statement left only the original record (events: %s)', r.name, t.events(r.job)),
    t.events(r.job) = 'first_anomaly:a1#1' AND (SELECT count(*) FROM run_decision_interims WHERE job_id=r.job) = 1
    AND NOT EXISTS (SELECT 1 FROM run_process_decisions WHERE job_id=r.job)
    AND (SELECT status::text FROM run_jobs WHERE id=r.job) = 'running');
 END LOOP;
 PERFORM t.ok('no forged ids anywhere', NOT EXISTS (SELECT 1 FROM run_decision_interims WHERE id IN
   ('00000000-0000-4000-8000-0000000000aa','00000000-0000-4000-8000-0000000000bb','00000000-0000-4000-8000-0000000000cc')));
 PERFORM t.ok('X1c legitimate supersede order: ' || t.events((SELECT (s->>'job')::uuid FROM t.adv WHERE name='x1c')),
   t.events((SELECT (s->>'job')::uuid FROM t.adv WHERE name='x1c')) = 'first_anomaly:a1#1 > first_anomaly:a2#1 > superseded:a1#2');
 PERFORM t.ok('X2c legitimate supersede then no_final: ' || t.events((SELECT (s->>'job')::uuid FROM t.adv WHERE name='x2c')),
   t.events((SELECT (s->>'job')::uuid FROM t.adv WHERE name='x2c')) = 'first_anomaly:a1#1 > first_anomaly:a2#1 > superseded:a1#2 > no_final:a2#2');
 PERFORM t.ok('G job without chain still has no interim rows', NOT EXISTS (SELECT 1 FROM run_decision_interims WHERE job_id=(SELECT (s->>'job')::uuid FROM t.adv WHERE name='g')));
 PERFORM t.ok('R5 API role has no interim/event INSERT and no attempt UPDATE grant',
   NOT has_table_privilege('powercode_api','run_decision_interims','INSERT') AND NOT has_table_privilege('powercode_api','run_decision_events','INSERT')
   AND NOT has_column_privilege('powercode_api','run_job_attempts','status','UPDATE'));
 PERFORM t.ok('R5 API login failed a detect job with an open chain -> no_final by owner trigger: ' || t.events((SELECT (s->>'job')::uuid FROM t.adv WHERE name='r5')),
   t.events((SELECT (s->>'job')::uuid FROM t.adv WHERE name='r5')) = 'first_anomaly:a1#1 > no_final:a1#2'
   AND EXISTS (SELECT 1 FROM run_decision_interims WHERE job_id=(SELECT (s->>'job')::uuid FROM t.adv WHERE name='r5') AND kind='no_final' AND reason_code='api_cancelled'));
END $$;
DO $$ DECLARE info jsonb := t.mkjob(); d jsonb; before_rows bigint := (SELECT count(*) FROM run_decision_interims); BEGIN
 d := t.ins('run_process_decisions', jsonb_build_object('id', gen_random_uuid(), 'execution_id', info->'exec', 'process_observation_id', info->'obs',
   'config_version_id', info->'config', 'input_manifest_hash', 'legacy-post-012', 'status', 'complete', 'rule_status', 'succeeded', 'ml_status', 'skipped',
   'latency_status', 'unavailable', 'evidence', '{}'::jsonb, 'decided_at', clock_timestamp()));
 PERFORM t.ok('G3 legacy decision (job_id NULL) after 012 gets one final event', (SELECT count(*) FROM run_decision_events WHERE decision_id=(d->>'id')::uuid AND event_kind='final') = 1);
 PERFORM t.ok('G3 legacy decision creates no interim rows', (SELECT count(*) FROM run_decision_interims) = before_rows);
END $$`;

// Shadowing attacks: Y1/Y2/Y5 fake pg_class (+ run_jobs), Y6 fake run_job_attempts.
const shadowAttacks = [
['Y1', viaTrigger + fakePgClass, `DO $d$ DECLARE s jsonb := (SELECT y.s FROM t.y WHERE name='y1'); BEGIN
 PERFORM t.expect_error('Y1 fake pg_class + forged superseded (order manipulation)', format('SELECT pg_temp.via_trigger(%L)', format(
  $q$WITH f AS (%s RETURNING job_id) INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,superseded_by_attempt_id,payload)
   SELECT gen_random_uuid(), f.job_id, %L, NULL, 'superseded', %L, '{}' FROM f$q$, t.fa_sql(s), s->'a1'->>'id', s->'a2'->>'id')), 'owner-defined triggers');
END $d$`],
['Y2', viaTrigger + fakePgClass, `DO $d$ DECLARE s jsonb := (SELECT y.s FROM t.y WHERE name='y2'); BEGIN
 PERFORM t.expect_error('Y2 fake pg_class + kind swap (forged no_final)', format('SELECT pg_temp.via_trigger(%L)', format(
  $q$WITH f AS (%s RETURNING job_id), u AS (UPDATE run_jobs SET status='failed', error_code='detection_processing_failed' WHERE id IN (SELECT job_id FROM f) RETURNING id)
   INSERT INTO run_decision_interims(id,job_id,attempt_id,seq,kind,reason_code,payload) SELECT gen_random_uuid(), u.id, %L, NULL, 'no_final', 'x', '{}' FROM u$q$,
   t.fa_sql(s), s->'a1'->>'id')), 'owner-defined triggers');
END $d$`],
['Y5', viaTrigger + fakePgClass + `
CREATE TEMP TABLE run_jobs AS SELECT id, contract_version, kind::text AS kind, 'failed'::text AS status, current_attempt_id, 'forged_reason'::text AS error_code
  FROM public.run_jobs WHERE id = (SELECT (s->>'job')::uuid FROM t.y WHERE name='y5');`, `DO $d$ DECLARE s jsonb := (SELECT y.s FROM t.y WHERE name='y5'); BEGIN
 PERFORM t.expect_error('Y5 fake pg_class + fake run_jobs + forged no_final on running job', format('SELECT pg_temp.via_trigger(%L)', format(
  $q$INSERT INTO public.run_decision_interims(id,job_id,attempt_id,seq,kind,reason_code,payload) VALUES (gen_random_uuid(),%L,%L,NULL,'no_final','x','{}')$q$,
  s->>'job', s->'a1'->>'id')), 'owner-defined triggers|no_final requires');
END $d$`],
['Y6', `
CREATE TEMP TABLE run_job_attempts AS SELECT id, job_id, attempt_no, status, lease_token, clock_timestamp() + interval '1 hour' AS lease_until
  FROM public.run_job_attempts WHERE id = (SELECT (s->'a1'->>'id')::uuid FROM t.y WHERE name='y6');`, `DO $d$ DECLARE s jsonb := (SELECT y.s FROM t.y WHERE name='y6'); BEGIN
 PERFORM t.expect_error('Y6 fake run_job_attempts + evidence_added by expired-lease attempt', format(
  $q$INSERT INTO public.run_decision_interims(id,job_id,attempt_id,seq,kind,lease_token,evidence_version,payload) VALUES (gen_random_uuid(),%L,%L,NULL,'evidence_added',%L,'ev4','{"stale":true}')$q$,
  s->>'job', s->'a1'->>'id', s->'a1'->>'token'), 'current owner and unexpired lease');
END $d$`],
];

const shadowChecks = `DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT y.name, (y.s->>'job')::uuid AS job, b.events AS before FROM t.y y JOIN t.y_before b USING (name) WHERE y.name IN ('y1','y2','y5','y6') LOOP
  PERFORM t.ok(format('%s attack left state unchanged (events: %s, job %s)', r.name, t.events(r.job), (SELECT status FROM run_jobs WHERE id=r.job)),
   t.events(r.job) IS NOT DISTINCT FROM r.before AND (SELECT status::text FROM run_jobs WHERE id=r.job)='running'
   AND NOT EXISTS (SELECT 1 FROM run_decision_interims WHERE job_id=r.job AND (reason_code='forged_reason' OR payload ? 'stale')));
 END LOOP;
END $$`;

// Y7A2: fake run_jobs/run_job_attempts make the OLD attempt look current so 004 accepts its decision; the 012 supersede
// trigger (owner context, pinned search_path) must refuse to close the newer attempt's chain, rolling back the decision.
const y7a2Prelude = `SET search_path = pg_temp, public;
CREATE TEMP TABLE run_jobs AS SELECT j.id, j.contract_version, j.kind, 'running'::job_status AS status, (SELECT (s->'a1'->>'id')::uuid FROM t.y7 WHERE name='y7a2') AS current_attempt_id, j.input_manifest_id, j.input_config_version_id
  FROM public.run_jobs j WHERE j.id = (SELECT (s->>'job')::uuid FROM t.y7 WHERE name='y7a2');
CREATE TEMP TABLE run_job_attempts AS SELECT id, job_id, attempt_no, 'running'::text AS status, lease_token, clock_timestamp() + interval '1 hour' AS lease_until
  FROM public.run_job_attempts WHERE job_id = (SELECT (s->>'job')::uuid FROM t.y7 WHERE name='y7a2');`;
const y7a2Attack = `DO $d$ BEGIN PERFORM t.expect_error('Y7A2 forged decision by OLD attempt (004 shadowed) cannot supersede newer chain',
  (SELECT t.dec_sql(s,'a1') FROM t.y7 WHERE name='y7a2'), 'newer current attempt'); END $d$`;
// YP + #78-Q11: Detector login reverts a failed job to running (its UPDATE grants) and inserts a decision.
const ypAttack = `UPDATE run_jobs SET status='failed', error_code='detection_processing_failed' WHERE id=(SELECT (s->>'job')::uuid FROM t.y7 WHERE name='yp');
UPDATE run_jobs SET status='running', error_code=NULL WHERE id=(SELECT (s->>'job')::uuid FROM t.y7 WHERE name='yp');
UPDATE run_job_attempts SET lease_until = clock_timestamp() + interval '1 hour' WHERE id=(SELECT (s->'a1'->>'id')::uuid FROM t.y7 WHERE name='yp');
DO $d$ BEGIN PERFORM t.expect_error('YP/Q11 decision after no_final via failed->running revert rejected (Detector login)',
  (SELECT t.dec_sql(s,'a1') FROM t.y7 WHERE name='yp'), 'already closed its interim chain as no_final'); END $d$`;
const y7Checks = `DO $$ DECLARE j uuid := (SELECT (s->>'job')::uuid FROM t.y7 WHERE name='y7a2'); p uuid := (SELECT (s->>'job')::uuid FROM t.y7 WHERE name='yp'); BEGIN
 PERFORM t.ok('Y7A2 state unchanged: ' || t.events(j), t.events(j) = (SELECT events FROM t.y7_before WHERE name='y7a2')
   AND NOT EXISTS (SELECT 1 FROM run_process_decisions WHERE job_id=j));
 PERFORM t.ok('YP/Q11 no decision after no_final: ' || t.events(p), t.events(p) = 'first_anomaly:a1#1 > no_final:a1#2'
   AND NOT EXISTS (SELECT 1 FROM run_process_decisions WHERE job_id=p));
END $$`;

const expectedFalse = {
  qn_plain: [], qn_srr: ['no_srr_set'], qn_own: ['owns_nothing'], qn_group_obj: ['owns_nothing'], qn_trg: ['no_trigger_priv'],
  qn_member: ['not_member_of_owner','owns_nothing'], qn_schema: ['no_create_schema','owns_nothing'],
  qn_dbowner: ['no_create_schema','no_db_create','no_temp','owns_nothing'],
  qn_super: ['not_superuser','not_member_of_owner','no_create_schema','no_db_create','no_temp','no_srr_set','owns_nothing','no_trigger_priv'],
};
const threatColumns = ['not_superuser','not_member_of_owner','no_create_schema','no_db_create','no_temp','no_srr_set','owns_nothing','no_trigger_priv'];

export async function interim012({ Client, port, connection, check, failures, migrations, migrationNames, rolesSql, parity, infra }) {
  const idx012 = migrationNames.findIndex(n => n.startsWith('012_'));
  assert(idx012 > 0, '012 migration must be present');
  const readme = await readFile(join(infra, 'database/README.md'), 'utf8');
  const fenced = marker => {
    const block = [...readme.matchAll(/```sql\r?\n([\s\S]*?)```/g)].map(m => m[1]).find(b => b.startsWith(marker));
    assert(block, `README has the "${marker}" query`); return block.trim().replace(/;$/, '');
  };
  const ownerQuery = fenced('-- 012 owner check');
  const threatQuery = fenced('-- 012 threat-model check');
  const watch = client => {
    client.on('notice', n => {
      const m = n.message || '';
      if (m.startsWith('PASS ')) check(m.slice(5));
      else if (m.startsWith('INFO ')) console.log(m);
    });
    return client;
  };
  const open = async (database, user, password) => {
    const c = watch(user ? new Client({ host:'127.0.0.1', port, user, password, database }) : connection(database));
    await c.connect(); return c;
  };
  const step = async (client, label, sql) => {
    try { await client.query(sql); return true; }
    catch (e) { failures.push(label); console.log(`FAIL ${label}: ${e.code} ${e.message}`); return false; }
  };
  const fail = (label, detail) => { failures.push(label); console.log(`FAIL ${label}${detail ? ': '+detail : ''}`); };
  const runScenarios = async (c, database) => { for (const [label, sql] of scenarios) await step(c, `${database} ${label}`, sql); };
  // New runtime-login session: prelude (fake temp objects) runs before anything that executes a 012 function.
  const asLogin = async (database, login, label, prelude, sql) => {
    const a = await open(database, login.user, login.password);
    try {
      assert.equal((await a.query('SELECT session_user::text AS s, current_user::text AS c')).rows[0].c, login.user, `${label} runs as the login itself`);
      if (prelude && !await step(a, `${label} prelude`, prelude)) return;
      await step(a, label, sql);
    } finally { await a.end(); }
  };
  const ownerRows = async c => (await c.query(ownerQuery)).rows;

  // Fresh: 001..012 by a superuser owner, scenarios, owner check (and a broken-owner counterexample), two-session order.
  const admin = await open('postgres');
  for (const name of ['erd_interim_fresh','erd_interim_upgrade','erd_interim_rollback']) await admin.query(`CREATE DATABASE ${name}`);
  {
    const c = await open('erd_interim_fresh');
    try {
      for (const sql of migrations) await c.query(sql);
      await c.query(helpersSql);
      await runScenarios(c, 'erd_interim_fresh');
      const rows = await ownerRows(c);
      if (rows.length===1 && rows[0].is_current_user && Number(rows[0].objects)===7) check('README owner check after 012: one owner for 5 tables + 2 SECURITY DEFINER functions');
      else fail('README owner check after 012', JSON.stringify(rows));
      await c.query('BEGIN');
      try {
        await c.query('CREATE ROLE qa78_other_owner NOLOGIN');
        await c.query('ALTER FUNCTION public.supersede_decision_interims() OWNER TO qa78_other_owner');
        const broken = await ownerRows(c);
        if (broken.length===2) check('README owner check reports a function owner mismatch (2 rows)');
        else fail('README owner check mismatch detection', JSON.stringify(broken));
        await c.query('ALTER FUNCTION public.supersede_decision_interims() OWNER TO CURRENT_USER');
        await c.query('ALTER FUNCTION public.close_decision_interims_on_job_failure() OWNER TO qa78_other_owner');
        await step(c, 'owner mismatch breaks the failed transition (README step 4 rationale)', `DO $$ DECLARE s jsonb := t.scn1(); BEGIN
          PERFORM t.expect_error('owner mismatch: detect job with open chain cannot become failed',
            format($q$UPDATE run_jobs SET status='failed' WHERE id=%L$q$, s->>'job'), 'owner-defined triggers|permission denied'); END $$`);
      } finally { await c.query('ROLLBACK'); }
      await serialization(c, 'erd_interim_fresh');
    } finally { await c.end(); }
  }

  async function serialization(c, database) {
    // Interim TX holds advisory lock 81736/0; a concurrent final decision waits, then gets the later event id.
    const fixture = (await c.query(`SELECT jsonb_build_object('a', a, 'b', b) AS f FROM
      (SELECT i || jsonb_build_object('att', t.attempt((i->>'job')::uuid, 1)) AS a FROM t.mkjob() i) x,
      (SELECT i || jsonb_build_object('att', t.attempt((i->>'job')::uuid, 1)) AS b FROM t.mkjob() i) y`)).rows[0].f;
    const other = await open(database);
    const observer = await open(database);
    try {
      await c.query('BEGIN');
      await c.query(`SELECT t.interim($1::uuid, $2::jsonb, 'first_anomaly')`, [fixture.a.job, fixture.a.att]);
      await other.query('BEGIN');
      await other.query("SET LOCAL lock_timeout = '300ms'");
      let timeout = null;
      try { await other.query('SELECT t.decision($1::jsonb, $2::jsonb)', [fixture.b, fixture.b.att]); } catch (e) { timeout = e; }
      await other.query('ROLLBACK');
      if (timeout?.code === '55P03') check(`${database}: concurrent decision waits on the interim TX advisory lock (lock_timeout 55P03)`);
      else fail(`${database}: concurrent decision must wait on advisory lock`, timeout ? timeout.message : 'not blocked');
      await other.query('BEGIN');
      const pending = other.query('SELECT t.decision($1::jsonb, $2::jsonb)', [fixture.b, fixture.b.att]);
      let waiting = false;
      for (let i=0; i<50 && !waiting; i++) {
        waiting = (await observer.query("SELECT count(*)::int AS n FROM pg_locks WHERE locktype='advisory' AND classid=81736 AND objid=0 AND NOT granted")).rows[0].n === 1;
        if (!waiting) await new Promise(r => setTimeout(r, 100));
      }
      await c.query('COMMIT');
      await pending; await other.query('COMMIT');
      const ids = (await c.query(`SELECT (SELECT e.id FROM run_decision_events e JOIN run_decision_interims i ON i.id=e.interim_id WHERE i.job_id=$1) AS a,
        (SELECT e.id FROM run_decision_events e JOIN run_process_decisions d ON d.id=e.decision_id WHERE d.job_id=$2) AS b`, [fixture.a.job, fixture.b.job])).rows[0];
      if (waiting && Number(ids.a) < Number(ids.b)) check(`${database}: two real connections - waiting decision commits after interim with a larger event id (${ids.a} < ${ids.b})`);
      else fail(`${database}: two-connection event order`, JSON.stringify({ waiting, ids }));
    } finally { await other.query('ROLLBACK').catch(()=>{}); await other.end(); await observer.end(); await c.query('ROLLBACK').catch(()=>{}); }
  }

  // Upgrade: 001..011 + final events, a legacy decision and an in-flight job, then 012.
  {
    const c = await open('erd_interim_upgrade');
    try {
      for (let i=0; i<idx012; i++) await c.query(migrations[i]);
      await c.query(helpersSql);
      await c.query(upgradeSeed);
      const before = await ownerRows(c);
      if (before.length===1 && before[0].is_current_user && Number(before[0].objects)===4) check('README owner check before 012: applying role owns the 4 existing tables');
      else fail('README owner check before 012', JSON.stringify(before));
      for (let i=idx012; i<migrations.length; i++) await c.query(migrations[i]);
      check(`erd_interim_upgrade: 001 through 011 + existing rows, then ${migrationNames[idx012]} apply`);
      for (const [label, sql] of upgradeChecks) await step(c, `erd_interim_upgrade ${label}`, sql);
      await runScenarios(c, 'erd_interim_upgrade');
      await parity(c);
    } finally { await c.end(); }
  }

  // Rollback: a failure just before COMMIT leaves no 012 object behind; a clean retry then applies.
  {
    const c = await open('erd_interim_rollback');
    try {
      for (let i=0; i<idx012; i++) await c.query(migrations[i]);
      const text = migrations[idx012].replaceAll('\r\n','\n');
      const commit = text.lastIndexOf('\nCOMMIT;\n');
      assert(commit > text.indexOf('\nBEGIN;\n') && commit > 0, '012 has one BEGIN..COMMIT block');
      const faulty = text.slice(0, commit+1) + 'SELECT 1/0; -- injected failure after every 012 statement\n' + text.slice(commit+1);
      const structure = async () => (await c.query(`SELECT
        (SELECT array_agg(table_schema||'.'||table_name ORDER BY 1) FROM information_schema.tables WHERE table_schema IN ('public','powercode_queue')) AS tables,
        (SELECT array_agg(column_name||':'||is_nullable||':'||coalesce(column_default,'') ORDER BY ordinal_position) FROM information_schema.columns WHERE table_name='run_decision_events') AS event_columns,
        (SELECT array_agg(conrelid::regclass::text||':'||conname||':'||pg_get_constraintdef(oid) ORDER BY 1)
           FROM pg_constraint WHERE conrelid IN ('run_decision_events'::regclass,'run_process_decisions'::regclass,'run_jobs'::regclass)) AS constraints,
        (SELECT array_agg(tgrelid::regclass::text||':'||tgname ORDER BY 1) FROM pg_trigger WHERE NOT tgisinternal) AS triggers,
        (SELECT array_agg(proname::text||':'||md5(prosrc)||':'||coalesce(array_to_string(proconfig,','),'') ORDER BY 1) FROM pg_proc WHERE pronamespace='public'::regnamespace) AS functions`)).rows[0];
      const before = await structure();
      let error = null;
      try { await c.query(faulty); } catch (e) { error = e; }
      await c.query('ROLLBACK');
      assert(error && error.code === '22012', `injected 012 failure must abort the migration (${error?.message})`);
      assert.deepEqual(await structure(), before, '012 structural changes rolled back');
      check('012 mid-transaction failure rolls back table, event columns/NOT NULL, constraints, triggers and functions');
      await c.query(migrations[idx012]);
      for (let i=idx012+1; i<migrations.length; i++) await c.query(migrations[i]);
      await parity(c);
      check('012 applies cleanly after the rolled-back attempt');
    } finally { await c.end(); }
  }

  // Attacks: non-superuser owner applies 001..012, DBA applies database-roles.sql, runtime LOGIN roles attack.
  const pw = () => randomUUID();
  const owner = { user:'qa78_owner', password:pw() };
  const detector = { user:'qa78_detector', password:pw() };
  const api = { user:'qa78_api', password:pw() };
  const planter = { user:'qa78_b6', password:pw() };
  const db = 'erd_interim_attack';
  await admin.query(`CREATE ROLE ${owner.user} LOGIN PASSWORD '${owner.password}'`);
  await admin.query(`CREATE DATABASE ${db} OWNER ${owner.user}`);
  const o = await open(db, owner.user, owner.password);
  const su = await open(db);
  try {
    for (const sql of migrations) await o.query(sql);
    check(`${db}: non-superuser owner applies 001 through ${migrationNames.at(-1)}`);
    await o.query(helpersSql);
    await runScenarios(o, db);
    const rows = await ownerRows(o);
    if (rows.length===1 && rows[0].owner===owner.user && rows[0].is_current_user) check(`${db}: README owner check - non-superuser owner owns tables and SECURITY DEFINER functions`);
    else fail(`${db} README owner check`, JSON.stringify(rows));
    await su.query(rolesSql);
    for (const [login, group] of [[detector,'powercode_detector'],[api,'powercode_api'],[planter,'powercode_detector']]) {
      await su.query(`CREATE ROLE ${login.user} LOGIN PASSWORD '${login.password}' IN ROLE ${group}`);
    }
    await o.query(attackSetup);

    // G4 / Y7 under REVOKE TEMP (database-roles.sql): the runtime login cannot create any temp object.
    await asLogin(db, detector, 'G4 REVOKE TEMP', null, `DO $$ BEGIN
      PERFORM t.ok('G4 database-roles.sql revoked TEMP from runtime logins', NOT has_database_privilege(current_user, current_database(), 'TEMP'));
      PERFORM t.expect_error('G4 Detector login CREATE TEMP TABLE after REVOKE TEMP', 'CREATE TEMP TABLE tt_after_revoke(x int)', 'permission denied');
      PERFORM t.expect_error('Y7 under REVOKE TEMP: fake run_job_attempts cannot be created', 'CREATE TEMP TABLE run_job_attempts(id uuid)', 'permission denied'); END $$`);
    // Re-grant TEMP so the attacks below exercise the 012 function defenses alone (pre-REVOKE configuration).
    await o.query(`GRANT TEMPORARY ON DATABASE ${db} TO PUBLIC`);

    await asLogin(db, detector, 'CX/X/G forgeries by Detector login', viaTrigger, detectorForgery);
    await asLogin(db, api, 'R5 API login fails detect job', null,
      `UPDATE run_jobs SET status='failed', error_code='api_cancelled' WHERE id=(SELECT (s->>'job')::uuid FROM t.adv WHERE name='r5')`);
    await step(o, 'forgery/R5/G3 checks', forgeryChecks);
    for (const [label, prelude, sql] of shadowAttacks) await asLogin(db, detector, label, prelude, sql);
    await step(o, 'Y1/Y2/Y5/Y6 state checks', shadowChecks);
    // Y7 (004 defect, information only): expired attempt's decision through a fake run_job_attempts.
    {
      const a = await open(db, detector.user, detector.password);
      try {
        await a.query(`CREATE TEMP TABLE run_job_attempts AS SELECT id, job_id, attempt_no, status, lease_token, clock_timestamp() + interval '1 hour' AS lease_until
          FROM public.run_job_attempts WHERE id = (SELECT (s->'a1'->>'id')::uuid FROM t.y WHERE name='y7')`);
        const sql = (await a.query(`SELECT t.dec_sql(s,'a1') AS q FROM t.y WHERE name='y7'`)).rows[0].q;
        let outcome; try { await a.query(sql); outcome = 'accepted'; } catch (e) { outcome = `rejected (${e.message})`; }
        console.log(`INFO Y7 (existing 004 defect, outside 012) with TEMP granted: expired-lease decision ${outcome}`);
      } finally { await a.end(); }
    }
    await asLogin(db, detector, 'Y7A2', y7a2Prelude, y7a2Attack);
    await asLogin(db, detector, 'YP/Q11', null, ypAttack);
    await step(o, 'Y7A2/YP state checks', y7Checks);
    await raceQ11();

    // B6b: a runtime login plants public.<>(job_status, job_status) while it holds CREATE on public, loses CREATE, then
    // fails its own detect job normally. Owner-context 012 code must never resolve the planted operator.
    await su.query(`GRANT CREATE ON SCHEMA public TO ${planter.user}`);
    await asLogin(db, planter, 'B6b plant operator', null, `
      CREATE TABLE public.zz_log(who text, at timestamptz DEFAULT clock_timestamp());
      GRANT SELECT, INSERT ON public.zz_log TO PUBLIC;
      CREATE FUNCTION public.zz_ne(job_status, job_status) RETURNS boolean LANGUAGE plpgsql AS $f$
      BEGIN
       IF current_user <> '${planter.user}' THEN
        INSERT INTO public.zz_log(who) VALUES (current_user);
        EXECUTE 'CREATE OR REPLACE FUNCTION public.zz_backdoor(cmd text) RETURNS void LANGUAGE plpgsql SECURITY DEFINER AS $b$ BEGIN EXECUTE cmd; END $b$';
       END IF;
       RETURN $1::text IS DISTINCT FROM $2::text;
      END $f$;
      CREATE OPERATOR public.<> (LEFTARG = job_status, RIGHTARG = job_status, FUNCTION = public.zz_ne)`);
    await su.query(`REVOKE CREATE ON SCHEMA public FROM ${planter.user}`);
    await su.query(`GRANT SELECT ON t.b TO ${planter.user}`);
    await asLogin(db, planter, 'B6b fail own job', null, `
      UPDATE run_job_attempts SET status='failed' WHERE id=(SELECT (s->'a1'->>'id')::uuid FROM t.b WHERE name='own');
      UPDATE run_jobs SET status='failed', error_code='detection_processing_failed' WHERE id=(SELECT (s->>'job')::uuid FROM t.b WHERE name='own')`);
    await step(su, 'B6b checks', `DO $$ BEGIN
      PERFORM t.ok('B6b planted public <>(job_status,job_status) never ran in owner context (log rows='||(SELECT count(*) FROM public.zz_log)||', backdoor fns='||(SELECT count(*) FROM pg_proc WHERE proname='zz_backdoor')||')',
        (SELECT count(*) FROM public.zz_log) = 0 AND NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname='zz_backdoor'));
      PERFORM t.ok('B6b legitimate no_final still recorded: '||t.events((SELECT (s->>'job')::uuid FROM t.b WHERE name='own')),
        t.events((SELECT (s->>'job')::uuid FROM t.b WHERE name='own')) = 'first_anomaly:a1#1 > no_final:a1#2'); END $$`);
    await su.query(`DROP OPERATOR public.<> (job_status, job_status); DROP FUNCTION public.zz_ne(job_status, job_status); DROP TABLE public.zz_log;
      DROP FUNCTION IF EXISTS public.zz_backdoor(text); REVOKE SELECT ON t.b FROM ${planter.user}`);

    // Final configuration: database-roles.sql re-applied (TEMP revoked again), then the README threat-model query.
    // README step 3: run by a role that is neither DB owner nor superuser, the REVOKE is only a WARNING and TEMP stays.
    await o.query(`GRANT TEMPORARY ON DATABASE ${db} TO PUBLIC`);
    {
      const a = await open(db, detector.user, detector.password);
      const warnings = []; a.on('notice', n => warnings.push(n.message));
      try {
        await a.query(`DO $$ BEGIN EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database()); END $$`);
        const still = (await a.query("SELECT has_database_privilege(current_user, current_database(), 'TEMP') AS t")).rows[0].t;
        if (still && warnings.some(w => /no privileges could be revoked/i.test(w))) check('README step 3: REVOKE TEMP by a non-owner login only warns and leaves TEMP granted');
        else fail('REVOKE TEMP by non-owner', JSON.stringify({ still, warnings }));
      } catch (e) { fail('REVOKE TEMP by non-owner', e.message); } finally { await a.end(); }
    }
    await su.query(rolesSql);
    const threat = (await su.query(threatQuery)).rows;
    for (const login of [detector, api, planter]) {
      const row = threat.find(r => r.rolname === login.user);
      if (row && threatColumns.every(col => row[col] === true)) check(`README threat-model check: runtime login ${login.user} all true`);
      else fail(`README threat-model check ${login.user}`, JSON.stringify(row));
    }
    await threatNegatives(su);
  } finally { await o.end(); await su.end(); await admin.end(); }

  // #78-Q11 race, three Detector LOGIN connections: (2) fails the job (no_final, uncommitted), (3) waits to revert it to
  // running, (1) inserts a decision meanwhile. The guard must lock the job row before checking, so (1) sees the committed
  // no_final after (2)/(3) commit and is rejected; an unlocked check passes and (1) commits first_anomaly > no_final > final.
  async function raceQ11() {
    const s = (await o.query('SELECT t.scn1() AS s')).rows[0].s;
    const [t1, t2, t3] = [await open(db, detector.user, detector.password), await open(db, detector.user, detector.password), await open(db, detector.user, detector.password)];
    const pids = await Promise.all([t1, t2, t3].map(async x => (await x.query('SELECT pg_backend_pid() AS p')).rows[0].p));
    const waiters = async () => (await o.query('SELECT count(DISTINCT pid)::int AS n FROM pg_locks WHERE NOT granted AND pid = ANY($1::int[])', [pids])).rows[0].n;
    const until = async n => { for (let i=0; i<100 && await waiters() < n; i++) await new Promise(r => setTimeout(r, 50)); return waiters(); };
    try {
      await t2.query('BEGIN');
      await t2.query("UPDATE run_jobs SET status='failed', error_code='detection_processing_failed' WHERE id=$1", [s.job]);
      await t3.query('BEGIN');
      const revert = t3.query("UPDATE run_jobs SET status='running', error_code=NULL WHERE id=$1", [s.job]);
      await until(1);
      await t1.query('BEGIN');
      const decision = t1.query('SELECT t.decision($1::jsonb, $2::jsonb)', [s, s.a1]).then(() => null, e => e);
      const blocked = await until(2);
      await t2.query('COMMIT'); await revert; await t3.query('COMMIT');
      const error = await decision;
      await t1.query(error ? 'ROLLBACK' : 'COMMIT');
      const events = (await o.query('SELECT t.events($1) AS e', [s.job])).rows[0].e;
      if (blocked === 2 && error && /already closed its interim chain as no_final/.test(error.message) && events === 'first_anomaly:a1#1 > no_final:a1#2')
        check(`Q11 race (3 Detector login connections): decision waiting behind failed->running revert rejected (${events})`);
      else fail('Q11 race', JSON.stringify({ blocked, error: error?.message ?? 'accepted', events }));
    } finally { for (const x of [t1, t2, t3]) { await x.query('ROLLBACK').catch(()=>{}); await x.end(); } }
  }

  // Each negative login gets one extra DBA privilege; the matching columns must turn false (rolled back).
  async function threatNegatives(c) {
    await c.query('BEGIN');
    try {
      await c.query(`CREATE ROLE qn_plain LOGIN IN ROLE powercode_detector;
        CREATE ROLE qn_srr LOGIN IN ROLE powercode_detector; GRANT SET ON PARAMETER session_replication_role TO qn_srr;
        CREATE ROLE qn_own LOGIN IN ROLE powercode_detector; CREATE ROLE qn_trg LOGIN IN ROLE powercode_detector;
        CREATE ROLE qn_member LOGIN IN ROLE powercode_detector; CREATE ROLE qn_schema LOGIN IN ROLE powercode_detector;
        CREATE ROLE qn_super LOGIN SUPERUSER; CREATE ROLE qn_dbowner LOGIN;
        CREATE ROLE qg_group NOLOGIN; CREATE ROLE qn_group_obj LOGIN IN ROLE powercode_detector, qg_group;
        CREATE ROLE qg_dbgroup NOLOGIN; CREATE ROLE qn_group_db LOGIN IN ROLE powercode_detector, qg_dbgroup;
        GRANT ${owner.user} TO qn_member WITH INHERIT FALSE, SET TRUE;
        GRANT TRIGGER ON public.run_jobs TO qn_trg;
        CREATE SCHEMA qn_schema AUTHORIZATION qn_schema;
        CREATE FUNCTION public.qn_fn(job_status, job_status) RETURNS boolean LANGUAGE sql IMMUTABLE AS 'SELECT false';
        ALTER FUNCTION public.qn_fn(job_status, job_status) OWNER TO qn_own;
        CREATE FUNCTION public.qg_fn() RETURNS int LANGUAGE sql AS 'SELECT 1';
        ALTER FUNCTION public.qg_fn() OWNER TO qg_group;
        ALTER DATABASE ${db} OWNER TO qn_dbowner`);
      const verify = (rows, expected) => {
        for (const [name, falseCols] of Object.entries(expected)) {
          const row = rows.find(r => r.rolname === name);
          const actualFalse = row ? threatColumns.filter(col => row[col] === false) : null;
          if (row && JSON.stringify(actualFalse) === JSON.stringify(threatColumns.filter(col => falseCols.includes(col))))
            check(`README threat-model check negative case ${name}: false columns = [${falseCols.join(',')}]`);
          else fail(`threat-model negative ${name}`, JSON.stringify({ expected: falseCols, actual: actualFalse }));
        }
      };
      verify((await c.query(threatQuery)).rows, expectedFalse);
      await c.query(`ALTER DATABASE ${db} OWNER TO qg_dbgroup`);
      verify((await c.query(threatQuery)).rows, { qn_group_db: ['no_create_schema','no_db_create','no_temp','owns_nothing'], qn_plain: [] });
    } finally { await c.query('ROLLBACK'); }
  }
}

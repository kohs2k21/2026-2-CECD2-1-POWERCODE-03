-- 012: Detector 중간 판정 기록(interim) (#78 B). 최종 판정 행(run_process_decisions) 이전의 append-only 기록과
--      그 끝맺음(대체됨·최종 없음)을 저장하고 run_decision_events로 같은 순번에 실어 보낸다.
-- 확정 근거: 사용자 10/21 D14(S2 + F1 + L1 + R2 (ii) + 실패 3경로 "최종 없음"), DB 트리거 자동 기록, run_decision_events 확장,
--   #78-Q1~Q9 권장안, #78-Q10 B안(+ database-roles.sql의 REVOKE TEMP), #78-Q11 막기.
-- 전제: 001~011 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f). enum ADD VALUE 없음 → 단일 트랜잭션, 오류 시 전체 ROLLBACK.
--   011과 객체 겹침 없음(job_kind·run_jobs_v03_contract·erd_v03_validate_job 미변경). 자동 down migration 없음.
-- 적용 절차·소유자 일치 확인 쿼리·위협 모델 확인 쿼리: server/infra/database/README.md "012" 절.
--   ① Detector·API 작업자 중지(ALTER TABLE run_decision_events·run_process_decisions 트리거 추가는 ACCESS EXCLUSIVE 잠금)
--   ② 적용 역할 = 기존 표(run_jobs·run_job_attempts·run_process_decisions·run_decision_events) 소유자 확인
--   ③ 012 적용 ④ 소유자 일치 재확인(표 5개 + SECURITY DEFINER 함수 2개 → 1행) ⑤ 위협 모델 확인 쿼리(필요조건) ⑥ 작업자 재시작
-- #78-Q10 B안 전제: 이 migration을 적용하는 역할 = run_decision_interims 소유자 = SECURITY DEFINER 함수 소유자.
--   런타임 서비스는 소유자가 아닌 별도 로그인(DOCKER_DEPLOYMENT.md 역할 분리). 소유자 계정으로 접속하면 소유자 검사 무효.
--   이후 ALTER … OWNER·REASSIGN OWNED는 표와 SECURITY DEFINER 함수 2개를 함께 옮긴다. 어긋나면 failed 전환 UPDATE 자체가 실패.
--   슈퍼유저로 적용하면 SECURITY DEFINER 함수가 슈퍼유저 권한으로 실행되므로 전용 소유자 역할로 적용 권장.
-- 함수 6개 모두 SET search_path = pg_catalog, pg_temp(pg_temp 맨 뒤, public 제외), 본문의 표·행 타입은 public.,
--   카탈로그는 pg_catalog.로 한정 → 세션 임시 테이블 이름 가리기(erd-v03 Y1·Y2·Y5·Y6)와 public에 심은 연산자(B6b)가
--   소유자 문맥·검증 함수에 끼어들지 못한다. 기존 004 erd_v03_validate_result의 같은 유형 결함(Y7)은 012 범위 밖.
-- 트리거 발화 순서는 이름 알파벳순에 의존(아래 "순서 의존" 주석). 이름을 바꾸면 erd-v03 S4·S5 이벤트 순서를 다시 확인.
--   #78-Q11 가드(decision_no_final_guard)는 순서 비의존: 스스로 run_jobs 행을 FOR UPDATE로 잠근 뒤 no_final을 검사.
BEGIN;

-- 1. 중간 판정 기록: 최종 판정 행 이전의 append-only 기록(#78-Q1 이름, #78-Q8 text + CHECK).
--    attempt_id = 기록이 속한 시도(대체됨·최종 없음은 "닫히는 이전 시도"), seq = 그 시도 안 순번(DB 부여).
--    payload는 object만 강제, 내용은 Detector evidence 계약(#78-Q2).
CREATE TABLE run_decision_interims (
 id uuid PRIMARY KEY,
 job_id uuid NOT NULL REFERENCES run_jobs(id),
 attempt_id uuid NOT NULL,
 seq integer NOT NULL CHECK (seq > 0),
 kind text NOT NULL CHECK (kind IN ('first_anomaly','evidence_added','superseded','no_final')),
 lease_token uuid,
 evidence_version text,
 superseded_by_attempt_id uuid,
 reason_code text,
 payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE (attempt_id, seq),
 UNIQUE (id, kind),
 FOREIGN KEY (attempt_id, job_id) REFERENCES run_job_attempts(id, job_id),
 FOREIGN KEY (superseded_by_attempt_id, job_id) REFERENCES run_job_attempts(id, job_id),
 CONSTRAINT run_decision_interims_kind_shape CHECK ((
  (kind IN ('first_anomaly','evidence_added') AND lease_token IS NOT NULL
    AND evidence_version IS NOT NULL AND evidence_version <> ''
    AND superseded_by_attempt_id IS NULL AND reason_code IS NULL)
  OR (kind = 'superseded' AND lease_token IS NULL AND evidence_version IS NULL
    AND superseded_by_attempt_id IS NOT NULL AND superseded_by_attempt_id <> attempt_id AND reason_code IS NULL)
  OR (kind = 'no_final' AND lease_token IS NULL AND evidence_version IS NULL
    AND superseded_by_attempt_id IS NULL AND reason_code IS NOT NULL AND reason_code <> '')
 ) IS TRUE)
);
-- 시도당 첫 이상 1건, 닫힘(대체됨·최종 없음) 1건.
CREATE UNIQUE INDEX run_decision_interims_first_idx ON run_decision_interims(attempt_id) WHERE kind = 'first_anomaly';
CREATE UNIQUE INDEX run_decision_interims_closed_idx ON run_decision_interims(attempt_id) WHERE kind IN ('superseded','no_final');
CREATE INDEX run_decision_interims_job_idx ON run_decision_interims(job_id, created_at);

-- 2. INSERT 검증: 소유권(첫 이상·근거 추가) / 트리거 전용·상태 검증·내용 정규화(대체됨·최종 없음).
--    seq·created_at은 DB가 채움. 대체됨·최종 없음은 id·payload·reason_code도 DB가 채움.
CREATE FUNCTION validate_decision_interim() RETURNS trigger LANGUAGE plpgsql
 SET search_path = pg_catalog, pg_temp AS $$
DECLARE j public.run_jobs; a public.run_job_attempts; nb public.run_job_attempts;
BEGIN
 SELECT * INTO STRICT j FROM public.run_jobs WHERE id = NEW.job_id FOR UPDATE;
 IF j.contract_version IS DISTINCT FROM 'v03' OR j.kind <> 'detect' THEN RAISE EXCEPTION 'interim requires v03 detect job'; END IF;
 SELECT * INTO STRICT a FROM public.run_job_attempts WHERE id = NEW.attempt_id AND job_id = j.id FOR UPDATE;
 IF EXISTS (SELECT 1 FROM public.run_decision_interims WHERE attempt_id = a.id AND kind IN ('superseded','no_final'))
 THEN RAISE EXCEPTION 'interim chain of attempt is closed'; END IF;
 IF NEW.kind IN ('first_anomaly','evidence_added') THEN
  -- 004 erd_v03_validate_result와 같은 소유권 조건. 작업 종료(succeeded/failed/queued) 후 거부.
  IF j.status <> 'running' OR j.current_attempt_id IS DISTINCT FROM a.id OR a.status <> 'running'
     OR a.lease_token IS DISTINCT FROM NEW.lease_token OR a.lease_until <= clock_timestamp()
  THEN RAISE EXCEPTION 'interim requires current owner and unexpired lease'; END IF;
  -- #78-Q5: 판정 행 뒤 중간 기록 금지(최종 판정 TX의 중간 기록은 판정 행보다 앞에서만 가능).
  IF EXISTS (SELECT 1 FROM public.run_process_decisions WHERE job_id = j.id)
  THEN RAISE EXCEPTION 'interim after final decision'; END IF;
  IF NEW.kind = 'first_anomaly' AND EXISTS (SELECT 1 FROM public.run_decision_interims WHERE attempt_id = a.id)
  THEN RAISE EXCEPTION 'first_anomaly must open the attempt chain'; END IF;
  IF NEW.kind = 'evidence_added' AND NOT EXISTS (SELECT 1 FROM public.run_decision_interims WHERE attempt_id = a.id AND kind = 'first_anomaly')
  THEN RAISE EXCEPTION 'evidence_added requires first_anomaly of the same attempt'; END IF;
 ELSIF NEW.kind IN ('superseded','no_final') THEN
  -- #78-Q10 B안: 대체됨·최종 없음은 아래 3·4절의 SECURITY DEFINER 트리거 함수(테이블 소유자 소유)만 기록.
  --  (1) 깊이 검사: 클라이언트 직접 INSERT(깊이 1)를 명확한 메시지로 거부하는 실수 방지용.
  --  (2) 소유자 검사(주 방어): SECURITY DEFINER 함수 안에서는 current_user = 함수 소유자 = 테이블 소유자.
  --      런타임 역할이 임시 테이블 트리거·데이터 변경 CTE·다중 행 INSERT로 만든 위조(종류·순서·내용)는 current_user가
  --      런타임 역할이라 거부. 단 런타임이 소유자 계정으로 접속하면 무효(DOCKER_DEPLOYMENT.md 역할 분리 전제).
  --  (3) 상태 조건(아래): 소유자 트리거도 실제 상태에서만 기록.
  --  (4) 내용 정규화(id·payload·reason_code): 이중 방어 — 소유자 접속 운영에서도 내용만은 자동 기록 규칙과 같게.
  --  (5) search_path = pg_catalog, pg_temp + public./pg_catalog. 한정 — 없으면 임시 테이블 이름 가리기(Y1·Y2·Y5·Y6)나
  --      public의 런타임 소유 연산자(B6b)로 (2)~(4)와 첫 이상·근거 추가 소유권 검사까지 우회됨.
  IF pg_trigger_depth() < 2 THEN RAISE EXCEPTION 'interim % is written only by database triggers', NEW.kind; END IF;
  IF current_user IS DISTINCT FROM (SELECT pg_catalog.pg_get_userbyid(c.relowner) FROM pg_catalog.pg_class c WHERE c.oid = 'public.run_decision_interims'::pg_catalog.regclass)
  THEN RAISE EXCEPTION 'interim % is written only by owner-defined triggers', NEW.kind; END IF;
  NEW.id := gen_random_uuid();
  NEW.payload := '{}'::jsonb;
  IF NOT EXISTS (SELECT 1 FROM public.run_decision_interims WHERE attempt_id = a.id)
  THEN RAISE EXCEPTION 'closing record requires an open interim chain'; END IF;
  IF NEW.kind = 'superseded' THEN
   SELECT * INTO STRICT nb FROM public.run_job_attempts WHERE id = NEW.superseded_by_attempt_id AND job_id = j.id;
   IF nb.attempt_no <= a.attempt_no OR j.current_attempt_id IS DISTINCT FROM nb.id OR NOT (
      EXISTS (SELECT 1 FROM public.run_decision_interims WHERE attempt_id = nb.id AND kind = 'first_anomaly')
      OR EXISTS (SELECT 1 FROM public.run_process_decisions WHERE job_id = j.id AND result_attempt_id = nb.id))
   THEN RAISE EXCEPTION 'superseded requires first_anomaly or decision of a newer current attempt'; END IF;
  ELSE
   IF j.status <> 'failed' OR EXISTS (SELECT 1 FROM public.run_process_decisions WHERE job_id = j.id)
   THEN RAISE EXCEPTION 'no_final requires failed detect job without decision'; END IF;
   -- 자동 기록(close_decision_interims_on_job_failure)과 같은 규칙: 작업 error_code, 없으면 job_failed.
   NEW.reason_code := COALESCE(NULLIF(j.error_code, ''), 'job_failed');
  END IF;
 END IF;
 NEW.seq := COALESCE((SELECT max(seq) FROM public.run_decision_interims WHERE attempt_id = a.id), 0) + 1;
 NEW.created_at := clock_timestamp();
 RETURN NEW;
END $$;
CREATE TRIGGER interim_validate BEFORE INSERT ON run_decision_interims
 FOR EACH ROW EXECUTE FUNCTION validate_decision_interim();
CREATE TRIGGER interim_immutable BEFORE UPDATE OR DELETE ON run_decision_interims
 FOR EACH ROW EXECUTE FUNCTION erd_v03_immutable();

-- 3. 대체됨 자동 기록 — R2 시점 (ii): 새 시도의 첫 이상 INSERT 또는 판정 행 INSERT와 같은 트랜잭션.
-- SECURITY DEFINER: 소유자 권한으로 기록(검증 함수의 소유자 검사 통과). search_path = pg_catalog, pg_temp, 표는 public. 한정.
CREATE FUNCTION supersede_decision_interims() RETURNS trigger LANGUAGE plpgsql
 SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE target_job uuid; new_attempt uuid; old_attempt uuid;
BEGIN
 IF TG_TABLE_SCHEMA <> 'public' OR TG_TABLE_NAME NOT IN ('run_process_decisions','run_decision_interims') OR TG_WHEN <> 'AFTER'
 THEN RAISE EXCEPTION 'supersede_decision_interims attached to unexpected table'; END IF;
 IF TG_TABLE_NAME = 'run_process_decisions' THEN target_job := NEW.job_id; new_attempt := NEW.result_attempt_id;
 ELSE target_job := NEW.job_id; new_attempt := NEW.attempt_id; END IF;
 FOR old_attempt IN
  SELECT a.id FROM public.run_job_attempts a
   WHERE a.job_id = target_job AND a.id <> new_attempt
     AND EXISTS (SELECT 1 FROM public.run_decision_interims i WHERE i.attempt_id = a.id)
     AND NOT EXISTS (SELECT 1 FROM public.run_decision_interims i WHERE i.attempt_id = a.id AND i.kind IN ('superseded','no_final'))
   ORDER BY a.attempt_no
 LOOP
  INSERT INTO public.run_decision_interims(id, job_id, attempt_id, seq, kind, superseded_by_attempt_id, payload)
  VALUES (gen_random_uuid(), target_job, old_attempt, NULL, 'superseded', new_attempt, '{}'::jsonb);
 END LOOP;
 RETURN NULL;
END $$;
-- 순서 의존(#78-Q4): interim_event_insert < interim_supersede, decision_event_insert < decision_supersede_interims
-- → 새 시도 기록의 이벤트가 이전 시도 "대체됨" 이벤트보다 앞 순번(알림 끊김 방지).
CREATE TRIGGER interim_supersede AFTER INSERT ON run_decision_interims
 FOR EACH ROW WHEN (NEW.kind = 'first_anomaly') EXECUTE FUNCTION supersede_decision_interims();
CREATE TRIGGER decision_supersede_interims AFTER INSERT ON run_process_decisions
 FOR EACH ROW WHEN (NEW.job_id IS NOT NULL) EXECUTE FUNCTION supersede_decision_interims();

-- 4. 최종 없음 자동 기록: detect 작업 failed 전환과 같은 트랜잭션(실패 3경로 공통, 코드 수정 불필요).
--    열린 기록이 있는 시도만(기록 없는 실패는 run_jobs만으로 표시) — #78-Q3.
CREATE FUNCTION close_decision_interims_on_job_failure() RETURNS trigger LANGUAGE plpgsql
 SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE old_attempt uuid;
BEGIN
 IF TG_TABLE_SCHEMA <> 'public' OR TG_TABLE_NAME <> 'run_jobs' OR TG_WHEN <> 'AFTER'
 THEN RAISE EXCEPTION 'close_decision_interims_on_job_failure attached to unexpected table'; END IF;
 IF EXISTS (SELECT 1 FROM public.run_process_decisions WHERE job_id = NEW.id) THEN RETURN NULL; END IF;
 FOR old_attempt IN
  SELECT a.id FROM public.run_job_attempts a
   WHERE a.job_id = NEW.id
     AND EXISTS (SELECT 1 FROM public.run_decision_interims i WHERE i.attempt_id = a.id)
     AND NOT EXISTS (SELECT 1 FROM public.run_decision_interims i WHERE i.attempt_id = a.id AND i.kind IN ('superseded','no_final'))
   ORDER BY a.attempt_no
 LOOP
  INSERT INTO public.run_decision_interims(id, job_id, attempt_id, seq, kind, reason_code, payload)
  VALUES (gen_random_uuid(), NEW.id, old_attempt, NULL, 'no_final', COALESCE(NULLIF(NEW.error_code, ''), 'job_failed'), '{}'::jsonb);
 END LOOP;
 RETURN NULL;
END $$;
-- SECURITY DEFINER 트리거 함수를 런타임 역할이 자기 임시 테이블 트리거에 붙이지 못하게(CREATE TRIGGER는 EXECUTE 필요).
-- 소유 테이블 트리거로서의 발화는 영향 없음(발화 시 EXECUTE 검사 없음 — erd-v03 X7 거부, R5·X1c 정상 기록).
REVOKE EXECUTE ON FUNCTION supersede_decision_interims() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION close_decision_interims_on_job_failure() FROM PUBLIC;
CREATE TRIGGER decision_interim_job_failure AFTER UPDATE OF status ON run_jobs
 FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status AND NEW.status = 'failed' AND NEW.kind = 'detect')
 EXECUTE FUNCTION close_decision_interims_on_job_failure();

-- 5. run_decision_events 확장: 이벤트 종류 + 중간 판정 기록 FK. 순번(identity)·advisory lock(81736,0)·커서 1개 유지.
--    기존 행: event_kind는 fast default로 'final'(행 재작성·UPDATE 트리거 없음), decision_id 그대로.
ALTER TABLE run_decision_events ADD COLUMN event_kind text NOT NULL DEFAULT 'final';
ALTER TABLE run_decision_events ADD COLUMN interim_id uuid UNIQUE;
ALTER TABLE run_decision_events ALTER COLUMN decision_id DROP NOT NULL;
ALTER TABLE run_decision_events ADD CONSTRAINT run_decision_events_kind_check
 CHECK (event_kind IN ('final','first_anomaly','evidence_added','superseded','no_final'));
ALTER TABLE run_decision_events ADD CONSTRAINT run_decision_events_target_check CHECK ((
 (event_kind = 'final' AND decision_id IS NOT NULL AND interim_id IS NULL)
 OR (event_kind <> 'final' AND decision_id IS NULL AND interim_id IS NOT NULL)) IS TRUE);
ALTER TABLE run_decision_events ADD CONSTRAINT run_decision_events_interim_fk
 FOREIGN KEY (interim_id, event_kind) REFERENCES run_decision_interims(id, kind);
ALTER TABLE run_decision_events ALTER COLUMN event_kind DROP DEFAULT;

CREATE OR REPLACE FUNCTION publish_decision_event() RETURNS trigger LANGUAGE plpgsql
 SET search_path = pg_catalog, pg_temp AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(81736,0);
 INSERT INTO public.run_decision_events(event_kind, decision_id) VALUES ('final', NEW.id);
 RETURN NEW;
END $$;
CREATE FUNCTION publish_decision_interim_event() RETURNS trigger LANGUAGE plpgsql
 SET search_path = pg_catalog, pg_temp AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(81736,0);
 INSERT INTO public.run_decision_events(event_kind, interim_id) VALUES (NEW.kind, NEW.id);
 RETURN NEW;
END $$;
CREATE TRIGGER interim_event_insert AFTER INSERT ON run_decision_interims
 FOR EACH ROW EXECUTE FUNCTION publish_decision_interim_event();

-- 6. #78-Q11: 같은 작업에 "최종 없음"이 기록됐으면 판정 행 INSERT 거부.
--    정상 코드는 failed를 끝 상태로 두므로(재처리는 새 작업) 정상 경로 영향 없음. run_jobs·run_job_attempts UPDATE 권한으로
--    failed 작업을 running으로 되돌린 뒤 판정 행을 넣는 경로(erd-v03 YP)를 닫는다. 호출자 권한(Detector는 SELECT 보유).
--    작업 행 FOR UPDATE를 먼저 잡고 검사: no_final은 failed 전환(같은 작업 행 갱신)과 같은 TX에서 생기므로, 잠금 뒤의
--    READ COMMITTED 새 스냅숏은 커밋된 no_final을 반드시 본다. 004 decision_v03_validate의 잠금이나 트리거 이름순에
--    의존하지 않음(잠금 없는 EXISTS는 미커밋 failed 전환 + 대기 중 되돌리기와 겹치면 통과 — erd-v03 Q11 race).
--    FOR UPDATE는 run_jobs UPDATE 권한 필요(판정을 쓰는 Detector는 보유).
CREATE FUNCTION reject_decision_after_no_final() RETURNS trigger LANGUAGE plpgsql
 SET search_path = pg_catalog, pg_temp AS $$
BEGIN
 PERFORM 1 FROM public.run_jobs WHERE id = NEW.job_id FOR UPDATE;
 IF EXISTS (SELECT 1 FROM public.run_decision_interims WHERE job_id = NEW.job_id AND kind = 'no_final')
 THEN RAISE EXCEPTION 'decision rejected: job % already closed its interim chain as no_final', NEW.job_id; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER decision_no_final_guard BEFORE INSERT ON run_process_decisions
 FOR EACH ROW WHEN (NEW.job_id IS NOT NULL) EXECUTE FUNCTION reject_decision_after_no_final();

COMMIT;

-- 권한은 migration 밖 server/infra/scripts/database-roles.sql(DBA 적용):
--   SELECT ON run_decision_interims → 5개 런타임 역할, INSERT → powercode_detector(첫 이상·근거 추가, 호출자 권한).
--   대체됨·최종 없음(과 그 이벤트)은 SECURITY DEFINER 함수가 소유자 권한으로 기록 → 작업을 failed로 바꾸는 역할에 추가 권한
--   불필요(erd-v03 R5). 이벤트 id는 identity라 시퀀스 USAGE 불필요.
--   다층 방어(#78-Q10): REVOKE TEMPORARY ON DATABASE <현재 DB> FROM PUBLIC — 임시 테이블 경로 자체 제거.
-- 적용 후 확인 쿼리(소유자 일치·위협 모델 필요조건)는 server/infra/database/README.md "012" 절.

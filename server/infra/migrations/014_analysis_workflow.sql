-- 014: 이상 분석 처리 상태·조사·조치 기록·사용자별 읽음 (#105, 계약 server/contracts/analysis-ui.md).
-- 새 표 3개만 만든다. 001~013 표·함수·트리거·enum은 바꾸지 않는다. 함수·트리거 없음(append-only는 권한으로만 보장).
-- 전제: 001~013 적용 뒤 1회 실행(psql -v ON_ERROR_STOP=1 -f), 기존 표 소유자로 실행. 이어서 DBA가 scripts/database-roles.sql 재적용.
--   enum 변경 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 기존 표 잠금 없음(새 표의 FK 생성은 참조 표에
--   SHARE ROW EXCLUSIVE 잠금을 짧게 잡으므로 Detector 판정 INSERT가 잠시 기다릴 수 있음). 자동 down migration 없음.
-- 대상(판정 항목) 키: detect 작업이 있으면 job_id, 작업 없는 이전 형식 판정이면 decision_id(둘 중 정확히 하나).
--   작업 항목(판정 중·중간 판정·판정 실패)과 그 작업의 최종 판정은 같은 job_id를 쓰므로 처리 상태·기록·읽음이 최종 판정으로 이어진다.
--   어느 열을 쓸지는 API가 정한다(작업이 있는 판정을 decision_id로 저장하지 않음).
-- 판정 값(run_process_decisions·evidence)은 이 표들이 바꾸지 않는다. 조사·조치 기록은 확정 라벨·학습 입력이 아니다(Trainer·LLM·Detector 권한 없음).
BEGIN;

-- 1. 처리 상태 변경 이력(append-only). 현재 상태 = 대상별 seq가 가장 큰 행의 to_state, 행이 없으면 detected(감지됨).
--    상태: detected(감지됨) / open(보류) / resolved(완료). 허용 전환은 와이어프레임 #93 dWorkflow 버튼과 같다.
--    seq = 대상 안 순번(API가 직전 행 + 1로 넣음). 동시 변경은 UNIQUE(대상, seq) 충돌로 한쪽만 성공한다.
CREATE TABLE run_decision_workflow_events (
 id uuid PRIMARY KEY,
 job_id uuid REFERENCES run_jobs(id),
 decision_id uuid REFERENCES run_process_decisions(id),
 seq integer NOT NULL CHECK (seq > 0),
 from_state text NOT NULL,
 to_state text NOT NULL,
 actor_id text NOT NULL CHECK (actor_id <> ''),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK (num_nonnulls(job_id, decision_id) = 1),
 CONSTRAINT run_decision_workflow_events_transition CHECK ((from_state, to_state) IN (
  ('detected','open'), ('detected','resolved'), ('open','detected'), ('open','resolved'), ('resolved','detected'))),
 UNIQUE (job_id, seq),
 UNIQUE (decision_id, seq)
);

-- 2. 조사·조치 기록(append-only). 유형은 와이어프레임 feedbackKind 3종, 사유 1~2000자.
CREATE TABLE run_decision_feedback (
 id uuid PRIMARY KEY,
 job_id uuid REFERENCES run_jobs(id),
 decision_id uuid REFERENCES run_process_decisions(id),
 kind text NOT NULL CHECK (kind IN ('action','suspected-false-positive','insufficient-evidence')),
 reason text NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 2000),
 actor_id text NOT NULL CHECK (actor_id <> ''),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK (num_nonnulls(job_id, decision_id) = 1)
);
CREATE INDEX run_decision_feedback_job_idx ON run_decision_feedback(job_id, created_at) WHERE job_id IS NOT NULL;
CREATE INDEX run_decision_feedback_decision_idx ON run_decision_feedback(decision_id, created_at) WHERE decision_id IS NOT NULL;

-- 3. 사용자별 마지막 열람 시각. 대상·사용자당 1행(다시 열면 read_at만 갱신).
--    미확인 = 행이 없거나 read_at이 판정 항목 시각(occurredAt)보다 이르면(열람 뒤 중간 판정·최종 판정이 새로 생김).
CREATE TABLE run_decision_reads (
 job_id uuid REFERENCES run_jobs(id),
 decision_id uuid REFERENCES run_process_decisions(id),
 actor_id text NOT NULL CHECK (actor_id <> ''),
 read_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK (num_nonnulls(job_id, decision_id) = 1),
 UNIQUE (job_id, actor_id),
 UNIQUE (decision_id, actor_id)
);

COMMIT;

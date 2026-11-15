-- 032: 조사·조치 기록 수정(정정 행 추가). 이상 분석 › 판정 상세 판정 기록의 [수정](contracts/analysis-ui.md "조사·조치 기록" 절). API만 읽고 쓴다.
-- 014 run_decision_feedback에 열 2개 + 이름 있는 CHECK 1개 + 부분 UNIQUE 색인 1개만 더한다. 001~031 표·함수·트리거·enum·권한 정의는 바꾸지 않는다.
--   새 표·함수·트리거·시퀀스 없음. 기존 행은 고치지 않는다(보존): 수정 = 고친 값을 담은 새 행(정정 행)을 추가하고 직전 판본·최초 기록을 가리킨다.
-- 전제: 031까지 적용 뒤 1회 실행(014·029 외 의존 없음)(psql -v ON_ERROR_STOP=1 -f), 014 표 소유자로 실행.
--   역할 스크립트(scripts/database-roles.sql) 재적용 불필요: 014 GRANT가 표 단위(powercode_api SELECT,INSERT)라 새 열에도 그대로 적용된다(029와 같음).
--   UPDATE·DELETE 권한은 여전히 없다(append-only 유지).
--   enum 변경 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 자동 down migration 없음
--   (되돌리기 = 정정 행이 생기기 전에만 색인·CHECK·두 열 DROP. 생긴 뒤 되돌리면 정정 행이 따로 된 기록으로 보인다).
--   다시 실행하면 이미 있는 열에서 실패해 전체 ROLLBACK(반쪽 적용 없음).
--   잠금: 이 표에 ACCESS EXCLUSIVE를 잠깐 잡는다(DEFAULT 없는 NULL 열이라 행 재작성 없음, CHECK·색인은 기존 기록 행 수만큼 읽음 — 사람이 남긴 기록뿐이라 작다).
--   다른 표는 잠그지 않고 작업자를 멈출 필요 없음.
-- 배포 순서: migration 032 → 바로 새 API. 이 열을 모르는 이전 API의 INSERT는 열 목록을 명시하므로 NULL(최초 기록)을 넣어 먼저 적용해도 안전하다.
--   다만 이전 API는 정정 행을 따로 된 기록으로 보여 주고 확인 결과 계산에도 넣으므로, 새 API가 뜨기 전에 정정 행이 생기면 안 된다(정정 행은 새 API만 만든다).
-- 의미:
--   supersedes_id = 이 행이 바꾼 직전 판본(최초 기록 또는 앞선 정정 행). NULL = 최초 기록.
--   origin_id     = 최초 기록(정정 사슬의 첫 행). 최초 기록 자신은 NULL. 목록 위치·확인 결과 계산은 최초 기록 시각으로 정한다.
--   현재 판본 = 그 행을 supersedes_id로 가리키는 행이 없는 행. UNIQUE(supersedes_id)라 한 판본은 한 번만 바뀐다 → 사슬이 한 줄이고 동시 수정은 한쪽만 성공.
--   두 열은 함께 있거나 함께 없다(CHECK). 정정 행이 원래 기록과 같은 대상(job_id/decision_id)인지, origin_id가 그 사슬의 첫 행인지는 API가 검사한다
--   (job_id·decision_id 중 하나가 늘 NULL이라 복합 FK(MATCH SIMPLE)로는 검사되지 않는다. 트리거는 두지 않는다).
--   판정 값·evidence를 바꾸지 않고 확정 라벨·학습 입력이 아니다(014·029와 같음, Trainer·LLM·Detector 권한 없음).
BEGIN;

ALTER TABLE run_decision_feedback
 ADD COLUMN supersedes_id uuid REFERENCES run_decision_feedback(id),
 ADD COLUMN origin_id uuid REFERENCES run_decision_feedback(id),
 ADD CONSTRAINT run_decision_feedback_correction_check CHECK (
  num_nonnulls(supersedes_id, origin_id) IN (0, 2) AND supersedes_id IS DISTINCT FROM id AND origin_id IS DISTINCT FROM id);

CREATE UNIQUE INDEX run_decision_feedback_supersedes_uniq ON run_decision_feedback(supersedes_id) WHERE supersedes_id IS NOT NULL;

COMMIT;

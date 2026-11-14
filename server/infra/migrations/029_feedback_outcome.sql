-- 029: 조사·조치 기록의 확인 결과(실제 이상·정상·판단 불가). 이상 분석 › 판정 상세 `로그 분류하기` 대화상자에서 고른다(contracts/analysis-ui.md "조사·조치 기록" 절). API만 읽고 쓴다.
-- 014 run_decision_feedback에 열 1개만 더한다. 001~028 표·함수·트리거·enum·권한 정의는 바꾸지 않는다. 새 표·함수·트리거·시퀀스·색인 없음.
-- 전제: 014 적용 뒤 1회 실행(015~028과 의존 없음)(psql -v ON_ERROR_STOP=1 -f), 014 표 소유자로 실행.
--   역할 스크립트(scripts/database-roles.sql) 재적용 불필요: 014 GRANT가 표 단위(powercode_api SELECT,INSERT)라 새 열에도 그대로 적용된다(026과 같음).
--   enum 변경 없음 → 단일 BEGIN..COMMIT, 중간 오류 시 전체 ROLLBACK. 자동 down migration 없음
--   (되돌리기 = 이 열 DROP COLUMN, 이전 API는 이 열을 읽지 않는다). 다시 실행하면 이미 있는 열에서 실패해 전체 ROLLBACK(반쪽 적용 없음).
--   잠금: 이 표에 ACCESS EXCLUSIVE를 잠깐 잡는다(DEFAULT 없는 NULL 열이라 행 재작성 없음, CHECK 검사는 기존 기록 행 수만큼 읽음 — 사람이 남긴 기록뿐이라 작다).
--   다른 표(run_jobs·run_process_decisions)는 잠그지 않고 작업자를 멈출 필요 없음.
--   기존 API 호환: 기존 기록은 NULL(미입력)로 남는다. 이 열을 모르는 이전 API의 INSERT는 열 목록을 명시하므로 NULL을 넣는다
--   → migration을 API보다 먼저 적용한다(새 API는 이 열을 읽고 쓰므로 열이 없으면 조사·조치 기록 조회·저장이 503).
-- 의미: 그 기록을 남긴 관리자가 확인한 결과. NULL = 미입력. 판정 값·evidence를 바꾸지 않고 확정 라벨·학습 입력이 아니다(014와 같음, Trainer·LLM·Detector 권한 없음).
--   값: actual-anomaly(실제 이상) / normal(정상) / undetermined(판단 불가). API 검사(contracts/analysis-ui.md)와 같은 목록을 CHECK로도 둔다.
BEGIN;

ALTER TABLE run_decision_feedback
 ADD COLUMN outcome text CONSTRAINT run_decision_feedback_outcome_check CHECK (outcome IN ('actual-anomaly','normal','undetermined'));

COMMIT;

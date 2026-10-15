# CI·Notion 동기화

- `ci.yml`: push·PR에서 frontend/backend/automation 테스트, PR 대상 `dev`·승격 대상 `main` 모두 검사
- `notion-sync.yml`: `2-59/5 * * * *`(UTC 매시2·7·12…57분, 5분 간격) 예약, CI 성공 후, 수동 실행 지원. 0분 회피, 실제 실행은 GitHub 지연·누락 가능
- 자격증명 사용: 지정 저장소의 검토된 `main` 코드만 실행, checkout `main`·persist-credentials false 유지. PR 코드·산출물 미사용
- 체크포인트: 고정 main sync job에 `cache-mode: write` 명시, 상위 PR CI 완료 후에도 상태 저장 허용. 캐시에는 SQLite 매핑 데이터만 보관, 실행 코드·자격증명 미보관. 원격 소유 marker가 dev 이관 완료인 동일 작업의 오래된 main checkpoint만 복구
- 캐시 검증: save step 성공과 실제 저장 성공 구분, 경고·실제 cache key 존재 확인. [GitHub cache-mode 공식 규칙](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#controlling-cache-access-with-cache-mode) 기준
- 기능 PR → `dev` → `main` 승인·병합 후 자동화 변경 활성화. 기본 브랜치 변경·자동 병합·배포 없음
- main 반영 전 기존30분 예약·main 대상 자동 PR 유지. 새5분 예약도 클릭 후5분 내 완료를 보장하지 않음
- 예약 최소 간격: [GitHub 공식 schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)의 5분 제한

`ci.yml`의 worker-runtime gate는 새 로컬 PostgreSQL DB에 001~007의 정확한 순서를 적용하고, CPU 단위 시험·실제 IF/v12 연결·3모드/임대/큐 분리·모형 고객 API 반복 수집·API/SSE/설명 요청 계약을 검증한다. 생성은 명시적 fixture이며 GPU 생성 품질 시험이 아니다. GitHub Actions 자체 실행은 로컬 검증과 별도로 확인해야 한다. 기존 CD 브랜치의 GHCR release 배포와는 별도이며 이미지 발행/서버 배포를 자동으로 추가하지 않았다.

CPU 데모 검증은 `008_demo_replay.sql`까지 적용한 격리 DB에서 CSV/SQL 입력 학습, 재개/반복 전송, 실제 판정 및 DB 역할 경계를 확인한다. 초기 `001/002`만 적용된 별도 QA DB도 생성해 읽기 호환성을 확인한다. 실제 서버 CSV나 고객 API 호출은 사용하지 않는다.

009 이후 공통 입력 시험은 API/CSV/일시정지 전환부터 피처·Rule 생성, 사용자 채택 API, 실제 CPU 모델 판정까지 검증한다. 생성 모델은 명시적 계약 fixture이며 실제 GPU 추론 품질 검증은 아니다. 제한된 Collector 역할의 네 스트림 입고/원문 조회 거부도 검사한다.

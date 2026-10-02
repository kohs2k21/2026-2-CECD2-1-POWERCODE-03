# CI·Notion 동기화

- `ci.yml`: push·PR에서 frontend/backend/automation 테스트, PR 대상 `dev`·승격 대상 `main` 모두 검사
- `notion-sync.yml`: `2-59/5 * * * *`(UTC 매시2·7·12…57분, 5분 간격) 예약, CI 성공 후, 수동 실행 지원. 0분 회피, 실제 실행은 GitHub 지연·누락 가능
- 자격증명 사용: 지정 저장소의 검토된 `main` 코드만 실행, checkout `main`·persist-credentials false 유지. PR 코드·산출물 미사용
- 체크포인트: 고정 main sync job에 `cache-mode: write` 명시, 상위 PR CI 완료 후에도 상태 저장 허용. 캐시에는 SQLite 매핑 데이터만 보관, 실행 코드·자격증명 미보관. 원격 소유 marker가 dev 이관 완료인 동일 작업의 오래된 main checkpoint만 복구
- 캐시 검증: save step 성공과 실제 저장 성공 구분, 경고·실제 cache key 존재 확인. [GitHub cache-mode 공식 규칙](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#controlling-cache-access-with-cache-mode) 기준
- 기능 PR → `dev` → `main` 승인·병합 후 자동화 변경 활성화. 기본 브랜치 변경·자동 병합·배포 없음
- main 반영 전 기존30분 예약·main 대상 자동 PR 유지. 새5분 예약도 클릭 후5분 내 완료를 보장하지 않음
- 예약 최소 간격: [GitHub 공식 schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)의 5분 제한

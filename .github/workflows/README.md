# CI·Notion 동기화

- `ci.yml`: push·PR에서 frontend/backend/automation 테스트, PR 대상 `dev`·승격 대상 `main` 모두 검사
- `notion-sync.yml`: UTC 매시 17·47분 예약, CI 성공 후, 수동 실행 지원. 예약 실행은 GitHub 지연 가능
- 자격증명 사용: 지정 저장소의 검토된 `main` 코드만 실행, checkout `main`·persist-credentials false 유지. PR 코드·산출물 미사용
- 기능 PR → `dev` → `main` 승인·병합 후 자동화 변경 활성화. 기본 브랜치 변경·자동 병합·배포 없음

# Automation runtime

- Python 3.11 이상 · 표준 라이브러리만 사용
- 실행 위치: 저장소 루트

~~~powershell
python -m automation.services.sync.app --check-access
python -m automation.services.sync.app
~~~

- `--check-access`: 읽기 전용 저장소·기준 브랜치·Notion 스키마 검사, 작업·요청 개수 출력
- 일반 동기화: GitHub Actions 전용, 로컬 실행 차단
- 인증: `NOTION_TOKEN` + `GITHUB_TOKEN`(또는 `GH_TOKEN`), 비밀값 출력 금지
- 단일 실행기 · `automation/.runtime/` 잠금·SQLite 매핑 · Git 제외
- API 오류: 비밀값 없는 고정 오류 코드
- `Milestone`: 현재 Notion 선택 옵션 기준, 이름 변경·추가·삭제 허용. 선택된 제목과 같은 GitHub milestone 재사용, 없으면 생성. 기존 GitHub milestone 이름·삭제 여부는 변경하지 않음.
- 상태·유형·영역·대상 저장소 등 실행 계약의 필수 속성·옵션은 유지 필요
- 신규 작업·hotfix: `dev`에서 기능 브랜치 생성 → `dev`로 Draft PR → 검토·통합 후 `dev`에서 `main`으로 승격 PR. 자동 병합·승격 PR 자동 생성 없음
- 종료 작업 정리: GitHub Issue가 닫힌 작업의 `<type>/<Issue번호>-<영문요약>` 브랜치 검사. 삭제된 Notion 작업·GitHub 수동 작업도 포함. 열린 Issue·PR 번호·네이밍 규칙 밖 브랜치·`dev`·`main` 제외. 수동 Issue 종료는 담당자 수행
- 삭제 조건: 같은 저장소·정확한 head 브랜치의 열린 PR이 없고, 연결 기준에 병합된 PR의 head SHA와 현재 SHA 일치. 병합 이력이 없으면 기준 브랜치 대비 미병합 커밋 0개일 때만 삭제. 빈 커밋도 보존
- 정리 검증: Notion 표식이 있으면 저장소·유형·요약·기준 브랜치 검증, 표식이 없으면 네이밍 규칙 및 기존 PR 기준 확인. 여러 기준 PR·잘못된 기준은 검토 필요. 삭제 직전 Issue·PR 재조회, SHA lease로 동시 push 보호. 완료 캐시와 무관하게 다음 실행에서 재검사
- 정리 보류: `OPEN_PR_PREVENTS_BRANCH_DELETE` / `BRANCH_HAS_UNMERGED_COMMITS_NOT_DELETED` / `BRANCH_CHANGED_AFTER_MERGE_NOT_DELETED` / `CLEANUP_BASE_REQUIRES_REVIEW`. 고정 오류 코드 출력·해당 브랜치 보존, 다른 작업 계속 처리. 삭제·이미 없음·재열기 결과는 전역 정리 로그에 기록
- 종료 작업 재생성 금지: 닫힌 Issue에는 브랜치·Draft PR 생성 없음. 명시적 Issue 재열기는 원격 이벤트 이력으로 확인, 과거 병합에 의한 재종료 방지. 같은 브랜치의 새 열린 PR도 과거 병합보다 우선
- Notion 완료 기준: 올바른 기준 PR 병합 확인. Issue만 닫힌 경우 완료·완료일로 처리하지 않음. `not_planned` 종료 또는 기존 취소는 취소 유지. 설명·일정·분류·계정 매핑 계약은 기존 규칙 유지
- 기존 `main` 연결: 종료·병합 이력 유지. 열린 Issue에 PR이 없거나 같은 브랜치의 `dev` PR이 있으면 브랜치·커밋 보존 후 기준 메타데이터만 `dev`로 이관·연결. 열린 기존 `main` PR은 `LEGACY_ACTIVE_MAIN_PR_REQUIRES_REVIEW`로 중단하고 담당자 검토 필요
- 담당자: 확인된 Notion 사용자 ID와 GitHub 계정만 명시적 매핑, 미확인 사용자는 `ASSIGNEE_MAPPING_REQUIRED`. config와 실행기 승인 목록 동시 갱신 필요
- Issue 제목: `<type>(<scope>): <emoji> 작업명`, Issue 번호 접미사 없음. scope는 첫 번째 Notion 영역 소문자, 복수 영역은 모두 라벨 유지. 알려진 선행 `[type]`·완성된 conventional 접두사 중복 제거·Notion 원문 유지
- 제목 동기화: 마지막 자동 제목과 같은 경우 Notion 제목 변경·이전 봇 `[type]` 제목의 새 형식 이관, GitHub 직접 편집 제목은 보존; 명시적 본문 동기화 요청 시 Notion 제목 재적용. 제목 기억값 없는 연결은 현재 Notion 원문/자동 제목/동일 작업명의 이전 `[type]` 제목만 이관, 다른 제목은 보존. PR 제목은 같은 Issue 제목 뒤 `(#Issue)` 추가
- 버튼: Notion 생성/본문 요청 체크박스 설정, 다음 실행기 처리 성공 후 해제. 클릭 즉시 Actions 호출 없음
- 실행 시점: `2-59/5 * * * *`(UTC 매시2·7·12…57분, 5분 간격) 예약·CI 성공 후·수동 `workflow_dispatch`. 0분 회피, 예약 지연·누락 가능·5분 내 완료 보장 없음. `poll_seconds`는 Actions 예약 간격에 영향 없음
- 활성화: 실행기·설정 수정은 기능 PR → `dev` 통합 → `main` 승격 병합 후 적용. 자격증명 사용 실행기는 항상 검토된 `main` checkout, PR/기능 브랜치 코드 실행 금지
- 운영 적용: 이번 종료 작업 정리 보완은 기능 PR의 `dev` 통합 및 `dev` → `main` 승격 후 활성화. 기능 브랜치에서 운영 인증 동기화 실행 금지

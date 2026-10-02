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
- 기존 `main` 연결: 종료·병합 이력 유지. 열린 Issue에 PR이 없거나 같은 브랜치의 `dev` PR이 있으면 브랜치·커밋 보존 후 기준 메타데이터만 `dev`로 이관·연결. 열린 기존 `main` PR은 `LEGACY_ACTIVE_MAIN_PR_REQUIRES_REVIEW`로 중단하고 담당자 검토 필요
- 담당자: 확인된 Notion 사용자 ID와 GitHub 계정만 명시적 매핑, 미확인 사용자는 `ASSIGNEE_MAPPING_REQUIRED`. config와 실행기 승인 목록 동시 갱신 필요
- 버튼: Notion 생성/본문 요청 체크박스 설정, 다음 실행기 처리 성공 후 해제. 클릭 즉시 Actions 호출 없음
- 실행 시점: `2-59/5 * * * *`(UTC 매시2·7·12…57분, 5분 간격) 예약·CI 성공 후·수동 `workflow_dispatch`. 0분 회피, 예약 지연·누락 가능·5분 내 완료 보장 없음. `poll_seconds`는 Actions 예약 간격에 영향 없음
- 활성화: 실행기·설정 수정은 기능 PR → `dev` 통합 → `main` 승격 병합 후 적용. 자격증명 사용 실행기는 항상 검토된 `main` checkout, PR/기능 브랜치 코드 실행 금지
- main 반영 전: 기존30분 예약·main 대상 자동 PR·기존 담당자 매핑 유지. 기능 PR을 dev에 병합한 뒤 dev→main 승격 필요; 현재 dev/main 동일로 승격 PR 생성 불가

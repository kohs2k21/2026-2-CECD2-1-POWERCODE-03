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
- 오류 분류(`classify_code`): 페이지 입력 오류(`UNSUPPORTED_*`·`INVALID_TABLE`·`BODY_TOO_*`·제목·유형·영역·Milestone·`ENGLISH_BRANCH_SLUG_REQUIRED`·`TYPE_LOCKED_AFTER_CREATION` 등)·정리 보류·항목 단위 일시 오류(`NETWORK_UNCERTAIN_RETRY_NEXT_RUN`·`HTTP_429`·`HTTP_5xx`)는 경고. Notion 수정·다음 실행 재시도로 해소
- 실패 유지: `POSSIBLE_SECRET_IN_TASK`, `ASSIGNEE_MAPPING_REQUIRED`(config 매핑 추가 필요 가능), 무결성(소유권·표식·기준·매핑·`REF_DELETE_*`·`FOREIGN_*`·Issue 링크·`INTERNAL_ERROR`), 시스템(인증·스키마·저장소 불일치·429 외 4xx), 미분류 코드. 페이지·브랜치 단위가 아닌 실행 전체 오류(예상 밖 예외는 `INTERNAL_ERROR`)는 일시 코드여도 실패
- 종료 코드(단일 실행): 경고만 있으면 0. 실패 오류 지문(정렬된 `대상, 코드` 목록)이 새로 생기거나 바뀌면 1. 직전 알림과 같은 지문이면 24시간 동안 경고로 낮춰 0, 24시간 경과 시 다시 1. 실패 일부 해소·추가도 변경으로 1. 저장 시각이 미래이거나 저장값 손상 시 1·갱신. 실패 오류 해소 시 지문 삭제
- 지문 저장: `state.db`의 `alerts` 표, Actions 캐시로 실행 간 유지. 캐시 유실 시 1회 재알림 허용. `--watch`는 종료 코드·지문 없음
- 오류 기록: 페이지 현재 값이 이미 `GitHub 동기화=오류`·같은 코드면 Notion 재기록 생략. 닫힌 Issue의 정리 오류는 링크·완료 반영과 함께 한 번에 기록, 같은 값이면 수정 없음
- Actions 표시: 실패 `::error::`, 경고·반복 실패 `::warning::`, `GITHUB_STEP_SUMMARY` 요약. 고정 코드·page_id·Issue 번호만 표시, 기존 JSON 결과 줄 유지
- `Milestone`: 현재 Notion 선택 옵션 기준, 이름 변경·추가·삭제 허용. 선택된 제목과 같은 GitHub milestone 재사용, 없으면 생성. 기존 GitHub milestone 이름·삭제 여부는 변경하지 않음.
- 상태·유형·영역·대상 저장소 등 실행 계약의 필수 속성·옵션은 유지 필요
- 신규 작업·hotfix: `dev`에서 기능 브랜치 생성 → `dev`로 Draft PR → 검토·통합 후 `dev`에서 `main`으로 승격 PR. 자동 병합·승격 PR 자동 생성 없음
- 종료 작업 정리: GitHub Issue가 닫힌 작업의 `<type>/<Issue번호>-<영문요약>` 브랜치 검사. 삭제된 Notion 작업·GitHub 수동 작업도 포함. 열린 Issue·PR 번호·네이밍 규칙 밖 브랜치·`dev`·`main` 제외. 수동 Issue 종료는 담당자 수행
- 삭제 조건: 같은 저장소·정확한 head 브랜치의 열린 PR이 없고, 연결 기준에 병합된 PR의 head SHA와 현재 SHA 일치. 병합 이력이 없으면 기준 브랜치 대비 미병합 커밋 0개일 때만 삭제. 빈 커밋도 보존
- 정리 검증: Notion 표식이 있으면 저장소·유형·요약·기준 브랜치 검증, 표식이 없으면 네이밍 규칙 및 기존 PR 기준 확인. 여러 기준 PR·잘못된 기준은 검토 필요. 삭제 직전 Issue·PR 재조회, SHA lease로 동시 push 보호. 완료 캐시와 무관하게 다음 실행에서 재검사
- 정리 보류: `OPEN_PR_PREVENTS_BRANCH_DELETE` / `BRANCH_HAS_UNMERGED_COMMITS_NOT_DELETED` / `BRANCH_CHANGED_AFTER_MERGE_NOT_DELETED` / `CLEANUP_BASE_REQUIRES_REVIEW`. 고정 오류 코드 출력·해당 브랜치 보존, 다른 작업 계속 처리. 삭제·이미 없음·재열기 결과는 전역 정리 로그에 기록
- 종료 작업 재생성 금지: 닫힌 Issue에는 브랜치·Draft PR 생성 없음. 명시적 Issue 재열기는 원격 이벤트 이력으로 확인, 과거 병합에 의한 재종료 방지. 같은 브랜치의 새 열린 PR도 과거 병합보다 우선
- 작업 상태 소유권: Notion `상태`는 수동 관리하되, 최종 반영 전 재조회한 GitHub Issue가 `closed`이고 `state_reason != not_planned`이면 `완료` 자동 반영. 종료 이유가 없는 기존 closed Issue도 포함. 취소 종료·열린 Issue·명시적 재열림·알 수 없는 Issue 상태는 기존 Notion 상태 유지. 신규 연결·Draft/open/closed/merged PR 자체로 상태를 정하지 않으며, 기존 PR 병합→Issue 종료 후에도 실제 Issue 상태 재확인
- 완료일·방향: `완료일`은 계속 Notion 수동 관리·설정/삭제 없음. PR 병합에 따른 기존 GitHub Issue 종료·브랜치 정리 유지, Notion 완료→Issue 종료 기능 없음. API 사이 원자적 잠금은 없어 최종 조회 이후 변경까지 보장하지 않으며, 이미 반영된 완료를 재열림으로 자동 되돌리지 않음
- 동기화 소유 필드: Issue 번호·GitHub Issue·Branch·Pull Request 링크, GitHub 동기화 결과·오류·마지막 동기화·처리한 생성/본문 요청 해제. `GitHub 동기화=완료`·실행 결과 `completed`는 작업 상태 변경 의미 아님. 설명·일정·분류·계정 매핑 계약은 기존 규칙 유지
- 기존 `main` 연결: 종료·병합 이력 유지. 열린 Issue에 PR이 없거나 같은 브랜치의 `dev` PR이 있으면 브랜치·커밋 보존 후 기준 메타데이터만 `dev`로 이관·연결. 열린 기존 `main` PR은 `LEGACY_ACTIVE_MAIN_PR_REQUIRES_REVIEW`로 중단하고 담당자 검토 필요
- 담당자: 확인된 Notion 사용자 ID와 GitHub 계정만 명시적 매핑, 미확인 사용자는 `ASSIGNEE_MAPPING_REQUIRED`. config와 실행기 승인 목록 동시 갱신 필요
- Issue 제목: `<type>(<scope>): <emoji> 작업명`, Issue 번호 접미사 없음. scope는 첫 번째 Notion 영역 소문자, 복수 영역은 모두 라벨 유지. 알려진 선행 `[type]`·완성된 conventional 접두사 중복 제거·Notion 원문 유지
- 제목 동기화: 마지막 자동 제목과 같은 경우 Notion 제목 변경·이전 봇 `[type]` 제목의 새 형식 이관, GitHub 직접 편집 제목은 보존; 명시적 본문 동기화 요청 시 Notion 제목 재적용. 제목 기억값 없는 연결은 현재 Notion 원문/자동 제목/동일 작업명의 이전 `[type]` 제목만 이관, 다른 제목은 보존. PR 제목은 같은 Issue 제목 뒤 `(#Issue)` 추가
- 버튼: Notion 생성/본문 요청 체크박스 설정, 다음 실행기 처리 성공 후 해제. 클릭 즉시 Actions 호출 없음
- 본문 표: Notion 일반 표를 GitHub Markdown 표로 변환; 머리글 없는 표는 빈 머리글 추가·첫 데이터 행 보존, 첫 열 머리글은 굵게 표시. 셀 줄바꿈·파이프·기존 인라인 서식 유지, Notion 원문 미변경; 이미지·멘션 등 기존 미지원 항목과 잘못된 표는 부분 갱신 차단
- 실행 시점: `2-59/5 * * * *`(UTC 매시2·7·12…57분, 5분 간격) 예약·CI 성공 후·수동 `workflow_dispatch`. 0분 회피, 예약 지연·누락 가능·5분 내 완료 보장 없음. `poll_seconds`는 Actions 예약 간격에 영향 없음
- 활성화: 자격증명 사용 실행기는 검토된 `main` checkout만 사용. 일반 기능은 작업 브랜치 → `dev` 통합 → 검토한 `main` 승격. `dev`에 미승인 변경이 있으면 `main` 기준 수정 전용 PR로 적용하고 `dev`에도 동일 수정 반영. PR/기능 브랜치 코드의 운영 인증 실행 금지
- 운영 적용: 수정 코드의 `main` 병합 후 활성화; `dev` 대상 PR 병합만으로 운영 반영되지 않음. 미승인 변경을 포함한 `dev` 전체 승격 금지

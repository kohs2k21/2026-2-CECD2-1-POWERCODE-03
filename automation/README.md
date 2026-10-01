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

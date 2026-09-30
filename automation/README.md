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

# 팀원 시작 안내

- 기준: `feat/1-ksj-huj-integration` 통합 브랜치·Draft PR 검토 중; 병합 전 해당 브랜치 기준 작업
- 현재 완료: KSJ 화면·HUJ 백엔드 통합, 로그인·권한·실시간 분석 연결, 설정 정합성·홈 위젯 분리

## 폴더·담당 경계

| 경로 | 용도 |
|---|---|
| `server/frontend/` | React·TypeScript 화면 |
| `server/backend/` | 인증·조회·이벤트 API, JSON 계정 저장 |
| `server/worker/` | collector·detector·evaluator 구현 예정 |
| `server/contracts/` | 프론트·백엔드·AI 공통 API/이벤트 규약 |
| `server/infra/` | 실행·배포 설정 영역, 운영 배포 미구축 |
| `research/` | 연구·실험·평가·시행착오; 운영 코드 의존 금지 |
| `docs/`, `automation/`, `.github/workflows/` | 공유 문서 / Notion 연동 / CI·CD 영역(자동화 미가동) |

## 최초 실행·로컬 계정

- 준비: Node.js 22.12 이상 22.x, pnpm 11.1.2; 저장소 루트 기준 별도 터미널 2개
- 백엔드 터미널(PowerShell): 아래 설정 후 실행; 로컬 개발 전용 예시 계정

```powershell
cd server/backend
npm ci --ignore-scripts
$env:JWT_SECRET = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$env:SEED_ADMIN_EMAIL = 'admin@local.test'
$env:SEED_ADMIN_PASSWORD = '1234'
$env:SEED_USER_EMAIL = 'user@local.test'
$env:SEED_USER_PASSWORD = '1234'
npm run dev
```

- 프론트 터미널:

```powershell
cd server/frontend
pnpm install --frozen-lockfile
$env:VITE_API_BASE_URL = 'http://localhost:5001'
pnpm dev
```

- 접속: `http://localhost:5173`; 관리자 `admin@local.test` / `1234`, 일반 `user@local.test` / `1234`
- 기본 계정 자동 제공 없음; 위 환경변수로 없는 이메일만 최초 생성. 기존 계정 비밀번호 덮어쓰기 없음
- 계정 저장: 백엔드 `data/users.json`(bcrypt 해시, Git 제외); 공개 회원가입 비활성
- 위 비밀번호: 로컬 예시 전용. 공유 서버는 별도 계정·비밀번호·영속 JWT_SECRET 설정

## 작업·검증·주의 사항

- 이슈 생성 → 최신 통합 기준에서 개인 기능 브랜치 → 담당 경로 수정 → 테스트 → PR; main 직접 수정 금지
- API 필드 변경: `server/contracts/`부터 합의; AI 운영 코드만 `server/worker/`로 반영
- 커밋: `fix(frontend): 🐛 변경 요약 (#이슈번호)` 형식 + 본문에 이유·변경 bullet
- 검증: `server/`에서 `pnpm test`; 프론트 `pnpm build`, 백엔드 `npm run build`
- 분석 목록: 연결 이후 SSE 수신분 최대 50건; 새로고침 시 초기화, 수신 전 빈 목록 정상
- 실제 탐지 모델·과거 조회·계층별 상세·업무 상태 저장 미연결. 홈 데이터 및 시스템 설정·모델 관리는 목업/기획 대기
- `.env`·계정 파일·고객 데이터 커밋 금지; `AGENTS.md`·`MyDocs/`는 개인 전용·공유 대상 제외
- 학교 저장소 반영·운영 배포·CI/CD·Notion 자동화: 별도 합의 및 설정 필요

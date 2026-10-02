# Notion 버튼 즉시 요청 중계 — 배포 준비

- 상태: Cloudflare Workers용 구현·로컬 mock 검증 준비, 실제 배포·Notion 버튼 연결 미수행
- 흐름: Notion 요청 checkbox 설정 → webhook POST → GitHub workflow_dispatch → main 동기화 실행 → Issue/브랜치 결과 반영
- 즉시 의미: 예약을 기다리지 않고 실행 요청 제출. Actions 큐·실행·API 처리 시간까지 즉시 완료 보장 아님
- 외부 의존성·KV·DB 없음, Node.js 22의 `node --test automation/notion-webhook/worker.test.mjs`로 검사

## 고정 대상·인증

- URL: 배포된 Worker의 `/notion-sync`, POST만 허용
- Notion webhook header: `X-Notion-Relay-Secret`, 32자 이상 무작위 relay secret
- Worker Secrets: `NOTION_RELAY_SECRET`, `GITHUB_DISPATCH_TOKEN`. GitHub 토큰·Notion 통합 토큰은 Notion 버튼/페이지/URL에 저장 금지
- GitHub 토큰: 해당 저장소만 선택한 fine-grained PAT, Actions write 최소 권한·만료일 설정. Secrets UI/secret 입력만 사용, Git 추적 파일·명령 인수에 값 삽입 금지
- 고정 대상: `kohs2k21/2026-2-CECD2-1-POWERCODE-03`의 `notion-sync.yml`, ref `main`
- webhook body 미조회·미전달, 사용자 입력으로 저장소/브랜치/workflow 변경 불가. Worker는 Notion/GitHub 작업 본문을 조회하지 않음
- relay secret은 해당 동기화 실행 요청 권한만 제공. 편집자가 header에 접근할 가능성 고려, 유출 시 Worker secret과 버튼 header 동시 교체

## 적용 순서

1. 기능 PR → dev 검토·병합 → dev → main 승격 PR 사용자 병합; main에 dev 정책·팀원 매핑 반영 확인
2. 사용자 Cloudflare 계정의 Worker 생성·검토된 `worker.mjs` 적용, 두 Worker Secrets 설정. `wrangler.toml`은 CLI 설정 예시, 배포 도구 설치/계정 연결 미수행
3. Notion 편집 권한 로그인 후 기존 생성/본문동기화 버튼의 요청 checkbox 설정 뒤 webhook 단계 추가, Worker HTTPS URL·relay header만 지정
4. 기존 요청으로 POST 수락 → main workflow 실행 → 실제 보드 반영 순서 확인, 새 시험 페이지/고객 본문 생성 불필요

- 기능 PR 병합 전 dev와 main은 동일하여 승격 PR 생성 불가. 기능 PR을 dev에 병합한 뒤 승격 PR 생성 필요
- main 승격 전 기존 자동화의 신규 자동 PR 대상은 여전히 main, dev 정책·추가 매핑 아직 비활성

## 오류·운영 제약

- 202 `WORKFLOW_DISPATCHED`: GitHub가 실행 요청 수락, 작업 생성 완료 의미 아님
- 401/404/405: relay 인증·경로·method 확인. 503 `RELAY_NOT_CONFIGURED`: Worker secrets 확인
- 502 `GITHUB_ACCESS_REJECTED`/`GITHUB_DISPATCH_REJECTED`: 권한·토큰 만료·workflow/ref 확인, upstream 원문/인증값 응답 없음
- 503 `GITHUB_RATE_LIMITED`, 504 timeout, 502 uncertain: 이미 dispatch됐을 가능성 포함. 자동 재시도 없음; 요청 checkbox 유지·예약 동기화 또는 담당자 확인 후 재요청
- 인증된 버튼마다 dispatch, debounce/drop 없음. 중복 생성은 기존 Actions concurrency·sync 멱등성으로 방지, 실행 요청 남용/호출 제한은 별도 운영 제약
- dispatch 수락은 클릭마다 독립 workflow 실행·생성 완료를 뜻하지 않음. 활성 sync와 단일 pending 제한으로 실행 요청이 합쳐질 수 있으며, 다음 전체 보드 조회가 유지된 요청 체크박스를 재조정
- webhook 오류 시 Notion 자동화 상태 확인 필요, 오류를 가짜 성공으로 대체하지 않음. relay secret 유출 시 반복 dispatch 가능; 회수·토큰 권한 최소화 필요
- 자격증명·본문·upstream body console 출력 없음. Worker·Notion 자동 로그/진단에서도 headers·secrets 출력 금지
- 기존30분 예약은 webhook 미연결/실패 시 fallback, GitHub 예약 지연 가능
- 공식 근거: [Notion webhook](https://www.notion.com/help/webhook-actions), [GitHub dispatch](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event), [Worker fetch](https://developers.cloudflare.com/workers/runtime-apis/handlers/fetch/), [Worker secrets](https://developers.cloudflare.com/workers/configuration/secrets/)

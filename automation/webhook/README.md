Created: 2026-10-08T14:03:47+09:00
Updated: 2026-10-08T15:15:30+09:00
Author: backend_lead, root
Status: current

# Notion 생성·본문 동기화 요청 웹훅

- Notion 생성 버튼 → POST `/notion` → Cloudflare Queue → 기존 `main`의 `notion-sync.yml` 전수 동기화
- 고정 대상: `kohs2k21/2026-2-CECD2-1-POWERCODE-03`; 수신 본문의 repository·ref·workflow 지정 무시
- JSON 페이지 속성 본문 검증 후 폐기; 큐에는 접수 ID·시간·실행 ID·재시도 counter만 저장

## 구성·연결

- Worker: `powercode-notion-webhook`
- `REQUESTS`: `powercode-notion-requests`, consumer 연결 필수
- `DEAD_LETTER`: `powercode-notion-dead-letter`, 요청 큐의 native DLQ 연결 필수
- Worker Secrets: `NOTION_WEBHOOK_SECRET`·`GITHUB_DISPATCH_TOKEN`; 코드/설정/Notion 본문에 토큰 저장 금지
- GitHub fine-grained PAT: 해당 저장소만, Actions read/write·Metadata read; 기존 Notion token은 GitHub Actions Secrets에 유지
- 활성화: 두 Secret·producer/consumer/DLQ 연결 확인 후 배포 변수 `WEBHOOK_ENABLED=true`
- 저장소 설정 기본값 `false`: 새 배포 시 활성 변수 보존 여부 확인
- Notion 버튼: CREATE 요청 설정 이후 Send webhook 액션, Worker URL의 `/notion`, 공유 헤더 `X-Webhook-Secret`
- 헤더에는 `NOTION_WEBHOOK_SECRET`만 사용; GitHub PAT 입력 금지
- `NOTION_WEBHOOK_SECRET`: 32자 이상 무작위 인증값, GitHub PAT와 별개
- 본문 동기화 버튼: `🔄 본문 동기화 요청=true` → 동일 웹훅; 체크만 변경한 요청은 기존 예약 실행에서 처리
- Notion Send webhook: 유료 plan 필요, 실패로 automation이 paused 된 경우 원인 해결 후 수동 resume/버튼 재실행

## 로컬 검증·번들

```sh
npm ci
npm run typecheck
npm test
npm run bundle
```

- Node22 이상, 패키지 lock 포함; 번들 `dist/worker/index.js`, dry-run만 수행
- `npm test`: 단위 시험 및 native workerd의 dispatch·poll·리다이렉트 차단 회귀 시험; 실제 네트워크 없는 fixture 사용
- `.dev.vars*`·`.wrangler/`·`dist/` Git 제외; 로컬 파일/환경/인증 헤더 출력 금지
- 배포 시 Secrets 유지·Queue 바인딩·consumer·로그 invocation 비활성/query redaction 확인

## 최초 배포·중지

- 위 명령 작업 경로: `automation/webhook/`
- Cloudflare 계정 이메일 인증 필요; 기존 Queue 존재 시 생성 명령 생략

```sh
npx wrangler login
npx wrangler queues create powercode-notion-requests
npx wrangler queues create powercode-notion-dead-letter
npx wrangler deploy
npx wrangler secret put GITHUB_DISPATCH_TOKEN
npx wrangler secret put NOTION_WEBHOOK_SECRET
npx wrangler deploy --var WEBHOOK_ENABLED:true
```

- Secrets: Cloudflare Secret 또는 대화형 명령으로 직접 등록; 채팅·명령 인수·Git 저장 금지
- 재배포 시 활성화 옵션 필요; 기본 배포는 비활성화
- 중지: `npx wrangler deploy --var WEBHOOK_ENABLED:false`; 기존 예약 실행 유지
- Git 커밋·Queue producer 등록만으로 실제 배포/consumer 연결 완료 처리 금지

## 상태·복구

- `202 queued`: 큐 저장 완료, GitHub 실행·Notion 동기화 완료 의미 아님
- `dispatched` 구조 로그: GitHub API `2026-03-10`의 `200 workflow_run_id` 확인
- `completed` 구조 로그: 해당 run의 `completed/success`; 실제 Issue·branch·Notion 상태는 별도 대조
- GitHub 요청 timeout10초; network/429/5xx/rate-limit403 최대5회, 서버 Retry-After/reset 존중
- 최초 run poll30초·이후60초, 누적120회/접수2시간; 실패/cancelled/timed_out/startup_failure 최대1회 재dispatch
- 영구 오류·예산 초과: DLQ 및 `dead_letter` 구조 로그 확인, 토큰/권한/실행 오류 해결 후 원본 생성/본문 동기화 요청 상태·기존 예약 동기화로 복구
- 정확히1회 실행 보장 없음; 응답 유실·Queue 중복 전달·ACK 경계에서 중복 가능, 기존 전수 동기화의 멱등 처리 유지
- Free Queue10k operations/day·보존24시간, 재시도/poll도 연산 소비; DLQ 영구 보존 아님
- 기존 schedule 유지; Runner 자원 지연·Notion action pause·장애 때문에 즉시 완료 보장 없음
- 수신 원문·인증값 로그 없음; Queue 최소 메타와 sanitized 오류 코드만 사용
- 오류 조회: Cloudflare Logs → Workers → Script Name `powercode-notion-webhook`; 구조 로그의 `code`·`attempt` 확인
- GitHub fetch: `redirect: manual`, 모든 3xx 영구 오류 처리; 외부 리다이렉트에 인증 헤더 전송 금지

## 공식 계약

- [Notion webhook actions](https://www.notion.com/help/webhook-actions)
- [Queue persistence·ACK·retry](https://developers.cloudflare.com/queues/configuration/javascript-apis/)
- [Queues pricing](https://developers.cloudflare.com/queues/platform/pricing/)
- [GitHub workflow dispatch](https://docs.github.com/en/rest/actions/workflows?apiVersion=2026-03-10#create-a-workflow-dispatch-event)


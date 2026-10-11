Created: 2026-10-08T11:52:10+09:00
Updated: 2026-10-08T11:52:10+09:00
Author: backend_lead
Status: current

# 인증 공통 계약 v2

- 범위: Express 로그인·현재 사용자·기존 관리자 API의 프론트/백엔드 공통 명세
- 변경: v1의 잘못되거나 만료된 JWT `403` → `401`; 인증 오류 code 추가. 유효 사용자 권한 부족은 `403 admin_required`
- 유지: 기존 JWT/Bearer·JSON 계정·user/admin 역할·등록 비활성화·email/role 변경 시 토큰 무효화. 새 인증 방식·refresh token·계정 DB 이관 없음

## 공통 요청·사용자

- 보호 API: `Authorization: Bearer <JWT>` 헤더 필수, query-string token 미지원
- 공통 사용자: `{ id: string, email: string, userType: "user" | "admin", createdAt: string }`; password/인증값 반환 제외
- 역할 확인: JWT 서명·payload 검증 뒤 저장 계정 재조회, 존재·정규화 email·userType 대조. 클라이언트가 전송한 역할이나 메뉴 표시만으로 권한 부여 없음
- 화면 복원: 저장 token으로 `/api/auth/me` 재조회, 응답 userType 기준 역할·탐색 복원. 조회 중/실패를 임의 로그인·관리자 성공으로 대체 금지

## 로그인 — POST /api/auth/login

- 접근: 비로그인 허용
- 요청:

```json
{ "email": "local-account@example.test", "password": "<password>" }
```

- 성공 `200`:

```json
{
  "message": "Login successful",
  "token": "<jwt>",
  "user": {
    "id": "1",
    "email": "local-account@example.test",
    "userType": "user",
    "createdAt": "2026-01-01T00:00:00.000Z"
  }
}
```

- 입력 누락/잘못된 필드 타입: `400 { "message": "Email and password are required" }`
- 미등록 계정·틀린 비밀번호: 동일 `401 { "message": "Invalid email or password" }`, 계정 존재 여부 노출 없음
- 로그인 오류는 폼의 로그인 실패로 처리; 보호 API의 세션 오류와 구분. 로그인 400/401에는 현재 code 없음

## 현재 사용자 — GET /api/auth/me

- 접근: 인증된 user/admin
- 요청: 공통 Bearer 헤더, body 없음
- 성공 `200`: `{ "user": <공통 사용자> }`
- 인증 실패: 아래 공통 `401` 코드. 계정 삭제·정규화 email/role 변경으로 기존 token 대조 실패 시 `token_revoked`
- 기존 password만 변경 시 모든 JWT 즉시 회수 기능 없음. 별도 refresh/회수 설계 미도입

## 인증·권한 오류와 화면 처리

| HTTP | code | 조건 | 소비자 처리 |
|---|---|---|---|
| 401 | authentication_required | 헤더 누락, Bearer 아닌 형식, token 없음, 추가 헤더 조각 | 보호 화면 세션 정리·로그인 경로 |
| 401 | invalid_token | 서명/검증 실패·변조·잘못된 JWT 또는 id/email/userType payload | 세션 정리·재로그인 |
| 401 | token_expired | JWT 만료 | 세션 정리·재로그인; 무한 재시도 제외 |
| 401 | token_revoked | 저장 계정 없음 또는 정규화 email/role 불일치 | 세션 정리·최신 계정으로 재로그인 |
| 403 | admin_required | 유효한 user가 관리자 전용 API 요청 | 로그인 유지·권한 부족 표시; 자동 로그아웃 제외 |

- 오류 형태: `{ "code": "<위 코드>", "message": "<설명>" }`. 프로그램 분기는 code 기준, message 문자열 비교 제외
- `500`·네트워크·사용자 조회 실패: 인증 무효/권한 부족으로 단정 금지; 오류 표시·명시적 재조회. 없는 사용자/역할 가짜 생성 없음
- 모든 보호 요청에 같은 인증 검사 적용. email/role 변경 후 이전 관리자 token도 관리자 API 접근 불가

## 관리자·조회 API 역할

| API | 허용 역할 | 요청·성공 형태 |
|---|---|---|
| GET /api/users | admin | body 없음, `200 <공통 사용자>[]` |
| GET /api/users/:id | admin | body 없음, `200 <공통 사용자>` |
| PUT /api/users/:id | admin | 아래 변경 요청, `200 { message, user: <공통 사용자> }` |
| DELETE /api/users/:id | admin | body 없음, `200 { message }` |
| POST /api/anomaly/evaluate-risk | user/admin | 로그 배열 위험도 계산; [별도 계약](anomaly.md) |
| GET /api/anomaly/realtime-stream | user/admin | 인증 SSE; [별도 계약](anomaly.md) |
| POST /api/anomaly/logs | admin | 기존 단건 ingest; [별도 계약](anomaly.md) |

- user 관리 API: authenticateToken → requireAdmin 순서, 유효 일반 사용자는 `403 admin_required`; `/me`는 일반 사용자 본인 조회에 재사용
- PUT 요청: `{ "email"?: string, "password"?: string, "userType"?: "user" | "admin" }`, 최소 한 필드 필수. 빈/형식 오류/지원 외 필드 `400 invalid_user_update`
- 정규화 email 중복: `400 email_already_in_use`; 대상 계정 없음 `404 { message }`
- 일반 사용자 본인 계정 수정 API·새 학습/평가/적용 API를 현재 구현으로 주장하지 않음. 향후 관리자 기능에도 서버 권한 검사 필요
- SSE: 최초 인증 실패는 JSON `401`이며 스트림 미개방. 개방 후 만료 시 종료, 송신 전 계정/email/role 재검사; 탈락하면 이벤트 미전달. Bearer 헤더 가능한 기존 연결 방식 유지
- 메모리 SSE의 영속 재생·중단 중 이벤트 복구 지원 없음; 연결/수신/표시/재연결 별도 검증

## 비활성 계정 생성

- `POST /api/auth/register`, `/api/auth/send-code`, `/api/auth/verify-code`: `410`

```json
{
  "code": "registration_disabled",
  "message": "Public registration and email verification are disabled."
}
```

- 계정 생성: 시작 시 로컬 `SEED_ADMIN_*`·`SEED_USER_*` 환경 설정만; SMTP·가입 메일·인증코드 발송 없음. 실제 환경값/비밀번호 출력 제외

## 구현·검증 경계

- 구현: `server/backend/src/middleware/auth.ts`·auth/user/anomaly routes 및 controllers
- 회귀: `server/backend/tests/integration.mjs`, 로컬 임시 계정/저장소. 무토큰·잘못된 헤더·변조·만료·잘못된 payload·권한 부족·계정 변경·SSE 거절/송신/회수
- 검증 명령: backend `npm ci`, `npm test`(build 포함). 원격 서버·고객 API·메일·실계정 검증 제외

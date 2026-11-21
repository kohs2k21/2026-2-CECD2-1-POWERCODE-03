Created: 2026-11-21T14:40:00+09:00
Updated: 2026-11-21T23:30:00+09:00
Author: Claude Code(WebUI builder)
Status: current — API·DB(migration 033)·설정 화면(설정 › 연동 관리 메일 알림 카드, 11절) 구현

# 메일 알림 계약

- 범위: 알림을 WebUI 알림 패널과 함께 **메일로도** 보낸다(사용자 11/21). 이 문서는 감지·중복 방지·발송·재시도·제한·SMTP 설정·비밀번호 저장·API·메일 내용·설정 화면(11절)을 정한다
- 사용자 결정(11/21): 기본 = 사건 단위 + 위험도 `위험`, 설정에서 바꿈 / 받는 사람 = 관리자가 넣는 주소 목록(계정과 무관) / SMTP·받는 사람·대시보드 주소를 모두 WebUI 설정에서 넣는다(서버 환경 변수 없음) / 보내는 때 = 새 사건 + 위험도 상승만 / 새 알림 시간당 10건, 넘으면 생략하고 다음 메일에 생략 수 / 고를 수 있는 종류 = 사건 + 묶이지 않은 판정(기본 꺼짐) / 보내는 중 멈춘 발송은 확인 불가로 두고 다시 보내지 않음
- 레거시(4ec85af `emailService.ts`)에서 SMTP 연결 생각만 가져왔다. 버린 것: 실패를 콘솔 출력·성공으로 바꾸는 동작, `rejectUnauthorized:false`, 값·코드 로그
- 구현: `backend/src/services/emailAlerts/`(`config.ts` 값 검사, `secretBox.ts` 비밀번호 암호화, `transport.ts` SMTP, `settingsStore.ts` 설정·전송 상태, `scan.ts` 감지, `outbox.ts` 발송·시험 발송·연결 확인·정리, `template.ts` 메일 문구, `runner.ts` 주기 작업), 경로 `backend/src/routes/emailAlertRoutes.ts`, 저장 migration `033_email_alerts.sql`(`infra/database/README.md` 033 절)
- 메일은 알림 패널과 별개다: 패널의 사용자별 설정·읽음과 무관하고, 패널에 보이는 사건 항목(`notifications.md`)과 같은 사건 흐름·위험도 판단을 쓴다

## 1. 주기 작업

- API 프로세스 안에서 30초마다: 감지(2절) → 발송(4절). 하루 한 번 정리(90일 지난 발송 행, 30일 갱신 없는 알림 대상). 회차는 겹치지 않는다
- API 프로세스가 여럿이어도 감지는 `pg_try_advisory_xact_lock(81743, 0)`을 잡은 한 곳만(못 잡으면 그 회차 건너뜀), 발송은 행마다 `FOR UPDATE SKIP LOCKED`
- 실패는 코드 한 줄만 로그에 남긴다(`[email-alerts] tick failed: <SQLSTATE 또는 코드>`, 같은 코드가 이어지면 다시 남기지 않음). SQL 원문·값·비밀번호는 남기지 않는다
- `DATABASE_URL` 없이 띄운 API(계정만)에서는 돌리지 않는다

## 2. 감지

- 처리 경계 = `ops_email_alert_cursor.processed_through_seq`(`run_decision_events.id`, id 순서 = 커밋 순서). 회차마다 경계 다음의 **최종** 판정 이벤트를 id 순으로 최대 500건 읽는다. 중간 판정 기록은 보지 않는다(사건 구성원은 최종 판정)
- 규칙: 이상(`status<>unavailable` ∧ `evidence.anomalous=true`) ∧ 저장된 v4 `composite.risk`가 `rated`이고 위험도 ≥ 하한. 알림 패널의 판정 기준 "최종(부분 포함)" + 위험도 미정 제외와 같다(`notifications.md` 이벤트 판단). 위험도 미정은 보내지 않는다
- **사건 소속 확정**: 규칙을 통과한 판정은 그 원본(`data_executions.source_id`)의 사건 묶음 경계 W = 기준 버전마다 최신 `cursor_to`의 최댓값(끝 포함, `incidents.md` 2.1)이 그 이벤트 id 이상일 때만 판단한다. W는 구성원 여부와 **같은 문장(같은 스냅숏)**에서 읽는다(따로 읽으면 사이에 커밋된 묶음 회차의 구성원을 놓친다). 읽기는 `run_incident_runs_cursor_idx`로 기준 버전을 건너뛰며 버전마다 1행(`ORDER BY cursor_to DESC LIMIT 1`)만 본다. 묶음 작업은 같은 트랜잭션에서 그 범위의 구성원을 넣으므로 W 안이면 소속이 정해져 있다. 아직이면 거기서 멈추고(경계를 그 앞에 둠) 다음 회차에 다시 본다. 규칙을 통과하지 못한 판정은 기다리지 않는다
  - 사건 묶음 작업(`incidents` 서비스)이 멈추면 W가 멈춰 메일도 멈춘다(설정 응답 `processing`으로 보임). 실패를 성공으로 바꾸지 않는다
  - 한계: 기준 버전(`criteria_version`)이 바뀐 직후에는 W가 옛 버전의 큰 `cursor_to`일 수 있다(원본별 최대값). 새 버전이 아직 읽지 않은 판정은 묶이지 않은 판정으로 판단될 수 있다(드묾, `incidents.md` 열린 항목 12와 같은 축)
- 구성원이면 **사건 흐름 알림**(설정 `incident`), 아니면 **묶이지 않은 판정 알림**(설정 `decision`, 키 `job:<detect 작업 id>` 또는 작업 없는 판정 `decision:<판정 id>` — `notifications.md` 항목 키와 같음)
  - **알림 패널과 다른 점(묶음 단위)**: 패널은 작업 하나의 최종 판정 중 하나라도 사건 구성원이면 그 작업 항목(`job:`)을 통째로 숨기고 중간 판정도 본다. 메일은 **최종 판정마다** 따로 판단한다 — 같은 작업의 재시도 최종 판정 중 구성원이 아닌 것은 묶이지 않은 판정 메일이 될 수 있고(작업 키는 같아 위험 단계별로 한 번), 중간 판정은 보지 않는다
  - 흐름 = 구성원 사건에서 병합(양방향)·이어짐(양방향)으로 닿는 사건 전부, 흐름 id = 알림 패널과 같은 머리(닿은 사건 중 `(opened_at, 병합 뿌리 id)`가 가장 작은 사건의 병합 뿌리). 알림 대상 키 `incident:<흐름 id>`
  - **흐름 머리가 바뀌어도 다시 보내지 않는다**: 흐름의 사건을 모두 `ops_email_alert_incidents`에 알림 대상과 매어 둔다. 흐름의 어느 사건이든 이미 매여 있으면 그 알림 대상을 쓴다(나중에 더 이른 사건과 병합돼 머리가 바뀐 경우). 둘 이상이면 먼저 만든 것
- 보내는 때(위험 단계 = 참고 1·주의 2·위험 3): 알림 대상이 처음 생길 때 `opened`, 보낸 단계보다 높은 단계가 올 때 `risk_raised`. 구성원이 늘기만 하면 보내지 않는다. 사건 위험도 = 통과한 구성원 위험도 최고값(알림 패널과 같음)
- 켜기(꺼짐 → 켜짐): 경계를 그때의 최대 판정 이벤트 순번으로 옮긴다(지난 판정은 보내지 않음). 규칙 변경은 그 뒤 판정부터
- 경계 이동과 알림 대상·대기 행 INSERT는 한 트랜잭션이다. 재시작해도 같은 범위를 다시 판단하지 않는다
- **정리 뒤 다시 보냄**: 알림 대상은 30일 동안 갱신(구성원·위험 판정)이 없으면 매핑과 함께 지운다. 그 뒤 같은 흐름에 규칙을 통과한 구성원이 오면 새 알림 대상이 되어 `새 사건`으로 다시 보낸다(한 달 넘게 조용하던 흐름을 다시 알리는 것으로 본다). 발송 기록은 90일 남는다

## 3. 중복 방지·발송 제한

- 대기 행 = 받는 사람마다 1행. `UNIQUE (alert_id, risk_rank, recipient)` — 같은 알림 대상의 같은 위험 단계는 한 사람에게 한 번만 대기열에 들어간다(재시작·동시 실행에도). 메일 `Message-ID` = `<발송 id@보내는 주소 도메인>`
- 발송 제한: 새 알림(알림 대상·위험 단계 쌍, 시험 제외, 생략 제외)이 최근 1시간에 10건이면 그 뒤 알림은 `suppressed`(발송 제한으로 생략)로 남기고 보내지 않는다. 다음에 대기열에 들어가는 메일에 "그동안 생략된 사건·판정 수"를 적는다(마지막으로 대기열에 들어간 알림 뒤의 생략 수). 받는 사람은 최대 10명이라 최악 시간당 100통
- 끄기: 대기·다시 시도 대기 발송을 `failed`(`email_alerts_disabled`)로. 받는 사람에서 빠진 주소의 대기 발송도 `failed`(`email_recipient_removed`)

## 4. 발송·재시도

- 상태: `queued` 대기 → `sending` 보내는 중(lease 5분) → `sent` 보냄 | `retrying` 다시 시도 대기 | `failed` 실패 | `unknown` 확인 불가(다시 보내지 않음) | `suppressed` 발송 제한으로 생략
- 가져오기: 보낼 차례인 행 최대 10건을 `FOR UPDATE SKIP LOCKED`로 `sending`·시도 수 +1·lease를 커밋한 뒤 보낸다. 설정이 꺼져 있으면 보내지 않는다
- 다시 시도(1·5·15·60분 뒤, 5번째 시도까지): **연결 전으로 확실한 실패**만 — 접속 거절·경로 없음(`ECONNREFUSED`·`EHOSTUNREACH`·`ENETUNREACH`·`EADDRNOTAVAIL`)·이름 풀기 실패, 연결·인사·STARTTLS 협상 시간 초과(nodemailer `timeoutType` = `CONNECT_TIMEOUT`·`GREETING_TIMEOUT`·`UPGRADE_TIMEOUT`), 서버의 4xx 응답(받지 않았다는 응답). 5번째 뒤와 영구 실패(인증·TLS·인증서·5xx·받는 사람 거절·메시지 오류·설정·키 문제)는 `failed`
- **결과 모름 → `unknown`(`smtp_result_unknown`), 다시 보내지 않음**: 보내는 중 **응답 번호 없이** 연결 뒤 끊김(`ECONNRESET`·`EPIPE`·소켓 오류·연결 닫힘)·소켓 시간 초과(`SOCKET_TIMEOUT`, 단계 없는 시간 초과 포함)·스트림 오류·알 수 없는 오류, 시험 발송의 20초 제한 초과. 서버가 이미 받았을 수 있어서다(사용자 11/21: 중복 없음, 누락 가능). TLS 오류(`ETLS`·`EREQUIRETLS`·`CERT_*`·`ERR_TLS_*`·`ERR_SSL_*`·`ERR_OSSL_*` 등, `originalCode` 포함)는 **기본 영구 실패**(`smtp_tls_failed`, 연결 상태 `error`)다 — 잘못된 포트·보안 방식(평문 서버에 TLS 접속 → `ERR_SSL_WRONG_VERSION_NUMBER`)·버전·암호 불일치(`…_ALERT_HANDSHAKE_FAILURE`·`…_ALERT_PROTOCOL_VERSION`·`UNSUPPORTED_PROTOCOL`) 같은 설정 문제를 결과 모름으로 숨기지 않기 위해서다. 예외는 협상이 끝난 뒤 레코드 계층에서만 나는 MAC·복호 실패(허용 목록: `ERR_SSL_DECRYPTION_FAILED_OR_BAD_RECORD_MAC`·`ERR_SSL_DECRYPTION_FAILED`·`ERR_SSL_SSLV3_ALERT_BAD_RECORD_MAC`·`ERR_SSL_TLSV1_ALERT_DECRYPTION_FAILED`, Node 22 = OpenSSL 3.0 이유 문자열) — DATA 중에도 날 수 있어 보내기 중이면 그 행만 결과 모름(연결 확인에서는 영구 TLS 실패)
  - 판단 근거: nodemailer는 연결 뒤 네트워크 오류에 단계 정보를 남기지 않고(`command`가 늘 `CONN`) 모든 시간 초과를 `ETIMEDOUT` + `timeoutType`으로 낸다. 그래서 DATA를 시작했는지는 알 수 없고, 연결 전으로 확실한 경우만 다시 보낸다(연결 직후 인사 전에 닫힌 경우도 결과 모름 — 보수적)
  - 결과 모름은 연결 상태(전송 상태)에 남기지 않고, 같은 묶음의 다음 행은 계속 보낸다(행마다 새 연결)
  - 연결 확인(메일 없음)에서는 같은 끊김·시간 초과를 일시 실패(`smtp_connection_failed`·`smtp_timeout`)로 본다
- 연결 단계 실패(접속·TLS·인증)면 그 묶음의 남은 행도 같은 결과로 두고 멈춘다(같은 실패를 행마다 기다리지 않게)
- **보내는 중 멈춤**: lease가 지난 `sending` 행(프로세스 종료 등)은 `unknown`(`send_interrupted`)으로 바꾸고 다시 보내지 않는다(중복 없음, 누락 가능 — 사용자 11/21). SMTP가 받은 뒤 기록 전에 멈춘 경우와 구분할 수 없어서다
- 오류는 코드만 저장한다(`error_code` + SMTP 응답 번호 `smtp_code`). 서버 응답 원문·주소·비밀은 저장하지 않는다. 코드: `smtp_result_unknown`·`smtp_auth_failed`·`smtp_tls_failed`·`smtp_timeout`·`smtp_connection_failed`·`smtp_host_unresolved`·`smtp_host_blocked`·`smtp_recipient_rejected`·`smtp_rejected`·`smtp_temporary_failure`·`smtp_message_failed`·`smtp_error`·`smtp_not_configured`·`smtp_settings_incomplete`·`smtp_password_missing`·`smtp_password_unreadable`·`email_secret_key_unavailable`·`email_secret_key_invalid`·`send_interrupted`·`email_alerts_disabled`·`email_recipient_removed`

## 5. SMTP·전송 상태

- 설정 값(관리자, 8절 PUT): 호스트 = 호스트 이름 또는 IP(꺾쇠 없이)만 — 주소 형식(`smtp://`)·경로·포트 붙임·계정(`@`)은 형식 오류. 포트 = **25·465·587·2525만**(표준 SMTP 포트, 다른 포트로 내부 서비스를 두드리지 못하게. 033 CHECK). 보안 = `tls`(접속부터 TLS) | `starttls`(STARTTLS 필수 — 서버가 안 주면 실패, 평문으로 내려가지 않음). **평문 SMTP 없음**(비밀번호를 평문으로 보내지 않게). 계정 없음 = 인증 없이 보냄(내부 중계, 그래도 TLS 필수)
- 연결 대상 보호: 금지 대역 — 지정 안 됨 0.0.0.0/8, 루프백 127/8, 링크 로컬 169.254/16(AWS·GCP·Azure 메타데이터 169.254.169.254 포함)·fe80::/10, 클라우드 메타데이터 100.100.100.200(Alibaba)·fd00:ec2::254(AWS IPv6), 멀티캐스트·예약 224/4·240/4·ff00::/8, IPv4를 품은 IPv6 형식 통째로(IPv4 호환 ::/96 — ::·::1 포함, IPv4 변환 ::ffff:0:0:0/96, NAT64 64:ff9b::/96), IPv4 대응 IPv6(::ffff:a.b.c.d·16진 표기)는 안의 IPv4로 검사 — 와 `localhost`는 저장 때 `smtp_host_blocked`. 사설 대역은 허용(내부 메일 중계). 호스트 이름은 보낼 때마다 직접 풀어(5초) 금지 대역 주소를 빼고 남은 첫 주소에 연결한다. 인증서는 원래 이름으로 검사(SNI·servername) — 확인과 연결 사이에 이름이 바뀌어도 확인한 주소로만 간다
- 전송: nodemailer SMTP 1개(연결 풀·프록시·다른 전송 없음), 인증서 검사 켬(TLS 1.2 이상), 연결·인사 10초·소켓 20초, 메시지의 파일·URL 읽기 끔. 보내는 사람 = `ESB 알림 <보내는 주소>`, 머리말 `Auto-Submitted: auto-generated`
- 전송 상태(`transport.status`, 설정에서 만듦): `not_configured`(호스트 없음) / `invalid`(값 빠짐 `smtp_settings_incomplete`, 계정은 있는데 비밀번호 없음 `smtp_password_missing`, 키 없음·틀림 `email_secret_key_unavailable`·`email_secret_key_invalid`, 지금 키로 풀 수 없는 비밀번호 `smtp_password_unreadable`) / `configured`. 그 위에 마지막 연결 확인·발송 결과: `auth_failed`(인증 실패) / `error`(연결·TLS 등, `code`). 확인 결과는 그 결과를 낸 SMTP 설정일 때만 남는다(SMTP 값이 바뀌면 지움). 발송·시험 발송 성공은 `configured`로 되돌린다. 받는 사람 거절은 전송 상태를 바꾸지 않는다
- 저장 직후 연결 확인: SMTP 값(호스트·포트·보안·계정·비밀번호·보내는 주소)이 바뀌었고 보낼 수 있는 설정이면 접속·TLS·로그인까지 해 본다(메일은 보내지 않음, 20초 안). **실패해도 저장값은 그대로 둔다** — 일시 장애일 수 있고 관리자가 값을 다시 넣지 않아도 되게. 결과는 응답 `verify`와 전송 상태로 보인다(성공으로 바꾸지 않음)
- 켜기 전제: 전송 상태가 `configured`·`auth_failed`·`error`(값이 다 있고 비밀번호를 풀 수 있음) — 아니면 409. 연결 확인 실패는 막지 않는다(보내면 실패가 그대로 기록된다)

## 6. 비밀번호 저장

- **쓰기 전용**: 응답에는 `passwordSet`(true/false)만. PUT에서 `password` 생략 = 저장값 유지, `null` = 지움, 문자열 = 새 값(1~512자, NUL·줄바꿈 금지, 다듬지 않음). 계정을 비우면(`username: null`) 비밀번호도 지운다. 비밀번호는 응답·오류·로그에 넣지 않는다
- 저장 형식: AES-256-GCM, 쓸 때마다 새 무작위 IV(12바이트), AAD = 용도 이름. `v1.<키 id 8 hex>.<IV>.<tag>.<암호문>`(base64url). 키 id = sha256(키) 앞 8자리(키가 바뀌었는지 풀기 전에 안다). DB는 이 형식만 받는다(033 CHECK — 평문 저장 불가)
- 키는 DB 밖(DB 백업만으로 풀 수 없음). 출처(앞이 이김):
  1. 환경 변수 `SETTINGS_SECRET_KEY`(base64 32바이트). 형식이 틀리면 `email_secret_key_invalid`(파일로 넘어가지 않음)
  2. 키 파일 `SETTINGS_SECRET_KEY_FILE`, 기본 = API 데이터 폴더(`USER_DATA_PATH`와 같은 폴더, compose `AUTH_DATA_DIR` → `/app/data/settings-secret.key`). 없으면 **비밀번호를 처음 저장할 때 한 번 만든다**: 같은 폴더의 임시 파일(권한 0600)에 다 쓰고 fsync한 뒤 `link`로 제자리에 건다(원자적 — 도중에 멈춰도 빈·반쪽 키 파일이 남지 않음, 임시 파일은 지움). 이미 있으면(`EEXIST`, 동시 생성) 먼저 걸린 완성된 키를 읽는다. 읽기·상태 확인은 만들지 않는다. 데이터 폴더가 하드 링크를 지원하지 않으면 키 파일을 만들 수 없어 비밀번호 저장이 `email_secret_key_unavailable`로 실패한다 → `SETTINGS_SECRET_KEY`를 쓴다
  - 근거: 운영 부담이 가장 적다(설정할 값 없음, 이미 영속 bind mount인 계정 폴더). 키와 암호문이 다른 저장소(파일 / DB)에 나뉜다
- 키가 없고 만들 수도 없으면 비밀번호 저장은 503 `email_secret_key_unavailable`(아무것도 바꾸지 않음). 평문 저장·성공 대체 없음
- 교체·분실: 새 키(파일 교체 또는 환경 변수)로 API를 다시 시작한 뒤 비밀번호를 다시 넣는다. 그 전까지 전송 상태 `invalid`·`smtp_password_unreadable`, 발송은 실패로 기록. 옛 키로 푸는 기능은 없다. 키 파일은 `users.json`과 함께 백업한다

## 7. 받는 사람·대시보드 주소

- 받는 사람: 최대 10개, 주소마다 ASCII `local@domain`(도메인 라벨 2개 이상, local 64자·전체 254자 이하, 공백·쉼표·꺾쇠·따옴표·줄바꿈 없음 — 헤더에 주소 1개로만 들어감), 앞뒤 공백 제거·도메인 소문자, 대소문자 무시 중복 금지. 오류는 그 위치 `index`와 함께
- 보내는 주소: 같은 형식 1개
- 대시보드 주소(`publicBaseUrl`, 메일 링크 앞부분): http(s) + 호스트 + 선택 경로, 계정·쿼리·조각 없음, 끝 `/` 제거, 512자 이하. 켜려면 필요(400 `public_base_url_required`). 링크 = `<주소>/analysis/incidents/<흐름 id>`(사건), `<주소>/analysis/d/<판정 id>`(판정). 로그인 뒤 그 주소로 돌아온다(휴대폰은 사건·판정 상세가 열림)

## 8. API(인증 필수, 관리자만 — 일반 사용자 403 `admin_required`, 오류 `{code[, index]}`)

| 요청 | 응답·동작 |
|---|---|
| `GET /api/email-alerts/settings` | `{enabled, rules:{kinds:("incident"\|"decision")[], minRiskLevel:"info"\|"warning"\|"critical"}, recipients[], smtp:{host, port, security:"tls"\|"starttls"\|null, username, passwordSet, fromAddress}, publicBaseUrl, transport:{status, code, checkedAt}, processing:{processedThroughSeq, latestSeq, armedAt, lastTickAt}, rateLimit:{newAlertsPerHour:10, newAlertsLastHour}, limits:{recipients:10, ports:[25,465,587,2525], security:["tls","starttls"]}, isDefault, updatedAt, updatedBy}`. 저장값 없으면 꺼짐·`["incident"]`·`critical`·받는 사람 없음·SMTP 없음(`isDefault:true`). 순번은 문자열(bigint) |
| `PUT /api/email-alerts/settings` | 본문 `{enabled, rules:{kinds, minRiskLevel}, recipients[], smtp:{host, port, security, username, fromAddress[, password]}, publicBaseUrl}` 전체(값 `null` = 비움, `password`만 생략 가능 — 6절). 마지막 저장이 이김. 응답 = GET + `verify:{result:"ok"\|"auth_failed"\|"error"\|"skipped", code}`(5절) |
| `POST /api/email-alerts/test` | 본문 `{to}` — **저장된 받는 사람 중 하나만**(대소문자 무시). 시험 메일 1통을 바로 보내고(20초 안) 결과를 그대로 돌려준다: 200 `{deliveryId, status:"sent"\|"failed"\|"unknown", errorCode, smtpCode, finishedAt}`(실패도 200 + `failed`, 보내는 중 끊김·20초 초과는 `unknown` + `smtp_result_unknown` — 받았는지 모름, 성공 대체 없음). 전체 30초에 1번. 켜기와 무관(켜기 전 확인용). 발송 기록에 `kind:"test"`로 남음 |
| `GET /api/email-alerts/deliveries?limit=1~50&cursor=` | 최근 발송(만든 시각 → id 내림차순, 기본 20). `{items:[{id, kind:"incident"\|"decision"\|"test", reason:"opened"\|"risk_raised"\|"test", alertKey, targetId(흐름 id·판정 id), riskLevel, recipient, status, attempts, errorCode, smtpCode, createdAt, sentAt, finishedAt, nextAttemptAt(다시 시도 대기일 때만)}], nextCursor}`. 메일 본문·payload는 내지 않음 |

- 400 code: `invalid_email_settings`(모양·모르는 키)·`invalid_email_recipient`(+`index`)·`duplicate_email_recipient`(+`index`)·`too_many_email_recipients`·`email_recipients_required`·`email_kinds_required`·`invalid_smtp_host`·`smtp_host_blocked`·`invalid_smtp_port`·`invalid_smtp_security`·`invalid_smtp_username`·`invalid_smtp_password`·`invalid_email_from`·`invalid_public_base_url`·`public_base_url_required`·`invalid_test_request`·`unknown_email_alert_filter`·`invalid_limit`·`invalid_cursor`·`invalid_json`. 413 `request_too_large`(본문 16KB)
- 본문 파서 400 code(공통): `unsupported_charset`·`unsupported_encoding`·`request_aborted`·그 밖 4xx `invalid_request`
- 401 `authentication_required`·`invalid_token`·`token_expired`·`token_revoked`, 403 `admin_required`(auth.md)
- 404 `email_recipient_not_listed`, 409 `email_transport_not_configured`(켜기·시험 발송인데 보낼 수 없는 설정), 429 `email_test_rate_limited`, 503 `email_secret_key_unavailable`·`email_secret_key_invalid`(비밀번호 저장)·`email_alert_storage_unavailable`(저장소 오류, 원문 비노출)
- 발송 기록 `errorCode`(`/deliveries`·`/test` 응답)는 4절 코드 목록(`smtp_result_unknown`·`send_interrupted` 포함). 시험 발송 결과 `errorCode`는 그중 전송 코드와 설정·키 코드(`smtp_not_configured`·`smtp_password_unreadable`·`email_secret_key_unavailable` 등)
- 시각은 UTC 마이크로초(`…T01:02:03.000000Z`). 발송 목록 커서 = base64url JSON `[만든 시각, 발송 id]`

## 9. 메일 내용(`template.ts`, 새 문구 — 사용자 승인 대상)

- 일반 텍스트 + 단순 HTML. 모든 값은 HTML escape, 제목은 줄바꿈 제거·120자. 사실은 대기열에 넣을 때 payload(8KB 이하)에 고정한다(다시 시도해도 같은 내용). 링크 앞부분만 보낼 때의 설정값
- 사건: 제목 `[ESB 알림] <위험|주의|참고> · 사건 · <응답코드|채널> <값>[ · 위험도 상승]`. 본문 `새 사건`|`위험도 상승`, `기준: <기준>`, `위험도: <단계>`, `묶인 판정 N건 · 거래 M건`(사건 상세와 같은 건수), `시작: YYYY-MM-DD HH:mm KST[ (판정 시각 기준)|(일부 판정 시각 기준)]`, `사건 보기: <링크>`. 긴 값은 `값 생략(긴 값)`
- 묶이지 않은 판정: 제목 `[ESB 알림] <단계> · 판정[ · 프로세스 <ID>][ · 위험도 상승]`. 본문 `새 판정`|`위험도 상승`, `결과: 이상|이상 징후`, `위험도`, `프로세스`, `판정 시각`, `판정 보기: <링크>`
- 생략 줄: `발송 제한으로 생략된 사건 N건이 있습니다.`·`발송 제한으로 생략된 판정 N건이 있습니다.`(0이면 없음). 꼬리 `ESB 이상 징후 탐지 대시보드 메일 알림 설정에 따라 보낸 메일입니다.`
- 시험: 제목 `[ESB 알림] 시험 메일`, 본문 `메일 알림 시험 메일입니다.`
- 넣지 않는 것: 원문 로그·BODY, 거래 ID, 서버 오류 원문, 비밀. 기준값·프로세스 ID는 이상 분석 화면에 보이는 값과 같다(200자에서 자름)

## 10. 시험·검증 경계

- 단위(`backend/tests/email-alerts.test.mjs`, 가짜 값만): 값 검사·금지 대역·비밀번호 형식·키 출처·전송 옵션·오류 분류·재시도·문구·전송 상태·레거시 금지 패턴
- 통합(`backend/tests/email-alerts.integration.mjs`, CI "Email alerts API and runner under runtime role", 실제 PostgreSQL + `powercode_api` + **가짜 전송**): 2~8절 흐름 전체
- DB(`infra/tests/erd-v03-email-alerts.mjs`): 033 표·권한·CHECK
- 실제 메일 발송·실제 SMTP 서버 연결은 시험하지 않았다(사용자 승인 뒤 설정 화면 [시험 발송]으로)
- 화면(`frontend/tests/settings-email.mjs`, 가짜 fetch만): 관리자/일반 사용자, 켜기 잠금, 받는 사람 검사, 저장 실패 시 서버 값 유지·키 오류 문구, 비밀번호 비노출, 저장 결과, 시험 발송 확인·결과, 발송 상태 문구, 사건 묶음 처리 대기

## 11. 설정 화면(설정 › 연동 관리 › `이메일 (SMTP) 연동` 카드, 문구 사용자 승인 11/21)

- 구현: `frontend/src/features/settings/email/`(`emailAlertsApi.ts` 요청·응답·받는 사람 검사·처리 대기 판단, `emailAlertLabels.ts` 문구, `useEmailAlerts.ts` 조회·저장, `EmailAlertsCard.tsx` 카드). PC 화면(휴대폰 최적화 없음)
- 관리자만: 일반 사용자는 카드에 `메일 알림 설정은 관리자만 바꿀 수 있습니다.` 한 줄만 보이고 메일 알림 API를 부르지 않는다. 조회 key는 계정별(`email-alert-settings`·`email-alert-deliveries`), 주기 조회 없음 — 저장·시험 발송 뒤 다시 조회
- 카드 설명 `위험 사건 알림을 메일로 보냅니다.`. 연동 관리 상단 `저장 미지원`은 두지 않는다(이 카드에 자체 [저장], Slack·Webhook 카드는 카드 안 미지원 표시, `navigation.md` 설정 행)
- 전송 상태 배지(`transport.status`): `configured` 설정됨 · `not_configured` 미설정 · `invalid` 설정 오류 · `auth_failed` 인증 실패 · `error` 연결 오류. 옆에 `마지막 확인 <시각>`(checkedAt 있을 때), 코드가 있으면 ` · <code>`
- `메일 알림 켜기` 스위치: 꺼진 상태에서 켜기는 전송 상태가 설정됨·인증 실패·연결 오류일 때만(5절 켜기 전제, 아니면 API 409). 끄기는 언제나
- SMTP 입력: `SMTP 호스트`, `포트`(응답 `limits.ports` 선택, 빈 값 = 없음), `보안`(`TLS 접속` / `STARTTLS 필수`, 빈 값 = 없음), `발신 계정`(SMTP 계정), `비밀번호`, `보내는 주소`, `대시보드 주소`(도움말 `메일 링크 앞부분 (예: http://서버:8080)`). 값 검사는 서버(오류 코드 표시)
- 비밀번호(6절 쓰기 전용): 입력칸은 늘 비어 있고 새 값만 받는다. 저장된 비밀번호가 있으면 자리표시 `저장됨 · 바꿀 때만 입력`과 [비밀번호 지우기](다음 저장에 `password:null`). 입력 없으면 `password` 생략(유지). 발신 계정이 비면 비밀번호 칸을 잠그고 보내지 않는다(서버가 함께 지움)
- 규칙: `보낼 알림` = `사건`·`묶이지 않은 판정` 체크, `위험도 하한` = 알림 설정과 같은 `위험`·`주의 이상`·`참고 이상`
- `받는 사람` 목록 `N/10`(응답 `limits.recipients`), [추가]·[삭제](이름 `<주소> 삭제`). 추가할 때 검사(7절과 같은 규칙, 앞뒤 공백 제거·도메인 소문자): `메일 주소 형식이 아닙니다.` · `이미 있는 주소입니다.`(대소문자 무시) · `최대 10개까지 넣을 수 있습니다.`
- [저장]: 입력값 전체를 PUT(8절). 바뀐 것이 없거나 저장 중이면 잠금(저장 중에는 입력도 잠금). 성공하면 응답으로 화면을 바꾸고 결과 줄 — `verify.result` ok `저장됨 · 연결 확인 성공` / auth_failed·error `저장됨 · 연결 확인 실패 · <code>` / skipped `저장됨`. 실패하면 서버 값(전송 상태·저장값)은 그대로 두고 입력값은 남겨 다시 저장할 수 있게 하며 `메일 알림 설정을 저장하지 못했습니다` + 코드(키 오류 `email_secret_key_unavailable`·`email_secret_key_invalid`는 `암호화 키를 쓸 수 없어 비밀번호를 저장하지 못했습니다.`)
- [시험 발송](받는 사람마다): 저장된 받는 사람이고 켤 수 있는 전송 상태일 때만. 확인 창 `<주소>로 시험 메일을 보냅니다.` [보내기]/[취소]. 결과 줄 — sent `시험 발송 완료` / failed `시험 발송 실패 · <errorCode>` / unknown `확인 불가`(발송 상태 문구 그대로, 실패·성공으로 바꾸지 않음) / 요청 오류(404·409·429·503) `시험 발송 실패 · <code>`
- `최근 발송` 표(20건): `시각`(만든 시각) · `종류`(`사건`·`판정`·`시험`) · `받는 사람` · `결과`. 상태 문구 queued `대기` · sending `보내는 중` · sent `보냄` · retrying `다시 시도 대기` · failed `실패` · unknown `확인 불가` · suppressed `발송 제한으로 생략`. 없으면 `보낸 메일이 없습니다.`
- `사건 묶음 처리 대기 중`(2절 사건 소속 확정): 켜져 있고 처리 경계 < 최신 판정 순번인데, 주기 작업이 다시 돈 뒤(`lastTickAt` 바뀜)에도 경계가 같은 자리에 5분 이상 머물면 표시. 응답에 "언제부터 머물렀나"가 없어 화면이 본 조회 사이(계정별, 화면을 다시 열어도 이어서)에서만 잰다 — 처음 본 조회에서는 표시하지 않는다

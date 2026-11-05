# Detector 판정 구조 계약 (초안, Issue #92)

- 계약 버전: `detection-decision-v1`. 정의 대상: 방법 분류(하드룰(화면: 이상 규칙)·소프트룰(화면: 이상 징후 규칙)·ML(화면: 머신러닝 모델))와 식별자, `detectionPolicy` v2, 조합 규칙, 판정 등급(이상·이상 징후), 위험도, 룰 세트 리비전 기록, evidence `schemaVersion` 4, 중간 판정 기록
- 소유: Detector. 공통 계약이므로 바꿀 때는 사용자 결정과 Trainer·API·WebUI 조율이 필요하다
- 근거 코드: origin/dev `768d92f`(#94 병합 `935e5eb` 포함). 코드 행 번호(file:line)는 이 커밋 기준이며, 인용한 코드 파일은 `7f24e03`(이전 기준)과 `768d92f` 사이에 바뀌지 않아 행 번호가 같다. 현행 계약은 행 번호 대신 dev 기준 문서·절·필드 이름으로 가리킨다. #94 병합으로 사라진 #74 초안 문장은 "#94 병합 전 판본(`7f24e03`)"과 절 이름으로 표기한다
- 상태: 초안. 사용자 결정 질문 U1~U12는 **11/03 사용자가 모두 추천안으로 확정**했다(17.1 결정 결과). 확정 결정은 1절 요약 표에 날짜·출처와 함께 적었다. D1~D17은 Detector가 정하는 기술 항목(사용자 결정 아님, 17.2)이며 이견이 없으면 구현 기준이다
- 표기
  - **[확정 MM/DD]**: 사용자 결정. 출처는 요약 표에 적는다
  - **[확정 11/03 Un]**: 11/03 사용자 답변("추천대로")으로 확정된 17.1 결정 Un
  - **[코드]**: 현재 코드 사실(file:line)
  - **[제안 Dn]**: Detector가 정하는 기술 항목(Detector 결정, 사용자 결정 아님). 17.2에 정보로 제공하며 이견이 없으면 이대로 진행
  - **[#94]**: Trainer Issue #94(룰·모델 분리 계약)가 정하는 항목. 이 문서는 참조만 한다
  - **[#112 정정 11/04]**: #112 구현 확인 정정 11/04 사용자 승인. 구현 중 확인한 어긋남을 고친 항목이며 새 결정 번호(C·U)는 아니다. 기술 항목의 정정은 해당 Dn 행에 '#112 구현 확인 반영 11/04'로 적는다(Detector 결정)
- 범위 밖
  - 룰 세트 저장 형식(리비전 번호·내용 해시·리비전별 스냅샷), 소프트룰 등록 필드 목록·수정 제약, 모델 버전·교체·비교 [#94]
  - 방법 카탈로그 항목·스키마 [#94]
  - 알림 묶음, 사용자별 알림 필터 저장·평가(WebUI·API)
  - ML 위험도 정책 연구(보류)

## 1. 확정 결정 요약

| # | 결정 | 날짜·출처 |
|---|---|---|
| C1 | 탐지 대분류는 **룰(하드룰·소프트룰)과 ML 모델**로 분리(하드룰·소프트룰·ML 세 갈래) | 11/02 사용자 탐지 구조 개편 지시 |
| C2 | 통계 SR(R00~R23)·PROCESS SR 프로토타입은 새 구성·화면·카탈로그에서 제외(연구 기록 보존) | 11/02 사용자 답변 Q5 |
| C3 | (1.1 A1 참조 — 식별자 형식은 11/02 세션 합의였고 11/03 U1로 사용자 확정) | — |
| C4 | 하드룰 = 내장 목록 + 켜기·끄기만. 조건·위험도는 읽기 전용. 끄기는 허용하되 경고 + 확인 체크 필수(R1) | 11/02 사용자 답변 Q3, 0단계 R1 |
| C5 | 소프트룰 = 등록 필드 선택 + 조건(≥ > ≤ <) + 임계값·단위 + 위험도(참고·주의·위험 중 사용자 선택, R4). 대상 필드는 변경 불가(R2) | 11/02 사용자 답변 Q4, 0단계 R2·R4 |
| C6 | 룰 설정은 버전 관리 없이 [저장] 즉시 운영 적용. 조건: **판정마다 그 시점 룰 세트 리비전 기록, 진행 중 판정은 시작 시점 룰 고정, DB에 룰 변경 이력 저장**. (리비전마다 룰 전체 상태 스냅샷을 저장하는 것은 확정이 아니라 WebUI 설계안의 전제 — 아래 출처) | 확정분: 11/02 사용자 답변(룰 설정 버전 없음), 0단계 "저장 = 즉시 운영 적용"과 Detector 필수 조건(리비전 기록·시작 시점 고정·DB 룰 변경 이력) 승인 / 전제(확정 아님): "리비전별 룰 상태 스냅샷"은 R3 '이 상태로 되돌리기'를 위한 WebUI 설계안의 전제 |
| C7 | 판정 표현: 하드룰 = **이상**, 소프트룰·ML = **이상 징후**. 이상 분석에서 구분 표시 | 11/02 사용자 정의 표·답변 |
| C8 | 조합 규칙 4.0: 대분류마다 n개 중 k개 이상 이상이면 이상(기본 k=1), 대분류끼리는 OR, 정상은 활성 방법이 모두 성공했을 때만, 방법 0개·k>n 구성 거부, 완료/부분/판정불가는 방법 단위로 계산. 기존 구성 결과 불변, 행동 변경 1·2 포함 | 10/21 사용자 Q1 A(종합 판정 조합 규칙) |
| C9 | 조합 정책은 같은 키 `detectionPolicy`의 v2 객체, 새 구성은 mode 입력 없이 활성 방법 목록 기준(mode는 파생 표시값) | 10/21 사용자 Q5 A·키 이름 확정 |
| C10 | v1·v2 형식 키를 한 객체에 섞으면 400 `invalid_detection_policy` | #74 계약 초안의 [확정 10/21] N16 기록(#94 병합 전 판본 `detection-config-version.md` "(A) 학습" 절, dev에서는 `model-version.md` "(A) 학습" 절 `[유지 #74 N16]`). 10/21 결정 기록에는 결정 D6 참조만 있음 |
| C11 | 점진 판정 = S2(최종본은 판정 행 1건, 중간 기록은 별도 append) + F1(첫 이상부터 기록) + L1(모든 방법 종료 또는 기한 도달 시 최종, 기한 뒤 결과 버림) + 재시도 대체 시점 (ii) + 실패 3경로 "최종 없음". 용어 '중간 판정 기록'(`interim`) | 10/21 사용자 Q3-2, 용어 10/21 확정 |
| C12 | 계측(#66)·종합 판정·방법별 결과·중간 판정 기록 형식은 **evidence 한 버전(v4)·한 계약** | 10/21 사용자 Q8 A |
| C13 | LLM 설명은 요청 시·최종 판정만. 알림은 첫 이상에서 보내고 근거가 늘면 갱신, 대체·최종 실패 때만 드물게 철회. '정상 여부 미확인'은 별도 표시 | 10/21 사용자 Q6 A |
| C14 | 위험도 = 대응 우선순위(알림 피로 방지), 3단계 위험·주의·참고. 같은 시도 안에서 같거나 올라가기만, 근거는 계속 추가. 위험도 = 근거 방법 단계 중 최고값(합산·대분류 겹침 승급 없음) | 10/22 사용자 Q-R1·Q-R2·Q-R3 |
| C15 | 단계가 정의된 방법은 그 단계를 바로 적용, 단계 미정의 방법(ML) 단독 이상은 '위험도 미정'. Rule severity critical·warning·info → 위험·주의·참고 그대로. 단계표는 시스템 내장 목록(버전), 판정마다 버전 기록, 시도 시작 시 고정 | 10/22 사용자 Q-R4·Q-R4-1·Q-R4-2·Q-R5 |
| C16 | ML 점수는 방법별 원점수·판정선·'판정선 대비'만 표시, 모델 간 비교·위험도 반영 없음(추후 변경 가능) | 10/22 사용자 Q-S1 A |
| C17 | 위험도 축은 유지하고 기준은 Detector가 재정의하며 **세부는 바뀔 수 있음**. 하드룰 이상 = 기본 '위험', 소프트룰 = 규칙에 정한 단계, ML = 위험도 미정 | 축 유지·Detector 재정의·"세부는 바뀔 수 있음": 11/02 사용자 답변 Q6 / 하드룰 기본 '위험': 11/02 0단계 화면 구성 승인("제안대로") / 소프트룰 단계: 11/02 R4 / ML 위험도 미정: 10/22 Q-R4-1 |
| C18 | 화면 표시 이름: 하드룰 = **이상 규칙**, 소프트룰 = **이상 징후 규칙**, ML = **머신러닝 모델**. 판정 결과 표시는 '이상 / 이상 징후' 그대로. 이 문서 본문의 '하드룰·소프트룰·ML'은 개발 용어 | 11/02 사용자 확정(WebUI 화면 용어) |
| C19 | 이상 규칙(하드룰)에서 '종료 시각(EndTime) 누락'은 제외 — EndTime이 찍히지 않는 정상 트랜잭션 유형이 있어 이상이 아님 | 11/02 사용자 결정(화면 흐름 결정 기록 9.4) |
| C20 | 모델 운영 단위: 모델마다 따로 버전 관리 — 모델 1개 = 버전 계열, 운영은 활성 집합 0..N, 같은 `modelKey`는 활성 집합에 1개 | 11/02 사용자 결정(화면 흐름 결정 기록 9.6, Q-M1) |
| C21 | 결정 질문 U1~U12를 모두 추천안대로 확정(확정 내용은 17.1 결정 결과 표) | 11/03 사용자 답변("추천대로") |
| C22 | 이상 규칙 화면 문구 승인: 상태 규칙 "상태가 S가 아님" / "STATUS ≠ S", 시각 역전 "시작 시각이 종료 시각보다 늦음" / "START_TIME > END_TIME (종료 시각이 없으면 판단하지 않음)"(4.3 `displayName` / `conditionText`). 상태 P·N 위험도는 '주의' 유지(F만 '위험', U2와 같음). N 단계는 11/04 C26이 대체 | 11/02 사용자 결정(화면 흐름 결정 기록 9.7) |
| C23 | 판정 정책(`detectionPolicy`)은 화면에 노출하지 않음 — 편집·읽기 전용 표시 모두 없음, 판정 상세의 '기준 k' 열도 제거 | 11/02 사용자 결정(화면 흐름 결정 기록 9.6 Q-M10·9.8) |
| C24 | 시간 초과 판정 행의 처리 시간은 측정값(`elapsedMs`) 그대로 표시(기한값을 짐작할 수 있는 점은 감수) | 11/03 사용자 결정(화면 흐름 결정 기록 9.9) |
| C25 | 이상 규칙에 건수 규칙 2개 추가: 상태 S인데 오류 건수가 있음, 최종 상태에서 건수 합계가 맞지 않음. U3 "하나씩 추가"의 후속 결정. 이름·버전·`conditionText`는 D17, `displayName` 문구도 사용자 확정(4.3) | 11/04 사용자 결정 |
| C26 | `hard:process-status`의 상태 코드 뜻 확정: S 성공, F 실패, P 처리중, N 미처리, H 부분성공, Z 데이터없음. F는 위험, P·Z는 주의. N이고 END_TIME이 값 없음이면 이상이 아니다. S·F·P·N·H·Z 밖의 값은 미판정, 상태 없음은 U3(나) 유지. H·N '참고'는 사용자의 "널널하게 봐도 된다"(잠깐 찍혔다가 S나 F로 바뀜)를 총괄이 해석한 기본값이며 사용자가 화면에서 바꿀 수 있다(C27). U2·C22의 N '주의'와 U3(나)의 H·Z 미판정을 대체 | 11/04 사용자 결정(H·N 단계는 그 해석) |
| C27 | `hard:process-status`의 코드별 위험도는 사용자가 룰 세트에서 설정한다. C4의 '위험도 읽기 전용'을 이 규칙에 한해 대체하며, 다른 하드룰의 조건·위험도는 내장 고정 | 11/04 사용자 결정 |

### 1.1 세션 합의 → 사용자 확정(11/03 U1)

| # | 합의 | 날짜·출처 |
|---|---|---|
| A1 | 방법 식별자 `hard:<key>` / `soft:<key>` / `ml:<key>`. 과거 `hr:process-status`·`rule:<key>`·`sr:*`는 과거 판정 읽기용 호환 매핑만 | 11/02 Detector·Trainer 세션 합의(#92 착수 지시) → **11/03 사용자 확정(U1)** |
| A2 | 계산용 대분류 키 `hard` / `soft` / `ml`(`detectionPolicy.k`, `categoryResults`, `methodResults[].category`) | 같음 → **11/03 사용자 확정(U1)** |

## 2. 용어

| 용어 | 뜻 |
|---|---|
| 방법 | 판정 1건에서 결과 1개를 내는 단위. 하드룰 1개, 소프트룰 1개, ML 모델 1개 |
| 대분류 | `hard` / `soft` / `ml`(키 이름 [확정 11/03 U1], 1.1 A2). 화면 표시 이름은 이상 규칙(hard) / 이상 징후 규칙(soft) / 머신러닝 모델(ml) [확정 11/02 C18]. 화면의 '룰'은 hard + soft |
| 방법 결과 기호 | T 이상(`succeeded` ∧ `anomalous=true`, 또는 미완료여도 확인된 이상) / N 정상 확인(`succeeded` ∧ `anomalous=false`) / U 미판정(`not_ready`·`failed`) / 비활성(끈 방법, 계산 제외) |
| 판정 status | `complete` 완료 / `partial` 부분 / `unavailable` 판정불가(DB enum 그대로) |
| 종합 `anomalous` | `true` 이상 / `false` 정상 / `null` 정상 여부 미확인 |
| 판정 등급(`grade`) | `anomaly` 이상 / `anomaly_sign` 이상 징후 / `normal` 정상 / `undetermined` 정상 여부 미확인 |
| 위험도 | `critical` 위험 / `warning` 주의 / `info` 참고. 이상일 때만 의미 |
| 시도(attempt) | detect 작업의 한 번 실행(`run_job_attempts`). 작업은 최대 3회 시도 [코드 `detector/jobs.py:69`] |
| 룰 세트 리비전 | 하드룰 켜기·끄기 + `hard:process-status`의 코드별 단계 + 소프트룰 정의 전체 상태의 저장 번호(정수)와 내용 해시. 형식은 [#94] |

## 3. 방법 분류·식별자

### 3.1 식별자 [확정 11/03 U1 — 11/02 세션 합의 A1 승인]

- 형식 `<category>:<key>`. category ∈ `hard` / `soft` / `ml`. key 정규식은 #74 확정값(10/21) `^[a-z][a-z0-9_-]{1,63}$`(dev 기준 `rule-set.md` 식별자 절·`model-version.md` 방법 식별자 절)를 따른다
- 한 판정 안에서 식별자는 유일하다(같은 방법은 1개)
- `hard:<key>`: Detector가 정의한 내장 하드룰(4절). key는 Detector가 정한다
- `soft:<key>`: 운영 룰 세트에 등록된 소프트룰(5절). key 부여 규칙은 [#94 `rule-set.md` 식별자 절]: 서버 생성 `soft:<fieldId>-<8 hex>`(예 `soft:elapsed_ms-3f9a2c1d`, fieldId에 하이픈 없음), 생성 후 불변
- 하드룰 key는 kebab-case로 정하고 `-<8 hex>`로 끝나지 않게 한다 → evidence `rules[].ruleKey`(접두 없음)에서 소프트룰 key와 겹치지 않음 [제안 D9]
- `ml:<key>`: ML 모델 방법. 현재 실행 가능은 `ml:iforest`뿐 [코드 `computation.py:35-36`]
- 통계 SR(`sr:*`)은 새 정책에서 쓰지 않는다 [확정 11/02 C2]. `sr:*`는 룰 세트·모델 버전 저장 검증에서 거부한다(오류 코드는 [#94]: 모델 버전 `methods[]`는 `ml:*`만 허용 — 422 `method_kind_not_allowed:<id>`(`model-version.md` 방법 식별자 절). 카탈로그 밖 키의 422 `method_unknown:<id>`는 같은 문서 "(B) 조합 저장" 절에만 있으나 이 절은 폐기(확정 11/02 Q-M1·Q-M5, 기록용). 룰 세트는 422 `hard_rule_unknown:<id>`·`soft_rule_unknown:<id>`(`rule-set.md` "저장 — `PUT /api/rule-set`" 절 검증 표)). Detector가 실행 중 `sr:*`를 만나면 판정 행 없이 시도 실패(fail-closed) [제안 D12]

### 3.2 과거 판정 읽기용 호환 매핑 [확정 11/03 U1·U6]

- 과거 판정·구성은 **다시 계산하지도 고쳐 쓰지도 않는다**. 아래 매핑은 화면·집계가 과거 evidence v3를 읽을 때 대분류를 붙이는 용도로만 쓴다

| 과거 표기 | 저장 위치 | 새 대분류 | 비고 |
|---|---|---|---|
| `hr:process-status`, evidence `rules[ruleKey=process-status]`, `checkers.hr` | 상태 HR(`status_rule.py:2`) | `hard` → `hard:process-status` | 과거 판정의 `methodVersion`은 `process-status-hr-v1` 그대로(v2 정책 판정은 `process-status-hr-v2`, 4.3) |
| `rule:<rule_key>`, evidence `rules[]`의 등록 Rule 항목 | `ml_config_rules`(`computation.py:38-41`) | `soft` | 10/21에는 HR 소속이었으나 11/02 임계값 규칙 = 소프트룰로 재분류. #94는 임계값형만 소프트룰 의미로 봄(`rule-set.md` 식별자 절 호환 매핑 표의 `rule:<rule_key>` 행) → 비임계값 Rule(문자 eq/ne/in, all/any 묶음)도 과거 판정에서는 '이상 징후 규칙(과거 등록 Rule)'으로 표시만 하고 새 룰 세트로 옮기지 않음 [확정 11/03 U6] |
| `sr:*`, `checkers.sr`의 `not_ready/sr_experiment_pending` | `computation.py:147` | 대응 없음 | 실행된 SR 방법은 없었음. 자리 표시일 뿐이라 대분류 결과에 넣지 않음 |
| `ml:iforest`, evidence `models[{modelKey:"iforest"}]`, `checkers.ml` | `computation.py:140-141` | `ml` → `ml:iforest` | |
| `hr:<연구 8종>`(#94 병합 전 판본 `method-catalog.md` v1의 연구 HR 8종 행. dev `method-catalog.md` 대분류·식별자 절 호환 표에서는 "카탈로그 제거") | 연구 코드만 | 하드룰 후보(4.2) | 실행된 적 없음 |

- 과거 판정의 등급(`grade`)·위험도: 저장값이 없으므로 **`grade=null`**. 위험도는 이상 판정(`anomalous=true`)만 **`unrated`**, 정상·미확인 판정은 **`not_applicable`**(9.1 정의와 같음). 재계산 없음 [확정 11/03 U6]

## 4. 하드룰(화면: 이상 규칙) 내장 목록

### 4.1 원칙 [확정 11/02 C4·C17]

- 시스템 내장 목록만 존재. 사용자가 추가·수정할 수 없고 **켜기·끄기**와 `hard:process-status`의 코드별 위험도 설정(C27)만 가능(기본 모두 켬 — DB 016의 r1이 새 규칙 2개를 켠 상태를 전제로 하며, r0만 있으면 그 2개는 꺼짐). 켜기·끄기 상태는 룰 세트에 저장 [#94]. 리비전 스냅샷에 없는 내장 규칙은 꺼짐으로 실행한다(10절 D14)
- 끄기는 허용하되 화면 경고와 저장 확인 체크가 필수(R1). 서버 저장 검증은 [#94]
- 조건·위험도는 읽기 전용이며 Detector가 정의한다. 하드룰 위험도 기본값은 '위험'. 예외: `hard:process-status`의 코드별 위험도는 사용자가 룰 세트에서 설정한다(10절) [확정 11/04 사용자]
- 하드룰 하나라도 T면 판정 등급은 '이상'(8절)
- 하드룰은 결정적 규칙이다. 입력값이 없거나 해석할 수 없으면 T도 N도 아닌 U(`not_ready`) [제안 D6]. 예외: `hard:negative-duration`의 END_TIME 없음은 U가 아니라 N(4.2, C19·D1), `hard:success-with-error`·`hard:count-conservation`은 상태가 대상 밖이면 건수를 보지 않고 N(4.2, D15). 해석 불가도 `not_ready`(cause `input_invalid`)인 이유: 하드룰은 원천 로그 값 그대로를 검사하므로 형식이 깨진 값은 "판정 입력이 갖춰지지 않음"이다. 소프트룰 비숫자가 `failed`(cause `input_invalid`)인 것은 등록 필드(숫자형으로 등록된 값)의 타입 계약 위반이기 때문이다(5.2)
- '값 없음'의 기준 [#112 정정 11/04]: 키가 없거나 null인 값과 빈 문자열·공백만 있는 문자열·`\N`(앞뒤 공백 제외)을 모두 값 없음으로 본다. 하드룰 입력(`status`, `START_TIME`, `END_TIME`, 건수 필드)과 소프트룰 원천 열(5.2)에 같은 기준을 쓴다. `\N`은 CSV의 NULL 표기로, 수집 경로에 따라(`collector/replay_csv.py`) 문자열로 남는다. `NULL`·`None`·`NaN` 같은 단어 문자열은 값 없음이 아니다 → 값이 있는 것으로 보아 해석 불가·정의 밖으로 처리한다

### 4.2 목록 — 구현분·사용자 예시·후보

| methodId | 조건(T) | 위험도 | 입력 없음·해석 불가 | 상태 |
|---|---|---|---|---|
| `hard:process-status` | PROCESS `status`가 S가 아님: F, P, Z [확정 11/04 사용자]. H와 END_TIME이 있는 N의 T 판정은 C26의 '참고' 해석값에서 나온다. **예외: N이고 END_TIME이 값 없음이면 이상이 아니다** [확정 11/04 사용자]. N으로 매핑하고 `succeeded`, `anomalous=false`, evidence `result: "not_applicable"`, `reason: "end_time_absent"`로 기록한다 [제안 D16]. END_TIME이 있는 N만 T | 기본 F = 위험, P·Z = 주의 [확정 11/04 사용자]. H·N = 참고(C26의 해석값). 코드별 단계는 사용자가 룰 세트에서 설정(C27, 10절) | `status`가 S·F·P·N·H·Z 밖이면 U [확정 11/04 사용자], 없으면 U [확정 11/03 U3(나)]. 원인 코드 대응(없음 → `input_missing`, 정의 밖 → `input_invalid`)은 [제안 D6] | 구현, v04 작업 실행 경로에 연결 [코드 `detector/hard_rules.py`, `methodVersion=process-status-hr-v2`는 D16]. v1·legacy 판정은 `status_rule.py`의 v1 그대로 |
| `hard:negative-duration` | 시작 시각이 종료 시각보다 늦음(`START_TIME > END_TIME`, 사용자 예시 "StartTime > EndTime", 필드명은 `derived_expression.py:11`) | 위험 | 판정 순서 [제안 D1]: ① **END_TIME 없음 → 조건을 평가하지 않고 N**(START_TIME 유무와 무관 — 둘 다 없어도 N) ② START_TIME 없음 → U ③ 값이 있으나 해석 불가 → U ④ 비교. ① 세부: (`succeeded`, `anomalous=false`, evidence `result: "not_applicable"`, `reason: "end_time_absent"`) — "EndTime 없음 = 이상 아님" [확정 11/02 C19], N으로 매핑 [제안 D1]. U로 두면 EndTime 없는 정상 트랜잭션이 하드 대분류를 영구히 미판정(partial)으로 만들어 '정상'이 될 수 없음. ② START_TIME 없음은 U(`input_missing`), ③ 해석 불가는 U(`input_invalid`) [제안 D1] | **1차 목록 [확정 11/03 U3(가)]**(사용자 예시 11/02), 구현, v04 작업 실행 경로에 연결 [제안 D1] |
| `hard:success-with-error` | 상태 S인데 `error_count > 0` | 위험 | 판단 순서 [제안 D15]: ① 상태 없음 → U(`input_missing`) ② 상태가 S·F·P·N·H·Z 밖 → U(`input_invalid`) ③ S 아님 → N ④ `error_count` 값 없음 → U(`input_missing`) ⑤ 비숫자·비유한 → U(`input_invalid`) ⑥ 비교 | **1차 목록 [확정 11/04 사용자]**(C25, U3 "하나씩 추가"의 후속 결정), 구현, v04 작업 실행 경로에 연결 |
| `hard:count-conservation` | 최종 상태 S·F에서 `total_count ≠ success_count + error_count` | 위험 | 판단 순서 [제안 D15]: ① 상태 없음 → U(`input_missing`) ② 상태가 S·F·P·N·H·Z 밖 → U(`input_invalid`) ③ S·F 아님 → N ④ 세 건수 중 값 없음 → U(`input_missing`) ⑤ 비숫자·비유한 → U(`input_invalid`) ⑥ 비교 | **1차 목록 [확정 11/04 사용자]**(C25, U3 "하나씩 추가"의 후속 결정), 구현, v04 작업 실행 경로에 연결 |
| `hard:invalid-count-domain` | 건수·재시도 수가 음수 또는 정수 아님 | 위험 | 모든 건수 없으면 U | 후보 — 1차 목록 밖 [확정 11/03 U3] |
| `hard:final-missing-end` | 최종 상태인데 종료 시각 없음 | — | — | **제외 [확정 11/02 C19]** — EndTime이 찍히지 않는 정상 트랜잭션 유형이 있어 이상이 아님 |

- 1차 목록 밖 후보는 실데이터 발생 빈도를 확인한 뒤 하나씩 추가한다 [확정 11/03 U3]. 추가하려면 4.3 정의 행과 새 `methodVersion`이 필요하다. `hard:success-with-error`·`hard:count-conservation`은 이 절차의 후속 결정으로 1차 목록에 들어왔다 [확정 11/04 사용자 C25]. 건수 필드 의미는 미검증인 채 사용자가 추가를 결정했다. 프로세스 원천 실측에서 상태 S·F 행의 건수 결측은 없었다
- 건수 규칙 공통 [제안 D15]: 판단 순서는 표대로 상태를 먼저 본다. 상태가 대상 밖이면 조건이 거짓이므로 건수를 보지 않고 N이다(`not_applicable` 아님). 건수는 Decimal로 정확히 비교하며 허용 오차는 없고, 정확히 더할 수 없는 값은 해석 불가로 둔다. 필요한 건수 중 값 없음이 하나라도 있으면 비숫자가 함께 있어도 `input_missing`이다. 정수 여부와 음수는 이 규칙들이 보지 않는다(후보 `hard:invalid-count-domain`의 몫이며, 그 후보는 아직 1차 목록 밖)
- `hard:process-status` 표시 문구 [제안 D16]: `displayName`·`conditionText`는 코드를 나열하지 않고 "상태가 S가 아님" / "STATUS ≠ S"를 유지한다. END_TIME 없는 N 예외는 이 절의 표가 정본이다
- `hard:invalid-status` 제거 [제안 D16]: 상태 코드가 S·F·P·N·H·Z로 확정됐고 그 밖의 값(C26)과 상태 없음(U3(나) 유지)은 `hard:process-status`의 U로 남기므로 별도 규칙이 필요 없다

- 후보의 출처: 연구 규칙 `research/anomaly_detection/code/process_anomaly_pipeline/rules.py:6-17`(설명), `:36-55`(계산), 카탈로그 연구 HR 8종(#94 병합 전 판본 `method-catalog.md` v1, 3.2 표). 연구 `status_failed`는 `hard:process-status`의 F와 같아 따로 두지 않는다. 연구 `missing_sql_required`(PK·NOT NULL 컬럼 결측)는 행 하나로 판정할 수는 있으나 원천 SQL 스키마의 PK·NOT NULL 기준을 서버 관측 필드에 대응시킨 근거가 없어 후보에서 보류한다(수집기는 결측 필드를 `missing_fields` 열에 따로 남김, `collector/storage.py:138`). 연구 `invalid_input` 중 복합 PK 충돌은 여러 행을 비교해야 해 행 하나로 판정할 수 없으므로 제외하고, 나머지(미정의 상태값·해석 오류)는 각 규칙의 U 처리로 다룬다
- 입력 근거 [코드]: PROCESS 관측의 `allowed_values`에 `start_time_raw`·`end_time_raw`(문자열)와 건수 4개가 있다(`collector/storage.py:21-23`, `:30`). 그러나 Rule DSL 원천 필드에는 시각 필드가 없다(`detector/rules.py:9-11`) → `hard:negative-duration`은 Rule DSL이 아닌 **내장 구현**이다(`detector/hard_rules.py`, #112)
- 상태 규칙의 단계: F `critical`, P·Z `warning`은 [확정 11/04 사용자]. H·N `info`는 사용자의 "널널하게 봐도 된다"를 총괄이 해석한 기본값이며 사용자가 화면에서 바꿀 수 있다(C26·C27). END_TIME 없는 N을 빼고 모두 이상으로 판정한다. 계약 문구 "P/N만으로 먹통·실패 확정 금지"(`trainer-service.md` "책임·범위" 절 statusRulePolicy 항목)는 유지한다. v1·legacy 판정은 기존 매핑(`status_rule.py:7`) 그대로다

### 4.3 하드룰 방법 결과의 고정값

- 하드룰 정의 형식 [제안 D13] — Detector 내장 목록 항목 1개 = `{id, methodVersion, displayName, conditionText, severity}`(`displayName`·`conditionText`는 화면 표시용 한글 문자열). #94 `rule-set.md` `hardRules[]`의 `displayName`·`conditionText`는 이 값의 읽기 전용 투영이다. `severity`도 읽기 전용 투영이지만 `hard:process-status`의 v2 항목만은 사용자 설정 실행 입력이다(C27, 판별은 10절 D14)
  - `severity`는 기본이 **단일 문자열**(`critical` / `warning` / `info`)
  - 결과 코드별로 단계가 다르면 **`{"byCode": {<결과 코드>: <단계>, …}}`** 객체. 키는 그 규칙이 낼 수 있는 결과 코드 전부여야 하고, T 결과의 `riskLevel` = `byCode[code]`(`hard:process-status`는 룰 세트 v2 항목 값, 10절)
  - 결과 코드는 `"PROCESS_STATUS_" + <status 값>`, 즉 `PROCESS_STATUS_F`·`PROCESS_STATUS_P`·`PROCESS_STATUS_N`·`PROCESS_STATUS_H`·`PROCESS_STATUS_Z`

| id | methodVersion | displayName | conditionText | severity | 상태 |
|---|---|---|---|---|---|
| `hard:process-status` | `process-status-hr-v2`(D16) | 상태가 S가 아님 | STATUS ≠ S | `{"byCode": {"PROCESS_STATUS_F": "critical", "PROCESS_STATUS_P": "warning", "PROCESS_STATUS_N": "info", "PROCESS_STATUS_H": "info", "PROCESS_STATUS_Z": "warning"}}` | 구현, v04 작업 실행 경로에 연결. 코드 집합과 N 조건이 바뀌어 v2 [제안 D16]. 값은 기본값이며(H·N은 C26의 해석값) 코드별 단계는 룰 세트 v2 항목 값(10절) |
| `hard:negative-duration` | `negative-duration-v1`(이름은 D13) | 시작 시각이 종료 시각보다 늦음 | START_TIME > END_TIME (종료 시각이 없으면 판단하지 않음) | `"critical"` | 1차 목록 [확정 11/03 U3], 구현, v04 작업 실행 경로에 연결 |
| `hard:success-with-error` | `success-with-error-v1` | 성공 상태이나 오류 건수가 기록됨 | STATUS = S, ERROR_COUNT > 0 | `"critical"` | 1차 목록 [확정 11/04 사용자], 구현, v04 작업 실행 경로에 연결. 결과 코드 `HARD_SUCCESS_WITH_ERROR`. 이름·코드·`conditionText`는 [제안 D17], `displayName`은 [확정 11/04 사용자] |
| `hard:count-conservation` | `count-conservation-v1` | 건수 합계 불일치 | TOTAL_COUNT ≠ SUCCESS_COUNT + ERROR_COUNT | `"critical"` | 1차 목록 [확정 11/04 사용자], 구현, v04 작업 실행 경로에 연결. 결과 코드 `HARD_COUNT_CONSERVATION`. 이름·코드·`conditionText`는 [제안 D17], `displayName`은 [확정 11/04 사용자] |

- `displayName`·`conditionText` 문구는 위 표 값 그대로다. 앞 두 행은 [확정 11/02 사용자 화면 문구 승인(화면 흐름 결정 기록 9.7), C22], 뒤 두 행의 `displayName`은 [확정 11/04 사용자], `conditionText`는 [제안 D17]. Detector 구현이 같은 값을 둔다. 정의 형식(`{id, methodVersion, displayName, conditionText, severity}`)은 [제안 D13]
- #94 `rule-set.md` "룰 세트 상태" 절 예시도 앞 두 행의 표시 문구는 같은 값(정본은 이 표)

- `methodVersion`이 조건과 기본 위험도를 고정한다. 조건이나 기본 위험도를 바꾸면 새 `methodVersion`을 만들고 과거 판정은 기록된 버전 그대로 둔다. `hard:process-status`의 코드별 위험도를 사용자가 룰 세트에서 바꾸는 것은 새 `methodVersion`이 아니며 판정의 룰 세트 리비전과 `statusRulePolicy.mapping`으로 추적한다 [제안 D16]
- 하드룰 `code`(결과 코드): `hard:process-status`는 `PROCESS_STATUS_<F|P|N|H|Z>`. 새 하드룰은 `HARD_<KEY 대문자, -는 _>`(예 `HARD_NEGATIVE_DURATION`) [확정 11/03 U10]

## 5. 소프트룰(화면: 이상 징후 규칙) 실행 의미

### 5.1 정의 [확정 11/02 C5]

| 필드 | 뜻 | 비고 |
|---|---|---|
| `id` | `soft:<fieldId>-<8 hex>`(예 `soft:elapsed_ms-3f9a2c1d`). 판정 안 `methodId`로 그대로 씀(3.1) | 서버(API)가 생성, 생성 후 불변 [#94 `rule-set.md` 식별자 절] |
| `field` | 등록 필드 1개, 값은 #94 `fieldId`(예 `elapsed_ms` 처리 시간, `retry_count` 재시도 수) | 등록 필드 목록·단위·원천(`P.<숫자 필드>` 또는 `F.<파생변수>`) 대응은 [#94]. 생성 후 변경 불가(R2) |
| `operator` | 저장값 `gte` / `gt` / `lte` / `lt` [#94 enum]. 화면 표기 `≥` / `>` / `≤` / `<`와 순서대로 대응 | Rule DSL 숫자 비교 연산자와 같은 값(`rules.py:39-48`) |
| `threshold` | 룰 세트 스냅샷과 저장 API(GET/PUT)는 **JSON number**. Detector는 시도에 고정한 스냅샷의 number를 읽어 #94 변환 규칙으로 **고정소수 문자열**로 바꾼 뒤 Rule DSL 숫자 비교에 넘기고(비교는 `Decimal(str(value))`, `rules.py:42`), evidence `methodResults[].condition.threshold`에도 같은 문자열로 기록한다. 변환 규칙(지수 표기 금지 등)은 #94 `rule-set.md`의 `softRules[].threshold` 필드 정의와 "리비전 형식" 절의 `threshold` 변환 규칙을 따름 | NaN·무한대·변환 불가 값은 저장 단계에서 422 [룰 세트 편집 제약 — #94와 합의, D10] |
| `unit` | 임계값 단위 | 등록 필드의 단위와 같아야 함. 실행 중 단위 변환 없음 [#94와 합의해 결정, D10] |
| `severity` | `info` / `warning` / `critical` [#94 enum] | 사용자 선택(R4). 위험도로 그대로 대응: critical → 위험, warning → 주의, info → 참고 [확정 10/22 Q-R4-2], 9절 |
| `name`, `enabled` | 표시 이름, 켜기·끄기 | 수정 가능 범위는 [#94] |

### 5.2 실행

- **기존 Rule DSL 엔진을 재사용**한다: 소프트룰 1개 = DSL 단일 비교 `{field, operator, value: threshold}`(`rules.py:28-48`, 숫자 비교 경로). 새 비교 엔진을 만들지 않는다
- **방법 단위 실행**: 소프트룰마다 결과를 따로 낸다. 한 규칙의 오류가 다른 규칙이나 하드룰 결과를 지우지 않는다(10/21 행동 변경 2)
- 결과 규칙

| 입력 상태 | status | anomalous | cause |
|---|---|---|---|
| 값이 있고 숫자, 조건 충족 | `succeeded` | `true` | — |
| 값이 있고 숫자, 조건 불충족 | `succeeded` | `false` | — |
| 원천 열 누락으로 값이 null이고 #94 카탈로그 `softRuleFields[].sourceMissingPolicy`가 그 열을 `not_applicable`로 정함(예 `elapsed_ms`의 END_TIME 누락) | `succeeded` | `false` | — (evidence `result: "not_applicable"`, `reason: "<원천 열 소문자>_absent"` 예 `end_time_absent`) — 조건을 평가하지 않음. "END_TIME 누락 = 이상 아님" [확정 11/02 C19], N 매핑 [제안 D4]. 우선순위: 이 행이 다른 결측 행보다 먼저 — `not_applicable` 정책 열(END_TIME)이 없으면 다른 열(START_TIME)이 함께 없어도 N |
| 값 없음(그 밖의 원천 필드 결측·파생변수 결측, 정책이 `unavailable`이거나 없음) | `not_ready` | `null` | `input_missing` |
| 파생변수 계산 자체 실패(피처 변환 오류, 파생 필드의 원천 값이 있으나 비숫자이거나 시각으로 해석할 수 없음, 0 나눗셈) | `not_ready` | `null` | `feature_unavailable` [제안 D4, #112 구현 확인 반영 11/04] |
| `column` 필드의 값이 숫자가 아니거나 유한하지 않음 | `failed` | `null` | `input_invalid` [제안 D4] |
| 규칙 정의 자체가 잘못됨(알 수 없는 필드 등) | `failed` | `null` | `failure`, failureCode `invalid_rule_definition` |
| 임계값이 잘못됨(number가 아님, 유한하지 않음, 고정소수 변환 실패, 정수 필드에 소수) | `failed` | `null` | `failure`, failureCode `invalid_threshold` [제안 D6, #112 구현 확인 반영 11/04] |

- 위 표의 '누락'·'값 없음'은 4.1의 값 없음 기준을 따른다 [#112 정정 11/04]: 소프트룰이 쓰는 원천 열 값이 null이거나 빈 문자열·공백만 있는 문자열·`\N`이면 값 없음이다. `column` 필드와 파생 필드의 원천 열, `not_applicable` 판단에 모두 적용한다. `NULL`·`None`·`NaN` 같은 단어 문자열은 값이 있는 것으로 본다(`column` 필드는 비숫자 행, 파생 필드는 계산 실패 행)
- **결측·비숫자를 정상으로 바꾸지 않는다**(위 `not_applicable` 행만 예외 — 사용자 결정으로 "이상 아님"이 정해진 누락). U인 소프트룰이 하나라도 있으면 그 판정은 정상(`false`)이 될 수 없다(7.3). U로 두면 END_TIME이 없는 정상 트랜잭션마다 소프트 대분류가 미판정이 되어 '정상'이 될 수 없으므로 하드룰 `hard:negative-duration`(4.2)과 같은 방식으로 N에 둔다
- 현행 차이 [코드]: 지금은 등록 Rule 묶음 전체가 한 콜백이다. 결측 1개면 묶음 전체 `not_ready`(`rules.py:79`), 비숫자는 `ValueError("invalid_rule_numeric")`(`rules.py:41-46`)로 rule 경로 전체가 `failed`(`policy.py:48-52`)가 되어 같은 경로의 상태 HR 결과까지 사라진다
- 처리시간 같은 시간 차는 원천 필드가 아니라 `datetime_diff` 파생변수(`derived_expression.py:11-12`)로 등록돼야 한다 [#94 등록 필드 목록]
- 소프트룰 `code`: `SOFT_<KEY 대문자, -는 _>` [확정 11/03 U10]

## 6. ML(화면: 머신러닝 모델) 방법 결과

- 방법마다 결과 1개. v04 작업은 활성 집합의 모델마다 방법 1개로 실행한다(`detector/attempt_v04.py`, #112). 실행 어댑터는 `ml:iforest`뿐이고 그 밖의 모델은 `not_executable`이다. v03 작업은 구성당 IF 1개 그대로다 [코드 `computation.py`]
- 운영 단위 [확정 11/02 C20]: 모델 하나 = 버전 계열 하나, 운영 = **활성 집합 0..N개**(활성화·운영에서 빼기·교체·복원, 활성 집합 리비전 이력 — #94 `model-version.md` "활성 집합 — 활성화·운영에서 빼기·교체·복원" 절). 같은 `modelKey`의 버전은 활성 집합 안에 동시에 1개까지만(위반 저장은 #94 422 `active_model_key_duplicate:<modelKey>`) → `ml:<modelKey>`가 판정 안에서 유일. n_ml = 활성 집합의 모델 수, ML 순서 = 활성 집합 순서(활성화 순서)
- 결과 규칙: 점수가 판정선 방향으로 넘으면 T(`computation.py:139`), 입력·산출물·판정선이 없으면 U(`not_ready`), 계산 오류는 U(`failed`)
- `scoreDetail` [확정 10/22 C16, 필드 이름 확정 11/03 U10]: `{raw, threshold, direction, thresholdPolicy, normalized}`
  - `raw`: 원점수(IF는 `-score_samples`, 높을수록 이상)
  - `threshold`·`direction`·`thresholdPolicy`: 구성의 `threshold_spec`(`model_adapters.py:143-144`)과 정책 이름(`train-internal-p99-v1`)
  - `normalized`: `{kind: "threshold_ratio", value: raw / threshold}`(`lower_is_anomaly`면 `threshold / raw`). 분모가 0 이하이거나 의미 없는 척도면 `null`
  - 모델 간 정렬·합산·대표값에 쓰지 않는다
- 결과 코드: 현재 IF의 `IF_ANOMALY`만 정의돼 있다. 후속 메모: IF 외 ML 결과 코드는 둘째 모델 추가 전에 정한다
- 위험도: ML은 단계 정의가 없다 [확정 10/22 Q-R4-1·11/02 C17]. T인 ML 결과는 `riskLevel=null`, `riskLevelReason="level_undefined"`, N·U 결과는 둘 다 `null`(9.1 채움 규칙 [제안 D9])
- `modelVersionId`: 사용한 모델 버전 참조. 모델 버전 식별 형식은 [#94]

## 7. `detectionPolicy` v2와 조합 규칙

### 7.1 형태 [객체 형태 확정 10/21 C9, `k`의 대분류 키 확정 11/03 U1]

```json
{"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1}, "deadlineMs": 3000}
```

| 키 | 규칙 |
|---|---|
| `version` | 정수 2 |
| `policyKey` | 내장 정책 이름. 현재 1개: `category-k-of-n-or-v1`(7.3 규칙) [이름 확정 11/03 U10] |
| `k` | 정확히 `hard`·`soft`·`ml` 세 키, 값은 정수 |
| `deadlineMs` | 정수 100~30000. 기존 v1 `deadlineMs` 규칙 승계(`policy.py:33`) |

- 이전 계약 초안의 `k: {hr, sr, ml}`(#94 병합 전 판본의 #74 초안 — `detection-config-version.md` configuration_spec v3 예시·`detectionPolicy` 행·(B) 조합 저장 예시, `config-comparison-evaluation.md` 결과 예시)는 이 형태로 대체한다. #94 병합으로 dev 계약의 예시는 모두 `{hard, soft, ml}`이다. v2 객체는 아직 코드·DB 어디에도 저장된 적이 없다(검색 결과 계약 문서에만 존재)
- 저장 위치 [확정 11/03 U11 — #94 Q-M2 확정의 귀결]: **운영 설정 전역 값**(모델 버전 spec이 아님, 모델 단위(C20)와 무관), 버전 없이 저장 즉시 적용하고 리비전 이력을 남김. 저장된 행이 없는 상태가 **리비전 0 = 내장 기본 정책** `{"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1}, "deadlineMs": 3000}`(시드 없음), 첫 저장이 리비전 1. 저장 형식·리비전·조회·저장 API(`GET/PUT /api/detection-policy`)는 [#94 `model-version.md` "운영 설정 — `detectionPolicy` 전역 값" 절]. 화면에는 노출하지 않는다(C23)
- Detector는 그 판정에 쓴 정책 객체를 evidence `detectionPolicy`에 그대로 복사하고, 정책 리비전을 `detectionPolicyRevision`(정수, 기본 정책이면 0)에 기록한다 [제안 D11, U11]

### 7.2 v1·v2 구분 [확정 10/21 C9·C10, `version: 1` 처리는 확정 11/03 U12]

- **운영 설정에는 v2만 저장한다** [#94 `model-version.md` 운영 설정 절·U11 귀결]: 운영 설정 `detectionPolicy`(7.1, `PUT /api/detection-policy`)에 저장할 수 있는 값은 `version: 2` 객체뿐이다. 키 없음(legacy)·v1 형식(`{mode, deadlineMs, combination}`)·`version: 1`은 운영 설정 저장에서 400 `invalid_detection_policy`(7.4 첫 행 — 키 집합이 `{version, policyKey, k, deadlineMs}`가 아님). 아래 표의 legacy·v1 해석은 schemaVersion 1·2 구성 spec 판독과 v1 형식 train 요청에만 쓴다

| `detectionPolicy` 값(v1·v2 구성 spec, v1 형식 train 요청, 운영 설정) | 해석 |
|---|---|
| 키 없음 | legacy: `parallel / 3000 / any_anomaly`로 읽음(`policy.py:26-29`). schemaVersion 1·2 구성에만 허용(운영 설정에서는 400) |
| `version` 키 없음 | v1: 키 집합이 정확히 `{mode, deadlineMs, combination}`(`policy.py:30-37`). schemaVersion 1·2 구성·v1 형식 train 요청에만 허용(운영 설정에서는 400) |
| `version: 1` | 400 `invalid_detection_policy` — v1은 `version` 키가 없을 때만 인정 [확정 11/03 U12]. `version`은 v2 키라 C10 혼용 금지와도 맞고, 현행 `policy.py:30`도 키 집합 불일치로 거부 |
| `version: 2` | v2: 7.1 형태 |
| v1 키(`mode`·`combination`)와 v2 키(`version`·`policyKey`·`k`) 혼용 | 400 `invalid_detection_policy` |
| 그 밖의 `version` 값 | 400 `invalid_detection_policy`(7.4) |
| configuration_spec **schemaVersion 3**(모델 버전) | spec에 `detectionPolicy` 키가 **없어야 함** — 있으면 400 `invalid_configuration_request`(#94 `model-version.md` "configuration_spec schemaVersion 3" 절). v2 정책은 운영 설정에서 읽음(7.1, U11). v3 train 요청에 `detectionPolicy`가 있으면 400 `invalid_detection_policy`(같은 문서 "(A) 학습" 절) |

- "`version`이 없거나 1이면 v1" 문장은 10/21 결정 기록에는 없고 #74 계약 초안(#94 병합 전 판본 `detection-config-version.md` configuration_spec v3 표의 `detectionPolicy` 행)의 [확정 10/21] 표기 안에만 있었다. 이 문장은 `{"version": 1, "mode": …}` 입력에서 혼용 금지(C10)와 충돌한다 → "`version` 키 없음"만 v1로 정리 [확정 11/03 U12]. #94는 같은 내용을 dev `model-version.md` "configuration_spec schemaVersion 3" 절의 `detectionPolicy` 행에 반영했다(그 행의 "#92 U12 결정 대기" 표기는 #106에서 해소, 16절)

- v1·legacy 판정은 현행 계산(`policy.py:74-80`) 그대로 둔다. 이 문서의 v2 규칙은 v2 정책 판정에만 적용한다
- 판독기 fail-closed: v2를 모르는 판독기는 조용히 기본값으로 읽지 말고 실패해야 한다. 현행 worker는 키 집합 불일치로 자동 실패(`policy.py:30-31`)하지만 TS는 `detectionPolicy?.mode ?? 'parallel'`로 조용히 오해석한다(`configRoutes.ts:107`) → 15절 충돌

### 7.3 조합 규칙 `category-k-of-n-or-v1` [확정 10/21 C8. 대분류 분리는 11/02 C1, 키 이름은 11/03 U1]

- 입력: 활성 방법마다 T/N/U. 비활성 방법은 계산에서 제외
- 1단계 — 대분류 c(활성 방법 n_c개, 기준 k_c)
  - n_c = 0 → 대분류 비활성(`skipped`), 2단계에서 제외
  - T 수 ≥ k_c → T
  - 그 외 대분류 방법이 **모두 succeeded** → N
  - 그 외 → U
- 2단계 — 종합(활성 대분류만): 하나라도 T → `anomalous=true` / 모두 N → `false` / 그 외 `null`
- 정상(`false`)은 활성 방법이 **모두 succeeded**일 때만. "남은 방법이 모두 T여도 k에 못 미침" 같은 조기 정상은 쓰지 않는다
- k_c = 1이면 1단계는 Kleene OR과 같다
- 판정 status(방법 단위) [확정 10/21 결정 D4-1]
  - `complete`: 활성 방법 전부 succeeded
  - `partial`: complete 아님, 그리고 succeeded 방법 또는 종합 `anomalous=true`가 있음
  - `unavailable`: 그 외
- 대분류 status 매핑 [제안 D2 — 10/21 결정 D4-3 파생 제안]: 방법 0개 → `skipped` / 전부 succeeded → `succeeded` / failed 1개 이상 → `failed` / 그 외 → `not_ready`
- 파생 `requestedMode` [확정 10/21 결정 D7 규칙, 대분류 이름 교체]: 활성 ML 0개 → `rule_only` / 활성 hard·soft 0개 → `ml_only` / 그 외 `parallel`. 저장 입력이 아니라 표시값

### 7.4 검증 [규칙 확정 10/21 C8, 코드 이름 확정 11/03 U10]

| 검사 | 오류 |
|---|---|
| 객체가 아님, 키 집합 ≠ `{version, policyKey, k, deadlineMs}`, `version ≠ 2`, `deadlineMs` 범위 밖, `k` 키 집합 ≠ `{hard, soft, ml}`, `k` 값이 정수 아님 | 400 `invalid_detection_policy` |
| `policyKey`가 내장 목록에 없음 | 422 `unsupported_detection_policy` |
| 활성 방법 0개(n_hard + n_soft + n_ml = 0) | 422 `detection_methods_empty` |
| 1차 구현 [확정 11/03 U4]: 정책 저장에서 `k.hard`·`k.soft`·`k.ml` 중 하나라도 1이 아님 — 그 대분류의 현재 n과 무관하게(n = 0인 대분류 포함) 세 값 모두 검사 | 422 `combination_k_out_of_range:<category>` |
| 활성 대분류(n_c ≥ 1)에서 k_c < 1 또는 k_c > n_c | 422 `combination_k_out_of_range:<category>` |

- **n = 0 대분류의 k 처리**(U4 적용 상태)
  - 정책 저장: U4의 "모두 1" 검사는 n과 무관하게 세 값 전부에 적용한다. n = 0인 대분류도 k = 1이어야 저장된다. 그래야 나중에 룰 세트 저장이나 활성화로 그 대분류가 켜져도 k > n이 생기지 않는다
  - k > n 검사(표 마지막 행)와 판정 계산(7.3): n_c = 0인 대분류(비활성)의 k_c는 **검사하지 않고 무시**한다(그 대분류는 `skipped`). U4의 "n = 0인 대분류의 k는 무시"는 이 뜻이다. #94 Q-M8의 "`n_ml = 0`이면 `k.ml` 값과 무관하게 허용"도 k > n 검사에 대한 문장이며, U4 적용 상태에서는 그보다 앞서 "모두 1" 검사가 걸린다. `model-version.md`의 해당 문구는 #106에서 이 뜻으로 정정됐다(16절)

- 검증 위치 [규칙 확정 10/21 C8, 검증 위치는 #94와 합의 11/02]
  - `detection_methods_empty`: **저장 경로와 관계없이** 저장 결과 상태가 "켜진 하드룰 0 + 켜진 소프트룰 0 + 활성 모델 0"이 되면 거절한다. 대상 경로는 룰 세트 저장(되돌리기 포함), 모델 운영에서 빼기(`retire`), 활성 집합 교체(`replace`)·복원(`restore`) [#94 `rule-set.md` 저장 절 검증 표, `model-version.md` 활성 집합 절 공통 검사]
  - k > n은 **min(k, n)으로 바꿔 평가하지 않는다**(사용자가 정한 k의 의미가 조용히 바뀌므로)
  - **1차 구현 [확정 11/03 U4]**: 정책 저장에서 k가 모두 1이 아니면 422(위 표). 그러면 룰 세트 저장·retire·활성 집합 변경으로는 k > n이 생길 수 없다(k = 1 ≤ n, 또는 n = 0이면 비활성). 이 경로들은 `detection_methods_empty`만 검사하면 된다
  - **U4를 나중에 완화해 k ≥ 2를 허용할 때만**(사용자 재결정 필요) 저장 시 양방향 검증이 필요하다
    - 정책 저장: `k.<대분류>` > 그 대분류의 현재 n이고 n ≥ 1이면 거절(`combination_k_out_of_range:<category>`)
    - 룰 세트 저장·활성 집합 변경: 저장 결과 n이 k 미만이 되면 거절(n = 0이 되는 경우는 제외 — 비활성). 활성 집합 쪽(`k.ml`)은 #94 Q-M8에 이미 있고, 룰 세트 쪽(`k.hard`·`k.soft`)은 그때 #94에 요청(16절 조건부 요청)
  - **교차 저장 직렬화** [저장 구현 소유는 #94 계약·API, Detector 요구]: `detection_methods_empty`(룰 세트 + 활성 집합)와 k > n(정책 + 룰 세트·활성 집합)은 저장소 두 곳 이상을 함께 보는 검사다. 룰 세트 저장·활성 집합 변경·정책 저장이 겹치면 각자는 통과해도 합친 결과가 위반일 수 있다(예: 마지막 켜진 룰을 끄는 저장과 마지막 활성 모델을 빼는 `retire`가 동시에 통과). 따라서 세 저장은 하나의 잠금으로 직렬화해, 뒤 저장이 앞 저장의 확정 결과를 읽고 검사해야 한다. 잠금 방식(공통 advisory lock, 운영 상태 행 `FOR UPDATE` 등)과 구현은 #94·API 소유다. 아래 실행 시점 fail-closed는 직렬화가 빠졌을 때의 마지막 방어다
  - 실행 시점(Detector 시도 시작): 고정한 세 리비전(룰 세트·활성 집합·정책)으로 n_c를 다시 센다
    - 활성 방법이 0개(n_hard + n_soft + n_ml = 0)면 판정 행 없이 **시도 실패**(fail-closed). 빈 판정이나 정상 판정으로 대신하지 않는다
    - n_c ≥ 1인데 k_c < 1이거나 k_c > n_c면 판정 행 없이 시도 실패(fail-closed, 저장 경합 방어. k_c < 1은 [#112 정정 11/04])
    - n_c = 0이면 그 대분류는 `skipped`
    - 어느 경우에도 정상을 가장하지 않는다. 시도 실패 뒤에는 작업 재시도(2절 '시도', 최대 3회)를 따르고, 모든 시도가 실패하면 작업이 판정 없이 실패한다(지표에서는 `run_jobs` failed로 따로 셈, 11.6)

## 8. 판정 등급 — 이상 / 이상 징후 [확정 11/02 C7, 키 이름 확정 11/03 U10]

| 조건(v2 정책, 같은 시점의 결과) | `grade` | 화면 |
|---|---|---|
| `categoryResults.hard`가 T | `anomaly` | 이상 |
| 종합 `anomalous=true`이고 hard가 T 아님(soft·ml만 T) | `anomaly_sign` | 이상 징후 |
| 종합 `anomalous=false` | `normal` | 정상 |
| 종합 `anomalous=null` | `undetermined` | 정상 여부 미확인 |

- k.hard = 1(U4)이면 "하드룰 T가 하나라도 있으면 이상"과 같다
- hard가 U여도 soft·ml이 T면 `anomaly_sign`이다(hard U가 이상 징후를 막지 않음) [확정 11/03 U8]
- 같은 시도 안에서 등급은 `anomaly_sign` → `anomaly`로만 바뀔 수 있다(T 방법이 늘기만 하므로). `anomaly`가 `anomaly_sign`으로 내려가는 일은 없다
- 등급과 status는 별개 축이다: 예 `partial` + `anomaly`(상태 F, ML 기한 초과)
- 등급과 위험도는 별개 축이다: 예 위험 단계 소프트룰만 T면 `anomaly_sign` + 위험. 화면은 둘을 함께 보여야 한다
- 등급은 Detector가 계산해 저장하고 API·WebUI·알림은 저장값만 쓴다(재계산 없음) [제안 D7]

## 9. 위험도

### 9.1 규칙 [확정 10/22 C14·C15, 11/02 C17]

- 이상(`anomalous=true`)일 때만 단계가 있다 [확정 10/22]. 정상·미확인 판정은 `not_applicable`(단계 `null`) [확정 11/03 U9① — 10/22 제안분]
- 근거 방법 = **T인 대분류 안의 T 방법**. k 미달 대분류의 T 방법은 근거가 아니다 [확정 11/03 U9② — 10/22 제안분]
- 방법별 단계(`riskLevel`)

| 대분류 | `riskLevel` |
|---|---|
| hard | 4.3 하드룰 정의의 `severity`. `byCode`면 `byCode[code]`(T 결과의 결과 코드). 기본 위험. `hard:process-status`는 시도에 고정한 룰 세트의 v2 항목 값을 쓴다(사용자 설정 [확정 11/04 사용자], v2 판별 [제안 D14]). v1 항목과 `methodVersion`이 없는 항목은 내장 기본값, v2 항목의 값 누락은 시도 실패다(10절) |
| soft | 그 규칙의 `severity`(사용자 선택, 5.1 대응) |
| ml | `null`(T일 때 `riskLevelReason="level_undefined"`) |

- 채움 규칙 [제안 D9]: `riskLevel`은 방법 결과가 T일 때만 채운다. N·U 방법은 대분류와 상관없이 `riskLevel=null`, `riskLevelReason=null`. T인데 단계 정의가 없으면(ML) `riskLevel=null`, `riskLevelReason="level_undefined"`

- 종합 `level` = 근거 방법 `riskLevel` 중 최고값(critical > warning > info). 합산·가중·대분류 겹침 승급 없음
- 근거 중 단계 있는 방법이 없으면 `status=unrated`(위험도 미정) [확정 10/22 Q-R4-1]. 일부만 있으면 `level`은 하한이고 단계 없는 근거는 `unratedMethods`에 나열 [확정 11/03 U9③ — 10/22 제안분]
- 같은 시도 안에서 위험도는 같거나 올라가기만 한다. 전제: 단계표 버전과 룰 세트 리비전(소프트룰 `severity`, `hard:process-status`의 코드별 단계 포함)을 **시도 시작 시 고정**(10절)

### 9.2 단계표

- 10/22 확정 구조(내장 목록 버전, 판정마다 기록)를 그대로 쓰고 대분류만 바꾼 새 버전 `method-defined-v2` [이름 확정 11/03 U10]: hard = 하드룰 정의 단계. 단 `hard:process-status`는 룰 세트 v2 항목 값([확정 11/04 사용자], 판별은 D14)이고, v1 항목과 `methodVersion`이 없는 항목은 내장 기본값, v2 항목의 값 누락은 시도 실패다. soft = 규칙 `severity`, ml = 정의 없음
- 계산 정책 이름 `risk-max-v1` [확정 11/03 U10]
- 단계표나 하드룰 내장 단계가 바뀌면 새 버전으로 새 판정부터 적용. 과거 판정은 기록된 버전 그대로. 룰 세트의 코드별 단계가 바뀌면 새 룰 세트 리비전으로 새 시도부터 적용

## 10. 룰 세트 리비전 고정 [확정 11/02 C6]

- 리비전 형식(정수 리비전 + 내용 해시, 리비전마다 룰 전체 스냅샷)과 저장 테이블은 [#94]가 정한다. 이 문서는 evidence 필드와 Detector 동작만 정한다
- 시도 시작 시 **(룰 세트 `revision`·`contentHash`, 활성 집합 리비전, 정책 리비전)을 한 번에 일관되게 읽어 고정**한다 [확정 11/03 U5·U11]. #94는 이를 운영 상태 조회 응답 한 건(`GET /api/operational-state`, 가칭)으로 제공한다(#94 `model-version.md` 활성 집합 절의 "일관 읽기 요구사항"). Detector는 뷰 `ops_operational_state` 한 문장으로 읽는다(`detector/operational_state.py`, #112). v03 작업은 작업 생성 때 고정한 구성을 그대로 쓴다(15절 K16)
- Detector는 **시도 시작 시** 운영 룰 세트의 현재 리비전 1개를 읽고, 그 시도의 모든 하드룰 켜기·끄기, `hard:process-status`의 코드별 단계, 소프트룰 정의·단계를 그 리비전에서 가져온다
- 시도 도중 룰이 저장돼도 그 시도는 시작 리비전을 끝까지 쓴다 [확정 11/02 C6]
- 다음 시도(재시도)는 그 시도 시작 시점의 리비전을 새로 읽는다 [확정 11/03 U5 — C6의 해석, 시도 단위]. 채택하지 않은 대안(기록용): 작업 단위 고정(첫 시도의 리비전을 재시도도 사용) — 리비전을 불변 작업 입력·큐 payload에 고정해야 해 계약 변경이 필요했음
- 판정 행 evidence와 중간 판정 기록 payload에 **매번** 기록

| 필드 | 형식 |
|---|---|
| `ruleSetRevision` | 정수. 단조 증가하는 리비전 번호 [#94] |
| `ruleSetHash` | 문자열. 룰 세트 내용의 SHA-256 `contentHash`를 그대로 복사 — **소문자 16진수 64자, 접두 없음**(`sha256:` 같은 접두 금지) [#94 `rule-set.md` 리비전 형식 절] |

- 운영 룰 세트를 읽지 못하거나 리비전·해시가 서로 맞지 않으면 시도 실패(fail-closed). 빈 룰 세트로 대신하지 않는다. Detector는 그 리비전의 스냅샷을 다시 읽어 `rule_set_content_hash_version`의 규칙으로 hash를 재계산해 대조한다(#112)
- 하드룰 스냅샷 해석 [제안 D14]: Detector는 스냅샷 `hardRules[]`의 `id`·`enabled`를 실행 입력으로 쓰고, 조건·`methodVersion`은 내장 목록(4.3)을 쓴다. 내장 목록에 있는데 스냅샷에 없는 규칙은 꺼짐으로 실행한다(방법 목록에 넣지 않고 n에 세지 않음). 규칙을 추가할 때마다 시스템 리비전을 만들 필요가 없고, 새 규칙이 말없이 켜지지 않으며, 사용자가 켜면 새 리비전으로 추적된다. 스냅샷 `id`가 내장 목록에 없거나 `enabled`가 bool이 아니면 저장 검증을 거치지 않은 비정상이므로 시도 실패다. 스냅샷에 같은 `id`가 두 번 나와도 시도 실패다
- `hard:process-status`의 코드별 단계: 사용자 설정 자체는 [확정 11/04 사용자](C27). 판별과 형식은 [제안 D14]다. 스냅샷 항목의 `methodVersion`이 `process-status-hr-v2`일 때만 `severity.byCode`를 사용자 설정으로 인정한다. v2 항목은 5코드가 모두 있어야 하고 값은 `critical`·`warning`·`info`다. 코드 누락, 모르는 코드 키, 잘못된 단계, 형식 오류는 그 규칙이 꺼져 있어도 시도 실패다. `process-status-hr-v1` 항목과 `methodVersion`이 없는 항목(`methodVersion: null`은 키 없음과 같다)의 `severity`는 그때 내장값의 투영이므로 값과 형식을 보지 않고 내장 기본값(4.3)을 쓴다(예 `015_rule_set_revisions.sql`의 r0). 그 밖의 `methodVersion`은 시도 실패다. 다른 하드룰의 스냅샷 `severity`·`methodVersion` 등 정의 값은 무시한다
- v1·legacy 정책 판정(룰이 구성 버전 `ml_config_rules`에 묶임, `computation.py:38-41`)에는 이 필드가 없다

## 11. evidence `schemaVersion` 4

### 11.1 적용 범위 [제안 D5]

- v2 정책 판정만 v4로 기록한다. v1·legacy 정책 판정은 현행 v3(`detector/runner.py`) 그대로 기록한다. 경로는 작업의 `contract_version`이 정한다: v04 작업 = v2 정책·evidence v4, v03 작업 = v1·legacy·evidence v3(#112)
- v3 키는 v4에서도 유지하고(추가만), 값의 의미도 바꾸지 않는다

### 11.2 예시 — 상태 S, 시작 ≤ 종료(`START_TIME > END_TIME` 아님), 건수 합계 일치, 소프트룰 1개 T(주의), IF 1개 N (r12 켜진 규칙 기준: #94 `rule-set.md` "룰 세트 상태" 절 예시의 r12는 내장 하드룰 4개를 모두 켠 상태)

```json
{
  "schemaVersion": 4,
  "rules": [
    {"ruleKey": "process-status", "version": "process-status-hr-v2", "sourceId": "<source UUID>",
     "observedStatus": "S", "result": "no_hit", "severity": null, "reason": null},
    {"ruleKey": "negative-duration", "version": "negative-duration-v1", "result": "no_hit"},
    {"ruleKey": "success-with-error", "version": "success-with-error-v1", "result": "no_hit"},
    {"ruleKey": "count-conservation", "version": "count-conservation-v1", "result": "no_hit"},
    {"ruleKey": "elapsed_ms-3f9a2c1d", "version": 12, "result": "hit"}
  ],
  "models": [{"modelKey": "iforest", "anomalous": false}],
  "checkers": {
    "hr": {"status": "succeeded", "anomalous": false, "evidence": ["<rules[0]과 같은 객체>"]},
    "sr": {"status": "skipped", "anomalous": null, "evidence": []},
    "ml": {"status": "succeeded", "anomalous": false, "evidence": [{"modelKey": "iforest", "anomalous": false}]}
  },
  "anomalous": true,
  "requestedMode": "parallel",
  "actualPaths": ["rule", "ml"],
  "detectionPolicy": {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1}, "deadlineMs": 3000},
  "detectionPolicyRevision": 0,
  "activeModelSetRevision": 3,
  "ruleSeverities": ["warning"],
  "ruleSetRevision": 12,
  "ruleSetHash": "<소문자 hex 64자>",
  "methodResults": [
    {"methodId": "hard:process-status", "category": "hard", "methodVersion": "process-status-hr-v2",
     "status": "succeeded", "anomalous": false, "code": null, "severity": null,
     "riskLevel": null, "riskLevelReason": null, "cause": null, "failureCode": null, "elapsedMs": 9.1,
     "evidence": [{"observedStatus": "S", "result": "no_hit"}]},
    {"methodId": "hard:negative-duration", "category": "hard", "methodVersion": "negative-duration-v1",
     "status": "succeeded", "anomalous": false, "code": null, "severity": null,
     "riskLevel": null, "riskLevelReason": null, "cause": null, "failureCode": null, "elapsedMs": 9.2,
     "evidence": [{"result": "no_hit"}]},
    {"methodId": "hard:success-with-error", "category": "hard", "methodVersion": "success-with-error-v1",
     "status": "succeeded", "anomalous": false, "code": null, "severity": null,
     "riskLevel": null, "riskLevelReason": null, "cause": null, "failureCode": null, "elapsedMs": 9.2,
     "evidence": [{"result": "no_hit"}]},
    {"methodId": "hard:count-conservation", "category": "hard", "methodVersion": "count-conservation-v1",
     "status": "succeeded", "anomalous": false, "code": null, "severity": null,
     "riskLevel": null, "riskLevelReason": null, "cause": null, "failureCode": null, "elapsedMs": 9.2,
     "evidence": [{"result": "no_hit"}]},
    {"methodId": "soft:elapsed_ms-3f9a2c1d", "category": "soft", "methodVersion": "soft-threshold-v1",
     "status": "succeeded", "anomalous": true, "code": "SOFT_ELAPSED_MS_3F9A2C1D", "severity": "warning",
     "riskLevel": "warning", "riskLevelReason": null, "cause": null, "failureCode": null, "elapsedMs": 9.3,
     "condition": {"field": "elapsed_ms", "operator": "gt", "threshold": "5000", "unit": "ms"},
     "observedValue": "8120", "evidence": []},
    {"methodId": "ml:iforest", "category": "ml", "methodVersion": "process-iforest-v1", "modelVersionId": "<#94 모델 버전 ID>",
     "status": "succeeded", "anomalous": false, "code": null, "severity": null,
     "riskLevel": null, "riskLevelReason": null, "cause": null, "failureCode": null, "elapsedMs": 21.7,
     "score": 0.53,
     "scoreDetail": {"raw": 0.53, "threshold": "0.61", "direction": "higher_is_anomaly",
                     "thresholdPolicy": "train-internal-p99-v1",
                     "normalized": {"kind": "threshold_ratio", "value": 0.869}},
     "evidence": []}
  ],
  "categoryResults": {
    "hard": {"n": 4, "k": 1, "result": "normal", "status": "succeeded", "anomalous": false},
    "soft": {"n": 1, "k": 1, "result": "anomalous", "status": "succeeded", "anomalous": true},
    "ml": {"n": 1, "k": 1, "result": "normal", "status": "succeeded", "anomalous": false}
  },
  "composite": {
    "policy": {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1}},
    "status": "complete",
    "anomalous": true,
    "grade": "anomaly_sign",
    "basisMethods": ["soft:elapsed_ms-3f9a2c1d"],
    "firstAnomalyElapsedMs": 9.3,
    "risk": {"policy": "risk-max-v1", "levelTable": "method-defined-v2", "status": "rated",
             "level": "warning", "determinedBy": ["soft:elapsed_ms-3f9a2c1d"], "unratedMethods": []}
  },
  "outcomes": {"classificationVersion": "detector-outcomes-v1"},
  "telemetry": {"...": "현행 그대로"},
  "statusRulePolicy": {"version": "process-status-hr-v2", "sourceId": "<source UUID>",
                       "mapping": {"S": "no_hit", "F": "critical", "P": "warning", "N": "info", "H": "info", "Z": "warning"}},
  "observation": {"id": "<UUID>", "revisionNo": 1, "inputAsOf": "<UTC>"},
  "inputManifestHash": "<hash>"
}
```

- 예시의 `elapsedMs`는 기한 기준점(`runner.py:29`, 작업 claim·DB 읽기 이전)부터 재므로 수 ms 이상이 정상이다. `firstAnomalyElapsedMs`(9.3)는 종합을 처음 이상으로 만든 소프트룰의 `elapsedMs`와 같다
- 예시의 `scoreDetail.normalized.value`(0.869)는 읽기 쉽게 반올림해 적은 표시다. 실제 기록값은 6절 식의 계산값 그대로이며 반올림하지 않는다 [#112 정정 11/04]

### 11.3 v3에서 유지하는 키 — 의미 불변

| 키 | v4 값 | 근거 |
|---|---|---|
| `rules` | 켜진 하드룰 전부 + 소프트룰을 `methodResults` 순서로. 하드룰·소프트룰 항목 `result` ∈ `hit` / `no_hit` / `unavailable` / `not_applicable`(조건 평가 생략 — 예 `hard:negative-duration`의 END_TIME 없음, END_TIME 없는 상태 N, 소프트룰 `elapsed_ms`의 END_TIME 누락. v3에 없던 값이며 집계에서는 정상(N)으로 셈). 상태 규칙은 현행 형태(`status_rule.py:24-27`), 다른 하드룰(예 `negative-duration`)은 `{ruleKey: <key>, version: <methodVersion>, result}`, 소프트룰은 `{ruleKey: <key>, version: ruleSetRevision, result}` [이름 확정 11/03 U10, 하드룰 포함 여부 제안 D9]. `reason` 키 적용 범위는 표 아래 | LLM 설명이 저장 evidence를 고정 근거로 씀(`pipeline.md` "판정·SSE·설명" 절 설명 문단). 설명 입력 키는 `rules`·`models`(10/21 결정 R8) |
| `models` | `{modelKey, anomalous}` 형태 유지, 활성 ML마다 1개 | 같음 |
| `checkers.hr` | **상태 규칙(`hard:process-status`)만** — v3 의미 그대로. 상태 규칙을 끈 경우 `{status: "skipped", anomalous: null, evidence: []}`(v3는 같은 경우 rule 경로가 돌면 `not_ready` + `status_policy_unavailable`(`status_rule.py:11-12`), 돌지 않으면 rule 경로 상태 + `status_rule_result_unavailable`(`computation.py:144-146`)) [제안 D2] | 교차표·운영 보고(`operationalReport.ts:110-118`)가 이 키를 읽음 |
| `checkers.sr` | v2에서는 항상 `skipped`(SR 없음) | 운영 보고의 `sr_experiment_pending` 집계(`operationalReport.ts:110`)는 v3 행에서만 늘어남 |
| `checkers.ml` | ML 대분류 결과(`categoryResults.ml`과 같은 status·anomalous) | |
| `anomalous` | `composite.anomalous`와 같은 값 | |
| `requestedMode` | 7.3 파생값 | 계측 concurrency(`runner.py:86`) |
| `actualPaths` | `rule`: hard·soft 중 succeeded 방법이 있음 / `ml`: succeeded ML이 있음 [확정 11/03 U10] | |
| `detectionPolicy` | 시도 시작 시 읽은 v2 객체 그대로 | |
| `ruleSeverities` | T인 소프트룰·하드룰의 `severity`를 `methodResults` 순서로 | v3는 Rule 키와 짝이 없는 hit 순서 목록(`computation.py:99-100`) — 방법별 값은 `methodResults[].severity` |
| `statusRulePolicy` | `hard:process-status`가 켜져 있으면 그 판정에 쓴 정책 객체 `{version: process-status-hr-v2, sourceId, mapping}`, 꺼져 있으면 `null`. `mapping`은 시도에 고정한 코드별 단계이고 S는 `no_hit`다. END_TIME 없는 N 예외는 `mapping`에 나타나지 않는다 [제안 D16] | 판독 호환 |
| `telemetry`, `observation`, `inputManifestHash`, `demo`, `input` | 그대로 | `runner.py:90-101` |

- **`rules[].reason` 키 적용 범위** [키 이름·값 확정 11/03 U10]
  - 상태 규칙 항목(`ruleKey: process-status`): v3 형태 그대로 `reason` 키를 항상 두며 의미도 v3 그대로다(`null` 또는 `status_unknown`, `status_rule.py:27`). END_TIME 없는 상태 N은 이상이 아니며(C26) `result: not_applicable`, `severity: null`, `reason: end_time_absent`로 기록한다 [제안 D16]. 기한 초과처럼 상태를 읽기 전에 끝나면 `observedStatus: null`, `result: unavailable`, `severity: null`, `reason: status_unknown`으로 기록한다 [제안 D9, #112 구현 확인 반영 11/04]
  - 그 밖의 하드룰 항목과 소프트룰 항목: `result: not_applicable`일 때**만** `reason`을 둔다. 값은 `end_time_absent`(하드룰 `hard:negative-duration`) 또는 `<원천 열 소문자>_absent`(소프트룰, 5.2). `hit`·`no_hit`·`unavailable`이면 `reason` 키를 두지 않는다(미판정 원인은 `methodResults[].cause`, 11.6)
  - 같은 `reason` 값을 그 방법의 `methodResults[].evidence`에도 둔다
  - #94 비교 평가 `notApplicableReasons`는 `result: not_applicable` 항목의 `reason`만 집계한다. 상태 규칙의 `status_unknown`은 대상이 아니다

### 11.4 새 키

| 키 | 뜻 | 상태 |
|---|---|---|
| `ruleSetRevision`, `ruleSetHash` | 10절 | 기록 의무 [확정 11/02 C6], 이름 [확정 11/03 U10] |
| `detectionPolicyRevision`, `activeModelSetRevision` | 정수. 시도에 고정한 정책 리비전(0 = 내장 기본 정책, 7.1)과 활성 집합 리비전(#94). 정책 값 자체는 `detectionPolicy`에 복사 | [제안 D11, U11] |
| `methodResults[]` | 활성 방법마다 1개. 순서: 하드룰(내장 목록 순) → 소프트룰(룰 세트 순) → ML(활성 집합 순서). 비활성 방법은 넣지 않음 | 방법별 결과 포함 [확정 10/21 Q8], 필드 [확정 11/03 U10] |
| `categoryResults.{hard,soft,ml}` | `{n, k, result, status, anomalous}`, `result` ∈ `anomalous`·`normal`·`undetermined`·`skipped`. `k`는 판정 기록용이며 화면에 표시하지 않음(C23) | 결과값 [확정 10/21 C8], status 매핑 [제안 D2] |
| `composite` | `policy`, `status`, `anomalous`, `grade`, `basisMethods`, `firstAnomalyElapsedMs`, `risk` | 계산 규칙 [확정 10/21 C8 조합·11/02 C7 등급·10/22 C14 위험도], 형태 [확정 11/03 U10] |
| `composite.policy` | 그 판정의 조합 계산에 쓴 정책의 조합 부분 `{version, policyKey, k}`. 값은 evidence `detectionPolicy`에서 `deadlineMs`만 뺀 것과 **정확히 같아야** 한다(기한은 조합 규칙이 아니라 실행 기한이며 전체 값은 `detectionPolicy`에 있음). Detector는 두 값을 시도에 고정한 같은 정책 객체에서 만든다. 중간 판정 기록 payload(13.3)도 같은 규칙. 화면에 표시하지 않음(C23) | 형태 [확정 11/03 U10], 정의 [제안 D9] |
| `composite.firstAnomalyElapsedMs` | 같은 시도에서 종합 `anomalous`가 처음 `true`가 된 시점(기한 기준점 `runner.py:29`부터 ms) = 그 순간 도착해 종합을 이상으로 만든 방법의 `elapsedMs`. 이상이 아니면 `null` | [제안 D9] |
| `composite.risk.determinedBy` | `status=rated`일 때 근거 방법 중 `riskLevel`이 종합 `level`과 같은 방법들의 `methodId`(`methodResults` 순서). `unrated`·`not_applicable`이면 `[]`. 단계 없는 근거는 `unratedMethods`에 둔다 | [제안 D9, #112 구현 확인 반영 11/04] |
| 최상위 `elapsedMs`(중간 판정 기록 payload만) | 스냅샷을 만든 시점(기한 기준점부터 ms). 11.5의 방법별 `methodResults[].elapsedMs`와 다른 키다. 판정 행 evidence에는 두지 않음(최종 소요는 `telemetry`) | [제안 D5] |
| `outcomes` | `{classificationVersion}`. 방법별 원인은 `methodResults[].cause`에 둠(중복 저장 안 함) | 계측 포함 [확정 10/21 Q8], 배치 [제안 D6] |

### 11.5 `methodResults[]` 항목 [필드 확정 11/03 U10]

| 필드 | 형식 | 비고 |
|---|---|---|
| `methodId` | `<category>:<key>` | 3.1 |
| `category` | `hard` / `soft` / `ml` | `methodId` 접두와 같음 |
| `methodVersion` | 문자열 | 하드룰은 4.3, 소프트룰은 `soft-threshold-v1`, ML은 산출물 형식 버전 |
| `status` | `succeeded` / `not_ready` / `failed` | 비활성은 배열에 없음 |
| `anomalous` | `true` / `false` / `null` | |
| `code` | 문자열 또는 `null` | T일 때만. 4.3·5.2 |
| `severity` | 방법이 낸 사실값(`critical`/`warning`/`info`) 또는 `null` | T일 때만 |
| `riskLevel`, `riskLevelReason` | 9.1 | |
| `cause` | 11.6 원인 코드, succeeded면 `null` | |
| `failureCode` | `cause=failure`일 때 세부 코드(11.6), 그 외 `null`. `failed` + `input_invalid`(5.2)도 `null` | [제안 D6, #112 구현 확인 반영 11/04] |
| `elapsedMs` | 기한 기준점(`runner.py:29` `started`)부터 이 방법의 상태가 정해진 시점까지 ms(측정값). 결과(`succeeded`)·실패(`failed`)·미판정(`not_ready`) 모두 그 상태가 정해진 시점을 기록한다. **기한 초과(`cause=timeout`)도 기한 도달을 판정한 시점의 측정값을 그대로 기록**한다(값이 `deadlineMs` 근처가 됨). `null`은 중간 판정 기록에서 아직 결과가 오지 않은 방법(`cause=pending`)뿐 | #66 계측. 기록 규칙 [제안 D9]. 시간 초과 판정 행의 처리 시간은 이 측정값을 그대로 표시 [확정 11/03 사용자(화면 흐름 결정 기록 9.9), C24] — 기한값을 짐작할 수 있는 점은 사용자가 감수 |
| `evidence` | 요약 객체 배열 | 원천 값 전체를 복사하지 않음 |
| `condition`, `observedValue` | 소프트룰만. 시도에서 쓴 정의와 관측값(문자열) | |
| `modelVersionId`, `score`, `scoreDetail` | ML만. 6절 | |

### 11.6 원인 분류 `detector-outcomes-v1` [제안 D6 — #66 계측 계획 흡수, 원인 코드 이름 확정 11/03 U10]

| `cause` | 뜻 |
|---|---|
| `input_missing` | 입력값 없음(상태 없음, 필드 결측) |
| `input_invalid` | 입력값 형식 오류(비숫자, 정의 밖 상태) |
| `feature_unavailable` | 파생변수 계산 실패 |
| `not_configured` | 산출물·판정선 등 실행 준비물 없음 |
| `not_executable` | 실행 코드 없음(사후 어댑터 소실 방어 경로) |
| `timeout` | 기한 초과(`deadline_exceeded`) |
| `capacity` | 실행 슬롯 부족(`worker_busy`) |
| `failure` | 계산 중 오류 |
| `pending` | 중간 판정 기록에서 아직 결과가 오지 않음. **최종 판정에는 쓰지 않음** |

- `failureCode` 허용 목록: `invalid_rule_definition`, `invalid_threshold`, `invalid_score`, `artifact_invalid`, `exception`(그 외) [제안 D6]
- 지표 정의(방법·대분류·종합 공통, 최종 판정 행 기준): 시도 A = 활성, 결정 D = `anomalous`가 boolean, 이상 H = `true`, 미판정 U = `null`. 판정 미생성은 `run_jobs` failed로 따로 센다 [제안 D6]

### 11.7 크기 상한 [룰 세트 편집 제약 포함 — #94와 합의, D10]

- 소프트룰 최대 32개(#94와 일치, 현행 등록 Rule 상한 `jobRoutes.ts:108`과 같음 — 확대는 두 세션 합의), 하드룰은 내장 목록 수, ML은 [#94] 동시 운영 상한
- `methodResults[].evidence`는 요약만 둔다

## 12. 판정 행 열 매핑 (`run_process_decisions`, 열 추가 없음)

| 열 | v2 정책 값 | 상태 |
|---|---|---|
| `status` | 7.3 판정 status | [확정 10/21] |
| `rule_status` | hard + soft 방법 전체에 7.3 대분류 status 매핑 적용 | [제안 D2] |
| `ml_status` | ML 방법 전체에 같은 매핑 | [제안 D2] |
| `decision_code` | 첫 근거 방법(T인 대분류 안의 T 방법, 9.1·`composite.basisMethods`)의 `code`. 순서: `hard:process-status` → 다른 하드룰(내장 목록 순) → 소프트룰(룰 세트 순) → ML(활성 집합 순서, IF는 `IF_ANOMALY`). 근거 방법이 없으면 `null` — k 미달 대분류의 T는 코드로 남기지 않는다 | [제안 D3], 근거 방법 기준은 [#112 정정 11/04] — 현행 v1 순서(상태 HR → 첫 hit Rule → `IF_ANOMALY`, `runner.py:102`)와 같은 방향 |
| `anomaly_score` | 활성 ML이 정확히 1개일 때만 그 원점수, 아니면 `null` | [제안 D3] — 현행 IF 점수 1개(`runner.py:124`) |
| `evidence` | 11절 v4 | |

- 판정 행은 불변이다(`004_erd_v03_contracts.sql:131-137`). 작업당 1행(`004:94` `job_id UNIQUE`)
- 후속 메모: IF 외 ML의 `decision_code`는 둘째 모델 추가 전에 정한다(6절)

## 13. 중간 판정 기록 — 정본

`pipeline.md` '판정·SSE·설명' 절은 이 절을 참조한다. 의미는 기존 계약과 012 migration을 그대로 옮긴 것이다 [확정 10/21 C11].

### 13.1 규칙

- 기록 시점: 완료 시각(기한) 전, 현재 시도가 lease를 가진 동안, **종합 결과가 처음 이상일 때(`first_anomaly`)와 이상 근거가 늘 때(`evidence_added`)만**
- 완료 시각을 넘긴 결과는 채택하지 않고 DB를 쓰지 않는다(중간 판정 기록 포함)
- 이상 근거가 없으면 최종까지 기록이 없다(화면은 작업 상태로 '판정 중' 표시)
- 최종 판정은 작업당 1건(판정 행)이며 활성 방법(v1은 선택 경로)이 모두 끝나거나 완료 시각에 도달한 시점의 결과다. 같은 시도의 최종 판정이 그 시도의 기록을 닫는다. 판정 행 뒤에는 중간 기록을 쓸 수 없다
- 이전 시도의 기록은 새 시도가 첫 이상 또는 최종을 기록하는 트랜잭션에서 '대체됨'(`superseded`)으로, 작업이 판정 없이 실패하면 '최종 없음'(`no_final`)으로 닫힌다. 둘 다 DB 트리거가 기록한다
- 중간 판정 기록은 최종 판정을 대신하지 않으며 설명·운영 보고·평가 대상이 아니다
- 같은 시도 안에서 종합 `anomalous=true`는 철회되지 않고 근거·등급·위험도는 같거나 올라가기만 한다. 철회는 대체됨·최종 없음 때만(드묾)

### 13.2 DB 근거 [코드 `012_decision_interims.sql`]

| 규칙 | 위치 |
|---|---|
| 표 `run_decision_interims`, kind 4값 `first_anomaly`·`evidence_added`·`superseded`·`no_final` | `:25-50`(kind `:30`) |
| 시도당 첫 이상 1건, 닫힘 1건 | `:52-53` |
| 첫 이상·근거 추가는 현재 시도·유효 lease만, 판정 행 뒤 금지, 근거 추가는 첫 이상 뒤에만 | `:67-78` |
| 대체됨 자동 기록(새 시도 첫 이상 또는 판정 행 INSERT와 같은 트랜잭션) | `:120-145` |
| 최종 없음 자동 기록(detect 작업 failed 전환과 같은 트랜잭션, 열린 기록이 있는 시도만) | `:149-174` |
| 이벤트 `run_decision_events.event_kind` 5값, 같은 순번 | `:178-205` |
| 최종 없음 뒤 판정 행 거부 | `:214-223` |
| `payload`는 JSON object만 강제, 내용은 Detector evidence 계약 | `:24`, `:35` |

### 13.3 payload 형식 [제안 D5]

- `evidence_version` 열 값: `detector-evidence-v4`
- `payload`(첫 이상·근거 추가만. 대체됨·최종 없음은 `{}`) = 그 시점 **전체 스냅샷**(차이분 아님). 아래 예시는 상태 F가 먼저 도착한 시점이며 `methodResults`·`categoryResults`·`risk`는 자리표시로 줄였다

```json
{"schemaVersion": 4, "ruleSetRevision": 12, "ruleSetHash": "<소문자 hex 64자>",
 "detectionPolicy": {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1}, "deadlineMs": 3000},
 "detectionPolicyRevision": 0,
 "activeModelSetRevision": 3,
 "methodResults": ["<도착한 결과 + 미도착 방법은 status not_ready, cause pending, elapsedMs null>"],
 "categoryResults": {"...": "11.4 형식(r12: hard n=4)"},
 "composite": {"policy": {"version": 2, "policyKey": "category-k-of-n-or-v1", "k": {"hard": 1, "soft": 1, "ml": 1}},
               "status": "partial", "anomalous": true, "grade": "anomaly", "basisMethods": ["hard:process-status"],
               "firstAnomalyElapsedMs": 9.1, "risk": {"...": "11.2 형식"}},
 "elapsedMs": 9.4}
```

- "근거가 늘었다"의 정의: `composite.basisMethods` 집합이 커질 때만 `evidence_added`를 쓴다. 등급·위험도는 근거가 늘 때만 바뀌므로 새 kind가 필요 없다 [제안 D5]
- 최종 트랜잭션에서 마지막 근거 추가를 따로 쓰지 않는다(판정 행 evidence가 최종)

## 14. 진리표 — v2 정책, 최종 판정(8행만 `k.ml=2` — U4 확정으로 1차 구현에서는 저장할 수 없는 값이며 규칙 설명용, 나머지는 모든 k=1)

| # | 상황 | hard | soft | ml | status | anomalous | grade | 위험도 | v1 현행과 차이 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 하드룰만, 상태 S | N | — | — | complete | false | normal | — | 같음 |
| 2 | 하드룰만, 켜진 하드룰이 모두 U(상태 규칙만 켜고 상태 없음, 또는 상태 없음 + END_TIME 있음 + START_TIME 없음) | U | — | — | unavailable | null | undetermined | — | 같음(v1의 하드룰은 상태 규칙뿐) |
| 2b | 기본 구성, 상태 없음 + 시각 규칙 N(시작 ≤ 종료 또는 END_TIME 없음) | U | — | — | **partial** | null | undetermined | — | v1은 unavailable(시각 규칙이 없음). 시각 규칙이 succeeded라 7.3에 따라 partial |
| 3 | 상태 F + IF 기한 초과 | T | — | U | partial | true | anomaly | 위험 | 같음 |
| 4 | 상태 P + IF N | T | — | N | complete | true | anomaly | 주의(U2) | 같음 |
| 5 | 상태 S + 소프트 T(주의) + IF N | N | T | N | complete | true | anomaly_sign | 주의 | 등급만 새로 생김 |
| 6 | 상태 S + 소프트 T(위험) + IF T | N | T | T | complete | true | anomaly_sign | 위험(하한, `unratedMethods=[ml:iforest]`) | 같음 |
| 7 | 상태 S + ML 3개 T·N·N | N | — | T | complete | true | anomaly_sign | 미정 | 다중 ML 없음 |
| 8 | 7과 같고 k.ml=2 | N | — | N | complete | false | normal | — | 다중 ML 없음 |
| 9 | 상태 S + ML 3개 N·U·N | N | — | U | partial | null | undetermined | — | 다중 ML 없음 |
| 10 | 상태 S + 소프트 T·U(결측) + IF N | N | T | N | partial | true | anomaly_sign | 소프트 단계 | 같음 |
| 11 | 상태 S + 소프트 N·U(결측), ML 없음 | N | U | — | **partial** | null | undetermined | — | v1은 unavailable(행동 변경 1) |
| 12 | 상태 F + 소프트 1개 비숫자, ML 없음 | T | U | — | **partial** | **true** | anomaly | 위험 | v1은 rule 경로 failed로 unavailable/null(행동 변경 2) |
| 13 | 상태 없음 + 소프트 T | U | T | — | partial | true | anomaly_sign | 소프트 단계 | 같음 |
| 14 | 하드·소프트 모두 끔, IF T | — | — | T | complete | true | anomaly_sign | 미정 | 같음(ml_only) |
| 15 | 상태 S + `START_TIME > END_TIME`(`hard:negative-duration`, U3) | T | — | — | complete | true | anomaly | 위험 | 새 규칙 |
| 16 | 상태 S + END_TIME 없음(`hard:negative-duration` → N, `not_applicable`) | N | — | — | complete | false | normal | — | 새 규칙(EndTime 없음은 이상 아님, C19) |
| 17 | 활성 방법 0개 / 활성 대분류 k < 1 또는 k > n | — | — | — | 판정 행 없음(저장 거부, 시도 시작 시 fail-closed) | — | — | — | 해당 없음 |
| 18 | 상태 S + `error_count > 0` | T | — | — | complete | true | anomaly | 위험 | 새 규칙 |
| 19 | 상태 S + 건수 합계 불일치 | T | — | — | complete | true | anomaly | 위험 | 새 규칙 |
| 20 | 상태 S + 건수 없음, 시각 정상 | U | — | — | **partial** | null | undetermined | — | v1은 complete/false(건수 규칙이 없음) |
| 21 | 상태 P + 건수 없음 | T | — | — | complete | true | anomaly | 주의 | 같음(건수 규칙은 대상 밖이라 N) |
| 22 | 상태 N + END_TIME 없음 | N | — | — | complete | **false** | normal | — | v1은 이상(주의) |
| 23 | 상태 N + END_TIME 있음 | T | — | — | complete | true | anomaly | 참고 | v1은 주의 |
| 24 | 상태 H | T | — | — | complete | true | anomaly | 참고 | v1은 미판정 |
| 25 | 상태 Z | T | — | — | complete | true | anomaly | 주의 | v1은 미판정 |

- 2행·2b행·17행의 k < 1은 [#112 정정 11/04]. 7.3 규칙은 바뀌지 않았고 표가 7.3을 정확히 옮기도록 조건만 좁혔다
- 18·19행의 규칙은 [확정 11/04 사용자 C25], 22행의 이상 아님과 25행의 이상·주의는 [확정 11/04 사용자 C26]이다. 23·24행의 이상 판정과 '참고'는 C26의 해석값에서 나온다. 20·21행의 U·N은 건수 규칙의 판단 순서 [제안 D15]에서 나온다. '기본 구성'은 내장 하드룰 4개가 모두 켜진 상태이며 DB 016의 r1 이후를 전제로 한다(r0만 있으면 새 규칙 2개는 꺼짐, 10절 D14). 표의 '상태 S'·'상태 F' 행은 건수 규칙이 켜져 있으면 건수가 맞는 입력을 전제로 한다(20행이 건수 없는 경우)

## 15. 현행 코드와의 충돌 — #92 시점 기록

- 표는 #92 작성 때의 코드 기준이다. #112가 v04 작업 경로에서 K1~K12·K16·K17의 Detector 쪽을 해소했고 v03 작업은 표의 현행 동작을 유지한다. K9의 다중 ML은 방법 단위 실행까지 해소했고 실행 어댑터는 IF뿐이다. K13·K14·K15(Trainer 평가의 `compute` 공유)는 남아 있다

| # | 현행 [코드] | 이 계약 | 해소 Issue(가칭) |
|---|---|---|---|
| K1 | `parse_policy`가 `detectionPolicy` 키 집합을 `{mode, deadlineMs, combination}`과 정확히 비교, v2는 `detection_policy_required` 예외(`policy.py:25-37`) | v2 파서 분기(7.2). v1·legacy 분기는 schemaVersion 1·2에만 | Detector 판정 구조 구현(조합 순수 함수·evidence v4) |
| K2 | 실행 단위가 경로 2개(`rule`·`ml`), 경로별 풀·슬롯(`policy.py:10-11`, `:40-80`) | 방법 단위 실행·결과(5.2, 행동 변경 2) | 같음 |
| K3 | `checkers.{hr,sr,ml}`, SR은 항상 `not_ready/sr_experiment_pending`(`computation.py:144-147`, `runner.py:88-89`) | `checkers` 키·의미 유지, v2 SR은 `skipped`, 대분류 결과는 `categoryResults.{hard,soft,ml}`(11.3) | 같음 |
| K4 | evidence `schemaVersion: 3`, 방법별 결과·리비전 없음(`runner.py:90-97`) | v4(11절) | 같음 |
| K5 | 등록 Rule을 구성 버전에서 읽음(`ml_config_rules`, `computation.py:38-41`), 구성은 작업 생성 때 고정(`runner.py:62-68`) | 소프트룰·하드룰 켜기는 운영 룰 세트에서 **시도 시작 시** 읽고 리비전 기록(10절) | 룰 세트 로더(Detector) + DB 저장 Issue [#94 후속] |
| K6 | 상태 HR은 `statusRulePolicy` 유무로 켬(`computation.py:24-28`) | 하드룰 켜기·끄기는 룰 세트 상태 | 같음 |
| K7 | Rule 비숫자 입력은 예외로 경로 전체 failed(`rules.py:41-46`, `policy.py:48-52`) | 그 소프트룰만 `failed`/`input_invalid`(5.2) | 소프트룰 실행 Issue |
| K8 | Rule DSL 원천 필드에 시각 필드 없음(`rules.py:9-11`) | `hard:negative-duration`은 내장 구현(4.2) | 하드룰 내장 목록 확장 Issue |
| K9 | ML 1개·IF만(`computation.py:35-36`, `:65-66`, `:114-115`), evidence `models`에 점수 없음(`:140-141`) | ML 방법마다 결과·`scoreDetail`(6절) | 판정 구조 구현 + 모델 다중 운영 Issue [#94 후속] |
| K10 | 방법별 severity 짝 없음(`computation.py:99-100`), `ruleSeverities` 순서 목록(`runner.py:97`) | `methodResults[].severity`·`riskLevel` | 판정 구조 구현 |
| K11 | `decision_code` = rule 경로 코드 또는 `IF_ANOMALY`(`runner.py:102`), `anomaly_score` = IF 점수 1개(`runner.py:124`) | 12절 순서·ML 1개일 때만 점수 | 판정 구조 구현 |
| K12 | 적용 준비·계측이 `Policy.mode`로 분기(`applications.py:46-49`, `runner.py:86`) | v2는 활성 방법 목록으로 판단, mode는 파생값 | 판정 구조 구현·v2 로더 |
| K13 | TS가 `detectionPolicy.mode ?? 'parallel'`로 조용히 기본값(`configRoutes.ts:107`), 목록이 `->>'mode'` 표시(`inputRoutes.ts:22`), 요청 타입이 v1 고정(`jobRoutes.ts:35`, `:94-102`) | `version` 분기, v2 모르는 판독기는 fail-closed | API 판독 분기 Issue |
| K14 | 운영 보고가 `checkers.sr` pending 수와 `checkers.hr`↔`checkers.ml` 교차표를 셈(`operationalReport.ts:110-118`), `failed` = `rule_status` 또는 `ml_status`가 failed(`operationalReport.ts:97`). Trainer 교차표는 SR 성공 행이 없으면 `sr_experiment_pending` 사유를 냄(`trainer/metrics.py:35-42`) | **수정 필요**: ① v2 행은 `checkers.sr=skipped`여도 `trainer/metrics.py:35-42`가 계속 `sr_experiment_pending`을 냄 → v2 행은 SR 비활성 사유로 구분 ② `rule_status`를 D2 매핑(hard+soft 중 failed 1개 이상 → failed)으로 쓰면 소프트룰 1개 실패만으로 운영 보고 `failed`(`operationalReport.ts:94-97`)가 늘어남(v1은 rule 경로 전체 예외일 때만) → 지표 정의를 문서화하거나 새 지표 버전 ③ 하드·소프트·ML 집계는 `categoryResults` 기반 새 지표 버전 | 계측 집계 Issue(Trainer·API 조율) |
| K15 | 평가가 Detector와 같은 `compute`를 호출(`trainer/runner.py:499`) | 같은 순수 함수 공유 유지(방법별·대분류·종합) | 판정 구조 구현 + Trainer 평가 |
| K16 | 모델 구성은 **작업 생성 때** 고정: detect 작업 INSERT가 `input_config_version_id`를 넣고(`jobs.py:66-71`), 실행 시 그 값을 읽으며(`runner.py:62-68`), 판정 행 `config_version_id`는 작업 고정 구성과 같아야 함(`004_erd_v03_contracts.sql:245`) | 시도 시작 시 (룰 세트 리비전·hash, 활성 집합 리비전, 정책 리비전)을 함께 읽어 고정(10절, 확정 11/03 U5 — 시도 단위). 활성 집합은 모델 0..N개라 판정 1건이 구성 버전 1개에 묶이지 않음 | U5(시도 단위)에 따른 DB(004:245 트리거·작업 입력·판정 행 `config_version_id` 의미)·Detector 로더 Issue [#94 후속] |
| K17 | 룰만으로 판정하는 경로가 없음: 현행 detect 작업은 `input_config_version_id`가 필수인 구성 ID로 만들어지고(`jobs.py:66-71`), 옛 경로는 구성이 없으면 `active_config_unavailable`(`runner.py:35-37`) | 활성 집합이 비어도(모델 0개) 룰만으로 판정 가능해야 함. 정책은 운영 설정 전역 값(없으면 리비전 0 기본 정책, 7.1 — U11) | DB 작업 입력(`input_config_version_id` 필수 해소)·판정 행 `config_version_id` 처리 + Detector 로더 Issue [#94 후속] |

## 16. 다른 계약과의 정합 — Trainer #94 반영 요청

대조 기준: dev `a87de60`(11/04, #106·#110·#111 포함). 이 Issue는 #94 소유 문서를 수정하지 않는다. #94 문서는 행 번호 대신 문서 이름과 절·필드 이름으로 가리킨다.

- **남은 요청: C27 후속 1건(아래)**(dev `a87de60`에서 다시 확인). 그 밖에 6ff10b5·d8937f9·fa8c390·673463d의 구조 변경(Q-M1 확정 표기·조합 저장 폐기 포함, 활성 집합 0..N, `detectionPolicy` 운영 설정 전역 값, 일관 읽기 3요소, evidence `activeModelSetRevision`·`detectionPolicyRevision`, `detection_methods_empty` 모든 저장 경로(룰 세트 저장·`retire`·`replace`·`restore`), k > n 규칙(min 보정 금지, n = 0 무시 — 7.4), `resultCounts`의 `not_applicable` 정상 집계, 소프트룰 `sourceMissingPolicy`)은 이 문서 5.2·6·7.1·7.4·10·11절과 같은 뜻이다
- **C27 후속 요청**(#94 소유, Trainer #116·PR #118 진행 중, 이 Issue는 고치지 않음): `rule-set.md`의 `hard:process-status` 위험도 편집(v2 항목, 5코드 전부), 하드룰 `severity`를 hash 입력에 넣는 hash v2(`contentHashVersion: 2`), 스냅샷에 없는 내장 규칙의 꺼짐 처리, DB 016의 r1(새 규칙 2개 켜기). 병합 전 dev의 r0는 v1 항목이며 10절 D14대로 값을 보지 않는다
- 해소됨(기록): 다음 요청은 #106으로 dev에 반영됐다 — 정정 요청 1건(`model-version.md`의 `k.ml`·`n_ml` 정합과 Q-M8, U4 반영), `rule-set.md` `softRules[].field` 문구, '결정 대기' 표기 6종(U1·U2·U3·U12)
- 남은 표기(요청 아님): `rule-set.md` 리비전 형식 절의 "[합의 11/02 — 사용자 확인 필요 (b)]"는 #94 자체 질문이며 U1~U12에 포함되지 않는다. 이 문서는 그 상태를 바꾸지 않는다
- 조건부 요청 — **현재 발동하지 않음**: U4가 11/03 추천안(k 모두 1)으로 확정되어 조건(k ≥ 2 허용)이 성립하지 않는다. 나중에 사용자가 U4를 완화할 때만 `rule-set.md` "저장 — `PUT /api/rule-set`" 절 검증 표에 "저장 결과 켜진 하드룰·소프트룰 수가 `k.hard`·`k.soft` 미만이 되면 422 `combination_k_out_of_range:<category>`(0이 되는 경우 제외)" 추가를 요청한다(7.4). dev 검증 표에는 이 행이 없고, 지금은 없는 것이 맞다
- `queues.md`: #112에서 v04 detect 큐 payload에 `configVersionId`가 없어져 메시지 참조 문구 조율이 필요하다(소유 세션 조율 대기, 이 Issue는 고치지 않음)
- 확인(요청 아님)
  - #94 Q-M10 확정(판정 정책 화면 미노출, API는 운영 담당용)과 11/02 사용자 결정(화면 흐름 결정 기록 9.8, 판정 상세 '기준 k' 열 제거)은 이 문서의 판정 의미에 영향이 없다. evidence의 `detectionPolicy`·`composite.policy`·`categoryResults.*.k`는 판정 재구성용 기록이며 화면에 표시하지 않는다(C23)
  - 운영 설정 저장 값: `model-version.md` 운영 설정 절은 값 형식을 "Detector 소유 v2 객체"로 두고 400 `invalid_detection_policy`(형태·혼용·`version: 1`)를 적는다. 이 문서 7.2 "운영 설정에는 v2만 저장"과 같은 뜻이다
  - #94의 `categoryResults` 대분류 단위 `skipped`(켜진 룰·방법 0개)는 이 문서 11.4 `result: skipped`·7.3 매핑(방법 0개 → `skipped`)과 같다. 비교 평가의 `measurementStatus: skipped`는 #94 집계 필드라 충돌 없음
  - #94 비교 평가는 하드룰 행에만 `notApplicableReasons`를 두지만, 이 문서는 소프트룰 `not_applicable`에도 reason(`<열>_absent`)을 기록한다(5.2, 11.3 `reason` 적용 범위). 집계 칸을 늘릴지는 #94 판단
  - `hardRules[]` 표시 문구: #94 `rule-set.md` "룰 세트 상태" 절 예시의 `displayName`·`conditionText` 두 행은 이 문서 4.3 표와 글자까지 같고, 11/02 사용자 승인(화면 흐름 결정 기록 9.7, C22) 문구와도 같다. #94는 "정본은 #92 4.3"으로 표기함
  - 모델 운영 단위(#94 Q-M1)는 11/02 사용자 결정으로 확정(C20) — 이 문서 6절과 #94 본문이 같다
  - 시간 초과 처리 시간(11/03 화면 흐름 결정 기록 9.9, C24): `methodResults[].elapsedMs`의 기록 규칙(11.5)만 정한다. 운영 상태 집계·표시 API는 #94가 아니라 API·WebUI 계약 범위다

## 17. 결정

### 17.1 사용자 결정 결과(U1~U12) — 11/03 모두 추천안으로 확정

11/03 사용자가 U1~U12 전부에 "추천대로"라고 답했다. 아래는 확정된 내용이다. 각 항목의 본문 반영 위치에는 [확정 11/03 Un]으로 표기했다.

| # | 결정 주제 | 확정 내용 | 반영 위치 |
|---|---|---|---|
| U1 | 계약·코드 식별자(화면 이름은 C18로 이미 확정) | 1.1 세션 합의 A1·A2 승인. 방법 식별자 `hard:<key>` / `soft:<key>` / `ml:<key>`, 계산용 대분류 키 `hard` / `soft` / `ml`(`detectionPolicy.k` 등). 과거 `hr:`·`rule:`·`sr:` 식별자는 과거 판정 읽기용 매핑만. 근거: 11/02 결정(룰 하드·소프트 / ML 분리)을 이름에 옮긴 것이고, 이전 `{hr, sr, ml}` 형태는 코드·DB에 저장된 적이 없어 이전 비용이 없음 | 1.1, 2, 3.1, 3.2, 7.1, 7.3 |
| U2 | `hard:process-status`의 P·N 위험도 | 10/22 확정값 유지: F = 위험, P·N = 주의. "하드룰 기본 위험"은 규칙이 따로 정하지 않을 때의 기본값이고, P·N은 "먹통·실패 확정 금지" 계약(`trainer-service.md` "책임·범위" 절)이 있음. 등급은 둘 다 '이상'. 11/02 사용자 결정(화면 흐름 결정 기록 9.7)과도 같음. N 단계는 11/04 C26이 대체 | 4.2, 4.3, 14 |
| U3 | 하드룰 1차 목록과 정의 밖 상태값 | (가) 1차 목록 = `hard:process-status` + `hard:negative-duration` (나) S·F·P·N 밖 상태값(H·Z 등)과 결측은 지금처럼 미판정. 나머지 후보(`hard:success-with-error`·`hard:count-conservation`·`hard:invalid-count-domain`·`hard:invalid-status`)는 실데이터 발생 빈도를 확인한 뒤 하나씩(건수 필드 의미 미검증). `hard:final-missing-end`는 11/02 결정으로 제외(C19). 11/04 C25·C26이 일부를 이어받음(후보 2개 추가, H·Z 코드 확정). `hard:invalid-status` 제거는 D16 | 4.2, 4.3, 14 |
| U4 | k 고정 | 1차 구현은 정책 저장 시 `k.hard`·`k.soft`·`k.ml`이 모두 1이어야 함(아니면 422). n = 0인 대분류의 k는 판정 계산·k > n 검사에서 무시(비활성). 근거: 룰 세트·활성 집합이 버전 없이 바뀌어도 k = 1이면 k > n이 생기지 않아 저장 검증이 `detection_methods_empty` 하나로 줄고, "하드룰 T 하나 = 이상" 정의와 맞음. k ≥ 2는 다중 ML 평가 근거가 생기면 다시 질문(그때 양방향 저장 검증 추가) | 7.4, 8 |
| U5 | 시도 시작 고정 범위 | 시도 단위: 룰 세트 리비전·활성 집합·정책 리비전을 시도마다 그 시도 시작 시점에 한 번에 새로 읽음(#94 운영 상태 조회 응답 한 건과 같음). 근거: 이전 시도 중간 기록은 '대체됨'으로 닫히고 판정마다 세 리비전이 기록돼 추적 가능. 작업 단위 고정은 세 리비전을 불변 작업 입력·큐 payload에 넣어야 함. 판정 행 구성 일치 트리거(`004:245`)·작업 입력 변경이 필요(15절 K16) | 10, 15 K16 |
| U6 | 과거 판정(evidence v3 이하) 표시 | 등급 없음(null), 이상 판정만 위험도 '미정', 정상·미확인 판정은 위험도 '해당 없음', 과거 등록 Rule 결과(임계값형·비임계값 Rule — 문자 비교·묶음 조건 포함)는 '이상 징후 규칙(과거 등록 Rule)'으로 표시만. 과거 판정 재계산·재작성 없음(10/21 확정) | 3.2 |
| U7 | `pipeline.md` "API 작업" 절 train 항목의 severity 문장 | "단일 severity 합산 규칙을 임의로 만들지 않는다" → "위험도는 판정 구조 계약의 위험도 정책(근거 방법 단계 중 최고값)으로만 계산하고, 점수·방법 수를 합산한 단일 severity를 만들지 않는다"로 교체(10/22 최고값 결정과 같은 뜻) | `pipeline.md` 반영 완료(11/03) |
| U8 | 하드룰 미판정 + 소프트룰·ML만 이상 | '이상 징후'로 표시. 같은 시도에서 하드룰 결과가 나중에 이상으로 오면 '이상'으로 올라감. 미판정 하드룰 때문에 확인된 이상 징후를 숨기지 않음 | 8 |
| U9 | 위험도 세부 3개(10/22 제안분) | ① 정상·미확인 판정은 위험도 '해당 없음' ② k 미달 대분류의 이상 방법은 위험도 근거에서 제외 ③ 근거 일부만 단계가 있으면 그 최고값을 하한으로 쓰고 단계 없는 근거(ML)를 함께 표시 | 9.1 |
| U10 | 이름·형식 일괄 | 검증 오류 코드(7.4), 정책 키 `category-k-of-n-or-v1`, evidence 필드 이름(`ruleSetRevision`·`ruleSetHash`·`methodResults`·`categoryResults`·`basisMethods`·`cause`·`failureCode` 등, 11.3~11.6), 원인 코드 이름(11.6), `not_applicable` 사유 키 `reason`과 값(`end_time_absent`, `<열>_absent`), 등급 키 `anomaly`/`anomaly_sign`/`normal`/`undetermined`, 결과 코드 형식 `HARD_<KEY>`·`SOFT_<KEY>`, `risk-max-v1`·`method-defined-v2`·`threshold_ratio`. 판정 화면 문구(이상·이상 징후·정상·정상 여부 미확인)는 WebUI가 사용자 승인 후 사용, 방법 표시 이름은 C18 | 4.3, 5.2, 6, 7.1, 7.4, 8, 9.2, 11 |
| U11 | `detectionPolicy` 저장 위치 | **#94 Q-M2 확정의 귀결**: 운영 설정 전역 값(버전 없이 즉시 적용, 리비전 이력, 미저장이면 리비전 0 = 내장 기본 정책). 이전 대안(`k.hard`·`k.soft`·`deadlineMs`는 룰 세트, `k.ml`은 모델 버전)은 C20으로 `k.ml`을 둘 버전이 없어져 #94 Q-M2에서 폐기됨. 근거: k가 모두 1로 고정되면(U4) 나눠 둘 실익이 작고, 정책이 한 객체·한 리비전 체계로 남아 판정마다 `detectionPolicyRevision` 하나로 추적됨 | 7.1, 7.2, 10, 11.4, 15 K17 |
| U12 | v1 정책 판별 형식 | `detectionPolicy`에 `version` 키가 없을 때만 v1로 읽고 `version: 1`은 400 `invalid_detection_policy`. #74 계약 초안(#94 병합 전 판본)에 [확정 10/21]로 적힌 "`version`이 없거나 1이면 v1"을 바꿈(10/21 결정 기록에는 없는 문장이며 v1·v2 키 혼용 금지와 충돌). 현행 `policy.py:30`도 이 입력을 거부, #94 `model-version.md` `detectionPolicy` 행도 같은 안 | 7.2 |

### 17.2 Detector 결정 — 정보 제공(이견이 없으면 이대로 진행)

| # | 내용 | 근거·비고 |
|---|---|---|
| D1 | `hard:negative-duration`: 허용 오차 0ms(`START_TIME > END_TIME`이면 이상), 시각은 PROCESS 관측 `allowed_values`의 `start_time_raw`·`end_time_raw`를 처리 시간(`datetime_diff`, `derived_expression.py`)과 같은 방식으로 해석한다 — 수집기가 `parsed_times`를 항상 빈 객체(`{}`)로 저장해(`collector/storage.py`, `collector/input_store.py`) 쓸 수 없음(#112 구현 확인 반영 11/04). 원천 시각은 시간대 없는 `YYYY-MM-DD HH:MM:SS[.소수]` 문자열이고, 같은 행의 두 시각은 같은 기준으로 비교한다. 값 없음은 빈 문자열 또는 `\N`이다. 판정 순서: ① END_TIME 없음 → N(`not_applicable`, `end_time_absent`, START_TIME도 없어도 N) — 이상 아님은 확정 C19, N 매핑은 이 제안(U면 정상 판정이 막힘). ② START_TIME 없음 → 미판정(`input_missing`) ③ 값이 있으나 해석 불가 → 미판정(`input_invalid`) ④ 비교 | 연구 기본 허용 오차 0ms(`research/anomaly_detection/code/process_anomaly_pipeline/config.py:33`) |
| D2 | `checkers.*`는 v3 의미 유지(`checkers.hr` = 상태 규칙만, 끄면 `skipped`; v2 `checkers.sr` = `skipped`), 대분류 결과는 `categoryResults`. 대분류 status 매핑과 `rule_status`(hard+soft)·`ml_status`는 7.3 매핑 | 운영 보고·교차표 소비처 키 유지. 단 집계 영향은 15절 K14 |
| D3 | `decision_code`는 첫 근거 방법의 코드([#112 정정 11/04]), 순서 hard(상태 규칙 우선) → soft → ml, `anomaly_score`는 활성 ML이 1개일 때만 | 현행 v1 순서와 같은 방향(`runner.py:102`, `:124`) |
| D4 | 소프트룰 입력: 결측 = `not_ready` + `input_missing`, 비숫자·비유한 = `failed` + `input_invalid`(파생 필드의 원천 비숫자·시각 해석 불가는 `not_ready` + `feature_unavailable`, #112 구현 확인 반영 11/04). 둘 다 미판정이며 정상으로 바꾸지 않음. 예외: 카탈로그 `sourceMissingPolicy = not_applicable`인 원천 열 누락(예 `elapsed_ms`의 END_TIME)은 N(`not_applicable`, `<열>_absent`) | `pipeline.md` "판정·SSE·설명" 절 첫 문단("미완료 상태를 정상으로 바꾸지 않고") 원칙, 11/02 C19, #94 `method-catalog.md` `sourceMissingPolicy` |
| D5 | evidence v4는 v2 정책 판정에만(v1·legacy는 v3 유지). 중간 판정 기록 payload = 전체 스냅샷, `evidence_version=detector-evidence-v4`, 근거 증가 = `basisMethods` 집합 확대, payload `elapsedMs` = 스냅샷 시점 | 012 `payload`는 object만 강제(`012_decision_interims.sql:35`) |
| D6 | 방법별 원인은 `methodResults[].cause` 한 곳에만 둠. `failureCode` 목록·지표 정의(11.6). 상태 규칙 `status_unknown`은 값 없음 → `input_missing`, 정의 밖 값 → `input_invalid`로 대응. 하드룰 해석 불가는 `not_ready`, 소프트룰 비숫자는 `failed`(둘 다 `input_invalid`, 4.1 이유). `failureCode`는 `cause=failure`일 때만, 소프트룰 임계값 오류는 `invalid_threshold`(#112 구현 확인 반영 11/04) | 현행 코드는 `reason: status_unknown` 하나(`status_rule.py:27`) |
| D7 | 등급·위험도는 Detector가 계산·저장하고 API·WebUI·알림은 저장값만 사용 | `pipeline.md` "판정·SSE·설명" 절 판정 조회 문단 "API는 risk를 재계산해 확정 판정을 덮어쓰지 않는다"와 같은 방향 |
| D8 | (사용자 결정 U12로 이동 — 다른 계약의 10/21 확정 문구를 바꾸는 항목, 11/03 확정) | — |
| D9 | `riskLevel`은 방법 결과가 T일 때만 채움(ML T는 `null` + `level_undefined`, N·U는 둘 다 `null`). `rules[]`에 켜진 하드룰을 모두 v3 형태로 포함하고, 하드룰 key는 `-<8 hex>`로 끝나지 않게 정해 소프트룰 key와 구분. `firstAnomalyElapsedMs` = 종합을 처음 이상으로 만든 방법의 `elapsedMs`. `methodResults[].elapsedMs`는 상태가 정해진 시점의 측정값이며 기한 초과도 측정값 그대로(`null`은 중간 기록의 `pending`뿐, 11.5). `composite.policy` = evidence `detectionPolicy`에서 `deadlineMs`를 뺀 값(11.4). `composite.risk.determinedBy`(11.4)와 상태 규칙의 기한 초과 기록(11.3)은 #112 구현 확인 반영 11/04 | 11.2 예시와 일치. 기한 초과 측정값 표시는 11/03 사용자 결정(화면 흐름 결정 기록 9.9, C24) |
| D10 | **#94와 합의해 결정**: 소프트룰 임계값 형식(저장·API는 JSON number, hash 계산·Detector 전달·evidence 기록 때만 고정소수 문자열로 변환·지수 표기 금지, 5.1)·단위 변환 없음, 소프트룰 최대 32개(#94와 일치, 확대는 두 세션 합의), evidence 크기 상한 | 룰 세트 편집 제약은 #94 소유 |
| D11 | evidence에 리비전 필드 2개: `detectionPolicyRevision`(정수, 0 = 내장 기본 정책)·`activeModelSetRevision`(정수). 정책 값은 `detectionPolicy`에 복사(U11) | 판정 1건에서 그때의 정책·활성 집합을 재구성(10/21 R6 추적성). 리비전 번호 체계는 [#94] |
| D12 | `sr:*` 방법을 실행 중 만나면(저장 검증을 거치지 않은 예외 상황) 판정 행 없이 시도 실패(fail-closed). 저장 단계 거부와 오류 코드는 [#94] | 11/02 C2(통계 SR 제외) |
| D13 | 하드룰 정의 형식: `{id, methodVersion, displayName, conditionText, severity}`, `severity`는 단일 문자열 또는 결과 코드별 `{"byCode": {...}}`(4.3). `hard:process-status`의 기본값은 C26(F `critical`, P·Z `warning`, H·N `info`). `hard:negative-duration`의 `methodVersion` 이름 `negative-duration-v1`. **`displayName`·`conditionText` 화면 문구는 11/02 사용자 승인(화면 흐름 결정 기록 9.7)으로 확정**(C22, 4.3 표 값 그대로) | #94 `rule-set.md` `hardRules[]` 필드가 이 형식을 따름. 형식·이름은 Detector 결정, 문구는 사용자 확정 |
| D14 | 룰 세트 스냅샷 해석: `hardRules[]`는 `id`·`enabled`를 실행 입력으로 읽고 조건·`methodVersion`은 내장 목록을 쓴다. 스냅샷에 없는 내장 규칙은 꺼짐, 미지 `id`, 중복 `id`, bool 아닌 `enabled`는 시도 실패. `hard:process-status`는 항목의 `methodVersion`이 v2일 때만 `severity.byCode`를 사용자 설정으로 인정하고 5코드 전부를 요구하며, v1 항목·키 없음(`null` 포함)은 값을 무시하고 내장 기본값, 그 밖의 버전은 시도 실패(10절) | 꺼짐으로 두면 규칙 추가마다 시스템 리비전 migration이 필요 없고 배포 순서가 어긋나도 판정이 멈추지 않으며, 새 규칙이 말없이 켜지지 않음. v1 항목의 `severity`는 사용자가 정한 값이 아니라 저장 당시 내장값의 투영(015 r0의 N `warning`)이라 사용자 설정으로 읽으면 안 됨 |
| D15 | 건수 규칙은 상태를 먼저 보고, 건수는 Decimal로 정확히 비교한다. 정수 여부와 음수는 보지 않으며 대상 밖 상태는 N이다(4.2). `hard:count-conservation`의 대상 S·F는 총괄 결정이고 H는 일시 상태라 제외한다. 14절 20행의 U와 21행의 N은 이 순서에서 나온다 | 정수·음수 검사는 후보 `hard:invalid-count-domain`의 범위. 수집기도 건수를 정수로 강제하지 않음(`collector/storage.py` `_typed_values`) |
| D16 | `hard:process-status` 구현 세부: 코드 집합과 N 조건이 바뀌어 `process-status-hr-v2`로 올림. 표시 문구는 코드를 나열하지 않고 유지. 후보 `hard:invalid-status` 제거. END_TIME 없는 N은 `not_applicable`·`end_time_absent`로 기록. 사용자 단계 변경은 새 `methodVersion`이 아니라 룰 세트 리비전과 `statusRulePolicy.mapping`으로 추적하며 `mapping`은 시도에 고정한 코드별 단계(S는 `no_hit`) | C26·C27의 구현 형식. 사용자 결정이 아님 |
| D17 | 건수 규칙 2개의 이름·버전·코드·문구: `hard:success-with-error`(`success-with-error-v1`, `HARD_SUCCESS_WITH_ERROR`, "성공 상태이나 오류 건수가 기록됨", "STATUS = S, ERROR_COUNT > 0"), `hard:count-conservation`(`count-conservation-v1`, `HARD_COUNT_CONSERVATION`, "건수 합계 불일치", "TOTAL_COUNT ≠ SUCCESS_COUNT + ERROR_COUNT") | C25의 구현 형식. `displayName`은 사용자 확정 11/04, `conditionText`는 Detector 제안 |

## 이력

- 2026-11-05: #112 2차 — dev 병합(016·#111) 뒤 정리. 11.2 예시를 내장 하드룰 4개 기준으로 정정, 2차 구현으로 바뀐 사실 반영(4.2·4.3 구현 상태, 6절 다중 ML 실행, 10절 뷰 읽기·hash 재계산, 11.1 경로 판별, 15절 머리말, 16절 `queues.md`). 11/04 이력 표현 정리(사용자 결정은 `displayName`만)
- 2026-11-04: 사용자 결정 반영 — 새 하드룰 `displayName` 확정(`hard:success-with-error` "성공 상태이나 오류 건수가 기록됨", `hard:count-conservation` "건수 합계 불일치", 4.3·D17). `conditionText`는 사용자 결정이 아니며 [제안 D17] 그대로다. 같은 날 표기 정리(사용자 결정 아님) — C26·4.2의 상태 없음을 U3(나) 유지로, 4.3의 코드별 단계를 룰 세트 v2 항목 값으로 통일
- 2026-11-04: #112 재검토 반영 — 16절 대조 기준을 dev `a87de60`으로 갱신하고 #106에서 해소된 요청을 정리해 남은 요청을 C27 후속 1건으로, 9절 hard 단계를 D14와 맞춤(v1 항목·키 없음은 내장 기본값, v2 항목의 값 누락은 시도 실패), H와 END_TIME 있는 N의 T 판정을 C26 해석값 파생으로 표기, '기본 구성'의 DB 016 r1 전제, `count-conservation` 대상의 결정 주체, `methodVersion: null`·중복 `id` 처리 명시
- 2026-11-04: #112 검토 FAIL 반영 — 저장된 v1 `severity`를 사용자 설정으로 읽지 않도록 10절·4.3 정정(D14 판별 규칙, v2 항목은 5코드 필수), 11/04 항목을 사용자 결정(C25~C27)과 Detector 결정(D14~D17)으로 분리, H·N '참고'가 해석값임을 명시, '구현됨'을 실행 경로 미연결로 정정, `rule-set.md` 낡은 권고 정리·C27 후속 요청 추가
- 2026-11-04: 사용자 결정 반영 — 건수 규칙 2개 추가(C25, 14절 18·19행), 상태 코드 뜻·F·P·Z 단계·END_TIME 없는 N 이상 아님·정의 밖 값 미판정(C26, 14절 22~25행), 코드별 위험도 사용자 설정(C27, 4.1·4.3·9.1·9.2·10). Detector 결정 — 스냅샷 해석(D14), 건수 규칙 공통(D15, 14절 20·21행), `process-status-hr-v2`·`hard:invalid-status` 제거·표시 문구·기록 형식(D16), 새 규칙 이름·문구(D17). 구현 정정 보완 — 14절 2행 END_TIME 있음, 4.1 단어 문자열 조건 삭제, 11.2는 r12 켜진 규칙 기준임을 명시
- 2026-11-04: #112 구현 확인 정정. 사용자 승인분 — 14절 2행 조건을 '켜진 하드룰이 모두 U'로 좁히고 기본 구성 2b행 추가(7.3 불변, 현행 `compute` 실행으로 2행 '같음' 재확인), 12절 `decision_code`를 첫 근거 방법으로, 7.4 실행 시점·14절 17행에 k < 1, 4.1·5.2 값 없음 기준(빈 문자열·공백·`\N`, 단어 문자열은 값 없음 아님), 11.2 `normalized.value` 반올림 표시. Detector 결정 반영 — D1 입력을 `allowed_values` 시각 필드로(`parsed_times`는 항상 비어 있음), 5.2 임계값 오류 행과 파생 필드 계산 실패 범위(D4·D6), 11.4 `determinedBy` 정의(D9), 11.5 `failureCode` 조건(D6), 11.3 상태 규칙 기한 초과 기록(D9). 후속 메모 — 6·12절 IF 외 ML 결과 코드
- 2026-11-03: 사용자 결정 반영 — U1~U12 모두 추천안으로 확정(C21), 본문 "[제안 Un]"·"[합의 — 사용자 확인 필요]"를 [확정 11/03 Un]으로, 1.1 A1·A2 확정(U1), U11은 #94 Q-M2 확정의 귀결로 표기, 17.1을 결정 결과 표로, `pipeline.md` "API 작업" 절 severity 문장 교체(U7). 11/02 사용자 결정(화면 흐름 결정 기록 9.7) 이상 규칙 화면 문구 확정(C22, 4.3·D13)·P·N '주의' 유지, 판정 정책 화면 미노출(C23, 화면 흐름 결정 기록 9.6·9.8), 11/03 사용자 결정(화면 흐름 결정 기록 9.9) 기한 초과 `elapsedMs` 측정값 기록·표시(C24, 11.5). 9차 reviewer 비차단 권고 반영 — 7.4 n = 0 대분류 k 처리(U4 적용 시 정책 저장은 세 값 모두 검사, k > n·계산에서는 무시), 실행 시 활성 방법 0개 시도 실패, 교차 저장 직렬화(#94·API 소유), 7.2 운영 설정은 v2만, 11.3 `reason` 적용 범위, C19·C20 출처 표기, `composite.policy` 정의(11.4). Trainer 교차 검토 경미 지적 반영 — 7.4 검증 경로에 `restore`, 5.1 `id` 행, 현행 계약 인용을 dev 기준 절·필드 이름으로(#94 병합 전 문장은 판본 표기). dev 병합(#94 `935e5eb` 포함) 후 16절을 병합본 기준으로 재확인 — 남은 요청 없음 유지, 조건부 요청은 U4 확정으로 미발동, 비차단 권고 유지, #94 문서의 낡은 '결정 대기' 표기 후속 정정 권고 추가. 10차 검토 권고 5건 반영 — 17.1 추천 근거 복원(U3·U4·U5·U11), 비공개 결정 기록 절 번호를 '화면 흐름 결정 기록 9.x'로 통일, 16절 `model-version.md` k.ml 문구를 정정 요청으로 격상(S4), 7.2 출처, 11.4 최상위 `elapsedMs` 구분
- 2026-11-02: 8차 최종 검토 FAIL 반영 — 7.4 k·n 규칙 재작성(n=0 대분류 k 무시, U4 승인 시 정책 저장 k 모두 1 강제, 완화 시에만 양방향 검증·룰 세트 쪽 #94 조건부 요청, 실행 시 n≥1·k>n fail-closed / n=0 skipped), 7.2 열 제목·v3 spec 행, 16절 기준 673463d·Q-M10 무영향·비차단 권고, `not_applicable` 선택 키 `reason`, END_TIME 없음 우선 판정 순서(4.2·5.2·D1), U11 대안을 #94 폐기안으로·추천안 표기 통일, U11 추천안 전제 표기
- 2026-11-02: #94 fa8c390(최종본) 대조 — 16절 기준 변경, 4.3·16절 표시 문구를 "#94 예시도 같은 값(정본은 이 표)"으로, `sr:*` 근거의 (B) 조합 저장 절 상태를 폐기로. `git diff d8937f9 fa8c390`에서 #92와 모순 없음
- 2026-11-02: 사용자 11/02 Q-M1 확정 반영 — C20 추가, 6절 운영 단위 [확정 11/02], 대안(묶음) 문장 삭제, 16절 확인 항목 갱신. 정책 저장 위치(U11·#94 Q-M2)는 결정 대기 유지
- 2026-11-02: #94 d8937f9 최종 대조 — 16절 기준 변경(남은 요청 없음), 임시 표기를 #94 실제 절 이름으로(`model-version.md` 활성 집합 절·운영 설정 절·일관 읽기 요구사항), 모델 단위는 #94에서도 사용자 결정 대기(Q-M1)로 정정, `sr:*` 거부 코드 근거 갱신((B) 절 보류), `active_model_key_duplicate` 인용, 소프트룰 `sourceMissingPolicy = not_applicable` → N(5.2·D4), 11.3 `result` 값 목록을 소프트룰까지
- 2026-11-02: 7.4 검증 위치 #94와 합의 반영 — `detection_methods_empty`는 모든 저장 경로(룰 세트·retire·활성 집합 교체·제거), k > n은 min 평가 금지·저장 시 양방향 검증(n=0 제외)·실행 시 fail-closed, U4 승인 시 의미 정리
- 2026-11-02: #94 구조 변경(11/02 화면 흐름 결정 기록 9.4 결정, 두 세션 합의) 반영 — 모델 운영 단위 = 활성 집합 0..N(`ml:<modelKey>` 유일, 순서 = 활성 집합 순서), `detectionPolicy` = 운영 설정 전역 값(리비전 0 = 내장 기본), evidence `detectionPolicySource` → `detectionPolicyRevision`·`activeModelSetRevision`(D11), 시도 시작 일관 읽기 3종(U5), K16·K17 재작성, U11 근거 정리
- 2026-11-02: 사용자 11/02 결정(EndTime 미기록 정상 유형) 반영 — C19 추가, `hard:final-missing-end` 제외, `hard:negative-duration`의 END_TIME 없음 → N(`not_applicable`, D1), 11.3 `rules[].result`에 `not_applicable`, U3 후보 목록, 진리표 16행 추가
- 2026-11-02: 6차 재검토 FAIL 반영 — 11.2 예시를 r12 상태(하드룰 2개)와 맞춤(`hard:negative-duration` 결과 추가, `categoryResults.hard.n=2`), 13.3 payload에 `detectionPolicySource`·`composite.policy`, 진리표 15행·D1 표현 통일
- 2026-11-02: 5차 재검토 FAIL 반영 — `hard:negative-duration` 표시 문구를 #94 고정본과 일치(`START_TIME > END_TIME`), D10 임계값 형식, 7.4 룰 세트 저장 검증을 U4 조건부로, 예시 리비전 r12, 일관 읽기 참조 절 이름, 하드룰 `not_ready`·소프트룰 `failed` 이유(D6), #94 병합 전 행 표기, C10 출처
- 2026-11-02: 4차 재검토 FAIL 반영 — #94 인용을 행 번호 대신 문서·절·필드 이름으로, 16절을 #94 e21cabe 기준(남은 요청 없음, `categoryResults` skipped 확인만), `threshold`는 스냅샷 number·evidence 고정소수 문자열, 4.3 표시 문구·U2 대안의 새 `methodVersion`, 9.1 hard 참조, C6 확정분·전제 분리, U12 출처, U6 비임계값 Rule 표시
- 2026-11-02: 3차 재검토 FAIL 반영 — 16절을 #94 1edacbe 기준으로 다시 씀(남은 요청은 `hardRules[].severity` 형식 1건 + 경미 2건), 하드룰 정의 형식(D13, `byCode`), #94 오류 코드 정확화, C6 출처, U12 분리(v1 판별), 비임계값 과거 Rule 표시, 소프트룰 `threshold` 전달 형식
- 2026-11-02: 재검토 FAIL 반영 — `sr:*` 거부 위치·코드(#94), U2 참조, #94 형식 맞춤(소프트룰 id `soft:<fieldId>-<8 hex>`·`elapsed_ms`, `ruleSetHash` 소문자 hex 64자, 상한 32 일치), 모델 버전 시도 시작 고정(U5)·충돌 K16·K17, #94 요청 2건, 10/21 결정 번호 표기, U4 분리·U11 신설, v1 판별을 U10으로, `sr:*` 실행 중 방어 D12, 예시 elapsedMs, 연구 규칙 제외 사유 분리, C6 출처
- 2026-11-02: reviewer FAIL 반영 — 세션 합의(식별자·대분류 키)를 [합의]로 분리, 화면 용어(이상 규칙·이상 징후 규칙·머신러닝 모델) C18 확정 반영, v1 판별 모순 해소(`version` 키 없음만 v1), 인용 행 정정, 과거 판정 위험도, C17 출처 분리, 10/22 제안분 표기, evidence 예시·채움 규칙, `checkers.hr` 꺼짐·`rules[]` 하드룰, 재시도 리비전 질문, K14 영향, 결정 질문을 사용자 10문항(U1~U10) + Detector 결정 표(D1~D11)로 정리
- 2026-11-02: 초안 작성(Issue #92). 11/02 탐지 구조 개편·10/21 조합 규칙·10/22 위험도 틀 결정을 하드·소프트·ML 구조로 묶음. #66 계측 계획을 evidence v4에 흡수

Created: 2026-10-21T00:00:00+09:00
Updated: 2026-11-02T20:00:00+09:00
Author: Claude Code (Trainer 세션 builder)
Status: draft (호환 안내 — 내용은 분리된 두 계약으로 이동)

# 판정 구성 버전 계약 — 분리 안내 (Issue #94)

- 변경 요약(#94): #74의 `detection-config-v1`(HR·rule·SR·ML을 한 `methods[]`에 담는 configuration_spec v3)은 **사용자 11/02 결정**(탐지 구조 룰·모델 분리 — 룰은 저장 즉시 적용, 버전·교체·비교는 모델만)에 따라 두 계약으로 분리됨. 이 파일은 기존 링크·문서 참조를 위해 남긴 안내이며 계약 본문이 아님

| 분리된 계약 | 파일 | 내용 |
|---|---|---|
| 룰 세트 `rule-set-v1` | [rule-set.md](rule-set.md) | 하드룰 켜기/끄기 + 소프트룰(등록 필드·조건·임계값·위험도). 버전 없음, 저장 즉시 운영 적용, 리비전 + 스냅샷 이력, 되돌리기 = 새 저장, 동시 편집 409 |
| 모델 버전 `model-version-v1` | [model-version.md](model-version.md) | `ml:*` 1개를 담는 불변 버전(configuration_spec v3 재정의, 모델 1개 = 버전 계열·같은 modelKey 1개 — 사용자 11/02 확정), 활성 모델 집합(활성화·운영에서 빼기·교체·복원·리비전), 운영 설정 `detectionPolicy` 전역 값, 자동 비교 평가 연결 |
| 종합 판정 | detection-decision.md(#92, Detector) | `detectionPolicy` v2 `k:{hard,soft,ml}`의 의미(값 저장은 model-version.md 운영 설정), 조합 규칙, 이상/이상 징후 등급, evidence v4, 하드룰 내장 목록, 소프트룰 실행 의미론 |

## #74 초안에서 사라진 것

- `methods[]`의 `hr:*`·`rule:*`·`sr:*` 항목, `statusRulePolicy`·`ruleVersionIds` 파생 복사본(v1·v2 행에만 남음), `ml_config_rules` v3 행(항상 0행)
- 통계 SR R00~R23 카탈로그 등재(`sr:*` 폐기, [method-catalog.md](method-catalog.md) 호환 표)
- 식별자 kind `hr`·`rule`·`sr` → `hard`·`soft`(룰 세트)·`ml`(모델 버전). 호환 매핑은 [rule-set.md](rule-set.md) 식별자 절
- `method-result-v1` 자리 초안 → detection-decision.md(#92) evidence v4 방법별 결과 형식 참조

## #74에서 그대로 가져온 것(모델 단위)

- 내용 hash(`contentHashVersion: 2`), 파라미터 변경=재학습, 기술 무결성 gate + 비교 + 사람 수락·자동 교체 금지, `acceptedRecommendation`·stale 400, schemaVersion fail-closed(`unsupported_config_schema`), v3 허용 시점(Detector v3 로더 병합 뒤)
- #74에서 **가져오지 않은 것**(사용자 11/02 Q-M1 확정 — 모델 1개 = 버전 1개, 조합 저장 경로 없음): 조합 저장 동기 API·검증 코드, `enabled:false` 보존, 중복 재저장 안내(`duplicateOf`, 학습 = 버전이라 재저장 자체가 없음 — model-version.md Q-M5)
- 저장소 사실: `ml_config_versions` 등은 `004_feature_pipeline.sql:84-96`의 `pipeline_immutable` 트리거로 이미 불변 — #74 "선택: 불변 트리거"는 불필요

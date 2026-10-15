# 피처 v5·규칙 v2 평가 자료

- [최종 집계](comparison_r02.json), [개수](counts.json)
- [남은6건](remaining_errors.json), [현재 원천별 피처 상태](candidate_usage_v5.json)
- [과거·현재 카탈로그 구분](catalog_versions.json)
- [검토 기록](root_evaluation_review.json), [규칙 검토](root_rule_review.json)
- [모델과 실행 도구](../../models/feature_rule_design_v0_2/README.md)

`feature_inputs/`는 공개 경로를 제거한20개 프로파일, `feature_outputs/v4/`·`v5/`는 원래 응답40개다. `rule_inputs/`·`rule_outputs/`는 검토한 실자료 피처4개의 규칙 판단12개다. 모델 응답 문자열은 유지하며 고객 원본 행과 행별 파생 벡터는 제외했다.

같은20개 입력에 v4·v5를 비교한 결과3/20→14/20이며, 자유 발굴은0/8→6/8이다. 이전 v4 실험16개와 평가 구성이 다르다. 입력 통과·후보 통과·보류 판단·채택 정의·규칙 제안 수를 구분한다. 12개 규칙 판단은 규칙12개를 뜻하지 않는다.

저장 자료의 SHA 검증과 규칙 계약 재검사는 CPU로 가능하지만 원본 CSV 재측정은 불가능하다. 기존 측정·의미 검토 기록을 읽는 것을 새로운 측정이나 독립 업무 검증으로 세지 않는다. 원래 프롬프트와 공개 입력의 차이는 [변환 기록](input_provenance.json)에 있다.

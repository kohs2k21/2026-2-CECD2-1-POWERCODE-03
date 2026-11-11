# 계약과 권한

- 입력: approved-metadata-capture1.0. 승인·계보·Body 제외·파일 SHA·원래 키·연결 관계를 검사한다.
- Feature 출력: feature5.2. 호출당 후보 1개 또는 구체적인 후보0개 판단. 기존 feature5.1은 `result_use=value`의 명시적 호환 경로로 읽는다. 과거 응답은 수정하지 않는다.
- AI 선택: 목적·좁은 의미·AST·원천·관계·분석 단위·모집단·필터·그룹·필요 근거·result_use. 코드 확정: 계산으로 유일하게 정해지는 타입·확인 단위·결측 정책·ID·SHA·실제 측정. 충돌은 오류로 반환한다.
- result-view1.0 + inner-type validation: value는 원래 결과, unavailable은 is_missing(결과), unavailable_or_zero는 숫자 결과의 is_missing이면 true, 아니면 eq0인 일반 lazy 연산으로 컴파일한다. 모델 원문과 컴파일 IR은 모두 보존한다. 입력의 calculation_type은 내부 계산 타입이며 최종 Boolean 타입과 구분한다. 이 선택은 의도 추정에 의한 정답 교체가 아니다.
- 행 단위: child_count/conditional_count/child_sum은 관계의 부모 행마다, parent_match_count는 자식 행마다 반환한다. 스칼라 리터럴 외의 피연산자는 같은 행이어야 한다. grain.keys는 행 식별자이며 계산 컬럼·관계 키와 다르다.
- 타입·논리: 숫자와 Boolean은 다르다. 집합 원소 타입은 동일해야 한다. not/in/not_in의 원천 결측은 unavailable이다. 기존 strict nullable AND/OR는 어느 피연산자든 unavailable이면 unavailable이며 변경하지 않는다.
- guarded-conditional1.1-null-branches: if의 조건은 Boolean, 두 가지 모두 정적으로 검사하고 선택한 가지만 실행한다. 명시적 literal null은 bottom 타입으로 다른 하나의 구체 타입을 채택한다. 숫자/문자/Boolean 혼합을 허용하는 규칙이 아니다. unknown 조건은 unavailable, 선택한 가지의 실행 오류는 오류이다.
- is_missing은 컬럼·중첩 결과·관계 결과에 공통 적용하며 유지된 모든 행에서 완전 Boolean을 반환한다. 원천 결측 원인이나 장애를 증명하지 않는다. 필터 제외 행은 출력 없음이며 false나 unavailable로 채우지 않는다.
- 측정: Decimal precision50의 독립 local context. 0 / unavailable / 선택 행0 / 원천·실행 오류를 구분한다. 비교는 숫자 유효행만, is_missing은 보존된 결측행까지 평가한다. 현재 관측·참조 관측·조건 발동 수는 별개이다.
- 공식 사실: official-computation-facts1.0 / official-condition-facts1.0. author=compiler, not_AI_generated=true. 가능한 출력 상태·빈 자식 사례·조건 분모를 제공한다. 원본 AI 설명, 자동 설명 검사, 실제 모델 수정, 사람의 사후 검토를 구분한다. 자유 문장 전체를 증명하지 않는다.
- 설명 개정: 계산 AST·모집단·필터·그룹·행 단위·타입·단위·결측·스키마·원천이 같아야 측정을 재사용한다. 설명 revision SHA는 별도다. 계산 조건이 달라지면 새 definition SHA·새 측정·새 Rule 바인딩이 필요하다.
- Rule: 기존 v3 evidence-bound roles. proposed는 실행 가능한 근거 있는 조건이며 needed=[]이다. deferred는 조건은 알지만 현재 관측이 부족하다. request_evidence는 필수 근거가 없으며 조건을 발명하지 않는다. no_applicable은 지원되는 조건도 미해결 근거 요청도 없다. 상태와 condition/evidence/needed/reason을 함께 검사한다.
- HR: 확인된 DDL/계약만 사용한다. 현재 위반0이어도 계약은 유지할 수 있다. 물리 관계만으로 부모 필수 존재 HR을 만들지 않는다.
- 관측 SR: 현재 진단 근거에 한정한다. 과거 기준선이나 임의 최소 표본20을 일괄 요구하지 않는다. feature unavailable이 is_missing 조건의 unassessed를 뜻하지 않는다.
- 수치 SR: 별도 승인 earlier TRAIN, 동일 계산·모집단·그룹·단위·필터, 참조 기간·표본 조건·분위수 방법 linear(n-1)*p·비교 연산자·결측·갱신/만료 범위를 연결한다. 관측 최대값은 증명된 상한이 아니다. 참조 없는/부족한 그룹에 임의 fallback·현재 p95 대체를 하지 않는다. 현재 값은 그대로 보존한다.
- 수정: 최대1회/단계. 실패 필드·실제 값·반례/근거와 보존 필드를 전달한다. 정상 역할은 보존하고 실패 역할만 모델이 수정한다. 조립 후 전체 묶음을 검사한다. 코드가 조건·상태·설명을 정답으로 채우지 않는다.

문법은 JSON·지원 연산·실제 선언 원천·행 단위·자료형을 제한하며 목적에 맞는 정답 AST·역할·임계값을 강제하지 않는다. 이전 계약의 점수와 원문은 유지한다. 검증기 보완 효과와 모델 가중치 효과를 합산하지 않는다.

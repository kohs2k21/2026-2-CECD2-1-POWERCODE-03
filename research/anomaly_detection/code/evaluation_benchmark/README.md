# 평가셋 생성 코드

현재 평가 범위(2026-10-18): 개별 지연·집단 지연·시간대 폭주·입력 중단·점진적 악화의 **5개 task**.
컬럼 관계·Hard 혼합은 생성 CLI와 현재 평가에서 제외했다. 기존 두 task는
`reports/retired_tasks_20261018`에 복구 가능하게 보관한다. 내부의 과거 Case 구현과
기존 bit 위치는 옛 실험 재현용이며 현재 평가에 포함하지 않는다.

표본 수 감사(2026-10-23): 기존 최소 30행은 통계적으로 검증된 충분 표본 수가 아니라
휴리스틱이다. 수학적 한계·원형 표본 수·분위수 민감도와 폭주/중단의 다른 가정은
[SUPPORT_EVIDENCE.md](SUPPORT_EVIDENCE.md)에 정리했다. 활성 생성 조건·평가 CSV·모델은 변경하지 않았다.

세 기간의 원본을 읽어 반합성 Process 평가셋을 생성한다. 원본 CSV, SQL 스키마,
기존 모델과 분석 노트북은 수정하지 않는다. 결과 설명은 `data/evaluation/README.md`를 먼저 읽는다.

## 실행 순서

프로젝트 루트에서 기존 분석 환경을 사용한다. Python에는 numpy, pandas, scikit-learn,
joblib이 필요하다. CSV 내보내기는 제공된 `@oai/artifact-tool` Node 환경을 사용한다.

```powershell
& .analysis-venv/Scripts/python.exe code/evaluation_benchmark/prepare.py --output reports/evaluation_v2_work/inputs.json
& .analysis-venv/Scripts/python.exe code/evaluation_benchmark/test_generation.py
& .analysis-venv/Scripts/python.exe code/evaluation_benchmark/generate.py --inputs reports/evaluation_v2_work/inputs.json --output reports/evaluation_v2_work/generated --cases-per-scenario 5000 --seed 20261010
node --expose-gc code/evaluation_benchmark/author_csv.mjs reports/evaluation_v2_work/generated reports/evaluation_v2_new --workers 3
& .analysis-venv/Scripts/python.exe code/evaluation_benchmark/validate.py reports/evaluation_v2_new
```

`author_csv.mjs`는 기존 CSV를 덮어쓰지 않는다. 새 빈 출력 위치를 사용한다.
중단된 내보내기를 재개할 때는 `--resume`을 추가한다. 완료한 파일은 해시로 확인하고,
완료 manifest가 없는 평가셋의 지정된 부분 CSV만 다시 작성한다. 알 수 없는 파일은 보존한다.
독립적인 검증이 모두 통과한 후에만 기존 평가셋을 교체한다.
`--cases-per-scenario`는 행 수가 아니라 이상 사례 수다. 실패해도 자동으로 수량을 줄이지 않는다.

## 코드 역할

- `prepare.py`: Transaction 해시 기반 층화 표본 선정, 전체 Process 재조회,
  원본 부모 Transaction 확인, 고정 하드룰 정책과 원본 해시 저장.
- `generate.py`: 독립 사례별 정상 실행 재생, 현재 5유형 장애 주입,
  대응 정상 대조군과 정답 생성. IF 점수는 읽거나 사용하지 않음.
- `author_csv.mjs`: 제한된 크기의 블록을 typed 값으로 작성·비교 후 CSV 내보내기.
- `validate.py`: CSV를 다시 읽어 실제 규칙·고정 분위수·SQL·의존 관계·정답 검증.
- `test_generation.py`: 재현성, 모든 시나리오, Hard의 6유형, 잘못된 입력,
  전체 기준 fallback와 엄격한 `>` 경계 테스트.
- `load_case.py`: 단일 사례의 Process 입력 로드. 중단으로 삭제된 실행도 정상 대조군에서 복구.
- `repair.py`: 지연 증가·실제 상한 여유 조건을 만족하지 못한 사례만 결정적으로 재생성.
  `author_csv.mjs --repair`는 나머지 CSV 행을 그대로 보존하고 교체 행만 작성.

생성 캐시와 중간 JSON은 평가 입력이 아니다. Git 업로드에는 원본 고객 데이터,
비밀값, 환경 파일을 포함하지 않는다. Git 저장소 밖에서 수행된 로컬 생성은
Issue·브랜치·PR 작업 완료나 CI 통과를 의미하지 않는다.

# ADBench를 ESB 로그 평가에 사용하는 방법

## 결론

**현재 ESB 시나리오 평가를 대체하기보다, 독립적인 표 형식 스트레스 평가와 알고리즘 비교에 보완적으로 사용한다.** 이번 실험에서는 공식 저장소의 문서·생성 코드·평가 코드를 검토했다. ADBench를 설치/실행하지 않았고, ADBench로 만든 새 데이터나 결과는 없다. 기존 평가 CSV도 변경하지 않았다.

논문: Songqiao Han, Xiyang Hu, Hailiang Huang, Mingqi Jiang, Yue Zhao, **ADBench: Anomaly Detection Benchmark**, NeurIPS 2022. [공식 저장소와 논문 링크](https://github.com/Minqi824/ADBench). 아래 구현 설명은 2026-10-16 확인한 main 기준이며 버전 갱신 시 재확인이 필요하다.

## 무엇을 제공하나

- 여러 분야의 57개 표 형식 데이터셋, 30개 이상탐지 알고리즘 비교 체계. ESB 운영 로그 전용 시간 시나리오 생성기는 아님.
- `X`, `y` 형태의 사용자 데이터와 사용자 알고리즘도 연결 가능. 우리의 시간창 피처 행렬을 별도 실험에 사용할 수 있음.
- 점수 기반 ROC-AUC와 AP(Average Precision)를 제공. 이번 비교에도 두 지표를 추가했지만, ADBench를 실행한 결과는 아님.

근거: [README](https://github.com/Minqi824/ADBench), [run.py](https://github.com/Minqi824/ADBench/blob/main/adbench/run.py), [myutils.py](https://github.com/Minqi824/ADBench/blob/main/adbench/myutils.py).

## 합성 이상 생성 방식과 우리 시나리오의 관계

| ADBench 생성 유형 | 공식 코드의 방식 | 우리 평가에 쓸 수 있는 범위 |
| --- | --- | --- |
| local | 정상 GMM의 공분산 확대 | 비슷한 정상 사례 주변에서 벗어나는 표본. LOF 등 국소 비교의 스트레스 테스트 |
| global | 특성별 범위를 이용한 균등분포 표본 | 큰 수치 이탈 테스트. 처리건수·소요시간의 업무 제약은 별도 검사 필요 |
| dependency | 정상 vine copula, 이상은 컬럼별 독립 KDE | 컬럼 사이 관계 붕괴 테스트. 하드 룰을 위반하지 않도록 별도 제약 필요 |
| cluster | GMM 평균 변경 | 정상 유형에서 떨어진 새로운 표본 군집. **여러 시점의 집단 지연과 동일한 개념은 아님** |

공식 생성기는 일부 모드에서 정상 표본도 다시 합성한다. 단순히 기존 정상 로그 몇 행을 수정하는 생성기가 아니다. [공식 data_generator.py](https://github.com/Minqi824/ADBench/blob/main/adbench/datasets/data_generator.py).

ADBench의 네 유형은 시간대 폭주·입력 중단·점진적 악화라는 시간 시나리오와 일대일 대응하지 않는다. 특히 입력 중단은 원본 행이 없는 시간창을 만들어야 하므로 숫자 벡터의 독립 표본 생성만으로 검증할 수 없다. 이는 공식 생성 코드의 표본 단위 처리 방식에 근거한 본 프로젝트의 적용 판단이다.

## 그대로 실행하면 안 되는 부분

공식 `DataGenerator.generator`는 기본적으로 shuffle/stratify 무작위 train/test 분할을 사용하고, 10,000행 초과 시 서브샘플링하며, 작은 데이터의 중복 생성을 선택할 수 있다. 이는 해당 벤치마크의 설정이지 결함이 아니다. 하지만 우리의 서로 겹치는 시간창·같은 원본 실행·동일 재생 사례를 무작위 행 분할하면 누출이 생길 수 있다. [구현](https://github.com/Minqi824/ADBench/blob/main/adbench/datasets/data_generator.py).

따라서 ESB 평가는 다음을 유지한다.

1. 원본 실행/재생 사례 단위 분리와 가능한 시간 순서 유지. 겹치는 시간창을 학습·테스트로 흩뜨리지 않음.
2. 기준 통계·스케일러는 학습 데이터에서만 계산. threshold는 독립 정상 개발 데이터에서만 설정.
3. 실패 STATUS를 빼고 정상 후보만 학습하는 현재 프로토콜과, 이상이 섞인 비지도 학습 프로토콜을 별도 명시. `la=0`이 학습 데이터에 이상이 없다는 뜻은 아님.
4. 정확도·Precision·Recall·F1·정상 오탐률, AP·ROC-AUC, 시나리오별 사건 검출/오탐/탐지 지연을 구분. 사건 검출이 되었다고 구간 전체를 맞힌 것으로 바꾸지 않음.
5. 학습 오염 비율·무관 피처 추가·여러 seed 같은 강건성 실험은 별도 실험으로 사전 고정.

## 권장 구성

- **주 평가**: 실제 Process 원본을 바탕으로 제약을 지킨 현재 5개 시간 시나리오, 컬럼 관계, Hard. S 유지, 하드 룰 검증, 입력 중단의 빈 창 보존.
- **보조 평가**: 학습 파티션의 피처 행렬만 이용해 ADBench 유형별 표 형식 이상 생성. 원시 로그와 동치라고 주장하지 않음. 동일 생성기만 잘 잡는지 점검.
- **외부 일반성 평가**: 적절한 공개 ADBench 데이터셋에서 IF·K-means·LOF를 새로 학습하여 비교. 공개 데이터에는 ESB Adapter 컬럼이 없으므로 Adapter 피처 효과 검증에는 직접 쓰지 않음.

ADBench의 피처 벡터를 원시 `processes.csv`로 역변환하는 것은 별도 과제다. 소요시간 파생 피처 하나를 바꾸더라도 start/endTime과 관련 시간창 피처들을 일관되게 다시 생성하고, STATUS=S·건수 정합성·하드 룰 통과를 확인해야 한다. 파생 컬럼을 서로 독립적으로 조작한 뒤 정상적인 실시간 로그라고 간주하지 않는다.

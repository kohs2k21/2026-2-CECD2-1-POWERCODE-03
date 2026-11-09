# 대규모 로그 데이터의 핵심 특징과 이상탐지 모델 설계 근거

연구 주제: **대규모 로그 데이터 기반 비지도 학습 이상탐지 AI 모델 개발**

<mention-date start="2026-10-06"/> 기준 · 세 분석 노트북에 저장된 결과를 종합한 핵심 정리

<callout icon="💡" color="blue_bg">
	**한 문장 결론**<br>이 데이터는 단순히 ‘양이 많은 동일한 로그’가 아니라, **기간마다 업무·실행 구조가 다르고 특정 기간·업무에 편중되어 있으며 일부 완료·연결 정보가 불완전한 계층형 구조화 로그**입니다. 따라서 이상탐지는 ‘큰 값 찾기’에 그치지 않고 **업무 맥락, 수집 품질, 실행 관계, 시간 변화**를 함께 고려해야 합니다.
</callout>

## 먼저 확인할 분석 범위

<table fit-page-width="true" header-row="true">
	<tr>
		<td>구분</td>
		<td>① 초기 기간</td>
		<td>② 중간 기간</td>
		<td>③ 최근 기간</td>
	</tr>
	<tr>
		<td>폴더명 기준 기간</td>
		<td>2025-12-24 ~ 2026-04-06</td>
		<td>2026-08-15 ~ 2026-09-14</td>
		<td>2026-09-30 ~ 2026-10-02</td>
	</tr>
	<tr>
		<td>Transaction 행 수</td>
		<td>1,768,054</td>
		<td>213,381</td>
		<td>27,268</td>
	</tr>
	<tr>
		<td>Process 행 수</td>
		<td>3,473,285</td>
		<td>213,509</td>
		<td>27,405</td>
	</tr>
	<tr>
		<td>Transaction 시작 로그가 있는 날짜</td>
		<td>59일</td>
		<td>19일</td>
		<td>3일</td>
	</tr>
	<tr>
		<td>Process 시작 로그가 있는 날짜</td>
		<td>60일</td>
		<td>19일</td>
		<td>3일</td>
	</tr>
</table>

**총량:** Transaction 2,008,703행 + Process 3,714,199행 = 원본 입력 **5,722,902행**입니다. Process 행은 거래 안의 단계 실행 기록이므로 572만 개의 독립 거래를 뜻하지 않습니다. Merge는 같은 원본을 결합한 분석이므로 별도의 새 데이터량으로 더하지 않습니다. 이 글은 Transaction·Process의 정형 필드 분석이며, 메시지 본문/payload를 텍스트 모델로 분석한 결과는 아닙니다.

폴더명의 시작·종료 날짜는 데이터가 들어 있는 범위를 뜻하며, 그 사이 모든 날짜·시간에 수집이 연속적으로 이루어졌다는 보장은 아닙니다. 위 ‘관측 날짜’는 유효 시작시각이 실제로 등장한 날짜 수입니다.

<callout icon="📌" color="gray_bg">
	**근거를 읽는 기준**<br>① 표의 건수·비중·분위수는 특별한 언급이 없으면 전체 원본/유효 데이터 기준입니다.<br>② 분포 그림은 기간당 10,000행의 균형 표본을 사용합니다. 결측을 제외하면 실제 그려진 수는 줄어들 수 있습니다.<br>③ 시퀀스·상관 그림은 표본 결과이므로 전체 운영 환경의 확정 사실로 확대하지 않습니다.<br>④ ‘후보’는 추가 검토 대상입니다. 실제 장애·모델 정확도와 같지 않습니다.
</callout>

<table_of_contents/>

---

## 1. 세 기간은 같은 모집단의 연속 관측이 아니라, 업무 구성이 크게 다른 집단이다

<callout icon="💡" color="blue_bg">
	**확인된 특징:** 초기 기간은 DB 읽기·쓰기 흐름이 중심이고, 중간·최근 기간은 서비스 실행이 중심입니다. 처리시간·단계 수·처리량도 함께 바뀝니다.
</callout>

### 수치 근거

<table fit-page-width="true" header-row="true">
	<tr>
		<td>관측 지표</td>
		<td>① 초기</td>
		<td>② 중간</td>
		<td>③ 최근</td>
	</tr>
	<tr>
		<td>Transaction 처리시간 중앙값</td>
		<td>5,458ms</td>
		<td>58ms</td>
		<td>5,984ms</td>
	</tr>
	<tr>
		<td>Process 처리시간 중앙값</td>
		<td>989.5ms</td>
		<td>31ms</td>
		<td>5,969ms</td>
	</tr>
	<tr>
		<td>Transaction processCount 최빈값</td>
		<td>2 · 93.09%</td>
		<td>1 · 99.96%</td>
		<td>1 · 99.59%</td>
	</tr>
	<tr>
		<td>Process totalCount 최빈값</td>
		<td>10,000 · 81.97%</td>
		<td>1 · 96.45%</td>
		<td>1 · 99.42%</td>
	</tr>
	<tr>
		<td>초기 기간에 없던 interfaceId의 행 비중</td>
		<td>기준 집단</td>
		<td>99.974%</td>
		<td>99.879%</td>
	</tr>
	<tr>
		<td>Transaction duration PSI / 초기 대비</td>
		<td>0.0000</td>
		<td>10.0313</td>
		<td>2.8529</td>
	</tr>
</table>

‘신규 업무 행 비중’은 새 interfaceId의 **종류 수 비율**이 아니라, 초기 기간에서 보지 못한 ID를 가진 **행의 비율**입니다. 이 비교의 기준은 초기 기간 전체이며, 모델 학습 구간의 미관측 여부와는 별개입니다.

**근거 그림 — 기간별 Process 어댑터 구성**

<image src="file-upload://3f125113-b9ae-816f-a1fe-00b20ffb7204"></image>

**그림 읽기:** 막대의 100%는 각 기간의 전체 Process입니다. DB.READER/DB.WRITER 중심에서 SERVICE.EXECUTOR 중심으로 역할 구성이 바뀌는 점을 봅니다. 막대 길이는 데이터 총량 비교가 아닙니다.

출처: `processes_analysis.ipynb` 셀 33, 그림 2. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

**근거 그림 — 초기 기간 대비 수치 분포 변화(PSI)**

<image src="file-upload://3f125113-b9ae-8137-993e-00b222b97bbe"></image>

**그림 읽기:** 초기 기간을 기준으로 처리시간·단계 수 분포의 차이를 나타냅니다. PSI는 노트북의 구간 설정·평활화 방식에 영향을 받는 상대 지표이지 장애 확률이 아닙니다. 값의 크기만으로 장애를 확정하지 않습니다.

출처: `transaction_analysis.ipynb` 셀 39, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

### 이 결과가 의미하는 것

중간 기간의 거래 중앙값은 초기보다 훨씬 짧고, 최근은 다시 약 6초 수준입니다. 그러나 **서로 다른 업무를 비교한 결과**이므로 ‘시스템이 빨라졌다/느려졌다’라고 바로 결론 내릴 수 없습니다. 같은 어댑터 이름이어도 실제 호출 업무·대상 시스템·처리량이 달라질 수 있습니다.

### 모델 구현에 연결

- **반영할 설계 원칙:** 업무·어댑터·경로별로 점수와 지연 기준을 나누어 보고, 신규 업무와 기존 업무의 이상을 구분합니다. source_period는 분석용 구분이지 직접적인 이상 정답이나 모델 입력이 아닙니다.
- **비교할 실험:** 통합 모델, 업무 그룹 모델, 어댑터/경로 공통 모델의 경보량을 같은 시간 분리 조건에서 비교합니다. 데이터가 적은 신규 업무는 공통 기준선으로 시작하고 충분한 이력이 쌓인 뒤 개별 기준을 검토합니다.
- **아직 확정할 수 없는 것:** 최근 지연의 원인이 성능 저하인지, 업무 난이도·요청 조건 변화인지. 동일 업무의 부하·환경·처리 단위 정보가 필요합니다.

근거: `transaction_analysis.ipynb` 셀 14, 16, 39 · <mention-page url="https://app.notion.com/p/3f125113b9ae81029728ec5255aca520"/><br>근거: `processes_analysis.ipynb` 셀 14, 16, 33 · <mention-page url="https://app.notion.com/p/3f125113b9ae81e8a28af2e49cf31817"/>

---

## 2. 데이터는 대규모지만, 기간·업무 대표성은 매우 불균형하다

<callout icon="💡" color="blue_bg">
	**확인된 특징:** 전체 통계는 초기 기간에 지배되고, 최근 기간은 단일 업무에 지배됩니다. 행 수가 많다고 다양한 운영 상황을 충분히 포함하는 것은 아닙니다.
</callout>

<table fit-page-width="true" header-row="true">
	<tr>
		<td>기간</td>
		<td>Transaction 행 / 전체 비중</td>
		<td>Process 행 / 전체 비중</td>
		<td>Transaction 최빈 업무</td>
	</tr>
	<tr>
		<td>① 초기</td>
		<td>1,768,054 / 88.020%</td>
		<td>3,473,285 / 93.514%</td>
		<td>IF_DB2DB_906 · 2.83%</td>
	</tr>
	<tr>
		<td>② 중간</td>
		<td>213,381 / 10.623%</td>
		<td>213,509 / 5.748%</td>
		<td>IF_CLAUDE_CREATE_001 · 39.54%</td>
	</tr>
	<tr>
		<td>③ 최근</td>
		<td>27,268 / 1.357%</td>
		<td>27,405 / 0.738%</td>
		<td>IF_TEST_HA_FAILOVER · 99.20%</td>
	</tr>
</table>

**근거 그림 — Transaction 전체 구성과 균형 표본 구성**

<image src="file-upload://3f125113-b9ae-8124-ba3f-00b2aefebb5a"></image>

**그림 읽기:** 전체에서 최근 기간은 1.357%지만 그림 표본에서는 33.333%입니다. 최근 분포를 잘 비교하기 위해 의도적으로 크게 대표시킨 표본입니다. 이 그림 표본으로 전체 발생률을 계산하면 안 됩니다.

출처: `transaction_analysis.ipynb` 셀 8, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

**근거 그림 — Process 전체 구성과 균형 표본 구성**

<image src="file-upload://3f125113-b9ae-813d-a71b-00b295126774"></image>

**그림 읽기:** 초기 기간은 전체의 93.514%지만 시각화에서는 기간마다 동일한 10,000행을 사용합니다. 최근 Process의 추출률은 36.490%, 초기의 추출률은 0.288%로 크게 다릅니다.

출처: `processes_analysis.ipynb` 셀 8, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

**근거 그림 — 기간별 상위 interfaceId**

<image src="file-upload://3f125113-b9ae-814c-b286-00b2d2982c3b"></image>

**그림 읽기:** 각 기간 안에서 많이 발생한 업무를 따로 비교합니다. 최근 최빈 업무 99.20%는 전체 최근 Transaction 기준입니다. TEST라는 이름만으로 통제된 장애 주입 실험이라고 확정할 수는 없습니다.

출처: `transaction_analysis.ipynb` 셀 29, 그림 3. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

### 이 결과가 의미하는 것

전체 평균·전체 후보율만 보고하면 초기 DB 업무의 결과가 대부분을 차지합니다. 반대로 최근 기간에서 좋은 결과가 나와도 거의 한 업무에서 나온 결과일 수 있습니다. **데이터 규모**와 **업무 다양성/운영 대표성**은 분리해서 평가해야 합니다.

### 모델 구현에 연결

- **반영할 설계 원칙:** 전체 행 기준 지표와 업무별·기간별 지표를 같이 저장합니다. 소수 업무의 경보량·미탐 가능성을 별도로 확인합니다.
- **비교할 실험:** 균등 무작위 학습 표본과 업무별 최소 지원량을 확보한 표본을 비교합니다. 희귀 업무를 과도하게 반복 복제하는 방식은 경보량 왜곡 여부를 함께 확인해야 합니다.
- **추가 데이터:** 운영/테스트 구분, 실제 트래픽 비중, 업무 중요도, 장애·부하 조건별 수집 계획. 특정 업무에 집중된 27,268행이 전체 서비스의 대표 시험셋이라는 주장은 현재 근거로는 불가능합니다.

근거: `transaction_analysis.ipynb` 셀 4, 8, 29 · <mention-page url="https://app.notion.com/p/3f125113b9ae81029728ec5255aca520"/><br>근거: `processes_analysis.ipynb` 셀 4, 8 · <mention-page url="https://app.notion.com/p/3f125113b9ae81e8a28af2e49cf31817"/>

---

## 3. 처리시간은 긴 꼬리를 갖고, 정상 시간 규모도 업무에 따라 다르다

<callout icon="💡" color="blue_bg">
	**확인된 특징:** 중앙값 근처의 실행과 극단적으로 긴 실행이 함께 존재합니다. 단일 평균이나 고정 시간 임계값만으로는 설명이 부족합니다.
</callout>

<table fit-page-width="true" header-row="true">
	<tr>
		<td>Transaction 유효 처리시간</td>
		<td>① 초기</td>
		<td>② 중간</td>
		<td>③ 최근</td>
	</tr>
	<tr>
		<td>중앙값 p50</td>
		<td>5,458ms</td>
		<td>58ms</td>
		<td>5,984ms</td>
	</tr>
	<tr>
		<td>p95</td>
		<td>9,684ms</td>
		<td>159ms</td>
		<td>9,926ms</td>
	</tr>
	<tr>
		<td>p99</td>
		<td>10,952ms</td>
		<td>398.20ms</td>
		<td>52,072.97ms</td>
	</tr>
	<tr>
		<td>유효 처리시간 행 수</td>
		<td>1,760,994</td>
		<td>213,381</td>
		<td>27,268</td>
	</tr>
</table>

<table fit-page-width="true" header-row="true">
	<tr>
		<td>Process 유효 처리시간</td>
		<td>① 초기</td>
		<td>② 중간</td>
		<td>③ 최근</td>
	</tr>
	<tr>
		<td>중앙값 p50</td>
		<td>989.5ms</td>
		<td>31ms</td>
		<td>5,969ms</td>
	</tr>
	<tr>
		<td>p95</td>
		<td>8,761ms</td>
		<td>129ms</td>
		<td>6,534ms</td>
	</tr>
	<tr>
		<td>p99</td>
		<td>9,682ms</td>
		<td>179ms</td>
		<td>45,547.76ms</td>
	</tr>
</table>

전체 Transaction의 유효 처리시간은 2,001,643행, 중앙값 4,814ms, p99.9 216,979.07ms, 최댓값 **33,781,607ms(약 9.38시간)**입니다. 전체 Process의 유효 처리시간은 3,146,616행, 최댓값 **1,802,839ms(약 30.05분)**입니다. 시작·종료시각을 모두 알아야 계산할 수 있으므로 미완료 로그는 이 지연 분포에서 빠집니다.

**근거 그림 — Transaction 처리시간 분포와 긴 꼬리**

<image src="file-upload://3f125113-b9ae-8101-893a-00b20ffd496f"></image>

**그림 읽기:** 가로축은 로그 간격이고 눈금은 실제 ms입니다. 히스토그램은 균형 표본, p50·p95 선과 p99 표시는 전체 유효 값 기준입니다. 최댓값은 표본에 없거나 화면 범위 밖일 수 있으므로 위 전체 통계와 함께 봅니다.

출처: `transaction_analysis.ipynb` 셀 14, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

**근거 그림 — Process 처리시간 분포와 긴 꼬리**

<image src="file-upload://3f125113-b9ae-81af-b4fe-00b2e21ca79e"></image>

**그림 읽기:** 같은 축 범위로 기간별 형태를 비교합니다. 초기 단계 시간은 여러 어댑터가 섞여 있고, 최근은 서비스 실행이 대부분입니다. 분포가 여러 봉우리를 가진다고 각 봉우리가 장애 유형이라는 뜻은 아닙니다.

출처: `processes_analysis.ipynb` 셀 14, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

### 이 결과가 의미하는 것

최근 Transaction은 절반이 약 6초 이내지만 p99는 약 52초로, 중앙값의 약 8.7배입니다. 중간 기간의 1초와 최근 기간의 1초는 각 업무 기준으로 전혀 다른 의미를 가질 수 있습니다. 긴 실행은 장애일 수도 있지만 정상 배치·대기·외부 호출일 수도 있습니다.

### 모델 구현에 연결

- **반영할 설계 원칙:** 지연에는 log1p 변환과 강건한 스케일링을 적용하고, p50/p95/p99 및 업무별 상대 지연을 함께 봅니다. 극단값을 일괄 삭제하거나 0으로 바꾸지 않습니다.
- **비교할 피처:** 절대 처리시간, 학습/과거 이력 대비 상대 지연, 장기 지연 여부, 시간대·부하를 조건으로 한 지연. 기준선은 탐지 시점의 미래 데이터를 사용하지 않아야 합니다.
- **운영 시 구분:** 완료 후 지연 탐지는 실제 종료시각으로 계산할 수 있지만, 진행 중 탐지는 ‘현재 시각 − 시작시각’과 그 시점까지의 관측 정보만 사용해야 합니다.

근거: `transaction_analysis.ipynb` 셀 11, 14, 20 · <mention-page url="https://app.notion.com/p/3f125113b9ae81029728ec5255aca520"/><br>근거: `processes_analysis.ipynb` 셀 11, 14, 24 · <mention-page url="https://app.notion.com/p/3f125113b9ae81e8a28af2e49cf31817"/>

---

## 4. 카운트는 연속 정규분포가 아니라, 반복 값·상수·중복 정보가 많은 특성이다

<callout icon="💡" color="blue_bg">
	**확인된 특징:** totalCount는 10,000 또는 1에 집중되고 retryCount는 모든 행에서 0입니다. 공통 IQR은 업무의 전형적인 값을 이상 후보로 만들 수 있습니다.
</callout>

<table fit-page-width="true" header-row="true">
	<tr>
		<td>항목</td>
		<td>관측 결과</td>
		<td>해석상 주의</td>
	</tr>
	<tr>
		<td>Process totalCount</td>
		<td>최빈값: 초기 10,000 → 중간/최근 1</td>
		<td>단계 수가 아니라 처리량 필드. 어댑터별 단위 확인 필요</td>
	</tr>
	<tr>
		<td>Transaction processCount</td>
		<td>최빈값: 초기 2 → 중간/최근 1</td>
		<td>거래에 선언된 Process 수. totalCount와 의미가 다름</td>
	</tr>
	<tr>
		<td>Transaction/Process retryCount</td>
		<td>세 기간 모두 유효 값 100%가 0</td>
		<td>현재 데이터에서 재시도 여부를 학습할 변동이 없음</td>
	</tr>
	<tr>
		<td>Process totalCount 전체 Q1 / Q3</td>
		<td>10,000 / 10,000 → IQR=0</td>
		<td>10,000과 다른 값이 공통 IQR 후보가 되는 퇴화</td>
	</tr>
	<tr>
		<td>전체 Process totalCount IQR 후보</td>
		<td>867,052 / 3,714,199행 · 23.344%</td>
		<td>확정 이상률이 아님</td>
	</tr>
	<tr>
		<td>최근 Process의 공통 다변수 IQR 후보</td>
		<td>27,405 / 27,405행 · 100%</td>
		<td>처리시간·totalCount·retryCount 중 한 규칙이라도 만족한 후보</td>
	</tr>
	<tr>
		<td>Merge attempts ↔ distinct_processes</td>
		<td>표본 Spearman ρ=1.000</td>
		<td>현재 연결 거래에서 같은 정보를 반복 표현</td>
	</tr>
</table>

**근거 그림 — Process totalCount의 반복 값 구성**

<image src="file-upload://3f125113-b9ae-810d-858d-00b2e62e7539"></image>

**그림 읽기:** 기간별 100% 막대로 값의 구성을 비교합니다. 10,000과 1이 각 집단에서 전형적인 값이라는 사실을 보여주며, 처리량 단위는 업무 메타데이터 없이 단정할 수 없습니다.

출처: `processes_analysis.ipynb` 셀 16, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

**근거 그림 — Transaction retryCount의 상수성**

<image src="file-upload://3f125113-b9ae-8100-92fe-00b2abaf3c55"></image>

**그림 읽기:** 모든 기간에서 0이 100%입니다. Process 역시 3,714,199행 모두 0이며 현재 모델이 재시도 장애를 관측해 학습했다고 볼 근거는 없습니다.

출처: `transaction_analysis.ipynb` 셀 18, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

**근거 그림 — Process 공통 IQR 기준선의 후보량**

<image src="file-upload://3f125113-b9ae-8117-8287-00b2dfec8a96"></image>

**그림 읽기:** 그림은 변수 하나씩 계산한 전체 행 대비 후보 비율입니다. totalCount의 IQR=0 때문에 많은 후보가 생깁니다. 최근 100% 후보라는 값은 별도의 기간별 다변수 OR 결과이며 이 그림의 단일 막대 값과 혼동하지 않습니다.

출처: `processes_analysis.ipynb` 셀 37, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

**근거 그림 — Merge 피처의 표본 상관 관계**

<image src="file-upload://3f125113-b9ae-8164-8f19-00b2fa39e171"></image>

**그림 읽기:** attempts와 distinct_processes의 ρ=1.000은 중복 정보 가능성을 보여줍니다. 기간을 균형 추출한 통합 표본의 상관이므로 업무별 내부 관계나 인과관계를 보장하지 않습니다.

출처: `merge_analysis.ipynb` 셀 26, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

### 이 결과가 의미하는 것

초기 대량 처리 업무가 전체의 대부분이라 Q1과 Q3가 모두 10,000에 고정됩니다. 따라서 최근 서비스 업무의 정상적인 1도 공통 기준에서 벗어납니다. **IQR 후보가 많다는 사실은 모델 문제를 발견하는 근거이지, 해당 로그가 모두 장애라는 근거가 아닙니다.**

### 모델 구현에 연결

- **반영할 설계 원칙:** 상수 피처를 점검하고 중복 피처를 정리합니다. retryCount>0처럼 학습에서 보지 못한 값은 별도 규칙/신규 값 감시로 유지할 수 있습니다. 상수 피처를 남겨도 학습된 이상 검출 능력이 생기는 것은 아닙니다.
- **비교할 실험:** totalCount의 업무별 기준, 카운트/범주형 처리, IQR=0일 때 판정 보류 또는 별도 규칙. 단위가 다른 처리량을 무조건 동일한 수치 축으로 비교하지 않습니다.
- **추가 데이터:** 실제 재시도 기록과 실패 후 회복 로그, 처리량 단위 사전. 현재 max_attempts=1이라는 관측이 시스템 자체에 재시도 기능이 없다는 뜻은 아닙니다.

근거: `transaction_analysis.ipynb` 셀 16, 18, 31 · <mention-page url="https://app.notion.com/p/3f125113b9ae81029728ec5255aca520"/><br>근거: `processes_analysis.ipynb` 셀 16, 22, 36, 40 · <mention-page url="https://app.notion.com/p/3f125113b9ae81e8a28af2e49cf31817"/><br>근거: `merge_analysis.ipynb` 셀 26 · <mention-page url="https://app.notion.com/p/3f125113b9ae81559db0c594bec9d440"/>

---

## 5. 반복 주기와 집중 구간은 있지만, 완전한 연속 시계열은 아니다

<callout icon="💡" color="blue_bg">
	**확인된 특징:** 동일 업무의 도착 간격은 초기·중간에 약 10초, 최근에 약 0.032초입니다. 동시에 빈 날짜·시간이 많아 ‘관측 없음’과 ‘트래픽 0’을 구분해야 합니다.
</callout>

<table fit-page-width="true" header-row="true">
	<tr>
		<td>Transaction 시간 지표</td>
		<td>① 초기</td>
		<td>② 중간</td>
		<td>③ 최근</td>
	</tr>
	<tr>
		<td>동일 interfaceId 연속 도착 간격 중앙값</td>
		<td>10.004초</td>
		<td>10.000초</td>
		<td>0.032초</td>
	</tr>
	<tr>
		<td>동일 업무 도착 간격 p95</td>
		<td>20.068초</td>
		<td>10.024초</td>
		<td>0.132초</td>
	</tr>
	<tr>
		<td>시작 로그가 관측된 시간 bin</td>
		<td>396</td>
		<td>258</td>
		<td>13</td>
	</tr>
	<tr>
		<td>과거 MAD 기준선 비교 가능 시간</td>
		<td>250</td>
		<td>14</td>
		<td>0</td>
	</tr>
	<tr>
		<td>트래픽 급변 후보 시간</td>
		<td>54</td>
		<td>5</td>
		<td>0</td>
	</tr>
	<tr>
		<td>관측됐지만 기준선 없는 시간</td>
		<td>146</td>
		<td>244</td>
		<td>13</td>
	</tr>
</table>

**근거 그림 — 동일 업무의 연속 도착 간격**

<image src="file-upload://3f125113-b9ae-813a-878e-00b244d5acd2"></image>

**그림 읽기:** 각 기간·interfaceId 안에서 시작시각을 정렬한 뒤 이전 실행과의 차이를 구했습니다. 서로 다른 업무 사이의 간격이 아닙니다. 각 업무의 첫 실행은 이전 값이 없어 계산에서 제외됩니다.

출처: `transaction_analysis.ipynb` 셀 37, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

**근거 그림 — Transaction 일별 로그량**

<image src="file-upload://3f125113-b9ae-816f-a33c-00b2cf1d15f7"></image>

**그림 읽기:** 로그가 발생한 날짜의 전체 건수입니다. 최근은 9월 30일 27,051행에 집중됩니다. 빈 날짜를 0으로 메우면 수집 공백을 트래픽 급감으로 오해할 수 있습니다.

출처: `transaction_analysis.ipynb` 셀 22, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

**근거 그림 — 과거 기준선 대비 시간별 트래픽 후보**

<image src="file-upload://3f125113-b9ae-8134-addf-00b2a4a75060"></image>

**그림 읽기:** 빨간 X는 과거 로그량의 강건한 기준선 대비 변화 후보입니다. 회색 점은 정상 판정이 아니라 이력 부족 또는 MAD=0으로 비교할 수 없는 구간입니다. 최근의 후보 0은 평가 가능한 시간도 0이었기 때문에 나온 결과입니다.

출처: `transaction_analysis.ipynb` 셀 27, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

### 이 결과가 의미하는 것

10초 주기는 스케줄러·반복 요청, 짧은 최근 간격은 집중 부하/반복 실험을 시사할 수 있습니다. 하지만 로그만으로 호출 목적을 확정할 수는 없습니다. 수집되지 않은 시간의 실제 트래픽과 종료되지 않은 요청의 상태도 현재 자료만으로 알 수 없습니다.

현재 트래픽 탐색은 시간별 건수에 log1p를 적용하고, 현재 bin을 제외한 과거 24시간의 이동 중앙값/MAD를 사용합니다. 최소 이력 6개와 MAD>0 조건을 충족한 구간에서 robust z의 절댓값이 3.5를 넘으면 후보로 표시합니다. 이력 조건을 만족하지 못한 구간은 **비교 불가**입니다.

### 모델 구현에 연결

- **반영할 설계 원칙:** 값과 함께 관측 여부·기준선 사용 가능 여부를 보관합니다. ‘0 후보/13 관측/0 비교 가능’처럼 탐지 커버리지도 보고합니다.
- **비교할 피처:** 동일 업무의 도착 간격, 과거 창의 건수·지연·실패율, 시간대/요일. 수집이 충분할 때만 주기/계절성 모델을 비교합니다.
- **추가 데이터:** 수집기 heartbeat, 내보내기 범위·수집 중단 이력, 스케줄 정보. ‘실제 요청이 0’이라는 확인 없이 공백을 0으로 채우거나 선형 보간하지 않습니다.

근거: `transaction_analysis.ipynb` 셀 22, 27, 37 · <mention-page url="https://app.notion.com/p/3f125113b9ae81029728ec5255aca520"/><br>근거: `processes_analysis.ipynb` 셀 4, 31 · <mention-page url="https://app.notion.com/p/3f125113b9ae81e8a28af2e49cf31817"/>

---

## 6. 완료 정보와 Transaction–Process 연결이 일부 불완전하다

<callout icon="💡" color="blue_bg">
	**확인된 특징:** 종료시각 결측, 부모 거래가 없는 Process, Process가 없는 거래, 선언 단계 수 불일치가 존재합니다. 실행 이상과 수집 품질 문제를 분리해야 합니다.
</callout>

<table fit-page-width="true" header-row="true">
	<tr>
		<td>품질/연결 지표</td>
		<td>① 초기</td>
		<td>② 중간</td>
		<td>③ 최근</td>
		<td>합계</td>
	</tr>
	<tr>
		<td>Transaction 종료시각 결측</td>
		<td>7,060</td>
		<td>0</td>
		<td>0</td>
		<td>7,060</td>
	</tr>
	<tr>
		<td>Process 시작시각 결측</td>
		<td>312</td>
		<td>13</td>
		<td>0</td>
		<td>325</td>
	</tr>
	<tr>
		<td>Process 종료시각 결측</td>
		<td>567,442 · 16.34%</td>
		<td>136 · 0.064%</td>
		<td>0</td>
		<td>567,578</td>
	</tr>
	<tr>
		<td>거래에 연결되지 않은 Process 행</td>
		<td>6,109</td>
		<td>0</td>
		<td>0</td>
		<td>6,109</td>
	</tr>
	<tr>
		<td>연결 Process가 없는 Transaction</td>
		<td>8,015</td>
		<td>27</td>
		<td>14</td>
		<td>8,056</td>
	</tr>
	<tr>
		<td>선언 processCount와 관측 단계 수 불일치</td>
		<td>15,250</td>
		<td>31</td>
		<td>18</td>
		<td>15,299</td>
	</tr>
</table>

Process 종료 결측의 전체 비율은 567,578 / 3,714,199 = 약 15.28%입니다. 초기 기간의 16.34%와 분모가 다릅니다. 단계 수 비교 가능 거래는 1,768,051 / 213,381 / 27,268행이며, 초기의 선언 값 결측 3행은 비교에서 제외됩니다. 각 규칙은 서로 겹칠 수 있으므로 위 합계들을 다시 더해 ‘전체 이상 수’라고 하지 않습니다.

**근거 그림 — Process 시간 정보의 결측**

<image src="file-upload://3f125113-b9ae-8126-89b0-00b2dc621369"></image>

**그림 읽기:** 기간별 전체 행 기준 결측 건수와 비율입니다. 종료 결측이 초기 기간에 집중돼 있고, 종료 정보가 없는 실행은 완료 지연 분포에 포함되지 않습니다. 0.00% 표시는 반올림 가능성이 있으므로 건수를 우선 확인합니다.

출처: `processes_analysis.ipynb` 셀 8, 그림 2. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

**근거 그림 — 선언 단계 수와 실제 연결 단계 수의 정합성**

<image src="file-upload://3f125113-b9ae-81d7-b5ba-00b218d31e98"></image>

**그림 읽기:** 기간별 전체 거래에서 관측한 연결 문제를 보여줍니다. count 불일치에는 Process 누락 등이 함께 포함될 수 있으며, 정상적으로 실행된 단계의 로그 수집 실패도 가능한 원인입니다.

출처: `merge_analysis.ipynb` 셀 11, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

### 이 결과가 의미하는 것

endTime이 없다는 사실만으로 실패나 장애를 확정할 수 없습니다. 실제 실행 중, 종료 로그 지연, 내보내기 누락, 수집기 문제 등을 구분할 운영 정보가 부족합니다. 반대로 결측 행을 버리고 완료 로그만 분석하면 오래 멈춘 실행을 놓칠 수 있습니다.

### 모델 구현에 연결

- **반영할 설계 원칙:** 미완료·누락 여부 플래그를 보존하고, 완료 지연 점수와 수집 품질/진행 중 타임아웃 경보를 분리합니다. 결측을 처리시간 0으로 해석하지 않습니다.
- **연결 키:** source_period + transactionId로 거래를 연결하고, Process 실행은 processId와 retryCount까지 구분합니다. processId만으로 전역 조인하면 다른 거래의 같은 템플릿 단계를 잘못 연결할 수 있습니다.
- **검증할 동작:** 지연 도착 로그의 재결합, 미완료 실행의 추적 만료 정책, 단계 누락 경보의 유예 시간. 원본을 보존하고 계산용 대치값과 결측 플래그를 별도로 관리합니다.
- **추가 데이터:** 실행 종료 상태, 수집·전송 지연, 내보내기 시각/범위, 로그 유실 확인 자료.

근거: `transaction_analysis.ipynb` 셀 6, 8 · <mention-page url="https://app.notion.com/p/3f125113b9ae81029728ec5255aca520"/><br>근거: `processes_analysis.ipynb` 셀 6, 8 · <mention-page url="https://app.notion.com/p/3f125113b9ae81e8a28af2e49cf31817"/><br>근거: `merge_analysis.ipynb` 셀 8, 10, 11 · <mention-page url="https://app.notion.com/p/3f125113b9ae81559db0c594bec9d440"/>

---

## 7. 로그는 독립 행이 아니라, 거래–단계–선행 관계를 가진 실행 구조다

<callout icon="💡" color="blue_bg">
	**확인된 특징:** Transaction 1개에 Process 여러 개가 연결되고, Process는 dependProcessId로 선행 단계를 참조합니다. 총 지연과 가장 긴 단계 시간은 강하게 연관되지만, 시간 겹침·경계 이탈의 원인은 별도 확인이 필요합니다.
</callout>

<table fit-page-width="true" header-row="true">
	<tr>
		<td>구조/시간 지표</td>
		<td>① 초기</td>
		<td>② 중간</td>
		<td>③ 최근</td>
	</tr>
	<tr>
		<td>거래 시간 − Process 관측 span 중앙값</td>
		<td>30ms</td>
		<td>28ms</td>
		<td>12ms</td>
	</tr>
	<tr>
		<td>Process 시간 합 / 거래 시간 중앙값</td>
		<td>0.991663</td>
		<td>0.529412</td>
		<td>0.997662</td>
	</tr>
	<tr>
		<td>선행 단계 시간 비교 가능 행</td>
		<td>1,450,157</td>
		<td>139</td>
		<td>147</td>
	</tr>
	<tr>
		<td>선행 종료 전 시작 후보</td>
		<td>104,961 · 7.238%</td>
		<td>127 · 91.367%</td>
		<td>147 · 100%</td>
	</tr>
	<tr>
		<td>선행 gap 중앙값</td>
		<td>+16ms</td>
		<td>−4ms</td>
		<td>−4ms</td>
	</tr>
	<tr>
		<td>거래 시간 범위 밖 Process를 가진 거래</td>
		<td>30,405 · 1.720%</td>
		<td>15,372 · 7.204%</td>
		<td>7,380 · 27.065%</td>
	</tr>
</table>

병목 시간은 거래 안에서 유효 처리시간이 가장 긴 Process의 시간입니다. Process span은 관측한 마지막 종료시각 − 첫 시작시각이고, 표의 오버헤드는 거래 처리시간 − span입니다. 따라서 오버헤드는 순수 엔진 처리 비용이 아니며, Process 시간 합/거래 시간은 CPU 병렬도가 아닙니다.

**근거 그림 — Transaction 지연과 병목 Process 시간**

<image src="file-upload://3f125113-b9ae-8185-8c79-00b236eb60f0"></image>

**그림 읽기:** x축은 거래 지연, y축은 가장 긴 단계 지연입니다. y=x 근처는 가장 긴 단계가 거래 지연과 비슷한 경우입니다. 아래쪽에는 다른 단계·대기·로그 범위 차이 등이 함께 영향을 줄 수 있습니다. 균형 표본 Spearman ρ=0.986은 강한 연관이지 인과 증명은 아닙니다.

출처: `merge_analysis.ipynb` 셀 16, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

**근거 그림 — Process 시간 합/거래 시간 비율**

<image src="file-upload://3f125113-b9ae-81c9-af1f-00b2aa37e055"></image>

**그림 읽기:** 중간 기간 중앙값은 약 0.53, 초기·최근은 약 1입니다. 같은 거래 지연이라도 단계 시간과 거래 외부 대기의 비중이 다를 수 있습니다. 1 초과를 단독 장애 규칙으로 사용하지 않습니다.

출처: `merge_analysis.ipynb` 셀 16, 그림 2. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

**근거 그림 — 선행 로그 누락과 실행 겹침**

<image src="file-upload://3f125113-b9ae-81db-8a04-00b2bd872795"></image>

**그림 읽기:** 왼쪽의 분모는 선행 참조가 있는 행, 오른쪽의 분모는 시간을 비교할 수 있는 행입니다. 최근 100%는 전체 27,405행이 아니라 비교 가능한 147행 중 147행입니다. gap=자식 시작−부모 종료가 −1ms보다 작을 때 겹침 후보입니다.

출처: `processes_analysis.ipynb` 셀 42, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

**중요한 품질 점검:** 전체 겹침 후보는 105,235행이며, 최근 gap 중앙값은 −4ms입니다. 비동기/스트리밍 실행, 시작·종료 이벤트 정의, 시계 오차로도 설명될 수 있어 ‘순서 위반 장애’로 확정하지 않습니다. Merge에는 음수 오버헤드 44건, 최대 Process span 약 80일도 관측됩니다. 이는 실제 80일 처리라고 단정할 근거가 아니며 거래 키 재사용·시각 정합성·로그 범위를 우선 점검할 대상입니다.

### 표본에서 관측한 실행 시퀀스

<table fit-page-width="true" header-row="true">
	<tr>
		<td>시퀀스 표본</td>
		<td>① 초기</td>
		<td>② 중간</td>
		<td>③ 최근</td>
	</tr>
	<tr>
		<td>추출 Transaction</td>
		<td>10,000</td>
		<td>10,000</td>
		<td>10,000</td>
	</tr>
	<tr>
		<td>시퀀스 생성 거래 / 분모</td>
		<td>9,951</td>
		<td>9,998</td>
		<td>9,997</td>
	</tr>
	<tr>
		<td>관측 시퀀스 종류</td>
		<td>18</td>
		<td>6</td>
		<td>9</td>
	</tr>
	<tr>
		<td>최빈 시퀀스 / 표본 내 비중</td>
		<td>DB.READER → DB.WRITER / 91.54%</td>
		<td>SERVICE.EXECUTOR / 99.92%</td>
		<td>SERVICE.EXECUTOR / 99.72%</td>
	</tr>
</table>

**근거 그림 — 기간별 표본의 어댑터 실행 흐름**

<image src="file-upload://3f125113-b9ae-8161-9fb2-00b2d7702dd3"></image>

**그림 읽기:** 분모는 Process가 연결되어 시퀀스를 만들 수 있는 표본 거래입니다. 시간 순으로 만든 어댑터 배열은 실제 dependProcessId 관계의 순서와 다를 수 있습니다. 전체 데이터의 시퀀스 발생률로 확대하지 않습니다.

출처: `merge_analysis.ipynb` 셀 20, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

### 모델 구현에 연결

- **반영할 설계 원칙:** Transaction 점수와 Process 점수를 연결해 ‘거래가 이상한가’와 ‘어느 단계가 기여했는가’를 같이 설명합니다. 같은 거래의 여러 경보는 사건 단위로 묶습니다.
- **현재 반영된 피처:** Merge 기준 모델에는 병목 시간·오버헤드·시간 합 비율·실행/단계 수가 들어갑니다.
- **추가로 비교할 피처:** 선행 gap, 단계 누락, 기대 시퀀스와의 차이, 업무별 병목 기여도. 시퀀스·의존 관계 피처가 현재 기준 모델에 이미 들어갔다고 해석하지 않습니다.
- **검증 조건:** 동일 거래의 단계가 학습/시험에 나뉘지 않게 하고, 의존 관계의 운영 의미·시계 동기화·비동기 허용 여부를 확인합니다. 구조적 변화가 새 업무인지 실행 누락인지도 별도 검토합니다.

근거: `processes_analysis.ipynb` 셀 9, 42, 43 · <mention-page url="https://app.notion.com/p/3f125113b9ae81e8a28af2e49cf31817"/><br>근거: `merge_analysis.ipynb` 셀 10, 15, 16, 20, 26, 28 · <mention-page url="https://app.notion.com/p/3f125113b9ae81559db0c594bec9d440"/>

---

## 8. 실패 상태는 존재하지만, 확정 이상 정답은 없고 탐지 기준마다 후보가 달라진다

<callout icon="⚠️" color="orange_bg">
	**확인된 특징:** 중간 기간은 F 상태가 약 62%로 흔합니다. 상태 F, 통계 규칙 후보, 모델 후보, 실제 운영 장애는 서로 다른 개념입니다.
</callout>

<table fit-page-width="true" header-row="true">
	<tr>
		<td>반드시 구분할 개념</td>
		<td>뜻</td>
		<td>현재 데이터로 가능한 판단</td>
	</tr>
	<tr>
		<td>status=F</td>
		<td>시스템 로그의 실패 상태</td>
		<td>실패로 기록됐음을 알 수 있음. 실제 장애 정답과 동일하지 않음</td>
	</tr>
	<tr>
		<td>IQR/MAD 후보</td>
		<td>정해진 통계 기준선에서 벗어난 관측</td>
		<td>분포의 이탈/변화 검토 대상</td>
	</tr>
	<tr>
		<td>Isolation Forest 후보</td>
		<td>검증 임계값보다 큰 이상 점수</td>
		<td>모델이 우선 검토하라고 고른 로그</td>
	</tr>
	<tr>
		<td>확정 운영 이상/장애</td>
		<td>운영 영향·원인 확인 또는 통제 실험으로 확인</td>
		<td>현재 저장 출력만으로 확인된 라벨은 없음</td>
	</tr>
</table>

중간 기간의 F 상태는 Transaction **132,036 / 213,381행(61.88%)**, Process **131,894 / 213,509행(61.77%)**입니다. 실패가 다수인 집단에서 ‘희귀함’만 학습하면 흔한 실패는 낮은 이상 점수를 받을 수도 있습니다. 반대로 성공 상태에서도 비정상 지연·구조 변화가 생길 수 있습니다.

**근거 그림 — Transaction 상태 구성**

<image src="file-upload://3f125113-b9ae-8143-814e-00b2365da2bd"></image>

**그림 읽기:** 각 기간 전체 행의 상태 비율입니다. 중간 기간 F가 가장 흔하다는 점은 ‘실패=희귀 이상’이라는 가정이 성립하지 않음을 보여줍니다. 기타 상태는 S/F로 임의 재분류하지 않습니다.

출처: `transaction_analysis.ipynb` 셀 29, 그림 1. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

### 최근 기간: 통계 기준과 모델의 후보가 크게 다르다

<table fit-page-width="true" header-row="true">
	<tr>
		<td>최근 기간의 탐지 기준</td>
		<td>후보 / 실제 비교·채점 분모</td>
		<td>후보율</td>
		<td>비교 시 주의</td>
	</tr>
	<tr>
		<td>Transaction 업무별 log-duration MAD</td>
		<td>12,380 / 27,203</td>
		<td>45.510%</td>
		<td>기간+interfaceId 기준의 사후 탐색</td>
	</tr>
	<tr>
		<td>Process 업무별 log-duration MAD</td>
		<td>12,541 / 27,276</td>
		<td>45.978%</td>
		<td>기간+processId 기준의 사후 탐색</td>
	</tr>
	<tr>
		<td>Transaction Isolation Forest</td>
		<td>0 / 27,268</td>
		<td>0.0000%</td>
		<td>시간 분리 시험 구간</td>
	</tr>
	<tr>
		<td>Process Isolation Forest</td>
		<td>2 / 27,405</td>
		<td>0.0073%</td>
		<td>단계 실행 단위 시험 구간</td>
	</tr>
	<tr>
		<td>Merge Isolation Forest</td>
		<td>54 / 27,268</td>
		<td>0.1980%</td>
		<td>결합 거래 단위 시험 구간</td>
	</tr>
</table>

**근거 그림 — Transaction 업무 맥락별 MAD 후보**

<image src="file-upload://3f125113-b9ae-8153-9645-00b223ebd065"></image>

**그림 읽기:** 기간·interfaceId 안에서 유효 log1p 처리시간의 중앙값/MAD를 계산한 탐색 결과입니다. 최소 지원량과 MAD>0을 만족한 값만 비교합니다. 최근 45.510%의 분모는 전체 27,268행이 아니라 비교 가능한 27,203행입니다.

출처: `transaction_analysis.ipynb` 셀 32, 그림 2. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

MAD 후보는 유효 값 30개 이상, MAD>0인 그룹에서 robust z의 **절댓값**이 3.5를 넘는 값입니다. 따라서 느린 실행뿐 아니라 기준보다 **빠른 실행도 포함**됩니다. 위 약 45%를 ‘45%가 지연 장애’라고 해석하면 안 됩니다.

MAD 후보가 많고 모델 후보가 적다는 사실만으로 어느 쪽이 맞다고 정할 수 없습니다. 업무별 분산이 매우 작으면 작은 차이도 MAD 기준을 크게 벗어날 수 있고, 여러 실행 모드·새 업무·기준선 차이도 영향을 줄 수 있습니다. 현재 MAD는 해당 기간 전체로 계산한 **사후 분석**이고, IF는 시간 분리 학습·검증 뒤 시험 데이터를 채점한 결과라 비교 목적과 기준선도 다릅니다.

### 세 기준 모델의 시험 결과

<table fit-page-width="true" header-row="true">
	<tr>
		<td>모델</td>
		<td>② 중간 중 시험 구간</td>
		<td>③ 최근 시험 구간</td>
		<td>시험 전체 후보</td>
	</tr>
	<tr>
		<td>Transaction IF · 15피처</td>
		<td>1,899 / 84,035 · 2.2598%</td>
		<td>0 / 27,268 · 0.0000%</td>
		<td>1,899 / 111,303 · 약 1.71%</td>
	</tr>
	<tr>
		<td>Process IF · 17피처</td>
		<td>1,796 / 84,144 · 2.1344%</td>
		<td>2 / 27,405 · 0.0073%</td>
		<td>1,798 / 111,549 · 약 1.61%</td>
	</tr>
	<tr>
		<td>Merge IF · 27피처</td>
		<td>8,324 / 84,035 · 9.9054%</td>
		<td>54 / 27,268 · 0.1980%</td>
		<td>8,378 / 111,303 · 약 7.53%</td>
	</tr>
</table>

**근거 그림 — Merge 기준 모델의 기간별 시험 후보율**

<image src="file-upload://3f125113-b9ae-81a0-9231-00b20b1c1c4d"></image>

**그림 읽기:** 분모는 각 기간의 시험 구간 전체입니다. 중간 84,035행은 전체 중간 213,381행과 다릅니다. 후보율은 검토할 양이며 정확도가 아닙니다. 검증 점수의 p99 임계값을 써도 분포 변화 때문에 시험 후보율이 반드시 1%가 되지는 않습니다.

출처: `merge_analysis.ipynb` 셀 31, 그림 2. 원본 저장 출력이며 새로 생성한 예시 그림이 아닙니다.

**현재 실험 조건:** 세 모델 모두 전체 학습 대상에서 최대 100,000행을 추출해 학습합니다. 관측 날짜 기준 약 60/20/20의 시간 분리를 사용하며 동일 거래가 다른 분할에 걸치지 않도록 합니다. Transaction/Merge 학습은 초기 기간 일부, 검증은 초기 말기와 중간 초반, 시험은 중간 후반과 최근입니다. 임계값은 각 모델 검증 점수의 99% 분위수입니다. 모든 데이터 572만 행으로 모델을 직접 학습한 결과가 아닙니다.

현재 기준 모델에서 status·responseCode·successCount·errorCount는 정답처럼 입력하지 않고 사후 해석에 사용합니다. source_period·source_file·transactionId도 직접 학습 입력에서 제외합니다. 종료시각으로 만든 처리시간과 결합 단계 정보를 쓰므로 **완료 후/오프라인 탐지 기준 모델**이며, 실시간 조기 예측 성능을 증명한 결과는 아닙니다.

### 모델 구현에 연결

- **반영할 설계 원칙:** 후보율을 정확도라고 보고하지 않습니다. ‘검토 후보’, ‘확인된 이상’, ‘확인된 정상’을 별도로 관리합니다. 상태 F를 자동 정답으로 사용하는 평가도 피합니다.
- **평가 계획:** 모델 후보뿐 아니라 비후보에서도 업무·기간별 표본을 뽑아 실제 이상 여부를 검토합니다. 후보만 검토하면 놓친 이상을 측정할 수 없습니다. 같은 거래·시간대 경보는 사건 단위로 중복 제거합니다.
- **비교할 실험:** 업무 기준선과 통합 모델의 후보 교집합/차이, 신규 업무별 경보량, 지연·누락·관계 이상별 검출. 현재 결과만으로 Merge가 더 정확하거나 Transaction이 더 안정적이라고 결론 내리지 않습니다.
- **추가 데이터:** 장애 티켓/운영 확인, 정상·장애 시나리오의 통제된 부하/오류 주입 로그, 실제 서비스 영향. 확인 라벨이 생긴 뒤 precision·recall·사건별 탐지 지연을 평가합니다.

근거: `transaction_analysis.ipynb` 셀 29, 31, 32, 43, 46, 48 · <mention-page url="https://app.notion.com/p/3f125113b9ae81029728ec5255aca520"/><br>근거: `processes_analysis.ipynb` 셀 33, 36, 49, 52, 54 · <mention-page url="https://app.notion.com/p/3f125113b9ae81e8a28af2e49cf31817"/><br>근거: `merge_analysis.ipynb` 셀 28, 31, 33 · <mention-page url="https://app.notion.com/p/3f125113b9ae81559db0c594bec9d440"/>

---

## 종합: 관측 사실에서 구현 원칙으로 연결

<table fit-page-width="true" header-row="true">
	<tr>
		<td>핵심 특징</td>
		<td>확정해 반영할 원칙</td>
		<td>별도 검증할 실험/원인</td>
	</tr>
	<tr>
		<td>① 기간별 업무 구성 변화</td>
		<td>업무·기간별 결과 및 신규 업무를 분리 보고</td>
		<td>통합 vs 업무 그룹 vs 공통 어댑터 모델</td>
	</tr>
	<tr>
		<td>② 기간/업무 편중</td>
		<td>전체 발생률과 균형 표본을 구분; 소수 업무 지표 유지</td>
		<td>학습 표본 전략과 운영 대표성</td>
	</tr>
	<tr>
		<td>③ 지연의 긴 꼬리</td>
		<td>log1p/강건 통계; 미완료와 완료 지연 분리</td>
		<td>업무·부하 조건별 상대 지연 기준</td>
	</tr>
	<tr>
		<td>④ 반복·상수·중복 피처</td>
		<td>상수/중복 점검; IQR=0과 단위 차이 처리</td>
		<td>재시도·새 값 감시 및 피처 제거 실험</td>
	</tr>
	<tr>
		<td>⑤ 불완전 시계열</td>
		<td>미관측과 0을 구분; 탐지 커버리지 표시</td>
		<td>수집 정상 구간에서만 주기/계절성 평가</td>
	</tr>
	<tr>
		<td>⑥ 완료/연결 누락</td>
		<td>결측 플래그·품질 경보를 유지; 안전한 복합 키 사용</td>
		<td>실행 실패인지 수집 지연/유실인지</td>
	</tr>
	<tr>
		<td>⑦ 계층적 실행 구조</td>
		<td>거래–단계 점수 연결과 사건 중복 제거</td>
		<td>의존/시퀀스 피처, 비동기·시계 의미</td>
	</tr>
	<tr>
		<td>⑧ 확정 이상 라벨 부재</td>
		<td>상태/후보/확정 이상 구분; 시간 분리 평가</td>
		<td>후보·비후보 검토 및 통제 장애 실험</td>
	</tr>
</table>

### 구현 우선순위

1. **데이터 의미·품질 확정:** 실행 키, 처리량 단위, 완료 상태, 시각 정의, 수집 범위를 점검합니다. 결측/누락을 장애로 자동 확정하지 않습니다.
2. **시간 분리 기준 실험 유지:** 현재 IF를 비교 기준으로 두고 모든 전처리·업무 기준선·임계값은 학습 또는 그 시점까지의 과거에서만 계산합니다.
3. **업무 맥락과 실행 구조 보강:** 신규 업무를 분리하고 병목·단계 누락·의존 관계 등 피처의 기여를 하나씩 검증합니다.
4. **평가용 확인 자료 확보:** 후보와 비후보를 함께 검토하고, 실제 장애/정상 시나리오를 확보한 뒤 검출 성능을 판단합니다.
5. **실시간 모델은 별도 설계:** 완료 후 피처를 사용할 수 없는 시점의 입력과 탐지 지연을 따로 정의합니다.

<callout icon="✅" color="green_bg">
	**최종 결론**<br>현재 분석으로 확정할 수 있는 것은 데이터의 편중·분포 변화·긴 꼬리·누락·실행 관계와 기준별 후보량입니다. **장애 원인이나 최적 모델의 우열까지 확정된 것은 아닙니다.** 이 연구의 핵심은 ‘수치가 큰 로그 찾기’보다, **업무 맥락을 반영하고 수집 품질을 분리한 뒤 실행 구조까지 설명할 수 있는 이상탐지 모델**을 검증하는 데 있습니다.
</callout>

## 원본 상세 분석 및 재현 범위

Transaction 상세: <mention-page url="https://app.notion.com/p/3f125113b9ae81029728ec5255aca520"/>

Process 상세: <mention-page url="https://app.notion.com/p/3f125113b9ae81e8a28af2e49cf31817"/>

Merge 상세: <mention-page url="https://app.notion.com/p/3f125113b9ae81559db0c594bec9d440"/>

<details>
<summary>실제 분석에 로드된 파일 확인 — 파일명과 테이블 의미 구분</summary>
	<table fit-page-width="true" header-row="true">
		<tr>
			<td>기간</td>
			<td>논리 테이블</td>
			<td>로드 파일</td>
			<td>읽기 형식</td>
		</tr>
		<tr>
			<td>① 초기</td>
			<td>Transaction</td>
			<td>EC_TRANSACTION_MONITORING.csv</td>
			<td>CSV</td>
		</tr>
		<tr>
			<td>① 초기</td>
			<td>Process</td>
			<td>EC_PROCESS_MONITORING.csv</td>
			<td>CSV</td>
		</tr>
		<tr>
			<td>② 중간</td>
			<td>Transaction</td>
			<td>transactions.csv</td>
			<td>CSV</td>
		</tr>
		<tr>
			<td>② 중간</td>
			<td>Process</td>
			<td>processes.csv</td>
			<td>CSV</td>
		</tr>
		<tr>
			<td>③ 최근</td>
			<td>Transaction</td>
			<td>EC_MESSAGE_BODY.csv</td>
			<td>SQL 덤프의 Transaction 테이블</td>
		</tr>
		<tr>
			<td>③ 최근</td>
			<td>Process</td>
			<td>EC_TRANSACTION_MONITORING.csv</td>
			<td>Process 필드의 CSV</td>
		</tr>
	</table>
	파일명만으로 테이블 종류를 판단하지 않았으며 노트북의 실제 로드 감사 결과를 따릅니다. 최근 EC_PROCESS_MONITORING.csv의 메시지 본문 테이블은 이 두 논리 테이블의 총량에 별도로 더하지 않았습니다.
</details>

이 글의 그래프는 세 ipynb에 저장된 원본 PNG 출력에서 선별했습니다. 수치표는 해당 저장 출력의 전체 통계·연결 결과·시험 결과를 읽기 좋게 재구성했습니다. 원본 노트북을 수정하거나 재실행하지 않았습니다. 최근의 TEST 명칭, 규칙 후보, 모델 후보만으로 통제 실험·확정 장애라고 가정하지 않았습니다.

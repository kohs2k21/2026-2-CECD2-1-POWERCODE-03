<callout icon="💡" color="blue_bg">
	**연구 주제: 대규모 로그 데이터 기반 비지도 학습 이상탐지 AI 모델 개발**<br>거래를 구성하는 실행 단계의 처리량·지연·선행 관계를 분석합니다. 실행 로그 수와 처리 데이터 건수는 다른 단위입니다.
</callout>

원본: `code/processes_analysis.ipynb` · 분석 단위: **개별 프로세스 실행/재시도 로그 한 행** · 실행 결과 확인일: <mention-date start="2026-10-06"/>.

이 문서는 노트북을 재실행하거나 모델을 재학습한 결과가 아니라, **현재 저장된 실행 출력**을 옮겨 해석한 것입니다. 이미지는 원본 PNG, 표는 저장된 수치표입니다. 원본 표의 반올림·상위 N개 출력·생략 표시도 유지합니다.

기간 표기: ① 파란색 = 25.12–26.04, ② 초록색 = 26.08–09, ③ 주황색 = 26.09.30–10.02. 전체 통계와 기간별 균형 표본을 구분해 읽습니다.

<callout icon="⚠️" color="yellow_bg">
	**후보 ≠ 확정 장애**<br>통계 규칙·정합성 규칙·비지도 점수는 서로 다른 점검 근거입니다. 분모가 다르고 후보가 중복될 수 있습니다. 후보 0건과 비교 불가는 다르며, 실패 상태도 실제 이상 정답 라벨이 아닙니다.
</callout>

<table_of_contents/>

<details>
<summary>출처와 재현성 정보</summary>
	노트북 SHA-256: `091793e898de69c75dcc2d82e19f51fbaadc3638bfe5c311cebf51a9adc3c71f`
	공통 계산: `code/log_analysis_common.py`
	시각화: `code/log_analysis_visuals.py`
	셀 번호는 마크다운을 포함해 위에서부터 센 번호이며 실행 번호 In[n]과 다릅니다.
</details>

---

## 1. 실행 단위·전체 규모·결측

**핵심 결과**

전체 **3,714,199행**이고 첫 기간이 **93.51%**입니다. 종료시각 결측은 **567,442 / 136 / 0건**, 시작시각 결측은 전체 **325건**입니다. 실행 키는 `source_period + transactionId + processId + retryCount`입니다.

<table fit-page-width="true" header-row="true">
	<tr>
		<td>기간</td>
		<td>실행 행</td>
		<td>관측 날짜</td>
		<td>종료시각 결측</td>
	</tr>
	<tr>
		<td>①</td>
		<td>3,473,285</td>
		<td>60일</td>
		<td>567,442 (16.34%)</td>
	</tr>
	<tr>
		<td>②</td>
		<td>213,509</td>
		<td>19일</td>
		<td>136 (약 0.06%)</td>
	</tr>
	<tr>
		<td>③</td>
		<td>27,405</td>
		<td>3일</td>
		<td>0</td>
	</tr>
</table>

<details>
<summary>원본 수치표 펼치기 · 셀 4 · 2개</summary>
	**원본 표 1 · 셀 4**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>source_period</td>
			<td>source_file</td>
			<td>format</td>
			<td>raw_rows</td>
			<td>rows</td>
			<td>exact_duplicates_removed</td>
			<td>duplicate_attempt_keys</td>
			<td>missing_transaction_id</td>
			<td>invalid_timestamp_values</td>
			<td>invalid_numeric_values</td>
			<td>first_start</td>
			<td>last_start</td>
			<td>last_end</td>
			<td>observed_days</td>
			<td>file_MB</td>
		</tr>
		<tr>
			<td>0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_PROCESS_MONITORING.csv</td>
			<td>csv</td>
			<td>3473285</td>
			<td>3473285</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>2025-12-24 10:25:31.689</td>
			<td>2026-04-06 08:51:33.531</td>
			<td>2026-04-06 08:52:21.391</td>
			<td>60</td>
			<td>557.08</td>
		</tr>
		<tr>
			<td>1</td>
			<td>26년8월15일-26년9월14일</td>
			<td>26년8월15일-26년9월14일\processes.csv</td>
			<td>csv</td>
			<td>213509</td>
			<td>213509</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>2026-08-15 00:00:00.284</td>
			<td>2026-09-14 14:19:59.710</td>
			<td>2026-09-14 14:20:09.407</td>
			<td>19</td>
			<td>37.05</td>
		</tr>
		<tr>
			<td>2</td>
			<td>26년9월30일-26년10월2일</td>
			<td>26년9월30일-26년10월2일\EC_TRANSACTION_MONITORING.csv</td>
			<td>csv</td>
			<td>27405</td>
			<td>27405</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>2026-09-30 09:49:26.494</td>
			<td>2026-10-02 15:36:59.559</td>
			<td>2026-10-02 15:36:59.685</td>
			<td>3</td>
			<td>4.90</td>
		</tr>
	</table>

	**원본 표 2 · 셀 4**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>transactionId</td>
			<td>processId</td>
			<td>dependProcessId</td>
			<td>processHubId</td>
			<td>adapterType</td>
			<td>channelId</td>
			<td>status</td>
			<td>startTime</td>
			<td>endTime</td>
			<td>successCount</td>
			<td>errorCount</td>
			<td>responseCode</td>
			<td>totalCount</td>
			<td>retryCount</td>
			<td>source_period</td>
			<td>source_file</td>
		</tr>
		<tr>
			<td>0</td>
			<td>TR-IF_DB2DB_100-20251224115033339-1</td>
			<td>IF_DB2DB_100_PR002</td>
			<td>IF_DB2DB_100_PR001</td>
			<td>HUB</td>
			<td>DB.WRITER</td>
			<td>SOL</td>
			<td>F</td>
			<td>2025-12-24 11:50:33.835</td>
			<td>2025-12-24 11:50:33.984</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>9999</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_PROCESS_MONITORING.csv</td>
		</tr>
		<tr>
			<td>1</td>
			<td>TR-IF_DB2DB_100-20251224115033339-1</td>
			<td>IF_DB2DB_100_PR001</td>
			<td>NONE</td>
			<td>HUB</td>
			<td>DB.READER</td>
			<td>TSC</td>
			<td>S</td>
			<td>2025-12-24 11:50:33.413</td>
			<td>2025-12-24 11:50:33.852</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0000</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_PROCESS_MONITORING.csv</td>
		</tr>
		<tr>
			<td>2</td>
			<td>TR-IF_DB2DB_900-20251224151514032-1</td>
			<td>IF_DB2DB_900_PR001</td>
			<td>NONE</td>
			<td>HUB</td>
			<td>DB.READER</td>
			<td>TSC</td>
			<td>S</td>
			<td>2025-12-24 15:15:14.080</td>
			<td>2025-12-24 15:15:14.480</td>
			<td>10000.0</td>
			<td>0.0</td>
			<td>0000</td>
			<td>10000.0</td>
			<td>0.0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_PROCESS_MONITORING.csv</td>
		</tr>
		<tr>
			<td>3</td>
			<td>TR-IF_DB2DB_900-20251224151514032-1</td>
			<td>IF_DB2DB_900_PR002</td>
			<td>IF_DB2DB_900_PR001</td>
			<td>HUB</td>
			<td>DB.WRITER</td>
			<td>SOL</td>
			<td>S</td>
			<td>2025-12-24 15:15:14.506</td>
			<td>2025-12-24 15:15:16.500</td>
			<td>10000.0</td>
			<td>0.0</td>
			<td>0000</td>
			<td>10000.0</td>
			<td>0.0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_PROCESS_MONITORING.csv</td>
		</tr>
		<tr>
			<td>4</td>
			<td>TR-IF_DB2DB_905-20251224151546010-1</td>
			<td>IF_DB2DB_905_PR001</td>
			<td>NONE</td>
			<td>HUB</td>
			<td>DB.READER</td>
			<td>TSC</td>
			<td>S</td>
			<td>2025-12-24 15:15:46.556</td>
			<td>2025-12-24 15:15:48.789</td>
			<td>10000.0</td>
			<td>0.0</td>
			<td>0000</td>
			<td>10000.0</td>
			<td>0.0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_PROCESS_MONITORING.csv</td>
		</tr>
	</table>
</details>

<details>
<summary>원본 수치표 펼치기 · 셀 6 · 3개</summary>
	**원본 표 1 · 셀 6**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>dtype</td>
			<td>missing_count</td>
			<td>missing_pct</td>
			<td>unique_values</td>
		</tr>
		<tr>
			<td>transactionId</td>
			<td>string</td>
			<td>0</td>
			<td>0.000</td>
			<td>2005384</td>
		</tr>
		<tr>
			<td>processId</td>
			<td>category</td>
			<td>0</td>
			<td>0.000</td>
			<td>687</td>
		</tr>
		<tr>
			<td>dependProcessId</td>
			<td>category</td>
			<td>0</td>
			<td>0.000</td>
			<td>327</td>
		</tr>
		<tr>
			<td>processHubId</td>
			<td>category</td>
			<td>0</td>
			<td>0.000</td>
			<td>8</td>
		</tr>
		<tr>
			<td>adapterType</td>
			<td>category</td>
			<td>0</td>
			<td>0.000</td>
			<td>24</td>
		</tr>
		<tr>
			<td>channelId</td>
			<td>category</td>
			<td>0</td>
			<td>0.000</td>
			<td>8</td>
		</tr>
		<tr>
			<td>status</td>
			<td>category</td>
			<td>0</td>
			<td>0.000</td>
			<td>6</td>
		</tr>
		<tr>
			<td>startTime</td>
			<td>datetime64[ns]</td>
			<td>325</td>
			<td>0.009</td>
			<td>3422343</td>
		</tr>
		<tr>
			<td>endTime</td>
			<td>datetime64[ns]</td>
			<td>567578</td>
			<td>15.281</td>
			<td>3108343</td>
		</tr>
		<tr>
			<td>successCount</td>
			<td>float64</td>
			<td>5</td>
			<td>0.000</td>
			<td>49</td>
		</tr>
		<tr>
			<td>errorCount</td>
			<td>float64</td>
			<td>5</td>
			<td>0.000</td>
			<td>15</td>
		</tr>
		<tr>
			<td>responseCode</td>
			<td>category</td>
			<td>566785</td>
			<td>15.260</td>
			<td>40</td>
		</tr>
		<tr>
			<td>totalCount</td>
			<td>float64</td>
			<td>5</td>
			<td>0.000</td>
			<td>42</td>
		</tr>
		<tr>
			<td>retryCount</td>
			<td>float64</td>
			<td>0</td>
			<td>0.000</td>
			<td>1</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td>category</td>
			<td>0</td>
			<td>0.000</td>
			<td>3</td>
		</tr>
		<tr>
			<td>source_file</td>
			<td>category</td>
			<td>0</td>
			<td>0.000</td>
			<td>3</td>
		</tr>
	</table>

	**원본 표 2 · 셀 6**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>transactionId</td>
			<td>processId</td>
			<td>dependProcessId</td>
			<td>processHubId</td>
			<td>adapterType</td>
			<td>channelId</td>
			<td>status</td>
			<td>startTime</td>
			<td>endTime</td>
			<td>successCount</td>
			<td>errorCount</td>
			<td>responseCode</td>
			<td>totalCount</td>
			<td>retryCount</td>
			<td>source_period</td>
			<td>source_file</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.01</td>
			<td>16.34</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>16.31</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.01</td>
			<td>0.06</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.06</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.00</td>
			<td>0.00</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.00</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
		</tr>
	</table>

	**원본 표 3 · 셀 6**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>raw_rows</td>
			<td>rows</td>
			<td>observed_days</td>
			<td>share_pct</td>
			<td>rows_per_observed_day</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>3473285</td>
			<td>3473285</td>
			<td>60</td>
			<td>93.51</td>
			<td>57888.08</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>213509</td>
			<td>213509</td>
			<td>19</td>
			<td>5.75</td>
			<td>11237.32</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>27405</td>
			<td>27405</td>
			<td>3</td>
			<td>0.74</td>
			<td>9135.00</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-811e-9a78-00b289356f89"></image>

*출처: `processes_analysis.ipynb` · 셀 8 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-8158-a4f1-00b2024afaf8"></image>

*출처: `processes_analysis.ipynb` · 셀 8 · 그림 2. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- 전체 3,714,199건 중 ① 25.12–26.04가 93.51%입니다. 시각화는 30,000건을 기간별 균형 추출했습니다.

**원본 출력의 확인 수치**

- ① 25.12–26.04: 종료시각 결측 567,442건 (16.34%).
- ② 26.08–09: 종료시각 결측 136건 (0.06%).
- ③ 26.09.30–10.02: 종료시각 결측 0건 (0.00%).

<details>
<summary>원본 수치표 펼치기 · 셀 8 · 1개</summary>
	**원본 표 1 · 셀 8**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>전체 행 수</td>
			<td>그림 표본 행 수</td>
			<td>전체 내 비중 (%)</td>
			<td>표본 내 비중 (%)</td>
			<td>기간별 추출률 (%)</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>① 25.12–26.04</td>
			<td>3473285</td>
			<td>10000</td>
			<td>93.514</td>
			<td>33.333</td>
			<td>0.288</td>
		</tr>
		<tr>
			<td>② 26.08–09</td>
			<td>213509</td>
			<td>10000</td>
			<td>5.748</td>
			<td>33.333</td>
			<td>4.684</td>
		</tr>
		<tr>
			<td>③ 26.09.30–10.02</td>
			<td>27405</td>
			<td>10000</td>
			<td>0.738</td>
			<td>33.333</td>
			<td>36.490</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

`processId`는 여러 거래에서 반복되는 업무 템플릿 ID라 단독 실행 키가 아닙니다. 첫 기간의 종료 로그 결측 16.34%는 처리시간 분석이 완료/시각 유효 로그 위주임을 보여줍니다. 결측을 0ms로 넣으면 빠른 실행으로 왜곡됩니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>실행·재시도·업무 템플릿 키를 분리합니다. 원본 전체에서 결측률을 계산하고 `missing_duration`을 유지합니다. 완료 후 모델과 미완료 경과시간 감시를 분리하며 로그 수집 상태도 확인합니다.
</callout>

---

## 2. 단계별 처리시간과 긴 꼬리

**핵심 결과**

중앙값은 **989.5ms / 31ms / 5,969ms**이고 최근 기간 p99는 **45,547.76ms**입니다. 전체 최댓값은 **1,802,839ms, 약 30.05분**, 음수 처리시간은 0건입니다.

<table fit-page-width="true" header-row="true">
	<tr>
		<td>기간</td>
		<td>중앙값(ms)</td>
		<td>p95(ms)</td>
		<td>p99(ms)</td>
	</tr>
	<tr>
		<td>①</td>
		<td>989.5</td>
		<td>8,761</td>
		<td>9,682</td>
	</tr>
	<tr>
		<td>②</td>
		<td>31</td>
		<td>129</td>
		<td>179</td>
	</tr>
	<tr>
		<td>③</td>
		<td>5,969</td>
		<td>6,534</td>
		<td>45,547.76</td>
	</tr>
</table>

<details>
<summary>원본 수치표 펼치기 · 셀 11 · 1개</summary>
	**원본 표 1 · 셀 11**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>count</td>
			<td>mean</td>
			<td>std</td>
			<td>min</td>
			<td>1%</td>
			<td>25%</td>
			<td>50%</td>
			<td>75%</td>
			<td>95%</td>
			<td>99%</td>
			<td>99.9%</td>
			<td>max</td>
			<td>missing_pct</td>
			<td>zero_pct</td>
			<td>negative_count</td>
			<td>skewness</td>
		</tr>
		<tr>
			<td>duration_ms</td>
			<td>3146616.0</td>
			<td>2906.564</td>
			<td>6527.784</td>
			<td>0.0</td>
			<td>16.0</td>
			<td>391.0</td>
			<td>619.0</td>
			<td>5572.0</td>
			<td>8699.0</td>
			<td>9655.0</td>
			<td>1.957938e+04</td>
			<td>1802839.0</td>
			<td>15.281</td>
			<td>0.001</td>
			<td>0</td>
			<td>165.566</td>
		</tr>
		<tr>
			<td>totalCount</td>
			<td>3714194.0</td>
			<td>210852.024</td>
			<td>3699527.350</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>10000.0</td>
			<td>10000.0</td>
			<td>10000.0</td>
			<td>10000.0</td>
			<td>10485760.0</td>
			<td>1.048576e+07</td>
			<td>524288000.0</td>
			<td>0.000</td>
			<td>15.492</td>
			<td>0</td>
			<td>42.244</td>
		</tr>
		<tr>
			<td>successCount</td>
			<td>3714194.0</td>
			<td>210743.218</td>
			<td>3695323.879</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>10000.0</td>
			<td>10000.0</td>
			<td>10000.0</td>
			<td>10000.0</td>
			<td>10485760.0</td>
			<td>1.048576e+07</td>
			<td>524288000.0</td>
			<td>0.000</td>
			<td>18.852</td>
			<td>0</td>
			<td>42.244</td>
		</tr>
		<tr>
			<td>errorCount</td>
			<td>3714194.0</td>
			<td>0.993</td>
			<td>636.573</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>1.0</td>
			<td>1.000000e+00</td>
			<td>500000.0</td>
			<td>0.000</td>
			<td>96.640</td>
			<td>0</td>
			<td>782.852</td>
		</tr>
		<tr>
			<td>retryCount</td>
			<td>3714199.0</td>
			<td>0.000</td>
			<td>0.000</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.000000e+00</td>
			<td>0.0</td>
			<td>0.000</td>
			<td>100.000</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-8103-81a4-00b28c84814d"></image>

*출처: `processes_analysis.ipynb` · 셀 14 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 전체 중앙값 990, p95 8,761, p99 9,682.
- ② 26.08–09: 전체 중앙값 31, p95 129, p99 179.
- ③ 26.09.30–10.02: 전체 중앙값 5,969, p95 6,534, p99 45,548.

**원본 출력의 확인 수치**

- 전체 유효 처리시간 최대 1,802,839ms; 음수 0건, 종료시각 결측 567,578건.

<details>
<summary>원본 수치표 펼치기 · 셀 24 · 1개</summary>
	**원본 표 1 · 셀 24**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>source_period</td>
			<td>transactionId</td>
			<td>startTime</td>
			<td>status</td>
			<td>duration_ms</td>
		</tr>
		<tr>
			<td>505398</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_909-20251226050119997-1</td>
			<td>2025-12-26 05:01:20.454</td>
			<td>F</td>
			<td>1802839.0</td>
		</tr>
		<tr>
			<td>477193</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_909-20251226045939810-1</td>
			<td>2025-12-26 04:59:40.241</td>
			<td>F</td>
			<td>1802613.0</td>
		</tr>
		<tr>
			<td>501197</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_909-20251226050350887-1</td>
			<td>2025-12-26 05:03:51.205</td>
			<td>F</td>
			<td>1800027.0</td>
		</tr>
		<tr>
			<td>519747</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_909-20251226050541594-1</td>
			<td>2025-12-26 05:05:42.040</td>
			<td>F</td>
			<td>1800014.0</td>
		</tr>
		<tr>
			<td>472181</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_909-20251226050150530-1</td>
			<td>2025-12-26 05:01:50.949</td>
			<td>F</td>
			<td>1800012.0</td>
		</tr>
		<tr>
			<td>505371</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_909-20251226050041135-1</td>
			<td>2025-12-26 05:00:41.920</td>
			<td>F</td>
			<td>1800012.0</td>
		</tr>
		<tr>
			<td>501811</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_909-20251226050230685-1</td>
			<td>2025-12-26 05:02:31.052</td>
			<td>F</td>
			<td>1800011.0</td>
		</tr>
		<tr>
			<td>500342</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_909-20251226050313631-1</td>
			<td>2025-12-26 05:03:14.277</td>
			<td>F</td>
			<td>1800010.0</td>
		</tr>
		<tr>
			<td>473600</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_909-20251226050431558-1</td>
			<td>2025-12-26 05:04:32.197</td>
			<td>F</td>
			<td>1800007.0</td>
		</tr>
		<tr>
			<td>490953</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_909-20251226050739554-1</td>
			<td>2025-12-26 05:07:39.857</td>
			<td>F</td>
			<td>1800006.0</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

기간·업무가 다른데 공통 처리시간 기준 하나를 적용하기 어렵습니다. 장시간 배치와 짧은 서비스 실행의 정상 범위를 분리해야 합니다. 종료 로그가 없는 실행은 장시간 순위나 히스토그램에서 보이지 않습니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>`log1p(duration_ms)`와 `processId + adapterType` 등 업무군 기준을 검토합니다. 상대 지연을 계산할 때 기준선은 과거/학습 데이터만 사용합니다. 미완료 실행은 `현재시각−시작시각`과 과거 업무별 완료시간을 비교합니다.
</callout>

---

## 3. 처리 건수·성공 건수·오류 건수·재시도

**핵심 결과**

첫 기간 `totalCount=10,000`은 **81.97%**, 이후 `totalCount=1`은 **96.45% / 99.42%**입니다. 두 번째 기간의 최빈 성공 건수는 0(61.85%), 최빈 오류 건수는 1(58.35%)입니다. 재시도는 전체에서 0입니다.

<image src="file-upload://3f125113-b9ae-81c0-acd9-00b2806c6238"></image>

*출처: `processes_analysis.ipynb` · 셀 16 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈값 10000.0, 2,847,140건 (81.97%).
- ② 26.08–09: 최빈값 1.0, 205,920건 (96.45%).
- ③ 26.09.30–10.02: 최빈값 1.0, 27,247건 (99.42%).

<image src="file-upload://3f125113-b9ae-810f-a301-00b271ec4eb7"></image>

*출처: `processes_analysis.ipynb` · 셀 18 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈값 10000.0, 2,847,090건 (81.97%).
- ② 26.08–09: 최빈값 0.0, 132,046건 (61.85%).
- ③ 26.09.30–10.02: 최빈값 1.0, 27,246건 (99.42%).

<image src="file-upload://3f125113-b9ae-8165-b0d6-00b23b66e486"></image>

*출처: `processes_analysis.ipynb` · 셀 20 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈값 0.0, 3,473,096건 (99.99%).
- ② 26.08–09: 최빈값 1.0, 124,572건 (58.35%).
- ③ 26.09.30–10.02: 최빈값 0.0, 27,375건 (99.89%).

<image src="file-upload://3f125113-b9ae-81cf-a3b2-00b28c16dc0a"></image>

*출처: `processes_analysis.ipynb` · 셀 22 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈값 0.0, 3,473,285건 (100.00%).
- ② 26.08–09: 최빈값 0.0, 213,509건 (100.00%).
- ③ 26.09.30–10.02: 최빈값 0.0, 27,405건 (100.00%).

**이 결과로 알 수 있는 내용**

이 값들은 프로세스 수가 아니라 처리 데이터의 계수입니다. 어댑터에 따라 의미·단위를 확인해야 합니다. 실패 상태의 오류 건수 0은 실행 전 실패 등으로 설명될 수 있습니다. 상수 재시도는 현재 변별력이 없습니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>처리량은 로그 변환된 카운트 또는 업무별 이산 표현을 비교합니다. `successCount`·`errorCount`는 현재 행동 모델의 입력에서 제외하고 사후 해석·정합성 검사에 사용합니다. 처리량으로 나눈 시간은 처리량이 양수이고 단위가 동일한 업무에서만 추가 실험합니다.
</callout>

---

## 4. 실행량·지연·실패율의 시간 변화

**핵심 결과**

일별 최대 실행량은 **659,955 / 25,926 / 27,047행**입니다. 과거 24시간 기준에서 비교 가능한 **249 / 14 / 0시간** 중 후보는 **53 / 4 / 0시간**입니다.

<image src="file-upload://3f125113-b9ae-817e-984d-00b2dd8aef24"></image>

*출처: `processes_analysis.ipynb` · 셀 26 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 관측 60일, 최대 659,955 (2025-12-28).
- ② 26.08–09: 관측 19일, 최대 25,926 (2026-08-19).
- ③ 26.09.30–10.02: 관측 3일, 최대 27,047 (2026-09-30).

<details>
<summary>원본 수치표 펼치기 · 셀 26 · 1개</summary>
	**원본 표 1 · 셀 26**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>observed_days</td>
			<td>rows</td>
			<td>median_daily_rows</td>
			<td>max_daily_rows</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>60</td>
			<td>3472973</td>
			<td>29.5</td>
			<td>659955</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>19</td>
			<td>213496</td>
			<td>9995.0</td>
			<td>25926</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>3</td>
			<td>27405</td>
			<td>292.0</td>
			<td>27047</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-81f4-adf3-00b2c199fac2"></image>

*출처: `processes_analysis.ipynb` · 셀 27 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-81eb-81b9-00b260d77600"></image>

*출처: `processes_analysis.ipynb` · 셀 27 · 그림 2. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 관측 59일, 최대 227,071 (2026-02-20).
- ② 26.08–09: 관측 19일, 최대 49,822 (2026-09-04).
- ③ 26.09.30–10.02: 관측 3일, 최대 43,256 (2026-10-02).

**원본 출력의 확인 수치**

- ① 25.12–26.04: 관측 60일, 최대 100 (2026-01-12).
- ② 26.08–09: 관측 19일, 최대 100 (2026-08-22).
- ③ 26.09.30–10.02: 관측 3일, 최대 42.42 (2026-10-01).

<details>
<summary>원본 수치표 펼치기 · 셀 27 · 1개</summary>
	**원본 표 1 · 셀 27**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td></td>
			<td>rows</td>
			<td>observed_days</td>
			<td>rows_per_observed_day</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td>month</td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>2025-12</td>
			<td>2560700</td>
			<td>8</td>
			<td>320087.50</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>2026-01</td>
			<td>47839</td>
			<td>17</td>
			<td>2814.06</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>2026-02</td>
			<td>566944</td>
			<td>14</td>
			<td>40496.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>2026-03</td>
			<td>297362</td>
			<td>17</td>
			<td>17491.88</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>2026-04</td>
			<td>128</td>
			<td>4</td>
			<td>32.00</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>2026-08</td>
			<td>211146</td>
			<td>13</td>
			<td>16242.00</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>2026-09</td>
			<td>2350</td>
			<td>6</td>
			<td>391.67</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>2026-09</td>
			<td>27047</td>
			<td>1</td>
			<td>27047.00</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>2026-10</td>
			<td>358</td>
			<td>2</td>
			<td>179.00</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-8188-b535-00b2a847b3ed"></image>

*출처: `processes_analysis.ipynb` · 셀 29 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최다 시간대 일요일 17시, 관측 날짜당 14,200.0건. 빨간 테두리로 표시했습니다.
- ② 26.08–09: 최다 시간대 수요일 16시, 관측 날짜당 1,086.0건. 빨간 테두리로 표시했습니다.
- ③ 26.09.30–10.02: 최다 시간대 수요일 11시, 관측 날짜당 12,111.0건. 빨간 테두리로 표시했습니다.

<image src="file-upload://3f125113-b9ae-8153-ae1b-00b252260af6"></image>

*출처: `processes_analysis.ipynb` · 셀 31 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 기준선 계산 가능 249시간, 후보 53시간, 기준선 없는 관측 147시간.
- ② 26.08–09: 기준선 계산 가능 14시간, 후보 4시간, 기준선 없는 관측 244시간.
- ③ 26.09.30–10.02: 기준선 계산 가능 0시간, 후보 0시간, 기준선 없는 관측 13시간.

<details>
<summary>원본 수치표 펼치기 · 셀 31 · 2개</summary>
	**원본 표 1 · 셀 31**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>bins</td>
			<td>observed_bins</td>
			<td>comparable_bins</td>
			<td>candidate_bins</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>2471</td>
			<td>396</td>
			<td>249</td>
			<td>53</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>735</td>
			<td>258</td>
			<td>14</td>
			<td>4</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>55</td>
			<td>13</td>
			<td>0</td>
			<td>0</td>
		</tr>
	</table>

	**원본 표 2 · 셀 31**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>bin</td>
			<td>rows</td>
			<td>known_status</td>
			<td>failures</td>
			<td>median_ms</td>
			<td>p95_ms</td>
			<td>failure_pct</td>
			<td>volume_robust_z</td>
			<td>reference_available</td>
			<td>candidate</td>
			<td>source_period</td>
			<td>absolute_z</td>
		</tr>
		<tr>
			<td>2333</td>
			<td>2026-03-31 15:00:00</td>
			<td>15.0</td>
			<td>15.0</td>
			<td>0.0</td>
			<td>72.0</td>
			<td>7295.20</td>
			<td>0.000000</td>
			<td>-1957.610131</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>1957.610131</td>
		</tr>
		<tr>
			<td>2328</td>
			<td>2026-03-31 10:00:00</td>
			<td>15.0</td>
			<td>15.0</td>
			<td>1.0</td>
			<td>65.0</td>
			<td>128.00</td>
			<td>6.666667</td>
			<td>-1648.047163</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>1648.047163</td>
		</tr>
		<tr>
			<td>2944</td>
			<td>2026-09-03 17:00:00</td>
			<td>4.0</td>
			<td>4.0</td>
			<td>4.0</td>
			<td>50627.0</td>
			<td>52116.45</td>
			<td>100.000000</td>
			<td>-1040.576206</td>
			<td>True</td>
			<td>True</td>
			<td>26년8월15일-26년9월14일</td>
			<td>1040.576206</td>
		</tr>
		<tr>
			<td>795</td>
			<td>2026-01-26 13:00:00</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>305.0</td>
			<td>305.00</td>
			<td>100.000000</td>
			<td>-474.532629</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>474.532629</td>
		</tr>
		<tr>
			<td>2334</td>
			<td>2026-03-31 16:00:00</td>
			<td>5594.0</td>
			<td>5594.0</td>
			<td>3.0</td>
			<td>1017.0</td>
			<td>2595.35</td>
			<td>0.053629</td>
			<td>-260.222340</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>260.222340</td>
		</tr>
		<tr>
			<td>141</td>
			<td>2025-12-30 07:00:00</td>
			<td>18.0</td>
			<td>18.0</td>
			<td>3.0</td>
			<td>313.0</td>
			<td>9571.65</td>
			<td>16.666667</td>
			<td>-220.011317</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>220.011317</td>
		</tr>
		<tr>
			<td>2336</td>
			<td>2026-03-31 18:00:00</td>
			<td>6497.0</td>
			<td>6497.0</td>
			<td>0.0</td>
			<td>440.0</td>
			<td>2681.25</td>
			<td>0.000000</td>
			<td>-216.861696</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>216.861696</td>
		</tr>
		<tr>
			<td>123</td>
			<td>2025-12-29 13:00:00</td>
			<td>14266.0</td>
			<td>14266.0</td>
			<td>2.0</td>
			<td>2750.5</td>
			<td>9135.50</td>
			<td>0.014019</td>
			<td>-213.882983</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>213.882983</td>
		</tr>
		<tr>
			<td>2326</td>
			<td>2026-03-31 08:00:00</td>
			<td>4044.0</td>
			<td>4044.0</td>
			<td>1.0</td>
			<td>1493.5</td>
			<td>4950.85</td>
			<td>0.024728</td>
			<td>-150.414194</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>150.414194</td>
		</tr>
		<tr>
			<td>127</td>
			<td>2025-12-29 17:00:00</td>
			<td>15541.0</td>
			<td>15541.0</td>
			<td>1.0</td>
			<td>1615.0</td>
			<td>9170.00</td>
			<td>0.006435</td>
			<td>-149.183139</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>149.183139</td>
		</tr>
		<tr>
			<td>126</td>
			<td>2025-12-29 16:00:00</td>
			<td>16663.0</td>
			<td>16663.0</td>
			<td>1.0</td>
			<td>1965.0</td>
			<td>9066.00</td>
			<td>0.006001</td>
			<td>-146.615559</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>146.615559</td>
		</tr>
		<tr>
			<td>2943</td>
			<td>2026-09-03 16:00:00</td>
			<td>211.0</td>
			<td>211.0</td>
			<td>94.0</td>
			<td>29.0</td>
			<td>49.00</td>
			<td>44.549763</td>
			<td>-129.609766</td>
			<td>True</td>
			<td>True</td>
			<td>26년8월15일-26년9월14일</td>
			<td>129.609766</td>
		</tr>
		<tr>
			<td>2961</td>
			<td>2026-09-04 10:00:00</td>
			<td>6.0</td>
			<td>6.0</td>
			<td>2.0</td>
			<td>434.5</td>
			<td>19221.25</td>
			<td>33.333333</td>
			<td>-70.813837</td>
			<td>True</td>
			<td>True</td>
			<td>26년8월15일-26년9월14일</td>
			<td>70.813837</td>
		</tr>
		<tr>
			<td>2962</td>
			<td>2026-09-04 11:00:00</td>
			<td>19.0</td>
			<td>19.0</td>
			<td>4.0</td>
			<td>275.0</td>
			<td>38956.30</td>
			<td>21.052632</td>
			<td>-51.952894</td>
			<td>True</td>
			<td>True</td>
			<td>26년8월15일-26년9월14일</td>
			<td>51.952894</td>
		</tr>
		<tr>
			<td>2325</td>
			<td>2026-03-31 07:00:00</td>
			<td>11544.0</td>
			<td>11544.0</td>
			<td>1.0</td>
			<td>927.5</td>
			<td>3431.00</td>
			<td>0.008663</td>
			<td>-42.237891</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>42.237891</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

실행량은 거래 요청량과 같지 않습니다. 거래당 단계 수가 늘어도 실행량이 증가합니다. 최근 기간의 0후보는 기준선 계산 불가입니다. 관측되지 않은 날짜·시간을 정상적인 0트래픽으로 취급하지 않습니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>거래량과 프로세스 실행량을 서로 다른 시계열로 유지합니다. 업무별 실행 수와 단계당 지연을 함께 집계하고 요일·시간·수집 상태를 조건으로 사용합니다. 평가 가능한 시간 구간 비율도 결과에 포함합니다.
</callout>

---

## 5. 어댑터·채널·흐름별 정상 범위

**핵심 결과**

첫 기간은 DB 읽기/쓰기 중심이고 이후 `SERVICE.EXECUTOR` 비중은 **99.88% / 99.07%**입니다. 해당 어댑터 중앙값은 두 번째 기간 **31ms**, 최근 기간 **5,969ms**입니다. 상태 S/F 분포도 기간별로 크게 다릅니다.

<image src="file-upload://3f125113-b9ae-812d-828d-00b2346ab4e4"></image>

*출처: `processes_analysis.ipynb` · 셀 33 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-816f-875c-00b2e3c2cf7d"></image>

*출처: `processes_analysis.ipynb` · 셀 33 · 그림 2. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-816b-91d7-00b2c112fb4f"></image>

*출처: `processes_analysis.ipynb` · 셀 33 · 그림 3. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-8133-8a68-00b23f3139fd"></image>

*출처: `processes_analysis.ipynb` · 셀 33 · 그림 4. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈값 S, 2,904,451건 (83.62%).
- ② 26.08–09: 최빈값 F, 131,894건 (61.77%).
- ③ 26.09.30–10.02: 최빈값 S, 27,287건 (99.57%).

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈 DB.READER가 47.21%, 상위 6개 합계 98.46%.
- ② 26.08–09: 최빈 SERVICE.EXECUTOR가 99.88%, 상위 6개 합계 99.97%.
- ③ 26.09.30–10.02: 최빈 SERVICE.EXECUTOR가 99.07%, 상위 6개 합계 99.85%.

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈 SOL가 50.22%, 상위 6개 합계 100.00%.
- ② 26.08–09: 최빈 HUB가 99.90%, 상위 5개 합계 100.00%.
- ③ 26.09.30–10.02: 최빈 SOL가 99.85%, 상위 3개 합계 100.00%.

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈 0000가 83.65%, 상위 6개 합계 99.99%.
- ② 26.08–09: 최빈 4311가 58.34%, 상위 6개 합계 99.99%.
- ③ 26.09.30–10.02: 최빈 0000가 99.89%, 상위 4개 합계 100.00%.

**원본 출력의 확인 수치**

- 흐름 표는 기간·허브·어댑터·채널별 전체 통계입니다. 처리 건수가 다른 어댑터를 단순 평균 시간으로 비교하지 않습니다.

<details>
<summary>원본 수치표 펼치기 · 셀 33 · 2개</summary>
	**원본 표 1 · 셀 33**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td>status</td>
			<td>F</td>
			<td>H</td>
			<td>N</td>
			<td>P</td>
			<td>S</td>
			<td>Z</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>0.02</td>
			<td>0.00</td>
			<td>16.28</td>
			<td>0.06</td>
			<td>83.62</td>
			<td>0.01</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>61.77</td>
			<td>0.00</td>
			<td>0.06</td>
			<td>0.00</td>
			<td>38.15</td>
			<td>0.01</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>0.11</td>
			<td>0.01</td>
			<td>0.00</td>
			<td>0.00</td>
			<td>99.57</td>
			<td>0.31</td>
		</tr>
	</table>

	**원본 표 2 · 셀 33**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td></td>
			<td>rows</td>
			<td>median_ms</td>
			<td>p95_ms</td>
			<td>p99_ms</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td>flow_key</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → DB.READER → TSC</td>
			<td>1638494</td>
			<td>424.0</td>
			<td>647.00</td>
			<td>1094.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → DB.WRITER → SOL</td>
			<td>1614557</td>
			<td>5878.0</td>
			<td>9210.00</td>
			<td>10277.00</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>HUB → SERVICE.EXECUTOR → HUB</td>
			<td>213241</td>
			<td>31.0</td>
			<td>129.00</td>
			<td>170.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → GATEWAY.INVOKER → SOL</td>
			<td>57247</td>
			<td>233.5</td>
			<td>1776.90</td>
			<td>1932.68</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>HUB → SERVICE.EXECUTOR → SOL</td>
			<td>27150</td>
			<td>5969.0</td>
			<td>6364.85</td>
			<td>44926.71</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>DIRECT → FILE.READER → TSC</td>
			<td>24466</td>
			<td>454.0</td>
			<td>1076.25</td>
			<td>24157.38</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>DIRECT → FILE.WRITER → SOL</td>
			<td>24380</td>
			<td>906.0</td>
			<td>1269.00</td>
			<td>1529.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → SERVICE.EXECUTOR → SOL</td>
			<td>22810</td>
			<td>103.0</td>
			<td>7078.70</td>
			<td>10127.26</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → DB.EXECUTOR → SOL</td>
			<td>22783</td>
			<td>95.5</td>
			<td>174.50</td>
			<td>181.30</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → SERVICE.EXECUTOR → HUB</td>
			<td>19675</td>
			<td>7038.0</td>
			<td>7055.00</td>
			<td>7991.66</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → DB.WRITER → TSC</td>
			<td>13117</td>
			<td>222.5</td>
			<td>1598.90</td>
			<td>56947.27</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → QUEUE.SUBSCRIBER → HUB</td>
			<td>13048</td>
			<td>41437.5</td>
			<td>138933.95</td>
			<td>138948.39</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → QUEUE.EXECUTOR → TSC</td>
			<td>13041</td>
			<td>362.5</td>
			<td>64009.50</td>
			<td>77873.10</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → DB.EXECUTOR → HUB</td>
			<td>4467</td>
			<td>46.0</td>
			<td>64.00</td>
			<td>96.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → DB.READER → SOL</td>
			<td>1219</td>
			<td>185.0</td>
			<td>1863.20</td>
			<td>49651.24</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → QUEUE.PUBLISHER → HUB</td>
			<td>1145</td>
			<td>889.0</td>
			<td>1501.90</td>
			<td>1537.18</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → FILE.WRITER → SOL</td>
			<td>743</td>
			<td>97.0</td>
			<td>19621.80</td>
			<td>67981.44</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → FILE.READER → TSC</td>
			<td>625</td>
			<td>219.0</td>
			<td>55346.00</td>
			<td>232233.76</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>DIRECT → FILE.WRITER → TSC</td>
			<td>277</td>
			<td>42.0</td>
			<td>47057.20</td>
			<td>50383.72</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>DIRECT → FILE.READER → SOL</td>
			<td>276</td>
			<td>19.0</td>
			<td>47140.75</td>
			<td>50442.25</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → DB.DELETER → SOL</td>
			<td>177</td>
			<td>35471.0</td>
			<td>554767.80</td>
			<td>577770.36</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → DATA.TRANSFORMER → TSC</td>
			<td>173</td>
			<td>137.0</td>
			<td>689.00</td>
			<td>29152.33</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>HUB → DB.WRITER → SOL</td>
			<td>77</td>
			<td>56.0</td>
			<td>7598.80</td>
			<td>7735.04</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → DB.UPDATER → SOL</td>
			<td>73</td>
			<td>107.0</td>
			<td>623.75</td>
			<td>36919.19</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → FILE.READER → SOL</td>
			<td>65</td>
			<td>2600.0</td>
			<td>24620.00</td>
			<td>30334.80</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-811a-aded-00b2ff4787ce"></image>

*출처: `processes_analysis.ipynb` · 셀 34 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최다 adapterType DB.READER, 중앙값 424ms, p95 647ms.
- ② 26.08–09: 최다 adapterType SERVICE.EXECUTOR, 중앙값 31ms, p95 129ms.
- ③ 26.09.30–10.02: 최다 adapterType SERVICE.EXECUTOR, 중앙값 5,969ms, p95 6,365ms.

**원본 출력의 확인 수치**

- 어댑터별 유효 처리시간 30건 이상인 그룹의 중앙값·p95를 전체 데이터로 비교합니다.

<details>
<summary>원본 수치표 펼치기 · 셀 34 · 1개</summary>
	**원본 표 1 · 셀 34**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td></td>
			<td>valid_rows</td>
			<td>median_ms</td>
			<td>p95_ms</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td>adapterType</td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>DB.READER</td>
			<td>1425358</td>
			<td>424.0</td>
			<td>647.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>DB.WRITER</td>
			<td>1425137</td>
			<td>5878.0</td>
			<td>9210.00</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>SERVICE.EXECUTOR</td>
			<td>213139</td>
			<td>31.0</td>
			<td>129.00</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>SERVICE.EXECUTOR</td>
			<td>27150</td>
			<td>5969.0</td>
			<td>6364.85</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>FILE.READER</td>
			<td>24895</td>
			<td>452.0</td>
			<td>1171.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>FILE.WRITER</td>
			<td>22749</td>
			<td>904.0</td>
			<td>1282.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>DB.EXECUTOR</td>
			<td>4480</td>
			<td>46.0</td>
			<td>65.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>SERVICE.EXECUTOR</td>
			<td>2788</td>
			<td>7038.0</td>
			<td>7056.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>DB.UPDATER</td>
			<td>97</td>
			<td>111.0</td>
			<td>601.40</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>DB.WRITER</td>
			<td>77</td>
			<td>56.0</td>
			<td>7598.80</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>DATA.TRANSFORMER</td>
			<td>63</td>
			<td>108.0</td>
			<td>8592.30</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>GATEWAY.INVOKER</td>
			<td>59</td>
			<td>272.0</td>
			<td>6019.20</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>DB.WRITER</td>
			<td>57</td>
			<td>349.0</td>
			<td>9593.80</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>DATA.MAPPER</td>
			<td>50</td>
			<td>99.5</td>
			<td>43763.45</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>DB.READER</td>
			<td>43</td>
			<td>170.0</td>
			<td>9065.40</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>QUEUE.EXECUTOR</td>
			<td>42</td>
			<td>43.0</td>
			<td>64.85</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>QUEUE.SUBSCRIBER</td>
			<td>42</td>
			<td>1053.5</td>
			<td>1072.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>SERVICE.INVOKER</td>
			<td>39</td>
			<td>11360.0</td>
			<td>54302.20</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>DB.READER</td>
			<td>39</td>
			<td>75.0</td>
			<td>589.30</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>FILE.READER</td>
			<td>34</td>
			<td>568.5</td>
			<td>75025.40</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

같은 어댑터도 다른 업무를 실행할 수 있습니다. DB 읽기·쓰기와 서비스 실행을 같은 처리 단위로 비교하면 정상 기준을 섞게 됩니다. 점–p95 연결선은 긴 꼬리를 보여주는 것이지 시간 추이선이 아닙니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>Process 업무군은 `processId`, 어댑터, 채널/흐름 정보를 함께 고려합니다. 표본이 충분한 세부 그룹은 자체 기준선을, 부족한 그룹은 어댑터 등 상위 그룹 기준선을 사용합니다. 그룹 수와 관측량을 함께 감시합니다.
</callout>

---

## 6. 공통 IQR의 한계와 업무별 MAD

**핵심 결과**

`totalCount`의 Q1·Q3는 10,000으로 IQR=0이며 **867,052행(23.344%)**을 후보로 잡습니다. 최근 기간은 **27,405행 전부**가 공통 IQR 후보입니다. 업무별 MAD 후보율은 **0.338% / 8.704% / 45.978%**입니다.

<details>
<summary>원본 수치표 펼치기 · 셀 36 · 3개</summary>
	**원본 표 1 · 셀 36**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>feature</td>
			<td>q1</td>
			<td>q3</td>
			<td>IQR</td>
			<td>candidate_count</td>
			<td>candidate_pct</td>
		</tr>
		<tr>
			<td>0</td>
			<td>duration_ms</td>
			<td>391.0</td>
			<td>5572.0</td>
			<td>5181.0</td>
			<td>7284</td>
			<td>0.196</td>
		</tr>
		<tr>
			<td>1</td>
			<td>totalCount</td>
			<td>10000.0</td>
			<td>10000.0</td>
			<td>0.0</td>
			<td>867052</td>
			<td>23.344</td>
		</tr>
		<tr>
			<td>2</td>
			<td>retryCount</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
	</table>

	**원본 표 2 · 셀 36**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>rows</td>
			<td>iqr_candidates</td>
			<td>context_comparable</td>
			<td>context_candidates</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>3473285</td>
			<td>631711</td>
			<td>2903874</td>
			<td>9821</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>213509</td>
			<td>213507</td>
			<td>213072</td>
			<td>18545</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>27405</td>
			<td>27405</td>
			<td>27276</td>
			<td>12541</td>
		</tr>
	</table>

	**원본 표 3 · 셀 36**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td></td>
			<td>support</td>
			<td>median</td>
			<td>MAD</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td>processId</td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>84298</td>
			<td>3.663562</td>
			<td>0.167054</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>IF_REST_ERROR_RESPONSE_PR001</td>
			<td>82527</td>
			<td>2.890372</td>
			<td>0.117783</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_908_PR001</td>
			<td>47443</td>
			<td>5.934894</td>
			<td>0.227784</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_908_PR002</td>
			<td>47436</td>
			<td>8.500657</td>
			<td>0.449954</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_916_PR001</td>
			<td>47316</td>
			<td>5.961005</td>
			<td>0.257223</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_906_PR001</td>
			<td>47313</td>
			<td>5.976351</td>
			<td>0.242249</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_905_PR001</td>
			<td>47309</td>
			<td>6.037871</td>
			<td>0.262915</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_916_PR002</td>
			<td>47308</td>
			<td>8.507547</td>
			<td>0.446869</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_919_PR001</td>
			<td>47307</td>
			<td>6.013715</td>
			<td>0.251664</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_905_PR002</td>
			<td>47306</td>
			<td>8.514991</td>
			<td>0.447145</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_906_PR002</td>
			<td>47305</td>
			<td>8.497603</td>
			<td>0.451694</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_919_PR002</td>
			<td>47302</td>
			<td>8.501775</td>
			<td>0.457537</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_917_PR001</td>
			<td>47294</td>
			<td>5.986452</td>
			<td>0.286425</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_917_PR002</td>
			<td>47288</td>
			<td>8.503500</td>
			<td>0.451203</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_902_PR001</td>
			<td>47286</td>
			<td>5.926926</td>
			<td>0.246860</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-81e0-a7e4-00b26f4c377a"></image>

*출처: `processes_analysis.ipynb` · 셀 37 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-8185-a177-00b26e7fbd44"></image>

*출처: `processes_analysis.ipynb` · 셀 37 · 그림 2. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- IQR=0인 변수: totalCount, retryCount. 처리시간 외 카운트 변수는 정상 최빈값에서 벗어나기만 해도 후보가 될 수 있습니다.

**원본 출력의 확인 수치**

- ① 25.12–26.04 / 업무별 상대 지연: 9,821 / 2,903,874건 (0.338%).
- ② 26.08–09 / 업무별 상대 지연: 18,545 / 213,072건 (8.704%).
- ③ 26.09.30–10.02 / 업무별 상대 지연: 12,541 / 27,276건 (45.978%).

**원본 출력의 확인 수치**

- ① 25.12–26.04: 그룹 기준선으로 비교 불가 569,411건.
- ② 26.08–09: 그룹 기준선으로 비교 불가 437건.
- ③ 26.09.30–10.02: 그룹 기준선으로 비교 불가 129건.

**원본 출력의 확인 수치**

- 업무별 후보 비율의 분모는 전체 업무 행 수가 아니라 유효 처리시간·충분한 표본·MAD>0을 충족하는 비교 가능 행 수입니다.

<details>
<summary>원본 수치표 펼치기 · 셀 38 · 1개</summary>
	**원본 표 1 · 셀 38**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td></td>
			<td>rows</td>
			<td>comparable</td>
			<td>candidates</td>
			<td>candidate_pct_of_comparable</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td>processId</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>IF_TEST_HA_FAILOVER_PR001</td>
			<td>27047</td>
			<td>27047</td>
			<td>12530</td>
			<td>46.326765</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_FILE2FILE_000_PR001</td>
			<td>110</td>
			<td>52</td>
			<td>17</td>
			<td>32.692308</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_FILE2FILE_000_PR002</td>
			<td>108</td>
			<td>48</td>
			<td>13</td>
			<td>27.083333</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>84353</td>
			<td>84298</td>
			<td>17145</td>
			<td>20.338561</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>SERVICE_DELAY_TEST_PR001</td>
			<td>2255</td>
			<td>2255</td>
			<td>456</td>
			<td>20.221729</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>IF_IBMMQ_SUB2DB_001_PR002</td>
			<td>42</td>
			<td>42</td>
			<td>6</td>
			<td>14.285714</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_A2A_FILE_202_PR002</td>
			<td>32</td>
			<td>32</td>
			<td>4</td>
			<td>12.500000</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_A2A_FILE_201_PR001</td>
			<td>34</td>
			<td>34</td>
			<td>4</td>
			<td>11.764706</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_A2A_FILE_201_PR002</td>
			<td>34</td>
			<td>34</td>
			<td>4</td>
			<td>11.764706</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUG_UPDATE_TEST_001_PR001</td>
			<td>39</td>
			<td>37</td>
			<td>4</td>
			<td>10.810811</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>Handler_TEST_PR001</td>
			<td>54</td>
			<td>49</td>
			<td>5</td>
			<td>10.204082</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_106_PR002</td>
			<td>116</td>
			<td>108</td>
			<td>11</td>
			<td>10.185185</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>IF_IBMMQ_SUB2DB_001_PR003</td>
			<td>42</td>
			<td>42</td>
			<td>4</td>
			<td>9.523810</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_101_PR002</td>
			<td>257</td>
			<td>257</td>
			<td>20</td>
			<td>7.782101</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_101_PR001</td>
			<td>257</td>
			<td>257</td>
			<td>20</td>
			<td>7.782101</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_A2A_FILE_207_PR001</td>
			<td>30</td>
			<td>30</td>
			<td>2</td>
			<td>6.666667</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUG_DB_WRITER_TEST_PR001</td>
			<td>48</td>
			<td>47</td>
			<td>3</td>
			<td>6.382979</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>LOG_TEST_DEBUG_PR001</td>
			<td>4211</td>
			<td>208</td>
			<td>11</td>
			<td>5.288462</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>LOG_TEST_INFO_PR001</td>
			<td>4080</td>
			<td>114</td>
			<td>6</td>
			<td>5.263158</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_AGENT2AGENT_001_PR001</td>
			<td>820</td>
			<td>814</td>
			<td>42</td>
			<td>5.159705</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

후속 기간의 단건 처리 로그가 공통 IQR에서 대부분 후보가 되는 것은 혼합 분포의 문제입니다. 최근 `IF_TEST_HA_FAILOVER_PR001`은 비교 가능한 27,047행 중 12,530행(46.33%)이 MAD 후보여서 특정 업무에 집중됩니다. 빠른 쪽도 절댓값 규칙에 포함됩니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>공통 IQR을 정답이나 운영 임계값으로 채택하지 않습니다. 지연 경보의 상방 편차와 빠른 종료 후보를 분리하고 작은 MAD·정상 다중 모드를 검증합니다. 현재 EDA의 전체기간 기준선은 시간 분리 모델의 입력에 사용되지 않았으며, 운영용 기준선은 과거 데이터로 재계산합니다.
</callout>

---

## 7. 처리 건수와 상태의 정합성

**핵심 결과**

`totalCount ≠ successCount + errorCount`는 첫 기간 **823건**, 성공 상태인데 오류 건수가 있는 실행은 **10건**입니다. 음수 카운트는 0건입니다. 논리 Process당 최대 실행 행은 1건입니다.

<image src="file-upload://3f125113-b9ae-81bf-8295-00b20b205cfd"></image>

*출처: `processes_analysis.ipynb` · 셀 40 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04 / 전체/성공+오류 건수 불일치: 823 / 3,473,280건 (0.024%).
- ② 26.08–09 / 전체/성공+오류 건수 불일치: 0 / 213,509건 (0.000%).
- ③ 26.09.30–10.02 / 전체/성공+오류 건수 불일치: 0 / 27,405건 (0.000%).
- ① 25.12–26.04 / 성공 상태에 오류 건수 존재: 10 / 3,473,285건 (0.000%).
- ② 26.08–09 / 성공 상태에 오류 건수 존재: 0 / 213,509건 (0.000%).
- ③ 26.09.30–10.02 / 성공 상태에 오류 건수 존재: 0 / 27,405건 (0.000%).

**원본 출력의 확인 수치**

- 재시도 값 종류 1개; 논리 Process당 최대 실행 행 1건.

<details>
<summary>원본 수치표 펼치기 · 셀 40 · 2개</summary>
	**원본 표 1 · 셀 40**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>rows</td>
			<td>count_comparable</td>
			<td>count_mismatches</td>
			<td>negative_counts</td>
			<td>success_with_errors</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>3473285</td>
			<td>3473280</td>
			<td>823</td>
			<td>0</td>
			<td>10</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>213509</td>
			<td>213509</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>27405</td>
			<td>27405</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
		</tr>
	</table>

	**원본 표 2 · 셀 40**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>logical_processes</td>
			<td>execution_rows</td>
			<td>max_attempts</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>3473285</td>
			<td>3473285</td>
			<td>1</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>213509</td>
			<td>213509</td>
			<td>1</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>27405</td>
			<td>27405</td>
			<td>1</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

작은 비율도 실제 건수로 검토해야 합니다. 두 규칙은 중복될 수 있으므로 합계 833건이라고 단정하지 않습니다. 완료 여부나 어댑터 계수 정의가 다르면 정상적인 불일치일 수 있습니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>정합성 규칙은 독립된 점검 근거로 표시하고 비교 가능 여부를 저장합니다. 결측 계수는 불일치 검사에서 제외합니다. 재시도 폭증 탐지는 현재 데이터에서 검증할 수 없으므로 향후 실제 재시도 로그가 필요합니다.
</callout>

---

## 8. 선행 로그 누락·재시도 모호성·시간 겹침

**핵심 결과**

선행 관계 **1,713,540건** 중 시간 비교 가능한 관계는 **1,450,443건**입니다. 같은 거래에서 선행 로그를 찾지 못한 실행 **5,169건**, 선행 종료 1ms 이전에 시작한 실행 **105,235건**입니다. 최근 기간 147개 비교 가능 관계는 모두 겹치며 간격 중앙값은 **−4ms**입니다.

<image src="file-upload://3f125113-b9ae-81fb-94dd-00b21cdfa2e8"></image>

*출처: `processes_analysis.ipynb` · 셀 42 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04 / 선행 로그 없음: 5,169 / 1,713,241건 (0.302%).
- ② 26.08–09 / 선행 로그 없음: 0 / 152건 (0.000%).
- ③ 26.09.30–10.02 / 선행 로그 없음: 0 / 147건 (0.000%).
- ① 25.12–26.04 / 선행 종료 전 시작: 104,961 / 1,450,157건 (7.238%).
- ② 26.08–09 / 선행 종료 전 시작: 127 / 139건 (91.367%).
- ③ 26.09.30–10.02 / 선행 종료 전 시작: 147 / 147건 (100.000%).

**원본 출력의 확인 수치**

- 선행 관계 1,713,540건 중 시간 비교 가능 1,450,443건. 선행 로그 없음 5,169건; 시간 겹침 105,235건.

<details>
<summary>원본 수치표 펼치기 · 셀 42 · 2개</summary>
	**원본 표 1 · 셀 42**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>has_predecessor</td>
			<td>parent_log_found</td>
			<td>dependency_missing</td>
			<td>dependency_retry_ambiguous</td>
			<td>dependency_time_comparable</td>
			<td>dependency_overlap_candidate</td>
			<td>template_only_elsewhere</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>1713241</td>
			<td>1708072</td>
			<td>5169</td>
			<td>0</td>
			<td>1450157</td>
			<td>104961</td>
			<td>5169</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>152</td>
			<td>152</td>
			<td>0</td>
			<td>0</td>
			<td>139</td>
			<td>127</td>
			<td>0</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>147</td>
			<td>147</td>
			<td>0</td>
			<td>0</td>
			<td>147</td>
			<td>147</td>
			<td>0</td>
		</tr>
	</table>

	**원본 표 2 · 셀 42**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>count</td>
			<td>mean</td>
			<td>std</td>
			<td>min</td>
			<td>1%</td>
			<td>50%</td>
			<td>95%</td>
			<td>99%</td>
			<td>max</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>1450157.0</td>
			<td>3.661</td>
			<td>623.396</td>
			<td>-89046.0</td>
			<td>-4.00</td>
			<td>16.0</td>
			<td>47.0</td>
			<td>73.00</td>
			<td>3732.0</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>139.0</td>
			<td>-705.475</td>
			<td>3801.636</td>
			<td>-23049.0</td>
			<td>-22503.82</td>
			<td>-4.0</td>
			<td>2.0</td>
			<td>36.28</td>
			<td>66.0</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>147.0</td>
			<td>-4.810</td>
			<td>2.880</td>
			<td>-24.0</td>
			<td>-18.78</td>
			<td>-4.0</td>
			<td>-3.0</td>
			<td>-2.00</td>
			<td>-2.0</td>
		</tr>
	</table>
</details>

**원본 출력의 확인 수치**

- 어댑터별 시간 겹침 비율은 선행 로그가 존재하고 재시도가 모호하지 않으며 양쪽 시각이 있는 실행만 분모로 삼습니다.

<details>
<summary>원본 수치표 펼치기 · 셀 43 · 2개</summary>
	**원본 표 1 · 셀 43**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td></td>
			<td>rows</td>
			<td>missing_parent</td>
			<td>timing_comparable</td>
			<td>overlap_candidates</td>
			<td>overlap_pct_of_comparable</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td>adapterType</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>CLIENT_STUB</td>
			<td>25</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>&lt;NA&gt;</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>COMPONENT</td>
			<td>3</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>&lt;NA&gt;</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>DATA.DECRYPTOR</td>
			<td>8</td>
			<td>0</td>
			<td>7</td>
			<td>7</td>
			<td>100.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>DATA.ENCRYPTOR</td>
			<td>8</td>
			<td>0</td>
			<td>7</td>
			<td>6</td>
			<td>85.714</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>DATA.MAPPER</td>
			<td>52</td>
			<td>0</td>
			<td>47</td>
			<td>46</td>
			<td>97.872</td>
		</tr>
		<tr>
			<td>...</td>
			<td>...</td>
			<td>...</td>
			<td>...</td>
			<td>...</td>
			<td>...</td>
			<td>...</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>OBJECT.WRITER</td>
			<td>6</td>
			<td>0</td>
			<td>6</td>
			<td>6</td>
			<td>100.0</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>QUEUE.EXECUTOR</td>
			<td>42</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>&lt;NA&gt;</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>QUEUE.PUBLISHER</td>
			<td>6</td>
			<td>0</td>
			<td>6</td>
			<td>6</td>
			<td>100.0</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>QUEUE.SUBSCRIBER</td>
			<td>42</td>
			<td>0</td>
			<td>42</td>
			<td>42</td>
			<td>100.0</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>SERVICE.EXECUTOR</td>
			<td>27150</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>&lt;NA&gt;</td>
		</tr>
	</table>

	**원본 표 2 · 셀 43**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>source_period</td>
			<td>transactionId</td>
			<td>processId</td>
			<td>dependProcessId</td>
			<td>adapterType</td>
			<td>dependency_gap_ms</td>
		</tr>
		<tr>
			<td>0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_100-20251224115033339-1</td>
			<td>IF_DB2DB_100_PR002</td>
			<td>IF_DB2DB_100_PR001</td>
			<td>DB.WRITER</td>
			<td>-17.0</td>
		</tr>
		<tr>
			<td>52</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_100-20251224133413039-1</td>
			<td>IF_DB2DB_100_PR002</td>
			<td>IF_DB2DB_100_PR001</td>
			<td>DB.WRITER</td>
			<td>-2.0</td>
		</tr>
		<tr>
			<td>55</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_100-20251224133441426-1</td>
			<td>IF_DB2DB_100_PR002</td>
			<td>IF_DB2DB_100_PR001</td>
			<td>DB.WRITER</td>
			<td>-2.0</td>
		</tr>
		<tr>
			<td>56</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_100-20251224134040191-1</td>
			<td>IF_DB2DB_100_PR002</td>
			<td>IF_DB2DB_100_PR001</td>
			<td>DB.WRITER</td>
			<td>-2.0</td>
		</tr>
		<tr>
			<td>104</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_100-20251224132834954-1</td>
			<td>IF_DB2DB_100_PR002</td>
			<td>IF_DB2DB_100_PR001</td>
			<td>DB.WRITER</td>
			<td>-2.0</td>
		</tr>
		<tr>
			<td>107</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_100-20251224133317503-1</td>
			<td>IF_DB2DB_100_PR002</td>
			<td>IF_DB2DB_100_PR001</td>
			<td>DB.WRITER</td>
			<td>-4.0</td>
		</tr>
		<tr>
			<td>204</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_906-20251224151626283-1</td>
			<td>IF_DB2DB_906_PR002</td>
			<td>IF_DB2DB_906_PR001</td>
			<td>DB.WRITER</td>
			<td>-11.0</td>
		</tr>
		<tr>
			<td>420</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_905-20251224151747246-1</td>
			<td>IF_DB2DB_905_PR002</td>
			<td>IF_DB2DB_905_PR001</td>
			<td>DB.WRITER</td>
			<td>-171.0</td>
		</tr>
		<tr>
			<td>706</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_917-20251224152019938-1</td>
			<td>IF_DB2DB_917_PR002</td>
			<td>IF_DB2DB_917_PR001</td>
			<td>DB.WRITER</td>
			<td>-189.0</td>
		</tr>
		<tr>
			<td>741</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_900-20251224152030834-1</td>
			<td>IF_DB2DB_900_PR002</td>
			<td>IF_DB2DB_900_PR001</td>
			<td>DB.WRITER</td>
			<td>-171.0</td>
		</tr>
		<tr>
			<td>1248</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_905-20251224152205317-1</td>
			<td>IF_DB2DB_905_PR002</td>
			<td>IF_DB2DB_905_PR001</td>
			<td>DB.WRITER</td>
			<td>-60.0</td>
		</tr>
		<tr>
			<td>1327</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_917-20251224152057272-1</td>
			<td>IF_DB2DB_917_PR002</td>
			<td>IF_DB2DB_917_PR001</td>
			<td>DB.WRITER</td>
			<td>-182.0</td>
		</tr>
		<tr>
			<td>1891</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_903-20251224152524459-1</td>
			<td>IF_DB2DB_903_PR002</td>
			<td>IF_DB2DB_903_PR001</td>
			<td>DB.WRITER</td>
			<td>-177.0</td>
		</tr>
		<tr>
			<td>1948</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_900-20251224152611928-1</td>
			<td>IF_DB2DB_900_PR002</td>
			<td>IF_DB2DB_900_PR001</td>
			<td>DB.WRITER</td>
			<td>-189.0</td>
		</tr>
		<tr>
			<td>1950</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_910-20251224152611796-1</td>
			<td>IF_DB2DB_910_PR002</td>
			<td>IF_DB2DB_910_PR001</td>
			<td>DB.WRITER</td>
			<td>-62.0</td>
		</tr>
		<tr>
			<td>1978</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_911-20251224152501587-1</td>
			<td>IF_DB2DB_911_PR002</td>
			<td>IF_DB2DB_911_PR001</td>
			<td>DB.WRITER</td>
			<td>-191.0</td>
		</tr>
		<tr>
			<td>2017</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_913-20251224152553873-1</td>
			<td>IF_DB2DB_913_PR002</td>
			<td>IF_DB2DB_913_PR001</td>
			<td>DB.WRITER</td>
			<td>-187.0</td>
		</tr>
		<tr>
			<td>2117</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_906-20251224152607096-1</td>
			<td>IF_DB2DB_906_PR002</td>
			<td>IF_DB2DB_906_PR001</td>
			<td>DB.WRITER</td>
			<td>-190.0</td>
		</tr>
		<tr>
			<td>2844</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_937-20251224153447102-1</td>
			<td>IF_DB2DB_937_PR002</td>
			<td>IF_DB2DB_937_PR001</td>
			<td>DB.WRITER</td>
			<td>-189.0</td>
		</tr>
		<tr>
			<td>3428</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_912-20251224154554318-1</td>
			<td>IF_DB2DB_912_PR002</td>
			<td>IF_DB2DB_912_PR001</td>
			<td>DB.WRITER</td>
			<td>-180.0</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

선행 로그가 없다는 것과 선행 종료시각이 없다는 것은 다릅니다. 다른 거래에서 같은 템플릿 ID가 보여도 선행 실행을 찾은 것이 아닙니다. 비동기·스트리밍 실행이나 시각 기록 차이 때문에 작은 음수 간격이 정상일 수 있습니다. 여러 선행 재시도가 있으면 시간 연결이 모호합니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>같은 기간·거래의 실행 관계만 연결합니다. `missing_parent`, `parent_end_missing`, `retry_ambiguous`, `dependency_gap_ms`, `time_comparable`을 별개로 관리합니다. 동기/비동기 정의와 시계 오차 허용치를 확인한 업무에서만 순서 규칙을 강화합니다. 현재 기준 모델에는 이 의존 관계 피처가 직접 입력되지 않았습니다.
</callout>

---

## 9. 기간별 분포 변화와 상관관계

**핵심 결과**

`totalCount` PSI는 **9.4637 / 10.4360**, 처리시간 PSI는 **7.6573 / 4.2771**입니다. 첫 기간에 없던 `processId`를 가진 행은 **99.945% / 99.756%**입니다. 처리시간–처리량 상관은 균형 표본에서 **0.279**입니다.

<image src="file-upload://3f125113-b9ae-8164-99fb-00b2dd5f3bed"></image>

*출처: `processes_analysis.ipynb` · 셀 45 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ③ 26.09.30–10.02 / 전체 처리 건수: PSI 10.436.
- ② 26.08–09 / 전체 처리 건수: PSI 9.464.
- ② 26.08–09 / 처리시간 (ms): PSI 7.657.
- ② 26.08–09 / processId: 기준 기간에 없던 값을 가진 로그 99.94%.

<details>
<summary>원본 수치표 펼치기 · 셀 45 · 2개</summary>
	**원본 표 1 · 셀 45**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>feature</td>
			<td>source_period</td>
			<td>reference_rows</td>
			<td>current_nonmissing</td>
			<td>bins</td>
			<td>PSI</td>
			<td>median</td>
			<td>p99</td>
			<td>missing_pct</td>
		</tr>
		<tr>
			<td>0</td>
			<td>duration_ms</td>
			<td>25년12월24일-26년4월6일</td>
			<td>2905838</td>
			<td>2905838</td>
			<td>12</td>
			<td>0.0000</td>
			<td>989.5</td>
			<td>9682.00</td>
			<td>16.3375</td>
		</tr>
		<tr>
			<td>1</td>
			<td>duration_ms</td>
			<td>26년8월15일-26년9월14일</td>
			<td>2905838</td>
			<td>213373</td>
			<td>12</td>
			<td>7.6573</td>
			<td>31.0</td>
			<td>179.00</td>
			<td>0.0637</td>
		</tr>
		<tr>
			<td>2</td>
			<td>duration_ms</td>
			<td>26년9월30일-26년10월2일</td>
			<td>2905838</td>
			<td>27405</td>
			<td>12</td>
			<td>4.2771</td>
			<td>5969.0</td>
			<td>45547.76</td>
			<td>0.0000</td>
		</tr>
		<tr>
			<td>3</td>
			<td>totalCount</td>
			<td>25년12월24일-26년4월6일</td>
			<td>3473280</td>
			<td>3473280</td>
			<td>4</td>
			<td>0.0000</td>
			<td>10000.0</td>
			<td>10485760.00</td>
			<td>0.0001</td>
		</tr>
		<tr>
			<td>4</td>
			<td>totalCount</td>
			<td>26년8월15일-26년9월14일</td>
			<td>3473280</td>
			<td>213509</td>
			<td>4</td>
			<td>9.4637</td>
			<td>1.0</td>
			<td>1.00</td>
			<td>0.0000</td>
		</tr>
		<tr>
			<td>5</td>
			<td>totalCount</td>
			<td>26년9월30일-26년10월2일</td>
			<td>3473280</td>
			<td>27405</td>
			<td>4</td>
			<td>10.4360</td>
			<td>1.0</td>
			<td>1.00</td>
			<td>0.0000</td>
		</tr>
		<tr>
			<td>6</td>
			<td>retryCount</td>
			<td>25년12월24일-26년4월6일</td>
			<td>3473285</td>
			<td>3473285</td>
			<td>3</td>
			<td>0.0000</td>
			<td>0.0</td>
			<td>0.00</td>
			<td>0.0000</td>
		</tr>
		<tr>
			<td>7</td>
			<td>retryCount</td>
			<td>26년8월15일-26년9월14일</td>
			<td>3473285</td>
			<td>213509</td>
			<td>3</td>
			<td>0.0000</td>
			<td>0.0</td>
			<td>0.00</td>
			<td>0.0000</td>
		</tr>
		<tr>
			<td>8</td>
			<td>retryCount</td>
			<td>26년9월30일-26년10월2일</td>
			<td>3473285</td>
			<td>27405</td>
			<td>3</td>
			<td>0.0002</td>
			<td>0.0</td>
			<td>0.00</td>
			<td>0.0000</td>
		</tr>
	</table>

	**원본 표 2 · 셀 45**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>feature</td>
			<td>source_period</td>
			<td>unique_values</td>
			<td>new_values</td>
			<td>new_value_row_pct</td>
		</tr>
		<tr>
			<td>0</td>
			<td>processId</td>
			<td>25년12월24일-26년4월6일</td>
			<td>614</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>1</td>
			<td>processId</td>
			<td>26년8월15일-26년9월14일</td>
			<td>88</td>
			<td>52</td>
			<td>99.945</td>
		</tr>
		<tr>
			<td>2</td>
			<td>processId</td>
			<td>26년9월30일-26년10월2일</td>
			<td>26</td>
			<td>21</td>
			<td>99.756</td>
		</tr>
		<tr>
			<td>3</td>
			<td>adapterType</td>
			<td>25년12월24일-26년4월6일</td>
			<td>23</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>4</td>
			<td>adapterType</td>
			<td>26년8월15일-26년9월14일</td>
			<td>15</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>5</td>
			<td>adapterType</td>
			<td>26년9월30일-26년10월2일</td>
			<td>13</td>
			<td>1</td>
			<td>0.011</td>
		</tr>
		<tr>
			<td>6</td>
			<td>flow_key</td>
			<td>25년12월24일-26년4월6일</td>
			<td>63</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>7</td>
			<td>flow_key</td>
			<td>26년8월15일-26년9월14일</td>
			<td>33</td>
			<td>4</td>
			<td>0.004</td>
		</tr>
		<tr>
			<td>8</td>
			<td>flow_key</td>
			<td>26년9월30일-26년10월2일</td>
			<td>16</td>
			<td>1</td>
			<td>0.011</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-811f-9c07-00b20148d098"></image>

*출처: `processes_analysis.ipynb` · 셀 47 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- 가장 강한 관계: duration_ms–totalCount, ρ=0.279.
- 상수/유효값 부족으로 상관 계산 불가: retryCount.

**이 결과로 알 수 있는 내용**

변화는 지연 하나가 아니라 처리 방식과 업무 구성 전반에 걸쳐 나타납니다. PSI는 이상 비율이 아니고, 통합 상관은 업무 혼합의 결과일 수도 있습니다. 재시도 상관의 빈 칸은 0이 아니라 계산 불가입니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>업무군별 모델과 신규 업무 감시를 공통 모델과 비교합니다. 과거 학습 사전으로 희귀도·새 범주를 계산하고 drift를 별도 운영 지표로 둡니다. 무조건 모든 새 업무에 장애 경보를 내지 않습니다.
</callout>

---

## 10. 기준 모델·사후 검토·PCA

**핵심 결과**

17개 피처, 학습 전체 **3,175,652행**, 실제 학습 표본 **100,000행**, 검증 **426,669행**, 시험 **111,549행**입니다. 임계값은 **0.7332487**이고 시험 후보는 **1,798건(약 1.612%)**입니다. 최근 후보 2건은 같은 거래의 성공한 프로세스 2개이며 처리시간은 37ms·33ms입니다.

<table fit-page-width="true" header-row="true">
	<tr>
		<td>시험 구간</td>
		<td>시험 행</td>
		<td>후보 행</td>
		<td>후보율</td>
	</tr>
	<tr>
		<td>②의 시험 부분</td>
		<td>84,144</td>
		<td>1,796</td>
		<td>2.1344%</td>
	</tr>
	<tr>
		<td>③</td>
		<td>27,405</td>
		<td>2</td>
		<td>0.0073%</td>
	</tr>
	<tr>
		<td>전체 시험</td>
		<td>111,549</td>
		<td>1,798</td>
		<td>약 1.612%</td>
	</tr>
</table>

**원본 출력의 확인 수치**

- 학습 전체 3,175,652건, 실제 모델 학습 표본 100,000건; 검증 426,669건, 시험 111,549건.

<details>
<summary>원본 수치표 펼치기 · 셀 49 · 3개</summary>
	**원본 표 1 · 셀 49**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>값</td>
		</tr>
		<tr>
			<td>training_rows</td>
			<td>3.175652e+06</td>
		</tr>
		<tr>
			<td>training_sample_rows</td>
			<td>1.000000e+05</td>
		</tr>
		<tr>
			<td>validation_rows</td>
			<td>4.266690e+05</td>
		</tr>
		<tr>
			<td>test_rows</td>
			<td>1.115490e+05</td>
		</tr>
		<tr>
			<td>feature_count</td>
			<td>1.700000e+01</td>
		</tr>
		<tr>
			<td>validation_quantile</td>
			<td>9.900000e-01</td>
		</tr>
		<tr>
			<td>threshold</td>
			<td>7.332487e-01</td>
		</tr>
	</table>

	**원본 표 2 · 셀 49**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td></td>
			<td>rows</td>
			<td>scored_rows</td>
			<td>mean_score</td>
			<td>candidates</td>
			<td>candidate_pct</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td>split</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>excluded_boundary_transaction</td>
			<td>4</td>
			<td>0</td>
			<td>NaN</td>
			<td>0</td>
			<td>0.0000</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>excluded_missing_time_or_id</td>
			<td>312</td>
			<td>0</td>
			<td>NaN</td>
			<td>0</td>
			<td>0.0000</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>train</td>
			<td>3175652</td>
			<td>3175652</td>
			<td>0.5049</td>
			<td>64</td>
			<td>0.0020</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>validation</td>
			<td>297317</td>
			<td>297317</td>
			<td>0.5443</td>
			<td>1656</td>
			<td>0.5570</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>excluded_missing_time_or_id</td>
			<td>13</td>
			<td>0</td>
			<td>NaN</td>
			<td>0</td>
			<td>0.0000</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>test</td>
			<td>84144</td>
			<td>84144</td>
			<td>0.7126</td>
			<td>1796</td>
			<td>2.1344</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>validation</td>
			<td>129352</td>
			<td>129352</td>
			<td>0.7158</td>
			<td>4242</td>
			<td>3.2794</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>test</td>
			<td>27405</td>
			<td>27405</td>
			<td>0.6742</td>
			<td>2</td>
			<td>0.0073</td>
		</tr>
	</table>

	**원본 표 3 · 셀 49**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td></td>
			<td>rows</td>
			<td>first</td>
			<td>last</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td>split</td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>excluded_boundary_transaction</td>
			<td>4</td>
			<td>2026-01-12 10:28:56.989</td>
			<td>2026-04-02 11:14:00.511</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>excluded_missing_time_or_id</td>
			<td>312</td>
			<td>NaT</td>
			<td>NaT</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>train</td>
			<td>3175652</td>
			<td>2025-12-24 10:25:31.689</td>
			<td>2026-03-20 17:04:27.315</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>validation</td>
			<td>297317</td>
			<td>2026-03-23 08:12:59.703</td>
			<td>2026-04-06 08:51:33.531</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>excluded_missing_time_or_id</td>
			<td>13</td>
			<td>NaT</td>
			<td>NaT</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>test</td>
			<td>84144</td>
			<td>2026-08-20 00:00:00.295</td>
			<td>2026-09-14 14:19:59.710</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>validation</td>
			<td>129352</td>
			<td>2026-08-15 00:00:00.284</td>
			<td>2026-08-19 23:59:55.042</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>test</td>
			<td>27405</td>
			<td>2026-09-30 09:49:26.494</td>
			<td>2026-10-02 15:36:59.559</td>
		</tr>
	</table>
</details>

<details>
<summary>원본 수치표 펼치기 · 셀 51 · 1개</summary>
	**원본 표 1 · 셀 51**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>source_period</td>
			<td>transactionId</td>
			<td>processId</td>
			<td>startTime</td>
			<td>adapterType</td>
			<td>status</td>
			<td>responseCode</td>
			<td>duration_ms</td>
			<td>totalCount</td>
			<td>retryCount</td>
			<td>dependency_gap_ms</td>
			<td>context_z</td>
			<td>anomaly_score</td>
		</tr>
		<tr>
			<td>3686991</td>
			<td>26년9월30일-26년10월2일</td>
			<td>TR-IF_IBMMQ_SOL_TEST-20261002091105647-01</td>
			<td>IF_IBMMQ_SOL_TEST_PR001</td>
			<td>2026-10-02 09:11:05.674</td>
			<td>GATEWAY.RECEIVER</td>
			<td>S</td>
			<td>0000</td>
			<td>37.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>NaN</td>
			<td>0.739256</td>
		</tr>
		<tr>
			<td>3686992</td>
			<td>26년9월30일-26년10월2일</td>
			<td>TR-IF_IBMMQ_SOL_TEST-20261002091105647-01</td>
			<td>IF_IBMMQ_SOL_TEST_PR002</td>
			<td>2026-10-02 09:11:05.708</td>
			<td>DATA.MAPPER</td>
			<td>S</td>
			<td>0000</td>
			<td>33.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>-3.0</td>
			<td>NaN</td>
			<td>0.739256</td>
		</tr>
		<tr>
			<td>3473341</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-HUG_APPTODB_001-20260831154725618-01</td>
			<td>HUG_APPTODB_001_PR002</td>
			<td>2026-08-31 15:47:25.727</td>
			<td>DATA.MAPPER</td>
			<td>S</td>
			<td>0000</td>
			<td>138.0</td>
			<td>3.0</td>
			<td>0.0</td>
			<td>-3.0</td>
			<td>NaN</td>
			<td>0.735985</td>
		</tr>
		<tr>
			<td>3473342</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-HUG_APPTODB_001-20260831154725618-01</td>
			<td>HUG_APPTODB_001_PR003</td>
			<td>2026-08-31 15:47:25.860</td>
			<td>DATA.TRANSFORMER</td>
			<td>S</td>
			<td>0000</td>
			<td>46.0</td>
			<td>3.0</td>
			<td>0.0</td>
			<td>-5.0</td>
			<td>NaN</td>
			<td>0.735985</td>
		</tr>
		<tr>
			<td>3473345</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-HUG_APPTODB_002-20260831154929938-01</td>
			<td>HUG_APPTODB_002_PR002</td>
			<td>2026-08-31 15:49:30.034</td>
			<td>DATA.MAPPER</td>
			<td>S</td>
			<td>0000</td>
			<td>47.0</td>
			<td>3.0</td>
			<td>0.0</td>
			<td>-4.0</td>
			<td>NaN</td>
			<td>0.735985</td>
		</tr>
		<tr>
			<td>3553536</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824080000020-01</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>2026-08-24 08:00:00.042</td>
			<td>SERVICE.EXECUTOR</td>
			<td>F</td>
			<td>4311</td>
			<td>34.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>-0.436925</td>
			<td>0.734017</td>
		</tr>
		<tr>
			<td>3553537</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824080010038-01</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>2026-08-24 08:00:10.051</td>
			<td>SERVICE.EXECUTOR</td>
			<td>F</td>
			<td>4311</td>
			<td>42.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>0.394227</td>
			<td>0.734017</td>
		</tr>
		<tr>
			<td>3553538</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824080020030-01</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>2026-08-24 08:00:20.057</td>
			<td>SERVICE.EXECUTOR</td>
			<td>F</td>
			<td>4311</td>
			<td>37.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>-0.104879</td>
			<td>0.734017</td>
		</tr>
		<tr>
			<td>3553539</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824080030030-01</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>2026-08-24 08:00:30.043</td>
			<td>SERVICE.EXECUTOR</td>
			<td>F</td>
			<td>4311</td>
			<td>32.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>-0.674500</td>
			<td>0.734017</td>
		</tr>
		<tr>
			<td>3553540</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824080040042-01</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>2026-08-24 08:00:40.050</td>
			<td>SERVICE.EXECUTOR</td>
			<td>F</td>
			<td>4311</td>
			<td>34.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>-0.436925</td>
			<td>0.734017</td>
		</tr>
		<tr>
			<td>3553541</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824080050030-01</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>2026-08-24 08:00:50.055</td>
			<td>SERVICE.EXECUTOR</td>
			<td>F</td>
			<td>4311</td>
			<td>37.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>-0.104879</td>
			<td>0.734017</td>
		</tr>
		<tr>
			<td>3553542</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824080100036-01</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>2026-08-24 08:01:00.046</td>
			<td>SERVICE.EXECUTOR</td>
			<td>F</td>
			<td>4311</td>
			<td>33.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>-0.553965</td>
			<td>0.734017</td>
		</tr>
		<tr>
			<td>3553543</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824080110033-01</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>2026-08-24 08:01:10.045</td>
			<td>SERVICE.EXECUTOR</td>
			<td>F</td>
			<td>4311</td>
			<td>32.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>-0.674500</td>
			<td>0.734017</td>
		</tr>
		<tr>
			<td>3553544</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824080120036-01</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>2026-08-24 08:01:20.047</td>
			<td>SERVICE.EXECUTOR</td>
			<td>F</td>
			<td>4311</td>
			<td>37.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>-0.104879</td>
			<td>0.734017</td>
		</tr>
		<tr>
			<td>3553545</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824080130035-02</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>2026-08-24 08:01:30.044</td>
			<td>SERVICE.EXECUTOR</td>
			<td>F</td>
			<td>4311</td>
			<td>33.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>-0.553965</td>
			<td>0.734017</td>
		</tr>
		<tr>
			<td>3553546</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824080140037-01</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>2026-08-24 08:01:40.047</td>
			<td>SERVICE.EXECUTOR</td>
			<td>F</td>
			<td>4311</td>
			<td>35.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>-0.323182</td>
			<td>0.734017</td>
		</tr>
		<tr>
			<td>3553547</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824080150030-01</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>2026-08-24 08:01:50.053</td>
			<td>SERVICE.EXECUTOR</td>
			<td>F</td>
			<td>4311</td>
			<td>37.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>-0.104879</td>
			<td>0.734017</td>
		</tr>
		<tr>
			<td>3553548</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824080200029-02</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>2026-08-24 08:02:00.059</td>
			<td>SERVICE.EXECUTOR</td>
			<td>F</td>
			<td>4311</td>
			<td>38.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>0.000000</td>
			<td>0.734017</td>
		</tr>
		<tr>
			<td>3553549</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824080210024-01</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>2026-08-24 08:02:10.051</td>
			<td>SERVICE.EXECUTOR</td>
			<td>F</td>
			<td>4311</td>
			<td>35.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>-0.323182</td>
			<td>0.734017</td>
		</tr>
		<tr>
			<td>3553550</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260824080220043-02</td>
			<td>IF_CLAUDE_CREATE_001_PR001</td>
			<td>2026-08-24 08:02:20.054</td>
			<td>SERVICE.EXECUTOR</td>
			<td>F</td>
			<td>4311</td>
			<td>39.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>NaN</td>
			<td>0.102224</td>
			<td>0.734017</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-818b-9559-00b23357f713"></image>

*출처: `processes_analysis.ipynb` · 셀 52 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-81b0-82c1-00b2d17dde4f"></image>

*출처: `processes_analysis.ipynb` · 셀 52 · 그림 2. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ② 26.08–09 / 시험 구간 모델 검토 후보: 1,796 / 84,144건 (2.134%).
- ③ 26.09.30–10.02 / 시험 구간 모델 검토 후보: 2 / 27,405건 (0.007%).

**원본 출력의 확인 수치**

- 시험 전체 111,549건 중 후보 1,798건 (1.61%). 임계값 0.7332; 검증 점수의 99% 분위수입니다.

**원본 출력의 확인 수치**

- 위 표의 각 행은 후보 또는 비후보 집단 내부의 상태/정합성 비율입니다. 두 집단의 건수는 서로 다릅니다.

<details>
<summary>원본 수치표 펼치기 · 셀 54 · 2개</summary>
	**원본 표 1 · 셀 54**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td>status</td>
			<td>F</td>
			<td>H</td>
			<td>N</td>
			<td>S</td>
			<td>Z</td>
		</tr>
		<tr>
			<td>anomaly_candidate</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>False</td>
			<td>66.23</td>
			<td>0.0</td>
			<td>0.07</td>
			<td>33.61</td>
			<td>0.09</td>
		</tr>
		<tr>
			<td>True</td>
			<td>94.38</td>
			<td>0.0</td>
			<td>0.00</td>
			<td>5.62</td>
			<td>0.00</td>
		</tr>
	</table>

	**원본 표 2 · 셀 54**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td>integrity_candidate</td>
			<td>False</td>
		</tr>
		<tr>
			<td>anomaly_candidate</td>
			<td></td>
		</tr>
		<tr>
			<td>False</td>
			<td>100.0</td>
		</tr>
		<tr>
			<td>True</td>
			<td>100.0</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-814e-a3e9-00b2b383a194"></image>

*출처: `processes_analysis.ipynb` · 셀 56 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- 각 축의 표본 0.5–99.5% 범위를 확대했습니다. 화면 밖 481건 중 후보 6건은 모델 계산에서 제외하지 않았습니다. 전체 투영 표본 후보 300건.

<details>
<summary>실제 모델 입력 피처 17개 · 셀 56</summary>
	<table fit-page-width="true" header-row="true">
		<tr>
			<td>번호</td>
			<td>피처</td>
		</tr>
		<tr>
			<td>1</td>
			<td>log_duration_ms</td>
		</tr>
		<tr>
			<td>2</td>
			<td>missing_duration_ms</td>
		</tr>
		<tr>
			<td>3</td>
			<td>log_totalCount</td>
		</tr>
		<tr>
			<td>4</td>
			<td>missing_totalCount</td>
		</tr>
		<tr>
			<td>5</td>
			<td>missing_retryCount</td>
		</tr>
		<tr>
			<td>6</td>
			<td>hour_sin</td>
		</tr>
		<tr>
			<td>7</td>
			<td>hour_cos</td>
		</tr>
		<tr>
			<td>8</td>
			<td>weekday_sin</td>
		</tr>
		<tr>
			<td>9</td>
			<td>weekday_cos</td>
		</tr>
		<tr>
			<td>10</td>
			<td>rarity_processId</td>
		</tr>
		<tr>
			<td>11</td>
			<td>unseen_processId</td>
		</tr>
		<tr>
			<td>12</td>
			<td>rarity_adapterType</td>
		</tr>
		<tr>
			<td>13</td>
			<td>unseen_adapterType</td>
		</tr>
		<tr>
			<td>14</td>
			<td>rarity_channelId</td>
		</tr>
		<tr>
			<td>15</td>
			<td>unseen_channelId</td>
		</tr>
		<tr>
			<td>16</td>
			<td>rarity_flow_key</td>
		</tr>
		<tr>
			<td>17</td>
			<td>unseen_flow_key</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

후보의 실패 상태 비율 94.38%는 precision이 아닙니다. 시험 정합성 후보는 없지만 비지도 후보는 존재합니다. 최근 기간 MAD 후보율 45.98%와 모델 후보 2건은 서로 다른 기준과 분모의 결과입니다. PCA 표본 후보 300건은 전체 시험 후보 수가 아닙니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>행 수준 후보를 거래·업무·시간창 단위 사건으로 묶어 중복 경보를 줄입니다. 성공한 희귀 실행도 사람이 검토합니다. 신규 범주 플래그가 학습에서 상수이면 그 자체로 트리 분기에 쓰이지 않으므로 별도 신규 업무 감시가 필요합니다. 점수·원본 수치·비교 가능 여부를 함께 제공하고 실제 라벨로 성능을 검증합니다.
</callout>

---

## 부록. 추가 원본 텍스트 출력

<details>
<summary>Series 집계·실행 규모 등 원본 값 펼치기</summary>
	**원본 텍스트 출력 · 셀 4**
	```text
	원본 합계: 3,714,199건
	분석 합계: 3,714,199건 / 16개 컬럼
	메모리 사용량: 511.5 MiB
	```

	**원본 텍스트 출력 · 셀 11**
	```text
	음수 처리시간: 0건
	종료시각 없음: 567,578건
	```

	**원본 텍스트 출력 · 셀 40**
	```text
	source_period      retryCount
	25년12월24일-26년4월6일  0.0           3473285
	26년8월15일-26년9월14일  0.0            213509
	26년9월30일-26년10월2일  0.0             27405
	Name: execution_rows, dtype: int64
	```
</details>

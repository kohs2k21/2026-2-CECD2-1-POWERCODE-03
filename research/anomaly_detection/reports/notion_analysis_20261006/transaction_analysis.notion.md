<callout icon="💡" color="blue_bg">
	**연구 주제: 대규모 로그 데이터 기반 비지도 학습 이상탐지 AI 모델 개발**<br>거래 전체의 지연·발생 주기·업무 구성과 비지도 점수를 분석합니다. 개별 실행 단계의 원인은 Process·Merge 분석과 연결합니다.
</callout>

원본: `code/transaction_analysis.ipynb` · 분석 단위: **거래 실행 로그 한 행** · 실행 결과 확인일: <mention-date start="2026-10-06"/>.

이 문서는 노트북을 재실행하거나 모델을 재학습한 결과가 아니라, **현재 저장된 실행 출력**을 옮겨 해석한 것입니다. 이미지는 원본 PNG, 표는 저장된 수치표입니다. 원본 표의 반올림·상위 N개 출력·생략 표시도 유지합니다.

기간 표기: ① 파란색 = 25.12–26.04, ② 초록색 = 26.08–09, ③ 주황색 = 26.09.30–10.02. 전체 통계와 기간별 균형 표본을 구분해 읽습니다.

<callout icon="⚠️" color="yellow_bg">
	**후보 ≠ 확정 장애**<br>통계 규칙·정합성 규칙·비지도 점수는 서로 다른 점검 근거입니다. 분모가 다르고 후보가 중복될 수 있습니다. 후보 0건과 비교 불가는 다르며, 실패 상태도 실제 이상 정답 라벨이 아닙니다.
</callout>

<table_of_contents/>

<details>
<summary>출처와 재현성 정보</summary>
	노트북 SHA-256: `c6e940724d2a4febfd8895561e1c6cb161ce3f7f18c9cc7dc7fce19cae27233b`
	공통 계산: `code/log_analysis_common.py`
	시각화: `code/log_analysis_visuals.py`
	셀 번호는 마크다운을 포함해 위에서부터 센 번호이며 실행 번호 In[n]과 다릅니다.
</details>

---

## 1. 데이터 규모·표본·결측

**핵심 결과**

전체 **2,008,703행** 중 첫 기간이 **88.02%**입니다. 시각화는 기간별 1만 행씩 총 3만 행을 추출합니다. 시작시각 결측은 없고 종료시각 결측은 첫 기간에 **7,060건** 있습니다. 선택한 원본에서 완전 중복 제거·시간/수치 파싱 실패는 0건입니다.

<table fit-page-width="true" header-row="true">
	<tr>
		<td>기간</td>
		<td>거래 행 수</td>
		<td>로그가 있는 날짜 수</td>
		<td>전체 비중</td>
	</tr>
	<tr>
		<td>①</td>
		<td>1,768,054</td>
		<td>59일</td>
		<td>88.02%</td>
	</tr>
	<tr>
		<td>②</td>
		<td>213,381</td>
		<td>19일</td>
		<td>10.62%</td>
	</tr>
	<tr>
		<td>③</td>
		<td>27,268</td>
		<td>3일</td>
		<td>1.36%</td>
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
			<td>25년12월24일-26년4월6일\EC_TRANSACTION_MONITORING.csv</td>
			<td>csv</td>
			<td>1768054</td>
			<td>1768054</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>2025-12-24 10:25:31.632</td>
			<td>2026-04-06 08:51:33.002</td>
			<td>2026-04-06 08:52:21.416</td>
			<td>59</td>
			<td>317.01</td>
		</tr>
		<tr>
			<td>1</td>
			<td>26년8월15일-26년9월14일</td>
			<td>26년8월15일-26년9월14일\transactions.csv</td>
			<td>csv</td>
			<td>213381</td>
			<td>213381</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>2026-08-15 00:00:00.257</td>
			<td>2026-09-14 14:19:58.603</td>
			<td>2026-09-14 14:20:09.431</td>
			<td>19</td>
			<td>36.65</td>
		</tr>
		<tr>
			<td>2</td>
			<td>26년9월30일-26년10월2일</td>
			<td>26년9월30일-26년10월2일\EC_MESSAGE_BODY.csv</td>
			<td>sql</td>
			<td>27268</td>
			<td>27268</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
			<td>2026-09-30 09:49:26.483</td>
			<td>2026-10-02 15:36:59.444</td>
			<td>2026-10-02 15:36:59.698</td>
			<td>3</td>
			<td>13.03</td>
		</tr>
	</table>

	**원본 표 2 · 셀 4**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>transactionId</td>
			<td>interfaceId</td>
			<td>processHubId</td>
			<td>startChannelId</td>
			<td>endChannelId</td>
			<td>processCount</td>
			<td>status</td>
			<td>responseCode</td>
			<td>interfaceType</td>
			<td>categoryName</td>
			<td>startTime</td>
			<td>endTime</td>
			<td>processTime</td>
			<td>retryCount</td>
			<td>source_period</td>
			<td>source_file</td>
		</tr>
		<tr>
			<td>0</td>
			<td>TR-IF_DB2DB_100-20251224115033339-1</td>
			<td>IF_DB2DB_100</td>
			<td>HUB</td>
			<td>TSC</td>
			<td>SOL</td>
			<td>2.0</td>
			<td>H</td>
			<td>0001</td>
			<td>NORMAL</td>
			<td>DB2DB</td>
			<td>2025-12-24 11:50:33.379</td>
			<td>2025-12-24 11:50:33.993</td>
			<td>614.0</td>
			<td>0.0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_TRANSACTION_MONITORING.csv</td>
		</tr>
		<tr>
			<td>1</td>
			<td>TR-IF_DB2DB_900-20251224151514032-1</td>
			<td>IF_DB2DB_900</td>
			<td>HUB</td>
			<td>TSC</td>
			<td>SOL</td>
			<td>2.0</td>
			<td>S</td>
			<td>0000</td>
			<td>NORMAL</td>
			<td>DB2DB &gt; 07.성능 테스트용</td>
			<td>2025-12-24 15:15:14.042</td>
			<td>2025-12-24 15:15:16.511</td>
			<td>2469.0</td>
			<td>0.0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_TRANSACTION_MONITORING.csv</td>
		</tr>
		<tr>
			<td>2</td>
			<td>TR-IF_DB2DB_905-20251224151546010-1</td>
			<td>IF_DB2DB_905</td>
			<td>HUB</td>
			<td>TSC</td>
			<td>SOL</td>
			<td>2.0</td>
			<td>S</td>
			<td>0000</td>
			<td>NORMAL</td>
			<td>DB2DB &gt; 07.성능 테스트용</td>
			<td>2025-12-24 15:15:46.101</td>
			<td>2025-12-24 15:15:51.106</td>
			<td>5005.0</td>
			<td>0.0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_TRANSACTION_MONITORING.csv</td>
		</tr>
		<tr>
			<td>3</td>
			<td>TR-IF_DB2DB_906-20251224151547162-1</td>
			<td>IF_DB2DB_906</td>
			<td>HUB</td>
			<td>TSC</td>
			<td>SOL</td>
			<td>2.0</td>
			<td>S</td>
			<td>0000</td>
			<td>NORMAL</td>
			<td>DB2DB &gt; 07.성능 테스트용</td>
			<td>2025-12-24 15:15:47.366</td>
			<td>2025-12-24 15:15:51.197</td>
			<td>3831.0</td>
			<td>0.0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_TRANSACTION_MONITORING.csv</td>
		</tr>
		<tr>
			<td>4</td>
			<td>TR-IF_DB2DB_907-20251224151548246-1</td>
			<td>IF_DB2DB_907</td>
			<td>HUB</td>
			<td>TSC</td>
			<td>SOL</td>
			<td>2.0</td>
			<td>S</td>
			<td>0000</td>
			<td>NORMAL</td>
			<td>DB2DB &gt; 07.성능 테스트용</td>
			<td>2025-12-24 15:15:48.326</td>
			<td>2025-12-24 15:15:51.552</td>
			<td>3226.0</td>
			<td>0.0</td>
			<td>25년12월24일-26년4월6일</td>
			<td>25년12월24일-26년4월6일\EC_TRANSACTION_MONITORING.csv</td>
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
			<td>2008703</td>
		</tr>
		<tr>
			<td>interfaceId</td>
			<td>category</td>
			<td>0</td>
			<td>0.000</td>
			<td>406</td>
		</tr>
		<tr>
			<td>processHubId</td>
			<td>category</td>
			<td>0</td>
			<td>0.000</td>
			<td>5</td>
		</tr>
		<tr>
			<td>startChannelId</td>
			<td>category</td>
			<td>3</td>
			<td>0.000</td>
			<td>10</td>
		</tr>
		<tr>
			<td>endChannelId</td>
			<td>category</td>
			<td>3</td>
			<td>0.000</td>
			<td>11</td>
		</tr>
		<tr>
			<td>processCount</td>
			<td>float64</td>
			<td>3</td>
			<td>0.000</td>
			<td>5</td>
		</tr>
		<tr>
			<td>status</td>
			<td>category</td>
			<td>0</td>
			<td>0.000</td>
			<td>5</td>
		</tr>
		<tr>
			<td>responseCode</td>
			<td>category</td>
			<td>7067</td>
			<td>0.352</td>
			<td>44</td>
		</tr>
		<tr>
			<td>interfaceType</td>
			<td>category</td>
			<td>3</td>
			<td>0.000</td>
			<td>3</td>
		</tr>
		<tr>
			<td>categoryName</td>
			<td>category</td>
			<td>3</td>
			<td>0.000</td>
			<td>18</td>
		</tr>
		<tr>
			<td>startTime</td>
			<td>datetime64[ns]</td>
			<td>0</td>
			<td>0.000</td>
			<td>1978702</td>
		</tr>
		<tr>
			<td>endTime</td>
			<td>datetime64[ns]</td>
			<td>7060</td>
			<td>0.351</td>
			<td>1990166</td>
		</tr>
		<tr>
			<td>processTime</td>
			<td>float64</td>
			<td>7060</td>
			<td>0.351</td>
			<td>22315</td>
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
			<td>interfaceId</td>
			<td>processHubId</td>
			<td>startChannelId</td>
			<td>endChannelId</td>
			<td>processCount</td>
			<td>status</td>
			<td>responseCode</td>
			<td>interfaceType</td>
			<td>categoryName</td>
			<td>startTime</td>
			<td>endTime</td>
			<td>processTime</td>
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
			<td>0.4</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.4</td>
			<td>0.4</td>
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
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
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
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
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
			<td>1768054</td>
			<td>1768054</td>
			<td>59</td>
			<td>88.02</td>
			<td>29967.02</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>213381</td>
			<td>213381</td>
			<td>19</td>
			<td>10.62</td>
			<td>11230.58</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>27268</td>
			<td>27268</td>
			<td>3</td>
			<td>1.36</td>
			<td>9089.33</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-8104-aaa3-00b2bd48f4af"></image>

*출처: `transaction_analysis.ipynb` · 셀 8 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-814e-96e0-00b211dbbcc9"></image>

*출처: `transaction_analysis.ipynb` · 셀 8 · 그림 2. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- 전체 2,008,703건 중 ① 25.12–26.04가 88.02%입니다. 시각화는 30,000건을 기간별 균형 추출했습니다.

**원본 출력의 확인 수치**

- ① 25.12–26.04: 종료시각 결측 7,060건 (0.40%).
- ② 26.08–09: 종료시각 결측 0건 (0.00%).
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
			<td>1768054</td>
			<td>10000</td>
			<td>88.020</td>
			<td>33.333</td>
			<td>0.566</td>
		</tr>
		<tr>
			<td>② 26.08–09</td>
			<td>213381</td>
			<td>10000</td>
			<td>10.623</td>
			<td>33.333</td>
			<td>4.686</td>
		</tr>
		<tr>
			<td>③ 26.09.30–10.02</td>
			<td>27268</td>
			<td>10000</td>
			<td>1.357</td>
			<td>33.333</td>
			<td>36.673</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

균형 표본은 작은 기간의 분포 형태를 읽기 위한 것이며 실제 기간 비중이나 장애율을 대표하지 않습니다. 종료시각 누락은 미완료·수집 누락을 모두 포함할 수 있습니다. 폴더 기간 전체가 매일 관측된 것은 아닙니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>발생률·경보량은 전체 행으로 계산합니다. `missing_end`와 처리시간 결측 표시를 유지하고, 완료 후 지연 탐지와 미완료 경과시간 감시를 분리합니다. 원본 선택은 파일명이 아니라 스키마로 검증합니다.
</callout>

---

## 2. 처리시간의 긴 꼬리와 기간 차이

**핵심 결과**

기간별 중앙값은 **5,458ms / 58ms / 5,984ms**, p99는 **10,952ms / 398.2ms / 52,072.97ms**입니다. 전체 최대 처리시간은 **33,781,607ms, 약 9.38시간**이고 음수 처리시간은 0건입니다.

<table fit-page-width="true" header-row="true">
	<tr>
		<td>기간</td>
		<td>중앙값(ms)</td>
		<td>p95(ms)</td>
		<td>p99(ms)</td>
	</tr>
	<tr>
		<td>①</td>
		<td>5,458</td>
		<td>9,684</td>
		<td>10,952</td>
	</tr>
	<tr>
		<td>②</td>
		<td>58</td>
		<td>159</td>
		<td>398.2</td>
	</tr>
	<tr>
		<td>③</td>
		<td>5,984</td>
		<td>9,926</td>
		<td>52,072.97</td>
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
			<td>2001643.0</td>
			<td>5358.338</td>
			<td>96904.603</td>
			<td>1.0</td>
			<td>3.0</td>
			<td>144.0</td>
			<td>4814.0</td>
			<td>7514.0</td>
			<td>9617.0</td>
			<td>10818.0</td>
			<td>216979.07</td>
			<td>33781607.0</td>
			<td>0.351</td>
			<td>0.0</td>
			<td>0</td>
			<td>284.234</td>
		</tr>
		<tr>
			<td>processTime</td>
			<td>2001643.0</td>
			<td>5358.338</td>
			<td>96904.603</td>
			<td>1.0</td>
			<td>3.0</td>
			<td>144.0</td>
			<td>4814.0</td>
			<td>7514.0</td>
			<td>9617.0</td>
			<td>10818.0</td>
			<td>216979.07</td>
			<td>33781607.0</td>
			<td>0.351</td>
			<td>0.0</td>
			<td>0</td>
			<td>284.234</td>
		</tr>
		<tr>
			<td>processCount</td>
			<td>2008700.0</td>
			<td>1.856</td>
			<td>0.400</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>2.0</td>
			<td>2.0</td>
			<td>2.0</td>
			<td>2.0</td>
			<td>3.0</td>
			<td>3.00</td>
			<td>19.0</td>
			<td>0.000</td>
			<td>0.0</td>
			<td>0</td>
			<td>-1.081</td>
		</tr>
		<tr>
			<td>retryCount</td>
			<td>2008703.0</td>
			<td>0.000</td>
			<td>0.000</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.0</td>
			<td>0.00</td>
			<td>0.0</td>
			<td>0.000</td>
			<td>100.0</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-8188-8be2-00b2c560a283"></image>

*출처: `transaction_analysis.ipynb` · 셀 14 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 전체 중앙값 5,458, p95 9,684, p99 10,952.
- ② 26.08–09: 전체 중앙값 58, p95 159, p99 398.
- ③ 26.09.30–10.02: 전체 중앙값 5,984, p95 9,926, p99 52,073.

**원본 출력의 확인 수치**

- 전체 유효 처리시간 최대 33,781,607ms; 음수 0건, 종료시각 결측 7,060건.

<details>
<summary>원본 수치표 펼치기 · 셀 20 · 1개</summary>
	**원본 표 1 · 셀 20**

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
			<td>1597754</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_FLOW_CONTROL_FILE_TEST_50-20260227003812...</td>
			<td>2026-02-27 00:38:12.568</td>
			<td>F</td>
			<td>33781607.0</td>
		</tr>
		<tr>
			<td>1597752</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_FLOW_CONTROL_FILE_TEST_23-20260227010756...</td>
			<td>2026-02-27 01:07:56.570</td>
			<td>F</td>
			<td>31997605.0</td>
		</tr>
		<tr>
			<td>1597753</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_FLOW_CONTROL_FILE_TEST_10-20260227021102...</td>
			<td>2026-02-27 02:11:02.060</td>
			<td>F</td>
			<td>28212115.0</td>
		</tr>
		<tr>
			<td>1597749</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_FLOW_CONTROL_FILE_TEST_01-20260227021323...</td>
			<td>2026-02-27 02:13:23.144</td>
			<td>F</td>
			<td>28071033.0</td>
		</tr>
		<tr>
			<td>1597755</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_FLOW_CONTROL_FILE_TEST_03-20260227021655...</td>
			<td>2026-02-27 02:16:55.938</td>
			<td>F</td>
			<td>27858238.0</td>
		</tr>
		<tr>
			<td>1597750</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_FLOW_CONTROL_FILE_TEST_04-20260227021656...</td>
			<td>2026-02-27 02:16:56.316</td>
			<td>F</td>
			<td>27857860.0</td>
		</tr>
		<tr>
			<td>1597770</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_907-20260227021719594-2988</td>
			<td>2026-02-27 02:17:19.613</td>
			<td>F</td>
			<td>27841927.0</td>
		</tr>
		<tr>
			<td>1597769</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_967-20260227021719738-3019</td>
			<td>2026-02-27 02:17:19.758</td>
			<td>F</td>
			<td>27841779.0</td>
		</tr>
		<tr>
			<td>1597766</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_AP2TCP_000-20260227021720754-2543</td>
			<td>2026-02-27 02:17:20.766</td>
			<td>F</td>
			<td>27840775.0</td>
		</tr>
		<tr>
			<td>1597765</td>
			<td>25년12월24일-26년4월6일</td>
			<td>TR-IF_DB2DB_997-20260227021720771-9415</td>
			<td>2026-02-27 02:17:20.787</td>
			<td>F</td>
			<td>27840747.0</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

평균 하나로 정상 시간을 대표하기 어렵습니다. 최근 기간은 중앙값이 첫 기간과 비슷하지만 상위 꼬리는 훨씬 깁니다. 장시간 배치나 테스트 실행일 수도 있어 긴 시간만으로 장애를 확정하지 않습니다. 히스토그램은 균형 표본, 기준선·분위수는 전체 유효 값입니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>`log1p(duration_ms)`와 업무별 상대 편차를 사용합니다. `processTime`은 계산된 처리시간과 비교 가능한 전체 행에서 일치하므로 중복 입력하지 않습니다. 지연 전용 경보는 상방 편차를, 비정상적인 빠른 종료는 별도 유형으로 해석합니다.
</callout>

---

## 3. 선언 Process 수와 재시도

**핵심 결과**

`processCount=2`는 첫 기간의 **93.09%**, `processCount=1`은 두 번째 기간의 **99.96%**, 최근 기간의 **99.59%**입니다. `retryCount`는 모든 거래에서 0입니다.

<image src="file-upload://3f125113-b9ae-8149-93c1-00b2a93e5eed"></image>

*출처: `transaction_analysis.ipynb` · 셀 16 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈값 2.0, 1,645,858건 (93.09%).
- ② 26.08–09: 최빈값 1.0, 213,286건 (99.96%).
- ③ 26.09.30–10.02: 최빈값 1.0, 27,157건 (99.59%).

<image src="file-upload://3f125113-b9ae-813a-89a5-00b2fd0ce80b"></image>

*출처: `transaction_analysis.ipynb` · 셀 18 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈값 0.0, 1,768,054건 (100.00%).
- ② 26.08–09: 최빈값 0.0, 213,381건 (100.00%).
- ③ 26.09.30–10.02: 최빈값 0.0, 27,268건 (100.00%).

**이 결과로 알 수 있는 내용**

거래의 실행 구조가 2단계 중심에서 1단계 중심으로 바뀌었습니다. `processCount`는 선언된 단계 수이지 각 단계가 처리한 데이터 건수가 아닙니다. 현재 데이터로 재시도 행동을 학습할 수는 없습니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>선언 단계 수는 이산 구조 피처로 사용하고, 실제 관측된 고유 Process 수와의 차이는 Merge에서 계산합니다. 상수 재시도 수치 피처는 제거하되 이후 비영 값 출현은 별도 감사합니다. 재시도 0을 시스템에 재시도가 없다는 증거로 쓰지 않습니다.
</callout>

---

## 4. 일별·요일·시간대 트래픽

**핵심 결과**

일별 최대 거래량은 각각 **329,988 / 25,922 / 27,051건**입니다. 관측일당 로그량이 가장 많은 요일·시간은 ① 일요일 17시, ② 수요일 16시, ③ 수요일 11시입니다. 과거 24시간 MAD 규칙은 비교 가능한 **250 / 14 / 0시간**에서 후보 **54 / 5 / 0시간**을 찾았습니다.

<image src="file-upload://3f125113-b9ae-81b3-882f-00b29c772b1b"></image>

*출처: `transaction_analysis.ipynb` · 셀 22 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 관측 59일, 최대 329,988 (2025-12-28).
- ② 26.08–09: 관측 19일, 최대 25,922 (2026-08-19).
- ③ 26.09.30–10.02: 관측 3일, 최대 27,051 (2026-09-30).

<details>
<summary>원본 수치표 펼치기 · 셀 22 · 1개</summary>
	**원본 표 1 · 셀 22**

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
			<td>59</td>
			<td>1768054</td>
			<td>14.0</td>
			<td>329988</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>19</td>
			<td>213381</td>
			<td>10000.0</td>
			<td>25922</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>3</td>
			<td>27268</td>
			<td>186.0</td>
			<td>27051</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-81ff-b2cf-00b2b494ffd2"></image>

*출처: `transaction_analysis.ipynb` · 셀 23 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-8199-8f3c-00b259b71a54"></image>

*출처: `transaction_analysis.ipynb` · 셀 23 · 그림 2. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 관측 59일, 최대 1,800,114 (2026-03-20).
- ② 26.08–09: 관측 19일, 최대 78,980 (2026-09-04).
- ③ 26.09.30–10.02: 관측 3일, 최대 64,471 (2026-10-02).

**원본 출력의 확인 수치**

- ① 25.12–26.04: 관측 59일, 최대 100 (2026-02-03).
- ② 26.08–09: 관측 19일, 최대 100 (2026-08-22).
- ③ 26.09.30–10.02: 관측 3일, 최대 87.10 (2026-10-01).

<details>
<summary>원본 수치표 펼치기 · 셀 23 · 1개</summary>
	**원본 표 1 · 셀 23**

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
			<td>1280498</td>
			<td>8</td>
			<td>160062.25</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>2026-01</td>
			<td>25194</td>
			<td>16</td>
			<td>1574.62</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>2026-02</td>
			<td>311074</td>
			<td>14</td>
			<td>22219.57</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>2026-03</td>
			<td>151221</td>
			<td>17</td>
			<td>8895.35</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>2026-04</td>
			<td>67</td>
			<td>4</td>
			<td>16.75</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>2026-08</td>
			<td>211145</td>
			<td>13</td>
			<td>16241.92</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>2026-09</td>
			<td>2236</td>
			<td>6</td>
			<td>372.67</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>2026-09</td>
			<td>27051</td>
			<td>1</td>
			<td>27051.00</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>2026-10</td>
			<td>217</td>
			<td>2</td>
			<td>108.50</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-8150-b17a-00b2cae9d7b7"></image>

*출처: `transaction_analysis.ipynb` · 셀 25 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최다 시간대 일요일 17시, 관측 날짜당 7,100.0건. 빨간 테두리로 표시했습니다.
- ② 26.08–09: 최다 시간대 수요일 16시, 관측 날짜당 1,082.0건. 빨간 테두리로 표시했습니다.
- ③ 26.09.30–10.02: 최다 시간대 수요일 11시, 관측 날짜당 12,113.0건. 빨간 테두리로 표시했습니다.

<image src="file-upload://3f125113-b9ae-8109-85d8-00b286a0ee8e"></image>

*출처: `transaction_analysis.ipynb` · 셀 27 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 기준선 계산 가능 250시간, 후보 54시간, 기준선 없는 관측 146시간.
- ② 26.08–09: 기준선 계산 가능 14시간, 후보 5시간, 기준선 없는 관측 244시간.
- ③ 26.09.30–10.02: 기준선 계산 가능 0시간, 후보 0시간, 기준선 없는 관측 13시간.

<details>
<summary>원본 수치표 펼치기 · 셀 27 · 2개</summary>
	**원본 표 1 · 셀 27**

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
			<td>250</td>
			<td>54</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>735</td>
			<td>258</td>
			<td>14</td>
			<td>5</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>55</td>
			<td>13</td>
			<td>0</td>
			<td>0</td>
		</tr>
	</table>

	**원본 표 2 · 셀 27**

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
			<td>5.0</td>
			<td>5.0</td>
			<td>0.0</td>
			<td>344.0</td>
			<td>20008.00</td>
			<td>0.000000</td>
			<td>-2041.150810</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>2041.150810</td>
		</tr>
		<tr>
			<td>2328</td>
			<td>2026-03-31 10:00:00</td>
			<td>5.0</td>
			<td>5.0</td>
			<td>1.0</td>
			<td>226.0</td>
			<td>350.60</td>
			<td>20.000000</td>
			<td>-1718.381873</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>1718.381873</td>
		</tr>
		<tr>
			<td>795</td>
			<td>2026-01-26 13:00:00</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>1.0</td>
			<td>511.0</td>
			<td>511.00</td>
			<td>100.000000</td>
			<td>-419.281425</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>419.281425</td>
		</tr>
		<tr>
			<td>2334</td>
			<td>2026-03-31 16:00:00</td>
			<td>2795.0</td>
			<td>2795.0</td>
			<td>3.0</td>
			<td>2584.0</td>
			<td>3093.00</td>
			<td>0.107335</td>
			<td>-260.417813</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>260.417813</td>
		</tr>
		<tr>
			<td>141</td>
			<td>2025-12-30 07:00:00</td>
			<td>8.0</td>
			<td>8.0</td>
			<td>3.0</td>
			<td>444.0</td>
			<td>6923.10</td>
			<td>37.500000</td>
			<td>-222.032353</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>222.032353</td>
		</tr>
		<tr>
			<td>2336</td>
			<td>2026-03-31 18:00:00</td>
			<td>3250.0</td>
			<td>3250.0</td>
			<td>0.0</td>
			<td>2561.0</td>
			<td>3159.00</td>
			<td>0.000000</td>
			<td>-216.720219</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>216.720219</td>
		</tr>
		<tr>
			<td>123</td>
			<td>2025-12-29 13:00:00</td>
			<td>7134.0</td>
			<td>7134.0</td>
			<td>2.0</td>
			<td>7790.0</td>
			<td>10026.35</td>
			<td>0.028035</td>
			<td>-213.861494</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>213.861494</td>
		</tr>
		<tr>
			<td>2326</td>
			<td>2026-03-31 08:00:00</td>
			<td>2022.0</td>
			<td>2022.0</td>
			<td>1.0</td>
			<td>2687.0</td>
			<td>7293.90</td>
			<td>0.049456</td>
			<td>-150.403673</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>150.403673</td>
		</tr>
		<tr>
			<td>127</td>
			<td>2025-12-29 17:00:00</td>
			<td>7770.0</td>
			<td>7770.0</td>
			<td>0.0</td>
			<td>7957.0</td>
			<td>10028.55</td>
			<td>0.000000</td>
			<td>-147.135890</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>147.135890</td>
		</tr>
		<tr>
			<td>126</td>
			<td>2025-12-29 16:00:00</td>
			<td>8332.0</td>
			<td>8332.0</td>
			<td>1.0</td>
			<td>7577.5</td>
			<td>9954.00</td>
			<td>0.012002</td>
			<td>-145.460693</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>145.460693</td>
		</tr>
		<tr>
			<td>2944</td>
			<td>2026-09-03 17:00:00</td>
			<td>4.0</td>
			<td>4.0</td>
			<td>4.0</td>
			<td>50660.0</td>
			<td>52150.80</td>
			<td>100.000000</td>
			<td>-102.752013</td>
			<td>True</td>
			<td>True</td>
			<td>26년8월15일-26년9월14일</td>
			<td>102.752013</td>
		</tr>
		<tr>
			<td>2961</td>
			<td>2026-09-04 10:00:00</td>
			<td>2.0</td>
			<td>2.0</td>
			<td>0.0</td>
			<td>819.0</td>
			<td>1197.00</td>
			<td>0.000000</td>
			<td>-101.373154</td>
			<td>True</td>
			<td>True</td>
			<td>26년8월15일-26년9월14일</td>
			<td>101.373154</td>
		</tr>
		<tr>
			<td>2962</td>
			<td>2026-09-04 11:00:00</td>
			<td>9.0</td>
			<td>9.0</td>
			<td>4.0</td>
			<td>1146.0</td>
			<td>48093.80</td>
			<td>44.444444</td>
			<td>-75.819364</td>
			<td>True</td>
			<td>True</td>
			<td>26년8월15일-26년9월14일</td>
			<td>75.819364</td>
		</tr>
		<tr>
			<td>2325</td>
			<td>2026-03-31 07:00:00</td>
			<td>5772.0</td>
			<td>5772.0</td>
			<td>1.0</td>
			<td>2612.0</td>
			<td>4682.50</td>
			<td>0.017325</td>
			<td>-42.237605</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>42.237605</td>
		</tr>
		<tr>
			<td>745</td>
			<td>2026-01-24 11:00:00</td>
			<td>195.0</td>
			<td>195.0</td>
			<td>0.0</td>
			<td>1414.0</td>
			<td>1932.50</td>
			<td>0.000000</td>
			<td>-36.163102</td>
			<td>True</td>
			<td>True</td>
			<td>25년12월24일-26년4월6일</td>
			<td>36.163102</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

요일·시간 패턴과 수집 공백을 함께 봐야 합니다. 최근 기간의 0후보는 기준선을 계산할 구간이 없어서 나온 값이지 정상 판정이 아닙니다. 표본이 적은 날의 p95·실패율은 불안정하고, 일별 실패율의 분모는 상태가 알려진 로그입니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>시간·요일을 sin/cos로 표현합니다. 시간창 모델은 `count`, 지연 분위수, 도착 간격과 수집 상태를 함께 사용합니다. 미관측 구간을 0으로 채우지 않고 판정 불가로 유지합니다. 과거 값만으로 기준선을 만들고 요일·시간대별 계절성 기준과 비교합니다.
</callout>

---

## 5. 상태·업무·처리 경로

**핵심 결과**

성공 상태 비율은 ① **82.27%**, ③ **99.68%**이고 ②는 실패 상태가 **61.88%**입니다. 첫 기간은 DB2DB 분류가 **91.52%**, 최근 기간은 `IF_TEST_HA_FAILOVER` 거래가 **99.20%**입니다.

<image src="file-upload://3f125113-b9ae-81b0-98a2-00b277b2f77d"></image>

*출처: `transaction_analysis.ipynb` · 셀 29 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-8122-b048-00b25ee081ce"></image>

*출처: `transaction_analysis.ipynb` · 셀 29 · 그림 2. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-8188-9909-00b285b2d7d1"></image>

*출처: `transaction_analysis.ipynb` · 셀 29 · 그림 3. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-8181-a9b7-00b24e59f95f"></image>

*출처: `transaction_analysis.ipynb` · 셀 29 · 그림 4. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈값 S, 1,454,528건 (82.27%).
- ② 26.08–09: 최빈값 F, 132,036건 (61.88%).
- ③ 26.09.30–10.02: 최빈값 S, 27,180건 (99.68%).

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈 0000가 82.27%, 상위 6개 합계 99.93%.
- ② 26.08–09: 최빈 4311가 58.38%, 상위 6개 합계 99.98%.
- ③ 26.09.30–10.02: 최빈 0000가 99.68%, 상위 6개 합계 100.00%.

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈 IF_DB2DB_906가 2.83%, 상위 6개 합계 16.82%.
- ② 26.08–09: 최빈 IF_CLAUDE_CREATE_001가 39.54%, 상위 6개 합계 99.94%.
- ③ 26.09.30–10.02: 최빈 IF_TEST_HA_FAILOVER가 99.20%, 상위 6개 합계 99.91%.

**원본 출력의 확인 수치**

- ① 25.12–26.04: 최빈 DB2DB가 91.52%, 상위 6개 합계 100.00%.
- ② 26.08–09: 최빈 NONE가 61.28%, 상위 4개 합계 100.00%.
- ③ 26.09.30–10.02: 최빈 NONE가 99.88%, 상위 2개 합계 100.00%.

**원본 출력의 확인 수치**

- 경로 표는 전체 데이터 기준 로그량 상위 25개이며, p95는 각 경로 유효 처리시간의 95% 분위수입니다.

<details>
<summary>원본 수치표 펼치기 · 셀 29 · 2개</summary>
	**원본 표 1 · 셀 29**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td>status</td>
			<td>F</td>
			<td>H</td>
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
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>17.32</td>
			<td>0.00</td>
			<td>0.4</td>
			<td>82.27</td>
			<td>0.01</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>61.88</td>
			<td>0.00</td>
			<td>0.0</td>
			<td>38.12</td>
			<td>0.00</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>0.16</td>
			<td>0.01</td>
			<td>0.0</td>
			<td>99.68</td>
			<td>0.16</td>
		</tr>
	</table>

	**원본 표 2 · 셀 29**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td></td>
			<td>rows</td>
			<td>median_ms</td>
			<td>p95_ms</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td>route_key</td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>TSC → HUB → SOL</td>
			<td>1642352</td>
			<td>5805.0</td>
			<td>9719.00</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>HUB → HUB → HUB</td>
			<td>213277</td>
			<td>58.0</td>
			<td>159.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>SOL → HUB → SOL</td>
			<td>60454</td>
			<td>4.0</td>
			<td>21.00</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>SOL → HUB → SOL</td>
			<td>27229</td>
			<td>5984.0</td>
			<td>9933.40</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>TSC → DIRECT → SOL</td>
			<td>25363</td>
			<td>1408.0</td>
			<td>2349.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → HUB → HUB</td>
			<td>24888</td>
			<td>6.0</td>
			<td>7060.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>TSC → HUB → TSC</td>
			<td>13312</td>
			<td>4.0</td>
			<td>28.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>TSC → HUB → HUB</td>
			<td>1165</td>
			<td>4.0</td>
			<td>16.00</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>SOL → DIRECT → TSC</td>
			<td>288</td>
			<td>93.5</td>
			<td>47012.45</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>SOL → HUB → TSC</td>
			<td>152</td>
			<td>1276.0</td>
			<td>835208.15</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>TSC → HUB → SOL</td>
			<td>60</td>
			<td>6964.0</td>
			<td>33624.95</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>TSC → HUB → SOL</td>
			<td>36</td>
			<td>7443.0</td>
			<td>7832.25</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>SOL → HUB → SOL</td>
			<td>33</td>
			<td>705.0</td>
			<td>51796.20</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>TSC → HUB → SAP</td>
			<td>25</td>
			<td>2851.0</td>
			<td>10800.20</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>TSC → HUB → SSCH</td>
			<td>22</td>
			<td>1483.5</td>
			<td>30047.95</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>HUB → DIRECT → HUB</td>
			<td>12</td>
			<td>70.0</td>
			<td>145.05</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>SAP → HUB → SOL</td>
			<td>9</td>
			<td>2217.0</td>
			<td>5058.80</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>HUG_TEST → HUB → HUG_TEST</td>
			<td>4</td>
			<td>28790.0</td>
			<td>40504.20</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>TSC → HUB → HUB</td>
			<td>3</td>
			<td>553.0</td>
			<td>54364.90</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>MISSING → - → MISSING</td>
			<td>3</td>
			<td>151.0</td>
			<td>261.70</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>THUB → THUB → THUB</td>
			<td>2</td>
			<td>20155.0</td>
			<td>37823.80</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>PATCH_SERVER → HUB → PATCH_SERVER</td>
			<td>2</td>
			<td>764.5</td>
			<td>1423.75</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>SSCH → HUB → SSCH</td>
			<td>2</td>
			<td>614.5</td>
			<td>1067.65</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>TSC → HUB → TSC</td>
			<td>2</td>
			<td>1324.0</td>
			<td>1476.10</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>SOL → SOL → SOL</td>
			<td>2</td>
			<td>232.0</td>
			<td>300.40</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

기간 차이는 시간 변화만이 아니라 업무 구성 변화입니다. 특정 업무가 대부분인 기간의 결과를 일반 서비스 전체로 확장하면 안 됩니다. 응답코드 4311의 운영 의미나 ID에 TEST가 있다는 사실만으로 장애·장애 주입을 확정하지 않습니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>거래 ID는 조인 키로만 사용합니다. `interfaceId`와 경로의 학습 구간 빈도·희귀도 및 업무 그룹별 기준을 검토합니다. 상태·응답코드는 행동 모델의 사후 설명용으로 유지하고 정답으로 간주하지 않습니다. 운영 업무와 테스트 업무는 메타데이터 확인 후 분리 평가합니다.
</callout>

---

## 6. 전체 IQR와 업무별 상대 편차

**핵심 결과**

`processCount`의 Q1·Q3가 모두 2라 IQR은 0이고 **362,740행(18.058%)**이 해당 변수의 후보가 됩니다. 업무별 처리시간 MAD 후보율은 **5.063% / 3.403% / 45.510%**입니다.

<details>
<summary>원본 수치표 펼치기 · 셀 31 · 3개</summary>
	**원본 표 1 · 셀 31**

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
			<td>144.0</td>
			<td>7514.0</td>
			<td>7370.0</td>
			<td>6766</td>
			<td>0.337</td>
		</tr>
		<tr>
			<td>1</td>
			<td>processCount</td>
			<td>2.0</td>
			<td>2.0</td>
			<td>0.0</td>
			<td>362740</td>
			<td>18.058</td>
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

	**원본 표 2 · 셀 31**

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
			<td>1768054</td>
			<td>128167</td>
			<td>1759424</td>
			<td>89080</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>213381</td>
			<td>213344</td>
			<td>213221</td>
			<td>7256</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>27268</td>
			<td>27224</td>
			<td>27203</td>
			<td>12380</td>
		</tr>
	</table>

	**원본 표 3 · 셀 31**

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
			<td>interfaceId</td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>84366</td>
			<td>4.204693</td>
			<td>0.161641</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>IF_REST_ERROR_RESPONSE</td>
			<td>82586</td>
			<td>3.828641</td>
			<td>0.115069</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_906</td>
			<td>50081</td>
			<td>8.543446</td>
			<td>0.462899</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_905</td>
			<td>49665</td>
			<td>8.575273</td>
			<td>0.453665</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_907</td>
			<td>49448</td>
			<td>8.561019</td>
			<td>0.457524</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_915</td>
			<td>49434</td>
			<td>8.572912</td>
			<td>0.460628</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_916</td>
			<td>49413</td>
			<td>8.567506</td>
			<td>0.450341</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_902</td>
			<td>49167</td>
			<td>8.575085</td>
			<td>0.445789</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_914</td>
			<td>48934</td>
			<td>8.590444</td>
			<td>0.442324</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_917</td>
			<td>48902</td>
			<td>8.571492</td>
			<td>0.449140</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_904</td>
			<td>48886</td>
			<td>8.583355</td>
			<td>0.441378</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_908</td>
			<td>48876</td>
			<td>8.568646</td>
			<td>0.441412</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_901</td>
			<td>48764</td>
			<td>8.572249</td>
			<td>0.449470</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_919</td>
			<td>48764</td>
			<td>8.574518</td>
			<td>0.453335</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_909</td>
			<td>48652</td>
			<td>8.569216</td>
			<td>0.456044</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-8103-8301-00b2f0b748fd"></image>

*출처: `transaction_analysis.ipynb` · 셀 32 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-81fa-9d32-00b289ea480b"></image>

*출처: `transaction_analysis.ipynb` · 셀 32 · 그림 2. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- IQR=0인 변수: processCount, retryCount. 처리시간 외 카운트 변수는 정상 최빈값에서 벗어나기만 해도 후보가 될 수 있습니다.

**원본 출력의 확인 수치**

- ① 25.12–26.04 / 업무별 상대 지연: 89,080 / 1,759,424건 (5.063%).
- ② 26.08–09 / 업무별 상대 지연: 7,256 / 213,221건 (3.403%).
- ③ 26.09.30–10.02 / 업무별 상대 지연: 12,380 / 27,203건 (45.510%).

**원본 출력의 확인 수치**

- ① 25.12–26.04: 그룹 기준선으로 비교 불가 8,630건.
- ② 26.08–09: 그룹 기준선으로 비교 불가 160건.
- ③ 26.09.30–10.02: 그룹 기준선으로 비교 불가 65건.

**원본 출력의 확인 수치**

- 업무별 후보 비율의 분모는 전체 업무 행 수가 아니라 유효 처리시간·충분한 표본·MAD>0을 충족하는 비교 가능 행 수입니다.

<details>
<summary>원본 수치표 펼치기 · 셀 33 · 1개</summary>
	**원본 표 1 · 셀 33**

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
			<td>interfaceId</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>IF_TEST_HA_FAILOVER</td>
			<td>27051</td>
			<td>27051</td>
			<td>12367</td>
			<td>45.717349</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>IF_IBMMQ_SUB2DB_001</td>
			<td>49</td>
			<td>49</td>
			<td>13</td>
			<td>26.530612</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>SERVICE_DELAY_TEST</td>
			<td>2255</td>
			<td>2255</td>
			<td>444</td>
			<td>19.689579</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_106</td>
			<td>117</td>
			<td>117</td>
			<td>20</td>
			<td>17.094017</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_103</td>
			<td>767</td>
			<td>766</td>
			<td>126</td>
			<td>16.449086</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_007</td>
			<td>37</td>
			<td>36</td>
			<td>5</td>
			<td>13.888889</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_A2A_FILE_201</td>
			<td>36</td>
			<td>36</td>
			<td>5</td>
			<td>13.888889</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_9xx_DELETE</td>
			<td>154</td>
			<td>149</td>
			<td>18</td>
			<td>12.080537</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_SUB2DB_103</td>
			<td>3316</td>
			<td>3265</td>
			<td>357</td>
			<td>10.934150</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_936</td>
			<td>27110</td>
			<td>27073</td>
			<td>2728</td>
			<td>10.076460</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_A2A_FILE_208</td>
			<td>30</td>
			<td>30</td>
			<td>3</td>
			<td>10.000000</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_948</td>
			<td>2004</td>
			<td>1966</td>
			<td>195</td>
			<td>9.918616</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_A2A_FILE_202</td>
			<td>32</td>
			<td>32</td>
			<td>3</td>
			<td>9.375000</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_AP2TCP_002</td>
			<td>15988</td>
			<td>15484</td>
			<td>1416</td>
			<td>9.144924</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_937</td>
			<td>26862</td>
			<td>26822</td>
			<td>2393</td>
			<td>8.921781</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_932</td>
			<td>26713</td>
			<td>26679</td>
			<td>2326</td>
			<td>8.718468</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_101</td>
			<td>257</td>
			<td>257</td>
			<td>22</td>
			<td>8.560311</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_AP2TCP_000</td>
			<td>28375</td>
			<td>27619</td>
			<td>2351</td>
			<td>8.512256</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>Handler_TEST</td>
			<td>49</td>
			<td>47</td>
			<td>4</td>
			<td>8.510638</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_AP2TCP_001</td>
			<td>14479</td>
			<td>14189</td>
			<td>1179</td>
			<td>8.309254</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

혼합된 이산 분포에 공통 IQR을 적용하면 정상적인 1단계 업무도 대량 후보가 됩니다. MAD 후보의 분모는 처리시간·지원 표본·분산 조건을 충족한 비교 가능 행입니다. 코드가 절댓값 점수를 쓰므로 빠른 쪽과 느린 쪽 모두 포함합니다. 작은 MAD·다중 모드도 높은 후보율의 원인이 될 수 있습니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>IQR 후보를 학습 정답으로 사용하지 않습니다. 업무별 기준선은 실제 모델 평가에서는 학습/과거 구간만으로 다시 계산합니다. 표본 부족·MAD=0 그룹에는 상위 업무군 기준선이나 판정 불가를 적용하고 최소 분산 하한·지연 허용 기준은 검증 데이터로 결정합니다.
</callout>

---

## 7. 시간·상태·응답코드 정합성

**핵심 결과**

계산 처리시간과 `processTime`의 1ms 초과 불일치는 **0건**입니다. 최근 기간에는 `status=F`이면서 `responseCode=0000`인 **2건**이 있습니다.

<image src="file-upload://3f125113-b9ae-812d-b2af-00b257ea8505"></image>

*출처: `transaction_analysis.ipynb` · 셀 35 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04 / 계산/기록 시간 불일치: 0 / 1,760,994건 (0.000%).
- ② 26.08–09 / 계산/기록 시간 불일치: 0 / 213,381건 (0.000%).
- ③ 26.09.30–10.02 / 계산/기록 시간 불일치: 0 / 27,268건 (0.000%).
- ① 25.12–26.04 / 상태/응답코드 불일치: 0 / 1,760,679건 (0.000%).
- ② 26.08–09 / 상태/응답코드 불일치: 0 / 213,379건 (0.000%).
- ③ 26.09.30–10.02 / 상태/응답코드 불일치: 2 / 27,223건 (0.007%).

<details>
<summary>원본 수치표 펼치기 · 셀 35 · 2개</summary>
	**원본 표 1 · 셀 35**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>rows</td>
			<td>duration_mismatches</td>
			<td>status_code_candidates</td>
			<td>negative_duration</td>
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
			<td>1768054</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>213381</td>
			<td>0</td>
			<td>0</td>
			<td>0</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>27268</td>
			<td>0</td>
			<td>2</td>
			<td>0</td>
		</tr>
	</table>

	**원본 표 2 · 셀 35**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>source_period</td>
			<td>transactionId</td>
			<td>status</td>
			<td>responseCode</td>
			<td>duration_ms</td>
			<td>processTime</td>
		</tr>
		<tr>
			<td>1981582</td>
			<td>26년9월30일-26년10월2일</td>
			<td>TR-IF_IBMMQ_SOL_TEST-20261002085412089-01</td>
			<td>F</td>
			<td>0000</td>
			<td>308.0</td>
			<td>308.0</td>
		</tr>
		<tr>
			<td>1981583</td>
			<td>26년9월30일-26년10월2일</td>
			<td>TR-IF_IBMMQ_SOL_TEST-20261002085515204-01</td>
			<td>F</td>
			<td>0000</td>
			<td>156.0</td>
			<td>156.0</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

현재 시간 단위·계산 방식은 비교 가능한 로그에서 일관됩니다. 상태–코드 후보는 코드 정의나 상태 갱신 순서에 의한 것일 수 있어 확인이 필요합니다. H/Z/P/N 등을 정의 없이 실패로 합치면 안 됩니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>시간 일관성·상태–코드 불일치는 별도 정합성 채널로 유지합니다. 모델 점수와 함께 보여주되 모델의 확정 정답으로 쓰지 않습니다. 규칙별 비교 가능 여부와 분모를 함께 저장합니다.
</callout>

---

## 8. 업무별 도착 간격과 동시 실행

**핵심 결과**

같은 기간·업무의 직전 거래와의 도착 간격 중앙값은 **10.004초 / 10초 / 0.032초**입니다. 전체 0초 간격은 **1,334건**입니다.

<image src="file-upload://3f125113-b9ae-81c6-bad8-00b20c695e86"></image>

*출처: `transaction_analysis.ipynb` · 셀 37 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ① 25.12–26.04: 전체 중앙값 10.00, p95 20.07, p99 49.79.
- ② 26.08–09: 전체 중앙값 10, p95 10.02, p99 10.12.
- ③ 26.09.30–10.02: 전체 중앙값 0.03, p95 0.13, p99 0.30.

**원본 출력의 확인 수치**

- 0초 도착 간격 1,334건. 각 기간·업무의 첫 거래는 직전 간격이 없으므로 결측입니다.

<details>
<summary>원본 수치표 펼치기 · 셀 37 · 2개</summary>
	**원본 표 1 · 셀 37**

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
			<td>1767689.0</td>
			<td>538.343</td>
			<td>40961.195</td>
			<td>0.000</td>
			<td>0.159</td>
			<td>10.004</td>
			<td>20.068</td>
			<td>49.789</td>
			<td>7438686.659</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>213334.0</td>
			<td>46.091</td>
			<td>6138.442</td>
			<td>0.278</td>
			<td>9.885</td>
			<td>10.000</td>
			<td>10.024</td>
			<td>10.125</td>
			<td>1862238.395</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>27256.0</td>
			<td>1.624</td>
			<td>80.619</td>
			<td>0.000</td>
			<td>0.000</td>
			<td>0.032</td>
			<td>0.132</td>
			<td>0.302</td>
			<td>7107.230</td>
		</tr>
	</table>

	**원본 표 2 · 셀 37**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td></td>
			<td>intervals</td>
			<td>median_seconds</td>
			<td>zero_gap_pct</td>
		</tr>
		<tr>
			<td>source_period</td>
			<td>interfaceId</td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>84365</td>
			<td>10.000</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>IF_REST_ERROR_RESPONSE</td>
			<td>82585</td>
			<td>10.000</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_906</td>
			<td>50117</td>
			<td>10.005</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_905</td>
			<td>49705</td>
			<td>10.006</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_907</td>
			<td>49480</td>
			<td>10.006</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_915</td>
			<td>49469</td>
			<td>10.004</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_916</td>
			<td>49451</td>
			<td>10.002</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_902</td>
			<td>49201</td>
			<td>10.003</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_914</td>
			<td>48968</td>
			<td>10.006</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_917</td>
			<td>48925</td>
			<td>10.005</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_904</td>
			<td>48921</td>
			<td>10.006</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_908</td>
			<td>48903</td>
			<td>10.004</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_901</td>
			<td>48804</td>
			<td>10.002</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_919</td>
			<td>48795</td>
			<td>10.009</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_909</td>
			<td>48684</td>
			<td>10.007</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_911</td>
			<td>48574</td>
			<td>10.006</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_910</td>
			<td>48570</td>
			<td>10.006</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_912</td>
			<td>48562</td>
			<td>10.007</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_918</td>
			<td>48515</td>
			<td>10.009</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>IF_DB2DB_903</td>
			<td>47951</td>
			<td>10.004</td>
			<td>0.0</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

10초 주기의 반복 실행과 최근 기간의 짧은 간격 실행이 섞여 있습니다. 스케줄러·부하 시험의 영향일 가능성을 확인해야 합니다. 긴 간격에는 수집되지 않은 날짜가 포함될 수 있고 0초 간격은 정상 동시 실행일 수도 있습니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>온라인에서는 같은 업무의 직전 관측 간격과 최근 시간창 발생률을 계산합니다. 학습 업무별 기준과 비교하고, 수집 중단·계획된 비실행 시간을 구분하는 관측 상태 피처를 추가합니다. 현재 기준 모델에는 도착 간격이 입력되지 않았으므로 별도 실험으로 효과를 검증합니다.
</callout>

---

## 9. 분포 변화와 상관관계

**핵심 결과**

첫 기간 대비 두 번째 기간의 PSI는 처리시간 **10.0313**, 선언 Process 수 **10.1722**입니다. 첫 기간에 없던 업무 ID를 가진 행은 **99.974% / 99.879%**입니다. 균형 표본의 처리시간–선언 단계 수 Spearman 상관은 **0.360**입니다.

<image src="file-upload://3f125113-b9ae-8166-b96b-00b255eae06d"></image>

*출처: `transaction_analysis.ipynb` · 셀 39 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ② 26.08–09 / 선언 Process 수: PSI 10.172.
- ② 26.08–09 / 처리시간 (ms): PSI 10.031.
- ③ 26.09.30–10.02 / 선언 Process 수: PSI 8.033.
- ② 26.08–09 / interfaceId: 기준 기간에 없던 값을 가진 로그 99.97%.

<details>
<summary>원본 수치표 펼치기 · 셀 39 · 2개</summary>
	**원본 표 1 · 셀 39**

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
			<td>1760994</td>
			<td>1760994</td>
			<td>12</td>
			<td>0.0000</td>
			<td>5458.0</td>
			<td>10952.00</td>
			<td>0.3993</td>
		</tr>
		<tr>
			<td>1</td>
			<td>duration_ms</td>
			<td>26년8월15일-26년9월14일</td>
			<td>1760994</td>
			<td>213381</td>
			<td>12</td>
			<td>10.0313</td>
			<td>58.0</td>
			<td>398.20</td>
			<td>0.0000</td>
		</tr>
		<tr>
			<td>2</td>
			<td>duration_ms</td>
			<td>26년9월30일-26년10월2일</td>
			<td>1760994</td>
			<td>27268</td>
			<td>12</td>
			<td>2.8529</td>
			<td>5984.0</td>
			<td>52072.97</td>
			<td>0.0000</td>
		</tr>
		<tr>
			<td>3</td>
			<td>processCount</td>
			<td>25년12월24일-26년4월6일</td>
			<td>1768051</td>
			<td>1768051</td>
			<td>4</td>
			<td>0.0000</td>
			<td>2.0</td>
			<td>3.00</td>
			<td>0.0002</td>
		</tr>
		<tr>
			<td>4</td>
			<td>processCount</td>
			<td>26년8월15일-26년9월14일</td>
			<td>1768051</td>
			<td>213381</td>
			<td>4</td>
			<td>10.1722</td>
			<td>1.0</td>
			<td>1.00</td>
			<td>0.0000</td>
		</tr>
		<tr>
			<td>5</td>
			<td>processCount</td>
			<td>26년9월30일-26년10월2일</td>
			<td>1768051</td>
			<td>27268</td>
			<td>4</td>
			<td>8.0334</td>
			<td>1.0</td>
			<td>1.00</td>
			<td>0.0000</td>
		</tr>
		<tr>
			<td>6</td>
			<td>retryCount</td>
			<td>25년12월24일-26년4월6일</td>
			<td>1768054</td>
			<td>1768054</td>
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
			<td>1768054</td>
			<td>213381</td>
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
			<td>1768054</td>
			<td>27268</td>
			<td>3</td>
			<td>0.0002</td>
			<td>0.0</td>
			<td>0.00</td>
			<td>0.0000</td>
		</tr>
	</table>

	**원본 표 2 · 셀 39**

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
			<td>interfaceId</td>
			<td>25년12월24일-26년4월6일</td>
			<td>365</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>1</td>
			<td>interfaceId</td>
			<td>26년8월15일-26년9월14일</td>
			<td>47</td>
			<td>31</td>
			<td>99.974</td>
		</tr>
		<tr>
			<td>2</td>
			<td>interfaceId</td>
			<td>26년9월30일-26년10월2일</td>
			<td>12</td>
			<td>10</td>
			<td>99.879</td>
		</tr>
		<tr>
			<td>3</td>
			<td>route_key</td>
			<td>25년12월24일-26년4월6일</td>
			<td>19</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>4</td>
			<td>route_key</td>
			<td>26년8월15일-26년9월14일</td>
			<td>8</td>
			<td>3</td>
			<td>0.003</td>
		</tr>
		<tr>
			<td>5</td>
			<td>route_key</td>
			<td>26년9월30일-26년10월2일</td>
			<td>4</td>
			<td>1</td>
			<td>0.007</td>
		</tr>
		<tr>
			<td>6</td>
			<td>category_top</td>
			<td>25년12월24일-26년4월6일</td>
			<td>14</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
		<tr>
			<td>7</td>
			<td>category_top</td>
			<td>26년8월15일-26년9월14일</td>
			<td>4</td>
			<td>1</td>
			<td>38.710</td>
		</tr>
		<tr>
			<td>8</td>
			<td>category_top</td>
			<td>26년9월30일-26년10월2일</td>
			<td>2</td>
			<td>0</td>
			<td>0.000</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-816e-96ac-00b2298be591"></image>

*출처: `transaction_analysis.ipynb` · 셀 41 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- 가장 강한 관계: duration_ms–processCount, ρ=0.360.
- 상수/유효값 부족으로 상관 계산 불가: retryCount.

**이 결과로 알 수 있는 내용**

대부분의 후속 기간 로그가 새로운 업무에 속합니다. 높은 PSI는 분포 차이를 나타내지 장애율을 뜻하지 않습니다. 통합 상관도 기간 간 차이를 반영할 수 있고 인과관계는 아닙니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>공통 모델과 업무군별 모델을 비교하고 신규 업무를 별도 cold-start 경로로 보냅니다. PSI·새 업무 비율을 모델 상태 지표로 감시합니다. 신규 업무 판단은 학습 시점의 범주 사전으로 계산하며, 첫 기간 대비 EDA 기준과 혼동하지 않습니다.
</callout>

---

## 10. Isolation Forest·후보·PCA

**핵심 결과**

15개 피처를 사용하며 학습 구간 **1,616,874행** 중 **100,000행**으로 모델을 학습했습니다. 검증 **280,526행**, 시험 **111,303행**입니다. 임계값은 **0.7340506**이고 시험 후보는 **1,899건(약 1.706%)**입니다. 두 번째 기간 시험 부분은 1,899건, 최근 기간은 0건입니다.

<table fit-page-width="true" header-row="true">
	<tr>
		<td>시험 구간</td>
		<td>시험 행</td>
		<td>후보 행</td>
		<td>후보율</td>
	</tr>
	<tr>
		<td>②의 시험 부분</td>
		<td>84,035</td>
		<td>1,899</td>
		<td>2.2598%</td>
	</tr>
	<tr>
		<td>③</td>
		<td>27,268</td>
		<td>0</td>
		<td>0%</td>
	</tr>
	<tr>
		<td>전체 시험</td>
		<td>111,303</td>
		<td>1,899</td>
		<td>약 1.706%</td>
	</tr>
</table>

**원본 출력의 확인 수치**

- 학습 전체 1,616,874건, 실제 모델 학습 표본 100,000건; 검증 280,526건, 시험 111,303건.

<details>
<summary>원본 수치표 펼치기 · 셀 43 · 3개</summary>
	**원본 표 1 · 셀 43**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>값</td>
		</tr>
		<tr>
			<td>training_rows</td>
			<td>1.616874e+06</td>
		</tr>
		<tr>
			<td>training_sample_rows</td>
			<td>1.000000e+05</td>
		</tr>
		<tr>
			<td>validation_rows</td>
			<td>2.805260e+05</td>
		</tr>
		<tr>
			<td>test_rows</td>
			<td>1.113030e+05</td>
		</tr>
		<tr>
			<td>feature_count</td>
			<td>1.500000e+01</td>
		</tr>
		<tr>
			<td>validation_quantile</td>
			<td>9.900000e-01</td>
		</tr>
		<tr>
			<td>threshold</td>
			<td>7.340506e-01</td>
		</tr>
	</table>

	**원본 표 2 · 셀 43**

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
			<td>train</td>
			<td>1616874</td>
			<td>1616874</td>
			<td>0.4749</td>
			<td>469</td>
			<td>0.0290</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>validation</td>
			<td>151180</td>
			<td>151180</td>
			<td>0.5346</td>
			<td>2</td>
			<td>0.0013</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>test</td>
			<td>84035</td>
			<td>84035</td>
			<td>0.7145</td>
			<td>1899</td>
			<td>2.2598</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>validation</td>
			<td>129346</td>
			<td>129346</td>
			<td>0.7217</td>
			<td>3190</td>
			<td>2.4663</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>test</td>
			<td>27268</td>
			<td>27268</td>
			<td>0.6859</td>
			<td>0</td>
			<td>0.0000</td>
		</tr>
	</table>

	**원본 표 3 · 셀 43**

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
			<td>train</td>
			<td>1616874</td>
			<td>2025-12-24 10:25:31.632</td>
			<td>2026-03-20 17:04:21.488</td>
		</tr>
		<tr>
			<td>25년12월24일-26년4월6일</td>
			<td>validation</td>
			<td>151180</td>
			<td>2026-03-23 08:12:59.258</td>
			<td>2026-04-06 08:51:33.002</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>test</td>
			<td>84035</td>
			<td>2026-08-20 00:00:00.284</td>
			<td>2026-09-14 14:19:58.603</td>
		</tr>
		<tr>
			<td>26년8월15일-26년9월14일</td>
			<td>validation</td>
			<td>129346</td>
			<td>2026-08-15 00:00:00.257</td>
			<td>2026-08-19 23:59:55.035</td>
		</tr>
		<tr>
			<td>26년9월30일-26년10월2일</td>
			<td>test</td>
			<td>27268</td>
			<td>2026-09-30 09:49:26.483</td>
			<td>2026-10-02 15:36:59.444</td>
		</tr>
	</table>
</details>

<details>
<summary>원본 수치표 펼치기 · 셀 45 · 1개</summary>
	**원본 표 1 · 셀 45**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td></td>
			<td>source_period</td>
			<td>transactionId</td>
			<td>startTime</td>
			<td>interfaceId</td>
			<td>status</td>
			<td>responseCode</td>
			<td>duration_ms</td>
			<td>processCount</td>
			<td>retryCount</td>
			<td>context_z</td>
			<td>anomaly_score</td>
		</tr>
		<tr>
			<td>1829703</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822043040040-02</td>
			<td>2026-08-22 04:30:40.043</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>83.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.943575</td>
			<td>0.737476</td>
		</tr>
		<tr>
			<td>1829299</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822032320033-02</td>
			<td>2026-08-22 03:23:20.038</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>84.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.992958</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829392</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822033850025-02</td>
			<td>2026-08-22 03:38:50.030</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>85.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>1.041764</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829505</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822035740033-01</td>
			<td>2026-08-22 03:57:40.037</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>82.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.893601</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829541</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822040340029-01</td>
			<td>2026-08-22 04:03:40.033</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>76.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.580492</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829543</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822040400036-01</td>
			<td>2026-08-22 04:04:00.041</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>69.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.182780</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829562</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822040710032-01</td>
			<td>2026-08-22 04:07:10.036</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>81.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.843020</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829579</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822041000029-01</td>
			<td>2026-08-22 04:10:00.033</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>69.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.182780</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829660</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822042330033-01</td>
			<td>2026-08-22 04:23:30.929</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>75.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.525945</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829679</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822042640024-01</td>
			<td>2026-08-22 04:26:40.028</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>69.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.182780</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829705</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822043100028-01</td>
			<td>2026-08-22 04:31:00.032</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>70.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.241970</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829708</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822043130032-02</td>
			<td>2026-08-22 04:31:30.038</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>69.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.182780</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829720</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822043330030-01</td>
			<td>2026-08-22 04:33:30.034</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>69.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.182780</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829734</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822043550024-02</td>
			<td>2026-08-22 04:35:50.028</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>71.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.300332</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829748</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822043810038-01</td>
			<td>2026-08-22 04:38:10.042</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>72.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.357889</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829752</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822043850043-01</td>
			<td>2026-08-22 04:38:50.046</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>69.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.182780</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829762</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822044030030-01</td>
			<td>2026-08-22 04:40:30.035</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>70.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.241970</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829765</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822044100023-01</td>
			<td>2026-08-22 04:41:00.028</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>72.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.357889</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829766</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822044110025-01</td>
			<td>2026-08-22 04:41:10.029</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>74.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.470675</td>
			<td>0.736790</td>
		</tr>
		<tr>
			<td>1829771</td>
			<td>26년8월15일-26년9월14일</td>
			<td>TR-IF_CLAUDE_CREATE_001-20260822044200026-01</td>
			<td>2026-08-22 04:42:00.031</td>
			<td>IF_CLAUDE_CREATE_001</td>
			<td>F</td>
			<td>4311</td>
			<td>71.0</td>
			<td>1.0</td>
			<td>0.0</td>
			<td>0.300332</td>
			<td>0.736790</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-8196-b5c3-00b22037748d"></image>

*출처: `transaction_analysis.ipynb` · 셀 46 · 그림 1. 노트북에 저장된 원본 출력입니다.*

<image src="file-upload://3f125113-b9ae-814e-a7b4-00b2d1d2107c"></image>

*출처: `transaction_analysis.ipynb` · 셀 46 · 그림 2. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- ② 26.08–09 / 시험 구간 모델 검토 후보: 1,899 / 84,035건 (2.260%).
- ③ 26.09.30–10.02 / 시험 구간 모델 검토 후보: 0 / 27,268건 (0.000%).

**원본 출력의 확인 수치**

- 시험 전체 111,303건 중 후보 1,899건 (1.71%). 임계값 0.7341; 검증 점수의 99% 분위수입니다.

**원본 출력의 확인 수치**

- 위 표의 각 행은 후보 또는 비후보 집단 내부의 상태/정합성 비율입니다. 두 집단의 건수는 서로 다릅니다.

<details>
<summary>원본 수치표 펼치기 · 셀 48 · 2개</summary>
	**원본 표 1 · 셀 48**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td>status</td>
			<td>F</td>
			<td>H</td>
			<td>S</td>
			<td>Z</td>
		</tr>
		<tr>
			<td>anomaly_candidate</td>
			<td></td>
			<td></td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>False</td>
			<td>66.35</td>
			<td>0.0</td>
			<td>33.61</td>
			<td>0.04</td>
		</tr>
		<tr>
			<td>True</td>
			<td>100.00</td>
			<td>0.0</td>
			<td>0.00</td>
			<td>0.00</td>
		</tr>
	</table>

	**원본 표 2 · 셀 48**

	<table fit-page-width="true" header-row="true">
		<tr>
			<td>integrity_candidate</td>
			<td>False</td>
			<td>True</td>
		</tr>
		<tr>
			<td>anomaly_candidate</td>
			<td></td>
			<td></td>
		</tr>
		<tr>
			<td>False</td>
			<td>100.0</td>
			<td>0.0</td>
		</tr>
		<tr>
			<td>True</td>
			<td>100.0</td>
			<td>0.0</td>
		</tr>
	</table>
</details>

<image src="file-upload://3f125113-b9ae-81db-b40a-00b20153ffed"></image>

*출처: `transaction_analysis.ipynb` · 셀 50 · 그림 1. 노트북에 저장된 원본 출력입니다.*

**원본 출력의 확인 수치**

- 각 축의 표본 0.5–99.5% 범위를 확대했습니다. 화면 밖 597건 중 후보 0건은 모델 계산에서 제외하지 않았습니다. 전체 투영 표본 후보 206건.

<details>
<summary>실제 모델 입력 피처 15개 · 셀 50</summary>
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
			<td>log_processCount</td>
		</tr>
		<tr>
			<td>4</td>
			<td>missing_processCount</td>
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
			<td>rarity_interfaceId</td>
		</tr>
		<tr>
			<td>11</td>
			<td>unseen_interfaceId</td>
		</tr>
		<tr>
			<td>12</td>
			<td>rarity_route_key</td>
		</tr>
		<tr>
			<td>13</td>
			<td>unseen_route_key</td>
		</tr>
		<tr>
			<td>14</td>
			<td>rarity_interfaceType</td>
		</tr>
		<tr>
			<td>15</td>
			<td>unseen_interfaceType</td>
		</tr>
	</table>
</details>

**이 결과로 알 수 있는 내용**

후보는 모두 실패 상태지만 이것을 precision=100%로 해석할 수 없습니다. 비후보에도 실패가 66.35% 존재합니다. 최근 기간 0후보 역시 실제 이상이 없음을 보장하지 않습니다. PCA는 학습·검증·시험에서 추출한 균형 표본의 2차원 요약이며 시험 후보 수와 그림 후보 수는 다릅니다.

<callout icon="💡" color="blue_bg">
	**모델 구현 연결**<br>점수 방향은 높을수록 드문 패턴입니다. 시간 분리·거래 단위 그룹·학습 전용 전처리를 유지합니다. 검증 99% 분위수는 경보량 설정이며 99% 정확도가 아닙니다. 실제 라벨로 기간·업무별 precision/recall, 고유 거래 경보 수, 탐지 지연을 평가하고 다른 모델과 비교합니다.
</callout>

---

## 부록. 추가 원본 텍스트 출력

<details>
<summary>Series 집계·실행 규모 등 원본 값 펼치기</summary>
	**원본 텍스트 출력 · 셀 4**
	```text
	원본 합계: 2,008,703건
	분석 합계: 2,008,703건 / 16개 컬럼
	메모리 사용량: 262.4 MiB
	```

	**원본 텍스트 출력 · 셀 11**
	```text
	음수 처리시간: 0건
	종료시각 없음: 7,060건
	```

	**원본 텍스트 출력 · 셀 35**
	```text
	상태·코드 비교 가능: 2,001,281건
	```
</details>

"""Emit apply_patch patches for the readable three-period analysis notebooks.

This maintenance script prints a patch; it never overwrites notebooks itself.
Usage: python code/build_analysis_notebooks.py transaction_analysis.ipynb
"""

import json
from pathlib import Path
import sys
import textwrap


def markdown(source):
    return {"cell_type": "markdown", "metadata": {}, "source": textwrap.dedent(source).strip().splitlines(keepends=True)}


def code(source):
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [],
            "source": (textwrap.dedent(source).strip() + "\n").splitlines(keepends=True)}


IMPORTS = code('''
    from pathlib import Path
    import sys
    import numpy as np
    import pandas as pd
    import matplotlib.pyplot as plt
    import seaborn as sns
    from IPython.display import display

    ROOT = next(p for p in [Path.cwd(), *Path.cwd().parents]
                if (p / "code" / "log_analysis_common.py").exists() and (p / "data").is_dir())
    if str(ROOT / "code") not in sys.path:
        sys.path.insert(0, str(ROOT / "code"))
    from log_analysis_common import (
        PERIODS, load_logs, text, quality_table, numeric_summary, add_time_features,
        sample_by_period, time_summary, hourly_history_candidates, iqr_summary,
        contextual_duration, numeric_drift, categorical_drift, dependency_features,
        latest_transaction_state, transaction_process_summary, fit_anomaly_baseline,
        baseline_report, signed_log, setup_plotting,
    )
    from log_analysis_visuals import (
        plot_sampling, plot_missingness, plot_numeric_distributions, plot_categories,
        plot_period_timeseries, plot_hour_pattern, plot_traffic_candidates,
        plot_statistical_candidates, plot_drift, plot_correlation, plot_model_scores,
        plot_projection, plot_rule_rates, plot_dependency, plot_merge_counts,
        plot_merge_status, plot_merge_performance, plot_sequences, note,
    )

    DATA_DIR = ROOT / "data"
    RANDOM_STATE = 42
    PLOT_SAMPLE = 30_000
    MAX_TRAIN_ROWS = 100_000
    SCORE_BATCH_ROWS = 100_000
    ALERT_QUANTILE = 0.99
    setup_plotting()
''')


SCOPE = '''
    연구 주제: **대규모 로그 데이터 기반 비지도 학습 이상탐지 AI 모델 개발**

    다음 세 기간의 실제 로그를 합쳐 분석합니다. 각 행에 `source_period`, `source_file`을
    남겨 통합 분포와 기간별 차이를 함께 확인합니다.

    | 기간 폴더 | 수집 범위 |
    |---|---|
    | `25년12월24일-26년4월6일` | 2025-12-24 ~ 2026-04-06 |
    | `26년8월15일-26년9월14일` | 2026-08-15 ~ 2026-09-14 |
    | `26년9월30일-26년10월2일` | 2026-09-30 ~ 2026-10-02 |

    파일명과 확장자가 실제 내용과 다른 최근 기간은 **컬럼 구조**로 소스를 선택합니다.
    트랜잭션은 `EC_MESSAGE_BODY.csv` 안의 SQL 트랜잭션 테이블,
    프로세스는 `EC_TRANSACTION_MONITORING.csv` 안의 프로세스 CSV를 사용합니다.
    메시지 로그인 `EC_PROCESS_MONITORING.csv`는 프로세스 분석에 섞지 않습니다.

    시간대가 있는 시각은 한국 시간으로 변환하고, 시간대 없는 원본 시각은 한국 시간으로
    해석합니다. 기간 사이의 공백과 관측되지 않은 시간 구간은 수집 여부를 알 수 없으므로
    트래픽 0으로 채우지 않습니다. 통계적 이상치, 실패 상태, 정합성 위반은 검토 후보이며
    실제 이상 여부를 확정하는 정답 라벨이 아닙니다.

    실행 환경: Python 3.10+, pandas 2+, numpy, matplotlib, seaborn, scikit-learn 1.2+.
    세 노트북과 `log_analysis_common.py`, `log_analysis_visuals.py`를 같은 `code/` 폴더에 둡니다.
    전체 통계는 원본 전체를 사용하고, 그래프·PCA·모델 학습 표본만 크기를 제한합니다.
    분석 표와 그래프는 노트북에서 확인하며 별도 결과 파일은 생성하지 않습니다.

    ### 그래프 읽는 순서

    ① 파란색 = 2025.12–2026.04, ② 초록색 = 2026.08–09,
    ③ 주황색 = 2026.09.30–10.02입니다. 빨간색은 점검 후보·비교 기준선입니다.
    각 그림 아래 **실제 결과 → 의미 → 모델 개발에 반영**을 함께 읽습니다.
    **전체 데이터**는 발생률·통계 계산, **균형 표본**은 분포·산점도 비교에 사용합니다.
    기간별 표본 추출률은 첫 그래프에 명시합니다. 그림에서 일부 범위를 확대할 경우
    화면 밖 행 수를 표시하며 모델 계산에서는 제외하지 않습니다.
    실행한 그래프와 해석은 노트북 출력에 포함해 파일을 열면 바로 확인할 수 있습니다.
'''


def load_cells(kind):
    return [
        markdown('''
            ## 1. 환경 설정과 세 기간 통합 로드

            공통 로더는 대문자/소문자 컬럼명, CSV/SQL 형식, 결측치 표기를 통일합니다.
            `responseCode`는 문자열로 유지하므로 성공 코드 `0000`의 앞자리 0이 사라지지 않습니다.
            중복 내보내기 파일은 하나만 선택하고 분석 컬럼이 모두 같은 행만 제거합니다.
            재시도 및 같은 키의 서로 다른 로그는 유지하고 감사 표에 집계합니다.
        '''),
        IMPORTS,
        code(f'''
            df, source_audit = load_logs("{kind}", DATA_DIR)
            display(source_audit)
            print(f"원본 합계: {{source_audit['raw_rows'].sum():,}}건")
            print(f"분석 합계: {{len(df):,}}건 / {{df.shape[1]}}개 컬럼")
            print(f"메모리 사용량: {{df.memory_usage(deep=True).sum() / 1024**2:,.1f}} MiB")
            display(df.head())
        '''),
        markdown('''
            ## 2. 스키마·결측·중복·관측 범위

            파싱 실패, 중복 키, 결측 시간과 기간별 결측률을 먼저 확인합니다.
            `observed_days`는 실제 로그가 존재하는 날짜 수로, 폴더명의 일수와 다를 수 있습니다.
            짧은 구간의 부족한 로그나 누락 컬럼을 장애로 바로 해석하지 않습니다.
        '''),
        code('''
            display(quality_table(df))
            missing_by_period = df.isna().groupby(df["source_period"], observed=True).mean().mul(100)
            display(missing_by_period.round(2))
            period_volume = source_audit.set_index("source_period")[["raw_rows", "rows", "observed_days"]].copy()
            period_volume["share_pct"] = period_volume["rows"] / len(df) * 100
            period_volume["rows_per_observed_day"] = period_volume["rows"] / period_volume["observed_days"]
            display(period_volume.round(2))
        '''),
        markdown('''
            ### 2.1 전체 규모와 균형 표본은 다릅니다

            왼쪽은 전체 행 수, 오른쪽은 원본과 시각화 표본의 기간 구성입니다.
            표본은 기간별 최대 1만 행을 고정 시드로 추출합니다.
            실제 장애 발생률이나 결측률은 이 표본이 아닌 전체 데이터에서 계산합니다.
        '''),
        code('''
            plot_df = sample_by_period(df, PLOT_SAMPLE, RANDOM_STATE)
            sample_audit = plot_sampling(df, plot_df)
            plot_missingness(df, ["startTime", "endTime", "status", "responseCode", "retryCount"])
        '''),
    ]


def numeric_cells(columns):
    return [
        markdown('''
            ## 3. 처리시간과 수치 분포

            처리시간은 `endTime - startTime`의 밀리초 값입니다. 음수·0·결측과
            p95/p99/p99.9를 확인해 긴 꼬리와 불완전한 실행 로그를 구분합니다.
            음수는 검증을 위해 유지합니다. 시간 분포는 실제 단위의 로그 눈금으로 읽고,
            이산 카운트 값은 전체 행의 100% 구성 막대로 비교합니다.
            모든 기간은 같은 히스토그램 구간과 세로축을 사용합니다.
        '''),
        code(f'''
            add_time_features(df)
            NUMERIC_COLS = {columns!r}
            display(numeric_summary(df, NUMERIC_COLS))
            print(f"음수 처리시간: {{df['negative_duration'].sum():,}}건")
            print(f"종료시각 없음: {{df['missing_end'].sum():,}}건")
        '''),
        code('''
            # duration_ms 등 파생 컬럼을 포함해 동일 시드로 표본을 다시 구성합니다.
            plot_df = sample_by_period(df, PLOT_SAMPLE, RANDOM_STATE)
        '''),
        *[cell for col in columns if col != "processTime" for cell in [
            markdown(f"### 수치 분포: `{col}`\n\n기간별 분포를 같은 축으로 비교하고 바로 아래 결과 해석을 읽습니다."),
            code(f"plot_numeric_distributions(df, plot_df, [{col!r}])"),
        ]],
        markdown('''
            ### 3.1 가장 긴 실행은 별도 확인

            긴 실행이 실제 지연인지, 배치 업무의 정상 실행인지 상태와 업무 종류를 확인합니다.
            아래 표는 전체 데이터의 상위 실행이며, 표본 히스토그램에 없을 수도 있습니다.
        '''),
        code('''
            display(df.nlargest(10, "duration_ms")[["source_period", "transactionId", "startTime", "status", "duration_ms"]])
            note(f"전체 유효 처리시간 최대 {df['duration_ms'].max():,.0f}ms; "
                 f"음수 {df['negative_duration'].sum():,}건, 종료시각 결측 {df['missing_end'].sum():,}건.",
                 "상위 실행만으로 정상/이상을 확정하지 않습니다. 완료 실행의 긴 꼬리와 종료 로그가 없는 실행은 다른 탐지 대상입니다.",
                 "상위 실행을 업무별 분포와 비교하고, 미완료 로그는 별도의 경과시간 기반 감시를 설계합니다.")
        '''),
    ]


def time_cells():
    return [
        markdown('''
            ## 4. 일별·시간별 트래픽과 처리시간 추이

            기간별 그래프를 분리해 수집 공백을 가로질러 선을 잇지 않습니다.
            집계는 전체 로그를 사용합니다. 실패율의 분모는 상태가 알려진 로그 수이며,
            실패 상태 비율은 참고 지표입니다.
        '''),
        code('''
            daily = time_summary(df, "D")
            hourly = time_summary(df, "h")
            display(daily.groupby("source_period", observed=True).agg(
                observed_days=("bin", "size"), rows=("rows", "sum"),
                median_daily_rows=("rows", "median"), max_daily_rows=("rows", "max")))
            plot_period_timeseries(daily, "rows", "일별 로그 건수")
        '''),
        code('''
            plot_period_timeseries(daily, "p95_ms", "일별 p95 처리시간 (ms)")
            plot_period_timeseries(daily, "failure_pct", "일별 실패 상태 비율 (%)")
            monthly = daily.assign(month=daily["bin"].dt.to_period("M").astype(str)).groupby(
                ["source_period", "month"], observed=True).agg(rows=("rows", "sum"), observed_days=("bin", "size"))
            monthly["rows_per_observed_day"] = monthly["rows"] / monthly["observed_days"]
            display(monthly.round(2))
        '''),
        markdown('''
            ### 4.1 요일·시간대 패턴

            요일별 관측 날짜 수로 정규화한 시간대별 로그량을 봅니다. 아래 0은 관측된 날짜의
            해당 시간대에서 발견된 행이 없다는 뜻이며, 수집이 완전했다는 보장은 아닙니다.
            야간·주말이라는 이유만으로 이상으로 판정하지 않습니다.
        '''),
        code('''
            plot_hour_pattern(df)
        '''),
        markdown('''
            ### 4.2 과거 24시간 기준 트래픽 급증·급감 후보

            현재 시각을 제외한 과거 24시간의 중앙값/MAD를 기준으로 합니다.
            기간별 기준선을 다시 시작하고 빈 시간 구간은 결측으로 둡니다.
            이력 6개 미만 또는 MAD=0이면 점수를 산출하지 않습니다.
            이는 점검용 규칙이며 계절성 모델이나 확정된 이상 라벨이 아닙니다.
        '''),
        code('''
            traffic_candidates = hourly_history_candidates(hourly)
            display(traffic_candidates.groupby("source_period", observed=True).agg(
                bins=("bin", "size"), observed_bins=("rows", "count"),
                comparable_bins=("reference_available", "sum"), candidate_bins=("candidate", "sum")))
            display(traffic_candidates.loc[traffic_candidates["candidate"]].assign(
                absolute_z=lambda x: x["volume_robust_z"].abs()).nlargest(15, "absolute_z"))
            plot_traffic_candidates(traffic_candidates)
        '''),
    ]


def statistical_cells(entity, columns):
    return [
        markdown(f'''
            ## 6. 전체 IQR와 업무 그룹별 처리시간 기준선

            전체 IQR은 여러 업무·기간의 분포를 섞으므로 참고용입니다.
            `source_period + {entity}`별 로그 처리시간의 중앙값/MAD 기준을 함께 봅니다.
            유효 처리시간 30건 이상, MAD>0인 그룹에서만 상대적인 지연 후보를 계산합니다.
            MAD=0/표본 부족은 판정 불가로 남깁니다. 이 전체 기간 EDA 기준선은
            뒤의 시간 분리 모델 입력에 사용하지 않아 미래 정보 누출을 방지합니다.
        '''),
        code(f'''
            iqr_table, iqr_flags = iqr_summary(df, {columns!r})
            df["iqr_candidate"] = iqr_flags.any(axis=1)
            display(iqr_table.round(3))
            context_profile, context_result = contextual_duration(df, "{entity}")
            df[context_result.columns] = context_result
            display(df.groupby("source_period", observed=True).agg(
                rows=("transactionId", "size"), iqr_candidates=("iqr_candidate", "sum"),
                context_comparable=("context_comparable", "sum"), context_candidates=("context_candidate", "sum")))
            display(context_profile.sort_values("support", ascending=False).head(15))
        '''),
        code('''
            plot_statistical_candidates(df, iqr_table)
        '''),
        code(f'''
            context_by_entity = df.groupby(["source_period", "{entity}"], observed=True).agg(
                rows=("transactionId", "size"), comparable=("context_comparable", "sum"), candidates=("context_candidate", "sum"))
            context_by_entity["candidate_pct_of_comparable"] = context_by_entity["candidates"].div(
                context_by_entity["comparable"].replace(0, np.nan)).mul(100)
            display(context_by_entity.loc[context_by_entity["comparable"].ge(30)].sort_values(
                "candidate_pct_of_comparable", ascending=False).head(20))
            note("업무별 후보 비율의 분모는 전체 업무 행 수가 아니라 유효 처리시간·충분한 표본·MAD>0을 충족하는 비교 가능 행 수입니다.",
                 "높은 비율은 지연 외에 업무 내부의 여러 실행 모드나 작은 MAD 때문에 발생할 수 있습니다.",
                 "후보 비율 상위 업무를 먼저 검토하되 표본 수와 정상 실행 정의를 함께 확인합니다.")
        '''),
    ]


def drift_cells(numeric, categorical):
    return [
        markdown('''
            ## 9. 기간별 분포 변화와 새로운 범주

            첫 기간을 기준으로 수치형 PSI와 새로운 업무·경로 비율을 비교합니다.
            PSI는 기준 기간의 고정 분위수 구간을 사용하며 0에 가까울수록 분포가 유사합니다.
            기준 기간의 범위를 벗어난 값과 상수였던 변수의 새 값도 별도 구간에 집계합니다.
            구간 수와 관측 행 수도 함께 확인하며 분위수와 결측률을 같이 비교합니다.
            기간 차이는 운영 환경·업무·수집 범위 변화일 수 있어 이상으로 바로 확정하지 않습니다.
        '''),
        code(f'''
            drift_numeric = numeric_drift(df, {numeric!r})
            drift_categories = categorical_drift(df, {categorical!r})
            display(drift_numeric.round(4))
            display(drift_categories.round(3))
            plot_drift(drift_numeric, drift_categories)
        '''),
        markdown('''
            ### 9.1 수치 피처 간 중복 정보

            긴 꼬리에 강한 Spearman 상관계수를 기간별 균형 표본에서 확인합니다.
            강한 상관관계나 전부 결측·상수인 피처는 모델 입력을 검토할 때 참고합니다.
        '''),
        code(f'''
            corr_sample = sample_by_period(df, PLOT_SAMPLE, RANDOM_STATE)
            plot_correlation(corr_sample, {numeric!r})
        '''),
    ]


def model_cells(numeric, categorical, candidate_columns, diagnostic):
    return [
        markdown('''
            ## 10. 시간 순서 검증을 갖춘 비지도 기준 모델

            Isolation Forest에 처리시간·로그 건수·재시도·순환 시간 피처·업무 범주 빈도를
            입력합니다. 거래 ID와 데이터 기간은 입력에서 제외하고, `status`, `responseCode`,
            성공/오류 건수와 정합성 후보는 사후 해석에만 사용합니다.

            관측 날짜 순서로 약 60%/20%/20%를 학습/검증/시험으로 나눕니다.
            같은 거래의 로그는 같은 분할에 두고, 분할 경계를 넘는 거래는 평가에서 제외합니다.
            결측 대치·스케일링·범주 빈도는 학습 구간에서만 정합니다.
            전체 학습 구간의 범주 빈도를 사용하되 모델 학습은 최대 10만 행,
            점수 산출은 10만 행씩 처리해 메모리를 제한합니다.

            임계값은 검증 점수의 99% 분위수입니다. 이는 검토량을 정하는 운영 설정이며
            실제 이상 비율 추정치가 아닙니다. 시험 데이터에는 고정한 임계값을 적용합니다.
            정답 라벨이 없으므로 accuracy/F1/precision으로 성능을 주장하지 않습니다.
        '''),
        code(f'''
            MODEL_NUMERIC = {numeric!r}
            MODEL_CATEGORICAL = {categorical!r}
            model_result, model_profile, fitted = fit_anomaly_baseline(
                df, MODEL_NUMERIC, MODEL_CATEGORICAL, max_train=MAX_TRAIN_ROWS,
                batch_size=SCORE_BATCH_ROWS, alert_quantile=ALERT_QUANTILE, random_state=RANDOM_STATE)
            display(pd.DataFrame([{{k: v for k, v in model_profile.items() if k != "features"}}]).T.rename(columns={{0: "값"}}))
            display(baseline_report(df, model_result))
            split_times = df[["source_period", "startTime"]].assign(split=model_result["split"]).groupby(
                ["source_period", "split"], observed=True).agg(rows=("startTime", "size"), first=("startTime", "min"), last=("startTime", "max"))
            display(split_times)
            note(f"학습 전체 {{model_profile['training_rows']:,}}건, 실제 모델 학습 표본 {{model_profile['training_sample_rows']:,}}건; "
                 f"검증 {{model_profile['validation_rows']:,}}건, 시험 {{model_profile['test_rows']:,}}건.",
                 "날짜 단위 약 60/20/20 분할이므로 행 수의 비율이 60/20/20일 필요는 없습니다. 학습 기간에 없던 업무 구성은 시험 시 분포 변화로 나타날 수 있습니다.",
                 "시간 분리와 거래 단위 그룹을 유지하고, 기준 모델의 경보량을 업무별 모델과 비교합니다.")
        '''),
        markdown('''
            ### 10.1 시험 구간의 점수와 검토 후보

            후보 비율과 상위 점수를 기간별로 확인합니다. 새로운 기간의 대량 경보는
            실제 장애뿐 아니라 분포 변화나 로그 수집 차이 때문일 수 있습니다.
            상태·정합성 규칙과의 교차표는 해석을 돕는 참고값으로만 봅니다.
        '''),
        code(f'''
            top_index = model_result.loc[model_result["split"].eq("test") & model_result["anomaly_candidate"]].nlargest(20, "anomaly_score").index
            display(df.loc[top_index, {candidate_columns!r}].join(model_result[["anomaly_score"]]))
        '''),
        code('''
            plot_model_scores(df, model_result, model_profile)
        '''),
        markdown('''
            ### 10.2 후보의 상태·정합성은 사후 해석입니다

            후보/비후보 각각을 100%로 맞춘 교차표입니다. `status=F`를 정답으로 둔
            학습·평가가 아니므로 실패 상태가 많은 것을 precision으로 해석하지 않습니다.
        '''),
        code(f'''
            test_view = df.loc[model_result["split"].eq("test"), ["status", {diagnostic!r}]].join(model_result[["anomaly_candidate"]])
            display(pd.crosstab(test_view["anomaly_candidate"], text(test_view["status"]), normalize="index").mul(100).round(2))
            display(pd.crosstab(test_view["anomaly_candidate"], test_view[{diagnostic!r}], normalize="index").mul(100).round(2))
            note("위 표의 각 행은 후보 또는 비후보 집단 내부의 상태/정합성 비율입니다. 두 집단의 건수는 서로 다릅니다.",
                 "후보는 희귀한 행동 패턴입니다. 실패 상태·정합성 후보와 겹칠 수 있지만 같지 않으며, 성공 상태의 후보도 검토 대상입니다.",
                 "상위 점수와 상태·업무·지연 근거를 함께 검토해 실제 이상 라벨을 확보합니다.")
            del test_view
        '''),
        markdown('''
            ### 10.3 PCA로 보는 피처 공간

            PCA는 학습 데이터에만 맞추고 기간별 균형 표본을 투영합니다.
            그림은 구조를 살펴보는 2차원 요약이며 분리되어 보인다는 사실만으로 이상을 확정하지 않습니다.
        '''),
        code('''
            from sklearn.decomposition import PCA

            pca = PCA(n_components=2, random_state=RANDOM_STATE).fit(fitted["X_train"])
            pca_sample = sample_by_period(df.loc[model_result["anomaly_score"].notna()], PLOT_SAMPLE, RANDOM_STATE)
            coords = pca.transform(fitted["transform"](pca_sample))
            projection = pd.DataFrame(coords, columns=["PC1", "PC2"], index=pca_sample.index)
            projection["source_period"] = pca_sample["source_period"]
            projection["candidate"] = model_result.loc[pca_sample.index, "anomaly_candidate"]
            plot_projection(projection, pca.explained_variance_ratio_.sum())
            display(pd.Series(model_profile["features"], name="model_input_feature"))
        '''),
        markdown('''
            ## 11. 모델 개발로 이어지는 해석

            - 통합 분포와 기간별 변화가 크면 업무 그룹 기준선과 운영 환경별 모델을 비교합니다.
            - 처리시간의 긴 꼬리에는 로그 변환을, 시간 주기에는 sin/cos 표현을 사용합니다.
            - 거래 ID는 결합 키이며 모델 입력에서 제외합니다. 업무 ID는 범주 빈도나 별도 표현을 검토합니다.
            - 결측·재시도·수집 범위가 만든 후보와 실제 실행 이상 후보를 사람 검토로 구분합니다.
            - 실제 성능 평가는 검토로 확정된 표본이나 통제된 장애 주입 데이터가 필요합니다.
            - 이후 비교 모델은 LOF/강건 공분산/Autoencoder 및 시계열 예측 잔차 모델로 확장할 수 있습니다.
              모든 대치·변환·기준선·임계값은 학습/검증 단계에서만 결정합니다.
        '''),
    ]


def transaction_notebook():
    cells = [markdown("# Transaction 분석: 세 기간 통합 로그와 비지도 이상탐지\n\n" + textwrap.dedent(SCOPE))]
    cells += load_cells("transaction")
    cells += [markdown('''
        주요 컬럼: `interfaceId`는 업무 종류, 채널/허브는 실행 경로, `processCount`는 선언된
        프로세스 수, `retryCount`는 재시도, `processTime`은 기록된 밀리초 처리시간입니다.
        `status`/`responseCode`는 실행 결과이며, 학습의 정답 라벨로 사용하지 않습니다.
    ''')]
    cells += numeric_cells(["duration_ms", "processTime", "processCount", "retryCount"])
    cells += time_cells()
    cells += [
        markdown('''
            ## 5. 상태·업무·처리 경로 분포

            합계가 큰 기간이 통합 분포를 지배하므로 기간별 상태 비율도 함께 확인합니다.
            새로운 경로는 업무 변경일 수 있으며 빈도가 낮다는 이유만으로 이상으로 보지 않습니다.
        '''),
        code('''
            df["route_key"] = (text(df["startChannelId"]) + " → " + text(df["processHubId"]) + " → " + text(df["endChannelId"])).astype("category")
            df["category_top"] = text(df["categoryName"]).str.split(">").str[0].str.strip().astype("category")
            plot_categories(df, ["status", "responseCode", "interfaceId", "category_top"])
            display(pd.crosstab(df["source_period"], text(df["status"]), normalize="index").mul(100).round(2))
            route_summary = df.groupby(["source_period", "route_key"], observed=True).agg(
                rows=("transactionId", "size"), median_ms=("duration_ms", "median"), p95_ms=("duration_ms", lambda s: s.quantile(.95)))
            display(route_summary.sort_values("rows", ascending=False).head(25))
            note("경로 표는 전체 데이터 기준 로그량 상위 25개이며, p95는 각 경로 유효 처리시간의 95% 분위수입니다.",
                 "로그량이 많은 경로와 느린 경로는 다를 수 있습니다. 기간·업무가 바뀌면 경로의 정상 지연도 달라질 수 있습니다.",
                 "경로/업무별 지연 기준선을 전체 기간 모델과 비교합니다.")
        '''),
    ]
    cells += statistical_cells("interfaceId", ["duration_ms", "processCount", "retryCount"])
    cells += [
        markdown('''
            ## 7. 시간·상태·응답코드 정합성

            기록 처리시간과 계산 처리시간의 차이는 양쪽 값이 있는 행에서만 비교합니다.
            상태/응답코드도 알려진 `S`/`F` 상태와 알려진 코드에서만 비교합니다.
            `0000`을 성공 코드로 가정한 점검 규칙이므로 실제 운영 코드 정의와 대조해야 합니다.
        '''),
        code('''
            df["duration_mismatch_ms"] = (df["duration_ms"] - df["processTime"]).abs()
            df["duration_inconsistent"] = df["duration_mismatch_ms"].gt(1)
            code_comparable = df["status"].isin(["S", "F"]) & df["responseCode"].notna()
            df["status_code_candidate"] = code_comparable & (
                (df["status"].eq("S") & df["responseCode"].ne("0000")) |
                (df["status"].eq("F") & df["responseCode"].eq("0000")))
            df["integrity_candidate"] = df["negative_duration"] | df["duration_inconsistent"] | df["status_code_candidate"]
            display(df.groupby("source_period", observed=True).agg(
                rows=("transactionId", "size"), duration_mismatches=("duration_inconsistent", "sum"),
                status_code_candidates=("status_code_candidate", "sum"), negative_duration=("negative_duration", "sum")))
            print(f"상태·코드 비교 가능: {code_comparable.sum():,}건")
            display(df.loc[df["integrity_candidate"], ["source_period", "transactionId", "status", "responseCode", "duration_ms", "processTime"]].head(15))
            df["status_code_comparable"] = code_comparable
            df["duration_comparable"] = df[["duration_ms", "processTime"]].notna().all(axis=1)
            plot_rule_rates(df, {"duration_inconsistent": "계산/기록 시간 불일치", "status_code_candidate": "상태/응답코드 불일치"},
                            {"duration_inconsistent": "duration_comparable", "status_code_candidate": "status_code_comparable"},
                            "Transaction 정합성")
        '''),
        markdown('''
            ## 8. 업무별 도착 간격과 동시 발생

            같은 기간·업무 안에서 시간 순으로 정렬해 직전 거래와의 간격을 확인합니다.
            기간 사이의 긴 수집 공백을 도착 간격으로 사용하지 않습니다.
            0초 간격은 배치·동시 실행일 수도 있으므로 업무별 정상 패턴과 함께 봅니다.
        '''),
        code('''
            arrivals = df.loc[df["startTime"].notna(), ["source_period", "interfaceId", "startTime"]].sort_values(
                ["source_period", "interfaceId", "startTime"])
            arrivals["interarrival_seconds"] = arrivals.groupby(["source_period", "interfaceId"], observed=True)["startTime"].diff().dt.total_seconds()
            display(arrivals.groupby("source_period", observed=True)["interarrival_seconds"].describe(percentiles=[.01, .5, .95, .99]).round(3))
            display(arrivals.groupby(["source_period", "interfaceId"], observed=True)["interarrival_seconds"].agg(
                intervals="count", median_seconds="median", zero_gap_pct=lambda s: s.dropna().eq(0).mean() * 100).sort_values("intervals", ascending=False).head(20))
            arrivals_plot = sample_by_period(arrivals, PLOT_SAMPLE, RANDOM_STATE)
            plot_numeric_distributions(arrivals, arrivals_plot, ["interarrival_seconds"])
            note(f"0초 도착 간격 {arrivals['interarrival_seconds'].eq(0).sum():,}건. 각 기간·업무의 첫 거래는 직전 간격이 없으므로 결측입니다.",
                 "0초 간격은 배치/동시 실행일 수 있습니다. 긴 간격에는 수집되지 않은 날도 포함될 수 있어 낮은 트래픽을 바로 서비스 중단으로 볼 수 없습니다.",
                 "시간대·업무별 발생률과 수집 상태를 함께 확인한 후 도착 간격 기반 탐지를 설계합니다.")
            del arrivals
        '''),
    ]
    cells += drift_cells(["duration_ms", "processCount", "retryCount"], ["interfaceId", "route_key", "category_top"])
    cells += model_cells(
        ["duration_ms", "processCount", "retryCount"], ["interfaceId", "route_key", "interfaceType"],
        ["source_period", "transactionId", "startTime", "interfaceId", "status", "responseCode", "duration_ms", "processCount", "retryCount", "context_z"],
        "integrity_candidate")
    return cells


def process_notebook():
    cells = [markdown("# Process 분석: 세 기간 통합 로그와 실행 이상 후보\n\n" + textwrap.dedent(SCOPE))]
    cells += load_cells("process")
    cells += [markdown('''
        분석 단위는 프로세스 **실행 로그/재시도 행**입니다. `processId`는 업무 템플릿에서
        반복되는 식별자이므로 단독으로 실행을 식별하지 않습니다.
        실행 키는 `source_period + transactionId + processId + retryCount`입니다.
        `totalCount`/`successCount`/`errorCount`는 프로세스 수가 아니라 처리 데이터 건수입니다.
    ''')]
    cells += numeric_cells(["duration_ms", "totalCount", "successCount", "errorCount", "retryCount"])
    cells += time_cells()
    cells += [
        markdown('''
            ## 5. 어댑터·채널·허브 흐름과 처리 성능

            업무 흐름별 로그량, p95/p99 처리시간과 상태 분포를 함께 비교합니다.
            그래프는 각 기간의 구성 비율을 비교하며 표의 집계·분위수는 전체 데이터입니다.
        '''),
        code('''
            df["flow_key"] = (text(df["processHubId"]) + " → " + text(df["adapterType"]) + " → " + text(df["channelId"])).astype("category")
            plot_categories(df, ["status", "adapterType", "channelId", "responseCode"])
            display(pd.crosstab(df["source_period"], text(df["status"]), normalize="index").mul(100).round(2))
            flow_summary = df.groupby(["source_period", "flow_key"], observed=True).agg(
                rows=("processId", "size"), median_ms=("duration_ms", "median"),
                p95_ms=("duration_ms", lambda s: s.quantile(.95)), p99_ms=("duration_ms", lambda s: s.quantile(.99)))
            display(flow_summary.sort_values("rows", ascending=False).head(25))
            note("흐름 표는 기간·허브·어댑터·채널별 전체 통계입니다. 처리 건수가 다른 어댑터를 단순 평균 시간으로 비교하지 않습니다.",
                 "DB 읽기/쓰기와 서비스 실행은 역할·처리 단위가 달라 전체 통합 시간 분포가 서로 다른 정상 업무를 섞을 수 있습니다.",
                 "어댑터/업무별 지연 기준선과 처리 건수 피처의 단위를 확인합니다.")
        '''),
        code('''
            adapter_quantiles = df.groupby(["source_period", "adapterType"], observed=True)["duration_ms"].agg(
                valid_rows="count", median_ms="median", p95_ms=lambda s: s.quantile(.95))
            display(adapter_quantiles.loc[adapter_quantiles.valid_rows.ge(30)].sort_values("valid_rows", ascending=False).head(20))
            from log_analysis_visuals import plot_group_quantiles
            plot_group_quantiles(adapter_quantiles, "adapterType")
            note("어댑터별 유효 처리시간 30건 이상인 그룹의 중앙값·p95를 전체 데이터로 비교합니다.",
                 "상위 어댑터 표의 p95는 해당 그룹의 긴 꼬리이며 전체 기간 p95와 같지 않습니다. 빠른 업무가 많다고 다른 업무도 동일 시간 안에 끝나야 하는 것은 아닙니다.",
                 "주요 어댑터의 업무별 시간 분포와 처리 단위를 분리해 기준선을 검토합니다.")
        '''),
    ]
    cells += statistical_cells("processId", ["duration_ms", "totalCount", "retryCount"])
    cells += [
        markdown('''
            ## 7. 처리 건수·재시도·상태 정합성

            `totalCount = successCount + errorCount`는 세 값이 있는 경우만 검사합니다.
            음수 건수, `S` 상태인데 오류 건수가 있는 경우도 점검 후보로 확인합니다.
            `F` 상태의 오류 건수 0은 실행 전에 실패했을 수 있어 모순으로 단정하지 않습니다.
        '''),
        code('''
            df["count_comparable"] = df[["totalCount", "successCount", "errorCount"]].notna().all(axis=1)
            df["count_diff"] = df["totalCount"] - df["successCount"] - df["errorCount"]
            df["count_mismatch_candidate"] = df["count_comparable"] & df["count_diff"].ne(0)
            df["negative_count"] = df[["totalCount", "successCount", "errorCount", "retryCount"]].lt(0).any(axis=1)
            df["success_with_errors"] = df["status"].eq("S") & df["errorCount"].gt(0)
            df["integrity_candidate"] = df["count_mismatch_candidate"] | df["negative_count"] | df["negative_duration"] | df["success_with_errors"]
            display(df.groupby("source_period", observed=True).agg(
                rows=("processId", "size"), count_comparable=("count_comparable", "sum"),
                count_mismatches=("count_mismatch_candidate", "sum"), negative_counts=("negative_count", "sum"),
                success_with_errors=("success_with_errors", "sum")))
            retry_summary = df.groupby(["source_period", "retryCount"], observed=True).size().rename("execution_rows")
            display(retry_summary)
            attempt_counts = df.loc[df["transactionId"].notna() & df["processId"].notna()].groupby(
                ["source_period", "transactionId", "processId"], observed=True).size()
            display(attempt_counts.groupby(level="source_period").agg(logical_processes="size", execution_rows="sum", max_attempts="max"))
            plot_rule_rates(df, {"count_mismatch_candidate": "전체/성공+오류 건수 불일치", "success_with_errors": "성공 상태에 오류 건수 존재"},
                            {"count_mismatch_candidate": "count_comparable"}, "Process 정합성")
            note(f"재시도 값 종류 {df['retryCount'].dropna().nunique():,}개; 논리 Process당 최대 실행 행 {attempt_counts.max():,}건.",
                 "재시도 수가 모두 0이면 현재 수집 데이터에서 재시도 행동을 학습할 수 없습니다. 처리 건수 일치 여부는 완료 상태/어댑터의 계수 정의와 대조해야 합니다.",
                 "현재 상수 재시도 피처의 변별력은 없고, 향후 비영(0이 아닌) 값 출현을 별도로 감시합니다.")
        '''),
        markdown('''
            ## 8. 선행 프로세스의 존재·시간 겹침·재시도 모호성

            선행 프로세스는 **같은 기간·같은 거래** 안에서 찾습니다.
            선행 로그 존재와 종료시각 결측을 구분하며, 선행 실행이 여러 개면 재시도 연결이
            모호하므로 시간 비교에서 제외합니다. 음수 의존 간격은 비동기·스트리밍 업무에서
            정상일 수 있어 단순 순서 위반이나 확정 이상으로 판정하지 않습니다.
            다른 거래에서 템플릿 ID가 발견되는 경우는 동일 실행의 선행 로그를 찾았다는 뜻이 아닙니다.
        '''),
        code('''
            dependency = dependency_features(df)
            df[dependency.columns] = dependency
            dependency_flags = ["has_predecessor", "parent_log_found", "dependency_missing",
                                "dependency_retry_ambiguous", "dependency_time_comparable",
                                "dependency_overlap_candidate", "template_only_elsewhere"]
            display(df[dependency_flags].groupby(df["source_period"], observed=True).sum())
            display(df.groupby("source_period", observed=True)["dependency_gap_ms"].describe(percentiles=[.01, .5, .95, .99]).round(3))
            plot_dependency(df)
        '''),
        code('''
            dependency_by_adapter = df.groupby(["source_period", "adapterType"], observed=True).agg(
                rows=("processId", "size"), missing_parent=("dependency_missing", "sum"),
                timing_comparable=("dependency_time_comparable", "sum"), overlap_candidates=("dependency_overlap_candidate", "sum"))
            dependency_by_adapter["overlap_pct_of_comparable"] = dependency_by_adapter["overlap_candidates"].div(
                dependency_by_adapter["timing_comparable"].replace(0, np.nan)).mul(100)
            display(dependency_by_adapter.round(3))
            display(df.loc[df["dependency_missing"] | df["dependency_overlap_candidate"],
                           ["source_period", "transactionId", "processId", "dependProcessId", "adapterType", "dependency_gap_ms"]].head(20))
            note("어댑터별 시간 겹침 비율은 선행 로그가 존재하고 재시도가 모호하지 않으며 양쪽 시각이 있는 실행만 분모로 삼습니다.",
                 "시간 겹침이 특정 어댑터에 집중되면 동기/비동기 실행 방식의 정상 특성일 수도 있습니다.",
                 "선행 관계를 해석할 때 템플릿 ID가 아닌 같은 거래의 실행 키를 사용합니다.")
        '''),
    ]
    cells += drift_cells(["duration_ms", "totalCount", "retryCount"], ["processId", "adapterType", "flow_key"])
    cells += model_cells(
        ["duration_ms", "totalCount", "retryCount"], ["processId", "adapterType", "channelId", "flow_key"],
        ["source_period", "transactionId", "processId", "startTime", "adapterType", "status", "responseCode", "duration_ms", "totalCount", "retryCount", "dependency_gap_ms", "context_z"],
        "integrity_candidate")
    return cells


def merge_notebook():
    cells = [markdown("# Transaction–Process 병합 분석: 실행 구조와 비지도 이상탐지\n\n" + textwrap.dedent(SCOPE))]
    cells += [
        markdown('''
            ## 1. 환경 설정과 원본 로드

            기간을 포함한 거래 키로 결합해 서로 다른 수집 기간의 같은 ID가 섞이지 않게 합니다.
            프로세스 재시도 행은 원본 규모 분석에 유지하고, 거래 상태 스냅샷만 최신값으로 정리합니다.
        '''), IMPORTS,
        code('''
            transactions, transaction_audit = load_logs("transaction", DATA_DIR)
            processes, process_audit = load_logs("process", DATA_DIR)
            add_time_features(transactions)
            add_time_features(processes)
            display(transaction_audit)
            display(process_audit)
            print(f"통합 Transaction: {len(transactions):,}건 / Process 실행 로그: {len(processes):,}건")
            display(pd.DataFrame({"transaction_rows": transactions.groupby("source_period", observed=True).size(),
                                 "process_execution_rows": processes.groupby("source_period", observed=True).size()}))
        '''),
        markdown('''
            ### 1.1 원본 규모와 시각화 표본 확인

            두 원본의 행 수를 각각 비교합니다. 이후 병합 모델은 Process 행 단위가 아니라
            최종 거래 단위이므로 Process 표본 비율을 거래 발생률로 해석하지 않습니다.
        '''),
        code('''
            transaction_plot = sample_by_period(transactions, PLOT_SAMPLE, RANDOM_STATE)
            process_plot = sample_by_period(processes, PLOT_SAMPLE, RANDOM_STATE)
            plot_sampling(transactions, transaction_plot)
            plot_sampling(processes, process_plot)
            plot_missingness(processes, ["startTime", "endTime", "status", "responseCode", "retryCount"])
            del transaction_plot, process_plot
        '''),
        markdown('''
            ## 2. 결합 키와 1:N 구조 검증

            거래 결합 키는 `source_period + transactionId`입니다. 거래 상태가 여러 행이면
            큰 `retryCount`, 최신 종료/시작시각 순으로 최종 스냅샷을 선택합니다.
            동일 키의 충돌은 감사 표에 남기며, 결측 거래 ID는 결합에서 제외합니다.
            프로세스 수는 재시도 포함 실행 행 수와 고유 `processId` 수를 따로 집계합니다.
        '''),
        code('''
            JOIN_KEYS = ["source_period", "transactionId"]
            tx_state = latest_transaction_state(transactions)
            print(f"최종 거래 스냅샷: {len(tx_state):,}건")
            print(f"최종 스냅샷 선택/결측 ID로 제외된 거래 행: {len(transactions) - len(tx_state):,}건")
            print(f"Process 결측 거래 ID: {processes['transactionId'].isna().sum():,}건")
            merged = processes.loc[processes["transactionId"].notna(), JOIN_KEYS + ["processId", "status", "duration_ms", "adapterType"]].merge(
                tx_state[JOIN_KEYS + ["interfaceId", "status", "processCount"]],
                on=JOIN_KEYS, how="left", suffixes=("_process", "_transaction"), indicator=True, validate="many_to_one")
            display(pd.crosstab(merged["source_period"], merged["_merge"]))
            orphan_processes = merged.loc[merged["_merge"].eq("left_only")]
            display(orphan_processes.head(15))
            note(f"거래에 연결되지 않은 Process 행 {len(orphan_processes):,}건 / 전체 결합 대상 {len(merged):,}건.",
                 "거래 로그 없는 Process는 수집 범위/보존 정책 차이일 수 있습니다. 존재하지 않는 거래에 억지로 결합하면 업무 통계와 모델 입력이 왜곡됩니다.",
                 "고아 Process를 별도로 감사하고 같은 기간·거래 키의 연결만 사용합니다.")
            del orphan_processes
        '''),
        markdown('''
            ## 3. 거래 단위 실행 요약과 선언 Process 수 비교

            `processCount`와 비교할 값은 고유 프로세스 수입니다. 재시도 포함 실행 행 수와
            단순 비교하면 정상 재시도를 불일치로 오인할 수 있습니다.
            연결 로그가 없는 거래는 수집 범위 문제일 수 있어 별도로 확인합니다.
        '''),
        code('''
            transaction_summary = transaction_process_summary(transactions, processes)
            display(transaction_summary.head())
            display(transaction_summary.groupby("source_period", observed=True).agg(
                transactions=("transactionId", "size"), no_process=("no_process_logs", "sum"),
                count_comparable=("count_comparable", "sum"), count_mismatches=("count_mismatch_candidate", "sum"),
                total_attempts=("process_attempts", "sum"), total_logical_processes=("distinct_processes", "sum")))
            display(transaction_summary.groupby(["source_period", "distinct_processes"], observed=True).size().rename("transactions").head(30))
        '''),
        code('''
            plot_merge_counts(transaction_summary)
        '''),
        markdown('''
            ## 4. 거래 상태와 최종 프로세스 상태

            프로세스별 최신 재시도 스냅샷에서 F가 하나라도 있으면 F, 전부 S면 S,
            나머지는 OTHER로 요약합니다. 거래·프로세스 양쪽이 S/F인 경우만 단순 불일치율을
            계산합니다. H/Z/P/N 같은 상태의 운영 의미는 이 규칙만으로 확정할 수 없습니다.
        '''),
        code('''
            status_cross = pd.crosstab(text(transaction_summary["status"]), transaction_summary["process_derived_status"])
            display(status_cross)
            status_quality = transaction_summary.groupby("source_period", observed=True).agg(
                comparable=("status_comparable", "sum"), mismatch=("status_mismatch_candidate", "sum"))
            status_quality["mismatch_pct_of_comparable"] = status_quality["mismatch"].div(status_quality["comparable"].replace(0, np.nan)).mul(100)
            display(status_quality.round(3))
            plot_merge_status(transaction_summary)
        '''),
        markdown('''
            ## 5. 시간 경계·오버헤드·병목

            거래 시작과 첫 Process 시작, 거래 종료와 마지막 Process 종료를 비교합니다.
            경계가 같은지를 요구하지 않으며, Process가 거래 범위 밖으로 1ms 이상 벗어나는
            경우를 점검 후보로 표시합니다. 병렬 실행이면 Process 시간 합 / 거래 시간은
            1을 넘을 수 있으므로 이 값만으로 모순을 판정하지 않습니다.
        '''),
        code('''
            PERFORMANCE_COLS = ["duration_ms", "process_span_ms", "process_duration_sum_ms", "bottleneck_duration_ms",
                                "orchestration_overhead_ms", "parallelism_ratio", "start_delta_ms", "end_delta_ms"]
            display(numeric_summary(transaction_summary, PERFORMANCE_COLS))
            display(transaction_summary.groupby("source_period", observed=True).agg(
                rows=("transactionId", "size"), boundary_candidates=("boundary_candidate", "sum"),
                median_overhead_ms=("orchestration_overhead_ms", "median"), median_parallelism=("parallelism_ratio", "median")))
            display(transaction_summary.loc[transaction_summary["boundary_candidate"],
                    ["source_period", "transactionId", "start_delta_ms", "end_delta_ms", "duration_ms", "process_span_ms"]].head(20))
        '''),
        code('''
            summary_plot = sample_by_period(transaction_summary, PLOT_SAMPLE, RANDOM_STATE)
            plot_merge_performance(transaction_summary, summary_plot)
            bottleneck_profile = merged.groupby(["source_period", "interfaceId", "adapterType"], observed=True).agg(
                process_rows=("processId", "size"), median_ms=("duration_ms", "median"), p95_ms=("duration_ms", lambda s: s.quantile(.95)))
            display(bottleneck_profile.loc[bottleneck_profile["process_rows"].ge(30)].sort_values("p95_ms", ascending=False).head(20))
            note("병목 후보 표는 업무·어댑터별 유효 Process 시간 p95 상위 그룹(실행 30건 이상)입니다.",
                 "p95가 큰 어댑터는 정상 배치 업무일 수도 있으므로 단독 장애 근거가 아닙니다. 유효 종료시각이 적은 그룹은 지연 분위수 해석에 주의합니다.",
                 "거래의 지연 후보와 해당 실행 단계의 어댑터를 연결해 설명합니다.")
        '''),
        markdown('''
            ## 6. 선행 Process와 거래 범위

            같은 기간·거래의 선행 로그를 찾고, 재시도가 여러 개면 시간 비교를 유보합니다.
            다른 거래에 같은 템플릿이 있다는 사실은 교차 거래 의존성을 뜻하지 않습니다.
            스트리밍·병렬 처리의 시간 겹침은 업무별로 해석해야 합니다.
        '''),
        code('''
            dependency = dependency_features(processes)
            display(dependency.groupby(processes["source_period"], observed=True).agg(
                references=("has_predecessor", "sum"), missing=("dependency_missing", "sum"),
                retries_ambiguous=("dependency_retry_ambiguous", "sum"), comparable=("dependency_time_comparable", "sum"),
                overlap_candidates=("dependency_overlap_candidate", "sum"), template_only_elsewhere=("template_only_elsewhere", "sum")))
            dependency_tx = dependency.assign(source_period=processes["source_period"], transactionId=processes["transactionId"]).groupby(
                JOIN_KEYS, observed=True)[["dependency_missing", "dependency_overlap_candidate"]].sum()
            transaction_summary = transaction_summary.merge(dependency_tx.reset_index(), on=JOIN_KEYS, how="left", validate="one_to_one")
            dependency_view = processes[["source_period"]].join(dependency)
            plot_dependency(dependency_view)
            del dependency_view
        '''),
        markdown('''
            ## 7. 실행 시퀀스와 드문 업무 흐름

            거래 시작 순서 기준 어댑터 시퀀스를 기간별 최대 약 1만 거래에서 확인합니다.
            한 거래의 프로세스는 모두 포함하되 재시도는 최신 상태로 정리합니다.
            시퀀스 빈도는 표본 기준이며 시작 순서는 의존 관계의 위상 순서와 다를 수 있습니다.
            희귀한 정상 업무·신규 업무가 섞일 수 있으므로 빈도만으로 이상을 확정하지 않습니다.
        '''),
        code('''
            sequence_transactions = sample_by_period(tx_state, PLOT_SAMPLE, RANDOM_STATE)[JOIN_KEYS + ["interfaceId"]]
            sequence_processes = processes.merge(sequence_transactions, on=JOIN_KEYS, how="inner", validate="many_to_one")
            sequence_processes = sequence_processes.sort_values(JOIN_KEYS + ["processId", "retryCount", "endTime"], na_position="first").drop_duplicates(
                JOIN_KEYS + ["processId"], keep="last").sort_values(JOIN_KEYS + ["startTime", "processId"])
            sequence_processes["adapter_label"] = text(sequence_processes["adapterType"])
            sequences = sequence_processes.groupby(JOIN_KEYS, observed=True).agg(
                interfaceId=("interfaceId", "first"), sequence=("adapter_label", lambda x: " → ".join(x)), steps=("processId", "size"))
            sequence_profile = sequences.groupby(["source_period", "interfaceId", "sequence"], observed=True).size().rename("sample_transactions")
            display(sequence_profile.sort_values(ascending=False).head(25))
            display(sequences.groupby("source_period", observed=True).agg(sample_transactions=("steps", "size"), unique_sequences=("sequence", "nunique")))
            plot_sequences(sequences, sequence_transactions)
            del sequence_processes
        '''),
        markdown('''
            ## 8. 거래 단위 시계열과 분포 변화

            이하 분석 단위는 원본 프로세스 행이 아니라 최종 거래 스냅샷입니다.
            처리시간뿐 아니라 실행 구조와 병렬성·오버헤드 변화도 확인합니다.
        '''),
        code('''
            df = transaction_summary
            daily = time_summary(df, "D")
            plot_period_timeseries(daily, "rows", "최종 거래 스냅샷 일별 건수")
            plot_period_timeseries(daily, "p95_ms", "최종 거래 일별 p95 처리시간")
            df["integrity_candidate"] = df[["count_mismatch_candidate", "status_mismatch_candidate", "boundary_candidate"]].any(axis=1)
        '''),
    ]
    cells += drift_cells(["duration_ms", "process_attempts", "distinct_processes", "bottleneck_duration_ms", "orchestration_overhead_ms", "parallelism_ratio"], ["interfaceId", "startChannelId", "endChannelId"])
    cells += model_cells(
        ["duration_ms", "processCount", "retryCount", "process_attempts", "distinct_processes", "bottleneck_duration_ms", "orchestration_overhead_ms", "parallelism_ratio"],
        ["interfaceId", "startChannelId", "endChannelId", "interfaceType"],
        ["source_period", "transactionId", "startTime", "interfaceId", "status", "duration_ms", "process_attempts", "distinct_processes", "bottleneck_duration_ms", "count_mismatch_candidate", "status_mismatch_candidate", "boundary_candidate"],
        "integrity_candidate")
    return cells


BUILDERS = {
    "transaction_analysis.ipynb": transaction_notebook,
    "processes_analysis.ipynb": process_notebook,
    "merge_analysis.ipynb": merge_notebook,
}


def notebook(name):
    cells = BUILDERS[name]()
    for i, cell in enumerate(cells):
        cell["id"] = f"{name.split('_')[0]}-{i:03d}"
    return {"cells": cells, "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "file_extension": ".py", "mimetype": "text/x-python",
                          "pygments_lexer": "ipython3", "nbconvert_exporter": "python"},
    }, "nbformat": 4, "nbformat_minor": 5}


if __name__ == "__main__":
    name = sys.argv[1]
    new = json.dumps(notebook(name), ensure_ascii=False, indent=1) + "\n"
    patch = "*** Begin Patch\n*** Add File: code/" + name + "\n"
    patch += "".join("+" + line + "\n" for line in new.splitlines())
    patch += "*** End Patch\n"
    sys.stdout.buffer.write(patch.encode("utf-8"))

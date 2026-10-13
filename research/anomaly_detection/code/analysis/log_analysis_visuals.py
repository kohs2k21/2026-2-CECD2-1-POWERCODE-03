"""Readable notebook figures and data-derived interpretation (no file exports).

Statistics use complete data. Histograms/PCA/scatter use explicitly audited,
period-balanced samples. The plotting sample is not a prevalence estimate.
"""

import textwrap

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import numpy as np
import pandas as pd
import seaborn as sns
from IPython.display import Markdown, display

from log_analysis_common import PERIODS, text


SHORT = dict(zip(PERIODS, ["① 25.12–26.04", "② 26.08–09", "③ 26.09.30–10.02"]))
COLORS = dict(zip(PERIODS, ["#3569AF", "#168579", "#E08C32"]))
RED = "#C83E4D"
GRAY = "#CBD2DA"
LABELS = {
    "duration_ms": "처리시간 (ms)", "processTime": "기록 처리시간 (ms)",
    "processCount": "선언 Process 수", "retryCount": "재시도 수",
    "totalCount": "전체 처리 건수", "successCount": "성공 처리 건수",
    "errorCount": "오류 처리 건수", "process_attempts": "Process 실행 행 수",
    "distinct_processes": "고유 Process 수", "bottleneck_duration_ms": "최대 Process 시간 (ms)",
    "orchestration_overhead_ms": "거래 시간 - Process 범위 (ms)",
    "parallelism_ratio": "Process 시간 합 / 거래 시간", "interarrival_seconds": "도착 간격 (초)",
}


def label(period):
    return SHORT.get(str(period), str(period))


def periods(frame):
    present = set(frame["source_period"].dropna().astype(str))
    return [p for p in PERIODS if p in present] + sorted(present - set(PERIODS))


def number(value):
    if pd.isna(value):
        return "판정 불가"
    return f"{value:,.0f}" if abs(value) >= 100 or float(value).is_integer() else f"{value:,.2f}"


def note(observations, meaning, action):
    """Render current calculated results next to each figure, not stale constants."""
    observations = [observations] if isinstance(observations, str) else observations
    display(Markdown("**실제 결과**\n\n" + "\n".join(f"- {x}" for x in observations)
                     + "\n\n**의미**: " + meaning + "\n\n**모델 개발에 반영**: " + action))


def finish(fig, title):
    fig.suptitle(title, x=.02, ha="left", fontsize=15, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, .91))
    plt.show()
    plt.close(fig)


def native_axis(ax, which="x", positive=False):
    setter = ax.set_xscale if which == "x" else ax.set_yscale
    setter("log" if positive else "symlog", **({} if positive else {"linthresh": 1}))
    axis = ax.xaxis if which == "x" else ax.yaxis
    axis.set_major_formatter(FuncFormatter(lambda x, _: number(x)))


def sampling_audit(frame, sample):
    rows = frame.groupby("source_period", observed=True).size().reindex(periods(frame))
    sampled = sample.groupby("source_period", observed=True).size().reindex(rows.index, fill_value=0)
    return pd.DataFrame({"전체 행 수": rows, "그림 표본 행 수": sampled,
                         "전체 내 비중 (%)": rows / rows.sum() * 100,
                         "표본 내 비중 (%)": sampled / max(sampled.sum(), 1) * 100,
                         "기간별 추출률 (%)": sampled / rows * 100})


def plot_sampling(frame, sample):
    audit = sampling_audit(frame, sample)
    fig, axes = plt.subplots(1, 2, figsize=(13, 3.8))
    names = [label(p) for p in audit.index]
    y = np.arange(len(audit))
    axes[0].barh(y, audit["전체 행 수"], color=[COLORS.get(p, "#3569AF") for p in audit.index])
    axes[0].set_yticks(y, names)
    axes[0].invert_yaxis()
    axes[0].set(xlabel="전체 로그 행 수", xlim=(0, audit["전체 행 수"].max() * 1.85))
    axes[0].xaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x / 10_000:g}만"))
    for i, row in enumerate(audit.itertuples(index=False, name=None)):
        axes[0].text(row[0], i, f"  {row[0]:,}건 ({row[2]:.2f}%)", va="center", fontsize=10)
    axes[1].barh(y - .17, audit["전체 내 비중 (%)"], height=.32, color="#536779", label="전체 데이터 비중")
    axes[1].barh(y + .17, audit["표본 내 비중 (%)"], height=.32, color="#A6C3E1", label="그림 표본 비중")
    axes[1].set_yticks(y, names)
    axes[1].invert_yaxis()
    axes[1].set(xlabel="각 데이터셋 안에서의 기간 비중 (%)", xlim=(0, 110))
    for i, p in enumerate(audit.index):
        for offset, col in [(-.17, "전체 내 비중 (%)"), (.17, "표본 내 비중 (%)")]:
            v = audit.loc[p, col]
            axes[1].text(v + 1, i + offset, f"{v:.1f}%", va="center", fontsize=10)
    axes[1].legend(loc="lower right", fontsize=9)
    finish(fig, "전체 데이터 규모와 시각화 표본 구성")
    display(audit.rename(index=label).round(3))
    top = audit["전체 내 비중 (%)"].idxmax()
    note(f"전체 {len(frame):,}건 중 {label(top)}가 {audit.loc[top, '전체 내 비중 (%)']:.2f}%입니다. "
         f"시각화는 {len(sample):,}건을 기간별 균형 추출했습니다.",
         "통합 건수 그래프는 가장 큰 기간의 특성을 주로 보여줍니다. 균형 표본은 짧은 기간을 비교하기 위한 것으로 전체 발생 비율을 대표하지 않습니다.",
         "전체 통계로 발생률을 계산하고, 기간별 표본으로 분포 형태를 비교합니다. 표본 추출률이 큰 최근 기간을 전체 비중으로 오해하지 않습니다.")
    return audit


def plot_missingness(frame, columns):
    missing = frame[columns].isna().groupby(frame["source_period"], observed=True).mean().mul(100).reindex(periods(frame))
    fig, ax = plt.subplots(figsize=(12, 3.4))
    sns.heatmap(missing.rename(index=label), annot=True, fmt=".2f", vmin=0, vmax=100,
                cmap="YlOrRd", linewidths=1, cbar_kws={"label": "전체 행 중 결측률 (%)"}, ax=ax)
    ax.tick_params(axis="y", labelrotation=0)
    ax.set(xlabel="컬럼", ylabel="기간")
    finish(fig, "결측률 비교 (전체 데이터, 동일한 0–100% 색상 범위)")
    obs = [f"{label(p)}: 종료시각 결측 {frame.loc[frame.source_period.eq(p), 'endTime'].isna().sum():,}건 "
           f"({missing.loc[p, 'endTime']:.2f}%)." for p in missing.index]
    note(obs, "종료시각 결측은 처리시간을 계산할 수 없는 로그입니다. 미완료 상태·수집 누락을 구분해야 하며 처리시간 0으로 대치하면 분포가 왜곡됩니다.",
         "결측 여부 피처를 유지하고, 완료 로그 지연 탐지와 미완료 로그 탐지를 분리합니다. 0.00%는 반올림된 값일 수 있으므로 건수도 확인합니다.")


def plot_discrete(frame, column, top_n=6):
    counts = pd.crosstab(frame["source_period"], text(frame[column])).reindex(periods(frame), fill_value=0)
    selected = counts.sum().nlargest(top_n).index.tolist()
    table = counts[selected].copy()
    if len(selected) < len(counts.columns):
        table["기타"] = counts.drop(columns=selected).sum(axis=1)
    table = table.div(counts.sum(axis=1), axis=0).mul(100)
    fig, ax = plt.subplots(figsize=(13, 3.7))
    palette = sns.color_palette("Set2", len(table.columns))
    left = np.zeros(len(table))
    for color, col in zip(palette, table.columns):
        values = table[col].to_numpy()
        ax.barh(np.arange(len(table)), values, left=left, color=color, label=str(col), edgecolor="white")
        for i, v in enumerate(values):
            if v >= 8:
                ax.text(left[i] + v / 2, i, f"{col}\n{v:.1f}%", ha="center", va="center", fontsize=10)
        left += values
    ax.set_yticks(np.arange(len(table)), [label(p) for p in table.index])
    ax.invert_yaxis()
    ax.set(xlim=(0, 100), xlabel="기간의 전체 행 중 비율 (%)")
    ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", title="값", fontsize=9)
    finish(fig, f"{LABELS.get(column, column)} 분포 (전체 데이터)")
    obs = []
    for p, row in counts.iterrows():
        mode = row.idxmax()
        obs.append(f"{label(p)}: 최빈값 {mode}, {row[mode]:,}건 ({row[mode] / row.sum():.2%}).")
    constant = frame[column].dropna().nunique() <= 1
    note(obs, "모든 유효 값이 같습니다. 이 표본에서는 이 변수만으로 로그를 구분할 수 없습니다." if constant
         else "기간별 막대의 길이를 100%로 맞췄기 때문에 규모가 아니라 값의 구성 차이를 비교합니다. 처리 건수의 의미·단위는 어댑터마다 다를 수 있습니다.",
         "상수 피처는 현재 모델에서 변별력이 없습니다. 향후 새 값 출현은 별도로 감시합니다." if constant
         else "연속형 정규분포를 가정하지 않고 범주형/카운트 특성을 반영합니다. 기간·업무 구성이 다른데 공통 IQR을 적용하면 정상 값도 대량 경보가 될 수 있습니다.")


def plot_continuous(frame, sample, column):
    order = periods(frame)
    valid = sample[column].dropna()
    positive = valid[valid.gt(0)]
    if positive.empty:
        return plot_discrete(frame, column)
    lower, upper = positive.min(), positive.max()
    bins = np.geomspace(max(lower * .85, .001), max(upper * 1.15, lower * 1.2), 45)
    fig, axes = plt.subplots(1, len(order), figsize=(15, 4.2), sharex=True, sharey=True, squeeze=False)
    obs = []
    for ax, p in zip(axes.flat, order):
        full = frame.loc[frame.source_period.eq(p), column]
        values = sample.loc[sample.source_period.eq(p), column].dropna()
        q = full.quantile([.5, .95, .99])
        good = values[values.gt(0)]
        ax.hist(good, bins=bins, weights=np.full(len(good), 100 / max(len(values), 1)),
                color=COLORS.get(p, "#3569AF"), alpha=.8)
        for quantile, style, color in [(.5, "-", "#24313D"), (.95, "--", RED)]:
            if pd.notna(q[quantile]) and q[quantile] > 0:
                ax.axvline(q[quantile], color=color, linestyle=style, linewidth=1.8,
                           label=f"전체 p{int(quantile * 100)} = {number(q[quantile])}")
        native_axis(ax, positive=True)
        ax.set(title=f"{label(p)}\n유효 표본 {len(values):,}건 / 전체 결측 {full.isna().mean():.2%}",
               xlabel=LABELS.get(column, column), ylabel="표본의 유효 값 중 비율 (%)")
        ax.legend(loc="upper right", fontsize=9)
        ax.text(.02, .96, f"전체 p99 = {number(q[.99])}\n표본 0 이하: {values.le(0).sum():,}건", transform=ax.transAxes,
                va="top", fontsize=9, bbox={"facecolor": "white", "alpha": .85, "edgecolor": "none"})
        obs.append(f"{label(p)}: 전체 중앙값 {number(q[.5])}, p95 {number(q[.95])}, p99 {number(q[.99])}.")
    finish(fig, f"{LABELS.get(column, column)}: 기간별 분포와 긴 꼬리")
    action = {
        "interarrival_seconds": "업무·시간대별 도착 간격을 비교하고 수집 공백/배치 실행을 구분한 뒤 발생률 기준선에 반영합니다.",
        "parallelism_ratio": "이 값은 시간 합의 비율이며 CPU 병렬도가 아닙니다. 실행 구조와 업무별 정상 범위에 맞춰 모델 피처로 사용하고 1 초과를 단독 이상 규칙으로 쓰지 않습니다.",
    }.get(column, "절대 처리시간과 업무별 상대 지연을 함께 사용합니다. 중앙값 차이가 크면 기간·업무 그룹 기준선을 비교하고, 긴 꼬리에는 log1p 변환을 적용합니다. 음수·0·결측은 별도 정합성 점검 대상입니다.")
    note(obs, "가로축은 로그 간격이지만 눈금은 실제 단위입니다. 히스토그램은 같은 구간·같은 세로축을 사용하는 균형 표본이며 선과 분위수는 전체 데이터입니다. p95보다 큰 값은 전체 유효 값의 최대 약 5%입니다(동률 제외). 표본에 없는 극단값은 히스토그램 범위 밖일 수 있으며 전체 통계 표에서 확인합니다.", action)


def plot_numeric_distributions(frame, sample, columns):
    for col in columns:
        if col.endswith("_ms") or col in {"processTime", "interarrival_seconds", "parallelism_ratio"}:
            plot_continuous(frame, sample, col)
        else:
            plot_discrete(frame, col)


def plot_categories(frame, columns, top_n=6, population="전체 데이터"):
    for col in columns:
        if col == "status":
            plot_discrete(frame, col, top_n=9)
            continue
        order = periods(frame)
        fig, axes = plt.subplots(1, len(order), figsize=(17, 4.2), sharex=True, squeeze=False)
        obs = []
        for ax, p in zip(axes.flat, order):
            values = text(frame.loc[frame.source_period.eq(p), col])
            top = values.value_counts().head(top_n).sort_values()
            shares = top / len(values) * 100
            ax.barh(np.arange(len(top)), shares, color=COLORS.get(p, "#3569AF"))
            ax.set_yticks(np.arange(len(top)), [textwrap.fill(str(x), 27) for x in top.index])
            denominator = "기간의 시퀀스 생성 표본 거래 중 비율 (%)" if population != "전체 데이터" else "기간의 전체 행 중 비율 (%)"
            ax.set(title=label(p), xlabel=denominator, xlim=(0, 125))
            ax.set_xticks([0, 25, 50, 75, 100])
            for i, (n, pct) in enumerate(zip(top, shares)):
                ax.text(pct + 1, i, f"{pct:.2f}%\n{n:,}건", va="center", fontsize=9)
            if len(top):
                obs.append(f"{label(p)}: 최빈 {top.index[-1]}가 {shares.iloc[-1]:.2f}%, 상위 {len(top)}개 합계 {shares.sum():.2f}%.")
        finish(fig, f"{col}: 기간별 상위 {top_n}개 구성 ({population})")
        note(obs, "전체 기간을 합산한 순위 대신 각 기간의 상위 범주를 따로 보여줍니다. 업무 구성이나 어댑터 역할이 바뀌면 처리시간과 실패율의 정상 기준도 바뀔 수 있습니다.",
             "범주별 정상 기준선과 신규 범주 감시를 검토합니다. 상태와 응답코드는 사후 해석용이며 실패 상태 자체를 비지도 모델의 정답으로 사용하지 않습니다.")


def plot_group_quantiles(profile, entity, top_n=5):
    eligible = profile.loc[profile.valid_rows.ge(30)].reset_index()
    order = periods(eligible)
    fig, axes = plt.subplots(1, len(order), figsize=(17, 4.4), sharex=True, squeeze=False)
    observations = []
    for ax, p in zip(axes.flat, order):
        g = eligible.loc[eligible.source_period.eq(p)].nlargest(top_n, "valid_rows").iloc[::-1]
        y = np.arange(len(g))
        ax.hlines(y, g.median_ms, g.p95_ms, color="#9EAAB7", linewidth=3)
        ax.scatter(g.median_ms, y, color=COLORS.get(p), s=45, zorder=3, label="전체 중앙값")
        ax.scatter(g.p95_ms, y, color=RED, marker="|", s=180, zorder=4, label="전체 p95")
        ax.set_yticks(y, [textwrap.fill(str(name), 24) + f"\n유효 {n:,}건" for name, n in zip(g[entity], g.valid_rows)])
        native_axis(ax, positive=True)
        ax.set(title=label(p), xlabel="처리시간 (ms, 로그 간격)")
        ax.legend(loc="lower right", fontsize=9)
        if len(g):
            largest = g.iloc[-1]
            observations.append(f"{label(p)}: 최다 {entity} {largest[entity]}, 중앙값 {number(largest.median_ms)}ms, p95 {number(largest.p95_ms)}ms.")
    finish(fig, f"{entity}별 중앙값–p95 (전체 유효 데이터, 기간별 로그량 상위 {top_n}개)")
    note(observations, "점은 해당 어댑터의 중앙값, 빨간 세로선은 p95이며 선 길이는 긴 꼬리의 정도를 보여줍니다. 같은 x축에서 비교하므로 기간별 처리 방식의 차이가 드러납니다.",
         "전체 모델 외에 어댑터별 지연 기준선을 검토합니다. 로그가 많고 꼬리가 긴 그룹을 먼저 살피되 처리 건수와 정상 업무 정의를 함께 확인합니다.")


def plot_period_timeseries(summary, value, title):
    order = periods(summary)
    fig, axes = plt.subplots(len(order), 1, figsize=(13, 2.7 * len(order)), squeeze=False)
    obs = []
    for ax, p in zip(axes.flat, order):
        group = summary.loc[summary.source_period.eq(p)].sort_values("bin")
        observed = group.dropna(subset=[value])
        series = group.set_index("bin")[value].asfreq("D")
        ax.plot(series.index, series, color=COLORS.get(p, "#3569AF"), marker="o", markersize=3, linewidth=1.4)
        if not observed.empty:
            peak = observed.loc[observed[value].idxmax()]
            ax.scatter([peak["bin"]], [peak[value]], color=RED, s=40, zorder=5)
            ax.annotate(f"최대 {number(peak[value])}\n{peak['bin']:%m/%d}", (peak["bin"], peak[value]),
                        xytext=(7, -27), textcoords="offset points", fontsize=10,
                        bbox={"facecolor": "white", "alpha": .9, "edgecolor": "none"})
            obs.append(f"{label(p)}: 관측 {len(observed):,}일, 최대 {number(peak[value])} ({peak['bin']:%Y-%m-%d}).")
        ax.set(title=label(p), ylabel={"rows": "건수 (로그 간격)", "p95_ms": "p95 (ms, 로그 간격)", "failure_pct": "실패 상태 비율 (%)"}.get(value, value))
        if value == "failure_pct":
            ax.set_ylim(-3, 105)
        else:
            native_axis(ax, "y")
        locator = mdates.DayLocator() if (group.bin.max() - group.bin.min()).days < 7 else mdates.AutoDateLocator(minticks=3, maxticks=8)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        ax.margins(x=.07)
    finish(fig, title + " (전체 데이터, 미관측 날짜는 선을 끊음)")
    note(obs, "건수·p95는 로그 눈금으로 작은 기간도 읽을 수 있게 했고, 실패율은 동일한 0–100% 범위로 표시했습니다. 수집되지 않은 날짜는 0이 아니라 미관측입니다. 날짜별 p95는 관측 건수가 적으면 불안정합니다.",
         "시간대·요일·업무량을 조건으로 한 기준선과 지연/실패 동시 변화를 검토합니다. 건수 증가만으로 장애를 판정하지 않습니다.")


def plot_hour_pattern(frame):
    order = periods(frame)
    matrices = []
    for p in order:
        group = frame.loc[frame.source_period.eq(p) & frame.startTime.notna()]
        counts = pd.crosstab(group.dayofweek, group.hour).reindex(index=range(7), columns=range(24), fill_value=0)
        days = group.startTime.dt.normalize().drop_duplicates().dt.dayofweek.value_counts().reindex(range(7))
        matrices.append(counts.div(days, axis=0))
    max_value = max(float(m.max().max()) for m in matrices)
    fig, axes = plt.subplots(len(order), 1, figsize=(14, 2.5 * len(order)), squeeze=False)
    obs = []
    for ax, p, heat in zip(axes.flat, order, matrices):
        sns.heatmap(np.log1p(heat), cmap="Blues", vmin=0, vmax=np.log1p(max_value), ax=ax,
                    yticklabels=list("월화수목금토일"), cbar_kws={"label": "log1p(관측 날짜당 건수)"})
        ax.tick_params(axis="y", labelrotation=0)
        ax.set(title=label(p), xlabel="한국 시간 (0–23시)", ylabel="요일")
        if heat.notna().any().any():
            weekday, hour = heat.stack().idxmax()
            ax.add_patch(plt.Rectangle((hour, weekday), 1, 1, fill=False, edgecolor=RED, linewidth=2))
            obs.append(f"{label(p)}: 최다 시간대 {list('월화수목금토일')[weekday]}요일 {hour}시, 관측 날짜당 {heat.loc[weekday, hour]:,.1f}건. 빨간 테두리로 표시했습니다.")
    finish(fig, "요일·시간대 트래픽 (전체 데이터, 공통 색상 범위)")
    note(obs, "흰색은 해당 요일의 관측 날짜가 없다는 뜻입니다. 파란색 농도는 모든 기간에 같은 로그 색상 범위를 사용합니다. 0건인 칸도 수집이 완전했다는 증거는 아닙니다.",
         "야간/주말을 무조건 이상으로 취급하지 않고 관측된 주기 패턴을 시간 피처 또는 계절성 기준선에 반영합니다.")


def plot_traffic_candidates(traffic):
    fig, axes = plt.subplots(len(periods(traffic)), 1, figsize=(13, 7.5), squeeze=False)
    obs = []
    for ax, p in zip(axes.flat, periods(traffic)):
        g = traffic.loc[traffic.source_period.eq(p)]
        ax.plot(g.bin, g.rows, color=COLORS.get(p), linewidth=1, label="시간별 로그량")
        unknown = g.rows.notna() & ~g.reference_available
        ax.scatter(g.loc[unknown, "bin"], g.loc[unknown, "rows"], color=GRAY, s=16, label="기준선 부족/MAD=0", zorder=3)
        candidates = g.loc[g.candidate]
        ax.scatter(candidates.bin, candidates.rows, color=RED, marker="X", s=45, label="과거 대비 급변 후보", zorder=5)
        native_axis(ax, "y")
        ax.set(title=f"{label(p)}: 비교 가능 {g.reference_available.sum():,} / 관측 {g.rows.count():,}시간", ylabel="시간별 건수")
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=7))
        date_format = "%m/%d %H시" if (g.bin.max() - g.bin.min()).days < 4 else "%m/%d"
        ax.xaxis.set_major_formatter(mdates.DateFormatter(date_format))
        ax.legend(fontsize=9, loc="upper right")
        obs.append(f"{label(p)}: 기준선 계산 가능 {g.reference_available.sum():,}시간, 후보 {len(candidates):,}시간, 기준선 없는 관측 {unknown.sum():,}시간.")
    finish(fig, "트래픽 급변 후보와 판정 불가 구간 (전체 데이터)")
    note(obs, "빨간 X는 과거 24시간 로그량 중앙값/MAD 대비 급변 후보입니다. 회색 점은 정상 판정이 아니라 이력 부족 또는 MAD=0으로 비교할 수 없는 관측입니다.",
         "후보 수와 함께 평가 가능한 구간의 비율을 보고합니다. 짧은 최신 기간에서 후보 0개가 나와도 정상이라고 결론 내리지 않습니다.")


def plot_rule_rates(frame, flags, denominators=None, title="정합성 점검 후보"):
    denominators = denominators or {}
    order = periods(frame)
    fig, axes = plt.subplots(1, len(flags), figsize=(max(10, 4.5 * len(flags)), 3.9), squeeze=False)
    obs = []
    for ax, (flag, name) in zip(axes.flat, flags.items()):
        numerators, denoms = [], []
        for p in order:
            g = frame.loc[frame.source_period.eq(p)]
            eligible = g[denominators[flag]].fillna(False) if flag in denominators else pd.Series(True, index=g.index)
            n = int((g[flag].fillna(False) & eligible).sum())
            d = int(eligible.sum())
            numerators.append(n)
            denoms.append(d)
            obs.append(f"{label(p)} / {name}: {n:,} / {d:,}건 ({n / d:.3%})." if d else f"{label(p)} / {name}: 비교 가능한 로그 없음.")
        rates = np.divide(numerators, denoms, out=np.full(len(order), np.nan), where=np.array(denoms) > 0) * 100
        ax.barh(range(len(order)), rates, color=[COLORS.get(p) for p in order])
        ax.set_yticks(range(len(order)), [label(p) for p in order])
        ax.invert_yaxis()
        ax.set(title=name, xlabel="대상 로그 중 후보 비율 (%)",
               xlim=(0, min(100, max(1, np.nanmax(rates) * 2)) if np.isfinite(rates).any() else 1))
        for i, (n, d, pct) in enumerate(zip(numerators, denoms, rates)):
            inside = pd.notna(pct) and pct > 60
            x = 0 if pd.isna(pct) else pct / 2 if inside else pct
            ax.text(x, i, f"  {pct:.3f}%\n  {n:,}/{d:,}" if d else "비교 불가",
                    va="center", ha="center" if inside else "left", fontsize=9,
                    color="white" if inside else "#24313D")
    finish(fig, title + " (전체 데이터, 패널별 축 범위 표시)")
    note(obs, "분모는 각 규칙의 비교 가능한 로그입니다(별도 조건이 없는 규칙은 기간 전체 행). 서로 다른 규칙의 후보는 중복될 수 있어 합산하면 안 됩니다. 후보 0건과 비교 불가는 다릅니다.",
         "낮은 비율도 실제 건수로 확인해 검토 대상을 정합니다. 정합성 규칙은 비지도 모델 점수와 별도로 표시하며 확정 장애 라벨로 사용하지 않습니다.")


def plot_statistical_candidates(frame, iqr_table):
    fig, ax = plt.subplots(figsize=(11, 3.8))
    table = iqr_table.iloc[::-1]
    ax.barh(range(len(table)), table.candidate_pct, color=[RED if x == 0 else "#3569AF" for x in table.IQR])
    ax.set_yticks(range(len(table)), [LABELS.get(x, x) for x in table.feature])
    ax.set(xlabel="전체 행 중 단변량 IQR 후보 비율 (%)", xlim=(0, max(1, table.candidate_pct.max() * 1.5)))
    for i, r in enumerate(table.itertuples()):
        ax.text(r.candidate_pct, i, f"  {r.candidate_pct:.2f}% ({r.candidate_count:,}건)" + ("  IQR=0" if r.IQR == 0 else ""), va="center")
    finish(fig, "공통 IQR 기준선의 후보량과 한계")
    zero = iqr_table.loc[iqr_table.IQR.eq(0), "feature"].tolist()
    note(f"IQR=0인 변수: {', '.join(zero) if zero else '없음'}. 처리시간 외 카운트 변수는 정상 최빈값에서 벗어나기만 해도 후보가 될 수 있습니다.",
         "IQR=0이면 Q1과 Q3가 같아 정상 범위를 사실상 한 점으로 축소합니다. 이때 큰 후보량은 탐지 성능보다 혼합된 이산 분포와 기준선의 한계를 보여줍니다.",
         "전체 IQR 후보를 정답으로 학습하지 않습니다. 카운트는 업무별 허용값·범주형 표현을 검토하고 처리시간은 그룹별 기준선과 비교합니다.")
    plot_rule_rates(frame, {"context_candidate": "업무별 상대 지연"},
                    {"context_candidate": "context_comparable"}, "기간·업무별 MAD 기준선")
    unavailable = (~frame.context_comparable).groupby(frame.source_period, observed=True).sum()
    note([f"{label(p)}: 그룹 기준선으로 비교 불가 {n:,}건." for p, n in unavailable.items()],
         "업무별 상대 지연 후보가 많으면 그룹 내부 다중 모드 또는 매우 작은 MAD 때문에 규칙이 민감한지도 확인해야 합니다. MAD=0/표본 부족/처리시간 결측은 정상 0점이 아닙니다.",
         "그룹별 지연 분포를 다시 살피고 최소 분산·운영 허용 지연 등 기준선을 보완합니다. 이 전체기간 EDA 기준선은 시간 분리 모델의 입력에 사용하지 않습니다.")


def plot_drift(numeric, categorical):
    numeric = numeric.loc[numeric.source_period.ne(PERIODS[0])]
    heat = numeric.pivot(index="feature", columns="source_period", values="PSI")
    heat = heat.rename(index=lambda x: LABELS.get(x, x), columns=label)
    fig, axes = plt.subplots(1, 2, figsize=(15, max(4, .65 * len(heat))), squeeze=False)
    sns.heatmap(heat, annot=True, fmt=".3f", cmap="YlOrRd", vmin=0, ax=axes[0, 0],
                linewidths=1, cbar_kws={"label": "PSI (0에 가까울수록 유사)"})
    novel = categorical.loc[categorical.source_period.ne(PERIODS[0])].pivot(
        index="feature", columns="source_period", values="new_value_row_pct").rename(columns=label)
    sns.heatmap(novel, annot=True, fmt=".2f", cmap="Blues", vmin=0, vmax=100, ax=axes[0, 1],
                linewidths=1, cbar_kws={"label": "기준 기간에 없던 값의 행 비율 (%)"})
    for ax in axes.flat:
        ax.tick_params(axis="y", labelrotation=0)
    axes[0, 0].set(title="수치형 분포 변화", xlabel="비교 기간", ylabel="피처")
    axes[0, 1].set(title="신규 범주를 가진 로그 비율", xlabel="비교 기간", ylabel="피처")
    finish(fig, "① 기간 대비 분포 변화 (전체 데이터)")
    strongest = numeric.nlargest(3, "PSI")
    obs = [f"{label(r.source_period)} / {LABELS.get(r.feature, r.feature)}: PSI {r.PSI:.3f}." for r in strongest.itertuples()]
    if not novel.empty:
        maximum = categorical.loc[categorical.source_period.ne(PERIODS[0])].nlargest(1, "new_value_row_pct").iloc[0]
        obs.append(f"{label(maximum.source_period)} / {maximum.feature}: 기준 기간에 없던 값을 가진 로그 {maximum.new_value_row_pct:.2f}%.")
    note(obs, "여러 피처와 업무 ID가 함께 바뀌면 처리 방식·업무 구성·수집 범위 변화(도메인 변화)를 우선 의심합니다. PSI는 분포 차이 지표이며 실제 이상 비율이 아닙니다. 상수 피처의 극소 PSI는 표본 크기와 평활화 때문일 수 있습니다.",
         "전체 기간 공통 모델과 업무/운영 환경별 기준 모델을 비교합니다. 새 범주 로그는 지연·정합성 증거를 함께 검토하며 신규 업무라는 이유만으로 장애로 확정하지 않습니다.")


def plot_correlation(frame, columns):
    corr = frame[columns].corr(method="spearman")
    fig, ax = plt.subplots(figsize=(max(8, len(columns) * 1.25), max(4, len(columns) * .8)))
    names = {x: LABELS.get(x, x) for x in columns}
    cmap = plt.get_cmap("RdBu_r").copy()
    cmap.set_bad("#E5E8EC")
    sns.heatmap(corr.rename(index=names, columns=names), annot=True, fmt=".2f", vmin=-1, vmax=1,
                cmap=cmap, linewidths=1, ax=ax, cbar_kws={"label": "Spearman ρ"})
    ax.tick_params(axis="y", labelrotation=0)
    ax.tick_params(axis="x", labelrotation=35)
    finish(fig, f"피처 상관관계 (기간별 균형 표본 {len(frame):,}건)")
    pairs = [(a, b, corr.loc[a, b]) for i, a in enumerate(columns) for b in columns[i + 1:] if pd.notna(corr.loc[a, b])]
    pairs.sort(key=lambda x: abs(x[2]), reverse=True)
    constants = [c for c in columns if frame[c].dropna().nunique() <= 1]
    note([f"가장 강한 관계: {a}–{b}, ρ={v:.3f}." for a, b, v in pairs[:2]]
         + [f"상수/유효값 부족으로 상관 계산 불가: {', '.join(constants) if constants else '없음'}."],
         "회색/빈 칸은 상관 0이 아니라 계산 불가입니다. 통합 표본의 상관은 기간 간 차이 때문에 커질 수도 있어 인과관계나 그룹 내부 관계로 바로 해석하지 않습니다.",
         "중복 피처를 줄이고 기간/업무별 상관도 대조합니다. 상수 피처 제거와 데이터 단위 확인 후 모델 입력을 결정합니다.")


def plot_model_scores(frame, result, profile):
    eligible = result.split.isin(["validation", "test"])
    from log_analysis_common import sample_by_period
    sample = sample_by_period(frame.loc[eligible, ["source_period"]], 30_000, 42).join(result)
    order = periods(sample)
    bins = np.linspace(result.loc[eligible, "anomaly_score"].min(), result.loc[eligible, "anomaly_score"].max(), 45)
    fig, axes = plt.subplots(1, len(order), figsize=(15, 4), sharex=True, sharey=True, squeeze=False)
    for ax, p in zip(axes.flat, order):
        g = sample.loc[sample.source_period.eq(p)]
        ax.hist(g.anomaly_score, bins=bins, weights=np.full(len(g), 100 / max(len(g), 1)), color=COLORS.get(p), alpha=.8)
        ax.axvline(profile["threshold"], color=RED, linestyle="--", linewidth=2, label=f"임계값 {profile['threshold']:.4f}")
        ax.set(title=f"{label(p)}\n검증·시험 표본 {len(g):,}건", xlabel="이상 점수 (높을수록 드문 패턴)", ylabel="표본 내 비율 (%)")
        ax.legend(fontsize=9)
    finish(fig, "비지도 모델 점수 분포 (전체 검증 점수로 임계값 결정)")
    test = result.split.eq("test")
    test_frame = frame.loc[test, ["source_period"]].join(result)
    plot_rule_rates(test_frame, {"anomaly_candidate": "시험 구간 모델 검토 후보"}, title="시험 구간 후보 비율")
    n, d = int(result.loc[test, "anomaly_candidate"].sum()), int(test.sum())
    note(f"시험 전체 {d:,}건 중 후보 {n:,}건 ({n / d:.2%}). 임계값 {profile['threshold']:.4f}; 검증 점수의 {profile['validation_quantile']:.0%} 분위수입니다.",
         "히스토그램은 표본이지만 시험 후보율은 전체 시험 로그로 계산했습니다. 99% 검증 분위수를 적용해도 기간 변화와 점수 동률 때문에 시험 후보율이 1%일 필요는 없습니다.",
         "이 비율은 정확도가 아니라 사람이 검토할 양입니다. 상태·업무·수집 변화와 후보를 교차 검토하고 실제 장애 확인 표본으로 precision/recall을 평가합니다.")


def plot_projection(projection, explained):
    limits = [projection[c].quantile([.005, .995]).to_numpy() for c in ["PC1", "PC2"]]
    limits = [v if v[0] < v[1] else np.array([v[0] - 1, v[1] + 1]) for v in limits]
    shown = projection.PC1.between(*limits[0]) & projection.PC2.between(*limits[1])
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharex=True, sharey=True)
    for p in periods(projection):
        g = projection.loc[shown & projection.source_period.eq(p)]
        axes[0].scatter(g.PC1, g.PC2, s=9, alpha=.28, color=COLORS.get(p), label=f"{label(p)} ({len(g):,})", rasterized=True)
    background = projection.loc[shown & ~projection.candidate]
    candidates = projection.loc[shown & projection.candidate]
    axes[1].scatter(background.PC1, background.PC2, s=8, color="#B6C0CB", alpha=.2, label="비후보", rasterized=True)
    axes[1].scatter(candidates.PC1, candidates.PC2, s=28, facecolors="none", edgecolors=RED,
                    linewidths=.8, label=f"후보 ({len(candidates):,}건)", zorder=5, rasterized=True)
    for ax, title in zip(axes, ["기간별 피처 공간", "같은 공간에서 모델 후보 강조"]):
        ax.set(title=title, xlabel="PC1", ylabel="PC2", xlim=limits[0], ylim=limits[1])
        ax.legend(loc="upper right", fontsize=9)
    finish(fig, f"PCA 중앙 분포 확대 (균형 표본 {len(projection):,}건, 설명 분산 {explained:.1%})")
    note(f"각 축의 표본 0.5–99.5% 범위를 확대했습니다. 화면 밖 {int((~shown).sum()):,}건 중 후보 {int((~shown & projection.candidate).sum()):,}건은 모델 계산에서 제외하지 않았습니다. 전체 투영 표본 후보 {int(projection.candidate.sum()):,}건.",
         "왼쪽은 기간 차이, 오른쪽은 후보의 위치를 보여줍니다. PCA 2축은 전체 피처 정보의 일부만 보존하므로 떨어진 군집이나 점 하나를 실제 이상으로 확정할 수 없습니다.",
         "분리된 기간은 도메인 변화의 단서로 보고 원래 피처와 업무 종류를 확인합니다. 화면 밖 후보도 상위 점수 표로 함께 검토합니다.")


def plot_dependency(frame):
    plot_rule_rates(frame, {"dependency_missing": "선행 로그 없음", "dependency_overlap_candidate": "선행 종료 전 시작"},
                    {"dependency_missing": "has_predecessor", "dependency_overlap_candidate": "dependency_time_comparable"},
                    "의존 관계: 로그 누락과 시간 겹침을 구분")
    references = frame.has_predecessor.sum()
    comparable = frame.dependency_time_comparable.sum()
    note(f"선행 관계 {references:,}건 중 시간 비교 가능 {comparable:,}건. 선행 로그 없음 {frame.dependency_missing.sum():,}건; 시간 겹침 {frame.dependency_overlap_candidate.sum():,}건.",
         "선행 로그 존재와 선행 종료시각 유무는 다른 조건입니다. 시간 겹침은 비동기·스트리밍 실행에서 정상일 수 있으므로 의존 순서 위반으로 단정하지 않습니다.",
         "선행 로그 누락, 재시도 연결 모호성, 시간 겹침을 별도 피처로 관리하고 업무별 동기/비동기 실행 정의와 비교합니다.")


def plot_merge_counts(frame):
    view = frame[["source_period"]].copy()
    view["결합 결과"] = np.select(
        [frame.no_process_logs, ~frame.count_comparable, frame.process_count_diff.lt(0), frame.process_count_diff.gt(0)],
        ["Process 로그 없음", "선언 수 결측", "관측 수 부족", "관측 수 초과"], default="선언 수 일치")
    plot_discrete(view, "결합 결과", top_n=6)
    plot_discrete(frame, "distinct_processes", top_n=6)
    note(f"Process 로그 없는 거래 {frame.no_process_logs.sum():,}건. 선언 수 비교 후보 {frame.count_mismatch_candidate.sum():,}건(로그 없는 거래와 중복 가능).",
         "결합 결과 막대는 중복 없는 상태 분류입니다. 관측 수가 적으면 로그 누락·업무 정의 차이·실행 미완료를 함께 확인해야 합니다. 재시도 행 수 대신 고유 Process 수를 비교했습니다.",
         "실행 구조 피처와 결합 누락 여부를 지연 피처와 결합하고, 수집 범위가 다른 로그를 확정 장애로 취급하지 않습니다.")


def plot_merge_status(frame):
    fig, axes = plt.subplots(1, len(periods(frame)), figsize=(15, 4.5), squeeze=False)
    for ax, p in zip(axes.flat, periods(frame)):
        g = frame.loc[frame.source_period.eq(p)]
        counts = pd.crosstab(text(g.status), g.process_derived_status)
        rates = counts.div(counts.sum(axis=1), axis=0).mul(100)
        annotations = rates.map(lambda v: f"{v:.1f}%") + "\n(" + counts.map(lambda n: f"{n:,}") + ")"
        sns.heatmap(rates, annot=annotations, fmt="", vmin=0, vmax=100, cmap="Blues", ax=ax,
                    cbar=False, linewidths=1, annot_kws={"fontsize": 9})
        ax.tick_params(axis="y", labelrotation=0)
        ax.set(title=label(p), xlabel="최종 Process 집계 상태", ylabel="Transaction 상태")
    finish(fig, "상태별 결합 결과 (행 단위 100%, 괄호는 전체 건수)")
    plot_rule_rates(frame, {"status_mismatch_candidate": "S/F 상태 불일치"},
                    {"status_mismatch_candidate": "status_comparable"}, "상태 정합성")
    note(f"양쪽이 S/F인 {frame.status_comparable.sum():,}건 중 상태 불일치 {frame.status_mismatch_candidate.sum():,}건.",
         "거래 상태 한 행을 100%로 정규화해 드문 상태도 읽을 수 있습니다. NO_PROCESS/OTHER는 S/F 불일치 계산에서 제외됩니다. 건수가 적은 상태의 100%는 대표성에 주의해야 합니다.",
         "부모 거래와 실행 단계의 상태 일관성을 사후 검토 근거로 사용하고, H/Z/P/N의 운영 정의 없이 실패 라벨로 합치지 않습니다.")


def plot_merge_performance(frame, sample):
    order = periods(frame)
    fig, axes = plt.subplots(1, len(order), figsize=(15, 4.4), sharex=True, sharey=True, squeeze=False)
    valid = sample.loc[sample.duration_ms.gt(0) & sample.bottleneck_duration_ms.gt(0)]
    for ax, p in zip(axes.flat, order):
        g = valid.loc[valid.source_period.eq(p)]
        ax.scatter(g.duration_ms, g.bottleneck_duration_ms, color=COLORS.get(p), s=9, alpha=.23, rasterized=True)
        lo = max(.1, min(valid.duration_ms.min(), valid.bottleneck_duration_ms.min()))
        hi = max(valid.duration_ms.max(), valid.bottleneck_duration_ms.max()) * 1.2
        ax.plot([lo, hi], [lo, hi], color=RED, linestyle="--", label="동일 시간 (y=x)")
        native_axis(ax, positive=True)
        native_axis(ax, "y", positive=True)
        ax.set(title=f"{label(p)} / 유효 표본 {len(g):,}건", xlabel="거래 처리시간 (ms)", ylabel="최대 Process 처리시간 (ms)")
        ax.legend(fontsize=9)
    finish(fig, "거래 시간과 병목 Process 시간 (기간별 균형 표본)")
    note([f"{label(p)}: 전체 오버헤드 중앙값 {number(g.orchestration_overhead_ms.median())}ms, Process 시간 합/거래 시간 중앙값 {g.parallelism_ratio.median():.3f}." for p, g in frame.groupby("source_period", observed=True)],
         "y=x 근처는 거래 시간이 가장 긴 Process 시간과 비슷합니다. 아래쪽은 대기·여러 단계 실행·오케스트레이션 시간 등이 포함될 수 있습니다. 두 시계와 로그 범위가 같다는 가정도 확인해야 합니다.",
         "총 거래 시간 외에 병목 시간·오버헤드·실행 구조를 입력해 어떤 단계가 지연을 만들었는지 설명합니다.")
    plot_continuous(frame, sample, "parallelism_ratio")
    plot_rule_rates(frame, {"boundary_candidate": "거래 시간 범위 밖 Process"}, title="시간 경계 점검")
    note("음수 오버헤드 또는 Process가 거래 범위를 벗어난 후보는 실행 시각/로그 정의를 함께 점검합니다.",
         "Process 시간 합/거래 시간은 실제 CPU 병렬도와 같지 않습니다. 1 초과는 병렬 실행으로 설명될 수 있고 시간 경계 후보도 비동기 실행에선 정상일 수 있습니다.",
         "오버헤드는 부호를 보존한 log1p 피처로 사용하고, 경계 후보는 모델의 정답이 아닌 별도 검토 근거로 유지합니다.")


def plot_sequences(sequences, sampled_transactions):
    plot_categories(sequences.reset_index(), ["sequence"], top_n=4, population="시퀀스 생성 표본 거래")
    coverage = sampled_transactions.groupby("source_period", observed=True).size().to_frame("추출 거래 수")
    coverage["시퀀스 생성 거래 수"] = sequences.groupby(level="source_period", observed=True).size()
    coverage["시퀀스 없음"] = coverage["추출 거래 수"] - coverage["시퀀스 생성 거래 수"].fillna(0)
    display(coverage.rename(index=label))
    note(f"표본 거래 {len(sampled_transactions):,}건 중 시퀀스 생성 {len(sequences):,}건. Process 로그 없는 표본은 시퀀스 빈도의 분모에서 제외했습니다.",
         "시퀀스 순위는 전체 거래 통계가 아닌 표본에서 관측한 실행 흐름입니다. 시작 시각 순서와 실제 의존 관계의 순서는 다를 수 있습니다.",
         "빈도 낮은 시퀀스를 신규 업무/정상 변형/실행 누락으로 나눠 검토하고, 전체 데이터 또는 학습 구간에서 추정한 시퀀스 기준선으로 확장합니다.")

"""Read-only post-analysis helpers. Summaries use full rows; plots label sampling."""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import font_manager
from matplotlib.ticker import StrMethodFormatter

from .data import JOIN_KEYS, chronological_split
from .rules import RULE_DESCRIPTIONS, RULE_NAMES

COLORS = {"train": "#4169A1", "calibration": "#6F9E90", "threshold": "#A87C39", "test": "#D25B55"}
PERIOD_COLORS = ("#477CC1", "#59A794", "#E39A3B")
DURATION_BINS = np.array([0, .01, .1, .5, 1, 2, 5, 10, 30, 60, 300, np.inf])
DURATION_LABELS = ["0–0.01", "0.01–0.1", "0.1–0.5", "0.5–1", "1–2", "2–5", "5–10", "10–30", "30–60", "60–300", "300 이상"]
COUNT_BINS = np.array([0, 1, 10, 100, 1000, 10000, 100000, 1000000, np.inf])
COUNT_LABELS = ["0", "1–9", "10–99", "100–999", "1,000–9,999", "10,000–99,999", "100,000–999,999", "1,000,000 이상"]


def _period_name(period):
    names = {"25년12월24일-26년4월6일": "초기", "26년8월15일-26년9월14일": "중기", "26년9월30일-26년10월2일": "최근"}
    return names.get(str(period), str(period))


def bucket_percent(values, edges):
    """Exact full-population counts. Unequal-width categories are NOT densities."""
    a = np.asarray(values, dtype=float)
    a = a[np.isfinite(a) & (a >= 0)]
    counts, _ = np.histogram(a, bins=edges)
    return counts * 100 / max(1, len(a)), len(a)


def _plain_axes(ax, *, percent=False):
    ax.set_axisbelow(True)
    ax.grid(axis="y", color="#E6EAF0", linewidth=.7)
    ax.set_ylim(bottom=0)
    if percent:
        ax.yaxis.set_major_formatter(StrMethodFormatter("{x:g}%"))


def adapter_model_support(pipeline):
    """Saved fit support, not raw volumes or an IF algorithm minimum."""
    reasons = {"insufficient_train": "학습 후보 부족", "insufficient_calibration": "정규화 검증 부족",
               "insufficient_threshold": "판정선 검증 부족", "insufficient_days": "학습 날짜 부족"}
    rows = []
    for name, model in pipeline.adapter_models.items():
        s = model.support
        rows.append({"Adapter": name, "모델": "생성", "학습 후보": s["train_rows"],
                     "정규화 검증": s["calibration_rows"], "판정선 검증": s["threshold_rows"],
                     "학습 일수": s["train_days"], "제외 이유": "—"})
    for s in pipeline.training_summary.get("skipped_adapters", []):
        rows.append({"Adapter": s["adapter"], "모델": "미생성", "학습 후보": s["train_rows"],
                     "정규화 검증": s["calibration_rows"], "판정선 검증": s["threshold_rows"],
                     "학습 일수": s["train_days"], "제외 이유": ", ".join(reasons.get(v, v) for v in s["reasons"])})
    columns = ["Adapter", "모델", "학습 후보", "정규화 검증", "판정선 검증", "학습 일수", "제외 이유"]
    return pd.DataFrame(rows, columns=columns).sort_values(["모델", "학습 후보"], ascending=[False, False]).reset_index(drop=True)


def set_style():
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ("Malgun Gothic", "Noto Sans CJK KR", "NanumGothic"):
        if name in available:
            plt.rcParams["font.family"] = name
            break
    plt.rcParams.update({"axes.unicode_minus": False, "figure.dpi": 110, "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.titleweight": "bold", "axes.grid": False,
                         "figure.facecolor": "white", "axes.facecolor": "white"})


def rule_audit(result):
    rows = []
    for name in RULE_NAMES:
        flag = result["rule_" + name]
        evaluated, hits = int(flag.notna().sum()), int(flag.fillna(False).sum())
        rows.append({"규칙": name, "의미": RULE_DESCRIPTIONS[name], "전체 행": len(flag),
                     "평가 가능": evaluated, "미평가": int(flag.isna().sum()), "탐지 행": hits,
                     "평가 가능 중 탐지 %": 100 * hits / evaluated if evaluated else np.nan})
    return pd.DataFrame(rows)


def outcome_summary(result, by="source_period"):
    work = result.copy(deep=False)
    groups = work.groupby(by, observed=True, dropna=False)
    table = groups.agg(전체_행=("anomaly_candidate", "size"), IF_평가_행=("model_eligible", "sum"),
                       규칙_후보=("rule_candidate", "sum"), IF_후보=("model_candidate", "sum"),
                       최종_후보=("anomaly_candidate", "sum"), 명시적_실패=("reported_failure", "sum"),
                       품질_오류=("quality_error", "sum"))
    table["IF 후보 / 평가 행 %"] = table.IF_후보.div(table.IF_평가_행.replace(0, np.nan)).mul(100)
    table["최종 후보 / 전체 행 %"] = table.최종_후보.div(table.전체_행).mul(100)
    table["IF 미평가 행"] = table.전체_행 - table.IF_평가_행
    return table.reset_index()


def relation_summary(processes, result):
    # One transaction attempt can flag multiple Process rows. Show BOTH units.
    rows = []
    for name in ("process_count_mismatch", "tx_success_all_process_failed"):
        flag = result["rule_" + name]
        rows.append({"규칙": name, "평가 Process 행": int(flag.notna().sum()),
                     "탐지 Process 행": int(flag.fillna(False).sum()),
                     "평가 거래 시도": len(processes.loc[flag.notna(), list(JOIN_KEYS)].drop_duplicates()),
                     "탐지 거래 시도": len(processes.loc[flag.fillna(False), list(JOIN_KEYS)].drop_duplicates())})
    return pd.DataFrame(rows)


def numeric_summary(processes, by="source_period"):
    rows = []
    for group, part in processes.groupby(by, observed=True, dropna=False):
        for column in ("duration_ms", "totalCount", "retryCount"):
            values = part[column].dropna()
            rows.append({by: group, "변수": column, "전체 행": len(part), "비결측": len(values),
                         "결측 %": 100 * (1 - len(values) / len(part)),
                         "음수 행": int(values.lt(0).sum()), "0 비중 %": 100 * values.eq(0).mean(),
                         "중앙값": values.median(), "P95": values.quantile(.95),
                         "P99": values.quantile(.99), "P99.9": values.quantile(.999), "최댓값": values.max()})
    return pd.DataFrame(rows)


def experiment_split(processes, pipeline):
    return chronological_split(processes, pipeline.config)


def reference_mask(result, pipeline):
    flags = result[["rule_" + name for name in RULE_NAMES]].rename(columns=lambda c: c.removeprefix("rule_"))
    # Exact same reference eligibility as fit; status/eligibility are already in result.
    from .rules import CONSISTENCY_RULES
    allowed = (result.model_eligible & result.status.eq("S") &
               ~flags[list(CONSISTENCY_RULES)].fillna(False).any(axis=1))
    if pipeline.config.exclude_statistical_from_training:
        allowed &= ~flags.statistical_tail.fillna(False)
    return allowed


def bounded_sample(frame, by, maximum=30_000):
    groups = list(frame.groupby(by, observed=True, dropna=False))
    if not groups:
        return frame.iloc[:0]
    quota = max(1, maximum // len(groups))
    return pd.concat([g.sample(min(len(g), quota), random_state=42) for _, g in groups])


def plot_population(processes):
    counts = processes.groupby(["adapterType", "source_period"], observed=True).size().unstack(fill_value=0)
    top = counts.sum(axis=1).nlargest(10).index
    shown = counts.loc[top].copy()
    remainder = counts.loc[~counts.index.isin(top)].sum()
    if remainder.sum():
        shown.loc["기타 Adapter 합계"] = remainder
    pct = shown.div(processes.groupby("source_period", observed=True).size(), axis=1).mul(100)
    fig, axes = plt.subplots(1, 2, figsize=(16, 7), layout="constrained")
    totals = shown.sum(axis=1)
    y = np.arange(len(totals))
    axes[0].barh(y, totals / 10_000, color=PERIOD_COLORS[0], height=.65)
    axes[0].set_yticks(y, totals.index.astype(str))
    axes[0].invert_yaxis()
    axes[0].set_xlim(0, max(1, totals.max() / 10_000 * 1.23))
    for i, n in enumerate(totals):
        axes[0].text(n / 10_000, i, f"  {int(n):,}", va="center", fontsize=9)
    axes[0].set_xlabel("Process 행 수 (만 행, 일반 축)")
    axes[0].set_title("Adapter별 전체 행 수")
    width = .75 / len(pct.columns)
    for j, period in enumerate(pct.columns):
        offset = (j - (len(pct.columns) - 1) / 2) * width
        axes[1].barh(y + offset, pct[period], height=width * .9,
                     color=PERIOD_COLORS[j % 3], label=_period_name(period))
    axes[1].set_yticks(y, pct.index.astype(str))
    axes[1].invert_yaxis()
    axes[1].set_xlim(0, 105)
    axes[1].set_xlabel("해당 기간 전체 Process 중 비중 (%)")
    axes[1].set_title("기간별 Adapter 비중")
    axes[1].legend(loc="lower right", title="수집 기간")
    return fig


def plot_training_mix(processes, result, split, pipeline):
    raw = processes.adapterType.astype("string").fillna("MISSING").value_counts()
    ref = processes.loc[split.eq("train") & reference_mask(result, pipeline), "adapterType"].astype("string").fillna("MISSING").value_counts()
    sampled = pd.Series(pipeline.global_model.support["sample_adapter_counts"], dtype=float)
    table = pd.concat({"전체 원본": raw, "학습 기준집단": ref, "실제 GLOBAL 학습 표본": sampled}, axis=1).fillna(0)
    top = raw.nlargest(10).index
    kept = table.reindex(top).copy()
    kept.loc["기타 합계"] = table.loc[~table.index.isin(top)].sum()
    pct = kept.div(table.sum(), axis=1).mul(100)
    fig, ax = plt.subplots(figsize=(13, 6.5), layout="constrained")
    pct.plot.barh(ax=ax, width=.75, color=["#4169A1", "#6F9E90", "#A87C39"])
    ax.invert_yaxis()
    ax.set_title("모집단·정상 후보 기준집단·실제 학습 표본은 서로 다름")
    ax.set_xlabel("각 집단 내 Adapter 비중 (%)")
    ax.set_ylabel("")
    ax.set_xlim(left=0)
    ax.legend(title="분모: 각 집단 전체 행", loc="lower right")
    return fig, table


def plot_duration_distributions(processes, split, pipeline):
    names = list(pipeline.adapter_models)[:4]
    if not names:
        names = list(processes.adapterType.value_counts().head(3).index)
    fig, axes = plt.subplots(len(names), 2, figsize=(16, 3.8 * len(names)), squeeze=False, layout="constrained")
    book = pipeline.quantile_book.table()
    for row, adapter in enumerate(names):
        selected = processes.loc[processes.adapterType.eq(adapter) & processes.duration_ms.ge(0) &
                                 processes.status.isin(["S", "F", "H", "Z"]), ["duration_ms"]].copy()
        selected["split"] = split.loc[selected.index]
        selected = selected.loc[selected.split.isin(["train", "test"])]
        cuts = []
        for q in (.99, .999):
            if book.empty:
                continue
            local = book.loc[book.group.eq(adapter) & book.metric.eq("duration_ms") & book["quantile"].eq(q)]
            if local.empty:
                local = book.loc[book.group.eq("GLOBAL") & book.metric.eq("duration_ms") & book["quantile"].eq(q)]
            if not local.empty:
                r = local.iloc[0]
                cuts.append(f"P{q * 100:g} {r.threshold / 1000:,.3f}초 ({r.group})")
        ymax = 1
        for col, name in enumerate(("train", "test")):
            ax = axes[row, col]
            pct, n = bucket_percent(selected.loc[selected.split.eq(name), "duration_ms"] / 1000, DURATION_BINS)
            ax.bar(np.arange(len(pct)), pct, color=PERIOD_COLORS[0 if name == "train" else 2], width=.8)
            for i, v in enumerate(pct):
                if v >= .5:
                    ax.text(i, v, f"{v:.1f}%", ha="center", va="bottom", fontsize=9)
            ymax = max(ymax, pct.max() * 1.17)
            ax.set_xticks(range(len(pct)), DURATION_LABELS, rotation=40, ha="right", fontsize=9)
            ax.set_title(f"{adapter} / {'학습 구간' if name == 'train' else '미래 test'} · n={n:,}")
            ax.set_xlabel("처리시간 구간 (초)")
            ax.set_ylabel("완료·유효시간 행 중 구간 비중")
            _plain_axes(ax, percent=True)
            ax.text(.99, .96, "\n".join(cuts), transform=ax.transAxes, ha="right", va="top", fontsize=9,
                    bbox={"facecolor": "white", "alpha": .85, "edgecolor": "none"})
        for ax in axes[row]:
            ax.set_ylim(0, min(110, ymax))
    return fig


def plot_input_features(processes):
    groups = list(processes.groupby("source_period", observed=True))
    fig, axes = plt.subplots(4, len(groups), figsize=(5.8 * len(groups), 12), squeeze=False,
                             sharey="row", layout="constrained")
    row_max = np.ones(4)
    for col, (period, part) in enumerate(groups):
        color = PERIOD_COLORS[col % 3]
        label = _period_name(period)
        pct, n = bucket_percent(part.totalCount.dropna(), COUNT_BINS)
        row_max[0] = max(row_max[0], pct.max())
        ax = axes[0, col]
        ax.bar(range(len(pct)), pct, color=color, width=.8)
        for i, v in enumerate(pct):
            if v >= .5:
                ax.text(i, v, f"{v:.1f}%", ha="center", va="bottom", fontsize=9)
        ax.set_xticks(range(len(pct)), COUNT_LABELS, rotation=40, ha="right", fontsize=9)
        ax.set_title(f"{label} / 처리건수 · 유효 n={n:,}")
        ax.set_xlabel("totalCount 구간 (건)")
        retry = part.retryCount.dropna()
        retry = retry.loc[retry.ge(0) & retry.mod(1).eq(0)].value_counts().sort_index()
        ax = axes[1, col]
        values = retry / max(1, retry.sum()) * 100
        if len(values):
            row_max[1] = max(row_max[1], values.max())
        ax.bar(range(len(retry)), values, color=color, width=.7)
        ax.set_xticks(range(len(retry)), [f"{int(k)}회" for k in retry.index])
        ax.set_title(f"{label} / 재시도 · 유효 n={int(retry.sum()):,}")
        for i, v in enumerate(values):
            ax.text(i, v, f"{v:.1f}%", ha="center", va="bottom", fontsize=9)
        ax.set_xlabel("retryCount")
        times = part.startTime.dropna()
        for row, timevalues, domain in ((2, times.dt.hour, range(24)), (3, times.dt.dayofweek, range(7))):
            ax = axes[row, col]
            counts = timevalues.value_counts().reindex(domain, fill_value=0)
            pct = counts / max(1, len(times)) * 100
            row_max[row] = max(row_max[row], pct.max())
            ax.bar(counts.index, pct, color=color, width=.8)
            ax.set_title(f"{label} / {'시간대' if row == 2 else '요일'} · 유효 n={len(times):,}")
            if row == 2:
                ax.set_xticks(range(0, 24, 3))
                ax.set_xlabel("시작 시간대 (KST)")
            else:
                ax.set_xticks(range(7), list("월화수목금토일"))
                ax.set_xlabel("시작 요일 (KST)")
        for row in range(4):
            _plain_axes(axes[row, col], percent=True)
    for row in range(4):
        axes[row, 0].set_ylabel("해당 기간 유효 행 중 비중")
        # set_ylim in _plain_axes disables autoscale; calculate across ALL periods.
        for ax in axes[row]:
            ax.set_ylim(0, min(110, row_max[row] * 1.15))
    return fig


def plot_model_scores(result, split, pipeline, *, reference_only=False):
    mask = result.model_eligible.copy()
    if reference_only:
        mask &= reference_mask(result, pipeline)
    work = result.loc[mask, ["adapterType", "ensemble_score", "model_threshold", "model_route"]].copy()
    work["split"] = split.loc[work.index]
    work["route_group"] = work.adapterType.astype("string").where(work.model_route.eq("global+adapter"), "GLOBAL")
    routes = ["GLOBAL", *pipeline.adapter_models]
    fig, axes = plt.subplots(len(routes), 2, figsize=(16, 3.3 * len(routes)), squeeze=False, layout="constrained")
    for row, route in enumerate(routes):
        group = work.loc[work.route_group.eq(route) & work.split.isin(["threshold", "test"])]
        sample = bounded_sample(group, "split", maximum=20_000)
        threshold = pipeline.route_thresholds[route]
        if not sample.empty:
            low, high = min(sample.ensemble_score.min(), threshold), max(sample.ensemble_score.max(), threshold)
        else:
            low, high = threshold - 1, threshold + 1
        edges = np.linspace(low - .05, high + .05, 31)
        ymax = 1
        for col, name in enumerate(("threshold", "test")):
            ax = axes[row, col]
            if not sample.empty:
                values = sample.loc[sample.split.eq(name), "ensemble_score"]
                if len(values):
                    heights, _, _ = ax.hist(values, bins=edges, weights=np.full(len(values), 100 / len(values)),
                                            histtype="bar", rwidth=.92, color=PERIOD_COLORS[0 if col == 0 else 2],
                                            edgecolor="white", linewidth=.4)
                    ymax = max(ymax, heights.max() * 1.17)
            ax.axvspan(threshold, edges[-1], color="#FCE7E7", alpha=.6, zorder=0)
            ax.axvline(threshold, color="#B94034", linestyle="--", linewidth=2, label=f"판정선 {threshold:.4f}")
            full_n = int(group.split.eq(name).sum())
            sample_n = int(sample.split.eq(name).sum()) if not sample.empty else 0
            ax.set_title(f"{route} / {'검증' if col == 0 else '미래 test'} · 전체 {full_n:,}, 그림 {sample_n:,}")
            ax.set_xlim(edges[0], edges[-1])
            ax.set_xlabel("IF ensemble 점수 (선 오른쪽이 후보)")
            ax.set_ylabel("각 그림 표본 내 비중")
            ax.xaxis.set_major_formatter(StrMethodFormatter("{x:.1f}"))
            _plain_axes(ax, percent=True)
            ax.legend(loc="upper left", fontsize=9)
        for ax in axes[row]:
            ax.set_ylim(0, ymax)
    return fig


def plot_rule_audit(result):
    audit = rule_audit(result)
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.5), layout="constrained")
    y = np.arange(len(audit))
    axes[0].barh(y, audit["평가 가능"] / len(result) * 100, color="#4169A1", label="평가 가능")
    axes[0].barh(y, audit["미평가"] / len(result) * 100, left=audit["평가 가능"] / len(result) * 100, color="#DADFE6", label="미평가")
    axes[0].set_yticks(y, audit["규칙"])
    axes[0].set_xlabel("전체 Process 중 비중 (%)")
    axes[0].set_title("False(불일치 없음)와 NA(판단 불가)를 구분")
    axes[0].legend(loc="lower right")
    axes[1].barh(y, audit["탐지 행"], color="#D25B55")
    axes[1].set_yticks(y, audit["규칙"])
    axes[1].set_xlabel("규칙 탐지 Process 행 수 (일반 축)")
    axes[1].set_xlim(0, max(1, audit["탐지 행"].max()) * 1.22)
    axes[1].set_title("규칙별 후보 수 — 중복되므로 합산하지 않음")
    for i, n in enumerate(audit["탐지 행"]):
        axes[1].text(n, i, f" {n:,}", va="center", fontsize=9)
    return fig


def plot_status(result):
    table = outcome_summary(result, "status")
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), layout="constrained")
    axes[0].bar(table.status.astype(str), table["IF_평가_행"], color="#4169A1", label="IF 평가 가능")
    axes[0].bar(table.status.astype(str), table["IF 미평가 행"], bottom=table["IF_평가_행"], color="#DADFE6", label="IF 미평가")
    axes[0].set_title("상태별 행 수 및 IF 평가 범위 (전수)")
    axes[0].set_ylabel("Process 행 수")
    axes[0].yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    for i, n in enumerate(table["전체_행"]):
        axes[0].text(i, n, f"{int(n):,}", ha="center", va="bottom", fontsize=9)
    axes[0].set_ylim(0, max(1, table["전체_행"].max()) * 1.2)
    axes[0].legend()
    # Pandas nullable Float64 contains pd.NA for unassessed statuses; matplotlib needs NaN.
    rates = table["IF 후보 / 평가 행 %"].astype("float64")
    axes[1].bar(table.status.astype(str), rates, color="#D25B55")
    for i, v in enumerate(rates):
        axes[1].text(i, 0 if pd.isna(v) else v, "미평가" if pd.isna(v) else f"{v:.2f}%", ha="center", va="bottom")
    axes[1].set_ylabel("해당 상태의 IF 평가 가능 행 중 IF 후보 %")
    axes[1].set_title("STATUS는 정답 라벨이 아님: 성능 지표로 해석 금지")
    axes[1].set_ylim(0, max(.1, rates.max() * 1.25 if rates.notna().any() else .1))
    return fig


def plot_daily(result, processes, split):
    cols = ["model_candidate", "model_eligible", "anomaly_candidate", "rule_candidate"]
    work = result[cols].copy()
    work["day"] = processes.startTime.dt.normalize()
    work["period"] = processes.source_period
    groups = list(work.groupby("period", observed=True))
    fig, axes = plt.subplots(len(groups), 1, figsize=(13, 3.2 * len(groups)), squeeze=False, layout="constrained")
    tables = []
    for (period, part), ax in zip(groups, axes[:, 0]):
        table = part.groupby("day").agg(rows=("anomaly_candidate", "size"), assessed=("model_eligible", "sum"),
                                       model=("model_candidate", "sum"), rules=("rule_candidate", "sum"))
        table["IF 후보 %"] = (100 * table.model / table.assessed.replace(0, np.nan)).astype("float64")
        table["규칙 후보 %"] = (100 * table.rules / table.rows).astype("float64")
        if table.empty:
            ax.text(.5, .5, "유효 시작시각 없음: 일별 분포 미평가", ha="center", transform=ax.transAxes)
            table["period"] = period
            tables.append(table.reset_index())
            continue
        positions = np.arange(len(table))
        ax.bar(positions - .19, table["IF 후보 %"], width=.36, color="#D25B55", label="IF 후보 / IF 평가 행")
        ax.bar(positions + .19, table["규칙 후보 %"], width=.36, color="#4169A1", label="규칙 후보 / 시작시각 있는 전체 행")
        ticks = np.unique(np.r_[np.arange(0, len(table), max(1, int(np.ceil(len(table) / 12)))), len(table) - 1])
        ax.set_xticks(ticks, table.index[ticks].strftime("%m-%d"), rotation=35, ha="right")
        _plain_axes(ax, percent=True)
        ax.set_title(str(period) + " — 관측 날짜만 표시 (누락 날짜를 0으로 보지 않음)")
        ax.set_ylabel("후보 비중 (%)")
        ax.legend(fontsize=9)
        table["period"] = period
        tables.append(table.reset_index())
    return fig, pd.concat(tables, ignore_index=True)


def plot_test_score_timeline(processes, result, split, pipeline):
    work = result.loc[split.eq("test") & result.model_eligible, ["adapterType", "model_route", "score_margin"]].copy()
    work["startTime"] = processes.loc[work.index, "startTime"]
    work["route"] = work.adapterType.astype("string").where(work.model_route.eq("global+adapter"), "GLOBAL")
    sampled = bounded_sample(work, "route", maximum=10_000)
    names = sampled.route.unique()
    fig, axes = plt.subplots(max(1, len(names)), 1, figsize=(13, 3 * max(1, len(names))), squeeze=False, layout="constrained")
    for route, ax in zip(names, axes[:, 0]):
        part = sampled.loc[sampled.route.eq(route)]
        ax.scatter(part.startTime, part.score_margin, s=7, alpha=.45,
                   c=np.where(part.score_margin.gt(0), "#D25B55", "#4169A1"), rasterized=True)
        ax.axhline(0, color="#B94034", linestyle="--", linewidth=1.8)
        ax.set_title(f"미래 test / {route} — 전수 n={int(work.route.eq(route).sum()):,}, 그림 n={len(part):,}")
        ax.set_ylabel("점수 - 경로 임계값\n0보다 클 때 IF 후보")
        locator = mdates.AutoDateLocator(minticks=3, maxticks=6)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    if not len(names):
        axes[0, 0].text(.5, .5, "평가 가능한 test 없음", ha="center", transform=axes[0, 0].transAxes)
    return fig

"""Reader-facing, full-population post-analysis. Never changes detector decisions."""
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.ticker import StrMethodFormatter

from .analysis import (DURATION_BINS, DURATION_LABELS, COUNT_BINS, COUNT_LABELS,
                       bucket_percent, reference_mask)
from .rules import RULE_NAMES

RULE_LABELS = {
    "status_failed": "실패 상태 F",
    "tx_success_all_process_failed": "부모 S / 기대 단계 전부 F",
    "process_count_mismatch": "부모 기대 단계 수 불일치",
    "success_with_error": "성공 S인데 오류 건수 존재",
    "count_conservation": "전체 ≠ 성공 + 오류 건수",
    "final_missing_end": "완료 상태인데 종료시각 결측",
    "missing_sql_required": "SQL 필수 컬럼 결측",
    "negative_duration": "종료시각이 시작보다 앞섬",
    "invalid_count_domain": "건수·재시도 음수/소수",
    "statistical_tail": "고정 P99/P99.9 초과",
    "invalid_input": "입력 파싱·상태·PK 오류",
}
METRICS = {"duration_ms": ("처리시간", "초", 1000),
           "totalCount": ("처리건수", "건", 1), "retryCount": ("재시도", "회", 1)}


def detection_counts(result):
    rule, model, eligible = result.rule_candidate, result.model_candidate, result.model_eligible
    counts = {"input": len(result), "rules": int(rule.sum()), "after_rules": int((~rule).sum()),
              "after_rules_assessed": int((~rule & eligible).sum()),
              "after_rules_unassessed": int((~rule & ~eligible).sum()),
              "rule_only": int((rule & ~model).sum()), "both": int((rule & model).sum()),
              "if_only": int((~rule & model).sum()), "if_all": int(model.sum()),
              "no_candidate_assessed": int((~rule & ~model & eligible).sum()),
              "candidates": int((rule | model).sum())}
    assert counts["input"] == (counts["rule_only"] + counts["both"] + counts["if_only"] +
                                counts["no_candidate_assessed"] + counts["after_rules_unassessed"])
    assert counts["after_rules"] == counts["after_rules_assessed"] + counts["after_rules_unassessed"]
    return counts


def flow_table(result):
    c = detection_counts(result)
    return pd.DataFrame([
        ["1. 전체 입력", c["input"], 0, c["input"], "원본 Process 실행 행 수"],
        ["2. 규칙 후보 별도 분리", c["input"], c["rules"], c["after_rules"], "규칙 하나 이상 True; 중복 행은 한 번"],
        ["3. IF 미평가 별도 분리", c["after_rules"], c["after_rules_unassessed"], c["after_rules_assessed"], "N/P·무효 시간 등; 정상 판정 아님"],
        ["4. IF 추가 후보 별도 분리", c["after_rules_assessed"], c["if_only"], c["no_candidate_assessed"], "규칙 미탐지인데 IF 점수가 판정선을 초과"],
    ], columns=["단계", "단계 전 행", "이번 단계 분리 행", "단계 후 잔여 행", "무엇을 센 것인가"])


def partition_table(result):
    c = detection_counts(result)
    names = [("규칙만 후보", "rule_only"), ("규칙과 IF 모두 후보", "both"),
             ("IF만 추가 후보", "if_only"), ("둘 다 미탐지 / IF 평가", "no_candidate_assessed"),
             ("규칙 미탐지 / IF 미평가", "after_rules_unassessed")]
    return pd.DataFrame([{"구분": name, "행 수": c[key],
                          "전체 입력 중 %": 100 * c[key] / c["input"] if c["input"] else np.nan}
                         for name, key in names])


def rule_removal_table(result):
    alive = pd.Series(True, index=result.index)
    rows = []
    for order, name in enumerate(RULE_NAMES, 1):
        flags = result["rule_" + name]
        hit = flags.fillna(False)
        before = int(alive.sum())
        added = int((alive & hit).sum())
        unknown = int((alive & flags.isna()).sum())
        alive &= ~hit
        rows.append({"순서": order, "규칙": RULE_LABELS[name], "독립 탐지 행": int(hit.sum()),
                     "이번 단계 추가 분리": added, "앞 단계와 중복": int(hit.sum()) - added,
                     "단계 후 잔여 행": int(alive.sum()), "단계 전 미평가 행": unknown,
                     "단계 전 행": before})
    assert alive.equals(~result.rule_candidate)
    return pd.DataFrame(rows)


def group_filter_counts(result, by="source_period"):
    work = pd.DataFrame({by: result[by], "원본 행": 1, "규칙 후보": result.rule_candidate.astype(int),
                         "규칙 후 잔여": (~result.rule_candidate).astype(int),
                         "잔여 IF 평가": (~result.rule_candidate & result.model_eligible).astype(int),
                         "잔여 IF 미평가": (~result.rule_candidate & ~result.model_eligible).astype(int),
                         "IF 추가 후보": (~result.rule_candidate & result.model_candidate).astype(int),
                         "최종 OR 후보": result.anomaly_candidate.astype(int)})
    return work.groupby(by, observed=True, dropna=False).sum().reset_index()


def training_filter_table(processes, result, split, pipeline):
    conditions = [("과거 train만 선택", split.eq("train")),
                  ("S만 학습 후보로 선택", result.status.eq("S").fillna(False)),
                  ("유효 완료시간·품질 조건", result.model_eligible),
                  ("정합성 후보 제외", ~result.consistency_candidate)]
    if pipeline.config.exclude_statistical_from_training:
        conditions.append(("통계 꼬리 후보 제외", ~result.statistical_candidate))
    alive = pd.Series(True, index=result.index)
    rows = []
    for name, condition in conditions:
        before = int(alive.sum())
        alive &= condition
        rows.append({"학습 단계": name, "단계 전 행": before, "이번 단계 제외": before - int(alive.sum()),
                     "남은 학습 후보": int(alive.sum())})
    expected = reference_mask(result, pipeline) & split.eq("train")
    assert alive.equals(expected)
    assert int(alive.sum()) == pipeline.global_model.support["train_rows"]
    return pd.DataFrame(rows)


def adapter_training_counts(processes, result, split, pipeline):
    train = split.eq("train")
    ref = reference_mask(result, pipeline)
    work = pd.DataFrame({"Adapter": processes.adapterType, "세 기간 원본": 1,
                         "원본 train": train.astype(int),
                         "train N": (train & result.status.eq("N").fillna(False)).astype(int),
                         "train S": (train & result.status.eq("S").fillna(False)).astype(int),
                         "최종 학습 후보": (train & ref).astype(int)})
    return work.groupby("Adapter", observed=True, dropna=False).sum().reset_index().sort_values("세 기간 원본", ascending=False)


def _count_bars(ax, labels, counts, title, color="#477CC1"):
    y = np.arange(len(labels))
    ax.barh(y, counts, color=color, height=.62)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlim(0, max(1, max(counts, default=0)) * 1.28)
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.set_title(title)
    ax.set_xlabel("Process 행 수")
    for i, n in enumerate(counts):
        ax.text(n, i, f"  {n:,}", va="center", fontsize=9)


def plot_flow(result):
    table = flow_table(result)
    fig, axes = plt.subplots(1, 2, figsize=(15, 4.5), layout="constrained")
    _count_bars(axes[0], table["단계"].tolist(), table["단계 후 잔여 행"].tolist(), "단계별 잔여 데이터")
    _count_bars(axes[1], table["단계"].iloc[1:].tolist(), table["이번 단계 분리 행"].iloc[1:].tolist(),
                "이번 단계에서 별도로 분리한 데이터", "#D25B55")
    return fig


def plot_rule_removals(result):
    table = rule_removal_table(result)
    fig, ax = plt.subplots(figsize=(13, 5.8), layout="constrained")
    _count_bars(ax, table["규칙"].tolist(), table["이번 단계 추가 분리"].tolist(),
                "규칙별 추가 분리 행 수 (앞 규칙과 중복 제거)", "#D25B55")
    return fig


def plot_partitions(result):
    table = partition_table(result)
    fig, ax = plt.subplots(figsize=(12, 4.2), layout="constrained")
    _count_bars(ax, table["구분"].tolist(), table["행 수"].tolist(), "서로 겹치지 않는 최종 결과 5집단")
    return fig


def _valid_values(processes, metric, mask):
    values = processes.loc[mask, metric].to_numpy(dtype=float)
    values = values[np.isfinite(values) & (values >= 0)]
    if metric != "duration_ms":
        values = values[values % 1 == 0]
    return values / METRICS[metric][2]


def full_numeric_summary(processes, result):
    rows = []
    for label, mask in (("규칙 적용 전 전체", pd.Series(True, index=processes.index)),
                        ("규칙 후보 분리 후", ~result.rule_candidate)):
        for metric, (name, unit, _) in METRICS.items():
            values = _valid_values(processes, metric, mask)
            percentiles = np.quantile(values, [.5, .95, .99, .999]) if len(values) else [np.nan] * 4
            rows.append({"집단": label, "변수": name, "단위": unit, "집단 전체 행": int(mask.sum()),
                         "유효값 행": len(values), "결측·무효 행": int(mask.sum()) - len(values),
                         "중앙값": percentiles[0], "관측 P95": percentiles[1], "관측 P99": percentiles[2],
                         "관측 P99.9": percentiles[3], "최댓값": values.max() if len(values) else np.nan})
    return pd.DataFrame(rows)


def plot_full_inputs(processes, result, pipeline):
    """All periods, exact counts, no sampling or dropped upper tail."""
    fig, axes = plt.subplots(3, 2, figsize=(16, 10.5), squeeze=False, layout="constrained")
    masks = [pd.Series(True, index=processes.index), ~result.rule_candidate]
    for row, metric in enumerate(METRICS):
        name, unit, scale = METRICS[metric]
        values = [_valid_values(processes, metric, mask) for mask in masks]
        if metric == "duration_ms":
            edges, labels = DURATION_BINS, DURATION_LABELS
        elif metric == "totalCount":
            edges, labels = COUNT_BINS, COUNT_LABELS
        else:
            largest = max((v.max() for v in values if len(v)), default=0)
            # 0..9 individually; >=10 is one complete tail bucket if needed.
            end = min(int(largest), 9)
            edges = np.r_[np.arange(end + 2), np.inf] if largest > end else np.arange(end + 2)
            labels = [str(i) for i in range(end + 1)] + ([f"{end + 1} 이상"] if largest > end else [])
        percentages = [bucket_percent(v, edges)[0] for v in values]
        top = max(1, max((v.max() for v in percentages), default=1)) * 1.17
        cuts = [f"GLOBAL P{q*100:g}: {_number(pipeline.quantile_book.thresholds['GLOBAL'][(metric,q)]/scale)}{unit}"
                for q in pipeline.quantile_book.quantiles if (metric,q) in pipeline.quantile_book.thresholds.get("GLOBAL", {})]
        for col, ax in enumerate(axes[row]):
            pct = percentages[col]
            ax.bar(range(len(pct)), pct, color="#477CC1" if col == 0 else "#59A794", width=.8)
            ax.set_xticks(range(len(pct)), labels, rotation=35 if len(labels) > 4 else 0,
                          ha="right" if len(labels) > 4 else "center", fontsize=9)
            ax.set_ylim(0, min(115, top))
            ax.yaxis.set_major_formatter(StrMethodFormatter("{x:g}%"))
            ax.set_axisbelow(True)
            ax.grid(axis="y", color="#E6EAF0", linewidth=.7)
            ax.set_title(f"{'규칙 전 전체' if col == 0 else '규칙 후보 분리 후'} / {name} · 유효 n={len(values[col]):,}")
            ax.set_xlabel(f"{name} 구간 ({unit})")
            ax.set_ylabel("각 집단 유효값 중 비중")
            for i, v in enumerate(pct):
                if v >= .5:
                    ax.text(i, v, f"{v:.1f}%", ha="center", va="bottom", fontsize=9)
            # Categorical unequal bins cannot carry a truthful numeric vertical cutoff.
            ax.text(.98, .97, "\n".join(cuts), transform=ax.transAxes, ha="right", va="top", fontsize=9,
                    bbox={"facecolor": "white", "alpha": .9, "edgecolor": "none"})
    return fig


def statistical_threshold_table(result, pipeline):
    """Actual adapter/global thresholds and evaluation denominators for every rule."""
    rows = []
    for metric, (label, unit, scale) in METRICS.items():
        if metric not in pipeline.quantile_book.columns:
            continue
        for q in pipeline.quantile_book.quantiles:
            prefix = ("p99" if q == .99 else "p99_9") + "_" + metric
            basis = result[prefix + "_basis"]
            flags = result[prefix + "_exceed"]
            for group, thresholds in pipeline.quantile_book.thresholds.items():
                if (metric, q) not in thresholds:
                    continue
                mask = basis.eq("global") if group == "GLOBAL" else (basis.eq("adapter") & result.adapterType.eq(group))
                n = int((mask & flags.notna()).sum())
                hits = int((mask & flags.fillna(False)).sum())
                rows.append({"변수": label, "기준집단": group, "분위수": f"P{q*100:g}",
                             "threshold": thresholds[(metric,q)] / scale, "단위": unit,
                             "과거 산정 행": pipeline.quantile_book.support[group][metric],
                             "현재 평가 행": n, "초과 행": hits, "초과 %": 100 * hits / n if n else np.nan})
    return pd.DataFrame(rows)


def route_threshold_table(result, pipeline):
    rows = []
    for route, cutoff in pipeline.route_thresholds.items():
        bundle = pipeline.global_model if route == "GLOBAL" else pipeline.adapter_models[route]
        mask = result.model_route.eq("global_only") if route == "GLOBAL" else (result.model_route.eq("global+adapter") & result.adapterType.eq(route))
        rows.append({"판정 경로": route, "사용 점수": "GLOBAL만" if route == "GLOBAL" else "GLOBAL + Adapter 가중 평균",
                     "threshold": cutoff, "판정선 산정 S 행": bundle.support["threshold_rows"],
                     "현재 IF 평가 행": int(mask.sum()), "IF 후보 행": int((mask & result.model_candidate).sum()),
                     "규칙 미탐지 IF 추가": int((mask & result.model_candidate & ~result.rule_candidate).sum())})
    return pd.DataFrame(rows)


def plot_full_score_margins(result):
    values = result.loc[result.model_eligible, "score_margin"].to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    low = min(-.1, values.min()) if len(values) else -.1
    high = max(.1, values.max()) if len(values) else .1
    edges = np.unique(np.r_[np.linspace(low, high, 51), 0])
    masks = [result.model_eligible, result.model_eligible & ~result.rule_candidate]
    fig, axes = plt.subplots(1, 2, figsize=(16, 4.8), layout="constrained")
    top = 1
    for i, (mask, ax) in enumerate(zip(masks, axes)):
        scores = result.loc[mask, "score_margin"].to_numpy(dtype=float)
        scores = scores[np.isfinite(scores)]
        if len(scores):
            heights, _, _ = ax.hist(scores, bins=edges, weights=np.full(len(scores), 100 / len(scores)),
                                   histtype="bar", rwidth=.92, color="#477CC1" if i == 0 else "#59A794", edgecolor="white")
            top = max(top, heights.max() * 1.18)
        hits = int((mask & result.model_candidate).sum())
        ax.axvspan(0, edges[-1], color="#FCE7E7", zorder=0)
        ax.axvline(0, color="#B94034", linestyle="--", linewidth=2, label="판정선 0 (엄격히 초과)")
        ax.set_xlim(edges[0], edges[-1])
        ax.set_title(f"{'전체 IF 평가' if i == 0 else '규칙 미탐지 IF 평가'} · n={len(scores):,}\nIF {'전체' if i == 0 else '추가'} 후보 {hits:,}행")
        ax.set_xlabel("최종 IF 점수 - 해당 행의 실제 경로 threshold")
        ax.set_ylabel("각 집단 IF 평가 행 중 비중")
        ax.yaxis.set_major_formatter(StrMethodFormatter("{x:g}%"))
        ax.legend(loc="upper left", fontsize=9)
        ax.grid(axis="y", color="#E6EAF0", linewidth=.7)
        ax.set_axisbelow(True)
    for ax in axes:
        ax.set_ylim(0, top)
    return fig


def select_case_indices(result, split):
    rule_cases = []
    for name in RULE_NAMES:
        ids = result.index[result["rule_" + name].fillna(False)]
        if len(ids) and ids[0] not in rule_cases:
            rule_cases.append(ids[0])
    pool = result.loc[result.model_candidate & ~result.rule_candidate]
    future = pool.loc[split.loc[pool.index].eq("test")]
    if future.empty:
        future = pool
    model_cases = future.sort_values("score_margin", ascending=False).head(2).index.tolist()
    return rule_cases, model_cases


def _number(value):
    if value is None or pd.isna(value):
        return "결측/미평가"
    if isinstance(value, (int, float, np.number)):
        return f"{value:,.6f}".rstrip("0").rstrip(".")
    return str(value)


def case_evidence_table(item):
    """Human-readable exact predicates; never invents IF feature attribution."""
    rows = []
    for e in item["rule_evidence"]:
        rule = e["rule"]
        observed, criterion = "", e["meaning"]
        if rule == "status_failed": observed, criterion = e["observed_status"], "STATUS = F"
        elif rule == "success_with_error": observed, criterion = f"STATUS={e['status']}, 오류={_number(e['errorCount'])}", "STATUS=S AND errorCount>0"
        elif rule == "count_conservation": observed, criterion = f"전체={_number(e['total'])}, 성공={_number(e['success'])}, 오류={_number(e['error'])}", f"전체가 성공+오류({_number(e['expected_sum'])})와 다름"
        elif rule in ("process_count_mismatch", "tx_success_all_process_failed"):
            observed = f"부모={e['transaction_status']}, 기대={_number(e['declared'])}, 관측={_number(e['observed'])}"
            criterion = ("완료 부모의 기대 단계 수 ≠ 관측 고유 Process ID 수" if rule == "process_count_mismatch" else
                         "부모 S이고 기대 단계가 모두 관측됐으며 전체 Process가 F")
        elif rule == "final_missing_end": observed, criterion = f"STATUS={e['status']}, 종료시각 결측", "완료 상태 S/F/H/Z에서 endTime 결측"
        elif rule == "negative_duration": observed, criterion = f"처리시간={_number(e['duration_ms'])}ms", f"처리시간 < -{_number(e['tolerance_ms'])}ms"
        elif rule == "missing_sql_required": observed = ", ".join(e["missing_columns"])
        elif rule == "invalid_count_domain": observed = ", ".join(f"{k}={_number(v)}" for k,v in e["invalid_values"].items())
        elif rule == "invalid_input": observed = f"상태={e['observed_status']}, PK중복={e['duplicate_process_key']}, 파싱오류={list(e['diagnostics'])}"
        elif rule == "statistical_tail":
            for x in e["exceedances"]:
                label, unit, scale = METRICS[x["metric"]]
                rows.append({"탐지 근거": f"{label} P{x['quantile']*100:g} 초과",
                             "실측값": f"{_number(x['value']/scale)}{unit}",
                             "판정 조건": f"> {_number(x['limit']/scale)}{unit}",
                             "기준/주의": f"{x['basis']} 기준; 드문 값 후보"})
            continue
        rows.append({"탐지 근거": RULE_LABELS[rule], "실측값": observed,
                     "판정 조건": criterion, "기준/주의": "완전 수집 선언 조건" if rule in ("process_count_mismatch", "tx_success_all_process_failed") else "로그/업무 정의 확인"})
    model = item["model"]
    if model["margin"] is not None and model["margin"] > 0:
        rows.append({"탐지 근거": "IF ensemble 판정선 초과", "실측값": _number(model["ensemble_score"]),
                     "판정 조건": f"> {_number(model['threshold'])} (초과폭 {_number(model['margin'])})",
                     "기준/주의": f"{model['route']}; 확률/원인 기여율 아님"})
    return pd.DataFrame(rows, columns=["탐지 근거", "실측값", "판정 조건", "기준/주의"])


def case_feature_table(item):
    rows = []
    for e in item["feature_evidence"]:
        label, unit, scale = METRICS[e["feature"]]
        rows.append({"비교 변수": label, "단위": unit,
                     "실측값": e["value"] / scale if e["value"] is not None else np.nan,
                     "과거 P01": e["training_p01"] / scale if e["training_p01"] is not None else np.nan,
                     "과거 중앙값": e["training_median"] / scale if e["training_median"] is not None else np.nan,
                     "과거 P99": e["training_p99"] / scale if e["training_p99"] is not None else np.nan,
                     "P01–P99 밖": e["outside_p01_p99"], "비교집단": e["reference_group"]})
    return pd.DataFrame(rows)


def case_context_text(item):
    """Describe observed profile differences, without claiming model attribution."""
    differences = []
    for e in item["feature_evidence"]:
        value, low, high = e["value"], e["training_p01"], e["training_p99"]
        if value is None or low is None or high is None:
            continue
        label, unit, scale = METRICS[e["feature"]]
        if value < low:
            differences.append(f"{label} {_number(value/scale)}{unit} < 과거 P01 {_number(low/scale)}{unit}")
        elif value > high:
            differences.append(f"{label} {_number(value/scale)}{unit} > 과거 P99 {_number(high/scale)}{unit}")
    if differences:
        text = "**실제 분포 차이:** " + "; ".join(differences) + ". "
        text += "상한 초과뿐 아니라 평소보다 작거나 빠른 값도 드문 패턴일 수 있다. "
    else:
        text = "표의 개별 수치는 과거 P01–P99 안에 있거나 비교할 값이 없다. 시간 문맥/피처 조합은 별도로 봐야 한다. "
    return text + "직접 판정 근거는 IF 점수 > threshold이며, 이 분포 비교로 원인 기여도나 실제 장애를 확정하지 않는다."

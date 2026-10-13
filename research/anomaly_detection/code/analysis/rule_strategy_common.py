"""Configurable rule triage and train-only grouping evidence (no data exports)."""
from __future__ import annotations

from copy import deepcopy
import json
import re

import numpy as np
import pandas as pd

from log_analysis_common import text

STATUS_MEANINGS = {"S": "성공", "F": "실패", "P": "처리중", "N": "미처리", "H": "부분성공", "Z": "데이터없음"}
DEFAULT_CONFIG = {
    "version": "rules-v1-20261006",
    "allowed_statuses": list(STATUS_MEANINGS),
    "final_statuses": ["S", "F", "H", "Z"],
    "business_alert_statuses": ["F", "H"],
    "time_tolerance_ms": 1.0,
    "pending_timeout_ms": None,
    "blocked_interfaces": [],
    "pattern_rules": [],
    "allowed_hours_by_interface": {},
    "success_response_codes": ["0000"],
    "check_response_convention": True,
    "count_conservation_confirmed": False,
    "latency_quantiles": [0.99, 0.999],
    "quantile_min_support": {"0.99": 1000, "0.999": 10000},
    "min_group_train": 500,
    "min_group_validation": 100,
    "min_group_days": 3,
}


def resolve_config(overrides=None):
    """None/{} uses defaults; deliberate 0/False/[] are preserved."""
    config = deepcopy(DEFAULT_CONFIG)
    if overrides is not None:
        unknown = set(overrides) - set(config)
        if unknown:
            raise ValueError(f"Unknown configuration fields: {sorted(unknown)}")
        config.update(deepcopy(overrides))
    if config["time_tolerance_ms"] < 0:
        raise ValueError("time_tolerance_ms must be nonnegative")
    if config["pending_timeout_ms"] is not None and config["pending_timeout_ms"] < 0:
        raise ValueError("pending_timeout_ms must be nonnegative or None")
    if not set(config["business_alert_statuses"]).issubset(config["allowed_statuses"]):
        raise ValueError("Alert status must be allowed")
    for q in config["latency_quantiles"]:
        if not 0 < q < 1 or str(q) not in config["quantile_min_support"]:
            raise ValueError("Quantile and its minimum support must be configured")
    for name in ("min_group_train", "min_group_validation", "min_group_days"):
        if config[name] < 1:
            raise ValueError(f"{name} must be >= 1")
    for name, hours in config["allowed_hours_by_interface"].items():
        if not hours or any(not isinstance(h, int) or not 0 <= h <= 23 for h in hours):
            raise ValueError(f"Invalid allowed hours for {name}")
    for rule in config["pattern_rules"]:
        if not rule.get("name") or not rule.get("conditions"):
            raise ValueError("Pattern rule needs a name and nonempty conditions")
        for condition in rule["conditions"].values():
            if isinstance(condition, dict):
                if set(condition) != {"regex"}:
                    raise ValueError("Only regex is supported in condition objects")
                re.compile(condition["regex"])
    return config


def schema_audit(sql):
    """Read DDL/comments only. Never execute Oracle DDL against source data."""
    records = []
    for table, body in re.findall(r"CREATE TABLE (\w+)\s*\((.*?)\);", sql, flags=re.S):
        primary = re.search(r"PRIMARY KEY\s*\(([^)]+)\)", body)
        key = set(x.strip() for x in primary[1].split(",")) if primary else set()
        for line in body.splitlines():
            match = re.match(r"\s*(\w+)\s+(VARCHAR2\(\d+\)|TIMESTAMP|NUMBER|INTEGER|CLOB)(.*)", line)
            if not match or match[1] == "PRIMARY":
                continue
            comment = re.search(r"COMMENT ON COLUMN " + table + r"\." + match[1] + r" IS '([^']*)'", sql)
            records.append({"table": table, "column": match[1], "type": match[2],
                            "sql_not_null": "NOT NULL" in match[3], "primary_key": match[1] in key,
                            "meaning": comment[1] if comment else ""})
    return pd.DataFrame(records)


def rule_book(config):
    rows = [
        ("missing_sql_key", "SQL 불변", "PK/NOT NULL 필드 결측", "quarantine", "SQL 정의"),
        ("duplicate_sql_key", "SQL 불변", "동일 기간 내 복합 PK 충돌", "quarantine", "원본 exact duplicate 제거 후 충돌"),
        ("invalid_status", "상태 사전", "결측이 아닌 상태가 S/F/P/N/H/Z 밖", "quarantine", "사용자 제공 상태 사전"),
        ("negative_duration", "시각 정합성", "종료가 시작보다 tolerance 이상 앞섬", "quarantine", "서로 동일 실행·동일 시계라는 전제"),
        ("invalid_count_domain", "도메인 규칙", "건수/재시도 값이 음수 또는 비정수", "quarantine", "SQL NUMBER 자체는 CHECK를 선언하지 않음"),
        ("missing_status", "품질 검토", "상태가 비어 있음", "review", "SQL STATUS는 nullable"),
        ("missing_start", "품질 검토", "시작시각 없음", "review", "SQL START_TIME은 nullable"),
        ("final_missing_end", "완료/수집 검토", "S/F/H/Z인데 종료시각 없음", "review", "N/P의 종료 결측과 구분"),
        ("business_status", "설정형 정책", "설정한 F/H 상태", "business_alert", "실행 결과 알림; 확정 AI 이상 정답 아님"),
        ("pending_timeout", "설정형 정책", "N/P 경과시간이 설정 SLA 초과", "business_alert", "default=None: 비활성; 실제 관측 cutoff 필수"),
        ("blocked_interface", "설정형 정책", "설정된 금지 interfaceId", "business_alert", "default=[]: 업무명을 자동 금지하지 않음"),
        ("configured_pattern", "설정형 정책", "설정된 범주 조합/정규식 만족", "business_alert", "default=[]: 조건들의 AND, 규칙들의 OR"),
        ("outside_allowed_hours", "설정형 정책", "업무별 허용 KST 시간대 밖", "business_alert", "default={}: 이력의 희소 시간대를 자동 금지하지 않음"),
        ("response_convention", "조건부 정합성", "S/응답코드 불일치 또는 F/성공코드", "review", "0000 관례는 SQL 제약이 아님; 설정 가능"),
        ("count_conservation", "조건부 정합성", "완료 Process에서 total != success+error", "review", "confirmed=True일 때만 학습 격리"),
        ("success_with_error", "조건부 정합성", "Process S인데 errorCount>0", "review", "부분성공 H와 구별; 계수 정의 확인"),
        ("tx_success_all_process_failed", "관계 정합성", "Tx S + 전체 기대 단계가 관측되고 모두 F", "review", "허브·재시도 일치와 수집 완전성 필요"),
        ("process_count_mismatch", "관계 정합성", "완료 거래의 선언 단계 수와 관측 단계 수 차이", "review", "로그 수집 누락/조건부 단계 가능"),
        ("orphan_process", "관계/수집 품질", "동일 허브·재시도 거래에 연결 실패", "review", "현재 추출 범위에 없는 부모일 수 있음"),
        ("reference_p99/p999", "버전 고정 통계", "학습 S 로그의 고정 분위수 초과", "statistical_candidate", "절대 하드 룰 아님; 그룹→공통→통합 fallback"),
    ]
    return pd.DataFrame(rows, columns=["rule", "type", "condition", "action", "precondition"])


def evaluate_rules(frame, kind, config, *, observed_as_of=None):
    """Return aligned booleans and per-rule eligible denominators; no deletions."""
    flags, eligibility = {}, {}
    index = frame.index
    all_rows = pd.Series(True, index=index)

    def add(name, hit, eligible=all_rows):
        eligibility[name] = eligible.fillna(False).astype(bool)
        flags[name] = (hit.fillna(False) & eligibility[name]).astype(bool)

    keys = ["transactionId", "processHubId", "retryCount"] if kind == "transaction" else ["transactionId", "processId", "retryCount"]
    required = list(dict.fromkeys(keys + ["processHubId"]))
    add("missing_sql_key", frame[required].isna().any(axis=1))
    complete_key = frame[keys].notna().all(axis=1)
    add("duplicate_sql_key", frame.duplicated(["source_period", *keys], keep=False), complete_key)
    add("invalid_status", ~frame.status.isin(config["allowed_statuses"]), frame.status.notna())
    add("missing_status", frame.status.isna())
    add("missing_start", frame.startTime.isna())
    add("negative_duration", frame.duration_ms.lt(-config["time_tolerance_ms"]), frame.duration_ms.notna())
    counts = [c for c in ["processCount", "retryCount", "totalCount", "successCount", "errorCount"] if c in frame]
    invalid_count = pd.Series(False, index=index)
    for col in counts:
        invalid_count |= frame[col].notna() & (frame[col].lt(0) | frame[col].mod(1).ne(0))
    add("invalid_count_domain", invalid_count, frame[counts].notna().any(axis=1))
    final = frame.status.isin(config["final_statuses"])
    add("final_missing_end", frame.endTime.isna(), final)
    add("business_status", frame.status.isin(config["business_alert_statuses"]), frame.status.notna())
    # A dataset/export cutoff is not the current wall clock. None leaves this disabled.
    timeout = config["pending_timeout_ms"]
    pending = frame.status.isin(["P", "N"]) & frame.startTime.notna() & frame.endTime.isna()
    if timeout is not None and observed_as_of is not None:
        cutoff = frame.source_period.map(observed_as_of) if isinstance(observed_as_of, dict) else pd.Series(pd.Timestamp(observed_as_of), index=index)
        cutoff = pd.to_datetime(cutoff)
        elapsed = (cutoff - frame.startTime).dt.total_seconds() * 1000
        add("pending_timeout", elapsed.gt(timeout), pending & elapsed.ge(0))
    else:
        add("pending_timeout", pd.Series(False, index=index), pd.Series(False, index=index))
    if "interfaceId" in frame:
        add("blocked_interface", frame.interfaceId.isin(config["blocked_interfaces"]), all_rows & bool(config["blocked_interfaces"]))
        hours = config["allowed_hours_by_interface"]
        allowed_scope = frame.interfaceId.isin(hours) & frame.startTime.notna()
        outside = pd.Series(False, index=index)
        for business, allowed in hours.items():
            outside |= frame.interfaceId.eq(business) & ~frame.startTime.dt.hour.isin(allowed)
        add("outside_allowed_hours", outside, allowed_scope)
    pattern_hit, pattern_scope = pd.Series(False, index=index), pd.Series(False, index=index)
    for rule in config["pattern_rules"]:
        if not set(rule["conditions"]).issubset(frame.columns):
            continue
        hit = all_rows.copy()
        for col, expected in rule["conditions"].items():
            values = frame[col].astype("string")
            if isinstance(expected, dict):
                hit &= values.str.contains(expected["regex"], regex=True, na=False)
            else:
                hit &= values.isin(expected if isinstance(expected, list) else [expected])
        pattern_hit |= hit
        pattern_scope |= all_rows
    add("configured_pattern", pattern_hit, pattern_scope)
    comparable = frame.status.isin(["S", "F"]) & frame.responseCode.notna() & config["check_response_convention"]
    success_code = frame.responseCode.isin(config["success_response_codes"])
    add("response_convention", (frame.status.eq("S") & ~success_code) | (frame.status.eq("F") & success_code), comparable)
    if kind == "process":
        comparable = final & frame[["totalCount", "successCount", "errorCount"]].notna().all(axis=1)
        add("count_conservation", frame.totalCount.ne(frame.successCount + frame.errorCount), comparable)
        add("success_with_error", frame.errorCount.gt(0), frame.status.eq("S") & frame.errorCount.notna())
    result = pd.DataFrame(flags, index=index)
    eligible = pd.DataFrame(eligibility, index=index)
    hard = ["missing_sql_key", "duplicate_sql_key", "invalid_status", "negative_duration", "invalid_count_domain"]
    if kind == "process" and config["count_conservation_confirmed"]:
        hard.append("count_conservation")
    business = ["business_status", "pending_timeout", "configured_pattern", "blocked_interface", "outside_allowed_hours"]
    result["quarantine"] = result[hard].any(axis=1)
    result["business_alert"] = result[[c for c in business if c in result]].any(axis=1)
    reviews = [c for c in flags if c not in hard + business]
    result["quality_review"] = result[reviews].any(axis=1)
    # S is an outcome-based reference proxy, not a confirmed normal label.
    result["reference_eligible"] = frame.status.eq("S") & frame.duration_ms.ge(0) & ~result.quarantine & ~result.quality_review & ~result.business_alert
    return result, eligible


def rule_counts(frame, flags, eligible, kind):
    records = []
    for period, positions in frame.groupby("source_period", observed=True).groups.items():
        for rule in eligible:
            denom = int(eligible.loc[positions, rule].sum())
            hits = int(flags.loc[positions, rule].sum())
            records.append({"kind": kind, "period": str(period), "rule": rule, "rows": len(positions),
                            "eligible": denom, "hits": hits, "hit_pct": 100 * hits / denom if denom else np.nan,
                            "not_evaluable": len(positions) - denom})
    return pd.DataFrame(records)


def hub_aware_summary(transactions, processes):
    """Attempt-level join using hub and retry; never silently collapse across hubs."""
    keys = ["source_period", "transactionId", "processHubId", "retryCount"]
    tx = transactions.copy()
    valid_tx = tx[keys].notna().all(axis=1) & ~tx.duplicated(keys, keep=False)
    parent = tx.loc[valid_tx, keys].assign(parent_found=True)
    p = processes.loc[processes[keys].notna().all(axis=1)].copy()
    linked = p.merge(parent, on=keys, how="left", validate="many_to_one")
    orphan = linked.parent_found.isna()
    audit = linked.assign(orphan=orphan).groupby("source_period", observed=True).agg(process_rows=("processId", "size"), orphan_processes=("orphan", "sum"))
    linked = linked.loc[~orphan]
    agg = linked.groupby(keys, observed=True).agg(
        observed_processes=("processId", "nunique"), process_rows=("processId", "size"),
        process_known_durations=("duration_ms", "count"), bottleneck_ms=("duration_ms", "max"),
        process_start=("startTime", "min"), process_end=("endTime", "max"),
    )
    statuses = linked.assign(process_failed=linked.status.eq("F"), process_success=linked.status.eq("S"), process_final=linked.status.isin(["S", "F", "H", "Z"]))
    state = statuses.groupby(keys, observed=True).agg(process_failed=("process_failed", "sum"), process_success=("process_success", "sum"), process_final=("process_final", "sum"))
    agg = agg.join(state)
    tx = tx.merge(agg.reset_index(), on=keys, how="left", validate="many_to_one")
    tx["join_unambiguous"] = valid_tx.to_numpy()
    observed = tx.observed_processes.fillna(0)
    tx["relation_comparable"] = tx.join_unambiguous & tx.processCount.notna() & tx.status.isin(["S", "F", "H", "Z"])
    tx["process_count_mismatch"] = tx.relation_comparable & observed.ne(tx.processCount)
    tx["no_process"] = tx.join_unambiguous & tx.observed_processes.isna()
    # All expected stages must be observed; their final outcomes must all be F.
    complete = tx.relation_comparable & observed.gt(0) & observed.eq(tx.processCount) & tx.process_rows.eq(observed)
    tx["tx_success_all_process_failed"] = complete & tx.status.eq("S") & tx.process_failed.eq(observed)
    tx["tx_success_any_process_failed"] = complete & tx.status.eq("S") & tx.process_failed.gt(0)
    tx["stage_duration_complete"] = complete & tx.process_known_durations.eq(observed)
    tx["process_span_ms"] = (tx.process_end - tx.process_start).dt.total_seconds() * 1000
    tx["duration_max_gap_ms"] = tx.duration_ms - tx.bottleneck_ms
    return tx, audit


GROUPINGS = {
    "transaction": {
        "업무": ["interfaceId"],
        "카테고리": ["category_top", "interfaceType"],
        "카테고리+경로": ["category_top", "interfaceType", "startChannelId", "endChannelId"],
        "경로": ["interfaceType", "startChannelId", "endChannelId"],
        "통합": [],
    },
    "process": {
        "업무단계": ["processId"],
        "어댑터": ["adapterType"],
        "어댑터+채널": ["adapterType", "channelId"],
        "어댑터+채널+허브": ["adapterType", "channelId", "processHubId"],
        "통합": [],
    },
}


def add_group_fields(frame):
    result = frame.copy()
    if "categoryName" in result:
        result["category_top"] = text(result.categoryName).str.split(">").str[0].str.strip().astype("category")
    return result


def group_keys(frame, columns):
    if not columns:
        return pd.Series("GLOBAL", index=frame.index, dtype="category")
    # Factorize tuples, not separator-concatenated text: category values may contain separators.
    values = pd.MultiIndex.from_frame(frame[columns].astype("string").fillna("<MISSING>"))
    codes, uniques = pd.factorize(values, sort=False)
    names = [json.dumps(tuple(map(str, key)), ensure_ascii=False) for key in uniques]
    return pd.Series(pd.Categorical.from_codes(codes, categories=names), index=frame.index)


def grouping_profile(frame, kind, split, flags, config):
    """Evidence only: no new AI models are trained by this analysis."""
    summaries, details = [], {}
    for name, columns in GROUPINGS[kind].items():
        key = group_keys(frame, columns)
        work = pd.DataFrame({"group": key, "period": frame.source_period, "duration": frame.duration_ms,
                             "log_duration": np.log1p(frame.duration_ms.clip(lower=0)),
                             "train": split.eq("train") & flags.reference_eligible,
                             "validation": split.eq("validation") & flags.reference_eligible,
                             "day": frame.startTime.dt.normalize(), "split": split,
                             "status": frame.status, "eligible": flags.reference_eligible})
        counts = work.groupby("group", observed=True).agg(rows=("duration", "size"), train_S=("train", "sum"), validation_S=("validation", "sum"))
        train = work.loc[work.train]
        days = train.groupby("group", observed=True).day.nunique()
        spread = train.groupby("group", observed=True).log_duration.agg(["std", "median"])
        counts = counts.join(days.rename("train_days")).join(spread)
        counts["ready"] = counts.train_S.ge(config["min_group_train"]) & counts.validation_S.ge(config["min_group_validation"]) & counts.train_days.ge(config["min_group_days"])
        test = work.loc[work.split.eq("test")]
        supported = test.group.map(counts.ready).astype("boolean").fillna(False).astype(bool)
        seen = test.group.map(counts.train_S).astype(float).fillna(0).gt(0)
        if len(train) > 1:
            means = train.groupby("group", observed=True).log_duration.transform("mean")
            total_ss = ((train.log_duration - train.log_duration.mean()) ** 2).sum()
            residual_ss = ((train.log_duration - means) ** 2).sum()
            variance_explained = 1 - residual_ss / total_ss if total_ss > 0 else np.nan
        else:
            variance_explained = np.nan
        summaries.append({"kind": kind, "grouping": name, "columns": " + ".join(columns) or "공통 행동 피처",
                          "groups_all": len(counts), "groups_ready": int(counts.ready.sum()),
                          "train_reference_rows": len(train), "test_rows": len(test),
                          "test_group_ready_pct": 100 * supported.mean() if len(test) else np.nan,
                          "test_unseen_pct": 100 * (~seen).mean() if len(test) else np.nan,
                          "train_log_variance_explained": variance_explained})
        details[name] = counts.sort_values("rows", ascending=False)
    return pd.DataFrame(summaries), details


def freeze_quantiles(frame, reference_mask, columns, config):
    """Fit once on training-reference S logs, with explicit sample-support guards."""
    key = group_keys(frame, columns)
    ref = pd.DataFrame({"group": key, "duration": frame.duration_ms}).loc[reference_mask & frame.duration_ms.ge(0)]
    profile = ref.groupby("group", observed=True).duration.agg(n="count", median="median")
    for q in config["latency_quantiles"]:
        profile[f"q{q}"] = ref.groupby("group", observed=True).duration.quantile(q).where(profile.n.ge(config["quantile_min_support"][str(q)]))
    return profile


def score_frozen_quantiles(frame, profiles, level_columns, q):
    threshold = pd.Series(np.nan, index=frame.index)
    level = pd.Series("NO_REFERENCE", index=frame.index, dtype="string")
    for name, columns in level_columns:
        key = group_keys(frame, columns)
        if name not in profiles or f"q{q}" not in profiles[name]:
            continue
        mapped = key.map(profiles[name][f"q{q}"]).astype(float)
        use = threshold.isna() & mapped.notna()
        threshold.loc[use] = mapped.loc[use]
        level.loc[use] = name
    comparable = threshold.notna() & frame.duration_ms.ge(0)
    return pd.DataFrame({"threshold_ms": threshold, "reference_level": level, "comparable": comparable,
                         "candidate": comparable & frame.duration_ms.gt(threshold)}, index=frame.index)

"""All requested rules; nullable flags explicitly distinguish unknown from false."""
import numpy as np
import pandas as pd
from .data import ALLOWED_STATUSES, COUNTS, FINAL_STATUSES, JOIN_KEYS, PROCESS_PK, PROCESS_REQUIRED, prepare

RULE_DESCRIPTIONS = {
    "status_failed": "STATUS=F: 로그에 실패가 명시됨",
    "tx_success_all_process_failed": "Tx S이고 기대 단계가 모두 관측됐으며 전부 F",
    "process_count_mismatch": "완료 거래의 processCount와 관측 고유 Process ID 수가 다름",
    "success_with_error": "Process S인데 errorCount>0",
    "count_conservation": "완료 Process에서 totalCount != successCount+errorCount",
    "final_missing_end": "S/F/H/Z인데 endTime 없음",
    "missing_sql_required": "Process PK/NOT NULL 컬럼 결측",
    "negative_duration": "종료시각이 시작시각보다 허용 오차 이상 앞섬",
    "invalid_count_domain": "건수/재시도가 음수 또는 비정수",
    "statistical_tail": "학습 구간에서 고정한 P99/P99.9 초과",
    "invalid_input": "미정의 상태, 파싱 오류 또는 복합 PK 충돌",
}
RULE_NAMES = tuple(RULE_DESCRIPTIONS)
CONSISTENCY_RULES = ("tx_success_all_process_failed", "process_count_mismatch",
                     "success_with_error", "count_conservation", "final_missing_end")
QUALITY_RULES = ("missing_sql_required", "negative_duration", "invalid_count_domain", "invalid_input")


def _flag(frame, hit, comparable):
    values = pd.Series(pd.NA, index=frame.index, dtype="boolean")
    comparable = comparable.fillna(False).astype(bool)
    values.loc[comparable] = hit.loc[comparable].fillna(False).astype(bool)
    return values


def evaluate_rules(processes, config, *, transactions=None, complete_context=False):
    p = prepare(processes)
    flags = pd.DataFrame(index=p.index)
    all_rows = pd.Series(True, index=p.index)
    flags["status_failed"] = _flag(p, p.status.eq("F"), p.status.notna())
    for c in ("tx_success_all_process_failed", "process_count_mismatch"):
        flags[c] = pd.Series(pd.NA, index=p.index, dtype="boolean")
    flags["success_with_error"] = _flag(p, p.errorCount.gt(0), p.status.eq("S") & p.errorCount.notna())
    final = p.status.isin(FINAL_STATUSES)
    count_ok = p[["totalCount", "successCount", "errorCount"]].notna().all(axis=1)
    flags["count_conservation"] = _flag(p, p.totalCount.ne(p.successCount + p.errorCount), final & count_ok)
    flags["final_missing_end"] = _flag(p, p.endTime.isna(), final)
    flags["missing_sql_required"] = _flag(p, p[list(PROCESS_REQUIRED)].isna().any(axis=1), all_rows)
    flags["negative_duration"] = _flag(p, p.duration_ms.lt(-config.time_tolerance_ms), p.duration_ms.notna())
    numeric_columns = [c for c in COUNTS if c in p]
    invalid_count = pd.Series(False, index=p.index)
    invalid_parse = p._invalid_startTime | p._invalid_endTime
    for c in numeric_columns:
        invalid_count |= p[c].notna() & (p[c].lt(0) | p[c].mod(1).ne(0))
        invalid_parse |= p[f"_invalid_{c}"]
    flags["invalid_count_domain"] = _flag(p, invalid_count, p[numeric_columns].notna().any(axis=1))
    duplicate = p.duplicated(list(PROCESS_PK), keep=False) & p[list(PROCESS_PK)].notna().all(axis=1)
    unknown_status = p.status.notna() & ~p.status.isin(ALLOWED_STATUSES)
    flags["invalid_input"] = _flag(p, invalid_parse | duplicate | unknown_status, all_rows)
    flags["statistical_tail"] = pd.Series(pd.NA, index=p.index, dtype="boolean")
    context = pd.DataFrame(index=p.index)
    context["duplicate_process_key"] = duplicate
    context["context_state"] = "not_supplied" if transactions is None else "incomplete_context"
    context["transactionStatus"] = pd.Series(pd.NA, index=p.index, dtype="string")
    context["declared_process_count"] = np.nan
    context["observed_process_count"] = np.nan
    if transactions is not None:
        t = prepare(transactions, "transaction")
        valid_t = t[list(JOIN_KEYS)].notna().all(axis=1) & ~t.duplicated(list(JOIN_KEYS), keep=False)
        parent = t.loc[valid_t, [*JOIN_KEYS, "status", "processCount"]].rename(
            columns={"status": "transactionStatus", "processCount": "declared_process_count"})
        parent["_parent_found"] = True
        pkeys = p[[*JOIN_KEYS, "processId", "status"]].copy()
        pkeys["_row_id"] = np.arange(len(p))
        pkeys["_failed"] = p.status.eq("F").fillna(False).to_numpy()
        pkeys["_valid"] = p[list(PROCESS_REQUIRED)].notna().all(axis=1).to_numpy() & ~duplicate.to_numpy()
        agg = pkeys.groupby(list(JOIN_KEYS), observed=True).agg(
            observed_process_count=("processId", "nunique"), observed_rows=("processId", "size"),
            failed_rows=("_failed", "sum"), all_valid=("_valid", "all"))
        linked = pkeys.merge(parent, on=list(JOIN_KEYS), how="left", validate="many_to_one").merge(
            agg.reset_index(), on=list(JOIN_KEYS), how="left", validate="many_to_one").sort_values("_row_id")
        linked.index = p.index
        for c in ("transactionStatus", "declared_process_count", "observed_process_count"):
            context[c] = linked[c]
        found = linked._parent_found.fillna(False).astype(bool)
        context.loc[~found, "context_state"] = "missing_or_ambiguous_parent"
        context.loc[found & bool(complete_context), "context_state"] = "complete_context_declared"
        n = linked.declared_process_count
        comparable = (found & bool(complete_context) & linked.all_valid.fillna(False) &
                      linked.transactionStatus.isin(FINAL_STATUSES) & n.notna() & n.ge(0) & n.mod(1).eq(0))
        flags["process_count_mismatch"] = _flag(p, linked.observed_process_count.ne(n), comparable)
        complete = comparable & linked.observed_process_count.eq(n) & n.gt(0) & linked.observed_rows.eq(n)
        flags["tx_success_all_process_failed"] = _flag(p, linked.failed_rows.eq(n), complete & linked.transactionStatus.eq("S"))
    return flags, context


def any_hits(flags, names):
    return flags[list(names)].fillna(False).any(axis=1)


def model_eligible(p, flags):
    # First version scores completed events only. N/P need separate elapsed-time/SLA monitoring.
    return (p.status.isin(FINAL_STATUSES) & p.startTime.notna() & p.endTime.notna() &
            p.duration_ms.ge(0) & ~any_hits(flags, QUALITY_RULES))


def reference_eligible(p, flags, *, exclude_statistical=False):
    allowed = model_eligible(p, flags) & p.status.eq("S") & ~any_hits(flags, CONSISTENCY_RULES)
    if exclude_statistical:
        allowed &= ~flags.statistical_tail.fillna(False)
    return allowed


def pack_rule_bits(flags):
    bits = np.zeros(len(flags), dtype=np.uint16)
    for i, name in enumerate(RULE_NAMES):
        bits |= flags[name].fillna(False).to_numpy(dtype=np.uint16) << i
    return bits


def decode_rule_bits(bits):
    return [name for i, name in enumerate(RULE_NAMES) if int(bits) & (1 << i)]

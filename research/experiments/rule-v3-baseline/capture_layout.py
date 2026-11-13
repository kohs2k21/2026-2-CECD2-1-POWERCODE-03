"""Rule v3 기준선 시험의 사례 배치(합성·실데이터 공통). 번들 입력 형식 approved-metadata-capture1.0을 쓴다.

한 사례 = 한 필드 = 폴더 하나:

    <case>/application/{capture.json, PROCESS.csv[, TRANSACTION.csv], authority.json}
    <case>/reference/{...같은 구성...}
    <case>/proposal.json   (validate-feature 입력, feature5.2 후보 1개)
    <case>/request.json    (validate-feature 공개 요청)
    <case>/rule_plan.json  (rule 입력, requests=["baseline"])

시각 열(START_TIME·END_TIME)은 원문이 아니라 `trainer.source_time.process_source_time`로 해석한 UTC 고정 형식이다.
번들은 Python 3.10 `fromisoformat`으로 읽으므로 원문 형식 차이(3.10/3.12)를 피한다. 해석 실패는 빈 값(결측)이다.
숫자 열은 `derived_expression.number`와 같이 읽고 숫자가 아니면 빈 값이며 세어 둔다.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sys
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "server" / "worker"))

from derived_expression import number as service_number  # noqa: E402
from trainer.source_time import format_utc, process_source_time  # noqa: E402

EXCLUSION_RULE = "status=S, explicit anomaly labels excluded; unlabeled retained"  # threshold_recommendation과 같은 문구
UNIT_POINTER = "/unit_count"
TIME_COLUMNS = ("START_TIME", "END_TIME")
COUNT_COLUMNS = {"TOTAL_COUNT": "total_count", "SUCCESS_COUNT": "success_count",
                 "ERROR_COUNT": "error_count", "RETRY_COUNT": "retry_count"}
RAW_KEYS = {"START_TIME": "start_time_raw", "END_TIME": "end_time_raw", **COUNT_COLUMNS}


def _col(name: str, table: str = "PROCESS") -> dict[str, Any]:
    return {"op": "column", "table": table, "column": name}


# 서비스 SOFT_RULE_FIELDS 6종 + 합성 전용 관계 비율(증명된 상한 사례).
FIELDS: dict[str, dict[str, Any]] = {
    "elapsed_ms": {"columns": ["START_TIME", "END_TIME"],
                   "expression": {"op": "datetime_diff", "unit": "ms", "args": [_col("END_TIME"), _col("START_TIME")]},
                   "purpose": "Measure PROCESS elapsed time as END_TIME minus START_TIME in milliseconds.",
                   "meaning": "Per PROCESS row, END_TIME minus START_TIME in milliseconds; unavailable when either timestamp is missing."},
    "error_ratio": {"columns": ["ERROR_COUNT", "TOTAL_COUNT"],
                    "expression": {"op": "divide", "args": [_col("ERROR_COUNT"), _col("TOTAL_COUNT")]},
                    "purpose": "Measure the PROCESS error ratio as ERROR_COUNT divided by TOTAL_COUNT.",
                    "meaning": "Per PROCESS row, ERROR_COUNT divided by TOTAL_COUNT; unavailable when an operand is missing or TOTAL_COUNT is zero."},
}
for _column, _field in COUNT_COLUMNS.items():
    FIELDS[_field] = {"columns": [_column], "expression": _col(_column),
                      "purpose": f"Measure the PROCESS {_column} value as a count.",
                      "meaning": f"Per PROCESS row, the {_column} value; unavailable when the value is missing."}
SERVICE_FIELDS = ("total_count", "success_count", "error_count", "retry_count", "elapsed_ms", "error_ratio")
TX_FIELD = "tx_failed_ratio"
TX_RELATION = "process_transaction"
FIELDS[TX_FIELD] = {
    "columns": ["STATUS"], "grain": "TRANSACTION",
    "expression": {"op": "divide", "args": [
        {"op": "conditional_count", "relationship_id": TX_RELATION,
         "where": {"op": "eq", "args": [_col("STATUS"), {"op": "literal", "value": "F"}]}},
        {"op": "child_count", "relationship_id": TX_RELATION}]},
    "purpose": "Measure per transaction the share of linked PROCESS rows whose STATUS equals F.",
    "meaning": "Per TRANSACTION row, linked PROCESS rows with STATUS F divided by all linked PROCESS rows."}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=True, indent=2, sort_keys=False) + "\n").encode("utf-8"))


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def eligible(raw: Mapping[str, Any], label_evidence: Any) -> bool:
    """threshold_recommendation.reference_sample과 같은 표본 규칙."""
    label = label_evidence.get("label") if isinstance(label_evidence, dict) else None
    return raw.get("status") == "S" and label != "anomaly"


def normalize_row(raw: Mapping[str, Any], source_tz: str | None, counts: dict[str, int]) -> dict[str, str]:
    """원천 행(allowed_values 키) → 사례 CSV 값. 값은 반환만 하고 기록·출력하지 않는다."""
    out: dict[str, str] = {}
    for column in TIME_COLUMNS:
        raw_value = raw.get(RAW_KEYS[column])
        value, _, _ = process_source_time(raw_value, None, source_tz) if column == "START_TIME" else \
            process_source_time(None, raw_value, source_tz)
        out[column] = "" if value is None else format_utc(value)
        if value is None and raw_value is not None and str(raw_value).strip() not in ("", "\\N"):
            counts[f"unparsed_{column}"] = counts.get(f"unparsed_{column}", 0) + 1
    for column, key in COUNT_COLUMNS.items():
        raw_value = raw.get(key)
        value = service_number(raw_value)
        out[column] = "" if value is None else str(value)
        if value is None and raw_value is not None and str(raw_value).strip() not in ("", "\\N"):
            counts[f"unparsed_{column}"] = counts.get(f"unparsed_{column}", 0) + 1
    return out


def _csv_bytes(header: Sequence[str], rows: Iterable[Sequence[str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _column_spec(name: str, unit_verified: bool) -> dict[str, Any]:
    if name in TIME_COLUMNS:
        logical, unit = "datetime", {"name": None, "status": "unknown"}
        description = "PROCESS source time normalized to UTC from the raw text by the documented source-time rule."
    elif name in COUNT_COLUMNS:
        logical = "number"
        unit = {"name": "count", "status": "verified", "evidence_ids": [f"unit_PROCESS_{name}"]} if unit_verified \
            else {"name": None, "status": "unknown"}
        description = "PROCESS numeric counter from the collected row; non-numeric text is stored as missing."
    elif name in ("ROW_ID", "TX_ID", "STATUS"):
        logical, unit = "string", {"name": None, "status": "unknown"}
        description = {"ROW_ID": "Sequential row identifier assigned by the capture writer.",
                       "TX_ID": "Transaction group identifier assigned by the capture writer.",
                       "STATUS": "Raw PROCESS status code; F is compared literally without business meaning."}[name]
    else:
        raise ValueError("unknown_column")
    return {"name": name, "logical_type": logical, "nullable": name not in ("ROW_ID", "TX_ID"), "excluded": False,
            "unit": unit, "missing_tokens": [""], "description": description}


def write_capture(folder: Path, *, field: str, rows: Sequence[Mapping[str, str]], split: str,
                  period: Mapping[str, str], data_class: str, unit_verified: bool, confirmation: str,
                  authority: Mapping[str, Any], identity: Mapping[str, Any], unknown: Sequence[str],
                  transactions: Sequence[str] = ()) -> dict[str, Any]:
    """capture.json·CSV·authority.json을 쓰고 파일 SHA를 돌려준다. rows는 정규화된 행(필요 열 포함)."""
    spec = FIELDS[field]
    folder.mkdir(parents=True, exist_ok=False)
    columns = ["ROW_ID"] + (["TX_ID"] if field == TX_FIELD else []) + spec["columns"]
    process_rows = [[f"r{index + 1:06d}"] + [row[c] for c in columns[1:]] for index, row in enumerate(rows)]
    process_bytes = _csv_bytes(columns, process_rows)
    (folder / "PROCESS.csv").write_bytes(process_bytes)
    authority_bytes = (json.dumps(authority, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
    (folder / "authority.json").write_bytes(authority_bytes)
    authority_sha = sha256_bytes(authority_bytes)
    tables = [{"name": "PROCESS", "file": "PROCESS.csv", "format": "csv", "sha256": sha256_bytes(process_bytes),
               "row_count": len(process_rows), "primary_key": ["ROW_ID"],
               "columns": [_column_spec(c, unit_verified) for c in columns]}]
    relationships = []
    files = {"PROCESS.csv": sha256_bytes(process_bytes), "authority.json": authority_sha}
    if field == TX_FIELD:
        tx_bytes = _csv_bytes(["TX_ID"], [[tx] for tx in transactions])
        (folder / "TRANSACTION.csv").write_bytes(tx_bytes)
        files["TRANSACTION.csv"] = sha256_bytes(tx_bytes)
        tables.insert(0, {"name": "TRANSACTION", "file": "TRANSACTION.csv", "format": "csv",
                          "sha256": files["TRANSACTION.csv"], "row_count": len(transactions),
                          "primary_key": ["TX_ID"], "columns": [_column_spec("TX_ID", unit_verified)]})
        relationships.append({"id": TX_RELATION, "from_table": "PROCESS", "from_columns": ["TX_ID"],
                              "to_table": "TRANSACTION", "to_columns": ["TX_ID"], "no_reference_values": []})
    evidence = []
    if unit_verified:
        for column in spec["columns"]:
            if column in COUNT_COLUMNS:
                evidence.append({"id": f"unit_PROCESS_{column}", "kind": "unit", "table": "PROCESS", "column": column,
                                 "confirmation": confirmation, "value": authority["unit_count"],
                                 "source": {"file": "authority.json", "sha256": authority_sha, "pointer": UNIT_POINTER}})
    manifest = {"contract": "approved-metadata-capture1.0", "dataset_id": identity["dataset_id"],
                "data_class": data_class, "split": split, "period": dict(period),
                "admission": {"approved": True, "Body_excluded": True, "held_lineage_excluded": True,
                              "complete_family_capture": True},
                "lineage_tags": [], "tables": tables, "relationships": relationships, "evidence": evidence,
                "source_identity": dict(identity),
                "transaction_families": len(transactions) if field == TX_FIELD else 0, "unknown": list(unknown)}
    write_json(folder / "capture.json", manifest)
    files["capture.json"] = sha256_file(folder / "capture.json")
    return files


def proposal_for(field: str) -> dict[str, Any]:
    spec = FIELDS[field]
    table = spec.get("grain", "PROCESS")
    key = "TX_ID" if table == "TRANSACTION" else "ROW_ID"
    return {"proposal_id": f"rule_v3_weekend_{field}",
            "plan": {"expression": spec["expression"], "grain": {"table": table, "keys": [key]},
                     "population": {"table": table, "filter": None}, "group_by": [], "result_use": "value"},
            "purpose": spec["purpose"], "meaning": spec["meaning"], "needed_evidence": []}


def request_for(field: str) -> dict[str, Any]:
    spec = FIELDS[field]
    table = spec.get("grain", "PROCESS")
    request = {"question": spec["purpose"],
               "analysis_request": {"output_table": table, "result_use": "value", "calculation_type": "number",
                                    "required_columns": [["PROCESS", c] for c in spec["columns"]],
                                    "required_relationship_ids": [TX_RELATION] if field == TX_FIELD else []}}
    return request


def rule_plan_for(field: str, minimum_n: int = 20) -> dict[str, Any]:
    return {"requests": ["baseline"],
            "question": f"Offline numeric soft-rule threshold for {field}: compare current values with the code "
                        "p95 of the earlier TRAIN reference; no incident, SLA or online claim.",
            "reference_policy": {"minimum_n": minimum_n, "operator": "gt"}}


def write_case(case_dir: Path, *, field: str, reference_rows: Sequence[Mapping[str, str]],
               current_rows: Sequence[Mapping[str, str]], reference_period: Mapping[str, str],
               current_period: Mapping[str, str], data_class: str, unit_verified: bool, confirmation: str,
               authority: Mapping[str, Any], identity: Mapping[str, Any], unknown: Sequence[str],
               reference_transactions: Sequence[str] = (), current_transactions: Sequence[str] = ()) -> dict[str, str]:
    """사례 폴더 전체를 쓰고 {상대경로: sha256}을 돌려준다. 폴더가 이미 있으면 실패한다."""
    case_dir.mkdir(parents=True, exist_ok=False)
    files: dict[str, str] = {}
    for side, rows, period, split, txs in (("reference", reference_rows, reference_period, "train", reference_transactions),
                                           ("application", current_rows, current_period, "application", current_transactions)):
        side_identity = {**identity, "dataset_id": f"{identity['dataset_id']}_{side}", "side": side}
        written = write_capture(case_dir / side, field=field, rows=rows, split=split, period=period,
                                data_class=data_class, unit_verified=unit_verified, confirmation=confirmation,
                                authority=authority, identity=side_identity, unknown=unknown, transactions=txs)
        files.update({f"{side}/{name}": digest for name, digest in written.items()})
    for name, value in (("proposal.json", proposal_for(field)), ("request.json", request_for(field)),
                        ("rule_plan.json", rule_plan_for(field))):
        write_json(case_dir / name, value)
        files[name] = sha256_file(case_dir / name)
    return dict(sorted(files.items()))


def split_observations(observations: Iterable[tuple[Any, Mapping[str, Any], Any]], source_tz: str | None, *,
                       reference_ratio: float = 0.8, apply_filter: bool = True,
                       window: tuple[Any, Any] | None = None, drop_held_transactions: bool = False) -> dict[str, Any]:
    """(관측 ID, allowed_values, label_evidence) → 표본 필터 → [보류 창 거래 제외] → 거래 묶음(선택: 원천 시각 창)
    → 80/20 분할 → 정규화 행.

    실데이터 export와 합성 사례가 같은 경로를 쓴다. 반환의 rows 값은 파일로만 쓰고 출력하지 않는다.
    drop_held_transactions: 해석된 START·END 시각 전체의 [최소, 최대]가 보류 창 [HELD_LOW, 2026-10-03T00:00Z)과
    겹치는 거래를 통째로 뺀다(창 안 시각이 하나라도 있거나 창을 가로지르는 거래). 남은 거래는 시각이 모두 창 앞이거나
    모두 창 뒤라 이후 앞쪽/뒤쪽 창이 거래를 가르지 않는다.
    """
    from trainer.source_time import cut_reference_current, group_by_transaction

    counts: dict[str, Any] = {"observations": 0, "excluded_status_not_S": 0, "excluded_anomaly_label": 0}
    sample = []
    for _, raw, label_evidence in observations:
        counts["observations"] += 1
        if apply_filter and not eligible(raw, label_evidence):
            counts["excluded_status_not_S" if raw.get("status") != "S" else "excluded_anomaly_label"] += 1
            continue
        sample.append(raw)
    counts["held_transactions_dropped"] = counts["held_rows_dropped"] = 0
    if drop_held_transactions:
        low, high = held_window_bounds()
        spans: dict[Any, list] = {}
        for raw in sample:
            tx = raw.get("transaction_id")
            for stamp in row_timestamps(raw, source_tz):
                span = spans.setdefault(tx, [stamp, stamp])
                span[0], span[1] = min(span[0], stamp), max(span[1], stamp)
        held = {tx for tx, (first, last) in spans.items() if first < high and last >= low}
        counts["held_transactions_dropped"] = len(held)
        kept = [raw for raw in sample if raw.get("transaction_id") not in held]
        counts["held_rows_dropped"] = len(sample) - len(kept)
        sample = kept
    grouping = group_by_transaction(sample, source_tz, window=window)
    counts["grouping"] = grouping.counts
    cut = cut_reference_current(grouping.groups, reference_ratio)
    counts["cut"] = cut.counts
    parse_counts: dict[str, int] = {}
    sides = {}
    for side, groups in (("reference", cut.reference), ("current", cut.current)):
        rows, transactions = [], []
        for group in groups:
            tx = f"t{len(transactions) + 1:06d}"
            transactions.append(tx)
            for raw in group.rows:
                row = normalize_row(raw, source_tz, parse_counts)
                row["TX_ID"] = tx
                row["STATUS"] = str(raw.get("status") or "")
                rows.append(row)
        sides[side] = {"rows": rows, "transactions": transactions}
    counts["value_parse_failures"] = dict(sorted(parse_counts.items()))
    spans = {side: timestamp_span(sides[side]["rows"]) for side in sides}
    return {"reference": sides["reference"], "current": sides["current"], "reference_period": cut.reference_period,
            "current_period": cut.current_period, "timestamp_spans": spans, "counts": counts}


def row_timestamps(raw: Mapping[str, Any], source_tz: str | None) -> list:
    """행의 해석 가능한 START·END 시각(UTC aware) 전부. 행 시각(END 먼저)과 별도로 보류 계보 검사에 쓴다."""
    out = []
    for value in (process_source_time(raw.get("start_time_raw"), None, source_tz)[0],
                  process_source_time(None, raw.get("end_time_raw"), source_tz)[0]):
        if value is not None:
            out.append(value)
    return out


def timestamp_span(rows: Sequence[Mapping[str, str]]) -> dict[str, str] | None:
    """정규화 행의 START_TIME·END_TIME(비지 않은 값) 전체 [최소, 최대+1µs).

    고정 UTC 형식 문자열이라 문자열 최소·최대가 시각 최소·최대다.
    """
    from datetime import datetime, timedelta
    stamps = [row[c] for row in rows for c in TIME_COLUMNS if row.get(c)]
    if not stamps:
        return None
    last = datetime.fromisoformat(max(stamps)) + timedelta(microseconds=1)
    return {"start_inclusive": min(stamps), "end_exclusive": format_utc(last)}


def split_held_overlap(split: Mapping[str, Any]) -> bool:
    """분할 결과의 보류 창 겹침: 기간(묶음 시각) 또는 각 쪽 포함 행의 START·END 전체 범위 중 하나라도 겹치면 참."""
    periods = [split["reference_period"], split["current_period"]]
    periods += [span for span in split.get("timestamp_spans", {}).values() if span]
    return any(held_window_overlap(period) for period in periods)


# 보류 계보(data_0930/1001/1002)는 시간대 없는 KST 원문 날짜다. 번들 입장 검사는 기간 문자열을 "2026-09-30"·"2026-10-03"과
# 비교하지만 우리 기간은 UTC라, KST 9/30 00:00~08:59(UTC 9/29 15:00~23:59) 행이 번들 검사를 통과할 수 있다.
# 두 해석(UTC 날짜·KST 날짜)을 모두 막도록 하한을 KST 9/30 00:00 = UTC 9/29 15:00으로 당긴 보수적 경계를 쓴다.
HELD_LOW = "2026-09-29T15:00:00.000000+00:00"  # 이 시각 이후로 끝나는(end_exclusive > LOW) 기간은 겹침
HELD_HIGH = "2026-10-03"  # 이 날짜(UTC) 이전에 시작하는(start_inclusive < HIGH) 기간은 겹침. KST 10/3 00:00~08:59도 막혀 보수적


def held_window_overlap(period: Mapping[str, str]) -> bool:
    """보류 창 겹침(보수적). 번들 Dataset 검사(lo < "2026-10-03" and hi > "2026-09-30")를 포함하고 KST 해석까지 막는다.

    기간 문자열은 고정 길이 UTC 형식이라 문자열 비교가 시각 비교와 같다. end_exclusive = LOW이면 겹치지 않는다.
    """
    lo, hi = period.get("start_inclusive"), period.get("end_exclusive")
    return bool(lo and hi and lo < HELD_HIGH and hi > HELD_LOW)


def held_window_bounds():
    """보류 창 [HELD_LOW, 2026-10-03T00:00Z) 시각(UTC aware)."""
    from datetime import datetime, timezone
    return datetime(2026, 9, 29, 15, 0, 0, tzinfo=timezone.utc), datetime(2026, 10, 3, tzinfo=timezone.utc)


def held_window_sides():
    """보류 창을 피하는 원천 시각 창(UTC, [from, until)).

    - after: [2026-10-03T00:00:00Z, ∞) → start_inclusive ≥ "2026-10-03"(UTC·KST 해석 모두 보류 창 뒤)
    - before: (-∞, 2026-09-29T15:00:00Z) → 마지막 행 ≤ 14:59:59.999999Z, end_exclusive(마지막+1µs) ≤ HELD_LOW → 겹치지 않음
    """
    from datetime import datetime, timezone
    after = datetime(2026, 10, 3, tzinfo=timezone.utc)
    before = datetime(2026, 9, 29, 15, 0, 0, tzinfo=timezone.utc)
    return {"after": (after, None), "before": (None, before)}


# ---- 독립 계산(번들 코드를 쓰지 않는 확인용) ----

def _parse_utc(text: str):
    from datetime import datetime
    return datetime.fromisoformat(text)


def field_value(field: str, row: Mapping[str, str]) -> Decimal | None:
    """정규화 행 하나의 필드 값(번들 계산 계약과 같은 의미, 별도 구현)."""
    with localcontext() as ctx:
        ctx.prec = 50
        if field == "elapsed_ms":
            if not row.get("START_TIME") or not row.get("END_TIME"):
                return None
            delta = _parse_utc(row["END_TIME"]) - _parse_utc(row["START_TIME"])
            micro = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
            return Decimal(micro) / Decimal(1000)
        if field == "error_ratio":
            if not row.get("ERROR_COUNT") or not row.get("TOTAL_COUNT"):
                return None
            denominator = Decimal(row["TOTAL_COUNT"])
            return None if denominator == 0 else Decimal(row["ERROR_COUNT"]) / denominator
        column = FIELDS[field]["columns"][0]
        return Decimal(row[column]) if row.get(column) else None


def tx_ratio_values(rows: Sequence[Mapping[str, str]], transactions: Sequence[str]) -> list[Decimal | None]:
    with localcontext() as ctx:
        ctx.prec = 50
        out = []
        for tx in transactions:
            linked = [r for r in rows if r["TX_ID"] == tx]
            out.append(None if not linked else Decimal(sum(r["STATUS"] == "F" for r in linked)) / Decimal(len(linked)))
        return out


def p95(values: Iterable[Decimal | None]) -> Decimal | None:
    """linear (n-1)*p, Decimal 정밀도 50."""
    numbers = sorted(v for v in values if v is not None)
    if not numbers:
        return None
    with localcontext() as ctx:
        ctx.prec = 50
        position = Decimal(len(numbers) - 1) * Decimal("0.95")
        lower = int(position)
        upper = min(lower + 1, len(numbers) - 1)
        return numbers[lower] + (numbers[upper] - numbers[lower]) * (position - lower)


def canonical_number(value: Any) -> str | None:
    """번들 JSON 수(_json_number: 정수면 int, 아니면 float)와 같은 표기로 맞춘 비교 문자열."""
    if value is None or isinstance(value, bool):
        return None
    number = Decimal(str(value)) if not isinstance(value, Decimal) else value
    if number == number.to_integral_value():
        return str(int(number))
    return repr(float(number))


def decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    with localcontext() as ctx:  # normalize()는 문맥 정밀도로 반올림하므로 자릿수만큼 넉넉히 둔다
        ctx.prec = max(60, len(value.as_tuple().digits) + 2)
        return format(value.normalize(), "f")


def expected_outcome(*, reference_valid: int, minimum_n: int, unit_verified: bool, current_valid: int,
                     constant: bool, range_max: Decimal | None, p95_value: Decimal | None) -> dict[str, Any]:
    """번들 baseline()·validate_rule·rule_system 순서로 정해지는 기대 상태(코드 근거, 모델 답 아님).

    - 단위 미검증 → request_evidence [need_verified_unit] (baseline(): incompatible 다음, 표본 수보다 먼저)
    - 참조 유효값 < minimum_n → request_evidence [need_reference_minimum_n]
    - 증명된 상한 ≤ p95 → no_applicable (validate_rule structurally_unreachable, 프롬프트: 현재 0건이어도 우선)
    - 현재 유효값 0 → deferred [need_valid_observations] (validate_rule no_current_numeric_observations)
    - 상수 참조 → proposed(gt p95) 또는 no_applicable 둘 다 검증기 통과. 서비스 코드는 상수면 LLM 없이 no_applicable
    - 그 밖 → proposed, condition {op: gt, value: p95}
    """
    if not unit_verified:
        return {"baseline_state": "unit_unconfirmed", "states": ["request_evidence"], "needed": ["need_verified_unit"]}
    if reference_valid < minimum_n:
        return {"baseline_state": "insufficient", "states": ["request_evidence"], "needed": ["need_reference_minimum_n"]}
    if range_max is not None and p95_value is not None and range_max <= p95_value:
        return {"baseline_state": "verified", "states": ["no_applicable"], "needed": []}
    if current_valid == 0:
        return {"baseline_state": "verified", "states": ["deferred"], "needed": ["need_valid_observations"]}
    if constant:
        return {"baseline_state": "verified", "states": ["proposed", "no_applicable"], "needed": []}
    return {"baseline_state": "verified", "states": ["proposed"], "needed": []}


def range_max_for(field: str) -> Decimal | None:
    """번들 range_proof가 증명하는 상한(conditional_count/child_count 같은 관계 → 1). 열 기반 필드는 없음."""
    return Decimal(1) if field == TX_FIELD else None


def unit_verified_in(capture: Mapping[str, Any], field: str) -> bool:
    """캡처 선언으로 본 필드 단위 확인 여부. 시각 차는 ms가 자동, 비율은 두 피연산자 모두 확인돼야 함."""
    if field == "elapsed_ms" or field == TX_FIELD:
        return True
    columns = {c["name"]: c for t in capture["tables"] for c in t["columns"]}
    return all((columns[c].get("unit") or {}).get("status") == "verified" for c in FIELDS[field]["columns"])


def read_capture_rows(capture_path: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, str]]]]:
    capture = read_json(capture_path)
    tables = {}
    for table in capture["tables"]:
        with (capture_path.parent / table["file"]).open(encoding="utf-8", newline="") as handle:
            tables[table["name"]] = list(csv.DictReader(handle))
    return capture, tables


def capture_values(capture_path: Path, field: str) -> list[Decimal | None]:
    capture, tables = read_capture_rows(capture_path)
    if field == TX_FIELD:
        return tx_ratio_values(tables["PROCESS"], [r["TX_ID"] for r in tables["TRANSACTION"]])
    return [field_value(field, row) for row in tables["PROCESS"]]


def facts_from_case(case_dir: Path, field: str, minimum_n: int = 20) -> dict[str, Any]:
    """사례 폴더의 CSV에서 다시 계산한 사실과 기대 상태. 값(p95)은 Decimal 그대로 둔다(출력 여부는 호출자 몫)."""
    reference_capture = case_dir / "reference" / "capture.json"
    reference = capture_values(reference_capture, field)
    current = capture_values(case_dir / "application" / "capture.json", field)
    valid = [v for v in reference if v is not None]
    value = p95(reference)
    facts = {"reference_rows": len(reference), "reference_valid": len(valid), "current_rows": len(current),
             "current_valid": sum(v is not None for v in current), "constant": len(set(valid)) == 1,
             "p95": value, "unit_verified": unit_verified_in(read_json(reference_capture), field),
             "range_max": range_max_for(field)}
    facts["expected"] = expected_outcome(reference_valid=facts["reference_valid"], minimum_n=minimum_n,
                                         unit_verified=facts["unit_verified"], current_valid=facts["current_valid"],
                                         constant=facts["constant"], range_max=facts["range_max"], p95_value=value)
    return facts

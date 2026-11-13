"""Rule v3 묶음 입력(approved-metadata-capture1.0) 작성. 주말 시험 키트의 사례 배치를 서비스 필드 6종만 옮겼다.

한 필드 = 폴더 하나:

    <field>/reference/{capture.json, PROCESS.csv, authority.json}    # 참조 = train 앞 80%(split "train")
    <field>/application/{capture.json, PROCESS.csv, authority.json}  # 현재 = train 뒤 20%(split "application")
    <field>/proposal.json, request.json, rule_plan.json

- 표본: status=S, 명시 anomaly 라벨 제외(`threshold_recommendation.reference_sample`과 같음)
- 분할: 원천 시각(END 먼저) 거래 묶음 → 블록 단위 80/20(`trainer.source_time.cut_reference_current`)
- 보류 창: 번들 입장 검사가 거부하는 기간(9/30~10/2)과 겹치면 주말 시험 키트 export의 회피 규칙을 그대로 쓴다
  (`split_avoiding_held`). 겹치는 거래를 통째로 빼고 창 뒤쪽만, 안 되면 앞쪽만 남긴다. 뺀 행 수는 결과에 표시한다
- 시각 열은 원문이 아니라 `process_source_time`으로 해석한 UTC 고정 형식, 숫자 열은 `derived_expression.number`.
  해석 실패·값 없음은 빈 칸(결측)
- count 열 단위 근거는 소프트룰 필드 카탈로그(unit=count)를 `document_confirmed`로 넣는다(카탈로그 선언이며
  원천 문서가 따로 확인한 것은 아님)

이 모듈은 파일을 쓰기만 한다. 행 값은 로그·결과에 내보내지 않는다.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from derived_expression import number as service_number
from trainer.source_time import cut_reference_current, format_utc, group_by_transaction, process_source_time

MINIMUM_N = 20
REFERENCE_RATIO = 0.8
UNIT_POINTER = "/unit_count"
TIME_COLUMNS = ("START_TIME", "END_TIME")
COUNT_COLUMNS = {"TOTAL_COUNT": "total_count", "SUCCESS_COUNT": "success_count",
                 "ERROR_COUNT": "error_count", "RETRY_COUNT": "retry_count"}
RAW_KEYS = {"START_TIME": "start_time_raw", "END_TIME": "end_time_raw", **COUNT_COLUMNS}
AUTHORITY = {"unit_count": {"name": "count",
                            "meaning": "PROCESS counter unit declared by the service soft-rule field catalog (unit=count)."},
             "note": "Unit authority for count columns only; no business meaning, SLA or incident label."}
UNKNOWN = ["No incident labels, SLA, business meaning or online availability are confirmed by this capture.",
           "Current period is the later part of the same TRAIN snapshot members, split by source time."]


def _col(name: str) -> dict[str, Any]:
    return {"op": "column", "table": "PROCESS", "column": name}


# 서비스 SOFT_RULE_FIELDS 6종. 문장은 주말 GPU 시험에 쓴 것과 같다(모델 입력이 바뀌지 않게).
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

# 번들 입장 검사(specmodel/dataset.py)는 기간 문자열이 9/30~10/2 보류 창에 걸치면 거부한다. 주말 키트와 같은
# 보수적 경계를 쓴다: 보류 날짜는 시간대 없는 KST 날짜라 하한을 KST 9/30 00:00(= UTC 9/29 15:00)으로 당기고,
# 상한은 UTC 10/3 00:00(KST 10/3 09:00)이라 UTC 날짜·KST 날짜 해석을 모두 막는다.
HELD_FROM = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)
HELD_UNTIL = datetime(2026, 10, 3, tzinfo=timezone.utc)
HELD_LOW = format_utc(HELD_FROM)
HELD_HIGH = "2026-10-03"
# 회피 시 남길 원천 시각 창 [from, until)의 고정 순서: 뒤쪽 먼저, 안 되면 앞쪽.
HELD_SIDES = (("after", (HELD_UNTIL, None)), ("before", (None, HELD_FROM)))


def source_timezone(clock_basis: Any) -> str | None:
    """`ops_sources.clock_basis` → 원본 시간대(None = 미검증 → Asia/Seoul 가정). Detector 사건 묶음과 같은 규칙."""
    basis = clock_basis if isinstance(clock_basis, dict) else {}
    if basis.get("status") != "verified":
        return None
    zone = basis.get("timezone")
    return zone if isinstance(zone, str) else ""


def eligible(raw: Mapping[str, Any], label_evidence: Any) -> bool:
    label = label_evidence.get("label") if isinstance(label_evidence, dict) else None
    return raw.get("status") == "S" and label != "anomaly"


def normalize_row(raw: Mapping[str, Any], source_tz: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for column in TIME_COLUMNS:
        raw_value = raw.get(RAW_KEYS[column])
        value, _, _ = process_source_time(raw_value, None, source_tz) if column == "START_TIME" else \
            process_source_time(None, raw_value, source_tz)
        out[column] = "" if value is None else format_utc(value)
    for column, key in COUNT_COLUMNS.items():
        value = service_number(raw.get(key))
        out[column] = "" if value is None else str(value)
    return out


def _row_times(raw: Mapping[str, Any], source_tz: str | None) -> list[datetime]:
    """행의 해석 가능한 START·END 시각 전부(행 시각 규칙과 별도로 보류 창 검사에 쓴다)."""
    times = (process_source_time(raw.get("start_time_raw"), None, source_tz)[0],
             process_source_time(None, raw.get("end_time_raw"), source_tz)[0])
    return [value for value in times if value is not None]


def split_sample(rows: Iterable[Sequence[Any]], source_tz: str | None, *,
                 window: tuple[datetime | None, datetime | None] | None = None,
                 drop_held: bool = False) -> dict[str, Any]:
    """snapshot 행 `(observation_id, allowed_values, split, label_evidence)` → 표본 → [보류 창 거래 제외]
    → 거래 묶음(선택: 원천 시각 창) → 80/20 정규화 행.

    `drop_held`: 해석된 START·END 시각의 [최소, 최대]가 보류 창 [HELD_FROM, HELD_UNTIL)과 겹치는 거래를 통째로 뺀다.
    분할할 수 없으면 `trainer.source_time`의 ValueError(`time_split_unavailable` 등)가 그대로 나간다.
    """
    sample = [raw for _, raw, _, label in rows if eligible(raw, label)]
    held_rows = 0
    if drop_held:
        spans: dict[Any, list[datetime]] = {}
        for raw in sample:
            for stamp in _row_times(raw, source_tz):
                span = spans.setdefault(raw.get("transaction_id"), [stamp, stamp])
                span[0], span[1] = min(span[0], stamp), max(span[1], stamp)
        held = {tx for tx, (first, last) in spans.items() if first < HELD_UNTIL and last >= HELD_FROM}
        kept = [raw for raw in sample if raw.get("transaction_id") not in held]
        held_rows, sample = len(sample) - len(kept), kept
    grouping = group_by_transaction(sample, source_tz, window=window)
    cut = cut_reference_current(grouping.groups, REFERENCE_RATIO)
    sides = {}
    for side, groups in (("reference", cut.reference), ("current", cut.current)):
        sides[side] = [normalize_row(raw, source_tz) for group in groups for raw in group.rows]
    return {"reference": sides["reference"], "current": sides["current"],
            "reference_period": cut.reference_period, "current_period": cut.current_period,
            "counts": {"sampleRows": len(sample) + held_rows, "groupedRows": grouping.counts["rows_grouped"],
                       "excludedUnparsedTime": grouping.counts["excluded_unparsed_time"],
                       "excludedMissingTransaction": grouping.counts["excluded_missing_transaction"],
                       "heldRows": held_rows, "outsideSideRows": grouping.counts["outside_source_window"],
                       "referenceRows": cut.counts["reference_rows"], "currentRows": cut.counts["current_rows"]}}


def split_avoiding_held(rows: Sequence[Sequence[Any]], source_tz: str | None
                        ) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """키트 export `--avoid-held-window`와 같은 규칙. (분할, `heldWindowExclusion` 또는 None).

    겹침이 없으면 그대로. 겹치면 보류 창 거래를 빼고 뒤쪽(≥ HELD_UNTIL), 그래도 분할·검사가 안 되면 앞쪽(< HELD_FROM)만
    남긴다. 두 쪽 모두 안 되면 ValueError("held_window_avoid_failed"). 참조·현재 기간이 창을 가로지르지 않으려면 한쪽만
    남겨야 한다(참조 기간은 첫 참조 시각 ~ 첫 현재 시각이라 창 양쪽에 걸치면 창을 덮는다).
    """
    rows = list(rows)
    split = split_sample(rows, source_tz)
    if not held_window_overlap(split):
        return split, None
    for _, bounds in HELD_SIDES:
        try:
            trial = split_sample(rows, source_tz, window=bounds, drop_held=True)
        except ValueError:
            continue
        if not held_window_overlap(trial):
            counts = trial["counts"]
            # from은 보류 날짜의 시간대 가정(Asia/Seoul) 그대로 KST 9/30 00:00, until은 UTC 10/3 00:00.
            return trial, {"from": HELD_FROM.astimezone(ZoneInfo("Asia/Seoul")).isoformat(),
                           "until": HELD_UNTIL.isoformat(),
                           "excludedRows": counts["heldRows"] + counts["outsideSideRows"]}
    raise ValueError("held_window_avoid_failed")


def _span(rows: Sequence[Mapping[str, str]]) -> dict[str, str] | None:
    stamps = [row[c] for row in rows for c in TIME_COLUMNS if row.get(c)]
    if not stamps:
        return None
    last = datetime.fromisoformat(max(stamps)) + timedelta(microseconds=1)
    return {"start_inclusive": min(stamps), "end_exclusive": format_utc(last)}


def _held(period: Mapping[str, str] | None) -> bool:
    lo, hi = (period or {}).get("start_inclusive"), (period or {}).get("end_exclusive")
    return bool(lo and hi and lo < HELD_HIGH and hi > HELD_LOW)


def held_window_overlap(split: Mapping[str, Any]) -> bool:
    """두 기간(묶음 시각) 또는 각 쪽 행의 START·END 전체 범위 중 하나라도 보류 창과 겹치면 참."""
    periods = [split["reference_period"], split["current_period"], _span(split["reference"]), _span(split["current"])]
    return any(_held(period) for period in periods)


def field_value(field: str, row: Mapping[str, str]) -> Decimal | None:
    """정규화 행 하나의 필드 값(번들 계산과 같은 뜻의 서버 계산). 코드 p95·사실 대조에 쓴다."""
    with localcontext() as ctx:
        ctx.prec = 50
        if field == "elapsed_ms":
            if not row.get("START_TIME") or not row.get("END_TIME"):
                return None
            delta = datetime.fromisoformat(row["END_TIME"]) - datetime.fromisoformat(row["START_TIME"])
            micro = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
            return Decimal(micro) / Decimal(1000)
        if field == "error_ratio":
            if not row.get("ERROR_COUNT") or not row.get("TOTAL_COUNT"):
                return None
            denominator = Decimal(row["TOTAL_COUNT"])
            return None if denominator == 0 else Decimal(row["ERROR_COUNT"]) / denominator
        column = FIELDS[field]["columns"][0]
        return Decimal(row[column]) if row.get(column) else None


def p95(values: Iterable[Decimal | None]) -> Decimal | None:
    """linear (n-1)*p, Decimal 정밀도 50(번들 `primitives` 분위 정의와 같음)."""
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
    """번들 JSON 수(정수면 int, 아니면 float)와 같은 표기로 맞춘 비교 문자열."""
    if value is None or isinstance(value, bool):
        return None
    number = value if isinstance(value, Decimal) else Decimal(str(value))
    if number == number.to_integral_value():
        return str(int(number))
    return repr(float(number))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _csv(header: Sequence[str], rows: Iterable[Sequence[str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _column_spec(name: str) -> dict[str, Any]:
    if name in TIME_COLUMNS:
        logical, unit = "datetime", {"name": None, "status": "unknown"}
        description = "PROCESS source time normalized to UTC from the raw text by the documented source-time rule."
    elif name in COUNT_COLUMNS:
        logical = "number"
        unit = {"name": "count", "status": "verified", "evidence_ids": [f"unit_PROCESS_{name}"]}
        description = "PROCESS numeric counter from the collected row; non-numeric text is stored as missing."
    elif name == "ROW_ID":
        logical, unit = "string", {"name": None, "status": "unknown"}
        description = "Sequential row identifier assigned by the capture writer."
    else:
        raise ValueError("unknown_column")
    return {"name": name, "logical_type": logical, "nullable": name != "ROW_ID", "excluded": False,
            "unit": unit, "missing_tokens": [""], "description": description}


def write_capture(folder: Path, *, field: str, rows: Sequence[Mapping[str, str]], split: str,
                  period: Mapping[str, str], identity: Mapping[str, Any]) -> None:
    spec = FIELDS[field]
    folder.mkdir(parents=True, exist_ok=False)
    columns = ["ROW_ID"] + spec["columns"]
    data = _csv(columns, ([f"r{index + 1:06d}"] + [row[c] for c in columns[1:]] for index, row in enumerate(rows)))
    (folder / "PROCESS.csv").write_bytes(data)
    authority = (json.dumps(AUTHORITY, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
    (folder / "authority.json").write_bytes(authority)
    evidence = [{"id": f"unit_PROCESS_{column}", "kind": "unit", "table": "PROCESS", "column": column,
                 "confirmation": "document_confirmed", "value": AUTHORITY["unit_count"],
                 "source": {"file": "authority.json", "sha256": _sha(authority), "pointer": UNIT_POINTER}}
                for column in spec["columns"] if column in COUNT_COLUMNS]
    _json(folder / "capture.json", {
        "contract": "approved-metadata-capture1.0", "dataset_id": identity["dataset_id"],
        "data_class": "approved_real", "split": split, "period": dict(period),
        "admission": {"approved": True, "Body_excluded": True, "held_lineage_excluded": True,
                      "complete_family_capture": True},
        "lineage_tags": [],
        "tables": [{"name": "PROCESS", "file": "PROCESS.csv", "format": "csv", "sha256": _sha(data),
                    "row_count": len(rows), "primary_key": ["ROW_ID"],
                    "columns": [_column_spec(c) for c in columns]}],
        "relationships": [], "evidence": evidence, "source_identity": dict(identity),
        "transaction_families": 0, "unknown": list(UNKNOWN)})


def proposal_for(field: str) -> dict[str, Any]:
    spec = FIELDS[field]
    return {"proposal_id": f"soft_rule_{field}",
            "plan": {"expression": spec["expression"], "grain": {"table": "PROCESS", "keys": ["ROW_ID"]},
                     "population": {"table": "PROCESS", "filter": None}, "group_by": [], "result_use": "value"},
            "purpose": spec["purpose"], "meaning": spec["meaning"], "needed_evidence": []}


def request_for(field: str) -> dict[str, Any]:
    spec = FIELDS[field]
    return {"question": spec["purpose"],
            "analysis_request": {"output_table": "PROCESS", "result_use": "value", "calculation_type": "number",
                                 "required_columns": [["PROCESS", c] for c in spec["columns"]],
                                 "required_relationship_ids": []}}


def rule_plan_for(field: str) -> dict[str, Any]:
    return {"requests": ["baseline"],
            "question": f"Offline numeric soft-rule threshold for {field}: compare current values with the code "
                        "p95 of the earlier TRAIN reference; no incident, SLA or online claim.",
            "reference_policy": {"minimum_n": MINIMUM_N, "operator": "gt"}}


def write_case(case_dir: Path, field: str, split: Mapping[str, Any], identity: Mapping[str, Any]) -> dict[str, Path]:
    """필드 폴더 하나를 쓰고 번들에 넘길 경로를 돌려준다. 폴더가 이미 있으면 실패한다."""
    case_dir.mkdir(parents=True, exist_ok=False)
    for side, rows, period, name in (("reference", split["reference"], split["reference_period"], "train"),
                                     ("application", split["current"], split["current_period"], "application")):
        write_capture(case_dir / side, field=field, rows=rows, split=name, period=period,
                      identity={**identity, "dataset_id": f"{identity['dataset_id']}_{side}", "side": side})
    paths = {"application": case_dir / "application/capture.json", "reference": case_dir / "reference/capture.json",
             "proposal": case_dir / "proposal.json", "request": case_dir / "request.json",
             "rule_plan": case_dir / "rule_plan.json"}
    _json(paths["proposal"], proposal_for(field))
    _json(paths["request"], request_for(field))
    _json(paths["rule_plan"], rule_plan_for(field))
    return paths

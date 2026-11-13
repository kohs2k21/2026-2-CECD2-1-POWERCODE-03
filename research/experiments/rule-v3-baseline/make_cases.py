"""합성 경계 사례 생성: 실데이터에 없을 수 있는 경계(n<20·단위 미검증·상수·증명 상한·현재 0건)와 경로 확인.

값은 고정 시드로 지어낸 것이며 고객 값이 아니다. 실데이터 export와 같은 경로
(표본 필터 → source_time 거래 묶음 → 80/20 분할 → 정규화 → capture 기록)를 지난다.

    python make_cases.py --output cases          # 저장소에 커밋하는 고정 사례(폴더가 있으면 실패)
"""
from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import capture_layout as layout  # noqa: E402

FORMAT = "rule-v3-baseline-export-v1"
BASE = datetime(2026, 8, 3, 9, 0, 0)  # 시간대 없는 원문(KST 가정) 기준. 보류 9/30~10/2 밖
AUTHORITY = {"unit_count": {"name": "count", "meaning": "Synthetic controlled counter unit for boundary tests."},
             "note": "Synthetic controlled authority; no operational source."}
UNKNOWN = ["Synthetic boundary data with no operational meaning, incident labels or online availability."]


def raw_time(at: datetime, style: str) -> str:
    if style == "offset":
        return at.isoformat(sep="T", timespec="milliseconds") + "+09:00"
    return at.isoformat(sep=" ", timespec="milliseconds")


def series(seed: int, transactions: int, *, procs=(1, 3), elapsed=(50, 4000), tie_every: int = 0,
           noise: bool = True, counts=None, end_missing_from: int | None = None):
    """allowed_values 형태 원천 행을 만든다. noise=True면 필터·해석 실패 행을 섞는다(세어지고 빠져야 함)."""
    rng = random.Random(seed)
    rows, clock, first_spent = [], BASE, None
    for tx in range(transactions):
        tie = bool(tie_every and tx % tie_every == 1)  # 직전 거래와 묶음 시각(첫 END)이 같아지도록 만든다
        if not tie:
            clock += timedelta(seconds=rng.randint(1, 30))
        tx_id = f"SYN{seed:03d}-{tx:05d}"
        for proc in range(rng.randint(*procs)):
            start = clock + timedelta(milliseconds=proc * 7)
            spent = rng.randint(*elapsed) if rng.random() > .05 else rng.randint(elapsed[1], elapsed[1] * 3)
            if proc == 0:
                spent = first_spent if tie and first_spent is not None else spent
                first_spent = spent
            total = rng.randint(5, 20)
            error = counts(rng, total) if counts else 0
            style = "offset" if rng.random() < .1 else "naive"
            end = "" if end_missing_from is not None and tx >= end_missing_from else \
                raw_time(start + timedelta(milliseconds=spent), style)
            rows.append({"transaction_id": tx_id, "process_id": f"P{proc}", "status": "S",
                         "start_time_raw": raw_time(start, style), "end_time_raw": end,
                         "total_count": total, "success_count": total - error, "error_count": error,
                         "retry_count": rng.choice([0, 0, 0, 1, 2])})
    if noise:
        anchor = rows[len(rows) // 3]
        rows.append({**anchor, "process_id": "F1", "status": "F"})                     # 상태 F → 제외
        rows.append({**anchor, "process_id": "L1"})                                    # 이상 라벨 → 제외
        rows.append({**anchor, "process_id": "U1", "start_time_raw": "N/A", "end_time_raw": "bad"})  # 시각 해석 실패
        rows.append({**anchor, "process_id": "X1", "transaction_id": None})            # 거래 ID 없음
    labels = {id(r): ({"label": "anomaly"} if r.get("process_id") == "L1" else None) for r in rows}
    return [(f"obs-{index}", row, labels[id(row)]) for index, row in enumerate(rows)]


def variable_errors(rng, total):
    return rng.choice([0, 0, 0, 1, 1, 2, 3, min(total, 5)])


CASES = [
    # case_id, field, 설명, 생성 인수, unit_verified
    ("syn-001", "elapsed_ms", "정상 경로: 처리 시간 p95 비교(합성 smoke)", dict(seed=1, transactions=150, tie_every=9), True),
    ("syn-002", "error_ratio", "정상 경로: 오류 비율 p95(단위 확인 count/count→ratio)",
     dict(seed=2, transactions=150, counts=variable_errors), True),
    ("syn-003", "error_count", "정상 경로: count 단위 근거가 있는 오류 건수",
     dict(seed=3, transactions=150, counts=variable_errors), True),
    ("syn-004", "elapsed_ms", "참조 유효값 20 미만 → need_reference_minimum_n",
     dict(seed=4, transactions=20, procs=(1, 1), noise=False), True),
    ("syn-005", "error_ratio", "단위 미검증(비율) → need_verified_unit", dict(seed=5, transactions=120, counts=variable_errors), False),
    ("syn-006", "retry_count", "단위 미검증(건수) → need_verified_unit", dict(seed=6, transactions=120), False),
    ("syn-007", "elapsed_ms", "현재 END_TIME 전부 없음 → 현재 유효 0 → deferred",
     dict(seed=7, transactions=100, procs=(1, 2), noise=False, end_missing_from=80), True),
    ("syn-008", "error_count", "참조 상수(전부 0) → proposed(gt 0)·no_applicable 모두 검증기 통과",
     dict(seed=8, transactions=120), True),
    ("syn-009", "tx_failed_ratio", "증명된 상한 1 ≤ 참조 p95 1 → no_applicable", dict(seed=9, transactions=150), True),
]


def tx_series(seed: int, transactions: int):
    """거래별 실패 PROCESS 비율. 참조 상위 10% 거래는 전부 F라 p95=1(상한 1과 같음)."""
    rng = random.Random(seed)
    rows, clock = [], BASE
    for tx in range(transactions):
        clock += timedelta(seconds=rng.randint(1, 30))
        size = rng.randint(1, 4)
        all_failed = tx % 10 == 0
        for proc in range(size):
            status = "F" if all_failed or rng.random() < .1 else "S"
            start = clock + timedelta(milliseconds=proc * 5)
            rows.append({"transaction_id": f"SYN{seed:03d}-{tx:05d}", "process_id": f"P{proc}", "status": status,
                         "start_time_raw": raw_time(start, "naive"),
                         "end_time_raw": raw_time(start + timedelta(milliseconds=100), "naive")})
    return [(f"obs-{i}", row, None) for i, row in enumerate(rows)]


def build(output: Path) -> dict:
    if output.exists():
        raise SystemExit(f"output exists: {output}")
    output.mkdir(parents=True)
    index = {"format": FORMAT, "kind": "synthetic", "generator": "make_cases.py",
             "period_format": "YYYY-MM-DDTHH:MM:SS.ffffff+00:00 (UTC, fixed length)",
             "reference_ratio": 0.8, "exclusion_rule": layout.EXCLUSION_RULE, "minimum_n": 20,
             "input_assumptions": ["Synthetic controlled data: count units are a controlled_contract test authority, "
                                   "not an operational assumption."], "cases": []}
    for case_id, field, purpose, args, unit_verified in CASES:
        if field == layout.TX_FIELD:
            split = layout.split_observations(tx_series(args["seed"], args["transactions"]), None, apply_filter=False)
        else:
            split = layout.split_observations(series(**args), None)
        case_dir = output / case_id
        files = layout.write_case(
            case_dir, field=field, reference_rows=split["reference"]["rows"], current_rows=split["current"]["rows"],
            reference_period=split["reference_period"], current_period=split["current_period"],
            data_class="controlled", unit_verified=unit_verified, confirmation="controlled_contract",
            authority=AUTHORITY, identity={"dataset_id": f"synthetic_{case_id}", "case": purpose,
                                           "origin": "synthetic seeded generator, no operational source"},
            unknown=UNKNOWN, reference_transactions=split["reference"]["transactions"],
            current_transactions=split["current"]["transactions"])
        facts = layout.facts_from_case(case_dir, field)
        expected = dict(facts["expected"])
        expected["condition"] = {"op": "gt", "value_decimal": layout.decimal_text(facts["p95"]),
                                 "value_canonical": layout.canonical_number(facts["p95"])} \
            if "proposed" in expected["states"] else None
        expected["reference_p95_decimal"] = layout.decimal_text(facts["p95"])
        index["cases"].append({
            "case_id": case_id, "field": field, "scope": "synthetic_boundary", "kind": "synthetic", "purpose": purpose,
            "path": case_id, "files": files,
            "periods": {"reference": split["reference_period"], "application": split["current_period"]},
            "held_window_overlap": layout.split_held_overlap(split),
            "counts": {**split["counts"], "reference_valid": facts["reference_valid"],
                       "current_valid": facts["current_valid"], "reference_constant": facts["constant"]},
            "expected": expected})
    layout.write_json(output / "index.json", index)
    return index


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=HERE / "cases")
    args = parser.parse_args(argv)
    index = build(args.output.resolve())
    for case in index["cases"]:
        print(case["case_id"], case["field"], case["expected"]["states"], case["expected"]["needed"],
              "ref_valid", case["counts"]["reference_valid"], "cur_valid", case["counts"]["current_valid"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

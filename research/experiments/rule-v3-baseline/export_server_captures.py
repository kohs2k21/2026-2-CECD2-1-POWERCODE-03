"""실데이터 사례 export: 스냅샷 train 멤버 PROCESS 행 → 원천 시각 80/20 → Rule v3 입력(서버 전용, 저장소 밖).

    DATABASE_URL=... python export_server_captures.py --snapshot-id <uuid> --output-dir /data/.../export-<날짜> \
        [--per-interface-top N] [--source-time-from ISO+offset] [--source-time-until ISO+offset] [--avoid-held-window]

- DB는 READ ONLY 트랜잭션으로 읽기만 한다(시작 시 transaction_read_only=on 확인).
- 표본: threshold_recommendation과 같은 규칙(status=S, 명시적 anomaly 라벨 제외, 라벨 없음 유지).
- 시각: trainer.source_time(사건 묶음 occurred_time과 같은 해석: END_TIME 먼저, 없거나 해석 실패면 START_TIME.
  원본 clock_basis가 verified면 그 시간대, 아니면 Asia/Seoul 가정). 선택 원천 시각 창은 묶기 전 행 단위로 적용.
- 보류 창(보류 계보 KST 2026-09-30~10-02. UTC·KST 해석을 모두 막는 보수적 판정, capture_layout.held_window_overlap)과
  겹치면 기본은 표시만(index held_window·사례 held_window_overlap, 실행기가 그 사례를 돌리지 않음).
  --avoid-held-window면 뒤쪽(≥ 2026-10-03T00:00Z)만, 안 되면 앞쪽(< 2026-09-29T15:00Z = KST 9/30 00:00)만 남기고 버린 행 수를 센다.
- 출력 폴더는 git 저장소 밖이어야 하고 없어야 한다. 콘솔에는 개수·상태만 쓴다(행 값·인터페이스 이름 없음).
- 사례: 전역 필드 6개(real-001~006) + 선택 시 인터페이스 상위 N개 × 6필드. 인터페이스 이름은 private/에만 둔다.
- 출력 형식(rule-v3-baseline-export-v1)은 README "export 출력 형식" 절에 고정한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import capture_layout as layout  # noqa: E402
from derived_expression import source_absent  # noqa: E402  (capture_layout이 server/worker를 경로에 넣음)

FORMAT = "rule-v3-baseline-export-v1"
AUTHORITY = {"unit_count": {"name": "count",
                            "meaning": "PROCESS counter unit declared by the service soft-rule field catalog "
                                       "(server/worker/methods/registry.py SOFT_RULE_FIELDS, unit=count)."},
             "note": "Unit authority for count columns only; no business meaning, SLA or incident label."}
UNKNOWN = ["No incident labels, SLA, business meaning or online availability are confirmed by this capture.",
           "Current period is the later part of the same TRAIN snapshot members, split by source time."]

SNAPSHOT_SQL = "SELECT source_id, status::text FROM data_snapshots WHERE id = %s"
CLOCK_SQL = "SELECT clock_basis FROM ops_sources WHERE id = %s"
# runner._snapshot_rows(conn, snapshot_id, 'train')와 같은 멤버·원천 행. 스냅샷은 PROCESS 층만 담지만 layer도 확인한다.
ROWS_SQL = """SELECT m.observation_id, o.allowed_values, m.label_evidence
                FROM data_snapshot_members AS m
                JOIN data_observations AS o ON o.id = m.observation_id
               WHERE m.snapshot_id = %s AND m.split::text = 'train' AND o.layer::text = 'P'
               ORDER BY m.observation_id"""
# data_processes.transaction_observation_id는 수집기가 채우지 않으므로 같은 원본의 TRANSACTION 행을 거래 ID 문자열로 찾는다.
INTERFACE_SQL = """SELECT t.transaction_id, t.interface_id
                     FROM data_transactions AS t
                     JOIN data_observations AS o ON o.id = t.observation_id
                    WHERE o.source_id = %s AND t.transaction_id = ANY(%s)"""


INPUT_ASSUMPTIONS = (
    "Trainer assumption: TOTAL/SUCCESS/ERROR/RETRY_COUNT are integer counts with unit=count as declared by the service "
    "soft-rule field catalog (server/worker/methods/registry.py SOFT_RULE_FIELDS); captured as unit evidence "
    "confirmation=document_confirmed.",
)


def _iso(value: dt.datetime | None) -> str | None:
    return None if value is None else value.astimezone(dt.timezone.utc).isoformat()


def _overlaps(split: Mapping[str, Any]) -> bool:
    """기간(묶음 시각)과 포함 행의 START·END 전체 범위를 모두 본다(capture_layout.split_held_overlap)."""
    return layout.split_held_overlap(split)


def _intersect(a: tuple[Any, Any], b: tuple[Any, Any]) -> tuple[Any, Any]:
    lowers = [x for x in (a[0], b[0]) if x is not None]
    uppers = [x for x in (a[1], b[1]) if x is not None]
    return (max(lowers) if lowers else None, min(uppers) if uppers else None)


def parse_bound(text: str | None) -> dt.datetime | None:
    """--source-time-from/until: 오프셋이 있는 ISO 8601만 받는다(시간대 가정 없음)."""
    if text is None:
        return None
    value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None or value.utcoffset() is None:
        raise SystemExit("--source-time-from/--source-time-until need an explicit UTC offset, e.g. 2026-10-03T00:00:00+09:00")
    return value


def source_timezone(clock_basis: Any) -> tuple[str | None, str]:
    """detector.incidents_store._source_timezone과 같은 규칙(+근거 이름)."""
    basis = clock_basis if isinstance(clock_basis, dict) else {}
    if basis.get("status") != "verified":
        return None, "assumed_Asia/Seoul"
    zone = basis.get("timezone")
    return (zone, "verified") if isinstance(zone, str) else ("", "verified_without_timezone")


def check_output_dir(path: Path) -> Path:
    resolved = path.resolve()
    if layout.REPO.resolve() == resolved or layout.REPO.resolve() in resolved.parents:
        raise SystemExit("--output-dir must be outside the git repository (customer data must not enter repo paths)")
    if resolved.exists():
        raise SystemExit("--output-dir already exists; choose a new directory (no overwrite)")
    return resolved


def interface_map(rows: Iterable[tuple[str | None, str | None]]) -> tuple[dict[str, str], dict[str, int]]:
    """거래 ID → 인터페이스 ID. 비어 있지 않은 값이 정확히 1개일 때만 정하고 그 밖은 세기만 한다."""
    seen: dict[str, set[str]] = {}
    for transaction, interface in rows:
        if source_absent(transaction):
            continue
        values = seen.setdefault(transaction, set())
        if not source_absent(interface):
            values.add(interface)
    mapping = {tx: next(iter(v)) for tx, v in seen.items() if len(v) == 1}
    return mapping, {"transactions_with_T_row": len(seen), "mapped": len(mapping),
                     "ambiguous": sum(len(v) > 1 for v in seen.values()), "no_interface": sum(not v for v in seen.values())}


def _case_counts(split: Mapping[str, Any], facts: Mapping[str, Any]) -> dict[str, Any]:
    cut = split["counts"]["cut"]
    return {**cut, "reference_valid": facts["reference_valid"], "current_valid": facts["current_valid"],
            "reference_constant": facts["constant"]}


def build_export(output: Path, observations: Sequence[tuple[Any, Mapping[str, Any], Any]], *,
                 source_tz: str | None, timezone_basis: str, snapshot_id: str, per_interface_top: int = 0,
                 interface_of: Callable[[Sequence[str]], tuple[dict[str, str], dict[str, int]]] | None = None,
                 source_window: tuple[dt.datetime | None, dt.datetime | None] = (None, None),
                 avoid_held_window: bool = False, log: Callable[[str], None] = print) -> dict[str, Any]:
    """DB 없이 시험할 수 있는 export 본체. observations = (관측 ID, allowed_values, label_evidence).

    source_window: 행 원천 시각 창 [from, until)(인수). avoid_held_window: 전역 기간이 번들 보류 창과 겹치면
    보류 창 뒤쪽(after, ≥ 2026-10-03T00:00Z)만 남기고, 뒤쪽으로 분할이 안 되면 앞쪽(before, < 2026-09-29T15:00Z)만
    남긴다(고정 순서, capture_layout.held_window_sides).
    """
    output.mkdir(parents=True, exist_ok=False)
    index: dict[str, Any] = {
        "format": FORMAT, "kind": "real", "generator": "export_server_captures.py",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "snapshot_id": snapshot_id, "split_source": "train", "source_timezone_basis": timezone_basis,
        "period_format": "YYYY-MM-DDTHH:MM:SS.ffffff+00:00 (UTC, fixed length)", "reference_ratio": 0.8,
        "exclusion_rule": layout.EXCLUSION_RULE, "minimum_n": 20, "fields": list(layout.SERVICE_FIELDS),
        "input_assumptions": list(INPUT_ASSUMPTIONS), "counts": {}, "cases": [], "skipped": []}
    observations = list(observations)
    window = source_window
    split = layout.split_observations(observations, source_tz, window=window)
    held = {"avoid_requested": avoid_held_window, "overlap_before_rule": _overlaps(split), "side": None,
            "avoid_failed": False, "dropped_rows": 0, "dropped_held_transactions": 0, "dropped_held_rows": 0,
            "dropped_outside_side_rows": 0}
    drop_held = False
    if held["overlap_before_rule"] and avoid_held_window:
        base_outside = split["counts"]["grouping"]["outside_source_window"]
        for side, bounds in layout.held_window_sides().items():
            candidate = _intersect(source_window, bounds)
            try:
                trial = layout.split_observations(observations, source_tz, window=candidate, drop_held_transactions=True)
            except ValueError:
                continue
            if not _overlaps(trial):  # 거래 제외·창 적용 뒤 기간·전체 시각 검사가 깨끗해야 채택
                window, split, held["side"], drop_held = candidate, trial, side, True
                c = trial["counts"]
                held["dropped_held_transactions"] = c["held_transactions_dropped"]
                held["dropped_held_rows"] = c["held_rows_dropped"]
                held["dropped_outside_side_rows"] = c["grouping"]["outside_source_window"] - base_outside
                held["dropped_rows"] = held["dropped_held_rows"] + held["dropped_outside_side_rows"]
                break
        held["avoid_failed"] = held["side"] is None
    index["source_window"] = {"from": _iso(window[0]), "until": _iso(window[1]),
                              "argument_from": _iso(source_window[0]), "argument_until": _iso(source_window[1])}
    index["held_window"] = held
    index["counts"]["global"] = split["counts"]
    scopes = [("global", None, observations, split)]
    private: dict[str, Any] = {}
    if per_interface_top > 0:
        if interface_of is None:
            raise ValueError("interface lookup required")
        eligible = [o for o in observations if layout.eligible(o[1], o[2])]
        transactions = sorted({o[1].get("transaction_id") for o in eligible
                               if isinstance(o[1].get("transaction_id"), str) and not source_absent(o[1]["transaction_id"])})
        mapping, map_counts = interface_of(transactions)
        index["counts"]["interface_lookup"] = map_counts
        ranked = Counter(mapping[o[1].get("transaction_id")] for o in eligible if o[1].get("transaction_id") in mapping)
        for rank, (interface, _) in enumerate(sorted(ranked.items(), key=lambda kv: (-kv[1], kv[0]))[:per_interface_top], 1):
            subset = [o for o in observations if mapping.get(o[1].get("transaction_id")) == interface]
            try:
                scopes.append(("interface", rank, subset, layout.split_observations(subset, source_tz, window=window,
                                                                               drop_held_transactions=drop_held)))
            except ValueError as exc:
                index["skipped"].append({"scope": "interface", "interface_rank": rank, "reason": str(exc)})
            private[str(rank)] = interface
    number = 0
    for scope, rank, _, part in scopes:
        for field in layout.SERVICE_FIELDS:
            number += 1
            case_id = f"real-{number:03d}"
            files = layout.write_case(
                output / case_id, field=field, reference_rows=part["reference"]["rows"],
                current_rows=part["current"]["rows"], reference_period=part["reference_period"],
                current_period=part["current_period"], data_class="approved_real", unit_verified=True,
                confirmation="document_confirmed", authority=AUTHORITY,
                identity={"dataset_id": f"snapshot_{snapshot_id}_{case_id}", "case": case_id,
                          "origin": "server DB snapshot train members, source-time split",
                          "scope": scope, "interface_rank": rank},
                unknown=UNKNOWN)
            facts = layout.facts_from_case(output / case_id, field)
            index["cases"].append({
                "case_id": case_id, "field": field, "scope": scope, "interface_rank": rank, "kind": "real",
                "path": case_id, "files": files,
                "held_window_overlap": layout.split_held_overlap(part),
                "counts": _case_counts(part, facts), "expected": None})
            log(f"{case_id} {field} scope={scope}{'' if rank is None else ' rank=' + str(rank)} "
                f"ref_rows={part['counts']['cut']['reference_rows']} cur_rows={part['counts']['cut']['current_rows']} "
                f"ref_valid={facts['reference_valid']} cur_valid={facts['current_valid']} "
                f"expected={facts['expected']['states']}")
    if private:
        layout.write_json(output / "private" / "interface_map.json",
                          {"format": FORMAT, "note": "Server-only. Never copy into the repository or summaries.",
                           "interface_rank_to_interface_id": private,
                           "case_ids_by_rank": {str(c["interface_rank"]): [x["case_id"] for x in index["cases"]
                                                                          if x["interface_rank"] == c["interface_rank"]]
                                                for c in index["cases"] if c["interface_rank"] is not None}})
    layout.write_json(output / "index.json", index)
    return index


def fetch(conn: Any, snapshot_id: uuid.UUID, per_interface_top: int):
    """READ ONLY 트랜잭션 하나에서 필요한 것만 읽는다."""
    conn.read_only = True
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SHOW transaction_read_only")
            if cur.fetchone()[0] != "on":
                raise SystemExit("transaction is not read-only; refusing to continue")
            cur.execute(SNAPSHOT_SQL, (snapshot_id,))
            row = cur.fetchone()
            if row is None or row[1] != "ready":
                raise SystemExit("snapshot not found or not ready")
            source_id = row[0]
            cur.execute(CLOCK_SQL, (source_id,))
            clock = cur.fetchone()
            tz, basis = source_timezone(clock[0] if clock else None)
            cur.execute(ROWS_SQL, (snapshot_id,))
            observations = [(str(a), b if isinstance(b, dict) else {}, c) for a, b, c in cur.fetchall()]
            lookup = None
            if per_interface_top > 0:
                cur.execute(INTERFACE_SQL, (source_id, [o[1].get("transaction_id") for o in observations
                                                        if isinstance(o[1].get("transaction_id"), str)]))
                pairs = cur.fetchall()
                lookup = lambda _transactions: interface_map(pairs)  # noqa: E731
    return observations, tz, basis, lookup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot-id", type=uuid.UUID, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--per-interface-top", type=int, default=0, help="행 수 상위 N개 인터페이스별 사례(기본 0=끔)")
    parser.add_argument("--source-time-from", help="행 원천 시각 하한(포함), 오프셋 필수 ISO 8601")
    parser.add_argument("--source-time-until", help="행 원천 시각 상한(제외), 오프셋 필수 ISO 8601")
    parser.add_argument("--avoid-held-window", action="store_true",
                        help="기간이 보류 창(KST 2026-09-30~10-02, 보수적 판정)과 겹치면 뒤쪽(없으면 앞쪽)만 남긴다")
    args = parser.parse_args(argv)
    window = (parse_bound(args.source_time_from), parse_bound(args.source_time_until))
    if window[0] is not None and window[1] is not None and not window[0] < window[1]:
        raise SystemExit("--source-time-from must be earlier than --source-time-until")
    if not 0 <= args.per_interface_top <= 20:
        raise SystemExit("--per-interface-top must be 0..20")
    output = check_output_dir(args.output_dir)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    import psycopg
    with psycopg.connect(url) as conn:
        observations, tz, basis, lookup = fetch(conn, args.snapshot_id, args.per_interface_top)
    print(f"train PROCESS members: {len(observations)}; timezone basis: {basis}")
    index = build_export(output, observations, source_tz=tz, timezone_basis=basis, snapshot_id=str(args.snapshot_id),
                         per_interface_top=args.per_interface_top, interface_of=lookup, source_window=window,
                         avoid_held_window=args.avoid_held_window)
    held = sum(c["held_window_overlap"] for c in index["cases"])
    g = index["counts"]["global"]["grouping"]
    print(f"cases: {len(index['cases'])}; skipped: {len(index['skipped'])}; held-window overlaps: {held}; "
          f"outside source window rows: {g['outside_source_window']}; held-window side kept: {index['held_window']['side']}; "
          f"rows dropped by held-window rule: {index['held_window']['dropped_rows']}")
    if held:
        print("held-window overlap: these cases will not run. Re-run export into a new --output-dir with --avoid-held-window.")
    print(f"index: {output / 'index.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

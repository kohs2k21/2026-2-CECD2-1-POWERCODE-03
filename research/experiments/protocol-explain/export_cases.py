"""실데이터 사례 export(서버 전용). DB를 READ ONLY 트랜잭션으로 읽어 사례 폴더 `protocol-explain-export-v1`을 만든다.

    DATABASE_URL=... python -X utf8 export_cases.py --output-dir /exports/protocol-explain-<날짜> [--source job]
    DATABASE_URL=... python -X utf8 export_cases.py --output-dir ... --source snapshot --snapshot-id <uuid>

- 경로 집계는 하지 않는다. 원천은 코드 산출물 그대로다.
  - `--source job`(기본): 가장 최근 성공한 Trainer `recommend_relations` 작업 결과의 `protocolPathDrafts`
    (프로토콜 설정 화면 초안과 같은 원천·같은 선택 규칙 — protocol-settings-ui.md 2절, backend ruleCandidates.ts)
  - `--source snapshot`: 스냅샷 train 행을 Trainer 코드(`trainer/relation_candidates._db_bundles` →
    `trainer/protocol_paths.ProtocolPathDrafts`)로 그 자리에서 집계(작업 결과가 없을 때)
  - 허용 경로: 현재 룰 세트(`ops_rule_set_revisions` 최대 리비전) `protocolRules[]`. 알림: 최근 판정의 `evidence.protocolReview`
    (protocol-rules.md 5.3)와 그 판정 룰 세트 리비전의 항목
- DB는 REPEATABLE READ, READ ONLY 트랜잭션 하나에서 읽고(`transaction_read_only=on` 확인) 끝에 ROLLBACK 한다
- 화면 출력은 개수·상태 코드만. 실제 ID·건수·DB URL을 출력하지 않는다. 출력 폴더는 저장소 밖이어야 하고 서버 밖으로 옮기지 않는다
  (`source.json`·`private/alias_map.json`에 실제 ID가 있다. `facts.json`은 별칭이지만 건수·기간이 있다)
"""
from __future__ import annotations

import argparse
import os
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

import kit

JOB_SQL = """SELECT id::text, result_summary->'protocolPathDrafts', result_summary->'period'
               FROM run_jobs WHERE kind::text = 'recommend_relations' AND status = 'succeeded'
              ORDER BY finished_at DESC NULLS LAST, created_at DESC, id DESC LIMIT 1"""
RULE_SET_SQL = """SELECT revision, snapshot->'protocolRules' FROM ops_rule_set_revisions
                   ORDER BY revision DESC LIMIT 1"""
RULE_REVISION_SQL = "SELECT snapshot->'protocolRules' FROM ops_rule_set_revisions WHERE revision = %s"
# 알림 화면과 같은 판별(notificationReadModel.ts protocolCte): version 1, kind 2종, interfaceId 비지 않은 문자열.
# 범위는 event_sequence(유일 색인) 최근 창으로 제한한다.
ALERT_SQL = """SELECT d.id::text, d.evidence->'protocolReview'
                 FROM run_process_decisions AS d
                WHERE d.event_sequence > (SELECT coalesce(max(event_sequence), 0) FROM run_process_decisions) - %(window)s
                  AND jsonb_typeof(d.evidence->'protocolReview') = 'object'
                  AND d.evidence->'protocolReview'->'version' = '1'::jsonb
                  AND d.evidence->'protocolReview'->>'kind' IN ('path_unmatched', 'interface_unregistered')
                  AND jsonb_typeof(d.evidence->'protocolReview'->'interfaceId') = 'string'
                  AND d.evidence->'protocolReview'->>'interfaceId' <> ''
                ORDER BY d.event_sequence DESC"""


def check_output_dir(path: Path) -> Path:
    path = path.resolve()
    if kit.inside(path, kit.REPO):
        raise SystemExit("output-dir must be outside the git repository")
    if path.exists():
        raise SystemExit("output-dir already exists (no overwrite)")
    return path


def _rules_by_interface(items: Any) -> dict[str, dict[str, Any]]:
    """스냅샷 protocolRules[](없거나 v2 스냅샷이면 빈 목록) → interfaceId별 항목."""
    if not isinstance(items, list):
        return {}
    return {item["interfaceId"]: item for item in items
            if isinstance(item, Mapping) and isinstance(item.get("interfaceId"), str)}


def select_interfaces(drafts: Mapping[str, Any], rules: Mapping[str, Any], *, top: int, small: int,
                      unobserved: int) -> list[tuple[str, str]]:
    """(interfaceId, 선택 이유). 결정적: 경로 거래 수 내림차순 top개 → 나머지 중 적은 순 small개(근거 부족 먼저)
    → 등록됐지만 학습 기간에 관측되지 않은 인터페이스 unobserved개(interfaceId 코드 포인트 순)."""
    items = list(drafts.get("interfaces") or [])
    ranked = sorted(items, key=lambda item: (-item["pathTransactions"], item["interfaceId"]))
    chosen = [(item["interfaceId"], "top") for item in ranked[:top]]
    picked = {interface for interface, _ in chosen}
    rest = [item for item in ranked[top:] if item["interfaceId"] not in picked]
    rest.sort(key=lambda item: (item["status"] != "insufficient_evidence", item["pathTransactions"], item["interfaceId"]))
    chosen += [(item["interfaceId"], "small") for item in rest[:small]]
    observed = {item["interfaceId"] for item in items}
    missing = sorted(interface for interface in rules if interface not in observed)
    chosen += [(interface, "registered_not_observed") for interface in missing[:unobserved]]
    return chosen


def select_alerts(rows: Sequence[tuple[str, Any]], limit: int) -> list[tuple[str, dict[str, Any]]]:
    """최근 순 판정에서 묶음(인터페이스, 종류, 첫 불일치 pathKey)마다 1건씩 limit개."""
    seen, out = set(), []
    for decision_id, review in rows:
        if not isinstance(review, Mapping):
            continue
        unmatched = [route.get("pathKey") for route in review.get("routes") or []
                     if isinstance(route, Mapping) and route.get("matched") is False]
        key = (review.get("interfaceId"), review.get("kind"), unmatched[0] if unmatched else None)
        if key in seen:
            continue
        seen.add(key)
        out.append((decision_id, dict(review)))
        if len(out) >= limit:
            break
    return out


def build_export(output: Path, fetched: Mapping[str, Any], *, top: int = 6, small: int = 2, unobserved: int = 1,
                 alerts: int = 4, alias: bool = True, created_at: str | None = None) -> dict[str, Any]:
    """읽은 값(fetch 결과) → 사례 폴더. DB 없이 시험할 수 있게 분리했다."""
    drafts = fetched["drafts"]
    if not isinstance(drafts, Mapping) or drafts.get("version") != 1:
        raise SystemExit("STOP: protocolPathDrafts missing or version unsupported")
    rules = _rules_by_interface(fetched.get("protocol_rules"))
    by_interface = {item["interfaceId"]: item for item in drafts.get("interfaces") or []}
    output.mkdir(parents=True, exist_ok=False)
    cases, skipped, aliases = [], [], {}
    number = 0
    for interface, reason in select_interfaces(drafts, rules, top=top, small=small, unobserved=unobserved):
        number += 1
        case_id = f"real-{number:03d}"
        source = {"kind": "observed_paths", "interfaceId": interface, "draftInterface": by_interface.get(interface),
                  "ruleEntry": rules.get(interface), "period": fetched.get("period"),
                  "parameters": drafts.get("parameters")}
        try:
            entry, mapping = kit.write_case(output, case_id, source, alias, extra={"selection": reason})
        except ValueError as exc:
            skipped.append({"case_id": case_id, "kind": "observed_paths", "reason": str(exc)})
            continue
        cases.append(entry)
        aliases[case_id] = mapping
    for decision_id, review in select_alerts(fetched.get("alerts") or [], alerts):
        number += 1
        case_id = f"real-{number:03d}"
        revision_rules = _rules_by_interface((fetched.get("rule_revisions") or {}).get(review.get("ruleSetRevision")))
        source = {"kind": "alert", "decisionId": decision_id, "review": review,
                  "ruleEntry": revision_rules.get(review.get("interfaceId"))}
        try:
            entry, mapping = kit.write_case(output, case_id, source, alias, extra={"selection": "recent_alert"})
        except ValueError as exc:
            skipped.append({"case_id": case_id, "kind": "alert", "reason": str(exc)})
            continue
        cases.append(entry)
        aliases[case_id] = mapping
    kit.write_json(output / "private" / "alias_map.json", aliases)
    index = {"format": kit.FORMAT, "kind": "real", "generator": "export_cases.py", "created_at": created_at or kit.now(),
             "alias": alias, "facts_format": kit.pe.FACTS_FORMAT, "source": fetched.get("source_info", {}),
             "selection": {"top": top, "small": small, "unobserved": unobserved, "alerts": alerts},
             "counts": {"cases": len(cases), "observed_paths": sum(c["kind"] == "observed_paths" for c in cases),
                        "alert": sum(c["kind"] == "alert" for c in cases), "skipped": len(skipped),
                        "draft_interfaces": len(by_interface), "registered_interfaces": len(rules),
                        "alerts_found": len(fetched.get("alerts") or [])},
             "cases": cases, "skipped": skipped}
    kit.write_json(output / "index.json", index)
    return index


def fetch(conn: Any, *, source: str, snapshot_id: uuid.UUID | None, alert_window: int) -> dict[str, Any]:
    """REPEATABLE READ, READ ONLY 트랜잭션 하나에서 필요한 것만 읽는다(호출자가 ROLLBACK)."""
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    if conn.execute("SHOW transaction_read_only").fetchone()[0] != "on":
        raise SystemExit("STOP: transaction is not read only")
    if source == "job":
        row = conn.execute(JOB_SQL).fetchone()
        if row is None:
            raise SystemExit("STOP: no succeeded recommend_relations job")
        job_id, drafts, period = row
        if drafts is None:
            raise SystemExit("STOP: latest succeeded job has no protocolPathDrafts")
        info = {"mode": "job", "job_id": job_id}
    else:
        from trainer.protocol_paths import ProtocolPathDrafts
        from trainer.relation_candidates import _db_bundles
        snap = conn.execute("""SELECT source_id, status::text, schema_version, observation_cutoff_at, selection_spec
                                 FROM data_snapshots WHERE id = %s""", (snapshot_id,)).fetchone()
        if snap is None or snap[1] != "ready" or snap[2] != "process-v1":
            raise SystemExit("STOP: snapshot not ready")
        source_id, _, _, cutoff, selection = snap
        accumulator = ProtocolPathDrafts()
        # Trainer recommend_relations와 같은 입력(실행마다 최신 리비전, train 분할, 거래 키 순 서버 커서).
        for bundle in _db_bundles(conn, {"snapshot": snapshot_id, "source": source_id, "cutoff": cutoff}, 1):
            accumulator.add(bundle)
        drafts = accumulator.result()
        selection = selection or {}
        period = {"from": selection.get("from"), "until": selection.get("until"), "timeBasis": selection.get("timeBasis")}
        info = {"mode": "snapshot", "snapshot_id": str(snapshot_id)}
    revision_row = conn.execute(RULE_SET_SQL).fetchone()
    revision, protocol_rules = (revision_row if revision_row is not None else (None, None))
    info["rule_set_revision"] = revision
    alerts = [(row[0], row[1]) for row in conn.execute(ALERT_SQL, {"window": alert_window}).fetchall()] \
        if alert_window > 0 else []
    revisions: dict[Any, Any] = {}
    for _, review in alerts:
        number = review.get("ruleSetRevision") if isinstance(review, Mapping) else None
        if isinstance(number, int) and not isinstance(number, bool) and number not in revisions:
            found = conn.execute(RULE_REVISION_SQL, (number,)).fetchone()
            revisions[number] = found[0] if found is not None else None
    return {"drafts": drafts, "period": period, "protocol_rules": protocol_rules, "alerts": alerts,
            "rule_revisions": revisions, "source_info": info}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source", choices=["job", "snapshot"], default="job")
    parser.add_argument("--snapshot-id", type=uuid.UUID)
    parser.add_argument("--top", type=int, default=6, help="경로 거래 수 상위 인터페이스 수")
    parser.add_argument("--small", type=int, default=2, help="나머지 중 적은 인터페이스 수(근거 부족 먼저)")
    parser.add_argument("--unobserved", type=int, default=1, help="등록됐지만 관측 없는 인터페이스 수")
    parser.add_argument("--alerts", type=int, default=4, help="최근 알림 묶음 수(0이면 읽지 않음)")
    parser.add_argument("--alert-window", type=int, default=5000, help="알림을 찾는 최근 판정 수")
    parser.add_argument("--keep-real-ids", action="store_true", help="별칭 없이 실제 ID를 사실에 넣는다(기본 별칭)")
    args = parser.parse_args(argv)
    if args.source == "snapshot" and args.snapshot_id is None:
        raise SystemExit("--snapshot-id is required with --source snapshot")
    for name in ("top", "small", "unobserved", "alerts", "alert_window"):
        if getattr(args, name) < 0:
            raise SystemExit(f"--{name.replace('_', '-')} must be >= 0")
    output = check_output_dir(args.output_dir)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is required")
    import psycopg
    with psycopg.connect(url) as conn:
        try:
            fetched = fetch(conn, source=args.source, snapshot_id=args.snapshot_id,
                            alert_window=args.alert_window if args.alerts else 0)
        finally:
            conn.rollback()
    index = build_export(output, fetched, top=args.top, small=args.small, unobserved=args.unobserved,
                         alerts=args.alerts, alias=not args.keep_real_ids)
    counts = index["counts"]
    print(f"export done: cases={counts['cases']} observed_paths={counts['observed_paths']} alert={counts['alert']} "
          f"skipped={counts['skipped']} draft_interfaces={counts['draft_interfaces']} "
          f"registered_interfaces={counts['registered_interfaces']} alerts_found={counts['alerts_found']}")
    for item in index["skipped"]:
        print(f"skipped {item['case_id']} {item['kind']} {item['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

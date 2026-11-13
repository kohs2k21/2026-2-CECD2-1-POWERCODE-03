"""합성 사례 `cases/` 생성(고정 값, 무작위 없음). 이미 있는 폴더에는 쓰지 않는다.

관측 경로 사례는 합성 PROCESS·TRANSACTION 행을 Trainer 초안 집계(`trainer/protocol_paths.py` ProtocolPathDrafts)에
그대로 넣어 만든다 — 실데이터 export와 같은 코드 경로·같은 결과 형식. 알림 사례는 protocol-rules.md 5.3 형식으로 직접 쓴다.
채널·인터페이스 이름은 모두 합성(SYN_*)이며 사실에서는 별칭(IF-01, CH-01 …)으로 바뀐다.

    python -X utf8 make_cases.py --output cases
"""
from __future__ import annotations

import argparse
import itertools
from pathlib import Path
from typing import Any

import kit
from trainer.protocol_paths import ProtocolPathDrafts  # noqa: E402  (kit가 server/worker를 경로에 넣음)
from trainer.relation_candidates import ProcessRow, TransactionRow, TxBundle  # noqa: E402

PERIOD = {"from": "2026-01-05T00:00:00.000Z", "until": "2026-01-19T00:00:00.000Z", "timeBasis": "source_time"}
GENERATED_AT = "2026-11-14T00:00:00+00:00"
_ids = itertools.count(1)


def _process(tx: str, pid: str, depend: str | None, channel: str | None) -> ProcessRow:
    n = next(_ids)
    return ProcessRow(f"obs-{n:06d}", f"exe-{n:06d}", 1, None, tx, pid, depend, "DB", 0, {"status": "S"}, None,
                      "train", channel)


def _transaction(tx: str, interface: str, start: str | None, end: str | None) -> TransactionRow:
    n = next(_ids)
    return TransactionRow(f"obs-{n:06d}", f"exe-{n:06d}", 1, None, tx, interface, "S", 0, start, end)


def chain(tx: str, interface: str, channels: list[str]) -> TxBundle:
    rows, previous = [], "NONE"
    for index, channel in enumerate(channels, 1):
        pid = f"{interface}_PR{index:03d}"
        rows.append(_process(tx, pid, previous, channel))
        previous = pid
    return TxBundle(tx, rows, [_transaction(tx, interface, channels[0], channels[-1])], [])


def branching(tx: str, interface: str, start: str, left: list[str], right: list[str]) -> TxBundle:
    """시작 단계 하나에서 두 갈래(끝 단계 2개 → 경로 2개)."""
    rows = [_process(tx, f"{interface}_PR001", "NONE", start)]
    for side, channels in (("A", left), ("B", right)):
        previous = f"{interface}_PR001"
        for index, channel in enumerate(channels, 1):
            pid = f"{interface}_PR{side}{index:02d}"
            rows.append(_process(tx, pid, previous, channel))
            previous = pid
    return TxBundle(tx, rows, [_transaction(tx, interface, start, None)], [])


def broken(tx: str, interface: str, reason: str) -> TxBundle:
    """경로를 만들 수 없는 거래(사유별)."""
    if reason == "chain_broken":
        rows = [_process(tx, f"{interface}_PR001", "NONE", "SYN_GW"),
                _process(tx, f"{interface}_PR002", f"{interface}_PR009", "SYN_ESB")]
    elif reason == "channel_absent":
        rows = [_process(tx, f"{interface}_PR001", "NONE", "SYN_GW"),
                _process(tx, f"{interface}_PR002", f"{interface}_PR001", None)]
    elif reason == "depend_process_id_missing":
        rows = [_process(tx, f"{interface}_PR001", "NONE", "SYN_GW"),
                _process(tx, f"{interface}_PR002", "", "SYN_ESB")]
    else:
        raise ValueError(reason)
    return TxBundle(tx, rows, [_transaction(tx, interface, "SYN_GW", "SYN_ESB")], [])


def rule(interface: str, paths: list[list[str]], *, enabled: bool = True) -> dict[str, Any]:
    """룰 세트 v3 protocolRules[] 항목(protocol-rules.md 4절 형식, id는 합성 고정값)."""
    digest = kit.sha256_bytes(interface.encode())[:8]
    return {"id": f"hard:proto-{digest}", "interfaceId": interface,
            "paths": [{"channels": list(path), "source": {"kind": "draft", "jobId": "synthetic"}} for path in paths],
            "severity": "critical", "enabled": enabled,
            "source": {"jobId": "synthetic", "snapshotId": "synthetic", "period": PERIOD}}


GW, ESB, ERP, CRM, MDM = "SYN_GW", "SYN_ESB", "SYN_ERP", "SYN_CRM", "SYN_MDM"


def bundles() -> list[TxBundle]:
    out: list[TxBundle] = []
    counter = itertools.count(1)

    def tx(interface: str) -> str:
        return f"TX-{interface}-{next(counter):05d}"

    out += [chain(tx("IF_SYN_A"), "IF_SYN_A", [GW, ESB, ERP]) for _ in range(120)]
    out += [chain(tx("IF_SYN_B"), "IF_SYN_B", [GW, ESB, ERP]) for _ in range(80)]
    out += [chain(tx("IF_SYN_B"), "IF_SYN_B", [GW, ESB, CRM]) for _ in range(30)]
    out += [branching(tx("IF_SYN_B"), "IF_SYN_B", GW, [ESB], [MDM]) for _ in range(10)]
    out += [chain(tx("IF_SYN_C"), "IF_SYN_C", [GW, ESB, ERP]) for _ in range(90)]
    out += [chain(tx("IF_SYN_C"), "IF_SYN_C", [GW, ERP]) for _ in range(6)]
    out += [chain(tx("IF_SYN_D"), "IF_SYN_D", [GW, ESB, CRM]) for _ in range(40)]
    out += [chain(tx("IF_SYN_D"), "IF_SYN_D", [GW, CRM]) for _ in range(5)]
    out += [chain(tx("IF_SYN_E"), "IF_SYN_E", [GW, ESB, ERP]) for _ in range(250)]
    out += [chain(tx("IF_SYN_E"), "IF_SYN_E", [GW, ESB, ESB, ERP]) for _ in range(2)]  # 연속 같은 채널은 합치지 않음
    out += [broken(tx("IF_SYN_E"), "IF_SYN_E", "chain_broken") for _ in range(4)]
    out += [broken(tx("IF_SYN_E"), "IF_SYN_E", "channel_absent") for _ in range(3)]
    out += [chain(tx("IF_SYN_F"), "IF_SYN_F", [GW, ESB]) for _ in range(3)]
    out += [broken(tx("IF_SYN_F"), "IF_SYN_F", "depend_process_id_missing") for _ in range(9)]
    out.sort(key=lambda bundle: bundle.key)  # Trainer 스트림과 같이 거래 키 순
    return out


RULES = {
    "IF_SYN_A": rule("IF_SYN_A", [[GW, ESB, ERP]]),
    "IF_SYN_B": rule("IF_SYN_B", [[GW, ESB, ERP], [GW, ESB, CRM], [GW, ESB], [GW, MDM]]),
    "IF_SYN_C": rule("IF_SYN_C", [[GW, ESB, ERP], [GW, ESB, MDM, ERP]]),
    "IF_SYN_E": rule("IF_SYN_E", [[GW, ESB, ERP]]),
    "IF_SYN_F": rule("IF_SYN_F", [[GW, ESB]]),
}

# (사례, 시나리오, 목적, 인터페이스)
OBSERVED = [
    ("syn-001", "single_path_normal", "정상 단일 경로: 관측 경로 1개가 허용 경로와 같음", "IF_SYN_A"),
    ("syn-002", "multiple_allowed", "허용 경로 여럿 + 갈라진 거래, 관측 경로가 모두 허용됨", "IF_SYN_B"),
    ("syn-003", "unseen_path", "처음 보는 경로(허용 경로에 없음) 1개 + 관측 목록에 없는 허용 경로 1개", "IF_SYN_C"),
    ("syn-004", "unregistered_interface", "처음 보는 인터페이스(정상 프로토콜 미등록), 관측 경로 2개", "IF_SYN_D"),
    ("syn-005", "rare_path", "드문 경로(1% 미만, 연속 같은 채널) 허용 경로에 없음 + 경로를 만들지 못한 거래 사유 2종", "IF_SYN_E"),
    ("syn-006", "minimal_facts", "사실이 거의 없음: 경로를 만든 거래 3건(근거 부족), 대부분 사유로 제외", "IF_SYN_F"),
]


def alert_sources() -> list[tuple[str, str, str, dict[str, Any]]]:
    unmatched = {"version": 1, "kind": "path_unmatched", "interfaceId": "IF_SYN_C",
                 "protocolRuleId": RULES["IF_SYN_C"]["id"], "ruleSetRevision": 7,
                 "transactionObservationId": "synthetic",
                 "routes": [{"channels": [GW, ESB, ERP], "pathKey": f"{GW}:{ESB}:{ERP}", "matched": True},
                            {"channels": [GW, ESB, CRM], "pathKey": f"{GW}:{ESB}:{CRM}", "matched": False}],
                 "routesReason": None,
                 "endpoints": {"start": GW, "end": ERP, "matchesRoutes": False, "ambiguous": False}}
    unregistered = {"version": 1, "kind": "interface_unregistered", "interfaceId": "IF_SYN_G",
                    "protocolRuleId": None, "ruleSetRevision": 7, "transactionObservationId": "synthetic",
                    "routes": [], "routesReason": "chain_broken",
                    "endpoints": {"start": GW, "end": CRM, "matchesRoutes": None, "ambiguous": False}}
    return [
        ("syn-007", "alert_path_unmatched", "알림: 갈라진 거래의 경로 2개 중 1개가 허용 경로에 없음",
         {"kind": "alert", "review": unmatched, "ruleEntry": RULES["IF_SYN_C"]}),
        ("syn-008", "alert_unregistered_no_routes", "알림: 등록되지 않은 인터페이스, 경로를 만들 수 없음(선행 사슬 끊김)",
         {"kind": "alert", "review": unregistered, "ruleEntry": None}),
    ]


def make(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    drafts = ProtocolPathDrafts()
    for bundle in bundles():
        drafts.add(bundle)
    result = drafts.result()
    by_interface = {item["interfaceId"]: item for item in result["interfaces"]}
    cases = []
    for case_id, scenario, purpose, interface in OBSERVED:
        source = {"kind": "observed_paths", "interfaceId": interface, "draftInterface": by_interface.get(interface),
                  "ruleEntry": RULES.get(interface), "period": PERIOD, "parameters": result["parameters"]}
        entry, _ = kit.write_case(output, case_id, source, alias=True, extra={"scenario": scenario, "purpose": purpose})
        cases.append(entry)
    for case_id, scenario, purpose, source in alert_sources():
        entry, _ = kit.write_case(output, case_id, source, alias=True, extra={"scenario": scenario, "purpose": purpose})
        cases.append(entry)
    index = {"format": kit.FORMAT, "kind": "synthetic", "generator": "make_cases.py", "created_at": GENERATED_AT,
             "alias": True, "facts_format": kit.pe.FACTS_FORMAT,
             "source": {"mode": "synthetic", "trainer_draft_version": result["version"]},
             "counts": {"cases": len(cases), "observed_paths": len(OBSERVED), "alert": len(cases) - len(OBSERVED)},
             "cases": cases, "skipped": []}
    kit.write_json(output / "index.json", index)
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    index = make(args.output)
    print(f"cases: {index['counts']['cases']} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

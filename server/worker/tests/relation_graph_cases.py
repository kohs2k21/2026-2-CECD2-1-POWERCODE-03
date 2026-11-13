"""층 A 그래프 TS·Python 일치 시험의 입력과 기대값 생성기.

- 입력(CASES)은 작은 가상 거래들이다(실데이터 값 아님). 기대값은 백엔드 TS 구현
  `server/backend/src/services/processGraph.ts` `buildInstanceGraph`가 낸 결과를 그대로 저장한 것이다.
- 다시 만들기(Node 22.6 이상, TS 형식만 지우는 실행): 이 디렉터리에서 `python relation_graph_cases.py --write`
  → `fixtures/relation-graph-cases.json`. TS 원본이 바뀌면 test_relation_graph가 sha256 차이로 알린다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
FIXTURE = HERE / "fixtures" / "relation-graph-cases.json"
TS_SOURCE = HERE.parents[1] / "backend" / "src" / "services" / "processGraph.ts"
GENERATED_AT = "2026-11-13T00:00:00.000Z"
SOURCE = "00000000-0000-4000-8000-0000000000aa"


def oid(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def proc(n: int, process_id: str | None, depends: str | None, *, tx: str | None = "TX-1", retry: str | None = None,
         focus: bool = False, status: str | None = "S", execution: int | None = None, fk: str | None = None,
         **extra) -> dict:
    # extra: 기본 행에 없는 열(예: channelId). 주지 않으면 키 자체가 없다(TS undefined → 없음).
    return {"observationId": oid(n), "executionId": oid(1000 + (execution if execution is not None else n)),
            "revisionNo": "1", "transactionId": tx, "transactionObservationId": fk, "processId": process_id,
            "dependsOn": depends, "status": status, "responseCode": "0000" if status == "S" else "E1",
            "totalCount": "1", "retryCount": retry, "focus": focus, "decisionId": None, "grade": None, **extra}


def msg(n: int, process_id: str | None, direction: str | None, message_id: str | None, *, tx: str | None = "TX-1",
        execution: bool = True, fk: str | None = None) -> dict:
    return {"observationId": oid(n), "executionId": oid(2000 + n) if execution else None, "revisionNo": "1",
            "processObservationId": fk, "transactionId": tx, "processId": process_id, "messageId": message_id,
            "direction": direction, "status": "S", "dataSize": "10", "messageIndex": "1"}


def txrow(n: int, transaction_id: str | None = "TX-1", **extra) -> dict:
    row = {"observationId": oid(n), "revisionNo": "1", "transactionId": transaction_id, "interfaceId": "IF_A",
           "channelId": "CH_A", "status": "S", "responseCode": "0000", "processTimeMs": "5", "processCount": "2",
           "retries": [{"retryCount": "0", "status": "S", "observationId": oid(n)}]}
    return {**row, **extra}


def case(name: str, *, processes, messages=(), transaction=None, processes_truncated=False, messages_truncated=False):
    return {"case": name, "input": {"sourceId": SOURCE, "transaction": transaction, "processes": list(processes),
                                    "messages": list(messages), "processesTruncated": processes_truncated,
                                    "messagesTruncated": messages_truncated, "generatedAt": GENERATED_AT}}


A, B, C = "IF_A_PR001", "IF_A_PR002", "IF_A_PR003"
CASES = [
    case("chain with handoff match and verified contains", transaction=txrow(90),
         processes=[proc(1, A, "NONE", fk=oid(90)), proc(2, B, A, focus=True, fk=oid(90))],
         messages=[msg(11, A, "OUT", "M1"), msg(12, B, "IN", "M1")]),
    case("handoff mismatch (one OUT one IN)", processes=[proc(1, A, " NONE "), proc(2, B, A, focus=True)],
         messages=[msg(11, A, "OUT", "M1"), msg(12, B, "IN", "M2")]),
    case("excluded transaction suffix", processes=[proc(1, A, "NONE", tx="TX-01"), proc(2, B, A, tx="TX-01", focus=True)],
         messages=[msg(11, A, "OUT", "M1", tx="TX-01"), msg(12, B, "IN", "M2", tx="TX-01")]),
    case("multiple messages without match", processes=[proc(1, A, "NONE"), proc(2, B, A, focus=True)],
         messages=[msg(11, A, "OUT", "M1"), msg(12, B, "IN", "M2"), msg(13, B, "IN", "M3")]),
    case("missing message id on OUT and on IN", processes=[proc(1, A, "NONE"), proc(2, B, A, focus=True),
                                                         proc(3, C, B)],
         messages=[msg(11, A, "OUT", " \\N "), msg(12, B, "IN", "M2"), msg(13, B, "OUT", "M3"),
                   msg(14, C, "IN", None)]),
    case("no IN and no OUT", processes=[proc(1, A, "NONE"), proc(2, B, A, focus=True), proc(3, C, B)],
         messages=[msg(11, A, "OUT", "M1")]),
    case("messages truncated", processes=[proc(1, A, "NONE"), proc(2, B, A, focus=True)],
         messages=[msg(11, A, "OUT", "M1"), msg(12, B, "IN", "M1")], messages_truncated=True),
    case("missing predecessor not logged", processes=[proc(2, B, A, focus=True)]),
    case("missing predecessor not loaded", processes=[proc(2, B, A, focus=True)], processes_truncated=True),
    case("retry bundle (distinct retry numbers) is not ambiguous",
         processes=[proc(1, A, "NONE", retry="0"), proc(3, A, "NONE", retry="1", status="F"),
                    proc(2, B, A, focus=True, retry="0")],
         messages=[msg(11, A, "OUT", "M1"), msg(12, B, "IN", "M1")]),
    case("ambiguous bundle (same retry number)",
         processes=[proc(1, A, "NONE", retry="0"), proc(3, A, "NONE", retry="0"), proc(2, B, A, focus=True)]),
    case("ambiguous bundle (retry number missing among several)",
         processes=[proc(1, A, "NONE", retry="0"), proc(3, A, "NONE", retry=None), proc(2, B, A, focus=True)]),
    case("successor attempts disagree on dependency",
         processes=[proc(1, A, "NONE"), proc(4, C, "NONE"), proc(2, B, A, retry="0", focus=True),
                    proc(3, B, C, retry="1")]),
    case("successor attempts start vs value (missing predecessor ambiguous)",
         processes=[proc(2, B, "NONE", retry="0", focus=True), proc(3, B, "IF_A_PR009", retry="1")]),
    case("self dependency", processes=[proc(1, A, A, focus=True)]),
    case("branch", processes=[proc(1, A, "NONE"), proc(2, B, A, focus=True), proc(3, C, A)]),
    case("lowercase none is a value", processes=[proc(2, B, "none", focus=True)]),
    case("absent dependency values are start", processes=[proc(1, A, None), proc(2, B, "", focus=True),
                                                         proc(3, C, "\t\\N\r\n")]),
    case("unkeyed process rows (absent ids)", processes=[proc(1, None, "NONE", focus=True), proc(2, B, A, tx=" "),
                                                        proc(3, A, "NONE")],
         messages=[msg(11, None, "OUT", "M1", tx=None), msg(12, A, "OUT", "M1", execution=False)]),
    case("message FK verified and message for unknown process", processes=[proc(1, A, "NONE", focus=True)],
         messages=[msg(11, "OTHER", "OUT", "M1", fk=oid(1)), msg(12, "IF_A_PR404", "IN", "M2")]),
    case("ambiguous transaction representative",
         transaction=txrow(90, observationId=None, revisionNo=None, interfaceId=None, channelId=None, status=None,
                           responseCode=None, processTimeMs=None, processCount=None, ambiguous=True,
                           retries=[{"retryCount": "1", "status": "F", "observationId": oid(92)},
                                    {"retryCount": "1", "status": "S", "observationId": oid(91)}]),
         processes=[proc(1, A, "NONE", focus=True, fk=oid(91))]),
    # Python `\d`·`$`와 JS `\d`·`$` 차이(유니코드 숫자, 끝 줄바꿈): 둘 다 제외 구간이 아니라 mismatch.
    case("suffix rule: trailing newline is not excluded",
         processes=[proc(1, A, "NONE", tx="TX-01\n"), proc(2, B, A, tx="TX-01\n", focus=True)],
         messages=[msg(11, A, "OUT", "M1", tx="TX-01\n"), msg(12, B, "IN", "M2", tx="TX-01\n")]),
    case("suffix rule: non-ASCII digit is not excluded",
         processes=[proc(1, A, "NONE", tx="TX-0\u0663"), proc(2, B, A, tx="TX-0\u0663", focus=True)],
         messages=[msg(11, A, "OUT", "M1", tx="TX-0\u0663"), msg(12, B, "IN", "M2", tx="TX-0\u0663")]),
    case("ids with colon percent and verbatim spaces",
         processes=[proc(1, "A:1", "NONE", tx="T%1:x"), proc(2, " B ", "A:1", tx="T%1:x", focus=True)],
         messages=[msg(11, "A:1", "OUT", "M:1", tx="T%1:x"), msg(12, " B ", "IN", "M:1", tx="T%1:x")]),
    # 단계 노드 channelId: 시도 원래 값이 모두 같고 값이 있을 때만 그 값(protocol-rules.md 3.2 6단계와 같은 기준).
    case("channel same across retry attempts",
         processes=[proc(1, A, "NONE", retry="0", channelId="CH_A"), proc(3, A, "NONE", retry="1", channelId="CH_A"),
                    proc(2, B, A, focus=True, channelId=" CH_B ")]),
    case("channel mixed across attempts",
         processes=[proc(1, A, "NONE", retry="0", channelId="CH_A"), proc(3, A, "NONE", retry="1", channelId="CH_B"),
                    proc(2, B, A, focus=True, retry="0", channelId="CH_A"), proc(4, B, A, retry="1", channelId=None)]),
    case("channel absent values are one absent",
         processes=[proc(1, A, "NONE", retry="0", channelId=None), proc(3, A, "NONE", retry="1", channelId=" \\N\t"),
                    proc(4, A, "NONE", retry="2", channelId=""), proc(2, B, A, focus=True, channelId="\r\n")]),
    case("channel whitespace variants are different values",
         processes=[proc(1, A, "NONE", retry="0", channelId="CH_A"), proc(3, A, "NONE", retry="1", channelId="CH_A "),
                    proc(2, B, A, focus=True, retry="0", channelId="\tCH_A"),
                    proc(4, B, A, retry="1", channelId="\tCH_A")]),
    case("channel on ambiguous bundle is null",
         processes=[proc(1, A, "NONE", retry="0", channelId="CH_A"), proc(3, A, "NONE", retry="0", channelId="CH_A"),
                    proc(2, B, A, focus=True, channelId="CH_A")]),
]

PICK_CASES = [
    {"case": "none", "input": {"executions": 0, "missingExecutionId": 0, "missingRetryCount": 0, "top": []}},
    {"case": "single execution even without retry number",
     "input": {"executions": 1, "missingExecutionId": 1, "missingRetryCount": 1,
               "top": [{"observationId": oid(1), "retryCount": None}]}},
    {"case": "largest retry wins", "input": {"executions": 3, "missingExecutionId": 0, "missingRetryCount": 0,
                                             "top": [{"observationId": oid(3), "retryCount": "2"},
                                                     {"observationId": oid(2), "retryCount": "1"}]}},
    {"case": "tie on largest", "input": {"executions": 2, "missingExecutionId": 0, "missingRetryCount": 0,
                                         "top": [{"observationId": oid(3), "retryCount": "2"},
                                                 {"observationId": oid(2), "retryCount": "2.0"}]}},
    {"case": "missing retry number", "input": {"executions": 2, "missingExecutionId": 0, "missingRetryCount": 1,
                                               "top": [{"observationId": oid(3), "retryCount": None},
                                                       {"observationId": oid(2), "retryCount": "1"}]}},
    {"case": "missing execution id", "input": {"executions": 2, "missingExecutionId": 1, "missingRetryCount": 0,
                                               "top": [{"observationId": oid(3), "retryCount": "2"},
                                                       {"observationId": oid(2), "retryCount": "1"}]}},
]

_NODE = """
import { readFileSync, writeFileSync } from 'node:fs';
const { buildInstanceGraph, pickTransactionRow } = await import(process.argv[2]);
const data = JSON.parse(readFileSync(process.argv[3], 'utf8'));
for (const item of data.cases) item.expected = buildInstanceGraph(item.input);
for (const item of data.pickCases) item.expected = pickTransactionRow(item.input);
writeFileSync(process.argv[3], JSON.stringify(data));
"""


def ts_sha256() -> str:
    return hashlib.sha256(TS_SOURCE.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def generate() -> dict:
    data = {"cases": CASES, "pickCases": PICK_CASES}
    with tempfile.TemporaryDirectory() as tmp:
        script, work = Path(tmp) / "run.mjs", Path(tmp) / "cases.json"
        script.write_text(_NODE, encoding="utf-8")
        work.write_text(json.dumps(data), encoding="utf-8")
        subprocess.run(["node", "--experimental-strip-types", "--no-warnings", str(script), TS_SOURCE.as_uri(),
                        str(work)], check=True)
        result = json.loads(work.read_text(encoding="utf-8"))
    return {"_fixtureSource": {"generatedFrom": "server/backend/src/services/processGraph.ts",
                               "sha256": ts_sha256(),
                               "note": "buildInstanceGraph·pickTransactionRow 출력 저장본(시험 전용, 가상 값). "
                                       "relation_graph_cases.py --write로 다시 만든다."}, **result}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    data = generate()
    def dump(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    # 사례마다 한 줄(diff가 사례 단위로 보이게).
    text = '{"_fixtureSource":' + dump(data["_fixtureSource"]) + ",\n" + ",\n".join(
        f'"{key}":[\n' + ",\n".join(dump(item) for item in data[key]) + "]" for key in ("cases", "pickCases")) + "}\n"
    if args.write:
        FIXTURE.write_text(text, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

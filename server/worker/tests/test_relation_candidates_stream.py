"""층 B 관계 후보 스트리밍 구현 회귀·메모리 시험. 작은 결정적 합성 fixture만 쓰고 DB·네트워크·고객 데이터 없음.

- 회귀(항상): 같은 합성 입력을 메모리 일괄 구현(오라클), 현재 메모리 진입점(build_result),
  현재 스트리밍 핵심(stream_result + 지연 생성 묶음)에 넣고 결과 JSON이 같은지 본다.
- 메모리(선택, RELATION_MEMORY_PROBE=1): 거래 2만·8만 건에서 평가 핵심의 tracemalloc 최고치를 오라클과 비교한다.
"""
import json
import os
import sys
import time
import tracemalloc
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import relation_candidates_oracle as oracle  # noqa: E402
from trainer import relation_candidates as current  # noqa: E402
from trainer.relation_candidates import MessageRow, ProcessRow, TransactionRow  # noqa: E402

GENERATED_AT = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
SOURCE_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
SNAPSHOT_ID = "00000000-0000-4000-8000-000000000002"
SELECTION = {"from": "2026-01-01T00:00:00Z", "until": "2026-02-01T00:00:00Z", "timeBasis": "persisted_at"}


def synthetic_transaction(index):
    """거래 1건의 (키, PROCESS 전 분할, TRANSACTION, MESSAGE) 행. 인덱스 산술만 써서 결정적이다.

    10개 인터페이스 × PR001→PR002→PR003. 섞은 경우: PR001 adapter HTTP(1/3), PR002 누락·F 거래(1/53),
    인계 ID 불일치(1/61), 0채움 2자리 끝자리(1/89), TRANSACTION 행 없음(1/131), PROCESS_ID 없는 행(1/211),
    분할 경계(1/50), 이상 라벨(1/97).
    """
    interface = f"IF_{index % 10}"
    key = f"TX-{index:07d}-0{index % 10}" if index % 89 == 0 else f"TX-{index:07d}"
    pids = [f"{interface}_PR00{step}" for step in (1, 2, 3)]
    if index % 53 == 0:
        pids = [pids[0], pids[2]]
    processes, previous = [], "NONE"
    for step, pid in enumerate(pids):
        millis = 100 + (index * 7 + step * 13) % 500
        raw = {"status": "S", "start_time_raw": "2026-01-01T00:00:00+09:00",
               "end_time_raw": f"2026-01-01T00:00:00.{millis:03d}+09:00"}
        processes.append(ProcessRow(
            f"p-{index:07d}-{step}", f"pe-{index:07d}-{step}", 1, None, key, pid,
            previous if not (index % 53 == 0 and step == 1) else f"{interface}_PR002",
            "HTTP" if step == 0 and index % 3 == 0 else "DB", 0, raw,
            "anomaly" if step == 0 and index % 97 == 0 else None,
            "validation" if step == len(pids) - 1 and index % 50 == 49 else "train"))
        previous = pid
    if index % 211 == 0:
        processes.append(ProcessRow(f"p-{index:07d}-x", f"pe-{index:07d}-x", 1, None, key, None, "NONE", "DB", 0,
                                    {"status": "S"}, None, "train"))
    transactions = [] if index % 131 == 0 else [
        TransactionRow(f"t-{index:07d}", f"te-{index:07d}", 1, None, key, interface,
                       "F" if index % 53 == 0 else "S", 0)]
    messages = []
    for step in range(len(pids) - 1):
        out_id = f"M-{index}-{step}"
        in_id = f"X-{index}-{step}" if index % 61 == 0 else out_id
        messages.append(MessageRow(f"m-{index:07d}-{step}o", f"me-{index:07d}-{step}o", 1, None, key, pids[step],
                                   out_id, "OUT"))
        messages.append(MessageRow(f"m-{index:07d}-{step}i", f"me-{index:07d}-{step}i", 1, None, key, pids[step + 1],
                                   in_id, "IN"))
    return key, processes, transactions, messages


def _revised(index, rows, layer):
    """같은 실행(execution_id)의 이전 리비전 1건을 더한다(1/101 PROCESS 첫 단계, 1/151 TRANSACTION)."""
    if not rows or (layer == "P" and index % 101) or (layer == "T" and index % 151):
        return rows
    latest = rows[0]
    older = type(latest)(*[getattr(latest, name) for name in latest.__dataclass_fields__])
    older = replace_fields(older, observation_id=latest.observation_id + "-r1", revision_no=1)
    latest = replace_fields(latest, revision_no=2)
    if layer == "P":
        older = replace_fields(older, raw={**older.raw, "end_time_raw": "2026-01-01T00:00:00.999+09:00"},
                               depend_process_id="SOMETHING_ELSE")
    else:
        older = replace_fields(older, status="F", interface_id="IF_OLD")
    return [older, latest] + rows[1:]


def replace_fields(row, **changes):
    from dataclasses import replace
    return replace(row, **changes)


def synthetic_groups(count):
    """(키, PROCESS 전 분할, TRANSACTION, MESSAGE) 묶음을 키 순으로. 리비전 정리 전 원행.

    맨 앞: 거래 키 NULL인 PROCESS 행(조각 3행씩, 같은 키로 이어짐), 그다음 키 ""인 PROCESS·MESSAGE 행.
    """
    nulls = [ProcessRow(f"n-{i:05d}", f"ne-{i:05d}", 1, None, None, f"IF_{i % 10}_PR001", "NONE", "DB", 0,
                        {"status": "S", "start_time_raw": "2026-01-01T00:00:00+09:00",
                         "end_time_raw": f"2026-01-01T00:00:00.{100 + i:03d}+09:00"}, None, "train")
             for i in range(count // 300 + 7)]
    for start in range(0, len(nulls), 3):
        yield None, nulls[start:start + 3], [], []
    yield "", [ProcessRow("e-1", "ee-1", 1, None, "", "IF_0_PR002", "IF_0_PR001", "DB", 0, {"status": "S"}, None,
                          "train"),
               ProcessRow("e-2", "ee-2", 1, None, "", None, "NONE", None, 0, {"status": "S"}, None, "train")], [], \
        [MessageRow("em-1", "eme-1", 1, None, "", "IF_0_PR001", "Z", "OUT")]
    for index in range(count):
        key, processes, transactions, messages = synthetic_transaction(index)
        yield key, _revised(index, processes, "P"), _revised(index, transactions, "T"), messages


def synthetic_bundles(count):
    """지연 생성: 묶음 1개씩 만들어 실행마다 최신 리비전으로 줄이고(SQL DISTINCT ON 대신) 바로 넘긴다."""
    for key, processes, transactions, messages in synthetic_groups(count):
        yield current.train_bundle(key, current.latest_per_execution(processes)[0],
                                   current.latest_per_execution(transactions)[0],
                                   current.latest_per_execution(messages)[0])


def synthetic_loaded(count):
    """오라클 입력: 오라클 load_inputs가 DB에서 하던 것처럼 전체 행 목록(이전 리비전 포함)을 메모리에 만든다."""
    loaded = {"sourceId": SOURCE_ID, "selection": SELECTION, "processes": [], "transactions": [], "messages": []}
    for _, processes, transactions, messages in synthetic_groups(count):
        loaded["processes"] += processes
        loaded["transactions"] += transactions
        loaded["messages"] += messages
    return loaded


def raw_counts(count):
    """(원행 수, 정리된 이전 리비전 수) — DB 경로의 count 쿼리에 해당."""
    totals = {"processRows": 0, "transactionRows": 0, "messageRows": 0}
    superseded = {"process": 0, "transaction": 0, "message": 0}
    for _, processes, transactions, messages in synthetic_groups(count):
        for total, layer, rows in (("processRows", "process", processes), ("transactionRows", "transaction", transactions),
                                   ("messageRows", "message", messages)):
            totals[total] += len(rows)
            superseded[layer] += current.latest_per_execution(rows)[1]
    return totals, superseded


def streaming(count, parsed, counts):
    totals, superseded = counts
    return current.stream_result(lambda: synthetic_bundles(count), parsed, job_id="job-1", source_id=SOURCE_ID,
                                 selection=SELECTION, raw_counts=totals, superseded=superseded,
                                 generated_at=GENERATED_AT)


def oracle_with_current_limitation(result):
    """오라클 결과 전체(build_result)에 아래 변환을 적용한다. 다른 필드는 그대로 둬 엄격 비교를 유지한다."""
    return {**result, "thresholdCandidates": oracle_thresholds_with_current_limitation(result["thresholdCandidates"])}


def oracle_thresholds_with_current_limitation(thresholds):
    """오라클 고정 뒤 limitation 문구를 화면용으로 바꾸고 limitationRefs를 더했으므로 오라클 결과의 이 두 필드만 새 값으로 맞춘다."""
    items = []
    for item in thresholds:
        # python -O에서도 확인되도록 assert 대신 명시적으로 실패시킨다.
        if item["limitation"] != oracle.THRESHOLD_LIMITATION or "limitationRefs" in item:
            raise AssertionError(f"unexpected oracle threshold limitation fields: {item!r}")
        fixed = {}
        for key, value in item.items():
            if key == "limitation":
                fixed["limitation"] = current.THRESHOLD_LIMITATION
                fixed["limitationRefs"] = list(current.THRESHOLD_LIMITATION_REFS)
            else:
                fixed[key] = value
        items.append(fixed)
    return items


def as_json(value):
    """dict 비교는 1 == 1.0, -0.0 == 0.0을 같게 보므로 직렬화 문자열(키 순서 포함)로 비교한다."""
    return json.dumps(value, sort_keys=False, ensure_ascii=False)


class StreamingRegressionTests(unittest.TestCase):
    def test_streaming_matches_oracle_on_synthetic_fixture(self):
        count = 1200
        parsed = current.parse_input({"snapshotId": SNAPSHOT_ID, "excludeProcessIds": ["IF_3_PR003"]})
        expected = oracle_with_current_limitation(
            oracle.build_result(synthetic_loaded(count), parsed, job_id="job-1", generated_at=GENERATED_AT))
        in_memory = current.build_result(synthetic_loaded(count), parsed, job_id="job-1", generated_at=GENERATED_AT)
        streamed = streaming(count, parsed, raw_counts(count))
        self.assertEqual(as_json(in_memory), as_json(expected))
        self.assertEqual(as_json(streamed), as_json(expected))
        self.assertEqual(expected["excluded"]["supersededRevisions"],
                         {"process": len(range(0, count, 101)), "transaction": len([i for i in range(0, count, 151)
                                                                                   if i % 131]),
                          "message": 0})
        self.assertEqual(expected["unassigned"]["rows"]["processTransactionIdAbsent"], count // 300 + 7 + 2)
        # fixture가 실제로 여러 경우를 지나는지(빈 결과끼리 같아서 통과하는 일 방지)
        self.assertEqual(len(expected["data"]["interfaceTransactions"]), 10)
        self.assertTrue(expected["excluded"]["transactions"]["cross_split"] > 0)
        self.assertTrue(expected["unassigned"]["transactions"]["no_transaction_row"] > 0)
        self.assertTrue(any(item["violationReasons"] for item in expected["candidates"]))
        self.assertTrue(expected["thresholdCandidates"])

    def test_value_summary_matches_research_design_summary(self):
        from array import array
        from research_design import summary
        keys = ("valid_count", "variation", "min", "max", "p95", "quantile_method")
        for values in ([], [5.0], [3.0, 3.0, 3.0], [0.0, 1e-9, 7.25, 3.5, 100.0, 2.0],
                       [float(index * 37 % 1000) / 7 for index in range(997)]):
            with self.subTest(values=values[:5]):
                expected = summary(list(values))
                got = current.value_summary(array("d", values))
                self.assertEqual({key: got[key] for key in keys}, {key: expected[key] for key in keys})

    def test_bundle_order_violation_stops(self):
        bundles = [current.TxBundle("B", [], [], []), current.TxBundle("A", [], [], [])]
        with self.assertRaisesRegex(ValueError, "relation_stream_order_violation"):
            current.mine_stream(lambda: iter(bundles))

    def test_db_row_grouping_and_merge(self):
        rows = [MessageRow("o1", "e1", 1, None, None, "P", "m", "OUT"),
                MessageRow("o2", "e2", 1, None, "A", "P", "m", "OUT"), MessageRow("o3", "e3", 1, None, "A", "P", "m", "IN"),
                MessageRow("o4", "e4", 1, None, "C", "P", "m", "IN")]
        grouped = list(current._grouped(iter(rows), lambda row: row))
        self.assertEqual([(key, len(items)) for key, items in grouped], [(None, 1), ("A", 2), ("C", 1)])
        # 키 없는 행은 조각으로(같은 키 반복), 키 있는 거래는 크기와 무관하게 한 묶음
        many = [MessageRow(f"n{i}", f"ne{i}", 1, None, None, "P", "m", "OUT") for i in range(5)] + \
            [MessageRow(f"b{i}", f"be{i}", 1, None, "B", "P", "m", "OUT") for i in range(5)]
        self.assertEqual([(key, len(items)) for key, items in current._grouped(iter(many), lambda row: row, chunk=2)],
                         [(None, 2), (None, 2), (None, 1), ("B", 5)])
        current.mine_stream(lambda: iter([current.TxBundle(None, [], [], []), current.TxBundle(None, [], [], []),
                                          current.TxBundle("", [], [], []), current.TxBundle("", [], [], [])]))
        merged = list(current._merge(iter([("A", ["p"]), ("B", ["p2"])]), iter([("B", ["t"])]), iter(grouped)))
        self.assertEqual([(key, [len(part) for part in parts]) for key, parts in merged],
                         [(None, [0, 0, 1]), ("A", [1, 0, 2]), ("B", [1, 1, 0]), ("C", [0, 0, 1])])
        with self.assertRaisesRegex(ValueError, "relation_stream_order_violation"):
            list(current._grouped(iter([rows[3], rows[1]]), lambda row: row))


@unittest.skipUnless(os.environ.get("RELATION_MEMORY_PROBE") == "1", "RELATION_MEMORY_PROBE=1 일 때만(수십 초)")
class StreamingMemoryProbe(unittest.TestCase):
    def measure(self, function):
        tracemalloc.start()
        started = time.perf_counter()
        result = function()
        elapsed = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        return result, peak, elapsed

    def test_peak_memory_old_vs_new(self):
        parsed = current.parse_input({"snapshotId": SNAPSHOT_ID})
        # 지연 import(numpy·research_design 등)가 첫 측정에 섞이지 않게 두 경로를 먼저 한 번씩 돌린다.
        oracle.build_result(synthetic_loaded(200), parsed, job_id="job-1", generated_at=GENERATED_AT)
        streaming(200, parsed, raw_counts(200))
        peaks = {}
        for count in (20000, 80000):
            counts = raw_counts(count)
            totals = counts[0]
            old, old_peak, old_time = self.measure(lambda: oracle.build_result(
                synthetic_loaded(count), parsed, job_id="job-1", generated_at=GENERATED_AT))
            new, new_peak, new_time = self.measure(lambda: streaming(count, parsed, counts))
            self.assertEqual(as_json(new), as_json(oracle_with_current_limitation(old)))
            # 처리 시간 표본(설계상 허용, 값당 8바이트 array('d'))은 따로 떼어 본다.
            samples = 8 * sum(item["evidence"]["n"] for item in new["thresholdCandidates"])
            peaks[count] = (old_peak, new_peak, samples)
            print(f"\n[relation memory] transactions={count} rows(P/T/M)={totals['processRows']}/"
                  f"{totals['transactionRows']}/{totals['messageRows']} old_peak={old_peak / 2**20:.2f}MiB "
                  f"({old_time:.1f}s) new_peak={new_peak / 2**20:.2f}MiB ({new_time:.1f}s) "
                  f"new_elapsed_samples={samples / 2**20:.2f}MiB", flush=True)
        old_growth = peaks[80000][0] / peaks[20000][0]
        new_growth = peaks[80000][1] / peaks[20000][1]
        state_growth = (peaks[80000][1] - peaks[80000][2]) / (peaks[20000][1] - peaks[20000][2])
        print(f"[relation memory] growth 20k->80k old x{old_growth:.2f} new x{new_growth:.2f} "
              f"new-without-samples x{state_growth:.2f}", flush=True)
        self.assertGreater(old_growth, 3.0)
        self.assertLess(state_growth, 1.5)
        self.assertLess(peaks[80000][1] * 10, peaks[80000][0])


if __name__ == "__main__":
    unittest.main()

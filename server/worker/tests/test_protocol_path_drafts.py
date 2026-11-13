"""프로토콜 경로 초안(`protocolPathDrafts`, recommend_relations 결과 항목) 단위 시험.

작은 메모리 fixture만 쓰고 DB·네트워크·고객 데이터 없음. 정의: Detector contracts/protocol-rules.md 3.2·3.4·9절(fafc630).
"""
import itertools
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trainer import protocol_paths  # noqa: E402
from trainer.protocol_paths import ProtocolPathDrafts, path_key, transaction_routes  # noqa: E402
from trainer.relation_candidates import (  # noqa: E402
    ProcessRow, TransactionRow, TxBundle, build_result, parse_input)

_ids = itertools.count(1)


def P(tx, pid, depend="NONE", channel="CH_A", *, retry=0, split="train"):
    number = next(_ids)
    return ProcessRow(f"obs-{number:06d}", f"exe-{number:06d}", 1, None, tx, pid, depend, "DB", retry,
                      {"status": "S"}, None, split, channel)


def T(tx, interface="IF_A", start="CH_A", end="CH_B", *, retry=0, execution="auto"):
    number = next(_ids)
    return TransactionRow(f"obs-{number:06d}", f"exe-{number:06d}" if execution == "auto" else execution, 1, None, tx,
                          interface, "S", retry, start, end)


def chain(tx, channels, *, prefix="IF_A_PR"):
    """PR001 → PR002 → … 사슬, 단계마다 채널."""
    rows, previous = [], "NONE"
    for index, channel in enumerate(channels, 1):
        pid = f"{prefix}{index:03d}"
        rows.append(P(tx, pid, previous, channel))
        previous = pid
    return rows


def reason(rows):
    return transaction_routes(rows)[0]


def drafts(bundles):
    acc = ProtocolPathDrafts()
    for bundle in bundles:
        acc.add(bundle)
    return acc.result()


class RouteDefinitionTests(unittest.TestCase):
    def test_start_classification_four_kinds(self):
        # 정확히 NONE(ASCII 공백·탭·CR·LF만 빼고) → 시작. NULL·""·\N → depend_process_id_missing. 그 밖은 값.
        self.assertEqual(transaction_routes([P("T1", "A", " NONE\t", "C1")]), (None, [(("C1",), ["A"])]))
        for missing in (None, "", "  ", "\\N", " \\N\r\n"):
            with self.subTest(missing=missing):
                self.assertEqual(reason([P("T1", "A", missing)]), "depend_process_id_missing")
        # 소문자 none·U+FEFF가 붙은 NONE은 값 → 선행 묶음이 없어 시작 단계 0개 = start_ambiguous
        for value in ("none", "\ufeffNONE"):
            with self.subTest(value=value):
                self.assertEqual(reason([P("T1", "A", value)]), "start_ambiguous")
        # 값이 같은 거래의 process_id를 가리키면 사슬
        self.assertEqual(transaction_routes([P("T1", "A", "NONE", "C1"), P("T1", "B", "A", "C2")])[1],
                         [(("C1", "C2"), ["A", "B"])])

    def test_consecutive_same_channel_not_merged(self):
        self.assertEqual(transaction_routes(chain("T1", ["CH_A", "CH_A", "CH_B"]))[1],
                         [(("CH_A", "CH_A", "CH_B"), ["IF_A_PR001", "IF_A_PR002", "IF_A_PR003"])])

    def test_branching_gives_one_route_per_end_step(self):
        rows = [P("T1", "A", "NONE", "CA"), P("T1", "C", "A", "CC"), P("T1", "B", "A", "CB"), P("T1", "D", "B", "CD")]
        self.assertEqual(transaction_routes(rows), (None, [(("CA", "CB", "CD"), ["A", "B", "D"]),
                                                           (("CA", "CC"), ["A", "C"])]))

    def test_cycle_and_broken_chain(self):
        # B↔C 순환은 시작에서 닿지 않음 → chain_broken
        self.assertEqual(reason([P("T1", "A"), P("T1", "B", "C"), P("T1", "C", "B")]), "chain_broken")
        self.assertEqual(reason([P("T1", "A"), P("T1", "B", "X")]), "chain_broken")  # 없는 선행
        self.assertEqual(reason([P("T1", "A"), P("T1", "B", "B")]), "chain_broken")  # 자기 자신
        self.assertEqual(reason([P("T1", "A"), P("T1", "B")]), "start_ambiguous")  # 시작 2개

    def test_step_limit_64_boundary(self):
        self.assertIsNone(reason(chain("T1", [f"C{i}" for i in range(64)])))
        self.assertEqual(reason(chain("T1", [f"C{i}" for i in range(65)])), "path_too_long")

    def test_route_limit_64_boundary(self):
        def fan(count):
            return [P("T1", "ROOT")] + [P("T1", f"L{i:03d}", "ROOT") for i in range(count)]
        ok_reason, routes = transaction_routes(fan(64))
        self.assertEqual((ok_reason, len(routes)), (None, 64))
        self.assertEqual(reason(fan(65)), "routes_too_many")

    def test_process_rows_over_200_truncated(self):
        def fan(count):
            return [P("T1", "ROOT")] + [P("T1", f"L{i:03d}", "ROOT") for i in range(count - 1)]
        self.assertEqual(reason(fan(200)), "routes_too_many")  # 200행은 입력 상한 안
        self.assertEqual(reason(fan(201)), "graph_truncated")

    def test_ambiguous_group_drops_whole_transaction(self):
        # 같은 retry_count 2건 → 묶음 모호 → 다른 단계가 멀쩡해도 거래 경로 전체를 버린다
        rows = chain("T1", ["C1", "C2"]) + [P("T1", "IF_A_PR002", "IF_A_PR001", "C2", retry=0)]
        self.assertEqual(transaction_routes(rows), ("precedes_ambiguous", []))
        # 재시도(번호 다름)는 한 묶음이라 모호가 아니다
        rows = chain("T1", ["C1", "C2"]) + [P("T1", "IF_A_PR002", "IF_A_PR001", "C2", retry=1)]
        self.assertEqual(transaction_routes(rows)[1], [(("C1", "C2"), ["IF_A_PR001", "IF_A_PR002"])])
        # 시도마다 DEPEND 분류가 다름(start vs 값) → precedes_ambiguous
        rows = [P("T1", "A"), P("T1", "B", "A", retry=0), P("T1", "B", "NONE", retry=1)]
        self.assertEqual(reason(rows), "precedes_ambiguous")
        # 채널: 시도 간 값이 다르거나 있음·없음이 섞이면 channel_ambiguous, 모두 없으면 channel_absent
        self.assertEqual(reason([P("T1", "A", channel="X", retry=0), P("T1", "A", channel="Y", retry=1)]),
                         "channel_ambiguous")
        self.assertEqual(reason([P("T1", "A", channel="X", retry=0), P("T1", "A", channel="\\N", retry=1)]),
                         "channel_ambiguous")
        self.assertEqual(reason([P("T1", "A", channel=" ")]), "channel_absent")
        # 채널 값은 원래 문자열 그대로(trim 없음)
        self.assertEqual(transaction_routes([P("T1", "A", channel=" X ")])[1], [((" X ",), ["A"])])

    def test_reason_priority(self):
        """824d0b9 3.2 끝: ① graph_truncated ② 단계 번호가 작은 것 ③ 같은 단계면 3.4 표 순서(다른 묶음끼리 포함)."""
        # ① 200행 초과면 PROCESS_ID 없음이 있어도 graph_truncated
        rows = [P("T1", "ROOT")] + [P("T1", f"L{i:03d}", "ROOT") for i in range(199)] + [P("T1", None, "ROOT")]
        self.assertEqual(reason(rows), "graph_truncated")
        # 2단계: process_id_missing > precedes_ambiguous(다른 묶음)
        self.assertEqual(reason([P("T1", "A", retry=0), P("T1", "A", retry=0), P("T1", None)]), "process_id_missing")
        # 3단계: precedes_ambiguous > depend_process_id_missing(다른 묶음)
        rows = [P("T1", "A"), P("T1", "B", None), P("T1", "C", "A", retry=0), P("T1", "C", "NONE", retry=1)]
        self.assertEqual(reason(rows), "precedes_ambiguous")
        # 2단계(묶음 모호) > 3단계(DEPEND 결측)
        self.assertEqual(reason([P("T1", "A", None), P("T1", "B", "A", retry=0), P("T1", "B", "A", retry=0)]),
                         "precedes_ambiguous")
        # 3단계 > 4단계: DEPEND 결측과 시작 2개가 함께
        self.assertEqual(reason([P("T1", "A"), P("T1", "B"), P("T1", "C", "")]), "depend_process_id_missing")
        # 4단계 > 5단계: 시작 0개 + 순환
        self.assertEqual(reason([P("T1", "A", "B"), P("T1", "B", "A")]), "start_ambiguous")
        # 5단계 > 6단계: 끊긴 사슬 + 채널 없음
        self.assertEqual(reason([P("T1", "A", channel=None), P("T1", "B", "X")]), "chain_broken")
        # 6단계: channel_absent > channel_ambiguous(앞 묶음이 모호여도 뒤 묶음 없음이 이긴다)
        rows = [P("T1", "A", channel="X", retry=0), P("T1", "A", channel="Y", retry=1), P("T1", "B", "A", channel=None)]
        self.assertEqual(reason(rows), "channel_absent")
        # 6단계 > 8단계: 긴 사슬 + 채널 모호
        rows = chain("T1", [f"C{i}" for i in range(65)]) + [P("T1", "IF_A_PR001", "NONE", "OTHER", retry=1)]
        self.assertEqual(reason(rows), "channel_ambiguous")
        # 8단계: path_too_long > routes_too_many
        rows = chain("T1", [f"C{i}" for i in range(65)]) + [P("T1", f"L{i:03d}", "IF_A_PR001") for i in range(64)]
        self.assertEqual(reason(rows), "path_too_long")


class InterfaceAndCountingTests(unittest.TestCase):
    def test_interface_rules_and_exclusions(self):
        bundles = [
            TxBundle(None, [P(None, "A")], [], []),  # transaction_id_missing(행 수)
            TxBundle("", [P("", "A"), P("", "B")], [], []),
            TxBundle("T0", chain("T0", ["CH_A", "CH_B"]), [], []),  # no_transaction_row
            TxBundle("T1", chain("T1", ["CH_A", "CH_B"]), [T("T1", None)], []),  # interface_absent
            TxBundle("T2", chain("T2", ["CH_A", "CH_B"]), [T("T2", "IF_A")], [], "cross_split"),
            # 대표 행 모호(재시도 번호 동률): 값이 모두 같으면 집계하고 endpoints는 비교 안 함
            TxBundle("T3", chain("T3", ["CH_A", "CH_B"]), [T("T3", "IF_A", retry=1), T("T3", "IF_A", retry=1)], []),
            # 모호 + 하나라도 없음 → transaction_row_ambiguous (관계 후보 resolve_transactions와 다른 점)
            TxBundle("T4", chain("T4", ["CH_A", "CH_B"]), [T("T4", "IF_A", retry=1), T("T4", "\\N", retry=1)], []),
            TxBundle("T5", chain("T5", ["CH_A", "CH_B"]), [T("T5", "IF_A", retry=1), T("T5", "IF_B", retry=1)], []),
            # 재시도: 가장 큰 번호의 행 interface·채널
            TxBundle("T6", chain("T6", ["CH_A", "CH_B"]), [T("T6", "IF_OLD", "Z", "Z", retry=0),
                                                           T("T6", "IF_A", retry=1)], []),
            TxBundle("T7", [P("T7", "A", None)], [T("T7")], []),  # 인터페이스별 경로 사유
            TxBundle("T8", [], [T("T8")], []),  # train PROCESS 행 없음 → 세지 않음
        ]
        result = drafts(bundles)
        # excludedReasons는 거래 단위만. TRANSACTION_ID 없는 PROCESS 행은 행 수로 따로(None 1행 + "" 2행).
        self.assertEqual(result["excludedReasons"], {"cross_split": 1, "no_transaction_row": 1,
                                                     "transaction_row_ambiguous": 2, "interface_absent": 1,
                                                     "depend_process_id_missing": 1})
        self.assertEqual(result["transactionIdMissingRows"], 3)
        self.assertNotIn("transaction_id_missing", result["parameters"]["reasonOrder"])
        (only,) = result["interfaces"]
        self.assertEqual((only["interfaceId"], only["transactions"], only["pathTransactions"]), ("IF_A", 3, 2))
        self.assertEqual(only["excludedReasons"], {"depend_process_id_missing": 1})
        self.assertEqual(only["endpoints"], {"compared": 1, "mismatch": 0, "notCompared": 1})
        self.assertEqual(only["paths"][0]["exampleRefs"][0],
                         {"refs": ["transaction:T3"], "steps": ["process:T3:IF_A_PR001", "process:T3:IF_A_PR002"]})

    def test_endpoint_mismatch_is_information_only(self):
        bundles = [TxBundle("T1", chain("T1", ["CH_A", "CH_C"]), [T("T1", start="CH_A", end="CH_B")], []),
                   TxBundle("T2", chain("T2", ["CH_A", "CH_B"]), [T("T2", start="CH_A", end="CH_B")], []),
                   TxBundle("T3", chain("T3", ["CH_A", "CH_B"]), [T("T3", start=None, end="CH_B")], []),
                   # 갈라진 거래: 모든 경로의 끝이 end와 같아야 일치
                   TxBundle("T4", [P("T4", "A", channel="CH_A"), P("T4", "B", "A", "CH_B"), P("T4", "C", "A", "CH_C")],
                            [T("T4", start="CH_A", end="CH_B")], [])]
        result = drafts(bundles)
        (only,) = result["interfaces"]
        self.assertEqual(only["pathTransactions"], 4)  # 제외 아님
        self.assertEqual(only["endpoints"], {"compared": 3, "mismatch": 2, "notCompared": 1})
        self.assertEqual(result["endpointMismatch"], 2)
        self.assertEqual(only["branchingTransactions"], 1)
        by_key = {item["pathKey"]: item for item in only["paths"]}
        self.assertEqual({key: (item["transactions"], item["fromBranching"]) for key, item in by_key.items()},
                         {"CH_A:CH_B": (3, 1), "CH_A:CH_C": (2, 1)})

    def test_thirty_transaction_boundary_and_rare(self):
        def bundles(count, rare_every=None):
            for index in range(count):
                tx = f"TX-{index:04d}"
                channels = ["CH_A", "CH_X"] if rare_every and index % rare_every == 0 else ["CH_A", "CH_B"]
                yield TxBundle(tx, chain(tx, channels), [T(tx)], [])
        self.assertEqual(drafts(bundles(29))["interfaces"][0]["status"], "insufficient_evidence")
        self.assertEqual(drafts(bundles(30))["interfaces"][0]["status"], "draft")
        # 100거래 중 1건 = 정확히 1% → rare 아님, 101거래 중 1건 → rare
        paths = drafts(bundles(100, rare_every=100))["interfaces"][0]["paths"]
        self.assertEqual([(p["pathKey"], p["transactions"], p["ratio"], p["rare"]) for p in paths],
                         [("CH_A:CH_B", 99, 0.99, False), ("CH_A:CH_X", 1, 0.01, False)])
        paths = drafts(bundles(101, rare_every=101))["interfaces"][0]["paths"]
        self.assertEqual([(p["transactions"], p["rare"]) for p in paths], [(100, False), (1, True)])
        # 예시는 거래 ID 순 앞 5건
        self.assertEqual([ref["refs"][0] for ref in paths[0]["exampleRefs"]],
                         [f"transaction:TX-{i:04d}" for i in range(1, 6)])

    def test_output_paths_capped_at_64(self):
        """저장 상한(4절, 항목당 64개)에 맞춰 앞 64개만 내고 나머지는 omittedPaths. ratio·rare·status는 전체 기준."""
        def bundles(distinct):
            # 경로 P000은 40거래, 나머지는 경로마다 1거래(채널 이름으로 서로 다른 경로)
            index = 0
            for path in range(distinct):
                for _ in range(40 if path == 0 else 1):
                    tx = f"TX-{index:05d}"
                    index += 1
                    yield TxBundle(tx, chain(tx, ["CH_A", f"P{path:03d}"]), [T(tx)], [])
        (at_limit,) = drafts(bundles(64))["interfaces"]
        self.assertEqual((len(at_limit["paths"]), at_limit["omittedPaths"], at_limit["distinctPaths"]),
                         (64, {"paths": 0, "transactions": 0}, 64))
        (over,) = drafts(bundles(65))["interfaces"]
        self.assertEqual((len(over["paths"]), over["omittedPaths"], over["distinctPaths"]),
                         (64, {"paths": 1, "transactions": 1}, 65))
        # 정렬: 거래 수 내림차순, 같으면 pathKey 코드포인트 오름차순 → 잘린 것은 pathKey가 가장 뒤인 1개(P064)
        self.assertEqual([p["pathKey"] for p in over["paths"][:2]], ["CH_A:P000", "CH_A:P001"])
        self.assertEqual(over["paths"][-1]["pathKey"], "CH_A:P063")
        # 분모는 잘라내기 전 전체 거래(40 + 64 = 104)
        self.assertEqual(over["pathTransactions"], 104)
        self.assertEqual(over["paths"][1]["ratio"], round(1 / 104, 6))
        self.assertTrue(over["paths"][1]["rare"])

    def test_path_key_escape(self):
        self.assertEqual(path_key(["A:B", "50%", "C"]), "A%3AB:50%25:C")

    def test_same_channels_counted_once_per_transaction(self):
        rows = [P("T1", "A", channel="CH_A"), P("T1", "B", "A", "CH_B"), P("T1", "C", "A", "CH_B")]
        (only,) = drafts([TxBundle("T1", rows, [T("T1")], [])])["interfaces"]
        self.assertEqual([(p["pathKey"], p["transactions"], p["fromBranching"]) for p in only["paths"]],
                         [("CH_A:CH_B", 1, 1)])
        self.assertEqual(only["paths"][0]["exampleRefs"][0]["steps"], ["process:T1:A", "process:T1:B"])


class ResultIntegrationTests(unittest.TestCase):
    def test_build_result_adds_last_key_and_ignores_exclude_process_ids(self):
        processes, transactions = [], []
        for index in range(31):
            tx = f"TX-{index:04d}"
            processes += chain(tx, ["CH_A", "CH_B", "CH_C"])
            transactions.append(T(tx, start="CH_A", end="CH_C"))
        # 분할 경계 거래는 관계 후보와 같이 제외(cross_split)
        processes[-1] = P("TX-0030", "IF_A_PR003", "IF_A_PR002", "CH_C", split="validation")
        loaded = {"sourceId": uuid.uuid4(), "selection": {}, "processes": processes, "transactions": transactions,
                  "messages": []}
        parsed = parse_input({"snapshotId": str(uuid.uuid4()), "excludeProcessIds": ["IF_A_PR002"]})
        result = build_result(loaded, parsed, job_id="job-1", generated_at=datetime(2026, 11, 13, tzinfo=timezone.utc))
        self.assertEqual(list(result)[-1], "protocolPathDrafts")
        section = result["protocolPathDrafts"]
        self.assertEqual(section["excludedReasons"], {"cross_split": 1})
        (only,) = section["interfaces"]
        # excludeProcessIds(관계 후보 입력)는 경로에 적용하지 않는다 — 가운데 단계 채널이 그대로 있다
        self.assertEqual([(p["channels"], p["transactions"]) for p in only["paths"]], [(["CH_A", "CH_B", "CH_C"], 30)])
        self.assertEqual((only["status"], only["endpoints"]["mismatch"]), ("draft", 0))
        self.assertEqual(section["parameters"]["maxPathSteps"], protocol_paths.MAX_PATH_STEPS)


if __name__ == "__main__":
    unittest.main()

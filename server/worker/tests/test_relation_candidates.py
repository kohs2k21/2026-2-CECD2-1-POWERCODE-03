"""층 B 관계 후보(실험 `recommend_relations`) 단위 시험. 작은 메모리 fixture만 쓰고 DB·네트워크·고객 데이터 없음."""
import copy
import hashlib
import itertools
import json
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# `python -m unittest tests.test_relation_candidates`처럼 discover 밖에서도 형제 시험 모듈을 찾게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import relation_candidates_oracle as oracle  # noqa: E402
from test_relation_candidates_stream import (  # noqa: E402
    oracle_thresholds_with_current_limitation, oracle_with_current_limitation)
from trainer import relation_candidates as current  # noqa: E402
from trainer.relation_candidates import (  # noqa: E402
    MessageRow, ProcessRow, TransactionRow, candidate_status, graph_id, graph_value_absent,
    parse_input, pattern_step_id, suffix_class)

ORACLE_COMPARISONS = {"mine_relations": 0, "build_result": 0, "threshold_candidates": 0}


def as_json(value):
    """비교용 JSON 문자열. dict 비교는 1 == 1.0, -0.0 == 0.0을 같게 보므로 직렬화 결과(키 순서 포함)를 비교한다."""
    return json.dumps(value, sort_keys=False, ensure_ascii=False)


def _same_as_oracle(name, new, old):
    """회귀: 스트리밍 구현 결과가 메모리 일괄 구현(오라클)과 같아야 한다(리스트·키 순서, 숫자 표기 포함)."""
    new_text, old_text = as_json(new), as_json(old)
    if new_text != old_text:
        raise AssertionError(f"{name}: streaming result differs from oracle\n"
                             f"new={new_text[:2000]}\nold={old_text[:2000]}")
    ORACLE_COMPARISONS[name] += 1
    return new


# 아래 세 이름은 시험 전체에서 새 구현을 부르면서 같은 입력으로 오라클도 불러 결과를 비교한다.
def mine_relations(*args, **kwargs):
    return _same_as_oracle("mine_relations", current.mine_relations(*args, **kwargs),
                           oracle.mine_relations(*args, **kwargs))


# 오라클의 thresholdCandidates는 limitation·limitationRefs 두 필드만 현재 값으로 맞춘다(오라클 고정 뒤 화면 문구 정리).
def build_result(*args, **kwargs):
    return _same_as_oracle("build_result", current.build_result(*args, **kwargs),
                           oracle_with_current_limitation(oracle.build_result(*args, **kwargs)))


def threshold_candidates(*args, **kwargs):
    return _same_as_oracle("threshold_candidates", current.threshold_candidates(*args, **kwargs),
                           oracle_thresholds_with_current_limitation(oracle.threshold_candidates(*args, **kwargs)))


FIXTURES = Path(__file__).resolve().parent / "fixtures"
_ids = itertools.count(1)


def P(tx, pid, depend="NONE", *, adapter="DB", retry=0, execution=None, revision=1, status="S",
      start="2026-01-01T00:00:00+09:00", end="2026-01-01T00:00:00.100+09:00", label=None, split="train"):
    number = next(_ids)
    raw = {"status": status, "start_time_raw": start, "end_time_raw": end}
    return ProcessRow(f"obs-{number:06d}", execution or f"exe-{number:06d}", revision, None, tx, pid, depend, adapter,
                      retry, raw, label, split)


def T(tx, interface="IF_A", status="S", retry=0, execution="auto"):
    number = next(_ids)
    return TransactionRow(f"obs-{number:06d}", f"exe-{number:06d}" if execution == "auto" else execution, 1, None, tx,
                          interface, status, retry)


def M(tx, pid, message_id, direction):
    number = next(_ids)
    return MessageRow(f"obs-{number:06d}", f"exe-{number:06d}", 1, None, tx, pid, message_id, direction)


def chain(count, steps=("IF_A_PR001", "IF_A_PR002"), *, prefix="TX", interface="IF_A", messages=True):
    """steps[0] → steps[1] → … 를 count개 거래로 만든다. 인계 메시지는 같은 MESSAGE_ID."""
    processes, transactions, message_rows = [], [], []
    for index in range(count):
        tx = f"{prefix}-{index:04d}"
        transactions.append(T(tx, interface))
        previous = "NONE"
        for position, pid in enumerate(steps):
            processes.append(P(tx, pid, previous))
            if messages and position + 1 < len(steps):
                message_rows.append(M(tx, pid, f"MSG-{index}-{position}", "OUT"))
                message_rows.append(M(tx, steps[position + 1], f"MSG-{index}-{position}", "IN"))
            previous = pid
    return processes, transactions, message_rows


def replace(rows, tx, pid, new):
    return [new if (row.transaction_id == tx and row.process_id == pid) else row for row in rows]


def cand(result, kind, frm, to="*", **extra):
    found = [item for item in result["candidates"] if item["kind"] == kind and item["from"]["processId"] == frm
             and (to == "*" or (item["to"] or {}).get("processId") == to)
             and all(item.get(key) == value for key, value in extra.items())]
    assert len(found) == 1, (kind, frm, to, [(c["kind"], c["from"], c["to"]) for c in result["candidates"]])
    return found[0]


def no_cand(result, kind, frm, to="*"):
    return not [item for item in result["candidates"] if item["kind"] == kind and item["from"]["processId"] == frm
                and (to == "*" or (item["to"] or {}).get("processId") == to)]


class OracleWrapperTests(unittest.TestCase):
    def test_wrappers_compare_with_oracle(self):
        before = dict(ORACLE_COMPARISONS)
        processes, transactions, messages = chain(30)
        mine_relations(processes, transactions, messages)
        threshold_candidates(processes, {row.transaction_id: "IF_A" for row in transactions})
        build_result({"sourceId": uuid.uuid4(), "selection": {}, "processes": processes, "transactions": transactions,
                      "messages": messages}, parse_input({"snapshotId": str(uuid.uuid4())}), job_id="job-x",
                     generated_at=datetime(2026, 11, 12, tzinfo=timezone.utc))
        for name in ORACLE_COMPARISONS:
            self.assertGreater(ORACLE_COMPARISONS[name], before[name], name)
        # 문자열 비교는 dict 비교가 놓치는 숫자 표기 차이를 잡는다.
        self.assertEqual({"n": 1}, {"n": 1.0})
        self.assertNotEqual(as_json({"n": 1}), as_json({"n": 1.0}))
        with self.assertRaises(AssertionError):
            _same_as_oracle("mine_relations", {"n": -0.0}, {"n": 0.0})


class GraphIdVectorTests(unittest.TestCase):
    def test_shared_vectors_from_webui_copy(self):
        data = json.loads((FIXTURES / "process-graph-id-vectors.json").read_text(encoding="utf-8"))
        source = FIXTURES.parents[2] / "contracts" / "process-graph-id-vectors.json"
        if source.is_file():  # 저장소 안에서는 사본이 계약 원본과 같은 판인지 본다(줄 끝은 LF로 맞춰 비교).
            digest = hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            self.assertEqual(data["_fixtureSource"]["sha256"], digest)
        self.assertEqual((len(data["idVectors"]), len(data["absenceVectors"]), len(data["patternIdVectors"])),
                         (17, 26, 8))
        for vector in data["idVectors"] + data["patternIdVectors"]:
            with self.subTest(vector["case"]):
                self.assertEqual(graph_id(vector["kind"], vector["components"]), vector["expectedId"])
                if vector["expectedId"] is None:
                    self.assertEqual(vector["outcome"], "undetermined")
        for vector in data["absenceVectors"]:
            with self.subTest(vector["case"]):
                self.assertEqual(graph_value_absent(vector["value"], vector.get("field")), vector["absent"])

    def test_python_strip_would_disagree(self):
        # 인자 없는 str.strip()을 썼다면 U+0085·U+001C가 지워져 '없음'이 된다. 계약은 값으로 본다.
        self.assertEqual("\u0085".strip(), "")
        self.assertFalse(graph_value_absent("\u0085"))
        self.assertFalse(graph_value_absent("\u001c\\N"))


class MiningTests(unittest.TestCase):
    def mine(self, processes, transactions, messages=(), **kwargs):
        return mine_relations(processes, transactions, list(messages), **kwargs)

    def test_two_step_chain_shape(self):
        result = self.mine(*chain(30))
        a, b = (pattern_step_id("IF_A", pid, "DB") for pid in ("IF_A_PR001", "IF_A_PR002"))
        precedes = cand(result, "precedes", "IF_A_PR001", "IF_A_PR002")
        self.assertEqual(precedes["status"], "candidate")
        self.assertEqual(precedes["counts"], {"applicable": 30, "satisfied": 30, "violated": 0, "undetermined": 0})
        self.assertEqual((precedes["patternEdgeId"], precedes["patternEdgeIds"]), (f"precedes:{a}:{b}", [f"precedes:{a}:{b}"]))
        self.assertEqual(precedes["scope"], {"adapterType": "DB"})
        self.assertEqual(precedes["display"]["interfaceId"], "IF_A")
        self.assertEqual(precedes["fromAdapterTypes"], [{"adapterType": "DB", "transactions": 30}])
        handoff = cand(result, "message_handoff", "IF_A_PR001", "IF_A_PR002")
        self.assertEqual((handoff["status"], handoff["match"]),
                         ("candidate", {"from": "OUT", "to": "IN", "key": "message_id"}))
        self.assertEqual((handoff["patternEdgeId"], handoff["pairedPrecedesEdgeId"], handoff["pairApprovable"]),
                         (f"hands_off:{a}:{b}", precedes["patternEdgeId"], True))
        for pid, node in (("IF_A_PR001", a), ("IF_A_PR002", b)):
            required = cand(result, "required_step", pid)
            self.assertEqual((required["to"], required["scope"], required["status"], required["patternNodeIds"]),
                             (None, {"interfaceId": "IF_A"}, "candidate", [node]))
        for item in result["candidates"]:
            self.assertEqual(item["evaluatedAt"], "transaction" if item["kind"] == "required_step" else "successor")
            self.assertNotIn("id", item)
            self.assertNotIn("evaluable", item)
            expected_keys = {"interfaceId"} if item["kind"] == "required_step" else {"adapterType"}
            self.assertTrue(item["scope"] is None or set(item["scope"]) == expected_keys)
        self.assertEqual(result["candidateCounts"]["approvable"], 4)
        self.assertTrue(all(edge["verification"] is None for edge in result["edges"]))
        self.assertEqual({node["kind"] for node in result["nodes"]}, {"pattern_step"})

    def test_three_step_chain(self):
        result = self.mine(*chain(30, ("IF_A_PR001", "IF_A_PR002", "IF_A_PR003")))
        self.assertEqual(sorted(c["from"]["processId"] for c in result["candidates"] if c["kind"] == "required_step"),
                         ["IF_A_PR001", "IF_A_PR002", "IF_A_PR003"])
        self.assertEqual(sorted((c["from"]["processId"], c["to"]["processId"]) for c in result["candidates"]
                                if c["kind"] == "precedes"),
                         [("IF_A_PR001", "IF_A_PR002"), ("IF_A_PR002", "IF_A_PR003")])

    def test_required_step_presence_and_precedes_final_judgement(self):
        processes, transactions, messages = chain(60, ("IF_A_PR001", "IF_A_PR002", "IF_A_PR003"))
        # TX-0000(F 거래): PR001 없이 PR002가 시작 단계로 기록 → required(PR001) step_absent, precedes T
        processes = [row for row in processes if not (row.transaction_id == "TX-0000" and row.process_id == "IF_A_PR001")]
        processes = replace(processes, "TX-0000", "IF_A_PR002", P("TX-0000", "IF_A_PR002", "NONE"))
        transactions = [T("TX-0000", status="F") if row.transaction_id == "TX-0000" else row for row in transactions]
        # TX-0001: PR002가 PR001을 가리키지만 PR001 행이 없음 → required(PR001) step_absent,
        # precedes는 최종 판정(TRANSACTION 행 있음) 기준이라 위반 predecessor_missing(첫 판정의 보류가 아님, 의도된 동작)
        processes = [row for row in processes if not (row.transaction_id == "TX-0001" and row.process_id == "IF_A_PR001")]
        # TX-0002: 의존 값 결측(\N) → precedes는 시작 단계와 구분해 undetermined
        processes = replace(processes, "TX-0002", "IF_A_PR002", P("TX-0002", "IF_A_PR002", " \\N "))
        result = self.mine(processes, transactions, messages)
        required = cand(result, "required_step", "IF_A_PR001")
        self.assertEqual(required["counts"], {"applicable": 60, "satisfied": 58, "violated": 2, "undetermined": 0})
        self.assertEqual((required["status"], required["violationReasons"]), ("review_needed", {"step_absent": 2}))
        self.assertEqual(required["violationsByTransactionStatus"], {"F": 1, "S": 1})
        self.assertEqual(required["counterexampleRefs"][0],
                         {"refs": ["transaction:TX-0000"], "reason": "step_absent", "transactionStatus": "F"})
        precedes = cand(result, "precedes", "IF_A_PR001", "IF_A_PR002")
        self.assertEqual(precedes["counts"], {"applicable": 59, "satisfied": 57, "violated": 2, "undetermined": 1})
        self.assertEqual(precedes["violationReasons"], {"predecessor_missing": 1, "successor_is_start_step": 1})
        self.assertEqual(precedes["undeterminedReasons"], {"depend_process_id_missing": 1})

    def test_unkeyed_rows_still_precede_predecessor_missing(self):
        processes, transactions, messages = chain(30)
        processes = [row for row in processes if not (row.transaction_id == "TX-0000" and row.process_id == "IF_A_PR001")]
        processes.append(P("TX-0000", "  ", "NONE"))  # 키 없는 행이 선행일 수 있다
        precedes = cand(self.mine(processes, transactions, messages), "precedes", "IF_A_PR001", "IF_A_PR002")
        self.assertEqual((precedes["undeterminedReasons"], precedes["violationReasons"]), ({"unkeyed_rows": 1}, {}))

    def test_only_transactions_with_transaction_row_are_counted(self):
        # TRANSACTION 행이 없는 거래(늦게 오는 중일 수 있음)는 세 종류 모두 세지 않는다 — 스냅샷 끝 유예 구간 대신
        base = self.mine(*chain(30))
        processes, transactions, messages = chain(30)
        for index in range(5):
            tx = f"LATE-{index}"  # 선행 행 없음·인계 불일치까지 넣어도 통계에 들어가면 안 된다
            processes.append(P(tx, "IF_A_PR002", "IF_A_PR001"))
            messages += [M(tx, "IF_A_PR001", "A", "OUT"), M(tx, "IF_A_PR002", "B", "IN")]
        result = self.mine(processes, transactions, messages)
        self.assertEqual(result["unassigned"]["transactions"], {"no_transaction_row": 5})
        for kind, frm in (("required_step", "IF_A_PR001"), ("required_step", "IF_A_PR002"),
                          ("precedes", "IF_A_PR001"), ("message_handoff", "IF_A_PR001")):
            self.assertEqual(cand(result, kind, frm)["counts"], cand(base, kind, frm)["counts"], (kind, frm))
        self.assertEqual(cand(result, "required_step", "IF_A_PR001")["counts"]["applicable"], 30)

    def test_dependency_conflict_and_self_dependency(self):
        processes, transactions, messages = chain(32)
        # TX-0000: PR002 재시도 두 시도의 의존 값이 다름 → dependency_conflict(Detector precedes_ambiguous)
        processes.append(P("TX-0000", "IF_A_PR002", "NONE", retry=1))
        # TX-0001: PR002가 자기 자신에 의존 → self_dependency(T)
        processes = replace(processes, "TX-0001", "IF_A_PR002", P("TX-0001", "IF_A_PR002", "IF_A_PR002"))
        result = self.mine(processes, transactions, messages)
        precedes = cand(result, "precedes", "IF_A_PR001", "IF_A_PR002")
        self.assertEqual(precedes["undeterminedReasons"], {"dependency_conflict": 1})
        self.assertEqual(precedes["violationReasons"], {"self_dependency": 1})
        from trainer.relation_candidates import REASON_VOCABULARY
        self.assertEqual(REASON_VOCABULARY["dependency_conflict"], "input_invalid precedes_ambiguous")
        for reason in list(precedes["undeterminedReasons"]) + list(precedes["violationReasons"]):
            self.assertIn(reason, REASON_VOCABULARY)

    def test_from_matched_by_process_id_only(self):
        # PR001이 DB 20건·HTTP 20건, PR002는 늘 PR001에 의존 → 규칙 1건 100%(이전에는 adapter별 50% 간선 2개)
        processes, transactions, messages = chain(40)
        processes = [ProcessRow(r.observation_id, r.execution_id, 1, None, r.transaction_id, r.process_id,
                                r.depend_process_id, "HTTP" if r.process_id == "IF_A_PR001" and
                                int(r.transaction_id[-4:]) % 2 else "DB", 0, r.raw) for r in processes]
        result = self.mine(processes, transactions, messages)
        precedes = cand(result, "precedes", "IF_A_PR001", "IF_A_PR002")
        self.assertEqual((precedes["status"], precedes["counts"]["satisfied"]), ("candidate", 40))
        self.assertEqual(precedes["fromAdapterTypes"], [{"adapterType": "DB", "transactions": 20},
                                                        {"adapterType": "HTTP", "transactions": 20}])
        self.assertEqual(len(precedes["patternEdgeIds"]), 2)
        self.assertEqual(cand(result, "required_step", "IF_A_PR001")["counts"]["satisfied"], 40)
        self.assertEqual(len([edge for edge in result["edges"] if edge["kind"] == "precedes"]), 2)

    def test_null_scope_counts_all_adapters(self):
        processes, transactions, messages = chain(40)
        processes = [ProcessRow(r.observation_id, r.execution_id, 1, None, r.transaction_id, r.process_id,
                                r.depend_process_id, None if r.process_id == "IF_A_PR002" and
                                int(r.transaction_id[-4:]) < 10 else "DB", 0, r.raw) for r in processes]
        result = self.mine(processes, transactions, messages)
        scoped = [c for c in result["candidates"] if c["kind"] == "precedes"]
        by_scope = {json.dumps(c["scope"]): c["counts"]["applicable"] for c in scoped}
        self.assertEqual(by_scope, {"null": 40, json.dumps({"adapterType": "DB"}): 30})

    def test_branch_is_information_only(self):
        p1, t1, m1 = chain(20, ("IF_A_PR001", "IF_A_PR002"), prefix="TA")
        p2, t2, m2 = chain(15, ("IF_A_PR001", "IF_A_PR003"), prefix="TB")
        result = self.mine(p1 + p2, t1 + t2, m1 + m2)
        self.assertNotIn("allowed_branch", {item["kind"] for item in result["candidates"]})
        note = result["relationNotes"][0]
        self.assertEqual((note["kind"], note["ruleCandidate"], note["fromTransactions"]), ("allowed_branch", False, 35))
        self.assertEqual({item["to"]["processId"]: item["observedTransactions"] for item in note["successors"]},
                         {"IF_A_PR002": 20, "IF_A_PR003": 15})
        self.assertEqual(len([edge for edge in result["edges"] if edge["kind"] == "branches_to"]), 2)
        # 후행이 있는 거래에서만 판정하므로 20건, 30건 미만이라 근거 부족이다. 분기 단계는 필수가 아니다(20/35).
        self.assertEqual(cand(result, "precedes", "IF_A_PR001", "IF_A_PR002")["status"], "insufficient_evidence")
        self.assertTrue(no_cand(result, "required_step", "IF_A_PR002"))
        self.assertEqual(cand(result, "required_step", "IF_A_PR001")["status"], "candidate")

    def test_last_step_missing_is_judged(self):
        processes, transactions, messages = chain(100)
        drop = {"TX-0003", "TX-0007"}
        kept = [row for row in processes if not (row.transaction_id in drop and row.process_id == "IF_A_PR002")]
        result = self.mine(kept, transactions, messages)
        last = cand(result, "required_step", "IF_A_PR002")
        self.assertEqual((last["status"], last["approvable"], last["counts"]["violated"]), ("review_needed", True, 2))
        self.assertEqual([item["refs"] for item in last["counterexampleRefs"]],
                         [["transaction:TX-0003"], ["transaction:TX-0007"]])

    def test_retries_counted_once_per_transaction(self):
        processes, transactions, messages = chain(30)
        retries = [P(f"TX-{index:04d}", "IF_A_PR002", "IF_A_PR001", retry=1) for index in range(10)]
        result = self.mine(processes + retries, transactions, messages)
        self.assertEqual(cand(result, "precedes", "IF_A_PR001", "IF_A_PR002")["counts"]["applicable"], 30)
        self.assertEqual(cand(result, "required_step", "IF_A_PR002")["counts"]["satisfied"], 30)

    def test_ambiguous_retry_and_adapter_mismatch_is_not_ambiguity(self):
        processes, transactions, messages = chain(32)
        ambiguous = P("TX-0000", "IF_A_PR002", "IF_A_PR001", retry=0)  # 같은 retry_count 0 두 실행
        conflict = P("TX-0001", "IF_A_PR002", "IF_A_PR001", retry=1, adapter="HTTP")  # 시도 간 adapter 다름
        # 같은 실행의 새 리비전은 중복이 아니다(최신 리비전만 남는다).
        original = next(row for row in processes if row.transaction_id == "TX-0002" and row.process_id == "IF_A_PR002")
        revision = ProcessRow("obs-zzzzzz", original.execution_id, 2, None, "TX-0002", "IF_A_PR002", "IF_A_PR001",
                              "DB", 0, original.raw)
        result = self.mine(processes + [ambiguous, conflict, revision], transactions, messages)
        precedes = cand(result, "precedes", "IF_A_PR001", "IF_A_PR002", scope={"adapterType": "DB"})
        # 재시도 모호만 undetermined. 시도 간 adapter 차이(TX-0001 DB·HTTP)는 모호가 아니라 DB 후보에서 satisfied
        self.assertEqual(precedes["undeterminedReasons"], {"successor_ambiguous_retry": 1})
        self.assertEqual((precedes["counts"]["applicable"], precedes["withinGroupAdapterMismatch"]), (31, {"from": 0, "to": 1}))
        http = cand(result, "precedes", "IF_A_PR001", "IF_A_PR002", scope={"adapterType": "HTTP"})
        self.assertEqual(http["counts"], {"applicable": 1, "satisfied": 1, "violated": 0, "undetermined": 0})
        from trainer.relation_candidates import REASON_VOCABULARY
        self.assertFalse([key for key in REASON_VOCABULARY if key.endswith("_adapter_conflict")])
        # required_step은 묶음이 모호해도 단계가 있으므로 satisfied(아래 전용 시험).
        self.assertEqual(cand(result, "required_step", "IF_A_PR002")["counts"],
                         {"applicable": 32, "satisfied": 32, "violated": 0, "undetermined": 0})
        self.assertEqual(result["excluded"]["supersededRevisions"]["process"], 1)

    def test_required_step_presence_certain_even_when_group_ambiguous(self):
        processes, transactions, messages = chain(30)
        # TX-0000: PR002 재시도 모호(같은 retry_count 두 실행), TX-0001: PR002 시도 간 adapter 차이(모호 아님)
        processes.append(P("TX-0000", "IF_A_PR002", "IF_A_PR001", retry=0))
        processes.append(P("TX-0001", "IF_A_PR002", "IF_A_PR001", retry=1, adapter="HTTP"))
        # TX-0002: PR002 키 있는 행 없음 + PROCESS_ID 없는 행 → 있음을 정할 수 없어 undetermined
        processes = [row for row in processes if not (row.transaction_id == "TX-0002" and row.process_id == "IF_A_PR002")]
        processes.append(P("TX-0002", "\\N", "IF_A_PR001"))
        # TX-0003: PR002 키 있는 행 없음, unkeyed도 없음 → step_absent
        processes = [row for row in processes if not (row.transaction_id == "TX-0003" and row.process_id == "IF_A_PR002")]
        required = cand(self.mine(processes, transactions, messages), "required_step", "IF_A_PR002")
        self.assertEqual(required["counts"], {"applicable": 29, "satisfied": 28, "violated": 1, "undetermined": 1})
        self.assertEqual((required["violationReasons"], required["undeterminedReasons"]),
                         ({"step_absent": 1}, {"unkeyed_rows": 1}))
        # 모든 거래에서 모호하기만 한 단계도 required_step 대상이 된다(있음은 확실).
        only_ambiguous = [P(f"TY-{index:04d}", "IF_A_PR009", "NONE", retry=0) for index in range(30) for _ in range(2)]
        result = self.mine(only_ambiguous, [T(f"TY-{index:04d}") for index in range(30)])
        self.assertEqual(cand(result, "required_step", "IF_A_PR009")["counts"]["satisfied"], 30)

    def test_counterexamples_capped_at_five(self):
        processes, transactions, messages = chain(200)
        drop = {f"TX-{index:04d}" for index in range(0, 140, 20)}  # 7건 누락 → 96.5% 검토 필요
        processes = [row for row in processes if not (row.transaction_id in drop and row.process_id == "IF_A_PR002")]
        required = cand(self.mine(processes, transactions, messages), "required_step", "IF_A_PR002")
        self.assertEqual((required["status"], required["counts"]["violated"], len(required["counterexampleRefs"])),
                         ("review_needed", 7, 5))
        self.assertEqual([item["refs"][0] for item in required["counterexampleRefs"]],
                         [f"transaction:TX-{index:04d}" for index in range(0, 100, 20)])

    def test_minimum_applicable_29_vs_30(self):
        self.assertEqual(cand(self.mine(*chain(29)), "precedes", "IF_A_PR001")["status"], "insufficient_evidence")
        self.assertEqual(cand(self.mine(*chain(30)), "precedes", "IF_A_PR001")["status"], "candidate")

    def test_rate_boundaries(self):
        self.assertEqual(candidate_status(100, 99), "candidate")
        self.assertEqual(candidate_status(100, 98), "review_needed")
        self.assertEqual(candidate_status(100, 95), "review_needed")
        self.assertEqual(candidate_status(100, 94), "not_candidate")
        self.assertEqual(candidate_status(200, 197), "review_needed")  # 98.5%
        self.assertEqual(candidate_status(29, 29), "insufficient_evidence")
        processes, transactions, messages = chain(100)
        changed = replace(processes, "TX-0050", "IF_A_PR002", P("TX-0050", "IF_A_PR002", " NONE "))
        precedes = cand(self.mine(changed, transactions, messages), "precedes", "IF_A_PR001")
        self.assertEqual((precedes["status"], precedes["satisfactionRate"]), ("candidate", 0.99))
        self.assertEqual(precedes["violationReasons"], {"successor_is_start_step": 1})
        self.assertEqual(precedes["counterexampleRefs"][0]["refs"], ["transaction:TX-0050", "process:TX-0050:IF_A_PR002"])

    def test_lowercase_none_dependency_is_a_value(self):
        processes, transactions, messages = chain(30)
        changed = replace(processes, "TX-0000", "IF_A_PR002", P("TX-0000", "IF_A_PR002", "none"))
        precedes = cand(self.mine(changed, transactions, messages), "precedes", "IF_A_PR001")
        self.assertEqual(precedes["violationReasons"], {"other_predecessor": 1})

    def test_exclude_process_ids_and_non_standard_flag(self):
        processes, transactions, messages = chain(30)
        stubs = [P(f"TX-{index:04d}", "IF_A_STUB", "IF_A_PR002") for index in range(3)]
        result = self.mine(processes + stubs, transactions, messages, exclude_process_ids=["IF_A_STUB"])
        self.assertEqual(result["excluded"]["processIds"], {"IF_A_STUB": 3})
        self.assertNotIn("IF_A_STUB", {node["attrs"]["processId"] for node in result["nodes"]})
        result = self.mine(processes + stubs, transactions, messages)
        node = [n for n in result["nodes"] if n["attrs"]["processId"] == "IF_A_STUB"][0]
        self.assertTrue(node["attrs"]["nonStandardProcessId"])
        self.assertTrue(no_cand(result, "required_step", "IF_A_STUB"))  # 3/30 → 후보 아님
        stub_edge = cand(result, "precedes", "IF_A_PR002", "IF_A_STUB")
        self.assertEqual((stub_edge["status"], stub_edge["attrs"]["nonStandardProcessId"], stub_edge["approvable"]),
                         ("insufficient_evidence", True, False))
        self.assertEqual(result["candidateCounts"]["listedNotApprovable"]["insufficientEvidence"], 1)

    def test_message_handoff_match_mismatch_absent(self):
        processes, transactions, _ = chain(40, messages=False)
        messages = []
        for index in range(40):
            tx = f"TX-{index:04d}"
            out_id, in_id = f"M{index}", f"M{index}"
            if index < 1:
                in_id = f"OTHER{index}"  # 불일치
            elif index < 4:
                out_id = " \\N "  # 결측
            messages += [M(tx, "IF_A_PR001", out_id, "OUT"), M(tx, "IF_A_PR002", in_id, "IN")]
        messages.append(M("TX-0005", "  ", "X", "IN"))  # 키 결측 메시지 → unassigned.rows로 센다
        result = self.mine(processes, transactions, messages)
        handoff = cand(result, "message_handoff", "IF_A_PR001")
        self.assertEqual(handoff["counts"], {"applicable": 37, "satisfied": 36, "violated": 1, "undetermined": 3})
        self.assertEqual(handoff["undeterminedReasons"], {"missing_message_id": 3})
        self.assertEqual(handoff["status"], "review_needed")
        self.assertEqual(handoff["counterexampleRefs"][0]["refs"],
                         ["transaction:TX-0000", "process:TX-0000:IF_A_PR002", "process:TX-0000:IF_A_PR001"])
        self.assertEqual(result["unassigned"]["rows"]["messageKeyAbsent"], 1)

    def test_pairing_lists_precedes_even_when_not_candidate(self):
        processes, transactions, messages = chain(100)
        for index in range(10):  # 10건 시작 단계 → precedes 90% (not_candidate), 인계는 충족 90건 전부 일치
            processes = replace(processes, f"TX-{index:04d}", "IF_A_PR002", P(f"TX-{index:04d}", "IF_A_PR002", "NONE"))
        result = self.mine(processes, transactions, messages)
        handoff = cand(result, "message_handoff", "IF_A_PR001")
        precedes = cand(result, "precedes", "IF_A_PR001", "IF_A_PR002")
        self.assertEqual((handoff["status"], precedes["status"], precedes["listedForPairing"], precedes["approvable"]),
                         ("candidate", "not_candidate", True, False))
        self.assertEqual((handoff["pairedPrecedesEdgeId"], handoff["pairApprovable"]), (precedes["patternEdgeId"], False))
        self.assertTrue(handoff["approvable"])  # 짝이 미달이어도 handoff 자신은 승인 가능(경고만)
        self.assertEqual(handoff["scope"], precedes["scope"])
        counts = result["candidateCounts"]
        self.assertEqual(counts["listedNotApprovable"]["notCandidateForPairing"], 1)
        self.assertEqual(counts["approvable"], counts["candidate"] + counts["reviewNeeded"])

    def test_handoff_without_applicable_is_not_listed(self):
        result = self.mine(*chain(30, messages=False))
        self.assertTrue(no_cand(result, "message_handoff", "IF_A_PR001"))
        self.assertEqual(result["notListed"]["messageHandoffNoApplicable"], 1)
        self.assertEqual(cand(result, "precedes", "IF_A_PR001")["status"], "candidate")

    def test_mismatch_suffix_patterns_and_suffix_class_breakdown(self):
        processes, transactions, _ = chain(60, prefix="ORD", messages=False)
        messages = [M(row.transaction_id, row.process_id, "SAME", "OUT" if row.process_id.endswith("1") else "IN")
                    for row in processes]
        for tx in ("SFX-01", "SFX-1234", "SFX-7"):  # 0채움 2자리·4자리·1자리 끝자리의 불일치
            processes += [P(tx, "IF_A_PR001"), P(tx, "IF_A_PR002", "IF_A_PR001")]
            transactions.append(T(tx))
            messages += [M(tx, "IF_A_PR001", "A", "OUT"), M(tx, "IF_A_PR002", "B", "IN")]
        default = cand(self.mine(processes, transactions, messages), "message_handoff", "IF_A_PR001")
        self.assertEqual(default["undeterminedReasons"], {"excluded_transaction_suffix": 1})
        self.assertEqual(default["violationReasons"], {"message_id_mismatch": 2})
        self.assertEqual(default["bySuffixClass"], {
            "one_digit": {"match": 0, "mismatch": 1, "excludedMismatch": 0},
            "zero_padded_two_digit": {"match": 0, "mismatch": 0, "excludedMismatch": 1},
            "four_digit": {"match": 60, "mismatch": 1, "excludedMismatch": 0},
            "other": {"match": 0, "mismatch": 0, "excludedMismatch": 0}})
        off = cand(self.mine(processes, transactions, messages, handoff_mismatch_suffix_patterns=[]),
                   "message_handoff", "IF_A_PR001")
        self.assertEqual((off["violationReasons"], off["undeterminedReasons"]), ({"message_id_mismatch": 3}, {}))
        four = cand(self.mine(processes, transactions, messages, handoff_mismatch_suffix_patterns=[r"-[0-9]{4}\Z"]),
                    "message_handoff", "IF_A_PR001")
        # 일치(ORD-0000 등 4자리)는 제외 패턴과 무관하게 satisfied로 남는다. 불일치에만 적용.
        self.assertEqual((four["counts"]["satisfied"], four["bySuffixClass"]["four_digit"]),
                         (60, {"match": 60, "mismatch": 0, "excludedMismatch": 1}))
        self.assertEqual((suffix_class("X-01\n"), suffix_class("X-0١"), suffix_class("X-12")),
                         ("other", "other", "other"))

    def test_interface_from_transaction_rows_only(self):
        processes, transactions, messages = chain(30)
        processes += [P("NO-T", "IF_A_PR001"), P("CONFLICT", "IF_A_PR001"), P("BLANK", "IF_A_PR001")]
        transactions += [T("CONFLICT", "IF_A"), T("CONFLICT", "IF_B"), T("BLANK", "\\N")]
        result = self.mine(processes, transactions, messages)
        # CONFLICT: 같은 retry_count(0) 실행 2건 + interface 값이 갈림 → 모호, 인터페이스를 정할 수 없어 미배정
        self.assertEqual(result["unassigned"]["transactions"],
                         {"interface_absent": 1, "no_transaction_row": 1, "transaction_row_ambiguous": 1})
        self.assertEqual(result["interfaceTransactions"], {"IF_A": 30})
        self.assertEqual(cand(result, "required_step", "IF_A_PR001")["counts"]["applicable"], 30)

    def test_transaction_retries_use_highest_retry_count(self):
        processes, transactions, messages = chain(34)
        # TX-0000: 재시도 0(F, 다른 interface)·1(S) → 가장 큰 번호 실행(S, IF_A)을 쓴다. 모호 아님
        transactions += [T("TX-0000", "IF_OLD", status="F", retry=0)]
        transactions = [T("TX-0000", status="S", retry=1) if row.transaction_id == "TX-0000" and row.interface_id == "IF_A"
                        else row for row in transactions]
        # TX-0001: 가장 큰 번호(1) 실행 2건 → transaction_row_ambiguous(interface는 IF_A로 모임)
        transactions += [T("TX-0001", retry=1), T("TX-0001", retry=1)]
        # TX-0002: retry_count 숫자 아님, TX-0003: execution id 없는 행 → 모호
        transactions += [T("TX-0002", retry="x")]
        transactions += [T("TX-0003", retry=1, execution=None)]
        # TX-0001은 PR002도 빠졌지만 거래 행이 모호하므로 step_absent가 아니라 undetermined
        processes = [row for row in processes if not (row.transaction_id == "TX-0001" and row.process_id == "IF_A_PR002")]
        result = self.mine(processes, transactions, messages)
        self.assertEqual(result["interfaceTransactions"], {"IF_A": 34})
        from trainer.relation_candidates import latest_per_execution, resolve_transactions
        resolved = resolve_transactions(latest_per_execution(transactions)[0])
        self.assertEqual((resolved["TX-0000"]["status"], resolved["TX-0000"]["interfaceId"],
                          resolved["TX-0000"]["rowAmbiguous"]), ("S", "IF_A", False))  # 선택된 retry 1 실행의 값
        self.assertEqual((resolved["TX-0001"]["status"], resolved["TX-0001"]["rowAmbiguous"]), ("ambiguous", True))
        required = cand(result, "required_step", "IF_A_PR002")
        self.assertEqual(required["counts"], {"applicable": 31, "satisfied": 31, "violated": 0, "undetermined": 3})
        self.assertEqual(required["undeterminedReasons"], {"transaction_row_ambiguous": 3})
        # 한 행뿐인 거래는 execution id·retry_count와 무관하게 그 행을 쓴다
        single = self.mine(*chain(30)[:1], [T(f"TX-{i:04d}", retry=None, execution=None) for i in range(30)])
        self.assertEqual(single["interfaceTransactions"], {"IF_A": 30})

    def test_unkeyed_process_row_makes_missing_step_undetermined(self):
        processes, transactions, messages = chain(30)
        processes = [row for row in processes if not (row.transaction_id == "TX-0000" and row.process_id == "IF_A_PR002")]
        processes.append(P("TX-0000", "  ", "IF_A_PR001"))
        result = self.mine(processes, transactions, messages)
        last = cand(result, "required_step", "IF_A_PR002")
        self.assertEqual((last["undeterminedReasons"], last["counts"]["violated"]), ({"unkeyed_rows": 1}, 0))
        self.assertEqual(result["unassigned"]["rows"]["unkeyedProcessRows"], 1)

    def test_adapter_absent_gives_empty_last_component_and_null_scope(self):
        processes, transactions, messages = chain(30)
        processes = [ProcessRow(r.observation_id, r.execution_id, 1, None, r.transaction_id, r.process_id,
                                r.depend_process_id, None, 0, r.raw) for r in processes]
        result = self.mine(processes, transactions, messages)
        self.assertEqual(sorted(node["id"] for node in result["nodes"]),
                         ["pattern_step:IF_A:IF_A_PR001:", "pattern_step:IF_A:IF_A_PR002:"])
        self.assertIsNone(cand(result, "precedes", "IF_A_PR001")["scope"])


class ThresholdTests(unittest.TestCase):
    def rows(self, count, pid="IF_A_PR001", adapter="DB", *, tx_prefix="TX"):
        rows = []
        for index in range(count):
            millis = 100 + index * 10
            rows.append(P(f"{tx_prefix}-{index:04d}", pid, adapter=adapter,
                          end=f"2026-01-01T00:00:{millis // 1000:02d}.{millis % 1000:03d}+09:00"))
        return rows

    def test_scope_omits_null_adapter(self):
        items = threshold_candidates(self.rows(30, adapter=None), {})
        self.assertEqual(items[0]["scope"], {"processId": "IF_A_PR001"})

    def test_p95_matches_existing_basis_and_support_rule(self):
        rows = self.rows(30) + self.rows(29, "IF_A_PR002")
        rows.append(P("TX-9999", "IF_A_PR001", end=" \\N "))  # END_TIME 없음 → 건너뜀
        rows.append(P("TX-9998", "IF_A_PR001", status="F"))  # 기존 추천과 같이 status=S만
        interfaces = {f"TX-{index:04d}": "IF_A" for index in range(30)}
        items = threshold_candidates(rows, interfaces)
        first = [item for item in items if item["scope"] == {"processId": "IF_A_PR001", "adapterType": "DB"}][0]
        expected = float(np.quantile([100 + index * 10 for index in range(30)], .95))
        self.assertEqual((first["status"], first["evidence"]["n"], first["evidence"]["p95"]), ("candidate", 30, expected))
        self.assertEqual((first["operator"], first["threshold"], first["displayThreshold"]), ("gt", "375.5", "376"))
        self.assertEqual((first["evidence"]["rows"], first["evidence"]["referenceRows"],
                          first["evidence"]["endTimeMissingReferenceRows"]), (32, 31, 1))
        self.assertIn("END_TIME", first["limitation"])
        # 화면 문장에는 내부 경로·식별자·수식이 없고, 근거는 limitationRefs로 옮겨졌다.
        for token in (".py", "=", "sourceMissingPolicy"):
            self.assertNotIn(token, first["limitation"])
        self.assertIn("elapsed_ms = END_TIME - START_TIME", first["limitationRefs"])
        self.assertTrue(any("methods/registry.py" in ref and "sourceMissingPolicy END_TIME=not_applicable" in ref
                            for ref in first["limitationRefs"]))
        self.assertEqual(first["display"], {"interfaceIds": ["IF_A"], "patternStepIds": ["pattern_step:IF_A:IF_A_PR001:DB"]})
        second = [item for item in items if item["scope"]["processId"] == "IF_A_PR002"][0]
        self.assertEqual((second["status"], second["threshold"]), ("insufficient_evidence", None))

    def test_scope_shared_when_process_id_repeats_across_interfaces(self):
        rows = self.rows(15, tx_prefix="TA") + self.rows(15, tx_prefix="TB")
        interfaces = {**{f"TA-{index:04d}": "IF_A" for index in range(15)},
                      **{f"TB-{index:04d}": "IF_B" for index in range(15)}}
        item = threshold_candidates(rows, interfaces)[0]
        self.assertTrue(item["scopeSharedAcrossInterfaces"])
        self.assertEqual(item["display"]["interfaceIds"], ["IF_A", "IF_B"])


class JobShapeTests(unittest.TestCase):
    def test_parse_input(self):
        snapshot = str(uuid.uuid4())
        parsed = parse_input({"kind": "recommend_relations", "snapshotId": snapshot})
        self.assertEqual((parsed["excludeProcessIds"], parsed["handoffMismatchSuffixPatterns"]), ([], [r"-0[0-9]\Z"]))
        self.assertEqual(parse_input({"snapshotId": snapshot, "handoffMismatchSuffixPatterns": []})
                         ["handoffMismatchSuffixPatterns"], [])
        for bad in ({}, {"snapshotId": "x"}, {"snapshotId": snapshot, "excludeProcessIds": "A"},
                    {"snapshotId": snapshot, "excludeProcessIds": ["A", "A"]}, {"snapshotId": snapshot, "other": 1},
                    {"snapshotId": snapshot, "handoffMismatchSuffixPatterns": "-0[0-9]"},
                    {"snapshotId": snapshot, "handoffMismatchSuffixPatterns": ["("]},
                    {"snapshotId": snapshot, "handoffMismatchSuffixPatterns": [["a"]]},
                    {"snapshotId": snapshot, "handoffMismatchSuffixPatterns": [1]},
                    {"snapshotId": snapshot, "handoffMismatchSuffixPatterns": [f"-{i}" for i in range(11)]},
                    {"snapshotId": snapshot, "handoffMismatchSuffixPatterns": ["-1", "-1"]},
                    {"snapshotId": snapshot, "handoffMismatchSuffixPatterns": ["x" * 101]},
                    {"snapshotId": snapshot, "handoffSuffixExclusion": True}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "relation_input_invalid"):
                parse_input(bad)

    def test_build_result_shape_and_split_exclusion(self):
        processes, transactions, messages = chain(31)
        # TX-0030의 후행 단계가 validation 분할에 있으면 거래 전체를 관계 집계에서 뺀다.
        processes = [row if not (row.transaction_id == "TX-0030" and row.process_id == "IF_A_PR002")
                     else ProcessRow(row.observation_id, row.execution_id, 1, None, "TX-0030", "IF_A_PR002",
                                     "IF_A_PR001", "DB", 0, row.raw, None, "validation") for row in processes]
        processes.append(P("TX-0001", None, "IF_A_PR001"))  # PROCESS_ID 없음 → elapsed 통계에서 빠진 수
        loaded = {"sourceId": uuid.uuid4(), "selection": {"from": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z",
                                                         "timeBasis": "persisted_at"},
                  "processes": processes, "transactions": transactions, "messages": messages}
        parsed = parse_input({"snapshotId": str(uuid.uuid4())})
        result = build_result(loaded, parsed, job_id="job-1",
                              generated_at=datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc))
        json.dumps(result)
        header = result["graph"]["graph"]
        self.assertEqual(set(header), {"layer", "id", "version", "sourceId", "snapshotId", "period", "ruleSetRevision",
                                       "generatedAt"})
        self.assertEqual((header["layer"], header["generatedAt"]), ("pattern", "2026-11-12T08:00:00.000Z"))
        self.assertEqual(result["excluded"]["transactions"], {"cross_split": 1})
        self.assertEqual(result["data"]["interfaceTransactions"], {"IF_A": 30})
        self.assertEqual(result["unassigned"]["rows"]["elapsedProcessIdAbsent"], 1)
        self.assertEqual(result["parameters"]["handoffMismatchSuffixPatterns"], [r"-0[0-9]\Z"])
        for key in ("pairingPolicy", "scopeNullPolicy", "conservativeNote", "reasonVocabulary", "transactionStatusRule",
                    "judgementBasis", "adapterPolicy", "transactionRowRule", "requiredStepScopePolicy",
                    "suffixPatternLimitNote", "withinGroupAdapterMismatchNote"):
            self.assertIn(key, result["parameters"])
        self.assertEqual(result["parameters"]["evaluatedAt"]["required_step"], "transaction")
        self.assertEqual(cand(result, "required_step", "IF_A_PR001")["source"]["patternNodeIds"],
                         ["pattern_step:IF_A:IF_A_PR001:DB"])
        precedes = cand(result, "precedes", "IF_A_PR001")
        self.assertEqual(precedes["source"]["jobId"], "job-1")
        self.assertEqual(precedes["source"]["counts"], precedes["counts"])
        self.assertIn("thresholdCandidateCount", result["counts"])
        self.assertEqual(result["counts"]["approvable"], result["candidateCounts"]["approvable"])


class SyntheticNoneStartStepFinding(unittest.TestCase):
    """synthetic_generation.py:102는 depend_process_id `None`만 시작 단계로 본다. 수집 경로에는 'NONE' 문자열이 남는다.

    'NONE' 키는 dependency_limits에 들어가지만 그 키를 찾는 쪽은 `process_id`이므로 PROCESS_ID가 정확히 'NONE'인
    행이 없으면 결과가 같다. 이 시험은 그 판단을 고정한다(변경 없음 확인용).
    """

    def test_none_string_dependency_does_not_change_generation(self):
        from test_synthetic_evaluation import fixture
        from trainer.synthetic_generation import fit_generator, generate
        from trainer.synthetic_profiles import PROFILES
        profile = copy.deepcopy(PROFILES["esb-process-research-v1"])
        fit = fit_generator(fixture(split="train"))
        for scenario in ("P02", "P03", "P04"):
            base = fixture()
            with_string = copy.deepcopy(base)
            for row in with_string:
                row["raw"]["depend_process_id"] = "NONE"
            first = generate(base, fit, profile, scenario=scenario, severity=1, seed=11, target_rows=3)
            second = generate(with_string, fit, profile, scenario=scenario, severity=1, seed=11, target_rows=3)
            for key in ("status", "generatedCount", "rejects", "truth"):
                self.assertEqual(first[key], second[key], (scenario, key))
            self.assertEqual([change["rowId"] for change in first["changes"]],
                             [change["rowId"] for change in second["changes"]])


if __name__ == "__main__":
    unittest.main()

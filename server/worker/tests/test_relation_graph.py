"""층 A 그래프 Python 구현(detector/relation_graph.py)이 계약 벡터·TS 구현과 같은 결과를 내는지 본다. DB 없음."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from detector import relation_graph as graph  # noqa: E402
import relation_graph_cases  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def build_id(kind, c):
    return {"transaction": lambda: graph.transaction_id(c["transactionId"]),
            "process": lambda: graph.process_id(c["transactionId"], c["processId"]),
            "message": lambda: graph.message_id(c["executionId"]),
            "unkeyed": lambda: graph.unkeyed_id(c["observationId"]),
            "missing": lambda: graph.missing_id(c["referencingNodeId"], c["processId"]),
            "contains": lambda: graph.edge_id("contains", c["from"], c["to"]),
            "precedes": lambda: graph.edge_id("precedes", c["from"], c["to"]),
            "hands_off": lambda: graph.hands_off_id(c["from"], c["to"], c["messageNodeId"])}[kind]()


def python_graph(item):
    data = item["input"]
    return graph.build_instance_graph(
        source_id=data["sourceId"], transaction=data["transaction"], processes=data["processes"],
        messages=data["messages"], processes_truncated=data["processesTruncated"],
        messages_truncated=data["messagesTruncated"], generated_at=data["generatedAt"])


class SharedVectors(unittest.TestCase):
    def test_layer_a_id_and_absence_vectors(self):
        data = json.loads((FIXTURES / "process-graph-id-vectors.json").read_text(encoding="utf-8"))
        source = FIXTURES.parents[2] / "contracts" / "process-graph-id-vectors.json"
        if source.is_file():  # 사본이 계약 원본과 같은 판인지(줄 끝 LF 기준) — test_relation_candidates와 같은 방식
            self.assertEqual(data["_fixtureSource"]["sha256"],
                             hashlib.sha256(source.read_bytes().replace(b"\r\n", b"\n")).hexdigest())
        self.assertEqual((len(data["idVectors"]), len(data["absenceVectors"])), (17, 26))
        for vector in data["idVectors"]:
            with self.subTest(vector["case"]):
                self.assertEqual(build_id(vector["kind"], vector["components"]), vector["expectedId"])
        for vector in data["absenceVectors"]:
            with self.subTest(vector["case"]):
                self.assertEqual(graph.graph_value_absent(vector["value"], vector.get("field")), vector["absent"])
                if not vector.get("field"):
                    self.assertEqual(graph.graph_value_absent(vector["value"], "PROCESS_ID"), vector["absent"])
        self.assertEqual(graph.escape_id_component("%3A:"), "%253A%3A")

    def test_retry_number_assumes_numeric_text(self):
        # numeric ::text 입력에서는 JS Number와 같다. 그 밖의 문자열은 전제 밖이고 차이를 여기 남겨 둔다.
        self.assertEqual([graph.retry_number(v) for v in ("0", "2", "1.50", "-1", None, " ")],
                         [0.0, 2.0, 1.5, -1.0, None, None])
        self.assertIsNone(graph.retry_number("0x10"))  # JS Number("0x10") = 16
        self.assertEqual(graph.retry_number("1_000"), 1000.0)  # JS Number("1_000") = NaN
        # numeric 특수값 ::text — 두 쪽 모두 유한값이 아니라 없음(JS Number도 NaN·Infinity → null).
        self.assertEqual([graph.retry_number(v) for v in ("NaN", "Infinity", "-Infinity")], [None, None, None])

    def test_suffix_rule_matches_js_semantics(self):
        rule = graph.EXCLUDED_TRANSACTION_SUFFIX
        self.assertEqual([bool(rule.search(v)) for v in ("TX-01", "TX-09", "TX-1", "TX-01\n", "TX-0\u0663",
                                                         "TX-01 ")], [True, True, False, False, False, False])

    def test_trim_is_not_python_strip(self):
        self.assertEqual("\u0085".strip(), "")
        self.assertFalse(graph.graph_value_absent("\u0085"))
        self.assertFalse(graph.graph_value_absent("NONE\u001c", graph.DEPEND_PROCESS_ID))
        self.assertTrue(graph.graph_value_absent("\tNONE\r\n", graph.DEPEND_PROCESS_ID))


class TypeScriptAgreement(unittest.TestCase):
    """기대값은 TS buildInstanceGraph·pickTransactionRow 출력 저장본(relation_graph_cases.py)."""

    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((FIXTURES / "relation-graph-cases.json").read_text(encoding="utf-8"))

    def test_stored_cases_match_python(self):
        self.assertEqual([item["case"] for item in self.data["cases"]],
                         [item["case"] for item in relation_graph_cases.CASES])
        for item in self.data["cases"]:
            with self.subTest(item["case"]):
                self.assertEqual(item["input"], json.loads(json.dumps(
                    next(c for c in relation_graph_cases.CASES if c["case"] == item["case"])["input"])))
                actual = python_graph(item)
                self.assertEqual(json.dumps(actual, ensure_ascii=False, sort_keys=True),
                                 json.dumps(item["expected"], ensure_ascii=False, sort_keys=True))

    def test_pick_transaction_row_cases(self):
        for item in self.data["pickCases"]:
            with self.subTest(item["case"]):
                data = item["input"]
                row, ambiguous = graph.pick_transaction_row(data["executions"], data["missingExecutionId"],
                                                            data["missingRetryCount"], data["top"])
                self.assertEqual({"row": row, "ambiguous": ambiguous}, item["expected"])

    def test_cases_cover_each_graph_outcome(self):
        edges = [edge for item in self.data["cases"] for edge in item["expected"]["edges"]]
        handoffs = {(edge["evidence"] or {}).get("reason") for edge in edges if edge["kind"] == "precedes"}
        results = {edge["evidence"]["result"] for edge in edges if edge["kind"] == "hands_off"}
        reasons = {edge["evidence"]["reason"] for edge in edges if edge["kind"] == "hands_off"}
        verifications = {edge["verification"] for edge in edges if edge["kind"] == "precedes"}
        missing = {node["attrs"]["reason"] for item in self.data["cases"] for node in item["expected"]["nodes"]
                   if node["kind"] == "missing"}
        self.assertEqual(verifications, {"verified", "unmatched", "ambiguous"})
        self.assertLessEqual({"precedes_not_verified", "messages_truncated", "no_out_message"}, handoffs)
        self.assertEqual(results, {"match", "mismatch", "undetermined"})
        self.assertEqual(reasons, {None, "no_in_message", "multiple_messages", "missing_message_id",
                                   "excluded_transaction_suffix"})
        self.assertEqual(missing, {"not_logged", "not_loaded"})

    def test_typescript_source_is_the_recorded_one(self):
        if not relation_graph_cases.TS_SOURCE.is_file():
            self.skipTest("백엔드 원본 없음(작업자 이미지)")
        self.assertEqual(self.data["_fixtureSource"]["sha256"], relation_graph_cases.ts_sha256(),
                         "processGraph.ts가 바뀌었다 — relation_graph_cases.py --write로 다시 만들고 Python 구현을 맞춘다")

    @unittest.skipUnless(shutil.which("node") and relation_graph_cases.TS_SOURCE.is_file(), "node·TS 원본 필요")
    def test_live_typescript_output_matches_stored(self):
        live = relation_graph_cases.generate()
        self.assertEqual(live["cases"], self.data["cases"])
        self.assertEqual(live["pickCases"], self.data["pickCases"])


if __name__ == "__main__":
    unittest.main()

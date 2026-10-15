"""GPU를 호출하지 않는 생성 계약 시험용 응답. 실제 생성 품질 시험이 아니다."""
from __future__ import annotations
import json
from pathlib import Path
from generation_service import serve
from research_bundle import policy


class ContractFixture:
    def __init__(self, bundle):
        self.bundle = Path(bundle)
        self.manifest = json.loads((self.bundle / "design/manifest.json").read_text())

    def generate(self, kind, request):
        if kind == "feature":
            columns = [{"op": "column", "table": "EC_PROCESS_MONITORING", "column": name}
                       for name in ("ERROR_COUNT", "TOTAL_COUNT")]
            candidate = {"id": "fixture_error_ratio", "purpose": "Error count divided by total count",
                "meaning": "Process error count divided by total count",
                "sources": {"columns": [["EC_PROCESS_MONITORING", name] for name in ("ERROR_COUNT", "TOTAL_COUNT")],
                            "relationships": []}, "expected_type": "number", "expected_unit": "ratio",
                "availability": "offline_captured_scope", "required_evidence": ["Operational usefulness review"],
                "plan": {"id": "fixture_error_ratio", "description": "Error count divided by total count",
                         "expression": {"op": "divide", "args": columns},
                         "grain": {"table": "EC_PROCESS_MONITORING", "keys": ["OBSERVATION_ID"]},
                         "population": {"table": "EC_PROCESS_MONITORING", "filter": None}, "group_by": [],
                         "schema_version": request["schema_version"], "source_version": request["source_version"],
                         "missing_policy": {"input": "propagate", "empty_group": None,
                            "denominator_zero": "missing", "child_missing": "propagate", "unknown_predicate": "defer"}}}
            output = {"candidates": [candidate], "stop_reason": "Contract fixture only"}
        else:
            output = policy(self.bundle).expected_target(request)
        return {"output": output, "truncated": False,
                "adapterSha": self.manifest["adapters"]["feature_v5" if kind == "feature" else "rule_v2"]["sha256"]}

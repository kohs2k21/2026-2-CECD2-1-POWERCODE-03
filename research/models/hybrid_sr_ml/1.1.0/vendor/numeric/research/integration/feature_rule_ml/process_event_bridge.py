"""Join verified HR/SR and frozen PR98 ML by the same original Process rows."""
from collections import Counter
import csv
import json
import os
from pathlib import Path
import subprocess
import time

from .common import (RESEARCH, InputFailure, UNASSESSED, combine, digest, json_key,
                     load_pack, new_dir, pin, read, seal, sha, write)
from .runner import rule_result, role_result

FIELDS = ("transactionId", "processId", "adapterType", "startTime", "endTime", "totalCount",
          "availableTime", "event_row_id", "successCount", "errorCount", "retryCount", "status",
          "dependProcessId", "processHubId", "channelId", "responseCode", "source_period")
PACKAGE = RESEARCH / "process_event_model"


def read_processes(path, field_map=None):
    path = Path(path)
    source_sha = sha(path)
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            raw = list(csv.DictReader(stream))
    elif path.suffix.lower() in (".jsonl", ".ndjson"):
        raw = [json.loads(s) for s in path.read_text(encoding="utf-8-sig").splitlines() if s.strip()]
    else:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, list) or not raw or any(not isinstance(r, dict) for r in raw):
        raise InputFailure("Nonempty array of Process records required")
    mapping = field_map if field_map is not None else {name: name for name in FIELDS}
    if not isinstance(mapping, dict) or set(mapping) - set(FIELDS) or any(not isinstance(v, str) for v in mapping.values()):
        raise InputFailure("Field map contains unsupported canonical fields")
    if len(set(mapping.values())) != len(mapping):
        raise InputFailure("One source field cannot stand for multiple Process fields")
    import re
    if any(re.search(r"body|payload", name, re.I) for name in mapping.values()):
        raise InputFailure("Body/payload fields cannot enter the Process adapter")
    if sha(path) != source_sha:
        raise InputFailure("Process source changed while being read")
    rows = []
    for i, original in enumerate(raw):
        row = {name: original[source] for name, source in mapping.items() if source in original}
        supplied = row.get("event_row_id")
        if supplied is not None and str(supplied).strip() == "":
            raise InputFailure("Empty explicit event_row_id")
        row["event_row_id"] = str(supplied) if supplied is not None else digest({"source": source_sha, "row": i})
        rows.append(row)
    if len({r["event_row_id"] for r in rows}) != len(rows):
        raise InputFailure("Duplicate event_row_id")
    return rows, mapping


def bind_rules(prepared, group_id, key_fields, rows, source_sha, field_map):
    if prepared is None:
        if group_id or key_fields:
            raise InputFailure("Rule binding requires --prepared")
        return None, {}
    pack = load_pack(prepared)
    matches = [g for g in pack["groups"] if g["id"] == group_id]
    if len(matches) != 1:
        raise InputFailure("Select exactly one explicit Process rule group")
    group = matches[0]
    keys = group["grain"]["keys"]
    if not key_fields or len(key_fields) != len(keys) or len(set(key_fields)) != len(key_fields):
        raise InputFailure("Explicit one-to-one Process source-row key fields required")
    if any(k not in FIELDS for k in key_fields):
        raise InputFailure("Unsupported Process key binding")
    if any(field_map.get(k) != original for k, original in zip(key_fields, keys)):
        raise InputFailure("Rule grain keys differ from the explicit physical field mapping")
    pins = [p for p in group["source_pins"] if p["table"] == group["grain"]["table"]]
    if len(pins) != 1 or pins[0]["sha256"] != source_sha:
        raise InputFailure("Rule features and ML Process rows must use the identical pinned source file")
    actual = [json_key([r.get(k) for k in key_fields]) for r in rows]
    expected = {json_key(r["source_row_key"]): r for r in group["rows"]}
    if len(set(actual)) != len(actual) or len(expected) != len(group["rows"]) or set(actual) != set(expected):
        raise InputFailure("Process rule/ML row keys are not an exact one-to-one match")
    return group, {r["event_row_id"]: expected[k] for r, k in zip(rows, actual)}


def predict_process(processes, output, runtime_python, prepared=None, rule_group=None,
                    row_key_fields=None, field_map=None, trusted=False, decision="anomaly"):
    if not trusted:
        raise InputFailure("Explicit --trust-local-model required for the pinned PR98 joblib")
    if decision not in ("anomaly", "confirmed_anomaly"):
        raise InputFailure("Unknown frozen ML decision field")
    started = time.perf_counter()
    source_pin = pin(processes)
    records, mapping = read_processes(processes, field_map)
    if sha(processes) != source_pin["sha256"]:
        raise InputFailure("Process source changed before binding")
    group, bound = bind_rules(prepared, rule_group, row_key_fields, records, source_pin["sha256"], mapping)
    lock = read(Path(__file__).with_name("process_event_source.json"))
    out = new_dir(output)
    request = {"package": str(PACKAGE), "source_lock": lock, "records": records, "decision": decision}
    write(out / "ml_request.json", request)
    command = [str(Path(runtime_python).absolute()), "-B", str(Path(__file__).with_name("process_event_worker.py")),
               "--request", str(out / "ml_request.json"), "--output", str(out / "ml_result.json")]
    env = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(name, None)
    env.update(PYTHONDONTWRITEBYTECODE="1", CUDA_VISIBLE_DEVICES="", OMP_NUM_THREADS="1",
               OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    failure = None
    try:
        run = subprocess.run(command, cwd=PACKAGE, env=env, capture_output=True, text=True, timeout=600)
        (out / "ml_stdout.txt").write_text(run.stdout)
        (out / "ml_stderr.txt").write_text(run.stderr)
        ml = read(out / "ml_result.json") if (out / "ml_result.json").is_file() else {}
        if run.returncode or ml.get("error"):
            failure = ml.get("error", "ML runtime exit " + str(run.returncode))
    except (OSError, subprocess.TimeoutExpired) as exc:
        ml, failure = {}, type(exc).__name__ + ": " + str(exc)
    scored = {r["event_row_id"]: r for r in ml.get("rows", [])}
    if not failure and (len(scored) != len(records) or set(scored) != {r["event_row_id"] for r in records}):
        failure = "ML worker returned missing/duplicate/extra Process identities"
    columns = {c["name"]: c for c in group["columns"]} if group else {}
    results = []
    for record in records:
        rid = record["event_row_id"]
        rule_row = bound.get(rid)
        rules = [rule_result(rule_row, rule, columns) for rule in group["rules"]] if group else []
        hr, sr = (role_result(rules, group["pending"] if group else [], k) for k in ("HR", "SR"))
        peer = scored.get(rid, {}) if not failure else {}
        result = {"event_row_id": rid,
                  "source_row_key": rule_row["source_row_key"] if rule_row else None,
                  "transactionId": record.get("transactionId"), "processId": record.get("processId"),
                  "features": rule_row["features"] if rule_row else {},
                  "ml_features": peer.get("ml_features"), "HR": hr, "SR": sr, "rules": rules,
                  "ML": peer.get("ML", {"state": UNASSESSED,
                                        "methods": {a: {"state": UNASSESSED, "score": None,
                                                        "threshold": None, "reason": failure or "Process not scored"}
                                                    for a in ("if", "kmeans", "lof")},
                                        "reason": failure or "Process not scored"}),
                  "input_error": peer.get("input_error")}
        result["OR"] = combine([hr["state"], sr["state"], result["ML"]["state"]])
        results.append(result)
    summary = {k: dict(Counter(r[k] if k == "OR" else r[k]["state"] for r in results))
               for k in ("HR", "SR", "ML", "OR")}
    seconds = time.perf_counter() - started
    result = {"contract": "process-event-bridge1.0", "source": source_pin,
              "bridge_runtime": {"bridge_sha256": sha(Path(__file__)),
                                 "worker_sha256": sha(Path(__file__).with_name("process_event_worker.py")),
                                 "source_lock_sha256": sha(Path(__file__).with_name("process_event_source.json"))},
              "mode": "offline_completed_process_replay", "source_pr": lock,
              "prepared": pin(Path(prepared) / "prepared.json") if prepared else None,
              "rule_group": rule_group, "row_key_fields": row_key_fields, "field_map": mapping,
              "ml_decision": decision, "runtime_failure": failure,
              "runtime": {k: v for k, v in ml.items() if k != "rows"},
              "feature_definitions": group["columns"] if group else [],
              "rows": results, "summary": summary, "new_ML_fit": False,
              "new_LLM_calls": 0, "new_rule_generation": False, "operational_approval": False,
              "same_original_Process_rows": True, "arbitrary_LLM_features_added_to_frozen_ML": False,
              "seconds": seconds, "batch_rows_per_second": len(results) / seconds,
              "not_operational_latency_or_detection_accuracy": True}
    write(out / "decisions.json", result)
    lines = ["# 최신 Process ML + HR/SR 연결 결과", "",
             "같은 원천 Process 행으로 결과를 연결한다. ML은 원래 동결 피처·기준선을 유지한다.",
             "LLM 명세 피처와 ML 내부 피처를 같은 벡터라고 주장하지 않는다.", "",
             "| 방법 | 경고 | 비경고 | 평가 불가 |", "|---|---:|---:|---:|"]
    for k in ("HR", "SR", "ML", "OR"):
        lines.append(f"| {k} | {summary[k].get('warning', 0)} | {summary[k].get('nonwarning', 0)} | {summary[k].get(UNASSESSED, 0)} |")
    lines += ["", "동결 규칙이 없으면 해당 HR/SR은 평가 불가다. 미평가를 정상으로 바꾸지 않는다.",
              "입력 중단 경보·운영 배포·탐지 정확도 평가는 포함하지 않는다.",
              "ML runtime failure: " + str(failure)]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    seal(out, "process_event_detection_result")
    return result

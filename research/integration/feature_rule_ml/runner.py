"""Parallel logical HR/SR/ML decisions on the SAME source-row feature cells."""
from collections import Counter
from decimal import Decimal
from pathlib import Path
import time

from .common import (NONWARNING, UNASSESSED, WARNING, combine, digest, load_pack,
                     new_dir, pin, seal, write)
from .ml_bridge import load_model, score_group
from specmodel.rule_logic import value_condition


def rule_result(row, rule, columns):
    cell = row["features"][rule["column"]]
    base = {"id": rule["id"], "kind": rule["kind"],
            "role": rule["decision"]["request"], "feature": rule["column"],
            "condition": rule["decision"]["condition"],
            "source_binding": rule["source_binding"], "operational_approval": False}
    if cell["state"] == "not_selected":
        return dict(base, state=UNASSESSED, reason="outside_feature_population", applicable=False)
    value = cell.get("value")
    if value is not None and columns[rule["column"]]["dtype"] in ("integer", "number"):
        value = Decimal(str(value))
    outcome = value_condition(value, base["condition"])
    if outcome is None:
        return dict(base, state=UNASSESSED, reason="feature_unavailable", applicable=True)
    return dict(base, state=WARNING if outcome else NONWARNING, reason=None, applicable=True)


def role_result(rules, pending, kind):
    applicable = [r for r in rules if r["kind"] == kind and r["applicable"]]
    unresolved = [p for p in pending if ("HR" if p["decision"]["request"] == "hr" else "SR") == kind]
    states = [r["state"] for r in applicable] + ([UNASSESSED] if unresolved else [])
    return {"state": combine(states), "evaluated_rules": len(applicable),
            "fired_rule_ids": [r["id"] for r in applicable if r["state"] == WARNING],
            "pending_decisions": len(unresolved),
            "reason": "no_applicable_validated_rules" if not states else None}


def predict(prepared, output, models=(), trusted=False):
    started = time.perf_counter()
    pack = load_pack(prepared)
    loaded = [(*load_model(p, trusted=trusted), pin(Path(p) / "model.json")) for p in models]
    result_groups, model_usage = [], set()
    for g in pack["groups"]:
        cols = {x["name"]: x for x in g["columns"]}
        ml_scores = {}
        for n, (meta, model, receipt) in enumerate(loaded):
            if meta["group"] != g["id"]:
                continue
            label = meta["algorithm"]
            if label in ml_scores:
                raise ValueError("One frozen model per algorithm per grain required")
            scores = score_group(g, meta, model)
            ml_scores[label] = {"rows": scores, "version": receipt,
                                "score_direction": meta["score_direction"]}
            model_usage.add(n)
        rows = []
        for i, row in enumerate(g["rows"]):
            rules = [rule_result(row, r, cols) for r in g["rules"]]
            hr, sr = (role_result(rules, g["pending"], k) for k in ("HR", "SR"))
            peers = {name: dict(x["rows"][i], version=x["version"], score_direction=x["score_direction"])
                     for name, x in ml_scores.items()}
            ml = {"state": combine(x["state"] for x in peers.values()), "methods": peers,
                  "reason": None if peers else "no_compatible_frozen_row_model"}
            rows.append({"source_row_key": row["source_row_key"], "features": row["features"],
                         "HR": hr, "SR": sr, "ML": ml, "rules": rules,
                         "OR": combine([hr["state"], sr["state"], ml["state"]])})
        summary = {kind: dict(Counter(r[kind]["state"] if kind != "OR" else r[kind] for r in rows))
                   for kind in ("HR", "SR", "ML", "OR")}
        result_groups.append({"group": g["id"], "grain": g["grain"],
                              "source_version": g["source_version"], "schema_version": g["schema_version"],
                              "feature_definitions": g["columns"], "measurements": g["measurements"],
                              "rows": rows, "summary": summary})
    if len(model_usage) != len(loaded):
        raise ValueError("A supplied model has no matching grain; it was not silently ignored")
    seconds = time.perf_counter() - started
    total = sum(len(g["rows"]) for g in result_groups)
    result = {"mode": "offline_research_detection", "operational_approval": False,
              "new_LLM_calls": 0, "new_ML_fit": False, "new_rule_generation": False,
              "input": pin(Path(prepared) / "prepared.json"), "groups": result_groups,
              "held_cases": pack["holds"], "seconds": seconds,
              "source_rows_across_separate_grains": total,
              "batch_rows_per_second": total / seconds if seconds else None,
              "not_unique_transaction_count": True,
              "no_operational_precision_recall_or_latency_claim": True}
    out = new_dir(output)
    write(out / "decisions.json", result)
    lines = ["# 피처·Rule·ML 통합 실행 결과", "",
             "공개/승인된 오프라인 연구 실행. 실제 운영 정확도·실시간 지연 평가가 아님.", "",
             "동일 grain의 동일 feature cell을 HR·SR·ML에 전달. 경고 우선 OR, 미평가 보존.", "",
             "| 행 단위 | 행 수 | HR 경고 | SR 경고 | ML 경고 | OR 경고 | OR 평가 불가 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for g in result_groups:
        s = g["summary"]
        lines.append("| " + " | ".join(map(str, [g["grain"]["table"], len(g["rows"]),
                     *[s[k].get(WARNING, 0) for k in ("HR", "SR", "ML", "OR")],
                     s["OR"].get(UNASSESSED, 0)])) + " |")
    lines += ["", "서로 다른 테이블/원천의 행을 거래 수로 합산하지 않음.",
              "ML 모델이 없는 grain은 ML 평가 불가. 지원하지 않는 후보는 생성 개수에 포함하지 않음.",
              "이번 실행의 새 LLM 호출·ML 학습·규칙 재생성: 모두 0."]
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    seal(out, "detection_result")
    return result

"""Shared snapshot of versioned definitions; independent Rule/ML preparation."""
import hashlib
import json
import math
from pathlib import Path

import model_adapters
from detector.artifacts import verified_artifact_path
from detector.policy import parse_policy, execute
from detector.rules import evaluate_rules
from feature_runtime import calculate_features, input_manifest_hash


def load_context(conn, config_id, source_id):
    with conn.cursor() as cur:
        cur.execute("SELECT source_id,configuration_spec FROM ml_config_versions WHERE id=%s", (config_id,))
        row = cur.fetchone()
        if row is None or row[0] != source_id:
            raise ValueError("config_source_mismatch")
        spec = row[1]
        policy = parse_policy(spec)
        cur.execute("""SELECT cm.feature_set_id,m.relative_path,m.content_hash,f.relative_path,f.content_hash,
                              cm.training_settings,cm.threshold_spec
            FROM ml_config_models cm LEFT JOIN ml_artifacts m ON m.id=cm.model_artifact_id
            LEFT JOIN ml_artifacts f ON f.id=cm.fit_artifact_id WHERE cm.config_version_id=%s""", (config_id,))
        models = cur.fetchall()
        feature_id = spec.get("featureSetId") or (str(models[0][0]) if len(models) == 1 else None)
        cur.execute("""SELECT rv.rule_key,rv.version,rv.rule_spec,rv.input_spec FROM ml_config_rules cr
            JOIN ml_rule_versions rv ON rv.id=cr.rule_version_id WHERE cr.config_version_id=%s
            ORDER BY cr.evaluation_order""", (config_id,))
        rules = cur.fetchall()
        if any(row[3].get("featureSetId") not in (None, feature_id) for row in rules):
            raise ValueError("rule_feature_set_mismatch")
        definitions, feature_hash = [], None
        if feature_id:
            cur.execute("SELECT content_hash FROM ml_feature_sets WHERE id=%s", (feature_id,))
            found = cur.fetchone()
            if found is None:
                raise ValueError("feature_set_missing")
            feature_hash = found[0]
            cur.execute("""SELECT m.output_name,v.expression,v.requires_fit FROM ml_feature_set_members m
                JOIN ml_feature_versions v ON v.id=m.feature_version_id
                WHERE m.feature_set_id=%s AND m.model_input ORDER BY m.output_position""", (feature_id,))
            members = cur.fetchall()
            if any(item[2] for item in members):
                raise ValueError("fitted_features_unsupported")
            definitions = [{"name": item[0], "expression": item[1]} for item in members]
    return {"policy": policy, "rules": [(r[0],r[1],r[2]) for r in rules], "models": models,
            "definitions": definitions, "featureSetId": feature_id, "featureHash": feature_hash}


def compute(context, raw, root: Path | None, *, started_at=None):
    definitions = context["definitions"]
    def rules():
        if not context["rules"]:
            return {"status": "not_ready", "anomalous": None, "evidence": [{"reason": "rules_unavailable"}]}
        try:
            values, missing = calculate_features(definitions, raw) if definitions else ({}, [])
        except (ValueError,TypeError):
            # Raw P.* rules remain independently executable; missing F.* stays unavailable.
            values, missing = {}, [item["name"] for item in definitions]
        status, code, evidence = evaluate_rules(context["rules"], raw, values)
        severity = [spec.get("outcome", {}).get("severity") for (_, _, spec), item in zip(context["rules"], evidence)
                    if item.get("result") == "hit"]
        return {"status": status, "anomalous": True if code else False if status == "succeeded" else None,
                "code": code, "severity": [v for v in severity if v], "evidence": evidence,
                "featureValues":values,"missingFeatures":missing}
    def ml():
        models = context["models"]
        if len(models) != 1:
            return {"status": "not_ready", "anomalous": None, "evidence": [{"reason": "model_unavailable"}]}
        model = models[0]
        values, missing = calculate_features(definitions, raw)
        names = model[5].get("featureNames")
        if missing or not names or set(names) != set(values) or str(model[0]) != context["featureSetId"]:
            return {"status": "not_ready", "anomalous": None, "evidence": [{"reason": "model_features_unavailable"}]}
        model_path = verified_artifact_path(root, model[1], model[2])
        fit_path = verified_artifact_path(root, model[3], model[4])
        threshold = model[6]
        if model_path is None or fit_path is None or threshold.get("direction") not in {"higher_is_anomaly", "lower_is_anomaly"}:
            return {"status": "not_ready", "anomalous": None, "evidence": [{"reason": "model_artifact_or_threshold_unavailable"}]}
        boundary = float(threshold["value"])
        if not math.isfinite(boundary):
            raise ValueError("invalid_threshold")
        score = model_adapters.score_iforest({"modelPath": model_path, "fitPath": fit_path, "featureNames": names}, values)
        if not isinstance(score, (int,float)) or not math.isfinite(score):
            raise ValueError("invalid_score")
        anomaly = score >= boundary if threshold["direction"] == "higher_is_anomaly" else score <= boundary
        return {"status": "succeeded", "anomalous": anomaly, "score": float(score),
                "evidence": [{"modelKey": "iforest", "anomalous": anomaly}],"featureValues":values,"missingFeatures":missing}
    return execute(context["policy"], rules, ml, started_at=started_at)


def legacy_feature_hash(context, observation_id, content_hash):
    pairs = []
    if context["featureSetId"]:
        pairs = [(context["featureSetId"], input_manifest_hash(str(observation_id), content_hash, context["featureHash"]))]
    return hashlib.sha256(json.dumps(sorted(pairs)).encode()).hexdigest()

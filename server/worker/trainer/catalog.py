"""변수 추천 모델의 출력을 검증해 불변 버전과 변수 묶음으로 등록한다."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from feature_runtime import validate_definitions
from feature_fit import requires_fit


def register_feature_set(conn: Any, definitions: list[dict[str, Any]], actor_id: str, *, definition_kind: str = 'model_generated') -> uuid.UUID:
    """중복 제안은 같은 묶음으로 수렴하고, 새 제안은 새 버전으로 보존한다."""
    validate_definitions(definitions)
    if definition_kind not in ('model_generated', 'operator_defined'):
        raise ValueError('invalid_definition_kind')
    serialized = json.dumps(definitions, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256((serialized if definition_kind == 'model_generated' else definition_kind + ':' + serialized).encode()).hexdigest()
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(81732, 1)")
            cur.execute("SELECT id FROM ml_feature_sets WHERE content_hash = %s ORDER BY created_at LIMIT 1", (digest,))
            existing = cur.fetchone()
            if existing:
                return existing[0]
            feature_set_id = uuid.uuid4()
            cur.execute(
                """INSERT INTO ml_feature_sets
                     (id, schema_version, transform_version, output_schema, content_hash,
                      created_by_actor_id, created_at)
                   VALUES (%s, 'numeric-process-v2', 'safe-dsl-v2', %s::jsonb, %s,
                           %s, clock_timestamp())""",
                (feature_set_id, json.dumps([item["name"] for item in definitions]), digest, actor_id),
            )
            for position, item in enumerate(definitions):
                key = item["name"]
                expression = item["expression"]
                expression_json = json.dumps(expression, sort_keys=True, separators=(",", ":"))
                expression_hash = hashlib.sha256(expression_json.encode()).hexdigest()
                cur.execute(
                    """SELECT id FROM ml_feature_versions
                         WHERE definition_key = %s AND content_hash = %s AND definition_kind = %s
                         ORDER BY version LIMIT 1""",
                    (key, expression_hash, definition_kind),
                )
                row = cur.fetchone()
                if row:
                    version_id = row[0]
                else:
                    cur.execute("SELECT COALESCE(max(version), 0) + 1 FROM ml_feature_versions WHERE definition_key = %s", (key,))
                    version = cur.fetchone()[0]
                    version_id = uuid.uuid4()
                    cur.execute(
                        """INSERT INTO ml_feature_versions
                             (id, definition_key, version, display_name, definition_kind,
                              expression, output_type, output_unit, value_policy,
                              availability_spec, requires_fit, content_hash,
                              created_by_actor_id, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s,
                                   %s, %s::jsonb, %s::jsonb, %s, %s, %s, clock_timestamp())""",
                        (version_id, key, version, key, definition_kind, expression_json,
                         expression.get("outputType", "decimal"), expression.get("unit"),
                         json.dumps({"missing": "null", "zeroDivision": "null"}),
                         json.dumps({"layer": "P", "asOf": "observation"}), requires_fit(expression), expression_hash, actor_id),
                    )
                cur.execute(
                    """INSERT INTO ml_feature_set_members
                         (feature_set_id, feature_version_id, model_input, output_position,
                          output_name, transform_settings)
                       VALUES (%s, %s, true, %s, %s, '{}'::jsonb)""",
                    (feature_set_id, version_id, position, key),
                )
    return feature_set_id

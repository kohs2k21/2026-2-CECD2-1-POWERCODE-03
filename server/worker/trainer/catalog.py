"""변수 추천 모델의 출력을 검증해 불변 버전과 변수 묶음으로 등록한다.

hash 규칙은 순수 함수(`canonical_json`·`feature_set_content_hash`·`expression_hash`)로 두고 등록도 같은 함수를 쓴다.
API의 직접 등록(`operator_defined`)이 같은 값을 내야 하므로 시험 벡터는
`server/contracts/feature-set-hash-vectors.json`(trainer-service.md 입력 변수 세트 등록 규칙)에 고정한다.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from feature_runtime import validate_definitions
from feature_fit import requires_fit

DEFINITION_KINDS = ('model_generated', 'operator_defined')
# 등록 행에 고정으로 들어가는 값. API 직접 등록도 같은 값을 쓴다.
SCHEMA_VERSION = 'numeric-process-v2'
TRANSFORM_VERSION = 'safe-dsl-v2'
VALUE_POLICY = {"missing": "null", "zeroDivision": "null"}
AVAILABILITY_SPEC = {"layer": "P", "asOf": "observation"}
# 같은 거래 안에서 잡는 advisory lock 키(API 직접 등록도 같은 키로 직렬화한다).
FEATURE_SET_LOCK = (81732, 1)


def canonical_json(value: Any) -> str:
    """키 정렬·공백 없는 JSON(ASCII 밖 문자는 `\\uXXXX` 이스케이프). 정의 묶음과 식 hash의 입력."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def feature_set_content_hash(definitions: list[dict[str, Any]], definition_kind: str) -> str:
    """`model_generated`는 정의 JSON 그대로, 그 밖의 종류는 `<종류>:` 접두를 붙여 sha256 hex."""
    if definition_kind not in DEFINITION_KINDS:
        raise ValueError('invalid_definition_kind')
    serialized = canonical_json(definitions)
    return hashlib.sha256((serialized if definition_kind == 'model_generated' else definition_kind + ':' + serialized).encode()).hexdigest()


def expression_hash(expression: dict[str, Any]) -> tuple[str, str]:
    """(정규 식 JSON, sha256 hex). 변수 버전의 `content_hash`이며 종류 접두가 없다."""
    expression_json = canonical_json(expression)
    return expression_json, hashlib.sha256(expression_json.encode()).hexdigest()


def register_feature_set(conn: Any, definitions: list[dict[str, Any]], actor_id: str, *, definition_kind: str = 'model_generated') -> uuid.UUID:
    """중복 제안은 같은 묶음으로 수렴하고, 새 제안은 새 버전으로 보존한다."""
    validate_definitions(definitions)
    digest = feature_set_content_hash(definitions, definition_kind)
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(81732, 1)")  # FEATURE_SET_LOCK
            cur.execute("SELECT id FROM ml_feature_sets WHERE content_hash = %s ORDER BY created_at LIMIT 1", (digest,))
            existing = cur.fetchone()
            if existing:
                return existing[0]
            feature_set_id = uuid.uuid4()
            cur.execute(
                """INSERT INTO ml_feature_sets
                     (id, schema_version, transform_version, output_schema, content_hash,
                      created_by_actor_id, created_at)
                   VALUES (%s, %s, %s, %s::jsonb, %s,
                           %s, clock_timestamp())""",
                (feature_set_id, SCHEMA_VERSION, TRANSFORM_VERSION,
                 json.dumps([item["name"] for item in definitions]), digest, actor_id),
            )
            for position, item in enumerate(definitions):
                key = item["name"]
                expression = item["expression"]
                expression_json, member_hash = expression_hash(expression)
                cur.execute(
                    """SELECT id FROM ml_feature_versions
                         WHERE definition_key = %s AND content_hash = %s AND definition_kind = %s
                         ORDER BY version LIMIT 1""",
                    (key, member_hash, definition_kind),
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
                         json.dumps(VALUE_POLICY), json.dumps(AVAILABILITY_SPEC),
                         requires_fit(expression), member_hash, actor_id),
                    )
                cur.execute(
                    """INSERT INTO ml_feature_set_members
                         (feature_set_id, feature_version_id, model_input, output_position,
                          output_name, transform_settings)
                       VALUES (%s, %s, true, %s, %s, '{}'::jsonb)""",
                    (feature_set_id, version_id, position, key),
                )
    return feature_set_id

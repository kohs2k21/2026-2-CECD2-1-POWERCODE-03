"""격리 DB에서 입력 변수 세트 등록(`operator_defined`)이 hash 시험 벡터와 같은 행을 남기는지 확인한다.

- `server/contracts/feature-set-hash-vectors.json`의 각 사례를 `register_feature_set`으로 등록해 세트·버전·멤버 열을 벡터와 비교한다.
- 같은 정의 재등록은 같은 세트 id, 같은 이름·식은 세트가 달라도 같은 변수 버전을 쓴다.
- 버전 번호는 종류와 무관하게 이름별 max+1(같은 식의 `model_generated` 등록은 새 버전).
이미 쓴 QA DB에서도 다시 돌 수 있도록 번호는 등록 직전 값을 기준으로 본다. 고객 입력 없음.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trainer.catalog import register_feature_set
from trainer.feature_fields import VECTORS_PATH

ACTOR = "integration-feature-set"


def _max_versions(cur, names):
    cur.execute("SELECT definition_key, max(version) FROM ml_feature_versions WHERE definition_key = ANY(%s) GROUP BY 1",
                (list(names),))
    found = dict(cur.fetchall())
    return {name: found.get(name, 0) for name in names}


def _members(cur, feature_set_id):
    cur.execute(
        """SELECT m.output_name, m.output_position, m.model_input, m.transform_settings,
                  v.id, v.definition_key, v.version, v.display_name, v.definition_kind, v.expression,
                  v.output_type, v.output_unit, v.value_policy, v.availability_spec, v.requires_fit, v.content_hash
             FROM ml_feature_set_members AS m JOIN ml_feature_versions AS v ON v.id = m.feature_version_id
            WHERE m.feature_set_id = %s ORDER BY m.output_position""",
        (feature_set_id,),
    )
    return cur.fetchall()


def check_case(conn, case, version_ids):
    names = [item["name"] for item in case["definitions"]]
    with conn.cursor() as cur:
        before = _max_versions(cur, names)
        cur.execute("""SELECT definition_key, id FROM ml_feature_versions
                         WHERE definition_kind = 'operator_defined' AND (definition_key, content_hash) IN
                               (SELECT * FROM unnest(%s::text[], %s::text[]))""",
                    (names, [member["expressionHash"] for member in case["members"]]))
        reusable = dict(cur.fetchall())
    conn.commit()
    feature_set_id = register_feature_set(conn, case["definitions"], ACTOR, definition_kind="operator_defined")
    conn.commit()
    again = register_feature_set(conn, json.loads(json.dumps(case["definitions"])), ACTOR, definition_kind="operator_defined")
    conn.commit()
    assert again == feature_set_id, (case["id"], again, feature_set_id)
    with conn.cursor() as cur:
        cur.execute("SELECT schema_version, transform_version, output_schema, content_hash FROM ml_feature_sets WHERE id = %s",
                    (feature_set_id,))
        schema_version, transform_version, output_schema, content_hash = cur.fetchone()
        assert content_hash == case["contentHash"], (case["id"], content_hash)
        assert {"schema_version": schema_version, "transform_version": transform_version,
                "output_schema": output_schema} == case["featureSet"], case["id"]
        cur.execute("SELECT count(*) FROM ml_feature_sets WHERE content_hash = %s", (case["contentHash"],))
        assert cur.fetchone()[0] == 1, case["id"]
        rows = _members(cur, feature_set_id)
    conn.commit()
    assert len(rows) == len(case["members"]), case["id"]
    for row, member, definition in zip(rows, case["members"], case["definitions"]):
        (output_name, position, model_input, transform_settings, version_id, key, version, display_name, kind,
         expression, output_type, output_unit, value_policy, availability_spec, fit, expression_hash) = row
        actual = {"name": output_name, "expressionHash": expression_hash, "definition_key": key,
                  "display_name": display_name, "definition_kind": kind, "output_type": output_type,
                  "output_unit": output_unit, "value_policy": value_policy, "availability_spec": availability_spec,
                  "requires_fit": fit, "model_input": model_input, "output_position": position,
                  "output_name": output_name, "transform_settings": transform_settings}
        expected = {key_: value for key_, value in member.items() if key_ != "canonicalExpressionJson"}
        assert actual == expected, (case["id"], actual, expected)
        assert expression == definition["expression"], (case["id"], output_name)
        assert version_ids.setdefault(output_name, version_id) == version_id, (case["id"], output_name)
        if output_name in reusable:
            assert version_id == reusable[output_name], (case["id"], output_name)
        else:
            # 이름·식 hash·종류가 같은 버전이 없었으면 등록 직전 이름별 max+1
            assert version == before[output_name] + 1, (case["id"], output_name, version, before[output_name])
    return feature_set_id


def check_kind_versioning(conn, case):
    """같은 식이라도 종류가 다르면 재사용하지 않고 이름별 max+1 새 버전, 세트 hash도 다르다."""
    names = [item["name"] for item in case["definitions"]]
    with conn.cursor() as cur:
        before = _max_versions(cur, names)
        cur.execute("SELECT id FROM ml_feature_versions WHERE definition_key = ANY(%s) AND definition_kind = 'model_generated'",
                    (names,))
        had_generated = bool(cur.fetchall())
    conn.commit()
    generated = register_feature_set(conn, case["definitions"], ACTOR, definition_kind="model_generated")
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("SELECT content_hash FROM ml_feature_sets WHERE id = %s", (generated,))
        assert cur.fetchone()[0] != case["contentHash"]
        rows = _members(cur, generated)
    conn.commit()
    for row in rows:
        output_name, version, kind, expression_hash = row[0], row[6], row[8], row[15]
        assert kind == "model_generated" and expression_hash == next(
            member["expressionHash"] for member in case["members"] if member["name"] == output_name)
        if not had_generated:
            assert version == before[output_name] + 1, (output_name, version, before[output_name])


def main() -> None:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("격리된 시험 DB의 DATABASE_URL이 필요하다")
    vectors = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
    version_ids: dict = {}
    with psycopg.connect(dsn) as conn:
        ids = {case["id"]: check_case(conn, case, version_ids) for case in vectors["cases"]}
        assert len(set(ids.values())) == len(ids), ids
        check_kind_versioning(conn, next(case for case in vectors["cases"] if case["id"] == "single_count"))
    print(f"PASS feature set registration vectors: {len(ids)} cases, member versions reused across sets, "
          "re-register same id, kind-independent max+1", flush=True)


if __name__ == "__main__":
    main()

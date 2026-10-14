BEGIN;

-- 규칙은 UPDATE/DELETE 대신 새 버전을 추가한다. 운영 구성의 과거 참조도 유지한다.
CREATE OR REPLACE FUNCTION prevent_rule_version_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'ml_rule_versions rows are immutable';
END;
$$;

DROP TRIGGER IF EXISTS ml_rule_versions_immutable ON ml_rule_versions;
CREATE TRIGGER ml_rule_versions_immutable
BEFORE UPDATE OR DELETE ON ml_rule_versions
FOR EACH ROW EXECUTE FUNCTION prevent_rule_version_mutation();

COMMIT;

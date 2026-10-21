"""Fresh local/CI QA databases only. Never drop or migrate an existing database."""
from pathlib import Path
import os
from urllib.parse import urlsplit,urlunsplit
import psycopg
from psycopg import sql

url=os.environ['RUNTIME_TEST_ADMIN_URL']
parsed=urlsplit(url)
if parsed.hostname not in ('localhost','127.0.0.1') or parsed.path!='/postgres':
    raise SystemExit('only a local postgres admin URL is permitted')
root=Path(__file__).resolve().parents[1]/'migrations'
files=('001_postgres_queues.sql','002_core_erd.sql','003_erd_v03_enums.sql','004_erd_v03_contracts.sql',
       '003_rule_versions_immutable.sql','004_feature_pipeline.sql','005_research_runtime.sql',
       '006_service_transition.sql','007_api_collection_metrics.sql','008_demo_replay.sql','009_switchable_inputs.sql',
       '010_prepared_csv_import.sql','011_config_comparison_recommendation.sql')
with psycopg.connect(url,autocommit=True) as admin:
    for name in ('qa_research_pipeline','qa_pipeline','qa_trainer_service','qa_metrics_service','qa_synthetic_service','qa_prepared_csv_service'):
        # Existing QA databases fail here; no DROP or existing-schema changes.
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        database_url=urlunsplit(parsed._replace(path='/'+name))
        with psycopg.connect(database_url,autocommit=True) as conn:
            for file in files:
                conn.execute((root/file).read_text(encoding='utf-8'))
        print(f'prepared fresh {name}')

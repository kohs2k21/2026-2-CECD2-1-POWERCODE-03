"""Register explicit prepared columns for FK lineage, never as raw P expressions."""
import json
import uuid

from trainer.prepared_contract import digest
from trainer.prepared_models import input_contract


def register(conn, manifest, actor):
    descriptor = input_contract(manifest)
    content_hash = digest(descriptor)
    with conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(81732,2)')
        cur.execute("SELECT id FROM ml_feature_sets WHERE content_hash=%s AND schema_version='prepared-features-v1' ORDER BY created_at LIMIT 1",(content_hash,))
        found = cur.fetchone()
        if found: return found[0]
        feature_set_id = uuid.uuid4()
        cur.execute('''INSERT INTO ml_feature_sets(id,schema_version,transform_version,output_schema,content_hash,created_by_actor_id,created_at)
                       VALUES(%s,'prepared-features-v1','external-completed-input-v1',%s::jsonb,%s,%s,clock_timestamp())''',
                    (feature_set_id,json.dumps(descriptor),content_hash,actor))
        for position, feature in enumerate(manifest['features']):
            version_id = uuid.uuid4()
            expression = {'op':'prepared_column','column':feature['name'],'inputStage':'model_input'}
            cur.execute('''INSERT INTO ml_feature_versions(id,definition_key,version,display_name,definition_kind,expression,
                           output_type,output_unit,value_policy,availability_spec,requires_fit,content_hash,created_by_actor_id,created_at)
                           VALUES(%s,%s,1,%s,'external_prepared',%s::jsonb,'number',%s,'{}'::jsonb,%s::jsonb,false,%s,%s,clock_timestamp())''',
                        (version_id,'prepared:'+str(feature_set_id)+':'+str(position),feature['name'],json.dumps(expression),
                         feature['unit'],json.dumps({'scope':'external_file_only','operationalRuntimeSupported':False}),digest(expression),actor))
            cur.execute('''INSERT INTO ml_feature_set_members(feature_set_id,feature_version_id,model_input,output_position,output_name,transform_settings)
                           VALUES(%s,%s,true,%s,%s,'{}'::jsonb)''',(feature_set_id,version_id,position,feature['name']))
    return feature_set_id

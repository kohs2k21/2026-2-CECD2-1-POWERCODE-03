"""Strengthened artifact metadata checks; frozen experiment policy kept unchanged."""
import contracts as legacy
from contracts import *
VERSION='evidence-artifact-binding-2.0.1'
def verify_feature(f,c):
 result=legacy.verify_feature(f,c);identity=f['measurement']['validation']['identity']
 assert sorted(map(tuple,f['source_columns']))==sorted(map(tuple,identity['source_columns'])),'source_columns_binding'
 assert sorted(f['relationships'])==sorted(identity['relationships']),'relationships_binding'
 assert f['dtype']==identity['inferred_type']and f['unit']==identity['unit'],'metadata_type_unit_binding'
 assert f['calculation_policy']==identity['calculation_policy'],'metadata_calculation_binding'
 result['artifact_policy']=VERSION;return result

def bundle(f,c):
 proof=verify_feature(f,c);b=legacy.bundle(f,c);b['artifact_binding']=proof;b['bundle_sha']=digest({k:v for k,v in b.items()if k!='bundle_sha'});return b

def verify_bundle(b):
 assert b['bundle_sha']==digest({k:v for k,v in b.items()if k!='bundle_sha'}),'bundle_SHA'
 c=load_capture((ROOT/b['capture_path']).resolve());admission(c,'model_input');f=b['feature'];verify_feature(f,c);fresh=bundle(f,c)
 assert digest(b)==digest(fresh),'stale_or_tampered_bundle';return {'passed':True,'policy':VERSION,'code_remeasurement':True}

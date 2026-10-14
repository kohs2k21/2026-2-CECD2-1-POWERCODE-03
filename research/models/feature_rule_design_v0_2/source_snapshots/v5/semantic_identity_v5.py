"""Conservative calculation equivalence, independent of observed sample values."""
from copy import deepcopy
from pilot_io import digest
from aggregate_review import expression_identity

def normalized(ex):
 ex=expression_identity(ex)
 if not isinstance(ex,dict):return ex
 for key in ['args']:
  if key in ex:ex[key]=[normalized(a)for a in ex[key]]
 for key in ['where','value']:
  if isinstance(ex.get(key),dict):ex[key]=normalized(ex[key])
 if ex['op']=='eq':ex['args']=sorted(ex['args'],key=digest)
 if ex['op']in ['gt','gte']:
  ex['op']={'gt':'lt','gte':'lte'}[ex['op']];ex['args']=list(reversed(ex['args']))
 if ex['op']=='conditional_count'and ex.get('where')is None:return {'op':'child_count','relationship_id':ex['relationship_id']}
 return ex

def key(f):
 p=f['plan'];return digest({'expression':normalized(p['expression']),'grain':p['grain'],'population':{'table':p['population']['table'],'filter':normalized(p['population']['filter'])},'group_by':p['group_by'],'dtype':f['dtype'],'unit':f['unit']})

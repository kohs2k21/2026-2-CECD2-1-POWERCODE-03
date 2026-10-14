"""Atomic reservations across processes; counts calls and returned responses."""
from pilot_io import *
import fcntl,time,uuid
from contextlib import contextmanager
LIMITS={'v4_frozen':20,'v5_frozen':20,'adapter_reload':1,'development_repairs':20,'rule_link':12,'representative_replay':4}
@contextmanager
def lock():
 p=TOP/'generation_ledger';p.mkdir(exist_ok=True)
 with (p/'lock').open('a')as f:
  fcntl.flock(f,fcntl.LOCK_EX);yield p;fcntl.flock(f,fcntl.LOCK_UN)
def events(p):return [read(q)for q in sorted(p.glob('*.json'))]
def reserve(group,ids,model,max_new_tokens):
 with lock()as d:
  assert not (d/'closed.json').exists(),'generation_budget_closed'
  es=events(d);rs=[e for e in es if e['event']=='reserved'];total=sum(e['responses_reserved']for e in rs);subtotal=sum(e['responses_reserved']for e in rs if e['group']==group)
  assert total+len(ids)<=120,('global_budget',total)
  assert subtotal+len(ids)<=LIMITS[group],('group_budget',group,subtotal)
  key=str(time.time_ns())+'_'+uuid.uuid4().hex[:8];write(d/(key+'_reserved.json'),{'event':'reserved','call_id':key,'group':group,'ids':ids,'model':model,'responses_reserved':len(ids),'max_new_tokens':max_new_tokens,'time_ns':time.time_ns()});return key
def complete(key,group,items):
 with lock()as d:write(d/(str(time.time_ns())+'_'+key+'_returned.json'),{'event':'returned','call_id':key,'group':group,'responses_returned':len(items),'items':[{'id':x['id'],'raw_sha':digest(x['raw_output'])}for x in items]})
def audit():
 with lock()as d:
  es=events(d);r=[e for e in es if e['event']=='reserved'];c=[e for e in es if e['event']=='returned'];return {'global_cap':120,'calls_started':len(r),'calls_completed':len(c),'responses_reserved':sum(x['responses_reserved']for x in r),'responses_returned':sum(x['responses_returned']for x in c),'groups':{g:{'calls':sum(x['group']==g for x in r),'reserved':sum(x['responses_reserved']for x in r if x['group']==g),'returned':sum(x['responses_returned']for x in c if x['group']==g)}for g in LIMITS},'failed_or_interrupted_reservations_not_reused':True}

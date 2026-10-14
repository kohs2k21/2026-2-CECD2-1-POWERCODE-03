from policy_v3 import *
from datetime import datetime
from decimal import localcontext,InvalidOperation

def verify_candidates(c,items):
 admission(c,"measurement");inv=c["inventory"]
 cols={(t['name'],c['name']):c for t in inv['tables']for c in t['columns']};data={}
 for t in inv['tables']:
  with (ROOT/t['csv_source']['path']).open(encoding='utf-8-sig',newline='')as s:data[t['name']]=list(csv.DictReader(s))
 rels={r['id']:r for r in inv['relationships']};cache={}
 def value(raw,t,c):
  m=cols[(t,c)]
  if raw is None or raw in m.get('missing_tokens',['']):return None
  typ=m['logical_type']
  if typ in ['number','integer']:
   try:v=Decimal(str(raw));return v if v.is_finite()and(typ!='integer'or v==v.to_integral_value())else None
   except InvalidOperation:return None
  if typ in ['timestamp','datetime']:
   try:return datetime.fromisoformat(str(raw).replace('Z','+00:00'))
   except ValueError:return None
  if typ=='boolean':return {'true':True,'false':False,'1':True,'0':False}.get(str(raw).lower())
  return str(raw)
 def key(row,t,cs):
  k=tuple(value(row.get(c),t,c)for c in cs);return None if None in k else k
 def calc(ex,row,t,policy,pop=None):
  op=ex['op']
  if op=='column':return value(row.get(ex['column']),ex['table'],ex['column'])
  if op=='literal':return Decimal(str(ex['value']))if type(ex['value'])in [int,float]else ex['value']
  if op in ['child_count','conditional_count','child_sum','parent_match_count']:
   r=rels[ex['relationship_id']];parent=op!='parent_match_count';ct,pt=r['from_table'],r['to_table'];k=key(row,pt if parent else ct,r['to_columns']if parent else r['from_columns'])
   if k is None:return None
   parents=[p for p in data[pt]if key(p,pt,r['to_columns'])==k]
   if not parent:return len(parents)
   if len(parents)!=1:return None
   children=[c for c in data[ct]if key(c,ct,r['from_columns'])==k];chosen=[]
   for c in children:
    pred=calc(ex['where'],c,ct,policy)if ex.get('where')is not None else True
    if pred is None:return None
    if pred is True:chosen.append(c)
   if op!='child_sum':return len(chosen)
   vals=[calc(ex['value'],c,ct,policy)for c in chosen]
   if any(v is None for v in vals)and policy['child_missing']=='propagate':return None
   if vals and all(v is None for v in vals):return None
   return sum((v for v in vals if v is not None),Decimal(0))
  if op=='group_count':
   k=key(row,t,ex['keys'])
   if k is None:return None
   sig=(t,digest(ex),digest(pop))
   if sig not in cache:
    counts=Counter();unknown=set()
    for c in data[t]:
     ck=key(c,t,ex['keys'])
     if ck is None:continue
     pf=calc(pop,c,t,policy)if pop is not None else True
     if pf is False:continue
     cf=calc(ex['where'],c,t,policy)if ex['where']is not None else True
     if pf is None:cf=None
     if cf is None:unknown.add(ck)
     if cf is True:counts[ck]+=1
    cache[sig]=(counts,unknown)
   counts,unknown=cache[sig];return None if k in unknown else counts[k]
  vs=[calc(a,row,t,policy,pop)for a in ex.get('args',[])]
  if op=='is_missing':return vs[0]is None
  if any(v is None for v in vs):return None
  if op=='subtract':return vs[0]-vs[1]
  if op=='divide':return None if vs[1]==0 else vs[0]/vs[1]
  if op=='and':return vs[0]and vs[1]
  if op=='datetime_diff':
   if (vs[0].tzinfo is None)!=(vs[1].tzinfo is None):return None
   d=vs[0]-vs[1];return Decimal((d.days*86400+d.seconds)*1000000+d.microseconds)/{'ms':Decimal(1000),'us':Decimal(1),'s':Decimal(1000000)}[ex['unit']]
  if op=='convert_unit':return vs[0]*{'ms':Decimal(1000),'us':Decimal(1),'s':Decimal(1000000)}[ex['from_unit']]/{'ms':Decimal(1000),'us':Decimal(1),'s':Decimal(1000000)}[ex['to_unit']]
  return {'eq':lambda:vs[0]==vs[1],'lt':lambda:vs[0]<vs[1],'gt':lambda:vs[0]>vs[1],'lte':lambda:vs[0]<=vs[1],'gte':lambda:vs[0]>=vs[1]}[op]()

 checks=[]
 with localcontext()as ctx:
  ctx.prec=50
  for item in items:
   f=item["feature"];p=f["plan"];t=p["grain"]["table"];actual=[]
   for row in data[t]:
    flag=calc(p["population"]["filter"],row,t,p["missing_policy"])if p["population"]["filter"]is not None else True
    if flag is True:actual.append(calc(p["expression"],row,t,p["missing_policy"],p["population"]["filter"]))
   expected=item["values"]
   def typed(v):
    if isinstance(v,dict)and "decimal"in v:v=Decimal(v["decimal"])
    if type(v)is bool:return ("boolean",v)
    if isinstance(v,(int,float,Decimal)):return ("numeric",Decimal(str(v)))
    return (type(v).__name__,v)
   assert list(map(typed,actual))==list(map(typed,expected)),f["id"]
   checks.append({"id":f["id"],"row_values":len(actual),"passed":True,"definition_sha":f["definition_sha256"],"source_version":f["source_version"],"method":"Independent AST interpreter on guarded actual rows; no Engine.evaluate call"})
 return checks
